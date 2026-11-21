"""Read-only stock-iPod identity, library, and playlist snapshots.

This module is the information side of the stock-iPod boundary.  It parses the
same classic database that :mod:`soundbox.stock_ipod` writes, but it never
creates, edits, renames, or removes anything on the mounted device.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import io
import json
import os
from pathlib import Path
import struct

from clickwheel.ipod import (  # type: ignore[import-untyped]
    get_ipod_tracks,
    merge_playcounts,
    parse_itunesdb,
)

from .stock_ipod_capture import StockIpodNativeCapture, capture_stock_ipod_native
from .stock_ipod_format import StockWriteFormat, inspect_stock_write_format
from .stock_ipod_identity import hardware_model, model_from_serial, normalize_display_model


@dataclass(frozen=True)
class StockIpodHeader:
    database_id: int
    library_persistent_id: int
    version: int
    checksum_scheme: int


@dataclass(frozen=True)
class StockIpodTrackSnapshot:
    track_id: int
    dbid: int
    title: str
    artist: str
    album: str
    genre: str
    filetype: str
    size: int
    length_ms: int
    bitrate_kbps: int
    sample_rate: int
    play_count: int
    skip_count: int
    rating: int
    last_played: int
    last_skipped: int
    location: str
    pending_plays: int
    pending_skips: int
    year: int = 0
    bpm: int = 0
    comments: str = ""
    bookmark_time: int = 0


@dataclass(frozen=True)
class StockIpodPlaylistEntry:
    dbid: int
    title: str
    artist: str


@dataclass(frozen=True)
class StockIpodPlaylistSnapshot:
    playlist_id: int
    title: str
    kind: str
    dynamic: bool
    entries: tuple[StockIpodPlaylistEntry, ...]


@dataclass(frozen=True)
class StockIpodSnapshot:
    target: Path
    volume_name: str
    volume_uuid: str
    filesystem: str
    protocol: str
    device_identifier: str
    writable: bool
    capacity_bytes: int
    free_bytes: int
    model: str
    model_source: str
    family: str
    generation: str
    identity_key: str
    database_id: int
    library_persistent_id: int
    database_version: int
    checksum_scheme: int
    database_sha256: str
    media_bytes: int
    pending_playback_records: int
    playback_status: str
    recovery_count: int
    tracks: tuple[StockIpodTrackSnapshot, ...]
    playlists: tuple[StockIpodPlaylistSnapshot, ...]
    serial_number: str = ""
    serial_source: str = ""
    usb_serial: str = ""
    firmware_version: str = ""
    firmware_source: str = ""
    transport_revision: str = ""
    dac_name: str = ""
    dac_source: str = ""
    sync_state: str = "untracked"
    sync_detail: str = "No committed Mac-side fast-sync baseline"
    sync_local_changes: int = 0
    sync_committed_at: str = ""
    artwork_database_sha256: str = ""
    stock_format: StockWriteFormat | None = None
    usb_family: str = ""
    firewire_guid: str = ""
    identity_conflict: bool = False
    play_counts_sha256: str = ""


def playback_refresh_compatible(before: StockIpodSnapshot, after: StockIpodSnapshot) -> bool:
    """A read-only history update may patch the current native view in place."""
    observed = ('free_bytes', 'tracks', 'pending_playback_records', 'playback_status',
                'sync_state', 'sync_detail', 'sync_local_changes', 'sync_committed_at',
                'play_counts_sha256')
    if replace(after, **{name: getattr(before, name) for name in observed}) != before:
        return False
    if len(before.tracks) != len(after.tracks):
        return False
    history = ('play_count', 'skip_count', 'rating', 'last_played', 'last_skipped',
               'pending_plays', 'pending_skips', 'bookmark_time')
    return all(replace(new, **{name: getattr(old, name) for name in history}) == old
               for old, new in zip(before.tracks, after.tracks))


def read_stock_ipod_header(root: str | Path) -> StockIpodHeader:
    """Read only the bounded MHBD header used for stable identity."""
    path = Path(root) / "iPod_Control" / "iTunes" / "iTunesDB"
    with path.open("rb") as handle:
        data = handle.read(0xA4)
    return _header_from_bytes(data)


def _header_from_bytes(data: bytes) -> StockIpodHeader:
    if len(data) < 0x50 or data[:4] != b"mhbd":
        raise ValueError("The stock iPod database header is invalid")
    header_length = struct.unpack_from("<I", data, 0x04)[0]
    if header_length < 0x50:
        raise ValueError("The stock iPod database header is too short")
    return StockIpodHeader(
        database_id=struct.unpack_from("<Q", data, 0x18)[0],
        library_persistent_id=struct.unpack_from("<Q", data, 0x48)[0],
        version=struct.unpack_from("<I", data, 0x10)[0],
        checksum_scheme=struct.unpack_from("<H", data, 0x30)[0],
    )


def stock_ipod_device_key(volume_uuid: str, library_persistent_id: int) -> str:
    """Stable key for one iPod library; ordinary DatabaseID is rewrite-volatile."""
    if not library_persistent_id:
        return ""
    uuid = "".join(
        char for char in str(volume_uuid or "").upper()
        if char.isalnum() or char == "-"
    )
    suffix = f"{library_persistent_id:016X}"
    return f"stock-ipod-v1:{uuid}:{suffix}" if uuid else f"stock-ipod-v1:{suffix}"


def confirmed_model_from_recovery(root: str | Path) -> str:
    """Recover a previous explicit confirmation from SoundBox-owned evidence."""
    base = Path(root) / ".soundbox" / "stock-ipod-recovery"
    try:
        candidates = sorted(base.glob("*/plan.json"), reverse=True)[:32]
    except OSError:
        return ""
    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        model = normalize_display_model(str(value.get("model") or ""))
        if hardware_model(model) is not None:
            return model
    return ""


def _playlist_snapshots(database: dict, tracks: list[dict]) -> tuple[StockIpodPlaylistSnapshot, ...]:
    by_track_id = {int(item.get("trackID") or 0): item for item in tracks}
    output: list[StockIpodPlaylistSnapshot] = []
    seen: set[tuple[int, str]] = set()
    seen_smart_titles: set[str] = set()
    canonical_smart_titles = {
        str(item.get("Title") or "").casefold()
        for item in (database.get("mhlp_smart") or [])
        if item.get("isSmartPlaylist")
    }
    sources = ((database.get("mhlp") or [], "Playlist"),
               (database.get("mhlp_smart") or [], "Smart playlist"))
    for playlists, default_kind in sources:
        for raw in playlists:
            title = str(raw.get("Title") or "Untitled playlist")
            # Apple can mirror one smart list in both the ordinary MHLP and
            # canonical MHSD-5 datasets with different playlist IDs.  Expose
            # only the canonical record so a managed action cannot target the
            # alias while leaving the real system index untouched.
            if (
                default_kind == "Playlist"
                and bool(raw.get("isSmartPlaylist"))
                and title.casefold() in canonical_smart_titles
            ):
                continue
            playlist_id = int(raw.get("playlistID") or 0)
            marker = (playlist_id, title)
            if marker in seen:
                continue
            seen.add(marker)
            is_master = bool(raw.get("isMaster"))
            is_smart = bool(raw.get("isSmartPlaylist")) or default_kind.startswith("Smart")
            if is_smart and title.casefold() in seen_smart_titles:
                continue
            if is_smart:
                seen_smart_titles.add(title.casefold())
            kind = "Library" if is_master else ("Smart playlist" if is_smart else "Playlist")
            entries = []
            for ref in raw.get("items") or []:
                track = by_track_id.get(int(ref.get("trackID") or 0))
                if track is None:
                    continue
                entries.append(StockIpodPlaylistEntry(
                    dbid=int(track.get("dbid") or 0),
                    title=str(track.get("title") or "Unknown title"),
                    artist=str(track.get("artist") or ""),
                ))
            output.append(StockIpodPlaylistSnapshot(
                playlist_id=playlist_id,
                title=title,
                kind=kind,
                dynamic=is_smart,
                entries=tuple(entries),
            ))
    return tuple(output)


def read_stock_ipod_snapshot(
    root: str | Path,
    *,
    model_number: str = "",
    volume_uuid: str = "",
    filesystem: str = "",
    protocol: str = "",
    device_identifier: str = "",
    writable: bool = False,
    capacity_bytes: int = 0,
    free_bytes: int = 0,
    serial_number: str = "",
    serial_source: str = "",
    usb_serial: str = "",
    firmware_version: str = "",
    firmware_source: str = "",
    transport_revision: str = "",
    native_capture: StockIpodNativeCapture | None = None,
    usb_family: str = "",
    firewire_guid: str = "",
    observed_model_number: str = "",
    observed_model_source: str = "",
    identity_conflict: bool = False,
) -> StockIpodSnapshot:
    """Parse a complete, immutable view of a mounted classic stock iPod."""
    target = Path(root).expanduser().resolve(strict=True)
    captured = native_capture or capture_stock_ipod_native(target, include_artwork=False)
    captured.require_target(target)
    header = _header_from_bytes(captured.database)
    database = parse_itunesdb(io.BytesIO(captured.database))
    raw_tracks = [dict(item) for item in get_ipod_tracks(database)]

    pending_by_index: dict[int, tuple[int, int]] = {}
    pending_records = 0
    playback_status = "No pending playback records"
    if captured.play_counts is not None:
        entries = captured.playback_entries(expected_tracks=len(raw_tracks))
        if entries is None:
            playback_status = "Playback records are unreadable"
        elif len(entries) != len(raw_tracks):
            playback_status = (
                f"Playback record mismatch ({len(entries)} for {len(raw_tracks)} tracks)")
        else:
            for index, entry in enumerate(entries):
                plays = int(entry.play_count or 0)
                skips = int(entry.skip_count or 0)
                pending_by_index[index] = (plays, skips)
                if entry.has_data:
                    pending_records += 1
            merge_playcounts(raw_tracks, entries)
            playback_status = f"{pending_records} pending playback record(s)"

    tracks = []
    for index, raw in enumerate(raw_tracks):
        pending_plays, pending_skips = pending_by_index.get(index, (0, 0))
        tracks.append(StockIpodTrackSnapshot(
            track_id=int(raw.get("trackID") or 0),
            dbid=int(raw.get("dbid") or 0),
            title=str(raw.get("title") or "Unknown title"),
            artist=str(raw.get("artist") or ""),
            album=str(raw.get("album") or ""),
            genre=str(raw.get("genre") or ""),
            filetype=str(raw.get("filetype") or raw.get("Filetype") or ""),
            size=int(raw.get("size") or 0),
            length_ms=int(raw.get("length") or 0),
            bitrate_kbps=int(raw.get("bitrate") or 0),
            sample_rate=int(raw.get("sampleRate") or 0),
            play_count=int(raw.get("playCount") or 0),
            skip_count=int(raw.get("skipCount") or 0),
            rating=int(raw.get("rating") or 0),
            last_played=int(raw.get("lastPlayed") or 0),
            last_skipped=int(raw.get("lastSkipped") or 0),
            location=str(raw.get("location") or ""),
            pending_plays=pending_plays,
            pending_skips=pending_skips,
            year=int(raw.get("year") or 0),
            bpm=int(raw.get("bpm") or 0),
            comments=str(raw.get("Comment") or raw.get("comment") or ""),
            bookmark_time=int(raw.get("bookmarkTime") or 0),
        ))

    model = normalize_display_model(observed_model_number)
    model_source = (observed_model_source or "connected device metadata") if model else ""
    serial_model, serial_suffix = model_from_serial(serial_number)
    identity_conflict = identity_conflict or bool(model and serial_model and model != serial_model)
    observed_hardware = hardware_model(model or serial_model)
    if (observed_hardware is not None and usb_family in {"iPod Video", "iPod Classic"}
            and observed_hardware.family != usb_family):
        identity_conflict = True
    if identity_conflict:
        model, model_source = "", "conflicting device identity"
    elif not model:
        model = serial_model
        model_source = f"serial suffix {serial_suffix}" if model else ""
    if not model and not identity_conflict:
        model = normalize_display_model(model_number)
        model_source = "remembered confirmation" if model else ""
    if not model and not identity_conflict:
        model = confirmed_model_from_recovery(target)
        if model:
            model_source = "previous SoundBox sync"
    hardware = hardware_model(model)
    family, generation, dac_name = ((hardware.family, hardware.generation, hardware.dac_name)
        if hardware else (usb_family or "Classic stock iPod", "Unknown generation", ""))
    dac_source = f"verified {model} hardware catalog" if dac_name else ""
    identity_key = stock_ipod_device_key(volume_uuid, header.library_persistent_id)
    if not capacity_bytes or not free_bytes:
        try:
            stat = os.statvfs(target)
            capacity_bytes = capacity_bytes or stat.f_blocks * stat.f_frsize
            free_bytes = free_bytes or stat.f_bavail * stat.f_frsize
        except OSError:
            pass
    recovery = target / ".soundbox" / "stock-ipod-recovery"
    try:
        recovery_count = sum(1 for item in recovery.iterdir() if item.is_dir())
    except OSError:
        recovery_count = 0

    return StockIpodSnapshot(
        target=target,
        volume_name=target.name,
        volume_uuid=str(volume_uuid or ""),
        filesystem=str(filesystem or ""),
        protocol=str(protocol or ""),
        device_identifier=str(device_identifier or ""),
        writable=bool(writable),
        capacity_bytes=int(capacity_bytes or 0),
        free_bytes=int(free_bytes or 0),
        model=model,
        model_source=model_source,
        family=family,
        generation=generation,
        identity_key=identity_key,
        database_id=header.database_id,
        library_persistent_id=header.library_persistent_id,
        database_version=header.version,
        checksum_scheme=header.checksum_scheme,
        database_sha256=captured.database_sha256,
        media_bytes=sum(item.size for item in tracks),
        pending_playback_records=pending_records,
        playback_status=playback_status,
        recovery_count=recovery_count,
        tracks=tuple(tracks),
        playlists=_playlist_snapshots(database, raw_tracks),
        artwork_database_sha256=captured.artwork_database_sha256,
        stock_format=inspect_stock_write_format(captured.database, captured.artwork_database,
            usb_family=usb_family, firewire_guid=firewire_guid or usb_serial),
        usb_family=usb_family,
        firewire_guid=firewire_guid or usb_serial,
        serial_number=str(serial_number or ""),
        serial_source=str(serial_source or ""),
        usb_serial=str(usb_serial or ""),
        firmware_version=str(firmware_version or ""),
        firmware_source=str(firmware_source or ""),
        transport_revision=str(transport_revision or ""),
        dac_name=dac_name,
        dac_source=dac_source,
        identity_conflict=identity_conflict,
        play_counts_sha256=captured.play_counts_sha256,
    )
