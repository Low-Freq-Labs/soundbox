"""Guarded stock-iPod database planning and commit support.

The third-party iTunesDB implementation is deliberately treated as a renderer,
not as SoundBox's sync engine.  It only sees a temporary staging tree.  All
live-device reads, source validation, media placement, recovery copies, pinned
mount checks, and atomic writes remain owned by SoundBox.

Writer admission follows the observed classic iTunesDB/checksum format.
Artwork additionally needs supported descriptors or an observed Video family.
New audio retains the bounded MP3/WAV policy; existing indexed audio keeps its
original format bytes during catalog changes. Cached song/playlist associations
never imply foreign-media ownership or permission to replace/delete audio.
Compressed databases, corrupt playback deltas and changing targets fail closed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import secrets
import stat as stat_mod
import struct
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, cast
from collections.abc import Mapping, Sequence

import mutagen
from mutagen.mp3 import MP3

from clickwheel.ipod import (  # type: ignore[import-untyped]
    get_ipod_tracks,
    merge_playcounts,
    parse_itunesdb,
)
from clickwheel.ipod.itunesdb_writer import (  # type: ignore[import-untyped]
    ChecksumType,
    PlaylistInfo,
    TrackInfo,
    write_itunesdb,
)
from clickwheel.ipod.itunesdb_writer.mhod_spl_writer import (  # type: ignore[import-untyped]
    prefs_from_parsed,
    rules_from_parsed,
)
from clickwheel.ipod.itunesdb_writer.mhyp_writer import PlaylistItemMeta  # type: ignore[import-untyped]
from clickwheel.ipod.sync import _existing_track_to_trackinfo  # type: ignore[import-untyped]

from . import manifest as device_manifest
from . import metadata
from .ipod_policy import pace_classic_usb as _pace_classic_usb
from .library import Library, Playlist, Track
from .paths import is_within
from contextlib import ExitStack
from .source_acquisition import SourceContract, acquire_source
from .transaction_outcome import record_outcome
from .operation_control import (
    checkpoint, cancellation_disabled, CommittedOperationError, OperationCancelled,
    phase_progress, record_metric,
)
from . import stock_delete
from .rockbox_media import (
    InterruptedFileVerifier,
    PlannedRockboxMedia,
    RockboxMediaCache,
)
from .stock_artwork import (
    RenderedStockArtwork,
    StockArtworkCache,
    render_incremental_stock_artwork,
    render_stock_artwork,
)
from .stock_ipod_shadow import (
    StockIpodShadow,
    StockIpodShadowStore,
    StockIpodSyncStatus,
    rebase_deleted_shadow,
)
from .stock_ipod_capture import (
    StockIpodCaptureError, StockIpodNativeCapture, capture_stock_ipod_native,
)
from .stock_ipod_catalog import (
    STOCK_AUDIO_SUFFIXES, StockPlaylistRecognition, recognize_stock_playlists,
    StockCatalogTrackMatch, catalog_metadata, compare_stock_catalog, matching_catalog_witness_rows,
    catalog_witness_target_rows,
)
from .stock_ipod_catalog_state import CatalogWitnessRow, StockCatalogWitness
from .ipod_export_metadata import freeze_display_rows, frozen_pair
from .ipod_export_witness import (build_export_witness, rebase_export_witness,
                                  stored_metadata, verify_export_witness)
from .stock_ipod_playlist_binding import PlaylistBinding
from .stock_ipod_format import StockWriteFormat, inspect_stock_write_format, stock_renderer_capabilities, _artwork_formats
from .stock_ipod_signing import verified_stock_signing_context, sign_stock_database
from .stock_ipod_native_preserve import preserve_native_catalog
from .sync import DeviceSession, VerifiedCopyReceipt, assert_target_safe
from .device_staging import PublicationOwnershipLost


SUPPORTED_SUFFIXES = STOCK_AUDIO_SUFFIXES
SUPPORTED_MODELS = {
    "MA446": ("iPod Video", "5.5th Gen"),
}
_STOCK_SOURCE_PROFILE = "ma446-mp3-pcm16-stereo-44100-v1"


class _StockProgress:
    """Bounded phase telemetry; a phase endpoint never means export success."""

    def __init__(self, stage: str, total: int, label: str = ''):
        self.stage, self.total, self.done = stage, total, 0
        self.reported = time.monotonic()
        phase_progress(stage, 0, total, label)

    def report(self, label: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or self.done == self.total or now - self.reported >= 0.2:
            phase_progress(self.stage, self.done, self.total, label)
            self.reported = now

    def advance(self, amount: int = 1, label: str = '') -> None:
        self.done += amount
        self.report(label)


class StockIpodError(RuntimeError):
    recovery_status = "not needed"
    """A stock-iPod plan or guarded commit could not be completed safely."""


@dataclass(frozen=True)
class StockMediaCopy:
    """One new, content-addressed media file in a stock-iPod plan."""

    source: Path
    relative_path: Path
    location: str
    size: int
    sha256: str
    dbid: int
    media_plan: PlannedRockboxMedia | None = field(
        default=None, repr=False, compare=False)


@dataclass(frozen=True)
class StockMediaAdoption:
    """One exact unindexed file recovered from an interrupted dual sync."""

    source: Path
    relative_path: Path
    location: str
    size: int
    sha256: str
    dbid: int


@dataclass(frozen=True)
class StockMediaRewrite:
    """One existing stock file replaced by a verified safer payload.

    Composite dual-boot uses this only when the live stock file is the exact
    canonical source but Rockbox requires a picture-free derivative. The old
    bytes are copied into the transaction recovery directory before the live
    path is atomically replaced, and rollback restores them exactly.
    """

    source: Path
    relative_path: Path
    size_before: int
    sha256_before: str
    size: int
    sha256: str
    dbid: int
    media_plan: PlannedRockboxMedia | None = field(
        default=None, repr=False, compare=False)


@dataclass(frozen=True)
class StockMediaDelete:
    """One now-unreferenced stock-iPod media file eligible for deletion."""

    relative_path: Path
    size: int
    sha256: str
    dbids: tuple[int, ...]


@dataclass(frozen=True)
class StockMediaOmission:
    """One playlist entry intentionally excluded from stock firmware."""

    track_id: str
    title: str
    source: Path
    reason: str


@dataclass(frozen=True)
class StockArtworkBefore:
    """One live artwork file whose exact bytes a commit may replace."""

    relative_path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class StockMetadataUpdate:
    """One selected, content-reused record enriched from SoundBox metadata."""

    dbid: int
    title_before: str
    title_after: str
    artist_before: str
    artist_after: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class StockIpodSyncPlan:
    """Fully rendered dry-run result; constructing it never writes the target."""

    target: Path
    target_mount_identity: tuple[int, int]
    model_number: str
    database_relative_path: Path
    database_sha256_before: str
    play_counts_sha256_before: str | None
    rendered_database: bytes = field(repr=False)
    rendered_artwork: RenderedStockArtwork | None = field(default=None, repr=False)
    artwork_before: tuple[StockArtworkBefore, ...] = ()
    metadata_updates: tuple[StockMetadataUpdate, ...] = ()
    omissions: tuple[StockMediaOmission, ...] = ()
    copies: tuple[StockMediaCopy, ...] = ()
    adoptions: tuple[StockMediaAdoption, ...] = ()
    rewrites: tuple[StockMediaRewrite, ...] = ()
    media_deletes: tuple[StockMediaDelete, ...] = ()
    artwork_delete_paths: tuple[Path, ...] = ()
    playlist_names: tuple[str, ...] = ()
    source_track_count: int = 0
    reused_track_count: int = 0
    existing_track_count: int = 0
    final_track_count: int = 0
    removed_track_count: int = 0
    merged_play_count_entries: int = 0
    identity_key: str = ""
    serial_number: str = ""
    shadow_fast_path: bool = False
    shadow_reconcile_reason: str = ""
    shadow_store: StockIpodShadowStore | None = field(
        default=None, repr=False, compare=False)
    shadow_sources: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False, compare=False)
    shadow_artwork: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False, compare=False)
    shadow_playlists: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False, compare=False)
    source_device_paths: dict[str, str] = field(
        default_factory=dict, repr=False, compare=False)
    source_device_dbids: dict[str, int] = field(
        default_factory=dict, repr=False, compare=False)
    dual_boot: bool = False
    defer_cleanup: bool = False
    rockbox_media_cache: RockboxMediaCache | None = field(
        default=None, repr=False, compare=False)
    interrupted_file_verifier: InterruptedFileVerifier | None = field(
        default=None, repr=False, compare=False)
    source_guard_paths: tuple[Path, ...] = ()
    catalog_only: bool = False
    catalog_recognition: tuple[StockPlaylistRecognition, ...] = ()
    catalog_witness: StockCatalogWitness | None = None
    stock_format: StockWriteFormat | None = None
    artwork_warning: str = ""
    incremental_catalog: bool = False
    catalog_warning: str = ""
    catalog_playlist_updates: tuple[int, ...] = ()
    catalog_artwork_track_count: int = 0
    captured_export: bool = False
    catalog_notice: str = ""

    @property
    def bytes_to_copy(self) -> int:
        return sum(item.size for item in self.copies + self.rewrites)

    @property
    def artwork_bytes_to_copy(self) -> int:
        return self.rendered_artwork.bytes_to_copy if self.rendered_artwork else 0

    @property
    def bytes_to_delete(self) -> int:
        return sum(item.size for item in self.media_deletes)

    @property
    def database_changed(self) -> bool:
        return hashlib.sha256(
            self.rendered_database).hexdigest() != self.database_sha256_before

    @property
    def requires_commit(self) -> bool:
        return bool(
            self.copies
            or self.adoptions
            or self.rewrites
            or self.media_deletes
            or self.artwork_delete_paths
            or self.play_counts_sha256_before is not None
            or self.artwork_bytes_to_copy
            or self.database_changed
        )

    def summary(self) -> dict[str, Any]:
        return {
            "writable": True,
            "model": self.model_number,
            "playlists": list(self.playlist_names),
            "source_tracks": self.source_track_count,
            "selected_tracks": self.source_track_count + len(self.omissions),
            "omitted_tracks": len(self.omissions),
            "reused_tracks": self.reused_track_count,
            "new_tracks": len(self.copies) + len(self.adoptions),
            "recovered_tracks": len(self.adoptions),
            "rewritten_tracks": len(self.rewrites),
            "removed_tracks": self.removed_track_count,
            "media_files_to_delete": len(self.media_deletes),
            "bytes_to_delete": self.bytes_to_delete,
            "metadata_updates": len(self.metadata_updates),
            "bytes_to_copy": self.bytes_to_copy,
            "artwork_tracks": (
                self.rendered_artwork.linked_tracks if self.rendered_artwork else 0),
            "unique_artwork": (
                self.rendered_artwork.unique_images if self.rendered_artwork else 0),
            "artwork_bytes_to_copy": self.artwork_bytes_to_copy,
            "artwork_pixel_bytes": (
                sum(item.segment.size for item in self.rendered_artwork.range_writes)
                if self.rendered_artwork and self.rendered_artwork.incremental else
                sum(item.size for item in self.rendered_artwork.ithmb_files)
                if self.rendered_artwork else 0
            ),
            "transaction_mode": (
                "reconcile" if not self.shadow_fast_path
                else "delta" if self.requires_commit else "no-op"
            ),
            "shadow_fast_path": self.shadow_fast_path,
            "shadow_reconcile_reason": self.shadow_reconcile_reason,
            "existing_tracks": self.existing_track_count,
            "final_tracks": self.final_track_count,
            "merged_play_count_entries": self.merged_play_count_entries,
            "database_sha256_before": self.database_sha256_before,
            "database_sha256_after": hashlib.sha256(
                self.rendered_database).hexdigest(),
            "catalog_only": self.catalog_only,
            "catalog_matched_tracks": len({match.local_id for row in self.catalog_recognition
                                           for match in row.tracks}),
            "catalog_unmatched_tracks": len({local for row in self.catalog_recognition
                                             for local in row.unmatched_track_ids}),
            "write_format_profile": self.stock_format.database_profile if self.stock_format else "",
            "artwork_warning": self.artwork_warning,
            "incremental_catalog": self.incremental_catalog,
            "catalog_warning": self.catalog_warning,
            "catalog_notice": self.catalog_notice,
            "catalog_playlist_updates": len(self.catalog_playlist_updates),
            "catalog_artwork_updates": self.catalog_artwork_track_count,
        }


@dataclass(frozen=True)
class StockIpodCommitResult:
    recovery_directory: Path
    copied_tracks: int
    recovered_tracks: int
    reused_tracks: int
    rewritten_tracks: int
    playlists: tuple[str, ...]
    database_sha256: str
    artwork_tracks: int = 0
    unique_artwork: int = 0
    metadata_updates: int = 0
    artwork_pixel_bytes: int = 0
    deleted_media_files: int = 0
    deleted_bytes: int = 0
    cleanup_failures: tuple[str, ...] = ()
    shadow_updated: bool = False
    shadow_warning: str = ""
    artwork_warning: str = ""
    catalog_warning: str = ""
    catalog_playlist_updates: int = 0
    catalog_artwork_updates: int = 0
    catalog_witness: StockCatalogWitness | None = field(default=None, repr=False, compare=False)
    catalog_notice: str = ""


@dataclass(frozen=True)
class StockIpodDeletePlan:
    """Read-only playlist/index deletion with independent physical cleanup."""

    target: Path
    target_mount_identity: tuple[int, int]
    model_number: str
    database_relative_path: Path
    database_sha256_before: str
    play_counts_sha256_before: str | None
    rendered_database: bytes = field(repr=False)
    rendered_artwork: RenderedStockArtwork | None = field(default=None, repr=False)
    artwork_before: tuple[StockArtworkBefore, ...] = ()
    artwork_delete_paths: tuple[Path, ...] = ()
    media_deletes: tuple[stock_delete.IndexedFile, ...] = ()
    artwork_delete_entities: tuple[stock_delete.IndexedFile, ...] = ()
    playlist_ids: tuple[int, ...] = ()
    playlist_names: tuple[str, ...] = ()
    deleted_track_count: int = 0
    existing_track_count: int = 0
    final_track_count: int = 0
    remaining_playlist_count: int = 0
    merged_play_count_entries: int = 0
    dual_boot: bool = False
    repair_empty_playlist_index: bool = False
    catalog_witness: StockCatalogWitness | None = field(default=None, repr=False, compare=False)
    catalog_notice: str = ""
    catalog_warning: str = ""
    shadow_before: StockIpodShadow | None = field(default=None, repr=False, compare=False)
    shadow_store: StockIpodShadowStore | None = field(default=None, repr=False, compare=False)
    shadow_ithmb_before: dict[str, dict[str, int]] | None = field(default=None, repr=False, compare=False)
    catalog_witness_before: StockCatalogWitness | None = field(default=None, repr=False, compare=False)
    artwork_database_sha256_before: str = ""
    identity_key: str = ""
    serial_number: str = ""

    @property
    def bytes_to_delete(self) -> int:
        return sum(item.size for item in self.media_deletes)

    @property
    def artwork_bytes_to_copy(self) -> int:
        return self.rendered_artwork.bytes_to_copy if self.rendered_artwork else 0

    def summary(self) -> dict[str, Any]:
        return {
            "writable": True,
            "model": self.model_number,
            "playlist_ids": list(self.playlist_ids),
            "playlists": list(self.playlist_names),
            "deleted_track_records": self.deleted_track_count,
            "deleted_media_files": len(self.media_deletes),
            "bytes_to_delete": self.bytes_to_delete,
            "existing_tracks": self.existing_track_count,
            "final_tracks": self.final_track_count,
            "remaining_playlists": self.remaining_playlist_count,
            "merged_play_count_entries": self.merged_play_count_entries,
            "dual_boot": self.dual_boot,
            "repair_empty_playlist_index": self.repair_empty_playlist_index,
            "database_sha256_before": self.database_sha256_before,
            "database_sha256_after": hashlib.sha256(
                self.rendered_database).hexdigest(),
        }


@dataclass(frozen=True)
class StockIpodDeleteResult:
    recovery_directory: Path
    playlists: tuple[str, ...]
    deleted_track_records: int
    deleted_media_files: int
    deleted_bytes: int
    remaining_tracks: int
    remaining_playlists: int
    database_sha256: str
    cleanup_failures: tuple[str, ...] = ()
    cleanup_cancelled: bool = False
    catalog_witness: StockCatalogWitness | None = field(default=None, repr=False, compare=False)
    catalog_notice: str = ""
    catalog_warning: str = ""
    shadow_updated: bool = False
    shadow_warning: str = ""


def normalize_model_number(value: str) -> str:
    """Normalize Apple labels such as ``MA446LL/A`` to product code MA446."""
    text = (value or "").strip().upper().replace(" ", "")
    if "/" in text:
        text = text.split("/", 1)[0]
    for code in SUPPORTED_MODELS:
        if text.startswith(code):
            return code
    return text


def supports_stock_write(model_number: str) -> bool:
    return normalize_model_number(model_number) in SUPPORTED_MODELS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    checkpoint()
    with path.open("rb") as handle:
        while True:
            checkpoint()
            chunk = handle.read(1024 * 1024)
            checkpoint()
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _db_path(root: Path) -> Path:
    itunes = root / "iPod_Control" / "iTunes"
    cdb = itunes / "iTunesCDB"
    db = itunes / "iTunesDB"
    if cdb.is_file() and cdb.stat().st_size:
        raise StockIpodError(
            "This iPod uses iTunesCDB; this writer requires an uncompressed classic iTunesDB.")
    sqlite = itunes / "Library.itdb"
    if sqlite.is_file() and sqlite.stat().st_size:
        raise StockIpodError("This iPod uses a SQLite library; this writer requires a classic iTunesDB")
    if not db.is_file() or not db.stat().st_size:
        raise StockIpodError(f"No non-empty iTunesDB found at {db}")
    return db


def _captured_write_format(captured: StockIpodNativeCapture, *, usb_family: str = "",
                           firewire_guid: str = '') -> StockWriteFormat:
    write_format = inspect_stock_write_format(captured.database, captured.artwork_database,
        usb_family=usb_family, firewire_guid=firewire_guid)
    if not write_format.writable:
        raise StockIpodError(write_format.reason)
    return write_format


def _native_artwork_paths(captured: StockIpodNativeCapture, write_format: StockWriteFormat,
                          cache: StockArtworkCache | None) -> tuple[Path, ...]:
    format_ids = (cache.format_ids if cache is not None else tuple(_artwork_formats(captured.artwork_database))
                  if write_format.supports_artwork and captured.artwork_database is not None else ())
    return (Path('iPod_Control/Artwork/ArtworkDB'),
            *(Path('iPod_Control/Artwork') / f'F{format_id}_1.ithmb' for format_id in format_ids))


def _stock_ithmb_tokens(session: DeviceSession, artwork: bytes | None) -> dict[str, dict[str, int]]:
    """Observe only declared thumbnail file epochs, never audio or pixel bytes."""
    result = {}
    for format_id in _artwork_formats(artwork) if artwork is not None else ():
        relative = Path('iPod_Control/Artwork') / f'F{format_id}_1.ithmb'
        with session.parent_fd(relative) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat_mod.S_ISREG(info.st_mode):
                raise StockIpodError(f'Artwork file changed before its baseline: {relative}')
            result[name] = dict(size=info.st_size, mtime_ns=info.st_mtime_ns)
    session.revalidate()
    return result


def _location_path(root: Path, location: str, *, check_parent: bool = True) -> Path | None:
    parts = [part for part in str(location or "").strip(":").split(":") if part]
    if not parts or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        return None
    candidate = root.joinpath(*parts)
    if not check_parent:
        # Inventory comparison only. Any operation touching this path must
        # still resolve it through DeviceSession before reading or writing.
        return candidate
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError:
        return None
    return candidate if is_within(parent, root) else None


def _parse_int(value: object) -> int:
    try:
        return int(float(str(value or "0").strip()))
    except (TypeError, ValueError):
        return 0


def _new_dbid(occupied: set[int]) -> int:
    while True:
        value = secrets.randbits(64)
        if value and value not in occupied:
            occupied.add(value)
            return value


def _technical_audio(path: Path) -> tuple[int, int, int]:
    """Return duration-ms, bitrate-kbps, and sample rate without mutation."""
    try:
        parsed = mutagen.File(path)
        info = getattr(parsed, "info", None)
        duration = int(round(float(getattr(info, "length")) * 1000))
        sample_rate = int(getattr(info, "sample_rate"))
        bitrate_raw = int(getattr(info, "bitrate", 0) or 0)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise StockIpodError(f"Could not read audio properties from {path}: {exc}") from exc
    if duration <= 0 or sample_rate <= 0:
        raise StockIpodError(f"Invalid audio properties for {path}")
    return duration, max(0, bitrate_raw // 1000), sample_rate


def _stock_wav_omission(subtype: str, rate: int, channels: int) -> str:
    return (
        f"{subtype or 'unknown'} / {rate} Hz / "
        f"{channels} ch WAV; validated stock boundary is "
        "PCM 16-bit / 44.1 kHz / stereo"
    )


def _stock_wav_header_omission(path: Path) -> str | None:
    """Reject a definite unsupported PCM/float WAV without reading its audio.

    This never admits a source or supplies a content proof. Compatible, unknown,
    malformed and unstable headers use the ordinary full acquisition path.
    """
    if path.suffix.lower() != ".wav":
        return None

    def epoch(observed: os.stat_result) -> tuple[int, ...]:
        return (observed.st_dev, observed.st_ino, observed.st_mode,
                observed.st_nlink, observed.st_size, observed.st_mtime_ns,
                observed.st_ctime_ns)

    try:
        checkpoint()
        if path.resolve(strict=True) != path:
            return None
        before = path.lstat()
        if not stat_mod.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path, flags), "rb", buffering=0) as source:
            if epoch(os.fstat(source.fileno())) != epoch(before):
                return None
            header = source.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                return None
            end = struct.unpack_from("<I", header, 4)[0] + 8
            if not 36 <= end <= before.st_size:
                return None
            reason = None
            for _ in range(128):
                checkpoint()
                if source.tell() + 8 > end:
                    return None
                chunk = source.read(8)
                if len(chunk) != 8:
                    return None
                kind, size = struct.unpack("<4sI", chunk)
                next_chunk = source.tell() + size + size % 2
                if next_chunk > end:
                    return None
                if kind == b"fmt ":
                    if size < 16:
                        return None
                    fields = source.read(min(size, 40))
                    if len(fields) != min(size, 40):
                        return None
                    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fields)
                    subtypes = ({8: "PCM_U8", 16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}
                                if tag == 1 else {32: "FLOAT", 64: "DOUBLE"} if tag == 3 else {})
                    subtype = subtypes.get(bits)
                    if (subtype is None or not 1 <= channels <= 32 or not 1 <= rate <= 768_000
                            or align != channels * (bits // 8) or byte_rate != rate * align):
                        return None
                    if subtype != "PCM_16" or rate != 44_100 or channels != 2:
                        reason = _stock_wav_omission(subtype, rate, channels)
                    break
                source.seek(next_chunk)
            if (epoch(os.fstat(source.fileno())) != epoch(before)
                    or epoch(path.lstat()) != epoch(before)
                    or path.resolve(strict=True) != path):
                return None
            checkpoint()
            return reason
    except (OSError, ValueError, struct.error):
        return None


def _stock_media_omission(path: Path) -> str:
    """Return why a source is unsafe for the validated MA446 boundary, or ''.

    This is deliberately stock-only. Rockbox and generic-device exporters never
    call it and continue receiving original source files unchanged.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            info = MP3(str(path)).info
            layer = int(getattr(info, "layer", 0) or 0)
            rate = int(getattr(info, "sample_rate", 0) or 0)
            channels = int(getattr(info, "channels", 0) or 0)
            bitrate = int(getattr(info, "bitrate", 0) or 0)
        except Exception as exc:
            return f"unreadable MP3 properties ({exc})"
        if layer != 3:
            return f"MPEG Layer {layer or 'unknown'}; validated stock support is Layer III"
        if rate not in {32_000, 44_100, 48_000}:
            return f"{rate or 'unknown'} Hz MP3; validated stock rates are 32/44.1/48 kHz"
        if channels not in {1, 2}:
            return f"{channels or 'unknown'}-channel MP3; stock audio must be mono/stereo"
        if not 8_000 <= bitrate <= 320_000:
            return f"{bitrate // 1000} kbps MP3; stock limit is 8–320 kbps"
        return ""
    if suffix == ".wav":
        import soundfile as sf
        try:
            info = sf.info(str(path))
        except (OSError, RuntimeError) as exc:
            return f"unreadable WAV properties ({exc})"
        if (
            info.format == "WAV"
            and info.subtype == "PCM_16"
            and info.samplerate == 44_100
            and info.channels == 2
        ):
            return ""
        return _stock_wav_omission(info.subtype, info.samplerate, info.channels)
    return f"{suffix.lstrip('.').upper() or 'unknown'} is not a stock-iPod MP3/WAV"


