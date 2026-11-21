"""UI-facing library presentation and filesystem-export composition helpers.

Returns plain, JSON-serializable structures (dicts/lists), so the heavy modules
stay UI-agnostic and unit-testable. All real logic lives in the other modules;
this just composes them. Nothing here writes outside an explicit target (it
delegates writes to sync.run, which enforces the safety guard).
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from typing import Callable

from . import manifest as manifest_mod, metadata, rekordbox_xml
from .library import Library, Playlist, Track


def load_library(xml_path: str | Path) -> Library:
    """Parse a rekordbox XML export (read-only)."""
    return rekordbox_xml.load(xml_path)


def playlist_tree(lib: Library) -> dict:
    """Nested folder/playlist tree for the sidebar (mirrors rekordbox)."""
    root: dict = {"name": "Playlists", "type": "folder", "children": []}
    catalog=getattr(lib,'source_playlist_catalog',{})
    if catalog:
        active=[r for r in catalog['nodes'] if r.get('rb_local_deleted')==0 and r.get('Attribute') in (0,1,4)]
        children: dict[str,list] = {}
        for row in active:children.setdefault(row['ParentID'],[]).append(row)
        visible={p.pid:p for p in lib.playlists}
        def walk_native(parent,path,seen):
            out=[]
            for row in sorted(children.get(parent,[]),key=lambda r:r['Seq']):
                if row['ID'] in seen:raise ValueError('Native playlist folder cycle')
                if row['Attribute']==1:
                    out.append(dict(name=row['Name'],type='folder',pid=row['ID'],attribute=row['Attribute'],
                                    children=walk_native(row['ID'],path+[row['Name']],seen|{row['ID']})))
                elif row['ID'] in visible:
                    pl=visible[row['ID']]
                    out.append(dict(name=pl.name,type='playlist',pid=pl.pid,attribute=row['Attribute'],
                                    count=len(lib.resolve(pl)),path=pl.display_path))
            return out
        root['children']=walk_native('root',[],set())
        return root
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
    rockbox_media_cache=None,
    rockbox_art_cache=None,
    display_rows: Mapping[str, Mapping] | None = None,
    validate_inputs: Callable[[], None] | None = None,
    validate_artwork: Callable[[str], None] | None = None,
    expected_manifest_payload: bytes | None = None,
    expected_manifest_absent: bool = False,
):
    """Export already-resolved Playlist objects (rekordbox OR custom SoundBox).

    Always uses tree layout (mirror folders, exact playlist names). Set
    clean=False to skip the AppleDouble scrub (do it once after a batch instead
    of per playlist). Writes only under target (sync.run enforces the guard).
    """
    from . import sync as sync_mod
    target = Path(target)
    report = sync_mod.run(
        lib, playlists, target,
        layout="tree", dry_run=dry_run, derive_artists=derive_artists,
        on_progress=on_progress, should_cancel=should_cancel,
        artist_overrides=artist_overrides, title_overrides=title_overrides,
        art_cache=art_cache,
        rockbox_media_cache=rockbox_media_cache,
        rockbox_art_cache=rockbox_art_cache,
        display_rows=display_rows,
        validate_inputs=validate_inputs,
        validate_artwork=validate_artwork,
        expected_manifest_payload=expected_manifest_payload,
        expected_manifest_absent=expected_manifest_absent,
    )
    if (not dry_run and clean and not report.cancelled
            and not any(row.errors for row in report.playlists)):
        if report.mount_identity is None:
            raise RuntimeError('Export has no captured device identity for cleanup')
        sync_mod.clean_applefiles(target, report.written_dirs,
                                 expected_mount_identity=report.mount_identity)
    return report


def clean_device(target: str | Path, dirs=None, *,
                 expected_mount_identity: tuple[int, int] | None = None) -> int:
    """Scrub macOS AppleDouble clutter from a target's Music/ + Playlists/. Pass
    `dirs` (the folders written this run) to scrub only those (fast); None = full scan."""
    from . import sync as sync_mod
    return sync_mod.clean_applefiles(
        Path(target), dirs, expected_mount_identity=expected_mount_identity)


def _rockbox_art_advisory(target: Path) -> str | None:
    """Read-only check of the device's Rockbox art setting. SoundBox writes a
    uniform external cover for every managed track and strips embedded pictures
    only from its disposable device-side audio copy.  The only state worth
    flagging is art turned fully off, because then nothing shows at all."""
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
    from . import sync as sync_mod
    target = Path(target)
    res: dict = {
        "playlists": 0, "entries": 0, "missing": [], "advisories": [],
    }
    managed_issues = sync_mod.verify_managed(Path(target), deep=deep)
    if manifest_mod.has_composite_ownership(target):
        from .dual_ipod import _rockbox_interrupted_writes
        managed_issues.extend(
            f"interrupted write: {item.relative_path}"
            for item in _rockbox_interrupted_writes(target))
    if deep or managed_issues:
        res["managed_issues"] = managed_issues
    adv = _rockbox_art_advisory(target)
    if adv:
        res["advisories"].append(adv)
    session = sync_mod.DeviceSession(target, [], create=False)
    target = session.target
    try:
        pdir = session.guard(target / "Playlists")
    except (OSError, RuntimeError):
        res["missing"].append(("Playlists", "<unsafe path>"))
        return res
    if not pdir.is_dir():
        return res
    for m3u in sorted(pdir.rglob("*.m3u8")):
        if m3u.name.startswith("._"):  # macOS AppleDouble sidecars on FAT
            continue
        res["playlists"] += 1
        try:
            text = session.guard(m3u).read_text(encoding="utf-8")
        except (OSError, RuntimeError):
            res["missing"].append((str(m3u.relative_to(target)), "<unreadable>"))
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            res["entries"] += 1
            try:
                exists = session.guard(target / line.lstrip("/")).is_file()
            except (OSError, RuntimeError):
                exists = False
            if not exists:
                res["missing"].append((str(m3u.relative_to(target)), line))
    return res


def capture_device_drift_manifest(target: str | Path) -> tuple[dict, bytes] | None:
    """One bounded worker observation for managed-ID capture and drift display."""
    import json
    from .device_manifest_capture import read_device_manifest
    payload = read_device_manifest(target)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except (UnicodeError, ValueError):
        return manifest_mod.parse(payload), payload
    if isinstance(data, dict) and int(data.get('schema') or 0) == manifest_mod.COMPOSITE_SCHEMA:
        return manifest_mod.parse_composite(payload), payload
    return manifest_mod.parse(payload), payload


def device_drift(
    lib: Library,
    target: str | Path,
    extra=None,
    *,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    display_rows: Mapping[str, Mapping] | None = None,
    captured_manifest: tuple[dict, bytes] | None = None,
):
    """How far a device has drifted from the current library since last sync
    (renamed/moved/changed/removed playlists). Read-only. None if no manifest.

    `extra` = custom SoundBox playlists (not in lib.playlists) so they aren't
    mis-counted as removed."""
    target_path = Path(target)
    composite = (int(captured_manifest[0].get('schema') or 0) == manifest_mod.COMPOSITE_SCHEMA
                 if captured_manifest is not None else manifest_mod.has_composite_ownership(target_path))
    if composite:
        # Imported lazily: dual_ipod itself composes the lower-level engine
        # modules, while this read-only route only needs its profile-aware diff.
        from .dual_ipod import composite_device_drift
        return composite_device_drift(
            lib,
            target_path,
            extra,
            artist_overrides=artist_overrides,
            title_overrides=title_overrides,
            display_rows=display_rows,
            captured_manifest=captured_manifest,
        )
    m = captured_manifest[0] if captured_manifest is not None else manifest_mod.load(target_path)
    if not m.get("playlists"):
        return None
    drift = manifest_mod.diff(m, lib, extra)
    from .playlist_retirement import legacy_flat_playlists
    wanted = manifest_mod.managed_current_playlists(m, lib, extra)
    affected = {key for key, *_ in drift.renamed + drift.moved + drift.changed + drift.removed}
    for playlist in wanted:
        key = manifest_mod.playlist_key(playlist)
        if key not in affected and key in legacy_flat_playlists(m, [playlist]):
            drift.changed.append((key, playlist.name))
    return drift


def resync_device(
    lib: Library,
    target: str | Path,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache=None,
    rockbox_media_cache=None,
    rockbox_art_cache=None,
    extra=None,
    display_rows: Mapping[str, Mapping] | None = None,
    validate_inputs: Callable[[], None] | None = None,
    validate_artwork: Callable[[str], None] | None = None,
    expected_manifest_payload: bytes | None = None,
    expected_manifest_absent: bool = False,
):
    """Bring a device in line with rekordbox (see sync.resync). Writes only under target.

    `extra` = custom SoundBox playlists so they're kept/updated, not deleted as 'removed'."""
    from . import sync as sync_mod
    return sync_mod.resync(
        lib, Path(target), on_progress=on_progress, should_cancel=should_cancel,
        artist_overrides=artist_overrides, title_overrides=title_overrides,
        art_cache=art_cache, rockbox_media_cache=rockbox_media_cache,
        rockbox_art_cache=rockbox_art_cache, extra=extra, display_rows=display_rows,
        validate_inputs=validate_inputs, validate_artwork=validate_artwork,
        expected_manifest_payload=expected_manifest_payload,
        expected_manifest_absent=expected_manifest_absent)
