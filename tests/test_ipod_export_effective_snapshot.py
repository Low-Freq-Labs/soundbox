"""Generated iPod exports consume one durable effective musical generation."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import struct
import unittest
from unittest import mock

from clickwheel.ipod import get_ipod_tracks, parse_itunesdb, read_ipod
from PIL import Image

from soundbox import engine, manifest, stock_ipod, sync
from soundbox.dual_ipod import DualIpodError, commit_dual_ipod_sync, composite_playlist_entry
from soundbox.export_snapshot import capture_export
from soundbox.ipod_export_witness import stored_metadata, verify_export_witness
from soundbox.library import Playlist, Track
from soundbox.rockbox_media import _display_tags
from soundbox.stock_ipod_catalog import catalog_metadata, compare_stock_catalog
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.stock_ipod_signing import StockSigningContext, sign_stock_database, verified_stock_signing_context
from tests import test_stock_ipod as stock_fixture
from tests import test_dual_ipod as dual_fixture


def _rows(keys, *, suffix='captured'):
    return {key: dict(title=f'{key} {suffix}', artist=f'{suffix} Artist',
        album=f'{suffix} Album', genre='Dubstep', comments='durable snapshot',
        year=2026, bpm=145) for key in keys}


def _native(root):
    return get_ipod_tracks(read_ipod(str(root)))


class EffectiveIpodExport(unittest.TestCase):
    def setUp(self):
        self.fixture = stock_fixture.StockIpodSync()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.library.source = f.sources / 'native.db'
        f.library.source_kind = 'rekordbox-db'
        f.library.source_library_id = 'generated-native-library'
        f.playlist.name = 'Favorites'
        f.library.source_playlist_catalog = {'nodes': [dict(ID=f.playlist.pid,
            UUID='native-favorites-uuid', rb_local_deleted=0)]}
        for key, track in f.library.tracks_by_id.items():
            track.source_native_id = f'native-{key}'
            track.source_uuid = f'uuid-{key}'
            track.source_native_path = str(track.path)
        self.identity = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume').identity_key
        self.shadow = StockIpodShadowStore(f.base / 'shadow')
        self.original_audio = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in (f.existing_source, f.new_source)}

    def _capture(self, rows):
        f = self.fixture
        return capture_export(f.library, [f.playlist], complete=True,
            playlist_identities=True, display_rows=rows).materialize()

    def _plan(self, inputs):
        f = self.fixture
        return stock_ipod.plan_stock_ipod_sync(inputs.library, list(inputs.playlists), f.ipod,
            model_number='MA446', filesystem='msdos', art_cache=f.art_cache,
            stock_art_cache=f.stock_art_cache, shadow_store=self.shadow,
            identity_key=self.identity, display_rows=inputs.display_rows, usb_family='iPod Video')

    def _assert_source_audio(self):
        self.assertEqual(self.original_audio,
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.original_audio})

    def test_frozen_seven_fields_survive_raw_and_gui_changes_and_reopen(self):
        f = self.fixture; desired = _rows(('old', 'new'))
        # Raw native refresh and effective migration intentionally disagree.
        f.library.tracks_by_id['old'].name = 'partially acknowledged raw title'
        f.library.tracks_by_id['old'].artist = 'partially acknowledged artist'
        inputs = self._capture(desired)
        legacy = self._plan(replace(inputs, display_rows=None))
        self.assertNotIn('old captured', {row['title'] for row in get_ipod_tracks(
            parse_itunesdb(io.BytesIO(legacy.rendered_database)))})
        desired['old']['title'] = 'newer edit after capture'
        f.library.tracks_by_id['new'].name = 'later raw refresh'
        plan = self._plan(inputs)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        output = {row['title']: row for row in _native(f.ipod)}
        for key in ('old', 'new'):
            self.assertEqual(dict(stored_metadata(output[f'{key} captured'])),
                             _rows((key,))[key])
        witness = result.catalog_witness
        self.assertIsNotNone(witness, result.catalog_warning)
        self.assertEqual({row.local_id for row in witness.rows}, {'old', 'new'})
        self.assertEqual(len(witness.bindings), 1)
        self.assertEqual(witness.bindings[0].uuid, 'native-favorites-uuid')
        self.assertEqual([key for key, _dbid in witness.bindings[0].members], ['old', 'new'])
        self.assertEqual(witness.database_sha256, hashlib.sha256(
            (f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()).hexdigest())
        old = next(row for row in witness.rows if row.local_id == 'old')
        self.assertEqual(old.dbid, 101)
        self.assertEqual(output['old captured']['location'], ':iPod_Control:Music:F00:AAAA.wav')
        self.assertEqual(output['old captured']['playCount'], 2)
        current = compare_stock_catalog(inputs.library, list(inputs.playlists),
            read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume'),
            display_rows=inputs.display_rows, catalog_witness=witness, incremental=True)
        self.assertFalse(current.needs_sync, current)
        self._assert_source_audio()

    def test_flat_witness_reconnect_accepts_new_count_name_and_metadata(self):
        f = self.fixture
        first = stock_ipod.commit_stock_ipod_sync(self._plan(self._capture(_rows(('old', 'new')))))
        self.assertIsNotNone(first.catalog_witness, first.catalog_warning)
        third = f.sources / 'third.wav'; stock_fixture._wave(third, 1_323, 300)
        f.library.tracks_by_id['third'] = Track('third', 'stale raw third', 'stale artist',
            third.as_uri(), source_native_id='native-third', source_uuid='uuid-third',
            source_native_path=str(third))
        f.playlist.keys[:] = ['new', 'third', 'old']; f.playlist.name = 'Favorites updated'
        latest = self._capture(_rows(('old', 'new', 'third'), suffix='latest'))
        snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        comparison = compare_stock_catalog(latest.library, list(latest.playlists), snapshot,
            display_rows=latest.display_rows, catalog_witness=first.catalog_witness, incremental=True)
        self.assertTrue(comparison.needs_sync)
        self.assertTrue(comparison.playlist_changes[0].recognized)
        self.assertEqual([row.local_id for row in comparison.playlist_changes[0].added], ['third'])
        plan = stock_ipod.plan_stock_ipod_catalog_update(latest.library, list(latest.playlists), f.ipod,
            snapshot=snapshot, model_number='MA446', filesystem='msdos', update_metadata=True,
            display_rows=latest.display_rows, catalog_witness=first.catalog_witness, incremental=True,
            art_cache=f.art_cache, stock_art_cache=f.stock_art_cache, usb_family='iPod Video')
        self.assertEqual(len(plan.copies), 1)
        second = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(second.database_sha256, plan.catalog_witness.database_sha256)
        verify_export_witness(plan.catalog_witness,
            (f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes(),
            stock_ipod.capture_stock_ipod_native(f.ipod, include_artwork=True).artwork_database_sha256)
        final = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        current = compare_stock_catalog(latest.library, list(latest.playlists), final,
            display_rows=latest.display_rows, catalog_witness=plan.catalog_witness, incremental=True)
        self.assertFalse(current.needs_sync, current)
        self.assertEqual({row['title'] for row in _native(f.ipod)},
                         {'old latest', 'new latest', 'third latest'})
        self._assert_source_audio()

    def test_identical_file_alias_exports_without_false_bijection(self):
        f = self.fixture
        f.library.tracks_by_id['alias'] = replace(f.library.tracks_by_id['new'], track_id='alias',
            source_native_id='native-alias', source_uuid='uuid-alias')
        f.art_cache.paths['alias'] = f.art_cache.paths['new']
        f.playlist.keys[:] = ['new', 'alias', 'new']
        desired = _rows(('new',)); desired['alias'] = dict(desired['new'])
        result = stock_ipod.commit_stock_ipod_sync(self._plan(self._capture(desired)))
        self.assertIsNone(result.catalog_witness)
        self.assertEqual(result.catalog_warning, '')
        self.assertIn('aliases', result.catalog_notice)
        snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
        favorites = next(row for row in snapshot.playlists if row.title == 'Favorites')
        self.assertEqual(len(favorites.entries), 3)
        self.assertEqual(len({row.dbid for row in favorites.entries}), 1)
        self._assert_source_audio()

    def test_conflicting_shared_file_metadata_refused_before_target_write(self):
        f = self.fixture
        f.library.tracks_by_id['alias'] = replace(f.library.tracks_by_id['new'], track_id='alias',
            source_native_id='native-alias', source_uuid='uuid-alias')
        f.playlist.keys[:] = ['new', 'alias']
        before = f._snapshot(f.ipod)
        for column in ('title', 'artist', 'album', 'genre', 'comments', 'year', 'bpm'):
            with self.subTest(column=column):
                desired = _rows(('new',)); desired['alias'] = dict(desired['new'])
                desired['alias'][column] = 146 if column == 'bpm' else (2025 if column == 'year' else 'different')
                with self.assertRaisesRegex(stock_ipod.StockIpodError, 'one audio file'):
                    self._plan(self._capture(desired))
                self.assertEqual(f._snapshot(f.ipod), before)

    def test_folder_names_preserved_and_next_full_sync_is_noop(self):
        f = self.fixture; f.playlist.folder_path[:] = ['Sets', 'Gym']
        inputs = self._capture(_rows(('old', 'new')))
        first = stock_ipod.commit_stock_ipod_sync(self._plan(inputs))
        self.assertIsNotNone(first.catalog_witness)
        self.assertEqual(len(first.catalog_witness.bindings), 1)
        self.assertEqual(first.catalog_warning, '')
        self.assertIn('Sets / Gym / Favorites', [row.title for row in
            read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume').playlists])
        second = self._plan(inputs)
        self.assertFalse(second.requires_commit)
        self.assertEqual(second.rendered_database, (f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes())

    def test_missing_optional_native_identity_is_notice_without_export_attention(self):
        f = self.fixture; f.library.source_playlist_catalog = {'nodes': []}
        inputs = self._capture(_rows(('old', 'new')))
        plan = self._plan(inputs)
        self.assertEqual(plan.catalog_warning, '')
        self.assertIn('native UUID', plan.catalog_notice)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(result.catalog_warning, '')
        self.assertEqual(result.catalog_notice, plan.catalog_notice)
        self.assertIsNotNone(result.catalog_witness)
        verify_export_witness(result.catalog_witness,
            (f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes(),
            stock_ipod.capture_stock_ipod_native(f.ipod, include_artwork=True).artwork_database_sha256)
        self._assert_source_audio()

    def test_actual_readback_required_and_failure_does_not_undo_committed_export(self):
        f = self.fixture; plan = self._plan(self._capture(_rows(('old', 'new'))))
        original = stock_ipod.capture_stock_ipod_native
        def readback(*args, **kwargs):
            captured = original(*args, **kwargs)
            changed = captured.database.replace('new captured'.encode('utf-16le'),
                                                'new outsider'.encode('utf-16le'))
            return replace(captured, database=changed)
        with mock.patch.object(stock_ipod, 'capture_stock_ipod_native', side_effect=readback):
            result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertIsNone(result.catalog_witness)
        self.assertIn('readback', result.catalog_warning)
        self.assertEqual({row['title'] for row in _native(f.ipod)}, {'old captured', 'new captured'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_export_witness(plan.catalog_witness, plan.rendered_database + b'x', '')
        self._assert_source_audio()

    def test_estimated_bpm_never_becomes_native_musical_authority(self):
        f = self.fixture; desired = _rows(('old', 'new'))
        for row in desired.values():
            row.update(bpm=177.73, bpm_estimated=True)
        inputs = self._capture(desired)
        self.assertNotIn('bpm', inputs.display_rows['new'])
        self.assertTrue(inputs.display_rows['new']['bpm_estimated'])
        result = stock_ipod.commit_stock_ipod_sync(self._plan(inputs))
        self.assertIsNotNone(result.catalog_witness)
        self.assertEqual({row['bpm'] for row in _native(f.ipod)}, {0})

    def test_bad_selected_projection_fails_without_validating_unprojected_migration(self):
        f = self.fixture
        f.library.tracks_by_id['unselected'] = replace(f.library.tracks_by_id['old'],
            track_id='unselected', year='invalid raw historical migration value')
        rows = _rows(('old', 'new'))
        self.assertIsNotNone(self._plan(self._capture(rows)))
        before = f._snapshot(f.ipod)
        rows['new']['year'] = 'NaN'
        with self.assertRaisesRegex(ValueError, 'numeric metadata'):
            self._capture(rows)
        self.assertEqual(f._snapshot(f.ipod), before)

    def test_complete_projection_never_rederives_pair_but_partial_rows_keep_policy(self):
        f = self.fixture; track = f.library.tracks_by_id['new']; rows = _rows(('new',))
        with mock.patch('soundbox.metadata.effective_artist', side_effect=AssertionError('Redundant artist derivation')), \
             mock.patch('soundbox.metadata.effective_title', side_effect=AssertionError('Redundant title derivation')):
            frozen = self._capture(rows)
            self.assertEqual(frozen.display_rows['new'], rows['new'])
            self.assertEqual(dict(catalog_metadata(track, f.library, display_rows=rows)), rows['new'])
            for invalid in ({**rows['new'], 'artist': None}, {**rows['new'], 'year': 'NaN'},
                            {**rows['new'], 'id': 'other'}):
                with self.subTest(invalid=tuple(invalid)), self.assertRaises(ValueError):
                    catalog_metadata(track, f.library, display_rows={'new': invalid})
        from soundbox import metadata
        with mock.patch.object(metadata, 'effective_artist', wraps=metadata.effective_artist) as artist, \
             mock.patch.object(metadata, 'effective_title', wraps=metadata.effective_title) as title:
            partial = dict(catalog_metadata(track, f.library, display_rows={'new': {'title': 'Only title'}}))
        self.assertEqual(partial['title'], 'Only title')
        self.assertEqual(partial['artist'], 'Fixture Artist')
        artist.assert_called_once(); title.assert_called_once()

    def test_signed_classic_full_capture_keeps_authentication_and_exact_metadata(self):
        f = self.fixture; guid = '0011223344556677'
        context = StockSigningContext(bytes.fromhex(guid), bytes(range(16)), bytes(range(12)))
        path = f.ipod / 'iPod_Control/iTunes/iTunesDB'
        raw = bytearray(path.read_bytes()); struct.pack_into('<I', raw, 0x10, 117)
        path.write_bytes(sign_stock_database(bytes(raw), context))
        inputs = self._capture(_rows(('old', 'new')))
        plan = stock_ipod.plan_stock_ipod_sync(inputs.library, list(inputs.playlists), f.ipod,
            model_number='MC297', filesystem='fat32', art_cache=f.art_cache,
            stock_art_cache=f.stock_art_cache, shadow_store=self.shadow,
            identity_key=self.identity, display_rows=inputs.display_rows,
            usb_family='iPod Classic', firewire_guid=guid)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(verified_stock_signing_context(path.read_bytes(), guid), context)
        self.assertIsNotNone(result.catalog_witness, result.catalog_warning)
        self.assertEqual({row['title'] for row in _native(f.ipod)}, {'old captured', 'new captured'})
        self._assert_source_audio()

    def test_discovered_model_keeps_exact_shadow_but_known_identity_conflicts_refuse(self):
        f = self.fixture
        stock_ipod.commit_stock_ipod_sync(self._plan(self._capture(_rows(('old', 'new')))))
        shadow = self.shadow.load(self.identity)
        captured = stock_ipod.capture_stock_ipod_native(f.ipod, include_artwork=True)
        before = f._snapshot(f.ipod)
        for old, observed, valid in (('', 'MC297', True), ('MC297', '', True),
                                     ('MC297', 'MC293', False)):
            with self.subTest(old=old,observed=observed):
                options = dict(root=f.ipod.resolve(strict=True), identity_key=self.identity, model=observed,
                    serial_number='same-serial', database_sha256=captured.database_sha256,
                    native_capture=captured)
                self.assertEqual(stock_ipod._shadow_matches_live(
                    replace(shadow, model=old, serial_number='same-serial'), **options)[0], valid)
                for changed in (dict(identity_key='other-device'), dict(serial_number='other-serial'),
                                dict(database_sha256='f'*64)):
                    self.assertFalse(stock_ipod._shadow_matches_live(
                        replace(shadow, model=old, serial_number='same-serial'),
                        **{**options, **changed})[0])
        self.assertEqual(f._snapshot(f.ipod), before)


class EffectiveDualExport(unittest.TestCase):
    def setUp(self):
        self.fixture = dual_fixture.DualIpodSyncTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.playlist.keys[:] = ['safe', 'unsafe']
        self.fixture.library.source = self.fixture.sources / 'native.db'

    def test_optional_identity_notice_survives_dual_commit_and_noop(self):
        f = self.fixture
        f.library.source_kind = 'rekordbox-db'
        f.library.source_playlist_catalog = {'nodes': []}
        rows = _rows(('safe', 'unsafe'))
        plan = f._plan(display_rows=rows)
        self.assertEqual(plan.stock.catalog_warning, '')
        self.assertIn('native UUID', plan.stock.catalog_notice)
        result = commit_dual_ipod_sync(plan)
        self.assertEqual(result.stock.catalog_warning, '')
        self.assertEqual(result.stock.catalog_notice, plan.stock.catalog_notice)
        following = f._plan(reconcile=True, display_rows=rows)
        self.assertFalse(following.requires_commit)
        noop = commit_dual_ipod_sync(following)
        self.assertEqual(noop.stock.catalog_warning, '')
        self.assertEqual(noop.stock.catalog_notice, following.stock.catalog_notice)

    def test_dual_stock_payload_manifest_and_reconnect_share_frozen_generation(self):
        f = self.fixture; desired = _rows(('safe', 'unsafe'))
        source_bytes = {path: path.read_bytes() for path in (f.safe, f.unsafe)}
        frozen = capture_export(f.library, [f.playlist], complete=True,
                                playlist_identities=True, display_rows=desired).materialize()
        plan = f._plan(display_rows=frozen.display_rows)
        desired['safe']['title'] = 'later uncaptured edit'
        result = commit_dual_ipod_sync(plan)
        self.assertIsNotNone(result.stock.catalog_witness, result.stock.catalog_warning)
        catalog = _native(f.ipod)
        self.assertEqual(dict(stored_metadata(catalog[0])), _rows(('safe',))['safe'])
        current = manifest.load_composite(f.ipod)
        for path, row in current['files'].items():
            if row['kind'] in {'shared-stock-media', 'rockbox-media'}:
                key = next(key for key, track in f.library.tracks_by_id.items()
                           if str(track.path.resolve()) == row['source'])
                self.assertEqual(_display_tags(f.ipod / path.lstrip('/')),
                                 (f'{key} captured', 'captured Artist'))
        expected = composite_playlist_entry(f.library, f.playlist, display_rows=frozen.display_rows)
        self.assertEqual(next(iter(current['playlists'].values()))['metadata_sig'], expected['metadata_sig'])
        self.assertFalse(engine.device_drift(f.library, f.ipod, display_rows=frozen.display_rows,
            captured_manifest=(current, (f.ipod / '.soundbox/manifest.json').read_bytes())).any())
        latest = _rows(('safe', 'unsafe'), suffix='latest')
        next_plan = f._plan(reconcile=True, display_rows=latest)
        self.assertTrue(next_plan.requires_commit)
        commit_dual_ipod_sync(next_plan)
        self.assertFalse(f._plan(reconcile=True, display_rows=latest).requires_commit)
        self.assertEqual({path: path.read_bytes() for path in source_bytes}, source_bytes)

    def test_rockbox_only_payload_tags_follow_capture_without_raw_metadata(self):
        f = self.fixture; desired = _rows(('safe',))
        f.playlist.keys[:] = ['safe']
        inputs = capture_export(f.library, [f.playlist],
                                display_rows=desired).materialize()
        target = f.base / 'RockboxOnly'; target.mkdir(); (target / '.rockbox').mkdir()
        report = engine.export_playlists(inputs.library, list(inputs.playlists), target,
            art_cache=f.art_cache, rockbox_media_cache=f.media_cache,
            rockbox_art_cache=f.rockbox_art_cache, display_rows=inputs.display_rows)
        self.assertFalse(report.cancelled)
        self.assertFalse([error for row in report.playlists for error in row.errors])
        rows = manifest.load(target)['files']
        payloads = [target / path.lstrip('/') for path, row in rows.items() if row['kind'] == 'track']
        self.assertEqual(len(payloads), 1)
        self.assertEqual(_display_tags(payloads[0]), ('safe captured', 'captured Artist'))
        latest = _rows(('safe',), suffix='latest')
        current = engine.resync_device(inputs.library, target,
            art_cache=f.art_cache, rockbox_media_cache=f.media_cache,
            rockbox_art_cache=f.rockbox_art_cache, display_rows=latest,
            expected_manifest_payload=(target / '.soundbox/manifest.json').read_bytes())
        self.assertFalse(current.cancelled)
        self.assertFalse([error for row in current.playlists for error in row.errors])
        payloads = [target / path.lstrip('/') for path, row in manifest.load(target)['files'].items()
                    if row['kind'] == 'track' and row['playlists']]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(_display_tags(payloads[0]), ('safe latest', 'latest Artist'))
        noop = engine.resync_device(inputs.library, target,
            art_cache=f.art_cache, rockbox_media_cache=f.media_cache,
            rockbox_art_cache=f.rockbox_art_cache, display_rows=latest,
            expected_manifest_payload=(target / '.soundbox/manifest.json').read_bytes())
        self.assertFalse(noop.playlists)

    def test_new_dual_selection_retains_other_managed_effective_values(self):
        f = self.fixture; f.playlist.keys[:] = ['safe']
        commit_dual_ipod_sync(f._plan(display_rows=_rows(('safe',))))
        addition = Playlist('New selection', [], ['unsafe'], pid='addition')
        f.library.playlists.append(addition)
        frozen = _rows(('safe', 'unsafe'), suffix='latest')
        result = commit_dual_ipod_sync(f._plan(selected=[addition], display_rows=frozen))
        self.assertIsNotNone(result.stock.catalog_witness)
        current = manifest.load_composite(f.ipod)
        self.assertEqual(set(current['playlists']), {'dual-test', 'addition'})
        safe = next(row for row in _native(f.ipod) if row['title'] == 'safe latest')
        self.assertEqual(safe['artist'], 'latest Artist')
        self.assertFalse(engine.device_drift(f.library, f.ipod, display_rows=frozen).any())

    def test_stock_only_field_edit_does_not_rewrite_rockbox_audio(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        commit_dual_ipod_sync(f._plan(display_rows=rows))
        rows['safe']['album'] = 'later native album'
        plan = f._plan(reconcile=True, display_rows=rows)
        self.assertTrue(plan.stock.requires_commit)
        self.assertFalse(plan.media_copies)
        self.assertFalse([item for item in plan.file_rewrites if item.kind == 'rockbox-media'])
        commit_dual_ipod_sync(plan)
        self.assertEqual(_native(f.ipod)[0]['album'], 'later native album')
        self.assertFalse(f._plan(reconcile=True, display_rows=rows).requires_commit)

    def test_absent_managed_catalog_cannot_discover_unprojected_retained_members(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        first = f._plan(display_rows=rows, expected_manifest_absent=True)
        commit_dual_ipod_sync(first)
        before = (f.ipod / '.soundbox/manifest.json').read_bytes()
        with mock.patch.object(f.media_cache, 'plan', side_effect=AssertionError('unprojected media prep')):
            with self.assertRaisesRegex(DualIpodError, 'appeared after capture'):
                f._plan(display_rows=rows, expected_manifest_absent=True)
        self.assertEqual((f.ipod / '.soundbox/manifest.json').read_bytes(), before)

    def test_combined_export_validates_once_after_plan_before_any_publication(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        target = f.base / 'MissingRockbox'
        checked = []
        def refuse():
            checked.append(True)
            self.assertFalse(target.exists())
            raise ValueError('selected inputs changed')
        with self.assertRaisesRegex(ValueError, 'selected inputs changed'):
            engine.export_playlists(f.library, [f.playlist], target,
                rockbox_media_cache=f.media_cache, display_rows=rows, validate_inputs=refuse)
        self.assertEqual(checked, [True])
        self.assertFalse(target.exists())
        def admit():
            checked.append(True)
            self.assertFalse((target / 'Music').exists())
        report = engine.export_playlists(f.library, [f.playlist], target,
            rockbox_media_cache=f.media_cache, display_rows=rows, validate_inputs=admit)
        self.assertFalse([error for row in report.playlists for error in row.errors])
        self.assertEqual(len(checked), 2)
        once = mock.Mock()
        noop = engine.resync_device(f.library, target, rockbox_media_cache=f.media_cache,
                                    display_rows=rows, validate_inputs=once)
        self.assertFalse(noop.playlists)
        once.assert_called_once_with()

    def test_changed_cover_after_prepare_stops_honestly_partial_before_cover_copy(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        target = f.base / 'RockboxArtRace'; target.mkdir(); (target / '.rockbox').mkdir()
        sources = {path: path.read_bytes() for path in (f.safe, f.unsafe)}
        initial = dict(f.art_cache.values)
        replacement = f.base / 'later-cover.jpg'
        Image.new('RGB', (300, 300), (230, 90, 10)).save(replacement)
        original_prepare = f.rockbox_art_cache.prepare
        prepared = []
        def prepare(*args, **kwargs):
            result = original_prepare(*args, **kwargs)
            prepared.append(result)
            f.art_cache.values['safe'] = replacement
            return result
        def validate(tid):
            if f.art_cache.values[tid] != initial[tid]:
                raise ValueError('selected cover changed')
        with mock.patch.object(f.rockbox_art_cache, 'prepare', side_effect=prepare):
            result = engine.export_playlists(f.library, [f.playlist], target,
                rockbox_media_cache=f.media_cache, rockbox_art_cache=f.rockbox_art_cache,
                art_cache=f.art_cache, display_rows=rows, validate_artwork=validate)
        self.assertEqual(len(prepared), 1)
        self.assertTrue(any('selected cover changed' in error
                            for row in result.playlists for error in row.errors))
        self.assertFalse(list(target.rglob('cover*')))
        current = manifest.load(target)
        self.assertEqual(current['playlists']['dual-test']['sig'], 'partial')
        self.assertNotEqual(current['completed_generation'], current['generation'])
        owned = [row for row in current['files'].values() if row['kind'] == 'track']
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]['title'], 'safe captured')
        repaired = engine.resync_device(f.library, target,
            rockbox_media_cache=f.media_cache, rockbox_art_cache=f.rockbox_art_cache,
            art_cache=f.art_cache, display_rows=rows)
        self.assertFalse([error for row in repaired.playlists for error in row.errors])
        self.assertNotEqual(manifest.load(target)['playlists']['dual-test']['sig'], 'partial')
        self.assertEqual({path: path.read_bytes() for path in sources}, sources)

    def test_rockbox_changed_or_appeared_manifest_refuses_before_source_plan(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        target = f.base / 'RockboxBaseline'; target.mkdir(); (target / '.rockbox').mkdir()
        absent = engine.resync_device(f.library, target, display_rows=rows,
                                     expected_manifest_absent=True)
        self.assertFalse(absent.playlists)
        self.assertFalse((target / '.soundbox').exists())
        engine.export_playlists(f.library, [f.playlist], target,
            rockbox_media_cache=f.media_cache, display_rows=rows, expected_manifest_absent=True)
        path = target / '.soundbox/manifest.json'; captured = path.read_bytes()
        for options in ({'expected_manifest_absent': True},
                        {'expected_manifest_payload': captured + b'\n'}):
            for operation in ('resync', 'export'):
                with self.subTest(options=tuple(options), operation=operation):
                    before = stock_fixture.StockIpodSync._snapshot(target)
                    with mock.patch.object(sync, 'plan', side_effect=AssertionError('source planning started')):
                        with self.assertRaisesRegex(RuntimeError, 'catalog changed after capture'):
                            if operation == 'resync':
                                engine.resync_device(f.library, target, display_rows=rows, **options)
                            else:
                                engine.export_playlists(f.library, [f.playlist], target,
                                    rockbox_media_cache=f.media_cache, display_rows=rows, **options)
                    self.assertEqual(stock_fixture.StockIpodSync._snapshot(target), before)

    def test_rockbox_manifest_change_between_plans_refuses_before_publication(self):
        f = self.fixture; rows = _rows(('safe', 'unsafe'))
        target = f.base / 'RockboxHeldBaseline'; target.mkdir(); (target / '.rockbox').mkdir()
        engine.export_playlists(f.library, [f.playlist], target,
            rockbox_media_cache=f.media_cache, display_rows=rows)
        path = target / '.soundbox/manifest.json'; captured = path.read_bytes()
        before = dict(stock_fixture.StockIpodSync._snapshot(target)); calls = []
        original = sync.plan
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            path.write_bytes(captured + b'\n')
            return result
        with mock.patch.object(sync, 'plan', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'catalog changed after capture'):
                engine.resync_device(f.library, target, rockbox_media_cache=f.media_cache,
                    display_rows=_rows(('safe', 'unsafe'), suffix='later'),
                    expected_manifest_payload=captured)
        self.assertEqual(len(calls), 1)
        before['.soundbox/manifest.json'] = captured + b'\n'
        self.assertEqual(dict(stock_fixture.StockIpodSync._snapshot(target)), before)

    def test_drift_manifest_capture_preserves_legacy_and_exact_composite_observations(self):
        f = self.fixture
        self.assertIsNone(engine.capture_device_drift_manifest(f.ipod))
        state = f.ipod / '.soundbox'; state.mkdir()
        path = state / 'manifest.json'
        for raw in (b'{broken', b'{}', b'{"schema":1,"playlists":{"x":{"name":"legacy"}}}',
                    b'{"schema":2,"playlists":{},"files":{}}'):
            path.write_bytes(raw)
            observed = engine.capture_device_drift_manifest(f.ipod)
            self.assertEqual(observed[1], raw)
            self.assertEqual(observed[0], manifest.load(f.ipod))
            self.assertEqual(observed[0], manifest.parse(raw))
        path.unlink()
        commit_dual_ipod_sync(f._plan(display_rows=_rows(('safe', 'unsafe'))))
        observed = engine.capture_device_drift_manifest(f.ipod)
        self.assertEqual(observed, manifest.read_composite(f.ipod))
        with mock.patch.object(manifest, 'read_device_manifest',
                               side_effect=AssertionError('duplicate device manifest read')):
            drift = engine.device_drift(f.library, f.ipod,
                display_rows=_rows(('safe', 'unsafe')), captured_manifest=observed)
        self.assertFalse(drift.any())
        encoded_schema = observed[1].replace(b'"schema": 3', b'"schema": "3"')
        self.assertNotEqual(encoded_schema, observed[1])
        path.write_bytes(encoded_schema)
        observed = engine.capture_device_drift_manifest(f.ipod)
        self.assertEqual(observed, manifest.read_composite(f.ipod))
        self.assertFalse(engine.device_drift(f.library, f.ipod,
            display_rows=_rows(('safe', 'unsafe')), captured_manifest=observed).any())
