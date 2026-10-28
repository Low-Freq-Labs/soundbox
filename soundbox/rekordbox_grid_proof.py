"""Temporary-copy-only grid transaction proof. No publish-to-source API.

The deliberately expensive full SQL inventories and staged encrypted databases
are proof machinery, not the proposed per-click desktop save path. Working ANLZ
files live inside an original OfflineWorkspace; inputs are bytes, never paths to
source analysis/audio. A shared workspace journal blocks the offline tag writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from .rekordbox_grid import (
    GridEdit, GridSnapshot, clear_secondary_grid, plan_grid_edit, read_grid,
)
from .rekordbox_write import (
    ConflictError, OfflineWorkspace, RecoveryRequired, WriteProofError,
    _backup, _connect, _digest, _directory, _json, _quote, _regular, _rows, _state,
)
from .state_io import atomic_bytes, sync_dir

_FORMAT = 'soundbox-offline-grid-v1'
_FILES = frozenset(('config.json', 'working.DAT', 'working.EXT', 'before.DAT',
                    'before.EXT', 'after.DAT', 'after.EXT', 'last-result.json'))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fault(_phase: str):
    """Crash injection on disposable workspaces only."""


@dataclass(frozen=True)
class GridProofPlan:
    workspace_id: str
    revision: str
    operation: str
    value: int
    timestamp: str


class OfflineGridWorkspace:
    def __init__(self, root: Path):
        self.workspace = OfflineWorkspace(root)
        self.folder = self.workspace.root / 'grid'
        _directory(self.folder)
        _regular(self.folder / 'config.json')
        self.config = json.loads((self.folder / 'config.json').read_bytes())
        if (self.config.get('format') != _FORMAT
                or self.config.get('workspace_id') != self.workspace.metadata['workspace_id']
                or not self.config.get('track_id') or not self.config.get('track_uuid')):
            raise WriteProofError('Invalid offline grid workspace identity')
        self._guard()

    @classmethod
    def create(cls, source: Path, dat: bytes, ext: bytes, track_id: str):
        read_grid(dat)
        clear_secondary_grid(ext)
        w = OfflineWorkspace.capture(source)
        # A failed setup preserves its disposable capture for diagnosis.
        try:
            conn = _connect(w.database, readonly=True)
            try:
                row = conn.execute('SELECT UUID FROM djmdContent WHERE ID=?', (track_id,)).fetchone()
                if not row or not row[0]:
                    raise WriteProofError('Missing offline grid track')
            finally:
                conn.close()
            folder = w.root / 'grid'
            folder.mkdir(mode=0o700)
            atomic_bytes(folder / 'config.json', _json({'format': _FORMAT,
                'workspace_id': w.metadata['workspace_id'], 'track_id': track_id,
                'track_uuid': row[0]}))
            atomic_bytes(folder / 'working.DAT', dat)
            atomic_bytes(folder / 'working.EXT', ext)
            sync_dir(w.root)
            return cls(w.root)
        except BaseException:
            # This original temporary capture has admitted no edit/recovery.
            shutil.rmtree(w.root)
            raise

    def _guard(self):
        self.workspace._guard()
        _directory(self.folder)
        for p in self.folder.iterdir():
            if p.name not in _FILES:
                raise RecoveryRequired('Unknown offline grid recovery file')
            _regular(p)
        for name in ('config.json', 'working.DAT', 'working.EXT'):
            _regular(self.folder / name)
        if json.loads((self.folder / 'config.json').read_bytes()) != self.config:
            raise ConflictError('Offline grid binding changed')

    def _bytes(self):
        self._guard()
        return {suffix: (self.folder / ('working.' + suffix)).read_bytes()
                for suffix in ('DAT', 'EXT')}

    def _revision(self, state, files):
        return _digest({'state': state, 'files': {k: _sha(v) for k, v in files.items()}})

    def _pending(self):
        if self.workspace.transaction.exists() or self.workspace.retired.exists():
            raise RecoveryRequired('Recover the offline workspace before editing')

    @staticmethod
    def _row(conn, table, key, value):
        cur = conn.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?', (value,))
        names = [v[0] for v in cur.description]
        rows = cur.fetchall()
        if len(rows) != 1:
            raise WriteProofError('Missing or ambiguous grid metadata')
        return dict(zip(names, rows[0], strict=True))

    def _candidate(self, conn, files, operation, value, timestamp):
        track = self._row(conn, 'djmdContent', 'ID', self.config['track_id'])
        file = None
        if operation == 'bpm':
            rows = _rows(conn, 'contentFile')
            rows = [r for r in rows if r['ContentID'] == track['ID']
                    and r['Path'] == track['AnalysisDataPath']]
            if len(rows) != 1:
                raise WriteProofError('Missing or ambiguous primary analysis-file metadata')
            file = rows[0]
        snapshot = GridSnapshot(track, files['DAT'], files['EXT'], file)
        counter = (self._row(conn, 'agentRegistry', 'registry_id', 'localUpdateCount')
                   if operation in ('double', 'halve', 'bpm') else {})
        edit = GridEdit(self.config['track_id'], self.config['track_uuid'],
                        snapshot.revision, operation, value)
        candidate = plan_grid_edit(snapshot, counter, edit, timestamp=timestamp)
        return ({'DAT': candidate.dat, 'EXT': candidate.ext},
                [(d.table, d.key, d.before, d.after) for d in candidate.changes])

    def plan(self, operation: str, value: int = 0) -> GridProofPlan:
        with self.workspace._lock():
            self._guard()
            self._pending()
            files = self._bytes()
            conn = _connect(self.workspace.database, readonly=True)
            try:
                conn.execute('BEGIN')
                state = _state(conn)
                timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                timestamp = timestamp.replace('T', ' ').replace('+00:00', ' +00:00')
                self._candidate(conn, files, operation, value, timestamp)
                return GridProofPlan(self.config['workspace_id'], self._revision(state, files),
                                     operation, value, timestamp)
            finally:
                conn.close()

    @staticmethod
    def _apply_changes(conn, changes):
        for table, key, before, after in changes:
            names = [k for k in after if before[k] != after[k]]
            assignments = ','.join(_quote(k) + '=?' for k in names)
            pk = 'registry_id' if table == 'agentRegistry' else 'ID'
            conn.execute(f'UPDATE {_quote(table)} SET {assignments} WHERE {_quote(pk)}=?',
                         (*[after[k] for k in names], key))

    @staticmethod
    def _prove_changes(before_conn, after_conn, before, after, changes):
        if (before['schema'] != after['schema'] or before['metadata'] != after['metadata']
                or before['tables'].keys() != after['tables'].keys()):
            raise WriteProofError('Grid proof changed database schema/metadata')
        for table in before['tables']:
            if table not in ('djmdContent', 'agentRegistry', 'contentFile'):
                if before['tables'][table] != after['tables'][table]:
                    raise WriteProofError('Grid proof changed unrelated table: ' + table)
                continue
            pk = 'registry_id' if table == 'agentRegistry' else 'ID'
            expected = {row[pk]: row for row in _rows(before_conn, table)}
            for changed_table, key, old, row in changes:
                if changed_table == table:
                    if expected.get(key) != old:
                        raise ConflictError('Grid metadata baseline changed')
                    expected[key] = row
            if expected != {row[pk]: row for row in _rows(after_conn, table)}:
                raise WriteProofError('Grid proof changed unrelated rows/fields: ' + table)

    def apply(self, plan: GridProofPlan) -> bool:
        with self.workspace._lock():
            self._guard()
            self._pending()
            if plan.workspace_id != self.config['workspace_id']:
                raise ConflictError('Grid plan belongs to another workspace')
            conn = _connect(self.workspace.database)
            try:
                conn.execute('BEGIN IMMEDIATE')
                before = _state(conn)
                files = self._bytes()
                if self._revision(before, files) != plan.revision:
                    raise ConflictError('Offline grid/database changed after planning')
                after_files, changes = self._candidate(conn, files, plan.operation,
                                                       plan.value, plan.timestamp)
                if after_files == files:
                    conn.rollback()
                    return False
                t = self.workspace.transaction
                t.mkdir(mode=0o700)
                sync_dir(self.workspace.root)
                _backup(self.workspace.database, t / 'before.db')
                _backup(t / 'before.db', t / 'after.db')
                staged = _connect(t / 'after.db')
                before_conn = _connect(t / 'before.db', readonly=True)
                try:
                    staged.execute('BEGIN IMMEDIATE')
                    self._apply_changes(staged, changes)
                    after = _state(staged)
                    self._prove_changes(before_conn, staged, before, after, changes)
                    staged.commit()
                finally:
                    staged.close()
                    before_conn.close()
                for side, payloads in (('before', files), ('after', after_files)):
                    for suffix, raw in payloads.items():
                        atomic_bytes(self.folder / (side + '.' + suffix), raw)
                record = {'format': _FORMAT, 'workspace_id': self.config['workspace_id'],
                    'state_before': _digest(before), 'state_after': _digest(after),
                    'db_backups': {side: _sha((t / (side + '.db')).read_bytes())
                                   for side in ('before', 'after')},
                    'files': {side: {s: _sha(b) for s, b in payloads.items()}
                              for side, payloads in (('before', files), ('after', after_files))},
                    'decision': 'prepared'}
                atomic_bytes(t / 'journal.json', _json(record))
                _fault('journal-ready')
                self._apply_changes(conn, changes)
                if _digest(_state(conn)) != record['state_after']:
                    raise WriteProofError('Unexpected offline grid SQL side effect')
                for suffix in ('DAT', 'EXT'):
                    self._guard()
                    if self._bytes()[suffix] != files[suffix]:
                        raise ConflictError('Offline analysis changed before publication')
                    atomic_bytes(self.folder / ('working.' + suffix), after_files[suffix])
                    _fault('published-' + suffix)
                _fault('before-commit')
                conn.commit()
                _fault('after-commit')
                atomic_bytes(t / 'journal.json', _json({**record, 'decision': 'committed'}))
                _fault('decision-durable')
            finally:
                conn.close()  # rollback uncommitted SQL; preserve file journal
            outcome = self._recover_locked()
            if outcome != 'committed':
                raise RecoveryRequired('Offline grid result was not committed')
            return True

    def recover(self):
        with self.workspace._lock():
            return self._recover_locked()

    def _recover_locked(self):
        self._guard()
        w = self.workspace
        if w.retired.exists():
            if w.transaction.exists():
                raise RecoveryRequired('Two offline recovery generations exist')
            receipt = self._read_record(self.folder / 'last-result.json')
            self._verify_result(receipt, receipt.get('outcome'))
            w._remove_directory(w.retired)
            return receipt['outcome']
        if not w.transaction.exists():
            return None
        record = self._read_record(w.transaction / 'journal.json')
        for side in ('before', 'after'):
            p = w.transaction / (side + '.db')
            _regular(p)
            if (_sha(p.read_bytes()) != record['db_backups'][side]
                    or _digest(w._read_state(p)) != record['state_' + side]):
                raise RecoveryRequired('Offline grid database backup does not verify')
            for suffix in ('DAT', 'EXT'):
                p = self.folder / (side + '.' + suffix)
                _regular(p)
                if _sha(p.read_bytes()) != record['files'][side][suffix]:
                    raise RecoveryRequired('Offline grid analysis backup does not verify')
        conn = _connect(w.database)  # SQLite recovers its own hot journal
        try:
            conn.execute('BEGIN IMMEDIATE')
            state = _digest(_state(conn))
            if record['state_before'] == record['state_after'] == state:
                outcome = 'committed' if record['decision'] == 'committed' else 'rolled-back'
            elif state == record['state_after']:
                outcome = 'committed'
            elif state == record['state_before'] and record['decision'] == 'prepared':
                outcome = 'rolled-back'
            else:
                raise RecoveryRequired('Unexpected SQL state; offline grid evidence preserved')
            # Validate the entire set before restoring even one file.
            current = self._bytes()
            for suffix, raw in current.items():
                if _sha(raw) not in (record['files'][side][suffix] for side in ('before', 'after')):
                    raise RecoveryRequired('Unexpected analysis bytes; recovery stops without writes')
            side = 'after' if outcome == 'committed' else 'before'
            for suffix in ('DAT', 'EXT'):
                desired = (self.folder / (side + '.' + suffix)).read_bytes()
                if current[suffix] != desired:
                    self._guard()
                    if self._bytes()[suffix] != current[suffix]:
                        raise RecoveryRequired('Analysis changed during offline recovery')
                    atomic_bytes(self.folder / ('working.' + suffix), desired)
            self._verify_result(record, outcome, conn)
            atomic_bytes(self.folder / 'last-result.json', _json({**record, 'outcome': outcome}))
            conn.commit()
        finally:
            conn.close()
        w._retire()
        return outcome

    def _read_record(self, path):
        try:
            _regular(path)
            record = json.loads(path.read_bytes())
            if (record['format'] != _FORMAT
                    or record['workspace_id'] != self.config['workspace_id']
                    or record['decision'] not in ('prepared', 'committed')
                    or set(record['files']) != {'before', 'after'}
                    or set(record['db_backups']) != {'before', 'after'}):
                raise ValueError('Invalid grid recovery binding')
            digests = [record['state_before'], record['state_after'], *record['db_backups'].values()]
            for side in ('before', 'after'):
                if set(record['files'][side]) != {'DAT', 'EXT'}:
                    raise ValueError('Invalid grid recovery file set')
                digests.extend(record['files'][side].values())
            if any(type(d) is not str or len(d) != 64 or any(c not in '0123456789abcdef' for c in d)
                   for d in digests):
                raise ValueError('Invalid grid recovery digest')
            return record
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RecoveryRequired('Offline grid recovery record is missing or damaged') from exc

    def _verify_result(self, record, outcome, conn=None):
        if outcome not in ('committed', 'rolled-back'):
            raise RecoveryRequired('Invalid offline grid outcome')
        side = 'after' if outcome == 'committed' else 'before'
        state = _state(conn) if conn is not None else self.workspace._read_state(self.workspace.database)
        if (_digest(state) != record['state_' + side]
                or any(_sha(v) != record['files'][side][s] for s, v in self._bytes().items())):
            raise RecoveryRequired('Offline grid result failed independent verification')