def _effective_track_metadata(
    track: Track,
    source: Path,
    *,
    derive_artists: bool,
    artist_overrides: dict | None,
    title_overrides: dict | None,
    library: Library,
    display_rows: Mapping[str, Mapping] | None = None,
) -> tuple[str, str]:
    captured = frozen_pair(display_rows, track.track_id)
    if captured is not None:
        return captured
    override = (title_overrides or {}).get(track.track_id)
    artist = metadata.effective_artist(
        track.track_id,
        override or track.name,
        track.artist,
        artist_overrides,
        derive_artists,
        cast("metadata.ArtistRegistry | None", library.artist_registry),
        stem=source.stem,
    )
    title = metadata.effective_title(
        track.track_id,
        track.name,
        artist,
        title_overrides,
        getattr(library, "title_junk", None),
    )
    return title or source.stem, artist


def _new_track_info(
    track: Track,
    source: Path,
    location: str,
    dbid: int,
    *,
    derive_artists: bool,
    artist_overrides: dict | None,
    title_overrides: dict | None,
    library: Library,
    size_override: int | None = None,
    technical_override: tuple[int, int, int] | None = None,
    display_rows: Mapping[str, Mapping] | None = None,
) -> TrackInfo:
    duration, bitrate, sample_rate = technical_override or _technical_audio(source)
    title, artist = _effective_track_metadata(
        track,
        source,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
        library=library,
        display_rows=display_rows,
    )
    suffix = source.suffix.lower()
    stat = source.stat()
    info = TrackInfo(
        title=title,
        location=location,
        size=stat.st_size if size_override is None else size_override,
        length=duration,
        filetype=suffix.lstrip("."),
        filetype_desc=(
            "MPEG audio file" if suffix == ".mp3" else "WAV audio file"),
        bitrate=bitrate,
        sample_rate=sample_rate,
        artist=artist or None,
        genre=track.genre or None,
        year=_parse_int(track.year),
        bpm=_parse_int(track.bpm),
        date_added=int(track.added_ts or stat.st_mtime),
        last_modified=int(stat.st_mtime),
        dbid=dbid,
    )
    if display_rows is not None and track.track_id in display_rows:
        row = display_rows[track.track_id]
        info.album, info.genre, info.comment = (str(row[field]) or None for field in ('album', 'genre', 'comments'))
        info.year, info.bpm = int(row['year']), int(row.get('bpm', 0))
    return info


_NATIVE_STRING_FIELDS = {
    'comment': 'Comment', 'composer': 'Composer', 'grouping': 'Grouping',
    'lyrics': 'Lyrics', 'eq_setting': 'EQ Setting', 'category': 'Category',
    'description': 'Description Text', 'podcast_enclosure_url': 'Podcast Enclosure URL',
    'podcast_rss_url': 'Podcast RSS URL', 'subtitle': 'Subtitle', 'show_name': 'Show',
    'episode_id': 'Episode', 'network_name': 'TV Network', 'album_artist': 'Album Artist',
    'sort_artist': 'Sort Artist', 'keywords': 'Track Keywords', 'show_locale': 'Show Locale',
    'sort_name': 'Sort Title', 'sort_album': 'Sort Album',
    'sort_album_artist': 'Sort Album Artist', 'sort_composer': 'Sort Composer', 'sort_show': 'Sort Show',
}


def _existing_track_info(raw: dict) -> TrackInfo:
    info = _existing_track_to_trackinfo(raw)
    # These parser/writer round-trip fields were added after the high-level
    # helper and must be copied explicitly so ratings/opaque state survive.
    info.user_id = int(raw.get("userID") or 0)
    info.app_rating = int(raw.get("appRating") or 0)
    info.unk144 = int(raw.get("unk144") or 0)
    info.media_type = int(raw.get("mediaType") or 1)
    info.season_number = int(raw.get("seasonNumber") or 0)
    info.episode_number = int(raw.get("episodeNumber") or 0)
    info.filetype_desc = raw.get("Filetype") or None
    for attribute, key in _NATIVE_STRING_FIELDS.items():
        if key in raw:
            setattr(info, attribute, raw[key] or None)
    info.has_lyrics = bool(raw.get('lyricsFlag'))
    return info


def _enrich_existing_track_info(
    info: TrackInfo,
    raw: dict,
    track: Track,
    source: Path,
    *,
    derive_artists: bool,
    artist_overrides: dict | None,
    title_overrides: dict | None,
    library: Library,
    display_rows: Mapping[str, Mapping] | None = None,
) -> StockMetadataUpdate | None:
    """Update only human-facing metadata on one selected reused record.

    The existing TrackInfo supplies every identity, location, technical, rating,
    bookmark, and playback field. Legacy callers enrich nonempty title/artist;
    a validated effective snapshot supplies the seven supported musical fields.
    """
    title, artist = _effective_track_metadata(
        track,
        source,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
        library=library,
        display_rows=display_rows,
    )
    title_before = str(raw.get("title") or "")
    artist_before = str(raw.get("artist") or "")
    title_after = title.strip() or title_before
    artist_after = artist.strip() or artist_before
    changed = []
    if display_rows is not None and track.track_id in display_rows:
        row = display_rows[track.track_id]
        title_after, artist_after = str(row['title']), str(row['artist'])
        for column, attribute, native in (('album', 'album', 'album'), ('genre', 'genre', 'genre'),
                                        ('comments', 'comment', 'Comment'), ('year', 'year', 'year'),
                                        ('bpm', 'bpm', 'bpm')):
            if column not in row:
                continue
            prior = int(raw.get(native) or 0) if column in {'year', 'bpm'} else str(raw.get(native) or '')
            value = row[column]
            if value != prior:
                setattr(info, attribute, value if column in {'year', 'bpm'} else value or None)
                changed.append(column)
    if title_after != title_before:
        changed.append('title')
    if artist_after != artist_before:
        changed.append('artist')
    if not changed:
        return None
    info.title = title_after
    info.artist = artist_after or None
    return StockMetadataUpdate(
        dbid=int(raw.get("dbid") or 0),
        title_before=title_before,
        title_after=title_after,
        artist_before=artist_before,
        artist_after=artist_after,
        fields=tuple(changed),
    )


def _raw_blob(raw: dict, key: str) -> bytes | None:
    value = raw.get(key)
    if not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise StockIpodError(f"Invalid {key} playlist blob") from exc


def _playlist_info(raw: dict, track_id_to_dbid: dict[int, int]) -> PlaylistInfo:
    ids: list[int] = []
    item_meta: list[PlaylistItemMeta] = []
    for item in raw.get("items") or []:
        dbid = track_id_to_dbid.get(int(item.get("trackID") or 0))
        if not dbid:
            raise StockIpodError(
                f"Playlist {raw.get('Title')!r} references an unknown track")
        ids.append(dbid)
        item_meta.append(PlaylistItemMeta(
            podcast_group_flag=int(item.get("podcastGroupFlag") or 0),
            group_id=int(item.get("groupID") or 0),
            podcast_group_ref=int(item.get("podcastGroupRef") or 0),
        ))
    kwargs: dict[str, Any] = {
        "name": str(raw.get("Title") or ""),
        "track_ids": ids,
        "playlist_id": raw.get("playlistID"),
        "master": bool(raw.get("isMaster")),
        "sortorder": int(raw.get("sortOrder") or 0),
        "podcast_flag": int(raw.get("podcastFlag") or 0),
        "group_flag": int(raw.get("groupFlag") or 0),
        "mhsd5_type": int(raw.get("mhsd5Type") or 0),
        "raw_mhod100": _raw_blob(raw, "rawMhod100"),
        "raw_mhod102": _raw_blob(raw, "rawMhod102"),
        "item_metadata": item_meta,
    }
    if raw.get("isSmartPlaylist"):
        smart_data = raw.get("smartPlaylistData")
        smart_rules = raw.get("smartPlaylistRules")
        if not isinstance(smart_data, dict) or not isinstance(smart_rules, dict):
            raise StockIpodError(
                f"Smart playlist {raw.get('Title')!r} is missing rules")
        kwargs["smart_prefs"] = prefs_from_parsed(smart_data)
        kwargs["smart_rules"] = rules_from_parsed(smart_rules)
    return PlaylistInfo(**kwargs)


def _smart_rule_body_offsets(data: bytes) -> list[int]:
    """Return MHOD-51 body offsets from the real MHSD-5/MHLP hierarchy.

    Opaque MHOD-102 playlist settings can legally contain byte sequences that
    look like complete ``mhod``/``SLst`` chunks. Searching the whole database
    for those magic bytes therefore over-counts rules and is not a structural
    parser.
    """
    if len(data) < 24 or data[:4] != b"mhbd":
        raise StockIpodError("Rendered database lacks a valid MHBD root")
    root_header = struct.unpack_from("<I", data, 4)[0]
    root_total = struct.unpack_from("<I", data, 8)[0]
    dataset_count = struct.unpack_from("<I", data, 0x14)[0]
    if root_header < 24 or root_total != len(data) or root_header > len(data):
        raise StockIpodError("Rendered database has an invalid MHBD size")

    offsets: list[int] = []
    cursor = root_header
    smart_datasets = 0
    for _ in range(dataset_count):
        if cursor + 16 > len(data) or data[cursor:cursor + 4] != b"mhsd":
            raise StockIpodError("Rendered database has an invalid MHSD hierarchy")
        header = struct.unpack_from("<I", data, cursor + 4)[0]
        total = struct.unpack_from("<I", data, cursor + 8)[0]
        dataset_type = struct.unpack_from("<I", data, cursor + 12)[0]
        end = cursor + total
        if header < 16 or total < header or end > len(data):
            raise StockIpodError("Rendered database has an invalid MHSD size")
        if dataset_type == 5:
            smart_datasets += 1
            mhlp = cursor + header
            if mhlp + 12 > end or data[mhlp:mhlp + 4] != b"mhlp":
                raise StockIpodError("Rendered smart dataset lacks MHLP")
            playlist_header = struct.unpack_from("<I", data, mhlp + 4)[0]
            playlist_count = struct.unpack_from("<I", data, mhlp + 8)[0]
            playlist_cursor = mhlp + playlist_header
            if playlist_header < 12 or playlist_cursor > end:
                raise StockIpodError("Rendered smart MHLP has an invalid size")
            for _playlist in range(playlist_count):
                if (
                    playlist_cursor + 20 > end
                    or data[playlist_cursor:playlist_cursor + 4] != b"mhyp"
                ):
                    raise StockIpodError("Rendered smart MHLP has an invalid MHYP")
                mhyp_header = struct.unpack_from("<I", data, playlist_cursor + 4)[0]
                mhyp_total = struct.unpack_from("<I", data, playlist_cursor + 8)[0]
                mhod_count = struct.unpack_from("<I", data, playlist_cursor + 12)[0]
                playlist_end = playlist_cursor + mhyp_total
                child = playlist_cursor + mhyp_header
                if (
                    mhyp_header < 20 or mhyp_total < mhyp_header
                    or playlist_end > end
                ):
                    raise StockIpodError("Rendered smart MHYP has an invalid size")
                for _mhod in range(mhod_count):
                    if child + 16 > playlist_end or data[child:child + 4] != b"mhod":
                        raise StockIpodError("Rendered smart MHYP has an invalid MHOD")
                    mhod_header = struct.unpack_from("<I", data, child + 4)[0]
                    mhod_total = struct.unpack_from("<I", data, child + 8)[0]
                    mhod_type = struct.unpack_from("<I", data, child + 12)[0]
                    child_end = child + mhod_total
                    if (
                        mhod_header < 16 or mhod_total < mhod_header
                        or child_end > playlist_end
                    ):
                        raise StockIpodError("Rendered smart MHOD has an invalid size")
                    if mhod_type == 51:
                        body = child + mhod_header
                        if body + 136 > child_end or data[body:body + 4] != b"SLst":
                            raise StockIpodError("Rendered MHOD-51 lacks a valid SLst body")
                        offsets.append(body)
                    child = child_end
                playlist_cursor = playlist_end
            if playlist_cursor != end:
                raise StockIpodError("Rendered smart dataset has trailing/missing bytes")
        cursor = end
    if cursor != len(data) or smart_datasets != 1:
        raise StockIpodError("Rendered database has an invalid smart-dataset count")
    return offsets


def _patch_smart_rule_headers(data: bytes, smart_raw: list[dict]) -> bytes:
    """Preserve MHOD-51's opaque Apple ``unk004`` field.

    clickwheel 0.19.1 semantically round-trips the rules but writes this one
    header word as zero.  The database is un-hashed on MA446, so restoring the
    source word after rendering is safe and independently parser-validated.
    """
    offsets = _smart_rule_body_offsets(data)
    if len(offsets) != len(smart_raw):
        raise StockIpodError(
            "Rendered smart-playlist count changed unexpectedly "
            f"({len(offsets)} != {len(smart_raw)})")
    out = bytearray(data)
    for offset, raw in zip(offsets, smart_raw, strict=True):
        value = int((raw.get("smartPlaylistRules") or {}).get("unk004") or 0)
        struct.pack_into(">I", out, offset + 4, value)
    return bytes(out)


def _alpha_name(seed: bytes, counter: int) -> str:
    digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
    return "".join(chr(ord("A") + value % 26) for value in digest[:4])


def _allocate_location(
    root: Path,
    source_hash: str,
    suffix: str,
    occupied: set[str],
) -> tuple[Path, str]:
    seed = bytes.fromhex(source_hash)
    bucket = f"F{seed[0] % 20:02d}"
    for counter in range(100_000):
        name = _alpha_name(seed, counter) + suffix
        relative = Path("iPod_Control") / "Music" / bucket / name
        key = str(relative).replace(os.sep, "/").casefold()
        if key not in occupied and not (root / relative).exists():
            occupied.add(key)
            location = ":" + ":".join(relative.parts)
            return relative, location
    raise StockIpodError(f"Could not allocate a unique iPod filename for {source_hash}")


def _track_header_offsets(data: bytes) -> dict[int, tuple[int, int]]:
    """Walk native chunks, never search opaque strings for MHIT magic."""
    if len(data) < 24 or data[:4] != b'mhbd':
        raise StockIpodError('Invalid catalog root while preserving audio formats')
    cursor = struct.unpack_from('<I', data, 4)[0]
    count = struct.unpack_from('<I', data, 0x14)[0]
    result = {}
    for _ in range(count):
        if cursor + 16 > len(data) or data[cursor:cursor + 4] != b'mhsd':
            raise StockIpodError('Invalid catalog dataset while preserving audio formats')
        header, size, kind = struct.unpack_from('<III', data, cursor + 4)
        end = cursor + size
        if header < 16 or size < header or end > len(data):
            raise StockIpodError('Invalid catalog dataset size')
        if kind == 1:
            start = cursor + header
            if start + 12 > end or data[start:start + 4] != b'mhlt':
                raise StockIpodError('Catalog track list is unavailable')
            list_header, tracks = struct.unpack_from('<II', data, start + 4)
            item = start + list_header
            if list_header < 12 or item > end:
                raise StockIpodError('Catalog track list framing is invalid')
            for _track in range(tracks):
                if item + 0x78 > end or data[item:item + 4] != b'mhit':
                    raise StockIpodError('Catalog track header is invalid')
                track_header, track_size = struct.unpack_from('<II', data, item + 4)
                if track_header < 0x78 or track_size < track_header or item + track_size > end:
                    raise StockIpodError('Catalog track header size is unsupported')
                dbid = struct.unpack_from('<Q', data, item + 0x70)[0]
                if not dbid or dbid in result:
                    raise StockIpodError('Catalog audio-format identity is ambiguous')
                result[dbid] = (item, track_header)
                item += track_size
            if item != end:
                raise StockIpodError('Catalog track list has trailing bytes')
        cursor = end
    if cursor != len(data):
        raise StockIpodError('Catalog dataset inventory is incomplete')
    return result


def _preserve_catalog_audio_headers(original: bytes, rendered: bytes, *, catalog_only: bool) -> bytes:
    before = _track_header_offsets(original); after = _track_header_offsets(rendered)
    result = bytearray(rendered)
    # Catalog-only changes can touch musical fields, artwork, native playback
    # delta and renderer indexes; every other original header byte stays exact.
    changed = ((8, 0x14), (0x34, 0x38), (0x50, 0x5C), (0x6C, 0x70),
               (0x7A, 0x7E), (0x80, 0x84), (0x9C, 0xA5), (0xB2, 0xB3),
               (0x120, 0x12C), (0x160, 0x164), (0x1E0, 0x1E4), (0x1F4, 0x1F8))
    for dbid in before.keys() & after.keys():
        source, source_header = before[dbid]; target, target_header = after[dbid]
        if catalog_only:
            if source_header != target_header:
                raise StockIpodError('Catalog-only rendering cannot change a track header format')
            result[target:target + target_header] = original[source:source + source_header]
            for start, end in changed:
                if end <= target_header:
                    result[target + start:target + end] = rendered[target + start:target + end]
        else:
            # The upstream parser returns display labels such as "Apple
            # Lossless / AAC", which its writer otherwise defaults to MP3.
            # Existing audio is immutable, including unknown format bytes.
            for start, end in ((0x18, 0x20), (0xCC, 0xD0)):
                if end <= min(source_header, target_header):
                    result[target + start:target + end] = original[source + start:source + end]
    return bytes(result)


def _catalog_playlist_headers(data: bytes) -> dict[tuple[int, int], tuple[int, int]]:
    """Locate native MHYP headers by dataset/identity, including the master."""
    result = {}; cursor = struct.unpack_from('<I', data, 4)[0]
    for _ in range(struct.unpack_from('<I', data, 0x14)[0]):
        if cursor + 16 > len(data) or data[cursor:cursor + 4] != b'mhsd':
            raise StockIpodError('Catalog playlist dataset is invalid')
        header, size, kind = struct.unpack_from('<III', data, cursor + 4)
        end = cursor + size
        if header < 16 or size < header or end > len(data):
            raise StockIpodError('Catalog playlist dataset framing is invalid')
        if kind in {2, 3, 5}:
            start = cursor + header
            if start + 12 > end or data[start:start + 4] != b'mhlp':
                raise StockIpodError('Catalog playlist list is unavailable')
            list_header, count = struct.unpack_from('<II', data, start + 4)
            item = start + list_header
            if list_header < 12 or item > end:
                raise StockIpodError('Catalog playlist list framing is invalid')
            for _playlist in range(count):
                if item + 0x30 > end or data[item:item + 4] != b'mhyp':
                    raise StockIpodError('Catalog playlist header is invalid')
                length, total = struct.unpack_from('<II', data, item + 4)
                if length < 0x30 or total < length or item + total > end:
                    raise StockIpodError('Catalog playlist header framing is invalid')
                master = bool(data[item + 0x14]) and kind in {2, 3}
                identity = 0 if master else struct.unpack_from('<Q', data, item + 0x1C)[0]
                key = (kind, identity)
                if key in result or not master and not identity:
                    raise StockIpodError('Catalog playlist identity is ambiguous')
                result[key] = (item, length); item += total
            if item != end:
                raise StockIpodError('Catalog playlist list has trailing bytes')
        cursor = end
    if cursor != len(data):
        raise StockIpodError('Catalog playlist inventory is incomplete')
    return result


def _preserve_catalog_playlist_headers(original: bytes, rendered: bytes) -> bytes:
    before = _catalog_playlist_headers(original); after = _catalog_playlist_headers(rendered)
    if before.keys() != after.keys():
        raise StockIpodError('Catalog rendering changed playlist identities')
    result = bytearray(rendered)
    for key, (source, length) in before.items():
        target, target_length = after[key]
        if length != target_length:
            raise StockIpodError('Catalog rendering cannot change a playlist header format')
        # Keep identity, timestamps, flags and opaque settings. Recomputed chunk
        # lengths/counts describe newly rendered members/library indexes.
        result[target:target + length] = original[source:source + length]
        result[target + 8:target + 0x14] = rendered[target + 8:target + 0x14]
        result[target + 0x28:target + 0x2A] = rendered[target + 0x28:target + 0x2A]
    return bytes(result)


def _render_database(
    original: bytes,
    track_infos: list[TrackInfo],
    regular: list[PlaylistInfo],
    smart: list[PlaylistInfo],
    smart_raw: list[dict],
    master_name: str,
    capabilities: Any,
    *, catalog_only: bool = False, write_format: StockWriteFormat | None = None,
    metadata_fields: Mapping[int, Sequence[str]] | None = None,
) -> tuple[bytes, dict]:
    """Render and parse the final patched bytes once for caller validation."""
    with tempfile.TemporaryDirectory(prefix="soundbox-stock-ipod-render.") as td:
        root = Path(td)
        itunes = root / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        source = itunes / "iTunesDB"
        source.write_bytes(original)
        ok = write_itunesdb(
            str(root),
            track_infos,
            backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=regular,
            smart_playlists=smart,
            capabilities=capabilities,
            master_playlist_name=master_name,
        )
        if not ok:
            raise StockIpodError("The staged iTunesDB renderer refused the plan")
        rendered = _patch_smart_rule_headers(source.read_bytes(), smart_raw)
        preserve_native = catalog_only or bool(write_format and write_format.database_version == 0x75)
        rendered = _preserve_catalog_audio_headers(original, rendered, catalog_only=preserve_native)
        if catalog_only:
            rendered = _preserve_catalog_playlist_headers(original, rendered)
        if preserve_native:
            rendered = preserve_native_catalog(original, rendered, metadata_fields=metadata_fields or {})
        if struct.unpack_from('<H', original, 0x30)[0] == 1:
            if write_format is None or not write_format.signature_verified:
                raise StockIpodError('A verified current iPod signing identity is required')
            rendered = sign_stock_database(rendered,
                verified_stock_signing_context(original, write_format.firewire_guid))
        source.write_bytes(rendered)
        # A second parse proves that the final, patched bytes are structurally
        # readable before they are ever eligible for a device commit.
        parsed = parse_itunesdb(str(source))
    return rendered, parsed


def _shadow_matches_live(
    shadow: StockIpodShadow | None,
    *,
    root: Path,
    identity_key: str,
    model: str,
    serial_number: str,
    database_sha256: str,
    native_capture: StockIpodNativeCapture | None = None,
) -> tuple[bool, str]:
    if shadow is None:
        return False, "No committed Mac-side baseline"
    if shadow.identity_key != identity_key or shadow.model and model and shadow.model != model:
        return False, "Device identity or model differs from the baseline"
    if (
        shadow.serial_number and serial_number
        and shadow.serial_number != serial_number
    ):
        return False, "Hardware serial differs from the baseline"
    if shadow.database_sha256 != database_sha256:
        return False, "iTunesDB changed since the last SoundBox commit"
    artwork_dir = root / "iPod_Control" / "Artwork"
    artwork_db = artwork_dir / "ArtworkDB"
    if native_capture is not None:
        native_capture.require_target(root)
        if not native_capture.includes_artwork:
            raise ValueError("Stock sync-status capture must include ArtworkDB")
    if shadow.artwork_database_sha256:
        actual_art_hash = (native_capture.artwork_database_sha256
                           if native_capture is not None
                           else _sha256(artwork_db) if artwork_db.is_file() else "")
        if actual_art_hash != shadow.artwork_database_sha256:
            return False, "ArtworkDB changed since the last SoundBox commit"
    elif (native_capture.artwork_database is not None
          if native_capture is not None else artwork_db.exists()):
        return False, "ArtworkDB appeared since the last SoundBox commit"
    for name, row in shadow.ithmb.items():
        path = artwork_dir / name
        try:
            stat = path.stat()
        except OSError:
            return False, f"{name} is missing"
        if (
            stat.st_size != int(row.get("size") or -1)
            or stat.st_mtime_ns != int(row.get("mtime_ns") or -1)
        ):
            return False, f"{name} changed since the last SoundBox commit"
    return True, ""


