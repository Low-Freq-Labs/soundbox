"""One bounded, descriptor-pinned observation of a device ownership manifest.

The returned bytes are operation-local input, never a persistent freshness proof
or permission to publish. Commit still compares its exact expected baseline.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat

from .operation_control import checkpoint, record_metric


MAX_MANIFEST_BYTES = 64 * 1024 * 1024


class ManifestCaptureError(ValueError):
    pass


def _epoch(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode)


def read_device_manifest(root: str | Path) -> bytes | None:
    """Read at most 64 MiB + 1 using pinned root/state/file descriptors.

    Missing state returns None. Links, oversized input, replacement, remounts,
    and observed changes fail closed. No on-device files are created.
    """
    # Deferred to avoid manifest -> capture -> sync -> manifest import cycles.
    from .sync import DeviceSession

    requested = Path(root).expanduser()
    if not requested.exists():
        return None
    session = DeviceSession(requested, [], create=False)
    session.guard(Path(".soundbox/manifest.json"))
    checkpoint()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_descriptor = os.open(session.target, flags)
    try:
        opened_root = os.fstat(root_descriptor)
        if (opened_root.st_dev, opened_root.st_ino) != session.mount_identity:
            raise ManifestCaptureError("Device changed during manifest capture")
        try:
            state_before = os.stat(".soundbox", dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            session.revalidate()
            return None
        if not stat.S_ISDIR(state_before.st_mode):
            raise ManifestCaptureError("SoundBox state path is not a regular directory")
        state_descriptor = os.open(".soundbox", flags, dir_fd=root_descriptor)
        try:
            if _epoch(os.fstat(state_descriptor)) != _epoch(state_before):
                raise ManifestCaptureError("SoundBox state changed during manifest capture")
            try:
                before = os.stat("manifest.json", dir_fd=state_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                session.revalidate()
                return None
            if not stat.S_ISREG(before.st_mode):
                raise ManifestCaptureError("SoundBox manifest is not a regular file")
            if before.st_size > MAX_MANIFEST_BYTES:
                raise ManifestCaptureError("SoundBox manifest exceeds the 64 MiB read limit")
            descriptor = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW,
                                 dir_fd=state_descriptor)
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if _epoch(opened) != _epoch(before):
                    raise ManifestCaptureError("SoundBox manifest changed during capture")
                pieces = []
                size = 0
                record_metric("device_manifest_capture_reads")
                while size <= MAX_MANIFEST_BYTES:
                    checkpoint()
                    piece = handle.read(min(1024 * 1024, MAX_MANIFEST_BYTES + 1 - size))
                    if not piece:
                        break
                    pieces.append(piece)
                    size += len(piece)
                    record_metric("device_manifest_capture_bytes", len(piece))
                closed = os.fstat(handle.fileno())
            if size > MAX_MANIFEST_BYTES:
                raise ManifestCaptureError("SoundBox manifest exceeds the 64 MiB read limit")
            after = os.stat("manifest.json", dir_fd=state_descriptor, follow_symlinks=False)
            state_after = os.stat(".soundbox", dir_fd=root_descriptor, follow_symlinks=False)
            if (size != before.st_size or _epoch(before) != _epoch(closed)
                    or _epoch(before) != _epoch(after)
                    or _epoch(state_before) != _epoch(state_after)
                    or _epoch(state_before) != _epoch(os.fstat(state_descriptor))):
                raise ManifestCaptureError("SoundBox manifest changed during capture")
            session.revalidate()
            return b"".join(pieces)
        finally:
            os.close(state_descriptor)
    finally:
        os.close(root_descriptor)
