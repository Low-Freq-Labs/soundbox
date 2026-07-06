"""Imported (non-rekordbox) tracks: soundbox.imports + the untracked exemption."""

import tempfile
import unittest
import wave
from pathlib import Path

from soundbox import imports
from soundbox.rekordbox_xml import Library, Playlist, Track


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
                       playlists=[], source_xml="synthetic")

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


if __name__ == "__main__":
    unittest.main()
