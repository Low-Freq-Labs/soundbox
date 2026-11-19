"""Immutable export admission keeps selected data and the full overlap boundary."""
from dataclasses import FrozenInstanceError
import json
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
from soundbox.stock_ipod_playlist_binding import (
    DevicePlaylist, compare_playlist_bindings, source_playlist_identity,
)
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

    def test_catalog_identity_opt_in_preserves_adopted_native_binding(self):
        self.selected.name = 'FAVORITES'
        self.selected.export_key = 'cust:FAVORITES'
        node = dict(ID='native', UUID='selected-uuid', rb_local_deleted=0,
                    ParentID=object(), unrelated=object())
        self.library.source_playlist_catalog = {
            'nodes': [node, dict(ID='other', UUID=object())],
            'members': object(), 'revision': object(),
        }
        live = source_playlist_identity(self.library, self.selected)
        captured = capture_export(self.library, [self.selected], playlist_identities=True)
        node['UUID'] = 'later-uuid'
        inputs = captured.materialize()
        worker = source_playlist_identity(inputs.library, inputs.playlists[0])
        self.assertEqual(worker, live)
        self.assertEqual(worker.key, 'cust:FAVORITES')
        self.assertEqual(worker.native_id, 'native')
        self.assertEqual(worker.uuid, 'selected-uuid')
        self.assertEqual(inputs.library.source_playlist_catalog, {'nodes': [
            dict(ID='native', UUID='selected-uuid', rb_local_deleted=0)]})
        compared = compare_playlist_bindings((worker,), (DevicePlaylist(901, 'Favorites', (11, 12, 11)),),
                                             track_links={'a': 11, 'b': 12})
        self.assertTrue(compared[0].recognized)
        self.assertTrue(compared[0].membership_safe)
        self.assertTrue(compared[0].renamed)
        self.assertFalse(compared[0].added or compared[0].removed or compared[0].reordered)
        default = capture_export(self.library, [self.selected]).materialize()
        self.assertEqual(default.library.source_playlist_catalog, {})
        self.assertTrue(source_playlist_identity(default.library, default.playlists[0]).identity_error)

    def test_catalog_identity_opt_in_preserves_missing_duplicate_deleted_and_malformed_refusals(self):
        valid = dict(ID='native', UUID='selected-uuid', rb_local_deleted=0)
        for nodes in ([], [valid, dict(valid)], [{**valid, 'rb_local_deleted': 1}],
                      [{**valid, 'UUID': ''}], [{**valid, 'UUID': 42}],
                      [dict(ID=None, UUID='selected-uuid')]):
            with self.subTest(nodes=nodes):
                self.library.source_playlist_catalog = {'nodes': nodes}
                live = source_playlist_identity(self.library, self.selected)
                inputs = capture_export(self.library, [self.selected], playlist_identities=True).materialize()
                worker = source_playlist_identity(inputs.library, inputs.playlists[0])
                self.assertTrue(live.identity_error)
                self.assertEqual(worker, live)
        self.library.source_playlist_catalog = {'nodes': [valid, {**valid, 'rb_local_deleted': 1}]}
        inputs = capture_export(self.library, [self.selected], playlist_identities=True).materialize()
        self.assertFalse(source_playlist_identity(inputs.library, inputs.playlists[0]).identity_error)

    def test_catalog_identity_opt_in_rejects_non_scalar_selected_nodes(self):
        for nodes in ([dict(ID='native', UUID={'not': 'a UUID'})],
                      [dict(ID='native', UUID='uuid', rb_local_deleted=[])],
                      [dict(ID='native', UUID='uuid', rb_local_deleted=float('nan'))],
                      [object()]):
            with self.subTest(nodes=nodes):
                self.library.source_playlist_catalog = {'nodes': nodes}
                with self.assertRaisesRegex(ValueError, 'invalid native'):
                    capture_export(self.library, [self.selected], playlist_identities=True)

    def test_complete_identity_capture_retains_only_bounded_valid_unselected_headers(self):
        selected = dict(ID='native', UUID='selected-uuid', rb_local_deleted=0)
        other = dict(ID='other', UUID='other-uuid', rb_local_deleted=0,
                     ParentID=object(), members=object())
        self.library.source_playlist_catalog = {'nodes': [selected, other], 'members': object()}
        captured = capture_export(self.library, [self.selected], complete=True, playlist_identities=True)
        other['UUID'] = 'later-replacement'
        inputs = captured.materialize()
        self.assertEqual(inputs.library.source_playlist_catalog, {'nodes': [selected,
            dict(ID='other', UUID='other-uuid', rb_local_deleted=0)]})
        identity = source_playlist_identity(inputs.library, inputs.library.playlists[1])
        self.assertEqual(identity.uuid, 'other-uuid')
        self.assertFalse(identity.identity_error)

    def test_invalid_or_ambiguous_unselected_headers_do_not_block_complete_export(self):
        selected = dict(ID='native', UUID='selected-uuid', rb_local_deleted=0)
        valid_other = dict(ID='other', UUID='other-uuid', rb_local_deleted=0)
        for unwanted in ([dict(ID='other', UUID=object())],
                         [valid_other, dict(valid_other)],
                         [dict(ID='other', UUID='', rb_local_deleted=0)], [object()]):
            with self.subTest(unwanted=unwanted):
                self.library.source_playlist_catalog = {'nodes': [selected, *unwanted]}
                inputs = capture_export(self.library, [self.selected], complete=True,
                                        playlist_identities=True).materialize()
                self.assertEqual(inputs.library.source_playlist_catalog, {'nodes': [selected]})
                self.assertTrue(source_playlist_identity(inputs.library,
                    inputs.library.playlists[1]).identity_error)
        self.library.source_playlist_catalog = {'nodes': [
            dict(ID='native', UUID=object()), valid_other]}
        with self.assertRaisesRegex(ValueError, 'invalid native'):
            capture_export(self.library, [self.selected], complete=True, playlist_identities=True)

    def test_empty_pid_playlist_filters_are_preserved(self):
        playlist = Playlist('Local', [], ['a', 'b'])
        self.library.removed_by_playlist = {'': ['b']}
        inputs = capture_export(self.library, [playlist]).materialize()
        self.assertEqual([t.track_id for t in inputs.library.resolve(inputs.playlists[0])], ['a'])

    def test_nonempty_exclusion_sets_round_trip_for_selected_and_complete_inventory(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                selected_mask={'b'};unrelated_mask={'other', 'missing'}
                self.library.removed_by_playlist={'native': selected_mask, '4016338146': unrelated_mask}
                captured=capture_export(self.library, [self.selected], complete=complete)
                raw=json.loads(captured.payload)['removed_by_playlist']
                self.assertEqual(raw['native'], ['b'])
                if complete:self.assertEqual(raw['4016338146'], ['missing', 'other'])
                else:self.assertNotIn('4016338146', raw)
                self.assertEqual(self.selected.keys, ['a', 'b', 'a'])
                self.assertEqual(selected_mask, {'b'});self.assertEqual(unrelated_mask, {'other', 'missing'})
                selected_mask.add('a');unrelated_mask.clear()
                inputs=captured.materialize();worker_mask=inputs.library.removed_by_playlist['native']
                self.assertIs(type(worker_mask), set);self.assertEqual(worker_mask, {'b'})
                self.assertIsNot(worker_mask, selected_mask)
                self.assertEqual([track.track_id for track in inputs.library.resolve(inputs.playlists[0])], ['a', 'a'])
                self.assertEqual(inputs.playlists[0].keys, ['a', 'b', 'a'])
                worker_mask.clear()
                self.assertEqual(captured.materialize().library.removed_by_playlist['native'], {'b'})
                self.assertIn(Path('/other/source.wav'), captured.sources)
                self.assertIn(Path('/other/ANLZ0000.DAT'), captured.sources)
                self.assertEqual(tuple(inputs.library.export_source_paths), captured.sources)

    def test_exclusion_lists_and_sets_have_one_deterministic_payload(self):
        self.library.removed_by_playlist={'native': ['b', 'missing', 'b']}
        before=capture_export(self.library, [self.selected])
        self.library.removed_by_playlist={'native': {'missing', 'b'}}
        after=capture_export(self.library, [self.selected])
        self.assertEqual(before.payload, after.payload)
        self.assertEqual(before.materialize().library.removed_by_playlist, {'native': {'b', 'missing'}})

    def test_exclusion_conversion_is_typed_and_does_not_coerce_other_musical_values(self):
        for invalid in ('b', {'b': True}, {'b', 7}, [True], None):
            with self.subTest(invalid=invalid):
                self.library.removed_by_playlist={'native': invalid}
                with self.assertRaisesRegex(ValueError, 'membership identities'):
                    capture_export(self.library, [self.selected])
        self.library.removed_by_playlist={'native': {'b'}, 'unrelated': object()}
        self.assertEqual(capture_export(self.library, [self.selected]).materialize().library.removed_by_playlist,
            {'native': {'b'}})
        self.library.removed_by_playlist={};self.a.info={'label': {'invalid musical set'}}
        source_uri=self.a.location_uri;keys=list(self.selected.keys)
        with self.assertRaises(TypeError):capture_export(self.library, [self.selected])
        self.assertEqual(self.library.removed_by_playlist, {})
        self.assertEqual(self.a.info, {'label': {'invalid musical set'}})
        self.assertEqual(self.a.location_uri, source_uri);self.assertEqual(self.selected.keys, keys)

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
