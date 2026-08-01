"""Read-only stock-iPod identity, library, and playlist snapshots.

This module is the information side of the stock-iPod boundary.  It parses the
same classic database that :mod:`soundbox.stock_ipod` writes, but it never
creates, edits, renames, or removes anything on the mounted device.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct

from clickwheel.ipod import (  # type: ignore[import-untyped]
    get_ipod_tracks,
    merge_playcounts,
    parse_itunesdb,
    read_play_counts,
)

from .stock_ipod import normalize_model_number, supports_stock_write


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


_HARDWARE_CATALOG = {
    "MA446": ("iPod Video", "5.5th generation", "Wolfson WM8758BG"),
}

# The serial-number suffix mapping is the mechanism used by libgpod's device
# catalog.  Keep this intentionally narrow until each additional model is
# backed by a fixture and hardware-catalog entry.  WEE is deliberately omitted:
# historical libgpod tables assign that suffix to more than one model.
_SERIAL_SUFFIX_MODELS = {
    "VQM": "MA446",
    "V9M": "MA446",
    "V9N": "MA446",
}


def model_from_serial(serial_number: str) -> tuple[str, str]:
    """Return a verified model and the serial suffix that established it."""
    serial = "".join(
        char for char in str(serial_number or "").upper() if char.isalnum())
    suffix = serial[-3:] if len(serial) >= 3 else ""
    return _SERIAL_SUFFIX_MODELS.get(suffix, ""), suffix


def read_stock_ipod_header(root: str | Path) -> StockIpodHeader:
    """Read only the bounded MHBD header used for stable identity."""
    path = Path(root) / "iPod_Control" / "iTunes" / "iTunesDB"
    with path.open("rb") as handle:
        data = handle.read(0xA4)
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
        model = normalize_model_number(str(value.get("model") or ""))
        if supports_stock_write(model):
            return model
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _playlist_snapshots(database: dict, tracks: list[dict]) -> tuple[StockIpodPlaylistSnapshot, ...]:
    by_track_id = {int(item.get("trackID") or 0): item for item in tracks}
    output: list[StockIpodPlaylistSnapshot] = []
    seen: set[tuple[int, str]] = set()
    seen_smart_titles: set[str] = set()
    sources = ((database.get("mhlp") or [], "Playlist"),
               (database.get("mhlp_smart") or [], "Smart playlist"))
    for playlists, default_kind in sources:
        for raw in playlists:
            title = str(raw.get("Title") or "Untitled playlist")
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
) -> StockIpodSnapshot:
    """Parse a complete, immutable view of a mounted classic stock iPod."""
    target = Path(root).expanduser().resolve(strict=True)
    database_path = target / "iPod_Control" / "iTunes" / "iTunesDB"
    header = read_stock_ipod_header(target)
    database = parse_itunesdb(str(database_path))
    raw_tracks = [dict(item) for item in get_ipod_tracks(database)]

    play_counts_path = database_path.with_name("Play Counts")
    pending_by_index: dict[int, tuple[int, int]] = {}
    pending_records = 0
    playback_status = "No pending playback records"
    if play_counts_path.is_file():
        entries = read_play_counts(target)
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
        ))

    model, serial_suffix = model_from_serial(serial_number)
    model_source = f"serial suffix {serial_suffix}" if model else ""
    if not model:
        model = normalize_model_number(model_number)
        model_source = "remembered confirmation" if model else ""
    if not model:
        model = confirmed_model_from_recovery(target)
        if model:
            model_source = "previous SoundBox sync"
    family, generation, dac_name = _HARDWARE_CATALOG.get(
        model, ("Classic stock iPod", "Unknown generation", ""))
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
        database_sha256=_sha256(database_path),
        media_bytes=sum(item.size for item in tracks),
        pending_playback_records=pending_records,
        playback_status=playback_status,
        recovery_count=recovery_count,
        tracks=tuple(tracks),
        playlists=_playlist_snapshots(database, raw_tracks),
        serial_number=str(serial_number or ""),
        serial_source=str(serial_source or ""),
        usb_serial=str(usb_serial or ""),
        firmware_version=str(firmware_version or ""),
        firmware_source=str(firmware_source or ""),
        transport_revision=str(transport_revision or ""),
        dac_name=dac_name,
        dac_source=dac_source,
    )
