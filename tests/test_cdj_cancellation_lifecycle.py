"""Real CDJ transactions/workers on generated files; no devices or app state."""
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import MethodType, SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.cdj import CdjDeviceMixin, CdjWorker
from desktop.usb_device_view import UsbSnapshot
from soundbox import cdj_export, cdj_transaction as tx
from soundbox.device_operations import (DeviceCommand, DeviceOperationCoordinator,
                                       DeviceSessionIdentity, Phase)
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.source_acquisition import SourceAcquisitionError, SourceContract, acquire_source
from tests import test_cdj_transaction as fixtures


class _HeldRead:
    def __init__(self, raw, entered, release, reads):
        self.raw, self.entered, self.release, self.reads = raw, entered, release, reads

    def __enter__(self):
        self.raw.__enter__()
        return self

    def __exit__(self, *args):
        return self.raw.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def read(self, count=-1):
        value = self.raw.read(count)
        self.reads.append(len(value))
        if not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError('Generated read was never released')
        return value


class CdjCancellationLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = f = fixtures.CdjTransactionTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.workers = []
        self.releases = []
        self.addCleanup(self.retire_workers)
        self.messages = []
        self.enterContext(mock.patch('desktop.cdj.QMessageBox.warning',
            side_effect=lambda *_args:self.messages.append(_args)))
        self.host = h = SimpleNamespace(_cdj_worker=None, _exporting=True, _cancelling=False,
            _shutting_down=False, _export_batch=None, _dual_sync_queue={},
            _device_coordinator=DeviceOperationCoordinator(),
            _device_mount_tokens={str(f.target):f.token},
            _usb_view_cache={str(f.target):UsbSnapshot(f.target, f.token, None)},
            _usb_view_requests=set(), _current_device_path=str(f.target),
            cancel_btn=mock.Mock(), progress=mock.Mock(),
            _stop_pulse=mock.Mock(), _start_pulse=mock.Mock(), _set_device_progress=mock.Mock(),
            _sync_cue_editing=mock.Mock(), _flash_status=mock.Mock(),
            _update_device_action_state=mock.Mock(), _export_target_result=mock.Mock(),
            _maybe_finish_export_target=mock.Mock(), _restore_edit_status=mock.Mock(),
            _present_usb_device=mock.Mock(), _on_device_phase_progress=mock.Mock())
        for name in ('_start_device_worker', '_finish_worker', '_finish_device_result_worker'):
            setattr(h, name, MethodType(getattr(MainWindow, name), h))
        for name in ('_review_cdj_plan', '_finish_cdj'):
            setattr(h, name, MethodType(getattr(CdjDeviceMixin, name), h))

    def retire_workers(self):
        self.host._shutting_down = True
        for release in self.releases:release.set()
        for worker in self.workers:
            try:
                worker.cancel();worker.wait(3000)
            except RuntimeError:
                pass
        self.app.processEvents()

    def until(self, predicate):
        deadline = monotonic() + 4
        while not predicate():
            self.app.processEvents()
            self.assertLess(monotonic(), deadline, 'Generated CDJ worker did not retire')
            sleep(.001)
        self.app.processEvents()

    def start(self, operation, *, cancelled=False, review=False):
        worker = CdjWorker(operation)
        self.workers.append(worker)
        self.host._cdj_worker = worker
        if cancelled:worker.cancel()
        worker.finished.connect(lambda:self.host._finish_device_result_worker('_cdj_worker', worker,
            lambda result:self.host._review_cdj_plan(str(self.fixture.target), result, worker.stop.is_set())
            if review else self.host._finish_cdj(str(self.fixture.target), result)))
        self.host._start_device_worker(worker, str(self.fixture.target), 'plan-cdj' if review else 'commit-cdj')
        return worker

    def publish(self, plan):
        self.host._review_cdj_plan(str(self.fixture.target), plan)
        worker = self.host._cdj_worker
        self.workers.append(worker)
        return worker

    def finished(self):
        self.until(lambda:self.host._cdj_worker is None)
        self.assertIsNone(self.host._device_coordinator.active)
        self.assertFalse(self.host._exporting)
        self.assertFalse(self.host._cancelling)

    def source_audio(self):
        source = self.fixture.root/'source.wav'
        source.write_bytes(b'generated source audio\n' * 150_000)
        raw = source.read_bytes()
        (self.fixture.candidate/'Contents/Artist/track.wav').write_bytes(raw)
        return source, raw

    @contextmanager
    def held_read(self, wanted):
        raw_open = Path.open
        entered, release = Event(), Event()
        self.releases.append(release)
        reads = []
        def opened(path, *args, **options):
            result = raw_open(path, *args, **options)
            if wanted(path) and args and args[0] == 'rb':
                return _HeldRead(result, entered, release, reads)
            return result
        with mock.patch.object(Path, 'open', opened):
            yield entered, release, reads

    def assert_quiet_cancel(self):
        self.assertFalse(self.messages)
        message = self.host._flash_status.call_args.args[0]
        self.assertIn('cancelled', message)
        self.assertNotIn('complete', message.lower())
        self.assertNotIn('nothing written', message.lower())

    def test_actual_planning_cancel_is_typed_quiet_and_writes_nothing(self):
        self.start(lambda cancel, _progress:self.fixture.plan(should_cancel=cancel),
                   cancelled=True, review=True)
        self.finished()
        self.assert_quiet_cancel()
        self.assertTrue(self.host._device_coordinator.last_result.cancelled)
        self.fixture.assert_old_generation()
        self.assertFalse((self.fixture.target/'.soundbox-cdj').exists())

    def test_successful_plan_cancelled_before_delivery_closes_workspace(self):
        workspace = tempfile.TemporaryDirectory(prefix='soundbox-cdj-cancel-')
        folder = Path(workspace.name)
        plan = self.fixture.plan(workspace=workspace)
        self.host._review_cdj_plan(str(self.fixture.target), plan, cancelled=True)
        self.assertFalse(folder.exists())
        self.assertIsNone(plan.workspace)
        self.assertIsNone(self.host._cdj_worker)
        self.assertFalse(self.host._exporting)
        self.assert_quiet_cancel()
        self.fixture.assert_old_generation()

    def test_cancel_before_commit_has_no_journal_and_keeps_sources(self):
        source, raw = self.source_audio()
        plan = self.fixture.plan()
        self.start(lambda cancel, progress:tx.commit(plan, should_cancel=cancel, on_progress=progress),
                   cancelled=True)
        self.finished()
        self.assert_quiet_cancel()
        self.assertTrue(self.host._device_coordinator.last_result.cancelled)
        self.assertEqual(source.read_bytes(), raw)
        self.fixture.assert_old_generation()

    def test_mid_copy_cancel_rolls_back_native_catalogs_and_preserves_sources(self):
        source, raw = self.source_audio()
        plan = self.fixture.plan()
        audio = self.fixture.candidate/'Contents/Artist/track.wav'
        previous = self.host._usb_view_cache[str(self.fixture.target)]
        with self.held_read(lambda path:path == audio) as (entered, release, reads):
            worker = self.publish(plan)
            self.until(entered.is_set)
            worker.cancel();self.host._cancelling = True;release.set()
            self.finished()
        self.assertEqual(reads, [1024 * 1024])
        self.assert_quiet_cancel()
        self.assertEqual(source.read_bytes(), raw)
        self.fixture.assert_old_generation()
        self.assertIs(self.host._usb_view_cache[str(self.fixture.target)], previous)
        result = self.host._device_coordinator.last_result
        self.assertTrue(result.cancelled)
        self.assertFalse(result.committed)
        self.assertEqual(result.recovery, 'rolled back')

    def test_cancel_after_one_copy_keeps_reusable_audio_without_false_nothing_written(self):
        source, raw = self.source_audio()
        plan = self.fixture.plan()
        worker = None
        def operation(cancel, _progress):
            return tx.commit(plan, should_cancel=cancel,
                on_progress=lambda *_args:worker.cancel())
        worker = self.start(operation)
        self.finished()
        self.assert_quiet_cancel()
        self.fixture.assert_old_generation(reusable=True)
        self.assertEqual((self.fixture.target/'Contents/Artist/track.wav').read_bytes(), raw)
        self.assertEqual(source.read_bytes(), raw)

    def test_late_cancel_during_retirement_hash_keeps_commit_and_resumable_cleanup(self):
        f = self.fixture
        removed = 'Contents/removed.wav'
        old_audio = b'generated removed audio\n' * 100_000
        f.put(f.target/removed, old_audio)
        plan = f.plan(removed_files=(removed,))
        backup = f.target/'.soundbox-cdj/transactions'/plan.manifest['generation']/removed
        with self.held_read(lambda path:path == backup) as (entered, release, reads):
            worker = self.publish(plan)
            self.until(entered.is_set)
            worker.cancel();release.set()
            self.finished()
        self.assertEqual(reads, [1024 * 1024])
        self.assertFalse(self.messages)
        self.assertTrue(self.host._device_coordinator.last_result.committed)
        self.assertEqual(self.host._device_coordinator.last_result.recovery, 'committed')
        self.assertEqual((f.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))
        self.assertEqual(backup.read_bytes(), old_audio)
        self.assertTrue((f.target/tx.JOURNAL).is_file())
        self.assertFalse((f.target/removed).exists())
        self.assertIn('cleanup pending', self.host._flash_status.call_args.args[0])
        self.assertNotIn('complete', self.host._flash_status.call_args.args[0].lower())
        native = {path:(f.target/path).read_bytes() for path in tx.DATABASE_FILES}
        cancelled = Event();cancelled.set()
        with operation_scope(cancelled):
            tx.recover(f.session, f.root/'receipts')
        self.assertTrue(cancelled.is_set())
        self.assertFalse(backup.exists())
        self.assertFalse((f.target/tx.JOURNAL).exists())
        self.assertEqual({path:(f.target/path).read_bytes() for path in tx.DATABASE_FILES}, native)
        self.assertEqual((f.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))

    def test_cancel_between_cleanup_unlinks_retains_remaining_claim_and_committed_journal(self):
        f = self.fixture
        removed = ('Contents/removed-a.wav', 'Contents/removed-b.wav')
        for path in removed:f.put(f.target/path, path.encode())
        plan = f.plan(removed_files=removed)
        cancel = Event()
        def progress(done, _total, _path):
            if done == len(plan.changes) + 1:cancel.set()
        with self.assertRaises(tx.CommittedCleanupPending) as caught:
            tx.commit(plan, should_cancel=cancel.is_set, on_progress=progress)
        self.assertTrue(caught.exception.cleanup_cancelled)
        folder = f.target/'.soundbox-cdj/transactions'/plan.manifest['generation']
        self.assertFalse((folder/removed[0]).exists())
        self.assertEqual((folder/removed[1]).read_bytes(), removed[1].encode())
        self.assertTrue((f.target/tx.JOURNAL).exists())
        tx.recover(f.session, f.root/'receipts')
        self.assertFalse((folder/removed[1]).exists())
        self.assertFalse((f.target/tx.JOURNAL).exists())

    def test_cancel_during_manifest_publication_finishes_saved_journal_before_cleanup(self):
        f = self.fixture
        plan = f.plan();cancel = Event()
        actual = tx.CdjSession.atomic_write
        def written(session, path, data):
            value = actual(session, path, data)
            if path == f.target/tx.MANIFEST:cancel.set()
            return value
        with operation_scope(cancel), mock.patch.object(tx.CdjSession, 'atomic_write', written), \
                self.assertRaises(tx.CommittedCleanupPending) as caught:
            tx.commit(plan, should_cancel=cancel.is_set)
        self.assertTrue(caught.exception.cleanup_cancelled)
        self.assertEqual((f.target/tx.MANIFEST).read_bytes(), tx._json(plan.manifest))
        self.assertIn(b'"state":"committed"', (f.target/tx.JOURNAL).read_bytes())
        with operation_scope(cancel):tx.recover(f.session, f.root/'receipts')
        self.assertFalse((f.target/tx.JOURNAL).exists())

    def test_executing_recovery_restores_exact_native_bytes_despite_cancelled_context(self):
        f = self.fixture
        source, raw = self.source_audio()
        plan = f.plan()
        def interrupted(*_args):raise fixtures.SimulatedCrash()
        with self.assertRaises(fixtures.SimulatedCrash):tx.commit(plan, on_progress=interrupted)
        self.assertTrue((f.target/tx.JOURNAL).is_file())
        stopped = Event();stopped.set()
        with operation_scope(stopped):tx.recover(f.session, f.root/'receipts')
        self.assertTrue(stopped.is_set())
        f.assert_old_generation()
        self.assertEqual(source.read_bytes(), raw)

    def test_explicit_acquisition_and_native_read_cancel_keep_typed_contract(self):
        source = self.fixture.root/'source.wav';source.write_bytes(b'generated audio')
        with self.assertRaises(OperationCancelled) as caught:
            with acquire_source(SourceContract(source), self.fixture.root/'capture',
                                should_cancel=lambda:True):
                self.fail('Cancelled source capture was admitted')
        self.assertIsInstance(caught.exception, SourceAcquisitionError)
        self.assertEqual(source.read_bytes(), b'generated audio')
        self.assertEqual(list((self.fixture.root/'capture').iterdir()), [])
        library = SimpleNamespace(tracks_by_id={}, source_library_id='42', playlists=[],
                                  source=self.fixture.root/'desktop/generated-source.db')
        with self.assertRaises(OperationCancelled):
            cdj_export.plan_export(library, [], self.fixture.target, token=self.fixture.token,
                volume_uuid=self.fixture.volume, should_cancel=lambda:True,
                recovery_root=self.fixture.root/'receipts')
        self.fixture.assert_old_generation()

    def test_suppressed_disconnected_plan_retires_flags_and_closes_its_workspace(self):
        workspace = tempfile.TemporaryDirectory(prefix='soundbox-cdj-disconnected-')
        folder = Path(workspace.name)
        plan = self.fixture.plan(workspace=workspace)
        release, entered = Event(), Event();self.releases.append(release)
        def operation(_cancel, _progress):
            entered.set();release.wait(3)
            return plan
        self.start(operation, review=True)
        self.until(entered.is_set)
        self.host._device_coordinator.disconnect(str(self.fixture.target))
        release.set();self.finished()
        self.assertFalse(folder.exists())
        self.assertIsNone(plan.workspace)
        self.host._flash_status.assert_not_called()
        self.fixture.assert_old_generation()

    def test_obsolete_cdj_retirement_preserves_newer_worker_and_progress(self):
        h = self.host
        old = CdjWorker(lambda *_args:None)
        plan = self.fixture.plan()
        old.result = plan
        newer = object()
        h._cdj_worker = newer
        h._device_coordinator.begin(
            DeviceCommand(DeviceSessionIdentity('', 0), 'commit-cdj'), newer, Phase.EXECUTING)
        h._exporting = True;h._cancelling = True
        h._finish_device_result_worker('_cdj_worker', old, mock.Mock())
        self.assertIs(h._cdj_worker, newer)
        self.assertIs(h._device_coordinator.active.worker, newer)
        self.assertTrue(h._exporting)
        self.assertTrue(h._cancelling)
        h._stop_pulse.assert_not_called()
