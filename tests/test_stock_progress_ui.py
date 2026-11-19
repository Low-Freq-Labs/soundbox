"""Actual worker phase delivery advances stock progress without inventing completion."""
from pathlib import Path
import tempfile
import threading
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton
from shiboken6 import isValid

from desktop.app import MainWindow
from desktop.workers import OneShotTask
from soundbox.device_operations import DeviceOperationCoordinator
from soundbox.operation_control import phase_progress


class StockProgressUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.area).resolve()
        self.progress = QProgressBar()
        self.status = QLabel()
        self.cancel = QPushButton()
        self.workers = []
        self.host = SimpleNamespace(
            _device_coordinator=DeviceOperationCoordinator(),
            _device_mount_tokens={str(self.root): self.root.stat().st_dev},
            _stock_plan_worker=None, _stock_commit_worker=None,
            _shutting_down=False, _cancelling=False,
            progress=self.progress, status_label=self.status, cancel_btn=self.cancel,
            _start_pulse=mock.Mock(), _set_status_count=mock.Mock(),
            _update_device_action_state=mock.Mock())
        self.host._set_device_progress = MethodType(MainWindow._set_device_progress, self.host)
        self.host._on_device_phase_progress = MethodType(MainWindow._on_device_phase_progress, self.host)
        self.addCleanup(self.close)

    def close(self):
        for worker, release in self.workers:
            release.set()
            if isValid(worker): worker.wait()
        self.app.processEvents()
        self.progress.deleteLater(); self.status.deleteLater(); self.cancel.deleteLater()
        QCoreApplication.sendPostedEvents()

    def until(self, condition):
        limit = time.monotonic() + 3
        while not condition() and time.monotonic() < limit:
            self.app.processEvents(); QTest.qWait(1)
        self.assertTrue(condition(), 'Generated progress did not arrive')

    def start(self, work, *, stock=True):
        release = threading.Event()
        worker = OneShotTask(lambda: work(release))
        self.workers.append((worker, release))
        name = '_stock_commit_worker' if stock else '_dual_commit_worker'
        setattr(self.host, name, worker)
        worker.finished.connect(lambda: MainWindow._finish_worker(self.host, name, worker))
        MainWindow._start_device_worker(self.host, worker, str(self.root), 'commit')
        return worker, release

    def test_verified_byte_phases_advance_large_export_but_never_claim_final_readback(self):
        total = 4_500_000_000
        rows = [('Checking iPod files', 1, 2), ('Checking iPod files', 2, 2),
                ('iTunes copying', total // 2, 2 * total), ('iTunes copying', total, 2 * total),
                ('iTunes verifying', total + total // 2, 2 * total),
                ('iTunes verifying', 2 * total, 2 * total),
                ('iTunes media', total, total), ('iTunes artwork', 10, 20),
                ('iTunes artwork', 20, 20), ('iTunes database', 1, 1),
                ('iTunes cleanup', 1, 1)]
        seen = []
        original = self.host._on_device_phase_progress
        def observed(worker, stage, done, count, label):
            original(worker, stage, done, count, label)
            seen.append((stage, done, self.progress.value()))
        self.host._on_device_phase_progress = observed
        self.host._set_device_progress(150)
        def work(release):
            for stage, done, count in rows: phase_progress(stage, done, count, 'Generated song')
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work)
        self.until(lambda: len(seen) == len(rows))
        values = [value for _, _, value in seen]
        self.assertEqual(values, sorted(values))
        self.assertGreater(len(set(values)), 5)
        self.assertEqual(seen[2], ('iTunes copying', total // 2, 350))
        self.assertEqual(seen[3], ('iTunes copying', total, 450))
        self.assertEqual(seen[4], ('iTunes verifying', total + total // 2, 550))
        self.assertEqual(seen[5], ('iTunes verifying', 2 * total, 650))
        self.assertEqual(values[-1], 800)
        self.assertNotIn(1000, values)
        self.assertIn('Finishing iPod', self.host._start_pulse.call_args.args)
        release.set(); self.until(lambda: self.host._stock_commit_worker is None)
        self.assertIsNone(self.host._device_coordinator.active)

    def test_cancelled_and_foreign_worker_events_cannot_advance_or_replace_status(self):
        entered = threading.Event()
        def work(release):
            entered.set()
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work)
        self.until(entered.is_set)
        self.host._set_device_progress(377)
        self.host._device_coordinator.active.cancellation.set()
        self.host._on_device_phase_progress(worker, 'iTunes media', 100, 100, 'late')
        self.host._on_device_phase_progress(object(), 'iTunes database', 1, 1, 'foreign')
        self.assertEqual(self.progress.value(), 377)
        self.host._start_pulse.assert_not_called()
        release.set(); self.until(lambda: self.host._stock_commit_worker is None)

    def test_dual_primary_progress_keeps_its_existing_owner(self):
        entered = threading.Event()
        def work(release):
            entered.set()
            if not release.wait(3): raise RuntimeError('Generated progress wait expired')
        worker, release = self.start(work, stock=False)
        self.until(entered.is_set)
        self.host._set_device_progress(377)
        self.host._on_device_phase_progress(worker, 'iTunes media', 100, 100, 'nested stock copy')
        self.assertEqual(self.progress.value(), 377)
        self.host._start_pulse.assert_not_called()
        release.set(); self.until(lambda: self.host._dual_commit_worker is None)