def _source_hashes_with_shadow(
    sources: list[Path],
    shadow: StockIpodShadow | None,
) -> dict[Path, str]:
    rows = shadow.sources if shadow else {}
    hashes: dict[Path, str] = {}
    for source in sources:
        checkpoint()
        stat = source.stat()
        row = rows.get(str(source)) or {}
        cached = str(row.get("sha256") or "")
        if (
            len(cached) == 64
            and int(row.get("size") or -1) == stat.st_size
            and int(row.get("mtime_ns") or -1) == stat.st_mtime_ns
        ):
            hashes[source] = cached
        else:
            hashes[source] = _sha256(source)
    return hashes


def inspect_stock_ipod_sync_status(
    library: Library,
    target: str | Path,
    *,
    identity_key: str,
    model_number: str,
    serial_number: str,
    shadow_store: StockIpodShadowStore,
    extra_playlists: tuple[Playlist, ...] = (),
    native_capture: StockIpodNativeCapture | None = None,
    catalog_witness: StockCatalogWitness | None = None,
    display_rows: Mapping[str, Mapping] | None = None,
) -> StockIpodSyncStatus:
    """Compare one mounted iPod with its Mac shadow without writing either."""
    from collections import Counter
    root = Path(target).expanduser().resolve(strict=True)
    if native_capture is not None:
        native_capture.require_target(root)
    observed_sha = native_capture.database_sha256 if native_capture is not None else ''
    observed_art = (native_capture.artwork_database_sha256
                    if native_capture is not None and native_capture.includes_artwork else None)

    def result(state, detail, local_changes=0, committed_at='') -> StockIpodSyncStatus:
        return StockIpodSyncStatus(state, detail, local_changes, committed_at, observed_sha, observed_art)

    shadow = shadow_store.load(identity_key)
    if shadow is None:
        return result(
            "untracked", "No committed Mac-side fast-sync baseline")
    if catalog_witness is not None and native_capture is None:
        native_capture = capture_stock_ipod_native(root, include_artwork=True)
        observed_sha = native_capture.database_sha256
        observed_art = native_capture.artwork_database_sha256
    database = root / "iPod_Control/iTunes/iTunesDB" if native_capture is not None else _db_path(root)
    observed_sha = native_capture.database_sha256 if native_capture is not None else _sha256(database)
    matches, reason = _shadow_matches_live(
        shadow,
        root=root,
        identity_key=identity_key,
        model=normalize_model_number(model_number),
        serial_number=serial_number,
        database_sha256=observed_sha,
        native_capture=native_capture,
    )
    if not matches:
        return result(
            "reconcile_required", reason, committed_at=shadow.committed_at)

    changed: set[str] = set()
    current_source_paths: dict[str, str] = {}
    expected_source_paths: dict[tuple[str, int], set[str]] = {}
    drift = device_manifest.diff(
        {"playlists": shadow.playlists}, library, extra=extra_playlists,
        expected_profile=None)
    for rows in (drift.renamed, drift.moved, drift.changed, drift.removed):
        changed.update(str(row[0]) for row in rows)
    for source_text, row in shadow.sources.items():
        track_id = str(row.get("track_id") or "")
        track = library.tracks_by_id.get(track_id)
        source = track.path if track is not None else Path(source_text)
        expected_source_paths.setdefault((track_id, int(row.get('dbid') or 0)), set()).add(source_text)
        try:
            resolved = source.resolve(strict=True)
            stat = resolved.stat()
            if track is not None:
                current_source_paths[track_id] = str(resolved)
        except OSError:
            changed.add(f"source:{track_id or source_text}")
            continue
        # ``size`` and ``sha256`` describe the disposable device payload. A
        # picture-free payload can legitimately be smaller than its canonical
        # source, so comparing source bytes against payload bytes creates a
        # permanent false Sync-needed state immediately after a clean commit.
        # New shadows record the canonical size separately. Older shadows still
        # have the canonical mtime, which is enough to avoid that false positive
        # until the next successful commit upgrades the row.
        source_size = row.get("source_size")
        if (
            (
                source_size is not None
                and stat.st_size != int(source_size)
            )
            or stat.st_mtime_ns != int(row.get("mtime_ns") or -1)
        ):
            changed.add(f"source:{track_id or source_text}")
    for row in shadow.artwork.values():
        art_path = str(row.get("art_path") or "")
        if not art_path:
            continue
        try:
            stat = Path(art_path).stat()
        except OSError:
            changed.add(f"art:{row.get('track_id') or art_path}")
            continue
        if (
            stat.st_size != int(row.get("art_size") or -1)
            or stat.st_mtime_ns != int(row.get("art_mtime_ns") or -1)
        ):
            changed.add(f"art:{row.get('track_id') or art_path}")
    pending = (native_capture.play_counts is not None if native_capture is not None
               else database.with_name("Play Counts").is_file())
    if pending:
        changed.add("play-counts")
    metadata_pending = False
    if catalog_witness is not None:
        try:
            checkpoint()
            if (catalog_witness.source != (str(library.source), library.source_kind, library.source_library_id)
                    or catalog_witness.device_key != identity_key or native_capture is None):
                raise ValueError('Song comparison belongs to another source or device')
            catalog_witness.encode()
            verify_export_witness(catalog_witness, native_capture.database, native_capture.artwork_database_sha256)
            local_counts = Counter(row.local_id for row in catalog_witness.rows)
            dbid_counts = Counter(row.dbid for row in catalog_witness.rows)
            actual_paths: dict[str, set[str]] = {}
            current_playlists = device_manifest.current_playlists_by_key(library, extra_playlists)
            native_ids = {binding.native_id for binding in catalog_witness.bindings if binding.native_id}
            current_uuids: dict[str, list[object]] = {}
            for node in library.source_playlist_catalog.get('nodes', ()):
                if (isinstance(node, Mapping) and str(node.get('ID', '')) in native_ids
                        and node.get('rb_local_deleted', 0) == 0):
                    current_uuids.setdefault(str(node['ID']), []).append(node.get('UUID'))
            for binding in catalog_witness.bindings:
                current_playlist = current_playlists.get(binding.key)
                if (current_playlist is None or str(current_playlist.pid or '') != binding.native_id
                        or library.source_kind == 'rekordbox-db' and binding.native_id
                        and (not binding.uuid or current_uuids.get(binding.native_id) != [binding.uuid])
                        or (not binding.native_id or library.source_kind != 'rekordbox-db') and binding.uuid):
                    raise ValueError('Playlist comparison identity is unavailable or changed')
                members = {local for local, _dbid in binding.members}
                for identity in binding.source_members:
                    if identity[0] in members:
                        actual_paths.setdefault(identity[0], set()).add(identity[4])
            required = {'title', 'artist', 'album', 'genre', 'comments', 'year'}
            for witness_row in catalog_witness.rows:
                checkpoint()
                track = library.tracks_by_id.get(witness_row.local_id)
                projected = display_rows.get(witness_row.local_id) if display_rows is not None else None
                paths = actual_paths.get(witness_row.local_id)
                expected_paths = expected_source_paths.get((witness_row.local_id, witness_row.dbid), set())
                actual_path_matches = (paths == {str(track.path)} if paths and track is not None else
                    bool(expected_paths and expected_paths == {current_source_paths.get(witness_row.local_id)}))
                if (track is None or local_counts[witness_row.local_id] != 1 or dbid_counts[witness_row.dbid] != 1
                        or (track.native_id, track.source_uuid, track.source_native_path or str(track.path))
                            != (witness_row.native_id, witness_row.uuid, witness_row.source_path)
                        or not actual_path_matches or not isinstance(projected, Mapping) or not required <= projected.keys()
                        or 'bpm' not in projected and not projected.get('bpm_estimated', False)):
                    metadata_pending = True
                    continue
                desired = catalog_metadata(track, library, display_rows={witness_row.local_id: projected})
                stored = dict(witness_row.metadata)
                if any(stored[field] != value for field, value in desired):
                    changed.add(f'metadata:{witness_row.local_id}')
        except (ValueError, TypeError, KeyError, OverflowError):
            metadata_pending = True
    if changed:
        return result(
            "local_changes",
            f"{len(changed)} local or playback change(s) ready to plan",
            len(changed),
            shadow.committed_at,
        )
    if metadata_pending:
        return result('comparison_pending', 'Song details are not ready for comparison',
                      committed_at=shadow.committed_at)
    return result(
        "up_to_date", "Matches the last committed Mac-side baseline",
        0, shadow.committed_at)


def plan_stock_ipod_catalog_update(
    library: Library,
    selected: list[Playlist],
    target: str | Path,
    *,
    snapshot: Any,
    model_number: str,
    filesystem: str,
    art_cache: Any | None = None,
    stock_art_cache: StockArtworkCache | None = None,
    update_metadata: bool = False,
    derive_artists: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    allow_dual_boot: bool = False,
    display_rows: Mapping[str, Mapping] | None = None,
    match_labels: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    catalog_witness: StockCatalogWitness | None = None,
    usb_family: str = "",
    incremental: bool = False,
    firewire_guid: str = '',
) -> StockIpodSyncPlan:
    """Sync cached catalog associations; acquire only verified new additions.

    Without incremental mode every playlist keeps its membership/order. Stock
    incremental mode edits selected references through stable bindings, preserving
    foreign audio and unknown entries. Neither mode grants audio ownership.
    Dual catalog callers compose their metadata plan through the composite lane.
    """
    from .export_snapshot import source_paths
    planning = _StockProgress('iTunes planning', 4, 'Reading iPod library')
    root = Path(target).expanduser().resolve(strict=True)
    guard_paths = source_paths(library)
    session = DeviceSession(root, list(guard_paths), create=False)
    if str(filesystem or "").lower() not in {"msdos", "fat32", "fat"}:
        raise StockIpodError("Stock catalog updates require a confirmed FAT32 volume")
    model = normalize_model_number(model_number)
    dual_boot = (root / ".rockbox").exists()
    if dual_boot != allow_dual_boot:
        raise StockIpodError("Use the matching stock/composite catalog update path")
    if incremental and dual_boot:
        raise StockIpodError("Incremental catalog membership sync currently requires stock-only layout")
    if not selected or Path(snapshot.target) != root or snapshot.checksum_scheme not in {0, 1}:
        raise StockIpodError("A current matching stock catalog snapshot is required")
    _db_path(root)
    try:
        captured = capture_stock_ipod_native(root, include_artwork=True)
    except StockIpodCaptureError as exc:
        raise StockIpodError(str(exc)) from exc
    original = captured.database
    write_format = _captured_write_format(captured, usb_family=usb_family, firewire_guid=firewire_guid)
    artwork_warning = ""
    if stock_art_cache is not None and not write_format.supports_artwork:
        artwork_warning = write_format.artwork_reason
        stock_art_cache = None
    elif stock_art_cache is not None:
        stock_art_cache = stock_art_cache.for_profile(write_format.artwork_profile)
    if len(original) < 0x32 or original[:4] != b"mhbd":
        raise StockIpodError("The stock catalog checksum profile is unsupported")
    if captured.database_sha256 != snapshot.database_sha256:
        raise StockIpodError("iTunesDB changed since the cached view; refresh the iPod first")
    planning.advance(label='Read iPod library')
    comparison = (compare_stock_catalog(library, selected, snapshot,
        derive_artists=derive_artists, artist_overrides=artist_overrides, title_overrides=title_overrides,
        display_rows=display_rows, match_labels=match_labels, catalog_witness=catalog_witness,
        incremental=incremental)
        if update_metadata or incremental else None)
    recognized = comparison.recognition if comparison is not None else recognize_stock_playlists(
        library, selected, snapshot, derive_artists=derive_artists,
        artist_overrides=artist_overrides, title_overrides=title_overrides,
        display_rows=display_rows, match_labels=match_labels, catalog_witness=catalog_witness,
        incremental=incremental)
    if not any(row.recognized for row in recognized):
        raise StockIpodError("No unambiguous existing stock playlist association is available")
    planning.advance(label='Compared selected playlists')
    planning.report('Preparing selected songs and cover art', force=True)
    parsed = parse_itunesdb(io.BytesIO(original))
    tracks = get_ipod_tracks(parsed)
    by_dbid = {int(row.get("dbid") or 0): row for row in tracks}
    id_to_dbid = {int(row.get("trackID") or 0): int(row.get("dbid") or 0) for row in tracks}
    if (len(by_dbid) != len(tracks) or len(id_to_dbid) != len(tracks)
            or any(value <= 0 for value in (*by_dbid, *id_to_dbid))):
        raise StockIpodError("Existing iTunesDB track identity is invalid")
    play_hash: str | None = None
    merged = 0
    if captured.play_counts is not None:
        entries = captured.playback_entries(expected_tracks=len(tracks))
        if entries is None or len(entries) != len(tracks):
            raise StockIpodError("Play Counts does not match the current stock catalog")
        merge_playcounts(tracks, entries)
        merged = sum(1 for entry in entries if entry.has_data)
        play_hash = hashlib.sha256(captured.play_counts).hexdigest()
    infos = [_existing_track_info(row) for row in tracks]
    # The upstream round-trip helper omits several string MHODs. Preserve them
    # for every row when this narrowly scoped catalog operation rewrites the DB.
    extra_strings = _NATIVE_STRING_FIELDS
    info_by_dbid = {int(info.dbid): info for info in infos}
    matched = {(match.local_id, match.dbid): match for row in recognized for match in row.tracks}
    cached_rows = {row.dbid: row for row in snapshot.tracks}
    for recognized_playlist in recognized:
        if not recognized_playlist.recognized:
            continue
        current = [row for row in parsed.get("mhlp") or []
                   if not row.get("isMaster")
                   and int(row.get("playlistID") or 0) == recognized_playlist.device_playlist_id]
        cached_playlist = next((row for row in snapshot.playlists
                                if row.playlist_id == recognized_playlist.device_playlist_id), None)
        if (len(current) != 1 or cached_playlist is None
                or str(current[0].get("Title") or "") != cached_playlist.title
                or [id_to_dbid.get(int(item.get('trackID') or 0)) for item in current[0].get('items') or []]
                != [entry.dbid for entry in cached_playlist.entries]):
            raise StockIpodError("Cached stock playlist identity changed")
        members = {id_to_dbid.get(int(item.get("trackID") or 0))
                   for item in current[0].get("items") or []}
        if not incremental and any(match.dbid not in members for match in recognized_playlist.tracks):
            raise StockIpodError("Cached stock song is outside the recognized playlist")
    updates = []
    changes = comparison.changes if update_metadata and comparison is not None else ()
    changed_fields: dict[int, list[str]] = {}
    for change in changes:
        setattr(info_by_dbid[change.dbid], 'comment' if change.field == 'comments' else change.field,
                change.after or None if change.field in {'artist', 'album', 'genre', 'comments'} else change.after)
        changed_fields.setdefault(change.dbid, []).append(change.field)
    for dbid, fields in changed_fields.items():
        before = by_dbid[dbid]; after = info_by_dbid[dbid]
        updates.append(StockMetadataUpdate(dbid, str(before.get('title') or ''), str(after.title or ''),
            str(before.get('artist') or ''), str(after.artist or ''), tuple(dict.fromkeys(fields))))
    # Only additions with a safe selected-playlist continuation acquire audio.
    # Retained foreign tracks remain catalog associations, never content proofs.
    copies: list[StockMediaCopy] = []
    omissions: list[StockMediaOmission] = []
    membership_updates: dict[int, tuple[str, list[int]]] = {}
    catalog_warning = ''
    links = {local: dbid for local, dbid in matched}
    if incremental and comparison is not None:
        safe_changes = [row for row in comparison.playlist_changes if row.recognized and row.membership_safe]
        unsafe = [row for row in comparison.playlist_changes if row.recognized and not row.membership_safe]
        if unsafe:
            catalog_warning = ('Playlist membership left unchanged where cached song identities are ambiguous; '
                               'unmatched device entries and audio were preserved')
        occupied_dbids = set(by_dbid)
        occupied_paths = set()
        for row in tracks:
            path = _location_path(root, str(row.get('location') or ''), check_parent=False)
            if path is not None:
                occupied_paths.add(str(path.relative_to(root)).replace(os.sep, '/').casefold())
        new_ids = tuple(dict.fromkeys(occurrence.local_id for change in safe_changes
                        for occurrence in change.added if occurrence.dbid is None))
        new_sources: dict[Path, tuple[int, str]] = {}
        for local in new_ids:
            checkpoint()
            if local in links:
                continue
            new_track = library.tracks_by_id[local]
            source = new_track.path.expanduser().resolve(strict=True)
            assert_target_safe(root, [source])
            shared = new_sources.get(source)
            if shared is not None:
                raise StockIpodError('Independent new song identities share one source path; reconcile them before Sync')
            if source.suffix.lower() not in SUPPORTED_SUFFIXES:
                # A rejected container needs no source bytes or cloud hydration.
                omissions.append(StockMediaOmission(
                    local, new_track.name, source, _stock_media_omission(source)))
                continue
            header_omission = _stock_wav_header_omission(source)
            if header_omission:
                omissions.append(StockMediaOmission(local, new_track.name, source, header_omission))
                continue
            with acquire_source(SourceContract(source),
                    Path(tempfile.gettempdir()) / 'soundbox-source-staging') as acquired:
                omission = _stock_media_omission(acquired.path)
                if omission:
                    omissions.append(StockMediaOmission(local, new_track.name, source, omission))
                    continue
                technical = _technical_audio(acquired.path)
                dbid = _new_dbid(occupied_dbids)
                relative, new_location = _allocate_location(root, acquired.sha256, source.suffix.lower(), occupied_paths)
                info = _new_track_info(new_track, source, new_location, dbid, derive_artists=derive_artists,
                    artist_overrides=artist_overrides, title_overrides=title_overrides, library=library,
                    size_override=acquired.size, technical_override=technical)
                info.bpm = 0  # only a saved, nonestimated displayed BPM is admitted below
                for field_name, value in catalog_metadata(new_track, library, derive_artists=derive_artists,
                        artist_overrides=artist_overrides, title_overrides=title_overrides, display_rows=display_rows):
                    setattr(info, 'comment' if field_name == 'comments' else field_name,
                            value or None if field_name in {'artist', 'album', 'genre', 'comments'} else value)
                copies.append(StockMediaCopy(source, relative, new_location, acquired.size, acquired.sha256, dbid))
            infos.append(info); info_by_dbid[dbid] = info
            new_sources[source] = (dbid, new_location)
            links[local] = dbid
            matched[(local, dbid)] = StockCatalogTrackMatch(local, dbid, 0, new_location)
        for playlist_change in safe_changes:
            desired = [links[local] for local in playlist_change.source.members if local in links]
            assert playlist_change.device_playlist_id is not None
            membership_updates[playlist_change.device_playlist_id] = (playlist_change.desired_name, desired)
        added_matches = {local: match for (local, dbid), match in matched.items() if dbid not in by_dbid}
        recognized = tuple(replace(row,
            tracks=(*row.tracks, *(added_matches[local] for local in dict.fromkeys(
                next(change.source.members for change in safe_changes if change.source.key == row.playlist_key))
                if local in added_matches)),
            unmatched_track_ids=tuple(local for local in row.unmatched_track_ids if local not in added_matches))
            if row.device_playlist_id in membership_updates else row for row in recognized)
    prepared = {}
    for (local, _dbid), match in matched.items():
        checkpoint()
        track = library.tracks_by_id.get(local)
        raw = by_dbid.get(match.dbid)
        if incremental and match.dbid not in by_dbid:
            if art_cache is not None and stock_art_cache is not None:
                image = art_cache.image_path(local)
                if image is not None:
                    prepared[match.dbid] = stock_art_cache.prepare(image, verify_cache=True)
            continue
        if (track is None or raw is None or int(raw.get("trackID") or 0) != match.device_track_id
                or str(raw.get("location") or "") != match.location):
            raise StockIpodError("Cached stock song identity changed")
        cached_row = cached_rows[match.dbid]
        if (str(raw.get("title") or "Unknown title"), str(raw.get("artist") or ""),
                int(raw.get("length") or 0), int(raw.get("size") or 0)) != (
                cached_row.title, cached_row.artist, cached_row.length_ms, cached_row.size):
            raise StockIpodError("Cached stock song metadata changed")
        # Lexical confinement only: no device media stat, open, hash or glob.
        location = _location_path(root, match.location, check_parent=False)
        if location is None or not location.is_relative_to(root / "iPod_Control" / "Music"):
            raise StockIpodError("Matched stock media has an unsupported catalog location")
        if art_cache is not None and stock_art_cache is not None:
            image = art_cache.image_path(local)
            if image is not None:
                prepared[match.dbid] = stock_art_cache.prepare(image, verify_cache=True)
    artwork = None
    artwork_dir = root / "iPod_Control" / "Artwork"
    artwork_before = []
    if prepared and stock_art_cache is not None:
        if captured.artwork_database is not None:
            artwork_before.append(StockArtworkBefore(
                Path("iPod_Control/Artwork/ArtworkDB"), len(captured.artwork_database),
                captured.artwork_database_sha256))
            # This renderer can preserve unselected slot identities from the
            # fresh ArtworkDB without reading their pixel/audio payloads.
            artwork = render_incremental_stock_artwork(
                infos, prepared, existing_artwork_dir=artwork_dir,
                expected_artwork_database=captured.artwork_database)
        else:
            # Full new-art rendering reads no previous pixels. Capture any
            # pre-existing format files before rendering, never a newer occupant.
            for name in (f'F{format_id}_1.ithmb' for format_id in stock_art_cache.format_ids):
                path = artwork_dir / name
                if path.is_file():
                    artwork_before.append(StockArtworkBefore(
                        Path("iPod_Control/Artwork") / name, path.stat().st_size, _sha256(path)))
            artwork = render_stock_artwork(
                infos, prepared, cache=stock_art_cache, existing_artwork_dir=artwork_dir)
        try:
            after_art = capture_stock_ipod_native(root, include_artwork=True)
        except StockIpodCaptureError as exc:
            raise StockIpodError(str(exc)) from exc
        if (after_art.database != captured.database or after_art.play_counts != captured.play_counts
                or after_art.artwork_database != captured.artwork_database):
            raise StockIpodError("Stock catalogs changed during artwork rendering; refresh the iPod first")
        for before in artwork_before:
            if before.relative_path.name == "ArtworkDB":
                continue
            path = root / before.relative_path
            if not path.is_file() or path.stat().st_size != before.size or _sha256(path) != before.sha256:
                raise StockIpodError("Stock artwork pixels changed during rendering")
    planning.advance(label='Prepared selected songs and cover art')
    regular_raw = [row for row in parsed.get("mhlp") or [] if not row.get("isMaster")]
    smart_raw = list(parsed.get("mhlp_smart") or [])
    master = next((row for row in parsed.get("mhlp") or [] if row.get("isMaster")), None)
    capabilities = stock_renderer_capabilities(write_format)
    if master is None or capabilities is None or int(capabilities.checksum) != write_format.checksum_scheme:
        raise StockIpodError("The existing stock database/profile is unavailable")
    regular_infos = [_playlist_info(row, id_to_dbid) for row in regular_raw]
    for info in regular_infos:
        continuation = membership_updates.get(int(info.playlist_id or 0))
        if continuation is not None:
            name, ids = continuation
            old_metadata = dict(zip(info.track_ids, info.item_metadata or []))
            info.name = name; info.track_ids = ids
            info.item_metadata = [old_metadata.get(dbid, PlaylistItemMeta()) for dbid in ids]
    rendered, checked = _render_database(
        original, infos, regular_infos,
        [_playlist_info(row, id_to_dbid) for row in smart_raw], smart_raw,
        str(master.get("Title") or "iPod"), capabilities, catalog_only=True, write_format=write_format,
        metadata_fields=changed_fields)
    out_tracks = get_ipod_tracks(checked)
    out_by_dbid = {int(row.get("dbid") or 0): row for row in out_tracks}
    if len(out_tracks) != len(tracks) + len(copies) or set(out_by_dbid) != set(info_by_dbid):
        raise StockIpodError("Catalog update changed stock track identities")
    for before in tracks:
        out = out_by_dbid[int(before["dbid"])]
        info = info_by_dbid[int(before["dbid"])]
        for key in ("location", "filetype", "size", "length", "bitrate", "sampleRate", "playCount",
                    "skipCount", "rating", "lastPlayed", "lastSkipped", "bookmarkTime"):
            if out.get(key) != before.get(key):
                raise StockIpodError(f"Catalog update changed existing {key}")
        if (str(out.get("title") or ""), str(out.get("artist") or ""),
                int(out.get("mhiiLink") or 0), int(out.get("artworkCount") or 0),
                int(out.get("artworkSize") or 0)) != (
                str(info.title or ""), str(info.artist or ""), info.mhii_link,
                info.artwork_count, info.artwork_size):
            raise StockIpodError("Catalog metadata/artwork linkage failed validation")
        if (str(out.get('album') or ''), str(out.get('genre') or ''),
                str(out.get('Comment') or out.get('comment') or ''),
                int(out.get('year') or 0), int(out.get('bpm') or 0)) != (
                str(info.album or ''), str(info.genre or ''), str(info.comment or ''), info.year, info.bpm):
            raise StockIpodError("Catalog supported metadata failed validation")
        for attribute, key in extra_strings.items():
            if attribute != 'comment' and str(out.get(key) or '') != str(before.get(key) or ''):
                raise StockIpodError(f"Catalog update changed preserved {key}")
    out_ids = {int(row.get("trackID") or 0): int(row.get("dbid") or 0) for row in out_tracks}
    def playlist_state(rows, ids):
        return [(int(row.get("playlistID") or 0), str(row.get("Title") or ""),
                 [ids.get(int(item.get("trackID") or 0), 0) for item in row.get("items") or []])
                for row in rows]
    out_master = [row for row in checked.get('mhlp') or [] if row.get('isMaster')]
    if (len(out_master) != 1 or int(out_master[0].get('playlistID') or 0) != int(master.get('playlistID') or 0)
            or str(out_master[0].get('Title') or '') != str(master.get('Title') or '')
            or [out_ids.get(int(item.get('trackID') or 0)) for item in out_master[0].get('items') or []]
            != [int(info.dbid) for info in infos]):
        raise StockIpodError('Catalog update changed the master library identity or lost tracks')
    expected_playlist_state = [(playlist_id, *membership_updates[playlist_id])
        if playlist_id in membership_updates else (playlist_id, name, ids)
        for playlist_id, name, ids in playlist_state(regular_raw, id_to_dbid)]
    if expected_playlist_state != playlist_state(
            [row for row in checked.get("mhlp") or [] if not row.get("isMaster")], out_ids):
        raise StockIpodError("Catalog update changed playlist identity/order/membership")
    out_smart = list(checked.get("mhlp_smart") or [])
    if len(out_smart) != len(smart_raw) or any(
            before.get(key) != after.get(key)
            for before, after in zip(smart_raw, out_smart, strict=True)
            for key in ("Title", "mhsd5Type", "smartPlaylistData", "smartPlaylistRules")):
        raise StockIpodError("Catalog update changed a built-in smart playlist")
    art_changed = bool(artwork and (artwork.publish_artwork_db or artwork.range_writes))
    membership_changed = expected_playlist_state != playlist_state(regular_raw, id_to_dbid)
    playlist_update_ids = tuple(after[0] for before, after in zip(
        playlist_state(regular_raw, id_to_dbid), expected_playlist_state, strict=True) if before != after)
    art_update_count = len(artwork.updated_dbids) if art_changed and artwork is not None else 0
    if not updates and not art_changed and play_hash is None and not copies and not membership_changed:
        rendered = original
    if not art_changed:
        artwork_before = []
    def stored_metadata(raw):
        return (('title', str(raw.get('title') or '')), ('artist', str(raw.get('artist') or '')),
                ('album', str(raw.get('album') or '')), ('genre', str(raw.get('genre') or '')),
                ('comments', str(raw.get('Comment') or raw.get('comment') or '')),
                ('year', int(raw.get('year') or 0)), ('bpm', int(raw.get('bpm') or 0)))
    witness_source = (str(library.source), library.source_kind, library.source_library_id)
    prior_rows = matching_catalog_witness_rows(library, snapshot, catalog_witness)
    prior_target_rows = catalog_witness_target_rows(library, snapshot, catalog_witness)
    witnessed_rows: list[CatalogWitnessRow] = []
    if catalog_witness is not None and catalog_witness.source == witness_source and catalog_witness.device_key == snapshot.identity_key:
        selected_dbids = {dbid for _local, dbid in matched}
        for row in catalog_witness.rows:
            if (row.dbid not in selected_dbids and (row.local_id, row.dbid) in prior_target_rows):
                output = by_dbid[row.dbid] if rendered == original else out_by_dbid[row.dbid]
                witnessed_rows.append(replace(row, device_track_id=int(output.get('trackID') or 0),
                                              metadata=stored_metadata(output)))
    for (local, dbid), match in matched.items():
        track = library.tracks_by_id[local]
        output = by_dbid[dbid] if rendered == original else out_by_dbid[dbid]
        prior = prior_rows.get((local, dbid))
        art_key = (prepared[dbid].cache_key if dbid in prepared else prior.artwork_key
                   if prior is not None and catalog_witness is not None
                   and catalog_witness.artwork_database_sha256 == captured.artwork_database_sha256 else '')
        witnessed_rows.append(CatalogWitnessRow(local, track.native_id, track.source_uuid,
            track.source_native_path or str(track.path), dbid, int(output.get('trackID') or 0),
            match.location, stored_metadata(output), art_key))
    bindings: list[PlaylistBinding] = []
    if catalog_witness is not None and catalog_witness.source == witness_source and catalog_witness.device_key == snapshot.identity_key:
        changed_keys = {row.source.key for row in comparison.playlist_changes
                        if row.recognized and row.membership_safe} if comparison is not None else set()
        bindings.extend(row for row in catalog_witness.bindings if row.key not in changed_keys)
    if incremental and comparison is not None:
        for playlist_change in comparison.playlist_changes:
            if playlist_change.recognized and playlist_change.membership_safe:
                assert playlist_change.device_playlist_id is not None
                bindings.append(PlaylistBinding(playlist_change.source.key, playlist_change.source.native_id,
                    playlist_change.source.uuid, playlist_change.device_playlist_id, playlist_change.desired_name,
                    tuple((local, links[local]) for local in playlist_change.source.members if local in links),
                    source_label=playlist_change.source.label,
                    source_members=playlist_change.source.identities))
            elif (playlist_change.binding is not None
                    and playlist_change.binding.key not in {row.key for row in bindings}):
                bindings.append(playlist_change.binding)
    witness = StockCatalogWitness(witness_source, snapshot.identity_key, hashlib.sha256(rendered).hexdigest(),
        tuple(witnessed_rows), hashlib.sha256(artwork.artwork_db).hexdigest() if art_changed and artwork is not None
        else captured.artwork_database_sha256, tuple(bindings))
    session.revalidate()
    planning.advance(label='Validated iPod export plan')
    return StockIpodSyncPlan(
        target=root, target_mount_identity=session.mount_identity, model_number=model,
        database_relative_path=Path("iPod_Control/iTunes/iTunesDB"),
        database_sha256_before=captured.database_sha256, play_counts_sha256_before=play_hash,
        rendered_database=rendered, rendered_artwork=artwork, artwork_before=tuple(artwork_before),
        copies=tuple(copies), omissions=tuple(omissions),
        metadata_updates=tuple(updates), playlist_names=tuple(row.name for row in recognized if row.recognized),
        source_track_count=sum(row.count for row in recognized if row.recognized),
        reused_track_count=len({dbid for _local, dbid in matched if dbid in by_dbid}),
        existing_track_count=len(tracks), final_track_count=len(infos),
        merged_play_count_entries=merged, identity_key=snapshot.identity_key,
        serial_number=snapshot.serial_number, dual_boot=dual_boot, defer_cleanup=dual_boot,
        source_guard_paths=guard_paths, catalog_only=not incremental, catalog_recognition=recognized, catalog_witness=witness,
        stock_format=write_format, artwork_warning=artwork_warning,
        incremental_catalog=incremental, catalog_warning=catalog_warning,
        catalog_playlist_updates=playlist_update_ids, catalog_artwork_track_count=art_update_count)


