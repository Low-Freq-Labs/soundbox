"""Generated BSD ancestry and signing admission; never probe a real device."""
from copy import deepcopy
from dataclasses import replace
import struct
import unittest
from unittest import mock

from desktop import devices
from soundbox.device_readiness import stock_write_readiness
from soundbox.stock_ipod_format import inspect_stock_write_format


GUID_A = '000A270000000001'
GUID_B = '000A270000000002'


def usb(serial=GUID_A, *, bsd='disk4s1', product=0x1261, entry=100):
    return dict(IOObjectClass='IOUSBHostDevice', idVendor=1452, idProduct=product,
        IORegistryEntryID=entry, **{'USB Product Name': 'iPod', 'USB Serial Number': serial},
        IORegistryEntryChildren=[{'Vendor Identification': 'Apple', 'Product Identification': 'iPod',
            'Product Revision Level': '1.62', 'IORegistryEntryChildren': [
                {'IOObjectClass': 'IOMedia', 'BSD Name': bsd}]}])


def classic_header():
    data = bytearray(0xF4); data[:4] = b'mhbd'
    struct.pack_into('<III', data, 4, len(data), len(data), 1)
    struct.pack_into('<I', data, 0x10, 0x75)
    struct.pack_into('<H', data, 0x30, 1)
    return bytes(data)


