"""UI-layer tests: MainWindow wiring, offscreen, against a synthetic library.

Hermetic by construction (see soundbox-gotchas):
  • QStandardPaths test mode BEFORE the app module loads — all saves land in
    the qttest sandbox, never the real library.json / art cache.
  • rekordbox_db.db_available patched False — the live DB is never touched.
One QApplication + one MainWindow for the module (Qt allows only one app).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QStandardPaths, Qt

QStandardPaths.setTestModeEnabled(True)

from soundbox import engine, rekordbox_db
from soundbox.rekordbox_xml import Library, Playlist, Track

_app = _win = None


def _make_lib() -> Library:
    tracks = {
        "1": Track("1", "Bravo", "Zed", "file://localhost/tmp/b.wav",
                   bpm="140.00", key="10A", total_time=185, date_added="2026-01-02"),
        "2": Track("2", "alpha", "Ann", "file://localhost/tmp/a.wav",
                   bpm="96.00", key="2A", total_time=720, date_added="2026-03-01"),
        "3": Track("3", "Charlie", "Mia", "file://localhost/tmp/c.wav",
                   bpm="128.00", key="9B", total_time=45, date_added="2026-02-10"),
    }
    pls = [Playlist("SET ONE", ["CRATES"], keys=["3", "1"]),
           Playlist("SET TWO", [], keys=["2"])]
    return Library(tracks_by_id=tracks, playlists=pls, source_xml="synthetic")


def setUpModule():
    global _app, _win
    rekordbox_db.db_available = lambda: False  # tests never read the live DB
    from desktop.app import MainWindow, make_app
    _app = make_app()
    _win = MainWindow()
    _win._dev_timer.stop()  # no background device polling during tests
    _win.lib = _make_lib()
    _win._build_artist_registry()
    _win._apply_settings_to_lib()
    _win._populate_tree(engine.summary(_win.lib))


def tearDownModule():
    _win.shutdown()


class _FakeArt:
    def __init__(self, kinds):
        self.kinds = kinds

    def status_kind(self, tid):
        return self.kinds.get(tid, "")


class CollectionView(unittest.TestCase):
    def test_pinned_first_with_count(self):
        it = _win.sb_tree.topLevelItem(0)
        self.assertIn("Collection", it.text(0))
        self.assertIn("(3)", it.text(0))

    def test_shows_all_tracks_newest_first(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual([r["title"] for r in _win.model._rows],
                         ["alpha", "Charlie", "Bravo"])  # date_added desc

    def test_not_a_custom_playlist(self):
        from desktop.trees import CUSTOM_ROLE
        self.assertFalse(_win.sb_tree.topLevelItem(0).data(0, CUSTOM_ROLE))

    def test_honors_hide_untracked(self):
        # Direct call (NOT via a Qt slot — slots swallow exceptions, which hid a
        # real is_untracked signature crash once). Keyless+no-BPM+short = hidden.
        _win.lib.tracks_by_id["9"] = Track("9", "cue stub", "",
                                           "file://localhost/tmp/s.wav",
                                           total_time=2)
        _win.lib.hide_untracked = True
        try:
            names = [t.name for t in _win._collection_tracks()]
            self.assertNotIn("cue stub", names)
            self.assertEqual(len(names), 3)
        finally:
            _win.lib.hide_untracked = False
            del _win.lib.tracks_by_id["9"]


class PlaylistSelection(unittest.TestCase):
    def test_rows_in_sacred_order(self):
        it = _win._find_tree_item("CRATES / SET ONE")
        self.assertIsNotNone(it)
        _win.tree.setCurrentItem(it)
        _app.processEvents()
        self.assertEqual([r["id"] for r in _win.model._rows], ["3", "1"])

    def test_select_only_clears_other_trees(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        self.assertIsNone(_win.sb_tree.currentItem())


class ArtColumn(unittest.TestCase):
    def test_rows_annotated_from_cache(self):
        real = _win.art_cache
        _win.art_cache = _FakeArt({"3": "cover", "1": "miss"})
        try:
            _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
            _win._reload_current_view()
        finally:
            _win.art_cache = real
        self.assertEqual([r["art"] for r in _win.model._rows], ["cover", "miss"])


class SortAndSearch(unittest.TestCase):
    def setUp(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()

    def test_header_sort_then_clear_restores(self):
        from desktop.model import COLUMNS
        bpm = next(i for i, c in enumerate(COLUMNS) if c[0] == "bpm")
        _win.table.sortByColumn(bpm, Qt.DescendingOrder)
        self.assertEqual([r["id"] for r in _win.model._rows], ["1", "3"])
        _win.table.sortByColumn(-1, Qt.AscendingOrder)
        self.assertEqual([r["id"] for r in _win.model._rows], ["3", "1"])

    def test_search_filters_titles(self):
        _win.search.setText("brav")
        self.assertEqual([r["id"] for r in _win.model._rows], ["1"])
        _win.search.setText("")
        self.assertEqual(len(_win.model._rows), 2)


class SettingsPage(unittest.TestCase):
    def test_gear_toggles_page(self):
        _win._open_settings()
        self.assertEqual(_win.body_stack.currentIndex(), 1)
        _win._open_settings()  # gear acts as Back
        self.assertEqual(_win.body_stack.currentIndex(), 0)

    def test_master_toggle_updates_settings_and_persists(self):
        _win._chk_hide.setChecked(True)  # real slot → save (qttest sandbox)
        try:
            self.assertTrue(_win.settings["hide_untracked"])
            self.assertTrue(_win.lib.hide_untracked)
            self.assertTrue(_win._data_file.exists())
            self.assertIn(".qttest", str(_win._data_file))  # sandboxed, not the real one
        finally:
            _win._chk_hide.setChecked(False)
        self.assertFalse(_win.settings["hide_untracked"])


class LibraryBackups(unittest.TestCase):
    def test_save_rotates_backups(self):
        import os as _os
        import time as _time
        bdir = _win._data_file.parent / "backups"
        for p in bdir.glob("library-*.json") if bdir.exists() else []:
            p.unlink()
        _win._save_state()                      # ensure library.json exists
        _win.custom["BK TEST"] = ["1"]
        _win._save_state()                      # first backup of the pre-edit state
        backups = list(bdir.glob("library-*.json"))
        self.assertEqual(len(backups), 1)
        # Unchanged content + within the hour → no second backup.
        _win._save_state()
        self.assertEqual(len(list(bdir.glob("library-*.json"))), 1)
        # Age the backup past the throttle and change content → a second one.
        _os.utime(backups[0], (backups[0].stat().st_atime,
                               _time.time() - 7200))
        _win.custom.pop("BK TEST")
        _win._save_state()
        self.assertEqual(len(list(bdir.glob("library-*.json"))), 2)

    def test_backup_failure_never_blocks_save(self):
        # Make the backup path GENUINELY fail: a file squatting where the
        # backups directory must go → mkdir raises OSError inside _backup_state.
        import shutil
        bdir = _win._data_file.parent / "backups"
        if bdir.is_dir():
            shutil.rmtree(bdir)
        bdir.write_text("squatter")
        try:
            _win._save_state()  # must not raise; library.json must still land
            self.assertTrue(_win._data_file.exists())
        finally:
            bdir.unlink()


class DeviceLabels(unittest.TestCase):
    def test_drift_beats_free_space(self):
        s = _win._device_label("IPOD", 5_000_000_000, 2)
        self.assertIn("2 playlists out of date", s)

    def test_non_fat_warning(self):
        s = _win._device_label("IPOD", 5_000_000_000, 0, "exfat")
        self.assertIn("not FAT32", s)
        self.assertIn("EXFAT", s)

    def test_fat_shows_free(self):
        s = _win._device_label("IPOD", 5_000_000_000, 0, "msdos")
        self.assertIn("5.0 GB free", s)

    def test_tooltip_warns_non_fat(self):
        tip = _win._device_tip("/Volumes/X", 1_000_000_000)
        self.assertIn("Filesystem", tip)


if __name__ == "__main__":
    unittest.main()


class DeletedTracks(unittest.TestCase):
    """SoundBox-only delete (2026-07-02): hides from views + exports, restorable,
    rekordbox never involved."""

    def tearDown(self):
        _win.hidden.clear()
        _win._apply_settings_to_lib()

    def test_delete_hides_everywhere_and_restores(self):
        _win.hidden.update(["1"])
        _win._apply_settings_to_lib()
        self.assertNotIn("1", [t.track_id for t in _win._collection_tracks()])
        pl = _win.lib.playlists[0]  # SET ONE keys ["3","1"]
        self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ["3"])
        _win._restore_hidden()
        self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ["3", "1"])
        self.assertFalse(_win.hidden)

    def test_copy_filenames_real_on_disk_names(self):
        _win._copy_filenames(["1", "2"])
        from PySide6.QtWidgets import QApplication
        self.assertEqual(QApplication.clipboard().text(), "b.wav\na.wav")

    def test_cmd_r_renames_current_row(self):
        from PySide6.QtWidgets import QAbstractItemView
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        _win.table.setCurrentIndex(_win.model.index(0, 3))  # non-editable cell
        _win._rename_current()  # ⌘R → falls back to the Title editor
        self.assertEqual(_win.table.state(), QAbstractItemView.EditingState)
        self.assertEqual(_win.table.currentIndex().column(), 0)  # Title cell
        _win.table.closePersistentEditor(_win.table.currentIndex())
        _app.processEvents()
        _win.table.setCurrentIndex(_win.model.index(0, 1))  # Artist cell selected
        _win._rename_current()  # ⌘R → edits the ARTIST, not the title
        self.assertEqual(_win.table.state(), QAbstractItemView.EditingState)
        self.assertEqual(_win.table.currentIndex().column(), 1)
        _win.table.closePersistentEditor(_win.table.currentIndex())

    def test_remove_from_custom_keeps_collection(self):
        _win.custom["MIX"] = ["1", "2"]
        _win._remove_from_custom("MIX", ["1"])
        self.assertEqual(_win.custom["MIX"], ["2"])
        # Collection unaffected — track 1 still everywhere else.
        self.assertIn("1", [t.track_id for t in _win._collection_tracks()])
        _win.custom.pop("MIX", None)

    def test_remove_from_rb_playlist_vetoes_only_that_playlist(self):
        pl = _win.lib.playlists[0]           # SET ONE keys ["3","1"]
        pl.pid = "42"
        try:
            _win._remove_from_rb_playlist(pl.display_path, ["1"])
            self.assertEqual([t.track_id for t in _win.lib.resolve(pl)], ["3"])
            self.assertIn("1", [t.track_id for t in _win._collection_tracks()])
        finally:
            pl.pid = ""
            _win.removals.clear()
            _win._apply_settings_to_lib()
            _win._reload_current_view()  # don't leave the table stale for later tests


class BackupWorker(unittest.TestCase):
    def _run(self, fn):
        from desktop.workers import OneShotTask
        results = []
        w = OneShotTask(fn)
        w.done.connect(results.append)
        w.start()
        self.assertTrue(w.wait(5000))
        _app.processEvents()
        return results[0]

    def test_result_and_exception_delivery(self):
        self.assertEqual(self._run(lambda: 42), 42)
        err = self._run(lambda: (_ for _ in ()).throw(ValueError("bad backup")))
        self.assertIsInstance(err, ValueError)

    def test_full_zip_roundtrip_through_app_dirs(self):
        # Real export_backup/restore_backup against the sandboxed app data dir.
        import tempfile
        from pathlib import Path
        from soundbox import backup
        _win._save_state()
        data_dir = _win._data_file.parent
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "b.zip"
            n = self._run(lambda: backup.export_backup(data_dir, z))
            self.assertIsInstance(n, int)
            self.assertGreater(n, 0)
            res = self._run(lambda: backup.restore_backup(data_dir, z))
            self.assertNotIsInstance(res, Exception)
            self.assertIn("overrides", res)


class Graveyard(unittest.TestCase):
    def tearDown(self):
        _win.hidden.clear()
        _win.hidden_dates.clear()
        _win.removals.clear()
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()

    def test_lists_deletes_and_removals_with_dates(self):
        _win.hidden.add("1"); _win.hidden_dates["1"] = "2026-07-01"
        pl = _win.lib.playlists[0]; pl.pid = "42"
        _win.removals["42"] = {"3": "2026-07-02"}
        try:
            _win._refresh_graveyard()
            rows = {_win._grave.topLevelItem(i).text(0):
                    _win._grave.topLevelItem(i).text(1)
                    for i in range(_win._grave.topLevelItemCount())}
            self.assertEqual(rows["Bravo"], "2026-07-01")
            self.assertEqual(rows["Charlie   (from SET ONE)"], "2026-07-02")
        finally:
            pl.pid = ""

    def test_restore_selected_resurrects(self):
        _win.hidden.add("1"); _win.hidden_dates["1"] = "2026-07-01"
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()
        _win._grave.topLevelItem(0).setSelected(True)
        _win._grave_restore_selected()
        self.assertNotIn("1", _win.hidden)
        self.assertNotIn("1", _win.hidden_dates)
        self.assertEqual(_win._grave.topLevelItemCount(), 0)

    def test_date_stamped_on_delete(self):
        import time as _t
        from PySide6.QtWidgets import QMessageBox
        real = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            _win._delete_tracks(["2"])
        finally:
            QMessageBox.question = real
        self.assertEqual(_win.hidden_dates.get("2"), _t.strftime("%Y-%m-%d"))


class TitleJunkSetting(unittest.TestCase):
    def tearDown(self):
        _win.settings["title_rules_off"] = []
        _win.settings["title_words"] = []
        _win._refresh_title_rules()
        _win._apply_settings_to_lib()

    def test_rules_listed_by_name_with_checkboxes(self):
        from soundbox.metadata import BUILTIN_TITLE_RULES
        _win._refresh_title_rules()
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertEqual(labels, [n for _i, n, _p in BUILTIN_TITLE_RULES])
        self.assertTrue(all(_win._rules.topLevelItem(i).checkState(0) == Qt.Checked
                            for i in range(len(labels))))

    def test_unchecking_a_rule_disables_it(self):
        from soundbox.metadata import tidy_title
        _win._refresh_title_rules()
        it = _win._rules.topLevelItem(0)  # "Trailing FINAL"
        it.setCheckState(0, Qt.Unchecked)  # fires _on_rule_toggled
        self.assertIn("final", _win.settings["title_rules_off"])
        self.assertEqual(tidy_title("GUTTER FINAL", _win._title_junk()),
                         "GUTTER FINAL")   # rule off → kept
        self.assertEqual(tidy_title("GUTTER v2", _win._title_junk()), "GUTTER")

    def test_add_and_remove_custom_word(self):
        from soundbox.metadata import tidy_title
        _win._word_edit.setText("promo")
        _win._add_drop_word()
        self.assertEqual(_win.settings["title_words"], ["promo"])
        self.assertEqual(tidy_title("BANGER promo", _win._title_junk()), "BANGER")
        _win._remove_drop_word("promo")
        self.assertEqual(_win.settings["title_words"], [])
        self.assertEqual(tidy_title("BANGER promo", _win._title_junk()),
                         "BANGER promo")

    def test_regex_entry_supported_and_validated(self):
        from soundbox.metadata import tidy_title
        # a power user adds a raw regex — it runs like the built-ins
        _win._word_edit.setText(r"\bdubplate\d+\b")
        _win._add_drop_word()
        self.assertIn(r"\bdubplate\d+\b", _win.settings["title_words"])
        self.assertEqual(tidy_title("WHOMP dubplate33", _win._title_junk()), "WHOMP")
        # its list label marks it as regex
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertTrue(any("regex" in x for x in labels))
        # an INVALID regex is rejected loudly (warning), never stored
        from PySide6.QtWidgets import QMessageBox
        real, hits = QMessageBox.warning, []
        QMessageBox.warning = staticmethod(lambda *a, **k: hits.append(1))
        try:
            _win._word_edit.setText("([bad")
            _win._add_drop_word()
        finally:
            QMessageBox.warning = real
        self.assertEqual(hits, [1])
        self.assertNotIn("([bad", _win.settings["title_words"])


class ColumnPersistence(unittest.TestCase):
    def test_widths_and_visibility_roundtrip(self):
        t = _win.table
        t.setColumnWidth(3, 123)
        t.setColumnHidden(4, True)
        _win._save_columns()
        t.setColumnWidth(3, 60)
        t.setColumnHidden(4, False)
        _win._restore_columns()
        self.assertEqual(t.columnWidth(3), 123)
        self.assertTrue(t.isColumnHidden(4))
        # sort indicator is cleared on restore (never launch pre-sorted)
        self.assertEqual(t.horizontalHeader().sortIndicatorSection(), -1)
        t.setColumnHidden(4, False)
        _win._save_columns()


class ArtistMerges(unittest.TestCase):
    def tearDown(self):
        _win.aliases.clear()
        _win._build_artist_registry()

    def test_alias_applies_and_unmerges(self):
        _win.lib.tracks_by_id["1"].artist = "ZED"     # make ZED a tagged artist
        _win.aliases["zedd"] = "ZED"
        _win._build_artist_registry()
        import soundbox.metadata as m
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "ZED")
        _win._remove_alias("zedd")
        self.assertEqual(_win.aliases, {})
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "zedd")

    def test_merge_dialog_collects_accepts_and_unmerges(self):
        from desktop.suggest import MergeDialog
        dlg = MergeDialog([("zedd", "ZED", 0.93), ("Rotten", "Riot Ten", 0.92)],
                          {"old": "TARGET"}, {"zedd": 1, "ZED": 9})
        dlg.list.topLevelItem(1).setCheckState(0, Qt.Unchecked)  # human rejects
        dlg._unmerge(dlg.done_list.topLevelItem(0), "old")
        dlg._apply()
        self.assertEqual(dlg.accepts, [("zedd", "ZED")])
        self.assertEqual(dlg.unmerges, ["old"])

    def test_apply_merges_updates_state_and_registry(self):
        _win.lib.tracks_by_id["1"].artist = "ZED"
        _win.aliases["gone"] = "X"
        _win._apply_merges([("zedd", "ZED")], ["gone"])   # the real app path
        self.assertEqual(_win.aliases, {"zedd": "ZED"})
        import soundbox.metadata as m
        self.assertEqual(m.effective_artist("2", "x", "zedd", None, True,
                                            _win.lib.artist_registry), "ZED")


class SuggestQueue(unittest.TestCase):
    def test_dialog_collects_checked_accepts(self):
        import desktop.suggest as sg
        real_run = sg.SuggestWorker.run
        sg.SuggestWorker.run = lambda self: None  # no network in tests
        try:
            dlg = sg.SuggestDialog([("1", "t1"), ("2", "t2")])
            dlg._worker.wait(2000)
            dlg._add_suggestion("1", "t1", "GRiZ", 1.0)
            dlg._add_suggestion("2", "t2", "Wrongy", 0.9)
            # Unchecked by default — accepting right away takes NOTHING.
            dlg._accept_checked()
            self.assertEqual(dlg.accepts, [])
            dlg.list.topLevelItem(0).setCheckState(0, Qt.Checked)
            dlg._accept_checked()
            self.assertEqual(dlg.accepts, [("1", "GRiZ")])
            dlg.shutdown()
        finally:
            sg.SuggestWorker.run = real_run


class PlayerHardening(unittest.TestCase):
    def test_scrub_disarmed_until_track_loaded(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        w.resize(400, 44)
        hits = []
        w.seeked.connect(hits.append)
        # simulate what mousePressEvent does when inactive vs active
        class Ev:
            def position(self):
                class P:  # noqa: N801
                    def x(self):
                        return 200.0
                return P()
        w.mousePressEvent(Ev())          # disarmed → ignored
        self.assertEqual(hits, [])
        w.set_active(True)
        w.mousePressEvent(Ev())          # armed → seeks
        self.assertEqual(len(hits), 1)

    def test_play_track_arms_wave_and_rebinding_is_safe(self):
        pb = _win.player_bar
        self.assertFalse(pb.wave._active)
        pb.play_track("1", "/tmp/nonexistent.wav", title="X — Y")
        self.assertTrue(pb.wave._active)
        pb._rebind_audio_device()  # never raises, even with no device change
        pb.stop()

    def test_playback_error_signal_wired_to_status(self):
        _app.processEvents()  # drain any late async error from earlier tests
        hits = []
        real = _win._flash_status
        _win._flash_status = lambda msg, *a: hits.append(msg)
        try:
            _win.player_bar.playback_error.emit("boom")
            _app.processEvents()
        finally:
            _win._flash_status = real
        # The neighboring play_track test's REAL backend error can also land
        # during this window (async, timing varies) — the wiring claim is:
        # every playback_error becomes exactly this status flash.
        self.assertTrue(hits)
        self.assertEqual(set(hits), {"Playback error"})


class QtTrapAudit(unittest.TestCase):
    """Regression pins for the 2026-07-03 Qt-trap audit."""

    def test_oneshots_pruned_after_finish(self):
        from desktop.workers import _OneShotArt
        w = _OneShotArt("1", lambda: None)
        _win._oneshots = [w]
        _win._finish_oneshot(w)
        self.assertEqual(_win._oneshots, [])

    def test_space_only_plays_from_browsing_surfaces(self):
        # Policy tested directly (offscreen focus doesn't propagate reliably).
        self.assertTrue(_win._space_should_play(None))
        self.assertTrue(_win._space_should_play(_win.table))
        self.assertTrue(_win._space_should_play(_win.tree))
        self.assertTrue(_win._space_should_play(_win.device_tree))
        self.assertFalse(_win._space_should_play(_win.search))   # types a space
        self.assertFalse(_win._space_should_play(_win._grave))   # native toggle
        self.assertFalse(_win._space_should_play(_win._rules))
        self.assertFalse(_win._space_should_play(_win.fs_view))  # device browser

    def test_art_index_save_is_atomic(self):
        import json, tempfile
        from pathlib import Path
        from soundbox.artfetch import ArtCache
        with tempfile.TemporaryDirectory() as td:
            c = ArtCache(Path(td))
            c.index["x"] = {"status": "none"}
            c._save()
            self.assertEqual(json.loads((Path(td) / "index.json").read_text()),
                             {"x": {"status": "none"}})
            self.assertFalse((Path(td) / "index.tmp").exists())


class ArtAutostartPolicy(unittest.TestCase):
    def test_autostart_only_when_uncached_tracks_exist(self):
        calls = []
        real = _win._start_art_fill
        _win._start_art_fill = lambda *a, **k: calls.append(1)
        try:
            # every track known to the cache → NO autostart
            for tid in _win.lib.tracks_by_id:
                _win.art_cache.index[tid] = {"status": "none"}
            _win._maybe_autostart_art_fill()
            self.assertEqual(calls, [])
            # one genuinely new track → autostart fires (and the worker's to-do
            # list is exactly the uncached ones, so only new songs get searched)
            _win.art_cache.index.pop(next(iter(_win.lib.tracks_by_id)))
            _win._maybe_autostart_art_fill()
            self.assertEqual(calls, [1])
        finally:
            _win._start_art_fill = real
            _win.art_cache.index.clear()


class ArtistCatalog(unittest.TestCase):
    def tearDown(self):
        _win.art_cache.index.clear()

    def test_groups_only_avatar_tracks_by_artist(self):
        # track 1+3 wear avatars, 2 has a real cover → catalog shows avatars only
        _win.art_cache.index.update({
            "1": {"status": "ok", "src": "https://soundcloud.com/zed", "ext": "jpg"},
            "2": {"status": "ok", "src": "https://soundcloud.com/zed/track", "ext": "jpg"},
            "3": {"status": "ok", "src": "https://soundcloud.com/mia", "ext": "jpg"},
        })
        groups = _win._artist_avatar_groups()
        artists = {a: tids for a, tids, _img in groups}
        self.assertIn("1", artists.get("Zed", []))
        self.assertIn("3", artists.get("Mia", []))
        self.assertNotIn("2", sum((t for _a, t, _i in groups), []))

    def test_apply_sets_art_on_all_tracks(self):
        from soundbox.artfetch import ArtResult
        _win._apply_artist_picture(["1", "3"], ArtResult(
            b"\xff\xd8z", "image/jpeg", 1.0, "https://soundcloud.com/zed", "Zed"))
        self.assertEqual(_win.art_cache.status_kind("1"), "avatar")
        self.assertEqual(_win.art_cache.status_kind("3"), "avatar")

    def test_dialog_stays_open_and_updates_row(self):
        from desktop.artistcatalog import ArtistCatalogDialog
        seen = []

        def on_action(choice, done):
            seen.append(choice["kind"])
            done(True, None)

        dlg = ArtistCatalogDialog([("Zed", ["1", "9"], None)], on_action)
        dlg.list.setCurrentItem(dlg.list.topLevelItem(0))
        dlg._delete()
        self.assertEqual(seen, ["delete"])
        self.assertEqual(dlg.result(), 0)   # NOT auto-closed — stays open
        self.assertEqual(dlg.list.topLevelItemCount(), 0)  # row gone
        self.assertIn("blocked", dlg.status.text())

    def test_delete_blocks_all_artist_tracks(self):
        _win._on_catalog_action(
            {"kind": "delete", "artist": "Zed", "tids": ["1", "3"]},
            lambda ok, img: None)
        self.assertEqual(_win.art_cache.status_kind("1"), "blocked")
        self.assertEqual(_win.art_cache.status_kind("3"), "blocked")
        _win.art_cache.index.clear()

    def test_rename_flows_through_alias_plane(self):
        from PySide6.QtWidgets import QInputDialog
        from desktop.artistcatalog import ArtistCatalogDialog
        applied = []
        dlg = ArtistCatalogDialog(
            [("zedd", ["1"], None)],
            lambda c, done: (applied.append(c), done(True, None)))
        dlg.list.setCurrentItem(dlg.list.topLevelItem(0))
        real = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: ("ZED", True))
        try:
            dlg._rename()   # the ⌘R slot
        finally:
            QInputDialog.getText = real
        self.assertEqual(applied[0]["kind"], "rename")
        self.assertEqual(dlg.list.topLevelItem(0).text(0), "ZED")  # row renamed in place
        self.assertEqual(dlg.result(), 0)   # dialog persists for the next fix
        # app side: rename applies via the alias/merge machinery
        _win._on_catalog_action(
            {"kind": "rename", "artist": "zedd", "new": "ZED", "tids": ["1"]},
            lambda ok, img: None)
        self.assertEqual(_win.aliases.get("zedd"), "ZED")
        _win.aliases.clear()
        _win._build_artist_registry()
