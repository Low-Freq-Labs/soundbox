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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sync import DeviceSession

from .paths import is_external_device_path
from .device_manifest_capture import read_device_manifest

SCHEMA = 2
COMPOSITE_SCHEMA = 3
ROCKBOX_EXPORT_PROFILE = "rockbox-files-no-embedded-universal-bmp-v6"
COMPOSITE_EXPORT_PROFILE = (
    "dual-single-copy-no-embedded-universal-bmp-early-riffinfo-catalogue-v9")
LEGACY_COMPOSITE_EXPORT_PROFILES = frozenset({
    "dual-single-copy-no-embedded-rgb500-v1",
    "dual-single-copy-no-embedded-rgb500-tags-v2",
    "dual-single-copy-no-embedded-rgb160-tags-v3",
    "dual-single-copy-no-embedded-rgb160-bmp-riffinfo-v4",
    "dual-single-copy-no-embedded-multiview-bmp-riffinfo-v5",
    "dual-single-copy-no-embedded-paletted8-bmp-riffinfo-v6",
    "dual-single-copy-no-embedded-universal-bmp-riffinfo-v7",
    "dual-single-copy-no-embedded-universal-bmp-early-riffinfo-v8",
})
_DIRNAME = ".soundbox"
_FILENAME = "manifest.json"


@dataclass(frozen=True)
class CompositeLibraryCounts:
    """User-facing counts from one validated schema-3 ownership manifest.

    Rockbox playlists are the composite library authority; iTunesDB contains
    only the stock-safe subset. Keeping both numbers explicit prevents a valid
    stock-only count from masquerading as the size of the whole dual library.
    """

    managed_tracks: int
    stock_visible_tracks: int
    rockbox_only_tracks: int
    managed_playlists: int


@dataclass(frozen=True)
class CompositePlaylistStatus:
    """Verified dashboard entries plus identities whose live M3U8 drifted."""

    entries: dict[str, tuple[str, ...]]
    changed_keys: frozenset[str]


def manifest_path(device_root) -> Path:
    return Path(device_root) / _DIRNAME / _FILENAME


def _guarded_manifest_path(
        device_root, *, create: bool = False,
        session: DeviceSession | None = None,
) -> tuple[Path, DeviceSession]:
    # Local import avoids the manifest/sync module dependency at import time.
    from .sync import DeviceSession
    root = Path(device_root)
    if session is None:
        if not create and not root.exists():
            raise FileNotFoundError(root)
        session = DeviceSession(root, [], create=create)
    else:
        session.revalidate()
        if root.expanduser().resolve(strict=True) != session.target:
            raise RuntimeError("Manifest target differs from the captured device")
    return session.guard(manifest_path(session.target)), session


def has_composite_ownership(device_root, *, session: DeviceSession | None = None) -> bool:
    """Bounded read-only guard for the dual-library schema.

    Older Rockbox-only export/resync code must never interpret schema 3 as an
    empty schema-2 manifest and overwrite its cross-firmware ownership state.
    """
    payload = (read_device_manifest(device_root) if session is None else
               read_device_manifest(device_root, session=session))
    if payload is None:
        return False
    try:
        data = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and int(data.get("schema") or 0) == COMPOSITE_SCHEMA


def _empty() -> dict:
    return {
        "schema": SCHEMA,
        "updated": "",
        "generation": 0,
        "completed_generation": 0,
        "playlists": {},
        "files": {},
    }


def load(device_root, *, session: DeviceSession | None = None) -> dict:
    """Read a device's manifest, or a blank one if absent/corrupt."""
    payload = (read_device_manifest(device_root) if session is None else
               read_device_manifest(device_root, session=session))
    return parse(payload)


def parse(payload: bytes | None) -> dict:
    """Pure schema-2/legacy parsing; captured bytes grant no write authority."""
    if payload is None:
        return _empty()
    try:
        data = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("playlists"), dict):
        return _empty()
    # V1 playlist identity remains useful. Ownership begins empty so upgrading
    # can never claim or prune pre-existing device content retroactively.
    if data.get("schema") != SCHEMA:
        data = {
            **_empty(),
            "updated": str(data.get("updated") or ""),
            "playlists": dict(data.get("playlists") or {}),
        }
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    # Old/tampered manifests cannot make another product's device tree managed.
    # Drop only the in-memory ownership row; never inspect or touch its payload.
    data["files"] = {
        str(path): row for path, row in data["files"].items()
        if not is_external_device_path(path)
    }
    data.setdefault("generation", 0)
    data.setdefault("completed_generation", 0)
    return data


def load_composite(device_root, *, session: DeviceSession | None = None) -> dict:
    """Read and strictly validate a schema-3 composite ownership manifest."""
    return read_composite(device_root, session=session)[0]


def read_composite(device_root, *, session: DeviceSession | None = None) -> tuple[dict, bytes]:
    """Return one fresh captured payload and its strictly validated model."""
    try:
        payload = (read_device_manifest(device_root) if session is None else
               read_device_manifest(device_root, session=session))
    except OSError as exc:
        raise ValueError("Composite SoundBox manifest is unreadable") from exc
    if payload is None:
        raise ValueError("Composite SoundBox manifest is unreadable")
    return parse_composite(payload),payload


