"""Descriptor-pinned temporary publication; callers retain device authority."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import secrets
import shutil
import stat
from typing import BinaryIO, Callable, ContextManager, Iterator


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


def _owned(fd: int, name: str, identity: tuple[int, int]) -> bool:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity


@contextmanager
def staged_output(
    destination: Path, parent_fd: ParentContext, revalidate: Callable[[], None],
    *, after_publish: Callable[[int], None] | None = None, mode: int = 0o666,
) -> Iterator[BinaryIO]:
    """Keep one exclusively created inode open until verification and publication.

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
        info = os.fstat(handle)
        identity = (info.st_dev, info.st_ino)
        try:
            with os.fdopen(handle, 'w+b') as output:
                yield output
                output.flush()
                os.fsync(output.fileno())
                revalidate()
                with parent_fd(destination) as (current_fd, _):
                    current = os.fstat(current_fd)
                    if (current.st_dev, current.st_ino) != (parent.st_dev, parent.st_ino):
                        raise RuntimeError('Device parent changed before publication')
                if not _owned(fd, temporary, identity):
                    raise RuntimeError('Device temporary file changed before publication')
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                if after_publish is not None:
                    after_publish(fd)
        finally:
            try:
                if _owned(fd, temporary, identity):
                    os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass  # An inaccessible original mount retains only our own stage.


def copy_stream(source: BinaryIO, output: BinaryIO) -> None:
    """Retain CPython's macOS descriptor fast-copy path, with a portable fallback."""
    # The pinned macOS Python exposes the same fcopyfile entry used by shutil.
    # Unlike shutil.copyfile, it accepts our already-open, exclusive destination.
    import posix
    fast_copy = getattr(posix, '_fcopyfile', None)
    data_only = getattr(posix, '_COPYFILE_DATA', None)
    if fast_copy is not None and data_only is not None:
        try:
            fast_copy(source.fileno(), output.fileno(), data_only)
            return
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
            source.seek(0)
            output.seek(0)
            output.truncate()
    shutil.copyfileobj(source, output, 1024 * 1024)
