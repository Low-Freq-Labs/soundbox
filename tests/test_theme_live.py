"""Live theme painting, preview transactions and hidden-widget catch-up."""
import copy
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QColorDialog, QFrame, QPushButton,
                              QVBoxLayout)

from desktop import theme
from desktop.settings_page import SettingsPageMixin
from desktop.theme_controller import install
from desktop.wordmark import Wordmark


class _Appearance(QFrame, SettingsPageMixin):
    def __init__(self):
        super().__init__()
        self.settings = dict(theme_accent=theme.DEFAULT_ACCENT.lower(),
                             theme_text_mode='white', theme_text_custom='#123456')
        self._schedule_settings_save = mock.Mock()
        box = QVBoxLayout(self)
        self.wordmark = Wordmark(); box.addWidget(self.wordmark)
        self._theme_color = QPushButton('Theme'); box.addWidget(self._theme_color)
        self.primary = QPushButton('Apply'); self.primary.setObjectName('Primary')
        box.addWidget(self.primary)
        self.cue = QPushButton('Cue'); self.cue.setStyleSheet('background:#f62055;')
        box.addWidget(self.cue)
        self.resize(360, 240)
        self._apply_theme_color(self.settings['theme_accent'])


class LiveTheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.controller = install(cls.app)

    def setUp(self):
        self.original = (theme.PURPLE, theme.ON_ACCENT)
        self.frame = _Appearance(); self.frame.show(); self.app.processEvents()

    def tearDown(self):
        self.frame.close(); self.frame.deleteLater()
        theme.set_accent(*self.original); self.controller.update()

    @staticmethod
    def fill(widget):
        image = widget.grab().toImage()
        return image.pixelColor(round(8*image.devicePixelRatio()),
                                round(8*image.devicePixelRatio())).name()

    def picker(self, accept, *, text=False):
        dialog = QColorDialog(self.frame)
        dialog.setOption(QColorDialog.DontUseNativeDialog)
        observed = {}
        def move():
            dialog.setCurrentColor(QColor('#dd2200'))
            dialog.setCurrentColor(QColor('#169ba7'))
            QTimer.singleShot(100, finish)
        def finish():
            observed.update(accent=theme.PURPLE, lettering=theme.ON_ACCENT,
                            preferences=copy.deepcopy(self.frame.settings),
                            fill=self.fill(self.frame.primary))
            if accept:dialog.accept()
            else:dialog.reject()
        QTimer.singleShot(0, move)
        with mock.patch('desktop.settings_page.QColorDialog', return_value=dialog):
            if text:self.frame._choose_theme_text_color()
            else:self.frame._choose_theme_color()
        return observed

    def test_live_cancel_renders_latest_color_without_saving_and_restores_original(self):
        before = copy.deepcopy(self.frame.settings)
        with mock.patch.object(self.app, 'setStyleSheet', wraps=self.app.setStyleSheet) as global_style:
            observed = self.picker(False)
        self.assertEqual(observed['accent'], '#169ba7')
        self.assertEqual(observed['fill'], '#169ba7')
        self.assertEqual(observed['preferences'], before)
        self.assertEqual(self.frame.settings, before)
        self.assertEqual(theme.PURPLE, before['theme_accent'])
        self.assertEqual(self.fill(self.frame.primary), before['theme_accent'])
        self.frame._schedule_settings_save.assert_not_called()
        global_style.assert_not_called()

    def test_accept_keeps_latest_preview_and_saves_only_once(self):
        before = copy.deepcopy(self.frame.settings)
        observed = self.picker(True)
        self.assertEqual(observed['preferences'], before)
        self.assertEqual(self.frame.settings['theme_accent'], '#169ba7')
        self.assertEqual(self.fill(self.frame.primary), '#169ba7')
        self.frame._schedule_settings_save.assert_called_once()

    def test_lettering_changes_immediately_and_custom_cancel_keeps_saved_choice(self):
        with mock.patch.object(self.app, 'setStyleSheet', wraps=self.app.setStyleSheet) as global_style:
            self.frame._apply_theme_text('black')
            self.app.processEvents()
            self.assertEqual(self.frame.primary.palette().color(QPalette.ButtonText), QColor('#000000'))
            observed = self.picker(False, text=True)
        self.assertEqual(observed['lettering'], '#169ba7')
        self.assertEqual(theme.ON_ACCENT, '#000000')
        self.assertEqual(self.frame.settings['theme_text_mode'], 'black')
        self.assertEqual(self.frame.settings['theme_text_custom'], '#123456')
        global_style.assert_not_called()

    def test_hidden_controls_catch_up_on_show_and_cue_inline_color_is_preserved(self):
        hidden = QFrame(); layout = QVBoxLayout(hidden)
        button = QPushButton(''); button.setObjectName('Primary'); layout.addWidget(button)
        try:
            self.frame._apply_theme_color('#169ba7')
            self.assertEqual(button.styleSheet(), '')
            hidden.show(); self.app.processEvents()
            self.assertEqual(self.fill(button), '#169ba7')
            self.assertEqual(self.fill(self.frame.cue), '#f62055')
        finally:
            hidden.close(); hidden.deleteLater()
