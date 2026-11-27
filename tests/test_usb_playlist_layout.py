"""USB playlists share the stock frame while keeping USB-only action authority."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QIcon,QPainter,QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication,QHeaderView,QStyle,QStyleOptionHeader,
                              QStyleOptionViewItem,QTableWidget,QTreeWidgetItem)

from desktop.devices import DeviceFacts
from desktop.stock_device_view import DeviceSongRow,StockIpodPages
from desktop.usb_device_view import UsbDevicePages, UsbPlaylist, UsbSnapshot
from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong


def painted_header_label(header):
    """Paint through the real widget's stylesheet; QFont misses section rules."""
    option=QStyleOptionHeader();header.initStyleOptionForIndex(option,0)
    option.rect=QRect(0,0,300,26);option.text='Playlist / Track'
    option.icon=QIcon()  # Compare the label style, independent of native file-header icons.
    option.textAlignment=Qt.AlignCenter;option.sortIndicator=QStyleOptionHeader.None_
    option.state&=~(QStyle.State_MouseOver|QStyle.State_On|QStyle.State_Sunken)
    pixels=QPixmap(option.rect.size());pixels.fill(Qt.transparent)
    painter=QPainter(pixels)
    try:
        painter.setFont(header.font())
        header.style().drawControl(QStyle.CE_Header,option,painter,header)
    finally:painter.end()
    return pixels.toImage()


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
        from desktop.theme_controller import install
        install(self.app)
        reference=QTableWidget(1,3);reference.setObjectName('TrackTable')
        reference.setHorizontalHeaderLabels(['Playlist / Track','Tracks','Type'])
        reference.show();self.addCleanup(self.dispose,reference)
        for pages,index in ((self.pages,1),(self.stock,2)):
            pages.resize(900,500);pages.set_page(index);pages.show()
        self.app.processEvents()
        bold=painted_header_label(reference.horizontalHeader())
        unstyled=QTableWidget(1,3);unstyled.setHorizontalHeaderLabels(['Playlist / Track','Tracks','Type'])
        unstyled.show();self.addCleanup(self.dispose,unstyled);self.app.processEvents()
        self.assertNotEqual(painted_header_label(unstyled.horizontalHeader()),bold)
        for tree in (self.pages.playlist_tree,self.stock.playlist_tree):
            self.assertEqual(tree.columnCount(),3)
            self.assertEqual([tree.headerItem().text(i) for i in range(3)],
                             ['Playlist / Track','Tracks','Type'])
            self.assertEqual((tree.columnWidth(1),tree.columnWidth(2)),(70,150))
            self.assertEqual(tree.header().height(),26)
            self.assertEqual(tree.header().sectionResizeMode(0),QHeaderView.Stretch)
            self.assertEqual([tree.header().sectionResizeMode(i) for i in (1,2)],
                             [QHeaderView.Interactive,QHeaderView.Interactive])
            self.assertTrue(tree.alternatingRowColors());self.assertTrue(tree.uniformRowHeights())
        views=[self.pages.playlist_tree,self.stock.playlist_tree,self.stock.music_table,
               *self.pages.detail_trees.values(),*self.stock.detail_trees.values()]
        for view in views:
            with self.subTest(surface=view.objectName()):
                header=view.header() if hasattr(view,'header') else view.horizontalHeader()
                for column in range(header.count()):
                    option=QStyleOptionHeader();header.initStyleOptionForIndex(option,column)
                    self.assertEqual(option.textAlignment,Qt.AlignCenter)
                self.assertEqual(painted_header_label(header),bold)
        self.item('10').setExpanded(True);self.item('11').setExpanded(True)
        self.app.processEvents();tree=self.pages.playlist_tree
        before=tree.header().grab().toImage();tree.setCurrentItem(self.item('11').child(0))
        self.app.processEvents();self.assertTrue(tree.selectedItems())
        self.assertEqual(tree.header().grab().toImage(),before)
        for tree,item in ((tree,self.item('11').child(0)),
                          (self.stock.general_tree,QTreeWidgetItem(self.stock.general_tree,['Identity','Value']))):
            option=QStyleOptionViewItem();index=tree.indexFromItem(item,0)
            tree.itemDelegateForIndex(index).initStyleOption(option,index)
            self.assertEqual(option.displayAlignment&Qt.AlignHorizontal_Mask,Qt.AlignLeft)
        music=self.stock.music_model
        music.beginResetModel()
        music.tracks=(DeviceSongRow('Song','Artist','WAV',1234,60000,2,0,'/generated/song.wav',31,'iTunes'),)
        music.endResetModel()
        self.assertIsNone(music.data(music.index(0,0),Qt.TextAlignmentRole))
        self.assertEqual(music.data(music.index(0,3),Qt.TextAlignmentRole),int(Qt.AlignRight|Qt.AlignVCenter))
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
        self.assertTrue(folder.flags() & Qt.ItemIsUserCheckable)
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

    def click_check(self,item):
        tree=self.pages.playlist_tree
        tree.scrollToItem(item);self.app.processEvents()
        index=tree.indexFromItem(item);option=QStyleOptionViewItem();option.initFrom(tree)
        option.rect=tree.visualRect(index);tree.itemDelegateForIndex(index).initStyleOption(option,index)
        check=tree.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator,option,tree)
        self.assertFalse(check.isEmpty())
        QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=check.center())

    def nested_folders(self):
        nested=CdjPlaylist('20','10','Nested',True,())
        shared=CdjPlaylist('21','20','Shared',False,((self.songs[0].title,self.songs[0].path),),('31',))
        duplicate=CdjPlaylist('22','20','Another shared list',False,shared.entries,shared.song_ids)
        empty=CdjPlaylist('23','20','Empty folder',True,())
        native=replace(self.native,playlists=(self.folder,self.first,nested,shared,duplicate,empty,self.second))
        self.publish(replace(self.snapshot,native=native))
        self.pages.resize(900,500);self.pages.set_page(1);self.pages.show();self.app.processEvents()
        return native

    def test_folder_checkbox_cascades_nested_playlists_once_without_loading_or_navigating(self):
        self.nested_folders();loaded=[];self.pages.song_requested.connect(loaded.append)
        enriched=[];self.pages.row_enricher=lambda *args:enriched.append(args)
        with mock.patch('desktop.usb_device_view.QTreeWidgetItemIterator',
                        side_effect=AssertionError('Folder checkbox traversed song rows')), \
                mock.patch.object(self.pages,'_update_delete_playlist_button',
                                  wraps=self.pages._update_delete_playlist_button) as update:
            self.click_check(self.item('10'))
            update.assert_called_once_with()
        self.assertEqual(self.pages._checked_playlist_ids(),('10',))
        self.assertEqual(self.item('10').checkState(0),Qt.Checked)
        self.assertEqual(self.item('20').checkState(0),Qt.Checked)
        self.assertFalse(self.item('10').isExpanded());self.assertFalse(self.item('20').isExpanded())
        self.assertFalse(self.item('11').isExpanded());self.assertEqual(self.item('11').childCount(),0)
        self.assertTrue(self.item('23').flags() & Qt.ItemIsUserCheckable)
        self.assertEqual(self.pages.playlist_tree.selectedItems(),[])
        self.assertEqual(self.pages.stack.currentIndex(),1)
        self.assertEqual(loaded,[]);self.assertEqual(enriched,[]);self.assertEqual(self.actions,[])
        self.assertEqual(self.pages.delete_playlist_button.text(),'Delete checked folder…')
        self.pages.delete_playlist_button.click()
        self.assertEqual(self.deleted,[['10']])
        self.assertEqual(self.pages._states['generated']['checked'],['playlist:11','playlist:21','playlist:22','playlist:23'])
        self.assertEqual(set(self.pages._states['generated']['folder_deletions']),{'10','20','23'})
        # A nested folder overlaps its parent; toggling it never duplicates leaf IDs.
        self.item('10').setExpanded(True);self.app.processEvents()
        self.click_check(self.item('20'))
        self.assertEqual(self.pages._checked_playlist_ids(),('11',))
        self.assertEqual(self.item('10').checkState(0),Qt.PartiallyChecked)
        self.assertEqual(self.item('20').checkState(0),Qt.Unchecked)
        self.click_check(self.item('10'))
        self.assertEqual(self.pages._checked_playlist_ids(),('10',))
        self.assertEqual(self.item('10').checkState(0),Qt.Checked)

    def test_folder_partial_state_refresh_preserves_only_checked_leaves_not_new_descendants(self):
        native=self.nested_folders()
        self.click_check(self.item('10'))
        self.item('10').setExpanded(True);self.item('20').setExpanded(True);self.app.processEvents()
        self.click_check(self.item('21'))
        self.assertEqual(self.item('10').checkState(0),Qt.PartiallyChecked)
        self.assertEqual(self.item('20').checkState(0),Qt.PartiallyChecked)
        self.assertEqual(set(self.pages._checked_playlist_ids()),{'11','22','23'})
        self.click_check(self.item('21'))
        self.assertEqual(self.item('10').checkState(0),Qt.Checked)
        added=CdjPlaylist('24','20','Added later',False,(),())
        self.publish(replace(self.snapshot,native=replace(native,playlists=(*native.playlists,added))))
        self.app.processEvents()
        self.assertEqual(set(self.pages._checked_playlist_ids()),{'11','21','22','23'})
        self.assertEqual(self.item('24').checkState(0),Qt.Unchecked)
        self.assertEqual(self.item('10').checkState(0),Qt.PartiallyChecked)
        self.assertEqual(self.item('20').checkState(0),Qt.PartiallyChecked)
        self.assertTrue(self.item('10').isExpanded());self.assertTrue(self.item('20').isExpanded())
        self.assertTrue(self.item('23').flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(all(token in {'playlist:11','playlist:21','playlist:22','playlist:23'}
                            for token in self.pages._states['generated']['checked']))

    def test_empty_folder_selection_never_authorizes_its_derived_checked_ancestor(self):
        native=replace(self.native,playlists=(CdjPlaylist('4','0','SETS',True,()),
            CdjPlaylist('5','4','HAMDI',True,())))
        self.publish(replace(self.snapshot,native=native))
        self.pages.resize(900,500);self.pages.set_page(1);self.pages.show();self.app.processEvents()
        self.item('4').setExpanded(True);self.app.processEvents()
        self.click_check(self.item('5'))
        self.assertEqual(self.item('4').checkState(0),Qt.Checked)
        self.assertEqual(self.pages._checked_playlist_ids(),('5',))
        self.pages.delete_playlist_button.click();self.assertEqual(self.deleted,[['5']])
        self.publish(replace(self.snapshot,native=replace(native,playlists=(native.playlists[0],))))
        self.app.processEvents()
        self.assertEqual(self.pages._checked_playlist_ids(),())
        self.assertTrue(self.item('4').flags() & Qt.ItemIsUserCheckable)
        self.click_check(self.item('4'))
        self.assertEqual(self.pages._checked_playlist_ids(),('4',))
        self.pages.delete_playlist_button.click();self.assertEqual(self.deleted,[['5'],['4']])

    def test_restored_folder_intent_requires_known_subtree_and_every_terminal_checked(self):
        native=self.nested_folders();self.click_check(self.item('10'))
        state=self.pages._states['generated']
        state['checked'].remove('playlist:21')
        state['folder_deletions']['unknown']=None
        self.pages._snapshot=replace(self.snapshot,notice='retain saved state')
        self.publish(replace(self.snapshot,native=replace(native)))
        self.app.processEvents()
        self.assertEqual(set(self.pages._checked_playlist_ids()),{'11','22','23'})
        self.assertEqual(self.item('10').checkState(0),Qt.PartiallyChecked)
        self.assertEqual(self.item('21').checkState(0),Qt.Unchecked)
        self.assertEqual(self.pages._explicit_folders,{'23'})

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
