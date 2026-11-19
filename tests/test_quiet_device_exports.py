"""Explicit Export/Sync proceeds without a second review or routine success modal."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.app import MainWindow
from desktop.export_dialog import ExportSelectionMixin
from soundbox.stock_ipod import StockIpodCommitResult, StockMediaOmission
from tests import test_ipod_export_entries as entries
from tests import test_ipod_artwork_ui as artwork


class QuietDeviceExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def generated(self):
        area=self.enterContext(TemporaryDirectory())
        target=Path(area).resolve()
        helper=entries.IpodExportEntries()
        owner,_playlist,capture=helper.generated()
        stock,dual=helper.generated_plan(target,changed=True)
        owner._stop_pulse=mock.Mock()
        owner._flash_status=mock.Mock()
        owner._export_target_result=mock.Mock()
        owner._set_status_count=mock.Mock()
        owner._publish_ipod_export_witness=mock.Mock()
        owner._dual_active_preconfirmed=False
        owner._dual_active_confirmed=False
        owner._dual_active_action='sync'
        owner._dual_active_device=str(target)
        owner._dual_active_playlist_keys=set()
        owner._dual_active_track_total=0
        owner._dual_sync_queue={}
        owner._reset_dual_batch=mock.Mock()
        owner._dual_plan_worker=None
        owner._dual_commit_worker=None
        owner._device_completion_message=MethodType(MainWindow._device_completion_message,owner)
        owner._commit_stock_ipod_plan=mock.Mock()
        owner._commit_dual_ipod_plan=mock.Mock()
        owner._finalize_ipod_operation=mock.Mock()
        owner._refresh_devices=mock.Mock()
        owner.progress.value.return_value=0
        return owner,target,stock,dual,capture

    def test_valid_stock_and_dual_plan_admit_commit_without_review_and_keep_capture(self):
        for kind in ('stock','dual'):
            with self.subTest(kind=kind):
                owner,target,stock,dual,capture=self.generated()
                plan=stock if kind=='stock' else dual
                with mock.patch.object(QMessageBox,'question',side_effect=AssertionError('Second export review')):
                    getattr(MainWindow,f'_on_{kind}_ipod_plan')(owner,str(target),plan,_export_capture=capture)
                capture.require_current.assert_called_once_with(owner,str(target))
                getattr(owner,f'_commit_{kind}_ipod_plan').assert_called_once_with(
                    str(target),plan,_export_capture=capture)

    def test_stock_completion_is_quiet_after_finalization_and_keeps_included_skipped_counts(self):
        owner,target,stock,_dual,_capture=self.generated()
        omissions=tuple(StockMediaOmission(str(i),f'PCM24 song {i}',Path('/generated')/f'{i}.wav',
            '24-bit WAVE is outside this iPod export profile') for i in range(55))
        stock=replace(stock,source_track_count=156,omissions=omissions)
        result=StockIpodCommitResult(target/'.recovery',156,0,0,0,('Favorites',),'a'*64)
        with mock.patch.object(QMessageBox,'information') as info, mock.patch.object(QMessageBox,'warning') as warning:
            MainWindow._on_stock_ipod_commit(owner,str(target),result,plan=stock)
            self.assertFalse(hasattr(owner,'_last_device_completion'))
            owner._finalize_ipod_operation.call_args.args[1]()
        info.assert_not_called();warning.assert_not_called()
        self.assertIn('156 songs',owner._flash_status.call_args.args[0])
        self.assertIn('55 skipped',owner._flash_status.call_args.args[0])
        self.assertIn('PCM24 song 0',owner._last_device_completion['text'])
        self.assertFalse(owner._last_device_completion['attention'])

    def test_stock_noop_finalizes_quietly_with_omission_summary(self):
        owner,target,stock,_dual,_capture=self.generated()
        stock=replace(stock,rendered_database=b'generated database',source_track_count=1,
            omissions=(StockMediaOmission('missing','Skipped song',Path('/generated/skipped.wav'),'PCM24'),))
        with mock.patch.object(QMessageBox,'information') as info:
            MainWindow._on_stock_ipod_plan(owner,str(target),stock)
            owner._commit_stock_ipod_plan.assert_not_called()
            self.assertFalse(hasattr(owner,'_last_device_completion'))
            owner._finalize_ipod_operation.call_args.args[1]()
        info.assert_not_called()
        self.assertIn('1 skipped',owner._flash_status.call_args.args[0])

    def test_stock_committed_art_shadow_cache_and_cleanup_warnings_remain_visible(self):
        for field in ('artwork_warning','shadow_warning','catalog_warning','cleanup_failures'):
            with self.subTest(field=field):
                owner,target,stock,_dual,_capture=self.generated()
                result=StockIpodCommitResult(target/'.recovery',1,0,0,0,('Favorites',),'a'*64)
                value=('Generated committed warning',) if field=='cleanup_failures' else 'Generated committed warning'
                result=replace(result,**{field:value})
                with mock.patch.object(QMessageBox,'warning') as warning, mock.patch.object(QMessageBox,'information') as info:
                    MainWindow._on_stock_ipod_commit(owner,str(target),result,plan=stock)
                    owner._finalize_ipod_operation.call_args.args[1]()
                warning.assert_called_once();info.assert_not_called()
                self.assertIn('Generated committed warning',warning.call_args.args[2])
                self.assertTrue(owner._last_device_completion['attention'])
                self.assertIn('needs attention',owner._flash_status.call_args.args[0])

    def test_optional_catalog_association_notice_is_quiet_for_full_and_noop_exports(self):
        notice=('Some exported playlists retain folder labels or lack a native UUID; '
                'their optional catalog association was not saved. Full Sync remains available.')
        for noop in (False,True):
            with self.subTest(noop=noop):
                owner,target,stock,_dual,_capture=self.generated()
                result=StockIpodCommitResult(target/'.recovery',31,0,2,0,('Folder/DNB',),'a'*64,
                    catalog_notice=notice)
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     mock.patch.object(QMessageBox,'information') as info:
                    if noop:
                        stock=replace(stock,rendered_database=b'generated database',catalog_notice=notice)
                        MainWindow._on_stock_ipod_plan(owner,str(target),(stock,result))
                    else:MainWindow._on_stock_ipod_commit(owner,str(target),result,plan=stock)
                    owner._finalize_ipod_operation.call_args.args[1]()
                warning.assert_not_called();info.assert_not_called()
                self.assertFalse(owner._last_device_completion['attention'])
                self.assertEqual(owner._last_device_completion['issues'],())
                self.assertIn(notice,owner._last_device_completion['text'])

    def test_real_warning_popup_contains_only_failure_details_not_success_report(self):
        owner,target,stock,_dual,_capture=self.generated()
        stock=replace(stock,source_track_count=156,omissions=(
            StockMediaOmission('skipped','Long PCM24 song',Path('/generated/long.wav'),'PCM24'),))
        issue='The comparison cache could not be saved: permission denied'
        result=StockIpodCommitResult(target/'.recovery',31,0,2,0,('Favorites',),'a'*64,
            catalog_warning=issue)
        with mock.patch.object(QMessageBox,'warning') as warning:
            MainWindow._on_stock_ipod_commit(owner,str(target),result,plan=stock)
            owner._finalize_ipod_operation.call_args.args[1]()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1],'iPod needs attention')
        self.assertEqual(warning.call_args.args[2],issue)
        for routine in ('Copied 31','Long PCM24 song','Recovery files','Eject from SoundBox'):
            self.assertNotIn(routine,warning.call_args.args[2])
            self.assertIn(routine,owner._last_device_completion['text'])
        self.assertEqual(owner._last_device_completion['issues'],(issue,))

    def test_dual_completion_keeps_optional_notes_quiet_and_real_cleanup_concise(self):
        from soundbox.dual_ipod import DualIpodCommitResult
        for failed in (False,True):
            with self.subTest(failed=failed):
                owner,target,_stock,dual,_capture=self.generated()
                owner.rockbox_media_cache=SimpleNamespace(clear=mock.Mock())
                notice='Optional folder association was not saved; full Sync is available'
                stock=StockIpodCommitResult(target/'.recovery',31,0,2,0,('DNB',),'a'*64,
                    catalog_notice=notice)
                result=DualIpodCommitResult(stock,2,3,0,0,1,'b'*64,
                    cleanup_failures=('Unindexed song could not be removed',) if failed else ())
                with mock.patch.object(QMessageBox,'warning') as warning, \
                     mock.patch.object(QMessageBox,'information') as info:
                    MainWindow._on_dual_ipod_commit(owner,str(target),result,dual)
                    owner._finalize_ipod_operation.call_args.args[1]()
                info.assert_not_called()
                self.assertIn(notice,owner._last_device_completion['text'])
                self.assertEqual(owner._last_device_completion['attention'],failed)
                if failed:
                    warning.assert_called_once()
                    self.assertIn('Some device files could not be removed',warning.call_args.args[2])
                    self.assertIn('Unindexed song could not be removed',warning.call_args.args[2])
                    self.assertNotIn(notice,warning.call_args.args[2])
                    self.assertNotIn('iTunes: copied',warning.call_args.args[2])
                else:warning.assert_not_called()

    def test_attention_body_bounds_failure_lists_and_keeps_unabridged_diagnostics(self):
        owner,_target,_stock,_dual,_capture=self.generated()
        issues=('Could not save the iPod database\n'+'\n'.join('Failed path '+str(i) for i in range(100)),
                'Cleanup could not remove '+('x'*2000),'Third failure','Fourth failure')
        with mock.patch.object(QMessageBox,'warning') as warning, self.assertLogs('soundbox',level='WARNING') as logged:
            owner._device_completion_message('iPod Sync complete','Routine export statistics',
                attention=True,issues=issues)
        text=warning.call_args.args[2]
        self.assertIn('Could not save the iPod database',text)
        self.assertIn('Additional failure details',text)
        self.assertLess(len(text),1600)
        self.assertNotIn('Routine export statistics',text)
        self.assertEqual(owner._last_device_completion['issues'],issues)
        self.assertIn('Fourth failure','\n'.join(logged.output))

    def test_batch_warning_excludes_completed_target_reports_but_retains_outcomes(self):
        owner,_target,_stock,_dual,_capture=self.generated()
        results=[('Healthy USB','Copied 100 songs; recovery files retained'),
                 ('Failed iPod','Database readback failed'),('Other USB','Cleanup failed')]
        owner._export_batch=dict(active=None,pending=[],cancelled=False,
            outcomes=['complete','failed','attention'],results=results)
        owner._device_operation_owned=lambda *_:False
        owner._update_device_action_state=mock.Mock()
        with mock.patch.object(QMessageBox,'warning') as warning, \
             mock.patch.object(QMessageBox,'information') as information:
            ExportSelectionMixin._start_next_export_target(owner)
        warning.assert_called_once();information.assert_not_called()
        self.assertIn('Failed iPod: Database readback failed',warning.call_args.args[2])
        self.assertIn('Other USB: Cleanup failed',warning.call_args.args[2])
        self.assertNotIn('Healthy USB',warning.call_args.args[2])
        self.assertEqual(owner._last_export_batch_results,tuple(results))

    def test_normal_drop_and_sync_need_no_prompt_but_explicit_repair_still_does(self):
        owner,target,_stock,_dual,_capture=self.generated()
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No) as question:
            self.assertTrue(MainWindow._confirm_dual_ipod_drop(owner,str(target),SimpleNamespace()))
            self.assertTrue(MainWindow._confirm_ipod_sync(owner,str(target)))
            question.assert_not_called()
            self.assertFalse(MainWindow._confirm_ipod_sync(owner,str(target),audit_existing=True))
        question.assert_called_once()
        self.assertIn('Repair',question.call_args.args[1])

    def test_catalog_sync_real_workers_are_quiet_and_preserve_navigation(self):
        case=artwork.IpodArtworkAdmission();case.app=self.app;case.setUp()
        self.addCleanup(lambda:self.assertTrue(case.doCleanups()))
        before=case.seat()
        case.question.side_effect=AssertionError('Second catalog Sync review')
        case.request();case.drain()
        case.plan_stock.assert_called_once();case.commit_stock.assert_called_once()
        case.information.assert_not_called();case.warning.assert_not_called()
        self.assertEqual(case.seat(),before)
        self.assertIn('matched',case.window._last_device_completion['text'])

    def test_successful_target_batch_is_quiet_and_retains_each_result(self):
        owner,target,_stock,_dual,_capture=self.generated()
        owner._export_batch=dict(active=None,pending=[],cancelled=False,outcomes=['complete','complete'],
            results=[('USB','Playlist exported'),('iPod','156 songs · 55 skipped')])
        owner._device_operation_owned=lambda *_:False
        owner._update_device_action_state=mock.Mock()
        with mock.patch.object(QMessageBox,'information') as info:
            ExportSelectionMixin._start_next_export_target(owner)
        info.assert_not_called()
        self.assertIn('Export complete',owner._flash_status.call_args.args[0])
        self.assertEqual(owner._last_export_batch_results,
            (('USB','Playlist exported'),('iPod','156 songs · 55 skipped')))

    def test_chooser_batch_keeps_stock_counts_in_its_final_nonmodal_footer(self):
        owner,target,stock,_dual,_capture=self.generated()
        stock=replace(stock,source_track_count=156,omissions=tuple(
            StockMediaOmission(str(i),'Skipped',Path('/generated/skipped.wav'),'PCM24') for i in range(55)))
        active=dict(path=str(target),name='iPod')
        batch=dict(active=active,pending=[],cancelled=False,outcomes=[],results=[],outcome='',error='',started=True)
        owner._export_batch=batch
        owner._export_target_complete=MethodType(ExportSelectionMixin._export_target_complete,owner)
        result=StockIpodCommitResult(target/'.recovery',156,0,0,0,('Favorites',),'a'*64)
        owner._device_operation_owned=lambda *_:False
        owner._update_device_action_state=mock.Mock()
        with mock.patch.object(QMessageBox,'information') as info:
            MainWindow._on_stock_ipod_commit(owner,str(target),result,plan=stock)
            owner._finalize_ipod_operation.call_args.args[1]()
            batch['results'].append(('iPod',batch['note']));batch['outcomes'].append(batch['outcome']);batch['active']=None
            ExportSelectionMixin._start_next_export_target(owner)
        info.assert_not_called()
        self.assertIn('156 songs',owner._flash_status.call_args.args[0])
        self.assertIn('55 skipped',owner._flash_status.call_args.args[0])

    def test_accepted_playlist_delete_finishes_quietly_after_finalization(self):
        owner,target,_stock,_dual,_capture=self.generated()
        result=SimpleNamespace(playlists=('Favorites',),cleanup_failures=(),deleted_track_records=5,
            deleted_media_files=5,deleted_bytes=123,remaining_playlists=2,remaining_tracks=9,
            recovery_directory=target/'.recovery',cleanup_cancelled=False)
        with mock.patch.object(QMessageBox,'information') as info, mock.patch.object(QMessageBox,'warning') as warning:
            MainWindow._on_stock_playlist_delete_commit(owner,str(target),result)
            self.assertFalse(hasattr(owner,'_last_device_completion'))
            owner._finalize_ipod_operation.call_args.args[1]()
        info.assert_not_called();warning.assert_not_called()
        self.assertIn('deleted',owner._flash_status.call_args.args[0])
        self.assertIn('5 unused track',owner._last_device_completion['text'])


if __name__=='__main__':
    unittest.main()
