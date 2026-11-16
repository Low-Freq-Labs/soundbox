"""Identity-bound tag transactions on synthetic encrypted libraries only."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_tags as tags
from soundbox import rekordbox_write as offline
from tests.test_rekordbox_write import _fixture


class NativeTagTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        source_dir = self.base / 'source'
        source_dir.mkdir()
        self.source = source_dir / 'master.db'
        conn = _fixture(self.source)
        self.authority = tags.TagWriteAuthority(str(self.source), 'synthetic-library', tags._schema(conn))
        conn.close()
        self.closed = self.enterContext(mock.patch.object(tags, '_assert_closed'))
        self.enterContext(mock.patch.object(tags, '_installed_version', return_value=tags.NATIVE_VERSION))
        self.store = tags.LiveTagStore(self.authority, self.base / 'recovery')
        self.request = tags.TagWriteRequest('synthetic-library',
            (tags.SelectedTagTrack('2', 'track-two', False),), '11', 'tag-dubstep', True, 'Dubstep')

    def connect(self):
        return offline._connect(self.source)

    def query(self, sql, args=()):
        conn = self.connect()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def state(self):
        conn = self.connect()
        try:
            return offline._state(conn)
        finally:
            conn.close()

    def mutate(self, sql):
        conn = self.connect()
        conn.execute(sql)
        conn.close()

    def test_add_matches_native_fields_and_preserves_registry_timestamp(self):
        before = self.state()
        self.assertEqual(self.store.apply(self.request), 1)
        after = self.state()
        self.assertEqual({t for t in before['tables'] if before['tables'][t] != after['tables'][t]},
                         {'djmdSongMyTag', 'agentRegistry'})
        self.assertEqual(self.query("SELECT int_1,updated_at FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(42, 'counter-time')])
        row = self.query("SELECT TrackNo,rb_data_status,rb_local_data_status,rb_local_deleted,"
                         "rb_local_synced,usn,rb_local_usn,created_at,updated_at FROM djmdSongMyTag "
                         "WHERE MyTagID='11' AND ContentID='2'")[0]
        self.assertEqual(row[:7], (None, 0, 0, 0, 0, None, 42))
        self.assertEqual(row[7], row[8])
        self.assertTrue(row[7].endswith(' +00:00'))
        self.assertTrue((self.store.root / 'baseline.db').is_file())
        self.assertFalse(self.store.journal.exists())

    def test_unsynced_removal_is_a_real_delete_and_keeps_other_history(self):
        self.store.apply(self.request)
        remove = replace(self.request, tracks=(tags.SelectedTagTrack('2', 'track-two', True),), present=False)
        self.assertEqual(self.store.apply(remove), 1)
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE ContentID='2' AND MyTagID='11'"), [(0,)])
        self.assertEqual(self.query("SELECT rb_local_deleted FROM djmdSongMyTag WHERE ID='historical'"), [(1,)])
        self.assertEqual(self.query("SELECT int_1,updated_at FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(43, 'counter-time')])

    def test_historical_removal_is_blocked_without_native_acceptance(self):
        self.mutate("UPDATE djmdSongMyTag SET usn=NULL WHERE ID='existing'")
        request = replace(self.request, tracks=(tags.SelectedTagTrack('1', 'track-one', True),), present=False)
        before = self.state()
        with self.assertRaisesRegex(offline.WriteProofError, 'Historical tag removal'):
            self.store.apply(request)
        self.assertEqual(self.state(), before)

    def test_unverified_cloud_sync_metadata_is_not_guessed(self):
        authority=replace(self.authority,synced_removal=True)
        store=tags.LiveTagStore(authority,self.base/'cloud-recovery')
        request=replace(self.request,tracks=(tags.SelectedTagTrack('1','track-one',True),),present=False)
        with self.assertRaisesRegex(offline.WriteProofError,'synchronization metadata'):
            store.apply(request)

    def test_accepted_synchronized_removal_matches_native_tombstone_and_keeps_sync_flag(self):
        self.mutate("UPDATE djmdSongMyTag SET usn=NULL WHERE ID='existing'")
        authority=replace(self.authority,synced_removal=True)
        store=tags.LiveTagStore(authority,self.base/'accepted-recovery')
        request=replace(self.request,tracks=(tags.SelectedTagTrack('1','track-one',True),),present=False)
        self.assertEqual(store.apply(request),1)
        row=self.query("SELECT UUID,rb_data_status,rb_local_data_status,rb_local_deleted,"
                       "rb_local_synced,usn,rb_local_usn,created_at,updated_at "
                       "FROM djmdSongMyTag WHERE ID='existing'")[0]
        self.assertEqual(row[:8],('existing-uuid',2,0,1,1,None,42,'original-created'))
        self.assertTrue(row[8].endswith(' +00:00'))
        self.assertEqual(self.query("SELECT int_1,updated_at FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(42,'counter-time')])

    def test_noop_has_no_counter_change_or_new_backup(self):
        request = replace(self.request, tracks=(tags.SelectedTagTrack('1', 'track-one', True),))
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(self.store.apply(request), 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertFalse((self.store.root / 'baseline.db').exists())

    def test_mixed_selection_updates_only_missing_assignments(self):
        request = replace(self.request, tracks=(tags.SelectedTagTrack('1', 'track-one', True),
                                                tags.SelectedTagTrack('2', 'track-two', False)))
        self.assertEqual(self.store.apply(request), 1)
        self.assertEqual(self.query("SELECT updated_at FROM djmdSongMyTag WHERE ID='existing'"), [('original-updated',)])

    def test_source_and_selection_conflicts_fail_without_mutation(self):
        before = self.state()
        for request in [replace(self.request, library_id='wrong'),
                        replace(self.request, tag_uuid='wrong'),
                        replace(self.request, tag_name='Renamed tag'),
                        replace(self.request, tracks=(tags.SelectedTagTrack('2', 'wrong', False),)),
                        replace(self.request, tracks=(tags.SelectedTagTrack('2', 'track-two', True),)),
                        replace(self.request, tracks=(tags.SelectedTagTrack('3', 'track-deleted', False),)),
                        replace(self.request, tracks=self.request.tracks * 2)]:
            with self.subTest(request=request), self.assertRaises(offline.ConflictError):
                self.store.apply(request)
        self.assertEqual(self.state(), before)

    def test_running_rekordbox_or_agent_blocks_before_connection(self):
        self.closed.side_effect = offline.WriteProofError('rekordbox is running')
        with mock.patch.object(self.store, '_connect') as connection:
            with self.assertRaises(offline.WriteProofError):
                self.store.apply(self.request)
            connection.assert_not_called()

    def test_wrong_version_schema_and_pilot_target_fail_closed(self):
        with mock.patch.object(tags, '_installed_version', return_value='unknown'):
            with self.assertRaises(offline.WriteProofError):
                self.store.apply(self.request)
        pilot = tags.LiveTagStore(replace(self.authority, pilot_track_id='1', pilot_tag_id='11'),
                                  self.base / 'pilot-recovery')
        with self.assertRaises(offline.WriteProofError):
            pilot.apply(self.request)
        self.mutate('CREATE TABLE unverified_schema(id TEXT)')
        with self.assertRaises(offline.ConflictError):
            self.store.apply(self.request)

    def test_backup_failure_cannot_change_the_source(self):
        before = self.state()
        with mock.patch.object(self.store, '_backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.apply(self.request)
        self.assertEqual(self.state(), before)

    def test_precommit_failure_rolls_back_and_can_retry(self):
        before = self.state()
        def fault(phase):
            if phase == 'before-commit':
                raise OSError('failure')
        with mock.patch.object(tags, '_fault', fault):
            with self.assertRaises(OSError):
                self.store.apply(self.request)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())
        self.assertEqual(self.store.apply(self.request), 1)

    def test_recovery_refuses_a_rebound_track_identity(self):
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('interrupted')
        with mock.patch.object(tags, '_fault', fault):
            with self.assertRaises(OSError):
                self.store.apply(self.request)
        self.mutate("UPDATE djmdContent SET UUID='replacement' WHERE ID='2'")
        before = self.state()
        with self.assertRaisesRegex(offline.RecoveryRequired, 'Track identity changed'):
            self.store.check_ready()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_recovery_rejects_impossible_assignment_deltas_before_sqlite(self):
        def fault(phase):
            if phase == 'before-commit':
                raise OSError('injected')
        with mock.patch.object(tags, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(self.request)
        original = self.store.journal.read_bytes()
        source_before = self.source.read_bytes()
        for damage in ('link-field', 'link-counter', 'registry-counter', 'action'):
            with self.subTest(damage=damage):
                record = json.loads(original)
                delta = {d['table']: d for d in record['changes']}
                if damage == 'link-field':
                    delta['djmdSongMyTag']['after']['TrackNo'] = 4
                elif damage == 'link-counter':
                    delta['djmdSongMyTag']['after']['rb_local_usn'] += 1
                elif damage == 'registry-counter':
                    delta['agentRegistry']['after']['int_1'] += 1
                else:
                    record['request']['present'] = False
                self.store.journal.write_text(json.dumps(record))
                with mock.patch.object(self.store, '_connect') as connection:
                    with self.assertRaises(offline.RecoveryRequired):
                        self.store.check_ready()
                    connection.assert_not_called()
                self.assertEqual(self.source.read_bytes(), source_before)
                self.assertTrue(self.store.journal.exists())

    def test_recovery_refuses_replaced_database_file_before_sql_connection(self):
        def fault(phase):
            if phase == 'before-commit':
                raise OSError('injected')
        with mock.patch.object(tags, '_fault', fault), self.assertRaises(OSError):
            self.store.apply(self.request)
        replacement = self.source.with_name('replacement.db')
        conn = _fixture(replacement)
        conn.close()
        replacement.replace(self.source)
        before = self.source.read_bytes()
        with mock.patch.object(self.store, '_connect') as connection:
            with self.assertRaises(offline.RecoveryRequired):
                self.store.check_ready()
            connection.assert_not_called()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(self.store.journal.exists())

    def test_committed_but_unconfirmed_write_recovers_without_duplicate_rows(self):
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('connection lost')
        with mock.patch.object(tags, '_fault', fault):
            with self.assertRaises(OSError):
                self.store.apply(self.request)
        self.assertTrue(self.store.journal.exists())
        # Recover, but reject retrying the old expected membership.
        with self.assertRaises(offline.ConflictError):
            self.store.apply(self.request)
        self.assertFalse(self.store.journal.exists())
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE ContentID='2' AND MyTagID='11'"), [(1,)])

    def test_unknown_postcrash_state_is_preserved(self):
        def fault(phase):
            if phase == 'after-commit':
                raise OSError('connection lost')
        with mock.patch.object(tags, '_fault', fault):
            with self.assertRaises(OSError):
                self.store.apply(self.request)
        self.mutate("UPDATE agentRegistry SET int_1=999 WHERE registry_id='localUpdateCount'")
        before = self.state()
        with self.assertRaises(offline.RecoveryRequired):
            self.store.apply(self.request)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_process_starting_before_commit_causes_a_rollback(self):
        before = self.state()
        def fault(phase):
            if phase == 'journal-ready':
                self.closed.side_effect = offline.WriteProofError('rekordbox started')
        with mock.patch.object(tags, '_fault', fault):
            with self.assertRaises(offline.WriteProofError):
                self.store.apply(self.request)
        self.assertEqual(self.state(), before)

    def test_authorizer_refuses_unrelated_table_writes_and_trigger_side_effects(self):
        from sqlcipher3 import dbapi2 as sqlcipher
        self.mutate("CREATE TRIGGER not_allowed AFTER INSERT ON djmdSongMyTag "
                    "BEGIN UPDATE unknown_table SET opaque=X'09'; END")
        conn = self.connect()
        authority = replace(self.authority, schema_sha256=tags._schema(conn))
        conn.close()
        store = tags.LiveTagStore(authority, self.base / 'trigger-recovery')
        before = self.state()
        with self.assertRaisesRegex(sqlcipher.DatabaseError, 'authorized'):
            store.apply(self.request)
        self.assertEqual(self.state(), before)

    def test_authority_file_cannot_enable_a_different_library(self):
        from dataclasses import asdict
        path = self.base / 'authority.json'
        path.write_text(json.dumps(asdict(self.authority)))
        self.assertIsNotNone(tags.load_authority(path, self.source, 'synthetic-library'))
        self.assertIsNone(tags.load_authority(path, self.source, 'wrong-library'))
        path.unlink()
        path.symlink_to(self.source)
        self.assertIsNone(tags.load_authority(path, self.source, 'synthetic-library'))

    def test_missing_cached_backup_is_recreated_before_the_next_change(self):
        self.store.apply(self.request)
        (self.store.root/'baseline.db').unlink()
        remove=replace(self.request,tracks=(tags.SelectedTagTrack('2','track-two',True),),present=False)
        self.assertEqual(self.store.apply(remove),1)
        self.assertTrue((self.store.root/'baseline.db').is_file())

    def test_damaged_pending_backup_blocks_before_opening_the_source(self):
        def fault(phase):
            if phase=='after-commit':raise OSError('interrupted')
        with mock.patch.object(tags,'_fault',fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        (self.store.root/'baseline.db').write_bytes(b'damaged backup')
        before=self.source.read_bytes()
        with mock.patch.object(self.store,'_connect') as connect:
            with self.assertRaises(offline.RecoveryRequired):self.store.apply(self.request)
            connect.assert_not_called()
        self.assertEqual(self.source.read_bytes(),before)
        self.assertTrue(self.store.journal.exists())

    def test_real_process_exit_recovers_commits_and_spilled_rollback_journals(self):
        from dataclasses import asdict
        script='''
import json,os,sys
from pathlib import Path
from soundbox import rekordbox_tags as tags
tags._assert_closed=lambda:None
tags._installed_version=lambda:tags.NATIVE_VERSION
original=tags.LiveTagStore._connect
def connect(self):
    conn=original(self);conn.execute('PRAGMA cache_size=1');return conn
tags.LiveTagStore._connect=connect
def fault(phase):
    if phase==sys.argv[3]:os._exit(86)
tags._fault=fault
store=tags.LiveTagStore(tags.TagWriteAuthority(**json.loads(sys.argv[1])),Path(sys.argv[2]))
request=tags.TagWriteRequest('synthetic-library',(tags.SelectedTagTrack('2','track-two',False),),'11','tag-dubstep',True,'Dubstep')
store.apply(request)
'''
        for phase,outcome in [('before-commit','rolled-back'),('after-commit','committed')]:
            with self.subTest(phase=phase):
                directory=self.base/phase;source=directory/'source'/'master.db'
                source.parent.mkdir(parents=True)
                conn=_fixture(source);conn.close()
                authority=replace(self.authority,database=str(source))
                root=directory/'recovery'
                result=subprocess.run([sys.executable,'-c',script,json.dumps(asdict(authority)),str(root),phase],
                                      capture_output=True,timeout=30)
                self.assertEqual(result.returncode,86,result.stderr.decode())
                store=tags.LiveTagStore(authority,root)
                with store._lock():
                    conn=store._connect()
                    try:self.assertEqual(store._recover(conn),outcome)
                    finally:conn.close()
                self.assertFalse(store.journal.exists())
