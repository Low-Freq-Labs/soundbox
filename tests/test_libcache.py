"""Library snapshot cache: faithful round-trip, corruption-tolerant load."""

import tempfile
import unittest
from pathlib import Path

from soundbox import libcache
from soundbox.rekordbox_xml import Library, Playlist, Track


def _lib() -> Library:
    tracks = {
        "1": Track("1", "Bravo", "Zed", "file://localhost/tmp/b.wav",
                   bpm="140.00", key="10A", total_time=185, date_added="2026-01-02",
                   genre="Riddim", size=123, bitrate="1411"),
        "sb-x": Track("sb-x", "Liveset", "", "file://localhost/tmp/set.m4a",
                      total_time=5400, imported=True),
    }
    pls = [Playlist("SET ONE", ["CRATES"], keys=["1", "sb-x"], pid="99")]
    return Library(tracks_by_id=tracks, playlists=pls, source_xml="/x/lib.xml")


class RoundTrip(unittest.TestCase):
    def test_dump_load_faithful(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "libcache.json"
            libcache.dump(_lib(), p)
            got = libcache.load(p)
            self.assertIsNotNone(got)
            self.assertEqual(set(got.tracks_by_id), {"1", "sb-x"})
            t = got.tracks_by_id["1"]
            self.assertEqual((t.name, t.artist, t.bpm, t.genre), ("Bravo", "Zed", "140.00", "Riddim"))
            self.assertTrue(got.tracks_by_id["sb-x"].imported)  # imported flag survives
            self.assertEqual(got.playlists[0].keys, ["1", "sb-x"])
            self.assertEqual(got.playlists[0].pid, "99")

    def test_path_property_reconstructs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            libcache.dump(_lib(), p)
            got = libcache.load(p)
            self.assertEqual(got.tracks_by_id["sb-x"].path.name, "set.m4a")


class Tolerant(unittest.TestCase):
    def test_missing_file_is_none(self):
        self.assertIsNone(libcache.load("/no/such/libcache.json"))

    def test_corrupt_json_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"; p.write_text("{not json")
            self.assertIsNone(libcache.load(p))

    def test_wrong_schema_is_none(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"; p.write_text(json.dumps({"schema": 999, "tracks": []}))
            self.assertIsNone(libcache.load(p))

    def test_empty_library_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            libcache.dump(Library(tracks_by_id={}, playlists=[], source_xml="x"), p)
            self.assertIsNone(libcache.load(p))  # nothing to paint → treat as no cache


if __name__ == "__main__":
    unittest.main()
