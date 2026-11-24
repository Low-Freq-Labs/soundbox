"""Portable library schema, recovery and durable persistence, independent of Qt."""
from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
import hashlib
from itertools import chain
import json
import math
import time
import uuid
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

from .state_io import StateChangedError, atomic_bytes, state_area
from . import state_records
from .mutation_domain import FrozenDict, FrozenList, ObservedDict, ObservedList


_MAX_STATE_BYTES = 64 * 1024**2


class InvalidState(ValueError):
    pass


class VerificationChanged(StateChangedError):
    """Another canonical owner won during a worker check; capture and retry."""


StatePath = tuple[str, ...]
StateReadPath = tuple[str | int, ...]
_OBSERVED_TYPES: dict[type, type] = {ObservedDict: dict, ObservedList: list}
_JSON_CONTAINER_TYPES = {**_OBSERVED_TYPES, FrozenDict: dict, FrozenList: list}
_VERIFICATION_LEAF_LIMIT = 32


def _read_paths(paths: Iterable[StateReadPath]) -> tuple[StateReadPath, ...]:
    owned=tuple(paths)
    if any(not isinstance(path,tuple) or not path or any(
            not (type(part) is str and part and '\0' not in part
                 or type(part) is int and part>=0) for part in path) for path in owned):
        raise InvalidState('Selected read paths require dictionary keys or nonnegative integer indices')
    selected=set(owned)
    if len(selected)!=len(owned) or any(path[:i] in selected for path in owned for i in range(1,len(path))):
        raise InvalidState('Overlapping selected read paths')
    return owned


def _read_value(document, path: StateReadPath) -> tuple[bool, Any, bool]:
    current=document
    for key in path:
        if type(key) is str and isinstance(current,dict):
            if key not in current:return False,None,True
            current=current[key]
        elif type(key) is int and isinstance(current,list):
            if key>=len(current):return False,None,True
            current=current[key]
        else:return False,None,False
    return True,current,True


@dataclass(frozen=True)
class _PatchToken:
    owner: str
    generation: int
    revision: int


def _patch_paths(paths: Iterable[StatePath]) -> tuple[StatePath, ...]:
    owned = tuple(paths)
    if any(not isinstance(path, tuple) or not path
           or any(type(part) is not str or not part or '\0' in part for part in path)
           for path in owned):
        raise InvalidState('State patch paths must be nonempty tuples of dictionary keys')
    selected = set(owned)
    if len(selected) != len(owned) or any(path[:i] in selected for path in owned for i in range(1, len(path))):
        raise InvalidState('Overlapping state patch paths')
    return owned


@dataclass(frozen=True)
class StatePatch:
    """Small immutable intent, detached without visiting the durable document."""
    _token: _PatchToken
    _payload: bytes = dataclass_field(repr=False)

    def edits(self) -> tuple[dict[StatePath, Any], tuple[StatePath, ...]]:
        """Return fresh caller-owned leaves only, never repository-owned state."""
        try:
            if type(self._payload) is not bytes or len(self._payload) > _MAX_STATE_BYTES:
                raise ValueError('Invalid patch payload')
            data = json.loads(self._payload)
            if (not isinstance(data, dict) or set(data) != {'set', 'remove'}
                    or not isinstance(data['set'], list) or not isinstance(data['remove'], list)
                    or any(not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], list)
                           for row in data['set'])
                    or any(not isinstance(path, list) for path in data['remove'])):
                raise ValueError('Invalid patch operations')
            paths = tuple(tuple(row[0]) for row in data['set'])
            removals = tuple(tuple(path) for path in data['remove'])
            if not paths and not removals:
                raise ValueError('Empty patch operations')
            _patch_paths((*paths, *removals))
            replacements = {path: row[1] for path, row in zip(paths, data['set'])}
        except (ValueError, TypeError) as exc:
            raise InvalidState('Invalid immutable state patch') from exc
        return replacements, removals


@dataclass(frozen=True)
class StateCommit:
    patch: StatePatch
    generation: int
    revision: int
    backup_warning: str = ''


