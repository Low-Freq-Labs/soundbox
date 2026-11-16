"""Durable native gestures on the shared worker owner, with no live source I/O."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication,QFrame,QTreeWidgetItem

from desktop.rekordbox_playlists import NativePlaylistsMixin,PlaylistEditController,request_for,QUEUE
from desktop.tag_controller import TagEditController
from desktop.trees import RB_NODE_ROLE
from soundbox.library import Library,Playlist,Track
from soundbox import engine,rekordbox_playlist_plan as planner
from soundbox.rekordbox_write import ConflictError,WriteProofError


def native_catalog(name='List'):
    node=dict(ID='1000',UUID='00000000-0000-4000-8000-000000000001',Name=name,ParentID='root',
              Seq=1,Attribute=0,rb_local_deleted=0)
    result=dict(nodes=[node],members=[]);result['revision']=planner.revision(result['nodes'],result['members'])
    return result


class Window(QFrame,NativePlaylistsMixin):
    def __init__(self,root):
        super().__init__();self.settings={};self.removals={};self._shutting_down=False
        self._state_maintenance=False;self._refresh_worker=None;self._view_key='rb:List'
        track=Track('one','One','Artist','file:///never-open/audio.wav',source_uuid='track-one')
        self.lib=Library({'one':track},[Playlist('List',[],[],'1000')],'/synthetic/master.db',
                         source_kind='rekordbox-db',source_library_id='synthetic',source_playlist_catalog=native_catalog())
        self.tag_controller=TagEditController(root,self,store_factory=lambda lib:None)
        self.tag_controller.lib=self.lib;self.tag_controller.verified=True
        self.tag_controller._recover_pending=mock.Mock()
        self._save_state=mock.Mock(return_value=True);self._flash_status=mock.Mock()
        self._native_refresh_view=mock.Mock();self._device_operation_owned=lambda:False

    def _apply_settings_to_lib(self, *, invalidate_rows=True, configure_artwork=True):
        self.lib.removed_by_playlist=copy.deepcopy(self.removals)


class NativePlaylistGestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name).resolve()
        self.window=Window(self.root);self.catalog=copy.deepcopy(self.window.lib.source_playlist_catalog)
        self.calls=[];self.error=''
        def apply(request):
            if self.error:raise ConflictError(self.error)
            self.assertEqual(request['revision'],self.catalog['revision'])
            self.calls.append(copy.deepcopy(request))
            self.catalog['nodes'][0]['Name']=request['name']
            self.catalog['revision']=planner.revision(self.catalog['nodes'],self.catalog['members'])
            return copy.deepcopy(self.catalog)
        def committed_transition(request):
            # This fake backend proves only its most recently applied edge.
            if self.calls and self.calls[-1]==request:
                return request['revision'],self.catalog['revision']
            return None
        self.backend=SimpleNamespace(apply=apply,committed_transition=committed_transition)
        self.controller=PlaylistEditController(self.window,store_factory=lambda lib:self.backend)
        self.window.playlist_controller=self.controller
        self.closed=mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed',return_value=None)
        self.closed.start()

    def tearDown(self):
        self.controller.stop();self.window.tag_controller.shutdown();self.closed.stop()
        self.window.close();self.window.deleteLater();self.app.processEvents();self.area.cleanup()

    def wait(self,predicate):
        elapsed=0
        while not predicate() and elapsed<3000:
            loop=QEventLoop();QTimer.singleShot(10,loop.quit);loop.exec();elapsed+=10
        self.assertTrue(predicate(),'Source worker did not retire')

    def rename(self,name):
        return request_for(self.window.lib.source_playlist_catalog,'rename',identity='1000',name=name)

    def test_incoming_duplicate_add_is_confirmed_and_declining_retains_one_occurrence(self):
        w=self.window;w._native_request=mock.Mock(return_value=True)
        for accepted in (False,True):
            w._confirm_duplicate_add=mock.Mock(return_value=accepted)
            w._native_append('1000',['one','one'])
            w._confirm_duplicate_add.assert_called_once_with('List',['one'])
            request=w._native_request.call_args.kwargs
            self.assertEqual(len(request['tracks']),2 if accepted else 1)
            self.assertEqual(request['allow_duplicate'],accepted)

    def test_duplicate_confirmation_cannot_redirect_append_to_reused_playlist_uuid(self):
        w=self.window;w._native_request=mock.Mock(return_value=True)
        def replace(_name,_ids):
            w.lib.source_playlist_catalog['nodes'][0]['UUID']='replacement-uuid'
            return True
        w._confirm_duplicate_add=replace
        w._native_append('1000',['one','one'])
        w._native_request.assert_not_called()
        self.assertIn('changed during confirmation',w._flash_status.call_args.args[0])

    def test_saved_native_result_publishes_only_after_worker_and_fifo_rebases_its_own_tail(self):
        self.assertTrue(self.controller.enqueue(self.rename('First')))
        self.assertTrue(self.controller.enqueue(self.rename('Second')))
        self.assertEqual(self.window.lib.playlists[0].name,'List')
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([r['name'] for r in self.calls],['First','Second'])
        self.assertEqual(self.window.lib.playlists[0].name,'Second')
        self.assertNotIn(QUEUE,self.window.settings) if QUEUE not in self.window.settings else self.assertEqual(self.window.settings[QUEUE],[])

    def test_smart_delete_is_separately_blocked_before_durable_queue_or_confirmation(self):
        w=self.window;node=w.lib.source_playlist_catalog['nodes'][0]
        node.update(Attribute=4,SmartList='<NODE Opaque="keep"/>',rb_data_status=0,
                    rb_local_data_status=0,rb_local_synced=0,usn=None,rb_local_usn=1)
        request=request_for(w.lib.source_playlist_catalog,'delete_smart',identity='1000')
        self.assertFalse(self.controller.enqueue(request))
        self.assertEqual(self.controller.pending,[])
        w._save_state.assert_not_called()
        with mock.patch('desktop.rekordbox_playlists.QMessageBox.question',
                        side_effect=AssertionError('Unaccepted deletion reached confirmation')):
            w._native_delete('1000')
        self.assertIn('separate acceptance',w._flash_status.call_args.args[0])

    def test_smart_delete_menu_keeps_rename_disabled_and_explains_separate_gate(self):
        w=self.window;node=w.lib.source_playlist_catalog['nodes'][0]
        node.update(Attribute=4,SmartList='<NODE Opaque="keep"/>')
        item=QTreeWidgetItem(['Smart']);item.setData(0,RB_NODE_ROLE,'1000')
        menu=w._native_menu(item)
        try:
            actions={action.text():action for action in menu.actions()}
            self.assertFalse(actions['Delete'].isEnabled())
            self.assertIn('separate acceptance',actions['Delete'].toolTip())
            self.assertFalse(actions['Rename'].isEnabled())
        finally:menu.deleteLater()

    def test_accepted_smart_delete_projects_only_exact_list_then_publishes_worker_catalog(self):
        w=self.window;nodes=w.lib.source_playlist_catalog['nodes'];target=nodes[0]
        target.update(Attribute=4,SmartList='<NODE Opaque="keep"/>',rb_data_status=0,
                      rb_local_data_status=0,rb_local_synced=0,usn=None,rb_local_usn=1)
        following={**target,'ID':'3000','UUID':'00000000-0000-4000-8000-000000003000',
                   'Seq':2,'Name':'Remaining','SmartList':'<NODE Other="keep"/>'}
        nodes.append(following)
        w.lib.source_playlist_catalog['revision']=planner.revision(nodes,[])
        w.lib.playlists=[Playlist('List',[],['one'],'1000'),Playlist('Remaining',[],['one'],'3000')]
        self.catalog=copy.deepcopy(w.lib.source_playlist_catalog)
        def remove(request):
            self.calls.append(copy.deepcopy(request))
            self.catalog['nodes']=[row for row in self.catalog['nodes'] if row['ID']!=request['playlist_id']]
            self.catalog['nodes'][0]['Seq']=1
            self.catalog['revision']=planner.revision(self.catalog['nodes'],[])
            return copy.deepcopy(self.catalog)
        self.backend.apply=remove
        request=request_for(w.lib.source_playlist_catalog,'delete_smart',identity='1000')
        with mock.patch.object(self.controller,'smart_delete_reason',return_value=''):
            self.assertTrue(self.controller.enqueue(request))
        self.assertEqual([p.pid for p in w.lib.playlists],['1000','3000'])
        projected=w._native_presentation()
        self.assertEqual([p.pid for p in projected.playlists],['3000'])
        self.assertEqual(projected.playlists[0].keys,['one'])
        self.assertEqual(set(projected.tracks_by_id),{'one'})
        self.wait(lambda:not self.controller.pending and not w.tag_controller.owned)
        self.assertEqual([p.pid for p in w.lib.playlists],['3000'])
        self.assertEqual(set(w.lib.tracks_by_id),{'one'})
        self.assertEqual(self.calls[0]['operation'],'delete_smart')
        w._native_refresh_view.assert_called_once()

    def test_synced_smart_menu_requires_new_profile_without_borrowing_local_acceptance(self):
        from soundbox.rekordbox_playlist_store import SMART_DELETE_PROFILE,SMART_LOCAL_DELETE_PROFILE
        node=self.window.lib.source_playlist_catalog['nodes'][0]
        node.update(Attribute=4,SmartList='<NODE Opaque="keep"/>',rb_data_status=0,
                    rb_local_data_status=0,rb_local_synced=1,usn=None,rb_local_usn=1)
        self.controller.factory=None
        authority=SimpleNamespace(smart_delete_accepted=True,smart_delete_profile=SMART_DELETE_PROFILE)
        with mock.patch('desktop.rekordbox_playlists.load_authority',return_value=authority):
            self.assertIn('profile',self.controller.smart_delete_reason('1000'))
            authority.smart_delete_profile=SMART_LOCAL_DELETE_PROFILE
            self.assertEqual(self.controller.smart_delete_reason('1000'),'')
            item=QTreeWidgetItem(['Smart']);item.setData(0,RB_NODE_ROLE,'1000')
            menu=self.window._native_menu(item)
            try:
                actions={action.text():action for action in menu.actions()}
                self.assertTrue(actions['Delete'].isEnabled())
                self.assertFalse(actions['Rename'].isEnabled())
            finally:menu.deleteLater()

    def test_delete_confirmation_and_inline_rename_cannot_retarget_replaced_uuid(self):
        w=self.window;original=w.lib.source_playlist_catalog['nodes'][0]
        def replace_during_confirmation(*_args):
            original['UUID']='00000000-0000-4000-8000-000000009999'
            from desktop.rekordbox_playlists import QMessageBox
            return QMessageBox.Yes
        with mock.patch('desktop.rekordbox_playlists.QMessageBox.question',side_effect=replace_during_confirmation), \
                mock.patch.object(self.controller,'enqueue') as enqueue:
            w._native_delete('1000')
            enqueue.assert_not_called()
        original['UUID']='00000000-0000-4000-8000-000000000001'
        callbacks=[];w.tree=SimpleNamespace(rename_inline=lambda _item,_name,callback:callbacks.append(callback))
        w._find_tree_item=mock.Mock(return_value=object())
        w._native_rename('1000')
        original['UUID']='00000000-0000-4000-8000-000000009999'
        with mock.patch.object(self.controller,'enqueue') as enqueue:
            callbacks[0]('Renamed')
            enqueue.assert_not_called()
        self.assertEqual(self.controller.pending,[])

    def test_create_dialog_keeps_original_destination_and_smart_shortcut_never_queues_false_rename(self):
        w=self.window;folder={**w.lib.source_playlist_catalog['nodes'][0], 'ID':'2000','Seq':2,'Attribute':1,
                             'UUID':'00000000-0000-4000-8000-000000002000'}
        w.lib.source_playlist_catalog['nodes'].append(folder)
        def replace_parent(*_args):
            folder['UUID']='00000000-0000-4000-8000-000000009999'
            return 'New playlist',True
        with mock.patch('desktop.rekordbox_playlists.QInputDialog.getText',side_effect=replace_parent), \
                mock.patch.object(self.controller,'enqueue') as enqueue:
            w._native_new('2000')
            enqueue.assert_not_called()
        w.lib.source_playlist_catalog['nodes'][0]['Attribute']=4
        w._find_tree_item=mock.Mock(side_effect=AssertionError('False smart rename editor'))
        w._native_rename('1000')
        self.assertEqual(self.controller.pending,[])

    def test_analysis_turn_allows_one_native_fifo_head_and_retains_the_next(self):
        from types import MethodType
        from desktop.analysis_priority import AnalysisPriorityMixin
        w=self.window;tags=w.tag_controller
        w._analysis_worker=None;w._analysis_priority_pending=lambda:True
        w._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,w)
        w._analysis_source_admitted=MethodType(AnalysisPriorityMixin._analysis_source_admitted,w)
        tags.analysis_priority=w._analysis_priority_pending
        tags.analysis_admission=w._analysis_source_priority;tags.source_admitted=w._analysis_source_admitted
        self.controller.enqueue(self.rename('First'));self.controller.enqueue(self.rename('Second'))
        self.app.processEvents();self.assertEqual(self.calls,[])
        AnalysisPriorityMixin._analysis_offer_source_turn(w,w.lib)
        self.wait(lambda:len(self.calls)==1 and not tags.owned)
        for _ in range(3):self.controller.pump();self.app.processEvents()
        self.assertEqual([r['name'] for r in self.calls],['First'])
        self.assertEqual(self.controller.pending[0]['request']['name'],'Second')
        self.assertTrue(w._analysis_source_priority())
        AnalysisPriorityMixin._analysis_offer_source_turn(w,w.lib)
        self.wait(lambda:not self.controller.pending and not tags.owned)
        self.assertEqual([r['name'] for r in self.calls],['First','Second'])

    def test_rotating_native_playlist_queue_runs_between_continuous_cue_admissions(self):
        from types import MethodType
        from desktop.analysis_priority import AnalysisPriorityMixin
        w=self.window;tags=w.tag_controller;w._analysis_worker=None
        w._analysis_priority_pending=lambda:True
        w._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,w)
        tags.analysis_priority=w._analysis_priority_pending;tags.analysis_admission=w._analysis_source_priority
        tags.source_admitted=MethodType(AnalysisPriorityMixin._analysis_source_admitted,w)
        cues=[]
        def cue():
            if tags._analysis_blocks_source() or tags.source_job_owned:return
            tags._run_source_job('cues',lambda:cues.append(True),lambda _worker:None)
        tags._start_next_cue=cue;tags._cue_queue=[SimpleNamespace(page='hot')]
        try:
            self.controller.enqueue(self.rename('First'));self.controller.enqueue(self.rename('Second'))
            for index in range(4):
                AnalysisPriorityMixin._analysis_offer_source_turn(w,w.lib)
                self.wait(lambda:not tags.busy)
                self.assertEqual((len(cues),len(self.calls)),((index+2)//2,(index+1)//2))
                self.assertTrue(tags.owned)
            self.assertEqual([r['name'] for r in self.calls],['First','Second'])
        finally:tags._cue_queue=[]

    def test_verified_fifo_completion_finishes_status_after_busy_banner(self):
        self.window._finish_edit_status=mock.Mock()
        active=[]
        self.window.tag_controller.changed.connect(lambda:active.append(self.window.tag_controller.update_status_text))
        self.controller.enqueue(self.rename('Saved'))
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertIn('Updating playlist…',active)
        self.assertNotIn('Updating rekordbox playlists…',active)
        self.window._finish_edit_status.assert_called_once_with(quiet=True)
        self.assertEqual(self.window.tag_controller.update_status_text,'')

    def test_local_persistence_failure_does_not_admit_native_worker_or_hide_a_track(self):
        before=copy.deepcopy(self.window.settings);self.window._save_state.return_value=False
        with mock.patch.object(self.window.tag_controller,'run_playlist_job') as worker:
            self.assertFalse(self.controller.enqueue(self.rename('Denied')))
            worker.assert_not_called()
        self.assertEqual(self.window.settings,before);self.assertEqual(self.window.removals,{})

    def test_failed_admission_restores_existing_empty_queue_owner(self):
        queue=[];self.window.settings[QUEUE]=queue
        request=self.rename('Denied');before=copy.deepcopy(request)
        self.window._save_state.return_value=False
        self.assertFalse(self.controller.enqueue(request))
        self.assertIs(self.window.settings[QUEUE],queue)
        self.assertEqual(request,before);self.assertEqual(self.calls,[])

    def test_source_failure_stays_visible_pending_without_soundbox_veto(self):
        self.error='Native selection changed';self.controller.enqueue(self.rename('Denied'))
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(self.window.lib.playlists[0].name,'List')
        self.assertEqual(self.window.removals,{})
        self.assertIn('Native selection changed',self.controller.pending[0]['error'])
        self.assertEqual(self.calls,[])

    def test_open_rekordbox_waits_durably_without_repeated_immediate_worker_launch(self):
        with mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')):
            self.controller.enqueue(self.rename('Later'))
            self.wait(lambda:self.controller.not_before>0 and not self.window.tag_controller.owned)
        self.assertEqual(len(self.controller.pending),1);self.assertEqual(self.calls,[])
        self.controller.not_before=0;self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.window.lib.playlists[0].name,'Later')

    def test_explicit_retry_persists_the_renewed_identity_before_second_publication(self):
        from uuid import uuid4
        self.error='Native selection changed';self.controller.enqueue(self.rename('Retry'))
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        old=copy.deepcopy(self.controller.pending[0]['request'])
        renewed={**old,'operation_id':uuid4().hex}
        self.backend.prepare_retry=mock.Mock(return_value=renewed)
        original=self.backend.apply
        def apply(request):
            self.assertEqual(self.controller.pending[0]['request'],renewed)
            self.assertFalse(self.controller.pending[0]['retry'])
            return original(request)
        self.backend.apply=apply;self.error='';self.controller.retry()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.backend.prepare_retry.assert_called_once_with(old)
        self.assertEqual([r['operation_id'] for r in self.calls],[renewed['operation_id']])

    def test_discard_requires_concrete_confirmation_and_preserves_source_and_filters(self):
        self.error='Conflict';self.controller.enqueue(self.rename('Never saved'))
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        before=copy.deepcopy(self.window.settings)
        from desktop.rekordbox_playlists import QMessageBox
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.controller.discard()
        self.assertEqual(self.window.settings,before)
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.controller.discard()
        self.assertEqual(self.controller.pending,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.window.lib.playlists[0].name,'List');self.assertEqual(self.window.removals,{})

    def test_invalid_saved_queue_is_preserved_and_cannot_admit_a_worker(self):
        self.window.settings[QUEUE]=[{'request':{'operation_id':'../../foreign'}}]
        before=copy.deepcopy(self.window.settings)
        self.controller.pump();self.controller.schedule()
        self.assertEqual(self.window.settings,before);self.assertEqual(self.calls,[])
        self.assertIn('invalid',self.window.tag_controller.playlist_update_status)

    def test_native_removal_gesture_does_not_modify_legacy_local_removal_filters(self):
        self.window.removals={'older':{'old-track':'original date'}}
        with mock.patch.object(self.controller,'enqueue',return_value=True) as save:
            self.window._native_remove('List',['one'])
        request=save.call_args.args[0]
        self.assertEqual(request['operation'],'remove');self.assertEqual(request['playlist_id'],'1000')
        self.assertEqual(request['tracks'][0]['track_id'],'one')
        self.assertEqual(self.window.removals,{'older':{'old-track':'original date'}})

    def test_same_named_native_lists_target_stable_selected_id(self):
        self.window.lib.playlists.append(Playlist('List',[],[],'2000'))
        self.window.lib.source_playlist_catalog['nodes'].append(dict(
            self.window.lib.source_playlist_catalog['nodes'][0],ID='2000',Seq=2,
            UUID='00000000-0000-4000-8000-000000000002'))
        self.window._active_native_playlist_id='2000'
        with mock.patch.object(self.controller,'enqueue',return_value=True) as save:
            self.window._native_remove('List',['one'])
        self.assertEqual(save.call_args.args[0]['playlist_id'],'2000')

    def test_empty_native_folders_render_and_context_menu_order_is_explicit(self):
        self.window.lib.source_playlist_catalog['nodes'].append(dict(
            self.window.lib.source_playlist_catalog['nodes'][0],ID='2000',Seq=2,Name='Empty',Attribute=1,
            UUID='00000000-0000-4000-8000-000000000002'))
        tree=engine.playlist_tree(self.window.lib)
        self.assertEqual(tree['children'][1],dict(name='Empty',type='folder',pid='2000',children=[]))
        item=QTreeWidgetItem(['Empty']);item.setData(0,RB_NODE_ROLE,'2000')
        menu=self.window._native_menu(item)
        self.assertEqual([a.text() for a in menu.actions()],['New Playlist','New Smart Playlist','New Folder','Rename','Delete'])
        self.assertTrue(all(a.isEnabled() for a in menu.actions()));menu.deleteLater()

    def test_removing_final_native_reference_pins_exact_uuid_without_registering_audio(self):
        self.window.lib.playlists[0].keys=['one'];request=self.rename('unused')
        request['operation']='remove';request['tracks']=[dict(track_id='one')]
        self.window._native_apply_catalog(copy.deepcopy(self.catalog),request)
        self.assertIn('one',self.window.lib.tracks_by_id)
        self.assertEqual(self.window.settings['rekordbox_collection_tracks'],{'synthetic':{'one':'track-one'}})
