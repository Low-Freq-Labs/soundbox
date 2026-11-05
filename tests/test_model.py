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

    def test_sort_time_understands_hour_format(self):
        self.assertEqual(_sort_value("time", {"time": "1:30:00"}),
                         _sort_value("time", {"time": "90:00"}))
        self.assertLess(_sort_value("time", {"time": "59:59"}),
                        _sort_value("time", {"time": "1:00:00"}))

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


class DisplayRoles(unittest.TestCase):
    def test_display_edit_alignment_and_default_paint_roles_retain_their_meaning(self):
        from PySide6.QtCore import QModelIndex
        m = TrackModel(_rows())
        self.assertEqual(m.data(m.index(0, _col('title'))), 'Bravo')
        empty_artist = m.index(2, _col('artist'))
        self.assertEqual(m.data(empty_artist), '—')
        self.assertEqual(m.data(empty_artist, Qt.EditRole), '')
        self.assertEqual(m.data(m.index(0, ART_COL), Qt.TextAlignmentRole),
                         int(Qt.AlignHCenter | Qt.AlignVCenter))
        self.assertEqual(m.data(m.index(0, _col('bpm')), Qt.TextAlignmentRole),
                         int(Qt.AlignRight | Qt.AlignVCenter))
        self.assertIsNone(m.data(empty_artist, Qt.TextAlignmentRole))
        for role in (Qt.FontRole, Qt.BackgroundRole, Qt.ForegroundRole,
                     Qt.DecorationRole, Qt.CheckStateRole, Qt.UserRole):
            self.assertIsNone(m.data(m.index(0, 0), role))
            self.assertIsNone(m.data(QModelIndex(), role))
        self.assertIsNone(m.data(QModelIndex()))
        self.assertEqual(m.headerData(0, Qt.Horizontal), 'Track Title')
        self.assertIsNone(m.headerData(0, Qt.Vertical))


