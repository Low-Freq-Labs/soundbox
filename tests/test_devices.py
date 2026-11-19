"""Read-only macOS removable-device fact probes."""

from __future__ import annotations

from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from desktop import devices


class ClassicIpodFacts(unittest.TestCase):
    def setUp(self):
        self.identity = self.enterContext(mock.patch.object(devices,'_stock_identity_for_volume',return_value=None))
        self.enterContext(mock.patch.object(devices,'_usb_identity_for_volume',return_value=None))

    def test_same_name_remount_forgets_every_path_keyed_fact(self):
        path = "/Volumes/IPOD-REMOUNT"
        caches = (
            devices._EXTERNAL_CACHE,
            devices._FS_CACHE,
            devices._PROFILE_CACHE,
            devices._INFO_CACHE,
            devices._IPOD_FACTS_CACHE,
            devices._USB_IDENTITY_CACHE,
            devices._USB_IDENTITY_RETRY,
            devices._IPOD_FACTS_OWNERS,
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
            "IOObjectClass": "IOUSBHostDevice",
            "USB Product Name": "iPod",
            "idVendor": 1452,
            "idProduct": 0x1209,
            "USB Serial Number": "000A270015F42B87",
            "IORegistryEntryChildren": [{
                "Vendor Identification": "Apple",
                "Product Identification": "iPod",
                "Product Revision Level": "1.62",
                "IORegistryEntryChildren": [{"BSD Name": "disk9s1"}],
            }],
        }]
        with mock.patch.dict(devices._INFO_CACHE, {'/Volumes/IPOD': {
                'DeviceIdentifier': 'disk9s1', 'VolumeUUID': 'fixture-volume'}}, clear=True), \
                mock.patch.object(devices, "_ioreg_records", return_value=usb), \
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


class IdleDiscoveryRetries(unittest.TestCase):
    def test_ipod_layout_created_after_mount_replaces_temporary_usb_profile_without_remount(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            volume=Path(td);child=volume/'IPOD';child.mkdir()
            original=Path.stat
            remote=SimpleNamespace(st_mode=child.stat().st_mode,st_dev=volume.stat().st_dev+1)
            with mock.patch.object(Path,'stat',lambda p,**kw:remote if p==child else original(p,**kw)), \
                    mock.patch.object(devices,'Path',return_value=volume), \
                    mock.patch.multiple(devices,_EXTERNAL_CACHE={},_FS_CACHE={str(child):'hfs'},
                        _PROFILE_CACHE={},_INFO_CACHE={str(child):{'MediaType':'iPod'}},_IPOD_FACTS_CACHE={}), \
                    mock.patch.object(devices,'_DISCOVERY_RETRY',False), \
                    mock.patch.object(devices,'_is_external',return_value=True):
                before=devices.device_profile(child);self.assertEqual(before.kind,'generic_usb')
                self.assertEqual(len(devices.list_devices()),1)
                self.assertTrue(devices.discovery_needs_retry())
                (child/'iPod_Control/iTunes').mkdir(parents=True)
                self.assertEqual(len(devices.list_devices()),1)
                after=devices.device_profile(child)
                self.assertEqual(after.kind,'stock_ipod');self.assertTrue(after.stock_preview)
                self.assertFalse(after.stock_write);self.assertEqual(after.filesystem,'hfs')
                self.assertFalse(devices.discovery_needs_retry())

    def test_ordinary_generic_usb_does_not_gain_profile_probes_or_periodic_retry(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            volume=Path(td);child=volume/'USB';child.mkdir();original=Path.stat
            remote=SimpleNamespace(st_mode=child.stat().st_mode,st_dev=volume.stat().st_dev+1)
            with mock.patch.object(Path,'stat',lambda p,**kw:remote if p==child else original(p,**kw)), \
                    mock.patch.object(devices,'Path',return_value=volume), \
                    mock.patch.multiple(devices,_EXTERNAL_CACHE={},_FS_CACHE={},_PROFILE_CACHE={},
                        _INFO_CACHE={str(child):{'MediaType':'Generic'}},_IPOD_FACTS_CACHE={}), \
                    mock.patch.object(devices,'_DISCOVERY_RETRY',False), \
                    mock.patch.object(devices,'_is_external',return_value=True), \
                    mock.patch.object(devices,'inspect_device') as inspect:
                self.assertEqual(len(devices.list_devices()),1)
                self.assertFalse(devices.discovery_needs_retry());inspect.assert_not_called()

    def test_failed_probe_retries_but_definitive_internal_scan_can_sleep(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            volume = Path(td)
            child = volume / 'Internal'
            child.mkdir()
            original = Path.stat
            remote_stat = SimpleNamespace(st_mode=child.stat().st_mode,
                                          st_dev=volume.stat().st_dev + 1)
            with mock.patch.object(Path, 'stat', lambda p, **kw: remote_stat if p == child else original(p, **kw)), \
                    mock.patch.object(devices, 'Path', return_value=volume), \
                    mock.patch.multiple(devices, _EXTERNAL_CACHE={}, _FS_CACHE={},
                                        _PROFILE_CACHE={}, _INFO_CACHE={}, _IPOD_FACTS_CACHE={}), \
                    mock.patch.object(devices, '_DISCOVERY_RETRY', False), \
                    mock.patch.object(devices, '_is_external', return_value=False):
                self.assertEqual(devices.list_devices(), [])
                self.assertTrue(devices.discovery_needs_retry())
                devices._EXTERNAL_CACHE[str(child)] = False
                self.assertEqual(devices.list_devices(), [])
                self.assertFalse(devices.discovery_needs_retry())

    def test_old_unmounted_folder_does_not_retry_but_fresh_mountpoint_does(self):
        with tempfile.TemporaryDirectory() as td:
            volume = Path(td)
            child = volume / 'Data'
            child.mkdir()
            created = child.stat().st_ctime
            with mock.patch.object(devices, 'Path', return_value=volume), \
                    mock.patch.multiple(devices, _EXTERNAL_CACHE={}, _FS_CACHE={},
                                        _PROFILE_CACHE={}, _INFO_CACHE={}, _IPOD_FACTS_CACHE={}), \
                    mock.patch.object(devices, '_DISCOVERY_RETRY', False), \
                    mock.patch.object(devices, '_is_external') as probe:
                with mock.patch.object(devices.time, 'time', return_value=created + 1):
                    self.assertEqual(devices.list_devices(), [])
                    self.assertTrue(devices.discovery_needs_retry())
                with mock.patch.object(devices.time, 'time', return_value=created + 31):
                    self.assertEqual(devices.list_devices(), [])
                    self.assertFalse(devices.discovery_needs_retry())
                probe.assert_not_called()
