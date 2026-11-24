"""RAM feedback and durable playlist publication in an isolated desktop sidebar."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QItemSelectionModel

from desktop.rekordbox_playlists import PlaylistEditController, QUEUE
from soundbox import engine, rekordbox_playlist_plan as planner
from soundbox.library import Library, Playlist, Track
from soundbox.state_repository import StateRepository,read_state
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
        # Hold native execution independently of canonical admission. A fake
        # tags.busy hold also blocks the real StateCommitter's source token.
        self.source=self.enterContext(mock.patch.object(self.win.tag_controller,'run_playlist_job',return_value=False))
        self.win.settings['rekordbox_db_path']=str(self.win.lib.source)
        self.assertTrue(self.win._save_state())
        self.win._populate_tree(engine.summary(self.win.lib))
        self.win._find_tree_item('','3').setExpanded(True)
        self.win.tree.setCurrentItem(self.win._find_tree_item('Folder / Delete','2'))
        self.addCleanup(self.close)

    def close(self):
        self.controller.stop();self.win.tag_controller.busy=False
        self.win.shutdown();self.win.close();self.win.deleteLater();self.app.processEvents()

    def wait(self,condition):
        from PySide6.QtTest import QTest
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(condition(),'Playlist canonical publication did not settle')

    def durable(self):
        self.wait(lambda:not self.controller.admission_pending and not self.win._state_publication)
        self.assertFalse(self.controller.admission_error)
        return StateRepository(self.win._state_repository.path).load()['settings'].get(QUEUE,[])

    def _prepare_track_move(self):
        """Distinct occurrences share songs; no source file is opened."""
        w = self.win; catalog = w.lib.source_playlist_catalog
        catalog['members'] = [row for row in catalog['members'] if row['PlaylistID'] != '2'] + [
            dict(ID=f'entry-{index}', UUID=f'00000000-0000-4000-8000-{index + 100:012d}', PlaylistID='2',
                 ContentID=content, TrackNo=index, rb_local_deleted=0)
            for index, content in enumerate(('42', '43', '42', '43'), 1)]
        w.lib.playlists[0].keys = ['t', 'u', 't', 'u']
        catalog['revision'] = planner.revision(catalog['nodes'], catalog['members'])
        w._reload_current_view()
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 'u', 't', 'u'])
        return w

    def test_track_move_feedback_is_durable_atomic_and_preserves_duplicate_selection_and_masks(self):
        w = self._prepare_track_move(); before = deepcopy(w.lib.source_playlist_catalog)
        w.removals = {'2': {'u': 'keep-this-mask'}}
        selection = w.table.selectionModel()
        selection.setCurrentIndex(w.model.index(2, 1), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        identity = w._native_row_identity(w.model._rows[2])
        captured = w._capture_track_reorder([w.model._rows[2]])
        anchor = deepcopy(w.model._rows[0]['_native_membership'])
        with self.held_canonical():
            self.assertTrue(w._drop_track_reorder(captured, anchor))
            self.assertEqual([row['id'] for row in w.model._rows], ['t', 't', 'u', 'u'])
            self.assertEqual(w._native_row_identity(w.model._rows[0]), identity)
            self.assertEqual(w.table.currentIndex().row(), 0)
            self.assertEqual(w.table.currentIndex().column(), 1)
            self.assertEqual(w.lib.source_playlist_catalog, before)
            self.source.assert_not_called()
        jobs = self.durable()
        self.assertEqual(len(jobs), 1)
        self.assertEqual([child['operation'] for child in jobs[0]['request']['requests']], ['remove', 'append'])
        self.assertEqual(jobs[0]['removal_overlay']['entries'], [])
        self.assertEqual(w.removals, {'2': {'u': 'keep-this-mask'}})
        # A second drag binds the replacement occurrences already projected by
        # the first durable envelope, without changing the verified source.
        second = w._capture_track_reorder([w.model._rows[0]])
        self.assertTrue(w._drop_track_reorder(second, None))
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 'u', 'u', 't'])
        self.assertEqual(w._native_row_identity(w.model._rows[-1]), identity)
        self.assertEqual(w.lib.source_playlist_catalog, before)
        self.assertEqual(len(self.durable()), 2)

    def test_track_move_rejects_stale_drag_and_does_not_reorder_filtered_or_sorted_views(self):
        w = self._prepare_track_move()
        captured = w._capture_track_reorder([w.model._rows[2]])
        w.lib.source_playlist_catalog['members'][1]['UUID'] = 'changed-after-drag'
        self.assertFalse(w._drop_track_reorder(captured, None))
        self.assertEqual(self.controller.pending, [])
        w.model.set_filter('Synthetic')
        self.assertIsNone(w._capture_track_reorder(w.model._rows[:1]))
        w.model.set_filter(''); w.model.sort(0)
        self.assertIsNone(w._capture_track_reorder(w.model._rows[:1]))

    def test_membership_move_reuses_prepared_rows_without_sidebar_or_artist_work(self):
        w = self._prepare_track_move(); reset = mock.Mock(); w.model.modelReset.connect(reset)
        capture = w._capture_track_reorder([w.model._rows[2]])
        anchor = deepcopy(w.model._rows[0]['_native_membership'])
        with mock.patch.object(w, '_populate_tree') as sidebar, \
                mock.patch.object(w, '_prepare_artists') as artists, \
                mock.patch.object(w, '_row_for') as derive:
            self.assertTrue(w._drop_track_reorder(capture, anchor))
            request = self.durable()[0]['request']
            self.assertEqual([row['id'] for row in w.model._rows], ['t', 't', 'u', 'u'])
            # Exact source-ACK cause survives removal of its queue row; the
            # renderer must not infer a full refresh from an empty queue.
            w._native_refresh_view(request)
            sidebar.assert_not_called(); artists.assert_not_called(); derive.assert_not_called()
        reset.assert_not_called()

    def test_retry_after_failed_admission_reprojects_the_complete_saved_order(self):
        w = self._prepare_track_move()
        capture = w._capture_track_reorder([w.model._rows[2]])
        anchor = deepcopy(w.model._rows[0]['_native_membership'])
        with mock.patch.object(w._state_repository, 'commit_patch', side_effect=OSError('Fixture disk full')):
            self.assertTrue(w._drop_track_reorder(capture, anchor)); self.failed_admission()
        w._reload_current_view()
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 'u', 't', 'u'])
        self.assertTrue(self.controller.retry())
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 't', 'u', 'u'])
        self.durable()
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 't', 'u', 'u'])

    def test_track_table_self_drag_reaches_atomic_membership_intention(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPointF, Qt
        w = self._prepare_track_move(); table = w.table
        table.selectRow(2)
        accepted = []
        event = SimpleNamespace(source=lambda: table, position=lambda: QPointF(table.visualRect(w.model.index(0, 0)).topLeft()),
                                accept=lambda: accepted.append(True), setDropAction=lambda action: None)
        def drag(*args, **kwargs):
            self.assertIsNotNone(table._track_reorder_capture)
            table.dragMoveEvent(event)
            self.assertIsNotNone(table._reorder_line)
            table.dropEvent(event)
        with mock.patch('desktop.trees._start_chip_drag', side_effect=drag):
            table.startDrag(Qt.CopyAction)
        self.assertEqual(len(accepted), 2)
        self.assertIsNone(table._track_reorder_capture)
        self.assertIsNone(table._reorder_line)
        self.assertEqual([row['id'] for row in w.model._rows], ['t', 't', 'u', 'u'])
        self.assertEqual(len(self.durable()), 1)

    def test_track_reorder_autoscrolls_long_sets_and_stops_on_leave(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPointF
        w = self._prepare_track_move(); catalog = w.lib.source_playlist_catalog
        catalog['members'] = [row for row in catalog['members'] if row['PlaylistID'] != '2'] + [
            dict(ID=f'long-{i}', UUID=f'00000000-0000-4000-8000-{i + 200:012d}', PlaylistID='2',
                 ContentID='42', TrackNo=i, rb_local_deleted=0) for i in range(1, 121)]
        w.lib.playlists[0].keys = ['t'] * 120
        catalog['revision'] = planner.revision(catalog['nodes'], catalog['members'])
        w._reload_current_view(); w.resize(1100, 900); w.show(); self.app.processEvents()
        table = w.table; bar = table.verticalScrollBar(); bar.setValue(0)
        self.assertGreater(bar.maximum(), 1)
        table._track_reorder_capture = w._capture_track_reorder([w.model._rows[0]])
        event = SimpleNamespace(source=lambda: table,
                                position=lambda: QPointF(20, table.viewport().height() - 2),
                                accept=lambda: None, setDropAction=lambda action: None)
        table.dragMoveEvent(event); self.assertTrue(table._reorder_scroll_timer.isActive())
        table._reorder_autoscroll(); self.assertGreater(bar.value(), 0)
        table._set_reorder_line(None); self.assertFalse(table._reorder_scroll_timer.isActive())
        table._track_reorder_capture = None

    def test_track_move_keeps_typed_duplicate_draft_bound_to_the_same_occurrence(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLineEdit
        w = self._prepare_track_move(); w.show(); w.activateWindow(); w.raise_(); QTest.qWait(40)
        index = w.model.index(1, 0)
        w.table.setCurrentIndex(index); w.table.edit(index); self.app.processEvents()
        editor = w.table.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        editor.setFocus(); self.app.processEvents()
        self.assertTrue(editor.hasFocus(), 'The synthetic draft editor must own native focus before the drag')
        editor.selectAll(); QTest.keyClicks(editor, 'Keep this unsaved text')
        editor.setSelection(5, 7)
        identity = w._native_row_identity(w.model._rows[1])
        # Move another occurrence to the front, replacing its neighboring tail
        # memberships; the open editor remains song-bound.
        captured = w._capture_track_reorder([w.model._rows[2]])
        self.assertTrue(w._drop_track_reorder(captured, w.model._rows[0]['_native_membership']))
        self.app.processEvents()
        editors = w.table.findChildren(QLineEdit)
        self.assertTrue(editors)
        replacement = next(value for value in editors if value.isVisible() and value.text() == 'Keep this unsaved text')
        self.assertTrue(replacement.isModified())
        self.assertEqual(replacement.selectedText(), 'this un')
        self.assertEqual(w._native_row_identity(w.model._rows[w.table.currentIndex().row()]), identity)
        self.durable()

    def failed_admission(self):
        self.wait(lambda:bool(self.controller.admission.error) and not self.controller.admission.active
                  and not self.win._state_publication)
        self.assertTrue(self.controller.pending)
        self.assertFalse(StateRepository(self.win._state_repository.path).load()['settings'].get(QUEUE))
        self.source.assert_not_called()

    def discard(self):
        with mock.patch('desktop.rekordbox_playlists.QMessageBox.question',return_value=QMessageBox.Yes):
            self.assertTrue(self.controller.discard())
        self.assertEqual(self.durable(),[])
        self.assertEqual(self.controller.pending,[])

    @contextmanager
    def held_canonical(self):
        entered,release=threading.Event(),threading.Event()
        commit=self.win._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise RuntimeError('Canonical fixture hold timed out')
            return commit(patch)
        with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=held):
            try:yield entered,release
            finally:
                release.set()
                self.wait(lambda:not self.win._state_publication)

    def test_ram_sidebar_feedback_precedes_durability_and_never_becomes_native_or_export_truth(self):
        from PySide6.QtTest import QTest
        from desktop.trees import RB_NODE_ROLE
        before=deepcopy(self.win.lib.source_playlist_catalog)
        saved=StateRepository(self.win._state_repository.path).load()
        playlists=deepcopy(self.win.lib.playlists)
        self.win.table.selectRow(0);seat=self.win._browser_seat()
        with self.held_canonical() as (entered,release):
            self.assertTrue(self.win._native_request('rename',identity='2',name='Pending label'))
            self.assertTrue(entered.wait(2))
            self.assertIsNotNone(self.win._find_tree_item('Folder / Pending label','2'))
            self.assertEqual(self.win.tree.currentItem().data(0,RB_NODE_ROLE),'2')
            self.assertEqual(self.win._browser_seat(),seat)
            self.assertTrue(self.controller.admission_pending)
            self.assertTrue(self.win._pending_save_reason())
            self.assertTrue(self.win._source_selection_reason())
            self.assertEqual(read_state(self.win._state_repository.path),saved)
            self.assertEqual(self.win.lib.source_playlist_catalog,before)
            self.assertEqual(self.win.lib.playlists,playlists)
            QTest.qWait(10);self.source.assert_not_called();release.set()
        self.assertEqual(self.durable(),self.controller.pending)
        self.wait(lambda:self.source.call_count>0)
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.assertEqual(self.win.lib.playlists,playlists)
        self.assertEqual(self.win._browser_seat(),seat)

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
        persisted=self.durable()
        self.assertEqual(persisted,self.controller.pending)
        self.assertEqual(persisted[0]['request']['operation'],'reorder')
        self.assertEqual(self.win.lib.source_playlist_catalog,source)
        # A second drag uses the durable projected order; both intents survive.
        self.assertTrue(self.win._native_reorder('1','3',False))
        self.assertEqual(len(self.controller.pending),2)
        self.assertEqual(self.win._browser_seat(),seat)
        self.assertEqual(self.durable(),self.controller.pending)

    def test_virtual_artists_stays_fixed_and_failed_reorder_retains_only_the_unsaved_intention(self):
        from desktop.trees import RB_NODE_ROLE,ARTISTS_ROLE,PINNED_TREE_ROLE
        artists=self.win.tree.topLevelItem(1)
        self.assertTrue(artists.data(0,ARTISTS_ROLE));self.assertTrue(artists.data(0,PINNED_TREE_ROLE))
        self.assertFalse(self.win.tree.mimeData([artists]).hasFormat(self.win.tree._native_mime))
        before=[self.win.tree.topLevelItem(i).data(0,RB_NODE_ROLE) for i in range(self.win.tree.topLevelItemCount())]
        source=deepcopy(self.win.lib.source_playlist_catalog);seat=self.win._browser_seat()
        with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=OSError('Fixture disk full')):
            self.assertTrue(self.win._native_reorder('3','1',False))
            request=deepcopy(self.controller.pending[0]['request']);self.failed_admission()
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertEqual(self.win.lib.source_playlist_catalog,source)
        self.assertEqual(self.win._browser_seat(),seat)
        self.discard()
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
        self.assertEqual(self.durable(),self.controller.pending)

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
        self.assertEqual(self.durable(),self.controller.pending)
        self.win._populate_tree(engine.summary(self.win.lib))
        self.assertIsNone(self.win._find_tree_item('Folder / Delete','2'))
        self.assertEqual(self.controller.pending[0]['error'],'')
        print(f'persisted folder-delete feedback: {elapsed:.2f} ms')

    def test_failure_and_discard_reconcile_newest_catalog_and_retry_reprojects(self):
        self.assertTrue(self.win._native_request('delete',identity='1'))
        self.durable()
        self.win.lib.source_playlist_catalog['nodes'][1]['Name']='External'
        self.win.lib.playlists[0].name='External'
        deferred=[];reconcile=self.win._native_reconcile_source
        def reconciled():
            reconcile();deferred.append(self.win._refresh_pending)
        with mock.patch.object(self.win,'_native_reconcile_source',side_effect=reconciled):
            self.controller.fail('Source changed');self.durable()
        self.assertEqual(deferred,[True])
        self.wait(lambda:self.win._start_rekordbox_refresh.call_count==1)
        self.win._start_rekordbox_refresh.assert_called_once_with(Path('/synthetic/master.db'))
        self.win._start_rekordbox_refresh.reset_mock()
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
        self.assertTrue(self.controller.retry());self.durable()
        self.assertIsNone(self.win._find_tree_item('Folder / External','2'))
        self.discard()
        self.assertEqual(self.controller.pending,[])
        self.wait(lambda:self.win._start_rekordbox_refresh.call_count==1)
        self.win._start_rekordbox_refresh.assert_called_once_with(Path('/synthetic/master.db'))
        self.assertIsNotNone(self.win._find_tree_item('Folder / External','2'))
        self.assertEqual(self.win.removals,{})

    def test_failed_admission_keeps_row_and_identity_mismatch_does_not_hide_a_new_song_list(self):
        source=deepcopy(self.win.lib.source_playlist_catalog)
        with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=OSError('Fixture disk full')):
            self.assertTrue(self.win._native_request('delete',identity='1'));self.failed_admission()
        self.assertEqual(self.win.lib.source_playlist_catalog,source)
        self.assertEqual(self.win._native_presentation().source_playlist_catalog,source)
        self.discard()
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
        self.assertEqual(self.durable(),self.controller.pending)
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
        self.assertEqual(self.win._native_view_playlist('Folder / Delete').keys,['t','u'])
        self.win._native_remove('Folder / Delete',['t'])
        self.assertEqual([r['id'] for r in self.win.model._rows],['u'])
        self.assertEqual(self.win.lib.playlists[0].keys,['t'])
        self.assertEqual(self.win.lib.source_playlist_catalog,before)
        self.durable()
        self.controller.fail('Synthetic source conflict');self.durable()
        self.assertEqual([r['id'] for r in self.win.model._rows],['t'])
        self.assertTrue(self.controller.retry());self.durable()
        self.assertEqual([r['id'] for r in self.win.model._rows],['u'])
        self.discard()
        self.assertEqual([r['id'] for r in self.win.model._rows],['t'])
        self.assertEqual(self.win.lib.source_playlist_catalog,before)

    def test_failed_create_save_and_foreign_or_newer_source_do_not_project(self):
        before=deepcopy(self.win.lib.source_playlist_catalog)
        with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=OSError('Fixture disk full')):
            self.assertTrue(self.win._native_request('create',name='Not saved'))
            request=deepcopy(self.controller.pending[0]['request']);self.failed_admission()
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertEqual(self.win._native_presentation().source_playlist_catalog,before)
        self.discard()
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
