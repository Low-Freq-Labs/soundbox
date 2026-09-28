"""Read-only Rockbox theme and database observations."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from soundbox.rockbox_config import (
    database_refresh_pending,
    database_auto_update_enabled,
    theme_album_art_sides,
)


class RockboxConfigTests(unittest.TestCase):
    def test_database_auto_update_observation_does_not_change_preferences(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".rockbox").mkdir()
            config = root / ".rockbox/config.cfg"
            self.assertFalse(database_auto_update_enabled(root))
            self.assertFalse(config.exists())
            for payload, expected in (
                (b"tagcache_autoupdate: off\r\nmax files in playlist: 5000\r\n", False),
                (b"tagcache_autoupdate: on\n", True),
                (b"volume: -20\n", False),
                (b"\xff", False),
            ):
                config.write_bytes(payload)
                self.assertEqual(database_auto_update_enabled(root), expected)
                self.assertEqual(config.read_bytes(), payload)

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
            config.write_bytes(b"tagcache_autoupdate: off\n")
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
            config.write_bytes(b"tagcache_autoupdate: off\n")
            self.assertTrue(database_refresh_pending(root))


if __name__ == "__main__":
    unittest.main()
