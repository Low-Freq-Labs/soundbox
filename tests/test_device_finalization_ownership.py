"""Terminal refreshes belong to the accepted mount, through real worker retirement."""
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QProgressBar

from desktop.app import MainWindow, _DeviceRefreshCompletion
from desktop.device_browser import DeviceBrowserMixin
from desktop.devices import DeviceFacts
from soundbox.device_operations import DeviceOperationCoordinator, DeviceOperationResult, DeviceSessionIdentity
from soundbox.device_profile import DeviceProfile
from soundbox.library import Library
from soundbox.stock_ipod_format import ARTWORK_PROFILE, DATABASE_PROFILE, StockWriteFormat
from soundbox.stock_ipod_info import StockIpodPlaylistSnapshot, StockIpodSnapshot


class DeviceFinalizationOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = self.enterContext(TemporaryDirectory(prefix='soundbox-refresh-owner-'))
        target = Path(self.area) / 'SAME-IPOD'
        target.mkdir()
        self.dev = str(target)
        self.frame = QFrame()
        self.addCleanup(self.frame.deleteLater)
        self.entered, self.release = Event(), Event()
        self.workers = []
        self.profile = DeviceProfile('stock_ipod', 'Stock iPod', 'msdos', False, True,
            False, True, True, stock_format=StockWriteFormat(DATABASE_PROFILE, ARTWORK_PROFILE))
        self.snapshot = StockIpodSnapshot(target=target, volume_name='SAME-IPOD', volume_uuid='A',
            filesystem='msdos', protocol='USB', device_identifier='fixture', writable=True,
            capacity_bytes=100, free_bytes=50, model='', model_source='', family='iPod Video',
            generation='', identity_key='old-catalog', database_id=1, library_persistent_id=1,
            database_version=25, checksum_scheme=0, database_sha256='a'*64, media_bytes=0,
            pending_playback_records=0, playback_status='', recovery_count=0, tracks=(),
            playlists=(), stock_format=self.profile.stock_format)
        self.identity = DeviceSessionIdentity(self.dev, target.stat().st_dev)
        coordinator = DeviceOperationCoordinator()
        coordinator.observe(self.identity)
        coordinator.last_result = DeviceOperationResult(committed=True)
        h = self.host = SimpleNamespace(lib=Library({}, [], '/synthetic/source.db'),
            overrides={}, titles={}, custom={}, settings={}, _state_maintenance=False,
            _shutting_down=False, _export_batch=None, _exporting=False, _deleting=False,
            _pending_device_operation=None, _stock_view_callbacks={}, _stock_view_pending={},
            _stock_view_warming=set(), _stock_view_failures={}, _stock_view_device='',
            _stock_view_cache={self.dev:(self.snapshot,None)}, _stock_catalog_witness_cache={},
            _stock_snapshot=self.snapshot, _device_profiles={self.dev:self.profile},
            _device_mount_tokens={self.dev:self.identity.mount_token}, _device_coordinator=coordinator,
            _current_device_path=self.dev, _device_finalization_token=None,
            _device_finalization_mounts={}, _device_finalization_refreshing=False,
            _device_finalization_error=None, _eject_ready_device='', _eject_verify_worker=None,
            _stock_art_worker=None, _stock_art_rescan=False, _pulse_word=None,
            _EJECT_BLOCKING_WORKERS=DeviceBrowserMixin._EJECT_BLOCKING_WORKERS)
        for attr in h._EJECT_BLOCKING_WORKERS:
            setattr(h, attr, None)
        h.progress = QProgressBar(self.frame)
        h.progress.setRange(0,1000)
        h.eject_btn, h.device_sync_btn, h.cancel_btn = (QPushButton(self.frame) for _ in range(3))
        h.stock_ipod_pages = SimpleNamespace(set_loading=mock.Mock(), set_snapshot=mock.Mock(),
            set_playlist_deletion_available=mock.Mock(), set_playlist_actions_available=mock.Mock(),
            set_error=mock.Mock())
        h.stock_catalog_witness_store = SimpleNamespace(load=lambda *_:None)
        h._stored_stock_model = lambda *_:''
        h._sync_playlists = lambda:[]
        h._restore_stock_device_view = mock.Mock()
        h._schedule_settings_save = mock.Mock()
        h._schedule_stock_artwork = mock.Mock()
        h._refresh_devices = mock.Mock()
        h._flash_status = mock.Mock()
        h._set_status_text = mock.Mock()
        h._export_target_result = mock.Mock()
        h._maybe_finish_export_target = mock.Mock()
        h._set_device_progress = h.progress.setValue
        h._start_pulse = lambda word:setattr(h,'_pulse_word',word)
        h._stop_pulse = lambda **_:setattr(h,'_pulse_word',None)
        for name in ('_device_operation_owned', '_stock_has_sync_source', '_update_device_action_state',
                     '_release_eject_ready', '_read_stock_device_view', '_discard_stock_view_callbacks',
                     '_finish_stock_device_view_worker', '_refresh_device_surfaces',
                     '_finish_device_surface_refresh', '_finalize_ipod_operation', '_finalize_device_operations',
                     '_on_stock_device_view', '_finish_worker', '_finish_ipod_loading_status',
                     '_fail_initial_ipod_preload'):
            setattr(h,name,MethodType(getattr(MainWindow,name),h))
        def start(worker, path, intent):
            self.workers.append(worker)
            MainWindow._start_device_worker(h,worker,path,intent)
        h._start_device_worker = start
        self.enterContext(mock.patch('desktop.app.device_facts', return_value=DeviceFacts(writable=True)))
        self.enterContext(mock.patch('soundbox.stock_ipod_capture.capture_stock_ipod_native',
                                     side_effect=self.capture))
        self.enterContext(mock.patch('soundbox.stock_ipod_info.read_stock_ipod_snapshot',
                                     side_effect=lambda *_,**__:self.snapshot))
        self.warning = self.enterContext(mock.patch('desktop.app.QMessageBox.warning'))
        self.addCleanup(self.join_workers)

    def capture(self, *args, **kwargs):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError('Isolated worker did not receive its release')
        return object()

    def join_workers(self):
        self.release.set()
        for worker in self.workers:
            try:
                worker.wait()
            except RuntimeError:
                pass
        self.app.processEvents()

    def wait(self, condition):
        deadline = monotonic()+5
        while not condition() and monotonic()<deadline:
            self.app.processEvents(QEventLoop.ExcludeUserInputEvents)
        self.assertTrue(condition(), 'Isolated completion was not delivered')

    def remount(self, dev=None):
        dev = dev or self.dev
        coordinator = self.host._device_coordinator
        coordinator.disconnect(dev)
        replacement = DeviceSessionIdentity(dev,self.identity.mount_token)
        coordinator.observe(replacement)
        return replacement

    def test_standalone_sync_disables_eject_until_real_dashboard_retirement(self):
        h = self.host
        completed = []
        h._finalize_ipod_operation(self.dev,lambda:completed.append(
            (h._stock_view_worker,h._exporting,h.eject_btn.isEnabled(),h._eject_ready_device)))
        self.wait(self.entered.is_set)
        self.assertFalse(h.eject_btn.isEnabled())
        self.assertTrue(h._device_finalization_refreshing)
        self.release.set()
        self.wait(lambda:bool(completed))
        self.assertEqual(completed,[(None,False,True,self.dev)])
        self.assertEqual(h._device_coordinator.last_result.refresh,'complete')
        self.assertEqual(h._stock_view_callbacks,{})
        self.warning.assert_not_called()

    def test_export_from_collection_refreshes_cached_catalog_without_navigation(self):
        h = self.host
        h._current_device_path = ''
        fresh = replace(self.snapshot, database_sha256='b'*64,
            playlists=(StockIpodPlaylistSnapshot(93, 'New exported playlist', 'Playlist', False, ()),))
        completed = []
        with mock.patch('soundbox.stock_ipod_info.read_stock_ipod_snapshot', return_value=fresh):
            h._finalize_ipod_operation(self.dev, lambda:completed.append(
                (h._stock_view_cache[self.dev][0], h._stock_view_worker)))
            self.assertIsNotNone(h._stock_view_worker,
                'A hidden target must refresh its old cached native catalog too')
            self.wait(self.entered.is_set)
            self.assertEqual(completed, [])
            self.assertIs(h._stock_view_cache[self.dev][0], self.snapshot)
            self.release.set()
            self.wait(lambda:bool(completed))
        self.assertEqual(completed, [(fresh, None)])
        self.assertEqual(h._stock_view_cache[self.dev][0].playlists[0].title,
                         'New exported playlist')
        self.assertEqual(h._current_device_path, '')
        self.assertIs(h._stock_snapshot, self.snapshot)
        h.stock_ipod_pages.set_loading.assert_not_called()
        h.stock_ipod_pages.set_snapshot.assert_not_called()
        self.assertEqual(h._device_coordinator.last_result.refresh, 'complete')
        self.warning.assert_not_called()

    def test_same_path_replacement_retires_dashboard_callback_once_without_publication(self):
        h = self.host
        completed = mock.Mock()
        h._finalize_ipod_operation(self.dev,completed)
        self.wait(self.entered.is_set)
        worker = h._stock_view_worker
        self.remount()
        replacement = (self.snapshot, object())
        h._stock_view_cache[self.dev] = replacement
        self.release.set()
        self.wait(lambda:self.warning.called)
        self.assertIs(h._stock_view_cache[self.dev],replacement)
        completed.assert_not_called()
        h._refresh_devices.assert_not_called()
        self.warning.assert_called_once()
        self.assertEqual(worker._stock_view_callbacks,[])
        self.assertEqual(h._eject_ready_device,'')
        self.assertEqual(h._device_coordinator.last_result.refresh,'failed')
        self.app.processEvents()
        self.warning.assert_called_once()

    def test_sidebar_same_path_remount_does_not_replace_a_new_operation_result(self):
        h = self.host
        h._current_device_path = ''
        worker = SimpleNamespace()
        h._refresh_devices = lambda **_:setattr(h,'_dev_scan_worker',worker)
        completed = mock.Mock()
        h._finalize_ipod_operation(self.dev,completed)
        self.wait(self.entered.is_set)
        self.release.set()
        self.wait(lambda:hasattr(worker, '_surface_refresh_callbacks'))
        self.remount()
        new_result = DeviceOperationResult(committed=True,changed_files=37)
        h._device_coordinator.last_result = new_result
        worker._surface_refresh_callbacks[0]()
        worker._surface_refresh_callbacks[0]()
        self.assertIs(h._device_coordinator.last_result,new_result)
        completed.assert_not_called()
        self.warning.assert_called_once()
        self.assertEqual(h._eject_ready_device,'')

    def test_superseded_finalizer_cannot_clear_new_owner_or_show_old_success(self):
        h = self.host
        callbacks = []
        h._refresh_device_surfaces = lambda dev,**kw:callbacks.append(kw['after_finished'])
        old, new = mock.Mock(), mock.Mock()
        h._finalize_ipod_operation(self.dev,old)
        h._device_coordinator.last_result = DeviceOperationResult(committed=True,changed_files=9)
        h._finalize_ipod_operation(self.dev,new)
        token = h._device_finalization_token
        callbacks[0]()
        self.assertIs(h._device_finalization_token,token)
        self.assertTrue(h._exporting)
        self.assertEqual(h._pulse_word,'Finalizing iPod')
        old.assert_not_called()
        callbacks[1]()
        new.assert_called_once_with()
        self.assertEqual(h._device_coordinator.last_result.changed_files,9)

    def test_multi_target_captures_later_mount_before_first_refresh(self):
        h = self.host
        other = str(Path(self.area)/'OTHER')
        h._device_coordinator.observe(DeviceSessionIdentity(other,self.identity.mount_token))
        h._device_profiles[other] = self.profile
        h._current_device_path = ''
        worker = SimpleNamespace()
        h._refresh_devices = mock.Mock(side_effect=lambda **_:setattr(h,'_dev_scan_worker',worker))
        completed = mock.Mock()
        h._finalize_device_operations([self.dev,other],completed)
        self.remount(other)
        self.wait(self.entered.is_set)
        self.release.set()
        self.wait(lambda:hasattr(worker, '_surface_refresh_callbacks'))
        worker._surface_refresh_callbacks[0]()
        completed.assert_not_called()
        self.warning.assert_called_once()
        h._refresh_devices.assert_called_once()

    def test_shutdown_consumes_queued_completion_without_new_reads_or_ui(self):
        h = self.host
        h._finalize_ipod_operation(self.dev,mock.Mock())
        self.wait(self.entered.is_set)
        completion = h._stock_view_worker._stock_view_callbacks[0]
        h._shutting_down = True
        self.release.set()
        self.wait(lambda:h._stock_view_worker is None)
        self.assertTrue(completion.completed)
        h._refresh_devices.assert_not_called()
        self.warning.assert_not_called()
        self.assertFalse(h._exporting)

    def test_discarded_pending_completion_never_replays_on_replacement(self):
        h = self.host
        callback = mock.Mock()
        completion = _DeviceRefreshCompletion(h,self.dev,self.identity,None,callback)
        h._stock_view_callbacks[self.dev] = [completion]
        self.remount()
        h._discard_stock_view_callbacks(self.dev,'Original mount disappeared')
        self.assertEqual(h._stock_view_callbacks,{})
        self.wait(lambda:completion.completed)
        callback.assert_called_once_with()
        self.assertIn('remounted',str(h._device_finalization_error))
        completion.finish()
        callback.assert_called_once_with()

    def test_ordinary_refresh_cannot_mutate_a_later_finalization_owner(self):
        h = self.host
        callbacks = []
        h._read_stock_device_view = lambda _dev,_profile,**kw:callbacks.append(kw['after_finished'])
        ordinary = mock.Mock()
        h._refresh_device_surfaces(self.dev,after_finished=ordinary)
        h._device_finalization_token = object()
        h._device_finalization_refreshing = True
        h._exporting = True
        h.progress.setValue(811)
        callbacks[0]()
        self.assertTrue(h._exporting)
        self.assertEqual(h.progress.value(),811)
        self.assertIsNone(h._device_finalization_error)
        ordinary.assert_not_called()
        h._refresh_devices.assert_not_called()
        completion = _DeviceRefreshCompletion(h,self.dev,self.identity,None,ordinary)
        completion.finish(RuntimeError('Old ordinary read failed'))
        self.assertTrue(completion.completed)
        self.assertIsNone(h._device_finalization_error)
        ordinary.assert_not_called()

    def test_ordinary_sidebar_callback_cannot_release_later_finalization(self):
        h = self.host
        worker = SimpleNamespace()
        h._refresh_devices = lambda **_:setattr(h,'_dev_scan_worker',worker)
        ordinary = mock.Mock()
        h._finish_device_surface_refresh(ordinary,expected_dev=self.dev,
            expected_identity=self.identity)
        h._device_finalization_token = object()
        h._device_finalization_refreshing = True
        h._exporting = True
        worker._surface_refresh_callbacks[0]()
        self.assertTrue(h._exporting)
        self.assertIsNone(h._device_finalization_error)
        ordinary.assert_not_called()
