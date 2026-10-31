"""Encrypted bare-registration transactions in original marked TEMP copies only.

Frozen capture inputs are copied, never SQL-opened. No source publication or
production authority exists. Full logical inventories here are offline proofs,
not a proposed per-import production performance policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from soundbox.rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect, _directory, _inactive_journal_proof,
    _integrity, _json, _regular,
)
from soundbox.state_io import atomic_bytes, sync_dir
from research.cdj_export.playlist_transaction import _copy_backup, _inventory, _sha
from . import registration

_PREFIX = 'soundbox-registration-'
_FORMAT = 'soundbox-offline-registration-v1'
_PAYLOADS = ('before.db', 'after.db')
_SUFFIXES = ('-wal', '-shm', '-journal')


def _checkpoint(_phase):
    """Subprocess crash hook for synthetic TEMP workspaces only."""


class OfflineRegistrationWorkspace:
    PREFIX = _PREFIX
    FORMAT = _FORMAT
    ADAPTER = registration

    def __init__(self, root):
        self.root = Path(root).absolute()
        if (self.root != self.root.resolve() or not self.root.name.startswith(self.PREFIX)
                or not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
            raise WriteProofError('Registration requires its original marked TEMP root')
        _directory(self.root)
        marker = self.root/'workspace.json'; _regular(marker)
        self.metadata = json.loads(marker.read_bytes())
        if (self.metadata.get('format') != self.FORMAT or self.metadata.get('root') != str(self.root)
                or not self.metadata.get('workspace_id')):
            raise WriteProofError('Invalid or relocated registration workspace')
        self.database = self.root/'working.db'
        self.transaction = self.root/'transaction'
        self.retired = self.root/'retired'
        self._guard()
        if (_sha(self.root/'baseline.db') != self.metadata['baseline_sha256']
                or _inventory(self.root/'baseline.db') != self.metadata['baseline']):
            raise RecoveryRequired('Registration baseline no longer verifies')

    @classmethod
    def capture(cls, directory):
        source = Path(directory).absolute()
        if source != source.resolve(strict=True) or source.is_relative_to('/Volumes'):
            raise WriteProofError('Explicit canonical frozen local capture required')
        for name in ('master.db', 'state.json', 'masterPlaylists6.xml'):
            _regular(source/name)
        if any((source/('master.db'+s)).exists() or (source/('master.db'+s)).is_symlink() for s in _SUFFIXES):
            raise WriteProofError('Frozen capture must be self-contained without SQL companions')
        before_hash = _sha(source/'master.db')
        expected = json.loads((source/'state.json').read_bytes())
        xml = (source/'masterPlaylists6.xml').read_bytes()
        root = Path(tempfile.mkdtemp(prefix=cls.PREFIX)).resolve(); root.chmod(0o700)
        try:
            shutil.copyfile(source/'master.db', root/'captured.db')
            _copy_backup(root/'captured.db', root/'baseline.db')
            (root/'captured.db').unlink()
            if _inventory(root/'baseline.db') != expected or _sha(source/'master.db') != before_hash:
                raise ConflictError('Frozen registration input changed or has a stale inventory')
            _copy_backup(root/'baseline.db', root/'working.db')
            atomic_bytes(root/'masterPlaylists6.xml', xml)
            info = root.stat()
            atomic_bytes(root/'workspace.json', _json(dict(format=cls.FORMAT, root=str(root),
                root_file_id=[info.st_dev, info.st_ino], workspace_id=str(uuid4()), source=str(source),
                baseline=expected, baseline_sha256=_sha(root/'baseline.db'),
                xml_sha256=_sha(root/'masterPlaylists6.xml'))))
            return cls(root)
        except BaseException as exc:
            # A capture admits no write. Retain unexpected files instead of a
            # recursive cleanup that could follow a redirect after a failed guard.
            try:
                for path in root.iterdir():
                    _regular(path); path.unlink()
                root.rmdir()
            except (OSError, WriteProofError) as error:
                exc.add_note(f'Incomplete registration capture retained at {root}: {error}')
            raise

    def _guard(self):
        info = self.root.stat()
        if (self.root != self.root.resolve() or info.st_uid != os.getuid()
                or [info.st_dev, info.st_ino] != self.metadata['root_file_id']
                or self.root.is_relative_to(Path(self.metadata['source']))):
            raise WriteProofError('Registration workspace identity changed or overlaps its input')
        _directory(self.root)
        _regular(self.root/'workspace.json')
        if json.loads((self.root/'workspace.json').read_bytes()) != self.metadata:
            raise RecoveryRequired('Registration marker changed')
        for name in ('working.db', 'baseline.db', 'masterPlaylists6.xml', 'writer.lock', 'last-result.json',
                     'audio-identity.json', 'planned-intent.json'):
            path = self.root/name
            if path.exists() or path.is_symlink():
                _regular(path)
        _regular(self.database)
        if _sha(self.root/'masterPlaylists6.xml') != self.metadata['xml_sha256']:
            raise RecoveryRequired('Registration must preserve playlist XML byte-for-byte')
        for suffix in _SUFFIXES:
            path = self.database.with_name(self.database.name+suffix)
            if path.exists() or path.is_symlink():
                _regular(path)
                if not (self.transaction/'journal.json').is_file():
                    raise RecoveryRequired('Unjournaled registration SQL companion; preserved')
        for directory in (self.transaction, self.retired):
            if directory.exists() or directory.is_symlink():
                _directory(directory)
                for path in directory.iterdir():
                    if path.name not in {*_PAYLOADS, 'journal.json', *(
                            name+suffix for name in _PAYLOADS for suffix in _SUFFIXES)}:
                        raise RecoveryRequired('Unknown registration recovery payload; preserved')
                    _regular(path)

    @contextmanager
    def _lock(self):
        self._guard()
        path = self.root/'writer.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            _regular(path)
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (path.stat().st_dev, path.stat().st_ino):
                raise WriteProofError('Registration lock identity changed')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriteProofError('Registration workspace is busy') from exc
            self._guard()
            yield
        finally:
            os.close(fd)

    def _discard_retired(self):
        self._guard()
        if self.retired.exists():
            for path in self.retired.iterdir():
                path.unlink()
            self.retired.rmdir(); sync_dir(self.root)

    def _retire(self):
        os.replace(self.transaction, self.retired); sync_dir(self.root)
        _checkpoint('retired')
        self._discard_retired()

    def _record(self):
        self._guard()
        try:
            path = self.transaction/'journal.json'; _regular(path)
            record = json.loads(path.read_bytes())
            if (record['format'] != self.FORMAT or record['workspace_id'] != self.metadata['workspace_id']
                    or record['database_file_id'] != [self.database.stat().st_dev, self.database.stat().st_ino]
                    or set(record['payload_sha256']) != set(_PAYLOADS)
                    or record['plan']['workspace_id'] != self.metadata['workspace_id']
                    or _json(record['plan']) != (self.root/'planned-intent.json').read_bytes()):
                raise ValueError('Registration recovery identity mismatch')
            for name in _PAYLOADS:
                if any((self.transaction/(name+s)).exists() or
                       (self.transaction/(name+s)).is_symlink() for s in _SUFFIXES):
                    raise ValueError('Recovery payload has an unexpected SQL companion')
                if _sha(self.transaction/name) != record['payload_sha256'][name]:
                    raise ValueError('Registration payload hash mismatch')
            before = _connect(self.transaction/'before.db', readonly=True)
            after = _connect(self.transaction/'after.db', readonly=True)
            try:
                _integrity(before); _integrity(after)
                if self.ADAPTER.verify(before, after, record['plan']['candidate']) != record['after']:
                    raise ValueError('Registration recovery delta mismatch')
            finally:
                after.close(); before.close()
            return record
        except (OSError, ValueError, KeyError, TypeError, WriteProofError) as exc:
            raise RecoveryRequired('Invalid registration recovery evidence; preserved') from exc

    def _recover(self):
        self._discard_retired()
        if not self.transaction.exists():
            return None
        if not (self.transaction/'journal.json').exists():
            # No working mutation can occur before this journal is durable.
            self._retire()
            return 'abandoned-preparation'
        record = self._record()
        # Only verified evidence permits SQLite to recover its own TEMP journal.
        conn = _connect(self.database)
        try:
            _integrity(conn)
            current = registration._state(conn)
            if current not in (record['after'], record['plan']['candidate']['baseline']):
                raise RecoveryRequired('Registration database is outside its exact recovery pair')
            # This original marked TEMP database can retain WAL mode after a
            # crash. A later read-only opener would otherwise recreate empty
            # unowned companions after journal retirement. Normalize only this
            # verified private working copy while its recovery journal remains.
            # Source pilots use their separate transport and never take this path.
            if conn.execute('PRAGMA journal_mode').fetchone()[0]=='wal':
                checkpoint=conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if checkpoint[0]!=0 or conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                    raise RecoveryRequired('Private registration WAL is still owned by another connection')
                _checkpoint('recovery-normalized')
        finally:
            conn.close()
        if current == record['after']:
            outcome = 'committed'
        elif current == record['plan']['candidate']['baseline']:
            outcome = 'rolled-back'
        else:
            raise RecoveryRequired('Registration database is outside its exact recovery pair')
        # SQLite can retain a zero-header journal after rollback without spills.
        # Remove only that independently verified inactive TEMP leftover, never
        # an unknown journal, so retirement cannot strand the next operation.
        inactive = _inactive_journal_proof(self.database)
        if inactive is not None:
            if _inactive_journal_proof(self.database) != inactive:
                raise RecoveryRequired('Inactive registration journal changed')
            self.database.with_name(self.database.name+'-journal').unlink()
            sync_dir(self.root)
        atomic_bytes(self.root/'last-result.json', _json({**record, 'outcome': outcome,
                                                       'retired_inactive_journal': inactive}))
        _checkpoint('receipt-ready')
        self._retire()
        return outcome

    def recover(self):
        with self._lock():
            return self._recover()

    def inspect_wave(self, path):
        with self._lock():
            if self.transaction.exists() or self.retired.exists():
                raise RecoveryRequired('Recover registration before capturing another source')
            audio = registration.inspect_wave(path, self.root/'audio-capture')
            atomic_bytes(self.root/'audio-identity.json', _json(asdict(audio)))
            return audio

    def plan(self, audio, *, identity, uuid, timestamp, day, operation_id=None):
        with self._lock():
            if self.transaction.exists() or self.retired.exists():
                raise RecoveryRequired('Recover registration before planning another operation')
            audio.revalidate()
            proof = self.root/'audio-identity.json'
            if not proof.is_file() or proof.read_bytes() != _json(asdict(audio)):
                raise ConflictError('Capture this exact source identity in the workspace before planning')
            conn = _connect(self.database, readonly=True)
            try:
                _integrity(conn)
                candidate = self.ADAPTER.plan(conn, audio, identity=identity, uuid=uuid, timestamp=timestamp, day=day)
            finally:
                conn.close()
            operation_id = operation_id or str(uuid4())
            registration._uuid(operation_id)
            result = dict(workspace_id=self.metadata['workspace_id'], operation_id=operation_id, candidate=candidate)
            atomic_bytes(self.root/'planned-intent.json', _json(result))
            return result

    def apply(self, plan):
        with self._lock():
            self._recover()
            if (set(plan) != {'workspace_id', 'operation_id', 'candidate'}
                    or plan['workspace_id'] != self.metadata['workspace_id']):
                raise ConflictError('Registration belongs to another workspace')
            registration._uuid(plan['operation_id'])
            receipt = self.root/'last-result.json'
            if receipt.exists():
                previous = json.loads(receipt.read_bytes())
                if previous.get('plan', {}).get('operation_id') == plan['operation_id']:
                    if previous['plan'] != json.loads(_json(plan)):
                        raise ConflictError('Registration operation identity was reused for another intent')
                    if previous['outcome'] == 'committed':
                        if _inventory(self.database) != previous['after']:
                            raise ConflictError('Committed registration receipt has a changed database')
                        return 'already-committed'
            issued = self.root/'planned-intent.json'
            if not issued.is_file() or issued.read_bytes() != _json(plan):
                raise ConflictError('Registration intent changed after durable planning')
            conn = _connect(self.database, readonly=True)
            try:
                audio = self.ADAPTER.validate_plan(conn, plan['candidate'])
            finally:
                conn.close()
            audio.revalidate()
            self.transaction.mkdir(mode=0o700)
            _copy_backup(self.database, self.transaction/'before.db')
            _copy_backup(self.database, self.transaction/'after.db')
            conn = _connect(self.transaction/'after.db')
            before = _connect(self.transaction/'before.db', readonly=True)
            try:
                conn.execute('BEGIN IMMEDIATE')
                self.ADAPTER._apply(conn, plan['candidate'])
                after = self.ADAPTER.verify(before, conn, plan['candidate'])
                conn.commit(); _integrity(conn)
            finally:
                before.close(); conn.close()
            record = dict(format=self.FORMAT, workspace_id=self.metadata['workspace_id'], plan=json.loads(_json(plan)),
                database_file_id=[self.database.stat().st_dev, self.database.stat().st_ino], after=after,
                payload_sha256={name: _sha(self.transaction/name) for name in _PAYLOADS})
            _checkpoint('prepared')
            atomic_bytes(self.transaction/'journal.json', _json(record))
            _checkpoint('journal-ready')
            self._record()
            conn = _connect(self.database)
            before = _connect(self.transaction/'before.db', readonly=True)
            try:
                conn.execute('BEGIN IMMEDIATE')
                audio.revalidate(); self._guard()
                self.ADAPTER._apply(conn, plan['candidate'])
                self.ADAPTER.verify(before, conn, plan['candidate'])
                _checkpoint('sql-applied')
                audio.revalidate(); self._guard()
                conn.commit(); _checkpoint('sql-committed')
            except BaseException:
                conn.rollback()
                raise
            finally:
                before.close(); conn.close()
            if self._recover() != 'committed':
                raise RecoveryRequired('Registration did not verify as committed')
            return 'committed'
