"""Fulfilled manual scalars stay exact across prepared display/query/export."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import (alias_intents, browser_cache, cdj_export, dual_ipod, engine,
                      libcache, library_codec, metadata, metadata_context,
                      metadata_provenance as provenance, stock_ipod_catalog)
from soundbox.export_snapshot import capture_export
from soundbox.library import Library, Playlist, Track
from desktop.prepared_browser import registry_inputs
from desktop.workers import ArtFillWorker
from tests import test_prepared_native_publication as window_fixtures

MANUAL='Payday -Shadow Puppets (Yami Flip)'
AUTOMATIC='Payday -Shadow Puppets (YAMI Flip)'


def fixture(path=Path('/generated/song.wav')):
    track=Track('1',MANUAL,'YAMI',path.as_uri(),bpm='140',source_uuid='uuid-one',source_native_path=str(path))
    tracks={'1':track,**{str(i):Track(str(i),'Independent song '+str(i),'YAMI',
        f'file:///generated/independent-{i}.wav',bpm='140',source_uuid='uuid-'+str(i)) for i in (2,3)}}
    library=Library(tracks,[Playlist('Set',[],['1'],pid='set')],Path('/generated/master.db'),
                    source_kind='rekordbox-db',source_library_id='library')
    scope=dict(source=str(library.source),kind=library.source_kind,library_id=library.source_library_id)
    origin=dict(scope=scope,local_id='1',native_id=track.native_id,uuid=track.source_uuid,path=str(track.path),
                automatic='YAMI',native_artist='YAMI',automatic_title=MANUAL,native_title=MANUAL,title_shield='')
    origin=provenance.seed(origin,title=MANUAL,artist='YAMI',display_artist='YAMI')
    origin=provenance.acknowledge(origin,dict(id='a'*32,field='title',value=MANUAL,
        curation=dict(token='',manual=MANUAL,physical=MANUAL,overlay=None)))
    return library,{alias_intents.key(scope,origin):origin}


def prepare(library,origins,previous=None):
    bases,trusted,prepared=registry_inputs(library,origins,{},previous)
    library.artist_registry=metadata.build_registry(bases,trusted)
    return prepared


class ManualMetadataContext(unittest.TestCase):
    def test_exact_manual_ack_survives_registry_title_case_and_explicit_overrides_win(self):
        library,origins=fixture();track=library.tracks_by_id['1'];prepare(library,origins)
        before=deepcopy(origins)
        self.assertEqual(engine.effective_pair(track,library=library),(MANUAL,'YAMI'))
        self.assertEqual(engine.track_row(track,library=library)['title'],MANUAL)
        self.assertEqual(engine.playlist_rows(library,'Set')[0]['title'],MANUAL)
        self.assertEqual(engine.effective_pair(track,titles={'1':'NEW explicit TITLE'},library=library)[0],'NEW explicit TITLE')
        library.artist_registry=metadata.build_registry(['YAMI'],['YAMI'],{'YAMI':'Explicit Alias'})
        self.assertEqual(engine.effective_pair(track,library=library),(MANUAL,'Explicit Alias'))
        self.assertEqual(origins,before)

    def test_wrong_scope_native_identity_binding_and_detached_track_refuse_borrowed_ack(self):
        library,origins=fixture();prepare(library,origins);track=library.tracks_by_id['1']
        self.assertEqual(engine.effective_pair(track,registry=library.artist_registry)[0],AUTOMATIC)
        for changes in ({'source':'/other/master.db'},{'source_library_id':'other'},{'source_kind':'xml'}):
            foreign=replace(library,**changes)
            with self.subTest(changes=changes):self.assertEqual(engine.effective_pair(track,library=foreign)[0],AUTOMATIC)
        for changes in ({'source_uuid':'foreign-uuid'},{'source_native_id':'other-native'},
                        {'source_native_path':'/other/song.wav'}):
            other=replace(track,**changes);changed=replace(library,tracks_by_id={'1':other})
            with self.subTest(changes=changes):self.assertEqual(engine.effective_pair(other,library=changed)[0],AUTOMATIC)
        self.assertEqual(engine.effective_pair(replace(track),library=library)[0],AUTOMATIC)
        library.source_binding_issues={'1':'Ambiguous source'}
        self.assertEqual(engine.effective_pair(track,library=library)[0],AUTOMATIC)

    def test_source_aba_rebuilds_only_its_own_exact_manual_context(self):
        library,origins=fixture();previous=prepare(library,origins)
        foreign=replace(library,source='/other/master.db',source_library_id='other',
                        tracks_by_id=deepcopy(library.tracks_by_id))
        previous=prepare(foreign,origins,previous)
        self.assertEqual(engine.effective_pair(foreign.tracks_by_id['1'],library=foreign)[0],AUTOMATIC)
        returned=replace(foreign,source=library.source,source_library_id=library.source_library_id)
        self.assertEqual(engine.effective_pair(returned.tracks_by_id['1'],library=returned)[0],AUTOMATIC)
        prepare(returned,origins,previous)
        self.assertEqual(engine.effective_pair(returned.tracks_by_id['1'],library=returned)[0],MANUAL)

    def test_superseded_tokens_changed_native_values_and_duplicate_owners_refuse_manual(self):
        library,origins=fixture();key=next(iter(origins))
        for mutate in (lambda origin:origin['provenance']['acks']['title'].update(superseded=True),
                       lambda origin:origin['provenance']['edits'].update(title='b'*32),
                       lambda origin:origin.update(native_title='Outside title')):
            changed=deepcopy(origins);mutate(changed[key]);prepare(library,changed)
            self.assertEqual(engine.effective_pair(library.tracks_by_id['1'],library=library)[0],AUTOMATIC)
        prepare(library,{**origins,'duplicate':origins[key]})
        self.assertEqual(engine.effective_pair(library.tracks_by_id['1'],library=library)[0],AUTOMATIC)
        prepare(library,origins)
        self.assertIsNone(metadata_context.values(library,library.tracks_by_id['1'],('Outside native title','YAMI'))[0])
        self.assertEqual(engine.effective_pair(library.tracks_by_id['1'],library=library,
            source_pair=('Outside native title','YAMI'))[0],'Outside native title')

    def test_prepared_context_is_immutable_detached_and_unchanged_rows_do_not_reparse_origins(self):
        library,origins=fixture();previous=prepare(library,origins);context=library.metadata_context
        with self.assertRaises(FrozenInstanceError):context.source=('wrong','wrong','wrong')
        copied=deepcopy(library)
        self.assertIs(copied.metadata_context,context)
        self.assertEqual(engine.effective_pair(copied.tracks_by_id['1'],library=copied)[0],MANUAL)
        with mock.patch('soundbox.metadata_context.prepare_row',side_effect=AssertionError('Unchanged row reparsed')):
            current=registry_inputs(library,origins,{},previous)[2]
        self.assertFalse(current['changed_ids'])
        self.assertIs(current['manual_rows']['1'],previous['manual_rows']['1'])
        origins[next(iter(origins))]['provenance']['acks']['title']['manual']='Uncommitted mutation'
        self.assertEqual(engine.effective_pair(library.tracks_by_id['1'],library=library)[0],MANUAL)
        self.assertEqual(metadata_context.signature(library),context.signature)

    def test_reader_cache_transport_stays_unbound_but_export_freezes_selected_manual_values(self):
        library,origins=fixture();prepare(library,origins)
        with tempfile.TemporaryDirectory() as area:
            path=Path(area)/'library.json';libcache.dump(library,path);raw=libcache.load(path)
            self.assertIsNone(raw.metadata_context)
        receiver=library_codec.LibraryReceiver(library_codec.describe(library),max_records=100)
        for kind,field,rows in library_codec.batches(library):receiver.add(kind,field,rows)
        self.assertIsNone(receiver.finish().metadata_context)
        snapshot=capture_export(library,library.playlists)
        self.assertNotIn('metadata_context',json.loads(snapshot.payload))
        materialized=snapshot.materialize()
        self.assertIsNone(materialized.library.metadata_context)
        self.assertEqual(materialized.titles,{'1':MANUAL})
        self.assertEqual(engine.playlist_rows(materialized.library,'Set',titles=materialized.titles)[0]['title'],MANUAL)
        foreign=replace(library,source_library_id='other')
        self.assertEqual(capture_export(foreign,foreign.playlists).materialize().titles,{})

    def test_stock_dual_and_cdj_hint_fallbacks_agree_with_browser_without_native_access(self):
        library,origins=fixture();prepare(library,origins);track=library.tracks_by_id['1']
        fields=dict(stock_ipod_catalog.catalog_metadata(track,library))
        self.assertEqual(fields['title'],MANUAL)
        self.assertIn((MANUAL,'YAMI'),stock_ipod_catalog._labels(track,library,True,{},{}))
        self.assertEqual(dual_ipod._effective_display_metadata(library,track,track.path,
            derive_artists=True,artist_overrides={},title_overrides={}), (MANUAL,'YAMI'))
        rows={};cdj_export._selection_hint(library,library.playlists,rows)
        self.assertEqual(rows['1'][6:8],[MANUAL,'YAMI'])


class ManualMetadataConsumers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        window_fixtures.PreparedPublication.setUpClass()

    def test_artwork_query_uses_the_exact_prepared_manual_title(self):
        with tempfile.TemporaryDirectory() as area:
            path=Path(area)/'song.wav';path.write_bytes(b'owned generated placeholder, never decoded')
            library,origins=fixture(path);prepare(library,origins)
            provider=SimpleNamespace(fetch_best=mock.Mock(return_value=None))
            cache=SimpleNamespace(status=lambda _tid:None,store_auto_if_unfetched=mock.Mock(return_value=False),rebalance_default_photos=mock.Mock())
            worker=ArtFillWorker(library,cache,{}, {},provider=provider);self.addCleanup(worker.deleteLater)
            with mock.patch.object(worker._cancelled,'wait'):worker._fill()
            provider.fetch_best.assert_called_once_with('YAMI',MANUAL)

    def test_window_first_rows_artists_player_label_and_fresh_cache_library_keep_manual_title(self):
        host=window_fixtures.PreparedPublication();self.addCleanup(host.doCleanups);host.setUp();window=host.window
        area=Path(self.enterContext(tempfile.TemporaryDirectory()));path=area/'generated.wav';path.write_bytes(b'owned placeholder, never decoded')
        library,origins=fixture(path);window.lib=library;window.settings[alias_intents.ORIGINS]=origins
        fingerprint=browser_cache.fingerprint
        def fingerprint_prepared(inputs):
            # A complete-looking later render cannot repair a checkpoint key
            # computed before its exact-source manual inputs were prepared.
            self.assertEqual(engine.effective_pair(window.lib.tracks_by_id['1'],library=window.lib)[0],MANUAL)
            self.assertEqual(inputs[-1],metadata_context.signature(window.lib))
            return fingerprint(inputs)
        with mock.patch.object(browser_cache,'fingerprint',side_effect=fingerprint_prepared) as keyed:
            window._apply_loaded_library()
        keyed.assert_called_once()
        window._show_collection();window._prepare_artists()
        self.assertEqual(next(row for row in window.model._all_rows if row['id']=='1')['title'],MANUAL)
        self.assertEqual(window._artists_rows['1']['title'],MANUAL)
        self.assertEqual(window._track_label('1'),MANUAL)
        track=window.lib.tracks_by_id['1']
        with mock.patch.object(window.player_bar,'play_track') as load,mock.patch.object(window,'_sync_cue_editing'):
            window._open_track(track,autoplay=False)
        self.assertEqual(load.call_args.kwargs['title'],'YAMI — '+MANUAL)
        old_key=window._browser_cache_key
        raw=replace(window.lib,tracks_by_id=deepcopy(window.lib.tracks_by_id),metadata_context=None)
        window.lib=raw;window._apply_loaded_library();window._show_collection();window._prepare_artists()
        self.assertIsNotNone(window.lib.metadata_context)
        self.assertEqual(window._row_for(window.lib.tracks_by_id['1'])['title'],MANUAL)
        self.assertEqual(window._artists_rows['1']['title'],MANUAL)
        self.assertEqual(window._browser_cache_key,old_key)

    def test_ack_only_manual_context_change_invalidates_saved_browser_key_before_first_rows(self):
        host=window_fixtures.PreparedPublication();self.addCleanup(host.doCleanups);host.setUp();window=host.window
        library,origins=fixture();window.lib=library;window.settings[alias_intents.ORIGINS]={}
        window._apply_loaded_library();window._show_collection()
        before=window._browser_cache_key
        self.assertEqual(window._row_for(library.tracks_by_id['1'])['title'],AUTOMATIC)
        window.settings[alias_intents.ORIGINS]=origins
        window._apply_loaded_library();window._show_collection()
        self.assertNotEqual(window._browser_cache_key,before)
        self.assertEqual(window._row_for(library.tracks_by_id['1'])['title'],MANUAL)
        self.assertEqual(browser_cache.SCHEMA,6)
