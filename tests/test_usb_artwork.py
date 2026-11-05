"""Selected USB artwork stays native, bounded and off the GUI thread."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QThread, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFrame, QTreeWidget, QTreeWidgetItem

from desktop.devices import DeviceFacts
from desktop.track_info import TrackInfoMixin
from desktop.usb_device_view import UsbSnapshot
from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong, read_artwork


class UsbArtworkCapture(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        self.root=Path(area.name).resolve();self.token=self.root.stat().st_dev
        self.path=self.root/'PIONEER/Artwork/00000/a1.jpg'
        self.path.parent.mkdir(parents=True);self.path.write_bytes(b'native cover')
        self.song=CdjSong('1','/Contents/track.wav','USB track',artwork='/PIONEER/Artwork/00000/a1.jpg')

    def test_capture_reads_only_the_selected_cover_and_does_not_modify_it(self):
        original=os.open;opened=[]
        def guarded(path,*args,**kwargs):
            opened.append(str(path))
            return original(path,*args,**kwargs)
        with mock.patch('soundbox.cdj_usb.os.open',side_effect=guarded):
            self.assertEqual(read_artwork(self.root,self.token,self.song),b'native cover')
        self.assertEqual(opened,[str(self.root),'PIONEER','Artwork','00000','a1.jpg'])
        self.assertEqual(self.path.read_bytes(),b'native cover')
        self.assertEqual(read_artwork(self.root,self.token,
            replace(self.song,artwork='PIONEER/Artwork/00000/a1.jpg')),b'native cover')
        with mock.patch('soundbox.cdj_usb.CdjReadSession',side_effect=AssertionError('No cover needs no USB read')):
            self.assertEqual(read_artwork(self.root,self.token,replace(self.song,artwork='')),b'')

    def test_rejects_non_artwork_and_ambiguous_references_before_opening_the_mount(self):
        for path in ('/Contents/track.wav','/PIONEER/Artwork/../secret',
                     '/PIONEER/Artwork/./a.jpg','//PIONEER/Artwork/a.jpg',
                     '/PIONEER/Artwork/a:1.jpg','/PIONEER/Artwork/a\\1.jpg',
                     '/PIONEER/Artwork/a\0.jpg','/PIONEER/Artwork//a.jpg'):
            with self.subTest(path=path),mock.patch('soundbox.cdj_usb.CdjReadSession') as session:
                with self.assertRaises(ValueError):read_artwork(self.root,self.token,replace(self.song,artwork=path))
                session.assert_not_called()

    def test_linked_parent_or_cover_and_changed_mount_are_unavailable(self):
        with self.assertRaises(ValueError):read_artwork(self.root,self.token+1,self.song)
        self.path.unlink();self.path.symlink_to(self.root/'elsewhere.jpg')
        with self.assertRaises(OSError):read_artwork(self.root,self.token,self.song)
        self.path.unlink();self.path.parent.rmdir()
        self.path.parent.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(OSError):read_artwork(self.root,self.token,self.song)

    def test_oversized_cover_fails_before_streaming_the_payload(self):
        with self.path.open('wb') as stream:stream.truncate(8*1024*1024+1)
        with self.assertRaises(ValueError):read_artwork(self.root,self.token,self.song)


class Host(TrackInfoMixin,QFrame):
    pass


class UsbInfoArtwork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        self.root=Path(area.name).resolve();self.path=self.root/'PIONEER/Artwork/00000/a1.jpg'
        self.path.parent.mkdir(parents=True)
        image=QImage(1200,600,QImage.Format_RGB32);image.fill(QColor('red'))
        self.assertTrue(image.save(str(self.path)))
        self.song=CdjSong('1','/Contents/track.wav','USB track',artwork='/PIONEER/Artwork/00000/a1.jpg')
        self.snapshot=UsbSnapshot(self.root,self.root.stat().st_dev,DeviceFacts())
        self.host=Host();self.host._exporting=False
        self.host.stack=SimpleNamespace(currentIndex=lambda:1)
        self.host.device_content_stack=SimpleNamespace(currentIndex=lambda:2)
        tree=QTreeWidget(self.host);item=QTreeWidgetItem(['USB track']);tree.addTopLevelItem(item)
        item.setData(0,Qt.UserRole,self.song);tree.setCurrentItem(item)
        self.host.usb_device_pages=SimpleNamespace(playlist_tree=tree,_snapshot=self.snapshot,
            _display_rows={self.song.id:dict(id='1',title='USB track')})
        self.host.art_cache=SimpleNamespace(image_path=mock.Mock(side_effect=AssertionError('Desktop art')))
        self.host._device_operation_owned=mock.Mock(return_value=False)
        self.host._defer_device_operation_until_scan_finishes=mock.Mock(return_value=False)
        self.host._flash_status=mock.Mock()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_information_button_prepares_native_cover_before_show_and_reuses_it_in_info(self):
        reads=[];shown=[]
        def capture(target,token,song):
            reads.append((QThread.currentThread(),self.host._exporting))
            return read_artwork(target,token,song)
        def show(dialog):
            shown.append(dialog.row)
            self.assertFalse(self.host._exporting)
            self.assertEqual(dialog._owner.pending,0)
            self.assertFalse(dialog._covers[0].pixmap().isNull())
            self.assertEqual(dialog._image.pixelColor(0,0).name(),'#fe0000')
            dialog.tabs.setCurrentIndex(1)
            self.assertTrue(all(not cover.pixmap().isNull() for cover in dialog._covers))
            self.assertFalse(dialog.controls)
            dialog.reject();dialog.deleteLater()
        with mock.patch('soundbox.cdj_usb.read_artwork',side_effect=capture), \
                mock.patch('desktop.track_info._exec_info',side_effect=show):
            self.host._open_track_info()
        self.assertEqual(len(reads),1);self.assertIsNot(reads[0][0],self.app.thread())
        self.assertTrue(reads[0][1]);self.assertEqual(shown[0]['location'],str(self.root)+self.song.path)
        self.host.art_cache.image_path.assert_not_called()

    def test_deferred_information_keeps_captured_song_and_snapshot(self):
        callbacks=[]
        def defer(callback):callbacks.append(callback);return True
        self.host._defer_device_operation_until_scan_finishes.side_effect=defer
        with mock.patch('desktop.track_info._exec_info') as show:
            self.host._open_track_info();show.assert_not_called()
            self.host.usb_device_pages._snapshot=replace(self.snapshot,mount_token=self.snapshot.mount_token+1)
            self.host._defer_device_operation_until_scan_finishes.side_effect=None
            self.host._defer_device_operation_until_scan_finishes.return_value=False
            callbacks[0]()
            dialog=show.call_args.args[0]
            self.assertFalse(dialog._image.isNull());dialog.reject();dialog.deleteLater()

    def test_busy_device_never_starts_cover_read_and_missing_cover_settles(self):
        self.host._device_operation_owned.return_value=True
        with mock.patch('soundbox.cdj_usb.read_artwork') as read:
            self.host._open_track_info();read.assert_not_called()
        self.host._flash_status.assert_called_with('Wait for the current device operation')
        self.host._device_operation_owned.return_value=False
        self.path.unlink()
        with mock.patch('desktop.track_info._exec_info') as show:
            self.host._open_track_info()
            dialog=show.call_args.args[0]
            self.assertTrue(dialog._art_ready);self.assertTrue(dialog._image.isNull())
            self.assertEqual(dialog._owner.pending,0);self.assertFalse(self.host._exporting)
            dialog.reject();dialog.deleteLater()

    def test_native_artwork_badge_never_uses_the_desktop_cache(self):
        from desktop.cdj import CdjDeviceMixin
        host=SimpleNamespace(lib=SimpleNamespace(source_library_id='42',tracks_by_id={'17':object()}),
            art_cache=SimpleNamespace(status_kind=mock.Mock(side_effect=AssertionError('Desktop art status'))))
        row={}
        CdjDeviceMixin._enrich_usb_row(host,replace(self.song,source_id='17',source_db_id='42'),row)
        self.assertEqual(row['art'],'cover')
        CdjDeviceMixin._enrich_usb_row(host,replace(self.song,artwork=''),row)
        self.assertEqual(row['art'],'')

    def test_usb_container_or_empty_selection_never_falls_back_to_desktop_info(self):
        tree=self.host.usb_device_pages.playlist_tree
        folder=QTreeWidgetItem(['USB folder']);tree.addTopLevelItem(folder)
        folder.setData(0,Qt.UserRole,CdjPlaylist('7','0','USB folder',True,()))
        self.host._selected_track=mock.Mock(side_effect=AssertionError('Stale desktop selection'))
        with mock.patch('desktop.track_info._exec_info') as show:
            tree.setCurrentItem(folder);self.host._open_track_info()
            tree.setCurrentItem(None);self.host._open_track_info()
            show.assert_not_called()
        self.host._selected_track.assert_not_called()
        self.assertEqual(self.host._flash_status.call_count,2)
        self.host._flash_status.assert_called_with('Select a USB track to view its information')

    def test_desktop_artwork_publication_cannot_reload_matching_usb_track_id(self):
        with mock.patch('desktop.track_info._exec_info') as show:
            self.host._open_track_info()
        dialog=show.call_args.args[0]
        self.assertEqual(dialog.row['id'],'1')
        self.assertFalse(dialog._desktop_artwork)
        with mock.patch('soundbox.cdj_usb.read_artwork',side_effect=AssertionError('Unowned USB read')), \
                mock.patch.object(dialog,'_load_artwork') as reload:
            self.host._refresh_info_artwork(['1']);reload.assert_not_called()
        self.assertEqual(dialog._owner.pending,0);dialog.reject();dialog.deleteLater()

    def test_same_path_native_records_keep_distinct_information_and_covers(self):
        from desktop.usb_device_view import UsbDevicePages
        first=replace(self.song,id='1',title='First native record')
        second=replace(first,id='2',title='Second native record',artwork='/PIONEER/Artwork/00000/a2.jpg')
        playlist=CdjPlaylist('7','0','Duplicate file records',False,
            ((first.title,first.path),(second.title,second.path),(first.title,first.path)),('1','2','1'))
        native=CdjLibrary((playlist,),'Fixture',None,None,(),0,(),songs=(first,second))
        pages=UsbDevicePages();self.addCleanup(pages.deleteLater)
        pages.set_snapshot(replace(self.snapshot,native=native),name='Fixture',
            profile=SimpleNamespace(kind='generic_usb',cdj_export=True),free=0,drift=0)
        item=pages.playlist_tree.topLevelItem(0);pages._expand_playlist(item)
        self.assertEqual([item.child(i).data(0,Qt.UserRole) for i in range(3)],[first,second,first])
        self.assertEqual(pages._display_rows['1']['title'],first.title)
        self.assertEqual(pages._display_rows['2']['title'],second.title)
        self.host.usb_device_pages=pages;pages.playlist_tree.setCurrentItem(item.child(0))
        with mock.patch('desktop.track_info._exec_info') as show:
            self.host._open_track_info()
            dialog=show.call_args.args[0];self.assertEqual(dialog.row['title'],first.title)
            self.assertFalse(dialog._image.isNull());dialog.reject();dialog.deleteLater()
