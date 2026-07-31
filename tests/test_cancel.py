"""Cancel semantics — a cancelled export must leave the device USABLE and
RESUMABLE, never a lie:
  • completed playlists (earlier jobs) stay fully complete;
  • the interrupted playlist gets an honest m3u8 of only what's on the device;
  • its manifest sig is poisoned so drift flags it and the next Sync finishes;
  • the finishing Sync restores the full m3u8 + a clean manifest.
"""

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from soundbox import engine, manifest
from soundbox.library import Library, Playlist, Track


class TestCancelSemantics(unittest.TestCase):
    def setUp(self):
        self.dev = Path(tempfile.mkdtemp(prefix="dev_"))
        self.src = Path(tempfile.mkdtemp(prefix="src_"))
        for n in ("a.wav", "B.wav", "c.wav"):
            (self.src / n).write_bytes(b"RIFF" + n.encode())

    def _t(self, tid, fn):
        uri = "file://localhost" + quote(str(self.src / fn))
        return Track(track_id=tid, name=tid.upper(), artist="Artist",
                     location_uri=uri, bpm="140.00", key="2A", total_time=200)

    def _lib(self, *pls):
        tracks = {k: self._t(k, f"{k}.wav") for k in ("a", "b", "c")}
        return Library(tracks_by_id=tracks, playlists=list(pls), source=Path("x"))

    def _m3u8(self, rel):
        return (self.dev / "Playlists" / rel).read_text(encoding="utf-8")

    def test_cancel_mid_copy_is_usable_and_resumable(self):
        pl = Playlist(name="BIG", folder_path=[], keys=["a", "b", "c"], pid="1")
        lib = self._lib(pl)

        # Cancel after the first file has been copied.
        copied = 0

        def on_progress(done, total, label):
            nonlocal copied
            copied = done

        rep = engine.export_playlists(lib, [pl], self.dev,
                                      on_progress=on_progress,
                                      should_cancel=lambda: copied >= 1)
        self.assertTrue(rep.cancelled)

        # m3u8 exists, lists ONLY what's on the device, and says why.
        text = self._m3u8("BIG.m3u8")
        self.assertIn("/Music/Artist/A/A.wav", text)
        self.assertNotIn("/Music/Artist/C/C.wav", text)
        self.assertIn("# CANCELLED: 2 track(s) not exported", text)

        # Manifest records the playlist but with a poisoned sig → drift flags it.
        m = manifest.load(self.dev)
        self.assertEqual(m["playlists"]["1"]["sig"], "partial")
        dr = engine.device_drift(lib, self.dev)
        self.assertEqual([k for k, _ in dr.changed], ["1"])

        # One Sync finishes the job: full m3u8, real sig, no more drift.
        engine.resync_device(lib, self.dev)
        text = self._m3u8("BIG.m3u8")
        for t in ("A", "B", "C"):
            self.assertIn(f"/Music/Artist/{t}/{t}.wav", text)
        self.assertNotIn("CANCELLED", text)
        self.assertEqual(manifest.load(self.dev)["playlists"]["1"]["sig"],
                         manifest.playlist_sig(pl))
        self.assertFalse(engine.device_drift(lib, self.dev).any())

    def test_completed_playlist_before_cancel_stays_complete(self):
        # Job 1 exports fully; job 2 is cancelled immediately (the ExportWorker
        # queue model: one playlist per run). P1 must remain complete + clean.
        p1 = Playlist(name="DONE", folder_path=[], keys=["a"], pid="1")
        p2 = Playlist(name="CUT", folder_path=[], keys=["b", "c"], pid="2")
        lib = self._lib(p1, p2)
        engine.export_playlists(lib, [p1], self.dev)
        rep = engine.export_playlists(lib, [p2], self.dev,
                                      should_cancel=lambda: True)
        self.assertTrue(rep.cancelled)

        self.assertIn("/Music/Artist/A/A.wav", self._m3u8("DONE.m3u8"))
        m = manifest.load(self.dev)
        self.assertEqual(m["playlists"]["1"]["sig"], manifest.playlist_sig(p1))
        self.assertEqual(m["playlists"]["2"]["sig"], "partial")
        # Only the cut playlist drifts; DONE stays clean.
        dr = engine.device_drift(lib, self.dev)
        self.assertEqual([k for k, _ in dr.changed], ["2"])

    def test_cancel_with_everything_already_present_is_clean(self):
        # Cancelling a RE-export (all files already on device) loses nothing:
        # entries all exist → full m3u8, no partial flag.
        pl = Playlist(name="AGAIN", folder_path=[], keys=["a", "b"], pid="1")
        lib = self._lib(pl)
        engine.export_playlists(lib, [pl], self.dev)
        rep = engine.export_playlists(lib, [pl], self.dev,
                                      should_cancel=lambda: True)
        self.assertTrue(rep.cancelled)
        text = self._m3u8("AGAIN.m3u8")
        self.assertIn("/Music/Artist/A/A.wav", text)
        self.assertIn("/Music/Artist/B/B.wav", text)
        self.assertNotIn("CANCELLED", text)
        self.assertEqual(manifest.load(self.dev)["playlists"]["1"]["sig"],
                         manifest.playlist_sig(pl))


if __name__ == "__main__":
    unittest.main()


class TestResyncCancel(unittest.TestCase):
    def setUp(self):
        self.dev = Path(tempfile.mkdtemp(prefix="dev_"))
        self.src = Path(tempfile.mkdtemp(prefix="src_"))
        for n in ("a.wav", "B.wav"):
            (self.src / n).write_bytes(b"RIFF" + n.encode())

    def _t(self, tid, fn):
        uri = "file://localhost" + quote(str(self.src / fn))
        return Track(track_id=tid, name=tid.upper(), artist="Artist",
                     location_uri=uri, bpm="140.00", key="2A", total_time=200)

    def test_cancelled_resync_defers_prune_and_forget(self):
        # Export two playlists, then delete one from rekordbox. A CANCELLED resync
        # must not prune its music or forget it — the next Sync finishes cleanup.
        tracks = {k: self._t(k, f"{k}.wav") for k in ("a", "b")}
        p1 = Playlist(name="KEEP", folder_path=[], keys=["a"], pid="1")
        p2 = Playlist(name="GONE", folder_path=[], keys=["b"], pid="2")
        lib1 = Library(tracks_by_id=tracks, playlists=[p1, p2], source=Path("x"))
        engine.export_playlists(lib1, [p1, p2], self.dev)

        lib2 = Library(tracks_by_id=tracks, playlists=[p1], source=Path("x"))
        rep = engine.resync_device(lib2, self.dev, should_cancel=lambda: True)
        self.assertTrue(rep.cancelled)
        # music NOT pruned, manifest NOT forgotten → still resumable
        self.assertTrue((self.dev / "Music/Artist/B").is_dir())
        self.assertIn("2", manifest.load(self.dev)["playlists"])

        # The next (uncancelled) Sync completes the cleanup.
        engine.resync_device(lib2, self.dev)
        self.assertFalse((self.dev / "Music/Artist/B").exists())
        self.assertNotIn("2", manifest.load(self.dev)["playlists"])
        self.assertTrue((self.dev / "Music/Artist/A").is_dir())  # shared-safe keep
