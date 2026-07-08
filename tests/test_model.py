"""TrackModel view logic: click-to-sort (view-only), art column, filter interplay."""

import unittest

from PySide6.QtCore import Qt

from desktop.model import COLUMNS, ART_COL, TrackModel, _sort_value


def _col(key: str) -> int:
    return next(i for i, c in enumerate(COLUMNS) if c[0] == key)


def _rows():
    return [
        {"id": "1", "title": "Bravo", "artist": "Z", "bpm": "140.00", "key": "10A",
         "time": "3:05", "art": "cover"},
        {"id": "2", "title": "alpha", "artist": "A", "bpm": "96.00", "key": "2A",
         "time": "12:00", "art": ""},
        {"id": "3", "title": "Charlie", "artist": "", "bpm": "", "key": "9B",
         "time": "0:45", "art": "miss"},
        {"id": "4", "title": "delta", "artist": "M", "bpm": "128.00", "key": "2B",
         "time": "3:04", "art": "avatar"},
    ]


class SortBehavior(unittest.TestCase):
    def _titles(self, m):
        return [m._rows[i]["title"] for i in range(m.rowCount())]

    def test_unsorted_is_original_order(self):
        m = TrackModel(_rows())
        self.assertEqual(self._titles(m), ["Bravo", "alpha", "Charlie", "delta"])

    def test_sort_title_case_insensitive(self):
        m = TrackModel(_rows())
        m.sort(_col("title"), Qt.AscendingOrder)
        self.assertEqual(self._titles(m), ["alpha", "Bravo", "Charlie", "delta"])

    def test_sort_bpm_numeric_empty_last(self):
        m = TrackModel(_rows())
        m.sort(_col("bpm"), Qt.AscendingOrder)
        self.assertEqual(self._titles(m), ["alpha", "delta", "Bravo", "Charlie"])

    def test_sort_time_as_duration(self):
        m = TrackModel(_rows())
        m.sort(_col("time"), Qt.AscendingOrder)
        self.assertEqual(self._titles(m), ["Charlie", "delta", "Bravo", "alpha"])

    def test_sort_key_camelot_wheel(self):
        m = TrackModel(_rows())
        m.sort(_col("key"), Qt.AscendingOrder)  # 2A < 2B < 9B < 10A (not lexical)
        self.assertEqual(self._titles(m), ["alpha", "delta", "Charlie", "Bravo"])

    def test_sort_art_groups_by_usefulness(self):
        m = TrackModel(_rows())
        m.sort(ART_COL, Qt.AscendingOrder)  # cover, avatar, miss, unfetched
        self.assertEqual(self._titles(m), ["Bravo", "delta", "Charlie", "alpha"])

    def test_clear_sort_restores_original(self):
        m = TrackModel(_rows())
        m.sort(_col("bpm"), Qt.DescendingOrder)
        m.sort(-1)
        self.assertEqual(self._titles(m), ["Bravo", "alpha", "Charlie", "delta"])

    def test_sort_persists_across_set_rows(self):
        m = TrackModel(_rows())
        m.sort(_col("title"), Qt.AscendingOrder)
        m.set_rows(_rows())
        self.assertEqual(self._titles(m), ["alpha", "Bravo", "Charlie", "delta"])

    def test_filter_plus_sort(self):
        m = TrackModel(_rows())
        m.sort(_col("title"), Qt.AscendingOrder)
        m.set_filter("a")  # alpha, Bravo(a), Charlie(a), delta(a) — all contain 'a'
        m.set_filter("lt")  # only delta
        self.assertEqual(self._titles(m), ["delta"])


class ArtColumn(unittest.TestCase):
    def test_glyphs_and_tooltips(self):
        m = TrackModel(_rows())
        glyph = {r["id"]: m.data(m.index(i, ART_COL)) for i, r in enumerate(m._rows)}
        self.assertEqual(glyph, {"1": "●", "2": "", "3": "○", "4": "◐"})
        tip = m.data(m.index(0, ART_COL), Qt.ToolTipRole)
        self.assertIn("Cover art", tip)

    def test_art_not_editable(self):
        m = TrackModel(_rows())
        self.assertFalse(m.flags(m.index(0, ART_COL)) & Qt.ItemIsEditable)
        self.assertFalse(m.setData(m.index(0, ART_COL), "x"))

    def test_refresh_art_updates_in_place(self):
        m = TrackModel(_rows())
        m.refresh_art(lambda tid: "cover" if tid == "2" else "miss")
        self.assertEqual(m._rows[1]["art"], "cover")
        self.assertEqual(m.data(m.index(1, ART_COL)), "●")


