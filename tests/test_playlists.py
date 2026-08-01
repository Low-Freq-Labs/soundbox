import unittest
from pathlib import Path

from soundbox.playlists import M3UEntry, flat_playlist_path, render_m3u8, tree_playlist_path


class TestRender(unittest.TestCase):
    def test_order_preserved_and_header(self):
        entries = [
            M3UEntry("/Music/A/1.flac", "A", "One"),
            M3UEntry("/Music/B/2.mp3", "B", "Two"),
        ]
        out = render_m3u8(entries)
        self.assertEqual(
            out, "#EXTM3U\n/Music/A/1.flac\n/Music/B/2.mp3\n"
        )

    def test_missing_becomes_comment(self):
        entries = [
            M3UEntry(None, "Lost", "Track"),
            M3UEntry("/Music/A/ok.flac", "A", "ok"),
        ]
        out = render_m3u8(entries)
        self.assertIn("# MISSING: Lost - Track", out)
        self.assertIn("/Music/A/ok.flac", out)
        # Missing line must be a comment so Rockbox ignores it.
        self.assertTrue(any(l.startswith("#") for l in out.splitlines() if "MISSING" in l))


class TestPaths(unittest.TestCase):
    root = Path("/V/Playlists")

    def test_tree_mirrors_folders(self):
        p = tree_playlist_path(self.root, ["SETS", "HAMDI"], "HAMDI v4 ?")
        self.assertEqual(p, Path("/V/Playlists/SETS/HAMDI/HAMDI v4.m3u8"))

    def test_flat_joins_with_dash(self):
        p = flat_playlist_path(self.root, ["SETS", "HAMDI"], "HAMDI v4")
        self.assertEqual(p, Path("/V/Playlists/SETS - HAMDI - HAMDI v4.m3u8"))


if __name__ == "__main__":
    unittest.main()
