"""Waveform peaks cache: pure accelerator — correct hits, silent misses."""

import tempfile
import unittest
from pathlib import Path

from soundbox.waveform import cache_key, cache_load, cache_store


class WaveCache(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sb-wcache-"))
        self.audio = self.dir / "track.wav"
        self.audio.write_bytes(b"fake-audio-bytes")
        self.cache = self.dir / "waveforms"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip(self):
        pk = [0.1234, 0.5, 1.0]
        cache_store(self.cache, self.audio, pk)
        self.assertEqual(cache_load(self.cache, self.audio), [0.123, 0.5, 1.0])

    def test_empty_peaks_never_cached(self):
        cache_store(self.cache, self.audio, [])
        self.assertIsNone(cache_load(self.cache, self.audio))

    def test_modified_file_misses(self):
        cache_store(self.cache, self.audio, [1.0])
        import os
        st = self.audio.stat()
        self.audio.write_bytes(b"different-audio-bytes!!")
        os.utime(self.audio, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        self.assertIsNone(cache_load(self.cache, self.audio))

    def test_corrupt_entry_is_a_miss(self):
        cache_store(self.cache, self.audio, [1.0])
        (self.cache / f"{cache_key(self.audio)}.json").write_text("{broken")
        self.assertIsNone(cache_load(self.cache, self.audio))

    def test_missing_everything_is_quietly_none(self):
        self.assertIsNone(cache_load(self.cache / "nope", self.dir / "ghost.wav"))
        cache_store(self.cache, self.dir / "ghost.wav", [1.0])  # must not raise
