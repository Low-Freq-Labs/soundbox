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


# ---- Fix Links: relocate imported files the user moved on disk ----

def missing(imported: dict) -> dict:
    """The imported records whose file is gone from its recorded path."""
    return {tid: rec for tid, rec in imported.items()
            if not Path(str(rec.get("path", ""))).is_file()}


def search_roots(imported: dict) -> list[Path]:
    """Where to look for moved files: the nearest still-existing ancestor of
    each imported file's old location (a move into a subfolder or sibling
    folder lands under it). Deduped; roots inside another root are dropped."""
    nearest: set[Path] = set()
    for rec in imported.values():
        for anc in Path(str(rec.get("path", ""))).parents:
            if anc.is_dir():
                nearest.add(anc)
                break
    out: list[Path] = []
    for r in sorted(nearest, key=lambda p: len(p.parts)):
        if not any(o == r or o in r.parents for o in out):
            out.append(r)
    return out


def _index(roots: list[Path], cap: int = 200_000):
    """One walk per root: audio files by lowercase name and by (size, ext).
    Hidden dirs skipped; `cap` bounds pathological trees."""
    by_name: dict[str, list[Path]] = {}
    by_size: dict[tuple[int, str], list[Path]] = {}
    seen = 0
    for root in roots:
        for p in root.rglob("*"):
            if seen >= cap:
                return by_name, by_size
            try:
                rel_parts = p.relative_to(root).parts
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            if not is_audio(p) or not p.is_file():
                continue
            seen += 1
            by_name.setdefault(p.name.lower(), []).append(p)
            try:
                by_size.setdefault((p.stat().st_size, p.suffix.lower()), []).append(p)
            except OSError:
                pass
    return by_name, by_size


def find_moved(lost: dict, roots: list[Path]) -> dict[str, Path]:
    """tid -> new location for files the user moved. Matching, strictest first:
    exact filename (case-insensitive), disambiguated by recorded byte size;
    then a unique size+extension match (catches a renamed file). Same name AND
    same size in several places = duplicate copies — take the shallowest.
    Truly ambiguous stays missing: never guess a wrong file onto the iPod."""
    by_name, by_size = _index([Path(r) for r in roots])
    found: dict[str, Path] = {}
    for tid, rec in lost.items():
        old = Path(str(rec.get("path", "")))
        size = int(rec.get("size", 0) or 0)
        cands = list(by_name.get(old.name.lower(), []))
        if size and cands:
            sized = [c for c in cands if c.stat().st_size == size]
            if sized:
                cands = sized
            elif len(cands) > 1:
                cands = []  # several same-name files, none the right size
        if not cands and size:
            cands = list(by_size.get((size, old.suffix.lower()), []))
            if len(cands) != 1:
                cands = []  # rename rescue only when it's unambiguous
        if len(cands) == 1:
            found[tid] = cands[0]
        elif len(cands) > 1 and size:
            found[tid] = min(cands, key=lambda p: (len(p.parts), str(p)))
    return found
