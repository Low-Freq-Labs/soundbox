"""Real viewport drag events target actual native folders before expansion."""
from copy import deepcopy
import json
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QColor, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent,
                          QDropEvent, QMouseEvent, QPixmap)
from PySide6.QtTest import QTest

from desktop import theme
from desktop.trees import NATIVE_BINDING_ROLE, RB_NODE_KIND_ROLE, RB_NODE_ROLE
from soundbox import engine
from tests import test_smart_playlist_move_ui as fixtures


class CollapsedNativeFolderDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.SmartMovePublication.setUpClass()

    def setUp(self):
        roles = {name: getattr(theme, name) for name in
                 ('PURPLE', 'PURPLE_LIGHT', 'PURPLE_DARK', 'ON_ACCENT', 'SUBTLE', 'QSS')}
        self.addCleanup(lambda: [setattr(theme, name, value) for name, value in roles.items()])
        self.fixture = fixtures.SmartMovePublication()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.w = self.fixture.w
        self.tree = self.w.tree
        self.tree.setStyleSheet(theme.STATIC_QSS)
        self.w.show()
        self.fixture.case.app.processEvents()
        self.target = self.w._find_tree_item('', '3')
        self.target.setExpanded(False)
        self.tree.doItemsLayout()
        self.fixture.case.app.processEvents()
        self.moves = []
        self.placements = []
        self.orders = []
        self.tree.native_moved.connect(lambda *args: self.moves.append(args))
        self.tree.native_placed.connect(lambda *args: self.placements.append(args))
        self.tree.native_reordered.connect(lambda *args: self.orders.append(args))
        self.enterContext(mock.patch.object(self.w.playlist_controller, 'node_move_reason', return_value=''))
        self.request = self.enterContext(mock.patch.object(self.w, '_native_request', return_value=True))

    def events(self, identity, point):
        tree = self.tree
        source = self.w._find_tree_item('', identity)
        mime = tree.mimeData([source])

        # Qt constructs source() from its platform drag manager. Supply only
        # that owned source identity while exercising real Qt event delivery.
        class Enter(QDragEnterEvent):
            def source(self):
                return tree

        class Move(QDragMoveEvent):
            def source(self):
                return tree

        class Drop(QDropEvent):
            def source(self):
                return tree

        events = (Enter(point, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier),
                  Move(point, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier),
                  Drop(QPointF(point), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier))
        for event in events:
            event._owned_mime = mime  # Qt drag events borrow this QObject.
        return events

    def send(self, event):
        QCoreApplication.sendEvent(self.tree.viewport(), event)
        self.assertTrue(event.isAccepted(), f'{type(event).__name__} refused the exact internal native drag')

    def point(self, zone):
        rect = self.tree.visualItemRect(self.target)
        offset = {'above': 2, 'inside': rect.height()//2, 'below': rect.height()-3}[zone]
        return QPoint(rect.center().x(), rect.top()+offset)

    def pixels(self):
        pixmap = QPixmap(self.tree.viewport().size())
        self.tree.viewport().render(pixmap)
        return pixmap.toImage()

    def test_real_native_folder_payload_and_reused_items_keep_their_exact_kinds(self):
        summary = engine.summary(self.w.lib)
        folder = next(node for node in summary['tree']['children'] if node['pid'] == '3')
        self.assertEqual(folder['attribute'], 1)
        for identity, kind in (('1', 1), ('2', 4), ('3', 1), ('4', 0), ('7', 1)):
            with self.subTest(identity=identity):
                item = self.w._find_tree_item('', identity)
                self.assertEqual(item.data(0, RB_NODE_KIND_ROLE), kind)
                before_binding = deepcopy(item.data(0, NATIVE_BINDING_ROLE))
                item.setData(0, RB_NODE_KIND_ROLE, None)
                self.w._populate_tree(summary)
                self.assertIs(self.w._find_tree_item('', identity), item)
                self.assertEqual(item.data(0, RB_NODE_KIND_ROLE), kind)
                self.assertEqual(item.data(0, NATIVE_BINDING_ROLE), before_binding)

    def test_collapsed_populated_folder_accepts_all_node_kinds_directly_inside(self):
        before = deepcopy(self.w.lib.source_playlist_catalog)
        self.assertGreater(self.target.childCount(), 0)
        self.assertFalse(self.target.isExpanded())
        self.assertTrue(self.target.flags() & Qt.ItemIsDropEnabled)
        for identity in ('5', '2', '7'):
            with self.subTest(identity=identity):
                enter, move, drop = self.events(identity, self.point('inside'))
                self.send(enter)
                self.send(move)
                self.assertEqual(self.tree._hover.data(RB_NODE_ROLE), '3')
                self.assertTrue(self.tree._hover_outline)
                self.assertIsNone(self.tree._insert_y)
                self.assertFalse(self.target.isExpanded())
                self.send(drop)
                self.assertEqual(self.moves[-1], (identity, '3'))
                self.assertEqual(self.request.call_args.args, ('move',))
                self.assertEqual(self.request.call_args.kwargs['parent'], '3')
                self.assertFalse(self.tree._hover.isValid())
                self.assertIsNone(self.tree._insert_y)
        self.assertEqual(self.moves, [('5', '3'), ('2', '3'), ('7', '3')])
        self.assertEqual(self.placements, [])
        self.assertEqual(self.orders, [])
        self.assertEqual(self.w.lib.source_playlist_catalog, before)
        self.fixture.case.source.assert_not_called()

    def test_collapsed_folder_has_distinct_above_inside_below_native_destinations(self):
        for zone, parent, after in (('above', 'root', False), ('inside', '3', None), ('below', 'root', True)):
            with self.subTest(zone=zone):
                enter, move, drop = self.events('5', self.point(zone))
                self.send(enter)
                self.send(move)
                if zone == 'inside':
                    self.assertEqual(self.tree._hover.data(RB_NODE_ROLE), '3')
                    self.assertIsNone(self.tree._insert_y)
                else:
                    self.assertFalse(self.tree._hover.isValid())
                    self.assertIsNotNone(self.tree._insert_y)
                self.send(drop)
                request = self.request.call_args.kwargs
                self.assertEqual(request['identity'], '5')
                self.assertEqual(request['parent'], parent)
                if after is None:
                    self.assertIsNone(request['anchor_id'])
                    self.assertFalse(request['after'])
                else:
                    self.assertEqual((request['anchor_id'], request['after']), ('3', after))
        self.assertEqual(self.moves, [('5', '3')])
        self.assertEqual(self.placements, [('5', 'root', '3', False), ('5', 'root', '3', True)])

    def test_inside_has_two_accent_boundaries_and_insertion_has_exactly_one_without_filling_label(self):
        rect = self.tree.visualItemRect(self.target)
        x = self.tree.viewport().width()-16
        for color in ('#ffffff', '#12ab34'):
            with mock.patch.object(theme, 'PURPLE_LIGHT', color):
                accent = QColor(color).rgb()
                for zone in ('above', 'inside', 'below'):
                    with self.subTest(color=color, zone=zone):
                        enter, move, drop = self.events('5', self.point(zone))
                        self.send(enter)
                        self.send(move)
                        image = self.pixels()
                        top = image.pixelColor(x, rect.top()).rgb()
                        bottom = image.pixelColor(x, rect.bottom()).rgb()
                        self.assertEqual(top == accent, zone in ('above', 'inside'))
                        self.assertEqual(bottom == accent, zone in ('below', 'inside'))
                        self.assertNotEqual(image.pixelColor(x, rect.center().y()).rgb(), accent)
                        self.send(drop)

    def test_collapsed_empty_folder_accepts_folder_and_full_populated_subtree(self):
        self.target = self.w._find_tree_item('', '7')
        self.target.setExpanded(False)
        self.tree.doItemsLayout()
        before = deepcopy(self.w.lib.source_playlist_catalog)
        enter, move, drop = self.events('1', self.point('inside'))
        self.send(enter)
        self.send(move)
        self.assertEqual(self.tree._hover.data(RB_NODE_ROLE), '7')
        self.send(drop)
        self.assertEqual(self.moves, [('1', '7')])
        self.assertEqual(self.request.call_args.kwargs, dict(identity='1', parent='7', anchor_id=None, after=False))
        self.assertEqual(self.w.lib.source_playlist_catalog, before)
        self.assertIs(self.w._find_tree_item('', '2').parent(), self.w._find_tree_item('', '1'))

    def test_actual_nonselected_native_folder_starts_its_own_drag(self):
        source = self.w._find_tree_item('', '7')
        self.tree.setCurrentItem(self.w._find_tree_item('', '2'))
        point = self.tree.visualItemRect(source).center()
        QTest.mousePress(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        moved = point+QPoint(40, 0)
        movement = QMouseEvent(QEvent.MouseMove, QPointF(moved),
            QPointF(self.tree.viewport().mapToGlobal(moved)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        with mock.patch('desktop.trees._start_chip_drag') as start:
            self.tree.mouseMoveEvent(movement)
        QTest.mouseRelease(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, moved)
        start.assert_called_once()
        captured = json.loads(bytes(start.call_args.args[1].data(self.tree._native_mime)))
        self.assertEqual(captured['id'], '7')
        self.assertEqual(captured['uuid'], source.data(0, NATIVE_BINDING_ROLE)['uuid'])

    def test_leave_and_invalid_self_target_clear_both_inside_boundaries(self):
        for ending in ('leave', 'self'):
            with self.subTest(ending=ending):
                enter, move, _drop = self.events('5', self.point('inside'))
                self.send(enter)
                self.send(move)
                self.assertTrue(self.tree._hover_outline)
                if ending == 'leave':
                    QCoreApplication.sendEvent(self.tree.viewport(), QDragLeaveEvent())
                else:
                    source = self.w._find_tree_item('', '5')
                    source.parent().setExpanded(True)
                    self.tree.doItemsLayout()
                    _enter, invalid, _drop = self.events('5', self.tree.visualItemRect(source).center())
                    QCoreApplication.sendEvent(self.tree.viewport(), invalid)
                    self.assertFalse(invalid.isAccepted())
                self.assertFalse(self.tree._hover.isValid())
                self.assertFalse(self.tree._hover_outline)
                self.assertIsNone(self.tree._insert_y)
                self.assertEqual(self.moves, [])

    def test_same_identity_reconciliation_clears_inside_feedback_without_replacing_folder(self):
        enter, move, _drop = self.events('5', self.point('inside'))
        self.send(enter)
        self.send(move)
        self.assertTrue(self.tree._hover_outline)
        self.assertEqual(self.tree._hover.data(RB_NODE_ROLE), '3')
        item = self.target
        self.w._populate_tree(engine.summary(self.w.lib))
        self.assertIs(self.w._find_tree_item('', '3'), item)
        self.assertFalse(self.tree._hover.isValid())
        self.assertFalse(self.tree._hover_outline)
        self.assertIsNone(self.tree._insert_y)
        rect = self.tree.visualItemRect(item)
        image = self.pixels()
        accent = QColor(theme.PURPLE_LIGHT).rgb()
        x = self.tree.viewport().width()-16
        self.assertNotEqual(image.pixelColor(x, rect.top()).rgb(), accent)
        self.assertNotEqual(image.pixelColor(x, rect.bottom()).rgb(), accent)
        QCoreApplication.sendEvent(self.tree.viewport(), QDragLeaveEvent())


if __name__ == '__main__':
    unittest.main()
