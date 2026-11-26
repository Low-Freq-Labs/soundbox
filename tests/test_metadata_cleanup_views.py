"""Source cleanup choice reaches presentation, artwork and detached exporters."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import alias_intents, cdj_export, engine, library_codec, metadata
from soundbox import metadata_cleanup_policy as policy
from soundbox import stock_ipod, stock_ipod_catalog, dual_ipod
from soundbox.export_snapshot import capture_export
from soundbox.library import Library, Playlist, Track
from desktop.app import MainWindow
from desktop.prepared_browser import registry_inputs
from desktop.workers import ArtFillWorker


def library():
    track = Track('1', 'Origin - Song (Editor Remix) FINAL_', 'Native Artist',
                  'file:///generated/song.wav', source_uuid='uuid-one')
    playlist = Playlist('Set', [], ['1'], pid='playlist')
    return Library({'1': track}, [playlist], Path('/generated/master.db'),
                   source_kind='rekordbox-db', source_library_id='fixture-library')


class MetadataCleanupViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def original(self):
        lib = library()
        lib.metadata_cleanup_enabled = False
        lib.artist_registry = metadata.build_registry(
            ['Known', 'Native Artist'], aliases={'Native Artist': 'Manual Alias'}, cleanup_enabled=False)
        return lib

    def test_keep_preserves_raw_case_whitespace_empty_artist_and_title_without_rescue(self):
        registry = metadata.build_registry(['Known'], cleanup_enabled=False)
        for context in (registry, metadata.freeze_registry(registry)):
            for artist in ('', 'KNOWN ', 'Known_', 'native artist'):
                with self.subTest(artist=artist, frozen=type(context).__name__):
                    self.assertEqual(metadata.effective_artist('1', 'Known - Song', artist,
                        registry=context, stem='Another - Filename'), artist)
            self.assertEqual(metadata.effective_title('1', ' Known - Song FINAL_ ', 'Known',
                cleanup=False), ' Known - Song FINAL_ ')
        self.assertFalse(metadata.registry_context_equal(registry, metadata.build_registry(['Known'])))

    def test_keep_manual_overrides_and_exact_alias_chains_still_win(self):
        registry = metadata.build_registry([], aliases={'Variant': 'Chosen', 'Chosen': 'Final'},
                                           cleanup_enabled=False)
        self.assertEqual(metadata.effective_artist('1', 'Wrong - Song', 'Raw', {'1': 'Variant'},
            registry=registry), 'Final')
        self.assertEqual(metadata.effective_artist('1', 'Wrong - Song', 'variant', registry=registry), 'variant')
        self.assertEqual(metadata.effective_title('1', 'Raw FINAL_', 'Raw', {'1': 'Manual FINAL_'},
            cleanup=False), 'Manual FINAL_')

    def test_real_window_registry_and_labels_follow_exact_saved_source_choice(self):
        lib = library()
        settings = policy.with_choice({}, lib, 'original')
        window = SimpleNamespace(lib=lib, settings=settings, overrides={}, titles={},
                                 aliases={'Native Artist': 'Manual Alias'}, _title_junk=lambda: [])
        with mock.patch.object(metadata, 'derive_artist', side_effect=AssertionError('Automatic derivation')):
            MainWindow._build_artist_registry(window, invalidate_rows=False)
            self.assertEqual(MainWindow._track_label(window, '1'), lib.tracks_by_id['1'].name)
        self.assertFalse(lib.metadata_cleanup_enabled)
        row = engine.track_row(lib.tracks_by_id['1'], registry=lib.artist_registry)
        self.assertEqual((row['title'], row['artist']), (lib.tracks_by_id['1'].name, 'Manual Alias'))

    def test_registry_preparation_invalidates_when_only_cleanup_choice_changes(self):
        lib = library()
        bases, _trusted, previous = registry_inputs(lib, {}, {})
        self.assertEqual(bases, ['Editor'])
        lib.metadata_cleanup_enabled = False
        bases, _trusted, current = registry_inputs(lib, {}, {}, previous)
        self.assertEqual(bases, ['Native Artist'])
        self.assertEqual(current['changed_ids'], {'1'})

    def test_detached_export_and_worker_library_preserve_original_policy_and_alias_owner(self):
        lib = self.original()
        snapshot = capture_export(lib, lib.playlists)
        lib.metadata_cleanup_enabled = True
        lib.artist_registry._explicit_aliases.clear()
        captured = snapshot.materialize()
        self.assertFalse(captured.library.metadata_cleanup_enabled)
        row = engine.playlist_rows(captured.library, 'Set')[0]
        self.assertEqual((row['title'], row['artist']),
                         ('Origin - Song (Editor Remix) FINAL_', 'Manual Alias'))
        receiver = library_codec.LibraryReceiver(library_codec.describe(captured.library), max_records=100)
        for kind, field, rows in library_codec.batches(captured.library):
            receiver.add(kind, field, rows)
        restored = receiver.finish()
        self.assertFalse(restored.metadata_cleanup_enabled)
        self.assertEqual(engine.playlist_rows(restored, 'Set')[0]['artist'], 'Native Artist')

    def test_all_firmware_label_fallbacks_honor_keep_even_without_a_registry(self):
        lib = self.original()
        lib.artist_registry = None
        track = lib.tracks_by_id['1']
        expected = track.name, track.artist
        arguments = dict(derive_artists=True, artist_overrides={}, title_overrides={})
        self.assertEqual(stock_ipod._effective_track_metadata(track, track.path, library=lib, **arguments), expected)
        self.assertEqual(dual_ipod._effective_display_metadata(lib, track, track.path, **arguments), expected)
        fields = dict(stock_ipod_catalog.catalog_metadata(track, lib))
        self.assertEqual((fields['title'], fields['artist']), expected)
        track.name = ''; track.artist = ''
        self.assertEqual(stock_ipod._effective_track_metadata(track, track.path, library=lib, **arguments), ('', ''))
        self.assertEqual(dual_ipod._effective_display_metadata(lib, track, track.path, **arguments), ('', ''))

    def test_cdj_selection_hint_changes_for_policy_without_mutating_source_rows(self):
        lib = self.original(); lib.artist_registry = None
        original = deepcopy(lib.tracks_by_id)
        keep = cdj_export._selection_hint(lib, lib.playlists, {})
        lib.metadata_cleanup_enabled = True
        clean = cdj_export._selection_hint(lib, lib.playlists, {})
        self.assertNotEqual(keep, clean)
        self.assertEqual(lib.tracks_by_id, original)

    def test_artwork_query_keeps_original_metadata_and_manual_aliases(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'generated.wav'; path.write_bytes(b'generated fixture, never decoded')
            lib = self.original(); lib.tracks_by_id['1'].location_uri = path.as_uri()
            provider = SimpleNamespace(fetch_best=mock.Mock(return_value=None))
            cache = SimpleNamespace(status=lambda _tid: None, store_auto_if_unfetched=mock.Mock(return_value=False),
                                    rebalance_default_photos=mock.Mock())
            worker = ArtFillWorker(lib, cache, {}, {}, provider=provider)
            self.addCleanup(worker.deleteLater)
            with mock.patch.object(worker._cancelled, 'wait'):
                worker._fill()
            provider.fetch_best.assert_called_once_with('Manual Alias', lib.tracks_by_id['1'].name)

    def test_explicit_alias_generation_does_not_clean_an_original_title(self):
        lib = library(); settings = policy.with_choice({}, lib, 'original')
        raw = alias_intents.capture(lib, titles={}, artists={}, aliases={'Native Artist': 'Manual Alias'},
            settings=settings, visible={}, owned={}, previous={})
        descriptor, targets, _origins = alias_intents.build(raw)
        target = targets[descriptor['order'][0]]
        self.assertEqual((target['title'], target['artist']), (lib.tracks_by_id['1'].name, 'Manual Alias'))

    def test_original_alias_provenance_does_not_move_featured_credits_into_the_title(self):
        lib = library()
        lib.tracks_by_id['1'].name = 'Whiplash'
        lib.tracks_by_id['1'].artist = 'Gardella feat. Seth David'
        settings = policy.with_choice({}, lib, 'original')
        raw = alias_intents.capture(lib, titles={}, artists={}, aliases={}, settings=settings,
            visible={}, owned={}, previous={})
        origins = alias_intents.origins(json.loads(raw)['payload'], {}, {})
        self.assertEqual(next(iter(origins.values()))['provenance']['title_base'], 'Whiplash')

    def test_generated_stock_plan_publishes_original_values_without_touching_sources(self):
        from clickwheel.ipod import get_ipod_tracks, read_ipod
        from tests.test_stock_ipod import StockIpodSync
        fixture = StockIpodSync(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        track = fixture.library.tracks_by_id['new']
        track.name = 'Origin - Song (Editor Remix) FINAL_'; track.artist = 'Native Artist'
        lib = replace(fixture.library, metadata_cleanup_enabled=False)
        before = fixture.new_source.read_bytes()
        plan = stock_ipod.plan_stock_ipod_sync(lib, [fixture.playlist], fixture.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video')
        stock_ipod.commit_stock_ipod_sync(plan)
        dbid = plan.source_device_dbids[str(track.path.resolve())]
        row = next(row for row in get_ipod_tracks(read_ipod(fixture.ipod)) if row['dbid'] == dbid)
        self.assertEqual((row['title'], row['artist']), (track.name, track.artist))
        self.assertEqual(fixture.new_source.read_bytes(), before)

    def test_original_alias_change_and_unmerge_replay_after_native_readback_keeps_title(self):
        lib = library(); settings = policy.with_choice({}, lib, 'original')
        track = lib.tracks_by_id['1']; original_title = track.name
        for aliases, expected in (({'Native Artist': 'First'}, 'First'),
                                  ({'Native Artist': 'Second'}, 'Second'), ({}, 'Native Artist')):
            raw = alias_intents.capture(lib, titles={}, artists={}, aliases=aliases,
                settings=settings, visible={}, owned={}, previous={})
            descriptor, targets, origins = alias_intents.build(raw)
            identity = descriptor['order'][0]
            self.assertEqual((targets[identity]['title'], targets[identity]['artist']), (original_title, expected))
            track.artist = expected
            origins[identity]['native_artist'] = expected
            settings = {**settings, alias_intents.ORIGINS: origins}
