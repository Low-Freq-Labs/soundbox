"""Persisted playlist feedback with an actual isolated desktop sidebar."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QItemSelectionModel

from desktop.rekordbox_playlists import PlaylistEditController, QUEUE
from soundbox import engine, rekordbox_playlist_plan as planner
from soundbox.library import Library, Playlist, Track
from soundbox.state_repository import StateRepository
from soundbox.rekordbox_playlist_store import request_for


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
        self.win._find_tree_item('','3').setExpanded(True)
        self.win.tree.setCurrentItem(self.win._find_tree_item('Folder / Delete','2'))
        self.addCleanup(self.close)

    def close(self):
        self.controller.stop();self.win.tag_controller.busy=False
        self.win.shutdown();self.win.close();self.win.deleteLater();self.app.processEvents()

    def test_virtual_artists_stays_second_and_only_exact_generated_native_bindings_are_hidden(self):
        from soundbox import playlist_unification as merge
        from desktop.trees import COLLECTION_ROLE,ARTISTS_ROLE,PINNED_TREE_ROLE,RB_NODE_ROLE,NATIVE_TREE_ROLE
        state=merge.new(self.win.lib.source,self.win.lib.source_library_id)
        state['customs_done']=True
        state['nodes']['artists']=dict(id='3',uuid='00000000-0000-4000-8000-000000000003')
        self.win.settings[merge.KEY]=state
        self.win.lib.source_playlist_catalog['nodes'].append(dict(ID='5',UUID='00000000-0000-4000-8000-000000000005',
            Name='Artists',ParentID='root',Seq=3,Attribute=1,rb_local_deleted=0))
        catalog=self.win.lib.source_playlist_catalog;catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        before=deepcopy(self.win.lib.source_playlist_catalog)
        self.win._populate_tree(engine.summary(self.win.lib))
        first,second=(self.win.tree.topLevelItem(i) for i in (0,1))
        self.assertTrue(first.data(0,COLLECTION_ROLE))
        self.assertTrue(second.data(0,ARTISTS_ROLE))
        self.assertFalse(second.data(0,NATIVE_TREE_ROLE))
        self.assertIsNone(second.data(0,RB_NODE_ROLE))
        self.assertTrue(first.data(0,PINNED_TREE_ROLE))
        self.assertTrue(second.data(0,PINNED_TREE_ROLE))
        self.assertIsNone(self.win._find_tree_item('','3'))
        user_folder=self.win._find_tree_item('','5')
        self.assertIsNotNone(user_folder)
        self.assertFalse(user_folder.data(0,PINNED_TREE_ROLE))
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        # A replaced UUID is not the archived generated folder identity.
        catalog['nodes'][2]['UUID']='00000000-0000-4000-8000-000000000099'
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        self.win._populate_tree(engine.summary(self.win.lib))
        self.assertIsNotNone(self.win._find_tree_item('','3'))

    def test_two_thousand_virtual_artists_have_correct_count_without_native_jobs(self):
        from desktop.trees import ARTISTS_ROLE,COLLECTION_ROLE
        self.win.lib.tracks_by_id={str(i):Track(str(i),'Track '+str(i),'Artist '+str(i),
            'file:///never-open/'+str(i)+'.wav',bpm='120',total_time=200) for i in range(2000)}
        self.win._build_artist_registry();self.win._apply_settings_to_lib()
        before=deepcopy(self.win.lib.source_playlist_catalog)
        with mock.patch.object(self.controller,'enqueue') as enqueue:
            self.win._populate_tree(engine.summary(self.win.lib))
            self.win.unification_controller.pump()
        enqueue.assert_not_called()
        self.assertTrue(self.win.tree.topLevelItem(0).data(0,COLLECTION_ROLE))
        artists=self.win.tree.topLevelItem(1)
        self.assertTrue(artists.data(0,ARTISTS_ROLE));self.assertIn('(2000)',artists.text(0))
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.assertEqual(self.controller.pending,[])

    def test_native_folder_reorder_is_durable_without_switching_the_selected_playlist(self):
        from desktop.trees import RB_NODE_ROLE
        self.win._accordion_guard=True
        for identity in ('1','3'):self.win._find_tree_item('',identity).setExpanded(True)
        self.win._accordion_guard=False
        self.win.table.selectRow(0)
        seat=self.win._browser_seat();source=deepcopy(self.win.lib.source_playlist_catalog)
        self.assertTrue(self.win._native_reorder('3','1',False))
        roots=[self.win.tree.topLevelItem(i).data(0,RB_NODE_ROLE) for i in range(self.win.tree.topLevelItemCount())]
        self.assertLess(roots.index('3'),roots.index('1'))
        self.assertEqual(self.win._active_native_playlist_id,'2')
        self.assertEqual(self.win.tree.currentItem().data(0,RB_NODE_ROLE),'2')
        self.assertEqual(self.win._browser_seat(),seat)
        self.assertTrue(all(self.win._find_tree_item('',identity).isExpanded() for identity in ('1','3')))
        persisted=StateRepository(self.win._state_repository.path).load()['settings'][QUEUE]
        self.assertEqual(persisted,self.controller.pending)
        self.assertEqual(persisted[0]['request']['operation'],'reorder')
        self.assertEqual(self.win.lib.source_playlist_catalog,source)
        # A second drag uses the durable projected order; both intents survive.
        self.assertTrue(self.win._native_reorder('1','3',False))
        self.assertEqual(len(self.controller.pending),2)
        self.assertEqual(self.win._browser_seat(),seat)

    def test_virtual_artists_stays_fixed_and_failed_reorder_persistence_does_not_move_anything(self):
        from desktop.trees import RB_NODE_ROLE,ARTISTS_ROLE,PINNED_TREE_ROLE
        artists=self.win.tree.topLevelItem(1)
        self.assertTrue(artists.data(0,ARTISTS_ROLE));self.assertTrue(artists.data(0,PINNED_TREE_ROLE))
        self.assertFalse(self.win.tree.mimeData([artists]).hasFormat(self.win.tree._native_mime))
        before=[self.win.tree.topLevelItem(i).data(0,RB_NODE_ROLE) for i in range(self.win.tree.topLevelItemCount())]
        with mock.patch.object(self.win,'_save_state',return_value=False):
            self.assertFalse(self.win._native_reorder('3','1',False))
        self.assertEqual(self.controller.pending,[])
        self.assertEqual([self.win.tree.topLevelItem(i).data(0,RB_NODE_ROLE) for i in range(self.win.tree.topLevelItemCount())],before)

    def test_batch_projection_shows_created_playlist_membership_after_one_durable_admission(self):
        from soundbox.rekordbox_playlist_store import batch_for
        before=deepcopy(self.win.lib.source_playlist_catalog);preview=deepcopy(before)
        create=request_for(preview,'create',name='Batched set',parent='3')
        preview['nodes'].append(dict(ID=create['playlist_id'],UUID=create['playlist_uuid'],Name=create['name'],
                                    ParentID='3',Seq=2,Attribute=0,rb_local_deleted=0))
        append=request_for(preview,'append',identity=create['playlist_id'],tracks=list(self.win.lib.tracks_by_id.values()))
        request=batch_for(before,[create,append])
        seat=self.win._browser_seat()
        self.assertTrue(self.controller.enqueue(request))
        self.assertEqual(self.win._browser_seat(),seat)
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        item=self.win._find_tree_item('',create['playlist_id'])
        self.assertIsNotNone(item)
        self.win.tree.setCurrentItem(item)
        self.assertEqual({row['id'] for row in self.win.model._rows},{'t','u'})
        self.assertEqual(StateRepository(self.win._state_repository.path).load()['settings'][QUEUE],self.controller.pending)

    def _large_collection(self):
        for number in range(3200):
            identity=str(1000+number)
            self.win.lib.tracks_by_id[identity]=Track(identity,'Song '+identity,'Artist',
                'file:///never-open/'+identity+'.wav',bpm='120',total_time=200,added_ts=number+1)
        self.win._apply_settings_to_lib()
        self.win._populate_tree(engine.summary(self.win.lib))
        self.win.show();self.app.processEvents()
        self.win.tree.setCurrentItem(self.win.tree.topLevelItem(0))
        self.app.processEvents()
        selection=self.win.table.selectionModel()
        selection.select(self.win.model.index(3110,0),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
        selection.select(self.win.model.index(3180,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        selection.setCurrentIndex(self.win.model.index(3180,3),QItemSelectionModel.NoUpdate)
        self.win.table.verticalScrollBar().setValue(self.win.table.verticalScrollBar().maximum())
        self.app.processEvents()

    def test_unrelated_artist_save_does_not_reset_large_collection_or_navigation(self):
        self._large_collection()
        folder=self.win._find_tree_item('', '3');folder.setExpanded(True)
        seat=self.win._browser_seat();generation=self.win._cache_gen
        rows=self.win.model._rows;reset=mock.Mock();self.win.model.modelReset.connect(reset)
        request=request_for(self.win.lib.source_playlist_catalog,'create',name='New artist',parent='3')
        catalog=deepcopy(self.win.lib.source_playlist_catalog)
        catalog['nodes'].append(dict(ID=request['playlist_id'],UUID=request['playlist_uuid'],Name='New artist',
                                    ParentID='3',Seq=2,Attribute=0,rb_local_deleted=0))
        catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
        with mock.patch.object(self.win,'_refresh_devices') as devices, \
                mock.patch.object(self.win,'_refresh_graveyard') as grave, \
                mock.patch.object(self.win,'_cache_library'), \
                mock.patch.object(self.win.art_cache,'configure_default_tracks') as art:
            self.win._native_apply_catalog(catalog,request)
            self.win._native_refresh_view(request)
        self.assertIs(self.win.model._rows,rows);reset.assert_not_called()
        self.assertEqual(self.win._browser_seat(),seat)
        self.assertEqual(self.win._cache_gen,generation)
        self.assertIs(self.win._find_tree_item('', '3'),folder)
        self.assertTrue(folder.isExpanded())
        devices.assert_not_called();grave.assert_not_called();art.assert_not_called()

    def test_full_source_refresh_preserves_multiple_tracks_scroll_and_expanded_folder(self):
        self._large_collection()
        folder=self.win._find_tree_item('', '3');folder.setExpanded(True)
        seat=self.win._browser_seat()
        fresh=deepcopy(self.win.lib)
        fresh.tracks_by_id['new']=Track('new','Newest','Artist','file:///never-open/new.wav',
                                      bpm='120',total_time=200,added_ts=99999)
        self.win.lib=fresh
        with mock.patch.object(self.win.shared_import_controller,'schedule'), \
                mock.patch.object(self.win.native_analysis_controller,'discover'), \
                mock.patch.object(self.win.media_controller,'discover'):
            self.win._apply_loaded_library()
        after=self.win._browser_seat()
        for key in ('current','column','selected','top','horizontal'):self.assertEqual(after[key],seat[key],key)
        self.assertEqual(after['scroll'],seat['scroll']+1)
        self.assertIs(self.win._find_tree_item('', '3'),folder)
        self.assertTrue(folder.isExpanded())

    def test_sidebar_refresh_keeps_folder_collapse_and_active_playlist_selection(self):
        self.win._accordion_guard=True
        self.win._find_tree_item('', '1').setExpanded(True)
        self.win._find_tree_item('', '3').setExpanded(True)
        self.win._accordion_guard=False
        self.win.table.selectRow(0)
        seat=self.win._browser_seat()
        for expanded in (True,False,True,False):
            self.win._find_tree_item('', '3').setExpanded(expanded)
            self.win._native_pending_changed(rebuild=True)
            self.assertEqual(self.win._find_tree_item('', '3').isExpanded(),expanded)
            self.assertEqual(self.win._browser_seat(),seat)

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
        self.assertEqual([r['id'] for r in self.win.model._rows],['u','t'])
        self.assertEqual(self.win._native_view_playlist('Folder / Delete').keys,['t','u'])
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
