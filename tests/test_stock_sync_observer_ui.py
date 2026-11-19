"""Targeted effective-row capture and scalar stock status publication."""
from dataclasses import replace
from pathlib import Path
from threading import get_ident
from time import monotonic
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.workers import DeviceScanWorker
from soundbox import stock_ipod
from soundbox.library import Track
from soundbox.device_operations import DeviceOperationCoordinator, DeviceSessionIdentity
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_shadow import StockIpodSyncStatus
from tests import test_ipod_export_effective_snapshot as export_fixture


class StockSyncObserverUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.case = export_fixture.EffectiveIpodExport(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.f = f = self.case.fixture
        self.desired = export_fixture._rows(('old','new'))
        result = stock_ipod.commit_stock_ipod_sync(self.case._plan(self.case._capture(self.desired)))
        self.dev = str(f.ipod.resolve(strict=True))
        self.snapshot = read_stock_ipod_snapshot(f.ipod,volume_uuid='generated-volume')
        self.token = f.ipod.stat().st_dev
        self.stored_witness = result.catalog_witness
        self.store = SimpleNamespace(load=lambda _:self.stored_witness)
        self.host = SimpleNamespace(lib=f.library,overrides={},titles={},_shutting_down=False,
            _stock_catalog_witness_cache={self.dev:result.catalog_witness},
            stock_catalog_witness_store=self.store,stock_ipod_shadow_store=self.case.shadow,
            _sync_playlists=lambda:(),_managed_ipod_display_ids=mock.Mock(
                side_effect=AssertionError('Stock capture walked a composite playlist')),
            _row_for=mock.Mock(side_effect=lambda track:dict(id=track.track_id,**self.desired[track.track_id])),
            _stock_view_cache={self.dev:(self.snapshot,None)},_current_device_path=self.dev,
            _stock_view_pending={},
            _stock_snapshot=self.snapshot,_device_mount_tokens={self.dev:self.token},
            _device_profiles={self.dev:SimpleNamespace(kind='stock_ipod')},
            stock_ipod_pages=SimpleNamespace(set_sync_status=mock.Mock(),set_snapshot=mock.Mock(
                side_effect=AssertionError('Scalar status rebuilt native tables'))),
            _update_device_action_state=mock.Mock())

    def scan(self, *, cold=False):
        f = self.f; host = self.host
        worker = DeviceScanWorker(None,defer_playlists=True)
        host._dev_scan_worker = worker
        profile = SimpleNamespace(kind='stock_ipod',has_stock_ipod=True,model='MA446',stock_write=False,
                                  library_persistent_id=self.snapshot.library_persistent_id)
        threads = []
        inspector = stock_ipod.inspect_stock_ipod_sync_status
        def inspect(*args,**kwargs):
            threads.append(get_ident())
            return inspector(*args,**kwargs)
        def provide(value):
            self.assertEqual(get_ident(),main)
            if cold:
                value.provide_playlists((),lib=f.library,artist_overrides={},title_overrides={},
                    stock_shadow_store=self.case.shadow,stock_catalog_store=self.store)
            else:
                MainWindow._supply_device_scan_playlists(host,value)
        main = get_ident()
        worker.playlists_requested.connect(provide,Qt.QueuedConnection)
        original_open = Path.open
        def no_audio(path,*args,**kwargs):
            if path.suffix.casefold()=='.wav':
                raise AssertionError('Status observation opened source or device audio')
            return original_open(path,*args,**kwargs)
        with (
            mock.patch('desktop.workers.list_devices',return_value=[('Generated',self.dev,1000)]),
            mock.patch('desktop.devices.discovery_needs_retry',return_value=False),
            mock.patch('desktop.workers.device_profile',return_value=profile),
            mock.patch('desktop.workers.device_facts',return_value=DeviceFacts(volume_uuid='generated-volume')),
            mock.patch.object(f.library,'resolve',side_effect=AssertionError('Observer resolved playlist songs')),
            mock.patch.object(Path,'open',no_audio),
            mock.patch.object(stock_ipod,'inspect_stock_ipod_sync_status',side_effect=inspect),
        ):
            try:
                worker.start()
                until = monotonic()+5
                while worker.isRunning() and monotonic()<until:
                    self.app.processEvents(QEventLoop.ExcludeUserInputEvents)
                self.assertTrue(worker.wait(1000),'Generated status worker did not retire')
            finally:
                worker.cancel(); worker.wait()
        self.assertEqual(len(threads),1)
        self.assertNotEqual(threads[0],main)
        self.assertIs(worker.results[0][4],profile)
        self.assertFalse(profile.stock_write)
        worker.deleteLater()
        return worker

    def test_actual_worker_title_artist_pending_then_refreshed_witness_clean(self):
        self.f.library.tracks_by_id['unselected'] = Track('unselected','Never captured','Artist',
                                                        'file:///unselected/never-open.wav')
        clean = self.scan()
        self.assertEqual(clean.results[0][3],0)
        self.assertEqual(set(clean.display_rows),{'old','new'})
        self.assertEqual(set(clean.stock_catalog_witnesses),{self.dev})
        self.desired['old']['title'] = 'New displayed title'
        dirty = self.scan()
        self.assertGreater(dirty.results[0][3],0)
        self.assertEqual(dirty.stock_status[self.dev].state,'local_changes')
        self.desired['old']['artist'] = 'New displayed artist'
        self.assertGreater(self.scan().results[0][3],0)
        plan = self.case._plan(self.case._capture(self.desired))
        result = stock_ipod.commit_stock_ipod_sync(plan)
        self.host._stock_catalog_witness_cache[self.dev] = result.catalog_witness
        self.stored_witness = result.catalog_witness
        self.assertEqual(self.scan().results[0][3],0)
        self.assertNotIn('unselected',{call.args[0].track_id for call in self.host._row_for.call_args_list})

    def test_cold_persisted_witness_without_projected_rows_is_unknown(self):
        worker = self.scan(cold=True)
        self.assertIsNone(worker.results[0][3])
        self.assertEqual(worker.stock_status[self.dev].state,'comparison_pending')

    def test_cold_known_view_refreshes_scoped_witness_then_next_capture_completes(self):
        self.host._stock_catalog_witness_cache.clear()
        pending = self.scan(cold=True)
        self.assertIsNone(pending.results[0][3])
        MainWindow._publish_stock_sync_observations(self.host,pending)
        self.assertEqual(set(self.host._stock_view_pending),{self.dev})
        self.assertNotIn(self.dev,self.host._stock_catalog_witness_cache)
        self.refresh_cached_view()
        self.assertIs(self.host._stock_catalog_witness_cache[self.dev],self.stored_witness)
        clean = self.scan()
        self.assertEqual(set(clean.display_rows),{'old','new'})
        self.assertEqual(clean.results[0][3],0)
        MainWindow._publish_stock_sync_observations(self.host,clean)
        self.assertEqual(self.host._stock_view_cache[self.dev][0].sync_state,'up_to_date')

    def test_observed_changed_catalog_uses_one_real_joined_view_refresh(self):
        self.desired['old']['title'] = 'Later exported title'
        result = stock_ipod.commit_stock_ipod_sync(self.case._plan(self.case._capture(self.desired)))
        self.stored_witness = result.catalog_witness
        self.assertNotEqual(self.snapshot.database_sha256,result.database_sha256)
        observed = self.scan()
        MainWindow._publish_stock_sync_observations(self.host,observed)
        MainWindow._publish_stock_sync_observations(self.host,observed)
        self.assertEqual(set(self.host._stock_view_pending),{self.dev})
        self.refresh_cached_view()
        self.assertEqual(self.host._stock_view_cache[self.dev][0].database_sha256,result.database_sha256)
        self.assertIs(self.host._stock_catalog_witness_cache[self.dev],result.catalog_witness)
        self.assertEqual(self.scan().results[0][3],0)

    def refresh_cached_view(self):
        """Run the actual joined native reader used by the serialized lane."""
        host = self.host
        host._dev_scan_worker = None; host._stock_view_worker = None
        host._stock_view_callbacks = {}; host._stock_view_device = ''
        host._device_finalization_token = None; host._device_finalization_mounts = {}
        host._state_maintenance = False; host._exporting = False
        host._stock_view_failures = {}; host._stock_view_warming = set()
        host._stock_art_worker = None; host._stock_art_rescan = False
        host._device_mount_refresh_pending = False
        host._current_device_path = ''
        host._device_coordinator = DeviceOperationCoordinator()
        host._device_coordinator.observe(DeviceSessionIdentity(self.dev,self.token))
        host._stored_stock_model = lambda *_:'MA446'
        host.settings = {}
        for name in ('_schedule_settings_save','_schedule_stock_artwork','_finish_ipod_loading_status',
                     '_flash_status','_on_device_phase_progress'):
            setattr(host,name,mock.Mock())
        for name in ('_read_stock_device_view','_finish_stock_device_view_worker','_finish_worker',
                     '_on_stock_device_view'):
            setattr(host,name,MethodType(getattr(MainWindow,name),host))
        starts = []
        def start(worker,path,intent):
            starts.append(worker)
            MainWindow._start_device_worker(host,worker,path,intent)
        host._start_device_worker = start
        with mock.patch('desktop.app.device_facts',return_value=DeviceFacts(
                volume_uuid='generated-volume',filesystem_name='fat32',writable=True)), \
             mock.patch('desktop.app.device_fs',return_value='fat32'):
            host._read_stock_device_view(self.dev)
            worker = host._stock_view_worker
            try:
                until = monotonic()+5
                while host._stock_view_worker is not None and monotonic()<until:
                    self.app.processEvents(QEventLoop.ExcludeUserInputEvents)
                self.assertIsNone(host._stock_view_worker,'Fresh native view did not join')
            finally:
                worker.cancel(); worker.wait()
        self.assertFalse(host._stock_view_pending)
        self.assertEqual(len(starts),1)
        host.stock_ipod_pages.set_snapshot.assert_not_called()

    def test_scalar_publication_preserves_catalog_and_rejects_stale_source_mount_or_digest(self):
        host = self.host
        scope = (str(host.lib.source),host.lib.source_kind,host.lib.source_library_id)
        status = StockIpodSyncStatus('local_changes','Current title changed',1,'verified',
                                    self.snapshot.database_sha256)
        worker = SimpleNamespace(captured_source_scope=scope,mount_tokens={self.dev:self.token},
                                 stock_status={self.dev:status})
        for change in ('source','mount','catalog','artwork'):
            with self.subTest(change=change):
                wrong = SimpleNamespace(**worker.__dict__)
                if change=='source':wrong.captured_source_scope=('different','source','scope')
                if change=='mount':wrong.mount_tokens={self.dev:self.token+1}
                if change=='catalog':wrong.stock_status={self.dev:replace(status,database_sha256='f'*64)}
                if change=='artwork':wrong.stock_status={self.dev:replace(status,artwork_database_sha256='f'*64)}
                MainWindow._publish_stock_sync_observations(host,wrong)
                self.assertIs(host._stock_view_cache[self.dev][0],self.snapshot)
                host.stock_ipod_pages.set_sync_status.assert_not_called()
                self.assertEqual(set(host._stock_view_pending),
                                 {self.dev} if change in {'catalog','artwork'} else set())
                host._stock_view_pending.clear()
        MainWindow._publish_stock_sync_observations(host,worker)
        published = host._stock_view_cache[self.dev][0]
        self.assertIs(published.tracks,self.snapshot.tracks)
        self.assertIs(published.playlists,self.snapshot.playlists)
        self.assertEqual(host._current_device_path,self.dev)
        self.assertIs(host._stock_snapshot,published)
        host.stock_ipod_pages.set_sync_status.assert_called_once_with('local_changes','Current title changed',1,'verified')
        host.stock_ipod_pages.set_snapshot.assert_not_called()
