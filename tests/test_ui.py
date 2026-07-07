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
        stamp = _win.hidden_dates.get("2")
        self.assertTrue(stamp.startswith(_t.strftime("%Y-%m-%d ")))
        self.assertRegex(stamp, r"\d{2}:\d{2}$")  # minute precision


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
        _win.lib.tracks_by_id["1"].artist = "Zed"   # undo test mutation
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


class SearchSelection(unittest.TestCase):
    def test_selection_survives_search_and_clear(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        # narrow to Bravo, click it
        _win.search.setText("brav")
        _win.table.setCurrentIndex(_win.model.index(0, 0))
        picked = _win.model._rows[0]["id"]
        # clear the search — the same track must still be selected (not oblivion)
        _win.search.setText("")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)
        # narrowing to something that EXCLUDES it: selection can't show; but
        # bringing it back re-seats it again
        _win.search.setText("charl")
        _win.search.setText("")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())


class GraveyardFreshness(unittest.TestCase):
    def test_delete_shows_in_graveyard_immediately(self):
        from PySide6.QtWidgets import QMessageBox
        real = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            _win._delete_tracks(["2"])  # no settings-open needed
        finally:
            QMessageBox.question = real
        names = [_win._grave.topLevelItem(i).text(0)
                 for i in range(_win._grave.topLevelItemCount())]
        self.assertTrue(any("alpha" in n for n in names))
        _win.hidden.clear()
        _win.hidden_dates.clear()
        _win._apply_settings_to_lib()
        _win._refresh_graveyard()


class ArtistsView(unittest.TestCase):
    def test_click_opens_browser_folder_page(self):
        _win._refresh_custom()
        it = _win.sb_tree.topLevelItem(1)
        self.assertIn("Artists", it.text(0))
        self.assertIn("(3)", it.text(0))          # 3 distinct artists
        _win.sb_tree.setCurrentItem(it)
        _app.processEvents()
        self.assertEqual(_win.stack.currentIndex(), 2)   # the Artists browser
        names = [_win.artists_view.topLevelItem(i).text(0)
                 for i in range(_win.artists_view.topLevelItemCount())]
        self.assertEqual(names, ["Ann  (1)", "Mia  (1)", "Zed  (1)"])
        # names only — no songs until you drop an artist down
        self.assertEqual(_win.artists_view.topLevelItem(0).childCount(), 0)

    def test_expand_reveals_songs_lazily(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)   # fires _fill_artist
        _app.processEvents()
        self.assertEqual(zed.childCount(), 1)
        self.assertEqual(zed.child(0).text(0), "Bravo")
        self.assertEqual(zed.child(0).text(1), "Zed")   # artist column visible

    def test_selected_track_reads_artists_page(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)
        _app.processEvents()
        _win.artists_view.setCurrentItem(zed.child(0))
        t = _win._selected_track()      # Space-play source of truth
        self.assertIsNotNone(t)
        self.assertEqual(t.track_id, "1")
        _win.artists_view.setCurrentItem(zed)   # artist row = nothing to play
        self.assertIsNone(_win._selected_track())

    def test_stray_letter_never_moves_selection(self):
        """Qt type-ahead is OFF in every browser: pressing a letter while a
        view has focus must not teleport the selection (search box filters)."""
        from PySide6.QtTest import QTest
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        _win.artists_view.setCurrentItem(zed)
        for w in (_win.artists_view, _win.sb_tree, _win.tree, _win.device_tree,
                  _win.table):
            self.assertIsNone(w.keyboardSearch("c"), type(w).__name__)
        QTest.keyClick(_win.artists_view, Qt.Key_A)   # 'a' would jump w/o fix
        self.assertIs(_win.artists_view.currentItem(), zed)

    def test_inline_edit_routes_to_overrides(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))
        _app.processEvents()
        zed = next(_win.artists_view.topLevelItem(i)
                   for i in range(_win.artists_view.topLevelItemCount())
                   if _win.artists_view.topLevelItem(i).text(0).startswith("Zed"))
        zed.setExpanded(True)
        _app.processEvents()
        child = zed.child(0)
        fetch = _win._art_fetch_one
        _win._art_fetch_one = lambda tid: None  # no worker threads in tests
        try:
            child.setText(0, "Bravo Deluxe")       # fires _on_artist_item_edited
            self.assertEqual(_win.titles.get("1"), "Bravo Deluxe")
            child.setText(1, "ZEDD")               # artist edit → override
            self.assertEqual(_win.overrides.get("1"), "ZEDD")
            long = "X" * 500                        # clamp like Collection
            child.setText(0, long)
            from soundbox.paths import TITLE_MAX
            self.assertEqual(len(_win.titles["1"]), TITLE_MAX)
        finally:
            _win._art_fetch_one = fetch
            _win.titles.pop("1", None)
            _win.overrides.pop("1", None)
            _win._save_state()
            _win._build_artist_registry()
        # collection view still works after (back to the table page)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual(_win.stack.currentIndex(), 0)


