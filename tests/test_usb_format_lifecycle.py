"""Exercise format confirmation and independent USB ownership without hardware."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTreeWidget, QWidget

from desktop.app import MainWindow
from desktop.cdj import CdjDeviceMixin
from desktop.device_browser import DeviceBrowserMixin
from soundbox.device_operations import DeviceOperationCoordinator, DeviceCommand, DeviceSessionIdentity, Phase


class FormatHost(CdjDeviceMixin, QWidget):
    _device_operation_owned = MainWindow._device_operation_owned
    _EJECT_BLOCKING_WORKERS = DeviceBrowserMixin._EJECT_BLOCKING_WORKERS

    def __init__(self, root):
        super().__init__()
        self.lib=None
        self._shutting_down=False
        self._exporting=False
        self._deleting=False
        self._stock_view_warming=set()
        self._stock_view_pending={}
        self._pending_device_operation=None
        self._device_coordinator=DeviceOperationCoordinator()
        self._usb_format_worker=None
        self._usb_format_device=''
        self._device_mount_refresh_pending=False
        self._device_profiles={'/fixture/USB':SimpleNamespace(kind='generic_usb')}
        self._device_mount_tokens={'/fixture/USB':91}
        self._usb_device_rows={}
        self._usb_view_cache={}
        self._usb_view_requests=set()
        self._current_device_path=''
        self.stack=mock.Mock()
        self.settings={}
        self._data_file=root/'library.json'
        self.device_tree=QTreeWidget(self)
        self.cancel_btn=QWidget(self)
        self._start_pulse=mock.Mock()
        self._stop_pulse=mock.Mock()
        self._flash_status=mock.Mock()
        self._update_device_action_state=mock.Mock()
        self._sync_cue_editing=mock.Mock()
        self._refresh_devices=mock.Mock()
        self._release_eject_ready=mock.Mock()
        self._defer_device_operation_until_scan_finishes=lambda _:False


class FormatLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name)
        self.host=FormatHost(self.root);self.addCleanup(self.host.deleteLater)
        self.enterContext(mock.patch('desktop.cdj.device_facts',return_value=SimpleNamespace(volume_uuid='fixture')))
        self.enterContext(mock.patch('desktop.cdj.CdjWorker.start'))
        self.enterContext(mock.patch('desktop.cdj.QMessageBox.warning'))
        self.enterContext(mock.patch('desktop.cdj.QTimer.singleShot'))
        self.target=SimpleNamespace(volumes=('/fixture/USB','/fixture/SIBLING'))
        self.capture=self.enterContext(mock.patch('soundbox.usb_format.capture',return_value=self.target))

    def inspect(self):
        self.host._reformat_usb('/fixture/USB')
        worker=self.host._usb_format_worker
        worker.run();worker.finished.emit()
        return worker

    def dialog(self, outcome, *, error=None):
        dialog=mock.Mock(spec=QDialog)
        dialog.choice=('FIXTURE','ExFAT')
        dialog.exec=mock.Mock(return_value=outcome,side_effect=error)
        # No instance Accepted member: production must use QDialog.DialogCode.
        self.enterContext(mock.patch('desktop.usb_preferences.FormatUsbDialog',return_value=dialog))
        return dialog

    def test_confirmation_starts_erase_and_other_usb_retains_export_lane(self):
        dialog=self.dialog(QDialog.DialogCode.Accepted)
        inspected=self.inspect()
        worker=self.host._usb_format_worker
        self.assertIsNot(worker,inspected)
        self.assertFalse(self.host._exporting)
        self.assertTrue(self.host._device_operation_owned())
        self.assertTrue(self.host._device_operation_owned('/fixture/USB'))
        self.assertTrue(self.host._device_operation_owned('/fixture/SIBLING'))
        self.assertFalse(self.host._device_operation_owned('/fixture/OTHER'))
        self.assertFalse(self.host._device_operation_owned(''))
        dialog.deleteLater.assert_called_once()
        plan=SimpleNamespace(close=mock.Mock())
        with mock.patch('soundbox.usb_format.format_usb',return_value=dict(MountPoint=str(self.root),
                VolumeUUID='new',VolumeName='FIXTURE')) as erase, \
                mock.patch('soundbox.cdj_export.plan_initialize',return_value=plan), \
                mock.patch('soundbox.cdj_transaction.commit') as commit:
            worker.run()
        self.assertEqual(erase.call_args.args,(self.target,'FIXTURE','ExFAT'))
        self.assertIn('should_cancel',erase.call_args.kwargs)
        commit.assert_called_once_with(plan);plan.close.assert_called_once()
        # An unrelated export can own its ordinary QThread throughout format
        # completion. Format retirement must never clear or cancel that owner.
        identity=DeviceSessionIdentity('/fixture/OTHER',17)
        self.host._device_coordinator.observe(identity)
        exporter=object();self.host._cdj_worker=exporter;self.host._exporting=True
        self.host._device_coordinator.begin(DeviceCommand(identity,'commit-cdj'),exporter,Phase.EXECUTING)
        self.host._stop_pulse.reset_mock()
        worker.finished.emit()
        self.assertEqual(self.host._usb_format_device,'')
        self.assertIsNone(self.host._usb_format_worker)
        self.assertIs(self.host._device_coordinator.active.worker,exporter)
        self.assertIs(self.host._cdj_worker,exporter)
        self.assertTrue(self.host._exporting)
        self.host._stop_pulse.assert_not_called()

    def test_rejected_confirmation_releases_all_ownership_without_erase(self):
        self.dialog(QDialog.DialogCode.Rejected)
        with mock.patch('soundbox.usb_format.format_usb') as erase:
            self.inspect()
        erase.assert_not_called()
        self.assertEqual(self.host._usb_format_device,'')
        self.assertIsNone(self.host._usb_format_worker)
        self.assertFalse(self.host._device_operation_owned())

    def test_dialog_exception_cannot_leave_format_busy(self):
        self.dialog(None,error=RuntimeError('dialog failure'))
        self.inspect()
        self.assertFalse(self.host._device_operation_owned())
        self.assertIsNone(self.host._usb_format_worker)
        self.host._flash_status.assert_called_with('USB format stopped',15)

    def test_initial_capture_blocks_all_targets_until_whole_disk_volumes_are_known(self):
        self.host._reformat_usb('/fixture/USB')
        self.assertTrue(self.host._device_operation_owned('/fixture/OTHER'))
        worker=self.host._usb_format_worker
        worker.result=ValueError('capture failed');worker.finished.emit()
        self.assertFalse(self.host._device_operation_owned())

    def test_shutdown_joins_retiring_formatter_without_starting_initialization(self):
        self.dialog(QDialog.DialogCode.Accepted)
        self.host._reformat_usb('/fixture/USB')
        worker=self.host._usb_format_worker
        worker.run();self.host._shutting_down=True
        worker.finished.emit()
        self.assertIsNone(self.host._usb_format_worker)
        self.assertEqual(self.host._usb_format_device,'')

    def test_scan_deferral_binds_only_requested_usb_and_rejects_its_own_remount(self):
        for changed in ('/fixture/OTHER','/fixture/USB'):
            with self.subTest(changed=changed):
                resume=mock.Mock();worker=SimpleNamespace(finished=mock.Mock())
                host=SimpleNamespace(_dev_scan_worker=worker,
                    _pending_device_operation=None,_device_mount_tokens={'/fixture/USB':1,'/fixture/OTHER':2},
                    _device_profiles={'/fixture/USB':SimpleNamespace(kind='generic_usb')},
                    _device_coordinator=SimpleNamespace(eject_pending=False),
                    _shutting_down=False,progress=mock.Mock(),cancel_btn=mock.Mock(),
                    _start_pulse=mock.Mock(),_stop_pulse=mock.Mock())
                self.assertTrue(MainWindow._defer_device_operation_until_scan_finishes(host,resume,target='/fixture/USB'))
                worker.finished.connect.assert_not_called()
                host._device_mount_tokens[changed]+=10
                host._dev_scan_worker=None
                worker._operation_resume_callbacks[0](True)
                self.assertEqual(resume.call_count,int(changed!='/fixture/USB'))
                self.assertIsNone(host._pending_device_operation)
                self.assertFalse(host._exporting)

    def test_export_control_stays_enabled_for_other_usb_during_format(self):
        self.host._usb_format_device='/fixture/USB'
        self.host._usb_format_volumes=('/fixture/USB',)
        self.host._usb_format_started=True
        self.host._device_profiles['/fixture/OTHER']=SimpleNamespace(kind='generic_usb')
        self.host.device_sync_btn=QPushButton(self.host)
        self.host.eject_btn=QPushButton(self.host)
        self.host._current_device_path='/fixture/OTHER'
        MainWindow._update_device_action_state(self.host)
        self.assertTrue(self.host.device_sync_btn.isEnabled())
        self.assertFalse(self.host.eject_btn.isEnabled())
        self.host._current_device_path='/fixture/USB'
        MainWindow._update_device_action_state(self.host)
        self.assertFalse(self.host.device_sync_btn.isEnabled())
        self.host._current_device_path='/fixture/OTHER'
        self.host._device_profiles['/fixture/OTHER']=SimpleNamespace(kind='stock_ipod')
        MainWindow._update_device_action_state(self.host)
        self.assertFalse(self.host.device_sync_btn.isEnabled())


if __name__=='__main__':unittest.main()
