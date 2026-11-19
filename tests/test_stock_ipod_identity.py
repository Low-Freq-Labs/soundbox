"""Generated identity metadata; display catalog never admits a stock writer."""
from dataclasses import asdict
from pathlib import Path
import os
import plistlib
import struct
import tempfile
import unittest
from unittest import mock

from clickwheel.ipod.ipod_models import capabilities_for_family_gen
from clickwheel.ipod.itunesdb_writer import ChecksumType, write_itunesdb

from soundbox.stock_ipod_identity import (
    hardware_model, model_from_serial, normalize_display_model,
    parse_stock_identity, read_stock_identity,
)
from soundbox.stock_ipod_info import confirmed_model_from_recovery, read_stock_ipod_snapshot
from soundbox.device_profile import inspect_device
from soundbox.stock_ipod_signing import StockSigningContext, sign_stock_database


class StockIdentityMetadata(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name) / 'generated-device'
        self.metadata = self.root / 'iPod_Control/Device'
        self.metadata.mkdir(parents=True)

    def test_classic_retail_regions_normalize_without_writer_model_table(self):
        for model in ('MB029', 'MB147', 'MB145', 'MB150', 'MB562', 'MB565', 'MC293', 'MC297'):
            with self.subTest(model=model):
                self.assertEqual(normalize_display_model(model + 'LL/A'), model)
                self.assertEqual(normalize_display_model(model[1:]), model)
                self.assertEqual(hardware_model(model).family, 'iPod Classic')
        for value in ('iPod_Classic', 'iPod6,1', 'A1238', 'MB029 extra', 'MC297/../../x'):
            self.assertEqual(normalize_display_model(value), '')

    def test_primary_catalog_unique_classic_suffixes_and_ambiguous_video(self):
        expected = dict(Y5N='MB029', YMV='MB147', YMU='MB145', YMX='MB150',
                        **{'2C5': 'MB562', '2C7': 'MB565', '9ZS': 'MC293', '9ZU': 'MC297'})
        for suffix, model in expected.items():
            with self.subTest(suffix=suffix):
                self.assertEqual(model_from_serial('GENERATED' + suffix), (model, suffix))
        self.assertEqual(model_from_serial('GENERATEDWEE'), ('', 'WEE'))
        self.assertEqual(model_from_serial('000A2700231E2DD2')[0], '')
        self.assertEqual(model_from_serial('GENERATEDV9M')[0], 'MA446')

    def test_video_catalog_covers_original_and_late_models(self):
        for model in ('MA002', 'MA146', 'MA003', 'MA147', 'MA452',
                      'MA444', 'MA446', 'MA448', 'MA450', 'MA664'):
            with self.subTest(model=model):
                self.assertEqual(hardware_model(model).family, 'iPod Video')
                self.assertTrue(hardware_model(model).dac_name.startswith('Wolfson WM8758'))
        self.assertIn('2005', hardware_model('MA002').generation)
        self.assertIn('5.5', hardware_model('MA450').generation)

    def test_product_type_cannot_hide_retail_model_or_become_model(self):
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            raw = plistlib.dumps(dict(ProductType='iPod_Classic', ModelNumStr='MC297LL/A',
                SerialNumber='GENERATED9ZU', VisibleBuildID='2.0.4'), fmt=fmt)
            result = parse_stock_identity(raw, 'SysInfoExtended')
            self.assertEqual(result.model_number, 'MC297')
            self.assertEqual(result.serial_number, 'GENERATED9ZU')
            self.assertEqual(result.firmware_version, '2.0.4')
            self.assertEqual(result.model_source, 'SysInfoExtended')
        self.assertEqual(parse_stock_identity(b'ProductType: iPod_Classic\n', 'SysInfo').model_number, '')
        (self.root / 'iPod_Control/iTunes').mkdir()
        for raw in (plistlib.dumps(dict(ProductType='iPod_Classic', ModelNumStr='MC297LL/A')),
                    b'ProductType: iPod_Classic\nModelNumStr: MC297LL/A\n'):
            (self.metadata / 'SysInfoExtended').write_bytes(raw)
            self.assertEqual(inspect_device(self.root).model, 'MC297LL/A')

    def test_build_and_transport_revisions_are_not_firmware(self):
        for fields in (dict(BuildID='0x2048000', ProductRevisionLevel='1.62'),
                       dict(VisibleBuildID='0x2048000'), dict(FirmwareVersion=204)):
            self.assertEqual(parse_stock_identity(plistlib.dumps(fields), 'SysInfoExtended').firmware_version, '')
        self.assertEqual(parse_stock_identity(b'pszSerialNumber: GENERATEDYMV\n'
            b'ModelNumStr: MB147LL/A\nvisibleBuildID: 1.1.2\n', 'SysInfo').firmware_version, '1.1.2')

    def test_metadata_reads_only_two_existing_bounded_files_and_keeps_sources(self):
        extended = self.metadata / 'SysInfoExtended'
        extended.write_bytes(plistlib.dumps(dict(ProductType='iPod_Classic', SerialNumber='GENERATED9ZU')))
        plain = self.metadata / 'SysInfo'
        plain.write_bytes(b'ModelNumStr: MC297LL/A\nFirmwareVersion: 2.0.4\n')
        original = {p: p.read_bytes() for p in (extended, plain)}
        with (mock.patch.object(Path, 'glob', side_effect=AssertionError('No enumeration')),
             mock.patch.object(Path, 'rglob', side_effect=AssertionError('No enumeration')),
             mock.patch.object(Path, 'iterdir', side_effect=AssertionError('No enumeration'))):
            result = read_stock_identity(self.root)
        self.assertEqual(result.model_number, 'MC297')
        self.assertEqual(result.model_source, 'SysInfo')
        self.assertEqual(result.serial_number, 'GENERATED9ZU')
        self.assertEqual(result.serial_source, 'SysInfoExtended')
        self.assertEqual(result.firmware_version, '2.0.4')
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_oversized_symlink_and_fifo_metadata_fail_closed(self):
        extended = self.metadata / 'SysInfoExtended'
        with extended.open('wb') as stream:
            stream.seek(1024 * 1024)
            stream.write(b'x')
        plain = self.metadata / 'SysInfo'
        plain.write_bytes(b'ModelNumStr: MB562LL/A\n')
        self.assertEqual(read_stock_identity(self.root).model_number, 'MB562')
        extended.unlink()
        outside = self.root.parent / 'outside'
        outside.write_bytes(plistlib.dumps(dict(ModelNumStr='MC297')))
        extended.symlink_to(outside)
        self.assertEqual(read_stock_identity(self.root).model_number, 'MB562')
        extended.unlink()
        os.mkfifo(extended)
        self.assertEqual(read_stock_identity(self.root).model_number, 'MB562')

    def test_replaced_metadata_epoch_is_not_accepted(self):
        extended = self.metadata / 'SysInfoExtended'
        extended.write_bytes(plistlib.dumps(dict(ModelNumStr='MC297')))
        replacement = self.metadata / 'next'
        replacement.write_bytes(plistlib.dumps(dict(ModelNumStr='MB029')))
        real_stat = os.stat
        def replace_before_name_stat(path, *args, **kwargs):
            if path == 'SysInfoExtended' and kwargs.get('dir_fd') is not None and replacement.exists():
                os.replace(replacement, extended)
            return real_stat(path, *args, **kwargs)
        with mock.patch('soundbox.stock_ipod_identity.os.stat', side_effect=replace_before_name_stat):
            result = read_stock_identity(self.root)
        self.assertEqual(result.model_number, '')
        self.assertEqual(read_stock_identity(self.root).model_number, 'MB029')

    def test_replaced_metadata_parent_does_not_supply_current_facts(self):
        extended = self.metadata / 'SysInfoExtended'
        extended.write_bytes(plistlib.dumps(dict(ModelNumStr='MC297')))
        root = self.root.resolve()
        original_stat = Path.stat
        replaced = False
        def swap_before_root_stat(path, *args, **kwargs):
            nonlocal replaced
            if path == root and not replaced:
                replaced = True
                self.metadata.rename(self.metadata.with_name('old-Device'))
                self.metadata.mkdir()
                (self.metadata / 'SysInfoExtended').write_bytes(plistlib.dumps(dict(ModelNumStr='MB029')))
            return original_stat(path, *args, **kwargs)
        # Resolve/root admission also stats root. Arm the swap only after the
        # original metadata file has actually been opened.
        real_open = os.open
        def arm_on_metadata_open(path, flags, *args, **kwargs):
            nonlocal replaced
            result = real_open(path, flags, *args, **kwargs)
            if path == 'SysInfoExtended':
                replaced = False
            return result
        replaced = True
        with mock.patch('soundbox.stock_ipod_identity.os.open', side_effect=arm_on_metadata_open), \
             mock.patch.object(Path, 'stat', autospec=True, side_effect=swap_before_root_stat):
            result = read_stock_identity(self.root)
        self.assertEqual(result.model_number, '')
        self.assertEqual(read_stock_identity(self.root).model_number, 'MB029')

    def test_malformed_and_conflicting_metadata_do_not_invent_facts(self):
        for raw in (b'<?xml broken', b'bplistbroken', b'\xff\xfe',
                    plistlib.dumps({'ModelNumStr': ['MC297'], 'SerialNumber': 123})):
            with self.subTest(raw=raw[:30]):
                self.assertFalse(any(asdict(parse_stock_identity(raw, 'SysInfo')).values()))

    def test_contradictory_fresh_identities_mark_an_explicit_conflict(self):
        conflicting = parse_stock_identity(b'ModelNumStr: MB029\nModelNumStr: MC297\n', 'SysInfo')
        self.assertTrue(conflicting.identity_conflict)
        extended = self.metadata / 'SysInfoExtended'
        extended.write_bytes(plistlib.dumps(dict(ModelNumStr='MC297', SerialNumber='GENERATEDV9M')))
        self.assertTrue(read_stock_identity(self.root).identity_conflict)
        extended.write_bytes(plistlib.dumps(dict(ModelNumStr='MC297')))
        (self.metadata / 'SysInfo').write_bytes(b'ModelNumStr: MB029\n')
        self.assertTrue(read_stock_identity(self.root).identity_conflict)