class DefaultView(unittest.TestCase):
    def test_lands_on_collection_when_nothing_selected(self):
        _win._select_only(None)  # clear all three sidebars
        _win.sb_tree.setCurrentItem(None)
        _win.tree.setCurrentItem(None)
        _win._default_to_collection()
        cur = _win.sb_tree.currentItem()
        self.assertIsNotNone(cur)
        self.assertIn("Collection", cur.text(0))


class SortSelection(unittest.TestCase):
    def test_selection_survives_sort_toggle_after_search(self):
        _win.tree.setCurrentItem(_win._find_tree_item("CRATES / SET ONE"))
        _app.processEvents()
        _win.search.setText("brav")
        _win.table.setCurrentIndex(_win.model.index(0, 0))
        picked = _win.model._rows[0]["id"]
        _win.search.setText("")
        _win.table.sortByColumn(0, Qt.AscendingOrder)   # A→Z
        _app.processEvents()
        _win.table.sortByColumn(0, Qt.DescendingOrder)  # Z→A
        _app.processEvents()
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)
        _win.table.sortByColumn(-1, Qt.AscendingOrder)
        _win.search.setText("")



class ViewPlaceMemory(unittest.TestCase):
    """Leaving a view and coming back re-seats where you were — never back
    at the top like you were never there."""

    def _click(self, item):
        _win.sb_tree.setCurrentItem(item)
        _app.processEvents()

    def test_collection_place_survives_round_trip(self):
        self._click(_win.sb_tree.topLevelItem(0))       # Collection
        self.assertGreater(len(_win.model._rows), 1)
        row = len(_win.model._rows) - 1                 # park on the LAST track
        picked = _win.model._rows[row]["id"]
        _win.table.setCurrentIndex(_win.model.index(row, 0))
        self._click(_win.sb_tree.topLevelItem(1))       # off to Artists…
        self._click(_win.sb_tree.topLevelItem(0))       # …and back
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], picked)


