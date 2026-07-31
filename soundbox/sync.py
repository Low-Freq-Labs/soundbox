"""Copy tracks and write Rockbox playlists to a target folder.

SAFETY CONTRACT (read every comment near a write):
  * The ONLY place this program writes is under `target`.
  * `dry_run=True` (the default for the `dry-run` command) performs NO writes
    at all: no mkdir, no copy, no playlist files. It only plans and reports.
  * Source files are only ever READ (copied via a temp file + atomic rename;
    see _safe_copy). They are never modified, moved, renamed, or deleted.
  * A guard refuses to run if `target` overlaps the source music library, so
    we can never write into the library.
  * Existing target files are never overwritten when their content differs;
    that is reported as a conflict and skipped.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast

from . import playlists as pl_mod
from . import metadata
from . import manifest
from .library import Library, Playlist
from .paths import device_music_path, is_within
from .report import PlaylistReport, SyncReport


class NotEnoughSpace(RuntimeError):
    """Raised before any write when the new files won't fit on the target device."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _destination_key(path: str) -> str:
    """FAT-safe identity: slash-normalized, Unicode-normalized, case-folded."""
    normalized = "/" + "/".join(
        part for part in str(path).replace("\\", "/").split("/") if part)
    return unicodedata.normalize("NFD", normalized).casefold()


