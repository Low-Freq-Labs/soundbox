"""Disposable catalog witness never claims source bytes or device ownership."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox.stock_ipod_catalog_state import CatalogWitnessRow, StockCatalogWitness, StockCatalogWitnessStore
from soundbox.stock_ipod_playlist_binding import PlaylistBinding


class CatalogWitnessTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        self.root = Path(area.name) / 'cache'
        self.store = StockCatalogWitnessStore(self.root)
        row = CatalogWitnessRow('local', 'native', 'uuid', '/unavailable/source.wav', 101, 1,
            ':iPod_Control:Music:F00:101.wav', (('title', 'Title'), ('artist', 'Artist'), ('album', ''),
            ('genre', ''), ('comments', ''), ('year', 2026), ('bpm', 140)), 'cover-key')
        self.witness = StockCatalogWitness(('/unavailable/library.db', 'rekordbox-db', '42'),
            'volume-library', 'a' * 64, (row,), 'b' * 64)

    def test_actual_atomic_cache_roundtrip_contains_no_media_authority(self):
        self.store.save(self.witness)
        self.assertEqual(self.store.load(self.witness.device_key), self.witness)
        raw = json.loads(self.store.path_for(self.witness.device_key).read_bytes())
        self.assertNotIn('owned', raw['rows'][0]); self.assertNotIn('source_sha256', raw['rows'][0])
        self.assertFalse(Path('/unavailable/source.wav').exists())

    def test_writer_refuses_payload_larger_than_its_reader_contract(self):
        large = replace(self.witness, rows=(replace(self.witness.rows[0],
                        metadata=(('comments', 'x' * 2000),)),))
        with mock.patch('soundbox.stock_ipod_catalog_state.MAX_BYTES', 1024):
            with self.assertRaisesRegex(ValueError, 'supported size'):
                self.store.save(large)
        self.assertFalse(self.root.exists())

    def test_unknown_corrupt_and_foreign_cache_are_misses(self):
        self.store.save(self.witness); path = self.store.path_for(self.witness.device_key)
        for raw in (b'broken', b'{}', replace(self.witness, device_key='another-volume').encode()):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                self.assertIsNone(self.store.load(self.witness.device_key))

    def test_duplicate_correspondence_or_invalid_identity_cannot_be_saved(self):
        for value in (replace(self.witness, rows=self.witness.rows * 2),
                      replace(self.witness, rows=(replace(self.witness.rows[0], source_path='relative.wav'),))):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.store.save(value)
        self.assertFalse(self.root.exists())

    def test_failed_publication_preserves_previous_disposable_witness(self):
        self.store.save(self.witness)
        path = self.store.path_for(self.witness.device_key)
        before = path.read_bytes()
        with mock.patch('soundbox.state_io.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.store.save(replace(self.witness, database_sha256='c' * 64))
        self.assertEqual(path.read_bytes(), before)

    def test_symlinked_cache_root_is_not_followed(self):
        destination = self.root.parent / 'unrelated'; destination.mkdir()
        self.root.symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.store.save(self.witness)
        self.assertEqual(list(destination.iterdir()), [])

    def test_playlist_binding_roundtrip_retains_duplicate_occurrences_and_no_ownership(self):
        binding = PlaylistBinding('source-playlist', 'native-id', 'native-uuid', 901, 'Favorites',
                                  (('local', 101), ('local', 101)))
        witness = replace(self.witness, bindings=(binding,))
        self.store.save(witness)
        self.assertEqual(self.store.load(witness.device_key), witness)
        raw = json.loads(witness.encode())
        self.assertEqual(raw['bindings'][0]['device_playlist_id'], 901)
        self.assertNotIn('owned', raw['bindings'][0])
        self.assertNotIn('source_sha256', raw['bindings'][0])

    def test_legacy_witness_without_playlist_bindings_remains_readable(self):
        raw = json.loads(self.witness.encode()); del raw['bindings']
        self.assertEqual(StockCatalogWitness.decode(json.dumps(raw).encode()), self.witness)

    def test_duplicate_binding_keys_and_ambiguous_song_bindings_are_rejected(self):
        binding = PlaylistBinding('source-playlist', '', '', 901, 'Favorites', (('local', 101),))
        for bindings in ((binding, binding), (replace(binding, members=(('local', 101), ('alias', 101))),)):
            with self.subTest(bindings=bindings):
                with self.assertRaises(ValueError):
                    replace(self.witness, bindings=bindings).encode()


if __name__ == '__main__':
    unittest.main()
