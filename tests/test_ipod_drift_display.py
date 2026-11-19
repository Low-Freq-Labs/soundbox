"""Managed iPod drift projects only current members and reuses one observation."""
import json
from pathlib import Path
from types import MethodType,SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.usb_device_view import UsbSnapshot
from desktop.workers import DeviceScanWorker
from soundbox.device_profile import DeviceProfile
from soundbox.library import Library,Playlist,Track
from soundbox.operation_control import checkpoint


class IpodDriftDisplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def generated(self,kind='rockbox'):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        target=Path(area.name)/'generated-device';target.mkdir();token=target.stat().st_dev;dev=str(target)
        tracks={key:Track(key,key,'Artist',f'file:///generated/{key}.wav') for key in ('one','two','unmanaged')}
        playlist=Playlist('Selected',[],keys=['one','two'],pid='selected')
        library=Library(tracks,[playlist,Playlist('Other',[],keys=['unmanaged'],pid='other')],'/generated/library.xml')
        profile=DeviceProfile(kind,'USB' if kind=='generic_usb' else 'Rockbox','fat32',kind=='rockbox',False,
                              kind=='rockbox',False,False)
        cached=UsbSnapshot(target,token,DeviceFacts())
        worker=DeviceScanWorker(None,defer_playlists=True,known_paths={dev},known_mount_tokens={dev:token},
                                usb_views={dev:cached})
        self.addCleanup(worker.deleteLater)
        rows={key:dict(id=key,title='Effective '+key,artist='Effective artist',album='',genre='Current genre',
                       bpm='137',key='8A',year='2026',bpm_estimated=False) for key in tracks}
        owner=SimpleNamespace(lib=library,_dev_scan_worker=worker,_shutting_down=False,
            overrides={},titles={},_sync_playlists=lambda:[],_row_for=mock.Mock(side_effect=lambda t:rows[t.track_id]))
        owner._managed_ipod_display_ids=MethodType(MainWindow._managed_ipod_display_ids,owner)
        worker.playlists_requested.connect(lambda current:MainWindow._supply_device_scan_playlists(owner,current))
        self.enterContext(mock.patch('desktop.workers.list_devices',return_value=[('Generated',dev,1000)]))
        self.enterContext(mock.patch('desktop.workers.device_profile',return_value=profile))
        self.enterContext(mock.patch('desktop.devices.discovery_needs_retry',return_value=False))
        return owner,worker,dev,rows

    def test_managed_and_new_members_only_are_projected_and_exact_capture_is_reused(self):
        owner,worker,dev,rows=self.generated()
        manifest=dict(schema=2,playlists={'selected':dict(pid='selected',name='Selected',tracks=['one'])},files={})
        captured=(manifest,json.dumps(manifest).encode())
        with mock.patch('desktop.workers.engine.capture_device_drift_manifest',return_value=captured) as capture, \
             mock.patch('desktop.workers.engine.device_drift',return_value=SimpleNamespace(count=lambda:0)) as drift:
            worker.run()
        capture.assert_called_once_with(dev)
        self.assertIs(drift.call_args.kwargs['captured_manifest'],captured)
        self.assertEqual(set(drift.call_args.kwargs['display_rows']),{'one','two'})
        self.assertEqual({call.args[0].track_id for call in owner._row_for.call_args_list},{'one','two'})
        self.assertEqual(worker.results[0][3],0)
        rows['one']['title']='Later GUI title'
        self.assertEqual(worker.display_rows['one']['title'],'Effective one')

    def test_absent_manifest_needs_no_projection_or_second_device_read(self):
        owner,worker,dev,_rows=self.generated()
        with mock.patch('desktop.workers.engine.capture_device_drift_manifest',return_value=None) as capture, \
             mock.patch('desktop.workers.engine.device_drift',side_effect=AssertionError('Duplicate manifest read')):
            worker.run()
        capture.assert_called_once_with(dev);owner._row_for.assert_not_called()
        self.assertEqual(worker.results[0][3],0)

    def test_generic_usb_does_not_request_rows_or_ownership_comparisons(self):
        owner,worker,_dev,_rows=self.generated('generic_usb')
        with mock.patch('desktop.workers.engine.capture_device_drift_manifest',side_effect=AssertionError('Idle generic manifest')), \
             mock.patch('desktop.workers.engine.device_drift',side_effect=AssertionError('Idle generic drift')):
            worker.run()
        owner._row_for.assert_not_called()
        self.assertIsNone(worker.results[0][3])

    def test_unreadable_observation_stays_unknown_without_fallback_reread(self):
        owner,worker,_dev,_rows=self.generated()
        with mock.patch('desktop.workers.engine.capture_device_drift_manifest',side_effect=ValueError('Changed manifest')), \
             mock.patch('desktop.workers.engine.device_drift',side_effect=AssertionError('Unowned reread')):
            worker.run()
        owner._row_for.assert_not_called()
        self.assertIsNone(worker.results[0][3])

    def test_cancelled_capture_uses_real_worker_event_and_admits_no_projection(self):
        owner,worker,_dev,_rows=self.generated()
        def cancelled(_path):
            worker.cancel()
            checkpoint()
            raise AssertionError('The real operation cancellation was not bound')
        with mock.patch('desktop.workers.engine.capture_device_drift_manifest',side_effect=cancelled), \
             mock.patch('desktop.workers.engine.device_drift',side_effect=AssertionError('Cancelled drift')):
            worker.run()
        owner._row_for.assert_not_called()
        self.assertIsNone(worker.results)
