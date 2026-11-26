"""Accepted cue transactions; explicit per-library and per-track authority.

Use the SAME recovery root as LiveTagStore for a library. The shared lock and
journal serialize domains; an unresolved other-domain journal blocks admission.
Nothing here writes audio, analysis, XML, playlists or device storage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
from uuid import uuid4

from .rekordbox_cues import (CueEdit, CueIdentities, CueSnapshot, plan_cue_edit,
                              _public_cue, _check_sync, _CUE_COLUMNS)
from .rekordbox_tags import NATIVE_VERSION, _file_sha, _installed_version, _schema
from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, assert_rekordbox_closed, encrypted_backup,
    _cell, _identity, _inactive_journal_proof, _json, _quote, _regular,
)
from .state_io import atomic_bytes, sync_dir
from .native_transaction import NativeTransactionLease
from . import rekordbox_prep_read as prep_read

_FORMAT = 'soundbox-native-cues-v1'
_TABLES = frozenset(('djmdCue', 'djmdContent', 'contentCue', 'agentRegistry'))


@dataclass(frozen=True)
class CueLibraryAuthority:
    """Acceptance-bound library permission; every store still binds one track."""
    database: str
    library_id: str
    schema_sha256: str
    accepted: bool
    application_version: str = NATIVE_VERSION

    def __post_init__(self):
        if self.accepted is not True:
            raise WriteProofError('Cue writing requires native acceptance')
        CueWriteAuthority(self.database, self.library_id, self.schema_sha256,
                          'validate-library-authority', self.application_version)

    def bind(self, track_id: str) -> CueWriteAuthority:
        return CueWriteAuthority(self.database, self.library_id, self.schema_sha256,
                                  track_id, self.application_version)


def load_cue_authority(path: Path, source: Path, library_id: str) -> CueLibraryAuthority | None:
    try:
        _regular(path)
        authority = CueLibraryAuthority(**json.loads(path.read_bytes()))
        if authority.database != str(source) or authority.library_id != library_id:
            return None
        return authority
    except (OSError, ValueError, KeyError, TypeError, WriteProofError):
        return None


@dataclass(frozen=True)
class CueWriteAuthority:
    database: str
    library_id: str
    schema_sha256: str
    # Bound operation target; retain the established serialized field name.
    pilot_track_id: str
    application_version: str = NATIVE_VERSION

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in
                (self.database, self.library_id, self.pilot_track_id))
                or not Path(self.database).is_absolute()
                or len(self.schema_sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.schema_sha256)
                or self.application_version != NATIVE_VERSION):
            raise WriteProofError('Invalid pilot cue-write authority')


def _closed():
    assert_rekordbox_closed()


def _fault(_phase: str):
    """Synthetic tests only; never fault-inject a live library."""


def _encode_row(row):
    return {k: _cell(v) for k, v in row.items()} if row is not None else None


def _pk(table):
    return 'registry_id' if table == 'agentRegistry' else 'ID'


class PilotCueStore(NativeTransactionLease):
    """Single-track cue writer. Source selection cannot manufacture authority."""

    authority: CueWriteAuthority

    def _native_version(self):
        return _installed_version()

    def _assert_native_closed(self):
        _closed()

    def _snapshot(self, conn) -> CueSnapshot:
        return prep_read.cue_snapshot(self._rows, conn, self.authority.pilot_track_id)

    def snapshot(self) -> CueSnapshot:
        with self._lock():
            conn = self._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN')
                return self._snapshot(conn)
            finally:
                conn.close()

    def grid_snapshot(self):
        """Fresh read-only grid for cue presentation; grants no grid-write API."""
        with self._lock():
            conn = self._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN')
                track = self._snapshot(conn).track
                try:
                    return prep_read.grid_snapshot(self.database, self.root, track)
                except (OSError, ValueError, WriteProofError):
                    # Native cues are DB-only. Missing/unreadable ANLZ must not
                    # veto an independently authorized cue save or erase cache.
                    return None
            finally:
                conn.close()

    def _baseline_identity(self):
        return tuple((p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                      p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                     for p in (self.root / 'baseline.db', self.root / 'baseline.json'))

    def _verify_backup(self, sha):
        try:
            proof = self._baseline_identity()
            if proof == self._backup_proof and sha == self._backup_sha:
                return
            record = json.loads((self.root / 'baseline.json').read_bytes())
            if (record['source'] != str(self.database) or record['library_id'] != self.authority.library_id
                    or record['sha256'] != sha or _file_sha(self.root / 'baseline.db') != sha
                    or proof != self._baseline_identity()):
                raise RecoveryRequired('Cue recovery backup does not verify')
            self._backup_proof, self._backup_sha = proof, sha
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RecoveryRequired('Cue recovery backup is missing or damaged') from exc

    def _backup(self, counter):
        if counter == self._last_counter:
            try:
                self._verify_backup(self._backup_sha)
                return
            except RecoveryRequired:
                pass  # no pending journal: replace damaged backup with current verified source
        self._guard()
        standalone_digest: list[str] = []
        with encrypted_backup(self.database, standalone_digest=standalone_digest) as baseline:
            temp = self.root / 'baseline-new.db'
            if temp.exists() or temp.is_symlink():
                raise RecoveryRequired('An unfinished baseline publication is preserved')
            with baseline.open('rb') as src, temp.open('xb') as dst:
                os.chmod(temp, 0o600)
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            # Compare the transferred bytes to the capture's integrity-bracketed
            # digest. Reuse no later path observation or live source proof.
            sha = standalone_digest[0] if standalone_digest else _file_sha(baseline)
            if _file_sha(temp) != sha:
                raise WriteProofError('Encrypted cue backup did not verify')
            os.replace(temp, self.root / 'baseline.db')
            sync_dir(self.root)
            atomic_bytes(self.root / 'baseline.json', _json({
                'format': _FORMAT, 'source': str(self.database), 'library_id': self.authority.library_id,
                'sha256': sha, 'counter': counter}))
            self._last_counter = counter
            self._backup_proof, self._backup_sha = self._baseline_identity(), sha

    @staticmethod
    def _validate_delta(record, edit, delta):
        """Reject records describing mutations the admitted writer cannot make."""
        track, aggregate, cue, registry = (delta[t] for t in
            ('djmdContent', 'contentCue', 'djmdCue', 'agentRegistry'))
        before, after = track['before'], track['after']
        counter = registry['before']
        stamp = after['updated_at']
        old_count = before['CueUpdated']
        if old_count is not None and (type(old_count) is not str or not 1 <= len(old_count) <= 19
                or not old_count.isascii() or not old_count.isdecimal() or int(old_count) >= 2**63 - 1):
            raise RecoveryRequired('Cue recovery has an unproved content counter')
        if (type(counter['int_1']) is not int or not 0 <= counter['int_1'] < 2**63 - 2
                or _json(registry['after']) != _json({**counter, 'int_1': counter['int_1'] + 2})
                or type(stamp) is not str
                or _json(after) != _json({**before,
                    'CueUpdated': str(int(before['CueUpdated'] or '0') + 1),
                    'rb_local_usn': counter['int_1'] + 1, 'updated_at': stamp})):
            raise RecoveryRequired('Cue recovery changes unrelated content fields')
        parsed = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S.%f %z')
        old_items = json.loads(aggregate['before']['Cues']) if aggregate['before'] else []
        if not isinstance(old_items, list) or any(not isinstance(item, dict) for item in old_items):
            raise RecoveryRequired('Cue recovery aggregate is malformed')
        row = cue['after'] if edit.operation == 'add' else cue['before']
        if edit.operation == 'add':
            if cue['before'] is not None or cue['after'] is None:
                raise RecoveryRequired('Cue recovery action does not match request')
            empty = CueSnapshot(before, (), None)
            planned = plan_cue_edit(empty, counter, replace(edit, revision=empty.revision), now=parsed,
                identities=CueIdentities(row['ID'], row['UUID'], aggregate['after']['UUID']))
            if _json(row) != _json(_encode_row(planned[0].after)):
                raise RecoveryRequired('Cue recovery inserted row differs from native plan')
            items = [*old_items, _public_cue({k: row[k] for k in _CUE_COLUMNS})]
            if aggregate['before'] is None:
                expected = _encode_row(planned[2].after)
            else:
                expected = {**aggregate['before'], 'Cues': json.dumps(items, ensure_ascii=False,
                    separators=(',', ':'), allow_nan=False),
                    'rb_local_usn': counter['int_1'] + 2, 'updated_at': stamp}
        elif edit.operation == 'delete':
            if (cue['after'] is not None or row is None or edit.position_ms is not None
                    or (row['ID'], row['UUID']) != (edit.cue_id, edit.cue_uuid)
                    or edit.page not in ('hot', 'memory') or type(edit.slot) is not int
                    or not 0 <= edit.slot < 8
                    or row['Kind'] != (0 if edit.page == 'memory' else edit.slot + (1 if edit.slot < 3 else 2))):
                raise RecoveryRequired('Cue recovery deletion differs from request')
            _check_sync(row)
            items = [item for item in old_items if item.get('ID') != row['ID']]
            if len(items) != len(old_items) - 1 or aggregate['before'] is None:
                raise RecoveryRequired('Cue recovery deletion has no aggregate member')
            expected = {**aggregate['before'], 'Cues': json.dumps(items, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False),
                'rb_local_usn': counter['int_1'] + 2, 'updated_at': stamp}
        else:
            raise RecoveryRequired('Cue recovery action is unsupported')
        if _json(aggregate['after']) != _json(expected):
            raise RecoveryRequired('Cue recovery changes unrelated aggregate fields')

    def _record(self):
        try:
            record = json.loads(self.journal.read_bytes())
            info = self.database.stat()
            if (record['format'] != _FORMAT or record['source'] != str(self.database)
                    or record['library_id'] != self.authority.library_id
                    or record.get('source_file_id') != [info.st_dev, info.st_ino]
                    or record['track_id'] != self.authority.pilot_track_id
                    or not isinstance(record['changes'], list) or len(record['changes']) != 4
                    or {d['table'] for d in record['changes']} != _TABLES
                    or any(not d['key'] or 'before' not in d or 'after' not in d for d in record['changes'])):
                raise RecoveryRequired('Cue recovery identity/shape mismatch; evidence preserved')
            edit = CueEdit(**record['request'])
            delta = {d['table']: d for d in record['changes']}
            track = delta['djmdContent']
            aggregate = delta['contentCue']
            cue = delta['djmdCue']
            registry = delta['agentRegistry']
            if (edit.track_id != self.authority.pilot_track_id
                    or track['key'] != edit.track_id
                    or any(track[side]['ID'] != edit.track_id or track[side]['UUID'] != edit.track_uuid
                           for side in ('before', 'after'))
                    or aggregate['key'] != edit.track_uuid
                    or aggregate['after']['ContentID'] != edit.track_id
                    or aggregate['after']['ID'] != edit.track_uuid
                    or registry['key'] != 'localUpdateCount'
                    or registry['before']['registry_id'] != 'localUpdateCount'
                    or registry['after'] != {**registry['before'], 'int_1': registry['before']['int_1'] + 2}
                    or (cue['before'] is None) == (cue['after'] is None)
                    or any(row is not None and (row['ID'] != cue['key'] or row['ContentID'] != edit.track_id
                                               or row['ContentUUID'] != edit.track_uuid)
                           for row in (cue['before'], cue['after']))
                    or any(not isinstance(record[k], str) or len(record[k]) != 64
                           or any(c not in '0123456789abcdef' for c in record[k])
                           for k in ('before_revision', 'after_revision', 'baseline_sha256'))):
                raise RecoveryRequired('Cue recovery target/delta mismatch; evidence preserved')
            self._validate_delta(record, edit, delta)
            self._verify_backup(record['baseline_sha256'])
            return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError, WriteProofError) as exc:
            raise RecoveryRequired('Cue recovery record is damaged or belongs to another operation') from exc

    def _matches(self, conn, record, side):
        if self._snapshot(conn).revision != record[side + '_revision']:
            return False
        for d in record['changes']:
            rows = self._rows(conn, d['table'], f' WHERE {_quote(_pk(d["table"]))}=?', (d['key'],))
            if len(rows) > 1 or _encode_row(rows[0] if rows else None) != d[side]:
                return False
        return True

    def _recover(self, conn):
        if not self.journal.exists():
            return None
        record = self._record()
        conn.execute('BEGIN IMMEDIATE')
        try:
            self._guard()
            if self._matches(conn, record, 'after'):
                outcome = 'committed'
            elif self._matches(conn, record, 'before'):
                outcome = 'rolled-back'
            else:
                raise RecoveryRequired('Unexpected cue state during recovery; evidence preserved')
            # Let SQLite recover hot journals itself. A zero-header leftover
            # requires no rollback; retain its exact identity in the receipt so
            # either save domain can recognize it. Never delete it manually.
            proof = _inactive_journal_proof(self.database)
            atomic_bytes(self.root / 'last-result.json', _json(
                {**record, 'outcome': outcome, 'inactive_sqlite_journal': proof}))
            self.journal.unlink()
            sync_dir(self.root)
            conn.commit()
            return outcome
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _authorizer(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE):
            if trigger or database != 'main':
                return sqlite3.SQLITE_DENY
            if table == 'djmdCue' and action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK
            if table == 'contentCue' and action == sqlite3.SQLITE_INSERT:
                return sqlite3.SQLITE_OK
            allowed = {'djmdContent': ('CueUpdated', 'rb_local_usn', 'updated_at'),
                       'contentCue': ('Cues', 'rb_local_usn', 'updated_at'),
                       'agentRegistry': ('int_1',)}
            if action == sqlite3.SQLITE_UPDATE and column in allowed.get(table, ()):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _apply(conn, delta):
        for d in delta:
            table, pk = _quote(d.table), _quote(_pk(d.table))
            if d.after is None:
                conn.execute(f'DELETE FROM {table} WHERE {pk}=?', (d.key,))
            elif d.before is None:
                columns = ','.join(_quote(k) for k in d.after)
                values = ','.join('?' for _ in d.after)
                conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({values})', tuple(d.after.values()))
            else:
                names = [k for k in d.after if d.after[k] != d.before[k]]
                assignments = ','.join(_quote(k) + '=?' for k in names)
                conn.execute(f'UPDATE {table} SET {assignments} WHERE {pk}=?',
                             (*[d.after[k] for k in names], d.key))

    def apply(self, edit: CueEdit) -> bool:
        if edit.track_id != self.authority.pilot_track_id:
            raise WriteProofError('This cue authority is limited to the reviewed pilot track')
        with self._lock():
            conn = self._connect()
            try:
                self._recover(conn)
                conn.execute('BEGIN IMMEDIATE')
                self._guard()
                if (_identity(conn) != (self.authority.library_id, '6000')
                        or _schema(conn) != self.authority.schema_sha256):
                    raise ConflictError('The source identity/schema changed before the cue transaction')
                file_id = self.database.stat().st_dev, self.database.stat().st_ino
                before = self._snapshot(conn)
                counters = self._rows(conn, 'agentRegistry', " WHERE registry_id='localUpdateCount'")
                if len(counters) != 1:
                    raise WriteProofError('Ambiguous local update counter')
                identities = None
                if edit.operation == 'add':
                    for _ in range(32):
                        identities = CueIdentities(str(secrets.randbelow(2**32 - 1) + 1),
                                                   str(uuid4()), str(uuid4()) if before.aggregate is None else '')
                        if not conn.execute('SELECT 1 FROM djmdCue WHERE ID=? OR UUID=?',
                                            (identities.cue_id, identities.cue_uuid)).fetchone():
                            if not identities.aggregate_uuid or not conn.execute(
                                    'SELECT 1 FROM contentCue WHERE UUID=?', (identities.aggregate_uuid,)).fetchone():
                                break
                    else:
                        raise WriteProofError('Could not allocate unique cue identities')
                delta = plan_cue_edit(before, counters[0], edit, now=datetime.now(timezone.utc), identities=identities)
                if not delta:
                    conn.rollback()
                    return False
                # Derive the exact expected snapshot without executing any SQL.
                cue_rows = {r['ID']: r for r in before.cues}
                cue_change = delta[0]
                if cue_change.after is None:
                    del cue_rows[cue_change.key]
                else:
                    cue_rows[cue_change.key] = cue_change.after
                assert delta[1].after is not None and delta[2].after is not None
                after = CueSnapshot(delta[1].after, tuple(cue_rows.values()), delta[2].after)
                self._backup(counters[0]['int_1'])
                record = {'format': _FORMAT, 'source': str(self.database),
                          'library_id': self.authority.library_id, 'track_id': edit.track_id,
                          'source_file_id': list(file_id),
                          'operation_id': str(uuid4()), 'baseline_sha256': self._backup_sha,
                          'request': asdict(edit), 'before_revision': before.revision,
                          'after_revision': after.revision,
                          'changes': [{'table': d.table, 'key': d.key,
                                       'before': _encode_row(d.before), 'after': _encode_row(d.after)} for d in delta]}
                atomic_bytes(self.journal, _json(record))
                _fault('journal-ready')
                self._guard()
                conn.set_authorizer(self._authorizer)
                self._apply(conn, delta)
                if not self._matches(conn, record, 'after'):
                    raise WriteProofError('Cue changes differ from the intended exact rows')
                self._guard()
                if file_id != (self.database.stat().st_dev, self.database.stat().st_ino):
                    raise ConflictError('The source database file was replaced during the cue operation')
                _fault('before-commit')
                conn.commit()
                _fault('after-commit')
                conn.close()
                conn = None
                conn = self._connect()
                if self._recover(conn) != 'committed':
                    raise RecoveryRequired('Cue commit could not be independently verified')
                self._last_counter = counters[0]['int_1'] + 2
                return True
            except BaseException:
                if conn is not None:
                    conn.rollback()
                raise
            finally:
                if conn is not None:
                    conn.close()