class AnalysisUpdates(unittest.TestCase):
    def test_estimated_bpm_tooltip_requires_both_flag_and_value(self):
        m = TrackModel(_rows())
        index = m.index(0, _col('bpm'))
        self.assertIsNone(m.data(index, Qt.ToolTipRole))
        self.assertFalse(m.update_analysis('1', bpm_estimated=True))
        self.assertEqual(m.data(index), '140.00')
        self.assertEqual(m.data(index, Qt.ToolTipRole), 'Estimated BPM from waveform')
        m.update_analysis('3', bpm_estimated=True)
        self.assertIsNone(m.data(m.index(2, _col('bpm')), Qt.ToolTipRole))
        for invalid in (None, 1, 'yes'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                m.update_analysis('1', bpm_estimated=invalid)

    def test_estimate_metadata_refreshes_bpm_without_resetting_filtered_sorted_view(self):
        m = TrackModel(_rows())
        m.set_filter('140')
        m.sort(_col('bpm'))
        resets, changes = [], []
        m.modelReset.connect(lambda: resets.append(True))
        m.dataChanged.connect(lambda first, last, roles:
                              changes.append((first.column(), last.column(), roles)))
        hay = m._all_rows[0]['_hay']
        self.assertFalse(m.update_analysis('1', bpm='140.00', bpm_estimated=True))
        self.assertEqual(resets, [])
        self.assertEqual(m._all_rows[0]['_hay'], hay)
        self.assertEqual([(first, last) for first, last, _roles in changes],
                         [(_col('bpm'), _col('bpm'))])
        self.assertEqual(changes[0][2], [Qt.DisplayRole, Qt.ToolTipRole])
        self.assertTrue(m.update_analysis('1', bpm='150.00', bpm_estimated=True))
        self.assertEqual(m.rowCount(), 0)
        self.assertEqual(resets, [True])

    def test_accepted_bpm_update_clears_estimate_even_when_value_is_unchanged(self):
        m = TrackModel([{**_rows()[0], 'bpm_estimated': True}])
        self.assertFalse(m.update_bpm('1', '140.00'))
        self.assertFalse(m._rows[0]['bpm_estimated'])
        self.assertIsNone(m.data(m.index(0, _col('bpm')), Qt.ToolTipRole))
        m.update_bpm('1', '141.00', estimated=True)
        self.assertEqual(m.data(m.index(0, _col('bpm')), Qt.ToolTipRole),
                         'Estimated BPM from waveform')


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


class IncrementalModelWork(unittest.TestCase):
    def test_same_normalized_filter_and_sort_preserve_selection_indexes_without_work(self):
        from unittest import mock
        from PySide6.QtCore import QPersistentModelIndex
        import desktop.model as model
        m = TrackModel(_rows())
        m.set_filter("a")
        m.sort(_col("title"))
        selected = QPersistentModelIndex(m.index(1, 0))
        resets = []
        m.modelReset.connect(lambda: resets.append(True))
        with mock.patch.object(model, "row_matches", side_effect=AssertionError("repeated search")), \
                mock.patch.object(model, "_sort_value", side_effect=AssertionError("repeated sort")):
            m.set_filter("  A  ")
            m.sort(_col("title"), Qt.AscendingOrder)
        self.assertEqual(resets, [])
        self.assertTrue(selected.isValid())
        self.assertEqual(selected.data(), "Bravo")

    def test_replacing_rows_still_reapplies_an_unchanged_filter_and_sort(self):
        m = TrackModel(_rows())
        m.set_filter("alpha")
        m.sort(_col("title"))
        rows = _rows()
        rows[1]["title"] = "No longer matches"
        rows[0]["title"] = "alpha replacement"
        m.set_rows(rows)
        m.set_filter("alpha")
        self.assertEqual([row["id"] for row in m._rows], ["1"])

    def test_inline_edit_reindexes_filter_and_updates_duplicate_memberships(self):
        first = _rows()[0]
        m = TrackModel([dict(first), dict(first), _rows()[1]])
        m.set_filter("bravo")
        edits = []
        m.cell_edited.connect(lambda *args: edits.append(args))
        self.assertTrue(m.setData(m.index(0, _col("title")), "Renamed"))
        self.assertEqual(m.rowCount(), 0)
        self.assertEqual(edits, [("title", "1", "Renamed")])
        m.set_filter("renamed")
        self.assertEqual([row["title"] for row in m._rows], ["Renamed", "Renamed"])

    def test_inline_sort_edit_reorders_without_changing_export_order(self):
        m = TrackModel(_rows())
        m.sort(_col("title"))
        self.assertTrue(m.setData(m.index(0, _col("title")), "Zulu"))
        self.assertEqual([row["title"] for row in m._rows], ["Bravo", "Charlie", "delta", "Zulu"])
        m.sort(-1)
        self.assertEqual([row["id"] for row in m._rows], ["1", "2", "3", "4"])

    def test_identical_explicit_edit_still_records_manual_intent(self):
        m = TrackModel(_rows())
        edits = []
        m.cell_edited.connect(lambda *args: edits.append(args))
        self.assertTrue(m.setData(m.index(0, 0), "Bravo"))
        self.assertEqual(edits, [("title", "1", "Bravo")])

    def test_art_fill_keeps_browsing_order_until_explicit_same_direction_sort(self):
        m = TrackModel([{'id': 'one', 'art': 'cover'}, {'id': 'two', 'art': 'miss'}])
        m.sort(_col('art'))
        m.refresh_art(lambda tid: 'miss' if tid == 'one' else 'cover')
        self.assertEqual([row['id'] for row in m._rows], ['one', 'two'])
        m.sort(_col('art'))
        self.assertEqual([row['id'] for row in m._rows], ['two', 'one'])
        m.sort(-1)
        self.assertEqual([row['id'] for row in m._rows], ['one', 'two'])
