"""Exercise format confirmation and independent USB ownership without hardware."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from types import MethodType
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTreeWidget, QWidget

from desktop.app import MainWindow
from desktop.cdj import CdjDeviceMixin
from desktop.device_browser import DeviceBrowserMixin
from soundbox.device_operations import DeviceOperationCoordinator, DeviceCommand, DeviceSessionIdentity, Phase
from soundbox.library import Library
from soundbox.usb_format import FormatTarget


class FormatHost(CdjDeviceMixin, QWidget):
    _device_operation_owned = MainWindow._device_operation_owned
    _stock_has_sync_source = MainWindow._stock_has_sync_source
    _EJECT_BLOCKING_WORKERS = DeviceBrowserMixin._EJECT_BLOCKING_WORKERS

    def __init__(self, root):
        super().__init__()
        self.lib=None
        self._shutting_down=False
        self._exporting=False
        self._deleting=False
        self._stock_view_warming=set()
        self._stock_view_pending={}
        self._stock_view_cache={}
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

    def test_pending_library_refresh_never_captures_a_format_target(self):
        self.host._refresh_worker=object()
        self.host._reformat_usb('/fixture/USB')
        self.capture.assert_not_called()
        self.assertIsNone(self.host._usb_format_worker)
        self.assertFalse(self.host._device_operation_owned())

    def test_pending_native_ack_owner_never_starts_format_capture(self):
        self.host.tag_controller=SimpleNamespace(owned=True)
        self.host._reformat_usb('/fixture/USB')
        self.capture.assert_not_called();self.assertIsNone(self.host._usb_format_worker)

    def test_no_library_format_still_captures_saved_import_paths_and_refuses_malformed_records(self):
        self.host.imported={'sb-owned':dict(path='/fixture/USB/protected.wav')}
        self.dialog(QDialog.DialogCode.Rejected);self.inspect()
        self.assertEqual(self.capture.call_args.kwargs['sources'],[Path('/fixture/USB/protected.wav')])
        self.capture.reset_mock()
        for record in (None,{},dict(path=17),dict(path='relative.wav'),dict(path='/fixture/../foreign.wav')):
            with self.subTest(record=record):
                self.host.imported={'sb-owned':record}
                self.host._reformat_usb('/fixture/USB')
                self.capture.assert_not_called();self.assertIsNone(self.host._usb_format_worker)
                self.assertIn('locations need attention',self.host._flash_status.call_args.args[0])
        self.host.imported=[]
        self.host._reformat_usb('/fixture/USB');self.capture.assert_not_called()

    def test_owned_import_repair_folder_import_and_relink_block_capture_until_retirement(self):
        worker=mock.Mock();worker.isRunning.return_value=False
        for kind in ('location','import','relink'):
            with self.subTest(kind=kind):
                self.host._import_location_worker=worker if kind=='location' else None
                self.host._import_tasks=SimpleNamespace(pending=int(kind=='import'))
                self.host._relink_search={'worker':worker}
                self.host._oneshots=[worker] if kind=='relink' else []
                self.host._reformat_usb('/fixture/USB')
                self.capture.assert_not_called();self.assertIsNone(self.host._usb_format_worker)
        worker.isRunning.assert_not_called()
        # A remembered completed search is not an active path publisher.
        self.host._oneshots=[];self.host._import_location_worker=None;self.host._import_tasks.pending=0
        self.dialog(QDialog.DialogCode.Rejected);self.inspect()
        self.capture.assert_called_once();self.assertFalse(self.host._device_operation_owned())

    def test_native_refresh_waits_until_formatter_join_and_resumes_exact_path_on_every_outcome(self):
        for outcome in ('cancel','capture_error','complete','format_error'):
            with self.subTest(outcome=outcome):
                host=FormatHost(self.root);self.addCleanup(host.deleteLater)
                host._start_rekordbox_refresh=MethodType(MainWindow._start_rekordbox_refresh,host)
                host.tag_controller=SimpleNamespace(owned=False,recover_before_read=mock.Mock());host._refresh_worker=None
                host._state_maintenance=False;host._refresh_pending=False;host.native_reader=None
                host._repair_import_locations=mock.Mock()
                host._sync_rekordbox_connection=mock.Mock();host._sync_tags=mock.Mock()
                dialog=self.dialog(QDialog.DialogCode.Rejected if outcome=='cancel' else QDialog.DialogCode.Accepted)
                self.capture.side_effect=None;self.capture.return_value=self.target
                host._reformat_usb('/fixture/USB');capturing=host._usb_format_worker
                if outcome in ('complete','format_error'):
                    capturing.run();capturing.finished.emit()
                    worker=host._usb_format_worker
                    self.assertIsNot(worker,capturing)
                    self.assertTrue(host._usb_format_started)
                else:worker=capturing
                MainWindow._repair_import_locations(host)
                self.assertTrue(host._usb_format_import_repair_pending)
                host._repair_import_locations.assert_not_called()
                fake=SimpleNamespace(start=mock.Mock(),finished=mock.Mock())
                path=Path('/fixture/exact-deferred-master.db')
                with mock.patch('desktop.app.RefreshWorker',return_value=fake) as refresh, \
                        mock.patch('desktop.cdj.QTimer.singleShot') as timers:
                    host._start_rekordbox_refresh(path)
                    refresh.assert_not_called();timers.assert_not_called()
                    self.assertTrue(host._refresh_pending)
                    self.assertEqual(host._usb_format_refresh_path,path)
                    worker.result=(RuntimeError('owned failure') if outcome.endswith('error') else
                                   '/fixture/USB' if outcome=='complete' else self.target)
                    worker.finished.emit()
                    self.assertIsNone(host._usb_format_worker)
                    self.assertFalse(host._device_operation_owned())
                    refresh.assert_not_called()  # no start from the worker's result signal
                    repairs=[call.args[-1] for call in timers.call_args_list
                             if len(call.args)==3 and call.args[1] is host
                             and call.args[-1] is host._repair_import_locations]
                    self.assertEqual(len(repairs),1);repairs[0]()
                    host._repair_import_locations.assert_called_once()
                    self.assertFalse(host._usb_format_import_repair_pending)
                    resumes=[call.args[-1] for call in timers.call_args_list
                             if len(call.args)==3 and call.args[1] is host
                             and call.args[-1] is not host._repair_import_locations]
                    self.assertEqual(len(resumes),1)
                    if outcome=='complete':
                        # A newer source read can win the queued resume event.
                        # Keep the exact pending path for its follow-up read.
                        host._refresh_worker=object();resumes[0]()
                        refresh.assert_not_called();self.assertTrue(host._refresh_pending)
                        self.assertEqual(host._usb_format_refresh_path,path)
                        host._refresh_worker=None
                        host._rekordbox_database_path=lambda:Path('/fixture/different-default.db')
                        MainWindow._refresh_rekordbox(host)
                    else:resumes[0]()
                    refresh.assert_called_once_with(path);fake.start.assert_called_once()
                    self.assertIsNone(host._usb_format_refresh_path)
                    self.assertFalse(host._refresh_pending)
                    self.assertIs(host._refresh_worker,fake)
                    host._refresh_worker=None
                if outcome=='cancel':dialog.exec.assert_called_once()

    def test_scan_wait_does_not_rebind_accepted_format_to_a_new_source(self):
        callbacks=[]
        self.host._defer_device_operation_until_scan_finishes=lambda callback:callbacks.append(callback) or True
        self.host._reformat_usb('/fixture/USB')
        self.assertEqual(len(callbacks),1)
        self.host.lib=Library({},[],'/fixture/USB/master.db')
        callbacks.pop()()
        self.capture.assert_not_called();self.assertIsNone(self.host._usb_format_worker)
        self.assertIn('nothing erased',self.host._flash_status.call_args.args[0])

    def test_source_refresh_completion_before_capture_delivery_never_opens_confirmation(self):
        self.host._reformat_usb('/fixture/USB');worker=self.host._usb_format_worker
        worker.run()
        self.host.lib=Library({},[],'/fixture/USB/master.db')
        with mock.patch('desktop.usb_preferences.FormatUsbDialog') as dialog:
            worker.finished.emit()
        dialog.assert_not_called()
        self.assertIsNone(self.host._usb_format_worker)
        self.assertFalse(self.host._device_operation_owned())

    def test_source_refresh_during_native_capture_refuses_even_a_successful_target(self):
        def capture(*_args,**_kwargs):
            self.host.lib=Library({},[],'/fixture/USB/master.db')
            return self.target
        self.capture.side_effect=capture
        with mock.patch('desktop.usb_preferences.FormatUsbDialog') as dialog:
            worker=self.inspect()
        self.assertIsInstance(worker.result,RuntimeError)
        dialog.assert_not_called();self.assertFalse(self.host._device_operation_owned())

    def test_library_and_restore_changes_during_modal_confirmation_never_start_erase(self):
        for change in ('new_source','new_refresh','restore','paths'):
            with self.subTest(change=change):
                self.host.lib=Library({},[],'/fixture/source.xml')
                self.host._refresh_worker=None
                self.host._state_repository=SimpleNamespace(area=SimpleNamespace(generation=1))
                dialog=self.dialog(QDialog.DialogCode.Accepted)
                def changed():
                    if change=='new_source':self.host.lib=Library({},[],'/fixture/USB/master.db')
                    elif change=='new_refresh':self.host._refresh_worker=object()
                    elif change=='restore':self.host._state_repository.area.generation+=1
                    else:self.host.lib.source=Path('/fixture/USB/master.db')
                    return QDialog.DialogCode.Accepted
                dialog.exec.side_effect=changed
                with mock.patch('soundbox.usb_format.format_usb') as erase:
                    self.inspect()
                erase.assert_not_called();self.assertIsNone(self.host._usb_format_worker)
                self.assertFalse(self.host._device_operation_owned())

    def test_new_refresh_after_confirmation_refuses_worker_before_format_api(self):
        self.dialog(QDialog.DialogCode.Accepted);self.inspect()
        worker=self.host._usb_format_worker;self.host._refresh_worker=object()
        with mock.patch('soundbox.usb_format.format_usb') as erase:
            worker.run()
        erase.assert_not_called();self.assertIsInstance(worker.result,RuntimeError)
        worker.finished.emit();self.assertFalse(self.host._device_operation_owned())

    def test_source_change_during_final_disk_capture_refuses_the_real_erase_boundary(self):
        self.target=FormatTarget('/fixture/USB',(91,42),'fixture','disk99',(),128_000_000,('/fixture/USB',))
        self.capture.return_value=self.target
        self.dialog(QDialog.DialogCode.Accepted);self.inspect()
        worker=self.host._usb_format_worker
        def refreshed(*_args,**_kwargs):
            self.host.lib=Library({},[],'/fixture/USB/master.db')
            return self.target
        self.capture.side_effect=refreshed
        # Keep format_usb's own pre-erase capture and final cancellation checks
        # real; only the native target capture and irreversible erase are fake.
        with mock.patch('soundbox.usb_format._erase') as erase, \
                mock.patch('soundbox.cdj_export.plan_initialize') as initialize:
            worker.run()
        erase.assert_not_called();initialize.assert_not_called()
        self.assertIsInstance(worker.result,RuntimeError)
        self.assertIn('nothing erased',str(worker.result))
        worker.finished.emit();self.assertFalse(self.host._device_operation_owned())

    def test_scan_deferral_binds_only_requested_usb_and_rejects_its_own_remount(self):
        for changed in ('/fixture/OTHER','/fixture/USB'):
            with self.subTest(changed=changed):
                resume=mock.Mock();worker=SimpleNamespace(finished=mock.Mock())
                host=SimpleNamespace(_dev_scan_worker=worker,
                    _pending_device_operation=None,_device_mount_tokens={'/fixture/USB':1,'/fixture/OTHER':2},
                    _device_profiles={'/fixture/USB':SimpleNamespace(kind='generic_usb')},
                    _device_coordinator=DeviceOperationCoordinator(),
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
        self.host.lib=Library({}, [], '/synthetic/library.xml')
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
