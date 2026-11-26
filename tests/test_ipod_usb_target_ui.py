"""Two explicit export roles share one physical iPod and its operation owner."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QMimeData, QPoint, QPointF, QTimer, Qt
from PySide6.QtWidgets import QApplication

from desktop.devices import DeviceFacts
from desktop.trees import DEVPATH_ROLE, DEVROLE_ROLE
from desktop.usb_device_view import UsbDevicePages, UsbSnapshot
from soundbox.cdj_usb import CdjLibrary, CdjPlaylist
from soundbox.device_operations import DeviceCommand, Phase
from soundbox.device_profile import DeviceProfile
from soundbox.library import Playlist
from tests import test_device_navigation_publication as navigation
from tests.test_stock_first_export_actions import FAVORITES, snapshot


class IpodUsbTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.case=navigation.DeviceNavigationPublication();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.w=self.case.window
        for timer in self.w.findChildren(QTimer):timer.stop()
        self.root=self.w._data_file.parent/'generated-ipod';self.root.mkdir()
        self.dev=str(self.root)
        self.native=snapshot(FAVORITES,target=self.root,volume_uuid='generated-volume')
        self.profile=DeviceProfile('stock_ipod','iTunes','fat32',False,True,False,True,True,
            model='MA446',library_persistent_id=2,stock_format=self.native.stock_format)
        self.facts=DeviceFacts(volume_uuid='generated-volume',filesystem_name='fat32',writable=True,
            capacity_bytes=100000,device_identifier='generated')
        self.enterContext(mock.patch('desktop.app.device_facts',return_value=self.facts))
        self.enterContext(mock.patch('desktop.app.device_fs',return_value='fat32'))
        w=self.w;w.lib.playlists=[Playlist('Favorites',[],['1'],'1000')]
        w.lib.source_playlist_catalog={'nodes':[dict(ID='1000',UUID='00000000-0000-4000-8000-000000001000',
            rb_local_deleted=0)],'members':[]}
        w._stock_view_cache={self.dev:(self.native,None)};w._stock_snapshot=self.native
        self.token=self.root.stat().st_dev
        self.scan()

    def scan(self,*,token=None):
        self.w._on_devices_scanned([('Generated',self.dev,50000,3,self.profile)],
            mount_tokens={self.dev:self.token if token is None else token})

    def rows(self):
        return {self.w.device_tree.topLevelItem(i).data(0,DEVROLE_ROLE) or '':self.w.device_tree.topLevelItem(i)
                for i in range(self.w.device_tree.topLevelItemCount())}

    def select(self,role):
        self.w._on_device_click(self.rows()[role],0)
        return self.w.usb_device_pages if role else self.w.stock_ipod_pages

    def test_two_rows_share_physical_identity_without_leaking_sync_into_usb(self):
        w=self.w;rows=self.rows()
        self.assertEqual(set(rows),{'','ipod_usb'})
        self.assertEqual([row.data(0,DEVPATH_ROLE) for row in rows.values()],[self.dev,self.dev])
        self.assertEqual(set(w._device_profiles),{self.dev})
        self.assertIs(w._device_profiles[self.dev],self.profile)
        self.assertEqual(len(w._device_coordinator.mounts),1)
        self.assertNotIn('Sync',rows['ipod_usb'].text(0)+rows['ipod_usb'].toolTip(0))
        self.assertNotIn('iPod USB',rows['ipod_usb'].text(0))
        self.select('');self.assertEqual(w.device_sync_btn.text(),'Sync')
        pages=self.select('ipod_usb')
        self.assertEqual(w.device_sync_btn.text(),'Export')
        self.assertEqual(w.device_content_stack.currentIndex(),2)
        self.assertIn(self.dev,w._usb_view_requests)
        details=pages.detail_trees['SoundBox']
        self.assertFalse(any('Sync' in details.topLevelItem(i).text(0) for i in range(details.topLevelItemCount())))
        self.assertEqual(w._drift_cache[self.dev],3)
        self.scan()
        self.assertIs(self.rows()['ipod_usb'],rows['ipod_usb'])
        self.assertEqual(w.device_sync_btn.text(),'Export')
        self.assertIs(w._device_profiles[self.dev],self.profile)

    def test_primary_action_and_drop_pin_explicit_role_instead_of_current_selection(self):
        w=self.w;self.select('ipod_usb')
        with mock.patch.object(w,'_show_export_dialog') as chooser:
            w._device_primary_action();chooser.assert_called_once_with((self.dev,'ipod_usb'))
        self.select('')
        with (mock.patch.object(w,'_begin_cdj_export') as cdj,
              mock.patch.object(w,'_begin_stock_ipod_sync') as stock):
            w._on_device_drop(self.dev,[('rb','Favorites')],role='ipod_usb')
            cdj.assert_called_once();self.assertEqual(cdj.call_args.kwargs,{'from_drop':True,'target_role':'ipod_usb'})
            stock.assert_not_called();cdj.reset_mock()
            w._on_device_drop(self.dev,[('rb','Favorites')])
            stock.assert_called_once();cdj.assert_not_called()
        with mock.patch.object(w,'_on_device_drop') as drop:
            mime=QMimeData();mime.setText('rb\tFavorites')
            event=SimpleNamespace(position=lambda:QPointF(0,0),mimeData=lambda:mime,
                acceptProposedAction=mock.Mock(),ignore=mock.Mock())
            with mock.patch.object(w.device_tree,'itemAt',return_value=self.rows()['ipod_usb']):w.device_tree.dropEvent(event)
            drop.assert_called_once_with(self.dev,[('rb','Favorites')],role='ipod_usb')
            event.acceptProposedAction.assert_called_once()

    def test_usb_menu_has_only_export_delete_and_physical_eject(self):
        w=self.w;seen=[]
        def choose(menu,_pos):
            seen.extend(action.text() for action in menu.actions());menu.actions()[1].trigger()
        with (mock.patch.object(w.device_tree,'itemAt',return_value=self.rows()['ipod_usb']),
              mock.patch('desktop.app.exec_transient_menu',side_effect=choose),
              mock.patch.object(w,'_begin_cdj_delete',create=True) as remove):
            w._device_menu(QPoint(0,0))
        self.assertEqual(seen,['Export…','Delete playlists…','Eject'])
        remove.assert_called_once_with(self.dev,target_role='ipod_usb')

    def test_stock_background_publication_preserves_selected_usb_surface(self):
        w=self.w;self.select('');w._set_device_tab(2)
        self.assertEqual((w.device_tab_group.checkedId(),w.stock_ipod_pages.stack.currentIndex()),(2,2))
        pages=self.select('ipod_usb');position=w.device_content_stack.currentIndex()
        self.assertEqual((w.device_tab_group.checkedId(),pages.stack.currentIndex()),(0,0))
        w._set_device_tab(1)
        w._set_stock_device_tab(0)
        self.assertEqual((w.device_tab_group.checkedId(),pages.stack.currentIndex()),(1,1))
        stock=w.stock_ipod_pages
        with mock.patch.object(stock,'set_snapshot',side_effect=AssertionError('Hidden stock page replaced USB')):
            w._on_stock_device_view(self.dev,(replace(self.native,sync_state='local_changes',sync_local_changes=9),None))
        self.assertEqual(w.device_content_stack.currentIndex(),position)
        self.assertIs(w.device_content_stack.currentWidget(),pages)
        self.assertEqual(w._current_device_role,'ipod_usb')
        self.assertEqual(w.device_sync_btn.text(),'Export')
        self.assertEqual((w.device_tab_group.checkedId(),pages.stack.currentIndex()),(1,1))
        self.select('')
        self.assertEqual(w.device_tab_group.checkedId(),w.stock_ipod_pages.stack.currentIndex())

    def test_shared_busy_eject_and_remount_retire_both_logical_rows(self):
        w=self.w;self.select('ipod_usb');mount=w._device_coordinator.mounts[self.dev]
        worker=object();w._device_coordinator.begin(DeviceCommand(mount,'commit-cdj'),worker,Phase.EXECUTING)
        try:
            w._update_device_action_state();self.assertFalse(w.device_sync_btn.isEnabled())
            with mock.patch('desktop.device_browser.QMessageBox.warning') as warning:w._eject(self.dev)
            warning.assert_called_once();self.assertFalse(w._device_coordinator.eject_pending)
        finally:w._device_coordinator.finished(worker)
        self.scan(token=self.token+1)
        self.assertEqual(w._current_device_path,'');self.assertEqual(w._current_device_role,'')
        self.assertNotEqual(w._device_coordinator.mounts[self.dev],mount)
        self.assertEqual(set(self.rows()),{'','ipod_usb'})
        w._retire_ejected_device(self.dev)
        self.assertEqual(w.device_tree.topLevelItemCount(),0)
        self.assertNotIn(self.dev,w._device_profiles);self.assertNotIn(self.dev,w._device_coordinator.mounts)

    def test_usb_delete_signal_refuses_hidden_stock_page_and_pins_visible_role(self):
        w=self.w;pages=self.select('ipod_usb')
        with mock.patch.object(w,'_begin_cdj_delete',create=True) as remove:
            pages.delete_requested.emit(['42'])
            remove.assert_called_once_with(self.dev,['42'],target_role='ipod_usb')
            remove.reset_mock();self.select('');pages.delete_requested.emit(['42']);remove.assert_not_called()

    def test_retired_stock_and_async_generic_usb_restore_keep_header_and_body_on_the_same_tab(self):
        w=self.w;self.select('');w._set_device_tab(2)
        w._retire_ejected_device(self.dev)
        target=self.root.parent/'ordinary-usb';target.mkdir();dev=str(target)
        profile=DeviceProfile('generic_usb','USB','fat32',False,False,True,False,False)
        rows=[('Ordinary USB',dev,50000,0,profile)];tokens={dev:target.stat().st_dev}
        w._on_devices_scanned(rows,mount_tokens=tokens)
        w._on_device_click(w.device_tree.topLevelItem(0),0)
        pages=w.usb_device_pages
        self.assertEqual((w.device_tab_group.checkedId(),pages.stack.currentIndex()),(0,0))
        # The requested worker now supplies this device's saved playlist tab.
        # Page restoration must not leave the old Overview header checked.
        facts=replace(self.facts,volume_uuid='ordinary-usb-uuid')
        native=CdjLibrary((),'Ordinary',0,0,(),0,())
        pages._states[facts.volume_uuid]=dict(page=1,expanded=[],selected='')
        w._usb_view_cache[dev]=UsbSnapshot(target,tokens[dev],facts,native=native)
        w._on_devices_scanned(rows,mount_tokens=tokens)
        self.assertEqual((w.device_tab_group.checkedId(),pages.stack.currentIndex()),(1,1))


class UsbPlaylistDeleteSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.pages=UsbDevicePages();self.addCleanup(self.pages.deleteLater)
        self.first=CdjPlaylist('1','0','First',False,())
        self.second=CdjPlaylist('2','0','Second',False,())
        self.folder=CdjPlaylist('3','0','Folder',True,())
        native=CdjLibrary((self.first,self.second,self.folder),'Generated USB',0,0,(),0,())
        self.snapshot=UsbSnapshot(Path('/generated/no-device-io'),7,
            DeviceFacts(volume_uuid='generated',writable=True),native=native)
        self.profile=SimpleNamespace(kind='ipod_usb',label='USB',cdj_export=True)
        self.pages.set_snapshot(self.snapshot,name='Generated',profile=self.profile,free=0,drift=629)
        self.deleted=[];self.pages.delete_requested.connect(self.deleted.append)

    def test_checked_playlists_delete_only_exact_leaf_ids_and_reject_folder_or_stale_menu(self):
        tree=self.pages.playlist_tree;first,second,folder=(tree.topLevelItem(i) for i in range(3))
        first.setCheckState(0,Qt.Checked);second.setCheckState(0,Qt.Checked)
        self.assertEqual(self.pages._checked_playlist_ids(),('1','2'))
        with mock.patch.object(tree,'itemAt',return_value=first),mock.patch('desktop.usb_device_view.exec_transient_menu',
                side_effect=lambda menu,_pos:menu.actions()[0].trigger()):self.pages._playlist_context_menu(QPoint(0,0))
        self.assertEqual(self.deleted,[['1','2']])
        first.setCheckState(0,Qt.Unchecked);second.setCheckState(0,Qt.Unchecked)
        folder.setCheckState(0,Qt.Checked);self.assertFalse(self.pages._checked_playlist_ids())
        first.setCheckState(0,Qt.Checked)
        def replaced(menu,_pos):
            self.pages.set_snapshot(replace(self.snapshot),name='Generated',profile=self.profile,free=0,drift=0)
            menu.actions()[0].trigger()
        with mock.patch.object(tree,'itemAt',return_value=first),mock.patch('desktop.usb_device_view.exec_transient_menu',side_effect=replaced):
            self.pages._playlist_context_menu(QPoint(0,0))
        self.assertEqual(self.deleted,[['1','2']])
        self.pages.playlist_tree.topLevelItem(0).setCheckState(0,Qt.Checked)
        self.pages.set_playlist_deletion_available(False)
        self.assertFalse(self.pages._checked_playlist_ids())