class DeviceSession:
    """Pinned, revalidated authority for one mounted export target."""

    def __init__(
            self, target: Path, sources: list[Path], *, create: bool = True):
        requested = target.expanduser()
        assert_target_safe(requested, sources)
        # A generic explicit export folder may not exist yet. Creating exactly
        # that target is the session's first and only pre-identity write.
        if create:
            requested.mkdir(parents=True, exist_ok=True)
        elif not requested.is_dir():
            raise RuntimeError(f"Device target is no longer mounted: {requested}")
        self.target = requested.resolve(strict=True)
        assert_target_safe(self.target, sources)
        stat = self.target.stat()
        self._device = stat.st_dev
        self._inode = stat.st_ino

    def revalidate(self) -> None:
        current = self.target.resolve(strict=True)
        stat = current.stat()
        if (current != self.target or stat.st_dev != self._device
                or stat.st_ino != self._inode):
            raise RuntimeError(
                f"Device target changed or was remounted: {self.target}")

    def guard(self, path: Path) -> Path:
        self.revalidate()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.target / candidate
        # Resolve the nearest existing parent so a symlink cannot redirect a
        # later mkdir/copy beyond the pinned volume.
        parent = candidate.parent
        while not parent.exists() and parent != self.target:
            parent = parent.parent
        resolved_parent = parent.resolve(strict=True)
        if not is_within(resolved_parent, self.target):
            raise RuntimeError(
                f"REFUSING path outside device target: {candidate}")
        return candidate

    def atomic_copy(self, source: Path, destination: Path) -> None:
        target = self.guard(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.revalidate()
        _safe_copy(source, target)

    def atomic_write(self, destination: Path, data: bytes) -> None:
        target = self.guard(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.revalidate()
        tmp = target.with_name(target.name + ".sbpart")
        try:
            with tmp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self.revalidate()
            os.replace(tmp, target)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _fmt_size(n: int) -> str:
    """Human bytes in decimal units (matches Finder), for space messages."""
    f = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if f < 1000 or u == "TB":
            return f"{f:.1f} {u}"
        f /= 1000
    return f"{f:.1f} TB"


@dataclass
class PlannedFile:
    source: Path
    device_path: str          # '/Music/Artist/Track/Track.flac'
    target_path: Path
    size: int
    track_id: str = ""        # to look up this track's cover art


def _dirs_up_to(start_dir: Path, root: Path) -> list[Path]:
    """`start_dir` and each parent up to and including `root` (assumes start_dir is
    inside root — true for our constructed device paths)."""
    out, p = [], start_dir
    while True:
        out.append(p)
        if p == root or p.parent == p:
            break
        p = p.parent
    return out


def _clean_dir(d: Path) -> int:
    """Delete '._*' sidecars + '*.sbpart' temps directly inside d (shallow). Count."""
    removed = 0
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    for p in entries:
        if p.name.startswith("._") or p.name.endswith(".sbpart"):
            try:
                if p.is_file():
                    p.unlink()  # WRITE: our-folder clutter only.
                    removed += 1
            except OSError:
                pass
    return removed


def clean_applefiles(target: Path, dirs=None) -> int:
    """Delete device clutter — macOS AppleDouble ('._*') sidecars + orphan '*.sbpart'
    copy temps — under the target's Music/ and Playlists/ folders ONLY (never a sibling
    like a rekordbox PIONEER/ export; never any source).

    If `dirs` is given (the folders written this run), scan ONLY those, shallow — far
    faster than rglob'ing the whole device every time (the FINALIZING cost). Each dir
    is guarded to be within Music/ or Playlists/. `dirs=None` falls back to a full scan
    (manual 'clean device')."""
    removed = 0
    if dirs is not None:
        music, plists = target / "Music", target / "Playlists"
        for d in {Path(x) for x in dirs}:
            if is_within(d, music) or is_within(d, plists):  # safety: our trees only
                removed += _clean_dir(d)
        return removed
    for sub in ("Music", "Playlists"):
        base = target / sub
        if not base.is_dir():
            continue
        for pattern in ("._*", "*.sbpart"):
            for p in base.rglob(pattern):
                try:
                    if p.is_file():
                        p.unlink()  # WRITE: deletes our-folder clutter only.
                        removed += 1
                except OSError:
                    pass
    return removed


def erase_device_contents(target: Path, sources: list[Path]) -> int:
    """Permanently empty one explicitly selected mounted device.

    This is intentionally separate from ownership-based Sync pruning. It may
    remove unknown content, but only after the UI's explicit whole-device
    confirmation. The mount root itself is never removed. The SoundBox manifest
    is deleted last, so a partial erase retains its repair/eject warning rather
    than publishing an empty successful state.
    """
    session = DeviceSession(Path(target), sources, create=False)
    session.revalidate()
    entries = list(session.target.iterdir())
    ownership = manifest.manifest_path(session.target).parent
    entries.sort(key=lambda path: path == ownership)
    removed = 0
    errors: list[str] = []

    for path in entries:
        if path == ownership and errors:
            break
        try:
            session.revalidate()
            guarded = session.guard(path)
            if guarded.is_symlink():
                guarded.unlink()
            elif guarded.is_dir():
                shutil.rmtree(guarded)
            else:
                guarded.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    session.revalidate()
    if errors:
        raise RuntimeError(
            "Device was only partially erased; its SoundBox sync record was "
            "preserved:\n" + "\n".join(errors[:10]))

    # macOS can recreate these volume-index folders while a mounted device is
    # being observed. They contain no user media and do not constitute a
    # partial SoundBox erase.
    volatile = {".fseventsd", ".Spotlight-V100", ".Trashes"}
    remaining = [
        path.name for path in session.target.iterdir()
        if path.name not in volatile
    ]
    if remaining:
        raise RuntimeError(
            "Device was not empty after erase: " + ", ".join(remaining[:10]))
    return removed


def verify_managed(target: Path, *, deep: bool = False) -> list[str]:
    """Return integrity problems for manifest-managed device files.

    Normal verification checks references and sizes. Deep Verify additionally
    rehashes every managed file, including same-size corruption.
    """
    root = Path(target).expanduser().resolve()
    current = manifest.load(root)
    issues: list[str] = []
    seen: set[str] = set()
    for device_path, row in (current.get("files") or {}).items():
        key = _destination_key(str(device_path))
        if key in seen:
            issues.append(f"destination collision: {device_path}")
            continue
        seen.add(key)
        path = root / str(device_path).lstrip("/")
        try:
            resolved = path.resolve(strict=True)
            if not is_within(resolved, root) or not resolved.is_file():
                issues.append(f"missing or escaped: {device_path}")
                continue
            size = resolved.stat().st_size
            if size != int(row.get("size") or -1):
                issues.append(f"wrong size: {device_path}")
                continue
            if deep and _sha256(resolved) != str(row.get("sha256") or ""):
                issues.append(f"checksum mismatch: {device_path}")
        except OSError:
            issues.append(f"missing or unreadable: {device_path}")
    return issues


def _safe_copy(src: Path, dst: Path) -> None:
    """Copy file DATA to a temp file in the destination folder, then ATOMICALLY
    rename it into place.

    Writing straight to the final name means a crash / yanked USB / power loss
    mid-copy leaves a TRUNCATED file at the real name — which the next sync's
    size-only check then treats as a permanent 'conflict' and never heals. Copying
    to `<name>.sbpart` first and os.replace()-ing it in means an interruption leaves
    only an orphan temp (swept by clean_applefiles), and the real file only ever
    appears complete. os.replace is atomic within one filesystem, including FAT.

    copystat is best-effort: it raises EINVAL on macOS FAT/fskit, and the audio
    bytes are what matter. Only ever writes under `target`.
    """
    tmp = dst.with_name(dst.name + ".sbpart")
    try:
        shutil.copyfile(src, tmp)            # read-only src -> temp in dst's folder
        try:
            shutil.copystat(src, tmp)
        except OSError:
            pass                             # FAT can't store full metadata
        os.replace(tmp, dst)                 # atomic; dst never pre-exists on this path
    except OSError:
        try:
            tmp.unlink()                     # drop the partial temp so a re-run retries
        except OSError:
            pass
        raise


def assert_target_safe(target: Path, sources: list[Path]) -> None:
    """Refuse to proceed if the target overlaps any source location.

    Prevents ever writing into the source music library. Pure path math.
    """
    target = target.expanduser().resolve()
    for src in sources:
        if is_within(target, src.parent) or is_within(src, target):
            raise RuntimeError(
                f"REFUSING: target {target} overlaps source library near {src.parent}. "
                "Choose a target that is outside your music library."
            )


def plan(
    lib: Library,
    selected: list[Playlist],
    target: Path,
    derive_artists: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
) -> tuple[dict[str, PlannedFile], dict[int, list[pl_mod.M3UEntry]]]:
    """Build the copy plan + playlist entries. READ ONLY (no writes).

    Returns (unique_files_by_devicepath, entries_per_playlist_index).
    Deduplicates source files across playlists and resolves device-path
    collisions deterministically.
    """
    music_root = target / "Music"
    files: dict[str, PlannedFile] = {}          # device_path -> PlannedFile
    src_to_device: dict[str, str] = {}          # source str -> device_path
    entries_by_pl: dict[int, list[pl_mod.M3UEntry]] = {}

    for idx, plist in enumerate(selected):
        entries: list[pl_mod.M3UEntry] = []
        for track in lib.resolve(plist):
            src = track.path
            try:
                exists = src.is_file()
            except OSError:
                exists = False
            if not exists:
                entries.append(pl_mod.M3UEntry(None, track.artist, track.name))
                continue

            src_key = str(src)
            device_path = src_to_device.get(src_key)
            if device_path is None:
                override = (title_overrides or {}).get(track.track_id)
                artist = metadata.effective_artist(
                    track.track_id, override or track.name, track.artist,
                    artist_overrides, derive_artists,
                    cast("metadata.ArtistRegistry | None", lib.artist_registry),
                    stem=src.stem,
                )
                # The on-device filename is ALWAYS the SoundBox Track Title —
                # override, else the tidied rekordbox name — NEVER the on-disk
                # filename (disk names are often junk the user's curation has
                # already cleaned in the app; source files are never renamed).
                # sanitize/length-cap happens in device_music_path; a same-name
                # collision downstream gets a short source-hash suffix.
                title = metadata.effective_title(
                    track.track_id, track.name, artist, title_overrides,
                    getattr(lib, "title_junk", None))
                device_path = _assign_device_path(src, artist, files, music_root, title)
                src_to_device[src_key] = device_path
                files[device_path] = PlannedFile(
                    source=src,
                    device_path=device_path,
                    target_path=target / device_path.lstrip("/"),
                    size=src.stat().st_size,
                    track_id=track.track_id,
                )
            entries.append(pl_mod.M3UEntry(device_path, track.artist, track.name))
        entries_by_pl[idx] = entries
    return files, entries_by_pl


def _assign_device_path(
    src: Path, artist: str, files: dict[str, PlannedFile], music_root: Path,
    name_override: str | None = None,
) -> str:
    """Pick a unique device path, disambiguating collisions from *different*
    source files by appending a short source hash before the extension."""
    base = device_music_path(src, artist, name_override=name_override)
    occupied = {_destination_key(path) for path in files}
    if _destination_key(base) not in occupied:
        return base
    # FAT32 is case-insensitive and macOS/device firmware can disagree about
    # composed Unicode. Treat all equivalent spellings as the same name.
    stem, dot, ext = base.rpartition(".")
    suffix = hashlib.sha1(str(src).encode()).hexdigest()[:6]
    candidate = f"{stem}_{suffix}{dot}{ext}" if dot else f"{base}_{suffix}"
    counter = 2
    while _destination_key(candidate) in occupied:
        candidate = (
            f"{stem}_{suffix}-{counter}{dot}{ext}"
            if dot else f"{base}_{suffix}-{counter}")
        counter += 1
    return candidate


def run(
    lib: Library,
    selected: list[Playlist],
    target: Path,
    layout: str = "both",
    dry_run: bool = True,
    derive_artists: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
) -> SyncReport:
    """Execute (or simulate, if dry_run) the sync. All writes are under target.

    on_progress(done, total, label) is called once per file processed, for a
    live progress bar. should_cancel() is polled before each file; if it returns
    True the copy stops cleanly (files already copied are kept).
    """
    target = target.expanduser().resolve()

    files, entries_by_pl = plan(lib, selected, target, derive_artists=derive_artists,
                                artist_overrides=artist_overrides,
                                title_overrides=title_overrides)
    assert_target_safe(target, [pf.source for pf in files.values()])
    session = (
        DeviceSession(target, [pf.source for pf in files.values()])
        if not dry_run else None)
    previous_manifest = manifest.load(target) if not dry_run else {}
    managed_files = {
        str(path): dict(row)
        for path, row in (previous_manifest.get("files") or {}).items()
        if isinstance(row, dict)
    }
    selected_keys = {manifest.playlist_key(pl) for pl in selected}
    for row in managed_files.values():
        row["playlists"] = [
            key for key in (row.get("playlists") or [])
            if key not in selected_keys
        ]
    memberships: dict[str, set[str]] = {}
    for index, plist in enumerate(selected):
        key = manifest.playlist_key(plist)
        for entry in entries_by_pl[index]:
            if entry.device_path is not None:
                memberships.setdefault(entry.device_path, set()).add(key)
    source_hashes: dict[Path, str] = {}

    # --- Free-space precheck ---------------------------------------------
    # Refuse to start writing if the NEW files won't fit, with one clear message,
    # instead of failing file-by-file partway through a multi-GB copy (and leaving a
    # half-populated device). Two-tier so it's cheap: `gross` (sum of planned sizes)
    # uses sizes already gathered by plan() — NO device I/O. Only when the worst case
    # exceeds free space do we pay for the precise per-file "already present?" check.
    if not dry_run and files:
        try:
            st = os.statvfs(target)
            free = st.f_bavail * st.f_frsize
        except OSError:
            free = None
        if free is not None:
            gross = sum(pf.size for pf in files.values())
            if gross > free:
                present = 0
                for pf in files.values():
                    try:
                        if pf.target_path.exists() and pf.target_path.stat().st_size == pf.size:
                            present += pf.size  # already on the device — won't re-copy
                    except OSError:
                        pass
                need = gross - present
                if need > free:
                    raise NotEnoughSpace(
                        f"Not enough space on {target.name or target}: needs "
                        f"{_fmt_size(need)} but only {_fmt_size(free)} is free. "
                        f"Delete some tracks from the device or export fewer playlists.")

    report = SyncReport(dry_run=dry_run)
    playlists_root = target / "Playlists"
    music_root = target / "Music"

    # --- Copy phase -------------------------------------------------------
    copied_paths: set[str] = set()
    already: set[str] = set()
    conflicts: set[str] = set()
    global_errors: list[str] = []
    bytes_copied = 0
    written_dirs: set[Path] = set()  # folders we actually wrote → scoped AppleDouble scrub

    total = len(files)
    # Emit an immediate 0/total so the UI bar is DETERMINATE from the first frame
    # (no indeterminate "candy cane") — the verify/copy of many files can take a
    # while, and a real 0%→100% bar reads far better than a barber-pole.
    if on_progress:
        on_progress(0, total, "")
    cancelled = False
    for done, (device_path, pf) in enumerate(files.items(), 1):
        if should_cancel and should_cancel():
            cancelled = True
            break
        tgt = pf.target_path
        try:
            if session is not None:
                session.guard(tgt)
            source_stat = pf.source.stat()
            source_identity = (
                f"{source_stat.st_size}:{source_stat.st_mtime_ns}")
            previous = managed_files.get(device_path) or {}
            expected_hash = (
                str(previous.get("sha256") or "")
                if (previous.get("source_identity") == source_identity
                    and int(previous.get("size") or -1) == pf.size)
                else "")
            if not expected_hash:
                expected_hash = source_hashes.setdefault(
                    pf.source, _sha256(pf.source))
            was_present = tgt.exists()
            if was_present:
                actual_hash = (
                    _sha256(tgt) if tgt.stat().st_size == pf.size else "")
                if actual_hash == expected_hash:
                    already.add(device_path)
                elif device_path in managed_files and session is not None:
                    # Only a manifest-owned destination may be repaired.
                    session.atomic_copy(pf.source, tgt)
                    bytes_copied += pf.size
                    copied_paths.add(device_path)
                    written_dirs.update(_dirs_up_to(tgt.parent, music_root))
                else:
                    conflicts.add(device_path)
                    global_errors.append(f"CONFLICT (kept existing, not overwritten): {tgt}")
            elif dry_run:
                # Plan only: count as would-copy, write nothing.
                bytes_copied += pf.size
                copied_paths.add(device_path)
            else:
                # WRITE: session revalidates the pinned mount and atomic path.
                assert session is not None
                session.atomic_copy(pf.source, tgt)
                bytes_copied += pf.size  # count only AFTER a successful copy
                copied_paths.add(device_path)
                written_dirs.update(_dirs_up_to(tgt.parent, music_root))
            if device_path in already or device_path in copied_paths:
                managed_files[device_path] = {
                    "kind": "track",
                    "owned": (
                        bool(previous.get("owned", True))
                        if previous else not was_present),
                    "source": str(pf.source.resolve()),
                    "source_identity": source_identity,
                    "size": pf.size,
                    "sha256": expected_hash,
                    "playlists": sorted(memberships.get(device_path, ())),
                }
        except OSError as exc:
            global_errors.append(f"ERROR copying {pf.source} -> {tgt}: {exc}")
        # Drop this track's cover.jpg into its folder (Rockbox reads it per-track).
        if art_cache is not None and not dry_run and tgt.parent.is_dir():
            img = art_cache.image_path(pf.track_id)
            if img:
                cover = tgt.parent / "cover.jpg"
                cover_device_path = "/" + str(
                    cover.relative_to(target)).replace(os.sep, "/")
                try:
                    art_hash = _sha256(img)
                    previous_art = managed_files.get(cover_device_path) or {}
                    owned = bool(previous_art.get("owned", True))
                    was_present = cover.exists()
                    if (not cover.exists()
                            or (previous_art and owned
                                and _sha256(cover) != art_hash)):
                        assert session is not None
                        session.atomic_copy(img, cover)
                        written_dirs.update(
                            _dirs_up_to(tgt.parent, music_root))
                    elif not previous_art and _sha256(cover) != art_hash:
                        raise OSError(
                            f"unowned artwork conflict at {cover}")
                    art_stat = img.stat()
                    managed_files[cover_device_path] = {
                        "kind": "artwork",
                        "owned": (
                            bool(previous_art.get("owned", True))
                            if previous_art else not was_present),
                        "source": str(img.resolve()),
                        "source_identity":
                            f"{art_stat.st_size}:{art_stat.st_mtime_ns}",
                        "size": art_stat.st_size,
                        "sha256": art_hash,
                        "playlists": sorted(
                            memberships.get(device_path, ())),
                    }
                except OSError as exc:
                    global_errors.append(
                        f"ERROR writing artwork {img} -> {cover}: {exc}")
        if on_progress:
            on_progress(done, total, pf.source.name)

    report.bytes_to_copy = bytes_copied

    # --- Playlist phase ---------------------------------------------------
    # Runs even after a cancel: every playlist still gets an HONEST m3u8 of what
    # made it onto the device, so finished/partial playlists are usable NOW and
    # the incomplete ones are flagged (poisoned sig) for the next Sync to finish.
    incomplete: set[str] = set()
    used_playlist_paths: dict[str, str] = {}
    for idx, plist in enumerate(selected):
        entries = entries_by_pl[idx]
        pr = PlaylistReport(name=plist.display_path)
        pr.in_playlist = len(entries)
        pr.found = sum(1 for e in entries if e.device_path is not None)
        pr.missing = sum(1 for e in entries if e.device_path is None)
        pr.copied = sum(1 for e in entries if e.device_path in copied_paths)
        pr.already_present = sum(1 for e in entries if e.device_path in already)
        pr.conflicts = sum(1 for e in entries if e.device_path in conflicts)

        if cancelled:
            # Cancel semantics: the playlist file must describe what's actually ON
            # the device — a playable partial, not a full list with dead entries.
            # (Entries not yet processed may still exist from an earlier export, so
            # fall back to a stat for those.)
            kept, skipped = [], 0
            for e in entries:
                if e.device_path is None:
                    kept.append(e)  # keep the "# MISSING:" comment (source gone)
                elif (e.device_path in copied_paths or e.device_path in already
                        or (target / e.device_path.lstrip("/")).exists()):
                    kept.append(e)
                else:
                    skipped += 1
            text = pl_mod.render_m3u8(kept)
            if skipped:
                incomplete.add(manifest.playlist_key(plist))
                text += (f"# CANCELLED: {skipped} track(s) not exported — "
                         f"sync the device to finish\n")
        else:
            text = pl_mod.render_m3u8(entries)
        out_paths: list[Path] = []
        if layout in ("tree", "both"):
            out_paths.append(pl_mod.tree_playlist_path(playlists_root, plist.folder_path, plist.name))
        if layout in ("flat", "both"):
            out_paths.append(pl_mod.flat_playlist_path(playlists_root, plist.folder_path, plist.name))

        resolved_paths: list[Path] = []
        playlist_identity = manifest.playlist_key(plist)
        for op in out_paths:
            key = _destination_key("/" + str(op.relative_to(target)))
            owner = used_playlist_paths.get(key)
            if owner is not None and owner != playlist_identity:
                suffix = hashlib.sha1(
                    playlist_identity.encode("utf-8")).hexdigest()[:6]
                op = op.with_name(f"{op.stem}_{suffix}{op.suffix}")
                key = _destination_key("/" + str(op.relative_to(target)))
            used_playlist_paths[key] = playlist_identity
            resolved_paths.append(op)

        for op in resolved_paths:
            pr.playlist_files.append(str(op))
            if not dry_run:
                device_playlist_path = "/" + str(
                    op.relative_to(target)).replace(os.sep, "/")
                previous_playlist = managed_files.get(
                    device_playlist_path) or {}
                try:
                    if op.exists() and not previous_playlist:
                        raise OSError(
                            f"unowned playlist conflict at {op}")
                    assert session is not None
                    encoded = text.encode("utf-8")
                    session.atomic_write(op, encoded)
                    managed_files[device_playlist_path] = {
                        "kind": "playlist",
                        "owned": True,
                        "source": playlist_identity,
                        "source_identity": manifest.playlist_sig(
                            plist,
                            getattr(lib, "hidden_ids", frozenset()),
                            getattr(lib, "removed_by_playlist", None)),
                        "size": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "playlists": [playlist_identity],
                    }
                    written_dirs.update(
                        _dirs_up_to(op.parent, playlists_root))
                except OSError as exc:
                    incomplete.add(playlist_identity)
                    pr.errors.append(
                        f"ERROR writing playlist {op}: {exc}")

        report.playlists.append(pr)

    report.written_dirs = written_dirs

    if global_errors and report.playlists:
        report.playlists[0].errors.extend(global_errors)
        incomplete.update(selected_keys)

    # Record what we put on the device (by stable playlist ID) so later syncs can
    # detect drift. Writes only under target (<device>/.soundbox/manifest.json).
    report.cancelled = cancelled
    if not dry_run:
        try:
            assert session is not None
            session.revalidate()
            manifest.record_export(target, selected, incomplete,
                                   hidden=getattr(lib, "hidden_ids", frozenset()),
                                   removals=getattr(lib, "removed_by_playlist", None),
                                   managed_files=managed_files,
                                   completed=not cancelled and not global_errors)
            session.revalidate()
        except OSError as exc:
            if report.playlists:
                report.playlists[0].errors.append(
                    f"ERROR writing SoundBox manifest: {exc}")
    return report


def _prune_music(music_root: Path, keep_dirs: set[Path]) -> int:
    """Delete track folders under Music that no remaining playlist references. Only
    ever removes whole <Artist>/<Track>/ dirs strictly inside music_root (never
    outside it), taking each track's cover.jpg with it. Returns folders removed."""
    if not music_root.is_dir():
        return 0
    removed = 0
    for artist_dir in list(music_root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        for track_dir in list(artist_dir.iterdir()):
            if not track_dir.is_dir():
                continue
            if track_dir.resolve() in keep_dirs:
                continue
            if not is_within(track_dir, music_root):  # paranoia guard before rmtree
                continue
            shutil.rmtree(track_dir, ignore_errors=True)
            removed += 1
        try:  # drop the artist folder if it's now empty
            if not any(artist_dir.iterdir()):
                artist_dir.rmdir()
        except OSError:
            pass
    return removed


def _prune_manifest_owned(
        session: DeviceSession, rows: dict[str, dict], *,
        media: bool) -> int:
    """Delete only unreferenced paths explicitly owned by manifest v2."""
    removed = 0
    emptied: set[Path] = set()
    for device_path, row in list(rows.items()):
        if row.get("playlists"):
            continue
        kind = str(row.get("kind") or "")
        if kind != "playlist" and not media:
            continue
        path = session.guard(
            session.target / str(device_path).lstrip("/"))
        if bool(row.get("owned", False)):
            try:
                path.unlink(missing_ok=True)
                removed += 1
                emptied.add(path.parent)
            except OSError:
                continue
        # An unowned matching file was referenced but never claimed. Forget
        # the reference without deleting its bytes.
        rows.pop(device_path, None)
    for directory in sorted(
            emptied, key=lambda path: len(path.parts), reverse=True):
        for candidate in (directory, directory.parent):
            if candidate == session.target:
                continue
            try:
                session.guard(candidate)
                candidate.rmdir()
            except OSError:
                pass
    return removed


def _prune_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for d in sorted((p for p in root.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


def _dropped_tracks(prev: dict, by_key: dict, changed: list,
                    hidden=frozenset(), removals=None) -> bool:
    """True if any 'changed' playlist lost a track it previously had on the device
    (the only case besides a deleted playlist that can orphan audio). A pure add,
    reorder, rename, or move never orphans. A SoundBox delete OR per-playlist
    removal counts as lost — its file must be pruned off the device."""
    for key, _name in changed:
        old = set(prev.get(key, {}).get("tracks", []))
        cur = by_key.get(key)
        new = (set(cur.keys) - manifest._excluded(cur, hidden, removals)
               if cur is not None else set())
        if old - new:
            return True
    return False


def resync(
    lib: Library,
    target: Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
    prune: bool = True,
    extra=None,
) -> SyncReport:
    """Bring a device back in line with rekordbox using its manifest — DIFF-DRIVEN:
    only the playlists that actually drifted (renamed / moved / tracks changed) are
    re-exported; unchanged playlists are already correct on the device and left alone.
    Stale m3u8s (renamed/moved/removed) are deleted, music is pruned ONLY when a track
    could have been orphaned, and playlists deleted from rekordbox are forgotten.
    Writes ONLY under target.

    `extra` = custom SoundBox playlists (not in lib.playlists), so a synced custom
    playlist is recognized and updated rather than deleted as 'removed'.

    NOTE: this trusts the manifest — it won't re-copy a file you manually deleted off
    the device (all rekordbox-driven edits are handled). Full re-verify isn't the goal."""
    target = Path(target).expanduser()
    m = manifest.load(target)
    prev = m.get("playlists", {})
    by_key = {manifest.playlist_key(p): p for p in list(lib.playlists) + list(extra or [])}

    wanted = [by_key[k] for k in prev if k in by_key]     # still exist -> current version
    removed_keys = [k for k in prev if k not in by_key]   # deleted in rekordbox

    drift = manifest.diff(m, lib, extra)
    drifted_keys = {k for k, *_ in (drift.renamed + drift.moved + drift.changed)}
    to_export = [p for p in wanted if manifest.playlist_key(p) in drifted_keys]

    # 1) Re-export ONLY the drifted playlists (copy any new files + rewrite their m3u8s
    #    + upsert their manifest entries). Unchanged playlists are skipped entirely —
    #    the big win: no re-verifying every file of every synced playlist each time.
    report = run(lib, to_export, target, layout="tree", dry_run=False,
                 on_progress=on_progress, should_cancel=should_cancel,
                 artist_overrides=artist_overrides, title_overrides=title_overrides,
                 art_cache=art_cache)

    # 2) Stale playlists are removed below through manifest ownership only.

    # 3) Prune orphan music — ONLY when something could actually be orphaned (a playlist
    #    was removed, or a changed one dropped a track). The keep-set spans ALL wanted
    #    playlists so shared files stay safe.
    #    Cancelled? Skip the heavy deletes (a cancel means STOP) — and skip step 4 too,
    #    so the removed keys stay in the manifest and the NEXT Sync re-detects and
    #    finishes the cleanup. (Step 2 above still ran: it's a few unlinks, and the
    #    manifest has already moved past the old names, so leaving them would leak
    #    stale m3u8s forever.)
    # (report.cancelled only reflects the copy loop — with nothing to copy it was
    # never polled, so ask should_cancel directly before the destructive steps.)
    stopped = report.cancelled or bool(should_cancel and should_cancel())
    report.cancelled = stopped
    media_prune = bool(
        prune and not stopped and (
            removed_keys or _dropped_tracks(
                prev, by_key, drift.changed,
                getattr(lib, "hidden_ids", frozenset()),
                getattr(lib, "removed_by_playlist", None))))

    # 4) Forget playlists that no longer exist in rekordbox.
    if not stopped:
        mm = manifest.load(target)
        for k in removed_keys:
            mm["playlists"].pop(k, None)
        for row in (mm.get("files") or {}).values():
            row["playlists"] = [
                key for key in (row.get("playlists") or [])
                if key not in removed_keys
            ]
        sources = [
            track.path for track in lib.tracks_by_id.values()
            if track.path.is_file()
        ]
        session = DeviceSession(target, sources)
        _prune_manifest_owned(
            session, mm.setdefault("files", {}), media=media_prune)
        session.revalidate()
        manifest.save(target, mm)

    if not stopped:
        clean_applefiles(
            target, report.written_dirs)  # only folders we rewrote
    return report
