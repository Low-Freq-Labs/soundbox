"""Portable library schema, recovery and durable persistence, independent of Qt."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_io import StateChangedError, atomic_bytes, state_area
from . import state_records


_MAX_STATE_BYTES = 64 * 1024**2


class InvalidState(ValueError):
    pass


def _validate_state(data: Any) -> dict:
    if not isinstance(data, dict):
        raise InvalidState('Library must be a JSON object')
    maps = ('custom', 'imported', 'artist_overrides', 'title_overrides',
            'playlist_removals', 'playlist_additions', 'artist_aliases', 'settings')
    for key in maps:
        if key in data and not isinstance(data[key], dict):
            raise InvalidState(f'{key} must be an object')
    for key in ('custom', 'playlist_additions'):
        for values in data.get(key, {}).values():
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise InvalidState(f'{key} memberships must be lists of track IDs')
    for key in ('artist_overrides', 'title_overrides', 'artist_aliases'):
        if any(not isinstance(v, str) for v in data.get(key, {}).values()):
            raise InvalidState(f'{key} values must be text')
    for values in [data.get('hidden_tracks', {}), *data.get('playlist_removals', {}).values()]:
        if isinstance(values, list):
            valid = all(isinstance(v, str) for v in values)
        elif isinstance(values, dict):
            valid = all(isinstance(v, str) for v in values.values())
        else:
            valid = False
        if not valid:
            raise InvalidState('Hidden/removal entries must contain track IDs and dates')
    if 'last_xml' in data and not isinstance(data['last_xml'], str):
        raise InvalidState('last_xml must be text')
    for row in data.get('imported', {}).values():
        if not isinstance(row, dict):
            raise InvalidState('Imported track records must be objects')
        for field in ('path', 'name', 'artist', 'bookmark'):
            if field in row and not isinstance(row[field], str):
                raise InvalidState(f'Imported {field} must be text')
    settings = data.get('settings', {})
    if 'custom_playlist_tree' in settings:
        from .playlist_folders import validate
        try:validate(settings['custom_playlist_tree'])
        except (ValueError, TypeError) as exc:raise InvalidState('Invalid SoundBox playlist folders') from exc
    if 'custom_playlist_expanded' in settings and (not isinstance(settings['custom_playlist_expanded'], list)
            or any(not isinstance(key, str) for key in settings['custom_playlist_expanded'])):
        raise InvalidState('Invalid expanded playlist folders')
    for key in ('hide_untracked', 'hide_nokey', 'hide_short'):
        if key in settings and not isinstance(settings[key], bool):
            raise InvalidState(f'{key} must be a boolean')
    for key in ('hide_short_secs', 'waveform_zoom'):
        if key in settings and (type(settings[key]) not in (int, float)
                                or not math.isfinite(settings[key]) or settings[key] < 0):
            raise InvalidState(f'{key} must be a finite nonnegative number')
    for key in ('title_rules_off', 'title_words'):
        if key in settings and (not isinstance(settings[key], list)
                                or any(not isinstance(v, str) for v in settings[key])):
            raise InvalidState(f'{key} must be a list of text')
    for key in ('stock_ipod_models', 'stock_ipod_identity'):
        if key in settings and not isinstance(settings[key], dict):
            raise InvalidState(f'{key} must be an object')
    for row in data.get('imported', {}).values():
        for key in ('time', 'size', 'added_ts'):
            if key in row and (type(row[key]) not in (int, float)
                               or not math.isfinite(row[key]) or row[key] < 0):
                raise InvalidState(f'Imported {key} must be a finite nonnegative number')
    if 'analysis_results' in settings:
        from .analysis import validate_result, result_key
        results = settings['analysis_results']
        if not isinstance(results, dict):
            raise InvalidState('analysis_results must be an object')
        for key, record in results.items():
            if not isinstance(record, dict):
                raise InvalidState('Analysis results must be objects')
            try:
                validate_result(record)
                if key != result_key(record['path']):
                    raise ValueError('Analysis result path binding changed')
            except (ValueError, TypeError, KeyError) as exc:
                raise InvalidState('Invalid saved analysis result') from exc
    # Unknown namespaces and settings remain byte-value-equivalent on roundtrip.
    return data


def validate_state(data: Any) -> dict:
    """Validate and detach caller-owned state before retaining/publication."""
    return copy.deepcopy(_validate_state(data))


def _same_json(left: Any, right: Any) -> bool:
    """Compare already-owned JSON without conflating bool/int or map order.

    This is only a no-op hint against a successfully encoded private document;
    unknown Python values still take the full validator/encoder path.
    """
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if (list(left) != list(right)
                or any(type(key) is not str for key in left)):
            return False
        # Settings commonly hold large saved grids before small control values.
        # Find a changed scalar before walking those unrelated nested records.
        # Delay only containers; order, types and scalar representation retain
        # the same exact comparison, including signed zero.
        nested = []
        for key, value in left.items():
            if type(value) in (dict, list):
                nested.append((value, right[key]))
            elif not _same_json(value, right[key]):
                return False
        return all(_same_json(value, other) for value, other in nested)
    if type(left) is list:
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right))
    if type(left) is float:
        # JSON preserves the sign on zero and the numeric representation.
        return bool(left.hex() == right.hex())
    if type(left) in (str, int, bool, type(None)):
        return bool(left == right)
    return False


def _read_document(path: Path) -> tuple[dict, dict, dict, bool, bytes, tuple]:
    # json.loads created this tree solely for the reader. The repository keeps
    # it privately and detaches once on return; a second complete copy here adds
    # no ownership boundary. Public validate_state still always detaches.
    raw, epoch = state_records.capture_file(path)
    physical = json.loads(raw)
    if isinstance(physical, dict) and state_records.MARKER in physical:
        document, epochs, specifications = state_records.expand(path, physical)
        after, after_epoch = state_records.capture_file(path)
        if after != raw or after_epoch != epoch:
            raise StateChangedError('Library changed while reading its song records')
        return _validate_state(document), epochs, specifications, True, raw, epoch
    return _validate_state(physical), {}, {}, False, raw, epoch


def read_state(path: Path) -> dict:
    """Read logical state from legacy portable JSON or its versioned record store."""
    return _read_document(path)[0]


def portable_state(path: Path) -> bytes:
    """Materialize a complete standalone JSON snapshot, including per-song data.

    Use under the area's lock when capturing live state. A manifest alone is
    intentionally not a portable library and older readers reject its shape.
    Legacy snapshots retain their exact bytes.
    """
    data, _, _, stored, raw, _ = _read_document(path)
    if not stored:
        return raw
    payload = state_records.encode(data)
    if len(payload) > _MAX_STATE_BYTES:
        raise InvalidState('Library JSON exceeds the 64 MiB safety limit')
    return payload


@dataclass(frozen=True)
class Recovery:
    damaged: Path
    backup: Path | None


class StateRepository:
    def __init__(self, path: Path):
        self.path = path
        self.area = state_area(path.parent)
        self.generation = self.area.generation
        self._document: dict = {}
        self.recovery: Recovery | None = None
        self.writable = True
        self.backup_error: OSError | None = None
        self._saved_payload: bytes | None = None
        self._saved_identity: tuple | None = None
        self._record_epochs: dict[str, tuple] = {}
        self._record_specs: dict = {}
        self._storage_active = False

    def _identity(self) -> tuple | None:
        try:
            st = self.path.stat()
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            return None

    def load(self) -> dict:
        with self.area.lock:
            self.generation = self.area.generation
            self._saved_payload = None
            self._saved_identity = None
            self._record_epochs = {}
            self._record_specs = {}
            self._storage_active = False
            self.recovery = None
            self.writable = True
            if not self.path.exists():
                self._document = {}
                return {}
            try:
                (data, self._record_epochs, self._record_specs, self._storage_active,
                 self._saved_payload, self._saved_identity) = _read_document(self.path)
            except (OSError, ValueError):
                damaged = self.path.with_name(f'library.corrupt-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json')
                # Preserve the exact damaged bytes; never rename away the only
                # library before a recoverable replacement has been selected.
                atomic_bytes(damaged, self.path.read_bytes())
                data = {}
                chosen = None
                candidates = sorted((self.path.parent / 'backups').glob('*.json'),
                                    key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for candidate in candidates:
                    try:
                        payload = portable_state(candidate)
                        data = _validate_state(json.loads(payload))
                    except (OSError, ValueError):
                        continue
                    chosen = candidate
                    self.area.publish({'library.json': payload})
                    break
                self.recovery = Recovery(damaged, chosen)
                # No usable backup: allow explicit restore, but forbid ordinary
                # UI autosaves from burying a damaged library with empty state.
                self.writable = chosen is not None
            self._document = data
            if self.recovery is not None:
                self._saved_identity = self._identity()
            return copy.deepcopy(data)

    def backup(self) -> None:
        with self.area.lock:
            if not self.path.exists():
                return
            if self.path.stat().st_size > _MAX_STATE_BYTES:
                raise InvalidState('Library JSON exceeds the 64 MiB safety limit')
            if self._storage_active:
                self._assert_record_base()
                self._record_epochs = state_records.validate_epochs(self.path, self._record_epochs)
                directory = self.path.parent / 'backups'
                old = sorted(directory.glob('library-*.json'))
                if old and time.time() - old[-1].stat().st_mtime < 3600:
                    return
                current = portable_state(self.path)
            else:
                current = self.path.read_bytes()
            if len(current) > _MAX_STATE_BYTES:
                raise InvalidState('Library JSON exceeds the 64 MiB safety limit')
            # The last published payload was fully validated. Reuse that pure
            # result only after reading and comparing every current byte; a
            # changed/corrupt file still takes the complete validation path.
            if current != self._saved_payload and not self._storage_active:
                validate_state(json.loads(current.decode('utf-8')))
            directory = self.path.parent / 'backups'
            directory.mkdir(parents=True, exist_ok=True)
            old = sorted(directory.glob('library-*.json'))
            if old and (time.time() - old[-1].stat().st_mtime < 3600
                        or old[-1].read_bytes() == current):
                return
            dest = directory / f'library-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json'
            atomic_bytes(dest, current)
            for path in sorted(directory.glob('library-*.json'))[:-10]:
                path.unlink()

    def save(self, changes: dict) -> None:
        with self.area.lock:
            if self.generation != self.area.generation:
                raise StateChangedError('Library was restored; reload before saving')
            if not self.writable:
                raise OSError('Library recovery required; restore a valid backup before saving')
            candidate = {**self._document, **changes}
            if self._storage_active or any(candidate.get('settings', {}).get(key)
                                           for key in state_records.NAMESPACES
                                           if isinstance(candidate.get('settings', {}), dict)):
                self._save_records(candidate)
                return
            if (self._saved_payload is not None and self._saved_identity is not None
                    and _same_json(candidate, self._document)
                    and self._identity() == self._saved_identity):
                return
            document = validate_state(candidate)
            payload = json.dumps(document, indent=2, allow_nan=False).encode('utf-8')
            if len(payload) > _MAX_STATE_BYTES:
                raise InvalidState('Library JSON exceeds the 64 MiB safety limit; previous state was preserved')
            if self._saved_payload is None:
                identity = self._identity()
                try:
                    existing = self.path.read_bytes()
                except OSError:
                    existing = None
                if existing == payload and identity is not None and identity == self._identity():
                    self._document = document
                    self._saved_payload = payload
                    self._saved_identity = identity
            if (payload == self._saved_payload and self._saved_identity is not None
                    and self._identity() == self._saved_identity):
                return
            self.backup_error = None
            try:
                self.backup()
            except OSError as exc:
                self.backup_error = exc
            self.area.publish({'library.json': payload})
            self._document = document
            self._saved_payload = payload
            self._saved_identity = self._identity()

    def _assert_record_base(self) -> None:
        if self._saved_identity is not None and self._identity() != self._saved_identity:
            raise StateChangedError('Canonical library changed; reload before saving')

    def _save_records(self, candidate: dict) -> None:
        """Publish changed owned records and a small ordered manifest together."""
        self._assert_record_base()
        epochs = state_records.validate_epochs(self.path, self._record_epochs)
        slim = dict(candidate)
        settings = dict(candidate.get('settings', {}))
        slim['settings'] = settings
        previous = self._document.get('settings', {})
        selected: dict[str, Any] = {}
        for namespace, kind in state_records.NAMESPACES.items():
            if namespace not in settings:
                continue
            values = settings[namespace]
            if not isinstance(values, dict if kind == 'map' else list):
                raise InvalidState(f'{namespace} must be a {kind}')
            selected[namespace] = values
            settings[namespace] = {} if kind == 'map' else []
        document = validate_state(slim)
        writes: dict[str, bytes | Path | None] = {}
        specifications = {}
        record_costs = {}
        logical_bytes = len(state_records.encode(document))
        count = 0
        for namespace, values in selected.items():
            kind = state_records.NAMESPACES[namespace]
            old_values: Any = previous.get(namespace, {} if kind == 'map' else [])
            if not isinstance(old_values, dict if kind == 'map' else list):
                old_values = {} if kind == 'map' else []
            old_entries = dict(self._record_specs.get(namespace, {}).get('entries', []))
            reusable = {}
            if kind == 'list':
                # Completing/reordering one song must not detach every later
                # dense job merely because its manifest position changed.
                ids = [row.get('id') for row in old_values if isinstance(row, dict)]
                incoming_ids = [row.get('id') for row in values if isinstance(row, dict)]
                if (len(ids) == len(old_values) and len(incoming_ids) == len(values)
                        and all(type(key) is str and key for key in ids + incoming_ids)
                        and len(set(ids)) == len(ids) and len(set(incoming_ids)) == len(incoming_ids)):
                    reusable = {row['id']: (row, old_entries.get(str(i)))
                                for i, row in enumerate(old_values)}
            entries = []
            owned: Any = {} if kind == 'map' else []
            if values:
                logical_bytes += 4
            items = values.items() if kind == 'map' else ((str(i), value) for i, value in enumerate(values))
            for key, value in items:
                count += 1
                if count > state_records.MAX_RECORDS or type(key) is not str:
                    raise InvalidState('Invalid or excessive canonical state records')
                before = (old_values.get(key) if kind == 'map' else
                          old_values[int(key)] if int(key) < len(old_values) else None)
                digest = old_entries.get(key)
                if (kind == 'list' and isinstance(value, dict) and type(value.get('id')) is str
                        and value['id'] in reusable):
                    before, digest = reusable[value['id']]
                if digest is not None and _same_json(value, before):
                    retained = before
                    cost = epochs[digest][5]
                else:
                    if namespace == 'analysis_results':
                        _validate_state({'settings': {'analysis_results': {key: value}}})
                    retained = copy.deepcopy(value)
                    payload = state_records.encode(retained)
                    cost = len(payload) + 6 * payload.count(b'\n')
                    digest = hashlib.sha256(payload).hexdigest()
                    if digest not in epochs:
                        target = state_records.record_path(self.path, digest)
                        if target.exists() or target.is_symlink():
                            _, epoch = state_records.capture(self.path, digest)
                            epochs[digest] = (*epoch, cost)
                        else:
                            writes[f'{state_records.DIRECTORY}/{digest}.json'] = payload
                logical_bytes += state_records.entry_cost(kind, key, cost)
                record_costs[digest] = cost
                if logical_bytes > _MAX_STATE_BYTES:
                    raise InvalidState('Library state exceeds the 64 MiB safety limit; previous state was preserved')
                entries.append([key, digest])
                if kind == 'map':
                    owned[key] = retained
                else:
                    owned.append(retained)
            specifications[namespace] = {'kind': kind, 'entries': entries}
            document['settings'][namespace] = owned
        if self._storage_active and _same_json(document, self._document):
            self._record_epochs = epochs
            return
        body = dict(document)
        body['settings'] = dict(document['settings'])
        for namespace in specifications:
            body['settings'][namespace] = None
        # settings=[] causes older state validators to reject this manifest,
        # rather than silently use a partial library without the song payloads.
        manifest = {state_records.MARKER: 1, 'settings': [], 'revision': uuid.uuid4().hex,
                    'document': body, 'records': specifications}
        payload = state_records.encode(manifest)
        if len(payload) > _MAX_STATE_BYTES:
            raise InvalidState('Library state exceeds the 64 MiB safety limit; previous state was preserved')
        self.backup_error = None
        try:
            self.backup()
        except OSError as exc:
            self.backup_error = exc
        self._assert_record_base()
        epochs = state_records.validate_epochs(self.path, epochs)
        live = state_records.references(specifications)
        # Only previously referenced canonical files may be retired; unknown
        # occupants and preserved corruption/recovery evidence are never swept.
        for digest in state_records.references(self._record_specs) - live:
            writes[f'{state_records.DIRECTORY}/{digest}.json'] = None
        writes['library.json'] = payload
        self.area.publish(writes)
        self._document = document
        self._saved_payload = payload
        self._saved_identity = self._identity()
        self._record_specs = specifications
        self._record_epochs = {digest: (*state_records.identity(state_records.record_path(self.path, digest)),
                                       record_costs[digest])
                               for digest in live}
        self._storage_active = True
