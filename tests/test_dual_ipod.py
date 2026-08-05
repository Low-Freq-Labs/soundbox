"""Composite single-copy dual-boot planner and rollback tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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
    TrackInfo,
    write_itunesdb,
)

from soundbox.dual_ipod import (
    DUAL_MANIFEST_SCHEMA,
    commit_dual_ipod_sync,
    plan_dual_ipod_sync,
)
from soundbox import dual_ipod, engine, manifest as device_manifest, stock_ipod
from soundbox.library import Library, Playlist, Track
from soundbox.rockbox_media import (
    RockboxArtworkCache,
    RockboxMediaCache,
    _riff_info_values,
)
from soundbox.stock_artwork import StockArtworkCache
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.sync import DeviceSession


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wave(path: Path, frames: int, value: int, width: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(width)
        output.setframerate(44_100)
        if width == 2:
            sample = struct.pack("<h", value)
        else:
            sample = int(value).to_bytes(3, "little", signed=True)
        output.writeframes(sample * 2 * frames)


class _ArtCache:
    def __init__(self, values: dict[str, Path]):
        self.values = values

    def image_path(self, track_id: str) -> Path | None:
        return self.values.get(track_id)


class DualIpodSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ipod = self.base / "IPOD"
        self.sources = self.base / "sources"
        self.ipod.mkdir()
        self.sources.mkdir()
        (self.ipod / ".rockbox").mkdir()

        self.safe = self.sources / "safe.wav"
        _wave(self.safe, 441, 100)
        existing_media = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        existing_media.parent.mkdir(parents=True)
        existing_media.write_bytes(self.safe.read_bytes())

        itunes = self.ipod / "iPod_Control/iTunes"
        itunes.mkdir(parents=True)
        capabilities = capabilities_for_family_gen("iPod Video", "5.5th Gen")
        self.assertIsNotNone(capabilities)
        existing = TrackInfo(
            title="Safe",
            artist="Fixture",
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
            playlists=[PlaylistInfo("Existing", [101], playlist_id=202)],
            capabilities=capabilities,
        ))

        self.unsafe = self.sources / "unsafe24.wav"
        _wave(self.unsafe, 882, 200, width=3)
        self.unsupported = self.sources / "notes.xyz"
        self.unsupported.write_bytes(b"not audio")
        self.missing = self.sources / "missing.mp3"
        tracks = {
            "safe": Track("safe", "Safe", "Fixture", self.safe.as_uri()),
            "unsafe": Track(
                "unsafe", "Unsafe 24", "Fixture", self.unsafe.as_uri()),
            "unsupported": Track(
                "unsupported", "Notes", "Fixture", self.unsupported.as_uri()),
            "missing": Track(
                "missing", "Missing", "Fixture", self.missing.as_uri()),
        }
        self.playlist = Playlist(
            "Dual Test", [], list(tracks), pid="dual-test")
        self.library = Library(tracks, [self.playlist], self.sources)

        cover = self.base / "art" / "cover.jpg"
        cover.parent.mkdir()
        Image.new("RGB", (500, 500), (20, 80, 140)).save(
            cover, "JPEG", quality=90, progressive=False)
        self.art_cache = _ArtCache({"safe": cover, "unsafe": cover})
        self.media_cache = RockboxMediaCache(self.base / "rockbox-media")
        self.rockbox_art_cache = RockboxArtworkCache(
            self.base / "rockbox-art")
        self.stock_art_cache = StockArtworkCache(self.base / "stock-art")
        self.stock_shadow_store = StockIpodShadowStore(
            self.base / "stock-shadow")

    def _plan(self, selected=None, **kwargs):
        return plan_dual_ipod_sync(
            self.library,
            list([self.playlist] if selected is None else selected),
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            **kwargs,
        )

    def test_plan_and_commit_share_stock_media_and_keep_unsafe_rockbox_only(self):
        before_database = _hash(self.ipod / "iPod_Control/iTunes/iTunesDB")
        before_files = sorted(
            str(path.relative_to(self.ipod)) for path in self.ipod.rglob("*")
            if path.is_file())

        plan = self._plan()

        self.assertEqual(
            sorted(str(path.relative_to(self.ipod)) for path in self.ipod.rglob("*")
                   if path.is_file()),
            before_files,
        )
        self.assertEqual(_hash(self.ipod / "iPod_Control/iTunes/iTunesDB"), before_database)
        self.assertEqual(plan.stock_safe_sources, 1)
        self.assertEqual(plan.rockbox_only_sources, 1)
        self.assertEqual(len(plan.missing), 1)
        self.assertEqual(len(plan.unsupported), 1)
        self.assertEqual(len(plan.media_copies), 1)
        self.assertEqual(len(plan.artwork_copies), 6)
        self.assertEqual(plan.progress_total, 2)
        playlist_text = plan.playlists[0].payload.decode("utf-8")
        self.assertIn("/iPod_Control/Music/F00/AAAA.wav", playlist_text)
        self.assertIn("/Music/Fixture/Unsafe 24/Unsafe 24.wav", playlist_text)
        self.assertNotIn("Missing", playlist_text)
        self.assertNotIn("Notes", playlist_text)
        self.assertIn(
            Path("iPod_Control/Music/F00/AAAA.160x160.bmp"),
            {item.relative_path for item in plan.artwork_copies},
        )
        self.assertIn(
            Path("iPod_Control/Music/F00/AAAA.116x116.bmp"),
            {item.relative_path for item in plan.artwork_copies},
        )
        self.assertIn(
            Path("iPod_Control/Music/F00/AAAA.bmp"),
            {item.relative_path for item in plan.artwork_copies},
        )
        self.assertIn(
            Path("Music/Fixture/Unsafe 24/Unsafe 24.160x160.bmp"),
            {item.relative_path for item in plan.artwork_copies},
        )

        progress = []
        result = commit_dual_ipod_sync(
            plan,
            on_progress=lambda done, total, _label: progress.append(
                (done, total)),
        )

        self.assertEqual(result.copied_media, 1)
        self.assertEqual(result.copied_artwork, 6)
        self.assertEqual(progress, [(0, 2), (1, 2), (2, 2)])
        database_tracks = get_ipod_tracks(read_ipod(self.ipod))
        self.assertEqual(len(database_tracks), 1)
        self.assertEqual(database_tracks[0]["dbid"], 101)
        rockbox_media = self.ipod / plan.media_copies[0].relative_path
        self.assertTrue(rockbox_media.is_file())
        self.assertNotIn(
            str(plan.media_copies[0].relative_path).replace("/", ":"),
            {str(item.get("location") or "") for item in database_tracks},
        )
        self.assertEqual(
            _riff_info_values(self.ipod / "iPod_Control/Music/F00/AAAA.wav"),
            ("Safe", "Fixture"),
        )
        self.assertEqual(
            _riff_info_values(rockbox_media),
            ("Unsafe 24", "Fixture"),
        )
        manifest = json.loads(
            (self.ipod / ".soundbox/manifest.json").read_text())
        self.assertEqual(manifest["schema"], DUAL_MANIFEST_SCHEMA)
        self.assertTrue(
            manifest["files"]["/iPod_Control/Music/F00/AAAA.wav"]["owned"])
        self.assertEqual(
            manifest["files"][
                "/iPod_Control/Music/F00/AAAA.160x160.bmp"
            ]["playlists"],
            ["dual-test"],
        )
        config = (self.ipod / ".rockbox/config.cfg").read_text()
        self.assertIn("tagcache_autoupdate: on", config)
        self.assertIn(
            "playlist viewer track display: title from tags", config)

    def test_plan_reports_monotonic_visible_progress(self):
        events = []

        plan = self._plan(
            on_progress=lambda done, total, label:
            events.append((done, total, label)))

        self.assertIsNotNone(plan)
        self.assertGreater(len(events), 4)
        self.assertEqual(events[0][0], 0)
        self.assertEqual(events[-1][0], events[-1][1])
        self.assertTrue(all(
            current[0] <= following[0]
            and current[1] == following[1]
            for current, following in zip(events, events[1:])
        ))

    def test_delete_plan_verifies_retained_playlists_in_worker_plan(self):
        key = device_manifest.playlist_key(self.playlist)
        expected = dual_ipod.composite_playlist_entry(
            self.library, self.playlist)
        expected["name"] = "Stale name"

        with self.assertRaisesRegex(
                dual_ipod.DualIpodError,
                "pending changes"):
            self._plan(expected_playlist_rows={key: expected})

    def test_no_write_plan_retains_specs_not_full_audio_derivatives(self):
        plan = self._plan()

        self.assertFalse(self.media_cache.root.exists())
        self.assertTrue(plan.media_copies[0].media_plan is not None)
        stock_media = plan.stock.copies + plan.stock.rewrites
        self.assertTrue(stock_media[0].media_plan is not None)
        self.assertEqual(
            plan.media_copies[0].source,
            plan.media_copies[0].media_plan.source,
        )
        self.assertEqual(
            stock_media[0].source,
            stock_media[0].media_plan.source,
        )

    def test_committed_noop_plan_reuses_media_without_derivative_rebuild(self):
        commit_dual_ipod_sync(self._plan())

        with mock.patch.object(
                self.media_cache,
                "plan",
                side_effect=AssertionError("unchanged media was rebuilt"),
        ):
            plan = self._plan(reconcile=True)

        self.assertFalse(plan.requires_commit)

    def test_recognized_rockbox_append_becomes_sync_and_is_adopted_once(self):
        second = Playlist(
            "Device Picks", [], ["unsafe"], pid="device-picks")
        self.library.playlists.append(second)
        commit_dual_ipod_sync(self._plan(selected=[self.playlist, second]))
        manifest = device_manifest.load_composite(self.ipod)
        playlist_path = next(
            self.ipod / device_path.lstrip("/")
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "playlist"
            and row.get("source") == "device-picks"
        )
        safe_device_path = next(
            device_path
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "shared-stock-media"
            and row.get("source") == str(self.safe.resolve())
        )
        with playlist_path.open("a", encoding="utf-8") as output:
            output.write("/<HDD0>" + safe_device_path + "\n")

        drift = engine.device_drift(self.library, self.ipod)
        self.assertIsNotNone(drift)
        self.assertEqual(drift.count(), 1)
        self.assertEqual(drift.changed[0][0], "device-picks")

        plan = self._plan(
            selected=[self.playlist, second],
            reconcile=True,
            adoptable_playlist_keys={"dual-test", "device-picks"},
        )
        self.assertEqual(
            plan.playlist_additions,
            (dual_ipod.DualPlaylistAddition("device-picks", ("safe",)),),
        )
        self.assertFalse(plan.media_copies)
        device_write = next(
            item for item in plan.playlists
            if item.playlist_key == "device-picks")
        # Rockbox's volume-qualified spelling is recognized as the same owned
        # media path, then normalized back to SoundBox's canonical M3U8 form.
        self.assertTrue(device_write.changed)
        commit_dual_ipod_sync(plan)

        updated = device_manifest.load_composite(self.ipod)
        self.assertEqual(
            updated["playlists"]["device-picks"]["tracks"],
            ["unsafe", "safe"],
        )
        second.added_keys = ["safe"]
        self.assertFalse(engine.device_drift(self.library, self.ipod).any())

    def test_duplicate_rockbox_append_is_removed_without_app_addition(self):
        commit_dual_ipod_sync(self._plan())
        manifest = device_manifest.load_composite(self.ipod)
        playlist_path = next(
            self.ipod / device_path.lstrip("/")
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "playlist"
            and row.get("source") == "dual-test"
        )
        safe_device_path = next(
            device_path
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "shared-stock-media"
            and row.get("source") == str(self.safe.resolve())
        )
        with playlist_path.open("a", encoding="utf-8") as output:
            output.write("/<HDD0>" + safe_device_path + "\n")

        drift = engine.device_drift(self.library, self.ipod)
        self.assertEqual(drift.count(), 1)
        plan = self._plan(
            reconcile=True,
            adoptable_playlist_keys={"dual-test"},
        )
        self.assertFalse(plan.playlist_additions)
        playlist_write = plan.playlists[0]
        self.assertTrue(playlist_write.changed)
        self.assertEqual(
            playlist_write.payload.decode("utf-8").count(safe_device_path),
            1,
        )
        commit_dual_ipod_sync(plan)
        self.assertEqual(
            playlist_path.read_text(encoding="utf-8").count(safe_device_path),
            1,
        )
        self.assertFalse(engine.device_drift(self.library, self.ipod).any())

    def test_unrecognized_rockbox_append_does_not_import(self):
        commit_dual_ipod_sync(self._plan())
        manifest = device_manifest.load_composite(self.ipod)
        playlist_path = next(
            self.ipod / device_path.lstrip("/")
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "playlist"
        )
        with playlist_path.open("a", encoding="utf-8") as output:
            output.write("/Music/Unknown/New Song.wav\n")

        self.assertEqual(engine.device_drift(
            self.library, self.ipod).count(), 1)
        with self.assertRaisesRegex(
                dual_ipod.DualIpodError,
                "automatic device imports are not enabled"):
            self._plan(
                reconcile=True,
                adoptable_playlist_keys={"dual-test"},
            )

    def test_nonprimary_rockbox_volume_does_not_acquire_ownership(self):
        commit_dual_ipod_sync(self._plan())
        manifest = device_manifest.load_composite(self.ipod)
        playlist_path = next(
            self.ipod / device_path.lstrip("/")
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "playlist"
        )
        safe_device_path = next(
            device_path
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "shared-stock-media"
            and row.get("source") == str(self.safe.resolve())
        )
        with playlist_path.open("a", encoding="utf-8") as output:
            output.write("/<HDD1>" + safe_device_path + "\n")

        with self.assertRaisesRegex(
                dual_ipod.DualIpodError, "unrecognized song"):
            self._plan(
                reconcile=True,
                adoptable_playlist_keys={"dual-test"},
            )

    def test_committed_noop_plan_does_not_hash_unchanged_device_artwork(self):
        commit_dual_ipod_sync(self._plan())
        real_verify = dual_ipod._verify_owned_before

        def reject_unchanged_art(root, device_path, row):
            if row.get("kind") == "rockbox-artwork":
                raise AssertionError(
                    f"unchanged artwork was read from the device: {device_path}")
            return real_verify(root, device_path, row)

        with mock.patch(
                "soundbox.dual_ipod._verify_owned_before",
                side_effect=reject_unchanged_art):
            plan = self._plan(reconcile=True)

        self.assertFalse(plan.requires_commit)

    def test_additive_playlist_does_not_rehash_manifest_proven_stock_media(self):
        first = self._plan()
        commit_dual_ipod_sync(first)
        manifest = device_manifest.load_composite(self.ipod)
        stock_path = next(
            self.ipod / device_path.lstrip("/")
            for device_path, row in manifest["files"].items()
            if row.get("kind") == "shared-stock-media"
        )
        second = Playlist(
            "Safe Again", ["GENRES"], ["safe"], pid="safe-again")
        self.library.playlists.append(second)
        real_sha256 = stock_ipod._sha256

        def reject_manifest_proven_media(path):
            if Path(path) == stock_path:
                raise AssertionError(
                    f"manifest-proven stock media was rehashed: {path}")
            return real_sha256(path)

        with mock.patch(
                "soundbox.stock_ipod._sha256",
                side_effect=reject_manifest_proven_media):
            plan = self._plan([second])

        self.assertTrue(plan.requires_commit)
        self.assertEqual(plan.stock_safe_sources, 1)

    def test_source_stat_change_marks_owning_playlist_for_sync(self):
        commit_dual_ipod_sync(self._plan())
        before = self.unsafe.stat()
        os.utime(
            self.unsafe,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )

        drift = engine.device_drift(self.library, self.ipod)

        self.assertIsNotNone(drift)
        self.assertIn(("dual-test", "Dual Test"), drift.changed)

    def test_legacy_media_profile_marks_owning_playlist_for_sync(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest = device_manifest.load_composite(self.ipod)
        media = next(
            row for row in manifest["files"].values()
            if row.get("kind") in {"shared-stock-media", "rockbox-media"}
        )
        media["media_profile"] = (
            "rockbox-no-embedded-art-normalized-tags-v4")
        manifest_path.write_text(json.dumps(
            manifest, indent=2, sort_keys=True), encoding="utf-8")

        drift = engine.device_drift(self.library, self.ipod)

        self.assertIsNotNone(drift)
        self.assertIn(("dual-test", "Dual Test"), drift.changed)

    def test_effective_title_change_is_drift_and_rewrites_wav_info(self):
        commit_dual_ipod_sync(self._plan())
        clean = engine.device_drift(
            self.library,
            self.ipod,
            title_overrides={"safe": "Safe"},
        )
        self.assertIsNotNone(clean)
        self.assertEqual(clean.count(), 0)

    def test_active_theme_size_gets_exact_art_and_prior_sizes_are_retained(self):
        theme_dir = self.ipod / ".rockbox/wps"
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.wps").write_text(
            "%Cl(4,4,200,200)%Cd\n", encoding="utf-8")
        config = self.ipod / ".rockbox/config.cfg"
        config.write_text(
            "wps: /.rockbox/wps/theme.wps\n", encoding="utf-8")

        first = self._plan()
        self.assertEqual(len(first.artwork_copies), 8)
        self.assertIn(
            Path("iPod_Control/Music/F00/AAAA.200x200.bmp"),
            {item.relative_path for item in first.artwork_copies},
        )
        commit_dual_ipod_sync(first)
        manifest = device_manifest.load_composite(self.ipod)
        self.assertEqual(manifest["rockbox_artwork_sides"], [200, 160, 116])

        (theme_dir / "new.wps").write_text(
            "%Cl(4,4,180,180)%Cd\n", encoding="utf-8")
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "theme.wps", "new.wps"),
            encoding="utf-8",
        )
        drift = engine.device_drift(self.library, self.ipod)
        self.assertIsNotNone(drift)
        self.assertIn(
            ("rockbox-theme-art", "Rockbox theme artwork"), drift.changed)

        second = self._plan(reconcile=True)
        self.assertEqual(len(second.artwork_copies), 2)
        self.assertTrue(all(
            item.relative_path.name.endswith(".180x180.bmp")
            for item in second.artwork_copies))
        self.assertFalse(any(
            item.relative_path.name.endswith(".200x200.bmp")
            for item in second.orphan_deletes))

    def test_exact_size_bmp_profile_replaces_owned_legacy_art_paths(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest = device_manifest.load_composite(self.ipod)
        manifest["profile"] = "dual-single-copy-no-embedded-rgb160-tags-v3"
        for row in manifest["playlists"].values():
            row["export_profile"] = manifest["profile"]
            row.pop("metadata_sig", None)

        rewritten_files = {}
        legacy_paths = []
        for device_path, row in manifest["files"].items():
            if row.get("kind") != "rockbox-artwork":
                rewritten_files[device_path] = row
                continue
            current = self.ipod / device_path.lstrip("/")
            legacy = current.with_name(current.name + ".legacy.jpg")
            current.rename(legacy)
            legacy_device_path = "/" + str(legacy.relative_to(self.ipod))
            rewritten_files[legacy_device_path] = row
            legacy_paths.append(legacy)
        manifest["files"] = rewritten_files
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

        plan = self._plan(reconcile=True)
        self.assertEqual(len(plan.artwork_copies), 6)
        self.assertEqual(sum(
            item.kind == "rockbox-artwork" for item in plan.orphan_deletes), 6)
        commit_dual_ipod_sync(plan)
        self.assertTrue(all(not path.exists() for path in legacy_paths))
        migrated = device_manifest.load_composite(self.ipod)
        artwork_paths = {
            path for path, row in migrated["files"].items()
            if row.get("kind") == "rockbox-artwork"
        }
        self.assertEqual(len(artwork_paths), 6)
        self.assertTrue(any(path.endswith("AAAA.bmp") for path in artwork_paths))
        self.assertTrue(any(
            path.endswith("Unsafe 24.bmp") for path in artwork_paths))

        titles = {"safe": "Visible edited title"}
        drift = engine.device_drift(
            self.library,
            self.ipod,
            title_overrides=titles,
        )
        self.assertIsNotNone(drift)
        self.assertEqual(drift.count(), 1)

        changed = self._plan(
            title_overrides=titles,
            reconcile=True,
        )
        self.assertTrue(changed.requires_commit)
        self.assertEqual(len(changed.stock.rewrites), 1)
        commit_dual_ipod_sync(changed)
        self.assertEqual(
            _riff_info_values(self.ipod / "iPod_Control/Music/F00/AAAA.wav"),
            ("Visible edited title", "Fixture"),
        )
        clean = engine.device_drift(
            self.library,
            self.ipod,
            title_overrides=titles,
        )
        self.assertIsNotNone(clean)
        self.assertEqual(clean.count(), 0)

    def test_manifest_failure_rolls_back_stock_and_all_new_rockbox_files(self):
        before_database = _hash(self.ipod / "iPod_Control/iTunes/iTunesDB")
        before_media = _hash(self.ipod / "iPod_Control/Music/F00/AAAA.wav")
        plan = self._plan()
        real_write = DeviceSession.atomic_write

        def fail_manifest(session, destination, data):
            if Path(destination).name == "manifest.json":
                raise OSError("injected manifest failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_manifest):
            with self.assertRaisesRegex(OSError, "injected manifest failure"):
                commit_dual_ipod_sync(plan)

        self.assertEqual(_hash(self.ipod / "iPod_Control/iTunes/iTunesDB"), before_database)
        self.assertEqual(_hash(self.ipod / "iPod_Control/Music/F00/AAAA.wav"), before_media)
        self.assertFalse((self.ipod / ".soundbox/manifest.json").exists())
        self.assertFalse((self.ipod / ".rockbox/config.cfg").exists())
        for item in plan.media_copies + plan.artwork_copies:
            self.assertFalse((self.ipod / item.relative_path).exists())
        for item in plan.playlists:
            self.assertFalse((self.ipod / item.relative_path).exists())

    def test_reconcile_plan_does_not_stream_managed_device_audio(self):
        commit_dual_ipod_sync(self._plan())
        real_dual_hash = __import__(
            "soundbox.dual_ipod", fromlist=["_sha256"])._sha256
        real_stock_hash = stock_ipod._sha256

        def forbid_device_audio(real_hash):
            def guarded(path):
                candidate = Path(path)
                if (
                    candidate.is_relative_to(self.ipod)
                    and candidate.suffix.lower() in {".mp3", ".wav"}
                ):
                    raise AssertionError(
                        f"planner streamed managed device audio: {candidate}")
                return real_hash(candidate)
            return guarded

        with (
            mock.patch(
                "soundbox.dual_ipod._sha256",
                side_effect=forbid_device_audio(real_dual_hash),
            ),
            mock.patch(
                "soundbox.stock_ipod._sha256",
                side_effect=forbid_device_audio(real_stock_hash),
            ),
        ):
            plan = plan_dual_ipod_sync(
                self.library,
                [self.playlist],
                self.ipod,
                model_number="MA446",
                filesystem="msdos",
                rockbox_media_cache=self.media_cache,
                rockbox_art_cache=self.rockbox_art_cache,
                art_cache=self.art_cache,
                stock_art_cache=self.stock_art_cache,
                stock_shadow_store=self.stock_shadow_store,
                identity_key="fixture-device",
                title_overrides={
                    "safe": "Safe renamed",
                    "unsafe": "Unsafe renamed",
                },
                reconcile=True,
            )

        self.assertEqual(len(plan.stock.rewrites), 1)
        self.assertEqual(
            sum(item.kind == "rockbox-media" for item in plan.file_rewrites),
            1,
        )
        result = commit_dual_ipod_sync(plan)
        self.assertFalse(list(
            (result.stock.recovery_directory / "media").rglob("*.before")))
        dual_recovery = self.ipod / ".soundbox/dual-recovery"
        self.assertFalse([
            path for path in dual_recovery.rglob("*.before")
            if "/files/Music/" in str(path).replace("\\", "/")
        ])
    def test_incremental_playlist_add_accumulates_then_repeats_as_noop(self):
        first = self._plan()
        commit_dual_ipod_sync(first)

        safe_two = self.sources / "safe-two.wav"
        unsafe_two = self.sources / "unsafe-two.wav"
        _wave(safe_two, 661, 300)
        _wave(unsafe_two, 771, 400, width=3)
        self.library.tracks_by_id.update({
            "safe-two": Track(
                "safe-two", "Safe Two", "Fixture", safe_two.as_uri()),
            "unsafe-two": Track(
                "unsafe-two", "Unsafe Two", "Fixture", unsafe_two.as_uri()),
        })
        second_playlist = Playlist(
            "Second Test", [], ["safe-two", "unsafe-two"], pid="second-test")
        self.library.playlists.append(second_playlist)

        second = self._plan([second_playlist])
        self.assertEqual(second.generation, 2)
        self.assertEqual(len(second.playlists), 2)
        self.assertEqual(
            sum(item.changed for item in second.playlists), 1)
        self.assertEqual(second.stock_safe_sources, 2)
        self.assertEqual(second.rockbox_only_sources, 2)
        self.assertEqual(len(second.stock.copies), 1)
        self.assertEqual(len(second.media_copies), 1)
        commit_dual_ipod_sync(second)

        manifest = json.loads(
            (self.ipod / ".soundbox/manifest.json").read_text())
        self.assertEqual(manifest["generation"], 2)
        self.assertEqual(
            manifest["playlist_order"], ["dual-test", "second-test"])
        database_tracks = get_ipod_tracks(read_ipod(self.ipod))
        self.assertEqual(len(database_tracks), 2)
        playlist_paths = {
            row["source"]: self.ipod / path.lstrip("/")
            for path, row in manifest["files"].items()
            if row["kind"] == "playlist"
        }
        second_text = playlist_paths["second-test"].read_text()
        self.assertIn("/iPod_Control/Music/", second_text)
        self.assertIn("/Music/Fixture/Unsafe Two/Unsafe Two.wav", second_text)
        self.assertTrue(playlist_paths["dual-test"].is_file())

        unchanged = self._plan([second_playlist])
        self.assertEqual(unchanged.generation, 2)
        self.assertFalse(unchanged.requires_commit)
        self.assertFalse(unchanged.stock.requires_commit)
        self.assertFalse(any(item.changed for item in unchanged.playlists))

    def test_device_playlist_names_use_leaf_and_disambiguate_collisions(self):
        unique = [
            Playlist("Dubstep", ["GENRES"], [], pid="dubstep"),
            Playlist("House", ["GENRES"], [], pid="house"),
        ]
        self.assertEqual(
            dual_ipod._device_playlist_names(unique),
            ["Dubstep", "House"],
        )

        duplicated = [
            Playlist("Warmup", ["HOUSE"], [], pid="house-warmup"),
            Playlist("Warmup", ["DUBSTEP"], [], pid="dubstep-warmup"),
        ]
        self.assertEqual(
            dual_ipod._device_playlist_names(duplicated),
            ["HOUSE / Warmup", "DUBSTEP / Warmup"],
        )

    def test_reordered_manifest_sequence_drives_stock_and_rockbox_order(self):
        commit_dual_ipod_sync(self._plan())
        second = Playlist(
            "Second Test", ["GENRES"], ["safe"], pid="second-test")
        self.library.playlists.append(second)
        commit_dual_ipod_sync(self._plan([second]))

        reordered = self._plan(
            [second, self.playlist],
            reconcile=True,
            respect_selected_order=True,
        )
        self.assertTrue(reordered.requires_commit)
        self.assertTrue(all(item.changed for item in reordered.playlists))
        self.assertEqual(
            [item.relative_path.name for item in reordered.playlists],
            ["01 - Second Test.m3u8", "02 - Dual Test.m3u8"],
        )
        self.assertTrue(any(
            item.order_changed for item in reordered.playlists))
        commit_dual_ipod_sync(reordered)

        manifest = device_manifest.load_composite(self.ipod)
        self.assertEqual(
            manifest["playlist_order"], ["second-test", "dual-test"])
        playlist_paths = {
            row["source"]: self.ipod / path.lstrip("/")
            for path, row in manifest["files"].items()
            if row.get("kind") == "playlist"
        }
        self.assertLess(
            playlist_paths["second-test"].stat().st_mtime,
            playlist_paths["dual-test"].stat().st_mtime,
        )
        parsed = read_ipod(self.ipod)
        managed_names = [
            str(row.get("Title") or "")
            for row in (parsed.get("mhlp") or [])
            if not row.get("isMaster")
            and str(row.get("Title") or "") in {"Second Test", "Dual Test"}
        ]
        self.assertEqual(managed_names, ["Second Test", "Dual Test"])

        noop = self._plan(
            [second, self.playlist],
            reconcile=True,
            respect_selected_order=True,
        )
        self.assertFalse(noop.requires_commit)

    def test_delete_immediately_after_reorder_uses_reordered_generation(self):
        commit_dual_ipod_sync(self._plan())
        second = Playlist(
            "Second Test", ["GENRES"], ["safe"], pid="second-test")
        self.library.playlists.append(second)
        commit_dual_ipod_sync(self._plan([second]))
        commit_dual_ipod_sync(self._plan(
            [second, self.playlist],
            reconcile=True,
            respect_selected_order=True,
        ))

        manifest = device_manifest.load_composite(self.ipod)
        expected = {"dual-test": dict(manifest["playlists"]["dual-test"])}
        deletion = self._plan(
            [self.playlist],
            reconcile=True,
            expected_playlist_rows=expected,
        )

        self.assertTrue(deletion.requires_commit)
        commit_dual_ipod_sync(deletion)
        final = device_manifest.load_composite(self.ipod)
        self.assertEqual(final["playlist_order"], ["dual-test"])

    def test_incremental_playlist_rewrite_rolls_back_on_manifest_failure(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = self.ipod / ".soundbox/manifest.json"
        manifest_before = manifest_path.read_bytes()
        playlist_path = self.ipod / "Playlists/01 - Dual Test.m3u8"
        playlist_before = playlist_path.read_bytes()
        database_before = _hash(self.ipod / "iPod_Control/iTunes/iTunesDB")

        self.playlist.keys = ["unsafe", "safe"]
        changed = self._plan()
        self.assertTrue(changed.requires_commit)
        self.assertEqual(sum(item.changed for item in changed.playlists), 1)
        real_write = DeviceSession.atomic_write

        def fail_manifest(session, destination, data):
            if Path(destination).name == "manifest.json":
                raise OSError("injected later-generation manifest failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_manifest):
            with self.assertRaisesRegex(
                    OSError, "later-generation manifest failure"):
                commit_dual_ipod_sync(changed)

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(playlist_path.read_bytes(), playlist_before)
        self.assertEqual(
            _hash(self.ipod / "iPod_Control/iTunes/iTunesDB"), database_before)

    def test_incremental_media_repair_is_recovery_backed(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = self.ipod / ".soundbox/manifest.json"
        manifest_before = manifest_path.read_bytes()
        manifest = json.loads(manifest_before)
        media_device_path = next(
            path for path, row in manifest["files"].items()
            if row["kind"] == "rockbox-media")
        live_media = self.ipod / media_device_path.lstrip("/")
        media_before = live_media.read_bytes()

        _wave(self.unsafe, 991, 900, width=3)
        changed = self._plan()
        self.assertEqual(len(changed.file_rewrites), 1)
        real_write = DeviceSession.atomic_write

        def fail_manifest(session, destination, data):
            if Path(destination).name == "manifest.json":
                raise OSError("injected media-generation failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_manifest):
            with self.assertRaisesRegex(
                    OSError, "media-generation failure"):
                commit_dual_ipod_sync(changed)

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(live_media.read_bytes(), media_before)

    def test_composite_drift_and_sync_reconcile_both_playlist_views(self):
        commit_dual_ipod_sync(self._plan())
        # Missing/unsupported-by-both members were intentionally omitted, so
        # they do not create permanent phantom drift after a completed export.
        self.assertFalse(engine.device_drift(self.library, self.ipod).any())

        self.playlist.name = "Dual Renamed"
        self.playlist.folder_path = ["Sets"]
        self.playlist.keys = ["unsafe", "safe", "unsupported", "missing"]
        drift = engine.device_drift(self.library, self.ipod)
        self.assertEqual(drift.count(), 1)
        self.assertEqual([row[0] for row in drift.renamed], ["dual-test"])
        self.assertEqual([row[0] for row in drift.moved], ["dual-test"])
        self.assertEqual([row[0] for row in drift.changed], ["dual-test"])

        current = device_manifest.managed_current_playlists(
            device_manifest.load_composite(self.ipod), self.library)
        plan = plan_dual_ipod_sync(
            self.library,
            current,
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )
        self.assertEqual(len(plan.file_deletes), 1)
        old_playlist = self.ipod / plan.file_deletes[0].relative_path
        self.assertTrue(old_playlist.is_file())
        new_playlist = self.ipod / plan.playlists[0].relative_path
        self.assertEqual(
            plan.playlists[0].relative_path,
            Path("Playlists/01 - Dual Renamed.m3u8"),
        )
        commit_dual_ipod_sync(plan)

        self.assertFalse(old_playlist.exists())
        self.assertTrue(new_playlist.is_file())
        self.assertLess(
            new_playlist.read_text().index("/Music/Fixture/Unsafe 24/Unsafe 24.wav"),
            new_playlist.read_text().index("/iPod_Control/Music/F00/AAAA.wav"),
        )
        parsed = read_ipod(self.ipod)
        regular_names = {
            str(row.get("Title") or "")
            for row in (parsed.get("mhlp") or []) if not row.get("isMaster")
        }
        self.assertIn("Dual Renamed", regular_names)
        self.assertNotIn("Dual Test", regular_names)
        self.assertFalse(engine.device_drift(self.library, self.ipod).any())

    def test_legacy_nested_playlist_migrates_into_catalogue_root(self):
        self.playlist.folder_path = ["GENRES"]
        commit_dual_ipod_sync(self._plan())
        flat = self.ipod / "Playlists/01 - Dual Test.m3u8"
        legacy_flat = self.ipod / "Playlists/GENRES - Dual Test.m3u8"
        nested = self.ipod / "Playlists/GENRES/Dual Test.m3u8"
        nested.parent.mkdir(parents=True)
        flat.rename(nested)

        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest = device_manifest.load_composite(self.ipod)
        legacy_profile = (
            "dual-single-copy-no-embedded-universal-bmp-early-riffinfo-v8")
        manifest["profile"] = legacy_profile
        for row in manifest["playlists"].values():
            row["export_profile"] = legacy_profile
        file_row = manifest["files"].pop(
            "/Playlists/01 - Dual Test.m3u8")
        manifest["files"]["/Playlists/GENRES/Dual Test.m3u8"] = file_row
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

        plan = self._plan()
        self.assertEqual(
            [item.relative_path for item in plan.playlists],
            [Path("Playlists/01 - Dual Test.m3u8")],
        )
        self.assertIn(
            Path("Playlists/GENRES/Dual Test.m3u8"),
            {item.relative_path for item in plan.file_deletes},
        )
        commit_dual_ipod_sync(plan)

        self.assertTrue(flat.is_file())
        self.assertFalse(legacy_flat.exists())
        self.assertFalse(nested.exists())
        self.assertEqual(
            device_manifest.load_composite(self.ipod)["profile"],
            device_manifest.COMPOSITE_EXPORT_PROFILE,
        )

    def test_current_profile_unnumbered_playlist_migrates_on_normal_sync(self):
        commit_dual_ipod_sync(self._plan())
        numbered = self.ipod / "Playlists/01 - Dual Test.m3u8"
        unnumbered = self.ipod / "Playlists/Dual Test.m3u8"
        numbered.rename(unnumbered)

        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest = device_manifest.load_composite(self.ipod)
        self.assertEqual(
            manifest["profile"], device_manifest.COMPOSITE_EXPORT_PROFILE)
        file_row = manifest["files"].pop(
            "/Playlists/01 - Dual Test.m3u8")
        manifest["files"]["/Playlists/Dual Test.m3u8"] = file_row
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

        plan = self._plan()

        self.assertTrue(plan.requires_commit)
        self.assertEqual(
            [item.relative_path for item in plan.playlists],
            [Path("Playlists/01 - Dual Test.m3u8")],
        )
        self.assertIn(
            Path("Playlists/Dual Test.m3u8"),
            {item.relative_path for item in plan.file_deletes},
        )
        commit_dual_ipod_sync(plan)
        self.assertTrue(numbered.is_file())
        self.assertFalse(unnumbered.exists())

    def test_composite_sync_removes_deleted_playlist_and_orphan_media(self):
        commit_dual_ipod_sync(self._plan())
        manifest_before = device_manifest.load_composite(self.ipod)
        playlist_path = next(
            self.ipod / path.lstrip("/")
            for path, row in manifest_before["files"].items()
            if row.get("kind") == "playlist"
        )
        media_paths = {
            row.get("kind"): self.ipod / path.lstrip("/")
            for path, row in manifest_before["files"].items()
            if row.get("kind") in {"shared-stock-media", "rockbox-media"}
        }
        self.library.playlists.clear()
        self.assertEqual(engine.device_drift(self.library, self.ipod).count(), 1)
        plan = plan_dual_ipod_sync(
            self.library,
            [],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )
        self.assertEqual(len(plan.file_deletes), 1)
        self.assertTrue(any(
            item.kind == "rockbox-media" for item in plan.orphan_deletes))
        self.assertGreater(plan.retained_stale_files, 0)
        result = commit_dual_ipod_sync(plan)

        self.assertFalse(playlist_path.exists())
        self.assertTrue(media_paths["shared-stock-media"].is_file())
        self.assertFalse(media_paths["rockbox-media"].exists())
        self.assertGreater(result.deleted_orphans, 0)
        self.assertFalse(result.cleanup_failures)
        parsed = read_ipod(self.ipod)
        regular_names = {
            str(row.get("Title") or "")
            for row in (parsed.get("mhlp") or []) if not row.get("isMaster")
        }
        self.assertEqual(regular_names, {"Existing"})
        self.assertFalse(device_manifest.load_composite(self.ipod)["playlists"])
        shadow = self.stock_shadow_store.load("fixture-device")
        self.assertIsNotNone(shadow)
        assert shadow is not None
        self.assertFalse(shadow.playlists)
        self.assertFalse(shadow.sources)
        self.assertFalse(shadow.artwork)
        self.assertIsNone(engine.device_drift(self.library, self.ipod))

    def test_composite_sync_to_zero_removes_shared_media_and_stock_artwork(self):
        capabilities = capabilities_for_family_gen("iPod Video", "5.5th Gen")
        self.assertIsNotNone(capabilities)
        existing_media = self.ipod / "iPod_Control/Music/F00/AAAA.wav"
        self.assertTrue(write_itunesdb(
            str(self.ipod),
            [TrackInfo(
                title="Safe",
                artist="Fixture",
                location=":iPod_Control:Music:F00:AAAA.wav",
                size=existing_media.stat().st_size,
                length=10,
                filetype="wav",
                sample_rate=44_100,
                dbid=101,
            )],
            backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=[],
            capabilities=capabilities,
        ))
        commit_dual_ipod_sync(self._plan())
        manifest = device_manifest.load_composite(self.ipod)
        managed_media = [
            self.ipod / path.lstrip("/")
            for path, row in manifest["files"].items()
            if row.get("kind") in {"shared-stock-media", "rockbox-media"}
        ]
        # Deletion is authorized by the exact manifest/live baseline. It must
        # not depend on regenerating an old artwork profile or on sources that
        # are no longer selected for export.
        for source in self.art_cache.values.values():
            source.unlink(missing_ok=True)
        self.library.playlists.clear()
        plan = plan_dual_ipod_sync(
            self.library,
            [],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )

        self.assertEqual(plan.stock.removed_track_count, 1)
        self.assertEqual(plan.stock.final_track_count, 0)
        self.assertEqual(len(plan.stock.media_deletes), 1)
        self.assertEqual(len(plan.stock.artwork_delete_paths), 3)
        self.assertTrue(any(
            item.kind == "shared-stock-media" for item in plan.orphan_deletes))
        self.assertEqual(sum(
            item.kind == "stock-artwork" for item in plan.file_deletes), 3)
        progress = []
        self.assertEqual(plan.progress_total, 2)
        result = commit_dual_ipod_sync(
            plan,
            on_progress=lambda done, total, _label: progress.append(
                (done, total)),
        )

        self.assertEqual(get_ipod_tracks(read_ipod(self.ipod)), [])
        self.assertEqual(progress, [(0, 2), (1, 2), (2, 2)])
        self.assertTrue(all(not path.exists() for path in managed_media))
        artwork = self.ipod / "iPod_Control/Artwork"
        self.assertTrue(all(not (artwork / name).exists() for name in (
            "ArtworkDB", "F1028_1.ithmb", "F1029_1.ithmb")))
        self.assertGreaterEqual(result.deleted_orphans, 2)
        self.assertFalse(list(
            (self.ipod / ".soundbox/dual-recovery").rglob(
                "orphans/**/*.before")))

    def test_collection_delete_prunes_orphan_on_next_composite_sync(self):
        commit_dual_ipod_sync(self._plan())
        manifest_before = device_manifest.load_composite(self.ipod)
        unsafe_path = next(
            self.ipod / path.lstrip("/")
            for path, row in manifest_before["files"].items()
            if row.get("kind") == "rockbox-media"
        )
        unsafe_source_hash = _hash(self.unsafe)
        self.library.hidden_ids.add("unsafe")

        current = device_manifest.managed_current_playlists(
            manifest_before, self.library)
        plan = plan_dual_ipod_sync(
            self.library,
            current,
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )

        self.assertTrue(any(
            item.relative_path == unsafe_path.relative_to(self.ipod)
            for item in plan.orphan_deletes))
        result = commit_dual_ipod_sync(plan)
        self.assertFalse(unsafe_path.exists())
        self.assertGreater(result.deleted_orphans, 0)
        self.assertEqual(_hash(self.unsafe), unsafe_source_hash)
        playlist_path = next(
            self.ipod / path.lstrip("/")
            for path, row in device_manifest.load_composite(
                self.ipod)["files"].items()
            if row.get("kind") == "playlist"
        )
        self.assertNotIn("Unsafe 24.wav", playlist_path.read_text())

    def test_empty_composite_plan_removes_appledouble_clutter_and_empty_dirs(self):
        commit_dual_ipod_sync(self._plan())
        self.library.playlists.clear()
        commit_dual_ipod_sync(self._plan([], reconcile=True))

        stale_dir = self.ipod / "Music" / "Old Artist"
        stale_dir.mkdir(parents=True)
        (stale_dir / "._Old Song.wav").write_bytes(b"AppleDouble")
        (self.ipod / "Music" / "._Old Artist").write_bytes(b"AppleDouble")

        drift = engine.device_drift(self.library, self.ipod)
        self.assertIsNone(drift)

        plan = self._plan([], reconcile=True)
        clutter = [
            item for item in plan.orphan_deletes
            if item.kind == "rockbox-clutter"
        ]
        self.assertEqual(len(clutter), 2)
        self.assertEqual(plan.summary()["rockbox_clutter_files"], 2)
        self.assertTrue(plan.requires_commit)

        result = commit_dual_ipod_sync(plan)
        self.assertEqual(result.deleted_orphans, 2)
        self.assertFalse(list((self.ipod / "Music").rglob("._*")))
        self.assertFalse(stale_dir.exists())

    def test_interrupted_rockbox_write_requires_reconciliation(self):
        commit_dual_ipod_sync(self._plan())
        music = self.ipod / "Music"
        (music / "._Finder metadata").write_bytes(b"AppleDouble")

        clean = engine.device_drift(self.library, self.ipod)
        self.assertIsNotNone(clean)
        self.assertFalse(clean.any())

        (music / "interrupted.wav.sbpart").write_bytes(b"partial")
        drift = engine.device_drift(self.library, self.ipod)
        self.assertIsNotNone(drift)
        self.assertIn(("rockbox-clutter", "Rockbox cleanup"), drift.changed)

    def test_interrupted_completed_media_is_verified_and_adopted_on_retry(self):
        commit_dual_ipod_sync(self._plan())
        second_source = self.sources / "second-unsafe24.wav"
        _wave(second_source, 1_323, 300, width=3)
        self.library.tracks_by_id["second-unsafe"] = Track(
            "second-unsafe", "Second Unsafe", "Fixture", second_source.as_uri())
        second = Playlist(
            "Second", [], ["second-unsafe"], pid="second")

        interrupted = self._plan([second])
        media = next(
            item for item in interrupted.media_copies
            if item.kind == "rockbox-media")
        prepared = self.media_cache.materialize(media.media_plan)
        try:
            landed = self.ipod / media.relative_path
            landed.parent.mkdir(parents=True, exist_ok=True)
            landed.write_bytes(prepared.payload.read_bytes())
        finally:
            self.media_cache.discard(prepared)
        manifest_path = device_manifest.manifest_path(self.ipod)
        baseline = _hash(manifest_path)
        recovery = (
            self.ipod / ".soundbox/dual-recovery"
            / f"20260802-120000-{baseline[:12]}"
        )
        recovery.mkdir(parents=True)
        journal = interrupted.summary()
        # Playlist membership may legitimately change between the interrupted
        # attempt and retry; exact overlapping files remain recoverable.
        journal["stock_safe_sources"] += 1
        (recovery / "plan.json").write_text(json.dumps(
            journal, indent=2, sort_keys=True))

        drift = engine.device_drift(self.library, self.ipod)
        self.assertIsNotNone(drift)
        self.assertIn(
            ("interrupted-transaction", "Interrupted iPod sync recovery"),
            drift.changed,
        )

        retry = self._plan([second])
        self.assertEqual(len(retry.media_copies), 0)
        self.assertEqual(len(retry.file_adoptions), 1)
        self.assertEqual(
            retry.summary()["recovered_rockbox_media_files"], 1)
        self.assertEqual(
            retry.progress_total,
            len(retry.stock.copies) + 1,
        )

        progress = []
        result = commit_dual_ipod_sync(
            retry,
            on_progress=lambda done, total, label: progress.append(
                (done, total, label)),
        )
        self.assertEqual(result.copied_media, 0)
        self.assertEqual(result.recovered_media, 1)
        self.assertIn(
            (1, retry.progress_total, str(media.relative_path)),
            progress,
        )
        manifest = device_manifest.load_composite(self.ipod)
        device_path = "/" + str(media.relative_path).replace(os.sep, "/")
        self.assertEqual(
            manifest["files"][device_path]["sha256"], media.sha256)

    def test_interrupted_completed_media_is_verified_before_commit_writes(self):
        commit_dual_ipod_sync(self._plan())
        second_source = self.sources / "second-unsafe24.wav"
        _wave(second_source, 1_323, 300, width=3)
        self.library.tracks_by_id["second-unsafe"] = Track(
            "second-unsafe", "Second Unsafe", "Fixture", second_source.as_uri())
        second = Playlist("Second", [], ["second-unsafe"], pid="second")
        interrupted = self._plan([second])
        media = next(
            item for item in interrupted.media_copies
            if item.kind == "rockbox-media")
        landed = self.ipod / media.relative_path
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_bytes(b"x" * media.size)
        baseline = _hash(device_manifest.manifest_path(self.ipod))
        recovery = (
            self.ipod / ".soundbox/dual-recovery"
            / f"20260802-120001-{baseline[:12]}"
        )
        recovery.mkdir(parents=True)
        (recovery / "plan.json").write_text(json.dumps(
            interrupted.summary(), indent=2, sort_keys=True))

        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest_before = manifest_path.read_bytes()
        retry = self._plan([second])
        self.assertEqual(len(retry.file_adoptions), 1)

        with self.assertRaisesRegex(
                RuntimeError, "changed after dry-run"):
            commit_dual_ipod_sync(retry)
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(landed.read_bytes(), b"x" * media.size)

    def test_interrupted_stock_media_is_verified_and_adopted_on_retry(self):
        commit_dual_ipod_sync(self._plan())
        second_source = self.sources / "second-safe.wav"
        _wave(second_source, 1_323, 300)
        self.library.tracks_by_id["second-safe"] = Track(
            "second-safe", "Second Safe", "Fixture", second_source.as_uri())
        second = Playlist("Second", [], ["second-safe"], pid="second")
        interrupted = self._plan([second])
        stock_copy = interrupted.stock.copies[0]
        landed = self.ipod / stock_copy.relative_path
        landed.parent.mkdir(parents=True, exist_ok=True)
        if stock_copy.media_plan is not None:
            prepared = self.media_cache.materialize(stock_copy.media_plan)
            try:
                landed.write_bytes(prepared.payload.read_bytes())
            finally:
                self.media_cache.discard(prepared)
        else:
            landed.write_bytes(stock_copy.source.read_bytes())
        baseline = _hash(device_manifest.manifest_path(self.ipod))
        recovery = (
            self.ipod / ".soundbox/dual-recovery"
            / f"20260802-120002-{baseline[:12]}"
        )
        recovery.mkdir(parents=True)
        (recovery / "plan.json").write_text(json.dumps(
            interrupted.summary(), indent=2, sort_keys=True))

        retry = self._plan([second])
        self.assertEqual(len(retry.stock.copies), 0)
        self.assertEqual(len(retry.stock.adoptions), 1)
        self.assertEqual(retry.summary()["recovered_stock_tracks"], 1)

        result = commit_dual_ipod_sync(retry)
        self.assertEqual(result.stock.copied_tracks, 0)
        self.assertEqual(result.stock.recovered_tracks, 1)
        tracks = get_ipod_tracks(read_ipod(self.ipod))
        self.assertIn(
            stock_copy.location,
            {str(item.get("location") or "") for item in tracks},
        )

    def test_collection_removal_keeps_media_referenced_by_another_playlist(self):
        commit_dual_ipod_sync(self._plan())
        second = Playlist(
            "Second Unsafe", [], ["unsafe"], pid="second-unsafe")
        self.library.playlists.append(second)
        commit_dual_ipod_sync(self._plan([second]))
        manifest_before = device_manifest.load_composite(self.ipod)
        unsafe_path = next(
            self.ipod / path.lstrip("/")
            for path, row in manifest_before["files"].items()
            if row.get("kind") == "rockbox-media"
        )
        self.library.removed_by_playlist[self.playlist.pid] = {"unsafe": "now"}
        current = device_manifest.managed_current_playlists(
            manifest_before, self.library)

        plan = plan_dual_ipod_sync(
            self.library,
            current,
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )

        self.assertFalse(any(
            item.relative_path == unsafe_path.relative_to(self.ipod)
            for item in plan.orphan_deletes))
        commit_dual_ipod_sync(plan)
        self.assertTrue(unsafe_path.is_file())
        manifest_after = device_manifest.load_composite(self.ipod)
        row = manifest_after["files"][
            "/" + str(unsafe_path.relative_to(self.ipod))]
        self.assertEqual(row["playlists"], ["second-unsafe"])

    def test_orphan_cleanup_waits_for_manifest_publication(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest_before = manifest_path.read_bytes()
        database = self.ipod / "iPod_Control/iTunes/iTunesDB"
        database_before = database.read_bytes()
        manifest = device_manifest.load_composite(self.ipod)
        unsafe_path = next(
            self.ipod / path.lstrip("/")
            for path, row in manifest["files"].items()
            if row.get("kind") == "rockbox-media"
        )
        self.library.hidden_ids.add("unsafe")
        current = device_manifest.managed_current_playlists(
            manifest, self.library)
        plan = plan_dual_ipod_sync(
            self.library,
            current,
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )
        real_write = DeviceSession.atomic_write

        def fail_manifest(session, destination, data):
            if Path(destination).name == "manifest.json":
                raise OSError("injected orphan manifest failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_manifest):
            with self.assertRaisesRegex(OSError, "orphan manifest failure"):
                commit_dual_ipod_sync(plan)

        self.assertTrue(unsafe_path.is_file())
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(database.read_bytes(), database_before)

    def test_orphan_cleanup_failure_rolls_back_before_manifest(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest_before = manifest_path.read_bytes()
        database_path = self.ipod / "iPod_Control/iTunes/iTunesDB"
        database_before = database_path.read_bytes()
        manifest = device_manifest.load_composite(self.ipod)
        unsafe_path = next(
            self.ipod / path.lstrip("/")
            for path, row in manifest["files"].items()
            if row.get("kind") == "rockbox-media"
        )
        self.library.hidden_ids.add("unsafe")
        current = device_manifest.managed_current_playlists(
            manifest, self.library)
        plan = plan_dual_ipod_sync(
            self.library,
            current,
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )
        real_replace = os.replace

        def fail_unsafe_replace(source, destination):
            if Path(source).resolve() == unsafe_path.resolve():
                raise OSError("injected orphan cleanup failure")
            return real_replace(source, destination)

        with mock.patch("soundbox.dual_ipod.os.replace", fail_unsafe_replace):
            with self.assertRaisesRegex(OSError, "orphan cleanup failure"):
                commit_dual_ipod_sync(plan)

        self.assertTrue(unsafe_path.is_file())
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(database_path.read_bytes(), database_before)

    def test_composite_rename_delete_rolls_back_if_manifest_publish_fails(self):
        commit_dual_ipod_sync(self._plan())
        manifest_path = device_manifest.manifest_path(self.ipod)
        manifest_before = manifest_path.read_bytes()
        database_before = _hash(self.ipod / "iPod_Control/iTunes/iTunesDB")
        old_playlist = self.ipod / "Playlists/01 - Dual Test.m3u8"
        old_payload = old_playlist.read_bytes()
        self.playlist.name = "Moved"
        self.playlist.folder_path = ["Other"]
        plan = plan_dual_ipod_sync(
            self.library,
            [self.playlist],
            self.ipod,
            model_number="MA446",
            filesystem="msdos",
            rockbox_media_cache=self.media_cache,
            rockbox_art_cache=self.rockbox_art_cache,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            stock_shadow_store=self.stock_shadow_store,
            identity_key="fixture-device",
            reconcile=True,
        )
        new_playlist = self.ipod / plan.playlists[0].relative_path
        real_write = DeviceSession.atomic_write

        def fail_manifest(session, destination, data):
            if Path(destination).name == "manifest.json":
                raise OSError("injected reconcile manifest failure")
            return real_write(session, destination, data)

        with mock.patch.object(DeviceSession, "atomic_write", fail_manifest):
            with self.assertRaisesRegex(
                    OSError, "reconcile manifest failure"):
                commit_dual_ipod_sync(plan)

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(old_playlist.read_bytes(), old_payload)
        self.assertFalse(new_playlist.exists())
        self.assertEqual(
            _hash(self.ipod / "iPod_Control/iTunes/iTunesDB"), database_before)


if __name__ == "__main__":
    unittest.main()