class SortValueEdges(unittest.TestCase):
    def test_non_camelot_key_after_wheel(self):
        self.assertLess(_sort_value("key", {"key": "12B"}),
                        _sort_value("key", {"key": "Gm"}))

    def test_empty_sorts_after_values(self):
        self.assertLess(_sort_value("bpm", {"bpm": "999"}),
                        _sort_value("bpm", {"bpm": ""}))


if __name__ == "__main__":
    unittest.main()


class EditClamp(unittest.TestCase):
    """Manual title/artist edits clamp to the DEVICE budgets — what you see in
    the app is exactly what the exported file is named."""

    def test_title_paste_clamped_to_device_budget(self):
        from soundbox.paths import TITLE_MAX
        m = TrackModel([{"id": "1", "title": "x", "artist": "a"}])
        m.setData(m.index(0, 0), "T" * 500)
        self.assertEqual(len(m._rows[0]["title"]), TITLE_MAX)

    def test_artist_clamped(self):
        from soundbox.paths import ARTIST_MAX
        m = TrackModel([{"id": "1", "title": "x", "artist": "a"}])
        m.setData(m.index(0, 1), "A" * 500)
        self.assertEqual(len(m._rows[0]["artist"]), ARTIST_MAX)

    def test_long_title_short_disk_name_never_falls_back(self):
        from pathlib import Path
        from soundbox.paths import device_music_path
        # disk name is a cryptic 3-char acronym; SoundBox title is long + clean
        p = device_music_path(Path("/x/sb2.wav"), "Zero",
                              name_override="Some Beautifully Long Descriptive Title")
        self.assertIn("Some Beautifully Long Descriptive Title", p)
        self.assertNotIn("sb2", p)


class Search(unittest.TestCase):
    """Multi-field, multi-term search — the whole point is finding by ARTIST,
    genre, key, bpm or format, not just title."""

    def _rows(self):
        return [
            {"id": "1", "title": "Live @ EDC 2026", "artist": "Rezz",
             "genre": "Riddim", "key": "2A", "bpm": "140.00", "format": "WAV"},
            {"id": "2", "title": "Sunset Set", "artist": "Of The Trees",
             "genre": "Downtempo", "key": "9B", "bpm": "85.00", "format": "MP3"},
            {"id": "3", "title": "EDC Mainstage", "artist": "Subtronics",
             "genre": "Dubstep", "key": "2A", "bpm": "150.00", "format": "FLAC"},
        ]

    def _ids(self, m):
        return [m._rows[i]["id"] for i in range(m.rowCount())]

    def test_finds_by_artist_not_in_title(self):
        m = TrackModel(self._rows())
        m.set_filter("rezz")            # artist only — the busted case
        self.assertEqual(self._ids(m), ["1"])

    def test_finds_by_genre_and_key_and_format(self):
        m = TrackModel(self._rows())
        m.set_filter("dubstep"); self.assertEqual(self._ids(m), ["3"])
        m.set_filter("9b");      self.assertEqual(self._ids(m), ["2"])
        m.set_filter("flac");    self.assertEqual(self._ids(m), ["3"])

    def test_bare_bpm_number_matches(self):
        m = TrackModel(self._rows())
        m.set_filter("140")             # not "140.00"
        self.assertEqual(self._ids(m), ["1"])

    def test_multi_term_is_and_across_fields(self):
        m = TrackModel(self._rows())
        m.set_filter("edc rezz")        # title has EDC, artist has Rezz → row 1 only
        self.assertEqual(self._ids(m), ["1"])
        m.set_filter("edc subtronics")  # row 3
        self.assertEqual(self._ids(m), ["3"])
        m.set_filter("edc downtempo")   # no single row has both
        self.assertEqual(self._ids(m), [])

    def test_case_insensitive_and_blank_shows_all(self):
        m = TrackModel(self._rows())
        m.set_filter("REZZ"); self.assertEqual(self._ids(m), ["1"])
        m.set_filter("");     self.assertEqual(len(self._ids(m)), 3)

    def test_edit_then_search_reindexes(self):
        m = TrackModel(self._rows())
        m.set_filter("rezz"); self.assertEqual(self._ids(m), ["1"])
        rows = self._rows(); rows[0]["artist"] = "Renamed"
        m.set_rows(rows)                # _hay must rebuild, old term gone
        m.set_filter("rezz");    self.assertEqual(self._ids(m), [])
        m.set_filter("renamed"); self.assertEqual(self._ids(m), ["1"])
