"""Native playlist drops retain a safe visible destination across tree refresh."""
import unittest
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDragMoveEvent, QDropEvent, QDragLeaveEvent, QColor
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from desktop.trees import DeviceTree, PlaylistTree, PATH_ROLE, DEVPATH_ROLE, RB_NODE_ROLE, RB_NODE_KIND_ROLE
from desktop import theme


class NativePlaylistHover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree=PlaylistTree();self.tree.resize(300,200);self.tree.show()
        self.addCleanup(self.tree.close);self.addCleanup(self.tree.deleteLater)
        self.folder=self.row('Folder','1',1);self.target=self.row('Destination','2',0)
        self.app.processEvents();self.mime=QMimeData();self.mime.setText('trk\tsb-real-one,sb-real-two')

    def row(self,name,identity,kind):
        item=QTreeWidgetItem([name]);item.setData(0,PATH_ROLE,name)
        item.setData(0,RB_NODE_ROLE,identity);item.setData(0,RB_NODE_KIND_ROLE,kind)
        self.tree.addTopLevelItem(item);return item

    def move(self,item):
        pos=self.tree.visualItemRect(item).center() if item is not None else QPoint(290,190)
        event=QDragMoveEvent(pos,Qt.CopyAction,self.mime,Qt.LeftButton,Qt.NoModifier)
        self.tree.dragMoveEvent(event);return event

    def test_only_regular_playlist_highlights_and_leave_clears(self):
        self.assertTrue(self.move(self.target).isAccepted())
        self.assertEqual(self.target.background(0).color(),QColor(theme.PURPLE))
        self.assertFalse(self.move(self.folder).isAccepted())
        self.assertEqual(self.target.background(0).style(),Qt.NoBrush)
        self.move(self.target);self.tree.dragLeaveEvent(QDragLeaveEvent())
        self.assertEqual(self.target.background(0).style(),Qt.NoBrush)

    def test_refresh_invalidates_old_hover_and_drop_uses_current_native_id(self):
        self.move(self.target);self.tree.clear()
        self.target=self.row('Rebuilt destination','3',0);self.app.processEvents()
        self.assertTrue(self.move(self.target).isAccepted())
        hits=[];self.tree.tracks_dropped.connect(lambda pid,ids:hits.append((pid,ids)))
        event=QDropEvent(self.tree.visualItemRect(self.target).center(),Qt.CopyAction,
                         self.mime,Qt.LeftButton,Qt.NoModifier)
        self.tree.dropEvent(event)
        self.assertTrue(event.isAccepted());self.assertEqual(hits,[('3',['sb-real-one','sb-real-two'])])
        self.assertEqual(self.target.background(0).style(),Qt.NoBrush)


class DeviceHover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_refresh_during_drag_uses_rebuilt_target_and_clears_without_freed_item(self):
        tree=DeviceTree();tree.resize(300,200);tree.show()
        self.addCleanup(tree.close);self.addCleanup(tree.deleteLater)
        old=QTreeWidgetItem(['Old device']);old.setData(0,DEVPATH_ROLE,'/fixture/old')
        tree.addTopLevelItem(old);tree._set_hover(old)
        tree.clear()
        # Leave immediately after unplugging must also tolerate an invalid row.
        tree.dragLeaveEvent(QDragLeaveEvent())
        target=QTreeWidgetItem(['New device']);target.setData(0,DEVPATH_ROLE,'/fixture/new')
        tree.addTopLevelItem(target);self.app.processEvents()
        mime=QMimeData();mime.setText('cust\tTest playlist')
        pos=tree.visualItemRect(target).center()
        move=QDragMoveEvent(pos,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        tree.dragMoveEvent(move)
        self.assertEqual(target.background(0).color(),QColor(theme.PURPLE))
        hits=[];tree.dropped.connect(lambda path,items:hits.append((path,items)))
        drop=QDropEvent(pos,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
        tree.dropEvent(drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(hits,[('/fixture/new',[('cust','Test playlist')])])
        self.assertEqual(target.background(0).style(),Qt.NoBrush)
