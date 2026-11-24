"""Full-backup export/restore: zip roundtrip, folder + library.json inputs,
pre-restore safety snapshot, invalid input."""

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from soundbox import backup


def _make_data_dir(root: Path) -> Path:
    d = root / "appdata"
    (d / "backups").mkdir(parents=True)
    (d / "art").mkdir()
    (d / "library.json").write_text(json.dumps(
        {"custom": {"MIX": ["1"]}, "artist_overrides": {"1": "WODD"},
         "title_overrides": {}, "hidden_tracks": [], "settings": {}}))
    (d / "backups" / "library-20260701-000000.json").write_text("{}")
    (d / "art" / "index.json").write_text("{}")
    (d / "art" / "1.jpg").write_bytes(b"\xff\xd8fakejpeg")
    (d / "art" / "defaults").mkdir()
    (d / "art" / "defaults" / "index.json").write_text(
        '{"schema": 1, "seed": "abc", "photos": {}}')
    (d / "art" / "defaults" / "pool.jpg").write_bytes(b"\xff\xd8default")
    return d


class BackupRoundtrip(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(prefix="bk_")
        self.addCleanup(area.cleanup)
        self.tmp = Path(area.name)
        self.data = _make_data_dir(self.tmp)

    def test_export_zip_contents(self):
        z = self.tmp / "b.zip"
        n = backup.export_backup(self.data, z)
        self.assertEqual(n, 6)  # library + history + track art + default pool
        names = set(zipfile.ZipFile(z).namelist())
        self.assertIn("library.json", names)
        self.assertIn("README.txt", names)
        self.assertIn("art/1.jpg", names)
        self.assertIn("art/defaults/index.json", names)
        self.assertIn("art/defaults/pool.jpg", names)

    def _fresh_target(self) -> Path:
        t = self.tmp / "fresh"
        (t / "backups").mkdir(parents=True)
        (t / "library.json").write_text(json.dumps({"custom": {}, "settings": {}}))
        return t

    def _assert_restored(self, target: Path, res: dict):
        data = json.loads((target / "library.json").read_text())
        self.assertEqual(data["artist_overrides"], {"1": "WODD"})
        self.assertTrue((target / "art" / "1.jpg").is_file())
        self.assertTrue((target / "art" / "defaults" / "pool.jpg").is_file())
        self.assertEqual(res["overrides"], 1)
        self.assertEqual(res["art_files"], 4)
        # pre-restore snapshot of the old state exists
        self.assertTrue(list((target / "backups").glob("pre-restore-*.json")))

    def test_restore_from_zip(self):
        z = self.tmp / "b.zip"
        backup.export_backup(self.data, z)
        t = self._fresh_target()
        self._assert_restored(t, backup.restore_backup(t, z))

    def test_restore_from_folder(self):
        t = self._fresh_target()
        self._assert_restored(t, backup.restore_backup(t, self.data))

    def test_restore_from_library_json_path(self):
        t = self._fresh_target()
        self._assert_restored(
            t, backup.restore_backup(t, self.data / "library.json"))

    def test_invalid_inputs_raise(self):
        t = self._fresh_target()
        with self.assertRaises(ValueError):
            backup.restore_backup(t, self.tmp / "nope")
        bad = self.tmp / "bad"
        bad.mkdir()
        with self.assertRaises(ValueError):
            backup.restore_backup(t, bad)  # folder without library.json

    def scoped_art(self):
        from PIL import Image
        from soundbox.artwork import ArtCache, ArtResult
        from soundbox.library import Library, Track

        def library(name):
            track = Track('1', name, 'Artist', f'file:///synthetic/{name}.wav',
                          source_uuid=f'uuid-{name}')
            return Library({'1': track}, [], f'/synthetic/{name}.xml',
                           source_kind='xml', source_library_id=name)

        def picture(color):
            stream = BytesIO()
            Image.new('RGB', (16, 16), color).save(stream, format='PNG')
            return ArtResult(stream.getvalue(), 'image/png', 1, 'manual', color)

        a, b = library('A'), library('B')
        legacy = ArtCache(self.data / 'art')
        legacy.store_ok('1', picture('red'))
        first = legacy.adopt_legacy(a)
        second = first.for_library(b)
        self.assertIsNone(second.image_path('1'))
        second.store_ok('1', picture('blue'))
        return a, b, first, second

    def test_full_backup_restores_distinct_source_art_and_adoption_ownership(self):
        from soundbox.artwork import ArtCache

        a, b, first, second = self.scoped_art()
        expected = [cache.image_path('1').read_bytes() for cache in (first, second)]
        self.assertNotEqual(*expected)
        archive = self.tmp / 'scoped.zip'
        backup.export_backup(self.data, archive)
        target = self._fresh_target()
        backup.restore_backup(target, archive)
        restored = ArtCache(target / 'art')
        self.assertEqual([restored.for_library(lib).image_path('1').read_bytes()
                          for lib in (a, b)], expected)
        self.assertEqual((target / 'art' / 'ownership.json').read_bytes(),
                         (self.data / 'art' / 'ownership.json').read_bytes())

    def test_restore_rejects_missing_scoped_image_before_replacing_state(self):
        _a, _b, _first, second = self.scoped_art()
        second.canonical_image_path('1').unlink()
        target = self._fresh_target()
        before = (target / 'library.json').read_bytes()
        with self.assertRaises(ValueError):
            backup.restore_backup(target, self.data)
        self.assertEqual((target / 'library.json').read_bytes(), before)

    def test_old_owned_backup_replaces_current_source_art_and_preserves_other_source(self):
        from PIL import Image
        from soundbox import source_truth
        from soundbox.artwork import ArtCache, ArtResult

        a, b, _first, second = self.scoped_art()
        other = second.image_path('1').read_bytes()
        old = _make_data_dir(self.tmp / 'old')
        document = json.loads((old / 'library.json').read_text())
        track = a.tracks_by_id['1']
        document['settings'][source_truth.CURATION] = {
            'format': 'soundbox-source-truth-v1',
            'active': {'scope': source_truth.scope(a), 'pins': {'1': {
                'native_id': track.native_id, 'uuid': track.source_uuid,
                'path': str(track.native_path)}}, 'playlist_pins': {},
                'unbound': [], 'unbound_playlists': []}, 'partitions': {}}
        (old / 'library.json').write_text(json.dumps(document))
        stream = BytesIO()
        Image.new('RGB', (16, 16), 'green').save(stream, format='PNG')
        legacy = ArtCache(old / 'art')
        legacy.store_ok('1', ArtResult(stream.getvalue(), 'image/png', 1, 'manual', 'green'))
        expected = legacy.image_path('1').read_bytes()
        backup.restore_backup(self.data, old)
        restored = ArtCache(self.data / 'art')
        self.assertEqual(restored.for_library(a).image_path('1').read_bytes(), expected)
        self.assertEqual(restored.for_library(b).image_path('1').read_bytes(), other)

    def test_old_art_without_saved_owner_cannot_overwrite_bound_current_state(self):
        from soundbox.artwork import ArtCache

        a, _b, first, _second = self.scoped_art()
        expected = first.image_path('1').read_bytes()
        old = _make_data_dir(self.tmp / 'unowned')
        (old / 'art' / 'index.json').write_text(json.dumps({'1': {'status': 'ok', 'ext': 'jpg'}}))
        before = (self.data / 'library.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'saved artwork owner'):
            backup.restore_backup(self.data, old)
        self.assertEqual((self.data / 'library.json').read_bytes(), before)
        self.assertEqual(ArtCache(self.data / 'art').for_library(a).image_path('1').read_bytes(), expected)

    def test_old_backup_preserves_registered_stable_import_artwork_identity(self):
        from PIL import Image
        from soundbox import source_truth
        from soundbox.artwork import ArtCache, ArtResult
        from soundbox.library import Library, Track

        tid = 'sb-012345abcdef'
        track = Track(tid, 'Imported', 'Artist', 'file:///synthetic/converted.mp3',
                      source_uuid='registered-native-uuid')
        library = Library({tid: track}, [], Path('/synthetic/library.db'),
                          source_kind='rekordbox-db', source_library_id='one-library')

        def picture(color):
            stream = BytesIO()
            Image.new('RGB', (16, 16), color).save(stream, format='PNG')
            return ArtResult(stream.getvalue(), 'image/png', 1, 'manual', color)

        current = ArtCache(self.data / 'art').for_library(library)
        current.store_ok(tid, picture('red'))
        old = _make_data_dir(self.tmp / 'old-stable')
        document = json.loads((old / 'library.json').read_text())
        document['settings'][source_truth.CURATION] = {
            'format': 'soundbox-source-truth-v1',
            'active': {'scope': source_truth.scope(library), 'pins': {tid: {
                'native_id': track.native_id, 'uuid': track.source_uuid,
                'path': str(track.native_path)}}, 'playlist_pins': {},
                'unbound': [], 'unbound_playlists': []}, 'partitions': {}}
        (old / 'library.json').write_text(json.dumps(document))
        selected = ArtCache(old / 'art')
        selected.store_ok(tid, picture('blue'))
        expected = selected.image_path(tid).read_bytes()

        backup.restore_backup(self.data, old)
        restored = ArtCache(self.data / 'art').for_library(library)
        self.assertEqual(restored._bindings[tid], ('local-id', tid))
        self.assertEqual(restored.cover_snapshot(tid).data, expected)
        self.assertEqual(restored.image_path(tid), current.image_path(tid))

    def test_old_art_with_missing_saved_song_pin_cannot_borrow_current_owner(self):
        from PIL import Image
        from soundbox import source_truth
        from soundbox.artwork import ArtCache, ArtResult

        a, _b, _first, _second = self.scoped_art()
        old = _make_data_dir(self.tmp / 'missing-song-owner')
        document = json.loads((old / 'library.json').read_text())
        document['settings'][source_truth.CURATION] = {
            'format': 'soundbox-source-truth-v1',
            'active': {'scope': source_truth.scope(a), 'pins': {},
                       'playlist_pins': {}, 'unbound': [], 'unbound_playlists': []},
            'partitions': {}}
        (old / 'library.json').write_text(json.dumps(document))
        stream = BytesIO()
        Image.new('RGB', (16, 16), 'green').save(stream, format='PNG')
        ArtCache(old / 'art').store_ok(
            '1', ArtResult(stream.getvalue(), 'image/png', 1, 'manual', 'unproved'))
        before = {str(p.relative_to(self.data)): p.read_bytes()
                  for p in self.data.rglob('*') if p.is_file()}

        with self.assertRaisesRegex(ValueError, 'unproved song owner'):
            backup.restore_backup(self.data, old)

        self.assertEqual({str(p.relative_to(self.data)): p.read_bytes()
                          for p in self.data.rglob('*') if p.is_file()}, before)


if __name__ == "__main__":
    unittest.main()
