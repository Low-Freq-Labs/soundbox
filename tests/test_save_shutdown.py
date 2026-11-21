"""Quit waits for captured saves without stopping the usable application."""
from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QEventLoop, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from desktop.app import MainWindow
from soundbox.state_repository import read_state
from tests import test_device_navigation_publication as fixture


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

    def test_cmd_q_and_close_keep_active_backup_restore_ownership(self):
        self.window._state_maintenance=True
        event=QEvent(QEvent.Quit)
        self.assertTrue(self.window.eventFilter(self.app,event))
        close=QCloseEvent();self.window.closeEvent(close)
        self.assertFalse(close.isAccepted());self.window.shutdown.assert_not_called()

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

    def test_one_close_keeps_qt_alive_until_save_completion_then_closes_automatically(self):
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
        QTest.qWait(100)
        self.window.shutdown.assert_called_once_with()
        self.assertFalse(self.window.eventFilter(self.app, QEvent(QEvent.Quit)))

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

    def test_unsaved_playlist_and_tag_definition_admissions_block_quit_source_switch_and_restore(self):
        for name in ('playlist_controller','tag_definition_controller'):
            with self.subTest(controller=name):
                controller=SimpleNamespace(admission_pending=True,admission_error='Generated unsaved intention')
                with mock.patch.object(self.window,name,controller,create=True):
                    event=QCloseEvent();self.window.closeEvent(event)
                    self.assertFalse(event.isAccepted());self.window.shutdown.assert_not_called()
                    self.assertIn('unsaved',self.window._source_selection_reason())
                    self.window._state_publication=False;self.window._backup_worker=None
                    self.assertFalse(self.window._begin_state_maintenance(restoring=True))

    def test_refused_preference_admission_cannot_authorize_close(self):
        self.window.settings={'columns_state':'Newest unsaved columns'}
        self.window._settings_dirty_keys={'columns_state'}
        self.window._state_committer=SimpleNamespace(queue_preferences=mock.Mock(return_value=False),
            preferences_error=None,pending=False)
        event=QCloseEvent();self.window.closeEvent(event)
        self.assertFalse(event.isAccepted());self.window.shutdown.assert_not_called()
        self.assertEqual(self.window._settings_dirty_keys,{'columns_state'})

    def test_idle_unadmitted_metadata_proof_allows_normal_quit_preserves_intentions_and_other_save_gates(self):
        self.window.settings={'pending_metadata_edits':[{'request':None,'error':''}],
            'source_truth_migration':{'order':['frozen-pair'],'cursor':0,'unresolved':[]}}
        self.window.metadata_controller=SimpleNamespace(planning_pending=True,migration_pending=True,
            queue_error=lambda:'',can_quit_with_unadmitted_metadata=lambda:True)
        before=deepcopy(self.window.settings)
        self.assertFalse(self.window._defer_quit_for_saves())
        self.assertFalse(self.window.eventFilter(self.app,QEvent(QEvent.Quit)))
        self.window.settings['pending_prep_edits']=[{'error':''}]
        self.assertTrue(self.window._defer_quit_for_saves())
        self.window.settings.pop('pending_prep_edits')
        self.window.tag_controller.busy=True;self.window.tag_controller._busy_kind='metadata'
        self.assertTrue(self.window._defer_quit_for_saves())
        self.window.tag_controller.busy=False
        event=QCloseEvent();self.window.closeEvent(event)
        self.assertTrue(event.isAccepted());self.window.shutdown.assert_called_once_with()
        self.assertEqual(self.window.settings,before)


