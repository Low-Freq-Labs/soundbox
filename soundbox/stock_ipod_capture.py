"""Bounded, operation-local captures of the small stock-iPod catalogs.

These observations are never a commit authority or a persistent freshness cache.
FAT timestamps can remain identical across writes, so exact repeated bytes and
presence brackets supplement (rather than replace) descriptor/path epochs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile
from typing import Any, cast

from .operation_control import checkpoint


class StockIpodCaptureError(ValueError):
    """The native catalogs could not be observed as one stable small set."""


@dataclass(frozen=True)
class StockIpodNativeCapture:
    target: Path
    database: bytes
    database_sha256: str
    artwork_database: bytes | None
    artwork_database_sha256: str
    play_counts: bytes | None
    includes_artwork: bool

    @property
    def play_counts_sha256(self) -> str:
        """Observed playback state for view invalidation, never write authority."""
        return hashlib.sha256(self.play_counts).hexdigest() if self.play_counts is not None else ""

    def require_target(self, target: Path) -> None:
        if self.target != target:
            raise StockIpodCaptureError("Stock catalog capture belongs to another target")

    def playback_entries(self, *, expected_tracks: int | None = None) -> list[Any] | None:
        """Use the pinned native parser against only the captured Mac copy."""
        if self.play_counts is None:
            return None
        data = self.play_counts
        if len(data) < 16 or data[:4] != b"mhdp":
            return None
        header_size, entry_size, entry_count = struct.unpack_from("<III", data, 4)
        # The pinned parser assumes each entry has its first four bytes. Its
        # length-only test otherwise accepts entry_size=0 with billions of
        # entries, turning a tiny corrupt file into an unbounded loop.
        if (header_size < 16 or header_size > len(data) or entry_size < 4
                or entry_count > (len(data) - header_size) // entry_size
                or (expected_tracks is not None and entry_count != expected_tracks)):
            return None
        from clickwheel.ipod import read_play_counts  # type: ignore[import-untyped]
        with tempfile.TemporaryDirectory(prefix="soundbox-play-counts-") as directory:
            root = Path(directory)
            path = root / "iPod_Control/iTunes/Play Counts"
            path.parent.mkdir(parents=True)
            path.write_bytes(self.play_counts)
            return cast(list[Any] | None, read_play_counts(root))


def _epoch(path: Path, target: Path) -> tuple[int, ...] | None:
    # A catalog's final component can be ordinary while a parent redirects
    # into PIONEER or outside the device. Reject those aliases before opening
    # any payload, including when a formerly absent file is checked again.
    parent = target
    for component in path.relative_to(target).parts[:-1]:
        parent /= component
        try:
            value = parent.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(value.st_mode):
            raise StockIpodCaptureError(f"Stock catalog parent is not a real directory: {parent.name}")
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode) or not path.resolve(strict=True).is_relative_to(target):
        raise StockIpodCaptureError(f"Stock catalog is not a confined regular file: {path.name}")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode)


def _read(path: Path, target: Path, expected: tuple[int, ...] | None,
          limit: int) -> bytes | None:
    checkpoint()
    if _epoch(path, target) != expected:
        raise StockIpodCaptureError("Stock catalogs changed during capture")
    if expected is None:
        return None
    if expected[2] > limit:
        raise StockIpodCaptureError(f"Stock catalog exceeds the bounded read limit: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        opened = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                  before.st_ctime_ns, before.st_mode)
        if opened != expected:
            raise StockIpodCaptureError("Stock catalogs changed before payload read")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
        closed = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                  after.st_ctime_ns, after.st_mode)
    if (opened != expected or closed != expected or len(data) != expected[2]
            or _epoch(path, target) != expected):
        raise StockIpodCaptureError("Stock catalogs changed during capture")
    return data


def capture_stock_ipod_native(root: str | Path, *,
                              include_artwork: bool = True) -> StockIpodNativeCapture:
    """Capture once per worker operation; share only within that operation.

    No media or ithmb pixels are read. Limits are 256 MiB per database and
    64 MiB for pending play counts. Any observed change fails for a fresh retry.
    """
    target = Path(root).expanduser().resolve(strict=True)
    mount = target.stat()
    identity = (mount.st_dev, mount.st_ino)

    def check_mount() -> None:
        current = target.stat()
        if ((current.st_dev, current.st_ino) != identity
                or target.resolve(strict=True) != target):
            raise StockIpodCaptureError("Stock target changed during capture")

    def read_checked(path: Path, epoch: tuple[int, ...] | None,
                     limit: int) -> bytes | None:
        check_mount()
        data = _read(path, target, epoch, limit)
        check_mount()
        return data
    paths = [target / "iPod_Control/iTunes/iTunesDB",
             target / "iPod_Control/iTunes/Play Counts"]
    limits = [256 * 1024 * 1024, 64 * 1024 * 1024]
    if include_artwork:
        paths.append(target / "iPod_Control/Artwork/ArtworkDB")
        limits.append(256 * 1024 * 1024)
    epochs = [_epoch(path, target) for path in paths]
    if epochs[0] is None:
        raise StockIpodCaptureError("Stock iPod iTunesDB is missing")
    values = [read_checked(path, epoch, limit)
              for path, epoch, limit in zip(paths, epochs, limits)]
    # A second complete pass notices same-size rewrites hidden by FAT epochs.
    for path, epoch, limit, expected in zip(paths, epochs, limits, values):
        if read_checked(path, epoch, limit) != expected:
            raise StockIpodCaptureError("Stock catalogs changed during capture")
    current = target.stat()
    if ((current.st_dev, current.st_ino) != identity
            or target.resolve(strict=True) != target
            or [_epoch(path, target) for path in paths] != epochs):
        raise StockIpodCaptureError("Stock catalogs changed during capture")
    database = values[0]
    assert database is not None
    artwork = values[2] if include_artwork else None
    return StockIpodNativeCapture(
        target, database, hashlib.sha256(database).hexdigest(), artwork,
        hashlib.sha256(artwork).hexdigest() if artwork is not None else "",
        values[1], include_artwork)
