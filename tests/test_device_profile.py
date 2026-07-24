"""Read-only device classification and stock-iPod planning."""

from pathlib import Path
import tempfile
import unittest

from soundbox.device_profile import inspect_device, plan_stock_ipod_export


class DeviceProfiles(unittest.TestCase):
    def _root(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name)

    @staticmethod
    def _snapshot(root: Path):
        return sorted(
            (str(path.relative_to(root)), path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")
        )

    def test_generic_fat32_is_rockbox_style_writable(self):
        root = self._root()
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        self.assertEqual(profile.kind, "generic_usb")
        self.assertTrue(profile.rockbox_export)
        self.assertEqual(self._snapshot(root), before)

    def test_rockbox_stock_and_dual_boot_are_distinct(self):
        root = self._root()
        (root / ".rockbox").mkdir()
        self.assertEqual(inspect_device(root, "msdos").kind, "rockbox")
        (root / "iPod_Control/iTunes").mkdir(parents=True)
        (root / "iPod_Control/iTunes/iTunesDB").write_bytes(b"sacred")
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        self.assertEqual(profile.kind, "dual_boot")
        self.assertTrue(profile.rockbox_export)
        self.assertTrue(profile.stock_preview)
        self.assertFalse(profile.stock_write)
        self.assertEqual(self._snapshot(root), before)

    def test_stock_ipod_plan_is_explicitly_no_write(self):
        root = self._root()
        (root / "iPod_Control/iTunes").mkdir(parents=True)
        database = root / "iPod_Control/iTunes/iTunesDB"
        database.write_bytes(b"years of user state")
        audio = root / "fixture.m4a"
        audio.write_bytes(b"fixture")
        before = self._snapshot(root)
        profile = inspect_device(root, "msdos")
        plan = plan_stock_ipod_export(profile, [audio, root / "missing.mp3"])
        self.assertEqual(profile.kind, "stock_ipod")
        self.assertFalse(profile.rockbox_export)
        self.assertFalse(plan["writable"])
        self.assertEqual(plan["formats"], {".m4a": 1})
        self.assertEqual(plan["missing_count"], 1)
        self.assertEqual(self._snapshot(root), before)

    def test_non_fat_generic_is_not_exportable(self):
        root = self._root()
        profile = inspect_device(root, "exfat")
        self.assertEqual(profile.kind, "generic_usb")
        self.assertFalse(profile.rockbox_export)
