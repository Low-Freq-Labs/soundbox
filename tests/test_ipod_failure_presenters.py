"""Actual iPod failure presenters bound text without inventing transaction outcomes."""
from pathlib import Path
from types import MethodType,SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.app import MainWindow
from desktop.export_dialog import DeviceRequestNotStarted,ExportSelectionMixin
from desktop.stock_catalog_sync import _failed
from soundbox.operation_control import CommittedOperationError,OperationCancelled
from soundbox.stock_ipod import StockIpodError
from tests import test_quiet_device_exports as quiet_fixture
from tests import test_device_finalization_ownership as finalization_fixture


class IpodFailurePresenters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def owner(self):
        fixture=quiet_fixture.QuietDeviceExports()
        self.addCleanup(fixture.doCleanups)
        owner,target=fixture.generated()[:2]
        owner._set_status_text=mock.Mock();owner._update_device_action_state=mock.Mock()
        return owner,target

    def large_error(self,kind=RuntimeError):
        return kind('Cannot read selected song\n'+('Original diagnostic detail\n'*3000)+'END OF FULL DIAGNOSTIC')

    def check(self,text,logged):
        self.assertIn('Cannot read selected song',text)
        self.assertLess(len(text),1600)
        self.assertIn('Additional failure details',text)
        self.assertIn('END OF FULL DIAGNOSTIC','\n'.join(logged.output))

    def test_stock_export_and_delete_plan_errors_are_bounded_and_honestly_read_only(self):
        for kind in ('stock','delete','dual'):
            with self.subTest(kind=kind):
                owner,target=self.owner();error=self.large_error()
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     self.assertLogs('soundbox',level='ERROR') as logged:
                    if kind=='delete':MainWindow._on_stock_playlist_delete_plan(owner,str(target),((1,'Favorites'),),error)
                    elif kind=='dual':MainWindow._on_dual_ipod_plan(owner,str(target),error)
                    else:MainWindow._on_stock_ipod_plan(owner,str(target),error)
                warning.assert_called_once()
                text=warning.call_args.args[2];self.check(text,logged)
                self.assertIn('No device files were changed',text)
                self.assertNotIn('attempted',text)

    def test_stock_export_and_delete_commit_errors_keep_explicit_outcome_visible(self):
        for deleting in (False,True):
            for outcome in ('unknown','rolled back','recovery required','committed'):
                with self.subTest(deleting=deleting,outcome=outcome):
                    owner,target=self.owner()
                    error=self.large_error(CommittedOperationError if outcome=='committed' else StockIpodError)
                    if outcome in {'rolled back','recovery required'}:error.recovery_status=outcome
                    with mock.patch.object(QMessageBox,'critical') as critical, \
                         self.assertLogs('soundbox',level='ERROR') as logged:
                        method=(MainWindow._on_stock_playlist_delete_commit if deleting else MainWindow._on_stock_ipod_commit)
                        method(owner,str(target),error)
                    text=critical.call_args.args[2];self.check(text,logged)
                    expected={'unknown':'could not be established','rolled back':'rollback completed',
                        'recovery required':'rollback was incomplete','committed':'were committed'}[outcome]
                    self.assertIn(expected,text)
                    self.assertNotIn('No device files were changed',text)
                    self.assertNotIn('attempted',text)

    def test_dual_failure_bounds_reason_and_keeps_recovery_warning(self):
        owner,_target=self.owner();error=self.large_error();error.recovery_status='recovery required'
        error.recovery_directory='/generated/current-recovery'
        with mock.patch.object(QMessageBox,'critical') as critical, \
             self.assertLogs('soundbox',level='ERROR') as logged:
            MainWindow._show_dual_ipod_failure(owner,error)
        text=critical.call_args.args[2];self.check(text,logged)
        self.assertIn('Keep the recovery files',text)
        self.assertIn('/generated/current-recovery','\n'.join(logged.output))

    def test_composite_device_only_preflight_errors_are_bounded_without_changing_admission(self):
        for deleting in (False,True):
            with self.subTest(deleting=deleting):
                owner,target=self.owner();error=self.large_error()
                owner._current_device_path=str(target)
                owner._device_profiles={str(target):SimpleNamespace(kind='dual_boot')}
                owner._cached_composite_manifest=mock.Mock(side_effect=error)
                owner._refresh_device_surfaces=mock.Mock();owner._begin_dual_ipod_sync=mock.Mock()
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     self.assertLogs('soundbox',level='ERROR') as logged:
                    if deleting:MainWindow._begin_composite_playlist_delete(owner,str(target),('selected',))
                    else:MainWindow._apply_composite_playlist_order(owner,('selected',))
                self.check(warning.call_args.args[2],logged)
                self.assertIn('No device files were changed',warning.call_args.args[2])
                owner._begin_dual_ipod_sync.assert_not_called()

    def test_delete_cancellation_is_quiet_only_when_commit_and_recovery_are_safe(self):
        for outcome in ('rolled back','recovery required','committed'):
            with self.subTest(outcome=outcome):
                owner,target=self.owner();error=OperationCancelled('Generated deletion cancellation')
                if outcome=='committed':error.committed=True
                else:error.recovery_status=outcome
                with mock.patch.object(QMessageBox,'critical') as critical, \
                     mock.patch.object(QMessageBox,'warning') as warning, \
                     mock.patch.object(QMessageBox,'information') as information:
                    if outcome=='rolled back':
                        MainWindow._on_stock_playlist_delete_commit(owner,str(target),error)
                        critical.assert_not_called()
                        self.assertIn('cancelled',owner._flash_status.call_args.args[0])
                    else:
                        with self.assertLogs('soundbox',level='ERROR'):
                            MainWindow._on_stock_playlist_delete_commit(owner,str(target),error)
                        critical.assert_called_once()
                        expected='were committed' if outcome=='committed' else 'recovery needs review'
                        self.assertIn(expected,critical.call_args.args[2])
                        owner._flash_status.assert_not_called()
                warning.assert_not_called();information.assert_not_called()

    def test_catalog_failure_is_bounded_and_committed_batch_retains_typed_truth(self):
        owner,target=self.owner();owner._device_coordinator=SimpleNamespace(active=None)
        error=self.large_error(CommittedOperationError)
        request=SimpleNamespace(batch=None,device=str(target))
        with mock.patch.object(QMessageBox,'warning') as warning, \
             self.assertLogs('soundbox',level='ERROR') as logged:
            _failed(owner,request,error,committed=True)
        text=warning.call_args.args[2];self.check(text,logged)
        self.assertIn('were committed',text)
        target_row=dict(path=str(target),name='iPod',kind='stock_ipod')
        batch=dict(active=target_row)
        owner._export_batch=batch
        request=SimpleNamespace(batch=batch,batch_target=target_row,device=str(target))
        _failed(owner,request,error,committed=True)
        delivered=owner._export_target_result.call_args.args[1]
        self.assertIs(delivered,error)
        self.assertTrue(delivered.committed)

    def test_ipod_batch_keeps_committed_and_recovery_truth_and_only_safe_cancel_quiet(self):
        for outcome in ('committed','recovery required','rolled back'):
            with self.subTest(outcome=outcome):
                owner,target=self.owner()
                error=self.large_error(CommittedOperationError if outcome=='committed' else OperationCancelled)
                if outcome!='committed':error.recovery_status=outcome
                batch=dict(active=dict(path=str(target),name='iPod',kind='stock_ipod'),cancelled=False,pending=[])
                owner._export_batch=batch
                if outcome=='rolled back':
                    with mock.patch('desktop.export_dialog.device_failure_text') as formatter:
                        ExportSelectionMixin._export_target_result(owner,str(target),error)
                    formatter.assert_not_called()
                else:
                    with self.assertLogs('soundbox',level='ERROR') as logged:
                        ExportSelectionMixin._export_target_result(owner,str(target),error)
                    self.assertIn('END OF FULL DIAGNOSTIC','\n'.join(logged.output))
                expected={'committed':'attention','recovery required':'failed','rolled back':'cancelled'}[outcome]
                self.assertEqual(batch['outcome'],expected)
                full=batch.get('note') or batch.get('error')
                self.assertIn('END OF FULL DIAGNOSTIC',full)
                batch.update(active=None,results=[('iPod',full)],outcomes=[expected])
                owner._device_operation_owned=lambda *_:False
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     mock.patch.object(QMessageBox,'information') as information:
                    ExportSelectionMixin._start_next_export_target(owner)
                information.assert_not_called()
                if outcome=='rolled back':warning.assert_not_called()
                else:
                    warning.assert_called_once();body=warning.call_args.args[2]
                    self.assertLess(len(body),1600)
                    self.assertIn('were committed' if outcome=='committed' else 'rollback was incomplete',body)

    def test_batch_preserves_unstarted_reason_without_hiding_unknown_backend_outcome(self):
        reason='A different device operation started; retry the waiting Sync'
        for kind in (DeviceRequestNotStarted,RuntimeError,StockIpodError):
            with self.subTest(kind=kind.__name__):
                owner,target=self.owner();error=kind(reason)
                batch=dict(active=dict(path=str(target),name='iPod',kind='stock_ipod'),
                    cancelled=False,pending=[])
                owner._export_batch=batch
                with self.assertLogs('soundbox',level='ERROR') as logged:
                    ExportSelectionMixin._export_target_result(owner,str(target),error)
                self.assertEqual(batch['outcome'],'failed')
                if kind is DeviceRequestNotStarted:
                    self.assertEqual(error.__dict__['recovery_status'],'not needed')
                    self.assertEqual(batch['error'],reason)
                    self.assertIn('No device files were changed','\n'.join(logged.output))
                else:
                    self.assertEqual(batch['error'],
                        'Device recovery status could not be established. '
                        'Check the device status before retrying.\n\n'+reason)
                    self.assertNotIn('No device files were changed','\n'.join(logged.output))

    def test_unstarted_type_cannot_hide_later_commit_or_recovery_evidence(self):
        for committed in (False,True):
            with self.subTest(committed=committed):
                owner,target=self.owner();error=DeviceRequestNotStarted('Explicit outcome evidence')
                if committed:error.committed=True
                else:error.recovery_status='recovery required'
                batch=dict(active=dict(path=str(target),name='iPod',kind='stock_ipod'),
                    cancelled=False,pending=[])
                owner._export_batch=batch
                with self.assertLogs('soundbox',level='ERROR'):
                    ExportSelectionMixin._export_target_result(owner,str(target),error)
                self.assertEqual(batch['outcome'],'attention' if committed else 'failed')
                self.assertIn('were committed' if committed else 'rollback was incomplete',
                    batch.get('note') or batch['error'])

    def test_owned_export_plan_commit_and_dual_failure_present_one_batch_modal(self):
        for phase in ('stock-plan','stock-commit','dual-commit'):
            with self.subTest(phase=phase):
                owner,target=self.owner();error=self.large_error()
                dev=str(target)
                batch=dict(active=dict(path=dev,name='iPod',kind='dual_boot' if phase.startswith('dual') else 'stock_ipod'),
                    pending=[],cancelled=False,outcomes=[],results=[])
                owner._export_batch=batch
                owner._export_target_result=MethodType(ExportSelectionMixin._export_target_result,owner)
                owner._show_dual_ipod_failure=MethodType(MainWindow._show_dual_ipod_failure,owner)
                owner._device_operation_owned=lambda *_:False
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     mock.patch.object(QMessageBox,'critical') as critical, \
                     mock.patch.object(QMessageBox,'information') as information, \
                     self.assertLogs('soundbox',level='ERROR'):
                    method={'stock-plan':MainWindow._on_stock_ipod_plan,
                        'stock-commit':MainWindow._on_stock_ipod_commit,
                        'dual-commit':MainWindow._on_dual_ipod_commit}[phase]
                    method(owner,dev,error)
                    warning.assert_not_called();critical.assert_not_called()
                    batch.update(active=None,outcomes=[batch['outcome']],results=[('iPod',batch['error'])])
                    ExportSelectionMixin._start_next_export_target(owner)
                warning.assert_called_once();critical.assert_not_called();information.assert_not_called()
                self.assertLess(len(warning.call_args.args[2]),1600)
                self.assertIn('END OF FULL DIAGNOSTIC',owner._last_device_failure['diagnostic'])
                self.assertIn('END OF FULL DIAGNOSTIC',owner._last_export_batch_results[0][1])

    def test_actual_final_refresh_errors_are_bounded_after_join_and_batch_shows_only_once(self):
        from soundbox.device_operations import DeviceSessionIdentity
        for multi in (False,True):
            for batched in (False,True):
                with self.subTest(multi=multi,batched=batched):
                    case=finalization_fixture.DeviceFinalizationOwnership();case.app=self.app;case.setUp()
                    self.addCleanup(case.doCleanups)
                    owner=case.host;completed=mock.Mock();error=self.large_error()
                    devices=[case.dev]
                    if multi:
                        second=Path(case.area)/'SECOND-IPOD';second.mkdir();devices.append(str(second))
                        identity=DeviceSessionIdentity(str(second),second.stat().st_dev)
                        owner._device_coordinator.observe(identity)
                        owner._device_profiles[str(second)]=case.profile
                        owner._device_mount_tokens[str(second)]=identity.mount_token
                    if batched:
                        batch=dict(active=dict(path=case.dev,name='iPod',kind='stock_ipod'),pending=[],
                            cancelled=False,outcomes=[],results=[])
                        owner._export_batch=batch
                        owner._export_target_result=MethodType(ExportSelectionMixin._export_target_result,owner)
                    with mock.patch('soundbox.stock_ipod_info.read_stock_ipod_snapshot',side_effect=error), \
                         self.assertLogs('soundbox',level='ERROR') as logged:
                        if multi:owner._finalize_device_operations(devices,completed)
                        else:owner._finalize_ipod_operation(case.dev,completed)
                        case.wait(case.entered.is_set)
                        case.warning.assert_not_called()
                        case.release.set()
                        case.wait(lambda:owner._device_finalization_token is None)
                        if batched:
                            case.warning.assert_not_called()
                            self.assertEqual(batch['outcome'],'attention')
                            batch.update(active=None,outcomes=['attention'],results=[('iPod',batch['note'])])
                            ExportSelectionMixin._start_next_export_target(owner)
                    case.warning.assert_called_once();completed.assert_not_called()
                    body=case.warning.call_args.args[2]
                    self.assertLess(len(body),1600)
                    self.assertIn('were committed',body)
                    self.assertIn('reconnect before another write',body)
                    self.assertIn('END OF FULL DIAGNOSTIC','\n'.join(logged.output))
                    self.assertTrue(owner._device_coordinator.last_result.committed)
                    self.assertEqual(owner._device_coordinator.last_result.refresh,'failed')
                    self.assertFalse(owner._exporting);self.assertFalse(owner._device_finalization_refreshing)
                    self.assertIsNone(owner._stock_view_worker)


if __name__=='__main__':
    unittest.main()
