"""Private Qt fixtures: split swatch/hex hit targets and inline sidebar names."""
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPushButton, QLineEdit, QTreeWidgetItem

from desktop.color_button import ColorButton
from desktop.theme_controller import install
from desktop.trees import SoundBoxTree


class InlineControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([]);install(cls.app)

    def setUp(self):
        self.frame=QFrame();layout=QHBoxLayout(self.frame)
        self.button=ColorButton('#FFFFFF');self.original=QPushButton('#FFFFFF')
        icon=QPixmap(16,16);icon.fill(QColor('#ffffff'))
        for button in (self.button,self.original):
            button.setIcon(QIcon(icon));button.setFocusPolicy(Qt.NoFocus);layout.addWidget(button)
        self.frame.show();self.app.processEvents()
        self.colors=[];self.clicks=[]
        self.button.hex_committed.connect(self.colors.append)
        self.button.clicked.connect(lambda:self.clicks.append(True))

    def tearDown(self):self.frame.close();self.frame.deleteLater();self.app.processEvents()

    def edit(self, text):
        QTest.mouseClick(self.button,Qt.LeftButton,pos=self.button._label_rect().center())
        self.assertFalse(self.button.editor.isHidden())
        QTest.keyClicks(self.button.editor,text)

    def test_same_appearance_and_swatch_opens_picker_while_label_types_hex(self):
        self.assertEqual(self.button.size(),self.original.size())
        self.assertEqual(self.button.grab().toImage(),self.original.grab().toImage())
        QTest.mouseClick(self.button,Qt.LeftButton,pos=QPoint(20,self.button.height()//2))
        self.assertEqual(self.clicks,[True]);self.assertTrue(self.button.editor.isHidden())
        self.edit('12ab34');QTest.keyClick(self.button.editor,Qt.Key_Return)
        self.assertEqual(self.colors,['#12ab34']);self.assertEqual(self.clicks,[True])

    def test_escape_and_invalid_hex_leave_existing_color_unchanged(self):
        self.edit('#123456');QTest.keyClick(self.button.editor,Qt.Key_Escape)
        self.assertEqual(self.colors,[])
        self.edit('nothex');QTest.keyClick(self.button.editor,Qt.Key_Return)
        self.assertEqual(self.colors,[]);self.assertEqual(self.button.text(),'#FFFFFF')

    def test_sidebar_editor_omits_icon_and_count_and_only_commits_on_accept(self):
        tree=SoundBoxTree();tree.setHeaderHidden(True);item=QTreeWidgetItem(['★  Rap  (301)']);tree.addTopLevelItem(item)
        tree.show();self.app.processEvents();saved=[]
        try:
            tree.rename_inline(item,'Rap',saved.append);self.app.processEvents()
            editor=tree.findChild(QLineEdit,'PlaylistNameEditor');self.assertIsNotNone(editor)
            self.assertEqual(editor.text(),'Rap')
            QTest.keyClicks(editor,'Drums');QTest.keyClick(editor,Qt.Key_Return);QTest.qWait(20)
            self.assertEqual(saved,['Drums']);self.assertEqual(item.text(0),'★  Rap  (301)')
            tree.rename_inline(item,'Rap',saved.append);self.app.processEvents()
            editor=tree.findChild(QLineEdit,'PlaylistNameEditor');QTest.keyClicks(editor,'Discard')
            QTest.keyClick(editor,Qt.Key_Escape);QTest.qWait(20);self.assertEqual(saved,['Drums'])
        finally:tree.close();tree.deleteLater()
