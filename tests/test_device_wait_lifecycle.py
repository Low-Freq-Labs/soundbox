"""Unstarted device requests retire through real scan and batch ownership."""
from copy import deepcopy
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.export_dialog import ExportSelectionMixin
from desktop.export_wait import cancel_source_syncs, defer_source_sync
from desktop.stock_catalog_sync import cancel_deferred_sync, request_sync
from desktop.workers import OneShotTask
from soundbox import engine
from soundbox.device_operations import DeviceOperationCoordinator
from tests import test_stock_shared_catalog_integration as fixture


class _Control:
    def __init__(self):
        self.visible = False
        self.number = 0

    def setVisible(self, value):
        self.visible = value

    def value(self):
        return self.number


class DeviceWaitLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.case = fixture.SharedCatalogIntegration()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.workers = []
        self.addCleanup(self.retire_workers)
        self.device = str(self.case.target)
        snapshot = self.case.snapshot()
        self.facts = DeviceFacts(volume_uuid='generated-volume', writable=True)
        self.messages = []
        for module in ('desktop.app', 'desktop.stock_catalog_sync', 'desktop.export_dialog',
                       'desktop.export_wait', 'desktop.ipod_export_capture'):
            self.enterContext(mock.patch(module + '.device_facts',
                side_effect=lambda *_args, **_options: self.facts))
        self.enterContext(mock.patch('desktop.stock_catalog_sync.device_fs', return_value='fat32'))
        self.enterContext(mock.patch('desktop.app.device_fs', return_value='fat32'))
        for method in ('warning', 'information'):
            self.enterContext(mock.patch.object(QMessageBox, method,
                side_effect=lambda _host, title, text, *_args: self.messages.append((title, text))))
        values = SimpleNamespace(lib=self.case.library, titles={}, overrides={}, settings={},
            art_cache=self.case.art, stock_art_cache=self.case.cache, _shutting_down=False,
            _state_maintenance=False, _exporting=False, _deleting=False, _cancelling=False,
            _stock_view_warming=set(), _stock_view_pending={}, _pending_device_operation=None,
            _stock_plan_worker=None, _stock_commit_worker=None, _stock_view_worker=None,
            _dev_scan_worker=None, _stock_catalog_witness_cache={}, _export_batch=None,
            _cdj_waiting=None, _source_sync_waiting=None, _dual_sync_queue={},
            _device_finalization_token=None, _device_mount_refresh_pending=False,
            _device_coordinator=DeviceOperationCoordinator(),
            _device_mount_tokens={self.device:self.case.target.stat().st_dev},
            _device_profiles={self.device:SimpleNamespace(kind='stock_ipod', has_stock_ipod=True)},
            _stock_view_cache={self.device:(snapshot, None)}, _current_device_path=self.device,
            _EJECT_BLOCKING_WORKERS=MainWindow._EJECT_BLOCKING_WORKERS,
            progress=_Control(), cancel_btn=_Control(), pulse=None, notes=[],
            tag_controller=SimpleNamespace(owned=False, pending=[]),
            metadata_controller=SimpleNamespace(pending=[], migration_pending=False,
                queue_error=lambda:''),
            _export_playlists=lambda **_options:[self.case.playlist],
            _sync_playlists=lambda:[self.case.playlist],
            _row_for=lambda track:engine.track_row(track, {}, {}),
            _stock_write_access=lambda *_args:snapshot.stock_format,
            _release_eject_ready=lambda:None, _update_device_action_state=lambda:None,
            _on_device_phase_progress=lambda *_args:None,
            _begin_stock_ipod_sync=mock.Mock(), _begin_dual_ipod_sync=mock.Mock())
        self.host = h = QObject()
        for name, value in vars(values).items():setattr(h, name, value)
        h._start_pulse = lambda message:setattr(h, 'pulse', message)
        h._stop_pulse = lambda **_options:setattr(h, 'pulse', None)
        h._set_device_progress = lambda value:setattr(h.progress, 'number', value)
        h._flash_status = lambda message, *_args:h.notes.append(message)
        for name in ('_device_operation_owned', '_defer_device_operation_until_scan_finishes',
                     '_start_device_worker', '_finish_worker', '_finish_device_result_worker',
                     '_finish_device_scan_worker', '_retire_device_scan_worker',
                     '_deliver_device_scan_callbacks'):
            setattr(h, name, MethodType(getattr(MainWindow, name), h))
        for name in ('_start_next_export_target', '_maybe_finish_export_target',
                     '_export_target_result', '_validate_export_target', '_request_ipod_export_capture'):
            setattr(h, name, MethodType(getattr(ExportSelectionMixin, name), h))

    def retire_workers(self):
        self.host._shutting_down = True
        for worker, release in self.workers:
            release.set()
            try:
                worker.cancel()
                worker.wait(3000)
            except RuntimeError:
                pass
        committer = getattr(self.host, '_state_committer', None)
        if committer is not None:committer.owner.drain()
        owner = getattr(self.host.tag_controller, 'owner', None)
        if owner is not None:owner.drain()
        self.host.deleteLater()
        self.app.processEvents()

    def until(self, predicate):
        deadline = monotonic() + 3
        while not predicate():
            self.app.processEvents()
            self.assertLess(monotonic(), deadline, 'Device wait did not retire')
            sleep(.001)
        self.app.processEvents()

    def batch(self):
        h = self.host
        target = dict(path=self.device, name='Generated iPod', kind='stock_ipod',
            token=h._device_mount_tokens[self.device], uuid=self.facts.volume_uuid)
        batch = dict(binding=(str(h.lib.source), str(h.lib.source_library_id)),
            keys=['cust:FAVORITES'], pending=[target], active=None, results=[],
            cancelled=False, error='', started=False)
        h._export_batch = batch
        return batch

    def scan(self):
        h = self.host
        release = Event()
        worker = OneShotTask(lambda:release.wait(3))
        worker.retry_discovery = False
        self.workers.append((worker, release))
        h._dev_scan_worker = worker
        worker.finished.connect(lambda:h._finish_device_scan_worker(worker))
        h._start_device_worker(worker, '', 'discover')
        return worker, release

    def deferred(self, *, prepare=False, batch=True):
        h = self.host
        captured = self.batch() if batch else None
        if prepare:
            scans = []
            def access(*_args):
                if not scans:scans.append(self.scan())
                return h._stock_view_cache[self.device][0].stock_format
            h._stock_write_access = access
        else:
            scans = [self.scan()]
        if batch:
            h._start_next_export_target()
            self.assertTrue(captured['started'])
        else:
            request_sync(h, ('901', 'Favorites', 2), device=self.device, confirmed=True)
        worker, release = scans[0]
        self.assertIsNotNone(h._pending_device_operation)
        self.assertIsNotNone(getattr(h._pending_device_operation, '_catalog_request', None))
        return captured, worker, release, worker._operation_resume_callbacks[0]

    def assert_terminal(self, batch, text):
        self.until(lambda:self.host._dev_scan_worker is None)
        self.assertIsNone(batch['active'])
        self.assertEqual(len(batch['results']), 1)
        self.assertIn(text, batch['results'][0][1])
        self.assertNotIn('Export complete', batch['results'][0][1])
        self.assertIsNone(self.host._stock_plan_worker)
        self.assertIsNone(self.host._stock_commit_worker)
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)

    def test_begin_deferred_track_uuid_change_finishes_batch_once(self):
        batch, _worker, release, repeat = self.deferred()
        self.host.lib.tracks_by_id['alpha'].source_uuid = 'replacement-track-uuid'
        release.set(); self.assert_terminal(batch, 'changed')
        repeat(True); repeat(False)
        self.assertEqual(len(batch['results']), 1)

    def test_prepare_deferred_native_path_change_finishes_batch_once(self):
        batch, _worker, release, repeat = self.deferred(prepare=True)
        self.host.lib.tracks_by_id['alpha'].source_native_path = '/synthetic/replacement.wav'
        release.set(); self.assert_terminal(batch, 'changed')
        repeat(True)
        self.assertEqual(len(batch['results']), 1)

    def test_begin_cancel_leaves_unrelated_scan_alive_and_retires_batch(self):
        batch, worker, release, repeat = self.deferred()
        self.assertTrue(cancel_deferred_sync(self.host))
        self.assertTrue(worker.isRunning())
        self.assertFalse(worker.cancellation.is_set())
        release.set(); self.assert_terminal(batch, 'cancelled')
        repeat(True)
        self.assertEqual(len(batch['results']), 1)
        self.until(lambda:self.host._export_batch is None)
        self.assertEqual(batch['outcomes'],['cancelled'])
        self.assertEqual(self.messages,[])

    def test_prepare_cancel_leaves_scan_alive_and_retires_batch(self):
        batch, worker, release, _repeat = self.deferred(prepare=True)
        self.assertTrue(cancel_deferred_sync(self.host))
        self.assertFalse(worker.cancellation.is_set())
        release.set(); self.assert_terminal(batch, 'cancelled')
        self.until(lambda:self.host._export_batch is None)
        self.assertEqual(batch['outcomes'],['cancelled'])
        self.assertEqual(self.messages,[])

    def test_disconnect_during_scan_finishes_original_target_honestly(self):
        batch, _worker, release, repeat = self.deferred()
        self.host._device_mount_tokens.pop(self.device)
        self.facts = replace(self.facts, volume_uuid='replacement')
        release.set(); self.assert_terminal(batch, 'changed')
        repeat(True)
        self.assertEqual(len(batch['results']), 1)

    def test_obsolete_catalog_callback_cannot_clear_newer_batch_progress(self):
        old, _worker, release, repeat = self.deferred()
        newer = self.batch(); newer['active'] = newer['pending'].pop(0)
        self.host._exporting = True; self.host.pulse = 'Newer batch progress'
        self.assertFalse(cancel_deferred_sync(self.host))
        release.set(); self.until(lambda:self.host._dev_scan_worker is None)
        repeat(True)
        self.assertIs(self.host._export_batch, newer)
        self.assertTrue(self.host._exporting)
        self.assertEqual(self.host.pulse, 'Newer batch progress')
        self.assertEqual((old['results'], newer['results']), ([], []))

    def test_cancel_standalone_deferred_sync_does_not_cancel_scan(self):
        _batch, worker, release, repeat = self.deferred(batch=False)
        self.assertTrue(cancel_deferred_sync(self.host))
        self.assertFalse(worker.cancellation.is_set())
        self.assertIn('cancelled', self.host.notes[-1])
        release.set(); self.until(lambda:self.host._dev_scan_worker is None)
        repeat(True)
        self.assertFalse(self.host._exporting)
        self.assertIsNone(self.host._stock_plan_worker)

    def test_cancel_deferred_sync_preserves_unrelated_format_presentation(self):
        batch, worker, release, repeat = self.deferred()
        h = self.host
        h._usb_format_device = '/generated/unrelated-usb'
        h.pulse = 'Formatting unrelated USB'
        h.progress.setVisible(True)
        self.assertTrue(cancel_deferred_sync(h))
        self.assertTrue(batch['cancelled'])
        self.assertFalse(worker.cancellation.is_set())
        self.assertFalse(h._exporting)
        self.assertFalse(h._cancelling)
        self.assertEqual(h.pulse, 'Formatting unrelated USB')
        self.assertTrue(h.progress.visible)
        self.assertEqual(batch['results'], [])
        release.set(); self.until(lambda:h._dev_scan_worker is None)
        repeat(True)
        self.assertEqual(h.pulse, 'Formatting unrelated USB')
        self.assertTrue(h.progress.visible)
        self.assertEqual(batch['results'], [])
        h._usb_format_device = ''
        self.assertFalse(h._device_operation_owned())
        h._maybe_finish_export_target()
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('cancelled', batch['results'][0][1])

    def test_stale_wait_preserves_another_owner_until_its_real_retirement(self):
        batch, _scan, release, _repeat = self.deferred()
        h = self.host
        h.lib.tracks_by_id['alpha'].name = 'Newer displayed title'
        other = []
        other_release = Event()
        def update():
            if h._dev_scan_worker is not None or h._device_coordinator.active is not None or other:
                return
            worker = OneShotTask(lambda:other_release.wait(3))
            other.append(worker); self.workers.append((worker, other_release))
            h._unrelated_worker = worker
            def completed(_result):
                h._exporting = False; h._stop_pulse()
            worker.finished.connect(lambda:h._finish_device_result_worker(
                '_unrelated_worker', worker, completed))
            h._start_device_worker(worker, self.device, 'inspect')
            h._exporting = True; h.pulse = 'Unrelated owner progress'
        h._update_device_action_state = update
        release.set(); self.until(lambda:h._dev_scan_worker is None)
        self.assertEqual(len(other), 1)
        self.assertTrue(other[0].isRunning())
        self.assertFalse(other[0].cancellation.is_set())
        self.assertIs(h._device_coordinator.active.worker, other[0])
        self.assertTrue(h._exporting)
        self.assertEqual(h.pulse, 'Unrelated owner progress')
        self.assertEqual(batch['results'], [])
        self.assertEqual(batch['error'],
            'A different device operation started; retry the waiting Sync')
        self.assertEqual(batch['outcome'],'failed')
        h._update_device_action_state = lambda:None
        other_release.set(); self.until(lambda:h._unrelated_worker is None)
        self.assertEqual(len(batch['results']), 1)
        self.assertEqual(batch['results'][0][1],
            'A different device operation started; retry the waiting Sync')

    def waiting_source(self):
        h = self.host
        batch = self.batch(); batch['active'] = batch['pending'].pop(0)
        batch['started'] = True
        h.metadata_controller.pending = [{'error':''}]
        self.assertTrue(defer_source_sync(h, 'stock', self.device, [self.case.playlist]))
        return batch

    def test_cancel_source_wait_finishes_only_its_batch_and_preserves_writer_intent(self):
        batch = self.waiting_source()
        pending = deepcopy(self.host.metadata_controller.pending)
        queue = self.host._source_sync_waiting
        self.assertTrue(cancel_source_syncs(self.host))
        self.assertTrue(batch['cancelled'])
        self.assertEqual(self.host.metadata_controller.pending, pending)
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('cancelled', batch['results'][0][1])
        self.assertFalse(cancel_source_syncs(self.host))
        self.assertEqual(len(batch['results']), 1)
        self.assertTrue(queue)  # Detaching UI commands never erases native work.
        self.host._begin_stock_ipod_sync.assert_not_called()

    def test_obsolete_source_wait_never_dispatches_into_newer_batch(self):
        old = self.waiting_source()
        newer = self.batch(); newer['active'] = newer['pending'].pop(0)
        self.host.pulse = 'Newer source wait'; self.host.cancel_btn.setVisible(True)
        self.host.metadata_controller.pending = []
        self.until(lambda:self.host._source_sync_waiting is None)
        self.host._begin_stock_ipod_sync.assert_not_called()
        self.assertEqual((old['results'], newer['results']), ([], []))
        self.assertEqual(self.host.pulse, 'Newer source wait')
        self.assertTrue(self.host.cancel_btn.visible)

    def test_source_wait_error_retains_native_intent_and_reports_failure(self):
        batch = self.waiting_source()
        self.host.metadata_controller.pending[0]['error'] = 'Saved metadata needs attention'
        pending = deepcopy(self.host.metadata_controller.pending)
        self.until(lambda:self.host._source_sync_waiting is None)
        self.assertEqual(self.host.metadata_controller.pending, pending)
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('needs attention', batch['results'][0][1])
        self.host._begin_stock_ipod_sync.assert_not_called()

    def test_source_change_during_wait_finishes_captured_batch_without_dispatch(self):
        batch = self.waiting_source()
        self.host.lib = replace(self.host.lib, source=str(self.case.base / 'other.db'))
        self.host.metadata_controller.pending = []
        self.until(lambda:self.host._source_sync_waiting is None)
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('changed', batch['results'][0][1])
        self.host._begin_stock_ipod_sync.assert_not_called()

    def test_refused_source_ready_dispatch_finishes_batch_without_claiming_success(self):
        batch = self.waiting_source()
        self.host.metadata_controller.pending = []
        self.until(lambda:self.host._source_sync_waiting is None)
        self.host._begin_stock_ipod_sync.assert_called_once()
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('did not start', batch['results'][0][1])
        self.assertNotIn('Export complete', batch['results'][0][1])

    def test_new_source_wait_is_an_active_batch_until_cancelled(self):
        from desktop.state_commit import StateCommitter
        from desktop.tag_controller import TagEditController
        from soundbox.state_repository import StateRepository

        h = self.host
        self.case.playlist.name = 'New source playlist'
        h._begin_stock_ipod_sync = MethodType(MainWindow._begin_stock_ipod_sync, h)
        h.aliases = {}; h.imported = {}; h.custom = {}
        h.hidden = set(); h.hidden_dates = {}; h.removals = {}; h.playlist_additions = {}
        h._state_publication = False
        h._state_repository = StateRepository(self.case.base / 'checkpoint/library.json')
        h._state_repository.load()
        h._state_repository.save(dict(settings=h.settings, title_overrides=h.titles,
            artist_overrides=h.overrides, artist_aliases=h.aliases, imported=h.imported,
            custom=h.custom, hidden_tracks={}, playlist_removals={}, playlist_additions={}))
        h.tag_controller = TagEditController(self.case.base / 'checkpoint-controller', h,
            store_factory=lambda _:None)
        h._state_committer = StateCommitter(h)
        # A real held canonical owner delays the checkpoint. The unrelated
        # native backlog remains intact and is not this new export's barrier.
        h.tag_controller.busy = True
        h.metadata_controller.pending = [{'error':''}]
        pending = deepcopy(h.metadata_controller.pending)
        saved = h._state_repository.path.read_bytes()
        batch = self.batch()
        with mock.patch('desktop.ipod_export_capture.capture_export',
                        side_effect=AssertionError('Captured while the canonical owner was held')) as capture:
            h._start_next_export_target()
            self.app.processEvents()
            self.assertTrue(batch['started'])
            self.assertIsNotNone(batch['active'])
            self.assertEqual(batch['results'], [])
            self.assertIsNotNone(h._source_sync_waiting)
            self.assertTrue(h._state_committer._checkpoints)
            self.assertTrue(cancel_source_syncs(h))
            h.tag_controller.busy = False
            h.tag_controller.idle.emit()
            self.until(lambda:not h._state_committer.pending)
            capture.assert_not_called()
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('cancelled', batch['results'][0][1])
        self.assertIsNone(h._source_sync_waiting)
        self.assertIsNone(h._stock_plan_worker)
        self.assertIsNone(h._stock_commit_worker)
        self.assertEqual(h.metadata_controller.pending, pending)
        self.assertEqual(h._state_repository.path.read_bytes(), saved)