class NowPlayingHighlight(unittest.TestCase):
    """Loading a track always puts the cursor on it in the visible browser."""

    def test_table_highlights_loaded_track(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()
        _win.table.clearSelection()
        _win.table.setCurrentIndex(_win.model.index(-1, -1))       # nothing current
        _win._highlight_now_playing("2")
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], "2")

    def test_artists_page_expands_and_highlights(self):
        from desktop.app import ARTIST_TRACK_ROLE
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))  # Artists
        _app.processEvents()
        _win._highlight_now_playing("1")                           # Zed — Bravo
        it = _win.artists_view.currentItem()
        self.assertIsNotNone(it)
        self.assertEqual(it.data(0, ARTIST_TRACK_ROLE), "1")
        self.assertTrue(it.parent().isExpanded())

    def test_track_not_in_view_is_a_noop(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        before = _win.table.currentIndex().row()
        _win._highlight_now_playing("no-such-track")
        self.assertEqual(_win.table.currentIndex().row(), before)


class SpaceSemantics(unittest.TestCase):
    """Space plays the HIGHLIGHTED track when it isn't the loaded one —
    pausing is only for when cursor and loaded track agree."""

    def setUp(self):
        self._played = []
        self._toggled = []
        self._orig_play, self._orig_toggle = _win._play_track, _win.player_bar.toggle
        _win._play_track = lambda t: self._played.append(t.track_id)
        _win.player_bar.toggle = lambda: self._toggled.append(1)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()

    def tearDown(self):
        _win._play_track, _win.player_bar.toggle = self._orig_play, self._orig_toggle
        _win.player_bar._track_id = None

    def _select(self, tid):
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == tid)
        _win.table.setCurrentIndex(_win.model.index(row, 0))

    def test_different_track_highlighted_loads_it(self):
        _win.player_bar._track_id = "1"       # something else is loaded
        self._select("2")
        _win._play_selected()
        self.assertEqual(self._played, ["2"])  # loads, never just pauses
        self.assertEqual(self._toggled, [])

    def test_same_track_highlighted_toggles(self):
        _win.player_bar._track_id = "2"
        self._select("2")
        _win._play_selected()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])

    def test_no_selection_toggles_loaded(self):
        _win.player_bar._track_id = "1"
        _win.table.setCurrentIndex(_win.model.index(-1, -1))
        _win._play_selected()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])


