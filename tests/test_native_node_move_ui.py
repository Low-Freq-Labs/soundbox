"""General native node placement keeps subtree custody through durable ACK."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.trees import NATIVE_BINDING_ROLE
from soundbox import engine, rekordbox_playlist_plan as planner
from soundbox.state_repository import read_state
from tests import test_smart_playlist_move_ui as smart_fixture


class NativeNodeMovePublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):smart_fixture.SmartMovePublication.setUpClass()

    def setUp(self):
        smart_fixture.SmartMovePublication.setUp(self)
        catalog=self.w.lib.source_playlist_catalog
        for identity,content in (('5','42'),('6','43')):
            catalog['members'].append(dict(ID='member-'+identity,UUID='member-uuid-'+identity,
                PlaylistID=identity,ContentID=content,TrackNo=1,rb_local_deleted=0))
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.w._populate_tree(engine.summary(self.w.lib))
        self.w.tree.setCurrentItem(self.w._find_tree_item('Folder / Smart','2'))
        self.w._reload_current_view();self.assertTrue(self.w._save_state())

    def test_default_off_folder_move_preserves_source_state_and_canonical_queue(self):
        before=deepcopy((self.w.lib.source_playlist_catalog,self.w.lib.playlists))
        saved=read_state(self.w._state_repository.path)
        self.assertTrue(self.controller.node_move_reason())
        self.assertFalse(self.w._native_move('1','3'))
        self.assertEqual(self.controller.pending,[])
        self.assertEqual(read_state(self.w._state_repository.path),saved)
        self.assertEqual((self.w.lib.source_playlist_catalog,self.w.lib.playlists),before)
        self.case.source.assert_not_called()

    def test_accepted_generic_profile_routes_unanchored_smart_move_through_same_node_protocol(self):
        with (mock.patch.object(self.controller,'node_move_reason',return_value=''),
              mock.patch.object(self.controller,'smart_move_reason',side_effect=AssertionError('Legacy append route'))):
            self.assertTrue(self.w._native_move('2','3'))
            queue=self.case.durable()
        request=queue[0]['request']
        self.assertEqual((request['operation'],request['playlist_id'],request['parent'],request['node_attribute']),
                         ('move','2','3',4))
        self.assertEqual(self.w._native_view_playlist('Keep / Smart').keys,['t','u'])

    def test_folder_move_projects_all_descendant_paths_without_publishing_source_or_memberships(self):
        w=self.w;w.table.selectAll();seat=w._browser_seat()
        before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        saved=read_state(w._state_repository.path)
        with mock.patch.object(self.controller,'node_move_reason',return_value=''):
            with self.case.held_canonical() as (entered,release):
                self.assertTrue(w._native_move('1','3'));self.assertTrue(entered.wait(2))
                self.assertEqual(w._view_key,'rb:Keep / Folder / Smart')
                self.assertEqual(w._browser_seat(),seat)
                projected=w._native_presentation()
                self.assertEqual(projected.source_playlist_catalog['members'],before[0]['members'])
                children={p.pid:(p.folder_path,p.keys) for p in projected.playlists if p.pid in ('2','5','6')}
                self.assertEqual(children,{'2':(['Keep','Folder'],['t','u']),
                    '5':(['Keep','Folder'],['t']),'6':(['Keep','Folder'],['u'])})
                self.assertEqual(read_state(w._state_repository.path),saved)
                self.assertEqual((w.lib.source_playlist_catalog,w.lib.playlists),before)
                self.case.source.assert_not_called();release.set()
            queue=self.case.durable()
        self.assertEqual(queue[0]['request']['operation'],'move')
        self.assertEqual((queue[0]['request']['source_parent'],queue[0]['request']['parent']),('root','3'))
        self.assertEqual((w.lib.source_playlist_catalog,w.lib.playlists),before)

    def test_cross_parent_before_after_anchor_places_regular_playlist_at_requested_destination(self):
        w=self.w
        with mock.patch.object(self.controller,'node_move_reason',return_value=''):
            self.assertTrue(w._native_place('5','3','4',False))
            first=self.case.durable()[0]['request']
            self.assertEqual((first['operation'],first['anchor_id'],first['after']),('move','4',False))
            nodes=w._native_presentation().source_playlist_catalog['nodes']
            self.assertEqual({r['ID']:r['Seq'] for r in nodes if r['ParentID']=='3'},{'4':2,'5':1})
            # A later captured cross-parent move retains its destination anchor;
            # it never silently reorders the source folder instead.
            self.assertTrue(w._native_place('6','3','4',True))
            queue=self.case.durable()
            self.assertEqual([(r['request']['playlist_id'],r['request']['parent'],r['request']['after'])
                              for r in queue],[('5','3',False),('6','3',True)])
            nodes=w._native_presentation().source_playlist_catalog['nodes']
            self.assertEqual({r['ID']:r['Seq'] for r in nodes if r['ParentID']=='3'},{'4':2,'5':1,'6':3})

    def test_failed_canonical_folder_move_retains_intention_and_returns_to_verified_projection(self):
        w=self.w;before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        with (mock.patch.object(self.controller,'node_move_reason',return_value=''),
              mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('Synthetic full volume'))):
            self.assertTrue(w._native_move('1','3'))
            request=deepcopy(self.controller.pending[0]['request']);self.case.failed_admission()
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertEqual((w.lib.source_playlist_catalog,w.lib.playlists),before)
        self.assertEqual(w._native_presentation().source_playlist_catalog,before[0])
        self.case.discard();self.assertIsNotNone(w._find_tree_item('Folder / Smart','2'))

    def test_ordering_unique_source_gaps_uses_generic_placement_and_keeps_smart_membership(self):
        w=self.w;before=deepcopy(w.lib.source_playlist_catalog)
        with mock.patch.object(self.controller,'node_move_reason',return_value=''):
            self.assertTrue(w._native_reorder('6','5',False))
            queue=self.case.durable()
        request=queue[0]['request']
        self.assertEqual((request['operation'],request['source_parent'],request['parent'],request['anchor_id']),
                         ('move','1','1','5'))
        projected=w._native_presentation()
        self.assertEqual({r['ID']:r['Seq'] for r in projected.source_playlist_catalog['nodes']
                          if r['ParentID']=='1'},{'2':3,'5':2,'6':1})
        self.assertEqual(projected.source_playlist_catalog['members'],before['members'])
        self.assertEqual(next(p.keys for p in projected.playlists if p.pid=='2'),['t','u'])
        self.assertEqual(w.lib.source_playlist_catalog,before)

    def test_painted_destination_and_anchor_uuid_must_match_current_native_binding(self):
        w=self.w
        for identity,call in (('3',lambda:w._native_move('1','3')),
                              ('4',lambda:w._native_place('5','3','4',False)),
                              ('6',lambda:w._native_reorder('6','5',False)),
                              ('5',lambda:w._native_reorder('6','5',False))):
            with self.subTest(identity=identity):
                item=w._find_tree_item('',identity)
                captured=deepcopy(item.data(0,NATIVE_BINDING_ROLE))
                self.assertIsInstance(captured,dict)
                changed={**captured,'uuid':'00000000-0000-4000-8000-000000000099'}
                item.setData(0,NATIVE_BINDING_ROLE,changed)
                try:
                    with mock.patch.object(self.controller,'node_move_reason',return_value=''):
                        self.assertFalse(call())
                    self.assertEqual(self.controller.pending,[])
                    self.case.source.assert_not_called()
                finally:item.setData(0,NATIVE_BINDING_ROLE,captured)

    def test_worker_fifo_renews_folder_subtree_move_and_waits_for_canonical_ack_before_publication(self):
        w=self.w;catalog=deepcopy(w.lib.source_playlist_catalog)
        original_members=deepcopy(catalog['members']);original_children={r['ID']:deepcopy(r)
            for r in catalog['nodes'] if r['ParentID']=='1'}
        calls=[];edges=[];published=[]
        def apply(request):
            self.assertEqual(request['revision'],catalog['revision'])
            self.assertEqual(read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits'][0]['request'],request)
            old=catalog['revision'];target=next(r for r in catalog['nodes'] if r['ID']=='1')
            if not calls:
                self.assertEqual((request['source_parent'],request['parent']),('root','3'))
                next(r for r in catalog['nodes'] if r['ID']=='4')['Seq']=1
                target.update(ParentID='3',Seq=2)
            else:
                self.assertEqual((request['source_parent'],request['parent']),('3','7'))
                target.update(ParentID='7',Seq=1)
            catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            calls.append(deepcopy(request));edges.append((old,catalog['revision']))
            return deepcopy(catalog)
        self.controller.factory=lambda library:SimpleNamespace(apply=apply,committed_transition=lambda request:edges[-1])
        publish=w._native_apply_catalog
        def acknowledged(current,request):
            saved=read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits']
            self.assertFalse(any(job['request']['operation_id']==request['operation_id'] for job in saved))
            if saved:self.assertEqual(saved[0]['request']['revision'],current['revision'])
            published.append(request['operation_id']);publish(current,request)
        with mock.patch.object(self.controller,'node_move_reason',return_value=''):
            self.assertTrue(w._native_move('1','3'));self.assertTrue(w._native_move('1','7'))
            before=self.case.durable()
            with (mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'),
                  mock.patch.object(w.tag_controller,'_recover_pending'),
                  mock.patch.object(w.tag_controller,'run_playlist_job',side_effect=lambda work,done:
                      w.tag_controller._run_source_job('playlists',work,done)),
                  mock.patch.object(w,'_native_apply_catalog',side_effect=acknowledged)):
                self.controller.pump()
                self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual(len(calls),2);self.assertEqual(len(published),2)
        self.assertNotEqual(calls[1]['revision'],before[1]['request']['revision'])
        self.assertEqual(calls[1]['operation_id'],before[1]['request']['operation_id'])
        self.assertEqual(catalog['members'],original_members)
        self.assertEqual({r['ID']:r for r in catalog['nodes'] if r['ParentID']=='1'},original_children)
        self.assertEqual(w.lib.source_playlist_catalog,catalog)
        self.assertEqual(next(p for p in w.lib.playlists if p.pid=='2').folder_path,['Empty','Folder'])
        self.assertEqual(next(p for p in w.lib.playlists if p.pid=='2').keys,['t','u'])
        self.assertEqual(self.case.durable(),[])


if __name__=='__main__':unittest.main()