@dataclass(frozen=True)
class StateVerification:
    """Opaque current-canonical proof; it contains no private document values."""
    _token: _PatchToken

    @property
    def generation(self) -> int:return self._token.generation

    @property
    def revision(self) -> int:return self._token.revision


def apply_state_patch(document: dict, replacements: Mapping[StatePath, Any],
                      removals: Iterable[StatePath] = ()) -> dict:
    """Copy each affected dictionary ancestor once; preserve untouched owners.

    Sparse path authority and overlap policy are identical to StatePatch. The
    trie contains only admitted edits, never a copy/walk of unrelated state.
    """
    removed = tuple(removals)
    _patch_paths((*replacements, *removed))
    tree: dict[str, Any] = {}
    for path, value, remove in chain(
            ((path, value, False) for path, value in replacements.items()),
            ((path, None, True) for path in removed)):
        branch = tree
        for key in path[:-1]:
            branch = branch.setdefault(key, {})
        branch[path[-1]] = (remove, value)

    def visit(previous: dict, edits: dict[str, Any]) -> dict:
        result: dict | None = None
        for key, edit in edits.items():
            if isinstance(edit, tuple):
                remove, value = edit
                if remove and key not in previous:
                    continue
            else:
                child = previous.get(key, {})
                if not isinstance(child, dict):
                    raise InvalidState('State patch crosses a non-dictionary value')
                value = visit(child, edit)
                if value is child:
                    continue
                remove = False
            if result is None:
                result = dict(previous)
            if remove:
                result.pop(key, None)
            else:
                result[key] = value
        return previous if result is None else result
    return visit(document, tree)


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
    if (type(left) in _OBSERVED_TYPES and left.revision % 2
            or type(right) in _OBSERVED_TYPES and right.revision % 2):
        return False
    if left is right:
        return True
    left_type = _JSON_CONTAINER_TYPES.get(type(left), type(left))
    right_type = _JSON_CONTAINER_TYPES.get(type(right), type(right))
    if left_type is not right_type:
        return False
    if left_type is dict:
        if (list(left) != list(right)
                or any(type(key) is not str for key in left)):
            return False
        # Settings commonly hold large saved grids before small control values.
        # Find a changed scalar before walking those unrelated nested records.
        # Delay only containers; order, types and scalar representation retain
        # the same exact comparison, including signed zero.
        nested = []
        for key, value in left.items():
            if type(value) in (dict, list) or type(value) in _JSON_CONTAINER_TYPES:
                other = right[key]
                if value is other:
                    if type(value) in _OBSERVED_TYPES and value.revision % 2:return False
                else:nested.append((value, other))
            elif not _same_json(value, right[key]):
                return False
        return all(_same_json(value, other) for value, other in nested)
    if left_type is list:
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right))
    if left_type is float:
        # JSON preserves the sign on zero and the numeric representation.
        return bool(left.hex() == right.hex())
    if left_type in (str, int, bool, type(None)):
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
    """Read canonical song records or a complete portable backup document."""
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
        self._patch_owner = uuid.uuid4().hex
        self._revision = 0
        self._document: dict = {}
        self.recovery: Recovery | None = None
        self.writable = True
        self.backup_error: OSError | None = None
        self._saved_payload: bytes | None = None
        self._saved_identity: tuple | None = None
        self._record_epochs: dict[str, tuple] = {}
        # Equality witnesses never expose the private canonical values. Only
        # deeply immutable caller snapshots can retain a positive comparison.
        self._verification_leaves: dict[StateReadPath, tuple[Any, Any]] = {}
        self._record_specs: dict = {}
        self._namespace_costs: dict[str, int] = {}
        self._storage_active = False

    def _identity(self) -> tuple | None:
        try:
            st = self.path.stat()
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            return None

    def load(self) -> dict:
        with self.area.lock:
            try:
                return self._load()
            finally:
                # A lock-free capture made during loading must become obsolete
                # after the complete new private baseline has been installed.
                self._revision += 1

    def _load(self) -> dict:
        with self.area.lock:
            self._verification_leaves.clear()
            self.generation = self.area.generation
            self._saved_payload = None
            self._saved_identity = None
            self._record_epochs = {}
            self._record_specs = {}
            self._namespace_costs = {}
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
            self._namespace_costs = {
                namespace: (4 if spec['entries'] else 0) + sum(
                    state_records.entry_cost(spec['kind'], key, self._record_epochs[digest][5])
                    for key, digest in spec['entries'])
                for namespace, spec in self._record_specs.items()}
            if self.recovery is not None:
                self._saved_identity = self._identity()
            return copy.deepcopy(data)

    def backup(self) -> None:
        self._backup()

    def _backup(self, *, records_checked: bool = False) -> None:
        with self.area.lock:
            if not self.path.exists():
                return
            if self.path.stat().st_size > _MAX_STATE_BYTES:
                raise InvalidState('Library JSON exceeds the 64 MiB safety limit')
            if self._storage_active:
                self._assert_record_base()
                if not records_checked:
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

    def prepare_patch(self, replacements: Mapping[StatePath, Any],
                      removals: Iterable[StatePath] = ()) -> StatePatch:
        """Freeze only changed leaves; safe for an admission thread without IO.

        The caller serializes publication admission. A competing load/save is
        harmless: its version change makes this recipe fail before publication.
        Complete validation, record checks and durability belong to commit_patch.
        """
        token = _PatchToken(self._patch_owner, self.generation, self._revision)
        rows = tuple(replacements.items())
        removed = tuple(removals)
        _patch_paths((*(path for path, _value in rows), *removed))
        if not rows and not removed:
            raise InvalidState('State patch has no changes')
        try:
            payload = json.dumps({'set': rows, 'remove': removed}, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError) as exc:
            raise InvalidState('State patch values must be finite JSON data') from exc
        if len(payload) > _MAX_STATE_BYTES:
            raise InvalidState('State patch exceeds the state size limit')
        return StatePatch(token, payload)

    def commit_patch(self, patch: StatePatch) -> StateCommit:
        """Commit a previously admitted delta on the caller's ordered worker.

        No GUI-owned document is read here. The ordinary save path retains all
        validation, fresh record/file guards, backups and atomic rollback rules.
        """
        with self.area.lock:
            token = _PatchToken(self._patch_owner, self.generation, self._revision)
            if (not isinstance(patch, StatePatch) or patch._token != token
                    or self.generation != self.area.generation):
                raise StateChangedError('State patch is obsolete; capture it again before saving')
            if self._identity() != self._saved_identity:
                raise StateChangedError('Canonical library changed; reload before saving')
            replacements, removals = patch.edits()
            candidate = apply_state_patch(self._document, replacements, removals)
            self._save(candidate, complete=True)
            self._revision += 1
            return StateCommit(patch, self.generation, self._revision,
                               str(self.backup_error) if self.backup_error else '')

    def current_commit(self, result: StateCommit) -> bool:
        """Fence queued delivery without taking the state lock or reading files."""
        return (isinstance(result, StateCommit) and isinstance(result.patch, StatePatch)
                and result.patch._token.owner == self._patch_owner
                and result.patch._token.generation == result.generation
                and result.generation == self.generation == self.area.generation
                and result.revision == self._revision)

    def contains_selected_path(self, path: StateReadPath) -> bool:
        """Inspect baseline presence without IO or exposing canonical values.

        This is a capture aid, never durability proof. A caller still verifies
        the exact selected values or absence on its ordered checkpoint worker.
        """
        _read_paths((path,))
        return _read_value(self._document,path)[0]

    def verify_selected_values(self, expected: Mapping[StateReadPath, Any], *,
                               missing: Iterable[StateReadPath] = ()) -> StateVerification:
        """Worker comparison to fresh canonical state, without GUI IO.

        The caller supplies detached, semantically validated domain snapshots.
        Private documents are detached/copy-on-write: capture their reference
        briefly, compare outside the area lock, then fence the publication owner.
        Only a positive exact immutable/private leaf pair reuses its comparison;
        every call still checks the manifest, every record and the final owner.
        A GUI save never waits for this complete semantic or filesystem pass.
        """
        missing = tuple(missing)
        _read_paths((*expected, *missing))
        selected = copy.deepcopy(dict(expected))
        with self.area.lock:
            if self.generation != self.area.generation or not self.writable:
                self._verification_leaves.clear()
                raise StateChangedError('Canonical state needs reload before domain validation')
            token = _PatchToken(self._patch_owner, self.generation, self._revision)
            document = self._document
            saved_identity = self._saved_identity
            epochs = dict(self._record_epochs)
            witnesses = dict(self._verification_leaves)
        def fence(promote=()):
            with self.area.lock:
                if (token != _PatchToken(self._patch_owner, self.generation, self._revision)
                        or self.generation != self.area.generation):
                    raise VerificationChanged('Canonical owner changed during domain validation; capture again')
                if self._saved_identity != saved_identity or self._identity() != saved_identity:
                    raise StateChangedError('Canonical library changed during domain validation')
                for path, immutable, private in promote:
                    self._verification_leaves.pop(path, None)
                    self._verification_leaves[path] = immutable, private
                while len(self._verification_leaves) > _VERIFICATION_LEAF_LIMIT:
                    self._verification_leaves.pop(next(iter(self._verification_leaves)))
        if self._identity() != saved_identity:fence()
        different = False
        positive = []
        for path, value in selected.items():
            found,current,_valid = _read_value(document,path)
            if not found:different=True
            immutable = type(value) in (FrozenDict, FrozenList)
            witness = witnesses.get(path) if immutable else None
            same = witness is not None and witness[0] is value and witness[1] is current
            if different or not same and not _same_json(value, current):
                different = True
                break
            if immutable:positive.append((path, value, current))
        for path in missing:
            found,_current,valid=_read_value(document,path)
            if found or not valid:different=True
        drift = None
        try:state_records.validate_epochs(self.path, epochs)
        except (OSError, ValueError) as exc:drift = exc
        fence(positive if drift is None and not different else ())
        if drift is not None:raise StateChangedError('Canonical records changed during domain validation') from drift
        if different:raise StateChangedError('Selected domain differs from saved canonical state')
        return StateVerification(token)

    def _prune_verification_leaves(self) -> None:
        """Keep witnesses only while their unexposed canonical owner survives."""
        for path, (_immutable, private) in tuple(self._verification_leaves.items()):
            _found,current,_valid=_read_value(self._document,path)
            if current is not private:self._verification_leaves.pop(path)

    def current_verification(self, proof: StateVerification) -> bool:
        """Check queued proof ownership without a lock or filesystem access."""
        return (isinstance(proof, StateVerification) and proof._token.owner == self._patch_owner
                and proof.generation == self.generation == self.area.generation
                and proof.revision == self._revision)

    def save(self, changes: dict) -> None:
        with self.area.lock:
            self._save(changes)
            # Even a successful no-op is another serialized publication owner.
            self._revision += 1

    def _save(self, changes: dict, *, complete: bool = False) -> None:
        with self.area.lock:
            if self.generation != self.area.generation:
                self._verification_leaves.clear()
                raise StateChangedError('Library was restored; reload before saving')
            if not self.writable:
                raise OSError('Library recovery required; restore a valid backup before saving')
            candidate = changes if complete else {**self._document, **changes}
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
                    self._prune_verification_leaves()
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
            if complete and self._identity() != self._saved_identity:
                raise StateChangedError('Canonical library changed; reload before saving')
            self.area.publish({'library.json': payload})
            self._document = document
            self._prune_verification_leaves()
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
        observed: list[tuple[Any, int]] = []
        for namespace, kind in state_records.NAMESPACES.items():
            if namespace not in settings:
                continue
            values = settings[namespace]
            if not isinstance(values, dict if kind == 'map' else list):
                raise InvalidState(f'{namespace} must be a {kind}')
            if type(values) in _OBSERVED_TYPES:
                observed_value: Any = values
                revision = observed_value.revision
                if revision % 2:raise InvalidState('Observed state is changing; capture it after the mutation')
                observed.append((observed_value, revision))
            selected[namespace] = values
            settings[namespace] = {} if kind == 'map' else []
        document = validate_state(slim)
        writes: dict[str, bytes | Path | None] = {}
        specifications = {}
        namespace_costs = {}
        record_costs = {}
        logical_bytes = len(state_records.encode(document))
        count = 0
        for namespace, values in selected.items():
            kind = state_records.NAMESPACES[namespace]
            old_values: Any = previous.get(namespace, {} if kind == 'map' else [])
            if not isinstance(old_values, dict if kind == 'map' else list):
                old_values = {} if kind == 'map' else []
            old_spec = self._record_specs.get(namespace)
            if (values is old_values and old_spec is not None
                    and namespace in self._namespace_costs):
                # Only the repository's unexposed, copy-on-write baseline may
                # reuse a complete namespace without comparing caller values.
                # Its exact encoded cost/order/count were validated at load or
                # publication; both fresh full record guards still run.
                count += len(old_spec['entries'])
                logical_bytes += self._namespace_costs[namespace]
                if count > state_records.MAX_RECORDS:
                    raise InvalidState('Invalid or excessive canonical state records')
                if logical_bytes > _MAX_STATE_BYTES:
                    raise InvalidState('Library state exceeds the 64 MiB safety limit; previous state was preserved')
                specifications[namespace] = old_spec
                namespace_costs[namespace] = self._namespace_costs[namespace]
                document['settings'][namespace] = old_values
                continue
            old_entries = {entry[0]: entry for entry in (old_spec or {}).get('entries', [])}
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
            namespace_cost = 0
            if values:
                logical_bytes += 4
                namespace_cost += 4
            items = values.items() if kind == 'map' else ((str(i), value) for i, value in enumerate(values))
            for key, value in items:
                count += 1
                if count > state_records.MAX_RECORDS or type(key) is not str:
                    raise InvalidState('Invalid or excessive canonical state records')
                before = (old_values.get(key) if kind == 'map' else
                          old_values[int(key)] if int(key) < len(old_values) else None)
                prior_entry = old_entries.get(key)
                if (kind == 'list' and isinstance(value, dict) and type(value.get('id')) is str
                        and value['id'] in reusable):
                    before, prior_entry = reusable[value['id']]
                digest = prior_entry[1] if prior_entry is not None else None
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
                contribution = state_records.entry_cost(kind, key, cost)
                logical_bytes += contribution
                namespace_cost += contribution
                record_costs[digest] = cost
                if logical_bytes > _MAX_STATE_BYTES:
                    raise InvalidState('Library state exceeds the 64 MiB safety limit; previous state was preserved')
                entries.append(prior_entry if (prior_entry is not None and prior_entry[0] == key
                                              and prior_entry[1] == digest)
                               else [key, digest])
                if kind == 'map':
                    owned[key] = retained
                else:
                    owned.append(retained)
            specifications[namespace] = {'kind': kind, 'entries': entries}
            namespace_costs[namespace] = namespace_cost
            document['settings'][namespace] = owned
        if any(value.revision != revision or value.revision % 2 for value, revision in observed):
            raise InvalidState('Observed state changed while composing its canonical snapshot')
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
            # This save checked every record before composition and checks it
            # again before publication. A recent-backup no-op needs no third
            # sweep; an actual portable backup still reads/hashes every record.
            self._backup(records_checked=True)
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
        # Keep the exact epochs proved before publication. A blind fresh stat
        # here would bless unrelated external tampering after the final guard.
        # Only newly published digests need a stable byte/hash capture.
        published_epochs = {}
        for digest in live:
            if digest in epochs:published_epochs[digest] = epochs[digest]
            else:
                _, epoch = state_records.capture(self.path, digest)
                published_epochs[digest] = (*epoch, record_costs[digest])
        self._document = document
        self._prune_verification_leaves()
        self._saved_payload = payload
        self._saved_identity = self._identity()
        self._record_specs = specifications
        self._namespace_costs = namespace_costs
        self._record_epochs = published_epochs
        self._storage_active = True
