"""Cancelled device reports and obsolete owners retain truthful presentation."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QWidget

from desktop.app import MainWindow
from desktop.workers import OneShotTask
from soundbox.device_operations import DeviceOperationCoordinator, DeviceSessionIdentity
from soundbox.operation_control import OperationCancelled
from soundbox.report import PlaylistReport, SyncReport
from soundbox.library import Library


class _Label(QLabel):
    def reserve(self, _text):
        pass


class DeviceCancelPresenters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = TemporaryDirectory(prefix='soundbox-cancel-presenter-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.target = self.root / 'generated-device'
        self.target.mkdir()
        self.dev = str(self.target)
        self.host = QWidget()
        host = self.host
        host.lib = Library({}, [], '/synthetic/library.xml')
        host._current_device_path = self.dev
        host._device_profiles = {self.dev: SimpleNamespace(kind='rockbox')}
        host._device_mount_tokens = {self.dev: self.target.stat().st_dev}
        host._device_coordinator = DeviceOperationCoordinator()
        host._device_coordinator.observe(DeviceSessionIdentity(self.dev, self.target.stat().st_dev))
        host._EJECT_BLOCKING_WORKERS = ('export_worker', '_resync_worker', '_dev_scan_worker')
        for key, value in dict(
                export_worker=None, _resync_worker=None, _dev_scan_worker=None,
                _shutting_down=False, _exporting=True, _deleting=False, _cancelling=False,
                _usb_format_device='', _usb_format_volumes=(),
                _stock_view_warming=set(), _stock_view_pending={},
                _pending_device_operation=None, _export_batch=None,
                _source_sync_waiting=None, _cdj_waiting=None, _dual_sync_queue={},
                _device_mount_refresh_pending=False, _device_finalization_refreshing=False,
                _refresh_worker=None, _drift_cache={}, _eject_ready_device='',
                _preparing_library=False, _prep_update_message='', _deferred_edit_status='',
                _pulse_word=None, _pulse_dots=0, _status_generation=0,
                tag_controller=SimpleNamespace(update_status_text='', playlist_update_status='',
                                               registration_update_status=''),
                _set_status_count=mock.Mock(), _export_target_result=mock.Mock(),
                _maybe_finish_export_target=mock.Mock(), _refresh_devices=mock.Mock()).items():
            setattr(host, key, value)
        host.status_label = _Label(host)
        host.progress = QProgressBar(host)
        host.cancel_btn = QPushButton('Cancel', host)
        host.eject_btn = QPushButton('Eject', host)
        host.device_sync_btn = QPushButton('Sync', host)
        host._pulse_timer = QTimer(host)
        host._pulse_timer.setInterval(1000)
        self.page = 1
        host.stack = SimpleNamespace(currentIndex=lambda: self.page)
        host.fs_model = mock.Mock()
        host.fs_model.rootPath.return_value = self.dev
        host.fs_view = mock.Mock()
        for name in ('_on_resync_done', '_on_export_done', '_start_device_worker',
                     '_finish_device_result_worker', '_finish_worker',
                     '_device_operation_owned', '_update_device_action_state',
                     '_stock_has_sync_source', '_set_device_progress', '_set_status_text',
                     '_start_pulse', '_stop_pulse', '_edit_status_deferred',
                     '_restore_edit_status', '_sync_edit_status', '_background_status_available'):
            setattr(host, name, lambda *args, _name=name, **kwargs:
                    getattr(MainWindow, _name)(host, *args, **kwargs))
        host._flash_status = mock.Mock(side_effect=lambda *args, **kwargs:
                                       MainWindow._flash_status(host, *args, **kwargs))
        host._finalize_ipod_operation = mock.Mock(side_effect=lambda _dev, complete, **_kw: complete())
        host._finalize_device_operations = mock.Mock(side_effect=lambda _devs, complete: complete())
        self.information = self.patch('desktop.app.QMessageBox.information')
        self.warning = self.patch('desktop.app.QMessageBox.warning')
        self.critical = self.patch('desktop.app.QMessageBox.critical')
        visible = mock.patch.object(QWidget, 'show', side_effect=AssertionError('Visible test window'))
        visible.start()
        self.addCleanup(visible.stop)
        self.workers = []
        self.releases = []
        self.addCleanup(self.retire)
        host._set_device_progress(410)
        host.cancel_btn.setVisible(True)
        host._start_pulse('Cancelling')

    def patch(self, target):
        patcher = mock.patch(target)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def retire(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            try:
                worker.cancel()
                self.assertTrue(worker.wait(3000), 'Generated presenter worker did not retire')
            except RuntimeError:
                pass
        self.app.processEvents()
        self.host._pulse_timer.stop()
        self.host.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def assert_cancelled_presentation(self):
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.assertTrue(self.host.progress.isHidden())
        self.assertTrue(self.host.cancel_btn.isHidden())
        self.assertIn('cancel', self.host.status_label.text().lower())
        self.assertNotIn('✓', self.host.status_label.text())
        self.host._finalize_ipod_operation.assert_not_called()
        self.host._finalize_device_operations.assert_not_called()
        self.host.fs_model.setRootPath.assert_not_called()
        self.host.fs_view.setRootIndex.assert_not_called()
        self.warning.assert_not_called()
        self.critical.assert_not_called()

    def start_worker(self, attr, result, *, connect=True):
        entered, release = Event(), Event()
        self.releases.append(release)
        def held():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Generated presenter release timed out')
            return result
        worker = OneShotTask(held)
        self.workers.append(worker)
        setattr(self.host, attr, worker)
        callback = mock.Mock()
        if connect:
            worker.finished.connect(lambda w=worker:
                self.host._finish_device_result_worker(attr, w, callback))
        self.host._start_device_worker(worker, self.dev,
                                       'export' if attr == 'export_worker' else 'sync')
        self.assertTrue(entered.wait(2))
        return worker, release, callback

    def drain(self, attr, release):
        release.set()
        deadline = time.monotonic() + 3
        while getattr(self.host, attr, None) is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertIsNone(getattr(self.host, attr, None))

    def test_resync_report_cancel_is_truth_even_without_legacy_cancel_flag(self):
        self.host._on_resync_done(self.dev, SyncReport(False, cancelled=True))
        self.assert_cancelled_presentation()
        self.assertEqual(self.host._eject_ready_device, self.dev)

    def test_export_partial_cancel_report_keeps_completed_files_without_success_or_reads(self):
        completed = SyncReport(False, playlists=[PlaylistReport('Done', copied=1)])
        partial = SyncReport(False, playlists=[PlaylistReport('Partial', copied=1)], cancelled=True)
        self.host._on_export_done([(self.dev, completed), (self.dev, partial)])
        self.assert_cancelled_presentation()
        self.information.assert_not_called()

    def test_empty_report_legacy_cancel_retires_without_finalization_or_reads(self):
        self.host._cancelling = True
        self.host._on_export_done([])
        self.assert_cancelled_presentation()
        self.assertEqual(self.host._eject_ready_device, '')

    def test_healthy_reports_win_a_late_cancel_and_keep_normal_refresh(self):
        for kind in ('export', 'resync'):
            with self.subTest(kind=kind):
                self.host._cancelling = True
                self.host._exporting = True
                report = SyncReport(False, playlists=[PlaylistReport('Done', copied=1)])
                if kind == 'export':
                    self.host._on_export_done([(self.dev, report)])
                    self.host._finalize_device_operations.assert_called_once()
                else:
                    self.host._on_resync_done(self.dev, report)
                    self.host._finalize_ipod_operation.assert_called_once()
                self.assertFalse(self.host._cancelling)
                self.assertFalse(self.host._exporting)
                self.assertIn('✓', self.host.status_label.text())
                self.host.fs_model.setRootPath.assert_called()
                self.host.fs_view.setRootIndex.assert_called()
                self.host.fs_model.reset_mock()
                self.host.fs_view.reset_mock()

    def test_obsolete_cancelled_export_and_resync_results_retire_their_own_busy_flags(self):
        for attr in ('export_worker', '_resync_worker'):
            with self.subTest(attr=attr):
                self.host._exporting = True
                self.host._cancelling = True
                self.host._set_device_progress(410)
                self.host.cancel_btn.setVisible(True)
                self.host._device_coordinator.observe(
                    DeviceSessionIdentity(self.dev, self.target.stat().st_dev))
                report = SyncReport(False, cancelled=True)
                result = [(self.dev, report)] if attr == 'export_worker' else report
                _worker, release, callback = self.start_worker(attr, result)
                self.host._device_coordinator.disconnect(self.dev)
                self.drain(attr, release)
                callback.assert_not_called()
                self.assertIsNone(self.host._device_coordinator.active)
                self.assertFalse(self.host._exporting)
                self.assertFalse(self.host._cancelling)
                self.assertTrue(self.host.progress.isHidden())
                self.assertTrue(self.host.cancel_btn.isHidden())
                self.host._finalize_ipod_operation.assert_not_called()
                self.host._finalize_device_operations.assert_not_called()

    def test_late_obsolete_exception_cannot_clear_newer_device_owner(self):
        old, release, callback = self.start_worker('export_worker', OperationCancelled('old'), connect=False)
        self.host._device_coordinator.disconnect(self.dev)
        release.set()
        self.assertTrue(old.wait(3000))
        self.host._finish_device_result_worker('export_worker', old, callback)
        callback.assert_not_called()
        self.host._device_coordinator.observe(
            DeviceSessionIdentity(self.dev, self.target.stat().st_dev))
        newer, newer_release, _callback = self.start_worker(
            'export_worker', [(self.dev, SyncReport(False, cancelled=True))])
        self.host._device_coordinator.cancel()
        self.host._exporting = True
        self.host._cancelling = True
        self.host._dual_sync_queue = {'newer': 'preserve this queued request'}
        self.host._set_device_progress(430)
        self.host.cancel_btn.setVisible(True)
        self.host._start_pulse('Cancelling newer task')
        before = (self.host.status_label.text(), self.host.status_label.toolTip(),
                  self.host._pulse_word, self.host.progress.value())
        self.host._finish_device_result_worker('export_worker', old, callback)
        self.assertIs(self.host.export_worker, newer)
        self.assertIs(self.host._device_coordinator.active.worker, newer)
        self.assertTrue(self.host._exporting)
        self.assertTrue(self.host._cancelling)
        self.assertEqual(self.host._dual_sync_queue, {'newer': 'preserve this queued request'})
        self.assertFalse(self.host.progress.isHidden())
        self.assertFalse(self.host.cancel_btn.isHidden())
        self.assertEqual((self.host.status_label.text(), self.host.status_label.toolTip(),
                          self.host._pulse_word, self.host.progress.value()), before)
        self.drain('export_worker', newer_release)

    def test_obsolete_owner_releases_its_flags_without_hiding_separate_format_progress(self):
        self.host._cancelling = True
        _worker, release, callback = self.start_worker(
            '_resync_worker', SyncReport(False, cancelled=True))
        self.host._device_coordinator.disconnect(self.dev)
        self.host._usb_format_device = str(self.root / 'different-generated-usb')
        self.host._usb_format_volumes = (self.host._usb_format_device,)
        self.host._usb_format_started = True
        self.host._set_device_progress(480)
        self.host._start_pulse('Formatting other USB')
        self.host.cancel_btn.setVisible(False)
        before = (self.host.status_label.text(), self.host._pulse_word,
                  self.host.progress.value(), self.host.progress.isHidden())
        self.drain('_resync_worker', release)
        callback.assert_not_called()
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)
        self.assertEqual((self.host.status_label.text(), self.host._pulse_word,
                          self.host.progress.value(), self.host.progress.isHidden()), before)
        self.assertTrue(self.host._device_operation_owned())
