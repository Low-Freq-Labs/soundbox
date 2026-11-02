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

    def _apply_settings_to_lib(self):
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
        self.backend=SimpleNamespace(apply=apply)
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

    def test_saved_native_result_publishes_only_after_worker_and_fifo_rebases_its_own_tail(self):
        self.assertTrue(self.controller.enqueue(self.rename('First')))
        self.assertTrue(self.controller.enqueue(self.rename('Second')))
        self.assertEqual(self.window.lib.playlists[0].name,'List')
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([r['name'] for r in self.calls],['First','Second'])
        self.assertEqual(self.window.lib.playlists[0].name,'Second')
        self.assertNotIn(QUEUE,self.window.settings) if QUEUE not in self.window.settings else self.assertEqual(self.window.settings[QUEUE],[])

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
        self.assertEqual([a.text() for a in menu.actions()],['New Playlist','New Folder','Rename','Delete'])
        self.assertTrue(all(a.isEnabled() for a in menu.actions()));menu.deleteLater()

    def test_removing_final_native_reference_pins_exact_uuid_without_registering_audio(self):
        self.window.lib.playlists[0].keys=['one'];request=self.rename('unused')
        request['operation']='remove';request['tracks']=[dict(track_id='one')]
        self.window._native_apply_catalog(copy.deepcopy(self.catalog),request)
        self.assertIn('one',self.window.lib.tracks_by_id)
        self.assertEqual(self.window.settings['rekordbox_collection_tracks'],{'synthetic':{'one':'track-one'}})
