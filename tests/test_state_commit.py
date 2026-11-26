"""Actual Qt canonical-commit ownership; every durable file stays in TEMP."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QListWidget, QScrollBar

from desktop.state_commit import StateCommitter
from desktop.app import MainWindow
from desktop.settings_page import SettingsPageMixin
from desktop.tag_controller import TagEditController
from soundbox import backup
from soundbox import state_repository
from soundbox.state_repository import StateRepository, VerificationChanged, read_state
from soundbox.state_io import StateChangedError


class QtStateCommits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.window = QFrame(); w = self.window
        w.settings = {'queue': [{'id': 'original'}], 'view': {'expanded': []}, 'obsolete': True}
        w.titles = {'song': 'Original'}; w.overrides = {}; w.aliases = {}
        w._state_publication = False; w._state_maintenance = False; w._shutting_down = False
        w._state_repository = StateRepository(self.root / 'state/library.json')
        w.tag_controller = TagEditController(self.root / 'state', w, store_factory=lambda _: None)
        self.committer = StateCommitter(w)
        self.saves = []
        def save():
            if w._state_publication:
                self.committer.defer_save(); return False
            self.saves.append(deepcopy(w.settings))
            w._state_repository.save({'settings': w.settings, 'title_overrides': w.titles,
                                     'artist_overrides': w.overrides, 'artist_aliases': w.aliases})
            return True
        w._save_state = save; save(); self.saves.clear()
        self.addCleanup(self.close)

    def close(self):
        self.committer.owner.drain()
        self.window.tag_controller.owner.drain()
        self.window.deleteLater()
        self.app.processEvents()

    def events_until(self, condition, seconds=3):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(1)
        self.assertTrue(condition(), 'Queued commit did not retire')

    @contextmanager
    def held_commit(self):
        entered, release = threading.Event(), threading.Event()
        original = self.window._state_repository.commit_patch
        threads = []
        def delayed(patch):
            threads.append(threading.get_ident()); entered.set()
            if not release.wait(3):raise RuntimeError('Commit fixture timed out')
            return original(patch)
        with mock.patch.object(self.window._state_repository, 'commit_patch', side_effect=delayed):
            try:
                yield entered, release, threads
            finally:
                release.set()
                self.events_until(lambda: not self.committer.pending)

    def submit(self, committed=None, failed=None):
        return self.committer.submit({('settings', 'queue'): [], ('title_overrides', 'song'): 'Saved'},
                                     (), committed or mock.Mock(), failed or mock.Mock())

    def test_domain_guard_refuses_admission_without_canonical_io(self):
        saved=mock.Mock();failed=mock.Mock()
        with mock.patch.object(self.window._state_repository,'commit_patch') as commit:
            self.assertTrue(self.committer.submit({('settings','queue'):[]},(),saved,failed,current=lambda:False))
        commit.assert_not_called();saved.assert_not_called();failed.assert_called_once()
        self.assertFalse(self.committer.pending);self.assertEqual(self.window.settings['queue'],[{'id':'original'}])

    def test_domain_guard_drift_after_durability_reports_saved_and_preserves_newer_ram(self):
        saved=mock.Mock();failed=mock.Mock();main=threading.get_ident();calls=[]
        valid=True
        def current():calls.append(threading.get_ident());return valid
        with self.held_commit() as (entered,release,_threads):
            self.assertTrue(self.committer.submit({('settings','queue'):[]},(),saved,failed,current=current))
            self.assertTrue(entered.wait(2));valid=False
            self.window.settings['queue']=[{'id':'newer'}];release.set()
        saved.assert_not_called();failed.assert_called_once()
        self.assertTrue(failed.call_args.args[0].durable)
        self.assertEqual(self.window.settings['queue'],[{'id':'newer'}])
        self.assertEqual(read_state(self.window._state_repository.path)['settings']['queue'],[])
        self.assertEqual(calls,[main,main]);self.assertFalse(self.window.tag_controller.state_commit_owned)

    def test_domain_guard_exception_at_delivery_releases_owned_source_and_reports_failure(self):
        saved=mock.Mock();failed=mock.Mock();broken=False
        def current():
            if broken:raise ValueError('TEMP invalid changed owner')
            return True
        with self.held_commit() as (entered,release,_threads):
            self.assertTrue(self.committer.submit({('settings','queue'):[]},(),saved,failed,current=current))
            self.assertTrue(entered.wait(2));broken=True;release.set()
            with self.assertLogs('soundbox',level='ERROR'):self.events_until(lambda:not self.committer.pending)
        saved.assert_not_called();failed.assert_called_once();self.assertTrue(failed.call_args.args[0].durable)
        self.assertFalse(self.window.tag_controller.state_commit_owned)

    def test_navigation_and_heartbeat_continue_without_early_gui_or_continuation(self):
        w = self.window; committed = mock.Mock(); failed = mock.Mock()
        navigation = QListWidget(w); navigation.addItems(['Collection', 'Favorites']); navigation.setCurrentRow(0)
        ticks = []; timer = QTimer(); timer.setInterval(1); timer.timeout.connect(lambda: ticks.append(1))
        with self.held_commit() as (entered, release, threads):
            self.assertTrue(self.submit(committed, failed)); self.assertTrue(entered.wait(2))
            timer.start()
            QTest.keyClick(navigation, Qt.Key_Down)
            QTest.qWait(15); self.app.processEvents(); timer.stop()
            self.assertEqual(navigation.currentRow(), 1)
            self.assertTrue(ticks)
            self.assertNotEqual(threads[0], threading.get_ident())
            self.assertTrue(w._state_publication)
            self.assertEqual(w.titles['song'], 'Original')
            self.assertEqual(read_state(w._state_repository.path)['title_overrides']['song'], 'Original')
            committed.assert_not_called(); failed.assert_not_called()
            release.set()
        self.assertEqual(w.titles['song'], 'Saved')
        self.assertEqual(w.settings['queue'], [])
        self.assertEqual(read_state(w._state_repository.path)['title_overrides']['song'], 'Saved')
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_commit_never_takes_area_lock_on_gui_thread(self):
        area = self.window._state_repository.area; original = area.lock; main = threading.get_ident()
        class WorkerLock:
            def __enter__(self):
                if threading.get_ident() == main:raise AssertionError('GUI acquired durable state lock')
                return original.__enter__()
            def __exit__(self, *args):return original.__exit__(*args)
        committed = mock.Mock(); failed = mock.Mock()
        with mock.patch.object(area, 'lock', WorkerLock()):
            self.assertTrue(self.submit(committed, failed))
            self.events_until(lambda: not self.committer.pending)
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_durable_failure_keeps_old_ram_and_does_not_acknowledge(self):
        w = self.window; settings = w.settings; titles = w.titles
        before = read_state(w._state_repository.path); committed = mock.Mock(); failed = mock.Mock()
        with mock.patch.object(w._state_repository.area, 'publish', side_effect=OSError('Injected disk full')):
            self.assertTrue(self.submit(committed, failed))
            self.events_until(lambda: not self.committer.pending)
        self.assertIs(w.settings, settings); self.assertIs(w.titles, titles)
        self.assertEqual(read_state(w._state_repository.path), before)
        committed.assert_not_called(); failed.assert_called_once()
        self.assertIsInstance(failed.call_args.args[0], OSError)
        self.assertFalse(w._state_publication); self.assertFalse(w.tag_controller.state_commit_owned)

    def test_source_lane_remains_reserved_through_durable_gui_continuation(self):
        tags = self.window.tag_controller; observations = []
        def committed():
            observations.append((tags.owned, tags.source_job_owned, tags.state_commit_owned,
                                 tags._run_source_job('metadata', mock.Mock(), mock.Mock())))
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit(committed)); self.assertTrue(entered.wait(2))
            self.assertTrue(tags.owned); self.assertTrue(tags.source_job_owned)
            self.assertFalse(tags._run_source_job('playlists', mock.Mock(), mock.Mock()))
            release.set()
        self.assertEqual(observations, [(True, True, True, False)])
        self.assertFalse(tags.state_commit_owned)

    def test_unrelated_navigation_preferences_survive_and_exact_leaves_save_after_commit(self):
        w = self.window; committed = mock.Mock(); failed = mock.Mock()
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit(committed, failed)); self.assertTrue(entered.wait(2))
            w.settings['custom_playlist_expanded'] = ['Genres']
            w.settings['columns_state'] = 'Favorites'
            self.assertTrue(self.committer.queue_preferences({
                'custom_playlist_expanded':['Genres'],'columns_state':'Favorites'}))
            self.assertEqual(self.saves, [])
            release.set()
        self.events_until(lambda: not self.committer.pending)
        self.assertEqual(self.saves, [])
        self.assertEqual(w.settings['custom_playlist_expanded'], ['Genres'])
        self.assertEqual(w.settings['columns_state'], 'Favorites')
        saved = read_state(w._state_repository.path)
        self.assertEqual(saved['settings']['custom_playlist_expanded'], ['Genres'])
        self.assertEqual(saved['settings']['columns_state'], 'Favorites')
        self.assertEqual(saved['settings']['queue'], [])
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_duplicate_admission_and_occupied_source_reservation_are_rejected(self):
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit()); self.assertTrue(entered.wait(2))
            self.assertFalse(self.submit()); release.set()
        token = object(); self.window.tag_controller.reserve_state_commit(token)
        self.assertFalse(self.submit())
        self.assertFalse(self.window._state_publication)
        self.assertFalse(self.committer.pending)
        self.window.tag_controller.release_state_commit(token)

    def test_real_navigation_preferences_queue_during_source_commit_without_gui_save_or_lock(self):
        w=self.window;w._state_committer=self.committer;w._settings_dirty_keys=set()
        w._settings_save_timer=QTimer(w);w._settings_save_timer.setSingleShot(True)
        w._settings_save_timer.setInterval(400)
        w._settings_save_timer.timeout.connect(lambda:MainWindow._flush_settings_preferences(w))
        navigation=QListWidget(w);navigation.addItems(['Collection','Favorites']);navigation.setCurrentRow(0)
        scroll=QScrollBar(Qt.Vertical,w);scroll.setRange(0,100)
        def moved(value):
            w.settings['columns_state']=str(value)
            MainWindow._schedule_settings_save(w,'columns_state')
        scroll.valueChanged.connect(moved)
        ticks=[];timer=QTimer(w);timer.setInterval(1);timer.timeout.connect(lambda:ticks.append(1))
        area=w._state_repository.area;lock=area.lock;main=threading.get_ident()
        class WorkerLock:
            def __enter__(self):
                if threading.get_ident()==main:raise AssertionError('Preference UI acquired canonical area lock')
                return lock.__enter__()
            def __exit__(self,*args):return lock.__exit__(*args)
        committed=mock.Mock();failed=mock.Mock()
        with (mock.patch.object(area,'lock',WorkerLock()),
              mock.patch.object(w._state_repository,'save',side_effect=AssertionError('Full synchronous preference save')),
              self.held_commit() as (entered,release,_threads)):
            self.assertTrue(self.submit(committed,failed));self.assertTrue(entered.wait(2))
            timer.start();QTest.keyClick(navigation,Qt.Key_Down)
            for value in (10,60,90):scroll.setValue(value)
            self.assertTrue(MainWindow._flush_settings_preferences(w))
            QTest.qWait(15);self.app.processEvents();timer.stop()
            self.assertTrue(ticks);self.assertFalse(release.is_set())
            self.assertEqual(navigation.currentRow(),1);self.assertEqual(scroll.value(),90)
            self.assertEqual(w.settings['columns_state'],'90')
            self.assertTrue(self.committer.preferences_pending);committed.assert_not_called()
            self.assertEqual(self.saves,[]);release.set()
        durable=read_state(w._state_repository.path)
        self.assertEqual(durable['settings']['columns_state'],'90')
        self.assertEqual(durable['settings']['queue'],[])
        self.assertEqual(w.settings['columns_state'],'90');self.assertEqual(self.saves,[])
        committed.assert_called_once_with();failed.assert_not_called()

    def test_newer_preferences_coalesce_while_old_preference_is_durable_without_clobbering_ram(self):
        w=self.window;observed=[];original=w._state_repository.commit_patch
        def commit(patch):
            values,_=patch.edits();observed.append(values[('settings','waveform_zoom')])
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=commit):
            with self.held_commit() as (entered,release,_threads):
                w.settings['waveform_zoom']=1
                self.assertTrue(self.committer.queue_preferences({'waveform_zoom':1}))
                self.events_until(entered.is_set)
                for value in (2,3):
                    w.settings['waveform_zoom']=value
                    self.assertTrue(self.committer.queue_preferences({'waveform_zoom':value}))
                self.assertEqual(w.settings['waveform_zoom'],3);self.assertEqual(observed,[])
                release.set()
        self.assertEqual(observed,[1,3])
        self.assertEqual(w.settings['waveform_zoom'],3)
        self.assertEqual(read_state(w._state_repository.path)['settings']['waveform_zoom'],3)

    def test_preference_failure_retains_dirty_value_and_retry_never_reports_early_success(self):
        w=self.window;w._flash_status=mock.Mock();w.settings['waveform_zoom']=2
        with mock.patch.object(w._state_repository.area,'publish',side_effect=OSError('Injected preference disk full')) as publish:
            self.assertTrue(self.committer.queue_preferences({'waveform_zoom':2}))
            self.events_until(lambda:self.committer.preferences_error is not None)
            self.assertTrue(self.committer.preferences_pending);self.assertTrue(self.committer.pending)
            for _ in range(3):self.app.processEvents()
            self.assertEqual(publish.call_count,1)
        self.assertNotIn('waveform_zoom',read_state(w._state_repository.path)['settings'])
        self.assertEqual(w.settings['waveform_zoom'],2)
        self.assertTrue(self.committer.queue_preferences({'waveform_zoom':2}))
        self.events_until(lambda:not self.committer.pending)
        self.assertIsNone(self.committer.preferences_error)
        self.assertEqual(read_state(w._state_repository.path)['settings']['waveform_zoom'],2)

    def test_preference_admission_detaches_raw_aliases_and_forbids_curational_namespaces(self):
        w=self.window;w.tag_controller.busy=True;value={'flags':['first']}
        w.settings['analysis']=value
        self.assertTrue(self.committer.queue_preferences({'analysis':value}))
        value['flags'].append('later')
        for key in ('pending_metadata_edits','source_truth_targets','title_rules_off','title_words','rekordbox_db_path'):
            with self.subTest(key=key),self.assertRaises(ValueError):self.committer.queue_preferences({key:[]})
        w.tag_controller.busy=False;w.tag_controller.idle.emit()
        self.events_until(lambda:not self.committer.pending)
        self.assertEqual(read_state(w._state_repository.path)['settings']['analysis'],{'flags':['first']})
        self.assertEqual(w.settings['analysis'],{'flags':['first','later']})

    def test_restored_generation_refuses_queued_preferences_without_replaying_them(self):
        w=self.window;w.tag_controller.busy=True
        self.assertTrue(self.committer.queue_preferences({'columns_state':'Old'}))
        selected=self.root/'preference-restore';selected.mkdir()
        (selected/'library.json').write_text('{"settings":{"columns_state":"Restored"}}')
        backup.restore_backup(w._state_repository.path.parent,selected)
        w.settings=w._state_repository.load()['settings']
        w.tag_controller.busy=False;w.tag_controller.idle.emit()
        self.events_until(lambda:self.committer.preferences_error is not None)
        self.assertIsInstance(self.committer.preferences_error,StateChangedError)
        self.assertEqual(w.settings['columns_state'],'Restored')
        self.assertEqual(read_state(w._state_repository.path)['settings']['columns_state'],'Restored')

    def test_shutdown_flush_of_queued_preferences_runs_durability_only_on_worker(self):
        w=self.window;w.settings['columns_state']='Last size'
        self.assertTrue(self.committer.queue_preferences({'columns_state':'Last size'}))
        w._shutting_down=True;self.committer.owner.stop()
        lock=w._state_repository.area.lock;main=threading.get_ident()
        class WorkerLock:
            def __enter__(self):
                if threading.get_ident()==main:raise AssertionError('Shutdown preference IO on GUI')
                return lock.__enter__()
            def __exit__(self,*args):return lock.__exit__(*args)
        with mock.patch.object(w._state_repository.area,'lock',WorkerLock()):
            self.assertTrue(self.committer.flush_preferences())
        self.assertFalse(self.committer.pending);self.assertFalse(w._state_publication)
        self.assertEqual(read_state(w._state_repository.path)['settings']['columns_state'],'Last size')

    def test_successful_synchronous_checkpoint_clears_already_included_preference_queue(self):
        w=self.window;w._state_committer=self.committer;w._settings_dirty_keys={'waveform_zoom'}
        w.custom={};w.imported={};w.hidden=set();w.hidden_dates={};w.removals={};w.playlist_additions={};w._last_xml=''
        w._flash_status=mock.Mock();w.settings['waveform_zoom']=2;w.tag_controller.busy=True
        self.assertTrue(self.committer.queue_preferences({'waveform_zoom':2}));self.committer.defer_save()
        with mock.patch.object(w._state_repository,'commit_patch',wraps=w._state_repository.commit_patch) as commits:
            self.assertTrue(MainWindow._save_state(w))
            self.assertFalse(self.committer.preferences_pending);self.assertFalse(self.committer.deferred_save)
            self.assertEqual(w._settings_dirty_keys,set())
            w.tag_controller.busy=False;w.tag_controller.idle.emit();self.app.processEvents()
            commits.assert_not_called()
        self.assertEqual(read_state(w._state_repository.path)['settings']['waveform_zoom'],2)

    def test_stopped_owner_refuses_new_ordinary_preference_admission(self):
        self.committer.owner.stop()
        self.assertFalse(self.committer.queue_preferences({'waveform_zoom':2}))
        self.assertFalse(self.committer.preferences_pending)

    def test_explicit_quit_retries_only_retained_preferences_and_permanent_failure_stays_queued(self):
        w=self.window;w._state_committer=self.committer;w._settings_dirty_keys=set()
        w._flash_status=mock.Mock();w._pending_save_reason=lambda:MainWindow._pending_save_reason(w)
        w.settings['columns_state']='Newest geometry'
        with mock.patch.object(w._state_repository.area,'publish',side_effect=OSError('Preference volume unavailable')) as publish:
            self.assertTrue(self.committer.queue_preferences({'columns_state':'Newest geometry'}))
            self.events_until(lambda:self.committer.preferences_error is not None)
            self.assertTrue(MainWindow._defer_quit_for_saves(w))
            self.events_until(lambda:self.committer.preferences_error is not None)
            self.assertTrue(self.committer.preferences_pending);self.assertEqual(publish.call_count,2)
            for _ in range(3):self.app.processEvents()
            self.assertEqual(publish.call_count,2)
            self.assertEqual(w.settings['columns_state'],'Newest geometry')
        self.assertTrue(MainWindow._defer_quit_for_saves(w))
        self.events_until(lambda:not self.committer.pending)
        self.assertFalse(MainWindow._defer_quit_for_saves(w))
        self.assertEqual(read_state(w._state_repository.path)['settings']['columns_state'],'Newest geometry')

    def test_active_native_writer_or_probe_refuses_canonical_admission(self):
        w=self.window;before=read_state(w._state_repository.path)
        for field in ('busy','checking'):
            with self.subTest(field=field):
                setattr(w.tag_controller,field,True)
                try:
                    self.assertFalse(self.submit())
                    self.assertFalse(w._state_publication)
                    self.assertFalse(self.committer.pending)
                    self.assertFalse(w.tag_controller.state_commit_owned)
                    self.assertEqual(read_state(w._state_repository.path),before)
                finally:setattr(w.tag_controller,field,False)

    def test_stop_does_not_cancel_admitted_durability_or_drop_its_continuation(self):
        w = self.window; committed = mock.Mock(); failed = mock.Mock()
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit(committed, failed)); self.assertTrue(entered.wait(2))
            w._shutting_down = True
            self.committer.owner.stop()
            release.set()
        committed.assert_called_once_with(); failed.assert_not_called()
        self.assertEqual(read_state(w._state_repository.path)['title_overrides']['song'], 'Saved')
        self.assertFalse(w._state_publication); self.assertFalse(w.tag_controller.state_commit_owned)

    def test_queued_completion_cannot_mutate_a_restored_generation(self):
        w = self.window; original = w._state_repository.commit_patch
        finished = threading.Event(); committed = mock.Mock(); failed = mock.Mock()
        def commit_then_hold_delivery(patch):
            result = original(patch); finished.set(); return result
        with mock.patch.object(w._state_repository, 'commit_patch', side_effect=commit_then_hold_delivery):
            self.assertTrue(self.submit(committed, failed))
            self.assertTrue(finished.wait(2))
            selected = self.root / 'selected'; selected.mkdir()
            (selected / 'library.json').write_text('{"title_overrides":{"song":"Restored"},"settings":{"queue":[]}}')
            backup.restore_backup(w._state_repository.path.parent, selected)
            restored = w._state_repository.load()
            w.titles = restored['title_overrides']; w.settings = restored['settings']
            self.events_until(lambda: not self.committer.pending)
        self.assertEqual(w.titles['song'], 'Restored')
        committed.assert_not_called(); failed.assert_called_once()
        self.assertIsInstance(failed.call_args.args[0], StateChangedError)

    def test_generator_removal_and_absent_nested_removal_match_engine_semantics(self):
        w = self.window; committed = mock.Mock(); failed = mock.Mock()
        removals = (path for path in [('settings', 'obsolete'), ('settings', 'missing', 'child')])
        self.assertTrue(self.committer.submit({}, removals, committed, failed))
        self.events_until(lambda: not self.committer.pending)
        self.assertNotIn('obsolete', w.settings); self.assertNotIn('missing', w.settings)
        self.assertEqual(read_state(w._state_repository.path)['settings'], w.settings)
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_bare_root_removal_is_rejected_before_durable_admission(self):
        w = self.window; before = read_state(w._state_repository.path)
        committed = mock.Mock(); failed = mock.Mock()
        with self.assertRaises(ValueError):
            self.committer.submit({}, [('settings',)], committed, failed)
        self.assertEqual(read_state(w._state_repository.path), before)
        self.assertFalse(self.committer.pending); self.assertFalse(w._state_publication)
        self.assertFalse(w.tag_controller.state_commit_owned)
        committed.assert_not_called(); failed.assert_not_called()

    def test_shutdown_keeps_known_deferred_preferences_for_final_flush(self):
        w = self.window
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit()); self.assertTrue(entered.wait(2))
            w.settings['view']['expanded'] = ['Keep on shutdown']
            self.assertFalse(w._save_state())
            w._shutting_down = True
            self.committer.owner.stop(); release.set()
        durable = read_state(w._state_repository.path)['settings']['view']['expanded']
        self.assertTrue(self.committer.deferred_save or durable == ['Keep on shutdown'],
                        'Shutdown discarded known dirty preferences before their final flush')

    def test_mainwindow_save_defers_and_restore_admission_refuses_pending_commit(self):
        w = self.window; w._state_committer = self.committer
        w._refresh_worker = None; w._device_operation_owned = lambda: False; w.lib = None
        w._flash_status = mock.Mock()
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit()); self.assertTrue(entered.wait(2))
            self.assertFalse(MainWindow._save_state(w))
            self.assertTrue(self.committer.deferred_save)
            self.assertFalse(SettingsPageMixin._begin_state_maintenance(w, restoring=True))
            self.assertTrue(MainWindow._source_selection_reason(w))
            self.assertFalse(w._state_maintenance)
            release.set()

    def test_mainwindow_wrapper_admission_is_not_durable_success(self):
        w = self.window; w._state_committer = self.committer
        committed = mock.Mock(); failed = mock.Mock()
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(MainWindow._commit_metadata_state(w,
                {('title_overrides', 'song'): 'Wrapped'}, (), committed, failed))
            self.assertTrue(entered.wait(2))
            self.assertEqual(w.titles['song'], 'Original')
            committed.assert_not_called(); failed.assert_not_called()
            release.set()
        self.assertEqual(w.titles['song'], 'Wrapped')
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_pending_commit_defers_normal_quit_until_its_acknowledgement(self):
        w = self.window; w._flash_status = mock.Mock()
        w._pending_save_reason = lambda: MainWindow._pending_save_reason(w)
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit()); self.assertTrue(entered.wait(2))
            self.assertTrue(MainWindow._defer_quit_for_saves(w))
            w._flash_status.assert_called_once()
            release.set()
        self.assertEqual(MainWindow._pending_save_reason(w), '')

    def test_actual_shutdown_drains_commit_before_final_dirty_preferences_flush(self):
        w = self.window; w._state_committer = self.committer
        from desktop.usb_setup import UsbSetupController
        w.usb_setup_controller = UsbSetupController(w)
        w.usb_setup_controller.timer.start(10000)
        w._artists_refresh = SimpleNamespace(stop=mock.Mock())
        w._stock_view_pending = {}; w._dual_sync_queue = []
        w._device_coordinator = SimpleNamespace(cancel=mock.Mock())
        w.grid_controller = SimpleNamespace(shutdown=mock.Mock())
        def remember_columns():
            w.settings['view']['column_widths'] = [125, 250]
            return w._save_state()
        w._save_columns = remember_columns
        committed = mock.Mock(); failed = mock.Mock()
        with self.held_commit() as (entered, release, _threads):
            self.assertTrue(self.submit(committed, failed)); self.assertTrue(entered.wait(2))
            QTimer.singleShot(1, release.set)
            MainWindow.shutdown(w)
        self.assertTrue(w._shutting_down)
        self.assertTrue(w.usb_setup_controller.closing)
        self.assertFalse(w.usb_setup_controller.timer.isActive())
        self.assertFalse(self.committer.pending); self.assertFalse(self.committer.deferred_save)
        self.assertFalse(w._state_publication); self.assertFalse(w.tag_controller.state_commit_owned)
        durable = read_state(w._state_repository.path)
        self.assertEqual(durable['title_overrides']['song'], 'Saved')
        self.assertEqual(durable['settings']['view']['column_widths'], [125, 250])
        committed.assert_called_once_with(); failed.assert_not_called()

    def test_full_worker_verification_keeps_navigation_and_real_gui_save_unblocked(self):
        w=self.window;entered=threading.Event();release=threading.Event();outcomes=[];worker_id=[]
        original=state_repository._same_json
        w.custom={};w.imported={};w.hidden=set();w.hidden_dates={};w.removals={};w.playlist_additions={};w._last_xml=''
        w._flash_status=mock.Mock()
        navigation=QListWidget(w);navigation.addItems(['Collection','Favorites']);navigation.setCurrentRow(0)
        ticks=[];timer=QTimer();timer.setInterval(1);timer.timeout.connect(lambda:ticks.append(1))
        def compare(left,right):
            if threading.get_ident() in worker_id and not entered.is_set():
                entered.set()
                if not release.wait(3):raise RuntimeError('GUI verification fixture timeout')
            return original(left,right)
        def verify():
            worker_id.append(threading.get_ident())
            try:outcomes.append(w._state_repository.verify_selected_values({('title_overrides','song'):'Original'}))
            except Exception as exc:outcomes.append(exc)
        with mock.patch('soundbox.state_repository._same_json',side_effect=compare):
            worker=threading.Thread(target=verify);worker.start()
            try:
                self.assertTrue(entered.wait(3));timer.start()
                QTest.keyClick(navigation,Qt.Key_Down);QTest.qWait(10);self.app.processEvents();timer.stop()
                self.assertEqual(navigation.currentRow(),1);self.assertTrue(ticks)
                w.settings['view']['expanded']=['Genres']
                self.assertTrue(MainWindow._save_state(w))
                self.assertFalse(release.is_set())
            finally:release.set();worker.join()
        self.assertEqual(len(outcomes),1);self.assertIsInstance(outcomes[0],VerificationChanged)
        self.assertEqual(read_state(w._state_repository.path)['settings']['view']['expanded'],['Genres'])


if __name__ == '__main__':
    unittest.main()