class FailedSourceQuit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        fixture.DeviceNavigationPublication.setUpClass()

    def setUp(self):
        self.case=fixture.DeviceNavigationPublication();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.w=self.case.window
        for timer in self.w.findChildren(QTimer):timer.stop()
        self.queue=[dict(error='Generated native conflict',operation_id='a'*32,
                         source='/synthetic/master.db',keep={'selection':['1','2']})]
        self.w.settings['rekordbox_playlist_edits']=deepcopy(self.queue)
        self.assertTrue(self.w._save_state())
        self.shutdown=self.enterContext(mock.patch.object(self.w,'shutdown'))

    def wait(self,predicate):
        for _ in range(1000):
            if predicate():return
            QTest.qWait(5)
        self.fail('Quit verification did not settle')

    def close(self):
        event=QCloseEvent();self.w.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.w._shutting_down)

    def test_failed_source_queue_is_verified_off_gui_retained_exactly_and_quit_finishes(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        repository=self.w._state_repository;original=repository.verify_selected_values
        main=threading.get_ident();threads=[]
        def verify(*args,**kwargs):
            threads.append(threading.get_ident());entered.set()
            if not release.wait(5):raise RuntimeError('Generated verification was not released')
            return original(*args,**kwargs)
        with mock.patch.object(repository,'verify_selected_values',side_effect=verify):
            with mock.patch.object(self.w,'_save_state',side_effect=AssertionError('Quit performed full GUI save')):
                self.close();self.wait(entered.is_set)
                self.shutdown.assert_not_called()
                self.assertEqual(self.w.settings['rekordbox_playlist_edits'],self.queue)
                release.set();self.wait(lambda:self.shutdown.called)
        self.assertTrue(threads);self.assertTrue(all(thread!=main for thread in threads))
        self.assertEqual(read_state(repository.path)['settings']['rekordbox_playlist_edits'],self.queue)
        self.assertEqual(self.w.settings['rekordbox_playlist_edits'],self.queue)
        self.shutdown.assert_called_once_with()

    def test_cmd_q_uses_the_same_verified_failure_retention(self):
        event=QEvent(QEvent.Quit)
        self.assertTrue(self.w.eventFilter(self.app,event))
        self.wait(lambda:self.shutdown.called)
        self.assertEqual(read_state(self.w._state_repository.path)['settings']['rekordbox_playlist_edits'],self.queue)

    def test_unsaved_failed_queue_never_authorizes_quit_or_overwrites_durable_intent(self):
        self.w.settings['rekordbox_playlist_edits'][0]['error']='New unsaved error'
        current=deepcopy(self.w.settings['rekordbox_playlist_edits'])
        self.close();self.wait(lambda:self.w._quit_saves.error is not None)
        self.shutdown.assert_not_called()
        self.assertEqual(self.w.settings['rekordbox_playlist_edits'],current)
        self.assertEqual(read_state(self.w._state_repository.path)['settings']['rekordbox_playlist_edits'],self.queue)

    def test_changed_ram_after_checkpoint_capture_rejects_old_close_proof(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        repository=self.w._state_repository;original=repository.verify_selected_values
        def verify(*args,**kwargs):
            entered.set()
            if not release.wait(5):raise RuntimeError('Generated verification was not released')
            return original(*args,**kwargs)
        with mock.patch.object(repository,'verify_selected_values',side_effect=verify):
            self.close();self.wait(entered.is_set)
            newer=[{**self.queue[0],'error':'Newer failed operation'}]
            self.w.settings['rekordbox_playlist_edits']=newer
            release.set();self.wait(lambda:self.w._quit_saves.error is not None)
        self.shutdown.assert_not_called()
        self.assertIs(self.w.settings['rekordbox_playlist_edits'],newer)

    def test_active_native_save_finishes_before_failed_queue_can_be_verified_for_quit(self):
        tags=self.w.tag_controller
        def clear():tags.busy=False;tags._busy_kind=''
        self.addCleanup(clear)
        tags.busy=True;tags._busy_kind='playlists'
        self.close();QTest.qWait(75)
        self.shutdown.assert_not_called();self.assertIsNone(self.w._quit_saves.checkpoint)
        tags.busy=False;tags._busy_kind='';tags.idle.emit()
        self.wait(lambda:self.shutdown.called)
        self.assertEqual(read_state(self.w._state_repository.path)['settings']['rekordbox_playlist_edits'],self.queue)

    def test_canonical_recovery_and_unsaved_ram_admission_refuse_quit(self):
        with mock.patch.object(self.w._state_repository,'recovery',object()):
            self.close();self.wait(lambda:self.w._quit_saves.error is not None)
        self.shutdown.assert_not_called()
        self.w.tag_controller.tag_state_lane.failed.append(({},OSError('Generated unsaved click')))
        self.close();QTest.qWait(75)
        self.shutdown.assert_not_called()
        self.assertTrue(self.w.tag_controller.tag_state_lane.failed)
        self.w.tag_controller.tag_state_lane.failed.clear()

    def failed_source_ack(self):
        key='rekordbox_playlist_edits';before=[{**self.queue[0],'error':''}]
        self.w.settings[key]=deepcopy(before);self.assertTrue(self.w._save_state())
        ack=self.w.playlist_controller.ack
        def failed(error):self.w.settings[key]=[{**before[0],'error':str(error)}]
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('Generated ACK storage failure')):
            self.assertTrue(ack.start(before,[],mock.Mock(),failed))
            self.wait(lambda:bool(ack.error))
        self.assertTrue(self.w.tag_controller.source_ack_pending)
        self.assertTrue(self.w.settings[key][0]['error'])
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][key],before)
        return ack,key,before

    def test_failed_source_ack_can_quit_with_exact_original_canonical_queue_and_unsaved_error_marker(self):
        ack,key,before=self.failed_source_ack()
        self.close();self.wait(lambda:self.shutdown.called)
        self.assertFalse(ack.pending);self.assertFalse(self.w.tag_controller.source_ack_pending)
        self.assertEqual(self.w.settings[key],before)
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][key],before)

    def test_failed_source_ack_can_quit_after_proving_already_durable_ack_tail(self):
        ack,key,_before=self.failed_source_ack()
        self.w._state_repository.save({'settings':{**self.w.settings,key:[]}})
        self.close();self.wait(lambda:self.shutdown.called)
        self.assertFalse(ack.pending);self.assertFalse(self.w.tag_controller.source_ack_pending)
        self.assertEqual(self.w.settings[key],[])
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][key],[])
