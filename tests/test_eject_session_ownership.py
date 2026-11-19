"""Exact Eject sessions over generated workers and a fake OS process boundary."""
from types import SimpleNamespace
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.device_browser import DeviceBrowserMixin
from desktop.workers import OneShotTask
from soundbox.device_operations import DeviceCommand, DeviceOperationCoordinator, DeviceSessionIdentity, Phase


class Signal:
    def __init__(self):self.callbacks=[]
    def connect(self, callback):self.callbacks.append(callback)
    def emit(self, *args):
        for callback in tuple(self.callbacks):callback(*args)


class Process:
    NotRunning=0
    def __init__(self, parent):
        self.finished=Signal();self.errorOccurred=Signal();self.calls=[];self.deleted=0
    def start(self, *args):self.calls.append(args)
    def state(self):return self.NotRunning
    def readAllStandardError(self):return b'generated OS refusal'
    def deleteLater(self):self.deleted+=1


class Host(DeviceBrowserMixin):
    def __init__(self):
        self._device_coordinator=DeviceOperationCoordinator()
        self.identity=DeviceSessionIdentity('/synthetic/IPOD',7,'library')
        self._device_coordinator.observe(self.identity)
        self._stock_view_pending={};self._pending_device_operation=None
        self._exporting=False;self._device_finalization_refreshing=False;self._shutting_down=False
        self._eject_owner=None;self._eject_session=None;self._eject_proc=None;self._eject_ready_device=''
        self._update_device_action_state=mock.Mock();self._start_pulse=mock.Mock();self._stop_pulse=mock.Mock()
        self._flash_status=mock.Mock();self._set_status_text=mock.Mock();self._refresh_devices=mock.Mock()
        self.fs_view=mock.Mock();self.fs_model=mock.Mock();self._retire_ejected_device=mock.Mock()


class EjectSessionOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.host=Host();self.processes=[]
        def process(parent):
            value=Process(parent);self.processes.append(value);return value
        process.NotRunning=Process.NotRunning
        self.enterContext(mock.patch('desktop.device_browser.QProcess',process))
        self.warning=self.enterContext(mock.patch('desktop.device_browser.QMessageBox.warning'))

    def remount(self):
        host=self.host;host._device_coordinator.disconnect(host.identity.path)
        replacement=DeviceSessionIdentity(host.identity.path,host.identity.mount_token,'replacement')
        host._device_coordinator.observe(replacement)
        return replacement

    def retired_reader(self, attribute='_stock_view_worker'):
        host=self.host;worker=OneShotTask(lambda:None)
        setattr(host,attribute,worker)
        host._device_coordinator.begin(DeviceCommand(host.identity,'inspect'),worker,Phase.INSPECTING)
        worker.finished.connect(lambda:MainWindow._finish_worker(host,attribute,worker))
        worker.start();self.assertTrue(worker.wait(5000))
        return worker

    def events_until(self, condition):
        deadline=time.monotonic()+3
        while not condition():
            self.app.processEvents()
            self.assertLess(time.monotonic(),deadline,'queued Eject did not retire')

    def test_idle_reader_already_emitted_finished_queues_at_real_retirement_once(self):
        worker=self.retired_reader();host=self.host
        host._eject(host.identity.path)
        self.assertEqual(len(worker._eject_resume_callbacks),1)
        self.assertEqual(self.processes,[])
        self.assertIsNotNone(host._stock_view_worker)
        self.events_until(lambda:bool(self.processes))
        self.assertIsNone(host._stock_view_worker)
        self.assertEqual(worker._eject_resume_callbacks,[])
        process=self.processes[0]
        self.assertEqual(process.calls,[('diskutil',['eject',host.identity.path])])
        self.assertEqual(host._device_coordinator.active.command.target,host.identity)
        process.finished.emit(0,0)
        host._retire_ejected_device.assert_called_once_with(host.identity.path)
        self.assertFalse(host._exporting);self.assertEqual(host._device_coordinator.eject_pending,'')
        self.warning.assert_not_called()

    def test_queued_same_path_remount_retires_request_without_ejecting_replacement(self):
        worker=self.retired_reader();host=self.host
        host._eject(host.identity.path);replacement=self.remount()
        self.events_until(lambda:host._eject_owner is None)
        self.assertEqual(worker._eject_resume_callbacks,[])
        self.assertEqual(self.processes,[])
        self.assertEqual(host._device_coordinator.mounts[host.identity.path],replacement)
        self.assertFalse(host._exporting);self.assertEqual(host._device_coordinator.eject_pending,'')
        host._retire_ejected_device.assert_not_called()
        self.assertIn('device changed',host._flash_status.call_args.args[0])

    def test_terminal_refresh_blocks_eject_but_completed_refresh_can_eject_normally(self):
        host=self.host;reader=SimpleNamespace(cancel=mock.Mock())
        host._stock_view_worker=reader
        for flag in ('_exporting','_device_finalization_refreshing'):
            with self.subTest(flag=flag):
                setattr(host,flag,True);host._eject(host.identity.path);setattr(host,flag,False)
        self.assertEqual(len(self.warning.call_args_list),2)
        self.assertFalse(hasattr(reader,'_eject_resume_callbacks'));reader.cancel.assert_not_called()
        self.assertEqual(host._device_coordinator.eject_pending,'')
        host._stock_view_worker=None;host._eject_ready_device=host.identity.path
        host._eject(host.identity.path);self.processes[0].finished.emit(0,0)
        host._retire_ejected_device.assert_called_once()
        self.assertEqual(host._eject_ready_device,'')

    def test_stale_process_success_preserves_replacement_and_never_reports_ejected(self):
        host=self.host;host._eject(host.identity.path);process=self.processes[0]
        replacement=self.remount();process.finished.emit(0,0)
        host._retire_ejected_device.assert_not_called()
        self.assertEqual(host._device_coordinator.mounts[host.identity.path],replacement)
        self.assertIsNone(host._device_coordinator.active)
        self.assertFalse(host._exporting);self.assertEqual(host._device_coordinator.eject_pending,'')
        self.assertNotIn('Ejected',str(host._flash_status.call_args_list))

    def test_old_process_cannot_retire_new_process_owner_or_clear_its_progress(self):
        host=self.host;host._eject(host.identity.path);old=self.processes[0]
        replacement=self.remount();host._device_coordinator.finished(old)
        owner=object();new=Process(host)
        host._eject_owner=owner;host._eject_session=replacement;host._eject_proc=new
        host._device_coordinator.eject_pending=replacement.path;host._eject_ready_device=replacement.path
        host._device_coordinator.begin(DeviceCommand(replacement,'eject'),new,Phase.EJECTING)
        host._stop_pulse.reset_mock();old.finished.emit(0,0)
        self.assertIs(host._eject_owner,owner);self.assertIs(host._eject_proc,new)
        self.assertIs(host._device_coordinator.active.worker,new)
        self.assertEqual(host._device_coordinator.eject_pending,replacement.path)
        self.assertEqual(host._eject_ready_device,replacement.path);self.assertTrue(host._exporting)
        host._stop_pulse.assert_not_called();host._retire_ejected_device.assert_not_called()
        self.assertEqual(old.deleted,1);self.assertEqual(new.deleted,0)

    def test_error_and_finished_share_one_terminal_failure(self):
        host=self.host;host._eject(host.identity.path);process=self.processes[0]
        process.errorOccurred.emit(1);process.finished.emit(1,0);process.finished.emit(0,0)
        self.warning.assert_called_once();self.assertIn('generated OS refusal',self.warning.call_args.args[2])
        host._retire_ejected_device.assert_not_called();self.assertEqual(process.deleted,1)
        self.assertIsNone(host._device_coordinator.active);self.assertFalse(host._exporting)

    def test_missing_mount_never_creates_authority_or_starts_os_process(self):
        host=self.host;host._device_coordinator.disconnect(host.identity.path)
        host._eject(host.identity.path)
        self.assertEqual(self.processes,[]);self.assertEqual(host._device_coordinator.mounts,{})
        self.assertEqual(host._device_coordinator.eject_pending,'');self.assertIsNone(host._eject_owner)

    def test_shutdown_retires_running_process_once_and_refuses_new_terminal_ui(self):
        host=self.host;host._eject(host.identity.path);process=self.processes[0]
        host._shutting_down=True;host._stop_pulse.reset_mock()
        process.errorOccurred.emit(1);process.finished.emit(0,0)
        host._eject(host.identity.path)
        self.assertEqual(len(self.processes),1);self.assertEqual(process.deleted,1)
        self.assertIsNone(host._device_coordinator.active);self.assertIsNone(host._eject_owner)
        self.assertEqual(host._device_coordinator.eject_pending,'');self.assertFalse(host._exporting)
        host._stop_pulse.assert_not_called();host._flash_status.assert_not_called()
        host._retire_ejected_device.assert_not_called();self.warning.assert_not_called()

    def test_failure_modal_return_preserves_a_newer_progress_owner(self):
        host=self.host;host._eject(host.identity.path);process=self.processes[0]
        owner=object()
        def newer(*_args):
            host._eject_owner=owner;host._exporting=True
        self.warning.side_effect=newer;process.errorOccurred.emit(1)
        self.assertIs(host._eject_owner,owner);self.assertTrue(host._exporting)
        host._set_status_text.assert_not_called()

    def test_exact_success_retirement_clears_witness_and_callbacks_after_surface_cleanup(self):
        host=self.host;dev=host.identity.path
        for name in ('_device_profiles','_device_mount_tokens','_drift_cache','_usb_device_rows',
                     '_usb_view_cache','_stock_view_cache','_stock_catalog_witness_cache',
                     '_stock_view_failures','_stock_view_pending','_dual_sync_queue'):
            setattr(host,name,{dev:object()})
        host.device_tree=SimpleNamespace(topLevelItemCount=lambda:0)
        host._usb_view_requests={dev};host._stock_view_warming={dev};host._current_device_path=dev
        host.stack=mock.Mock();host._set_device_poll_interval=mock.Mock()
        def retired(path, _message):
            self.assertNotIn(path,host._device_coordinator.mounts)
            self.assertNotIn(path,host._stock_catalog_witness_cache)
            self.assertEqual(host._current_device_path,'')
        host._discard_stock_view_callbacks=mock.Mock(side_effect=retired)
        with mock.patch('desktop.devices.forget_device_mount'):
            DeviceBrowserMixin._retire_ejected_device(host,dev)
        host._discard_stock_view_callbacks.assert_called_once()
