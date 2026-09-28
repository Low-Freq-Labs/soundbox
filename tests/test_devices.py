"""Read-only macOS removable-device fact probes."""

from __future__ import annotations

from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from desktop import devices


class ClassicIpodFacts(unittest.TestCase):
    def test_same_name_remount_forgets_every_path_keyed_fact(self):
        path = "/Volumes/IPOD-REMOUNT"
        caches = (
            devices._EXTERNAL_CACHE,
            devices._FS_CACHE,
            devices._PROFILE_CACHE,
            devices._INFO_CACHE,
            devices._IPOD_FACTS_CACHE,
        )
        saved = [cache.get(path) for cache in caches]
        present = [path in cache for cache in caches]
        try:
            for index, cache in enumerate(caches):
                cache[path] = {"old": index}
            with mock.patch.object(
                    devices, "_is_external", return_value=True) as probe:
                self.assertTrue(devices.refresh_device_mount(path))
            probe.assert_called_once_with(Path(path))
            self.assertTrue(all(path not in cache for cache in caches))
        finally:
            for cache, had_value, value in zip(
                    caches, present, saved, strict=True):
                if had_value:
                    cache[path] = value
                else:
                    cache.pop(path, None)

    def test_pioneer_tree_is_reserved_inside_external_volume(self):
        with mock.patch.object(devices, "_is_external", return_value=True):
            self.assertTrue(devices._in_external_product_tree(
                "/Volumes/IPOD/PIONEER/rekordbox/export.pdb"))
            self.assertTrue(devices._in_external_product_tree(
                "/Volumes/IPOD/pioneer/USBANLZ/history.DAT"))
            self.assertFalse(devices._in_external_product_tree(
                "/Volumes/IPOD/Music/PIONEER/song.wav"))

    def test_finder_cache_supplies_real_firmware_and_hardware_serial(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "com.apple.iPod.plist"
            path.write_bytes(plistlib.dumps({"Devices": {
                "CURRENT": {
                    "Device Class": "iPod",
                    "Serial Number": "8M650V0HV9M",
                    "Firmware Version String": "1.3",
                },
            }}))
            self.assertEqual(
                devices._classic_ipod_preferences("CURRENT", path),
                {
                    "hardware_serial": "8M650V0HV9M",
                    "firmware_version": "1.3",
                },
            )

            self.assertEqual(devices._classic_ipod_preferences("OTHER", path), {})
            self.assertEqual(devices._classic_ipod_preferences("", path), {})

    def test_multiple_historical_ipods_fail_closed_without_exact_id(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "com.apple.iPod.plist"
            path.write_bytes(plistlib.dumps({"Devices": {
                "FIRST": {"Device Class": "iPod", "Firmware Version String": "1.2"},
                "SECOND": {"Device Class": "iPod", "Firmware Version String": "1.3"},
            }}))
            self.assertEqual(devices._classic_ipod_preferences("UNKNOWN", path), {})
            self.assertEqual(
                devices._classic_ipod_preferences("SECOND", path)["firmware_version"],
                "1.3",
            )

    def test_usb_transport_revision_stays_separate_from_firmware(self):
        devices._IPOD_FACTS_CACHE.clear()
        usb = [{
            "idVendor": 1452,
            "USB Serial Number": "000A270015F42B87",
        }]
        scsi = [{
            "Vendor Identification": "Apple",
            "Product Identification": "iPod",
            "Product Revision Level": "1.62",
        }]
        with mock.patch.object(devices, "_ioreg_records", side_effect=[usb, scsi]), \
                mock.patch.object(
                    devices, "_classic_ipod_preferences",
                    return_value={
                        "hardware_serial": "8M650V0HV9M",
                        "firmware_version": "1.3",
                    }):
            facts = devices._classic_ipod_facts("/Volumes/IPOD")
        self.assertEqual(facts["firmware_version"], "1.3")
        self.assertEqual(facts["transport_revision"], "1.62")
        self.assertNotEqual(facts["firmware_version"], facts["transport_revision"])


if __name__ == "__main__":
    unittest.main()
