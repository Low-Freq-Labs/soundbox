"""Desktop source edits finish while a separate generated iPod worker is held."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.workers import OneShotTask
from desktop.tag_controller import TagEditController
from soundbox.device_operations import DeviceCommand, DeviceOperationCoordinator, DeviceSessionIdentity, Phase
from tests import test_deferred_prep as prep_fixture
from tests import test_tag_assignment_intents as tag_fixture
from tests import test_tag_creation_controller as definition_fixture
from tests import test_metadata_controller as metadata_fixture
from tests import test_ui_source_playlists as playlist_fixture


class IpodEditConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        prep_fixture.DurablePrep.setUpClass()
        playlist_fixture.NativePlaylistGestures.setUpClass()

    def hold_device(self, window, kind, phase):
        release, entered = Event(), Event()
        def work():
            entered.set(); release.wait(30)
        worker = OneShotTask(work)
        coordinator = DeviceOperationCoordinator()
        identity = DeviceSessionIdentity('/generated/no-device-io', 1, 'generated-library')
        coordinator.observe(identity)
        state = coordinator.begin(DeviceCommand(identity, 'export'), worker, phase)
        window._device_coordinator = coordinator
        window._device_profiles = {identity.path: SimpleNamespace(kind=kind)}
        window._device_operation_owned = lambda:True
        def finish():
            release.set(); self.assertTrue(worker.wait(5000)); worker.deleteLater()
        self.addCleanup(finish)
        worker.start(); self.assertTrue(entered.wait(1))
        return worker, coordinator, state

    def wait(self, predicate):
        for _ in range(1000):
            if predicate():return
            QTest.qWait(5)
        self.assertTrue(predicate(), 'Desktop source work did not finish during held iPod work')

    def test_hot_memory_and_grid_confirm_before_ipod_worker_finishes(self):
        for kind, phase in (('stock_ipod', Phase.PLANNING), ('dual_boot', Phase.EXECUTING),
                            ('rockbox', Phase.FINALIZING)):
            with self.subTest(kind=kind, phase=phase):
                case = prep_fixture.DurablePrep(); case.setUp(); self.addCleanup(case.doCleanups)
                worker, coordinator, state = self.hold_device(case.window, kind, phase)
                self.assertTrue(case.controller.request_cue(case.tid, 'hot', 0, 'add', 777))
                case.wait(lambda:not case.controller.pending and not case.controller.busy)
                self.assertTrue(case.controller.request_cue(case.tid, 'memory', 0, 'add', 1555))
                case.wait(lambda:not case.controller.pending and not case.controller.busy)
                self.assertTrue(case.controller.request_grid(case.tid, 'shift', 1))
                case.wait(lambda:not case.controller.pending and not case.controller.busy)
                self.assertEqual(len(case.writes), 3)
                self.assertEqual(sorted(cue['position_ms'] for cue in case.window.lib.tracks_by_id[case.tid].source_cues),
                                 [777, 1555])
                self.assertTrue(worker.isRunning()); self.assertIs(coordinator.active, state)

    def test_assignment_native_readback_and_durable_ack_finish_during_ipod_io(self):
        for phase in (Phase.PLANNING, Phase.EXECUTING, Phase.FINALIZING):
            case = tag_fixture.DurableAssignment(); case.setUp(); self.addCleanup(case.doCleanups)
            worker, coordinator, state = self.hold_device(case.state, 'stock_ipod', phase)
            self.assertTrue(case.controller.request(['2'], 'tag', True))
            case.wait(lambda:not case.controller.pending and not case.controller.busy)
            self.assertEqual(len(case.calls), 1)
            self.assertEqual(case.saved[0].tracks_by_id['2'].source_tag_ids, ['tag'])
            self.assertEqual(case.state.settings[tag_fixture.KEY], [])
            self.assertTrue(worker.isRunning()); self.assertIs(coordinator.active, state)

    def test_definition_creation_finishes_during_ipod_worker(self):
        case = definition_fixture.CreationController(); case.setUp(); self.addCleanup(case.doCleanups)
        worker, coordinator, state = self.hold_device(case.window, 'stock_ipod', Phase.EXECUTING)
        request = case.request(); case.begin(request); case.window.tag_controller.deliver()
        self.assertEqual(case.requests, [request]); self.assertEqual(case.controller.pending, [])
        self.assertTrue(worker.isRunning()); self.assertIs(coordinator.active, state)

    def test_metadata_capture_write_and_readback_finish_on_real_source_lane_during_ipod_work(self):
        for kind in ('stock_ipod', 'dual_boot', 'rockbox'):
            with self.subTest(kind=kind):
                case = metadata_fixture.MetadataPhases(); case.setUp(); self.addCleanup(case.doCleanups)
                tags = TagEditController(Path(case.area), case.w, store_factory=lambda _lib:None)
                tags.lib = case.w.lib; tags.verified = True; tags._recover_pending = mock.Mock()
                self.addCleanup(tags.shutdown)
                case.w.tag_controller = tags; case.controller.tags = tags
                tags.idle.connect(case.controller.schedule); tags.changed.connect(case.controller.schedule)
                worker, coordinator, state = self.hold_device(case.w, kind, Phase.EXECUTING)
                tags.busy = True
                self.assertTrue(case.controller.enqueue('local-song', {'title':'Desired Title'}))
                case.pump(); self.assertFalse(case.captured); self.assertFalse(case.writes)
                tags.busy = False; case.pump()
                self.wait(lambda:not case.controller.pending and not tags.busy)
                self.assertEqual(len(case.captured), 1); self.assertEqual(len(case.writes), 1)
                self.assertEqual(case.w.lib.tracks_by_id['1'].name, 'Desired Title')
                self.assertEqual(len(case.w.published), 1)
                self.assertFalse(case.controller._background_state_busy())
                self.assertTrue(worker.isRunning()); self.assertIs(coordinator.active, state)

    def test_playlist_fifo_renames_finish_on_real_source_lane_during_ipod_work(self):
        for kind in ('stock_ipod', 'dual_boot', 'rockbox'):
            with self.subTest(kind=kind):
                case = playlist_fixture.NativePlaylistGestures(); case.setUp(); self.addCleanup(case.tearDown)
                worker, coordinator, state = self.hold_device(case.window, kind, Phase.PLANNING)
                case.window.tag_controller.busy = True
                self.assertTrue(case.controller.enqueue(case.rename('First')))
                self.assertTrue(case.controller.enqueue(case.rename('Second')))
                case.controller.pump(); self.assertEqual(case.calls, [])
                case.window.tag_controller.busy = False; case.controller.pump()
                case.wait(lambda:not case.controller.pending and not case.window.tag_controller.busy)
                self.assertEqual([request['name'] for request in case.calls], ['First', 'Second'])
                self.assertEqual(case.window.lib.playlists[0].name, 'Second')
                self.assertTrue(worker.isRunning()); self.assertIs(coordinator.active, state)

    def test_generic_unknown_device_and_actual_source_writer_keep_their_guards(self):
        for kind in ('generic_usb', 'unknown', 'stock_ipod'):
            with self.subTest(kind=kind):
                case = prep_fixture.DurablePrep(); case.setUp(); self.addCleanup(case.doCleanups)
                worker, _coordinator, _state = self.hold_device(case.window, kind, Phase.EXECUTING)
                if kind == 'stock_ipod':case.native.shared.busy = True
                self.assertTrue(case.controller.request_cue(case.tid, 'hot', 0, 'add', 777))
                QTest.qWait(30); self.assertEqual(case.writes, [])
                case.native.shared.busy = False
                case.window._device_profiles['/generated/no-device-io'].kind = 'stock_ipod'
                case.controller.pump(); case.wait(lambda:not case.controller.pending and not case.controller.busy)
                self.assertEqual(len(case.writes), 1); self.assertTrue(worker.isRunning())
