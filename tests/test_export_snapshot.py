"""Immutable export admission keeps selected data and the full overlap boundary."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from soundbox import cdj_export, sync
from soundbox.export_snapshot import capture_export
from soundbox.library import Library, Playlist, Track
from soundbox.metadata import ArtistRegistry
from soundbox.stock_ipod import commit_stock_ipod_sync, plan_stock_ipod_sync
from soundbox.dual_ipod import commit_dual_ipod_sync, plan_dual_ipod_sync


class ExportSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.a = Track('a', 'First', 'artist', 'file:///source/a.wav', source_uuid='uuid-a',
                       source_cue_revision='cue-revision', info={'label': 'before'})
        self.b = Track('b', 'Second', 'Artist', 'file:///source/b.wav')
        self.other = Track('other', 'Unselected', 'Other', 'file:///other/source.wav',
                           source_analysis_path='/other/ANLZ0000.DAT')
        self.selected = Playlist('Selected', ['Folder'], ['a', 'b', 'a'], 'native')
        self.unselected = Playlist('Unselected', [], ['other'], 'other')
        self.library = Library({'a': self.a, 'b': self.b, 'other': self.other},
                               [self.selected, self.unselected], '/library/master.db',
                               source_kind='rekordbox-db', source_library_id='library-id',
                               artist_registry=ArtistRegistry({'artist': 'Artist'}, {'artist'}))

    def test_selected_data_detaches_before_dispatch_and_each_materialization_is_independent(self):
        titles = {'a': 'Saved title', 'other': 'Unselected override'}
        artists = {'a': 'artist'}
        self.library.info_overrides = {'a': {'label': 'Saved label'}}
        self.library.media_facts = {'a': {'epoch': [1, 2, 3, 4, 5]}}
        captured = capture_export(self.library, [self.selected], titles=titles, artists=artists)
        self.a.name = 'Later'; self.a.info['label'] = 'later'
        self.selected.keys.clear(); self.selected.folder_path.append('Later')
        titles['a'] = 'Later'; artists['a'] = 'Other'
        self.library.hidden_ids.add('a')
        self.library.info_overrides['a']['label'] = 'Later'
        self.library.media_facts['a']['epoch'][0] = 99
        self.library.artist_registry._canon['artist'] = 'Later'
        self.library.source_library_id = 'different-library'
        inputs = captured.materialize()
        self.assertEqual(set(inputs.library.tracks_by_id), {'a', 'b'})
        self.assertEqual([track.track_id for track in inputs.library.resolve(inputs.playlists[0])], ['a', 'b', 'a'])
        self.assertEqual(inputs.library.tracks_by_id['a'].name, 'First')
        self.assertEqual(inputs.library.tracks_by_id['a'].info, {'label': 'before'})
        self.assertEqual(inputs.playlists[0].folder_path, ['Folder'])
        self.assertEqual(inputs.titles, {'a': 'Saved title'})
        self.assertEqual(inputs.artists, {'a': 'artist'})
        self.assertEqual(inputs.library.artist_registry.canonical('artist'), 'Artist')
        self.assertEqual(inputs.library.info_overrides['a']['label'], 'Saved label')
        self.assertEqual(inputs.library.media_facts['a']['epoch'], [1, 2, 3, 4, 5])
        self.assertEqual(inputs.library.source_library_id, 'library-id')
        inputs.library.tracks_by_id['a'].info.clear()
        self.assertEqual(captured.materialize().library.tracks_by_id['a'].info, {'label': 'before'})
        with self.assertRaises(FrozenInstanceError):
            captured.payload = b'changed'

    def test_hidden_raw_members_keep_native_membership_proof_without_exporting_them(self):
        self.library.hidden_ids.add('b')
        captured = capture_export(self.library, [self.selected]).materialize()
        self.assertIn('b', captured.library.tracks_by_id)
        self.assertEqual(captured.playlists[0].keys, ['a', 'b', 'a'])
        self.assertEqual([track.track_id for track in captured.library.resolve(captured.playlists[0])], ['a', 'a'])
        self.assertEqual([p.pid for p in captured.library.playlists], ['native'])

    def test_unrelated_cue_catalog_and_track_graphs_are_never_serialized(self):
        self.a.source_cue_refs = [object()]
        self.a.source_cues = [object()]
        self.a.source_tag_times = {'display-only': object()}
        self.other.info = {'unrelated': object()}
        self.library.source_playlist_catalog = {'unrelated': object()}
        inputs = capture_export(self.library, [self.selected]).materialize()
        self.assertEqual(inputs.library.tracks_by_id['a'].source_cue_refs, [])
        self.assertIsNone(inputs.library.tracks_by_id['a'].source_cues)
        self.assertEqual(inputs.library.tracks_by_id['a'].source_tag_times, {})
        self.assertEqual(inputs.library.tracks_by_id['a'].source_cue_revision, 'cue-revision')
        self.assertEqual(inputs.library.source_playlist_catalog, {})

    def test_empty_pid_playlist_filters_are_preserved(self):
        playlist = Playlist('Local', [], ['a', 'b'])
        self.library.removed_by_playlist = {'': ['b']}
        inputs = capture_export(self.library, [playlist]).materialize()
        self.assertEqual([t.track_id for t in inputs.library.resolve(inputs.playlists[0])], ['a'])

    def test_complete_firmware_inventory_retains_unselected_historical_identities(self):
        extra = Playlist('Custom', [], ['other'], 'custom')
        self.library.hidden_ids.add('other')
        captured = capture_export(self.library, [self.selected], extra=[extra], complete=True)
        self.other.location_uri = 'file:///later/relocated.wav'
        self.unselected.keys.clear(); extra.keys.clear()
        inputs = captured.materialize()
        self.assertEqual(set(inputs.library.tracks_by_id), {'a', 'b', 'other'})
        self.assertEqual(inputs.library.tracks_by_id['other'].path, Path('/other/source.wav'))
        self.assertEqual(inputs.library.playlists[1].keys, ['other'])
        self.assertEqual(inputs.extra[0].keys, ['other'])
        self.assertEqual(inputs.library.hidden_ids, {'other'})

    def test_standalone_track_detaches_without_a_fake_playlist(self):
        captured = capture_export(self.library, standalone=[self.a])
        self.a.name = 'Later'
        inputs = captured.materialize()
        self.assertEqual(inputs.playlists, ())
        self.assertEqual(inputs.library.playlists, [])
        self.assertEqual(inputs.standalone[0].name, 'First')

    def test_unselected_analysis_overlap_still_refuses_before_any_export_preparation(self):
        with tempfile.TemporaryDirectory() as area:
            target = Path(area) / 'USB'
            target.mkdir()
            self.other.source_analysis_path = str(target / 'source/ANLZ0000.DAT')
            captured = capture_export(self.library, [self.selected])
            self.other.source_analysis_path = '/later/analysis.DAT'
            inputs = captured.materialize()
            self.assertIn(target / 'source/ANLZ0000.DAT', captured.sources)
            self.assertNotIn('other', inputs.library.tracks_by_id)
            with mock.patch('soundbox.cdj_transaction.recover', side_effect=AssertionError('Prepared overlapping target')):
                with self.assertRaisesRegex(RuntimeError, 'overlaps source'):
                    cdj_export.plan_export(inputs.library, inputs.playlists, target,
                        token=target.stat().st_dev, volume_uuid='fixture')
            with mock.patch('soundbox.manifest.load', side_effect=AssertionError('Read overlapping target')):
                with self.assertRaisesRegex(RuntimeError, 'overlaps source'):
                    sync.run(inputs.library, list(inputs.playlists), target)
            for planner in (plan_stock_ipod_sync, plan_dual_ipod_sync):
                with self.subTest(planner=planner.__name__):
                    options = ({'rockbox_media_cache': SimpleNamespace(root=Path(area)), 'rockbox_art_cache': None}
                               if planner is plan_dual_ipod_sync else {})
                    with self.assertRaisesRegex(RuntimeError, 'overlaps source'):
                        planner(inputs.library, list(inputs.playlists), target,
                                model_number='MA446', filesystem='fat32', **options)
            # The original captured boundary also guards publication, even when
            # every selected payload is retained and there are no new copies.
            guarded = SimpleNamespace(target=target, source_guard_paths=captured.sources,
                                      copies=(), rewrites=(), progress_total=0,
                                      media_copies=(), artwork_copies=(), file_rewrites=())
            for publisher in (commit_stock_ipod_sync, commit_dual_ipod_sync):
                with self.subTest(publisher=publisher.__name__):
                    with self.assertRaisesRegex(RuntimeError, 'overlaps source'):
                        publisher(guarded)
