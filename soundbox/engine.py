"""High-level, UI-facing API — the single surface the desktop app calls.

Returns plain, JSON-serializable structures (dicts/lists), so the heavy modules
stay UI-agnostic and unit-testable. All real logic lives in the other modules;
this just composes them. Nothing here writes outside an explicit target (it
delegates writes to sync.run, which enforces the safety guard).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import manifest as manifest_mod, metadata, rekordbox_xml, sync as sync_mod
from .library import Library, Playlist, Track


def load_library(xml_path: str | Path) -> Library:
    """Parse a rekordbox XML export (read-only)."""
    return rekordbox_xml.load(xml_path)


def playlist_tree(lib: Library) -> dict:
    """Nested folder/playlist tree for the sidebar (mirrors rekordbox)."""
    root: dict = {"name": "Playlists", "type": "folder", "children": []}
    index: dict[tuple[str, ...], dict] = {(): root}

    for pl in lib.playlists:
        path: tuple[str, ...] = ()
        parent = root
        for part in pl.folder_path:
            path = path + (part,)
            node = index.get(path)
            if node is None:
                node = {"name": part, "type": "folder", "children": []}
                parent["children"].append(node)
                index[path] = node
            parent = node
        parent["children"].append({
            "name": pl.name,
            "type": "playlist",
            "count": len(lib.resolve(pl)),  # honest count (respects hide_untracked)
            "path": pl.display_path,  # stable id used to fetch rows / select
        })
    return root


def _find(lib: Library, display_path: str) -> Playlist | None:
    return next((p for p in lib.playlists if p.display_path == display_path), None)


def track_row(track: Track, overrides: dict | None = None,
              titles: dict | None = None, derive: bool = True,
              registry=None, junk=None) -> dict:
    """A table row with the *effective* title and artist (overrides win)."""
    r = track.to_row()
    # Artist first (derived from the raw/overridden name), THEN the neat title —
    # which may strip that very artist off the title's edge.
    src = (titles or {}).get(track.track_id) or track.name
    artist = metadata.effective_artist(
        track.track_id, src, track.artist, overrides, derive, registry,
        stem=track.path.stem)
    r["artist"] = artist
    r["title"] = metadata.effective_title(track.track_id, track.name, artist, titles, junk)
    return r


def playlist_rows(lib: Library, display_path: str, overrides: dict | None = None,
                  titles: dict | None = None, derive: bool = True) -> list[dict]:
    """Track rows for one playlist, in exact rekordbox order."""
    pl = _find(lib, display_path)
    if pl is None:
        return []
    reg = lib.artist_registry
    junk = getattr(lib, "title_junk", None)
    return [track_row(t, overrides, titles, derive, reg, junk)
            for t in lib.resolve(pl)]


def summary(lib: Library) -> dict:
    """Top-level info for the UI on load."""
    return {
        "source": str(lib.source),
        "sourceKind": lib.source_kind,
        "trackCount": lib.track_count,
        "playlistCount": len(lib.playlists),
        "tree": playlist_tree(lib),
    }


def export_playlists(
    lib: Library,
    playlists: list,
    target: str | Path,
    *,
    dry_run: bool = False,
    derive_artists: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    clean: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
):
    """Export already-resolved Playlist objects (rekordbox OR custom SoundBox).

    Always uses tree layout (mirror folders, exact playlist names). Set
    clean=False to skip the AppleDouble scrub (do it once after a batch instead
    of per playlist). Writes only under target (sync.run enforces the guard).
    """
    target = Path(target)
    report = sync_mod.run(
        lib, playlists, target,
        layout="tree", dry_run=dry_run, derive_artists=derive_artists,
        on_progress=on_progress, should_cancel=should_cancel,
        artist_overrides=artist_overrides, title_overrides=title_overrides,
        art_cache=art_cache,
    )
    if not dry_run and clean:
        sync_mod.clean_applefiles(target, report.written_dirs)
    return report


def clean_device(target: str | Path, dirs=None) -> int:
    """Scrub macOS AppleDouble clutter from a target's Music/ + Playlists/. Pass
    `dirs` (the folders written this run) to scrub only those (fast); None = full scan."""
    return sync_mod.clean_applefiles(Path(target), dirs)


def _rockbox_art_advisory(target: Path) -> str | None:
    """Read-only check of the device's Rockbox art setting. SoundBox writes a
    curated cover.jpg for EVERY track (the universal floor); Rockbox's default
    lets embedded MP3/M4A art win where present — accepted by design (user
    decision 2026-07-04). The ONLY state worth flagging is art turned fully
    OFF, because then nothing shows at all."""
    cfg = target / ".rockbox" / "config.cfg"
    if not cfg.is_file():
        return None
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("album art:"):
                if line.split(":", 1)[1].strip().lower() == "off":
                    return ("Rockbox album art is OFF on this device — no covers "
                            "will show. On the device: Settings → Theme Settings "
                            "→ Album Art.")
    except OSError:
        pass
    return None


def verify_export(target: str | Path, *, deep: bool = False) -> dict:
    """Re-read every generated .m3u8 under the target and confirm each referenced
    file actually exists on the device. READ-ONLY — the post-export
    "did everything land?" check (run it off the UI thread; it walks the USB).
    Returns {"playlists": n, "entries": n, "missing": [(playlist, entry), ...]}."""
    target = Path(target)
    res: dict = {
        "playlists": 0, "entries": 0, "missing": [], "advisories": [],
    }
    managed_issues = sync_mod.verify_managed(Path(target), deep=deep)
    if deep or managed_issues:
        res["managed_issues"] = managed_issues
    adv = _rockbox_art_advisory(target)
    if adv:
        res["advisories"].append(adv)
    pdir = target / "Playlists"
    if not pdir.is_dir():
        return res
    for m3u in sorted(pdir.rglob("*.m3u8")):
        if m3u.name.startswith("._"):  # macOS AppleDouble sidecars on FAT
            continue
        res["playlists"] += 1
        try:
            text = m3u.read_text(encoding="utf-8")
        except OSError:
            res["missing"].append((str(m3u.relative_to(target)), "<unreadable>"))
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            res["entries"] += 1
            if not (target / line.lstrip("/")).is_file():
                res["missing"].append((str(m3u.relative_to(target)), line))
    return res


def device_drift(lib: Library, target: str | Path, extra=None):
    """How far a device has drifted from the current library since last sync
    (renamed/moved/changed/removed playlists). Read-only. None if no manifest.

    `extra` = custom SoundBox playlists (not in lib.playlists) so they aren't
    mis-counted as removed."""
    m = manifest_mod.load(Path(target))
    if not m.get("playlists"):
        return None
    return manifest_mod.diff(m, lib, extra)


def resync_device(
    lib: Library,
    target: str | Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
    extra=None,
):
    """Bring a device in line with rekordbox (see sync.resync). Writes only under target.

    `extra` = custom SoundBox playlists so they're kept/updated, not deleted as 'removed'."""
    return sync_mod.resync(
        lib, Path(target), on_progress=on_progress, should_cancel=should_cancel,
        artist_overrides=artist_overrides, title_overrides=title_overrides,
        art_cache=art_cache, extra=extra)
