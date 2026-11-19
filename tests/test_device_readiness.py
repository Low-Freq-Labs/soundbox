"""Generated disk facts and hidden export choices; no real mount or device I/O."""
from dataclasses import replace
import struct
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from desktop import devices
from desktop.export_dialog import ExportDialog, ExportSelectionMixin
from desktop.workers import DeviceScanWorker
from soundbox.device_profile import DeviceProfile
from soundbox.device_readiness import stock_write_readiness
from soundbox.stock_ipod_format import inspect_stock_write_format
from soundbox.stock_ipod_info import stock_ipod_device_key
from soundbox.library import Library, Playlist


def stock_format(*, checksum=0, version=0x19, usb_family=''):
    data = bytearray(0xA4)
    data[:4] = b'mhbd'
    struct.pack_into('<III', data, 4, len(data), len(data), 1)
    struct.pack_into('<I', data, 0x10, version)
    struct.pack_into('<H', data, 0x30, checksum)
    return inspect_stock_write_format(bytes(data), usb_family=usb_family)


def profile(kind='stock_ipod', *, model='', filesystem='msdos', checksum=0):
    capability = stock_format(checksum=checksum) if kind in {'stock_ipod', 'dual_boot'} else None
    return DeviceProfile(kind, 'Fixture iPod' if kind in {'stock_ipod', 'dual_boot'} else 'USB',
        filesystem, kind in {'rockbox', 'dual_boot'}, kind in {'stock_ipod', 'dual_boot'},
        kind in {'rockbox', 'dual_boot'} and filesystem == 'msdos',
        kind in {'stock_ipod', 'dual_boot'}, bool(capability and capability.writable), model, 2, capability)


class CachedWriteFacts(unittest.TestCase):
    def test_typed_media_and_volume_refusals_cannot_be_masked_by_positive_flags(self):
        cases = (
            ({'Writable': True, 'WritableMedia': True, 'WritableVolume': True}, True),
            ({'WritableVolume': True}, True),
            ({'Writable': True}, True),
            ({'WritableMedia': True}, False),
            ({'ReadOnlyMedia': False, 'ReadOnlyVolume': False}, True),
            ({'ReadOnlyVolume': False}, False),
            ({'Writable': True, 'WritableVolume': False}, False),
            ({'WritableVolume': True, 'WritableMedia': False}, False),
            ({'WritableVolume': True, 'ReadOnlyMedia': True}, False),
            ({'Writable': True, 'ReadOnlyVolume': True}, False),
            ({'WritableVolume': 1}, False),
            ({'WritableVolume': 'true'}, False),
            ({'WritableVolume': True, 'WritableMedia': None}, False),
            ({'ReadOnlyMedia': 0, 'ReadOnlyVolume': False}, False),
            ({}, False),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields), \
                    mock.patch.dict(devices._INFO_CACHE, {'/synthetic/IPOD': fields}, clear=True), \
                    mock.patch.dict(devices._IPOD_FACTS_CACHE, {}, clear=True), \
                    mock.patch.object(devices, '_is_external', side_effect=AssertionError('disk probe')), \
                    mock.patch.object(devices, '_classic_ipod_facts', side_effect=AssertionError('hardware probe')):
                self.assertIs(devices.device_facts('/synthetic/IPOD', cached_only=True).writable, expected)


