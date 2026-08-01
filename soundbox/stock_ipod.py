"""Guarded stock-iPod database planning and commit support.

The third-party iTunesDB implementation is deliberately treated as a renderer,
not as SoundBox's sync engine.  It only sees a temporary staging tree.  All
live-device reads, source validation, media placement, recovery copies, pinned
mount checks, and atomic writes remain owned by SoundBox.

The first write-capable hardware boundary is intentionally narrow:

* iPod Video 5.5G model MA446 (the physically validated device)
* an Apple ``iTunesDB`` with checksum scheme 0
* MP3 and WAV audio
* additive media/library sync; selected same-name playlists are replaced

Unknown models, compressed databases, corrupt play-count deltas, missing
sources, unsupported formats, and changing devices fail closed before a write.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import mutagen
from mutagen.mp3 import MP3
import soundfile as sf

from clickwheel.ipod import (  # type: ignore[import-untyped]
    get_ipod_tracks,
    merge_playcounts,
    parse_itunesdb,
    read_play_counts,
)
from clickwheel.ipod.ipod_models import capabilities_for_family_gen  # type: ignore[import-untyped]
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
from .library import Library, Playlist, Track
from .paths import is_within
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
)
from .sync import DeviceSession, assert_target_safe


SUPPORTED_SUFFIXES = frozenset({".mp3", ".wav"})
SUPPORTED_MODELS = {
    "MA446": ("iPod Video", "5.5th Gen"),
}


class StockIpodError(RuntimeError):
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


@dataclass(frozen=True)
class StockIpodSyncPlan:
    """Fully rendered dry-run result; constructing it never writes the target."""

    target: Path
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
    playlist_names: tuple[str, ...] = ()
    source_track_count: int = 0
    reused_track_count: int = 0
    existing_track_count: int = 0
    final_track_count: int = 0
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

    @property
    def bytes_to_copy(self) -> int:
        return sum(item.size for item in self.copies)

    @property
    def artwork_bytes_to_copy(self) -> int:
        return self.rendered_artwork.bytes_to_copy if self.rendered_artwork else 0

    @property
    def database_changed(self) -> bool:
        return hashlib.sha256(
            self.rendered_database).hexdigest() != self.database_sha256_before

    @property
    def requires_commit(self) -> bool:
        return bool(
            self.copies
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
            "new_tracks": len(self.copies),
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
        }


@dataclass(frozen=True)
class StockIpodCommitResult:
    recovery_directory: Path
    copied_tracks: int
    reused_tracks: int
    playlists: tuple[str, ...]
    database_sha256: str
    artwork_tracks: int = 0
    unique_artwork: int = 0
    metadata_updates: int = 0
    artwork_pixel_bytes: int = 0
    shadow_updated: bool = False
    shadow_warning: str = ""


@dataclass(frozen=True)
class StockIpodDeletePlan:
    """Read-only playlist deletion and reference-counted media cleanup plan."""

    target: Path
    model_number: str
    database_relative_path: Path
    database_sha256_before: str
    play_counts_sha256_before: str | None
    rendered_database: bytes = field(repr=False)
    rendered_artwork: RenderedStockArtwork | None = field(default=None, repr=False)
    artwork_before: tuple[StockArtworkBefore, ...] = ()
    artwork_delete_paths: tuple[Path, ...] = ()
    media_deletes: tuple[StockMediaDelete, ...] = ()
    playlist_ids: tuple[int, ...] = ()
    playlist_names: tuple[str, ...] = ()
    deleted_track_count: int = 0
    existing_track_count: int = 0
    final_track_count: int = 0
    remaining_playlist_count: int = 0
    merged_play_count_entries: int = 0

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
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_path(root: Path) -> Path:
    itunes = root / "iPod_Control" / "iTunes"
    cdb = itunes / "iTunesCDB"
    db = itunes / "iTunesDB"
    if cdb.is_file() and cdb.stat().st_size:
        raise StockIpodError(
            "This iPod uses iTunesCDB; the first SoundBox stock writer only "
            "accepts the physically validated MA446 iTunesDB layout.")
    if not db.is_file() or not db.stat().st_size:
        raise StockIpodError(f"No non-empty iTunesDB found at {db}")
    return db


def _location_path(root: Path, location: str) -> Path | None:
    parts = [part for part in str(location or "").strip(":").split(":") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    candidate = root.joinpath(*parts)
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
        return (
            f"{info.subtype or 'unknown'} / {info.samplerate} Hz / "
            f"{info.channels} ch WAV; validated stock boundary is "
            "PCM 16-bit / 44.1 kHz / stereo"
        )
    return f"{suffix.lstrip('.').upper() or 'unknown'} is not a stock-iPod MP3/WAV"


def _effective_track_metadata(
    track: Track,
    source: Path,
    *,
    derive_artists: bool,
    artist_overrides: dict | None,
    title_overrides: dict | None,
    library: Library,
) -> tuple[str, str]:
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
) -> TrackInfo:
    duration, bitrate, sample_rate = _technical_audio(source)
    title, artist = _effective_track_metadata(
        track,
        source,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
        library=library,
    )
    suffix = source.suffix.lower()
    stat = source.stat()
    return TrackInfo(
        title=title,
        location=location,
        size=stat.st_size,
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
) -> StockMetadataUpdate | None:
    """Update only human-facing metadata on one selected reused record.

    The existing TrackInfo supplies every identity, location, technical, rating,
    bookmark, and playback field.  This narrow enrichment deliberately changes
    only title and artist, and only when SoundBox has non-empty effective values.
    """
    title, artist = _effective_track_metadata(
        track,
        source,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
        library=library,
    )
    title_before = str(raw.get("title") or "")
    artist_before = str(raw.get("artist") or "")
    title_after = title.strip() or title_before
    artist_after = artist.strip() or artist_before
    if title_after == title_before and artist_after == artist_before:
        return None
    info.title = title_after
    info.artist = artist_after or None
    return StockMetadataUpdate(
        dbid=int(raw.get("dbid") or 0),
        title_before=title_before,
        title_after=title_after,
        artist_before=artist_before,
        artist_after=artist_after,
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


def _render_database(
    original: bytes,
    track_infos: list[TrackInfo],
    regular: list[PlaylistInfo],
    smart: list[PlaylistInfo],
    smart_raw: list[dict],
    master_name: str,
    capabilities: Any,
) -> bytes:
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
        source.write_bytes(rendered)
        # A second parse proves that the final, patched bytes are structurally
        # readable before they are ever eligible for a device commit.
        parse_itunesdb(str(source))
    return rendered


def _shadow_matches_live(
    shadow: StockIpodShadow | None,
    *,
    root: Path,
    identity_key: str,
    model: str,
    serial_number: str,
    database_sha256: str,
) -> tuple[bool, str]:
    if shadow is None:
        return False, "No committed Mac-side baseline"
    if shadow.identity_key != identity_key or shadow.model != model:
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
    if shadow.artwork_database_sha256:
        if not artwork_db.is_file() or _sha256(artwork_db) != shadow.artwork_database_sha256:
            return False, "ArtworkDB changed since the last SoundBox commit"
    elif artwork_db.exists():
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
) -> StockIpodSyncStatus:
    """Compare one mounted iPod with its Mac shadow without writing either."""
    root = Path(target).expanduser().resolve(strict=True)
    shadow = shadow_store.load(identity_key)
    if shadow is None:
        return StockIpodSyncStatus(
            "untracked", "No committed Mac-side fast-sync baseline")
    database = _db_path(root)
    matches, reason = _shadow_matches_live(
        shadow,
        root=root,
        identity_key=identity_key,
        model=normalize_model_number(model_number),
        serial_number=serial_number,
        database_sha256=_sha256(database),
    )
    if not matches:
        return StockIpodSyncStatus(
            "reconcile_required", reason, committed_at=shadow.committed_at)

    changed: set[str] = set()
    drift = device_manifest.diff(
        {"playlists": shadow.playlists}, library, extra=extra_playlists)
    for rows in (drift.renamed, drift.moved, drift.changed, drift.removed):
        changed.update(str(row[0]) for row in rows)
    for source_text, row in shadow.sources.items():
        track_id = str(row.get("track_id") or "")
        track = library.tracks_by_id.get(track_id)
        source = track.path if track is not None else Path(source_text)
        try:
            stat = source.resolve(strict=True).stat()
        except OSError:
            changed.add(f"source:{track_id or source_text}")
            continue
        if (
            stat.st_size != int(row.get("size") or -1)
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
    pending = database.with_name("Play Counts").is_file()
    if pending:
        changed.add("play-counts")
    if changed:
        return StockIpodSyncStatus(
            "local_changes",
            f"{len(changed)} local or playback change(s) ready to plan",
            len(changed),
            shadow.committed_at,
        )
    return StockIpodSyncStatus(
        "up_to_date", "Matches the last committed Mac-side baseline",
        0, shadow.committed_at)


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
) -> StockIpodSyncPlan:
    """Render and validate a complete stock-iPod sync without target writes."""
    root = Path(target).expanduser().resolve(strict=True)
    if str(filesystem or "").lower() not in {"msdos", "fat32", "fat"}:
        raise StockIpodError(
            f"Stock iPod writes require an explicitly confirmed FAT32 volume; "
            f"got {filesystem or 'unknown'}.")
    if (root / ".rockbox").exists():
        raise StockIpodError(
            "Dual-boot stock databases remain read-only; use the Rockbox export path.")
    model = normalize_model_number(model_number)
    model_tuple = SUPPORTED_MODELS.get(model)
    if model_tuple is None:
        raise StockIpodError(
            f"Stock writes are not validated for model {model_number!r}; "
            "the initial allowlist contains MA446 only.")
    if not selected:
        raise StockIpodError("Select at least one playlist for stock-iPod sync")
    names = [playlist.display_path for playlist in selected]
    if len(set(names)) != len(names):
        raise StockIpodError("Selected stock-iPod playlist names must be unique")

    resolved: list[tuple[Track, Path]] = []
    omissions: list[StockMediaOmission] = []
    missing: list[Path] = []
    for playlist in selected:
        for track in library.resolve(playlist):
            source = track.path
            try:
                exists = source.is_file()
            except OSError:
                exists = False
            if not exists:
                missing.append(source)
            else:
                source = source.resolve(strict=True)
                omission = _stock_media_omission(source)
                if omission:
                    omissions.append(StockMediaOmission(
                        track.track_id, track.name, source, omission))
                else:
                    resolved.append((track, source))
    if missing:
        raise StockIpodError(
            f"{len(missing)} selected source track(s) are missing; first: {missing[0]}")
    sources = list(dict.fromkeys(path for _track, path in resolved))
    assert_target_safe(root, sources)

    database = _db_path(root)
    original = database.read_bytes()
    if len(original) < 0x32 or original[:4] != b"mhbd":
        raise StockIpodError("iTunesDB has an invalid header")
    hashing_scheme = struct.unpack_from("<H", original, 0x30)[0]
    if hashing_scheme != 0:
        raise StockIpodError(
            f"MA446 database unexpectedly requires checksum scheme {hashing_scheme}")

    database_sha256 = hashlib.sha256(original).hexdigest()
    shadow = shadow_store.load(identity_key) if shadow_store and identity_key else None
    shadow_fast_path, shadow_reason = _shadow_matches_live(
        shadow,
        root=root,
        identity_key=identity_key,
        model=model,
        serial_number=serial_number,
        database_sha256=database_sha256,
    )
    if not shadow_fast_path:
        shadow = None

    parsed = parse_itunesdb(str(database))
    parsed_tracks = get_ipod_tracks(parsed)
    play_counts_path = database.with_name("Play Counts")
    play_counts_hash: str | None = None
    merged_entries = 0
    if play_counts_path.exists():
        play_counts_hash = _sha256(play_counts_path)
        entries = read_play_counts(root)
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
    music = root / "iPod_Control" / "Music"
    if music.is_dir():
        for path in music.glob("F*/*"):
            if path.is_file():
                occupied_paths.add(str(path.relative_to(root)).replace(
                    os.sep, "/").casefold())

    source_hashes = _source_hashes_with_shadow(sources, shadow)
    desired_content_keys = {
        (path.stat().st_size, path.suffix.lower(), source_hashes[path])
        for path in sources
    }
    existing_by_content: dict[tuple[int, str, str], int] = {}
    wanted_shapes = {
        (path.stat().st_size, path.suffix.lower()) for path in sources}
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
    copies: list[StockMediaCopy] = []
    new_infos: list[TrackInfo] = []
    first_track_for_source: dict[Path, Track] = {}
    for track, source in resolved:
        first_track_for_source.setdefault(source, track)
    for source in sources:
        content_key = (
            source.stat().st_size, source.suffix.lower(), source_hashes[source])
        planned_dbid = content_to_dbid.get(content_key)
        if planned_dbid is None:
            planned_dbid = _new_dbid(occupied_dbids)
            relative, location = _allocate_location(
                root, source_hashes[source], source.suffix.lower(), occupied_paths)
            copies.append(StockMediaCopy(
                source=source,
                relative_path=relative,
                location=location,
                size=source.stat().st_size,
                sha256=source_hashes[source],
                dbid=planned_dbid,
            ))
            new_infos.append(_new_track_info(
                first_track_for_source[source], source, location, planned_dbid,
                derive_artists=derive_artists,
                artist_overrides=artist_overrides,
                title_overrides=title_overrides,
                library=library,
            ))
            content_to_dbid[content_key] = planned_dbid
        source_to_dbid[source] = planned_dbid

    playlist_sequences: dict[str, list[int]] = {name: [] for name in names}
    for playlist, name in zip(selected, names, strict=True):
        for track in library.resolve(playlist):
            source = track.path
            if source.is_file():
                resolved_source = source.resolve(strict=True)
                if resolved_source in source_to_dbid:
                    playlist_sequences[name].append(
                        source_to_dbid[resolved_source])

    raw_regular = [
        item for item in (parsed.get("mhlp") or [])
        if not item.get("isMaster")
    ]
    regular: list[PlaylistInfo] = []
    replaced: set[str] = set()
    for raw in raw_regular:
        name = str(raw.get("Title") or "")
        if name in playlist_sequences:
            regular.append(PlaylistInfo(
                name=name, track_ids=playlist_sequences[name],
                playlist_id=raw.get("playlistID"), sortorder=1,
                raw_mhod100=_raw_blob(raw, "rawMhod100"),
                raw_mhod102=_raw_blob(raw, "rawMhod102"),
            ))
            replaced.add(name)
        else:
            regular.append(_playlist_info(raw, track_id_to_dbid))
    for name in names:
        if name not in replaced:
            regular.append(PlaylistInfo(
                name=name, track_ids=playlist_sequences[name], sortorder=1))

    smart_raw = list(parsed.get("mhlp_smart") or [])
    smart = [_playlist_info(raw, track_id_to_dbid) for raw in smart_raw]
    master_raw = next(
        (item for item in (parsed.get("mhlp") or []) if item.get("isMaster")),
        None,
    )
    if master_raw is None:
        raise StockIpodError("iTunesDB is missing its master playlist")

    capabilities = capabilities_for_family_gen(*model_tuple)
    if capabilities is None or capabilities.checksum != ChecksumType.NONE:
        raise StockIpodError("The MA446 capability profile is unavailable or unsafe")
    existing_infos = [_existing_track_info(raw) for raw in parsed_tracks]
    existing_info_by_dbid = {
        int(info.dbid): (info, raw)
        for info, raw in zip(existing_infos, parsed_tracks, strict=True)
    }
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
        )
        if update is not None:
            metadata_updates.append(update)
    track_infos = existing_infos + new_infos
    prepared_by_dbid = {}
    if art_cache is not None and stock_art_cache is not None:
        for source, track in first_track_for_source.items():
            artwork_dbid = source_to_dbid.get(source)
            image_path = art_cache.image_path(track.track_id)
            if artwork_dbid and image_path is not None:
                prepared_by_dbid[artwork_dbid] = stock_art_cache.prepare(image_path)
    if stock_art_cache is None:
        rendered_artwork = None
    elif shadow is not None and (root / "iPod_Control/Artwork/ArtworkDB").is_file():
        rendered_artwork = render_incremental_stock_artwork(
            track_infos,
            prepared_by_dbid,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
            known_artwork=shadow.artwork,
        )
    else:
        rendered_artwork = render_stock_artwork(
            track_infos,
            prepared_by_dbid,
            cache=stock_art_cache,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
        )
    rendered = _render_database(
        original, track_infos, regular, smart, smart_raw,
        str(master_raw.get("Title") or "iPod"), capabilities)

    with tempfile.TemporaryDirectory(prefix="soundbox-stock-ipod-validate.") as td:
        staged = Path(td) / "iTunesDB"
        staged.write_bytes(rendered)
        check = parse_itunesdb(str(staged))
    output_tracks = get_ipod_tracks(check)
    output_by_track_id = {
        int(raw.get("trackID") or 0): int(raw.get("dbid") or 0)
        for raw in output_tracks
    }
    output_by_dbid = {int(raw.get("dbid") or 0): raw for raw in output_tracks}
    if len(output_tracks) != len(track_infos):
        raise StockIpodError("Rendered track count failed validation")
    for raw in parsed_tracks:
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
    )
    artwork_changed = bool(
        rendered_artwork and (
            rendered_artwork.publish_artwork_db
            or rendered_artwork.range_writes
        )
    )
    if (
        shadow_fast_path
        and not copies
        and not metadata_updates
        and not playlist_changed
        and not artwork_changed
        and play_counts_hash is None
    ):
        # Clickwheel legitimately regenerates opaque database IDs/headers.  If
        # the shadow proves every desired input is unchanged, retain Apple's
        # exact bytes and make this a true zero-write plan.
        rendered = original

    reused = sum(1 for source in sources if not any(
        item.source == source for item in copies))
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

    final_dbids = {int(info.dbid) for info in track_infos}
    shadow_sources = {
        key: dict(value) for key, value in (shadow.sources if shadow else {}).items()
        if int(value.get("dbid") or 0) in final_dbids
    }
    info_by_dbid = {int(info.dbid): info for info in track_infos}
    for source in sources:
        stat = source.stat()
        dbid = source_to_dbid[source]
        location = str(info_by_dbid[dbid].location or "")
        shadow_sources[str(source)] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": source_hashes[source],
            "suffix": source.suffix.lower(),
            "dbid": dbid,
            "device_path": location.lstrip(":").replace(":", "/"),
            "track_id": first_track_for_source[source].track_id,
        }
    shadow_playlists = {
        key: dict(value) for key, value in (shadow.playlists if shadow else {}).items()
    }
    for playlist in selected:
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
    for slot in (rendered_artwork.slots if rendered_artwork else ()):
        shadow_art_row: dict[str, Any] = dict(
            (shadow.artwork if shadow else {}).get(str(slot.song_id)) or {})
        shadow_art_row.update({
            "image_id": slot.image_id,
            "source_size": slot.source_size,
            "cache_key": slot.cache_key,
            "offsets": {str(key): value for key, value in slot.offsets},
        })
        local_art_track_id = track_id_by_dbid.get(slot.song_id, "")
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
        shadow_artwork[str(slot.song_id)] = shadow_art_row
    if not shadow_artwork and shadow is not None:
        shadow_artwork = {
            key: dict(value) for key, value in shadow.artwork.items()
            if int(key) in final_dbids
        }
    return StockIpodSyncPlan(
        target=root,
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
        playlist_names=tuple(names),
        source_track_count=len(resolved),
        reused_track_count=reused,
        existing_track_count=len(parsed_tracks),
        final_track_count=len(track_infos),
        merged_play_count_entries=merged_entries,
        identity_key=identity_key,
        serial_number=serial_number,
        shadow_fast_path=shadow_fast_path,
        shadow_reconcile_reason=shadow_reason,
        shadow_store=shadow_store,
        shadow_sources=shadow_sources,
        shadow_artwork=shadow_artwork,
        shadow_playlists=shadow_playlists,
    )


def commit_stock_ipod_sync(plan: StockIpodSyncPlan) -> StockIpodCommitResult:
    """Commit a validated plan with DB-last ordering and exact-file rollback."""
    sources = [item.source for item in plan.copies]
    session = DeviceSession(plan.target, sources, create=False)
    database = session.guard(plan.database_relative_path)
    play_counts = session.guard(database.with_name("Play Counts"))
    if _sha256(database) != plan.database_sha256_before:
        raise StockIpodError("iTunesDB changed after dry-run; make a fresh plan")
    if plan.play_counts_sha256_before is None:
        if play_counts.exists():
            raise StockIpodError("Play Counts appeared after dry-run; make a fresh plan")
    elif not play_counts.is_file() or _sha256(
            play_counts) != plan.play_counts_sha256_before:
        raise StockIpodError("Play Counts changed after dry-run; make a fresh plan")
    for item in plan.copies:
        if item.source.stat().st_size != item.size or _sha256(
                item.source) != item.sha256:
            raise StockIpodError(f"Source changed after dry-run: {item.source}")
        destination = session.guard(item.relative_path)
        if destination.exists():
            raise StockIpodError(f"Planned destination is no longer free: {destination}")
    before_by_path = {item.relative_path: item for item in plan.artwork_before}
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
    original_database = database.read_bytes()
    original_play_counts = play_counts.read_bytes() if play_counts.is_file() else None
    try:
        statvfs = os.statvfs(plan.target)
        free = statvfs.f_bavail * statvfs.f_frsize
    except OSError as exc:
        raise StockIpodError(f"Could not verify free space on {plan.target}: {exc}") from exc
    # Peak transaction footprint: new media + recovery copies + staged DB
    # replacement. Keep a small fixed margin for FAT directory allocation.
    recovery_bytes = len(original_database) + len(plan.rendered_database)
    recovery_bytes += sum(item.size for item in plan.artwork_before)
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
    session.atomic_write(recovery / "iTunesDB.before", original_database)
    if original_play_counts is not None:
        session.atomic_write(recovery / "Play Counts.before", original_play_counts)
    for artwork_item in plan.artwork_before:
        source = session.guard(artwork_item.relative_path)
        destination = recovery / "artwork" / (
            artwork_item.relative_path.name + ".before")
        session.atomic_copy(source, session.guard(destination))
    manifest = json.dumps(plan.summary(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
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

    copied: list[Path] = []
    committed = False
    artwork_committed = False
    try:
        for item in plan.copies:
            destination = session.guard(item.relative_path)
            session.atomic_copy(item.source, destination)
            if destination.stat().st_size != item.size or _sha256(
                    destination) != item.sha256:
                raise StockIpodError(f"Copied media failed verification: {destination}")
            copied.append(destination)
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
            if plan.rendered_artwork.publish_artwork_db:
                artwork_db = session.guard(
                    Path("iPod_Control") / "Artwork" / "ArtworkDB")
                session.atomic_write(artwork_db, plan.rendered_artwork.artwork_db)
                if _sha256(artwork_db) != hashlib.sha256(
                        plan.rendered_artwork.artwork_db).hexdigest():
                    raise StockIpodError("Committed ArtworkDB failed verification")
                artwork_committed = True
        session.atomic_write(database, plan.rendered_database)
        committed = True
        if original_play_counts is not None:
            consumed = session.guard(recovery / "Play Counts.consumed")
            consumed.parent.mkdir(parents=True, exist_ok=True)
            session.revalidate()
            os.replace(play_counts, consumed)
        session.revalidate()
    except Exception as original_exc:
        rollback_failures: list[str] = []
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
        if rollback_failures:
            raise StockIpodError(
                "Stock sync failed and exact rollback was incomplete; recovery is at "
                f"{recovery_abs}. Failures: {'; '.join(rollback_failures)}"
            ) from original_exc
        raise

    database_after = hashlib.sha256(plan.rendered_database).hexdigest()
    shadow_updated = False
    shadow_warning = ""
    if plan.shadow_store is not None and plan.identity_key:
        try:
            artwork_dir = plan.target / "iPod_Control" / "Artwork"
            artwork_db = artwork_dir / "ArtworkDB"
            artwork_sha = _sha256(artwork_db) if artwork_db.is_file() else ""
            shadow_ithmb: dict[str, dict[str, int]] = {}
            for format_id in (1028, 1029):
                path = artwork_dir / f"F{format_id}_1.ithmb"
                if path.is_file():
                    stat = path.stat()
                    shadow_ithmb[path.name] = {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
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

    return StockIpodCommitResult(
        recovery_directory=recovery_abs,
        copied_tracks=len(copied),
        reused_tracks=plan.reused_track_count,
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
        shadow_updated=shadow_updated,
        shadow_warning=shadow_warning,
    )


def plan_stock_ipod_playlist_delete(
    target: str | Path,
    playlist_ids: list[int] | tuple[int, ...],
    *,
    model_number: str,
    filesystem: str,
    stock_art_cache: StockArtworkCache | None = None,
) -> StockIpodDeletePlan:
    """Plan deletion of managed playlists and newly orphaned user-list tracks.

    Ownership is defined exclusively by regular user playlists. The master
    library and Apple's smart playlists are derived indexes, so counting either
    would make every track immortal. The only deletable Apple system index is
    the proven-empty/removable Videos smart playlist; every other smart list
    remains immutable. Pre-existing loose tracks are not swept: candidates come
    only from regular playlist(s) explicitly selected for deletion.
    """
    root = Path(target).expanduser().resolve(strict=True)
    if str(filesystem or "").lower() not in {"msdos", "fat32", "fat"}:
        raise StockIpodError(
            f"Stock iPod writes require an explicitly confirmed FAT32 volume; "
            f"got {filesystem or 'unknown'}.")
    if (root / ".rockbox").exists():
        raise StockIpodError(
            "Dual-boot stock databases remain read-only; use the Rockbox path.")
    model = normalize_model_number(model_number)
    model_tuple = SUPPORTED_MODELS.get(model)
    if model_tuple is None:
        raise StockIpodError(
            f"Stock writes are not validated for model {model_number!r}; "
            "the initial allowlist contains MA446 only.")
    requested_ids = tuple(dict.fromkeys(int(value) for value in playlist_ids))
    if not requested_ids:
        raise StockIpodError("Select at least one playlist to delete")

    database = _db_path(root)
    original = database.read_bytes()
    if len(original) < 0x32 or original[:4] != b"mhbd":
        raise StockIpodError("iTunesDB has an invalid header")
    hashing_scheme = struct.unpack_from("<H", original, 0x30)[0]
    if hashing_scheme != 0:
        raise StockIpodError(
            f"MA446 database unexpectedly requires checksum scheme {hashing_scheme}")

    parsed = parse_itunesdb(str(database))
    parsed_tracks = get_ipod_tracks(parsed)
    play_counts_path = database.with_name("Play Counts")
    play_counts_hash: str | None = None
    merged_entries = 0
    if play_counts_path.exists():
        play_counts_hash = _sha256(play_counts_path)
        entries = read_play_counts(root)
        if entries is None or len(entries) != len(parsed_tracks):
            raise StockIpodError(
                "Play Counts is unreadable or does not match the iTunesDB; "
                "refusing to risk playback history.")
        merge_playcounts(parsed_tracks, entries)
        merged_entries = sum(1 for item in entries if item.has_data)

    track_id_to_dbid: dict[int, int] = {}
    raw_by_dbid: dict[int, dict] = {}
    for raw in parsed_tracks:
        track_id = int(raw.get("trackID") or 0)
        dbid = int(raw.get("dbid") or 0)
        if not track_id or not dbid or dbid in raw_by_dbid:
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
            for item in raw_playlist.get("items") or []:
                track_id = int(item.get("trackID") or 0)
                dbid = track_id_to_dbid.get(track_id)
                if dbid is None:
                    raise StockIpodError(
                        f"Playlist {raw_playlist.get('Title')!r} references an "
                        "unknown track")
                refs.add(dbid)
        return refs

    candidates = referenced_dbids(selected_regular_raw)
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
        str(raw.get("location") or "").casefold()
        for raw in retained_raw_tracks if raw.get("location")
    }

    delete_paths: dict[str, tuple[Path, list[int]]] = {}
    music_root = root / "iPod_Control" / "Music"
    for dbid in sorted(deleted_dbids):
        raw = raw_by_dbid[dbid]
        location = str(raw.get("location") or "")
        if not location or location.casefold() in retained_locations:
            continue
        path = _location_path(root, location)
        if path is None or not is_within(path, music_root):
            raise StockIpodError(
                f"Track {dbid} has a media location outside iPod_Control/Music")
        if path.exists() and not path.is_file():
            raise StockIpodError(f"Track {dbid} media is not a regular file: {path}")
        if path.is_file():
            key = str(path.relative_to(root)).replace(os.sep, "/").casefold()
            if key not in delete_paths:
                delete_paths[key] = (path, [])
            delete_paths[key][1].append(dbid)
    media_deletes = tuple(
        StockMediaDelete(
            relative_path=path.relative_to(root),
            size=path.stat().st_size,
            sha256=_sha256(path),
            dbids=tuple(dbids),
        )
        for _key, (path, dbids) in sorted(delete_paths.items())
    )

    track_infos = [_existing_track_info(raw) for raw in retained_raw_tracks]
    regular = [_playlist_info(raw, track_id_to_dbid) for raw in remaining_raw]
    filtered_smart_raw: list[dict] = []
    for raw in remaining_smart_raw:
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
    capabilities = capabilities_for_family_gen(*model_tuple)
    if capabilities is None or capabilities.checksum != ChecksumType.NONE:
        raise StockIpodError("The MA446 capability profile is unavailable or unsafe")

    remaining_has_art = any(int(raw.get("mhiiLink") or 0) for raw in retained_raw_tracks)
    deleted_has_art = any(
        int(raw_by_dbid[dbid].get("mhiiLink") or 0) for dbid in deleted_dbids)
    rendered_artwork: RenderedStockArtwork | None = None
    if remaining_has_art:
        if stock_art_cache is None:
            raise StockIpodError(
                "Existing artwork requires the stock artwork cache for safe compaction")
        rendered_artwork = render_stock_artwork(
            track_infos,
            {},
            cache=stock_art_cache,
            existing_artwork_dir=root / "iPod_Control" / "Artwork",
        )
    managed_artwork_paths = (
        Path("iPod_Control") / "Artwork" / "ArtworkDB",
        Path("iPod_Control") / "Artwork" / "F1028_1.ithmb",
        Path("iPod_Control") / "Artwork" / "F1029_1.ithmb",
    )
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
        for relative in dict.fromkeys((*artwork_output_paths, *artwork_delete_paths))
        if (root / relative).is_file()
    )

    rendered = _render_database(
        original,
        track_infos,
        regular,
        smart,
        remaining_smart_raw,
        str(master_raw.get("Title") or "iPod"),
        capabilities,
    )
    with tempfile.TemporaryDirectory(prefix="soundbox-stock-ipod-delete-validate.") as td:
        staged = Path(td) / "iTunesDB"
        staged.write_bytes(rendered)
        check = parse_itunesdb(str(staged))
    output_tracks = get_ipod_tracks(check)
    output_by_dbid = {int(raw.get("dbid") or 0): raw for raw in output_tracks}
    if len(output_tracks) != len(retained_raw_tracks):
        raise StockIpodError("Rendered deletion track count failed validation")
    if deleted_dbids & set(output_by_dbid):
        raise StockIpodError("Rendered database retained an orphaned track")
    for raw in retained_raw_tracks:
        out = output_by_dbid.get(int(raw.get("dbid") or 0))
        if out is None:
            raise StockIpodError("Rendered database lost a retained track")
        for key in (
            "location", "playCount", "skipCount", "rating", "lastPlayed",
            "lastSkipped", "bookmarkTime", "size", "length", "sampleRate",
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

    return StockIpodDeletePlan(
        target=root,
        model_number=model,
        database_relative_path=database.relative_to(root),
        database_sha256_before=hashlib.sha256(original).hexdigest(),
        play_counts_sha256_before=play_counts_hash,
        rendered_database=rendered,
        rendered_artwork=rendered_artwork,
        artwork_before=artwork_before,
        artwork_delete_paths=artwork_delete_paths,
        media_deletes=media_deletes,
        playlist_ids=requested_ids,
        playlist_names=tuple(str(raw.get("Title") or "") for raw in selected_raw),
        deleted_track_count=len(deleted_dbids),
        existing_track_count=len(parsed_tracks),
        final_track_count=len(retained_raw_tracks),
        remaining_playlist_count=sum(
            1 for raw in remaining_raw if not raw.get("isSmartPlaylist")),
        merged_play_count_entries=merged_entries,
    )


def commit_stock_ipod_playlist_delete(
    plan: StockIpodDeletePlan,
) -> StockIpodDeleteResult:
    """Commit a deletion plan; database consistency precedes file cleanup."""
    session = DeviceSession(plan.target, [], create=False)
    database = session.guard(plan.database_relative_path)
    play_counts = session.guard(database.with_name("Play Counts"))
    if _sha256(database) != plan.database_sha256_before:
        raise StockIpodError("iTunesDB changed after dry-run; make a fresh plan")
    if plan.play_counts_sha256_before is None:
        if play_counts.exists():
            raise StockIpodError("Play Counts appeared after dry-run; make a fresh plan")
    elif not play_counts.is_file() or _sha256(
            play_counts) != plan.play_counts_sha256_before:
        raise StockIpodError("Play Counts changed after dry-run; make a fresh plan")
    for item in plan.media_deletes:
        path = session.guard(item.relative_path)
        if (
            not path.is_file()
            or path.stat().st_size != item.size
            or _sha256(path) != item.sha256
        ):
            raise StockIpodError(
                f"Orphan media changed after dry-run: {item.relative_path}")

    before_by_path = {item.relative_path: item for item in plan.artwork_before}
    for relative, before in before_by_path.items():
        live = session.guard(relative)
        if (
            not live.is_file()
            or live.stat().st_size != before.size
            or _sha256(live) != before.sha256
        ):
            raise StockIpodError(f"Artwork changed after dry-run: {relative}")
    if plan.rendered_artwork is not None:
        output_paths = (
            Path("iPod_Control") / "Artwork" / "ArtworkDB",
            *(item.relative_path for item in plan.rendered_artwork.ithmb_files),
        )
        for relative in output_paths:
            if relative not in before_by_path and session.guard(relative).exists():
                raise StockIpodError(f"Artwork file appeared after dry-run: {relative}")
        for ithmb in plan.rendered_artwork.ithmb_files:
            digest = hashlib.sha256()
            size = 0
            for segment in ithmb.segments:
                if (
                    not segment.path.is_file()
                    or segment.path.stat().st_size != segment.size
                    or _sha256(segment.path) != segment.sha256
                ):
                    raise StockIpodError(
                        f"Prepared artwork changed after dry-run: {segment.path}")
                size += segment.size
                with segment.path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            if size != ithmb.size or digest.hexdigest() != ithmb.sha256:
                raise StockIpodError(
                    f"Prepared artwork layout changed: {ithmb.relative_path}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    recovery = Path(".soundbox") / "stock-ipod-recovery" / (
        f"{stamp}-delete-{plan.database_sha256_before[:12]}")
    original_database = database.read_bytes()
    original_play_counts = play_counts.read_bytes() if play_counts.is_file() else None
    try:
        statvfs = os.statvfs(plan.target)
        free = statvfs.f_bavail * statvfs.f_frsize
    except OSError as exc:
        raise StockIpodError(f"Could not verify free space on {plan.target}: {exc}") from exc
    recovery_bytes = len(original_database) + len(plan.rendered_database)
    recovery_bytes += sum(item.size for item in plan.artwork_before)
    if original_play_counts is not None:
        recovery_bytes += len(original_play_counts) * 2
    required = plan.artwork_bytes_to_copy + recovery_bytes + 1024 * 1024
    if required > free:
        raise StockIpodError(
            f"Not enough free space for the recoverable deletion: "
            f"needs {required} bytes, has {free} bytes.")
    recovery_abs = session.guard(recovery)
    session.atomic_write(recovery / "iTunesDB.before", original_database)
    if original_play_counts is not None:
        session.atomic_write(recovery / "Play Counts.before", original_play_counts)
    for artwork_item in plan.artwork_before:
        source = session.guard(artwork_item.relative_path)
        destination = recovery / "artwork" / (artwork_item.relative_path.name + ".before")
        session.atomic_copy(source, session.guard(destination))
    manifest = json.dumps(plan.summary(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
    session.atomic_write(recovery / "plan.json", manifest)

    artwork_committed = False
    database_committed = False
    try:
        if plan.rendered_artwork is not None:
            for ithmb in plan.rendered_artwork.ithmb_files:
                destination = session.guard(ithmb.relative_path)
                session.atomic_concat(
                    [segment.path for segment in ithmb.segments], destination)
                if destination.stat().st_size != ithmb.size or _sha256(
                        destination) != ithmb.sha256:
                    raise StockIpodError(
                        f"Committed artwork failed verification: {destination}")
            artwork_db = session.guard(Path("iPod_Control") / "Artwork" / "ArtworkDB")
            session.atomic_write(artwork_db, plan.rendered_artwork.artwork_db)
            artwork_committed = True
        session.atomic_write(database, plan.rendered_database)
        database_committed = True
        if original_play_counts is not None:
            consumed = session.guard(recovery / "Play Counts.consumed")
            consumed.parent.mkdir(parents=True, exist_ok=True)
            session.revalidate()
            os.replace(play_counts, consumed)
        session.revalidate()
    except Exception as original_exc:
        rollback_failures: list[str] = []
        if database_committed:
            try:
                session.atomic_write(database, original_database)
            except Exception as rollback_exc:
                rollback_failures.append(f"iTunesDB: {rollback_exc}")
            if original_play_counts is not None and not play_counts.exists():
                try:
                    session.atomic_write(play_counts, original_play_counts)
                except Exception as rollback_exc:
                    rollback_failures.append(f"Play Counts: {rollback_exc}")
        if artwork_committed or plan.rendered_artwork is not None:
            output_paths = (
                Path("iPod_Control") / "Artwork" / "ArtworkDB",
                *(item.relative_path for item in (
                    plan.rendered_artwork.ithmb_files
                    if plan.rendered_artwork is not None else ())),
            )
            for relative in output_paths:
                rollback_before = before_by_path.get(relative)
                destination = session.guard(relative)
                try:
                    if rollback_before is None:
                        destination.unlink(missing_ok=True)
                    else:
                        saved = session.guard(
                            recovery / "artwork" / (relative.name + ".before"))
                        session.atomic_copy(saved, destination)
                except Exception as rollback_exc:
                    rollback_failures.append(f"{relative}: {rollback_exc}")
        if rollback_failures:
            raise StockIpodError(
                "Stock deletion failed and exact rollback was incomplete; recovery "
                f"is at {recovery_abs}. Failures: {'; '.join(rollback_failures)}"
            ) from original_exc
        raise

    cleanup_failures: list[str] = []
    deleted_files = 0
    deleted_bytes = 0
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
            deleted_files += 1
            deleted_bytes += item.size
        except Exception as exc:
            cleanup_failures.append(f"{item.relative_path}: {exc}")
    for relative in plan.artwork_delete_paths:
        path = session.guard(relative)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_failures.append(f"{relative}: {exc}")
    session.revalidate()
    return StockIpodDeleteResult(
        recovery_directory=recovery_abs,
        playlists=plan.playlist_names,
        deleted_track_records=plan.deleted_track_count,
        deleted_media_files=deleted_files,
        deleted_bytes=deleted_bytes,
        remaining_tracks=plan.final_track_count,
        remaining_playlists=plan.remaining_playlist_count,
        database_sha256=hashlib.sha256(plan.rendered_database).hexdigest(),
        cleanup_failures=tuple(cleanup_failures),
    )
