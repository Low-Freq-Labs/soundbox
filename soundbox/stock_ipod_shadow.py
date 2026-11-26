"""Disposable Mac-side acceleration state for guarded stock-iPod sync.

The connected iPod remains the source of truth.  A shadow is usable only when
its stable library identity and small live database fingerprints still match;
otherwise callers fall back to the complete read-only reconciliation path.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import ctypes
import errno
import secrets
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from threading import Lock, RLock

from .stock_ipod_catalog_state import StockCatalogWitness


SCHEMA = 1
PROFILE = "ma446-stock-shadow-v1"
_LOCKS: dict[str, Any] = {}
_LOCKS_GUARD = Lock()


def _store_lock(path: Path):
    with _LOCKS_GUARD:
        key = os.path.join(os.path.realpath(path.parent), path.name)
        return _LOCKS.setdefault(key, RLock())


def _cache_epoch(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cache_bytes(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    rows = []; size = 0
    while chunk := os.read(fd, min(64 * 1024, 8 * 1024 * 1024 + 1 - size)):
        rows.append(chunk); size += len(chunk)
        if size > 8 * 1024 * 1024:
            raise ValueError('Stock shadow exceeds its supported size')
    return b''.join(rows)


def _exclusive_cache_move(parent, name, target_parent, target_name):
    if sys.platform != 'darwin':
        raise OSError('Guarded shadow replacement requires macOS exclusive rename')
    call = ctypes.CDLL(None, use_errno=True).renameatx_np
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    if call(parent, os.fsencode(name), target_parent, os.fsencode(target_name), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)


def _safe_name(identity_key: str) -> str:
    return hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:32]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8") + b"\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class StockIpodShadow:
    identity_key: str
    model: str
    serial_number: str
    generation: int
    committed_at: str
    database_sha256: str
    artwork_database_sha256: str
    ithmb: dict[str, dict[str, int]]
    sources: dict[str, dict[str, Any]]
    artwork: dict[str, dict[str, Any]]
    playlists: dict[str, dict[str, Any]]
    _storage_token: tuple[tuple[int, ...], str] | None = dataclass_field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StockIpodShadow | None":
        if data.get("schema") != SCHEMA or data.get("profile") != PROFILE:
            return None
        required_maps = ("ithmb", "sources", "artwork", "playlists")
        if any(not isinstance(data.get(key), dict) for key in required_maps):
            return None
        numeric_fields = {
            'ithmb': ('size', 'mtime_ns'),
            'sources': ('size', 'source_size', 'mtime_ns', 'dbid'),
            'artwork': ('image_id', 'source_size', 'art_size', 'art_mtime_ns'),
            'playlists': (),
        }
        # These maps accelerate consumers that parse native catalogs on a miss.
        # Admit only rows those consumers can read; dropping damaged rows could
        # otherwise make an incomplete shadow appear to be an unchanged baseline.
        for group, fields in numeric_fields.items():
            for key, row in data[group].items():
                if not isinstance(row, dict):
                    return None
                for field in fields:
                    if field in row:
                        int(row[field] or 0)
                if group == 'artwork':
                    int(key)
                    offsets = row.get('offsets') or {}
                    if not isinstance(offsets, dict):
                        return None
                    for format_id, offset in offsets.items():
                        int(format_id)
                        int(offset)
                if group == 'playlists':
                    for field in ('folder_path', 'tracks'):
                        if field in row and (
                            not isinstance(row[field], list)
                            or any(not isinstance(value, str) for value in row[field])
                        ):
                            return None
        identity_key = str(data.get("identity_key") or "")
        database_sha256 = str(data.get("database_sha256") or "")
        if not identity_key or len(database_sha256) != 64:
            return None
        return cls(
            identity_key=identity_key,
            model=str(data.get("model") or ""),
            serial_number=str(data.get("serial_number") or ""),
            generation=max(0, int(data.get("generation") or 0)),
            committed_at=str(data.get("committed_at") or ""),
            database_sha256=database_sha256,
            artwork_database_sha256=str(
                data.get("artwork_database_sha256") or ""),
            ithmb={str(key): dict(value) for key, value in data["ithmb"].items()
                   if isinstance(value, dict)},
            sources={str(key): dict(value) for key, value in data["sources"].items()
                     if isinstance(value, dict)},
            artwork={str(key): dict(value) for key, value in data["artwork"].items()
                     if isinstance(value, dict)},
            playlists={str(key): dict(value) for key, value in data["playlists"].items()
                       if isinstance(value, dict)},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "profile": PROFILE,
            "identity_key": self.identity_key,
            "model": self.model,
            "serial_number": self.serial_number,
            "generation": self.generation,
            "committed_at": self.committed_at,
            "database_sha256": self.database_sha256,
            "artwork_database_sha256": self.artwork_database_sha256,
            "ithmb": self.ithmb,
            "sources": self.sources,
            "artwork": self.artwork,
            "playlists": self.playlists,
        }


@dataclass(frozen=True)
class StockIpodSyncStatus:
    state: str
    detail: str
    local_changes: int = 0
    committed_at: str = ""
    database_sha256: str = ""
    artwork_database_sha256: str | None = None
    play_counts_sha256: str | None = None
    change_summary: tuple[tuple[str, int], ...] = ()


def retained_source_rows(shadow: StockIpodShadow, before: Sequence[tuple[int, str, int]],
                         after: Sequence[tuple[int, str, int]], rewritten_paths: Sequence[str]) -> dict[str, dict]:
    """Carry accepted old observations verbatim only across unchanged payload identities.

    The caller has proved the exact native-before shadow. These rows remain
    acceleration, with fresh source epoch/profile checks required at next use.
    """
    old_counts = Counter(row[0] for row in before)
    new_counts = Counter(row[0] for row in after)
    source_counts = Counter(int(row.get('dbid') or 0) for row in shadow.sources.values())
    old = {dbid: (path, size) for dbid, path, size in before}
    new = {dbid: (path, size) for dbid, path, size in after}
    rewritten = {str(PurePosixPath(path)).casefold() for path in rewritten_paths}
    kept = {}
    for source, row in shadow.sources.items():
        dbid = int(row.get('dbid') or 0)
        path = str(row.get('device_path') or '')
        payload = path, int(row.get('size') or 0)
        if (dbid > 0 and old_counts[dbid] == new_counts[dbid] == source_counts[dbid] == 1
                and path and old.get(dbid) == new.get(dbid) == payload
                and str(PurePosixPath(path)).casefold() not in rewritten):
            kept[source] = dict(row)
    return kept


def rebase_deleted_shadow(
    shadow: StockIpodShadow | None, *, identity_key: str, model: str, serial_number: str,
    previous_database: bytes, previous_artwork_sha256: str,
    previous_ithmb: Mapping[str, Mapping[str, int]], rendered: bytes, artwork_sha256: str,
    ithmb: Mapping[str, Mapping[str, int]], previous_witness: StockCatalogWitness | None,
    deleted_playlist_ids: Sequence[int],
    artwork_updates: Mapping[int, tuple[int, int, Mapping[str, int]]],
) -> StockIpodShadow | None:
    """Renew a proven acceleration baseline after device-only playlist delete.

    Callers own fresh mount/catalog/ithmb observations and the renderer's
    preserved-pixel slot updates, then compare the saved generation before
    publishing this disposable result. This helper reads no source or device
    files and cannot invent source membership or authorize a device write.
    """
    from clickwheel.ipod import get_ipod_tracks, parse_itunesdb  # type: ignore[import-untyped]
    from .ipod_export_witness import rebase_export_witness
    if shadow is None or previous_witness is None:
        return None
    try:
        if (shadow.identity_key != identity_key or previous_witness.device_key != identity_key
                or shadow.model and model and shadow.model != model
                or shadow.serial_number and serial_number and shadow.serial_number != serial_number
                or shadow.database_sha256 != hashlib.sha256(previous_database).hexdigest()
                or shadow.artwork_database_sha256 != previous_artwork_sha256
                or shadow.ithmb != previous_ithmb):
            return None
        old = parse_itunesdb(io.BytesIO(previous_database))
        new = parse_itunesdb(io.BytesIO(rendered))
        old_native, new_native = get_ipod_tracks(old), get_ipod_tracks(new)
        old_rows = {int(row.get('dbid') or 0): row for row in old_native}
        new_rows = {int(row.get('dbid') or 0): row for row in new_native}
        if len(old_rows) != len(old_native) or len(new_rows) != len(new_native) or not set(new_rows) <= set(old_rows):
            return None
        def playlist_ids(parsed):
            return {int(row.get('playlistID') or 0) for group in ('mhlp', 'mhlp_smart')
                    for row in parsed.get(group, ()) if not row.get('isMaster')}
        before_ids, after_ids = playlist_ids(old), playlist_ids(new)
        deleted = set(deleted_playlist_ids)
        if not deleted <= before_ids or before_ids - after_ids != deleted or not after_ids <= before_ids:
            return None
        renewed = rebase_export_witness(previous_witness, device_key=identity_key,
            previous_database=previous_database, previous_artwork_sha256=previous_artwork_sha256,
            rendered=rendered, artwork_sha256=artwork_sha256,
            preserved_artwork_dbids=frozenset(artwork_updates))
        if renewed.witness is None:
            return None
        old_bindings = {binding.key: binding for binding in previous_witness.bindings}
        new_bindings = {binding.key: binding for binding in renewed.witness.bindings}
        for key in shadow.playlists:
            binding = old_bindings.get(key)
            if binding is None or binding.device_playlist_id not in deleted and new_bindings.get(key) != binding:
                return None
        sources = {}
        media_fields = ('location', 'size', 'length', 'sampleRate', 'filetype', 'bitrate')
        for source, original in shadow.sources.items():
            dbid = int(original.get('dbid') or 0)
            native = old_rows.get(dbid)
            if (native is None or str(original.get('device_path') or '').lstrip('/')
                    != str(native.get('location') or '').lstrip(':').replace(':', '/')
                    or int(original.get('size') or -1) != int(native.get('size') or -1)):
                return None
            if dbid not in new_rows:
                continue
            if any(new_rows[dbid].get(field) != native.get(field) for field in media_fields):
                return None
            sources[source] = deepcopy(original)
        retained_dbids = {int(row['dbid']) for row in sources.values()}
        artwork = {}
        for key, original in shadow.artwork.items():
            dbid = int(key)
            if dbid not in retained_dbids:
                continue
            if dbid not in new_rows or int(original.get('image_id') or 0) != int(old_rows[dbid].get('mhiiLink') or 0):
                return None
            row = deepcopy(original)
            update = artwork_updates.get(dbid)
            if update is not None:
                image_id, source_size, offsets = update
                if (image_id != int(new_rows[dbid].get('mhiiLink') or 0) or source_size < 0
                        or any(type(name) is not str or type(offset) is not int or offset < 0
                               for name, offset in offsets.items())):
                    return None
                row.update(image_id=image_id, source_size=source_size, offsets=dict(offsets))
            elif (artwork_sha256 != previous_artwork_sha256
                    or any(new_rows[dbid].get(field) != old_rows[dbid].get(field)
                           for field in ('mhiiLink', 'artworkCount', 'artworkSize'))):
                return None
            artwork[key] = row
        playlists = {key: deepcopy(row) for key, row in shadow.playlists.items()
                     if old_bindings[key].device_playlist_id not in deleted}
        return replace(shadow, model=model or shadow.model, serial_number=serial_number or shadow.serial_number,
            generation=shadow.generation + 1, committed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            database_sha256=hashlib.sha256(rendered).hexdigest(), artwork_database_sha256=artwork_sha256,
            ithmb={name: dict(row) for name, row in ithmb.items()},
            sources=sources, artwork=artwork, playlists=playlists, _storage_token=None)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


class StockIpodShadowStore:
    """Atomic per-device shadow storage below SoundBox AppData."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path_for(self, identity_key: str) -> Path:
        return self.root / _safe_name(identity_key) / "manifest.json"

    def load(self, identity_key: str) -> StockIpodShadow | None:
        if not identity_key:
            return None
        path = self.path_for(identity_key)
        try:
            with _store_lock(path):
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 8 * 1024 * 1024:
                        return None
                    payload = _cache_bytes(fd)
                    after = os.fstat(fd)
                    if _cache_epoch(before) != _cache_epoch(after) or _cache_epoch(after) != _cache_epoch(path.lstat()):
                        return None
                    token = (_cache_epoch(after), hashlib.sha256(payload).hexdigest())
                    raw = json.loads(payload)
                finally:
                    os.close(fd)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            shadow = StockIpodShadow.from_dict(raw)
        except (ValueError, TypeError, OverflowError):
            # Malformed disposable state is a miss, never device authority.
            # Preserve the bytes and let the planner reconcile native catalogs.
            return None
        return replace(shadow, _storage_token=token) if shadow and shadow.identity_key == identity_key else None

    def save(self, shadow: StockIpodShadow) -> None:
        if not shadow.identity_key:
            raise ValueError("A stock-iPod shadow requires a stable identity key")
        path = self.path_for(shadow.identity_key)
        with _store_lock(path):
            _atomic_json(path, shadow.as_dict())

    def replace_if_current(self, before: StockIpodShadow, renewed: StockIpodShadow | None,
                           *, same_generation: bool = False) -> bool:
        """Replace/retire only the exact cache entity returned by ``load``.

        The live cache leaf is exclusively claimed, then checked against its
        retained descriptor. Foreign occupants are restored/preserved. Ordinary
        store writers share this path lock; no source/device file is involved.
        """
        if before._storage_token is None:
            return False
        if same_generation and (renewed is None or any(getattr(renewed, name) != getattr(before, name)
                for name in ('committed_at', 'model', 'serial_number', 'database_sha256',
                             'artwork_database_sha256', 'ithmb'))):
            raise ValueError('A no-op shadow renewal must preserve the native generation and layout')
        if renewed is not None and (renewed.identity_key != before.identity_key
                                     or renewed.generation != before.generation + (0 if same_generation else 1)):
            raise ValueError('Delete shadow renewal has an invalid identity or generation')
        path = self.path_for(before.identity_key)
        with _store_lock(path):
            try:
                parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return False
            original = claim_fd = replacement = -1
            claim = '.delete-shadow-' + secrets.token_hex(12)
            claimed = verified = published = keep_claim = finalized = False
            try:
                parent_epoch = os.fstat(parent)
                def current_parent():
                    named = path.parent.lstat()
                    if (named.st_dev, named.st_ino) != (parent_epoch.st_dev, parent_epoch.st_ino):
                        raise OSError('Stock shadow directory changed during replacement')
                try:
                    original = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                except FileNotFoundError:
                    return False
                info = os.fstat(original)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return False
                payload = _cache_bytes(original)
                if ((_cache_epoch(info), hashlib.sha256(payload).hexdigest()) != before._storage_token
                        or _cache_epoch(os.fstat(original)) != _cache_epoch(info)
                        or StockIpodShadow.from_dict(json.loads(payload)) != before):
                    return False
                current_parent()
                os.mkdir(claim, 0o700, dir_fd=parent)
                claim_fd = os.open(claim, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                _exclusive_cache_move(parent, path.name, claim_fd, 'before.json')
                claimed = True
                moved = os.stat('before.json', dir_fd=claim_fd, follow_symlinks=False)
                retained = os.fstat(original)
                if (_cache_epoch(moved) != _cache_epoch(retained)
                        or (moved.st_dev, moved.st_ino) != (info.st_dev, info.st_ino)
                        or hashlib.sha256(_cache_bytes(original)).hexdigest() != before._storage_token[1]):
                    return False
                verified = True
                if renewed is not None:
                    encoded = json.dumps(renewed.as_dict(), ensure_ascii=False, indent=2, sort_keys=True).encode() + b'\n'
                    replacement = os.open('replacement.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                          0o600, dir_fd=claim_fd)
                    view = memoryview(encoded)
                    while view:
                        count = os.write(replacement, view)
                        if count <= 0:
                            raise OSError('Stock shadow replacement write made no progress')
                        view = view[count:]
                    os.fsync(replacement)
                    current_parent()
                    try:
                        _exclusive_cache_move(claim_fd, 'replacement.json', parent, path.name)
                    except FileExistsError:
                        return False
                    published = True
                    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    if _cache_epoch(named) != _cache_epoch(os.fstat(replacement)) or named.st_size != len(encoded):
                        return False
                os.fsync(parent)
                current_parent()
                finalized = True
                return True
            finally:
                # Only private claimed files can be unlinked. The public leaf
                # may already contain a newer generation or foreign occupant.
                cleanup_error = None
                if claimed and not published and not finalized:
                    try:
                        _exclusive_cache_move(claim_fd, 'before.json', parent, path.name)
                        claimed = False
                    except OSError as exc:
                        if exc.errno != errno.EEXIST or not verified:
                            keep_claim = True
                            cleanup_error = OSError(f'Stock shadow replacement retained evidence in {path.parent / claim}: {exc}')
                if claimed and verified:
                    try:
                        os.unlink('before.json', dir_fd=claim_fd)
                    except OSError as exc:
                        keep_claim = True
                        cleanup_error = exc
                if claim_fd >= 0:
                    try:
                        os.unlink('replacement.json', dir_fd=claim_fd)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        keep_claim = True
                        cleanup_error = exc
                for fd in (replacement, original, claim_fd):
                    if fd >= 0:
                        os.close(fd)
                if claim_fd >= 0 and not keep_claim:
                    try:
                        os.rmdir(claim, dir_fd=parent)
                    except OSError as exc:
                        cleanup_error = exc
                os.close(parent)
                if cleanup_error is not None:
                    raise cleanup_error

    def committed(
        self,
        *,
        identity_key: str,
        model: str,
        serial_number: str,
        database_sha256: str,
        artwork_database_sha256: str,
        ithmb: dict[str, dict[str, int]],
        sources: dict[str, dict[str, Any]],
        artwork: dict[str, dict[str, Any]],
        playlists: dict[str, dict[str, Any]],
    ) -> StockIpodShadow:
        with _store_lock(self.path_for(identity_key)):
            before = self.load(identity_key)
            shadow = StockIpodShadow(
                identity_key=identity_key,
                model=model,
                serial_number=serial_number,
                generation=(before.generation if before else 0) + 1,
                committed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                database_sha256=database_sha256,
                artwork_database_sha256=artwork_database_sha256,
                ithmb=ithmb,
                sources=sources,
                artwork=artwork,
                playlists=playlists,
            )
            self.save(shadow)
        return shadow
