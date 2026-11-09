"""Neutral engine helpers preserve isolation and source-admission boundaries."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox.cache_io import atomic_cache_bytes
from soundbox.native_transaction import NativeTransactionLease
from soundbox.rekordbox_write import WriteProofError


class SharedEngineInfrastructure(unittest.TestCase):
    def test_source_verification_import_does_not_initialize_device_backend(self):
        code = '''
import sys
from soundbox.source_verification import SourceVerificationCache
assert 'soundbox.rockbox_media' not in sys.modules
assert 'soundbox.stock_ipod' not in sys.modules
assert 'soundbox.dual_ipod' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failed_cache_replace_keeps_previous_bytes_and_retires_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            atomic_cache_bytes(path, b'previous')
            with mock.patch('soundbox.cache_io.os.replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    atomic_cache_bytes(path, b'candidate')
            self.assertEqual(path.read_bytes(), b'previous')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_legacy_cache_temporary_symlink_never_changes_outside_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, original = root / 'cache.json', root / 'original.wav'
            original.write_bytes(b'protected original')
            stale = path.with_name(f'.{path.name}.{os.getpid()}.sbpart')
            stale.symlink_to(original)
            atomic_cache_bytes(path, b'new cache')
            self.assertEqual(original.read_bytes(), b'protected original')
            self.assertTrue(stale.is_symlink())
            self.assertEqual(path.read_bytes(), b'new cache')

    def test_cache_publication_does_not_follow_a_replaced_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, outside = root / 'cache', root / 'outside'
            parent.mkdir(); outside.mkdir()
            original = outside / 'entry.json'; original.write_bytes(b'outside original')
            replace = os.replace
            def redirect_then_replace(source, target, **kwargs):
                parent.rename(root / 'captured-cache')
                parent.symlink_to(outside, target_is_directory=True)
                replace(source, target, **kwargs)
            with mock.patch('soundbox.cache_io.os.replace', side_effect=redirect_then_replace):
                atomic_cache_bytes(parent / 'entry.json', b'captured cache')
            self.assertEqual(original.read_bytes(), b'outside original')
            self.assertEqual((root / 'captured-cache/entry.json').read_bytes(), b'captured cache')

    def test_final_guard_runs_after_staging_and_can_abort_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'entry.json'; path.write_bytes(b'previous')
            def changed_source():
                stages = list(root.glob('*.sbpart'))
                self.assertEqual(len(stages), 1)
                self.assertEqual(stages[0].read_bytes(), b'candidate')
                self.assertEqual(path.read_bytes(), b'previous')
                raise OSError('source changed')
            with self.assertRaisesRegex(OSError, 'source changed'):
                atomic_cache_bytes(path, b'candidate', before_replace=changed_source)
            self.assertEqual(path.read_bytes(), b'previous')
            self.assertEqual(list(root.iterdir()), [path])

    def test_optional_sync_preserves_disposable_waveform_latency_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'record.sba5'
            with mock.patch('soundbox.cache_io.os.fsync') as sync:
                atomic_cache_bytes(path, b'unsynced cache', sync_file=False)
                sync.assert_not_called()
                atomic_cache_bytes(path, b'synced receipt')
                sync.assert_called_once()
            self.assertEqual(path.read_bytes(), b'synced receipt')

    def test_valid_long_cache_filename_does_not_overflow_its_temporary_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('x' * 245 + '.json')
            atomic_cache_bytes(path, b'complete cache')
            self.assertEqual(path.read_bytes(), b'complete cache')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_shared_native_lease_has_no_cue_publisher_or_cue_module_dependency(self):
        code = '''
import sys
from soundbox.native_transaction import NativeTransactionLease
assert 'soundbox.rekordbox_cue_store' not in sys.modules
assert not hasattr(NativeTransactionLease, 'apply')
assert not hasattr(NativeTransactionLease, '_recover')
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lease_rechecks_native_app_after_acquiring_shared_lock(self):
        class Lease(NativeTransactionLease):
            checks = 0

            def _native_version(self):
                return 'accepted-version'

            def _assert_native_closed(self):
                self.checks += 1
                if self.checks > 1:
                    raise WriteProofError('native app opened while acquiring lock')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            database = root / 'source' / 'master.db'
            database.parent.mkdir()
            database.write_bytes(b'synthetic unopened source')
            authority = SimpleNamespace(database=str(database), library_id='library',
                                        schema_sha256='0' * 64,
                                        application_version='accepted-version')
            lease = Lease(authority, root / 'recovery')
            with self.assertRaisesRegex(WriteProofError, 'opened while acquiring lock'):
                with lease._lock():
                    self.fail('Source operation admitted after native app opened')
            self.assertEqual(database.read_bytes(), b'synthetic unopened source')
