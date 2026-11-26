"""One stock Export reconciles exact managed links without re-exporting audio."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.stock_catalog_sync import stock_export_selection
from soundbox import stock_ipod
from soundbox.artwork import ArtCache,ArtResult
from soundbox.export_snapshot import capture_export
from soundbox.library import Playlist,Track
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_catalog_state import StockCatalogWitnessStore
from tests import test_export_playlist_baseline as baseline
from tests import test_stock_ipod as audio


class StockExportSyncBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.case=baseline.ActualExportPlaylistBaseline();self.case.setUp()
        self.addCleanup(self.case.doCleanups);self.f=self.case.f
        self.a=self.f.playlist;self.a.name='A managed';self.a.keys[:]=['old']
        self.b=Playlist('B clean',[],['new'],'managed-b')
        self.c=Playlist('C new',[],['c'],'new-c')
        path=self.f.sources/'third.wav';audio._wave(path,441,900)
        self.f.library.tracks_by_id['c']=Track('c','Third song','Fixture Artist',path.as_uri(),
            source_native_id='native-c',source_uuid=str(uuid4()),source_native_path=str(path))
        self.f.library.playlists=[self.a,self.b,self.c]
        self.f.library.source_playlist_catalog['nodes'].extend(
            dict(ID=row.pid,UUID=str(uuid4()),rb_local_deleted=0) for row in (self.b,self.c))
        self.f.art_cache=ArtCache((self.f.base/'combined-art').resolve()).for_library(self.f.library)
        cover=ArtResult((self.f.base/'curated.jpg').read_bytes(),'image/jpeg',1.,'file:///generated/cover.jpg','Cover')
        for key in self.f.library.tracks_by_id:self.f.art_cache.store_ok(key,cover,origin='manual')
        self.store=StockCatalogWitnessStore(self.f.base/'witnesses')
        initial=self.engine_plan([self.a,self.b])
        self.first=stock_ipod.commit_stock_ipod_sync(initial)
        self.store.save(self.first.catalog_witness)
        self.snapshot=read_stock_ipod_snapshot(self.f.ipod,model_number='MA446',volume_uuid='generated-volume',
            filesystem='fat32',writable=True,usb_family='iPod Video',
            native_capture=stock_ipod.capture_stock_ipod_native(self.f.ipod,include_artwork=True))
        self.dev=str(self.f.ipod)
        self.facts=DeviceFacts(volume_uuid='generated-volume',writable=True,usb_family='iPod Video')
        self.host=SimpleNamespace(lib=self.f.library,custom={},settings={},titles={},overrides={},
            _shutting_down=False,_state_maintenance=False,_exporting=False,
            _device_profiles={self.dev:SimpleNamespace(kind='stock_ipod',model='MA446')},
            _stock_view_cache={self.dev:(self.snapshot,None)},_drift_cache={self.dev:1},
            _stock_catalog_witness_cache={self.dev:self.first.catalog_witness},
            _device_mount_tokens={self.dev:self.f.ipod.stat().st_dev},
            _data_file=self.f.base/'state/library.json',stock_ipod_shadow_store=self.case.shadow,
            stock_catalog_witness_store=self.store,stock_art_cache=self.f.stock_art_cache,
            art_cache=self.f.art_cache,_export_batch=None)
        self.host._export_playlists=lambda **_options:list(self.f.library.playlists)
        self.host._sync_playlists=lambda:[];self.host._custom_playlists=lambda:[]
        self.host._row_for=lambda track:self.rows()[track.track_id]
        self.host._device_operation_owned=lambda:False
        self.host._defer_device_operation_until_scan_finishes=lambda _callback:False
        self.host._stock_write_access=lambda *_args:initial.stock_format
        self.host._stock_device_key=lambda *_args:self.case.key
        self.host._stock_plan_worker=None;self.workers=[]
        self.host._start_device_worker=lambda worker,*_args:self.workers.append(worker)
        self.host._finish_device_result_worker=mock.Mock()
        for name in ('_release_eject_ready','_set_device_progress','_start_pulse','_flash_status',
                     '_export_target_result','_validate_export_target','_request_ipod_export_capture'):
            setattr(self.host,name,mock.Mock())
        self.host.cancel_btn=mock.Mock()
        self.enterContext(mock.patch('desktop.app.device_facts',return_value=self.facts))
        self.enterContext(mock.patch('desktop.export_wait.device_facts',return_value=self.facts))
        self.enterContext(mock.patch('desktop.app.device_fs',return_value='fat32'))
        self.addCleanup(lambda:[worker.deleteLater() for worker in self.workers])

    def rows(self):
        return {key:dict(id=key,title=track.name,artist=track.artist,album='',genre='',comments='',year=0,bpm=0)
                for key,track in self.f.library.tracks_by_id.items()}

    def engine_plan(self,selected,**options):
        return stock_ipod.plan_stock_ipod_sync(self.f.library,selected,self.f.ipod,
            model_number='MA446',filesystem='fat32',usb_family='iPod Video',identity_key=self.case.key,
            shadow_store=self.case.shadow,display_rows=self.rows(),art_cache=self.f.art_cache,
            stock_art_cache=self.f.stock_art_cache,**options)

    def plan_through_desktop(self,selected,*,reconcile=False,scope=None):
        frozen=capture_export(self.f.library,selected,complete=True,playlist_identities=True,display_rows=self.rows())
        capture=SimpleNamespace(snapshot=frozen,require_current=mock.Mock(),verify_worker=mock.Mock(),artwork=None)
        MainWindow._begin_stock_ipod_sync(self.host,self.dev,list(selected),reconcile=reconcile,
            _export_capture=capture,**({'_stock_export_scope':scope} if scope is not None else {}))
        worker=self.workers[-1];worker.run()
        self.assertNotIsInstance(worker.result,Exception,str(worker.result))
        return worker.result[0] if isinstance(worker.result,tuple) else worker.result

    def unchanged_audio_guard(self):
        actual=stock_ipod.acquire_source;observed=[]
        def acquired(contract,*args,**kwargs):
            observed.append(contract.canonical)
            self.assertNotIn(contract.canonical,{self.f.existing_source.resolve(),self.f.new_source.resolve()},
                             'Unchanged managed audio was reacquired')
            return actual(contract,*args,**kwargs)
        return mock.patch.object(stock_ipod,'acquire_source',side_effect=acquired),observed

    def test_new_export_and_dirty_managed_list_use_one_plan_commit_and_only_one_new_copy(self):
        self.f.library.tracks_by_id['old'].name='Changed managed title'
        self.a.keys.append('c')
        self.host._drift_cache[self.dev]=0  # The asynchronous badge has not caught the edit.
        selected,scope=stock_export_selection(self.host,self.dev,[self.c])
        self.assertEqual([row.pid for row in selected],[self.a.pid,self.b.pid,self.c.pid])
        before_b=next(row for row in self.snapshot.playlists if row.title==self.b.name)
        guard,observed=self.unchanged_audio_guard()
        with guard:plan=self.plan_through_desktop(selected,scope=scope)
        self.assertEqual(observed,[self.f.library.tracks_by_id['c'].path.resolve()])
        self.assertEqual(len(plan.copies),1);self.assertEqual(plan.reused_track_count,2)
        self.assertEqual(plan.copies[0].source,self.f.library.tracks_by_id['c'].path.resolve())
        with mock.patch.object(stock_ipod,'commit_stock_ipod_sync',wraps=stock_ipod.commit_stock_ipod_sync) as commit:
            result=stock_ipod.commit_stock_ipod_sync(plan)
        commit.assert_called_once_with(plan)
        after=read_stock_ipod_snapshot(self.f.ipod,volume_uuid='generated-volume')
        self.assertEqual(next(row for row in after.playlists if row.title==self.b.name),before_b)
        self.assertEqual(next(row for row in after.playlists if row.title=='Keep Me').playlist_id,202)
        self.assertEqual(len(after.tracks),3)
        exported_a=next(row for row in after.playlists if row.title==self.a.name)
        exported_c=next(row for row in after.playlists if row.title==self.c.name)
        self.assertEqual(len(exported_a.entries),2)
        self.assertEqual(exported_a.entries[-1],exported_c.entries[0])
        status=stock_ipod.inspect_stock_ipod_sync_status(self.f.library,self.f.ipod,identity_key=self.case.key,
            model_number='MA446',serial_number='',shadow_store=self.case.shadow,
            catalog_witness=result.catalog_witness,display_rows=self.rows(),art_cache=self.f.art_cache)
        self.assertEqual((status.state,status.local_changes),('up_to_date',0))

    def test_existing_export_stays_on_catalog_sync_with_its_other_linked_lists(self):
        self.f.library.tracks_by_id['old'].name='Current displayed title'
        self.host.metadata_controller=SimpleNamespace(pending=[{'error':'Unrelated native failure'}],background_paused=True)
        with mock.patch('desktop.stock_catalog_sync.request_sync') as catalog:
            MainWindow._begin_stock_ipod_sync(self.host,self.dev,[self.a])
        catalog.assert_called_once()
        self.assertEqual([row.pid for row in catalog.call_args.kwargs['source_playlists']],[self.a.pid,self.b.pid])
        self.host._request_ipod_export_capture.assert_not_called();self.assertEqual(self.workers,[])

    def test_selected_catalog_fallback_reconciles_only_its_before_state(self):
        self.a.keys.clear()
        guard,_observed=self.unchanged_audio_guard()
        with guard:plan=self.plan_through_desktop([self.a],reconcile=True)
        result=stock_ipod.commit_stock_ipod_sync(plan)
        after=read_stock_ipod_snapshot(self.f.ipod,volume_uuid='generated-volume')
        self.assertEqual(next(row for row in after.playlists if row.title==self.b.name),
                         next(row for row in self.snapshot.playlists if row.title==self.b.name))
        self.assertEqual(next(row for row in after.playlists if row.title==self.a.name).entries,())
        self.assertIn(self.b.pid,self.case.shadow.load(self.case.key).playlists)
        self.assertFalse(result.catalog_warning)

    def test_removed_source_is_batched_without_dropping_clean_or_foreign_playlists(self):
        self.f.library.playlists.remove(self.b)
        next(row for row in self.f.library.source_playlist_catalog['nodes'] if row['ID']==self.b.pid)['rb_local_deleted']=1
        selected,scope=stock_export_selection(self.host,self.dev,[self.c])
        self.assertEqual(scope.removed_keys,(self.b.pid,));self.assertEqual([row.pid for row in selected],[self.a.pid,self.c.pid])
        before=self.f.new_source.read_bytes()
        guard,_observed=self.unchanged_audio_guard()
        with guard:plan=self.plan_through_desktop(selected,reconcile=True,scope=scope)
        self.assertEqual(len(plan.copies),1);self.assertEqual(len(plan.media_deletes),1)
        result=stock_ipod.commit_stock_ipod_sync(plan)
        after=read_stock_ipod_snapshot(self.f.ipod,volume_uuid='generated-volume')
        self.assertEqual({row.title for row in after.playlists if row.kind=='Playlist'},
                         {'Keep Me',self.a.name,self.c.name})
        self.assertEqual(self.f.new_source.read_bytes(),before)
        self.assertNotIn(self.b.pid,self.case.shadow.load(self.case.key).playlists)
        self.assertFalse(result.catalog_notice)

    def test_stale_source_uuid_or_reused_missing_key_cannot_be_auto_removed(self):
        node=next(row for row in self.f.library.source_playlist_catalog['nodes'] if row['ID']==self.b.pid)
        node['UUID']=str(uuid4())
        with self.assertRaisesRegex(ValueError,'identity changed'):stock_export_selection(self.host,self.dev,[self.c])
        self.f.library.playlists.remove(self.b)
        with self.assertRaisesRegex(ValueError,'replaced or is unavailable'):stock_export_selection(self.host,self.dev,[self.c])

    def test_removed_managed_name_cannot_authorize_deleting_a_foreign_namesake(self):
        self.f.library.playlists.remove(self.b)
        next(row for row in self.f.library.source_playlist_catalog['nodes'] if row['ID']==self.b.pid)['rb_local_deleted']=1
        managed=next(row for row in self.snapshot.playlists if row.title==self.b.name)
        duplicate=replace(managed,playlist_id=9999)
        self.host._stock_view_cache[self.dev]=(replace(self.snapshot,
            playlists=(*self.snapshot.playlists,duplicate)),None)
        with self.assertRaisesRegex(ValueError,'shares its name'):
            stock_export_selection(self.host,self.dev,[self.c])
        self.assertEqual(self.workers,[])
        self.host._request_ipod_export_capture.assert_not_called()

    def test_stock_then_usb_batch_preserves_original_choices_for_usb(self):
        from desktop.export_dialog import ExportSelectionMixin
        from tests.test_export_dialog import Host
        batch_root=self.f.base/'batch';batch_root.mkdir()
        host=Host(batch_root)
        self.addCleanup(host.deleteLater)
        host.lib=self.f.library
        host._custom_playlists=lambda:[]
        host._row_for=self.host._row_for
        stock=dict(path=self.dev,name='Stock',token=self.f.ipod.stat().st_dev,
            uuid=self.facts.volume_uuid,kind='stock_ipod',role='')
        usb={**stock,'name':'USB','kind':'ipod_usb','role':'ipod_usb'}
        host._device_profiles[self.dev]=self.host._device_profiles[self.dev]
        host._device_mount_tokens[self.dev]=stock['token']
        for field in ('_stock_view_cache','_stock_catalog_witness_cache','_drift_cache',
                      'custom','settings','titles','overrides'):
            setattr(host,field,getattr(self.host,field))
        host._export_batch.update(binding=(str(host.lib.source),str(host.lib.source_library_id)),
            keys=[self.c.pid],pending=[stock,usb])
        def stock_started(dev,playlists,**options):
            host.calls.append((dev,playlists,options));host._exporting=True
        host._begin_stock_ipod_sync=stock_started
        host._validate_export_target=mock.Mock()
        with mock.patch('desktop.export_dialog.device_facts',return_value=self.facts), \
                mock.patch('desktop.cdj._source_edits_pending',return_value=False), \
                mock.patch('desktop.cdj._source_edits_error',return_value=''):
            ExportSelectionMixin._start_next_export_target(host)
            self.assertTrue(host.calls,host._export_batch or host._last_export_batch_results)
            self.assertEqual([row.pid for row in host.calls[0][1]],[self.a.pid,self.b.pid,self.c.pid])
            self.assertEqual(host._export_batch['keys'],[self.c.pid])
            host._exporting=False;host._maybe_finish_export_target()
            for _ in range(4):self.app.processEvents()
            self.assertEqual([row.pid for row in host.calls[1][1]],[self.c.pid])
            self.assertEqual(host.calls[1][2]['target_role'],'ipod_usb')
            self.assertEqual(host.calls[1][2]['drop_target'],(stock['token'],stock['uuid']))
            self.assertEqual(host._export_batch['keys'],[self.c.pid])
            host._exporting=False;host._maybe_finish_export_target()
            for _ in range(4):self.app.processEvents()
            self.assertIsNone(host._export_batch)

    def test_missing_or_stale_pending_witness_refuses_without_starting_capture(self):
        for witness in (None,replace(self.first.catalog_witness,database_sha256='a'*64)):
            with self.subTest(witness=witness):
                self.host._stock_catalog_witness_cache[self.dev]=witness
                MainWindow._begin_stock_ipod_sync(self.host,self.dev,[self.c])
                self.host._request_ipod_export_capture.assert_not_called()
        self.assertEqual(self.host._export_target_result.call_count,2)
        self.assertEqual(self.workers,[])

    def test_scope_rejects_late_cache_changes_and_reappearing_removed_identity(self):
        self.f.library.playlists.remove(self.b)
        node=next(row for row in self.f.library.source_playlist_catalog['nodes'] if row['ID']==self.b.pid);node['rb_local_deleted']=1
        _selected,scope=stock_export_selection(self.host,self.dev,[self.c])
        self.host._stock_view_cache[self.dev]=(replace(self.snapshot,artwork_database_sha256='a'*64),None)
        with self.assertRaisesRegex(ValueError,'playlist links changed'):scope.require_current(self.host,self.dev)
        self.host._stock_view_cache[self.dev]=(self.snapshot,None);node['rb_local_deleted']=0
        with self.assertRaisesRegex(ValueError,'was replaced'):scope.require_current(self.host,self.dev)

    def test_usb_dual_and_rockbox_never_gain_stock_managed_selections(self):
        for kind in ('generic_usb','ipod_usb','dual_boot','rockbox'):
            with self.subTest(kind=kind):
                self.host._device_profiles[self.dev].kind=kind
                selected,scope=stock_export_selection(self.host,self.dev,[self.c])
                self.assertEqual(selected,(self.c,));self.assertIsNone(scope)
