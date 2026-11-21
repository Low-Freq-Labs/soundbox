"""Native tree placement gestures are predictable across complete folder trees."""
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from desktop import theme
from desktop.trees import (COLLECTION_ROLE, NATIVE_BINDING_ROLE, NATIVE_TREE_ROLE,
    PATH_ROLE, PINNED_TREE_ROLE, RB_NODE_KIND_ROLE, RB_NODE_ROLE,
    UnifiedPlaylistTree, _NativePlaylistMove)


class NativeTreeMoveGestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = UnifiedPlaylistTree()
        self.tree.setStyleSheet(theme.STATIC_QSS)
        self.tree.resize(420, 500)
        self.collection = QTreeWidgetItem(['Collection'])
        self.collection.setData(0, COLLECTION_ROLE, True)
        self.collection.setData(0, PINNED_TREE_ROLE, True)
        self.tree.addTopLevelItem(self.collection)
        self.genres = self.node('10', 'GENRES', kind=1)
        self.nested = self.node('11', 'Nested', parent=self.genres, kind=1)
        self.deep = self.node('12', 'Deeper', parent=self.nested, kind=1)
        self.anchor = self.node('13', 'Existing playlist', parent=self.deep)
        self.source = self.node('20', 'POPS', kind=4)
        self.source_child = self.node('21', 'Child', parent=self.source, kind=1)
        self.source_grandchild = self.node('22', 'Grandchild', parent=self.source_child, kind=1)
        self.tree.show()
        self.app.processEvents()
        self.addCleanup(self.close)

    def node(self, identity, label, *, parent=None, kind=0):
        item = QTreeWidgetItem([label])
        for role, value in ((RB_NODE_ROLE, identity), (RB_NODE_KIND_ROLE, kind),
                            (NATIVE_TREE_ROLE, True), (PINNED_TREE_ROLE, False)):
            item.setData(0, role, value)
        if kind != 1:
            item.setData(0, PATH_ROLE, label)
        item.setData(0, NATIVE_BINDING_ROLE, dict(source='/synthetic/master.db',
            library_id='isolated-tree', id=identity,
            uuid=f'00000000-0000-4000-8000-{int(identity):012d}', alias=None))
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
            parent.setExpanded(True)
        return item

    def close(self):
        self.tree.close()
        self.tree.deleteLater()
        self.app.processEvents()

    def event(self, point, *, mime=None):
        if mime is None:
            mime = self.tree.mimeData([self.source])
        return SimpleNamespace(source=lambda: self.tree, mimeData=lambda: mime,
            position=lambda: QPointF(point), acceptProposedAction=mock.Mock(), ignore=mock.Mock())

    def place_under(self, item, parent):
        if item.parent() is None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        else:
            item.parent().takeChild(item.parent().indexOfChild(item))
        parent.addChild(item)
        parent.setExpanded(True)
        self.app.processEvents()

    def test_folder_center_band_is_an_inside_target_for_every_native_node_kind(self):
        for kind in (0, 4, 1):
            self.source.setData(0, RB_NODE_KIND_ROLE, kind)
            for folder in (self.genres, self.nested, self.deep):
                rect = self.tree.visualItemRect(folder)
                # Distinct upper/lower quarters insert nearby; every pixel in
                # the middle half means inside, independent of expansion.
                band = max(2, rect.height()//4)
                for offset in range(band, rect.height()-band):
                    with self.subTest(kind=kind, folder=folder.text(0), offset=offset):
                        point = QPoint(rect.center().x(), rect.top()+offset)
                        destination = self.tree._native_drop_destination(point, '20')
                        self.assertIsInstance(destination, _NativePlaylistMove)
                        self.assertEqual(destination.parent, folder.data(0, RB_NODE_ROLE))
                        self.assertEqual(destination.anchor_id, '')
                        self.assertIsNone(destination.y)

    def test_folder_body_hover_and_append_signal_agree_without_mutating_qt_tree(self):
        moved = []
        ordered = []
        self.tree.native_moved.connect(lambda *args: moved.append(args))
        self.tree.native_reordered.connect(lambda *args: ordered.append(args))
        for kind in (0, 4, 1):
            self.source.setData(0, RB_NODE_KIND_ROLE, kind)
            event = self.event(self.tree.visualItemRect(self.deep).center())
            self.tree.dragMoveEvent(event)
            self.assertEqual(self.tree._hover.data(RB_NODE_ROLE), '12')
            self.assertIsNone(self.tree._insert_y)
            self.tree.dropEvent(event)
            event.acceptProposedAction.assert_called()
            self.assertIsNone(self.source.parent())
        self.assertEqual(moved, [('20', '12')]*3)
        self.assertEqual(ordered, [])

    def test_cross_parent_anchor_keeps_exact_parent_and_before_after_order(self):
        placed = []
        appended = []
        ordered = []
        self.tree.native_placed.connect(lambda *args: placed.append(args))
        self.tree.native_moved.connect(lambda *args: appended.append(args))
        self.tree.native_reordered.connect(lambda *args: ordered.append(args))
        rect = self.tree.visualItemRect(self.anchor)
        for kind in (0, 4, 1):
            self.source.setData(0, RB_NODE_KIND_ROLE, kind)
            for after in (False, True):
                with self.subTest(kind=kind, after=after):
                    point = QPoint(rect.center().x(), rect.bottom()-1 if after else rect.top()+1)
                    event = self.event(point)
                    destination = self.tree._native_drop_destination(point, '20')
                    self.assertIsInstance(destination, _NativePlaylistMove)
                    self.assertEqual((destination.parent, destination.anchor_id, destination.after),
                                     ('12', '13', after))
                    self.tree.dragMoveEvent(event)
                    self.assertIsNotNone(self.tree._insert_y)
                    self.assertFalse(self.tree._hover.isValid())
                    self.tree.dropEvent(event)
                    self.assertIsNone(self.source.parent())
        self.assertEqual(placed, [('20', '12', '13', after)
                                for _kind in (0, 4, 1) for after in (False, True)])
        self.assertEqual(appended, [])
        self.assertEqual(ordered, [])

    def test_nested_nodes_of_every_kind_can_return_to_root_whitespace(self):
        self.place_under(self.source, self.deep)
        moved = []
        self.tree.native_moved.connect(lambda *args: moved.append(args))
        point = QPoint(50, self.tree.viewport().height()-2)
        for kind in (0, 4, 1):
            self.source.setData(0, RB_NODE_KIND_ROLE, kind)
            destination = self.tree._native_drop_destination(point, '20')
            self.assertIsInstance(destination, _NativePlaylistMove)
            self.assertEqual(destination.parent, 'root')
            self.assertIsNotNone(destination.y)
            self.tree.dropEvent(self.event(point))
            self.assertIs(self.source.parent(), self.deep)
        self.assertEqual(moved, [('20', 'root')]*3)

    def test_folder_cannot_be_placed_inside_itself_or_any_descendant(self):
        self.source.setData(0, RB_NODE_KIND_ROLE, 1)
        moved = []
        ordered = []
        self.tree.native_moved.connect(lambda *args: moved.append(args))
        self.tree.native_reordered.connect(lambda *args: ordered.append(args))
        self.app.processEvents()
        for forbidden in (self.source, self.source_child, self.source_grandchild):
            rect = self.tree.visualItemRect(forbidden)
            for y in (rect.top()+1, rect.center().y(), rect.bottom()-1):
                with self.subTest(forbidden=forbidden.text(0), y=y):
                    point = QPoint(rect.center().x(), y)
                    self.assertIsNone(self.tree._native_drop_destination(point, '20'))
                    event = self.event(point)
                    self.tree.dropEvent(event)
                    event.ignore.assert_called_once()
        self.assertEqual(moved, [])
        self.assertEqual(ordered, [])
        self.assertIs(self.source_child.parent(), self.source)
        self.assertIs(self.source_grandchild.parent(), self.source_child)

    def test_changed_native_identity_is_refused_before_any_placement_signal(self):
        mime = self.tree.mimeData([self.source])
        binding = dict(self.source.data(0, NATIVE_BINDING_ROLE))
        binding['uuid'] = '00000000-0000-4000-8000-000000000099'
        self.source.setData(0, NATIVE_BINDING_ROLE, binding)
        moved = []
        self.tree.native_moved.connect(lambda *args: moved.append(args))
        event = self.event(self.tree.visualItemRect(self.deep).center(), mime=mime)
        self.tree.dropEvent(event)
        self.assertEqual(moved, [])
        event.ignore.assert_called_once()

    def test_pressed_nested_folder_drags_its_own_identity_without_playlist_selection(self):
        self.tree.setCurrentItem(self.source)
        point = self.tree.visualItemRect(self.nested).center()
        QTest.mousePress(self.tree.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        moved = point+QPoint(40, 0)
        movement = QMouseEvent(QEvent.MouseMove, QPointF(moved),
                              QPointF(self.tree.viewport().mapToGlobal(moved)),
                              Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        with mock.patch('desktop.trees._start_chip_drag') as start:
            self.tree.mouseMoveEvent(movement)
        start.assert_called_once()
        mime = start.call_args.args[1]
        captured = bytes(mime.data(self.tree._native_mime)).decode()
        self.assertIn('"id": "11"', captured)
        self.assertNotIn('"id": "20"', captured)
        self.assertIs(self.nested.parent(), self.genres)

    def test_folder_edge_inserts_by_that_folder_and_after_its_expanded_subtree(self):
        rect = self.tree.visualItemRect(self.nested)
        for after in (False, True):
            point = QPoint(rect.center().x(), rect.bottom()-1 if after else rect.top()+1)
            destination = self.tree._native_drop_destination(point, '20')
            self.assertIsInstance(destination, _NativePlaylistMove)
            self.assertEqual((destination.parent, destination.anchor_id, destination.after),
                             ('10', '11', after))
            expected = (self.tree.visualItemRect(self.anchor).bottom()
                        if after else rect.top())
            self.assertEqual(destination.y, expected)


if __name__ == '__main__':
    unittest.main()
