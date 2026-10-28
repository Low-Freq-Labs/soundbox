"""Accepted track-bound source-grid transactions with exact recovery.

No library authority is provisioned by this module. Desktop admission requires
separate explicit native acceptance. Reuses the accepted
source DB lease/backup and its exact shared Tag/Cue lock and journal, while ANLZ
publication/recovery is bound to the native UUID directory and exact byte states.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore, _encode_row
from .rekordbox_grid import AmbiguousGridEnd, GridEdit, GridSnapshot, plan_grid_edit
from .pcm_endpoint import capture_pcm_endpoint
from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _directory,
    _inactive_journal_proof, _json, _quote, _regular,
)
from .state_io import atomic_bytes, sync_dir

_FORMAT = 'soundbox-native-grid-v1'
_PAYLOADS = frozenset(('operation.json', 'before.DAT', 'before.EXT', 'after.DAT',
                       'after.EXT', 'publish.DAT', 'publish.EXT'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fault(_phase: str):
    """Synthetic tests only. Never inject failures on an actual library."""


def _decode_row(row):
    return {k: bytes.fromhex(v['blob']) if isinstance(v, dict) and set(v) == {'blob'} else v
            for k, v in row.items()}


@dataclass(frozen=True)
class GridWriteAuthority(CueWriteAuthority):
    track_uuid: str = ''
    manual_bpm_accepted: bool = False

    def __post_init__(self):
        super().__post_init__()
        if type(self.manual_bpm_accepted) is not bool:
            raise WriteProofError('Invalid manual BPM acceptance')
        try:
            if str(UUID(self.track_uuid)) != self.track_uuid:
                raise ValueError('Noncanonical UUID')
        except (ValueError, AttributeError) as exc:
            raise WriteProofError('Grid pilot requires the exact native track UUID') from exc


@dataclass(frozen=True)
class GridLibraryAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted: bool
    application_version: str = '6.8.7.0303'
    manual_bpm_accepted: bool = False

    def __post_init__(self):
        if self.accepted is not True:
            raise WriteProofError('Grid writing requires native acceptance')
        if type(self.manual_bpm_accepted) is not bool:
            raise WriteProofError('Invalid manual BPM acceptance')
        CueWriteAuthority(self.database, self.library_id, self.schema_sha256,
                          'validate-library-authority', self.application_version)

    def bind(self, track_id: str, track_uuid: str) -> GridWriteAuthority:
        return GridWriteAuthority(self.database, self.library_id, self.schema_sha256,
                                  track_id, self.application_version, track_uuid,
                                  self.manual_bpm_accepted)


def load_grid_authority(path: Path, source: Path, library_id: str) -> GridLibraryAuthority | None:
    try:
        _regular(path)
        authority = GridLibraryAuthority(**json.loads(path.read_bytes()))
        if authority.database != str(source) or authority.library_id != library_id:
            return None
        return authority
    except (OSError, ValueError, KeyError, TypeError, WriteProofError):
        return None


class _GridLease(PilotCueStore):
    """Share existing guarded SQL/backup ownership, without cue operations."""
    def __init__(self, authority, root, grid):
        self.grid = grid
        self._grid_source_id = None
        super().__init__(authority, root)

    def _guard(self):
        super()._guard()
        info = self.database.stat()
        current_id = (info.st_dev, info.st_ino)
        if current_id != self._grid_source_id:
            self._last_counter = None
            self._backup_proof = None
            self._backup_sha = ''
            self._grid_source_id = current_id

    def _guard_other_recovery(self):
        # This domain retires its payloads under the shared writer lock.
        pass

    @contextmanager
    def _lock(self):
        with super()._lock():
            self.grid._cleanup_completed()
            yield

    def _record(self):
        return self.grid._record()


class PilotGridStore:
    def __init__(self, authority: GridWriteAuthority, recovery_root: Path):
        self.authority = authority
        self.root = Path(recovery_root).absolute()
        self.payloads = self.root / 'grid-transaction'
        self.journal = self.root / 'transaction.json'
        self.completed = self.root / 'grid-completed.json'
        self.database = Path(authority.database)
        self.lease = _GridLease(authority, self.root, self)

    def _paths(self, track):
        uid = self.authority.track_uuid
        relative = f'/PIONEER/USBANLZ/{uid[:3]}/{uid[3:]}/ANLZ0000.DAT'
        if (track.get('ID') != self.authority.pilot_track_id or track.get('UUID') != uid
                or track.get('AnalysisDataPath') != relative):
            raise ConflictError('Grid track/analysis identity changed')
        dat = self.database.parent / 'share' / relative.lstrip('/')
        return {'DAT': dat, 'EXT': dat.with_suffix('.EXT')}

    def _file(self, path):
        _regular(path)
        if (path != path.resolve() or path.stat().st_uid != os.getuid()
                or path.stat().st_dev != self.root.stat().st_dev):
            raise WriteProofError('Analysis/recovery path redirects or crosses filesystems')
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ConflictError('Analysis changed during capture')
        return data

    def _snapshot(self, conn):
        rows = self.lease._rows(conn, 'djmdContent', ' WHERE ID=?',
                                (self.authority.pilot_track_id,))
        if len(rows) != 1 or rows[0]['rb_local_deleted']:
            raise ConflictError('Grid track is missing or deleted')
        track = rows[0]
        if (any(track.get(k, 0) != 0 for k in
                ('rb_data_status', 'rb_local_data_status'))
                or track.get('rb_local_synced', 0) not in (0, 1)
                or track.get('usn') is not None):
            raise WriteProofError('Unverified grid synchronization metadata')
        paths = self._paths(track)
        file = None
        if self.authority.manual_bpm_accepted:
            rows = self.lease._rows(conn, 'contentFile', ' WHERE ContentID=? AND Path=?',
                                    (track['ID'], track['AnalysisDataPath']))
            if len(rows) == 1 and rows[0].get('rb_local_path') == str(paths['DAT']):
                file = rows[0]
        return GridSnapshot(track, self._file(paths['DAT']), self._file(paths['EXT']), file)

    def _counter(self, conn):
        rows = self.lease._rows(conn, 'agentRegistry', " WHERE registry_id='localUpdateCount'")
        if len(rows) != 1:
            raise WriteProofError('Ambiguous grid update counter')
        return rows[0]

    def snapshot(self):
        with self.lease._lock():
            conn = self.lease._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN')
                return self._snapshot(conn)
            finally:
                conn.close()

    def recover(self):
        with self.lease._lock():
            conn = self.lease._connect()
            try:
                return self._recover(conn)
            finally:
                conn.close()

    def _authorizer(self, action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE):
            allowed = {'djmdContent': ('BPM', 'TrackInfoUpdated', 'rb_local_usn', 'updated_at'),
                       'agentRegistry': ('int_1',)}
            if self.authority.manual_bpm_accepted:
                allowed['djmdContent'] += ('AnalysisUpdated',)
                allowed['contentFile'] = ('Hash', 'Size', 'rb_local_usn', 'updated_at')
            return (sqlite3.SQLITE_OK if action == sqlite3.SQLITE_UPDATE and not trigger
                    and database == 'main' and column in allowed.get(table, ())
                    else sqlite3.SQLITE_DENY)
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _apply_rows(conn, changes):
        for d in changes:
            names = [k for k in d.after if d.before[k] != d.after[k]]
            assignments = ','.join(_quote(k) + '=?' for k in names)
            pk = 'registry_id' if d.table == 'agentRegistry' else 'ID'
            conn.execute(f'UPDATE {_quote(d.table)} SET {assignments} WHERE {_quote(pk)}=?',
                         (*[d.after[k] for k in names], d.key))

    def _copy_for_publish(self, role, raw, record):
        path = self.payloads / ('publish.' + role)
        if path.exists() or path.is_symlink():
            _regular(path)
            if _sha(path.read_bytes()) not in (record['files'][role][s] for s in ('before', 'after')):
                raise RecoveryRequired('Unknown grid publication staging bytes')
            path.unlink()
        atomic_bytes(path, raw)
        mode = record['files'][role]['mode']
        mtime = record['files'][role]['mtime_ns']
        if type(mode) is not int or not 0 <= mode <= 0o777 or type(mtime) is not int:
            raise RecoveryRequired('Invalid grid metadata in recovery')
        os.chmod(path, mode)
        os.utime(path, ns=(mtime, mtime))
        with path.open('rb') as handle:
            os.fsync(handle.fileno())
        return path

    def _publish(self, role, raw, record):
        self._exact_end(record)
        target = Path(record['files'][role]['path'])
        staged = self._copy_for_publish(role, raw, record)
        if self._file(target) == raw:
            staged.unlink()
            return
        self.lease._guard()
        # Caller validated the entire current set immediately before this loop.
        if _sha(self._file(target)) not in (record['files'][role][s] for s in ('before', 'after')):
            raise RecoveryRequired('Analysis changed before atomic grid publication')
        os.replace(staged, target)
        sync_dir(target.parent)
        sync_dir(self.payloads)
        if self._file(target) != raw:
            raise RecoveryRequired('Grid publication read-back failed')

    def apply(self, edit: GridEdit) -> bool:
        if edit.operation == 'bpm' and not self.authority.manual_bpm_accepted:
            raise WriteProofError('Manual BPM writing requires separate native acceptance')
        if edit.track_id != self.authority.pilot_track_id or edit.track_uuid != self.authority.track_uuid:
            raise WriteProofError('This grid authority is limited to the reviewed track')
        with self.lease._lock():
            conn = self.lease._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN IMMEDIATE')
                before = self._snapshot(conn)
                counter = self._counter(conn)
                now = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                now = now.replace('T', ' ').replace('+00:00', ' +00:00')
                endpoint = None
                try:
                    candidate = plan_grid_edit(before, counter, edit, timestamp=now)
                except AmbiguousGridEnd:
                    # Only the disputed terminal row needs a stronger, read-only
                    # source proof. Ordinary probes/saves never open audio.
                    endpoint = capture_pcm_endpoint(before.track['FolderPath'])
                    candidate = plan_grid_edit(before, counter, edit, timestamp=now,
                                               exact_end_ms=endpoint['end_ms'])
                if (candidate.dat, candidate.ext) == (before.dat, before.ext):
                    conn.rollback()
                    return False
                self.lease._backup(counter['int_1'])
                if self.payloads.exists() or self.payloads.is_symlink():
                    raise RecoveryRequired('Previous grid preparation is preserved')
                self.payloads.mkdir(mode=0o700)
                operation_id = str(uuid4())
                atomic_bytes(self.payloads / 'operation.json', _json({'operation_id': operation_id}))
                paths = self._paths(before.track)
                after_track = candidate.changes[0].after if candidate.changes else before.track
                after_counter = candidate.changes[1].after if candidate.changes else counter
                info = self.database.stat()
                record = {'format': _FORMAT, 'source': str(self.database),
                    'library_id': self.authority.library_id, 'track_id': edit.track_id,
                    'source_file_id': [info.st_dev, info.st_ino], 'operation_id': operation_id,
                    'baseline_sha256': self.lease._backup_sha, 'request': asdict(edit),
                    'timestamp': now, 'decision': 'prepared',
                    'track_before': _encode_row(before.track), 'track_after': _encode_row(after_track),
                    'counter_before': _encode_row(counter), 'counter_after': _encode_row(after_counter),
                    'files': {role: {'path': str(p), 'mode': p.stat().st_mode & 0o777,
                                     'mtime_ns': p.stat().st_mtime_ns}
                              for role, p in paths.items()}}
                if before.content_file is not None:
                    file_delta = next((d for d in candidate.changes if d.table == 'contentFile'), None)
                    record['content_file_before'] = _encode_row(before.content_file)
                    record['content_file_after'] = _encode_row(
                        file_delta.after if file_delta else before.content_file)
                if endpoint is not None:
                    record['exact_pcm_end'] = endpoint
                for role, old, new in (('DAT', before.dat, candidate.dat), ('EXT', before.ext, candidate.ext)):
                    atomic_bytes(self.payloads / ('before.' + role), old)
                    atomic_bytes(self.payloads / ('after.' + role), new)
                    record['files'][role].update(before=_sha(old), after=_sha(new))
                sync_dir(self.root)
                atomic_bytes(self.journal, _json(record))
                _fault('journal-ready')
                self.lease._guard()
                self._exact_end(record)
                if self._snapshot(conn).revision != before.revision or self._counter(conn) != counter:
                    raise ConflictError('Grid changed before transaction publication')
                conn.set_authorizer(self._authorizer)
                self._apply_rows(conn, candidate.changes)
                if not self._matches_sql(conn, record, 'after'):
                    raise WriteProofError('Unexpected grid SQL side effect')
                for role, raw in (('DAT', candidate.dat), ('EXT', candidate.ext)):
                    self._publish(role, raw, record)
                    _fault('published-' + role)
                self.lease._guard()
                self._exact_end(record)
                _fault('before-commit')
                conn.commit()
                _fault('after-commit')
                atomic_bytes(self.journal, _json({**record, 'decision': 'committed'}))
                _fault('decision-durable')
            finally:
                conn.close()
            # Independent connection and complete byte read-back before success.
            conn = self.lease._connect()
            try:
                if self._recover(conn) != 'committed':
                    raise RecoveryRequired('Grid save did not commit')
            finally:
                conn.close()
            return True

    @staticmethod
    def _exact_end(record):
        if 'exact_pcm_end' not in record:
            return None
        try:
            proof = capture_pcm_endpoint(_decode_row(record['track_before'])['FolderPath'])
        except (OSError, WriteProofError) as exc:
            raise RecoveryRequired('Audio endpoint cannot be reverified; grid evidence preserved') from exc
        if proof != record['exact_pcm_end']:
            raise RecoveryRequired('Audio endpoint identity changed; grid evidence preserved')
        return proof['end_ms']

    def _record(self):
        try:
            _regular(self.journal)
            record = json.loads(self.journal.read_bytes())
            info = self.database.stat()
            if (record['format'] != _FORMAT or record['source'] != str(self.database)
                    or record['library_id'] != self.authority.library_id
                    or record['source_file_id'] != [info.st_dev, info.st_ino]
                    or record['track_id'] != self.authority.pilot_track_id
                    or record['decision'] not in ('prepared', 'committed')
                    or str(UUID(record['operation_id'])) != record['operation_id']
                    or set(record['files']) != {'DAT', 'EXT'}):
                raise ValueError('Mismatched grid journal identity')
            self.lease._verify_backup(record['baseline_sha256'])
            self._payload_guard()
            marker = json.loads((self.payloads / 'operation.json').read_bytes())
            if marker != {'operation_id': record['operation_id']}:
                raise ValueError('Mismatched grid payload generation')
            track = _decode_row(record['track_before'])
            paths = self._paths(track)
            for role in ('DAT', 'EXT'):
                if record['files'][role]['path'] != str(paths[role]):
                    raise ValueError('Redirected grid recovery destination')
                for side in ('before', 'after'):
                    p = self.payloads / (side + '.' + role)
                    _regular(p)
                    if _sha(p.read_bytes()) != record['files'][role][side]:
                        raise ValueError('Grid backup hash mismatch')
            snap = GridSnapshot(track, (self.payloads / 'before.DAT').read_bytes(),
                                 (self.payloads / 'before.EXT').read_bytes(),
                                 _decode_row(record['content_file_before'])
                                 if 'content_file_before' in record else None)
            candidate = plan_grid_edit(snap, _decode_row(record['counter_before']),
                                       GridEdit(**record['request']), timestamp=record['timestamp'],
                                       exact_end_ms=self._exact_end(record))
            after_track = candidate.changes[0].after if candidate.changes else track
            after_counter = (candidate.changes[1].after if candidate.changes
                             else _decode_row(record['counter_before']))
            if (_encode_row(after_track) != record['track_after']
                    or _encode_row(after_counter) != record['counter_after']
                    or _sha(candidate.dat) != record['files']['DAT']['after']
                    or _sha(candidate.ext) != record['files']['EXT']['after']):
                raise ValueError('Grid journal does not match its pure plan')
            file_delta = next((d for d in candidate.changes if d.table == 'contentFile'), None)
            if snap.content_file is not None and _encode_row(
                    file_delta.after if file_delta else snap.content_file) != record.get('content_file_after'):
                raise ValueError('Grid analysis-file metadata does not match its pure plan')
            return record
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RecoveryRequired('Grid recovery evidence is damaged; preserve it') from exc

    def _matches_sql(self, conn, record, side):
        if 'content_file_before' in record:
            rows = self.lease._rows(conn, 'contentFile', ' WHERE ID=?',
                                    (record['content_file_before']['ID'],))
            if len(rows) != 1 or _encode_row(rows[0]) != record['content_file_' + side]:
                return False
        rows = self.lease._rows(conn, 'djmdContent', ' WHERE ID=?', (record['track_id'],))
        return (len(rows) == 1 and _encode_row(rows[0]) == record['track_' + side]
                and _encode_row(self._counter(conn)) == record['counter_' + side])

    def _recover(self, conn):
        if not self.journal.exists():
            return None
        record = self._record()
        conn.execute('BEGIN IMMEDIATE')
        try:
            before, after = self._matches_sql(conn, record, 'before'), self._matches_sql(conn, record, 'after')
            if before and after:
                outcome = 'committed' if record['decision'] == 'committed' else 'rolled-back'
            elif after:
                outcome = 'committed'
            elif before and record['decision'] == 'prepared':
                outcome = 'rolled-back'
            else:
                raise RecoveryRequired('Unexpected grid database state; evidence preserved')
            current = {role: self._file(Path(meta['path'])) for role, meta in record['files'].items()}
            if any(_sha(raw) not in (record['files'][role][s] for s in ('before', 'after'))
                   for role, raw in current.items()):
                raise RecoveryRequired('Unexpected analysis bytes; recovery makes no file changes')
            side = 'after' if outcome == 'committed' else 'before'
            for role in ('DAT', 'EXT'):
                desired = (self.payloads / (side + '.' + role)).read_bytes()
                self._publish(role, desired, record)
                _fault('recovered-' + role)
            self.lease._guard()
            if (not self._matches_sql(conn, record, side)
                    or any(_sha(self._file(Path(m['path']))) != m[side] for m in record['files'].values())):
                raise RecoveryRequired('Grid recovery read-back failed')
            receipt = {**record, 'outcome': outcome,
                       'inactive_sqlite_journal': _inactive_journal_proof(self.database)}
            atomic_bytes(self.completed, _json(receipt))
            atomic_bytes(self.root / 'last-result.json', _json(receipt))
            conn.commit()
            self.journal.unlink()
            sync_dir(self.root)
            _fault('journal-retired')
            self._cleanup_completed()
            return outcome
        except BaseException:
            conn.rollback()
            raise

    def _payload_guard(self):
        _directory(self.payloads)
        for p in self.payloads.iterdir():
            if p.name not in _PAYLOADS:
                raise RecoveryRequired('Unknown grid recovery payload')
            _regular(p)

    def _cleanup_completed(self):
        if self.root != self.root.resolve():
            raise WriteProofError('Grid recovery root redirects')
        if (self.journal.exists() or self.journal.is_symlink()
                or not (self.payloads.exists() or self.payloads.is_symlink())):
            return
        self._payload_guard()
        try:
            _regular(self.completed)
            receipt = json.loads(self.completed.read_bytes())
            children = list(self.payloads.iterdir())
            marker_path = self.payloads / 'operation.json'
            marker = json.loads(marker_path.read_bytes()) if marker_path.exists() else None
            info = self.database.stat()
            if (receipt['format'] != _FORMAT or receipt['source'] != str(self.database)
                    or receipt['library_id'] != self.authority.library_id
                    or receipt['source_file_id'] != [info.st_dev, info.st_ino]
                    or receipt['track_id'] != self.authority.pilot_track_id
                    or (marker is not None and marker != {'operation_id': receipt['operation_id']})
                    or (marker is None and children)
                    or receipt['outcome'] not in ('committed', 'rolled-back')):
                raise ValueError('Unknown grid retirement')
            # Only disposal after durable verified completion, never source edits.
            for p in list(self.payloads.iterdir()):
                if p.name != 'operation.json':
                    p.unlink()
                    _fault('payload-retired')
            if marker is not None:
                marker_path.unlink()
                _fault('marker-retired')
            self.payloads.rmdir()
            sync_dir(self.root)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RecoveryRequired('Unfinished grid preparation is preserved') from exc
