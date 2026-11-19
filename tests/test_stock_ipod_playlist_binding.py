"""Generated cached membership deltas: no audio, source/device paths or writers."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest
from unittest import mock

from soundbox.library import Library, Playlist, Track
from soundbox.stock_ipod_catalog import StockCatalogComparison
from soundbox.stock_ipod_playlist_binding import (
    DevicePlaylist, PlaylistBinding, SourcePlaylist, compare_playlist_bindings,
    normalize_playlist_name, playlist_binding_from_dict, source_playlist_identity)


class PlaylistBindings(unittest.TestCase):
    def source(self, members=('a', 'b'), **changes):
        return replace(SourcePlaylist('favorites', 'native-favorites', 'uuid-favorites',
                                      'Favorites', tuple(members)), **changes)

    def device(self, members=(101, 102), **changes):
        return replace(DevicePlaylist(901, 'Favorites', tuple(members)), **changes)

    def binding(self, members=(('a', 101), ('b', 102)), **changes):
        return replace(PlaylistBinding('favorites', 'native-favorites', 'uuid-favorites',
                                       901, 'Favorites', tuple(members)), **changes)

    def compare(self, source=None, device=None, *, links=None, bindings=()):
        with mock.patch('builtins.open', side_effect=AssertionError('audio/file read')), \
                mock.patch.object(Path, 'open', side_effect=AssertionError('path read')), \
                mock.patch.object(Path, 'stat', side_effect=AssertionError('path stat')):
            return compare_playlist_bindings([source or self.source()], [device or self.device()],
                track_links=links if links is not None else {'a': 101, 'b': 102, 'c': 103},
                bindings=bindings)[0]

    def test_manual_212_to_214_bootstraps_from_complete_old_cached_members(self):
        old = tuple(f'track-{index}' for index in range(212))
        links = {local: 1000 + index for index, local in enumerate(old)}
        source = self.source((*old, 'new-212', 'new-213'))
        result = self.compare(source, self.device(tuple(links.values())), links=links)
        self.assertTrue(result.recognized)
        self.assertTrue(result.membership_safe)
        self.assertEqual(result.basis, 'name-linked-members')
        self.assertEqual(len(result.retained), 212)
        self.assertEqual([(row.local_id, row.dbid) for row in result.added],
                         [('new-212', None), ('new-213', None)])
        self.assertEqual((result.removed, result.reordered, result.unknown_device_ids), ((), False, ()))
        self.assertEqual(result.binding.members, tuple(links.items()))

    def test_committed_association_survives_additions_and_playlist_rename(self):
        source = self.source(('a', 'b', 'c', 'new'), name='Renamed favorites')
        result = self.compare(source, bindings=(self.binding(),))
        self.assertEqual(result.basis, 'binding')
        self.assertTrue(result.membership_safe)
        self.assertTrue(result.renamed)
        self.assertTrue(result.needs_sync)
        self.assertEqual([(row.local_id, row.dbid) for row in result.added], [('c', 103), ('new', None)])

    def test_repeated_sync_after_214_commit_needs_only_the_next_new_occurrences(self):
        old = tuple(f'track-{index}' for index in range(214))
        links = {local: 1000 + index for index, local in enumerate(old)}
        saved = self.binding(tuple(links.items()))
        result = self.compare(self.source((*old, 'new-214', 'new-215')),
                              self.device(tuple(links.values())), links=links, bindings=(saved,))
        self.assertEqual(result.device_playlist_id, 901)
        self.assertEqual(result.basis, 'binding')
        self.assertEqual(len(result.retained), 214)
        self.assertEqual([row.local_id for row in result.added], ['new-214', 'new-215'])

    def test_reorder_and_removal_use_song_identity_instead_of_device_position(self):
        result = self.compare(self.source(('b', 'a')), self.device((101, 102, 103)),
                              bindings=(self.binding((('a', 101), ('b', 102), ('c', 103))),))
        self.assertTrue(result.membership_safe)
        self.assertTrue(result.reordered)
        self.assertEqual([(row.local_id, row.device_index) for row in result.retained], [('b', 1), ('a', 0)])
        self.assertEqual([(row.local_id, row.dbid) for row in result.removed], [('c', 103)])
        self.assertEqual(result.added, ())

    def test_duplicate_membership_keeps_occurrence_counts_and_existing_catalog_identity(self):
        result = self.compare(self.source(('a', 'b', 'a', 'a')), self.device((101, 101, 102)),
                              bindings=(self.binding((('a', 101), ('a', 101), ('b', 102))),))
        self.assertTrue(result.membership_safe)
        self.assertTrue(result.reordered)
        self.assertEqual([row.device_index for row in result.retained], [0, 2, 1])
        self.assertEqual([(row.local_id, row.dbid, row.source_index) for row in result.added], [('a', 101, 3)])
        self.assertEqual(result.removed, ())

    def test_duplicate_removal_changes_only_the_excess_occurrence(self):
        result = self.compare(self.source(('a',)), self.device((101, 101)),
                              bindings=(self.binding((('a', 101), ('a', 101))),))
        self.assertEqual([row.device_index for row in result.retained], [0])
        self.assertEqual([(row.local_id, row.device_index) for row in result.removed], [('a', 1)])
        self.assertFalse(result.reordered)

    def test_selected_empty_bound_playlist_removes_members_without_media_authority(self):
        result = self.compare(self.source(()), bindings=(self.binding(),))
        self.assertTrue(result.membership_safe)
        self.assertEqual([row.local_id for row in result.removed], ['a', 'b'])
        self.assertEqual((result.retained, result.added), ((), ()))
        self.assertFalse(any(key in asdict(result) for key in ('owned', 'media_deletes', 'source_sha256')))

    def test_overlapping_playlists_keep_shared_track_identity_without_competing(self):
        sources = [self.source(), self.source(('b', 'c'), key='other', native_id='native-other',
                                              uuid='uuid-other', name='Other')]
        devices = [self.device(), self.device((102, 103), playlist_id=902, name='Other')]
        rows = compare_playlist_bindings(sources, devices, track_links={'a': 101, 'b': 102, 'c': 103})
        self.assertTrue(all(row.membership_safe for row in rows))
        self.assertEqual([row.local_id for row in rows[0].retained], ['a', 'b'])
        self.assertEqual([row.local_id for row in rows[1].retained], ['b', 'c'])

    def test_unknown_foreign_occurrences_are_flagged_and_never_classified_as_removals(self):
        result = self.compare(self.source(('a', 'new')), self.device((999, 101, 999)),
                              bindings=(self.binding((('a', 101),)),))
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.unknown_device_ids, (999, 999))
        self.assertEqual(result.removed, ())
        self.assertEqual([row.local_id for row in result.retained], ['a'])
        self.assertEqual(result.added[0].local_id, 'new')
        self.assertIn('preserved', result.detail)

    def test_unknown_extra_alone_keeps_clean_metadata_and_artwork_sync_pending(self):
        source = self.source()
        device = self.device((101, 102, 999))
        saved = self.binding()
        before = (asdict(source), asdict(device), asdict(saved))
        result = self.compare(source, device, bindings=(saved,))
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.unknown_device_ids, (999,))
        self.assertEqual((result.added, result.removed, result.reordered, result.renamed,
                          result.unexpected_device_ids), ((), (), False, False, ()))
        self.assertIn('Unknown device entries are preserved', result.detail)
        self.assertTrue(result.needs_sync)
        comparison = StockCatalogComparison((), (), playlist_changes=(result,))
        self.assertEqual((comparison.changes, comparison.artwork_track_ids), ((), ()))
        self.assertTrue(comparison.needs_sync)
        self.assertEqual((asdict(source), asdict(device), asdict(saved)), before)

    def test_all_known_clean_playlist_metadata_and_artwork_do_not_need_sync(self):
        result = self.compare(bindings=(self.binding(),))
        self.assertTrue(result.recognized)
        self.assertTrue(result.membership_safe)
        self.assertEqual(result.detail, '')
        self.assertFalse(result.needs_sync)
        comparison = StockCatalogComparison((), (), playlist_changes=(result,))
        self.assertFalse(comparison.needs_sync)

    def test_name_count_candidate_does_not_invent_unknown_song_links(self):
        result = self.compare(device=self.device((999, 101)))
        self.assertTrue(result.recognized)
        self.assertEqual(result.basis, 'name-count')
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.binding.members, (('a',101),))
        self.assertEqual(result.unknown_device_ids, (999,))
        self.assertEqual([row.local_id for row in result.retained], ['a'])

    def test_known_external_addition_from_another_playlist_is_preserved_not_removed(self):
        result = self.compare(self.source(('a',)), self.device((101, 103)),
                              bindings=(self.binding((('a', 101),)),))
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertTrue(result.needs_sync)
        self.assertEqual(result.unexpected_device_ids, (103,))
        self.assertEqual((result.removed, result.unknown_device_ids), ((), ()))
        self.assertEqual([row.local_id for row in result.retained], ['a'])

    def test_external_duplicate_preserves_only_the_occurrence_beyond_baseline_and_desired(self):
        result = self.compare(self.source(('a',)), self.device((101, 101, 101)),
                              bindings=(self.binding((('a', 101), ('a', 101))),))
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.unexpected_device_ids, (101,))
        self.assertEqual([row.device_index for row in result.removed], [1])
        self.assertEqual([row.device_index for row in result.retained], [0])

    def test_bootstrap_cannot_remove_a_globally_known_but_unselected_member(self):
        result = self.compare(self.source(('a', 'new')), self.device((101, 103)))
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.unexpected_device_ids, (103,))
        self.assertEqual(result.removed, ())
        self.assertEqual(result.binding.members, (('a',101),))

    def test_partial_count_different_bootstrap_keeps_known_links_and_blocks_membership(self):
        for source, device in ((self.source(('a', 'new', 'another')), self.device((101, 999))),
                               (self.source(('a',)), self.device((101, 102)))):
            with self.subTest(source=source):
                result = self.compare(source, device)
                self.assertTrue(result.recognized)
                self.assertFalse(result.membership_safe)
                self.assertEqual([row.local_id for row in result.retained], ['a'])
                self.assertEqual(result.removed, ())
                self.assertEqual(result.basis, 'name-linked-members')
                self.assertEqual(result.binding.members, (('a',101),))

    def test_partial_playlist_link_survives_rename_without_claiming_foreign_occurrences(self):
        first=self.compare(self.source(('a','new')),self.device((101,999)))
        self.assertTrue(first.recognized)
        self.assertEqual(first.binding.members,(('a',101),))
        later=self.compare(replace(self.source(('a','new','later')),name='Renamed'),
            self.device((101,999)),bindings=(first.binding,))
        self.assertTrue(later.recognized)
        self.assertEqual(later.basis,'binding')
        self.assertFalse(later.membership_safe)
        self.assertEqual(later.unknown_device_ids,(999,))
        self.assertEqual(later.removed,())

    def test_count_different_without_any_safe_selected_member_does_not_bootstrap(self):
        self.assertFalse(self.compare(self.source(('new', 'another', 'third')),
                                      self.device((101, 999))).recognized)

    def test_empty_device_name_only_match_cannot_bootstrap_new_source_members(self):
        self.assertFalse(self.compare(device=self.device(())).recognized)

    def test_reused_source_id_or_changed_native_uuid_cannot_rebind_by_name(self):
        for changes in ({'native_id': 'another-native-id'}, {'uuid': 'replacement-uuid'}):
            with self.subTest(changes=changes):
                result = self.compare(replace(self.source(), **changes), bindings=(self.binding(),))
                self.assertFalse(result.recognized)
                self.assertIn('identity changed', result.detail)

    def test_missing_bound_device_id_does_not_reattach_to_same_name_replacement(self):
        result = self.compare(device=self.device(playlist_id=999), bindings=(self.binding(),))
        self.assertFalse(result.recognized)
        self.assertIn('missing', result.detail)

    def test_duplicate_names_or_duplicate_device_ids_do_not_pick_a_candidate(self):
        for devices in ([self.device(), self.device(playlist_id=902)],
                        [self.device(), self.device(name='Other')]):
            with self.subTest(devices=devices):
                result = compare_playlist_bindings([self.source()], devices, track_links={'a': 101, 'b': 102})[0]
                self.assertFalse(result.recognized)

    def test_count_does_not_override_a_normalized_name_collision(self):
        sources = [self.source(('a',)), self.source(key='second', native_id='second', uuid='second')]
        devices = [self.device((101,)), self.device(playlist_id=902)]
        result = compare_playlist_bindings(sources, devices, track_links={'a': 101, 'b': 102})
        self.assertTrue(all(not row.recognized for row in result))

    def test_name_normalization_handles_case_whitespace_and_canonical_unicode(self):
        self.assertEqual(normalize_playlist_name('  FAVORITES  '), 'favorites')
        self.assertEqual(normalize_playlist_name(' CAFE\u0301 '), normalize_playlist_name('Caf\u00e9'))
        for source_name, device_name in (('FAVORITES', 'Favorites'), (' Favorites ', 'favorites'),
                                         ('CAFE\u0301', 'Caf\u00e9')):
            with self.subTest(source_name=source_name):
                result = self.compare(self.source(name=source_name), self.device(name=device_name))
                self.assertTrue(result.recognized)
                self.assertTrue(result.membership_safe)
                self.assertEqual(result.device_playlist_id, 901)

    def test_partial_recognition_uses_the_same_normalized_name_rule(self):
        result = self.compare(self.source(('a', 'new', 'other-new'), name=' FAVORITES '),
                              self.device((101, 999), name='Favorites'))
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.unknown_device_ids, (999,))
        self.assertEqual([row.local_id for row in result.retained], ['a'])

    def test_bootstrap_rejects_normalized_device_collision_even_when_one_name_and_count_are_exact(self):
        devices = [self.device(), self.device((101,), playlist_id=902, name=' FAVORITES ')]
        result = compare_playlist_bindings([self.source()], devices, track_links={'a': 101, 'b': 102})[0]
        self.assertFalse(result.recognized)
        self.assertIn('ambiguous', result.detail)

    def test_bootstrap_rejects_normalized_source_collision_even_when_one_name_and_count_are_exact(self):
        sources = [self.source(), self.source(('a',), key='second', native_id='second',
                                              uuid='second', name=' favorites ')]
        result = compare_playlist_bindings(sources, [self.device()], track_links={'a': 101, 'b': 102})
        self.assertTrue(all(not row.recognized for row in result))

    def test_saved_binding_is_stronger_than_ambiguous_name_variants(self):
        devices = [self.device(), self.device(playlist_id=902, name=' FAVORITES ')]
        result = compare_playlist_bindings([self.source()], devices, track_links={'a': 101, 'b': 102},
                                           bindings=(self.binding(),))[0]
        self.assertEqual(result.device_playlist_id, 901)
        self.assertEqual(result.basis, 'binding')
        self.assertTrue(result.membership_safe)

    def test_existing_other_source_binding_prevents_name_based_takeover(self):
        result = self.compare(bindings=(self.binding(key='other-source'),))
        self.assertFalse(result.recognized)
        self.assertIn('another', result.detail)

    def test_conflicting_or_duplicate_saved_playlist_bindings_are_rejected(self):
        for bindings in ((self.binding(), self.binding()),
                         (self.binding(), self.binding(key='other-source'))):
            with self.subTest(bindings=bindings):
                self.assertFalse(self.compare(bindings=bindings).recognized)

    def test_duplicate_source_keys_are_not_silently_coalesced(self):
        rows = compare_playlist_bindings([self.source(), self.source()], [self.device()],
                                         track_links={'a': 101, 'b': 102})
        self.assertTrue(all(not row.recognized for row in rows))

    def test_distinct_source_aliases_cannot_claim_one_device_song_by_ordinal(self):
        result = self.compare(links={'a': 101, 'b': 101})
        self.assertTrue(result.recognized)
        self.assertFalse(result.membership_safe)
        self.assertEqual(result.ambiguous_source_ids, ('a', 'b'))
        self.assertEqual(result.unknown_device_ids, (101, 102))
        self.assertEqual(result.retained, ())

    def test_source_adapter_captures_actual_native_uuid_and_effective_members_without_io(self):
        playlist = Playlist('Favorites', [], ['a', 'b', 'a'], pid='native-favorites', added_keys=['c'])
        tracks = {key: Track(key, key, 'Artist', f'file:///unavailable/{key}.mp3') for key in ('a', 'b', 'c')}
        library = Library(tracks, [playlist], '/unavailable/master.db', source_kind='rekordbox-db',
            hidden_ids={'b'}, source_playlist_catalog={'nodes': [dict(ID=playlist.pid,
                UUID='actual-native-uuid', rb_local_deleted=0)], 'members': [], 'revision': 'cached'})
        with mock.patch.object(Path, 'stat', side_effect=AssertionError('source stat')), \
                mock.patch('builtins.open', side_effect=AssertionError('source read')):
            frozen = source_playlist_identity(library, playlist)
        self.assertEqual(frozen.uuid, 'actual-native-uuid')
        self.assertEqual(frozen.members, ('a', 'a', 'c'))
        playlist.keys.clear()
        self.assertEqual(frozen.members, ('a', 'a', 'c'))
        library.source_playlist_catalog['nodes'].append(library.source_playlist_catalog['nodes'][0])
        self.assertTrue(source_playlist_identity(library, playlist).identity_error)

    def test_witness_decoder_preserves_duplicates_and_refuses_ambiguous_or_malformed_identity(self):
        saved = self.binding((('a', 101), ('a', 101), ('b', 102)))
        raw = json.loads(json.dumps(asdict(saved)))
        self.assertEqual(playlist_binding_from_dict(raw), saved)
        for changed in ({'device_playlist_id': True}, {'members': [['a', 0]]},
                        {'members': [['a', 101], ['b', 101]]},
                        {'members': [['a', 101], ['a', 102]]}, {'extra': 'unknown'}):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    playlist_binding_from_dict({**raw, **changed})


if __name__ == '__main__':
    unittest.main()
