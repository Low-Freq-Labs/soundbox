"""Read-only rekordbox ANLZ path and PQTZ/PWV5 parsing."""

import tempfile
import unittest
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import patch

from soundbox import rekordbox_analysis


class ResolvePath(unittest.TestCase):
    def test_resolves_only_beneath_share(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "rekordbox" / "master.db"
            expected = db.parent / "share/PIONEER/USBANLZ/x/ANLZ0000.DAT"
            got = rekordbox_analysis.resolve_dat_path(
                db, "/PIONEER/USBANLZ/x/ANLZ0000.DAT")
            self.assertEqual(got, expected.resolve())

    def test_blank_and_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "rekordbox/master.db"
            self.assertIsNone(rekordbox_analysis.resolve_dat_path(db, ""))
            self.assertIsNone(rekordbox_analysis.resolve_dat_path(
                db, "../../master.db"))


class ReadAnalysis(unittest.TestCase):
    def test_merges_hot_and_memory_cues_with_extended_metadata(self):
        def entry(**values):
            defaults = {
                "hot_cue": 0, "status": "enabled", "type": "single",
                "time": 0, "loop_time": -1, "comment": "",
                "color_red": 0, "color_green": 0, "color_blue": 0,
            }
            defaults.update(values)
            return SimpleNamespace(**defaults)

        class Tag:
            def __init__(self, kind, entries):
                self.content = SimpleNamespace(cue_type=kind, entries=entries)

            def get(self):
                return self.content

        class FakeFile:
            def __init__(self, tags):
                self._tags = tags
                self.tag_types = list(tags)

            def getall_tags(self, name):
                return self._tags.get(name, [])

        classic = FakeFile({"PCOB": [
            Tag("hotcue", [entry(hot_cue=1, time=5_000)]),
            Tag("memory", [entry(time=17_145)]),
        ]})
        extended = FakeFile({"PCO2": [
            Tag("hotcue", [entry(
                hot_cue=1, time=5_000, comment="Intro",
                color_red=220, color_green=40, color_blue=90)]),
            Tag("memory", [entry(time=17_145, comment="Mix")]),
        ]})

        got = rekordbox_analysis._cue_rows(classic, extended)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], {
            "kind": "hot", "slot": 0, "position_ms": 5_000, "end_ms": -1,
            "type": "single", "label": "Intro", "color": [220, 40, 90],
        })
        self.assertEqual(got[1]["kind"], "memory")
        self.assertEqual(got[1]["slot"], 0)
        self.assertEqual(got[1]["position_ms"], 17_145)
        self.assertEqual(got[1]["label"], "Mix")

    def test_reads_pwv7_three_band_detail_without_modifying_2ex(self):
        with tempfile.TemporaryDirectory() as td:
            dat = Path(td) / "ANLZ0000.DAT"
            bands = bytes([1, 2, 3, 127, 12, 4])
            tag = (
                b"PWV7"
                + struct.pack(">IIIII", 24, 24 + len(bands), 3, 2, 0x00960000)
                + bands
            )
            two_ex = dat.with_suffix(".2EX")
            two_ex.write_bytes(b"PMAI fixture" + tag + b"trailer")
            before = two_ex.read_bytes()
            self.assertEqual(rekordbox_analysis._read_pwv7(dat), bands)
            self.assertEqual(two_ex.read_bytes(), before)

    def test_reads_grid_and_rgb_without_saving(self):
        class Grid:
            beats = [1, 2, 3]
            bpms = [140.0, 140.0, 139.99]
            times = [0.05, 0.479, 0.907]

        class Color:
            def get(self):
                return [0.25, 1.0], [[1, 6, 7], [12, 2, 1]]

        class FakeFile:
            def __init__(self, ext):
                self.tag_types = ["PQTZ"] if ext == ".DAT" else ["PWV5"]
                self.ext = ext

            def get_tag(self, name):
                return Grid() if name == "PQTZ" else Color()

        seen = []

        def parse(path):
            seen.append(Path(path))
            return FakeFile(Path(path).suffix.upper())

        with tempfile.TemporaryDirectory() as td:
            dat = Path(td) / "ANLZ0000.DAT"
            ext = dat.with_suffix(".EXT")
            dat.write_bytes(b"read-only fixture")
            ext.write_bytes(b"read-only fixture")
            before = (dat.read_bytes(), ext.read_bytes())
            with patch("pyrekordbox.anlz.AnlzFile.parse_file", side_effect=parse):
                got = rekordbox_analysis.read(dat)
            self.assertEqual(got["beats"], [
                [1, 50, 140.0], [2, 479, 140.0], [3, 907, 139.99]])
            self.assertEqual(got["heights"], [0.25, 1.0])
            self.assertEqual(got["colors"], [[1, 6, 7], [12, 2, 1]])
            self.assertEqual(seen, [dat, ext])
            self.assertEqual((dat.read_bytes(), ext.read_bytes()), before)

    def test_grid_only_reader_never_opens_ext_or_2ex(self):
        with tempfile.TemporaryDirectory() as td:
            dat = Path(td) / "ANLZ0000.DAT"
            ext = dat.with_suffix(".EXT")
            two_ex = dat.with_suffix(".2EX")
            entries = b"".join((
                struct.pack(">HHI", 1, 12800, 246),
                struct.pack(">HHI", 2, 12800, 715),
            ))
            tag = (
                b"PQTZ"
                + struct.pack(">II", 24, 24 + len(entries))
                + b"\0" * 4
                + struct.pack(">II", 0x80000, 2)
                + entries
            )
            dat.write_bytes(b"sacred-prefix" + tag + b"sacred-suffix")
            ext.write_bytes(b"sacred ext")
            two_ex.write_bytes(b"sacred 2ex")
            before = (dat.read_bytes(), ext.read_bytes(), two_ex.read_bytes())
            with patch(
                    "pyrekordbox.anlz.AnlzFile.parse_file",
                    side_effect=AssertionError("generic parser used")) as parse:
                got = rekordbox_analysis.read_beats(dat)
            self.assertEqual(got, [[1, 246, 128.0], [2, 715, 128.0]])
            parse.assert_not_called()
            self.assertEqual(
                (dat.read_bytes(), ext.read_bytes(), two_ex.read_bytes()),
                before)

    def test_missing_or_malformed_is_empty(self):
        self.assertEqual(rekordbox_analysis.read("/no/ANLZ0000.DAT"), {})
        with tempfile.TemporaryDirectory() as td:
            dat = Path(td) / "ANLZ0000.DAT"
            dat.write_bytes(b"not an analysis file")
            self.assertEqual(rekordbox_analysis.read(dat), {})
