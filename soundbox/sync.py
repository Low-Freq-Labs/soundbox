"""Copy tracks and write Rockbox playlists to a target folder.

SAFETY CONTRACT (read every comment near a write):
  * The ONLY place this program writes is under `target`.
  * `dry_run=True` (the default for the `dry-run` command) performs NO writes
    at all: no mkdir, no copy, no playlist files. It only plans and reports.
  * Source files are only ever READ (copied via a temp file + atomic rename;
    see _safe_copy). They are never modified, moved, renamed, or deleted.
  * A guard refuses to run if `target` overlaps the source music library, so
    we can never write into the library.
  * Existing target files are never overwritten when their content differs;
    that is reported as a conflict and skipped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
import os
import re
import shutil
import stat as stat_mod
import tempfile
import time
from contextlib import ExitStack, contextmanager
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, cast

from . import artencode
from . import playlists as pl_mod
from . import metadata
from . import manifest
from . import rockbox_config
from .library import Library, Playlist
from .paths import device_music_path, is_external_device_path, is_within
from .report import PlaylistReport, SyncReport
from .operation_control import OperationCancelled, cancellation_disabled, checkpoint, phase_progress, record_metric
from .rockbox_media import RockboxMediaError
from .source_acquisition import SourceContract, SourceAcquisitionError, acquire_source
from .device_staging import (copy_stream, open_parent, staged_output, new_file_output,
                             NewFilePublication, PublicationOwnershipLost)
from .export_snapshot import source_paths
from .ipod_export_metadata import freeze_display_rows, frozen_pair


class NotEnoughSpace(RuntimeError):
    """Raised before any write when the new files won't fit on the target device."""


def _copy_epoch(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_nlink, info.st_uid)


class VerifiedCopyReceipt:
    """Immediate new-copy proof, never valid outside its live FD context."""
    def __init__(self, session: DeviceSession, destination: Path, size: int, sha256: str,
                 publication: NewFilePublication, epoch: tuple[int, ...]):
        self.session = session
        self.destination = destination
        self.size = size
        self.sha256 = sha256
        self.publication = publication
        self.epoch = epoch
        self.active = True

    def require_current(self, session: DeviceSession, destination: Path, *,
                        size: int, sha256: str) -> bool:
        if (not self.active or session is not self.session or destination != self.destination
                or size != self.size or sha256 != self.sha256):
            raise PublicationOwnershipLost('Verified copy receipt no longer owns this media copy')
        if _copy_epoch(self.publication.require_current()) != self.epoch:
            raise PublicationOwnershipLost('Verified new media changed before acknowledgement')
        return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def _destination_key(path: str) -> str:
    """FAT-safe identity: slash-normalized, Unicode-normalized, case-folded."""
    normalized = "/" + "/".join(
        part for part in str(path).replace("\\", "/").split("/") if part)
    return unicodedata.normalize("NFD", normalized).casefold()


class DeviceSession:
    """Pinned, revalidated authority for one mounted export target."""

    def __init__(
            self, target: Path, sources: list[Path], *, create: bool = True,
            allow_external_roots: bool = False):
        requested = target.expanduser()
        # A generic explicit export folder may not exist yet. Creating exactly
        # that target is the session's first and only pre-identity write.
        if create:
            assert_target_safe(requested, sources)
            requested.mkdir(parents=True, exist_ok=True)
        elif not requested.is_dir():
            raise RuntimeError(f"Device target is no longer mounted: {requested}")
        self.target = requested.resolve(strict=True)
        assert_target_safe(self.target, sources)
        stat = self.target.stat()
        self._device = stat.st_dev
        self._inode = stat.st_ino
        self._allow_external_roots = bool(allow_external_roots)

    @property
    def mount_identity(self) -> tuple[int, int]:
        """Pinned filesystem/inode identity used across plan/confirm/commit."""
        return self._device, self._inode

    def revalidate(self) -> None:
        current = self.target.resolve(strict=True)
        stat = current.stat()
        if (current != self.target or stat.st_dev != self._device
                or stat.st_ino != self._inode):
            raise RuntimeError(
                f"Device target changed or was remounted: {self.target}")

    def guard(self, path: Path) -> Path:
        self.revalidate()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.target / candidate
        try:
            relative = candidate.relative_to(self.target)
        except ValueError:
            relative = candidate
        if (
            not self._allow_external_roots
            and is_external_device_path(relative)
        ):
            raise RuntimeError(
                f"REFUSING external device tree reserved for rekordbox/CDJ: "
                f"{candidate}")
        # Resolve the nearest existing parent so a symlink cannot redirect a
        # later mkdir/copy beyond the pinned volume.
        parent = candidate.parent
        while not parent.exists() and parent != self.target:
            parent = parent.parent
        resolved_parent = parent.resolve(strict=True)
        try:
            unresolved_tail = candidate.relative_to(parent)
        except ValueError:
            unresolved_tail = Path(candidate.name)
        effective_candidate = (resolved_parent / unresolved_tail).resolve(
            strict=False)
        if not is_within(effective_candidate, self.target):
            raise RuntimeError(
                f"REFUSING path outside device target: {candidate}")
        if (
            not self._allow_external_roots
            and is_external_device_path(
                effective_candidate.relative_to(self.target))
        ):
            raise RuntimeError(
                f"REFUSING external device tree reserved for rekordbox/CDJ: "
                f"{candidate}")
        return candidate

    @contextmanager
    def parent_fd(self, path: Path, *, create: bool = False) -> Iterator[tuple[int, str]]:
        target = self.guard(path)
        relative = target.relative_to(self.target)
        with open_parent(self.target, self.mount_identity, relative, create=create) as parent:
            self.revalidate()
            yield parent

    def _staged_output(self, destination: Path):
        return staged_output(destination, self.parent_fd, self.revalidate)

    def atomic_copy(self, source: Path, destination: Path, *, expected_sha256: str = '') -> None:
        _safe_copy(source, self.guard(destination), session=self, expected_sha256=expected_sha256)

    @contextmanager
    def atomic_copy_verified(self, source: Path, destination: Path, *,
                             expected_size: int, expected_sha256: str,
                             on_progress: Callable[[str, int, int], None] | None = None,
                             ) -> Iterator[VerifiedCopyReceipt]:
        """New/unindexed files only: expected copy plus one postrename FD readback.

        The returned receipt stays live only inside this context. No native
        index may publish or progress acknowledge this file before consumption.
        Rewrites and ordinary atomic_copy retain their original semantics.
        """
        if (type(expected_size) is not int or expected_size < 0
                or type(expected_sha256) is not str
                or re.fullmatch('[0-9a-f]{64}', expected_sha256) is None):
            raise ValueError('A verified new copy requires its exact captured size and SHA-256')
        destination = self.guard(destination)
        canonical = source.expanduser().resolve(strict=True)
        before = canonical.lstat()
        if not stat_mod.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise OSError('Source payload changed before verified copy')
        descriptor = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        reported = {'copying': time.monotonic(), 'verifying': time.monotonic()}
        def progress(phase, done, *, force=False):
            now = time.monotonic()
            if on_progress is not None and (force or now - reported[phase] >= 0.2):
                on_progress(phase, done, expected_size)
                reported[phase] = now
        with os.fdopen(descriptor, 'rb') as input_file, \
                new_file_output(destination, self.parent_fd, self.revalidate) as publication:
            opened = os.fstat(input_file.fileno())
            if _copy_epoch(opened) != _copy_epoch(before):
                raise OSError('Source payload changed before verified copy')
            space = os.fstatvfs(publication.handle)
            if space.f_bavail * space.f_frsize < expected_size:
                raise OSError(f'Insufficient device space for {destination.name}')
            output = publication.output
            phase_progress('Copying to device', 0, expected_size, destination.name)
            progress('copying', 0, force=True)
            digest = hashlib.sha256()
            size = 0
            while True:
                checkpoint()
                block = input_file.read(1024 * 1024)
                if not block:
                    break
                checkpoint()
                remaining = memoryview(block)
                while remaining:
                    written = output.write(remaining)
                    if type(written) is not int or written <= 0 or written > len(remaining):
                        raise OSError('Device copy did not write its payload bytes')
                    digest.update(remaining[:written])
                    size += written
                    remaining = remaining[written:]
                    if size > expected_size:
                        raise OSError('Source payload changed during verified copy')
                    progress('copying', size)
            progress('copying', size, force=True)
            closed = os.fstat(input_file.fileno())
            after = canonical.lstat()
            source_epoch = _copy_epoch(before)
            if (size != expected_size or digest.hexdigest() != expected_sha256
                    or source_epoch != _copy_epoch(closed) or source_epoch != _copy_epoch(after)
                    or source.resolve(strict=True) != canonical):
                raise OSError('Source payload changed during verified copy')
            record_metric('device_bytes_written', size)
            try:
                os.fchmod(publication.handle, stat_mod.S_IMODE(after.st_mode))
                os.utime(publication.handle, ns=(after.st_atime_ns, after.st_mtime_ns))
            except (OSError, RuntimeError):
                pass  # Same best-effort FAT timestamp/mode policy as atomic_copy.
            if (source_epoch != _copy_epoch(os.fstat(input_file.fileno()))
                    or source_epoch != _copy_epoch(canonical.lstat())
                    or source.resolve(strict=True) != canonical):
                raise OSError('Source payload changed before verified publication')
            publication.publish()
            verified_before = publication.require_current()
            phase_progress('Verifying device copy', 0, expected_size, destination.name)
            progress('verifying', 0, force=True)
            output.seek(0)
            copied_hash = hashlib.sha256()
            verified_size = 0
            for block in iter(lambda: output.read(1024 * 1024), b''):
                checkpoint()
                copied_hash.update(block)
                verified_size += len(block)
                if verified_size < expected_size:
                    progress('verifying', verified_size)
            verified_after = publication.require_current()
            if (verified_size != expected_size or copied_hash.hexdigest() != expected_sha256
                    or _copy_epoch(verified_before) != _copy_epoch(verified_after)):
                raise OSError('Published new media failed verification')
            if (source_epoch != _copy_epoch(os.fstat(input_file.fileno()))
                    or source_epoch != _copy_epoch(canonical.lstat())
                    or source.resolve(strict=True) != canonical):
                raise OSError('Source payload changed during device verification')
            # Close the read-only source before yielding a successful receipt.
            # A provider close error must still retire our unindexed output.
            input_file.close()
            record_metric('device_bytes_verified', verified_size)
            progress('verifying', verified_size, force=True)
            receipt = VerifiedCopyReceipt(self, destination, expected_size, expected_sha256,
                                           publication, _copy_epoch(verified_after))
            try:
                yield receipt
                receipt.require_current(self, destination, size=expected_size, sha256=expected_sha256)
                publication.accept()
            finally:
                receipt.active = False

    def atomic_write(self, destination: Path, data: bytes) -> None:
        with self._staged_output(destination) as output:
            output.write(data)

    def atomic_concat(self, sources: list[Path], destination: Path) -> None:
        """Concatenate verified local payloads into one atomic device file."""
        with self._staged_output(destination) as output:
            for source in sources:
                checkpoint()
                with source.open("rb") as input_file:
                    copy_stream(input_file, output, allow_native=False)

    def write_verified_range(
        self,
        source: Path,
        destination: Path,
        *,
        offset: int,
        size: int,
        sha256: str,
    ) -> None:
        """Write and verify one caller-proven unreferenced device-file range."""
        target = self.guard(destination)
        if offset < 0 or size < 0 or source.stat().st_size != size:
            raise RuntimeError(f"Invalid range write for {target}")
        with source.open("rb") as input_file:
            payload = input_file.read()
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != sha256:
            raise RuntimeError(f"Range source changed before write: {source}")
        with self.parent_fd(target) as (parent, name):
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, 'r+b') as handle:
                info = os.fstat(handle.fileno())
                if (not stat_mod.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_dev != self.mount_identity[0]):
                    raise RuntimeError(f"Range target is not a regular device file: {target}")
                if offset > info.st_size:
                    raise RuntimeError(
                        f"Range write would create a hole in {target}: {offset}>{info.st_size}")
                self.revalidate()
                handle.seek(offset)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                handle.seek(offset)
                landed = handle.read(size)
                if len(landed) != size or hashlib.sha256(landed).hexdigest() != sha256:
                    raise RuntimeError(f"Range write failed verification: {target}@{offset}")
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise RuntimeError(f"Range target changed during write: {target}")
            with self.parent_fd(target) as (current_parent, _):
                current = os.fstat(current_parent)
                original = os.fstat(parent)
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    raise RuntimeError(f"Range parent changed during write: {target}")
        self.revalidate()


