import tempfile
import unittest
from pathlib import Path

from soundbox import manifest
from soundbox.library import Library, Playlist


def PL(pid, name, folder, keys):
    return Playlist(name=name, folder_path=folder, keys=list(keys), pid=pid)


def lib_of(*pls):
    return Library(tracks_by_id={}, playlists=list(pls), source=Path("x"))


class TestManifest(unittest.TestCase):
    def test_sig_changes_with_tracks(self):
        a = PL("1", "Set", [], ["t1", "t2", "t3"])
        b = PL("1", "Set", [], ["t1", "t3", "t2"])  # reordered
        self.assertNotEqual(manifest.playlist_sig(a), manifest.playlist_sig(b))
        self.assertEqual(manifest.playlist_sig(a), manifest.playlist_sig(PL("1", "Set", [], ["t1", "t2", "t3"])))

    def test_key_prefers_pid_then_custom(self):
        self.assertEqual(manifest.playlist_key(PL("42", "X", [], [])), "42")
        self.assertEqual(manifest.playlist_key(PL("", "MyMix", [], [])), "cust:MyMix")

    def test_record_and_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        manifest.record_export(d, [PL("1", "Set A", ["crates"], ["t1", "t2"])])
        m = manifest.load(d)
        self.assertIn("1", m["playlists"])
        self.assertEqual(m["playlists"]["1"]["name"], "Set A")
        # A second export accumulates, doesn't clobber.
        manifest.record_export(d, [PL("2", "Set B", [], ["t9"])])
        m = manifest.load(d)
        self.assertEqual(set(m["playlists"]), {"1", "2"})

    def test_diff_detects_rename_move_change_remove(self):
        d = Path(tempfile.mkdtemp())
        manifest.record_export(d, [
            PL("1", "SONNY.AIF", [], ["a", "b"]),   # will be renamed + moved
            PL("2", "Bass", ["crates"], ["x"]),      # will change tracks
            PL("3", "Old", [], ["z"]),               # will be deleted in rekordbox
            PL("4", "Stable", [], ["k"]),            # unchanged
        ])
        m = manifest.load(d)
        # Current library: 1 renamed+moved, 2 track added, 3 gone, 4 same.
        lib = lib_of(
            PL("1", "SONNY.AIF v7", ["sets"], ["a", "b"]),
            PL("2", "Bass", ["crates"], ["x", "y"]),
            PL("4", "Stable", [], ["k"]),
        )
        dr = manifest.diff(m, lib)
        self.assertEqual([k for k, *_ in dr.renamed], ["1"])
        self.assertEqual([k for k, *_ in dr.moved], ["1"])
        self.assertEqual([k for k, *_ in dr.changed], ["2"])
        self.assertEqual([k for k, *_ in dr.removed], ["3"])
        self.assertTrue(dr.any())
        self.assertEqual(dr.count(), 3)  # pl 1 counted once despite rename+move

    def test_diff_clean_when_nothing_changed(self):
        d = Path(tempfile.mkdtemp())
        pls = [PL("1", "A", [], ["a"]), PL("2", "B", ["f"], ["b", "c"])]
        manifest.record_export(d, pls)
        dr = manifest.diff(manifest.load(d), lib_of(*pls))
        self.assertFalse(dr.any())


if __name__ == "__main__":
    unittest.main()
