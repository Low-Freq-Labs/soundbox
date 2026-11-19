"""Exact rendered labels and deliberate omissions are advisory export baselines."""
from dataclasses import asdict, replace
import hashlib
from pathlib import Path
import unittest
from unittest import mock
from uuid import uuid4

from soundbox import stock_ipod
from soundbox.ipod_export_witness import build_export_witness, rebase_export_witness, verify_export_witness
from soundbox.library import Playlist, Track
from soundbox.stock_ipod_catalog import compare_stock_catalog
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_playlist_binding import (
    DevicePlaylist, PlaylistBinding, SourcePlaylist, compare_playlist_bindings,
    playlist_binding_from_dict,
)
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from tests import test_stock_ipod as fixture


def identity(local):
    return local, 'native-' + local, 'uuid-' + local, '/source/' + local, '/audio/' + local


class ExportPlaylistBaseline(unittest.TestCase):
    def baseline(self, total=66, included=33):
        members = tuple('song-' + str(i) for i in range(total))
        source = SourcePlaylist('dnb', 'native-dnb', 'uuid-dnb', 'DNB', members,
            label='GENRES / DNB', identities=tuple(map(identity, members)))
        links = {local: 1000 + i for i, local in enumerate(members[:included])}
        device = DevicePlaylist(900, 'GENRES / DNB', tuple(links.values()))
        binding = PlaylistBinding(source.key, source.native_id, source.uuid,
            device.playlist_id, device.name, tuple(links.items()), source.label, source.identities)
        return source, device, binding, links

    def compare(self, source, device, binding, links):
        with mock.patch.object(Path, 'stat', side_effect=AssertionError('Pure delta touched media')), \
                mock.patch.object(Path, 'open', side_effect=AssertionError('Pure delta opened media')):
            return compare_playlist_bindings([source], [device], track_links=links, bindings=[binding])[0]

    def test_33_of_66_and_1_of_83_intentional_exports_are_clean_without_filtering_source(self):
        for total, included in ((66, 33), (83, 1)):
            with self.subTest(total=total, included=included):
                source, device, binding, links = self.baseline(total, included)
                row = self.compare(source, device, binding, links)
                self.assertTrue(row.recognized); self.assertTrue(row.membership_safe)
                self.assertFalse(row.needs_sync); self.assertTrue(binding.has_omissions)
                self.assertEqual((row.added, row.removed, row.unmapped_source_ids), ((), (), ()))
                self.assertEqual(len(row.retained), included)
                self.assertEqual(len(row.source.members), total)
                self.assertEqual(row.desired_name, 'GENRES / DNB')

    def test_new_rebound_native_and_actual_path_occurrences_are_not_hidden_as_old_omissions(self):
        source, device, binding, links = self.baseline()
        added = 'new-song'
        current = replace(source, members=(*source.members, added), identities=(*source.identities, identity(added)))
        row = self.compare(current, device, binding, links)
        self.assertEqual([(x.local_id, x.source_index) for x in row.added], [(added, 66)])
        self.assertTrue(row.needs_sync)
        for field in (1, 2, 3, 4):
            with self.subTest(field=field):
                changed = list(source.identities[50]); changed[field] += '-changed'
                identities = (*source.identities[:50], tuple(changed), *source.identities[51:])
                row = self.compare(replace(source, identities=identities), device, binding, links)
                self.assertEqual([(x.local_id, x.source_index) for x in row.added], [('song-50', 50)])
                self.assertTrue(row.needs_sync)

    def test_new_duplicate_omitted_occurrence_is_a_new_intention(self):
        source, device, binding, links = self.baseline(2, 1)
        source = replace(source, members=(*source.members, 'song-1'),
                         identities=(*source.identities, identity('song-1')))
        binding = replace(binding, source_members=source.identities)
        self.assertFalse(self.compare(source, device, binding, links).needs_sync)
        changed = replace(source, members=(*source.members, 'song-1'),
                          identities=(*source.identities, identity('song-1')))
        row = self.compare(changed, device, binding, links)
        self.assertEqual([(x.local_id, x.source_index) for x in row.added], [('song-1', 3)])

    def test_an_omitted_local_id_does_not_authorize_an_extra_device_occurrence(self):
        source, device, binding, links = self.baseline(2, 1)
        row = self.compare(source, replace(device, members=(*device.members, 777)), binding,
                           {**links, 'song-1': 777})
        self.assertTrue(row.needs_sync)
        self.assertFalse(row.membership_safe)
        self.assertEqual(row.unexpected_device_ids, (777,))

    def test_actual_label_stays_until_genuine_source_rename_or_folder_move(self):
        source, device, binding, links = self.baseline()
        for current in (source, replace(source, label='GENRES / DNB')):
            row = self.compare(current, device, binding, links)
            self.assertFalse(row.renamed); self.assertEqual(row.desired_name, device.name)
        for name, label in (('DNB', 'SETS / DNB'), ('New DNB', 'GENRES / New DNB')):
            row = self.compare(replace(source, name=name, label=label), device, binding, links)
            self.assertTrue(row.renamed); self.assertEqual(row.desired_name, label)
        row = self.compare(source, replace(device, name='external rename'), binding, links)
        self.assertTrue(row.renamed); self.assertEqual(row.desired_name, device.name)

    def test_legacy_decode_keeps_old_name_behavior_without_inventing_source_baseline(self):
        source, device, binding, links = self.baseline(2, 2)
        raw = asdict(binding); del raw['source_label']; del raw['source_members']
        legacy = playlist_binding_from_dict(raw)
        self.assertEqual((legacy.source_label, legacy.source_members), ('', ()))
        row = self.compare(source, device, legacy, links)
        self.assertTrue(row.renamed); self.assertEqual(row.desired_name, source.name)
        self.assertEqual(playlist_binding_from_dict(asdict(binding)), binding)

    def test_ambiguous_or_incomplete_occurrence_baselines_and_changed_playlist_uuid_refuse(self):
        source, device, binding, links = self.baseline(2, 1)
        raw = asdict(binding)
        for members in ((identity('other'),), (identity('song-0'), (*identity('song-0')[:2], 'different', *identity('song-0')[3:]))):
            with self.subTest(members=members), self.assertRaises(ValueError):
                playlist_binding_from_dict({**raw, 'source_members': members})
        row = self.compare(replace(source, uuid='replacement-playlist'), device, binding, links)
        self.assertFalse(row.recognized)
        self.assertIn('identity changed', row.detail)


