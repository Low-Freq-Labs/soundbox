"""Encrypted synthetic databases only. No live AppData, audio, or devices.

Subprocesses exit between durable transaction boundaries so recovery must work
with actual SQLite rollback/WAL recovery, not just mocked exception handling.
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_write as writer


def _fixture(path: Path):
    conn = writer._connect(path, create=True)
    conn.executescript('''
        CREATE TABLE djmdProperty(DBID TEXT PRIMARY KEY, DBVersion TEXT);
        INSERT INTO djmdProperty VALUES('synthetic-library','6000');
        CREATE TABLE djmdContent(ID TEXT PRIMARY KEY,UUID TEXT,
            rb_local_deleted INTEGER,Title TEXT,FolderPath TEXT);
        INSERT INTO djmdContent VALUES('1','track-one',0,'One','/do-not-read/one.wav');
        INSERT INTO djmdContent VALUES('2','track-two',0,'Two','/do-not-read/two.mp3');
        INSERT INTO djmdContent VALUES('3','track-deleted',1,'Deleted','/do-not-read/three.wav');
        CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Name TEXT,
            Attribute INTEGER,ParentID TEXT,rb_local_deleted INTEGER);
        INSERT INTO djmdMyTag VALUES('10','category','Genre',1,'root',0);
        INSERT INTO djmdMyTag VALUES('11','tag-dubstep','Dubstep',0,'10',0);
        INSERT INTO djmdMyTag VALUES('12','tag-riddim','Riddim',0,'10',0);
        INSERT INTO djmdMyTag VALUES('13','tag-deleted','Removed',0,'10',1);
        CREATE TABLE djmdSongMyTag(
            ID TEXT PRIMARY KEY, MyTagID TEXT, ContentID TEXT, TrackNo INTEGER,
            UUID TEXT, rb_data_status INTEGER, rb_local_data_status INTEGER,
            rb_local_deleted INTEGER, rb_local_synced INTEGER, usn INTEGER,
            rb_local_usn INTEGER, created_at TEXT, updated_at TEXT);
        INSERT INTO djmdSongMyTag VALUES('existing','11','1',NULL,'existing-uuid',
            0,0,0,1,7,40,'original-created','original-updated');
        INSERT INTO djmdSongMyTag VALUES('historical','12','2',NULL,'old-uuid',
            2,0,1,1,8,41,'old-created','old-updated');
        CREATE TABLE agentRegistry(registry_id TEXT PRIMARY KEY,int_1 INTEGER,
            updated_at TEXT,other TEXT);
        INSERT INTO agentRegistry VALUES('localUpdateCount',41,'counter-time','keep');
        INSERT INTO agentRegistry VALUES('otherRegistry',77,'other-time','keep too');
        CREATE TABLE djmdCue(ID TEXT PRIMARY KEY,ContentID TEXT,Kind INTEGER,Comment TEXT);
        INSERT INTO djmdCue VALUES('cue','1',10,'Preserve overflow cue');
        CREATE TABLE djmdPlaylist(ID TEXT PRIMARY KEY,Name TEXT,SmartList TEXT);
        INSERT INTO djmdPlaylist VALUES('playlist','Keep','opaque rule');
        CREATE TABLE unknown_table(id INTEGER PRIMARY KEY,opaque BLOB);
        INSERT INTO unknown_table VALUES(1,X'0001ff');
    ''')
    return conn


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ClosedRekordboxCheck(unittest.TestCase):
    def setUp(self):
        self.native = self.enterContext(mock.patch('soundbox.mac_process.rekordbox_running', return_value=None))

    def test_fresh_native_result_never_caches_process_absence(self):
        self.native.side_effect = [False, True, False]
        with mock.patch.object(writer.subprocess, 'run', side_effect=AssertionError('No fallback needed')):
            writer.assert_rekordbox_closed()
            with self.assertRaises(writer.WriteProofError):
                writer.assert_rekordbox_closed()
            writer.assert_rekordbox_closed()
        self.assertEqual(self.native.call_count, 3)

    def test_fresh_exact_name_query_accepts_only_no_matches(self):
        result = subprocess.CompletedProcess([], 1, '', '')
        with mock.patch.object(writer.subprocess, 'run', return_value=result) as query:
            writer.assert_rekordbox_closed()
            writer.assert_rekordbox_closed()
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.args[0],
                         ['/usr/bin/pgrep', '-x', '(rekordbox|rekordboxAgent)'])

    def test_running_or_unknown_process_state_fails_closed(self):
        for code, output, error in ((0, '123\n', ''), (2, '', 'error'),
                                    (1, 'unexpected', ''), (1, '', 'denied')):
            with self.subTest(code=code, output=output, error=error), \
                    mock.patch.object(writer.subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], code, output, error)):
                with self.assertRaises(writer.WriteProofError):
                    writer.assert_rekordbox_closed()
        with mock.patch.object(writer.subprocess, 'run', side_effect=OSError('unavailable')):
            with self.assertRaises(writer.WriteProofError):
                writer.assert_rekordbox_closed()


class OfflineTagWriter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "master.db"
        conn = _fixture(self.source)
        conn.close()
        self.source_sha = _sha(self.source)
        self.workspace = self.capture()

    def capture(self):
        workspace = writer.OfflineWorkspace.capture(self.source)
        self.addCleanup(shutil.rmtree, workspace.root, True)
        return workspace

    def test_encrypted_backup_folds_wal_without_the_offline_semantic_inventory(self):
        conn = writer._connect(self.source)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute("UPDATE djmdContent SET Title='Committed in WAL' WHERE ID='1'")
            wal = self.source.with_name('master.db-wal')
            original = self.source.read_bytes(), wal.read_bytes()
            with mock.patch.object(writer, '_state', side_effect=AssertionError('Offline audit in live backup')):
                with writer.encrypted_backup(self.source) as path:
                    private = path.parent
                    self.assertNotEqual(path.read_bytes()[:16], b'SQLite format 3\x00')
                    self.assertEqual(self.query("SELECT Title FROM djmdContent WHERE ID='1'", path=path),
                                     [('Committed in WAL',)])
                    self.assertEqual(self.query('SELECT opaque FROM unknown_table', path=path),
                                     [(bytes.fromhex('0001ff'),)])
            self.assertFalse(private.exists())
            self.assertEqual((self.source.read_bytes(), wal.read_bytes()), original)
        finally:
            conn.close()

    def test_encrypted_backup_rejects_failed_integrity_without_touching_source(self):
        with mock.patch.object(writer, '_integrity', side_effect=writer.WriteProofError('bad copy')):
            with self.assertRaisesRegex(writer.WriteProofError, 'bad copy'):
                with writer.encrypted_backup(self.source):
                    self.fail('Invalid backup was exposed')
        self.assertEqual(_sha(self.source), self.source_sha)

    def query(self, sql, args=(), path=None):
        conn = writer._connect(path or self.workspace.database, readonly=True)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def mutate(self, sql, args=()):
        conn = writer._connect(self.workspace.database)
        try:
            conn.execute(sql, args)
        finally:
            conn.close()

    def state(self):
        return self.workspace._read_state(self.workspace.database)

    def crash(self, boundary, *, wal=False, spill=False):
        if wal:
            self.mutate('PRAGMA journal_mode=WAL')
        script = '''
import os,sys
from pathlib import Path
from soundbox import rekordbox_write as writer
if sys.argv[3]=='spill':
    original=writer._connect
    def connect(*args,**kwargs):
        conn=original(*args,**kwargs)
        conn.execute('PRAGMA cache_size=1')
        return conn
    writer._connect=connect
workspace=writer.OfflineWorkspace(Path(sys.argv[1]))
plan=workspace.plan_tag_assignment(('2',),'11',True)
def checkpoint(name):
    if name==sys.argv[2]: os._exit(86)
writer._checkpoint=checkpoint
workspace.apply(plan)
'''
        result = subprocess.run([sys.executable, "-c", script,
                                 str(self.workspace.root), boundary, 'spill' if spill else 'normal'],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 86, result.stderr.decode())
        return writer.OfflineWorkspace(self.workspace.root)

    def test_capture_and_backups_are_encrypted_and_preserve_the_source(self):
        self.assertEqual(_sha(self.source), self.source_sha)
        for name in ("working.db", "baseline.db"):
            path = self.workspace.root / name
            self.assertNotEqual(path.read_bytes()[:16], b'SQLite format 3\0')
            plain = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    plain.execute('SELECT * FROM sqlite_master').fetchall()
            finally:
                plain.close()
        self.assertEqual(self.state(), self.workspace._read_state(self.workspace.root / 'baseline.db'))

    def test_add_changes_only_one_assignment_and_the_update_counter(self):
        before = self.state()
        baseline_sha = _sha(self.workspace.root / 'baseline.db')
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        result = self.workspace.apply(plan)
        self.assertEqual((result.outcome, result.changed_tracks), ('changed', 1))
        after = self.state()
        changed = {table for table in before['tables']
                   if before['tables'][table] != after['tables'][table]}
        self.assertEqual(changed, {'djmdSongMyTag', 'agentRegistry'})
        self.assertEqual(before['schema'], after['schema'])
        self.assertEqual(self.query("SELECT int_1,other FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(42, 'keep')])
        row = self.query("SELECT ID,UUID,TrackNo,rb_local_deleted,rb_local_usn FROM djmdSongMyTag "
                         "WHERE ContentID='2' AND MyTagID='11'")[0]
        self.assertNotEqual(row[0], row[1])
        self.assertEqual(row[2:], (None, 0, 42))
        self.assertEqual(_sha(self.source), self.source_sha)
        self.assertEqual(_sha(self.workspace.root / 'baseline.db'), baseline_sha)
        self.assertFalse(self.workspace.transaction.exists())

    def test_remove_preserves_history_and_readd_creates_a_distinct_identity(self):
        original = self.query("SELECT * FROM djmdSongMyTag WHERE ID='existing'")[0]
        self.workspace.apply(self.workspace.plan_tag_assignment(('1',), '11', False))
        removed = self.query("SELECT * FROM djmdSongMyTag WHERE ID='existing'")[0]
        self.assertEqual(removed[:5], original[:5])
        self.assertEqual(removed[5:10], (2, 0, 1, 0, 7))
        self.assertEqual(removed[11], original[11])
        self.workspace.apply(self.workspace.plan_tag_assignment(('1',), '11', True))
        rows = self.query("SELECT ID,rb_local_deleted FROM djmdSongMyTag WHERE ContentID='1' AND MyTagID='11'")
        self.assertEqual(len(rows), 2)
        self.assertIn(('existing', 1), rows)
        self.assertEqual(sum(deleted == 0 for _, deleted in rows), 1)

    def test_mixed_selection_add_and_remove_are_one_atomic_command_each(self):
        plan = self.workspace.plan_tag_assignment(('1', '2'), '11', True)
        self.assertEqual(plan.expected_present, (True, False))
        self.assertEqual(self.workspace.apply(plan).changed_tracks, 1)
        result = self.workspace.apply(self.workspace.plan_tag_assignment(('1', '2'), '11', False))
        self.assertEqual(result.changed_tracks, 2)
        self.assertEqual(self.query("SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'"), [(44,)])
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE MyTagID='11' AND rb_local_deleted=0"), [(0,)])

    def test_noop_does_not_write_the_working_database_or_increment_counters(self):
        sha = _sha(self.workspace.database)
        result = self.workspace.apply(self.workspace.plan_tag_assignment(('1',), '11', True))
        self.assertEqual((result.outcome, result.changed_tracks), ('unchanged', 0))
        self.assertEqual(_sha(self.workspace.database), sha)
        self.assertFalse(self.workspace.transaction.exists())

    def test_missing_deleted_category_and_duplicate_targets_fail_before_mutation(self):
        sha = _sha(self.workspace.database)
        for tracks, tag in [(('1',), 'missing'), (('1',), '13'), (('1',), '10'),
                            (('missing',), '11'), (('3',), '11'),
                            (('1', '1'), '11'), ((), '11')]:
            with self.subTest(tracks=tracks, tag=tag), self.assertRaises(writer.WriteProofError):
                self.workspace.plan_tag_assignment(tracks, tag, True)
        self.assertEqual(_sha(self.workspace.database), sha)

    def test_duplicate_active_links_and_unverified_ordering_fail_closed(self):
        self.mutate("UPDATE djmdSongMyTag SET TrackNo=1 WHERE ID='existing'")
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('1',), '11', False)
        self.mutate("UPDATE djmdSongMyTag SET TrackNo=NULL WHERE ID='existing'")
        self.mutate("INSERT INTO djmdSongMyTag SELECT 'duplicate',MyTagID,ContentID,TrackNo,"
                    "UUID,rb_data_status,rb_local_data_status,rb_local_deleted,rb_local_synced,usn,"
                    "rb_local_usn,created_at,updated_at FROM djmdSongMyTag WHERE ID='existing'")
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('1',), '11', False)

    def test_stale_and_cross_workspace_plans_are_rejected(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        self.mutate("UPDATE unknown_table SET opaque=X'02'")
        with self.assertRaises(writer.ConflictError):
            self.workspace.apply(plan)
        other = self.capture()
        with self.assertRaises(writer.ConflictError):
            other.apply(plan)

    def test_changed_database_header_metadata_invalidates_a_plan(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        self.mutate('PRAGMA user_version=123')
        with self.assertRaises(writer.ConflictError):
            self.workspace.apply(plan)

    def test_changed_library_identity_is_rejected(self):
        self.mutate("UPDATE djmdProperty SET DBID='different-library'")
        with self.assertRaises(writer.ConflictError):
            self.workspace.plan_tag_assignment(('2',), '11', True)

    def test_wrong_library_version_and_changed_link_schema_fail_closed(self):
        self.mutate("UPDATE djmdProperty SET DBVersion='unknown'")
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('1',), '11', False)
        self.mutate("UPDATE djmdProperty SET DBVersion='6000'")
        self.mutate("ALTER TABLE djmdSongMyTag ADD COLUMN unknown TEXT")
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('1',), '11', False)

    def test_unrelated_trigger_effects_are_rejected_on_the_stage(self):
        self.mutate("CREATE TRIGGER side_effect AFTER INSERT ON djmdSongMyTag "
                    "BEGIN UPDATE unknown_table SET opaque=X'03'; END")
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        before = self.state()
        with self.assertRaisesRegex(writer.WriteProofError, 'unrelated table'):
            self.workspace.apply(plan)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.workspace.transaction.exists())

    def test_trigger_changes_to_other_rows_in_allowed_tables_are_rejected(self):
        self.mutate("CREATE TRIGGER side_effect AFTER INSERT ON djmdSongMyTag "
                    "BEGIN UPDATE agentRegistry SET int_1=999 WHERE registry_id='otherRegistry'; END")
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        before = self.state()
        with self.assertRaisesRegex(writer.WriteProofError, 'unexpected rows'):
            self.workspace.apply(plan)
        self.assertEqual(self.state(), before)

    def test_conflict_between_stage_and_commit_does_not_overwrite_external_changes(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        def checkpoint(name):
            if name == 'journal_ready':
                self.mutate("UPDATE unknown_table SET opaque=X'04'")
        with mock.patch.object(writer, '_checkpoint', checkpoint):
            with self.assertRaises(writer.ConflictError):
                self.workspace.apply(plan)
        with self.assertRaises(writer.RecoveryRequired):
            self.workspace.recover()
        self.assertEqual(self.query('SELECT opaque FROM unknown_table'), [(b'\x04',)])
        self.assertTrue((self.workspace.transaction / 'before.db').is_file())

    def test_backup_failure_leaves_working_database_unchanged(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        before = _sha(self.workspace.database)
        with mock.patch.object(writer, '_backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        self.assertEqual(_sha(self.workspace.database), before)
        self.assertFalse(self.workspace.transaction.exists())

    def test_journal_failure_prevents_mutation(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        before = _sha(self.workspace.database)
        with mock.patch.object(writer, 'atomic_bytes', side_effect=OSError('fsync failed')):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        self.assertEqual(_sha(self.workspace.database), before)
        self.assertFalse(self.workspace.transaction.exists())

    def test_failure_before_commit_rolls_back_and_recovery_proves_it(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        before = self.state()
        def checkpoint(name):
            if name == 'before_commit':
                raise OSError('write failed')
        with mock.patch.object(writer, '_checkpoint', checkpoint):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.workspace.recover().outcome, 'recovered-rollback')

    def test_failure_after_commit_never_reports_a_success_without_readback(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        def checkpoint(name):
            if name == 'after_commit':
                raise OSError('verification interrupted')
        with mock.patch.object(writer, '_checkpoint', checkpoint):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        with self.assertRaises(writer.RecoveryRequired):
            self.workspace.plan_tag_assignment(('2',), '11', False)
        self.assertEqual(self.workspace.recover().outcome, 'recovered-commit')

    def test_receipt_failure_retains_evidence_of_the_committed_operation(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        original = writer.atomic_bytes
        def atomic(path, data):
            if path.name == 'last-result.json':
                raise OSError('receipt fsync failed')
            return original(path, data)
        with mock.patch.object(writer, 'atomic_bytes', side_effect=atomic):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        self.assertTrue(self.workspace.transaction.exists())
        self.assertEqual(self.workspace.recover().outcome, 'recovered-commit')

    def test_interrupted_retirement_cleanup_cannot_undo_a_committed_operation(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        with mock.patch.object(self.workspace, '_remove_directory', side_effect=OSError('cleanup interrupted')):
            with self.assertRaises(OSError):
                self.workspace.apply(plan)
        self.assertTrue(self.workspace.retired.exists())
        self.assertFalse(self.workspace.transaction.exists())
        self.assertIsNone(self.workspace.recover())
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE ContentID='2' "
                                    "AND MyTagID='11' AND rb_local_deleted=0"), [(1,)])

    def test_external_sqlite_writer_cannot_be_overwritten(self):
        from sqlcipher3 import dbapi2 as sqlcipher
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        conn = writer._connect(self.workspace.database)
        try:
            conn.execute('BEGIN EXCLUSIVE')
            with self.assertRaisesRegex(sqlcipher.OperationalError, 'locked'):
                self.workspace.apply(plan)
            self.assertFalse(self.workspace.transaction.exists())
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(self.workspace.plan_tag_assignment(('2',), '11', True).expected_present, (False,))

    def test_copy_operations_never_use_pyrekordbox_live_configuration_discovery(self):
        with mock.patch('pyrekordbox.config.update_config', side_effect=AssertionError('live discovery')):
            workspace = self.capture()
            result = workspace.apply(workspace.plan_tag_assignment(('2',), '11', True))
        self.assertEqual(result.outcome, 'changed')

    def test_process_exit_at_each_boundary_recovers_from_real_sqlite_state(self):
        for boundary, expected in [('backup_ready', None), ('journal_ready', 'recovered-rollback'),
                                   ('before_commit', 'recovered-rollback'),
                                   ('after_commit', 'recovered-commit'), ('verified', 'recovered-commit')]:
            with self.subTest(boundary=boundary):
                self.workspace = self.capture()
                reopened = self.crash(boundary)
                result = reopened.recover()
                self.assertEqual(result.outcome if result else None, expected)
                self.assertIsNone(reopened.recover())

    def test_process_exit_with_committed_wal_recovers_the_committed_state(self):
        reopened = self.crash('after_commit', wal=True)
        self.assertTrue(self.workspace.database.with_name('working.db-wal').is_file())
        self.assertEqual(reopened.recover().outcome, 'recovered-commit')

    def test_process_exit_after_dirty_pages_spill_recovers_the_hot_journal(self):
        reopened = self.crash('before_commit', spill=True)
        self.assertTrue(self.workspace.database.with_name('working.db-journal').is_file())
        self.assertEqual(reopened.recover().outcome, 'recovered-rollback')

    def test_private_connection_helper_also_rejects_non_temporary_paths(self):
        with self.assertRaisesRegex(writer.WriteProofError, 'temporary copies'):
            writer._connect(Path.home() / 'Library/Pioneer/rekordbox/master.db')

    def test_symlinked_sqlite_sidecar_is_rejected_without_touching_its_target(self):
        sidecar = self.workspace.database.with_name('working.db-wal')
        sidecar.symlink_to(self.source)
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('2',), '11', True)
        self.assertEqual(_sha(self.source), self.source_sha)

    def test_unknown_schema_capture_cleans_up_its_fresh_workspace(self):
        original = writer.OfflineWorkspace._capture_into
        roots = []
        def capture_into(source, root):
            roots.append(root)
            return original(source, root)
        conn = writer._connect(self.source)
        conn.execute("UPDATE djmdProperty SET DBVersion='unknown'")
        conn.close()
        with mock.patch.object(writer.OfflineWorkspace, '_capture_into', side_effect=capture_into):
            with self.assertRaises(writer.WriteProofError):
                writer.OfflineWorkspace.capture(self.source)
        self.assertEqual(len(roots), 1)
        self.assertFalse(roots[0].exists())

    def test_recovery_preserves_damaged_evidence_and_unexpected_current_state(self):
        self.crash('after_commit')
        conn = writer._connect(self.workspace.transaction / 'before.db')
        conn.execute("UPDATE unknown_table SET opaque=X'05'")
        conn.close()
        before = self.state()
        with self.assertRaises(writer.RecoveryRequired):
            self.workspace.recover()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.workspace.transaction.exists())

    def test_concurrent_owner_is_rejected(self):
        import fcntl
        lock = (self.workspace.root / 'writer.lock').open('a')
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(writer.WriteProofError, 'active writer'):
                self.workspace.plan_tag_assignment(('2',), '11', True)
        finally:
            lock.close()

    def test_symlinks_hardlinks_relocated_roots_and_unmarked_targets_are_rejected(self):
        with self.assertRaises((writer.WriteProofError, FileNotFoundError)):
            writer.OfflineWorkspace(self.base)
        alias = self.base / 'alias.db'
        alias.symlink_to(self.source)
        with self.assertRaises(writer.WriteProofError):
            writer.OfflineWorkspace.capture(alias)
        alias.unlink()
        path = self.workspace.database
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaises(writer.WriteProofError):
            self.workspace.plan_tag_assignment(('2',), '11', True)
        path.unlink()
        os.link(self.source, path)
        with self.assertRaises(writer.WriteProofError):
            writer.OfflineWorkspace(self.workspace.root)
        path.unlink()
        other = self.capture()
        relocated = self.base / other.root.name
        shutil.copytree(other.root, relocated)
        with self.assertRaises(writer.WriteProofError):
            writer.OfflineWorkspace(relocated)
        self.assertEqual(_sha(self.source), self.source_sha)

    def test_unexpected_recovery_files_are_not_deleted(self):
        self.workspace.transaction.mkdir(mode=0o700)
        sentinel = self.workspace.transaction / 'not-ours'
        sentinel.write_text('preserve')
        with self.assertRaises(writer.RecoveryRequired):
            self.workspace.recover()
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_journal_identity_tampering_does_not_authorize_recovery(self):
        self.crash('after_commit')
        path = self.workspace.transaction / 'journal.json'
        record = json.loads(path.read_text())
        record['workspace_id'] = 'another-workspace'
        path.write_text(json.dumps(record))
        with self.assertRaises(writer.RecoveryRequired):
            self.workspace.recover()
        self.assertTrue(self.workspace.transaction.exists())

    def test_inconsistent_expected_membership_is_rejected(self):
        plan = self.workspace.plan_tag_assignment(('2',), '11', True)
        with self.assertRaises(writer.ConflictError):
            self.workspace.apply(replace(plan, expected_present=(True,)))

    def test_capture_includes_a_committed_uncheckpointed_source_wal(self):
        source_conn = writer._connect(self.source)
        try:
            source_conn.execute('PRAGMA journal_mode=WAL')
            source_conn.execute('PRAGMA wal_autocheckpoint=0')
            source_conn.execute("INSERT INTO unknown_table VALUES(2,X'06')")
            before = (_sha(self.source), _sha(self.source.with_name('master.db-wal')))
            workspace = self.capture()
            self.assertEqual(self.query('SELECT id FROM unknown_table ORDER BY id',
                                        path=workspace.database), [(1,), (2,)])
            self.assertEqual(before, (_sha(self.source), _sha(self.source.with_name('master.db-wal'))))
        finally:
            source_conn.close()
