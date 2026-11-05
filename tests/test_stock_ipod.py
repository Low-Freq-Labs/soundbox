"""Offline stock-iPod renderer and guarded-commit tests."""

from __future__ import annotations

import hashlib
import os
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
from clickwheel.ipod.itunesdb_writer.mhyp_writer import write_mhyp

from soundbox import stock_ipod
from soundbox.library import Library, Playlist, Track
from soundbox.stock_ipod import (
    StockIpodError,
    commit_stock_ipod_playlist_delete,
    commit_stock_ipod_sync,
    inspect_stock_ipod_sync_status,
    plan_stock_ipod_playlist_delete,
    plan_stock_ipod_sync,
    rollback_committed_stock_ipod_sync,
)
from soundbox.stock_ipod_info import (
    confirmed_model_from_recovery,
    model_from_serial,
    read_stock_ipod_snapshot,
    stock_ipod_device_key,
)
from soundbox.stock_artwork import StockArtworkCache, read_stock_artwork_slots
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.stock_ipod_capture import capture_stock_ipod_native
from soundbox import stock_ipod_capture
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


class StockPlaylistCapacity(unittest.TestCase):
    def test_pinned_renderer_serializes_ten_thousand_playlist_entries(self):
        payload = write_mhyp("Ten Thousand", list(range(1, 10_001)))

        self.assertEqual(payload[:4], b"mhyp")
        self.assertEqual(struct.unpack_from("<I", payload, 0x10)[0], 10_000)
        self.assertEqual(struct.unpack_from("<I", payload, 0x08)[0], len(payload))

    def test_large_unchanged_playlist_selection_keeps_identity_with_linear_lookups(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area) / "synthetic-ipod"
            itunes = root / "iPod_Control/iTunes"
            itunes.mkdir(parents=True)
            count = 80
            playlists = [PlaylistInfo(f"Playlist {i}", [], playlist_id=1000+i)
                         for i in range(count)]
            self.assertTrue(write_itunesdb(
                root, [], backup=False, force_checksum=ChecksumType.NONE,
                playlists=playlists,
                capabilities=capabilities_for_family_gen("iPod Video", "5.5th Gen")))
            database = itunes / "iTunesDB"
            before = database.read_bytes()
            selected = [Playlist(p.name, [], [], pid=str(i))
                        for i, p in enumerate(playlists)]
            library = Library({}, selected, Path(area) / "synthetic-source")
            real_parse = stock_ipod.parse_itunesdb
            lookups = []
            parsed_payloads = []

            class ObservedPlaylist(dict):
                def get(self, key, default=None):
                    if key == "Title":
                        lookups.append(key)
                    return super().get(key, default)

            def parse(path):
                if hasattr(path, "read"):
                    parsed_payloads.append(path.getvalue())
                else:
                    parsed_payloads.append(Path(path).read_bytes())
                result = real_parse(path)
                if hasattr(path, "read") or Path(path) == database:
                    result["mhlp"] = [ObservedPlaylist(row) for row in result["mhlp"]]
                return result

            with mock.patch.object(stock_ipod, "parse_itunesdb", side_effect=parse):
                plan = plan_stock_ipod_sync(
                    library, selected, root, model_number="MA446", filesystem="msdos")
            self.assertFalse(plan.requires_commit)
            self.assertEqual(plan.rendered_database, before)
            self.assertEqual(database.read_bytes(), before)
            self.assertLessEqual(len(lookups), 8 * count + 8)
            # One captured baseline and one final patched output traversal.
            # Semantic validation consumes the renderer's exact parsed output.
            self.assertEqual(len(parsed_payloads), 2)
            self.assertEqual(parsed_payloads[0], before)

    def test_duplicate_native_names_keep_first_identity_and_requested_order(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area) / "synthetic-ipod"
            itunes = root / "iPod_Control/iTunes"
            itunes.mkdir(parents=True)
            self.assertTrue(write_itunesdb(
                root, [], backup=False, force_checksum=ChecksumType.NONE,
                playlists=[PlaylistInfo("Repeated", [], playlist_id=101),
                           PlaylistInfo("Repeated", [], playlist_id=102),
                           PlaylistInfo("Keep", [], playlist_id=103),
                           PlaylistInfo("Another", [], playlist_id=104)],
                capabilities=capabilities_for_family_gen("iPod Video", "5.5th Gen")))
            before = (itunes / "iTunesDB").read_bytes()
            selected = [Playlist("Another", [], [], pid="a"),
                        Playlist("Repeated", [], [], pid="b")]
            plan = plan_stock_ipod_sync(
                Library({}, selected, Path(area) / "synthetic-source"), selected,
                root, model_number="MA446", filesystem="msdos")
            candidate = Path(area) / "candidate"
            candidate.write_bytes(plan.rendered_database)
            result = stock_ipod.parse_itunesdb(str(candidate))
            rows = [(row["Title"], row["playlistID"])
                    for row in result["mhlp"] if not row.get("isMaster")]
            self.assertEqual(rows, [("Keep", 103), ("Another", 104), ("Repeated", 101)])
            self.assertEqual((itunes / "iTunesDB").read_bytes(), before)


