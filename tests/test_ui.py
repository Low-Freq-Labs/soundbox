"""UI-layer tests: MainWindow wiring, offscreen, against a synthetic library.

Hermetic by construction (see soundbox-gotchas):
  • QStandardPaths test mode BEFORE the app module loads; AppData is redirected
    to a fresh temporary directory, never the real library/art cache/app log.
  • rekordbox_db.db_available patched False — the live DB is never touched.
One QApplication + one MainWindow for the module (Qt allows only one app).
"""

import json
import os
import logging
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QStandardPaths, Qt
from PySide6.QtGui import QColor, QKeyEvent

QStandardPaths.setTestModeEnabled(True)

from soundbox import engine, manifest as device_manifest, rekordbox_db
from soundbox.library import Library, Playlist, Track
from soundbox.device_operations import DeviceSessionIdentity
from desktop.native_deck import PlaybackState

_app = _win = None


def _make_lib() -> Library:
    tracks = {
        "1": Track("1", "Bravo", "Zed", "file://localhost/tmp/b.wav",
                   bpm="140.00", key="10A", total_time=185, date_added="2026-01-02"),
        "2": Track("2", "alpha", "Ann", "file://localhost/tmp/a.wav",
                   bpm="96.00", key="2A", total_time=720, date_added="2026-03-01"),
        "3": Track("3", "Charlie", "Mia", "file://localhost/tmp/c.wav",
                   bpm="128.00", key="9B", total_time=45, date_added="2026-02-10"),
    }
    pls = [Playlist("SET ONE", ["CRATES"], keys=["3", "1"]),
           Playlist("SET TWO", [], keys=["2"])]
    return Library(tracks_by_id=tracks, playlists=pls, source="/synthetic/library.xml")


def setUpModule():
    global _app, _win
    rekordbox_db.db_available = lambda *_args: False  # tests never read the live DB
    from desktop.app import MainWindow, make_app
    _app = make_app()
    area = unittest.enterModuleContext(tempfile.TemporaryDirectory(
        prefix="soundbox-ui-tests-"))
    original_location = QStandardPaths.writableLocation
    unittest.enterModuleContext(mock.patch.object(
        QStandardPaths, "writableLocation",
        side_effect=lambda kind: area if kind == QStandardPaths.AppDataLocation
        else original_location(kind)))
    # Refresh callbacks later in the suite can discover mounts too. Keep all
    # background scans synthetic; device-worker tests override this locally.
    unittest.enterModuleContext(mock.patch(
        "desktop.workers.list_devices", return_value=[]))
    # Editing a synthetic title can schedule per-track discovery. Keep that
    # provider offline too; isolated AppData alone does not isolate the network.
    unittest.enterModuleContext(mock.patch(
        "soundbox_personal.discovery.fetch_best", return_value=None))
    # MainWindow normally starts an immediate removable-device discovery before
    # the timer can be stopped. Keep the UI harness genuinely hermetic so a
    # mounted player is never inspected by tests running beside the real app.
    with mock.patch.object(MainWindow, "_refresh_devices", autospec=True):
        _win = MainWindow()
    # Settings is ready before first show; this call also checks its idempotence
    # for the shared fixture used directly throughout the module.
    _win._ensure_settings_page()
    _win._dev_timer.stop()  # no background device polling during tests
    _win.lib = _make_lib()
    _win._build_artist_registry()
    _win._apply_settings_to_lib()
    _win._populate_tree(engine.summary(_win.lib))


def tearDownModule():
    _win.shutdown()


class _FakeArt:
    def __init__(self, kinds):
        self.kinds = kinds

    def status_kind(self, tid):
        return self.kinds.get(tid, "")

    def status_kinds(self, tids):
        return {tid: self.status_kind(tid) for tid in tids}


class PolishNavigation(unittest.TestCase):
    def test_artist_refresh_retains_expansion_current_cell_and_multiple_selected_tracks(self):
        from desktop.app import ARTIST_ROLE,ARTIST_TRACK_ROLE
        from PySide6.QtCore import QItemSelectionModel
        _win.search.setText('');_win._show_artists()
        view=_win.artists_view
        first,second=view.topLevelItem(0),view.topLevelItem(1)
        first.setExpanded(True);second.setExpanded(True)
        view.setCurrentItem(first.child(0),1)
        view.selectionModel().select(view.indexFromItem(second.child(0)),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        expanded={first.data(0,ARTIST_ROLE),second.data(0,ARTIST_ROLE)}
        current=first.child(0).data(0,ARTIST_TRACK_ROLE)
        selected={i.data(0,ARTIST_TRACK_ROLE) for i in view.selectedItems()}
        _win._show_artists(preserve=True);_app.processEvents()
        self.assertEqual({view.topLevelItem(i).data(0,ARTIST_ROLE) for i in range(view.topLevelItemCount())
                          if view.topLevelItem(i).isExpanded()},expanded)
        self.assertEqual(view.currentItem().data(0,ARTIST_TRACK_ROLE),current)
        self.assertEqual(view.currentColumn(),1)
        self.assertEqual({i.data(0,ARTIST_TRACK_ROLE) for i in view.selectedItems()},selected)
        _win._show_collection()

    def test_queued_sort_reseat_does_not_replace_newer_multiselection(self):
        from PySide6.QtCore import QItemSelectionModel
        _win.model.set_rows([dict(id=str(i),title=f'Track {i}') for i in range(3)])
        _win.table.setCurrentIndex(_win.model.index(0,0))
        _win._schedule_reseat_selection()
        _win.table.setCurrentIndex(_win.model.index(1,0))
        _win.table.selectionModel().select(_win.model.index(2,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        before={i.row() for i in _win.table.selectionModel().selectedRows()}
        _app.processEvents()
        self.assertEqual({i.row() for i in _win.table.selectionModel().selectedRows()},before)
        self.assertEqual(_win.table.currentIndex().row(),1)
        _win._show_collection()

    def test_delayed_scroll_from_previous_view_cannot_move_the_new_view(self):
        _win.model.set_rows([dict(id=str(i),title=f'Track {i}') for i in range(120)])
        original_key=getattr(_win,'_view_key',None)
        original_memory=getattr(_win,'_view_memory',{})
        try:
            _win._view_key='before';_win._view_memory={'first':(None,40),'second':(None,0)}
            _win._restore_view('first');_win._restore_view('second')
            _win.table.doItemsLayout();_win.table.verticalScrollBar().setValue(0)
            _app.processEvents()
            self.assertEqual(_win.table.verticalScrollBar().value(),0)
        finally:
            _win._view_key=original_key;_win._view_memory=original_memory;_win._show_collection()

    def test_online_art_publication_refreshes_information_after_success_only(self):
        from soundbox.artwork import ArtResult
        result=ArtResult(b'not decoded in this test','image/jpeg',1.0,'fixture','fixture')
        with mock.patch.object(_win.art_cache,'store_ok') as store, \
                mock.patch.object(_win,'_refresh_info_artwork') as refresh, \
                mock.patch.object(_win,'_queue_stock_artwork'), \
                mock.patch.object(_win,'_refresh_model_art'), \
                mock.patch.object(_win,'_background_status_available',return_value=False):
            _win._on_oneshot('1',None);refresh.assert_not_called()
            _win._on_oneshot('1',result)
            store.assert_called_once_with('1',result);refresh.assert_called_once_with(('1',))


class LocalPlaylistFolders(unittest.TestCase):
    def test_unified_sidebar_keeps_local_rename_until_native_cutover(self):
        item=_win.sb_tree.topLevelItem(2).child(0)
        _win.sb_tree.setCurrentItem(item)
        with mock.patch('desktop.app.QApplication.focusWidget',return_value=_win.tree), \
                mock.patch.object(_win,'_rename_local_node') as rename:
            _win._rename_current()
            rename.assert_called_once_with('playlist','A')

    def setUp(self):
        from copy import deepcopy
        self.old_custom=_win.custom
        self.old_settings=deepcopy(_win.settings)
        self.addCleanup(self.restore)
        self.enterContext(mock.patch.object(_win,'_save_state',return_value=True))
        self.key='81cf1d6c-45ac-4773-adfa-fd582b246137'
        _win.custom={'A':['1','2'],'B':['2','3'],'C':['3']}
        _win.settings['custom_playlist_tree']=[dict(folder=self.key,name='Sets',children=[{'playlist':'A'},{'playlist':'B'}]),{'playlist':'C'}]
        _win.settings['custom_playlist_expanded']=[self.key]
        _win._refresh_custom()

    def restore(self):
        _win.custom=self.old_custom;_win.settings=self.old_settings
        _win._refresh_custom();_win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))

    def test_folder_navigation_keeps_active_playlist_and_drag_exports_children_in_order(self):
        folder=_win.sb_tree.topLevelItem(2)
        self.assertEqual(folder.childCount(),2)
        _win.sb_tree.setCurrentItem(folder.child(0))
        before=[r['id'] for r in _win.model._rows]
        _win.sb_tree.setCurrentItem(folder)
        self.assertEqual([r['id'] for r in _win.model._rows],before)
        _win._reload_current_view()
        self.assertEqual([r['id'] for r in _win.model._rows],before)
        self.assertEqual(_win._resolve_table_drop(),('playlist','A'))
        lines=_win.sb_tree.mimeData([folder]).text().splitlines()
        self.assertEqual([s for s in lines if s.startswith('cust\t')],['cust\tA','cust\tB'])
        jobs=_win._resolve_jobs([('cust','A'),('cust','B')])
        self.assertEqual([p.folder_path for p in jobs],[['Sets'],['Sets']])
        self.assertEqual([device_manifest.playlist_key(p) for p in jobs],['cust:A','cust:B'])
        self.assertEqual(jobs[0].keys,['1','2'])

    def test_mouse_folder_navigation_keeps_collection_until_a_leaf_is_clicked(self):
        from PySide6.QtTest import QTest
        collection=_win.sb_tree.topLevelItem(0)
        _win.sb_tree.setCurrentItem(collection)
        before=[r['id'] for r in _win.model._rows]
        folder=_win.sb_tree.topLevelItem(2);folder.setExpanded(False)
        _app.processEvents()
        QTest.mouseClick(_win.sb_tree.viewport(),Qt.LeftButton,pos=_win.sb_tree.visualItemRect(folder).center())
        self.assertTrue(folder.isExpanded())
        self.assertEqual(_win._view_key,'sb:Collection')
        self.assertEqual([r['id'] for r in _win.model._rows],before)
        _win.sb_tree.setCurrentItem(folder.child(0))
        self.assertEqual(_win._view_key,'sb:A')
        self.assertEqual([r['id'] for r in _win.model._rows],['1','2'])

    def test_rekordbox_folder_keeps_active_view_and_track_action_scope(self):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtTest import QTest
        collection=_win.sb_tree.topLevelItem(0);_win.sb_tree.setCurrentItem(collection)
        before=[r['id'] for r in _win.model._rows]
        folder=_win._find_tree_item('CRATES / SET ONE').parent();folder.setExpanded(False);_app.processEvents()
        QTest.mouseClick(_win.tree.viewport(),Qt.LeftButton,pos=_win.tree.visualItemRect(folder).center())
        self.assertTrue(folder.isExpanded())
        self.assertEqual(_win._view_key,'sb:Collection')
        self.assertEqual([r['id'] for r in _win.model._rows],before)
        _win.tree.setCurrentItem(folder.child(0))
        self.assertEqual(_win._view_key,'rb:CRATES / SET ONE')
        ids=[r['id'] for r in _win.model._rows]
        _win.tree.setCurrentItem(folder);_win._reload_current_view()
        self.assertEqual([r['id'] for r in _win.model._rows],ids)
        menu=QMenu();_win._add_track_removal_actions(menu,['1'])
        self.assertEqual(menu.actions()[0].text(),'Remove from Playlist');menu.deleteLater()

    def test_pressed_folder_drag_uses_that_folder_instead_of_active_playlist(self):
        from PySide6.QtTest import QTest
        folder=_win.sb_tree.topLevelItem(2)
        _win.sb_tree.setCurrentItem(folder.child(0));_app.processEvents()
        position=_win.sb_tree.visualItemRect(folder).center()
        QTest.mousePress(_win.sb_tree.viewport(),Qt.LeftButton,pos=position)
        with mock.patch('desktop.trees._start_chip_drag') as drag:
            _win.sb_tree.startDrag(Qt.CopyAction)
        lines=drag.call_args.args[1].text().splitlines()
        self.assertIn('sbfolder\t'+self.key,lines)
        self.assertIn('cust\tB',lines)
        self.assertEqual(_win._view_key,'sb:A')
        QTest.mouseRelease(_win.sb_tree.viewport(),Qt.LeftButton,pos=position)

    def test_failed_save_does_not_move_a_playlist_or_change_memberships(self):
        from copy import deepcopy
        before=deepcopy(_win.settings['custom_playlist_tree']);members=deepcopy(_win.custom)
        with mock.patch.object(_win,'_save_state',return_value=False):
            _win._move_local_node('playlist:C',self.key,1)
        self.assertEqual(_win.settings['custom_playlist_tree'],before)
        self.assertEqual(_win.custom,members)
        self.assertEqual(_win.sb_tree.topLevelItem(3).data(0, Qt.UserRole+3),'C')

    def test_context_menu_order_and_builtin_protection(self):
        for item in (None,_win.sb_tree.topLevelItem(0),_win.sb_tree.topLevelItem(2)):
            menu=_win._local_context_menu(item)
            self.assertEqual([a.text() for a in menu.actions()],['New Playlist','New Folder','Rename','Delete'])
            self.assertEqual(menu.actions()[2].isEnabled(),item is _win.sb_tree.topLevelItem(2))
            menu.deleteLater()

    def test_confirmed_folder_deletion_preserves_collection_and_unrelated_playlist(self):
        from PySide6.QtWidgets import QMessageBox
        tracks=set(_win.lib.tracks_by_id)
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            _win._delete_local_node('folder',self.key)
        self.assertEqual(_win.custom,{'C':['3']})
        self.assertEqual(set(_win.lib.tracks_by_id),tracks)
        self.assertNotIn('Sets',str(_win.settings['custom_playlist_tree']))


class CollectionView(unittest.TestCase):
    def test_sidebar_count_never_formats_titles_or_reads_art(self):
        _win._artist_count_cache = None
        with mock.patch.object(_win, '_artist_rows', side_effect=AssertionError('full rows')), \
                mock.patch.object(_win, '_landed_time', side_effect=AssertionError('sort')), \
                mock.patch.object(_win.art_cache, 'status_kinds', side_effect=AssertionError('art')), \
                mock.patch('soundbox.engine.track_row', side_effect=AssertionError('titles')):
            _win._refresh_custom()
        self.assertIn('(3)', _win.sb_tree.topLevelItem(1).text(0))

    def test_sidebar_count_reuses_names_until_row_inputs_change(self):
        _win._artist_count_cache = None
        _win._refresh_custom()
        with mock.patch('soundbox.metadata.effective_artist', side_effect=AssertionError('recount')):
            _win._refresh_custom()

    def test_test_mode_never_opens_the_production_log(self):
        from desktop.app import LOG
        self.assertFalse(any(isinstance(h, logging.FileHandler)
                             for h in LOG.handlers))
        self.assertFalse(_win._mount_table_timer.isActive())

    def test_pinned_first_with_count(self):
        it = _win.sb_tree.topLevelItem(0)
        self.assertIn("Collection", it.text(0))
        self.assertIn("(3)", it.text(0))

    def test_orders_by_file_landing_then_name(self):
        # Synthetic tracks point at non-existent files → landing time 0.0 for all,
        # so the sort falls back to title A→Z (real birthtime order is covered by
        # CollectionLandedOrder with real files).
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual([r["title"] for r in _win.model._rows],
                         ["alpha", "Bravo", "Charlie"])  # name fallback

    def test_not_a_custom_playlist(self):
        from desktop.trees import CUSTOM_ROLE
        self.assertFalse(_win.sb_tree.topLevelItem(0).data(0, CUSTOM_ROLE))

    def test_honors_hide_untracked(self):
        # Direct call (NOT via a Qt slot — slots swallow exceptions, which hid a
        # real is_untracked signature crash once). Keyless+no-BPM+short = hidden.
        _win.lib.tracks_by_id["9"] = Track("9", "cue stub", "",
                                           "file://localhost/tmp/s.wav",
                                           total_time=2)
        _win.lib.hide_untracked = True
        try:
            names = [t.name for t in _win._collection_tracks()]
            self.assertNotIn("cue stub", names)
            self.assertEqual(len(names), 3)
        finally:
            _win.lib.hide_untracked = False
            del _win.lib.tracks_by_id["9"]


class PlaylistSelection(unittest.TestCase):
    def test_adopted_custom_add_uses_native_writer_and_keeps_archive(self):
        from dataclasses import replace
        from soundbox import playlist_unification as merge
        lib=_win.lib;settings=dict(_win.settings)
        try:
            _win.lib=replace(lib,source_kind='rekordbox-db',source_library_id='123')
            state=merge.new(lib.source,'123');state['customs_done']=True
            state['nodes']['custom:Favorites']=dict(id='1000',uuid='00000000-0000-4000-8000-000000000001')
            _win.settings[merge.KEY]=state
            before=dict(_win.custom)
            with mock.patch.object(_win,'_native_append') as append:
                _win._on_tracks_dropped('Favorites',['1'])
                append.assert_called_once_with('1000',['1'])
            self.assertEqual(_win.custom,before)
            with mock.patch.object(_win,'_native_tree_menu') as native, mock.patch.object(_win,'_sb_menu') as local:
                item=_win.sb_tree.topLevelItem(0)
                with mock.patch.object(_win.tree,'itemAt',return_value=item):_win._playlist_menu(QPoint(1,1))
                native.assert_called_once();local.assert_not_called()
        finally:_win.lib=lib;_win.settings=settings

    def test_sidebar_arrows_move_inside_the_active_playlist(self):
        from PySide6.QtTest import QTest
        item=_win._find_tree_item('CRATES / SET ONE')
        _win.tree.setCurrentItem(item);_win._reload_current_view()
        _win.table.setCurrentIndex(_win.model.index(0,0))
        _win.tree.setFocus()
        QTest.keyClick(_win.tree,Qt.Key_Down)
        self.assertIs(_win.tree.currentItem(),item)
        self.assertEqual(_win.table.currentIndex().row(),1)
        QTest.keyClick(_win.table,Qt.Key_Up)
        self.assertEqual(_win.table.currentIndex().row(),0)

    def test_regular_playlist_display_preserves_authored_set_order(self):
        it = _win._find_tree_item("CRATES / SET ONE")
        self.assertIsNotNone(it)
        _win.tree.setCurrentItem(it)
        _app.processEvents()
        self.assertEqual([r["id"] for r in _win.model._rows], ["3", "1"])

    def test_select_only_clears_other_trees(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        self.assertIs(_win.sb_tree.currentItem(),_win.tree.currentItem())
        self.assertIs(_win.sb_tree,_win.tree)


class BrowserDropTarget(unittest.TestCase):
    """Dropping onto the main browser adds to the playlist it shows — but only
    for writable SoundBox views. Artists (tag-derived) and rekordbox (rb:, the
    sacred read-only source) must reject every drop."""

    def _resolve(self, view_key):
        _win._view_key = view_key
        return _win._resolve_table_drop()

    def test_custom_playlist_takes_tracks_and_files(self):
        _win.custom["DROP TEST"] = ["1"]
        try:
            self.assertEqual(self._resolve("sb:DROP TEST"), ("playlist", "DROP TEST"))
        finally:
            _win.custom.pop("DROP TEST", None)

    def test_collection_is_file_import_only(self):
        self.assertEqual(self._resolve("sb:Collection"), ("collection", ""))

    def test_artists_view_rejects(self):
        self.assertIsNone(self._resolve("sb:Artists"))

    def test_rekordbox_view_never_written(self):
        self.assertIsNone(self._resolve("rb:CRATES / SET ONE"))

    def test_unknown_custom_name_rejects(self):
        self.assertIsNone(self._resolve("sb:No Such Playlist"))

    def test_no_view_rejects(self):
        self.assertIsNone(self._resolve(None))


class ArtColumn(unittest.TestCase):
    def test_rows_annotated_from_cache(self):
        real = _win.art_cache
        _win.art_cache = _FakeArt({"3": "cover", "1": "miss"})
        try:
            _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
            _win._reload_current_view()
        finally:
            _win.art_cache = real
        self.assertEqual([(r["id"],r["art"]) for r in _win.model._rows],
                         [("3","cover"),("1","miss")])


class SortAndSearch(unittest.TestCase):
    def setUp(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()

    def test_header_sort_then_clear_restores(self):
        from desktop.model import COLUMNS
        self.assertEqual([r["id"] for r in _win.model._rows], ["3", "1"])
        bpm = next(i for i, c in enumerate(COLUMNS) if c[0] == "bpm")
        _win.table.sortByColumn(bpm, Qt.DescendingOrder)
        self.assertEqual([r["id"] for r in _win.model._rows], ["1", "3"])
        _win.table.sortByColumn(-1, Qt.AscendingOrder)
        self.assertEqual([r["id"] for r in _win.model._rows], ["3", "1"])

    def test_search_filters_titles(self):
        _win.search.setText("brav")
        self.assertEqual([r["id"] for r in _win.model._rows], ["1"])
        _win.search.setText("")
        self.assertEqual(len(_win.model._rows), 2)


class SettingsPage(unittest.TestCase):
    def picker(self, value):
        from contextlib import ExitStack
        from PySide6.QtWidgets import QColorDialog, QDialog
        dialog = QColorDialog(_win)
        stack = ExitStack()
        stack.enter_context(mock.patch('desktop.settings_page.QColorDialog', return_value=dialog))
        stack.enter_context(mock.patch.object(dialog, 'exec', return_value=(QDialog.Accepted if value.isValid() else QDialog.Rejected)))
        stack.enter_context(mock.patch.object(dialog, 'selectedColor', return_value=value))
        return stack

    def test_grid_shortcuts_dispatch_all_five_operations_and_quantize(self):
        from desktop.keyboard import Keymap, BY_ID
        mapping = Keymap()
        with mock.patch.object(_win, '_edit_grid') as edit, \
                mock.patch.object(_win.quantize_btn, 'click') as toggle:
            for action in ('downbeat', 'left', 'right', 'double', 'halve'):
                command = 'grid.' + action
                self.assertIn(command, BY_ID)
                self.assertEqual(mapping.bindings(command), [])
                release = _win._dispatch_keyboard_action(command)
                if action in ('left', 'right'):
                    edit.assert_called_with(action, track_id=_win.player_bar.current_track())
                else:
                    edit.assert_called_with(action)
                if release:
                    release()
            _win._dispatch_keyboard_action('grid.quantize')
            toggle.assert_called_once()
        self.assertNotIn('grid.bpm', BY_ID)

    def test_quantize_button_saves_global_setting_and_tracks_both_states(self):
        previous = _win.settings.get('quantize', False)
        try:
            _win._set_quantize(False, save=False)
            with mock.patch.object(_win, '_schedule_settings_save') as save, \
                    mock.patch.object(_win.player_bar.player, 'setPosition') as seek:
                _win.quantize_btn.click()
                self.assertTrue(_win.player_bar.quantize_enabled)
                self.assertTrue(_win.settings['quantize'])
                self.assertTrue(_win.quantize_btn.isChecked())
                self.assertEqual(save.call_count, 1)
                _win.quantize_btn.click()
                self.assertFalse(_win.player_bar.quantize_enabled)
                self.assertFalse(_win.settings['quantize'])
                self.assertFalse(_win.quantize_btn.isChecked())
                self.assertEqual(save.call_count, 2)
                seek.assert_not_called()
        finally:
            _win._set_quantize(previous, save=False)

    def test_quantize_persists_and_reloads_from_isolated_library_state(self):
        from desktop.app import MainWindow
        with tempfile.TemporaryDirectory(prefix='soundbox-quantize-') as area:
            first = MainWindow(isolated_dir=area)
            try:
                self.assertFalse(first.quantize_btn.isChecked())
                first.quantize_btn.click()
                self.assertTrue(first._save_state())
            finally:
                first.close()
                first.shutdown()
            second = MainWindow(isolated_dir=area)
            try:
                self.assertTrue(second.quantize_btn.isChecked())
                self.assertTrue(second.player_bar.quantize_enabled)
            finally:
                second.close()
                second.shutdown()

    def test_new_hot_and_memory_cues_capture_quantize_state_without_moving_playhead(self):
        pb = _win.player_bar
        track = _win.lib.tracks_by_id['1']
        with mock.patch.object(pb, '_track_id', '1'), \
                mock.patch.object(pb, '_src_path', str(track.path)), \
                mock.patch.object(pb, '_dur', 5000), \
                mock.patch.object(pb, '_beat_grid', [100, 767, 1433, 2100]), \
                mock.patch.object(pb, 'quantize_enabled', True), \
                mock.patch.object(pb, 'capture_cue_position', return_value=1101), \
                mock.patch.object(pb.player, 'setPosition') as seek, \
                mock.patch.object(_win, '_device_operation_owned', return_value=False), \
                mock.patch.object(_win.tag_controller, 'cue_reason', return_value=''), \
                mock.patch.object(_win.tag_controller, 'request_cue', return_value=True) as save:
            for kind in ('hot', 'memory'):
                self.assertTrue(_win._edit_cue(kind, 0, 'add'))
                self.assertEqual(save.call_args.args, ('1', kind, 0, 'add', 1433))
            captured = save.call_args.args
            pb.quantize_enabled = False
            self.assertTrue(_win._edit_cue('hot', 1, 'add'))
            self.assertEqual(save.call_args.args, ('1', 'hot', 1, 'add', 1101))
            self.assertEqual(captured[-1], 1433)
            seek.assert_not_called()

    def test_empty_number_sets_without_preview_and_command_number_deletes_active_page(self):
        pb = _win.player_bar
        panel = pb.cue_panel
        previous_cues = {k: list(v) for k, v in panel._cues.items()}
        previous_loaded = panel._loaded
        panel.set_cues([])
        panel.set_loaded(True)
        panel.hot_tab.click()
        try:
            with mock.patch.object(_win, '_space_should_play', return_value=True), \
                    mock.patch.object(_win, '_edit_cue', return_value=True) as edit, \
                    mock.patch.object(pb, '_start_cue_preview') as preview, \
                    mock.patch.object(pb, '_release_cue_preview') as release:
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier))
                edit.assert_called_once_with('hot', 0, 'add')
                # Simulate the completed save while the creation key is held.
                panel.set_cues([dict(kind='hot', slot=0, position_ms=10003)])
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, Qt.Key_1, Qt.NoModifier))
                preview.assert_not_called()
                release.assert_not_called()
                panel.memory_tab.click()
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_2, Qt.ControlModifier))
                self.assertEqual(edit.call_args.args, ('memory', 1, 'delete'))
        finally:
            _win._keyboard_router.release_all()
            panel._cues = previous_cues
            panel.set_loaded(previous_loaded)
            panel.hot_tab.click()

    def test_theme_color_updates_logo_styles_and_restores_saved_choice(self):
        from desktop import theme
        old = _win.settings.get('theme_accent', theme.DEFAULT_ACCENT)
        before = dict(_win.settings)
        try:
            with mock.patch.object(_win, '_schedule_settings_save') as save:
                _win._apply_theme_color('#168fc0', save=True)
                save.assert_called_once()
            self.assertEqual(_win.settings['theme_accent'], '#168fc0')
            self.assertEqual(theme.PURPLE, '#168fc0')
            self.assertIn('selection-background-color: #168fc0', theme.QSS)
            self.assertEqual(_win.wordmark.size().toTuple(), (270, 55))
            self.assertTrue(_win.wordmark.renderer.isValid())
            _win._apply_theme_color(_win.settings['theme_accent'], save=False)
            self.assertEqual(_win._theme_color.text(), '#168FC0')
            with self.picker(QColor()):
                _win._choose_theme_color()
            self.assertEqual(_win.settings['theme_accent'], '#168fc0')
        finally:
            _win._apply_theme_color(old, save=False)
            _win.settings = before

    def test_theme_text_choice_survives_accent_changes_and_preserves_custom_color(self):
        from desktop import theme
        before = dict(_win.settings)
        try:
            _win._apply_theme_color('#00ff00', save=False)
            with mock.patch.object(_win, '_schedule_settings_save'):
                _win._theme_text_mode.setCurrentIndex(_win._theme_text_mode.findData('black'))
                self.assertIn('selection-color: #000000', theme.QSS)
                self.assertEqual(_win.settings['theme_text_mode'], 'black')
                with self.picker(QColor('#123456')):
                    _win._choose_theme_text_color()
                self.assertEqual(_win.settings['theme_text_custom'], '#123456')
                self.assertEqual(_win._theme_text_mode.currentData(), 'custom')
                self.assertIn('selection-color: #123456', theme.QSS)
                _win._theme_text_mode.setCurrentIndex(_win._theme_text_mode.findData('white'))
                _win._apply_theme_color('#ffee00', save=False)
                self.assertIn('selection-color: #ffffff', theme.QSS)
                self.assertEqual(_win.settings['theme_text_custom'], '#123456')
                with self.picker(QColor()):
                    _win._choose_theme_text_color()
                self.assertEqual(_win.settings['theme_text_mode'], 'white')
                _win._theme_text_mode.setCurrentIndex(_win._theme_text_mode.findData('custom'))
                self.assertIn('selection-color: #123456', theme.QSS)
                self.assertEqual(_win._theme_text_color.text(), '#123456')
        finally:
            _win.settings = before
            _win._apply_theme_color(before.get('theme_accent', theme.DEFAULT_ACCENT), save=False)

    def test_theme_text_custom_preference_reloads_from_saved_isolated_state(self):
        from desktop.app import MainWindow
        from desktop import theme
        before = dict(_win.settings)
        try:
            with tempfile.TemporaryDirectory(prefix='sb-theme-text-') as area:
                first = MainWindow(isolated_dir=area)
                try:
                    first._apply_theme_color('#00ff00', save=False)
                    first._apply_theme_text('custom', '#112233', save=False)
                    self.assertTrue(first._save_state())
                finally:
                    first.shutdown()
                    first.close()
                second = MainWindow(isolated_dir=area)
                try:
                    self.assertEqual(second.settings['theme_accent'], '#00ff00')
                    self.assertTrue(second._settings_page_created)
                    self.assertFalse(second.isVisible())
                    self.assertEqual(second._theme_text_mode.currentData(), 'custom')
                    self.assertEqual(second._theme_text_color.text(), '#112233')
                    second._open_settings()
                    self.assertEqual(second._theme_text_mode.currentData(), 'custom')
                    self.assertEqual(second._theme_text_color.text(), '#112233')
                    self.assertIn('selection-color: #112233', theme.QSS)
                finally:
                    second.shutdown()
                    second.close()
        finally:
            _win.settings = before
            _win._apply_theme_color(before.get('theme_accent', theme.DEFAULT_ACCENT), save=False)

    def test_configured_toggle_cue_page_switches_both_directions_without_repeat_or_typing(self):
        from desktop.keyboard import Keymap
        panel = _win.player_bar.cue_panel
        old_map, old_page = _win._keyboard_router.keymap, panel.pages.currentIndex()
        mapping = Keymap()
        mapping.assign('cues.toggle_page', 'F2')
        _win._keyboard_router.keymap = Keymap(mapping.dump())
        try:
            panel.hot_tab.click()
            with mock.patch.object(_win, '_space_should_play', return_value=True):
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier))
                self.assertEqual(panel.pages.currentIndex(), 1)
                self.assertTrue(panel.memory_tab.isChecked())
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier, '', True))
                self.assertEqual(panel.pages.currentIndex(), 1)
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, Qt.Key_F2, Qt.NoModifier))
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier))
                self.assertEqual(panel.pages.currentIndex(), 0)
                self.assertTrue(panel.hot_tab.isChecked())
            with mock.patch.object(_win, '_space_should_play', return_value=False):
                _win.eventFilter(_win.search, QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier))
                self.assertEqual(panel.pages.currentIndex(), 0)
        finally:
            _win._keyboard_router.release_all()
            _win._keyboard_router.keymap = old_map
            (panel.hot_tab if old_page == 0 else panel.memory_tab).click()

    def test_keyboard_cue_numbers_follow_page_and_release_original_page(self):
        panel = _win.player_bar.cue_panel
        previous_cues = {kind: list(cues) for kind, cues in panel._cues.items()}
        previous_loaded = panel._loaded
        panel.set_cues([
            {"kind": kind, "slot": slot, "position_ms": (slot + 1) * (1000 if kind == "hot" else 2000)}
            for kind in ("hot", "memory") for slot in range(8)])
        panel.set_loaded(True)
        try:
            with mock.patch.object(_win, "_space_should_play", return_value=True), \
                    mock.patch.object(_win.player_bar, "_start_cue_preview") as pressed, \
                    mock.patch.object(_win.player_bar, "_release_cue_preview") as released:
                for page_key, kind in ((Qt.Key_9, "hot"), (Qt.Key_0, "memory")):
                    _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, page_key, Qt.NoModifier))
                    for slot in range(8):
                        code = Qt.Key_1 + slot
                        _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, code, Qt.NoModifier))
                        self.assertEqual(pressed.call_args.args[1], (kind, slot))
                        _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, code, Qt.NoModifier))
                        self.assertEqual(released.call_args.args[0], (kind, slot))
                panel.hot_tab.click()
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier))
                _win._dispatch_keyboard_action('cues.toggle_page')
                _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, Qt.Key_1, Qt.NoModifier))
                self.assertEqual(released.call_args.args[0], ("hot", 0))
                from desktop.keyboard import Keymap
                original_map = _win._keyboard_router.keymap
                alternate = Keymap(original_map.dump())
                alternate.assign("cues.slot.0", "F1")
                _win._keyboard_router.keymap = alternate
                try:
                    panel.hot_tab.click()
                    pressed.reset_mock()
                    released.reset_mock()
                    for code in (Qt.Key_1, Qt.Key_F1):
                        _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyPress, code, Qt.NoModifier))
                    pressed.assert_called_once()
                    _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, Qt.Key_1, Qt.NoModifier))
                    released.assert_not_called()
                    _win.eventFilter(_win.table, QKeyEvent(QEvent.KeyRelease, Qt.Key_F1, Qt.NoModifier))
                    released.assert_called_once_with(("hot", 0))
                finally:
                    _win._keyboard_router.release_all()
                    _win._keyboard_router.keymap = original_map
        finally:
            _win._keyboard_router.release_all()
            panel._cues = previous_cues
            panel.set_loaded(previous_loaded)
            panel.hot_tab.click()

    def test_gear_toggles_page(self):
        _win._open_settings()
        self.assertEqual(_win.body_stack.currentIndex(), 1)
        _win._open_settings()  # gear acts as Back
        self.assertEqual(_win.body_stack.currentIndex(), 0)

    def test_master_toggle_updates_settings_and_persists(self):
        _win._chk_hide.setChecked(True)  # real slot → save (qttest sandbox)
        try:
            self.assertTrue(_win.settings["hide_untracked"])
            self.assertTrue(_win.lib.hide_untracked)
            self.assertTrue(_win._data_file.exists())
            self.assertEqual(_win._data_file.parent, Path(
                QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)))
            self.assertTrue(_win._data_file.parent.name.startswith(
                "soundbox-ui-tests-"))
        finally:
            _win._chk_hide.setChecked(False)
        self.assertFalse(_win.settings["hide_untracked"])

    def test_waveform_mode_selector_applies_and_persists(self):
        original = _win.settings.get("waveform_mode", "3BAND")
        seen = []
        real = _win.player_bar.set_waveform_mode
        _win.player_bar.set_waveform_mode = seen.append
        try:
            _win._waveform_mode_buttons["RGB"].click()
            self.assertEqual(_win.settings["waveform_mode"], "RGB")
            self.assertEqual(seen, ["RGB"])
            self.assertTrue(_win._waveform_mode_buttons["RGB"].isChecked())
            self.assertTrue(_win._data_file.exists())
        finally:
            _win.player_bar.set_waveform_mode = real
            _win._waveform_mode_buttons[original].click()


class LibraryBackups(unittest.TestCase):
    def test_save_rotates_backups(self):
        import os as _os
        import time as _time
        bdir = _win._data_file.parent / "backups"
        for p in bdir.glob("library-*.json") if bdir.exists() else []:
            p.unlink()
        _win._save_state()                      # ensure library.json exists
        _win.custom["BK TEST"] = ["1"]
        _win._save_state()                      # first backup of the pre-edit state
        backups = list(bdir.glob("library-*.json"))
        self.assertEqual(len(backups), 1)
        # Unchanged content + within the hour → no second backup.
        _win._save_state()
        self.assertEqual(len(list(bdir.glob("library-*.json"))), 1)
        # Age the backup past the throttle and change content → a second one.
        _os.utime(backups[0], (backups[0].stat().st_atime,
                               _time.time() - 7200))
        _win.custom.pop("BK TEST")
        _win._save_state()
        self.assertEqual(len(list(bdir.glob("library-*.json"))), 2)

    def test_backup_failure_never_blocks_save(self):
        # Make the backup path GENUINELY fail: a file squatting where the
        # backups directory must go → mkdir raises OSError inside _backup_state.
        import shutil
        bdir = _win._data_file.parent / "backups"
        if bdir.is_dir():
            shutil.rmtree(bdir)
        bdir.write_text("squatter")
        try:
            _win._save_state()  # must not raise; library.json must still land
            self.assertTrue(_win._data_file.exists())
        finally:
            bdir.unlink()


class DeviceLabels(unittest.TestCase):
    def test_pioneer_tree_refuses_browser_import_and_delete(self):
        from desktop import device_browser
        from PySide6.QtWidgets import QMessageBox

        path = Path("/Volumes/IPOD/PIONEER/rekordbox/export.pdb")
        with mock.patch.object(
                device_browser, "_in_external_product_tree",
                return_value=True), mock.patch.object(
                    _win, "_start_pulse") as pulse, mock.patch.object(
                        QMessageBox, "critical") as critical:
            _win._import_device_paths([path])
            _win._delete_paths([path])

        pulse.assert_not_called()
        critical.assert_called_once()

    def test_usb_tab_click_refreshes_an_already_checked_button_but_programmatic_check_does_not(self):
        previous = _win.device_tab_group.checkedButton()
        button = _win.device_tab_buttons[1]
        try:
            _win.device_tab_buttons[0].setChecked(True)
            with mock.patch.object(_win, '_set_device_tab') as choose:
                button.setChecked(True)
                choose.assert_called_once_with(1)
                choose.reset_mock()
                button.click()
                button.click()
                self.assertEqual(choose.call_args_list, [mock.call(1, refresh=True), mock.call(1, refresh=True)])
        finally:
            if previous is not None:
                previous.setChecked(True)

    def test_drift_keeps_capacity_label_and_uses_tooltip(self):
        s = _win._device_label("IPOD", 5_000_000_000, 2)
        self.assertEqual(s, 'IPOD · USB · 5.0 GB')
        self.assertIn('⚠ Sync needed', _win._device_tip('/Volumes/X', 1_000_000_000, drift=2))
        self.assertNotIn("2 changes", s)

    def test_non_fat_warning(self):
        s = _win._device_label("IPOD", 5_000_000_000, 0, "exfat")
        self.assertEqual(s, 'IPOD · USB · 5.0 GB')
        with mock.patch('desktop.app.device_fs', return_value='exfat'):
            tip = _win._device_tip('/Volumes/X', 1_000_000_000)
        self.assertIn('requires FAT32', tip)
        self.assertIn('exfat', tip)

    def test_fat_shows_capacity_without_free_label(self):
        s = _win._device_label("IPOD", 5_000_000_000, 0, "msdos")
        self.assertEqual(s, 'IPOD · USB · 5.0 GB')
        self.assertNotIn('free', s)

    def test_tooltip_warns_non_fat(self):
        tip = _win._device_tip("/Volumes/X", 1_000_000_000)
        self.assertIn("Filesystem", tip)

    def test_dual_boot_label_is_explicit(self):
        from types import SimpleNamespace
        profile = SimpleNamespace(kind='dual_boot', label="iTunes/Rockbox")
        label = _win._device_label(
            "IPOD", 5_000_000_000, 0, "msdos", profile)
        self.assertEqual(label, 'IPOD · iTunes/Rockbox iPod · 5.0 GB')

    def test_rockbox_membership_persists_custom_playlist_and_refreshes(self):
        from types import SimpleNamespace
        from soundbox.dual_ipod import DualPlaylistAddition, DualPlaylistUpdate

        old_custom = dict(_win.custom)
        old_additions = dict(_win.playlist_additions)
        _win.custom["Device Picks"] = ["1", "1"]
        try:
            count = _win._persist_rockbox_playlist_additions(SimpleNamespace(
                playlist_additions=(DualPlaylistAddition("cust:Device Picks", ("2",)),),
                playlist_updates=(DualPlaylistUpdate(
                    "cust:Device Picks", ("1", "1"), ("1", "2")),),
            ))
            self.assertEqual(count, 1)
            self.assertEqual(_win.custom["Device Picks"], ["1", "2"])
            self.assertEqual(_win.playlist_additions, old_additions)
            saved = json.loads(_win._data_file.read_text())
            self.assertEqual(saved["custom"]["Device Picks"], ["1", "2"])
            _win._show_custom("Device Picks")
            self.assertEqual([r["id"] for r in _win.model._rows], ["1", "2"])
        finally:
            _win.custom = old_custom
            _win._refresh_custom()
            _win._save_state()

    def test_rockbox_membership_rejects_source_playlist_and_concurrent_edit(self):
        from types import SimpleNamespace
        from soundbox.dual_ipod import DualPlaylistUpdate

        for update in (
            DualPlaylistUpdate("fixture-source", (), ("2",)),
            DualPlaylistUpdate("cust:Missing Playlist", ("1",), ("1", "2")),
        ):
            with self.assertRaises(RuntimeError):
                _win._persist_rockbox_playlist_additions(SimpleNamespace(
                    playlist_additions=(), playlist_updates=(update,)))

    def test_rockbox_membership_save_failure_restores_in_memory_playlist(self):
        from types import SimpleNamespace
        from soundbox.dual_ipod import DualPlaylistUpdate

        old_custom = dict(_win.custom)
        _win.custom["Device Picks"] = ["1", "1"]
        try:
            with mock.patch.object(_win, "_save_state", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "could not save"):
                    _win._persist_rockbox_playlist_additions(SimpleNamespace(
                        playlist_additions=(), playlist_updates=(DualPlaylistUpdate(
                            "cust:Device Picks", ("1", "1"), ("1", "2")),)))
            self.assertEqual(_win.custom["Device Picks"], ["1", "1"])
        finally:
            _win.custom = old_custom


class StockIpodUI(unittest.TestCase):
    def test_device_poll_backs_off_only_while_no_mount_exists(self):
        from desktop.app import (
            _DEVICE_POLL_ACTIVE_MS,
            _DEVICE_POLL_IDLE_MS,
        )

        with mock.patch.object(_win._dev_timer, "setInterval") as set_interval:
            _win._set_device_poll_interval(False)
            set_interval.assert_called_once_with(_DEVICE_POLL_IDLE_MS)
            set_interval.reset_mock()
            _win._set_device_poll_interval(True)
            set_interval.assert_called_once_with(_DEVICE_POLL_ACTIVE_MS)

    def test_composite_playlist_delete_routes_through_unified_zero_plan(self):
        from PySide6.QtWidgets import QMessageBox
        from soundbox.dual_ipod import composite_playlist_entry

        playlist = Playlist("Only", [], keys=[], pid="only")
        row = composite_playlist_entry(
            _win.lib,
            playlist,
            artist_overrides=_win.overrides,
            title_overrides=_win.titles,
        )
        current_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["only"],
            "playlists": {"only": row},
            "files": {},
        }
        old_playlists = _win._sync_playlists
        old_begin = _win._begin_dual_ipod_sync
        events = []
        try:
            _win._sync_playlists = (
                lambda: events.append(("validate",)) or [playlist])
            _win._begin_dual_ipod_sync = (
                lambda dev, playlists, **kwargs:
                events.append(("begin", dev, playlists, kwargs)))
            with (
                mock.patch.object(
                    _win, "_cached_composite_manifest",
                    return_value=current_manifest,
                ),
                mock.patch.object(
                    QMessageBox,
                    "question",
                    side_effect=lambda *args, **kwargs:
                    events.append(("confirm",)) or QMessageBox.Yes,
                ),
            ):
                _win._begin_composite_playlist_delete(
                    "/Volumes/IPOD", ("only",))
        finally:
            _win._sync_playlists = old_playlists
            _win._begin_dual_ipod_sync = old_begin

        self.assertEqual(
            [event[0] for event in events],
            ["confirm", "validate", "begin"],
        )
        self.assertEqual(events[2][1], "/Volumes/IPOD")
        self.assertEqual(events[2][2], [])
        self.assertTrue(events[2][3]["reconcile"])
        self.assertTrue(events[2][3]["confirmed"])
        self.assertEqual(events[2][3]["action"], "delete")
        self.assertNotIn("expected_playlist_rows", events[2][3])

    def test_composite_playlist_delete_retains_missing_playlist_snapshot(self):
        from PySide6.QtWidgets import QMessageBox

        retained = Playlist(
            "Retained", ["GENRES"], ["one", "two"], pid="retained")
        removed = Playlist("Remove", [], [], pid="remove")
        current_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["retained", "remove"],
            "playlists": {
                "retained": {
                    "pid": "retained",
                    "name": "Retained",
                    "folder_path": ["GENRES"],
                    "tracks": ["one", "two"],
                },
                "remove": {
                    "pid": "remove",
                    "name": "Remove",
                    "folder_path": [],
                    "tracks": [],
                },
            },
            "files": {},
        }
        old_playlists = _win._sync_playlists
        old_begin = _win._begin_dual_ipod_sync
        events = []
        try:
            # The retained source playlist disappeared after the last commit.
            # Explicit Delete must preserve its committed snapshot instead of
            # forcing a separate Sync or deleting that unrelated identity.
            _win._sync_playlists = lambda: [removed]
            _win._begin_dual_ipod_sync = (
                lambda dev, playlists, **kwargs:
                events.append((dev, playlists, kwargs)))
            with (
                mock.patch.object(
                    _win, "_cached_composite_manifest",
                    return_value=current_manifest,
                ),
                mock.patch.object(
                    QMessageBox, "question", return_value=QMessageBox.Yes),
            ):
                _win._begin_composite_playlist_delete(
                    "/Volumes/IPOD", ("remove",))
        finally:
            _win._sync_playlists = old_playlists
            _win._begin_dual_ipod_sync = old_begin

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "/Volumes/IPOD")
        self.assertEqual(events[0][1], [retained])
        self.assertTrue(events[0][2]["reconcile"])
        self.assertEqual(events[0][2]["action"], "delete")
        self.assertNotIn("expected_playlist_rows", events[0][2])

    def test_device_operation_waits_for_inflight_refresh(self):
        class FakeSignal:
            def __init__(self):
                self.slots = []

            def connect(self, slot):
                self.slots.append(slot)

            def emit(self):
                for slot in tuple(self.slots):
                    slot()

        class FakeWorker:
            def __init__(self):
                self.running = True
                self.finished = FakeSignal()
                self.results = []
                self.new_paths = ()
                self.retry_discovery = False

            def isRunning(self):
                return self.running

            def deleteLater(self):
                pass

        old_worker = _win._dev_scan_worker
        old_pending = _win._pending_device_operation
        old_exporting = _win._exporting
        worker = FakeWorker()
        worker.finished.connect(lambda: _win._finish_device_scan_worker(worker))
        resumed = []
        try:
            _win._dev_scan_worker = worker
            _win._pending_device_operation = None
            _win._exporting = False
            self.assertTrue(
                _win._defer_device_operation_until_scan_finishes(
                    lambda: resumed.append("started")))
            self.assertTrue(_win._exporting)
            self.assertEqual(resumed, [])

            worker.running = False
            with mock.patch.object(_win, '_on_devices_scanned'):
                worker.finished.emit()
            self.assertFalse(_win._exporting)
            self.assertEqual(resumed, ["started"])
        finally:
            _win._stop_pulse()
            _win._dev_scan_worker = old_worker
            _win._pending_device_operation = old_pending
            _win._exporting = old_exporting

    def test_stock_device_header_has_requested_tabs(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QTreeWidgetItem
        from desktop.trees import DEVPATH_ROLE, DEVROLE_ROLE
        dev='/Volumes/HEADER-STOCK-FIXTURE'
        item=QTreeWidgetItem(['Stock iPod'])
        item.setData(0,DEVPATH_ROLE,dev);item.setData(0,DEVROLE_ROLE,'')
        old_path,old_role=_win._current_device_path,_win._current_device_role
        try:
            with (mock.patch.dict(_win._device_profiles,{dev:SimpleNamespace(kind='stock_ipod',has_stock_ipod=True)}),
                  mock.patch.object(_win,'_read_stock_device_view') as read):
                _win._on_device_click(item,0)
                read.assert_called_once_with(dev,_win._device_profiles[dev])
                self.assertEqual(_win._current_device_role,'')
                self.assertEqual(
                    [button.text() for button in _win.device_tab_buttons[:3]],
                    ['Device','Songs','Playlists'])
                _win.device_tab_buttons[2].setChecked(True)
                self.assertTrue(_win.device_tab_buttons[2].isChecked())
                self.assertEqual(_win.stock_ipod_pages.stack.currentIndex(),2)
        finally:
            _win._current_device_path,_win._current_device_role=old_path,old_role
            for button in _win.device_tab_buttons:
                button.setVisible(False)

    def test_device_headers_and_detail_rows_match_the_regular_browser_grid(self):
        from PySide6.QtWidgets import QWidget
        from desktop.stock_device_view import StockIpodPages
        from PySide6.QtWidgets import QTreeWidgetItem
        header = _win.findChild(QWidget,'DeviceHeader')
        self.assertEqual(header.height(),_win.table.horizontalHeader().height())
        self.assertEqual(header.height(),26)
        tree = StockIpodPages._detail_tree('Grid')
        try:
            item = QTreeWidgetItem(tree,['One','Two'])
            QTreeWidgetItem(tree,['Three','Four'])
            tree.resize(400,160);tree.show();_app.processEvents()
            self.assertEqual(tree.visualItemRect(item).height(),24)
            self.assertEqual(tree.header().height(),26)
            pixels = tree.viewport().grab().toImage()
            from desktop import theme
            from PySide6.QtGui import QColor
            self.assertEqual(pixels.pixelColor(200,60),QColor(theme.ROW))
            self.assertEqual(pixels.pixelColor(200,84),QColor(theme.ROW_ALT))
        finally:
            tree.close()

    def test_usb_playlist_columns_preserve_stock_frame_when_browser_columns_change(self):
        header = _win.table.horizontalHeader()
        before = header.saveState()
        previous=(_win.size(),_win.isVisible(),_win.stack.currentIndex())
        try:
            _win.stack.setCurrentIndex(0);_win.show();_app.processEvents()
            _win.table.setColumnHidden(8,False)
            _win.table.setColumnHidden(2,True)
            _win.table.setColumnWidth(3,92)
            _win._sync_artist_columns()
            tree = _win.usb_device_pages.playlist_tree
            stock = _win.stock_ipod_pages.playlist_tree
            for width in (1100,1500):
                _win.resize(width,_win.height());_app.processEvents()
                self.assertEqual(tree.columnCount(),3)
                self.assertEqual([tree.headerItem().text(i) for i in range(3)],
                                 ['Playlist / Track','Tracks','Type'])
                for column in range(3):
                    self.assertFalse(tree.isColumnHidden(column))
                    self.assertEqual(tree.isColumnHidden(column),stock.isColumnHidden(column))
                    self.assertEqual(tree.header().visualIndex(column),stock.header().visualIndex(column))
                self.assertEqual((tree.columnWidth(1),tree.columnWidth(2)),(70,150))
                self.assertEqual(_win.artists_view.isColumnHidden(2),_win.table.isColumnHidden(2))
                self.assertEqual(_win.artists_view.columnWidth(3),_win.table.columnWidth(3))
            table_width=header.sectionSize(1)
            tree.setColumnWidth(1,81)
            self.assertEqual(header.sectionSize(1),table_width)
            tree.setColumnWidth(1,70)
        finally:
            header.restoreState(before)
            _win._sync_artist_columns()
            size,visible,page=previous
            _win.resize(size);_win.stack.setCurrentIndex(page);_win.setVisible(visible)

    def test_usb_playlist_refits_browser_columns_when_window_resizes(self):
        previous = (_win.stack.currentIndex(),_win.device_content_stack.currentIndex(),
                    _win.usb_device_pages.stack.currentIndex(),_win.size(),_win.isVisible())
        header = _win.table.horizontalHeader()
        state = header.saveState()
        try:
            _win.device_content_stack.setCurrentIndex(2)
            _win.usb_device_pages.set_page(1)
            _win.stack.setCurrentIndex(1)
            _win.show()
            for width in (1100,1500):
                _win.resize(width,_win.height());_app.processEvents()
                view = _win.usb_device_pages.playlist_tree
                self.assertEqual(view.header().length(),view.viewport().width())
                self.assertFalse(view.horizontalScrollBar().isVisible())
        finally:
            page,device,usb,size,visible = previous
            _win.resize(size)
            _win.stack.setCurrentIndex(page)
            _win.device_content_stack.setCurrentIndex(device)
            _win.usb_device_pages.set_page(usb)
            header.restoreState(state)
            _win._sync_artist_columns()
            _win.setVisible(visible)

    def test_usb_native_rows_load_metadata_and_restore_expansion_selection_and_tab(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from desktop.devices import DeviceFacts
        from desktop.usb_device_view import UsbDevicePages,UsbSnapshot
        from soundbox.cdj_usb import CdjLibrary,CdjPlaylist,CdjSong
        from pathlib import Path
        pages = UsbDevicePages()
        song = CdjSong('7','/Contents/device.wav','Device title','Device artist',14000,'8A',137,320,artwork='/PIONEER/art.jpg')
        native = CdjLibrary((CdjPlaylist('1','0','Fixture',False,((song.title,song.path),)),),
            'USB',8,8,(),1,(),songs=(song,))
        snapshot = UsbSnapshot(Path('/Volumes/NO-LIVE-READ'),1,DeviceFacts(volume_uuid='fake'),native=native)
        args = dict(name='USB',profile=SimpleNamespace(kind='generic_usb',label='USB',rockbox_export=True),free=1,drift=0)
        try:
            pages.set_snapshot(snapshot,**args)
            pages.resize(950,900);pages.show();_app.processEvents()
            self.assertEqual(len(pages.controls),7)
            self.assertTrue(all(control.height()==28 for control in pages.controls.values()))
            self.assertTrue(pages.apply_settings.isVisible())
            pages.set_page(1)
            parent = pages.playlist_tree.topLevelItem(0)
            parent.setExpanded(True)
            row = parent.child(0)
            self.assertEqual([row.text(i) for i in range(3)],['1. Device title','','USB'])
            details=pages._display_rows[song.id]
            self.assertEqual([details[key] for key in ('title','artist','bpm','key','time','format')],
                             ['Device title','Device artist','140.00','8A','2:17','WAV'])
            self.assertEqual(row.data(0,Qt.UserRole),song)
            self.assertEqual(row.toolTip(0),'Device title\nDevice artist\n/Contents/device.wav')
            pages.playlist_tree.setCurrentItem(row)
            received = []
            pages.song_requested.connect(received.append)
            pages._activate_song(row)
            self.assertEqual(received,[song])
            pages.set_snapshot(replace(snapshot),**args)
            _app.processEvents()
            self.assertEqual(pages.stack.currentIndex(),1)
            self.assertTrue(pages.playlist_tree.topLevelItem(0).isExpanded())
            self.assertEqual(pages.playlist_tree.currentItem().text(0),'1. Device title')
            self.assertEqual(pages.playlist_tree.currentItem().data(0,Qt.UserRole),song)
        finally:
            pages.close()

            # Rendering dispatches the shared fixture's accepted preference
            # timer. Its real ACK must retire before the next idle-status test.
            from time import monotonic
            from PySide6.QtTest import QTest
            self.assertTrue(_win._flush_settings_preferences())
            def pending_preferences():
                committer = _win._state_committer
                return (bool(committer and committer.pending)
                        or _win._state_publication
                        or _win.tag_controller.state_commit_owned
                        or bool(_win._settings_dirty_keys)
                        or _win._settings_save_timer.isActive()
                        or bool(_win._prep_update_message))
            deadline = monotonic() + 5
            while pending_preferences() and monotonic() < deadline:
                QTest.qWait(5)
            self.assertFalse(pending_preferences(),
                             'USB view preferences did not finish their durable acknowledgement')

    def test_stock_view_memory_retains_nested_selection_and_distinct_detail_panels(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QTreeWidgetItem
        from desktop.cdj import CdjDeviceMixin
        from desktop.stock_device_view import StockIpodPages
        pages = StockIpodPages()
        parent = QTreeWidgetItem(pages.playlist_tree,['Set'])
        parent.setData(0,Qt.UserRole,'18446744073709551615')
        child = QTreeWidgetItem(parent,['Song'])
        parent.setExpanded(True)
        pages.playlist_tree.setCurrentItem(child)
        pages.set_page(2)
        host = SimpleNamespace(_current_device_path='/not-live',
            _device_profiles={'/not-live':SimpleNamespace(has_stock_ipod=True)},
            _stock_snapshot=SimpleNamespace(target=Path('/not-live'),identity_key='fixture'),
            stock_ipod_pages=pages,settings={},_shutting_down=False,
            _schedule_settings_save=lambda *_keys:None,_set_stock_device_tab=pages.set_page)
        try:
            CdjDeviceMixin._remember_stock_device_view(host)
            host.settings = json.loads(json.dumps(host.settings))
            state = host.settings['device_views']['stock']['fixture']
            self.assertEqual(state['views']['StockDevicePlaylists']['selected'],[0,0])
            self.assertEqual(len([key for key in state['views'] if key.startswith('StockDeviceDetails:')]),4)
            pages.set_page(0)
            parent.setExpanded(False)
            pages.playlist_tree.setCurrentItem(parent)
            CdjDeviceMixin._restore_stock_device_view(host)
            self.assertEqual(pages.stack.currentIndex(),2)
            self.assertTrue(parent.isExpanded())
            self.assertIs(pages.playlist_tree.currentItem(),child)
        finally:
            pages.close()

    def test_usb_load_uses_device_audio_and_analysis_without_source_cue_overlay(self):
        from desktop.usb_device_view import UsbSnapshot
        from desktop.devices import DeviceFacts
        from soundbox.cdj_usb import CdjSong
        from pathlib import Path
        previous = (_win.usb_device_pages._snapshot,_win._cdj_worker)
        target = Path('/Volumes/NO-LIVE-USB-PLAYBACK')
        snapshot = UsbSnapshot(target,9,DeviceFacts(volume_uuid='fixture'))
        song = CdjSong('7','/Contents/actual.wav','Actual export','Device artist',14000,
                       analysis='/PIONEER/USBANLZ/A/ANLZ0000.DAT')
        started = []
        try:
            _win.usb_device_pages._snapshot = snapshot
            with mock.patch.object(_win,'_defer_device_operation_until_scan_finishes',return_value=False), \
                 mock.patch.object(_win,'_device_operation_owned',return_value=False), \
                 mock.patch.object(_win,'_start_device_worker',side_effect=lambda w,*_:started.append(w)) as start, \
                 mock.patch('soundbox.cdj_usb.prepare_playback',return_value=(target/'Contents/actual.wav',target/'PIONEER/USBANLZ/A/ANLZ0000.DAT')) as prepare, \
                 mock.patch.object(_win.player_bar,'play_track') as play, \
                 mock.patch.object(_win.tag_controller,'session_cues',side_effect=AssertionError('Source cue overlay')):
                _win._load_usb_song(song)
                worker = started[0]
                start.assert_called_once_with(worker,str(target),'inspect')
                worker.run()
                worker.finished.emit()
                _app.processEvents()
                prepare.assert_called_once_with(target,9,song)
                args,kwargs = play.call_args
                self.assertTrue(args[0].startswith('usb:fixture:'))
                self.assertEqual(args[1],target/'Contents/actual.wav')
                self.assertEqual(kwargs['analysis_path'],str(target/'PIONEER/USBANLZ/A/ANLZ0000.DAT'))
                self.assertIsNone(kwargs['cues'])
                self.assertIsNone(kwargs['autoplay'])
        finally:
            _win.usb_device_pages._snapshot,_win._cdj_worker = previous

    def test_usb_click_requests_metadata_and_opens_tabs_without_raw_filesystem_enumeration(self):
        from pathlib import Path
        from types import SimpleNamespace
        from desktop.devices import DeviceFacts
        from desktop.usb_device_view import UsbSnapshot, UsbPlaylist
        from desktop.trees import DEVPATH_ROLE
        dev = '/Volumes/SYNTHETIC-USB-VIEW'
        previous = (_win._current_device_path, dict(_win._device_profiles),
                    dict(_win._usb_view_cache), dict(_win._usb_device_rows),
                    set(_win._usb_view_requests), _win.device_content_stack.currentIndex(),
                    _win._current_device_role)
        snapshot = UsbSnapshot(Path(dev), 77, DeviceFacts(capacity_bytes=1000),
                               (UsbPlaylist('Set', 'Playlists/Set.m3u8', (('Track','/Music/a.wav'),)),))
        profile = SimpleNamespace(has_stock_ipod=False, label='USB', rockbox_export=True)
        class Item:
            def data(self, _column, _role):
                return dev if _role==DEVPATH_ROLE else None
        try:
            _win._device_profiles[dev] = profile
            _win._usb_view_cache[dev] = snapshot
            _win._usb_device_rows[dev] = ('USB',400,0,profile)
            with mock.patch.object(_win.fs_model, 'setRootPath', side_effect=AssertionError('Root enumeration')),                     mock.patch.object(_win, '_refresh_devices') as refresh:
                _win._on_device_click(Item(), 0)
                self.assertEqual(_win.device_content_stack.currentIndex(), 2)
                self.assertEqual(_win.usb_device_pages.stack.currentIndex(), 0)
                self.assertEqual([b.text() for b in _win.device_tab_buttons[:2]], ['Overview','Playlists'])
                self.assertFalse(_win.device_tab_buttons[2].isHidden())
                self.assertEqual([b.text() for b in _win.device_tab_buttons],
                    ["Overview","Playlists","Category","Sort","Column","Color","My Settings"])
                refresh.assert_called_once()
                _win.device_tab_buttons[1].click()
                self.assertEqual(_win.usb_device_pages.stack.currentIndex(), 1)
                self.assertEqual(refresh.call_count, 2)
                self.assertIn(dev, _win._usb_view_requests)
                item = _win.usb_device_pages.playlist_tree.topLevelItem(0)
                self.assertEqual(item.childCount(), 0)
                item.setExpanded(True)
                self.assertEqual(item.child(0).text(0), '1. Track')
        finally:
            current, profiles, cache, rows, requests, page, role = previous
            _win._current_device_path = current
            _win._current_device_role = role
            _win._device_profiles = profiles
            _win._usb_view_cache = cache
            _win._usb_device_rows = rows
            _win._usb_view_requests = requests
            _win.device_content_stack.setCurrentIndex(page)
            for button, label in zip(_win.device_tab_buttons, ('Device','Songs','Playlists')):
                button.setText(label)
                button.hide()

    def test_device_header_export_opens_selector_for_current_device(self):
        from types import SimpleNamespace
        old_sync = _win._show_export_dialog
        old_current = _win._current_device_path
        old_profiles = _win._device_profiles
        calls = []
        try:
            _win._current_device_path = "/Volumes/IPOD"
            _win._device_profiles = {"/Volumes/IPOD":SimpleNamespace(kind='generic_usb')}
            _win._show_export_dialog = calls.append
            _win._update_device_action_state()
            self.assertEqual(_win.device_sync_btn.text(),'Export')
            _win.device_sync_btn.click()
        finally:
            _win._show_export_dialog = old_sync
            _win._current_device_path = old_current
            _win._device_profiles = old_profiles
            _win._update_device_action_state()
        self.assertEqual(calls, ["/Volumes/IPOD"])
        try:
            _win._start_pulse("Syncing iPod")
            self.assertGreaterEqual(
                _win.status_label.width(),
                _win.status_label.fontMetrics().horizontalAdvance(
                    "Syncing iPod..."),
            )
            self.assertLess(
                _win.status_label.width(),
                _win.status_label.fontMetrics().horizontalAdvance(
                    "iPod playlist deletion cancelled — nothing written"),
            )
        finally:
            _win._stop_pulse()
            _win.status_label.setText("Ready")
        self.assertGreaterEqual(
            _win.status_count_label.width(),
            _win.status_count_label.fontMetrics().horizontalAdvance(
                "9999/9999"),
        )
        self.assertEqual(_win.progress.width(), 208)

    def test_analysis_pause_status_fits_after_shorter_pulse(self):
        try:
            _win._on_analysis_tasks_ready(3)
            _win._on_analysis_storage_paused(
                True, 19 * 1024 ** 3, 25 * 1024 ** 3)
            self.assertIn("Low disk space", _win.status_label.text())
            self.assertGreaterEqual(
                _win.status_label.width(),
                _win.status_label.fontMetrics().horizontalAdvance(
                    _win.status_label.text()))
            self.assertIn("25 GiB", _win.status_label.toolTip())
            self.assertFalse(_win._pulse_timer.isActive())
            _win._preparing_library = False
            _win._set_status_text("Ready")

            self.assertNotIn("25 GiB", _win.status_label.toolTip())
            self.assertLess(_win.status_label.width(), 100)
        finally:
            _win._preparing_library = False
            _win._prep_storage_pause = None
            _win._stop_pulse()
            _win._set_status_text("Ready")

    def test_footer_controls_stay_anchored_across_device_phases_and_counts(self):
        bar = _win.progress.parentWidget()
        previous = (bar.minimumWidth(), bar.maximumWidth(), bar.size(),_win.isVisible())
        try:
            _win.show();_app.processEvents()
            _win.progress.show()
            _win.cancel_btn.show()
            for width in (1240, 540):
                bar.setFixedWidth(width)
                anchor = None
                for phase, total in (("Planning iPod", None), ("Copying", 37),
                        ("Verifying", 123456), ("Finalizing iPod", None),
                        ("Finishing a very long device operation safely", 37)):
                    _win._start_pulse(phase)
                    if total is not None:_win._set_status_count(1, total)
                    _app.processEvents()
                    positions = (_win.progress.geometry(), _win.cancel_btn.geometry())
                    if anchor is None:anchor = positions
                    self.assertEqual(positions, anchor)
                    self.assertEqual(_win.progress.width(), 208)
                    self.assertTrue(bar.rect().contains(_win.cancel_btn.geometry()))
        finally:
            minimum, maximum, size, visible = previous
            bar.setMinimumWidth(minimum)
            bar.setMaximumWidth(maximum)
            bar.resize(size)
            _win.progress.hide()
            _win.cancel_btn.hide()
            _win._stop_pulse()
            _win._set_status_text("Ready")
            _win.setVisible(visible)

    def test_file_phases_do_not_replace_or_resize_overall_sync_status(self):
        from soundbox.device_operations import DeviceCommand, DeviceOperationState, Phase

        worker = object()
        active = DeviceOperationState(
            DeviceCommand(DeviceSessionIdentity('/fixture/iPod', 1), 'commit-dual'),
            Phase.EXECUTING, worker)
        try:
            _win._on_dual_ipod_progress(9, 37, "track.wav")
            _win._pulse_tick()
            text = _win.status_label.text()
            widths = (_win.status_label.width(), _win.status_count_label.width())
            value = _win.progress.value()
            with mock.patch.object(_win._device_coordinator, "active", active):
                for stage, done, total in [
                        ("Acquiring source", 0, 9_000_000_000),
                        ("Copying to device", 0, 14_500),
                        ("Verifying device copy", 14_500, 14_500)] * 10:
                    _win._on_device_phase_progress(
                        worker, stage, done, total, "fixture.wav")
                    self.assertEqual(_win.status_label.text(), text)
                    self.assertEqual(_win.status_count_label.text(), "9/37")
                    self.assertEqual(
                        (_win.status_label.width(), _win.status_count_label.width()),
                        widths)
                    self.assertEqual(_win.progress.value(), value)
                    self.assertTrue(_win._pulse_timer.isActive())
                self.assertIn("Verifying device copy", _win.status_label.toolTip())
                self.assertIn("14.5 KB", _win.status_label.toolTip())
                active.cancellation.set()
                _win._start_pulse("Cancelling")
                _win._on_device_phase_progress(worker, "Copying to device", 0, 1, "late")
                self.assertEqual(_win.status_label.text(), "Cancelling")
                self.assertNotIn("late", _win.status_label.toolTip())
        finally:
            _win._stop_pulse()
            _win._set_status_text("Ready")
            _win.progress.setVisible(False)

    def test_static_statuses_stop_old_animation_and_fit_their_text(self):
        try:
            for text in ("Ready", "Prep paused · Low Power", "Fetch failed ✕",
                         "iPod playlist deletion cancelled — nothing written",
                         "Stopping device work safely…", "Complete ✓"):
                with self.subTest(text=text):
                    _win._start_pulse("Loading")
                    _win._set_status_text(text)
                    _win._pulse_tick()  # already-queued tick must be harmless
                    self.assertEqual(_win.status_label.text(), text)
                    self.assertFalse(_win._pulse_timer.isActive())
                    self.assertGreaterEqual(
                        _win.status_label.width(),
                        _win.status_label.fontMetrics().horizontalAdvance(text))
        finally:
            _win._set_status_text("Ready")

    def test_older_flash_timer_cannot_clear_a_newer_identical_message(self):
        callbacks = []
        with mock.patch("desktop.app.QTimer.singleShot",
                        side_effect=lambda _ms, _context, callback: callbacks.append(callback)):
            _win._flash_status("Updated ✓", 6)
            _win._flash_status("Updated ✓", 6)
        callbacks[0]()
        self.assertEqual(_win.status_label.text(), "Updated ✓")
        callbacks[1]()
        self.assertEqual(_win.status_label.text(), "Ready")

    def test_verify_owns_status_and_idle_restores_analysis_pause(self):
        from types import SimpleNamespace
        from soundbox.device_operations import Phase

        try:
            _win._on_analysis_tasks_ready(3)
            _win._start_pulse("Verifying")
            with mock.patch.object(_win._device_coordinator, "active",
                                   SimpleNamespace(phase=Phase.INSPECTING)):
                _win._on_analysis_storage_paused(
                    True, 19 * 1024 ** 3, 25 * 1024 ** 3)
                _win._on_analysis_power_policy("battery")
                _win._on_art_progress(1, 20)
                self.assertEqual(_win.status_label.text(), "Verifying")
                self.assertTrue(_win._pulse_timer.isActive())
            _win._set_status_text("Ready")
            self.assertIn("Low disk space", _win.status_label.text())
            self.assertIn("25 GiB", _win.status_label.toolTip())
        finally:
            _win._preparing_library = False
            _win._prep_storage_pause = None
            _win._prep_progress = None
            _win._prep_power_policy = "ac"
            _win._set_status_text("Ready")

    def test_ipod_progress_keeps_animated_status_and_separate_count(self):
        try:
            _win._on_dual_ipod_progress(14, 37, "track.wav")
            self.assertEqual(_win.status_label.text(), "Syncing audio")
            self.assertEqual(_win.status_count_label.text(), "14/37")
            self.assertFalse(_win.status_count_label.isHidden())
            self.assertTrue(_win._pulse_timer.isActive())
            _win._pulse_tick()
            self.assertEqual(_win.status_label.text(), "Syncing audio.")
            self.assertEqual(_win.status_count_label.text(), "14/37")
        finally:
            _win._stop_pulse()
            _win._set_status_count()
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_ipod_plan_and_commit_share_one_monotonic_bar(self):
        old_preconfirmed = _win._dual_active_preconfirmed
        old_value = _win.progress.value()
        try:
            _win._dual_active_preconfirmed = False
            _win.progress.setRange(0, 1000)
            _win.progress.setValue(0)

            _win._on_dual_ipod_plan_progress(2, 4, "planning")
            planned_value = _win.progress.value()
            self.assertEqual(planned_value, 150)
            self.assertEqual(_win.status_label.text(), "Planning iPod")
            self.assertTrue(_win.status_count_label.isHidden())

            _win._on_dual_ipod_plan_progress(4, 4, "planned")
            self.assertEqual(_win.progress.value(), 300)

            _win._on_dual_ipod_progress(1, 4, "writing")
            self.assertGreater(_win.progress.value(), planned_value)
            self.assertEqual(_win.status_label.text(), "Syncing audio")

            _win._on_dual_ipod_progress(4, 4, "written")
            self.assertEqual(_win.progress.value(), 800)
        finally:
            _win._dual_active_preconfirmed = old_preconfirmed
            _win._stop_pulse()
            _win._set_status_count()
            _win.progress.setValue(old_value)
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_confirmed_batch_progress_gives_each_job_a_complete_slice(self):
        old = (
            _win._dual_active_preconfirmed,
            _win._dual_batch_total_tracks,
            _win._dual_batch_done_tracks,
            _win._dual_active_track_total,
            _win.progress.value(),
        )
        try:
            _win._dual_active_preconfirmed = True
            _win._dual_batch_total_tracks = 10
            _win._dual_batch_done_tracks = 2
            _win._dual_active_track_total = 3
            _win.progress.setValue(0)

            _win._on_dual_ipod_plan_progress(4, 4, "planned")
            self.assertEqual(_win.progress.value(), 250)
            _win._on_dual_ipod_progress(4, 4, "written")
            self.assertEqual(_win.progress.value(), 400)
            self.assertEqual(_win.status_count_label.text(), "5/10")
        finally:
            (
                _win._dual_active_preconfirmed,
                _win._dual_batch_total_tracks,
                _win._dual_batch_done_tracks,
                _win._dual_active_track_total,
                progress_value,
            ) = old
            _win._stop_pulse()
            _win._set_status_count()
            _win.progress.setValue(progress_value)
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_generic_work_reserves_final_twenty_percent(self):
        old_value = _win.progress.value()
        try:
            _win.progress.setValue(0)
            _win._on_progress(0, 4, "planned")
            self.assertEqual(_win.progress.value(), 150)
            _win._on_progress(2, 4, "copying")
            self.assertEqual(_win.progress.value(), 475)
            _win._on_progress(4, 4, "written")
            self.assertEqual(_win.progress.value(), 800)

            # Growing an active export queue must never move the bar backward.
            _win._on_progress(1, 8, "new queued work")
            self.assertEqual(_win.progress.value(), 800)
        finally:
            _win._stop_pulse(force=True)
            _win.cancel_btn.setVisible(False)
            _win.progress.setValue(old_value)
            _win.progress.setVisible(False)
            _win._set_status_text("Ready", force=True)

    def test_success_refreshes_open_ipod_before_sidebar_scan(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {dev: DeviceSessionIdentity(dev, 1)}))
        old_current = _win._current_device_path
        old_profile = _win._device_profiles.get(dev)
        old_reader = _win._read_stock_device_view
        old_refresh = _win._refresh_devices
        old_scan_worker = _win._dev_scan_worker
        old_exporting = _win._exporting
        events = []

        def read(device, profile, *, after_finished=None):
            events.append(("snapshot", device, profile))
            self.assertTrue(_win._exporting)
            self.assertIsNotNone(after_finished)
            after_finished()

        try:
            profile = SimpleNamespace(has_stock_ipod=True)
            _win._current_device_path = dev
            _win._device_profiles[dev] = profile
            _win._read_stock_device_view = read
            _win._refresh_devices = (
                lambda *, force=False: events.append(("sidebar", force)))
            _win._dev_scan_worker = None

            _win._refresh_device_surfaces(
                dev, after_finished=lambda: events.append(("complete",)))

            self.assertEqual(events, [
                ("snapshot", dev, profile),
                ("sidebar", True),
                ("complete",),
            ])
            self.assertFalse(_win._exporting)
        finally:
            _win._current_device_path = old_current
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile
            _win._read_stock_device_view = old_reader
            _win._refresh_devices = old_refresh
            _win._dev_scan_worker = old_scan_worker
            _win._exporting = old_exporting

    def test_device_finalization_waits_for_sidebar_worker(self):
        class FakeSignal:
            def __init__(self):
                self.slots = []

            def connect(self, slot):
                self.slots.append(slot)

            def emit(self):
                for slot in tuple(self.slots):
                    slot()

        class FakeWorker:
            def __init__(self):
                self.running = True
                self.finished = FakeSignal()

            def isRunning(self):
                return self.running

        old_refresh = _win._refresh_devices
        old_worker = _win._dev_scan_worker
        old_exporting = _win._exporting
        worker = FakeWorker()
        completed = []
        try:
            def refresh(*, force=False):
                self.assertTrue(force)
                _win._dev_scan_worker = worker

            _win._refresh_devices = refresh
            _win._finish_device_surface_refresh(
                lambda: completed.append("complete"))

            self.assertTrue(_win._exporting)
            self.assertEqual(completed, [])

            worker.running = False
            for callback in worker._surface_refresh_callbacks:
                callback()

            self.assertFalse(_win._exporting)
            self.assertEqual(completed, ["complete"])
        finally:
            _win._refresh_devices = old_refresh
            _win._dev_scan_worker = old_worker
            _win._exporting = old_exporting

    def test_finalization_suppresses_poll_through_success_dialog(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {'/Volumes/IPOD': DeviceSessionIdentity('/Volumes/IPOD', 1)}))
        old_refresh = _win._refresh_device_surfaces
        old_exporting = _win._exporting
        old_ready = _win._eject_ready_device
        exporting_during_dialog = []

        def refresh(_dev, *, after_finished=None):
            self.assertIsNotNone(after_finished)
            after_finished()

        try:
            _win._refresh_device_surfaces = refresh
            _win._finalize_ipod_operation(
                "/Volumes/IPOD",
                lambda: exporting_during_dialog.append((
                    _win._exporting, _win._eject_ready_device)),
            )
            self.assertEqual(
                exporting_during_dialog, [(False, "/Volumes/IPOD")])
            self.assertEqual(_win.progress.value(), 1000)
            self.assertFalse(_win._exporting)
            self.assertEqual(_win._eject_ready_device, "")
        finally:
            _win._stop_pulse()
            _win._refresh_device_surfaces = old_refresh
            _win._exporting = old_exporting
            _win._eject_ready_device = old_ready

    def test_failed_final_refresh_never_runs_success_callback(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {'/Volumes/IPOD': DeviceSessionIdentity('/Volumes/IPOD', 1)}))
        old_refresh = _win._refresh_device_surfaces
        old_exporting = _win._exporting
        old_ready = _win._eject_ready_device
        callbacks = []

        def refresh(_dev, *, after_finished=None):
            _win._device_finalization_error = RuntimeError("dashboard unreadable")
            self.assertIsNotNone(after_finished)
            after_finished()

        try:
            _win._refresh_device_surfaces = refresh
            with mock.patch("desktop.app.QMessageBox.warning") as warning:
                _win._finalize_ipod_operation(
                    "/Volumes/IPOD", lambda: callbacks.append("success"))
            self.assertEqual(callbacks, [])
            warning.assert_called_once()
            self.assertEqual(_win.status_label.text(), "Final refresh failed")
            self.assertEqual(_win._eject_ready_device, "")
            self.assertFalse(_win._exporting)
        finally:
            _win._stop_pulse(force=True)
            _win._refresh_device_surfaces = old_refresh
            _win._exporting = old_exporting
            _win._eject_ready_device = old_ready
            _win._device_finalization_error = None

    def test_multi_device_export_finalizes_every_target_before_done(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {dev: DeviceSessionIdentity(dev, 1) for dev in ('/Volumes/A', '/Volumes/B')}))
        old_refresh = _win._refresh_device_surfaces
        old_exporting = _win._exporting
        old_ready = _win._eject_ready_device
        events = []

        def refresh(device, *, after_finished=None, surface_progress=900):
            events.append(("refresh", device, surface_progress))
            self.assertIsNotNone(after_finished)
            after_finished()

        try:
            _win._refresh_device_surfaces = refresh
            _win._finalize_device_operations(
                ["/Volumes/A", "/Volumes/B", "/Volumes/A"],
                lambda: events.append(("done", _win.progress.value())),
            )
            self.assertEqual(events, [
                ("refresh", "/Volumes/A", 850),
                ("refresh", "/Volumes/B", 950),
                ("done", 1000),
            ])
            self.assertFalse(_win._exporting)
        finally:
            _win._stop_pulse(force=True)
            _win._refresh_device_surfaces = old_refresh
            _win._exporting = old_exporting
            _win._eject_ready_device = old_ready
            _win._device_finalization_error = None

    def test_clean_stock_sync_finalizes_before_quiet_up_to_date_status(self):
        class CleanPlan:
            requires_commit = False

            @staticmethod
            def summary():
                return {}

        dev = "/Volumes/IPOD"
        old_finalize = _win._finalize_ipod_operation
        callbacks = []
        try:
            _win._finalize_ipod_operation = (
                lambda device, callback: callbacks.append((device, callback)))
            with (
                mock.patch("soundbox.stock_ipod.StockIpodSyncPlan", CleanPlan),
                mock.patch("desktop.app.QMessageBox.information") as information,
            ):
                _win._on_stock_ipod_plan(dev, CleanPlan())
                information.assert_not_called()
                self.assertEqual([device for device, _cb in callbacks], [dev])
                callbacks[0][1]()
                information.assert_not_called()
                self.assertEqual(_win._last_device_completion['title'],'iTunes is up to date')
                self.assertIn('up to date',_win.status_label.text())
        finally:
            _win._finalize_ipod_operation = old_finalize
            _win._stop_pulse()
            _win.status_label.setText("Ready")

    def test_clean_composite_sync_finalizes_before_quiet_up_to_date_status(self):
        class CleanPlan:
            requires_commit = False

            @staticmethod
            def summary():
                return {}

        dev = "/Volumes/IPOD"
        old_finalize = _win._finalize_ipod_operation
        old_clear = _win.rockbox_media_cache.clear
        old_exporting = _win._exporting
        old_active_device = _win._dual_active_device
        old_active_keys = set(_win._dual_active_playlist_keys)
        old_preconfirmed = _win._dual_active_preconfirmed
        old_confirmed = _win._dual_active_confirmed
        old_action = _win._dual_active_action
        old_track_total = _win._dual_active_track_total
        callbacks = []
        try:
            _win._finalize_ipod_operation = (
                lambda device, callback: callbacks.append((device, callback)))
            _win.rockbox_media_cache.clear = lambda: None
            _win._exporting = True
            _win._dual_active_device = dev
            _win._dual_active_playlist_keys.clear()
            _win._dual_active_preconfirmed = False
            _win._dual_active_confirmed = True
            _win._dual_active_action = "sync"
            _win._dual_sync_queue.pop(dev, None)
            with (
                mock.patch("soundbox.dual_ipod.DualIpodSyncPlan", CleanPlan),
                mock.patch("desktop.app.QMessageBox.information") as information,
            ):
                _win._on_dual_ipod_plan(dev, CleanPlan())
                information.assert_not_called()
                self.assertEqual([device for device, _cb in callbacks], [dev])
                callbacks[0][1]()
                information.assert_not_called()
                self.assertEqual(_win._last_device_completion['title'],'iPod is up to date')
                self.assertIn('up to date',_win.status_label.text())
        finally:
            _win._finalize_ipod_operation = old_finalize
            _win.rockbox_media_cache.clear = old_clear
            _win._exporting = old_exporting
            _win._dual_active_device = old_active_device
            _win._dual_active_playlist_keys.clear()
            _win._dual_active_playlist_keys.update(old_active_keys)
            _win._dual_active_preconfirmed = old_preconfirmed
            _win._dual_active_confirmed = old_confirmed
            _win._dual_active_action = old_action
            _win._dual_active_track_total = old_track_total
            _win._dual_sync_queue.pop(dev, None)
            _win._stop_pulse()
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_mount_notification_retains_retry_when_lane_is_busy(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        stub = SimpleNamespace(
            _device_coordinator=SimpleNamespace(polling_allowed=False))
        stub._refresh_devices = lambda **kw: MainWindow._refresh_devices(stub, **kw)
        MainWindow._on_volume_mount_changed(stub)
        self.assertTrue(stub._device_mount_refresh_pending)

    def test_missed_mount_event_overrides_eject_hold(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        calls = []
        stub = SimpleNamespace(
            _volume_directory_stamp=10, _eject_ready_device="/Volumes/IPOD",
            _refresh_devices=lambda **kw: calls.append(kw))
        with mock.patch("desktop.app.os.stat", return_value=SimpleNamespace(st_mtime_ns=11)):
            MainWindow._poll_devices(stub)
        self.assertEqual(calls, [{"force": True}])
        self.assertTrue(stub._device_mount_refresh_pending)

    def test_mount_scan_bypasses_playback_and_eject_hold(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        started = []
        stub = SimpleNamespace(
            _device_mount_refresh_pending=True,
            _device_coordinator=SimpleNamespace(polling_allowed=True),
            _eject_ready_device="/Volumes/IPOD", _exporting=False, _deleting=False,
            _dev_scan_worker=None, _stock_view_worker=None,
            player_bar=SimpleNamespace(is_playing=lambda: True), settings={},
            device_tree=SimpleNamespace(topLevelItemCount=lambda: 0),
            _stock_view_cache={}, _device_mount_tokens={}, lib=None,
            _current_device_path='', _usb_view_cache={}, _usb_view_requests=set(),
            _sync_playlists=lambda: [], overrides={}, titles={}, stock_ipod_shadow_store=None,
            _supply_device_scan_playlists=lambda worker: None,
            _on_devices_discovered=lambda rows: None,
            _start_device_worker=lambda *args: started.append(args))
        stub._release_eject_ready = lambda: setattr(stub, "_eject_ready_device", "")
        with mock.patch("desktop.app.DeviceScanWorker") as worker:
            MainWindow._refresh_devices(stub)
        self.assertEqual(started, [(worker.return_value, "", "discover")])
        self.assertFalse(stub._device_mount_refresh_pending)
        self.assertEqual(stub._eject_ready_device, "")
        # Ordinary status polling must also work while playback continues.
        stub._dev_scan_worker = None
        started.clear()
        with mock.patch("desktop.app.DeviceScanWorker") as worker:
            MainWindow._refresh_devices(stub)
        self.assertEqual(started, [(worker.return_value, "", "discover")])

    def test_mount_notification_requests_force_scan(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        calls = []
        stub = SimpleNamespace(_refresh_devices=lambda **kw: calls.append(kw))
        MainWindow._on_volume_mount_changed(stub)
        self.assertEqual(calls, [{"force": True}])

    def test_eject_ready_hold_suppresses_periodic_scan(self):
        old_ready = _win._eject_ready_device
        old_refresh = _win._refresh_devices
        calls = []
        try:
            _win._eject_ready_device = "/Volumes/IPOD"
            _win._refresh_devices = lambda **kw: calls.append("scan")
            _win._device_mount_refresh_pending = False
            _win._volume_directory_stamp = os.stat('/Volumes').st_mtime_ns
            _win._poll_devices()
            self.assertEqual(calls, [])

            _win._eject_ready_device = ""
            _win._poll_devices()
            self.assertEqual(calls, ["scan"] if _win.device_tree.topLevelItemCount() else [])
        finally:
            _win._eject_ready_device = old_ready
            _win._refresh_devices = old_refresh

    def test_eject_ready_hold_suppresses_incidental_refresh(self):
        old_ready = _win._eject_ready_device
        old_worker = _win._dev_scan_worker
        try:
            _win._eject_ready_device = "/Volumes/IPOD"
            _win._dev_scan_worker = None
            with mock.patch("desktop.app.DeviceScanWorker") as scan_worker:
                _win._refresh_devices()
            scan_worker.assert_not_called()
            self.assertEqual(_win._eject_ready_device, "/Volumes/IPOD")
            self.assertIsNone(_win._dev_scan_worker)
        finally:
            _win._eject_ready_device = old_ready
            _win._dev_scan_worker = old_worker

    def test_art_worker_retains_slot_until_finished_callback(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        retired = mock.Mock()
        retired.isRunning.return_value = False
        stub = SimpleNamespace(lib=object(), _shutting_down=False,
                               providers=_win.providers,
                               _state_repository=SimpleNamespace(writable=True),
                               _art_worker=retired, _art_restart=False)
        with mock.patch("desktop.app.ArtFillWorker") as factory:
            MainWindow._start_art_fill(stub, restart=True)
        factory.assert_not_called()
        self.assertIs(stub._art_worker, retired)
        self.assertTrue(stub._art_restart)
        retired.stop.assert_called_once()

    def test_stale_art_finished_cannot_delete_current_worker(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        retired, current = mock.Mock(), mock.Mock()
        stub = SimpleNamespace(_art_worker=current, _shutting_down=False)
        MainWindow._on_art_finished(stub, retired)
        retired.deleteLater.assert_called_once()
        current.deleteLater.assert_not_called()
        self.assertIs(stub._art_worker, current)

    def test_art_finished_during_shutdown_cannot_restart_workers(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        worker = mock.Mock()
        stub = SimpleNamespace(_art_worker=worker, _shutting_down=True,
                               _art_restart=True)
        with mock.patch("desktop.app.QTimer.singleShot") as schedule:
            MainWindow._on_art_finished(stub, worker)
            MainWindow._maybe_prepare_stock_artwork(
                SimpleNamespace(lib=object(), _shutting_down=True))
        schedule.assert_not_called()
        self.assertIsNone(stub._art_worker)
        self.assertFalse(stub._art_restart)

    def test_shutdown_keeps_event_loop_alive_until_worker_really_finishes(self):
        from threading import Event
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from desktop.app import MainWindow
        from desktop.workers import OneShotTask
        from soundbox.device_operations import DeviceOperationCoordinator
        entered, release = Event(), Event()
        events = []

        def work():
            entered.set()
            release.wait(2)
            events.append("worker stopped")

        worker = OneShotTask(work)
        def retire_worker():
            # Keep ownership on every assertion/error path; a fixture failure
            # must never abort the unrelated remainder of the test process.
            release.set();worker.wait();worker.deleteLater()
        self.addCleanup(retire_worker)
        worker.start()
        self.assertTrue(entered.wait(1))
        stub = SimpleNamespace(
            _stock_view_pending={}, _dual_sync_queue={},
            _artists_refresh=mock.Mock(),
            _device_coordinator=DeviceOperationCoordinator(),
            _save_columns=lambda: None, _set_status_text=lambda text: None,
            tag_controller=SimpleNamespace(shutdown=lambda: None),
            grid_controller=SimpleNamespace(shutdown=lambda: None),
            usb_setup_controller=SimpleNamespace(stop=lambda: events.append('setup stopped')),
            worker=worker, findChildren=lambda kind: [],
        )
        QTimer.singleShot(0, lambda: events.append("Qt remained responsive"))
        QTimer.singleShot(25, release.set)
        MainWindow.shutdown(stub)
        self.assertFalse(worker.isRunning())
        self.assertEqual(events, ['setup stopped',"Qt remained responsive", "worker stopped"])

    def test_idle_eject_ignores_known_missing_media_without_verification(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {'/Volumes/IPOD': DeviceSessionIdentity('/Volumes/IPOD', 1)}))
        self.enterContext(mock.patch.object(_win, '_eject_owner', None))
        self.enterContext(mock.patch.object(_win, '_eject_session', None))
        old_exporting = _win._exporting
        old_pending = _win._device_coordinator.eject_pending
        try:
            with mock.patch.object(_win, "_begin_macos_eject") as eject, \
                    mock.patch("desktop.app.engine.verify_export", side_effect=AssertionError("eject verified library")):
                _win._eject("/Volumes/IPOD")
                eject.assert_called_once_with("/Volumes/IPOD")
        finally:
            _win._exporting = old_exporting
            _win._device_coordinator.eject_pending = old_pending
            _win._stop_pulse(force=True)

    def test_eject_queues_behind_read_only_poll_without_busy_modal(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {'/Volumes/IPOD': DeviceSessionIdentity('/Volumes/IPOD', 1)}))
        self.enterContext(mock.patch.object(_win, '_eject_owner', None))
        self.enterContext(mock.patch.object(_win, '_eject_session', None))
        class FakeSignal:
            def __init__(self):
                self.slots = []

            def connect(self, slot):
                self.slots.append(slot)

        class FakeScan:
            def __init__(self):
                self.finished = FakeSignal()

            def isRunning(self):
                return True

        old_scan = _win._dev_scan_worker
        old_pending_eject = _win._device_coordinator.eject_pending
        old_verify = _win._eject_verify_worker
        old_exporting = _win._exporting
        old_pulse_word = _win._pulse_word
        try:
            scan = FakeScan()
            _win._dev_scan_worker = scan
            _win._eject_verify_worker = None
            with mock.patch(
                    "desktop.device_browser.QMessageBox.warning") as warning:
                _win._eject("/Volumes/IPOD")
            warning.assert_not_called()
            self.assertIsNone(_win._eject_verify_worker)
            self.assertTrue(_win._exporting)
            self.assertEqual(_win._pulse_word, "Waiting to eject")
            self.assertEqual(len(scan._eject_resume_callbacks), 1)
            self.assertEqual(scan.finished.slots, [])
        finally:
            _win._stop_pulse(force=True)
            _win._dev_scan_worker = old_scan
            _win._device_coordinator.eject_pending = old_pending_eject
            _win._eject_verify_worker = old_verify
            _win._exporting = old_exporting
            _win._pulse_word = old_pulse_word
            _win._update_device_action_state()

    def test_eject_still_refuses_live_composite_writer(self):
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {'/Volumes/IPOD': DeviceSessionIdentity('/Volumes/IPOD', 1)}))
        class FakeWriter:
            def isRunning(self):
                return True

        old_worker = _win._dual_commit_worker
        old_pending = _win._pending_device_operation
        try:
            _win._dual_commit_worker = FakeWriter()
            _win._pending_device_operation = None
            with mock.patch(
                    "desktop.device_browser.QMessageBox.warning") as warning:
                _win._eject("/Volumes/IPOD")
            warning.assert_called_once()
            self.assertIn("operation to finish", warning.call_args.args[2])
        finally:
            _win._dual_commit_worker = old_worker
            _win._pending_device_operation = old_pending

    def test_failed_refresh_keeps_last_verified_ipod_snapshot(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_current = _win._current_device_path
        old_snapshot = _win._stock_snapshot
        old_set_error = _win.stock_ipod_pages.set_error
        old_flash = _win._flash_status
        calls = []
        snapshot = SimpleNamespace(target=Path(dev))
        try:
            _win._current_device_path = dev
            _win._stock_snapshot = snapshot
            _win.stock_ipod_pages.set_error = (
                lambda message, **kwargs: calls.append((message, kwargs)))
            _win._flash_status = lambda message: calls.append(("flash", message))

            _win._on_stock_device_view(dev, RuntimeError("temporary read error"))

            self.assertIs(_win._stock_snapshot, snapshot)
            self.assertEqual(calls[0], (
                "temporary read error", {"preserve_snapshot": True}))
            self.assertEqual(calls[1], (
                "flash", "iPod refresh failed — showing last verified state"))
        finally:
            _win._current_device_path = old_current
            _win._stock_snapshot = old_snapshot
            _win.stock_ipod_pages.set_error = old_set_error
            _win._flash_status = old_flash

    def test_dual_boot_device_click_opens_stock_dashboard_tabs(self):
        from types import SimpleNamespace
        from desktop.trees import DEVPATH_ROLE

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_reader = _win._read_stock_device_view
        old_current = _win._current_device_path
        old_role = _win._current_device_role
        calls = []

        class _Item:
            @staticmethod
            def data(_column, _role):
                return dev if _role==DEVPATH_ROLE else None

        _win._device_profiles[dev] = SimpleNamespace(
            kind="dual_boot", has_stock_ipod=True)
        _win._read_stock_device_view = (
            lambda device, profile: calls.append((device, profile)))
        try:
            _win._on_device_click(_Item(), 0)
            self.assertEqual(_win.device_content_stack.currentIndex(), 1)
            self.assertTrue(all(
                not button.isHidden() for button in _win.device_tab_buttons[:3]))
            self.assertEqual(calls[0][0], dev)
        finally:
            _win._read_stock_device_view = old_reader
            _win._current_device_path = old_current
            _win._current_device_role = old_role
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_device_click_uses_preloaded_dashboard_without_another_read(self):
        from types import SimpleNamespace
        from desktop.trees import DEVPATH_ROLE

        dev = "/Volumes/PRELOADED-IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_cache = _win._stock_view_cache.get(dev)
        old_current = _win._current_device_path
        old_role = _win._current_device_role
        old_apply = _win._on_stock_device_view
        old_read = _win._read_stock_device_view
        applied = []
        reads = []

        class _Item:
            @staticmethod
            def data(_column, _role):
                return dev if _role==DEVPATH_ROLE else None

        cached = (object(), None)
        try:
            _win._device_profiles[dev] = SimpleNamespace(
                kind="dual_boot", has_stock_ipod=True)
            _win._stock_view_cache[dev] = cached
            _win._on_stock_device_view = (
                lambda device, result: applied.append((device, result)))
            _win._read_stock_device_view = (
                lambda *args, **kwargs: reads.append((args, kwargs)))
            _win._on_device_click(_Item(), 0)
            self.assertEqual(applied, [(dev, cached)])
            self.assertEqual(reads, [])
        finally:
            _win._on_stock_device_view = old_apply
            _win._read_stock_device_view = old_read
            _win._current_device_path = old_current
            _win._current_device_role = old_role
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile
            if old_cache is None:
                _win._stock_view_cache.pop(dev, None)
            else:
                _win._stock_view_cache[dev] = old_cache

    def test_background_dashboard_result_is_cached_before_click(self):
        from soundbox.stock_ipod_info import (
            StockIpodPlaylistEntry, StockIpodPlaylistSnapshot,
            StockIpodSnapshot, StockIpodTrackSnapshot,
        )

        dev = "/Volumes/PRELOAD-CACHE"
        old_current = _win._current_device_path
        old_cache = _win._stock_view_cache.get(dev)
        old_warming = set(_win._stock_view_warming)

        tracks = tuple(StockIpodTrackSnapshot(
            track_id=i, dbid=i, title=f"Generated song {i}", artist="Generated artist",
            album="", genre="", filetype="WAV", size=100, length_ms=1000,
            bitrate_kbps=1411, sample_rate=44100, play_count=0, skip_count=0,
            rating=0, last_played=0, last_skipped=0,
            location=f":iPod_Control:Music:F00:{i}.wav", pending_plays=0,
            pending_skips=0) for i in (1, 2))
        playlist = StockIpodPlaylistSnapshot(1, "Generated set", "Playlist", False,
            tuple(StockIpodPlaylistEntry(track.dbid, track.title, track.artist)
                  for track in tracks))
        snapshot = StockIpodSnapshot(
            target=Path(dev), volume_name="PRELOAD-CACHE", volume_uuid="",
            filesystem="FAT32", protocol="USB", device_identifier="generated-disk",
            writable=True, capacity_bytes=1000, free_bytes=800, model="",
            model_source="", family="iPod", generation="Unknown generation",
            identity_key="", database_id=1, library_persistent_id=0,
            database_version=0x19, checksum_scheme=0, database_sha256="generated",
            media_bytes=200, pending_playback_records=0,
            playback_status="No pending playback records", recovery_count=0,
            tracks=tracks, playlists=(playlist,))
        try:
            _win._current_device_path = ""
            _win._stock_view_warming.add(dev)
            _win._start_ipod_loading_status()
            with mock.patch.object(_win, "_refresh_devices") as scan:
                _win._on_stock_device_view(dev, (snapshot, None))
                scan.assert_not_called()
            self.assertEqual(_win._stock_view_cache[dev], (snapshot, None))
            self.assertEqual(len(_win._stock_view_cache[dev][0].tracks), 2)
            self.assertEqual(len(_win._stock_view_cache[dev][0].playlists), 1)
            self.assertNotIn(dev, _win._stock_view_warming)
            self.assertEqual(_win.status_label.text(), "Ready")
        finally:
            _win._current_device_path = old_current
            _win._stock_view_warming = old_warming
            if old_cache is None:
                _win._stock_view_cache.pop(dev, None)
            else:
                _win._stock_view_cache[dev] = old_cache

    def test_new_ipod_is_visible_but_disabled_during_discovery(self):
        from types import SimpleNamespace

        dev = "/Volumes/HIDDEN-UNTIL-READY"
        old_worker = _win._dev_scan_worker
        old_profile = _win._device_profiles.get(dev)
        old_drift = _win._drift_cache.get(dev)
        old_current = _win._current_device_path
        old_status = _win.status_label.text()
        old_pulse_word = _win._pulse_word
        old_warming = set(_win._stock_view_warming)
        old_cache = _win._stock_view_cache.get(dev)
        _win.device_tree.clear()
        profile = SimpleNamespace(has_stock_ipod=True)
        try:
            _win._dev_scan_worker = SimpleNamespace(mount_tokens={dev: 17})
            _win._stock_view_cache.pop(dev, None)
            _win._stock_view_warming.discard(dev)
            _win._on_devices_discovered([
                ("HIDDEN-UNTIL-READY", dev, 1000, profile)])

            self.assertEqual(_win.device_tree.topLevelItemCount(), 1)
            self.assertTrue(_win.device_tree.topLevelItem(0).isDisabled())
            self.assertEqual(_win.device_tree.topLevelItem(0).text(0),
                             "⛃  HIDDEN-UNTIL-READY · Checking…")
            self.assertIn(dev, _win._stock_view_warming)
            self.assertEqual(_win._pulse_word, "Loading iPod")
            self.assertTrue(_win.status_label.text().startswith("Loading iPod"))
        finally:
            _win._stop_pulse()
            _win.status_label.setText(old_status)
            _win._pulse_word = old_pulse_word
            _win._stock_view_warming = old_warming
            _win._dev_scan_worker = old_worker
            _win._current_device_path = old_current
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile
            if old_drift is None:
                _win._drift_cache.pop(dev, None)
            else:
                _win._drift_cache[dev] = old_drift
            if old_cache is None:
                _win._stock_view_cache.pop(dev, None)
            else:
                _win._stock_view_cache[dev] = old_cache
            _win.device_tree.clear()

    def test_ipod_row_is_available_while_dashboard_loads(self):
        from types import SimpleNamespace
        from desktop.devices import DeviceFacts
        from desktop.trees import DEVPATH_ROLE,DEVROLE_ROLE
        from soundbox.device_profile import DeviceProfile

        dev = "/Volumes/PUBLISH-AFTER-PRELOAD"
        old_worker = _win._dev_scan_worker
        old_profiles = dict(_win._device_profiles)
        old_drift = dict(_win._drift_cache)
        old_tokens = dict(_win._device_mount_tokens)
        old_warming = set(_win._stock_view_warming)
        old_cache = dict(_win._stock_view_cache)
        old_current = _win._current_device_path
        old_status = _win.status_label.text()
        profile = DeviceProfile(kind="dual_boot", label="iTunes/Rockbox",
            filesystem="fat32", has_rockbox=True, has_stock_ipod=True,
            rockbox_export=True, stock_preview=True, stock_write=True)
        rows = [("PUBLISH-AFTER-PRELOAD", dev, 1000, 3, profile)]
        _win.device_tree.clear()
        try:
            _win._dev_scan_worker = SimpleNamespace(mount_tokens={dev: 23})
            _win._stock_view_cache.pop(dev, None)
            _win._stock_view_warming = {dev}
            _win._start_ipod_loading_status()
            with (
                mock.patch("desktop.app.device_fs", return_value="fat32"),
                mock.patch("desktop.app.device_facts", return_value=DeviceFacts(
                    capacity_bytes=256_000_000_000)),
            ):
                _win._on_devices_scanned(rows)
                self.assertEqual(_win.device_tree.topLevelItemCount(), 2)

                self.assertNotIn("Loading", _win.device_tree.topLevelItem(0).text(0))
                self.assertFalse(_win.device_tree.topLevelItem(0).isDisabled())
                self.assertEqual(_win.device_tree.topLevelItem(0).data(0,DEVROLE_ROLE),'')
                self.assertEqual(_win.device_tree.topLevelItem(1).data(0,DEVPATH_ROLE),dev)
                self.assertEqual(_win.device_tree.topLevelItem(1).data(0,DEVROLE_ROLE),'ipod_usb')
                # Preload completion clears the bottom loading status.
                _win._stock_view_cache[dev] = (object(), None)
                _win._on_devices_scanned(rows)

            self.assertEqual(_win.device_tree.topLevelItemCount(), 2)
            item = _win.device_tree.topLevelItem(0)
            self.assertEqual(item.data(0, DEVPATH_ROLE), dev)
            self.assertEqual(item.text(0),
                             "PUBLISH-AFTER-PRELOAD · iTunes/Rockbox iPod · 256.0 GB")
            self.assertIn("Sync needed", item.toolTip(0))
            self.assertIn("Capacity: 256.0 GB", item.toolTip(0))
            self.assertIn("1.0 KB free", item.toolTip(0))
            self.assertNotIn(dev, _win._stock_view_warming)
            self.assertEqual(_win.status_label.text(), "Ready")
            self.assertIsNone(_win._pulse_word)
        finally:
            _win._stop_pulse()
            _win.status_label.setText(old_status)
            _win._device_profiles = old_profiles
            _win._drift_cache = old_drift
            _win._device_mount_tokens = old_tokens
            _win._stock_view_warming = old_warming
            _win._stock_view_cache = old_cache
            _win._current_device_path = old_current
            _win._dev_scan_worker = old_worker
            _win.device_tree.clear()

    def test_finished_scan_clears_ownership_before_publishing_row(self):
        from types import SimpleNamespace

        dev = "/Volumes/PUBLISH-AFTER-THREAD"
        old_worker = _win._dev_scan_worker
        old_apply = _win._on_devices_scanned
        calls = []

        class Worker:
            new_paths = ()
            mount_tokens = {dev: 41}
            results = [("PUBLISH-AFTER-THREAD", dev, 1000, 0,
                        SimpleNamespace(has_stock_ipod=False))]

            @staticmethod
            def deleteLater():
                pass

        worker = Worker()
        try:
            _win._dev_scan_worker = worker

            def apply(results, *, mount_tokens=None):
                calls.append((results, mount_tokens, _win._dev_scan_worker))

            _win._on_devices_scanned = apply
            _win._finish_device_scan_worker(worker)

            self.assertIsNone(_win._dev_scan_worker)
            self.assertEqual(calls[0][0], worker.results)
            self.assertEqual(calls[0][1], worker.mount_tokens)
            self.assertIsNone(calls[0][2])
        finally:
            _win._on_devices_scanned = old_apply
            _win._dev_scan_worker = old_worker

    def test_generic_row_is_disabled_until_scan_thread_finished(self):
        from types import SimpleNamespace

        dev = "/Volumes/GENERIC-WAITS"
        old_worker = _win._dev_scan_worker
        old_profiles = dict(_win._device_profiles)
        old_drift = dict(_win._drift_cache)
        old_tokens = dict(_win._device_mount_tokens)
        old_current = _win._current_device_path
        profile = SimpleNamespace(
            has_stock_ipod=False, kind="generic", label="USB")
        _win.device_tree.clear()
        try:
            _win._dev_scan_worker = SimpleNamespace(mount_tokens={dev: 51})
            _win._on_devices_discovered([
                ("GENERIC-WAITS", dev, 1000, profile)])
            self.assertEqual(_win.device_tree.topLevelItemCount(), 1)

            self.assertTrue(_win.device_tree.topLevelItem(0).isDisabled())
            with (
                mock.patch("desktop.app.device_fs", return_value="fat32"),
                mock.patch.object(_win, "_device_tip", return_value="ready"),
            ):
                _win._dev_scan_worker = None
                _win._on_devices_scanned([
                    ("GENERIC-WAITS", dev, 1000, 0, profile)],
                    mount_tokens={dev: 51},
                )
            self.assertEqual(_win.device_tree.topLevelItemCount(), 1)
        finally:
            _win._dev_scan_worker = old_worker
            _win._device_profiles = old_profiles
            _win._drift_cache = old_drift
            _win._device_mount_tokens = old_tokens
            _win._current_device_path = old_current
            _win.device_tree.clear()

    def test_device_result_callback_runs_after_worker_ownership_clears(self):
        class Worker:
            result = "complete"

            @staticmethod
            def deleteLater():
                pass

        old_worker = _win._resync_worker
        old_exporting = _win._exporting
        calls = []
        worker = Worker()
        try:
            _win._resync_worker = worker
            _win._exporting = False
            _win._finish_device_result_worker(
                "_resync_worker", worker,
                lambda result: calls.append((result, _win._resync_worker)))
            self.assertEqual(calls, [("complete", None)])
        finally:
            _win._resync_worker = old_worker
            _win._exporting = old_exporting
            _win._update_device_action_state()

    def test_library_prep_cannot_flicker_ipod_loading_to_ready(self):
        dev = "/Volumes/STATUS-OWNED-IPOD"
        old_worker = _win._wf_fill_worker
        old_schedule = _win._schedule_waveform_recheck
        old_preparing = _win._preparing_library
        old_warming = set(_win._stock_view_warming)
        old_status = _win.status_label.text()

        class Worker:
            def defer_for_interaction(self, _seconds):
                pass

            @staticmethod
            def deleteLater():
                pass

        worker = Worker()
        try:
            _win._wf_fill_worker = worker
            _win._schedule_waveform_recheck = lambda: None
            _win._preparing_library = True
            _win._stock_view_warming = {dev}
            _win._start_ipod_loading_status()

            _win._on_library_prep_finished(worker)

            self.assertEqual(_win._pulse_word, "Loading iPod")
            self.assertTrue(_win._pulse_timer.isActive())
            self.assertTrue(_win.status_label.text().startswith("Loading iPod"))
        finally:
            _win._stock_view_warming.clear()
            _win._stop_pulse(force=True)
            _win.status_label.setText(old_status)
            _win._stock_view_warming = old_warming
            _win._wf_fill_worker = old_worker
            _win._schedule_waveform_recheck = old_schedule
            _win._preparing_library = old_preparing

    def test_failed_initial_preload_never_publishes_partial_ipod(self):
        dev = "/Volumes/FAILED-PRELOAD"
        old_current = _win._current_device_path
        old_status = _win.status_label.text()
        old_warming = set(_win._stock_view_warming)
        old_cache = dict(_win._stock_view_cache)
        _win.device_tree.clear()
        try:
            _win._current_device_path = ""
            _win._stock_view_cache.pop(dev, None)
            _win._stock_view_warming = {dev}
            _win._start_ipod_loading_status()

            _win._on_stock_device_view(dev, RuntimeError("unreadable"))

            self.assertEqual(_win.device_tree.topLevelItemCount(), 0)
            self.assertNotIn(dev, _win._stock_view_cache)
            self.assertNotIn(dev, _win._stock_view_warming)
            self.assertEqual(_win.status_label.text(),
                             "iPod load failed — retrying")
        finally:
            _win._stop_pulse()
            _win.status_label.setText(old_status)
            _win._stock_view_warming = old_warming
            _win._stock_view_cache = old_cache
            _win._current_device_path = old_current
            _win.device_tree.clear()

    def test_finished_discovery_starts_new_ipod_preload(self):
        from types import SimpleNamespace

        dev = "/Volumes/NEW-IPOD"
        old_worker = _win._dev_scan_worker
        old_profile = _win._device_profiles.get(dev)
        old_cache = _win._stock_view_cache.get(dev)
        old_reader = _win._read_stock_device_view
        old_warming = set(_win._stock_view_warming)
        calls = []

        class Worker:
            new_paths = (dev,)

            @staticmethod
            def deleteLater():
                pass

        worker = Worker()
        profile = SimpleNamespace(has_stock_ipod=True)
        try:
            _win._dev_scan_worker = worker
            _win._device_profiles[dev] = profile
            _win._stock_view_cache.pop(dev, None)
            _win._read_stock_device_view = (
                lambda device, value: calls.append((device, value)))
            _win._finish_device_scan_worker(worker)
            self.assertIsNone(_win._dev_scan_worker)
            self.assertEqual(calls, [(dev, profile)])
            self.assertIn(dev, _win._stock_view_warming)
        finally:
            _win._dev_scan_worker = old_worker
            _win._read_stock_device_view = old_reader
            _win._stock_view_warming = old_warming
            _win._stop_pulse(force=True)
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile
            if old_cache is None:
                _win._stock_view_cache.pop(dev, None)
            else:
                _win._stock_view_cache[dev] = old_cache

    def test_dashboard_read_waits_for_sidebar_reader(self):
        dev = "/Volumes/SERIALIZED-IPOD"
        old_scan = _win._dev_scan_worker
        old_view = _win._stock_view_worker
        old_pending = dict(_win._stock_view_pending)
        old_callbacks = dict(_win._stock_view_callbacks)
        scan = object()
        profile = object()
        try:
            _win._dev_scan_worker = scan
            _win._stock_view_worker = None
            _win._stock_view_pending.clear()
            _win._stock_view_callbacks.clear()
            _win._read_stock_device_view(dev, profile)
            self.assertIsNone(_win._stock_view_worker)
            self.assertIs(_win._stock_view_pending[dev], profile)
        finally:
            _win._dev_scan_worker = old_scan
            _win._stock_view_worker = old_view
            _win._stock_view_pending.clear()
            _win._stock_view_pending.update(old_pending)
            _win._stock_view_callbacks.clear()
            _win._stock_view_callbacks.update(old_callbacks)

    def test_stock_device_pages_render_snapshot(self):
        from soundbox.stock_ipod_format import ARTWORK_PROFILE, DATABASE_PROFILE, StockWriteFormat
        from soundbox.stock_ipod_info import (
            StockIpodPlaylistEntry, StockIpodPlaylistSnapshot,
            StockIpodSnapshot, StockIpodTrackSnapshot,
        )
        track = StockIpodTrackSnapshot(
            1, 2, "More Hype", "GRiZ", "", "", "WAV", 14_515_286,
            82_286, 1411, 44_100, 1, 0, 0, 0, 0,
            ":iPod_Control:Music:F05:SDMM.wav", 0, 0)
        huge_playlist_id = 10_469_178_251_650_046_358
        playlist = StockIpodPlaylistSnapshot(
            huge_playlist_id, "testinggggg", "Playlist", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        second_playlist_id = 8_532_857_218_490_441_969
        second_playlist = StockIpodPlaylistSnapshot(
            second_playlist_id, "House", "Playlist", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        library_root = StockIpodPlaylistSnapshot(
            1, "iPod", "Library", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        smart_playlist = StockIpodPlaylistSnapshot(
            2, "Music", "Smart playlist", True, ())
        apple_regular_alias = StockIpodPlaylistSnapshot(
            4, "Videos", "Playlist", False, ())
        apple_smart_playlist = StockIpodPlaylistSnapshot(
            5, "Videos", "Smart playlist", True, ())
        snapshot = StockIpodSnapshot(
            Path("/Volumes/IPOD"), "IPOD", "UUID", "MS-DOS (FAT32)",
            "USB", "disk4s2", True, 256_000_000_000, 255_000_000_000,
            "MA446", "serial suffix V9M", "iPod Video",
            "5.5th generation", "identity", 4, 5, 0x19, 0, "abc", 14_515_286,
            0, "No pending playback records", 2, (track,),
            (library_root, playlist, second_playlist, smart_playlist,
             apple_regular_alias, apple_smart_playlist),
            serial_number="8M650V0HV9M",
            serial_source="macOS iPod device record",
            firmware_version="1.3",
            firmware_source="macOS iPod device record",
            transport_revision="1.62",
            dac_name="Wolfson WM8758BG",
            dac_source="verified MA446 hardware catalog",
            sync_state="local_changes",
            sync_detail="1 curated cover changed",
            sync_local_changes=1,
            sync_committed_at="2026-08-01T03:55:00-05:00",
            stock_format=StockWriteFormat(DATABASE_PROFILE,ARTWORK_PROFILE,database_version=0x19),
            usb_family='iPod Video',
        )
        _win.stock_ipod_pages.set_snapshot(snapshot)
        pages = _win.stock_ipod_pages
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.music_model.index(0, 1).data(), "GRiZ")
        self.assertEqual(
            [name for name, _getter in pages.music_model.COLUMNS],
            ["Title", "Artist", "Format", "Size", "Time", "Plays", "Skips", "Type"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(0, 0).widget(),
            pages.detail_trees["Identity"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(0, 1).widget(),
            pages.detail_trees["Storage"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(1, 0).widget(),
            pages.detail_trees["Database"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(1, 1).widget(),
            pages.detail_trees["SoundBox"],
        )
        self.assertEqual(
            pages.detail_trees["Identity"].topLevelItem(0).text(0), "Name")
        self.assertEqual(
            pages.detail_trees["Storage"].topLevelItem(0).text(0), "Capacity")
        exact_storage = {'Capacity': '256,000,000,000 bytes', 'Used': '1,000,000,000 bytes',
                         'Free': '255,000,000,000 bytes'}
        for title, tree in pages.detail_trees.items():
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                self.assertEqual(item.toolTip(1), exact_storage.get(item.text(0), '')
                                 if title == 'Storage' else '')
        identity = {
            pages.detail_trees["Identity"].topLevelItem(index).text(0):
            pages.detail_trees["Identity"].topLevelItem(index).text(1)
            for index in range(pages.detail_trees["Identity"].topLevelItemCount())
        }
        self.assertEqual(identity["Model"], "MA446")
        self.assertEqual(identity["iPod firmware"], "1.3")
        self.assertEqual(identity["DAC / codec"], "Wolfson WM8758BG")
        self.assertEqual(identity["Serial number"], "8M650V0HV9M")
        self.assertNotIn("serial suffix", " ".join(identity.values()).casefold())
        self.assertNotIn("macos ipod device record", " ".join(
            identity.values()).casefold())
        self.assertIn("SCSI transport 1.62", identity["Connection"])
        soundbox = {
            pages.detail_trees["SoundBox"].topLevelItem(index).text(0):
            pages.detail_trees["SoundBox"].topLevelItem(index).text(1)
            for index in range(pages.detail_trees["SoundBox"].topLevelItemCount())
        }
        self.assertEqual(soundbox["Sync state"], "Local changes ready")
        self.assertEqual(soundbox["Sync detail"], "1 curated cover changed")
        self.assertEqual(soundbox["Pending local changes"], "1")
        self.assertEqual(
            soundbox["Last Sync"], "2026-08-01T03:55:00-05:00")
        pages.set_page(2)
        playlist_count = pages.playlist_tree.topLevelItemCount()
        identity_count = pages.detail_trees["Identity"].topLevelItemCount()
        pages.set_loading(preserve_page=True)
        self.assertEqual(pages.stack.currentIndex(), 2)
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.playlist_tree.topLevelItemCount(), playlist_count)
        self.assertEqual(
            pages.detail_trees["Identity"].topLevelItemCount(), identity_count)
        pages.set_error("temporary refresh failure", preserve_snapshot=True)
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.playlist_tree.topLevelItemCount(), playlist_count)
        from desktop.stock_device_view import CompositeDeviceData, StockIpodPages
        composite_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["managed", "rockbox"],
            "playlists": {
                "managed": {
                    "name": "testinggggg",
                    "folder_path": [],
                    "tracks": ["safe", "unsafe"],
                },
                "rockbox": {
                    "name": "Rockbox Only",
                    "folder_path": [],
                    "tracks": ["unsafe"],
                },
            },
            "files": {
                "/iPod_Control/Music/F05/SDMM.wav": {
                    "kind": "shared-stock-media",
                    "owned": True,
                    "dbid": 2,
                    "title": "More Hype",
                    "artist": "GRiZ",
                    "size": 14_515_286,
                },
                "/Music/Fixture/Unsafe/Unsafe.wav": {
                    "kind": "rockbox-media",
                    "owned": True,
                    "title": "Unsafe",
                    "artist": "Fixture",
                    "size": 1_024,
                },
            },
        }
        dual = StockIpodPages()
        dual.set_snapshot(
            snapshot,
            CompositeDeviceData(composite_manifest, {
                "managed": (
                    "/iPod_Control/Music/F05/SDMM.wav",
                    "/Music/Fixture/Unsafe/Unsafe.wav",
                ),
                "rockbox": ("/Music/Fixture/Unsafe/Unsafe.wav",),
            }, True, 9),
        )
        dual_database = {
            dual.detail_trees["Database"].topLevelItem(index).text(0):
            dual.detail_trees["Database"].topLevelItem(index).text(1)
            for index in range(
                dual.detail_trees["Database"].topLevelItemCount())
        }
        self.assertEqual(
            dual.detail_trees["Database"].headerItem().text(0),
            "ITUNES/ROCKBOX",
        )
        self.assertEqual(dual_database["Managed tracks"], "2")
        self.assertEqual(dual_database["iTunes tracks"], "1")
        self.assertEqual(dual_database["Rockbox-only tracks"], "1")
        self.assertEqual(
            dual_database["Rockbox database"],
            "Auto update on · 9 cache files",
        )
        self.assertNotIn("Stock smart playlists", dual_database)
        self.assertEqual(dual.music_model.rowCount(), 2)
        dual_types = {
            dual.music_model.index(row, 7).data()
            for row in range(dual.music_model.rowCount())
        }
        self.assertEqual(dual_types, {"iTunes/Rockbox", "Rockbox"})
        self.assertEqual(
            [dual.playlist_tree.topLevelItem(row).text(2)
             for row in range(dual.playlist_tree.topLevelItemCount())],
            ["iTunes/Rockbox", "Rockbox", "iTunes"],
        )
        self.assertEqual(
            dual.playlist_tree.topLevelItem(0).child(1).text(2), "Rockbox")
        self.assertEqual(
            dual.playlist_tree.topLevelItem(0).data(0, Qt.UserRole),
            "managed:managed",
        )

        pending = StockIpodPages()
        pending.set_snapshot(
            snapshot,
            CompositeDeviceData(
                composite_manifest,
                {"managed": (
                    "/iPod_Control/Music/F05/SDMM.wav",
                    "/Music/Fixture/Unsafe/Unsafe.wav",
                )},
                True,
                9,
                True,
            ),
        )
        pending_database = {
            pending.detail_trees["Database"].topLevelItem(index).text(0):
            pending.detail_trees["Database"].topLevelItem(index).text(1)
            for index in range(
                pending.detail_trees["Database"].topLevelItemCount())
        }
        self.assertEqual(
            pending_database["Rockbox database"],
            "Auto update on · Rockbox restart pending",
        )
        manual = StockIpodPages()
        manual.set_snapshot(
            snapshot, CompositeDeviceData(composite_manifest, {}, False, 9, True))
        manual_database = {
            manual.detail_trees["Database"].topLevelItem(index).text(0):
            manual.detail_trees["Database"].topLevelItem(index).text(1)
            for index in range(manual.detail_trees["Database"].topLevelItemCount())
        }
        self.assertEqual(
            manual_database["Rockbox database"], "Auto update off · user controlled")
        drifted = StockIpodPages()
        drifted.set_snapshot(
            snapshot,
            CompositeDeviceData(
                composite_manifest,
                {"rockbox": ("/Music/Fixture/Unsafe/Unsafe.wav",)},
                True,
                9,
                False,
                frozenset({"managed"}),
            ),
        )
        self.assertEqual(
            drifted.general_tree.topLevelItem(0).text(0), "Name")
        self.assertEqual(
            drifted.playlist_tree.topLevelItem(0).text(2), "iTunes/Rockbox")
        self.assertEqual(
            drifted.playlist_tree.topLevelItem(0).toolTip(2), "Sync needed")
        self.assertEqual(
            drifted.playlist_tree.topLevelItem(0).childCount(), 0)
        dual_calls = []
        dual.delete_playlists_requested.connect(dual_calls.append)
        dual.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        dual.delete_playlist_button.click()
        self.assertEqual(
            dual_calls, [(('managed:managed', 'testinggggg'),)])
        self.assertFalse(
            bool(dual.playlist_tree.topLevelItem(2).flags()
                 & Qt.ItemIsUserCheckable))
        order_calls = []
        dual.playlist_order_requested.connect(order_calls.append)
        dual.playlist_tree.setCurrentItem(
            dual.playlist_tree.topLevelItem(1))
        self.assertTrue(dual._playlist_order_ready())
        dual._drop_playlist_order(("rockbox", "managed"))
        self.assertEqual(
            [dual.playlist_tree.topLevelItem(index).data(0, Qt.UserRole)
             for index in range(2)],
            ["managed:rockbox", "managed:managed"],
        )
        self.assertFalse(dual._playlist_order_ready())
        self.assertEqual(order_calls, [("rockbox", "managed")])
        dual.close()
        self.assertEqual(pages.playlist_tree.columnCount(), 3)
        self.assertEqual(
            [pages.playlist_tree.headerItem().text(index) for index in range(3)],
            ["Playlist / Track", "Tracks", "Type"],
        )
        self.assertEqual(pages.playlist_tree.topLevelItemCount(), 3)
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).text(0),
            "testinggggg")
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).data(0, Qt.UserRole),
            str(huge_playlist_id))
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).child(0).text(0),
            "1. More Hype")
        self.assertEqual(pages.playlist_tree.topLevelItem(2).text(0), "Videos")
        self.assertEqual(
            pages.playlist_tree.topLevelItem(2).text(2), "iTunes")
        standalone = StockIpodPages()
        calls = []
        standalone.delete_playlists_requested.connect(calls.append)
        standalone.set_snapshot(snapshot)
        self.assertFalse(standalone.delete_playlist_button.isEnabled())
        standalone.playlist_tree.setCurrentItem(
            standalone.playlist_tree.topLevelItem(0).child(0))
        self.assertFalse(standalone.delete_playlist_button.isEnabled())
        standalone.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertTrue(standalone.delete_playlist_button.isEnabled())
        self.assertEqual(
            standalone.delete_playlist_button.text(),
            "Delete checked playlist…",
        )
        standalone.playlist_tree.topLevelItem(1).setCheckState(0, Qt.Checked)
        self.assertEqual(
            standalone.delete_playlist_button.text(),
            "Delete 2 checked playlists…",
        )
        standalone.delete_playlist_button.click()
        self.assertEqual(calls, [(
            (str(huge_playlist_id), "testinggggg"),
            (str(second_playlist_id), "House"),
        )])
        standalone.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
        standalone.playlist_tree.topLevelItem(1).setCheckState(0, Qt.Unchecked)
        standalone.playlist_tree.topLevelItem(2).setCheckState(0, Qt.Checked)
        standalone.delete_playlist_button.click()
        self.assertEqual(calls[-1], (("5", "Videos"),))
        standalone.set_page(2)
        standalone.set_loading(preserve_page=True)
        self.assertEqual(standalone.stack.currentIndex(), 2)
        standalone.set_snapshot(snapshot)
        self.assertEqual(standalone.stack.currentIndex(), 2)
        standalone.close()
        pages._music_widths_applied = False
        pages.set_page(1)
        _app.processEvents()
        from PySide6.QtWidgets import QHeaderView
        self.assertEqual(
            pages.music_table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.Stretch,
        )
        self.assertEqual(
            pages.music_table.columnWidth(1), pages.MUSIC_FIXED_COLUMN_WIDTHS[0])
        pages.set_page(0)

    def test_confirmed_model_is_scoped_to_stable_device_identity(self):
        from types import SimpleNamespace
        from unittest import mock
        from soundbox.device_profile import DeviceProfile

        profile = DeviceProfile(
            "stock_ipod", "Stock iPod", "msdos", False, True,
            False, True, False, "", 0x400B739988A917E1)
        old = _win.settings.get("stock_ipod_models")
        try:
            with mock.patch(
                    "desktop.app.device_facts",
                    return_value=SimpleNamespace(volume_uuid="FIXTURE-UUID")):
                key = _win._stock_device_key("/Volumes/IPOD", profile)
                _win._remember_stock_model(key, "MA446LL/A")
                self.assertEqual(
                    _win._stored_stock_model("/Volumes/IPOD", profile), "MA446")
                self.assertIn("FIXTURE-UUID", key)
                self.assertNotIn(str(profile.library_persistent_id), key)
        finally:
            _win.settings["stock_ipod_models"] = old if isinstance(old, dict) else {}

    def test_drop_routes_selected_playlist_to_two_phase_stock_flow(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        calls = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_stock_ipod_sync
        _win._device_profiles[dev] = SimpleNamespace(
            kind="stock_ipod", stock_preview=True, rockbox_export=False)
        _win._begin_stock_ipod_sync = (
            lambda device, playlists: calls.append((device, playlists)))
        try:
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            self.assertEqual(calls[0][0], dev)
            self.assertEqual([p.display_path for p in calls[0][1]],
                             ["CRATES / SET ONE"])
        finally:
            _win._begin_stock_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_drop_routes_selected_playlist_to_composite_dual_flow(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        calls = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        _win._device_profiles[dev] = SimpleNamespace(
            kind="dual_boot", stock_preview=True, rockbox_export=True)
        _win._begin_dual_ipod_sync = (
            lambda device, playlists, **kwargs:
            calls.append((device, playlists, kwargs)))
        _win._confirm_dual_ipod_drop = lambda _device, _playlist: True
        try:
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            self.assertEqual(calls[0][0], dev)
            self.assertEqual([p.display_path for p in calls[0][1]],
                             ["CRATES / SET ONE"])
            self.assertTrue(calls[0][2]["preconfirmed"])
        finally:
            _win._begin_dual_ipod_sync = real
            _win._confirm_dual_ipod_drop = real_confirm
            _win._reset_dual_batch()
            _win._dual_sync_queue.pop(dev, None)
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_additional_dual_drops_queue_while_sync_is_running(self):
        from types import SimpleNamespace

        class RunningWorker:
            def isRunning(self):
                return True

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_worker = _win._dual_plan_worker
        old_device = _win._dual_active_device
        old_keys = set(_win._dual_active_playlist_keys)
        old_queue = dict(_win._dual_sync_queue)
        old_batch_keys = set(_win._dual_batch_keys)
        old_batch_names = list(_win._dual_batch_names)
        old_batch_total = _win._dual_batch_total_tracks
        real_confirm = _win._confirm_dual_ipod_drop
        real = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(
                kind="dual_boot", stock_preview=True, rockbox_export=True)
            _win._dual_plan_worker = RunningWorker()
            _win._dual_active_device = dev
            _win._dual_active_playlist_keys.clear()
            _win._dual_sync_queue.clear()
            _win._dual_batch_keys.clear()
            _win._dual_batch_names.clear()
            _win._dual_batch_total_tracks = 0
            _win._begin_dual_ipod_sync = (
                lambda device, playlists: calls.append((device, playlists)))
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: True

            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])

            self.assertEqual(calls, [])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                ["CRATES / SET ONE"],
            )
        finally:
            _win._begin_dual_ipod_sync = real
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_plan_worker = old_worker
            _win._dual_active_device = old_device
            _win._dual_active_playlist_keys = old_keys
            _win._dual_sync_queue = old_queue
            _win._dual_batch_keys = old_batch_keys
            _win._dual_batch_names = old_batch_names
            _win._dual_batch_total_tracks = old_batch_total
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_drop_queues_while_first_job_waits_for_device_scan(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_pending = _win._pending_device_operation
        old_queue = dict(_win._dual_sync_queue)
        real_confirm = _win._confirm_dual_ipod_drop
        real_begin = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            first = next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == "CRATES / SET ONE")
            _win._dual_batch_keys = {device_manifest.playlist_key(first)}
            _win._dual_batch_names = [first.display_path]
            _win._dual_batch_total_tracks = len(_win.lib.resolve(first))
            _win._pending_device_operation = lambda: None
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: True
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))

            _win._on_device_drop(dev, [("rb", "SET TWO")])

            self.assertEqual(calls, [])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                ["SET TWO"],
            )
            self.assertEqual(_win._dual_batch_total_tracks, 3)
        finally:
            _win._confirm_dual_ipod_drop = real_confirm
            _win._begin_dual_ipod_sync = real_begin
            _win._pending_device_operation = old_pending
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_confirmed_multi_drop_starts_one_combined_generation(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real_begin = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        old_queue = dict(_win._dual_sync_queue)
        calls = []
        confirmations = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            _win._confirm_dual_ipod_drop = (
                lambda _device, playlist:
                confirmations.append(playlist.display_path) or True)
            _win._begin_dual_ipod_sync = (
                lambda device, playlists, **kwargs:
                calls.append((device, playlists, kwargs)))

            _win._on_device_drop(dev, [
                ("rb", "CRATES / SET ONE"),
                ("rb", "SET TWO"),
            ])

            self.assertEqual(
                confirmations, ["CRATES / SET ONE", "SET TWO"])
            self.assertEqual(
                [item.display_path for item in calls[0][1]],
                ["CRATES / SET ONE", "SET TWO"],
            )
            self.assertTrue(calls[0][2]["preconfirmed"])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                [],
            )
            self.assertEqual(_win._dual_batch_total_tracks, 3)
        finally:
            _win._begin_dual_ipod_sync = real_begin
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_declined_dual_drop_never_plans_or_queues(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real_begin = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        old_queue = dict(_win._dual_sync_queue)
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: False
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))

            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])

            self.assertEqual(calls, [])
            self.assertNotIn(dev, _win._dual_sync_queue)
            self.assertEqual(_win._dual_batch_total_tracks, 0)
        finally:
            _win._begin_dual_ipod_sync = real_begin
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_confirmed_dual_job_waits_for_queue_before_completion_dialog(self):
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        old_next = _win._start_next_confirmed_dual_job
        old_clear = _win.rockbox_media_cache.clear
        next_calls = []
        try:
            _win._reset_dual_batch()
            _win._dual_batch_names = ["ONE", "TWO"]
            _win._dual_batch_total_tracks = 5
            _win._dual_batch_done_tracks = 1
            _win._dual_active_preconfirmed = True
            _win._dual_active_track_total = 2
            _win._dual_active_device = dev
            _win._start_next_confirmed_dual_job = next_calls.append
            _win.rockbox_media_cache.clear = lambda: None
            result = SimpleNamespace()
            plan = SimpleNamespace()

            with mock.patch.object(QMessageBox, "information") as information:
                _win._on_dual_ipod_commit(dev, result, plan)

            information.assert_not_called()
            self.assertEqual(next_calls, [dev])
            self.assertEqual(_win._dual_batch_done_tracks, 3)
            self.assertEqual(_win._dual_batch_results, [(plan, result)])
        finally:
            _win._start_next_confirmed_dual_job = old_next
            _win.rockbox_media_cache.clear = old_clear
            _win._dual_sync_queue.pop(dev, None)
            _win._dual_active_device = ""
            _win._dual_active_playlist_keys.clear()
            _win._dual_active_preconfirmed = False
            _win._dual_active_track_total = 0
            _win._reset_dual_batch()

    def test_dual_completion_dialog_waits_for_device_finalization(self):
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {dev: DeviceSessionIdentity(dev, 1)}))
        old_refresh = _win._refresh_device_surfaces
        old_clear = _win.rockbox_media_cache.clear
        old_action = _win._dual_active_action
        callbacks = []

        def refresh(device, *, after_finished=None):
            self.assertEqual(device, dev)
            self.assertIsNotNone(after_finished)
            callbacks.append(after_finished)

        result = SimpleNamespace(
            stock=SimpleNamespace(
                copied_tracks=0,
                rewritten_tracks=0,
                reused_tracks=3,
                recovered_tracks=0,
                recovery_directory=Path("/Volumes/IPOD/.soundbox/recovery"),
            ),
            copied_media=0,
            recovered_media=0,
            copied_artwork=0,
            playlists=1,
            deleted_orphans=0,
            deleted_orphan_bytes=0,
            cleanup_failures=(),
        )
        plan = SimpleNamespace(
            playlist_additions=(), playlists=(), generation=50)
        try:
            _win._refresh_device_surfaces = refresh
            _win.rockbox_media_cache.clear = lambda: None
            _win._dual_active_action = "sync"
            _win._dual_sync_queue.pop(dev, None)

            with mock.patch.object(QMessageBox, "information") as information:
                _win._on_dual_ipod_commit(dev, result, plan)
                information.assert_not_called()
                self.assertTrue(_win._exporting)
                self.assertEqual(len(callbacks), 1)

                callbacks[0]()

                information.assert_not_called()
                self.assertIn(
                    "Eject is ready",
                    _win._last_device_completion['text'],
                )
                self.assertFalse(_win._exporting)
        finally:
            _win._stop_pulse()
            _win._refresh_device_surfaces = old_refresh
            _win.rockbox_media_cache.clear = old_clear
            _win._dual_active_action = old_action
            _win._dual_active_device = ""
            _win._dual_active_playlist_keys.clear()
            _win._dual_sync_queue.pop(dev, None)

    def test_next_confirmed_dual_job_coalesces_every_waiting_playlist(self):
        from unittest import mock
        from PySide6.QtCore import QTimer

        dev = "/Volumes/IPOD"
        playlists = [
            next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == display_path)
            for display_path in ("CRATES / SET ONE", "SET TWO")
        ]
        old_begin = _win._begin_dual_ipod_sync
        old_plan_worker = _win._dual_plan_worker
        old_commit_worker = _win._dual_commit_worker
        calls = []
        try:
            _win._dual_plan_worker = None
            _win._dual_commit_worker = None
            _win._dual_sync_queue[dev] = list(playlists)
            _win._begin_dual_ipod_sync = (
                lambda device, selected, **kwargs:
                calls.append((device, list(selected), kwargs)))

            def immediately(delay, context, callback):
                self.assertEqual(delay, 0)
                self.assertIs(context, _win)
                callback()

            with mock.patch.object(
                    QTimer, "singleShot",
                    side_effect=immediately):
                _win._start_next_confirmed_dual_job(dev)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], dev)
            self.assertEqual(
                [playlist.display_path for playlist in calls[0][1]],
                ["CRATES / SET ONE", "SET TWO"],
            )
            self.assertTrue(calls[0][2]["preconfirmed"])
            self.assertEqual(_win._dual_sync_queue[dev], [])
        finally:
            _win._begin_dual_ipod_sync = old_begin
            _win._dual_plan_worker = old_plan_worker
            _win._dual_commit_worker = old_commit_worker
            _win._dual_sync_queue.pop(dev, None)
            _win._reset_dual_batch()

    def test_drained_dual_queue_shows_one_combined_summary(self):
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        self.enterContext(mock.patch.dict(_win._device_coordinator.mounts,
            {dev: DeviceSessionIdentity(dev, 1)}))
        old_refresh = _win._refresh_device_surfaces
        old_flash = _win._flash_status
        refresh_calls = []
        flash_calls = []
        try:
            _win._reset_dual_batch()
            _win._dual_batch_names = ["ONE", "TWO"]
            _win._dual_batch_total_tracks = 5
            _win._dual_batch_done_tracks = 5
            result_one = SimpleNamespace(
                stock=SimpleNamespace(copied_tracks=2, rewritten_tracks=1),
                copied_media=3,
                deleted_orphans=0,
            )
            result_two = SimpleNamespace(
                stock=SimpleNamespace(copied_tracks=0, rewritten_tracks=0),
                copied_media=0,
                deleted_orphans=2,
            )
            _win._dual_batch_results = [
                (SimpleNamespace(), result_one),
                (SimpleNamespace(), result_two),
            ]
            def refresh(device, *, after_finished=None):
                refresh_calls.append(device)
                self.assertIsNotNone(after_finished)
                after_finished()

            _win._refresh_device_surfaces = refresh
            _win._flash_status = flash_calls.append

            with mock.patch.object(QMessageBox, "information") as information:
                _win._start_next_confirmed_dual_job(dev)

            information.assert_not_called()
            detail = _win._last_device_completion['text']
            self.assertIn("2 playlist jobs complete", detail)
            self.assertIn("5 queued track entries", detail)
            self.assertEqual(refresh_calls, [dev])
            self.assertEqual(flash_calls, ["iPod synced"])
            self.assertEqual(_win._dual_batch_total_tracks, 0)
        finally:
            _win._refresh_device_surfaces = old_refresh
            _win._flash_status = old_flash
            _win._dual_sync_queue.pop(dev, None)
            _win._reset_dual_batch()

    def test_verify_reports_integrity_issues_even_without_playlists(self):
        result = {"playlists": 0, "entries": 0, "missing": [],
                  "managed_issues": ["checksum mismatch: /Music/test.wav"]}
        with mock.patch("desktop.app.QMessageBox.warning") as warning, \
                mock.patch("desktop.app.QMessageBox.information") as information:
            _win._on_verify_done("/synthetic/IPOD", result)
        information.assert_not_called()
        self.assertIn("checksum mismatch", warning.call_args.args[2])
        self.assertNotIn("0 of 0", warning.call_args.args[2])

    def test_sync_routes_managed_dual_playlists_to_composite_reconcile(self):
        from types import SimpleNamespace
        from unittest import mock
        from soundbox import manifest as device_manifest

        dev = "/Volumes/IPOD"
        events = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
        _win._begin_dual_ipod_sync = (
            lambda device, playlists, **kwargs:
            events.append(("begin", device, playlists, kwargs)))
        try:
            with (
                mock.patch.object(
                    _win, "_confirm_ipod_sync",
                    side_effect=lambda _dev, **kw: events.append(("confirm",)) or True),
                mock.patch.object(
                    device_manifest,
                    "load_composite",
                    side_effect=AssertionError("GUI read the device manifest")),
            ):
                _win._sync_device(dev)
            self.assertEqual(
                [event[0] for event in events],
                ["confirm", "begin"],
            )
            begin = events[1]
            self.assertEqual(begin[1], dev)
            self.assertIsNone(begin[2])  # worker resolves managed playlists
            self.assertTrue(begin[3]["reconcile"])
            self.assertTrue(begin[3]["confirmed"])
            self.assertEqual(begin[3]["action"], "sync")
            self.assertFalse(begin[3]["audit_existing"])
            events.clear()
            with (
                mock.patch.object(_win, "_confirm_ipod_sync", return_value=True) as confirm,
                mock.patch.object(device_manifest, "load_composite",
                                  side_effect=AssertionError("GUI read the device manifest")),
            ):
                _win._sync_device(dev, audit_existing=True)
            confirm.assert_called_once_with(dev, audit_existing=True)
            self.assertTrue(events[0][3]["audit_existing"])
        finally:
            _win._begin_dual_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_device_playlist_order_routes_to_ordered_composite_reconcile(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        playlists = [
            next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == display_path)
            for display_path in ("CRATES / SET ONE", "SET TWO")
        ]
        keys = [device_manifest.playlist_key(item) for item in playlists]
        manifest = {
            "playlist_order": list(reversed(keys)),
            "playlists": {key: {} for key in keys},
        }
        old_current = _win._current_device_path
        old_profile = _win._device_profiles.get(dev)
        old_begin = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._current_device_path = dev
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._begin_dual_ipod_sync = (
                lambda device, selected, **kwargs:
                calls.append((device, list(selected), kwargs)))
            with (
                mock.patch.object(
                    _win, "_cached_composite_manifest", return_value=manifest),
                mock.patch.object(
                    QMessageBox, "question", side_effect=AssertionError("Second Apply Order review")) as question,
            ):
                _win._apply_composite_playlist_order(tuple(reversed(keys)))
                question.assert_not_called()

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], dev)
            self.assertEqual(
                [device_manifest.playlist_key(item) for item in calls[0][1]],
                list(reversed(keys)),
            )
            self.assertTrue(calls[0][2]["reconcile"])
            self.assertTrue(calls[0][2]["respect_selected_order"])
            self.assertTrue(calls[0][2]["confirmed"])
            self.assertEqual(calls[0][2]["action"], "order")
            self.assertIs(calls[0][2]["expected_manifest"],manifest)
            calls.clear()
            with mock.patch.object(_win,"_cached_composite_manifest",return_value=manifest), \
                    mock.patch.object(QMessageBox,"question",side_effect=AssertionError("Order review")), \
                    mock.patch.object(QMessageBox,"warning") as warning, \
                    mock.patch.object(_win,"_refresh_device_surfaces") as refresh:
                for invalid in ((keys[0],keys[0]),(keys[0],),(keys[0],'unknown')):
                    with self.subTest(invalid=invalid):
                        warning.reset_mock();refresh.reset_mock()
                        _win._apply_composite_playlist_order(invalid)
                        self.assertEqual(calls,[])
                        warning.assert_called_once();refresh.assert_called_once_with(dev)
                warning.reset_mock();refresh.reset_mock()
                with mock.patch('desktop.app.device_manifest.current_playlists_by_key',return_value={}):
                    _win._apply_composite_playlist_order(tuple(keys))
                self.assertEqual(calls,[])
                warning.assert_called_once();refresh.assert_called_once_with(dev)
        finally:
            _win._current_device_path = old_current
            _win._begin_dual_ipod_sync = old_begin
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_declined_dual_sync_does_not_start_planning(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))
            with (
                mock.patch.object(_win, "_confirm_ipod_sync", return_value=False),
                mock.patch.object(
                    device_manifest, "load_composite") as load_composite,
            ):
                _win._sync_device(dev)
            self.assertEqual(calls, [])
            load_composite.assert_not_called()
        finally:
            _win._begin_dual_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_sync_routes_linked_stock_playlists_to_catalog_reconcile(self):
        from types import SimpleNamespace
        from unittest import mock

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        _win._device_profiles[dev] = SimpleNamespace(kind="stock_ipod")
        try:
            with (
                mock.patch.object(_win, '_stock_has_sync_source', return_value=True),
                mock.patch('desktop.stock_catalog_sync.request_device_sync') as sync,
                mock.patch.object(_win, '_begin_stock_ipod_sync') as full_sync,
                mock.patch.object(_win, "_stock_device_key", return_value="fixture"),
                mock.patch.object(
                    _win.stock_ipod_shadow_store, "load",
                    side_effect=AssertionError("GUI read the shadow")),
            ):
                _win._sync_device(dev)
            sync.assert_called_once_with(_win, dev)
            full_sync.assert_not_called()
        finally:
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_device_scan_surfaces_stock_shadow_changes_as_sync_needed(self):
        from types import SimpleNamespace
        from unittest import mock
        from desktop.workers import DeviceScanWorker
        from soundbox.stock_ipod_info import stock_ipod_device_key
        from soundbox.stock_ipod_shadow import StockIpodSyncStatus

        identity = stock_ipod_device_key("UUID", 123)
        profile = SimpleNamespace(
            kind="stock_ipod",
            model="",
            library_persistent_id=123,
        )
        facts = SimpleNamespace(
            volume_uuid="UUID", hardware_serial="SERIAL")
        worker = DeviceScanWorker(
            _win.lib,
            [],
            artist_overrides={"1": "Edited artist"},
            title_overrides={"1": "Edited title"},
            stock_shadow_store=object(),
            stock_models={identity: "MA446"},
            known_paths={"/Volumes/IPOD"},
        )
        results = []
        worker.done.connect(results.append)
        with (
            mock.patch("desktop.workers.list_devices", return_value=[
                ("IPOD", "/Volumes/IPOD", 1000)]),
            mock.patch("desktop.workers.device_profile", return_value=profile),
            mock.patch("desktop.workers.device_facts", return_value=facts),
            mock.patch(
                "desktop.workers.engine.device_drift",
                return_value=None,
            ) as engine_drift,
            mock.patch(
                "soundbox.stock_ipod.inspect_stock_ipod_sync_status",
                return_value=StockIpodSyncStatus(
                    "local_changes", "playlist changed", 2)),
        ):
            worker.run()
        self.assertEqual(results[0][0][3], 2)
        drift_call = engine_drift.call_args
        self.assertEqual(
            drift_call.kwargs["artist_overrides"], {"1": "Edited artist"})
        self.assertEqual(
            drift_call.kwargs["title_overrides"], {"1": "Edited title"})

    def test_new_device_scan_publishes_pending_then_real_drift(self):
        from types import SimpleNamespace
        from desktop.workers import DeviceScanWorker

        profile = SimpleNamespace(
            kind="dual_boot", model="MA446", has_stock_ipod=True)
        worker = DeviceScanWorker(_win.lib, [], known_paths=set())
        discovered = []
        completed = []
        worker.discovered.connect(discovered.append)
        worker.done.connect(completed.append)
        with (
            mock.patch("desktop.workers.list_devices", return_value=[
                ("IPOD", "/Volumes/IPOD", 1000)]),
            mock.patch("desktop.workers.device_profile", return_value=profile),
            mock.patch("desktop.workers.engine.device_drift",
                       side_effect=lambda *a, **kw: (
                           self.assertTrue(discovered),
                           SimpleNamespace(count=lambda: 2))[1]) as drift,
        ):
            worker.run()
        self.assertEqual(discovered[0], [
            ("IPOD", "/Volumes/IPOD", 1000, profile)])
        self.assertEqual(completed[0][0][3], 2)
        self.assertEqual(worker.new_paths, ("/Volumes/IPOD",))
        drift.assert_called_once()

    def test_same_name_new_mount_token_is_a_new_connection(self):
        from types import SimpleNamespace
        from desktop.workers import DeviceScanWorker

        profile = SimpleNamespace(
            kind="dual_boot", model="MA446", has_stock_ipod=True)
        worker = DeviceScanWorker(
            _win.lib,
            [],
            known_paths={"/Volumes/IPOD"},
            known_mount_tokens={"/Volumes/IPOD": 88},
        )
        with (
            mock.patch("desktop.workers.list_devices", return_value=[
                ("IPOD", "/Volumes/IPOD", 1000)]),
            mock.patch("desktop.workers.os.stat",
                       return_value=SimpleNamespace(st_dev=99)),
            mock.patch("desktop.workers.refresh_device_mount",
                       return_value=True) as refresh_mount,
            mock.patch("desktop.workers.device_profile", return_value=profile),
            mock.patch("desktop.workers.engine.device_drift", return_value=None) as drift,
        ):
            worker.run()
        self.assertEqual(worker.mount_tokens, {"/Volumes/IPOD": 99})
        self.assertEqual(worker.new_paths, ("/Volumes/IPOD",))
        refresh_mount.assert_called_once_with("/Volumes/IPOD")
        drift.assert_called_once()

    def test_failed_initial_sync_check_does_not_publish_clean_status(self):
        from types import SimpleNamespace
        from desktop.workers import DeviceScanWorker

        profile = SimpleNamespace(kind="dual_boot", has_stock_ipod=True, model="MA446")
        worker = DeviceScanWorker(_win.lib, [], known_paths=set())
        with (
            mock.patch("desktop.workers.list_devices", return_value=[
                ("IPOD", "/Volumes/IPOD", 1000)]),
            mock.patch("desktop.workers.device_profile", return_value=profile),
            mock.patch("desktop.workers.engine.device_drift", side_effect=OSError("busy")),
        ):
            worker.run()
        self.assertIsNone(worker.results[0][3])
        self.assertIn("Sync status pending", _win._device_tip('/Volumes/IPOD', 1000, drift=None))

    def test_stock_plan_commits_without_a_second_confirmation(self):
        from desktop.app import MainWindow
        from tests.test_quiet_device_exports import QuietDeviceExports
        fixture=QuietDeviceExports()
        self.addCleanup(lambda:self.assertTrue(fixture.doCleanups()))
        owner,target,stock,_dual,capture=fixture.generated()
        with mock.patch('desktop.app.QMessageBox.question',side_effect=AssertionError('Second export review')):
            MainWindow._on_stock_ipod_plan(owner,str(target),stock,_export_capture=capture)
        capture.require_current.assert_called_once_with(owner,str(target))
        owner._commit_stock_ipod_plan.assert_called_once_with(str(target),stock,_export_capture=capture)

    def test_dual_sync_plan_commits_without_a_second_confirmation(self):
        from desktop.app import MainWindow
        from tests.test_quiet_device_exports import QuietDeviceExports
        fixture=QuietDeviceExports()
        self.addCleanup(lambda:self.assertTrue(fixture.doCleanups()))
        owner,target,_stock,dual,capture=fixture.generated()
        with mock.patch('desktop.app.QMessageBox.question',side_effect=AssertionError('Second export review')):
            MainWindow._on_dual_ipod_plan(owner,str(target),dual,_export_capture=capture)
        capture.require_current.assert_called_once_with(owner,str(target))
        owner._commit_dual_ipod_plan.assert_called_once_with(str(target),dual,_export_capture=capture)

    def test_stock_playlist_delete_prompt_is_explicit_and_defaults_to_cancel(self):
        source = (Path(__file__).parents[1] / "desktop" / "app.py").read_text()
        self.assertIn('"Delete stock iPod playlist?"', source)
        self.assertIn("Only songs unused by every remaining user playlist", source)
        self.assertIn("Deleted device media is not retained in recovery", source)
        device_source = (
            Path(__file__).parents[1] / "desktop" / "stock_device_view.py"
        ).read_text()
        self.assertIn("Delete checked playlists…", device_source)
        self.assertIn("Delete this playlist from iPod…", device_source)


if __name__ == "__main__":
    unittest.main()


class DeletedTracks(unittest.TestCase):
    """SoundBox-only delete (2026-07-02): hides from views + exports, restorable,
    rekordbox never involved."""

    def tearDown(self):
        _win.hidden.clear()
        _win._apply_settings_to_lib()

    def test_delete_hides_everywhere_and_restores(self):
        _win.hidden.update(["1"])
        _win._apply_settings_to_lib()
        self.assertNotIn("1", [t.track_id for t in _win._collection_tracks()])
        pl = _win.lib.playlists[0]  # SET ONE keys ["3","1"]
        self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ["3"])
        _win._refresh_graveyard()
        _win._grave.topLevelItem(0).setSelected(True)
        _win._grave_restore_selected()
        self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ["3", "1"])
        self.assertFalse(_win.hidden)

    def test_copy_filenames_real_on_disk_names(self):
        _win._copy_filenames(["1", "2"])
        from PySide6.QtWidgets import QApplication
        self.assertEqual(QApplication.clipboard().text(), "b.wav\na.wav")

    def test_cmd_r_renames_current_row(self):
        from PySide6.QtWidgets import QAbstractItemView
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        _win.table.setCurrentIndex(_win.model.index(0, 3))  # non-editable cell
        _win._rename_current()  # ⌘R → falls back to the Title editor
        self.assertEqual(_win.table.state(), QAbstractItemView.EditingState)
        self.assertEqual(_win.table.currentIndex().column(), 0)  # Title cell
        _win.table.closePersistentEditor(_win.table.currentIndex())
        _app.processEvents()
        _win.table.setCurrentIndex(_win.model.index(0, 1))  # Artist cell selected
        _win._rename_current()  # ⌘R → edits the ARTIST, not the title
        self.assertEqual(_win.table.state(), QAbstractItemView.EditingState)
        self.assertEqual(_win.table.currentIndex().column(), 1)
        _win.table.closePersistentEditor(_win.table.currentIndex())

    def test_remove_from_custom_keeps_collection(self):
        _win.custom["MIX"] = ["1", "2"]
        _win._remove_from_custom("MIX", ["1"])
        self.assertEqual(_win.custom["MIX"], ["2"])
        # Collection unaffected — track 1 still everywhere else.
        self.assertIn("1", [t.track_id for t in _win._collection_tracks()])
        _win.custom.pop("MIX", None)

    def test_native_removal_never_turns_a_local_filter_into_a_source_save(self):
        pl = _win.lib.playlists[0]
        before = {key:dict(values) for key,values in _win.removals.items()}
        with mock.patch.object(_win, '_native_remove') as native:
            _win._remove_from_rb_playlist(pl.display_path, ['1'])
        native.assert_called_once_with(pl.display_path, ['1'])
        self.assertEqual(_win.removals, before)
        self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ['3','1'])
        self.assertIn('1', [t.track_id for t in _win._collection_tracks()])


class TrackRemovalMenu(unittest.TestCase):
    def _menu(self, ids):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(_win)
        _win._add_track_removal_actions(menu, ids)
        return menu

    def tearDown(self):
        _win.custom.pop("MENU TEST", None)
        _win._refresh_custom()
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()

    def test_rekordbox_playlist_offers_both_scopes_and_routes_them(self):
        from copy import deepcopy
        from PySide6.QtWidgets import QMenu
        from soundbox.rekordbox_playlist_plan import revision
        item = _win._find_tree_item("CRATES / SET ONE")
        _win.tree.setCurrentItem(item)
        _app.processEvents()
        original_library = _win.lib
        library = _make_lib()
        library.source = '/synthetic/master.db'
        library.source_kind = 'rekordbox-db'
        library.source_library_id = 'menu-fixture'
        playlist = library.playlists[0]
        playlist.pid = '1000'
        playlist.keys = ['1']
        library.tracks_by_id['1'].source_uuid = '00000000-0000-4000-8000-000000000001'
        node = dict(ID='1000', UUID='00000000-0000-4000-8000-000000001000',
                    Name='SET ONE', ParentID='root', Seq=1, Attribute=0, SmartList=None,
                    rb_local_deleted=0, rb_local_synced=0, rb_data_status=0,
                    rb_local_data_status=0, usn=None, rb_local_usn=1)
        member = dict(ID='00000000-0000-4000-8000-000000002001',
                      UUID='00000000-0000-4000-8000-000000003001', PlaylistID='1000',
                      ContentID='1', TrackNo=1, rb_local_deleted=0, rb_local_synced=0,
                      rb_data_status=0, rb_local_data_status=0, usn=None, rb_local_usn=2)
        library.source_playlist_catalog = dict(nodes=[node], members=[member],
                                               revision=revision([node], [member]))
        before = deepcopy(library.source_playlist_catalog)
        removals = deepcopy(_win.removals)
        with (
            mock.patch.object(_win, '_native_presentation', return_value=library),
            mock.patch.object(_win, '_native_view_playlist', return_value=playlist),
            mock.patch.object(_win, '_native_request', return_value=True) as playlist_remove,
            mock.patch.object(_win, '_delete_tracks') as collection_remove,
        ):
            selected_rows = _win._native_membership_rows(playlist, [dict(id='1')])
            self.assertEqual(selected_rows[0]['_native_membership']['ID'], member['ID'])
            menu = QMenu(_win)
            try:
                _win._add_track_removal_actions(menu, ['1'], selected_rows=selected_rows)
                actions = menu.actions()
                self.assertEqual([a.text() for a in actions], [
                    'Remove from Playlist', 'Remove from Collection'])
                self.assertTrue(all(action.isEnabled() for action in actions))
                actions[0].trigger()
                actions[1].trigger()
                playlist_remove.assert_called_once()
                args, kwargs = playlist_remove.call_args
                self.assertEqual(args, ('remove',))
                self.assertEqual(kwargs['identity'], '1000')
                self.assertEqual(kwargs['tracks'], [library.tracks_by_id['1']])
                self.assertEqual(kwargs['memberships'], [selected_rows[0]['_native_membership']])
                collection_remove.assert_called_once_with(['1'])
            finally:
                menu.deleteLater()
        self.assertIs(_win.lib, original_library)
        self.assertEqual(library.source_playlist_catalog, before)
        self.assertEqual(_win.removals, removals)

    def test_soundbox_playlist_offers_both_scopes(self):
        from desktop.trees import CUSTOM_ROLE
        _win.custom["MENU TEST"] = ["1", "2"]
        _win._refresh_custom()
        item = next(
            _win.sb_tree.topLevelItem(i)
            for i in range(_win.sb_tree.topLevelItemCount())
            if _win.sb_tree.topLevelItem(i).data(0, CUSTOM_ROLE) == "MENU TEST")
        _win.sb_tree.setCurrentItem(item)
        _app.processEvents()
        self.assertEqual([a.text() for a in self._menu(["1", "2"]).actions()], [
            "Remove 2 tracks from Playlist",
            "Remove 2 tracks from Collection",
        ])

    def test_collection_offers_only_collection_scope(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual([a.text() for a in self._menu(["1"]).actions()], [
            "Remove from Collection"])


class DeckTrackMenu(unittest.TestCase):
    def test_menu_order_and_hamburger_geometry_share_toolbar_spacing(self):
        self.addCleanup(_win.setVisible, _win.isVisible())
        self.addCleanup(_win.body_stack.setCurrentIndex, _win.body_stack.currentIndex())
        _win.show()
        _win.body_stack.setCurrentIndex(0)
        _app.processEvents()
        controls = [*_win.grid_edit_buttons[:1], _win.bpm_value, *_win.grid_edit_buttons[1:],
                    _win.quantize_btn, _win.track_menu_btn, _win.playback_time,
                    _win.info_placeholder, _win.my_tags, _win.settings_btn]
        gaps = [b.x()-(a.x()+a.width()) for a,b in zip(controls,controls[1:])]
        self.assertLessEqual(max(gaps)-min(gaps),1)
        self.assertEqual(_win.track_menu_btn.size(),_win.quantize_btn.size())
        menu = _win._track_menu()
        self.assertEqual([a.text() for a in menu.actions()],
                         ['Change waveform color','Beat Count Display','Export to USB','Analyze Track'])
        self.assertEqual([_win._settings_tabs.tabText(i) for i in range(2)],['General','Analysis'])
        menu.deleteLater()

    def test_beat_mode_is_persistent_and_does_not_seek_or_reload(self):
        original = _win.settings.get('beat_count_mode',0)
        with mock.patch.object(_win.player_bar.player,'setPosition') as seek, \
             mock.patch.object(_win.player_bar,'play_track') as load:
            _win._set_beat_count_mode(2)
            self.assertEqual(_win.settings['beat_count_mode'],2)
            self.assertEqual(_win.player_bar.wave.beat_count_mode,2)
            seek.assert_not_called();load.assert_not_called()
        _win._set_beat_count_mode(original,save=False)


class TrackReanalysisMenu(unittest.TestCase):
    def test_singular_and_plural_actions_route_exact_selection(self):
        from PySide6.QtWidgets import QMenu

        with mock.patch.object(_win, "_reanalyze_tracks") as reanalyze, \
                mock.patch.object(_win, "_analyze_tracks") as analyze:
            one = QMenu(_win)
            _win._add_track_reanalysis_action(one, ["1"])
            self.assertEqual([a.text() for a in one.actions()],["Analyze Track"])
            one.actions()[0].trigger()
            analyze.assert_called_once_with(("1",))
            reanalyze.assert_not_called()

            reanalyze.reset_mock()
            analyze.reset_mock()
            many = QMenu(_win)
            _win._add_track_reanalysis_action(many, ["1", "2"])
            self.assertEqual([a.text() for a in many.actions()],["Analyze 2 Tracks"])
            many.actions()[0].trigger()
            analyze.assert_called_once_with(("1","2"))
            reanalyze.assert_not_called()


class TrackFileMenu(unittest.TestCase):
    def test_only_open_in_finder_remains_and_routes_selection(self):
        from PySide6.QtWidgets import QMenu

        with mock.patch.object(_win, "_open_in_finder") as reveal:
            menu = QMenu(_win)
            _win._add_track_file_actions(menu, ["1", "2"])
            self.assertEqual([action.text() for action in menu.actions()], [
                "Open in Finder",
            ])
            menu.actions()[0].trigger()
            reveal.assert_called_once_with(("1", "2"))

    def test_artist_header_does_not_offer_bulk_file_utilities(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(_win)
        _win._add_track_file_actions(menu, ["1", "2"], reveal=False)
        self.assertEqual(menu.actions(), [])

    def test_open_in_finder_reveals_exact_existing_source_paths(self):
        expected = [
            str(_win.lib.tracks_by_id["1"].path),
            str(_win.lib.tracks_by_id["2"].path),
        ]
        with (
            mock.patch("pathlib.Path.is_file", return_value=True),
            mock.patch.object(
                _win, "_start_finder_reveal",
                return_value=(True, 123),
            ) as start,
        ):
            _win._open_in_finder(["1", "2"])
        start.assert_called_once_with(expected)

    def test_open_in_finder_warns_when_source_is_unavailable(self):
        from PySide6.QtWidgets import QMessageBox

        with (
            mock.patch.object(_win, "_start_finder_reveal") as start,
            mock.patch.object(QMessageBox, "warning") as warning,
        ):
            _win._open_in_finder(["missing-track"])
        start.assert_not_called()
        warning.assert_called_once()


class BackupWorker(unittest.TestCase):
    def _run(self, fn):
        from desktop.workers import OneShotTask
        results = []
        w = OneShotTask(fn)
        w.done.connect(results.append)
        w.start()
        self.assertTrue(w.wait(5000))
        _app.processEvents()
        return results[0]

    def test_result_and_exception_delivery(self):
        self.assertEqual(self._run(lambda: 42), 42)
        err = self._run(lambda: (_ for _ in ()).throw(ValueError("bad backup")))
        self.assertIsInstance(err, ValueError)

    def test_full_zip_roundtrip_through_app_dirs(self):
        # Real worker roundtrip in its own disposable state; restoring the
        # shared window behind its back would correctly invalidate its writers.
        import tempfile
        from pathlib import Path
        from soundbox import backup
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "state"
            data_dir.mkdir()
            (data_dir / "library.json").write_text(
                json.dumps({"custom": {"Fixture": ["1"]}}))
            z = Path(td) / "b.zip"
            n = self._run(lambda: backup.export_backup(data_dir, z))
            self.assertIsInstance(n, int)
            self.assertGreater(n, 0)
            res = self._run(lambda: backup.restore_backup(data_dir, z))
            self.assertNotIsInstance(res, Exception)
            self.assertIn("overrides", res)


class SelectAllShortcut(unittest.TestCase):
    def test_command_a_selects_current_songs_from_sidebar_without_grid_edit(self):
        from desktop.keyboard import Keymap
        router = _win._keyboard_router
        previous = router.keymap
        router.keymap = Keymap({'bindings': {'grid.downbeat': ['A']}})
        _win.search.setText(''); _win._show_collection()
        _win.table.clearSelection()
        try:
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=_win.tree), \
                    mock.patch.object(_win, '_edit_grid') as edit:
                self.assertTrue(_win.eventFilter(_win.tree,
                    QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier)))
                self.assertEqual(len(_win.table.selectionModel().selectedRows()), _win.model.rowCount())
                edit.assert_not_called()
                self.assertTrue(_win.eventFilter(_win.tree,
                    QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier)))
                edit.assert_called_once_with('downbeat')
        finally:
            router.release_all(); router.keymap = previous

    def test_select_all_targets_artists_and_rebound_graveyard_and_leaves_text_native(self):
        from desktop.keyboard import Keymap
        from PySide6.QtWidgets import QLineEdit
        router = _win._keyboard_router; previous = router.keymap
        router.keymap = Keymap({'bindings': {'browser.select_all': ['Ctrl+L']}})
        try:
            _win._show_artists()
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=_win.artists_view), \
                    mock.patch.object(_win.table, 'selectAll') as background:
                self.assertTrue(_win.eventFilter(_win.artists_view,
                    QKeyEvent(QEvent.KeyPress, Qt.Key_L, Qt.ControlModifier)))
                # Collapsed artist headers represent all songs in their groups.
                from desktop.trees import ArtistsTree
                self.assertEqual(set(ArtistsTree._ids(_win.artists_view.selectedItems())),
                    {row['id'] for rows in _win._artists_browser.children.values() for row in rows})
                background.assert_not_called()
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=_win._grave), \
                    mock.patch.object(_win._grave, 'selectAll') as select:
                self.assertTrue(_win.eventFilter(_win._grave,
                    QKeyEvent(QEvent.KeyPress, Qt.Key_L, Qt.ControlModifier)))
                select.assert_called_once()
            editor = QLineEdit('keep native editing')
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=editor), \
                    mock.patch.object(_win, '_select_all_browser_tracks') as select:
                _win.eventFilter(editor, QKeyEvent(QEvent.ShortcutOverride, Qt.Key_A, Qt.ControlModifier))
                _win.eventFilter(editor, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier))
                select.assert_not_called()
            editor.deleteLater()
        finally:
            router.release_all(); router.keymap = previous; _win._show_collection()


class Graveyard(unittest.TestCase):
    def test_playback_keys_and_existing_cues_work_without_capturing_a_queue(self):
        from desktop.keyboard import Keymap
        router = _win._keyboard_router
        previous_map = router.keymap
        previous_order, previous_index = _win._autoplay_order, _win._autoplay_index
        panel = _win.player_bar.cue_panel
        previous_cues, previous_loaded = panel._cues, panel._loaded
        previous_page = panel.pages.currentIndex()
        router.release_all(); router.keymap = Keymap()
        _win._autoplay_order = ['already-existing']; _win._autoplay_index = 0
        panel.set_loaded(True); panel.set_cues([dict(kind='memory',slot=0,position_ms=400)])
        panel.memory_tab.click()
        try:
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=_win._grave), \
                 mock.patch.object(_win, '_toggle_player') as play, \
                 mock.patch.object(_win.player_bar, '_cue_press') as cue, \
                 mock.patch.object(_win.player_bar, '_cue_release') as release, \
                 mock.patch.object(panel, '_press') as marker, \
                 mock.patch.object(panel, '_release') as marker_release:
                self.assertTrue(_win.eventFilter(_win._grave, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier)))
                play.assert_called_once()
                self.assertTrue(_win.eventFilter(_win._grave, QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.NoModifier)))
                cue.assert_called_once()
                self.assertTrue(_win.eventFilter(_win._grave, QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)))
                release.assert_called_once()
                self.assertTrue(_win.eventFilter(_win._grave, QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier)))
                marker.assert_called_once_with('memory', 0)
                self.assertTrue(_win.eventFilter(_win._grave, QKeyEvent(QEvent.KeyRelease, Qt.Key_1, Qt.NoModifier)))
                marker_release.assert_called_once_with('memory', 0)
            self.assertEqual(_win._autoplay_order, ['already-existing'])
            self.assertEqual(_win._autoplay_index, 0)
            self.assertFalse(_win._grave.testAttribute(Qt.WA_InputMethodEnabled))
        finally:
            router.release_all(); router.keymap = previous_map
            panel._cues = previous_cues; panel.set_loaded(previous_loaded)
            panel.pages.setCurrentIndex(previous_page)
            _win._autoplay_order, _win._autoplay_index = previous_order, previous_index

    def test_graveyard_audition_keeps_existing_cues_playable_and_rejects_edits(self):
        pb = _win.player_bar
        panel = pb.cue_panel
        previous = (getattr(_win, '_cue_audition_track', None), panel._cues,
                    panel._loaded, panel._editable, panel._edit_reason)
        _win._cue_audition_track = 'audition'
        try:
            panel.set_loaded(True)
            panel.set_cues([dict(kind='hot',slot=0,position_ms=400)])
            with mock.patch.object(pb, 'current_track', return_value='audition'), \
                 mock.patch.object(_win.tag_controller, 'cue_reason', return_value=''), \
                 mock.patch.object(_win.tag_controller, 'request_cue') as write:
                _win._sync_cue_editing()
                self.assertTrue(panel.hot_slots[0].isEnabled())
                self.assertFalse(panel.hot_slots[1].isEnabled())
                self.assertFalse(_win._edit_cue('hot', 1, 'add'))
                self.assertFalse(_win._edit_cue('hot', 0, 'delete'))
                write.assert_not_called()
        finally:
            (_win._cue_audition_track, panel._cues, loaded, editable, reason) = previous
            panel.set_loaded(loaded); panel.set_editable(editable, reason)

    def test_graveyard_load_targets_its_selection_and_library_rename_stays_disabled(self):
        from desktop.keyboard import Keymap
        router = _win._keyboard_router; previous = router.keymap
        router.keymap = Keymap({'schema':1,'bindings':{'browser.load':['Return']}})
        _win.hidden.add('1'); _win._refresh_graveyard()
        item = _win._grave.topLevelItem(0); _win._grave.setCurrentItem(item)
        try:
            with mock.patch('desktop.app.QApplication.focusWidget', return_value=_win._grave), \
                 mock.patch.object(_win, '_grave_load') as load, \
                 mock.patch.object(_win, '_load_browser_selection') as background_load, \
                 mock.patch.object(_win, '_rename_current') as rename:
                self.assertTrue(_win.eventFilter(_win._grave,QKeyEvent(QEvent.KeyPress,Qt.Key_Return,Qt.NoModifier)))
                load.assert_called_once_with(item); background_load.assert_not_called()
                _win.eventFilter(_win._grave,QKeyEvent(QEvent.KeyPress,Qt.Key_R,Qt.ControlModifier))
                rename.assert_not_called()
        finally:
            router.release_all(); router.keymap = previous

    def tearDown(self):
        _win.hidden.clear()
        _win.hidden_dates.clear()
        _win.removals.clear()
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()

    def test_lists_deletes_and_removals_with_dates(self):
        _win.hidden.add("1"); _win.hidden_dates["1"] = "2026-07-01"
        pl = _win.lib.playlists[0]; pl.pid = "42"
        _win.removals["42"] = {"3": "2026-07-02"}
        try:
            _win._refresh_graveyard()
            rows = {_win._grave.topLevelItem(i).text(0):
                    _win._grave.topLevelItem(i).text(1)
                    for i in range(_win._grave.topLevelItemCount())}
            self.assertEqual(rows["Bravo"], "2026-07-01")
            self.assertEqual(rows["Charlie   (from SET ONE)"], "2026-07-02")
        finally:
            pl.pid = ""

    def test_restore_selected_resurrects(self):
        _win.hidden.add("1"); _win.hidden_dates["1"] = "2026-07-01"
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()
        _win._grave.topLevelItem(0).setSelected(True)
        with mock.patch.object(_win, "_maybe_fill_waveforms") as prepare:
            _win._grave_restore_selected()
        self.assertNotIn("1", _win.hidden)
        self.assertNotIn("1", _win.hidden_dates)
        self.assertEqual(_win._grave.topLevelItemCount(), 0)
        prepare.assert_called_once_with()

    def test_playlist_only_restore_does_not_schedule_analysis(self):
        pl = _win.lib.playlists[0]
        saved_pid = pl.pid
        pl.pid = "42"
        _win.removals["42"] = {"1": "2026-07-01"}
        try:
            _win._refresh_graveyard()
            _win._grave.topLevelItem(0).setSelected(True)
            with mock.patch.object(_win, "_maybe_fill_waveforms") as prepare:
                _win._grave_restore_selected()
            prepare.assert_not_called()
        finally:
            pl.pid = saved_pid

    def test_menu_has_only_selection_scoped_restore(self):
        from PySide6.QtWidgets import QMenu

        _win.hidden.update({"1", "2"})
        _win._refresh_graveyard()
        _win._grave.topLevelItem(0).setSelected(True)
        menu = QMenu(_win)
        _win._add_grave_restore_action(menu)
        self.assertEqual([action.text() for action in menu.actions()], [
            "Restore",
        ])

    def test_select_all_then_restore_uses_exact_selection(self):
        _win.hidden.update({"1", "2"})
        _win.hidden_dates.update({"1": "2026-07-01", "2": "2026-07-02"})
        _win._refresh_graveyard()
        _win._grave.selectAll()
        self.assertEqual(len(_win._grave.selectedItems()), 2)
        with mock.patch.object(_win, "_maybe_fill_waveforms"):
            _win._grave_restore_selected()
        self.assertFalse(_win.hidden)
        self.assertFalse(_win.hidden_dates)

    def test_double_click_does_not_restore(self):
        _win.hidden.add("1")
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()
        item = _win._grave.topLevelItem(0)
        previous = (getattr(_win, '_autoplay_context', None), _win._autoplay_order, _win._autoplay_index,
                    _win._autoplay_random_seen, _win._autoplay_random_queue,
                    getattr(_win, '_cue_audition_track', None),
                    getattr(_win, '_playback_navigation', None))
        _win.__dict__.pop('_playback_navigation', None)
        _win._autoplay_order = ['2', '3']; _win._autoplay_index = 0
        try:
            # A load admission is not a completed waveform/audio handoff. Keep
            # the current queue until the real acceptance callback commits it.
            with mock.patch.object(_win, '_open_track', wraps=_win._open_track) as load, \
                    mock.patch.object(Path, 'is_file', return_value=True), \
                    mock.patch.object(_win, '_analysis_result', return_value=None), \
                    mock.patch.object(_win.player_bar, 'play_track') as prepare, \
                    mock.patch.object(_win, '_highlight_now_playing'):
                _win._grave.itemDoubleClicked.emit(item, 0)
                load.assert_called_once_with(_win.lib.tracks_by_id['1'], autoplay=None,
                    capture_autoplay=False, audition=True)
                prepare.assert_called_once()
                self.assertEqual(_win._autoplay_order, ['2', '3'])
                self.assertEqual(_win._autoplay_index, 0)
                prepare.call_args.kwargs['on_started']()
                self.assertEqual(_win._autoplay_order, [])
                self.assertIsNone(_win._autoplay_index)
                self.assertIsNone(_win._autoplay_context)
                self.assertEqual(_win._cue_audition_track, '1')
            _app.processEvents()
            self.assertIn("1", _win.hidden)
            self.assertIn('1', _win.lib.hidden_ids)
        finally:
            (_win._autoplay_context, _win._autoplay_order, _win._autoplay_index,
             _win._autoplay_random_seen, _win._autoplay_random_queue,
             _win._cue_audition_track, navigation) = previous
            if navigation is None:
                _win.__dict__.pop('_playback_navigation', None)
            else:
                _win._playback_navigation = navigation

    def test_date_stamped_on_delete(self):
        import time as _t
        from PySide6.QtWidgets import QMessageBox
        real = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            _win._delete_tracks(["2"])
        finally:
            QMessageBox.question = real
        stamp = _win.hidden_dates.get("2")
        self.assertTrue(stamp.startswith(_t.strftime("%Y-%m-%d ")))
        self.assertRegex(stamp, r"\d{2}:\d{2}$")  # minute precision

    def test_collection_removal_discards_exact_analysis_immediately(self):
        import tempfile
        from PySide6.QtWidgets import QMessageBox
        from soundbox.track_analysis import cache_path

        with tempfile.TemporaryDirectory() as td:
            saved = _win._track_analysis_dir
            _win._track_analysis_dir = Path(td)
            current = cache_path(td, "2")
            unrelated = cache_path(td, "other")
            current.write_bytes(b"current")
            unrelated.write_bytes(b"keep")
            try:
                with mock.patch(
                        "desktop.app.QMessageBox.question",
                        return_value=QMessageBox.Yes), mock.patch.object(
                            _win.player_bar,
                            "cancel_analysis_cache_writes") as cancel:
                    _win._delete_tracks(["2"])
                self.assertFalse(current.exists())
                self.assertEqual(unrelated.read_bytes(), b"keep")
                cancel.assert_called_once_with({"2"})
            finally:
                _win._track_analysis_dir = saved


class TitleJunkSetting(unittest.TestCase):
    def tearDown(self):
        _win.settings["title_rules_off"] = []
        _win.settings["title_words"] = []
        _win._refresh_title_rules()
        _win._apply_settings_to_lib()

    def test_rules_listed_by_name_with_checkboxes(self):
        from soundbox.metadata import BUILTIN_TITLE_RULES
        _win._refresh_title_rules()
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertEqual(labels, [n for _i, n, _p in BUILTIN_TITLE_RULES])
        self.assertTrue(all(_win._rules.topLevelItem(i).checkState(0) == Qt.Checked
                            for i in range(len(labels))))

    def test_unchecking_a_rule_disables_it(self):
        from soundbox.metadata import tidy_title
        _win._refresh_title_rules()
        it = _win._rules.topLevelItem(0)  # combined mixdown markers
        it.setCheckState(0, Qt.Unchecked)  # fires _on_rule_toggled
        self.assertIn("mixdown", _win.settings["title_rules_off"])
        self.assertEqual(tidy_title("GUTTER FINAL", _win._title_junk()),
                         "GUTTER FINAL")   # rule off → kept
        self.assertEqual(tidy_title("GUTTER v2", _win._title_junk()), "GUTTER")

    def test_add_and_remove_custom_word(self):
        from soundbox.metadata import tidy_title
        _win._word_edit.setText("promo")
        _win._add_drop_word()
        self.assertEqual(_win.settings["title_words"], ["promo"])
        self.assertEqual(tidy_title("BANGER promo", _win._title_junk()), "BANGER")
        _win._remove_drop_word("promo")
        self.assertEqual(_win.settings["title_words"], [])
        self.assertEqual(tidy_title("BANGER promo", _win._title_junk()),
                         "BANGER promo")

    def test_regex_entry_supported_and_validated(self):
        from soundbox.metadata import tidy_title
        # a power user adds a raw regex — it runs like the built-ins
        _win._word_edit.setText(r"\bdubplate\d+\b")
        _win._add_drop_word()
        self.assertIn(r"\bdubplate\d+\b", _win.settings["title_words"])
        self.assertEqual(tidy_title("WHOMP dubplate33", _win._title_junk()), "WHOMP")
        # its list label marks it as regex
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertTrue(any("regex" in x for x in labels))
        # an INVALID regex is rejected loudly (warning), never stored
        from PySide6.QtWidgets import QMessageBox
        real, hits = QMessageBox.warning, []
        QMessageBox.warning = staticmethod(lambda *a, **k: hits.append(1))
        try:
            _win._word_edit.setText("([bad")
            _win._add_drop_word()
        finally:
            QMessageBox.warning = real
        self.assertEqual(hits, [1])
        self.assertNotIn("([bad", _win.settings["title_words"])

    def test_plain_bracket_tag_is_labeled_and_applied_as_exact_drop(self):
        from soundbox.metadata import tidy_title
        _win._word_edit.setText("(Original Mix)")
        _win._add_drop_word()
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertIn('Drop "(Original Mix)"', labels)
        self.assertEqual(len(labels), 5)
        self.assertEqual(tidy_title("LOUD (Original Mix)", _win._title_junk()),
                         "LOUD")
        self.assertEqual(tidy_title("LOUD (Original Mix Extended)",
                                    _win._title_junk()),
                         "LOUD (Original Mix Extended)")


class ColumnPersistence(unittest.TestCase):
    def test_widths_and_visibility_roundtrip(self):
        t = _win.table
        # Free enough width before enlarging BPM; independent drags may not
        # borrow from Title when the row already fills the viewport. The fresh
        # Key width alone is smaller than the requested BPM growth.
        t.setColumnHidden(4, True)
        t.setColumnHidden(5, True)
        t.setColumnWidth(3, 123)
        self.assertEqual(t.columnWidth(3), 123)
        _win._save_columns()
        t.setColumnWidth(3, 60)
        t.setColumnHidden(4, False)
        t.setColumnHidden(5, False)
        _win._restore_columns()
        self.assertEqual(t.columnWidth(3), 123)
        self.assertTrue(t.isColumnHidden(4))
        self.assertTrue(t.isColumnHidden(5))
        # sort indicator is cleared on restore (never launch pre-sorted)
        self.assertEqual(t.horizontalHeader().sortIndicatorSection(), -1)
        t.setColumnHidden(4, False)
        t.setColumnHidden(5, False)
        _win._save_columns()


class BrowserColumnInteraction(unittest.TestCase):
    def setUp(self):
        self.header = _win.table.horizontalHeader()
        self.original = (self.header.saveState(), dict(_win.settings),
                         _win.size(), _win.isVisible(), _win.stack.currentIndex())
        self.addCleanup(self.restore)
        _win.show()
        _win._show_collection()
        _win._apply_default_columns()
        _win.table.horizontalScrollBar().setValue(0)
        _app.processEvents()

    def restore(self):
        state, settings, size, visible, page = self.original
        _win.settings.clear()
        _win.settings.update(settings)
        _win.resize(size)
        self.header.restoreState(state)
        _win._show_collection()
        _win._sync_artist_columns()
        _win.stack.setCurrentIndex(page)
        _win.setVisible(visible)
        _app.processEvents()

    def test_column_drags_are_independent_and_stop_at_scrollbar(self):
        from PySide6.QtTest import QTest
        def widths():
            return [self.header.sectionSize(i) for i in range(self.header.count())]
        def drag(column, delta):
            edge = self.header.sectionViewportPosition(column) + self.header.sectionSize(column) - 1
            start = QPoint(edge, self.header.height() // 2)
            end = start + QPoint(delta, 0)
            QTest.mouseMove(self.header.viewport(), start)
            QTest.mousePress(self.header.viewport(), Qt.LeftButton, Qt.NoModifier, start)
            QTest.mouseMove(self.header.viewport(), end)
            QTest.mouseRelease(self.header.viewport(), Qt.LeftButton, Qt.NoModifier, end)
            _app.processEvents()
        # Create room by narrowing Title, then use some of that room for BPM.
        before = widths()
        drag(0, -80)
        self.assertEqual(widths(), [before[0] - 80, *before[1:]])
        before = widths()
        drag(3, 45)
        expected = before[:]
        expected[3] += 45
        self.assertEqual(widths(), expected)
        # Further growth stops at the edge without taking any other width.
        for column, resize in ((3, self.header.resizeSection),
                               (0, self.header.resizeSection),
                               (6, _win.artists_view.setColumnWidth)):
            before = widths()
            slack = _win.table.viewport().width() - self.header.length()
            resize(column, 5000)
            expected = before[:]
            expected[column] += slack
            self.assertEqual(widths(), expected)
            self.assertEqual(self.header.length(), _win.table.viewport().width())
        self.assertEqual(_win.artists_view.header().length(), self.header.length())
        self.assertEqual(self.header.sortIndicatorSection(), -1)
        for column in (7, 8, 9):
            _win._set_column_hidden(column, False)
            self.assertLessEqual(self.header.length(), _win.table.viewport().width())
        _win.resize(1000, _win.height())
        _app.processEvents()
        self.assertLessEqual(self.header.length(), _win.table.viewport().width())

    def test_older_ten_column_header_keeps_new_metadata_unchecked(self):
        from PySide6.QtWidgets import QTableWidget
        from desktop.model import COLUMNS
        old=QTableWidget(0,10)
        self.addCleanup(old.close)
        for i in (7,8,9):old.setColumnHidden(i,True)
        _win.settings['columns_state']=bytes(old.horizontalHeader().saveState().toBase64()).decode('ascii')
        _win.settings.pop('columns_keys',None)
        _win._restore_columns();_app.processEvents()
        self.assertEqual([i for i in range(len(COLUMNS)) if not _win.table.isColumnHidden(i)],list(range(7)))
        _win._set_column_hidden(10,False);_win._set_column_hidden(10,True)
        self.assertGreater(_win.table.viewport().width(),0)
        self.assertGreater(_win.model.rowCount(),0)
        self.assertEqual(_win.settings['columns_keys'],[key for key,_label,_hidden in COLUMNS])

    def test_all_optional_columns_remain_reachable_and_can_be_hidden_without_restart(self):
        from desktop.model import COLUMNS
        for i in range(len(COLUMNS)):_win.table.setColumnHidden(i,False)
        _win.resize(1000,_win.height());_win._fit_browser_columns(fill=True);_app.processEvents()
        self.assertTrue(_win.table.horizontalScrollBar().isVisible())
        self.assertGreater(_win.table.horizontalScrollBar().maximum(),0)
        self.assertGreater(_win.table.viewport().width(),0)
        _win.table.horizontalScrollBar().setValue(_win.table.horizontalScrollBar().maximum())
        self.assertLess(self.header.sectionViewportPosition(len(COLUMNS)-1),_win.table.viewport().width())
        _win._apply_default_columns();_app.processEvents()
        self.assertFalse(_win.table.horizontalScrollBar().isVisible())
        self.assertGreater(_win.model.rowCount(),0)

    def test_footer_geometry_never_changes_when_progress_count_and_cancel_appear(self):
        from desktop.app import FooterBar
        bar=_win.findChild(FooterBar)
        height=bar.height();viewport=_win.table.viewport().height()
        try:
            _win.progress.setVisible(True);_win.cancel_btn.setVisible(True)
            _win.status_count_label.setText('9999/9999');_win.status_count_label.setVisible(True)
            _app.processEvents()
            self.assertEqual(bar.height(),height)
            self.assertEqual(_win.table.viewport().height(),viewport)
            self.assertGreaterEqual(_win.cancel_btn.y(),0)
            self.assertLessEqual(_win.cancel_btn.y()+_win.cancel_btn.height(),height)
        finally:
            _win.progress.hide();_win.cancel_btn.hide();_win.status_count_label.hide()

    def test_legacy_restore_keeps_manual_widths_and_window_growth_expands_only_title(self):
        from PySide6.QtWidgets import QHeaderView
        self.header.setSectionResizeMode(0, QHeaderView.Stretch)
        _win.settings['columns_state'] = bytes(self.header.saveState().toBase64()).decode('ascii')
        _win._restore_columns()
        self.assertEqual(self.header.sectionResizeMode(0), QHeaderView.Interactive)
        self.header.resizeSection(0, 250)
        self.header.resizeSection(3, 120)
        _win.settings['columns_state'] = bytes(self.header.saveState().toBase64()).decode('ascii')
        self.header.resizeSection(0, 400)
        _win._restore_columns()
        before = [self.header.sectionSize(i) for i in range(self.header.count())]
        _win.resize(_win.width() + 100, _win.height())
        _app.processEvents()
        self.assertEqual(self.header.length(), _win.table.viewport().width())
        self.assertEqual(self.header.sectionSize(3), 120)
        self.assertEqual([self.header.sectionSize(i) for i in range(1, self.header.count())],
                         before[1:])
        grown_title = self.header.sectionSize(0)
        _win.resize(_win.width() - 100, _win.height())
        _app.processEvents()
        self.assertEqual(self.header.sectionSize(0), grown_title - 100)
        _win.artists_view.setColumnWidth(0, 275)
        self.assertEqual(self.header.sectionSize(0), 275)
        _win._show_artists()
        _app.processEvents()
        _win.resize(_win.width() + 80, _win.height())
        _app.processEvents()
        self.assertEqual(self.header.length(), _win.artists_view.viewport().width())
        self.assertEqual(_win.artists_view.columnWidth(0), self.header.sectionSize(0))

    def test_columns_reach_frame_and_overlaid_scrollbar_starts_below_header_at_both_ends(self):
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider
        _win.model.set_rows([{'id': str(i), 'title': f'Track {i}'} for i in range(300)])
        self.header.resizeSection(0, _win.table.width() + 100)
        _app.processEvents()
        self.assertFalse(_win.table.horizontalScrollBar().isVisible())
        self.assertLessEqual(self.header.length(), _win.table.viewport().width())
        self.assertGreater(_win.table.verticalScrollBar().maximum(), 0)
        for view, header in ((_win.table, self.header),
                             (_win.artists_view, _win.artists_view.header())):
            if view is _win.artists_view:
                _win._show_artists()
            else:
                _win._show_collection()
            _app.processEvents()
            bar = view._edge_scrollbar.bar
            self.assertLessEqual(header.length(), view.viewport().width())
            self.assertLessEqual(header.mapTo(view, QPoint(header.length(), 0)).x(),
                                 view.contentsRect().right()+1)
            self.assertEqual(view.viewport().geometry().right(),view.contentsRect().right())
            self.assertEqual(header.geometry().right(),view.contentsRect().right())
            header_bottom = header.mapTo(view, QPoint(0, header.height())).y()
            self.assertEqual(bar.mapTo(view, QPoint(0, 0)).y(), header_bottom)
            for position in (bar.minimum(), bar.maximum(), bar.minimum()):
                bar.setValue(position)
                self.assertEqual(view.verticalScrollBar().value(),position)
                option = QStyleOptionSlider()
                option.initFrom(bar)
                option.orientation = Qt.Vertical
                option.minimum, option.maximum = bar.minimum(), bar.maximum()
                option.sliderPosition, option.sliderValue = position, position
                option.pageStep = bar.pageStep()
                handle = bar.style().subControlRect(
                    QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
                self.assertGreaterEqual(bar.mapTo(view, handle.topLeft()).y(), header_bottom)
        self.assertEqual(_win.table.rowAt(0), 0)

    def test_hidden_browser_refits_when_returning_from_settings(self):
        original = _win.body_stack.currentIndex()
        try:
            for width in (1100, 1600, 1000):
                _win.body_stack.setCurrentIndex(1)
                _win.resize(width, _win.height())
                _app.processEvents()
                _win.body_stack.setCurrentIndex(0)
                _app.processEvents()
                self.assertEqual(self.header.length(), _win.table.viewport().width())
                self.assertFalse(_win.table.horizontalScrollBar().isVisible())
        finally:
            _win.body_stack.setCurrentIndex(original)

    def test_fitted_columns_do_not_flash_horizontal_scrollbar_during_window_resize(self):
        from PySide6.QtCore import QObject
        class Probe(QObject):
            def __init__(self):
                super().__init__()
                self.visibility_events = []
            def eventFilter(self, obj, event):
                if event.type() in (QEvent.Show, QEvent.Hide):
                    self.visibility_events.append(event.type())
                return False
        header = self.header
        fixed = sum(header.sectionSize(i) for i in range(1, header.count()))
        header.resizeSection(0, _win.table.viewport().width() - fixed)
        _app.processEvents()
        bar = _win.table.horizontalScrollBar()
        self.assertFalse(bar.isVisible())
        probe = Probe()
        bar.installEventFilter(probe)
        try:
            width, height = _win.width(), _win.table.viewport().height()
            for delta in (30, 60, 10, 0):
                _win.resize(width + delta, _win.height())
                _app.processEvents()
                self.assertEqual(_win.table.viewport().height(), height)
            self.assertEqual(probe.visibility_events, [])
        finally:
            bar.removeEventFilter(probe)


class ArtistMerges(unittest.TestCase):
    def tearDown(self):
        _win.aliases.clear()
        _win.lib.tracks_by_id["1"].artist = "Zed"   # undo test mutation
        _win._build_artist_registry()

    def test_alias_applies_and_unmerges(self):
        _win.lib.tracks_by_id["1"].artist = "ZED"     # make ZED a tagged artist
        _win.aliases["zedd"] = "ZED"
        _win._build_artist_registry()
        import soundbox.metadata as m
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "ZED")
        _win._remove_alias("zedd")
        self.assertEqual(_win.aliases, {})
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "zedd")

    def test_merge_dialog_collects_accepts_and_unmerges(self):
        from desktop.suggest import MergeDialog
        dlg = MergeDialog([("zedd", "ZED", 0.93), ("Rotten", "Riot Ten", 0.92)],
                          {"old": "TARGET"}, {"zedd": 1, "ZED": 9})
        dlg.list.topLevelItem(1).setCheckState(0, Qt.Unchecked)  # human rejects
        dlg._unmerge(dlg.done_list.topLevelItem(0), "old")
        dlg._apply()
        self.assertEqual(dlg.accepts, [("zedd", "ZED")])
        self.assertEqual(dlg.unmerges, ["old"])

    def test_apply_merges_updates_state_and_registry(self):
        _win.lib.tracks_by_id["1"].artist = "ZED"
        _win.aliases["gone"] = "X"
        _win._apply_merges([("zedd", "ZED")], ["gone"])   # the real app path
        self.assertEqual(_win.aliases, {"zedd": "ZED"})
        import soundbox.metadata as m
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "ZED")


class SuggestQueue(unittest.TestCase):
    def test_dialog_collects_checked_accepts(self):
        import desktop.suggest as sg
        real_run = sg.SuggestWorker.run
        sg.SuggestWorker.run = lambda self: None  # no network in tests
        try:
            dlg = sg.SuggestDialog([("1", "t1"), ("2", "t2")], provider=_win.providers.artists)
            dlg._worker.wait(2000)
            dlg._add_suggestion("1", "t1", "GRiZ", 1.0)
            dlg._add_suggestion("2", "t2", "Wrongy", 0.9)
            # Unchecked by default — accepting right away takes NOTHING.
            dlg._accept_checked()
            self.assertEqual(dlg.accepts, [])
            dlg.list.topLevelItem(0).setCheckState(0, Qt.Checked)
            dlg._accept_checked()
            self.assertEqual(dlg.accepts, [("1", "GRiZ")])
            dlg.shutdown()
        finally:
            sg.SuggestWorker.run = real_run


class TaskOwnership(unittest.TestCase):
    def test_finished_but_undelivered_worker_still_belongs_to_owner(self):
        from desktop.task_owner import TaskOwner
        from desktop.workers import OneShotTask
        owner = TaskOwner()
        worker = OneShotTask(lambda: None)
        retired = []
        owner.adopt(worker, retired.append)
        worker.start()
        self.assertTrue(worker.wait(2000))
        self.assertEqual(owner.pending, 1)
        self.assertEqual(retired, [])
        owner.drain()
        self.assertEqual(owner.pending, 0)
        self.assertEqual(retired, [worker])
        with self.assertRaisesRegex(RuntimeError, "stopping"):
            owner.adopt(OneShotTask(lambda: None))

    def test_dialog_shutdown_dispatches_events_and_rejects_late_results(self):
        from threading import Event
        from PySide6.QtCore import QTimer
        from desktop.artpicker import ArtPickerDialog, _CandidateSearch
        from desktop.suggest import SuggestDialog, SuggestWorker
        for worker_class, make in (
            (_CandidateSearch, lambda: ArtPickerDialog("artist", "title", provider=_win.providers.artwork)),
            (SuggestWorker, lambda: SuggestDialog([("1", "title")], provider=_win.providers.artists)),
        ):
            entered, release = Event(), Event()
            events = []
            def run(worker):
                entered.set()
                release.wait(2)
                events.append("worker stopped")
            with mock.patch.object(worker_class, "run", run):
                dialog = make()
                try:
                    self.assertTrue(entered.wait(1))
                    dialog.reject()
                    old_status = dialog.status.text()
                    if isinstance(dialog, SuggestDialog):
                        dialog._add_suggestion("1", "late", "late", 1.0)
                        dialog._on_progress(1, 1, 1)
                        self.assertEqual(dialog.list.topLevelItemCount(), 0)
                    else:
                        dialog._search_done(0)
                    self.assertEqual(dialog.status.text(), old_status)
                    QTimer.singleShot(0, lambda: events.append("events dispatched"))
                    QTimer.singleShot(25, release.set)
                    dialog.shutdown()
                    self.assertEqual(events, ["events dispatched", "worker stopped"])
                    self.assertEqual(dialog._tasks.pending, 0)
                finally:
                    release.set()
                    dialog.shutdown()

    def test_window_shutdown_drains_child_tasks_before_player_teardown(self):
        from threading import Event
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QWidget
        from desktop.app import MainWindow
        from desktop.task_owner import TaskOwner
        from desktop.workers import OneShotTask
        from soundbox.device_operations import DeviceOperationCoordinator
        parent = QWidget()
        owner = TaskOwner(parent)
        release = Event()
        events = []
        worker = OneShotTask(lambda: release.wait(2))
        owner.adopt(worker, lambda worker: events.append("child retired"))
        worker.start()
        stub = SimpleNamespace(
            _stock_view_pending={}, _dual_sync_queue={},
            _device_coordinator=DeviceOperationCoordinator(),
            _save_columns=lambda: None, _set_status_text=lambda text: None,
            tag_controller=SimpleNamespace(shutdown=lambda: events.append('cues drained')),
            grid_controller=SimpleNamespace(shutdown=lambda: events.append('grids drained')),
            usb_setup_controller=SimpleNamespace(stop=lambda: events.append('setup stopped')),
            _artists_refresh=mock.Mock(),
            findChildren=parent.findChildren,
            player_bar=SimpleNamespace(stop=lambda: None,
                shutdown=lambda: events.append("player closed")),
        )
        try:
            QTimer.singleShot(25, release.set)
            MainWindow.shutdown(stub)
            self.assertTrue(owner.stopping)
            self.assertEqual(owner.pending, 0)
            self.assertEqual(events, ['setup stopped','grids drained', 'cues drained', "child retired", "player closed"])
        finally:
            release.set()
            owner.drain()

    def test_waveform_shutdown_waits_for_retirement_and_closes_admission(self):
        from threading import Event
        from PySide6.QtCore import QTimer
        from desktop.player import PlayerBar, WaveformWorker
        entered, release = Event(), Event()
        def run(worker):
            entered.set()
            release.wait(2)
        pb = PlayerBar()
        try:
            with mock.patch.object(WaveformWorker, "run", run):
                pb._start_waveform("/tmp/synthetic-no-read.wav")
                self.assertTrue(entered.wait(1))
                QTimer.singleShot(25, release.set)
                pb.shutdown_waveforms()
                self.assertEqual(pb._wf_pending, [])
                self.assertIsNone(pb._wf)
                pb._start_waveform("/tmp/must-not-start.wav")
                self.assertIsNone(pb._wf)
                self.assertFalse(pb._analysis_is_current(
                    pb._src_path, pb._analysis_path, pb._analysis_generation))
        finally:
            release.set()
            pb.shutdown()


class PlaybackTime(unittest.TestCase):
    def setUp(self):
        from desktop.player import PlayerBar
        self.pb = PlayerBar()

    def tearDown(self):
        self.pb.shutdown()

    def test_elapsed_total_and_hour_formats_follow_duration_and_seeks(self):
        pb = self.pb
        self.assertEqual(pb.playback_time_text, '0:00/0:00')
        for duration, position, expected in (
                (195_000, 83_000, '1:23/3:15'),
                (3_599_999, 3_598_000, '59:58/59:59'),
                (3_600_000, 83_000, '0:01:23/1:00:00'),
                (3_723_000, 3_601_000, '1:00:01/1:02:03'),
                (363_723_000, 360_001_000, '100:00:01/101:02:03'),
                (195_000, -1, '0:00/3:15'),
                (195_000, 200_000, '3:15/3:15')):
            pb._on_dur(duration)
            pb._on_pos(position)
            self.assertEqual(pb.playback_time_text, expected)
        pb._pos = pb._dur = 0
        pb._emit_playback_time(0)
        self.assertEqual(pb.playback_time_text, '0:00/0:00')

    def test_position_polling_publishes_only_when_the_display_changes(self):
        events = []
        pb = self.pb
        pb.playback_time_changed.connect(events.append)
        pb._on_dur(195_000)
        for position in (83_000, 83_001, 83_250, 83_999):
            pb._on_pos(position)
        self.assertEqual(events, ['0:00/3:15', '1:23/3:15'])
        pb._on_pos(84_000)
        pb._on_pos(13_000)  # A paused cue/seek updates immediately too.
        self.assertEqual(events[-2:], ['1:24/3:15', '0:13/3:15'])

    def test_drag_preview_is_not_overwritten_by_a_late_backend_tick(self):
        pb = self.pb
        pb._on_dur(195_000)
        pb._on_pos(83_000)
        pb._deck_drag_active = True
        pb.wave._drag_origin_ms = 83_000
        pb.wave._drag_preview_ms = 84_000
        pb._on_wave_preview(84_000)
        pb._paint_position(83_500)
        pb._on_dur(196_000)
        self.assertEqual(pb.playback_time_text, '1:24/3:16')
        pb._deck_drag_active = False
        pb.wave._drag_origin_ms = None
        pb.wave._drag_preview_ms = None
        pb._on_pos(84_000)
        self.assertEqual(pb.playback_time_text, '1:24/3:16')

    def test_toolbar_receives_clock_text_and_reserves_space_for_hours(self):
        pb = _win.player_bar
        position, duration = pb._pos, pb._dur
        size = _win.size()
        visible = _win.isVisible()
        try:
            _win.resize(1240, 800)
            _win.show()
            _app.processEvents()
            pb._dur = 3_723_000
            pb._emit_playback_time(3_601_000)
            label = _win.playback_time
            label.parentWidget().layout().activate()
            self.assertEqual(label.text(), '1:00:01/1:02:03')
            self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
            widgets = [*_win.grid_edit_buttons, _win.bpm_value,
                       _win.quantize_btn, label, _win.info_placeholder,
                       _win.my_tags, _win.settings_btn]
            ordered = sorted(widgets, key=lambda widget: widget.geometry().left())
            for left, right in zip(ordered, ordered[1:]):
                self.assertLess(left.geometry().right(), right.geometry().left())
        finally:
            pb._pos, pb._dur = position, duration
            pb._time_signature = (-1, -1)
            pb._emit_playback_time()
            _win.resize(size)
            _win.setVisible(visible)


class PrepEditStatus(unittest.TestCase):
    def setUp(self):
        from desktop.tag_controller import TagEditController
        self.original = (_win._prep_update_message, _win.status_label.text(),
                         _win._preparing_library)
        self.status_patch = mock.patch.object(
            TagEditController, 'footer_status_text', new_callable=mock.PropertyMock)
        self.status = self.status_patch.start()
        self.addCleanup(self.status_patch.stop)
        self.addCleanup(self.restore)
        self.status.return_value = ''
        _win._prep_update_message = ''
        _win._preparing_library = False
        _win._set_status_text('Ready')

    def restore(self):
        message, text, preparing = self.original
        _win._prep_update_message = message
        _win._preparing_library = preparing
        _win._set_status_text(text)

    def test_probe_notifications_stay_quiet_and_verified_edits_finish_done_ready(self):
        label = _win.status_label
        style, font = label.styleSheet(), label.font()
        _win._sync_edit_status()
        self.assertEqual(label.text(), 'Ready')
        for message in ('Updating beatgrid…', 'Updating BPM…', 'Updating hot cue…',
                        'Updating memory cue…', 'Updating My Tags…'):
            self.status.return_value = message
            _win._sync_edit_status()
            self.assertEqual(label.text(), message)
            self.assertEqual(label.styleSheet(), style)
            self.assertEqual(label.font(), font)
            self.status.return_value = ''
            _win._sync_edit_status()  # An idle/probe signal is not save confirmation.
            self.assertEqual(label.text(), message)
            with mock.patch('desktop.app.QTimer.singleShot') as later:
                _win._finish_edit_status()
                self.assertEqual(label.text(), 'Done')
                self.assertEqual(later.call_args.args[0], 2000)
                later.call_args.args[-1]()
            self.assertEqual(label.text(), 'Ready')

    def test_old_done_timeout_cannot_clear_a_new_accepted_edit(self):
        self.status.return_value = 'Updating beatgrid…'
        _win._sync_edit_status()
        self.status.return_value = ''
        with mock.patch('desktop.app.QTimer.singleShot') as later:
            _win._finish_edit_status()
            clear_old = later.call_args.args[-1]
        self.status.return_value = 'Updating hot cue…'
        _win._sync_edit_status()
        clear_old()
        self.assertEqual(_win.status_label.text(), 'Updating hot cue…')

    def test_verified_playlist_completion_clears_active_banner_quietly(self):
        self.status.return_value = 'Updating playlist…'
        _win._sync_edit_status()
        self.status.return_value = ''
        with mock.patch.object(_win, '_flash_status') as flash:
            _win._finish_edit_status(quiet=True)
            flash.assert_not_called()
        self.assertEqual(_win.status_label.text(), 'Ready')
        self.assertEqual(_win._prep_update_message, '')

    def test_verified_quiet_completion_preserves_newer_work_and_other_status(self):
        self.status.return_value = 'Updating playlist…'
        _win._sync_edit_status()
        self.status.return_value = 'Updating hot cue…'
        _win._finish_edit_status(quiet=True)
        self.assertEqual(_win.status_label.text(), 'Updating hot cue…')
        self.status.return_value = ''
        _win._set_status_text('Playback error')
        _win._finish_edit_status(quiet=True)
        self.assertEqual(_win.status_label.text(), 'Playback error')
        self.assertEqual(_win._prep_update_message, '')

    def test_failed_edit_reports_the_specific_operation_without_done(self):
        self.status.return_value = 'Updating BPM…'
        _win._sync_edit_status()
        self.status.return_value = ''
        with mock.patch.object(_win, '_finish_tag_refresh'), \
                mock.patch.object(_win, '_start_rekordbox_refresh'):
            _win._tag_failed('Synthetic failure')
        self.assertEqual(_win.status_label.text(), 'Couldn’t update BPM: Synthetic failure')
        self.assertEqual(_win._prep_update_message, '')


class PlayerHardening(unittest.TestCase):
    def test_playlist_worker_edges_preserve_held_cues_scrub_and_loaded_deck(self):
        import threading
        import time
        from desktop.player import PlayerBar
        from PySide6.QtTest import QTest
        pb=PlayerBar();pb._track_id='1';pb._src_path=str(_win.lib.tracks_by_id['1'].path)
        pb._dur=60000;pb._playback_available=True;pb._native_error=''
        pb.cue_panel.set_loaded(True)
        pb.cue_panel.set_cues([dict(kind=kind,slot=0,position_ms=500) for kind in ('hot','memory')])
        entered=threading.Event();release=threading.Event()
        try:
            with mock.patch.object(_win,'player_bar',pb), \
                    mock.patch.object(_win.grid_controller,'presentation_reasons',return_value=('','')), \
                    mock.patch.object(_win.grid_controller,'view',return_value=([[1,0,120]],120)), \
                    mock.patch.object(_win.tag_controller,'cue_reason',return_value=''), \
                    mock.patch.object(_win,'_cache_library'), \
                    mock.patch.object(pb,'_start_cue_preview'), \
                    mock.patch.object(pb.player,'setSource') as load, \
                    mock.patch.object(pb.player,'setPosition') as seek, \
                    mock.patch.object(pb.player,'set_grid') as grid, \
                    mock.patch.object(pb.player,'pause') as pause, \
                    mock.patch.object(pb.player,'play') as play:
                pb.cue_panel._press('memory',0)
                pb._deck_drag_active=True;pb.wave._drag_origin_ms=1234;pb.wave._drag_preview_ms=1456
                pb._transport_play_active=True
                def work():entered.set();release.wait(5)
                self.assertTrue(_win.tag_controller.run_playlist_job(work,
                    lambda _worker:_win._native_refresh_view({'operation':'append'})))
                self.assertTrue(entered.wait(1))
                self.assertTrue(_win.grid_edit_buttons[0].isEnabled())
                release.set();deadline=time.monotonic()+5
                while _win.tag_controller.busy and time.monotonic()<deadline:QTest.qWait(5)
                self.assertFalse(_win.tag_controller.busy)
                self.assertIn(('memory',0),pb.cue_panel._pressed_controls)
                self.assertTrue(pb._deck_drag_active);self.assertTrue(pb.wave.is_dragging())
                self.assertEqual(pb.wave._drag_preview_ms,1456)
                self.assertTrue(pb._transport_play_active)
                self.assertTrue(_win.grid_edit_buttons[0].isEnabled())
                for call in (load,seek,grid,pause,play):call.assert_not_called()
        finally:
            release.set();pb._deck_drag_active=False;pb.wave._drag_origin_ms=None
            pb.cue_panel._pressed_controls.clear();pb.shutdown();_win._sync_grid()

    def test_marker_hold_blocks_session_cue_keyboard_and_button_for_both_pages(self):
        from desktop.player import PlayerBar
        pb=PlayerBar();pb._src_path='/synthetic/audio';pb._dur=60000
        pb._playback_available=True;pb.cue_panel.set_loaded(True)
        pb.cue_panel.set_cues([dict(kind=kind,slot=0,position_ms=500) for kind in ('hot','memory')])
        try:
            with mock.patch.object(pb,'_start_cue_preview') as preview:
                for kind in ('hot','memory'):
                    pb.cue_panel._press(kind,0)
                    self.assertFalse(pb.cue_btn.isEnabled())
                    preview.reset_mock()
                    pb._cue_press();pb.cue_btn.click();pb._cue_release()
                    preview.assert_not_called()
                    self.assertIn((kind,0),pb.cue_panel._pressed_controls)
                    pb.cue_panel._release(kind,0)
                    self.assertTrue(pb.cue_btn.isEnabled())
        finally:pb.shutdown()

    def test_late_grid_enables_bar_start_and_its_cmd_a_binding(self):
        from desktop.player import PlayerBar
        from desktop.keyboard import Keymap
        from PySide6.QtTest import QTest
        pb=PlayerBar();pb._track_id='1';pb._src_path=str(_win.lib.tracks_by_id['1'].path)
        pb._dur=60000;pb._pending_autoplay=False;pb._playback_available=True
        pb._native_error=''
        pb.cue_panel.set_loaded(True)
        pb.grid_ready.connect(lambda:_win._grid_changed('1'))
        old_map=_win._keyboard_router.keymap
        try:
            with mock.patch.object(_win,'player_bar',pb), \
                    mock.patch.object(_win.grid_controller,'presentation_reasons',side_effect=lambda *_a,**_k:('','') if pb._analysis_parts.get('beats') else ('Waiting for grid','Waiting for grid')), \
                    mock.patch.object(_win.grid_controller,'is_local',return_value=True), \
                    mock.patch.object(_win.grid_controller,'view',return_value=None), \
                    mock.patch.object(_win,'_edit_grid') as edit:
                _win._sync_grid();self.assertFalse(_win.grid_edit_buttons[0].isEnabled())
                pb._on_analysis_manifest(pb._src_path,'',pb._analysis_generation,
                    dict(beats=[[1,100,120],[2,600,120]],cues=[]))
                self.assertTrue(_win.grid_edit_buttons[0].isEnabled())
                _win.grid_edit_buttons[0].click();edit.assert_called_once_with('downbeat')
                edit.reset_mock()
                _win._keyboard_router.keymap=Keymap(dict(schema=1,bindings={'grid.downbeat':['Ctrl+A']}))
                _win.table.setFocus();QTest.keyClick(_win.table,Qt.Key_A,Qt.ControlModifier)
                edit.assert_called_once_with('downbeat')
        finally:
            _win._keyboard_router.release_all();_win._keyboard_router.keymap=old_map
            pb.shutdown();_win._sync_grid()

    def test_grid_capture_uses_visible_paused_cursor_and_fresh_playing_position(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._track_id, pb._src_path, pb._dur = 'synthetic', '/synthetic/audio', 5000
            pb.cue_panel.set_loaded(True)
            pb._native_error = ''
            pb.wave.set_timing(0, 5000)
            with mock.patch.object(pb, 'is_playing', return_value=False), \
                    mock.patch.object(pb, 'capture_cue_position', return_value=33) as capture:
                self.assertEqual(pb.capture_grid_position(), 0)
                capture.assert_not_called()
                pb.wave.set_timing(100.4, 5000)
                self.assertEqual(pb.capture_grid_position(), 100)
            with mock.patch.object(pb, 'is_playing', return_value=True), \
                    mock.patch.object(pb, 'capture_cue_position', return_value=1234) as capture:
                self.assertEqual(pb.capture_grid_position(), 1234)
                capture.assert_called_once()
            pb._pending_autoplay = True
            self.assertIsNone(pb.capture_grid_position())
        finally:
            pb.shutdown()
    def test_quantize_uses_nearest_actual_grid_rows_and_preserves_exact_off_mode(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._dur = 5000
            pb._beat_grid = [100, 767, 1433, 2100, 6000]
            self.assertEqual(pb.quantize_cue_position(1101), 1101)
            pb.quantize_enabled = True
            for position, expected in ((0, 100), (760, 767), (1100, 767),
                                       (1101, 1433), (1433, 1433), (4999, 2100)):
                self.assertEqual(pb.quantize_cue_position(position), expected)
            pb._beat_grid = []
            self.assertEqual(pb.quantize_cue_position(1101), 1101)
            # Edited/current deck rows replace the snapping targets immediately.
            pb._track_id, pb._src_path = 'synthetic', '/synthetic/audio'
            pb.refresh_live_grid('synthetic', '/synthetic/audio', [[1, 500, 120], [2, 1000, 120]], 120)
            self.assertEqual(pb.quantize_cue_position(1101), 1000)
        finally:
            pb.shutdown()

    def test_quantized_session_cue_relocates_once_and_existing_cues_preview_exactly(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        from desktop.player import PlayerBar, SessionCueState
        pb = PlayerBar()
        try:
            pb._src_path, pb._dur, pb._pos = '/synthetic/audio', 5000, 1101
            pb._beat_grid = [100, 767, 1433, 2100]
            pb.quantize_enabled = True
            pb._session_cue_state = SessionCueState.RELOCATION_PENDING
            with mock.patch.object(pb, 'is_playing', return_value=False), \
                    mock.patch.object(pb, '_set_position') as seek, \
                    mock.patch.object(pb, '_start_cue_preview') as preview:
                release=MainWindow._dispatch_keyboard_action(SimpleNamespace(player_bar=pb),'transport.cue')
                self.assertEqual(pb._cue_ms, 1433)
                self.assertEqual(pb._session_cue_state, SessionCueState.SET)
                seek.assert_called_once_with(1433)
                preview.assert_not_called()
                self.assertTrue(pb.cue_btn.property('active'))
                release()
                self.assertFalse(pb.cue_btn.property('active'))
                pb._cue_press()
                preview.assert_called_once_with(1433, ('session', -1))
                pb._marker_cue_press('hot', 0, 1101)
                self.assertEqual(preview.call_args.args, (1101, ('hot', 0)))
                pb._set_session_cue(1101)
                pb._cue_press()
                self.assertEqual(preview.call_args.args, (1101, ('session', -1)))
        finally:
            pb.shutdown()
    def test_steady_transport_updates_do_no_widget_work(self):
        """The 20 Hz position path must not rebuild unchanged button state.

        Rebuilding it anyway allocated a QPixmap and QIcon per cue slot
        twenty times a second and cost eight display frames per second at
        maximum zoom in the real app -- each one a doubled scroll step that
        reads as a shake. Measured, not theoretical: see docs/PLAYER.md.
        """
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb.resize(900, 140)
            calls = []
            real_refresh = pb.cue_panel._refresh
            pb.cue_panel._refresh = lambda: (
                calls.append(1), real_refresh())[1]
            pb._update_transport()
            calls.clear()
            for _ in range(40):
                pb._update_transport()
            self.assertEqual(
                calls, [],
                "steady transport updates rebuilt the cue panel")
            # A real state change must still land.
            pb._src_path = "/tmp/x.wav"
            pb._playback_available = True
            pb._update_transport()
            self.assertEqual(len(calls), 1)
        finally:
            pb.deleteLater()
            _app.processEvents()

    def test_native_rendering_suppresses_wasted_qt_repaints(self):
        """paintEvent early-returns under Metal, so a repaint draws nothing."""
        from desktop.player import WaveformBar
        wave = WaveformBar()
        try:
            painted = []
            wave.update = lambda *a, **k: painted.append(1)
            wave.set_native_rendering(True)
            painted.clear()
            wave.set_timing(1234.0, 200_000)
            self.assertEqual(
                painted, [], "Qt repainted while Metal owned the pixels")
            wave.set_native_rendering(False)
            painted.clear()
            wave.set_timing(2345.0, 200_000)
            self.assertEqual(len(painted), 1)
        finally:
            wave.deleteLater()
            _app.processEvents()

    def test_cue_pages_align_to_waveform_and_deck_buttons_are_circles(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb.resize(900, 140)
            pb.show()
            _app.processEvents()
            wave_top = pb.wave.mapTo(pb, QPoint(0, 0)).y()
            cue_top = pb.cue_panel.hot_slots[0].mapTo(pb, QPoint(0, 0)).y()
            self.assertLessEqual(abs(cue_top - wave_top), 1)
            self.assertFalse(hasattr(pb, "time"))
            self.assertFalse(hasattr(pb.cue_panel, "time"))
            self.assertEqual(pb.cue_btn.width(), pb.cue_btn.height())
            self.assertEqual(pb.play_btn.width(), pb.play_btn.height())
            self.assertEqual(pb.cue_btn.size(), pb.play_btn.size())
            selector_top = pb.beat_selector_btn.mapTo(
                pb, QPoint(0, 0)).y()
            cue_top = pb.cue_btn.mapTo(pb, QPoint(0, 0)).y()
            self.assertGreaterEqual(
                cue_top - (selector_top + pb.beat_selector_btn.height()), 7)
        finally:
            pb.close()
            pb.shutdown_waveforms()

    def test_cue_panel_has_two_eight_slot_pages_and_emits_gate_edges(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb._playback_available = True
        try:
            self.assertEqual(len(pb.cue_panel.hot_slots), 8)
            self.assertEqual(len(pb.cue_panel.memory_slots), 8)
            self.assertEqual(pb.cue_panel.pages.count(), 2)
            cues = [
                {"kind": "hot", "slot": 0, "position_ms": 5_000,
                 "type": "single", "label": "Intro", "color": [255, 59, 125]},
                {"kind": "memory", "slot": 7, "position_ms": 17_145,
                 "type": "loop", "label": "Mix", "color": [0, 0, 0]},
            ]
            hits = []
            presses = []
            releases = []
            pb.cue_panel.cue_requested.connect(hits.append)
            pb.cue_panel.cue_pressed.connect(
                lambda kind, slot, position:
                presses.append((kind, slot, position)))
            pb.cue_panel.cue_released.connect(
                lambda kind, slot: releases.append((kind, slot)))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.cue_panel.set_cues(cues)
            pb.cue_panel.set_loaded(True)
            pb.cue_panel.hot_slots[0].click()
            pb.cue_panel.memory_tab.click()
            pb.cue_panel.memory_slots[7].click()
            self.assertEqual(pb.cue_panel.pages.currentIndex(), 1)
            self.assertEqual(hits, [5_000, 17_145])
            self.assertEqual(
                presses, [("hot", 0, 5_000), ("memory", 7, 17_145)])
            self.assertEqual(releases, [("hot", 0), ("memory", 7)])
            self.assertIn("Intro", pb.cue_panel.hot_slots[0].toolTip())
            self.assertIn("Loop", pb.cue_panel.memory_slots[7].toolTip())
        finally:
            pb.shutdown_waveforms()

    def test_waveform_cues_use_fixed_slot_color_and_red_memory_identity(self):
        from PySide6.QtGui import QImage
        from desktop.player import WaveformBar

        wave = WaveformBar()
        wave.resize(900, 132)
        wave.set_zoom(64.0)
        wave.set_timing(60_000, 120_000)
        imported_hot = [220, 40, 90]
        slot_hot = (255, 55, 111)
        wave.set_analysis({
            "heights": [0.4] * 180,
            "colors": [[7, 1, 1]] * 180,
            "sample_rate": 150.0,
            "beats": [],
            "cues": [
                {"kind": "hot", "slot": 0, "position_ms": 58_000,
                 "color": imported_hot},
                # A stored non-red color must never leak into memory markers.
                {"kind": "memory", "slot": 0, "position_ms": 62_000,
                 "color": [20, 80, 240]},
            ],
        })
        image = QImage(wave.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        wave.render(image)

        overview_hot_x = round(58_000 / 120_000 * 900)
        overview_box_left = round(overview_hot_x - 9)
        self.assertEqual(
            image.pixelColor(overview_box_left, 1).name(), "#050608")
        self.assertEqual(
            image.pixelColor(overview_box_left + 2, 3).getRgb()[:3],
            slot_hot)
        overview_memory_x = round(62_000 / 120_000 * 900)
        self.assertEqual(
            image.pixelColor(overview_memory_x, 25).getRgb()[:3],
            (255, 52, 52))
        # The slightly larger arrow reaches the lane edge, hiding any
        # coincident red grid/stem instead of leaving a one-pixel sliver.
        self.assertIn(
            image.pixelColor(overview_memory_x, 27).name(),
            ("#050608", "#ff3434"))

        detail_hot_x = 900 // 2 + round((58_000 - 60_000) / 1000 * 64)
        self.assertEqual(
            image.pixelColor(detail_hot_x - 9, 52).getRgb()[:3],
            slot_hot)
        self.assertEqual(
            image.pixelColor(detail_hot_x, 49).name(), "#050608")
        detail_memory_x = (
            900 // 2 + round((62_000 - 60_000) / 1000 * 64))
        self.assertEqual(
            image.pixelColor(detail_memory_x, 129).getRgb()[:3],
            (255, 52, 52))
        self.assertIn(
            image.pixelColor(detail_memory_x, 131).name(),
            ("#050608", "#ff3434"))

    def test_session_cue_is_orange_in_overview_and_detail(self):
        from PySide6.QtGui import QImage
        from desktop.player import WaveformBar

        wave = WaveformBar()
        wave.resize(900, 132)
        wave.set_zoom(64.0)
        wave.set_timing(60_000, 120_000)
        wave.set_analysis({
            "heights": [0.4] * 180,
            "colors": [[7, 1, 1]] * 180,
            "sample_rate": 150.0,
            "beats": [],
            "cues": [],
        })
        wave.set_session_cue(58_000)
        image = QImage(wave.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        wave.render(image)

        overview_x = round(58_000 / 120_000 * 900)
        detail_x = 900 // 2 + round((58_000 - 60_000) / 1000 * 64)
        # Session CUE is arrow-only: the waveform body has no orange stem.
        self.assertNotEqual(
            image.pixelColor(overview_x, 10).name(), "#f08a19")
        self.assertEqual(
            image.pixelColor(overview_x, 22).name(), "#f08a19")
        self.assertNotEqual(
            image.pixelColor(detail_x, 80).name(), "#f08a19")
        self.assertEqual(
            image.pixelColor(detail_x, 125).name(), "#f08a19")
        self.assertIn(
            image.pixelColor(detail_x, 131).name(),
            ("#050608", "#f08a19"))

    def test_hot_cue_a_through_h_palette_is_exact_and_ignores_imported_rgb(self):
        from desktop.native_deck import NativeDeckBackend
        from desktop.player import _HOT_CUE_FALLBACKS

        expected = (
            "#ff376f", "#45acdb", "#7dc13d", "#aa72ff",
            "#30d26e", "#e0641b", "#305aff", "#c3af04",
        )
        self.assertEqual(_HOT_CUE_FALLBACKS, expected)
        imported = {"kind": "hot", "color": [1, 2, 3]}
        self.assertEqual(
            [NativeDeckBackend._cue_rgb(imported, slot)
             for slot in range(8)],
            [
                (255, 55, 111), (69, 172, 219), (125, 193, 61),
                (170, 114, 255), (48, 210, 110), (224, 100, 27),
                (48, 90, 255), (195, 175, 4),
            ],
        )

    def test_hot_and_memory_cues_gate_and_space_latches_playback(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        pb._playback_available = True
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]

            def position(target):
                pb._pos = target
                events.append(("seek", target))

            pb._set_position = position

            def play(**_options):
                state["playing"] = True
                events.append(("play",))

            def pause(after=None, **_options):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._play_clean = play
            pb._pause_clean = pause
            pb.cue_panel.set_cues([
                {"kind": "hot", "slot": 3, "position_ms": 12_345},
                {"kind": "memory", "slot": 0, "position_ms": 23_456},
            ])
            pb.cue_panel.set_loaded(True)

            pb.cue_panel.hot_slots[3].pressed.emit()
            self.assertTrue(state["playing"])
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
            self.assertEqual(events[-2:], [("seek", 12_345), ("play",)])
            pb.cue_panel.hot_slots[3].released.emit()
            self.assertFalse(state["playing"])
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(events[-2:], [("pause",), ("seek", 12_345)])

            pb.cue_panel.memory_slots[0].pressed.emit()
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
            pb.toggle()  # Space/play while held latches the preview.
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "❚❚")
            pb.cue_panel.memory_slots[0].released.emit()
            self.assertTrue(state["playing"])
            self.assertTrue(pb._cue_preview_latched)
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            pb.toggle()
            self.assertFalse(state["playing"])
            self.assertFalse(pb._cue_preview_latched)
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")

            pb._move_clean(34_567)
            pb.cue_btn.pressed.emit()
            # The first paused press relocates the marker and holds the orange
            # ring, without starting preview playback.
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(state["playing"])
            self.assertEqual(pb.wave._session_cue_ms, 34_567)
            pb.cue_btn.released.emit()
            self.assertFalse(pb.cue_btn.property("active"))
            pb.cue_btn.pressed.emit()
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.wave._session_cue_ms, 34_567)
            pb.toggle()
            pb.cue_btn.released.emit()
            self.assertTrue(state["playing"])
            self.assertEqual(pb._cue_ms, 34_567)
            self.assertTrue(pb._cue_preview_latched)
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "❚❚")
            pb.toggle()
            self.assertFalse(state["playing"])
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
        finally:
            pb.shutdown_waveforms()

    def test_memory_recall_sets_exact_regular_cue_without_changing_saved_cues_or_hot_behavior(self):
        from copy import deepcopy
        from desktop.player import PlayerBar

        for initially_playing in (False, True):
            with self.subTest(initially_playing=initially_playing):
                pb = PlayerBar()
                state = {'playing': initially_playing}
                try:
                    pb._src_path, pb._dur = '/tmp/loaded.wav', 60_000
                    pb._ordinary_playback_requested = initially_playing
                    pb._transport_play_active = initially_playing
                    pb.is_playing = lambda: state['playing']
                    pb._set_position = lambda target: setattr(pb, '_pos', target)
                    pb._play_clean = lambda **_options: state.update(playing=True)
                    def pause(after=None, **_options):
                        state['playing'] = False
                        if after is not None:after()
                    pb._pause_clean = pause
                    pb.quantize_enabled = True
                    pb._beat_grid = [23_000, 23_500, 24_000]
                    saved = [dict(kind='memory',slot=0,position_ms=23_456),
                             dict(kind='hot',slot=0,position_ms=34_567)]
                    pb._analysis_parts = {'cues': deepcopy(saved)}
                    pb.cue_panel.set_cues(saved);pb.cue_panel.set_loaded(True)
                    before = deepcopy(pb.cue_panel._cues)
                    creation = mock.Mock();pb.cue_panel.empty_pressed.connect(creation)
                    pb._set_session_cue(1_000)
                    with mock.patch.object(pb, 'quantize_cue_position',
                                           side_effect=AssertionError('Existing cue timing stays exact')):
                        pb.cue_panel.memory_slots[0].pressed.emit()
                        self.assertEqual((pb._cue_ms, pb.wave._session_cue_ms), (23_456, 23_456))
                        self.assertTrue(state['playing'])
                        pb._pos = 25_000
                        pb.cue_panel.memory_slots[0].released.emit()
                        self.assertFalse(state['playing'])
                        pb.cue_btn.pressed.emit()
                        self.assertEqual(pb._pos, 23_456)
                        pb._pos = 26_000
                        pb.cue_btn.released.emit()
                        self.assertEqual(pb._pos, 23_456)
                        self.assertFalse(state['playing'])
                        state['playing'] = initially_playing
                        pb._ordinary_playback_requested = initially_playing
                        pb.cue_panel.hot_slots[0].pressed.emit()
                        pb.cue_panel.hot_slots[0].released.emit()
                        self.assertEqual(state['playing'], initially_playing)
                    self.assertEqual(pb._cue_ms, 23_456)
                    self.assertEqual(pb.cue_panel._cues, before)
                    self.assertEqual(pb._analysis_parts['cues'], saved)
                    creation.assert_not_called()
                finally:
                    pb.shutdown_waveforms()

    def test_hot_cue_during_latched_playback_keeps_transport_running(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        pb._playback_available = True
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]
            pb._set_position = lambda target: events.append(("seek", target))

            def play(**_options):
                state["playing"] = True
                events.append(("play",))

            def pause(after=None, **_options):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._play_clean = play
            pb._pause_clean = pause
            pb.cue_panel.set_cues([
                {"kind": "hot", "slot": 0, "position_ms": 1_000},
                {"kind": "hot", "slot": 1, "position_ms": 2_000},
            ])
            pb.cue_panel.set_loaded(True)
            pb.cue_panel.hot_slots[0].pressed.emit()
            pb.toggle()
            pb.cue_panel.hot_slots[0].released.emit()
            self.assertTrue(pb._cue_preview_latched)

            pb.cue_panel.hot_slots[1].pressed.emit()
            self.assertTrue(pb._cue_preview_latched)
            self.assertTrue(state["playing"])
            pb.cue_panel.hot_slots[1].released.emit()
            self.assertTrue(state["playing"])
            self.assertEqual(events[-2:], [("seek", 2_000), ("play",)])
        finally:
            pb.shutdown_waveforms()

    def test_space_latch_survives_release_during_pending_seek_fade(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        state = {"playing": True}
        pending = []
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]
            pb._pause_clean = lambda after=None, **_options: pending.append(after)
            pb._set_position = lambda target: events.append(("seek", target))

            def play(**_options):
                state["playing"] = True
                events.append(("play",))

            pb._play_clean = play
            pb._start_cue_preview(9_876, ("hot", 3))
            pb.toggle()
            pb._release_cue_preview(("hot", 3))
            self.assertTrue(pb._cue_preview_latched)
            self.assertEqual(len(pending), 1)

            state["playing"] = False  # the real fade has now reached silence
            pending.pop()()
            self.assertTrue(state["playing"])
            self.assertEqual(events, [("seek", 9_876), ("play",)])
        finally:
            pb.shutdown_waveforms()

    def test_live_database_cues_override_stale_or_missing_waveform_sidecars(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        try:
            live = [{
                "kind": "hot", "slot": 3, "position_ms": 44_000,
                "label": "Drop", "color": [170, 114, 255],
            }]
            stale = [{
                "kind": "hot", "slot": 4, "position_ms": 44_000,
                "label": "Wrong shifted slot", "color": [1, 2, 3],
            }]
            pb._live_cues = live
            pb._apply_analysis({
                "beats": [], "heights": [], "colors": [],
                "sample_rate": 150.0, "cues": stale,
            })
            self.assertEqual(pb.wave._cues, live)
            self.assertIsNotNone(pb.cue_panel._cues["hot"][3])
            self.assertIsNone(pb.cue_panel._cues["hot"][4])

        finally:
            pb.close()
            pb.shutdown_waveforms()

    def test_live_grid_and_cues_survive_late_analysis_without_transport_changes(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._track_id, pb._src_path, pb._analysis_path = 'synthetic', '/synthetic/audio', ''
            pb._pending_autoplay = False
            cues = [dict(kind='hot', slot=3, position_ms=44000)]
            pb._live_cues = cues
            rows = [[1, 100, 90.0], [2, 766, 90.0], [3, 1433, 90.0]]
            with mock.patch.object(pb.player, 'set_grid', return_value=True), \
                    mock.patch.object(pb.player, 'set_analysis') as upload, \
                    mock.patch.object(pb.player, 'setPosition') as seek:
                self.assertFalse(pb.refresh_live_grid('other', pb._src_path, rows, 90))
                self.assertTrue(pb.refresh_live_grid('synthetic', pb._src_path, rows, 90))
                stale = dict(beats=[[1, 0, 120.0]], cues=[], heights=[.5], colors=[[14, 0, 0]])
                pb._on_analysis_manifest(pb._src_path, '', pb._analysis_generation, stale)
                pb._on_analysis_overview(pb._src_path, '', pb._analysis_generation, stale)
                self.assertEqual(pb.wave._beat_times, [100, 766, 1433])
                self.assertEqual(pb.wave._cues, cues)
                self.assertEqual(upload.call_args.args[0]['beats'], rows)
                self.assertEqual(pb._bpm, 90)
                seek.assert_not_called()
                pb.clear_live_grid()
                pb._on_analysis_overview(pb._src_path, '', pb._analysis_generation, stale)
                self.assertEqual(pb.wave._beat_times, [0])
                self.assertEqual(pb.wave._cues, cues)
        finally:
            pb.shutdown()

    def test_bpm_row_update_preserves_order_and_reconciles_sort_and_filter(self):
        from desktop.model import TrackModel, COLUMNS
        model = TrackModel([dict(id='a', bpm='90.00'), dict(id='b', bpm='140.00')])
        self.assertFalse(model.update_bpm('a', '180.00'))
        self.assertEqual([r['id'] for r in model._rows], ['a', 'b'])
        column = next(i for i, row in enumerate(COLUMNS) if row[0] == 'bpm')
        model.sort(column)
        self.assertEqual([r['id'] for r in model._rows], ['b', 'a'])
        self.assertTrue(model.update_bpm('a', '90.00'))
        self.assertEqual([r['id'] for r in model._rows], ['a', 'b'])
        model.set_filter('90.00')
        self.assertEqual(len(model._rows), 1)
        self.assertTrue(model.update_bpm('a', '180.00'))
        self.assertEqual(model._rows, [])

    def test_track_navigation_icons_are_exact_mirrors(self):
        from PySide6.QtCore import QSize
        from desktop.player import _track_nav_icon

        previous = _track_nav_icon(-1).pixmap(QSize(18, 14)).toImage()
        following = _track_nav_icon(1).pixmap(QSize(18, 14)).toImage()
        self.assertEqual(previous.size(), following.size())
        for y in range(previous.height()):
            for x in range(previous.width()):
                self.assertEqual(
                    previous.pixelColor(x, y),
                    following.pixelColor(previous.width() - 1 - x, y),
                )

    def test_scrub_disarmed_until_track_loaded(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        w.resize(400, 44)
        w.set_timing(10_000, 30_000)
        hits = []
        w.seeked.connect(hits.append)
        # Main-wave navigation begins only after the deck is armed.
        class Ev:
            def __init__(self, x=200.0, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w.mousePressEvent(Ev())
        w.mouseReleaseEvent(Ev())        # disarmed → ignored
        self.assertEqual(hits, [])
        w.set_active(True)
        w.mousePressEvent(Ev())
        self.assertEqual(hits, [])        # no jump on mouse-down
        w.mouseReleaseEvent(Ev())        # stationary grab lands where held
        self.assertEqual(hits, [10_000 / 30_000])
        w.mousePressEvent(Ev())
        w.mouseMoveEvent(Ev(108.0, Qt.LeftButton))
        w.mouseReleaseEvent(Ev(108.0))
        self.assertEqual(len(hits), 2)

    def test_play_track_arms_wave_and_rebinding_is_safe(self):
        pb = _win.player_bar
        self.assertFalse(pb.wave._active)
        pb.play_track("1", "/tmp/nonexistent.wav", title="X — Y")
        self.assertTrue(pb.wave._active)
        self.assertEqual(_win.now_playing.toolTip(), "")
        self.assertIn("X", _win.now_playing.text())
        pb.stop()

    def test_playback_error_signal_wired_to_status(self):
        _app.processEvents()  # drain any late async error from earlier tests
        hits = []
        real = _win._flash_status
        _win._flash_status = lambda msg, *a: hits.append(msg)
        try:
            _win.player_bar.playback_error.emit("boom")
            _app.processEvents()
        finally:
            _win._flash_status = real
        # The neighboring play_track test's REAL backend error can also land
        # during this window (async, timing varies) — the wiring claim is:
        # every playback_error becomes exactly this status flash.
        self.assertTrue(hits)
        self.assertEqual(set(hits), {"Playback error — hover for details"})

    def test_cloud_playback_failure_explains_provider_readiness(self):
        message = ("Cloud storage cannot provide set.m4a. Open your cloud storage "
                   "app and check its sign-in/connection, then press Play to retry.")
        _win._on_playback_error(message)
        self.assertEqual(_win.status_label.text(),
                         "Cloud file unavailable — check cloud storage")
        self.assertEqual(_win.status_label.toolTip(), message)

    def test_16_beat_jump_math_and_boundaries(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._dur = 30_000
            self.assertEqual(pb._beat_delta_ms(), 8_000)  # 16 beats at 120 BPM
            pb._pos = 7_999
            self.assertEqual(pb._beat_jump_target(-1),0)
            self.assertEqual(pb._beat_jump_target(1), 15_999)
            pb._pos = 22_001
            self.assertEqual(pb._beat_jump_target(-1), 14_001)
            self.assertEqual(pb._beat_jump_target(1),30_000)
        finally:
            pb.shutdown_waveforms()

    def test_16_beat_jump_uses_exact_rekordbox_grid(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._playback_available = True
            pb._dur = 60_000
            # Deliberately uneven grid: proves this is timestamps, not headline BPM.
            pb._beat_grid = [i * 500 + (40 if i >= 10 else 0) for i in range(40)]
            pb._pos = 5_140  # 100ms into grid beat at 5.040s
            self.assertEqual(pb._beat_jump_target(1), 13_140)
            self.assertEqual(pb._beat_jump_target(-1),0)  # partial jump reaches the beginning
            pb._pos = 13_140
            self.assertEqual(pb._beat_jump_target(-1), 5_140)
        finally:
            pb.shutdown_waveforms()

    def test_16_beat_jump_returns_to_audio_start_before_first_grid_row(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._src_path = '/synthetic/loaded.wav'
            pb._playback_available = True
            pb._dur = 60_000
            # A valid imported grid can start later than audio/cue zero.
            pb._beat_grid = [round(54 + i * 60_000 / 138) for i in range(100)]
            original_grid = list(pb._beat_grid)
            for start in (0, 53, 54):
                pb._pos = start
                target = pb._beat_jump_target(1)
                self.assertIsNotNone(target)
                pb._pos = target
                pb._update_transport()
                self.assertTrue(pb.beat_back_btn.isEnabled())
                self.assertEqual(pb._beat_jump_target(-1), start)
            self.assertEqual(pb._beat_grid, original_grid)
            pb._pos = 0
            self.assertIsNone(pb._beat_jump_target(-1))
            # Lead-ins can cover multiple beats, including an entire jump.
            pb._beat_grid = [10_000 + i * 500 for i in range(100)]
            pb._pos = 0
            self.assertEqual(pb._beat_jump_target(1), 8_000)
            pb._pos = 8_000
            self.assertEqual(pb._beat_jump_target(-1), 0)
        finally:
            pb.shutdown_waveforms()

    def test_jump_selector_supports_beats_and_bars(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        changes = []
        pb.jump_mode_changed.connect(changes.append)
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._dur = 120_000
            pb._pos = 20_000
            pb.set_jump_mode("4 BEATS")
            self.assertEqual(pb._beat_delta_ms(), 2_000)
            self.assertEqual(pb._beat_jump_target(1), 22_000)
            pb.set_jump_mode("8 BARS")
            self.assertEqual(pb._beat_delta_ms(), 16_000)
            self.assertEqual(pb._beat_jump_target(-1), 4_000)
            self.assertEqual(changes, ["4 BEATS", "8 BARS"])
            self.assertEqual(pb.beat_selector_btn.text(), "8 BARS")
        finally:
            pb.shutdown_waveforms()

    def test_beat_buttons_disable_at_track_edges(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb._playback_available = True
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._on_dur(30_000)
            pb._on_pos(0)
            self.assertFalse(pb.beat_back_btn.isEnabled())
            self.assertTrue(pb.beat_forward_btn.isEnabled())
            pb._on_pos(30_000)
            self.assertTrue(pb.beat_back_btn.isEnabled())
            self.assertFalse(pb.beat_forward_btn.isEnabled())
        finally:
            pb.shutdown_waveforms()

    def test_paused_cue_holds_preview_then_returns(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PAUSED
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

            def setPosition(self, ms):
                self.positions.append(ms)

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._pause_clean = lambda after=None, **_options: (
                fake.pause(), after() if after is not None else None)
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb._set_session_cue(12_345)
            pb._pos = 13_000
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 12_345)
            self.assertEqual(fake.plays, 1)
            self.assertTrue(pb._cue_preview_active)
            pb._pos = 13_000  # playback advanced while the button was held
            pb.cue_btn.released.emit()
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.positions, [12_345, 12_345])
            self.assertEqual(pb._pos, 12_345)
            self.assertFalse(pb._cue_preview_active)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_paused_manual_move_relocates_session_cue_once(self):
        from desktop.player import PlayerBar, SessionCueState

        pb = PlayerBar()
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]

            def position(target):
                pb._pos = target
                events.append(("seek", target))

            def play(**_options):
                state["playing"] = True
                events.append(("play",))

            def pause(after=None, **_options):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._set_position = position
            pb._play_clean = play
            pb._pause_clean = pause
            pb._set_session_cue(1_000)

            pb._move_clean(20_000)
            self.assertEqual(
                pb._session_cue_state,
                SessionCueState.RELOCATION_PENDING)
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 20_000)
            self.assertEqual(pb._session_cue_state, SessionCueState.SET)
            self.assertTrue(pb.cue_btn.property('active'))
            self.assertFalse(pb._cue_preview_active)
            self.assertFalse(state["playing"])
            self.assertEqual(events, [("seek", 20_000)])
            pb.cue_btn.released.emit()
            self.assertFalse(pb.cue_btn.property('active'))

            pb.cue_btn.pressed.emit()
            self.assertTrue(pb._cue_preview_active)
            self.assertTrue(pb.cue_btn.property('active'))
            self.assertTrue(state["playing"])
            self.assertEqual(events[-2:], [("seek", 20_000), ("play",)])
            pb.cue_btn.released.emit()
            self.assertFalse(state["playing"])
            self.assertFalse(pb.cue_btn.property('active'))

            pb._move_clean(31_000)
            self.assertEqual(
                pb._session_cue_state,
                SessionCueState.RELOCATION_PENDING)
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 31_000)
            self.assertFalse(pb._cue_preview_active)
            self.assertFalse(state["playing"])
        finally:
            pb.shutdown_waveforms()

    def test_stationary_playing_wave_grab_pauses_and_resumes_exactly(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 20_000
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def setPosition(self, ms):
                self.pos = ms
                self.positions.append(ms)

            def position(self):
                return self.pos

            def playbackRate(self):
                return 1.0

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

        class Ev:
            def position(self):
                return type("P", (), {
                    "x": lambda _self: 300.0,
                    "y": lambda _self: 100.0,
                })()

            def accept(self):
                pass

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            ramps = []
            pb._animate_gain = (
                lambda end, duration, completion=None:
                ramps.append((end, duration, completion)))
            pb._src_path = "/tmp/loaded.wav"
            pb._playback_available = True
            pb._dur = 60_000
            pb.wave.resize(600, 132)
            pb.wave.set_active(True)
            pb.wave.set_timing(20_000, 60_000)
            pb.wave.mousePressEvent(Ev())
            self.assertTrue(pb.wave.is_dragging())
            self.assertTrue(pb._deck_drag_active)
            self.assertFalse(pb._pause_pending)
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.positions, [])
            pb.wave.mouseReleaseEvent(Ev())
            self.assertEqual(fake.positions[-1], 20_000)
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.plays, 1)
            self.assertEqual(
                [(end, duration) for end, duration, _completion in ramps],
                [(pb._deck_volume, pb.FADE_IN_MS)],
            )
            self.assertFalse(pb._deck_drag_active)
            self.assertEqual(pb.wave._position_ms, 20_000)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_playing_waveform_drag_pauses_then_seeks_once_and_resumes(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 10_000
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return self.pos

            def setPosition(self, ms):
                self.pos = ms
                self.positions.append(ms)

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            ramps = []
            pb._animate_gain = (
                lambda end, duration, completion=None:
                ramps.append((end, duration, completion)))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._transport_play_active = True
            pb._anchor_transport(10_000, True)
            pb._on_wave_drag_started()
            self.assertEqual(fake.pauses, 1)
            self.assertFalse(pb._pause_pending)
            self.assertEqual(pb.audio.volume(), 0.0)
            self.assertFalse(pb._transport_running)
            self.assertEqual(ramps, [])
            pb._on_state(PlaybackState.PAUSED)
            self.assertTrue(pb._transport_play_active)

            pb._seek(8_000 / 30_000)  # release's one authoritative seek
            self.assertEqual(fake.positions, [])  # held until release
            pb._on_wave_drag_finished()
            self.assertEqual(fake.positions[-1], 8_000)
            self.assertEqual(fake.plays, 1)
            self.assertEqual(fake.state, PlaybackState.PLAYING)
            self.assertTrue(pb._transport_play_active)
            self.assertEqual(
                [(end, duration) for end, duration, _completion in ramps],
                [(pb._deck_volume, pb.FADE_IN_MS)],
            )
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_clean_pause_pins_backend_to_exact_presented_position(self):
        from desktop.player import PlayerBar
        from PySide6.QtTest import QTest

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 9_900
                self.holds = 0

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return self.pos

            def setPosition(self, ms):
                self.pos = ms

            def begin_visual_hold(self):
                self.holds += 1
                return 10_037.25

            def pause(self):
                self.state = PlaybackState.PAUSED

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._animate_gain = (
                lambda _end, _duration, completion=None:
                setattr(pb, "_gain_completion", completion))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._anchor_transport(10_000, True)
            QTest.qWait(12)
            pb._pause_clean()
            pb._on_gain_finished()
            # Resume starts from the exact newest Metal drawable, never from
            # the backend's stale coarse 9.9-second position.
            self.assertEqual(fake.pos, 10_037.25)
            self.assertEqual(fake.holds, 1)
            self.assertEqual(pb._pos, fake.pos)
            self.assertFalse(pb._transport_running)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_transport_clock_interpolates_between_backend_ticks(self):
        from desktop.player import PlayerBar
        from PySide6.QtTest import QTest

        class FakePlayer:
            def playbackState(self):
                return PlaybackState.PLAYING

            def playbackRate(self):
                return 1.0

        pb = PlayerBar()
        real_player = pb.player
        try:
            pb.player = FakePlayer()
            pb._dur = 10_000
            pb._anchor_transport(1_000, True)
            QTest.qWait(35)
            # The fake backend is frozen; only the monotonic transport clock
            # can advance this value between backend position signals.
            self.assertGreaterEqual(pb._transport_position(), 1_025)
            self.assertLess(pb._transport_position(), 1_150)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_activation_resynchronizes_audio_buttons_and_waveform(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.synced = 0

            def synchronize(self):
                self.synced += 1

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return 4321

            def set_visual_position(self, _ms, _preview):
                pass

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._pos = 1000
            pb._transport_play_active = False
            pb.resynchronize_transport()
            self.assertEqual(fake.synced, 1)
            self.assertEqual(pb._pos, 4321)
            self.assertTrue(pb._transport_play_active)
            self.assertEqual(pb.play_btn.text(), "❚❚")

            fake.state = PlaybackState.PAUSED
            pb.resynchronize_transport()
            self.assertEqual(pb._pos, 4321)
            self.assertFalse(pb._transport_play_active)
            self.assertEqual(pb.play_btn.text(), "▶")
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_new_transport_intent_rejects_stale_gain_completion(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        called = []
        try:
            pb._gain_completion = lambda: called.append("stale")
            pb._gain_epoch = pb._transport_epoch
            pb._begin_transport_intent()
            pb._on_gain_finished()
            self.assertEqual(called, [])
        finally:
            pb.shutdown_waveforms()

    def test_transport_has_no_python_repaint_timer(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            self.assertFalse(hasattr(pb, "_scroll_timer"))
        finally:
            pb.shutdown_waveforms()

    def test_backend_drift_is_slewed_not_applied_as_a_tick(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def playbackState(self):
                return PlaybackState.PLAYING

            def playbackRate(self):
                return 1.0

        pb = PlayerBar()
        real_player = pb.player
        try:
            pb.player = FakePlayer()
            pb._dur = 10_000
            pb._anchor_transport(1_000, True)
            before = pb._transport_anchor_ms
            pb._on_backend_pos(1_100)
            # A normal backend correction queues a slew; it cannot jump the
            # waveform in the signal handler.
            self.assertEqual(pb._transport_anchor_ms, before)
            self.assertGreater(pb._transport_slew_ms, 0)
            self.assertEqual(pb._transport_anchor_ms, before)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_new_track_waits_then_starts_on_first_grid_beat(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PAUSED
                self.positions = []
                self.plays = 0

            def playbackState(self):
                return self.state

            def setPosition(self, ms):
                self.positions.append(ms)

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._pending_autoplay = True
            pb._dur = 0  # analysis wins the race; media is not loaded yet
            pb._apply_analysis({
                "beats": [[1, 875, 140.0], [2, 1304, 140.0]],
                "heights": [], "colors": [],
                "provenance": {"grid": "rekordbox"},
            })
            self.assertEqual(fake.plays, 0)
            pb._on_dur(60_000)
            self.assertEqual(fake.positions, [875])
            self.assertEqual(fake.plays, 1)
            self.assertEqual(pb._pos, 875)
            self.assertEqual(pb._cue_ms, 875)
            self.assertEqual(pb.wave._session_cue_ms, 875)
            self.assertFalse(pb._pending_autoplay)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_failed_load_clears_play_and_pending_analysis_intent(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        real_player = pb.player
        try:
            pb.player = mock.Mock()
            pb._src_path = "/tmp/unavailable.m4a"
            pb._transport_play_active = True
            pb._ordinary_playback_requested = True
            pb._pending_autoplay = True
            pb._pending_start_ms = 875
            pb._pending_should_play = True
            pb._pause_pending = True
            pb._gain_completion = mock.Mock()
            old_epoch = pb._transport_epoch
            pb._on_error(None, "Cloud file unavailable")
            pb._on_dur(60_000)  # late load/analysis must not restart playback
            self.assertGreater(pb._transport_epoch, old_epoch)
            self.assertFalse(pb._transport_play_active)
            self.assertFalse(pb._ordinary_playback_requested)
            self.assertFalse(pb._pending_autoplay)
            self.assertFalse(pb._pause_pending)
            self.assertIsNone(pb._gain_completion)
            self.assertEqual(pb.play_btn.text(), "▶")
            pb.player.pause.assert_called_once_with()
            pb.player.play.assert_not_called()
            pb.player.setPosition.assert_not_called()
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_load_only_waits_then_pauses_on_first_grid_beat(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.positions = []
                self.plays = 0

            def playbackState(self):
                return PlaybackState.PAUSED

            def setPosition(self, ms):
                self.positions.append(ms)

            def play(self):
                self.plays += 1

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._pending_autoplay = True
            pb._pending_should_play = False
            pb._dur = 0
            pb._apply_analysis({
                "beats": [[1, 875, 140.0], [2, 1304, 140.0]],
                "heights": [], "colors": [],
                "provenance": {"grid": "rekordbox"},
            })
            self.assertEqual(fake.plays, 0)
            pb._on_dur(60_000)
            self.assertEqual(fake.positions, [875])
            self.assertEqual(fake.plays, 0)
            self.assertEqual(pb._pos, 875)
            self.assertEqual(pb._cue_ms, 875)
            self.assertFalse(pb._pending_autoplay)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_rgb_raster_is_stable_and_song_anchored(self):
        from desktop.player import WaveformBar, _rasterize_rgb
        heights = [0.1, 0.8, 0.2, 0.6, 0.4, 1.0]
        colors = [[1, 2, 3], [4, 5, 6], [7, 1, 2],
                  [2, 7, 1], [3, 4, 5], [14, 0, 0]]
        columns = _rasterize_rgb(heights, colors, 6.0, 3.0)
        # Fixed 2-sample bins choose the local peak once; viewport position
        # never participates in color selection.
        self.assertEqual(columns, [
            [0.8, [4, 5, 6]], [0.6, [2, 7, 1]], [1.0, [14, 0, 0]],
        ])
        w = WaveformBar()
        w.PIXELS_PER_SECOND = 3.0
        w.set_analysis({
            "heights": heights, "colors": colors, "sample_rate": 6.0,
            "columns": columns,
            "_detail_bands": bytes([
                1, 2, 3,
                4, 5, 127,
                6, 7, 8,
                9, 10, 11,
                12, 13, 14,
                15, 16, 17,
            ]),
        })
        before = [(height, color.rgb()) for height, color in w._rgb_columns]
        w.set_timing(250, 10_000)
        w.set_timing(750, 10_000)
        after = [(height, color.rgb()) for height, color in w._rgb_columns]
        self.assertEqual(after, before)
        self.assertEqual(w._song_column(1000), 3)
        self.assertEqual(w._rgb_detail[1][0], 1.0)

    def test_current_waveform_visibility_shoulder_preserves_true_zero(self):
        from desktop.player import _display_waveform_height

        values = [
            _display_waveform_height(value / 65535.0)
            for value in (0, 1, 2, 6, 66, 65535)
        ]
        self.assertEqual(values[0], 0.0)
        self.assertGreater(values[1], 0.0)
        self.assertGreater(values[3], 0.004)
        self.assertAlmostEqual(values[-1], 0.84)
        self.assertEqual(values, sorted(values))

    def test_qt_waveform_preserves_dedicated_rgb_despite_band_master(self):
        from desktop.player import WaveformBar

        colors = [[14, 0, 0], [0, 7, 0], [0, 0, 7]]
        w = WaveformBar()
        w.set_analysis({
            "heights": [0.5, 0.5, 0.5],
            "colors": colors,
            "sample_rate": 150.0,
            "_spectral_24": bytes(
                [255] * 8 + [0] * 16
                + [0] * 8 + [255] * 8 + [0] * 8
                + [0] * 16 + [255] * 8),
        })
        self.assertEqual(w._rgb_colors, colors)

    def test_waveform_builds_bar_beat_positions_from_imported_grid(self):
        from desktop.player import WaveformBar

        w = WaveformBar()
        w.set_analysis({
            "beats": [
                [1, 500, 120.0], [2, 1000, 120.0],
                [3, 1500, 120.0], [4, 2000, 120.0],
                [1, 2500, 120.0], [2, 3000, 120.0],
            ],
        })
        self.assertEqual(w._beat_positions, [
            (500, 1, 1), (1000, 1, 2), (1500, 1, 3),
            (2000, 1, 4), (2500, 2, 1), (3000, 2, 2),
        ])

    def test_grid_only_update_retains_wave_and_cues_and_counts_opening_partial_bar_zero(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        cue = dict(kind='hot', slot=0, position_ms=3000)
        w.set_analysis({'heights': [.5] * 300, 'colors': [[14, 0, 0]] * 300,
                        'sample_rate': 150.0, 'cues': [cue]})
        colors = w._rgb_colors
        beats = [[(i + 1) % 4 + 1, 9 + i * 2000 // 3, 90.0] for i in range(20)]
        w.set_grid(beats)
        self.assertIs(w._rgb_colors, colors)
        self.assertEqual(w._cues[0]['position_ms'], 3000)
        self.assertEqual(w._beat_positions[0], (9, 0, 2))
        self.assertIn((10009, 4, 1), w._beat_positions)

    def test_qt_fallback_draws_every_beat_as_opaque_timing_ink(self):
        source = (
            Path(__file__).parents[1] / "desktop" / "player.py"
        ).read_text()
        grid = source.split("def _paint_grid", 1)[1].split(
            "@staticmethod", 1)[0]
        self.assertIn("QColor(255, 55, 88)", grid)
        self.assertIn("QColor(120, 135, 158)", grid)
        self.assertNotIn("QColor(255, 55, 88, 175)", grid)
        self.assertNotIn("QColor(120, 135, 158, 85)", grid)

    def test_waveform_motion_keeps_subpixel_phase_at_120hz(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        # At 92 px/s, 120 Hz advances 0.736 logical pixels per frame. Integer
        # columns would duplicate some frames; the continuous song coordinate
        # preserves a distinct phase for every refresh.
        xs = [w._song_x(i * 8) for i in range(12)]
        self.assertEqual(len(set(xs)), len(xs))
        for a, b in zip(xs, xs[1:]):
            self.assertAlmostEqual(b - a, 0.736, places=6)

    def test_scrolling_waveform_seek_is_relative_to_fixed_playhead(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 92)
        w.set_active(True)
        w.set_timing(10_000, 30_000)
        hits = []
        w.seeked.connect(hits.append)
        # Center is current time; 92px right is exactly +1 second.
        self.assertEqual(w._time_at_x(200, 200), 10.0)
        self.assertEqual(w._time_at_x(292, 200), 11.0)
        # Grab the waveform and pull it left by 92px to reveal +1 second.
        w.mousePressEvent(Ev(200))
        w.mouseMoveEvent(Ev(108, Qt.LeftButton))
        w.mouseReleaseEvent(Ev(108))
        self.assertAlmostEqual(hits[-1], 11 / 30, places=4)
        self.assertEqual(w._position_ms, 11_000)

    def test_overview_click_seeks_to_absolute_song_position(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, y):
                self._x = x
                self._y = y

            def position(self):
                return type("P", (), {
                    "x": lambda _self: self._x,
                    "y": lambda _self: self._y,
                })()

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 132)
        w.set_active(True)
        w.set_timing(10_000, 30_000)
        previews, seeks = [], []
        w.previewed.connect(previews.append)
        w.seeked.connect(seeks.append)
        w.mousePressEvent(Ev(300, 10))
        self.assertEqual(previews, [22_500])
        w.mouseReleaseEvent(Ev(300, 10))
        self.assertEqual(seeks, [0.75])
        self.assertEqual(w._position_ms, 22_500)

    def test_zoom_controls_scale_only_the_detail_waveform(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, y):
                self._x, self._y = x, y

            def position(self):
                return type("P", (), {
                    "x": lambda _self: self._x,
                    "y": lambda _self: self._y,
                })()

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 132)
        w.set_timing(10_000, 30_000)
        changes, seeks = [], []
        w.zoom_changed.connect(changes.append)
        w.seeked.connect(seeks.append)
        overview_target = w._overview_time_at_x(300)
        self.assertEqual(w.PIXELS_PER_SECOND, 92.0)
        w.mousePressEvent(Ev(50, 40))  # plus
        self.assertEqual(w.PIXELS_PER_SECOND, 128.0)
        self.assertEqual(w._overview_time_at_x(300), overview_target)
        self.assertAlmostEqual(w._time_at_x(328, 200), 11.0)
        w.mousePressEvent(Ev(20, 40))  # minus
        self.assertEqual(w.PIXELS_PER_SECOND, 92.0)
        w.set_zoom(2000.0)
        self.assertEqual(w.PIXELS_PER_SECOND, 1500.0)
        self.assertEqual(w._overview_time_at_x(300), overview_target)
        self.assertAlmostEqual(w._time_at_x(1700, 200), 11.0)
        self.assertEqual(changes, [128.0, 92.0, 1500.0])
        self.assertEqual(seeks, [])

    def test_zoom_scale_is_one_bounded_canonical_sequence(self):
        from desktop.player import WaveformBar, _ZOOM_LEVELS

        w = WaveformBar()
        changes = []
        w.zoom_changed.connect(changes.append)
        for scale in _ZOOM_LEVELS:
            w.set_zoom(scale)
            self.assertEqual(w.PIXELS_PER_SECOND, scale)
        self.assertEqual(_ZOOM_LEVELS[0], 24.0)
        self.assertEqual(_ZOOM_LEVELS[-1], 1500.0)
        w.set_zoom(-100)
        self.assertEqual(w.PIXELS_PER_SECOND, 24.0)
        w.set_zoom(50_000)
        self.assertEqual(w.PIXELS_PER_SECOND, 1500.0)

    def test_waveform_drag_is_anchored_and_releases_exactly(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 92)
        w.set_active(True)
        w.set_analysis({
            "beats": [[1, 8_000, 140.0], [2, 9_000, 140.0],
                      [3, 10_000, 140.0]],
        })
        w.set_timing(10_000, 30_000)
        hits = []
        previews = []
        w.seeked.connect(hits.append)
        w.previewed.connect(previews.append)
        w.mousePressEvent(Ev(200))
        w.mouseMoveEvent(Ev(290, Qt.LeftButton))
        self.assertAlmostEqual(w._position_ms, 9_021.739, places=3)
        self.assertEqual(hits, [])  # no decoder seek while the hand is moving
        w.mouseMoveEvent(Ev(380, Qt.LeftButton))
        # Still calculated from the original 10s mouse-down position. The old
        # compounding implementation would have run away to roughly 7 seconds.
        self.assertAlmostEqual(w._position_ms, 8_043.478, places=3)
        self.assertEqual(hits, [])
        self.assertAlmostEqual(previews[0], 9_021.739, places=3)
        self.assertAlmostEqual(previews[1], 8_043.478, places=3)
        w.mouseReleaseEvent(Ev(380))
        # One exact decoder seek at the timestamp under the hand. There is no
        # release-time magnet that can make the waveform slip several pixels.
        self.assertAlmostEqual(hits[0] * 30_000, 8_043.478, places=3)

    def test_native_drag_preview_is_forwarded_before_release(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.visual = []

            def set_visual_position(self, ms, preview):
                self.visual.append((ms, preview))

            def playbackState(self):
                return PlaybackState.PAUSED

        pb = PlayerBar()
        real = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._pos = 10_000
            pb._on_wave_drag_started()
            pb._on_wave_preview(8_750)
            self.assertEqual(fake.visual, [
                (10_000, True), (8_750, True),
            ])
            pb._deck_drag_target = 8_750
            pb._paint_position(10_100)
            # A late backend/fade tick cannot overwrite the held destination
            # between mouse-up and the decoder's one authoritative seek.
            self.assertEqual(fake.visual, [
                (10_000, True), (8_750, True),
            ])
        finally:
            pb.player = real
            pb.shutdown_waveforms()

    def test_stationary_grab_uses_exact_native_hold_not_coarse_python_tick(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.positions = []
                self.visual = []

            def begin_visual_hold(self):
                return 10_037.25

            def set_visual_position(self, ms, preview):
                self.visual.append((ms, preview))

            def setPosition(self, ms):
                self.positions.append(ms)

            def playbackState(self):
                return PlaybackState.PAUSED

            def playbackRate(self):
                return 1.0

            def position(self):
                return 10_000

        pb = PlayerBar()
        real = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._dur = 30_000
            pb._pos = 10_000
            pb.wave._duration_ms = 30_000
            pb.wave._drag_origin_ms = 10_000
            pb.wave._drag_preview_ms = 10_000

            pb._on_wave_drag_started()

            self.assertEqual(pb._pos, 10_037.25)
            self.assertEqual(pb.wave._drag_origin_ms, 10_037.25)
            self.assertEqual(pb.wave._drag_preview_ms, 10_037.25)
            # Atomic native capture owns the initial freeze; there is no second
            # setPreview call at the stale 10,000 ms Python poll.
            self.assertEqual(fake.visual, [])

            pb._deck_drag_target = 10_037.25
            pb._on_wave_drag_finished()
            self.assertEqual(fake.positions, [10_037.25])
        finally:
            pb.player = real
            pb.shutdown_waveforms()

    def test_track_arrows_follow_visible_row_order(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _win.model.set_filter("")
        _app.processEvents()
        self.assertEqual(_win._player_order(), ["2", "1", "3"])
        played = []
        real = _win._load_track
        _win._load_track = lambda t, **_options: played.append(t.track_id)
        try:
            _win.player_bar._track_id = "1"
            _win._play_relative(-1)
            _win._play_relative(1)
        finally:
            _win._load_track = real
            _win.player_bar._track_id = None
        self.assertEqual(played, ["2", "3"])

    def test_grid_nudges_scale_by_zoom_and_keep_closest_views_precise(self):
        from desktop.player import PlayerBar
        from desktop.app import MainWindow
        from types import SimpleNamespace
        pb = PlayerBar()
        saved = []
        track = Track('nudge', 'Synthetic', '', 'file:///synthetic.wav')
        fake = SimpleNamespace(
            _sync_grid=lambda: None, player_bar=pb, _refresh_worker=None,
            _state_maintenance=False, _shutting_down=False,
            _device_operation_owned=lambda: False,
            tag_controller=SimpleNamespace(lib=Library({'nudge': track}, [], 'synthetic')),
            grid_controller=SimpleNamespace(reason=lambda *_a, **_k: '', is_local=lambda _tid: False,
                request=lambda *a, **k: saved.append((a, k)) or True))
        try:
            pb._track_id='nudge'; pb._src_path=str(track.path)
            pb._native_error = ''; pb._pending_autoplay = False
            pb.cue_panel.set_loaded(True)
            for zoom, step in ((24,50),(32,50),(48,25),(64,25),(92,10),(128,10),
                               (184,5),(256,5),(384,3),(576,3),(864,2),(1152,1),(1500,1)):
                pb.wave.set_zoom(zoom, emit=False)
                self.assertEqual(pb.grid_nudge_ms(),step)
                for direction, sign in (('left',-1),('right',1)):
                    for modifier, multiplier in ((Qt.NoModifier,1),(Qt.ShiftModifier,3)):
                        with mock.patch('desktop.app.QApplication.keyboardModifiers',return_value=modifier):
                            self.assertTrue(MainWindow._edit_grid(fake,direction))
                        self.assertEqual(saved[-1][0],('nudge','shift',sign*step*multiplier))
        finally:
            pb.shutdown_waveforms()

    def test_track_buttons_emit_direction_requests(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        hits = []
        try:
            pb._track_id = "loaded"
            pb.set_track_navigation(True, True)
            pb.previous_requested.connect(lambda: hits.append(-1))
            pb.next_requested.connect(lambda: hits.append(1))
            pb.previous_btn.click()
            pb.next_btn.click()
            self.assertEqual(hits, [-1, 1])
        finally:
            pb.shutdown_waveforms()


class QtTrapAudit(unittest.TestCase):
    """Regression pins for the 2026-07-03 Qt-trap audit."""

    def test_interaction_deferral_does_not_wake_every_waveform_frame(self):
        """Only the waveform host resize owns native geometry/display work."""
        class Worker:
            def __init__(self):
                self.seconds = []

            def defer_for_interaction(self, seconds):
                self.seconds.append(seconds)

        worker = Worker()
        player = _win.player_bar.player
        wakes = []
        old_worker = getattr(_win, "_wf_fill_worker", None)
        old_request = getattr(player, "request_display", None)
        _win._wf_fill_worker = worker
        player.request_display = lambda: wakes.append(True)
        try:
            _win._defer_library_prep(0.75)
            self.assertEqual(worker.seconds, [0.75])
            self.assertEqual(wakes, [])
        finally:
            _win._wf_fill_worker = old_worker
            if old_request is None:
                del player.request_display
            else:
                player.request_display = old_request

    def test_oneshots_pruned_after_finish(self):
        from desktop.workers import _OneShotArt
        w = _OneShotArt("1", lambda: None)
        before = list(getattr(_win, "_oneshots", []))
        _win._oneshots = [*before, w]
        _win._finish_oneshot(w)
        self.assertEqual(_win._oneshots, before)

    def test_space_only_plays_from_browsing_surfaces(self):
        # Policy tested directly (offscreen focus doesn't propagate reliably).
        self.assertTrue(_win._space_should_play(None))
        self.assertTrue(_win._space_should_play(_win.table))
        self.assertTrue(_win._space_should_play(_win.tree))
        self.assertTrue(_win._space_should_play(_win.device_tree))
        self.assertFalse(_win._space_should_play(_win.search))   # types a space
        self.assertFalse(_win._space_should_play(_win._grave))   # enabled per playback action only
        self.assertFalse(_win._space_should_play(_win._rules))
        self.assertFalse(_win._space_should_play(_win.fs_view))  # device browser

    def test_c_key_routes_press_and_release_to_momentary_cue(self):
        calls = []
        real_policy = _win._space_should_play
        real_press = _win.player_bar._cue_press
        real_release = _win.player_bar._cue_release
        _win._space_should_play = lambda _focus: True
        _win.player_bar._cue_press = lambda: calls.append("press")
        _win.player_bar._cue_release = lambda: calls.append("release")
        try:
            press = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.NoModifier)
            release = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
            self.assertTrue(_win.eventFilter(_win.table, press))
            self.assertTrue(_win.eventFilter(_win.table, release))
            self.assertEqual(calls, ["press", "release"])
        finally:
            _win._space_should_play = real_policy
            _win.player_bar._cue_press = real_press
            _win.player_bar._cue_release = real_release
            _win._cue_key_down = False

    def test_c_claims_shortcut_override_and_swallows_repeat_keypresses(self):
        calls = []
        real_policy = _win._space_should_play
        real_press = _win.player_bar._cue_press
        real_release = _win.player_bar._cue_release
        _win._space_should_play = lambda _focus: True
        _win.player_bar._cue_press = lambda: calls.append("press")
        _win.player_bar._cue_release = lambda: calls.append("release")
        try:
            override = QKeyEvent(
                QEvent.ShortcutOverride, Qt.Key_C, Qt.NoModifier)
            press = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.NoModifier)
            repeat = QKeyEvent(
                QEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c", True, 2)
            release = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
            self.assertTrue(_win.eventFilter(_win.table, override))
            self.assertTrue(override.isAccepted())
            self.assertTrue(_win.eventFilter(_win.table, press))
            self.assertTrue(_win.eventFilter(_win.table, repeat))
            self.assertTrue(_win.eventFilter(_win.table, release))
            self.assertEqual(calls, ["press", "release"])
        finally:
            _win._space_should_play = real_policy
            _win.player_bar._cue_press = real_press
            _win.player_bar._cue_release = real_release
            _win._cue_key_down = False

    def test_browser_views_do_not_offer_text_input_to_macos(self):
        """A held C must not enter Cocoa's accent-character composition path.

        ShortcutOverride alone cannot prevent that native input-method path;
        the non-text browser widgets themselves must opt out. Inline delegate
        editors remain real text widgets with IME support.
        """
        for view in (_win.table, _win.tree, _win.sb_tree, _win.device_tree,
                     _win.artists_view):
            self.assertFalse(
                view.testAttribute(Qt.WA_InputMethodEnabled),
                type(view).__name__,
            )

        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QLineEdit, QStyleOptionViewItem
        from desktop.trees import CellEditDelegate
        edit_model = QStandardItemModel()
        edit_model.appendRow(QStandardItem("editable"))
        delegate = CellEditDelegate()
        editor = delegate.createEditor(
            _win.table.viewport(), QStyleOptionViewItem(),
            edit_model.index(0, 0))
        try:
            self.assertIsInstance(editor, QLineEdit)
            self.assertTrue(editor.testAttribute(Qt.WA_InputMethodEnabled))
        finally:
            editor.deleteLater()

    def test_command_c_is_not_stolen_by_cue_shortcut(self):
        calls = []
        real_press = _win.player_bar._cue_press
        _win.player_bar._cue_press = lambda: calls.append("press")
        try:
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.MetaModifier)
            self.assertFalse(_win.eventFilter(_win.search, event))
            self.assertEqual(calls, [])
        finally:
            _win.player_bar._cue_press = real_press

    def test_art_index_save_is_atomic(self):
        import json, tempfile
        from pathlib import Path
        from soundbox.artwork import ArtCache
        with tempfile.TemporaryDirectory() as td:
            c = ArtCache(Path(td))
            c.index["x"] = {"status": "none"}
            c._save()
            self.assertEqual(json.loads((Path(td) / "index.json").read_text()),
                             {"x": {"status": "none"}})
            self.assertFalse((Path(td) / "index.tmp").exists())

    def test_device_destructive_guards_watch_the_real_export_worker(self):
        from pathlib import Path

        source = (
            Path(__file__).parents[1] / "desktop" / "device_browser.py"
        ).read_text()
        self.assertIn('"export_worker", "_resync_worker"', source)
        self.assertNotIn('"_export_worker"', source)


class ArtAutostartPolicy(unittest.TestCase):
    def test_autostart_only_when_uncached_tracks_exist(self):
        calls = []
        real = _win._start_art_fill
        _win._start_art_fill = lambda *a, **k: calls.append(1)
        try:
            # every track known to the cache → NO autostart
            for tid in _win.lib.tracks_by_id:
                _win.art_cache.index[tid] = {"status": "none"}
            _win._maybe_autostart_art_fill()
            self.assertEqual(calls, [])
            # one genuinely new track → autostart fires (and the worker's to-do
            # list is exactly the uncached ones, so only new songs get searched)
            _win.art_cache.index.pop(next(iter(_win.lib.tracks_by_id)))
            _win._maybe_autostart_art_fill()
            self.assertEqual(calls, [1])
        finally:
            _win._start_art_fill = real
            _win.art_cache.index.clear()


class ArtistCatalog(unittest.TestCase):
    def tearDown(self):
        _win.art_cache.index.clear()

    def test_closed_catalog_is_retired_from_parent_widget_tree(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QApplication
        from desktop.artistcatalog import ArtistCatalogDialog
        with mock.patch.object(_win, '_artist_avatar_groups', return_value=[('A', ['1'], None)]):
            QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
            _win._open_artist_catalog()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(_win.findChildren(ArtistCatalogDialog), [])

    def test_groups_only_avatar_tracks_by_artist(self):
        # track 1+3 wear avatars, 2 has a real cover → catalog shows avatars only
        _win.art_cache.index.update({
            "1": {"status": "ok", "src": "https://soundcloud.com/zed", "ext": "jpg"},
            "2": {"status": "ok", "src": "https://soundcloud.com/zed/track", "ext": "jpg"},
            "3": {"status": "ok", "src": "https://soundcloud.com/mia", "ext": "jpg"},
        })
        groups = _win._artist_avatar_groups()
        artists = {a: tids for a, tids, _img in groups}
        self.assertIn("1", artists.get("Zed", []))
        self.assertIn("3", artists.get("Mia", []))
        self.assertNotIn("2", sum((t for _a, t, _i in groups), []))

    def test_apply_sets_art_on_all_tracks(self):
        from soundbox.artwork import ArtResult
        _win._apply_artist_picture(["1", "3"], ArtResult(
            b"\xff\xd8z", "image/jpeg", 1.0, "https://soundcloud.com/zed", "Zed"))
        self.assertEqual(_win.art_cache.status_kind("1"), "avatar")
        self.assertEqual(_win.art_cache.status_kind("3"), "avatar")

    def test_dialog_stays_open_and_updates_row(self):
        from desktop.artistcatalog import ArtistCatalogDialog
        seen = []

        def on_action(choice, done):
            seen.append(choice["kind"])
            done(True, None)

        dlg = ArtistCatalogDialog([("Zed", ["1", "9"], None)], on_action)
        dlg.list.setCurrentItem(dlg.list.topLevelItem(0))
        dlg._delete()
        self.assertEqual(seen, ["delete"])
        self.assertEqual(dlg.result(), 0)   # NOT auto-closed — stays open
        self.assertEqual(dlg.list.topLevelItemCount(), 0)  # row gone
        self.assertIn("blocked", dlg.status.text())

    def test_delete_blocks_all_artist_tracks(self):
        _win.art_cache.index.update({tid: {'status': 'ok', 'ext': 'jpg',
                                          'src': 'https://soundcloud.com/zed'} for tid in ('1', '3')})
        _win._on_catalog_action(
            {"kind": "delete", "artist": "Zed", "tids": ["1", "3"]},
            lambda ok, img: None)
        self.assertEqual(_win.art_cache.status_kind("1"), "blocked")
        self.assertEqual(_win.art_cache.status_kind("3"), "blocked")
        _win.art_cache.index.clear()

    def test_catalog_group_action_preserves_a_newer_real_cover(self):
        from soundbox.artwork import ArtResult
        _win.art_cache.store_ok('1', ArtResult(b'\xff\xd8avatar', 'image/jpeg', 1,
                                              'https://soundcloud.com/zed', 'Avatar'))
        _win.art_cache.store_ok('3', ArtResult(b'\xff\xd8cover', 'image/jpeg', 1,
                                              'https://soundcloud.com/zed/track', 'Cover'))
        before = _win.art_cache.cover_snapshot('3')
        _win._on_catalog_action({'kind': 'delete', 'artist': 'Zed', 'tids': ['1', '3']},
                                lambda *_args: None)
        self.assertEqual(_win.art_cache.status_kind('1'), 'blocked')
        self.assertEqual(_win.art_cache.cover_snapshot('3'), before)

    def test_rename_flows_through_alias_plane(self):
        from PySide6.QtWidgets import QInputDialog
        from desktop.artistcatalog import ArtistCatalogDialog
        applied = []
        dlg = ArtistCatalogDialog(
            [("zedd", ["1"], None)],
            lambda c, done: (applied.append(c), done(True, None)))
        dlg.list.setCurrentItem(dlg.list.topLevelItem(0))
        real = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: ("ZED", True))
        try:
            dlg._rename()   # the ⌘R slot
        finally:
            QInputDialog.getText = real
        self.assertEqual(applied[0]["kind"], "rename")
        self.assertEqual(dlg.list.topLevelItem(0).text(0), "ZED")  # row renamed in place
        self.assertEqual(dlg.result(), 0)   # dialog persists for the next fix
        # app side: rename applies via the alias/merge machinery
        _win._on_catalog_action(
            {"kind": "rename", "artist": "zedd", "new": "ZED", "tids": ["1"]},
            lambda ok, img: None)
        self.assertEqual(_win.aliases.get("zedd"), "ZED")
        _win.aliases.clear()
        _win._build_artist_registry()


class SearchSelection(unittest.TestCase):
    def test_selection_survives_search_and_clear(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        # narrow to Bravo, click it
        _win.search.setText("brav")
        _win.table.setCurrentIndex(_win.model.index(0, 0))
        picked = _win.model._rows[0]["id"]
        # clear the search — the same track must still be selected (not oblivion)
        _win.search.setText("")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)
        # narrowing to something that EXCLUDES it: selection can't show; but
        # bringing it back re-seats it again
        _win.search.setText("charl")
        _win.search.setText("")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())


class GraveyardFreshness(unittest.TestCase):
    def test_delete_shows_in_graveyard_immediately(self):
        from PySide6.QtWidgets import QMessageBox
        real = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            _win._delete_tracks(["2"])  # no settings-open needed
        finally:
            QMessageBox.question = real
        names = [_win._grave.topLevelItem(i).text(0)
                 for i in range(_win._grave.topLevelItemCount())]
        self.assertTrue(any("alpha" in n for n in names))
        _win.hidden.clear()
        _win.hidden_dates.clear()
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()


class ArtistsView(unittest.TestCase):
    def test_columns_share_exact_playlist_boundaries(self):
        from desktop.model import COLUMNS

        _win._show_collection()
        _app.processEvents()
        table_header = _win.table.horizontalHeader()
        expected = {
            column: (
                table_header.sectionViewportPosition(column),
                table_header.sectionSize(column),
            )
            for column in range(len(COLUMNS))
            if not _win.table.isColumnHidden(column)
        }

        _win._show_artists()
        _app.processEvents()
        artist_header = _win.artists_view.header()
        self.assertEqual(_win.artists_view.columnCount(), len(COLUMNS))
        self.assertEqual(
            [artist_header.model().headerData(i, Qt.Horizontal)
             for i in range(len(COLUMNS))],
            ["Artist / Track", *(label for _key, label, _hidden in COLUMNS[1:])],
        )
        actual = {
            column: (
                artist_header.sectionViewportPosition(column),
                artist_header.sectionSize(column),
            )
            for column in range(len(COLUMNS))
            if not _win.artists_view.isColumnHidden(column)
        }
        self.assertEqual(actual, expected)

    def test_resizing_either_view_keeps_shared_boundaries(self):
        column = 1  # Artist
        original = _win.table.columnWidth(column)
        try:
            _win.table.setColumnWidth(column, original + 17)
            _app.processEvents()
            self.assertEqual(
                _win.artists_view.columnWidth(column), original + 17)
            _win.artists_view.setColumnWidth(column, original + 9)
            _app.processEvents()
            self.assertEqual(_win.table.columnWidth(column), original + 9)
        finally:
            _win.table.setColumnWidth(column, original)

    def test_click_opens_browser_folder_page(self):
        _win._refresh_custom()
        it = _win.sb_tree.topLevelItem(1)
        self.assertIn("Artists", it.text(0))
        self.assertIn("(3)", it.text(0))          # 3 distinct artists
        _win.sb_tree.setCurrentItem(it)
        _app.processEvents()
        self.assertEqual(_win.stack.currentIndex(), 2)   # the Artists browser
        names = [_win.artists_view.topLevelItem(i).text(0)
                 for i in range(_win.artists_view.topLevelItemCount())]
        self.assertEqual(names, ["Ann  (1)", "Mia  (1)", "Zed  (1)"])
        # Songs are already ready; collapsed folders keep the same appearance.
        self.assertEqual(_win.artists_view.topLevelItem(0).childCount(), 1)
        self.assertFalse(_win.artists_view.topLevelItem(0).isExpanded())

    def test_expand_reveals_prepared_songs(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)
        _app.processEvents()
        self.assertEqual(zed.childCount(), 1)
        self.assertEqual(zed.child(0).text(0), "Bravo")
        self.assertEqual(zed.child(0).text(1), "Zed")   # artist column visible

    def test_selected_track_reads_artists_page(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)
        _app.processEvents()
        _win.artists_view.setCurrentItem(zed.child(0))
        t = _win._selected_track()      # browser selection source of truth
        self.assertIsNotNone(t)
        self.assertEqual(t.track_id, "1")
        _win.artists_view.setCurrentItem(zed)   # artist row = nothing to play
        self.assertIsNone(_win._selected_track())

    def test_stray_letter_never_moves_selection(self):
        """Qt type-ahead is OFF in every browser: pressing a letter while a
        view has focus must not teleport the selection (search box filters)."""
        from PySide6.QtTest import QTest
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        _win.artists_view.setCurrentItem(zed)
        for w in (_win.artists_view, _win.sb_tree, _win.tree, _win.device_tree,
                  _win.table):
            self.assertIsNone(w.keyboardSearch("c"), type(w).__name__)
        QTest.keyClick(_win.artists_view, Qt.Key_A)   # 'a' would jump w/o fix
        self.assertIs(_win.artists_view.currentItem(), zed)

    def test_inline_edit_routes_to_overrides(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)
        _app.processEvents()
        child = zed.child(0)
        fetch = _win._art_fetch_one
        _win._art_fetch_one = lambda tid: None  # no worker threads in tests
        try:
            child.setText(0, "Bravo Deluxe")       # fires _on_artist_item_edited
            self.assertEqual(_win.titles.get("1"), "Bravo Deluxe")
            child.setText(1, "ZEDD")               # artist edit → override
            self.assertEqual(_win.overrides.get("1"), "ZEDD")
            long = "X" * 500                        # clamp like Collection
            child.setText(0, long)
            from soundbox.paths import TITLE_MAX
            self.assertEqual(len(_win.titles["1"]), TITLE_MAX)
        finally:
            _win._art_fetch_one = fetch
            _win.titles.pop("1", None)
            _win.overrides.pop("1", None)
            _win._save_state()
            _win._build_artist_registry()
        # collection view still works after (back to the table page)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual(_win.stack.currentIndex(), 0)


class DefaultView(unittest.TestCase):
    def test_lands_on_collection_when_nothing_selected(self):
        _win._select_only(None)  # clear all three sidebars
        _win.sb_tree.setCurrentItem(None)
        _win.tree.setCurrentItem(None)
        _win._default_to_collection()
        cur = _win.sb_tree.currentItem()
        self.assertIsNotNone(cur)
        self.assertIn("Collection", cur.text(0))


class SortSelection(unittest.TestCase):
    def test_selection_survives_sort_toggle_after_search(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        _win.search.setText("brav")
        _win.table.setCurrentIndex(_win.model.index(0, 0))
        picked = _win.model._rows[0]["id"]
        _win.search.setText("")
        _win.table.sortByColumn(0, Qt.AscendingOrder)   # A→Z
        _app.processEvents()
        _win.table.sortByColumn(0, Qt.DescendingOrder)  # Z→A
        _app.processEvents()
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)
        _win.table.sortByColumn(-1, Qt.AscendingOrder)
        _win.search.setText("")



class ViewPlaceMemory(unittest.TestCase):
    """Leaving a view and coming back re-seats where you were — never back
    at the top like you were never there."""

    def _click(self, item):
        _win.sb_tree.setCurrentItem(item)
        _app.processEvents()

    def test_collection_place_survives_round_trip(self):
        self._click(_win.sb_tree.topLevelItem(0))       # Collection
        self.assertGreater(len(_win.model._rows), 1)
        row = len(_win.model._rows) - 1                 # park on the LAST track
        picked = _win.model._rows[row]["id"]
        _win.table.setCurrentIndex(_win.model.index(row, 0))
        self._click(_win.sb_tree.topLevelItem(1))       # off to Artists…
        self._click(_win.sb_tree.topLevelItem(0))       # …and back
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)


class NowPlayingHighlight(unittest.TestCase):
    """Loading a track always puts the cursor on it in the visible browser."""

    def test_table_highlights_loaded_track(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()
        _win.table.clearSelection()
        _win.table.setCurrentIndex(_win.model.index(-1, -1))       # nothing current
        _win._highlight_now_playing("2")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], "2")

    def test_artists_page_expands_and_highlights(self):
        from desktop.app import ARTIST_TRACK_ROLE
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))  # Artists
        _app.processEvents()
        _win._highlight_now_playing("1")                           # Zed — Bravo
        it = _win.artists_view.currentItem()
        self.assertIsNotNone(it)
        self.assertEqual(it.data(0, ARTIST_TRACK_ROLE), "1")
        self.assertTrue(it.parent().isExpanded())

    def test_track_not_in_view_is_a_noop(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        before = _win.table.currentIndex().row()
        _win._highlight_now_playing("no-such-track")
        self.assertEqual(_win.table.currentIndex().row(), before)

    def test_title_focus_clears_search_and_falls_back_to_collection(self):
        from desktop.app import COLLECTION_ROLE

        playlist = _win._find_tree_item("SET TWO")
        self.assertIsNotNone(playlist)
        _win.tree.setCurrentItem(playlist)  # contains only track 2
        _win.search.setText("alpha")
        _win.player_bar._track_id = "1"
        try:
            _win._focus_now_playing()
            self.assertEqual(_win.search.text(), "")
            self.assertTrue(
                _win.sb_tree.currentItem().data(0, COLLECTION_ROLE))
            idx = _win.table.currentIndex()
            self.assertTrue(idx.isValid())
            self.assertEqual(_win.model._rows[idx.row()]["id"], "1")
        finally:
            _win.player_bar._track_id = None

    def test_title_focus_preserves_current_view_when_track_is_present(self):
        playlist = _win._find_tree_item("CRATES / SET ONE")
        self.assertIsNotNone(playlist)
        _win.tree.setCurrentItem(playlist)
        _win.player_bar._track_id = "1"
        try:
            _win._focus_now_playing()
            self.assertIs(_win.tree.currentItem(), playlist)
            idx = _win.table.currentIndex()
            self.assertEqual(_win.model._rows[idx.row()]["id"], "1")
        finally:
            _win.player_bar._track_id = None


class AutoplaySemantics(unittest.TestCase):
    def setUp(self):
        self._saved = (
            _win._autoplay_mode,
            list(_win._autoplay_order),
            _win._autoplay_index,
            set(_win._autoplay_random_seen),
            list(_win._autoplay_random_queue),
        )
        self._real_save = _win._save_state
        self._context = getattr(_win, '_autoplay_context', None)
        self._navigation = getattr(_win, '_playback_navigation', None)
        self._audition = getattr(_win, '_cue_audition_track', None)
        _win.__dict__.pop('_playback_navigation', None)
        _win._autoplay_context = None
        _win._cue_audition_track = None
        _win._save_state = lambda: None
        _win._set_autoplay_mode("", save=False)

    def origin(self, identities, index):
        from desktop.playback_navigation import capture
        order = capture(_win.lib, ('transport-fixture',), [{'id': key} for key in identities],
                        _win._native_row_identity)
        cursor = order.cursor(index)
        _win._navigation().committed = cursor
        _win._install_autoplay_context(cursor)

    def tearDown(self):
        _win._save_state = self._real_save
        (mode, order, index, seen, queue) = self._saved
        _win._set_autoplay_mode(mode, save=False)
        _win._autoplay_order = order
        _win._autoplay_index = index
        _win._autoplay_random_seen = seen
        _win._autoplay_random_queue = queue
        _win._autoplay_context = self._context
        _win._cue_audition_track = self._audition
        if self._navigation is None:
            _win.__dict__.pop('_playback_navigation', None)
        else:
            _win._playback_navigation = self._navigation

    def test_three_header_modes_are_mutually_exclusive_and_toggle_off(self):
        for mode, button in _win._autoplay_buttons.items():
            button.click()
            self.assertEqual(_win._autoplay_mode, mode)
            self.assertTrue(button.property("active"))
            self.assertEqual(
                sum(bool(b.property("active"))
                    for b in _win._autoplay_buttons.values()),
                1,
            )
            button.click()
            self.assertEqual(_win._autoplay_mode, "")
            self.assertFalse(button.property("active"))

    def test_up_and_down_keep_the_playlist_that_started_the_track(self):
        playlist = _win._find_tree_item("CRATES / SET ONE")
        _win.tree.setCurrentItem(playlist)
        _app.processEvents()
        _win._capture_autoplay_context("1")
        self.assertEqual(_win._autoplay_order, ["3", "1"])
        self.assertEqual(_win._autoplay_index,1)

        # Browse elsewhere after playback starts; the frozen origin remains.
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        real_playable = _win._autoplay_playable
        _win._autoplay_playable = lambda _index: True
        try:
            _win._set_autoplay_mode("up", save=False)
            self.assertEqual(_win._next_autoplay_index(), 0)
            _win._autoplay_index = 0
            _win._set_autoplay_mode("down", save=False)
            self.assertEqual(_win._next_autoplay_index(), 1)
        finally:
            _win._autoplay_playable = real_playable

    def test_random_is_a_shuffle_bag_without_repeats(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        order = [row["id"] for row in _win.model._rows]
        _win._capture_autoplay_context(order[0])
        _win._set_autoplay_mode("random", save=False)
        _win._autoplay_rng.seed(7)
        real_playable = _win._autoplay_playable
        _win._autoplay_playable = lambda _index: True
        played = [order[0]]
        try:
            for _ in range(len(order) - 1):
                index = _win._next_autoplay_index()
                self.assertIsNotNone(index)
                _win._autoplay_index = index
                track_id = order[index]
                _win._autoplay_random_seen.add(track_id)
                played.append(track_id)
            self.assertEqual(len(set(played)), len(order))
            # A fresh cycle cannot immediately repeat the just-finished song.
            next_index = _win._next_autoplay_index()
            self.assertIsNotNone(next_index)
            self.assertNotEqual(order[next_index], played[-1])
        finally:
            _win._autoplay_playable = real_playable

    def test_terminal_advance_keeps_context_and_rejects_a_stale_end(self):
        self.origin(["3", "1"], 0)
        _win._set_autoplay_mode("down", save=False)
        real_playable = _win._autoplay_playable
        real_open = _win._open_track
        current = _win.player_bar._track_id
        opened = []
        _win._autoplay_playable = lambda _index: True
        requested = []
        def request(track, autoplay, capture_autoplay, **options):
            requested.append(options)
            return (
            opened.append(
                (track.track_id, autoplay, capture_autoplay)) or True)
        _win._open_track = request
        _win.player_bar._track_id = "3"
        try:
            _win._advance_autoplay("old-track")
            self.assertEqual(opened, [])
            _win._review_audition_active = True
            _win._advance_autoplay("3")
            self.assertEqual(opened, [])
            _win._review_audition_active = False
            _win._advance_autoplay("3")
            self.assertEqual(opened, [("1", True, False)])
            self.assertEqual(_win._autoplay_order, ["3", "1"])
            # A request is not a committed source handoff. The separate
            # lifecycle regressions cover accepting/cancelling this cursor.
            self.assertEqual(_win._autoplay_index, 0)
            self.assertEqual(requested[0]['autoplay_index'], 1)
            self.assertEqual(requested[0]['occurrence'].index, 1)
        finally:
            _win._review_audition_active = False
            _win._autoplay_playable = real_playable
            _win._open_track = real_open
            _win.player_bar._track_id = current

    def test_all_modes_advance_after_transient_pause_and_activation(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        fake = mock.Mock()
        fake.position.return_value = 1000
        fake.playbackRate.return_value = 1.0
        fake.playbackState.return_value = PlaybackState.PAUSED
        fake.source_retry_needed = False
        try:
            with (mock.patch.object(pb, "player", fake),
                  mock.patch.object(pb, "_animate_gain"),
                  mock.patch.object(_win, "player_bar", pb),
                  mock.patch.object(_win, "_dev_scan_worker", object()),
                  mock.patch.object(_win, "_autoplay_playable", return_value=True),
                  mock.patch.object(_win, "_open_track", return_value=True) as opened):
                pb.track_ended.connect(_win._advance_autoplay)
                pb._src_path = "/tmp/loaded.wav"
                pb._playback_available = True
                pb._waveform_ready = True
                pb._dur = 2000
                for mode in ("up", "random", "down"):
                    for resume in (False, True):
                        with self.subTest(mode=mode, resume=resume):
                            opened.reset_mock()
                            pb.pause()
                            self.origin(["1", "3", "2"], 1)
                            _win._set_autoplay_mode(mode, save=False)
                            pb._track_id = "3"
                            pb.toggle()
                            if resume:
                                pb.pause()
                                self.assertFalse(pb._ordinary_playback_requested)
                                pb.toggle()
                            # A refill/seek can publish PAUSED before PLAYING;
                            # activation may also land between those snapshots.
                            pb._on_state(PlaybackState.PAUSED)
                            pb.resynchronize_transport()
                            self.assertTrue(pb._transport_play_active)
                            pb._on_state(PlaybackState.PLAYING)
                            # The terminal flag can land a poll after PAUSED.
                            pb._on_state(PlaybackState.PAUSED)
                            pb._on_end_of_media()
                            pb._on_end_of_media()
                            _app.processEvents()
                            opened.assert_called_once()
                            request = opened.call_args.kwargs
                            self.assertEqual(set(request), {'autoplay', 'capture_autoplay',
                                                           'occurrence', 'autoplay_index'})
                            self.assertTrue(request['autoplay'])
                            self.assertFalse(request['capture_autoplay'])
                            self.assertEqual(request['occurrence'].index, request['autoplay_index'])
                            self.assertTrue(request['occurrence'].order.valid(_win.lib, request['autoplay_index']))
                            self.assertEqual(request['occurrence'].entry.song[0], opened.call_args.args[0].track_id)
        finally:
            pb.shutdown()

    def test_random_advances_during_device_discovery_after_cue_latch(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        fake = mock.Mock()
        fake.position.return_value = 1000
        fake.playbackRate.return_value = 1.0
        fake.playbackState.return_value = PlaybackState.PAUSED
        try:
            with (mock.patch.object(pb, "player", fake),
                  mock.patch.object(pb, "_animate_gain"),
                  mock.patch.object(_win, "player_bar", pb),
                  mock.patch.object(_win, "_dev_scan_worker", object()),
                  mock.patch.object(_win, "_autoplay_playable", return_value=True),
                  mock.patch.object(_win, "_open_track", return_value=True) as opened):
                pb.track_ended.connect(_win._advance_autoplay)
                pb._src_path = "/tmp/loaded.wav"
                pb._playback_available = True
                pb._track_id = "3"
                self.origin(["1", "3", "2"], 1)
                _win._set_autoplay_mode("random", save=False)
                pb._cue_press()
                self.assertFalse(pb._ordinary_playback_requested)
                fake.playbackState.return_value = PlaybackState.PLAYING
                pb.toggle()  # Play/Space promotes the held cue to normal play.
                self.assertTrue(pb._ordinary_playback_requested)
                fake.playbackState.return_value = PlaybackState.PAUSED
                pb._on_state(PlaybackState.PAUSED)
                pb._on_end_of_media()
                # Releasing the now-latched cue is not a new transport command,
                # even if it lands just before deferred terminal delivery.
                pb._cue_release()
                _app.processEvents()
                opened.assert_called_once()
                self.assertNotEqual(opened.call_args.args[0].track_id, "3")
        finally:
            pb.shutdown()

    def test_explicit_pause_and_new_command_cancel_terminal_advance(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        hits = []
        pb.track_ended.connect(hits.append)
        try:
            pb._track_id = "ended-track"
            pb._ordinary_playback_requested = True
            pb._pause_clean()
            pb._on_end_of_media()
            _app.processEvents()
            self.assertEqual(hits, [])
            pb._ordinary_playback_requested = True
            pb._on_end_of_media()
            pb._begin_transport_intent()
            _app.processEvents()
            self.assertEqual(hits, [])
        finally:
            pb.shutdown()

    def test_only_ordinary_transport_end_requests_advance(self):
        pb = _win.player_bar
        hits = []
        on_end = hits.append
        pb.track_ended.connect(on_end)
        saved = (
            pb._track_id,
            pb._ordinary_playback_requested,
            pb._cue_preview_active,
            pb._cue_preview_latched,
            pb._pause_pending,
        )
        try:
            pb._ordinary_playback_requested = True
            pb._track_id = "ended-track"
            pb._cue_preview_active = False
            pb._cue_preview_latched = False
            pb._pause_pending = False
            pb._on_end_of_media()
            _app.processEvents()
            self.assertEqual(hits, ["ended-track"])

            pb._ordinary_playback_requested = False
            pb._on_end_of_media()
            _app.processEvents()
            self.assertEqual(hits, ["ended-track"])
        finally:
            (
                pb._track_id,
                pb._ordinary_playback_requested,
                pb._cue_preview_active,
                pb._cue_preview_latched,
                pb._pause_pending,
            ) = saved
            pb.track_ended.disconnect(on_end)


class SpaceSemantics(unittest.TestCase):
    """Space controls only the loaded deck and never opens browser selection."""

    def setUp(self):
        self._played = []
        self._toggled = []
        self._orig_play, self._orig_toggle = _win._play_track, _win.player_bar.toggle
        _win._play_track = lambda t: self._played.append(t.track_id)
        _win.player_bar.toggle = lambda: self._toggled.append(1)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()

    def tearDown(self):
        _win._play_track, _win.player_bar.toggle = self._orig_play, self._orig_toggle
        _win.player_bar._track_id = None

    def _select(self, tid):
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == tid)
        _win.table.setCurrentIndex(_win.model.index(row, 0))

    def test_different_track_highlighted_still_toggles_loaded_deck(self):
        _win.player_bar._track_id = "1"       # something else is loaded
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])

    def test_same_track_highlighted_toggles(self):
        _win.player_bar._track_id = "2"
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])

    def test_nothing_loaded_is_a_noop(self):
        _win.player_bar._track_id = None
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [])


class DoubleClickSemantics(unittest.TestCase):
    """Browser mouse activation is the sole direct load gesture."""

    def setUp(self):
        self._loaded = []
        self._played = []
        self._orig_load, self._orig_play = _win._load_track, _win._play_track
        _win._load_track = lambda t, **_options: self._loaded.append(t.track_id)
        _win._play_track = lambda t: self._played.append(t.track_id)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()

    def tearDown(self):
        _win._load_track, _win._play_track = self._orig_load, self._orig_play

    def test_browser_load_passes_preserve_intent_policy(self):
        from desktop.app import MainWindow
        with mock.patch.object(_win, "_open_track") as load:
            MainWindow._load_track(_win, _win.lib.tracks_by_id["2"])
        load.assert_called_once_with(_win.lib.tracks_by_id["2"],
                                     autoplay=None, capture_autoplay=True)

    def test_table_double_click_routes_to_browser_load(self):
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == "2")
        _win._play_row(_win.model.index(row, 0))
        self.assertEqual(self._loaded, ["2"])
        self.assertEqual(self._played, [])

    def test_artist_double_click_routes_to_browser_load(self):
        from desktop.app import ARTIST_TRACK_ROLE
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))  # Artists
        _app.processEvents()
        top = _win.artists_view.topLevelItem(0)
        top.setExpanded(True)
        child = top.child(0)
        self.assertIsNotNone(child)
        self.assertTrue(child.data(0, ARTIST_TRACK_ROLE))
        _win._artists_double(child, 0)
        self.assertEqual(len(self._loaded), 1)
        self.assertEqual(self._played, [])


class QtWaveformFallback(unittest.TestCase):
    """AAC/ALAC receive the same canonical V5 analysis via QAudioDecoder."""

    @classmethod
    def setUpClass(cls):
        import math, shutil, struct, subprocess, tempfile, wave
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg and not shutil.which("afconvert"):
            raise unittest.SkipTest("AAC/ALAC fixture encoder not available")
        cls.dir = tempfile.mkdtemp(prefix="sb-wave-")
        cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)
        wav = f"{cls.dir}/probe.wav"
        sr = 44100
        with wave.open(wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(b"".join(
                struct.pack("<h", int(0.8 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
                            if i < sr else 0)
                for i in range(sr * 2)))          # 1s tone + 1s silence
        for name, codec in (("alac.m4a", ["-d", "alac"]),
                            ("aac.m4a", ["-d", "aac", "-b", "128000"])):
            # ffmpeg creates the same synthetic codec fixtures without macOS
            # AudioComponent services, which restricted runners may not expose.
            command = ([ffmpeg, "-v", "error", "-i", wav,
                        "-c:a", codec[1], f"{cls.dir}/{name}"] if ffmpeg else
                       ["afconvert", "-f", "m4af", *codec, wav,
                        f"{cls.dir}/{name}"])
            subprocess.run(command, check=True, capture_output=True)

    def _check(self, name):
        from desktop.player import qt_detail_peaks
        path = f"{self.dir}/{name}"
        detail = qt_detail_peaks(path)
        self.assertGreater(detail["detail_rate"], 6100)
        self.assertLess(detail["detail_rate"], 6200)
        self.assertEqual(
            detail["height_encoding"], "linear-u16le")
        import numpy as np
        heights = np.frombuffer(detail["detail_heights"], dtype="<u2")
        midpoint = len(heights) // 2
        self.assertGreater(int(max(heights[:midpoint - 30])), 63_000)
        self.assertLess(int(max(heights[midpoint + 30:])), 3_100)

    def test_imported_aac_alac_compact_upgrade_preserves_streams_and_native_rate(self):
        import hashlib
        from pathlib import Path
        from desktop.player import qt_detail_peaks
        from soundbox.waveform_detail import analyze_imported, DETAIL_MODEL
        from soundbox import track_analysis as ta
        imported = {"sample_rate": 150.0, "beats": [[1, 0, 120]],
                    "_blue_detail": bytes([31]) * 300,
                    "_rgb_detail": bytes.fromhex("e47c") * 300,
                    "_detail_bands": bytes([30, 40, 50]) * 300}
        for codec in ("aac", "alac"):
            with self.subTest(codec=codec):
                path = Path(self.dir) / f"{codec}.m4a"
                before = hashlib.sha256(path.read_bytes()).digest()
                decode = lambda: qt_detail_peaks(str(path), supplement=True)
                with mock.patch.object(ta, "StreamingAnalyzer", side_effect=AssertionError("full analyzer forbidden")):
                    data = analyze_imported(path, imported, None, decode)
                self.assertIsNotNone(data)
                self.assertEqual(data["waveform_model"], DETAIL_MODEL)
                self.assertEqual(data["timeline_sample_rate"], 44100)
                self.assertAlmostEqual(len(data["detail_minimum_values"]) / 2, 2000, delta=25)
                cache = Path(self.dir) / "compact"
                self.assertTrue(ta.store(cache, codec, path, data))
                self.assertTrue(ta.verify_complete(cache, codec, path))
                loaded = ta.load_detail(cache, codec, path)
                for key in ("_blue_detail", "_rgb_detail", "_detail_bands"):
                    self.assertEqual(loaded[key], imported[key])
                if codec == "alac":
                    reference = analyze_imported(Path(self.dir) / "probe.wav", imported)
                    for key in ("detail_minimum_values", "detail_maximum_values"):
                        self.assertEqual(data[key], reference[key])
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
                self.assertIsNone(qt_detail_peaks(str(path), checkpoint=lambda: False, supplement=True))

    def test_alac_waveform(self):
        self._check("alac.m4a")

    def test_aac_waveform(self):
        self._check("aac.m4a")


class DuplicateAdds(unittest.TestCase):
    """Dropping a track that's already in a SoundBox playlist asks Add
    Anyway/Skip — duplicates happen on purpose or not at all."""

    def setUp(self):
        _win.custom["DupTest"] = ["1"]
        self._asked = []

    def tearDown(self):
        _win.custom.pop("DupTest", None)
        _win._refresh_custom()
        _win._save_state()

    def _stub(self, answer):
        def fake(name, dupes):
            self._asked.append((name, list(dupes)))
            return answer
        _win._confirm_duplicate_add = fake
        self.addCleanup(lambda: delattr(_win, "_confirm_duplicate_add"))

    def test_fresh_tracks_add_without_asking(self):
        self._stub(answer=False)
        _win._on_tracks_dropped("DupTest", ["2", "3"])
        self.assertEqual(_win.custom["DupTest"], ["1", "2", "3"])
        self.assertEqual(self._asked, [])          # no dialog for clean drops

    def test_skip_keeps_playlist_unchanged(self):
        self._stub(answer=False)
        _win._on_tracks_dropped("DupTest", ["1", "2"])
        self.assertEqual(_win.custom["DupTest"], ["1", "2"])  # dupe skipped
        self.assertEqual(self._asked, [("DupTest", ["1"])])

    def test_add_anyway_duplicates_on_purpose(self):
        self._stub(answer=True)
        _win._on_tracks_dropped("DupTest", ["1"])
        self.assertEqual(_win.custom["DupTest"], ["1", "1"])

    def test_track_label_uses_effective_title(self):
        self.assertEqual(_win._track_label("1"), "Bravo")
        self.assertEqual(_win._track_label("no-such"), "no-such")


class SearchFocusPolicy(unittest.TestCase):
    """The search box must never grab keyboard focus at launch — the app opens
    on Collection ready to browse (transport keys, arrows), not typing into search."""

    def test_search_is_click_to_focus_only(self):
        self.assertEqual(_win.search.focusPolicy(), Qt.ClickFocus)


class PlaylistReorder(unittest.TestCase):
    """Drag-to-reorder SoundBox playlists: _move_custom rebuilds the dict in the
    new order; the tree clamps drops below the pinned Collection/Artists rows."""

    def setUp(self):
        self._saved = dict(_win.custom)
        _win.custom = {"A": ["1"], "B": ["2"], "C": ["3"]}
        _win._refresh_custom()

    def tearDown(self):
        _win.custom = self._saved
        _win._refresh_custom()
        _win._save_state()

    def test_move_to_front(self):
        _win._move_custom("C", 0)
        self.assertEqual(list(_win.custom), ["C", "A", "B"])
        self.assertEqual(_win.custom["C"], ["3"])  # tracks ride along

    def test_move_down_adjusts_for_own_row(self):
        # Insert line below B = raw index 2; A's own row above it must not
        # double-count, so A lands between B and C.
        _win._move_custom("A", 2)
        self.assertEqual(list(_win.custom), ["B", "A", "C"])

    def test_move_to_end(self):
        _win._move_custom("A", 3)
        self.assertEqual(list(_win.custom), ["B", "C", "A"])

    def test_noop_and_unknown_are_safe(self):
        _win._move_custom("A", 0)
        self.assertEqual(list(_win.custom), ["A", "B", "C"])
        _win._move_custom("nope", 1)
        self.assertEqual(list(_win.custom), ["A", "B", "C"])

    def test_tree_rows_follow_new_order(self):
        from desktop.trees import CUSTOM_ROLE
        _win._move_custom("B", 0)
        first = _win.sb_tree._first_custom_row()
        names = [_win.sb_tree.topLevelItem(i).data(0, CUSTOM_ROLE)
                 for i in range(first, _win.sb_tree.topLevelItemCount())
                 if _win.sb_tree.topLevelItem(i).data(0,CUSTOM_ROLE)]
        self.assertEqual(names, ["B", "A", "C"])

    def test_drop_row_clamps_below_pinned(self):
        from PySide6.QtCore import QPoint
        tree = _win.sb_tree
        first = tree._first_custom_row()
        self.assertEqual(first, 2)  # Collection + Artists pinned on top
        # A drop at the very top (over the pinned Collection row) clamps to the
        # first custom row instead of landing above the built-ins.
        top = tree.visualItemRect(tree.topLevelItem(0)).top() + 1
        self.assertEqual(tree._drop_row(QPoint(5, top)), first)
        # Below the last row → end of the list.
        self.assertEqual(tree._drop_row(QPoint(5, 10_000)),
                         tree.topLevelItemCount())


class ImportedFiles(unittest.TestCase):
    """Livesets imported straight into SoundBox: they join the pool (so
    Collection shows them automatically), can land in a SoundBox playlist on
    drop, dedup by path, and persist in library.json's 'imported' section."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.f1 = Path(self._td.name) / "Rezz Live @ Red Rocks 2026.mp3"
        self.f2 = Path(self._td.name) / "Subtronics EDC Set.wav"
        self.f1.write_bytes(b"x")  # existence + extension is what import checks
        self.f2.write_bytes(b"x")
        self._saved_imported = dict(_win.imported)
        self._saved_custom = dict(_win.custom)
        self._saved_waveform_rechecks = _win._waveform_rechecks_enabled
        _win._waveform_rechecks_enabled = False
        _win._waveform_recheck_timer.stop()
        _win.imported = {}

    def tearDown(self):
        for tid in list(_win.imported):
            _win.lib.tracks_by_id.pop(tid, None)
        _win.imported = self._saved_imported
        _win.custom = self._saved_custom
        _win._refresh_custom()
        _win._reload_current_view()  # later classes must not see imported rows
        _win._save_state()
        _win._waveform_recheck_timer.stop()
        _win._waveform_rechecks_enabled = self._saved_waveform_rechecks
        if self._saved_waveform_rechecks:
            _win._schedule_waveform_recheck()
        self._td.cleanup()

    def test_import_joins_collection(self):
        _win._import_files([str(self.f1), str(self.f2)])
        self.assertEqual(len(_win.imported), 2)
        names = [t.name for t in _win._collection_tracks()]
        self.assertIn("Rezz Live @ Red Rocks 2026", names)
        self.assertIn("Subtronics EDC Set", names)
        for tid in _win.imported:
            self.assertTrue(tid.startswith("sb-"))
            self.assertIn(tid, _win.lib.tracks_by_id)
        self.assertTrue(_win._waveform_rechecks_enabled)
        self.assertTrue(_win._waveform_recheck_timer.isActive())

    def test_reimport_reuses_id(self):
        _win._import_files([str(self.f1)])
        first = set(_win.imported)
        _win._import_files([str(self.f1)])
        self.assertEqual(set(_win.imported), first)  # same path → same id

    def test_import_into_playlist_also_hits_collection(self):
        _win.custom["LIVESETS"] = []
        _win._import_files([str(self.f1)], "LIVESETS")
        (tid,) = _win.imported
        self.assertEqual(_win.custom["LIVESETS"], [tid])
        self.assertIn(tid, [t.track_id for t in _win._collection_tracks()])

    def test_non_audio_skipped_audio_kept(self):
        from pathlib import Path
        junk = Path(self._td.name) / "cover.txt"
        junk.write_text("not audio")
        _win._import_files([str(junk), str(self.f1)])
        self.assertEqual(len(_win.imported), 1)  # only the mp3 made it

    def test_survives_a_library_reload(self):
        from soundbox import imports
        _win._import_files([str(self.f1)])
        fresh = _make_lib()  # what a rekordbox refresh hands back
        imports.inject(fresh, _win.imported)
        for tid in _win.imported:
            self.assertIn(tid, fresh.tracks_by_id)

    def test_persisted_in_library_json(self):
        import json
        _win._import_files([str(self.f1)])
        data = json.loads(_win._data_file.read_text(encoding="utf-8"))
        self.assertEqual(data["imported"], _win.imported)


class FixLinksUI(unittest.TestCase):
    """_apply_relinks re-points moved imported files and refreshes the pool."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        # Admission now records canonical paths. macOS exposes its TEMP root
        # through /var as well as /private/var; repair expectations must bind
        # the admitted path rather than a different spelling of that root.
        base = Path(self._td.name).resolve()
        self.old = base / "Mersiv Sound Haven 2021.m4a"
        self.old.write_bytes(b"x")
        self._saved = dict(_win.imported)
        self._saved_waveform_rechecks = _win._waveform_rechecks_enabled
        _win._waveform_rechecks_enabled = False
        _win._waveform_recheck_timer.stop()
        _win.imported = {}
        _win._import_files([str(self.old)])
        (self.tid,) = _win.imported
        self.new = base / "pt.1" / self.old.name
        self.new.parent.mkdir()
        self.old.rename(self.new)  # the user's "move everything into a folder"

    def tearDown(self):
        for tid in list(_win.imported):
            _win.lib.tracks_by_id.pop(tid, None)
        _win.imported = self._saved
        _win._refresh_custom()
        _win._reload_current_view()
        _win._save_state()
        _win._waveform_recheck_timer.stop()
        _win._waveform_rechecks_enabled = self._saved_waveform_rechecks
        if self._saved_waveform_rechecks:
            _win._schedule_waveform_recheck()
        self._td.cleanup()

    def test_missing_then_relink_heals_library(self):
        from soundbox import imports
        self.assertEqual(set(imports.missing(_win.imported)), {self.tid})
        found = imports.find_moved(imports.missing(_win.imported),
                                   imports.search_roots(_win.imported))
        self.assertEqual(found, {self.tid: self.new})
        _win._apply_relinks(found)
        self.assertEqual(imports.missing(_win.imported), {})
        self.assertEqual(_win.lib.tracks_by_id[self.tid].path, self.new)
        self.assertTrue(_win._waveform_rechecks_enabled)

    def test_startup_identity_repair_keeps_stable_id_and_curation(self):
        from soundbox.file_locations import LocationRepair, LocationUpdate
        try:
            _win.custom["LIVESETS"] = [self.tid]
            _win.titles[self.tid] = "Curated Set Title"
            result = LocationRepair(
                updates={self.tid: LocationUpdate(
                    expected_path=str(self.old), path=self.new,
                    bookmark="new opaque locator")},
                missing_count=1, unresolved_count=0)
            with mock.patch("desktop.app.QTimer.singleShot"):
                _win._on_import_locations_repaired(result)
            self.assertEqual(_win.imported[self.tid]["path"], str(self.new))
            self.assertEqual(_win.lib.tracks_by_id[self.tid].path, self.new)
            self.assertEqual(_win.custom["LIVESETS"], [self.tid])
            self.assertEqual(_win.titles[self.tid], "Curated Set Title")
        finally:
            _win.custom.pop("LIVESETS", None)
            _win.titles.pop(self.tid, None)

    def test_fix_links_noop_when_nothing_missing(self):
        _win._apply_relinks({})
        _win.imported[self.tid]["path"] = str(self.new)  # already healthy
        _win._fix_links()  # must not start a worker or pop a dialog
        self.assertEqual(len(getattr(_win, "_oneshots", [])), 0)


class ExportableViews(unittest.TestCase):
    """Collection and Artists are draggable playlists: whole pool, view order."""

    def test_collection_view_playlist_in_collection_order(self):
        pl = _win._view_playlist("collection")
        self.assertEqual(pl.name, "Collection")
        self.assertEqual(pl.keys, ["2", "1", "3"])  # no files → title A→Z fallback

    def test_artists_view_playlist_artist_order(self):
        pl = _win._view_playlist("artists")
        self.assertEqual(pl.name, "Artists")
        self.assertEqual(pl.keys, ["2", "3", "1"])  # Ann, Mia, Zed A→Z

    def test_resolve_jobs_handles_sbview(self):
        pls = _win._resolve_jobs([("sbview", "collection"), ("sbview", "artists"),
                                  ("sbview", "nope")])
        self.assertEqual([p.name for p in pls], ["Collection", "Artists"])

    def test_sync_playlists_includes_views(self):
        _win._sync_playlists_cache = None
        names = [p.name for p in _win._sync_playlists()]
        self.assertIn("Collection", names)
        self.assertIn("Artists", names)

    def test_sync_playlists_are_not_rebuilt_by_each_device_poll(self):
        _win._sync_playlists_cache = None
        first = _win._sync_playlists()
        real = _win._view_playlist
        _win._view_playlist = lambda _name: self.fail("rebuilt cached views")
        try:
            self.assertIs(_win._sync_playlists(), first)
        finally:
            _win._view_playlist = real

    def test_tree_drag_emits_sbview_lines(self):
        md = _win.sb_tree.mimeData([_win.sb_tree.topLevelItem(0),
                                    _win.sb_tree.topLevelItem(1)])
        self.assertEqual(md.text(), "sbview\tcollection\nsbview\tartists")

    def test_reserved_names_guard(self):
        self.assertTrue(_win._custom_name_taken("Collection"))
        self.assertTrue(_win._custom_name_taken("artists"))
        self.assertFalse(_win._custom_name_taken("LIVESETS"))


class SidebarSplit(unittest.TestCase):
    """SOUNDBOX/DEVICES header bars are splitter handles: drag to resize, sizes
    persist with the column layout."""

    def test_unified_playlists_and_devices_header_bar(self):
        from PySide6.QtWidgets import QLabel
        s = _win._sidebar_split
        self.assertEqual(s.count(), 2)
        self.assertIs(_win.tree,_win.sb_tree)
        h1 = " ".join(x.text() for x in s.handle(1).findChildren(QLabel))
        self.assertIn("DEVICES", h1)

    def test_sizes_persist_roundtrip(self):
        _win.resize(1100, 800)
        _app.processEvents()
        s = _win._sidebar_split
        s.setSizes([500, 120])
        _win._save_columns()
        saved = list(_win.settings["sidebar_split"])
        self.assertEqual(len(saved), 2)
        s.setSizes([saved[0] + 80, max(48, saved[1] - 80)])
        _win._restore_columns()
        self.assertEqual(s.sizes(), saved)

    def test_bad_saved_value_falls_back(self):
        _win.settings["sidebar_split"] = "garbage"
        _win._restore_columns()  # must not raise; classic proportions apply
        _win.settings.pop("sidebar_split", None)

    def test_devices_no_longer_fixed_height(self):
        self.assertGreater(_win.device_tree.maximumHeight(), 96)
        self.assertGreaterEqual(_win.device_tree.minimumHeight(), 48)

    def test_window_and_panes_restore_as_one_layout(self):
        original_geometry = _win.saveGeometry()
        original_setting = _win.settings.get("window_geometry")
        original_rect = _win.settings.get("window_rect")
        original_split = list(_win._sidebar_split.sizes())
        try:
            # Stay inside the offscreen platform's synthetic 800x800 display;
            # restoreGeometry intentionally clamps oversized windows.
            _win.resize(760, 700)
            _app.processEvents()
            saved_size = (_win.width(), _win.height())
            _win._sidebar_split.setSizes([540, 96])
            _win._save_columns()
            saved_geometry = _win.settings["window_geometry"]
            saved_rect = list(_win.settings["window_rect"])
            saved_split = list(_win.settings["sidebar_split"])

            _win.resize(900, 650)
            _win._sidebar_split.setSizes([200, 100])
            _win.settings["window_geometry"] = saved_geometry
            _win.settings["window_rect"] = saved_rect
            _win.settings["sidebar_split"] = saved_split
            _win._restore_window_geometry()
            _win._restore_columns()
            _app.processEvents()

            self.assertEqual((_win.width(), _win.height()), saved_size)
            self.assertEqual(_win._sidebar_split.sizes(), saved_split)
        finally:
            _win.restoreGeometry(original_geometry)
            _win._sidebar_split.setSizes(original_split)
            if original_setting is None:
                _win.settings.pop("window_geometry", None)
            else:
                _win.settings["window_geometry"] = original_setting
            if original_rect is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original_rect

    def test_corrupt_window_geometry_is_ignored(self):
        original = _win.settings.get("window_geometry")
        original_rect = _win.settings.get("window_rect")
        before = _win.size()
        try:
            _win.settings["window_rect"] = ["nope", 1, 2, 3]
            _win.settings["window_geometry"] = "not base64 geometry"
            _win._restore_window_geometry()  # must not raise or resize
            self.assertEqual(_win.size(), before)
        finally:
            if original is None:
                _win.settings.pop("window_geometry", None)
            else:
                _win.settings["window_geometry"] = original
            if original_rect is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original_rect

    def test_offscreen_window_rect_clamps_to_current_display(self):
        original = _win.settings.get("window_rect")
        try:
            _win.settings["window_rect"] = [99999, 99999, 900, 650]
            _win._restore_window_geometry()
            available = _app.primaryScreen().availableGeometry()
            self.assertTrue(available.contains(_win.frameGeometry().center()))
        finally:
            if original is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original


class ArtFetchHonesty(unittest.TestCase):
    def test_provider_failure_never_persists_a_miss(self):
        from types import SimpleNamespace
        from desktop.workers import ArtFillWorker
        from soundbox.artwork import ArtCache
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            source = root / 'track.wav'; source.write_bytes(b'fixture')
            cache = ArtCache(root / 'art')
            lib = Library({'1': Track('1', 'Title', 'Artist', source.as_uri())}, [], 'fixture')
            provider = SimpleNamespace(fetch_best=mock.Mock(side_effect=RuntimeError('dependency failed')))
            worker = ArtFillWorker(lib, cache, {}, {}, provider=provider)
            worker.DELAY = 0
            worker.run()
            self.assertEqual(cache.index, {})

    """A broken fetch must say 'failed', never masquerade as 'no art found'."""

    def test_oneshot_passes_exception_through(self):
        from desktop.workers import _OneShotArt
        got = []
        def boom():
            raise RuntimeError("net down")
        w = _OneShotArt("1", boom)
        w.done.connect(lambda tid, res: got.append((tid, res)))
        w.run()  # call synchronously — we're testing the logic, not the thread
        self.assertEqual(got[0][0], "1")
        self.assertIsInstance(got[0][1], RuntimeError)

    def test_on_oneshot_distinguishes_failure_from_no_art(self):
        stored = []
        real = _win.art_cache
        class Cache(_FakeArt):
            def store_ok(self, tid, res):
                stored.append(tid)
        _win.art_cache = Cache({})
        try:
            _win._on_oneshot("1", RuntimeError("boom"))  # failure → no store, honest flash
            self.assertEqual(stored, [])
            self.assertIn("failed", _win.status_label.text().lower())
            _win._on_oneshot("1", None)                  # genuine miss keeps old message
            self.assertIn("No art found", _win.status_label.text())
        finally:
            _win.art_cache = real


class WaveformFill(unittest.TestCase):
    def test_complete_receipt_survives_half_hour_without_opening_record(self):
        from soundbox import track_analysis as ta
        from desktop.workers import WaveformFillWorker
        canonical = Path(self.cache) / 'complete-receipt'
        source = Path(self.paths[0])
        record = ta.store(canonical, '0', source, ta.analyze_soundfile(source))
        track = Track('0', 'Test', 'Artist', source.as_uri(), bpm='140.00')
        lib = Library({'0': track}, [], source='synthetic')
        with mock.patch('desktop.workers.time.time', return_value=1800):
            WaveformFillWorker(lib, canonical).run()
        original = Path.open
        def opening(path, *args, **kwargs):
            if path in (record, source):
                raise AssertionError('Unchanged completed record/audio reopened')
            return original(path, *args, **kwargs)
        with mock.patch('desktop.workers.time.time', return_value=3600), \
                mock.patch.object(Path, 'open', opening):
            WaveformFillWorker(lib, canonical).run()

    """Canonical V5 preparation is resumable, safe, and metadata-aware."""

    @classmethod
    def setUpClass(cls):
        import math, struct, tempfile, wave
        cls.dir = tempfile.mkdtemp(prefix="sb-wffill-")
        cls.paths = []
        sr = 8000
        for i in range(3):
            p = f"{cls.dir}/set{i}.wav"
            with wave.open(p, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(b"".join(
                    struct.pack("<h", int(0.6 * 32767 * math.sin(2*math.pi*220*t/sr)))
                    for t in range(sr)))  # 1s tone
            cls.paths.append(p)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        import tempfile
        from pathlib import Path
        from soundbox.library import Library, Track
        self.cache = tempfile.mkdtemp(prefix="sb-wfcache-")
        tracks = {str(i): Track(str(i), f"Set {i}", "A",
                                Path(p).as_uri(), bpm="140.00")
                  for i, p in enumerate(self.paths)}
        tracks["ghost"] = Track("ghost", "Gone", "A",
                                Path(f"{self.dir}/nope.wav").as_uri())
        self.lib = Library(tracks_by_id=tracks, playlists=[], source="x")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.cache, ignore_errors=True)

    def test_complete_import_prepares_without_fallback_analysis(self):
        from soundbox import track_analysis as ta
        from soundbox.waveform_detail import DETAIL_MODEL
        from desktop.workers import WaveformFillWorker
        source = Path(self.paths[0])
        track = Track("0", "Test", "Artist", source.as_uri(),
                      source_analysis_path=self.paths[1])
        lib = Library({"0": track}, [], source="synthetic")
        imported = {"sample_rate": 150, "beats": [[1, 0, 120]],
                    "_blue_detail": bytes([31]) * 150,
                    "_rgb_detail": bytes.fromhex("e47c") * 150,
                    "_detail_bands": bytes([30, 40, 50]) * 150}
        canonical = Path(self.cache) / "analysis"
        with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
              mock.patch("soundbox.track_analysis.analyze_soundfile") as fallback,
              mock.patch("desktop.player.qt_detail_peaks") as qt):
            WaveformFillWorker(lib, canonical).run()
        fallback.assert_not_called()
        qt.assert_not_called()
        self.assertTrue(ta.is_ready(canonical, "0", source))
        self.assertEqual(ta.load_manifest(canonical, "0", source)["waveform_model"], DETAIL_MODEL)

    def _old_complete_cache(self):
        from soundbox import track_analysis as ta
        source = Path(self.paths[0])
        dat = Path(self.cache) / "ANLZ.DAT"
        dat.write_bytes(b"synthetic read-only analysis")
        imported = {"sample_rate": 150, "beats": [[1, 0, 120]],
                    "_blue_detail": bytes([31]) * 150,
                    "_rgb_detail": bytes.fromhex("e47c") * 150,
                    "_detail_bands": bytes([30, 40, 50]) * 150}
        cues = [{"kind": "hot", "slot": 0, "position_ms": 500,
                 "end_ms": -1, "type": "single", "label": "KEEP",
                 "color": [255, 59, 125]}]
        old = ta.with_imported_cues(
            ta.merge_import(ta.analyze_soundfile(source), imported), cues)
        canonical = Path(self.cache) / "analysis"
        record = ta.store(canonical, "0", source, old)
        track = Track("0", "Test", "Artist", source.as_uri(),
                      source_analysis_path=str(dat))
        return canonical, Library({"0": track}, [], source="synthetic"), imported, record

    def test_background_upgrades_signed_mip_without_selection_or_repeat_decode(self):
        from desktop.workers import WaveformFillWorker
        from desktop.player import WaveformWorker
        from soundbox import track_analysis as ta
        from soundbox.waveform_detail import DETAIL_MODEL
        canonical, lib, imported, record = self._old_complete_cache()
        source = Path(self.paths[0])
        before_source = source.read_bytes()
        before = ta.load_manifest(canonical, "0", source)
        self.assertNotEqual(before["waveform_model"], DETAIL_MODEL)
        totals, refreshed = [], []
        worker = WaveformFillWorker(lib, canonical)
        worker.tasks_ready.connect(totals.append)
        worker.detail_ready.connect(refreshed.append)
        with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
              mock.patch("soundbox.track_analysis.analyze_soundfile") as fallback,
              mock.patch("desktop.player.qt_detail_peaks") as qt):
            worker.run()
        self.assertEqual(totals, [1])
        self.assertEqual(refreshed, [str(source)])
        fallback.assert_not_called(); qt.assert_not_called()
        self.assertTrue(ta.verify_complete(canonical, "0", source))
        after = ta.load_manifest(canonical, "0", source)
        self.assertEqual(after["waveform_model"], DETAIL_MODEL)
        self.assertEqual(after["cues"], before["cues"])
        self.assertEqual(after["cue_import"], before["cue_import"])
        self.assertEqual(after["beats"], before["beats"])
        data = ta.load_detail(canonical, "0", source)
        for key in ("_blue_detail", "_rgb_detail", "_detail_bands"):
            self.assertEqual(data[key], imported[key])
        saved = record.read_bytes()
        with mock.patch("soundbox.waveform_detail.analyze_imported") as decode:
            WaveformFillWorker(lib, canonical).run()
            WaveformFillWorker(lib, canonical).run()
            WaveformWorker(str(source), track_id="0",
                           track_analysis_dir=canonical).run()
        decode.assert_not_called()
        self.assertEqual(record.read_bytes(), saved)
        self.assertEqual(source.read_bytes(), before_source)

    def test_background_compact_failure_keeps_cache_and_never_runs_full_analyzer(self):
        from desktop.workers import WaveformFillWorker
        canonical, lib, imported, record = self._old_complete_cache()
        before = record.read_bytes()
        for result in (None, {}):
            worker = WaveformFillWorker(lib, canonical)
            with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
                  mock.patch("soundbox.waveform_detail.analyze_imported", return_value=result),
                  mock.patch("soundbox.track_analysis.analyze_soundfile") as fallback):
                worker.run()
            fallback.assert_not_called()
            self.assertEqual(record.read_bytes(), before)

    def test_background_compact_defers_cloud_and_rechecks_after_wait(self):
        from desktop.workers import WaveformFillWorker
        canonical, lib, imported, record = self._old_complete_cache()
        before = record.read_bytes()
        for flags in ([True], [False, True]):
            (canonical / "preparation-index.json").unlink(missing_ok=True)
            worker = WaveformFillWorker(lib, canonical)
            with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
                  mock.patch("soundbox.rekordbox_analysis.read_beats", return_value=imported["beats"]),
                  mock.patch("soundbox.track_analysis.is_dataless_source", side_effect=flags),
                  mock.patch("soundbox.waveform_detail.analyze_imported") as decode):
                worker.run()
            decode.assert_not_called()
            self.assertEqual(record.read_bytes(), before)

    def test_background_compact_preserves_manual_metadata_and_uses_migration_reserve(self):
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis as ta
        canonical, lib, imported, record = self._old_complete_cache()
        self.assertTrue(ta.update_user_data(
            canonical, "0", self.paths[0], beats=[[1, 250, 128.0]]))
        before = ta.load_manifest(canonical, "0", self.paths[0])
        worker = WaveformFillWorker(lib, canonical)
        with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
              mock.patch.object(worker, "isRunning", return_value=True),
              mock.patch.object(worker, "_refresh_power_policy"),
              mock.patch.object(worker, "_wait_for_storage", return_value=True) as storage):
            worker.run()
        storage.assert_called_once_with(reclaiming=True)
        after = ta.load_manifest(canonical, "0", self.paths[0])
        self.assertEqual(after["provenance"]["user_data"], "soundbox")
        self.assertEqual(after["beats"], before["beats"])
        self.assertEqual(after["cues"], before["cues"])

    def test_background_compact_cancellation_keeps_old_record(self):
        from desktop.workers import WaveformFillWorker
        from soundbox.waveform_detail import analyze_imported
        canonical, lib, imported, record = self._old_complete_cache()
        before = record.read_bytes()
        prepared = analyze_imported(self.paths[0], imported)
        worker = WaveformFillWorker(lib, canonical)
        cancelled = False
        def cancel_after_decode(*args):
            nonlocal cancelled
            cancelled = True
            return prepared
        with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
              mock.patch("soundbox.waveform_detail.analyze_imported", side_effect=cancel_after_decode),
              mock.patch.object(worker, "isInterruptionRequested", side_effect=lambda: cancelled)):
            worker.run()
        self.assertTrue(cancelled)
        self.assertEqual(record.read_bytes(), before)

    def test_compact_completed_during_queue_wait_is_not_decoded_again(self):
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis as ta
        from soundbox.waveform_detail import analyze_imported
        canonical, lib, imported, record = self._old_complete_cache()
        prepared = analyze_imported(self.paths[0], imported)
        worker = WaveformFillWorker(lib, canonical)
        def finish_foreground():
            ta.store(canonical, "0", self.paths[0], prepared)
        with (mock.patch("soundbox.rekordbox_analysis.read", return_value=imported),
              mock.patch.object(worker, "_wait_for_playback", side_effect=finish_foreground),
              mock.patch("soundbox.waveform_detail.analyze_imported") as decode):
            worker.run()
        decode.assert_not_called()
        self.assertTrue(ta.verify_complete(canonical, "0", self.paths[0]))

    def test_discovery_receipts_reuse_headers_and_notice_late_sidecars(self):
        from soundbox import track_analysis as ta
        from desktop.workers import WaveformFillWorker
        canonical = Path(self.cache) / "analysis"
        source = Path(self.paths[0])
        record = ta.store(canonical, "0", source, ta.analyze_soundfile(source))
        dat = Path(self.cache) / "ANLZ.DAT"
        dat.write_bytes(b"PMAI" + bytes(28))
        track = Track("0", "Test", "Artist", source.as_uri(), source_analysis_path=str(dat))
        lib = Library({"0": track}, [], source="synthetic")
        opened = []
        original = Path.open

        def opening(path, *args, **kwargs):
            if path == record or path == source or path == dat:
                opened.append(path)
            return original(path, *args, **kwargs)

        with mock.patch("soundbox.rekordbox_analysis.read", return_value={}) as anlz, \
                mock.patch("soundbox.rekordbox_analysis.read_beats", return_value=None) as beats, \
                mock.patch.object(Path, "open", opening), \
                mock.patch("desktop.workers.time.time", return_value=1800):
            WaveformFillWorker(lib, canonical).run()
            self.assertEqual(opened, [record])  # readiness/grid/cues/fallback share it
            opened.clear()
            anlz.reset_mock(); beats.reset_mock()
            WaveformFillWorker(lib, canonical).run()  # a new worker loads the receipt
            self.assertEqual(opened, [])
            anlz.assert_not_called(); beats.assert_not_called()
            dat.with_suffix(".EXT").write_bytes(b"late waveform")
            WaveformFillWorker(lib, canonical).run()
            self.assertEqual(opened, [record])
            anlz.assert_called_once(); beats.assert_called_once()
            opened.clear()
            with mock.patch("desktop.workers.time.time", return_value=3600):
                WaveformFillWorker(lib, canonical).run()
            self.assertEqual(opened, [record])  # bounded fallback retries parse failures

    def test_receipts_do_not_suppress_cue_edits_or_forced_reanalysis(self):
        from soundbox import track_analysis as ta
        from desktop.workers import WaveformFillWorker
        canonical = Path(self.cache) / "analysis"
        source = Path(self.paths[0])
        ta.store(canonical, "0", source, ta.analyze_soundfile(source))
        track = Track("0", "Test", "Artist", source.as_uri())
        lib = Library({"0": track}, [], source="synthetic")
        WaveformFillWorker(lib, canonical).run()
        track.source_cues = []
        worker = WaveformFillWorker(lib, canonical)
        tasks = []
        worker.tasks_ready.connect(tasks.append)
        worker.run()
        self.assertEqual(tasks, [1])
        self.assertFalse(ta.imported_cues_need_sync(canonical, "0", source, []))
        WaveformFillWorker(lib, canonical).run()
        forced = WaveformFillWorker(lib, canonical, force_track_ids=("0",))
        forced.tasks_ready.connect(tasks.append)
        forced.run()
        self.assertEqual(tasks, [1, 1])
        self.assertEqual(forced.completed_forced_track_ids, {"0"})

    def test_loaded_track_bypasses_bulk_playback_and_low_power_deferral(self):
        from desktop.workers import WaveformFillWorker

        worker = WaveformFillWorker(self.lib, Path(self.cache) / "track_analysis")
        worker.prioritize(self.paths[1])
        worker._active_task_path = self.paths[1]
        worker._playback_active.set()
        worker._power_policy = "low-power"
        worker.defer_for_interaction(60)
        self.assertTrue(worker._analysis_checkpoint())

    def test_prepares_canonical_v5_analysis_and_never_writes_audio(self):
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.track_analysis import load

        canonical = Path(self.cache) / "track_analysis"
        before = Path(self.paths[0]).read_bytes()
        WaveformFillWorker(self.lib, canonical).run()
        got = load(canonical, "0", self.paths[0])
        self.assertIsNotNone(got)
        self.assertEqual(
            got["_audio_detail"]["mip_divisors"],
            list(track_analysis.RANGE_MIP_DIVISORS))
        self.assertGreater(
            sum(got["_audio_detail"]["mip_counts"]), 500)
        self.assertEqual(Path(self.paths[0]).read_bytes(), before)

    def test_timestamp_only_drift_is_adopted_without_audio_recook(self):
        import os
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        WaveformFillWorker(self.lib, canonical).run()
        source = Path(self.paths[0])
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        with mock.patch(
                "soundbox.track_analysis.analyze_soundfile") as analyze:
            WaveformFillWorker(self.lib, canonical).run()
        analyze.assert_not_called()
        self.assertTrue(track_analysis.is_ready(
            canonical, "0", source))

    def test_dataless_cache_miss_is_not_background_hydrated(self):
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox.library import Library

        track = self.lib.tracks_by_id["0"]
        small_lib = Library(
            tracks_by_id={"0": track}, playlists=[], source="synthetic")
        canonical = Path(self.cache) / "dataless-analysis"
        totals = []
        worker = WaveformFillWorker(small_lib, canonical)
        worker.tasks_ready.connect(totals.append)
        with (
                mock.patch(
                    "soundbox.track_analysis.is_dataless_source",
                    return_value=True),
                mock.patch(
                    "soundbox.track_analysis.adopt_source_identity") as adopt,
                mock.patch(
                    "soundbox.track_analysis.analyze_soundfile") as analyze):
            worker.run()
        self.assertEqual(totals, [0])
        adopt.assert_not_called()
        analyze.assert_not_called()

    def test_ready_dataless_track_defers_late_waveforms_but_keeps_cue_updates(self):
        from desktop.workers import WaveformFillWorker
        track = self.lib.tracks_by_id["0"]
        track.source_analysis_path = self.paths[1]
        lib = Library(tracks_by_id={"0": track}, playlists=[], source="test")
        worker = WaveformFillWorker(lib, Path(self.cache) / "analysis")
        with (
            mock.patch("soundbox.track_analysis.is_ready", return_value=True),
            mock.patch("soundbox.track_analysis.is_dataless_source", return_value=True),
            mock.patch("soundbox.track_analysis.fallback_waveform_modes", return_value=("rgb",)),
            mock.patch("soundbox.track_analysis.imported_waveforms_need_sync", return_value=True),
            mock.patch("soundbox.rekordbox_analysis.read_beats", return_value=None),
            mock.patch("soundbox.track_analysis.grid_needs_sync", return_value=False),
            mock.patch("soundbox.track_analysis.imported_cues_need_sync", return_value=True),
            mock.patch("soundbox.track_analysis.update_imported_cues", return_value=True) as cues,
            mock.patch("soundbox.track_analysis.analyze_soundfile") as decode,
        ):
            worker.run()
        cues.assert_called_once()
        decode.assert_not_called()

    def test_background_decode_rechecks_placeholder_after_wait(self):
        from desktop.workers import WaveformFillWorker
        track = self.lib.tracks_by_id["0"]
        lib = Library(tracks_by_id={"0": track}, playlists=[], source="test")
        worker = WaveformFillWorker(lib, Path(self.cache) / "analysis")
        with (
            mock.patch("soundbox.track_analysis.is_dataless_source", side_effect=[False, True]),
            mock.patch("soundbox.track_analysis.analyze_soundfile") as decode,
            mock.patch("desktop.player.qt_detail_peaks") as fallback,
        ):
            worker.run()
        decode.assert_not_called()
        fallback.assert_not_called()

    def test_forced_recook_can_deliberately_open_placeholder(self):
        from desktop.workers import WaveformFillWorker
        track = self.lib.tracks_by_id["0"]
        lib = Library(tracks_by_id={"0": track}, playlists=[], source="test")
        worker = WaveformFillWorker(
            lib, Path(self.cache) / "analysis", force_track_ids={"0"})
        with (
            mock.patch("soundbox.track_analysis.is_dataless_source", return_value=True),
            mock.patch("soundbox.track_analysis.analyze_soundfile", return_value=None) as decode,
            mock.patch("desktop.player.qt_detail_peaks", return_value=None),
        ):
            worker.run()
        decode.assert_called_once()

    def test_late_source_rewrite_is_detected_and_recooked(self):
        import math
        import struct
        import tempfile
        import wave
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.library import Library, Track

        with tempfile.TemporaryDirectory(prefix="sb-wf-rewrite-") as td:
            source = Path(td) / "new-track.wav"

            def write_tone(seconds):
                rate = 8000
                with wave.open(str(source), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(b"".join(
                        struct.pack(
                            "<h", int(0.5 * 32767 * math.sin(
                                2 * math.pi * 220 * frame / rate)))
                        for frame in range(rate * seconds)))

            write_tone(1)
            lib = Library(
                tracks_by_id={
                    "new": Track(
                        "new", "New", "A", source.as_uri(), bpm="140.00")},
                playlists=[], source="synthetic")
            canonical = Path(td) / "analysis"
            WaveformFillWorker(lib, canonical).run()
            self.assertTrue(track_analysis.is_ready(
                canonical, "new", source))

            # Rekordbox/tag tools may finish rewriting a newly landed container
            # after SoundBox's first cook. Its identity must become stale.
            write_tone(2)
            self.assertFalse(track_analysis.is_ready(
                canonical, "new", source))
            totals = []
            worker = WaveformFillWorker(lib, canonical)
            worker.tasks_ready.connect(totals.append)
            worker.run()
            self.assertEqual(totals, [1])
            self.assertTrue(track_analysis.is_ready(
                canonical, "new", source))
            self.assertTrue(track_analysis.verify_complete(
                canonical, "new", source))

    def test_forced_reanalysis_reimports_late_rekordbox_waveforms(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.library import Library

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        small_lib = Library(
            tracks_by_id={"0": track}, playlists=[], source="synthetic")
        WaveformFillWorker(small_lib, canonical).run()
        before_audio = Path(self.paths[0]).read_bytes()
        before = track_analysis.load_manifest(
            canonical, "0", self.paths[0])
        self.assertEqual(
            before["stream_provenance"]["rgb"], "soundbox-analysis")

        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only late-analysis fixture")
        track.source_analysis_path = str(dat)
        imported = {
            "beats": [[1, 100, 140.0]],
            "_blue_detail": b"\x21\x22",
            "_rgb_detail": b"\xe0\x7c\xe1\x7d",
            "_detail_bands": b"\x01\x02\x03\x04\x05\x06",
        }
        worker = WaveformFillWorker(
            small_lib, canonical, force_track_ids={"0"})
        totals = []
        worker.tasks_ready.connect(totals.append)
        with patch("soundbox.rekordbox_analysis.read", return_value=imported):
            worker.run()

        after = track_analysis.load_manifest(
            canonical, "0", self.paths[0])
        self.assertEqual(totals, [1])
        self.assertEqual(worker.completed_forced_track_ids, {"0"})
        self.assertEqual(after["stream_provenance"], {
            "detail": "soundbox-analysis",
            "blue": "rekordbox-anlz-pwv3",
            "rgb": "rekordbox-anlz-pwv5",
            "three_band": "rekordbox-anlz-pwv7",
        })

        quiet_worker = WaveformFillWorker(small_lib, canonical)
        quiet_totals = []
        quiet_worker.tasks_ready.connect(quiet_totals.append)
        # Imported modes alone do not prove compact-detail eligibility. Probe
        # once; this intentionally incomplete fixture then gets a quiet receipt.
        with patch("soundbox.rekordbox_analysis.read", return_value=imported) as read:
            quiet_worker.run()
            read.assert_called_once()
            read.reset_mock()
            WaveformFillWorker(small_lib, canonical).run()
            read.assert_not_called()
        self.assertEqual(quiet_totals, [0])
        self.assertEqual(Path(self.paths[0]).read_bytes(), before_audio)

    def test_recurring_scan_automatically_imports_late_waveforms(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.library import Library

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        small_lib = Library(
            tracks_by_id={"0": track}, playlists=[], source="synthetic")
        WaveformFillWorker(small_lib, canonical).run()
        before = track_analysis.load_manifest(
            canonical, "0", self.paths[0])
        self.assertEqual(
            before["stream_provenance"]["rgb"], "soundbox-analysis")

        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only late-analysis fixture")
        track.source_analysis_path = str(dat)
        imported = {
            "beats": [[1, 100, 140.0]],
            "_blue_detail": b"\x21\x22",
            "_rgb_detail": b"\xe0\x7c\xe1\x7d",
            "_detail_bands": b"\x01\x02\x03\x04\x05\x06",
        }
        worker = WaveformFillWorker(small_lib, canonical)
        totals = []
        worker.tasks_ready.connect(totals.append)
        with patch(
                "soundbox.rekordbox_analysis.read", return_value=imported):
            worker.run()

        after = track_analysis.load_manifest(
            canonical, "0", self.paths[0])
        self.assertEqual(totals, [1])
        self.assertEqual(after["stream_provenance"], {
            "detail": "soundbox-analysis",
            "blue": "rekordbox-anlz-pwv3",
            "rgb": "rekordbox-anlz-pwv5",
            "three_band": "rekordbox-anlz-pwv7",
        })

    def test_failed_forced_reanalysis_keeps_old_playable_record(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.library import Library

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        small_lib = Library(
            tracks_by_id={"0": track}, playlists=[], source="synthetic")
        WaveformFillWorker(small_lib, canonical).run()
        cache_path = track_analysis.cache_path(canonical, "0")
        before = cache_path.read_bytes()
        worker = WaveformFillWorker(
            small_lib, canonical, force_track_ids={"0"})
        with patch(
                "soundbox.track_analysis.analyze_soundfile",
                return_value=None
        ), patch("desktop.player.qt_detail_peaks", return_value=None):
            worker.run()
        self.assertEqual(worker.completed_forced_track_ids, set())
        self.assertEqual(cache_path.read_bytes(), before)

    def test_worker_has_safe_stack_and_metadata_only_cue_refresh(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        track.source_cues = [{
            "kind": "hot", "slot": 0, "position_ms": 500,
            "end_ms": -1, "type": "single", "label": "DROP",
            "color": [255, 59, 125],
        }]
        track_analysis.store(canonical, "0", self.paths[0], {
            "detail_heights": b"\x20\x00" * 6000,
            "detail_rate": 6000.0,
            "overview_heights": b"\x20\x00",
            "overview_rgb": b"\x01\x02\x03",
            "spectral_bands": bytes(range(24)),
            "blue_detail": b"\x21",
            "rgb_detail": b"\xe0\x7c",
            "three_band": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
        })
        before = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        worker = WaveformFillWorker(self.lib, canonical)
        self.assertEqual(worker.stackSize(), worker.STACK_BYTES)
        ready = []
        worker.detail_ready.connect(ready.append)
        with patch(
                "soundbox.track_analysis.analyze_soundfile",
                side_effect=AssertionError("cue refresh decoded audio")):
            worker.run()
        after = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        self.assertEqual(after, before)
        self.assertEqual(ready, [self.paths[0]])
        self.assertEqual(
            track_analysis.load(
                canonical, "0", self.paths[0])["cues"],
            track.source_cues)

    def test_worker_refreshes_corrected_rekordbox_grid_without_audio_decode(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only test fixture")
        track.source_analysis_path = str(dat)
        track.source_cues = []
        automatic = [[1, 250, 128.0], [2, 719, 128.0]]
        prepared = track_analysis.with_imported_cues({
            "detail_heights": b"\x20\x00" * 6000,
            "detail_rate": 6000.0,
            "overview_heights": b"\x20\x00",
            "overview_rgb": b"\x01\x02\x03",
            "spectral_bands": bytes(range(24)),
            "blue_detail": b"\x21",
            "rgb_detail": b"\xe0\x7c",
            "three_band": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
            "beats": automatic,
            "provenance": {
                "analysis": "rekordbox-import+soundbox",
                "grid": "rekordbox",
                "owner": "soundbox",
            },
        }, [])
        track_analysis.store(canonical, "0", self.paths[0], prepared)
        before = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        corrected = [[1, 375, 128.0], [2, 844, 128.0]]
        small_lib = type(self.lib)(
            tracks_by_id={"0": track}, playlists=[], source="x")
        worker = WaveformFillWorker(small_lib, canonical)
        totals = []
        ready = []
        worker.tasks_ready.connect(totals.append)
        worker.detail_ready.connect(ready.append)
        with patch(
                "soundbox.rekordbox_analysis.read_beats",
                return_value=corrected), patch(
                    "soundbox.track_analysis.analyze_soundfile",
                    side_effect=AssertionError("grid refresh decoded audio")):
            worker.run()
        after = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        self.assertEqual(totals, [1])
        self.assertEqual(ready, [self.paths[0]])
        self.assertEqual(after, before)
        self.assertEqual(
            track_analysis.load(
                canonical, "0", self.paths[0])["beats"],
            corrected)

    def test_preparation_status_waits_until_scan_finds_real_work(self):
        from unittest.mock import patch

        saved = _win._preparing_library
        _win._preparing_library = False
        try:
            with patch.object(_win, "_start_pulse") as pulse:
                _win._on_analysis_tasks_ready(0)
                self.assertFalse(_win._preparing_library)
                pulse.assert_not_called()

                _win._on_analysis_tasks_ready(2)
                self.assertTrue(_win._preparing_library)
                pulse.assert_called_once_with("Preparing your library", keep_count=False)
        finally:
            _win._preparing_library = saved

    def test_storage_guard_uses_pause_resume_hysteresis(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker

        canonical = Path(self.cache) / "track_analysis"
        canonical.mkdir()
        worker = WaveformFillWorker(self.lib, canonical)
        states = []
        worker.storage_paused.connect(
            lambda paused, free, resume: states.append((paused, free, resume)))
        free = [
            worker.MIN_FREE_BYTES - 1,
            worker.MIN_FREE_BYTES + 1,
            worker.RESUME_FREE_BYTES,
        ]
        with patch(
                "shutil.disk_usage",
                side_effect=[
                    SimpleNamespace(free=value) for value in free
                ]), patch("time.sleep"):
            self.assertTrue(worker._wait_for_storage())
        self.assertEqual(states, [
            (True, free[0], worker.RESUME_FREE_BYTES),
            (False, free[-1], worker.RESUME_FREE_BYTES)])

    def test_hidden_tracks_are_discarded_and_never_prepared(self):
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        canonical = Path(self.cache) / "track_analysis"
        WaveformFillWorker(self.lib, canonical).run()
        self.assertTrue(track_analysis.is_ready(
            canonical, "1", self.paths[1]))
        self.lib.hidden_ids = {"1"}
        totals = []
        worker = WaveformFillWorker(self.lib, canonical)
        worker.tasks_ready.connect(totals.append)
        worker.run()
        self.assertEqual(totals, [0])
        self.assertFalse(track_analysis.cache_path(
            canonical, "1").exists())
        self.assertTrue(track_analysis.is_ready(
            canonical, "0", self.paths[0]))

    def test_mid_store_graveyard_change_cannot_republish_analysis(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        original_store = track_analysis.store

        def store_then_hide(*args, **kwargs):
            result = original_store(*args, **kwargs)
            self.lib.hidden_ids.add("0")
            return result

        ready = []
        worker = WaveformFillWorker(self.lib, canonical)
        worker.detail_ready.connect(ready.append)
        with patch(
                "soundbox.track_analysis.store",
                side_effect=store_then_hide):
            worker.run()

        self.assertFalse(track_analysis.cache_path(
            canonical, "0").exists())
        self.assertNotIn(self.paths[0], ready)

    def test_interrupted_decode_never_degrades_existing_v5(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track_analysis.store(canonical, "0", self.paths[0], {
            "detail_heights": b"\x33" * 100,
            "detail_rate": 150.0,
            "overview_heights": b"\x40",
            "overview_rgb": b"\x02\x03\x04",
        })
        before = track_analysis.cache_path(
            canonical, "0").read_bytes()
        worker = WaveformFillWorker(self.lib, canonical)
        with patch(
                "soundbox.track_analysis.analyze_soundfile",
                return_value=None
        ), patch("desktop.player.qt_detail_peaks", return_value=None):
            worker.run()
        self.assertEqual(
            track_analysis.cache_path(canonical, "0").read_bytes(),
            before,
        )

    def test_autostart_wiring_starts_a_worker(self):
        real_dir = _win._track_analysis_dir
        _win._track_analysis_dir = Path(self.cache) / "track_analysis"
        saved_lib = _win.lib
        _win.lib = self.lib
        try:
            _win._maybe_fill_waveforms()
            self.assertIsNotNone(_win._wf_fill_worker)
            _win._wf_fill_worker.requestInterruption()
            _win._wf_fill_worker.wait(8000)
        finally:
            _win._track_analysis_dir = real_dir
            _win.lib = saved_lib

    def test_reanalysis_request_queues_only_available_selected_tracks(self):
        from unittest.mock import patch

        saved_lib = _win.lib
        saved_pending = set(_win._waveform_force_pending)
        _win.lib = self.lib
        _win._waveform_force_pending.clear()
        self.lib.hidden_ids = {"1"}
        saved_hidden = set(_win.hidden)
        _win.hidden = {"1"}
        try:
            with patch.object(_win, "_maybe_fill_waveforms") as start:
                _win._reanalyze_tracks(["0", "1", "ghost"])
            self.assertEqual(_win._waveform_force_pending, {"0"})
            start.assert_called_once_with()
        finally:
            _win._waveform_force_pending.clear()
            _win._waveform_force_pending.update(saved_pending)
            _win.hidden = saved_hidden
            _win.lib = saved_lib

    def test_recurring_recheck_timer_is_bounded_and_single_shot(self):
        from desktop.app import _WAVEFORM_RECHECK_MS

        saved = _win._waveform_rechecks_enabled
        _win._waveform_recheck_timer.stop()
        try:
            _win._waveform_rechecks_enabled = True
            _win._schedule_waveform_recheck()
            self.assertTrue(_win._waveform_recheck_timer.isSingleShot())
            self.assertEqual(
                _win._waveform_recheck_timer.interval(),
                _WAVEFORM_RECHECK_MS)
            self.assertTrue(_win._waveform_recheck_timer.isActive())
        finally:
            _win._waveform_recheck_timer.stop()
            _win._waveform_rechecks_enabled = saved


class CollectionLandedOrder(unittest.TestCase):
    """Collection default order = when the FILE landed on the computer (newest
    first), unified across rekordbox + imported. Real files, staggered creation."""

    def setUp(self):
        import tempfile, time
        from pathlib import Path
        from soundbox.library import Library, Track
        self._td = tempfile.TemporaryDirectory()
        d = Path(self._td.name)
        self.paths = []
        for nm in ("old", "mid", "new"):
            p = d / f"{nm}.wav"
            p.write_bytes(b"x")
            self.paths.append(p)
            time.sleep(0.05)  # ensure distinct birth times
        tracks = {
            "1": Track("1", "Zzz Old", "A", self.paths[0].as_uri()),
            "2": Track("2", "Aaa Mid", "A", self.paths[1].as_uri()),
            "3": Track("3", "Mmm New", "A", self.paths[2].as_uri()),
        }
        self._saved_lib, self._saved_cache = _win.lib, _win._landed_cache
        _win._landed_cache = {}
        _win.lib = Library(tracks_by_id=tracks, playlists=[], source="x")

    def tearDown(self):
        _win.lib, _win._landed_cache = self._saved_lib, self._saved_cache
        self._td.cleanup()

    def test_newest_file_first_regardless_of_title(self):
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["3", "2", "1"])  # new, mid, old — not title order

    def test_landed_time_is_cached(self):
        _win._collection_tracks()
        self.assertIn(str(self.paths[0]), _win._landed_cache)

    def test_sorts_by_add_time_not_birthtime(self):
        # Sonny's case + the unified timeline: add-time (added_ts) drives the
        # order across BOTH sources, with time-of-day precision. An import with an
        # OLD file but a later add-time must sort above a rekordbox track with a
        # NEWER file but an earlier add-time.
        from soundbox.library import Track
        _win._landed_cache = {}
        _win.lib.tracks_by_id = {
            # oldest file (paths[0]) but newest add-time -> FIRST (a set imported now)
            "late_set": Track("late_set", "Set Imported Now", "Sonny",
                              self.paths[0].as_uri(),
                              added_ts=2_000.0, imported=True),
            # newest file (paths[2]) but earlier add-time -> LAST (a rekordbox track)
            "early_rb": Track("early_rb", "Rekordbox Track Earlier", "Sonny",
                             self.paths[2].as_uri(), added_ts=1_000.0),
        }
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["late_set", "early_rb"])  # by add-time, not file age

    def test_known_add_times_do_not_resolve_paths_or_merge_shared_source_dates(self):
        from unittest.mock import PropertyMock
        tracks = _win.lib.tracks_by_id
        tracks['1'].added_ts = 3000.0
        tracks['2'].added_ts = 2000.0
        tracks['3'].added_ts = 1000.0
        tracks['2'].location_uri = tracks['1'].location_uri
        with mock.patch.object(Track, 'path', new_callable=PropertyMock,
                               side_effect=AssertionError('Known date needs no path')):
            self.assertEqual([t.track_id for t in _win._collection_tracks()], ['1', '2', '3'])

    def test_missing_add_time_falls_back_to_birthtime(self):
        # No added_ts (e.g. an XML-loaded library) -> file birthtime still orders.
        from soundbox.library import Track
        _win._landed_cache = {}
        _win.lib.tracks_by_id = {
            "1": Track("1", "Zzz Old", "A", self.paths[0].as_uri()),
            "3": Track("3", "Mmm New", "A", self.paths[2].as_uri()),
        }
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["3", "1"])  # newer file first


class ArtistsDrag(unittest.TestCase):
    """Artists tab is a drag source: a track row carries its id; an artist row
    carries all its tracks — as `trk` mime a SoundBox playlist already accepts."""

    def setUp(self):
        _win.sb_tree.setCurrentItem(None)
        _win._show_artists()
        _app.processEvents()

    def test_artist_header_drags_all_its_tracks(self):
        from desktop.trees import ARTIST_ROLE
        top = _win.artists_view.topLevelItem(0)
        md = _win.artists_view.mimeData([top])
        self.assertTrue(md.text().startswith("trk\t"))
        ids = md.text().split("\t", 1)[1].split(",")
        self.assertEqual(sorted(ids), sorted(r["id"] for r in
                                             _win._artist_children[top.data(0, ARTIST_ROLE)]))

    def test_track_row_drags_its_own_id(self):
        from desktop.trees import ARTIST_TRACK_ROLE
        top = _win.artists_view.topLevelItem(0)
        top.setExpanded(True)  # lazy-fills children
        _app.processEvents()
        child = top.child(0)
        md = _win.artists_view.mimeData([child])
        self.assertEqual(md.text(), "trk\t" + child.data(0, ARTIST_TRACK_ROLE))

    def test_drop_onto_playlist_adds(self):
        from desktop.trees import ARTIST_IDS_ROLE
        _win.custom["ARTDROP"] = []
        try:
            top = _win.artists_view.topLevelItem(0)
            ids = list(top.data(0, ARTIST_IDS_ROLE))
            _win._on_tracks_dropped("ARTDROP", ids)
            self.assertEqual(_win.custom["ARTDROP"], ids)
        finally:
            _win.custom.pop("ARTDROP", None)
            _win._refresh_custom()


class ArtistsSearch(unittest.TestCase):
    """The search bar filters the Artists browser too, by the same multi-field
    query (artist name, or any of the artist's tracks)."""

    def setUp(self):
        _win.search.setText("")
        _win.sb_tree.setCurrentItem(None)
        _win._show_artists()
        _app.processEvents()

    def tearDown(self):
        _win.search.setText("")

    def _visible_artists(self):
        from desktop.trees import ARTIST_ROLE as ARTIST_ROLE_
        v = _win.artists_view
        return [v.topLevelItem(i).data(0, ARTIST_ROLE_)
                for i in range(v.topLevelItemCount()) if not v.topLevelItem(i).isHidden()]

    def test_filter_by_artist_name(self):
        _win.search.setText("ann")     # the synthetic lib has artist "Ann"
        _app.processEvents()
        vis = self._visible_artists()
        self.assertIn("Ann", vis)
        self.assertNotIn("Zed", vis)

    def test_blank_shows_all_artists(self):
        _win.search.setText("ann"); _app.processEvents()
        _win.search.setText("");    _app.processEvents()
        self.assertEqual(set(self._visible_artists()), {"Ann", "Mia", "Zed"})

    def test_switching_to_tab_applies_active_search(self):
        _win.search.setText("mia")
        _win._show_artists()           # re-enter the tab with a live query
        _app.processEvents()
        self.assertEqual(self._visible_artists(), ["Mia"])


class LibraryInstantPaint(unittest.TestCase):
    """The library snapshot round-trips through the app so open isn't blank."""

    def test_cache_library_then_load_restores_pool(self):
        import tempfile
        from pathlib import Path
        from soundbox import libcache
        from soundbox.cache_process import CacheProcess
        with tempfile.TemporaryDirectory() as td:
            while _win._libcache_worker is not None:
                _win._libcache_worker.wait(4000)
                _app.processEvents()
            saved = _win._libcache_path
            saved_service = _win._cache_process
            _win._libcache_path = Path(td) / "libcache.json"
            _win._cache_process = CacheProcess(Path(td).resolve())
            _win._cache_process.start()
            try:
                _win._cache_library()
                for w in list(getattr(_win, "_oneshots", [])):
                    w.wait(4000)
                got = libcache.load(_win._libcache_path)
                self.assertIsNotNone(got)
                self.assertEqual(set(got.tracks_by_id), set(_win.lib.tracks_by_id))
            finally:
                _win._cache_process.close()
                _win._cache_process = saved_service
                _win._libcache_path = saved

    def test_newer_library_waits_behind_active_cache_write(self):
        class OwnedWorker:
            pass

        saved_worker = _win._libcache_worker
        saved_pending = _win._libcache_pending
        saved_shutting_down = _win._shutting_down
        newer = _make_lib()
        try:
            _win._libcache_worker = OwnedWorker()
            _win._libcache_pending = None
            _win._shutting_down = False
            _win.lib = newer
            _win._cache_library()
            self.assertIs(_win._libcache_pending, newer)
        finally:
            _win._libcache_worker = saved_worker
            _win._libcache_pending = saved_pending
            _win._shutting_down = saved_shutting_down


class RekordboxRefreshOrdering(unittest.TestCase):
    """Refresh clicks and results cannot silently land out of order."""

    def setUp(self):
        # Source selection has its own curation partition. Other UI cases share
        # this window; their edits must not become this test's source identity.
        names=('lib','titles','overrides','aliases','custom','removals','playlist_additions',
               'hidden','hidden_dates','settings','_rekordbox_source_verified','_rekordbox_connection_error')
        before={name:getattr(_win,name) for name in names}
        def restore():
            for name,value in before.items():setattr(_win,name,value)
            _win.tag_controller.configure(_win.lib,_win._rekordbox_source_verified)
            _win._sync_rekordbox_connection()
        self.addCleanup(restore)
        _win.lib=_make_lib();_win.titles={};_win.overrides={};_win.aliases={};_win.custom={}
        _win.removals={};_win.playlist_additions={};_win.hidden=set();_win.hidden_dates={};_win.settings={}

    def test_invalid_xml_retains_previous_library_and_remembered_source(self):
        previous, previous_xml = _win.lib, _win._last_xml
        with mock.patch("desktop.app.engine.load_library", side_effect=ValueError("Invalid XML")), \
                mock.patch("desktop.app.QMessageBox.critical") as error:
            _win.open_library("/synthetic/invalid.xml")
        error.assert_called_once()
        self.assertIs(_win.lib, previous)
        self.assertEqual(_win._last_xml, previous_xml)

    def test_selected_source_publishes_and_saves_only_after_worker_retirement(self):
        from desktop.workers import RefreshWorker
        path = Path("/synthetic/relocated/master.db")
        original = _win.lib
        settings = dict(_win.settings)
        replacement = _make_lib()
        replacement.source=str(path)
        publish_library = _win._on_refresh_done
        def publish(library, **options):
            self.assertIsNone(_win._refresh_worker)
            save.assert_not_called()
            self.assertEqual(_win.settings, settings)
            self.assertIs(library, replacement)
            return publish_library(library, **options)
        try:
            with mock.patch.object(RefreshWorker, "start"), \
                    mock.patch.object(_win, "_save_state", return_value=True) as save, \
                    mock.patch.object(_win, "_apply_loaded_library") as apply, \
                    mock.patch.object(_win, "_cache_library"), \
                    mock.patch.object(_win, "_refresh_devices"), \
                    mock.patch.object(_win, "_schedule_stock_artwork"), \
                    mock.patch("desktop.app.QTimer.singleShot"), \
                    mock.patch.object(_win, "_on_refresh_done", side_effect=publish) as done:
                _win._start_rekordbox_refresh(path, selection=True)
                worker = _win._refresh_worker
                worker.result = replacement
                worker.done.emit(replacement)
                done.assert_not_called()
                save.assert_not_called()
                self.assertIs(_win.lib, original)
                worker.finished.emit()
                save.assert_called_once()
                done.assert_called_once_with(replacement, metadata_changed_ids=(), selection_path=str(path))
                apply.assert_called_once_with(already_projected=True, reset_navigation=True)
                self.assertIs(_win.lib, replacement)
                self.assertEqual(_win.settings['rekordbox_db_path'], str(path))
        finally:
            _win.settings = settings
            _win._sync_rekordbox_connection()

    def test_failed_read_or_save_keeps_previous_source_and_curation(self):
        from desktop.workers import RefreshWorker
        original = _win.lib
        settings = dict(_win.settings)
        publish_library = _win._on_refresh_done
        for failed_read in (True, False):
            try:
                with self.subTest(failed_read=failed_read), mock.patch.object(RefreshWorker, "start"), \
                        mock.patch.object(_win, "_save_state", return_value=False) as save, \
                        mock.patch.object(_win, "_apply_loaded_library") as apply, \
                        mock.patch.object(_win, "_on_refresh_done", wraps=publish_library) as done, \
                        mock.patch.object(_win, "_on_refresh_fail") as failure:
                    _win._start_rekordbox_refresh(Path("/synthetic/new/master.db"), selection=True)
                    worker = _win._refresh_worker
                    worker.result = None if failed_read else _make_lib()
                    if worker.result is not None:worker.result.source='/synthetic/new/master.db'
                    worker.error = "Cannot read selected database" if failed_read else ""
                    worker.finished.emit()
                    if failed_read:
                        done.assert_not_called()
                        save.assert_not_called()
                    else:
                        done.assert_called_once_with(worker.result, metadata_changed_ids=(),
                                                    selection_path='/synthetic/new/master.db')
                        save.assert_called_once()
                    apply.assert_not_called()
                    failure.assert_called_once()
                    self.assertEqual(_win.settings, settings)
                    self.assertIs(_win.lib, original)
            finally:
                _win.settings = dict(settings)

    def test_source_card_distinguishes_cached_library_and_verified_source_without_probing(self):
        verified, error = _win._rekordbox_source_verified, _win._rekordbox_connection_error
        try:
            with mock.patch("desktop.app.rekordbox_db.db_available", side_effect=AssertionError("display must not probe source")):
                _win._rekordbox_connection_error = ""
                _win._rekordbox_source_verified = False
                _win._sync_rekordbox_connection()
                self.assertIn("source not yet verified", _win._rekordbox_section.status.text())
                _win._rekordbox_source_verified = True
                _win._sync_rekordbox_connection()
                self.assertIn("loaded", _win._rekordbox_section.status.text())
                _win._rekordbox_connection_error = "Source unavailable"
                _win._sync_rekordbox_connection()
                self.assertIn("Last refresh failed", _win._rekordbox_section.status.text())
        finally:
            _win._rekordbox_source_verified, _win._rekordbox_connection_error = verified, error
            _win._sync_rekordbox_connection()

    def test_click_during_refresh_queues_one_follow_up(self):
        class OwnedWorker:
            @staticmethod
            def isRunning():
                # Models the gap after run() returns but before Qt delivers
                # the worker's queued ``finished`` callback.
                return False

        saved_worker = _win._refresh_worker
        saved_pending = _win._refresh_pending
        try:
            _win._refresh_worker = OwnedWorker()
            _win._refresh_pending = False
            _win._refresh_rekordbox()
            self.assertTrue(_win._refresh_pending)
            _win._refresh_rekordbox()
            self.assertTrue(_win._refresh_pending)
        finally:
            _win._refresh_worker = saved_worker
            _win._refresh_pending = saved_pending

    def test_finished_refresh_schedules_queued_newer_read(self):
        class FinishedWorker:
            deleted = False

            def deleteLater(self):
                self.deleted = True

        saved_worker = _win._refresh_worker
        saved_pending = _win._refresh_pending
        saved_shutting_down = _win._shutting_down
        worker = FinishedWorker()
        callbacks = []
        try:
            _win._refresh_worker = worker
            _win._refresh_pending = True
            _win._shutting_down = False
            with mock.patch("desktop.app.QTimer.singleShot",
                            side_effect=lambda _delay, fn: callbacks.append(fn)):
                _win._finish_refresh_worker(worker)
            self.assertIsNone(_win._refresh_worker)
            self.assertFalse(_win._refresh_pending)
            self.assertTrue(worker.deleted)
            self.assertEqual(callbacks, [_win._refresh_rekordbox])
        finally:
            _win._refresh_worker = saved_worker
            _win._refresh_pending = saved_pending
            _win._shutting_down = saved_shutting_down


class WaveformRapidLoad(unittest.TestCase):
    """Rapid track changes stay safe; canonical hits load synchronously."""

    @classmethod
    def setUpClass(cls):
        import math, struct, tempfile, wave
        from pathlib import Path
        cls.dir = tempfile.mkdtemp(prefix="sb-wfrapid-")
        cls.paths = []
        sr = 8000
        for i in range(6):
            p = Path(cls.dir) / f"t{i}.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(b"".join(
                    struct.pack("<h", int(0.5 * 32767 * math.sin(2*math.pi*200*t/sr)))
                    for t in range(sr)))
            cls.paths.append(str(p))
        cls.cache = tempfile.mkdtemp(prefix="sb-wfrapid-cache-")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)
        shutil.rmtree(cls.cache, ignore_errors=True)

    def setUp(self):
        from desktop.player import PlayerBar
        self.pb = PlayerBar()

    def tearDown(self):
        self.pb.shutdown_waveforms()

    def test_canonical_v5_loads_inline_with_prebuilt_detail(self):
        from pathlib import Path
        from soundbox.track_analysis import COLOR_MODEL, store

        canonical = Path(self.cache) / "track_analysis"
        store(canonical, "v4", self.paths[0], {
            "detail_heights": b"\x20\x80\xff",
            "detail_rate": 6144.0,
            "overview_heights": b"\x40\xff",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "color_model": COLOR_MODEL,
            "beats": [[1, 625, 128.0]],
        })
        self.pb.track_analysis_dir = canonical
        self.pb._track_id = "v4"
        self.pb._src_path = self.paths[0]
        self.pb._pending_autoplay = True
        self.pb._start_waveform(self.paths[0])
        worker = self.pb._wf
        self.assertIsNotNone(worker)
        worker.wait(5000)
        _app.processEvents()
        self.assertIsNone(self.pb._wf)
        self.assertFalse(self.pb._pending_autoplay)
        self.assertIsNone(self.pb._pending_start_ms)
        self.assertEqual(self.pb._cue_ms, 0)
        self.assertEqual(self.pb.wave._beats[0][1], 0)
        self.assertEqual(list(self.pb.wave._audio_detail), [65534])
        self.assertEqual(self.pb.wave._audio_detail_rate, 768.0)

    def test_graveyard_cancel_discards_interactive_worker_cache_on_exit(self):
        from desktop.player import WaveformWorker
        from soundbox.track_analysis import cache_path

        canonical = Path(self.cache) / "track_analysis"
        canonical.mkdir(exist_ok=True)
        target = cache_path(canonical, "grave")
        target.write_bytes(b"disposable")
        worker = WaveformWorker(
            self.paths[0], track_id="grave",
            track_analysis_dir=canonical)
        self.pb._wf_pending.append(worker)
        try:
            self.pb.cancel_analysis_cache_writes({"grave"})
            self.assertTrue(worker._discard_requested.is_set())
            worker.run()
            self.assertFalse(target.exists())
        finally:
            self.pb._wf_pending.remove(worker)

    def test_hidden_audition_prepares_playable_deck_without_canonical_cache(self):
        from soundbox.track_analysis import cache_path, store
        canonical = Path(self.cache) / 'hidden-audition'
        self.pb.track_analysis_dir = canonical
        self.pb.analysis_cache_admitted = lambda _tid: False
        self.pb._track_id = 'hidden'; self.pb._src_path = self.paths[0]
        self.pb._pending_autoplay = True; self.pb._pending_should_play = False
        destinations = []
        def capture(destination, *args, **kwargs):
            destinations.append(Path(destination))
            return store(destination, *args, **kwargs)
        with mock.patch('soundbox.track_analysis.store', side_effect=capture):
            self.pb._start_waveform(self.paths[0])
            worker = self.pb._wf
            worker.wait(5000)
            self.assertFalse(worker.isRunning())
            _app.processEvents()
        self.assertTrue(destinations)
        self.assertTrue(all(p != canonical and not p.exists() for p in destinations))
        self.assertFalse(cache_path(canonical, 'hidden').exists())
        self.assertFalse(self.pb._pending_autoplay)
        self.assertTrue(self.pb._analysis_parts.get('_overview_heights'))
        self.assertTrue(self.pb._analysis_parts.get('_audio_detail'))

    def test_blocked_cache_publication_still_reads_existing_waveform_without_decoding(self):
        from desktop.player import WaveformWorker
        from soundbox.track_analysis import COLOR_MODEL,store,cache_path
        canonical=Path(self.cache)/'readable-cache'
        store(canonical,'cached',self.paths[0],dict(detail_heights=b'\x20\x80\xff',detail_rate=6144.,
            overview_heights=b'\x40\xff',overview_rgb=b'\x01\x02\x03\x04\x05\x06',color_model=COLOR_MODEL,beats=[]))
        target=cache_path(canonical,'cached');before=target.read_bytes()
        worker=WaveformWorker(self.paths[0],track_id='cached',track_analysis_dir=canonical)
        worker.transient_analysis=True;visible=[]
        worker.overview_ready.connect(lambda *_:visible.append(True))
        with mock.patch('soundbox.track_analysis.analyze_soundfile',side_effect=AssertionError('Cached source must not decode')), \
                mock.patch('desktop.player.qt_detail_peaks',side_effect=AssertionError('Cached AAC must not decode')), \
                mock.patch('soundbox.track_analysis.store',side_effect=AssertionError('Publication remains blocked')):
            worker.run()
        self.assertTrue(visible);self.assertEqual(target.read_bytes(),before)

    def test_long_cached_waveform_first_paint_uses_complete_geometry(self):
        from desktop.player import WaveformWorker
        backend=mock.Mock();backend.set_complete_analysis.return_value=(True,True)
        worker=WaveformWorker(self.paths[0],track_id='long',track_analysis_dir=self.cache,native_backend=backend)
        events=[];worker.manifest_ready.connect(lambda *_:events.append('manifest'))
        worker.overview_ready.connect(lambda *_:events.append('overview'))
        worker.detail_ready.connect(lambda *_:events.append('detail'))
        overview={'_overview_heights':b'\xff','_overview_rgb':b'\xff\x00\x00'}
        detailed={'_audio_detail':{'rate':1000}}
        def detail(*_,**options):
            self.assertEqual(events,['manifest'])
            backend.set_overview.assert_not_called()
            backend.set_complete_analysis.assert_not_called()
            self.assertEqual(options,{'include_spectral':False})
            return detailed
        def complete(preview,contour,generation):
            self.assertEqual(events,['manifest'])
            self.assertEqual(preview['_overview_heights'],overview['_overview_heights'])
            self.assertIs(contour,detailed)
            return True,True
        backend.set_complete_analysis.side_effect=complete
        with mock.patch('soundbox.track_analysis.load_manifest',return_value=dict(detail_rate=1000,detail_base_count=900001)), \
                mock.patch('soundbox.track_analysis.load_overview',return_value=overview), \
                mock.patch('soundbox.track_analysis.load_detail',side_effect=detail):
            self.assertEqual(worker._load_stages(),(True,True,True))
        self.assertEqual(events,['manifest','overview','detail'])
        backend.set_complete_analysis.assert_called_once()
        backend.set_overview.assert_not_called();backend.set_audio_detail.assert_not_called()
        self.assertTrue(overview['_native_uploaded']);self.assertTrue(detailed['_native_uploaded'])

    def test_incomplete_cached_waveform_waits_for_its_repaired_contour(self):
        from desktop.player import WaveformWorker
        backend=mock.Mock()
        worker=WaveformWorker(self.paths[0],track_id='partial',track_analysis_dir=self.cache,native_backend=backend)
        visible=[];worker.overview_ready.connect(lambda *_:visible.append('overview'))
        worker.detail_ready.connect(lambda *_:visible.append('detail'))
        with mock.patch('soundbox.track_analysis.load_manifest',return_value={}), \
                mock.patch('soundbox.track_analysis.load_overview',return_value={'_overview_heights':b'\xff'}), \
                mock.patch('soundbox.track_analysis.load_detail',return_value=None):
            self.assertEqual(worker._load_stages(),(True,True,False))
        self.assertFalse(visible)
        backend.set_overview.assert_not_called();backend.set_complete_analysis.assert_not_called()
        backend.set_audio_detail.assert_not_called()

    def test_native_rejected_complete_upload_never_publishes_a_ready_surface(self):
        from desktop.player import WaveformWorker
        backend=mock.Mock();backend.set_complete_analysis.return_value=(True,False)
        worker=WaveformWorker(self.paths[0],track_id='rejected',track_analysis_dir=self.cache,native_backend=backend)
        visible=[];worker.overview_ready.connect(lambda *_:visible.append('overview'))
        worker.detail_ready.connect(lambda *_:visible.append('detail'))
        with mock.patch('soundbox.track_analysis.load_manifest',return_value={}), \
                mock.patch('soundbox.track_analysis.load_overview',return_value={'_overview_heights':b'\xff'}), \
                mock.patch('soundbox.track_analysis.load_detail',return_value={'_audio_detail':{'rate':1000}}):
            self.assertEqual(worker._load_stages(),(True,True,True))
        self.assertFalse(worker.surface_ready);self.assertEqual(visible,[])

    def test_no_canonical_directory_prepares_complete_private_surface(self):
        from desktop.player import WaveformWorker
        from soundbox.track_analysis import store
        worker=WaveformWorker(self.paths[0],track_id='standalone')
        destinations=[];visible=[]
        def capture(destination,*args,**kwargs):
            destinations.append(Path(destination));return store(destination,*args,**kwargs)
        worker.detail_ready.connect(lambda _path,_generation,data:visible.append(data))
        with mock.patch('soundbox.track_analysis.store',side_effect=capture):worker.run()
        self.assertTrue(worker.surface_ready);self.assertTrue(visible[0]['_waveform_complete'])
        self.assertTrue(destinations);self.assertTrue(all(not path.exists() for path in destinations))

    def test_valid_cached_surface_does_not_resize_when_background_upgrade_finishes(self):
        from desktop.player import WaveformWorker
        from soundbox.track_analysis import WAVEFORM_MODEL
        worker=WaveformWorker(self.paths[0],analysis_path='/synthetic/ANLZ.DAT',
            track_id='old-valid',track_analysis_dir=self.cache)
        with mock.patch.object(worker,'_load_stages',return_value=(True,True,True)) as publish, \
                mock.patch('soundbox.track_analysis.load_manifest',return_value={'waveform_model':WAVEFORM_MODEL}), \
                mock.patch('soundbox.track_analysis.is_dataless_source',return_value=False), \
                mock.patch('soundbox.rekordbox_analysis.read',return_value={}), \
                mock.patch('soundbox.waveform_detail.complete_import',return_value=True), \
                mock.patch('soundbox.waveform_detail.analyze_imported',return_value={'completed':'new cache'}), \
                mock.patch('soundbox.track_analysis.store',return_value=True) as save:
            worker._run(self.cache)
        save.assert_called_once();publish.assert_called_once_with(self.cache)

    def test_canonical_rekordbox_grid_keeps_exact_first_beat(self):
        from pathlib import Path
        from soundbox.track_analysis import COLOR_MODEL, store

        canonical = Path(self.cache) / "track_analysis"
        store(canonical, "rb-v4", self.paths[0], {
            "detail_heights": b"\x20\x80\xff",
            "detail_rate": 6144.0,
            "overview_heights": b"\x40\xff",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "color_model": COLOR_MODEL,
            "beats": [[1, 625, 128.0], [2, 1094, 128.0]],
            "provenance": {
                "analysis": "rekordbox-import+soundbox",
                "grid": "rekordbox",
                "owner": "soundbox",
            },
        })
        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only marker")
        self.pb.track_analysis_dir = canonical
        self.pb._track_id = "rb-v4"
        self.pb._src_path = self.paths[0]
        self.pb._analysis_path = str(dat)
        self.pb._pending_autoplay = True
        self.pb._start_waveform(self.paths[0], str(dat))
        worker = self.pb._wf
        self.assertIsNotNone(worker)
        worker.wait(5000)
        _app.processEvents()
        self.assertIsNone(self.pb._wf)
        self.assertEqual(self.pb._pending_start_ms, 625)
        self.assertEqual(self.pb.wave._beats[0][1], 625)

    def test_cancelled_detail_read_never_enters_native_upload(self):
        from desktop.player import WaveformWorker
        backend = mock.Mock()
        worker = WaveformWorker(self.paths[0], track_id="stale",
                                track_analysis_dir=self.cache, native_backend=backend)
        cancelled = False
        def read_detail(*_args,**_kwargs):
            nonlocal cancelled
            cancelled = True
            return {"_audio_detail": {"rate": 1000}}
        with (mock.patch("soundbox.track_analysis.load_manifest", return_value={}),
              mock.patch("soundbox.track_analysis.load_overview", return_value={}),
              mock.patch("soundbox.track_analysis.load_detail", side_effect=read_detail),
              mock.patch.object(worker, "isInterruptionRequested", side_effect=lambda: cancelled)):
            self.assertEqual(worker._load_stages(), (True, True, True))
        backend.set_complete_analysis.assert_not_called()
        backend.set_audio_detail.assert_not_called()

    def test_rapid_switch_interrupts_previous(self):
        # Fire uncached decodes back to back; each new start interrupts the last.
        prev = None
        for p in self.paths:
            self.pb._src_path = p
            self.pb._start_waveform(p)
            if prev is not None and prev is not self.pb._wf:
                self.assertTrue(prev.isInterruptionRequested() or not _thread_alive(prev))
            prev = self.pb._wf
        self.pb.shutdown_waveforms()
        # every worker ref is cleaned up (no leaks, no running threads)
        _app.processEvents()
        self.assertTrue(all(not _thread_alive(w) for w in self.pb._wf_pending))


def _thread_alive(w):
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False


class RowCache(unittest.TestCase):
    """Computed rows are cached (instant playlist clicks), warmed off-thread,
    and invalidated when overrides/registry/junk change."""

    def setUp(self):
        _win._warm_timer.stop()
        _win._invalidate_rows()
        _win._warm_timer.stop()  # don't let the debounce fire during the test

    def tearDown(self):
        # test_warm_fills_every_track plants FAKE rows ({"id", "title": "x"}) in
        # the cache — a later class that renders a view from the cache would show
        # them (this latently broke SearchSelection once). Drop them.
        _win._row_cache.clear()
        _win._cache_gen += 1  # orphan any in-flight warm from this test
        _win._warm_timer.stop()

    def test_row_for_populates_and_reuses_cache(self):
        _win._row_cache.clear()
        t = _win.lib.tracks_by_id["1"]
        r1 = _win._row_for(t)
        self.assertIn("1", _win._row_cache)          # cached after first compute
        self.assertIsNot(r1, _win._row_cache["1"])   # returns a COPY (safe to mutate)
        r1["title"] = "MUT"
        self.assertNotEqual(_win._row_cache["1"]["title"], "MUT")  # cache untouched

    def test_background_warm_skips_graveyard_tracks(self):
        hidden = set(_win.lib.hidden_ids)
        worker = _win._warm_worker
        try:
            _win.lib.hidden_ids = {'1'}
            _win._warm_worker = None
            with mock.patch('desktop.workers.OneShotTask') as task:
                _win._do_warm()
                rows = task.call_args.args[0]()
            self.assertEqual(set(rows), set(_win.lib.tracks_by_id) - {'1'})
        finally:
            _win.lib.hidden_ids = hidden
            _win._warm_worker = worker

    def test_invalidate_clears_and_bumps_generation(self):
        _win._row_for(_win.lib.tracks_by_id["1"])
        g = _win._cache_gen
        _win._invalidate_rows()
        self.assertEqual(_win._row_cache, {})
        self.assertEqual(_win._cache_gen, g + 1)
        _win._warm_timer.stop()

    def test_warm_fills_every_track(self):
        _win._row_cache.clear()
        built = {tid: {"id": tid, "title": "x"} for tid in _win.lib.tracks_by_id}
        _win._on_rows_warmed(built, _win._cache_gen)   # gen matches → applied
        self.assertEqual(set(_win._row_cache), set(_win.lib.tracks_by_id))

    def test_stale_warm_is_discarded(self):
        _win._row_cache.clear()
        stale_gen = _win._cache_gen - 1
        _win._on_rows_warmed({"1": {"id": "1"}}, stale_gen)
        self.assertEqual(_win._row_cache, {})          # discarded, not applied
        _win._warm_timer.stop()

    def test_title_edit_invalidates(self):
        _win._row_cache.clear()
        _win._rows_for(_win.lib.tracks_by_id.values())
        other = _win._row_cache["2"]
        _win._on_cell_edited("title", "1", "New Title")
        self.assertNotIn("1", _win._row_cache)
        self.assertIs(_win._row_cache["2"], other)
        with mock.patch("desktop.app.engine.track_row", wraps=engine.track_row) as compute:
            _win._rows_for(_win.lib.tracks_by_id.values())
        self.assertEqual(compute.call_count, 1)
        self.assertEqual(_win._row_cache["1"]["title"], "New Title")
        _win.titles.pop("1", None)
        _win._save_state()
        _win._warm_timer.stop()


class RefreshWhileViewingCollection(unittest.TestCase):
    """A live rekordbox refresh landing while Collection is on screen must
    re-render the visible rows in place — _refresh_custom restores the sidebar
    selection with signals BLOCKED, so without the explicit re-render in
    _apply_loaded_library a new rekordbox add stayed invisible until you
    clicked away and back (the real-world 'new songs missing' bug)."""

    def tearDown(self):
        # Full restore for later classes: fresh 3-track lib, tree rebuilt,
        # visible view re-rendered (see soundbox-gotchas on UI test hygiene).
        _win.lib = _make_lib()
        _win._build_artist_registry()
        _win._apply_settings_to_lib()
        _win._populate_tree(engine.summary(_win.lib))
        _win._reload_current_view()

    def test_prep_bpm_tracks_the_loaded_song_and_saved_refresh_not_selection(self):
        _win.lib = _make_lib()
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        with mock.patch.object(_win.player_bar, 'current_track', return_value='1'):
            _win._sync_prep_bpm()
            self.assertEqual(_win.bpm_value.text(), '140.00')
            row = next(i for i, data in enumerate(_win.model._rows) if data['id'] == '2')
            _win.table.selectRow(row)
            self.assertEqual(_win._selected_ids(), ['2'])
            self.assertEqual(_win.bpm_value.text(), '140.00')
            fresh = _make_lib()
            fresh.tracks_by_id['1'].bpm = '142.50'
            self._refresh_lands(fresh)
            self.assertEqual(_win.bpm_value.text(), '142.50')
        with mock.patch.object(_win.player_bar, 'current_track', return_value=None):
            _win._sync_prep_bpm()
            self.assertEqual(_win.bpm_value.text(), '000.00')

    def _refresh_lands(self, fresh, *, prepared_native_ids=None):
        """What _on_refresh_done does with a freshly read library (direct call —
        Qt slots swallow exceptions)."""
        if prepared_native_ids is not None:self.assertEqual(prepared_native_ids,())
        _win.lib = fresh
        _win._apply_loaded_library()

    def test_loaded_deck_receives_cue_edits_and_deletions_without_reload(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        live = [{"kind": "hot", "slot": 1, "position_ms": 9000,
                 "label": "New drop", "color": [1, 2, 3]},
                {"kind": "memory", "slot": 0, "position_ms": 4000}]
        try:
            pb._track_id, pb._src_path = "1", str(_make_lib().tracks_by_id["1"].path)
            with mock.patch.object(_win, "player_bar", pb), \
                    mock.patch.object(pb.player, "set_cues") as publish, \
                    mock.patch.object(pb, "play_track") as load, \
                    mock.patch.object(pb.player, "setPosition") as seek:
                for cues in (live, []):
                    fresh = _make_lib()
                    fresh.tracks_by_id["1"].source_cues = cues
                    self._refresh_lands(fresh)
                    self.assertEqual(pb._live_cues, cues)
                    self.assertCountEqual(pb.wave._cues, cues)
                    publish.assert_called_with(cues)
                    # An older queued manifest cannot put stale cues back.
                    pb._on_analysis_manifest(pb._src_path, pb._analysis_path,
                        pb._analysis_generation, {"beats": [], "cues": live})
                    self.assertCountEqual(pb.wave._cues, cues)
                load.assert_not_called()
                seek.assert_not_called()
                pb.refresh_live_cues("other", pb._src_path, live)
                pb.refresh_live_cues("1", "/different/source.wav", live)
                pb.refresh_live_cues("1", pb._src_path, None)
                self.assertEqual(pb._live_cues, [])
        finally:
            pb.shutdown()
            pb.close()

    def test_new_track_appears_without_switching_views(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual(len(_win.model._rows), 3)  # showing Collection
        fresh = _make_lib()
        fresh.tracks_by_id["4"] = Track("4", "Delta", "New",
                                        "file://localhost/tmp/d.wav",
                                        bpm="150.00", key="4A", total_time=200,
                                        date_added="2026-07-19")
        self._refresh_lands(fresh)
        _app.processEvents()
        self.assertIn("Delta", [r["title"] for r in _win.model._rows])
        self.assertIn("(4)", _win.sb_tree.topLevelItem(0).text(0))
        # Still parked on Collection — the refresh must not steal the view.
        from desktop.trees import COLLECTION_ROLE
        self.assertTrue(_win.sb_tree.currentItem().data(0, COLLECTION_ROLE))

    def test_selected_track_keeps_its_seat(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == "3")
        _win.table.setCurrentIndex(_win.model.index(row, 0))
        self._refresh_lands(_make_lib())
        _app.processEvents()
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], "3")

    def test_tag_refresh_keeps_all_surviving_selected_rows(self):
        from PySide6.QtCore import QItemSelectionModel
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        selection=_win.table.selectionModel();selection.clearSelection()
        for row,data in enumerate(_win.model._rows):
            if data['id'] in ('1','3'):
                selection.select(_win.model.index(row,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        self.assertEqual(set(_win._selected_ids()),{'1','3'})
        with mock.patch.object(_win,'_on_refresh_done',side_effect=self._refresh_lands), \
                mock.patch.object(_win,'_refresh_rekordbox'):
            _win._tag_saved(_make_lib())
            _app.processEvents()
        self.assertEqual(set(_win._selected_ids()),{'1','3'})

    def test_refresh_waits_for_an_accepted_tag_write(self):
        pending=_win._refresh_pending
        _win.tag_controller.busy=True
        try:
            with mock.patch.object(_win,'_start_rekordbox_refresh') as start:
                _win._refresh_rekordbox()
                self.assertTrue(_win._refresh_pending)
                start.assert_not_called()
        finally:
            _win.tag_controller.busy=False
            _win._refresh_pending=pending


class StateMaintenance(unittest.TestCase):
    def _host(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from desktop.settings_page import SettingsPageMixin
        events = []
        host = SimpleNamespace(
            _state_maintenance=False, _state_publication=False,
            _backup_worker=None, _device_coordinator=SimpleNamespace(active=None),
            _state_repository=SimpleNamespace(writable=True), _shutting_down=False,
            _dev_timer=QTimer(), _warm_timer=QTimer(),
            _waveform_recheck_timer=QTimer(), _volume_refresh_timer=QTimer(),
            _settings_save_timer=QTimer(),
            _start_art_fill=lambda: None, _maybe_fill_waveforms=lambda: None,
            _repair_import_locations=lambda: None,
            setEnabled=lambda value: events.append(('enabled', value)),
            _start_pulse=lambda text: None, _stop_pulse=lambda: None,
            _flash_status=lambda *args: events.append(('status', args)),
            _save_state=lambda: events.append(('save', None)) or True,
        )
        host._end_state_maintenance = lambda: SettingsPageMixin._end_state_maintenance(host)
        return host, events

    def test_pending_usb_view_preferences_flush_before_the_backup_restore_barrier(self):
        from types import SimpleNamespace
        from desktop.settings_page import SettingsPageMixin
        host, events = self._host()
        def flush():
            events.append(('view-preferences', None))
            host._settings_save_timer.start(120)
        host.usb_device_pages = SimpleNamespace(_remember_view=flush)
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
            self.assertEqual(events, [('view-preferences', None), ('enabled', False), ('save', None)])
            self.assertFalse(host._settings_save_timer.isActive())
            self.assertTrue(host._state_publication)
        finally:
            host._end_state_maintenance()

    def test_maintenance_drains_late_artwork_before_final_save_with_events_alive(self):
        import threading
        from PySide6.QtCore import QTimer
        from desktop.workers import OneShotTask
        from desktop.settings_page import SettingsPageMixin
        host, events = self._host()
        release, started = threading.Event(), threading.Event()

        def operation():
            started.set()
            return release.wait(3) and 'artwork completed'

        worker = OneShotTask(operation)
        host._art_worker = worker
        worker.done.connect(lambda value: events.append(('artwork', value)))
        worker.start()
        self.assertTrue(started.wait(1))
        QTimer.singleShot(30, lambda: (events.append(('heartbeat', None)), release.set()))
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
            self.assertFalse(worker.isRunning())
            self.assertEqual([row[0] for row in events],
                             ['enabled', 'heartbeat', 'artwork', 'save'])
            self.assertTrue(host._state_publication)
        finally:
            release.set()
            worker.wait()
            host._end_state_maintenance()
        self.assertFalse(host._state_maintenance)

    def test_failed_final_save_prevents_state_task(self):
        from desktop.settings_page import SettingsPageMixin
        host, events = self._host()
        host._save_state = lambda: False
        self.assertFalse(SettingsPageMixin._begin_state_maintenance(host))
        self.assertFalse(host._state_publication)
        self.assertFalse(host._state_maintenance)
        self.assertEqual(events[-1], ('enabled', True))

    def test_nested_metadata_builder_joins_and_publishes_before_restore_barrier(self):
        import threading
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from desktop.task_owner import TaskOwner
        from desktop.workers import OneShotTask
        from desktop.settings_page import SettingsPageMixin
        host,events=self._host();owner=TaskOwner()
        host.metadata_controller=SimpleNamespace(tasks=owner)
        started,release=threading.Event(),threading.Event()
        def build():
            started.set();release.wait(3);return 'frozen metadata'
        worker=OneShotTask(build)
        def published(task):
            self.assertFalse(host._state_publication)
            self.assertTrue(task.isFinished())
            events.append(('metadata',task.result))
        owner.adopt(worker,published);worker.start()
        self.assertTrue(started.wait(1))
        QTimer.singleShot(20,release.set)
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host,restoring=True))
            self.assertEqual(owner.pending,0)
            self.assertLess([e[0] for e in events].index('metadata'),[e[0] for e in events].index('save'))
            self.assertTrue(host._state_publication)
        finally:
            release.set();owner.drain();host._end_state_maintenance();owner.deleteLater()

    def test_maintenance_waits_for_an_accepted_tag_save_before_final_state_save(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from desktop.settings_page import SettingsPageMixin
        host, events=self._host()
        host.tag_controller=SimpleNamespace(busy=True)
        def finish():
            events.append(('tags-finished',None))
            host.tag_controller.busy=False
        QTimer.singleShot(20,finish)
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
            self.assertLess([e[0] for e in events].index('tags-finished'),
                            [e[0] for e in events].index('save'))
        finally:
            host._end_state_maintenance()

    def test_explicit_restore_can_recover_a_write_disabled_library(self):
        from desktop.settings_page import SettingsPageMixin
        host, _events = self._host()
        host._state_repository.writable = False
        host._save_state = mock.Mock(side_effect=AssertionError('must preserve damaged file'))
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host, restoring=True))
            host._save_state.assert_not_called()
        finally:
            host._end_state_maintenance()

    def test_active_device_work_prevents_state_maintenance(self):
        from desktop.settings_page import SettingsPageMixin
        host, events = self._host()
        host._device_coordinator.active = object()
        self.assertFalse(SettingsPageMixin._begin_state_maintenance(host))
        self.assertFalse(host._state_maintenance)
        self.assertEqual(events[0][0], 'status')

    def test_save_failure_is_visible_even_when_caller_ignores_return(self):
        with mock.patch.object(_win._state_repository, 'save', side_effect=OSError('full')), \
                mock.patch.object(_win, '_flash_status') as flash:
            self.assertFalse(_win._save_state())
        self.assertIn('NOT saved', flash.call_args.args[0])
        self.assertIn('Library not saved', _win.windowTitle())
        self.assertTrue(_win._save_state())
        self.assertEqual(_win.windowTitle(), 'Soundbox')


class DualFailureEvidence(unittest.TestCase):
    def test_failure_dialog_uses_transaction_evidence(self):
        from desktop.app import MainWindow
        from soundbox.dual_ipod import DualIpodError
        from soundbox.operation_control import CommittedOperationError
        cases = [
            ('not needed', 'No device files were changed', 'rollback'),
            ('rolled back', 'rollback completed', 'No device files'),
            ('recovery required', 'rollback was incomplete', 'No device files'),
        ]
        for status, expected, forbidden in cases:
            with self.subTest(status=status), mock.patch('desktop.app.QMessageBox.critical') as dialog:
                error = DualIpodError('Cannot read source: fixture.wav')
                error.recovery_status = status
                MainWindow._show_dual_ipod_failure(_win, error)
                message = dialog.call_args.args[2]
                self.assertIn(expected, message)
                self.assertNotIn(forbidden, message)
        with mock.patch('desktop.app.QMessageBox.critical') as dialog:
            MainWindow._show_dual_ipod_failure(_win, OSError('unknown failure'))
            self.assertIn('could not be established', dialog.call_args.args[2])
        with mock.patch('desktop.app.QMessageBox.critical') as dialog:
            MainWindow._show_dual_ipod_failure(_win, CommittedOperationError('cleanup failed'))
            self.assertIn('were committed', dialog.call_args.args[2])


class RepeatedBackgroundWork(unittest.TestCase):
    def test_artwork_completion_waits_for_idle_without_scanning_on_cue_edges(self):
        try:
            with mock.patch.object(_win.model, 'refresh_art') as refresh, \
                    mock.patch.object(_win, '_refresh_devices') as devices, \
                    mock.patch.object(_win.player_bar, 'is_playing', return_value=True):
                _win._on_art_progress(1, 1)
                self.assertTrue(_win._art_status_timer.isActive())
                _win._flush_art_status()
                for active in (True, False) * 10:
                    _win._on_playback_active(active)
                refresh.assert_not_called()
                devices.assert_not_called()
            with mock.patch.object(_win.model, 'refresh_art') as refresh, \
                    mock.patch.object(_win.player_bar, 'is_playing', return_value=False):
                _win._flush_art_status()
                refresh.assert_called_once()
        finally:
            _win._art_status_timer.stop()

    def test_empty_idle_poll_never_starts_worker_but_retry_and_mount_do(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        calls = []
        stub = SimpleNamespace(
            _volume_directory_stamp=10, _device_mount_refresh_pending=False,
            _eject_ready_device="", _device_retry_at=float("inf"),
            device_tree=SimpleNamespace(topLevelItemCount=lambda: 0),
            _refresh_devices=lambda **kw: calls.append(kw))
        with mock.patch("desktop.app.os.stat", return_value=SimpleNamespace(st_mtime_ns=10)):
            for _ in range(100):
                MainWindow._poll_devices(stub)
            self.assertEqual(calls, [])
            stub._device_retry_at = 0
            MainWindow._poll_devices(stub)
            self.assertEqual(calls, [{}])
        with mock.patch("desktop.app.os.stat", return_value=SimpleNamespace(st_mtime_ns=11)):
            MainWindow._poll_devices(stub)
        self.assertEqual(calls[-1], {"force": True})

    def test_preference_burst_coalesces_and_curation_flushes_it(self):
        old = dict(_win.settings)
        try:
            with mock.patch.object(_win._state_repository, "save") as save:
                for zoom in (100, 120, 150):
                    _win._on_waveform_zoom_changed(zoom)
                _win._on_jump_mode_changed("4 BEATS")
                save.assert_not_called()
                self.assertTrue(_win._settings_save_timer.isActive())
                _win._save_state()  # same boundary used by curation/backup/shutdown
                save.assert_called_once()
                self.assertEqual(save.call_args.args[0]["settings"]["waveform_zoom"], 150)
                self.assertFalse(_win._settings_save_timer.isActive())
        finally:
            _win.settings = old
            _win._settings_save_timer.stop()

    def test_stock_art_warm_hits_skip_preparation_and_deleted_pixels_recover(self):
        from PIL import Image
        from desktop.workers import StockArtworkPrepWorker
        from soundbox.stock_artwork import StockArtworkCache
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "cover.jpg"
            Image.new("RGB", (64, 64), "blue").save(image)
            cache = StockArtworkCache(root / "stock")
            prepared = cache.prepare(image)
            StockArtworkPrepWorker(None, None, cache, images=[image]).run()
            cache = StockArtworkCache(root / "stock")
            with mock.patch.object(cache, "prepare", wraps=cache.prepare) as prepare, \
                    mock.patch("desktop.workers.time.sleep", side_effect=AssertionError("no pacing on hits")):
                StockArtworkPrepWorker(None, None, cache, images=[image]).run()
                prepare.assert_not_called()
                prepared.payloads[0].path.unlink()
                StockArtworkPrepWorker(None, None, cache, images=[image]).run()
                prepare.assert_called_once()
            self.assertTrue(prepared.payloads[0].path.is_file())
            cache.prepare(image)  # exact selected-sync verification still succeeds

    def test_failed_discovery_backoff_is_bounded_and_success_resets_it(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        worker = SimpleNamespace(retry_discovery=True, results=None)
        host = SimpleNamespace(
            _dev_scan_worker=worker, _finish_worker=lambda *args: None,
            _retire_device_scan_worker=lambda captured: MainWindow._retire_device_scan_worker(host, captured),
            _deliver_device_scan_callbacks=lambda worker: MainWindow._deliver_device_scan_callbacks(host, worker),
            _shutting_down=False, _device_coordinator=SimpleNamespace(eject_pending=False),
            _stock_view_pending={}, _stock_view_worker=None)
        with mock.patch('desktop.app.time.monotonic', return_value=100):
            for delay in (3, 6, 12, 24, 30, 30):
                MainWindow._finish_device_scan_worker(host, worker)
                self.assertEqual(host._device_retry_at, 100 + delay)
            worker.retry_discovery = False
            MainWindow._finish_device_scan_worker(host, worker)
        self.assertEqual(host._device_retry_at, float('inf'))
        self.assertEqual(host._device_retry_delay, 3)


class ProviderProfiles(unittest.TestCase):
    def test_public_commands_and_menus_preserve_local_actions(self):
        from soundbox.providers import Providers
        from PySide6.QtCore import QPoint
        old = _win.providers
        _win.providers = Providers('public')
        try:
            with mock.patch('desktop.app.ArtFillWorker') as fill, \
                    mock.patch.object(_win, '_run_oneshot') as fetch, \
                    mock.patch.object(_win.art_cache, 'retry_weak') as rescan:
                _win._start_art_fill()
                _win._maybe_autostart_art_fill()
                _win._rescan_covers()
                _win._suggest_artists()
                _win._art_fetch_one('1')
                _win._art_from_url('1')
                fill.assert_not_called()
                fetch.assert_not_called()
                rescan.assert_not_called()
            actions = []
            menu = mock.Mock()
            menu.addAction.side_effect = lambda text, *args: (actions.append(text),mock.Mock())[1]
            with mock.patch('desktop.app.QMenu', return_value=menu):
                _win._rb_header_menu(_win.tree, QPoint())
            self.assertIn('Suggest artist merges…', actions)
            self.assertFalse(any('cover' in a or 'Unknowns' in a for a in actions))
            from desktop.artistcatalog import ArtistCatalogDialog
            catalog = ArtistCatalogDialog([('Artist', ['1'], None)])
            self.assertTrue(catalog.url_btn.isHidden())
            with mock.patch('desktop.artistcatalog.QInputDialog.getText') as prompt:
                catalog._use_url()
                prompt.assert_not_called()
            catalog.close()
        finally:
            _win.providers = old

    def test_detach_reattach_preserves_curation_art_blocks_settings_and_analysis(self):
        import hashlib
        from PIL import Image
        import io
        from desktop.app import MainWindow
        from soundbox.artwork import ArtCache, ArtResult
        from soundbox.providers import Providers
        with tempfile.TemporaryDirectory(prefix='sb-profile-state-') as area:
            root = Path(area)
            original = {'custom': {'Favorites': ['1']}, 'title_overrides': {'1': 'Chosen'},
                        'artist_overrides': {'1': 'Artist'},
                        'extensions': {'lowfreqlabs.soundcloud': {'schema': 1, 'preference': 'keep'}},
                        'settings': {'future_provider_setting': {'enabled': True}}}
            (root / 'library.json').write_text(json.dumps(original))
            cache = ArtCache(root / 'art')
            pixels = io.BytesIO()
            Image.new('RGB', (24,24), 'blue').save(pixels, 'PNG')
            cache.store_ok('1', ArtResult(pixels.getvalue(), 'image/png', 1.0, 'file:fixture', 'Chosen'))
            cache.block('2')
            cache.normalize_all()
            (root / 'track_analysis').mkdir()
            analysis = root / 'track_analysis' / 'untouched.fixture'
            analysis.write_bytes(b'opaque existing analysis')
            def hashes():
                return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for directory in ('art', 'track_analysis')
                        for p in (root / directory).rglob('*') if p.is_file()}
            before = hashes()
            for profile in (_win.providers, Providers('public'), _win.providers):
                win = MainWindow(providers=profile, isolated_dir=area)
                try:
                    win._save_state()
                    stored = json.loads((root / 'library.json').read_text())
                    for key in ('custom', 'title_overrides', 'artist_overrides', 'extensions'):
                        self.assertEqual(stored[key], original[key])
                    self.assertEqual(stored['settings']['future_provider_setting'], {'enabled': True})
                    self.assertEqual(win.art_cache.status('2'), 'blocked')
                    self.assertEqual(hashes(), before)
                    self.assertIsNone(win._wf_fill_worker)
                    self.assertIsNone(win._art_worker)
                finally:
                    win.shutdown()
                    win.deleteLater()
            _app.processEvents()
