"""Full-library backup & restore (the "thorough" backup, in-app).

A backup is one .zip holding everything SoundBox knows that isn't rekordbox:
    library.json   title/artist overrides, custom playlists, deletes/removals,
                   settings — the crown jewels.
    backups/       the rotating library.json history.
    art/           the whole cover-art cache (index + one image per TrackID).
    README.txt     what this is + how to restore by hand.

Restore accepts the .zip OR a folder laid out the same way (e.g. an unzipped
backup, or the app-data dir itself) OR a path to a library.json inside one.
The CURRENT library.json is snapshotted into backups/ before anything is
overwritten, so a restore can never lose state. Never touches rekordbox.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

_ITEMS = ("library.json", "backups", "art")

_README = """SoundBox full backup — {date}
=================================
library.json   your portable SoundBox library: title/artist overrides, custom
               playlists, deletes/removals, settings. Keyed by rekordbox
               TrackID — the crown jewels.
backups/       rotating history of library.json (earlier states).
art/           the whole cover-art cache (index.json + one image per TrackID),
               all baseline-JPEG (iPod-safe). Saves an hour+ of re-fetching.

RESTORE: Settings -> "Restore from backup" and pick this .zip (or the
library.json inside the unzipped folder). By hand: quit Soundbox, copy
library.json (and art/ if wanted) into
~/Library/Application Support/LowFreqLabs/Soundbox/ and relaunch.
"""


def export_backup(data_dir, dest_zip) -> int:
    """Write the full backup zip. Returns the number of files archived.
    JPEGs are STORED (already compressed); everything else deflates."""
    data_dir, dest_zip = Path(data_dir), Path(dest_zip)
    n = 0
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", _README.format(date=time.strftime("%Y-%m-%d")))
        for item in _ITEMS:
            p = data_dir / item
            if p.is_file():
                z.write(p, item)
                n += 1
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and not f.name.startswith("."):
                        kind = (zipfile.ZIP_STORED if f.suffix.lower() in (".jpg", ".jpeg")
                                else zipfile.ZIP_DEFLATED)
                        z.write(f, str(f.relative_to(data_dir)), compress_type=kind)
                        n += 1
    return n


def _backup_root(src: Path, tmp: Path) -> Path:
    """Resolve whatever the user picked (zip / folder / library.json path) to a
    folder containing library.json. Zips are extracted into `tmp`."""
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)  # trusted local backup; still lands in OUR temp dir
        return tmp
    if src.is_file() and src.name == "library.json":
        return src.parent
    if src.is_dir():
        return src
    raise ValueError(f"Not a SoundBox backup: {src}")


def restore_backup(data_dir, src) -> dict:
    """Restore a backup into data_dir. Returns a summary dict. The current
    library.json is snapshotted into backups/ first — restore never loses state."""
    data_dir, src = Path(data_dir), Path(src)
    with tempfile.TemporaryDirectory(prefix="soundbox_restore_") as td:
        root = _backup_root(src, Path(td))
        lib_file = root / "library.json"
        try:
            data = json.loads(lib_file.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
        except (OSError, ValueError, AssertionError):
            raise ValueError(
                f"No valid library.json in {src} — is this a SoundBox backup?")

        # Safety snapshot of the CURRENT state before any overwrite.
        cur = data_dir / "library.json"
        if cur.is_file():
            bdir = data_dir / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(
                cur, bdir / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}.json")

        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lib_file, cur)
        restored = {"overrides": len(data.get("artist_overrides") or {})
                    + len(data.get("title_overrides") or {}),
                    "custom_playlists": len(data.get("custom") or {}),
                    "history": 0, "art_files": 0}

        hist = root / "backups"
        if hist.is_dir():
            (data_dir / "backups").mkdir(parents=True, exist_ok=True)
            for f in hist.glob("*.json"):
                shutil.copyfile(f, data_dir / "backups" / f.name)
                restored["history"] += 1

        art = root / "art"
        if art.is_dir():
            (data_dir / "art").mkdir(parents=True, exist_ok=True)
            for f in art.iterdir():  # merge-overwrite; never deletes extras
                if f.is_file():
                    shutil.copyfile(f, data_dir / "art" / f.name)
                    restored["art_files"] += 1
        return restored
