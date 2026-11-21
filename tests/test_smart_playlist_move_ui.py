"""Smart reparent gestures use exact source custody and durable native FIFO."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import engine, rekordbox_playlist_plan as planner
from desktop.rekordbox_playlists import _intent_xml_clocks
from soundbox.rekordbox_playlist_store import request_for
from soundbox.library import Playlist
from soundbox.rekordbox_playlist_move import context as move_context
from soundbox.state_repository import read_state
from tests import test_playlist_presentation as fixture


class SmartMovePublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.PlaylistPresentation.setUpClass()

    def setUp(self):
        case=fixture.PlaylistPresentation();case.setUp();self.addCleanup(case.doCleanups)
        self.case=case;self.w=case.win;self.controller=case.controller
        w=self.w;catalog=deepcopy(w.lib.source_playlist_catalog)
        for row in catalog['nodes']:
            row.update(rb_data_status=0,rb_local_data_status=0,rb_local_synced=1,
                       rb_local_usn=10,usn=None,SmartList=None,
                       created_at='2026-11-19 00:00:00.000 +00:00',
                       updated_at='2026-11-19 00:00:00.000 +00:00')
        target=catalog['nodes'][1]
        target.update(Attribute=4,Name='Smart',Seq=2,rb_local_synced=0,
                      SmartList='<NODE Id="2"><CONDITION PropertyName="myTag" ValueLeft="201"/></NODE>')
        # A move preserves the old parent's gaps, but normalizes the appended
        # destination order. These are intentionally not ordinary reorder rows.
        for identity,name,parent,seq,attribute in (('5','Before','1',1,0),
                ('6','After','1',4,0),('7','Empty','root',3,1)):
            catalog['nodes'].append({**catalog['nodes'][0], 'ID':identity,
                'UUID':f'00000000-0000-4000-8000-{int(identity):012d}',
                'Name':name,'ParentID':parent,'Seq':seq,'Attribute':attribute})
        catalog['nodes'][3]['Seq']=8
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        w.lib=replace(w.lib,source_playlist_catalog=catalog,playlists=[
            Playlist('Smart',['Folder'],['t','u'],'2'),Playlist('Other',['Keep'],['t'],'4'),
            Playlist('Before',['Folder'],['t'],'5'),Playlist('After',['Folder'],['u'],'6')])
        w.tag_controller.configure(w.lib,True)
        w._populate_tree(engine.summary(w.lib))
        w.tree.setCurrentItem(w._find_tree_item('Folder / Smart','2'))
        w._reload_current_view()
        self.assertTrue(w._save_state())

    def assert_source_unchanged(self, before):
        self.assertEqual((self.w.lib.source_playlist_catalog,self.w.lib.playlists),before)

    def test_default_off_move_is_refused_before_ram_canonical_or_source_admission(self):
        w=self.w;before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        saved=read_state(w._state_repository.path)
        self.assertTrue(self.controller.smart_move_reason())
        self.assertFalse(w._native_move('2','3'))
        self.assertEqual(self.controller.pending,[])
        self.assertEqual(read_state(w._state_repository.path),saved)
        self.assert_source_unchanged(before);self.case.source.assert_not_called()

    def test_move_preview_keeps_selection_membership_and_source_gaps_until_durable_ack(self):
        w=self.w;w.table.selectAll();seat=w._browser_seat()
        before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        saved=read_state(w._state_repository.path)
        with mock.patch.object(self.controller,'smart_edit_reason',return_value=''):
            with self.case.held_canonical() as (entered,release):
                self.assertTrue(w._native_move('2','3'));self.assertTrue(entered.wait(2))
                self.assertEqual(w._view_key,'rb:Keep / Smart')
                self.assertIsNotNone(w._find_tree_item('Keep / Smart','2'))
                self.assertEqual(w._browser_seat(),seat)
                self.assertEqual(w._native_view_playlist('Keep / Smart').keys,['t','u'])
                projected={row['ID']:row for row in w._native_presentation().source_playlist_catalog['nodes']}
                self.assertEqual({key:projected[key]['Seq'] for key in ('5','6')},{'5':1,'6':4})
                self.assertEqual((projected['4']['Seq'],projected['2']['Seq']),(1,2))
                self.assertEqual(read_state(w._state_repository.path),saved)
                self.assert_source_unchanged(before);self.case.source.assert_not_called()
                release.set()
            canonical=self.case.durable()
        self.assertEqual(canonical,self.controller.pending)
        self.assertEqual(canonical[0]['request']['operation'],'move_smart')
        self.assert_source_unchanged(before)

    def test_empty_folder_then_root_moves_keep_exact_request_bindings_and_source_truth(self):
        w=self.w;before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        with mock.patch.object(self.controller,'smart_edit_reason',return_value=''):
            self.assertTrue(w._native_move('2','7'))
            self.assertEqual(w._view_key,'rb:Empty / Smart')
            self.assertTrue(w._native_move('2','root'))
            self.assertEqual(w._view_key,'rb:Smart')
            queue=self.case.durable()
        self.assertEqual([job['request']['parent'] for job in queue],['7','root'])
        self.assertEqual([job['request']['source_parent'] for job in queue],['1','7'])
        self.assertEqual(queue[0]['request']['playlist_uuid'],before[0]['nodes'][1]['UUID'])
        self.assertEqual(queue[1]['request']['source_parent_uuid'],queue[0]['request']['parent_uuid'])
        self.assertEqual(w._native_view_playlist('Smart').keys,['t','u'])
        self.assert_source_unchanged(before)

    def test_failed_canonical_move_retains_request_but_does_not_publish_source_or_export_library(self):
        w=self.w;before=deepcopy((w.lib.source_playlist_catalog,w.lib.playlists))
        with (mock.patch.object(self.controller,'smart_edit_reason',return_value=''),
              mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('Generated volume full'))):
            self.assertTrue(w._native_move('2','3'))
            request=deepcopy(self.controller.pending[0]['request']);self.case.failed_admission()
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertEqual(w._native_presentation().source_playlist_catalog,before[0])
        self.assert_source_unchanged(before);self.case.discard()
        self.assertIsNotNone(w._find_tree_item('Folder / Smart','2'))

    def test_replaced_destination_uuid_or_source_rule_cannot_project_saved_move(self):
        w=self.w
        with mock.patch.object(self.controller,'smart_edit_reason',return_value=''):
            self.assertTrue(w._native_move('2','3'));self.case.durable()
        request=deepcopy(self.controller.pending[0]['request'])
        for identity,field,value in (('3','UUID','00000000-0000-4000-8000-000000000099'),
                                     ('2','SmartList','<NODE Changed="rule"/>')):
            with self.subTest(field=field):
                row=next(row for row in w.lib.source_playlist_catalog['nodes'] if row['ID']==identity)
                old=row[field];row[field]=value
                try:
                    projected=w._native_presentation()
                    target=next(row for row in projected.source_playlist_catalog['nodes'] if row['ID']=='2')
                    self.assertEqual(target['ParentID'],'1')
                    self.assertEqual(self.controller.pending[0]['request'],request)
                finally:row[field]=old

    def test_worker_ack_rebases_queued_move_and_publishes_only_after_exact_canonical_ack(self):
        w=self.w;catalog=deepcopy(w.lib.source_playlist_catalog)
        original_members=deepcopy(catalog['members']);calls=[];edges=[];published=[]
        def apply(request):
            self.assertEqual(request['revision'],catalog['revision'])
            self.assertEqual(read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits'][0]['request'],request)
            old=catalog['revision'];movement=move_context(catalog['nodes'],
                identity=request['playlist_id'],parent=request['parent'])
            self.assertEqual(movement['target']['ParentID'],request['source_parent'])
            for row in movement['destination_siblings']:
                row['Seq']=movement['positions'][row['ID']]
            movement['target'].update(ParentID=request['parent'],Seq=movement['positions'][request['playlist_id']])
            catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
            calls.append(deepcopy(request));edges.append((old,catalog['revision']))
            return deepcopy(catalog)
        backend=SimpleNamespace(apply=apply,committed_transition=lambda request:edges[-1])
        self.controller.factory=lambda library:backend
        publish=w._native_apply_catalog
        def acknowledged(current,request):
            saved=read_state(w._state_repository.path)['settings']['rekordbox_playlist_edits']
            self.assertFalse(any(job['request']['operation_id']==request['operation_id'] for job in saved))
            if saved:self.assertEqual(saved[0]['request']['revision'],current['revision'])
            published.append(request['operation_id']);publish(current,request)
        with mock.patch.object(self.controller,'smart_edit_reason',return_value=''):
            self.assertTrue(w._native_move('2','3'));self.assertTrue(w._native_move('2','root'))
            before=self.case.durable()
            with (mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'),
                  mock.patch.object(w.tag_controller,'_recover_pending'),
                  mock.patch.object(w.tag_controller,'run_playlist_job',side_effect=lambda work,done:
                      w.tag_controller._run_source_job('playlists',work,done)),
                  mock.patch.object(w,'_native_apply_catalog',side_effect=acknowledged)):
                self.controller.pump()
                self.case.wait(lambda:not self.controller.pending and not self.controller.admission_pending)
        self.assertEqual(len(calls),2);self.assertEqual(len(published),2)
        self.assertEqual([request['source_parent'] for request in calls],['1','3'])
        self.assertEqual([request['parent'] for request in calls],['3','root'])
        self.assertNotEqual(calls[1]['revision'],before[1]['request']['revision'])
        self.assertEqual(calls[1]['operation_id'],before[1]['request']['operation_id'])
        self.assertEqual(catalog['members'],original_members)
        self.assertEqual(w.lib.source_playlist_catalog,catalog)
        self.assertEqual(next(p.keys for p in w.lib.playlists if p.pid=='2'),['t','u'])
        self.assertEqual(self.case.durable(),[])

    def test_fifo_clock_chain_advances_only_target_and_destination_xml(self):
        request=request_for(self.w.lib.source_playlist_catalog,'move_smart',identity='2',parent='3')
        before=deepcopy(request['smart_move_clocks'])
        target_clock=request['xml_timestamp']+100
        parent_clock=request['smart_move_clocks']['parent_xml_timestamp']+200
        clocks={'2':target_clock,'3':parent_clock,'1':parent_clock+1000}
        _intent_xml_clocks(request,clocks,advance=True)
        self.assertEqual(request['xml_timestamp'],target_clock+1)
        self.assertEqual(request['smart_move_clocks']['parent_xml_timestamp'],
                         max(parent_clock+1,target_clock+1))
        self.assertEqual(clocks['1'],parent_clock+1000)
        self.assertEqual({key:value for key,value in request['smart_move_clocks'].items()
                          if key!='parent_xml_timestamp'},
                         {key:value for key,value in before.items() if key!='parent_xml_timestamp'})


if __name__=='__main__':unittest.main()
