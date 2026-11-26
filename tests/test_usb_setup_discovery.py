"""Connection setup probes stay separate from explicit USB catalog browsing."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from desktop.devices import DeviceFacts
from desktop.workers import DeviceScanWorker
from soundbox import cdj_usb
from soundbox.device_profile import DeviceProfile
from soundbox.operation_control import OperationCancelled


class UsbSetupDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.mount = self.root / 'Generated USB'
        self.mount.mkdir()
        self.path = str(self.mount)
        self.token = self.mount.stat().st_dev
        self.profile = DeviceProfile('generic_usb', 'USB', 'exfat', False, False,
                                     False, False, False)
        self.facts = DeviceFacts(volume_uuid='generated-volume', writable=True)
        self.enterContext(mock.patch('desktop.workers.list_devices',
                                     return_value=[('Generated USB', self.path, 1000)]))
        self.profile_probe = self.enterContext(mock.patch('desktop.workers.device_profile',
                                                          side_effect=lambda _path: self.profile))
        self.enterContext(mock.patch('desktop.workers.device_facts',
                                     side_effect=lambda *_args, **_kwargs: self.facts))
        self.enterContext(mock.patch('desktop.devices.discovery_needs_retry', return_value=False))
        self.native = self.enterContext(mock.patch('desktop.usb_device_view.read_native_usb_snapshot',
                                                  side_effect=AssertionError('Idle catalog read')))
        self.file_view = self.enterContext(mock.patch('desktop.usb_device_view.read_usb_snapshot',
                                                     side_effect=AssertionError('Idle file view read')))
        self.payload = self.enterContext(mock.patch.object(cdj_usb.CdjReadSession, 'read',
                                                           side_effect=AssertionError('Setup read payload bytes')))
        self.renderer = self.enterContext(mock.patch.object(cdj_usb, 'run_renderer',
                                                            side_effect=AssertionError('Setup parsed catalog')))
        self.drift = self.enterContext(mock.patch('desktop.workers.engine.device_drift',
                                                 side_effect=AssertionError('USB drift comparison')))
        self.probe = self.enterContext(mock.patch.object(cdj_usb, 'setup_readiness',
                                                        wraps=cdj_usb.setup_readiness))

    def worker(self, **options):
        worker = DeviceScanWorker(None, **options)
        self.addCleanup(worker.deleteLater)
        with mock.patch.object(worker, '_effective_profile', side_effect=lambda _path, profile: profile):
            worker.run()
        return worker

    def known(self, worker):
        return {row[:4] for row in worker.usb_setup_results.values()}

    def test_missing_setup_is_a_separate_role_bound_observation(self):
        worker = self.worker()
        path, token, volume, role, readiness = worker.usb_setup_results[self.path]
        self.assertEqual((path, token, volume, role),
                         (self.path, self.token, self.facts.volume_uuid, 'generic_usb'))
        self.assertEqual(readiness.state, 'missing')
        self.assertEqual(readiness.mount_identity, (self.token, self.mount.stat().st_ino))
        self.assertEqual(worker.usb_snapshots, {})
        self.assertIs(worker.results[0][4], self.profile)
        self.assertIsNone(worker.results[0][3])
        self.payload.assert_not_called(); self.renderer.assert_not_called()
        self.native.assert_not_called(); self.file_view.assert_not_called(); self.drift.assert_not_called()

    def test_known_missing_and_configured_mounts_do_not_repeat_on_stock_polls(self):
        first = self.worker()
        for _ in range(3):
            later = self.worker(known_paths={self.path}, known_mount_tokens={self.path: self.token},
                                usb_setup_known=self.known(first))
            self.assertEqual(later.usb_setup_results, {})
        self.probe.assert_called_once()
        folder = self.mount / 'PIONEER/rekordbox'; folder.mkdir(parents=True)
        (folder / 'export.pdb').write_bytes(b'')
        reconnect = self.worker()
        self.assertEqual(reconnect.usb_setup_results[self.path][-1].state, 'configured')
        self.worker(known_paths={self.path}, usb_setup_known=self.known(reconnect))
        self.assertEqual(self.probe.call_count, 2)

    def test_each_native_catalog_marker_counts_as_configured_even_without_playlists(self):
        folder = self.mount / 'PIONEER/rekordbox'; folder.mkdir(parents=True)
        for name in ('export.pdb', 'exportExt.pdb', 'exportLibrary.db'):
            with self.subTest(name=name):
                marker = folder / name; marker.write_bytes(b'')
                worker = self.worker()
                self.assertEqual(worker.usb_setup_results[self.path][-1].state, 'configured')
                marker.unlink()
        self.payload.assert_not_called(); self.renderer.assert_not_called()

    def test_stock_companion_probes_usb_role_without_replacing_physical_profile(self):
        (self.mount / 'iPod_Control').mkdir()
        self.profile = replace(self.profile, kind='stock_ipod', label='iTunes',
                               filesystem='msdos', has_stock_ipod=True,
                               stock_preview=True, stock_write=True)
        worker = self.worker()
        row = worker.usb_setup_results[self.path]
        self.assertEqual(row[3], 'ipod_usb')
        self.assertEqual(row[-1].state, 'missing')
        self.assertIsNotNone(row[-1].ipod_control_identity)
        self.assertIs(worker.results[0][4], self.profile)
        self.assertEqual(worker.usb_snapshots, {})
        self.native.assert_not_called(); self.drift.assert_not_called()

    def test_unknown_uuid_unwritable_and_unsupported_filesystems_do_not_probe(self):
        for facts, profile in (
                (replace(self.facts, volume_uuid=''), self.profile),
                (replace(self.facts, writable=False), self.profile),
                (self.facts, replace(self.profile, filesystem='apfs')),
                (self.facts, replace(self.profile, kind='rockbox', filesystem='msdos')),
                (self.facts, replace(self.profile, kind='stock_ipod', has_stock_ipod=True, filesystem='hfs'))):
            with self.subTest(facts=facts, profile=profile):
                self.facts, self.profile = facts, profile
                worker = self.worker(known_paths={self.path},
                                     usb_views={self.path: mock.Mock(mount_token=self.token)})
                self.assertEqual(worker.usb_setup_results, {})
        self.probe.assert_not_called()

    def test_unavailable_markers_are_known_once_without_catalog_fallback(self):
        folder = self.mount / '.soundbox-cdj'; folder.mkdir()
        (folder / 'pending.json').write_bytes(b'not read')
        first = self.worker()
        self.assertEqual(first.usb_setup_results[self.path][-1].state, 'unavailable')
        later = self.worker(known_paths={self.path}, usb_setup_known=self.known(first))
        self.assertEqual(later.usb_setup_results, {})
        self.probe.assert_called_once(); self.payload.assert_not_called()

    def test_late_identity_facts_can_have_their_first_probe_without_a_second_attachment(self):
        self.facts = replace(self.facts, volume_uuid='')
        self.assertEqual(self.worker().usb_setup_results, {})
        self.facts = replace(self.facts, volume_uuid='settled-volume')
        worker = self.worker(known_paths={self.path}, known_mount_tokens={self.path: self.token})
        self.assertEqual(worker.new_paths, ())
        self.assertEqual(worker.usb_setup_results[self.path][-1].state, 'missing')
        self.probe.assert_called_once()

    def test_provisional_ipod_does_not_prompt_for_generic_configuration(self):
        self.facts = replace(self.facts, media_type='iPod')
        self.assertEqual(self.worker().usb_setup_results, {})
        self.probe.assert_not_called()
        (self.mount / 'iPod_Control').mkdir()
        self.profile = replace(self.profile, kind='stock_ipod', has_stock_ipod=True)
        settled = self.worker(known_paths={self.path}, known_mount_tokens={self.path: self.token})
        self.assertEqual(settled.usb_setup_results[self.path][3], 'ipod_usb')
        self.probe.assert_called_once()

    def test_bad_mount_token_never_probes_or_publishes(self):
        worker = DeviceScanWorker(None)
        self.addCleanup(worker.deleteLater)
        for token in (0, self.token + 1):
            with self.subTest(token=token):
                worker.mount_tokens[self.path] = token
                worker._probe_usb_setup(self.path, self.profile)
                self.assertEqual(worker.usb_setup_results, {})
        self.probe.assert_not_called()

    def test_cancellation_during_probe_discards_results_before_finished_publication(self):
        worker = DeviceScanWorker(None)
        self.addCleanup(worker.deleteLater)
        def cancelled(*args, **kwargs):
            result = self.probe._mock_wraps(*args, **kwargs)
            worker.cancel()
            return result
        self.probe.side_effect = cancelled
        with mock.patch.object(worker, '_effective_profile', return_value=self.profile): worker.run()
        self.assertIsNone(worker.results)
        self.assertEqual(worker.usb_setup_results, {})
        self.native.assert_not_called()

    def test_same_device_number_root_replacement_during_probe_is_not_published(self):
        def replaced(*args, **kwargs):
            result = self.probe._mock_wraps(*args, **kwargs)
            self.mount.rename(self.root / 'Retired generated mount')
            self.mount.mkdir()
            return result
        self.probe.side_effect = replaced
        worker = self.worker()
        self.assertEqual(worker.usb_setup_results, {})
        self.assertEqual(worker.usb_snapshots, {})
        self.native.assert_not_called()

    def test_probe_cancellation_preserves_worker_cancellation_semantics(self):
        self.probe.side_effect = OperationCancelled('Generated cancellation')
        worker = self.worker()
        self.assertTrue(worker.cancellation.is_set())
        self.assertIsNone(worker.results)
        self.assertEqual(worker.usb_setup_results, {})
        self.native.assert_not_called()
