"""Live theme painting, preview transactions and hidden-widget catch-up."""
import copy
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPalette
from PySide6.QtWidgets import (QApplication, QColorDialog, QFrame, QPushButton,
                              QStyle, QStyleOptionButton, QVBoxLayout)

from desktop import theme
from desktop.settings_page import SettingsPageMixin
from desktop.theme_controller import install
from desktop.wordmark import Wordmark
from desktop.export_dialog import ExportDialog
from desktop.progress import ThemeProgressBar
from soundbox.library import Playlist


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
                            fill=self.fill(self.frame.primary),
                            icon_key=self.app.windowIcon().cacheKey())
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

    def test_icon_is_unchanged_during_preview_and_after_cancel(self):
        before = self.app.windowIcon().cacheKey()
        with mock.patch.object(self.app, 'setWindowIcon', wraps=self.app.setWindowIcon) as change:
            observed = self.picker(False)
        self.assertEqual(observed['icon_key'], before)
        self.assertEqual(self.app.windowIcon().cacheKey(), before)
        change.assert_not_called()

    def test_icon_changes_once_only_after_ok(self):
        before = self.app.windowIcon().cacheKey()
        with mock.patch.object(self.app, 'setWindowIcon', wraps=self.app.setWindowIcon) as change:
            observed = self.picker(True)
            self.assertEqual(observed['icon_key'], before)
            self.assertNotEqual(self.app.windowIcon().cacheKey(), before)
            self.assertEqual(self.app._soundbox_icon_accent, '#169ba7')
            self.frame._apply_theme_color(self.frame.settings['theme_accent'])
            change.assert_called_once()

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


class TransportRingTheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.controller = install(cls.app)

    def setUp(self):
        self.original = (theme.PURPLE, theme.ON_ACCENT)
        self.frame = QFrame(); self.frame.setObjectName('TopBar')
        layout = QVBoxLayout(self.frame)
        self.cue = QPushButton('CUE'); self.cue.setObjectName('CueBtn')
        self.play = QPushButton('▶'); self.play.setObjectName('DeckPlayBtn')
        for button in (self.cue, self.play):
            button.setFixedSize(48, 48); button.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(button)
        self.frame.show(); self.app.processEvents()

    def tearDown(self):
        self.frame.close(); self.frame.deleteLater()
        theme.set_accent(*self.original); self.controller.update()

    def capture(self, button, *, hover=False, down=False, **properties):
        button.setAttribute(Qt.WA_UnderMouse, hover)
        button.setDown(down)
        for key, value in properties.items():
            button.setProperty(key, value)
        button.style().unpolish(button); button.style().polish(button)
        # QPushButton tracks hit-testing separately from WA_UnderMouse. Send
        # directly to this private fixture; never move the system cursor.
        point = QPointF(24, 24) if hover else QPointF(-1, -1)
        self.app.sendEvent(button, QMouseEvent(
            QEvent.MouseMove, point, point, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        self.app.processEvents()
        option = QStyleOptionButton(); button.initStyleOption(option)
        self.assertEqual(bool(option.state & QStyle.State_MouseOver), hover)
        whole = self.frame.grab().toImage()
        at = button.mapTo(self.frame, QPoint(0, 0)); scale = whole.devicePixelRatio()
        return whole.copy(round(at.x()*scale), round(at.y()*scale),
                          round(button.width()*scale), round(button.height()*scale))

    def test_cue_hover_is_inert_and_mouse_or_keyboard_hold_stays_orange(self):
        import numpy as np
        for accent in ('#00c301', '#168fc0', '#e34c26'):
            theme.set_accent(accent); self.controller.update(); self.app.processEvents()
            resting = self.capture(self.cue, active=False)
            self.assertEqual(resting, self.capture(self.cue, hover=True, active=False))
            for mouse in (False, True):
                held = self.capture(self.cue, down=mouse, active=not mouse)
                self.assertEqual(held, self.capture(
                    self.cue, hover=True, down=mouse, active=not mouse))
                image = held.convertToFormat(QImage.Format_RGBA8888)
                data = np.ndarray((image.height(), image.width(), 4), dtype=np.uint8,
                                  buffer=image.bits(), strides=(image.bytesPerLine(), 4, 1))
                orange = (data[:, :, 0] > 200) & (data[:, :, 1] > 90) & (
                    data[:, :, 1] < 175) & (data[:, :, 2] < 65)
                self.assertGreater(int(orange.sum()), 20, accent)
            self.assertEqual(resting, self.capture(self.cue, active=False))

    def test_play_hover_is_inert_in_idle_pressed_and_playing_states(self):
        import numpy as np
        reference = None
        for accent in ('#00c301', '#168fc0', '#e34c26', '#7b2fbe', '#fffdf4'):
            theme.set_accent(accent); self.controller.update(); self.app.processEvents()
            resting = self.capture(self.play, playing=False)
            self.assertEqual(resting, self.capture(self.play, hover=True, playing=False))
            for mouse in (False, True):
                active = self.capture(self.play, down=mouse, playing=not mouse)
                self.assertNotEqual(active, resting)
                image = active.convertToFormat(QImage.Format_RGBA8888)
                data = np.ndarray((image.height(), image.width(), 4), dtype=np.uint8,
                                  buffer=image.bits(), strides=(image.bytesPerLine(), 4, 1))
                green = (data[:, :, 1] > 130) & (data[:, :, 0] < 65) & (data[:, :, 2] < 65)
                self.assertGreater(int(green.sum()), 20, accent)
                if reference is None:
                    reference = active
                self.assertEqual(active, reference, accent)
                self.assertEqual(active, self.capture(
                    self.play, hover=True, down=mouse, playing=not mouse))
            self.assertEqual(resting, self.capture(self.play, playing=False))


class ExportProgressTheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.controller=install(cls.app)

    def setUp(self):
        self.original=(theme.PURPLE,theme.ON_ACCENT)

    def tearDown(self):
        theme.set_accent(*self.original);self.controller.update()

    @staticmethod
    def pixels(widget):
        import numpy as np
        image=widget.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        return np.ndarray((image.height(),image.width(),4),dtype=np.uint8,
                          buffer=image.bits(),strides=(image.bytesPerLine(),4,1)).copy()

    def test_export_indicators_follow_current_accent_on_open_and_live_change(self):
        import numpy as np
        theme.set_accent('#ffffff','#000000');self.controller.update()
        dialog=ExportDialog([Playlist('Playlist',[],pid='p')],
                            [dict(path='usb',name='TEST-1',label='USB',free=100,kind='generic_usb')])
        self.addCleanup(dialog.deleteLater);self.addCleanup(dialog.close)
        dialog.playlists.topLevelItem(0).setCheckState(0,Qt.Checked)
        dialog.show();self.app.processEvents()
        self.assertGreater(dialog.devices.font().pixelSize(),16)
        for accent in ('#ffffff','#168fc0','#e27239'):
            theme.set_accent(accent,'#000000');self.controller.update();self.app.processEvents()
            expected=QColor(theme.PURPLE_LIGHT).getRgb()[:3]
            for tree in (dialog.playlists,dialog.devices):
                pixels=self.pixels(tree)
                self.assertGreater(int(np.all(pixels[:,:,:3]==expected,axis=2).sum()),50)
                self.assertEqual(int(np.all(pixels[:,:,:3]==(168,85,247),axis=2).sum()),0)

    def test_progress_text_uses_saved_lettering_only_on_the_filled_region(self):
        theme.set_accent('#ffffff','#000000');self.controller.update()
        bar=ThemeProgressBar();bar.setFixedSize(350,24)
        self.addCleanup(bar.deleteLater);self.addCleanup(bar.close)
        bar.show();self.app.processEvents()
        for value,black,light in ((0,False,True),(25,False,True),(50,True,True),(65,True,False),(100,True,False)):
            bar.setValue(value);self.app.processEvents();pixels=self.pixels(bar)
            center=pixels[:,pixels.shape[1]//2-30:pixels.shape[1]//2+30,:3]
            dark_ink=(center[:,:,0]<80)&(center[:,:,0]==center[:,:,1])&(center[:,:,1]==center[:,:,2])
            light_ink=(center[:,:,0]>150)&(center[:,:,2].astype(int)>center[:,:,0].astype(int)+2)
            self.assertEqual(bool(dark_ink.any()),black,value)
            self.assertEqual(bool(light_ink.any()),light,value)
        theme.set_accent('#ffffff','#e27239');self.controller.update();self.app.processEvents()
        pixels=self.pixels(bar)
        colored=(pixels[:,:,0]>180)&(pixels[:,:,1]<180)&(pixels[:,:,2]<140)
        self.assertGreater(int(colored.sum()),3)
