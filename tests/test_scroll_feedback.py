"""Trackpad input lights the actual visible thumb, including overlay browsers."""
import unittest

from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QTableWidget,QTableWidgetItem

from desktop import theme
from desktop.browser_scrollbar import BrowserScrollBar
from desktop.theme_controller import install
from desktop.prep_grid import _grid_icon


class ScrollFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.controller=install(cls.app)

    def test_trackpad_momentum_keeps_overlay_thumb_in_live_theme_color_then_returns_to_idle(self):
        table=QTableWidget(100,1);table.setItem(0,0,QTableWidgetItem('Track'))
        edge=BrowserScrollBar(table,table.horizontalHeader());table.resize(400,240);table.show();self.app.processEvents()
        previous=(theme.PURPLE,theme.ON_ACCENT)
        try:
            for color in ('#7b2fbe','#16a68f'):
                theme.set_accent(color);self.controller.update()
                event=QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(0,-8),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
                self.app.sendEvent(table.viewport(),event);self.app.processEvents()
                self.assertTrue(edge.bar.property('wheelScrolling'))
                image=edge.bar.grab().toImage()
                self.assertTrue(any(image.pixelColor(x,y)==QColor(color) for y in range(image.height()) for x in range(image.width())))
                QTest.qWait(160);self.app.sendEvent(table.viewport(),event);QTest.qWait(180)
                self.assertTrue(edge.bar.property('wheelScrolling'))
                end=QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(),QPoint(),Qt.NoButton,Qt.NoModifier,Qt.ScrollEnd,False)
                self.app.sendEvent(table.viewport(),end)
                self.assertFalse(edge.bar.property('wheelScrolling'))
                self.assertFalse(edge.bar._soundbox_scroll_feedback.isActive())
                # A conventional mouse has no phase/end event. Its feedback
                # must also clear promptly, rather than the former 300 ms tail.
                wheel=QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
                self.app.sendEvent(table.viewport(),wheel)
                self.assertTrue(edge.bar.property('wheelScrolling'))
                QTest.qWait(60);self.assertFalse(edge.bar.property('wheelScrolling'))
        finally:
            table.close();table.deleteLater();theme.set_accent(*previous);self.controller.update();self.app.processEvents()

    def test_bar_start_marker_uses_the_same_fixed_red_as_quantize(self):
        image=_grid_icon('downbeat').pixmap(72,48).toImage()
        self.assertTrue(any(image.pixelColor(x,y)==QColor(theme.GRID_RED) for y in range(image.height()) for x in range(image.width())))
        self.assertIn('color: '+theme.GRID_RED,theme.STATIC_QSS)
