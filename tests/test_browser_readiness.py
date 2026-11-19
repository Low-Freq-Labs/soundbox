"""Prepared Artists data remains current without work on first navigation."""
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QObject, QStandardPaths, Signal
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.artists_browser import ArtistsBrowser, artist_rows
from desktop.trees import ARTIST_ROLE, ARTIST_TRACK_ROLE, ArtistsTree
from soundbox.library import Library, Track
from soundbox.providers import Providers


class ArtistsPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ArtistsTree()
        self.addCleanup(self.view.deleteLater)
        self.browser = ArtistsBrowser(self.view)

    def rows(self):
        return [dict(id='1', artist='Ann', title='Alpha', bpm='140.00'),
                dict(id='2', artist='Zed', title='Beta', genre='Dubstep')]

    def test_stable_artist_order_keeps_unknown_last_and_tie_order(self):
        rows = [dict(id='u', artist='Unknown Artist', title='A'),
                dict(id='2', artist='Ann', title='alpha'),
                dict(id='1', artist='ann', title='ALPHA')]
        self.assertEqual([row['id'] for row in artist_rows(rows)], ['2', '1', 'u'])
        self.assertEqual(rows[0]['id'], 'u')

    def test_collapsed_children_are_ready_and_navigation_never_constructs_rows(self):
        self.browser.replace(self.rows())
        first = self.view.topLevelItem(0)
        self.assertEqual(first.childCount(), 1)
        self.assertFalse(first.isExpanded())
        with mock.patch('desktop.artists_browser.QTreeWidgetItem', side_effect=AssertionError('late row')):
            self.browser.reset_navigation()
            first.setExpanded(True)
            self.browser.filter(['140'])
        self.assertFalse(first.isHidden())
        self.assertTrue(self.view.topLevelItem(1).isHidden())
        self.assertEqual(first.child(0).data(0, ARTIST_TRACK_ROLE), '1')

    def test_changed_rows_retain_expansion_current_column_and_multiple_selection(self):
        self.browser.replace(self.rows())
        first, second = self.view.topLevelItem(0), self.view.topLevelItem(1)
        first.setExpanded(True)
        self.view.setCurrentItem(first.child(0), 1)
        self.view.selectionModel().select(self.view.indexFromItem(second.child(0)),
                                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
        changed = self.rows()
        changed[0].update(title='Renamed', artist='New artist')
        self.browser.replace(changed)
        self.assertEqual(self.view.currentItem().data(0, ARTIST_TRACK_ROLE), '1')
        self.assertEqual(self.view.currentColumn(), 1)
        self.assertTrue(self.view.currentItem().parent().isExpanded())
        self.assertEqual({item.data(0, ARTIST_TRACK_ROLE) for item in self.view.selectedItems()}, {'1', '2'})
        self.assertEqual(self.view.currentItem().text(0), 'Renamed')

    def test_identical_rows_reuse_items_even_after_search_cache_was_populated(self):
        rows = self.rows()
        self.browser.replace(rows)
        before = self.view.topLevelItem(0)
        self.browser.filter(['alpha'])
        self.browser.replace(rows)
        self.assertIs(self.view.topLevelItem(0), before)

    def test_finished_song_and_artwork_updates_reuse_all_existing_items(self):
        rows = self.rows()
        self.browser.replace(rows)
        first = self.view.topLevelItem(0)
        song = first.child(0)
        with mock.patch('desktop.artists_browser.QTreeWidgetItem', side_effect=AssertionError('rebuilt rows')):
            rows[0]['bpm'] = '145.00'
            self.browser.replace(rows)
            self.browser.refresh_art({'1': 'cover', '2': 'blocked'})
        self.assertIs(self.view.topLevelItem(0), first)
        self.assertIs(first.child(0), song)
        self.assertEqual(song.text(3), '145.00')
        self.assertEqual(self.browser.children['Zed'][0]['art'], 'blocked')


class LibraryBrowserReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(QStandardPaths, 'isTestModeEnabled', return_value=True))
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        area = self.enterContext(tempfile.TemporaryDirectory())
        self.window = MainWindow(providers=Providers('public'), isolated_dir=Path(area))
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.shutdown)
        self.library = Library({
            '1': Track('1', 'Alpha', 'Ann', 'file:///missing/one.wav', bpm='140', added_ts=1),
            '2': Track('2', 'Beta', 'Zed', 'file:///missing/two.wav', bpm='120', added_ts=2),
        }, [], 'synthetic')

    def apply_library(self, library):
        self.window.lib = library
        self.window._apply_loaded_library()

    def ids(self):
        view = self.window.artists_view
        return [view.topLevelItem(i).child(j).data(0, ARTIST_TRACK_ROLE)
                for i in range(view.topLevelItemCount())
                for j in range(view.topLevelItem(i).childCount())]

    def test_library_materialization_prepares_before_first_show_or_artist_click(self):
        self.apply_library(self.library)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.ids(), ['1', '2'])
        with mock.patch.object(self.window, '_artist_rows', side_effect=AssertionError('late projection')):
            self.window._show_artists()
            self.window.artists_view.topLevelItem(0).setExpanded(True)
        self.assertEqual(self.window.stack.currentWidget(), self.window.artists_view)

    def test_queued_edits_coalesce_and_prepare_hidden_view_with_current_metadata(self):
        self.apply_library(self.library)
        self.window._show_collection()
        with mock.patch.object(self.window._artists_browser, 'replace',
                               wraps=self.window._artists_browser.replace) as replace:
            self.window.titles['1'] = 'Changed title'
            self.window.overrides['1'] = 'New artist'
            self.window._build_artist_registry()
            self.window._invalidate_rows(['1'])
            self.window._invalidate_rows(['1'])
            self.app.processEvents()
        replace.assert_called_once()
        view = self.window.artists_view
        self.assertEqual(view.topLevelItem(0).data(0, ARTIST_ROLE), 'New artist')
        self.assertEqual(view.topLevelItem(0).child(0).text(0), 'Changed title')
        self.assertEqual(self.window.stack.currentIndex(), 0)

    def test_visibility_and_replacement_library_drop_old_prepared_tracks(self):
        self.apply_library(self.library)
        self.window.hidden.add('1')
        self.window._apply_settings_to_lib()
        self.window._refresh_custom()
        self.assertEqual(self.ids(), ['2'])
        replacement = Library({'3': Track('3', 'Gamma', 'Other', 'file:///missing/three.wav')}, [], 'other')
        self.apply_library(replacement)
        self.assertEqual(self.ids(), ['3'])
        self.assertEqual(set(self.window._artist_children), {'Other'})
        self.apply_library(Library({}, [], 'empty'))
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.window._artist_children, {})

    def test_artwork_status_refreshes_prepared_rows_without_changing_browser_page(self):
        self.apply_library(self.library)
        self.window._show_collection()
        self.window.art_cache.block('1')
        self.window._refresh_model_art()
        self.app.processEvents()
        self.assertEqual(self.window._artist_children['Ann'][0]['art'], 'blocked')
        self.assertEqual(self.window.stack.currentIndex(), 0)

    def test_single_song_invalidation_derives_only_that_song(self):
        self.apply_library(self.library)
        self.window.settings['track_info'] = {'1': {'genre': 'New genre'}}
        with mock.patch.object(self.window, '_row_for', wraps=self.window._row_for) as derive:
            self.window._invalidate_rows(['1'])
            self.app.processEvents()
        self.assertEqual([call.args[0].track_id for call in derive.call_args_list], ['1'])
        self.assertEqual(self.window._artist_children['Ann'][0]['genre'], 'New genre')

    def test_metadata_publication_preserves_prepared_services_and_unrelated_rows(self):
        self.apply_library(self.library)
        window=self.window
        window._show_collection()
        self.app.processEvents()  # Retire initial-load discovery before observing the ACK.
        before=window._row_cache['2']
        incoming=replace(self.library,tracks_by_id={**self.library.tracks_by_id,
            '1':replace(self.library.tracks_by_id['1'],name='Saved title',source_cues=[{'time':3}])})
        window.lib=incoming
        with (mock.patch('desktop.app.imports.inject',side_effect=AssertionError('media reinjection')),
              mock.patch('desktop.app.browser_cache.load',side_effect=AssertionError('disk row cache')),
              mock.patch.object(window.grid_controller,'configure',side_effect=AssertionError('grid preparation')),
              mock.patch.object(window.graveyard_controller,'prepare_library',side_effect=AssertionError('graveyard preparation')),
              mock.patch.object(window.art_cache,'configure_default_tracks',side_effect=AssertionError('artwork preparation')),
              mock.patch.object(window.native_analysis_controller,'discover',side_effect=AssertionError('analysis discovery')),
              mock.patch.object(window.media_controller,'discover',side_effect=AssertionError('media discovery')),
              mock.patch.object(window,'_default_to_collection',side_effect=AssertionError('navigation reset'))):
            window._apply_loaded_library(already_projected=True,prepared_native_ids=('1',))
        self.app.processEvents()
        self.assertIs(window._row_cache['2'],before)
        self.assertEqual(window._row_cache['1']['title'],'Saved title')
        self.assertEqual(window._metadata_native_index['1'][2],'Saved title')
        self.assertEqual(window._artist_children['Ann'][0]['title'],'Saved title')

    def test_metadata_publication_rebuilds_other_rows_when_artist_context_changes(self):
        self.apply_library(self.library)
        window=self.window
        incoming=replace(self.library,tracks_by_id={**self.library.tracks_by_id,
            '1':replace(self.library.tracks_by_id['1'],artist='New artist')})
        window.lib=incoming
        with mock.patch.object(window,'_invalidate_rows',wraps=window._invalidate_rows) as invalidate:
            window._apply_loaded_library(already_projected=True,prepared_native_ids=('1',))
            self.app.processEvents()
        self.assertEqual(set(invalidate.call_args.args[0]),{'1','2'})
        self.assertIn('New artist',window._artist_children)

    def test_equal_effective_collection_does_not_reset_sort_filter_selection_or_scroll_on_ack(self):
        window=self.window
        window.titles['1']='Saved title'
        self.apply_library(self.library)
        window._show_collection()
        window.model.set_filter('Saved')
        window.table.selectRow(0)
        previous=window.model._rows
        selected=window.table.currentIndex()
        scrollbar=window.table.verticalScrollBar().value()
        resets=[]
        window.model.modelReset.connect(lambda:resets.append(True))
        window.lib=replace(self.library,tracks_by_id={**self.library.tracks_by_id,
            '1':replace(self.library.tracks_by_id['1'],name='Saved title')})
        window._apply_loaded_library(already_projected=True,prepared_native_ids=('1',))
        self.app.processEvents()
        self.assertEqual(resets,[])
        self.assertIs(window.model._rows,previous)
        self.assertEqual(window.table.currentIndex(),selected)
        self.assertEqual(window.table.verticalScrollBar().value(),scrollbar)
        self.assertEqual([r['id'] for r in window.model._rows],['1'])

    def test_background_estimate_updates_artists_even_outside_the_current_playlist(self):
        self.library.tracks_by_id['1'].bpm = ''
        self.apply_library(self.library)
        self.window.model.set_rows([])
        track = self.library.tracks_by_id['1']
        worker = SimpleNamespace(lib=self.library)
        with mock.patch.object(self.window, '_wf_fill_worker', worker):
            self.window._on_waveform_bpm_ready(worker, dict(
                track_id='1', source=str(self.library.source), source_kind=self.library.source_kind,
                library_id=self.library.source_library_id, path=str(track.path), track_size=track.size,
                native_id=track.native_id, uuid=track.source_uuid, bpm=128.0,
                epoch=[1, 2, 100, 4, 5], size=100, provenance='soundbox-zero-anchor'))
            self.app.processEvents()
        self.assertEqual(self.window._artist_children['Ann'][0]['bpm'], '128.00')
        self.assertTrue(self.window._artist_children['Ann'][0]['bpm_estimated'])

    def test_readonly_completions_cannot_prompt_publish_or_restart_during_stop_or_restore(self):
        self.apply_library(self.library)
        self.window.imported = {'1': {'path': '/expected/one.wav'}}
        self.window._row_cache.clear()
        result = SimpleNamespace(updates={'1': SimpleNamespace(
            expected_path='/expected/one.wav', path=Path('/new/one.wav'), bookmark='')})
        for flag in ('_shutting_down', '_state_maintenance'):
            with self.subTest(flag=flag), mock.patch.object(self.window, flag, True), \
                    mock.patch.object(self.window, '_stop_pulse', side_effect=AssertionError('late UI')), \
                    mock.patch.object(self.window, '_save_state', side_effect=AssertionError('late state write')), \
                    mock.patch.object(self.window, '_apply_relinks', side_effect=AssertionError('late relink')), \
                    mock.patch.object(self.window, '_device_operation_owned', side_effect=AssertionError('late device admission')), \
                    mock.patch('desktop.workers.OneShotTask', side_effect=AssertionError('late worker')):
                self.window._on_merge_scan(({}, []))
                self.window._on_import_locations_repaired(result)
                self.window._on_links_found({'1': Path('/new/one.wav')})
                self.window._on_rows_warmed({'late': {}}, self.window._cache_gen)
                self.window._do_warm()
                self.window._begin_stock_ipod_sync('/stopped/device', [])
                self.window._begin_dual_ipod_sync('/stopped/device', [])
                self.window._start_confirmed_dual_jobs('/stopped/device', [])
            self.assertEqual(self.window.imported['1']['path'], '/expected/one.wav')
            self.assertNotIn('late', self.window._row_cache)

    def test_shutdown_removes_global_filter_while_next_window_keeps_live_theme_editing(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from desktop import theme
        from desktop.theme_controller import install
        controller=install(self.app)
        original=(theme.PURPLE,theme.ON_ACCENT)
        self.addCleanup(lambda:(theme.set_accent(*original),controller.update()))
        class TrackedWindow(MainWindow):
            filter_event_count=0
            observe_filter_events=False
            def eventFilter(self,obj,event):
                if self.observe_filter_events:self.filter_event_count+=1
                return super().eventFilter(obj,event)
        area=self.enterContext(tempfile.TemporaryDirectory())
        stopped=TrackedWindow(providers=Providers('public'),isolated_dir=Path(area)/'old')
        self.addCleanup(stopped.deleteLater);self.addCleanup(stopped.shutdown)
        stopped.shutdown()  # Keep its QObject alive: deferred deletion is not retirement.
        stopped.observe_filter_events=True
        current=MainWindow(providers=Providers('public'),isolated_dir=Path(area)/'new')
        self.addCleanup(current.deleteLater);self.addCleanup(current.shutdown)
        current.show();current._open_settings();current.activateWindow();QTest.qWait(1)
        with mock.patch('desktop.settings_page.QColorDialog') as picker, \
                mock.patch.object(current,'_schedule_settings_save') as save:
            button=current._theme_color;button._start_edit()
            QTest.keyClicks(button.editor,'12ab34');QTest.keyClick(button.editor,Qt.Key_Return)
            self.assertEqual(current.settings['theme_accent'],'#12ab34')
            save.assert_called_once_with('theme_accent','theme_text_mode','theme_text_custom');picker.assert_not_called()
        self.assertEqual(stopped.filter_event_count,0)
        self.assertIs(install(self.app),controller)

    def test_device_request_after_finished_signal_runs_once_after_owner_retirement(self):
        class Scan(QObject):
            finished = Signal()
            results = []
            new_paths = ()
            retry_discovery = False
        worker = Scan()
        window = self.window
        worker.finished.emit()  # The real owner callback is queued but not delivered yet.
        callback = mock.Mock()
        def finish(attr, captured):
            if getattr(window, attr) is captured:
                setattr(window, attr, None)
        with mock.patch.object(window, '_dev_scan_worker', worker), \
                mock.patch.object(window, '_finish_worker', side_effect=finish), \
                mock.patch.object(window, '_on_devices_scanned'):
            self.assertTrue(window._defer_device_operation_until_scan_finishes(callback))
            callback.assert_not_called()
            window._finish_device_scan_worker(worker)
            window._finish_device_scan_worker(worker)
        callback.assert_called_once_with()
        self.assertIsNone(window._pending_device_operation)
        self.assertFalse(window._exporting)

    def test_queued_device_request_rejects_replaced_owner_and_changed_mount_or_volume(self):
        window = self.window
        target = '/synthetic/device'
        def finish(attr, captured):
            if getattr(window, attr) is captured:
                setattr(window, attr, None)
        for change in ('owner', 'mount', 'volume', 'cancelled', 'shutdown'):
            with self.subTest(change=change):
                worker = SimpleNamespace(results=[], new_paths=(), retry_discovery=False, cancellation=Event())
                callback = mock.Mock()
                facts = SimpleNamespace(volume_uuid='first')
                window._device_mount_tokens = {target: 7}
                with mock.patch.object(window, '_dev_scan_worker', worker), \
                        mock.patch.object(window, '_shutting_down', False), \
                        mock.patch.object(window, '_finish_worker', side_effect=finish), \
                        mock.patch.object(window, '_on_devices_scanned'), \
                        mock.patch('desktop.app.device_facts', return_value=facts):
                    window._defer_device_operation_until_scan_finishes(callback, target=target)
                    if change == 'owner':
                        replacement = SimpleNamespace()
                        window._dev_scan_worker = replacement
                    elif change == 'mount':
                        window._device_mount_tokens[target] = 8
                    elif change == 'volume':
                        facts.volume_uuid = 'second'
                    elif change == 'cancelled':
                        worker.cancellation.set()
                    else:
                        window._shutting_down = True
                    window._finish_device_scan_worker(worker)
                    if change == 'owner':
                        self.assertIs(window._dev_scan_worker, replacement)
                callback.assert_not_called()
                self.assertIsNone(window._pending_device_operation)
                self.assertFalse(window._exporting)


class PlayerStartupLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_queued_unavailable_diagnostic_is_cancelled_with_its_player(self):
        from desktop.player import PlayerBar
        errors = []
        ended = []
        with mock.patch('desktop.player.NativeDeckBackend', side_effect=RuntimeError('fixture failure')):
            player = PlayerBar()
        player.playback_error.connect(errors.append)
        player.track_ended.connect(ended.append)
        player._track_id = 'synthetic-ended'
        player._ordinary_playback_requested = True
        player._on_end_of_media()
        player.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        with mock.patch('sys.excepthook') as uncaught:
            self.app.processEvents()
        self.assertEqual(errors, [])
        self.assertEqual(ended, [])
        uncaught.assert_not_called()

    def test_queued_usb_scroll_restore_is_cancelled_with_its_page(self):
        from desktop.usb_device_view import UsbDevicePages
        pages = UsbDevicePages()
        pages._view_key = 'fixture'
        pages._states = {'fixture': {'scroll': 0, 'overview_scroll': 0}}
        pages._restore_view()
        pages.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        with mock.patch('sys.excepthook') as uncaught:
            self.app.processEvents()
        uncaught.assert_not_called()
