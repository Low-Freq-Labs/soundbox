"""Resume proofs only for the exact stable source that was fully read."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox.source_verification import SourceVerificationCache


class SourceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"original audio")
        self.cache_root = self.root / "cache"
        self.hash_file = mock.Mock(side_effect=lambda p: hashlib.sha256(p.read_bytes()).hexdigest())

    def test_resume_after_later_failure_without_rereading_audio(self):
        first = SourceVerificationCache(self.cache_root)
        digest, _ = first.verify(self.source, self.hash_file)
        # Re-create the cache as after application restart / failed planning.
        second = SourceVerificationCache(self.cache_root)
        self.assertEqual(second.verify(self.source, self.hash_file)[0], digest)
        self.assertEqual(self.hash_file.call_count, 1)
        self.assertEqual(second.hits, 1)
        self.assertEqual(second.bytes_read, 0)

    def test_same_size_edit_with_preserved_mtime_invalidates_proof(self):
        cache = SourceVerificationCache(self.cache_root)
        old, before = cache.verify(self.source, self.hash_file)
        self.source.write_bytes(b"modified audio")
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        new, _ = cache.verify(self.source, self.hash_file)
        self.assertNotEqual(new, old)
        self.assertEqual(self.hash_file.call_count, 2)

    def test_malformed_proof_is_a_cache_miss(self):
        cache = SourceVerificationCache(self.cache_root)
        cache.verify(self.source, self.hash_file)
        path = next(cache.root.glob("*.json"))
        for payload in ("null", "[]", "{", '{"sha256": 42}'):
            path.write_text(payload)
            cache.verify(self.source, self.hash_file)
        self.assertEqual(self.hash_file.call_count, 5)

    def test_unreadable_source_never_publishes_proof(self):
        cache = SourceVerificationCache(self.cache_root)
        with self.assertRaises(OSError):
            cache.verify(self.source, mock.Mock(side_effect=OSError(81, "Need authenticator")))
        self.assertFalse(cache.root.exists())

    def test_continuously_changing_source_never_publishes_proof(self):
        cache = SourceVerificationCache(self.cache_root)

        def changing(path):
            digest = self.hash_file(path)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            return digest

        with self.assertRaisesRegex(RuntimeError, "Source changed while verifying"):
            cache.verify(self.source, changing)
        self.assertEqual(self.hash_file.call_count, 2)
        self.assertFalse(cache.root.exists())

    def _cloud_source(self):
        self.enterContext(mock.patch('soundbox.source_read_retry.sys.platform', 'darwin'))
        self.enterContext(mock.patch('soundbox.source_read_retry._CLOUD_STORAGE', self.root))
        return self.enterContext(mock.patch('soundbox.source_read_retry._wait'))

    def test_waking_provider_reopens_source_and_proves_complete_current_bytes(self):
        wait = self._cloud_source()
        cache = SourceVerificationCache(self.cache_root)
        calls = []

        def waking(path):
            calls.append(path.read_bytes())
            if len(calls) == 1:
                path.write_bytes(b'current audio after provider wake')
                raise OSError(81, 'Need authenticator')
            return self.hash_file(path)

        digest, stat = cache.verify(self.source, waking)
        self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(stat.st_size, len(b'current audio after provider wake'))
        self.assertEqual(len(calls), 2)
        wait.assert_called_once()
        self.assertEqual(SourceVerificationCache(self.cache_root).verify(
            self.source, mock.Mock(side_effect=AssertionError('proof was not saved')))[0], digest)

    def test_persistent_provider_auth_error_is_bounded_and_never_saves_proof(self):
        wait = self._cloud_source()
        denied = mock.Mock(side_effect=OSError(81, 'Need authenticator'))
        with self.assertRaises(OSError):
            SourceVerificationCache(self.cache_root).verify(self.source, denied)
        self.assertEqual(denied.call_count, 3)
        self.assertEqual(wait.call_count, 2)
        self.assertFalse(self.cache_root.exists())

    def test_provider_wait_cancellation_prevents_read_retry_and_proof(self):
        from threading import Event
        from soundbox.operation_control import OperationCancelled, operation_scope
        self.enterContext(mock.patch('soundbox.source_read_retry.sys.platform', 'darwin'))
        self.enterContext(mock.patch('soundbox.source_read_retry._CLOUD_STORAGE', self.root))
        stop = Event()
        denied = mock.Mock(side_effect=OSError(81, 'Need authenticator'))
        with operation_scope(stop), mock.patch('soundbox.source_read_retry.time.sleep',
                                              side_effect=lambda _seconds: stop.set()):
            with self.assertRaises(OperationCancelled):
                SourceVerificationCache(self.cache_root).verify(self.source, denied)
        denied.assert_called_once()
        self.assertFalse(self.cache_root.exists())

    def test_local_and_permission_failures_are_not_provider_retries(self):
        for cloud, error in ((False, OSError(81, 'Need authenticator')),
                             (True, PermissionError(13, 'Permission denied'))):
            with self.subTest(cloud=cloud), \
                    mock.patch('soundbox.source_read_retry.sys.platform', 'darwin'), \
                    mock.patch('soundbox.source_read_retry._CLOUD_STORAGE',
                               self.root if cloud else self.root / 'different'), \
                    mock.patch('soundbox.source_read_retry._wait') as wait:
                denied = mock.Mock(side_effect=error)
                with self.assertRaises(OSError):
                    SourceVerificationCache(self.cache_root).verify(self.source, denied)
                denied.assert_called_once()
                wait.assert_not_called()
