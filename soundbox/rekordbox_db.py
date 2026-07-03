"""Read the LIVE rekordbox database (read-only) into our Library model.

SAFETY: never touches the live master.db. It copies the DB (+ -wal/-shm) to a
temp file and reads the COPY read-only via pyrekordbox — same read-only posture
as the XML import, just sourced from a live snapshot instead of a manual export.
Never writes to rekordbox.

Produces the exact same `Library` object as the XML parser, so the UI, export,
and overrides all work unchanged. rekordbox content IDs == XML TrackIDs, so
artist/title overrides and SoundBox playlists carry over.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from .rekordbox_xml import Library, Playlist, Track

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
        return Library(tracks_by_id=tracks_by_id, playlists=playlists, source_xml=REKORDBOX_DB)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _deleted(row) -> bool:
    # rekordbox soft-deletes (keeps the row for cloud/device sync) — hide those,
    # exactly as rekordbox itself does.
    return bool(getattr(row, "rb_local_deleted", 0))


def _read_tracks(db) -> dict[str, Track]:
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
        out[str(c.ID)] = Track(
            track_id=str(c.ID),
            name=c.Title or "",
            artist=(c.Artist.Name if c.Artist else ""),
            location_uri=uri,
            bpm=bpm,
            key=_camelot(c.Key.ScaleName if c.Key else ""),
            total_time=int(c.Length or 0),
            date_added=str(c.DateCreated or "")[:10],
            genre=(c.Genre.Name if c.Genre else ""),
            size=int(c.FileSize or 0),
            bitrate=str(c.BitRate or ""),
            rating=str(c.Rating or ""),
        )
    return out


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