def verify_stock_ipod_catalog_noop(plan: StockIpodSyncPlan) -> None:
    """Fresh bounded witness publication check for an actual catalog no-op."""
    session = DeviceSession(plan.target, list(plan.source_guard_paths), create=False)
    if (not (plan.catalog_only or plan.incremental_catalog) or plan.requires_commit
            or session.mount_identity != plan.target_mount_identity or plan.catalog_witness is None):
        raise StockIpodError('A current catalog no-op is required')
    _db_path(plan.target)
    captured = capture_stock_ipod_native(plan.target, include_artwork=True)
    if (captured.database_sha256 != plan.database_sha256_before or captured.play_counts is not None
            or captured.artwork_database_sha256 != plan.catalog_witness.artwork_database_sha256):
        raise StockIpodError('Stock catalogs changed after comparison; refresh the iPod first')
    session.revalidate()


def plan_stock_ipod_sync(
    library: Library,
    selected: list[Playlist],
    target: str | Path,
    *,
    model_number: str,
    filesystem: str,
    derive_artists: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache: Any | None = None,
    stock_art_cache: StockArtworkCache | None = None,
    identity_key: str = "",
    serial_number: str = "",
    shadow_store: StockIpodShadowStore | None = None,
    media_payloads: dict[Path, Path | PlannedRockboxMedia] | None = None,
    rockbox_media_cache: RockboxMediaCache | None = None,
    allow_dual_boot: bool = False,
    playlist_names: tuple[str, ...] | None = None,
    managed_playlist_names_before: tuple[str, ...] = (),
    managed_playlist_keys_before: tuple[str, ...] = (),
    allow_empty_selection: bool = False,
    managed_track_rows_before: tuple[tuple[int, str, str], ...] = (),
    managed_source_rows_before: tuple[
        tuple[str, int, str, int, str], ...] = (),
    defer_cleanup: bool = False,
    allow_interrupted_media_adoption: bool = False,
    interrupted_file_verifier: InterruptedFileVerifier | None = None,
    trust_managed_inventory: bool = False,
    usb_family: str = "",
    firewire_guid: str = '',
    display_rows: Mapping[str, Mapping] | None = None,
    catalog_witness: StockCatalogWitness | None = None,
    extra_playlists: tuple[Playlist, ...] = (),
) -> StockIpodSyncPlan:
    """Render and validate a complete stock-iPod sync without target writes."""
    display_rows = freeze_display_rows(library, display_rows, titles=title_overrides, artists=artist_overrides)
    planning = _StockProgress('iTunes planning',
        sum(len(library.resolve(playlist)) for playlist in selected) + 4,
        'Reading iPod library')
    root = Path(target).expanduser().resolve(strict=True)
    guard_paths = tuple(getattr(library, 'export_source_paths', ()))
    planning_session = DeviceSession(root, list(guard_paths), create=False)
    if str(filesystem or "").lower() not in {"msdos", "fat32", "fat"}:
        raise StockIpodError(
            f"Stock iPod writes require an explicitly confirmed FAT32 volume; "
            f"got {filesystem or 'unknown'}.")
    dual_boot = (root / ".rockbox").exists()
    if dual_boot and not allow_dual_boot:
        raise StockIpodError(
            "Dual-boot stock databases remain read-only; use the Rockbox export path.")
    if allow_dual_boot and not dual_boot:
        raise StockIpodError(
            "Composite dual-boot planning requires an existing .rockbox layout.")
    if defer_cleanup and not (allow_dual_boot and dual_boot):
        raise StockIpodError(
            "Deferred stock cleanup is limited to composite dual-boot sync.")
    if trust_managed_inventory and not (allow_dual_boot and dual_boot):
        raise StockIpodError("Manifest inventory reuse requires composite ownership.")
    if allow_interrupted_media_adoption and not (allow_dual_boot and dual_boot):
        raise StockIpodError(
            "Interrupted stock-media adoption is limited to composite dual sync.")
    if allow_interrupted_media_adoption and interrupted_file_verifier is None:
        raise StockIpodError(
            "Interrupted stock-media adoption requires a resumable verifier.")
    model = normalize_model_number(model_number)
    if not selected and not managed_playlist_names_before and not allow_empty_selection:
        raise StockIpodError("Select at least one playlist for stock-iPod sync")
    names = (
        list(playlist_names)
        if playlist_names is not None
        else [playlist.display_path for playlist in selected]
    )
    if len(names) != len(selected):
        raise StockIpodError(
            "Stock-iPod playlist title count does not match the selection")
    if len({name.casefold() for name in names}) != len(names):
        raise StockIpodError("Selected stock-iPod playlist names must be unique")

    # Resolve the small native baseline before opening any selected audio. A
    # matching shadow can retain an already accepted, unchanged Mac source
    # contract; legacy/missing/changed source proofs still acquire exact bytes.
    database = _db_path(root)
    try:
        captured = capture_stock_ipod_native(root, include_artwork=True)
    except StockIpodCaptureError as exc:
        raise StockIpodError(str(exc)) from exc
    original = captured.database
    planning.advance(label='Read iPod library')
    write_format = _captured_write_format(captured, usb_family=usb_family, firewire_guid=firewire_guid)
    artwork_warning = ""
    if stock_art_cache is not None and not write_format.supports_artwork:
        artwork_warning = write_format.artwork_reason
        stock_art_cache = None
    elif stock_art_cache is not None:
        stock_art_cache = stock_art_cache.for_profile(write_format.artwork_profile)
    if len(original) < 0x32 or original[:4] != b"mhbd":
        raise StockIpodError("iTunesDB has an invalid header")
    hashing_scheme = struct.unpack_from("<H", original, 0x30)[0]
    if hashing_scheme != write_format.checksum_scheme:
        raise StockIpodError(
            f"Stock database requires unsupported checksum scheme {hashing_scheme}")
    database_sha256 = captured.database_sha256
    shadow = shadow_store.load(identity_key) if shadow_store and identity_key else None
    shadow_fast_path, shadow_reason = _shadow_matches_live(
        shadow, root=root, identity_key=identity_key, model=model,
        serial_number=serial_number, database_sha256=database_sha256)
    if not shadow_fast_path:
        shadow = None

    payload_overrides: dict[Path, Path | PlannedRockboxMedia] = {}
    for raw_source, raw_payload in (media_payloads or {}).items():
        source = Path(raw_source).expanduser().resolve(strict=True)
        if isinstance(raw_payload, PlannedRockboxMedia):
            if raw_payload.source != source:
                raise StockIpodError(
                    f"Planned media source mismatch: {source}")
            if rockbox_media_cache is None:
                raise StockIpodError(
                    "Planned Rockbox media requires its bounded staging cache")
            payload_overrides[source] = raw_payload
        else:
            payload_overrides[source] = Path(
                raw_payload).expanduser().resolve(strict=True)
    # The same source can occur in many selected playlists. Parse its format
    # once per plan, including when the bounded payload is the source itself.
    compatibility: dict[Path, str] = {}
    captured_hashes: dict[Path, str] = {}
    captured_technical: dict[Path, tuple[int, int, int]] = {}
    captured_observations: dict[Path, os.stat_result] = {}

    def capture_properties(path: Path, planned=None) -> None:
        with acquire_source(SourceContract(
                path, planned.source_size if planned else None,
                planned.source_sha256 if planned and planned.source_sha256_verified else None),
                (rockbox_media_cache.root if rockbox_media_cache else
                 Path(tempfile.gettempdir()) / "soundbox-source-staging")) as snapshot:
            compatibility[path] = _stock_media_omission(snapshot.path)
            captured_hashes[path] = snapshot.sha256
            captured_observations[path] = snapshot.observation
            if not compatibility[path]:
                captured_technical[path] = _technical_audio(snapshot.path)

    managed_payload_baselines = {
        str(source): (size, digest)
        for source, _dbid, _device_path, size, digest in managed_source_rows_before
    }

    def media_omission(path: Path) -> str:
        if path not in compatibility and path.suffix.lower() not in SUPPORTED_SUFFIXES:
            # The suffix alone proves this is outside the new-stock-media
            # profile. Do not acquire a long/cloud set merely to omit it.
            compatibility[path] = _stock_media_omission(path)
        if path not in compatibility:
            planned = payload_overrides.get(path)
            retained = (shadow.sources.get(str(path))
                        if shadow is not None and not allow_dual_boot and planned is None else None)
            observed = path.stat() if retained else None
            epoch = ([observed.st_dev, observed.st_ino, observed.st_size,
                      observed.st_mtime_ns, observed.st_ctime_ns] if observed else None)
            if (retained and observed is not None
                    and retained.get("stock_media_profile") == _STOCK_SOURCE_PROFILE
                    and retained.get("source_epoch") == epoch
                    and len(str(retained.get("source_sha256") or "")) == 64
                    and isinstance(retained.get("stock_technical"), list)
                    and len(retained["stock_technical"]) == 3
                    and all(isinstance(value, int) and value >= 0
                            for value in retained["stock_technical"])):
                # This is the old accepted contract, not a new proof. Keep its
                # exact epoch; never bless a changed inode/ctime with old bytes.
                compatibility[path] = ""
                captured_hashes[path] = str(retained["source_sha256"])
                captured_observations[path] = observed
                technical = retained["stock_technical"]
                captured_technical[path] = (int(technical[0]), int(technical[1]),
                                            int(technical[2]))
            elif (allow_dual_boot and path.suffix.lower() in {".mp3", ".wav"}
                    and isinstance(planned, PlannedRockboxMedia)
                    and planned.source_sha256_verified
                    and managed_payload_baselines.get(str(path))
                    == (planned.payload_size, planned.payload_sha256)):
                # This exact derivative already belongs to the validated stock
                # subset. Source/payload proofs establish unchanged technical
                # content; opening a cloud placeholder again adds no evidence.
                # The DBID/location/live-size ownership audit below still runs.
                # Compatibility belongs to these verified payload bytes, not
                # current cloud residency. Commit acquires changed payloads by
                # exact digest and revalidates retained-source baselines.
                compatibility[path] = ""
            elif isinstance(planned, PlannedRockboxMedia) and planned.stock_omission is not None:
                compatibility[path] = planned.stock_omission
                if planned.stock_technical is not None:
                    captured_technical[path] = planned.stock_technical
            else:
                header_omission = _stock_wav_header_omission(path)
                if header_omission:
                    compatibility[path] = header_omission
                else:
                    capture_properties(path, planned if isinstance(planned, PlannedRockboxMedia) else None)
        return compatibility[path]

    planned_payloads: dict[Path, PlannedRockboxMedia] = {}
    resolved: list[tuple[Track, Path, Path]] = []
    omissions: list[StockMediaOmission] = []
    missing: list[Path] = []
    source_observations: dict[Path, Path | None] = {}
    alias_values: dict[Path, tuple[str, tuple, str]] = {}
    alias_by_track: dict[str, tuple[tuple, str]] = {}

    def observe_source(path: Path, observations: dict[Path, Path | None]) -> Path | None:
        # Memberships can repeat a source thousands of times. This is only a
        # phase-local path observation; acquisition and commit keep their own
        # fresh descriptor/epoch/content proofs.
        if path not in observations:
            try:
                exists = path.is_file()
            except OSError:
                exists = False
            observations[path] = path.resolve(strict=True) if exists else None
        return observations[path]

    for playlist in selected:
        checkpoint()
        for track in library.resolve(playlist):
            observed_source = observe_source(track.path, source_observations)
            if observed_source is None:
                missing.append(track.path)
            else:
                source = observed_source
                if track.track_id not in alias_by_track:
                    title, artist = _effective_track_metadata(track, source, derive_artists=derive_artists,
                        artist_overrides=artist_overrides, title_overrides=title_overrides,
                        library=library, display_rows=display_rows)
                    values = (tuple(display_rows[track.track_id].items())
                              if display_rows is not None and track.track_id in display_rows else
                              (('title', title), ('artist', artist), ('genre', track.genre or ''),
                               ('year', _parse_int(track.year)), ('bpm', _parse_int(track.bpm))))
                    image = art_cache.image_path(track.track_id) if art_cache is not None else None
                    image_key = str(Path(image).resolve()) if image is not None else ''
                    alias_by_track[track.track_id] = values, image_key
                values, image_key = alias_by_track[track.track_id]
                previous = alias_values.get(source)
                if (previous is not None and previous[0] != track.track_id
                        and (previous[1], previous[2]) != (values, image_key)):
                    raise StockIpodError('Different library songs share one audio file but have different '
                        'metadata or artwork. Export one matching song entry before retrying: ' + str(source))
                alias_values[source] = (track.track_id, values, image_key)
                omission = media_omission(source)
                if omission:
                    omissions.append(StockMediaOmission(
                        track.track_id, track.name, source, omission))
                else:
                    override = payload_overrides.get(source, source)
                    if isinstance(override, PlannedRockboxMedia):
                        planned_payloads[source] = override
                        payload = source
                        payload_suffix = override.source.suffix.lower()
                    else:
                        payload = override
                        payload_suffix = payload.suffix.lower()
                    if payload_suffix != source.suffix.lower():
                        raise StockIpodError(
                            f"Prepared media changed file type: {source} -> {payload}")
                    payload_omission = media_omission(payload)
                    if payload_omission:
                        raise StockIpodError(
                            f"Prepared stock payload is incompatible for {source}: "
                            f"{payload_omission}")
                    resolved.append((track, source, payload))
            planning.advance(label=track.name)
    if missing:
        raise StockIpodError(
            f"{len(missing)} selected source track(s) are missing; first: {missing[0]}")
    sources = list(dict.fromkeys(path for _track, path, _payload in resolved))
    payload_by_source = {
        source: payload for _track, source, payload in resolved}
    assert_target_safe(root, [*sources, *payload_by_source.values()])

    parsed = parse_itunesdb(io.BytesIO(original))
    parsed_tracks = get_ipod_tracks(parsed)
    play_counts_hash: str | None = None
    merged_entries = 0
    if captured.play_counts is not None:
        play_counts_hash = hashlib.sha256(captured.play_counts).hexdigest()
        entries = captured.playback_entries(expected_tracks=len(parsed_tracks))
        if entries is None or len(entries) != len(parsed_tracks):
            raise StockIpodError(
                "Play Counts is unreadable or does not match the iTunesDB; "
                "refusing to risk playback history.")
        merge_playcounts(parsed_tracks, entries)
        merged_entries = sum(1 for item in entries if item.has_data)

    track_id_to_dbid: dict[int, int] = {}
    occupied_dbids: set[int] = set()
    for raw in parsed_tracks:
        track_id = int(raw.get("trackID") or 0)
        dbid = int(raw.get("dbid") or 0)
        if not track_id or not dbid or dbid in occupied_dbids:
            raise StockIpodError("Existing iTunesDB track identity is invalid")
        track_id_to_dbid[track_id] = dbid
        occupied_dbids.add(dbid)

    occupied_paths: set[str] = set()
    indexed_paths: set[str] = set()
    interrupted_candidates: dict[tuple[int, str], list[Path]] = {}
    for raw in parsed_tracks:
        location_path = _location_path(root, str(raw.get("location") or ""),
                                       check_parent=not trust_managed_inventory)
        if location_path is not None:
            indexed_paths.add(str(location_path.relative_to(root)).replace(
                os.sep, "/").casefold())
    music = root / "iPod_Control" / "Music"
    occupied_paths.update(indexed_paths)
    if (not trust_managed_inventory or allow_interrupted_media_adoption) and music.is_dir():
        for path in music.glob("F*/*"):
            if path.is_file():
                relative_key = str(path.relative_to(root)).replace(
                    os.sep, "/").casefold()
                occupied_paths.add(relative_key)
                if (
                    allow_interrupted_media_adoption
                    and relative_key not in indexed_paths
                    and not path.is_symlink()
                ):
                    interrupted_candidates.setdefault(
                        (path.stat().st_size, path.suffix.lower()), []).append(path)

    managed_source_names = {
        str(row[0]) for row in managed_source_rows_before}
    source_hashes = {
        source: planned.source_sha256
        for source, planned in planned_payloads.items()
        if (
            planned.source_sha256_verified
            or str(source) in managed_source_names
        )
    }
    source_hashes.update(captured_hashes)
    source_hashes.update(_source_hashes_with_shadow(
        [source for source in sources if source not in source_hashes],
        shadow,
    ))
    payload_hashes = {
        source: (
            planned_payloads[source].payload_sha256
            if source in planned_payloads
            else (
                source_hashes[source]
                if payload_by_source[source] == source
                else _sha256(payload_by_source[source])
            )
        )
        for source in sources
    }
    payload_sizes = {
        source: (
            planned_payloads[source].payload_size
            if source in planned_payloads
            else payload_by_source[source].stat().st_size
        )
        for source in sources
    }
    existing_by_content: dict[tuple[int, str, str], int] = {}
    managed_sources: dict[Path, tuple[int, Path, int, str]] = {}
    managed_source_dbids: set[int] = set()
    parsed_by_dbid = {
        int(raw.get("dbid") or 0): raw for raw in parsed_tracks}
    for source_text, raw_dbid, device_path, raw_size, digest in (
            managed_source_rows_before):
        try:
            source = Path(source_text).expanduser().resolve(strict=True)
        except OSError as exc:
            raise StockIpodError(
                f"Managed stock source is unavailable: {source_text}") from exc
        dbid = int(raw_dbid)
        managed_relative = Path(
            str(device_path).replace("\\", "/").lstrip("/"))
        expected_size = int(raw_size)
        expected_hash = str(digest)
        raw = parsed_by_dbid.get(dbid)
        managed_live = root / managed_relative
        managed_location = _location_path(
            root, str((raw or {}).get("location") or ""),
            check_parent=not trust_managed_inventory)
        retained = (trust_managed_inventory and payload_sizes.get(source) == expected_size
                    and payload_hashes.get(source) == expected_hash)
        if (
            source in managed_sources
            or dbid <= 0
            or dbid in managed_source_dbids
            or raw is None
            or managed_location is None
            or managed_location != managed_live
            or len(expected_hash) != 64
            or (not retained and (not managed_live.is_file()
                                  or managed_live.stat().st_size != expected_size))
        ):
            raise StockIpodError(
                f"Managed composite stock baseline is invalid for {source}")
        managed_sources[source] = (
            dbid, managed_relative, expected_size, expected_hash)
        managed_source_dbids.add(dbid)
        if retained:
            record_metric("retained_device_media")
    # Content reconciliation is needed only for sources that are not already
    # proven by the composite ownership manifest.  Including every managed
    # source here made an additive playlist scan the existing iPod library for
    # each canonical pre-sanitization hash, even though those sources already
    # had exact DBID/path/payload baselines above.  Apart from making queued
    # additions painfully slow, those redundant sustained device reads could
    # provoke a classic iPod USB transport reset (macOS Errno 6) during a
    # no-write plan.  New/unproven sources still take the same narrow
    # content-reconciliation path required by the safety contract.
    unmanaged_sources = [
        source for source in sources if source not in managed_sources]
    original_content_keys = {
        (path.stat().st_size, path.suffix.lower(), source_hashes[path])
        for path in unmanaged_sources}
    payload_content_keys = {
        (
            payload_sizes[path],
            payload_by_source[path].suffix.lower(),
            payload_hashes[path],
        )
        for path in unmanaged_sources}
    desired_content_keys = original_content_keys | payload_content_keys
    wanted_shapes = {
        (size, suffix) for size, suffix, _sha256_value in desired_content_keys}
    if shadow is not None:
        live_dbids = {int(raw.get("dbid") or 0) for raw in parsed_tracks}
        for row in shadow.sources.values():
            try:
                dbid = int(row.get("dbid") or 0)
                size = int(row.get("size") or -1)
                suffix = str(row.get("suffix") or "").lower()
                sha256 = str(row.get("sha256") or "")
                relative = Path(str(row.get("device_path") or ""))
                landed = root / relative
                if (
                    dbid in live_dbids and (size, suffix) in wanted_shapes
                    and len(sha256) == 64 and landed.is_file()
                    and landed.stat().st_size == size
                ):
                    existing_by_content[(size, suffix, sha256)] = dbid
            except (OSError, TypeError, ValueError):
                continue
        # A valid shadow only knows local sources selected in earlier SoundBox
        # syncs. A newly selected playlist may point at media that is already on
        # the iPod but absent from that map. Reconcile just those unproven
        # size/suffix candidates against live bytes before ever planning a copy.
        # This preserves the fast path for known songs without duplicating an
        # older playlist's audio after a partial shadow bootstrap.
        unproven = desired_content_keys - set(existing_by_content)
        unproven_shapes = {(size, suffix) for size, suffix, _sha256 in unproven}
        for raw in parsed_tracks:
            if not unproven:
                break
            location_path = _location_path(
                root, str(raw.get("location") or ""))
            if location_path is None or not location_path.is_file():
                continue
            shape = (
                location_path.stat().st_size, location_path.suffix.lower())
            if shape not in unproven_shapes:
                continue
            content_key = (shape[0], shape[1], _sha256(location_path))
            if content_key in unproven:
                existing_by_content[content_key] = int(raw["dbid"])
                unproven.remove(content_key)
                unproven_shapes = {
                    (size, suffix) for size, suffix, _sha256 in unproven}
    else:
        for raw in parsed_tracks:
            if int(raw.get("dbid") or 0) in managed_source_dbids:
                continue
            location_path = _location_path(root, str(raw.get("location") or ""))
            if location_path is None or not location_path.is_file():
                continue
            shape = (location_path.stat().st_size, location_path.suffix.lower())
            if shape not in wanted_shapes:
                continue
            existing_by_content[(shape[0], shape[1], _sha256(location_path))] = int(
                raw["dbid"])

    source_to_dbid: dict[Path, int] = {}
    content_to_dbid: dict[tuple[int, str, str], int] = dict(existing_by_content)
    claimed_dbids: set[int] = set()
    copies: list[StockMediaCopy] = []
    adoptions: list[StockMediaAdoption] = []
    rewrites: list[StockMediaRewrite] = []
    new_infos: list[TrackInfo] = []
    first_track_for_source: dict[Path, Track] = {}
    for track, source, _payload in resolved:
        first_track_for_source.setdefault(source, track)
    for source in sources:
        checkpoint()
        payload = payload_by_source[source]
        media_plan = planned_payloads.get(source)
        original_key = (
            source.stat().st_size, source.suffix.lower(), source_hashes[source])
        payload_key = (
            payload_sizes[source], payload.suffix.lower(), payload_hashes[source])
        managed = managed_sources.get(source)
        if managed is not None:
            managed_dbid, managed_relative, size_before, hash_before = managed
            if managed_dbid in claimed_dbids:
                raise StockIpodError(
                    f"Managed stock DBID was claimed twice: {managed_dbid}")
            if hash_before != payload_key[2] or size_before != payload_key[0]:
                rewrites.append(StockMediaRewrite(
                    source=source if media_plan is not None else payload,
                    relative_path=managed_relative,
                    size_before=size_before,
                    sha256_before=hash_before,
                    size=payload_sizes[source],
                    sha256=payload_hashes[source],
                    dbid=managed_dbid,
                    media_plan=media_plan,
                ))
            source_to_dbid[source] = managed_dbid
            claimed_dbids.add(managed_dbid)
            continue
        planned_dbid = content_to_dbid.get(payload_key)
        if planned_dbid in claimed_dbids:
            planned_dbid = None
        matched_original = False
        if planned_dbid is None:
            planned_dbid = content_to_dbid.get(original_key)
            if planned_dbid in claimed_dbids:
                planned_dbid = None
            matched_original = planned_dbid is not None
        if planned_dbid is None:
            planned_dbid = _new_dbid(occupied_dbids)
            shape = (payload_sizes[source], payload.suffix.lower())
            recovered_path = next((
                candidate
                for candidate in interrupted_candidates.get(shape, [])
                if interrupted_file_verifier is not None
                and interrupted_file_verifier.verify(
                    candidate,
                    relative_path=candidate.relative_to(root),
                    size=payload_sizes[source],
                    sha256=payload_hashes[source],
                )
            ), None)
            if recovered_path is not None:
                interrupted_candidates[shape].remove(recovered_path)
                relative = recovered_path.relative_to(root)
                location = ":" + ":".join(relative.parts)
                adoptions.append(StockMediaAdoption(
                    source=source,
                    relative_path=relative,
                    location=location,
                    size=payload_sizes[source],
                    sha256=payload_hashes[source],
                    dbid=planned_dbid,
                ))
            else:
                relative, location = _allocate_location(
                    root, payload_hashes[source], payload.suffix.lower(), occupied_paths)
                copies.append(StockMediaCopy(
                    source=source if media_plan is not None else payload,
                    relative_path=relative,
                    location=location,
                    size=payload_sizes[source],
                    sha256=payload_hashes[source],
                    dbid=planned_dbid,
                    media_plan=media_plan,
                ))
            if source not in captured_technical:
                capture_properties(source, media_plan)
            new_infos.append(_new_track_info(
                first_track_for_source[source], source, location, planned_dbid,
                derive_artists=derive_artists,
                artist_overrides=artist_overrides,
                title_overrides=title_overrides,
                library=library,
                size_override=payload_sizes[source],
                technical_override=captured_technical[source],
                display_rows=display_rows,
            ))
            content_to_dbid[payload_key] = planned_dbid
        elif matched_original and original_key != payload_key:
            raw = parsed_by_dbid.get(planned_dbid)
            live = _location_path(root, str((raw or {}).get("location") or ""))
            if raw is None or live is None or not live.is_file():
                raise StockIpodError(
                    f"Could not resolve existing media rewrite for DBID {planned_dbid}")
            rewrites.append(StockMediaRewrite(
                source=source if media_plan is not None else payload,
                relative_path=live.relative_to(root),
                size_before=live.stat().st_size,
                sha256_before=_sha256(live),
                size=payload_sizes[source],
                sha256=payload_hashes[source],
                dbid=planned_dbid,
                media_plan=media_plan,
            ))
            content_to_dbid[payload_key] = planned_dbid
        source_to_dbid[source] = planned_dbid
        # A distinct canonical source is a distinct logical library track even
        # when its bytes match another source. Reuse each live/new DB record at
        # most once so differing title/artist/artwork and playlist identity are
        # preserved. Repeated playlist references to the *same source path*
        # still share this one record through ``source_to_dbid``.
        claimed_dbids.add(planned_dbid)

    playlist_sequences: dict[str, list[int]] = {name: [] for name in names}
    playlist_source_observations: dict[Path, Path | None] = {}
    for playlist, name in zip(selected, names, strict=True):
        for track in library.resolve(playlist):
            # Reobserve after acquisition and reconciliation rather than reuse
            # the first phase's existence/canonical-path result.
            playlist_source = observe_source(track.path, playlist_source_observations)
            if playlist_source is None or playlist_source != source_observations.get(track.path):
                raise StockIpodError(
                    f"Selected source identity changed during planning: {track.path}")
            if playlist_source in source_to_dbid:
                playlist_sequences[name].append(source_to_dbid[playlist_source])

    raw_regular = [
        item for item in (parsed.get("mhlp") or [])
        if not item.get("isMaster")
    ]
    # Native duplicate names retain the first row's identity/opaque metadata,
    # matching the historical ordered lookup without scanning for every name.
    regular_by_name: dict[str, dict] = {}
    for raw in raw_regular:
        regular_by_name.setdefault(str(raw.get("Title") or ""), raw)
    regular: list[PlaylistInfo] = []
    managed_before = set(managed_playlist_names_before)
    managed_rows: dict[int, tuple[str, str]] = {}
    for raw_dbid, device_path, digest in managed_track_rows_before:
        dbid = int(raw_dbid)
        if dbid <= 0 or dbid in managed_rows:
            raise StockIpodError("Managed stock track identity is invalid")
        managed_rows[dbid] = (str(device_path), str(digest))
    managed_dbids = set(managed_rows)
    live_dbids = {int(raw.get("dbid") or 0) for raw in parsed_tracks}
    unknown_managed_dbids = managed_dbids - live_dbids
    if unknown_managed_dbids:
        raise StockIpodError(
            "Managed stock track identity no longer exists in iTunesDB "
            f"(DBID {min(unknown_managed_dbids)})")
    for raw in raw_regular:
        name = str(raw.get("Title") or "")
        if name in playlist_sequences:
            # Managed playlists are appended below in the exact selected
            # sequence. Retaining them in their historical iTunesDB positions
            # made the stock order diverge from the shared manifest order.
            continue
        elif name in managed_before:
            # Composite ownership authorizes replacing/removing only the exact
            # playlist names committed by the preceding schema-3 generation.
            continue
        else:
            regular.append(_playlist_info(raw, track_id_to_dbid))
    for name in names:
        prior = regular_by_name.get(name)
        regular.append(PlaylistInfo(
            name=name,
            track_ids=playlist_sequences[name],
            playlist_id=(prior or {}).get("playlistID"),
            sortorder=1,
            raw_mhod100=_raw_blob(prior, "rawMhod100") if prior else None,
            raw_mhod102=_raw_blob(prior, "rawMhod102") if prior else None,
        ))

    retained_playlist_dbids = {
        int(dbid)
        for playlist_info in regular
        for dbid in playlist_info.track_ids
    }
    pruned_dbids = managed_dbids - retained_playlist_dbids
    retained_locations = {
        str(raw.get("location") or "").casefold()
        for raw in parsed_tracks
        if int(raw.get("dbid") or 0) not in pruned_dbids
        and raw.get("location")
    }
    delete_paths: dict[str, tuple[Path, list[int]]] = {}
    music_root = root / "iPod_Control" / "Music"
    for raw in parsed_tracks:
        dbid = int(raw.get("dbid") or 0)
        if dbid not in pruned_dbids:
            continue
        location = str(raw.get("location") or "")
        if not location or location.casefold() in retained_locations:
            continue
        delete_candidate = _location_path(root, location)
        if delete_candidate is None or not is_within(
                delete_candidate, music_root):
            raise StockIpodError(
                f"Managed track {dbid} has media outside iPod_Control/Music")
        expected_path, expected_hash = managed_rows[dbid]
        expected_relative = expected_path.replace("\\", "/").lstrip("/")
        actual_relative = str(
            delete_candidate.relative_to(root)).replace(os.sep, "/")
        if actual_relative.casefold() != expected_relative.casefold():
            raise StockIpodError(
                f"Managed track {dbid} moved since the last SoundBox commit")
        if delete_candidate.exists() and not delete_candidate.is_file():
            raise StockIpodError(
                f"Managed track {dbid} media is not a regular file: "
                f"{delete_candidate}")
        if delete_candidate.is_file():
            if len(expected_hash) != 64:
                raise StockIpodError(
                    f"Managed track {dbid} has an invalid SoundBox baseline")
            if not dual_boot and _sha256(delete_candidate) != expected_hash:
                raise StockIpodError(
                    f"Managed track {dbid} bytes changed since the last "
                    "SoundBox commit")
            key = str(delete_candidate.relative_to(root)).replace(
                os.sep, "/").casefold()
            if key not in delete_paths:
                delete_paths[key] = (delete_candidate, [])
            delete_paths[key][1].append(dbid)
    media_deletes = tuple(
        StockMediaDelete(
            relative_path=path.relative_to(root),
            size=path.stat().st_size,
            sha256=managed_rows[dbids[0]][1],
            dbids=tuple(dbids),
        )
        for _key, (path, dbids) in sorted(delete_paths.items())
    )

    smart_raw = list(parsed.get("mhlp_smart") or [])
    smart = [_playlist_info(raw, track_id_to_dbid) for raw in smart_raw]
    master_raw = next(
        (item for item in (parsed.get("mhlp") or []) if item.get("isMaster")),
        None,
    )
    if master_raw is None:
        raise StockIpodError("iTunesDB is missing its master playlist")

    capabilities = stock_renderer_capabilities(write_format)
    if capabilities is None or int(capabilities.checksum) != write_format.checksum_scheme:
        raise StockIpodError("The observed stock database format is unavailable or unsafe")
    retained_raw_tracks = [
        raw for raw in parsed_tracks
        if int(raw.get("dbid") or 0) not in pruned_dbids
    ]
    existing_infos = [_existing_track_info(raw) for raw in retained_raw_tracks]
    existing_info_by_dbid = {
        int(info.dbid): (info, raw)
        for info, raw in zip(existing_infos, retained_raw_tracks, strict=True)
    }
    rewrite_by_dbid = {item.dbid: item for item in rewrites}
    for dbid, rewrite in rewrite_by_dbid.items():
        existing_pair = existing_info_by_dbid.get(dbid)
        if existing_pair is None:
            raise StockIpodError(
                f"Existing media rewrite lost database record {dbid}")
        info, _raw = existing_pair
        technical_source = rewrite.media_plan.source if rewrite.media_plan else rewrite.source
        if technical_source not in captured_technical:
            capture_properties(technical_source, rewrite.media_plan)
        duration, bitrate, sample_rate = captured_technical[technical_source]
        info.size = rewrite.size
        info.length = duration
        info.bitrate = bitrate
        info.sample_rate = sample_rate
    metadata_updates: list[StockMetadataUpdate] = []
    for source, track in first_track_for_source.items():
        dbid = source_to_dbid[source]
        existing_pair = existing_info_by_dbid.get(dbid)
        if existing_pair is None:
            continue
        info, raw = existing_pair
        update = _enrich_existing_track_info(
            info,
            raw,
            track,
            source,
            derive_artists=derive_artists,
            artist_overrides=artist_overrides,
            title_overrides=title_overrides,
            library=library,
            display_rows=display_rows,
        )
        if update is not None:
            metadata_updates.append(update)
    track_infos = existing_infos + new_infos
    planning.advance(label='Prepared song and playlist records')
    planning.report('Preparing cover art', force=True)
    pruned_had_art = any(
        int(raw.get("mhiiLink") or 0)
        for raw in parsed_tracks
        if int(raw.get("dbid") or 0) in pruned_dbids
    )
    prepared_by_dbid = {}
    if art_cache is not None and stock_art_cache is not None:
        for source, track in first_track_for_source.items():
            artwork_dbid = source_to_dbid.get(source)
            image_path = art_cache.image_path(track.track_id)
            if artwork_dbid and image_path is not None:
                prepared_by_dbid[artwork_dbid] = stock_art_cache.prepare(
                    image_path, verify_cache=shadow is None)
    if stock_art_cache is None:
        rendered_artwork = None
    elif shadow is not None and (root / "iPod_Control/Artwork/ArtworkDB").is_file():
        rendered_artwork = render_incremental_stock_artwork(
            track_infos,
            prepared_by_dbid,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
            known_artwork=shadow.artwork,
        )
        # The fast shadow path may trust unchanged disposable cache metadata,
        # because those bytes are never written. Every segment the renderer
        # actually selected for an incremental device write is still hashed
        # exactly before the plan can be approved.
        if rendered_artwork is not None:
            for write in rendered_artwork.range_writes:
                if _sha256(write.segment.path) != write.segment.sha256:
                    raise StockIpodError(
                        "Prepared stock artwork cache changed during planning")
    else:
        rendered_artwork = render_stock_artwork(
            track_infos,
            prepared_by_dbid,
            cache=stock_art_cache,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
        )
    managed_artwork_paths = _native_artwork_paths(captured, write_format, stock_art_cache)
    artwork_delete_paths = tuple(
        relative for relative in managed_artwork_paths
        if (
            pruned_had_art
            and rendered_artwork is None
            and not any(int(info.mhii_link or 0) for info in track_infos)
            and (root / relative).is_file()
        )
    )
    planning.advance(label='Prepared cover art')
    planning.report('Preparing iPod library', force=True)
    rendered, check = _render_database(
        original, track_infos, regular, smart, smart_raw,
        str(master_raw.get("Title") or "iPod"), capabilities, write_format=write_format,
        metadata_fields={row.dbid: row.fields for row in metadata_updates})
    output_tracks = get_ipod_tracks(check)
    output_by_track_id = {
        int(raw.get("trackID") or 0): int(raw.get("dbid") or 0)
        for raw in output_tracks
    }
    output_by_dbid = {int(raw.get("dbid") or 0): raw for raw in output_tracks}
    if len(output_tracks) != len(track_infos):
        raise StockIpodError("Rendered track count failed validation")
    if pruned_dbids & set(output_by_dbid):
        raise StockIpodError("Rendered database retained a removed managed track")
    for raw in retained_raw_tracks:
        out = output_by_dbid.get(int(raw["dbid"]))
        if out is None:
            raise StockIpodError("Rendered database lost an existing track")
        for key in (
            "location", "playCount", "skipCount", "rating", "lastPlayed",
            "lastSkipped", "bookmarkTime",
        ):
            if out.get(key) != raw.get(key):
                raise StockIpodError(
                    f"Rendered database changed existing {key} state")
    expected_by_dbid = {int(info.dbid): info for info in track_infos}
    for dbid, info in expected_by_dbid.items():
        out = output_by_dbid.get(dbid)
        if out is None:
            raise StockIpodError("Rendered database lost planned track metadata")
        if (
            str(out.get("title") or "") != str(info.title or "")
            or str(out.get("artist") or "") != str(info.artist or "")
        ):
            raise StockIpodError("Rendered database metadata failed validation")
        if display_rows is not None and stored_metadata(out) != (
                ('title', str(info.title or '')), ('artist', str(info.artist or '')),
                ('album', str(info.album or '')), ('genre', str(info.genre or '')),
                ('comments', str(info.comment or '')), ('year', int(info.year or 0)),
                ('bpm', int(info.bpm or 0))):
            raise StockIpodError("Rendered captured musical metadata failed validation")
        if (
            int(out.get("mhiiLink") or 0),
            int(out.get("artworkCount") or 0),
            int(out.get("artworkSize") or 0),
        ) != (info.mhii_link, info.artwork_count, info.artwork_size):
            raise StockIpodError("Rendered database artwork linkage failed validation")
    output_playlists = {
        str(raw.get("Title") or ""): [
            output_by_track_id.get(int(item.get("trackID") or 0), 0)
            for item in (raw.get("items") or [])
        ]
        for raw in (check.get("mhlp") or []) if not raw.get("isMaster")
    }
    for name, expected in playlist_sequences.items():
        if output_playlists.get(name) != expected:
            raise StockIpodError(f"Rendered playlist order failed for {name!r}")
    output_managed_order = [
        str(raw.get("Title") or "")
        for raw in (check.get("mhlp") or [])
        if (
            not raw.get("isMaster")
            and str(raw.get("Title") or "") in playlist_sequences
        )
    ]
    if output_managed_order != names:
        raise StockIpodError(
            "Rendered managed playlist sequence does not match the shared order")
    for name in managed_before - set(playlist_sequences):
        if name in output_playlists:
            raise StockIpodError(
                f"Rendered database retained removed managed playlist {name!r}")
    output_smart = list(check.get("mhlp_smart") or [])
    if len(output_smart) != len(smart_raw):
        raise StockIpodError("Rendered database lost built-in smart playlists")
    for before, after in zip(smart_raw, output_smart, strict=True):
        for key in ("Title", "mhsd5Type", "smartPlaylistData", "smartPlaylistRules"):
            if after.get(key) != before.get(key):
                raise StockIpodError(
                    f"Rendered smart playlist changed {key} for "
                    f"{before.get('Title')!r}")

    current_playlist_sequences = {
        str(raw.get("Title") or ""): [
            track_id_to_dbid.get(int(item.get("trackID") or 0), 0)
            for item in (raw.get("items") or [])
        ]
        for raw in raw_regular
    }
    playlist_changed = any(
        current_playlist_sequences.get(name) != expected
        for name, expected in playlist_sequences.items()
    ) or any(
        name in current_playlist_sequences and name not in playlist_sequences
        for name in managed_before
    )
    managed_names = set(playlist_sequences) | managed_before
    current_managed_order = [
        str(raw.get("Title") or "")
        for raw in raw_regular
        if str(raw.get("Title") or "") in managed_names
    ]
    playlist_changed = bool(
        playlist_changed or current_managed_order != names)
    artwork_changed = bool(
        rendered_artwork and (
            rendered_artwork.publish_artwork_db
            or rendered_artwork.range_writes
        )
    ) or bool(artwork_delete_paths)
    if (
        not copies
        and not adoptions
        and not rewrites
        and not pruned_dbids
        and not metadata_updates
        and not playlist_changed
        and not artwork_changed
        and play_counts_hash is None
    ):
        # Clickwheel regenerates opaque database IDs/headers. The reconciliation
        # above already compared every desired media, metadata, playlist and
        # artwork change, whether accelerated by a shadow or read in full.
        # Cache absence must not manufacture a database change: retain the exact
        # live bytes when no semantic mutation or playback merge is required.
        rendered = original

    copied_dbids = {item.dbid for item in copies}
    new_dbids = copied_dbids | {item.dbid for item in adoptions}
    reused = sum(
        1 for source in sources if source_to_dbid[source] not in new_dbids)
    artwork_before: list[StockArtworkBefore] = []
    if rendered_artwork is not None and (
            rendered_artwork.publish_artwork_db or rendered_artwork.range_writes):
        artwork_paths = [Path("iPod_Control") / "Artwork" / "ArtworkDB"]
        if not rendered_artwork.incremental:
            artwork_paths.extend(
                item.relative_path for item in rendered_artwork.ithmb_files)
        for relative in artwork_paths:
            live = root / relative
            if live.is_file():
                artwork_before.append(StockArtworkBefore(
                    relative, live.stat().st_size, _sha256(live)))
    for relative in artwork_delete_paths:
        live = root / relative
        if live.is_file() and all(
                item.relative_path != relative for item in artwork_before):
            artwork_before.append(StockArtworkBefore(
                relative, live.stat().st_size, _sha256(live)))

    final_dbids = {int(info.dbid) for info in track_infos}
    shadow_sources = {
        key: dict(value) for key, value in (shadow.sources if shadow else {}).items()
        if int(value.get("dbid") or 0) in final_dbids
    } if not managed_playlist_keys_before else {}
    info_by_dbid = {int(info.dbid): info for info in track_infos}
    for source in sources:
        checkpoint()
        canonical_stat = captured_observations.get(source) or source.stat()
        dbid = source_to_dbid[source]
        location = str(info_by_dbid[dbid].location or "")
        shadow_sources[str(source)] = {
            "size": payload_sizes[source],
            "source_size": canonical_stat.st_size,
            "mtime_ns": canonical_stat.st_mtime_ns,
            "sha256": payload_hashes[source],
            "suffix": source.suffix.lower(),
            "dbid": dbid,
            "device_path": location.lstrip(":").replace(":", "/"),
            "track_id": first_track_for_source[source].track_id,
        }
        if source in captured_observations:
            shadow_sources[str(source)].update({
                "source_epoch": [canonical_stat.st_dev, canonical_stat.st_ino,
                                 canonical_stat.st_size, canonical_stat.st_mtime_ns,
                                 canonical_stat.st_ctime_ns],
                "source_sha256": captured_hashes[source],
                "stock_media_profile": _STOCK_SOURCE_PROFILE,
                "stock_technical": list(captured_technical[source]),
            })
    previous_managed_keys = set(managed_playlist_keys_before)
    shadow_playlists = {
        key: dict(value) for key, value in (shadow.playlists if shadow else {}).items()
        if key not in previous_managed_keys
    }
    for playlist in selected:
        checkpoint()
        key = device_manifest.playlist_key(playlist)
        shadow_playlists[key] = device_manifest.entry_for(
            playlist,
            getattr(library, "hidden_ids", frozenset()),
            getattr(library, "removed_by_playlist", None),
        )
    track_id_by_dbid = {
        source_to_dbid[source]: first_track_for_source[source].track_id
        for source in sources
    }
    shadow_artwork: dict[str, dict[str, Any]] = {}
    tracked_dbids = {
        int(row.get("dbid") or 0) for row in shadow_sources.values()}
    for slot in (rendered_artwork.slots if rendered_artwork else ()):
        for dbid in slot.track_dbids or (slot.song_id,):
            info = info_by_dbid.get(dbid)
            if (dbid not in tracked_dbids or info is None
                    or int(info.mhii_link or 0) != slot.image_id):
                continue
            shadow_art_row: dict[str, Any] = dict(
                (shadow.artwork if shadow else {}).get(str(dbid)) or {})
            shadow_art_row.update({
                "image_id": slot.image_id,
                "source_size": slot.source_size,
                "cache_key": slot.cache_key,
                "offsets": {str(key): value for key, value in slot.offsets},
            })
            local_art_track_id = track_id_by_dbid.get(dbid, "")
            image_path = (
                art_cache.image_path(local_art_track_id)
                if art_cache and local_art_track_id else None)
            if image_path is not None:
                try:
                    art_stat = image_path.stat()
                    shadow_art_row.update({
                        "track_id": local_art_track_id,
                        "art_path": str(image_path.resolve(strict=True)),
                        "art_size": art_stat.st_size,
                        "art_mtime_ns": art_stat.st_mtime_ns,
                    })
                except OSError:
                    pass
            shadow_artwork[str(dbid)] = shadow_art_row
    if not shadow_artwork and shadow is not None:
        shadow_artwork = {
            key: dict(value) for key, value in shadow.artwork.items()
            if int(key) in tracked_dbids
        }
    source_device_paths = {
        str(source): "/" + str(info_by_dbid[source_to_dbid[source]].location or "")
        .lstrip(":").replace(":", "/")
        for source in sources
    }
    planning_session.revalidate()
    candidate_witness = None
    catalog_warning = ''
    catalog_notice = ''
    if display_rows is not None:
        comparison = build_export_witness(library, selected, names,
            device_key=identity_key, rendered=rendered,
            artwork_sha256=(hashlib.sha256(rendered_artwork.artwork_db).hexdigest()
                if rendered_artwork is not None and rendered_artwork.publish_artwork_db
                else captured.artwork_database_sha256),
            track_dbids={track.track_id: source_to_dbid[source] for track, source, _payload in resolved},
            artwork_keys={dbid: image.cache_key for dbid, image in prepared_by_dbid.items()},
            previous_witness=catalog_witness,
            previous_database=original,
            previous_artwork_sha256=captured.artwork_database_sha256,
            available_playlists=extra_playlists,
            preserved_artwork_dbids=frozenset(
                dbid for dbid, _image_id, _song_id in rendered_artwork.preserved_links
                if dbid not in prepared_by_dbid) if rendered_artwork is not None else frozenset())
        candidate_witness = comparison.witness
        catalog_warning = comparison.warning
        catalog_notice = comparison.notice
    plan = StockIpodSyncPlan(
        target=root,
        source_guard_paths=guard_paths,
        target_mount_identity=planning_session.mount_identity,
        model_number=model,
        database_relative_path=database.relative_to(root),
        database_sha256_before=hashlib.sha256(original).hexdigest(),
        play_counts_sha256_before=play_counts_hash,
        rendered_database=rendered,
        rendered_artwork=rendered_artwork,
        artwork_before=tuple(artwork_before),
        metadata_updates=tuple(metadata_updates),
        omissions=tuple(omissions),
        copies=tuple(copies),
        adoptions=tuple(adoptions),
        rewrites=tuple(rewrites),
        media_deletes=media_deletes,
        artwork_delete_paths=artwork_delete_paths,
        playlist_names=tuple(names),
        source_track_count=len(resolved),
        reused_track_count=reused,
        existing_track_count=len(parsed_tracks),
        final_track_count=len(track_infos),
        removed_track_count=len(pruned_dbids),
        merged_play_count_entries=merged_entries,
        identity_key=identity_key,
        serial_number=serial_number,
        shadow_fast_path=shadow_fast_path,
        shadow_reconcile_reason=shadow_reason,
        shadow_store=shadow_store,
        shadow_sources=shadow_sources,
        shadow_artwork=shadow_artwork,
        shadow_playlists=shadow_playlists,
        source_device_paths=source_device_paths,
        source_device_dbids={
            str(source): source_to_dbid[source] for source in sources},
        dual_boot=dual_boot,
        defer_cleanup=defer_cleanup,
        rockbox_media_cache=rockbox_media_cache,
        interrupted_file_verifier=interrupted_file_verifier,
        stock_format=write_format,
        artwork_warning=artwork_warning,
        catalog_witness=candidate_witness,
        catalog_warning=catalog_warning,
        catalog_notice=catalog_notice,
        captured_export=display_rows is not None,
    )
    if (not plan.requires_commit and shadow is not None and shadow_store is not None
            and shadow_sources != shadow.sources
            and not shadow_store.root.resolve().is_relative_to(root)):
        # A legacy baseline may be a true no-op forever. Retain newly acquired
        # Mac source proofs without manufacturing a device commit/generation.
        # Another generation wins; a disposable-cache failure is advisory.
        try:
            if shadow_store.load(identity_key) == shadow:
                shadow_store.save(replace(shadow, sources=shadow_sources))
        except (OSError, ValueError):
            logging.getLogger("soundbox").warning(
                "Could not enrich disposable stock source proofs", exc_info=True)
    planning.advance(label='Validated iPod export plan')
    return plan


