"""Multi-file recovery on encrypted disposable fixtures, including process exits."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_grid_proof as proof
from soundbox import rekordbox_write as offline
from soundbox.rekordbox_grid import Beat, read_grid
from tests.test_rekordbox_grid import container, primary, section
from tests.test_rekordbox_write import _fixture


def fixture(path):
    conn = _fixture(path)
    for name, kind in (('BPM', 'INTEGER'), ('Analysed', 'INTEGER'), ('FileType', 'INTEGER'),
                       ('AnalysisUpdated', 'TEXT'), ('TrackInfoUpdated', 'TEXT'),
                       ('rb_local_usn', 'INTEGER'), ('updated_at', 'TEXT')):
        conn.execute(f'ALTER TABLE djmdContent ADD COLUMN {name} {kind}')
    conn.execute("UPDATE djmdContent SET BPM=9000,Analysed=105,FileType=11,AnalysisUpdated='2',"
                 "TrackInfoUpdated='6',rb_local_usn=41,updated_at='original-time'")
    conn.close()


class OfflineGridProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name).resolve() / 'master.db'
        fixture(self.source)
        self.source_before = self.source.read_bytes()
        self.dat = primary(tuple(Beat((i + 3) % 4 + 1, 9000, 39 + i * 2000 // 3)
                                 for i in range(252)))
        self.ext = container(section(b'PQT2', bytes.fromhex('000000000100000200000000')
                                    + bytes(16) + bytes.fromhex('00000003000012340000000000000000')
                                    + bytes(6), 56),
                             section(b'PCO2', b'keep cues'), section(b'PWV5', b'keep wave'),
                             section(b'PWV3', struct.pack('>III', 1, 25143, 0x960000)
                                     + bytes(25143), 24))
        self.store = self.create()

    def create(self):
        store = proof.OfflineGridWorkspace.create(self.source, self.dat, self.ext, '2')
        self.addCleanup(shutil.rmtree, store.workspace.root, True)
        return store

    def mutate(self, sql):
        conn = offline._connect(self.store.workspace.database)
        try:
            conn.execute(sql)
        finally:
            conn.close()

    def test_complete_grid_operations_preserve_unrelated_metadata_and_source(self):
        before = self.store.workspace._read_state(self.store.workspace.database)
        self.assertTrue(self.store.apply(self.store.plan('shift', 1)))
        self.assertEqual(self.store.workspace._read_state(self.store.workspace.database), before)
        self.store.apply(self.store.plan('shift', -1))
        self.assertEqual(self.store._bytes()['DAT'], self.dat)
        self.store.apply(self.store.plan('double'))
        conn = offline._connect(self.store.workspace.database, readonly=True)
        try:
            row = conn.execute('SELECT BPM,TrackInfoUpdated,rb_local_usn FROM djmdContent WHERE ID=?',
                               ('2',)).fetchone()
            self.assertEqual(row, (18000, '7', 43))
            self.assertEqual(conn.execute('SELECT updated_at FROM agentRegistry WHERE registry_id=?',
                                         ('localUpdateCount',)).fetchone()[0], 'counter-time')
        finally:
            conn.close()
        self.store.apply(self.store.plan('halve'))
        self.assertEqual(self.store._bytes()['DAT'], self.dat)
        after = self.store.workspace._read_state(self.store.workspace.database)
        self.assertEqual({t for t in before['tables'] if before['tables'][t] != after['tables'][t]},
                         {'djmdContent', 'agentRegistry'})
        self.store.apply(self.store.plan('downbeat', 10009))
        self.assertEqual(read_grid(self.store._bytes()['DAT'])[0], Beat(2, 9000, 9))
        self.assertEqual(self.source.read_bytes(), self.source_before)

    def test_stale_cross_workspace_and_file_revision_are_rejected(self):
        plan = self.store.plan('shift', 1)
        other = self.create()
        with self.assertRaises(offline.ConflictError):
            other.apply(plan)
        self.store.apply(plan)
        with self.assertRaises(offline.ConflictError):
            self.store.apply(plan)
        stale = self.store.plan('shift', 1)
        self.mutate("UPDATE unknown_table SET opaque=X'02' WHERE id=1")
        with self.assertRaises(offline.ConflictError):
            self.store.apply(stale)
        self.assertFalse(self.store.workspace.transaction.exists())
        with self.assertRaises(offline.WriteProofError):
            self.store.plan('double', 1)

    def test_lock_and_stale_bpm_metadata_block_while_analysis_revision_survives(self):
        plan = self.store.plan('shift', 1)
        self.mutate('UPDATE djmdContent SET Analysed=233 WHERE ID=\'2\'')
        with self.assertRaises(offline.WriteProofError):
            self.store.plan('shift', 1)
        with self.assertRaises(offline.WriteProofError):
            self.store.apply(plan)
        self.mutate("UPDATE djmdContent SET Analysed=105,AnalysisUpdated='1' WHERE ID='2'")
        self.store.apply(self.store.plan('double'))
        conn = offline._connect(self.store.workspace.database, readonly=True)
        try:
            self.assertEqual(conn.execute("SELECT AnalysisUpdated FROM djmdContent WHERE ID='2'")
                             .fetchone()[0], '1')
        finally:
            conn.close()
        self.mutate("UPDATE djmdContent SET BPM=8999 WHERE ID='2'")
        with self.assertRaises(offline.WriteProofError):
            self.store.plan('double')
        self.assertEqual(self.source.read_bytes(), self.source_before)

    def test_noop_creates_no_recovery_or_sql_changes(self):
        self.assertFalse(self.store.apply(self.store.plan('shift', 0)))
        self.assertFalse(self.store.workspace.transaction.exists())
        self.assertEqual(self.store._bytes(), {'DAT': self.dat, 'EXT': self.ext})

    def test_staged_trigger_side_effect_rejects_without_publication(self):
        self.mutate("CREATE TRIGGER bad_grid AFTER UPDATE OF BPM ON djmdContent BEGIN "
                    "UPDATE unknown_table SET opaque=X'03' WHERE id=1; END")
        before = self.store._bytes()
        with self.assertRaises(offline.WriteProofError):
            self.store.apply(self.store.plan('double'))
        self.assertEqual(self.store._bytes(), before)
        self.assertEqual(self.source.read_bytes(), self.source_before)
        # Setup failure preserves the incomplete proof; no original source writes.
        with self.assertRaises(offline.RecoveryRequired):
            self.store.recover()

    def crash(self, store, operation, phase, wal):
        if wal:
            conn = offline._connect(store.workspace.database)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.close()
        script = '''
import os,sys
from pathlib import Path
from soundbox import rekordbox_grid_proof as proof
original=proof._connect
def connect(*args,**kwargs):
    conn=original(*args,**kwargs)
    conn.execute('PRAGMA cache_size=1')
    return conn
proof._connect=connect
store=proof.OfflineGridWorkspace(Path(sys.argv[1]))
plan=store.plan(sys.argv[2], 1 if sys.argv[2]=='shift' else 0)
def checkpoint(phase):
    if phase==sys.argv[3]: os._exit(86)
proof._fault=checkpoint
store.apply(plan)
'''
        result = subprocess.run([sys.executable, '-c', script, str(store.workspace.root),
                                 operation, phase], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 86, result.stdout + result.stderr)

    def test_real_process_exit_recovers_sql_and_both_files_as_one_result(self):
        for wal in (False, True):
            for operation in ('shift', 'double'):
                for phase in ('journal-ready', 'published-DAT', 'published-EXT',
                              'before-commit', 'after-commit', 'decision-durable'):
                    with self.subTest(wal=wal, operation=operation, phase=phase):
                        store = self.create()
                        before = store.workspace._read_state(store.workspace.database)
                        self.crash(store, operation, phase, wal)
                        # A fresh instance must discover and recover durable state.
                        reopened = proof.OfflineGridWorkspace(store.workspace.root)
                        committed = (phase == 'decision-durable'
                                     or operation == 'double' and phase == 'after-commit')
                        self.assertEqual(reopened.recover(), 'committed' if committed else 'rolled-back')
                        after = reopened.workspace._read_state(reopened.workspace.database)
                        if committed:
                            self.assertNotEqual(reopened._bytes()['DAT'], self.dat)
                            if operation == 'double':
                                self.assertNotEqual(after, before)
                            else:
                                self.assertEqual(after, before)
                        else:
                            self.assertEqual(reopened._bytes(), {'DAT': self.dat, 'EXT': self.ext})
                            self.assertEqual(after, before)
                        self.assertIsNone(reopened.recover())
                        self.assertFalse(reopened.workspace.transaction.exists())
                        self.assertEqual(self.source.read_bytes(), self.source_before)

    def test_recovery_conflicts_and_damaged_backup_preserve_evidence(self):
        for damage in ('analysis', 'backup', 'database', 'journal'):
            with self.subTest(damage=damage):
                store = self.create()
                self.crash(store, 'double', 'published-DAT', False)
                if damage == 'analysis':
                    (store.folder / 'working.EXT').write_bytes(b'foreign bytes')
                elif damage == 'backup':
                    (store.folder / 'before.DAT').write_bytes(b'damaged')
                elif damage == 'database':
                    conn = offline._connect(store.workspace.database)
                    conn.execute("UPDATE unknown_table SET opaque=X'05' WHERE id=1")
                    conn.close()
                else:
                    (store.workspace.transaction / 'journal.json').write_text('{}')
                current = store._bytes()
                with self.assertRaises(offline.RecoveryRequired):
                    store.recover()
                self.assertEqual(store._bytes(), current)
                self.assertTrue(store.workspace.transaction.exists())

    def test_other_writer_blocked_and_path_redirects_rejected(self):
        self.crash(self.store, 'shift', 'published-DAT', False)
        with self.assertRaises(offline.RecoveryRequired):
            self.store.workspace.plan_tag_assignment(('2',), '11', True)
        with self.assertRaises(offline.RecoveryRequired):
            self.store.workspace.recover()
        self.store.recover()
        p = self.store.folder / 'working.DAT'
        p.unlink()
        p.symlink_to(self.source)
        with self.assertRaises(offline.WriteProofError):
            self.store.plan('shift', 1)
        self.assertEqual(self.source.read_bytes(), self.source_before)

    def test_corrupt_or_rebound_plan_cannot_redirect_metadata(self):
        plan = self.store.plan('double')
        with self.assertRaises(offline.WriteProofError):
            self.store.apply(replace(plan, operation='unknown'))
        with self.assertRaises(offline.WriteProofError):
            self.store.apply(replace(plan, timestamp='invalid timestamp'))
        config = self.store.folder / 'config.json'
        binding = json.loads(config.read_bytes())
        binding['track_id'] = '1'
        config.write_text(json.dumps(binding))
        with self.assertRaises(offline.WriteProofError):
            self.store.plan('shift', 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(),
                         hashlib.sha256(self.source_before).digest())

    def test_durability_failure_before_journal_changes_no_working_targets(self):
        original = proof.atomic_bytes
        def write(path, data):
            if path.name == 'journal.json':
                raise OSError('disk full')
            original(path, data)
        before = self.store._bytes()
        with mock.patch.object(proof, 'atomic_bytes', side_effect=write):
            with self.assertRaises(OSError):
                self.store.apply(self.store.plan('shift', 1))
        self.assertEqual(self.store._bytes(), before)
        self.assertEqual(self.source.read_bytes(), self.source_before)


if __name__ == '__main__':
    unittest.main()
