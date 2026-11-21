"""Separate normalized smart constructors over generated encrypted TEMP rows."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_smart_playlist_creation as fixtures
from soundbox import rekordbox_playlist_store as store,rekordbox_playlist_plan as plan,rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect,_state,WriteProofError,ConflictError


class NormalizedSmartCreation(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SmartCreationProposals('test_any_is_explicit_default_for_one_or_multiple_exact_tag_bindings')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.add_folder()
        self.authority=replace(self.f.authority,smart_normalized_create_accepted=True,
            smart_normalized_create_profile=store.SMART_NORMALIZED_CREATE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.f.root/'normalized-smart-recovery')

    def request(self,parent='root'):
        return store.request_for(self.writer.snapshot(),'create_smart_normalized',name='New exact smart',parent=parent,tags=self.f.tags)

    def inspect(self):
        conn=_connect(self.f.database,readonly=True)
        try:return _state(conn),store.catalog(conn),store.row(conn,'agentRegistry','localUpdateCount')
        finally:conn.close()

    def test_contiguous_root_composition_matches_the_existing_native_smart_windows_exactly(self):
        from soundbox import rekordbox_playlist_create as normalized
        current=self.writer.snapshot();counter=self.inspect()[2]
        arguments=dict(identity='4000',uuid=fixtures.native_uuid(4000),name='Exact native windows',
            tag_ids=['201','202'],timestamp='2026-11-20 04:00:00.000 +00:00',
            definition_timestamp='2026-11-20 04:00:01.000 +00:00')
        existing=plan.create_smart(current['nodes'],counter,**arguments)
        proposed=normalized.smart_candidate(current['nodes'],counter,parent='root',
            position_timestamp=arguments['timestamp'],sibling_timestamps={},**arguments)
        for key in ('changes','playlist','counter'):self.assertEqual(proposed[key],existing[key],key)

    def test_gapped_root_normalizes_only_changed_peers_without_colliding_final_positions(self):
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=3 WHERE ID='2000'")
        finally:conn.close()
        before,catalog,counter=self.inspect();raw=self.f.xml_path.read_bytes();old={row['ID']:row for row in catalog['nodes']}
        self.assertTrue(store.smart_creation_parent_reason(catalog,'root'))
        self.assertFalse(store.smart_normalized_creation_parent_reason(catalog,'root'))
        request=self.request();observed,edge=self.writer.apply_with_transition(request)
        after,current,new_counter=self.inspect();created=next(row for row in current['nodes'] if row['ID']==request['playlist_id'])
        self.assertEqual((created['Attribute'],created['Seq'],created['ParentID']),(4,3,'root'))
        self.assertEqual(created['SmartList'],plan.smart_tag_rule(created['ID'],['201','202']))
        self.assertEqual(new_counter['int_1'],counter['int_1']+5)
        self.assertEqual(next(row for row in current['nodes'] if row['ID']=='2000'),
            {**old['2000'],'Seq':2,'updated_at':request['smart_clocks']['sibling_timestamps']['2000'],
             'rb_local_usn':counter['int_1']+3})
        for row in current['nodes']:
            if row['ID'] not in ('2000',created['ID']):self.assertEqual(row,old[row['ID']])
        for table,digest in before['tables'].items():
            if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],digest,table)
        expected=xml._touch(xml.append_smart_node(raw,created['ID']),created['ID'],request['xml_timestamp'],created_smart=True)
        self.assertEqual(self.f.xml_path.read_bytes(),expected);self.assertEqual(edge,(request['revision'],observed['revision']))
        previous=self.f.database.read_bytes(),self.f.xml_path.read_bytes();self.writer.apply(request)
        self.assertEqual((self.f.database.read_bytes(),self.f.xml_path.read_bytes()),previous)

    def test_empty_nested_destination_keeps_source_gaps_and_folder_metadata_exact(self):
        conn=_connect(self.f.database)
        try:
            template=store.row(conn,'djmdPlaylist','2000')
            folder={**template,'ID':'3000','UUID':fixtures.native_uuid(3000),'Seq':3,'ParentID':'2000'}
            conn.execute('INSERT INTO djmdPlaylist ('+','.join(folder)+') VALUES('+','.join('?' for _ in folder)+')',tuple(folder.values()))
        finally:conn.close()
        self.f.xml_path.write_bytes(xml.append_node(self.f.xml_path.read_bytes(),'3000','2000',1))
        before,catalog,counter=self.inspect();raw=self.f.xml_path.read_bytes()
        request=self.request('3000');self.writer.apply(request)
        after,current,new_counter=self.inspect();created=next(row for row in current['nodes'] if row['ID']==request['playlist_id'])
        self.assertEqual((created['ParentID'],created['Seq']),('3000',1));self.assertEqual(new_counter['int_1'],counter['int_1']+4)
        self.assertEqual([row for row in current['nodes'] if row['ID']!=created['ID']],catalog['nodes'])
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])
        expected=xml.touch(raw,'3000',request['smart_clocks']['parent_xml_timestamp'])
        expected=xml._touch(xml.append_smart_node(expected,created['ID'],'3000'),created['ID'],request['xml_timestamp'],created_smart=True)
        self.assertEqual(self.f.xml_path.read_bytes(),expected)

    def test_mixed_gapped_destination_uses_one_constructor_placement_then_combined_definition(self):
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Attribute=0,SmartList=NULL,Seq=4 WHERE ID='2001'")
        finally:conn.close()
        self.f.xml_path.write_bytes(self.f.xml_path.read_bytes().replace(b'Id="7D1" ParentId="7D0" Attribute="4"',b'Id="7D1" ParentId="7D0" Attribute="0"'))
        before,catalog,counter=self.inspect();request=self.request('2000');self.writer.apply(request)
        after,current,new_counter=self.inspect();nodes={row['ID']:row for row in current['nodes']}
        self.assertEqual((nodes['2001']['Seq'],nodes[request['playlist_id']]['Seq']),(1,2))
        self.assertEqual(new_counter['int_1'],counter['int_1']+5)
        self.assertEqual(nodes['2000'],next(row for row in catalog['nodes'] if row['ID']=='2000'))
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])

    def test_old_constructor_append_normalized_ordinary_and_node_profiles_never_grant_smart_normalization(self):
        request=self.request('2000')
        old=replace(self.f.authority,smart_create_accepted=True,smart_create_profile=store.SMART_CREATE_PROFILE,
            smart_append_create_accepted=True,smart_append_create_profile=store.SMART_APPEND_CREATE_PROFILE,
            normalized_create_accepted=True,normalized_create_profile=store.NORMALIZED_CREATE_PROFILE,
            node_move_accepted=True,node_move_profile=store.NODE_MOVE_PROFILE)
        for authority in (old,replace(old,smart_normalized_create_accepted=True)):
            writer=store.PlaylistStore(authority,self.f.root/'blocked')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('source lease')):
                with self.assertRaises(WriteProofError):writer.apply(request)
                with self.assertRaises(WriteProofError):writer.prepare_retry(request)

    def test_uuid_tag_xml_counter_and_ambiguous_destination_refuse_without_publication(self):
        request=self.request('2000');before=self.inspect(),self.f.xml_path.read_bytes()
        for field,value in (('parent_uuid',fixtures.native_uuid(999)),('smart_profile',store.SMART_CREATE_PROFILE)):
            changed=deepcopy(request);changed[field]=value
            with self.subTest(field=field),self.assertRaises((WriteProofError,ConflictError)):self.writer.apply(changed)
            self.assertEqual((self.inspect(),self.f.xml_path.read_bytes()),before)
        changed=deepcopy(request);changed['tags'][0]['uuid']=fixtures.native_uuid(999)
        with self.assertRaises(ConflictError):self.writer.apply(changed)
        changed=deepcopy(request);changed['smart_clocks']['sibling_timestamps']={'2001':changed['timestamp']}
        with self.assertRaises(ValueError):self.writer.apply(changed)
        changed=deepcopy(request);changed['smart_clocks']['parent_xml_timestamp']=0
        with self.assertRaises(WriteProofError):store.validate_request(changed)

    def test_explicit_failed_root_intention_can_retain_identity_and_tags_under_new_independent_profile(self):
        request=store.request_for(self.writer.snapshot(),'create_smart',name='INDIES',tags=self.f.tags)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=7 WHERE ID='2000'")
        finally:conn.close()
        current=self.writer.snapshot()
        renewed=store.normalized_smart_request(request,current,operation_id='1'*32)
        self.assertEqual(renewed['operation'],'create_smart_normalized')
        for key in ('playlist_id','playlist_uuid','parent','parent_uuid','tags','name','match'):
            self.assertEqual(renewed[key],request[key],key)
        self.writer.apply(renewed)
        created=next(row for row in self.writer.snapshot()['nodes'] if row['ID']==request['playlist_id'])
        self.assertEqual(created['Name'],'INDIES');self.assertEqual(created['UUID'],request['playlist_uuid'])

    def legacy_writer(self,*,normalized=True,append=False):
        authority=replace(self.authority,smart_create_accepted=True,smart_create_profile=store.SMART_CREATE_PROFILE,
            smart_append_create_accepted=append,smart_append_create_profile=store.SMART_APPEND_CREATE_PROFILE if append else '',
            smart_normalized_create_accepted=normalized,smart_normalized_create_profile=store.SMART_NORMALIZED_CREATE_PROFILE if normalized else '')
        return store.PlaylistStore(authority,self.f.root/('explicit-smart-retry-'+str(normalized)+'-'+str(append)))

    def test_explicit_retry_upgrades_ineligible_legacy_root_and_keeps_all_future_pins(self):
        request=store.request_for(self.writer.snapshot(),'create_smart',name='INDIES',tags=self.f.tags)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=7 WHERE ID='2000'")
        finally:conn.close()
        writer=self.legacy_writer();request={**request,'revision':writer.snapshot()['revision']};before=deepcopy(request)
        renewed=writer.prepare_retry(request)
        self.assertNotEqual(renewed['operation_id'],request['operation_id'])
        self.assertEqual(renewed['operation'],'create_smart_normalized');self.assertEqual(request,before)
        for key in ('playlist_id','playlist_uuid','parent','parent_uuid','tags','name','match'):
            self.assertEqual(renewed[key],request[key],key)
        with self.assertRaises(ValueError):writer.apply(request)
        writer.apply(renewed)

    def test_explicit_retry_without_new_capability_refuses_old_gapped_constructor_without_publication(self):
        request=store.request_for(self.writer.snapshot(),'create_smart',name='INDIES',tags=self.f.tags)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=7 WHERE ID='2000'")
        finally:conn.close()
        before=self.inspect(),self.f.xml_path.read_bytes()
        with self.assertRaises(WriteProofError):self.legacy_writer(normalized=False).prepare_retry(request)
        self.assertEqual((self.inspect(),self.f.xml_path.read_bytes()),before)

    def test_explicit_append_retry_sees_root_gaps_for_any_destination_and_bounds_real_parent_xml(self):
        request=store.request_for(self.writer.snapshot(),'create_smart_append',name='INDIES',parent='2000',tags=self.f.tags)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=7 WHERE ID='2000'")
        finally:conn.close()
        future=8_000_000_000_000
        self.f.xml_path.write_bytes(xml.touch(self.f.xml_path.read_bytes(),'2000',future))
        self.assertTrue(store.smart_creation_parent_reason(self.writer.snapshot(),'2000'))
        writer=self.legacy_writer(append=True);renewed=writer.prepare_retry(request)
        self.assertEqual(renewed['operation'],'create_smart_normalized')
        self.assertGreater(renewed['smart_clocks']['parent_xml_timestamp'],future)
        self.assertGreaterEqual(renewed['xml_timestamp'],request['xml_timestamp'])
        for key in ('playlist_id','playlist_uuid','parent','parent_uuid','tags','name','match'):
            self.assertEqual(renewed[key],request[key],key)
        writer.apply(renewed)

    def test_committed_legacy_receipt_is_never_upgraded_after_later_root_gaps(self):
        writer=self.legacy_writer()
        request=store.request_for(writer.snapshot(),'create_smart',name='Previously committed',tags=self.f.tags)
        writer.apply(request)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=9 WHERE ID='2000'")
        finally:conn.close()
        with mock.patch.object(writer,'snapshot',side_effect=AssertionError('Committed replay must return before renewed observation')):
            self.assertEqual(writer.prepare_retry(request),request)

    def test_historical_folder_creation_receipt_retains_original_protocol_when_root_was_gapped(self):
        writer=self.legacy_writer()
        request=store.request_for(writer.snapshot(),'create_smart',name='Historical folder constructor',parent='2000',tags=self.f.tags)
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=8 WHERE ID='2000'")
        finally:conn.close()
        # Reproduce an already supported old folder window, captured before
        # the stricter current UI admission policy. No automatic profile change.
        request={**request,'revision':writer.snapshot()['revision']}
        _,edge=writer.apply_with_transition(request)
        self.assertIsNotNone(edge)
        self.assertEqual(writer.committed_transition(request),edge)
        self.assertEqual(writer.prepare_retry(request),request)

    def test_interrupted_normalized_smart_creation_recovery_retry_preserve_exact_identity_tags_and_parent(self):
        request=self.request('2000');before=self.inspect(),self.f.xml_path.read_bytes()
        def interrupt(stage):
            if stage=='xml-published':raise RuntimeError('held normalized smart')
        with mock.patch.object(store,'_fault',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'held normalized smart'):
            self.writer.apply(request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual((self.inspect(),self.f.xml_path.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        for key in ('operation','playlist_id','playlist_uuid','parent','parent_uuid','tags','name','smart_profile'):
            self.assertEqual(renewed[key],request[key],key)
        self.writer.apply(renewed)
