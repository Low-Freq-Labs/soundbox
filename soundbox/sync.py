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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast

from . import playlists as pl_mod
from . import metadata
from . import manifest
from .paths import device_music_path, is_within
from .report import PlaylistReport, SyncReport
from .rekordbox_xml import Library, Playlist


class NotEnoughSpace(RuntimeError):
    """Raised before any write when the new files won't fit on the target device."""


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
    if base not in files:
        return base
    # Collision: same device path wanted by a different source file.
    stem, dot, ext = base.rpartition(".")
    suffix = hashlib.sha1(str(src).encode()).hexdigest()[:6]
    return f"{stem}_{suffix}{dot}{ext}" if dot else f"{base}_{suffix}"


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
    target = target.expanduser()

    files, entries_by_pl = plan(lib, selected, target, derive_artists=derive_artists,
                                artist_overrides=artist_overrides,
                                title_overrides=title_overrides)
    assert_target_safe(target, [pf.source for pf in files.values()])

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
            if tgt.exists():
                # Fast idempotency: same size => treat as already present (no hashing).
                # Different size => real conflict, never auto-overwrite.
                if tgt.stat().st_size == pf.size:
                    already.add(device_path)
                else:
                    conflicts.add(device_path)
                    global_errors.append(f"CONFLICT (kept existing, not overwritten): {tgt}")
            elif dry_run:
                # Plan only: count as would-copy, write nothing.
                bytes_copied += pf.size
                copied_paths.add(device_path)
            else:
                # WRITE: create dirs + copy file data. Target only.
                tgt.parent.mkdir(parents=True, exist_ok=True)
                _safe_copy(pf.source, tgt)
                bytes_copied += pf.size  # count only AFTER a successful copy
                copied_paths.add(device_path)
                written_dirs.update(_dirs_up_to(tgt.parent, music_root))
        except OSError as exc:
            global_errors.append(f"ERROR copying {pf.source} -> {tgt}: {exc}")
        # Drop this track's cover.jpg into its folder (Rockbox reads it per-track).
        if art_cache is not None and not dry_run and tgt.parent.is_dir():
            img = art_cache.image_path(pf.track_id)
            if img:
                cover = tgt.parent / "cover.jpg"
                if not cover.exists():
                    try:
                        shutil.copyfile(img, cover)
                        written_dirs.update(_dirs_up_to(tgt.parent, music_root))
                    except OSError:
                        pass
        if on_progress:
            on_progress(done, total, pf.source.name)

    report.bytes_to_copy = bytes_copied

    # --- Playlist phase ---------------------------------------------------
    # Runs even after a cancel: every playlist still gets an HONEST m3u8 of what
    # made it onto the device, so finished/partial playlists are usable NOW and
    # the incomplete ones are flagged (poisoned sig) for the next Sync to finish.
    incomplete: set[str] = set()
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

        for op in out_paths:
            pr.playlist_files.append(str(op))
            if not dry_run:
                # WRITE: playlist file, UTF-8 no BOM. Target only.
                op.parent.mkdir(parents=True, exist_ok=True)
                op.write_text(text, encoding="utf-8")
                written_dirs.update(_dirs_up_to(op.parent, playlists_root))

        report.playlists.append(pr)

    report.written_dirs = written_dirs

    if global_errors and report.playlists:
        report.playlists[0].errors.extend(global_errors)

    # Record what we put on the device (by stable playlist ID) so later syncs can
    # detect drift. Writes only under target (<device>/.soundbox/manifest.json).
    report.cancelled = cancelled
    if not dry_run:
        try:
            manifest.record_export(target, selected, incomplete,
                                   hidden=getattr(lib, "hidden_ids", frozenset()),
                                   removals=getattr(lib, "removed_by_playlist", None))
        except OSError:
            pass  # a manifest we couldn't write is not worth failing an export over
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

    playlists_root = target / "Playlists"
    old_m3u8 = {pl_mod.tree_playlist_path(playlists_root, e.get("folder_path", []),
                                          e.get("name", "")) for e in prev.values()}

    # 1) Re-export ONLY the drifted playlists (copy any new files + rewrite their m3u8s
    #    + upsert their manifest entries). Unchanged playlists are skipped entirely —
    #    the big win: no re-verifying every file of every synced playlist each time.
    report = run(lib, to_export, target, layout="tree", dry_run=False,
                 on_progress=on_progress, should_cancel=should_cancel,
                 artist_overrides=artist_overrides, title_overrides=title_overrides,
                 art_cache=art_cache)

    # 2) Delete stale m3u8s (old paths from renamed/moved/removed playlists). Compared
    #    against the CURRENT paths of ALL wanted playlists (unchanged ones keep theirs).
    new_m3u8 = {pl_mod.tree_playlist_path(playlists_root, p.folder_path, p.name)
                for p in wanted}
    for op in old_m3u8 - new_m3u8:
        try:
            op.unlink(missing_ok=True)
        except OSError:
            pass
    _prune_empty_dirs(playlists_root)

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
    if prune and not stopped and (
            removed_keys or _dropped_tracks(prev, by_key, drift.changed,
                                            getattr(lib, "hidden_ids", frozenset()),
                                            getattr(lib, "removed_by_playlist", None))):
        files, _ = plan(lib, wanted, target, artist_overrides=artist_overrides,
                        title_overrides=title_overrides)
        keep = {pf.target_path.parent.resolve() for pf in files.values()}
        _prune_music(target / "Music", keep)

    # 4) Forget playlists that no longer exist in rekordbox.
    if removed_keys and not stopped:
        mm = manifest.load(target)
        for k in removed_keys:
            mm["playlists"].pop(k, None)
        manifest.save(target, mm)

    clean_applefiles(target, report.written_dirs)  # only the folders we rewrote
    return report