class StockWriteReadiness(unittest.TestCase):
    def test_supported_catalog_format_needs_no_model_or_per_device_confirmation(self):
        for filesystem in ('msdos', 'fat', 'FAT32', 'MS-DOS (FAT32)'):
            with self.subTest(filesystem=filesystem):
                result = stock_write_readiness(writable=True, filesystem=filesystem,
                                               stock_format=stock_format())
                self.assertTrue(result.ready)
                self.assertTrue(result.selectable)
                self.assertEqual(result.reason, '')

    def test_writable_volume_does_not_bypass_filesystem_or_catalog_format(self):
        for changed in ({'writable': False}, {'filesystem': 'exfat'}, {'filesystem': 'hfs'},
                        {'filesystem': ''}, {'stock_format': stock_format(checksum=1)},
                        {'stock_format': stock_format(version=0x30)}, {'stock_format': None}):
            with self.subTest(changed=changed):
                result = stock_write_readiness(**dict(
                    {'writable': True, 'filesystem': 'msdos', 'stock_format': stock_format()}, **changed))
                self.assertFalse(result.ready)
                self.assertFalse(result.selectable)
                self.assertTrue(result.reason)

    def test_artwork_requires_its_own_observed_format_without_blocking_catalog_edits(self):
        values = dict(writable=True, filesystem='msdos', stock_format=stock_format())
        self.assertTrue(stock_write_readiness(**values).ready)
        artwork = stock_write_readiness(**values, artwork=True)
        self.assertFalse(artwork.ready)
        self.assertIn('Artwork', artwork.reason)
        self.assertTrue(stock_write_readiness(writable=True, filesystem='msdos', artwork=True,
                                             stock_format=stock_format(usb_family='iPod Video')).ready)

    def test_export_keeps_identity_and_catalog_format_independent_of_model_labels(self):
        facts = devices.DeviceFacts(volume_uuid='new-physical-device', writable=True)
        for kind in ('stock_ipod', 'dual_boot'):
            with self.subTest(kind=kind):
                for model in ('', 'MA446', 'OTHER-FRIENDS-IPOD'):
                    self.assertTrue(devices.device_export_readiness(profile(kind, model=model), facts, 7).ready)
                self.assertFalse(devices.device_export_readiness(
                    profile(kind, model='MA446', checksum=1), facts, 7).selectable)
                self.assertFalse(devices.device_export_readiness(
                    replace(profile(kind), stock_format=None, stock_write=True), facts, 7).selectable)
                self.assertFalse(devices.device_export_readiness(
                    profile(kind), replace(facts, volume_uuid=''), 7).selectable)
                self.assertFalse(devices.device_export_readiness(profile(kind), facts, 0).selectable)
        self.assertTrue(devices.device_export_readiness(profile('generic_usb', filesystem='exfat'), facts, 7).ready)
        self.assertFalse(devices.device_export_readiness(profile('generic_usb', filesystem='hfs'), facts, 7).selectable)

    def test_current_dashboard_format_resolves_incomplete_discovery_but_retains_mount_guards(self):
        from types import SimpleNamespace
        facts = devices.DeviceFacts(volume_uuid='current-device', writable=True)
        for kind in ('stock_ipod', 'dual_boot'):
            with self.subTest(kind=kind):
                pending = replace(profile(kind), stock_format=None, stock_write=False)
                observed = SimpleNamespace(stock_format=stock_format(), filesystem='msdos')
                self.assertFalse(devices.device_export_readiness(pending, facts, 7).ready)
                self.assertTrue(devices.device_export_readiness(
                    pending, facts, 7, stock_snapshot=observed).ready)
                for current, token in ((replace(facts, writable=False), 7),
                                       (replace(facts, volume_uuid=''), 7), (facts, 0)):
                    self.assertFalse(devices.device_export_readiness(
                        pending, current, token, stock_snapshot=observed).ready)
                observed.stock_format = stock_format(checksum=1)
                self.assertFalse(devices.device_export_readiness(
                    profile(kind), facts, 7, stock_snapshot=observed).ready)


