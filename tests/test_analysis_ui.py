"""Hermetic analysis controls and queued worker retirement; no live source/devices."""
from pathlib import Path
import time
import unittest
from unittest import mock

from PySide6.QtCore import QStandardPaths
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.analysis import AnalysisControls, AnalysisDialog
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

    def dialog(self, save):
        track = Track('sb-fixture','Fixture','Test',Path('/nonexistent-fixture.wav').as_uri(),imported=True)
        return AnalysisDialog(Library({track.track_id:track},[],'synthetic'),[track],None,{},save)

    def wait(self, condition):
        deadline = time.monotonic()+3
        while not condition() and time.monotonic()<deadline:
            QTest.qWait(5)
        self.assertTrue(condition())

    def test_success_publishes_only_after_finished_and_joins_without_reentrant_drain(self):
        save = mock.Mock(return_value=True)
        dialog = self.dialog(save)
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            dialog.begin(); worker = dialog.worker; worker.wait()
            save.assert_not_called()
            self.wait(lambda: dialog.owner.pending==0)
            self.assertIn('Saved in SoundBox',dialog.status.text())
            dialog.reject()
        save.assert_called_once()
        dialog.deleteLater()

    def test_cancel_after_worker_finished_before_delivery_cannot_save(self):
        save = mock.Mock(return_value=True)
        dialog = self.dialog(save)
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            dialog.begin(); dialog.worker.wait(); dialog.reject()
        save.assert_not_called()
        self.assertEqual(dialog.owner.pending,0)
        dialog.deleteLater()

    def test_failure_keeps_previous_data_and_allows_retry(self):
        save = mock.Mock(return_value=True)
        dialog = self.dialog(save)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=ValueError('Analysis Lock is on')):
            dialog.begin(); self.wait(lambda: dialog.owner.pending==0)
        save.assert_not_called();self.assertIn('Analysis Lock',dialog.status.text())
        self.assertTrue(dialog.start.isEnabled())
        dialog.reject();dialog.deleteLater()
