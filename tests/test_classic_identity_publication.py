"""Classic display facts survive publication without becoming write authority."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.stock_device_view import StockIpodPages
from soundbox.device_identity import remember_identity
from soundbox.stock_ipod import supports_stock_write
from tests.test_device_identity import snapshot
from tests.test_ipod_desktop_io import host, Snapshot


class ClassicIdentityPublication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def classic(self, **changes):
        values = dict(model='MC297', model_source='SysInfoExtended',
                      family='iPod Classic', generation='Late 2009 (160 GB)',
                      dac_name='Cirrus Logic CS42L55', dac_source='verified MC297 hardware catalog',
                      serial_number='8K1134H59ZU', serial_source='matched Apple record',
                      firmware_version='2.0.4', firmware_source='matched Apple record',
                      usb_serial='000A2700231E2DD2', usb_family='iPod Classic')
        return replace(snapshot(), **{**values, **changes})

    def test_fresh_classic_model_wins_over_legacy_video_confirmation(self):
        owner = SimpleNamespace(settings={'stock_ipod_models': {'key': 'MA446'}},
                                _stock_device_key=lambda *_: 'key')
        profile = SimpleNamespace(model='MC297LL/A')
        with mock.patch('desktop.app.device_facts', side_effect=AssertionError('No fresh probe')):
            self.assertEqual(MainWindow._stored_stock_model(owner, '/fixture/IPOD', profile), 'MC297')
        self.assertFalse(supports_stock_write('MC297'))

    def test_incompatible_remembered_model_does_not_override_live_usb_family(self):
        owner = SimpleNamespace(settings={'stock_ipod_models': {'key': 'MA446'}},
                                _stock_device_key=lambda *_: 'key')
        with mock.patch('desktop.app.device_facts', return_value=DeviceFacts(usb_family='iPod Classic')) as facts:
            self.assertEqual(MainWindow._stored_stock_model(owner, '/fixture/IPOD'), '')
        facts.assert_called_once_with('/fixture/IPOD', cached_only=True)

    def test_identity_facts_are_captured_inside_owned_dashboard_worker(self):
        owner = host()
        facts = DeviceFacts(model_number='MC297', model_source='SysInfoExtended',
                            hardware_serial='8K1134H59ZU', firmware_version='2.0.4',
                            usb_family='iPod Classic', identity_conflict=True)
        with (
            mock.patch('desktop.app.device_facts', return_value=facts) as probe,
            mock.patch('soundbox.stock_ipod_capture.capture_stock_ipod_native', return_value=object()),
            mock.patch('soundbox.stock_ipod_info.read_stock_ipod_snapshot', return_value=Snapshot(model='')) as reader,
        ):
            MainWindow._read_stock_device_view(owner, '/fixture/IPOD')
            probe.assert_not_called()
            worker = owner._stock_view_worker
            try:
                worker.run()
                self.assertNotIsInstance(worker.result, Exception)
                self.assertEqual(reader.call_args.kwargs['observed_model_number'], 'MC297')
                self.assertEqual(reader.call_args.kwargs['observed_model_source'], 'SysInfoExtended')
                self.assertTrue(reader.call_args.kwargs['identity_conflict'])
                self.assertEqual(reader.call_args.kwargs['serial_number'], '8K1134H59ZU')
                self.assertEqual(reader.call_args.kwargs['firmware_version'], '2.0.4')
            finally:
                worker.deleteLater()

    def test_boot_mode_restores_coherent_model_generation_and_dac_without_signing_id(self):
        records = {}
        first = self.classic()
        remember_identity(first, records)
        disk_mode = replace(first, model='', model_source='', generation='Unknown generation',
                            dac_name='', dac_source='', serial_number='', serial_source='',
                            firmware_version='', firmware_source='', usb_serial='', firewire_guid='')
        restored = remember_identity(disk_mode, records)
        self.assertEqual((restored.model, restored.generation, restored.dac_name),
                         (first.model, first.generation, first.dac_name))
        self.assertTrue(restored.model_source.startswith('Remembered: '))
        self.assertEqual((restored.usb_serial, restored.firewire_guid), ('', ''))
        self.assertFalse(supports_stock_write(restored.model))

    def test_replacement_transport_without_matching_serial_cannot_inherit_details(self):
        records = {}
        first = self.classic()
        remember_identity(first, records)
        other = replace(first, model='', model_source='', serial_number='', serial_source='',
                        firmware_version='', firmware_source='', usb_serial='000A270000000001')
        restored = remember_identity(other, records)
        self.assertEqual((restored.model, restored.serial_number, restored.firmware_version), ('', '', ''))

    def test_same_observed_hardware_serial_can_cross_transport_modes(self):
        records = {}
        first = self.classic()
        remember_identity(first, records)
        other_mode = replace(first, model='', model_source='', firmware_version='', firmware_source='',
                             usb_serial='8K1134H59ZU')
        restored = remember_identity(other_mode, records)
        self.assertEqual((restored.model, restored.firmware_version), ('MC297', '2.0.4'))

    def test_same_family_replacement_model_does_not_inherit_serial_or_firmware(self):
        records = {}
        first = self.classic()
        remember_identity(first, records)
        replacement = replace(first, model='MB147', model_source='SysInfoExtended',
                              serial_number='', serial_source='',
                              firmware_version='', firmware_source='')
        restored = remember_identity(replacement, records)
        self.assertEqual(restored.model, 'MB147')
        self.assertEqual((restored.serial_number, restored.firmware_version), ('', ''))
        self.assertNotIn('serial_number', records[first.identity_key])
        self.assertNotIn('firmware_version', records[first.identity_key])

    def test_replacement_retires_legacy_model_confirmation_through_dashboard_publication(self):
        first = self.classic()
        records = {}
        remember_identity(first, records)
        replacement = replace(first, model_source='remembered confirmation',
                              serial_number='', serial_source='', firmware_version='', firmware_source='',
                              usb_serial='000A270000000001')
        owner = host()
        owner.settings = {'stock_ipod_identity': records, 'stock_ipod_models': {first.identity_key: 'MC297'}}
        owner._current_device_path = '/other/device'
        owner._stock_view_failures = {}
        owner._finish_ipod_loading_status = mock.Mock()
        owner._update_device_action_state = mock.Mock()
        owner._schedule_settings_save = mock.Mock()
        MainWindow._on_stock_device_view(owner, str(first.target), (replacement, None, None))
        result = owner._stock_view_cache[str(first.target)][0]
        self.assertEqual((result.model, result.dac_name, result.serial_number, result.firmware_version),
                         ('', '', '', ''))
        self.assertEqual(result.generation, 'Unknown generation')
        self.assertNotIn(first.identity_key, owner.settings['stock_ipod_models'])
        owner._schedule_settings_save.assert_any_call('stock_ipod_models')

    def test_conflicting_observations_never_refill_cached_model_or_firmware(self):
        records = {}
        first = self.classic()
        remember_identity(first, records)
        conflict = replace(first, model='', model_source='conflicting device identity',
                           serial_number='', firmware_version='', identity_conflict=True)
        restored = remember_identity(conflict, records)
        self.assertEqual((restored.model, restored.serial_number, restored.firmware_version), ('', '', ''))
        self.assertNotIn(first.identity_key, records)

    def test_live_conflict_retires_legacy_confirmation_even_when_engine_model_is_already_empty(self):
        first = self.classic()
        owner = host()
        owner.settings = {'stock_ipod_identity': {},
                          'stock_ipod_models': {first.identity_key: 'MC297', 'other-ipod': 'MA446'}}
        owner._current_device_path = '/other/device'
        owner._stock_view_failures = {}
        owner._finish_ipod_loading_status = mock.Mock()
        owner._update_device_action_state = mock.Mock()
        owner._schedule_settings_save = mock.Mock()
        conflict = replace(first, model='', model_source='conflicting device identity',
                           serial_number='', firmware_version='', identity_conflict=True)
        MainWindow._on_stock_device_view(owner, str(first.target), (conflict, None, None))
        self.assertEqual(owner.settings['stock_ipod_models'], {'other-ipod': 'MA446'})
        owner._schedule_settings_save.assert_any_call('stock_ipod_models')

    def test_classic_dashboard_presents_all_supplied_identity_fields_without_window(self):
        pages = StockIpodPages()
        try:
            with mock.patch('PySide6.QtWidgets.QWidget.show', side_effect=AssertionError('Visible test window')):
                pages.set_snapshot(self.classic())
            tree = pages.detail_trees['Identity']
            rows = {tree.topLevelItem(i).text(0): tree.topLevelItem(i).text(1)
                    for i in range(tree.topLevelItemCount())}
            self.assertEqual(rows['Model'], 'MC297')
            self.assertEqual(rows['Family'], 'iPod Classic')
            self.assertEqual(rows['Serial number'], '8K1134H59ZU')
            self.assertEqual(rows['iPod firmware'], '2.0.4')
            self.assertEqual(rows['DAC / codec'], 'Cirrus Logic CS42L55')
            self.assertIn('2009', rows['Generation'])
        finally:
            pages.deleteLater()
