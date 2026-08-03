"""Rockbox settings are narrowed to DB freshness, tag display, and playlist capacity."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from soundbox.rockbox_config import (
    database_refresh_pending,
    desired_payload,
    plan,
    settings_ready,
    theme_album_art_sides,
)


class RockboxConfigTests(unittest.TestCase):
    def test_preserves_unrelated_lines_and_replaces_only_required_settings(self):
        before = (
            b"# personal config\n"
            b"volume: -33\n"
            b"tagcache_autoupdate: off\n"
            b"playlist viewer track display: track name\n"
            b"shuffle: on\n"
        )
        after = desired_payload(before)
        self.assertIn(b"# personal config\n", after)
        self.assertIn(b"volume: -33\n", after)
        self.assertIn(b"shuffle: on\n", after)
        self.assertIn(b"tagcache_autoupdate: on\n", after)
        self.assertIn(
            b"playlist viewer track display: title from tags\n", after)
        self.assertIn(b"sort playlists: alpha\n", after)
        self.assertIn(b"max files in playlist: 10000\n", after)
        self.assertNotIn(b"tagcache_autoupdate: off", after)

    def test_raises_small_playlist_limit_but_preserves_a_larger_one(self):
        self.assertIn(
            b"max files in playlist: 10000\n",
            desired_payload(b"max files in playlist: 2000\n"),
        )
        larger = b"max files in playlist: 32000\n"
        self.assertIn(larger, desired_payload(larger))

    def test_plan_is_read_only_and_becomes_ready_after_exact_payload_lands(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".rockbox").mkdir()
            config = root / ".rockbox/config.cfg"
            config.write_text("volume: -20\n", encoding="utf-8")
            item = plan(root)
            self.assertTrue(item.changed)
            self.assertEqual(config.read_text(encoding="utf-8"), "volume: -20\n")
            config.write_bytes(item.payload)
            self.assertTrue(settings_ready(root))
            self.assertFalse(plan(root).changed)

    def test_ready_config_preserves_rockbox_crlf_bytes_exactly(self):
        before = (
            b"volume: -20\r\n"
            b"playlist viewer track display: title from tags\r\n"
            b"tagcache_autoupdate: on\r\n"
            b"sort playlists: alpha\r\n"
            b"max files in playlist: 10000\r\n"
        )
        self.assertEqual(desired_payload(before), before)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".rockbox").mkdir()
            (root / ".rockbox/config.cfg").write_bytes(before)
            self.assertTrue(settings_ready(root))
            self.assertFalse(plan(root).changed)

    def test_active_theme_album_art_sizes_are_bounded_and_square(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wps = root / ".rockbox/wps/theme.wps"
            sbs = root / ".rockbox/wps/theme.sbs"
            wps.parent.mkdir(parents=True)
            wps.write_text(
                "%Cl(10,20,200,200,c,c)%Cd\n%Cl(1,2,300,300)\n",
                encoding="utf-8",
            )
            sbs.write_text(
                "%Cl(10,20,96,96)%Cd\n%Cl(1,2,120,100)\n",
                encoding="utf-8",
            )
            (root / ".rockbox/config.cfg").write_text(
                "wps: /.rockbox/wps/theme.wps\n"
                "sbs: /.rockbox/wps/theme.sbs\n",
                encoding="utf-8",
            )
            self.assertEqual(theme_album_art_sides(root), (200, 96))

    def test_database_refresh_pending_tracks_manifest_and_index_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rockbox = root / ".rockbox"
            rockbox.mkdir()
            config = rockbox / "config.cfg"
            config.write_bytes(desired_payload(None))
            manifest = root / ".soundbox/manifest.json"
            manifest.parent.mkdir()
            manifest.write_text("{}\n", encoding="utf-8")
            index = rockbox / "database_idx.tcd"
            index.write_bytes(b"index")

            os.utime(config, (100, 100))
            os.utime(index, (101, 101))
            os.utime(manifest, (104, 104))
            self.assertTrue(database_refresh_pending(root))

            os.utime(index, (106, 106))
            self.assertFalse(database_refresh_pending(root))

    def test_missing_index_is_pending_only_when_managed_inputs_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".rockbox").mkdir()
            self.assertFalse(database_refresh_pending(root))
            config = root / ".rockbox/config.cfg"
            config.write_bytes(desired_payload(None))
            self.assertTrue(database_refresh_pending(root))


if __name__ == "__main__":
    unittest.main()
