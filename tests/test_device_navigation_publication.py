"""Source acknowledgements update desktop data without navigating device pages."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtGui import QContextMenuEvent,QDragEnterEvent,QDropEvent
from PySide6.QtTest import QSignalSpy,QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidgetItem

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.trees import (ARTISTS_ROLE, COLLECTION_ROLE, CUSTOM_ROLE, DEVPATH_ROLE,DEVROLE_ROLE,
                           NATIVE_TREE_ROLE,PATH_ROLE,RB_NODE_ROLE)
from desktop.usb_device_view import UsbSnapshot
from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong
from soundbox import engine, playlist_unification
from soundbox.library import Library, Playlist, Track
from soundbox.providers import Providers
from soundbox.device_profile import DeviceProfile
from soundbox.stock_ipod_info import (StockIpodPlaylistEntry, StockIpodPlaylistSnapshot,
                                     StockIpodSnapshot, StockIpodTrackSnapshot)


class DeviceNavigationPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live',
                                     side_effect=AssertionError('live source')))
        self.enterContext(mock.patch('desktop.player.NativeDeckBackend',
                                     side_effect=RuntimeError('silent fixture deck')))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window = MainWindow(providers=Providers('public'), isolated_dir=root)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.shutdown)
        w = self.window
        for controller, method in ((w.graveyard_controller, 'prepare_library'),
                                   (w.native_analysis_controller, 'discover'),
                                   (w.media_controller, 'discover')):
            self.enterContext(mock.patch.object(controller, method))
        for method in ('_cache_library', '_arm_waveform_rechecks', '_schedule_stock_artwork',
                       '_resume_analysis_jobs', '_maybe_fill_waveforms', '_read_stock_device_view'):
            self.enterContext(mock.patch.object(w, method))
        w.lib = Library({str(i): Track(str(i), 'Song '+str(i), 'Artist',
            f'file:///synthetic/{i}.wav', added_ts=i, source_uuid=f'uuid-{i}') for i in (1, 2, 3)},
            [], '/synthetic/master.db', source_kind='rekordbox-db', source_library_id='library',
            source_playlist_catalog=dict(nodes=[], members=[], revision='a'*64))
        w._rekordbox_source_verified = True
        w._apply_loaded_library()
        w._show_collection()
        w._view_key = 'sb:Collection'

    def select_device(self, kind, path='/synthetic/DEVICE'):
        w = self.window
        stock = kind in ('stock_ipod', 'dual_boot')
        profile = SimpleNamespace(kind=kind, has_stock_ipod=stock, model='', label=kind,
                                  cdj_export=kind == 'generic_usb', rockbox_export=not stock)
        w._device_profiles[path] = profile
        w._device_mount_tokens[path] = 7
        item = QTreeWidgetItem([kind])
        item.setData(0, DEVPATH_ROLE, path)
        w.device_tree.addTopLevelItem(item)
        w.device_tree.setCurrentItem(item)
        if stock:
            track = StockIpodTrackSnapshot(1, 1, 'Device song', 'Device artist', '', '', 'WAV',
                100, 1000, 1411, 44100, 0, 0, 0, 0, 0, ':iPod_Control:Music:F00:song.wav', 0, 0)
            snapshot = StockIpodSnapshot(Path(path), kind, 'device-uuid', 'FAT32', 'USB',
                'fixture-disk', True, 1000, 500, '', '', '', '', 'fixture-key', 1, 2, 0x75,
                0, 'hash', 0, 0, '', 0, (track,), (StockIpodPlaylistSnapshot(99, 'Device set',
                    'Playlist', False, (StockIpodPlaylistEntry(1, 'Device song', 'Device artist'),)),))
            w._stock_view_cache[path] = (snapshot, None)
        else:
            song = CdjSong('7', '/Contents/song.wav', 'Device song', 'Device artist', 14000,
                           '8A', 137, 320)
            native = CdjLibrary((CdjPlaylist('99', '0', 'Device set', False,
                ((song.title, song.path),)),), kind, 8, 8, (), 1, (), songs=(song,))
            w._usb_view_cache[path] = UsbSnapshot(Path(path), 7,
                DeviceFacts(volume_uuid='device-uuid'), native=native)
            w._usb_device_rows[path] = (kind, 500, None, profile)
        w._on_device_click(item, 0)
        pages = w.stock_ipod_pages if stock else w.usb_device_pages
        w._set_device_tab(2 if stock else 1)
        tree = pages.playlist_tree
        parent = tree.topLevelItem(0)
        parent.setExpanded(True)
        self.assertIsNotNone(parent.child(0))
        tree.setCurrentItem(parent.child(0))
        tree.verticalScrollBar().setRange(0, 1000)
        tree.verticalScrollBar().setValue(240)
        w.device_tree.verticalScrollBar().setRange(0, 1000)
        w.device_tree.verticalScrollBar().setValue(72)
        return item, pages, tree

    def seat(self, pages, tree):
        w = self.window
        return (w.stack.currentIndex(), w.body_stack.currentIndex(),
            w.device_content_stack.currentIndex(), pages.stack.currentIndex(),
            w.device_tree.currentItem(), tuple(w.device_tree.selectedItems()),
            w.device_tree.verticalScrollBar().value(), w._current_device_path,
            tree.currentItem(), tuple(tree.selectedItems()), tree.topLevelItem(0).isExpanded(),
            tree.verticalScrollBar().value())

    def sidebar_playlist(self):
        self.window.custom['Generated drag set'] = ['1', '2']
        item = QTreeWidgetItem(['Generated drag set'])
        item.setData(0, CUSTOM_ROLE, 'Generated drag set')
        self.window.tree.addTopLevelItem(item)
        return item

    def test_sidebar_playlist_mouse_drag_keeps_device_navigation_and_selection(self):
        self._assert_sidebar_drag_keeps_device('generic_usb',native=False)

    def test_browser_models_retire_with_their_views_despite_retained_python_references(self):
        from PySide6.QtCore import QCoreApplication,QEvent,QThread
        from shiboken6 import isValid

        w=self.window
        models=(w.model,w.fs_model);views=(w.table,w.fs_view)
        self.assertEqual(w.model.rowCount(),3)
        self.assertTrue(w.fs_model.isReadOnly())
        destroyed=[]
        for name,model in zip(('tracks','files'),models):
            self.assertTrue(isValid(model))
            self.assertIs(model.thread(),self.app.thread())
            model.destroyed.connect(lambda _obj=None,name=name:
                destroyed.append((name,QThread.isMainThread())),Qt.DirectConnection)

        # Exercise the fixture's ordinary joined shutdown and queued deletion,
        # retaining the same Python wrappers as unittest does during cleanup.
        self.assertTrue(self.doCleanups())
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertFalse(isValid(w))
        self.assertTrue(all(not isValid(view) for view in views))
        self.assertTrue(all(not isValid(model) for model in models))
        self.assertEqual(sorted(destroyed),[('files',True),('tracks',True)])

    def test_native_sidebar_playlist_drag_keeps_stock_device_navigation(self):
        self._assert_sidebar_drag_keeps_device('stock_ipod',native=True)

    def _assert_sidebar_drag_keeps_device(self,kind,*,native):
        w = self.window
        source = self.sidebar_playlist()
        if native:
            source.setData(0,CUSTOM_ROLE,None)
            source.setData(0,NATIVE_TREE_ROLE,True)
            source.setData(0,PATH_ROLE,'Generated native set')
            source.setData(0,RB_NODE_ROLE,'99')
        target,pages,tree = self.select_device(kind)
        w.resize(1100,900);w.show();self.app.processEvents()
        before = self.seat(pages,tree)
        source_current = w.tree.currentItem()
        source_selected = w.tree.selectedItems()
        view_key = w._view_key
        flips = QSignalSpy(w.stack.currentChanged)
        drops = []
        # Exercise Qt mouse gesture, real MIME production and DeviceTree drop
        # parsing; replace only the native drag transport and export consumer.
        w.device_tree.dropped.disconnect()
        w.device_tree.dropped.connect(lambda dev,items:drops.append((dev,items)))
        point = w.tree.visualItemRect(source).center()
        def transport(view,md,_label,_actions):
            self.assertIs(view,w.tree)
            destination = w.device_tree.visualItemRect(target).center()
            enter = QDragEnterEvent(destination,Qt.CopyAction,md,Qt.LeftButton,Qt.NoModifier)
            self.app.sendEvent(w.device_tree.viewport(),enter)
            drop = QDropEvent(QPointF(destination),Qt.CopyAction,md,Qt.LeftButton,Qt.NoModifier)
            self.app.sendEvent(w.device_tree.viewport(),drop)
            self.assertTrue(drop.isAccepted())
        with mock.patch('desktop.trees._start_chip_drag',side_effect=transport) as drag:
            QTest.mousePress(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
            self.assertEqual(self.seat(pages,tree),before)
            QTest.mouseMove(w.tree.viewport(),point+QPoint(40,0),20)
            QTest.mouseRelease(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point+QPoint(40,0))
        self.app.processEvents()
        drag.assert_called_once()
        expected = ('rb','Generated native set') if native else ('cust','Generated drag set')
        self.assertEqual(drops,[(w._current_device_path,[expected])])
        self.assertEqual(self.seat(pages,tree),before)
        self.assertEqual(w._view_key,view_key)
        self.assertIs(w.tree.currentItem(),source_current)
        self.assertEqual(w.tree.selectedItems(),source_selected)
        self.assertEqual(flips.count(),0)

    def test_sidebar_playlist_click_and_keyboard_still_navigate(self):
        w = self.window
        source = self.sidebar_playlist()
        self.select_device('generic_usb')
        w.resize(1100,900);w.show();self.app.processEvents()
        point = w.tree.visualItemRect(source).center()
        QTest.mouseClick(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(w.stack.currentIndex(),0)
        self.assertEqual(w._view_key,'sb:Generated drag set')
        self.assertIs(w.tree.currentItem(),source)
        self.assertEqual(w.tree.selectedItems(),[source])
        self.select_device('generic_usb')
        QTest.keyClick(w.tree,Qt.Key_Home)
        self.assertEqual(w.stack.currentIndex(),0)
        self.assertEqual(w._view_key,'sb:Collection')
        self.assertTrue(w.tree.currentItem().data(0,COLLECTION_ROLE))

    def test_canceled_drag_and_removed_pressed_row_cannot_navigate(self):
        w = self.window
        source = self.sidebar_playlist()
        _target,pages,tree = self.select_device('generic_usb')
        w.resize(1100,900);w.show();self.app.processEvents()
        before = self.seat(pages,tree)
        point = w.tree.visualItemRect(source).center()
        with mock.patch('desktop.trees._start_chip_drag') as canceled:
            QTest.mousePress(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
            QTest.mouseMove(w.tree.viewport(),point+QPoint(40,0),20)
            QTest.mouseRelease(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        canceled.assert_called_once()
        self.assertEqual(self.seat(pages,tree),before)
        QTest.mousePress(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        w.tree.takeTopLevelItem(w.tree.indexOfTopLevelItem(source))
        QTest.mouseRelease(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(self.seat(pages,tree),before)

    def test_modifier_click_and_selected_playlist_drag_keep_the_selected_set(self):
        w = self.window
        first = self.sidebar_playlist()
        w.custom['Second generated set'] = ['3']
        second = QTreeWidgetItem(['Second generated set'])
        second.setData(0,CUSTOM_ROLE,'Second generated set')
        w.tree.addTopLevelItem(second)
        w.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        w.resize(1100,900);w.show();self.app.processEvents()
        QTest.mouseClick(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,
                         w.tree.visualItemRect(first).center())
        QTest.mouseClick(w.tree.viewport(),Qt.LeftButton,Qt.ControlModifier,
                         w.tree.visualItemRect(second).center())
        self.assertEqual(set(w.tree.selectedItems()),{first,second})
        current = w.tree.currentItem()
        captured = []
        point = w.tree.visualItemRect(first).center()
        with mock.patch('desktop.trees._start_chip_drag',
                        side_effect=lambda _view,md,_label,_actions:captured.append(md.text())):
            QTest.mousePress(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
            QTest.mouseMove(w.tree.viewport(),point+QPoint(40,0),20)
            QTest.mouseRelease(w.tree.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(len(captured),1)
        self.assertEqual(set(captured[0].splitlines()),
                         {'cust\tGenerated drag set','cust\tSecond generated set'})
        self.assertEqual(set(w.tree.selectedItems()),{first,second})
        self.assertIs(w.tree.currentItem(),current)

    def fresh(self, *, structural=False):
        w = self.window
        return replace(w.lib, tracks_by_id={**w.lib.tracks_by_id,
            '2': replace(w.lib.tracks_by_id['2'], name='Fresh saved title',
                bpm=('142' if w.lib.tracks_by_id['2'].bpm == '141' else '141')
                    if structural else w.lib.tracks_by_id['2'].bpm)})

    def context_devices(self):
        w=self.window;active,_,_=self.select_device('generic_usb','/synthetic/ACTIVE')
        rows=[]
        for kind in ('stock_ipod','dual_boot','rockbox','generic_usb'):
            path='/synthetic/'+kind.upper();item,_,_=self.select_device(kind,path)
            stock=kind in ('stock_ipod','dual_boot');rockbox=kind in ('rockbox','dual_boot')
            profile=DeviceProfile(kind,kind,'msdos',rockbox,stock,rockbox,stock,False)
            w._device_profiles[path]=profile;rows.append((kind,item))
            if stock:
                companion=QTreeWidgetItem([kind+' USB']);companion.setData(0,DEVPATH_ROLE,path)
                companion.setData(0,DEVROLE_ROLE,'ipod_usb');w.device_tree.addTopLevelItem(companion)
                w._usb_view_cache[path]=replace(w._usb_view_cache['/synthetic/ACTIVE'],target=Path(path))
                w._usb_device_rows[path]=(kind,500,None,profile);rows.append((kind+' USB',companion))
        w.resize(1240,1100);w.show();w._sidebar_split.setSizes([180,500]);self.app.processEvents()
        self.addCleanup(w.hide)
        return active,rows

    def context_collection(self):
        w=self.window
        item=next(w.sb_tree.topLevelItem(i) for i in range(w.sb_tree.topLevelItemCount())
                  if w.sb_tree.topLevelItem(i).data(0,COLLECTION_ROLE))
        w.sb_tree.setCurrentItem(item);w._on_custom_select(item,None);self.app.processEvents()
        return item

    def context_seat(self):
        w=self.window
        return (w.stack.currentIndex(),w.body_stack.currentIndex(),w.device_content_stack.currentIndex(),
            w._current_device_path,w._current_device_role,w.device_tree.currentItem(),
            tuple(w.device_tree.selectedItems()),w.sb_tree.currentItem(),tuple(w.sb_tree.selectedItems()),
            w.usb_device_pages.stack.currentIndex(),w.stock_ipod_pages.stack.currentIndex(),w._view_key)

    def test_real_right_context_preserves_browser_location_for_every_device_role(self):
        w=self.window;active,rows=self.context_devices();tree=w.device_tree
        for kind,item in rows:
            other=next((sibling for _kind,sibling in rows
                        if sibling.data(0,DEVPATH_ROLE)==item.data(0,DEVPATH_ROLE)
                        and (sibling.data(0,DEVROLE_ROLE) or '')!=(item.data(0,DEVROLE_ROLE) or '')),None)
            origins=[None,active,item]+([other] if other is not None else [])
            for origin in origins:
                with self.subTest(kind=kind,origin=None if origin is None else origin.text(0)):
                    if origin is None:self.context_collection()
                    else:w._on_device_click(origin,0)
                    tree.scrollToItem(item);self.app.processEvents();before=self.context_seat()
                    point=tree.visualItemRect(item).center();menus=[]
                    def menu(popup,*_):
                        self.assertEqual(self.context_seat(),before)
                        menus.append([action.text() for action in popup.actions()])
                        next(action for action in popup.actions() if action.text().startswith('Export')).trigger()
                        self.assertEqual(self.context_seat(),before);popup.deleteLater()
                    with mock.patch('desktop.app.exec_transient_menu',side_effect=menu), \
                            mock.patch.object(w,'_show_export_dialog') as export:
                        QTest.mousePress(tree.viewport(),Qt.RightButton,Qt.NoModifier,point)
                        self.app.processEvents();self.assertEqual(self.context_seat(),before)
                        QTest.mouseRelease(tree.viewport(),Qt.RightButton,Qt.NoModifier,point)
                        self.app.processEvents();self.assertEqual(self.context_seat(),before)
                        QTest.mouseDClick(tree.viewport(),Qt.RightButton,Qt.NoModifier,point)
                        self.app.processEvents();self.assertEqual(self.context_seat(),before)
                        event=QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point))
                        QApplication.sendEvent(tree.viewport(),event);self.app.processEvents()
                    self.assertEqual(len(menus),1);self.assertEqual(self.context_seat(),before)
                    path=item.data(0,DEVPATH_ROLE);role=item.data(0,DEVROLE_ROLE) or ''
                    export.assert_called_once_with((path,role) if role else path)
        w._on_device_click(active,0);self.app.processEvents()
        point=QPoint(5,tree.viewport().height()-4);self.assertIsNone(tree.itemAt(point));before=self.context_seat()
        with mock.patch('desktop.app.exec_transient_menu') as menu:
            QTest.mouseClick(tree.viewport(),Qt.RightButton,Qt.NoModifier,point)
            QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point)))
            self.app.processEvents()
        menu.assert_not_called();self.assertEqual(self.context_seat(),before)

    def test_context_browse_publishes_exact_attached_role_after_rebuild_and_ignores_disconnection(self):
        w=self.window;active,rows=self.context_devices();tree=w.device_tree
        target=next(item for kind,item in rows if kind=='stock_ipod')
        sibling=next(item for kind,item in rows if kind=='stock_ipod USB')
        for origin in (None,active,sibling):
            if origin is None:self.context_collection()
            else:w._on_device_click(origin,0)
            tree.scrollToItem(target);self.app.processEvents();before=self.context_seat();replacement=[]
            point=tree.visualItemRect(target).center()
            def menu(popup,*_):
                self.assertEqual(self.context_seat(),before)
                path=target.data(0,DEVPATH_ROLE);role=target.data(0,DEVROLE_ROLE) or ''
                detached=tree.takeTopLevelItem(tree.indexOfTopLevelItem(target))
                item=QTreeWidgetItem(['Rebuilt stock row']);item.setData(0,DEVPATH_ROLE,path)
                item.setData(0,DEVROLE_ROLE,role);tree.addTopLevelItem(item);replacement.append(item)
                del detached
                next(action for action in popup.actions() if action.text()=='Browse').trigger();popup.deleteLater()
            with mock.patch('desktop.app.exec_transient_menu',side_effect=menu):
                QTest.mouseClick(tree.viewport(),Qt.RightButton,Qt.NoModifier,point)
                QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point)))
                self.app.processEvents()
            target=replacement[0];self.assertIs(tree.currentItem(),target)
            self.assertEqual(tree.selectedItems(),[target]);self.assertEqual(w.stack.currentIndex(),1)
            self.assertEqual((w._current_device_path,w._current_device_role),(target.data(0,DEVPATH_ROLE),''))
            self.assertEqual(w.device_content_stack.currentIndex(),1)
        w._on_device_click(active,0);tree.scrollToItem(target);self.app.processEvents()
        before=self.context_seat();point=tree.visualItemRect(target).center()
        def disconnected(popup,*_):
            tree.takeTopLevelItem(tree.indexOfTopLevelItem(target))
            next(action for action in popup.actions() if action.text()=='Browse').trigger();popup.deleteLater()
        with mock.patch('desktop.app.exec_transient_menu',side_effect=disconnected):
            QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point)))
            self.app.processEvents()
        self.assertEqual(self.context_seat(),before);self.assertIsNotNone(sibling.treeWidget())

    def test_user_keyboard_navigation_matches_page_while_tab_and_context_key_preserve_location(self):
        w=self.window;active,rows=self.context_devices();tree=w.device_tree
        collection=self.context_collection();w.sb_tree.setFocus();self.app.processEvents();before=self.context_seat()
        QTest.keyClick(w.sb_tree,Qt.Key_Tab);self.app.processEvents()
        self.assertTrue(tree.hasFocus());self.assertTrue(collection.isSelected())
        self.assertEqual(self.context_seat(),before)
        w._on_device_click(active,0);tree.setFocus();self.app.processEvents();requests=[]
        tree.navigation_requested.connect(requests.append)
        for key in (Qt.Key_Down,Qt.Key_Up,Qt.Key_End,Qt.Key_Home,Qt.Key_PageDown,Qt.Key_PageUp):
            with self.subTest(key=key):
                previous=tuple(tree.selectedItems());count=len(requests)
                QTest.keyClick(tree,key);self.app.processEvents();item=tree.currentItem()
                self.assertEqual(tree.selectedItems(),[item])
                self.assertEqual((w._current_device_path,w._current_device_role),
                                 (item.data(0,DEVPATH_ROLE),item.data(0,DEVROLE_ROLE) or ''))
                self.assertEqual(w.stack.currentIndex(),1)
                self.assertEqual(len(requests),count+(tuple(tree.selectedItems())!=previous))
        before=self.context_seat();count=len(requests);point=tree.visualItemRect(tree.currentItem()).center()
        with mock.patch('desktop.app.exec_transient_menu',side_effect=lambda menu,*_:menu.deleteLater()):
            QTest.keyClick(tree,Qt.Key_Menu)
            QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Keyboard,point,tree.viewport().mapToGlobal(point)))
            self.app.processEvents()
        self.assertEqual(self.context_seat(),before);self.assertEqual(len(requests),count)
        # Discovery can change current programmatically without opening a page.
        for key,item in ((Qt.Key_Return,rows[0][1]),(Qt.Key_Enter,rows[1][1])):
            self.context_collection();count=len(requests);tree.setCurrentItem(item);self.app.processEvents()
            self.assertEqual(w.stack.currentIndex(),0);self.assertEqual(len(requests),count)
            tree.clearSelection();self.assertIs(tree.currentItem(),item);self.assertEqual(tree.selectedItems(),[])
            QTest.keyClick(tree,key);self.app.processEvents()
            self.assertEqual(w.stack.currentIndex(),1);self.assertIs(tree.currentItem(),item)
            self.assertEqual(tree.selectedItems(),[item]);self.assertEqual(len(requests),count+1)
            self.assertEqual((w._current_device_path,w._current_device_role),
                             (item.data(0,DEVPATH_ROLE),item.data(0,DEVROLE_ROLE) or ''))

    def test_finished_scan_retires_remounted_ipod_even_when_drag_skipped_preview(self):
        w=self.window;dev='/synthetic/DEVICE'
        self.select_device('stock_ipod',dev)
        profile=w._device_profiles[dev]
        profile.has_rockbox=False;profile.stock_preview=True;profile.stock_write=False
        w._stock_catalog_witness_cache[dev]=object()
        w._stock_view_failures[dev]='previous device error'
        w._dual_sync_queue[dev]=['previous device job']
        w._dev_scan_worker=SimpleNamespace(mount_tokens={dev:8})
        with mock.patch('desktop.app.QApplication.mouseButtons',return_value=Qt.LeftButton):
            w._on_devices_discovered([('New iPod',dev,100,profile)])
            self.assertIn(dev,w._stock_view_cache)
            w._on_devices_scanned([('New iPod',dev,100,0,profile)],mount_tokens={dev:8})
        w._dev_scan_worker=None
        self.assertNotIn(dev,w._stock_view_cache)
        self.assertNotIn(dev,w._stock_catalog_witness_cache)
        self.assertNotIn(dev,w._stock_view_failures)
        self.assertNotIn(dev,w._dual_sync_queue)
        self.assertIs(w._stock_view_pending[dev],profile)
        self.assertEqual(w._device_mount_tokens[dev],8)
        self.assertEqual(w._current_device_path,'')
        self.assertEqual(w.stack.currentIndex(),0)

    def test_metadata_ack_and_full_refresh_preserve_every_device_page_without_a_stack_flip(self):
        w = self.window
        for kind in ('stock_ipod', 'dual_boot', 'rockbox', 'generic_usb'):
            for structural in (False, True):
                with self.subTest(kind=kind, structural=structural):
                    _item, pages, tree = self.select_device(kind)
                    before = self.seat(pages, tree)
                    changes = []
                    w.stack.currentChanged.connect(changes.append)
                    try:
                        w._publish_metadata_library(self.fresh(structural=structural), ('2',))
                    finally:
                        w.stack.currentChanged.disconnect(changes.append)
                    self.assertEqual(self.seat(pages, tree), before)
                    self.assertEqual(changes, [])
                    self.assertEqual(w.lib.tracks_by_id['2'].name, 'Fresh saved title')
                    self.assertIs(w.device_content_stack.currentWidget(), pages)

    def test_late_source_result_keeps_the_newer_selected_device_and_settings_page(self):
        w = self.window
        self.select_device('stock_ipod', '/synthetic/OLD')
        incoming = self.fresh()
        _item, pages, tree = self.select_device('generic_usb', '/synthetic/NEW')
        w._open_settings()
        before = self.seat(pages, tree)
        self.assertTrue(w._on_refresh_done(incoming))
        self.assertEqual(self.seat(pages, tree), before)

    def test_returning_to_collection_uses_fresh_data_and_device_pages_stay_on_demand(self):
        w = self.window
        _item, pages, tree = self.select_device('generic_usb')
        w._publish_metadata_library(self.fresh(), ('2',))
        self.assertEqual(self.seat(pages, tree)[0], 1)
        self.assertNotIn('stock_ipod_pages', vars(w))
        collection = w.sb_tree.topLevelItem(0)
        self.assertTrue(collection.data(0, COLLECTION_ROLE))
        w.sb_tree.setCurrentItem(collection)
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertEqual(next(row['title'] for row in w.model._rows if row['id'] == '2'),
                         'Fresh saved title')
        self.assertEqual(w.device_tree.selectedItems(), [])

    def test_actual_disconnection_falls_back_and_later_ack_does_not_reopen_device(self):
        w = self.window
        self.select_device('generic_usb')
        w._publish_metadata_library(self.fresh(), ('2',))
        w._on_devices_scanned([], mount_tokens={})
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertEqual(w._current_device_path, '')
        self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
        self.assertEqual(next(row['title'] for row in w.model._rows if row['id'] == '2'),
                         'Fresh saved title')
        w._publish_metadata_library(self.fresh(), ('2',))
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertEqual(w._current_device_path, '')

    def test_explicit_changed_source_falls_back_to_complete_collection(self):
        w = self.window
        self.select_device('generic_usb')
        incoming = replace(self.fresh(), source='/synthetic/other.db', source_library_id='other')
        self.assertTrue(w._on_refresh_done(incoming, selection_path=str(incoming.source)))
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertEqual(w._view_key, 'sb:Collection')
        self.assertEqual(w.device_tree.selectedItems(), [])
        self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
        self.assertEqual({row['id'] for row in w.model._rows}, set(incoming.tracks_by_id))

    def test_disconnecting_a_previously_viewed_device_keeps_the_newer_artists_view(self):
        w = self.window
        self.select_device('generic_usb')
        artists = w.sb_tree.topLevelItem(1)
        self.assertTrue(artists.data(0, ARTISTS_ROLE))
        w.sb_tree.setCurrentItem(artists)
        self.assertEqual(w.stack.currentIndex(), 2)
        w._on_devices_scanned([], mount_tokens={})
        self.assertEqual(w.stack.currentIndex(), 2)
        self.assertIs(w.sb_tree.currentItem(), artists)
        self.assertEqual(w._view_key, 'sb:Artists')

    def test_early_stock_remount_discovery_preserves_the_newer_artists_view(self):
        w = self.window
        for kind in ('stock_ipod', 'dual_boot'):
            with self.subTest(kind=kind):
                self.select_device(kind)
                path = w._current_device_path
                profile = w._device_profiles[path]
                artists = w.sb_tree.topLevelItem(1)
                w.sb_tree.setCurrentItem(artists)
                with mock.patch.object(w, '_dev_scan_worker', SimpleNamespace(mount_tokens={path: 8})):
                    w._on_devices_discovered([(kind, path, 500, profile)])
                self.assertEqual(w.stack.currentIndex(), 2)
                self.assertIs(w.sb_tree.currentItem(), artists)
                self.assertEqual(w._view_key, 'sb:Artists')
                self.assertEqual(w._current_device_path, '')

    def test_early_stock_remount_discovery_falls_back_only_for_the_visible_device(self):
        w = self.window
        for kind in ('stock_ipod', 'dual_boot'):
            with self.subTest(kind=kind):
                self.select_device(kind)
                path = w._current_device_path
                profile = w._device_profiles[path]
                w._publish_metadata_library(self.fresh(), ('2',))
                with mock.patch.object(w, '_dev_scan_worker', SimpleNamespace(mount_tokens={path: 8})):
                    w._on_devices_discovered([(kind, path, 500, profile)])
                self.assertEqual(w.stack.currentIndex(), 0)
                self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
                self.assertEqual(next(row['title'] for row in w.model._rows if row['id'] == '2'),
                                 'Fresh saved title')
                self.assertEqual(w._current_device_path, '')

    def test_delayed_initial_library_keeps_a_newer_device_choice(self):
        w = self.window
        incoming = self.fresh()
        w.lib = None
        _item, pages, tree = self.select_device('generic_usb')
        before = self.seat(pages, tree)
        self.assertTrue(w._on_refresh_done(incoming))
        self.assertEqual(self.seat(pages, tree), before)

    def test_same_source_reselection_keeps_device_and_first_browser_load_still_defaults(self):
        w = self.window
        _item, pages, tree = self.select_device('generic_usb')
        before = self.seat(pages, tree)
        self.assertTrue(w._on_refresh_done(self.fresh(), selection_path=str(w.lib.source)))
        self.assertEqual(self.seat(pages, tree), before)
        w._select_only(w.sb_tree)
        w.stack.setCurrentIndex(0)
        w._view_key = None
        self.assertTrue(w._on_refresh_done(self.fresh()))
        self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
        self.assertEqual(w.stack.currentIndex(), 0)

    def test_initial_xml_completion_preserves_device_but_explicit_new_xml_selects_collection(self):
        w = self.window
        _item, pages, tree = self.select_device('generic_usb')
        before = self.seat(pages, tree)
        incoming = replace(self.fresh(), source='/synthetic/library.xml',
                           source_kind='xml', source_library_id='')
        with mock.patch('desktop.app.engine.load_library', return_value=incoming):
            self.assertTrue(w.open_library(str(incoming.source), initial=True))
        self.assertEqual(self.seat(pages, tree), before)
        different = replace(incoming, source='/synthetic/different.xml')
        with mock.patch('desktop.app.engine.load_library', return_value=different):
            self.assertTrue(w.open_library(str(different.source)))
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
        self.assertEqual(w.device_tree.selectedItems(), [])

    def test_pending_desktop_playlist_deletion_keeps_device_and_falls_back_only_in_desktop(self):
        w = self.window
        w.lib.playlists = [Playlist('Set', [], ['1'], '99')]
        w.lib.source_playlist_catalog['nodes'] = [dict(ID='99', UUID='playlist-uuid', Name='Set',
            ParentID='root', Seq=1, Attribute=0, rb_local_deleted=0)]
        w._populate_tree(engine.summary(w.lib))
        w.tree.setCurrentItem(w._find_tree_item('', '99'))
        _item, pages, tree = self.select_device('generic_usb')
        before = self.seat(pages, tree)
        with mock.patch.object(w, '_native_pending_deletions', return_value={'99'}):
            w._native_pending_changed(rebuild=True, presentation=w.lib)
        self.assertEqual(self.seat(pages, tree), before)
        self.assertEqual(w._view_key, 'sb:Collection')
        # The same invalidated view still needs an immediate desktop fallback.
        w.stack.setCurrentIndex(0)
        w._populate_tree(engine.summary(w.lib))
        w.tree.setCurrentItem(w._find_tree_item('', '99'))
        self.assertEqual(w._view_key, 'rb:Set')
        with mock.patch.object(w, '_native_pending_deletions', return_value={'99'}):
            w._native_pending_changed()
        self.assertTrue(w.sb_tree.currentItem().data(0, COLLECTION_ROLE))
        self.assertEqual(w._view_key, 'sb:Collection')

    def test_deleting_another_playlist_preserves_the_current_desktop_playlist(self):
        w = self.window
        w.lib.playlists = [Playlist('Delete', [], ['1'], '99'), Playlist('Keep', [], ['2'], '100')]
        w.lib.source_playlist_catalog['nodes'] = [dict(ID=identity, UUID='playlist-'+identity,
            Name=name, ParentID='root', Seq=number, Attribute=0, rb_local_deleted=0)
            for number, (identity, name) in enumerate((('99', 'Delete'), ('100', 'Keep')), 1)]
        w._populate_tree(engine.summary(w.lib))
        kept = w._find_tree_item('', '100')
        w.tree.setCurrentItem(kept)
        before = w.model._rows
        with mock.patch.object(w, '_native_pending_deletions', return_value={'99'}):
            w._native_pending_changed(rebuild=True, presentation=w.lib)
        self.assertEqual(w._view_key, 'rb:Keep')
        self.assertIs(w.tree.currentItem(), kept)
        self.assertIs(w.model._rows, before)
        self.assertEqual(w.stack.currentIndex(), 0)

    def test_terminal_local_playlist_adoption_keeps_the_active_device(self):
        w = self.window
        _item, pages, tree = self.select_device('generic_usb')
        before = self.seat(pages, tree)
        w.settings[playlist_unification.KEY] = playlist_unification.new(w.lib.source, w.lib.source_library_id)
        w._background_library_not_before = 0
        with (mock.patch.object(w.playlist_controller, 'reason', return_value=''),
              mock.patch.object(w.playlist_controller, 'enqueue', side_effect=AssertionError('source write'))):
            w.unification_controller.pump()
        self.assertTrue(w.unification_controller.state()['customs_done'])
        self.assertEqual(self.seat(pages, tree), before)
