import io
from dataclasses import replace
import os
import wave
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soundbox import manifest, rekordbox_xml, sync as sync_mod
from tests.support.library import write_xml
from soundbox.library import Library, Playlist, Track
from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache, RockboxMediaError, _riff_info_values


class TestSyncEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "library"
        self.src.mkdir()
        self.target = self.root / "USB"
        # Two real files + one referenced-but-missing track.
        (self.src / "good1.flac").write_bytes(b"AAAA")
        (self.src / "good2.mp3").write_bytes(b"BBBBBB")
        self.xml = self.src / "rb.xml"
        write_xml(
            self.xml,
            tracks=[
                ("1", "Good One", "Artist X", self.src / "good1.flac"),
                ("2", "Good Two", "Artist Y", self.src / "good2.mp3"),
                ("3", "Gone", "Lost Artist", self.src / "missing.wav"),
            ],
            playlist=["1", "2", "3"],
        )
        self.lib = rekordbox_xml.load(self.xml)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, layout="both", dry_run=True)
        self.assertFalse(self.target.exists(), "dry-run must not create the target")
        pr = rep.playlists[0]
        self.assertEqual(pr.in_playlist, 3)
        self.assertEqual(pr.found, 2)
        self.assertEqual(pr.missing, 1)
        self.assertEqual(rep.bytes_to_copy, 4 + 6)

    def test_explicit_folder_root_survives_rockbox_export_and_sync(self):
        canonical=Playlist('BASSSTATION',['SETS','HAMDI'],['1'],'selected')
        self.lib.playlists=[canonical]
        selected=replace(canonical,export_folder_path=['HAMDI'])
        sync_mod.run(self.lib,[selected],self.target,layout='tree',dry_run=False)
        destination=self.target/'Playlists/HAMDI/BASSSTATION.m3u8'
        self.assertTrue(destination.is_file())
        self.assertFalse((self.target/'Playlists/SETS').exists())
        recorded=manifest.load(self.target)
        self.assertEqual(recorded['playlists']['selected']['folder_path'],['SETS','HAMDI'])
        self.assertEqual(recorded['playlists']['selected']['export_folder_path'],['HAMDI'])
        sync_mod.run(self.lib,[canonical],self.target,layout='tree',dry_run=False)
        self.assertFalse((self.target/'Playlists/SETS').exists())
        sync_mod.resync(self.lib,self.target)
        self.assertTrue(destination.is_file())
        self.assertFalse((self.target/'Playlists/SETS').exists())
        self.assertEqual(canonical.folder_path,['SETS','HAMDI'])
        self.assertIsNone(canonical.export_folder_path)

    def test_rockbox_only_export_refuses_composite_manifest(self):
        manifest.save(self.target, {
            "schema": manifest.COMPOSITE_SCHEMA,
            "playlists": {},
            "files": {},
        })
        before = manifest.manifest_path(self.target).read_bytes()

        with self.assertRaisesRegex(RuntimeError, "composite dual-library"):
            sync_mod.run(
                self.lib, self.lib.playlists, self.target, dry_run=True)

        self.assertEqual(manifest.manifest_path(self.target).read_bytes(), before)

    def test_partial_reexport_preserves_other_playlist_media_and_art_owners(self):
        first = Playlist("First", [], ["1"], pid="first")
        second = Playlist("Second", [], ["1"], pid="second")
        self.lib.playlists = [first, second]
        canonical_art = self.root / "art.jpg"
        canonical_art.write_bytes(b"fixture artwork")
        art_cache = mock.Mock()
        art_cache.image_path.return_value = canonical_art
        sync_mod.run(self.lib, [first, second], self.target, layout="tree",
                     dry_run=False, art_cache=art_cache)
        sync_mod.run(self.lib, [first], self.target, layout="tree",
                     dry_run=False, art_cache=art_cache)
        rows = manifest.load(self.target)["files"]
        for row in rows.values():
            if row["kind"] in {"track", "artwork"}:
                self.assertEqual(row["playlists"], ["first", "second"])
        self.lib.playlists = [second]
        report = sync_mod.resync(self.lib, self.target, art_cache=art_cache)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(
            (self.target / "Music/Artist X/Good One/Good One.flac").read_bytes(),
            b"AAAA")
        self.assertEqual(
            (self.target / "Music/Artist X/Good One/cover.jpg").read_bytes(),
            b"fixture artwork")
        self.assertIn("Good One.flac", (self.target / "Playlists/Second.m3u8").read_text())
        self.assertFalse((self.target / "Playlists/First.m3u8").exists())

    def test_same_source_conflicting_logical_metadata_refuses_before_writes(self):
        source = self.lib.tracks_by_id["1"].path
        tracks = {
            key: Track(key, "Original", "Artist", source.as_uri())
            for key in ("logical", "native")
        }
        playlist = Playlist("Same source", [], ["logical", "native"], pid="same")
        library = Library(tracks, [playlist], self.xml)
        original = source.read_bytes()
        for titles, artists in (
            ({"logical": "First", "native": "Second"}, {}),
            ({}, {"logical": "First artist", "native": "Second artist"}),
        ):
            with self.subTest(titles=titles, artists=artists), \
                    mock.patch.object(sync_mod.DeviceSession, "atomic_copy",
                                      side_effect=AssertionError("no device copy")):
                with self.assertRaisesRegex(RockboxMediaError, "conflicting Rockbox metadata"):
                    sync_mod.run(library, [playlist], self.target, dry_run=False,
                                 title_overrides=titles, artist_overrides=artists)
            self.assertFalse(self.target.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_same_source_compatible_logical_metadata_shares_payload_and_caches_display(self):
        source = self.lib.tracks_by_id["1"].path
        tracks = {
            key: Track(key, "Original", "Artist", source.as_uri())
            for key in ("logical", "native")
        }
        first = Playlist("First", [], ["logical", "native"], pid="first")
        second = Playlist("Second", [], ["native", "logical"], pid="second")
        library = Library(tracks, [first, second], self.xml)
        titles = {"logical": "Shared title", "native": "Shared title"}
        artists = {"logical": "Shared artist", "native": "Shared artist"}
        with mock.patch.object(sync_mod.metadata, "effective_title",
                               wraps=sync_mod.metadata.effective_title) as title, \
                mock.patch.object(sync_mod.metadata, "effective_artist",
                                  wraps=sync_mod.metadata.effective_artist) as artist:
            files, entries = sync_mod.plan(
                library, [first, second], self.target,
                title_overrides=titles, artist_overrides=artists)
        self.assertEqual(title.call_count, 2)
        self.assertEqual(artist.call_count, 2)
        self.assertEqual(len(files), 1)
        self.assertEqual(len({entry.device_path for group in entries.values() for entry in group}), 1)
        planned = next(iter(files.values()))
        self.assertEqual((planned.title, planned.artist), ("Shared title", "Shared artist"))
        report = sync_mod.run(library, [first, second], self.target, dry_run=False,
                             title_overrides=titles, artist_overrides=artists)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(sum(row.copied for row in report.playlists), 4)
        self.assertEqual(len(list((self.target / "Music").rglob("*.flac"))), 1)

    def test_separate_exports_isolate_equal_titles_and_keep_original_paths(self):
        first = Playlist("First", [], ["1"], pid="first")
        second = Playlist("Second", [], ["2"], pid="second")
        for track in self.lib.tracks_by_id.values():
            track.name = "Same title"
            track.artist = "Same artist"
        self.lib.playlists = [first, second]
        art = {}
        for track_id in ("1", "2"):
            art[track_id] = self.root / f"art-{track_id}.jpg"
            art[track_id].write_bytes(f"cover {track_id}".encode())
        art_cache = mock.Mock()
        art_cache.image_path.side_effect = art.get
        for playlist in (first, second):
            report = sync_mod.run(self.lib, [playlist], self.target, layout="tree",
                                  dry_run=False, art_cache=art_cache)
            self.assertFalse(any(row.errors for row in report.playlists))
        rows = manifest.load(self.target)["files"]
        media = {row["source"]: path for path, row in rows.items() if row["kind"] == "track"}
        self.assertEqual(len(media), 2)
        self.assertEqual(len({Path(path).parent for path in media.values()}), 2)
        for track_id in ("1", "2"):
            source = self.lib.tracks_by_id[track_id].path
            destination = self.target / media[str(source.resolve())].lstrip("/")
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual((destination.parent / "cover.jpg").read_bytes(),
                             art[track_id].read_bytes())
        # A reversed overlap batch must retain both committed bindings.
        sync_mod.run(self.lib, [second, first], self.target, layout="tree",
                     dry_run=False, art_cache=art_cache)
        final_media = {row["source"]: path for path, row in manifest.load(self.target)["files"].items()
                       if row["kind"] == "track"}
        self.assertEqual(final_media, media)

    def test_unchanged_resync_performs_no_payload_reads_or_device_writes(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False, concise_playlists=True)
        before = {path.relative_to(self.target): path.read_bytes()
                  for path in self.target.rglob("*") if path.is_file()}
        with mock.patch.object(sync_mod, "run", side_effect=AssertionError("no export")), \
                mock.patch.object(sync_mod.rockbox_config, "theme_album_art_sides",
                                  side_effect=AssertionError("no theme reads")), \
                mock.patch.object(manifest, "save", side_effect=AssertionError("no writes")), \
                mock.patch.object(sync_mod, "_sha256", side_effect=AssertionError("no payload reads")):
            report = sync_mod.resync(self.lib, self.target)
        self.assertFalse(report.cancelled)
        self.assertEqual(report.bytes_to_copy, 0)
        after = {path.relative_to(self.target): path.read_bytes()
                 for path in self.target.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def _forbid_audio_reads(self, track_ids=("1", "2")):
        sources = {self.lib.tracks_by_id[key].path.resolve() for key in track_ids}
        rows = manifest.load(self.target)["files"]
        forbidden = sources | {
            (self.target / path.lstrip("/")).resolve()
            for path, row in rows.items()
            if row["kind"] == "track" and row["source"] in {str(path) for path in sources}}
        original = Path.open
        def checked(path, *args, **kwargs):
            self.assertNotIn(path.resolve(), forbidden, "Retained audio was opened")
            return original(path, *args, **kwargs)
        return mock.patch.object(Path, "open", checked)

    def test_artwork_only_resync_updates_cover_without_opening_retained_audio(self):
        image = self.root / "art.jpg"
        image.write_bytes(b"old artwork")
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree",
                     dry_run=False, art_cache=cache)
        before = image.stat()
        replacement = self.root / "replacement.jpg"
        replacement.write_bytes(b"new artwork")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(image)
        with self._forbid_audio_reads(), \
                mock.patch.object(sync_mod, "acquire_source", side_effect=AssertionError("no acquisition")):
            report = sync_mod.resync(self.lib, self.target, art_cache=cache)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(report.bytes_to_copy, 0)
        self.assertEqual(report.playlists[0].already_present, 2)
        self.assertEqual((self.target / "Music/Artist X/Good One/cover.jpg").read_bytes(), b"new artwork")
        self.assertEqual(image.stat().st_mtime_ns, before.st_mtime_ns)

    def test_artwork_derivatives_update_then_unchanged_sync_does_no_payload_work(self):
        from PIL import Image
        image = self.root / "art.jpg"
        Image.new("RGB", (32, 32), (200, 20, 40)).save(image, "JPEG")
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        derivatives = RockboxArtworkCache(self.root / "art-cache")
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree", dry_run=False,
                     art_cache=cache, rockbox_art_cache=derivatives)
        folder = self.target / "Music/Artist X/Good One"
        before = {name: (folder / name).read_bytes()
                  for name in ("cover.160x160.bmp", "cover.116x116.bmp", "cover.bmp")}
        Image.new("RGB", (32, 32), (20, 180, 70)).save(image, "JPEG")
        with self._forbid_audio_reads(), \
                mock.patch.object(sync_mod, "acquire_source", side_effect=AssertionError("no acquisition")):
            report = sync_mod.resync(self.lib, self.target, art_cache=cache,
                                     rockbox_art_cache=derivatives)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(report.bytes_to_copy, 0)
        for name, raw in before.items():
            self.assertNotEqual((folder / name).read_bytes(), raw)
        with mock.patch.object(sync_mod, "_sha256", side_effect=AssertionError("no payload hash")), \
                mock.patch.object(derivatives, "prepare", side_effect=AssertionError("no derivative")), \
                mock.patch.object(manifest, "save", side_effect=AssertionError("no manifest write")), \
                mock.patch.object(sync_mod.rockbox_config, "theme_album_art_sides",
                                  side_effect=AssertionError("no theme read")):
            repeat = sync_mod.resync(self.lib, self.target, art_cache=cache,
                                     rockbox_art_cache=derivatives)
        self.assertEqual(repeat.playlists, [])

    def test_changed_art_on_overlap_member_preserves_shared_owners_and_retained_audio(self):
        first = Playlist("First", [], ["1"], pid="first")
        second = Playlist("Second", [], ["1"], pid="second")
        self.lib.playlists = [first, second]
        image = self.root / "art.jpg"
        image.write_bytes(b"old art")
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        sync_mod.run(self.lib, [first, second], self.target, layout="tree", dry_run=False,
                     art_cache=cache)
        first.keys.append("2")
        image.write_bytes(b"new art")
        with self._forbid_audio_reads(("1",)):
            report = sync_mod.resync(self.lib, self.target, art_cache=cache)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(report.bytes_to_copy, 6)
        rows = manifest.load(self.target)["files"]
        for path, row in rows.items():
            if path.startswith("/Music/Artist X/Good One/"):
                self.assertEqual(row["playlists"], ["first", "second"])
        self.assertEqual((self.target / "Music/Artist X/Good One/cover.jpg").read_bytes(), b"new art")

    def test_changed_art_cannot_replace_a_preexisting_unowned_cover(self):
        image = self.root / "art.jpg"
        image.write_bytes(b"preexisting art")
        cover = self.target / "Music/Artist X/Good One/cover.jpg"
        cover.parent.mkdir(parents=True)
        cover.write_bytes(image.read_bytes())
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree", dry_run=False,
                     art_cache=cache)
        path = "/Music/Artist X/Good One/cover.jpg"
        self.assertFalse(manifest.load(self.target)["files"][path]["owned"])
        image.write_bytes(b"new local art")
        with self._forbid_audio_reads():
            report = sync_mod.resync(self.lib, self.target, art_cache=cache)
        self.assertIn("unowned artwork conflict", " ".join(report.playlists[0].errors))
        self.assertEqual(cover.read_bytes(), b"preexisting art")
        saved = manifest.load(self.target)
        self.assertFalse(saved["files"][path]["owned"])
        self.assertEqual(saved["playlists"][manifest.playlist_key(self.lib.playlists[0])]["sig"], "partial")

    def test_unavailable_local_art_preserves_cover_through_membership_sync(self):
        playlist = Playlist("First", [], ["1"], pid="first")
        self.lib.playlists = [playlist]
        image = self.root / "art.jpg"
        image.write_bytes(b"keep this art")
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        sync_mod.run(self.lib, [playlist], self.target, layout="tree", dry_run=False, art_cache=cache)
        playlist.keys.append("2")
        cache.image_path.return_value = None
        cache.image_path.side_effect = None
        with self._forbid_audio_reads(("1",)):
            report = sync_mod.resync(self.lib, self.target, art_cache=cache)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual((self.target / "Music/Artist X/Good One/cover.jpg").read_bytes(), b"keep this art")
        self.assertEqual(manifest.load(self.target)["files"][
            "/Music/Artist X/Good One/cover.jpg"]["playlists"], ["first"])

    def test_artwork_cancellation_keeps_old_unprocessed_cover_for_retry(self):
        images = {key: self.root / f"art-{key}.jpg" for key in ("1", "2")}
        for key, image in images.items():
            image.write_bytes(f"old art {key}".encode())
        cache = mock.Mock()
        cache.image_path.side_effect = images.get
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree", dry_run=False,
                     art_cache=cache)
        for key, image in images.items():
            image.write_bytes(f"new art {key}".encode())
        stopped = False
        def progress(done, _total, _label):
            nonlocal stopped
            if done == 1:
                stopped = True
        with self._forbid_audio_reads():
            report = sync_mod.resync(self.lib, self.target, art_cache=cache,
                                     on_progress=progress, should_cancel=lambda: stopped)
        self.assertTrue(report.cancelled)
        self.assertEqual((self.target / "Music/Artist X/Good One/cover.jpg").read_bytes(), b"new art 1")
        self.assertEqual((self.target / "Music/Artist Y/Good Two/cover.jpg").read_bytes(), b"old art 2")
        with self._forbid_audio_reads():
            retry = sync_mod.resync(self.lib, self.target, art_cache=cache)
        self.assertFalse(retry.cancelled)
        self.assertFalse(any(row.errors for row in retry.playlists))
        self.assertEqual((self.target / "Music/Artist Y/Good Two/cover.jpg").read_bytes(), b"new art 2")

    def test_artwork_dry_run_with_retained_inventory_writes_nothing(self):
        image = self.root / "art.jpg"
        image.write_bytes(b"old art")
        cache = mock.Mock()
        cache.image_path.side_effect = lambda key: image if key == "1" else None
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree", dry_run=False,
                     art_cache=cache)
        before = {path: path.read_bytes() for path in self.target.rglob("*") if path.is_file()}
        image.write_bytes(b"new art")
        with mock.patch.object(sync_mod, "_export_artwork", side_effect=AssertionError("no art write")), \
                mock.patch.object(manifest, "save", side_effect=AssertionError("no manifest write")), \
                self._forbid_audio_reads():
            report = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=True,
                                  art_cache=cache, reuse_managed_inventory=True)
        self.assertTrue(report.dry_run)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_explicit_sync_propagates_source_display_edits_and_removes_old_owned_paths(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, layout="tree", dry_run=False)
        source = self.lib.tracks_by_id["1"].path
        before = source.stat()
        source.write_bytes(b"CCCC")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        report = sync_mod.resync(self.lib, self.target)
        self.assertEqual(report.playlists[0].copied, 1)
        original = self.target / "Music/Artist X/Good One/Good One.flac"
        self.assertEqual(original.read_bytes(), b"CCCC")
        report = sync_mod.resync(self.lib, self.target,
                                 title_overrides={"1": "Changed title"},
                                 artist_overrides={"1": "Changed artist"})
        changed = self.target / "Music/Changed artist/Changed title/Changed title.flac"
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(changed.read_bytes(), b"CCCC")
        self.assertFalse(original.exists())
        self.assertEqual(source.read_bytes(), b"CCCC")
        self.assertIn("Changed title.flac", (self.target / "Playlists/SETS/My Set.m3u8").read_text())

    def test_one_changed_overlap_member_does_not_reacquire_retained_audio(self):
        first = Playlist("First", [], ["1", "2"], pid="first")
        second = Playlist("Second", [], ["1"], pid="second")
        self.lib.playlists = [first, second]
        sync_mod.run(self.lib, [first, second], self.target, layout="tree", dry_run=False)
        changed = self.lib.tracks_by_id["1"].path
        before = changed.stat()
        changed.write_bytes(b"EDIT")
        os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        retained = self.lib.tracks_by_id["2"].path
        retained_device = self.target / "Music/Artist Y/Good Two/Good Two.mp3"
        forbidden = {retained.resolve(), retained_device.resolve()}
        hash_file = sync_mod._sha256
        def checked_hash(path):
            self.assertNotIn(path.resolve(), forbidden)
            return hash_file(path)
        with mock.patch.object(sync_mod, "_sha256", side_effect=checked_hash), \
                mock.patch("soundbox.source_acquisition.record_metric") as metric:
            report = sync_mod.resync(self.lib, self.target)
        self.assertFalse(any(row.errors for row in report.playlists))
        self.assertEqual(sum(c.args[1] for c in metric.call_args_list
                             if c.args[0] == "canonical_bytes_read"), 4)
        self.assertEqual(sum(1 for c in metric.call_args_list if c.args[0] == "acquisitions"), 1)
        self.assertEqual(retained_device.read_bytes(), b"BBBBBB")
        for playlist in (first, second):
            self.assertIn("Good One.flac", (self.target / f"Playlists/{playlist.name}.m3u8").read_text())

    def test_stock_rejected_wavex_exports_to_rockbox_with_original_samples(self):
        import numpy as np
        import soundfile as sf
        from soundbox import stock_ipod
        source = self.src / 'rockbox24.wav'
        sf.write(source, np.arange(1764, dtype=np.int32).reshape(-1, 2) * 256,
                 44_100, format='WAVEX', subtype='PCM_24')
        original = source.read_bytes()
        expected, rate = sf.read(source, dtype='int32')
        self.assertIn('PCM_24', stock_ipod._stock_wav_header_omission(source.resolve()))
        track = Track('wavex', 'Rockbox 24', 'Fixture', source.as_uri())
        playlist = Playlist('Rockbox', [], ['wavex'], pid='rockbox')
        library = Library({'wavex': track}, [playlist], self.xml)
        cache = RockboxMediaCache(self.root / 'media-cache')
        report = sync_mod.run(library, [playlist], self.target, layout='tree',
                              dry_run=False, rockbox_media_cache=cache)
        self.assertFalse(any(row.errors for row in report.playlists))
        exported = self.target / 'Music/Fixture/Rockbox 24/Rockbox 24.wav'
        observed, observed_rate = sf.read(exported, dtype='int32')
        np.testing.assert_array_equal(observed, expected)
        self.assertEqual(observed_rate, rate)
        self.assertEqual(sf.info(exported).subtype, 'PCM_24')
        self.assertEqual(_riff_info_values(exported), ('Rockbox 24', 'Fixture'))
        self.assertIn('Rockbox 24.wav', (self.target / 'Playlists/Rockbox.m3u8').read_text())
        self.assertFalse((self.target / 'iPod_Control').exists())
        repeated = sync_mod.resync(library, self.target, rockbox_media_cache=cache)
        self.assertEqual(repeated.playlists, [])
        self.assertTrue(exported.is_file())
        self.assertEqual(source.read_bytes(), original)

    def test_normalized_rockbox_explicit_sync_has_zero_audio_work_when_unchanged(self):
        source = self.src / "actual.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44_100)
            output.writeframes(b"\1\0\2\0" * 100)
        original = source.read_bytes()
        track = Track("actual", "First title", "Fixture artist", source.as_uri())
        playlist = Playlist("First", [], ["actual"], pid="first")
        library = Library({"actual": track}, [playlist], self.xml)
        media_cache = RockboxMediaCache(self.root / "media-cache")
        sync_mod.run(library, [playlist], self.target, layout="tree", dry_run=False,
                     rockbox_media_cache=media_cache)
        old_path = self.target / "Music/Fixture artist/First title/First title.wav"
        self.assertEqual(_riff_info_values(old_path), ("First title", "Fixture artist"))
        with mock.patch("soundbox.source_acquisition.record_metric") as acquisition_metric, \
                mock.patch("soundbox.rockbox_media.record_metric") as transform_metric, \
                mock.patch.object(sync_mod, "record_metric") as device_metric:
            report = sync_mod.resync(library, self.target, rockbox_media_cache=media_cache,
                                     title_overrides={"actual": "Second title"})
        self.assertFalse(any(row.errors for row in report.playlists))
        new_path = self.target / "Music/Fixture artist/Second title/Second title.wav"
        self.assertEqual(_riff_info_values(new_path), ("Second title", "Fixture artist"))
        self.assertFalse(old_path.exists())
        self.assertEqual(sum(c.args[1] for c in acquisition_metric.call_args_list
                             if c.args[0] == "canonical_bytes_read"), len(original))
        self.assertEqual(sum(1 for c in transform_metric.call_args_list
                             if c.args[0] == "transformations"), 1)
        self.assertEqual(sum(c.args[1] for c in device_metric.call_args_list
                             if c.args[0] == "device_bytes_written"), new_path.stat().st_size)
        self.assertFalse(list((self.root / "media-cache").rglob("*.wav")))
        observed = []
        stat = Path.stat
        def source_stat(path, *args, **kwargs):
            if path == source:
                observed.append(path)
            return stat(path, *args, **kwargs)
        with mock.patch.object(media_cache, "prepare", side_effect=AssertionError("no transform")), \
                mock.patch.object(sync_mod, "_sha256", side_effect=AssertionError("no payload reads")), \
                mock.patch.object(sync_mod.DeviceSession, "atomic_copy", side_effect=AssertionError("no copies")), \
                mock.patch.object(manifest, "save", side_effect=AssertionError("no writes")), \
                mock.patch.object(Path, "stat", source_stat):
            report = sync_mod.resync(library, self.target, rockbox_media_cache=media_cache,
                                     title_overrides={"actual": "Second title"})
        self.assertEqual(len(observed), 1)
        self.assertEqual(report.bytes_to_copy, 0)
        self.assertEqual(report.playlists, [])
        self.assertEqual(source.read_bytes(), original)

    def test_preexisting_referenced_media_is_never_overwritten(self):
        destination = self.target / "Music/Artist X/Good One/Good One.flac"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"AAAA")
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        row = manifest.load(self.target)["files"]["/Music/Artist X/Good One/Good One.flac"]
        self.assertFalse(row["owned"])
        destination.write_bytes(b"user edited content")
        report = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertEqual(destination.read_bytes(), b"user edited content")
        self.assertEqual(report.playlists[0].conflicts, 1)
        self.assertTrue(report.playlists[0].errors)

    def test_sync_copies_and_writes_playlists(self):
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, layout="both", dry_run=False)
        # Files copied under Music/<Artist>/
        self.assertTrue((self.target / "Music/Artist X/Good One/Good One.flac").is_file())
        self.assertTrue((self.target / "Music/Artist Y/Good Two/Good Two.mp3").is_file())
        # Both tree + flat playlists written
        tree = self.target / "Playlists/SETS/My Set.m3u8"
        flat = self.target / "Playlists/SETS - My Set.m3u8"
        self.assertTrue(tree.is_file() and flat.is_file())
        text = tree.read_text(encoding="utf-8")
        # Exact order preserved; missing track is a comment.
        self.assertEqual(
            text,
            "#EXTM3U\n/Music/Artist X/Good One/Good One.flac\n/Music/Artist Y/Good Two/Good Two.mp3\n# MISSING: Lost Artist - Gone\n",
        )
        pr = rep.playlists[0]
        self.assertEqual(pr.copied, 2)

    def test_second_sync_is_idempotent(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        pr = rep.playlists[0]
        self.assertEqual(pr.copied, 0)
        self.assertEqual(pr.already_present, 2)

    def test_manifest_owned_corruption_is_repaired(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        # Manifest ownership permits an atomic repair, including same-size
        # corruption that a size-only sync would miss.
        victim = self.target / "Music/Artist X/Good One/Good One.flac"
        victim.write_bytes(b"ZZZZ")
        self.assertTrue(sync_mod.verify_managed(self.target, deep=True))
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertEqual(victim.read_bytes(), b"AAAA")
        self.assertEqual(rep.playlists[0].conflicts, 0)
        self.assertEqual(sync_mod.verify_managed(self.target, deep=True), [])

    def test_unknown_preexisting_music_is_never_overwritten_or_claimed(self):
        victim = self.target / "Music/Artist X/Good One/Good One.flac"
        victim.parent.mkdir(parents=True)
        victim.write_bytes(b"USER-CONTENT")
        rep = sync_mod.run(
            self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertEqual(victim.read_bytes(), b"USER-CONTENT")
        self.assertEqual(rep.playlists[0].conflicts, 1)

    def test_clean_applefiles_scoped(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        # Simulate macOS sidecars in our folders + a sibling folder to protect.
        (self.target / "Music/Artist X/._x.flac").write_bytes(b"junk")
        (self.target / "Playlists/._SETS").write_bytes(b"junk")
        sibling = self.target / "PIONEER"
        sibling.mkdir()
        (sibling / "._keep").write_bytes(b"keep")
        removed = sync_mod.clean_applefiles(self.target)
        self.assertEqual(removed, 2)
        # Sibling AppleDouble must be left alone.
        self.assertTrue((sibling / "._keep").exists())

    def test_free_space_precheck_refuses_and_writes_nothing(self):
        # Simulate a nearly-full device (2 bytes free) — the 10 bytes of new audio
        # can't fit, so it must refuse BEFORE writing anything.
        Fake = type("S", (), {"f_bavail": 2, "f_frsize": 1})
        with mock.patch.object(sync_mod.os, "statvfs", return_value=Fake()):
            with self.assertRaises(sync_mod.NotEnoughSpace):
                sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertFalse((self.target / "Music").exists())  # nothing written

    def test_free_space_precheck_allows_when_it_fits(self):
        # Plenty free — the sum uses plan() sizes (no device I/O) and passes.
        Fake = type("S", (), {"f_bavail": 10**9, "f_frsize": 1})
        with mock.patch.object(sync_mod.os, "statvfs", return_value=Fake()):
            sync_mod.run(
                self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertTrue((self.target / "Music").exists())

    def test_scoped_clean_only_touches_written_dirs(self):
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        self.assertTrue(rep.written_dirs)  # run() records the folders it wrote
        written = next(d for d in rep.written_dirs if d.name not in ("Music", "Playlists"))
        (written / "._junk").write_bytes(b"x")           # junk in a dir we wrote
        untouched = self.target / "Music" / "Nobody"; untouched.mkdir(parents=True)
        (untouched / "._keep").write_bytes(b"y")          # junk in a dir we did NOT
        sync_mod.clean_applefiles(self.target, rep.written_dirs)
        self.assertFalse((written / "._junk").exists())   # scrubbed
        self.assertTrue((untouched / "._keep").exists())  # left alone (not in the set)

    def test_no_partial_temps_after_copy_and_they_get_swept(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        # A completed copy leaves NO .sbpart temp behind (atomic rename into place).
        self.assertEqual(list((self.target / "Music").rglob("*.sbpart")), [])
        # An orphan temp from an interrupted transfer is swept by clean_applefiles.
        artist = next((self.target / "Music").iterdir())
        (artist / "orphan.wav.sbpart").write_bytes(b"partial")
        self.assertEqual(sync_mod.clean_applefiles(self.target), 1)

    def test_cover_art_written_per_track(self):
        # Fake art cache: returns a cover image for track id "1" only.
        art_img = self.root / "art.jpg"
        art_img.write_bytes(b"\xff\xd8\xff\xe0JPEGDATA")

        class FakeCache:
            def image_path(self, tid):
                return art_img if tid == "1" else None

        sync_mod.run(self.lib, self.lib.playlists, self.target,
                     dry_run=False, art_cache=FakeCache())
        cover = self.target / "Music/Artist X/Good One/cover.jpg"
        self.assertTrue(cover.is_file())
        self.assertEqual(cover.read_bytes(), b"\xff\xd8\xff\xe0JPEGDATA")
        # track "2" had no art → no cover
        self.assertFalse((self.target / "Music/Artist Y/Good Two/cover.jpg").exists())

    def test_rockbox_derivatives_strip_embedded_art_and_standardize_cover(self):
        from mutagen.id3 import APIC, ID3
        from mutagen.mp3 import MP3
        from PIL import Image

        source = self.src / "tagged.mp3"
        frame = b"\xff\xfb\x90\x64" + b"\0" * 413
        source.write_bytes(frame * 100)
        tags = ID3()
        tags.add(APIC(
            mime="image/jpeg", type=3, desc="", data=b"huge-cover" * 1000))
        tags.save(source)
        source_before = source.read_bytes()
        xml = self.src / "tagged.xml"
        write_xml(
            xml, [("tagged", "Tagged", "Artist", source)], ["tagged"])
        library = rekordbox_xml.load(xml)

        canonical_art = self.root / "canonical.jpg"
        buf = io.BytesIO()
        Image.new("L", (100, 100), 128).save(
            buf, "JPEG", progressive=False)
        canonical_art.write_bytes(buf.getvalue())

        class FakeArtCache:
            def image_path(self, track_id):
                return canonical_art if track_id == "tagged" else None

        report = sync_mod.run(
            library,
            library.playlists,
            self.target,
            dry_run=False,
            art_cache=FakeArtCache(),
            rockbox_media_cache=RockboxMediaCache(self.root / "media-cache"),
            rockbox_art_cache=RockboxArtworkCache(self.root / "art-cache"),
        )

        landed = self.target / "Music/Artist/Tagged/Tagged.mp3"
        self.assertEqual(report.sanitized_media, 1)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(len(MP3(source).tags.getall("APIC")), 1)
        self.assertEqual(len(MP3(landed).tags.getall("APIC")), 0)
        with Image.open(landed.parent / "cover.160x160.bmp") as image:
            self.assertEqual(image.format, "BMP")
            self.assertEqual(image.size, (160, 160))
            self.assertEqual(image.mode, "P")
        with Image.open(landed.parent / "cover.116x116.bmp") as image:
            self.assertEqual(image.format, "BMP")
            self.assertEqual(image.size, (116, 116))
            self.assertEqual(image.mode, "P")
        with Image.open(landed.parent / "cover.bmp") as image:
            self.assertEqual(image.format, "BMP")
            self.assertEqual(image.size, (160, 160))
            self.assertEqual(image.mode, "P")

        # A second export reuses both exact derivatives and the universal one.
        again = sync_mod.run(
            library,
            library.playlists,
            self.target,
            dry_run=False,
            art_cache=FakeArtCache(),
            rockbox_media_cache=RockboxMediaCache(self.root / "media-cache"),
            rockbox_art_cache=RockboxArtworkCache(self.root / "art-cache"),
        )
        self.assertEqual(again.playlists[0].copied, 0)
        self.assertEqual(again.playlists[0].already_present, 1)

    def test_guard_refuses_target_inside_library(self):
        bad_target = self.src / "inside"
        with self.assertRaises(RuntimeError):
            sync_mod.run(self.lib, self.lib.playlists, bad_target, dry_run=True)

    def test_device_session_rejects_remount_and_symlink_escape(self):
        self.target.mkdir()
        session = sync_mod.DeviceSession(
            self.target, [self.src / "good1.flac"])
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            session.guard(self.target / "escape" / "track.mp3")

        moved = self.root / "old-device"
        self.target.rename(moved)
        self.target.mkdir()
        with self.assertRaises(RuntimeError):
            session.revalidate()

    def test_device_session_refuses_rekordbox_cdj_tree(self):
        self.target.mkdir()
        session = sync_mod.DeviceSession(
            self.target, [self.src / "good1.flac"])

        with self.assertRaisesRegex(RuntimeError, "reserved for rekordbox/CDJ"):
            session.guard(self.target / "PIONEER" / "rekordbox" / "export.pdb")
        with self.assertRaisesRegex(RuntimeError, "reserved for rekordbox/CDJ"):
            session.atomic_write(
                self.target / "pioneer" / "USBANLZ" / "track.DAT", b"no")
        (self.target / "PIONEER").mkdir()
        (self.target / "cdj-data").symlink_to(
            self.target / "PIONEER", target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "reserved for rekordbox/CDJ"):
            session.guard(self.target / "cdj-data" / "history.DAT")
        self.assertEqual(
            session.guard(self.target / "Music" / "PIONEER" / "song.wav"),
            self.target / "Music" / "PIONEER" / "song.wav",
        )

    def test_browser_delete_uses_confirmed_session_not_same_name_remount(self):
        self.target.mkdir()
        victim = self.target / "Music" / "victim.mp3"
        victim.parent.mkdir()
        victim.write_bytes(b"confirmed-volume")
        session = sync_mod.DeviceSession(self.target, [], create=False)

        old_target = self.root / "old-device"
        self.target.rename(old_target)
        replacement = self.target / "Music" / "victim.mp3"
        replacement.parent.mkdir(parents=True)
        replacement.write_bytes(b"replacement-volume")

        with self.assertRaisesRegex(RuntimeError, "remounted"):
            sync_mod.delete_device_paths(
                session, [Path("Music/victim.mp3")])

        self.assertEqual(replacement.read_bytes(), b"replacement-volume")
        self.assertEqual(
            (old_target / "Music/victim.mp3").read_bytes(),
            b"confirmed-volume",
        )

    def test_browser_delete_deduplicates_selected_parent_and_child(self):
        self.target.mkdir()
        folder = self.target / "Music" / "Album"
        folder.mkdir(parents=True)
        (folder / "one.mp3").write_bytes(b"one")
        (folder / "two.mp3").write_bytes(b"two")
        session = sync_mod.DeviceSession(self.target, [], create=False)
        progress = []

        result = sync_mod.delete_device_paths(
            session,
            [Path("Music/Album/one.mp3"), Path("Music/Album")],
            on_progress=lambda done, total, label: progress.append(
                (done, total, label)),
        )

        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.total, 1)
        self.assertFalse(result.errors)
        self.assertFalse(result.cancelled)
        self.assertFalse(folder.exists())
        self.assertEqual(progress, [(1, 1, "Album")])

    def test_browser_delete_cancel_reports_incomplete_without_writes(self):
        self.target.mkdir()
        victim = self.target / "Music" / "victim.mp3"
        victim.parent.mkdir()
        victim.write_bytes(b"keep")
        session = sync_mod.DeviceSession(self.target, [], create=False)

        result = sync_mod.delete_device_paths(
            session,
            [Path("Music/victim.mp3")],
            should_cancel=lambda: True,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(result.deleted, 0)
        self.assertEqual(result.total, 1)
        self.assertTrue(victim.is_file())

    def test_explicit_whole_device_erase_removes_manifest_and_unknown_content(self):
        self.target.mkdir()
        (self.target / "Music").mkdir()
        (self.target / "Music" / "track.flac").write_bytes(b"audio")
        (self.target / "unknown-folder").mkdir()
        (self.target / "unknown-folder" / "notes.txt").write_text("mine")
        (self.target / "PIONEER").mkdir()
        (self.target / "PIONEER" / "rekordbox-export.pdb").write_text("rb")
        manifest.record_export(self.target, [], managed_files={
            "/Music/track.flac": {
                "size": 5, "sha256": "unused", "playlists": [],
            },
        })

        removed = sync_mod.erase_device_contents(
            self.target, [self.src / "good1.flac"])

        self.assertGreaterEqual(removed, 4)
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(sync_mod.verify_managed(self.target), [])

    def test_partial_whole_device_erase_preserves_manifest(self):
        from unittest import mock

        self.target.mkdir()
        blocked = self.target / "blocked"
        blocked.mkdir()
        (blocked / "keep.txt").write_text("keep")
        manifest.record_export(self.target, [], managed_files={
            "/blocked/keep.txt": {
                "size": 4, "sha256": "unused", "playlists": [],
            },
        })
        real_rmtree = sync_mod.shutil.rmtree
        blocked_resolved = blocked.resolve()

        def refuse(path, *args, **kwargs):
            if Path(path).resolve() == blocked_resolved:
                raise OSError("injected erase failure")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(sync_mod.shutil, "rmtree", side_effect=refuse):
            with self.assertRaisesRegex(RuntimeError, "partially erased"):
                sync_mod.erase_device_contents(
                    self.target, [self.src / "good1.flac"])

        self.assertTrue(manifest.manifest_path(self.target).is_file())
        self.assertTrue(blocked.is_dir())

    def test_fat_case_and_unicode_collisions_are_disambiguated(self):
        files: dict[str, sync_mod.PlannedFile] = {}
        root = self.target / "Music"
        first = sync_mod._assign_device_path(
            self.src / "good1.flac", "Artist", files, root, "Café")
        files[first] = sync_mod.PlannedFile(
            self.src / "good1.flac", first, self.target / first.lstrip("/"), 4)
        other = self.src / "other.flac"
        other.write_bytes(b"other")
        second = sync_mod._assign_device_path(
            other, "artist", files, root, "CAFE\u0301")
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            sync_mod._destination_key(first),
            sync_mod._destination_key(second))

    def test_plan_destination_index_scales_with_unique_sources_and_keeps_collisions(self):
        tracks = {}
        for index in range(100):
            source = self.src / f"collision-{index}.flac"
            source.write_bytes(str(index).encode())
            track_id = str(index)
            tracks[track_id] = Track(
                track_id, "Café" if index % 2 else "CAFE\u0301",
                "Artist" if index % 2 else "artist", source.as_uri())
        keys = list(tracks)
        playlists = [Playlist("First", [], keys), Playlist("Repeated", [], keys[::-1])]
        library = Library(tracks, playlists, self.xml)

        with mock.patch.object(sync_mod, "_destination_key",
                               wraps=sync_mod._destination_key) as normalize:
            files, entries = sync_mod.plan(
                library, playlists, self.target, derive_artists=False)
            self.assertLessEqual(normalize.call_count, 5 * len(tracks))

        self.assertEqual(len(files), len(tracks))
        self.assertEqual(len({sync_mod._destination_key(path) for path in files}), len(tracks))
        self.assertEqual([entry.device_path for entry in entries[0]],
                         [entry.device_path for entry in entries[1]][::-1])
        # Compare with the standalone allocator's existing complete-baseline
        # behavior, including deterministic suffixes for equivalent FAT names.
        expected = {}
        for planned in files.values():
            track = tracks[planned.track_id]
            title = sync_mod.metadata.effective_title(
                track.track_id, track.name, track.artist, None, library.title_junk)
            path = sync_mod._assign_device_path(
                track.path, track.artist, expected, self.target / "Music", title)
            expected[path] = planned
        self.assertEqual(list(files), list(expected))


if __name__ == "__main__":
    unittest.main()
