"""Proposed existing-tag rename mechanics on generated encrypted TEMP data only."""
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from sqlcipher3 import dbapi2 as sqlcipher

from soundbox import rekordbox_tag_definitions as definitions,rekordbox_tag_order as order
from soundbox import rekordbox_tag_rename as rename,rekordbox_write as writer
from tests import test_rekordbox_tag_definitions as fixtures


UUID='00000000-0000-4000-8000-000000000011'
CLOCK=datetime(2026,11,19,23,58,1,123000,tzinfo=timezone.utc)


class TagRenameMechanics(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source=self.root/'source/master.db';self.source.parent.mkdir()
        conn=fixtures.fixture(self.source)
        conn.execute('UPDATE djmdMyTag SET UUID=? WHERE ID=?',(UUID,'11'))
        self.rule='<NODE Id="1000"><CONDITION PropertyName="myTag" Operator="8" ValueLeft="11"/></NODE>'
        conn.execute('UPDATE djmdPlaylist SET SmartList=? WHERE ID=?',(self.rule,'playlist'))
        self.schema=definitions._schema(conn);conn.close()
        self.xml=self.source.with_name('masterPlaylists6.xml');self.xml.write_bytes(b'<retained-opaque-xml/>')
        self.authority=rename.TagRenameAuthority(str(self.source),'synthetic-library',self.schema,
            accepted=True,protocol_profile=rename.PROVISIONAL_PROTOCOL.profile)
        self.store=rename.TagRenameStore(self.authority,self.root/'recovery')
        self.request=rename.request_for('synthetic-library','10','category','11',UUID,'Dubstep','Renamed',now=CLOCK)
        self.enterContext(mock.patch.object(definitions,'_assert_closed'))
        self.enterContext(mock.patch.object(definitions,'_installed_version',return_value=rename.NATIVE_VERSION))

    def proposed(self):
        return mock.patch.dict(rename.VERIFIED_PROTOCOLS,{rename.PROVISIONAL_PROTOCOL.profile:rename.PROVISIONAL_PROTOCOL})

    def snapshot(self):
        conn=writer._connect(self.source,readonly=True)
        try:
            return writer._state(conn),{table:writer._rows(conn,table) for table in
                ('djmdMyTag','agentRegistry','djmdSongMyTag','djmdPlaylist','djmdContent','djmdCue')}
        finally:conn.close()

    def test_proposed_profile_and_other_domain_authorities_never_enable_production_rename(self):
        self.assertNotIn(rename.PROVISIONAL_PROTOCOL.profile,rename.VERIFIED_PROTOCOLS)
        path=self.root/'authority.json';path.write_text(json.dumps(asdict(self.authority)))
        self.assertIsNone(rename.load_authority(path,self.source,'synthetic-library'))
        with mock.patch.object(self.store.lease,'_connect') as connect:
            with self.assertRaises(writer.WriteProofError):self.store.apply(self.request)
            with self.assertRaises(writer.WriteProofError):self.store.recover()
            connect.assert_not_called()
        for cls,profile in ((definitions.TagDefinitionAuthority,definitions.NATIVE_CREATE_PROFILE.profile),
                (definitions.TagDeletionAuthority,definitions.NATIVE_DELETE_PROFILE.profile),
                (order.TagOrderAuthority,order.NATIVE_ORDER_PROFILE.profile)):
            authority=cls(str(self.source),'synthetic-library',self.schema,accepted=True,protocol_profile=profile)
            path.write_text(json.dumps(asdict(authority)))
            self.assertIsNone(rename.load_authority(path,self.source,'synthetic-library'))
            with self.assertRaises(writer.WriteProofError):rename.TagRenameStore(authority,self.root/'foreign')

    def test_request_freezes_old_new_labels_id_uuid_category_and_independent_clock(self):
        self.assertEqual(rename.validate_request(json.loads(json.dumps(self.request))),self.request)
        self.assertEqual(self.request['timestamp'],'2026-11-19 23:58:01.123 +00:00')
        for changes in ({'new_name':'Dubstep'},{'new_name':' padded '},{'new_name':'bad\nname'},
                {'name':''},{'tag_id':'011'},{'tag_uuid':'invalid'},{'action':'delete'},
                {'operation_id':'../outside'},{'timestamp':'2026-11-19 23:58:01'}, {'category_uuid':''}):
            with self.subTest(changes=changes),self.assertRaises(writer.WriteProofError):
                rename.validate_request({**self.request,**changes})
        with self.assertRaises(writer.WriteProofError):
            rename.request_for('lib','10','category','11',UUID,'Old','New',now=CLOCK.replace(tzinfo=None))

    def test_copy_only_proposal_preserves_all_target_fields_except_name_update_clock_and_local_usn(self):
        _state,rows=self.snapshot();category=next(row for row in rows['djmdMyTag'] if row['ID']=='10')
        siblings=[row for row in rows['djmdMyTag'] if row['ParentID']=='10'];original=deepcopy(siblings)
        counter=next(row for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        for status,synced,usn in ((257,1,8),(0,1,None),(0,0,None)):
            proposed=deepcopy(siblings);target=next(row for row in proposed if row['ID']=='11')
            target.update(rb_data_status=status,rb_local_synced=synced,usn=usn)
            changes=rename.plan_rename(self.request,category,proposed,counter,rename.PROVISIONAL_PROTOCOL)
            self.assertEqual(changes[0]['after'],{**target,'Name':'Renamed','updated_at':self.request['timestamp'],'rb_local_usn':42})
            self.assertEqual(changes[1]['after'],{**counter,'int_1':42})
            self.assertEqual(siblings,original)

    def test_encrypted_transaction_and_repeat_preserve_assignments_rules_other_tags_registry_xml_and_audio_metadata(self):
        before,rows=self.snapshot();request=deepcopy(self.request)
        with self.proposed():result=self.store.apply(self.request)
        after,updated=self.snapshot()
        self.assertEqual(self.request,request);self.assertEqual((result['ID'],result['UUID'],result['Name']),('11',UUID,'Renamed'))
        self.assertEqual({table for table in before['tables'] if before['tables'][table]!=after['tables'][table]},
                         {'djmdMyTag','agentRegistry'})
        self.assertEqual((before['schema'],before['metadata']),(after['schema'],after['metadata']))
        self.assertEqual([row for row in updated['djmdMyTag'] if row['ID']!='11'],[row for row in rows['djmdMyTag'] if row['ID']!='11'])
        for table in ('djmdSongMyTag','djmdPlaylist','djmdContent','djmdCue'):self.assertEqual(updated[table],rows[table],table)
        self.assertEqual(updated['djmdPlaylist'][0]['SmartList'],self.rule)
        for old,new in zip(rows['agentRegistry'],updated['agentRegistry'],strict=True):
            self.assertEqual(new,{**old,'int_1':42} if old['registry_id']=='localUpdateCount' else old)
        self.assertEqual(self.xml.read_bytes(),b'<retained-opaque-xml/>')
        with self.proposed():self.assertEqual(self.store.apply(self.request),result)
        self.assertEqual(self.snapshot()[0],after)

    def test_stale_name_id_uuid_category_and_duplicate_active_label_refuse_without_source_changes(self):
        for changes in ({'name':'Other'},{'tag_uuid':'00000000-0000-4000-8000-000000000099'},
                {'category_uuid':'replaced'},{'category_id':'20','category_uuid':'other-category'},{'new_name':'rIdDiM'}):
            before=self.snapshot()[0]
            with self.subTest(changes=changes),self.proposed(),self.assertRaises(writer.WriteProofError):
                self.store.apply({**self.request,**changes})
            self.assertEqual(self.snapshot()[0],before)

    def test_unknown_sync_flags_cloud_deleted_and_malformed_counter_refuse(self):
        _state,rows=self.snapshot();category=next(row for row in rows['djmdMyTag'] if row['ID']=='10')
        siblings=[row for row in rows['djmdMyTag'] if row['ParentID']=='10']
        counter=next(row for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        for changes in ({'rb_local_deleted':1},{'rb_data_status':256},{'rb_local_data_status':1},
                {'rb_local_synced':2},{'usn':None},{'rb_local_usn':42}):
            altered=deepcopy(siblings);next(row for row in altered if row['ID']=='11').update(changes)
            with self.subTest(changes=changes),self.assertRaises(writer.WriteProofError):
                rename.plan_rename(self.request,category,altered,counter,rename.PROVISIONAL_PROTOCOL)
        for value in (True,-1,2**63-1):
            with self.assertRaises(writer.WriteProofError):
                rename.plan_rename(self.request,category,siblings,{**counter,'int_1':value},rename.PROVISIONAL_PROTOCOL)

    def test_duplicate_uuid_in_another_category_is_not_adopted(self):
        conn=writer._connect(self.source)
        conn.execute("UPDATE djmdMyTag SET ParentID='20',UUID=? WHERE ID='12'",(UUID,));conn.close()
        before=self.snapshot()[0]
        with self.proposed(),self.assertRaisesRegex(writer.WriteProofError,'ambiguous'):
            self.store.apply(self.request)
        self.assertEqual(self.snapshot()[0],before)

    def test_same_label_in_another_category_and_deleted_history_are_preserved(self):
        conn=writer._connect(self.source)
        conn.execute("UPDATE djmdMyTag SET Name='Renamed',ParentID='20' WHERE ID='12'")
        conn.execute("UPDATE djmdMyTag SET Name='Renamed' WHERE ID='13'");conn.close()
        _before,rows=self.snapshot()
        with self.proposed():self.store.apply(self.request)
        self.assertEqual([row for row in self.snapshot()[1]['djmdMyTag'] if row['ID']!='11'],
                         [row for row in rows['djmdMyTag'] if row['ID']!='11'])

    def test_authorizer_denies_insert_delete_other_label_domains_and_sync_or_order_columns(self):
        conn=writer._connect(self.source)
        try:
            for sql in ("UPDATE djmdMyTag SET Seq=99 WHERE ID='11'","DELETE FROM djmdMyTag WHERE ID='11'",
                    "UPDATE djmdMyTag SET usn=1 WHERE ID='11'","UPDATE djmdSongMyTag SET MyTagID='12'",
                    "UPDATE djmdPlaylist SET Name='Changed'","UPDATE agentRegistry SET updated_at='Changed'"):
                conn.set_authorizer(self.store._authorizer)
                with self.subTest(sql=sql),self.assertRaisesRegex(sqlcipher.DatabaseError,'authorized'):conn.execute(sql)
                conn.set_authorizer(None)
        finally:conn.close()

    def test_interrupted_boundaries_recover_and_replay_exact_captured_label_once(self):
        for stage,outcome in (('journal-ready','rolled-back'),('before-commit','rolled-back'),('after-commit','committed')):
            with self.subTest(stage=stage):
                store=rename.TagRenameStore(self.authority,self.root/('recovery-'+stage))
                # Each independent case returns its target to the same synthetic
                # BEFORE before exercising the next ordinary transaction root.
                conn=writer._connect(self.source)
                conn.execute("UPDATE djmdMyTag SET Name='Dubstep',rb_local_usn=8,updated_at='old-updated' WHERE ID='11'")
                conn.execute("UPDATE agentRegistry SET int_1=41 WHERE registry_id='localUpdateCount'");conn.close()
                def interrupt(at):
                    if at==stage:raise RuntimeError('Interrupted '+stage)
                with self.proposed(),mock.patch.object(definitions,'_fault',side_effect=interrupt),self.assertRaises(RuntimeError):
                    store.apply(self.request)
                with self.proposed():
                    self.assertEqual(store.recover(),outcome)
                    result=store.apply(self.request)
                self.assertEqual((result['Name'],result['rb_local_usn']),('Renamed',42))
                counter=next(row for row in self.snapshot()[1]['agentRegistry'] if row['registry_id']=='localUpdateCount')
                self.assertEqual(counter['int_1'],42)

    def test_reviewed_stale_baseline_never_recovers_or_publishes_and_other_pilot_ids_refuse(self):
        before=self.snapshot()[0];stale=deepcopy(before);stale['metadata']={'other':'state'}
        with self.proposed(),mock.patch.object(self.store,'_recover') as recover:
            with self.assertRaises(writer.WriteProofError):self.store.apply(self.request,expected_state=stale)
            recover.assert_not_called()
        bound=rename.TagRenameStore(replace(self.authority,pilot_operation_ids=('a'*32,)),self.root/'scoped')
        with self.proposed(),self.assertRaises(writer.WriteProofError):bound.apply(self.request)
        self.assertEqual(self.snapshot()[0],before)

    def test_replay_never_overwrites_a_later_external_label_or_reused_operation(self):
        with self.proposed():self.store.apply(self.request)
        conn=writer._connect(self.source);conn.execute("UPDATE djmdMyTag SET Name='External' WHERE ID='11'");conn.close()
        before=self.snapshot()[0]
        with self.proposed():
            with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
            with self.assertRaises(writer.RecoveryRequired):self.store.apply({**self.request,'new_name':'Another'})
        self.assertEqual(self.snapshot()[0],before)
