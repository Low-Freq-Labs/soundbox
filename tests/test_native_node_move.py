"""Independently gated all-node placement over generated encrypted TEMP data."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_smart_playlist_move as fixtures
from soundbox import rekordbox_playlist_store as store,rekordbox_playlist_move as move,rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect,WriteProofError,ConflictError


class NativeNodePlacement(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SmartMoveCandidate('test_old_authorities_never_admit_cross_parent_source_work')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.database=self.fixture.database;self.path=self.fixture.path
        self.authority=replace(self.fixture.authority,node_move_accepted=True,node_move_profile=store.NODE_MOVE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.fixture.f.case.root/'node-move-recovery')

    def request(self,identity,parent,anchor_id=None,after=False):
        return store.request_for(self.writer.snapshot(),'move',identity=identity,parent=parent,anchor_id=anchor_id,after=after)

    def test_each_node_kind_moves_into_empty_folder_back_to_root_preserving_every_other_row_table_and_membership(self):
        for identity in ('1000','2000','4000'):
            with self.subTest(identity=identity):
                before,rows=self.fixture.inspect();old={row['ID']:row for row in rows['djmdPlaylist']}
                counter=next(row['int_1'] for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
                request=self.request(identity,'5000');self.writer.apply(request)
                after,current=self.fixture.inspect();new={row['ID']:row for row in current['djmdPlaylist']}
                for key in old:
                    if key!=identity:self.assertEqual(new[key],old[key],key)
                for table,digest in before['tables'].items():
                    if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],digest,table)
                self.assertEqual(new[identity],{**old[identity],'ParentID':'5000','Seq':1,
                    'updated_at':request['timestamp'],'rb_local_usn':counter+1})
                self.writer.apply(self.request(identity,'root'))

    def test_nested_populated_folder_moves_retain_descendant_links_rules_and_memberships(self):
        self.writer.apply(self.request('1000','5000'))
        self.writer.apply(self.request('5000','4000'))
        before,rows=self.fixture.inspect();old={row['ID']:row for row in rows['djmdPlaylist']}
        request=self.request('4000','root')
        self.assertEqual({binding['id'] for binding in request['move_descendants']},{'1000','2000','3000','5000'})
        self.writer.apply(request)
        after,current=self.fixture.inspect();new={row['ID']:row for row in current['djmdPlaylist']}
        for identity in ('1000','2000','3000','5000'):self.assertEqual(new[identity],old[identity])
        self.assertEqual(after['tables']['djmdSongPlaylist'],before['tables']['djmdSongPlaylist'])
        self.writer.apply(self.request('5000','root'))
        self.writer.apply(self.request('4000','5000'))
        final={row['ID']:row for row in self.writer.snapshot()['nodes']}
        self.assertEqual(final['1000']['ParentID'],'5000');self.assertEqual(final['2000']['ParentID'],'4000')

    def test_cross_parent_before_after_and_same_parent_append_use_exact_anchor_and_target_first_counter(self):
        request=self.request('1000','root',anchor_id='5000',after=False)
        self.writer.apply(request)
        current=self.writer.snapshot();root=sorted((r for r in current['nodes'] if r['ParentID']=='root'),key=lambda r:r['Seq'])
        self.assertEqual([r['ID'] for r in root],['4000','1000','5000'])
        counter=next(r for r in root if r['ID']=='1000')['rb_local_usn']
        self.assertEqual(next(r for r in root if r['ID']=='5000')['rb_local_usn'],counter+1)
        self.writer.apply(self.request('1000','root',anchor_id='4000',after=False))
        self.writer.apply(self.request('1000','root'))
        root=sorted((r for r in self.writer.snapshot()['nodes'] if r['ParentID']=='root'),key=lambda r:r['Seq'])
        self.assertEqual([r['ID'] for r in root],['4000','5000','1000'])

    def test_same_parent_relative_noop_preserves_counter_and_xml_even_with_native_gaps(self):
        before=self.fixture.inspect(),self.path.read_bytes()
        request=self.request('3000','4000')
        self.writer.apply(request)
        self.assertEqual((self.fixture.inspect(),self.path.read_bytes()),before)
        conn=_connect(self.database)
        try:conn.execute("UPDATE djmdPlaylist SET Seq=Seq+10 WHERE ParentID='4000'")
        finally:conn.close()
        request=self.request('3000','4000');before=self.fixture.inspect(),self.path.read_bytes()
        self.writer.apply(request)
        self.assertEqual((self.fixture.inspect(),self.path.read_bytes()),before)

    def test_every_old_capability_and_mismatched_profile_fail_before_source_lease_or_retry(self):
        request=self.request('1000','5000')
        for authority in (replace(self.authority,node_move_accepted=False,node_move_profile=''),
                          replace(self.authority,node_move_profile=store.SMART_MOVE_PROFILE)):
            writer=store.PlaylistStore(authority,self.fixture.f.case.root/'blocked-node')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('Source lease')):
                with self.assertRaises(WriteProofError):writer.apply(request)
                with self.assertRaises(WriteProofError):writer.prepare_retry(request)

    def test_cycle_anchors_and_stale_subtrees_refuse_without_native_publication(self):
        for identity,parent in (('4000','4000'),('4000','2000'),('1000','2000')):
            with self.subTest(identity=identity,parent=parent),self.assertRaises(ValueError):self.request(identity,parent)
        self.writer.apply(self.request('5000','4000'))
        with self.assertRaises(ValueError):self.request('4000','5000')
        with self.assertRaises(ValueError):self.request('1000','root',anchor_id='2000')
        request=self.request('4000','root');changed=deepcopy(request);changed['move_descendants'][0]['uuid']=request['playlist_uuid']
        before=self.fixture.inspect(),self.path.read_bytes()
        with self.assertRaises(ConflictError):self.writer.apply(changed)
        self.assertEqual((self.fixture.inspect(),self.path.read_bytes()),before)
        changed=deepcopy(request);changed['node_attribute']=0
        with self.assertRaises(WriteProofError):store.validate_request(changed)

    def test_interrupted_folder_move_rollback_retry_and_receipt_replay_preserve_scope(self):
        request=self.request('4000','5000');before=self.fixture.inspect(),self.path.read_bytes()
        def interrupted(stage):
            if stage=='xml-published':raise RuntimeError('Held native node placement')
        with mock.patch.object(store,'_fault',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'Held native'):
            self.writer.apply(request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual((self.fixture.inspect(),self.path.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        for key in ('playlist_id','playlist_uuid','node_attribute','source_parent','source_parent_uuid','parent','parent_uuid',
                    'anchor_id','anchor_uuid','anchor_attribute','after','smart_rule','move_descendants'):
            self.assertEqual(renewed[key],request[key],key)
        catalog,edge=self.writer.apply_with_transition(renewed);before=self.database.read_bytes(),self.path.read_bytes()
        self.writer.apply(renewed)
        self.assertEqual((self.database.read_bytes(),self.path.read_bytes()),before)
        self.assertEqual(self.writer.committed_transition(renewed),edge)
        self.assertEqual(catalog['revision'],edge[1])

    def test_deep_folder_destination_accepts_nesting_and_rejects_indirect_cycles(self):
        nodes=self.writer.snapshot()['nodes'];template=next(row for row in nodes if row['ID']=='5000')
        parent='root'
        for number in range(6000,6070):
            identity=str(number)
            nodes.append({**template,'ID':identity,'UUID':f'00000000-0000-4000-8000-{number:012d}',
                          'ParentID':parent,'Seq':1 if parent!='root' else 3})
            parent=identity
        proposal=move.node_context(nodes,identity='4000',parent=parent)
        self.assertEqual(proposal['positions']['4000'],1)
        self.assertTrue(move.node_reason(nodes,identity='6000',parent=parent))

    def test_committed_receipt_never_blesses_a_changed_subtree_or_replaced_anchor(self):
        request=self.request('4000','5000');self.writer.apply(request)
        conn=_connect(self.database)
        try:conn.execute("UPDATE djmdPlaylist SET ParentID='root' WHERE ID='1000'")
        finally:conn.close()
        before=self.fixture.inspect(),self.path.read_bytes()
        with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertEqual((self.fixture.inspect(),self.path.read_bytes()),before)

    def test_xml_whitelist_cannot_change_folder_name_type_rule_or_any_descendant(self):
        request=self.request('4000','5000');conn=_connect(self.database,readonly=True)
        try:proposal=store.build_plan(conn,request,self.path.read_bytes())
        finally:conn.close()
        for field,value in (('Name','Wrong'),('SmartList','Wrong'),('Attribute',4)):
            changes=deepcopy(proposal['changes']);changes[0]['after'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                xml.validate_transition(self.path.read_bytes(),proposal['xml'],changes,node_moved=('4000',),touched_playlists=('5000',))
        with self.assertRaises(ValueError):
            xml.validate_transition(self.path.read_bytes(),proposal['xml']+b' ',proposal['changes'],node_moved=('4000',),touched_playlists=('5000',))
        self.assertTrue(move.node_reason(self.writer.snapshot()['nodes'],identity='4000',parent='4000'))
