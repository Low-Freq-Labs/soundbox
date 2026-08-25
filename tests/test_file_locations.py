import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soundbox import file_locations


class CoreFoundationBookmarks(unittest.TestCase):
    def test_bookmark_follows_parent_folder_rename_without_search(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_dir = root / "Sets"
            old_dir.mkdir()
            old = old_dir / "AHEE Lost Lands.m4a"
            old.write_bytes(b"audio")
            bookmark = file_locations.create_bookmark(old)
            if bookmark is None:
                self.skipTest("Core Foundation bookmarks unavailable")

            new_dir = root / "Music Library" / "Sets"
            new_dir.parent.mkdir()
            old_dir.rename(new_dir)

            resolved = file_locations.resolve_bookmark(bookmark)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), (new_dir / old.name).resolve())

    def test_corrupt_bookmark_fails_closed(self):
        self.assertIsNone(file_locations.resolve_bookmark("not base64!"))


class SpotlightMigration(unittest.TestCase):
    def test_exact_unique_name_and_size_match(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td) / "Moved Set.m4a"
            candidate.write_bytes(b"12345")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=str(candidate).encode() + b"\0", stderr=b"")
            records = {
                "sb-a": {"path": "/gone/Moved Set.m4a", "size": 5},
            }
            with mock.patch(
                    "soundbox.file_locations.subprocess.run",
                    return_value=completed) as run:
                found = file_locations.spotlight_matches(records)
            self.assertEqual(found, {"sb-a": candidate})
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["/usr/bin/mdfind", "-0"])
            self.assertIn('kMDItemFSName == "Moved Set.m4a"cd', argv[2])
            self.assertIn("kMDItemFSSize == 5", argv[2])

    def test_duplicate_match_is_never_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            one = Path(td) / "one" / "Set.m4a"
            two = Path(td) / "two" / "Set.m4a"
            one.parent.mkdir(); two.parent.mkdir()
            one.write_bytes(b"same"); two.write_bytes(b"same")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=(str(one).encode() + b"\0"
                        + str(two).encode() + b"\0"), stderr=b"")
            with mock.patch(
                    "soundbox.file_locations.subprocess.run",
                    return_value=completed):
                found = file_locations.spotlight_matches({
                    "sb-a": {"path": "/gone/Set.m4a", "size": 4},
                })
            self.assertEqual(found, {})

    def test_unknown_size_never_enters_automatic_index_query(self):
        with mock.patch(
                "soundbox.file_locations.subprocess.run") as run:
            found = file_locations.spotlight_matches({
                "sb-a": {"path": "/gone/Set.m4a", "size": 0},
            })
        self.assertEqual(found, {})
        run.assert_not_called()


class LocationRepair(unittest.TestCase):
    def test_bookmark_resolution_precedes_spotlight_and_refreshes_locator(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_dir = root / "Sets"
            old_dir.mkdir()
            old = old_dir / "set.m4a"
            old.write_bytes(b"content")
            bookmark = file_locations.create_bookmark(old)
            if bookmark is None:
                self.skipTest("Core Foundation bookmarks unavailable")
            record = {
                "path": str(old), "size": len(b"content"),
                file_locations.BOOKMARK_KEY: bookmark,
            }
            new_dir = root / "Somewhere Else"
            old_dir.rename(new_dir)

            with mock.patch(
                    "soundbox.file_locations.spotlight_matches") as spotlight:
                repaired = file_locations.repair_locations({"sb-a": record})
            spotlight.assert_not_called()
            update = repaired.updates["sb-a"]
            self.assertEqual(update.expected_path, str(old))
            self.assertEqual(update.path.resolve(), (new_dir / old.name).resolve())
            self.assertTrue(update.bookmark)
            self.assertEqual(repaired.missing_count, 1)
            self.assertEqual(repaired.unresolved_count, 0)

    def test_healthy_bookmarked_record_does_no_resolution_or_query(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "set.m4a"
            path.write_bytes(b"x")
            record = {
                "path": str(path), "size": 1,
                file_locations.BOOKMARK_KEY: "opaque",
            }
            with (
                mock.patch("soundbox.file_locations.resolve_bookmark") as resolve,
                mock.patch("soundbox.file_locations.spotlight_matches") as spotlight,
            ):
                repaired = file_locations.repair_locations({"sb-a": record})
            resolve.assert_not_called()
            spotlight.assert_not_called()
            self.assertEqual(repaired.updates, {})
            self.assertEqual(repaired.missing_count, 0)
