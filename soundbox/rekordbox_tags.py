"""Narrow, identity-bound My Tag transactions, based on native reference edits.

No grid/cue/file-metadata writes. An explicit per-library authority is required;
selecting a source database does not grant it. OfflineWorkspace remains isolated.
Recovery compares exact affected rows and counter, never restores a whole DB.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
from typing import Iterator
from uuid import uuid4

from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, assert_rekordbox_closed, encrypted_backup,
    _LINK_COLUMNS, _digest, _guard_grid_recovery, _identity, _json,
    _owned_inactive_journal, _inactive_journal_proof, _quote, _regular,
)
from .state_io import atomic_bytes, sync_dir

NATIVE_VERSION = "6.8.7.0303"
_FORMAT = "soundbox-native-mytag-v1"


@dataclass(frozen=True)
class TagWriteAuthority:
    database: str
    library_id: str
    schema_sha256: str
    application_version: str = NATIVE_VERSION
    synced_removal: bool = False
    # A pilot authority cannot accidentally enable editing the full library.
    pilot_track_id: str = ""
    pilot_tag_id: str = ""

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in
                (self.database, self.library_id, self.schema_sha256, self.application_version))
                or not Path(self.database).is_absolute()
                or len(self.schema_sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.schema_sha256)
                or type(self.synced_removal) is not bool
                or type(self.pilot_track_id) is not str or type(self.pilot_tag_id) is not str
                or bool(self.pilot_track_id) != bool(self.pilot_tag_id)):
            raise WriteProofError('Invalid tag-write authority')


@dataclass(frozen=True)
class SelectedTagTrack:
    track_id: str
    source_uuid: str
    expected_present: bool


@dataclass(frozen=True)
class TagWriteRequest:
    library_id: str
    tracks: tuple[SelectedTagTrack, ...]
    tag_id: str
    tag_uuid: str
    present: bool
    tag_name: str = ""
    operation_id: str = ""


def _operation_id(value):
    return (type(value) is str and len(value) == 32
            and all(character in '0123456789abcdef' for character in value))


def _record_json(path):
    _regular(path)
    def epoch(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    before = epoch(path.stat());limit = 16 * 1024**2
    if before[2] > limit:raise RecoveryRequired('Tag recovery exceeds its supported bound')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        if epoch(os.fstat(stream.fileno())) != before:raise RecoveryRequired('Tag recovery changed before capture')
        raw = stream.read(limit + 1)
        if len(raw) > limit or epoch(os.fstat(stream.fileno())) != before or epoch(path.stat()) != before:
            raise RecoveryRequired('Tag recovery changed during capture')
    return json.loads(raw)


def _assert_closed() -> None:
    assert_rekordbox_closed()


def _installed_version() -> str:
    info = Path('/Applications/rekordbox 6/rekordbox.app/Contents/Info.plist')
    with info.open('rb') as handle:
        return str(plistlib.load(handle).get('CFBundleShortVersionString', ''))


def _schema(conn) -> str:
    sql = list(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
    metadata = {name: conn.execute(f'PRAGMA {name}').fetchone()[0]
                for name in ('user_version', 'application_id', 'encoding')}
    return _digest({'sql': sql, 'metadata': metadata})


def _file_sha(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def _fault(_phase: str) -> None:
    """Synthetic tests only; never fault-inject a live library."""


class LiveTagStore:
    def __init__(self, authority: TagWriteAuthority, recovery_root: Path):
        self.authority = authority
        self.database = Path(authority.database)
        self.root = Path(recovery_root).absolute()
        if self.root != self.root.resolve():
            raise WriteProofError('Tag recovery must not follow symlinks')
        if (self.root.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.root)):
            raise WriteProofError('Tag recovery overlaps the source library')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise WriteProofError('Tag recovery directory must be private')
        self.journal = self.root / 'transaction.json'
        self._backed_up = False
        self._last_counter: int | None = None
        self._baseline_proof = None
        self._baseline_sha = ""

    def _backup_identity(self):
        return tuple((p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                      p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                     for p in (self.root / 'baseline.db', self.root / 'baseline.json'))

    def _verify_backup(self, expected_sha: str) -> None:
        try:
            before = self._backup_identity()
            if before == self._baseline_proof and expected_sha == self._baseline_sha:
                return
            record = json.loads((self.root / 'baseline.json').read_bytes())
            if (record.get('source') != str(self.database)
                    or record.get('library_id') != self.authority.library_id
                    or record.get('sha256') != expected_sha
                    or _file_sha(self.root / 'baseline.db') != expected_sha
                    or before != self._backup_identity()):
                raise RecoveryRequired('Tag backup does not verify; evidence preserved')
            self._baseline_proof, self._baseline_sha = before, expected_sha
        except (OSError, ValueError, KeyError) as exc:
            raise RecoveryRequired('Tag backup is missing or damaged; evidence preserved') from exc

    def _validate_delta(self, record, request):
        changes = record['changes']
        registry = [d for d in changes if d['table'] == 'agentRegistry']
        links = [d for d in changes if d['table'] == 'djmdSongMyTag']
        targets = {t.track_id: t for t in request.tracks}
        if (type(request.present) is not bool or len(targets) != len(request.tracks)
                or any(type(t.expected_present) is not bool for t in request.tracks)
                or len(registry) != 1 or not links or len({d['key'] for d in links}) != len(links)):
            raise RecoveryRequired('Tag recovery action/count mismatch')
        counter = registry[0]
        before = counter['before']
        if (counter['key'] != 'localUpdateCount' or before['registry_id'] != 'localUpdateCount'
                or type(before['int_1']) is not int or not 0 <= before['int_1'] < 2**63 - len(links)
                or _json(counter['after']) != _json({**before, 'int_1': before['int_1'] + len(links)})):
            raise RecoveryRequired('Tag recovery counter differs from native plan')
        seen = set()
        for index, delta in enumerate(links, 1):
            old, row = delta['before'], delta['after']
            identity = row if row is not None else old
            if (not isinstance(identity, dict) or set(identity) != set(_LINK_COLUMNS)
                    or identity['ID'] != delta['key'] or identity['MyTagID'] != request.tag_id
                    or identity['ContentID'] not in targets or identity['ContentID'] in seen
                    or targets[identity['ContentID']].expected_present == request.present):
                raise RecoveryRequired('Tag recovery membership differs from request')
            seen.add(identity['ContentID'])
            next_count = before['int_1'] + index
            if request.present:
                if old is not None or row is None or not row['UUID']:
                    raise RecoveryRequired('Tag recovery insertion differs from request')
                expected = dict.fromkeys(_LINK_COLUMNS)
                expected.update(ID=row['ID'], UUID=row['UUID'], MyTagID=request.tag_id,
                    ContentID=row['ContentID'], rb_data_status=0, rb_local_data_status=0,
                    rb_local_deleted=0, rb_local_synced=0, usn=None, rb_local_usn=next_count,
                    created_at=row['created_at'], updated_at=row['created_at'])
                if _json(row) != _json(expected):
                    raise RecoveryRequired('Tag recovery insertion differs from native plan')
            else:
                if (old is None or set(old) != set(_LINK_COLUMNS) or old['TrackNo'] is not None
                        or old['rb_data_status'] != 0 or old['rb_local_data_status'] != 0
                        or old['rb_local_deleted'] or old['rb_local_synced'] not in (0, 1)
                        or old['usn'] is not None):
                    raise RecoveryRequired('Tag recovery removal has unproved native metadata')
                if old['rb_local_synced']:
                    if not self.authority.synced_removal or row is None or _json(row) != _json({**old,
                            'rb_data_status': 2, 'rb_local_deleted': 1, 'rb_local_usn': next_count,
                            'updated_at': row['updated_at']}):
                        raise RecoveryRequired('Tag recovery tombstone differs from native plan')
                elif row is not None:
                    raise RecoveryRequired('Tag recovery removal must delete local assignment')
        if ([d['after']['ContentID'] if d['after'] is not None else d['before']['ContentID'] for d in links]
                != [t.track_id for t in request.tracks if t.expected_present != request.present]):
            raise RecoveryRequired('Tag recovery omitted or reordered a requested membership change')

    def _record(self, path=None, *, pending=True, data=None) -> dict:
        try:
            selected = self.journal if path is None else path
            record = _record_json(selected) if data is None else data
            info = self.database.stat()
            if (not isinstance(record, dict)
                    or record.get('format') != _FORMAT or record.get('source') != str(self.database)
                    or record.get('library_id') != self.authority.library_id
                    or record.get('source_file_id') != [info.st_dev, info.st_ino]
                    or not isinstance(record.get('changes'), list) or not record['changes']
                    or any(c.get('table') not in ('djmdSongMyTag', 'agentRegistry')
                           or not c.get('key') or 'before' not in c or 'after' not in c
                           for c in record['changes'])):
                raise RecoveryRequired('Tag recovery identity/shape mismatch; evidence preserved')
            request = record['request']
            targets = tuple(SelectedTagTrack(**target) for target in request['tracks'])
            restored = TagWriteRequest(**{**request, 'tracks': targets})
            if (restored.library_id != self.authority.library_id or not targets
                    or not restored.tag_id or not restored.tag_uuid or not restored.tag_name
                    or any(not t.track_id or not t.source_uuid for t in targets)):
                raise RecoveryRequired('Tag recovery request mismatch; evidence preserved')
            if (type(restored.operation_id) is not str or restored.operation_id and (not _operation_id(restored.operation_id)
                    or record.get('operation_id') != restored.operation_id)):
                raise RecoveryRequired('Tag recovery operation identity changed')
            self._validate_delta(record, restored)
            if pending:self._verify_backup(record['baseline_sha256'])
            return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RecoveryRequired('Tag recovery record is damaged; evidence preserved') from exc

    def _guard(self):
        a = self.authority
        if (not self.database.is_absolute() or self.database != self.database.resolve()
                or self.database.name != 'master.db'
                or self.database.is_relative_to('/Volumes')):
            raise WriteProofError('Tag writes require the explicitly enabled local database')
        _regular(self.database)
        if self.database.stat().st_uid != os.getuid():
            raise WriteProofError('Source database is not owned by the current user')
        for suffix in ('-wal', '-shm', '-journal'):
            p = self.database.with_name(self.database.name + suffix)
            if p.exists() or p.is_symlink():
                _regular(p)
        for name in ('baseline.db', 'baseline.json', 'transaction.json', 'last-result.json'):
            p = self.root / name
            if p.exists() or p.is_symlink():
                _regular(p)
        if self.root.is_symlink() or self.root != self.root.resolve():
            raise WriteProofError('Recovery directory changed')
        _guard_grid_recovery(self.root, self.journal)
        if a.application_version != NATIVE_VERSION or _installed_version() != NATIVE_VERSION:
            raise WriteProofError('This rekordbox version has not passed tag-write acceptance')
        _assert_closed()
        if self.journal.exists():
            self._record()  # validate evidence before SQLite may recover a hot journal

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._guard()
        fd = os.open(self.root / 'writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _regular(self.root / 'writer.lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriteProofError('Another tag operation is finishing') from exc
            self._guard()
            yield
        finally:
            os.close(fd)

    def _connect(self):
        from .native_cipher import key_connection
        from sqlcipher3 import dbapi2 as sqlcipher
        self._guard()
        hot = self.database.with_name('master.db-journal')
        if (hot.exists() and not self.journal.exists()
                and not _owned_inactive_journal(self.database, self.root, self.authority.library_id)):
            raise RecoveryRequired('An unowned SQLite journal needs rekordbox recovery first')
        conn = sqlcipher.connect(f'{self.database.as_uri()}?mode=rw', uri=True,
                                 isolation_level=None, timeout=0.5)
        try:
            key_connection(conn)
            conn.execute('PRAGMA trusted_schema=OFF')
            conn.execute('PRAGMA synchronous=FULL')
            identity = _identity(conn)
            if identity != (self.authority.library_id, '6000'):
                raise ConflictError('The enabled rekordbox library identity changed')
            if _schema(conn) != self.authority.schema_sha256:
                raise ConflictError('The enabled rekordbox schema changed')
            return conn
        except BaseException:
            conn.close()
            raise

    def check_ready(self) -> None:
        """Called off the GUI thread before exposing writable checkboxes."""
        with self._lock():
            if self.journal.exists():
                conn = self._connect()
                try:
                    self._recover(conn)
                finally:
                    conn.close()
            elif (self.database.with_name('master.db-journal').exists()
                  and not _owned_inactive_journal(self.database, self.root, self.authority.library_id)):
                raise RecoveryRequired('Open rekordbox to recover its interrupted database first')

    def _backup(self, counter: int) -> None:
        if self._backed_up and counter == self._last_counter:
            try:
                self._verify_backup(self._baseline_sha)
                return
            except RecoveryRequired:
                # No transaction is pending here. A new verified snapshot of
                # the current source safely replaces an unavailable old backup.
                self._backed_up = False
        self._guard()
        with encrypted_backup(self.database) as source:
            temp = self.root / 'baseline-new.db'
            if temp.exists() or temp.is_symlink():
                raise RecoveryRequired('An unfinished baseline publication is preserved')
            with source.open('rb') as src, temp.open('xb') as dst:
                os.chmod(temp, 0o600)
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            sha = _file_sha(source)
            if _file_sha(temp) != sha:
                raise WriteProofError('The encrypted rekordbox backup did not verify')
            os.replace(temp, self.root / 'baseline.db')
            sync_dir(self.root)
            atomic_bytes(self.root / 'baseline.json', _json({
                'format': _FORMAT, 'source': str(self.database),
                'library_id': self.authority.library_id, 'sha256': sha,
                'counter': counter,
            }))
            self._backed_up = True
            self._last_counter = counter
            self._baseline_proof, self._baseline_sha = self._backup_identity(), sha

    @staticmethod
    def _row(conn, table: str, pk: str, key: str):
        cursor = conn.execute(f'SELECT * FROM {_quote(table)} WHERE {_quote(pk)}=?', (key,))
        names = [item[0] for item in cursor.description]
        row = cursor.fetchone()
        return dict(zip(names, row, strict=True)) if row is not None else None

    def _changes(self, conn, request: TagWriteRequest) -> list[dict]:
        if (request.library_id != self.authority.library_id or type(request.present) is not bool
                or not isinstance(request.operation_id, str)
                or request.operation_id and not _operation_id(request.operation_id)
                or type(request.tag_id) is not str or not request.tag_id
                or type(request.tag_uuid) is not str or not request.tag_uuid
                or type(request.tag_name) is not str or not request.tag_name
                or not request.tracks or len(request.tracks) > 16_384 or any(type(t.track_id) is not str or not t.track_id
                    or type(t.source_uuid) is not str or not t.source_uuid
                    or type(t.expected_present) is not bool for t in request.tracks)
                or len({t.track_id for t in request.tracks}) != len(request.tracks)):
            raise ConflictError('Tag request does not match the enabled library/selection')
        a = self.authority
        if a.pilot_track_id and (request.tag_id != a.pilot_tag_id
                                or any(t.track_id != a.pilot_track_id for t in request.tracks)):
            raise WriteProofError('This authority is limited to the reviewed pilot track and tag')
        tag = self._row(conn, 'djmdMyTag', 'ID', request.tag_id)
        if (not tag or tag['UUID'] != request.tag_uuid or tag['Name'] != request.tag_name or tag['Attribute'] != 0
                or tag['rb_local_deleted']):
            raise ConflictError('The selected tag changed or is no longer active')
        old_counter = self._row(conn, 'agentRegistry', 'registry_id', 'localUpdateCount')
        assert old_counter is not None
        counter = old_counter['int_1']
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00'
        changes = []
        for target in request.tracks:
            track = self._row(conn, 'djmdContent', 'ID', target.track_id)
            if not track or track['UUID'] != target.source_uuid or track['rb_local_deleted']:
                raise ConflictError('A selected track changed or is no longer active')
            ids = conn.execute(
                'SELECT ID FROM djmdSongMyTag WHERE MyTagID=? AND ContentID=? '
                'AND coalesce(rb_local_deleted,0)=0', (request.tag_id, target.track_id)).fetchall()
            if len(ids) > 1 or bool(ids) != target.expected_present:
                raise ConflictError('Tag membership changed; refresh the library before editing')
            if bool(ids) == request.present:
                continue
            counter += 1
            if request.present:
                row = dict.fromkeys(_LINK_COLUMNS)
                row.update(ID=str(uuid4()), UUID=str(uuid4()), MyTagID=request.tag_id,
                           ContentID=target.track_id, rb_data_status=0, rb_local_data_status=0,
                           rb_local_deleted=0, rb_local_synced=0, usn=None, rb_local_usn=counter,
                           created_at=now, updated_at=now)
                old = None
            else:
                old = self._row(conn, 'djmdSongMyTag', 'ID', ids[0][0])
                assert old is not None
                if old['TrackNo'] is not None:
                    raise WriteProofError('Tag assignment has unverified ordering metadata')
                if (old['rb_data_status'] != 0 or old['rb_local_data_status'] != 0
                        or old['rb_local_synced'] not in (0, 1) or old['usn'] is not None):
                    raise WriteProofError('Tag assignment has unverified synchronization metadata')
                if not old['rb_local_synced'] and old['usn'] is None:
                    row = None  # observed native deletion of an unsynced assignment
                else:
                    if not a.synced_removal:
                        raise WriteProofError('Historical tag removal has not passed native acceptance')
                    row = {**old, 'rb_data_status': 2, 'rb_local_deleted': 1,
                           'rb_local_usn': counter, 'updated_at': now}
            identity_row = row if row is not None else old
            assert identity_row is not None
            changes.append({'table': 'djmdSongMyTag', 'key': identity_row['ID'],
                            'before': old, 'after': row})
        if changes:
            # Native reference advances int_1 without touching updated_at.
            changes.append({'table': 'agentRegistry', 'key': 'localUpdateCount',
                            'before': old_counter, 'after': {**old_counter, 'int_1': counter}})
        return changes

    @staticmethod
    def _authorizer(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE):
            if trigger or database != 'main':
                return sqlite3.SQLITE_DENY
            if table == 'djmdSongMyTag':
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_UPDATE and table == 'agentRegistry' and column == 'int_1':
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @classmethod
    def _matches(cls, conn, changes, which: str) -> bool:
        return all(cls._row(conn, c['table'], 'ID' if c['table'] == 'djmdSongMyTag'
                            else 'registry_id', c['key']) == c[which] for c in changes)

    @staticmethod
    def _apply(conn, changes):
        for c in changes:
            table, old, row = c['table'], c['before'], c['after']
            if row is None:
                conn.execute('DELETE FROM djmdSongMyTag WHERE ID=?', (c['key'],))
            elif old is None:
                names = list(row)
                columns = ','.join(_quote(n) for n in names)
                values = ','.join('?' for _ in names)
                conn.execute(f'INSERT INTO {_quote(table)} ({columns}) VALUES ({values})', tuple(row.values()))
            else:
                names = [n for n in row if row[n] != old[n]]
                assignments = ','.join(f'{_quote(n)}=?' for n in names)
                pk = 'ID' if table == 'djmdSongMyTag' else 'registry_id'
                conn.execute(f'UPDATE {_quote(table)} SET {assignments} WHERE {_quote(pk)}=?',
                             (*[row[n] for n in names], c['key']))

    def _recover(self, conn) -> str | None:
        if not self.journal.exists():
            return None
        record = self._record()
        request = record['request']
        tag = self._row(conn, 'djmdMyTag', 'ID', request['tag_id'])
        if (not tag or tag['UUID'] != request['tag_uuid'] or tag['Name'] != request['tag_name']
                or tag['rb_local_deleted']):
            raise RecoveryRequired('Tag definition changed during recovery; evidence preserved')
        for target in request['tracks']:
            track = self._row(conn, 'djmdContent', 'ID', target['track_id'])
            if not track or track['UUID'] != target['source_uuid'] or track['rb_local_deleted']:
                raise RecoveryRequired('Track identity changed during recovery; evidence preserved')
        changes = record['changes']
        if self._matches(conn, changes, 'after'):
            outcome = 'committed'
        elif self._matches(conn, changes, 'before'):
            outcome = 'rolled-back'
        else:
            raise RecoveryRequired('Tag recovery found unexpected rows; evidence preserved')
        receipt={**record,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
        atomic_bytes(self.root / 'last-result.json', _json(receipt))
        if outcome == 'committed' and request.get('operation_id'):
            # One desktop assignment can be outstanding. Keep its own bounded
            # receipt while cue/grid/playlist work rotates the common receipt.
            atomic_bytes(self.root / 'tag-assignment-completed.json', _json(receipt))
        self.journal.unlink()
        sync_dir(self.root)
        return outcome

    def _replay(self, conn, request) -> int | None:
        if not request.operation_id:
            return None
        path = self.root / 'tag-assignment-completed.json'
        if not path.exists() and not path.is_symlink():
            return None
        metadata = _record_json(path)
        if (not isinstance(metadata, dict) or metadata.get('format') != _FORMAT
                or metadata.get('source') != str(self.database) or metadata.get('library_id') != self.authority.library_id
                or not _operation_id(metadata.get('operation_id'))):
            raise RecoveryRequired('Unrecognized completed My Tag assignment; preserve its evidence')
        if metadata['operation_id'] != request.operation_id:
            return None
        record = self._record(path, pending=False, data=metadata)
        if record.get('outcome') != 'committed' or _json(record['request']) != _json(asdict(request)):
            raise RecoveryRequired('The completed tag assignment differs from its saved intention')
        authority = self.authority
        if authority.pilot_track_id and (request.tag_id != authority.pilot_tag_id
                or any(target.track_id != authority.pilot_track_id for target in request.tracks)):
            raise WriteProofError('This authority is limited to the reviewed pilot track and tag')
        tag = self._row(conn, 'djmdMyTag', 'ID', request.tag_id)
        if (not tag or tag['UUID'] != request.tag_uuid or tag['Name'] != request.tag_name
                or tag['Attribute'] != 0 or tag['rb_local_deleted']):
            raise RecoveryRequired('The completed tag definition changed')
        for target in request.tracks:
            track = self._row(conn, 'djmdContent', 'ID', target.track_id)
            rows = conn.execute('SELECT ID FROM djmdSongMyTag WHERE MyTagID=? AND ContentID=? '
                                'AND coalesce(rb_local_deleted,0)=0', (request.tag_id, target.track_id)).fetchall()
            if (not track or track['UUID'] != target.source_uuid or track['rb_local_deleted']
                    or len(rows) > 1 or bool(rows) != request.present):
                raise RecoveryRequired('The completed tag assignment changed; preserve its intention')
        for change in record['changes']:
            if change['table'] == 'agentRegistry':
                current = self._row(conn, 'agentRegistry', 'registry_id', 'localUpdateCount')
                if (not current or type(current['int_1']) is not int
                        or current['int_1'] < change['after']['int_1']):
                    raise RecoveryRequired('The completed tag counter changed')
            elif self._row(conn, 'djmdSongMyTag', 'ID', change['key']) != change['after']:
                raise RecoveryRequired('The completed tag row changed; preserve its receipt')
        self._guard()
        return len(record['changes']) - 1

    def apply(self, request: TagWriteRequest) -> int:
        """Commit one atomic selected-track operation; return changed-track count."""
        with self._lock():
            conn = self._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN IMMEDIATE')
                self._guard()
                replay = self._replay(conn, request)
                if replay is not None:
                    conn.rollback()
                    return replay
                if (_identity(conn)[0] != self.authority.library_id
                        or _schema(conn) != self.authority.schema_sha256):
                    raise ConflictError('The source identity/schema changed before the transaction')
                file_id = (self.database.stat().st_dev, self.database.stat().st_ino)
                changes = self._changes(conn, request)
                if not changes:
                    conn.rollback()
                    return 0
                counter = changes[-1]['before']['int_1']
                self._backup(counter)
                record = {'format': _FORMAT, 'source': str(self.database),
                          'library_id': request.library_id, 'operation_id': request.operation_id or str(uuid4()),
                          'source_file_id': list(file_id),
                          'baseline_sha256': self._baseline_sha,
                          'request': asdict(request),
                          'changes': changes}
                payload=_json(record)
                if len(payload)>16*1024**2:raise WriteProofError('Tag transaction exceeds its supported record bound')
                atomic_bytes(self.journal, payload)
                _fault('journal-ready')
                self._guard()
                conn.set_authorizer(self._authorizer)
                self._apply(conn, changes)
                if not self._matches(conn, changes, 'after'):
                    raise WriteProofError('Tag changes did not match the exact intended rows')
                self._guard()
                if file_id != (self.database.stat().st_dev, self.database.stat().st_ino):
                    raise ConflictError('The source database file was replaced during the operation')
                _fault('before-commit')
                conn.commit()
                _fault('after-commit')
                conn.close()
                conn = None
                conn = self._connect()
                if self._recover(conn) != 'committed':
                    raise RecoveryRequired('Tag commit could not be independently verified')
                self._last_counter = changes[-1]['after']['int_1']
                return len(changes) - 1
            except BaseException:
                if conn is not None:
                    conn.rollback()
                raise
            finally:
                if conn is not None:
                    conn.close()


def load_authority(path: Path, source: Path, library_id: str) -> TagWriteAuthority | None:
    """A source picker/cache never manufactures write authority."""
    try:
        _regular(path)
        data = json.loads(path.read_bytes())
        authority = TagWriteAuthority(**data)
        if authority.database != str(source) or authority.library_id != library_id:
            return None
        return authority
    except (OSError, ValueError, TypeError, KeyError, WriteProofError):
        return None
