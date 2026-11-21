"""Separately gated folder constructor/placement/name compositions, TEMP only."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_smart_playlist_creation as fixtures
from soundbox import rekordbox_playlist_store as store,rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect,_state,WriteProofError,ConflictError


class NormalizedFolderCreation(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SmartCreationProposals('test_any_is_explicit_default_for_one_or_multiple_exact_tag_bindings')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.add_folder()
        self.authority=replace(self.f.authority,normalized_folder_create_accepted=True,
            normalized_folder_create_profile=store.NORMALIZED_FOLDER_CREATE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.f.root/'normalized-folder-recovery')

    def request(self,parent='root',name='New exact folder'):
        return store.request_for(self.writer.snapshot(),'create_folder_normalized',name=name,parent=parent,folder=True)

    def inspect(self):
        conn=_connect(self.f.database,readonly=True)
        try:return _state(conn),store.catalog(conn),store.row(conn,'agentRegistry','localUpdateCount')
        finally:conn.close()

    def test_gapped_root_preserves_legacy_count_constructor_then_normalizes_only_changed_peers(self):
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=3 WHERE ID='2000'")
        finally:conn.close()
        before,catalog,counter=self.inspect();request=self.request();raw=self.f.xml_path.read_bytes()
        self.writer.apply(request);after,current,final_counter=self.inspect()
        nodes={row['ID']:row for row in current['nodes']};old={row['ID']:row for row in catalog['nodes']}
        target=nodes[request['playlist_id']]
        self.assertEqual((target['Attribute'],target['ParentID'],target['Seq'],target['SmartList']),(1,'root',3,None))
        self.assertEqual(final_counter['int_1'],counter['int_1']+3)
        self.assertEqual(nodes['2000'],{**old['2000'],'Seq':2,'rb_local_usn':counter['int_1']+2,
            'updated_at':request['normalized_create_clocks']['sibling_timestamps']['2000']})
        for key in old:
            if key!='2000':self.assertEqual(nodes[key],old[key])
        for table,digest in before['tables'].items():
            if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],digest,table)
        expected=xml.touch(xml.append_node(raw,target['ID'],attribute=1),target['ID'],request['xml_timestamp'])
        self.assertEqual(self.f.xml_path.read_bytes(),expected)

    def test_folder_inside_mixed_gapped_folder_keeps_every_rule_and_parent_metadata(self):
        conn=_connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=4 WHERE ID='2001'")
        finally:conn.close()
        before,catalog,counter=self.inspect();old={row['ID']:row for row in catalog['nodes']}
        request=self.request('2000');self.writer.apply(request);after,current,final_counter=self.inspect()
        nodes={row['ID']:row for row in current['nodes']}
        self.assertEqual((nodes[request['playlist_id']]['ParentID'],nodes[request['playlist_id']]['Seq']),('2000',2))
        self.assertEqual(nodes['2001']['Seq'],1);self.assertEqual(nodes['2001']['SmartList'],old['2001']['SmartList'])
        self.assertEqual(nodes['2000'],old['2000']);self.assertEqual(final_counter['int_1'],counter['int_1']+4)
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])

    def test_ordinary_normalized_node_move_and_smart_flags_never_admit_new_folder_profile(self):
        request=self.request()
        old=replace(self.f.authority,normalized_create_accepted=True,normalized_create_profile=store.NORMALIZED_CREATE_PROFILE,
            node_move_accepted=True,node_move_profile=store.NODE_MOVE_PROFILE,smart_normalized_create_accepted=True,
            smart_normalized_create_profile=store.SMART_NORMALIZED_CREATE_PROFILE)
        for authority in (old,replace(old,normalized_folder_create_accepted=True)):
            writer=store.PlaylistStore(authority,self.f.root/'blocked-folder')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('source lease')):
                with self.assertRaises(WriteProofError):writer.apply(request)
                with self.assertRaises(WriteProofError):writer.prepare_retry(request)
        changed=deepcopy(request);changed['operation']='create_normalized';changed['normalized_create_profile']=store.NORMALIZED_CREATE_PROFILE
        with self.assertRaises(WriteProofError):store.validate_request(changed)

    def test_default_folder_name_constructor_retains_zero_xml_time_when_no_placement_or_name_write(self):
        before,_,counter=self.inspect();request=self.request(name='Untitled Folder');self.writer.apply(request)
        after,current,final_counter=self.inspect();created=next(row for row in current['nodes'] if row['ID']==request['playlist_id'])
        self.assertEqual(created['Name'],'Untitled Folder');self.assertEqual(final_counter['int_1'],counter['int_1']+1)
        self.assertEqual(xml._parse(self.f.xml_path.read_bytes())[0][int(created['ID'])][0]['Timestamp'],'0')
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])

    def test_parent_identity_refuses_and_interrupted_folder_recovers_then_retry_preserves_folder_profile(self):
        request=self.request('2000');before=self.inspect(),self.f.xml_path.read_bytes()
        changed=deepcopy(request);changed['parent_uuid']=fixtures.native_uuid(999)
        with self.assertRaises(ConflictError):self.writer.apply(changed)
        def interrupt(stage):
            if stage=='xml-published':raise RuntimeError('held normalized folder')
        with mock.patch.object(store,'_fault',side_effect=interrupt),self.assertRaisesRegex(RuntimeError,'held normalized folder'):
            self.writer.apply(request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual((self.inspect(),self.f.xml_path.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        for key in ('operation','playlist_id','playlist_uuid','parent','parent_uuid','folder','name','normalized_create_profile'):
            self.assertEqual(renewed[key],request[key],key)
        self.writer.apply(renewed)
