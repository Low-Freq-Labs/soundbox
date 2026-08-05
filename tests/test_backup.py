"""Full-backup export/restore: zip roundtrip, folder + library.json inputs,
pre-restore safety snapshot, invalid input."""

import json
import tempfile
import unittest
import zipfile
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
        self.tmp = Path(tempfile.mkdtemp(prefix="bk_"))
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


if __name__ == "__main__":
    unittest.main()