def _fmt_size(n: int) -> str:
    """Human bytes in decimal units (matches Finder), for space messages."""
    f = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1000 or u == "TB":
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} TB"


@dataclass
class PlannedFile:
    source: Path
    device_path: str          # '/Music/Artist/Track/Track.flac'
    target_path: Path
    size: int
    track_id: str = ""        # to look up this track's cover art
    title: str = ""
    artist: str = ""
    source_mtime_ns: int = 0
    # Canonical library file when ``source`` is a disposable Rockbox-safe
    # derivative.  Source audio itself is never modified.
    original_source: Path | None = None
    payload_profile: str = ""
    removed_embedded_images: int = 0

    @property
    def canonical_source(self) -> Path:
        return self.original_source or self.source


@dataclass(frozen=True)
class DeviceDeleteReport:
    """Exact outcome of one explicitly confirmed browser deletion."""

    deleted: int
    total: int
    errors: tuple[str, ...] = ()
    cancelled: bool = False


def _delete_device_tree(session: DeviceSession, relative: Path) -> None:
    """Delete one guarded device entry without following directory symlinks."""
    path = session.guard(relative)
    session.revalidate()
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        for child in list(path.iterdir()):
            _delete_device_tree(session, child.relative_to(session.target))
        session.revalidate()
        session.guard(relative).rmdir()
        return
    path.unlink()


