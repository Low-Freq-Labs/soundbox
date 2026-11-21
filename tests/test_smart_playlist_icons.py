"""Smart status modifies the chosen sidebar glyph, without native mutations."""
import unittest
from unittest import mock

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeWidgetItem, QWidget, QVBoxLayout

from desktop.trees import (PATH_ROLE, RB_NODE_KIND_ROLE, UnifiedPlaylistTree,
                           sidebar_row_geometry)
from desktop import theme


class SmartPlaylistIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=QWidget();self.window._playlist_icon_kind=lambda _index,default:self.kind
        self.tree=UnifiedPlaylistTree();QVBoxLayout(self.window).addWidget(self.tree)
        self.item=QTreeWidgetItem(['Playlist']);self.tree.addTopLevelItem(self.item)
        self.item.setData(0,PATH_ROLE,'Playlist');self.kind='note'
        self.window.resize(320,180);self.window.show();self.app.processEvents()
        self.addCleanup(self.window.deleteLater);self.addCleanup(self.window.close)

    def render(self,attribute,*,selected=False,scale=1):
        self.item.setData(0,RB_NODE_KIND_ROLE,attribute)
        option=QStyleOptionViewItem();option.initFrom(self.tree)
        option.widget=self.tree;option.rect=QRect(16,0,260,24)
        option.state=QStyle.State_Enabled|QStyle.State_Active
        if selected:option.state|=QStyle.State_Selected
        image=QImage(280*scale,24*scale,QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(scale);image.fill(self.tree.palette().base().color())
        painter=QPainter(image)
        self.tree.itemDelegate().paint(painter,option,self.tree.indexFromItem(self.item));painter.end()
        return image

    def test_gear_preserves_each_base_and_changes_only_the_badge_area_at_both_pixel_scales(self):
        icon,_text=sidebar_row_geometry(QRect(16,0,260,24),self.tree.indentation())
        badge=QRect(int(icon.right()-8),int(icon.bottom()-8),8,8).adjusted(-1,-1,1,1)
        for self.kind in ('note','star','crate'):
            for scale in (1,2):
                for selected in (False,True):
                    with self.subTest(kind=self.kind,scale=scale,selected=selected):
                        ordinary=self.render(0,selected=selected,scale=scale)
                        smart=self.render(4,selected=selected,scale=scale)
                        changed=[(x,y) for y in range(smart.height()) for x in range(smart.width())
                                 if smart.pixel(x,y)!=ordinary.pixel(x,y)]
                        self.assertTrue(changed)
                        self.assertTrue(all(badge.contains(x//scale,y//scale) for x,y in changed))
                        # A reused row loses the modifier immediately when its
                        # authoritative native kind changes back to ordinary.
                        self.assertEqual(self.render(0,selected=selected,scale=scale),ordinary)

    def test_exact_native_kind_controls_badge_without_changing_icon_hit_geometry(self):
        ordinary=self.render(0)
        for attribute in (None,1,'4'):
            with self.subTest(attribute=attribute):self.assertEqual(self.render(attribute),ordinary)
        self.item.setData(0,RB_NODE_KIND_ROLE,None)
        smart=self.render(4);self.assertNotEqual(smart,ordinary)
        point=sidebar_row_geometry(self.tree.visualItemRect(self.item),self.tree.indentation())[0].center().toPoint()
        self.assertIs(self.tree.playlist_icon_item_at(point),self.item)
        self.item.setData(0,PATH_ROLE,None)
        # A folder/local row cannot become smart through its name or an icon
        # preference, even if an unrelated role contains the numeric value4.
        self.assertEqual(self.render(4),self.render(0))
        self.assertIsNone(self.tree.playlist_icon_item_at(point))

    def test_gear_hole_preserves_custom_row_background_and_white_selection(self):
        self.tree.setStyleSheet('QTreeView { background: #16171d; } '
                                'QTreeView::item:selected { background: white; color: #101010; }')
        self.item.setData(0,Qt.BackgroundRole,QBrush(QColor('#374f68')))
        icon,_text=sidebar_row_geometry(QRect(16,0,260,24),self.tree.indentation())
        x,y=int((icon.right()-4)*2),int((icon.bottom()-4)*2)
        with mock.patch.object(theme,'ON_ACCENT','#101010'):
            for selected,color in ((False,QColor('#374f68')),(True,QColor('white'))):
                with self.subTest(selected=selected):
                    image=self.render(4,selected=selected,scale=2)
                    self.assertEqual(image.pixelColor(x,y),color)
                    self.assertEqual(image.pixelColor(x,y),image.pixelColor(270*2,12*2))


if __name__=='__main__':unittest.main()
