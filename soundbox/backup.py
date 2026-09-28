"""Full-library backup & restore (the "thorough" backup, in-app).

A backup is one .zip holding everything SoundBox knows that isn't rekordbox:
    library.json   title/artist overrides, custom/source playlist additions,
                   deletes/removals, settings — the crown jewels.
    backups/       the rotating library.json history.
    art/           the whole cover-art cache (index + one image per TrackID).
    README.txt     what this is + how to restore by hand.

Restore accepts the .zip OR a folder laid out the same way (e.g. an unzipped
backup, or the app-data dir itself) OR a path to a library.json inside one.
Restore stages and validates a merged replacement, then publishes under the
shared state owner with exact rollback and restart recovery. Never touches sources.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import os
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .state_io import atomic_bytes, copy_state, plain_tree, state_area
from .state_repository import read_state

_ITEMS = ("library.json", "backups", "art")

_README = """SoundBox full backup — {date}
=================================
library.json   your portable SoundBox library: title/artist overrides, custom
               playlists, Rockbox-adopted membership, deletes/removals,
               settings. Keyed by rekordbox
               TrackID — the crown jewels.
backups/       rotating history of library.json (earlier states).
art/           the whole cover-art cache (index.json + one image per TrackID +
               managed default-photo pool), all baseline-JPEG (iPod-safe).
               Saves an hour+ of re-fetching and preserves uploaded defaults.