def delete_device_paths(
    session: DeviceSession,
    relatives: list[Path],
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DeviceDeleteReport:
    """Delete a confirmed set of paths through an already pinned session.

    The UI creates ``session`` before showing its confirmation dialog. A
    same-name remount while that dialog is open therefore fails revalidation
    instead of granting authority over the replacement volume.
    """
    normalized: list[Path] = []
    for raw in relatives:
        relative = Path(raw)
        if relative.is_absolute() or relative in {Path(), Path(".")}:
            raise RuntimeError(
                f"REFUSING non-relative device deletion path: {relative}")
        guarded = session.guard(relative)
        canonical = guarded.relative_to(session.target)
        if any(parent == canonical or parent in canonical.parents
               for parent in normalized):
            continue
        normalized = [
            item for item in normalized if canonical not in item.parents]
        normalized.append(canonical)

    total = len(normalized)
    deleted = 0
    errors: list[str] = []
    for relative in normalized:
        if should_cancel is not None and should_cancel():
            return DeviceDeleteReport(
                deleted, total, tuple(errors), cancelled=True)
        try:
            _delete_device_tree(session, relative)
            deleted += 1
        except OSError as exc:
            errors.append(f"{relative.name}: {exc}")
        if on_progress is not None:
            on_progress(deleted + len(errors), total, relative.name)
    session.revalidate()
    return DeviceDeleteReport(deleted, total, tuple(errors))


def _dirs_up_to(start_dir: Path, root: Path) -> list[Path]:
    """`start_dir` and each parent up to and including `root` (assumes start_dir is
    inside root — true for our constructed device paths)."""
    out, p = [], start_dir
    while True:
        out.append(p)
        if p == root or p.parent == p:
            break
        p = p.parent
    return out


def _clean_dir(session: DeviceSession, directory: Path, *, recursive: bool = False) -> int:
    """Inspect real device directories; unlink clutter by the same pinned parent."""
    removed = 0
    try:
        with session.parent_fd(directory / '.soundbox-clean') as (fd, _):
            parent = os.fstat(fd)
            with os.scandir(fd) as scan:
                entries = list(scan)
            for entry in entries:
                if recursive and entry.is_dir(follow_symlinks=False):
                    removed += _clean_dir(session, directory / entry.name, recursive=True)
                elif ((entry.name.startswith('._') or entry.name.endswith('.sbpart'))
                      and entry.is_file(follow_symlinks=False)):
                    observed = entry.stat(follow_symlinks=False)
                    with session.parent_fd(directory / entry.name) as (current_fd, name):
                        current_parent = os.fstat(current_fd)
                        current = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                        if ((current_parent.st_dev, current_parent.st_ino)
                                != (parent.st_dev, parent.st_ino)
                                or (current.st_dev, current.st_ino)
                                != (observed.st_dev, observed.st_ino)
                                or not stat_mod.S_ISREG(current.st_mode)):
                            continue
                        session.revalidate()
                        os.unlink(name, dir_fd=current_fd)
                        removed += 1
    except (OSError, RuntimeError, ValueError):
        pass  # Optional clutter cleanup never follows a redirected directory.
    return removed


def clean_applefiles(
        target: Path, dirs=None, *, session: DeviceSession | None = None,
        expected_mount_identity: tuple[int, int] | None = None,
) -> int:
    """Remove AppleDouble/partial files only in real Music and Playlists trees.

    Export finalization scans only the supplied written directories, shallowly.
    An explicit manual clean recursively visits those two roots without following
    directory links. Neither mode opens or unlinks another product/source tree.
    """
    target = Path(target)
    if session is not None:
        session.revalidate()
        if target.expanduser().resolve(strict=True) != session.target:
            raise RuntimeError("Cleanup target differs from the captured device")
    if not target.is_dir():
        return 0
    if session is None:
        session = DeviceSession(target, [], create=False)
    if (expected_mount_identity is not None
            and session.mount_identity != expected_mount_identity):
        raise RuntimeError('Device target changed after export; cleanup refused')
    if dirs is None:
        removed = sum(_clean_dir(session, session.target / name, recursive=True)
                      for name in ('Music', 'Playlists'))
        session.revalidate()
        return removed
    removed = 0
    for directory in {Path(value) for value in dirs}:
        try:
            try:
                relative = directory.relative_to(target)
            except ValueError:
                relative = directory.relative_to(session.target)
            if (not relative.parts or relative.parts[0].casefold() not in ('music', 'playlists')
                    or '..' in relative.parts):
                continue
            removed += _clean_dir(session, session.target / relative)
        except ValueError:
            continue
    session.revalidate()
    return removed


def erase_device_contents(
    target: Path, sources: list[Path], *,
    expected_mount_identity: tuple[int, int] | None = None,
) -> int:
    """Permanently empty one explicitly selected mounted device.

    This is intentionally separate from ownership-based Sync pruning. It may
    remove unknown content, but only after the UI's explicit whole-device
    confirmation. The mount root itself is never removed. The SoundBox manifest
    is deleted last, so a partial erase retains its repair/eject warning rather
    than publishing an empty successful state.
    """
    # Whole-device Erase is the sole explicit exception to ordinary ownership
    # boundaries. Its confirmation names every unknown/external tree too.
    session = DeviceSession(
        Path(target), sources, create=False, allow_external_roots=True)
    if (expected_mount_identity is not None
            and session.mount_identity != expected_mount_identity):
        raise RuntimeError('Device target changed after erase confirmation')
    session.revalidate()
    entries = list(session.target.iterdir())
    ownership = manifest.manifest_path(session.target).parent
    entries.sort(key=lambda path: path == ownership)
    removed = 0
    errors: list[str] = []

    for path in entries:
        if path == ownership and errors:
            break
        try:
            session.revalidate()
            guarded = session.guard(path)
            if guarded.is_symlink():
                guarded.unlink()
            elif guarded.is_dir():
                shutil.rmtree(guarded)
            else:
                guarded.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    session.revalidate()
    if errors:
        raise RuntimeError(
            "Device was only partially erased; its SoundBox sync record was "
            "preserved:\n" + "\n".join(errors[:10]))

    # macOS can recreate these volume-index folders while a mounted device is
    # being observed. They contain no user media and do not constitute a
    # partial SoundBox erase.
    volatile = {".fseventsd", ".Spotlight-V100", ".Trashes"}
    remaining = [
        path.name for path in session.target.iterdir()
        if path.name not in volatile
    ]
    if remaining:
        raise RuntimeError(
            "Device was not empty after erase: " + ", ".join(remaining[:10]))
    return removed


def verify_managed(target: Path, *, deep: bool = False) -> list[str]:
    """Return integrity problems for manifest-managed device files.

    Normal verification checks references and sizes. Deep Verify additionally
    rehashes every managed file, including same-size corruption.
    """
    root = Path(target).expanduser().resolve()
    session = DeviceSession(root, [], create=False)
    current = (manifest.load_composite(root, session=session)
               if manifest.has_composite_ownership(root, session=session)
               else manifest.load(root, session=session))
    issues: list[str] = []
    seen: set[str] = set()
    for device_path, row in (current.get("files") or {}).items():
        key = _destination_key(str(device_path))
        if key in seen:
            issues.append(f"destination collision: {device_path}")
            continue
        seen.add(key)
        path = root / str(device_path).lstrip("/")
        try:
            path = session.guard(path)
            resolved = path.resolve(strict=True)
            if not is_within(resolved, root) or not resolved.is_file():
                issues.append(f"missing or escaped: {device_path}")
                continue
            size = resolved.stat().st_size
            if size != int(row.get("size") or -1):
                issues.append(f"wrong size: {device_path}")
                continue
            if deep and _sha256(resolved) != str(row.get("sha256") or ""):
                issues.append(f"checksum mismatch: {device_path}")
        except (OSError, RuntimeError):
            issues.append(f"missing or unreadable: {device_path}")
    return issues


def _safe_copy(
    src: Path, dst: Path, *, revalidate: Callable[[], None] | None = None,
    session: DeviceSession | None = None, expected_sha256: str = '',
) -> None:
    """Copy file DATA to a temp file in the destination folder, then ATOMICALLY
    rename it into place.

    Writing straight to the final name means a crash / yanked USB / power loss
    mid-copy leaves a TRUNCATED file at the real name — which the next sync's
    size-only check then treats as a permanent 'conflict' and never heals. Copying
    to an exclusively created `.soundbox-*.sbpart` first means an interruption leaves
    only an orphan temp (swept by clean_applefiles), and the real file only ever
    appears complete. os.replace is atomic within one filesystem, including FAT.

    Timestamp/mode preservation is best-effort on FAT/fskit. All production calls
    use the session's pinned parent; the standalone helper pins its given parent.
    """
    if session is None:
        dst = dst.parent.resolve(strict=True) / dst.name
        parent_info = dst.parent.stat()
        identity = (parent_info.st_dev, parent_info.st_ino)

        @contextmanager
        def parent_fd(path: Path, *, create: bool = False) -> Iterator[tuple[int, str]]:
            with open_parent(dst.parent, identity, path.relative_to(dst.parent)) as parent:
                yield parent

        staging = staged_output(dst, parent_fd, revalidate or (lambda: None))
    else:
        staging = session._staged_output(dst)
    with staging as output:
        canonical = src.resolve(strict=True)
        before = canonical.lstat()
        if not stat_mod.S_ISREG(before.st_mode):
            raise OSError(f'Source payload is not a regular file: {src}')
        source_epoch = _copy_epoch(before)
        size = before.st_size
        phase_progress("Verifying payload", 0, size, dst.name)
        # A captured expected digest is proved again by the independently read
        # copied bytes below. Prehashing the same source adds a redundant pass.
        # Uncontracted callers still establish their original fresh digest.
        source_hash = expected_sha256 or _sha256(src)
        space = os.fstatvfs(output.fileno())
        if space.f_bavail * space.f_frsize < size:
            raise OSError(f"Insufficient device space for {dst.name}")
        phase_progress("Copying to device", 0, size, dst.name)
        with canonical.open('rb') as source:
            if (_copy_epoch(os.fstat(source.fileno())) != source_epoch
                    or _copy_epoch(canonical.lstat()) != source_epoch
                    or src.resolve(strict=True) != canonical):
                raise OSError(f'Source payload changed before copy: {src}')
            copy_stream(source, output)
            if (_copy_epoch(os.fstat(source.fileno())) != source_epoch
                    or _copy_epoch(canonical.lstat()) != source_epoch
                    or src.resolve(strict=True) != canonical):
                raise OSError(f'Source payload changed during copy: {src}')
        output.flush()
        record_metric("device_bytes_written", size)
        phase_progress("Verifying device copy", size, size, dst.name)
        output.seek(0)
        copied_hash = hashlib.sha256()
        for chunk in iter(lambda: output.read(1024 * 1024), b''):
            checkpoint()
            copied_hash.update(chunk)
        if os.fstat(output.fileno()).st_size != size or copied_hash.hexdigest() != source_hash:
            message = ('Source payload changed or temporary copy failed verification'
                       if expected_sha256 else 'Temporary copy failed verification')
            raise OSError(f'{message}: {dst}')
        if revalidate is not None:
            revalidate()
        try:
            os.fchmod(output.fileno(), stat_mod.S_IMODE(before.st_mode))
            os.utime(output.fileno(), ns=(before.st_atime_ns, before.st_mtime_ns))
        except (OSError, RuntimeError):
            pass                             # FAT can't store full metadata


def assert_target_safe(target: Path, sources: list[Path]) -> None:
    """Refuse to proceed if the target overlaps any source location.

    Resolve every source parent and full source freshly. The target is one
    canonical boundary for this pass and must still resolve there afterward.
    """
    requested = target.expanduser()
    target = requested.resolve()
    for src in sources:
        # Keep the two independent fresh source resolutions, including repeated
        # lexical parents: a source directory or terminal link can retarget.
        # Match is_within's existing treatment of unavailable source paths.
        try:
            parent = src.parent.expanduser().resolve()
        except (OSError, RuntimeError):
            parent = None
        overlaps = parent is not None and target.is_relative_to(parent)
        if not overlaps:
            try:
                source = src.expanduser().resolve()
            except (OSError, RuntimeError):
                source = None
            overlaps = source is not None and source.is_relative_to(target)
        if overlaps:
            raise RuntimeError(
                f"REFUSING: target {target} overlaps source library near {src.parent}. "
                "Choose a target that is outside your music library."
            )
    if requested.resolve() != target:
        raise RuntimeError('Device target changed during source overlap validation')


def plan(
    lib: Library,
    selected: list[Playlist],
    target: Path,
    derive_artists: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    existing_files: dict | None = None,
    display_rows: Mapping[str, Mapping] | None = None,
) -> tuple[dict[str, PlannedFile], dict[int, list[pl_mod.M3UEntry]]]:
    """Build the copy plan + playlist entries. READ ONLY (no writes).

    Returns (unique_files_by_devicepath, entries_per_playlist_index).
    Deduplicates source files across playlists and resolves device-path
    collisions deterministically.
    """
    display_rows = freeze_display_rows(lib, display_rows, titles=title_overrides, artists=artist_overrides)
    music_root = target / "Music"
    files: dict[str, PlannedFile] = {}          # device_path -> PlannedFile
    occupied_keys: set[str] = set()            # normalized FAT file/folder identities
    occupied_sources: dict[str, str] = {}
    for path, row in (existing_files or {}).items():
        if not isinstance(row, dict) or row.get("kind") != "track":
            continue
        key = _destination_key(path)
        source = str(row.get("source") or "")
        for identity in (key, key.rsplit("/", 1)[0]):
            occupied_keys.add(identity)
            # Legacy shared folders may already have multiple sources. Never
            # reuse an ambiguous artwork directory for a new payload.
            prior = occupied_sources.get(identity, source)
            occupied_sources[identity] = source if prior == source else ""
    src_to_device: dict[str, str] = {}          # source str -> device_path
    entries_by_pl: dict[int, list[pl_mod.M3UEntry]] = {}
    observations: dict[Path, os.stat_result | None] = {}
    source_keys: dict[Path, str] = {}
    display_by_track: dict[str, tuple[str, str]] = {}
    display_by_source: dict[str, tuple[str, str]] = {}

    for idx, plist in enumerate(selected):
        entries: list[pl_mod.M3UEntry] = []
        for track in lib.resolve(plist):
            src = track.path
            if src not in observations:
                try:
                    current = src.stat()
                    record_metric("source_metadata_observations")
                    observations[src] = current if stat_mod.S_ISREG(current.st_mode) else None
                except OSError:
                    observations[src] = None
            observation = observations[src]
            if observation is None:
                captured = frozen_pair(display_rows, track.track_id)
                entries.append(pl_mod.M3UEntry(None, captured[1] if captured else track.artist,
                                              captured[0] if captured else track.name))
                continue

            src_key = source_keys.get(src)
            if src_key is None:
                src_key = str(src.expanduser().resolve())
                source_keys[src] = src_key
            display = display_by_track.get(track.track_id)
            if display is None:
                display = frozen_pair(display_rows, track.track_id)
            if display is None:
                from .engine import effective_pair
                title,artist=effective_pair(track,artist_overrides,title_overrides,derive_artists,
                    library=lib,stem=src.stem,registry=cast(
                        metadata.ArtistRegistry | metadata.FrozenArtistRegistry | None,lib.artist_registry))
                display = (title, artist)
                display_by_track[track.track_id] = display
            previous_display = display_by_source.get(src_key)
            if previous_display is not None and previous_display != display:
                raise RockboxMediaError(
                    "One source file resolved to conflicting Rockbox metadata: "
                    f"{src_key}")
            display_by_source[src_key] = display
            title, artist = display
            device_path = src_to_device.get(src_key)
            if device_path is None:
                device_path = _assign_device_path(
                    src, artist, files, music_root, title,
                    occupied_keys=occupied_keys, occupied_sources=occupied_sources)
                key = _destination_key(device_path)
                for identity in (key, key.rsplit("/", 1)[0]):
                    occupied_keys.add(identity)
                    occupied_sources[identity] = src_key
                src_to_device[src_key] = device_path
                files[device_path] = PlannedFile(
                    source=src,
                    device_path=device_path,
                    target_path=target / device_path.lstrip("/"),
                    size=observation.st_size, source_mtime_ns=observation.st_mtime_ns,
                    track_id=track.track_id,
                    title=title, artist=artist,
                    original_source=src,
                )
            captured = frozen_pair(display_rows, track.track_id)
            entries.append(pl_mod.M3UEntry(device_path, captured[1] if captured else track.artist,
                                          captured[0] if captured else track.name))
        entries_by_pl[idx] = entries
    return files, entries_by_pl


def _assign_device_path(
    src: Path, artist: str, files: dict[str, PlannedFile], music_root: Path,
    name_override: str | None = None,
    *, occupied_keys: set[str] | None = None,
    occupied_sources: dict[str, str] | None = None,
) -> str:
    """Give each source its own FAT-safe audio and artwork directory."""
    base = device_music_path(src, artist, name_override=name_override)
    owners = occupied_sources
    if owners is None:
        owners = {}
        for path, planned in files.items():
            key = _destination_key(path)
            source = str(planned.canonical_source.expanduser().resolve())
            for identity in (key, key.rsplit("/", 1)[0]):
                prior = owners.get(identity, source)
                owners[identity] = source if prior == source else ""
    occupied = occupied_keys if occupied_keys is not None else set(owners)
    source = str(src.expanduser().resolve())

    def available(candidate: str) -> bool:
        key = _destination_key(candidate)
        return all(identity not in occupied or owners.get(identity) == source
                   for identity in (key, key.rsplit("/", 1)[0]))

    if available(base):
        return base
    # Disambiguate the directory too: cover.bmp is folder-scoped in Rockbox.
    # Equal artist/title with different codecs must not share either cover.
    base_path = Path(base)
    suffix = hashlib.sha1(source.encode()).hexdigest()[:6]
    counter = 1
    while True:
        token = suffix if counter == 1 else f"{suffix}-{counter}"
        folder = base_path.parent.with_name(f"{base_path.parent.name}_{token}")
        candidate = str(folder / f"{base_path.stem}_{token}{base_path.suffix}")
        if available(candidate):
            return candidate
        counter += 1


def _artwork_sides(rows: dict[str, dict]) -> set[int]:
    sides = set(artencode.ROCKBOX_COVER_SIDES)
    for path, row in rows.items():
        if row.get("kind") == "artwork":
            match = re.search(r"\.([0-9]{1,3})x\1\.bmp$", path)
            if match and 1 <= int(match.group(1)) <= 240:
                sides.add(int(match.group(1)))
    return sides


def _artwork_variants(rockbox_art_cache, sides):
    if rockbox_art_cache is None:
        return ((None, "cover.jpg"),)
    return (*((side, f"cover.{side}x{side}.bmp") for side in sides),
            (artencode.ROCKBOX_FALLBACK_SIDE, "cover.bmp"))


def _artwork_source(track_id, art_cache, rockbox_art_cache):
    if art_cache is None:
        return None
    image = art_cache.image_path(track_id)
    if image is None:
        return None
    image = Path(image).resolve(strict=True)
    info = image.stat()
    from .rockbox_media import ART_PROFILE
    profile = ART_PROFILE if rockbox_art_cache is not None else "original"
    identity = (f"{info.st_dev}:{info.st_ino}:{info.st_size}:"
                f"{info.st_mtime_ns}:{info.st_ctime_ns}:{profile}")
    return image, identity


def _artwork_current(rows, parent, source, variants, track_id):
    image, identity = source
    return all(
        (row := rows.get(str(parent / filename), {})).get("kind") == "artwork"
        and row.get("source") == str(image)
        and row.get("source_identity") == identity
        and row.get('track_id') == track_id and bool(row.get('source_sha256'))
        for _side, filename in variants)


def _export_artwork(
        planned, target, session, rows, memberships, artwork_by_parent,
        art_cache, rockbox_art_cache, exact_sides, written_dirs, *, reuse_inventory,
        validate_artwork: Callable[[str], None] | None = None):
    """Artwork follows its own inputs, independently of retained audio."""
    parent = Path(planned.device_path).parent
    source = _artwork_source(planned.track_id, art_cache, rockbox_art_cache)
    if source is None:
        _validate_artwork_choice(validate_artwork, planned.track_id)
        # An unavailable local image is not an instruction to clear a cover.
        for path in artwork_by_parent.get(str(parent), ()):
            row = rows[path]
            row["playlists"] = sorted(set(row.get("playlists") or ()) | memberships)
        return
    image, identity = source
    variants = _artwork_variants(rockbox_art_cache, exact_sides)
    if reuse_inventory and _artwork_current(rows, parent, source, variants, planned.track_id):
        _validate_artwork_choice(validate_artwork, planned.track_id)
        for _side, filename in variants:
            row = rows[str(parent / filename)]
            row["playlists"] = sorted(set(row.get("playlists") or ()) | memberships)
        return
    for side, filename in variants:
        path = str(parent / filename)
        previous = rows.get(path) or {}
        prepared = rockbox_art_cache.prepare(image, side=side) if side is not None else None
        payload = prepared.payload if prepared else image
        digest = prepared.sha256 if prepared else _sha256(payload)
        size = prepared.size if prepared else payload.stat().st_size
        _validate_artwork_choice(validate_artwork, planned.track_id)
        if not (reuse_inventory and previous.get("owned") is True
                and previous.get("sha256") == digest and previous.get("size") == size):
            cover = session.guard(target / path.lstrip("/"))
            present = cover.exists()
            if present and _sha256(cover) != digest:
                if previous.get("owned") is not True:
                    raise OSError(f"unowned artwork conflict at {cover}")
                session.atomic_copy(payload, cover)
                written_dirs.update(_dirs_up_to(cover.parent, target / "Music"))
            elif not present:
                session.atomic_copy(payload, cover)
                written_dirs.update(_dirs_up_to(cover.parent, target / "Music"))
        else:
            present = True
        rows[path] = {
            "kind": "artwork",
            "owned": bool(previous.get("owned", True)) if previous else not present,
            "source": str(image), "source_identity": identity,
            "track_id": planned.track_id,
            "source_sha256": prepared.source_sha256 if prepared else digest,
            "size": size, "sha256": digest,
            "playlists": sorted(set(previous.get("playlists") or ()) | memberships),
        }


class _ArtworkChoiceChanged(OSError):
    """Captured selected cover intent changed after earlier payloads landed."""


def _validate_artwork_choice(callback: Callable[[str], None] | None, track_id: str) -> None:
    """A changed selected cover stops this combined export honestly as partial."""
    if callback is None:
        return
    try:
        callback(track_id)
    except OperationCancelled:
        raise
    except Exception as exc:
        raise _ArtworkChoiceChanged(f'Selected artwork changed during export: {exc}') from exc


def _captured_export_manifest(target: Path, session: DeviceSession | None, *,
        expected_payload: bytes | None, expected_absent: bool) -> dict:
    """Bind explicit iPod managed selection to one fresh exact payload."""
    payload = manifest.read_device_manifest(target, session=session)
    if ((expected_absent and payload is not None)
            or (expected_payload is not None and payload != expected_payload)):
        raise RuntimeError('The managed iPod catalog changed after capture; make a fresh export')
    if payload is not None:
        try:
            data = json.loads(payload)
        except (UnicodeError, ValueError):
            data = None
        if isinstance(data, dict) and int(data.get('schema') or 0) == manifest.COMPOSITE_SCHEMA:
            raise RuntimeError('Rockbox export cannot replace composite dual-library ownership')
    return manifest.parse(payload)


def run(
    lib: Library,
    selected: list[Playlist],
    target: Path,
    layout: str = "both",
    dry_run: bool = True,
    derive_artists: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
    rockbox_media_cache=None,
    rockbox_art_cache=None,
    reuse_managed_inventory: bool = False,
    _session: DeviceSession | None = None,
    display_rows: Mapping[str, Mapping] | None = None,
    validate_inputs: Callable[[], None] | None = None,
    validate_artwork: Callable[[str], None] | None = None,
    expected_manifest_payload: bytes | None = None,
    expected_manifest_absent: bool = False,
    concise_playlists: bool | None = None,
) -> SyncReport:
    """Execute (or simulate, if dry_run) the sync. All writes are under target.

    on_progress(done, total, label) is called once per file processed, for a
    live progress bar. should_cancel() is polled before each file; if it returns
    True the copy stops cleanly (files already copied are kept).
    """
    target = target.expanduser().resolve()
    if concise_playlists is None:
        concise_playlists = rockbox_media_cache is not None or (target / '.rockbox').is_dir()
    if concise_playlists:
        from .playlist_names import unique_playlist_names
        unique_playlist_names(playlist.name for playlist in selected)
    guard_paths = list(source_paths(lib))
    assert_target_safe(target, guard_paths)
    session = _session
    if session is not None:
        session.revalidate()
        if target != session.target:
            raise RuntimeError("Export target differs from the captured device")
    elif target.exists():
        session = DeviceSession(target, guard_paths, create=False)
    if session is not None and manifest.has_composite_ownership(target, session=session):
        raise RuntimeError(
            "This device uses SoundBox composite dual-library ownership; "
            "the Rockbox-only exporter is disabled for it.")

    # A missing explicit export folder has no prior device metadata. Never
    # adopt a root that appears while this source-only plan is being built.
    previous_manifest = (_captured_export_manifest(target, session,
        expected_payload=expected_manifest_payload, expected_absent=expected_manifest_absent)
        if expected_manifest_payload is not None or expected_manifest_absent else
        manifest.load(target, session=session) if session is not None else manifest._empty())
    from .export_selection import retain_projection
    selected = [retain_projection(playlist,previous_manifest.get('playlists',{}).get(
                    manifest.playlist_key(playlist),{})) for playlist in selected]
    files, entries_by_pl = plan(lib, selected, target, derive_artists=derive_artists,
                                artist_overrides=artist_overrides,
                                title_overrides=title_overrides,
                                existing_files=previous_manifest.get("files"), display_rows=display_rows)
    assert_target_safe(target, [pf.canonical_source for pf in files.values()])
    if session is not None:
        session.revalidate()
    elif not dry_run:
        if validate_inputs is not None:
            validate_inputs()
        if target.exists():
            raise RuntimeError(f"Device target appeared during export planning: {target}")
        session = DeviceSession(
            target,
            [*guard_paths, *[path for pf in files.values()
             for path in (pf.canonical_source, pf.source)]])
        validate_inputs = None  # The one admission check preceded root creation.
    managed_files = {
        str(path): dict(row)
        for path, row in (previous_manifest.get("files") or {}).items()
        if isinstance(row, dict)
    }
    retained_art_sides = _artwork_sides(managed_files)
    if session is not None:
        retained_art_sides.update(
            rockbox_config.theme_album_art_sides(target, session=session))
        session.revalidate()
    exact_art_sides = tuple(sorted(retained_art_sides, reverse=True))
    selected_keys = {manifest.playlist_key(pl) for pl in selected}
    for row in managed_files.values():
        row["playlists"] = [
            key for key in (row.get("playlists") or [])
            if key not in selected_keys
        ]
    memberships: dict[str, set[str]] = {}
    for index, plist in enumerate(selected):
        key = manifest.playlist_key(plist)
        for entry in entries_by_pl[index]:
            if entry.device_path is not None:
                memberships.setdefault(entry.device_path, set()).add(key)
    artwork_by_parent: dict[str, list[str]] = {}
    for path, row in managed_files.items():
        if row.get("kind") == "artwork":
            artwork_by_parent.setdefault(str(Path(path).parent), []).append(path)

    # --- Free-space precheck ---------------------------------------------
    # Refuse to start writing if the NEW files won't fit, with one clear message,
    # instead of failing file-by-file partway through a multi-GB copy (and leaving a
    # half-populated device). Two-tier so it's cheap: `gross` (sum of planned sizes)
    # uses sizes already gathered by plan() — NO device I/O. Only when the worst case
    # exceeds free space do we pay for the precise per-file "already present?" check.
    if not dry_run and files:
        try:
            st = os.statvfs(target)
            free = st.f_bavail * st.f_frsize
        except OSError:
            free = None
        if free is not None:
            gross = sum(pf.size for pf in files.values())
            if gross > free:
                present = 0
                for pf in files.values():
                    try:
                        if pf.target_path.exists() and pf.target_path.stat().st_size == pf.size:
                            present += pf.size  # already on the device — won't re-copy
                    except OSError:
                        pass
                need = gross - present
                if need > free:
                    raise NotEnoughSpace(
                        f"Not enough space on {target.name or target}: needs "
                        f"{_fmt_size(need)} but only {_fmt_size(free)} is free. "
                        f"Delete some tracks from the device or export fewer playlists.")

    report = SyncReport(dry_run=dry_run)
    if not dry_run and session is not None:
        report.mount_identity = session.mount_identity
    report.sanitized_media = sum(
        1 for planned in files.values()
        if planned.removed_embedded_images)
    playlists_root = target / "Playlists"
    music_root = target / "Music"

    if validate_inputs is not None:
        validate_inputs()

    # --- Copy phase -------------------------------------------------------
    copied_paths: set[str] = set()
    already: set[str] = set()
    conflicts: set[str] = set()
    global_errors: list[str] = []
    bytes_copied = 0
    written_dirs: set[Path] = set()  # folders we actually wrote → scoped AppleDouble scrub

    total = len(files)
    # Emit an immediate 0/total so the UI bar is DETERMINATE from the first frame
    # (no indeterminate "candy cane") — the verify/copy of many files can take a
    # while, and a real 0%→100% bar reads far better than a barber-pole.
    if on_progress:
        on_progress(0, total, "")
    cancelled = False
    for done, (device_path, pf) in enumerate(files.items(), 1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        tgt = pf.target_path
        previous = managed_files.get(device_path) or {}
        if reuse_managed_inventory and previous.get("owned") is True:
            from .rockbox_media import NORMALIZED_MEDIA_PROFILE
            profile = NORMALIZED_MEDIA_PROFILE if rockbox_media_cache is not None else "original"
            expected_identity = f"{pf.size}:{pf.source_mtime_ns}:{profile}"
            if (previous.get("source") == str(pf.canonical_source.resolve())
                    and previous.get("source_identity") == expected_identity
                    and previous.get("title") == pf.title
                    and previous.get("artist") == pf.artist):
                # Ordinary Sync retains committed inventory. Explicit Export
                # still captures and checks live bytes, including repair.
                previous["playlists"] = sorted(
                    set(previous.get("playlists") or ())
                    | memberships.get(device_path, set()))
                already.add(device_path)
                record_metric("retained_device_media")
                if not dry_run:
                    try:
                        _export_artwork(
                            pf, target, session, managed_files, memberships.get(device_path, set()),
                            artwork_by_parent, art_cache, rockbox_art_cache, exact_art_sides,
                            written_dirs, reuse_inventory=True, validate_artwork=validate_artwork)
                    except OperationCancelled:
                        cancelled = True
                        break
                    except _ArtworkChoiceChanged as exc:
                        global_errors.append(f"ERROR writing artwork for {pf.source}: {exc}")
                        break
                    except (OSError, RockboxMediaError) as exc:
                        global_errors.append(f"ERROR writing artwork for {pf.source}: {exc}")
                if on_progress:
                    on_progress(done, total, pf.source.name)
                continue
        staged = ExitStack()
        original_source = pf.source
        try:
            if session is not None:
                session.guard(tgt)
            source_stat = pf.canonical_source.stat()
            captured_hash = ""
            source_size = source_stat.st_size
            source_mtime_ns = source_stat.st_mtime_ns
            if not dry_run:
                if on_progress:
                    on_progress(done - 1, total, f"Acquiring {pf.canonical_source.name}")
                if rockbox_media_cache is not None:
                    prepared = rockbox_media_cache.prepare(
                        pf.canonical_source, title=pf.title, artist=pf.artist)
                    staged.callback(rockbox_media_cache.discard, prepared)
                    pf.source = prepared.payload
                    pf.size = prepared.payload_size
                    pf.payload_profile = prepared.profile
                    pf.removed_embedded_images = prepared.removed_images
                    report.sanitized_media += bool(prepared.removed_images)
                    captured_hash = prepared.payload_sha256
                    source_size = prepared.source_size
                    source_mtime_ns = prepared.source_mtime_ns
                else:
                    snapshot = staged.enter_context(acquire_source(
                        SourceContract(pf.canonical_source.resolve()),
                        Path(tempfile.gettempdir()) / "soundbox-source-staging",
                        should_cancel=should_cancel))
                    pf.source = snapshot.path
                    pf.size = snapshot.size
                    captured_hash = snapshot.sha256
                    source_size = snapshot.size
                    source_mtime_ns = snapshot.observation.st_mtime_ns
            source_identity = (
                f"{source_size}:{source_mtime_ns}"
                f":{pf.payload_profile or 'original'}")
            previous = managed_files.get(device_path) or {}
            expected_hash = (
                str(previous.get("sha256") or "")
                if (previous.get("source_identity") == source_identity
                    and int(previous.get("size") or -1) == pf.size)
                else "")
            # Captured bytes outrank a previous stat-only manifest hint.
            expected_hash = captured_hash or _sha256(pf.source)
            was_present = tgt.exists()
            if was_present:
                actual_hash = (
                    _sha256(tgt) if tgt.stat().st_size == pf.size else "")
                if actual_hash == expected_hash:
                    already.add(device_path)
                elif (device_path in managed_files and previous.get("owned", False)
                      and session is not None):
                    # Only a manifest-owned destination may be repaired.
                    session.atomic_copy(pf.source, tgt, expected_sha256=expected_hash)
                    bytes_copied += pf.size
                    copied_paths.add(device_path)
                    written_dirs.update(_dirs_up_to(tgt.parent, music_root))
                else:
                    conflicts.add(device_path)
                    global_errors.append(f"CONFLICT (kept existing, not overwritten): {tgt}")
            elif dry_run:
                # Plan only: count as would-copy, write nothing.
                bytes_copied += pf.size
                copied_paths.add(device_path)
            else:
                # WRITE: session revalidates the pinned mount and atomic path.
                assert session is not None
                session.atomic_copy(pf.source, tgt, expected_sha256=expected_hash)
                bytes_copied += pf.size  # count only AFTER a successful copy
                copied_paths.add(device_path)
                written_dirs.update(_dirs_up_to(tgt.parent, music_root))
            if not dry_run and device_path in copied_paths:
                # _safe_copy proved the captured digest on its open temporary
                # before publication. Do not reread the whole device file here;
                # cancellation must still record ownership of that completed copy.
                if tgt.stat().st_size != pf.size:
                    copied_paths.discard(device_path)
                    raise OSError(f"Copied payload failed verification: {tgt}")
            if device_path in already or device_path in copied_paths:
                managed_files[device_path] = {
                    "kind": "track",
                    "owned": (
                        bool(previous.get("owned", True))
                        if previous else not was_present),
                    "source": str(pf.canonical_source.resolve()),
                    "source_identity": source_identity,
                    "title": pf.title, "artist": pf.artist,
                    "size": pf.size,
                    "sha256": expected_hash,
                    "playlists": sorted(
                        set(previous.get("playlists") or ())
                        | memberships.get(device_path, set())),
                }
        except OperationCancelled:
            cancelled = True
            break
        except (OSError, RockboxMediaError, SourceAcquisitionError) as exc:
            global_errors.append(f"ERROR copying {pf.canonical_source} -> {tgt}: {exc}")
            cancelled = bool(should_cancel and should_cancel())
            # Preserve completed files, but stop work and suppress cleanup.
            break
        finally:
            staged.close()
            pf.source = original_source
        if not dry_run and tgt.parent.is_dir():
            try:
                _export_artwork(
                    pf, target, session, managed_files, memberships.get(device_path, set()),
                    artwork_by_parent, art_cache, rockbox_art_cache, exact_art_sides,
                    written_dirs, reuse_inventory=False, validate_artwork=validate_artwork)
            except OperationCancelled:
                cancelled = True
                break
            except _ArtworkChoiceChanged as exc:
                global_errors.append(f"ERROR writing artwork for {pf.source}: {exc}")
                break
            except (OSError, RockboxMediaError) as exc:
                global_errors.append(f"ERROR writing artwork for {pf.source}: {exc}")
        if on_progress:
            on_progress(done, total, pf.source.name)

    report.bytes_to_copy = bytes_copied
    cancelled = cancelled or bool(should_cancel and should_cancel())

    # --- Playlist phase ---------------------------------------------------
    # Runs even after a cancel: every playlist still gets an HONEST m3u8 of what
    # made it onto the device, so finished/partial playlists are usable NOW and
    # the incomplete ones are flagged (poisoned sig) for the next Sync to finish.
    incomplete: set[str] = set()
    used_playlist_paths: dict[str, str] = {}
    for idx, plist in enumerate(selected):
        entries = entries_by_pl[idx]
        pr = PlaylistReport(name=plist.display_path)
        pr.in_playlist = len(entries)
        pr.found = sum(1 for e in entries if e.device_path is not None)
        pr.missing = sum(1 for e in entries if e.device_path is None)
        pr.copied = sum(1 for e in entries if e.device_path in copied_paths)
        pr.already_present = sum(1 for e in entries if e.device_path in already)
        pr.conflicts = sum(1 for e in entries if e.device_path in conflicts)

        if cancelled:
            # Cancel semantics: the playlist file must describe what's actually ON
            # the device — a playable partial, not a full list with dead entries.
            # (Entries not yet processed may still exist from an earlier export, so
            # fall back to a stat for those.)
            kept, skipped = [], 0
            for e in entries:
                if e.device_path is None:
                    kept.append(e)  # keep the "# MISSING:" comment (source gone)
                elif (e.device_path in copied_paths or e.device_path in already
                        or (target / e.device_path.lstrip("/")).exists()):
                    kept.append(e)
                else:
                    skipped += 1
            text = pl_mod.render_m3u8(kept)
            if skipped:
                incomplete.add(manifest.playlist_key(plist))
                text += (f"# CANCELLED: {skipped} track(s) not exported — "
                         f"sync the device to finish\n")
        else:
            text = pl_mod.render_m3u8(entries)
        out_paths: list[Path] = []
        if layout in ("tree", "both"):
            out_paths.append(pl_mod.tree_playlist_path(playlists_root, plist.destination_folder_path, plist.name))
        if layout in ("flat", "both"):
            out_paths.append(pl_mod.flat_playlist_path(playlists_root, [] if concise_playlists else plist.destination_folder_path, plist.name))

        resolved_paths: list[Path] = []
        playlist_identity = manifest.playlist_key(plist)
        for op in out_paths:
            key = _destination_key("/" + str(op.relative_to(target)))
            owner = used_playlist_paths.get(key)
            if owner is not None and owner != playlist_identity:
                suffix = hashlib.sha1(
                    playlist_identity.encode("utf-8")).hexdigest()[:6]
                op = op.with_name(f"{op.stem}_{suffix}{op.suffix}")
                key = _destination_key("/" + str(op.relative_to(target)))
            used_playlist_paths[key] = playlist_identity
            resolved_paths.append(op)

        for op in resolved_paths:
            pr.playlist_files.append(str(op))
            if not dry_run:
                device_playlist_path = "/" + str(
                    op.relative_to(target)).replace(os.sep, "/")
                previous_playlist = managed_files.get(
                    device_playlist_path) or {}
                try:
                    from .playlist_retirement import PENDING_RETIREMENT
                    if PENDING_RETIREMENT in previous_playlist:
                        raise OSError(f'pending old playlist retirement at {op}; its file was preserved')
                    if op.exists() and not previous_playlist:
                        raise OSError(
                            f"unowned playlist conflict at {op}")
                    assert session is not None
                    encoded = text.encode("utf-8")
                    with cancellation_disabled():
                        session.atomic_write(op, encoded)
                    managed_files[device_playlist_path] = {
                        "kind": "playlist",
                        "owned": True,
                        "source": playlist_identity,
                        "source_identity": manifest.playlist_sig(
                            plist,
                            getattr(lib, "hidden_ids", frozenset()),
                            getattr(lib, "removed_by_playlist", None)),
                        "size": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "playlists": [playlist_identity],
                    }
                    written_dirs.update(
                        _dirs_up_to(op.parent, playlists_root))
                except OSError as exc:
                    incomplete.add(playlist_identity)
                    pr.errors.append(
                        f"ERROR writing playlist {op}: {exc}")

        report.playlists.append(pr)

    report.written_dirs = written_dirs

    if global_errors and report.playlists:
        report.playlists[0].errors.extend(global_errors)
        incomplete.update(selected_keys)

    # Record what we put on the device (by stable playlist ID) so later syncs can
    # detect drift. Writes only under target (<device>/.soundbox/manifest.json).
    name_retirements = {}
    if concise_playlists:
        from .playlist_retirement import legacy_flat_playlists, remember_retirement
        for key, (relative, old_row) in legacy_flat_playlists(previous_manifest, selected).items():
            device_path = '/' + relative.as_posix()
            retained = managed_files.get(device_path)
            if (retained is not None and retained.get('kind') == 'playlist'
                    and retained.get('owned') is True and retained.get('source') == key
                    and retained.get('size') == old_row.get('size')
                    and retained.get('sha256') == old_row.get('sha256')):
                managed_files[device_path] = remember_retirement(relative, retained)
                name_retirements[key] = relative, old_row
    report.cancelled = cancelled
    if not dry_run:
        try:
            assert session is not None
            session.revalidate()
            with cancellation_disabled():
                manifest.record_export(target, selected, incomplete,
                                       hidden=getattr(lib, "hidden_ids", frozenset()),
                                       removals=getattr(lib, "removed_by_playlist", None),
                                       managed_files=managed_files,
                                       completed=not cancelled and not global_errors,
                                       session=session)
            session.revalidate()
        except OSError as exc:
            if report.playlists:
                report.playlists[0].errors.append(
                    f"ERROR writing SoundBox manifest: {exc}")
        else:
            if concise_playlists and not cancelled and not incomplete and not global_errors:
                from .playlist_retirement import retire_playlist
                retired = False
                by_key = {manifest.playlist_key(playlist): item for playlist, item in zip(selected, report.playlists)}
                for key, (relative, old_row) in name_retirements.items():
                    if should_cancel is not None and should_cancel():
                        report.cancelled = True
                        incomplete.update(selected_keys)
                        break
                    playlist = next(playlist for playlist in selected if manifest.playlist_key(playlist) == key)
                    desired = '/' + pl_mod.flat_playlist_path(Path('Playlists'), [], playlist.name).as_posix()
                    published = managed_files.get(desired) or {}
                    if published.get('kind') != 'playlist' or published.get('source') != key:
                        continue
                    try:
                        with cancellation_disabled():
                            retire_playlist(session, relative, old_row)
                        managed_files.pop('/' + relative.as_posix(), None)
                        retired = True
                    except (OSError, RuntimeError) as exc:
                        incomplete.add(key)
                        by_key[key].errors.append(f'Old playlist name retained: {exc}')
                if retired or incomplete:
                    try:
                        manifest.record_export(target, selected, incomplete,
                            hidden=getattr(lib, 'hidden_ids', frozenset()),
                            removals=getattr(lib, 'removed_by_playlist', None), managed_files=managed_files,
                            completed=not incomplete, session=session)
                    except (OSError, RuntimeError) as exc:
                        report.playlists[0].errors.append(f'ERROR saving playlist-name cleanup: {exc}')
    return report


def _prune_manifest_owned(
        session: DeviceSession, rows: dict[str, dict], *,
        media: bool) -> int:
    """Delete only unreferenced paths explicitly owned by manifest v2."""
    removed = 0
    emptied: set[Path] = set()
    from .playlist_retirement import PENDING_RETIREMENT
    for device_path, row in list(rows.items()):
        # Retained migration evidence has its own exact-byte/claim retirement.
        # Even an invalid proof must never fall through to generic unlink.
        if row.get("playlists") or PENDING_RETIREMENT in row:
            continue
        kind = str(row.get("kind") or "")
        if kind != "playlist" and not media:
            continue
        path = session.guard(
            session.target / str(device_path).lstrip("/"))
        if bool(row.get("owned", False)):
            try:
                with session.parent_fd(path) as (parent, name):
                    try:
                        os.unlink(name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                session.revalidate()
                removed += 1
                emptied.add(path.parent)
            except FileNotFoundError:
                session.revalidate()
            except OSError:
                continue
        # An unowned matching file was referenced but never claimed. Forget
        # the reference without deleting its bytes.
        rows.pop(device_path, None)
    for directory in sorted(
            emptied, key=lambda path: len(path.parts), reverse=True):
        for candidate in (directory, directory.parent):
            if candidate == session.target:
                continue
            try:
                with session.parent_fd(candidate) as (parent, name):
                    os.rmdir(name, dir_fd=parent)
                session.revalidate()
            except OSError:
                pass
    return removed


def _dropped_tracks(prev: dict, by_key: dict, changed: list,
                    hidden=frozenset(), removals=None) -> bool:
    """True if any 'changed' playlist lost a track it previously had on the device
    (the only case besides a deleted playlist that can orphan audio). A pure add,
    reorder, rename, or move never orphans. A SoundBox delete OR per-playlist
    removal counts as lost — its file must be pruned off the device."""
    for key, _name in changed:
        old = set(prev.get(key, {}).get("tracks", []))
        cur = by_key.get(key)
        new = (set(cur.effective_keys) - manifest._excluded(cur, hidden, removals)
               if cur is not None else set())
        if old - new:
            return True
    return False


def resync(
    lib: Library,
    target: Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
    rockbox_media_cache=None,
    rockbox_art_cache=None,
    prune: bool = True,
    extra=None,
    display_rows: Mapping[str, Mapping] | None = None,
    validate_inputs: Callable[[], None] | None = None,
    validate_artwork: Callable[[str], None] | None = None,
    expected_manifest_payload: bytes | None = None,
    expected_manifest_absent: bool = False,
) -> SyncReport:
    """Bring a device back in line with rekordbox using its manifest — DIFF-DRIVEN:
    only the playlists that actually drifted (renamed / moved / tracks changed) are
    re-exported; unchanged playlists are already correct on the device and left alone.
    Stale m3u8s (renamed/moved/removed) are deleted, music is pruned ONLY when a track
    could have been orphaned, and playlists deleted from rekordbox are forgotten.
    Writes ONLY under target.

    `extra` = custom SoundBox playlists (not in lib.playlists), so a synced custom
    playlist is recognized and updated rather than deleted as 'removed'.

    NOTE: this trusts the manifest — it won't re-copy a file you manually deleted off
    the device (all rekordbox-driven edits are handled). Full re-verify isn't the goal."""
    target = Path(target).expanduser().resolve()
    sources = list(source_paths(lib))
    assert_target_safe(target, sources)
    session = DeviceSession(target, sources, create=False)
    if manifest.has_composite_ownership(target, session=session):
        raise RuntimeError(
            "This device uses SoundBox composite dual-library ownership; "
            "the Rockbox-only exporter is disabled for it.")
    m = (_captured_export_manifest(target, session,
        expected_payload=expected_manifest_payload, expected_absent=expected_manifest_absent)
        if expected_manifest_payload is not None or expected_manifest_absent else
        manifest.load(target, session=session))
    prev = m.get("playlists", {})
    by_key = manifest.current_playlists_by_key(lib, extra)

    wanted = manifest.managed_current_playlists(m, lib, extra)
    removed_keys = [k for k in prev if k not in by_key]   # deleted in rekordbox

    drift = manifest.diff(m, lib, extra)
    drifted_keys = {k for k, *_ in (drift.renamed + drift.moved + drift.changed)}
    from .playlist_retirement import legacy_flat_playlists
    name_migrations = legacy_flat_playlists(m, wanted)
    drifted_keys.update(name_migrations)
    # Explicit Sync checks current managed inputs; idle diff remains a bounded
    # playlist-only hint for schema 2. Source observations are metadata only:
    # payload capture/hashing is reserved for playlists that actually changed.
    files, entries = plan(
        lib, wanted, target, artist_overrides=artist_overrides,
        title_overrides=title_overrides, existing_files=m.get("files"), display_rows=display_rows)
    from .rockbox_media import NORMALIZED_MEDIA_PROFILE
    expected_profile = NORMALIZED_MEDIA_PROFILE if rockbox_media_cache is not None else "original"
    prior_files = m.get("files") or {}
    prior_paths_by_key: dict[str, set[str]] = {}
    for path, row in prior_files.items():
        if isinstance(row, dict) and row.get("kind") == "track":
            for key in row.get("playlists") or ():
                prior_paths_by_key.setdefault(key, set()).add(path)
    changed_media_paths: set[str] = set()
    changed_art_paths: set[str] = set()
    art_variants = _artwork_variants(
        rockbox_art_cache, sorted(_artwork_sides(prior_files), reverse=True))
    for path, planned in files.items():
        row = prior_files.get(path) or {}
        expected_identity = f"{planned.size}:{planned.source_mtime_ns}:{expected_profile}"
        if (row.get("source") != str(planned.canonical_source.resolve())
                or row.get("source_identity") != expected_identity
                or row.get("title") != planned.title
                or row.get("artist") != planned.artist):
            changed_media_paths.add(path)
        source = _artwork_source(planned.track_id, art_cache, rockbox_art_cache)
        if source is not None and not _artwork_current(
                prior_files, Path(path).parent, source, art_variants, planned.track_id):
            changed_art_paths.add(path)
    for index, playlist in enumerate(wanted):
        key = manifest.playlist_key(playlist)
        desired_paths = {entry.device_path for entry in entries[index]
                         if entry.device_path is not None}
        prior_paths = prior_paths_by_key.get(key, set())
        if (desired_paths != prior_paths
                or desired_paths & (changed_media_paths | changed_art_paths)):
            drifted_keys.add(key)
    to_export = [p for p in wanted if manifest.playlist_key(p) in drifted_keys]
    session.revalidate()
    if not to_export and validate_inputs is not None:
        validate_inputs()
    if not to_export and not removed_keys:
        return SyncReport(dry_run=False, cancelled=bool(should_cancel and should_cancel()),
                          mount_identity=session.mount_identity)

    # 1) Re-export ONLY the drifted playlists (copy any new files + rewrite their m3u8s
    #    + upsert their manifest entries). Unchanged playlists are skipped entirely —
    #    the big win: no re-verifying every file of every synced playlist each time.
    report = run(lib, to_export, target, layout="both" if name_migrations else "tree", dry_run=False,
                 concise_playlists=True,
                 on_progress=on_progress, should_cancel=should_cancel,
                 artist_overrides=artist_overrides, title_overrides=title_overrides,
                 art_cache=art_cache,
                 rockbox_media_cache=rockbox_media_cache,
                 rockbox_art_cache=rockbox_art_cache,
                 reuse_managed_inventory=True,
                 display_rows=display_rows,
                 validate_inputs=validate_inputs,
                 validate_artwork=validate_artwork,
                 expected_manifest_payload=expected_manifest_payload,
                 expected_manifest_absent=expected_manifest_absent,
                 _session=session) if to_export else SyncReport(
                     dry_run=False, mount_identity=session.mount_identity)

    # 2) Stale playlists are removed below through manifest ownership only.

    # Prune owned orphans only after a successful run, when a playlist was removed
    # or changed membership could leave files unreferenced. The keep-set spans
    # every wanted playlist so shared files stay safe. Cancelled or errored
    # partial runs skip orphan removal, removed-key publication and sidecar cleanup;
    # their retained removal baselines let the next Sync retry that work.
    # With nothing to copy, the copy loop never polled cancellation, so check
    # should_cancel again before cleanup without relabeling errors as Cancel.
    cancelled = report.cancelled or bool(should_cancel and should_cancel())
    stopped = cancelled or any(row.errors for row in report.playlists)
    report.cancelled = cancelled
    media_prune = bool(
        prune and not stopped and (
            removed_keys or _dropped_tracks(
                prev, by_key, drift.changed,
                getattr(lib, "hidden_ids", frozenset()),
                getattr(lib, "removed_by_playlist", None))))

    # 4) Forget playlists that no longer exist in rekordbox.
    if not stopped:
        mm = manifest.load(target, session=session)
        for k in removed_keys:
            mm["playlists"].pop(k, None)
        for row in (mm.get("files") or {}).values():
            row["playlists"] = [
                key for key in (row.get("playlists") or [])
                if key not in removed_keys
            ]
        rows = mm.setdefault("files", {})
        media_prune = media_prune or any(
            not row.get("playlists") and row.get("kind") in {"track", "artwork"}
            for row in rows.values())
        _prune_manifest_owned(session, rows, media=media_prune)
        session.revalidate()
        manifest.save(target, mm, session=session)

    if not stopped:
        clean_applefiles(
            target, report.written_dirs, session=session)  # only folders we rewrote
    return report