class QtWaveformFallback(unittest.TestCase):
    """ALAC/AAC (.m4a) get a REAL waveform via the Qt/ffmpeg decoder — same
    normalization as the libsndfile path, so the bar looks identical."""

    @classmethod
    def setUpClass(cls):
        import math, shutil, struct, subprocess, tempfile, wave
        if not shutil.which("afconvert"):
            raise unittest.SkipTest("afconvert not available")
        cls.dir = tempfile.mkdtemp(prefix="sb-wave-")
        wav = f"{cls.dir}/probe.wav"
        sr = 44100
        with wave.open(wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(b"".join(
                struct.pack("<h", int(0.8 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
                            if i < sr else 0)
                for i in range(sr * 2)))          # 1s tone + 1s silence
        for name, codec in (("alac.m4a", ["-d", "alac"]),
                            ("aac.m4a", ["-d", "aac", "-b", "128000"])):
            subprocess.run(["afconvert", "-f", "m4af", *codec, wav,
                            f"{cls.dir}/{name}"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _check(self, name):
        from desktop.player import qt_peaks
        from soundbox.waveform import DEFAULT_BUCKETS, peaks
        path = f"{self.dir}/{name}"
        self.assertEqual(peaks(path), [], "libsndfile grew m4a support — "
                         "fallback untested; revisit")
        pk = qt_peaks(path)
        self.assertEqual(len(pk), DEFAULT_BUCKETS)
        self.assertEqual(max(pk), 1.0)            # normalized to fill the bar
        half = DEFAULT_BUCKETS // 2
        self.assertGreater(max(pk[:half - 10]), 0.9)   # tone half is loud
        self.assertLess(max(pk[half + 10:]), 0.1)      # silent half is quiet

    def test_alac_waveform(self):
        self._check("alac.m4a")

    def test_aac_waveform(self):
        self._check("aac.m4a")


class DuplicateAdds(unittest.TestCase):
    """Dropping a track that's already in a SoundBox playlist asks Add
    Anyway/Skip — duplicates happen on purpose or not at all."""

    def setUp(self):
        _win.custom["DupTest"] = ["1"]
        self._asked = []

    def tearDown(self):
        _win.custom.pop("DupTest", None)
        _win._refresh_custom()
        _win._save_state()

    def _stub(self, answer):
        def fake(name, dupes):
            self._asked.append((name, list(dupes)))
            return answer
        _win._confirm_duplicate_add = fake
        self.addCleanup(lambda: delattr(_win, "_confirm_duplicate_add"))

    def test_fresh_tracks_add_without_asking(self):
        self._stub(answer=False)
        _win._on_tracks_dropped("DupTest", ["2", "3"])
        self.assertEqual(_win.custom["DupTest"], ["1", "2", "3"])
        self.assertEqual(self._asked, [])          # no dialog for clean drops

    def test_skip_keeps_playlist_unchanged(self):
        self._stub(answer=False)
        _win._on_tracks_dropped("DupTest", ["1", "2"])
        self.assertEqual(_win.custom["DupTest"], ["1", "2"])  # dupe skipped
        self.assertEqual(self._asked, [("DupTest", ["1"])])

    def test_add_anyway_duplicates_on_purpose(self):
        self._stub(answer=True)
        _win._on_tracks_dropped("DupTest", ["1"])
        self.assertEqual(_win.custom["DupTest"], ["1", "1"])

    def test_track_label_uses_effective_title(self):
        self.assertEqual(_win._track_label("1"), "Bravo")
        self.assertEqual(_win._track_label("no-such"), "no-such")


class SearchFocusPolicy(unittest.TestCase):
    """The search box must never grab keyboard focus at launch — the app opens
    on Collection ready to browse (Space-play, arrows), not typing into search."""

    def test_search_is_click_to_focus_only(self):
        self.assertEqual(_win.search.focusPolicy(), Qt.ClickFocus)


class PlaylistReorder(unittest.TestCase):
    """Drag-to-reorder SoundBox playlists: _move_custom rebuilds the dict in the
    new order; the tree clamps drops below the pinned Collection/Artists rows."""

    def setUp(self):
        self._saved = dict(_win.custom)
        _win.custom = {"A": ["1"], "B": ["2"], "C": ["3"]}
        _win._refresh_custom()

    def tearDown(self):
        _win.custom = self._saved
        _win._refresh_custom()
        _win._save_state()

    def test_move_to_front(self):
        _win._move_custom("C", 0)
        self.assertEqual(list(_win.custom), ["C", "A", "B"])
        self.assertEqual(_win.custom["C"], ["3"])  # tracks ride along

    def test_move_down_adjusts_for_own_row(self):
        # Insert line below B = raw index 2; A's own row above it must not
        # double-count, so A lands between B and C.
        _win._move_custom("A", 2)
        self.assertEqual(list(_win.custom), ["B", "A", "C"])

    def test_move_to_end(self):
        _win._move_custom("A", 3)
        self.assertEqual(list(_win.custom), ["B", "C", "A"])

    def test_noop_and_unknown_are_safe(self):
        _win._move_custom("A", 0)
        self.assertEqual(list(_win.custom), ["A", "B", "C"])
        _win._move_custom("nope", 1)
        self.assertEqual(list(_win.custom), ["A", "B", "C"])

    def test_tree_rows_follow_new_order(self):
        from desktop.trees import CUSTOM_ROLE
        _win._move_custom("B", 0)
        first = _win.sb_tree._first_custom_row()
        names = [_win.sb_tree.topLevelItem(i).data(0, CUSTOM_ROLE)
                 for i in range(first, _win.sb_tree.topLevelItemCount())]
        self.assertEqual(names, ["B", "A", "C"])

    def test_drop_row_clamps_below_pinned(self):
        from PySide6.QtCore import QPoint
        tree = _win.sb_tree
        first = tree._first_custom_row()
        self.assertEqual(first, 2)  # Collection + Artists pinned on top
        # A drop at the very top (over the pinned Collection row) clamps to the
        # first custom row instead of landing above the built-ins.
        top = tree.visualItemRect(tree.topLevelItem(0)).top() + 1
        self.assertEqual(tree._drop_row(QPoint(5, top)), first)
        # Below the last row → end of the list.
        self.assertEqual(tree._drop_row(QPoint(5, 10_000)),
                         tree.topLevelItemCount())


class ImportedFiles(unittest.TestCase):
    """Livesets imported straight into SoundBox: they join the pool (so
    Collection shows them automatically), can land in a SoundBox playlist on
    drop, dedup by path, and persist in library.json's 'imported' section."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.f1 = Path(self._td.name) / "Rezz Live @ Red Rocks 2026.mp3"
        self.f2 = Path(self._td.name) / "Subtronics EDC Set.wav"
        self.f1.write_bytes(b"x")  # existence + extension is what import checks
        self.f2.write_bytes(b"x")
        self._saved_imported = dict(_win.imported)
        self._saved_custom = dict(_win.custom)
        _win.imported = {}

    def tearDown(self):
        for tid in list(_win.imported):
            _win.lib.tracks_by_id.pop(tid, None)
        _win.imported = self._saved_imported
        _win.custom = self._saved_custom
        _win._refresh_custom()
        _win._reload_current_view()  # later classes must not see imported rows
        _win._save_state()
        self._td.cleanup()

    def test_import_joins_collection(self):
        _win._import_files([str(self.f1), str(self.f2)])
        self.assertEqual(len(_win.imported), 2)
        names = [t.name for t in _win._collection_tracks()]
        self.assertIn("Rezz Live @ Red Rocks 2026", names)
        self.assertIn("Subtronics EDC Set", names)
        for tid in _win.imported:
            self.assertTrue(tid.startswith("sb-"))
            self.assertIn(tid, _win.lib.tracks_by_id)

    def test_reimport_reuses_id(self):
        _win._import_files([str(self.f1)])
        first = set(_win.imported)
        _win._import_files([str(self.f1)])
        self.assertEqual(set(_win.imported), first)  # same path → same id

    def test_import_into_playlist_also_hits_collection(self):
        _win.custom["LIVESETS"] = []
        _win._import_files([str(self.f1)], "LIVESETS")
        (tid,) = _win.imported
        self.assertEqual(_win.custom["LIVESETS"], [tid])
        self.assertIn(tid, [t.track_id for t in _win._collection_tracks()])

    def test_non_audio_skipped_audio_kept(self):
        from pathlib import Path
        junk = Path(self._td.name) / "cover.txt"
        junk.write_text("not audio")
        _win._import_files([str(junk), str(self.f1)])
        self.assertEqual(len(_win.imported), 1)  # only the mp3 made it

    def test_survives_a_library_reload(self):
        from soundbox import imports
        _win._import_files([str(self.f1)])
        fresh = _make_lib()  # what a rekordbox refresh hands back
        imports.inject(fresh, _win.imported)
        for tid in _win.imported:
            self.assertIn(tid, fresh.tracks_by_id)

    def test_persisted_in_library_json(self):
        import json
        _win._import_files([str(self.f1)])
        data = json.loads(_win._data_file.read_text(encoding="utf-8"))
        self.assertEqual(data["imported"], _win.imported)


class FixLinksUI(unittest.TestCase):
    """_apply_relinks re-points moved imported files and refreshes the pool."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        self.old = Path(self._td.name) / "Mersiv Sound Haven 2021.m4a"
        self.old.write_bytes(b"x")
        self._saved = dict(_win.imported)
        _win.imported = {}
        _win._import_files([str(self.old)])
        (self.tid,) = _win.imported
        self.new = Path(self._td.name) / "pt.1" / self.old.name
        self.new.parent.mkdir()
        self.old.rename(self.new)  # the user's "move everything into a folder"

    def tearDown(self):
        for tid in list(_win.imported):
            _win.lib.tracks_by_id.pop(tid, None)
        _win.imported = self._saved
        _win._refresh_custom()
        _win._reload_current_view()
        _win._save_state()
        self._td.cleanup()

    def test_missing_then_relink_heals_library(self):
        from soundbox import imports
        self.assertEqual(set(imports.missing(_win.imported)), {self.tid})
        found = imports.find_moved(imports.missing(_win.imported),
                                   imports.search_roots(_win.imported))
        self.assertEqual(found, {self.tid: self.new})
        _win._apply_relinks(found)
        self.assertEqual(imports.missing(_win.imported), {})
        self.assertEqual(_win.lib.tracks_by_id[self.tid].path, self.new)

    def test_fix_links_noop_when_nothing_missing(self):
        _win._apply_relinks(imports_found := {})
        _win.imported[self.tid]["path"] = str(self.new)  # already healthy
        _win._fix_links()  # must not start a worker or pop a dialog
        self.assertEqual(len(getattr(_win, "_oneshots", [])), 0)
