"""USB playlists share the stock frame while keeping USB-only action authority."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QStyle, QStyleOptionViewItem

from desktop.devices import DeviceFacts
from desktop.stock_device_view import StockIpodPages
from desktop.usb_device_view import UsbDevicePages, UsbPlaylist, UsbSnapshot
from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong


class UsbPlaylistLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.pages=UsbDevicePages();self.addCleanup(self.dispose,self.pages)
        self.stock=StockIpodPages();self.addCleanup(self.dispose,self.stock)
        first=CdjSong('31','/Contents/first.wav','First song',artist='First artist')
        second=CdjSong('32','/Contents/second.mp3','Second song',artist='Second artist')
        self.songs=(first,second)
        self.folder=CdjPlaylist('10','0','Crates',True,())
        self.first=CdjPlaylist('11','10','First playlist',False,
            ((first.title,first.path),(second.title,second.path)),('31','32'))
        self.second=CdjPlaylist('12','0','Second playlist',False,())
        self.native=CdjLibrary((self.folder,self.first,self.second),'Generated',0,0,(),0,(),songs=self.songs)
        self.profile=SimpleNamespace(kind='generic_usb',label='USB',cdj_export=True)
        self.snapshot=UsbSnapshot(Path('/generated/no-device-io'),7,
            DeviceFacts(volume_uuid='generated',writable=True),native=self.native)
        self.publish(self.snapshot)
        self.actions=[];self.deleted=[]
        self.pages.primary_action_requested.connect(lambda:self.actions.append('export'))
        self.pages.delete_requested.connect(self.deleted.append)

    def dispose(self,pages):
        pages.close();pages.deleteLater();self.app.processEvents()

    def publish(self,snapshot):
        self.pages.set_snapshot(snapshot,name='Generated',profile=self.profile,free=0,drift=629)

    def item(self,identity):return self.pages._playlist_items[identity]

    def test_shared_headers_widths_frame_and_actions_match_stock(self):
        for pages,index in ((self.pages,1),(self.stock,2)):
            pages.resize(900,500);pages.set_page(index);pages.show()
        self.app.processEvents()
        for tree in (self.pages.playlist_tree,self.stock.playlist_tree):
            self.assertEqual(tree.columnCount(),3)
            self.assertEqual([tree.headerItem().text(i) for i in range(3)],
                             ['Playlist / Track','Tracks','Type'])
            self.assertEqual((tree.columnWidth(1),tree.columnWidth(2)),(70,150))
            self.assertEqual(tree.header().height(),26)
            self.assertEqual(tree.header().sectionResizeMode(0),QHeaderView.Stretch)
            self.assertTrue(tree.alternatingRowColors());self.assertTrue(tree.uniformRowHeights())
        usb=self.pages.stack.widget(1);stock=self.stock.stack.widget(2)
        self.assertEqual(usb.layout().contentsMargins(),stock.layout().contentsMargins())
        self.assertEqual(usb.layout().spacing(),stock.layout().spacing())
        self.assertEqual(usb.layout().itemAt(1).layout().contentsMargins(),
                         stock.layout().itemAt(1).layout().contentsMargins())
        self.assertEqual(self.pages.export_playlist_button.geometry(),self.stock.sync_playlist_button.geometry())
        self.assertEqual(self.pages.delete_playlist_button.geometry(),self.stock.delete_playlist_button.geometry())

    def test_titles_counts_and_numbered_lazy_children_use_only_three_columns(self):
        first=self.item('11');folder=self.item('10')
        self.assertEqual([first.text(i) for i in range(3)],['First playlist','2','USB'])
        self.assertEqual([folder.text(i) for i in range(3)],['Crates','','Folder'])
        self.assertEqual(first.childCount(),0)
        seen=[]
        def enrich(song,row):seen.append(song);row['title']='Prepared '+row['title'];row['art']='cover'
        self.pages.row_enricher=enrich
        first.setExpanded(True)
        self.assertEqual(seen,list(self.songs))
        self.assertEqual([first.child(i).text(0) for i in range(2)],
                         ['1. Prepared First song','2. Prepared Second song'])
        self.assertEqual([first.child(i).text(2) for i in range(2)],['USB','USB'])
        self.assertIs(first.child(0).data(0,Qt.UserRole),self.songs[0])
        self.assertEqual(self.pages._display_rows['31']['art'],'cover')
        self.assertFalse(first.child(0).flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(first.flags() & (Qt.ItemIsDragEnabled|Qt.ItemIsDropEnabled))
        self.assertFalse(folder.flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(first.flags() & Qt.ItemIsSelectable)

    def test_real_checkbox_clicks_accumulate_delete_without_expanding_or_selecting(self):
        self.pages.resize(900,500);self.pages.set_page(1);self.pages.show()
        self.item('10').setExpanded(True);self.app.processEvents()
        for identity in ('11','12'):
            item=self.item(identity);tree=self.pages.playlist_tree
            index=tree.indexFromItem(item)
            option=QStyleOptionViewItem();option.initFrom(tree)
            option.rect=tree.visualRect(index);tree.itemDelegateForIndex(index).initStyleOption(option,index)
            check=tree.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator,option,tree)
            self.assertFalse(check.isEmpty())
            QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=check.center())
            self.assertEqual(item.checkState(0),Qt.Checked)
        self.assertEqual(self.pages._checked_playlist_ids(),('11','12'))
        self.assertFalse(self.item('11').isExpanded())
        self.assertEqual(self.pages.playlist_tree.selectedItems(),[])
        self.assertEqual(self.pages.delete_playlist_button.text(),'Delete 2 checked playlists…')
        self.pages.delete_playlist_button.click()
        self.assertEqual(self.deleted,[['11','12']])

    def test_delete_refuses_stale_native_identity_busy_readonly_and_folder(self):
        first=self.item('11');first.setCheckState(0,Qt.Checked)
        self.pages.set_playlist_deletion_available(False,'Busy')
        self.pages.delete_playlist_button.click();self.assertEqual(self.deleted,[])
        self.assertFalse(self.pages.delete_playlist_button.isEnabled())
        self.pages.set_playlist_deletion_available(True)
        self.assertTrue(self.pages.delete_playlist_button.isEnabled())
        first.setData(0,Qt.UserRole,replace(self.first,title='Replaced row'))
        self.assertEqual(self.pages._checked_playlist_ids(),())
        self.pages._request_playlist_delete();self.assertEqual(self.deleted,[])
        readonly=replace(self.snapshot,facts=replace(self.snapshot.facts,writable=False))
        self.publish(readonly);self.item('11').setCheckState(0,Qt.Checked)
        self.assertFalse(self.pages.delete_playlist_button.isEnabled())
        self.pages._request_playlist_delete();self.assertEqual(self.deleted,[])

    def test_export_is_controller_gated_and_never_inherits_sync(self):
        self.pages.set_primary_action('Sync',True)
        self.assertEqual(self.pages.export_playlist_button.text(),'Export')
        self.pages.export_playlist_button.click();self.assertEqual(self.actions,['export'])
        self.pages.set_primary_action('Sync',False,'A device operation is running')
        self.pages.export_playlist_button.click();self.assertEqual(self.actions,['export'])
        self.assertIn('running',self.pages.export_playlist_button.toolTip())

    def test_song_activation_preserves_native_identity_and_has_no_source_edit_flags(self):
        first=self.item('11');first.setExpanded(True)
        child=first.child(1);activated=[]
        self.pages.song_requested.connect(activated.append)
        self.pages.playlist_tree.itemDoubleClicked.emit(child,0)
        self.assertEqual(activated,[self.songs[1]])
        self.assertFalse(child.flags() & (Qt.ItemIsEditable|Qt.ItemIsDragEnabled|Qt.ItemIsDropEnabled))

    def test_refresh_preserves_checked_expanded_song_selection_and_scroll_state(self):
        self.item('10').setExpanded(True);first=self.item('11');first.setExpanded(True)
        first.setCheckState(0,Qt.Checked)
        self.pages.playlist_tree.setCurrentItem(first.child(1))
        self.pages.set_page(1);self.pages._remember_view()
        before=dict(self.pages._states['generated'])
        self.publish(replace(self.snapshot))
        self.app.processEvents()
        self.assertTrue(self.item('10').isExpanded());self.assertTrue(self.item('11').isExpanded())
        self.assertEqual(self.item('11').checkState(0),Qt.Checked)
        self.assertEqual(self.pages._token(self.pages.playlist_tree.currentItem()),before['selected'])
        self.assertEqual(self.pages.stack.currentIndex(),1)
        self.assertEqual(self.pages._states['generated']['checked'],['playlist:11'])

    def test_checkbox_changes_do_not_walk_expanded_song_rows(self):
        self.pages._remember_view()
        with mock.patch('desktop.usb_device_view.QTreeWidgetItemIterator',
                        side_effect=AssertionError('Checkbox traversed songs')):
            self.item('11').setCheckState(0,Qt.Checked)
            self.item('12').setCheckState(0,Qt.Checked)
        self.assertEqual(self.pages._states['generated']['checked'],['playlist:11','playlist:12'])

    def test_m3u_counts_are_separate_and_have_no_native_delete_authority(self):
        playlist=UsbPlaylist('Rockbox list','Playlists/Rockbox list.m3u8',(('Other song','/Music/other.wav'),))
        self.publish(replace(self.snapshot,native=None,playlists=(playlist,)))
        item=self.pages.playlist_tree.topLevelItem(0)
        self.assertEqual([item.text(i) for i in range(3)],['Rockbox list','1','M3U8'])
        self.assertFalse(item.flags() & Qt.ItemIsUserCheckable)
        item.setExpanded(True)
        self.assertEqual(item.child(0).text(0),'1. Other song')
        self.assertFalse(self.pages.delete_playlist_button.isEnabled())
