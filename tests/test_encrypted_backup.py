"""Self-contained encrypted captures retain integrity and WAL recovery semantics."""
from pathlib import Path
import hashlib
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import rekordbox_write as writer
from tests.test_rekordbox_write import _fixture


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EncryptedCaptureBackup(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve(); self.source = self.root / 'source' / 'master.db'
        self.source.parent.mkdir(); conn = _fixture(self.source); conn.close()
        self.original = self.source.read_bytes()

    def rows(self, path):
        conn = writer._connect(path, readonly=True)
        try:
            return conn.execute('SELECT Title FROM djmdContent WHERE ID=\'1\'').fetchall()
        finally:
            conn.close()

    def test_absent_and_zero_wal_adopt_exact_encrypted_bytes_without_reencrypting(self):
        for empty in (False, True):
            with self.subTest(empty_wal=empty):
                wal = self.source.with_name('master.db-wal')
                if empty:wal.write_bytes(b'')
                with (mock.patch.object(writer, '_backup', side_effect=AssertionError('Redundant re-encryption')),
                      mock.patch.object(writer, '_integrity', wraps=writer._integrity) as integrity,
                      mock.patch.object(writer, '_identity', wraps=writer._identity) as identity):
                    with writer.encrypted_backup(self.source) as path:
                        private = path.parent
                        self.assertEqual(path.name, 'baseline.db')
                        self.assertEqual(path.read_bytes(), self.original)
                        self.assertNotEqual(path.read_bytes()[:16], b'SQLite format 3\0')
                        self.assertEqual(path.stat().st_nlink, 1)
                        self.assertNotEqual(path.stat().st_ino, self.source.stat().st_ino)
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                        integrity.assert_called_once(); identity.assert_called_once()
                        # A differently named clone cannot depend on any
                        # captured.db or baseline.db WAL/SHM companion.
                        clone = self.root / 'independent'; clone.mkdir(exist_ok=True)
                        target = clone / ('copy-' + str(empty) + '.db'); shutil.copyfile(path, target)
                        self.assertEqual(self.rows(target), [('One',)])
                    self.assertFalse(private.exists())
                self.assertEqual(self.source.read_bytes(), self.original)
                if empty:self.assertEqual(wal.read_bytes(), b'')

    def test_wal_header_mode_without_wal_pages_remains_self_contained(self):
        conn = writer._connect(self.source)
        try:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        finally:
            conn.close()
        before = self.source.read_bytes()
        with mock.patch.object(writer, '_backup', side_effect=AssertionError('Empty WAL needs no folding')):
            with writer.encrypted_backup(self.source) as path:
                self.assertEqual(path.read_bytes(), before)
                clone = self.root / 'wal-main-alone.db'; shutil.copyfile(path, clone)
                self.assertEqual(self.rows(clone), [('One',)])
        self.assertEqual(self.source.read_bytes(), before)

    def test_committed_nonempty_wal_uses_original_folding_and_preserves_source_pair(self):
        conn = writer._connect(self.source)
        try:
            conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute("UPDATE djmdContent SET Title='Committed WAL' WHERE ID='1'")
            wal = self.source.with_name('master.db-wal'); before = self.source.read_bytes(), wal.read_bytes()
            with mock.patch.object(writer, '_backup', wraps=writer._backup) as fold:
                with writer.encrypted_backup(self.source) as path:
                    clone = self.root / 'folded-main-alone.db'; shutil.copyfile(path, clone)
                    self.assertEqual(self.rows(clone), [('Committed WAL',)])
                fold.assert_called_once()
            self.assertEqual((self.source.read_bytes(), wal.read_bytes()), before)
        finally:
            conn.close()

    def test_nonempty_header_only_wal_is_never_assumed_empty(self):
        conn = writer._connect(self.source)
        try:
            conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute("UPDATE djmdContent SET Title='Checkpointed' WHERE ID='1'")
            header = self.source.with_name('master.db-wal').read_bytes()[:32]
        finally:
            conn.close()
        wal = self.source.with_name('master.db-wal'); wal.write_bytes(header)
        before = self.source.read_bytes(), wal.read_bytes()
        with mock.patch.object(writer, '_backup', wraps=writer._backup) as fold:
            with writer.encrypted_backup(self.source) as path:
                self.assertEqual(self.rows(path), [('Checkpointed',)])
            fold.assert_called_once()
        self.assertEqual((self.source.read_bytes(), wal.read_bytes()), before)

    def test_unknown_private_journal_mode_falls_back_without_normalization(self):
        original_connect = writer._connect; observed = []
        def connect(path, **kwargs):
            conn = original_connect(path, **kwargs); observed.append(Path(path))
            if Path(path).name == 'captured.db' and len(observed) == 1:
                proxy = mock.Mock(wraps=conn)
                proxy.execute.side_effect = lambda sql, *args: (
                    SimpleNamespace(fetchone=lambda: ('unknown',)) if sql == 'PRAGMA journal_mode'
                    else conn.execute(sql, *args))
                return proxy
            return conn
        with (mock.patch.object(writer, '_connect', side_effect=connect),
              mock.patch.object(writer, '_backup', wraps=writer._backup) as fold):
            with writer.encrypted_backup(self.source) as path:
                self.assertEqual(self.rows(path), [('One',)])
            fold.assert_called_once()
        self.assertNotIn(self.source, observed)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_failed_integrity_or_identity_never_exposes_or_reencrypts_a_bad_copy(self):
        for name in ('_integrity', '_identity'):
            with self.subTest(name=name):
                with (mock.patch.object(writer, name, side_effect=writer.WriteProofError('bad verified copy')),
                      mock.patch.object(writer, '_backup', side_effect=AssertionError('Bad copy must fail closed'))):
                    with self.assertRaisesRegex(writer.WriteProofError, 'bad verified copy'):
                        with writer.encrypted_backup(self.source):
                            self.fail('Bad capture exposed')
                self.assertEqual(self.source.read_bytes(), self.original)

    def test_copy_bytes_changed_after_integrity_fail_even_if_stat_proof_is_held_constant(self):
        original_integrity = writer._integrity; original_identity = writer._snapshot_identity
        stable = {}
        def identity(path):
            if str(path) not in stable:stable[str(path)] = original_identity(path)
            return stable[str(path)]
        def mutate_private(conn):
            original_integrity(conn)
            private = Path(conn.execute('PRAGMA database_list').fetchone()[2])
            self.assertNotEqual(private, self.source)
            raw = private.read_bytes(); private.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with (mock.patch.object(writer, '_integrity', side_effect=mutate_private),
              mock.patch.object(writer, '_snapshot_identity', side_effect=identity)):
            with self.assertRaisesRegex(writer.WriteProofError, 'capture changed'):
                with writer.encrypted_backup(self.source):self.fail('Changed private bytes exposed')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_new_wal_payload_during_private_validation_is_never_discarded_as_empty(self):
        original_identity = writer._identity
        def introduce_wal(conn):
            value = original_identity(conn)
            private = Path(conn.execute('PRAGMA database_list').fetchone()[2])
            private.with_name(private.name + '-wal').write_bytes(b'unknown new WAL bytes')
            return value
        with mock.patch.object(writer, '_identity', side_effect=introduce_wal):
            with self.assertRaisesRegex(writer.WriteProofError, 'capture changed'):
                with writer.encrypted_backup(self.source):self.fail('New private WAL payload discarded')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_source_change_during_copy_retries_and_hot_journal_still_blocks_before_sql(self):
        original_copy = shutil.copy2; copies = 0
        def copy(source, destination, **kwargs):
            nonlocal copies
            result = original_copy(source, destination, **kwargs)
            if Path(source) == self.source:
                copies += 1
                if copies == 1:
                    conn = writer._connect(self.source)
                    try:conn.execute("UPDATE djmdContent SET Title='New stable source' WHERE ID='1'")
                    finally:conn.close()
            return result
        with mock.patch('soundbox.rekordbox_db.shutil.copy2', side_effect=copy):
            with writer.encrypted_backup(self.source) as path:
                self.assertEqual(self.rows(path), [('New stable source',)])
        self.assertEqual(copies, 2)
        journal = self.source.with_name('master.db-journal'); payload = b'pending recovery'; journal.write_bytes(payload)
        before = sha(self.source)
        with mock.patch.object(writer, '_connect', side_effect=AssertionError('Hot source never opens SQL')):
            with self.assertRaisesRegex(RuntimeError, 'needs recovery'):
                with writer.encrypted_backup(self.source):self.fail('Hot source exposed')
        self.assertEqual(sha(self.source), before); self.assertEqual(journal.read_bytes(), payload)

    def test_existing_baseline_and_fsync_failure_never_replace_unknown_files(self):
        captured = self.root / 'captured.db'; shutil.copyfile(self.source, captured)
        baseline = self.root / 'baseline.db'; baseline.write_bytes(b'preserve unknown occupant')
        with self.assertRaises(FileExistsError):writer._standalone_capture(captured, baseline)
        self.assertEqual(baseline.read_bytes(), b'preserve unknown occupant')
        self.assertEqual(captured.read_bytes(), self.original)
        with mock.patch.object(writer.os, 'fsync', side_effect=OSError('fsync failed')):
            with self.assertRaisesRegex(OSError, 'fsync failed'):
                with writer.encrypted_backup(self.source):self.fail('Undurable backup exposed')
        self.assertEqual(self.source.read_bytes(), self.original)