class ActualExportPlaylistBaseline(unittest.TestCase):
    def setUp(self):
        self.f = fixture.StockIpodSync(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f
        f.library.source = f.sources / 'generated-native.db'
        f.library.source_kind = 'rekordbox-db'; f.library.source_library_id = 'generated-source'
        f.playlist.name = 'DNB'; f.playlist.folder_path[:] = ['GENRES']
        f.library.source_playlist_catalog = {'nodes': [dict(ID=f.playlist.pid, UUID=str(uuid4()), rb_local_deleted=0)]}
        for local, track in f.library.tracks_by_id.items():
            track.source_native_id = 'native-' + local; track.source_uuid = str(uuid4())
            track.source_native_path = str(track.path)
        self.key = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume').identity_key
        self.shadow = StockIpodShadowStore(f.base / 'shadow')

    def rows(self):
        return {local: dict(title=t.name, artist=t.artist) for local, t in self.f.library.tracks_by_id.items()}

    def plan(self):
        f = self.f
        return stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.key, shadow_store=self.shadow, display_rows=self.rows())

    def compare(self, result):
        f = self.f; snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        return compare_stock_catalog(f.library, [f.playlist], snapshot, display_rows=self.rows(),
                                     catalog_witness=result.catalog_witness, incremental=True)

    def test_real_folder_export_readback_seeds_exact_bound_clean_catalog_sync(self):
        result = stock_ipod.commit_stock_ipod_sync(self.plan())
        self.assertIsNotNone(result.catalog_witness)
        binding, = result.catalog_witness.bindings
        self.assertEqual((binding.name, binding.source_label), ('GENRES / DNB', 'GENRES / DNB'))
        self.assertEqual(binding.uuid, self.f.library.source_playlist_catalog['nodes'][0]['UUID'])
        self.assertFalse(binding.has_omissions)
        self.assertFalse(self.compare(result).needs_sync)
        self.assertFalse(result.catalog_warning)
        self.assertFalse(result.catalog_notice)

    def test_real_bound_folder_move_updates_only_label_without_audio_or_manual_playlist_drift(self):
        f = self.f; first = stock_ipod.commit_stock_ipod_sync(self.plan())
        snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Retained audio reacquired')):
            clean = stock_ipod.plan_stock_ipod_catalog_update(f.library, [f.playlist], f.ipod,
                snapshot=snapshot, model_number='MA446', filesystem='msdos', usb_family='iPod Video',
                display_rows=self.rows(), catalog_witness=first.catalog_witness, incremental=True)
        self.assertFalse(clean.requires_commit)
        manual = next(row for row in snapshot.playlists if row.title == 'Keep Me')
        bound = next(row for row in snapshot.playlists if row.title == 'GENRES / DNB')
        f.playlist.folder_path[:] = ['SETS']
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Folder move reacquired audio')):
            changed = stock_ipod.plan_stock_ipod_catalog_update(f.library, [f.playlist], f.ipod,
                snapshot=snapshot, model_number='MA446', filesystem='msdos', usb_family='iPod Video',
                display_rows=self.rows(), catalog_witness=first.catalog_witness, incremental=True)
        self.assertTrue(changed.requires_commit); self.assertEqual(changed.copies, ())
        second = stock_ipod.commit_stock_ipod_sync(changed)
        updated = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        self.assertEqual(next(row for row in updated.playlists if row.playlist_id == bound.playlist_id).title,
                         'SETS / DNB')
        self.assertEqual(next(row for row in updated.playlists if row.playlist_id == manual.playlist_id), manual)
        self.assertFalse(self.compare(second).needs_sync)

    def test_real_omission_then_same_path_supported_audio_is_reevaluated_by_explicit_full_plan(self):
        f = self.f; omitted = f.sources / 'omitted.wav'; fixture._wave24(omitted, 441)
        f.library.tracks_by_id['omitted'] = Track('omitted', 'Deliberately skipped', '', omitted.as_uri(),
            source_native_id='native-omitted', source_uuid=str(uuid4()), source_native_path=str(omitted))
        f.playlist.keys[:] = ['new', 'omitted']
        first_plan = self.plan(); self.assertEqual([x.track_id for x in first_plan.omissions], ['omitted'])
        first = stock_ipod.commit_stock_ipod_sync(first_plan)
        binding, = first.catalog_witness.bindings
        self.assertTrue(binding.has_omissions)
        self.assertEqual((len(binding.source_members), len(binding.members)), (2, 1))
        self.assertFalse(self.compare(first).needs_sync)
        # A cached badge is not an audio certificate. Root routes this binding's
        # explicit Sync through the full planner, which must inspect it afresh.
        fixture._wave(omitted, 441, 1000); current = omitted.read_bytes()
        acquire = stock_ipod.acquire_source; captured = []
        def tracked(contract, *args, **kwargs):
            captured.append(contract.canonical)
            return acquire(contract, *args, **kwargs)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=tracked):
            second_plan = self.plan()
        self.assertEqual(captured, [omitted.resolve()])
        self.assertEqual(len(second_plan.copies), 1)
        self.assertEqual(second_plan.copies[0].source, omitted.resolve())
        self.assertEqual(second_plan.omissions, ())
        second = stock_ipod.commit_stock_ipod_sync(second_plan)
        self.assertEqual(omitted.read_bytes(), current)
        self.assertFalse(second.catalog_witness.bindings[0].has_omissions)
        self.assertFalse(self.compare(second).needs_sync)


