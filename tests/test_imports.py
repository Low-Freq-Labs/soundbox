"""Imported (non-rekordbox) tracks: soundbox.imports + the untracked exemption."""

import tempfile
import hashlib
import os
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    def test_alac_aac_uses_container_metadata_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "set.m4a"
            path.write_bytes(b"container fixture")
            audio = SimpleNamespace(info=SimpleNamespace(length=6839.75))
            with (
                mock.patch("soundfile.info", side_effect=RuntimeError("unsupported")),
                mock.patch("mutagen.File", return_value=audio),
            ):
                rec = imports.probe(path)
        self.assertEqual(rec["time"], 6839)
        self.assertEqual(rec["size"], len(b"container fixture"))


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
        self.assertEqual(t.duration_str, "1:30:00")
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

    def test_backfills_existing_zero_time_record_before_injection(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "existing.m4a"
            path.write_bytes(b"old imported record")
            imported = {"sb-a": {"path": str(path), "time": 0, "size": 0}}
            audio = SimpleNamespace(info=SimpleNamespace(length=3594.9))
            with (
                mock.patch("soundfile.info", side_effect=RuntimeError("unsupported")),
                mock.patch("mutagen.File", return_value=audio),
            ):
                changed = imports.inject(self._lib(), imported)
            self.assertEqual(changed, 1)
            self.assertEqual(imported["sb-a"]["time"], 3594)
            self.assertEqual(imported["sb-a"]["size"], len(b"old imported record"))



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
    def test_equal_sized_collision_and_occupied_fallback_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'device' / 'set.mp3'
            source.parent.mkdir()
            source.write_bytes(b'new!')
            artist = root / 'media' / 'Unknown Artist'
            artist.mkdir(parents=True)
            original = artist / source.name
            suffix = hashlib.sha1(os.fsencode(source.resolve())).hexdigest()[:6]
            fallback = artist / f'set_{suffix}.mp3'
            original.write_bytes(b'old!')
            fallback.write_bytes(b'keep')
            with mock.patch('soundbox.source_acquisition.record_metric') as metrics:
                result = imports.copy_from_device([source], artist.parent)
            canonical_bytes = sum(call.args[1] for call in metrics.call_args_list
                                  if call.args[0] == 'canonical_bytes_read')
            self.assertEqual(canonical_bytes, source.stat().st_size)
            self.assertEqual(result, [artist / f'set_{suffix}-2.mp3'])
            self.assertEqual(original.read_bytes(), b'old!')
            self.assertEqual(fallback.read_bytes(), b'keep')
            self.assertEqual(result[0].read_bytes(), b'new!')
            self.assertEqual(source.read_bytes(), b'new!')

    def test_exact_existing_payload_is_reused_without_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'set.mp3'
            source.write_bytes(b'exact bytes')
            media = root / 'media'
            first = imports.copy_from_device([source, source], media)
            before = first[0].stat()
            with mock.patch('soundbox.mac_trash.exclusive_move', side_effect=AssertionError('repeat wrote')):
                second = imports.copy_from_device([source], media)
            self.assertEqual(second, first)
            self.assertEqual(first[0].stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(first[0].read_bytes(), source.read_bytes())

    def test_exclusive_name_race_does_not_overwrite_occupant(self):
        from soundbox.mac_trash import exclusive_move
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'set.mp3'
            source.write_bytes(b'new!')
            raced = []
            def publish(stage, target):
                if not raced:
                    target.write_bytes(b'keep')
                    raced.append(target)
                exclusive_move(stage, target)
            with mock.patch('soundbox.mac_trash.exclusive_move', side_effect=publish):
                result = imports.copy_from_device([source], root / 'media')
            self.assertEqual(raced[0].read_bytes(), b'keep')
            self.assertNotEqual(result, raced)
            self.assertEqual(result[0].read_bytes(), b'new!')
            self.assertEqual(source.read_bytes(), b'new!')

    def test_symlink_occupant_is_preserved_and_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'set.mp3'
            source.write_bytes(b'same')
            outside = root / 'outside.mp3'
            outside.write_bytes(b'same')
            artist = root / 'media' / 'Unknown Artist'
            artist.mkdir(parents=True)
            link = artist / source.name
            link.symlink_to(outside)
            result = imports.copy_from_device([source], artist.parent)
            self.assertTrue(link.is_symlink())
            self.assertNotEqual(result, [link])
            self.assertEqual(outside.read_bytes(), b'same')
            self.assertEqual(result[0].read_bytes(), b'same')

    def test_cancel_and_publication_failure_leave_no_partial_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'set.mp3'
            source.write_bytes(b'original' * 200000)
            media = root / 'media'
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                imports.copy_from_device([source], media, should_cancel=lambda: True)
            self.assertFalse(media.exists())
            with mock.patch('soundbox.mac_trash.exclusive_move', side_effect=OSError('publish refused')):
                with self.assertRaisesRegex(OSError, 'publish refused'):
                    imports.copy_from_device([source], media)
            self.assertEqual(list((media / 'Unknown Artist').iterdir()), [])
            self.assertEqual(source.read_bytes(), b'original' * 200000)

    def test_long_unicode_collision_keeps_valid_bounded_filename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / (('漢' * 82) + '.mp3')
            source.write_bytes(b'new')
            artist = root / 'media' / 'Unknown Artist'
            artist.mkdir(parents=True)
            (artist / source.name).write_bytes(b'old')
            result = imports.copy_from_device([source], artist.parent)
            self.assertLessEqual(len(os.fsencode(result[0].name)), 255)
            self.assertEqual(result[0].read_bytes(), b'new')
            self.assertEqual((artist / source.name).read_bytes(), b'old')

    def test_cancellation_during_source_capture_removes_only_its_staging(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'set.mp3'
            source.write_bytes(b'a' * (3 * 1024 * 1024))
            media = root / 'media'
            def cancel_after_chunk():
                return any(path.stat().st_size for path in media.rglob('set.mp3'))
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                imports.copy_from_device([source], media, should_cancel=cancel_after_chunk)
            self.assertEqual(list((media / 'Unknown Artist').iterdir()), [])
            self.assertEqual(source.read_bytes(), b'a' * (3 * 1024 * 1024))

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

    def test_playlist_parent_escape_is_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            device = root / "DEVICE"
            media = root / "SoundBox Media"
            playlist = device / "Playlists" / "host.m3u8"
            playlist.parent.mkdir(parents=True)
            outside = root / "private.mp3"
            outside.write_bytes(b"private")
            playlist.write_text("../../private.mp3\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "outside.*device"):
                imports.copy_from_device([playlist], media)

            self.assertFalse(media.exists())

    def test_playlist_symlink_escape_is_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            device = root / "DEVICE"
            media = root / "SoundBox Media"
            playlist = device / "Playlists" / "host.m3u8"
            playlist.parent.mkdir(parents=True)
            outside = root / "private"
            outside.mkdir()
            (outside / "private.mp3").write_bytes(b"private")
            (device / "Music").symlink_to(outside, target_is_directory=True)
            playlist.write_text("/Music/private.mp3\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "outside.*device"):
                imports.copy_from_device([playlist], media)

            self.assertFalse(media.exists())

if __name__ == "__main__":
    unittest.main()
