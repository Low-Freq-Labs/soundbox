"""Hidden desktop/worker admission with TEMP state and no physical backend I/O."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTreeWidgetItem

from desktop.devices import DeviceFacts
from desktop.stock_device_view import CompositeDeviceData, StockIpodPages
from desktop.trees import DEVPATH_ROLE
from desktop.workers import OneShotTask
from soundbox import manifest
from soundbox.device_operations import DeviceOperationCoordinator, DeviceSessionIdentity
from soundbox.library import Playlist
from soundbox.stock_ipod_format import StockWriteFormat, DATABASE_PROFILE, ARTWORK_PROFILE
from soundbox.stock_ipod_catalog import StockCatalogTrackMatch, StockPlaylistRecognition
from soundbox.stock_ipod_info import (StockIpodPlaylistEntry, StockIpodPlaylistSnapshot,
                                     StockIpodSnapshot, StockIpodTrackSnapshot)
import tests.test_device_navigation_publication as navigation_fixture


PLAYLIST_ID = 10_469_178_251_650_046_358


def snapshot(target):
    tracks = tuple(StockIpodTrackSnapshot(
        index, index, f'Song {index}', 'Artist', '', '', 'WAV', 100, 1000,
        1411, 44100, 0, 0, 0, 0, 0, f':iPod_Control:Music:F00:{index}.wav', 0, 0)
        for index in (1, 2))
    entries = tuple(StockIpodPlaylistEntry(row.dbid, row.title, row.artist) for row in tracks)
    return StockIpodSnapshot(Path(target), 'Fixture iPod', 'fixture-uuid', 'MS-DOS FAT32', 'USB',
        'fixture-disk', True, 1000, 500, 'MA446', 'confirmed', 'iPod Video', '5.5',
        'fixture-identity', 1, 2, 0x75, 0, 'database-before', 200, 0, '', 0,
        tracks, (StockIpodPlaylistSnapshot(PLAYLIST_ID, 'Favorites', 'Playlist', False, entries),),
        stock_format=StockWriteFormat(DATABASE_PROFILE,ARTWORK_PROFILE),usb_family='iPod Video')


class IpodArtworkAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Compose its isolated setup; do not inherit or rediscover its tests.
        fixture = navigation_fixture.DeviceNavigationPublication()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.window = fixture.window
        w = self.window
        for timer in w.findChildren(QTimer):
            timer.stop()
        self.target = w._data_file.parent / 'generated-device'
        self.target.mkdir()
        self.device = str(self.target)
        self.facts = DeviceFacts(volume_uuid='fixture-uuid', writable=True)
        self.enterContext(mock.patch('desktop.stock_catalog_sync.device_facts', side_effect=lambda *_a, **_k: self.facts))
        self.enterContext(mock.patch('desktop.stock_catalog_sync.device_fs', return_value='msdos'))
        self.enterContext(mock.patch('desktop.app.device_fs',return_value='msdos'))
        self.enterContext(mock.patch('desktop.app.device_facts', side_effect=lambda *_a, **_k: self.facts))
        self.question = self.enterContext(mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes))
        self.warning = self.enterContext(mock.patch.object(QMessageBox, 'warning'))
        self.information = self.enterContext(mock.patch.object(QMessageBox, 'information'))
        w.lib.playlists = [Playlist('Favorites', [], ['1', '2'], 'native-favorites')]
        w.lib.source_playlist_catalog={'nodes':[dict(ID='native-favorites',UUID='playlist-uuid',rb_local_deleted=0)],'members':[],'revision':'a'*64}
        w._stock_catalog_witness_cache={}
        w.__dict__['stock_catalog_witness_store']=mock.Mock()
        w.settings['stock_ipod_models'] = {'fixture-uuid:0000000000000002': 'MA446'}
        w.__dict__['stock_art_cache'] = object()
        w.__dict__['rockbox_media_cache'] = object()
        self.refreshes = []
        def refresh(device, *, after_finished, **_options):
            self.refreshes.append(device)
            after_finished()
        self.enterContext(mock.patch.object(w, '_refresh_device_surfaces', side_effect=refresh))
        self.stock_plan = SimpleNamespace(requires_commit=True, catalog_recognition=(
            StockPlaylistRecognition('native-favorites', 'Favorites', 2, PLAYLIST_ID,
                tuple(StockCatalogTrackMatch(str(index), index, index,
                    f':iPod_Control:Music:F00:{index}.wav') for index in (1, 2))),))
        self.dual_plan = SimpleNamespace(requires_commit=True, stock=self.stock_plan)
        self.stock_result = SimpleNamespace(shadow_warning='', cleanup_failures=())
        self.dual_result = SimpleNamespace(stock=self.stock_result, cleanup_failures=())
        self.plan_stock = self.enterContext(mock.patch('soundbox.stock_ipod.plan_stock_ipod_catalog_update',
                                                      return_value=self.stock_plan))
        self.plan_dual = self.enterContext(mock.patch('soundbox.dual_ipod.plan_dual_ipod_catalog_update',
                                                     return_value=self.dual_plan))
        self.commit_stock = self.enterContext(mock.patch('soundbox.stock_ipod.commit_stock_ipod_sync',
                                                        return_value=self.stock_result))
        self.commit_dual = self.enterContext(mock.patch('soundbox.dual_ipod.commit_dual_ipod_sync',
                                                       return_value=self.dual_result))
        self.configure('stock_ipod')
        self.addCleanup(self.retire_workers)

    def configure(self, kind):
        w = self.window
        self.snapshot = snapshot(self.target)
        w._device_coordinator = DeviceOperationCoordinator()
        w._device_profiles = {self.device: SimpleNamespace(kind=kind, has_stock_ipod=True,
            model='MA446', library_persistent_id=2, label='Fixture iPod', stock_write=True,
            cdj_export=False, rockbox_export=True, filesystem='msdos',
            stock_format=self.snapshot.stock_format)}
        w._device_mount_tokens = {self.device: self.target.stat().st_dev}
        w._device_coordinator.observe(DeviceSessionIdentity(self.device, self.target.stat().st_dev))
        w._stock_view_cache = {self.device: (self.snapshot, None)}
        w._current_device_path = self.device
        w._stock_snapshot = self.snapshot
        w.settings['stock_ipod_models'][w._stock_device_key(self.device)] = 'MA446'
        item = QTreeWidgetItem(['Fixture iPod'])
        item.setData(0, DEVPATH_ROLE, self.device)
        w.device_tree.addTopLevelItem(item)
        w.device_tree.setCurrentItem(item)
        pages = w.stock_ipod_pages
        pages.set_snapshot(self.snapshot)
        pages.set_page(2)
        pages.playlist_tree.setCurrentItem(pages.playlist_tree.topLevelItem(0))
        w.device_content_stack.setCurrentWidget(pages)
        w.stack.setCurrentIndex(1)
        w._update_device_action_state()
        return pages

    def retire_workers(self):
        for name in ('_stock_plan_worker', '_stock_commit_worker'):
            worker = getattr(self.window, name, None)
            if worker is not None:
                worker.cancel()
                worker.wait(5000)
        self.app.processEvents()

    def drain(self):
        deadline = time.monotonic() + 5
        while any(getattr(self.window, name, None) is not None
                  for name in ('_stock_plan_worker', '_stock_commit_worker')):
            self.app.processEvents()
            self.assertLess(time.monotonic(), deadline, 'Artwork workers did not retire')
            time.sleep(.001)
        self.app.processEvents()

    def request(self):
        self.window.stock_ipod_pages.sync_playlist_button.click()

    def seat(self):
        w = self.window
        return (w.stack.currentIndex(), w.body_stack.currentIndex(),
            w.device_content_stack.currentWidget(), w.stock_ipod_pages.stack.currentIndex(),
            w._current_device_path, w.device_tree.currentItem())

    def test_stock_button_runs_owned_plan_commit_and_refresh_without_navigation(self):
        before = self.seat()
        self.request()
        worker = self.window._stock_plan_worker
        self.assertIsInstance(worker, OneShotTask)
        self.assertIs(self.window._device_coordinator.active.worker, worker)
        self.drain()
        self.plan_stock.assert_called_once()
        self.commit_stock.assert_called_once_with(self.stock_plan)
        self.plan_dual.assert_not_called()
        self.commit_dual.assert_not_called()
        self.assertIs(self.plan_stock.call_args.kwargs['snapshot'], self.snapshot)
        self.assertEqual(self.snapshot.filesystem, 'MS-DOS FAT32')
        self.assertEqual(self.plan_stock.call_args.kwargs['filesystem'], 'msdos')
        self.assertIs(self.plan_stock.call_args.kwargs['update_metadata'], True)
        self.assertEqual(self.refreshes, [self.device])
        self.assertEqual(self.seat(), before)
        self.assertTrue(self.window._device_coordinator.last_result.committed)
        self.assertEqual(self.window._device_coordinator.last_result.refresh, 'complete')

    def test_dual_routes_only_through_composite_plan_and_commit(self):
        self.configure('dual_boot')
        self.request()
        self.drain()
        self.plan_dual.assert_called_once()
        self.commit_dual.assert_called_once_with(self.dual_plan)
        self.plan_stock.assert_not_called()
        self.commit_stock.assert_not_called()
        self.assertIs(self.plan_dual.call_args.kwargs['rockbox_media_cache'],
                      self.window.rockbox_media_cache)
        self.assertEqual(self.refreshes, [self.device])

    def test_pending_or_paused_source_edits_do_not_block_catalog_artwork(self):
        w = self.window
        pending = [{'id': 'unadmitted-background-song', 'error': 'Paused background update'}]
        w.settings['pending_metadata_edits'] = pending
        self.addCleanup(w.settings.pop, 'pending_metadata_edits', None)
        with mock.patch('desktop.export_wait.defer_source_sync', side_effect=AssertionError('source barrier')):
            self.request()
            self.drain()
        self.assertIs(w.metadata_controller.pending, pending)
        self.commit_stock.assert_called_once()
        self.assertEqual(self.refreshes, [self.device])

    def test_capture_is_detached_before_a_delayed_worker_materializes_it(self):
        w = self.window
        w.titles = {'1': 'Captured title'}
        w.overrides = {'1': 'Captured artist'}
        w._invalidate_rows(['1'])
        original_start = OneShotTask.start
        held = []
        with mock.patch.object(OneShotTask, 'start', lambda worker: held.append(worker)):
            self.request()
        self.assertEqual(len(held), 1)
        w.lib.tracks_by_id['1'].name = 'Later native title'
        w.titles['1'] = 'Later saved title'
        w.overrides['1'] = 'Later saved artist'
        w._invalidate_rows(['1'])
        original_start(held[0])
        self.drain()
        library, playlists, device = self.plan_stock.call_args.args
        self.assertIsNot(library, w.lib)
        self.assertEqual(library.tracks_by_id['1'].name, 'Song 1')
        self.assertEqual(playlists[0].keys, ['1', '2'])
        self.assertEqual(device, self.device)
        self.assertEqual(self.plan_stock.call_args.kwargs['title_overrides'], {'1': 'Captured title'})
        self.assertEqual(self.plan_stock.call_args.kwargs['artist_overrides'], {'1': 'Captured artist'})
        self.commit_stock.assert_not_called()

    def test_cancel_after_plan_finishes_before_delivery_never_commits(self):
        self.request()
        worker = self.window._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        self.assertIs(worker.result, self.stock_plan)
        self.assertIs(self.window._stock_plan_worker, worker)
        self.window._cancel_export()
        self.drain()
        self.commit_stock.assert_not_called()
        self.assertFalse(self.window._exporting)
        self.assertFalse(self.window._cancelling)
        self.assertIsNone(self.window._device_coordinator.active)
        self.assertEqual(self.refreshes, [])
        self.warning.assert_not_called()
        self.assertTrue(self.window.cancel_btn.isHidden())
        self.assertIn('cancelled', self.window.status_label.text().lower())

    def test_shutdown_after_finished_plan_suppresses_its_continuation(self):
        self.request()
        worker = self.window._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        self.window.shutdown()
        self.app.processEvents()
        self.commit_stock.assert_not_called()
        self.assertTrue(self.window._shutting_down)
        self.assertEqual(self.refreshes, [])

    def test_cached_admission_rechecks_shutdown_maintenance_owner_and_changed_identity(self):
        w = self.window
        access=w._stock_write_access
        for change in ('shutdown', 'maintenance', 'owner', 'mount', 'uuid', 'source'):
            with self.subTest(change=change):
                # Each restored fixture state is published through the same
                # controller refresh as a real completed device operation.
                w._update_device_action_state()
                self.assertTrue(w.stock_ipod_pages.sync_playlist_button.isEnabled())
                self.question.reset_mock()
                original_library = w.lib
                original_token = w._device_mount_tokens[self.device]
                blocker = OneShotTask(lambda: None)
                def admit(*args):
                    result=access(*args)
                    if change == 'shutdown': w._shutting_down = True
                    elif change == 'maintenance': w._state_maintenance = True
                    elif change == 'owner': w._stock_plan_worker = blocker
                    elif change == 'mount': w._device_mount_tokens[self.device] += 1
                    elif change == 'uuid': self.facts = replace(self.facts, volume_uuid='replacement-uuid')
                    else: w.lib = replace(w.lib, source='/synthetic/replacement.db')
                    return result
                try:
                    with mock.patch.object(w,'_stock_write_access',side_effect=admit) as check:
                        self.request()
                    check.assert_called_once()
                    self.question.assert_not_called()
                    self.plan_stock.assert_not_called()
                    self.commit_stock.assert_not_called()
                    if change == 'owner': self.assertIs(w._stock_plan_worker, blocker)
                finally:
                    w._shutting_down = False
                    w._state_maintenance = False
                    w._stock_plan_worker = None
                    w._device_mount_tokens[self.device] = original_token
                    self.facts = replace(self.facts, volume_uuid='fixture-uuid')
                    w.lib = original_library
                    blocker.deleteLater()

    def test_changed_catalog_or_source_after_planning_refuses_commit(self):
        self.request()
        worker = self.window._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        self.window._stock_view_cache[self.device] = (replace(self.snapshot,
            database_sha256='newer-device-catalog'), None)
        self.drain()
        self.commit_stock.assert_not_called()
        self.assertEqual(self.refreshes, [])
        self.assertIn('changed', str(self.warning.call_args))

    def test_worker_start_failure_releases_owned_flags_and_slot(self):
        with mock.patch.object(OneShotTask, 'start', side_effect=RuntimeError('start refused')):
            self.request()
        self.assertIsNone(self.window._stock_plan_worker)
        self.assertIsNone(self.window._device_coordinator.active)
        self.assertFalse(self.window._exporting)
        self.assertFalse(self.window._cancelling)
        self.assertIn('start refused', str(self.warning.call_args))

    def test_completion_refreshes_original_target_without_reopening_it(self):
        self.request()
        worker = self.window._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        self.window._open_settings()
        before = self.seat()
        self.drain()
        self.assertEqual(self.seat(), before)
        self.assertEqual(self.refreshes, [self.device])

    def test_summary_counts_matched_duplicate_occurrences(self):
        w = self.window
        w.lib.playlists[0].keys = ['1', '1', '2']
        entries = self.snapshot.playlists[0].entries
        self.snapshot = replace(self.snapshot, playlists=(replace(self.snapshot.playlists[0],
            entries=(entries[0], entries[0], entries[1])),))
        w._stock_view_cache[self.device] = (self.snapshot, None)
        w._stock_snapshot = self.snapshot
        w.stock_ipod_pages.set_snapshot(self.snapshot)
        w.stock_ipod_pages.playlist_tree.setCurrentItem(w.stock_ipod_pages.playlist_tree.topLevelItem(0))
        self.stock_plan.catalog_recognition = (StockPlaylistRecognition(
            'native-favorites', 'Favorites', 3, PLAYLIST_ID,
            (StockCatalogTrackMatch('1', 1, 1, ':iPod_Control:Music:F00:1.wav'),), ('2',)),)
        self.request()
        self.drain()
        self.information.assert_not_called()
        details = w._last_device_completion['text']
        self.assertIn('matched 2 of 3 songs', details)
        self.assertIn('1 unmatched or unsupported song(s)', details)

    def test_hidden_device_action_cannot_admit_from_settings(self):
        self.window._open_settings()
        self.request()
        self.plan_stock.assert_not_called()
        self.commit_stock.assert_not_called()
        self.assertEqual(self.refreshes, [])


class IpodArtworkPlaylistActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pages(self, *, dual=False):
        pages = StockIpodPages()
        self.addCleanup(pages.deleteLater)
        current = snapshot('/synthetic/never-opened-device')
        # A raw catalog member without a visible song row still counts.
        current = replace(current, tracks=current.tracks[:1])
        composite = CompositeDeviceData(dict(schema=3, profile=manifest.COMPOSITE_EXPORT_PROFILE,
            playlists={}, files={}), {}) if dual else None
        pages.set_snapshot(current, composite)
        pages.playlist_tree.setCurrentItem(pages.playlist_tree.topLevelItem(0))
        return pages

    def test_button_uses_raw_stock_identity_and_count_on_unmanaged_dual_row(self):
        for dual in (False, True):
            with self.subTest(dual=dual):
                pages = self.pages(dual=dual)
                selected = []
                pages.sync_requested.connect(selected.append)
                row = pages.playlist_tree.topLevelItem(0)
                self.assertEqual(row.childCount(), 1)
                if dual: self.assertEqual(row.data(0, Qt.UserRole), '')
                self.assertTrue(pages.sync_playlist_button.isEnabled())
                pages.sync_playlist_button.click()
                self.assertEqual(selected, [(str(PLAYLIST_ID), 'Favorites', 2)])

    def test_context_artwork_action_keeps_clicked_playlist_after_selection_changes(self):
        pages = self.pages(dual=True)
        original = pages.playlist_tree.topLevelItem(0)
        other = QTreeWidgetItem(pages.playlist_tree, ['Other'])
        other.setData(0, Qt.UserRole + 1, '123')
        other.setData(0, Qt.UserRole + 2, 9)
        selected = []
        pages.sync_requested.connect(selected.append)
        def choose(menu, _position):
            pages.playlist_tree.setCurrentItem(other)
            menu.actions()[0].trigger()
        with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=original), \
                mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
            pages._playlist_context_menu(QPoint(1, 1))
        self.assertEqual(selected, [(str(PLAYLIST_ID), 'Favorites', 2)])

    def test_context_artwork_action_rejects_a_replaced_snapshot(self):
        pages = self.pages(dual=True)
        original = pages.playlist_tree.topLevelItem(0)
        selected = []
        pages.sync_requested.connect(selected.append)
        def choose(menu, _position):
            pages.set_snapshot(snapshot('/synthetic/replacement-device'))
            menu.actions()[0].trigger()
        with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=original), \
                mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
            pages._playlist_context_menu(QPoint(1, 1))
        self.assertEqual(selected, [])