RESTORE: Settings -> "Restore from backup" and pick this .zip (or the
library.json inside the unzipped folder). By hand: quit Soundbox, copy
library.json (and art/ if wanted) into
~/Library/Application Support/LowFreqLabs/Soundbox/ and relaunch.
"""


# Bounds apply before extraction as well as to plain folder input.
_MAX_FILES = 100_000
_MAX_BYTES = 8 * 1024**3
_MAX_FILE_BYTES = 512 * 1024**2


def _validate_bundle(root: Path) -> None:
    try:
        read_state(root / "library.json")
    except (OSError, ValueError) as exc:
        raise ValueError(f"No valid library.json in {root}: {exc}") from exc
    files = []
    for item in _ITEMS:
        path = root / item
        if path.exists() or path.is_symlink():
            files.extend(plain_tree(path))
    if len(files) > _MAX_FILES:
        raise ValueError("Backup contains too many files")
    sizes = [p.stat().st_size for p in files]
    if sum(sizes) > _MAX_BYTES or any(n > _MAX_FILE_BYTES for n in sizes):
        raise ValueError("Backup exceeds the state size limit")
    for relative in ("art/index.json", "art/defaults/index.json"):
        path = root / relative
        if not path.exists():
            continue
        if path.stat().st_size > 64 * 1024**2:
            raise ValueError(f"{relative} exceeds the JSON size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Invalid {relative}")
        if relative == "art/index.json":
            for tid, entry in value.items():
                if not isinstance(entry, dict):
                    raise ValueError("Invalid artwork entry")
                if entry.get("status") == "ok":
                    filename = f"{tid}.{entry.get('ext', 'jpg')}"
                    if Path(filename).name != filename or not (root / "art" / filename).is_file():
                        raise ValueError(f"Missing or invalid artwork: {tid}")
        else:
            if (value.get("schema") != 1 or not isinstance(value.get("seed"), str)
                    or not value["seed"] or not isinstance(value.get("photos"), dict)
                    or not isinstance(value.get("assignments", {}), dict)):
                raise ValueError("Invalid default-photo index")
            for pid, row in value["photos"].items():
                if (not isinstance(row, dict) or len(pid) != 64
                        or any(c not in "0123456789abcdef" for c in pid)
                        or not (path.parent / f"{pid}.jpg").is_file()):
                    raise ValueError("Missing or invalid default photo")


def export_backup(data_dir, dest_zip) -> int:
    """Capture one consistent state under the owner; compress after releasing it.

    Existing destination archives survive failures. No partial zip is published.
    """
    data_dir, dest_zip = Path(data_dir), Path(dest_zip)
    area = state_area(data_dir)
    # A backup inside its own input would recursively archive itself/history.
    if dest_zip.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError("Choose a backup destination outside SoundBox state")
    with tempfile.TemporaryDirectory(prefix="soundbox_backup_") as td:
        snapshot = Path(td)
        with area.lock:
            _validate_bundle(data_dir)
            for item in _ITEMS:
                source = data_dir / item
                if source.exists():
                    copy_state(source, snapshot / item)
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{dest_zip.name}-", dir=dest_zip.parent)
        os.close(fd)
        tmp = Path(temp_name)
        n = 0
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("README.txt", _README.format(date=time.strftime("%Y-%m-%d")))
                for item in _ITEMS:
                    source = snapshot / item
                    if not source.exists():
                        continue
                    for f in plain_tree(source):
                        if f.name.startswith("."):
                            continue
                        kind = (zipfile.ZIP_STORED if f.suffix.lower() in (".jpg", ".jpeg")
                                else zipfile.ZIP_DEFLATED)
                        z.write(f, str(f.relative_to(snapshot)), compress_type=kind)
                        n += 1
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, dest_zip)
            from .state_io import sync_dir
            sync_dir(dest_zip.parent)
            return n
        finally:
            tmp.unlink(missing_ok=True)


def _backup_root(src: Path, tmp: Path) -> Path:
    """Resolve local input, rejecting links, unsafe names and unbounded archives."""
    if src.is_symlink():
        raise ValueError("Backup input cannot be a symlink")
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            entries = z.infolist()
            if (len(entries) > _MAX_FILES or sum(e.file_size for e in entries) > _MAX_BYTES
                    or any(e.file_size > _MAX_FILE_BYTES for e in entries)):
                raise ValueError("Backup archive exceeds the state size limit")
            seen = set()
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                mode = entry.external_attr >> 16
                if (path.is_absolute() or not path.parts or ".." in path.parts
                        or "\\" in name or str(path) != name.rstrip("/")
                        or path.parts[0] not in (*_ITEMS, "README.txt")
                        or stat.S_ISLNK(mode)
                        or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
                        or str(path).casefold() in seen):
                    raise ValueError(f"Unsafe backup member: {name}")
                seen.add(str(path).casefold())
            for entry in entries:
                target_path = tmp / entry.filename
                if entry.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(entry) as source, target_path.open("xb") as target:
                        shutil.copyfileobj(source, target)
        return tmp
    if src.is_file() and src.name == "library.json":
        return src.parent
    if src.is_dir():
        return src
    raise ValueError(f"Not a SoundBox backup: {src}")


def restore_backup(data_dir, src) -> dict:
    """Stage/validate first; publish library, history and artwork as one unit.

    Unrelated existing artwork/history are preserved (legacy merge semantics).
    Originals survive every failed publish and interrupted process. The area
    generation fences consumers holding pre-restore in-memory snapshots.
    """
    data_dir, src = Path(data_dir), Path(src)
    area = state_area(data_dir)
    with tempfile.TemporaryDirectory(prefix="soundbox_restore_") as td:
        root = _backup_root(src, Path(td) / "input")
        _validate_bundle(root)
        staged = Path(td) / "merged"
        staged.mkdir()
        with area.lock:
            # Freeze the entire source before touching any destination, including
            # when the selected backup is the current state directory itself.
            for item in _ITEMS:
                old = data_dir / item
                if old.exists():
                    copy_state(old, staged / item)
                source = root / item
                if source.exists():
                    copy_state(source, staged / item)
            _validate_bundle(staged)
            current = data_dir / "library.json"
            if current.exists():
                atomic_bytes(staged / "backups" /
                             f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.json",
                             current.read_bytes())
            data = read_state(staged / "library.json")
            restored = {"overrides": len(data.get("artist_overrides", {}))
                        + len(data.get("title_overrides", {})),
                        "custom_playlists": len(data.get("custom", {})),
                        "history": len(list((root / "backups").glob("*.json"))),
                        "art_files": len(plain_tree(root / "art")) if (root / "art").exists() else 0}
            area.publish({item: staged / item for item in _ITEMS if (staged / item).exists()})
            area.generation += 1
            area.art_revision += 1
            return restored
