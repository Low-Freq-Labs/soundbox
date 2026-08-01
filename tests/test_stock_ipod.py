"""Offline stock-iPod renderer and guarded-commit tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import wave

from PIL import Image

from clickwheel.ipod import get_ipod_tracks, read_ipod
from clickwheel.ipod.ipod_models import capabilities_for_family_gen
from clickwheel.ipod.itunesdb_writer import (
    ChecksumType,
    PlaylistInfo,
    SmartPlaylistPrefs,
    SmartPlaylistRule,
    SmartPlaylistRules,
    TrackInfo,
    write_itunesdb,
)

from soundbox.library import Library, Playlist, Track
from soundbox.stock_ipod import (
    StockIpodError,
    commit_stock_ipod_playlist_delete,
    commit_stock_ipod_sync,
    plan_stock_ipod_playlist_delete,
    plan_stock_ipod_sync,
)
from soundbox.stock_ipod_info import (
    confirmed_model_from_recovery,
    model_from_serial,
    read_stock_ipod_snapshot,
    stock_ipod_device_key,
)
from soundbox.stock_artwork import StockArtworkCache
from soundbox.sync import DeviceSession


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wave(path: Path, frames: int, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = struct.pack("<h", value)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44_100)
        out.writeframes(sample * 2 * frames)


def _wave24(path: Path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(3)
        out.setframerate(44_100)
        out.writeframes(b"\x00\x01\x00\x00\x01\x00" * frames)


class _FixtureArtCache:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths

    def image_path(self, track_id: str) -> Path | None:
        return self.paths.get(track_id)


class StockIpodSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ipod = self.base / "IPOD"
        self.sources = self.base / "sources"
        self.ipod.mkdir()
        self.sources.mkdir()

        existing_media = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        _wave(existing_media, 441, 100)
        self.existing_source = self.sources / "existing.wav"
        shutil.copy2(existing_media, self.existing_source)
        self.new_source = self.sources / "new.wav"
        _wave(self.new_source, 882, 200)

        itunes = self.ipod / "iPod_Control/iTunes"
        itunes.mkdir(parents=True)
        capabilities = capabilities_for_family_gen("iPod Video", "5.5th Gen")
        self.assertIsNotNone(capabilities)
        existing = TrackInfo(
            title="Existing",
            artist="Fixture Artist",
            location=":iPod_Control:Music:F00:AAAA.wav",
            size=existing_media.stat().st_size,
            length=10,
            filetype="wav",
            sample_rate=44_100,
            dbid=101,
        )
        regular = PlaylistInfo("Keep Me", [101], playlist_id=202)
        smart = PlaylistInfo(
            "Music",
            [],
            playlist_id=303,
            mhsd5_type=4,
            smart_prefs=SmartPlaylistPrefs(),
            smart_rules=SmartPlaylistRules(rules=[SmartPlaylistRule(
                field_id=0x3C,
                action_id=0x400,
                from_value=1,
                from_units=1,
                to_value=1,
                to_units=1,
            )]),
            # Regression oracle: opaque Apple settings may contain a byte-perfect
            # fake MHOD-51/SLst sequence. Header preservation must walk MHSD-5 /
            # MHLP / MHYP children, never count raw magic across the whole file.
            raw_mhod102=(
                b"mhod" + struct.pack("<IIIII", 24, 160, 51, 0, 0)
                + b"SLst" + bytes(132)
            ),
        )
        self.assertTrue(write_itunesdb(
            str(self.ipod),
            [existing],
            backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=[regular],
            smart_playlists=[smart],
            capabilities=capabilities,
        ))
        # One pending device play, indexed to the one existing MHIT.
        header = b"mhdp" + struct.pack("<III", 96, 28, 1) + bytes(80)
        entry = struct.pack("<IIIIIII", 2, 0, 0, 0, 0, 0, 0)
        (itunes / "Play Counts").write_bytes(header + entry)

        tracks = {
            "old": Track(
                "old", "Existing", "Fixture Artist",
                self.existing_source.as_uri(), total_time=1),
            "new": Track(
                "new", "New", "Fixture Artist",
                self.new_source.as_uri(), total_time=1),
        }
        self.playlist = Playlist(
            "SoundBox Test", [], ["old", "new"], pid="stock-test")
        self.library = Library(tracks, [self.playlist], self.sources)

        art = self.base / "curated.jpg"
        Image.new("RGB", (300, 300), (110, 30, 190)).save(
            art, "JPEG", quality=90)
        self.art_cache = _FixtureArtCache({"old": art, "new": art})
        self.stock_art_cache = StockArtworkCache(self.base / "stock-art-cache")

    @staticmethod
    def _snapshot(root: Path):
        return sorted(
            (str(path.relative_to(root)), path.read_bytes() if path.is_file() else None)
            for path in root.rglob("*")
        )

    def _rewrite_fixture_with_videos_smart_playlist(self):
        capabilities = capabilities_for_family_gen("iPod Video", "5.5th Gen")
        self.assertIsNotNone(capabilities)
        existing_media = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        existing = TrackInfo(
            title="Existing",
            artist="Fixture Artist",
            location=":iPod_Control:Music:F00:AAAA.wav",
            size=existing_media.stat().st_size,
            length=10,
            filetype="wav",
            sample_rate=44_100,
            dbid=101,
        )
        regular = PlaylistInfo("Keep Me", [101], playlist_id=202)

        def smart(name: str, playlist_id: int, dataset_type: int):
            return PlaylistInfo(
                name,
                [],
                playlist_id=playlist_id,
                mhsd5_type=dataset_type,
                smart_prefs=SmartPlaylistPrefs(),
                smart_rules=SmartPlaylistRules(rules=[SmartPlaylistRule(
                    field_id=0x3C,
                    action_id=0x400,
                    from_value=1,
                    from_units=1,
                    to_value=1,
                    to_units=1,
                )]),
            )

        self.assertTrue(write_itunesdb(
            str(self.ipod),
            [existing],
            backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=[regular],
            smart_playlists=[
                smart("Music", 303, 4),
                smart("Videos", 404, 0),
            ],
            capabilities=capabilities,
        ))

    def test_plan_is_read_only_and_preserves_state(self):
        before = self._snapshot(self.ipod)
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446LL/A", filesystem="msdos")
        self.assertEqual(self._snapshot(self.ipod), before)
        self.assertEqual(plan.model_number, "MA446")
        self.assertEqual(plan.reused_track_count, 1)
        self.assertEqual(len(plan.copies), 1)
        self.assertEqual(plan.existing_track_count, 1)
        self.assertEqual(plan.final_track_count, 2)
        self.assertEqual(plan.merged_play_count_entries, 1)

    def test_read_only_device_snapshot_merges_pending_history(self):
        before = self._snapshot(self.ipod)
        snapshot = read_stock_ipod_snapshot(
            self.ipod,
            volume_uuid="fixture-volume",
            filesystem="MS-DOS (FAT32)",
            protocol="USB",
            device_identifier="disk-test",
            writable=True,
            serial_number="8M650V0HV9M",
            serial_source="macOS iPod device record",
            usb_serial="000A270015F42B87",
            firmware_version="1.3",
            firmware_source="macOS iPod device record",
            transport_revision="1.62",
        )
        self.assertEqual(self._snapshot(self.ipod), before)
        self.assertEqual(snapshot.model, "MA446")
        self.assertEqual(snapshot.model_source, "serial suffix V9M")
        self.assertEqual(snapshot.generation, "5.5th generation")
        self.assertEqual(snapshot.firmware_version, "1.3")
        self.assertEqual(snapshot.transport_revision, "1.62")
        self.assertEqual(snapshot.dac_name, "Wolfson WM8758BG")
        self.assertIn("MA446", snapshot.dac_source)
        self.assertEqual(snapshot.pending_playback_records, 1)
        self.assertEqual(snapshot.tracks[0].play_count, 2)
        self.assertEqual(snapshot.tracks[0].pending_plays, 2)
        self.assertIn("Keep Me", [item.title for item in snapshot.playlists])
        self.assertIn("Music", [item.title for item in snapshot.playlists])
        self.assertEqual(
            snapshot.identity_key,
            stock_ipod_device_key(
                "fixture-volume", snapshot.library_persistent_id))

    def test_serial_model_map_is_narrow_and_fail_closed(self):
        self.assertEqual(model_from_serial("8M650V0HV9M"), ("MA446", "V9M"))
        self.assertEqual(model_from_serial("SERIAL-WEE"), ("", "WEE"))

    def test_commit_copies_media_db_last_and_archives_recovery(self):
        source_hashes = (_hash(self.existing_source), _hash(self.new_source))
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos")
        result = commit_stock_ipod_sync(plan)

        self.assertEqual(result.copied_tracks, 1)
        self.assertEqual(result.reused_tracks, 1)
        self.assertTrue((result.recovery_directory / "iTunesDB.before").is_file())
        self.assertTrue((result.recovery_directory / "Play Counts.before").is_file())
        self.assertTrue((result.recovery_directory / "Play Counts.consumed").is_file())
        self.assertFalse((self.ipod / "iPod_Control/iTunes/Play Counts").exists())
        self.assertEqual(confirmed_model_from_recovery(self.ipod), "MA446")
        self.assertEqual(
            (_hash(self.existing_source), _hash(self.new_source)), source_hashes)

        copied = self.ipod / plan.copies[0].relative_path
        self.assertEqual(_hash(copied), plan.copies[0].sha256)
        database = read_ipod(self.ipod)
        tracks = get_ipod_tracks(database)
        self.assertEqual(len(tracks), 2)
        existing = next(item for item in tracks if item["dbid"] == 101)
        self.assertEqual(existing["playCount"], 2)
        self.assertEqual(existing["location"], ":iPod_Control:Music:F00:AAAA.wav")
        smart = database.get("mhlp_smart") or []
        self.assertEqual([item["Title"] for item in smart], ["Music"])
        output_by_id = {item["trackID"]: item["dbid"] for item in tracks}
        playlist = next(
            item for item in database["mhlp"]
            if item.get("Title") == "SoundBox Test")
        self.assertEqual(
            [output_by_id[item["trackID"]] for item in playlist["items"]],
            [101, plan.copies[0].dbid],
        )

    def test_selected_reused_track_gets_soundbox_title_and_artist_only(self):
        source_hash = _hash(self.existing_source)
        before = get_ipod_tracks(read_ipod(self.ipod))[0]
        corrected = Track(
            "old", "Correct Title", "Correct Artist",
            self.existing_source.as_uri(), total_time=1,
        )
        playlist = Playlist("Metadata", [], ["old"])
        library = Library({"old": corrected}, [playlist], self.sources)

        plan = plan_stock_ipod_sync(
            library, [playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        self.assertEqual(len(plan.copies), 0)
        self.assertEqual(len(plan.metadata_updates), 1)
        update = plan.metadata_updates[0]
        self.assertEqual(update.dbid, 101)
        self.assertEqual(
            (update.title_before, update.artist_before),
            ("Existing", "Fixture Artist"),
        )
        self.assertEqual(
            (update.title_after, update.artist_after),
            ("Correct Title", "Correct Artist"),
        )
        self.assertEqual(plan.summary()["metadata_updates"], 1)

        result = commit_stock_ipod_sync(plan)
        self.assertEqual(result.metadata_updates, 1)
        self.assertEqual(_hash(self.existing_source), source_hash)
        track = get_ipod_tracks(read_ipod(self.ipod))[0]
        self.assertEqual(track["dbid"], 101)
        self.assertEqual(track["location"], ":iPod_Control:Music:F00:AAAA.wav")
        self.assertEqual(track["title"], "Correct Title")
        self.assertEqual(track["artist"], "Correct Artist")
        self.assertEqual(track["playCount"], 2)
        for key in (
            "size", "length", "filetype", "sampleRate", "bitrate",
            "rating", "bookmarkTime", "skipCount", "lastSkipped",
        ):
            self.assertEqual(track[key], before[key], key)

    def test_playlist_delete_removes_only_newly_unreferenced_track_and_media(self):
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        new_media = self.ipod / sync_plan.copies[0].relative_path
        commit_stock_ipod_sync(sync_plan)
        database = read_ipod(self.ipod)
        target_playlist = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )

        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(target_playlist["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        self.assertEqual(
            plan.playlist_ids, (int(target_playlist["playlistID"]),))
        self.assertEqual(plan.playlist_names, ("SoundBox Test",))
        self.assertEqual(plan.deleted_track_count, 1)
        self.assertEqual(len(plan.media_deletes), 1)
        self.assertEqual(plan.final_track_count, 1)
        self.assertGreater(plan.bytes_to_delete, 0)
        self.assertTrue(new_media.is_file())

        result = commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_track_records, 1)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertEqual(result.cleanup_failures, ())
        self.assertFalse(new_media.exists())
        after = read_ipod(self.ipod)
        self.assertEqual(
            [raw.get("Title") for raw in after["mhlp"] if not raw.get("isMaster")],
            ["Keep Me"],
        )
        tracks = get_ipod_tracks(after)
        self.assertEqual([raw["dbid"] for raw in tracks], [101])
        self.assertEqual(tracks[0]["location"], ":iPod_Control:Music:F00:AAAA.wav")
        self.assertTrue((result.recovery_directory / "iTunesDB.before").is_file())

    def test_playlist_delete_accepts_multiple_checked_playlists(self):
        collection = Playlist("Collection", [], ["old", "new"])
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist, collection], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        commit_stock_ipod_sync(sync_plan)
        database = read_ipod(self.ipod)
        regular = {
            raw.get("Title"): int(raw.get("playlistID") or 0)
            for raw in database["mhlp"] if not raw.get("isMaster")
        }
        selected_ids = (
            regular["SoundBox Test"],
            regular["Collection"],
        )

        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            list(selected_ids),
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        self.assertEqual(plan.playlist_ids, selected_ids)
        self.assertEqual(
            plan.playlist_names, ("SoundBox Test", "Collection"))
        self.assertEqual(plan.deleted_track_count, 1)
        self.assertEqual(plan.final_track_count, 1)
        self.assertEqual(plan.remaining_playlist_count, 1)

        result = commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.playlists, ("SoundBox Test", "Collection"))
        self.assertEqual(result.deleted_track_records, 1)
        after = read_ipod(self.ipod)
        self.assertEqual(
            [raw.get("Title") for raw in after["mhlp"] if not raw.get("isMaster")],
            ["Keep Me"],
        )

    def test_videos_system_playlist_is_narrowly_deletable_without_media(self):
        self._rewrite_fixture_with_videos_smart_playlist()
        before = read_ipod(self.ipod)
        videos = next(
            raw for raw in before["mhlp_smart"]
            if raw.get("Title") == "Videos")

        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(videos["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )

        self.assertEqual(plan.playlist_names, ("Videos",))
        self.assertEqual(plan.deleted_track_count, 0)
        self.assertEqual(plan.media_deletes, ())
        self.assertEqual(plan.final_track_count, 1)
        self.assertEqual(plan.remaining_playlist_count, 1)
        result = commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_track_records, 0)
        self.assertEqual(result.deleted_media_files, 0)
        after = read_ipod(self.ipod)
        self.assertEqual(
            [raw.get("Title") for raw in after["mhlp_smart"]],
            ["Music"],
        )
        self.assertEqual(
            [raw.get("Title") for raw in after["mhlp"] if not raw.get("isMaster")],
            ["Keep Me"],
        )
        self.assertEqual(len(get_ipod_tracks(after)), 1)

        music = after["mhlp_smart"][0]
        with self.assertRaisesRegex(StockIpodError, "not safely deletable"):
            plan_stock_ipod_playlist_delete(
                self.ipod,
                [int(music["playlistID"])],
                model_number="MA446",
                filesystem="msdos",
                stock_art_cache=self.stock_art_cache,
            )

    def test_collection_playlist_retains_every_track_when_other_playlist_deleted(self):
        collection = Playlist("Collection", [], ["old", "new"])
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist, collection], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        new_media = self.ipod / plan.copies[0].relative_path
        commit_stock_ipod_sync(plan)
        database = read_ipod(self.ipod)
        target = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )

        delete = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(target["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        self.assertEqual(delete.deleted_track_count, 0)
        self.assertEqual(delete.media_deletes, ())
        result = commit_stock_ipod_playlist_delete(delete)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertTrue(new_media.is_file())
        after = read_ipod(self.ipod)
        self.assertEqual(len(get_ipod_tracks(after)), 2)
        self.assertEqual(
            {raw.get("Title") for raw in after["mhlp"] if not raw.get("isMaster")},
            {"Keep Me", "Collection"},
        )

    def test_delete_compacts_remaining_artwork_and_removes_all_art_when_empty(self):
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        commit_stock_ipod_sync(sync_plan)
        database = read_ipod(self.ipod)
        soundbox = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )
        keep = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "Keep Me"
        )

        compact = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(soundbox["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        self.assertIsNotNone(compact.rendered_artwork)
        assert compact.rendered_artwork is not None
        self.assertEqual(compact.rendered_artwork.linked_tracks, 1)
        commit_stock_ipod_playlist_delete(compact)
        artwork = self.ipod / "iPod_Control/Artwork"
        self.assertEqual((artwork / "F1028_1.ithmb").stat().st_size, 20_000)
        self.assertEqual((artwork / "F1029_1.ithmb").stat().st_size, 80_000)

        empty = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(keep["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        self.assertIsNone(empty.rendered_artwork)
        self.assertEqual(len(empty.artwork_delete_paths), 3)
        commit_stock_ipod_playlist_delete(empty)
        self.assertFalse((artwork / "ArtworkDB").exists())
        self.assertFalse((artwork / "F1028_1.ithmb").exists())
        self.assertFalse((artwork / "F1029_1.ithmb").exists())
        self.assertEqual(get_ipod_tracks(read_ipod(self.ipod)), [])

    def test_stale_delete_plan_refuses_before_database_or_media_change(self):
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        new_media = self.ipod / sync_plan.copies[0].relative_path
        commit_stock_ipod_sync(sync_plan)
        database = read_ipod(self.ipod)
        target = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )
        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(target["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        db_path = self.ipod / "iPod_Control/iTunes/iTunesDB"
        db_path.write_bytes(db_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(StockIpodError, "changed after dry-run"):
            commit_stock_ipod_playlist_delete(plan)
        self.assertTrue(new_media.is_file())

    def test_delete_database_failure_restores_artwork_and_keeps_media(self):
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        new_media = self.ipod / sync_plan.copies[0].relative_path
        commit_stock_ipod_sync(sync_plan)
        database = read_ipod(self.ipod)
        target = next(
            raw for raw in database["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )
        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(target["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        db_path = self.ipod / "iPod_Control/iTunes/iTunesDB"
        db_before = db_path.read_bytes()
        artwork = self.ipod / "iPod_Control/Artwork"
        art_before = {
            path.name: path.read_bytes() for path in artwork.iterdir()
            if path.is_file()
        }
        real_write = DeviceSession.atomic_write

        def fail_live_database(session, destination, data):
            if Path(destination).name == "iTunesDB":
                raise OSError("synthetic delete DB failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_live_database):
            with self.assertRaisesRegex(OSError, "delete DB failure"):
                commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(db_path.read_bytes(), db_before)
        self.assertTrue(new_media.is_file())
        self.assertEqual(
            {path.name: path.read_bytes() for path in artwork.iterdir()
             if path.is_file()},
            art_before,
        )

    def test_artwork_plan_and_commit_link_every_track_without_source_mutation(self):
        source_hashes = (_hash(self.existing_source), _hash(self.new_source))
        before = self._snapshot(self.ipod)
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        self.assertEqual(self._snapshot(self.ipod), before)
        self.assertIsNotNone(plan.rendered_artwork)
        assert plan.rendered_artwork is not None
        self.assertEqual(plan.rendered_artwork.linked_tracks, 2)
        self.assertEqual(plan.rendered_artwork.unique_images, 1)
        self.assertEqual(plan.artwork_bytes_to_copy, 100_000 + len(
            plan.rendered_artwork.artwork_db))

        result = commit_stock_ipod_sync(plan)
        self.assertEqual(result.artwork_tracks, 2)
        self.assertEqual(result.unique_artwork, 1)
        artwork = self.ipod / "iPod_Control/Artwork"
        self.assertEqual((artwork / "F1028_1.ithmb").stat().st_size, 20_000)
        self.assertEqual((artwork / "F1029_1.ithmb").stat().st_size, 80_000)
        self.assertTrue((artwork / "ArtworkDB").is_file())
        tracks = get_ipod_tracks(read_ipod(self.ipod))
        self.assertTrue(all(int(track.get("mhiiLink") or 0) for track in tracks))
        self.assertTrue(all(int(track.get("artworkCount") or 0) == 1 for track in tracks))
        self.assertEqual(source_hashes, (_hash(self.existing_source), _hash(self.new_source)))

    def test_database_failure_rolls_back_new_artwork_files(self):
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        before = database.read_bytes()
        real_write = DeviceSession.atomic_write

        def fail_live_database(session, destination, data):
            if Path(destination).name == "iTunesDB":
                raise OSError("synthetic artwork DB-last failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_live_database):
            with self.assertRaisesRegex(OSError, "artwork DB-last failure"):
                commit_stock_ipod_sync(plan)
        self.assertEqual(database.read_bytes(), before)
        artwork = self.ipod / "iPod_Control/Artwork"
        self.assertFalse((artwork / "ArtworkDB").exists())
        self.assertFalse((artwork / "F1028_1.ithmb").exists())
        self.assertFalse((artwork / "F1029_1.ithmb").exists())
        self.assertFalse((self.ipod / plan.copies[0].relative_path).exists())

    def test_existing_artwork_is_recovered_exactly_if_database_commit_fails(self):
        first = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        commit_stock_ipod_sync(first)
        artwork = self.ipod / "iPod_Control/Artwork"
        before = {
            path.name: path.read_bytes()
            for path in artwork.iterdir() if path.is_file()
        }
        Image.new("RGB", (300, 300), (5, 210, 80)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        second = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        database_before = database.read_bytes()
        real_write = DeviceSession.atomic_write

        def fail_live_database(session, destination, data):
            if Path(destination).name == "iTunesDB":
                raise OSError("synthetic replacement failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_live_database):
            with self.assertRaisesRegex(OSError, "replacement failure"):
                commit_stock_ipod_sync(second)
        self.assertEqual(database.read_bytes(), database_before)
        self.assertEqual(
            {path.name: path.read_bytes()
             for path in artwork.iterdir() if path.is_file()},
            before,
        )
        recovery_art = second.target / ".soundbox/stock-ipod-recovery"
        newest = max(recovery_art.iterdir(), key=lambda path: path.stat().st_mtime_ns)
        self.assertTrue((newest / "artwork/ArtworkDB.before").is_file())
        self.assertTrue((newest / "artwork/F1028_1.ithmb.before").is_file())
        self.assertTrue((newest / "artwork/F1029_1.ithmb.before").is_file())

    def test_incomplete_existing_artwork_fails_closed_during_plan(self):
        first = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        commit_stock_ipod_sync(first)
        (self.ipod / "iPod_Control/Artwork/F1029_1.ithmb").unlink()
        with self.assertRaisesRegex(Exception, "incomplete"):
            plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod,
                model_number="MA446", filesystem="msdos",
                stock_art_cache=self.stock_art_cache,
            )

    def test_stale_database_refuses_before_copy(self):
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos")
        db = self.ipod / "iPod_Control/iTunes/iTunesDB"
        db.write_bytes(db.read_bytes() + b"changed")
        with self.assertRaisesRegex(StockIpodError, "changed after dry-run"):
            commit_stock_ipod_sync(plan)
        self.assertFalse((self.ipod / plan.copies[0].relative_path).exists())
        self.assertFalse((self.ipod / ".soundbox").exists())

    def test_database_write_failure_removes_only_new_media(self):
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos")
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        before = database.read_bytes()
        real_write = DeviceSession.atomic_write

        def fail_live_database(session, destination, data):
            if Path(destination).name == "iTunesDB":
                raise OSError("synthetic DB failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_live_database):
            with self.assertRaisesRegex(OSError, "synthetic DB failure"):
                commit_stock_ipod_sync(plan)
        self.assertEqual(database.read_bytes(), before)
        self.assertTrue((self.ipod / "iPod_Control/iTunes/Play Counts").is_file())
        self.assertFalse((self.ipod / plan.copies[0].relative_path).exists())
        self.assertTrue((self.ipod / ".soundbox/stock-ipod-recovery").is_dir())

    def test_unknown_model_fails_and_non_stock_format_is_explicitly_omitted(self):
        with self.assertRaisesRegex(StockIpodError, "allowlist"):
            plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod,
                model_number="MA450", filesystem="msdos")
        bad = self.sources / "bad.flac"
        bad.write_bytes(b"not audio")
        track = Track("bad", "Bad", "", bad.as_uri())
        playlist = Playlist("Bad", [], ["bad"])
        library = Library({"bad": track}, [playlist], self.sources)
        plan = plan_stock_ipod_sync(
            library, [playlist], self.ipod, model_number="MA446",
            filesystem="msdos")
        self.assertEqual(len(plan.omissions), 1)
        self.assertIn("FLAC is not a stock-iPod", plan.omissions[0].reason)
        self.assertEqual(plan.source_track_count, 0)
        self.assertEqual(plan.summary()["selected_tracks"], 1)
        self.assertEqual(plan.summary()["omitted_tracks"], 1)

    def test_24_bit_wav_is_omitted_only_from_stock_plan(self):
        unsafe = self.sources / "loot.wav"
        _wave24(unsafe, 441)
        source_hash = _hash(unsafe)
        track = Track("loot", "Hostage Situation - Loot", "", unsafe.as_uri())
        playlist = Playlist("House", [], ["loot", "new"])
        library = Library(
            {"loot": track, "new": self.library.tracks_by_id["new"]},
            [playlist], self.sources,
        )
        plan = plan_stock_ipod_sync(
            library, [playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )
        self.assertEqual(len(plan.omissions), 1)
        self.assertEqual(plan.omissions[0].track_id, "loot")
        self.assertIn("PCM_24", plan.omissions[0].reason)
        self.assertEqual(plan.source_track_count, 1)
        self.assertEqual(len(plan.copies), 1)
        self.assertEqual(_hash(unsafe), source_hash)

        commit_stock_ipod_sync(plan)
        self.assertEqual(_hash(unsafe), source_hash)
        database = read_ipod(self.ipod)
        output_by_id = {
            item["trackID"]: item["dbid"] for item in get_ipod_tracks(database)
        }
        house = next(
            raw for raw in database["mhlp"] if raw.get("Title") == "House")
        self.assertEqual(len(house["items"]), 1)
        self.assertEqual(
            output_by_id[house["items"][0]["trackID"]],
            plan.copies[0].dbid,
        )

    def test_soundbox_curation_vetoes_precede_stock_format_filter(self):
        unsafe = self.sources / "loot.wav"
        _wave24(unsafe, 441)
        loot = Track(
            "loot", "Hostage Situation - Loot", "", unsafe.as_uri())
        playlist = Playlist(
            "House", [], ["loot", "old", "new"], pid="house")
        library = Library(
            {
                "loot": loot,
                "old": self.library.tracks_by_id["old"],
                "new": self.library.tracks_by_id["new"],
            },
            [playlist],
            self.sources,
            hidden_ids={"loot"},
            removed_by_playlist={"house": {"old"}},
        )

        plan = plan_stock_ipod_sync(
            library, [playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )

        # A Collection delete is global, and a playlist removal is local. Both
        # are SoundBox curation vetoes, so neither reaches stock compatibility
        # checking or appears misleadingly as a firmware omission.
        self.assertEqual(plan.summary()["selected_tracks"], 1)
        self.assertEqual(plan.summary()["source_tracks"], 1)
        self.assertEqual(plan.summary()["omitted_tracks"], 0)
        self.assertEqual(plan.omissions, ())
        self.assertEqual(
            [item.source for item in plan.copies],
            [self.new_source.resolve()],
        )

    def test_unknown_filesystem_and_dual_boot_fail_closed(self):
        with self.assertRaisesRegex(StockIpodError, "confirmed FAT32"):
            plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod,
                model_number="MA446", filesystem="")
        (self.ipod / ".rockbox").mkdir()
        with self.assertRaisesRegex(StockIpodError, "Dual-boot"):
            plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod,
                model_number="MA446", filesystem="msdos")


if __name__ == "__main__":
    unittest.main()
