"""Independently gated smart metadata candidates over encrypted TEMP DB/XML."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

from soundbox import rekordbox_playlist_store as store
from soundbox import rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect, _rows, _state, WriteProofError, ConflictError, RecoveryRequired
from tests import test_smart_playlist_deletion as fixture


class SmartMetadataCandidate(unittest.TestCase):
    def setUp(self):
        self.case=fixture.SmartPlaylistDeletion();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.database=self.case.database;self.path=self.case.xml
        conn=_connect(self.database)
        try:
            template=next(row for row in _rows(conn,'djmdPlaylist') if row['ID']=='1000')
            folder={**template,'ID':'4000','UUID':fixture.native_uuid(4000),'Attribute':1,'Seq':1,'Name':'Proof folder'}
            columns=list(folder)
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [folder[key] for key in columns])
            conn.execute("UPDATE djmdPlaylist SET ParentID='4000' WHERE ID IN ('1000','2000','3000')")
            conn.commit();self.case.catalog=store.catalog(conn)
        finally:conn.close()
        payload=xml.append_node(self.path.read_bytes(),'4000',attribute=1)
        for identity in ('3E8','7D0','BB8'):
            payload=payload.replace(f'Id="{identity}" ParentId="0"'.encode(),f'Id="{identity}" ParentId="FA0"'.encode())
        self.path.write_bytes(payload)

    def request(self,operation='rename_smart',**options):
        kwargs={'identity':'2000',**({'name':'Review name'} if operation=='rename_smart' else {'anchor_id':'1000'}),**options}
        return store.request_for(self.case.catalog,operation,**kwargs)

    def writer(self,operation):
        kwargs=({'smart_rename_accepted':True,'smart_rename_profile':store.SMART_RENAME_PROFILE}
                if operation=='rename_smart' else
                {'smart_reorder_accepted':True,'smart_reorder_profile':store.SMART_REORDER_PROFILE})
        return store.PlaylistStore(replace(self.case.authority,**kwargs),self.case.root/'metadata-recovery')

    def snapshot(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn), {name:_rows(conn,name) for name in ('djmdPlaylist','djmdSongPlaylist','agentRegistry','djmdContent')}
        finally:conn.close()

    def test_old_create_delete_and_regular_reorder_authorities_never_accept_new_metadata_profiles(self):
        authority=replace(self.case.authority,reorder_accepted=True,smart_delete_accepted=True,
                          smart_delete_profile=store.SMART_LOCAL_DELETE_PROFILE)
        before=self.snapshot(),self.path.read_bytes()
        for operation in ('rename_smart','reorder_smart'):
            writer=store.PlaylistStore(authority,self.case.root/operation)
            with mock.patch.object(store,'encrypted_backup',side_effect=AssertionError('Unaccepted operation made a write backup')):
                with self.assertRaisesRegex(WriteProofError,'separate acceptance'):writer.apply(self.request(operation))
        self.assertEqual((self.snapshot(),self.path.read_bytes()),before)
        self.assertFalse(authority.smart_rename_accepted);self.assertFalse(authority.smart_reorder_accepted)
        for field in ('smart_rename_accepted','smart_reorder_accepted'):
            with self.assertRaises(WriteProofError):replace(authority,**{field:1})

    def test_inline_name_native_window_updates_exact_row_counter_plus_one_and_one_xml_clock(self):
        before,rows=self.snapshot();old={row['ID']:row for row in rows['djmdPlaylist']};payload=self.path.read_bytes()
        request=self.request();writer=self.writer('rename_smart');writer.apply(request)
        after,current=self.snapshot();new={row['ID']:row for row in current['djmdPlaylist']}
        old_counter=next(row for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        self.assertEqual(new['2000'],{**old['2000'],'Name':'Review name','updated_at':request['timestamp'],
                                      'rb_local_usn':old_counter['int_1']+1})
        self.assertEqual({key:value for key,value in new.items() if key!='2000'},
                         {key:value for key,value in old.items() if key!='2000'})
        self.assertEqual(current['djmdSongPlaylist'],rows['djmdSongPlaylist'])
        self.assertEqual(self.path.read_bytes(),xml._touch(payload,'2000',request['xml_timestamp'],created_smart=True))
        for table,digest in before['tables'].items():
            if table not in {'djmdPlaylist','agentRegistry'}:self.assertEqual(after['tables'][table],digest,table)
        committed=self.snapshot(),self.path.read_bytes();writer.apply(request)
        self.assertEqual((self.snapshot(),self.path.read_bytes()),committed)
        self.assertEqual(writer.committed_transition(request),(request['revision'],writer.snapshot()['revision']))

    def test_synced_local_inline_name_preserves_native_sync_history_and_opaque_rule(self):
        conn=_connect(self.database)
        try:
            conn.execute("UPDATE djmdPlaylist SET rb_local_synced=1 WHERE ID='2000'");conn.commit()
        finally:conn.close()
        writer=self.writer('rename_smart');_,rows=self.snapshot();old=next(row for row in rows['djmdPlaylist'] if row['ID']=='2000')
        counter=next(row['int_1'] for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        request=store.request_for(writer.snapshot(),'rename_smart',identity='2000',name='Synced local renamed');writer.apply(request)
        _,current=self.snapshot();new=next(row for row in current['djmdPlaylist'] if row['ID']=='2000')
        self.assertEqual(new,{**old,'Name':request['name'],'rb_local_usn':counter+1,'updated_at':request['timestamp']})
        self.assertEqual(new['rb_local_synced'],1);self.assertIsNone(new['usn'])
        self.assertEqual(current['djmdSongPlaylist'],rows['djmdSongPlaylist'])

    def test_mixed_adjacent_candidate_preserves_rule_memberships_parent_and_unrelated_xml(self):
        before,rows=self.snapshot();payload=self.path.read_bytes();old={r['ID']:r for r in rows['djmdPlaylist']}
        request=self.request('reorder_smart');writer=self.writer('reorder_smart');writer.apply(request)
        after,current=self.snapshot();new={r['ID']:r for r in current['djmdPlaylist']}
        counter=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')['int_1']
        self.assertEqual(new['2000'],{**old['2000'],'Seq':1,'updated_at':request['timestamp'],'rb_local_usn':counter+1})
        self.assertEqual(new['1000'],{**old['1000'],'Seq':2,'updated_at':request['survivor_timestamp'],'rb_local_usn':counter+2})
        self.assertEqual(new['3000'],old['3000'])
        self.assertEqual(self.path.read_bytes(),xml.touch(
            xml._touch(payload,'2000',request['xml_timestamp'],created_smart=True),
            '4000',request['smart_parent_xml_timestamp']))
        self.assertEqual(new['4000'],old['4000'])
        for table,digest in before['tables'].items():
            if table not in {'djmdPlaylist','agentRegistry'}:self.assertEqual(after['tables'][table],digest,table)
        self.assertEqual(current['djmdSongPlaylist'],rows['djmdSongPlaylist'])
        committed=self.snapshot(),self.path.read_bytes();writer.apply(request)
        self.assertEqual((self.snapshot(),self.path.read_bytes()),committed)

    def test_direct_long_moves_both_directions_and_regular_target_preserve_exact_smart_rows(self):
        conn=_connect(self.database)
        try:
            template=next(row for row in _rows(conn,'djmdPlaylist') if row['ID']=='2000')
            fourth={**template,'ID':'5000','UUID':fixture.native_uuid(5000),'Seq':4,'Name':'Other smart','rb_local_synced':1}
            columns=list(fourth)
            conn.execute(f'INSERT INTO djmdPlaylist ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                         [fourth[key] for key in columns]);conn.commit()
        finally:conn.close()
        self.path.write_bytes(xml.append_smart_node(self.path.read_bytes(),'5000','4000',timestamp=200))
        writer=self.writer('reorder_smart')
        for identity,anchor,after in (('2000','5000',True),('2000','1000',False),('3000','2000',False)):
            before,rows=self.snapshot();old={row['ID']:row for row in rows['djmdPlaylist']};payload=self.path.read_bytes()
            request=store.request_for(writer.snapshot(),'reorder_smart',identity=identity,anchor_id=anchor,after=after)
            self.assertTrue(request['smart_order_clocks'])
            counter=next(row['int_1'] for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
            writer.apply(request);_,current=self.snapshot();new={row['ID']:row for row in current['djmdPlaylist']}
            changed=[identity,*sorted(request['smart_order_clocks'],key=lambda key:old[key]['Seq'])]
            final_counter=next(row['int_1'] for row in current['agentRegistry'] if row['registry_id']=='localUpdateCount')
            self.assertEqual(final_counter,counter+len(changed))
            for offset,key in enumerate(changed,1):
                self.assertEqual(new[key]['rb_local_usn'],counter+offset)
                allowed={'Seq','updated_at','rb_local_usn'}
                self.assertEqual({name:value for name,value in new[key].items() if name not in allowed},
                                 {name:value for name,value in old[key].items() if name not in allowed})
            self.assertEqual({key:value for key,value in new.items() if key not in changed},
                             {key:value for key,value in old.items() if key not in changed})
            self.assertEqual(self.path.read_bytes(),xml.touch(xml._touch(payload,identity,request['xml_timestamp'],
                created_smart=old[identity]['Attribute']==4),'4000',request['smart_parent_xml_timestamp']))
            self.assertEqual(current['djmdSongPlaylist'],rows['djmdSongPlaylist'])
            final=self.snapshot(),self.path.read_bytes();writer.apply(request)
            self.assertEqual((self.snapshot(),self.path.read_bytes()),final)

    def test_root_move_across_folder_has_no_parent_clock_and_preserves_folder_identity(self):
        conn=_connect(self.database)
        try:
            conn.execute("UPDATE djmdPlaylist SET ParentID='root' WHERE ID IN ('1000','2000','3000')")
            conn.execute("UPDATE djmdPlaylist SET Seq=4 WHERE ID='4000'");conn.commit()
        finally:conn.close()
        self.path.write_bytes(self.path.read_bytes().replace(b'ParentId="FA0"',b'ParentId="0"'))
        writer=self.writer('reorder_smart');payload=self.path.read_bytes();_,rows=self.snapshot()
        request=store.request_for(writer.snapshot(),'reorder_smart',identity='2000',anchor_id='4000',after=True)
        self.assertEqual(request['smart_parent_xml_timestamp'],0);writer.apply(request)
        _,current=self.snapshot();old={row['ID']:row for row in rows['djmdPlaylist']};new={row['ID']:row for row in current['djmdPlaylist']}
        self.assertEqual(new['2000']['Seq'],4);self.assertEqual(new['4000']['Seq'],3)
        for key in ('UUID','Attribute','ParentID','Name','SmartList'):self.assertEqual(new['4000'][key],old['4000'][key])
        self.assertEqual(self.path.read_bytes(),xml._touch(payload,'2000',request['xml_timestamp'],created_smart=True))

    def test_changed_sibling_clock_set_or_parent_clock_refuses_without_publication(self):
        before=self.snapshot(),self.path.read_bytes()
        for field,value in (('smart_order_clocks',{}),('smart_order_clocks',{'3000':'2026-11-19 00:00:00.000 +00:00'}),
                            ('smart_parent_xml_timestamp',0)):
            request=self.request('reorder_smart');request[field]=value
            with self.subTest(field=field,value=value),self.assertRaises((ValueError,WriteProofError)):
                self.writer('reorder_smart').apply(request)
        self.assertEqual((self.snapshot(),self.path.read_bytes()),before)

    def test_reviewed_complete_baseline_is_checked_inside_writer_before_plan(self):
        import hashlib
        before,_=self.snapshot();payload=self.path.read_bytes();request=self.request();writer=self.writer('rename_smart')
        conn=_connect(self.database)
        try:
            conn.execute("UPDATE agentRegistry SET int_1=int_1+1 WHERE registry_id='localUpdateCount'")
            conn.commit()
        finally:conn.close()
        concurrent=self.snapshot(),self.path.read_bytes()
        with mock.patch.object(writer,'_recover_locked') as recover:
            with self.assertRaisesRegex(ConflictError,'complete native baseline changed'):
                writer.apply(request,expected_state=before,expected_xml_sha256=hashlib.sha256(payload).hexdigest())
            recover.assert_not_called()
        self.assertEqual((self.snapshot(),self.path.read_bytes()),concurrent)
        self.assertFalse((writer.root/('playlist-'+request['operation_id'])).exists())
        state,_=self.snapshot()
        with self.assertRaisesRegex(ConflictError,'complete native baseline changed'):
            writer.apply(request,expected_state=state,expected_xml_sha256='0'*64)
        writer.apply(request,expected_state=state,expected_xml_sha256=hashlib.sha256(payload).hexdigest())
        with self.assertRaisesRegex(RecoveryRequired,'cannot recover or replay'):
            writer.apply(request,expected_state=self.snapshot()[0],expected_xml_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_mixed_order_refuses_mismatched_native_xml_types_and_parent(self):
        original=self.path.read_bytes();request=self.request('reorder_smart')
        for before,after in ((b'Id="3E8" ParentId="FA0" Attribute="0"',b'Id="3E8" ParentId="FA0" Attribute="1"'),
                             (b'Id="FA0" ParentId="0" Attribute="1"',b'Id="FA0" ParentId="0" Attribute="0"')):
            self.assertIn(before,original);payload=original.replace(before,after);self.path.write_bytes(payload)
            before_db,_=self.snapshot()
            with self.assertRaises((ValueError,ConflictError)):self.writer('reorder_smart').apply(request)
            self.assertEqual(self.snapshot()[0],before_db);self.assertEqual(self.path.read_bytes(),payload)
        self.path.write_bytes(original)

    def test_plain_proposal_is_detached_read_only_and_does_not_enable_source(self):
        before=self.snapshot(),self.path.read_bytes();request=self.request('reorder_smart')
        proposal=self.case.writer.propose_smart_metadata(request)
        self.assertFalse(proposal['source_write_authority']);self.assertTrue(proposal['native_reference_required'])
        proposal['request']['smart_rule']='changed'
        self.assertEqual((self.snapshot(),self.path.read_bytes()),before)
        self.assertNotEqual(request['smart_rule'],'changed')
        with self.assertRaises(WriteProofError):self.case.writer.apply(request)

    def test_frozen_rule_identity_revision_and_cloud_history_fail_closed(self):
        for change in ('rule','uuid','profile','cloud'):
            with self.subTest(change=change):
                request=self.request();before=self.snapshot(),self.path.read_bytes()
                if change=='rule':request['smart_rule']='<NODE Different="yes"/>'
                if change=='uuid':request['playlist_uuid']=fixture.native_uuid(999)
                if change=='profile':request['smart_edit_profile']='invented'
                if change=='cloud':
                    conn=_connect(self.database);conn.execute('UPDATE djmdPlaylist SET usn=123 WHERE ID=?',('2000',));conn.commit();conn.close()
                    before=self.snapshot(),self.path.read_bytes();request=store.request_for(self.writer('rename_smart').snapshot(),'rename_smart',identity='2000',name='Review name')
                with self.assertRaises((ValueError,WriteProofError,ConflictError)):self.writer('rename_smart').apply(request)
                self.assertEqual((self.snapshot(),self.path.read_bytes()),before)

    def test_xml_smart_metadata_marker_never_admits_rule_or_unknown_fields(self):
        proposal=self.case.writer.propose_smart_metadata(self.request());payload=self.path.read_bytes()
        for field,value in (('SmartList','different'),('ParentID','3000'),('UUID',fixture.native_uuid(888)),('ImagePath','changed')):
            changes=deepcopy(proposal['changes']);changes[0]['after'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                xml.validate_transition(payload,proposal['xml'],changes,smart_modified=('2000',))
        with self.assertRaises(ValueError):xml.validate_transition(payload,proposal['xml'],proposal['changes'])
