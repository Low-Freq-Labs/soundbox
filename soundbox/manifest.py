"""On-device sync manifest — a small record of what Soundbox has put on a device.

Lives at ``<device>/.soundbox/manifest.json``. Keyed by the STABLE rekordbox
playlist ID (see Playlist.pid), so a playlist the user renames or moves in
rekordbox is still recognized as the same thing on the device. Diffing the
manifest against the current library reveals *drift* — what's been renamed,
changed, or deleted since the last sync — so we can update only what moved.

Soundbox reads/writes this file and the device only. It NEVER touches rekordbox.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
_DIRNAME = ".soundbox"
_FILENAME = "manifest.json"


def manifest_path(device_root) -> Path:
    return Path(device_root) / _DIRNAME / _FILENAME


def _empty() -> dict:
    return {"schema": SCHEMA, "updated": "", "playlists": {}}


def load(device_root) -> dict:
    """Read a device's manifest, or a blank one if absent/corrupt."""
    try:
        data = json.loads(manifest_path(device_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("playlists"), dict):
        return _empty()
    return data


def save(device_root, manifest: dict) -> None:
    p = manifest_path(device_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def playlist_key(playlist) -> str:
    """Identity used in the manifest: the stable rekordbox ID when present, else a
    name-based key for in-app custom playlists (which have no rekordbox ID)."""
    return playlist.pid or f"cust:{playlist.name}"


def _excluded(playlist, hidden, removals) -> set:
    """Ids vetoed for THIS playlist: Collection deletes (global) + per-playlist
    removals. Two planes of data: rekordbox is king, SoundBox edits veto."""
    return set(hidden) | set((removals or {}).get(playlist.pid or "", ()))


def playlist_sig(playlist, hidden=frozenset(), removals=None) -> str:
    """Hash of a playlist's EXPORTABLE state: ordered track ids MINUS the user's
    SoundBox deletes/removals — so a delete, removal, or restore in an already-
    exported playlist changes the sig → the device shows drift → Sync heals it."""
    ex = _excluded(playlist, hidden, removals)
    h = hashlib.sha1()
    for k in playlist.keys:
        if k in ex:
            continue
        h.update(k.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def entry_for(playlist, hidden=frozenset(), removals=None) -> dict:
    ex = _excluded(playlist, hidden, removals)
    return {
        "pid": playlist.pid,
        "name": playlist.name,
        "folder_path": list(playlist.folder_path),
        # Effective tracks — what actually went to the device (resolve() skips
        # vetoed ids on export, so the manifest must describe the same reality).
        "tracks": [k for k in playlist.keys if k not in ex],
        "sig": playlist_sig(playlist, hidden, removals),
    }


def record_export(device_root, playlists, incomplete=(), hidden=frozenset(),
                  removals=None) -> dict:
    """After an export, upsert the exported playlists into the device's manifest
    (accumulates — never clobbers entries for other playlists).

    `incomplete` = keys of playlists whose files did NOT all land (a cancel mid-
    copy). Those are recorded with a poisoned sig, so drift immediately flags
    them "out of date" and the next Sync finishes the job — a cancelled export
    must never masquerade as a completed one."""
    inc = set(incomplete)
    m = load(device_root)
    for pl in playlists:
        e = entry_for(pl, hidden, removals)
        if playlist_key(pl) in inc:
            e["sig"] = "partial"
        m["playlists"][playlist_key(pl)] = e
    m["schema"] = SCHEMA
    m["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(device_root, m)
    return m


@dataclass
class Drift:
    """What changed on a device since its last sync (by playlist identity)."""
    renamed: list = field(default_factory=list)   # (key, old_name, new_name)
    moved: list = field(default_factory=list)     # (key, old_folder, new_folder)
    changed: list = field(default_factory=list)   # (key, name)  tracks added/removed/reordered
    removed: list = field(default_factory=list)   # (key, name)  gone from rekordbox

    def any(self) -> bool:
        return bool(self.renamed or self.moved or self.changed or self.removed)

    def count(self) -> int:
        # Distinct playlists affected (a rename+change counts once).
        keys = {k for k, *_ in self.renamed + self.moved + self.changed + self.removed}
        return len(keys)


def diff(manifest: dict, library, extra=None) -> Drift:
    """Compare a device manifest against the current library. Only inspects
    playlists that were previously synced (present in the manifest); brand-new
    playlists are added by the user dragging them, not treated as drift.

    `extra` = additional current playlists to consider by identity — used to pass in
    the user's custom SoundBox playlists, which are NOT in library.playlists. Without
    them a synced custom playlist would look 'removed' (phantom drift + a resync would
    delete it)."""
    by_key = {}
    for pl in list(library.playlists) + list(extra or []):
        by_key[playlist_key(pl)] = pl
    hidden = set(getattr(library, "hidden_ids", ()) or ())
    removals = getattr(library, "removed_by_playlist", None)
    d = Drift()
    for key, e in manifest.get("playlists", {}).items():
        cur = by_key.get(key)
        if cur is None:
            d.removed.append((key, e.get("name", "")))
            continue
        if cur.name != e.get("name"):
            d.renamed.append((key, e.get("name", ""), cur.name))
        if list(cur.folder_path) != list(e.get("folder_path", [])):
            d.moved.append((key, " / ".join(e.get("folder_path", [])),
                            " / ".join(cur.folder_path)))
        if playlist_sig(cur, hidden, removals) != e.get("sig"):
            d.changed.append((key, cur.name))
    return d
