"""Hermetic analysis controls and queued worker retirement; no live source/devices."""
from pathlib import Path
import time
import unittest
from unittest import mock

from PySide6.QtCore import QStandardPaths
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.analysis import AnalysisControls, AnalysisWorker
from desktop.task_owner import TaskOwner
from soundbox.library import Library, Track


class AnalysisUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_have_requested_choices_and_safe_defaults(self):
        controls = AnalysisControls(automatic=True)
        self.assertTrue(controls.bpm.isChecked()); self.assertTrue(controls.key.isChecked())
        self.assertFalse(controls.auto.isChecked()); self.assertFalse(controls.first_cue.isChecked())
        self.assertFalse(controls.phrase.isEnabled())
        self.assertEqual(controls.bpm_range.count(),10)
        self.assertEqual(controls.bpm_range.currentText(),'70–180')
        self.assertEqual([controls.process.itemText(i) for i in range(3)],['Performance','Normal','Power saving'])
        controls.deleteLater()

    def worker(self):
        track = Track('sb-fixture','Fixture','Test',Path('/nonexistent-fixture.wav').as_uri(),imported=True)
        return AnalysisWorker(Library({track.track_id:track},[],'synthetic'),[track],None,{})

    def wait(self, condition):
        deadline = time.monotonic()+3
        while not condition() and time.monotonic()<deadline:
            QTest.qWait(5)
        self.assertTrue(condition())

    def test_success_publishes_only_after_finished_and_joins_without_reentrant_drain(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: save(w.result))
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            worker.start(); worker.wait()
            save.assert_not_called()
            self.wait(lambda: owner.pending==0)
        save.assert_called_once()

    def test_cancel_after_worker_finished_before_delivery_cannot_save(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: None if owner.stopping else save(w.result))
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            worker.start(); worker.wait(); owner.drain()
        save.assert_not_called()
        self.assertEqual(owner.pending,0)

    def test_failure_keeps_previous_data_and_allows_retry(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: save(w.result) if not isinstance(w.result,Exception) else None)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=ValueError('Analysis Lock is on')):
            worker.start(); worker.wait()
            self.assertIn('Analysis Lock',str(worker.result))
            self.wait(lambda: owner.pending==0)
        save.assert_not_called()
