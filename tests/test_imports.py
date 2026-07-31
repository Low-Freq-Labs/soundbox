"""Imported (non-rekordbox) tracks: soundbox.imports + the untracked exemption."""

import tempfile
import unittest
import wave
from pathlib import Path

from soundbox import imports
from soundbox.library import Library, Playlist, Track


def _write_wav(path: Path, seconds: float = 2.0, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class Ids(unittest.TestCase):
    def test_prefixed_and_unique(self):
        ids = {imports.new_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)
        for i in ids:
            self.assertTrue(i.startswith("sb-"))  # never collides with rekordbox

    def test_is_audio(self):
        self.assertTrue(imports.is_audio(Path("/x/Liveset (Rezz) 2026.MP3")))
        self.assertTrue(imports.is_audio(Path("/x/set.m4a")))
        self.assertFalse(imports.is_audio(Path("/x/notes.txt")))
        self.assertFalse(imports.is_audio(Path("/x/folder")))


class Probe(unittest.TestCase):
    def test_reads_duration_and_size(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "set.wav"
            _write_wav(f, seconds=3.0)
            rec = imports.probe(f)
            self.assertEqual(rec["path"], str(f))
            self.assertEqual(rec["time"], 3)
            self.assertGreater(rec["size"], 0)
            self.assertRegex(rec["added"], r"^\d{4}-\d{2}-\d{2}$")

    def test_never_raises_on_unreadable(self):
        rec = imports.probe(Path("/nope/does-not-exist.m4a"))
        self.assertEqual(rec["time"], 0)
        self.assertEqual(rec["size"], 0)


class MakeTrack(unittest.TestCase):
    def test_path_round_trips_through_uri(self):
        # Spaces, unicode and parens — liveset filenames are messy.
        p = Path("/tmp/Livesets/Übel & Good (Live @ EDC) [2026].mp3")
        t = imports.make_track("sb-abc", {"path": str(p), "added": "2026-07-06",
                                          "time": 5400, "size": 9})
        self.assertEqual(t.path, p)
        self.assertEqual(t.name, p.stem)  # title = filename stem
        self.assertTrue(t.imported)
        self.assertEqual(t.total_time, 5400)
        self.assertEqual(t.date_added, "2026-07-06")

    def test_never_swept_as_untracked(self):
        # No BPM, no key, even 0:00 — the exact profile hide-untracked hunts.
        t = imports.make_track("sb-x", {"path": "/tmp/set.mp3"})
        self.assertFalse(t.is_untracked(nokey=True, short=True, short_secs=10**6))


class Inject(unittest.TestCase):
    def _lib(self) -> Library:
        # bpm set: a real rekordbox track always has one from analysis.
        return Library(tracks_by_id={"1": Track("1", "A", "B", "file:///t/a.wav",
                                                bpm="140.00")},
                       playlists=[], source="synthetic")

    def test_joins_pool_and_is_idempotent(self):
        lib = self._lib()
        imported = {"sb-a": {"path": "/t/set one.mp3", "added": "2026-07-06"}}
        imports.inject(lib, imported)
        imports.inject(lib, imported)  # refresh re-injection must not dupe
        self.assertEqual(set(lib.tracks_by_id), {"1", "sb-a"})
        self.assertEqual(lib.tracks_by_id["sb-a"].name, "set one")

    def test_resolves_in_playlist_despite_hide_untracked(self):
        lib = self._lib()
        imports.inject(lib, {"sb-a": {"path": "/t/set.mp3"}})
        lib.hide_untracked = True  # would sweep any keyless no-BPM rekordbox row
        pl = Playlist("LIVE", [], keys=["sb-a", "1"])
        self.assertEqual([t.track_id for t in lib.resolve(pl)], ["sb-a", "1"])



class FixLinks(unittest.TestCase):
    """find_moved: relocate imported files the user moved — subfolder moves,
    renames (size rescue), duplicates, and honest refusal when ambiguous."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _rec(self, path: Path, size: int = 0) -> dict:
        return {"path": str(path), "size": size}

    def test_missing_detects_gone_files(self):
        live = self.root / "here.mp3"
        live.write_bytes(b"x")
        imp = {"a": self._rec(live), "b": self._rec(self.root / "gone.mp3")}
        self.assertEqual(set(imports.missing(imp)), {"b"})

    def test_moved_into_subfolder(self):
        new = self.root / "pt.1" / "AHEE Lost Lands 2024.m4a"
        new.parent.mkdir()
        new.write_bytes(b"abc")
        lost = {"a": self._rec(self.root / "AHEE Lost Lands 2024.m4a", 3)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {"a": new})

    def test_case_insensitive_name_match(self):
        new = self.root / "sub" / "SET ONE.MP3"
        new.parent.mkdir()
        new.write_bytes(b"abcd")
        lost = {"a": self._rec(self.root / "Set One.mp3", 4)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {"a": new})

    def test_renamed_file_rescued_by_unique_size(self):
        new = self.root / "renamed completely.m4a"
        new.write_bytes(b"12345678")
        lost = {"a": self._rec(self.root / "original name.m4a", 8)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {"a": new})

    def test_same_name_wrong_size_still_matches_by_name(self):
        # Exact filename is a strong signal even if bytes changed (re-tag etc.)
        new = self.root / "sub" / "set.m4a"
        new.parent.mkdir()
        new.write_bytes(b"different-size")
        lost = {"a": self._rec(self.root / "set.m4a", 3)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {"a": new})

    def test_ambiguous_same_name_no_size_is_skipped(self):
        (self.root / "d1").mkdir(); (self.root / "d2").mkdir()
        (self.root / "d1" / "set.m4a").write_bytes(b"one")
        (self.root / "d2" / "set.m4a").write_bytes(b"twoo")
        lost = {"a": self._rec(self.root / "set.m4a", 0)}  # size unknown
        self.assertEqual(imports.find_moved(lost, [self.root]), {})

    def test_duplicate_copies_pick_shallowest(self):
        (self.root / "deep" / "er").mkdir(parents=True)
        top = self.root / "set.m4a"
        top.write_bytes(b"same")
        (self.root / "deep" / "er" / "set.m4a").write_bytes(b"same")
        lost = {"a": self._rec(self.root / "old" / "set.m4a", 4)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {"a": top})

    def test_hidden_dirs_and_non_audio_ignored(self):
        (self.root / ".Trash").mkdir()
        (self.root / ".Trash" / "set.m4a").write_bytes(b"xx")
        (self.root / "set.txt").write_bytes(b"xx")
        lost = {"a": self._rec(self.root / "set.m4a", 2)}
        self.assertEqual(imports.find_moved(lost, [self.root]), {})

    def test_search_roots_nearest_existing_ancestor(self):
        gone = self.root / "Sets" / "x.m4a"  # Sets/ was renamed away
        keep = self.root / "Other" / "y.m4a"
        keep.parent.mkdir()
        keep.write_bytes(b"x")
        imp = {"a": self._rec(gone), "b": self._rec(keep)}
        roots = imports.search_roots(imp)
        self.assertIn(self.root, roots)          # nearest survivor for x.m4a
        self.assertNotIn(keep.parent, roots)     # contained in root — deduped


class DeviceImport(unittest.TestCase):
    def test_playlist_order_copies_to_soundbox_media_without_touching_device(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            device = root / "DEVICE"
            media = root / "SoundBox Media"
            music = device / "Music"
            music.mkdir(parents=True)
            first = music / "one.mp3"
            second = music / "two.mp3"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            playlist = device / "Playlists" / "set.m3u8"
            playlist.parent.mkdir()
            playlist.write_text(
                "#EXTM3U\n/Music/two.mp3\n/Music/one.mp3\n",
                encoding="utf-8")
            before = {
                path.relative_to(device): path.read_bytes()
                for path in device.rglob("*") if path.is_file()
            }
            copied = imports.copy_from_device([playlist], media)
            self.assertEqual([path.name for path in copied],
                             ["two.mp3", "one.mp3"])
            self.assertTrue(all(
                path.parent == media / "Unknown Artist"
                for path in copied))
            after = {
                path.relative_to(device): path.read_bytes()
                for path in device.rglob("*") if path.is_file()
            }
            self.assertEqual(after, before)

if __name__ == "__main__":
    unittest.main()