class _FixtureArtCache:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths

    def image_path(self, track_id: str) -> Path | None:
        return self.paths.get(track_id)


class StockIpodSync(unittest.TestCase):
    def setUp(self):
        pacer = mock.patch.object(stock_ipod, "_pace_classic_usb")
        self.usb_pace = pacer.start()
        self.addCleanup(pacer.stop)
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
            # Real Apple databases can mirror Videos in both the ordinary MHLP
            # and canonical MHSD-5 datasets under different IDs.
            playlists=[regular, smart("Videos", 405, 0)],
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

    def test_snapshot_and_status_share_one_exact_native_capture(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        identity = "stock-ipod-v1:capture-fixture"
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache, stock_art_cache=self.stock_art_cache,
            identity_key=identity, shadow_store=shadow_store)
        commit_stock_ipod_sync(plan)
        root = self.ipod.resolve()
        database = root / "iPod_Control/iTunes/iTunesDB"
        artwork = root / "iPod_Control/Artwork/ArtworkDB"
        opened = []
        real_open = stock_ipod_capture.os.open

        def observed(path, *args, **kwargs):
            if path in (database, artwork):
                opened.append(path)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(stock_ipod_capture.os, "open", observed):
            captured = capture_stock_ipod_native(root)
            snapshot = read_stock_ipod_snapshot(
                root, model_number="MA446", native_capture=captured)
            status = inspect_stock_ipod_sync_status(
                self.library, root, identity_key=identity,
                model_number="MA446", serial_number="", shadow_store=shadow_store,
                native_capture=captured)
        self.assertEqual(opened.count(database), 2)
        self.assertEqual(opened.count(artwork), 2)
        self.assertEqual(snapshot.database_sha256, plan.summary()["database_sha256_after"])
        self.assertEqual(status.state, "up_to_date")

    def test_snapshot_identity_tracks_and_digest_remain_one_observation(self):
        captured = capture_stock_ipod_native(self.ipod)
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        replacement = bytearray(captured.database)
        struct.pack_into("<Q", replacement, 0x48, 0xABCDEF)
        database.write_bytes(replacement)
        snapshot = read_stock_ipod_snapshot(
            self.ipod, model_number="MA446", native_capture=captured)
        self.assertEqual(snapshot.database_sha256, captured.database_sha256)
        self.assertNotEqual(snapshot.database_sha256, _hash(database))
        self.assertNotEqual(snapshot.library_persistent_id, 0xABCDEF)

    def test_stock_noop_reuses_exact_committed_source_proofs_without_audio_reads(self):
        store = StockIpodShadowStore(self.base / "stock-state")
        options = dict(model_number="MA446", filesystem="msdos",
                       identity_key="stock-ipod-v1:source-fixture", shadow_store=store)
        first = plan_stock_ipod_sync(self.library, [self.playlist], self.ipod, **options)
        commit_stock_ipod_sync(first)
        before = self._snapshot(self.ipod)
        real_hash = stock_ipod._sha256

        def bounded_hash(path):
            if "Music" in Path(path).parts:
                raise AssertionError("Retained device media was hashed")
            return real_hash(path)

        with (mock.patch.object(stock_ipod, "acquire_source",
                                side_effect=AssertionError("Unchanged source acquisition")),
              mock.patch.object(stock_ipod, "_technical_audio",
                                side_effect=AssertionError("Unchanged source format read")),
              mock.patch.object(stock_ipod, "_sha256", side_effect=bounded_hash)):
            repeated = plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod, **options)
        self.assertTrue(repeated.shadow_fast_path)
        self.assertFalse(repeated.requires_commit)
        self.assertEqual(self._snapshot(self.ipod), before)

    def test_stock_old_shadow_and_ctime_change_reacquire_instead_of_renewing_proof(self):
        from dataclasses import replace
        store = StockIpodShadowStore(self.base / "stock-state")
        identity = "stock-ipod-v1:source-fixture"
        options = dict(model_number="MA446", filesystem="msdos",
                       identity_key=identity, shadow_store=store)
        first = plan_stock_ipod_sync(self.library, [self.playlist], self.ipod, **options)
        commit_stock_ipod_sync(first)
        original_acquire = stock_ipod.acquire_source
        old = store.load(identity)
        self.assertIsNotNone(old)
        assert old is not None
        legacy = {path: {key: value for key, value in row.items()
                        if key not in {"source_epoch", "source_sha256",
                                       "stock_media_profile", "stock_technical"}}
                  for path, row in old.sources.items()}
        store.save(replace(old, sources=legacy))
        with mock.patch.object(stock_ipod, "acquire_source", wraps=original_acquire) as capture:
            legacy_plan = plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod, **options)
        self.assertEqual(capture.call_count, 2)
        self.assertFalse(legacy_plan.requires_commit)
        upgraded = store.load(identity)
        self.assertIsNotNone(upgraded)
        assert upgraded is not None
        self.assertEqual((upgraded.generation, upgraded.committed_at),
                         (old.generation, old.committed_at))
        with mock.patch.object(stock_ipod, "acquire_source",
                               side_effect=AssertionError("Legacy proof upgrade did not persist")):
            self.assertFalse(plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod, **options).requires_commit)
        store.save(old)
        source = self.existing_source
        original_stat = source.stat()
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertNotEqual(source.stat().st_ctime_ns, original_stat.st_ctime_ns)
        with mock.patch.object(stock_ipod, "acquire_source", wraps=original_acquire) as capture:
            changed = plan_stock_ipod_sync(self.library, [self.playlist], self.ipod, **options)
        self.assertEqual(capture.call_count, 1)
        proof = changed.shadow_sources[str(source.resolve())]
        self.assertNotEqual(proof["source_epoch"], old.sources[str(source.resolve())]["source_epoch"])

    def test_source_proof_retains_capture_epoch_when_source_changes_later_in_plan(self):
        from dataclasses import replace
        store = StockIpodShadowStore(self.base / "stock-state")
        identity = "stock-ipod-v1:late-source-fixture"
        options = dict(model_number="MA446", filesystem="msdos",
                       identity_key=identity, shadow_store=store)
        first = plan_stock_ipod_sync(self.library, [self.playlist], self.ipod, **options)
        commit_stock_ipod_sync(first)
        old = store.load(identity)
        assert old is not None
        legacy = {path: {key: value for key, value in row.items()
                        if key != "source_epoch"} for path, row in old.sources.items()}
        store.save(replace(old, sources=legacy))
        source = self.existing_source.resolve()
        before = source.stat()
        technical = stock_ipod._technical_audio
        changed = False

        def change_after_capture(snapshot):
            nonlocal changed
            if snapshot.name == source.name and not changed:
                payload = bytearray(source.read_bytes())
                payload[-1] ^= 1
                source.write_bytes(payload)
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed = True
            return technical(snapshot)

        with mock.patch.object(stock_ipod, "_technical_audio", change_after_capture):
            plan = plan_stock_ipod_sync(self.library, [self.playlist], self.ipod, **options)
        self.assertFalse(plan.requires_commit)
        self.assertEqual(plan.shadow_sources[str(source)]["source_epoch"][-1], before.st_ctime_ns)
        self.assertNotEqual(source.stat().st_ctime_ns, before.st_ctime_ns)

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

    def test_commit_refuses_same_name_target_replacement_after_plan(self):
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos")
        original = self.base / "original-ipod"
        self.ipod.rename(original)
        self.ipod.mkdir()

        with self.assertRaisesRegex(StockIpodError, "remounted after dry-run"):
            commit_stock_ipod_sync(plan)

        self.assertEqual(list(self.ipod.iterdir()), [])
        self.assertTrue((original / "iPod_Control/iTunes/iTunesDB").is_file())

    def test_dual_boot_rewrites_existing_media_with_exact_recovery(self):
        (self.ipod / ".rockbox").mkdir()
        payload = self.base / "prepared" / "existing.wav"
        _wave(payload, 442, 321)
        original_device = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        source_hash = _hash(self.existing_source)
        device_hash = _hash(original_device)
        before = self._snapshot(self.ipod)
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        identity = "stock-ipod-v1:dual-fixture"

        plan = plan_stock_ipod_sync(
            self.library,
            [self.playlist],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            media_payloads={self.existing_source.resolve(): payload.resolve()},
            allow_dual_boot=True,
            identity_key=identity,
            shadow_store=shadow_store,
        )

        self.assertEqual(self._snapshot(self.ipod), before)
        self.assertTrue(plan.dual_boot)
        self.assertEqual(len(plan.rewrites), 1)
        self.assertEqual(plan.rewrites[0].dbid, 101)
        self.assertEqual(plan.rewrites[0].sha256_before, device_hash)
        self.assertEqual(plan.rewrites[0].sha256, _hash(payload))
        self.assertEqual(
            plan.source_device_paths[str(self.existing_source.resolve())],
            "/iPod_Control/Music/F00/AAAA.wav",
        )

        result = commit_stock_ipod_sync(plan)

        self.assertEqual(result.rewritten_tracks, 1)
        self.assertEqual(_hash(original_device), _hash(payload))
        self.assertEqual(_hash(self.existing_source), source_hash)
        saved = (
            result.recovery_directory
            / "media/iPod_Control/Music/F00/AAAA.wav.before")
        self.assertTrue(saved.is_file())
        self.assertEqual(_hash(saved), device_hash)
        existing = next(
            item for item in get_ipod_tracks(read_ipod(self.ipod))
            if item["dbid"] == 101)
        self.assertEqual(existing["size"], payload.stat().st_size)
        self.assertEqual(existing["location"], ":iPod_Control:Music:F00:AAAA.wav")
        shadow = shadow_store.load(identity)
        self.assertIsNotNone(shadow)
        assert shadow is not None
        source_row = shadow.sources[str(self.existing_source.resolve())]
        self.assertEqual(source_row["size"], payload.stat().st_size)
        self.assertEqual(
            source_row["source_size"], self.existing_source.stat().st_size)
        status = inspect_stock_ipod_sync_status(
            self.library, self.ipod,
            identity_key=identity, model_number="MA446", serial_number="",
            shadow_store=shadow_store,
        )
        self.assertEqual(status.state, "up_to_date")

    def test_dual_boot_database_failure_restores_rewritten_media(self):
        (self.ipod / ".rockbox").mkdir()
        payload = self.base / "prepared" / "existing.wav"
        _wave(payload, 441, 654)
        original_device = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        before_hash = _hash(original_device)
        before_database = _hash(self.ipod / "iPod_Control/iTunes/iTunesDB")
        plan = plan_stock_ipod_sync(
            self.library,
            [self.playlist],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            media_payloads={self.existing_source.resolve(): payload.resolve()},
            allow_dual_boot=True,
        )
        new_destination = self.ipod / plan.copies[0].relative_path
        real_write = DeviceSession.atomic_write

        def fail_live_database(session, destination, data):
            destination = Path(destination)
            if destination.name == "iTunesDB" and destination.parent.name == "iTunes":
                raise OSError("injected database failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_live_database):
            with self.assertRaisesRegex(OSError, "injected database failure"):
                commit_stock_ipod_sync(plan)

        self.assertEqual(_hash(original_device), before_hash)
        self.assertEqual(
            _hash(self.ipod / "iPod_Control/iTunes/iTunesDB"), before_database)
        self.assertFalse(new_destination.exists())

    def test_committed_dual_stock_phase_can_be_rolled_back_exactly(self):
        (self.ipod / ".rockbox").mkdir()
        payload = self.base / "prepared" / "existing.wav"
        _wave(payload, 441, 777)
        before = self._snapshot(self.ipod)
        plan = plan_stock_ipod_sync(
            self.library,
            [self.playlist],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            media_payloads={self.existing_source.resolve(): payload.resolve()},
            allow_dual_boot=True,
        )
        result = commit_stock_ipod_sync(plan)
        self.assertNotEqual(self._snapshot(self.ipod), before)

        rollback_committed_stock_ipod_sync(plan, result)

        # Recovery evidence deliberately remains, but every live stock path is
        # restored and the newly allocated media file is gone.
        for relative, contents in before:
            path = self.ipod / relative
            self.assertTrue(path.exists(), relative)
            if contents is not None:
                self.assertEqual(path.read_bytes(), contents, relative)
        self.assertFalse((self.ipod / plan.copies[0].relative_path).exists())

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

    def test_distinct_identical_sources_keep_distinct_stock_records(self):
        duplicate = self.sources / "alternate-existing.wav"
        shutil.copy2(self.existing_source, duplicate)
        alternate = Track(
            "alternate", "Alternate Label", "Different Artist",
            duplicate.as_uri(), total_time=1,
        )
        tracks = {
            "old": self.library.tracks_by_id["old"],
            "alternate": alternate,
        }
        playlist = Playlist(
            "Distinct Identities", [], ["old", "alternate"], pid="distinct")
        library = Library(tracks, [playlist], self.sources)

        plan = plan_stock_ipod_sync(
            library, [playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
        )

        self.assertEqual(plan.reused_track_count, 1)
        self.assertEqual(len(plan.copies), 1)
        self.assertEqual(plan.final_track_count, 2)
        self.assertNotEqual(
            plan.source_device_paths[str(self.existing_source.resolve())],
            plan.source_device_paths[str(duplicate.resolve())],
        )

        commit_stock_ipod_sync(plan)
        database = read_ipod(self.ipod)
        by_dbid = {int(item["dbid"]): item for item in get_ipod_tracks(database)}
        self.assertEqual(len(by_dbid), 2)
        self.assertEqual(
            {(item["title"], item["artist"]) for item in by_dbid.values()},
            {
                ("Existing", "Fixture Artist"),
                ("Alternate Label", "Different Artist"),
            },
        )
        output_by_id = {
            int(item["trackID"]): int(item["dbid"])
            for item in by_dbid.values()
        }
        rendered = next(
            item for item in database["mhlp"]
            if item.get("Title") == "Distinct Identities")
        sequence = [
            output_by_id[int(item["trackID"])] for item in rendered["items"]]
        self.assertEqual(len(sequence), 2)
        self.assertNotEqual(sequence[0], sequence[1])

    def test_final_patched_database_still_receives_semantic_validation(self):
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        before = database.read_bytes()
        operations = (
            lambda: plan_stock_ipod_sync(
                self.library, [self.playlist], self.ipod,
                model_number="MA446", filesystem="msdos"),
            lambda: plan_stock_ipod_playlist_delete(
                self.ipod, [202], model_number="MA446", filesystem="msdos",
                stock_art_cache=self.stock_art_cache),
        )
        for operation in operations:
            with self.subTest(operation=operation), mock.patch.object(
                    stock_ipod, "_patch_smart_rule_headers", return_value=before):
                # A structurally readable result with the wrong songs must
                # still fail at the caller's full semantic validation.
                with self.assertRaisesRegex(StockIpodError, "count failed validation"):
                    operation()
                self.assertEqual(database.read_bytes(), before)

    def test_overlapping_memberships_observe_each_source_once_per_phase(self):
        selected = [Playlist(f"Overlap {i}", [], ["old", "new", "old"] * 20,
                             pid=f"overlap-{i}") for i in range(8)]
        checks = []
        real_is_file = Path.is_file
        def is_file(path):
            if path in (self.existing_source, self.new_source):
                checks.append(path)
            return real_is_file(path)
        with mock.patch.object(Path, "is_file", is_file):
            plan = plan_stock_ipod_sync(
                self.library, selected, self.ipod,
                model_number="MA446", filesystem="msdos")
        # Acquisition, hashes and commit guards are separate. Membership
        # observation itself scales with two unique paths and two phases.
        self.assertEqual(checks.count(self.existing_source), 2)
        self.assertEqual(checks.count(self.new_source), 2)
        staged = self.base / "overlap-iTunesDB"
        staged.write_bytes(plan.rendered_database)
        parsed = stock_ipod.parse_itunesdb(str(staged))
        mapping = {int(row["trackID"]): int(row["dbid"])
                   for row in get_ipod_tracks(parsed)}
        by_name = {row.get("Title"): row for row in parsed["mhlp"]}
        for playlist in selected:
            ids = [mapping[int(item["trackID"])] for item in by_name[playlist.name]["items"]]
            self.assertEqual(ids, [plan.source_device_dbids[str(self.library.tracks_by_id[key].path.resolve(strict=True))]
                                   for key in playlist.keys])

    def test_source_disappearance_or_redirect_between_membership_phases_is_refused(self):
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                replacement = self.sources / "replacement.wav"
                shutil.copyfile(self.new_source, replacement)
                before = self._snapshot(self.ipod)
                real_new_info = stock_ipod._new_track_info
                def change_after_acquisition(*args, **kwargs):
                    result = real_new_info(*args, **kwargs)
                    self.new_source.unlink()
                    if redirect:
                        self.new_source.symlink_to(replacement)
                    return result
                try:
                    with mock.patch.object(stock_ipod, "_new_track_info", side_effect=change_after_acquisition):
                        with self.assertRaisesRegex(StockIpodError, "source identity changed"):
                            plan_stock_ipod_sync(
                                self.library, [self.playlist], self.ipod,
                                model_number="MA446", filesystem="msdos")
                    self.assertEqual(self._snapshot(self.ipod), before)
                finally:
                    self.new_source.unlink(missing_ok=True)
                    shutil.copyfile(replacement, self.new_source)

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

    def test_pre_composite_dual_playlist_delete_removes_orphan_media(self):
        sync_plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
        )
        new_media = self.ipod / sync_plan.copies[0].relative_path
        old_media = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        source_hashes = {
            self.existing_source: _hash(self.existing_source),
            self.new_source: _hash(self.new_source),
        }
        commit_stock_ipod_sync(sync_plan)
        (self.ipod / ".rockbox").mkdir()
        before = read_ipod(self.ipod)
        target = next(
            raw for raw in before["mhlp"]
            if raw.get("Title") == "SoundBox Test"
        )
        with self.assertRaisesRegex(StockIpodError, "pre-composite"):
            plan_stock_ipod_playlist_delete(
                self.ipod,
                [int(target["playlistID"])],
                model_number="MA446",
                filesystem="msdos",
                stock_art_cache=self.stock_art_cache,
            )

        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [int(target["playlistID"])],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
            allow_dual_boot=True,
        )
        self.assertTrue(plan.dual_boot)
        self.assertEqual(plan.deleted_track_count, 1)
        self.assertEqual(plan.final_track_count, 1)
        self.assertEqual(len(plan.media_deletes), 1)
        self.assertIsNotNone(plan.rendered_artwork)
        commit_stock_ipod_playlist_delete(plan)

        after = read_ipod(self.ipod)
        self.assertEqual(
            [raw["dbid"] for raw in get_ipod_tracks(after)],
            [101],
        )
        self.assertNotIn(
            "SoundBox Test",
            {raw.get("Title") for raw in after["mhlp"]},
        )
        self.assertTrue(old_media.is_file())
        self.assertFalse(new_media.exists())
        for source, digest in source_hashes.items():
            self.assertEqual(_hash(source), digest)

    def test_pre_composite_dual_repairs_empty_playlist_orphan_index(self):
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
        self.assertTrue(write_itunesdb(
            str(self.ipod),
            [existing],
            backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=[],
            smart_playlists=[],
            capabilities=capabilities,
        ))
        (self.ipod / ".rockbox").mkdir()
        media_hash = _hash(existing_media)

        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [],
            model_number="MA446",
            filesystem="msdos",
            allow_dual_boot=True,
            repair_empty_playlist_index=True,
        )

        self.assertTrue(plan.repair_empty_playlist_index)
        self.assertEqual(plan.playlist_ids, ())
        self.assertEqual(plan.deleted_track_count, 1)
        self.assertEqual(plan.existing_track_count, 1)
        self.assertEqual(plan.final_track_count, 0)
        self.assertEqual(plan.remaining_playlist_count, 0)
        self.assertEqual(len(plan.media_deletes), 1)
        self.assertIsNone(plan.rendered_artwork)

        result = commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_track_records, 1)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertEqual(get_ipod_tracks(read_ipod(self.ipod)), [])
        self.assertFalse(existing_media.exists())
        self.assertEqual(_hash(self.existing_source), media_hash)

    def test_empty_playlist_orphan_index_repair_refuses_user_playlists(self):
        (self.ipod / ".rockbox").mkdir()
        with self.assertRaisesRegex(StockIpodError, "zero user playlists"):
            plan_stock_ipod_playlist_delete(
                self.ipod,
                [],
                model_number="MA446",
                filesystem="msdos",
                allow_dual_boot=True,
                repair_empty_playlist_index=True,
            )

    def test_videos_system_playlist_is_narrowly_deletable_without_media(self):
        self._rewrite_fixture_with_videos_smart_playlist()
        before = read_ipod(self.ipod)
        videos = next(
            raw for raw in before["mhlp_smart"]
            if raw.get("Title") == "Videos")
        aliases = [
            raw for raw in before["mhlp"]
            if raw.get("Title") == "Videos" and raw.get("isSmartPlaylist")
        ]
        self.assertEqual([int(raw["playlistID"]) for raw in aliases], [405])
        snapshot = read_stock_ipod_snapshot(self.ipod)
        displayed_videos = [
            item for item in snapshot.playlists if item.title == "Videos"
        ]
        self.assertEqual(len(displayed_videos), 1)
        self.assertEqual(displayed_videos[0].playlist_id, int(videos["playlistID"]))

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

    def test_playlist_delete_refuses_same_name_target_replacement_after_plan(self):
        plan = plan_stock_ipod_playlist_delete(
            self.ipod,
            [202],
            model_number="MA446",
            filesystem="msdos",
            stock_art_cache=self.stock_art_cache,
        )
        original = self.base / "original-ipod"
        self.ipod.rename(original)
        self.ipod.mkdir()

        with self.assertRaisesRegex(StockIpodError, "remounted after dry-run"):
            commit_stock_ipod_playlist_delete(plan)

        self.assertEqual(list(self.ipod.iterdir()), [])
        self.assertTrue((original / "iPod_Control/iTunes/iTunesDB").is_file())

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

    def test_shadow_fast_path_writes_only_changed_cover_slots(self):
        # Separate canonical files with identical bytes initially exercise
        # shared pixel offsets while allowing only one track to change later.
        new_art = self.base / "curated-new.jpg"
        shutil.copy2(self.art_cache.paths["old"], new_art)
        self.art_cache.paths["new"] = new_art
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        common = {
            "model_number": "MA446",
            "filesystem": "msdos",
            "art_cache": self.art_cache,
            "stock_art_cache": self.stock_art_cache,
            "identity_key": "stock-ipod-v1:fixture:0000000000000001",
            "serial_number": "8M650V0HV9M",
            "shadow_store": shadow_store,
        }
        first = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        first_result = commit_stock_ipod_sync(first)
        self.assertTrue(first_result.shadow_updated)
        _raw, first_slots, _files = read_stock_artwork_slots(
            self.ipod / "iPod_Control/Artwork")
        first_by_song = {slot.song_id: slot for slot in first_slots.values()}

        unchanged = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        self.assertTrue(unchanged.shadow_fast_path)
        self.assertEqual(len(unchanged.copies), 0)
        self.assertEqual(unchanged.summary()["artwork_pixel_bytes"], 0)
        self.assertFalse(unchanged.requires_commit)

        Image.new("RGB", (300, 300), (5, 210, 80)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        changed = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        self.assertTrue(changed.shadow_fast_path)
        self.assertIsNotNone(changed.rendered_artwork)
        assert changed.rendered_artwork is not None
        self.assertTrue(changed.rendered_artwork.incremental)
        self.assertEqual(changed.summary()["artwork_pixel_bytes"], 100_000)
        before_sizes = {
            plan.relative_path.name: plan.before_size
            for plan in changed.rendered_artwork.ithmb_files
        }
        result = commit_stock_ipod_sync(changed)
        self.assertEqual(result.artwork_pixel_bytes, 100_000)
        artwork = self.ipod / "iPod_Control/Artwork"
        _raw, changed_slots, _files = read_stock_artwork_slots(artwork)
        changed_by_song = {slot.song_id: slot for slot in changed_slots.values()}
        new_dbid = first.copies[0].dbid
        self.assertEqual(
            changed_by_song[new_dbid].offsets,
            first_by_song[new_dbid].offsets,
        )
        self.assertNotEqual(
            changed_by_song[101].offsets,
            first_by_song[101].offsets,
        )
        self.assertEqual(
            (artwork / "F1028_1.ithmb").stat().st_size,
            before_sizes["F1028_1.ithmb"] + 20_000,
        )
        self.assertEqual(
            (artwork / "F1029_1.ithmb").stat().st_size,
            before_sizes["F1029_1.ithmb"] + 80_000,
        )

        Image.new("RGB", (300, 300), (220, 40, 15)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        second_change = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        self.assertEqual(second_change.summary()["artwork_pixel_bytes"], 100_000)
        commit_stock_ipod_sync(second_change)
        Image.new("RGB", (300, 300), (15, 40, 220)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        third_change = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        second_sizes = {
            path.name: path.stat().st_size for path in artwork.glob("F*.ithmb")
        }
        commit_stock_ipod_sync(third_change)
        self.assertEqual(
            {path.name: path.stat().st_size for path in artwork.glob("F*.ithmb")},
            second_sizes,
        )

    def test_partial_shadow_reconciles_existing_audio_and_pixels(self):
        # Seed a device containing both tracks/artwork without a Mac shadow.
        commit_stock_ipod_sync(plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache, stock_art_cache=self.stock_art_cache,
        ))
        only_new = Playlist("Only New", [], ["new"], pid="only-new")
        only_old = Playlist("Only Old", [], ["old"], pid="only-old")
        split_library = Library(
            self.library.tracks_by_id, [only_new, only_old], self.sources)
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        common = {
            "model_number": "MA446",
            "filesystem": "msdos",
            "art_cache": self.art_cache,
            "stock_art_cache": self.stock_art_cache,
            "identity_key": "stock-ipod-v1:fixture:partial",
            "shadow_store": shadow_store,
        }

        # Bootstrap from only one playlist. The other track is live on-device,
        # but intentionally absent from the new local source map.
        commit_stock_ipod_sync(plan_stock_ipod_sync(
            split_library, [only_new], self.ipod, **common))
        shadow = shadow_store.load("stock-ipod-v1:fixture:partial")
        self.assertIsNotNone(shadow)
        assert shadow is not None
        self.assertNotIn(str(self.existing_source.resolve()), shadow.sources)

        # Selecting that older playlist must hash the narrow live candidate,
        # reuse its media and prove its current pixels instead of duplicating
        # audio or rewriting every previously unindexed cover.
        reconciled = plan_stock_ipod_sync(
            split_library, [only_old], self.ipod, **common)
        self.assertTrue(reconciled.shadow_fast_path)
        self.assertEqual(len(reconciled.copies), 0)
        self.assertEqual(reconciled.reused_track_count, 1)
        self.assertEqual(reconciled.summary()["artwork_pixel_bytes"], 0)

        Image.new("RGB", (300, 300), (12, 190, 240)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        changed = plan_stock_ipod_sync(
            split_library, [only_old], self.ipod, **common)
        self.assertEqual(len(changed.copies), 0)
        self.assertEqual(changed.summary()["artwork_pixel_bytes"], 100_000)

    def test_stock_sync_prunes_collection_delete_when_unreferenced(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        identity_key = "stock-ipod-v1:fixture:collection-delete"
        common = {
            "model_number": "MA446",
            "filesystem": "msdos",
            "art_cache": self.art_cache,
            "stock_art_cache": self.stock_art_cache,
            "identity_key": identity_key,
            "shadow_store": shadow_store,
        }
        first = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        new_media = self.ipod / first.copies[0].relative_path
        source_hash = _hash(self.new_source)
        commit_stock_ipod_sync(first)
        shadow = shadow_store.load(identity_key)
        self.assertIsNotNone(shadow)
        assert shadow is not None

        self.library.hidden_ids.add("new")
        managed_names = tuple(
            " / ".join([
                *map(str, row.get("folder_path") or []),
                str(row.get("name") or ""),
            ])
            for row in shadow.playlists.values()
        )
        managed_track_rows = tuple(sorted({
            (
                int(row.get("dbid") or 0),
                str(row.get("device_path") or ""),
                str(row.get("sha256") or ""),
            )
            for row in shadow.sources.values()
            if int(row.get("dbid") or 0) > 0
        }))
        bad_track_rows = tuple(
            (dbid, path, "0" * 64 if dbid != 101 else digest)
            for dbid, path, digest in managed_track_rows
        )
        with self.assertRaisesRegex(StockIpodError, "bytes changed"):
            plan_stock_ipod_sync(
                self.library,
                [self.playlist],
                self.ipod,
                managed_playlist_names_before=managed_names,
                managed_playlist_keys_before=tuple(shadow.playlists),
                managed_track_rows_before=bad_track_rows,
                **common,
            )
        plan = plan_stock_ipod_sync(
            self.library,
            [self.playlist],
            self.ipod,
            managed_playlist_names_before=managed_names,
            managed_playlist_keys_before=tuple(shadow.playlists),
            managed_track_rows_before=managed_track_rows,
            **common,
        )

        self.assertEqual(plan.removed_track_count, 1)
        self.assertEqual(len(plan.media_deletes), 1)
        self.assertEqual(plan.final_track_count, 1)
        result = commit_stock_ipod_sync(plan)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertFalse(result.cleanup_failures)
        self.assertFalse(new_media.exists())
        self.assertEqual(_hash(self.new_source), source_hash)
        self.assertEqual(
            [raw["dbid"] for raw in get_ipod_tracks(read_ipod(self.ipod))],
            [101],
        )

    def test_incremental_art_reuses_matching_free_pixels_without_writing(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        common = {
            "model_number": "MA446",
            "filesystem": "msdos",
            "art_cache": self.art_cache,
            "stock_art_cache": self.stock_art_cache,
            "identity_key": "stock-ipod-v1:fixture:free-pixels",
            "shadow_store": shadow_store,
        }
        original_pixels = self.art_cache.paths["old"].read_bytes()
        first = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        commit_stock_ipod_sync(first)
        artwork = self.ipod / "iPod_Control/Artwork"
        _raw, first_slots, _files = read_stock_artwork_slots(artwork)
        first_by_song = {slot.song_id: slot for slot in first_slots.values()}
        original_offsets = first_by_song[101].offsets

        Image.new("RGB", (300, 300), (5, 210, 80)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        changed = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        self.assertEqual(changed.summary()["artwork_pixel_bytes"], 100_000)
        commit_stock_ipod_sync(changed)
        before_pixels = {
            path.name: path.read_bytes() for path in artwork.glob("F*.ithmb")}

        # The first cover's old offsets are now free but retain their bytes.
        # Reverting to that cover must relink ArtworkDB without rewriting them.
        self.art_cache.paths["old"].write_bytes(original_pixels)
        reverted = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        self.assertTrue(reverted.requires_commit)
        self.assertIsNotNone(reverted.rendered_artwork)
        assert reverted.rendered_artwork is not None
        self.assertEqual(reverted.summary()["artwork_pixel_bytes"], 0)
        self.assertEqual(reverted.rendered_artwork.range_writes, ())
        reverted_slot = next(
            slot for slot in reverted.rendered_artwork.slots
            if slot.song_id == 101)
        self.assertEqual(reverted_slot.offsets, original_offsets)
        result = commit_stock_ipod_sync(reverted)
        self.assertEqual(result.artwork_pixel_bytes, 0)
        self.assertEqual(
            {path.name: path.read_bytes() for path in artwork.glob("F*.ithmb")},
            before_pixels,
        )

    def test_incremental_art_failure_leaves_published_database_and_art_valid(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        common = {
            "model_number": "MA446",
            "filesystem": "msdos",
            "art_cache": self.art_cache,
            "stock_art_cache": self.stock_art_cache,
            "identity_key": "stock-ipod-v1:fixture:0000000000000001",
            "shadow_store": shadow_store,
        }
        commit_stock_ipod_sync(plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common))
        Image.new("RGB", (300, 300), (30, 220, 10)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod, **common)
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        artwork_db = self.ipod / "iPod_Control/Artwork/ArtworkDB"
        before_database = database.read_bytes()
        before_artwork_db = artwork_db.read_bytes()
        real_write = DeviceSession.write_verified_range
        calls = 0

        def fail_second_range(session, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic second-range failure")
            return real_write(session, *args, **kwargs)

        with mock.patch.object(
                DeviceSession, "write_verified_range", fail_second_range):
            with self.assertRaisesRegex(OSError, "second-range failure"):
                commit_stock_ipod_sync(plan)
        self.assertEqual(database.read_bytes(), before_database)
        self.assertEqual(artwork_db.read_bytes(), before_artwork_db)
        parsed = read_ipod(self.ipod)
        self.assertEqual(len(get_ipod_tracks(parsed)), 2)
        self.assertTrue(all(
            int(track.get("mhiiLink") or 0) for track in get_ipod_tracks(parsed)))

    def test_shadow_save_failure_does_not_rollback_healthy_device_commit(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            identity_key="stock-ipod-v1:fixture",
            shadow_store=shadow_store,
        )
        with mock.patch.object(
                shadow_store, "save", side_effect=OSError("Mac disk full")):
            result = commit_stock_ipod_sync(plan)
        self.assertFalse(result.shadow_updated)
        self.assertIn("Mac disk full", result.shadow_warning)
        self.assertEqual(len(get_ipod_tracks(read_ipod(self.ipod))), 2)

    def test_reconnect_shadow_status_is_read_only_and_detects_local_art(self):
        shadow_store = StockIpodShadowStore(self.base / "stock-state")
        identity = "stock-ipod-v1:fixture"
        plan = plan_stock_ipod_sync(
            self.library, [self.playlist], self.ipod,
            model_number="MA446", filesystem="msdos",
            art_cache=self.art_cache, stock_art_cache=self.stock_art_cache,
            identity_key=identity, shadow_store=shadow_store,
        )
        commit_stock_ipod_sync(plan)
        before = self._snapshot(self.ipod)
        status = inspect_stock_ipod_sync_status(
            self.library, self.ipod,
            identity_key=identity, model_number="MA446", serial_number="",
            shadow_store=shadow_store,
        )
        self.assertEqual(status.state, "up_to_date")
        without_custom = Library(
            self.library.tracks_by_id, [], self.library.source)
        phantom = inspect_stock_ipod_sync_status(
            without_custom, self.ipod,
            identity_key=identity, model_number="MA446", serial_number="",
            shadow_store=shadow_store,
        )
        self.assertEqual(phantom.state, "local_changes")
        custom_status = inspect_stock_ipod_sync_status(
            without_custom, self.ipod,
            identity_key=identity, model_number="MA446", serial_number="",
            shadow_store=shadow_store,
            extra_playlists=(self.playlist,),
        )
        self.assertEqual(custom_status.state, "up_to_date")
        Image.new("RGB", (300, 300), (12, 34, 56)).save(
            self.art_cache.paths["old"], "JPEG", quality=90)
        changed = inspect_stock_ipod_sync_status(
            self.library, self.ipod,
            identity_key=identity, model_number="MA446", serial_number="",
            shadow_store=shadow_store,
        )
        self.assertEqual(changed.state, "local_changes")
        self.assertEqual(self._snapshot(self.ipod), before)

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
