"""Shared choice presentation keeps standard Qt activation and right checks."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import theme
from desktop.choices import ComboBox, Menu


class Choices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_popup_checks_current_value_on_right_and_keeps_keyboard_selection(self):
        combo = ComboBox()
        combo.addItems(['White', 'Custom'])
        combo.resize(200, 28); combo.show(); combo.showPopup()
        self.app.processEvents()
        view = combo.view()
        image = view.viewport().grab().toImage()
        ratio = image.devicePixelRatio()
        rect = view.visualRect(combo.model().index(0, 0))
        # The right gutter contains the check's light ink, independently of
        # text width or whether a native Cocoa popup is normally available.
        light = 0
        for y in range(round(rect.top()*ratio), round((rect.bottom()+1)*ratio)):
            for x in range(round((rect.right()-24)*ratio), round((rect.right()-4)*ratio)):
                color = image.pixelColor(x, y)
                light += min(color.red(), color.green(), color.blue()) > 180
        self.assertGreater(light, 4)
        QTest.keyClick(view, Qt.Key_Down)
        QTest.keyClick(view, Qt.Key_Return)
        self.assertEqual(combo.currentText(), 'Custom')
        combo.close(); combo.deleteLater()

    def test_nested_menu_reserves_right_gutter_without_clipping_long_label(self):
        menu = Menu()
        nested = menu.addMenu('Change waveform color')
        self.assertIsInstance(nested, Menu)
        action = nested.addAction('RGB'); action.setCheckable(True); action.setChecked(True)
        menu.popup(self.app.primaryScreen().availableGeometry().center())
        self.app.processEvents()
        label = menu.actions()[0]
        self.assertGreaterEqual(menu.actionGeometry(label).width(),
            menu.fontMetrics().horizontalAdvance(label.text())+44)
        menu.grab()  # exercise the actual painting path, including submenu arrow
        action.trigger(); self.assertFalse(action.isChecked())
        menu.close(); menu.deleteLater()

    def test_player_popup_keeps_its_check_gutter_inside_the_field(self):
        from desktop.player import PlayerLayoutSelector
        combo = PlayerLayoutSelector()
        combo.addItems(['1 Player', '2 Player', 'Simple', 'Browser'])
        combo.setFixedSize(90, 28); combo.show(); combo.showPopup()
        self.app.processEvents()
        view = combo.view()
        popup = view.window()
        self.assertEqual(popup.width(), combo.width())
        self.assertLessEqual(view.mapTo(popup, view.rect().topRight()).x(), popup.width()-1)
        combo.hidePopup(); combo.close(); combo.deleteLater()

    def test_accent_text_role_tracks_saved_theme(self):
        original, foreground = theme.PURPLE, theme.ON_ACCENT
        try:
            theme.set_accent('#125655', '#ffff00')
            self.assertEqual(theme.ON_ACCENT, '#ffff00')
        finally:
            theme.set_accent(original, foreground)