def committed_stock_export_witness(
    plan: StockIpodSyncPlan | StockIpodDeletePlan, session: DeviceSession,
    *, native_capture: StockIpodNativeCapture | None = None,
) -> tuple[StockCatalogWitness | None, str]:
    """Optional readback evidence retains the original operation's mount owner."""
    if (isinstance(plan, StockIpodSyncPlan) and not plan.captured_export
            or plan.catalog_witness is None):
        return None, ''
    try:
        session.revalidate()
        if session.target != plan.target or session.mount_identity != plan.target_mount_identity:
            raise ValueError('The exported device changed before comparison readback')
        captured = native_capture or capture_stock_ipod_native(plan.target, include_artwork=True)
        captured.require_target(plan.target)
        verify_export_witness(plan.catalog_witness, captured.database, captured.artwork_database_sha256)
        session.revalidate()
        return plan.catalog_witness, ''
    except Exception as exc:
        return None, f'The device operation succeeded, but its comparison baseline could not be verified: {exc}'


def commit_stock_ipod_sync(
    plan: StockIpodSyncPlan,
    on_track_committed: Callable[[Path], None] | None = None,
) -> StockIpodCommitResult:
    """Commit a validated plan with DB-last ordering and exact-file rollback."""
    sources = [*plan.source_guard_paths, *[item.source for item in plan.copies + plan.rewrites]]
    session = DeviceSession(plan.target, sources, create=False)
    preflight = _StockProgress('Checking iPod files',
        len(plan.adoptions) + len(plan.copies) + len(plan.rewrites) + len(plan.media_deletes),
        'Checking source and destination files')
    if plan.catalog_only and any((plan.copies, plan.adoptions, plan.rewrites,
                                  plan.media_deletes, plan.artwork_delete_paths)):
        raise StockIpodError("A catalog-only plan cannot copy, replace or delete audio")
    if plan.incremental_catalog and any((plan.adoptions, plan.rewrites, plan.media_deletes,
                                         plan.artwork_delete_paths, plan.shadow_sources,
                                         plan.shadow_store, plan.dual_boot)):
        raise StockIpodError("An incremental catalog plan may only add new audio and edit selected catalog rows")
    if session.mount_identity != plan.target_mount_identity:
        raise StockIpodError(
            "iPod target was remounted after dry-run; make a fresh plan")
    _db_path(plan.target)  # a compressed/SQLite layout may have appeared after planning
    database = session.guard(plan.database_relative_path)
    play_counts = session.guard(database.with_name("Play Counts"))
    original_database = database.read_bytes()
    if hashlib.sha256(original_database).hexdigest() != plan.database_sha256_before:
        raise StockIpodError("iTunesDB changed after dry-run; make a fresh plan")
    original_play_counts = play_counts.read_bytes() if play_counts.is_file() else None
    if plan.play_counts_sha256_before is None:
        if play_counts.exists():
            raise StockIpodError("Play Counts appeared after dry-run; make a fresh plan")
    elif original_play_counts is None or hashlib.sha256(
            original_play_counts).hexdigest() != plan.play_counts_sha256_before:
        raise StockIpodError("Play Counts changed after dry-run; make a fresh plan")
    for adoption in plan.adoptions:
        destination = session.guard(adoption.relative_path)
        if (
            plan.interrupted_file_verifier is None
            or not plan.interrupted_file_verifier.verify(
                destination,
                relative_path=adoption.relative_path,
                size=adoption.size,
                sha256=adoption.sha256,
            )
            or destination.is_symlink()
            or not destination.is_file()
        ):
            raise StockIpodError(
                "Interrupted stock recovery file changed after dry-run: "
                f"{adoption.relative_path}")
        preflight.advance(label=str(adoption.relative_path))
    for copy_item in plan.copies:
        if copy_item.media_plan is not None:
            if plan.rockbox_media_cache is None:
                raise StockIpodError(
                    "Bounded stock payload lost its Rockbox staging cache")
            # Exact source/payload proof belongs to per-track acquisition.
        elif copy_item.source.stat().st_size != copy_item.size or _sha256(
                copy_item.source) != copy_item.sha256:
            raise StockIpodError(
                f"Source changed after dry-run: {copy_item.source}")
        destination = session.guard(copy_item.relative_path)
        if destination.exists():
            raise StockIpodError(f"Planned destination is no longer free: {destination}")
        preflight.advance(label=copy_item.source.name)
    for rewrite_item in plan.rewrites:
        if rewrite_item.media_plan is not None:
            if plan.rockbox_media_cache is None:
                raise StockIpodError(
                    "Bounded stock rewrite lost its Rockbox staging cache")
            # Exact source/payload proof belongs to per-track acquisition.
        elif rewrite_item.source.stat().st_size != rewrite_item.size or _sha256(
                rewrite_item.source) != rewrite_item.sha256:
            raise StockIpodError(
                f"Prepared rewrite changed after dry-run: {rewrite_item.source}")
        destination = session.guard(rewrite_item.relative_path)
        if (
            not destination.is_file()
            or destination.stat().st_size != rewrite_item.size_before
            or (
                not plan.dual_boot
                and _sha256(destination) != rewrite_item.sha256_before
            )
        ):
            raise StockIpodError(
                f"Existing media changed after dry-run: {destination}")
        preflight.advance(label=str(rewrite_item.relative_path))
    for delete_item in plan.media_deletes:
        destination = session.guard(delete_item.relative_path)
        if (
            not destination.is_file()
            or destination.stat().st_size != delete_item.size
            or (
                not plan.dual_boot
                and _sha256(destination) != delete_item.sha256
            )
        ):
            raise StockIpodError(
                f"Managed media changed after dry-run: {delete_item.relative_path}")
        preflight.advance(label=str(delete_item.relative_path))
    before_by_path = {item.relative_path: item for item in plan.artwork_before}
    for relative in plan.artwork_delete_paths:
        before = before_by_path.get(relative)
        live = session.guard(relative)
        if (
            before is None
            or not live.is_file()
            or live.stat().st_size != before.size
            or _sha256(live) != before.sha256
        ):
            raise StockIpodError(
                f"Managed artwork changed after dry-run: {relative}")
    if plan.rendered_artwork is not None and (
            plan.rendered_artwork.publish_artwork_db
            or plan.rendered_artwork.range_writes):
        output_paths = [Path("iPod_Control") / "Artwork" / "ArtworkDB"]
        if not plan.rendered_artwork.incremental:
            output_paths.extend(
                item.relative_path for item in plan.rendered_artwork.ithmb_files)
        for relative in output_paths:
            live = session.guard(relative)
            before = before_by_path.get(relative)
            if before is None:
                if live.exists():
                    raise StockIpodError(
                        f"Artwork file appeared after dry-run: {relative}")
            elif (
                not live.is_file()
                or live.stat().st_size != before.size
                or _sha256(live) != before.sha256
            ):
                raise StockIpodError(
                    f"Artwork file changed after dry-run: {relative}")
        if plan.rendered_artwork.incremental:
            for ithmb in plan.rendered_artwork.ithmb_files:
                live = session.guard(ithmb.relative_path)
                try:
                    stat = live.stat()
                except OSError as exc:
                    raise StockIpodError(
                        f"Artwork file disappeared after dry-run: {ithmb.relative_path}"
                    ) from exc
                if (
                    stat.st_size != ithmb.before_size
                    or stat.st_mtime_ns != ithmb.before_mtime_ns
                ):
                    raise StockIpodError(
                        f"Artwork file changed after dry-run: {ithmb.relative_path}")
            segments = [item.segment for item in plan.rendered_artwork.range_writes]
        else:
            segments = [
                segment for ithmb in plan.rendered_artwork.ithmb_files
                for segment in ithmb.segments
            ]
        for segment in segments:
            if (
                not segment.path.is_file()
                or segment.path.stat().st_size != segment.size
                or _sha256(segment.path) != segment.sha256
            ):
                raise StockIpodError(
                    f"Prepared artwork changed after dry-run: {segment.path}")
        if not plan.rendered_artwork.incremental:
            for ithmb in plan.rendered_artwork.ithmb_files:
                digest = hashlib.sha256()
                size = 0
                for segment in ithmb.segments:
                    size += segment.size
                    with segment.path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                if size != ithmb.size or digest.hexdigest() != ithmb.sha256:
                    raise StockIpodError(
                        f"Prepared artwork layout changed after dry-run: {ithmb.relative_path}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    recovery = Path(".soundbox") / "stock-ipod-recovery" / (
        f"{stamp}-{plan.database_sha256_before[:12]}")
    try:
        statvfs = os.statvfs(plan.target)
        free = statvfs.f_bavail * statvfs.f_frsize
    except OSError as exc:
        raise StockIpodError(f"Could not verify free space on {plan.target}: {exc}") from exc
    # Peak transaction footprint: new media + recovery copies + staged DB
    # replacement. Keep a small fixed margin for FAT directory allocation.
    recovery_bytes = len(original_database) + len(plan.rendered_database)
    recovery_bytes += sum(item.size for item in plan.artwork_before)
    if not plan.dual_boot:
        recovery_bytes += sum(item.size_before for item in plan.rewrites)
    if original_play_counts is not None:
        recovery_bytes += len(original_play_counts) * 2
    required = (
        plan.bytes_to_copy + plan.artwork_bytes_to_copy
        + recovery_bytes + 1024 * 1024)
    if required > free:
        raise StockIpodError(
            f"Not enough free space for the recoverable stock transaction: "
            f"needs {required} bytes, has {free} bytes.")
    recovery_abs = session.guard(recovery)
    phase_progress('Preparing iPod recovery', 0, 1, 'Saving the current iPod catalogs')
    session.atomic_write(recovery / "iTunesDB.before", original_database)
    if original_play_counts is not None:
        session.atomic_write(recovery / "Play Counts.before", original_play_counts)
    for artwork_item in plan.artwork_before:
        source = session.guard(artwork_item.relative_path)
        destination = recovery / "artwork" / (
            artwork_item.relative_path.name + ".before")
        session.atomic_copy(source, session.guard(destination))
    for rewrite in plan.rewrites:
        if plan.dual_boot:
            continue
        source = session.guard(rewrite.relative_path)
        destination = (
            recovery / "media" / rewrite.relative_path.parent
            / (rewrite.relative_path.name + ".before"))
        session.atomic_copy(source, session.guard(destination))
    manifest = json.dumps({**plan.summary(), "transaction_state": "executing"}, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    session.atomic_write(recovery / "plan.json", manifest)
    if plan.rendered_artwork is not None and plan.rendered_artwork.incremental:
        range_journal = {
            "ithmb_before": {
                item.relative_path.name: {
                    "size": item.before_size,
                    "mtime_ns": item.before_mtime_ns,
                    "final_size": item.size,
                }
                for item in plan.rendered_artwork.ithmb_files
            },
            "writes": [
                {
                    "path": str(item.relative_path),
                    "offset": item.offset,
                    "size": item.segment.size,
                    "sha256": item.segment.sha256,
                }
                for item in plan.rendered_artwork.range_writes
            ],
        }
        session.atomic_write(
            recovery / "artwork-ranges.json",
            json.dumps(range_journal, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )

    phase_progress('Preparing iPod recovery', 1, 1, 'Saved the current iPod catalogs')
    media_progress = _StockProgress('iTunes media', plan.bytes_to_copy, 'Copying songs to iPod')
    copied: list[Path] = []
    rewritten: list[StockMediaRewrite] = []
    committed = False
    artwork_committed = False
    try:
        for copy_item in plan.copies:
            checkpoint()
            destination = session.guard(copy_item.relative_path)
            prepared = None
            copy_source = copy_item.source
            staging = ExitStack()
            try:
                if copy_item.media_plan is not None:
                    assert plan.rockbox_media_cache is not None
                    prepared = plan.rockbox_media_cache.materialize(
                        copy_item.media_plan)
                    copy_source = prepared.payload
                else:
                    snapshot = staging.enter_context(acquire_source(
                        SourceContract(copy_item.source.resolve(), copy_item.size, copy_item.sha256),
                        Path(tempfile.gettempdir()) / "soundbox-source-staging"))
                    copy_source = snapshot.path
                prior = media_progress.done
                def copy_progress(phase, done, _total):
                    work = 2 * prior + done + (copy_item.size if phase == 'verifying' else 0)
                    phase_progress('iTunes verifying' if phase == 'verifying' else 'iTunes copying',
                                   work, 2 * plan.bytes_to_copy, copy_item.source.name)
                with session.atomic_copy_verified(copy_source, destination,
                        expected_size=copy_item.size, expected_sha256=copy_item.sha256,
                        on_progress=copy_progress) as receipt:
                    if not isinstance(receipt, VerifiedCopyReceipt):
                        raise PublicationOwnershipLost('New media copy lost its exact publication receipt')
                    receipt.require_current(session, destination, size=copy_item.size, sha256=copy_item.sha256)
                copied.append(destination)
            finally:
                staging.close()
                if prepared is not None:
                    assert plan.rockbox_media_cache is not None
                    plan.rockbox_media_cache.discard(prepared)
            media_progress.advance(copy_item.size, copy_item.source.name)
            if on_track_committed is not None:
                on_track_committed(copy_item.relative_path)
            if plan.dual_boot:
                _pace_classic_usb()
        for rewrite_item in plan.rewrites:
            checkpoint()
            destination = session.guard(rewrite_item.relative_path)
            prepared = None
            copy_source = rewrite_item.source
            staging = ExitStack()
            try:
                if rewrite_item.media_plan is not None:
                    assert plan.rockbox_media_cache is not None
                    prepared = plan.rockbox_media_cache.materialize(
                        rewrite_item.media_plan)
                    copy_source = prepared.payload
                else:
                    snapshot = staging.enter_context(acquire_source(
                        SourceContract(rewrite_item.source.resolve(), rewrite_item.size, rewrite_item.sha256),
                        Path(tempfile.gettempdir()) / "soundbox-source-staging"))
                    copy_source = snapshot.path
                if plan.dual_boot:
                    saved = session.guard(
                        recovery / "media" / rewrite_item.relative_path.parent
                        / (rewrite_item.relative_path.name + ".before"))
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    session.revalidate()
                    os.replace(destination, saved)
                    rewritten.append(rewrite_item)
                session.atomic_copy(copy_source, destination)
                if not plan.dual_boot:
                    rewritten.append(rewrite_item)
                if destination.stat().st_size != rewrite_item.size or _sha256(
                        destination) != rewrite_item.sha256:
                    raise StockIpodError(
                        f"Rewritten media failed verification: {destination}")
            finally:
                staging.close()
                if prepared is not None:
                    assert plan.rockbox_media_cache is not None
                    plan.rockbox_media_cache.discard(prepared)
            media_progress.advance(rewrite_item.size, rewrite_item.source.name)
            if on_track_committed is not None:
                on_track_committed(rewrite_item.relative_path)
            if plan.dual_boot:
                _pace_classic_usb()
        artwork_total = 0
        if plan.rendered_artwork is not None and (
                plan.rendered_artwork.publish_artwork_db or plan.rendered_artwork.range_writes):
            artwork_total = (sum(item.segment.size for item in plan.rendered_artwork.range_writes)
                if plan.rendered_artwork.incremental else
                sum(item.size for item in plan.rendered_artwork.ithmb_files))
            if plan.rendered_artwork.publish_artwork_db:
                artwork_total += len(plan.rendered_artwork.artwork_db)
        artwork_progress = _StockProgress('iTunes artwork', artwork_total, 'Saving cover art')
        session.revalidate()
        if _sha256(database) != plan.database_sha256_before:
            raise StockIpodError("iTunesDB changed during media copy; aborting")
        if plan.rendered_artwork is not None and (
                plan.rendered_artwork.publish_artwork_db
                or plan.rendered_artwork.range_writes):
            if plan.rendered_artwork.incremental:
                for write in sorted(
                    plan.rendered_artwork.range_writes,
                    key=lambda item: (str(item.relative_path), item.offset),
                ):
                    session.write_verified_range(
                        write.segment.path,
                        write.relative_path,
                        offset=write.offset,
                        size=write.segment.size,
                        sha256=write.segment.sha256,
                    )
                    artwork_progress.advance(write.segment.size, str(write.relative_path))
                for ithmb in plan.rendered_artwork.ithmb_files:
                    if session.guard(ithmb.relative_path).stat().st_size != ithmb.size:
                        raise StockIpodError(
                            f"Incremental artwork size failed verification: "
                            f"{ithmb.relative_path}")
            else:
                for ithmb in plan.rendered_artwork.ithmb_files:
                    destination = session.guard(ithmb.relative_path)
                    session.atomic_concat(
                        [segment.path for segment in ithmb.segments], destination)
                    if destination.stat().st_size != ithmb.size or _sha256(
                            destination) != ithmb.sha256:
                        raise StockIpodError(
                            f"Committed artwork failed verification: {destination}")
                    artwork_progress.advance(ithmb.size, str(ithmb.relative_path))
            if plan.rendered_artwork.publish_artwork_db:
                artwork_db = session.guard(
                    Path("iPod_Control") / "Artwork" / "ArtworkDB")
                session.atomic_write(artwork_db, plan.rendered_artwork.artwork_db)
                if _sha256(artwork_db) != hashlib.sha256(
                        plan.rendered_artwork.artwork_db).hexdigest():
                    raise StockIpodError("Committed ArtworkDB failed verification")
                artwork_committed = True
                artwork_progress.advance(len(plan.rendered_artwork.artwork_db), 'ArtworkDB')
        checkpoint()
        database_progress = _StockProgress('iTunes database', 1, 'Saving songs and playlists')
        with cancellation_disabled():
            session.atomic_write(database, plan.rendered_database)
            committed = True
            if original_play_counts is not None:
                consumed = session.guard(recovery / "Play Counts.consumed")
                consumed.parent.mkdir(parents=True, exist_ok=True)
                session.revalidate()
                os.replace(play_counts, consumed)
            session.revalidate()
        database_progress.advance(label='Saved songs and playlists')
    except Exception as original_exc:
        with cancellation_disabled():
            rollback_failures: list[str] = []
            if getattr(original_exc, 'recovery_status', '') == 'recovery required':
                rollback_failures.append('Uncertain new media ownership: ' + str(original_exc))
            if committed:
                try:
                    session.atomic_write(database, original_database)
                except Exception as rollback_exc:
                    rollback_failures.append(f"iTunesDB: {rollback_exc}")
                if original_play_counts is not None and not play_counts.exists():
                    try:
                        session.atomic_write(play_counts, original_play_counts)
                    except Exception as rollback_exc:
                        rollback_failures.append(f"Play Counts: {rollback_exc}")
            if artwork_committed or (
                plan.rendered_artwork is not None
                and not plan.rendered_artwork.incremental
                and (
                    plan.rendered_artwork.publish_artwork_db
                    or plan.rendered_artwork.range_writes
                )
            ):
                output_paths = [Path("iPod_Control") / "Artwork" / "ArtworkDB"]
                if (
                    plan.rendered_artwork is not None
                    and not plan.rendered_artwork.incremental
                ):
                    output_paths.extend(
                        item.relative_path for item in plan.rendered_artwork.ithmb_files)
                for relative in output_paths:
                    saved_before = before_by_path.get(relative)
                    destination = session.guard(relative)
                    try:
                        if saved_before is None:
                            destination.unlink(missing_ok=True)
                        else:
                            saved = session.guard(
                                recovery / "artwork" / (relative.name + ".before"))
                            session.atomic_copy(saved, destination)
                    except Exception as rollback_exc:
                        rollback_failures.append(f"{relative}: {rollback_exc}")
            for destination in reversed(copied):
                try:
                    session.guard(destination).unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_failures.append(f"orphan media {destination}: {rollback_exc}")
            for rewrite_item in reversed(rewritten):
                destination = session.guard(rewrite_item.relative_path)
                saved = session.guard(
                    recovery / "media" / rewrite_item.relative_path.parent
                    / (rewrite_item.relative_path.name + ".before"))
                try:
                    if plan.dual_boot:
                        destination.unlink(missing_ok=True)
                        session.revalidate()
                        os.replace(saved, destination)
                    else:
                        session.atomic_copy(saved, destination)
                    if (
                        destination.stat().st_size != rewrite_item.size_before
                        or (
                            not plan.dual_boot
                            and _sha256(destination) != rewrite_item.sha256_before
                        )
                    ):
                        raise StockIpodError(
                            f"Restored media failed verification: {destination}")
                except Exception as rollback_exc:
                    rollback_failures.append(
                        f"rewritten media {destination}: {rollback_exc}")
            record_outcome(session, recovery, "recovery required" if rollback_failures else "rolled back",
                           error=str(original_exc), local_root=(plan.rockbox_media_cache.root
                               if plan.rockbox_media_cache else Path(tempfile.gettempdir()) / "soundbox-source-staging"))
            setattr(original_exc, "recovery_status", "recovery required" if rollback_failures else "rolled back")
            if rollback_failures:
                failure = StockIpodError(
                    "Stock sync failed and exact rollback was incomplete; recovery is at "
                    f"{recovery_abs}. Failures: {'; '.join(rollback_failures)}")
                failure.recovery_status = "recovery required"
                raise failure from original_exc
            raise

    try:
        record_outcome(session, recovery, "committed", local_root=(plan.rockbox_media_cache.root
                       if plan.rockbox_media_cache else Path(tempfile.gettempdir()) / "soundbox-source-staging"))
        cleanup_progress = _StockProgress('iTunes cleanup',
            (0 if plan.defer_cleanup else len(plan.media_deletes) + len(plan.artwork_delete_paths)) + 2,
            'Finishing the iPod export')
        cleanup_failures: list[str] = []
        deleted_media_files = 0
        deleted_bytes = 0
        if not plan.defer_cleanup:
            for item in plan.media_deletes:
                path = session.guard(item.relative_path)
                try:
                    if (
                        not path.is_file()
                        or path.stat().st_size != item.size
                        or _sha256(path) != item.sha256
                    ):
                        raise StockIpodError("file changed before cleanup")
                    path.unlink()
                    deleted_media_files += 1
                    deleted_bytes += item.size
                except Exception as exc:
                    cleanup_failures.append(f"{item.relative_path}: {exc}")
                cleanup_progress.advance(label=str(item.relative_path))
            for relative in plan.artwork_delete_paths:
                path = session.guard(relative)
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_failures.append(f"{relative}: {exc}")
                cleanup_progress.advance(label=str(relative))
            session.revalidate()

        database_after = hashlib.sha256(plan.rendered_database).hexdigest()
        shadow_updated = False
        shadow_warning = ""
        if plan.shadow_store is not None and plan.identity_key:
            try:
                artwork_dir = plan.target / "iPod_Control" / "Artwork"
                artwork_db = artwork_dir / "ArtworkDB"
                artwork = artwork_db.read_bytes() if artwork_db.is_file() else None
                artwork_sha = hashlib.sha256(artwork).hexdigest() if artwork is not None else ""
                shadow_ithmb = _stock_ithmb_tokens(session, artwork)
                plan.shadow_store.committed(
                    identity_key=plan.identity_key,
                    model=plan.model_number,
                    serial_number=plan.serial_number,
                    database_sha256=database_after,
                    artwork_database_sha256=artwork_sha,
                    ithmb=shadow_ithmb,
                    sources=plan.shadow_sources,
                    artwork=plan.shadow_artwork,
                    playlists=plan.shadow_playlists,
                )
                shadow_updated = True
            except Exception as exc:  # device commit is valid; cache can rebuild
                shadow_warning = (
                    "The iPod commit succeeded, but its Mac-side fast-sync baseline "
                    f"could not be saved: {exc}")

        cleanup_progress.advance(label='Checked the sync baseline')
        cleanup_progress.report('Checking catalog correspondence', force=True)
        witness, witness_warning = ((None, '') if plan.dual_boot else
                                    committed_stock_export_witness(plan, session))
        cleanup_progress.advance(label='Cleanup needs attention' if cleanup_failures else 'Finished export cleanup')
        return StockIpodCommitResult(
            recovery_directory=recovery_abs,
            copied_tracks=len(copied),
            recovered_tracks=len(plan.adoptions),
            reused_tracks=plan.reused_track_count,
            rewritten_tracks=len(rewritten),
            playlists=plan.playlist_names,
            database_sha256=database_after,
            artwork_tracks=(
                plan.rendered_artwork.linked_tracks if plan.rendered_artwork else 0),
            unique_artwork=(
                plan.rendered_artwork.unique_images if plan.rendered_artwork else 0),
            metadata_updates=len(plan.metadata_updates),
            artwork_pixel_bytes=(
                sum(item.segment.size for item in plan.rendered_artwork.range_writes)
                if plan.rendered_artwork and plan.rendered_artwork.incremental else
                sum(item.size for item in plan.rendered_artwork.ithmb_files)
                if plan.rendered_artwork else 0
            ),
            deleted_media_files=deleted_media_files,
            deleted_bytes=deleted_bytes,
            cleanup_failures=tuple(cleanup_failures),
            shadow_updated=shadow_updated,
            shadow_warning=shadow_warning,
            artwork_warning=plan.artwork_warning,
            catalog_warning=' '.join(value for value in (plan.catalog_warning, witness_warning) if value),
            catalog_notice=plan.catalog_notice,
            catalog_playlist_updates=len(plan.catalog_playlist_updates),
            catalog_artwork_updates=plan.catalog_artwork_track_count,
            catalog_witness=witness,
        )

    except Exception as exc:
        raise CommittedOperationError(
            f"Device changes committed; final cleanup failed: {exc}",
            StockIpodCommitResult(
                recovery_directory=recovery_abs, copied_tracks=len(copied),
                recovered_tracks=len(plan.adoptions), reused_tracks=plan.reused_track_count,
                rewritten_tracks=len(rewritten), playlists=plan.playlist_names,
                database_sha256=hashlib.sha256(plan.rendered_database).hexdigest()),
        ) from exc


@cancellation_disabled()
def rollback_committed_stock_ipod_sync(
    plan: StockIpodSyncPlan,
    result: StockIpodCommitResult,
) -> None:
    """Restore one just-committed stock plan from its exact recovery material.

    This is intentionally narrow support for the composite dual-boot
    transaction: it refuses unless the live iTunesDB is exactly the plan's
    committed output and every required recovery file is present. The old
    iTunesDB is published last; newly copied media is removed only afterward,
    when it is already unreferenced.
    """
    if (plan.media_deletes or plan.artwork_delete_paths) and not plan.defer_cleanup:
        raise StockIpodError(
            "A stock plan that already cleaned orphan files cannot be rolled back")
    session = DeviceSession(plan.target, [], create=False)
    database = session.guard(plan.database_relative_path)
    expected_after = hashlib.sha256(plan.rendered_database).hexdigest()
    if not database.is_file() or _sha256(database) != expected_after:
        raise StockIpodError(
            "Committed iTunesDB changed before composite rollback")
    recovery = result.recovery_directory
    if recovery.parent.parent.resolve() != (plan.target / ".soundbox").resolve():
        raise StockIpodError("Stock recovery directory is outside this device")
    saved_database = session.guard(recovery / "iTunesDB.before")
    if not saved_database.is_file() or _sha256(
            saved_database) != plan.database_sha256_before:
        raise StockIpodError("Stock recovery iTunesDB is missing or invalid")

    before_by_path = {item.relative_path: item for item in plan.artwork_before}
    if plan.rendered_artwork is not None and (
            plan.rendered_artwork.publish_artwork_db
            or plan.rendered_artwork.range_writes):
        output_paths = [Path("iPod_Control") / "Artwork" / "ArtworkDB"]
        if not plan.rendered_artwork.incremental:
            output_paths.extend(
                item.relative_path for item in plan.rendered_artwork.ithmb_files)
        for relative in output_paths:
            destination = session.guard(relative)
            before = before_by_path.get(relative)
            if before is None:
                destination.unlink(missing_ok=True)
                continue
            saved = session.guard(
                recovery / "artwork" / (relative.name + ".before"))
            if not saved.is_file() or _sha256(saved) != before.sha256:
                raise StockIpodError(
                    f"Stock artwork recovery is missing or invalid: {relative}")
            session.atomic_copy(saved, destination)

    for item in plan.rewrites:
        destination = session.guard(item.relative_path)
        saved = session.guard(
            recovery / "media" / item.relative_path.parent
            / (item.relative_path.name + ".before"))
        if (
            not saved.is_file()
            or saved.stat().st_size != item.size_before
            or (not plan.dual_boot and _sha256(saved) != item.sha256_before)
        ):
            raise StockIpodError(
                f"Stock media recovery is missing or invalid: {item.relative_path}")
        if plan.dual_boot:
            destination.unlink(missing_ok=True)
            session.revalidate()
            os.replace(saved, destination)
        else:
            session.atomic_copy(saved, destination)

    play_counts = session.guard(database.with_name("Play Counts"))
    if plan.play_counts_sha256_before is not None:
        saved_counts = session.guard(recovery / "Play Counts.before")
        if not saved_counts.is_file() or _sha256(
                saved_counts) != plan.play_counts_sha256_before:
            raise StockIpodError("Stock Play Counts recovery is missing or invalid")
        session.atomic_copy(saved_counts, play_counts)
    elif play_counts.exists():
        raise StockIpodError(
            "Play Counts appeared after the stock commit; refusing rollback")

    session.atomic_copy(saved_database, database)
    if _sha256(database) != plan.database_sha256_before:
        raise StockIpodError("Restored iTunesDB failed verification")
    for copy_item in plan.copies:
        destination = session.guard(copy_item.relative_path)
        if destination.is_file() and (
            destination.stat().st_size != copy_item.size
            or _sha256(destination) != copy_item.sha256
        ):
            raise StockIpodError(
                f"New stock media changed before rollback: {destination}")
        destination.unlink(missing_ok=True)


def plan_stock_ipod_playlist_delete(
    target: str | Path,
    playlist_ids: list[int] | tuple[int, ...],
    *,
    model_number: str,
    filesystem: str,
    stock_art_cache: StockArtworkCache | None = None,
    allow_dual_boot: bool = False,
    repair_empty_playlist_index: bool = False,
    usb_family: str = "",
    firewire_guid: str = '',
    catalog_witness: StockCatalogWitness | None = None,
    identity_key: str = '',
    serial_number: str = '',
    shadow_store: StockIpodShadowStore | None = None,
) -> StockIpodDeletePlan:
    """Plan deletion of managed playlists and newly orphaned user-list tracks.

    Ownership is defined exclusively by regular user playlists. The master
    library and Apple's smart playlists are derived indexes, so counting either
    would make every track immortal. The only deletable Apple system index is
    the proven-empty/removable Videos smart playlist; every other smart list
    remains immutable. Pre-existing loose tracks are not swept: candidates come
    only from regular playlist(s) explicitly selected for deletion. The narrow
    repair mode is the exception: it removes every orphaned iTunesDB record and
    linked device file only after proving that a pre-composite dual device has
    zero user playlists.
    """
    checkpoint()
    phase_progress('Reading iPod playlists', 0, 1, '')
    root = Path(target).expanduser().resolve(strict=True)
    planning_session = DeviceSession(root, [], create=False)
    if str(filesystem or "").lower() not in {"msdos", "fat32", "fat"}:
        raise StockIpodError(
            f"Stock iPod writes require an explicitly confirmed FAT32 volume; "
            f"got {filesystem or 'unknown'}.")
    dual_boot = (root / ".rockbox").exists()
    if dual_boot:
        if not allow_dual_boot:
            raise StockIpodError(
                "Dual-boot playlist deletion requires the guarded pre-composite path.")
        if device_manifest.manifest_path(root).exists():
            raise StockIpodError(
                "Composite ownership already exists; stock-only playlist deletion "
                "could split Rockbox and iTunesDB state.")
    if repair_empty_playlist_index and not (
            dual_boot and allow_dual_boot):
        raise StockIpodError(
            "Empty-playlist index repair is limited to pre-composite dual boot.")
    model = normalize_model_number(model_number)
    requested_ids = tuple(dict.fromkeys(int(value) for value in playlist_ids))
    if not requested_ids and not repair_empty_playlist_index:
        raise StockIpodError("Select at least one playlist to delete")
    if requested_ids and repair_empty_playlist_index:
        raise StockIpodError(
            "Empty-playlist index repair cannot also delete playlists")

    database = _db_path(root)
    try:
        captured = capture_stock_ipod_native(root, include_artwork=True)
    except StockIpodCaptureError as exc:
        raise StockIpodError(str(exc)) from exc
    original = captured.database
    write_format = _captured_write_format(captured, usb_family=usb_family, firewire_guid=firewire_guid)
    if stock_art_cache is not None and write_format.supports_artwork:
        stock_art_cache = stock_art_cache.for_profile(write_format.artwork_profile)
    if len(original) < 0x32 or original[:4] != b"mhbd":
        raise StockIpodError("iTunesDB has an invalid header")
    hashing_scheme = struct.unpack_from("<H", original, 0x30)[0]
    if hashing_scheme != write_format.checksum_scheme:
        raise StockIpodError(
            f"Stock database requires unsupported checksum scheme {hashing_scheme}")

    parsed = parse_itunesdb(io.BytesIO(original))
    checkpoint()
    parsed_tracks = get_ipod_tracks(parsed)
    play_counts_hash: str | None = None
    merged_entries = 0
    if captured.play_counts is not None:
        play_counts_hash = hashlib.sha256(captured.play_counts).hexdigest()
        entries = captured.playback_entries(expected_tracks=len(parsed_tracks))
        if entries is None or len(entries) != len(parsed_tracks):
            raise StockIpodError(
                "Play Counts is unreadable or does not match the iTunesDB; "
                "refusing to risk playback history.")
        merge_playcounts(parsed_tracks, entries)
        merged_entries = sum(1 for item in entries if item.has_data)

    track_id_to_dbid: dict[int, int] = {}
    raw_by_dbid: dict[int, dict] = {}
    for raw in parsed_tracks:
        checkpoint()
        track_id = int(raw.get("trackID") or 0)
        dbid = int(raw.get("dbid") or 0)
        if not track_id or not dbid or dbid in raw_by_dbid or track_id in track_id_to_dbid:
            raise StockIpodError("Existing iTunesDB track identity is invalid")
        track_id_to_dbid[track_id] = dbid
        raw_by_dbid[dbid] = raw

    raw_regular = [
        raw for raw in (parsed.get("mhlp") or [])
        if not raw.get("isMaster")
    ]
    smart_raw = list(parsed.get("mhlp_smart") or [])

    def removable_system_playlist(raw: dict) -> bool:
        return (
            bool(raw.get("isSmartPlaylist"))
            and str(raw.get("Title") or "").casefold() == "videos"
            and int(raw.get("mhsd5Type") or 0) == 0
        )

    # Only genuine user playlists from the ordinary dataset are independently
    # deletable.  Apple can also mirror Videos there as a smart alias with a
    # different ID; that alias belongs to the canonical MHSD-5 Videos index and
    # must never be mistaken for a user playlist.
    regular_by_id = {
        int(raw.get("playlistID") or 0): raw
        for raw in raw_regular if not raw.get("isSmartPlaylist")
    }
    if repair_empty_playlist_index and regular_by_id:
        raise StockIpodError(
            "Empty-playlist index repair requires zero user playlists")
    removable_smart_by_id = {
        int(raw.get("playlistID") or 0): raw
        for raw in smart_raw if removable_system_playlist(raw)
    }
    deletable_by_id = dict(regular_by_id)
    for playlist_id, raw in removable_smart_by_id.items():
        if playlist_id in deletable_by_id:
            raise StockIpodError("Deletable playlist identities are ambiguous")
        deletable_by_id[playlist_id] = raw
    missing_ids = [value for value in requested_ids if value not in deletable_by_id]
    if missing_ids:
        raise StockIpodError(
            "Selected playlist no longer exists or is not safely deletable "
            f"(ID {missing_ids[0]})")
    selected_raw = [deletable_by_id[value] for value in requested_ids]
    selected_regular_ids = set(requested_ids) & set(regular_by_id)
    selected_smart_ids = set(requested_ids) & set(removable_smart_by_id)
    deleting_videos_system_index = bool(selected_smart_ids)
    selected_regular_raw = [
        raw for raw in raw_regular
        if int(raw.get("playlistID") or 0) in selected_regular_ids
    ]
    remaining_raw = [
        raw for raw in raw_regular
        if int(raw.get("playlistID") or 0) not in selected_regular_ids
        and not (
            deleting_videos_system_index and removable_system_playlist(raw)
        )
    ]
    remaining_smart_raw = [
        raw for raw in smart_raw
        if int(raw.get("playlistID") or 0) not in selected_smart_ids
    ]

    def referenced_dbids(raw_playlists: list[dict]) -> set[int]:
        refs: set[int] = set()
        for raw_playlist in raw_playlists:
            checkpoint()
            for item in raw_playlist.get("items") or []:
                checkpoint()
                track_id = int(item.get("trackID") or 0)
                dbid = track_id_to_dbid.get(track_id)
                if dbid is None:
                    raise StockIpodError(
                        f"Playlist {raw_playlist.get('Title')!r} references an "
                        "unknown track")
                refs.add(dbid)
        return refs

    candidates = (
        set(raw_by_dbid)
        if repair_empty_playlist_index
        else referenced_dbids(selected_regular_raw)
    )
    retained_refs = referenced_dbids([
        raw for raw in remaining_raw if not raw.get("isSmartPlaylist")
    ])
    deleted_dbids = candidates - retained_refs
    retained_raw_tracks = [
        raw for raw in parsed_tracks if int(raw.get("dbid") or 0) not in deleted_dbids
    ]
    retained_track_ids = {
        int(raw.get("trackID") or 0) for raw in retained_raw_tracks
    }
    retained_locations = {
        stock_delete.path_key(str(raw.get("location") or ""))
        for raw in retained_raw_tracks if raw.get("location")
    }

    delete_paths: dict[str, tuple[Path, list[int]]] = {}
    music_root = root / "iPod_Control" / "Music"
    for position, dbid in enumerate(sorted(deleted_dbids)):
        checkpoint()
        phase_progress('Checking songs to delete', position, len(deleted_dbids), '')
        raw = raw_by_dbid[dbid]
        location = str(raw.get("location") or "")
        if not location or stock_delete.path_key(location) in retained_locations:
            continue
        path = _location_path(root, location, check_parent=False)
        if path is None or not path.is_relative_to(music_root):
            raise StockIpodError(
                f"Track {dbid} has a media location outside iPod_Control/Music")
        key = stock_delete.path_key(path.relative_to(root))
        if key not in delete_paths:
            delete_paths[key] = (path, [])
        delete_paths[key][1].append(dbid)
    media_rows = []
    for _key, (path, dbids) in sorted(delete_paths.items()):
        checkpoint()
        entity = stock_delete.capture(planning_session, path.relative_to(root), dbids=tuple(dbids))
        if entity is not None:
            media_rows.append(entity)
    media_deletes = tuple(media_rows)
    phase_progress('Checking songs to delete', len(deleted_dbids), len(deleted_dbids), '')

    track_infos = [_existing_track_info(raw) for raw in retained_raw_tracks]
    regular = [_playlist_info(raw, track_id_to_dbid) for raw in remaining_raw]
    filtered_smart_raw: list[dict] = []
    for raw in remaining_smart_raw:
        checkpoint()
        filtered = dict(raw)
        filtered["items"] = [
            item for item in (raw.get("items") or [])
            if int(item.get("trackID") or 0) in retained_track_ids
        ]
        filtered_smart_raw.append(filtered)
    smart = [_playlist_info(raw, track_id_to_dbid) for raw in filtered_smart_raw]
    master_raw = next(
        (item for item in (parsed.get("mhlp") or []) if item.get("isMaster")),
        None,
    )
    if master_raw is None:
        raise StockIpodError("iTunesDB is missing its master playlist")
    capabilities = stock_renderer_capabilities(write_format)
    if capabilities is None or int(capabilities.checksum) != write_format.checksum_scheme:
        raise StockIpodError("The observed stock database format is unavailable or unsafe")

    remaining_has_art = any(int(raw.get("mhiiLink") or 0) for raw in retained_raw_tracks)
    deleted_has_art = any(
        int(raw_by_dbid[dbid].get("mhiiLink") or 0) for dbid in deleted_dbids)
    rendered_artwork: RenderedStockArtwork | None = None
    if remaining_has_art and deleted_dbids:
        checkpoint()
        phase_progress('Preparing retained artwork', 0, 1, '')
        if not write_format.supports_artwork:
            raise StockIpodError(write_format.artwork_reason)
        if stock_art_cache is None:
            raise StockIpodError(
                "Existing artwork requires the stock artwork cache for safe compaction")
        rendered_artwork = render_stock_artwork(
            track_infos,
            {},
            cache=stock_art_cache,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
        )
    managed_artwork_paths = _native_artwork_paths(captured, write_format, stock_art_cache)
    artwork_delete_paths = tuple(
        relative for relative in managed_artwork_paths
        if deleted_has_art and rendered_artwork is None and (root / relative).is_file()
    )
    artwork_output_paths = (
        (
            Path("iPod_Control") / "Artwork" / "ArtworkDB",
            *(item.relative_path for item in rendered_artwork.ithmb_files),
        )
        if rendered_artwork is not None else ()
    )
    artwork_before = tuple(
        StockArtworkBefore(relative, (root / relative).stat().st_size, _sha256(root / relative))
        for relative in dict.fromkeys(artwork_output_paths)
        if (root / relative).is_file()
    )
    artwork_delete_entities = []
    for relative in artwork_delete_paths:
        checkpoint()
        entity = stock_delete.capture(planning_session, relative)
        if entity is not None:
            artwork_delete_entities.append(entity)

    checkpoint()
    phase_progress('Preparing iPod playlists', 0, 1, '')
    rendered, check = _render_database(
        original,
        track_infos,
        regular,
        smart,
        remaining_smart_raw,
        str(master_raw.get("Title") or "iPod"),
        capabilities,
        write_format=write_format,
    )
    output_tracks = get_ipod_tracks(check)
    output_by_dbid = {int(raw.get("dbid") or 0): raw for raw in output_tracks}
    if len(output_tracks) != len(retained_raw_tracks):
        raise StockIpodError("Rendered deletion track count failed validation")
    if deleted_dbids & set(output_by_dbid):
        raise StockIpodError("Rendered database retained an orphaned track")
    for raw in retained_raw_tracks:
        checkpoint()
        out = output_by_dbid.get(int(raw.get("dbid") or 0))
        if out is None:
            raise StockIpodError("Rendered database lost a retained track")
        for key in (
            "location", "playCount", "skipCount", "rating", "lastPlayed",
            "lastSkipped", "bookmarkTime", "size", "length", "sampleRate",
            "mhiiLink", "artworkCount", "artworkSize",
        ):
            if out.get(key) != raw.get(key):
                raise StockIpodError(
                    f"Rendered deletion changed retained {key} state")
    output_by_track_id = {
        int(raw.get("trackID") or 0): int(raw.get("dbid") or 0)
        for raw in output_tracks
    }
    expected_regular = {
        int(raw.get("playlistID") or 0): (
            str(raw.get("Title") or ""),
            [track_id_to_dbid[int(item.get("trackID") or 0)]
             for item in (raw.get("items") or [])],
        )
        for raw in remaining_raw
    }
    actual_regular = {
        int(raw.get("playlistID") or 0): (
            str(raw.get("Title") or ""),
            [output_by_track_id.get(int(item.get("trackID") or 0), 0)
             for item in (raw.get("items") or [])],
        )
        for raw in (check.get("mhlp") or []) if not raw.get("isMaster")
    }
    if actual_regular != expected_regular:
        raise StockIpodError("Rendered remaining playlists failed validation")
    output_smart = list(check.get("mhlp_smart") or [])
    if len(output_smart) != len(remaining_smart_raw):
        raise StockIpodError("Rendered database changed retained smart playlists")
    for before, after in zip(remaining_smart_raw, output_smart, strict=True):
        for key in ("Title", "mhsd5Type", "smartPlaylistData", "smartPlaylistRules"):
            if after.get(key) != before.get(key):
                raise StockIpodError(
                    f"Rendered smart playlist changed {key} for "
                    f"{before.get('Title')!r}")

    shadow_before = shadow_store.load(identity_key) if shadow_store is not None and identity_key else None
    shadow_ithmb_before = None
    if shadow_before is not None:
        try:
            shadow_ithmb_before = _stock_ithmb_tokens(planning_session, captured.artwork_database)
        except (OSError, RuntimeError, ValueError):
            pass  # Optional acceleration cannot make native-only deletion unavailable.
    catalog = rebase_export_witness(catalog_witness, device_key=identity_key,
        previous_database=original, previous_artwork_sha256=captured.artwork_database_sha256,
        rendered=rendered,
        artwork_sha256=(hashlib.sha256(rendered_artwork.artwork_db).hexdigest()
            if rendered_artwork is not None else ''
            if Path('iPod_Control/Artwork/ArtworkDB') in artwork_delete_paths
            else captured.artwork_database_sha256),
        preserved_artwork_dbids=frozenset(dbid for dbid, _image_id, _song_id in
            rendered_artwork.preserved_links) if rendered_artwork is not None else frozenset())
    planning_session.revalidate()
    checkpoint()
    phase_progress('Preparing iPod playlists', 1, 1, '')
    return StockIpodDeletePlan(
        target=root,
        target_mount_identity=planning_session.mount_identity,
        model_number=model,
        database_relative_path=database.relative_to(root),
        database_sha256_before=hashlib.sha256(original).hexdigest(),
        play_counts_sha256_before=play_counts_hash,
        rendered_database=rendered,
        rendered_artwork=rendered_artwork,
        artwork_before=artwork_before,
        artwork_delete_paths=artwork_delete_paths,
        media_deletes=media_deletes,
        artwork_delete_entities=tuple(artwork_delete_entities),
        playlist_ids=requested_ids,
        playlist_names=tuple(str(raw.get("Title") or "") for raw in selected_raw),
        deleted_track_count=len(deleted_dbids),
        existing_track_count=len(parsed_tracks),
        final_track_count=len(retained_raw_tracks),
        remaining_playlist_count=sum(
            1 for raw in remaining_raw if not raw.get("isSmartPlaylist")),
        merged_play_count_entries=merged_entries,
        dual_boot=dual_boot,
        repair_empty_playlist_index=repair_empty_playlist_index,
        catalog_witness=catalog.witness,
        catalog_notice=catalog.notice,
        catalog_warning=catalog.warning,
        shadow_before=shadow_before,
        shadow_store=shadow_store,
        shadow_ithmb_before=shadow_ithmb_before,
        catalog_witness_before=catalog_witness,
        artwork_database_sha256_before=captured.artwork_database_sha256,
        identity_key=identity_key,
        serial_number=serial_number,
    )


def _verify_indexed_delete_bindings(plan: StockIpodDeletePlan, original: bytes) -> None:
    """A filesystem witness cannot authorize deleting another native entity."""
    checkpoint()
    old_rows = get_ipod_tracks(parse_itunesdb(io.BytesIO(original)))
    new_rows = get_ipod_tracks(parse_itunesdb(io.BytesIO(plan.rendered_database)))
    old = {int(row.get('dbid') or 0): row for row in old_rows}
    kept = {int(row.get('dbid') or 0) for row in new_rows}
    if len(old) != len(old_rows) or len(kept) != len(new_rows):
        raise StockIpodError('Native deletion identities are ambiguous')
    retained_paths = set()
    for row in new_rows:
        checkpoint()
        path = _location_path(plan.target, str(row.get('location') or ''), check_parent=False)
        if path is not None:
            retained_paths.add(stock_delete.path_key(path.relative_to(plan.target)))
    for item in plan.media_deletes:
        checkpoint()
        key = stock_delete.path_key(item.relative_path)
        if (not item.dbids or not item.relative_path.is_relative_to(Path('iPod_Control/Music'))
                or key in retained_paths):
            raise StockIpodError('Deletion file is not an unreferenced native song')
        for dbid in item.dbids:
            row = old.get(dbid)
            path = _location_path(plan.target, str((row or {}).get('location') or ''), check_parent=False)
            if dbid in kept or path is None or stock_delete.path_key(path.relative_to(plan.target)) != key:
                raise StockIpodError('Deletion file lost its exact native song binding')
    if plan.artwork_delete_entities and any(
            int(row.get('mhiiLink') or 0) or int(row.get('artworkCount') or 0) for row in new_rows):
        raise StockIpodError('Retained native songs still reference artwork')


@dataclass(frozen=True)
class _DeletedBaselines:
    witness: StockCatalogWitness | None = None
    warning: str = ''
    notice: str = ''
    shadow_updated: bool = False
    shadow_warning: str = ''


@cancellation_disabled()
def _finish_deleted_baselines(plan: StockIpodDeletePlan, session: DeviceSession,
                              previous_database: bytes) -> _DeletedBaselines:
    """Renew only proven optional Mac caches after the native delete committed."""
    captured = None
    witness = None
    warning = ''
    if plan.catalog_witness is not None or plan.shadow_before is not None and plan.catalog_witness_before is not None:
        try:
            session.revalidate()
            captured = capture_stock_ipod_native(plan.target, include_artwork=True)
            captured.require_target(session.target)
            session.revalidate()
            if captured.database_sha256 != hashlib.sha256(plan.rendered_database).hexdigest():
                raise StockIpodError('Deleted iPod catalog changed before its comparison readback')
            witness, warning = committed_stock_export_witness(plan, session, native_capture=captured)
        except Exception as exc:
            captured = None
            warning = f'Deletion saved; its optional comparison baseline could not be verified: {exc}'
    shadow_updated = False
    shadow_warning = ''
    notice = ''
    if plan.shadow_store is not None and plan.shadow_before is not None:
        renewed = None
        try:
            if captured is not None and plan.shadow_ithmb_before is not None:
                updates = {dbid:(slot.image_id, slot.source_size,
                                {str(key):value for key,value in slot.offsets})
                    for slot in (plan.rendered_artwork.slots if plan.rendered_artwork else ())
                    for dbid in slot.track_dbids or (slot.song_id,)}
                renewed = rebase_deleted_shadow(plan.shadow_before,
                    identity_key=plan.identity_key, model=plan.model_number, serial_number=plan.serial_number,
                    previous_database=previous_database,
                    previous_artwork_sha256=plan.artwork_database_sha256_before,
                    previous_ithmb=plan.shadow_ithmb_before, rendered=captured.database,
                    artwork_sha256=captured.artwork_database_sha256,
                    ithmb=_stock_ithmb_tokens(session, captured.artwork_database),
                    previous_witness=plan.catalog_witness_before,
                    deleted_playlist_ids=plan.playlist_ids, artwork_updates=updates)
            shadow_updated = plan.shadow_store.replace_if_current(plan.shadow_before, renewed)
            if not shadow_updated:
                shadow_warning = 'The optional fast Sync baseline changed during deletion; it was left untouched.'
            elif renewed is None:
                notice = 'The previous optional fast Sync baseline was retired after deletion.'
        except Exception as exc:
            shadow_warning = f'Deletion saved; its optional fast Sync baseline could not be updated: {exc}'
    return _DeletedBaselines(witness, warning, notice, shadow_updated, shadow_warning)


def commit_stock_ipod_playlist_delete(
    plan: StockIpodDeletePlan,
) -> StockIpodDeleteResult:
    """Commit native records first, then retire exact confirmed file entities."""
    checkpoint()
    session = DeviceSession(plan.target, [], create=False)
    if session.mount_identity != plan.target_mount_identity:
        raise StockIpodError(
            "iPod target was remounted after dry-run; make a fresh plan")
    if any(not isinstance(item, stock_delete.IndexedFile)
           for item in (*plan.media_deletes, *plan.artwork_delete_entities)):
        raise StockIpodError('A current native indexed-file deletion plan is required')
    if {item.relative_path for item in plan.artwork_delete_entities} != set(plan.artwork_delete_paths):
        raise StockIpodError('Artwork deletion lost its captured file identities')
    database = session.guard(plan.database_relative_path)
    play_counts = session.guard(database.with_name("Play Counts"))
    phase_progress('Checking iPod deletion', 0, len(plan.media_deletes), '')
    original_database = database.read_bytes()
    if hashlib.sha256(original_database).hexdigest() != plan.database_sha256_before:
        raise StockIpodError("iTunesDB changed after dry-run; make a fresh plan")
    _verify_indexed_delete_bindings(plan, original_database)
    original_play_counts = play_counts.read_bytes() if play_counts.is_file() else None
    if plan.play_counts_sha256_before is None:
        if play_counts.exists():
            raise StockIpodError("Play Counts appeared after dry-run; make a fresh plan")
    elif original_play_counts is None or hashlib.sha256(
            original_play_counts).hexdigest() != plan.play_counts_sha256_before:
        raise StockIpodError("Play Counts changed after dry-run; make a fresh plan")
    for position, item in enumerate((*plan.media_deletes, *plan.artwork_delete_entities)):
        checkpoint()
        stock_delete.verify(session, item)
        phase_progress('Checking iPod deletion', position + 1,
                       len(plan.media_deletes) + len(plan.artwork_delete_entities), str(item.relative_path))

    before_by_path = {item.relative_path: item for item in plan.artwork_before}
    for relative, before in before_by_path.items():
        checkpoint()
        live = session.guard(relative)
        if (not live.is_file() or live.stat().st_size != before.size
                or _sha256(live) != before.sha256):
            raise StockIpodError(f"Artwork changed after dry-run: {relative}")
    if plan.rendered_artwork is not None:
        output_paths = (Path("iPod_Control/Artwork/ArtworkDB"),
                        *(item.relative_path for item in plan.rendered_artwork.ithmb_files))
        for relative in output_paths:
            checkpoint()
            if relative not in before_by_path and session.guard(relative).exists():
                raise StockIpodError(f"Artwork file appeared after dry-run: {relative}")
        for ithmb in plan.rendered_artwork.ithmb_files:
            digest = hashlib.sha256()
            size = 0
            for segment in ithmb.segments:
                checkpoint()
                if (not segment.path.is_file() or segment.path.stat().st_size != segment.size
                        or _sha256(segment.path) != segment.sha256):
                    raise StockIpodError(f"Prepared artwork changed after dry-run: {segment.path}")
                size += segment.size
                with segment.path.open("rb") as handle:
                    while True:
                        checkpoint()
                        chunk = handle.read(1024 * 1024)
                        checkpoint()
                        if not chunk:
                            break
                        digest.update(chunk)
            if size != ithmb.size or digest.hexdigest() != ithmb.sha256:
                raise StockIpodError(f"Prepared artwork layout changed: {ithmb.relative_path}")

    checkpoint()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    recovery = Path('.soundbox/stock-ipod-recovery') / (
        f'{stamp}-delete-{plan.database_sha256_before[:12]}-{secrets.token_hex(8)}')
    try:
        info = os.statvfs(plan.target)
        free = info.f_bavail * info.f_frsize
    except OSError as exc:
        raise StockIpodError(f"Could not verify free space on {plan.target}: {exc}") from exc
    recovery_bytes = len(original_database) + len(plan.rendered_database)
    recovery_bytes += sum(item.size for item in plan.artwork_before)
    if original_play_counts is not None:
        recovery_bytes += len(original_play_counts) * 2
    required = plan.artwork_bytes_to_copy + recovery_bytes + 1024 * 1024
    if required > free:
        raise StockIpodError(f'Not enough free space for the recoverable deletion: needs {required} bytes, has {free} bytes.')
    recovery_abs = session.guard(recovery)
    session.atomic_write(recovery / 'iTunesDB.before', original_database)
    if original_play_counts is not None:
        session.atomic_write(recovery / 'Play Counts.before', original_play_counts)
    for artwork_item in plan.artwork_before:
        checkpoint()
        session.atomic_copy(session.guard(artwork_item.relative_path),
                            session.guard(recovery / 'artwork' / (artwork_item.relative_path.name + '.before')))
    candidates = [('media', item) for item in plan.media_deletes]
    candidates.extend(('artwork', item) for item in plan.artwork_delete_entities)
    claims = [recovery / 'claimed' / f'{position:08d}.pending'
              for position in range(len(candidates))]
    journal = {**plan.summary(), 'transaction_state': 'executing',
               'cleanup_profile': 'native-indexed-entity-delete-v1',
               'delete_candidates': [dict(kind=kind, path=str(item.relative_path),
                    claim=str(claim), epoch=item.epoch, parent_identity=item.parent_identity,
                    dbids=item.dbids)
                    for (kind, item), claim in zip(candidates, claims, strict=True)]}
    session.atomic_write(recovery / 'plan.json',
                         json.dumps(journal, indent=2, sort_keys=True).encode() + b'\n')

    artwork_committed = False
    database_committed = False
    try:
        if plan.rendered_artwork is not None:
            phase_progress('Saving retained artwork', 0, len(plan.rendered_artwork.ithmb_files), '')
            for position, ithmb in enumerate(plan.rendered_artwork.ithmb_files):
                checkpoint()
                destination = session.guard(ithmb.relative_path)
                session.atomic_concat([part.path for part in ithmb.segments], destination)
                if destination.stat().st_size != ithmb.size or _sha256(destination) != ithmb.sha256:
                    raise StockIpodError(f'Committed artwork failed verification: {destination}')
                phase_progress('Saving retained artwork', position + 1,
                               len(plan.rendered_artwork.ithmb_files), '')
            artwork_db = session.guard(Path('iPod_Control/Artwork/ArtworkDB'))
            session.atomic_write(artwork_db, plan.rendered_artwork.artwork_db)
            artwork_committed = True
        checkpoint()
        phase_progress('Saving iPod playlists', 0, 1, '')
        checkpoint()
        with cancellation_disabled():
            session.atomic_write(database, plan.rendered_database)
            database_committed = True
            if original_play_counts is not None:
                consumed = session.guard(recovery / 'Play Counts.consumed')
                consumed.parent.mkdir(parents=True, exist_ok=True)
                session.revalidate()
                os.replace(play_counts, consumed)
            session.revalidate()
        phase_progress('Saving iPod playlists', 1, 1, '')
    except Exception as original_exc:
        with cancellation_disabled():
            rollback_failures = []
            if database_committed:
                try:
                    session.atomic_write(database, original_database)
                except Exception as rollback_exc:
                    rollback_failures.append(f'iTunesDB: {rollback_exc}')
                if original_play_counts is not None and not play_counts.exists():
                    try:
                        session.atomic_write(play_counts, original_play_counts)
                    except Exception as rollback_exc:
                        rollback_failures.append(f'Play Counts: {rollback_exc}')
            if artwork_committed or plan.rendered_artwork is not None:
                paths = (Path('iPod_Control/Artwork/ArtworkDB'),
                         *(item.relative_path for item in (plan.rendered_artwork.ithmb_files
                             if plan.rendered_artwork is not None else ())))
                for relative in paths:
                    destination = session.guard(relative)
                    rollback_before = before_by_path.get(relative)
                    try:
                        if rollback_before is None:
                            destination.unlink(missing_ok=True)
                        else:
                            saved = session.guard(recovery / 'artwork' / (relative.name + '.before'))
                            session.atomic_copy(saved, destination)
                    except Exception as rollback_exc:
                        rollback_failures.append(f'{relative}: {rollback_exc}')
            record_outcome(session, recovery, 'recovery required' if rollback_failures else 'rolled back',
                           error=str(original_exc))
            setattr(original_exc, 'recovery_status', 'recovery required' if rollback_failures else 'rolled back')
            if rollback_failures:
                failure = StockIpodError('Stock deletion failed and exact rollback was incomplete; '
                    f'recovery is at {recovery_abs}. Failures: {"; ".join(rollback_failures)}')
                failure.recovery_status = 'recovery required'
                raise failure from original_exc
        raise

    failures: list[str] = []
    deleted_files = 0
    deleted_bytes = 0
    cleanup_cancelled = False
    for position, ((kind, item), claim) in enumerate(zip(candidates, claims, strict=True)):
        try:
            checkpoint()
            phase_progress('Deleting iPod files', position, len(candidates), str(item.relative_path))
            checkpoint()
        except OperationCancelled:
            cleanup_cancelled = True
            failures.extend(f'Cleanup cancelled; retained {remaining.relative_path}'
                            for _kind, remaining in candidates[position:])
            break
        try:
            stock_delete.retire(session, item, claim)
            if kind == 'media':
                deleted_files += 1
                deleted_bytes += item.size
        except Exception as error:
            failures.append(f'{item.relative_path}: {error}')
        phase_progress('Deleting iPod files', position + 1, len(candidates), str(item.relative_path))
    with cancellation_disabled():
        record_outcome(session, recovery, 'committed',
                       error='\n'.join(failures))
        baselines = _finish_deleted_baselines(plan, session, original_database)
    return StockIpodDeleteResult(
        recovery_directory=recovery_abs, playlists=plan.playlist_names,
        deleted_track_records=plan.deleted_track_count, deleted_media_files=deleted_files,
        deleted_bytes=deleted_bytes, remaining_tracks=plan.final_track_count,
        remaining_playlists=plan.remaining_playlist_count,
        database_sha256=hashlib.sha256(plan.rendered_database).hexdigest(),
        cleanup_failures=tuple(failures), cleanup_cancelled=cleanup_cancelled,
        catalog_witness=baselines.witness,
        catalog_notice=' '.join(value for value in (plan.catalog_notice, baselines.notice) if value),
        catalog_warning=' '.join(value for value in (plan.catalog_warning, baselines.warning) if value),
        shadow_updated=baselines.shadow_updated, shadow_warning=baselines.shadow_warning)