def parse_composite(payload: bytes) -> dict:
    """Pure parsing only; captured bytes confer no publication authority."""
    try:
        data = json.loads(payload.decode('utf-8'))
    except (UnicodeError,ValueError) as exc:
        raise ValueError("Composite SoundBox manifest is unreadable") from exc
    if (
        not isinstance(data, dict)
        or int(data.get("schema") or 0) != COMPOSITE_SCHEMA
        or data.get("profile") not in (
            {COMPOSITE_EXPORT_PROFILE} | LEGACY_COMPOSITE_EXPORT_PROFILES)
        or not isinstance(data.get("playlists"), dict)
        or not isinstance(data.get("files"), dict)
    ):
        raise ValueError("Composite SoundBox manifest is invalid")
    data["files"] = {
        str(path): row for path, row in data["files"].items()
        if not is_external_device_path(path)
    }
    return data


def composite_library_counts(manifest: dict) -> CompositeLibraryCounts:
    """Count managed tracks/playlists without reading or mutating media.

    Schema 3 records the exact exported track IDs in each playlist and the
    iTunesDB DBID on every shared stock-media row. Track IDs are unioned across
    playlists, so a song used by several playlists is counted once.
    """
    if (
        not isinstance(manifest, dict)
        or int(manifest.get("schema") or 0) != COMPOSITE_SCHEMA
        or manifest.get("profile") not in (
            {COMPOSITE_EXPORT_PROFILE} | LEGACY_COMPOSITE_EXPORT_PROFILES)
        or not isinstance(manifest.get("playlists"), dict)
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValueError("Composite SoundBox manifest is invalid")
    track_ids: set[str] = set()
    for row in manifest["playlists"].values():
        if not isinstance(row, dict) or not isinstance(row.get("tracks"), list):
            raise ValueError("Composite playlist tracks are invalid")
        track_ids.update(str(value) for value in row["tracks"] if str(value))
    stock_dbids = {
        int(row.get("dbid") or 0)
        for path, row in manifest["files"].items()
        if not is_external_device_path(path)
        and isinstance(row, dict)
        and row.get("kind") == "shared-stock-media"
        and int(row.get("dbid") or 0) > 0
    }
    stock_count = len(stock_dbids)
    if stock_count > len(track_ids):
        raise ValueError("Composite stock count exceeds managed track count")
    return CompositeLibraryCounts(
        managed_tracks=len(track_ids),
        stock_visible_tracks=stock_count,
        rockbox_only_tracks=len(track_ids) - stock_count,
        managed_playlists=len(manifest["playlists"]),
    )


def composite_playlist_status(
        device_root, manifest: dict) -> CompositePlaylistStatus:
    """Read only unchanged managed M3U8 paths for the device dashboard.

    This deliberately reads only small manifest-owned playlist payloads. Audio
    files are never opened while building the device dashboard. Expected live
    drift is reported separately so it cannot erase otherwise valid stock
    identity/details, and changed playlist contents remain untrusted until the
    confirmed composite planner validates them.
    """
    composite_library_counts(manifest)  # strict schema/profile validation
    root = Path(device_root).expanduser().resolve(strict=True)
    from .sync import DeviceSession
    session = DeviceSession(root, [], create=False)
    output: dict[str, tuple[str, ...]] = {}
    changed: set[str] = set()
    for raw_path, row in manifest["files"].items():
        if is_external_device_path(raw_path):
            continue
        if not isinstance(row, dict) or row.get("kind") != "playlist":
            continue
        playlist_key_value = str(row.get("source") or "")
        if not playlist_key_value or not bool(row.get("owned")):
            raise ValueError("Composite playlist ownership is invalid")
        path_text = str(raw_path or "").replace("\\", "/")
        parts = [part for part in path_text.lstrip("/").split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise ValueError("Composite playlist path is invalid")
        candidate = root / Path(*parts)
        session.guard(candidate)
        try:
            path = candidate.resolve(strict=True)
        except OSError:
            changed.add(playlist_key_value)
            continue
        if not path.is_relative_to(root):
            raise ValueError("Composite playlist path escaped its device")
        if candidate.is_symlink() or not path.is_file():
            changed.add(playlist_key_value)
            continue
        expected_size = int(row.get("size") or -1)
        if expected_size < 0 or expected_size > 1024 * 1024:
            raise ValueError("Composite playlist size is invalid")
        try:
            if path.stat().st_size > 1024 * 1024:
                changed.add(playlist_key_value)
                continue
            payload = path.read_bytes()
        except OSError:
            changed.add(playlist_key_value)
            continue
        if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != str(
                row.get("sha256") or ""):
            changed.add(playlist_key_value)
            continue
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Composite playlist is not valid UTF-8") from exc
        output[playlist_key_value] = tuple(
            line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return CompositePlaylistStatus(output, frozenset(changed))


def composite_playlist_entries(
        device_root, manifest: dict) -> dict[str, tuple[str, ...]]:
    """Read verified managed M3U8 paths, rejecting any live drift."""
    status = composite_playlist_status(device_root, manifest)
    if status.changed_keys:
        raise ValueError("Composite playlist changed since the last commit")
    return status.entries


def save(device_root, manifest: dict, *, session: DeviceSession | None = None) -> None:
    p, session = _guarded_manifest_path(device_root, create=True, session=session)
    output = manifest
    if isinstance(manifest.get("files"), dict):
        output = {
            **manifest,
            "files": {
                str(path): row for path, row in manifest["files"].items()
                if not is_external_device_path(path)
            },
        }
    encoded = json.dumps(
        output, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    session.atomic_write(p, encoded)


def playlist_key(playlist) -> str:
    """Identity used in the manifest: the stable rekordbox ID when present, else a
    name-based key for in-app custom playlists (which have no rekordbox ID)."""
    return getattr(playlist,'export_key','') or playlist.pid or f"cust:{playlist.name}"


def current_playlists_by_key(library, extra=None) -> dict:
    """Index the current library using the manifest's stable identity rules."""
    output = {}
    for playlist in list(library.playlists) + list(extra or []):
        key = playlist_key(playlist)
        if key in output:
            raise ValueError(f"Current library has duplicate playlist identity {key!r}")
        output[key] = playlist
    return output


def managed_current_playlists(manifest: dict, library, extra=None) -> list:
    """Current versions of managed playlists, preserving manifest order."""
    by_key = current_playlists_by_key(library, extra)
    order = manifest.get("playlist_order")
    if not isinstance(order, list):
        order = list(manifest.get("playlists") or {})
    from .export_selection import retain_projection
    return [retain_projection(by_key[str(key)],manifest.get('playlists',{}).get(str(key),{}))
            for key in order if str(key) in by_key]


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
    for k in playlist.effective_keys:
        if k in ex:
            continue
        h.update(k.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def entry_for(playlist, hidden=frozenset(), removals=None, *,
              export_profile: str | None = None) -> dict:
    ex = _excluded(playlist, hidden, removals)
    entry = {
        "pid": playlist.pid,
        "name": playlist.name,
        "folder_path": list(playlist.folder_path),
        # Effective tracks — what actually went to the device (resolve() skips
        # vetoed ids on export, so the manifest must describe the same reality).
        "tracks": [k for k in playlist.effective_keys if k not in ex],
        "sig": playlist_sig(playlist, hidden, removals),
    }
    if export_profile is not None:
        entry["export_profile"] = export_profile
    if playlist.export_folder_path is not None:
        entry['export_folder_path'] = list(playlist.destination_folder_path)
    return entry


def record_export(device_root, playlists, incomplete=(), hidden=frozenset(),
                  removals=None, *, managed_files: dict | None = None,
                  completed: bool = True,
                  session: DeviceSession | None = None) -> dict:
    """After an export, upsert the exported playlists into the device's manifest
    (accumulates — never clobbers entries for other playlists).

    `incomplete` = keys of playlists whose files did NOT all land (a cancel mid-
    copy). Those are recorded with a poisoned sig, so drift immediately flags
    them "out of date" and the next Sync finishes the job — a cancelled export
    must never masquerade as a completed one."""
    inc = set(incomplete)
    _, session = _guarded_manifest_path(device_root, create=True, session=session)
    if has_composite_ownership(device_root, session=session):
        raise RuntimeError("Rockbox export cannot replace composite dual-library ownership")
    m = load(device_root, session=session)
    generation = int(m.get("generation") or 0) + 1
    for pl in playlists:
        e = entry_for(
            pl, hidden, removals,
            export_profile=ROCKBOX_EXPORT_PROFILE)
        if playlist_key(pl) in inc:
            e["sig"] = "partial"
        m["playlists"][playlist_key(pl)] = e
    if managed_files is not None:
        m["files"] = {
            str(path): dict(row) for path, row in managed_files.items()
            if not is_external_device_path(path)
        }
    m["schema"] = SCHEMA
    m["generation"] = generation
    if completed and not inc:
        m["completed_generation"] = generation
    m["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(device_root, m, session=session)
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


def diff(manifest: dict, library, extra=None, *,
         expected_profile: str | None = ROCKBOX_EXPORT_PROFILE) -> Drift:
    """Compare a device manifest against the current library. Only inspects
    playlists that were previously synced (present in the manifest); brand-new
    playlists are added by the user dragging them, not treated as drift.

    `extra` = additional current playlists to consider by identity — used to pass in
    the user's custom SoundBox playlists, which are NOT in library.playlists. Without
    them a synced custom playlist would look 'removed' (phantom drift + a resync would
    delete it)."""
    by_key = current_playlists_by_key(library, extra)
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
        if (
            playlist_sig(cur, hidden, removals) != e.get("sig")
            or (
                expected_profile is not None
                and e.get("export_profile") != expected_profile
            )
        ):
            d.changed.append((key, cur.name))
    return d
