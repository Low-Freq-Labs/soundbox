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
        self.root = Path(self.temp.name)
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
