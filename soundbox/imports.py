"""Imported (non-rekordbox) tracks — livesets etc. added straight into SoundBox.

rekordbox never sees these. Files are REFERENCED in place (never copied, moved,
or re-tagged — the same sacred rules as rekordbox sources). Each import gets a
stable ``sb-…`` id (can never collide with rekordbox's numeric TrackIDs), is
recorded in library.json, and is injected into every loaded Library so it shows
up in Collection, SoundBox playlists, art fetch and exports like any other
track. Title starts as the filename stem; the usual override/derivation
machinery (keyed by the stable id) takes it from there.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from urllib.parse import quote

from .rekordbox_xml import Library, Track

ID_PREFIX = "sb-"

# What we let in (all Rockbox-playable). Finder drops and the file dialog
# filter through this same set.
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac",
              ".ogg", ".opus"}


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def new_id() -> str:
    return ID_PREFIX + uuid.uuid4().hex[:12]


def probe(path: Path) -> dict:
    """Build the library.json record for a file: path + import date + what
    metadata we can read cheaply (duration via the soundfile header, size).
    Never raises — an unreadable header (e.g. ALAC/.m4a) just means time 0."""
    secs = 0
    try:
        import soundfile as sf
        secs = int(sf.info(str(path)).duration)
    except Exception:  # header soundfile can't parse — duration stays unknown
        pass
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "added": date.today().isoformat(),
            "time": secs, "size": size}


def make_track(tid: str, rec: dict) -> Track:
    """An imported file as a first-class Track. No BPM/key (rekordbox never
    analyzed it) — the `imported` flag exempts it from the hide-untracked
    sweep, which keys on exactly that absence."""
    p = Path(str(rec.get("path", "")))
    return Track(tid, p.stem, "", "file://localhost" + quote(str(p)),
                 total_time=int(rec.get("time", 0) or 0),
                 date_added=str(rec.get("added", "")),
                 size=int(rec.get("size", 0) or 0),
                 imported=True)


def inject(lib: Library, imported: dict) -> None:
    """Merge the imported tracks into a (re)loaded Library. Idempotent — safe
    to call after every rekordbox refresh."""
    for tid, rec in imported.items():
        lib.tracks_by_id[tid] = make_track(tid, rec)
