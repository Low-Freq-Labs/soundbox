"""Parallel fresh evidence checks retain cancellation and joined ownership."""
from contextvars import ContextVar, copy_context
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest import mock

from soundbox import native_transaction as native, operation_control as control
from soundbox.rekordbox_playlist_store import _sha
from soundbox.rekordbox_write import RecoveryRequired, WriteProofError


class NativeEvidenceHashes(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.paths = [self.root / name for name in ('before.db', 'after.db')]
        for index, path in enumerate(self.paths):
            path.write_bytes(bytes([index]) * (1024 * 1024))

    def test_pair_results_and_fresh_same_size_restored_mtime_edits(self):
        expected = [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths]
        self.assertEqual(native.hash_evidence(self.paths, _sha), expected)
        path = self.paths[0]; before = path.stat()
        with path.open('r+b') as handle:
            handle.seek(512 * 1024); handle.write(b'changed!')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        actual = native.hash_evidence(self.paths, _sha)
        self.assertNotEqual(actual[0], expected[0])
        self.assertEqual(actual, [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths])

    def test_small_receipts_stay_serial_without_executor_setup(self):
        for path in self.paths:path.write_bytes(b'small XML or receipt')
        with mock.patch.object(native, 'ThreadPoolExecutor', side_effect=AssertionError('Small evidence created threads')):
            self.assertEqual(native.hash_evidence(self.paths, _sha), [_sha(path) for path in self.paths])

    def test_missing_size_hint_defers_to_the_domains_original_hash_error(self):
        self.paths[1].unlink();observed=[]
        def hashing(path):
            observed.append(path)
            if not path.exists():raise RecoveryRequired('Domain-specific missing evidence')
            return _sha(path)
        with mock.patch.object(native,'ThreadPoolExecutor',side_effect=AssertionError('Missing hint created threads')):
            with self.assertRaisesRegex(RecoveryRequired,'Domain-specific missing evidence'):
                native.hash_evidence(self.paths,hashing)
        self.assertEqual(observed,self.paths)

    def test_failure_joins_the_peer_before_returning_or_releasing_ownership(self):
        peer_started, release_peer, returned, peer_finished = (Event() for _ in range(4))
        errors = []
        def hashing(path):
            if path == self.paths[0]:
                if not peer_started.wait(5):raise RuntimeError('Peer did not start')
                raise RecoveryRequired('Generated evidence failure')
            peer_started.set()
            if not release_peer.wait(5):raise RuntimeError('Peer was not released')
            peer_finished.set()
            return _sha(path)
        def owner():
            try:native.hash_evidence(self.paths, hashing)
            except BaseException as error:errors.append(error)
            finally:returned.set()
        thread = Thread(target=owner)
        thread.start()
        try:
            self.assertTrue(peer_started.wait(5))
            self.assertFalse(returned.wait(.05))
        finally:
            release_peer.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(peer_finished.is_set())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RecoveryRequired)

    def test_named_replacement_during_hash_cannot_match_a_stale_descriptor(self):
        old = self.paths[0].stat(); original = hashlib.file_digest
        replacement = self.root / 'replacement.db';replacement.write_bytes(b'foreign' * 100)
        def replace_after_read(stream, *args, **kwargs):
            result = original(stream, *args, **kwargs)
            if os.fstat(stream.fileno()).st_ino == old.st_ino:
                replacement.replace(self.paths[0])
            return result
        with mock.patch('hashlib.file_digest', side_effect=replace_after_read):
            with self.assertRaisesRegex(RecoveryRequired, 'during hashing'):
                native.hash_evidence(self.paths, _sha)

    def test_each_worker_inherits_cancellation_without_leaking_the_scope(self):
        stop = Event();stop.set(); observed = []
        def hashing(path):
            observed.append(path)
            control.checkpoint()
            raise AssertionError('Canceled evidence reached bytes')
        with control.operation_scope(stop):
            with self.assertRaises(control.OperationCancelled):
                native.hash_evidence(self.paths, hashing)
        self.assertCountEqual(observed, self.paths)
        self.assertFalse(control.cancellation_enabled())

    def test_copied_contexts_keep_progress_metrics_peaks_and_disabled_recovery(self):
        stop = Event();stop.set();progress = [];scope = ContextVar('hash_test_scope', default='outside')
        token = scope.set('captured')
        self.addCleanup(scope.reset, token)
        def hashing(path):
            self.assertEqual(scope.get(), 'captured')
            control.checkpoint()
            for index in range(500):
                control.record_metric('chunks')
                control.record_metric('peak', index + (500 if path == self.paths[1] else 0), peak=True)
                if index % 32 == 0:time.sleep(0)
            control.phase_progress('Evidence', 1, 1, path.name)
            return _sha(path)
        with control.operation_scope(stop, lambda *args:progress.append(args)):
            with control.cancellation_disabled():
                results = native.hash_evidence(self.paths, hashing)
            self.assertEqual(control._metrics.get(), {'chunks': 1000, 'peak': 999})
            with self.assertRaises(control.OperationCancelled):control.checkpoint()
        self.assertEqual(results, [_sha(path) for path in self.paths])
        self.assertCountEqual(progress, [('Evidence', 1, 1, path.name) for path in self.paths])
        self.assertIsNone(control._metrics_lock.get())


