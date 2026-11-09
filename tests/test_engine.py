import tempfile
import unittest
from pathlib import Path

from soundbox import engine
from tests.support.library import write_xml


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

    def test_verify_refuses_reserved_and_escaped_playlist_entries(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dev = root / "device"
            (dev / "Playlists").mkdir(parents=True)
            reserved = dev / "PIONEER"
            reserved.mkdir()
            (reserved / "reserved.mp3").write_bytes(b"private")
            outside = root / "outside.mp3"
            outside.write_bytes(b"private")
            (dev / "escape.mp3").symlink_to(outside)
            entries = ["/PIONEER/reserved.mp3", "/escape.mp3", "../outside.mp3"]
            (dev / "Playlists/TEST.m3u8").write_text("\n".join(entries))
            original_is_file = Path.is_file

            def guarded_is_file(path, *args, **kwargs):
                if path == outside or reserved in path.parents:
                    raise AssertionError("verification probed forbidden contents")
                return original_is_file(path, *args, **kwargs)

            with mock.patch.object(Path, "is_file", guarded_is_file):
                result = engine.verify_export(dev, deep=True)
            self.assertEqual([entry for _, entry in result["missing"]], entries)

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


if __name__ == "__main__":
    unittest.main()
