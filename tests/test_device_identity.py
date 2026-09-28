"""Remembered hardware facts survive boot mode, never device replacement."""
from dataclasses import replace
from pathlib import Path
import unittest

from soundbox.device_identity import remember_identity
from soundbox.stock_ipod_info import StockIpodSnapshot


def snapshot():
    return StockIpodSnapshot(
        Path('/Volumes/IPOD'), 'IPOD', 'UUID', 'FAT32', 'USB', 'disk4s2',
        True, 1000, 500, 'MA446', 'confirmed', 'iPod', '5.5', 'key',
        1, 2, 0x75, 0, 'hash', 500, 0, '', 0, (), (),
        serial_number='serial-one', serial_source='matched Apple record',
        firmware_version='1.3', firmware_source='matched Apple record')


class RememberedIdentity(unittest.TestCase):
    def test_boot_mode_fallback_and_fresh_firmware(self):
        records = {}
        first = snapshot()
        remember_identity(first, records)
        rockbox = replace(first, serial_number='', serial_source='',
                          firmware_version='', firmware_source='', database_id=99)
        restored = remember_identity(rockbox, records)
        self.assertEqual(restored.serial_number, first.serial_number)
        self.assertEqual(restored.firmware_version, '1.3')
        self.assertTrue(restored.firmware_source.startswith('Remembered: '))
        remember_identity(replace(first, firmware_version='1.4'), records)
        self.assertEqual(remember_identity(rockbox, records).firmware_version, '1.4')

    def test_other_device_and_conflicting_serial_never_inherit_firmware(self):
        records = {}
        first = snapshot()
        remember_identity(first, records)
        replacement = replace(first, identity_key='other', serial_number='',
                              firmware_version='')
        self.assertEqual(remember_identity(replacement, records).serial_number, '')
        conflict = replace(first, serial_number='serial-two', firmware_version='')
        self.assertEqual(remember_identity(conflict, records).firmware_version, '')
        self.assertNotIn('firmware_version', records['key'])

    def test_missing_identity_and_corrupted_cache_fail_closed(self):
        first = snapshot()
        records = {'key': {'serial_number': 42, 'serial_source': 'bad'}}
        unknown = replace(first, serial_number='', firmware_version='')
        self.assertEqual(remember_identity(unknown, records).serial_number, '')
        records = {}
        remember_identity(replace(first, volume_uuid=''), records)
        self.assertEqual(records, {})
