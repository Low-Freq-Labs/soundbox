"""Read the LIVE rekordbox database (read-only) into our Library model.

SAFETY: never touches the live master.db. It copies the DB (+ -wal/-shm) to a
temp file and reads the COPY read-only via pyrekordbox — same read-only posture
as the XML import, just sourced from a live snapshot instead of a manual export.
Never writes to rekordbox.

Normalizes into the same source-neutral `Library` model as the XML adapter, so
the UI, export, and overrides work unchanged. rekordbox content IDs == XML
TrackIDs, so artist/title overrides and SoundBox playlists carry over.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from .library import Library, Playlist, Track
from .rekordbox_analysis import resolve_dat_path

REKORDBOX_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"

# rekordbox musical key name -> Camelot (matches the XML's Tonality the user knows).
_CAMELOT = {
    "C": "8B", "Db": "3B", "C#": "3B", "D": "10B", "Eb": "5B", "D#": "5B",
    "E": "12B", "F": "7B", "Gb": "2B", "F#": "2B", "G": "9B", "Ab": "4B",
    "G#": "4B", "A": "11B", "Bb": "6B", "A#": "6B", "B": "1B",
    "Cm": "5A", "Dbm": "12A", "C#m": "12A", "Dm": "7A", "Ebm": "2A", "D#m": "2A",
    "Em": "9A", "Fm": "4A", "Gbm": "11A", "F#m": "11A", "Gm": "6A", "Abm": "1A",
    "G#m": "1A", "Am": "8A", "Bbm": "3A", "A#m": "3A", "Bm": "10A",
}

_HOT_CUE_COLORS = (
    [255, 55, 111], [69, 172, 219], [125, 193, 61], [170, 114, 255],
    [48, 210, 110], [224, 100, 27], [48, 90, 255], [195, 175, 4],
)


def db_available() -> bool:
    """True if a rekordbox database exists to read."""
    return REKORDBOX_DB.is_file()


def _camelot(scale: str) -> str:
    return _CAMELOT.get((scale or "").strip(), scale or "")


def load_live() -> Library:
    """Snapshot the live rekordbox DB and parse it (read-only). Raises on failure."""
    if not REKORDBOX_DB.is_file():
        raise FileNotFoundError(f"rekordbox database not found: {REKORDBOX_DB}")
    from pyrekordbox import Rekordbox6Database  # imported lazily (heavy dep)

    tmp = Path(tempfile.mkdtemp(prefix="soundbox_rb_"))
    copy = tmp / "master.db"
    try:
        # READ-ONLY snapshot: copy the live files; never open the originals.
        shutil.copy2(REKORDBOX_DB, copy)
        for ext in ("-wal", "-shm"):
            extra = REKORDBOX_DB.with_name(REKORDBOX_DB.name + ext)
            if extra.is_file():
                shutil.copy2(extra, copy.with_name(copy.name + ext))

        db = Rekordbox6Database(path=str(copy))
        try:
            tracks_by_id = _read_tracks(db)
            playlists = _read_playlists(db)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        # Soundbox is a PLAYLIST exporter — keep only tracks that are actually in a
        # playlist. Drops orphan collection entries (added to rekordbox but in no
        # playlist, files usually long gone) so the count matches the real library.
        referenced = {k for pl in playlists for k in pl.keys}
        tracks_by_id = {tid: t for tid, t in tracks_by_id.items() if tid in referenced}
        return Library(
            tracks_by_id=tracks_by_id,
            playlists=playlists,
            source=REKORDBOX_DB,
            source_kind="rekordbox-db",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _deleted(row) -> bool:
    # rekordbox soft-deletes (keeps the row for cloud/device sync) — hide those,
    # exactly as rekordbox itself does.
    return bool(getattr(row, "rb_local_deleted", 0))


def _read_tracks(db) -> dict[str, Track]:
    cues_by_track = _read_cues(db)
    out: dict[str, Track] = {}
    for c in db.get_content():
        if _deleted(c):
            continue
        loc = c.FolderPath or ""
        uri = "file://localhost" + quote(loc) if loc else ""
        try:
            bpm = f"{(c.BPM or 0) / 100:.2f}" if c.BPM else ""
        except (TypeError, ValueError):
            bpm = ""
        # created_at = when this row was written = when the track was added to
        # rekordbox (real time-of-day precision). DateCreated is a stale tag date
        # that can predate the add by weeks, so it's display-only; created_at
        # drives the "recently added" sort.
        try:
            added_ts = c.created_at.timestamp() if c.created_at else 0.0
        except (AttributeError, ValueError, OSError):
            added_ts = 0.0
        out[str(c.ID)] = Track(
            track_id=str(c.ID),
            name=c.Title or "",
            artist=(c.Artist.Name if c.Artist else ""),
            location_uri=uri,
            bpm=bpm,
            key=_camelot(c.Key.ScaleName if c.Key else ""),
            total_time=int(c.Length or 0),
            date_added=str(c.DateCreated or "")[:10],
            added_ts=added_ts,
            genre=(c.Genre.Name if c.Genre else ""),
            size=int(c.FileSize or 0),
            bitrate=str(c.BitRate or ""),
            rating=str(c.Rating or ""),
            source_analysis_path=str(resolve_dat_path(
                REKORDBOX_DB, getattr(c, "AnalysisDataPath", "") or "") or ""),
            source_cues=cues_by_track.get(str(c.ID), []),
        )
    return out


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cue_color(row, slot: int) -> list[int]:
    """Return stable display RGB without pretending undocumented DB indices
    are RGB values.

    Rekordbox stores a palette index for most hot cues, not an RGB triplet.
    Until that palette is verified independently, SoundBox uses its established
    deterministic slot colors. Explicit RGB-like row fields are accepted when
    a future pyrekordbox schema exposes them.
    """
    explicit = [
        _integer(getattr(row, name, 0))
        for name in ("ColorRed", "ColorGreen", "ColorBlue")
    ]
    if any(explicit):
        return [max(0, min(255, value)) for value in explicit]
    if slot >= 0:
        return list(_HOT_CUE_COLORS[slot % len(_HOT_CUE_COLORS)])
    return [126, 132, 148]


def _hot_cue_slot(kind_number: int) -> int | None:
    """Translate rekordbox ``djmdCue.Kind`` to its canonical A–P slot.

    The live database does *not* use a contiguous one-based sequence. A/B/C
    are 1/2/3, value 4 is reserved by rekordbox, and D–P are 5–17. Treating the
    field as ``Kind - 1`` shifts every cue from D onward and makes D impossible
    to display. Unknown/reserved values are rejected instead of being assigned
    a plausible-but-wrong letter.
    """
    if 1 <= kind_number <= 3:
        return kind_number - 1
    if 5 <= kind_number <= 17:
        return kind_number - 2
    return None


def _read_cues(db) -> dict[str, list[dict]]:
    """Read every active cue once and group it by rekordbox ContentID."""
    grouped: dict[str, list[dict]] = {}
    try:
        rows = db.get_cue()
    except (AttributeError, TypeError):
        return grouped
    memory: dict[str, list[dict]] = {}
    for row in rows:
        if _deleted(row):
            continue
        content_id = str(getattr(row, "ContentID", "") or "")
        if not content_id:
            continue
        kind_number = _integer(getattr(row, "Kind", 0))
        if kind_number == 0:
            kind = "memory"
            slot = -1
        else:
            kind = "hot"
            mapped_slot = _hot_cue_slot(kind_number)
            if mapped_slot is None:
                continue
            slot = mapped_slot
        position = max(0, _integer(getattr(row, "InMsec", 0)))
        end = _integer(getattr(row, "OutMsec", -1), -1)
        is_loop = end > position
        cue = {
            "kind": kind,
            "slot": slot,
            "position_ms": position,
            "end_ms": end if is_loop else -1,
            "type": "loop" if is_loop else "single",
            "label": str(getattr(row, "Comment", "") or "").strip(),
            "color": _cue_color(row, slot),
        }
        if kind == "memory":
            memory.setdefault(content_id, []).append(cue)
        else:
            grouped.setdefault(content_id, []).append(cue)
    for content_id, rows in memory.items():
        rows.sort(key=lambda cue: (
            cue["position_ms"], cue["end_ms"], cue["label"]))
        for slot, cue in enumerate(rows):
            cue["slot"] = slot
        grouped.setdefault(content_id, []).extend(rows)
    for rows in grouped.values():
        rows.sort(key=lambda cue: (
            0 if cue["kind"] == "hot" else 1,
            cue["slot"], cue["position_ms"]))
    return grouped


def _read_playlists(db) -> list[Playlist]:
    from pyrekordbox.db6.tables import DjmdSongPlaylist

    # SACRED ORDER: a playlist's track order is DjmdSongPlaylist.TrackNo — NOT the
    # order get_playlist_contents returns (that comes back sorted by ContentID).
    # Load all membership rows once, group by playlist, sort each by TrackNo.
    tracks_by_pl: dict[str, list] = {}
    for sp in db.query(DjmdSongPlaylist).all():
        if _deleted(sp):
            continue
        tracks_by_pl.setdefault(str(sp.PlaylistID), []).append(sp)
    for rows in tracks_by_pl.values():
        rows.sort(key=lambda r: (r.TrackNo if r.TrackNo is not None else 0))

    # Group nodes by parent and sort by Seq so we match rekordbox's exact order.
    children: dict[str, list] = {}
    for p in db.get_playlist():
        if _deleted(p):  # skip rekordbox-deleted playlists/folders
            continue
        children.setdefault(str(p.ParentID), []).append(p)
    for kids in children.values():
        kids.sort(key=lambda n: (n.Seq if n.Seq is not None else 0))

    playlists: list[Playlist] = []

    def walk(parent_id: str, path: list[str]):
        for p in children.get(parent_id, []):
            if p.Attribute == 1:  # folder → recurse, extending the path
                walk(str(p.ID), path + [p.Name])
            elif p.Attribute == 0:  # normal playlist → sacred manual order (TrackNo)
                keys = [str(sp.ContentID) for sp in tracks_by_pl.get(str(p.ID), [])]
                playlists.append(Playlist(
                    name=p.Name, folder_path=list(path), keys=keys, pid=str(p.ID)))
            elif p.Attribute == 4:  # SMART/dynamic playlist → contents are computed
                try:                # from the tag/filter criteria, not a membership table
                    keys = [str(s.ID) for s in db.get_playlist_contents(p)]
                except Exception:  # noqa: BLE001
                    keys = []
                playlists.append(Playlist(
                    name=p.Name, folder_path=list(path), keys=keys, pid=str(p.ID)))
            # else: skip special nodes (e.g. Attribute -128 = cloud)

    walk("root", [])
    return playlists
