"""Accent roles and the supplied vector logo; no live state or sources."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from desktop import theme
from desktop.wordmark import Wordmark


class AccentTheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        theme.set_accent(theme.DEFAULT_ACCENT)

    def test_accent_roles_reset_and_invalid_saved_values_fail_to_default(self):
        theme.set_accent('#168fc0')
        self.assertEqual(theme.PURPLE, '#168fc0')
        self.assertNotIn('#7B2FBE', theme.QSS)
        self.assertNotIn('@ON_ACCENT@', theme.QSS)
        theme.set_accent('#ffee00', '#ffffff')
        self.assertIn('selection-color: #ffffff', theme.QSS)
        for value in (None, '', 'red', '#invalid', '#fff; color: red'):
            theme.set_accent(value)
            self.assertEqual(theme.PURPLE, theme.DEFAULT_ACCENT.lower())
            self.assertEqual(theme.PURPLE, '#ffffff')
            self.assertEqual(theme.PURPLE_LIGHT, '#ffffff')
            self.assertEqual(theme.ON_ACCENT, '#000000')

    def test_saved_purple_retains_its_original_roles_and_lettering(self):
        theme.set_accent('#7b2fbe', '#ffffff')
        self.assertEqual((theme.PURPLE, theme.PURPLE_LIGHT, theme.PURPLE_DARK),
                         ('#7b2fbe', '#a855f7', '#5b21b6'))
        self.assertEqual(theme.ON_ACCENT, '#ffffff')

    def test_logo_keeps_header_size_and_recolors_only_its_accent_layer(self):
        logo = Wordmark()
        self.assertEqual(logo.size().toTuple(), (270, 55))
        self.assertTrue(logo.renderer.isValid())
        counts = []
        for accent in ('#168fc0', '#ee8844'):
            logo.set_accent(accent)
            image = QImage(270, 55, QImage.Format_ARGB32)
            image.fill(QColor('#1b1d24'))
            painter = QPainter(image)
            logo.renderer.render(painter)
            painter.end()
            colors = [image.pixelColor(x, y).name() for y in range(55) for x in range(270)]
            self.assertGreater(colors.count(accent), 1000)
            counts.append(colors.count('#ffffff'))
        self.assertEqual(counts[0], counts[1])
        logo.deleteLater()
