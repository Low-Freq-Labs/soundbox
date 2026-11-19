"""Cancel retires joined iPod owners quietly, without hiding successor work."""
from threading import Event
from time import monotonic
from types import MethodType
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressBar, QPushButton, QWidget

from desktop.app import MainWindow
from desktop.cdj import CdjDeviceMixin
from desktop.export_dialog import ExportSelectionMixin
from desktop.export_wait import cancel_source_syncs
from desktop.workers import OneShotTask
from desktop.workers import ExportWorker
from soundbox import sync
from soundbox.library import Library, Playlist, Track
from soundbox.operation_control import OperationCancelled, checkpoint
from tests import test_device_cancel_presenters as presenter_fixture
from tests import test_ipod_export_capture as capture_fixture
from tests import test_ipod_artwork_ui as artwork_fixture


class IpodCancelPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        case = presenter_fixture.DeviceCancelPresenters()
        case.app = self.app
        case.setUp()
        self.addCleanup(lambda: self.assertTrue(case.doCleanups()))
        host = case.host
        host._EJECT_BLOCKING_WORKERS += ('_stock_plan_worker', '_dual_plan_worker',
                                       '_stock_commit_worker', '_dual_commit_worker')
        for name in host._EJECT_BLOCKING_WORKERS:
            setattr(host, name, None)
        for name in ('_cancel_export', '_on_stock_ipod_plan', '_on_dual_ipod_plan',
                     '_on_stock_ipod_commit', '_on_dual_ipod_commit', '_show_dual_ipod_failure'):
            setattr(host, name, MethodType(getattr(MainWindow, name), host))
        host._dual_active_preconfirmed = False
        host._dual_active_confirmed = False
        host._dual_active_device = case.dev
        host._dual_active_playlist_keys = {'selected'}
        host._dual_active_action = 'sync'
        host._dual_active_track_total = 1
        host._reset_dual_batch = mock.Mock()
        host.rockbox_media_cache = mock.Mock()
        return case, host

    def until(self, predicate):
        deadline = monotonic() + 4
        while not predicate() and monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(1)
        self.assertTrue(predicate(), 'Generated cancellation did not retire')

    def test_cancelled_stock_and_dual_plan_join_before_hiding_controls_without_modal(self):
        for kind in ('stock', 'dual'):
            with self.subTest(kind=kind):
                case, host = self.host()
                entered, release = Event(), Event()
                case.releases.append(release)
                events = []
                def plan():
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Generated plan was not released')
                    checkpoint()
                    raise AssertionError('Cancelled plan reached its result')
                worker = OneShotTask(plan)
                case.workers.append(worker)
                attr = f'_{kind}_plan_worker'
                setattr(host, attr, worker)
                wait = worker.wait
                def joined(*args):
                    result = wait(*args)
                    if result:
                        events.append('joined')
                    return result
                worker.wait = joined
                def completed(result):
                    events.append('presented')
                    getattr(host, f'_on_{kind}_ipod_plan')(case.dev, result)
                worker.finished.connect(lambda: host._finish_device_result_worker(attr, worker, completed))
                host._start_device_worker(worker, case.dev, 'plan')
                self.assertTrue(entered.wait(2))
                host._cancel_export()
                self.assertTrue(host._cancelling)
                self.assertFalse(host.cancel_btn.isHidden())
                self.assertIs(getattr(host, attr), worker)
                self.assertEqual(events, [])
                release.set()
                self.until(lambda: getattr(host, attr) is None)
                self.assertEqual(events[:2], ['joined', 'presented'])
                self.assertFalse(host._cancelling)
                self.assertFalse(host._exporting)
                self.assertTrue(host.cancel_btn.isHidden())
                self.assertTrue(host.progress.isHidden())
                self.assertIn('cancelled', host.status_label.text().lower())
                case.warning.assert_not_called()
                case.critical.assert_not_called()
                case.information.assert_not_called()
                host._refresh_devices.assert_not_called()

    def test_real_planning_failure_keeps_error_dialog_and_retires_cancel(self):
        for kind in ('stock', 'dual'):
            with self.subTest(kind=kind):
                case, host = self.host()
                host._cancelling = True
                getattr(host, f'_on_{kind}_ipod_plan')(case.dev, ValueError('Generated source failure'))
                case.warning.assert_called_once()
                self.assertFalse(host._cancelling)
                self.assertTrue(host.cancel_btn.isHidden())

    def test_safe_commit_cancel_is_quiet_but_failed_recovery_remains_visible(self):
        for kind in ('stock', 'dual'):
            with self.subTest(kind=kind):
                case, host = self.host()
                cancelled = OperationCancelled('Generated safe boundary')
                cancelled.recovery_status = 'rolled back'
                getattr(host, f'_on_{kind}_ipod_commit')(case.dev, cancelled)
                case.critical.assert_not_called()
                case.warning.assert_not_called()
                self.assertTrue(host.cancel_btn.isHidden())
                self.assertFalse(host._cancelling)
                failed = OperationCancelled('Generated rollback failure')
                failed.recovery_status = 'recovery required'
                getattr(host, f'_on_{kind}_ipod_commit')(case.dev, failed)
                case.critical.assert_called_once()

    def test_usb_cancel_presenter_is_quiet_and_keeps_previous_catalog(self):
        case, host = self.host()
        host._sync_cue_editing = mock.Mock()
        host._usb_view_cache = {'old': 'preserved'}
        CdjDeviceMixin._finish_cdj(host, case.dev, OperationCancelled('Generated USB cancel'))
        self.assertEqual(host._usb_view_cache, {'old': 'preserved'})
        self.assertTrue(host.cancel_btn.isHidden())
        case.warning.assert_not_called()
        case.information.assert_not_called()

    def test_cancelled_and_mixed_success_batches_show_no_modal_but_errors_do(self):
        case, host = self.host()
        for outcomes in (['cancelled'], ['complete', 'cancelled'], ['failed', 'cancelled']):
            with self.subTest(outcomes=outcomes):
                case.information.reset_mock();case.warning.reset_mock()
                host._exporting = False
                host._export_batch = dict(active=None, pending=[], cancelled=True,
                    outcomes=outcomes, results=[('Generated iPod',
                        'Generated failure' if outcome=='failed' else outcome) for outcome in outcomes])
                ExportSelectionMixin._start_next_export_target(host)
                self.assertIsNone(host._export_batch)
                case.information.assert_not_called()
                self.assertEqual(case.warning.call_count, int('failed' in outcomes))
                if 'failed' in outcomes:self.assertIn('Generated failure',case.warning.call_args.args[2])

    def test_capture_failure_retires_its_controls_without_overwriting_another_owner(self):
        for other_owner in (False, True):
            with self.subTest(other_owner=other_owner):
                case, host = self.host()
                host._background_notice = mock.Mock()
                host._source_sync_waiting = None
                host._cancelling = False
                host._exporting = False
                release = None
                if other_owner:
                    _worker, release, _callback = case.start_worker('_resync_worker', None)
                before = (host._pulse_word, host.status_label.text(), host.cancel_btn.isHidden())
                with mock.patch('desktop.ipod_export_capture.request_ipod_export',
                                side_effect=ValueError('Generated selected-source failure')):
                    self.assertTrue(ExportSelectionMixin._request_ipod_export_capture(
                        host, 'stock', case.dev, []))
                if other_owner:
                    self.assertEqual((host._pulse_word, host.status_label.text(),
                                      host.cancel_btn.isHidden()), before)
                    host._background_notice.assert_called_once()
                    case.drain('_resync_worker', release)
                else:
                    self.assertTrue(host.cancel_btn.isHidden())
                    self.assertTrue(host.progress.isHidden())
                    self.assertIsNone(host._pulse_word)
                case.warning.assert_not_called()
                case.information.assert_not_called()

    def test_checkpoint_cancel_keeps_controls_until_its_real_worker_is_joined(self):
        case = capture_fixture.IpodExportCaptureTests()
        case.app = self.app
        case.setUp()
        self.addCleanup(lambda: self.assertTrue(case.doCleanups()))
        host = case.w
        frame = QWidget()
        self.addCleanup(frame.deleteLater)
        host.cancel_btn = QPushButton('Cancel', frame)
        host.progress = QProgressBar(frame)
        host.progress.setVisible(True)
        host._update_device_action_state = mock.Mock()
        host._maybe_finish_export_target = mock.Mock()
        host._pending_device_operation = None
        host._dual_sync_queue = {}
        host._cdj_waiting = None
        host._cancelling = False
        host._exporting = False
        host._deleting = False
        host._stock_view_pending = {}
        host._stock_view_warming = set()
        host._EJECT_BLOCKING_WORKERS = ()
        from soundbox.device_operations import DeviceOperationCoordinator
        host._device_coordinator = DeviceOperationCoordinator()
        host._device_operation_owned = MethodType(MainWindow._device_operation_owned, host)
        host.cancel_btn.clicked.connect(lambda: MainWindow._cancel_export(host))
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = host._state_repository.verify_selected_values
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Generated checkpoint was not released')
            return original(*args, **kwargs)
        before = host._state_repository.path.read_bytes()
        with mock.patch.object(host._state_repository, 'verify_selected_values', side_effect=held):
            case.request()
            case.until(entered.is_set)
            host.cancel_btn.click()
            self.assertTrue(host._cancelling)
            self.assertFalse(host.cancel_btn.isHidden())
            self.assertTrue(host._device_operation_owned())
            host.cancel_btn.click()
            self.assertFalse(host.cancel_btn.isHidden())
            release.set()
            case.until(lambda: not host._state_committer.owner.pending
                       and not getattr(host, '_cancelled_source_syncs', None))
        self.assertTrue(host.cancel_btn.isHidden())
        self.assertTrue(host.progress.isHidden())
        self.assertFalse(host._cancelling)
        self.assertEqual(host._state_repository.path.read_bytes(), before)
        case.ready.assert_not_called()
        case.failed.assert_not_called()

    def test_device_retirement_keeps_cancel_visible_until_its_cancelled_capture_also_joins(self):
        from desktop.task_owner import TaskOwner
        case, host = self.host()
        capture_entered, capture_release = Event(), Event()
        plan_entered, plan_release = Event(), Event()
        case.releases.extend((capture_release, plan_release))
        def held(entered, release):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Generated source/plan owner was not released')
            checkpoint()
        capture = OneShotTask(lambda: held(capture_entered, capture_release))
        owner = TaskOwner(host)
        self.addCleanup(owner.drain)
        request = dict(batch=None, checkpoint=None, manifest_worker=capture,
                       cancel_checkpoint=capture.cancel)
        owner.adopt(capture, lambda _task: request.update(manifest_worker=None))
        host._source_sync_waiting = [request]
        capture.start()
        self.assertTrue(capture_entered.wait(2))
        worker = OneShotTask(lambda: held(plan_entered, plan_release))
        case.workers.append(worker)
        host._stock_plan_worker = worker
        worker.finished.connect(lambda: host._finish_device_result_worker(
            '_stock_plan_worker', worker, lambda result: host._on_stock_ipod_plan(case.dev, result)))
        host._start_device_worker(worker, case.dev, 'plan')
        self.assertTrue(plan_entered.wait(2))
        host._cancel_export()
        self.assertTrue(worker.cancellation.is_set())
        self.assertTrue(capture.cancellation.is_set())
        plan_release.set()
        self.until(lambda: host._stock_plan_worker is None)
        self.assertTrue(host._cancelling)
        self.assertFalse(host.cancel_btn.isHidden())
        self.assertIs(request['manifest_worker'], capture)
        capture_release.set()
        self.until(lambda: not owner.pending and not getattr(host, '_cancelled_source_syncs', None))
        self.assertTrue(host.cancel_btn.isHidden())
        self.assertFalse(host._cancelling)
        case.warning.assert_not_called()
        case.critical.assert_not_called()
        case.information.assert_not_called()

    def test_actual_copy_failure_with_cancel_remains_an_error_in_batch_export_and_resync(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sources = root / 'sources'
            sources.mkdir()
            source = sources / 'selected.flac'
            source.write_bytes(b'Generated payload; independent device failure' * 64)
            catalog = sources / 'library.xml'
            catalog.write_bytes(b'Generated source boundary')
            target = root / 'generated-device'
            target.mkdir()
            original = source.read_bytes()
            track = Track('selected', 'Selected', 'Artist', source.as_uri())
            playlist = Playlist('Favorites', [], ['selected'], pid='favorites')
            library = Library({'selected': track}, [playlist], catalog, source_kind='fixture')
            worker = ExportWorker(library)
            self.addCleanup(worker.deleteLater)
            worker.add(str(target), [playlist], mount_token=target.stat().st_dev)
            def failed_copy(*_args, **_kwargs):
                worker.cancel()
                raise OSError('Generated device write failure')
            with mock.patch.object(sync, '_safe_copy', side_effect=failed_copy):
                worker.run()
            report = worker.result[0][1]
            self.assertTrue(report.cancelled)
            self.assertIn('Generated device write failure', '\n'.join(report.playlists[0].errors))
            self.assertEqual(source.read_bytes(), original)
            for resync in (False, True):
                with self.subTest(resync=resync):
                    case, host = self.host()
                    batch = dict(active=dict(path=case.dev), started=True, error='', outcome='', note='')
                    host._export_batch = batch
                    host._export_target_result = MethodType(ExportSelectionMixin._export_target_result, host)
                    host._export_target_result(case.dev, [(case.dev, report)])
                    self.assertEqual(batch['outcome'], 'failed')
                    if resync:
                        host._on_resync_done(case.dev, report)
                        case.warning.assert_called_once()
                    else:
                        host._on_export_done([(case.dev, report)])
                        case.information.assert_called_once()
                    self.assertIn('needs attention', host.status_label.text().lower())
                    self.assertIn('Generated device write failure', batch['error'])
                    self.assertNotIn('✓', host.status_label.text())

    def test_catalog_plan_error_is_not_relabelled_when_cancel_event_is_also_set(self):
        case = artwork_fixture.IpodArtworkAdmission()
        case.app = self.app
        case.setUp()
        self.addCleanup(lambda: self.assertTrue(case.doCleanups()))
        def failed(*_args, **_kwargs):
            case.window._stock_plan_worker.cancel()
            raise OSError('Generated planning storage failure')
        case.plan_stock.side_effect = failed
        case.request()
        case.drain()
        case.warning.assert_called_once()
        self.assertIn('Generated planning storage failure', str(case.warning.call_args))
        case.commit_stock.assert_not_called()
        self.assertTrue(case.window.cancel_btn.isHidden())
        self.assertFalse(case.window._cancelling)

    def test_capture_retirement_clears_only_its_cancel_flag_and_preserves_new_presentation(self):
        from desktop.task_owner import TaskOwner
        for successor in ('format', 'source'):
            with self.subTest(successor=successor):
                case, host = self.host()
                entered, release = Event(), Event()
                case.releases.append(release)
                def held():
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Generated capture was not released')
                    checkpoint()
                worker = OneShotTask(held)
                owner = TaskOwner(host)
                self.addCleanup(owner.drain)
                request = dict(batch=None, checkpoint=None, manifest_worker=worker,
                               cancel_checkpoint=worker.cancel)
                owner.adopt(worker, lambda _task: request.update(manifest_worker=None))
                host._source_sync_waiting = [request]
                worker.start()
                self.assertTrue(entered.wait(2))
                self.assertTrue(cancel_source_syncs(host))
                host._cancelling = True
                host._exporting = False
                if successor == 'format':
                    host._usb_format_device = '/generated/other-usb'
                    host._usb_format_started = True
                    host.cancel_btn.setVisible(False)
                else:
                    host._source_sync_waiting = [dict(batch=None)]
                    host.cancel_btn.setVisible(True)
                host._start_pulse('Formatting other USB' if successor == 'format' else 'New source wait')
                host.progress.setValue(731)
                before = (host._pulse_word, host.status_label.text(), host.progress.value(),
                          host.progress.isHidden(), host.cancel_btn.isHidden())
                release.set()
                self.until(lambda: not owner.pending and not getattr(host, '_cancelled_source_syncs', None))
                self.assertFalse(host._cancelling)
                self.assertEqual((host._pulse_word, host.status_label.text(), host.progress.value(),
                                  host.progress.isHidden(), host.cancel_btn.isHidden()), before)


if __name__ == '__main__':
    unittest.main()