class StockIdentitySnapshots(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        (self.root / 'iPod_Control/iTunes').mkdir(parents=True)
        self.assertTrue(write_itunesdb(self.root, [], backup=False,
            force_checksum=ChecksumType.NONE,
            capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen')))
        self.database = self.root / 'iPod_Control/iTunes/iTunesDB'
        self.before = self.database.read_bytes()

    def test_all_classic_suffixes_populate_snapshot_without_changing_native_format(self):
        expected = dict(Y5N='2007', YMV='2007', YMU='2007', YMX='2007',
                        **{'2C5': '2008', '2C7': '2008', '9ZS': '2009', '9ZU': '2009'})
        baseline = read_stock_ipod_snapshot(self.root).stock_format
        for suffix, year in expected.items():
            with self.subTest(suffix=suffix):
                snapshot = read_stock_ipod_snapshot(self.root, serial_number='GENERATED' + suffix,
                    firmware_version='2.0.4', firmware_source='generated exact device record')
                self.assertEqual(snapshot.family, 'iPod Classic')
                self.assertIn(year, snapshot.generation)
                self.assertEqual(snapshot.dac_name, 'Cirrus Logic CS42L55')
                self.assertIn(snapshot.model, snapshot.dac_source)
                self.assertEqual(snapshot.stock_format, baseline)
                self.assertEqual(self.database.read_bytes(), self.before)

    def test_fresh_observed_classic_model_overrides_remembered_video(self):
        snapshot = read_stock_ipod_snapshot(self.root, model_number='MA446',
            observed_model_number='MC297LL/A', observed_model_source='SysInfoExtended')
        self.assertEqual(snapshot.model, 'MC297')
        self.assertEqual(snapshot.model_source, 'SysInfoExtended')
        self.assertEqual(snapshot.family, 'iPod Classic')

    def test_remembered_classic_and_recovery_are_display_only(self):
        snapshot = read_stock_ipod_snapshot(self.root, model_number='MB565LL/A')
        self.assertEqual(snapshot.model, 'MB565')
        self.assertEqual(snapshot.model_source, 'remembered confirmation')
        recovery = self.root / '.soundbox/stock-ipod-recovery/generated'
        recovery.mkdir(parents=True)
        (recovery / 'plan.json').write_text('{"model": "MC297LL/A"}')
        self.assertEqual(confirmed_model_from_recovery(self.root), 'MC297')
        snapshot = read_stock_ipod_snapshot(self.root)
        self.assertEqual(snapshot.model, 'MC297')
        self.assertEqual(snapshot.model_source, 'previous SoundBox sync')
        self.assertEqual(self.database.read_bytes(), self.before)

    def test_usb_family_alone_does_not_guess_generation_model_dac_or_firmware(self):
        snapshot = read_stock_ipod_snapshot(self.root, usb_family='iPod Classic', transport_revision='2.70')
        self.assertEqual(snapshot.family, 'iPod Classic')
        self.assertEqual(snapshot.generation, 'Unknown generation')
        self.assertEqual((snapshot.model, snapshot.dac_name, snapshot.firmware_version), ('', '', ''))
        self.assertEqual(snapshot.transport_revision, '2.70')

    def test_live_conflict_cannot_inherit_a_remembered_model(self):
        for options in (dict(observed_model_number='MC297', serial_number='GENERATEDV9M'),
                        dict(identity_conflict=True)):
            snapshot = read_stock_ipod_snapshot(self.root, model_number='MA446', **options)
            self.assertTrue(snapshot.identity_conflict)
            self.assertEqual(snapshot.model_source, 'conflicting device identity')
            self.assertEqual((snapshot.model, snapshot.dac_name), ('', ''))

    def test_live_usb_family_conflicts_with_fresh_model_or_serial_catalog(self):
        for options in (dict(observed_model_number='MC297', usb_family='iPod Video'),
                        dict(serial_number='GENERATED9ZU', usb_family='iPod Video'),
                        dict(observed_model_number='MA446', usb_family='iPod Classic')):
            with self.subTest(options=options):
                snapshot = read_stock_ipod_snapshot(self.root, model_number='MA446', **options)
                self.assertTrue(snapshot.identity_conflict)
                self.assertEqual(snapshot.family, options['usb_family'])
                self.assertEqual((snapshot.model, snapshot.dac_name), ('', ''))
                self.assertEqual(snapshot.generation, 'Unknown generation')
        matching = read_stock_ipod_snapshot(self.root, observed_model_number='MC297',
            usb_family='iPod Classic')
        self.assertFalse(matching.identity_conflict)
        self.assertEqual(matching.model, 'MC297')

    def test_empty_signed_classic_identity_does_not_replace_signature_admission(self):
        guid = '0011223344556677'
        original = bytearray(self.before)
        struct.pack_into('<I', original, 0x10, 117)
        self.database.write_bytes(sign_stock_database(bytes(original),
            StockSigningContext(bytes.fromhex(guid), bytes(range(16)), bytes(range(12)))))
        signed = self.database.read_bytes()
        options = dict(serial_number='GENERATED9ZU', firmware_version='2.0.4',
                       usb_family='iPod Classic')
        connected = read_stock_ipod_snapshot(self.root, firewire_guid=guid, **options)
        self.assertEqual((connected.database_version, connected.checksum_scheme), (117, 1))
        self.assertEqual((connected.model, connected.firmware_version), ('MC297', '2.0.4'))
        self.assertEqual(connected.tracks, ())
        self.assertTrue(connected.stock_format.signature_verified)
        self.assertTrue(connected.stock_format.writable)
        unidentified = read_stock_ipod_snapshot(self.root, **options)
        self.assertEqual(unidentified.model, 'MC297')
        self.assertFalse(unidentified.stock_format.writable)
        self.assertEqual(self.database.read_bytes(), signed)
