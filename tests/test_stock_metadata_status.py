"""Worker-side stock status compares frozen musical values, never audio."""
from dataclasses import replace
import os
from pathlib import Path
import shutil
from threading import Event
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from tests import test_export_playlist_baseline as fixture


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
        self.assertEqual(status.state, 'reconcile_required')
        self.assertEqual(status.database_sha256, self.capture.database_sha256)

    def test_cancelled_observation_never_reports_success_or_mutates_the_captured_catalog(self):
        event = Event(); event.set()
        original = self.capture.database
        with operation_scope(event), self.assertRaises(OperationCancelled):
            self.status()
        self.assertEqual(self.capture.database, original)