class ScopedSigningFacts(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(devices,'_stock_identity_for_volume',return_value=None))
        self.enterContext(mock.patch.object(devices,'_usb_identity_for_volume',return_value=None))

    def test_two_ipods_bind_each_partition_to_its_own_usb_ancestor(self):
        tree = [usb(), usb(GUID_B, bsd='disk8s1', product=0x1209, entry=200)]
        for ordered in (tree, list(reversed(tree))):
            first = devices._ipod_hardware_for_bsd(ordered, 'disk4s1')
            second = devices._ipod_hardware_for_bsd(ordered, 'disk8s1')
            self.assertEqual((first['firewire_guid'], first['usb_family']), (GUID_A, 'iPod Classic'))
            self.assertEqual((second['firewire_guid'], second['usb_family']), (GUID_B, 'iPod Video'))

    def test_one_globally_connected_ipod_is_not_a_fallback_for_unmatched_bsd(self):
        self.assertEqual(devices._ipod_hardware_for_bsd([usb()], 'disk5s1'), {})
        self.assertEqual(devices._ipod_hardware_for_bsd([usb()], ''), {})
        self.assertEqual(devices._ipod_hardware_for_bsd([usb()], '/dev/disk4s1'), {})

    def test_duplicate_target_bsd_under_distinct_usb_devices_is_ambiguous(self):
        self.assertEqual(devices._ipod_hardware_for_bsd([usb(), usb(GUID_B, entry=200)], 'disk4s1'), {})

    def test_repeated_same_registry_entry_is_deduplicated_but_unidentified_roots_are_not(self):
        first = usb()
        self.assertEqual(devices._ipod_hardware_for_bsd([first, deepcopy(first)], 'disk4s1')['firewire_guid'], GUID_A)
        del first['IORegistryEntryID']; other = deepcopy(first)
        self.assertEqual(devices._ipod_hardware_for_bsd([first, other], 'disk4s1'), {})

    def test_nested_foreign_usb_device_cannot_inherit_parent_signing_identity(self):
        tree = usb(); tree['IORegistryEntryChildren'] = [dict(IOObjectClass='IOUSBHostDevice',
            idVendor=123, **{'USB Product Name': 'Other'},
            IORegistryEntryChildren=[{'BSD Name': 'disk4s1'}])]
        self.assertEqual(devices._ipod_hardware_for_bsd([tree], 'disk4s1'), {})

    def test_hardware_serial_or_zero_guid_is_not_an_accepted_signing_id(self):
        for serial in ('APPLE123456', '0' * 16, '000A27000000000Z', '0x' + GUID_A):
            with self.subTest(serial=serial):
                result = devices._ipod_hardware_for_bsd([usb(serial)], 'disk4s1')
                self.assertEqual(result['usb_serial'], serial)
                self.assertEqual(result['firewire_guid'], '')
        self.assertEqual(devices._ipod_hardware_for_bsd([usb(GUID_A.lower())], 'disk4s1')['firewire_guid'], GUID_A)

    def test_cache_follows_device_identifier_and_volume_uuid_at_same_mount_name(self):
        info = {'/synthetic/IPOD': {'DeviceIdentifier': 'disk4s1', 'VolumeUUID': 'A', 'MediaType': 'iPod'}}
        with mock.patch.dict(devices._INFO_CACHE, info, clear=True), \
                mock.patch.dict(devices._IPOD_FACTS_CACHE, {}, clear=True), \
                mock.patch.object(devices, '_ioreg_records', return_value=[usb(), usb(GUID_B, bsd='disk8s1', entry=200)]) as probe, \
                mock.patch.object(devices, '_classic_ipod_preferences', return_value={}):
            self.assertEqual(devices._classic_ipod_facts('/synthetic/IPOD')['firewire_guid'], GUID_A)
            devices._INFO_CACHE['/synthetic/IPOD'] = dict(DeviceIdentifier='disk8s1', VolumeUUID='B', MediaType='iPod')
            self.assertEqual(devices.device_facts('/synthetic/IPOD', cached_only=True).firewire_guid, '')
            self.assertEqual(devices._classic_ipod_facts('/synthetic/IPOD')['firewire_guid'], GUID_B)
            self.assertEqual(probe.call_count, 2)
            self.assertIn('-l', probe.call_args.args)
            self.assertEqual(devices.device_facts('/synthetic/IPOD', cached_only=True).firewire_guid, GUID_B)

    def test_transient_ancestry_miss_is_not_cached_as_a_permanent_missing_identity(self):
        with mock.patch.dict(devices._INFO_CACHE, {'/synthetic/IPOD': {
                'DeviceIdentifier': 'disk4s1', 'VolumeUUID': 'A'}}, clear=True), \
                mock.patch.dict(devices._IPOD_FACTS_CACHE, {}, clear=True), \
                mock.patch.object(devices, '_ioreg_records', side_effect=[[], [usb()]]) as probe, \
                mock.patch.object(devices, '_classic_ipod_preferences', return_value={}):
            self.assertEqual(devices._classic_ipod_facts('/synthetic/IPOD')['firewire_guid'], '')
            self.assertEqual(devices._classic_ipod_facts('/synthetic/IPOD')['firewire_guid'], GUID_A)
            self.assertEqual(probe.call_count, 2)

    def test_missing_bsd_facts_never_trigger_global_usb_probe(self):
        with mock.patch.dict(devices._INFO_CACHE, {}, clear=True), \
                mock.patch.dict(devices._IPOD_FACTS_CACHE, {}, clear=True), \
                mock.patch.object(devices, '_ioreg_records', side_effect=AssertionError('global fallback')), \
                mock.patch.object(devices, '_classic_ipod_preferences', return_value={}):
            self.assertEqual(devices._classic_ipod_facts('/synthetic/IPOD')['firewire_guid'], '')

    def test_classic_readiness_requires_current_scoped_guid_and_refuses_a_stale_profile_guid(self):
        profile = inspect_stock_write_format(classic_header(), header_only=True, firewire_guid=GUID_A)
        self.assertTrue(profile.requires_fwid)
        self.assertTrue(profile.writable)
        self.assertFalse(profile.signature_verified)
        values = dict(writable=True, filesystem='msdos', stock_format=profile)
        self.assertTrue(stock_write_readiness(**values, firewire_guid=GUID_A).ready)
        for guid in ('', GUID_B, '0' * 16, 'APPLE123456'):
            with self.subTest(guid=guid):
                self.assertFalse(stock_write_readiness(**values, firewire_guid=guid).ready)
        missing = inspect_stock_write_format(classic_header(), header_only=True)
        self.assertTrue(missing.requires_fwid)
        self.assertFalse(missing.writable)
        self.assertFalse(stock_write_readiness(writable=True, filesystem='msdos', stock_format=missing,
                                              firewire_guid=GUID_A).ready)

    def test_export_readiness_uses_per_target_signing_identity_independent_of_model(self):
        from soundbox.device_profile import DeviceProfile
        capability = inspect_stock_write_format(classic_header(), header_only=True, firewire_guid=GUID_A)
        profile = DeviceProfile('stock_ipod', 'Stock iPod', 'msdos', False, True, False, True, True,
                                stock_format=capability)
        facts = devices.DeviceFacts(volume_uuid='A', writable=True, firewire_guid=GUID_A)
        self.assertTrue(devices.device_export_readiness(profile, facts, 10).ready)
        self.assertFalse(devices.device_export_readiness(profile, replace(facts, firewire_guid=GUID_B), 10).ready)


if __name__ == '__main__':
    unittest.main()
