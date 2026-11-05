"""Presentation cache corruption/invalidation never changes canonical state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import io

from soundbox import browser_cache
from soundbox.library import Track


class BrowserCache(unittest.TestCase):
    def test_deep_malformed_cache_falls_back(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'browsercache.json'
            path.write_text('[' * 2000 + '0' + ']' * 2000)
            self.assertIsNone(browser_cache.load(path, 'key'))

    def test_capture_rejects_growth_and_oversize_before_json(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'browsercache.json'
            path.write_bytes(b'{}')
            stream = io.BytesIO(b'{}extra')
            stream.fileno = lambda: 123
            with (mock.patch.object(Path, 'open', return_value=stream),
                  mock.patch.object(browser_cache.os, 'fstat', return_value=path.stat()),
                  mock.patch.object(browser_cache.json, 'loads', side_effect=AssertionError('unbounded parse'))):
                self.assertIsNone(browser_cache.load(path, 'key'))
            with path.open('wb') as handle:
                handle.truncate(64 * 1024**2 + 1)
            with mock.patch.object(browser_cache.json, 'loads', side_effect=AssertionError('oversize parse')):
                self.assertIsNone(browser_cache.load(path, 'key'))

    def test_round_trip_and_corrupt_stale_or_linked_cache_falls_back(self):
        row = Track('1', 'Title', 'Artist', 'file:///missing.wav').to_row()
        rows = {'1': row}
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'browsercache.json'
            self.assertIsNone(browser_cache.load(path, 'key'))
            browser_cache.dump(path, 'key', rows)
            self.assertEqual(browser_cache.load(path, 'key'), rows)
            self.assertIsNone(browser_cache.load(path, 'different-inputs'))
            data = json.loads(path.read_bytes())
            data['rows']['1']['title'] = 'Corrupt'
            path.write_text(json.dumps(data))
            self.assertIsNone(browser_cache.load(path, 'key'))
            path.unlink(); path.symlink_to(Path(area) / 'missing')
            browser_cache.dump(path, 'key', rows)
            self.assertTrue(path.is_symlink())
            self.assertIsNone(browser_cache.load(path, 'key'))

    def test_key_covers_track_metadata_curation_aliases_and_policy(self):
        base = (frozenset({('1', 'Title', 'Artist')}), (), (), (), ('policy',))
        key = browser_cache.fingerprint(base)
        for candidate in (
                (frozenset({('1', 'New', 'Artist')}), *base[1:]),
                (base[0], (('1', 'Override'),), *base[2:]),
                (*base[:2], (('1', 'Title override'),), *base[3:]),
                (*base[:3], (('artist', 'alias'),), base[4]),
                (*base[:4], ('new policy',))):
            self.assertNotEqual(key, browser_cache.fingerprint(candidate))
