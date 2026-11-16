"""Definition mechanics on generated encrypted TEMP libraries, not acceptance."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import rekordbox_tag_definitions as definitions
from soundbox import rekordbox_tags as assignments
from soundbox import rekordbox_write as writer
from tests.test_rekordbox_write import _fixture as base_fixture


# Deliberately synthetic: no production profile or authority is provisioned.
PROFILE = definitions.DefinitionProtocol('synthetic-only-profile', 3)


def fixture(path):
    conn = base_fixture(path)
    conn.executescript('''
        DROP TABLE djmdMyTag;
        CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,Seq INTEGER,Name TEXT,
            Attribute INTEGER,ParentID TEXT,UUID TEXT,rb_data_status INTEGER,
            rb_local_data_status INTEGER,rb_local_deleted INTEGER,
            rb_local_synced INTEGER,usn INTEGER,rb_local_usn INTEGER,
            created_at TEXT,updated_at TEXT);
        INSERT INTO djmdMyTag VALUES('10',1,'Genre',1,'root','category',256,0,0,1,7,5,'old-created','old-updated');
        INSERT INTO djmdMyTag VALUES('11',1,'Dubstep',0,'10','tag-one',257,0,0,1,8,8,'old-created','old-updated');
        INSERT INTO djmdMyTag VALUES('12',2,'Riddim',0,'10','tag-two',257,0,0,1,9,9,'old-created','old-updated');
        INSERT INTO djmdMyTag VALUES('13',1000,'Deleted',0,'10','old-deleted-tag',258,0,1,1,10,10,'old-created','old-updated');
        INSERT INTO djmdMyTag VALUES('20',2,'Components',1,'root','other-category',256,0,0,1,11,11,'old-created','old-updated');
    ''')
    return conn


class DefinitionAdmission(unittest.TestCase):
    def test_provisional_profile_and_old_assignment_authorities_cannot_enable_source_creation(self):
        self.assertNotIn(definitions.PROVISIONAL_PROTOCOL.profile, definitions.VERIFIED_PROTOCOLS)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve();source = root / 'source/master.db';source.parent.mkdir()
            conn = fixture(source);schema = assignments._schema(conn);conn.close()
            authority = definitions.TagDefinitionAuthority(str(source), 'synthetic-library', schema,
                accepted=True, protocol_profile=definitions.PROVISIONAL_PROTOCOL.profile)
            path = root / 'authority.json';path.write_text(json.dumps(asdict(authority)))
            self.assertIsNone(definitions.load_authority(path, source, 'synthetic-library'))
            disabled = replace(authority, accepted=False, protocol_profile=definitions.NATIVE_CREATE_PROFILE.profile)
            path.write_text(json.dumps(asdict(disabled)))
            self.assertIsNone(definitions.load_authority(path, source, 'synthetic-library'))
            disabled_store = definitions.TagDefinitionStore(disabled, root / 'unaccepted-library')
            with mock.patch.object(disabled_store.lease,'_connect') as connect:
                with self.assertRaises(writer.WriteProofError):disabled_store.check_ready()
                connect.assert_not_called()
            store = definitions.TagDefinitionStore(authority, root / 'recovery')
            with mock.patch.object(store.lease, '_connect', side_effect=AssertionError('Disabled source connection')):
                with self.assertRaises(writer.WriteProofError):store.check_ready()
                with self.assertRaises(writer.WriteProofError):
                    store.apply(definitions.request_for('synthetic-library','10','category','New tag'))
            old = assignments.TagWriteAuthority(str(source), 'synthetic-library', schema)
            path.write_text(json.dumps({**asdict(old), 'accepted': True}))
            self.assertIsNone(definitions.load_authority(path, source, 'synthetic-library'))
            with self.assertRaises(writer.WriteProofError):definitions.TagDefinitionStore(old, root / 'old-domain')

    def test_captured_request_has_stable_explicit_ids_clock_and_strict_name(self):
        now = datetime(2026, 11, 9, 19, 10, 37, 420000, tzinfo=timezone.utc)
        request = definitions.request_for('library', '1', '1', 'New Tag', now=now)
        self.assertEqual(request['timestamp'], '2026-11-09 19:10:37.420 +00:00')
        self.assertEqual(request['final_timestamp'], request['timestamp'])
        self.assertEqual(definitions.validate_request(json.loads(json.dumps(request))), request)
        for change in ({'name': ''}, {'name': ' padded '}, {'name': 'unsafe\nname'},
                       {'tag_id': '01'}, {'tag_uuid': 'invalid'}, {'operation_id': '../escape'},
                       {'timestamp': '2026-11-09 19:10:37'}, {'library_id': ''},
                       {'final_timestamp':'2026-11-09 19:10:36.420 +00:00'}):
            with self.subTest(change=change), self.assertRaises(writer.WriteProofError):
                definitions.validate_request({**request, **change})
        with self.assertRaises(writer.WriteProofError):
            definitions.request_for('library', '1', '1', 'Name', now=now.replace(tzinfo=None))


class DefinitionTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve();self.source = self.root / 'source/master.db'
        self.source.parent.mkdir()
        conn = fixture(self.source);schema = assignments._schema(conn);conn.close()
        self.enterContext(mock.patch.object(definitions, 'VERIFIED_PROTOCOLS', {PROFILE.profile: PROFILE}))
        self.closed = self.enterContext(mock.patch.object(definitions, '_assert_closed'))
        self.enterContext(mock.patch.object(definitions, '_installed_version', return_value=assignments.NATIVE_VERSION))
        self.authority = definitions.TagDefinitionAuthority(str(self.source), 'synthetic-library', schema,
            accepted=True, protocol_profile=PROFILE.profile)
        self.store = definitions.TagDefinitionStore(self.authority, self.root / 'recovery')
        self.request = definitions.request_for('synthetic-library', '10', 'category', 'Synthetic new tag',
            now=datetime(2026,11,9,19,10,37,420000,tzinfo=timezone.utc))

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

    def test_only_one_leaf_and_counter_change_with_exact_frozen_row_and_encrypted_backup(self):
        before = self.state();category = self.query("SELECT * FROM djmdMyTag WHERE ID='10'")
        row = self.store.apply(self.request);after = self.state()
        self.assertEqual({name for name in before['tables'] if before['tables'][name] != after['tables'][name]},
                         {'djmdMyTag', 'agentRegistry'})
        self.assertEqual(self.query("SELECT * FROM djmdMyTag WHERE ID='10'"), category)
        self.assertEqual((row['ID'],row['UUID'],row['Seq'],row['Name'],row['ParentID']),
                         (self.request['tag_id'],self.request['tag_uuid'],3,'Synthetic new tag','10'))
        self.assertEqual([row[key] for key in ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced','usn')],
                         [0,0,0,0,None])
        self.assertEqual(row['rb_local_usn'],44)  # Synthetic profile, not native acceptance.
        self.assertEqual((row['created_at'],row['updated_at']), (self.request['timestamp'],self.request['timestamp']))
        self.assertEqual(self.query("SELECT int_1,updated_at,other FROM agentRegistry WHERE registry_id='localUpdateCount'"),
                         [(44,'counter-time','keep')])
        self.assertNotEqual((self.store.root/'baseline.db').read_bytes()[:16], b'SQLite format 3\0')
        self.assertFalse(self.store.journal.exists())

    def test_remote_category_counter_does_not_constrain_the_independent_local_writer_clock(self):
        # The retained native built-in parent has status256/synced1, remote81
        # and local77: these are independent sequences, not one shared clock.
        conn=writer._connect(self.source)
        try:category,siblings,counter=self.store._snapshot(conn,self.request)
        finally:conn.close()
        native_parent={**category,'ID':'1','UUID':'1','usn':81,'rb_local_usn':77}
        counter={**counter,'int_1':80}
        siblings=[{**row,'ParentID':'1'} for row in siblings]
        request={**self.request,'category_id':'1','category_uuid':'1'}
        before=deepcopy(native_parent)
        changes=definitions.plan_create(request,native_parent,siblings,counter,definitions.NATIVE_CREATE_PROFILE)
        self.assertEqual(native_parent,before)
        self.assertEqual([row['table'] for row in changes],['djmdMyTag','agentRegistry'])
        self.assertEqual(changes[0]['after']['ParentID'],'1')
        self.assertEqual(changes[1]['after']['int_1'],82)
        with self.assertRaises(writer.ConflictError):
            definitions.plan_create(request,{**native_parent,'UUID':'replacement'},siblings,counter,
                                    definitions.NATIVE_CREATE_PROFILE)
        # Actual encrypted publication retains the immutable category byte for
        # byte even when its remote USN exceeds this fixture's local counter.
        self.mutate("UPDATE djmdMyTag SET usn=? WHERE ID='10'",(2**63-1,))
        before=self.query("SELECT * FROM djmdMyTag WHERE ID='10'")
        self.store.apply(self.request)
        self.assertEqual(self.query("SELECT * FROM djmdMyTag WHERE ID='10'"),before)

    def test_changed_remote_category_clock_still_blocks_a_prepared_transaction(self):
        self.mutate("UPDATE djmdMyTag SET usn=? WHERE ID='10'",(2**63-1,))
        def fault(phase):
            if phase=='journal-ready':raise OSError('stop before source SQL')
        with mock.patch.object(definitions,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        self.mutate("UPDATE djmdMyTag SET usn=81 WHERE ID='10'")
        before=self.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.recover()
        self.assertEqual(self.state(),before)
        self.assertTrue(self.store.journal.exists())

    def test_proven_constructor_and_naming_composition_preserves_distinct_frozen_clocks(self):
        conn = writer._connect(self.source)
        try:category,siblings,counter = self.store._snapshot(conn,self.request)
        finally:conn.close()
        created = datetime(2026,11,10,3,49,24,407000,tzinfo=timezone.utc)
        final = datetime(2026,11,10,4,31,54,597000,tzinfo=timezone.utc)
        request = definitions.request_for('synthetic-library','10','category','SoundBox Verify Tag',
                                          now=created,final_now=final)
        planned = definitions.plan_create(request,category,siblings,counter,definitions.NATIVE_CREATE_PROFILE)
        self.assertEqual(planned[0]['after'],dict(ID=request['tag_id'],Seq=3,Name='SoundBox Verify Tag',
            Attribute=0,ParentID='10',UUID=request['tag_uuid'],rb_data_status=0,rb_local_data_status=0,
            rb_local_deleted=0,rb_local_synced=0,usn=None,rb_local_usn=43,
            created_at='2026-11-10 03:49:24.407 +00:00',updated_at='2026-11-10 04:31:54.597 +00:00'))
        self.assertEqual(planned[1]['after'],{**counter,'int_1':43})
        default = {**request,'name':'My Comment'}
        no_rename = definitions.plan_create(default,category,siblings,counter,definitions.NATIVE_CREATE_PROFILE)
        self.assertEqual(no_rename[1]['after'],{**counter,'int_1':42})
        self.assertEqual(no_rename[0]['after']['created_at'],no_rename[0]['after']['updated_at'])
        self.assertEqual(no_rename[0]['after']['created_at'],request['timestamp'])

    def test_composed_profile_copies_publish_only_final_new_rows_and_counter_effects(self):
        created = datetime(2026,11,10,3,49,24,407000,tzinfo=timezone.utc)
        final = datetime(2026,11,10,4,31,54,597000,tzinfo=timezone.utc)
        authority = replace(self.authority,protocol_profile=definitions.NATIVE_CREATE_PROFILE.profile)
        with mock.patch.dict(definitions.VERIFIED_PROTOCOLS,
                             {definitions.NATIVE_CREATE_PROFILE.profile:definitions.NATIVE_CREATE_PROFILE}):
            store = definitions.TagDefinitionStore(authority,self.store.root)
            before = self.state()
            default = definitions.request_for('synthetic-library','10','category','My Comment',now=created,final_now=final)
            first = store.apply(default)
            self.assertEqual(first['rb_local_usn'],42)
            self.assertEqual(first['updated_at'],first['created_at'])
            named = definitions.request_for('synthetic-library','10','category','Named copy proof',now=created,final_now=final)
            second = store.apply(named)
            self.assertEqual(second['rb_local_usn'],44)
            self.assertEqual(second['created_at'],named['timestamp'])
            self.assertEqual(second['updated_at'],named['final_timestamp'])
            self.assertEqual(store.apply(named),second)
            after = self.state()
            self.assertEqual({table for table in before['tables'] if before['tables'][table]!=after['tables'][table]},
                             {'djmdMyTag','agentRegistry'})

    def test_replay_survives_unrelated_later_definitions_and_a_category_rename(self):
        expected = self.store.apply(self.request)
        self.store.apply(definitions.request_for('synthetic-library','10','category','Another synthetic tag'))
        self.mutate("UPDATE djmdMyTag SET Name='Renamed category' WHERE ID='10'")
        before = self.state();fresh = definitions.TagDefinitionStore(self.authority, self.store.root)
        self.assertEqual(fresh.apply(self.request), expected);self.assertEqual(self.state(), before)

    def test_replay_refuses_replaced_category_without_duplicate_creation(self):
        self.store.apply(self.request);self.mutate("UPDATE djmdMyTag SET UUID='replacement' WHERE ID='10'")
        before = self.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.query('SELECT count(*) FROM djmdMyTag WHERE ID=?',(self.request['tag_id'],)),[(1,)])

    def test_replay_does_not_reinterpret_an_unknown_category_profile(self):
        self.store.apply(self.request)
        self.mutate("UPDATE djmdMyTag SET rb_local_data_status=1 WHERE ID='10'")
        before = self.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
        self.assertEqual(self.state(),before)

    def test_category_identity_name_and_global_retained_id_uuid_collisions_fail_closed(self):
        for change in ({'category_id':'20'}, {'category_uuid':'wrong'}, {'name':'Dubstep'},
                       {'tag_id':'13'}, {'tag_uuid':self.request['tag_uuid']}):
            request = {**self.request, **change}
            if change == {'tag_uuid':self.request['tag_uuid']}:
                self.mutate('UPDATE djmdMyTag SET UUID=? WHERE ID=?',(request['tag_uuid'],'13'))
            before = self.state()
            with self.subTest(change=change), self.assertRaises(writer.WriteProofError):self.store.apply(request)
            self.assertEqual(self.state(), before)

    def test_category_historical_read_profile_is_typed_and_never_widened_to_unknown_states(self):
        conn = writer._connect(self.source)
        try:category,siblings,counter = self.store._snapshot(conn,self.request)
        finally:conn.close()
        for field,value in (('Attribute',0),('ParentID','elsewhere'),('rb_local_deleted',1),
                            ('rb_data_status',0),('rb_local_data_status',1),('rb_local_synced',0),
                            ('usn',None),('usn',True),('usn',-1),('usn',2**63),
                            ('rb_local_usn',999),('UUID','replacement')):
            changed = {**category,field:value}
            with self.subTest(field=field,value=value), self.assertRaises(writer.WriteProofError):
                definitions.plan_create(self.request,changed,deepcopy(siblings),counter,PROFILE)
        siblings[1]['Seq'] = siblings[0]['Seq']
        with self.assertRaises(writer.ConflictError):definitions.plan_create(self.request,category,siblings,counter,PROFILE)

    def test_absent_capability_wrong_profile_version_schema_and_pilot_scope_never_write(self):
        before = self.state()
        for index,authority in enumerate((replace(self.authority,accepted=False),
                replace(self.authority,protocol_profile='unknown'),
                replace(self.authority,pilot_operation_ids=('a'*32,)),replace(self.authority,schema_sha256='a'*64))):
            store = definitions.TagDefinitionStore(authority,self.root/f'scope-{index}')
            with self.subTest(authority=authority), self.assertRaises(writer.WriteProofError):store.apply(self.request)
        with mock.patch.object(definitions,'_installed_version',return_value='unknown'):
            with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
        self.assertEqual(self.state(),before)

    def test_running_native_app_and_backup_failure_prevent_source_mutation(self):
        before = self.state();self.closed.side_effect = writer.WriteProofError('native app is open')
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
            connect.assert_not_called()
        self.closed.side_effect = None
        with mock.patch.object(self.store.lease,'_backup',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.store.apply(self.request)
        self.assertEqual(self.state(),before)

    def test_category_inventory_bound_refuses_before_publication(self):
        before = self.state()
        for limit in (2,3):
            with mock.patch.object(definitions,'_MAX_SIBLINGS',limit):
                with self.assertRaises(writer.ConflictError):self.store.apply(self.request)
            self.assertEqual(self.state(),before)
            self.assertFalse(self.store.journal.exists())

    def test_unexpected_postcrash_source_state_is_preserved_without_restoration(self):
        def fault(phase):
            if phase=='after-commit':raise OSError('unconfirmed copy commit')
        with mock.patch.object(definitions,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        self.mutate('UPDATE djmdMyTag SET Name=? WHERE ID=?',('Foreign edit',self.request['tag_id']))
        before = self.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.recover()
        self.assertEqual(self.state(),before);self.assertTrue(self.store.journal.exists())

    def test_source_replacement_is_rejected_before_a_sql_connection(self):
        def fault(phase):
            if phase=='journal-ready':raise OSError('unstarted copy transaction')
        with mock.patch.object(definitions,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        replacement = self.source.with_name('replacement.db');conn = fixture(replacement);conn.close()
        replacement.replace(self.source);before = self.source.read_bytes()
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.check_ready()
            connect.assert_not_called()
        self.assertEqual(self.source.read_bytes(),before);self.assertTrue(self.store.journal.exists())

    def test_precommit_rollback_and_postcommit_ack_failure_reconcile_without_new_ids(self):
        for phase,outcome in (('before-commit','rolled-back'),('after-commit','committed')):
            request = definitions.request_for('synthetic-library','10','category',f'{phase} synthetic')
            before = self.state()
            def fault(value):
                if value==phase:raise OSError('synthetic interrupted transaction')
            with mock.patch.object(definitions,'_fault',side_effect=fault):
                with self.assertRaises(OSError):self.store.apply(request)
            self.assertTrue(self.store.journal.exists());self.assertEqual(self.store.recover(),outcome)
            if outcome=='rolled-back':self.assertEqual(self.state(),before)
            expected = self.store.apply(request);self.assertEqual(expected['ID'],request['tag_id'])
            self.assertEqual(self.query('SELECT count(*) FROM djmdMyTag WHERE ID=?',(request['tag_id'],)),[(1,)])

    def test_tampered_journal_and_backup_block_before_connect(self):
        def fault(phase):
            if phase=='journal-ready':raise OSError('stop before SQL')
        with mock.patch.object(definitions,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        valid = self.store.journal.read_bytes()
        record = json.loads(valid);record['changes'][0]['after']['Name']='Injected name'
        self.store.journal.write_text(json.dumps(record))
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.check_ready()
            connect.assert_not_called()
        self.store.journal.write_bytes(valid);(self.store.root/'baseline.db').write_bytes(b'damaged encrypted recovery')
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.check_ready()
            connect.assert_not_called()

    def test_coherent_json_before_tampering_is_rejected_by_the_independent_encrypted_baseline(self):
        def fault(phase):
            if phase=='journal-ready':raise OSError('stop before source SQL')
        with mock.patch.object(definitions,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        with mock.patch.object(definitions,'_connect_copy',wraps=definitions._connect_copy) as copied:
            self.store._record();self.store._record()
            self.assertEqual(copied.call_count,1)
        record = json.loads(self.store.journal.read_bytes())
        record['category']['Name'] = 'Coherent but fabricated baseline category'
        record['before_revision'] = definitions._revision(record['category'],record['siblings'],record['counter'])
        record['after_revision'] = definitions._revision(record['category'],
            [*record['siblings'],record['changes'][0]['after']],record['changes'][1]['after'])
        self.store.journal.write_text(json.dumps(record));before = self.state()
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.check_ready()
            connect.assert_not_called()
        self.assertEqual(self.state(),before);self.assertTrue(self.store.journal.exists())

    def test_another_domain_journal_and_shared_writer_lock_are_never_bypassed(self):
        self.store.journal.write_text('{"format":"soundbox-native-mytag-v1"}')
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
            connect.assert_not_called()
        self.store.journal.unlink()
        with self.store.lease._lock():
            other = definitions.TagDefinitionStore(self.authority,self.store.root)
            with self.assertRaises(writer.WriteProofError):other.apply(self.request)

    def test_reused_backup_lease_does_not_expose_cue_authority(self):
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.WriteProofError):self.store.lease.apply(object())
            with self.assertRaises(writer.WriteProofError):self.store.lease.snapshot()
            connect.assert_not_called()

    def test_authorizer_blocks_existing_row_updates_deletes_and_trigger_side_effects(self):
        from sqlcipher3 import dbapi2 as sqlcipher
        for action in (sqlite3.SQLITE_DELETE,sqlite3.SQLITE_UPDATE):
            self.assertEqual(definitions.TagDefinitionStore._authorizer(action,'djmdMyTag','Name','main',None),sqlite3.SQLITE_DENY)
        self.mutate("CREATE TRIGGER forbidden AFTER INSERT ON djmdMyTag BEGIN UPDATE unknown_table SET opaque=X'09'; END")
        conn = writer._connect(self.source);authority = replace(self.authority,schema_sha256=assignments._schema(conn));conn.close()
        store = definitions.TagDefinitionStore(authority,self.root/'trigger-recovery');before = self.state()
        with self.assertRaisesRegex(sqlcipher.DatabaseError,'authorized'):store.apply(self.request)
        self.assertEqual(self.state(),before)

    def test_marked_authority_load_binds_only_exact_source_library_and_verified_profile(self):
        path = self.root/'authority.json';path.write_text(json.dumps(asdict(self.authority)))
        self.assertEqual(definitions.load_authority(path,self.source,'synthetic-library'),self.authority)
        self.assertIsNone(definitions.load_authority(path,self.source,'other'))
        self.assertIsNone(definitions.load_authority(path,self.source.with_name('other.db'),'synthetic-library'))

    def test_real_process_loss_reconciles_actual_encrypted_sqlite_journals(self):
        script = '''
import json,os,sys
from pathlib import Path
from soundbox import rekordbox_tag_definitions as d
raw=json.loads(sys.argv[1]);raw['pilot_operation_ids']=tuple(raw.get('pilot_operation_ids',()))
a=d.TagDefinitionAuthority(**raw)
d.VERIFIED_PROTOCOLS={'synthetic-only-profile':d.DefinitionProtocol('synthetic-only-profile',3)}
d._assert_closed=lambda:None
d._installed_version=lambda:a.application_version
original=d._Lease._connect
def connect(self):
    conn=original(self);conn.execute('PRAGMA cache_size=1');return conn
d._Lease._connect=connect
def fault(phase):
    if phase==sys.argv[4]:os._exit(86)
d._fault=fault
d.TagDefinitionStore(a,Path(sys.argv[2])).apply(json.loads(sys.argv[3]))
'''
        for phase,outcome in (('before-commit','rolled-back'),('after-commit','committed')):
            with self.subTest(phase=phase):
                folder = self.root/phase;source = folder/'source/master.db';source.parent.mkdir(parents=True)
                conn = fixture(source);conn.close()
                authority = replace(self.authority,database=str(source));recovery = folder/'recovery'
                result = subprocess.run([sys.executable,'-c',script,json.dumps(asdict(authority)),str(recovery),
                    json.dumps(self.request),phase],capture_output=True,timeout=30)
                self.assertEqual(result.returncode,86,result.stderr.decode())
                store = definitions.TagDefinitionStore(authority,recovery)
                self.assertEqual(store.recover(),outcome);self.assertFalse(store.journal.exists())
