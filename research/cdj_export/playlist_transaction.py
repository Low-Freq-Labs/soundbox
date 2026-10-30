"""Recoverable encrypted DB/XML publication inside marked TEMP copies only.

There is no live target, source-default discovery, authority bypass or API to
publish this workspace back to rekordbox. A durable journal precedes publication.
XML is durable before the SQL commit; that commit selects rollback vs completion.
Recovery validates the whole pair and all evidence before changing either file.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _backup, _connect,
    _directory, _integrity, _json, _regular, _rows, _state,
)
from soundbox.state_io import atomic_bytes, sync_dir
from .playlist_reference import load_capture
from .playlist_replay import _apply
from .playlist_xml import identities, validate_transition

_PREFIX = 'soundbox-playlist-txn-'
_FORMAT = 'soundbox-offline-playlist-transaction-v1'
_DB = 'working.db'
_XML = 'masterPlaylists6.xml'
_PAYLOADS = ('before.db', 'after.db', 'before.xml', 'after.xml')
_TABLES = ('djmdPlaylist', 'djmdSongPlaylist', 'agentRegistry')
_SQL_SUFFIXES = ('-wal', '-shm', '-journal')


def _checkpoint(_phase):
    """Synthetic subprocess fault hook; never used with a live target."""


def _sha(path):
    _regular(path)
    def identity(info):
        # Reading a newly created file can update atime. It is not part of the
        # byte/identity contract; replacement and content/ctime drift still fail.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    before = identity(path.stat())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != before:
            raise RecoveryRequired('Transaction evidence changed before hashing')
        result = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = identity(os.fstat(stream.fileno()))
    if identity(path.stat()) != before or after != before:
        raise RecoveryRequired('Transaction evidence changed during hashing')
    return result


def _inventory(path):
    conn = _connect(path, readonly=True)
    try:
        _integrity(conn)
        return _state(conn)
    finally:
        conn.close()


def _copy_backup(source, destination):
    """Fold WAL and normalize only this newly created disposable DB copy."""
    _backup(source, destination)
    conn = _connect(destination)
    try:
        if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise WriteProofError('Could not consolidate the private playlist backup')
    finally:conn.close()


def _projection(conn, changes):
    rows = {}
    for table in _TABLES:
        source = _rows(conn, table);key = 'registry_id' if table == 'agentRegistry' else 'ID'
        rows[table] = {r[key]:r for r in source}
        if len(rows[table]) != len(source):raise WriteProofError('Ambiguous playlist row identity')
    for change in changes:
        table, key = change['table'], change['id']
        if table not in _TABLES or (table == 'agentRegistry' and key != 'localUpdateCount'):
            raise WriteProofError('Unproven playlist transaction table/registry target')
        old = rows[table].get(key)
        if change['before'] == change['after']:
            raise WriteProofError('Unchanged row cannot authorize a playlist mutation')
        if old != change['before'] or (old is None and key in rows[table]):
            raise ConflictError('Playlist plan has a stale row baseline')
        if change['after'] is None:
            if old is None:
                raise WriteProofError('Cannot delete an absent playlist row')
            del rows[table][key]
        else:
            rows[table][key] = change['after']
    return rows


def _verify_projection(conn, before, projected):
    current = _state(conn)
    if current['schema'] != before['schema'] or current['metadata'] != before['metadata']:
        raise WriteProofError('Playlist mutation changed schema or SQLite metadata')
    for table in before['tables'].keys() | current['tables'].keys():
        if table not in _TABLES and current['tables'].get(table) != before['tables'].get(table):
            raise WriteProofError('Playlist mutation changed an unrelated table')
    for table in _TABLES:
        key = 'registry_id' if table == 'agentRegistry' else 'ID'
        rows = _rows(conn, table)
        if len(rows) != len(projected[table]) or {r[key]:r for r in rows} != projected[table]:
            raise WriteProofError('Playlist mutation changed an unplanned row')
    return current


class OfflinePlaylistWorkspace:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if (self.root != self.root.resolve() or not self.root.name.startswith(_PREFIX)
                or not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
            raise WriteProofError('Playlist publication requires its original marked TEMP root')
        _directory(self.root)
        marker = self.root/'workspace.json';_regular(marker)
        self.metadata = json.loads(marker.read_bytes())
        if (self.metadata.get('format') != _FORMAT or self.metadata.get('root') != str(self.root)
                or not self.metadata.get('workspace_id')):
            raise WriteProofError('Invalid or relocated playlist workspace')
        self.database = self.root/_DB;self.xml = self.root/_XML
        self.transaction = self.root/'transaction';self.retired = self.root/'retired'
        self._guard()
        if (_sha(self.root/'baseline.db') != self.metadata['baseline_sha256']
                or _sha(self.root/'baseline.xml') != self.metadata['baseline_xml_sha256']
                or _inventory(self.root/'baseline.db') != self.metadata['baseline']):
            raise RecoveryRequired('Playlist workspace baseline no longer verifies')

    @classmethod
    def capture(cls, directory):
        source = Path(directory).expanduser().resolve(strict=True)
        state, _ = load_capture(source)
        xml = source/'masterPlaylists6.xml';_regular(xml)
        payload = xml.read_bytes();identities(payload)
        root = Path(tempfile.mkdtemp(prefix=_PREFIX)).resolve();root.chmod(0o700)
        # Frozen inputs are never SQL-opened outside TEMP.
        raw = root/'captured.db';shutil.copyfile(source/'master.db', raw)
        try:_copy_backup(raw, root/'baseline.db')
        finally:raw.unlink()
        if _inventory(root/'baseline.db') != state:
            raise ConflictError('Frozen capture changed before workspace creation')
        _copy_backup(root/'baseline.db', root/_DB)
        atomic_bytes(root/_XML, payload);atomic_bytes(root/'baseline.xml', payload)
        atomic_bytes(root/'workspace.json', _json(dict(format=_FORMAT, root=str(root),
            workspace_id=str(uuid4()), source=str(source), baseline=state,
            baseline_sha256=_sha(root/'baseline.db'), baseline_xml_sha256=_sha(root/'baseline.xml'))))
        return cls(root)

    def _guard(self):
        if (self.root != self.root.resolve() or self.root.stat().st_mode & 0o077
                or self.root.stat().st_uid != os.getuid()
                or self.root.is_relative_to(Path(self.metadata['source']))):
            raise WriteProofError('Playlist workspace changed or overlaps its source')
        _directory(self.root)
        _regular(self.root/'workspace.json')
        if json.loads((self.root/'workspace.json').read_bytes()) != self.metadata:
            raise RecoveryRequired('Playlist workspace identity changed')
        for name in (_DB, _XML, 'baseline.db', 'baseline.xml', 'workspace.json',
                     'writer.lock', 'last-result.json'):
            path = self.root/name
            if path.exists() or path.is_symlink():_regular(path)
        for suffix in _SQL_SUFFIXES:
            path = self.database.with_name(_DB+suffix)
            if path.exists() or path.is_symlink():_regular(path)
        for area in (self.transaction, self.retired):
            if area.exists() or area.is_symlink():
                _directory(area)
                for path in area.iterdir():
                    allowed = {*_PAYLOADS, 'journal.json'} | {
                        name+suffix for name in ('before.db','after.db') for suffix in _SQL_SUFFIXES}
                    if path.name not in allowed:
                        raise RecoveryRequired('Unknown playlist recovery payload; preserved')
                    _regular(path)
        _regular(self.database);_regular(self.xml)

    @contextmanager
    def _lock(self):
        self._guard()
        fd = os.open(self.root/'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            _regular(self.root/'writer.lock')
            try:fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:raise WriteProofError('Playlist workspace is busy') from exc
            self._guard();yield
        finally:
            os.close(fd)

    def _discard_retired(self):
        self._guard()
        if self.retired.exists():
            for path in self.retired.iterdir():path.unlink()
            self.retired.rmdir();sync_dir(self.root)

    def _retire(self):
        os.replace(self.transaction, self.retired);sync_dir(self.root)
        _checkpoint('retired')
        self._discard_retired()

    def _record(self):
        self._guard()
        try:
            record = json.loads((self.transaction/'journal.json').read_bytes())
            if (record.get('format') != _FORMAT
                    or record.get('workspace_id') != self.metadata['workspace_id']
                    or record.get('database_file_id') != [self.database.stat().st_dev, self.database.stat().st_ino]
                    or set(record['payload_sha256']) != set(_PAYLOADS)
                    or not record['changes']):
                raise ValueError('Playlist recovery identity/shape mismatch')
            for name in _PAYLOADS:
                if _sha(self.transaction/name) != record['payload_sha256'][name]:
                    raise ValueError('Playlist recovery payload hash mismatch')
            for name in ('before.db','after.db'):
                if any((self.transaction/(name+s)).exists() for s in _SQL_SUFFIXES):
                    raise ValueError('Unexpected recovery-payload SQL companion')
            for which in ('before', 'after'):
                if _inventory(self.transaction/(which+'.db')) != record[which]:
                    raise ValueError('Playlist recovery inventory mismatch')
                identities((self.transaction/(which+'.xml')).read_bytes())
            validate_transition((self.transaction/'before.xml').read_bytes(),
                (self.transaction/'after.xml').read_bytes(), record['changes'])
            # Re-prove the intended complete logical delta, including unknown
            # tables, before any SQLite hot-journal or XML recovery is admitted.
            conn = _connect(self.transaction/'before.db', readonly=True)
            try:projected = _projection(conn, record['changes'])
            finally:conn.close()
            conn = _connect(self.transaction/'after.db', readonly=True)
            try:_verify_projection(conn, record['before'], projected)
            finally:conn.close()
            return record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RecoveryRequired('Invalid playlist recovery evidence; preserved') from exc

    def _recover(self):
        self._discard_retired()
        if not self.transaction.exists():return None
        if not (self.transaction/'journal.json').exists():
            # No canonical write is possible before the durable journal.
            self._retire();return 'abandoned-preparation'
        record = self._record()
        xml_sha = _sha(self.xml)
        before_xml = record['payload_sha256']['before.xml'];after_xml = record['payload_sha256']['after.xml']
        if xml_sha not in (before_xml, after_xml):
            raise RecoveryRequired('Playlist XML is outside the exact recovery pair')
        # A valid record authorizes SQLite to recover its own TEMP hot journal.
        conn = _connect(self.database)
        try:current = _state(conn)
        finally:conn.close()
        if current == record['after']:
            if xml_sha != after_xml:
                raise RecoveryRequired('Committed DB has an impossible XML prefix; preserved')
            outcome = 'committed'
        elif current == record['before']:
            if xml_sha != before_xml:
                atomic_bytes(self.xml, (self.transaction/'before.xml').read_bytes())
            outcome = 'rolled-back'
        else:
            raise RecoveryRequired('Playlist DB is outside the exact recovery pair')
        atomic_bytes(self.root/'last-result.json', _json({**record, 'outcome':outcome}))
        _checkpoint('receipt-ready')
        self._retire()
        return outcome

    def recover(self):
        with self._lock():return self._recover()

    def apply(self, changes, xml_after, *, expected_before, expected_xml_sha256):
        with self._lock():
            self._recover()
            if not changes:raise WriteProofError('An empty playlist transaction is not admitted')
            identities(xml_after)
            conn = _connect(self.database, readonly=True)
            try:
                before = _state(conn)
                if before != expected_before:raise ConflictError('Stale complete playlist baseline')
                projected = _projection(conn, changes)
            finally:conn.close()
            xml_before = self.xml.read_bytes();identities(xml_before)
            if hashlib.sha256(xml_before).hexdigest() != expected_xml_sha256:
                raise ConflictError('Stale complete playlist XML baseline')
            validate_transition(xml_before, xml_after, changes)
            self.transaction.mkdir(mode=0o700)
            _copy_backup(self.database, self.transaction/'before.db')
            _copy_backup(self.database, self.transaction/'after.db')
            atomic_bytes(self.transaction/'before.xml', xml_before)
            atomic_bytes(self.transaction/'after.xml', xml_after)
            conn = _connect(self.transaction/'after.db')
            try:
                conn.execute('BEGIN IMMEDIATE');_apply(conn, changes)
                after = _verify_projection(conn, before, projected)
                if after == before:raise WriteProofError('No changed playlist database rows')
                conn.commit();_integrity(conn)
            finally:conn.close()
            if _inventory(self.transaction/'before.db') != before:
                raise ConflictError('Working database changed during preparation')
            record = dict(format=_FORMAT, workspace_id=self.metadata['workspace_id'],
                operation_id=str(uuid4()), database_file_id=[self.database.stat().st_dev, self.database.stat().st_ino],
                before=before, after=after, changes=changes,
                payload_sha256={name:_sha(self.transaction/name) for name in _PAYLOADS})
            _checkpoint('prepared')
            atomic_bytes(self.transaction/'journal.json', _json(record));_checkpoint('journal-ready')
            self._record()
            conn = _connect(self.database)
            try:
                conn.execute('BEGIN IMMEDIATE')
                if _state(conn) != before or _sha(self.xml) != record['payload_sha256']['before.xml']:
                    raise ConflictError('Working DB/XML baseline changed before publication')
                _apply(conn, changes);_verify_projection(conn, before, projected)
                _checkpoint('sql-applied')
                self._guard()
                if _sha(self.xml) != record['payload_sha256']['before.xml']:
                    raise ConflictError('Working XML changed before publication')
                atomic_bytes(self.xml, xml_after);_checkpoint('xml-published')
                self._guard()
                if _sha(self.xml) != record['payload_sha256']['after.xml']:
                    raise ConflictError('Working XML changed before SQL commit')
                conn.commit();_checkpoint('sql-committed')
            except BaseException:
                conn.rollback();raise
            finally:conn.close()
            if self._recover() != 'committed':
                raise RecoveryRequired('Playlist transaction did not verify as committed')
            return record['operation_id']
