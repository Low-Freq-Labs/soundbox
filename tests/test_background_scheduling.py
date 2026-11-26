"""Coalesced wakeups retain current durable work and close with their owner."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFrame
from PySide6.QtTest import QSignalSpy, QTest

from desktop.analysis import AnalysisMixin
from desktop.native_analysis import NativeAnalysisController, KEY
from desktop.shared_imports import SharedImportController
from desktop.tag_controller import TagEditController
from desktop.task_owner import DeferredCall
from desktop.graveyard import _retain_archive
from soundbox import analysis, analysis_jobs
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository
from soundbox.state_io import atomic_bytes


class BackgroundScheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def events(self):
        for _ in range(4):
            self.app.processEvents()

    def window(self):
        w = QFrame()
        self.addCleanup(w.deleteLater)
        w.settings = {}
        w._shutting_down = False
        w._refresh_worker = None
        w._state_maintenance = False
        w._device_operation_owned = lambda: False
        w._save_state = mock.Mock(return_value=True)
        w.lib = Library({}, [], Path('/fixture/first.db'),
                        source_kind='rekordbox-db', source_library_id='first')
        w.tag_controller = TagEditController(Path('/fixture/state'), w,
                                             store_factory=lambda _: None)
        w.tag_controller.verified = True
        return w

    def test_wakeups_coalesce_and_read_latest_queue_without_inline_work(self):
        pending = []
        delivered = []
        owner = QFrame()
        self.addCleanup(owner.deleteLater)
        call = DeferredCall(lambda: delivered.append(list(pending)), owner)
        for value in range(100):
            pending.append(value)
            call.schedule()
        self.assertEqual(delivered, [])
        self.events()
        self.assertEqual(delivered, [list(range(100))])
        pending.append(100)
        call.schedule()
        self.events()
        self.assertEqual(delivered[-1], list(range(101)))

    def test_stop_and_parent_deletion_reject_already_posted_delivery(self):
        for kind in ('stop', 'delete'):
            with self.subTest(kind=kind):
                owner = QFrame()
                callback = mock.Mock()
                call = DeferredCall(callback, owner)
                call.schedule()
                if kind == 'stop':
                    call.stop()
                    call.schedule()
                else:
                    owner.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.events()
                callback.assert_not_called()
                if kind == 'stop':
                    owner.deleteLater()

    def test_status_burst_discovers_current_source_once_and_persists_before_work(self):
        w = self.window()
        w.tag_controller.busy = True
        factory = mock.Mock()
        controller = NativeAnalysisController(w, factory=factory)
        self.addCleanup(controller.stop)
        permissions = mock.Mock(return_value=object())
        backend = SimpleNamespace(load_authority=permissions, authority_file='fixture.json')
        with mock.patch('desktop.native_analysis.publication', return_value=backend), \
                mock.patch.object(controller, 'discover', wraps=controller.discover) as discover:
            for _ in range(100):
                w.tag_controller.changed.emit()
            self.assertEqual(discover.call_count, 0)
            track = Track('second-song', 'Second', '', 'file:///fixture/second.wav',
                          source_native_id='123', source_uuid='00000000-0000-4000-8000-000000000123')
            w.lib = Library({track.track_id: track}, [], Path('/fixture/second.db'),
                            source_kind='rekordbox-db', source_library_id='second')
            record = dict(engine=analysis.ENGINE, path=str(track.path), size=100,
                          epoch=[1, 2, 100, 3, 4], sha256='0' * 64, duration=10,
                          bpm=120, key='8A', first_cue=None,
                          beats=[[i % 4 + 1, i * 500, 120] for i in range(20)])
            w.settings['analysis_results'] = {analysis.result_key(track.path): record}
            self.events()
            self.assertEqual(discover.call_count, 1)
            self.assertEqual(permissions.call_count, 2)
        self.assertEqual(len(controller.pending), 1)
        job = controller.pending[0]
        self.assertEqual((job['source'], job['library_id'], job['local_id']),
                         ('/fixture/second.db', 'second', 'second-song'))
        self.assertEqual(job['record'], record)
        self.assertIsNot(job['record'], record)
        w._save_state.assert_called_once_with()
        factory.assert_not_called()  # The real owned source lane is still busy.
        w.tag_controller.busy = False

    def test_stopped_analysis_controller_keeps_intents_and_drops_queued_wakes(self):
        w = self.window()
        controller = NativeAnalysisController(w, factory=mock.Mock())
        saved = [{'fixture': 'durable intention remains untouched'}]
        w.settings[KEY] = deepcopy(saved)
        with mock.patch.object(controller, 'discover') as discover, \
                mock.patch.object(controller, 'pump') as pump:
            controller.schedule()
            w.tag_controller.changed.emit()
            controller.stop()
            self.events()
            discover.assert_not_called()
            pump.assert_not_called()
        self.assertEqual(w.settings[KEY], saved)

    def test_import_idle_burst_posts_one_current_pump_and_stop_rejects_it(self):
        w = self.window()
        controller = SharedImportController(w)
        self.addCleanup(controller.stop)
        with mock.patch.object(controller, 'pump') as pump:
            for _ in range(100):
                w.tag_controller.idle.emit()
            self.assertEqual(pump.call_count, 0)
            self.events()
            pump.assert_called_once_with()
            pump.reset_mock()
            controller.schedule()
            controller.stop()
            self.events()
            pump.assert_not_called()


class GraveyardHistoryPublication(unittest.TestCase):
    def test_equal_history_skips_publication_but_changed_and_missing_bytes_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = StateRepository(root / 'library.json').area
            path = root / 'graveyard-identities/fixture.json'
            relative = Path('graveyard-identities/fixture.json')
            document = dict(format='fixture', tracks={'one': {'uuid': 'original'}})
            _retain_archive(area, relative, document)
            original = path.read_bytes()
            with mock.patch('desktop.graveyard.atomic_bytes', wraps=atomic_bytes) as write, \
                    mock.patch('os.fsync', wraps=os.fsync) as sync:
                for _ in range(10):
                    _retain_archive(area, relative, document)
                write.assert_not_called()
                sync.assert_not_called()
                path.write_bytes(original.replace(b'original', b'originaX'))
                _retain_archive(area, relative, document)
                self.assertEqual(write.call_count, 1)
                self.assertEqual(path.read_bytes(), original)
                changed = deepcopy(document)
                changed['tracks']['one']['uuid'] = 'new historical identity'
                _retain_archive(area, relative, changed)
                self.assertEqual(write.call_count, 2)
                self.assertNotEqual(path.read_bytes(), original)
                path.unlink()
                _retain_archive(area, relative, changed)
                self.assertEqual(write.call_count, 3)

    def test_history_publication_refuses_linked_or_oversized_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = StateRepository(root / 'state/library.json').area
            path = area.root / 'graveyard-identities/fixture.json'
            relative = Path('graveyard-identities/fixture.json')
            path.parent.mkdir()
            outside = root / 'unrelated-source'
            outside.write_bytes(b'preserve this unrelated file')
            path.symlink_to(outside)
            with self.assertRaises(ValueError):
                _retain_archive(area, relative, {})
            self.assertEqual(outside.read_bytes(), b'preserve this unrelated file')
            path.unlink()
            path.write_bytes(b'x' * 17)
            with mock.patch('desktop.graveyard._MAX_ARCHIVE_BYTES', 16), \
                    self.assertRaises(ValueError):
                _retain_archive(area, relative, {})
            self.assertEqual(path.read_bytes(), b'x' * 17)


class AnalysisRetryScheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        class Host(QFrame, AnalysisMixin):
            pass
        w = Host()
        self.addCleanup(w.deleteLater)
        w._shutting_down = False
        w._analysis_owner = SimpleNamespace(stopping=False)
        w._analysis_worker = None
        w._analysis_queue = []
        w._refresh_worker = None
        w._device_operation_owned = lambda: False
        w._analysis_source_busy = mock.Mock(return_value=True)
        return w

    def test_busy_burst_owns_one_retry_and_empty_queue_starts_none(self):
        w = self.window()
        w._start_next_analysis()
        self.assertFalse(hasattr(w, '_analysis_retry_timer'))
        w._analysis_source_busy.assert_not_called()
        w._analysis_queue = [('durable current work',)]
        for _ in range(100):
            w._start_next_analysis()
        self.assertEqual(w._analysis_source_busy.call_count, 100)
        w._analysis_source_busy.reset_mock()
        QTest.qWait(600)
        # One real retry dispatch; the previous implementation kept 100 chains.
        self.assertEqual(w._analysis_source_busy.call_count, 1)
        w._analysis_queue = []
        w._analysis_source_busy.reset_mock()
        QTest.qWait(600)
        w._analysis_source_busy.assert_not_called()
        self.assertFalse(w._analysis_retry_timer.isActive())

    def test_queued_retry_observes_replaced_queue_and_closes_without_losing_it(self):
        w = self.window()
        w._analysis_queue = [('obsolete queue snapshot',)]
        w._start_next_analysis()
        current = Track('current', 'Current', '', 'file:///fixture/current.wav')
        w.lib = Library({current.track_id: current}, [], 'synthetic')
        job = dict(id='current-job', track_id=current.track_id, state='queued')
        w.settings = {analysis_jobs.KEY: [job]}
        w._analysis_queue = [(w.lib, current, {}, job['id'])]
        w._analysis_source_busy.return_value = False
        w._analysis_prerequisite_ready = mock.Mock(return_value=False)
        retry = QSignalSpy(w._analysis_retry_timer.timeout)
        with mock.patch.object(analysis_jobs, 'matches', return_value=True):
            # Observe the owned timeout itself: a busy event loop can dispatch
            # its 500 ms timer after a fixed 600 ms wall-clock wait has ended.
            self.assertTrue(retry.wait(2000), 'The owned analysis retry did not fire')
        self.assertEqual(retry.count(), 1)
        w._analysis_prerequisite_ready.assert_called_once_with(current, job)
        queued = list(w._analysis_queue)
        w._shutting_down = True
        closing_retry = QSignalSpy(w._analysis_retry_timer.timeout)
        self.assertTrue(closing_retry.wait(2000), 'The closing analysis retry did not retire')
        self.assertEqual(closing_retry.count(), 1)
        w._analysis_prerequisite_ready.assert_called_once_with(current, job)
        self.assertEqual(w._analysis_queue, queued)
        self.assertEqual(w.settings[analysis_jobs.KEY], [job])
        self.assertFalse(w._analysis_retry_timer.isActive())
