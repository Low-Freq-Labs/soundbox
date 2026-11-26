"""A USB setup observation offers consent once without becoming write authority."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QMessageBox,QWidget

from desktop.devices import DeviceFacts
from desktop.usb_setup import UsbSetupController
from soundbox.cdj_usb import CdjSetupReadiness
from soundbox.device_operations import DeviceOperationCoordinator,DeviceSessionIdentity
from soundbox.device_profile import DeviceProfile


class UsbSetupUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))/'TEST-2';self.root.mkdir()
        self.path=str(self.root);self.token=self.root.stat().st_dev
        self.host=w=QWidget();self.addCleanup(w.deleteLater)
        self.enterContext(mock.patch.object(w,'isVisible',return_value=True))
        w._shutting_down=False;w._device_coordinator=DeviceOperationCoordinator()
        w._device_coordinator.observe(DeviceSessionIdentity(self.path,self.token))
        w._device_mount_tokens={self.path:self.token}
        w._device_profiles={self.path:DeviceProfile('generic_usb','USB','fat32',False,False,False,False,False)}
        w._device_operation_owned=lambda:False
        w._usb_device_rows={self.path:('TEST-2',1000,None,w._device_profiles[self.path])}
        w._current_device_path=self.path;w._current_device_role=''
        w._state_repository=SimpleNamespace(generation=4,area=SimpleNamespace(generation=2))
        w.lib=SimpleNamespace(source='/generated/source.db',source_kind='rekordbox-db',source_library_id='fixture')
        w._begin_cdj_initialize=mock.Mock()
        self.facts=DeviceFacts(volume_uuid='fixture-volume',filesystem_name='fat32',writable=True)
        self.enterContext(mock.patch('desktop.usb_setup.device_facts',side_effect=lambda *_a,**_k:self.facts))
        self.controller=UsbSetupController(w);self.addCleanup(self.controller.stop)
        self.proof=CdjSetupReadiness('missing','',self.root,(self.token,self.root.stat().st_ino),
                                   self.facts.volume_uuid,'generic_usb')
        self.observation=(self.path,self.token,self.facts.volume_uuid,'generic_usb',self.proof)
        self.dialogs=[]

    def queue(self,observation=None):
        self.controller.consume({self.path:observation or self.observation});self.controller.timer.stop()

    def answer(self,accept=False,*,during=None):
        def choose():
            box=self.controller.dialog
            self.assertIsInstance(box,QMessageBox)
            self.dialogs.append(box.text())
            self.assertEqual(box.defaultButton().text(),'Not now')
            self.assertEqual(box.escapeButton().text(),'Not now')
            self.assertIn(self.host._usb_device_rows[self.path][0],box.informativeText())
            if during is not None:during()
            if self.controller.dialog is not None:
                next(button for button in box.buttons() if button.text()==('Configure' if accept else 'Not now')).click()
        QTimer.singleShot(0,choose);self.controller.poll();self.controller.timer.stop()

    def test_decline_preserves_files_and_suppresses_every_poll_until_reattachment(self):
        original=self.root/'music.wav';original.write_bytes(b'generated audio sentinel')
        before=(original.read_bytes(),original.stat())
        self.queue();self.answer()
        self.host._begin_cdj_initialize.assert_not_called()
        for _ in range(3):self.queue();self.controller.poll()
        self.assertEqual(len(self.dialogs),1)
        self.assertEqual(self.controller.known(),frozenset({self.observation[:4]}))
        after=original.stat();self.assertEqual(original.read_bytes(),before[0])
        self.assertEqual((after.st_ino,after.st_mtime_ns,after.st_ctime_ns),
                         (before[1].st_ino,before[1].st_mtime_ns,before[1].st_ctime_ns))
        self.assertEqual(list(self.root.iterdir()),[original])
        self.host._device_coordinator.disconnect(self.path)
        self.assertEqual(self.controller.known(),frozenset())
        self.host._device_coordinator.observe(DeviceSessionIdentity(self.path,self.token))
        self.queue();self.answer();self.assertEqual(len(self.dialogs),2)

    def test_configure_dispatches_exact_proof_without_playlist_or_sync_work(self):
        self.queue();self.answer(True)
        self.host._begin_cdj_initialize.assert_called_once_with(self.path,target_role='generic_usb',expected_setup=self.proof)
        self.assertEqual(list(self.root.iterdir()),[])
        self.queue();self.controller.poll();self.assertEqual(len(self.dialogs),1)

    def test_ordinary_settings_publication_during_consent_does_not_discard_configure(self):
        self.queue();self.answer(True,during=lambda:setattr(self.host._state_repository,'generation',5))
        self.host._begin_cdj_initialize.assert_called_once()

    def test_configured_unavailable_and_invalid_stock_role_never_offer(self):
        for state in ('configured','unavailable'):
            with self.subTest(state=state):
                self.controller.seen.clear()
                self.queue((*self.observation[:4],replace(self.proof,state=state)))
                self.controller.poll();self.assertFalse(self.controller.pending)
                self.assertEqual(self.controller.known(),frozenset({self.observation[:4]}))
        self.controller.seen.clear()
        self.queue((*self.observation[:3],'stock_ipod',replace(self.proof,target_role='stock_ipod')))
        self.controller.poll();self.assertFalse(self.controller.seen)
        self.host._begin_cdj_initialize.assert_not_called()

    def test_ipod_companion_offer_names_volume_and_dispatches_only_usb_role(self):
        self.host._device_profiles[self.path]=DeviceProfile('stock_ipod','iTunes','fat32',False,True,False,True,True)
        self.host._usb_device_rows[self.path]=('SONNY',1000,None,self.host._device_profiles[self.path])
        proof=replace(self.proof,target_role='ipod_usb',ipod_control_identity=(self.token,100))
        self.queue((*self.observation[:3],'ipod_usb',proof));self.answer(True)
        self.host._begin_cdj_initialize.assert_called_once_with(self.path,target_role='ipod_usb',expected_setup=proof)

    def test_busy_batch_restore_initial_loading_and_other_modal_defer_offer(self):
        guards=(('_export_batch',{'active':True}),('_state_maintenance',True),('_state_publication',True),
                ('_refresh_worker',object()),('_source_sync_waiting',object()),('_cdj_waiting',object()),
                ('_device_finalization_refreshing',True))
        self.queue()
        for name,value in guards:
            with self.subTest(guard=name):
                setattr(self.host,name,value);self.controller.poll();self.controller.timer.stop()
                self.assertIsNone(self.controller.dialog);self.assertEqual(len(self.controller.pending),1)
                setattr(self.host,name,None)
        self.host._device_coordinator.active=object();self.controller.poll();self.controller.timer.stop()
        self.assertEqual(len(self.controller.pending),1);self.host._device_coordinator.active=None
        with mock.patch('desktop.usb_setup.QApplication.activeModalWidget',return_value=object()):
            self.controller.poll();self.controller.timer.stop();self.assertEqual(len(self.controller.pending),1)
        self.answer();self.assertEqual(len(self.dialogs),1)

    def test_decline_remains_known_when_cached_facts_temporarily_unavailable(self):
        self.queue();self.answer()
        self.facts=replace(self.facts,writable=False)
        self.assertEqual(self.controller.known(),frozenset({self.observation[:4]}))
        self.facts=replace(self.facts,writable=True);self.queue();self.controller.poll()
        self.assertEqual(len(self.dialogs),1)

    def test_consent_is_refused_after_mount_role_uuid_source_restore_or_ownership_change(self):
        def remount():
            self.host._device_coordinator.disconnect(self.path)
            self.host._device_coordinator.observe(DeviceSessionIdentity(self.path,self.token))
        cases=(('remount',remount),('role',lambda:setattr(self.host,'_current_device_role','ipod_usb')),
               ('uuid',lambda:setattr(self,'facts',replace(self.facts,volume_uuid='different'))),
               ('source',lambda:setattr(self.host.lib,'source','/changed/source.db')),
               ('restore',lambda:setattr(self.host._state_repository.area,'generation',3)),
               ('busy',lambda:setattr(self.host._device_coordinator,'active',object())))
        for name,change in cases:
            with self.subTest(change=name):
                self.controller.seen.clear();self.controller.pending.clear()
                self.host._current_device_role='';self.facts=replace(self.facts,volume_uuid='fixture-volume')
                self.host.lib.source='/generated/source.db';self.host._state_repository.area.generation=2
                self.host._device_coordinator.active=None
                self.queue();self.answer(True,during=change)
                self.host._begin_cdj_initialize.assert_not_called()

    def test_stop_rejects_open_dialog_without_dispatching_or_reoffering(self):
        self.queue();self.answer(True,during=self.controller.stop)
        self.host._begin_cdj_initialize.assert_not_called()
        self.assertFalse(self.controller.pending);self.assertFalse(self.controller.timer.isActive())

    def test_stale_observation_is_not_rebound_to_another_volume_or_token(self):
        for observation in ((self.path,self.token+1,*self.observation[2:]),
                            (self.path,self.token,'different',self.observation[3],self.proof)):
            with self.subTest(observation=observation):
                self.queue(observation);self.assertFalse(self.controller.seen)
        self.host._begin_cdj_initialize.assert_not_called()


class UsbSetupScanPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_actual_window_consumes_only_after_scan_owner_retirement_and_drops_cancelled_results(self):
        from threading import Event
        from PySide6.QtCore import QThread
        from soundbox.device_operations import DeviceCommand,Phase
        from tests.test_device_navigation_publication import DeviceNavigationPublication
        case=DeviceNavigationPublication();case.setUp();self.addCleanup(case.doCleanups)
        w=case.window
        for timer in w.findChildren(QTimer):timer.stop()
        root=w._data_file.parent/'TEST-2';root.mkdir();path=str(root);token=root.stat().st_dev
        facts=DeviceFacts(volume_uuid='fixture-volume',filesystem_name='fat32',writable=True)
        profile=DeviceProfile('generic_usb','USB','fat32',False,False,False,False,False)
        proof=CdjSetupReadiness('missing','',root,(token,root.stat().st_ino),facts.volume_uuid,'generic_usb')
        setup=w.usb_setup_controller
        self.enterContext(mock.patch('desktop.app.device_facts',return_value=facts))
        self.enterContext(mock.patch('desktop.usb_setup.device_facts',return_value=facts))
        self.enterContext(mock.patch('desktop.app.device_fs',return_value='fat32'))
        self.enterContext(mock.patch.object(w,'isVisible',return_value=True))
        initialize=self.enterContext(mock.patch.object(w,'_begin_cdj_initialize'))
        def worker():
            scan=QThread(w);scan.cancellation=Event();scan.retry_discovery=False
            scan.mount_tokens={path:token};scan.new_paths=();scan.usb_snapshots={}
            scan.results=[('TEST-2',path,1000,None,profile)]
            scan.usb_setup_results={path:(path,token,facts.volume_uuid,'generic_usb',proof)}
            w._dev_scan_worker=scan
            return scan
        scan=worker();w._on_devices_discovered([('TEST-2',path,1000,profile)])
        identity=w._device_coordinator.mounts[path]
        w._device_coordinator.begin(DeviceCommand(identity,'discover'),scan,Phase.DISCOVERING)
        self.assertFalse(setup.seen)
        actual_consume=setup.consume
        def consume(observations):
            self.assertIsNone(w._dev_scan_worker);self.assertIsNone(w._device_coordinator.active)
            actual_consume(observations)
        with mock.patch.object(setup,'consume',side_effect=consume) as published:
            w._finish_device_scan_worker(scan);published.assert_called_once()
        setup.timer.stop();self.assertEqual(len(setup.pending),1)
        def decline():
            self.assertIsNotNone(setup.dialog)
            self.assertIn('TEST-2',setup.dialog.informativeText())
            setup.dialog.defaultButton().click()
        QTimer.singleShot(0,decline);setup.poll();setup.timer.stop()
        initialize.assert_not_called();self.assertEqual(len(setup.seen),1)
        setup.seen.clear();scan=worker();scan.cancellation.set()
        with mock.patch.object(setup,'consume',wraps=actual_consume) as published:
            w._finish_device_scan_worker(scan);published.assert_not_called()
        self.assertFalse(setup.seen);self.assertFalse(setup.pending)

    def test_actual_window_defers_routine_scan_while_setup_dialog_is_open_but_allows_forced_mount_scan(self):
        from desktop.app import MainWindow
        from tests.test_device_navigation_publication import DeviceNavigationPublication
        refresh=MainWindow._refresh_devices
        case=DeviceNavigationPublication();case.setUp();self.addCleanup(case.doCleanups)
        w=case.window
        for timer in w.findChildren(QTimer):timer.stop()
        w._isolated=False;w.usb_setup_controller.showing=True
        worker=mock.Mock()
        with (mock.patch('desktop.app.DeviceScanWorker',return_value=worker) as create,
              mock.patch.object(w,'_start_device_worker') as start):
            try:
                refresh(w)
                create.assert_not_called();start.assert_not_called()
                self.assertIsNone(w._dev_scan_worker)
                refresh(w,force=True)
                create.assert_called_once();start.assert_called_once_with(worker,'','discover')
                self.assertIs(w._dev_scan_worker,worker)
            finally:
                w._dev_scan_worker=None;w.usb_setup_controller.showing=False