class NativeEvidenceLease(unittest.TestCase):
    setUp = NativeEvidenceHashes.setUp

    def test_private_evidence_is_hashed_once_per_exclusive_lease(self):
        expected = [_sha(path) for path in self.paths]
        hashing = mock.Mock(wraps=_sha)
        with native._private_evidence_session(self.root):
            self.assertEqual(native.hash_evidence(self.paths, hashing), expected)
            self.assertEqual(native.hash_evidence(self.paths, hashing), expected)
            self.assertEqual(hashing.call_count, 2)
        native.hash_evidence(self.paths, hashing)
        self.assertEqual(hashing.call_count, 4)
        with native._private_evidence_session(self.root):
            native.hash_evidence(self.paths, hashing)
            self.assertEqual(hashing.call_count, 6)

    def test_restored_mtime_tampering_rehashes_and_exposes_only_changed_payload(self):
        calls = []
        def hashing(path):
            calls.append(path)
            return _sha(path)
        with native._private_evidence_session(self.root):
            expected = native.hash_evidence(self.paths, hashing)
            before = self.paths[0].stat()
            with self.paths[0].open('r+b') as handle:
                handle.seek(123); handle.write(b'changed')
            os.utime(self.paths[0], ns=(before.st_atime_ns, before.st_mtime_ns))
            observed = native.hash_evidence(self.paths, hashing)
            self.assertNotEqual(observed[0], expected[0])
            self.assertEqual(observed[1], expected[1])
            self.assertEqual(calls.count(self.paths[0]), 2)
            self.assertEqual(calls.count(self.paths[1]), 1)

    def test_replacement_symlink_and_missing_payload_never_reuse_old_proof(self):
        with native._private_evidence_session(self.root):
            expected = native.hash_evidence(self.paths, _sha)
            replacement = self.root / 'new.db'
            replacement.write_bytes(b'foreign' * 100)
            replacement.replace(self.paths[0])
            self.assertNotEqual(native.hash_evidence(self.paths, _sha)[0], expected[0])
            self.paths[0].unlink()
            self.paths[0].symlink_to(self.paths[1])
            with self.assertRaises(WriteProofError):native.hash_evidence(self.paths, _sha)
            self.paths[0].unlink()
            with self.assertRaises(OSError):native.hash_evidence(self.paths, _sha)

    def test_private_parent_replacement_forces_full_verification(self):
        archive = self.root / 'archive';archive.mkdir(mode=0o700)
        paths = [archive / path.name for path in self.paths]
        for source, target in zip(self.paths, paths):target.write_bytes(source.read_bytes())
        calls = []
        def hashing(path):calls.append(path);return _sha(path)
        with native._private_evidence_session(self.root):
            expected = native.hash_evidence(paths, hashing)
            archive.rename(self.root / 'old')
            archive.mkdir(mode=0o700)
            for target in paths:(self.root / 'old' / target.name).rename(target)
            self.assertEqual(native.hash_evidence(paths, hashing), expected)
            self.assertEqual(len(calls), 4)

    def test_source_or_nonprivate_paths_are_never_reused(self):
        outside = self.root / 'outside';outside.mkdir(mode=0o700)
        private = self.root / 'private';private.mkdir(mode=0o700)
        paths = [outside / path.name for path in self.paths]
        for source, target in zip(self.paths, paths):target.write_bytes(source.read_bytes())
        calls = []
        def hashing(path):calls.append(path);return _sha(path)
        with native._private_evidence_session(private):
            native.hash_evidence(paths, hashing);native.hash_evidence(paths, hashing)
        self.assertEqual(len(calls), 4)
        outside.chmod(0o755);calls.clear()
        with native._private_evidence_session(self.root):
            native.hash_evidence(paths, hashing);native.hash_evidence(paths, hashing)
        self.assertEqual(len(calls), 4)

    def test_exception_and_copied_context_cannot_keep_a_finished_lease_alive(self):
        calls = []
        def hashing(path):calls.append(path);return _sha(path)
        with self.assertRaisesRegex(RuntimeError, 'generated'):
            with native._private_evidence_session(self.root):
                native.hash_evidence(self.paths, hashing)
                context = copy_context()
                raise RuntimeError('generated')
        context.run(native.hash_evidence, self.paths, hashing)
        self.assertEqual(len(calls), 4)
        self.assertIsNone(native._evidence_session.get())

    def test_cached_evidence_still_honors_cancellation_and_disabled_recovery(self):
        stop = Event()
        with control.operation_scope(stop), native._private_evidence_session(self.root):
            expected = native.hash_evidence(self.paths, _sha)
            stop.set()
            with self.assertRaises(control.OperationCancelled):native.hash_evidence(self.paths, _sha)
            with control.cancellation_disabled():
                self.assertEqual(native.hash_evidence(self.paths, _sha), expected)

    def test_actual_lock_lifetime_expires_evidence_between_operations(self):
        paths = self.paths
        calls = []
        def hashing(path):calls.append(path);return _sha(path)
        class Lease(native.NativeTransactionLease):
            def _guard(self):pass
        lease = object.__new__(Lease);lease.root = self.root;lease.database = paths[0]
        with lease._lock():
            native.hash_evidence(paths, hashing);native.hash_evidence(paths, hashing)
            self.assertEqual(len(calls), 2)
        self.assertIsNone(native._evidence_session.get())
        with lease._lock():native.hash_evidence(paths, hashing)
        self.assertEqual(len(calls), 4)

    def test_different_source_filesystem_and_changed_hasher_do_not_reuse(self):
        calls = []
        def hashing(path):calls.append(path);return _sha(path)
        with native._private_evidence_session(self.root, source_device=self.root.stat().st_dev+1):
            native.hash_evidence(self.paths, hashing);native.hash_evidence(self.paths, hashing)
        self.assertEqual(len(calls), 4)
        other = mock.Mock(wraps=_sha)
        with native._private_evidence_session(self.root):
            native.hash_evidence(self.paths, hashing)
            native.hash_evidence(self.paths, other)
        self.assertEqual(other.call_count, 2)

    def test_new_hardlink_cannot_reuse_previously_regular_payload(self):
        with native._private_evidence_session(self.root):
            native.hash_evidence(self.paths, _sha)
            os.link(self.paths[0], self.root/'unapproved-hardlink')
            with self.assertRaises(WriteProofError):native.hash_evidence(self.paths, _sha)
