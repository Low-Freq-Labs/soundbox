"""Reachable Overview formatting on isolated widgets, never physical devices."""
from dataclasses import replace
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication,QDialog,QTreeWidgetItem
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.usb_device_view import UsbSnapshot
from desktop.trees import DEVPATH_ROLE,DEVROLE_ROLE
from soundbox.device_operations import DeviceSessionIdentity
from soundbox.device_profile import DeviceProfile
from soundbox.usb_format import FormatTarget
from soundbox.library import Library
from soundbox import imports


class UsbFormatEntrypoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False), \
                mock.patch.object(MainWindow,'_refresh_devices'):
            self.window=MainWindow(isolated_dir=self.root)
            # Let startup's empty import-location check retire before a test
            # installs records for a different, explicitly held path worker.
            self.app.processEvents()
        self.addCleanup(self.close)
        self.dev='/synthetic/USB-FORMAT'
        self.facts=DeviceFacts(volume_uuid='owned-format',writable=True,filesystem_name='ExFAT')
        self.enterContext(mock.patch('desktop.app.device_facts',return_value=self.facts))
        self.enterContext(mock.patch('desktop.cdj.device_facts',return_value=self.facts))
        self.window._device_profiles[self.dev]=DeviceProfile('generic_usb','USB','exfat',False,False,False,False,False)
        self.window._device_mount_tokens[self.dev]=7
        self.mount=DeviceSessionIdentity(self.dev,7,self.facts.volume_uuid)
        self.window._device_coordinator.observe(self.mount)
        self.snapshot=UsbSnapshot(Path(self.dev),7,self.facts)
        self.window._usb_view_cache[self.dev]=self.snapshot
        self.window._current_device_path=self.dev;self.window._current_device_role=''
        self.pages=self.window.usb_device_pages
        self.window.device_content_stack.setCurrentIndex(2);self.window.stack.setCurrentIndex(1)
        self.window._present_usb_device(self.dev);self.window._update_device_action_state()

    def close(self):
        self.window.shutdown();self.window.close();self.window.deleteLater();self.app.processEvents()

    def binding(self):return self.snapshot,self.mount

    def open_device_menu(self,choose,*,role=''):
        item=QTreeWidgetItem(['Generated USB'])
        item.setData(0,DEVPATH_ROLE,self.dev);item.setData(0,DEVROLE_ROLE,role)
        with mock.patch.object(self.window.device_tree,'itemAt',return_value=item), \
                mock.patch('desktop.app.exec_transient_menu',side_effect=choose):
            self.window._device_menu(QPoint(0,0))

    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(predicate())

    def test_overview_button_reaches_existing_format_dialog_and_cancel_never_erases(self):
        button=self.pages.reformat_usb_button
        self.assertEqual(button.text(),'Reformat USB…')
        self.assertFalse(button.isHidden());self.assertTrue(button.isEnabled())
        target=FormatTarget(self.dev,(7,8),self.facts.volume_uuid,'disk99',(),256_000_000_000,(self.dev,))
        dialog=mock.Mock(spec=QDialog);dialog.exec.return_value=QDialog.DialogCode.Rejected
        with mock.patch('desktop.cdj.CdjWorker.start'), \
                mock.patch.object(self.window,'_defer_device_operation_until_scan_finishes',return_value=False), \
                mock.patch('soundbox.usb_format.capture',return_value=target) as capture, \
                mock.patch('soundbox.usb_format.format_usb',side_effect=AssertionError('Cancelled format erased')) as erase, \
                mock.patch('desktop.usb_preferences.FormatUsbDialog',return_value=dialog) as show:
            button.click()
            worker=self.window._usb_format_worker
            self.assertIsNotNone(worker)
            worker.run();worker.finished.emit()
        capture.assert_called_once_with(self.dev,token=7,volume_uuid=self.facts.volume_uuid,sources=[])
        show.assert_called_once_with(target,self.window);dialog.exec.assert_called_once()
        erase.assert_not_called();self.assertIsNone(self.window._usb_format_worker)
        self.assertEqual(self.window._usb_format_device,'')

    def test_menu_order_and_right_clicked_unselected_target_reach_existing_cancelled_confirmation(self):
        self.window._current_device_path='/synthetic/OTHER'
        self.window._usb_view_cache.clear();self.window.stack.setCurrentIndex(0)
        target=FormatTarget(self.dev,(7,8),self.facts.volume_uuid,'disk99',(),256_000_000_000,(self.dev,))
        dialog=mock.Mock(spec=QDialog);dialog.exec.return_value=QDialog.DialogCode.Rejected
        labels=[]
        def choose(menu,_position):
            labels.extend(action.text() for action in menu.actions())
            self.assertTrue(menu.actions()[2].isEnabled());menu.actions()[2].trigger()
        with mock.patch('desktop.cdj.CdjWorker.start'), \
                mock.patch.object(self.window,'_defer_device_operation_until_scan_finishes',return_value=False), \
                mock.patch('soundbox.usb_format.capture',return_value=target) as capture, \
                mock.patch('soundbox.usb_format.format_usb') as erase, \
                mock.patch('desktop.usb_preferences.FormatUsbDialog',return_value=dialog) as show:
            self.open_device_menu(choose)
            worker=self.window._usb_format_worker;self.assertIsNotNone(worker)
            worker.run();worker.finished.emit()
        self.assertEqual(labels,['Export…','Delete playlists…','Reformat USB…','Eject'])
        capture.assert_called_once_with(self.dev,token=7,volume_uuid=self.facts.volume_uuid,sources=[])
        show.assert_called_once_with(target,self.window);erase.assert_not_called()
        self.assertEqual(self.window._current_device_path,'/synthetic/OTHER')

    def test_menu_trigger_rechecks_exact_mount_session_uuid_role_and_operation_owners(self):
        profile=self.window._device_profiles[self.dev]
        cases=('disconnect','mount_aba','token','uuid','readonly','role','busy','restore',
               'shutdown','refresh','native_ack','path_owner','batch','finalizing')
        for change in cases:
            with self.subTest(change=change),mock.patch.object(self.window,'_reformat_usb') as format_usb:
                def choose(menu,_position):
                    action=next(a for a in menu.actions() if a.text()=='Reformat USB…')
                    self.assertTrue(action.isEnabled())
                    with ExitStack() as changed:
                        if change=='disconnect':self.window._device_coordinator.disconnect(self.dev)
                        elif change=='mount_aba':
                            self.window._device_coordinator.disconnect(self.dev)
                            self.window._device_coordinator.observe(DeviceSessionIdentity(self.dev,7,self.facts.volume_uuid))
                        elif change=='token':self.window._device_mount_tokens[self.dev]=8
                        elif change in ('uuid','readonly'):
                            changed.enter_context(mock.patch('desktop.app.device_facts',return_value=replace(
                                self.facts,**({'volume_uuid':'replacement'} if change=='uuid' else {'writable':False}))))
                        elif change=='role':self.window._device_profiles[self.dev]=replace(profile,kind='dual_boot',has_stock_ipod=True,has_rockbox=True)
                        elif change=='busy':changed.enter_context(mock.patch.object(self.window,'_device_operation_owned',return_value=True))
                        elif change in ('restore','shutdown','refresh','batch','finalizing'):
                            attr={'restore':'_state_maintenance','shutdown':'_shutting_down','refresh':'_refresh_worker',
                                  'batch':'_export_batch','finalizing':'_device_finalization_refreshing'}[change]
                            changed.enter_context(mock.patch.object(self.window,attr,object() if change in ('refresh','batch') else True,create=True))
                        elif change=='native_ack':changed.enter_context(mock.patch.object(type(self.window.tag_controller),'owned',new_callable=mock.PropertyMock,return_value=True))
                        elif change=='path_owner':changed.enter_context(mock.patch.object(self.window,'_import_tasks',SimpleNamespace(pending=1),create=True))
                        action.trigger()
                try:self.open_device_menu(choose)
                finally:
                    self.window._device_profiles[self.dev]=profile
                    self.window._device_mount_tokens[self.dev]=7
                    self.window._device_coordinator.mounts[self.dev]=self.mount
                format_usb.assert_not_called()

    def test_menu_format_is_disabled_without_current_writable_mount_and_never_offered_for_ipod_roles(self):
        def disabled(menu,_position):
            action=next(a for a in menu.actions() if a.text()=='Reformat USB…')
            self.assertFalse(action.isEnabled());action.trigger()
        with mock.patch.object(self.window,'_reformat_usb') as format_usb:
            for facts in (replace(self.facts,writable=False),replace(self.facts,volume_uuid='')):
                with mock.patch('desktop.app.device_facts',return_value=facts):self.open_device_menu(disabled)
            self.window._device_coordinator.disconnect(self.dev);self.open_device_menu(disabled)
            self.window._device_coordinator.mounts[self.dev]=self.mount
            for kind in ('stock_ipod','dual_boot','rockbox','ipod_usb'):
                physical='dual_boot' if kind=='ipod_usb' else kind
                self.window._device_profiles[self.dev]=DeviceProfile(physical,physical,'exfat',
                    physical in ('dual_boot','rockbox'),physical in ('stock_ipod','dual_boot'),False,False,False)
                def absent(menu,_position):self.assertNotIn('Reformat USB…',[a.text() for a in menu.actions()])
                self.open_device_menu(absent,role='ipod_usb' if kind=='ipod_usb' else '')
        format_usb.assert_not_called()

    def test_busy_restore_and_shutdown_disable_or_refuse_captured_button_request(self):
        for flag in ('busy','_state_maintenance','_shutting_down'):
            with self.subTest(flag=flag),mock.patch.object(self.window,'_reformat_usb') as format_usb:
                if flag=='busy':
                    with mock.patch.object(self.window,'_device_operation_owned',return_value=True):
                        self.window._update_device_action_state()
                        self.assertFalse(self.pages.reformat_usb_button.isEnabled())
                        self.window._on_usb_reformat_requested(self.binding())
                else:
                    with mock.patch.object(self.window,flag,True):
                        self.window._update_device_action_state()
                        self.assertFalse(self.pages.reformat_usb_button.isEnabled())
                        self.window._on_usb_reformat_requested(self.binding())
                format_usb.assert_not_called()
            self.window._update_device_action_state()

    def test_snapshot_replacement_same_path_remount_and_volume_uuid_expire_request(self):
        with mock.patch.object(self.window,'_reformat_usb') as format_usb:
            self.window._usb_view_cache[self.dev]=replace(self.snapshot)
            self.window._on_usb_reformat_requested(self.binding())
            self.window._usb_view_cache[self.dev]=self.snapshot
            self.window._device_coordinator.disconnect(self.dev)
            self.window._device_coordinator.observe(DeviceSessionIdentity(self.dev,7,self.facts.volume_uuid))
            self.window._on_usb_reformat_requested(self.binding())
            self.window._device_coordinator.mounts[self.dev]=self.mount
            with mock.patch('desktop.app.device_facts',return_value=replace(self.facts,volume_uuid='replacement')):
                self.window._on_usb_reformat_requested(self.binding())
        format_usb.assert_not_called()

    def test_inflight_library_read_disables_overview_and_refuses_captured_request(self):
        with mock.patch.object(self.window,'_refresh_worker',object()), \
                mock.patch.object(self.window,'_reformat_usb') as format_usb:
            self.window._update_device_action_state()
            self.assertFalse(self.pages.reformat_usb_button.isEnabled())
            self.assertIn('library',self.pages.reformat_usb_button.toolTip())
            self.window._on_usb_reformat_requested(self.binding())
        format_usb.assert_not_called()

    def test_unpublished_native_ack_disables_overview_and_refuses_captured_request(self):
        with mock.patch.object(type(self.window.tag_controller),'owned',new_callable=mock.PropertyMock,return_value=True), \
                mock.patch.object(self.window,'_reformat_usb') as format_usb:
            self.window._update_device_action_state()
            self.assertFalse(self.pages.reformat_usb_button.isEnabled())
            self.window._on_usb_reformat_requested(self.binding())
        format_usb.assert_not_called()

    def test_owned_folder_import_disables_overview_until_its_retired_callback(self):
        with mock.patch.object(self.window,'_import_tasks',SimpleNamespace(pending=1),create=True), \
                mock.patch.object(self.window,'_reformat_usb') as format_usb:
            self.window._update_device_action_state()
            self.assertFalse(self.pages.reformat_usb_button.isEnabled())
            self.window._on_usb_reformat_requested(self.binding())
        format_usb.assert_not_called()

    def test_import_metadata_owner_immediately_refreshes_footer_on_admission_and_drain(self):
        path=self.root/'import.wav';path.write_bytes(b'owned fixture, no decoder')
        rec=dict(path=str(path),size=path.stat().st_size,time=0,added='2026-11-26')
        self.window.imported={'sb-import':rec}
        self.window.lib=Library({'sb-import':imports.make_track('sb-import',rec)},[],self.root/'source.xml')
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def probe(_path):
            entered.set();release.wait(3);return dict(size=path.stat().st_size,time=12)
        with mock.patch('soundbox.imports.probe',side_effect=probe):
            self.window._probe_import_metadata(['sb-import'])
            self.assertTrue(entered.wait(1));self.assertFalse(self.pages.reformat_usb_button.isEnabled())
            release.set();self.wait(lambda:self.window._import_tasks.pending==0)
        self.assertTrue(self.pages.reformat_usb_button.isEnabled())
        self.assertEqual(self.window.imported['sb-import']['time'],12)

    def test_relink_owner_immediately_refreshes_footer_after_joined_retirement(self):
        rec=dict(path=str(self.root/'missing.wav'),size=1,time=0,added='2026-11-26')
        self.window.imported={'sb-relink':rec}
        self.window.lib=Library({'sb-relink':imports.make_track('sb-relink',rec)},[],self.root/'source.xml')
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def search(*_args):
            entered.set();release.wait(3);return imports.RelinkSearch(complete=False)
        with mock.patch('soundbox.imports.find_moved',side_effect=search):
            self.window._fix_links([self.root])
            self.assertTrue(entered.wait(1));self.assertFalse(self.pages.reformat_usb_button.isEnabled())
            release.set();self.wait(lambda:not self.window._oneshots)
        self.assertTrue(self.pages.reformat_usb_button.isEnabled())
        self.assertEqual(self.window.imported['sb-relink']['path'],rec['path'])

    def test_ipod_dual_rockbox_and_ipod_usb_roles_never_offer_or_admit_format(self):
        for kind in ('stock_ipod','dual_boot','rockbox','ipod_usb'):
            physical='dual_boot' if kind=='ipod_usb' else kind
            stock=physical in ('stock_ipod','dual_boot');rockbox=physical in ('rockbox','dual_boot')
            self.window._device_profiles[self.dev]=DeviceProfile(physical,physical,'msdos',rockbox,stock,True,stock,False)
            self.window._current_device_role='ipod_usb' if kind=='ipod_usb' else ''
            with self.subTest(kind=kind),mock.patch.object(self.window,'_reformat_usb') as format_usb:
                self.window._present_usb_device(self.dev);self.window._update_device_action_state()
                self.assertTrue(self.pages.reformat_usb_button.isHidden())
                self.window._on_usb_reformat_requested(self.binding())
                format_usb.assert_not_called()


if __name__=='__main__':unittest.main()
