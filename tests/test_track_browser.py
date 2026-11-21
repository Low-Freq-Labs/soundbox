"""Private widget gestures: containers expand, songs alone receive selection."""
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidgetItem

from desktop.trees import TrackBrowserTree


class TrackBrowserGestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = TrackBrowserTree()
        self.tree.setHeaderLabels(['Playlist / Track'])
        self.folder = QTreeWidgetItem(self.tree, ['GENRES'])
        self.playlist = QTreeWidgetItem(self.folder, ['HOUSE'])
        self.track = QTreeWidgetItem(self.playlist, ['A song'])
        for item in (self.folder, self.playlist):
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self.tree.resize(600, 300)
        self.tree.show()
        self.app.processEvents()
        self.addCleanup(self.close)

    def close(self):
        self.tree.close()
        self.tree.deleteLater()
        self.app.processEvents()

    def click_label(self, item):
        rect = self.tree.visualItemRect(item)
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 85, rect.center().y()))

    def test_nested_containers_toggle_without_highlight_and_track_stays_selected(self):
        self.click_label(self.folder)
        self.assertTrue(self.folder.isExpanded())
        self.click_label(self.playlist)
        self.assertTrue(self.playlist.isExpanded())
        self.assertEqual(self.tree.selectedItems(), [])
        self.click_label(self.track)
        self.assertEqual(self.tree.selectedItems(), [self.track])
        self.click_label(self.playlist)
        self.assertFalse(self.playlist.isExpanded())
        self.assertFalse(self.folder.isSelected())
        self.assertFalse(self.playlist.isSelected())
        self.click_label(self.folder)
        self.assertFalse(self.folder.isExpanded())

    def test_branch_toggles_once_and_keyboard_keeps_container_unselected(self):
        rect = self.tree.visualItemRect(self.folder)
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, pos=QPoint(rect.left() - 8, rect.center().y()))
        self.assertTrue(self.folder.isExpanded())
        self.tree.setCurrentItem(self.folder)
        QTest.keyClick(self.tree, Qt.Key_Left)
        self.assertFalse(self.folder.isExpanded())
        QTest.keyClick(self.tree, Qt.Key_Right)
        self.assertTrue(self.folder.isExpanded())
        self.assertFalse(self.folder.isSelected())

    def test_playlist_checkbox_does_not_change_expansion(self):
        self.folder.setFlags(self.folder.flags() | Qt.ItemIsUserCheckable)
        self.folder.setCheckState(0, Qt.Unchecked)
        index = self.tree.indexFromItem(self.folder)
        option = QStyleOptionViewItem()
        option.initFrom(self.tree)
        option.rect = self.tree.visualRect(index)
        self.tree.itemDelegateForIndex(index).initStyleOption(option, index)
        rect = self.tree.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, self.tree)
        QTest.mouseClick(self.tree.viewport(), Qt.LeftButton, pos=rect.center())
        self.assertEqual(self.folder.checkState(0), Qt.Checked)
        self.assertFalse(self.folder.isExpanded())
        self.assertFalse(self.folder.isSelected())

    def test_keyboard_checkbox_and_navigation_keep_containers_unselected(self):
        self.folder.setFlags(self.folder.flags() | Qt.ItemIsUserCheckable)
        self.folder.setCheckState(0, Qt.Unchecked)
        self.tree.setCurrentItem(self.folder)
        QTest.keyClick(self.tree, Qt.Key_Space)
        self.assertEqual(self.folder.checkState(0), Qt.Checked)
        self.assertFalse(self.folder.isExpanded())
        QTest.keyClick(self.tree, Qt.Key_Right)
        QTest.keyClick(self.tree, Qt.Key_Down)
        self.assertIs(self.tree.currentItem(), self.playlist)
        self.assertFalse(self.playlist.isSelected())
        QTest.keyClick(self.tree, Qt.Key_Right)
        QTest.keyClick(self.tree, Qt.Key_Down)
        self.assertIs(self.tree.currentItem(), self.track)
        self.assertEqual(self.tree.selectedItems(), [self.track])

    def test_stock_playlist_click_keeps_order_actions_and_checked_deletion(self):
        from desktop.stock_device_view import StockIpodPages
        pages = StockIpodPages()
        self.addCleanup(pages.close)
        tree = pages.playlist_tree
        tree.resize(600, 300)
        pages.set_page(2)
        pages.resize(700, 500)
        pages.show()
        for key in ('one', 'two'):
            item = QTreeWidgetItem(tree, [key])
            item.setData(0, Qt.UserRole, 'managed:' + key)
            item.setData(0, Qt.UserRole + 3, key)
            item.setFlags((item.flags() & ~Qt.ItemIsSelectable) | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            QTreeWidgetItem(item, ['Song'])
        pages._playlist_order_available = True
        pages._has_snapshot = pages._playlist_sync_available = True
        pages._playlist_delete_available = True
        pages._committed_playlist_order = ('one', 'two')
        self.app.processEvents()
        first = tree.topLevelItem(0)
        rect = tree.visualItemRect(first)
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 85, rect.center().y()))
        self.assertIs(tree.currentItem(), first)
        self.assertEqual(tree.selectedItems(), [])
        self.assertTrue(first.isExpanded())
        self.assertTrue(pages._playlist_order_ready())
        QTest.keyClick(tree, Qt.Key_Space)
        self.assertEqual(first.checkState(0), Qt.Checked)
        self.assertTrue(pages.delete_playlist_button.isEnabled())
        orders = []
        pages.playlist_order_requested.connect(orders.append)
        pages._drop_playlist_order(('two', 'one'))
        self.assertEqual(pages._managed_playlist_order(), ('two', 'one'))
        self.assertIs(tree.currentItem(), first)
        self.assertFalse(first.isSelected())
        self.assertEqual(orders, [('two', 'one')])
        self.assertFalse(pages._playlist_order_ready())
        deleted = []
        pages.delete_playlists_requested.connect(deleted.append)
        pages.delete_playlist_button.click()
        self.assertEqual(deleted, [(('managed:one', 'one'),)])


if __name__ == '__main__':
    unittest.main()
