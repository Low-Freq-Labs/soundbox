"""Generated cached-catalog association; no live sources, mounts or audio IO."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import wave
import unittest
from unittest import mock

from PIL import Image
from clickwheel.ipod import get_ipod_tracks, read_ipod
from clickwheel.ipod.ipod_models import capabilities_for_family_gen
from clickwheel.ipod.itunesdb_writer import (
    ChecksumType, PlaylistInfo, SmartPlaylistPrefs, SmartPlaylistRules, TrackInfo, write_itunesdb)

from soundbox import dual_ipod, stock_ipod
from soundbox.library import Library, Playlist, Track
from soundbox.rockbox_media import RockboxMediaCache
from soundbox.stock_artwork import StockArtworkCache, read_stock_artwork_slots
from soundbox.stock_ipod_catalog import recognize_stock_playlists, compare_stock_catalog, catalog_match_labels
from soundbox.stock_ipod_capture import capture_stock_ipod_native
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.sync import DeviceSession


class StockCatalogTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        self.base = Path(area.name)
        self.target = self.base / "synthetic-ipod"; self.target.mkdir()
        self.source = self.base / "unavailable-sources"
        self.infos = [TrackInfo(
            title=title, artist=artist, location=f":iPod_Control:Music:F00:{dbid}.wav",
            size=2000 + dbid, length=10000, sample_rate=44100, filetype="wav", dbid=dbid,
            play_count=3, skip_count=1, rating=80)
            for dbid, title, artist in ((101, "Alpha", "Artist A"),
                                         (102, "Beta", "Artist B"), (103, "Other", "Artist C"))]
        self.playlists = [PlaylistInfo("Favorites", [102, 101], playlist_id=901),
                          PlaylistInfo("Other", [103], playlist_id=902)]
        self.smart = []
        self.write_catalog()
        self.playlist = Playlist("Favorites", [], ["alpha", "beta"], pid="favorites")
        self.library = Library({
            local: Track(local, title, artist, (self.source / f"{local}.wav").as_uri(),
                         total_time=10, size=2000 + dbid)
            for local, title, artist, dbid in (("alpha", "Alpha", "Artist A", 101),
                                               ("beta", "Beta", "Artist B", 102),
                                               ("other", "Other", "Artist C", 103))},
            [self.playlist], self.source / "synthetic-source.xml")
        self.images = {}
        for local, color in (("alpha", "red"), ("beta", "blue"), ("other", "green")):
            path = self.base / f"{local}.jpg"
            Image.new("RGB", (240, 240), color).save(path)
            self.images[local] = path
        self.art = mock.Mock(); self.art.image_path.side_effect = self.images.get
        self.stock_art = StockArtworkCache(self.base / "stock-art-cache")
        self.snapshot = self.read_snapshot()
        for module in (stock_ipod, dual_ipod):
            patcher = mock.patch.object(module, "_pace_classic_usb")
            patcher.start(); self.addCleanup(patcher.stop)

    def write_catalog(self):
        (self.target / "iPod_Control/iTunes").mkdir(parents=True, exist_ok=True)
        self.assertTrue(write_itunesdb(
            str(self.target), self.infos, backup=False, force_checksum=ChecksumType.NONE,
            playlists=self.playlists, smart_playlists=self.smart,
            capabilities=capabilities_for_family_gen("iPod Video", "5.5th Gen")))

    def read_snapshot(self):
        return read_stock_ipod_snapshot(
            self.target, model_number="MA446", volume_uuid="synthetic-volume",
            filesystem="fat32", capacity_bytes=10000000, free_bytes=9000000,
            native_capture=capture_stock_ipod_native(self.target, include_artwork=True))

    def plan(self, **options):
        return stock_ipod.plan_stock_ipod_catalog_update(
            self.library, [self.playlist], self.target, snapshot=self.snapshot,
            model_number="MA446", filesystem="fat32", art_cache=self.art,
            stock_art_cache=self.stock_art, usb_family='iPod Video', **options)

    def recognize(self):
        return recognize_stock_playlists(self.library, [self.playlist], self.snapshot)[0]

    def test_name_count_recognizes_and_track_links_ignore_playlist_order(self):
        row = self.recognize()
        self.assertTrue(row.recognized)
        self.assertEqual(row.device_playlist_id, 901)
        self.assertEqual({match.local_id: match.dbid for match in row.tracks}, {"alpha": 101, "beta": 102})

    def test_recognition_does_not_claim_unknown_song_correspondence(self):
        self.library.tracks_by_id["alpha"].name = "Different recording"
        row = self.recognize()
        self.assertTrue(row.recognized)
        self.assertEqual(row.unmatched_track_ids, ("alpha",))
        self.assertEqual([match.local_id for match in row.tracks], ["beta"])

    def test_count_or_exact_name_mismatch_does_not_recognize(self):
        for playlist in (replace(self.playlist, name="Different favorites"),
                         replace(self.playlist, keys=["alpha"])):
            with self.subTest(playlist=playlist):
                self.assertFalse(recognize_stock_playlists(self.library, [playlist], self.snapshot)[0].recognized)

    def test_unique_case_whitespace_and_unicode_normalized_playlist_names_recognize(self):
        for source_name, device_name in ((' FAVORITES ', 'Favorites'), ('Cafe\u0301', 'Café')):
            self.playlist.name = source_name
            favorites = next(row for row in self.snapshot.playlists if row.playlist_id == 901)
            snapshot = replace(self.snapshot, playlists=tuple(
                replace(row, title=device_name) if row.playlist_id == 901 else row for row in self.snapshot.playlists))
            self.assertTrue(recognize_stock_playlists(self.library, [self.playlist], snapshot)[0].recognized)
            collision = replace(favorites, title=source_name, playlist_id=999, entries=favorites.entries[:1])
            snapshot = replace(snapshot, playlists=(*snapshot.playlists, collision))
            self.assertFalse(recognize_stock_playlists(self.library, [self.playlist], snapshot)[0].recognized)

    def test_count_different_partial_recognition_updates_only_safe_known_metadata(self):
        self.library.tracks_by_id['alpha'].name = 'Unknown old recording'
        self.add_new_wave(); self.playlist.keys.append('new')
        self.playlist.name = ' FAVORITES '
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('unsafe additions')):
            plan = self.plan(update_metadata=True, incremental=True,
                             title_overrides={'beta': 'Known Beta updated'})
            self.assertTrue(plan.catalog_recognition[0].recognized)
            self.assertTrue(plan.catalog_warning)
            self.assertFalse(plan.copies or plan.catalog_playlist_updates)
            stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.read_snapshot()
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual((favorites.title, [row.dbid for row in favorites.entries]), ('Favorites', [102, 101]))
        self.assertEqual(favorites.entries[0].title, 'Known Beta updated')

    def test_duplicate_playlist_candidates_are_ambiguous(self):
        favorites = next(row for row in self.snapshot.playlists if row.title == "Favorites")
        duplicate = replace(favorites, playlist_id=999)
        self.snapshot = replace(self.snapshot, playlists=(*self.snapshot.playlists, duplicate))
        self.assertFalse(self.recognize().recognized)

    def test_duplicate_dbids_or_song_labels_never_match_by_ordinal(self):
        row = self.snapshot.tracks[0]
        self.snapshot = replace(self.snapshot, tracks=(*self.snapshot.tracks, row))
        self.assertIn("alpha", self.recognize().unmatched_track_ids)
        self.snapshot = self.read_snapshot()
        tracks = tuple(replace(row, title="Alpha", artist="Artist A", size=0)
                       if row.dbid == 102 else row for row in self.snapshot.tracks)
        self.snapshot = replace(self.snapshot, tracks=tracks)
        self.library.tracks_by_id["alpha"].size = 0
        self.assertIn("alpha", self.recognize().unmatched_track_ids)

    def test_known_different_duration_leaves_song_unmodified(self):
        self.library.tracks_by_id["alpha"].total_time = 120
        self.assertIn("alpha", self.recognize().unmatched_track_ids)

    def test_reused_numeric_track_id_is_not_a_song_correspondence(self):
        alpha = next(row for row in self.snapshot.tracks if row.dbid == 101)
        self.snapshot = replace(self.snapshot, tracks=tuple(
            replace(row, track_id=alpha.track_id) if row.dbid == 102 else row
            for row in self.snapshot.tracks))
        row = self.recognize()
        self.assertTrue(row.recognized)
        self.assertEqual(set(row.unmatched_track_ids), {"alpha", "beta"})

    def test_shared_device_row_cannot_take_independent_local_art_choices(self):
        self.library.tracks_by_id['alias'] = replace(self.library.tracks_by_id['alpha'], track_id='alias')
        another = Playlist('Other Favorites', [], ['alias'], pid='alias')
        self.playlists.append(PlaylistInfo('Other Favorites', [101], playlist_id=905))
        self.write_catalog(); self.snapshot = self.read_snapshot()
        rows = recognize_stock_playlists(self.library, [self.playlist, another], self.snapshot)
        self.assertTrue(all(row.recognized for row in rows))
        self.assertIn('alpha', rows[0].unmatched_track_ids)
        self.assertIn('alias', rows[1].unmatched_track_ids)

    def test_repeated_same_song_occurrences_keep_one_safe_link(self):
        self.playlists[0] = PlaylistInfo("Favorites", [101, 101], playlist_id=901)
        self.write_catalog(); self.snapshot = self.read_snapshot()
        self.playlist.keys = ["alpha", "alpha"]
        row = self.recognize()
        self.assertTrue(row.recognized)
        self.assertEqual([(match.local_id, match.dbid) for match in row.tracks], [("alpha", 101)])

    def test_plan_and_commit_art_without_source_or_device_media(self):
        original = (self.target / "iPod_Control/iTunes/iTunesDB").read_bytes()
        self.assertFalse(self.source.exists())
        self.assertFalse((self.target / "iPod_Control/Music").exists())
        with mock.patch.object(stock_ipod, "acquire_source", side_effect=AssertionError("Audio acquisition")), \
                mock.patch.object(stock_ipod, "_stock_media_omission", side_effect=AssertionError("Audio probe")):
            plan = self.plan(title_overrides={"alpha": "New title must wait"})
            self.assertEqual((self.target / "iPod_Control/iTunes/iTunesDB").read_bytes(), original)
            result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertTrue(plan.catalog_only)
        self.assertFalse(plan.metadata_updates)
        self.assertEqual((plan.copies, plan.rewrites, plan.media_deletes, plan.adoptions), ((), (), (), ()))
        self.assertEqual(result.copied_tracks, 0)
        tracks = {row["dbid"]: row for row in get_ipod_tracks(read_ipod(self.target))}
        self.assertEqual(tracks[101]["title"], "Alpha")
        self.assertNotEqual(tracks[101]["mhiiLink"], tracks[102]["mhiiLink"])
        self.assertEqual(plan.shadow_sources, {})
        self.assertFalse(self.source.exists())
        self.assertFalse((self.target / "iPod_Control/Music").exists())

    def test_optional_metadata_update_keeps_cached_device_order(self):
        plan = self.plan(update_metadata=True, title_overrides={"alpha": "Curated Alpha"})
        self.assertEqual([(row.dbid, row.title_after) for row in plan.metadata_updates],
                         [(101, "Curated Alpha"), (102, "Beta")])
        self.assertEqual([(row.dbid, row.fields) for row in plan.metadata_updates],
                         [(101, ('title', 'album')), (102, ('album',))])
        stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.read_snapshot()
        favorites = next(row for row in snapshot.playlists if row.title == "Favorites")
        self.assertEqual([row.dbid for row in favorites.entries], [102, 101])
        self.assertEqual([row.title for row in favorites.entries], ["Beta", "Curated Alpha"])
        self.assertEqual({row.dbid: row.album for row in snapshot.tracks},
                         {101: 'SoundBox', 102: 'SoundBox', 103: ''})

    def test_supported_display_metadata_sync_preserves_rating_history_and_other_strings(self):
        self.infos[0].album = 'Old album'; self.infos[0].genre = 'Old genre'
        self.infos[0].year = 1999; self.infos[0].bpm = 88
        self.infos[0].comment = 'Old comment'; self.infos[0].composer = 'Keep composer'
        self.infos[0].sort_artist = 'Preserved ordering'
        self.infos[2].comment = 'Unselected comment'
        self.write_catalog(); self.snapshot = self.read_snapshot()
        displayed = {'alpha': dict(id='alpha', title='Displayed title', artist='Displayed artist',
            album='Displayed album', genre='Displayed genre', comments='Unicode café\nsecond line',
            year='2026', bpm='146.95', bpm_estimated=False, rating='1', playCount='999')}
        plan = self.plan(update_metadata=True, display_rows=displayed)
        update = next(row for row in plan.metadata_updates if row.dbid == 101)
        self.assertEqual(set(update.fields), {'title', 'artist', 'album', 'genre', 'comments', 'year', 'bpm'})
        stock_ipod.commit_stock_ipod_sync(plan)
        tracks = {row['dbid']: row for row in get_ipod_tracks(read_ipod(self.target))}
        alpha = tracks[101]
        self.assertEqual((alpha['title'], alpha['artist'], alpha['album'], alpha['genre']),
                         ('Displayed title', 'Displayed artist', 'Displayed album', 'Displayed genre'))
        self.assertEqual((alpha['Comment'], alpha['year'], alpha['bpm']), ('Unicode café\nsecond line', 2026, 146))
        self.assertEqual((alpha['rating'], alpha['playCount'], alpha['skipCount']), (80, 3, 1))
        self.assertEqual((alpha['Composer'], alpha['Sort Artist']), ('Keep composer', 'Preserved ordering'))
        self.assertEqual(tracks[103]['Comment'], 'Unselected comment')

    def test_estimated_bpm_is_not_published_as_saved_metadata(self):
        self.infos[0].bpm = 88; self.write_catalog(); self.snapshot = self.read_snapshot()
        plan = self.plan(update_metadata=True, display_rows={'alpha': {'bpm': '175.50', 'bpm_estimated': True}})
        stock_ipod.commit_stock_ipod_sync(plan)
        alpha = next(row for row in get_ipod_tracks(read_ipod(self.target)) if row['dbid'] == 101)
        self.assertEqual(alpha['bpm'], 88)

    def test_pure_cached_diff_uses_displayed_overrides_without_io(self):
        with mock.patch('builtins.open', side_effect=AssertionError('Comparison IO')), \
                mock.patch.object(Path, 'stat', side_effect=AssertionError('Comparison stat')):
            diff = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
                display_rows={'alpha': {'title': 'Desired title', 'artist': 'Desired artist', 'album': 'Desired album'}})
        self.assertTrue(diff.needs_sync)
        self.assertEqual({change.field for change in diff.changes}, {'title', 'artist', 'album'})
        self.assertEqual({change.local_id for change in diff.changes}, {'alpha', 'beta'})
        self.assertEqual([(change.field, change.before, change.after)
                          for change in diff.changes if change.local_id == 'beta'],
                         [('album', '', 'SoundBox')])

    def test_backlog_changed_both_native_fields_uses_exact_original_identity_labels(self):
        from soundbox import alias_intents, metadata_provenance, source_truth
        track = self.library.tracks_by_id['alpha']
        track.name = 'New native title'; track.artist = 'New native artist'
        origin = dict(scope=source_truth.scope(self.library), local_id='alpha', native_id=track.native_id,
                      uuid=track.source_uuid, path=str(track.path), automatic='Artist A', automatic_title='Alpha',
                      native_title=track.name, native_artist=track.artist)
        origin = metadata_provenance.seed(origin, title='Alpha', artist='Artist A', display_artist='Artist A')
        key = alias_intents.key(origin['scope'], origin)
        labels = catalog_match_labels(self.library, [self.playlist], {key: origin})
        displayed = {'alpha': {'title': 'Displayed title', 'artist': 'Displayed artist'}}
        diff = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
                                     display_rows=displayed, match_labels=labels)
        self.assertEqual({row.local_id for row in diff.recognition[0].tracks}, {'alpha', 'beta'})
        plan = self.plan(update_metadata=True, display_rows=displayed, match_labels=labels)
        stock_ipod.commit_stock_ipod_sync(plan)
        alpha = next(row for row in self.read_snapshot().tracks if row.dbid == 101)
        self.assertEqual((alpha.title, alpha.artist), ('Displayed title', 'Displayed artist'))
        wrong = {key: {**origin, 'uuid': 'another-identity'}}
        self.assertEqual(catalog_match_labels(self.library, [self.playlist], wrong), {})

    def test_witness_supports_later_edit_and_clean_art_hint_without_media_authority(self):
        first = self.plan(update_metadata=True, display_rows={'alpha': {'title': 'First title', 'artist': 'First artist'}})
        stock_ipod.commit_stock_ipod_sync(first); self.snapshot = self.read_snapshot()
        witness = first.catalog_witness
        self.assertEqual(witness.database_sha256, self.snapshot.database_sha256)
        artwork = {row.local_id: row.artwork_key for row in witness.rows}
        first_rows = {'alpha': {'title': 'First title', 'artist': 'First artist'}}
        clean = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
            display_rows=first_rows, catalog_witness=witness, artwork_keys=artwork)
        self.assertFalse(clean.needs_sync)
        next_rows = {'alpha': {'title': 'Second title', 'artist': 'Second artist'}}
        dirty = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
            display_rows=next_rows, catalog_witness=witness, artwork_keys=artwork)
        self.assertEqual({change.field for change in dirty.changes}, {'title', 'artist'})
        second = self.plan(update_metadata=True, display_rows=next_rows, catalog_witness=witness)
        stock_ipod.commit_stock_ipod_sync(second)
        self.assertEqual(next(row.title for row in self.read_snapshot().tracks if row.dbid == 101), 'Second title')
        self.assertEqual(second.shadow_sources, {})

    def test_wrong_source_or_device_witness_cannot_restore_an_unmatched_identity(self):
        plan = self.plan(update_metadata=True, display_rows={'alpha': {'title': 'Saved title', 'artist': 'Saved artist'}})
        stock_ipod.commit_stock_ipod_sync(plan); self.snapshot = self.read_snapshot()
        self.library.tracks_by_id['alpha'].name = 'Later title'
        self.library.tracks_by_id['alpha'].artist = 'Later artist'
        for witness in (replace(plan.catalog_witness, device_key='different-volume'),
                        replace(plan.catalog_witness, source=('/different/source', 'unknown', ''))):
            with self.subTest(witness=witness):
                diff = compare_stock_catalog(self.library, [self.playlist], self.snapshot, catalog_witness=witness)
                self.assertIn('alpha', diff.recognition[0].unmatched_track_ids)

    def test_changed_art_key_or_artwork_catalog_is_sync_hint(self):
        plan = self.plan(update_metadata=True); stock_ipod.commit_stock_ipod_sync(plan)
        self.snapshot = self.read_snapshot()
        diff = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
            catalog_witness=plan.catalog_witness, artwork_keys={'alpha': 'new-cover-key'})
        self.assertEqual(diff.artwork_track_ids, ('alpha',))
        witness = replace(plan.catalog_witness, artwork_database_sha256='f' * 64)
        keys = {row.local_id: row.artwork_key for row in witness.rows}
        self.assertTrue(compare_stock_catalog(self.library, [self.playlist], self.snapshot,
            catalog_witness=witness, artwork_keys=keys).needs_sync)

    def test_invalid_supported_metadata_refuses_before_commit(self):
        before = (self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        for row in ({'year': 'bad year'}, {'bpm': 'Infinity'}, {'comments': 'null\0text'}):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    self.plan(update_metadata=True, display_rows={'alpha': row})
        self.assertEqual((self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes(), before)

    def test_unmatched_song_art_does_not_leak_to_device_position(self):
        self.library.tracks_by_id["alpha"].name = "Different recording"
        plan = self.plan()
        stock_ipod.commit_stock_ipod_sync(plan)
        tracks = {row["dbid"]: row for row in get_ipod_tracks(read_ipod(self.target))}
        self.assertEqual(int(tracks[101].get("mhiiLink") or 0), 0)
        self.assertGreater(tracks[102]["mhiiLink"], 0)
        self.assertEqual(plan.summary()["catalog_unmatched_tracks"], 1)

    def test_indexed_m4a_and_opaque_codec_sync_without_audio_and_preserve_format_bytes(self):
        for codec, raw_format in (('m4a', None), ('alac', None), ('m4a', b'Z9!?')):
            with self.subTest(codec=codec, raw_format=raw_format):
                self.infos[1].location = ':iPod_Control:Music:F00:102.m4a'
                self.infos[1].filetype = codec
                self.write_catalog()
                database = self.target / 'iPod_Control/iTunes/iTunesDB'
                before = bytearray(database.read_bytes())
                offset, _ = stock_ipod._track_header_offsets(before)[102]
                if raw_format:
                    before[offset + 0x18:offset + 0x1C] = raw_format
                    before[offset + 0xCC:offset + 0xD0] = b'ABCD'
                    database.write_bytes(before)
                self.snapshot = self.read_snapshot()
                with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('audio read')), \
                        mock.patch.object(stock_ipod, '_stock_media_omission', side_effect=AssertionError('codec probe')):
                    plan = self.plan(update_metadata=True, title_overrides={'beta': 'Curated AAC title'})
                    stock_ipod.commit_stock_ipod_sync(plan)
                output = database.read_bytes(); out_offset, _ = stock_ipod._track_header_offsets(output)[102]
                self.assertEqual(output[out_offset + 0x18:out_offset + 0x20], before[offset + 0x18:offset + 0x20])
                self.assertEqual(output[out_offset + 0xCC:out_offset + 0xD0], before[offset + 0xCC:offset + 0xD0])
                tracks = {row['dbid']: row for row in get_ipod_tracks(read_ipod(self.target))}
                self.assertGreater(tracks[102]['mhiiLink'], 0)
                self.assertEqual(tracks[102]['title'], 'Curated AAC title')
                self.assertFalse((self.target / 'iPod_Control/Music').exists())

    def add_new_wave(self, local='new'):
        self.source.mkdir(parents=True, exist_ok=True)
        path = self.source / f'{local}.wav'
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(2); handle.setsampwidth(2); handle.setframerate(44100)
            handle.writeframes(b'\0\0\0\0' * 441)
        self.library.tracks_by_id[local] = Track(local, f'New {local}', 'New Artist', path.as_uri())
        return path.resolve()

    def test_incremental_bootstrap_copies_only_new_song_and_updates_selected_order(self):
        new = self.add_new_wave()
        self.playlist.keys = ['alpha', 'new', 'beta']
        old_audio = {row.dbid: row.location for row in self.snapshot.tracks}
        original_acquire = stock_ipod.acquire_source
        acquired = []
        def acquire(contract, *args, **kwargs):
            acquired.append(contract.canonical)
            self.assertEqual(contract.canonical, new)
            return original_acquire(contract, *args, **kwargs)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=acquire), \
                mock.patch.object(Path, 'glob', side_effect=AssertionError('full media inventory')):
            plan = self.plan(update_metadata=True, incremental=True)
            self.assertEqual(len(plan.copies), 1)
            self.assertEqual(plan.reused_track_count, 2)
            self.assertTrue(plan.incremental_catalog)
            self.assertFalse(plan.catalog_only)
            self.assertEqual(plan.shadow_sources, {})
            result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(result.copied_tracks, 1)
        self.assertEqual(acquired, [new, new])
        snapshot = self.read_snapshot()
        rows = {row.dbid: row for row in snapshot.tracks}
        for dbid, location in old_audio.items():
            self.assertEqual(rows[dbid].location, location)
            self.assertFalse((self.target / Path(*location.split(':')[1:])).exists())
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual([row.dbid for row in favorites.entries], [101, plan.copies[0].dbid, 102])
        other = next(row for row in snapshot.playlists if row.playlist_id == 902)
        self.assertEqual([row.dbid for row in other.entries], [103])
        self.assertEqual(plan.catalog_witness.bindings[0].device_playlist_id, 901)

    def test_incremental_binding_survives_count_rename_and_reference_removal_without_audio_delete(self):
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first)
        self.snapshot = self.read_snapshot()
        self.playlist.name = 'Favorites renamed'; self.playlist.keys = ['beta']
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('retained audio')):
            plan = self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)
            self.assertFalse(plan.copies or plan.rewrites or plan.media_deletes)
            stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.read_snapshot()
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual((favorites.title, [row.dbid for row in favorites.entries]), ('Favorites renamed', [102]))
        self.assertEqual({row.dbid for row in snapshot.tracks}, {101, 102, 103})
        self.assertFalse((self.target / 'iPod_Control/Music').exists())

    def test_incremental_unknown_entries_preserve_membership_and_remain_dirty(self):
        self.library.tracks_by_id['alpha'].name = 'Cannot identify old Alpha'
        self.playlist.keys = ['alpha', 'beta']
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('ambiguous audio')):
            plan = self.plan(update_metadata=True, incremental=True,
                             title_overrides={'beta': 'Known Beta updated'})
            self.assertTrue(plan.catalog_warning)
            self.assertFalse(plan.copies)
            stock_ipod.commit_stock_ipod_sync(plan)
        self.snapshot = self.read_snapshot()
        favorites = next(row for row in self.snapshot.playlists if row.playlist_id == 901)
        self.assertEqual([row.dbid for row in favorites.entries], [102, 101])
        comparison = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
            incremental=True, catalog_witness=plan.catalog_witness)
        self.assertTrue(comparison.needs_sync)
        self.assertFalse(comparison.playlist_changes[0].membership_safe)

    def test_incremental_changed_new_source_refuses_before_device_write(self):
        new = self.add_new_wave(); self.playlist.keys.append('new')
        before = (self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        plan = self.plan(update_metadata=True, incremental=True)
        new.write_bytes(new.read_bytes() + b'changed')
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'Source changed'):
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual((self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes(), before)
        self.assertFalse((self.target / plan.copies[0].relative_path).exists())

    def test_incremental_database_failure_rolls_back_only_the_new_media(self):
        self.add_new_wave(); self.playlist.keys.append('new')
        before = (self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        plan = self.plan(update_metadata=True, incremental=True)
        original = DeviceSession.atomic_write
        def write(session, destination, data):
            if Path(destination).name == 'iTunesDB':
                raise OSError('generated publication failure')
            return original(session, destination, data)
        with mock.patch.object(DeviceSession, 'atomic_write', write):
            with self.assertRaisesRegex(OSError, 'generated publication failure'):
                stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual((self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes(), before)
        self.assertFalse((self.target / plan.copies[0].relative_path).exists())

    def test_unknown_hardware_metadata_sync_preserves_art_without_guessing_profile(self):
        plan = stock_ipod.plan_stock_ipod_catalog_update(
            self.library, [self.playlist], self.target, snapshot=self.snapshot,
            model_number='', filesystem='fat32', art_cache=self.art, stock_art_cache=self.stock_art,
            update_metadata=True, title_overrides={'alpha': 'Effective Alpha'})
        self.assertTrue(plan.stock_format.writable)
        self.assertFalse(plan.stock_format.supports_artwork)
        self.assertIn('unknown', plan.artwork_warning)
        self.assertIsNone(plan.rendered_artwork)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(result.artwork_warning, plan.artwork_warning)
        tracks = {row['dbid']: row for row in get_ipod_tracks(read_ipod(self.target))}
        self.assertEqual(tracks[101]['title'], 'Effective Alpha')

    def test_observed_artwork_format_supports_unknown_hardware_without_model_assumption(self):
        first = self.plan(); stock_ipod.commit_stock_ipod_sync(first)
        self.snapshot = self.read_snapshot()
        plan = stock_ipod.plan_stock_ipod_catalog_update(
            self.library, [self.playlist], self.target, snapshot=self.snapshot,
            model_number='', filesystem='fat32', art_cache=self.art, stock_art_cache=self.stock_art,
            update_metadata=True, title_overrides={'alpha': 'Effective Alpha'})
        self.assertTrue(plan.stock_format.supports_artwork)
        self.assertFalse(plan.artwork_warning)
        self.assertIsNotNone(plan.rendered_artwork)

    def test_incremental_native_playlist_uuid_change_invalidates_saved_association(self):
        self.library.source_kind = 'rekordbox-db'
        self.library.source_playlist_catalog = {'nodes': [{'ID': 'favorites', 'UUID': 'native-one',
                                                        'rb_local_deleted': 0}], 'members': []}
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first); self.snapshot = self.read_snapshot()
        self.assertEqual(first.catalog_witness.bindings[0].uuid, 'native-one')
        self.library.source_playlist_catalog['nodes'][0]['UUID'] = 'different-playlist'
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('wrong source')):
            with self.assertRaisesRegex(stock_ipod.StockIpodError, 'association'):
                self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)

    def test_manual_212_to_214_then_bound_214_to_216_reads_only_four_new_sources(self):
        self.infos = [TrackInfo(title=f'Title {index}', artist=f'Artist {index}',
            location=f':iPod_Control:Music:F00:{1000 + index}.m4a', filetype='m4a',
            size=5000 + index, length=10000, dbid=1000 + index, rating=60, play_count=9)
            for index in range(212)]
        self.playlists = [PlaylistInfo('Favorites', [info.dbid for info in reversed(self.infos)], playlist_id=901)]
        self.write_catalog()
        self.library.tracks_by_id = {str(index): Track(str(index), f'Title {index}', f'Artist {index}',
            (self.source / f'{index}.m4a').as_uri(), total_time=10, size=5000 + index)
            for index in range(212)}
        self.playlist.keys = list(self.library.tracks_by_id)
        self.snapshot = self.read_snapshot()
        acquired = []
        original_acquire = stock_ipod.acquire_source
        def acquire(contract, *args, **kwargs):
            self.assertTrue(contract.canonical.name.startswith('new'))
            acquired.append(contract.canonical)
            return original_acquire(contract, *args, **kwargs)
        witness = None
        for suffix in ('a', 'b'):
            for index in range(2):
                local = f'new-{suffix}-{index}'
                self.add_new_wave(local); self.playlist.keys.append(local)
            with mock.patch.object(stock_ipod, 'acquire_source', side_effect=acquire), \
                    mock.patch.object(Path, 'glob', side_effect=AssertionError('media scan')), \
                    mock.patch.object(Path, 'rglob', side_effect=AssertionError('media scan')):
                plan = self.plan(update_metadata=True, incremental=True, catalog_witness=witness)
                self.assertEqual(len(plan.copies), 2)
                self.assertEqual(plan.reused_track_count, 212 if suffix == 'a' else 214)
                self.assertFalse(plan.rewrites or plan.media_deletes or plan.shadow_sources)
                stock_ipod.commit_stock_ipod_sync(plan)
            witness = plan.catalog_witness; self.snapshot = self.read_snapshot()
            comparison = compare_stock_catalog(self.library, [self.playlist], self.snapshot,
                incremental=True, catalog_witness=witness)
            self.assertFalse(comparison.needs_sync)
            self.assertEqual(len(self.snapshot.tracks), 214 if suffix == 'a' else 216)
        self.assertEqual(len(acquired), 8)  # acquisition at plan and verified commit only
        for row in self.snapshot.tracks:
            if row.dbid < 1212:
                self.assertEqual((row.rating, row.play_count), (60, 9))

    def test_catalog_noop_witness_publication_rechecks_exact_native_catalog(self):
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first); self.snapshot = self.read_snapshot()
        noop = self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)
        self.assertFalse(noop.requires_commit)
        stock_ipod.verify_stock_ipod_catalog_noop(noop)
        database = self.target / 'iPod_Control/iTunes/iTunesDB'
        database.write_bytes(database.read_bytes() + b'changed')
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'changed after comparison'):
            stock_ipod.verify_stock_ipod_catalog_noop(noop)

    def test_incremental_completion_counts_new_metadata_art_and_playlist_change_exactly(self):
        self.add_new_wave(); self.playlist.keys.append('new')
        plan = self.plan(update_metadata=True, incremental=True, title_overrides={'alpha': 'Curated Alpha'})
        self.assertEqual(plan.catalog_playlist_updates, (901,))
        self.assertEqual(plan.catalog_artwork_track_count, 2)
        self.assertEqual(plan.summary()['catalog_unmatched_tracks'], 0)
        result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual((result.copied_tracks, result.reused_tracks, result.metadata_updates,
                          result.catalog_playlist_updates, result.catalog_artwork_updates), (1, 2, 2, 1, 2))
        self.assertEqual([(row.dbid, row.fields) for row in plan.metadata_updates],
                         [(101, ('title', 'album')), (102, ('album',))])

    def test_new_native_identity_cannot_reuse_a_bound_old_song_by_colliding_labels(self):
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first); self.snapshot = self.read_snapshot()
        self.add_new_wave('replacement')
        replacement = self.library.tracks_by_id['replacement']
        replacement.name = 'Alpha'; replacement.artist = 'Artist A'
        self.playlist.keys = ['replacement', 'beta']
        plan = self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)
        self.assertEqual(len(plan.copies), 1)
        self.assertNotEqual(plan.copies[0].dbid, 101)
        stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.read_snapshot()
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual([row.dbid for row in favorites.entries], [plan.copies[0].dbid, 102])
        self.assertIn(101, {row.dbid for row in snapshot.tracks})

    def test_art_completion_counts_only_changed_cover_after_unrelated_database_change(self):
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first)
        database = self.target / 'iPod_Control/iTunes/iTunesDB'
        content = bytearray(database.read_bytes()); content[0x60] ^= 1; database.write_bytes(content)
        self.snapshot = self.read_snapshot()
        Image.new('RGB', (240, 240), 'yellow').save(self.images['alpha'])
        plan = self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)
        self.assertEqual(plan.catalog_artwork_track_count, 1)
        self.assertEqual(plan.rendered_artwork.updated_dbids, (101,))

    def test_incremental_preserves_master_and_other_playlist_identity_flags_and_timestamps(self):
        database = self.target / 'iPod_Control/iTunes/iTunesDB'
        before = bytearray(database.read_bytes())
        for offset, length in stock_ipod._catalog_playlist_headers(before).values():
            if length >= 0x60:
                before[offset + 0x5C:offset + 0x60] = b'keep'
        database.write_bytes(before); self.snapshot = self.read_snapshot()
        self.add_new_wave(); self.playlist.keys.append('new')
        plan = self.plan(update_metadata=True, incremental=True)
        output = plan.rendered_database
        headers = stock_ipod._catalog_playlist_headers(output)
        for key, (offset, length) in stock_ipod._catalog_playlist_headers(before).items():
            after, new_length = headers[key]
            self.assertEqual(new_length, length)
            self.assertEqual(before[offset + 0x14:offset + 0x28], output[after + 0x14:after + 0x28])
            self.assertEqual(before[offset + 0x2A:offset + length], output[after + 0x2A:after + length])
        stock_ipod.commit_stock_ipod_sync(plan)

    def test_bound_reference_removal_after_source_collection_removal_keeps_foreign_audio(self):
        first = self.plan(update_metadata=True, incremental=True)
        stock_ipod.commit_stock_ipod_sync(first); self.snapshot = self.read_snapshot()
        self.playlist.keys = ['beta']; del self.library.tracks_by_id['alpha']
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('removed source audio')):
            plan = self.plan(update_metadata=True, incremental=True, catalog_witness=first.catalog_witness)
            self.assertFalse(plan.copies or plan.media_deletes or plan.rewrites)
            stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.read_snapshot()
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual([row.dbid for row in favorites.entries], [102])
        self.assertEqual({row.dbid for row in snapshot.tracks}, {101, 102, 103})

    def test_layout_change_after_incremental_plan_refuses_before_any_new_audio_copy(self):
        self.add_new_wave(); self.playlist.keys.append('new')
        plan = self.plan(update_metadata=True, incremental=True)
        database = self.target / 'iPod_Control/iTunes/iTunesDB'
        before = database.read_bytes()
        (database.parent / 'iTunesCDB').write_bytes(b'new incompatible layout')
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'iTunesCDB'):
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse((self.target / plan.copies[0].relative_path).exists())

    def test_blank_artist_filename_fallback_requires_unique_positive_cached_duration(self):
        self.infos[0].artist = ''; self.write_catalog(); self.snapshot = self.read_snapshot()
        self.library.tracks_by_id['alpha'].name = 'Curated source title'
        self.assertEqual({row.local_id: row.dbid for row in self.recognize().tracks}['alpha'], 101)
        for source_duration, device_duration, artist in ((0, 10000, ''), (10, 0, ''),
                (12, 10000, ''), (10, 10000, 'Different artist')):
            with self.subTest(source_duration=source_duration, device_duration=device_duration, artist=artist):
                self.library.tracks_by_id['alpha'].total_time = source_duration
                self.snapshot = replace(self.snapshot, tracks=tuple(replace(row, length_ms=device_duration,
                    artist=artist) if row.dbid == 101 else row for row in self.snapshot.tracks))
                self.assertIn('alpha', self.recognize().unmatched_track_ids)

    def test_blank_artist_filename_fallback_refuses_normalization_collisions_and_claimed_dbid(self):
        self.infos[0].artist = ''; self.write_catalog(); self.snapshot = self.read_snapshot()
        self.library.tracks_by_id['alpha'].name = 'Curated source title'
        self.library.tracks_by_id['beta'].location_uri = (self.source/'ALPHA.wav').as_uri()
        self.assertIn('alpha', self.recognize().unmatched_track_ids)
        self.library.tracks_by_id['beta'].location_uri = (self.source/'beta.wav').as_uri()
        self.snapshot = replace(self.snapshot, tracks=tuple(replace(row, title='ALPHA', artist='')
            if row.dbid == 102 else row for row in self.snapshot.tracks))
        self.assertIn('alpha', self.recognize().unmatched_track_ids)
        self.snapshot = self.read_snapshot()
        self.library.tracks_by_id['beta'].name = 'Alpha'; self.library.tracks_by_id['beta'].artist = ''
        self.assertIn('alpha', self.recognize().unmatched_track_ids)
        self.assertEqual({row.local_id: row.dbid for row in self.recognize().tracks}['beta'], 101)

    def test_changed_catalog_refuses_before_any_audio_or_target_write(self):
        self.playlists[0] = PlaylistInfo("Favorites renamed", [102, 101], playlist_id=901)
        self.write_catalog()
        before = (self.target / "iPod_Control/iTunes/iTunesDB").read_bytes()
        with self.assertRaisesRegex(stock_ipod.StockIpodError, "changed since"):
            self.plan()
        self.assertEqual((self.target / "iPod_Control/iTunes/iTunesDB").read_bytes(), before)

    def test_no_local_cover_is_an_exact_noop_without_new_sync_baseline(self):
        self.images.clear()
        before = (self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        plan = self.plan()
        self.assertFalse(plan.requires_commit)
        self.assertEqual(plan.rendered_database, before)
        self.assertIsNone(plan.shadow_store)
        self.assertFalse((self.target / '.soundbox').exists())

    def test_smart_rules_and_pending_playback_survive_catalog_art_update(self):
        import struct
        self.smart = [PlaylistInfo('Music', [], playlist_id=999, mhsd5_type=4,
                                  smart_prefs=SmartPlaylistPrefs(), smart_rules=SmartPlaylistRules())]
        self.write_catalog()
        before = read_ipod(self.target)['mhlp_smart'][0]
        playback = (b'mhdp' + struct.pack('<III', 96, 28, 3) + bytes(80)
                    + struct.pack('<IIIIIII', 2, 0, 0, 0, 0, 0, 0) + bytes(56))
        (self.target / 'iPod_Control/iTunes/Play Counts').write_bytes(playback)
        self.snapshot = self.read_snapshot()
        plan = self.plan(); stock_ipod.commit_stock_ipod_sync(plan)
        output = read_ipod(self.target)
        after = output['mhlp_smart'][0]
        for key in ('Title', 'mhsd5Type', 'smartPlaylistData', 'smartPlaylistRules'):
            self.assertEqual(after.get(key), before.get(key))
        alpha = next(row for row in get_ipod_tracks(output) if row['dbid'] == 101)
        self.assertEqual(alpha['playCount'], 5)
        self.assertFalse((self.target / 'iPod_Control/iTunes/Play Counts').exists())

    def test_incremental_update_preserves_unselected_art_slot(self):
        other = Playlist("Other", [], ["other"], pid="other")
        first = stock_ipod.plan_stock_ipod_catalog_update(
            self.library, [other], self.target, snapshot=self.snapshot,
            model_number="MA446", filesystem="fat32", art_cache=self.art,
            stock_art_cache=self.stock_art, usb_family='iPod Video')
        stock_ipod.commit_stock_ipod_sync(first)
        art_dir = self.target / "iPod_Control/Artwork"
        old_slots = read_stock_artwork_slots(art_dir)[1]
        old = next(row for row in old_slots.values() if row.song_id == 103)
        old_pixels = {path.name: path.read_bytes() for path in art_dir.glob("*.ithmb")}
        self.snapshot = self.read_snapshot()
        plan = self.plan()
        self.assertTrue(plan.rendered_artwork.incremental)
        stock_ipod.commit_stock_ipod_sync(plan)
        new = next(row for row in read_stock_artwork_slots(art_dir)[1].values() if row.song_id == 103)
        self.assertEqual(new, old)
        for name, pixels in old_pixels.items():
            self.assertEqual((art_dir / name).read_bytes()[:len(pixels)], pixels)

    def test_artwork_replaced_after_render_is_not_adopted_as_plan_baseline(self):
        first = self.plan(); stock_ipod.commit_stock_ipod_sync(first)
        self.snapshot = self.read_snapshot()
        database = self.target / 'iPod_Control/iTunes/iTunesDB'
        artwork = self.target / 'iPod_Control/Artwork/ArtworkDB'
        database_before = database.read_bytes()
        original = artwork.read_bytes()
        replacement = original + b'concurrent catalog replacement'
        render = stock_ipod.render_incremental_stock_artwork
        def changed_after_render(*args, **kwargs):
            self.assertEqual(kwargs['expected_artwork_database'], original)
            result = render(*args, **kwargs)
            artwork.write_bytes(replacement)
            return result
        with mock.patch.object(stock_ipod, 'render_incremental_stock_artwork',
                               side_effect=changed_after_render):
            with self.assertRaisesRegex(stock_ipod.StockIpodError, 'changed during artwork'):
                self.plan()
        self.assertEqual(database.read_bytes(), database_before)
        self.assertEqual(artwork.read_bytes(), replacement)

    def dual_plan(self, **options):
        (self.target / ".rockbox").mkdir(exist_ok=True)
        return dual_ipod.plan_dual_ipod_catalog_update(
            self.library, [self.playlist], self.target, snapshot=self.snapshot,
            model_number="MA446", filesystem="fat32", rockbox_media_cache=RockboxMediaCache(self.base / "staging"),
            art_cache=self.art, stock_art_cache=self.stock_art, **options, usb_family='iPod Video')

    def test_dual_precomposite_art_keeps_manifest_absent(self):
        plan = self.dual_plan()
        self.assertTrue(plan.catalog_only)
        result = dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual(result.manifest_sha256, "")
        self.assertFalse((self.target / ".soundbox/manifest.json").exists())
        self.assertFalse((self.target / "iPod_Control/Music").exists())

    def test_dual_owned_manifest_and_media_inventory_stay_exact(self):
        payload = json.dumps(dict(schema=3, profile=dual_ipod.DUAL_EXPORT_PROFILE,
                                  generation=7, completed_generation=7, playlists={}, files={
                                      "/Music/foreign.flac": dict(kind="rockbox-media", owned=False,
                                                                  opaque="preserve")}), indent=3).encode()
        path = self.target / ".soundbox/manifest.json"; path.parent.mkdir(); path.write_bytes(payload)
        plan = self.dual_plan()
        result = dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(result.manifest_sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(plan.generation, 7)
        self.assertFalse(plan.manifest_changed)

    def test_dual_committed_cleanup_failure_stays_committed(self):
        plan = self.dual_plan()
        database = self.target / "iPod_Control/iTunes/iTunesDB"
        before = database.read_bytes()
        real = dual_ipod.record_outcome
        def fail_executing(session, recovery, status, **kwargs):
            if status == "committed":
                raise OSError("Injected post-stock bookkeeping failure")
            return real(session, recovery, status, **kwargs)
        # A post-commit cleanup failure remains committed, with no false rollback.
        with mock.patch.object(dual_ipod, "record_outcome", side_effect=fail_executing):
            with self.assertRaises(stock_ipod.CommittedOperationError):
                dual_ipod.commit_dual_ipod_sync(plan)
        self.assertNotEqual(database.read_bytes(), before)
        self.assertFalse((self.target / ".soundbox/manifest.json").exists())

    def test_dual_catalog_failure_after_stock_rolls_back_exact_database(self):
        plan = self.dual_plan()
        database = self.target / "iPod_Control/iTunes/iTunesDB"
        before = database.read_bytes(); state = dict(stock_done=False, failed=False)
        commit = dual_ipod.commit_stock_ipod_sync
        validate = DeviceSession.revalidate
        def committed(*args, **kwargs):
            result = commit(*args, **kwargs); state['stock_done'] = True
            return result
        def revalidate(session):
            if state['stock_done'] and not state['failed']:
                state['failed'] = True
                raise OSError("Injected composite phase failure")
            return validate(session)
        with mock.patch.object(dual_ipod, "commit_stock_ipod_sync", side_effect=committed), \
                mock.patch.object(DeviceSession, "revalidate", revalidate):
            with self.assertRaisesRegex(OSError, "composite phase") as failure:
                dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual(failure.exception.recovery_status, "rolled back")
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse((self.target / ".soundbox/manifest.json").exists())

    def test_catalog_mode_refuses_injected_audio_mutation(self):
        plan = self.dual_plan()
        tampered = replace(plan, stock=replace(plan.stock, catalog_only=False))
        with self.assertRaisesRegex(dual_ipod.DualIpodError, "catalog-only"):
            dual_ipod.commit_dual_ipod_sync(tampered)


if __name__ == "__main__":
    unittest.main()
