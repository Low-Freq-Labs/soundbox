import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from soundbox import engine, manifest
from soundbox.library import Library, Playlist, Track


class TestResync(unittest.TestCase):
    def setUp(self):
        self.dev = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="dev_")))
        self.src = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="src_")))
        for n in ("A.wav", "B.wav", "c.wav", "Shared.wav"):
            (self.src / n).write_bytes(b"RIFF" + n.encode())

    def _t(self, tid, name, fn):
        uri = "file://localhost" + quote(str(self.src / fn))
        return Track(track_id=tid, name=name, artist="Artist", location_uri=uri,
                     bpm="140.00", key="2A", total_time=200)

    def _lib(self, *pls):
        tracks = {k: self._t(k, k.upper(), f"{k}.wav") for k in ("a", "b", "c")}
        tracks["s"] = self._t("s", "Shared", "Shared.wav")
        return Library(tracks_by_id=tracks, playlists=list(pls), source=Path("x"))

    def _m3u8s(self):
        return sorted(str(p.relative_to(self.dev)) for p in (self.dev / "Playlists").rglob("*.m3u8"))

    def _music(self):
        return sorted(p.name for p in (self.dev / "Music").glob("*/*") if p.is_dir())

    def test_rename_move_add_remove_and_shared_safety(self):
        P1 = Playlist(name="SONNY.AIF", folder_path=[], keys=["a", "s"], pid="1")
        P2 = Playlist(name="Bass", folder_path=["crates"], keys=["b", "s"], pid="2")
        engine.export_playlists(self._lib(P1, P2), [P1, P2], self.dev, dry_run=False)
        self.assertEqual(self._music(), ["A", "B", "Shared"])  # shared deduped

        # rekordbox: P1 renamed+moved, P2 swaps shared->c
        P1b = Playlist(name="SONNY.AIF v7", folder_path=["sets"], keys=["a", "s"], pid="1")
        P2b = Playlist(name="Bass", folder_path=["crates"], keys=["b", "c"], pid="2")
        lib2 = self._lib(P1b, P2b)

        dr = engine.device_drift(lib2, self.dev)
        self.assertEqual([k for k, *_ in dr.renamed], ["1"])
        self.assertEqual([k for k, *_ in dr.moved], ["1"])
        self.assertEqual([k for k, *_ in dr.changed], ["2"])

        engine.resync_device(lib2, self.dev)
        self.assertEqual(self._m3u8s(),
                         ["Playlists/crates/Bass.m3u8", "Playlists/sets/SONNY.AIF v7.m3u8"])
        self.assertEqual(self._music(), ["A", "B", "C", "Shared"])  # shared kept (P1 still uses it)

    def test_only_drifted_playlist_is_reexported(self):
        # Two synced playlists; change ONE track in ONE of them. Resync must touch
        # only the drifted playlist (its report entry present, the other absent).
        P1 = Playlist(name="One", folder_path=[], keys=["a"], pid="1")
        P2 = Playlist(name="Two", folder_path=[], keys=["b"], pid="2")
        engine.export_playlists(self._lib(P1, P2), [P1, P2], self.dev, dry_run=False)

        P2b = Playlist(name="Two", folder_path=[], keys=["b", "c"], pid="2")  # +c
        lib2 = self._lib(P1, P2b)
        rep = engine.resync_device(lib2, self.dev)
        names = [pr.name for pr in rep.playlists]
        self.assertEqual(names, ["Two"])                     # only the drifted one ran
        self.assertEqual(self._music(), ["A", "B", "C"])     # c copied in
        self.assertEqual(self._m3u8s(), ["Playlists/One.m3u8", "Playlists/Two.m3u8"])

    def test_pure_add_does_not_prune(self):
        # Adding a track (no removals anywhere) must NOT prune — _dropped_tracks False.
        from soundbox import manifest, sync
        P1 = Playlist(name="A", folder_path=[], keys=["a"], pid="1")
        engine.export_playlists(self._lib(P1), [P1], self.dev, dry_run=False)
        m = manifest.load(self.dev)
        by_key = {manifest.playlist_key(p): p
                  for p in self._lib(Playlist(name="A", folder_path=[], keys=["a", "b"], pid="1")).playlists}
        drift = manifest.diff(m, self._lib(Playlist(name="A", folder_path=[], keys=["a", "b"], pid="1")))
        self.assertFalse(sync._dropped_tracks(m.get("playlists", {}), by_key, drift.changed))

    def test_custom_playlist_not_deleted_as_removed(self):
        # A custom SoundBox playlist (pid="" -> key "cust:<name>") is NOT in
        # lib.playlists. Without passing it as `extra`, drift would flag it 'removed'
        # and resync would delete it. It must survive (and update) when passed in.
        rb = Playlist(name="RB", folder_path=[], keys=["a"], pid="1")
        cust = Playlist(name="My Mix", folder_path=[], keys=["b", "s"], pid="")
        engine.export_playlists(self._lib(rb), [rb, cust], self.dev, dry_run=False)
        self.assertIn("cust:My Mix", manifest.load(self.dev)["playlists"])

        lib2 = self._lib(rb)  # rekordbox unchanged; custom lives only in the app
        # Without extra: custom looks removed (the bug).
        self.assertEqual([k for k, *_ in engine.device_drift(lib2, self.dev).removed],
                         ["cust:My Mix"])
        # With extra: no drift, nothing removed.
        self.assertFalse(engine.device_drift(lib2, self.dev, [cust]).any())

        engine.resync_device(lib2, self.dev, extra=[cust])
        self.assertEqual(self._m3u8s(), ["Playlists/My Mix.m3u8", "Playlists/RB.m3u8"])
        self.assertEqual(self._music(), ["A", "B", "Shared"])  # custom's tracks kept
        self.assertIn("cust:My Mix", manifest.load(self.dev)["playlists"])  # still tracked

    def test_delete_playlist_prunes_its_orphans_only(self):
        P1 = Playlist(name="A", folder_path=[], keys=["a", "s"], pid="1")
        P2 = Playlist(name="B", folder_path=[], keys=["b", "c"], pid="2")
        engine.export_playlists(self._lib(P1, P2), [P1, P2], self.dev, dry_run=False)

        lib2 = self._lib(P1)  # P2 deleted in rekordbox
        dr = engine.device_drift(lib2, self.dev)
        self.assertEqual([k for k, *_ in dr.removed], ["2"])

        engine.resync_device(lib2, self.dev)
        self.assertEqual(self._m3u8s(), ["Playlists/A.m3u8"])
        self.assertEqual(self._music(), ["A", "Shared"])   # b, c pruned; a, shared kept
        self.assertEqual(list(manifest.load(self.dev)["playlists"]), ["1"])  # key 2 forgotten

    def test_soundbox_delete_drifts_and_syncs_both_ways(self):
        # Two planes of data (Sonny, 2026-07-02): rekordbox is king, but a
        # SoundBox delete vetoes a track — an already-exported playlist must go
        # "out of date", Sync prunes the file, restore re-drifts and re-adds it.
        P = Playlist(name="SET", folder_path=[], keys=["a", "b"], pid="1")
        lib = self._lib(P)
        engine.export_playlists(lib, [P], self.dev)
        self.assertFalse(engine.device_drift(lib, self.dev).any())

        lib.hidden_ids = {"b"}  # user deletes b from SoundBox
        dr = engine.device_drift(lib, self.dev)
        self.assertEqual([k for k, _ in dr.changed], ["1"])
        engine.resync_device(lib, self.dev)
        self.assertFalse((self.dev / "Music/Artist/B").exists())  # pruned
        text = (self.dev / "Playlists/SET.m3u8").read_text()
        self.assertNotIn("B.wav", text)
        self.assertFalse(engine.device_drift(lib, self.dev).any())

        lib.hidden_ids = set()  # user restores it
        dr = engine.device_drift(lib, self.dev)
        self.assertEqual([k for k, _ in dr.changed], ["1"])
        engine.resync_device(lib, self.dev)
        self.assertTrue((self.dev / "Music/Artist/B").is_dir())   # re-copied
        self.assertIn("B.wav", (self.dev / "Playlists/SET.m3u8").read_text())
        self.assertFalse(engine.device_drift(lib, self.dev).any())


    def test_playlist_removal_drifts_and_syncs(self):
        # Remove-from-playlist (veto keyed by pid) must behave like the delete
        # plane: drift → Sync rewrites m3u8 + prunes ONLY if orphaned. A track
        # still wanted by another playlist is never pruned.
        P1 = Playlist(name="ONE", folder_path=[], keys=["a", "s"], pid="1")
        P2 = Playlist(name="TWO", folder_path=[], keys=["s"], pid="2")
        lib = self._lib(P1, P2)
        engine.export_playlists(lib, [P1, P2], self.dev)

        lib.removed_by_playlist = {"1": {"s"}}   # remove shared from ONE only
        dr = engine.device_drift(lib, self.dev)
        self.assertEqual([k for k, _ in dr.changed], ["1"])
        engine.resync_device(lib, self.dev)
        self.assertNotIn("Shared.wav", (self.dev / "Playlists/ONE.m3u8").read_text())
        self.assertIn("Shared.wav", (self.dev / "Playlists/TWO.m3u8").read_text())
        self.assertTrue((self.dev / "Music/Artist/Shared").is_dir())  # TWO still wants it
        self.assertFalse(engine.device_drift(lib, self.dev).any())


if __name__ == "__main__":
    unittest.main()
