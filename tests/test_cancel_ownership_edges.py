"""Cancel a captured source wait without borrowing a different device owner."""
from threading import Event
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.cdj import CdjDeviceMixin
from desktop.cdj import CdjWorker
from desktop.workers import OneShotTask
from tests import test_device_wait_lifecycle as fixture
from tests import test_cdj_transaction as cdj_fixture


class CancelOwnershipEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fixture.DeviceWaitLifecycle.app = cls.app

    def setUp(self):
        self.case = fixture.DeviceWaitLifecycle()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.host = self.case.host
        self.dev = self.case.device
        self.host._cancel_export = MethodType(MainWindow._cancel_export, self.host)
        self.host._begin_cdj_export = MethodType(CdjDeviceMixin._begin_cdj_export, self.host)
        self.host._show_usb_format_status = mock.Mock()
        self.host._device_profiles[self.dev] = SimpleNamespace(kind='generic_usb', cdj_export=True)

    def source_wait(self):
        h = self.host
        h.metadata_controller.pending = [{'id':'unrelated-native-edit', 'error':''}]
        h._begin_cdj_export(self.dev, [self.case.case.playlist], from_drop=True)
        self.assertIsNotNone(h._cdj_waiting)
        self.assertEqual(h.pulse, 'Waiting for source edits')
        self.assertTrue(h.cancel_btn.visible)
        return h.metadata_controller.pending.copy()

    def test_cancel_cdj_source_wait_preserves_unrelated_format_presentation(self):
        pending = self.source_wait()
        h = self.host
        h._usb_format_device = '/generated/other-usb'
        h.pulse = 'Formatting other USB';h.progress.setVisible(True)
        h.cancel_btn.setVisible(False)
        h._cancel_export()
        self.assertIsNone(h._cdj_waiting)
        self.assertEqual(h.metadata_controller.pending, pending)
        self.assertEqual(h.pulse, 'Formatting other USB')
        self.assertTrue(h.progress.visible)
        self.assertFalse(h._cancelling)
        h._usb_format_device = ''
        self.assertFalse(h._device_operation_owned())

    def test_cancel_cdj_source_wait_preserves_current_unrelated_worker_progress(self):
        pending = self.source_wait()
        h = self.host
        release = Event()
        worker = OneShotTask(lambda:release.wait(3))
        self.case.workers.append((worker, release))
        h._unrelated_worker = worker
        worker.finished.connect(lambda:h._finish_device_result_worker(
            '_unrelated_worker', worker, lambda _value:setattr(h, '_exporting', False)))
        h._start_device_worker(worker, self.dev, 'inspect')
        h._exporting = True;h.pulse = 'Reading other device';h.progress.setVisible(True)
        h._cancel_export()
        self.assertIsNone(h._cdj_waiting)
        self.assertEqual(h.metadata_controller.pending, pending)
        self.assertTrue(worker.isRunning())
        self.assertFalse(worker.cancellation.is_set())
        self.assertIs(h._device_coordinator.active.worker, worker)
        self.assertTrue(h._exporting)
        self.assertEqual(h.pulse, 'Reading other device')
        self.assertTrue(h.progress.visible)
        release.set();self.case.until(lambda:h._unrelated_worker is None)
        self.assertFalse(h._exporting)

    def test_cancel_cdj_source_wait_without_other_owner_ends_the_exact_batch(self):
        h = self.host
        batch = self.case.batch();batch['active'] = batch['pending'].pop(0)
        batch['active']['kind'] = 'generic_usb';batch['started'] = True
        pending = self.source_wait()
        h._cancel_export()
        self.assertIsNone(h._cdj_waiting)
        self.assertEqual(h.metadata_controller.pending, pending)
        self.assertFalse(h._device_operation_owned())
        self.assertIsNone(h.pulse)
        self.assertFalse(h.cancel_btn.visible)
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('cancel', batch['results'][0][1].lower())
        self.assertNotIn('Export complete', batch['results'][0][1])

    def held_cdj_batch(self):
        case = cdj_fixture.CdjTransactionTests()
        case.setUp();self.addCleanup(case.doCleanups)
        h = self.host
        dev = str(case.target)
        h._device_mount_tokens[dev] = case.token
        batch = self.case.batch();target = batch['pending'].pop(0)
        target.update(path=dev, name='Generated USB', kind='generic_usb')
        batch['active'] = target;batch['started'] = True
        release, entered = Event(), Event()
        plan = case.plan()
        def operation(_cancel, _progress):
            entered.set();release.wait(3)
            return plan
        worker = CdjWorker(operation)
        self.case.workers.append((worker, release))
        h._cdj_worker = worker;h._exporting = True
        delivered = mock.Mock()
        worker.finished.connect(lambda:h._finish_device_result_worker('_cdj_worker', worker, delivered))
        h._start_device_worker(worker, dev, 'plan-cdj')
        self.assertTrue(entered.wait(2))
        return batch, worker, release, delivered

    def test_disconnected_worker_finishes_its_captured_export_batch_honestly(self):
        h = self.host
        batch, _worker, release, delivered = self.held_cdj_batch()
        h._device_coordinator.disconnect(batch['active']['path'])
        release.set();self.case.until(lambda:h._cdj_worker is None)
        delivered.assert_not_called()
        self.assertFalse(h._device_operation_owned())
        self.assertIsNone(batch['active'])
        self.assertEqual(len(batch['results']), 1)
        self.assertIn('disconnect', batch['results'][0][1].lower())
        self.assertNotIn('Export complete', batch['results'][0][1])
        h._maybe_finish_export_target()
        self.assertEqual(len(batch['results']), 1)

    def test_disconnected_old_worker_cannot_finish_newer_same_path_batch(self):
        h = self.host
        old, _worker, release, delivered = self.held_cdj_batch()
        target = old['active']
        h._device_coordinator.disconnect(target['path'])
        newer = self.case.batch();fresh = newer['pending'].pop(0)
        fresh.update(target);newer['active'] = fresh;newer['started'] = True
        h._exporting = True;h.pulse = 'Newer export batch progress'
        release.set();self.case.until(lambda:h._cdj_worker is None)
        delivered.assert_not_called()
        self.assertIs(h._export_batch, newer)
        self.assertIs(newer['active'], fresh)
        self.assertEqual(newer['results'], [])
        self.assertEqual(newer['error'], '')
        self.assertTrue(h._exporting)
        self.assertEqual(h.pulse, 'Newer export batch progress')
