"""Quit waits for captured saves without stopping the usable application."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QEventLoop, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from desktop.app import MainWindow


class _Window(MainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.settings = {}
        self._shutting_down = False
        self._state_maintenance = False
        self._shutdown_waiting = False
        self._analysis_worker = None
        self._import_tasks = SimpleNamespace(pending=0)
        self.tag_controller = SimpleNamespace(busy=False, _busy_kind='', _cue_queue=[])
        self.grid_controller = SimpleNamespace(_queue=[], _active=None)
        self.playlist_controller = SimpleNamespace(closing=False, pump=mock.Mock())
        self._flash_status = mock.Mock()
        self.shutdown = mock.Mock()


class SaveBeforeQuit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = _Window()
        self.addCleanup(self.window.deleteLater)

    def test_queued_and_failed_saves_refuse_close_without_stopping_edits(self):
        for key in ('shared_import_jobs', 'analysis_jobs', 'native_analysis_jobs',
                    'pending_prep_edits', 'rekordbox_playlist_edits'):
            for error in ('', 'Exact source bytes changed'):
                with self.subTest(queue=key, error=error):
                    self.window.settings = {key: [dict(error=error)]}
                    before = deepcopy(self.window.settings)
                    event = QCloseEvent()
                    self.window.closeEvent(event)
                    self.assertFalse(event.isAccepted())
                    self.assertEqual(self.window.settings, before)
                    self.assertFalse(self.window._shutting_down)
                    self.assertFalse(self.window.playlist_controller.closing)
                    self.window.shutdown.assert_not_called()
                    status = self.window._flash_status.call_args.args[0]
                    self.assertIn(error or 'Finishing saves', status)

    def test_cmd_q_uses_the_same_gate_before_application_teardown(self):
        self.window.settings = {'pending_prep_edits': [dict(error='')]}
        event = QEvent(QEvent.Quit)
        self.assertTrue(self.window.eventFilter(self.app, event))
        self.assertFalse(event.isAccepted())
        self.window.shutdown.assert_not_called()
        self.assertFalse(self.window._shutting_down)

    def test_active_analysis_import_and_source_writes_defer_quit(self):
        mutations = (
            lambda: setattr(self.window, '_analysis_worker', object()),
            lambda: setattr(self.window._import_tasks, 'pending', 1),
            lambda: self.window.tag_controller._cue_queue.append(object()),
            lambda: self.window.grid_controller._queue.append(object()),
            lambda: setattr(self.window.grid_controller, '_active', object()),
            lambda: self.window.tag_controller.__dict__.update(busy=True, _busy_kind='registration'),
        )
        for mutate in mutations:
            with self.subTest(source=mutate):
                self.window._analysis_worker = None
                self.window._import_tasks.pending = 0
                self.window.tag_controller = SimpleNamespace(busy=False, _busy_kind='', _cue_queue=[])
                self.window.grid_controller = SimpleNamespace(_queue=[], _active=None)
                mutate()
                self.assertTrue(self.window._defer_quit_for_saves())
                self.window.shutdown.assert_not_called()

    def test_save_completion_keeps_qt_alive_and_next_quit_proceeds(self):
        self.window.settings = {'shared_import_jobs': [dict(error='')]}
        initial = QCloseEvent()
        self.window.closeEvent(initial)
        self.assertFalse(initial.isAccepted())
        events = []
        loop = QEventLoop()
        def saved():
            self.window.settings['shared_import_jobs'].clear()
            events.append('saved while app stayed open')
            loop.quit()
        QTimer.singleShot(0, saved)
        loop.exec()
        self.assertEqual(events, ['saved while app stayed open'])
        self.window.shutdown.assert_not_called()
        self.assertFalse(self.window.eventFilter(self.app, QEvent(QEvent.Quit)))
        final = QCloseEvent()
        self.window.closeEvent(final)
        self.assertTrue(final.isAccepted())
        self.window.shutdown.assert_called_once_with()

    def test_read_only_background_work_does_not_block_quit(self):
        self.window._wf = object()
        self.window._art_worker = object()
        self.window._dev_scan_worker = object()
        self.window.tag_controller.__dict__.update(busy=True, _busy_kind='probe')
        self.assertEqual(self.window._pending_save_reason(), '')
        self.assertFalse(self.window._defer_quit_for_saves())

    def test_grid_probe_is_read_only_but_a_waiting_or_active_grid_edit_is_a_save(self):
        self.window.grid_controller._active = ('probe', 'track')
        for kind in ('probe', 'grid'):
            with self.subTest(worker=kind):
                self.window.tag_controller.__dict__.update(busy=True, _busy_kind=kind)
                self.assertFalse(self.window._defer_quit_for_saves())
        self.window.grid_controller._queue = [SimpleNamespace(track_id='track')]
        self.assertTrue(self.window._defer_quit_for_saves())
        self.window.grid_controller._queue = []
        self.window.grid_controller._active = SimpleNamespace(track_id='track')
        self.assertTrue(self.window._defer_quit_for_saves())

    def test_explicit_teardown_does_not_turn_into_a_permanent_quit_lock(self):
        self.window.settings = {'pending_prep_edits': [dict(error='Saved readback needs review')]}
        self.window._shutting_down = True
        self.assertFalse(self.window._defer_quit_for_saves())
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_confirmed_removal_and_invalid_saved_queue_stay_open(self):
        for settings in ({'graveyard_delete_intent': {'error': ''}},
                         {'native_analysis_jobs': {'unknown': 'evidence'}}):
            self.window.settings = settings
            self.assertTrue(self.window._defer_quit_for_saves())
            self.window.shutdown.assert_not_called()
