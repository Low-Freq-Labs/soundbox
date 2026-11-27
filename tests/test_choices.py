"""Shared choice presentation keeps standard Qt activation and right checks."""
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtGui import QAccessible, QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QListView, QStyle, QStyleOptionViewItem, QWidget
from shiboken6 import delete, isValid

from desktop import theme
from desktop.choices import ComboBox, Menu, exec_transient_menu


class Choices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transient_menus_release_actions_and_submenus_after_activation(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        activated = mock.Mock()
        for _ in range(20):
            menu = Menu(host)
            action = menu.addMenu('Nested').addAction('Choose', activated)
            with mock.patch.object(menu, 'exec', side_effect=lambda *_: action.trigger()):
                exec_transient_menu(menu, host.pos())
        self.assertEqual(activated.call_count, 20)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(host.findChildren(Menu), [])

    def test_empty_or_failed_transient_menus_are_also_retired(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        empty, failed = Menu(host), Menu(host)
        failed.addAction('Choose')
        exec_transient_menu(empty, host.pos())
        with mock.patch.object(failed, 'exec', side_effect=RuntimeError('popup failed')):
            with self.assertRaisesRegex(RuntimeError, 'popup failed'):
                exec_transient_menu(failed, host.pos())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(empty))
        self.assertFalse(isValid(failed))

    def test_transient_action_can_destroy_its_own_parent(self):
        host = QWidget()
        menu = Menu(host); menu.addAction('Close')
        with mock.patch.object(menu, 'exec', side_effect=lambda *_: delete(host)):
            exec_transient_menu(menu)
        self.assertFalse(isValid(menu))

    def test_combo_popup_checks_current_value_on_right_and_keeps_keyboard_selection(self):
        combo = ComboBox()
        combo.addItems(['White', 'Custom'])
        combo.setAccessibleName('Waveform color')
        combo.resize(200, 28); combo.show()
        self.app.processEvents()
        self.assertFalse(combo._popup_ready)
        self.assertFalse(any(view.property('soundboxThemePainted')
                             for view in combo.findChildren(QListView)))
        accessible=QAccessible.queryAccessibleInterface(combo)
        self.assertEqual(accessible.role(),QAccessible.ComboBox)
        reference=QComboBox();reference.addItems(['White','Custom'])
        reference.setAccessibleName('Waveform color')
        expected=QAccessible.queryAccessibleInterface(reference)
        self.assertEqual(accessible.text(QAccessible.Name),expected.text(QAccessible.Name))
        reference.deleteLater()
        # The actual keyboard popup path must finish native-popup suppression,
        # view and delegate construction before its first paint.
        QTest.keyClick(combo, Qt.Key_Down, Qt.AltModifier)
        self.app.processEvents()
        self.assertTrue(combo._popup_ready)
        for hint in (QStyle.SH_ComboBox_UseNativePopup,QStyle.SH_ComboBox_Popup):
            self.assertEqual(combo.style().styleHint(hint,None,combo),0)
        view = combo.view()
        self.assertTrue(view.isVisible())
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
        self.assertIn('ShowMenu',accessible.actionInterface().actionNames())
        accessible.actionInterface().doAction('ShowMenu')
        self.app.processEvents()
        self.assertIs(combo.view(),view)
        self.assertTrue(view.isVisible())
        QTest.keyClick(view,Qt.Key_Escape)
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

    def test_long_popup_label_does_not_paint_inside_the_checkmark_gutter(self):
        combo=ComboBox();combo.addItems(['Selected','M'*150]);combo.setCurrentIndex(0)
        image=QImage(150,30,QImage.Format_RGB32)
        option=QStyleOptionViewItem();option.rect=QRect(0,0,150,30)
        option.state=QStyle.State_Enabled
        painter=QPainter(image)
        combo.itemDelegate().paint(painter,option,combo.model().index(1,0));painter.end()
        background=QColor(theme.PANEL)
        self.assertTrue(all(image.pixelColor(x,y)==background for x in range(124,150) for y in range(30)))
        combo.deleteLater()
