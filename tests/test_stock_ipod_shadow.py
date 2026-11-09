"""Mac-side stock-iPod shadow persistence tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from soundbox.stock_ipod_shadow import StockIpodShadowStore


class StockIpodShadowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StockIpodShadowStore(self.root)

    def test_committed_round_trip_is_identity_scoped_and_generational(self):
        common = dict(
            identity_key="stock-ipod-v1:volume:0123456789ABCDEF",
            model="MA446",
            serial_number="SERIAL-V9M",
            database_sha256="a" * 64,
            artwork_database_sha256="b" * 64,
            ithmb={"F1028_1.ithmb": {"size": 20_000, "mtime_ns": 12}},
            sources={"/music/a.wav": {"dbid": 101}},
            artwork={"101": {"image_id": 100}},
            playlists={"playlist": {"name": "Playlist"}},
        )
        first = self.store.committed(**common)
        second = self.store.committed(**common)
        self.assertEqual(first.generation, 1)
        self.assertEqual(second.generation, 2)
        self.assertEqual(self.store.load(common["identity_key"]), second)
        self.assertIsNone(self.store.load("another-device"))

    def test_corrupt_or_incompatible_shadow_is_disposable(self):
        identity = "stock-ipod-v1:fixture"
        path = self.store.path_for(identity)
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.store.load(identity))
        path.write_text(json.dumps({
            "schema": 999,
            "profile": "future",
            "identity_key": identity,
            "playlists": {},
        }), encoding="utf-8")
        self.assertIsNone(self.store.load(identity))

    def test_malformed_generation_is_a_cache_miss_without_rewriting_evidence(self):
        identity = 'stock-ipod-v1:malformed-counter'
        shadow = self.store.committed(
            identity_key=identity, model='MA446', serial_number='',
            database_sha256='a' * 64, artwork_database_sha256='',
            ithmb={}, sources={}, artwork={}, playlists={})
        path = self.store.path_for(identity)
        for generation in ('bad', [1], {'counter': 1}, float('inf'), float('nan')):
            with self.subTest(generation=generation):
                raw = json.dumps(dict(shadow.as_dict(), generation=generation)).encode()
                path.write_bytes(raw)
                self.assertIsNone(self.store.load(identity))
                self.assertEqual(path.read_bytes(), raw)

    def test_malformed_nested_acceleration_rows_miss_without_rewriting_evidence(self):
        identity = 'stock-ipod-v1:malformed-rows'
        shadow = self.store.committed(
            identity_key=identity, model='MA446', serial_number='',
            database_sha256='a' * 64, artwork_database_sha256='',
            ithmb={}, sources={}, artwork={}, playlists={})
        path = self.store.path_for(identity)
        invalid = (
            ('ithmb', {'F1028_1.ithmb': {'size': 'bad', 'mtime_ns': 1}}),
            ('sources', {'/music/song.wav': {'source_size': float('inf')}}),
            ('sources', {'/music/song.wav': {'dbid': {'id': 1}}}),
            ('artwork', {'bad-id': {'image_id': 1}}),
            ('artwork', {'1': {'offsets': {'1028': 'bad'}}}),
            ('artwork', {'1': {'offsets': [1, 2]}}),
            ('playlists', {'playlist': {'folder_path': None}}),
            ('playlists', {'playlist': {'folder_path': ['Music', 1]}}),
            ('playlists', {'playlist': {'tracks': None}}),
            ('sources', {'/music/song.wav': ['invalid row']}),
        )
        for key, rows in invalid:
            with self.subTest(key=key, rows=rows):
                raw = json.dumps({**shadow.as_dict(), key: rows}).encode()
                path.write_bytes(raw)
                self.assertIsNone(self.store.load(identity))
                self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
