import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from soundbox import manifest, rekordbox_xml, sync as sync_mod
from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache


def loc(p: Path) -> str:
    return "file://localhost" + quote(str(p))


def write_xml(path: Path, tracks: list[tuple[str, str, str, Path]], playlist: list[str]) -> None:
    """tracks: (id, name, artist, source_path). playlist: list of track ids."""
    tnodes = "".join(
        f'<TRACK TrackID="{tid}" Name="{name}" Artist="{artist}" Location="{loc(src)}"/>'
        for tid, name, artist, src in tracks
    )
    keys = "".join(f'<TRACK Key="{k}"/>' for k in playlist)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="{len(tracks)}">{tnodes}</COLLECTION>'
        '<PLAYLISTS><NODE Type="0" Name="ROOT" Count="1">'
        '<NODE Type="0" Name="SETS" Count="1">'
        f'<NODE Type="1" Name="My Set" KeyType="0" Entries="{len(playlist)}">{keys}</NODE>'
        '</NODE></NODE></PLAYLISTS></DJ_PLAYLISTS>'
    )
    path.write_text(xml, encoding="utf-8")


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
        self.xml = self.root / "rb.xml"
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
        xml = self.root / "tagged.xml"
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


if __name__ == "__main__":
    unittest.main()