class SuccessiveExportWitnesses(unittest.TestCase):
    rows = ActualExportPlaylistBaseline.rows

    def setUp(self):
        ActualExportPlaylistBaseline.setUp(self)
        f = self.f
        f.playlist.keys[:] = ['new']
        self.favorites = Playlist('Favorites', [], ['old', 'new'])
        self.live = Playlist('Live Sets', [], ['old'])
        self.available = (self.favorites, self.live)

    def export(self, selected, previous=None, *, artwork=False):
        f = self.f
        self.last_plan = stock_ipod.plan_stock_ipod_sync(
            f.library, selected, f.ipod, model_number='MA446', filesystem='msdos',
            usb_family='iPod Video', identity_key=self.key, shadow_store=self.shadow,
            display_rows=self.rows(), catalog_witness=previous, extra_playlists=self.available,
            art_cache=f.art_cache if artwork else None,
            stock_art_cache=f.stock_art_cache if artwork else None)
        return stock_ipod.commit_stock_ipod_sync(self.last_plan)

    def renew(self, witness, before, rendered=None, *, previous_art='', artwork='', preserved=frozenset()):
        return build_export_witness(self.f.library, (), (), device_key=self.key,
            rendered=before if rendered is None else rendered, artwork_sha256=artwork,
            track_dbids={}, artwork_keys={}, previous_witness=witness,
            previous_database=before, previous_artwork_sha256=previous_art,
            available_playlists=self.available, preserved_artwork_dbids=preserved)

    def assert_clean(self, result, selected):
        snapshot = read_stock_ipod_snapshot(self.f.ipod, volume_uuid='generated-volume')
        comparison = compare_stock_catalog(self.f.library, selected, snapshot, display_rows=self.rows(),
            catalog_witness=result.catalog_witness, incremental=True)
        self.assertFalse(comparison.needs_sync, repr(comparison))
        self.assertTrue(all(row.recognized for row in comparison.playlist_changes))

    def test_separate_exports_preserve_exact_prior_custom_native_and_shared_memberships(self):
        first = self.export([self.f.playlist])
        second = self.export([self.favorites], first.catalog_witness)
        third = self.export([self.live], second.catalog_witness)
        self.assertEqual(len(third.catalog_witness.bindings), 3)
        self.assertEqual({row.local_id for row in third.catalog_witness.rows}, {'old', 'new'})
        self.assertFalse(third.catalog_notice); self.assertFalse(third.catalog_warning)
        self.assert_clean(third, [self.f.playlist, self.favorites, self.live])
        # New selected metadata supersedes a shared row, without losing the
        # other playlist's exact same-local/same-native correspondence.
        self.f.library.tracks_by_id['new'].name = 'New current title'
        fourth = self.export([self.favorites], third.catalog_witness)
        self.assertEqual(len(fourth.catalog_witness.bindings), 3)
        self.assert_clean(fourth, [self.f.playlist, self.favorites, self.live])

    def test_previous_wrong_source_device_or_preplan_catalog_is_not_blessed(self):
        first = self.export([self.f.playlist])
        database = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        for witness in (replace(first.catalog_witness, source=('/other/source', 'rekordbox-db', 'other')),
                        replace(first.catalog_witness, device_key='other-device'),
                        replace(first.catalog_witness, database_sha256='0' * 64),
                        replace(first.catalog_witness, artwork_database_sha256='1' * 64)):
            with self.subTest(witness=witness):
                renewed = self.renew(witness, database)
                self.assertEqual((renewed.witness.rows, renewed.witness.bindings), ((), ()))
                self.assertTrue(renewed.notice); self.assertFalse(renewed.warning)

    def test_missing_or_changed_current_native_uuid_drops_only_that_binding(self):
        first = self.export([self.f.playlist])
        second = self.export([self.favorites], first.catalog_witness)
        database = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        for nodes in ([], [dict(ID=self.f.playlist.pid, UUID='replacement', rb_local_deleted=0)]):
            with self.subTest(nodes=nodes):
                original = self.f.library.source_playlist_catalog
                self.f.library.source_playlist_catalog = {'nodes': nodes}
                try:
                    renewed = self.renew(second.catalog_witness, database)
                finally:
                    self.f.library.source_playlist_catalog = original
                self.assertEqual({binding.key for binding in renewed.witness.bindings}, {'cust:Favorites'})
                self.assertTrue(renewed.notice)

    def test_rebound_selected_song_cannot_relabel_a_prior_shared_binding(self):
        first = self.export([self.f.playlist])
        self.f.library.tracks_by_id['new'].source_uuid = 'replacement-song'
        second = self.export([self.favorites], first.catalog_witness)
        self.assertEqual([binding.key for binding in second.catalog_witness.bindings], ['cust:Favorites'])
        self.assertTrue(second.catalog_notice)

    def test_later_unselected_source_edits_remain_drift_instead_of_becoming_a_new_baseline(self):
        first = self.export([self.f.playlist])
        second = self.export([self.favorites], first.catalog_witness)
        baseline = next(binding for binding in second.catalog_witness.bindings if binding.key == 'cust:Favorites')
        self.favorites.keys[:] = ['old']
        third = self.export([self.live], second.catalog_witness)
        retained = next(binding for binding in third.catalog_witness.bindings if binding.key == 'cust:Favorites')
        self.assertEqual(retained, baseline)
        snapshot = read_stock_ipod_snapshot(self.f.ipod, volume_uuid='generated-volume')
        comparison = compare_stock_catalog(self.f.library, [self.favorites], snapshot,
            display_rows=self.rows(), catalog_witness=third.catalog_witness, incremental=True)
        self.assertTrue(comparison.needs_sync)
        self.assertEqual([row.local_id for row in comparison.playlist_changes[0].removed], ['new'])

    def test_selected_same_file_aliases_keep_only_unaffected_proven_prior_links(self):
        first = self.export([self.f.playlist])
        original = self.f.library.tracks_by_id['old']
        self.f.library.tracks_by_id['alias-old'] = replace(original, track_id='alias-old',
            source_native_id='native-alias-old', source_uuid='uuid-alias-old')
        aliases = Playlist('Aliases', [], ['old', 'alias-old'])
        second = self.export([aliases], first.catalog_witness)
        self.assertEqual([binding.key for binding in second.catalog_witness.bindings], [self.f.playlist.pid])
        self.assertEqual([row.local_id for row in second.catalog_witness.rows], ['new'])
        self.assertTrue(second.catalog_notice); self.assertFalse(second.catalog_warning)
        self.assert_clean(second, [self.f.playlist])

    def test_art_keys_survive_only_exact_global_or_renderer_preserved_pixel_proof(self):
        first = self.export([self.f.playlist], artwork=True)
        database = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        old = first.catalog_witness
        self.assertTrue(old.rows[0].artwork_key)
        changed_art = hashlib.sha256(b'different captured artwork').hexdigest()
        unproven = self.renew(old, database, previous_art=old.artwork_database_sha256, artwork=changed_art)
        self.assertEqual(unproven.witness.rows[0].artwork_key, '')
        self.assertTrue(unproven.notice)
        retained = self.renew(old, database, previous_art=old.artwork_database_sha256,
                              artwork=changed_art, preserved=frozenset(row.dbid for row in old.rows))
        self.assertEqual(retained.witness.rows, old.rows)
        self.assertFalse(retained.notice)
        verify_export_witness(retained.witness, database, changed_art)

    def test_actual_delete_rebases_survivors_without_source_and_reexport_restores_only_deleted_binding(self):
        first = self.export([self.f.playlist])
        second = self.export([self.favorites], first.catalog_witness)
        third = self.export([self.live], second.catalog_witness)
        before = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        dnb = next(binding for binding in third.catalog_witness.bindings if binding.key == self.f.playlist.pid)
        deletion = stock_ipod.plan_stock_ipod_playlist_delete(self.f.ipod, [dnb.device_playlist_id],
            model_number='MA446', filesystem='msdos', usb_family='iPod Video')
        stock_ipod.commit_stock_ipod_playlist_delete(deletion)
        after = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        # Device-only deletion neither opens nor needs the source library.
        with mock.patch.object(Path, 'open', side_effect=AssertionError('Rebase opened a file')):
            renewed = rebase_export_witness(third.catalog_witness, device_key=self.key,
                previous_database=before, previous_artwork_sha256='', rendered=after, artwork_sha256='')
        self.assertFalse(renewed.notice); self.assertFalse(renewed.warning)
        self.assertEqual({binding.key for binding in renewed.witness.bindings}, {'cust:Favorites', 'cust:Live Sets'})
        self.assertEqual(renewed.witness.bindings,
            tuple(binding for binding in third.catalog_witness.bindings if binding.key != self.f.playlist.pid))
        fourth = self.export([self.f.playlist], renewed.witness)
        self.assertEqual(self.last_plan.copies, ())
        self.assertEqual(len(fourth.catalog_witness.bindings), 3)
        self.assertFalse(fourth.catalog_notice)
        self.assert_clean(fourth, [self.f.playlist, self.favorites, self.live])

    def test_device_rebase_does_not_turn_changed_surviving_membership_into_an_omission(self):
        first = self.export([self.f.playlist])
        second = self.export([self.favorites], first.catalog_witness)
        before = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        self.favorites.keys[:] = ['old']
        self.export([self.favorites], second.catalog_witness)
        after = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        renewed = rebase_export_witness(second.catalog_witness, device_key=self.key,
            previous_database=before, previous_artwork_sha256='', rendered=after, artwork_sha256='')
        self.assertEqual([binding.key for binding in renewed.witness.bindings], [self.f.playlist.pid])
        self.assertTrue(renewed.notice)
        self.assertEqual(renewed.witness.bindings[0].source_members,
                         second.catalog_witness.bindings[0].source_members)

    def test_device_rebase_refuses_wrong_catalog_device_and_unproved_artwork(self):
        first = self.export([self.f.playlist], artwork=True)
        old = first.catalog_witness
        database = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        for device, before, art in (('different-device', database, old.artwork_database_sha256),
                                    (self.key, database + b'changed', old.artwork_database_sha256),
                                    (self.key, database, '0' * 64)):
            with self.subTest(device=device, art=art):
                result = rebase_export_witness(old, device_key=device, previous_database=before,
                    previous_artwork_sha256=art, rendered=database, artwork_sha256=old.artwork_database_sha256)
                self.assertIsNone(result.witness); self.assertTrue(result.notice)
        changed_art = hashlib.sha256(b'new artwork').hexdigest()
        result = rebase_export_witness(old, device_key=self.key, previous_database=database,
            previous_artwork_sha256=old.artwork_database_sha256, rendered=database, artwork_sha256=changed_art)
        self.assertEqual(result.witness.rows[0].artwork_key, '')
        self.assertTrue(result.notice)

    def test_actual_new_artwork_retains_prior_unprepared_pixel_keys_and_all_three_clean_bindings(self):
        f = self.f
        path = f.sources / 'third.wav'; fixture._wave(path, 441, 3333)
        f.library.tracks_by_id['third'] = Track('third', 'Third song', 'Fixture Artist', path.as_uri(),
            source_native_id='native-third', source_uuid=str(uuid4()), source_native_path=str(path))
        cover = f.base / 'distinct.jpg'
        fixture.Image.new('RGB', (300, 300), (20, 200, 70)).save(cover, 'JPEG', quality=90)
        f.art_cache.paths['third'] = cover
        self.favorites.keys[:] = ['old']
        self.live.keys[:] = ['third']
        first = self.export([f.playlist], artwork=True)
        second = self.export([self.favorites], first.catalog_witness, artwork=True)
        prior_keys = {row.local_id: row.artwork_key for row in second.catalog_witness.rows}
        third = self.export([self.live], second.catalog_witness, artwork=True)
        self.assertNotEqual(third.catalog_witness.artwork_database_sha256,
                            second.catalog_witness.artwork_database_sha256)
        self.assertEqual({row.local_id: row.artwork_key for row in third.catalog_witness.rows
                          if row.local_id in prior_keys}, prior_keys)
        self.assertTrue(all(prior_keys.values()))
        self.assertEqual(len(third.catalog_witness.bindings), 3)
        self.assertFalse(third.catalog_notice); self.assertFalse(third.catalog_warning)
        snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume',
            native_capture=stock_ipod.capture_stock_ipod_native(f.ipod, include_artwork=True))
        current_keys = {local: f.stock_art_cache.prepare(image).cache_key for local, image in f.art_cache.paths.items()}
        comparison = compare_stock_catalog(f.library, [f.playlist, self.favorites, self.live], snapshot,
            display_rows=self.rows(), catalog_witness=third.catalog_witness,
            artwork_keys=current_keys, incremental=True)
        self.assertFalse(comparison.needs_sync, repr(comparison))
        self.assertEqual(comparison.artwork_track_ids, ())
