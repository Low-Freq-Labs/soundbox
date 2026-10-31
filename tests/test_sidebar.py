"""Shared sidebar geometry and the real folder interaction boundary."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from desktop.trees import (PlaylistFolderTree, sidebar_row_geometry, COLLECTION_ROLE,
                           ARTISTS_ROLE, CUSTOM_ROLE, PATH_ROLE)


class SidebarGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_folder_and_all_leaf_kinds_share_measured_centers_labels_and_24_point_rows(self):
        tree=PlaylistFolderTree();tree.setHeaderHidden(True);tree.setUniformRowHeights(True)
        tree.setStyleSheet('QTreeView::item { height:24px; padding:0px 2px; }')
        tree.resize(260,260)
        folder=QTreeWidgetItem(['Same']);folder.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        folder.setFlags(folder.flags() & ~Qt.ItemIsSelectable);tree.addTopLevelItem(folder)
        nested=QTreeWidgetItem(['Same']);nested.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        folder.addChild(nested)
        for role,glyph in ((COLLECTION_ROLE,'⌂'),(ARTISTS_ROLE,'♪'),(CUSTOM_ROLE,'★'),(PATH_ROLE,'♪')):
            leaf=QTreeWidgetItem([glyph+'  Same']);leaf.setData(0,role,'Same');tree.addTopLevelItem(leaf)
        folder.setExpanded(True);tree.show();self.app.processEvents()
        try:
            boxes=[sidebar_row_geometry(tree.visualItemRect(tree.topLevelItem(i)))
                   for i in range(tree.topLevelItemCount())]
            self.assertEqual({b[0].center().x() for b in boxes},{14.0})
            self.assertEqual({b[1].left() for b in boxes},{24.0})
            self.assertTrue(all(tree.visualItemRect(tree.topLevelItem(i)).height()==24
                                for i in range(tree.topLevelItemCount())))
            child_icon,child_label=sidebar_row_geometry(tree.visualItemRect(nested))
            self.assertEqual(child_icon.center().x(),30)
            self.assertEqual(child_label.left(),40)
            # Paint through the delegate and compare actual label ink, not just
            # rectangles that happen to contain the same arithmetic.
            image=tree.viewport().grab().toImage();scale=image.devicePixelRatio()
            starts=[]
            for i in range(tree.topLevelItemCount()):
                rect=tree.visualItemRect(tree.topLevelItem(i))
                ink=[]
                for x in range(round(24*scale),round(80*scale)):
                    if any(max(image.pixelColor(x,y).getRgb()[:3])>140
                           for y in range(round(rect.top()*scale),round((rect.top()+rect.height())*scale))):
                        ink.append(x)
                self.assertTrue(ink);starts.append(min(ink))
            self.assertEqual(len(set(starts)),1)
            # The new drawn chevron retains Qt expansion and folder-only clicks.
            QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=boxes[0][0].center().toPoint())
            self.assertFalse(folder.isExpanded());self.assertEqual(tree.selectedItems(),[])
            QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=boxes[0][1].center().toPoint())
            self.assertTrue(folder.isExpanded());self.assertEqual(tree.selectedItems(),[])
        finally:
            tree.close();tree.deleteLater();self.app.processEvents()
