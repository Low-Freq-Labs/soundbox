"""Accepted auto artwork uses the same complete selected-image publication lane."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from desktop.track_info import TrackInfoDialog, TrackInfoMixin
from desktop.workers import ArtFillWorker
from soundbox.artwork import ArtCache
from tests.test_artwork_identity import library, result
from tests.test_save_shutdown import _Window


class Window(TrackInfoMixin, QWidget):
    pass


class ArtworkCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = Window()
        self.window.art_cache = ArtCache(Path(self.temp.name) / 'art').for_library(library())
        self.dialogs = []
        self.workers = []
        self.addCleanup(self.close)

    def close(self):
        for worker, release in self.workers:
            release.set()
            worker.stop()
            self.assertTrue(worker.wait(3000))
        for dialog in self.dialogs:
            dialog._owner.drain()
            dialog.deleteLater()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def dialog(self, *, current=None):
        cache = self.window.art_cache
        dialog = TrackInfoDialog({'id': '1', 'title': 'Song'}, parent=self.window,
            artwork=lambda: cache.image_path('1'), artwork_cache=cache,
            artwork_current=current)
        self.dialogs.append(dialog)
        return dialog

    def settle(self, predicate):
        until = time.monotonic() + 3
        while not predicate() and time.monotonic() < until:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def open_selected(self):
        window = self.window
        window.lib = library()
        window.stack = SimpleNamespace(currentIndex=lambda: 0)
        window._selected_track = lambda: window.lib.tracks_by_id['1']
        window._row_for = lambda track: {'id': track.track_id, 'title': track.name}
        window._save_track_info = Mock(return_value=True)
        window._art_pick = Mock()
        window._flash_status = Mock()
        with patch('desktop.track_info._exec_info', side_effect=self.dialogs.append):
            window._open_track_info()
        dialog = self.dialogs[-1]
        dialog.prepare_artwork()
        return dialog

    def test_open_info_survives_neighbor_refresh_with_draft_and_new_complete_cover(self):
        cache = self.window.art_cache
        cache.store_ok('1', result('red'))
        dialog = self.open_selected()
        dialog.controls['title'].setText('Unsaved title')
        refreshed = library()
        neighbor = library(tid='2', uuid='neighbor').tracks_by_id['2']
        refreshed.tracks_by_id['2'] = neighbor
        current = cache.for_library(refreshed)
        self.assertIsNot(current, cache)
        self.assertIs(current.scope_owner, cache.scope_owner)
        cache.retire()
        self.window.lib = refreshed
        self.window.art_cache = current
        current.store_ok('1', result('blue'))
        self.window._refresh_info_artwork(('1',))
        self.settle(lambda: not dialog._owner.pending)
        self.assertGreater(dialog._image.pixelColor(10, 10).blue(), 200)
        self.assertEqual(dialog.controls['title'].text(), 'Unsaved title')
        self.assertTrue(dialog.save_callback({'title': 'Unsaved title'}))
        self.window._save_track_info.assert_called_once_with('1', {'title': 'Unsaved title'})

    def test_real_info_factory_rejects_old_source_session_after_return_to_same_source(self):
        cache = self.window.art_cache
        cache.store_ok('1', result('red'))
        dialog = self.open_selected()
        cache.retire()
        other = cache.for_library(library('B', uuid='other'))
        other.retire()
        self.window.art_cache = other.for_library(library())
        self.window.art_cache.store_ok('1', result('blue'))
        self.assertFalse(dialog.save_callback({'title': 'Stale edit'}))
        self.window._save_track_info.assert_not_called()
        self.window._refresh_info_artwork(('1',))
        self.assertEqual(dialog._owner.pending, 0)
        self.assertGreater(dialog._image.pixelColor(10, 10).red(), 200)

    def test_info_does_not_wait_for_future_auto_fetch_but_accepted_cover_refreshes_completely(self):
        release = Event()
        entered = Event()
        artifact = Path(self.temp.name) / 'presence-only.wav'
        artifact.write_bytes(b'generated metadata-only presence fixture')
        lib = library(path=str(artifact))
        cache = self.window.art_cache.for_library(lib)
        self.window.art_cache = cache
        payload = result('blue')
        class Provider:
            def fetch_best(self, *_args):
                entered.set()
                release.wait(3)
                return payload
        worker = ArtFillWorker(lib, cache, {}, {}, provider=Provider())
        self.workers.append((worker, release))
        worker.DELAY = 0
        notified = []
        def accepted(tid):
            notified.append((tid, cache.status(tid)))
            self.window._refresh_info_artwork((tid,))
        worker.cover_changed.connect(accepted)
        worker.start()
        self.assertTrue(entered.wait(1))
        dialog = self.dialog()
        dialog.prepare_artwork()
        self.assertTrue(dialog._art_ready)
        self.assertTrue(dialog._image.isNull())
        self.assertFalse(release.is_set())
        release.set()
        self.settle(lambda: not dialog._image.isNull() and not dialog._owner.pending)
        self.assertEqual(notified, [('1', 'ok')])
        self.assertTrue(all(cover.pixmap() is not None and not cover.pixmap().isNull() for cover in dialog._covers))
        self.assertGreater(dialog._image.pixelColor(10, 10).blue(), 200)

    def test_auto_result_losing_to_manual_choice_never_emits_cover_publication(self):
        release = Event()
        entered = Event()
        artifact = Path(self.temp.name) / 'presence-only.wav'
        artifact.write_bytes(b'generated metadata-only presence fixture')
        lib = library(path=str(artifact))
        cache = self.window.art_cache
        class Provider:
            def fetch_best(self, *_args):
                entered.set()
                release.wait(3)
                return result('blue')
        worker = ArtFillWorker(lib, cache, {}, {}, provider=Provider())
        self.workers.append((worker, release))
        worker.DELAY = 0
        notified = []
        worker.cover_changed.connect(notified.append)
        worker.start()
        self.assertTrue(entered.wait(1))
        cache.store_ok('1', result('red'))
        release.set()
        self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.assertEqual(notified, [])
        self.assertEqual(cache.cover_snapshot('1').data, result('red').data)

    def test_source_aba_during_decode_cannot_publish_stale_prepared_image(self):
        cache = self.window.art_cache
        cache.store_ok('1', result('red'))
        release = Event()
        entered = Event()
        current = [True]
        dialog = self.dialog(current=lambda: current[0])
        def held():
            entered.set()
            release.wait(2)
            return result('red').data
        dialog._artwork = held
        dialog._load_artwork()
        self.assertTrue(entered.wait(1))
        current[0] = False
        cache.retire()
        b = cache.for_library(library('B', uuid='B'))
        self.window.art_cache = b.for_library(library())
        self.window.art_cache.store_ok('1', result('blue'))
        release.set()
        self.settle(lambda: not dialog._owner.pending)
        self.assertTrue(dialog._image.isNull())
        self.window._refresh_info_artwork(('1',))
        self.assertFalse(dialog._owner.pending)
        fresh = self.dialog()
        fresh.prepare_artwork()
        self.assertGreater(fresh._image.pixelColor(10, 10).blue(), 200)

    def test_first_prepared_frame_reads_correct_source_complete_cover(self):
        a = self.window.art_cache
        a.store_ok('1', result('red'))
        b = a.for_library(library('B', uuid='B'))
        b.store_ok('1', result('blue'))
        self.window.art_cache = b
        dialog = self.dialog()
        dialog.prepare_artwork()
        self.assertTrue(dialog._art_ready)
        self.assertEqual(dialog._owner.pending, 0)
        self.assertGreater(dialog._image.pixelColor(10, 10).blue(), 200)
        self.assertLess(dialog._image.pixelColor(10, 10).red(), 10)

    def test_identity_changed_before_first_frame_rejects_without_showing_blank_old_song(self):
        dialog = self.dialog(current=lambda: False)
        with patch.object(QDialog, 'exec') as shown:
            self.assertEqual(dialog.exec(), QDialog.Rejected)
        shown.assert_not_called()

    def test_production_fill_connection_refreshes_matching_open_info_after_accepted_save(self):
        window = _Window()
        self.addCleanup(window.deleteLater)
        release = Event()
        entered = Event()
        self.addCleanup(release.set)
        artifact = Path(self.temp.name) / 'production-presence-only.wav'
        artifact.write_bytes(b'generated fixture; no audio reads')
        window.lib = library(path=str(artifact))
        window.art_cache = ArtCache(Path(self.temp.name) / 'production-art').for_library(window.lib)
        class Provider:
            def fetch_best(self, *_args):
                entered.set()
                release.wait(3)
                return result('blue')
        window.providers = SimpleNamespace(allows=lambda _action: True, artwork=Provider())
        window._state_repository = SimpleNamespace(writable=True)
        window._art_worker = None
        window._art_restart = False
        window._art_status_timer = QTimer(window)
        window._art_status_timer.setInterval(60_000)
        window.player_bar = SimpleNamespace(is_playing=lambda: False)
        window.overrides = {}
        window.titles = {}
        window._on_art_progress = Mock()
        window._queue_stock_artwork = Mock()
        window._refresh_model_art = Mock()
        dialog = TrackInfoDialog({'id': '1'}, parent=window,
            artwork=lambda: window.art_cache.image_path('1'), artwork_cache=window.art_cache)
        self.dialogs.append(dialog)
        dialog.prepare_artwork()
        self.assertTrue(dialog._image.isNull())
        with patch.object(ArtFillWorker, 'DELAY', 0):
            window._start_art_fill()
            worker = window._art_worker
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(dialog._image.isNull())
                release.set()
                self.settle(lambda: window._art_worker is None and not dialog._owner.pending
                            and not dialog._image.isNull())
            finally:
                release.set()
                # The ordinary finished callback may already have deleted it.
                import shiboken6
                if shiboken6.isValid(worker):
                    worker.stop()
                    self.assertTrue(worker.wait(3000))
        self.assertGreater(dialog._image.pixelColor(10, 10).blue(), 200)
        window._refresh_model_art.assert_called()
        window._queue_stock_artwork.assert_called_once_with({'1'})

    def test_production_auto_notification_rejects_old_cache_and_source_aba(self):
        window = _Window()
        self.addCleanup(window.deleteLater)
        a = self.window.art_cache
        worker = SimpleNamespace(cache=a)
        window._art_worker = worker
        window.art_cache = a
        window._on_profile_art_changed = Mock()
        window._on_auto_art_changed(worker, '1')
        window._on_profile_art_changed.assert_called_once_with('1')
        window._on_profile_art_changed.reset_mock()
        b = a.for_library(library('B', uuid='B'))
        window.art_cache = b
        window._on_auto_art_changed(worker, '1')
        window.art_cache = b.for_library(library())
        window._on_auto_art_changed(worker, '1')
        window._on_profile_art_changed.assert_not_called()
