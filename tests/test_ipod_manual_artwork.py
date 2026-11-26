"""Manual artwork stays attached to the same song through real iPod rendering."""
from dataclasses import replace
from collections import OrderedDict
from hashlib import sha256
from io import BytesIO
import os
import unittest
from unittest import mock

from PIL import Image
from clickwheel.ipod import get_ipod_tracks, read_ipod, parse_itunesdb

from soundbox import engine, stock_ipod, shared_imports
from soundbox.artwork import ArtCache, ArtResult
from soundbox.export_snapshot import capture_export
from soundbox.stock_artwork import read_stock_artwork_slots
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.ipod_export_witness import verify_export_witness
from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache
from soundbox import manifest, dual_ipod, sync
from soundbox import artwork_observation
from tests import test_stock_ipod as fixture
from tests import test_dual_ipod as dual_fixture


def result(color):
    output = BytesIO(); Image.new('RGB', (48, 48), color).save(output, 'JPEG')
    return ArtResult(output.getvalue(), 'image/jpeg', 1., 'file:///generated/manual.jpg', 'Manual')


class ManualIpodArtwork(unittest.TestCase):
    def setUp(self):
        self.f = fixture.StockIpodSync(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.library.source = self.f.sources / 'master.db'
        self.f.library.source_kind = 'rekordbox-db'; self.f.library.source_library_id = '123'
        self.f.library.source_playlist_catalog = {'nodes': [{'ID': self.f.playlist.pid,
            'UUID': 'playlist-uuid', 'rb_local_deleted': 0}]}
        self.tid = 'sb-012345abcdef'
        track = replace(self.f.library.tracks_by_id.pop('new'), track_id=self.tid,
                        source_native_id='42', source_uuid='00000000-0000-4000-8000-000000000042')
        self.f.library.tracks_by_id[self.tid] = track
        self.f.playlist.keys[:] = [self.tid]
        self.art = ArtCache(self.f.base / 'state/art').for_library(self.f.library)
        self.art.import_default_photos([self.f.art_cache.paths['new']])
        self.art.configure_default_tracks(self.f.library.tracks_by_id)
        self.shadow = StockIpodShadowStore(self.f.base / 'shadow')
        self.key = read_stock_ipod_snapshot(self.f.ipod, model_number='MA446',
            filesystem='fat32', volume_uuid='generated-volume').identity_key
        self.options = dict(model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            art_cache=self.art, stock_art_cache=self.f.stock_art_cache,
            shadow_store=self.shadow, identity_key=self.key)

    def sync(self):
        # The accepted generation retains the stable local import and native
        # binding; no planner borrows the numeric native ID as artwork owner.
        selected = self.f.playlist
        rows = {tid: dict(id=tid, title=track.name, artist=track.artist, album='')
                for tid, track in self.f.library.tracks_by_id.items()}
        inputs = capture_export(self.f.library, [selected], complete=True, playlist_identities=True,
                                display_rows=rows).materialize()
        self.display_rows = inputs.display_rows
        self.assertEqual(inputs.library.tracks_by_id[self.tid].native_id, '42')
        plan = stock_ipod.plan_stock_ipod_sync(inputs.library, list(inputs.playlists), self.f.ipod,
            display_rows=inputs.display_rows, **self.options)
        return plan, stock_ipod.commit_stock_ipod_sync(plan)

    def status(self, witness):
        capture = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        return stock_ipod.inspect_stock_ipod_sync_status(self.f.library, self.f.ipod,
            identity_key=self.key, model_number='MA446', serial_number='', shadow_store=self.shadow,
            native_capture=capture, catalog_witness=witness, art_cache=self.art,
            display_rows=self.display_rows)

    def assert_pixels(self, dbid, expected):
        rows = get_ipod_tracks(read_ipod(self.f.ipod)); row = next(row for row in rows if row['dbid'] == dbid)
        directory = self.f.ipod / 'iPod_Control/Artwork'
        _raw, slots, _files = read_stock_artwork_slots(directory)
        slot = slots[int(row['mhiiLink'])]
        self.assertEqual(slot.song_id, dbid)
        for payload in expected.payloads:
            raw = (directory / f'F{payload.format_id}_1.ithmb').read_bytes()
            offset = slot.offset(payload.format_id)
            self.assertEqual(raw[offset:offset + payload.size], payload.path.read_bytes())

    def test_fallback_to_manual_is_pending_then_updates_exact_link_pixels_without_audio_copy(self):
        source_sha = sha256(self.f.new_source.read_bytes()).hexdigest()
        fallback = self.art.image_path(self.tid)
        first, outcome = self.sync()
        dbid = first.source_device_dbids[str(self.f.new_source.resolve())]
        self.assertEqual(self.status(outcome.catalog_witness).state, 'up_to_date')
        self.art.store_ok(self.tid, result('blue'))
        manual = self.art.image_path(self.tid)
        self.assertNotEqual(manual, fallback); self.assertTrue(fallback.is_file())
        self.assertEqual(self.status(outcome.catalog_witness).state, 'local_changes')
        changed, outcome = self.sync()
        self.assertEqual(changed.copies, ()); self.assertEqual(changed.rewrites, ())
        self.assertEqual(changed.source_device_dbids[str(self.f.new_source.resolve())], dbid)
        self.assertGreater(changed.summary()['artwork_pixel_bytes'], 0)
        expected = self.f.stock_art_cache.prepare(manual)
        self.assert_pixels(dbid, expected)
        self.assertEqual(self.status(outcome.catalog_witness).state, 'up_to_date')
        repeated, _outcome = self.sync()
        self.assertEqual(repeated.copies, ()); self.assertEqual(repeated.summary()['artwork_pixel_bytes'], 0)
        self.assertEqual(sha256(self.f.new_source.read_bytes()).hexdigest(), source_sha)
        self.assertEqual(self.art.cover_snapshot(self.tid).data, manual.read_bytes())

    def test_identical_legacy_to_scoped_adoption_is_clean_using_the_real_prepared_key(self):
        legacy = ArtCache(self.f.base / 'legacy/art'); legacy.store_ok(self.tid, result('red'))
        self.art = legacy; self.options['art_cache'] = legacy
        first, outcome = self.sync(); old_path = legacy.image_path(self.tid)
        self.art = legacy.adopt_legacy(self.f.library); self.options['art_cache'] = self.art
        self.assertNotEqual(self.art.image_path(self.tid), old_path)
        self.assertEqual(self.art.image_path(self.tid).read_bytes(), old_path.read_bytes())
        # Exercise the actual installed old shadow shape, without source_sha256.
        shadow = self.shadow.load(self.key)
        artwork = {key: {name: value for name, value in row.items() if name != 'source_sha256'}
                   for key, row in shadow.artwork.items()}
        self.shadow.save(replace(shadow, artwork=artwork))
        self.assertEqual(self.status(outcome.catalog_witness).state, 'up_to_date')
        with mock.patch('soundbox.artwork_observation.hashlib.file_digest', side_effect=AssertionError('Warm advisory observation reread JPEG')):
            self.assertEqual(self.status(outcome.catalog_witness).state, 'up_to_date')
        unchanged, _outcome = self.sync()
        self.assertEqual(unchanged.copies, ()); self.assertEqual(unchanged.summary()['artwork_pixel_bytes'], 0)
        self.assertEqual(unchanged.source_device_dbids, first.source_device_dbids)

    def test_current_choice_fence_rejects_a_retired_source_view(self):
        _plan, outcome = self.sync()
        self.art.retire()
        with self.assertRaisesRegex(ValueError, 'Artwork source changed'):
            self.status(outcome.catalog_witness)

    def test_unlinked_manual_change_does_not_request_sync(self):
        _plan, outcome = self.sync()
        self.art.store_ok('old', result('green'))
        self.assertEqual(self.status(outcome.catalog_witness).state, 'up_to_date')

    def test_manual_import_native_projection_and_catalog_sync_preserve_exact_cover(self):
        first, outcome = self.sync()
        dbid = first.source_device_dbids[str(self.f.new_source.resolve())]
        self.art.store_ok(self.tid, result('blue'))
        native = replace(self.f.library.tracks_by_id[self.tid], track_id='42',
            source_native_id='', location_uri=self.f.new_source.resolve().as_uri(),
            source_native_path=str(self.f.library.tracks_by_id[self.tid].native_path))
        native_library = replace(self.f.library,
            tracks_by_id={'42': native, 'old': self.f.library.tracks_by_id['old']},
            playlists=[replace(self.f.playlist, keys=['42'])])
        self.f.library = shared_imports.project(native_library, [dict(local_id=self.tid,
            source=str(native_library.source), library_id='123', native_id='42',
            native_uuid=native.source_uuid, canonical=str(native.path),
            native_path=str(native.native_path))])
        self.f.playlist = self.f.library.playlists[0]
        self.art = self.art.for_library(self.f.library); self.options['art_cache'] = self.art
        inputs = capture_export(self.f.library, [self.f.playlist], playlist_identities=True).materialize()
        snapshot = read_stock_ipod_snapshot(self.f.ipod, model_number='MA446',
            filesystem='fat32', volume_uuid='generated-volume')
        snapshot = replace(snapshot, identity_key=self.key)
        options = {key: value for key, value in self.options.items() if key not in ('shadow_store','identity_key')}
        plan = stock_ipod.plan_stock_ipod_catalog_update(inputs.library, list(inputs.playlists),
            self.f.ipod, snapshot=snapshot, catalog_witness=outcome.catalog_witness,
            update_metadata=True, incremental=True, display_rows=self.display_rows, **options)
        self.assertEqual(plan.copies, ()); self.assertEqual(plan.rewrites, ())
        self.assertGreater(plan.summary()['artwork_pixel_bytes'], 0)
        outcome = stock_ipod.commit_stock_ipod_sync(plan)
        self.assert_pixels(dbid, self.f.stock_art_cache.prepare(self.art.image_path(self.tid)))
        actual = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        verify_export_witness(plan.catalog_witness, actual.database, actual.artwork_database_sha256)
        self.assertEqual(self.status(plan.catalog_witness).state, 'up_to_date')
        self.assertEqual({row['dbid'] for row in get_ipod_tracks(read_ipod(self.f.ipod))},
                         {row['dbid'] for row in get_ipod_tracks(parse_itunesdb(BytesIO(first.rendered_database)))})

    def test_same_artist_and_soundbox_album_keep_distinct_per_track_art_links_and_pixels(self):
        self.f.playlist.keys[:] = ['old', self.tid]
        for track in self.f.library.tracks_by_id.values():track.artist = 'sonny.'
        self.art.store_ok(self.tid, result('blue'))
        plan, _outcome = self.sync()
        rows = {row['dbid']: row for row in get_ipod_tracks(read_ipod(self.f.ipod))}
        old = plan.source_device_dbids[str(self.f.existing_source.resolve())]
        manual = plan.source_device_dbids[str(self.f.new_source.resolve())]
        self.assertEqual(rows[old]['album'], 'SoundBox'); self.assertEqual(rows[manual]['album'], 'SoundBox')
        self.assertNotEqual(rows[old]['mhiiLink'], rows[manual]['mhiiLink'])
        for tid, dbid in (('old', old), (self.tid, manual)):
            self.assert_pixels(dbid, self.f.stock_art_cache.prepare(self.art.image_path(tid)))

    def test_rockbox_manual_choice_is_pending_and_sync_updates_only_exact_cover_bytes(self):
        target = self.f.base / 'ROCKBOX'; target.mkdir()
        media = RockboxMediaCache(self.f.base / 'flat-media')
        pixels = RockboxArtworkCache(self.f.base / 'flat-art')
        options = dict(art_cache=self.art, rockbox_media_cache=media, rockbox_art_cache=pixels)
        report = engine.export_playlists(self.f.library, [self.f.playlist], target, clean=False, **options)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertFalse(engine.device_drift(self.f.library, target, art_cache=self.art).any())
        before = manifest.load(target)
        audio = {path: (target / path.lstrip('/')).read_bytes() for path, row in before['files'].items() if row['kind'] == 'track'}
        self.art.store_ok(self.tid, result('blue'))
        self.assertTrue(engine.device_drift(self.f.library, target, art_cache=self.art).any())
        report = engine.resync_device(self.f.library, target, **options)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(sum(row.copied for row in report.playlists), 0)
        after = manifest.load(target)
        for path, raw in audio.items():self.assertEqual((target / path.lstrip('/')).read_bytes(), raw)
        for path, row in after['files'].items():
            if row['kind'] != 'artwork':continue
            self.assertEqual(row['track_id'], self.tid)
            self.assertEqual(row['source_sha256'], sha256(self.art.image_path(self.tid).read_bytes()).hexdigest())
            self.assertEqual(sha256((target / path.lstrip('/')).read_bytes()).hexdigest(), row['sha256'])
        self.assertFalse(engine.device_drift(self.f.library, target, art_cache=self.art).any())
        for row in after['files'].values():
            if row['kind'] == 'artwork':row.pop('source_sha256'); row.pop('track_id')
        manifest.save(target, after)
        self.assertTrue(engine.device_drift(self.f.library, target, art_cache=self.art).any())
        original = sync.DeviceSession.atomic_copy; writes = []
        def copied(session, source, destination):
            writes.append(str(destination)); return original(session, source, destination)
        with mock.patch.object(sync.DeviceSession, 'atomic_copy', copied):
            engine.resync_device(self.f.library, target, **options)
        self.assertEqual(writes, [])
        self.assertFalse(engine.device_drift(self.f.library, target, art_cache=self.art).any())

    def test_dual_manual_choice_updates_both_firmware_art_without_audio_copy(self):
        case = dual_fixture.DualIpodSyncTests(); case.setUp(); self.addCleanup(case.doCleanups)
        case.playlist.keys[:] = ['safe']
        art = ArtCache(case.base / 'state/art').for_library(case.library)
        art.import_default_photos([case.art_cache.image_path('safe')])
        art.configure_default_tracks(case.library.tracks_by_id); case.art_cache = art
        first = case._plan(); dual_ipod.commit_dual_ipod_sync(first)
        dbid = first.stock.source_device_dbids[str(case.safe.resolve())]
        self.assertFalse(engine.device_drift(case.library, case.ipod, art_cache=art).any())
        original_audio = case.safe.read_bytes()
        art.store_ok('safe', result('blue'))
        self.assertTrue(engine.device_drift(case.library, case.ipod, art_cache=art).any())
        plan = case._plan()
        self.assertEqual(plan.stock.copies, ()); self.assertEqual(plan.media_copies, ())
        self.assertEqual(plan.stock.source_device_dbids[str(case.safe.resolve())], dbid)
        self.assertTrue(plan.file_rewrites)
        self.assertTrue(all(row.kind == 'rockbox-artwork' for row in plan.file_rewrites))
        dual_ipod.commit_dual_ipod_sync(plan)
        for row in plan.file_rewrites:
            self.assertEqual((case.ipod / row.relative_path).read_bytes(), row.source.read_bytes())
        self.assertFalse(engine.device_drift(case.library, case.ipod, art_cache=art).any())
        self.assertEqual(case.safe.read_bytes(), original_audio)

    def test_advisory_digest_memo_is_bounded_and_restored_mtime_does_not_reuse_old_bytes(self):
        path = self.f.base / 'observation.jpg'; path.write_bytes(b'first observed bytes')
        before = path.stat(); first = artwork_observation.image_digest(path)
        path.write_bytes(b'other observed bytes')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertNotEqual(artwork_observation.image_digest(path), first)
        with mock.patch.object(artwork_observation, '_LIMIT', 2), \
                mock.patch.object(artwork_observation, '_DIGESTS', OrderedDict()):
            for index in range(3):
                image = self.f.base / f'observation-{index}.jpg'; image.write_bytes(bytes([index]))
                artwork_observation.image_digest(image)
            self.assertEqual(len(artwork_observation._DIGESTS), 2)

    def test_choice_publication_during_observation_rejects_the_previous_selection(self):
        original = self.art.image_path
        def changed(tid):
            path = original(tid); self.art.store_ok(tid, result('blue')); return path
        with mock.patch.object(self.art, 'image_path', side_effect=changed), \
                self.assertRaisesRegex(ValueError, 'Selected artwork changed'):
            with artwork_observation.selected_images(self.art, [self.tid]):pass
