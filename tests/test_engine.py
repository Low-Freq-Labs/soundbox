import tempfile
import unittest
from pathlib import Path

from soundbox import engine
from tests.test_sync import write_xml  # reuse the XML builder


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "a.flac").write_bytes(b"x")
        self.xml = root / "rb.xml"
        write_xml(
            self.xml,
            tracks=[("1", "Artist X - Song", "", root / "a.flac")],
            playlist=["1"],
        )
        self.lib = engine.load_library(self.xml)

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary_and_tree(self):
        s = engine.summary(self.lib)
        self.assertEqual(s["playlistCount"], 1)
        # Tree: ROOT -> SETS(folder) -> My Set(playlist)
        sets = s["tree"]["children"][0]
        self.assertEqual(sets["type"], "folder")
        self.assertEqual(sets["children"][0]["path"], "SETS / My Set")

    def test_rows_have_format_column(self):
        rows = engine.playlist_rows(self.lib, "SETS / My Set")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["format"], "FLAC")
        self.assertEqual(rows[0]["title"], "Song")  # neat_title strips the redundant edge-artist (2026-07-02)


if __name__ == "__main__":
    unittest.main()


class VerifyExport(unittest.TestCase):
    def test_reports_missing_and_present(self):
        import tempfile
        from pathlib import Path
        from soundbox import engine
        with tempfile.TemporaryDirectory() as td:
            dev = Path(td)
            (dev / "Music/A/T").mkdir(parents=True)
            (dev / "Music/A/T/T.wav").write_bytes(b"x")
            (dev / "Playlists").mkdir()
            (dev / "Playlists/GOOD.m3u8").write_text(
                "#EXTM3U\n/Music/A/T/T.wav\n", encoding="utf-8")
            (dev / "Playlists/BAD.m3u8").write_text(
                "#EXTM3U\n/Music/A/T/T.wav\n/Music/GONE/x/x.wav\n# MISSING: y\n",
                encoding="utf-8")
            res = engine.verify_export(dev)
            self.assertEqual(res["playlists"], 2)
            self.assertEqual(res["entries"], 3)
            self.assertEqual(res["missing"],
                             [("Playlists/BAD.m3u8", "/Music/GONE/x/x.wav")])

    def test_empty_device(self):
        import tempfile
        from soundbox import engine
        with tempfile.TemporaryDirectory() as td:
            res = engine.verify_export(td)
            self.assertEqual(res, {"playlists": 0, "entries": 0,
                                   "missing": [], "advisories": []})

    def test_rockbox_art_advisory_only_when_art_is_off(self):
        import tempfile
        from pathlib import Path
        from soundbox.engine import verify_export
        with tempfile.TemporaryDirectory() as td:
            dev = Path(td)
            # plain USB → silent; Rockbox default (embedded may win) → ALSO
            # silent — accepted by design: cover.jpg is the universal floor.
            self.assertEqual(verify_export(dev)["advisories"], [])
            (dev / ".rockbox").mkdir()
            self.assertEqual(verify_export(dev)["advisories"], [])
            (dev / ".rockbox/config.cfg").write_text(
                "album art: prefer embedded\n")
            self.assertEqual(verify_export(dev)["advisories"], [])
            # art fully OFF is the one state worth flagging: nothing shows.
            (dev / ".rockbox/config.cfg").write_text("album art: off\n")
            self.assertIn("OFF", verify_export(dev)["advisories"][0])
