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

import time
import uuid
import hashlib
import math
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from .library import Library, Track
from .paths import sanitize_component

ID_PREFIX = "sb-"

# What we let in (all Rockbox-playable). Finder drops and the file dialog
# filter through this same set.
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac",
              ".ogg", ".opus"}


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def new_id() -> str:
    return ID_PREFIX + uuid.uuid4().hex[:12]


def copy_from_device(
        selected: list[Path],
        media_root: Path | None = None) -> list[Path]:
    """Copy selected device tracks/playlists into SoundBox Media in order.

    Device originals are read-only. M3U8 entries are expanded in playlist
    order; duplicate source paths keep their first occurrence.
    """
    destination = (
        Path(media_root).expanduser()
        if media_root is not None
        else Path.home() / "Music" / "SoundBox Media")
    ordered: list[Path] = []
    seen: set[Path] = set()
    for raw in selected:
        path = Path(raw).expanduser().resolve()
        candidates = [path]
        if path.suffix.lower() == ".m3u8":
            candidates = []
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            # Root-absolute Rockbox entries are relative to the mounted volume.
            volume = next(
                (parent for parent in path.parents
                 if parent.parent == Path("/Volumes")),
                (path.parent.parent
                 if path.parent.name == "Playlists" else path.parent))
            for line in text.splitlines():
                entry = line.strip()
                if not entry or entry.startswith("#"):
                    continue
                candidates.append(
                    (volume / entry.lstrip("/")).resolve()
                    if entry.startswith("/")
                    else (path.parent / entry).resolve())
        for source in candidates:
            if source in seen or not is_audio(source) or not source.is_file():
                continue
            seen.add(source)
            ordered.append(source)

    artist_dir = destination / "Unknown Artist"
    artist_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in ordered:
        name = sanitize_component(source.stem) + source.suffix.lower()
        target = artist_dir / name
        if target.exists():
            if target.stat().st_size == source.stat().st_size:
                copied.append(target)
                continue
            suffix = hashlib.sha1(str(source).encode()).hexdigest()[:6]
            target = artist_dir / (
                f"{sanitize_component(source.stem)}_{suffix}"
                f"{source.suffix.lower()}")
        tmp = target.with_name(target.name + ".sbpart")
        try:
            shutil.copyfile(source, tmp)
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        copied.append(target)
    return copied


def duration_seconds(path: Path) -> int:
    """Read container duration without decoding or modifying the source.

    libsndfile is the cheap first path for WAV/AIFF/FLAC and the formats it can
    identify. Mutagen supplies container metadata for ALAC/AAC MP4 files (and
    is also a safe fallback for another supported header libsndfile rejects).
    """
    duration = 0.0
    try:
        import soundfile as sf
        duration = float(sf.info(str(path)).duration or 0.0)
    except Exception:  # noqa: BLE001 - fallback handles unsupported containers
        pass
    if not math.isfinite(duration) or duration <= 0:
        try:
            import mutagen
            audio = mutagen.File(path)
            info = getattr(audio, "info", None)
            duration = float(getattr(info, "length", 0.0) or 0.0)
        except Exception:  # noqa: BLE001 - an unreadable file stays unknown
            duration = 0.0
    return int(duration) if math.isfinite(duration) and duration > 0 else 0


def probe(path: Path) -> dict:
    """Build the library.json record for a file: path + import date + what
    metadata we can read cheaply (container duration and size). Never raises;
    an actually unreadable header simply keeps an unknown duration."""
    secs = duration_seconds(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "added": date.today().isoformat(),
            "added_ts": time.time(),  # full timestamp: newest-added sorts to the top
            "time": secs, "size": size}


def make_track(tid: str, rec: dict) -> Track:
    """An imported file as a first-class Track. No BPM/key (rekordbox never
    analyzed it) — the `imported` flag exempts it from the hide-untracked
    sweep, which keys on exactly that absence."""
    p = Path(str(rec.get("path", "")))
    added = str(rec.get("added", ""))
    # Prefer the full import timestamp; older records (date only) fall back to
    # that date at midnight so they still sort by day.
    ts = float(rec.get("added_ts", 0) or 0)
    if not ts and added:
        try:
            ts = datetime.strptime(added[:10], "%Y-%m-%d").timestamp()
        except ValueError:
            ts = 0.0
    return Track(tid, p.stem, "", "file://localhost" + quote(str(p)),
                 total_time=int(rec.get("time", 0) or 0),
                 date_added=added,
                 added_ts=ts,
                 size=int(rec.get("size", 0) or 0),
                 imported=True)


def refresh_metadata(imported: dict) -> int:
    """Backfill missing duration/size in existing imported records in place.

    Older ALAC/AAC records have ``time: 0`` because the original probe used
    only libsndfile. Read headers only for missing values; source audio remains
    byte-identical. Returns the number of records changed so the app can persist
    the repair once.
    """
    changed = 0
    for rec in imported.values():
        path = Path(str(rec.get("path", "")))
        if not path.is_file():
            continue
        repaired = False
        if int(rec.get("time", 0) or 0) <= 0:
            secs = duration_seconds(path)
            if secs > 0:
                rec["time"] = secs
                repaired = True
        if int(rec.get("size", 0) or 0) <= 0:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size > 0:
                rec["size"] = size
                repaired = True
        if repaired:
            changed += 1
    return changed


def inject(lib: Library, imported: dict) -> int:
    """Merge the imported tracks into a (re)loaded Library. Idempotent — safe
    to call after every rekordbox refresh. Missing legacy metadata is repaired
    from read-only source headers before Track rows are built."""
    changed = refresh_metadata(imported)
    for tid, rec in imported.items():
        lib.tracks_by_id[tid] = make_track(tid, rec)
    return changed


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
