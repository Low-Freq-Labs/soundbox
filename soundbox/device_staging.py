"""Descriptor-pinned temporary publication; callers retain device authority."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
from functools import lru_cache
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import BinaryIO, Callable, ContextManager, Iterator, cast

from .operation_control import cancellation_enabled, checkpoint


@contextmanager
def open_parent(
    root: Path, identity: tuple[int, int], relative: Path, *, create: bool = False,
) -> Iterator[tuple[int, str]]:
    """Walk only real directories on the pinned filesystem, without path reopens."""
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise RuntimeError('Invalid device publication path')
    fd = os.open(root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != identity:
            raise RuntimeError('Device root changed before publication')
        for component in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(component, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(
                component, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY,
                dir_fd=fd)
            os.close(fd)
            fd = child
            if os.fstat(fd).st_dev != identity[0]:
                raise RuntimeError('Device publication crosses the mount boundary')
        yield fd, relative.name
    finally:
        os.close(fd)


ParentContext = Callable[..., ContextManager[tuple[int, str]]]


def _owned(fd: int, name: str, handle: int) -> bool:
    # FAT derives a regular file's inode from its allocated first cluster; the
    # identity captured for a new empty file can change after our own write.
    # Compare the current name with the still-open original, never that stale ID.
    opened = os.fstat(handle)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        return False
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    after = os.fstat(handle)
    identity = (opened.st_dev, opened.st_ino)
    return all(stat.S_ISREG(row.st_mode) and row.st_nlink == 1
               and (row.st_dev, row.st_ino) == identity
               for row in (info, after))


@contextmanager
def staged_output(
    destination: Path, parent_fd: ParentContext, revalidate: Callable[[], None],
    *, after_publish: Callable[[int], None] | None = None, mode: int = 0o666,
    replace: bool = True,
) -> Iterator[BinaryIO]:
    """Keep one exclusively created file open through publication and cleanup.

    Old deterministic .sbpart files belong to neither this operation nor cleanup.
    Recheck both the parent and temporary inode before publishing; cleanup uses
    the original directory descriptor even if a mount/path has been replaced.
    """
    with parent_fd(destination, create=True) as (fd, name):
        parent = os.fstat(fd)
        temporary = '.soundbox-' + secrets.token_hex(16) + '.sbpart'
        handle = os.open(
            temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode, dir_fd=fd)
        try:
            # Keep the raw descriptor through cleanup too, including exceptions
            # raised while writing or flushing the buffered wrapper.
            with os.fdopen(handle, 'w+b', closefd=False) as output:
                yield output
                output.flush()
                os.fsync(output.fileno())
                revalidate()
                checkpoint()
                with parent_fd(destination) as (current_fd, _):
                    current = os.fstat(current_fd)
                    if (current.st_dev, current.st_ino) != (parent.st_dev, parent.st_ino):
                        raise RuntimeError('Device parent changed before publication')
                if not _owned(fd, temporary, handle):
                    raise RuntimeError('Device temporary file changed before publication')
                if replace:
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                else:
                    _rename_new(fd, temporary, name)
                if after_publish is not None:
                    after_publish(fd)
        finally:
            try:
                if _owned(fd, temporary, handle):
                    os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass  # An inaccessible original mount retains only our own stage.
            finally:
                os.close(handle)


class PublicationOwnershipLost(RuntimeError):
    """A new unindexed file cannot be proved ours for safe cleanup."""
    recovery_status = 'recovery required'


@lru_cache(maxsize=1)
def _exclusive_rename_call() -> Callable[[int, bytes, int, bytes, int], int]:
    if sys.platform != 'darwin':
        raise OSError('Exclusive device publication requires macOS')
    call = ctypes.CDLL(None, use_errno=True).renameatx_np
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    return cast(Callable[[int, bytes, int, bytes, int], int], call)


def exclusive_rename(source_parent: int, source: str,
                     destination_parent: int, destination: str) -> None:
    """Move one leaf between already pinned parents without replacing a name.

    Callers own path admission, entity verification and interruption recovery.
    This syscall alone grants no authority over either file or directory.
    """
    if (not source or not destination or source in ('.', '..') or destination in ('.', '..')
            or '/' in source or '/' in destination):
        raise ValueError('Exclusive publication requires single leaf names')
    if _exclusive_rename_call()(source_parent, os.fsencode(source), destination_parent,
                                os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination)


def exclusive_rename_from_path(source: Path, destination_parent: int,
                               destination: str) -> None:
    """Restore an exact admitted path without opening its privacy-owned parent.

    Protected macOS Trash permits an exact tagged-file operation while refusing
    directory access. Its caller must verify source ownership and revalidate the
    pinned destination; the syscall never replaces a concurrently arriving leaf.
    """
    source = Path(source)
    if (not source.is_absolute() or '..' in source.parts or not destination
            or destination in ('.', '..') or '/' in destination):
        raise ValueError('Exclusive restoration requires an absolute source and destination leaf')
    if _exclusive_rename_call()(-2, os.fsencode(source), destination_parent,
                                os.fsencode(destination), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination)


def _rename_new(parent: int, temporary: str, name: str) -> None:
    # Darwin RENAME_EXCL=4. No replacing fallback: a new media payload has no
    # authority over an occupant which appeared after planning.
    exclusive_rename(parent, temporary, parent, name)


class NewFilePublication:
    """One live descriptor for an unindexed, exclusively published new file.

    It is operation-local and valid only inside new_file_output. File-level
    publication precedes the caller's actual FD readback; the caller must not
    publish an index or accept this output until that readback succeeds.
    """
    def __init__(self, destination, parent_fd, revalidate, parent, name, temporary, handle, output):
        self.destination = destination
        self.parent_fd = parent_fd
        self.revalidate = revalidate
        self.parent = parent
        self.name = name
        self.temporary = temporary
        self.handle = handle
        self.output = output
        info = os.fstat(parent)
        self.parent_identity = info.st_dev, info.st_ino
        self.published = False
        self.accepted = False
        self.active = True

    def require_current(self) -> os.stat_result:
        if not self.active or not self.published:
            raise PublicationOwnershipLost('New media publication no longer owns its descriptor')
        self.revalidate()
        with self.parent_fd(self.destination) as (current, name):
            info = os.fstat(current)
            if ((info.st_dev, info.st_ino) != self.parent_identity or name != self.name
                    or not _owned(current, name, self.handle)):
                raise PublicationOwnershipLost('Published media changed before verification; replacement retained')
        return os.fstat(self.handle)

    def publish(self) -> None:
        if not self.active or self.published:
            raise RuntimeError('New media publication cannot be repeated')
        self.output.flush()
        os.fsync(self.handle)
        self.revalidate()
        checkpoint()
        with self.parent_fd(self.destination) as (current, name):
            info = os.fstat(current)
            if (info.st_dev, info.st_ino) != self.parent_identity or name != self.name:
                raise RuntimeError('Device parent changed before new media publication')
        if not _owned(self.parent, self.temporary, self.handle):
            raise RuntimeError('Device temporary file changed before new media publication')
        try:
            _rename_new(self.parent, self.temporary, self.name)
        except Exception:
            # A filesystem/error boundary may report failure after moving the
            # entry. Reconcile against our still-open entity before cleanup.
            if _owned(self.parent, self.name, self.handle):
                self.published = True
            elif not _owned(self.parent, self.temporary, self.handle):
                raise PublicationOwnershipLost('New media publication outcome is uncertain; evidence retained')
            raise
        self.published = True
        self.require_current()

    def accept(self) -> None:
        self.require_current()
        self.accepted = True

    def cleanup(self) -> None:
        if self.published and not self.accepted:
            claim = '.soundbox-rejected-' + secrets.token_hex(16) + '.sbpart'
            claimed = False
            try:
                self.require_current()
                # First claim the current leaf without replacing anything.
                # A late foreign leaf is restored or retained, never disposed
                # merely because our original entity was valid earlier.
                _rename_new(self.parent, self.name, claim)
                claimed = True
                if not _owned(self.parent, claim, self.handle):
                    raise PublicationOwnershipLost('New media was replaced during cleanup')
                self.revalidate()
                with self.parent_fd(self.destination) as (current, _name):
                    info = os.fstat(current)
                    if (info.st_dev, info.st_ino) != self.parent_identity:
                        raise PublicationOwnershipLost('New media parent changed during cleanup')
                os.unlink(claim, dir_fd=self.parent)
            except Exception as exc:
                if claimed:
                    try:
                        _rename_new(self.parent, claim, self.name)
                    except OSError as restore_error:
                        raise PublicationOwnershipLost(
                            f'New media cleanup is uncertain; claimed evidence retained at '
                            f'{self.destination.parent / claim}: {restore_error}') from exc
                raise PublicationOwnershipLost(
                    'New media could not be safely retired; replacement and recovery retained') from exc
        elif not self.published and _owned(self.parent, self.temporary, self.handle):
            os.unlink(self.temporary, dir_fd=self.parent)


@contextmanager
def new_file_output(destination: Path, parent_fd: ParentContext,
                    revalidate: Callable[[], None]) -> Iterator[NewFilePublication]:
    """Keep a new file's original FD through exclusive rename and verification.

    No existing destination may be replaced. An exception after publication
    retires only the currently proved own unindexed entity; uncertainty retains
    evidence with an explicit recovery-required outcome. Default staged_output
    replacement and its prepublication verification semantics are unchanged.
    """
    with parent_fd(destination, create=True) as (parent, name):
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, 'New media destination is occupied', name)
        temporary = '.soundbox-' + secrets.token_hex(16) + '.sbpart'
        handle = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o666, dir_fd=parent)
        publication = None
        try:
            with os.fdopen(handle, 'w+b', closefd=False) as output:
                publication = NewFilePublication(destination, parent_fd, revalidate, parent,
                                                 name, temporary, handle, output)
                try:
                    yield publication
                finally:
                    publication.cleanup()
        except Exception as exc:
            if publication is not None and publication.accepted:
                raise PublicationOwnershipLost(
                    'Verified new media finalization failed; payload and recovery retained') from exc
            raise
        finally:
            if publication is not None:
                publication.active = False
            elif _owned(parent, temporary, handle):
                os.unlink(temporary, dir_fd=parent)
            try:
                os.close(handle)
            except OSError as exc:
                if publication is not None and publication.published:
                    raise PublicationOwnershipLost(
                        'New media descriptor retirement failed; recovery retained') from exc
                raise


def copy_stream(source: BinaryIO, output: BinaryIO, *, allow_native: bool = True) -> None:
    """Bound cancellable copies; retain native copy when cancellation is disabled."""
    # The pinned macOS Python exposes the same fcopyfile entry used by shutil.
    # Unlike shutil.copyfile, it accepts our already-open, exclusive destination.
    import posix
    fast_copy = getattr(posix, '_fcopyfile', None)
    data_only = getattr(posix, '_COPYFILE_DATA', None)
    if allow_native and not cancellation_enabled() and fast_copy is not None and data_only is not None:
        try:
            fast_copy(source.fileno(), output.fileno(), data_only)
            return
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
            source.seek(0)
            output.seek(0)
            output.truncate()
    while True:
        checkpoint()
        block = source.read(1024 * 1024)
        if not block:
            return
        checkpoint()
        output.write(block)
