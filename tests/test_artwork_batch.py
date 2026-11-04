"""Presentation-only fallback-photo observations stay bounded and fresh."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from soundbox.artwork import ArtCache


class ArtworkBatchStatuses(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        self.cache = ArtCache(self.root / 'art')
        sources = []
        for index, color in enumerate(('red', 'green', 'blue')):
            source = self.root / f'{index}.png'
            Image.new('RGB', (8, 8), color).save(source)
            sources.append(source)
        self.cache.import_default_photos(sources)
        self.ids = [str(index) for index in range(120)]
        self.cache.configure_default_tracks(self.ids)

    def test_shared_pool_is_checked_once_and_matches_single_row_status(self):
        tracks = [*self.ids, *('outside-' + tid for tid in self.ids)]
        self.cache.index.update({'blocked': {'status': 'blocked'}, 'miss': {'status': 'none'},
                                 'cover': {'status': 'ok'}, 'avatar': {'status': 'ok', 'kind': 'avatar'}})
        tracks.extend(('blocked', 'miss', 'cover', 'avatar'))
        expected = {tid: self.cache.status_kind(tid) for tid in tracks}
        checks = []
        original = Path.is_file

        def is_file(path):
            if path.parent == self.cache._defaults_root:
                checks.append(path)
            return original(path)

        with mock.patch.object(Path, 'is_file', is_file):
            self.assertEqual(self.cache.status_kinds(iter(tracks)), expected)
        self.assertEqual(len(checks), 3)
        self.assertEqual(len(set(checks)), 3)

    def test_deleted_and_recreated_photos_are_fresh_on_next_batch_and_single_reads(self):
        self.assertEqual(set(self.cache.status_kinds(self.ids).values()), {'default'})
        saved = {row['path']: row['path'].read_bytes() for row in self.cache.default_photos()}
        for path in saved:
            path.unlink()
        self.assertEqual(set(self.cache.status_kinds(self.ids).values()), {''})
        self.assertIsNone(self.cache.default_image_path(self.ids[0]))
        for path, data in saved.items():
            path.write_bytes(data)
        self.assertEqual(set(self.cache.status_kinds(self.ids).values()), {'default'})
        path = self.cache.default_image_path(self.ids[0])
        self.assertIsNotNone(path)
        for photo in saved:
            photo.unlink()
        self.assertIsNone(self.cache.default_image_path(self.ids[0]))

    def test_all_real_covers_skip_the_default_pool(self):
        self.cache.index = {tid: {'status': 'ok'} for tid in self.ids}
        with mock.patch.object(self.cache, '_valid_default_ids', side_effect=AssertionError('pool')):
            self.assertEqual(set(self.cache.status_kinds(self.ids).values()), {'cover'})
            self.assertEqual(self.cache.status_kinds(()), {})

    def test_one_assigned_row_does_not_scan_unrelated_pool_photos(self):
        checks = []
        original = Path.is_file
        def is_file(path):
            if path.parent == self.cache._defaults_root:
                checks.append(path)
            return original(path)
        with mock.patch.object(Path, 'is_file', is_file), mock.patch.object(
                self.cache, '_valid_default_ids', side_effect=AssertionError('unneeded fallback')):
            self.assertEqual(self.cache.status_kinds(self.ids[:1]), {self.ids[0]: 'default'})
        self.assertEqual(len(checks), 1)

    def test_staged_default_changes_keep_single_reader_semantics(self):
        staged = self.cache._defaults_root / 'staged.jpg'
        self.cache._defaults['photos']['staged'] = {'name': 'Staged'}
        self.cache._changes[str(staged.relative_to(self.cache._area.root))] = b'not-published'
        tracks = ['outside-' + tid for tid in self.ids]
        expected = {tid: self.cache.status_kind(tid) for tid in tracks}
        self.assertEqual(self.cache.status_kinds(tracks), expected)


if __name__ == '__main__':
    unittest.main()