class ExportReadinessChoices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_remembered_model_only_enriches_display_and_cannot_override_unsupported_format(self):
        facts = devices.DeviceFacts(volume_uuid='same-library-uuid', writable=True)
        key = stock_ipod_device_key(facts.volume_uuid, 2)
        worker = DeviceScanWorker(None, stock_models={key: 'MA446LL/A'})
        self.addCleanup(worker.deleteLater)
        original = profile(checksum=1)
        with mock.patch('desktop.workers.device_facts', return_value=facts):
            enriched = worker._effective_profile('/synthetic/IPOD', original)
        self.assertEqual(enriched.model, 'MA446')
        self.assertFalse(enriched.stock_write)
        self.assertIs(enriched.stock_format, original.stock_format)
        self.assertFalse(devices.device_export_readiness(enriched, facts, 7).ready)

    def test_supported_target_can_be_selected_and_blocked_targets_explain_themselves(self):
        targets = [dict(path='/synthetic/IPOD', name='Connected iPod', label='Stock iPod',
                        free=100, kind='stock_ipod', selectable=True,
                        reason=''),
                   dict(path='/synthetic/READONLY', name='Other iPod', label='Stock iPod',
                        free=100, kind='stock_ipod', selectable=False,
                        reason='The connected volume is read-only')]
        dialog = ExportDialog([Playlist('Favorites', [], ['1'], 'favorites')], targets)
        self.addCleanup(dialog.deleteLater)
        dialog.playlists.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertTrue(dialog.export_button.isEnabled())
        self.assertEqual(dialog.selection()[1], [targets[0]])
        blocked = dialog.devices.topLevelItem(1)
        self.assertFalse(blocked.flags() & Qt.ItemIsEnabled)
        self.assertIn('read-only', blocked.text(0))
        blocked.setCheckState(0, Qt.Checked)
        self.assertEqual(dialog.selection()[1], [targets[0]])

    def host(self, device_profile, facts):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        host = SimpleNamespace(lib=Library({}, [Playlist('Favorites', [], ['1'], 'favorites')],
            '/synthetic/master.db', source_library_id='library'),
            _device_operation_owned=lambda *_: False, _export_batch=None,
            _device_profiles={'/synthetic/IPOD': device_profile},
            _device_mount_tokens={'/synthetic/IPOD': 7}, _usb_device_rows={},
            _export_playlists=lambda: [Playlist('Favorites', [], ['1'], 'favorites')],
            _flash_status=mock.Mock(), _update_device_action_state=mock.Mock(),
            _start_next_export_target=mock.Mock())
        self.enterContext(mock.patch('desktop.export_dialog.device_facts', return_value=facts))
        return host, parent

    def test_connected_supported_stock_target_queues_without_assuming_model_or_confirming(self):
        facts = devices.DeviceFacts(volume_uuid='new-device-uuid', writable=True)
        host, _parent = self.host(profile(), facts)
        selected = []
        def choose(_playlists, targets, **_options):
            selected.extend(targets)
            return SimpleNamespace(exec=lambda: QDialog.Accepted,
                selection=lambda: (['favorites'], targets), deleteLater=lambda: None)
        with mock.patch('desktop.export_dialog.ExportDialog', side_effect=choose), \
                mock.patch('desktop.export_dialog.Path.stat', side_effect=AssertionError('device stat')):
            ExportSelectionMixin._show_export_dialog(host, '/synthetic/IPOD')
        self.assertTrue(selected[0]['selectable'])
        self.assertEqual(selected[0]['reason'], '')
        self.assertEqual(host._export_batch['pending'][0]['uuid'], 'new-device-uuid')
        self.assertEqual(host._device_profiles['/synthetic/IPOD'].model, '')
        host._flash_status.assert_not_called()
        host._start_next_export_target.assert_called_once()

    def test_connected_readonly_or_unsupported_target_is_shown_with_reason_and_never_queued(self):
        for device_profile, writable, expected in (
                (profile(model='MA446'), False, 'read-only'),
                (profile(checksum=1), True, 'checksum family 1'),
                (profile(filesystem='hfs'), True, 'FAT32')):
            with self.subTest(expected=expected):
                host, _parent = self.host(device_profile,
                    devices.DeviceFacts(volume_uuid='fixture-uuid', writable=writable))
                def decline(_playlists, targets, **_options):
                    self.assertFalse(targets[0]['selectable'])
                    self.assertIn(expected, targets[0]['reason'])
                    return SimpleNamespace(exec=lambda: QDialog.Rejected, deleteLater=lambda: None)
                with mock.patch('desktop.export_dialog.ExportDialog', side_effect=decline):
                    ExportSelectionMixin._show_export_dialog(host)
                host._flash_status.assert_not_called()
                host._start_next_export_target.assert_not_called()
                self.assertIsNone(host._export_batch)
