"""iPod-only album sentinel projection leaves sources and real album names exact."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import io
import unittest
from unittest import mock

from clickwheel.ipod import get_ipod_tracks, parse_itunesdb

from soundbox import dual_ipod, stock_ipod
from soundbox.export_snapshot import capture_export
from soundbox.ipod_export_metadata import freeze_display_rows
from soundbox.library import Library, Track
from soundbox.stock_ipod_catalog import catalog_metadata, compare_stock_catalog
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from tests import test_dual_ipod as dual_fixture
from tests import test_export_playlist_baseline as stock_fixture
from tests.support.ipod import rewrite_catalog


def rendered_albums(payload):
    return {row['title']: row.get('album') or ''
            for row in get_ipod_tracks(parse_itunesdb(io.BytesIO(payload)))}


class IpodAlbumProjection(unittest.TestCase):
    def test_only_missing_or_validated_placeholder_albums_are_branded_and_projection_is_idempotent(self):
        track = Track('one', 'Title', 'Unknown Artist', 'file:///synthetic/song.wav',
                      info={'album': 'Unknown Artist'})
        library = Library({'one': track}, [], '/synthetic/library.xml')
        before = asdict(library)
        for album, expected in (('Unknown Artist', 'SoundBox'), (' UNKNOWN ARTIST\t', 'SoundBox'),
                ('unknown artist', 'SoundBox'), ('Unknown Album', 'SoundBox'), (' UNKNOWN ALBUM\t', 'SoundBox'),
                ('', 'SoundBox'), ('   ', 'SoundBox'), ('A Real Album', 'A Real Album'),
                (' Unknown Artists ', ' Unknown Artists '), ('Unknown Artist EP', 'Unknown Artist EP'),
                ('Unknown Album EP', 'Unknown Album EP')):
            with self.subTest(album=album):
                rows = {'one': dict(id='one', title='Title', artist='Unknown Artist', album=album)}
                original = deepcopy(rows)
                frozen = freeze_display_rows(library, rows)
                self.assertEqual(frozen['one']['album'], expected)
                self.assertEqual(frozen['one']['artist'], 'Unknown Artist')
                self.assertEqual(freeze_display_rows(library, frozen), frozen)
                self.assertEqual(rows, original);self.assertEqual(asdict(library), before)
        for invalid in (None, 123, b'Unknown Artist', 'Unknown Artist\0', 'X' * 4097):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaisesRegex(ValueError, 'Invalid displayed'):
                catalog_metadata(track, library, display_rows={'one': {'album': invalid}})
        self.assertEqual(track.to_row()['album'], 'Unknown Artist')


class IpodAlbumRenderedExports(unittest.TestCase):
    def stock(self):
        case = stock_fixture.ActualExportPlaylistBaseline();case.setUp();self.addCleanup(case.doCleanups)
        return case

    def rows(self, library):
        return {local: dict(id=local, title=track.name, artist=track.artist,
            album=track.info.get('album', ''), genre=track.genre, comments='', year=0,
            bpm=0, bpm_estimated=False) for local, track in library.tracks_by_id.items()}

    def hashes(self, library):
        return {track.path: hashlib.sha256(track.path.read_bytes()).hexdigest()
                for track in library.tracks_by_id.values() if track.path.is_file()}

    def test_direct_first_new_stock_export_preserves_native_info_album_and_brands_only_sentinel(self):
        for album, expected in (('Unknown Artist', 'SoundBox'), ('Unknown Album', 'SoundBox'),
                                ('Legitimate Album', 'Legitimate Album'), ('', 'SoundBox')):
            with self.subTest(album=album):
                case = self.stock();f = case.f;f.playlist.keys[:] = ['new']
                track = f.library.tracks_by_id['new'];track.info['album'] = album
                before, originals = asdict(f.library), self.hashes(f.library)
                plan = stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
                    model_number='MA446', filesystem='fat32', usb_family='iPod Video')
                self.assertEqual(rendered_albums(plan.rendered_database)['New'], expected)
                self.assertEqual(asdict(f.library), before);self.assertEqual(self.hashes(f.library), originals)

    def test_first_frozen_stock_export_witness_and_status_are_clean_without_mutating_capture(self):
        case = self.stock();f = case.f
        f.library.tracks_by_id['new'].info['album'] = 'Unknown Artist'
        f.library.tracks_by_id['old'].info['album'] = 'Real Album'
        rows = self.rows(f.library);before = deepcopy(rows);library_before = asdict(f.library)
        originals = self.hashes(f.library)
        captured = capture_export(f.library, [f.playlist], display_rows=rows)
        payload = captured.payload
        plan = stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
            model_number='MA446', filesystem='fat32', usb_family='iPod Video',
            identity_key=case.key, shadow_store=case.shadow, display_rows=rows)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        albums = rendered_albums(plan.rendered_database)
        self.assertEqual((albums['New'], albums['Existing']), ('SoundBox', 'Real Album'))
        witness_rows = {row.local_id: dict(row.metadata) for row in result.catalog_witness.rows}
        self.assertEqual((witness_rows['new']['album'], witness_rows['old']['album']), ('SoundBox', 'Real Album'))
        current = stock_ipod.capture_stock_ipod_native(f.ipod, include_artwork=True)
        status = stock_ipod.inspect_stock_ipod_sync_status(f.library, f.ipod, identity_key=case.key,
            model_number='MA446', serial_number='', shadow_store=case.shadow,
            native_capture=current, catalog_witness=result.catalog_witness, display_rows=rows)
        self.assertEqual((status.state, status.local_changes), ('up_to_date', 0))
        self.assertEqual(rows, before);self.assertEqual(asdict(f.library), library_before)
        self.assertEqual(captured.payload, payload)
        self.assertEqual(captured.materialize().library.tracks_by_id['new'].info['album'], 'Unknown Artist')
        self.assertEqual(self.hashes(f.library), originals)

    def test_incremental_album_update_renders_once_and_repeat_needs_no_media_or_metadata_write(self):
        for album in ('Unknown Artist', 'Unknown Album', ''):
            with self.subTest(album=album):self.assert_incremental_album_update(album)

    def assert_incremental_album_update(self, album):
        case = self.stock();f = case.f;f.playlist.keys[:] = ['new']
        track = f.library.tracks_by_id['new'];track.info['album'] = album
        rows = self.rows(f.library);originals = self.hashes(f.library);before = asdict(f.library)
        first = stock_ipod.commit_stock_ipod_sync(stock_ipod.plan_stock_ipod_sync(
            f.library, [f.playlist], f.ipod, model_number='MA446', filesystem='fat32',
            usb_family='iPod Video', display_rows=rows, identity_key=case.key, shadow_store=case.shadow))
        rewrite_catalog(f.ipod, lambda tracks, _playlists:
                        setattr(next(row for row in tracks if row.title == track.name), 'album', album))
        def plan(witness):
            snapshot = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
            return stock_ipod.plan_stock_ipod_catalog_update(f.library, [f.playlist], f.ipod,
                snapshot=snapshot, model_number='MA446', filesystem='fat32', usb_family='iPod Video',
                display_rows=rows, catalog_witness=witness, update_metadata=True, incremental=True)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Album-only Sync acquired media')):
            update = plan(first.catalog_witness)
            self.assertTrue(update.requires_commit);self.assertFalse(update.copies or update.rewrites)
            self.assertEqual(rendered_albums(update.rendered_database)[track.name], 'SoundBox')
            self.assertEqual([row.fields for row in update.metadata_updates], [('album',)])
            result = stock_ipod.commit_stock_ipod_sync(update)
            repeat = plan(result.catalog_witness)
            self.assertFalse(repeat.requires_commit)
            self.assertEqual(repeat.metadata_updates, ())
            fresh = read_stock_ipod_snapshot(f.ipod, volume_uuid='generated-volume')
            self.assertFalse(compare_stock_catalog(f.library, [f.playlist], fresh,
                display_rows=rows, catalog_witness=result.catalog_witness, incremental=True).needs_sync)
        self.assertEqual(asdict(f.library), before);self.assertEqual(self.hashes(f.library), originals)

    def test_dual_first_export_uses_same_frozen_album_projection_and_repeat_is_unchanged(self):
        case = dual_fixture.DualIpodSyncTests();case.setUp();self.addCleanup(case.doCleanups)
        source = case.sources / 'branded.wav';dual_fixture._wave(source, 441, 350)
        case.library.tracks_by_id['brand'] = Track('brand', 'Branded New', 'Unknown Artist', source.as_uri(),
                                                info={'album': 'Unknown Artist'})
        case.library.tracks_by_id['safe'].info['album'] = 'Real Album'
        case.playlist.keys[:] = ['brand', 'safe']
        rows = self.rows(case.library);before = deepcopy(rows);library_before = asdict(case.library)
        originals = self.hashes(case.library)
        plan = case._plan(display_rows=rows)
        self.assertEqual(rendered_albums(plan.stock.rendered_database)['Branded New'], 'SoundBox')
        self.assertEqual(rendered_albums(plan.stock.rendered_database)['Safe'], 'Real Album')
        dual_ipod.commit_dual_ipod_sync(plan)
        repeat = case._plan(display_rows=rows, reconcile=True, audit_existing=False)
        self.assertFalse(repeat.requires_commit)
        self.assertEqual(rows, before);self.assertEqual(asdict(case.library), library_before)
        self.assertEqual(self.hashes(case.library), originals)
