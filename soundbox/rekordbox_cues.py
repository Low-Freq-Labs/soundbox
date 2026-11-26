"""Pure cue-row planning from native references; no database/file write API.

The accepted rekordbox_cue_store composes these plans with its separate
authority, live transaction and recovery guards; this planner grants none.
Inputs are raw snapshot rows, not the lossy display/analysis cue dictionaries.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
import re
from struct import pack, unpack
from sys import getsizeof
from threading import Lock
from typing import Any, Literal
from uuid import UUID

from .rekordbox_write import ConflictError, WriteProofError, _digest

Row = dict[str, Any]
_CUE_COLUMNS = (
    'ID', 'ContentID', 'InMsec', 'InFrame', 'InMpegFrame', 'InMpegAbs',
    'OutMsec', 'OutFrame', 'OutMpegFrame', 'OutMpegAbs', 'Kind', 'Color',
    'ColorTableIndex', 'ActiveLoop', 'Comment', 'BeatLoopSize', 'CueMicrosec',
    'InPointSeekInfo', 'OutPointSeekInfo', 'ContentUUID', 'UUID',
    'rb_data_status', 'rb_local_data_status', 'rb_local_deleted',
    'rb_local_synced', 'usn', 'rb_local_usn', 'created_at', 'updated_at',
)
_AGGREGATE_COLUMNS = (
    'ID', 'ContentID', 'Cues', 'rb_cue_count', 'UUID', 'rb_data_status',
    'rb_local_data_status', 'rb_local_deleted', 'rb_local_synced', 'usn',
    'rb_local_usn', 'created_at', 'updated_at',
)
_INTERNAL = frozenset(('rb_data_status', 'rb_local_data_status',
                       'rb_local_deleted', 'rb_local_synced', 'usn', 'rb_local_usn'))
_FORMATS = frozenset((1, 11, 12))  # native MP3, WAV, AIFF references only
_REVISION_SCALARS = (str, int, bytes, type(None))


class _RevisionCache:
    """Bounded pure results; keys contain every freshly observed native cell."""

    def __init__(self, entries=8192, byte_limit=96 * 1024 * 1024,
                 entry_limit=128 * 1024):
        self.entries = entries
        self.byte_limit = byte_limit
        self.entry_limit = entry_limit
        self._values: OrderedDict[tuple, tuple[str, int]] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, key) -> str | None:
        with self._lock:
            found = self._values.get(key)
            if found is not None:
                self._values.move_to_end(key)
                return found[0]
        return None

    def remember(self, key, revision):
        def row_size(row):
            return (getsizeof(row) + getsizeof(row[0]) + getsizeof(row[1])
                    + sum(map(getsizeof, row[0])) + sum(map(getsizeof, row[1]))
                    + sum(getsizeof(value[0]) for value in row[1] if type(value) is tuple))
        # Conservatively count shared strings/scalars repeatedly, including
        # OrderedDict bookkeeping. Oversized opaque rows remain uncached.
        rows = (key[0], *key[1], *((key[2],) if key[2] is not None else ()))
        weight = getsizeof(key) + getsizeof(key[1]) + sum(map(row_size, rows)) + getsizeof(revision) + 256
        if not self.entries or weight > min(self.entry_limit, self.byte_limit):
            return
        with self._lock:
            old = self._values.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._values[key] = (revision, weight)
            self._bytes += weight
            while len(self._values) > self.entries or self._bytes > self.byte_limit:
                _, (_, removed) = self._values.popitem(last=False)
                self._bytes -= removed


_REVISION_CACHE = _RevisionCache()


def _revision_row_key(row):
    if type(row) is not dict:
        return None
    keys = tuple(row)
    if any(type(key) is not str for key in keys):
        return None
    values = []
    for value in row.values():
        if type(value) in _REVISION_SCALARS:
            values.append(value)
        elif type(value) is float and isfinite(value):
            # Tuple inputs are never admitted; this marker cannot collide with
            # another SQLite cell. IEEE bytes distinguish signed zero and int.
            values.append((pack('>d', value),))
        else:
            return None
    return keys, tuple(values)


def _revision_key_row(key):
    return dict(zip(key[0], (unpack('>d', value[0])[0] if type(value) is tuple else value
                             for value in key[1])))


def _revision_cells(row):
    if row is None:
        return None
    encoded = None
    for key, value in row.items():
        if isinstance(value, bytes):
            if encoded is None:
                encoded = dict(row)
            encoded[key] = {'blob': value.hex()}
    return row if encoded is None else encoded


def _revision_digest(track, cues, aggregate) -> str:
    return _digest({'track': _revision_cells(track),
                    'cues': [_revision_cells(row) for row in cues],
                    'aggregate': _revision_cells(aggregate)})


@dataclass(frozen=True)
class CueSnapshot:
    track: Row
    cues: tuple[Row, ...]
    aggregate: Row | None

    @property
    def revision(self) -> str:
        """Includes opaque fields and all cues, independent of SQL row order."""
        try:
            track = _revision_row_key(self.track)
            cue_rows = tuple(_revision_row_key(row) for row in self.cues)
            aggregate = None if self.aggregate is None else _revision_row_key(self.aggregate)
            if (track is None or any(row is None for row in cue_rows)
                    or (self.aggregate is not None and aggregate is None)):
                return _revision_digest(self.track, sorted(self.cues, key=lambda r: r['ID']),
                                        self.aggregate)
            if any(type(row[1][row[0].index('ID')]) is not str for row in cue_rows):
                return _revision_digest(self.track, sorted(self.cues, key=lambda r: r['ID']),
                                        self.aggregate)
            cue_rows = tuple(sorted(cue_rows, key=lambda row: row[1][row[0].index('ID')]))
            key = track, cue_rows, aggregate
            found = _REVISION_CACHE.get(key)
            if found is not None:
                return found
            # Compute from the immutable captured cells, so later input mutation
            # cannot publish a digest under an earlier key. No source observation,
            # authority or SQL read is retained or bypassed by this pure reuse.
            revision = _revision_digest(_revision_key_row(track),
                                        [_revision_key_row(row) for row in cue_rows],
                                        None if aggregate is None else _revision_key_row(aggregate))
            _REVISION_CACHE.remember(key, revision)
            return revision
        except (KeyError, TypeError, ValueError) as exc:
            raise WriteProofError('Invalid cue snapshot') from exc


@dataclass(frozen=True)
class CueEdit:
    track_id: str
    track_uuid: str
    revision: str
    page: Literal['hot', 'memory']
    slot: int
    operation: Literal['add', 'delete']
    position_ms: int | None = None
    # Deletion binds the actual row; a reordered Memory slot cannot redirect it.
    cue_id: str = ''
    cue_uuid: str = ''


@dataclass(frozen=True)
class CueIdentities:
    """Caller allocates and collision-checks these against the entire database."""
    cue_id: str
    cue_uuid: str
    aggregate_uuid: str = ''


@dataclass(frozen=True)
class CueDelta:
    table: str
    key: str
    before: Row | None
    after: Row | None


def _json_time(value: str) -> str:
    try:
        # Native SQL timestamps include a space before the UTC offset; the
        # aggregate uses ISO timestamps without that extra separator.
        parsed = datetime.fromisoformat(value.replace(' +', '+').replace(' -', '-'))
        if parsed.utcoffset() is None:
            raise ValueError('Missing timezone')
        return parsed.isoformat(timespec='milliseconds')
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteProofError('Cue timestamp is not understood') from exc


def _public_cue(row: Row) -> Row:
    result = {k: v for k, v in row.items() if k not in _INTERNAL and v is not None}
    for name in ('created_at', 'updated_at'):
        result[name] = _json_time(result[name])
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WriteProofError('Cue aggregate has duplicate JSON keys')
        result[key] = value
    return result


def _aggregate_items(snapshot: CueSnapshot) -> list[Row]:
    aggregate = snapshot.aggregate
    if aggregate is None:
        if snapshot.cues:
            raise WriteProofError('Existing cues have no verified aggregate')
        return []
    if (set(aggregate) != set(_AGGREGATE_COLUMNS)
            or aggregate['ID'] != snapshot.track['UUID']
            or aggregate['ContentID'] != snapshot.track['ID']
            or not aggregate['UUID'] or aggregate['rb_cue_count'] is not None):
        raise WriteProofError('Unverified cue aggregate shape/identity')
    _check_sync(aggregate, historical_aggregate=True)
    try:
        items = json.loads(aggregate['Cues'], object_pairs_hook=_unique_object,
                           parse_constant=lambda _: _invalid_json())
    except (ValueError, TypeError) as exc:
        raise WriteProofError('Cue aggregate JSON is invalid') from exc
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise WriteProofError('Cue aggregate must be a list of cue records')
    by_id = {row['ID']: row for row in snapshot.cues}
    seen = set()
    for item in items:
        key = item.get('ID')
        if not isinstance(key, str) or key in seen or key not in by_id:
            raise WriteProofError('Cue aggregate membership is ambiguous')
        seen.add(key)
        row = by_id[key]
        public = _public_cue(row)
        # Preserve unknown aggregate metadata, but never ignore a mismatch in
        # the native row fields or carry internal synchronization fields in JSON.
        if (any(item.get(k) != v for k, v in public.items()
                if not (k == 'Comment' and v == '' and k not in item))
                or any(k in item for k in _INTERNAL)
                or any(k in item and item[k] is not None
                       for k, v in row.items() if v is None)):
            raise ConflictError('Cue rows and aggregate disagree; refresh before editing')
    if seen != set(by_id):
        raise ConflictError('Cue rows and aggregate disagree; refresh before editing')
    return items


def _invalid_json():
    raise WriteProofError('Nonfinite cue aggregate JSON')


def _check_sync(row: Row, *, historical_aggregate: bool = False) -> None:
    if (any(row[k] != 0 for k in ('rb_data_status', 'rb_local_data_status',
                                  'rb_local_deleted'))
            or row['rb_local_synced'] not in ((0, 1) if historical_aggregate else (0,))
            or row['usn'] is not None):
        raise WriteProofError('Unverified cue synchronization metadata')


def _validate(snapshot: CueSnapshot, edit: CueEdit, counter: Row) -> list[Row]:
    track = snapshot.track
    if (edit.track_id != track.get('ID') or edit.track_uuid != track.get('UUID')
            or not edit.track_id or not edit.track_uuid or track.get('rb_local_deleted')
            or edit.revision != snapshot.revision):
        raise ConflictError('Loaded track or cues changed; refresh before editing')
    if (edit.page not in ('hot', 'memory') or type(edit.slot) is not int
            or not 0 <= edit.slot < 8 or edit.operation not in ('add', 'delete')):
        raise WriteProofError('Invalid cue operation/page/slot')
    if type(track.get('FileType')) is not int or track['FileType'] not in _FORMATS:
        raise WriteProofError('This format has no native cue reference')
    if (counter.get('registry_id') != 'localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0 <= counter['int_1'] < 2**63 - 2):
        raise WriteProofError('Invalid local update counter')
    keys = set()
    uuids = set()
    hot_kinds = set()
    for row in snapshot.cues:
        if (set(row) != set(_CUE_COLUMNS) or row['ContentID'] != edit.track_id
                or row['ContentUUID'] != edit.track_uuid
                or not isinstance(row['ID'], str) or not row['ID'] or row['ID'] in keys
                or not isinstance(row['UUID'], str) or not row['UUID'] or row['UUID'] in uuids
                or type(row['Kind']) is not int
                or type(row['InMsec']) is not int or row['InMsec'] < 0
                or row['rb_local_deleted']):
            raise WriteProofError('Unverified or ambiguous cue rows')
        keys.add(row['ID'])
        uuids.add(row['UUID'])
        if row['Kind'] in (1, 2, 3, *range(5, 18)):
            if row['Kind'] in hot_kinds:
                raise WriteProofError('Duplicate Hot Cue slot')
            hot_kinds.add(row['Kind'])
    return _aggregate_items(snapshot)


def cue_at_slot(snapshot: CueSnapshot, page: str, slot: int) -> Row | None:
    """Only the visible eight slots; overflow remains part of the snapshot."""
    if type(slot) is not int or not 0 <= slot < 8 or page not in ('hot', 'memory'):
        raise WriteProofError('Invalid cue page/slot')
    if page == 'memory':
        memory = sorted((r for r in snapshot.cues if r['Kind'] == 0),
                        key=lambda r: (*_memory_order(r), r['ID']))
        target = memory[slot] if slot < len(memory) else None
        if target and sum(_memory_order(r) == _memory_order(target) for r in memory) > 1:
            raise WriteProofError('Memory Cue numbering is ambiguous at this position')
        return target
    kind = slot + 1 if slot < 3 else slot + 2
    rows = [r for r in snapshot.cues if r['Kind'] == kind]
    if len(rows) > 1:
        raise WriteProofError('Duplicate Hot Cue slot')
    return rows[0] if rows else None


def _memory_order(row: Row) -> tuple:
    raw_end = row['OutMsec']
    end = raw_end if type(raw_end) is int and raw_end > row['InMsec'] else -1
    return row['InMsec'], end, str(row['Comment'] or '').strip()


def plan_cue_edit(snapshot: CueSnapshot, counter: Row, edit: CueEdit, *,
                  now: datetime, identities: CueIdentities | None = None) -> tuple[CueDelta, ...]:
    """Return detached exact row deltas. Does not access SQL, audio or ANLZ.

    Empty deletion and duplicate-position Memory creation are no-ops. A filled
    Hot slot cannot be replaced through the empty-slot creation operation.
    """
    items = _validate(snapshot, edit, counter)
    target = cue_at_slot(snapshot, edit.page, edit.slot)
    if edit.operation == 'delete':
        if target is None:
            if edit.cue_id or edit.cue_uuid:
                raise ConflictError('The cue selected for deletion changed')
            return ()
        if edit.position_ms is not None or (edit.cue_id, edit.cue_uuid) != (target['ID'], target['UUID']):
            raise ConflictError('The cue selected for deletion changed')
        _check_sync(target)
        cue_delta = CueDelta('djmdCue', target['ID'], target, None)
        items = [item for item in items if item['ID'] != target['ID']]
    else:
        if edit.cue_id or edit.cue_uuid or target is not None:
            raise ConflictError('Cue slot is no longer empty')
        position = edit.position_ms
        length = snapshot.track.get('Length')
        if (type(position) is not int or position < 0 or position >= 2**31
                or type(length) is not int or length <= 0 or position >= (length + 1) * 1000):
            raise WriteProofError('Cue position is outside the loaded track')
        if edit.page == 'memory' and any(r['Kind'] == 0 and r['InMsec'] == position
                                         for r in snapshot.cues):
            return ()
        if identities is None:
            raise WriteProofError('New cue identities are required')
        _validate_identities(identities, snapshot)
        stamp = _stamp(now)
        row: Row = dict.fromkeys(_CUE_COLUMNS)
        row.update(ID=identities.cue_id, UUID=identities.cue_uuid,
                   ContentID=edit.track_id, ContentUUID=edit.track_uuid,
                   InMsec=position, InFrame=position * 150 // 1000,
                   InMpegFrame=0, InMpegAbs=0, OutMsec=-1, OutFrame=0,
                   OutMpegFrame=0, OutMpegAbs=0,
                   Kind=0 if edit.page == 'memory' else edit.slot + (1 if edit.slot < 3 else 2),
                   Color=-1, rb_data_status=0, rb_local_data_status=0,
                   rb_local_deleted=0, rb_local_synced=0, created_at=stamp, updated_at=stamp)
        cue_delta = CueDelta('djmdCue', row['ID'], None, row)
        items.append(_public_cue(row))
    stamp = _stamp(now)
    track = snapshot.track
    old_count = track.get('CueUpdated')
    if old_count is not None and (not isinstance(old_count, str)
            or not re.fullmatch(r'[0-9]{1,19}', old_count) or int(old_count) >= 2**63 - 1):
        raise WriteProofError('Unverified CueUpdated counter')
    next_count = int(old_count or '0') + 1
    old_aggregate = snapshot.aggregate
    if old_aggregate is None:
        assert identities is not None
        aggregate = dict.fromkeys(_AGGREGATE_COLUMNS)
        aggregate.update(ID=edit.track_uuid, ContentID=edit.track_id,
                         UUID=identities.aggregate_uuid, rb_data_status=0,
                         rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0,
                         created_at=stamp)
    else:
        aggregate = dict(old_aggregate)
    aggregate.update(Cues=json.dumps(items, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                     rb_local_usn=counter['int_1'] + 2, updated_at=stamp)
    # JSON preserves native insertion order. The memory panel sorts independently.
    return deepcopy((cue_delta,
        CueDelta('djmdContent', edit.track_id, track,
                 {**track, 'CueUpdated': str(next_count), 'rb_local_usn': counter['int_1'] + 1,
                  'updated_at': stamp}),
        CueDelta('contentCue', edit.track_uuid, old_aggregate, aggregate),
        CueDelta('agentRegistry', 'localUpdateCount', counter,
                 {**counter, 'int_1': counter['int_1'] + 2})))


def _stamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise WriteProofError('An explicit timezone-aware cue timestamp is required')
    return now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00'


def _validate_identities(ids: CueIdentities, snapshot: CueSnapshot) -> None:
    try:
        aggregate_id = ids.aggregate_uuid
        # Existing aggregate identity is retained; no replacement UUID is used.
        if snapshot.aggregate is None or aggregate_id:
            if str(UUID(aggregate_id)) != aggregate_id:
                raise ValueError('Invalid aggregate identity')
        if (not isinstance(ids.cue_id, str) or not re.fullmatch(r'[1-9][0-9]*', ids.cue_id)
                or not 0 < int(ids.cue_id) < 2**32
                or str(UUID(ids.cue_uuid)) != ids.cue_uuid
                or len({ids.cue_uuid, aggregate_id, snapshot.track['UUID']}) != 3
                or any(r['ID'] == ids.cue_id or r['UUID'] in (ids.cue_uuid, ids.aggregate_uuid)
                       for r in snapshot.cues)
                or snapshot.aggregate is not None and snapshot.aggregate['UUID'] in
                    (ids.cue_uuid, ids.aggregate_uuid)):
            raise ValueError('Invalid or reused identity')
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteProofError('New cue identities are invalid or reused') from exc
