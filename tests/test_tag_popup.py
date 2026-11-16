"""Tag checklist dismissal delivers the same pointer gesture to the browser."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QStandardPaths, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from soundbox.library import Library, LibraryTag, Track
from soundbox.providers import Providers


class TagPopupBrowsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(QStandardPaths, 'isTestModeEnabled', return_value=True))
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.win = MainWindow(providers=Providers('public'), isolated_dir=root)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win.close)
        self.addCleanup(self.win.shutdown)
        self.win.lib = Library({str(i): Track(str(i), f'Song {i}', 'Artist',
                                f'file:///unavailable-popup-fixture/{i}.wav') for i in range(3)}, [], 'synthetic')
        self.win.model.set_rows([dict(id=str(i), title=f'Song {i}', artist='Artist') for i in range(3)])
        self.win.table.setCurrentIndex(self.win.model.index(0, 0))
        self.win.show()
        self.app.processEvents()
        self.button = self.win.my_tags
        # Exercise panel/input behavior only; tag source writes are separately
        # covered by disposable controller tests and never admitted here.
        self.button.popup.aboutToShow.disconnect(self.win._probe_tags)
        self.button.assignment_requested.disconnect(self.win._assign_tag)
        self.tags = [LibraryTag('tag', 'Fixture tag')]
        self.assigned = []
        self.button.assignment_requested.connect(self.assign)
        self.button.set_state(self.tags, [set()])
        self.loaded = self.enterContext(mock.patch.object(self.win, '_load_track'))

    def assign(self, tag, present):
        self.assigned.append((tag, present))
        self.button.set_state(self.tags, [{'tag'}], 'Updating My Tags…')
        self.button.set_state(self.tags, [{'tag'}])

    def open_and_assign(self):
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        self.app.processEvents()
        box = self.button.checkboxes['tag']
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))
        self.assertEqual(self.assigned, [('tag', True)])
        self.assertTrue(self.button.popup.isVisible())

    def song_point(self):
        rect = self.win.table.visualRect(self.win.model.index(2, 0))
        return self.win.table.viewport().mapTo(self.win, rect.center())

    def test_first_outside_press_dismisses_and_selects_the_clicked_song(self):
        self.open_and_assign()
        QTest.mouseClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
        self.assertFalse(self.button.popup.isVisible())
        self.assertEqual(self.win._selected_ids(), ['2'])
        self.loaded.assert_not_called()

    def test_one_double_click_after_assignment_loads_once_through_real_button_menu(self):
        observed = []
        def browse():
            try:
                box = self.button.checkboxes['tag']
                QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(8, box.height() // 2))
                observed.append(self.button.popup.isVisible())
                QTest.mouseDClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
            finally:
                self.button.popup.close()
        QTimer.singleShot(0, self.button, browse)
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(observed, [True])
        self.assertEqual(self.assigned, [('tag', True)])
        self.assertFalse(self.button.popup.isVisible())
        self.loaded.assert_called_once_with(self.win.lib.tracks_by_id['2'])

    def test_button_toggle_and_escape_dismiss_without_an_extra_assignment(self):
        self.open_and_assign()
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.assertFalse(self.button.popup.isVisible())
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        QTest.keyClick(self.button.checkboxes['tag'], Qt.Key_Escape)
        self.assertFalse(self.button.popup.isVisible())
        self.assertEqual(self.assigned, [('tag', True)])

    def test_deactivation_and_shutdown_close_the_modeless_panel(self):
        self.open_and_assign()
        QApplication.sendEvent(self.app, QEvent(QEvent.ApplicationDeactivate))
        self.assertFalse(self.button.popup.isVisible())
        self.button.popup.popup(self.button.mapToGlobal(QPoint(0, self.button.height())))
        self.win.shutdown()
        self.assertFalse(self.button.popup.isVisible())

    def test_open_checklist_keeps_transport_shortcuts_with_its_controls(self):
        self.open_and_assign()
        with (mock.patch.object(self.win.player_bar, 'current_track', return_value='already-loaded'),
              mock.patch.object(self.win.player_bar, 'toggle') as toggle):
            QTest.keyClick(self.win.table, Qt.Key_Space)
            toggle.assert_not_called()
            QTest.mouseClick(self.win.windowHandle(), Qt.LeftButton, pos=self.song_point())
            self.win.table.setFocus()
            QTest.keyClick(self.win.table, Qt.Key_Space)
            toggle.assert_called_once()
