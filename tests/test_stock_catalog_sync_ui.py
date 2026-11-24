"""Displayed-metadata catalog Sync over isolated MainWindow/device workers."""
from dataclasses import replace
from io import BytesIO
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication
from PIL import Image

from desktop.stock_catalog_sync import default_sync_selection, request_device_sync, request_sync, source_sync_selection, _failed
from desktop.workers import OneShotTask
from soundbox import alias_intents, engine, source_truth, playlist_unification
from soundbox.library import Playlist
from soundbox.stock_ipod_catalog import CATALOG_METADATA_FIELDS
from soundbox.stock_ipod_catalog_state import StockCatalogWitness
from soundbox.stock_ipod_playlist_binding import PlaylistBinding
from soundbox.stock_ipod_info import StockIpodPlaylistEntry
from soundbox.operation_control import CommittedOperationError
from soundbox.artwork import ArtResult
from soundbox.stock_artwork import StockArtworkCache
from soundbox.stock_ipod import plan_stock_ipod_catalog_update as real_catalog_plan
from soundbox.stock_ipod_catalog import catalog_source_playlist
from tests import test_ipod_artwork_ui as fixture
from tests import test_stock_ipod_catalog as catalog_fixture


class StockCatalogSyncUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        fixture.IpodArtworkAdmission.setUpClass()

    def setUp(self):
        self.case=fixture.IpodArtworkAdmission();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.window=self.case.window
        self.export_chooser=self.enterContext(mock.patch.object(self.window,'_show_export_dialog'))

    def witness(self):
        w=self.window
        return StockCatalogWitness((str(w.lib.source),w.lib.source_kind,w.lib.source_library_id),
            self.case.snapshot.identity_key,'a'*64,(),bindings=(PlaylistBinding(
                'native-favorites','native-favorites','playlist-uuid',fixture.PLAYLIST_ID,'Favorites',(('1',1),('2',2))),))

    def cover(self, color):
        out=BytesIO();Image.new('RGB',(12,12),color).save(out,'JPEG')
        return ArtResult(out.getvalue(),'image/jpeg',1.0,'https://soundcloud.com/artist/song','Same title')

    def freeze_display(self):
        w=self.window
        descriptor,targets=source_truth.build(source_truth.capture(w.lib,
            titles={'1':'Frozen displayed song'},artists={'1':'Frozen displayed artist'},aliases={},settings={}),include_equal=True)
        w.settings[source_truth.MIGRATION]=descriptor
        w.settings[source_truth.TARGETS]=targets
        w._invalidate_rows(['1'])
        return descriptor,targets

    def test_actual_row_for_frozen_pair_and_supported_fields_are_detached_for_worker(self):
        w=self.window
        self.freeze_display()
        w.settings['track_info']={'1':dict(album='Current album',genre='Current genre',comments='Current comment',year='2026',bpm='147.5')}
        expected=w._row_for(w.lib.tracks_by_id['1'])
        generic=engine.track_row(w.lib.tracks_by_id['1'],w.overrides,w.titles,registry=w.lib.artist_registry,junk=w._title_junk())
        self.assertNotEqual((generic['title'],generic['artist']),(expected['title'],expected['artist']))
        scope=source_truth.scope(w.lib);track=w.lib.tracks_by_id['1']
        origin=dict(scope=scope,local_id='1',native_id=track.native_id,uuid=track.source_uuid,
                    path=str(track.path),automatic='Artist',native_artist='Raw source artist',
                    automatic_title='Song 1',native_title='Raw source title')
        w.settings[alias_intents.ORIGINS]={alias_intents.key(scope,origin):origin}
        original_start=OneShotTask.start;held=[]
        with mock.patch.object(OneShotTask,'start',lambda worker:held.append(worker)):
            self.case.request()
        origin['native_title']='Later raw source title'
        original_start(held[0]);self.case.drain()
        options=self.case.plan_stock.call_args.kwargs
        row=options['display_rows']['1']
        self.assertEqual(set(row),{'id','bpm_estimated',*CATALOG_METADATA_FIELDS})
        for field in CATALOG_METADATA_FIELDS:self.assertEqual(row[field],expected.get(field,''))
        self.assertEqual((row['title'],row['artist']),('Frozen displayed song','Frozen displayed artist'))
        self.assertEqual(options['match_labels']['1'],[['Raw source title','Raw source artist']])
        self.assertTrue(options['update_metadata']);self.assertTrue(options['incremental'])
        self.case.commit_stock.assert_called_once()

    def test_native_ack_with_same_displayed_values_keeps_plan_and_newer_navigation(self):
        w=self.window
        self.freeze_display()
        self.case.request();worker=w._stock_plan_worker
        self.assertTrue(worker.wait(5000))
        w.lib=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '1':replace(w.lib.tracks_by_id['1'],name='New native title',artist='New native artist')})
        w._invalidate_rows(['1'])
        w._open_settings();before=self.case.seat()
        self.case.drain()
        self.case.commit_stock.assert_called_once()
        self.assertEqual(self.case.seat(),before)
        self.assertEqual(self.case.refreshes,[self.case.device])

    def test_count_212_to_214_captures_current_selection_and_all_source_path_boundary(self):
        w=self.window;template=w.lib.tracks_by_id['1']
        w.lib.tracks_by_id={str(i):replace(template,track_id=str(i),name=f'Song {i}',artist='Artist',
            location_uri=f'file:///synthetic/{i}.wav',source_native_id=str(i),source_uuid=f'uuid-{i}') for i in range(1,215)}
        overlap=self.case.target/'iPod_Control/iTunes/iTunesDB'
        w.lib.tracks_by_id['unselected']=replace(template,track_id='unselected',location_uri=overlap.as_uri())
        w.lib.playlists[0].keys=[str(i) for i in range(1,215)]
        device_template=self.case.snapshot.tracks[0]
        tracks=tuple(replace(device_template,dbid=i,track_id=i,title=f'Song {i}',
            location=f':iPod_Control:Music:F00:{i}.wav') for i in range(1,213))
        entries=tuple(StockIpodPlaylistEntry(row.dbid,row.title,row.artist) for row in tracks)
        self.case.snapshot=replace(self.case.snapshot,tracks=tracks,
            playlists=(replace(self.case.snapshot.playlists[0],entries=entries),))
        w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
        w._row_cache.clear()
        request_sync(w,(str(fixture.PLAYLIST_ID),'Favorites',212))
        self.case.drain()
        library,selected,_target=self.case.plan_stock.call_args.args
        self.assertEqual(len(selected[0].keys),214)
        self.assertEqual(len(library.tracks_by_id),214)
        self.assertNotIn('unselected',library.tracks_by_id)
        self.assertIn(overlap,library.export_source_paths)
        self.assertEqual(len(self.case.plan_stock.call_args.kwargs['display_rows']),214)
        self.case.commit_stock.assert_called_once()

    def test_default_selection_retains_exact_cached_playlist_identity_after_source_rename(self):
        w=self.window
        w._stock_catalog_witness_cache[self.case.device]=self.witness()
        w.lib.playlists[0].name='Renamed source set'
        selected=default_sync_selection(w,self.case.device)
        self.assertEqual(selected,((str(fixture.PLAYLIST_ID),'Favorites',2),))
        request_sync(w,selected)
        self.case.drain()
        self.assertEqual(self.case.plan_stock.call_args.args[1][0].name,'Renamed source set')
        self.case.commit_stock.assert_called_once()

    def test_known_omissions_explicit_sync_rechecks_full_firmware_eligibility(self):
        w = self.window
        source = catalog_source_playlist(w.lib, w.lib.playlists[0])
        baseline = self.witness()
        binding = replace(baseline.bindings[0], members=(('1', 1),),
            source_label=source.label, source_members=source.identities)
        self.assertTrue(binding.has_omissions)
        w._stock_catalog_witness_cache[self.case.device] = replace(baseline, bindings=(binding,))
        before = self.case.snapshot
        current = replace(before, tracks=before.tracks[:1],
            playlists=(replace(before.playlists[0], entries=before.playlists[0].entries[:1]),))
        w._stock_view_cache[self.case.device] = (current, None)
        w._open_settings(); seat = self.case.seat()
        with mock.patch.object(w, '_request_ipod_export_capture') as capture:
            request_sync(w, (str(fixture.PLAYLIST_ID), 'Favorites', 1), device=self.case.device)
        capture.assert_called_once_with('stock', self.case.device, [w.lib.playlists[0]], reconcile=True)
        self.case.plan_stock.assert_not_called()
        self.case.commit_stock.assert_not_called()
        self.case.question.assert_not_called()
        self.assertEqual(self.case.seat(), seat)

    def test_ambiguous_source_rows_do_not_get_an_ordinal_device_target(self):
        w=self.window
        w.lib.playlists.append(Playlist('Favorites',[],['1','2'],'other-native'))
        w.lib.source_playlist_catalog['nodes'].append(dict(ID='other-native',UUID='other-uuid',rb_local_deleted=0))
        self.assertEqual(default_sync_selection(w,self.case.device),())
        self.case.request();self.case.plan_stock.assert_not_called()
        self.case.commit_stock.assert_not_called()

    def test_preconfirmed_explicit_selection_captures_from_settings_without_new_confirmation(self):
        w=self.window;source=w.lib.playlists[0]
        w._open_settings();before=self.case.seat()
        request_sync(w,None,device=self.case.device,confirmed=True,source_playlists=[source])
        self.case.drain()
        self.case.question.assert_not_called()
        self.case.commit_stock.assert_called_once()
        self.assertEqual(self.case.seat(),before)

    def test_witness_is_saved_on_worker_only_after_commit_and_cache_error_is_advisory(self):
        w=self.window;main=threading.get_ident();events=[]
        self.case.stock_plan.catalog_witness=self.witness()
        def committed(_plan):events.append(('commit',threading.get_ident()));return self.case.stock_result
        def cache(_witness):
            events.append(('cache',threading.get_ident()))
            raise RuntimeError('fixture cache unavailable')
        self.case.commit_stock.side_effect=committed
        w.stock_catalog_witness_store.save.side_effect=cache
        with mock.patch.object(w,'_save_state',side_effect=AssertionError('catalog Sync saved canonical state')):
            self.case.request();plan=w._stock_plan_worker;self.assertTrue(plan.wait(5000))
            self.assertEqual(events,[])
            self.case.drain()
        self.assertEqual([kind for kind,_tid in events],['commit','cache'])
        self.assertTrue(all(tid!=main for _kind,tid in events))
        self.assertTrue(w._device_coordinator.last_result.committed)
        self.assertIn('optional comparison cache could not be saved',self.case.warning.call_args.args[2])
        self.case.warning.assert_called_once()
        self.case.information.assert_not_called()
        self.assertEqual(w._stock_catalog_witness_cache,{})

    def test_noop_verifies_fresh_baseline_before_worker_witness_save(self):
        w=self.window;events=[]
        self.case.stock_plan.requires_commit=False
        self.case.stock_plan.catalog_witness=self.witness()
        w.stock_catalog_witness_store.save.side_effect=lambda _witness:events.append('cache')
        with mock.patch('soundbox.stock_ipod.verify_stock_ipod_catalog_noop',side_effect=lambda _plan:events.append('verify')):
            self.case.request();self.case.drain()
        self.assertEqual(events,['verify','cache'])
        self.case.commit_stock.assert_not_called()
        self.assertIs(w._stock_catalog_witness_cache[self.case.device],self.case.stock_plan.catalog_witness)
        self.assertFalse(w._device_coordinator.last_result.committed)

    def test_unknown_model_uses_observed_format_and_known_model_cannot_bypass_missing_format(self):
        w=self.window
        self.case.snapshot=replace(self.case.snapshot,model='')
        w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
        request_sync(w,(str(fixture.PLAYLIST_ID),'Favorites',2))
        self.case.drain()
        self.assertEqual(self.case.plan_stock.call_args.kwargs['model_number'],'')
        self.case.plan_stock.reset_mock();self.case.commit_stock.reset_mock()
        self.case.snapshot=replace(self.case.snapshot,model='MA446',stock_format=None)
        w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
        request_sync(w,(str(fixture.PLAYLIST_ID),'Favorites',2))
        self.case.plan_stock.assert_not_called();self.case.commit_stock.assert_not_called()

    def test_normalized_header_association_is_unique_and_export_selection_requires_every_source(self):
        w=self.window;source=w.lib.playlists[0]
        source.name='  FAVORITES  '
        expected=((str(fixture.PLAYLIST_ID),'Favorites',2),)
        self.assertEqual(default_sync_selection(w,self.case.device),expected)
        self.assertEqual(source_sync_selection(w,self.case.device,[source]),expected)
        self.assertEqual(source_sync_selection(w,self.case.device,[source,Playlist('Missing',[],['1'],'missing')]),())
        request_device_sync(w,self.case.device);self.case.drain()
        self.case.commit_stock.assert_called_once()
        self.case.plan_stock.reset_mock();self.case.commit_stock.reset_mock()
        w.lib.playlists.append(Playlist('Favorites',[],['1'],'collision'))
        w.lib.source_playlist_catalog['nodes'].append(dict(ID='collision',UUID='collision-uuid',rb_local_deleted=0))
        self.assertEqual(default_sync_selection(w,self.case.device),())
        request_device_sync(w,self.case.device)
        self.case.plan_stock.assert_not_called()
        self.export_chooser.assert_called_once_with(preferred=self.case.device)

    def test_real_disconnection_before_finished_delivery_releases_only_original_owner(self):
        w=self.window
        self.case.request();worker=w._stock_plan_worker;self.assertTrue(worker.wait(5000))
        w._open_settings();before=self.case.seat()
        w._device_coordinator.disconnect(self.case.device)
        self.case.drain()
        self.case.commit_stock.assert_not_called()
        self.assertFalse(w._exporting);self.assertFalse(w._cancelling)
        self.assertIsNone(w._device_coordinator.active)
        self.assertEqual(self.case.seat(),before)

    def test_source_physical_identity_restore_mount_and_format_changes_after_plan_refuse_commit(self):
        w=self.window
        for change in ('source','mount','format','playlist UUID','track UUID','native ID',
                       'path','native path','size','duration','source inventory','repository'):
            with self.subTest(change=change):
                old_library=w.lib;old_snapshot=self.case.snapshot
                old_repository=w._state_repository
                old_keys=list(w.lib.playlists[0].keys);old_token=w._device_mount_tokens[self.case.device]
                self.case.request();worker=w._stock_plan_worker;self.assertTrue(worker.wait(5000))
                if change=='source':w.lib=replace(w.lib,source='/synthetic/replacement.db')
                elif change=='mount':w._device_mount_tokens[self.case.device]+=1
                elif change=='format':w._stock_view_cache[self.case.device]=(replace(old_snapshot,stock_format=None),None)
                elif change=='playlist UUID':
                    w.lib=replace(w.lib,source_playlist_catalog={**w.lib.source_playlist_catalog,
                        'nodes':[{**node,'UUID':'replaced-playlist'} for node in w.lib.source_playlist_catalog['nodes']]})
                elif change=='repository':
                    w._state_repository=SimpleNamespace(generation=0,area=SimpleNamespace(generation=0))
                elif change=='source inventory':
                    w.lib=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
                        'third':replace(w.lib.tracks_by_id['1'],track_id='third',location_uri='file:///synthetic/third.wav')})
                else:
                    fields={'track UUID':{'source_uuid':'replaced-track'},'native ID':{'source_native_id':'replaced-native'},
                        'path':{'location_uri':'file:///synthetic/moved.wav'},'native path':{'source_native_path':'/synthetic/native.wav'},
                        'size':{'size':w.lib.tracks_by_id['1'].size+1},
                        'duration':{'total_time':w.lib.tracks_by_id['1'].total_time+1}}[change]
                    w.lib=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,'1':replace(w.lib.tracks_by_id['1'],**fields)})
                self.case.drain();self.case.commit_stock.assert_not_called()
                w.lib=old_library;w.lib.playlists[0].keys=old_keys
                w._state_repository=old_repository;w._device_mount_tokens[self.case.device]=old_token
                w._stock_view_cache[self.case.device]=(old_snapshot,None)
                self.case.plan_stock.reset_mock();self.case.commit_stock.reset_mock()

    def test_later_metadata_rename_and_membership_keep_captured_catalog_inputs(self):
        w = self.window
        original = list(w.lib.playlists[0].keys)
        previous_name = w.lib.playlists[0].name
        previous_display = dict(w._row_for(w.lib.tracks_by_id['1']))
        self.case.request()
        worker = w._stock_plan_worker; self.assertTrue(worker.wait(5000))
        captured = self.case.plan_stock.call_args.args[1][0]
        captured_rows = self.case.plan_stock.call_args.kwargs['display_rows']
        w.lib.playlists[0].keys.reverse()
        w.lib.playlists[0].name = 'Renamed during Sync'
        w.lib.playlists[0].folder_path = ['New parent']
        w.settings['track_info'] = {'1':{'album':'Newer visible album','genre':'Later genre'}}
        w.titles['1'] = 'Later title'; w.overrides['1'] = 'Later artist'
        w._invalidate_rows(['1'])
        self.case.drain()
        self.case.commit_stock.assert_called_once_with(self.case.stock_plan)
        self.assertEqual(captured.keys, original)
        self.assertEqual(captured.name, previous_name)
        for field in CATALOG_METADATA_FIELDS:
            self.assertEqual(captured_rows['1'][field], previous_display.get(field, ''))
        self.assertEqual(w.lib.playlists[0].keys, list(reversed(original)))
        self.assertEqual(w.lib.playlists[0].name, 'Renamed during Sync')
        self.assertEqual(w._row_for(w.lib.tracks_by_id['1'])['title'], 'Later title')

    def test_failed_fresh_noop_verification_never_saves_comparison_cache(self):
        w=self.window
        self.case.stock_plan.requires_commit=False;self.case.stock_plan.catalog_witness=self.witness()
        with mock.patch('soundbox.stock_ipod.verify_stock_ipod_catalog_noop',side_effect=RuntimeError('catalog drift')):
            self.case.request();self.case.drain()
        w.stock_catalog_witness_store.save.assert_not_called()
        self.case.commit_stock.assert_not_called()
        self.case.information.assert_not_called()
        self.assertIn('catalog drift',str(self.case.warning.call_args))

    def test_observed_guid_is_detached_for_stock_and_dual_and_replacement_refuses_commit(self):
        w=self.window
        for kind in ('stock_ipod','dual_boot'):
            with self.subTest(kind=kind):
                self.case.configure(kind)
                self.case.snapshot=replace(self.case.snapshot,firewire_guid='000A270000000001',usb_serial='000A270000000001')
                w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
                request_sync(w,(str(fixture.PLAYLIST_ID),'Favorites',2))
                worker=w._stock_plan_worker;self.assertTrue(worker.wait(5000))
                plan=self.case.plan_dual if kind=='dual_boot' else self.case.plan_stock
                self.assertEqual(plan.call_args.kwargs['firewire_guid'],'000A270000000001')
                w._stock_view_cache[self.case.device]=(replace(self.case.snapshot,usb_serial='000A270000000002'),None)
                self.case.drain()
                self.case.commit_stock.assert_not_called();self.case.commit_dual.assert_not_called()
                self.case.plan_stock.reset_mock();self.case.plan_dual.reset_mock()

    def virtual_sync(self, view, title, *, prepared):
        w=self.window
        selected=w._view_playlist(view)
        self.assertIsNotNone(selected)
        tracks=tuple(replace(self.case.snapshot.tracks[0],dbid=int(key),track_id=int(key),
            title=w.lib.tracks_by_id[key].name,artist=w.lib.tracks_by_id[key].artist,
            location=f':iPod_Control:Music:F00:{key}.wav') for key in selected.keys)
        entries=tuple(StockIpodPlaylistEntry(row.dbid,row.title,row.artist) for row in tracks)
        self.case.snapshot=replace(self.case.snapshot,tracks=tracks,playlists=(replace(
            self.case.snapshot.playlists[0],title=title,entries=entries),))
        w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
        recognition=self.case.stock_plan.catalog_recognition[0]
        self.case.stock_plan.catalog_recognition=(replace(recognition,playlist_key='cust:'+title,
            name=title,count=len(selected.keys),tracks=tuple(replace(recognition.tracks[0],
                local_id=key,dbid=int(key),device_track_id=int(key),location=f':iPod_Control:Music:F00:{key}.wav')
                for key in selected.keys)),)
        w._sync_playlists_cache=[selected] if prepared else None
        original=w._view_playlist
        calls=[]
        def one_view(name):
            calls.append(name)
            if name!=view:raise AssertionError('Unrelated virtual view was materialized')
            return original(name)
        view_callback=(mock.Mock(side_effect=AssertionError('Prepared virtual view was rebuilt'))
                       if prepared else one_view)
        with mock.patch.object(w,'_sync_playlists',side_effect=AssertionError('All virtual views were enumerated')), \
                mock.patch.object(w,'_view_playlist',side_effect=view_callback):
            expected=((str(fixture.PLAYLIST_ID),title,len(selected.keys)),)
            self.assertEqual(default_sync_selection(w,self.case.device),expected)
            self.assertEqual(source_sync_selection(w,self.case.device,[selected]),expected)
            request_sync(w,expected)
            self.case.drain()
        captured=self.case.plan_stock.call_args.args[1]
        self.assertEqual([(row.name,row.pid,row.keys) for row in captured],[(title,'',selected.keys)])
        self.case.commit_stock.assert_called_once()
        self.assertTrue(prepared or calls)

    def test_manually_exported_collection_sync_uses_only_its_current_virtual_view(self):
        self.virtual_sync('collection','Collection',prepared=False)

    def test_manually_exported_artists_sync_reuses_its_prepared_virtual_view(self):
        self.virtual_sync('artists','Artists',prepared=True)

    def test_completed_native_custom_adoption_has_one_source_key_without_materializing_views(self):
        w=self.window;source=w.lib.playlists[0]
        source.pid='42'
        native_uuid='00000000-0000-4000-8000-000000000042'
        w.lib.source_playlist_catalog['nodes']=[dict(ID='42',UUID=native_uuid,rb_local_deleted=0)]
        state=playlist_unification.new(w.lib.source,w.lib.source_library_id)
        state.update(customs_done=True,nodes={'custom:Favorites':dict(id='42',uuid=native_uuid)})
        w.settings[playlist_unification.KEY]=state
        w.custom={'Favorites':['1','2']}
        w._apply_settings_to_lib(invalidate_rows=False,configure_artwork=False)
        self.assertEqual(source.export_key,'cust:Favorites')
        self.assertEqual(w._custom_playlists()[0].export_key,'cust:Favorites')
        self.case.stock_plan.catalog_recognition=(replace(self.case.stock_plan.catalog_recognition[0],
            playlist_key='cust:Favorites'),)
        w._stock_catalog_witness_cache[self.case.device]=StockCatalogWitness(
            (str(w.lib.source),w.lib.source_kind,w.lib.source_library_id),self.case.snapshot.identity_key,'a'*64,(),
            bindings=(PlaylistBinding('cust:Favorites','42',native_uuid,fixture.PLAYLIST_ID,'Favorites',(('1',1),('2',2))),))
        with mock.patch.object(w,'_sync_playlists',side_effect=AssertionError('All views were enumerated')), \
                mock.patch.object(w,'_view_playlist',side_effect=AssertionError('Unrelated view was prepared')):
            expected=((str(fixture.PLAYLIST_ID),'Favorites',2),)
            self.assertEqual(default_sync_selection(w,self.case.device),expected)
            self.assertEqual(source_sync_selection(w,self.case.device,[source]),expected)
            self.case.request();self.case.drain()
        captured=self.case.plan_stock.call_args.args[1]
        self.assertEqual(len(captured),1)
        self.assertEqual((captured[0].pid,captured[0].export_key),('42','cust:Favorites'))
        self.case.commit_stock.assert_called_once()

    def export_batch(self):
        w=self.window
        target=dict(path=self.case.device,name='Fixture iPod',kind='stock_ipod',
                    token=w._device_mount_tokens[self.case.device],uuid='fixture-uuid')
        batch=dict(binding=(str(w.lib.source),str(w.lib.source_library_id)),keys=['native-favorites'],
            pending=[],active=target,results=[],cancelled=False,error='',started=True)
        w._export_batch=batch
        return batch

    def test_actual_export_batch_plan_and_commit_failure_never_report_export_complete(self):
        for phase in ('plan','commit'):
            with self.subTest(phase=phase):
                batch=self.export_batch()
                operation=self.case.plan_stock if phase=='plan' else self.case.commit_stock
                self.case.warning.reset_mock();self.case.information.reset_mock()
                before=operation.side_effect
                operation.side_effect=RuntimeError(phase+' fixture failure')
                try:self.case.request();self.case.drain()
                finally:operation.side_effect=before
                self.assertEqual(len(batch['results']),1)
                self.assertIn(phase+' fixture failure',batch['results'][0][1])
                self.assertNotIn('Export complete',batch['results'][0][1])
                self.case.warning.assert_called_once();self.case.information.assert_not_called()
                self.assertIn(phase+' fixture failure',self.case.warning.call_args.args[2])

    def test_export_batch_rejected_current_view_and_late_cancel_keep_their_failures(self):
        for reason in ('physical identity','cancel'):
            with self.subTest(reason=reason):
                previous=self.window.lib
                batch=self.export_batch()
                self.case.request();worker=self.window._stock_plan_worker;self.assertTrue(worker.wait(5000))
                if reason=='physical identity':
                    self.window.lib=replace(previous,tracks_by_id={**previous.tracks_by_id,
                        '1':replace(previous.tracks_by_id['1'],location_uri='file:///synthetic/replaced.wav')})
                else:self.window._cancel_export()
                self.case.drain()
                self.assertNotIn('Export complete',batch['results'][0][1])
                self.assertIn('changed' if reason=='physical identity' else 'cancelled',batch['results'][0][1])
                self.case.commit_stock.assert_not_called()
                self.window.lib=previous

    def test_export_batch_retains_committed_failure_and_partial_success_details(self):
        batch=self.export_batch()
        self.case.commit_stock.side_effect=CommittedOperationError('fixture cleanup incomplete')
        self.case.request();self.case.drain()
        self.assertIn('Device changes were committed',batch['results'][0][1])
        self.assertTrue(self.window._device_coordinator.last_result.committed)
        self.case.commit_stock.side_effect=None
        batch=self.export_batch()
        row=self.case.stock_plan.catalog_recognition[0]
        self.case.stock_plan.catalog_recognition=(replace(row,tracks=row.tracks[:1],unmatched_track_ids=('2',)),)
        self.case.stock_plan.catalog_witness=self.witness()
        self.case.stock_plan.catalog_artwork_track_count=1
        self.case.stock_result.metadata_updates=1;self.case.stock_result.reused_tracks=1
        self.window.stock_catalog_witness_store.save.side_effect=OSError('fixture optional cache full')
        self.case.request();self.case.drain()
        note=batch['results'][0][1]
        details=self.window._last_device_completion['text']
        self.assertEqual(self.window._last_device_completion['title'],'iPod playlists synced')
        self.assertIn('matched 1 of 2 songs',details)
        self.assertIn('1 unmatched or unsupported',details)
        self.assertIn('optional comparison cache could not be saved',note)
        self.assertNotIn('matched 1 of 2 songs',note)
        self.assertNotIn('unmatched or unsupported',note)
        self.assertNotIn('Export complete',note)

    def test_old_request_failure_cannot_overwrite_a_newer_batch_or_its_progress(self):
        first=self.export_batch()
        with mock.patch('desktop.stock_catalog_sync._begin') as begin:
            request_sync(self.window,(str(fixture.PLAYLIST_ID),'Favorites',2))
        request=begin.call_args.args[1]
        newer=self.export_batch();newer['error']='Newer target owns this note'
        self.window._exporting=True
        _failed(self.window,request,RuntimeError('Obsolete failure'))
        self.assertEqual(newer['error'],'Newer target owns this note')
        self.assertEqual(first['error'],'')
        self.assertTrue(self.window._exporting)
        self.case.warning.assert_not_called()
        self.window._exporting=False

    def test_export_batch_disconnection_with_suppressed_callback_retains_failure(self):
        batch=self.export_batch();w=self.window
        self.case.request();worker=w._stock_plan_worker;self.assertTrue(worker.wait(5000))
        w._device_coordinator.disconnect(self.case.device)
        self.case.drain()
        self.assertEqual(len(batch['results']),1)
        self.assertIn('disconnected',batch['results'][0][1])
        self.assertNotIn('Export complete',batch['results'][0][1])
        self.case.commit_stock.assert_not_called()

    def test_selected_same_metadata_cover_change_before_or_after_plan_refuses_stock_and_dual(self):
        w=self.window;start=OneShotTask.start
        for kind in ('stock_ipod','dual_boot'):
            for phase in ('before','after'):
                with self.subTest(kind=kind,phase=phase):
                    self.case.configure(kind);w.art_cache.store_ok('1',self.cover('red'))
                    before=dict(w.art_cache.index['1'])
                    if phase=='before':
                        held=[]
                        with mock.patch.object(OneShotTask,'start',lambda worker:held.append(worker)):
                            self.case.request()
                    else:
                        self.case.request();self.assertTrue(w._stock_plan_worker.wait(5000))
                    w.art_cache.store_ok('1',self.cover('blue'))
                    self.assertEqual(w.art_cache.index['1'],before)
                    if phase=='before':start(held[0])
                    self.case.drain()
                    self.case.commit_stock.assert_not_called();self.case.commit_dual.assert_not_called()
                    if phase=='before':
                        self.case.plan_stock.assert_not_called();self.case.plan_dual.assert_not_called()
                    w.stock_catalog_witness_store.save.assert_not_called()
                    for operation in (self.case.plan_stock,self.case.plan_dual):operation.reset_mock()

    def test_cover_change_during_plan_never_reaches_noop_witness_or_commit(self):
        w=self.window;w.art_cache.store_ok('1',self.cover('red'))
        self.case.stock_plan.requires_commit=False;self.case.stock_plan.catalog_witness=self.witness()
        def plan(*_args,**options):
            options['art_cache'].store_ok('1',self.cover('blue'))
            return self.case.stock_plan
        self.case.plan_stock.side_effect=plan
        with mock.patch('soundbox.stock_ipod.verify_stock_ipod_catalog_noop') as verify:
            self.case.request();self.case.drain()
        verify.assert_not_called();w.stock_catalog_witness_store.save.assert_not_called()
        self.case.commit_stock.assert_not_called()
        self.assertIn('selected artwork changed',str(self.case.warning.call_args))

    def test_selected_cover_change_after_commit_worker_allocation_refuses_engine_commit(self):
        w=self.window;w.art_cache.store_ok('1',self.cover('red'));held=[];start=OneShotTask.start
        def dispatch(worker):
            if w._stock_commit_worker is worker:held.append(worker)
            else:start(worker)
        with mock.patch.object(OneShotTask,'start',dispatch):
            self.case.request();self.assertTrue(w._stock_plan_worker.wait(5000))
            self.app.processEvents()
        self.assertEqual(len(held),1)
        w.art_cache.store_ok('1',self.cover('blue'));start(held[0]);self.case.drain()
        self.case.commit_stock.assert_not_called();w.stock_catalog_witness_store.save.assert_not_called()

    def test_default_assignment_or_selected_source_visibility_change_refuses_commit(self):
        w=self.window;paths=[]
        for color in ('red','blue'):
            path=self.case.target.parent/(color+'.jpg');path.write_bytes(self.cover(color).data);paths.append(path)
        w.art_cache.import_default_photos(paths);w.art_cache.configure_default_tracks(['1','2'])
        self.case.request();self.assertTrue(w._stock_plan_worker.wait(5000))
        assigned=w.art_cache._default_assignments['1'];w.art_cache.remove_default_photos([assigned])
        self.case.drain();self.case.commit_stock.assert_not_called()
        w.art_cache.store_ok('1',self.cover('red'))
        self.case.request();self.assertTrue(w._stock_plan_worker.wait(5000))
        w.art_cache.exclude_source('https://soundcloud.com/artist/song')
        self.case.drain();self.case.commit_stock.assert_not_called()

    def test_unrelated_art_and_policy_changes_still_commit_and_gui_never_reads_images(self):
        w=self.window
        neighbor=replace(w.lib.tracks_by_id['1'],track_id='unselected',name='Unselected neighbor',
                         location_uri='file:///synthetic/unselected.wav',
                         source_native_id='9999',source_uuid='unselected-neighbor-uuid')
        w.lib=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,'unselected':neighbor})
        w._bind_library_artwork()
        self.assertNotIn('unselected',w.lib.playlists[0].keys)
        w.art_cache.store_ok('1',self.cover('red'));main=threading.get_ident()
        snapshot=w.art_cache.cover_snapshot;threads=[]
        def read(track):
            threads.append(threading.get_ident())
            if threads[-1]==main:raise AssertionError('GUI read image bytes')
            return snapshot(track)
        def plan(*_args,**options):
            options['art_cache'].cover_snapshot('1');return self.case.stock_plan
        self.case.plan_stock.side_effect=plan
        with mock.patch.object(w.art_cache,'cover_snapshot',side_effect=read), \
                mock.patch.object(w.art_cache,'export_image_snapshot',side_effect=AssertionError('unexpected image read')):
            self.case.request();self.assertTrue(w._stock_plan_worker.wait(5000))
            w.art_cache.store_ok('unselected',self.cover('blue'))
            w.art_cache.exclude_source('https://soundcloud.com/unrelated/song')
            w._open_settings();before=self.case.seat();self.case.drain()
        self.assertEqual(self.case.seat(),before);self.assertTrue(threads)
        self.case.commit_stock.assert_called_once_with(self.case.stock_plan)

    def test_adopted_playlist_identity_survives_real_worker_capture_and_catalog_planner(self):
        generated=catalog_fixture.StockCatalogTests();generated.setUp();self.addCleanup(generated.doCleanups)
        w=self.window;self.case.target=generated.target;self.case.device=str(generated.target)
        self.case.configure('stock_ipod')
        self.case.snapshot=replace(generated.snapshot,usb_family='iPod Video')
        w._stock_view_cache[self.case.device]=(self.case.snapshot,None)
        w._stock_snapshot=self.case.snapshot;w.stock_ipod_pages.set_snapshot(self.case.snapshot)
        w._device_profiles[self.case.device].stock_format=self.case.snapshot.stock_format
        w.__dict__['stock_art_cache']=StockArtworkCache(generated.base/'controller-stock-art')
        w.lib=generated.library;w.lib.source_kind='rekordbox-db';w.lib.source_library_id='native-fixture-library'
        source=w.lib.playlists[0];source.name='FAVORITES';source.pid='42'
        native_uuid='00000000-0000-4000-8000-000000000042'
        w.lib.source_playlist_catalog={'nodes':[
            dict(ID=source.pid,UUID=native_uuid,rb_local_deleted=0,uncaptured=object()),
            dict(ID='unrelated',UUID='unrelated-uuid',rb_local_deleted=0,uncaptured=object())],
            'members':[],'uncaptured':object()}
        w.lib.tracks_by_id={key:replace(track,source_native_id=str(101+index),source_uuid='uuid-'+key)
                            for index,(key,track) in enumerate(w.lib.tracks_by_id.items())}
        state=playlist_unification.new(w.lib.source,w.lib.source_library_id)
        state.update(customs_done=True,nodes={'custom:FAVORITES':dict(id=source.pid,uuid=native_uuid)})
        w.settings[playlist_unification.KEY]=state;w.custom={'FAVORITES':list(source.keys)}
        w.settings.update(hide_untracked=False,hide_short=False)
        w.titles={'alpha':'Saved Alpha'};w.overrides={}
        w._apply_settings_to_lib(invalidate_rows=False,configure_artwork=False)
        w._build_artist_registry();w._invalidate_rows()
        expected=catalog_source_playlist(w.lib,source)
        self.assertEqual(expected.key,'cust:FAVORITES');self.assertEqual(expected.uuid,native_uuid)
        self.assertEqual(default_sync_selection(w,self.case.device),(('901','Favorites',2),))
        database=generated.target/'iPod_Control/iTunes/iTunesDB';before=database.read_bytes()
        plans=[];threads=[]
        def plan(*args,**options):
            threads.append(threading.get_ident())
            result=real_catalog_plan(*args,**options);plans.append(result);return result
        with mock.patch('soundbox.stock_ipod.plan_stock_ipod_catalog_update',side_effect=plan) as actual, \
                mock.patch('soundbox.stock_ipod.acquire_source',side_effect=AssertionError('Read retained source audio')), \
                mock.patch('soundbox.stock_ipod._stock_media_omission',side_effect=AssertionError('Probed retained media')):
            request_sync(w,('901','Favorites',2),device=self.case.device,confirmed=True)
            self.case.drain()
        self.assertTrue(plans,str(self.case.warning.call_args))
        self.assertNotEqual(threads,[threading.get_ident()])
        library,selected,_target=actual.call_args.args
        self.assertEqual(catalog_source_playlist(library,selected[0]),expected)
        self.assertEqual(library.source_playlist_catalog,{'nodes':[
            dict(ID=source.pid,UUID=native_uuid,rb_local_deleted=0)]})
        self.assertEqual(set(library.tracks_by_id),{'alpha','beta'})
        self.assertIn(w.lib.tracks_by_id['other'].path,library.export_source_paths)
        self.assertEqual(actual.call_args.kwargs['display_rows']['alpha']['title'],'Saved Alpha')
        result=plans[0]
        self.assertEqual([(row.playlist_key,row.device_playlist_id,len(row.tracks))
                          for row in result.catalog_recognition],[('cust:FAVORITES',901,2)])
        self.assertTrue(result.metadata_updates);self.assertEqual(result.copies,())
        self.assertEqual(result.catalog_witness.bindings[0].uuid,native_uuid)
        self.case.commit_stock.assert_called_once_with(result)
        self.assertEqual(database.read_bytes(),before)
