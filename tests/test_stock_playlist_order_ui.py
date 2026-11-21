"""Drag order reaches joined stock workers without source membership Sync."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from desktop.stock_playlist_order import request_order
from desktop.workers import OneShotTask
from soundbox.operation_control import CommittedOperationError, OperationCancelled
from soundbox.stock_ipod_info import StockIpodPlaylistSnapshot
from tests import test_ipod_artwork_ui as fixture


class StockPlaylistOrderUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        fixture.IpodArtworkAdmission.setUpClass()

    def setUp(self):
        self.case = fixture.IpodArtworkAdmission(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.w = self.case.window; self.pages = self.w.stock_ipod_pages
        other = StockIpodPlaylistSnapshot(77, 'Other', 'Playlist', False, ())
        self.snapshot = replace(self.case.snapshot, playlists=(*self.case.snapshot.playlists, other))
        self.w._stock_view_cache[self.case.device] = (self.snapshot, None)
        self.w._stock_snapshot = self.snapshot
        self.pages.set_snapshot(self.snapshot)
        self.w._update_device_action_state()
        self.order = ('77', str(fixture.PLAYLIST_ID))
        self.plan = self.enterContext(mock.patch('soundbox.stock_playlist_order.plan_stock_playlist_order',
            return_value=SimpleNamespace(requires_commit=True)))
        self.result = SimpleNamespace(catalog_warning='', catalog_witness=None)
        self.commit = self.enterContext(mock.patch('soundbox.stock_playlist_order.commit_stock_playlist_order',
                                                   return_value=self.result))
        self.enterContext(mock.patch('desktop.stock_playlist_order.device_facts', return_value=self.case.facts))
        self.enterContext(mock.patch('desktop.stock_playlist_order.device_fs', return_value='msdos'))
        self.full_sync = self.enterContext(mock.patch.object(self.w, '_begin_stock_ipod_sync',
                                               side_effect=AssertionError('Order must not reconcile songs')))
        self.finalized = []
        def finalize(dev, complete, **_options):
            self.finalized.append(dev)
            fresh = replace(self.snapshot, playlists=tuple(reversed(self.snapshot.playlists)))
            self.w._stock_view_cache[dev] = (fresh, None)
            self.pages.set_snapshot(fresh)
            complete()
        self.enterContext(mock.patch.object(self.w, '_finalize_ipod_operation', side_effect=finalize))

    def wait(self, predicate):
        for _ in range(1000):
            if predicate():return
            QTest.qWait(5)
        self.fail('Playlist-order owner did not settle')

    def pending_drop(self):
        token=object()
        self.assertTrue(self.pages._set_playlist_order(self.order))
        self.pages._pending_playlist_order=self.order
        self.pages._pending_playlist_order_token=token
        return token

    def scan(self):
        release=threading.Event()
        worker=OneShotTask(lambda:release.wait(3))
        worker.retry_discovery=False
        self.w._dev_scan_worker=worker
        worker.finished.connect(lambda:self.w._finish_device_scan_worker(worker))
        self.w._start_device_worker(worker,'','discover')
        def retire():
            release.set()
            try:worker.wait(5000)
            except RuntimeError:pass
            self.app.processEvents()
        self.addCleanup(retire)
        return worker,release

    def assert_drop_restored(self, token):
        self.assertIsNone(self.pages.playlist_order_token)
        self.assertIsNone(self.pages._pending_playlist_order)
        self.assertEqual(self.pages._managed_playlist_order(),self.pages._committed_playlist_order)
        self.assertIsNot(token,self.pages.playlist_order_token)

    def test_app_dispatches_stock_order_exact_ids_without_source_playlist_or_media_sync(self):
        self.w._apply_composite_playlist_order(self.order)
        self.case.drain()
        self.plan.assert_called_once()
        self.assertEqual(self.plan.call_args.args, (self.case.device, (77, fixture.PLAYLIST_ID)))
        self.assertIs(self.plan.call_args.kwargs['snapshot'], self.snapshot)
        self.commit.assert_called_once(); self.full_sync.assert_not_called()
        self.assertEqual(self.finalized, [self.case.device])
        self.case.warning.assert_not_called(); self.case.information.assert_not_called()
        self.assertEqual(self.pages._managed_playlist_order(), self.order)

    def test_musical_metadata_edits_during_order_do_not_retarget_or_abort_native_order(self):
        original = OneShotTask.start; held = []
        with mock.patch.object(OneShotTask, 'start', lambda worker: held.append(worker)):
            request_order(self.w, self.order)
        self.assertEqual(len(held), 1)
        self.w.titles['1'] = 'Later ordinary title'
        self.w.lib.playlists[0].keys.reverse()
        original(held[0]); self.case.drain()
        self.commit.assert_called_once(); self.assertEqual(self.w.titles['1'], 'Later ordinary title')

    def test_mount_or_catalog_change_before_plan_delivery_refuses_commit(self):
        for change in ('mount', 'catalog'):
            with self.subTest(change=change):
                request_order(self.w, self.order)
                worker = self.w._stock_plan_worker; self.assertTrue(worker.wait(5000))
                if change == 'mount':
                    self.w._device_coordinator.disconnect(self.case.device)
                else:
                    self.w._stock_view_cache[self.case.device] = (replace(self.snapshot, database_sha256='changed'), None)
                self.case.drain()
                self.commit.assert_not_called()
                self.w._stock_view_cache[self.case.device] = (self.snapshot, None)
                self.w._device_coordinator.observe(fixture.DeviceSessionIdentity(self.case.device, self.case.target.stat().st_dev))
                self.plan.reset_mock()

    def test_bad_duplicate_unknown_and_busy_orders_do_not_start_a_writer(self):
        for bad in (('77','77'), ('0',), ('-1',), ('077',), (str(2**64),)):
            request_order(self.w, bad)
        self.plan.assert_not_called(); self.commit.assert_not_called()
        self.enterContext(mock.patch.object(self.w, '_device_operation_owned', return_value=True))
        request_order(self.w, self.order)
        self.plan.assert_not_called()

    def test_plan_failure_restores_the_exact_pending_drop(self):
        token = self.pending_drop()
        self.plan.side_effect = OSError('Generated catalog refusal')
        restored = self.enterContext(mock.patch.object(self.pages, 'restore_playlist_order', wraps=self.pages.restore_playlist_order))
        request_order(self.w, self.order); self.case.drain()
        restored.assert_called_with(token)
        self.commit.assert_not_called(); self.case.warning.assert_called_once()
        self.assert_drop_restored(token)

    def test_cancel_finished_plan_before_gui_delivery_never_starts_commit(self):
        token=self.pending_drop()
        request_order(self.w,self.order)
        worker=self.w._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        self.assertIsNotNone(worker.result)
        worker.cancel()
        self.case.drain()
        self.assert_drop_restored(token)
        self.commit.assert_not_called();self.case.warning.assert_not_called()
        self.assertEqual(self.finalized,[])
        self.assertIsNone(self.w._stock_plan_worker);self.assertIsNone(self.w._stock_commit_worker)
        self.assertIsNone(self.w._device_coordinator.active)
        self.assertTrue(self.w._device_coordinator.last_result.cancelled)
        self.assertFalse(self.w._device_coordinator.last_result.committed)

    def test_unrelated_mount_change_during_scan_does_not_cancel_target_order(self):
        _worker,release=self.scan();self.pending_drop()
        request_order(self.w,self.order)
        self.assertIsNotNone(self.w._pending_device_operation)
        self.w._device_mount_tokens['/generated/unrelated-device']=999
        release.set()
        self.wait(lambda:self.w._dev_scan_worker is None)
        self.case.drain()
        self.plan.assert_called_once();self.commit.assert_called_once()
        self.assertEqual(self.pages._managed_playlist_order(),self.order)

    def test_stale_scan_and_cancelled_scan_restore_drop_without_starting_plan(self):
        for change in ('mount','cancel'):
            with self.subTest(change=change):
                worker,release=self.scan();token=self.pending_drop()
                request_order(self.w,self.order)
                if change=='mount':self.w._device_mount_tokens[self.case.device]+=1
                else:worker.cancel()
                release.set()
                self.wait(lambda:self.w._dev_scan_worker is None)
                self.assert_drop_restored(token)
                self.assertIsNone(self.w._pending_device_operation)
                self.plan.assert_not_called();self.commit.assert_not_called()
                self.case.warning.assert_not_called()
                self.w._device_mount_tokens[self.case.device]=self.case.target.stat().st_dev

    def test_cancel_waiting_order_restores_drop_and_leaves_unrelated_scan_owned(self):
        worker,release=self.scan();token=self.pending_drop()
        request_order(self.w,self.order)
        self.w._cancel_export()
        self.assert_drop_restored(token)
        self.assertIsNone(self.w._pending_device_operation)
        self.assertIs(self.w._dev_scan_worker,worker)
        self.assertFalse(worker.cancellation.is_set())
        release.set();self.wait(lambda:self.w._dev_scan_worker is None)
        self.plan.assert_not_called();self.commit.assert_not_called()

    def test_dual_plan_refusal_and_commit_cancellation_restore_only_their_drop(self):
        for stage in ('plan','commit'):
            with self.subTest(stage=stage):
                token=self.pending_drop()
                self.w._playlist_order_drop=(self.case.device,token)
                self.w._dual_active_action='order'
                error=OperationCancelled('Generated dual order cancellation')
                if stage=='plan':self.w._on_dual_ipod_plan(self.case.device,error)
                else:self.w._on_dual_ipod_commit(self.case.device,error)
                self.assert_drop_restored(token)
                self.assertIsNone(self.w._playlist_order_drop)
        token=self.pending_drop()
        self.w._playlist_order_drop=(self.case.device,object())
        self.w._restore_playlist_order_drop(self.case.device)
        self.assertIs(self.pages.playlist_order_token,token)

    def test_older_dual_capture_failure_cannot_restore_a_newer_drop(self):
        old=self.pending_drop();self.w._playlist_order_drop=(self.case.device,old)
        callbacks=[]
        def capture(*_args,**options):callbacks.append(options['failed']);return True
        with mock.patch('desktop.ipod_export_capture.request_ipod_export',side_effect=capture):
            self.w._request_ipod_export_capture('dual',self.case.device,[],action='order')
            newer=self.pending_drop();self.w._playlist_order_drop=(self.case.device,newer)
            self.w._request_ipod_export_capture('dual',self.case.device,[],action='order')
        callbacks[0](ValueError('Old generated capture failed'),{'batch':None,'batch_target':None})
        self.assertIs(self.pages.playlist_order_token,newer)
        callbacks[1](ValueError('Current generated capture failed'),{'batch':None,'batch_target':None})
        self.assert_drop_restored(newer)
        self.plan.assert_not_called();self.commit.assert_not_called()

    def test_committed_failure_refreshes_actual_catalog_and_keeps_error_visible(self):
        self.commit.side_effect = CommittedOperationError('Generated readback refusal')
        request_order(self.w, self.order); self.case.drain()
        self.assertEqual(self.finalized, [self.case.device]); self.case.warning.assert_called_once()
