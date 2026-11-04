"""Closing modal info retires owned decode work before deleting cached images."""
from threading import Event
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from desktop.track_info import TrackInfoDialog, _exec_info


class InfoDialogLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_closed_modal_releases_parented_dialog_only_after_decode_retires(self):
        parent = QWidget()
        release = Event()
        events = []
        def resolve():
            release.wait(2)
            events.append('decode-ended')
        dialog = TrackInfoDialog({'title': 'Fixture'}, artwork=resolve, parent=parent)
        dialog.destroyed.connect(lambda: events.append('dialog-destroyed'))
        QTimer.singleShot(20, dialog.reject)
        QTimer.singleShot(60, release.set)
        _exec_info(dialog)
        self.assertEqual(dialog._owner.pending, 0)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(dialog))
        self.assertEqual(events, ['decode-ended', 'dialog-destroyed'])
        self.assertEqual(parent.findChildren(TrackInfoDialog), [])
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
