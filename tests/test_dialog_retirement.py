"""Temporary dialogs retire after results are captured, including cancellation."""
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from desktop.app import MainWindow
from desktop.duplicate_dialog import DuplicateAddDialog
from desktop.suggest import MergeDialog


class DialogRetirement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        host = QWidget()
        host._shutting_down = host._state_maintenance = False
        host._stop_pulse = mock.Mock()
        host._set_status_text = mock.Mock()
        host._apply_merges = mock.Mock()
        host._track_label = lambda track: track
        host.aliases = {}
        self.addCleanup(host.deleteLater)
        return host

    def test_merge_cancel_and_accept_retire_parented_dialogs_and_capture_choices(self):
        host = self.host()
        result = ({'variant': 1, 'target': 2}, [('variant', 'target', .95)])
        with mock.patch.object(MergeDialog, 'exec', return_value=QDialog.Rejected):
            MainWindow._on_merge_scan(host, result)
        host._apply_merges.assert_not_called()
        def accept(dialog):
            dialog.accepts = [('variant', 'target')]
            dialog.unmerges = ['old']
            return QDialog.Accepted
        with mock.patch.object(MergeDialog, 'exec', accept):
            MainWindow._on_merge_scan(host, result)
        host._apply_merges.assert_called_once_with((('variant', 'target'),), ('old',))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(host.findChildren(MergeDialog), [])

    def test_duplicate_confirmation_retains_skip_and_accept_without_retaining_dialog(self):
        host = self.host()
        for choice in (QDialog.Rejected, QDialog.Accepted):
            with mock.patch.object(DuplicateAddDialog, 'exec', return_value=choice):
                self.assertEqual(MainWindow._confirm_duplicate_add(host, 'Playlist', ['song']),
                                 choice == QDialog.Accepted)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(host.findChildren(DuplicateAddDialog), [])

    def test_shutdown_during_confirmation_rejects_further_publication(self):
        host = self.host()
        def quit_during_dialog(_dialog):
            host._shutting_down = True
            return QDialog.Accepted
        with mock.patch.object(DuplicateAddDialog, 'exec', quit_during_dialog):
            self.assertFalse(MainWindow._confirm_duplicate_add(host, 'Playlist', ['song']))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(host.findChildren(DuplicateAddDialog), [])
