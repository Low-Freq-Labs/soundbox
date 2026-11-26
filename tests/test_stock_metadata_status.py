"""Worker-side stock status compares frozen musical values, never audio."""
from dataclasses import replace
import io
import os
from pathlib import Path
import shutil
from threading import Event
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from tests import test_export_playlist_baseline as fixture
from tests.support.ipod import play_counts, rewrite_catalog


class StockMetadataStatus(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ActualExportPlaylistBaseline()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.f = self.case.f; self.f.playlist.keys[:] = ['new']
        self.rows = {'new': dict(id='new', title='Frozen Title', artist='Frozen Artist',
            album='Album', genre='DNB', comments='Saved note', year=2020, bpm=140, bpm_estimated=False)}
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.case.key, shadow_store=self.case.shadow, display_rows=self.rows)
        self.result = stock_ipod.commit_stock_ipod_sync(plan)
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.witness = self.result.catalog_witness

    def status(self, *, rows=None, witness=None, legacy=False):
        real_open = os.open
        media = {str(path.resolve()) for path in (self.f.existing_source, self.f.new_source)}
        def opening(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and str(path) in media:
                raise AssertionError('Metadata status read source audio')
            return real_open(path, *args, **kwargs)
        with mock.patch.object(stock_ipod.os, 'open', side_effect=opening), \
                mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Status acquired audio')), \
                mock.patch.object(stock_ipod, '_sha256', side_effect=AssertionError('Status hashed audio or reread catalog')):
            return stock_ipod.inspect_stock_ipod_sync_status(self.f.library, self.f.ipod,
                identity_key=self.case.key, model_number='MA446', serial_number='', shadow_store=self.case.shadow,
                native_capture=self.capture, catalog_witness=None if legacy else witness or self.witness,
                display_rows=self.rows if rows is None else rows)

    def test_exact_effective_projection_is_clean_despite_different_raw_source_fields(self):
        self.assertNotEqual(self.f.library.tracks_by_id['new'].name, self.rows['new']['title'])
        status = self.status()
        self.assertEqual(status.state, 'up_to_date')
        self.assertEqual(status.database_sha256, self.capture.database_sha256)
        self.assertEqual(status.artwork_database_sha256, '')
        self.assertEqual(status.local_changes, 0)

    def test_zero_one_and_all_playback_records_are_clean_and_preserved(self):
        from clickwheel.ipod import get_ipod_tracks, parse_itunesdb
        count = len(get_ipod_tracks(parse_itunesdb(io.BytesIO(self.capture.database))))
        database = self.capture.database
        path = self.f.ipod / 'iPod_Control/iTunes/Play Counts'
        for activity in ('zero', 'one', 'all', 'skips'):
            with self.subTest(activity=activity):
                rows = [(0, 0, 0, 0)] * count
                if activity == 'one':rows[0] = (2, 0, 80, 12000)
                if activity == 'all':rows = [(1, 0, 0, 500)] * count
                if activity == 'skips':rows = [(0, 3, 0, 0)] * count
                payload = play_counts(rows); path.write_bytes(payload)
                self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
                entries = self.capture.playback_entries(expected_tracks=count)
                self.assertIsNotNone(entries)
                self.assertEqual([(entry.play_count, entry.skip_count) for entry in entries],
                                 [(plays, skips) for plays, skips, _rating, _resume in rows])
                status = self.status()
                self.assertEqual((status.state, status.local_changes), ('up_to_date', 0))
                self.assertEqual(status.play_counts_sha256, self.capture.play_counts_sha256)
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(self.capture.database, database)

    def test_playback_does_not_mask_real_metadata_or_membership_edits(self):
        path = self.f.ipod / 'iPod_Control/iTunes/Play Counts'
        path.write_bytes(play_counts([(2, 0, 0, 0), (0, 1, 0, 0)]))
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        changed = {'new': {**self.rows['new'], 'title': 'A real edit', 'artist': 'Changed artist'}}
        status = self.status(rows=changed)
        self.assertEqual((status.state, status.local_changes), ('local_changes', 1))
        self.assertEqual(status.change_summary, (('song_details', 1),))
        self.f.playlist.keys.append('old')
        status = self.status()
        self.assertEqual((status.state, status.local_changes), ('local_changes', 1))
        self.assertEqual(status.change_summary, (('playlist', 1),))

    def test_external_playlist_song_and_cover_are_observed_without_sync_or_cache_rebase(self):
        from soundbox.ipod_export_witness import verify_export_witness
        witness_before = self.witness.encode()
        shadow_before = {p: p.read_bytes() for p in self.case.shadow.root.rglob('*.json')}
        rewrite_catalog(self.f.ipod, external=True, artwork=True)
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.assertNotEqual(self.capture.database_sha256, self.witness.database_sha256)
        self.assertTrue(self.capture.artwork_database)
        status = self.status()
        self.assertEqual((status.state, status.local_changes), ('up_to_date', 0))
        self.assertIn('linked playlists', status.detail)
        self.assertEqual(self.witness.encode(), witness_before)
        self.assertEqual({p: p.read_bytes() for p in shadow_before}, shadow_before)
        with self.assertRaisesRegex(ValueError, 'catalog changed'):
            verify_export_witness(self.witness, self.capture.database, self.capture.artwork_database_sha256)

    def test_known_device_playlist_name_order_membership_and_metadata_still_request_sync(self):
        self.f.playlist.keys[:] = ['old', 'new']
        self.rows['old'] = dict(id='old', title='Existing', artist='Fixture Artist',
            album='', genre='', comments='', year=0, bpm=0, bpm_estimated=False)
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.case.key, shadow_store=self.case.shadow, display_rows=self.rows)
        self.witness = stock_ipod.commit_stock_ipod_sync(plan).catalog_witness
        database = self.f.ipod / 'iPod_Control/iTunes/iTunesDB'; before = database.read_bytes()
        binding, = self.witness.bindings
        for change in ('name', 'order', 'membership', 'metadata'):
            with self.subTest(change=change):
                database.write_bytes(before)
                def edit(tracks, playlists):
                    linked = next(row for row in playlists if row.playlist_id == binding.device_playlist_id)
                    if change == 'name':linked.name = 'Changed on the device'
                    if change == 'order':linked.track_ids.reverse()
                    if change == 'membership':linked.track_ids.pop()
                    if change == 'metadata':
                        next(row for row in tracks if row.dbid == binding.members[0][1]).title = 'Device edit'
                rewrite_catalog(self.f.ipod, edit, external=True)
                self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
                status = self.status()
                self.assertEqual((status.state, status.local_changes), ('local_changes', 1))

    def test_external_growth_preserves_known_artwork_but_missing_or_changed_records_are_unresolved(self):
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.case.key, shadow_store=self.case.shadow, display_rows=self.rows,
            art_cache=self.f.art_cache, stock_art_cache=self.f.stock_art_cache)
        self.witness = stock_ipod.commit_stock_ipod_sync(plan).catalog_witness
        shadow = self.case.shadow.load(self.case.key)
        self.assertTrue(shadow.artwork)
        rewrite_catalog(self.f.ipod, external=True, artwork=True)
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.assertEqual(self.status().state, 'up_to_date')
        folder = self.f.ipod / 'iPod_Control/Artwork'
        original = {path: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        database = folder / 'ArtworkDB'
        thumbnail = next(folder.glob('*.ithmb'))
        for damage in ('missing database', 'changed record', 'missing thumbnails', 'truncated thumbnails'):
            with self.subTest(damage=damage):
                for path, payload in original.items():path.write_bytes(payload)
                if damage == 'missing database':database.unlink()
                if damage == 'changed record':
                    from soundbox.stock_artwork import parse_stock_artwork_slots
                    image_id = int(next(iter(shadow.artwork.values()))['image_id'])
                    raw = parse_stock_artwork_slots(original[database])[image_id].raw
                    payload = bytearray(original[database]); offset = payload.index(raw) + 48
                    payload[offset:offset + 4] = (int.from_bytes(raw[48:52], 'little') + 1).to_bytes(4, 'little')
                    database.write_bytes(payload)
                if damage == 'missing thumbnails':thumbnail.unlink()
                if damage == 'truncated thumbnails':thumbnail.write_bytes(b'')
                self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
                self.assertEqual((self.status().state, self.status().local_changes), ('comparison_pending', 0))

    def test_external_deletion_is_not_a_request_to_recreate_and_replaced_identity_is_unknown(self):
        binding, = self.witness.bindings
        rewrite_catalog(self.f.ipod,
            lambda _tracks, lists: lists.__setitem__(slice(None),
                [row for row in lists if row.playlist_id != binding.device_playlist_id]), external=True)
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.assertEqual(self.status().state, 'up_to_date')
        changed = bytearray(self.capture.database); changed[0x48] ^= 1
        database = self.f.ipod / 'iPod_Control/iTunes/iTunesDB'; database.write_bytes(changed)
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.assertEqual(self.status().state, 'comparison_pending')

    def test_each_saved_musical_field_marks_real_metadata_drift_without_audio_reads(self):
        for field, value in dict(title='Later title', artist='Later artist', album='Later album', genre='Later genre',
                                 comments='Later note', year=2021, bpm=141).items():
            with self.subTest(field=field):
                rows = {'new': {**self.rows['new'], field: value}}
                status = self.status(rows=rows)
                self.assertEqual(status.state, 'local_changes')
                self.assertEqual(status.local_changes, 1)
                self.assertEqual(status.database_sha256, self.capture.database_sha256)
                self.assertEqual(status.artwork_database_sha256, self.capture.artwork_database_sha256)

    def test_estimated_or_explicitly_unavailable_bpm_does_not_invent_a_saved_value(self):
        for row in ({**self.rows['new'], 'bpm': 999, 'bpm_estimated': True},
                    {key: value for key, value in {**self.rows['new'], 'bpm_estimated': True}.items() if key != 'bpm'}):
            with self.subTest(row=row):
                self.assertEqual(self.status(rows={'new': row}).state, 'up_to_date')

    def test_missing_partial_invalid_or_wrong_song_projection_never_reports_clean(self):
        for rows in ({}, {'new': dict(title='pair only', artist='pair only')},
                     {'new': {**self.rows['new'], 'id': 'another-song'}},
                     {'new': {**self.rows['new'], 'year': 'invalid'}},
                     {'new': {key: value for key, value in self.rows['new'].items() if key != 'bpm'}}):
            with self.subTest(rows=rows):
                self.assertEqual(self.status(rows=rows).state, 'comparison_pending')
        with mock.patch.object(stock_ipod, 'catalog_metadata', side_effect=AssertionError('Missing projection derived raw data')):
            self.assertEqual(self.status(rows={}).state, 'comparison_pending')

    def test_stale_source_device_catalog_and_song_identity_do_not_compare_new_values(self):
        for witness in (replace(self.witness, source=('/another/source', 'rekordbox-db', 'other')),
                        replace(self.witness, device_key='another-device'),
                        replace(self.witness, database_sha256='0' * 64),
                        replace(self.witness, artwork_database_sha256='1' * 64)):
            with self.subTest(witness=witness):
                self.assertEqual(self.status(witness=witness).state, 'comparison_pending')
        track = self.f.library.tracks_by_id['new']
        for field in ('source_native_id', 'source_uuid', 'source_native_path'):
            original = getattr(track, field)
            try:
                setattr(track, field, original + '-changed')
                self.assertEqual(self.status().state, 'comparison_pending')
            finally:
                setattr(track, field, original)

    def test_actual_path_rebinding_with_equal_source_size_and_mtime_is_unknown(self):
        track = self.f.library.tracks_by_id['new']
        other = self.f.sources / 'relocated-copy.wav'; shutil.copy2(self.f.new_source, other)
        self.assertEqual(other.stat().st_size, self.f.new_source.stat().st_size)
        self.assertEqual(other.stat().st_mtime_ns, self.f.new_source.stat().st_mtime_ns)
        track.location_uri = other.as_uri()
        self.assertEqual(self.status().state, 'comparison_pending')

    def test_current_native_playlist_uuid_is_required_and_ambiguous_headers_are_unknown(self):
        original = self.f.library.source_playlist_catalog
        node = original['nodes'][0]
        for nodes in ([], [{**node, 'UUID': 'replacement-playlist'}], [node, dict(node)],
                      [{**node, 'rb_local_deleted': 1}]):
            with self.subTest(nodes=nodes):
                self.f.library.source_playlist_catalog = {'nodes': nodes}
                self.assertEqual(self.status().state, 'comparison_pending')
        self.f.library.source_playlist_catalog = original
        self.assertEqual(self.status().state, 'up_to_date')

    def test_legacy_no_witness_semantics_and_missing_projection_remain_distinct(self):
        changed = {'new': {**self.rows['new'], 'title': 'new title'}}
        self.assertEqual(self.status(rows=changed, legacy=True).state, 'up_to_date')
        self.assertEqual(self.status(rows={}).state, 'comparison_pending')
        # Old witnesses without source_members still use the exact captured
        # canonical source key, not an assumed native locator as actual path.
        old = replace(self.witness, bindings=())
        self.assertEqual(self.status(witness=old).state, 'up_to_date')

    def test_changed_actual_native_catalog_returns_fresh_digest_for_one_view_refresh(self):
        rows = {'new': {**self.rows['new'], 'title': 'Device catalog changed'}}
        planned = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.case.key, display_rows=rows)
        stock_ipod.commit_stock_ipod_sync(planned)
        previous_sha = self.capture.database_sha256
        self.capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        self.assertNotEqual(previous_sha, self.capture.database_sha256)
        status = self.status()
        self.assertEqual(status.state, 'local_changes')
        self.assertEqual(status.local_changes, 1)
        self.assertEqual(status.database_sha256, self.capture.database_sha256)

    def test_cancelled_observation_never_reports_success_or_mutates_the_captured_catalog(self):
        event = Event(); event.set()
        original = self.capture.database
        with operation_scope(event), self.assertRaises(OperationCancelled):
            self.status()
        self.assertEqual(self.capture.database, original)
