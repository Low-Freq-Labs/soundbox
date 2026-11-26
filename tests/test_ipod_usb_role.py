"""An explicit USB companion preserves physical iPod identity and lazy reads."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop import devices
from desktop.workers import DeviceScanWorker
from soundbox.device_profile import DeviceProfile


def physical(kind='stock_ipod', filesystem='msdos'):
    return DeviceProfile(kind,'iTunes/Rockbox' if kind=='dual_boot' else 'iTunes',filesystem,
        kind=='dual_boot',True,kind=='dual_boot',True,True,model='MA446',library_persistent_id=123)


class IpodUsbRoleCapabilities(unittest.TestCase):
    def test_explicit_role_preserves_physical_profile_and_native_protection(self):
        for kind in ('stock_ipod','dual_boot'):
            for filesystem in ('msdos','fat32','fat','exfat'):
                with self.subTest(kind=kind,filesystem=filesystem):
                    original=physical(kind,filesystem);before=original.to_dict()
                    usb=devices.device_target_profile(original,'ipod_usb')
                    self.assertEqual(original.to_dict(),before)
                    self.assertIs(devices.device_target_profile(original,''),original)
                    self.assertIs(devices.device_target_profile(original,None),original)
                    self.assertEqual((usb.kind,usb.label),('ipod_usb','USB'))
                    self.assertTrue(usb.cdj_export)
                    self.assertTrue(usb.has_stock_ipod)
                    self.assertEqual(usb.has_rockbox,original.has_rockbox)
                    self.assertFalse(usb.stock_preview);self.assertFalse(usb.stock_write)
                    self.assertFalse(usb.rockbox_export)
                    self.assertEqual(usb.library_persistent_id,original.library_persistent_id)
                    self.assertIs(usb.stock_format,original.stock_format)
                    self.assertFalse(original.cdj_export)

    def test_unknown_role_unavailable_filesystem_and_non_ipod_never_gain_companion(self):
        for profile,role in ((None,'ipod_usb'),(physical(),'unknown'),
                (physical(filesystem='hfs'),'ipod_usb'),(physical(filesystem='apfs'),'ipod_usb'),
                (replace(physical(),kind='generic_usb',has_stock_ipod=False),'ipod_usb'),
                (replace(physical(),kind='rockbox',has_stock_ipod=False),'ipod_usb')):
            with self.subTest(profile=profile,role=role):
                self.assertIsNone(devices.device_target_profile(profile,role))

    def test_usb_readiness_uses_volume_guards_independently_of_stock_writer_admission(self):
        original=replace(physical(),stock_write=False,stock_format=None)
        usb=devices.device_target_profile(original,'ipod_usb')
        facts=devices.DeviceFacts(volume_uuid='generated-volume',writable=True)
        self.assertTrue(devices.device_export_readiness(usb,facts,41).ready)
        self.assertFalse(devices.device_export_readiness(original,facts,41).ready)
        for refused,token in ((replace(facts,writable=False),41),
                              (replace(facts,volume_uuid=''),41),(facts,0)):
            with self.subTest(facts=refused,token=token):
                self.assertFalse(devices.device_export_readiness(usb,refused,token).ready)
        self.assertFalse(devices.device_export_readiness(replace(usb,filesystem='hfs'),facts,41).ready)


class IpodUsbRoleScan(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path=str(self.root);self.token=os.stat(self.root).st_dev
        self.profile=physical();self.facts=devices.DeviceFacts(volume_uuid='generated-volume',writable=True)
        self.enterContext(mock.patch('desktop.workers.list_devices',return_value=[('Generated',self.path,1000)]))
        self.enterContext(mock.patch('desktop.workers.device_profile',return_value=self.profile))
        self.enterContext(mock.patch('desktop.workers.device_facts',return_value=self.facts))
        self.enterContext(mock.patch('desktop.devices.discovery_needs_retry',return_value=False))
        self.native=self.enterContext(mock.patch('desktop.usb_device_view.read_native_usb_snapshot',
            return_value=SimpleNamespace(mount_token=self.token)))
        self.filesystem=self.enterContext(mock.patch('desktop.usb_device_view.read_usb_snapshot',
            side_effect=AssertionError('Read stock iPod as Rockbox/filesystem')))
        self.drift=self.enterContext(mock.patch('desktop.workers.engine.device_drift',
            return_value=SimpleNamespace(count=lambda:2)))

    def run_worker(self,**options):
        worker=DeviceScanWorker(**options)
        self.addCleanup(worker.deleteLater)
        with mock.patch.object(worker,'_effective_profile',return_value=self.profile):worker.run()
        return worker

    def test_idle_stock_discovery_never_reads_usb_catalog_or_compares_usb_contents(self):
        worker=self.run_worker(lib=None)
        self.native.assert_not_called();self.filesystem.assert_not_called();self.drift.assert_not_called()
        self.assertEqual(worker.usb_snapshots,{})
        self.assertIs(worker.results[0][4],self.profile)
        self.assertTrue(worker.stock_detected)

    def test_explicit_usb_catalog_read_keeps_physical_result_and_existing_stock_drift(self):
        worker=self.run_worker(lib=object(),usb_requests={self.path})
        self.native.assert_called_once_with(self.path,self.token,self.facts)
        self.filesystem.assert_not_called();self.drift.assert_called_once()
        self.assertIs(worker.results[0][4],self.profile)
        self.assertEqual(worker.results[0][3],2)
        self.assertIs(worker.usb_snapshots[self.path],self.native.return_value)

    def test_idle_usb_cache_reuse_does_not_probe_or_refresh_native_catalog(self):
        cached=SimpleNamespace(mount_token=self.token)
        worker=self.run_worker(lib=None,known_paths={self.path},usb_views={self.path:cached})
        self.native.assert_not_called();self.filesystem.assert_not_called();self.drift.assert_not_called()
        self.assertIs(worker.usb_snapshots[self.path],cached)
        self.assertIs(worker.results[0][4],self.profile)

    def test_stale_usb_cache_waits_for_explicit_request_even_during_stock_discovery(self):
        worker=self.run_worker(lib=object(),known_paths={self.path},
                               usb_views={self.path:SimpleNamespace(mount_token=-1)})
        self.native.assert_not_called();self.filesystem.assert_not_called()
        self.drift.assert_called_once()
        self.assertNotIn(self.path,worker.usb_snapshots)
        self.assertEqual(worker.results[0][3],2)

    def test_usb_catalog_failure_stays_in_usb_snapshot_and_keeps_stock_profile(self):
        self.native.side_effect=ValueError('Generated native USB catalog unavailable')
        worker=self.run_worker(lib=None,usb_requests={self.path})
        self.assertIn('Generated native USB catalog unavailable',worker.usb_snapshots[self.path].error)
        self.assertIs(worker.results[0][4],self.profile)
        self.assertIsNone(worker.results[0][3])
