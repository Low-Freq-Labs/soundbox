"""Targeted effective-row capture and scalar stock status publication."""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
from threading import get_ident
from time import monotonic
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.workers import DeviceScanWorker
from soundbox import stock_ipod
from soundbox.library import Track
from soundbox.device_operations import DeviceOperationCoordinator, DeviceSessionIdentity
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_capture import capture_stock_ipod_native
from soundbox.stock_ipod_shadow import StockIpodSyncStatus
from tests import test_ipod_export_effective_snapshot as export_fixture
from tests import test_device_navigation_publication as navigation_fixture
from tests.support.ipod import play_counts, rewrite_catalog


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
        self.host = SimpleNamespace(lib=f.library,overrides={},titles={},art_cache=None,_shutting_down=False,
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

    def scan(self, *, cold=False, inspections=1):
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
        self.assertEqual(len(threads),inspections)
        self.assertTrue(all(thread!=main for thread in threads))
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

    def test_real_clean_postcommit_scan_returns_both_actions_to_export_and_later_drift_to_sync(self):
        self.desired['old']['title']='Generated committed display change'
        dirty=self.scan()
        self.assertGreater(dirty.results[0][3],0)
        result=stock_ipod.commit_stock_ipod_sync(self.case._plan(self.case._capture(self.desired)))
        self.assertFalse(result.catalog_warning)
        self.stored_witness=result.catalog_witness
        self.host._stock_catalog_witness_cache[self.dev]=result.catalog_witness
        clean=self.scan()
        self.assertEqual(clean.results[0][3],0)
        self.assertEqual(clean.stock_status[self.dev].state,'up_to_date')

        case=navigation_fixture.DeviceNavigationPublication();case.setUp()
        self.addCleanup(case.doCleanups)
        w=case.window
        for timer in w.findChildren(QTimer):timer.stop()
        profile=clean.results[0][4]
        profile.label='Generated iPod';profile.has_rockbox=False;profile.rockbox_export=False
        profile.stock_preview=True;profile.cdj_export=False
        profile.stock_format=self.snapshot.stock_format
        w.lib=self.f.library;w.custom={}
        w._device_profiles={self.dev:profile}
        w._device_mount_tokens={self.dev:self.token}
        w._device_coordinator=DeviceOperationCoordinator()
        w._device_coordinator.observe(DeviceSessionIdentity(self.dev,self.token))
        w._current_device_path=self.dev
        fresh=read_stock_ipod_snapshot(self.f.ipod,volume_uuid='generated-volume',
            native_capture=capture_stock_ipod_native(self.f.ipod,include_artwork=True))
        status=clean.stock_status[self.dev]
        fresh=replace(fresh,sync_state=status.state,sync_detail=status.detail,
            sync_local_changes=status.local_changes,sync_committed_at=status.committed_at)
        w._stock_view_cache={self.dev:(fresh,None)};w._stock_snapshot=fresh
        w._stock_catalog_witness_cache={self.dev:result.catalog_witness}
        w._drift_cache={self.dev:dirty.results[0][3]}
        pages=w.stock_ipod_pages;pages.set_snapshot(fresh)
        facts=DeviceFacts(volume_uuid='generated-volume',filesystem_name='fat32',writable=True)
        completed=[]
        def publish(worker):
            for _name,_path,_free,_drift,observed in worker.results:
                observed.label='Generated iPod';observed.has_rockbox=False;observed.rockbox_export=False
                observed.stock_preview=True;observed.cdj_export=False
                observed.stock_format=self.snapshot.stock_format
            w._on_devices_scanned(worker.results,mount_tokens=worker.mount_tokens)
            w._publish_stock_sync_observations(worker)
        def refreshed(_dev,*,after_finished,**_options):
            publish(clean)
            after_finished()
        with (mock.patch('desktop.app.device_facts',return_value=facts),
              mock.patch('desktop.app.device_fs',return_value='fat32'),
              mock.patch.object(w,'_refresh_device_surfaces',side_effect=refreshed)):
            w._update_device_action_state()
            self.assertEqual((w.device_sync_btn.text(),pages.sync_playlist_button.text()),('Sync','Sync'))
            # A normal poll must also publish its changed drift action when
            # the native view's detailed scalar status was already identical.
            publish(clean)
            self.assertEqual((w.device_sync_btn.text(),pages.sync_playlist_button.text()),('Export','Export'))
            w._drift_cache[self.dev]=dirty.results[0][3]
            w._update_device_action_state()
            # The native view is already clean while old sidebar drift remains;
            # the real finalizer must publish the actual clean scan before its
            # completion boundary, rather than clearing flags on commit alone.
            w._finalize_ipod_operation(self.dev,lambda:completed.append(
                (w.device_sync_btn.text(),pages.sync_playlist_button.text())))
            self.assertEqual(completed,[('Export','Export')])
            self.assertEqual(w._drift_cache[self.dev],0)
            self.assertIsNone(w._device_finalization_token)
            self.desired['old']['title']='Generated later unsynced edit'
            later=self.scan()
            self.assertGreater(later.results[0][3],0)
            publish(later)
            self.assertEqual((w.device_sync_btn.text(),pages.sync_playlist_button.text()),('Sync','Sync'),
                (w._drift_cache,w._stock_view_cache[self.dev][0].sync_state,
                 later.stock_status[self.dev],w._stock_view_pending))

    def test_adopted_export_identity_survives_native_catalog_and_fresh_library_readback(self):
        from soundbox import manifest,playlist_unification,rekordbox_playlist_plan
        from soundbox.library import Playlist
        from soundbox.rekordbox_playlist_store import request_for
        case=navigation_fixture.DeviceNavigationPublication();case.setUp()
        self.addCleanup(case.doCleanups)
        w=case.window
        for timer in w.findChildren(QTimer):timer.stop()
        uuid='00000000-0000-4000-8000-000000001000'
        catalog=dict(nodes=[dict(ID='1000',UUID=uuid,Name='Adopted',ParentID='root',
            Seq=1,Attribute=0,rb_local_deleted=0)],members=[dict(ID=str(index),
            UUID=f'00000000-0000-4000-8000-{index:012d}',PlaylistID='1000',
            ContentID=key,TrackNo=index,rb_local_deleted=0) for index,key in enumerate(('1','2'),1)])
        catalog['revision']=rekordbox_playlist_plan.revision(catalog['nodes'],catalog['members'])
        w.lib.playlists=[Playlist('Adopted',[],['1','2'],'1000')]
        w.lib.source_playlist_catalog=catalog
        state=playlist_unification.new(w.lib.source,w.lib.source_library_id)
        state['customs_done']=True;state['nodes']={'custom:Adopted':dict(id='1000',uuid=uuid)}
        w.settings[playlist_unification.KEY]=state;w.custom={'Adopted':['1','2']}
        w._apply_settings_to_lib(configure_artwork=False)
        baseline={'playlists':{'cust:Adopted':manifest.entry_for(w.lib.playlists[0])}}
        def assert_clean():
            self.assertEqual(w.lib.playlists[0].export_key,'cust:Adopted')
            self.assertFalse(any(p.pid for p in w._sync_playlists()))
            self.assertFalse(manifest.diff(baseline,w.lib,w._sync_playlists(),expected_profile=None).any())
        assert_clean()
        for prepared in (None,()):
            with self.subTest(prepared=prepared):
                raw=replace(w.lib,playlists=[replace(p,export_key='') for p in w.lib.playlists])
                # Raw source/cache rows alone would report exactly the phantom
                # removal. Production publication must derive the accepted alias
                # before the observer sees this library, including sparse ACKs.
                self.assertEqual(manifest.diff(baseline,raw,expected_profile=None).removed,
                    [('cust:Adopted','Adopted')])
                self.assertTrue(w._on_refresh_done(raw,prepared_native_ids=prepared))
                assert_clean()
        # Ordinary native edit publication reconstructs Playlist objects from
        # the returned catalog rather than preserving the old Python objects.
        request=request_for(catalog,'rename',identity='1000',name='Adopted')
        old=w.lib.playlists[0]
        w._native_apply_catalog(deepcopy(catalog),request)
        self.assertIsNot(w.lib.playlists[0],old)
        assert_clean()

    def test_playback_changes_refresh_once_merge_from_native_and_keep_header_clean(self):
        from desktop.stock_catalog_sync import has_sync_source, needs_sync
        self.host.custom = {}
        path = self.f.ipod / 'iPod_Control/iTunes/Play Counts'
        initial = {row.dbid: (row.play_count, row.skip_count) for row in self.snapshot.tracks}
        payload = play_counts([(2, 1, 80, 12000)] * len(self.snapshot.tracks))
        path.write_bytes(payload)
        observed = self.scan()
        self.assertEqual(observed.results[0][3], 0)
        self.host._drift_cache = {self.dev: observed.results[0][3]}
        self.assertTrue(has_sync_source(self.host, self.dev))
        self.assertFalse(needs_sync(self.host, self.dev))
        MainWindow._publish_stock_sync_observations(self.host, observed)
        self.assertEqual(set(self.host._stock_view_pending), {self.dev})
        self.refresh_cached_view()
        current = self.host._stock_view_cache[self.dev][0]
        self.assertTrue(current.play_counts_sha256)
        for row in current.tracks:
            self.assertEqual((row.play_count, row.skip_count), (initial[row.dbid][0] + 2, initial[row.dbid][1] + 1))
            self.assertEqual((row.rating, row.bookmark_time), (80, 12000))
        for _ in range(2):
            same = self.scan()
            self.assertEqual(same.results[0][3], 0)
            MainWindow._publish_stock_sync_observations(self.host, same)
            self.assertFalse(self.host._stock_view_pending)
        self.assertEqual(path.read_bytes(), payload)
        path.unlink()
        removed = self.scan()
        MainWindow._publish_stock_sync_observations(self.host, removed)
        self.assertEqual(set(self.host._stock_view_pending), {self.dev})
        self.refresh_cached_view()
        current = self.host._stock_view_cache[self.dev][0]
        self.assertEqual(current.play_counts_sha256, '')
        self.assertEqual({row.dbid: (row.play_count, row.skip_count) for row in current.tracks}, initial)

    def test_external_addition_refreshes_warm_and_cold_views_without_sync_or_repeated_reads(self):
        self.host.custom = {}
        rewrite_catalog(self.f.ipod, external=True)
        for cold in (False, True):
            with self.subTest(cold=cold):
                self.host._stock_view_cache[self.dev] = (self.snapshot, None)
                if cold:self.host._stock_catalog_witness_cache.clear()
                observed = self.scan(cold=cold)
                self.assertIn(observed.results[0][3], (None, 0))
                MainWindow._publish_stock_sync_observations(self.host, observed)
                self.assertEqual(set(self.host._stock_view_pending), {self.dev})
                self.refresh_cached_view()
                current = self.host._stock_view_cache[self.dev][0]
                self.assertIn('External playlist', {row.title for row in current.playlists})
                clean = self.scan()
                self.assertEqual(clean.results[0][3], 0)
                MainWindow._publish_stock_sync_observations(self.host, clean)
                self.assertFalse(self.host._stock_view_pending)

    def test_cold_persisted_witness_without_projected_rows_is_unknown(self):
        worker = self.scan(cold=True)
        self.assertIsNone(worker.results[0][3])
        self.assertEqual(worker.stock_status[self.dev].state,'comparison_pending')

    def test_worker_refreshes_stale_art_revision_without_mutating_the_publisher_or_opening_audio(self):
        from soundbox.artwork import ArtCache
        from soundbox.artwork_identity import BoundArtCache
        cache=ArtCache(self.f.base/'observer-art').for_library(self.f.library)
        self.host.art_cache=cache
        old_token=cache.selection_token(('old','new'))
        other=BoundArtCache(cache.root,self.f.library)
        other.store_none('old')
        self.assertFalse(cache.selection_current(old_token))
        revision=cache._revision;owner=cache.scope_owner;threads=[]
        original=cache.observation_view
        def observe(library):
            threads.append(get_ident())
            return original(library)
        with (mock.patch.object(cache,'observation_view',side_effect=observe) as observed,
              mock.patch.object(BoundArtCache,'__init__',side_effect=AssertionError('Observer constructed a cache')),
              mock.patch.object(cache._area,'publish',side_effect=AssertionError('Observer wrote art state')),
              mock.patch.object(cache,'_rebuild_default_assignments',side_effect=AssertionError('Observer rebalanced defaults'))):
            for _ in range(2):
                worker=self.scan()
                self.assertEqual(worker.results[0][3],0)
                self.assertEqual(worker.stock_status[self.dev].state,'up_to_date')
                self.assertFalse(worker.stock_comparison_errors)
            self.refresh_cached_view()
            self.assertEqual(self.host._stock_view_cache[self.dev][0].sync_state,'up_to_date')
            self.assertEqual(observed.call_count,3)
        self.assertTrue(all(thread!=get_ident() for thread in threads))
        self.assertIs(self.host.art_cache,cache)
        self.assertIs(cache.scope_owner,owner)
        self.assertEqual(cache._revision,revision)
        self.assertFalse(cache.selection_current(old_token))

    def test_retired_art_comparison_failure_logs_once_until_recovery_without_clearing_drift(self):
        from soundbox.artwork import ArtCache
        from soundbox.artwork_identity import BoundArtCache
        from desktop.stock_catalog_sync import needs_sync
        cache=ArtCache(self.f.base/'observer-retired-art').for_library(self.f.library)
        current=BoundArtCache(cache.root,self.f.library)
        self.host.art_cache=cache;self.host.custom={};cache.retire()
        fresh=read_stock_ipod_snapshot(self.f.ipod,volume_uuid='generated-volume',
            native_capture=capture_stock_ipod_native(self.f.ipod,include_artwork=True))
        pending=replace(fresh,sync_state='local_changes',sync_local_changes=629)
        self.host._stock_view_cache[self.dev]=(pending,None)
        self.host._drift_cache={self.dev:None}
        with mock.patch('desktop.app.LOG.warning') as warning:
            for _ in range(2):
                worker=self.scan(inspections=0)
                self.assertIsNone(worker.results[0][3])
                self.assertNotIn(self.dev,worker.stock_status)
                self.assertIn('StateChangedError',worker.stock_comparison_errors[self.dev])
                MainWindow._publish_stock_sync_observations(self.host,worker)
            warning.assert_called_once()
            self.assertTrue(needs_sync(self.host,self.dev))
            self.assertIs(self.host._stock_view_cache[self.dev][0],pending)
            self.host.art_cache=current
            clean=self.scan()
            self.assertEqual(clean.results[0][3],0)
            MainWindow._publish_stock_sync_observations(self.host,clean)
            self.assertFalse(self.host._stock_comparison_warnings)
            self.assertEqual(self.host._stock_view_cache[self.dev][0].sync_state,'up_to_date')

    def test_queued_art_observations_cannot_publish_after_revision_retirement_restore_or_owner_change(self):
        from soundbox.artwork import ArtCache
        from soundbox.artwork_identity import BoundArtCache
        for change in ('revision','retirement','restore','owner'):
            with self.subTest(change=change):
                cache=ArtCache(self.f.base/('observer-fence-'+change)).for_library(self.f.library)
                self.host.art_cache=cache
                worker=self.scan()
                self.assertEqual(worker.results[0][3],0)
                self.assertTrue(worker.comparison_current(cache))
                if change=='revision':cache.store_none('old')
                elif change=='retirement':cache.retire()
                elif change=='restore':cache._area.generation+=1
                else:self.host.art_cache=BoundArtCache(cache.root,self.f.library)
                self.assertFalse(worker.comparison_current(self.host.art_cache))
                before=self.host._stock_view_cache[self.dev]
                self.host.stock_ipod_pages.set_sync_status.reset_mock()
                MainWindow._publish_stock_sync_observations(self.host,worker)
                self.assertIs(self.host._stock_view_cache[self.dev],before)
                self.host.stock_ipod_pages.set_sync_status.assert_not_called()
                self.assertFalse(self.host._stock_view_pending)

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
