import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from soundbox import rekordbox_xml, sync as sync_mod


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

    def test_conflict_not_overwritten(self):
        sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        # Tamper with a target file -> different content, same name.
        victim = self.target / "Music/Artist X/Good One/Good One.flac"
        victim.write_bytes(b"DIFFERENT-CONTENT")
        rep = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
        # Must NOT overwrite; content preserved; reported as conflict.
        self.assertEqual(victim.read_bytes(), b"DIFFERENT-CONTENT")
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
            rep = sync_mod.run(self.lib, self.lib.playlists, self.target, dry_run=False)
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

    def test_guard_refuses_target_inside_library(self):
        bad_target = self.src / "inside"
        with self.assertRaises(RuntimeError):
            sync_mod.run(self.lib, self.lib.playlists, bad_target, dry_run=True)


if __name__ == "__main__":
    unittest.main()
