"""Native-shaped deletion on generated encrypted TEMP data, never live sources."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_tag_definitions as definitions
from soundbox import rekordbox_tags as assignments
from soundbox import rekordbox_write as writer
from soundbox.rekordbox_playlist_plan import smart_tag_rule
from tests.test_rekordbox_tag_definitions import fixture as create_fixture


def fixture(path):
    conn = create_fixture(path)
    conn.executescript('''
        ALTER TABLE djmdPlaylist ADD COLUMN Attribute INTEGER DEFAULT 0;
        ALTER TABLE djmdPlaylist ADD COLUMN rb_local_deleted INTEGER DEFAULT 0;
        INSERT INTO djmdMyTag VALUES('30',3,'Delete me',0,'10',
            '00000000-0000-4000-8000-000000000030',0,0,0,0,NULL,35,
            '2026-11-10 00:00:00.000 +00:00','2026-11-10 00:00:01.000 +00:00');
        INSERT INTO djmdMyTag VALUES('31',4,'Keep first',0,'10',
            '00000000-0000-4000-8000-000000000031',0,0,0,0,NULL,36,
            '2026-11-10 00:00:00.000 +00:00','2026-11-10 00:00:02.000 +00:00');
        INSERT INTO djmdMyTag VALUES('32',5,'Keep second',0,'10',
            '00000000-0000-4000-8000-000000000032',0,0,0,0,NULL,37,
            '2026-11-10 00:00:00.000 +00:00','2026-11-10 00:00:03.000 +00:00');
    ''')
    return conn


def request():
    return definitions.deletion_request_for('synthetic-library', '10', 'category', '30',
        '00000000-0000-4000-8000-000000000030', 'Delete me',
        now=datetime(2026, 11, 13, 2, 29, 58, 929000, tzinfo=timezone.utc))


class DeletionTransactions(unittest.TestCase):
    def setUp(self):
        self.area = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.area).resolve()
        self.source = self.root / 'source/master.db'
        self.source.parent.mkdir()
        conn = fixture(self.source)
        schema = assignments._schema(conn);conn.close()
        self.closed = self.enterContext(mock.patch.object(definitions, '_assert_closed'))
        self.enterContext(mock.patch.object(definitions, '_installed_version', return_value=assignments.NATIVE_VERSION))
        self.authority = definitions.TagDeletionAuthority(str(self.source), 'synthetic-library', schema,
            accepted=True, protocol_profile=definitions.NATIVE_DELETE_PROFILE.profile)
        self.store = definitions.TagDeletionStore(self.authority, self.root / 'recovery')
        self.request = request()

    def state(self):
        conn = writer._connect(self.source)
        try:return writer._state(conn)
        finally:conn.close()

    def query(self, sql, parameters=()):
        conn = writer._connect(self.source)
        try:return conn.execute(sql, parameters).fetchall()
        finally:conn.close()

    def mutate(self, sql, parameters=()):
        conn = writer._connect(self.source)
        try:conn.execute(sql, parameters)
        finally:conn.close()

    def test_only_target_sibling_compaction_and_counter_change_preserve_all_other_tables(self):
        before = self.state()
        conn = writer._connect(self.source)
        try:category, siblings, counter = self.store._snapshot(conn, self.request)[:3]
        finally:conn.close()
        expected = definitions.plan_delete(self.request, category, siblings, counter, definitions.NATIVE_DELETE_PROFILE)
        removed = self.store.apply(self.request);after = self.state()
        self.assertEqual(removed['ID'], '30')
        self.assertEqual({table for table in before['tables'] if before['tables'][table] != after['tables'][table]},
                         {'djmdMyTag', 'agentRegistry'})
        self.assertEqual(self.query('SELECT ID,Seq,rb_local_usn,updated_at FROM djmdMyTag WHERE ID IN (?,?) ORDER BY ID',
                                   ('31', '32')), [('31', 3, 42, self.request['timestamp']), ('32', 4, 43, self.request['timestamp'])])
        self.assertEqual(self.query("SELECT int_1,updated_at,other FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(44, 'counter-time', 'keep')])
        self.assertEqual(self.query("SELECT Seq,rb_local_deleted FROM djmdMyTag WHERE ID='13'"), [(1000, 1)])
        self.assertEqual(self.query("SELECT count(*) FROM djmdMyTag WHERE ID='30'"), [(0,)])
        receipt = json.loads(self.store._receipt_path(self.request['operation_id']).read_bytes())
        self.assertEqual(receipt['changes'], expected)
        self.assertEqual(receipt['format'], definitions.DELETE_FORMAT)
        self.assertFalse((self.store.root / 'baseline.db').read_bytes().startswith(b'SQLite format 3'))

    def test_last_leaf_has_deletion_increment_only_and_no_sibling_mutations(self):
        self.mutate("DELETE FROM djmdMyTag WHERE ID IN ('31','32')")
        self.store.apply(self.request)
        receipt = json.loads(self.store._receipt_path(self.request['operation_id']).read_bytes())
        self.assertEqual(len(receipt['changes']), 2)
        self.assertEqual(receipt['changes'][-1]['after']['int_1'], 42)

    def test_legacy_local_intent_without_phase_bundle_keeps_its_original_protocol(self):
        legacy = {key: value for key, value in self.request.items() if key != 'clocks'}
        self.store.apply(legacy)
        receipt = json.loads(self.store._receipt_path(legacy['operation_id']).read_bytes())
        self.assertEqual(receipt['request'], legacy)
        self.assertEqual(self.query("SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'"), [(44,)])

    def test_historical_and_synced_links_refuse_without_mutation(self):
        for kind in ('active', 'historical'):
            with self.subTest(kind=kind):
                if kind in ('active', 'historical'):
                    self.mutate("UPDATE djmdSongMyTag SET MyTagID='30' WHERE ID=?", ('existing' if kind == 'active' else 'historical',))
                before = self.state()
                with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
                self.assertEqual(self.state(), before);self.assertFalse(self.store.journal.exists())
                self.mutate("UPDATE djmdSongMyTag SET MyTagID='11' WHERE ID='existing'")
                self.mutate("UPDATE djmdSongMyTag SET MyTagID='12' WHERE ID='historical'")
                self.mutate("UPDATE djmdPlaylist SET Attribute=0,SmartList='opaque rule' WHERE ID='playlist'")

    def test_deleted_smart_reference_does_not_mutate_or_block_its_retained_rule(self):
        rule = smart_tag_rule('123', ['30'])
        self.mutate("UPDATE djmdPlaylist SET Attribute=4,rb_local_deleted=1,SmartList=? WHERE ID='playlist'", (rule,))
        self.store.apply(self.request)
        self.assertEqual(self.query("SELECT SmartList FROM djmdPlaylist WHERE ID='playlist'"), [(rule,)])

    def test_local_assignment_delete_consumes_no_extra_counter_and_keeps_all_smart_rule_bytes(self):
        self.mutate("INSERT INTO djmdSongMyTag VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            '00000000-0000-4000-8000-000000000040', '30', '1', None,
            '00000000-0000-4000-8000-000000000041', 0, 0, 0, 0, None, 40,
            '2026-11-11 00:00:00.000 +00:00', '2026-11-11 00:00:00.000 +00:00'))
        # Preserve unknown native rule bytes as well as positive/negative refs;
        # deletion does not reinterpret or rewrite any intelligent definition.
        for index, rule in enumerate((smart_tag_rule('123', ['30']),
                smart_tag_rule('123', ['30']).replace('Operator="8"', 'Operator="9"'), '<opaque-native-rule/>')):
            self.mutate('INSERT INTO djmdPlaylist(ID,Name,SmartList,Attribute,rb_local_deleted) VALUES(?,?,?,4,0)',
                        (f'smart-{index}', f'Keep smart {index}', rule))
        before_rule = self.query('SELECT * FROM djmdPlaylist ORDER BY ID')
        self.store.apply(self.request)
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE MyTagID='30'"), [(0,)])
        self.assertEqual(self.query("SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'"), [(44,)])
        self.assertEqual(self.query('SELECT * FROM djmdPlaylist ORDER BY ID'), before_rule)
        receipt = json.loads(self.store._receipt_path(self.request['operation_id']).read_bytes())
        links = [change for change in receipt['changes'] if change['table'] == 'djmdSongMyTag']
        self.assertEqual(len(links), 1)
        self.assertIsNone(links[0]['after'])

    def test_changed_target_category_order_and_sync_metadata_refuse_without_writing(self):
        mutations = ["UPDATE djmdMyTag SET Name='Replaced' WHERE ID='30'",
                     "UPDATE djmdMyTag SET UUID='replacement' WHERE ID='30'",
                     "UPDATE djmdMyTag SET UUID='category-replaced' WHERE ID='10'",
                     "UPDATE djmdMyTag SET Seq=3 WHERE ID='31'",
                     "UPDATE djmdMyTag SET rb_local_synced=1 WHERE ID='30'",
                     "UPDATE djmdMyTag SET rb_local_synced=1 WHERE ID='31'",
                     "UPDATE djmdMyTag SET usn=12 WHERE ID='30'",
                     "UPDATE djmdMyTag SET rb_data_status=257 WHERE ID='31'"]
        conn = writer._connect(self.source)
        try:category, siblings, counter = self.store._snapshot(conn, self.request)[:3]
        finally:conn.close()
        for sql in mutations:
            with self.subTest(sql=sql):
                self.mutate(sql);before = self.state()
                with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
                self.assertEqual(self.state(), before)
                conn = writer._connect(self.source)
                try:
                    for row in [category, *siblings]:
                        fields = [key for key in row if key != 'ID']
                        conn.execute('UPDATE djmdMyTag SET ' + ','.join('"' + key + '"=?' for key in fields) + ' WHERE ID=?',
                                     tuple(row[key] for key in fields) + (row['ID'],))
                finally:conn.close()

    def test_rollback_and_commit_recovery_at_every_publication_boundary(self):
        for phase, outcome in (('journal-ready', 'rolled-back'), ('before-commit', 'rolled-back'), ('after-commit', 'committed')):
            with self.subTest(phase=phase):
                source = self.root / phase / 'source/master.db';source.parent.mkdir(parents=True)
                conn = fixture(source);conn.close()
                authority = replace(self.authority, database=str(source))
                store = definitions.TagDeletionStore(authority, source.parent.parent / 'recovery')
                def fault(current):
                    if current == phase:raise KeyboardInterrupt('Simulated process boundary')
                with mock.patch.object(definitions, '_fault', side_effect=fault):
                    with self.assertRaises(KeyboardInterrupt):store.apply(self.request)
                fresh = definitions.TagDeletionStore(authority, store.root)
                self.assertEqual(fresh.recover(), outcome);self.assertFalse(fresh.journal.exists())
                fresh.apply(self.request)
                self.assertEqual(fresh.apply(self.request)['ID'], '30')

    def test_completed_receipt_replays_after_unrelated_edits_and_baseline_rotation(self):
        removed = self.store.apply(self.request)
        self.mutate("UPDATE djmdMyTag SET Name='Renamed category' WHERE ID='10'")
        self.mutate("UPDATE agentRegistry SET int_1=50 WHERE registry_id='localUpdateCount'")
        create_authority = definitions.TagDefinitionAuthority(str(self.source), 'synthetic-library',
            self.authority.schema_sha256, accepted=True, protocol_profile=definitions.NATIVE_CREATE_PROFILE.profile)
        create = definitions.TagDefinitionStore(create_authority, self.store.root)
        create.apply(definitions.request_for('synthetic-library', '10', 'category', 'Later tag'))
        before = self.state()
        self.assertEqual(self.store.apply(self.request), removed);self.assertEqual(self.state(), before)
        self.mutate("UPDATE djmdMyTag SET UUID='replaced-category' WHERE ID='10'")
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)

    def test_coherently_forged_before_json_is_bound_to_exact_encrypted_baseline(self):
        def fault(phase):
            if phase == 'journal-ready':raise OSError('stop before SQL')
        with mock.patch.object(definitions, '_fault', side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        record = json.loads(self.store.journal.read_bytes());record['category']['Name'] = 'Forged baseline'
        record['before_revision'] = definitions._revision(record['category'], record['siblings'], record['counter'], record['dependencies'])
        record['after_revision'] = definitions._revision(record['category'],
            definitions._deleted_siblings(record['siblings'], record['changes']), record['changes'][-1]['after'],
            {**record['dependencies'], 'links': []})
        self.store.journal.write_text(json.dumps(record))
        with mock.patch.object(self.store.lease, '_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.recover()
            connect.assert_not_called()
        self.assertTrue(self.store.journal.exists())

    def test_postcommit_recovery_refuses_deleted_uuid_reused_outside_original_category(self):
        def fault(phase):
            if phase == 'after-commit':raise OSError('stop before acknowledgement')
        with mock.patch.object(definitions, '_fault', side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        record = json.loads(self.store.journal.read_bytes())
        reused = {**record['changes'][-2]['before'], 'ID': '99', 'ParentID': '20'}
        self.mutate('INSERT INTO djmdMyTag(' + ','.join(writer._quote(key) for key in reused) + ') VALUES('
                    + ','.join('?' for _ in reused) + ')', tuple(reused.values()))
        self.assertEqual(self.query("SELECT ID,UUID,ParentID FROM djmdMyTag WHERE ID='99'"),
                         [('99', self.request['tag_uuid'], '20')])
        before = self.state()
        with self.assertRaises(writer.WriteProofError):self.store.recover()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_coherent_link_omission_cannot_detach_pending_plan_from_encrypted_before(self):
        self.mutate('INSERT INTO djmdSongMyTag VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            '00000000-0000-4000-8000-000000000040', '30', '1', None,
            '00000000-0000-4000-8000-000000000041', 0, 0, 0, 0, None, 40,
            '2026-11-11 00:00:00.000 +00:00', '2026-11-11 00:00:00.000 +00:00'))
        def fault(phase):
            if phase == 'journal-ready':raise OSError('stop before source SQL')
        with mock.patch.object(definitions, '_fault', side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        record = json.loads(self.store.journal.read_bytes())
        record['dependencies']['links'] = []
        record['changes'] = [change for change in record['changes'] if change['table'] != 'djmdSongMyTag']
        record['before_revision'] = definitions._revision(record['category'], record['siblings'],
                                                         record['counter'], record['dependencies'])
        record['after_revision'] = definitions._revision(record['category'],
            definitions._deleted_siblings(record['siblings'], record['changes']), record['changes'][-1]['after'],
            record['dependencies'])
        self.store.journal.write_text(json.dumps(record))
        with mock.patch.object(self.store.lease, '_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.recover()
            connect.assert_not_called()
        self.assertTrue(self.store.journal.exists())

    def test_fresh_closed_app_and_shared_foreign_journal_guards(self):
        before = self.state();self.closed.side_effect = writer.WriteProofError('native open')
        with self.assertRaisesRegex(writer.WriteProofError, 'native open'):self.store.apply(self.request)
        self.closed.side_effect = None
        self.store.journal.write_text('{"format":"soundbox-native-tag-definitions-v1"}')
        with mock.patch.object(self.store.lease, '_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
            connect.assert_not_called()
        self.store.journal.unlink()
        with self.store.lease._lock():
            other = definitions.TagDeletionStore(self.authority, self.store.root)
            with self.assertRaises(writer.WriteProofError):other.apply(self.request)
        self.assertEqual(self.state(), before)

    def test_authorizer_denies_song_rule_audio_mutations_and_trigger_side_effects(self):
        from sqlcipher3 import dbapi2 as sqlcipher
        for table, column in (('djmdSongMyTag', 'MyTagID'), ('djmdPlaylist', 'SmartList'),
                              ('djmdContent', 'FolderPath'), ('djmdMyTag', 'Name'), ('djmdMyTag', 'ParentID')):
            self.assertEqual(self.store._authorizer(sqlite3.SQLITE_UPDATE, table, column, 'main', None), sqlite3.SQLITE_DENY)
        self.mutate("CREATE TRIGGER forbidden AFTER DELETE ON djmdMyTag BEGIN UPDATE unknown_table SET opaque=X'09'; END")
        conn = writer._connect(self.source)
        authority = replace(self.authority, schema_sha256=assignments._schema(conn));conn.close()
        store = definitions.TagDeletionStore(authority, self.root / 'trigger-recovery');before = self.state()
        with self.assertRaises(sqlcipher.DatabaseError):store.apply(self.request)
        self.assertEqual(self.state(), before)

    def test_process_loss_reconciles_encrypted_hot_journal_and_committed_receipt(self):
        script = '''
import json,os,sys
from pathlib import Path
from soundbox import rekordbox_tag_definitions as d
raw=json.loads(sys.argv[1]);raw['pilot_operation_ids']=tuple(raw.get('pilot_operation_ids',()))
a=d.TagDeletionAuthority(**raw)
d._assert_closed=lambda:None
d._installed_version=lambda:a.application_version
original=d._Lease._connect
def connect(self):
    conn=original(self);conn.execute('PRAGMA cache_size=1');return conn
d._Lease._connect=connect
def fault(phase):
    if phase==sys.argv[4]:os._exit(86)
d._fault=fault
d.TagDeletionStore(a,Path(sys.argv[2])).apply(json.loads(sys.argv[3]))
'''
        for phase, outcome in (('before-commit', 'rolled-back'), ('after-commit', 'committed')):
            with self.subTest(phase=phase):
                source = self.root / ('process-' + phase) / 'source/master.db';source.parent.mkdir(parents=True)
                conn = fixture(source);conn.close()
                authority = replace(self.authority, database=str(source));recovery = source.parent.parent / 'recovery'
                result = subprocess.run([sys.executable, '-c', script, json.dumps(asdict(authority)), str(recovery),
                                         json.dumps(self.request), phase], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 86, result.stderr.decode())
                store = definitions.TagDeletionStore(authority, recovery)
                self.assertEqual(store.recover(), outcome);self.assertFalse(store.journal.exists())
                self.assertEqual(store.apply(self.request)['ID'], '30')


class SyncedDeletionTransactions(unittest.TestCase):
    state = DeletionTransactions.state
    query = DeletionTransactions.query
    mutate = DeletionTransactions.mutate

    def setUp(self):
        DeletionTransactions.setUp(self)
        self.mutate("UPDATE djmdMyTag SET rb_data_status=257,rb_local_synced=1,usn=13 WHERE ID='30'")
        for identity, flags in (('40', (0, 0, 0)), ('42', (0, 0, 1)), ('44', (2, 1, 1)), ('46', (0, 0, 1))):
            status, deleted, synced = flags
            self.mutate('INSERT INTO djmdSongMyTag VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                '00000000-0000-4000-8000-' + identity.zfill(12), '30', '1', None,
                '00000000-0000-4000-8000-' + str(int(identity) + 1).zfill(12), status, 0, deleted, synced,
                None, 40, '2026-11-11 00:00:00.000 +00:00', '2026-11-11 00:00:00.000 +00:00'))
        self.authority = replace(self.authority, protocol_profile=definitions.NATIVE_HISTORY_DELETE_PROFILE.profile)
        self.store = definitions.TagDeletionStore(self.authority, self.store.root)
        self.request['clocks'] = dict(target='2026-11-13 03:02:13.109 +00:00',
            siblings='2026-11-13 03:02:13.135 +00:00', associations='2026-11-13 03:02:12.992 +00:00')

    def test_native_row_lifetimes_and_three_independent_clocks_preserve_all_other_data(self):
        before = self.state()
        history = self.query("SELECT * FROM djmdSongMyTag WHERE ID LIKE '%000044'")
        self.store.apply(self.request)
        after = self.state()
        self.assertEqual({key for key in before['tables'] if before['tables'][key] != after['tables'][key]},
                         {'djmdMyTag', 'djmdSongMyTag', 'agentRegistry'})
        self.assertEqual(self.query("SELECT rb_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,updated_at FROM djmdMyTag WHERE ID='30'"),
            [(258, 1, 1, 13, 42, self.request['clocks']['target'])])
        self.assertEqual(self.query("SELECT ID,Seq,rb_local_usn,updated_at FROM djmdMyTag WHERE ID IN ('31','32') ORDER BY ID"),
            [('31', 3, 43, self.request['clocks']['siblings']), ('32', 4, 44, self.request['clocks']['siblings'])])
        self.assertEqual(self.query("SELECT count(*) FROM djmdSongMyTag WHERE ID LIKE '%000040'"), [(0,)])
        self.assertEqual(self.query("SELECT rb_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,updated_at FROM djmdSongMyTag WHERE ID LIKE '%000042' OR ID LIKE '%000046' ORDER BY ID"),
            [(2, 1, 1, None, 45, self.request['clocks']['associations'])] * 2)
        self.assertEqual(self.query("SELECT * FROM djmdSongMyTag WHERE ID LIKE '%000044'"), history)
        self.assertEqual(self.query("SELECT int_1,updated_at FROM agentRegistry WHERE registry_id='localUpdateCount'"), [(45, 'counter-time')])

    def test_synced_null_usn_definition_uses_data2_tombstone(self):
        self.mutate("UPDATE djmdMyTag SET rb_data_status=0,usn=NULL WHERE ID='30'")
        self.store.apply(self.request)
        self.assertEqual(self.query("SELECT rb_data_status,rb_local_deleted,usn FROM djmdMyTag WHERE ID='30'"), [(2, 1, None)])

    def test_historical_target_remote_counter_is_preserved_independently_of_local_progress(self):
        conn=writer._connect(self.source)
        try:category,siblings,counter,dependencies=self.store._snapshot(conn,self.request)
        finally:conn.close()
        original=self.store._plan(self.request,category,siblings,counter,
            definitions.NATIVE_HISTORY_DELETE_PROFILE,dependencies)
        for remote in (9999,2**63-1):
            changed=[{**row,'usn':remote} if row['ID']=='30' else deepcopy(row) for row in siblings]
            planned=self.store._plan(self.request,category,changed,counter,
                definitions.NATIVE_HISTORY_DELETE_PROFILE,dependencies)
            normalized=deepcopy(planned)
            for row in normalized:
                if row['table']=='djmdMyTag' and row['key']=='30':
                    self.assertEqual((row['before']['usn'],row['after']['usn']),(remote,remote))
                    row['before']['usn']=13;row['after']['usn']=13
            self.assertEqual(definitions._json(normalized),definitions._json(original))
        for remote in (True,-1,2**63,None):
            changed=[{**row,'usn':remote} if row['ID']=='30' else deepcopy(row) for row in siblings]
            with self.subTest(remote=remote),self.assertRaises(writer.WriteProofError):
                self.store._plan(self.request,category,changed,counter,
                    definitions.NATIVE_HISTORY_DELETE_PROFILE,dependencies)
        self.mutate("UPDATE djmdMyTag SET usn=? WHERE ID='30'",(2**63-1,))
        self.store.apply(self.request)
        self.assertEqual(self.query("SELECT rb_data_status,rb_local_deleted,usn,rb_local_usn FROM djmdMyTag WHERE ID='30'"),
                         [(258,1,2**63-1,42)])

    def test_absent_active_synced_batch_is_unproved_and_preserves_every_original_row(self):
        self.mutate("DELETE FROM djmdSongMyTag WHERE ID LIKE '%000042' OR ID LIKE '%000046'")
        before = self.state()
        with self.assertRaisesRegex(writer.WriteProofError, 'active synced assignments'):
            self.store.apply(self.request)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.store.journal.exists())

    def test_legacy_local_profile_cannot_widen_and_synced_requests_require_all_phase_clocks(self):
        legacy = definitions.TagDeletionStore(replace(self.authority,
            protocol_profile=definitions.NATIVE_DELETE_PROFILE.profile), self.store.root)
        before = self.state()
        with self.assertRaises(writer.WriteProofError):legacy.apply(self.request)
        self.assertEqual(self.state(), before)
        legacy_request = {key: value for key, value in self.request.items() if key != 'clocks'}
        with self.assertRaisesRegex(writer.WriteProofError, 'phase clocks'):self.store.apply(legacy_request)
        self.assertFalse(self.store.journal.exists())

    def test_unobserved_surviving_synced_sibling_is_guarded_even_under_combined_profile(self):
        self.mutate("UPDATE djmdMyTag SET rb_local_synced=1 WHERE ID='31'")
        before = self.state()
        with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.store.journal.exists())

    def test_unknown_target_or_link_envelope_cannot_enter_the_combined_profile(self):
        for sql in ("UPDATE djmdMyTag SET rb_data_status=1 WHERE ID='30'",
                    "UPDATE djmdMyTag SET rb_data_status=257,usn=NULL WHERE ID='30'",
                    "UPDATE djmdSongMyTag SET usn=7 WHERE ID LIKE '%000042'",
                    "UPDATE djmdSongMyTag SET rb_data_status=3 WHERE ID LIKE '%000044'"):
            conn = writer._connect(self.source)
            try:
                conn.execute('BEGIN');conn.execute(sql)
                category, siblings, counter, dependencies = self.store._snapshot(conn, self.request)
                with self.assertRaises(writer.WriteProofError):
                    self.store._plan(self.request, category, siblings, counter,
                                     definitions.NATIVE_HISTORY_DELETE_PROFILE, dependencies)
                conn.rollback()
            finally:conn.close()

    def test_permanent_replay_requires_exact_retained_tag_and_association_tombstones(self):
        removed = self.store.apply(self.request)
        before = self.state()
        self.assertEqual(self.store.apply(self.request), removed)
        self.assertEqual(self.state(), before)
        self.mutate("UPDATE djmdSongMyTag SET updated_at='unverified timestamp' WHERE ID LIKE '%000042'")
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)

    def test_postcommit_recovery_requires_the_whole_retained_tombstone_and_history(self):
        def fault(phase):
            if phase == 'after-commit':raise OSError('stop before acknowledgement')
        with mock.patch.object(definitions, '_fault', side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        self.mutate("UPDATE djmdMyTag SET updated_at='unexpected' WHERE ID='30'")
        before = self.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.recover()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.store.journal.exists())

    def test_synced_commit_recovery_and_replay_preserve_the_same_acknowledged_intent(self):
        def fault(phase):
            if phase == 'after-commit':raise OSError('stop before acknowledgement')
        with mock.patch.object(definitions, '_fault', side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        fresh = definitions.TagDeletionStore(self.authority, self.store.root)
        self.assertEqual(fresh.recover(), 'committed')
        self.assertEqual(fresh.apply(self.request)['ID'], '30')

    def test_process_exit_recovers_the_entire_synced_row_and_history_before_or_after_commit(self):
        script = '''
import json,os,sys
from pathlib import Path
from soundbox import rekordbox_tag_definitions as d
raw=json.loads(sys.argv[1]);raw['pilot_operation_ids']=tuple(raw.get('pilot_operation_ids',()))
a=d.TagDeletionAuthority(**raw)
d._assert_closed=lambda:None
d._installed_version=lambda:a.application_version
original=d._Lease._connect
def connect(self):
    conn=original(self);conn.execute('PRAGMA cache_size=1');return conn
d._Lease._connect=connect
def fault(phase):
    if phase==sys.argv[4]:os._exit(86)
d._fault=fault
d.TagDeletionStore(a,Path(sys.argv[2])).apply(json.loads(sys.argv[3]))
'''
        for phase, outcome in (('before-commit', 'rolled-back'), ('after-commit', 'committed')):
            with self.subTest(phase=phase):
                source = self.root / ('synced-process-' + phase) / 'source/master.db'
                source.parent.mkdir(parents=True);shutil.copyfile(self.source, source)
                authority = replace(self.authority, database=str(source))
                recovery = source.parent.parent / 'recovery'
                result = subprocess.run([sys.executable, '-c', script, json.dumps(asdict(authority)), str(recovery),
                                         json.dumps(self.request), phase], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 86, result.stderr.decode())
                fresh = definitions.TagDeletionStore(authority, recovery)
                self.assertEqual(fresh.recover(), outcome)
                self.assertEqual(fresh.apply(self.request)['ID'], '30')
                self.assertFalse(fresh.journal.exists())


if __name__ == '__main__':
    unittest.main()
