"""Persisted playlist feedback with an actual isolated desktop sidebar."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.rekordbox_playlists import PlaylistEditController, QUEUE
from soundbox import engine, rekordbox_playlist_plan as planner
from soundbox.library import Library, Playlist, Track
from soundbox.state_repository import StateRepository


class PlaylistPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False), \
                mock.patch.object(MainWindow,'_refresh_devices'):
            self.win=MainWindow(isolated_dir=Path(self.area.name))
        self.win.playlist_controller.stop()
        self.controller=PlaylistEditController(self.win,store_factory=lambda lib:None)
        self.win.playlist_controller=self.controller
        self.win._start_rekordbox_refresh=mock.Mock()
        def node(identity,name,parent,seq,folder=False):
            return dict(ID=identity,UUID=f'00000000-0000-4000-8000-{int(identity):012d}',
                        Name=name,ParentID=parent,Seq=seq,Attribute=int(folder),rb_local_deleted=0)
        nodes=[node('1','Folder','root',1,True),node('2','Delete','1',1),
               node('3','Keep','root',2,True),node('4','Other','3',1)]
        members=[dict(ID='member-'+pid,UUID='member-uuid-'+pid,PlaylistID=pid,ContentID='42',TrackNo=1,rb_local_deleted=0) for pid in ('2','4')]
        catalog=dict(nodes=nodes,members=members,revision=planner.revision(nodes,members))
        tracks={'t':Track('t','Synthetic','','file:///never-open/synthetic.wav',bpm='120',source_native_id='42',
                        source_uuid='00000000-0000-4000-8000-000000000042'),
                'u':Track('u','Second','','file:///never-open/second.wav',bpm='120',source_native_id='43',
                        source_uuid='00000000-0000-4000-8000-000000000043')}
        self.win.lib=Library(tracks,[Playlist('Delete',['Folder'],['t'],'2'),
            Playlist('Other',['Keep'],['t'],'4')],'/synthetic/master.db',
            source_kind='rekordbox-db',source_library_id='synthetic',source_playlist_catalog=catalog)
        self.win.tag_controller.configure(self.win.lib,True)
        self.win.tag_controller.busy=True  # deterministic delayed publication; no native API can run
        self.win._populate_tree(engine.summary(self.win.lib))
        self.win.tree.topLevelItem(1).setExpanded(True)
        self.win.tree.setCurrentItem(self.win._find_tree_item('Folder / Delete','2'))
        self.addCleanup(self.close)

    def close(self):
        self.controller.stop();self.win.tag_controller.busy=False
        self.win.shutdown();self.win.close();self.win.deleteLater();self.app.processEvents()

    def test_delete_is_immediate_durable_and_refresh_cannot_resurrect_it(self):
        before=deepcopy(self.win.lib.source_playlist_catalog)
        self.win._view_memory={'sb:Collection':('t',0)}
        started=time.monotonic()
        with mock.patch.object(self.win,'_show_collection',wraps=self.win._show_collection) as render:
            self.assertTrue(self.win._native_request('delete',identity='1'))
            self.assertEqual(render.call_count,1)
        elapsed=(time.monotonic()-started)*1000
        self.assertIsNone(self.win._find_tree_item('Folder / Delete','2'))
        self.assertEqual(self.win._view_key,'sb:Collection')
        self.assertEqual(self.win._view_memory['sb:Collection'],('t',0))
        self.assertEqual({r['id'] for r in self.win.model._rows},{'t','u'})
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.assertEqual([p.pid for p in self.win.lib.playlists],['2','4'])
        self.assertEqual(self.win.removals,{})
        self.assertTrue(self.win._find_tree_item('', '3').isExpanded())
        state=StateRepository(self.win._state_repository.path).load()
        self.assertEqual(state['settings'][QUEUE],self.controller.pending)
        self.win._populate_tree(engine.summary(self.win.lib))
        self.assertIsNone(self.win._find_tree_item('Folder / Delete','2'))
        self.assertEqual(self.controller.pending[0]['error'],'')
        print(f'persisted folder-delete feedback: {elapsed:.2f} ms')

    def test_failure_and_discard_reconcile_newest_catalog_and_retry_reprojects(self):
        self.assertTrue(self.win._native_request('delete',identity='1'))
        self.win.lib.source_playlist_catalog['nodes'][1]['Name']='External'
        self.win.lib.playlists[0].name='External'
        self.controller.fail('Source changed')
        self.assertTrue(self.win._refresh_pending)
        with (
                mock.patch.object(self.win,'_apply_loaded_library'),
                mock.patch.object(self.win,'_refresh_devices'),
                mock.patch.object(self.win,'_cache_library'),
                mock.patch.object(self.win,'_arm_waveform_rechecks'),
                mock.patch.object(self.win,'_sync_rekordbox_connection'),
                mock.patch.object(self.win,'_stop_pulse'),
                mock.patch.object(self.win,'_flash_status') as completion,
                mock.patch('desktop.app.QTimer.singleShot')):
            self.win._on_refresh_done(self.win.lib)
            completion.assert_not_called()
        self.assertIsNotNone(self.win._find_tree_item('Folder / External','2'))
        self.assertTrue(self.win._find_tree_item('', '3').isExpanded())
        self.controller.retry()
        self.assertIsNone(self.win._find_tree_item('Folder / External','2'))
        self.win.tag_controller.busy=False
        with mock.patch('desktop.rekordbox_playlists.QMessageBox.question',return_value=QMessageBox.Yes):
            self.controller.discard()
        self.assertEqual(self.controller.pending,[])
        self.win._start_rekordbox_refresh.assert_called_once_with(Path('/synthetic/master.db'))
        self.assertIsNotNone(self.win._find_tree_item('Folder / External','2'))
        self.assertEqual(self.win.removals,{})

    def test_failed_admission_keeps_row_and_identity_mismatch_does_not_hide_a_new_song_list(self):
        with mock.patch.object(self.win,'_save_state',return_value=False):
            self.assertFalse(self.win._native_request('delete',identity='1'))
        self.assertIsNotNone(self.win._find_tree_item('Folder / Delete','2'))
        self.assertTrue(self.win._native_request('delete',identity='1'))
        self.win.lib.source_playlist_catalog['nodes'][0]['UUID']='00000000-0000-4000-8000-999999999999'
        self.win._populate_tree(engine.summary(self.win.lib))
        self.assertIsNotNone(self.win._find_tree_item('Folder / Delete','2'))

    def test_create_rename_and_append_are_immediate_and_durable_before_publication(self):
        before=deepcopy(self.win.lib.source_playlist_catalog)
        self.assertTrue(self.win._native_request('create',name='New folder',folder=True))
        folder=self.controller.pending[-1]['request']['playlist_id']
        self.assertIsNotNone(self.win._find_tree_item('',folder))
        self.assertTrue(self.win._native_request('create',name='New set',parent=folder))
        identity=self.controller.pending[-1]['request']['playlist_id']
        self.assertIsNotNone(self.win._find_tree_item('New folder / New set',identity))
        self.assertEqual(self.win._active_native_playlist_id,identity)
        self.assertTrue(self.win._native_request('rename',identity=folder,name='Renamed folder'))
        self.assertEqual(self.win._view_key,'rb:Renamed folder / New set')
        self.assertIsNotNone(self.win._find_tree_item('Renamed folder / New set',identity))
        self.win._native_append(identity,['u','t'])
        self.assertEqual([r['id'] for r in self.win.model._rows],['u','t'])
        state=StateRepository(self.win._state_repository.path).load()
        self.assertEqual(state['settings'][QUEUE],self.controller.pending)
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.assertEqual([p.pid for p in self.win.lib.playlists],['2','4'])
        # A fresh snapshot/rebuilt sidebar must retain the pending view.
        self.win._populate_tree(engine.summary(self.win.lib))
        self.assertEqual(self.win._view_key,'rb:Renamed folder / New set')
        self.assertEqual([r['id'] for r in self.win.model._rows],['u','t'])
        self.assertEqual(len(self.controller.pending),4)

    def test_membership_feedback_preserves_order_and_source_rows_until_save(self):
        before=deepcopy(self.win.lib.source_playlist_catalog)
        self.win._native_append('2',['u'])
        self.assertEqual([r['id'] for r in self.win.model._rows],['t','u'])
        self.win._native_remove('Folder / Delete',['t'])
        self.assertEqual([r['id'] for r in self.win.model._rows],['u'])
        self.assertEqual(self.win.lib.playlists[0].keys,['t'])
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.controller.fail('Synthetic source conflict')
        self.assertEqual([r['id'] for r in self.win.model._rows],['t'])
        self.controller.retry()
        self.assertEqual([r['id'] for r in self.win.model._rows],['u'])
        self.win.tag_controller.busy=False
        with mock.patch('desktop.rekordbox_playlists.QMessageBox.question',return_value=QMessageBox.Yes):
            self.controller.discard()
        self.assertEqual([r['id'] for r in self.win.model._rows],['t'])
        self.assertEqual(self.win.lib.source_playlist_catalog,before)

    def test_failed_create_save_and_foreign_or_newer_source_do_not_project(self):
        with mock.patch.object(self.win,'_save_state',return_value=False):
            self.assertFalse(self.win._native_request('create',name='Not saved'))
        self.assertEqual(self.controller.pending,[])
        self.assertTrue(self.win._native_request('rename',identity='2',name='Queued name'))
        self.assertIsNotNone(self.win._find_tree_item('Folder / Queued name','2'))
        self.win.lib.source_playlist_catalog['revision']='f'*64
        self.win._native_pending_changed(rebuild=True)
        self.assertIsNotNone(self.win._find_tree_item('Folder / Delete','2'))
        self.assertEqual(len(self.controller.pending),1)

    def test_deleting_a_not_yet_published_creation_restores_collection_immediately(self):
        self.assertTrue(self.win._native_request('create',name='Temporary set'))
        identity=self.controller.pending[-1]['request']['playlist_id']
        self.assertEqual(self.win._active_native_playlist_id,identity)
        self.assertTrue(self.win._native_request('delete',identity=identity))
        self.assertIsNone(self.win._find_tree_item('',identity))
        self.assertEqual(self.win._view_key,'sb:Collection')
        self.assertEqual({r['id'] for r in self.win.model._rows},{'t','u'})
        self.assertEqual([j['request']['operation'] for j in self.controller.pending],['create','delete'])
        self.assertEqual([p.pid for p in self.win.lib.playlists],['2','4'])
