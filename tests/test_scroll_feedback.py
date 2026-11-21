"""Trackpad input lights the actual visible thumb, including overlay browsers."""
import unittest

from PySide6.QtCore import QEvent,QObject,QPoint,QPointF,Qt
from PySide6.QtGui import QWheelEvent,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QListWidget,QScrollArea,QTableWidget,QTableWidgetItem,QTextEdit,QWidget

from desktop import theme
from desktop.browser_scrollbar import BrowserScrollBar
from desktop.theme_controller import install, _SCROLL_FEEDBACK_IDLE_MS
from desktop.prep_grid import _grid_icon


class ScrollFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);cls.controller=install(cls.app)

    def test_trackpad_momentum_keeps_overlay_thumb_in_live_theme_color_then_returns_to_idle(self):
        table=QTableWidget(100,1);table.setItem(0,0,QTableWidgetItem('Track'))
        edge=BrowserScrollBar(table,table.horizontalHeader());table.resize(400,240);table.show();self.app.processEvents()
        previous=(theme.PURPLE,theme.ON_ACCENT)
        try:
            for color in ('#7b2fbe','#16a68f','#ffffff'):
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
                self.assertTrue(edge.bar.property('wheelScrolling'))
                self.assertTrue(edge.bar._soundbox_scroll_feedback.isActive())
                QTest.qWait(_SCROLL_FEEDBACK_IDLE_MS + 40)
                self.assertFalse(edge.bar.property('wheelScrolling'))
                self.assertFalse(edge.bar._soundbox_scroll_feedback.isActive())
                # A conventional mouse has no phase/end event. Its feedback
                # bridges adjacent ticks and still clears after idle.
                wheel=QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
                self.app.sendEvent(table.viewport(),wheel)
                self.assertTrue(edge.bar.property('wheelScrolling'))
                QTest.qWait(_SCROLL_FEEDBACK_IDLE_MS + 40);self.assertFalse(edge.bar.property('wheelScrolling'))
        finally:
            table.close();table.deleteLater();theme.set_accent(*previous);self.controller.update();self.app.processEvents()

    def test_rapid_reversals_keep_one_highlight_until_idle_and_do_not_change_geometry(self):
        table=QTableWidget(100,1)
        edge=BrowserScrollBar(table,table.horizontalHeader())
        table.resize(400,240);table.show();self.app.processEvents()
        changes=[];geometry=[]
        class Probe(QObject):
            def eventFilter(self,obj,event):
                if event.type()==QEvent.DynamicPropertyChange and bytes(event.propertyName())==b'wheelScrolling':
                    changes.append(bool(obj.property('wheelScrolling')))
                if event.type() in (QEvent.Hide,QEvent.Show,QEvent.Resize):
                    geometry.append(event.type())
                return False
        probe=Probe(edge.bar);edge.bar.installEventFilter(probe)
        def wheel(phase,delta):
            event=QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(0,delta),QPoint(0,delta*15),
                              Qt.NoButton,Qt.NoModifier,phase,False)
            self.app.sendEvent(table.viewport(),event)
            self.app.processEvents()
        try:
            for index in range(12):
                wheel(Qt.ScrollBegin,0)
                wheel(Qt.ScrollUpdate,8 if index%2 else -8)
                wheel(Qt.ScrollEnd,0)
                QTest.qWait((8,16,25,33,67)[index%5])
                self.assertTrue(edge.bar.property('wheelScrolling'))
            self.assertEqual(changes,[True])
            QTest.qWait(_SCROLL_FEEDBACK_IDLE_MS + 40)
            self.assertEqual(changes,[True,False])
            self.assertEqual(geometry,[])
        finally:
            table.close();table.deleteLater();self.app.processEvents()

    def test_phaseless_wheel_ticks_bridge_gaps_and_end_alone_does_not_light_an_axis(self):
        table=QTableWidget(100,1)
        edge=BrowserScrollBar(table,table.horizontalHeader())
        table.resize(400,240);table.show();self.app.processEvents()
        try:
            self.controller._scroll_feedback(edge.bar,phase=Qt.ScrollEnd)
            self.assertFalse(edge.bar.property('wheelScrolling'))
            self.assertFalse(hasattr(edge.bar,'_soundbox_scroll_feedback'))
            for _ in range(3):
                self.controller._scroll_feedback(edge.bar)
                QTest.qWait(50)
                self.assertTrue(edge.bar.property('wheelScrolling'))
            QTest.qWait(_SCROLL_FEEDBACK_IDLE_MS + 40)
            self.assertFalse(edge.bar.property('wheelScrolling'))
        finally:
            table.close();table.deleteLater();self.app.processEvents()

    def test_bar_start_marker_uses_the_same_fixed_red_as_quantize(self):
        image=_grid_icon('downbeat').pixmap(72,48).toImage()
        self.assertTrue(any(image.pixelColor(x,y)==QColor(theme.GRID_RED) for y in range(image.height()) for x in range(image.width())))
        self.assertIn('color: '+theme.GRID_RED,theme.STATIC_QSS)

    def test_smart_tag_list_scroll_hosts_and_text_dialogs_share_browser_thumb_and_idle_feedback(self):
        from desktop.tag_creation import NewSmartPlaylistDialog
        from soundbox.library import LibraryTag
        dialog = NewSmartPlaylistDialog([LibraryTag(str(i), f'Tag {i}', source_uuid=f'uuid-{i}')
                                        for i in range(80)], [])
        area = QScrollArea();host = QWidget();host.setMinimumSize(250, 2000);area.setWidget(host)
        text = QTextEdit();text.setPlainText('\n'.join(f'Line {i}' for i in range(100)))
        listing = QListWidget();listing.addItems([f'Item {i}' for i in range(100)])
        windows = (dialog, area, text, listing)
        previous = theme.PURPLE, theme.ON_ACCENT
        def contains(image, color):
            return any(image.pixelColor(x,y)==QColor(color)
                       for y in range(image.height()) for x in range(image.width()))
        try:
            theme.set_accent('#16a68f');self.controller.update()
            for window, view in zip(windows, (dialog.tags, area, text, listing)):
                if window is not dialog:window.resize(350, 250)
                window.show();self.app.processEvents();self.app.processEvents()
                with self.subTest(view=type(view).__name__):
                    edge = view._edge_scrollbar
                    self.assertIsInstance(edge, BrowserScrollBar)
                    self.assertIs(edge.bar, view._soundbox_overlay_scrollbar)
                    self.assertEqual(view.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                    self.assertGreater(edge.bar.maximum(), 0)
                    self.assertTrue(edge.bar.isVisible())
                    QTest.mouseMove(view.viewport(), QPoint(8, 8));self.app.processEvents()
                    self.assertTrue(contains(edge.bar.grab().toImage(), theme.BORDER))
                    QTest.mouseMove(edge.bar, QPoint(edge.bar.width() // 2, 8));self.app.processEvents()
                    self.assertTrue(contains(edge.bar.grab().toImage(), theme.PURPLE))
                    QTest.mouseMove(view.viewport(), QPoint(8, 8));self.app.processEvents()
                    self.assertTrue(contains(edge.bar.grab().toImage(), theme.BORDER))
                    event = QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(0,-8),QPoint(0,-120),
                                        Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
                    self.app.sendEvent(view.viewport(), event)
                    self.assertTrue(edge.bar.property('wheelScrolling'))
                    self.assertTrue(contains(edge.bar.grab().toImage(), theme.PURPLE))
                    self.assertEqual(edge.bar.value(), edge.native.value())
                    end = QWheelEvent(QPointF(30,60),QPointF(30,60),QPoint(),QPoint(),
                                      Qt.NoButton,Qt.NoModifier,Qt.ScrollEnd,False)
                    self.app.sendEvent(view.viewport(), end);QTest.qWait(_SCROLL_FEEDBACK_IDLE_MS + 40)
                    self.assertFalse(edge.bar.property('wheelScrolling'))
                    self.assertTrue(contains(edge.bar.grab().toImage(), theme.BORDER))
                window.hide()
        finally:
            for window in windows:window.close();window.deleteLater()
            theme.set_accent(*previous);self.controller.update();self.app.processEvents()

    def test_hidden_popup_reopens_idle_when_its_trackpad_end_was_not_delivered(self):
        view = QListWidget();view.addItems([str(i) for i in range(100)])
        view.resize(300, 200);view.show();self.app.processEvents()
        try:
            edge = view._edge_scrollbar
            self.controller._scroll_feedback(edge.bar, phase=Qt.ScrollUpdate)
            self.assertTrue(edge.bar.property('wheelScrolling'))
            view.hide();self.app.processEvents()
            self.assertFalse(edge.bar.property('wheelScrolling'))
            view.show();self.app.processEvents()
            self.assertIs(view._edge_scrollbar, edge)
            self.assertFalse(edge.bar.property('wheelScrolling'))
        finally:
            view.close();view.deleteLater();self.app.processEvents()
