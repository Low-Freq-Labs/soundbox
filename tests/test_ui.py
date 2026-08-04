"""UI-layer tests: MainWindow wiring, offscreen, against a synthetic library.

Hermetic by construction (see soundbox-gotchas):
  • QStandardPaths test mode BEFORE the app module loads — all saves land in
    the qttest sandbox, never the real library.json / art cache / app log.
  • rekordbox_db.db_available patched False — the live DB is never touched.
One QApplication + one MainWindow for the module (Qt allows only one app).
"""

import os
import logging
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QStandardPaths, Qt
from PySide6.QtGui import QKeyEvent

QStandardPaths.setTestModeEnabled(True)

from soundbox import engine, manifest as device_manifest, rekordbox_db
from soundbox.library import Library, Playlist, Track
from desktop.native_deck import PlaybackState

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
    return Library(tracks_by_id=tracks, playlists=pls, source="synthetic")


def setUpModule():
    global _app, _win
    rekordbox_db.db_available = lambda: False  # tests never read the live DB
    from desktop.app import MainWindow, make_app
    _app = make_app()
    # MainWindow normally starts an immediate removable-device discovery before
    # the timer can be stopped. Keep the UI harness genuinely hermetic so a
    # mounted player is never inspected by tests running beside the real app.
    with mock.patch.object(MainWindow, "_refresh_devices", autospec=True):
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
    def test_test_mode_never_opens_the_production_log(self):
        from desktop.app import LOG
        self.assertFalse(any(isinstance(h, logging.FileHandler)
                             for h in LOG.handlers))

    def test_pinned_first_with_count(self):
        it = _win.sb_tree.topLevelItem(0)
        self.assertIn("Collection", it.text(0))
        self.assertIn("(3)", it.text(0))

    def test_orders_by_file_landing_then_name(self):
        # Synthetic tracks point at non-existent files → landing time 0.0 for all,
        # so the sort falls back to title A→Z (real birthtime order is covered by
        # CollectionLandedOrder with real files).
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual([r["title"] for r in _win.model._rows],
                         ["alpha", "Bravo", "Charlie"])  # name fallback

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


class BrowserDropTarget(unittest.TestCase):
    """Dropping onto the main browser adds to the playlist it shows — but only
    for writable SoundBox views. Artists (tag-derived) and rekordbox (rb:, the
    sacred read-only source) must reject every drop."""

    def _resolve(self, view_key):
        _win._view_key = view_key
        return _win._resolve_table_drop()

    def test_custom_playlist_takes_tracks_and_files(self):
        _win.custom["DROP TEST"] = ["1"]
        try:
            self.assertEqual(self._resolve("sb:DROP TEST"), ("playlist", "DROP TEST"))
        finally:
            _win.custom.pop("DROP TEST", None)

    def test_collection_is_file_import_only(self):
        self.assertEqual(self._resolve("sb:Collection"), ("collection", ""))

    def test_artists_view_rejects(self):
        self.assertIsNone(self._resolve("sb:Artists"))

    def test_rekordbox_view_never_written(self):
        self.assertIsNone(self._resolve("rb:CRATES / SET ONE"))

    def test_unknown_custom_name_rejects(self):
        self.assertIsNone(self._resolve("sb:No Such Playlist"))

    def test_no_view_rejects(self):
        self.assertIsNone(self._resolve(None))


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

    def test_waveform_mode_selector_applies_and_persists(self):
        original = _win.settings.get("waveform_mode", "3BAND")
        seen = []
        real = _win.player_bar.set_waveform_mode
        _win.player_bar.set_waveform_mode = seen.append
        try:
            _win._waveform_mode_buttons["RGB"].click()
            self.assertEqual(_win.settings["waveform_mode"], "RGB")
            self.assertEqual(seen, ["RGB"])
            self.assertTrue(_win._waveform_mode_buttons["RGB"].isChecked())
            self.assertTrue(_win._data_file.exists())
        finally:
            _win.player_bar.set_waveform_mode = real
            _win._waveform_mode_buttons[original].click()


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
        self.assertIn("⚠ Sync needed", s)
        self.assertNotIn("2 changes", s)

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

    def test_dual_boot_label_is_explicit(self):
        from types import SimpleNamespace
        profile = SimpleNamespace(label="Dual Boot iPod")
        label = _win._device_label(
            "IPOD", 5_000_000_000, 0, "msdos", profile)
        self.assertIn("IPOD · Dual Boot iPod", label)


class StockIpodUI(unittest.TestCase):
    def test_composite_playlist_delete_routes_through_unified_zero_plan(self):
        from PySide6.QtWidgets import QMessageBox
        from soundbox.dual_ipod import composite_playlist_entry

        playlist = Playlist("Only", [], keys=[], pid="only")
        row = composite_playlist_entry(
            _win.lib,
            playlist,
            artist_overrides=_win.overrides,
            title_overrides=_win.titles,
        )
        current_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["only"],
            "playlists": {"only": row},
            "files": {},
        }
        old_playlists = _win._sync_playlists
        old_begin = _win._begin_dual_ipod_sync
        events = []
        try:
            _win._sync_playlists = (
                lambda: events.append(("validate",)) or [playlist])
            _win._begin_dual_ipod_sync = (
                lambda dev, playlists, **kwargs:
                events.append(("begin", dev, playlists, kwargs)))
            with (
                mock.patch(
                    "desktop.app.device_manifest.load_composite",
                    return_value=current_manifest,
                ),
                mock.patch.object(
                    QMessageBox,
                    "question",
                    side_effect=lambda *args, **kwargs:
                    events.append(("confirm",)) or QMessageBox.Yes,
                ),
            ):
                _win._begin_composite_playlist_delete(
                    "/Volumes/IPOD", ("only",))
        finally:
            _win._sync_playlists = old_playlists
            _win._begin_dual_ipod_sync = old_begin

        self.assertEqual(
            [event[0] for event in events],
            ["confirm", "validate", "begin"],
        )
        self.assertEqual(events[2][1], "/Volumes/IPOD")
        self.assertEqual(events[2][2], [])
        self.assertTrue(events[2][3]["reconcile"])
        self.assertTrue(events[2][3]["confirmed"])
        self.assertEqual(events[2][3]["action"], "delete")
        self.assertNotIn("expected_playlist_rows", events[2][3])

    def test_composite_playlist_delete_retains_missing_playlist_snapshot(self):
        from PySide6.QtWidgets import QMessageBox

        retained = Playlist(
            "Retained", ["GENRES"], ["one", "two"], pid="retained")
        removed = Playlist("Remove", [], [], pid="remove")
        current_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["retained", "remove"],
            "playlists": {
                "retained": {
                    "pid": "retained",
                    "name": "Retained",
                    "folder_path": ["GENRES"],
                    "tracks": ["one", "two"],
                },
                "remove": {
                    "pid": "remove",
                    "name": "Remove",
                    "folder_path": [],
                    "tracks": [],
                },
            },
            "files": {},
        }
        old_playlists = _win._sync_playlists
        old_begin = _win._begin_dual_ipod_sync
        events = []
        try:
            # The retained source playlist disappeared after the last commit.
            # Explicit Delete must preserve its committed snapshot instead of
            # forcing a separate Sync or deleting that unrelated identity.
            _win._sync_playlists = lambda: [removed]
            _win._begin_dual_ipod_sync = (
                lambda dev, playlists, **kwargs:
                events.append((dev, playlists, kwargs)))
            with (
                mock.patch(
                    "desktop.app.device_manifest.load_composite",
                    return_value=current_manifest,
                ),
                mock.patch.object(
                    QMessageBox, "question", return_value=QMessageBox.Yes),
            ):
                _win._begin_composite_playlist_delete(
                    "/Volumes/IPOD", ("remove",))
        finally:
            _win._sync_playlists = old_playlists
            _win._begin_dual_ipod_sync = old_begin

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "/Volumes/IPOD")
        self.assertEqual(events[0][1], [retained])
        self.assertTrue(events[0][2]["reconcile"])
        self.assertEqual(events[0][2]["action"], "delete")
        self.assertNotIn("expected_playlist_rows", events[0][2])

    def test_device_operation_waits_for_inflight_refresh(self):
        class FakeSignal:
            def __init__(self):
                self.slots = []

            def connect(self, slot):
                self.slots.append(slot)

            def emit(self):
                for slot in tuple(self.slots):
                    slot()

        class FakeWorker:
            def __init__(self):
                self.running = True
                self.finished = FakeSignal()

            def isRunning(self):
                return self.running

        old_worker = _win._dev_scan_worker
        old_pending = _win._pending_device_operation
        old_exporting = _win._exporting
        worker = FakeWorker()
        resumed = []
        try:
            _win._dev_scan_worker = worker
            _win._pending_device_operation = None
            _win._exporting = False
            self.assertTrue(
                _win._defer_device_operation_until_scan_finishes(
                    lambda: resumed.append("started")))
            self.assertTrue(_win._exporting)
            self.assertEqual(resumed, [])

            worker.running = False
            worker.finished.emit()
            self.assertFalse(_win._exporting)
            self.assertEqual(resumed, ["started"])
        finally:
            _win._stop_pulse()
            _win._dev_scan_worker = old_worker
            _win._pending_device_operation = old_pending
            _win._exporting = old_exporting

    def test_stock_device_header_has_requested_tabs(self):
        self.assertEqual(
            [button.text() for button in _win.device_tab_buttons],
            ["Device", "Songs", "Playlists"],
        )
        for button in _win.device_tab_buttons:
            button.setVisible(True)
        try:
            _win.device_tab_buttons[2].setChecked(True)
            self.assertTrue(_win.device_tab_buttons[2].isChecked())
            self.assertEqual(_win.stock_ipod_pages.stack.currentIndex(), 2)
        finally:
            for button in _win.device_tab_buttons:
                button.setVisible(False)

    def test_device_header_sync_is_always_available(self):
        old_sync = _win._sync_device
        old_current = _win._current_device_path
        calls = []
        try:
            _win._current_device_path = "/Volumes/IPOD"
            _win._sync_device = calls.append
            _win.device_sync_btn.click()
        finally:
            _win._sync_device = old_sync
            _win._current_device_path = old_current
        self.assertEqual(calls, ["/Volumes/IPOD"])
        try:
            _win._start_pulse("Syncing iPod")
            self.assertGreaterEqual(
                _win.status_label.width(),
                _win.status_label.fontMetrics().horizontalAdvance(
                    "Syncing iPod..."),
            )
            self.assertLess(
                _win.status_label.width(),
                _win.status_label.fontMetrics().horizontalAdvance(
                    "iPod playlist deletion cancelled — nothing written"),
            )
        finally:
            _win._stop_pulse()
            _win.status_label.setText("Ready")
        self.assertGreaterEqual(
            _win.status_count_label.width(),
            _win.status_count_label.fontMetrics().horizontalAdvance(
                "9999/9999"),
        )
        self.assertEqual(_win.progress.width(), 208)

    def test_ipod_progress_keeps_animated_status_and_separate_count(self):
        try:
            _win._on_dual_ipod_progress(14, 37, "track.wav")
            self.assertEqual(_win.status_label.text(), "Syncing iPod")
            self.assertEqual(_win.status_count_label.text(), "14/37")
            self.assertFalse(_win.status_count_label.isHidden())
            self.assertTrue(_win._pulse_timer.isActive())
            _win._pulse_tick()
            self.assertEqual(_win.status_label.text(), "Syncing iPod.")
            self.assertEqual(_win.status_count_label.text(), "14/37")
        finally:
            _win._stop_pulse()
            _win._set_status_count()
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_ipod_plan_and_commit_share_one_monotonic_bar(self):
        old_preconfirmed = _win._dual_active_preconfirmed
        old_value = _win.progress.value()
        try:
            _win._dual_active_preconfirmed = False
            _win.progress.setRange(0, 1000)
            _win.progress.setValue(0)

            _win._on_dual_ipod_plan_progress(2, 4, "planning")
            planned_value = _win.progress.value()
            self.assertEqual(planned_value, 150)
            self.assertEqual(_win.status_label.text(), "Planning iPod")

            _win._on_dual_ipod_progress(1, 4, "writing")
            self.assertGreater(_win.progress.value(), planned_value)
            self.assertEqual(_win.status_label.text(), "Syncing iPod")
        finally:
            _win._dual_active_preconfirmed = old_preconfirmed
            _win._stop_pulse()
            _win._set_status_count()
            _win.progress.setValue(old_value)
            _win.progress.setVisible(False)
            _win.status_label.setText("Ready")

    def test_success_refreshes_open_ipod_before_sidebar_scan(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_current = _win._current_device_path
        old_profile = _win._device_profiles.get(dev)
        old_reader = _win._read_stock_device_view
        old_refresh = _win._refresh_devices
        old_exporting = _win._exporting
        events = []

        def read(device, profile, *, after_finished=None):
            events.append(("snapshot", device, profile))
            self.assertTrue(_win._exporting)
            self.assertIsNotNone(after_finished)
            after_finished()

        try:
            profile = SimpleNamespace(has_stock_ipod=True)
            _win._current_device_path = dev
            _win._device_profiles[dev] = profile
            _win._read_stock_device_view = read
            _win._refresh_devices = lambda: events.append(("sidebar",))

            _win._refresh_device_surfaces(dev)

            self.assertEqual(events, [
                ("snapshot", dev, profile),
                ("sidebar",),
            ])
            self.assertFalse(_win._exporting)
        finally:
            _win._current_device_path = old_current
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile
            _win._read_stock_device_view = old_reader
            _win._refresh_devices = old_refresh
            _win._exporting = old_exporting

    def test_failed_refresh_keeps_last_verified_ipod_snapshot(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_current = _win._current_device_path
        old_snapshot = _win._stock_snapshot
        old_set_error = _win.stock_ipod_pages.set_error
        old_flash = _win._flash_status
        calls = []
        snapshot = SimpleNamespace(target=Path(dev))
        try:
            _win._current_device_path = dev
            _win._stock_snapshot = snapshot
            _win.stock_ipod_pages.set_error = (
                lambda message, **kwargs: calls.append((message, kwargs)))
            _win._flash_status = lambda message: calls.append(("flash", message))

            _win._on_stock_device_view(dev, RuntimeError("temporary read error"))

            self.assertIs(_win._stock_snapshot, snapshot)
            self.assertEqual(calls[0], (
                "temporary read error", {"preserve_snapshot": True}))
            self.assertEqual(calls[1], (
                "flash", "iPod refresh failed — showing last verified state"))
        finally:
            _win._current_device_path = old_current
            _win._stock_snapshot = old_snapshot
            _win.stock_ipod_pages.set_error = old_set_error
            _win._flash_status = old_flash

    def test_dual_boot_device_click_opens_stock_dashboard_tabs(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_reader = _win._read_stock_device_view
        old_current = _win._current_device_path
        calls = []

        class _Item:
            @staticmethod
            def data(_column, _role):
                return dev

        _win._device_profiles[dev] = SimpleNamespace(
            kind="dual_boot", has_stock_ipod=True)
        _win._read_stock_device_view = (
            lambda device, profile: calls.append((device, profile)))
        try:
            _win._on_device_click(_Item(), 0)
            self.assertEqual(_win.device_content_stack.currentIndex(), 1)
            self.assertTrue(all(
                not button.isHidden() for button in _win.device_tab_buttons))
            self.assertEqual(calls[0][0], dev)
        finally:
            _win._read_stock_device_view = old_reader
            _win._current_device_path = old_current
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_stock_device_pages_render_snapshot(self):
        from soundbox.stock_ipod_info import (
            StockIpodPlaylistEntry, StockIpodPlaylistSnapshot,
            StockIpodSnapshot, StockIpodTrackSnapshot,
        )
        track = StockIpodTrackSnapshot(
            1, 2, "More Hype", "GRiZ", "", "", "WAV", 14_515_286,
            82_286, 1411, 44_100, 1, 0, 0, 0, 0,
            ":iPod_Control:Music:F05:SDMM.wav", 0, 0)
        huge_playlist_id = 10_469_178_251_650_046_358
        playlist = StockIpodPlaylistSnapshot(
            huge_playlist_id, "testinggggg", "Playlist", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        second_playlist_id = 8_532_857_218_490_441_969
        second_playlist = StockIpodPlaylistSnapshot(
            second_playlist_id, "House", "Playlist", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        library_root = StockIpodPlaylistSnapshot(
            1, "iPod", "Library", False,
            (StockIpodPlaylistEntry(2, "More Hype", "GRiZ"),))
        smart_playlist = StockIpodPlaylistSnapshot(
            2, "Music", "Smart playlist", True, ())
        apple_regular_alias = StockIpodPlaylistSnapshot(
            4, "Videos", "Playlist", False, ())
        apple_smart_playlist = StockIpodPlaylistSnapshot(
            5, "Videos", "Smart playlist", True, ())
        snapshot = StockIpodSnapshot(
            Path("/Volumes/IPOD"), "IPOD", "UUID", "MS-DOS (FAT32)",
            "USB", "disk4s2", True, 256_000_000_000, 255_000_000_000,
            "MA446", "serial suffix V9M", "iPod Video",
            "5.5th generation", "identity", 4, 5, 0x75, 0, "abc", 14_515_286,
            0, "No pending playback records", 2, (track,),
            (library_root, playlist, second_playlist, smart_playlist,
             apple_regular_alias, apple_smart_playlist),
            serial_number="8M650V0HV9M",
            serial_source="macOS iPod device record",
            firmware_version="1.3",
            firmware_source="macOS iPod device record",
            transport_revision="1.62",
            dac_name="Wolfson WM8758BG",
            dac_source="verified MA446 hardware catalog",
            sync_state="local_changes",
            sync_detail="1 curated cover changed",
            sync_local_changes=1,
            sync_committed_at="2026-08-01T03:55:00-05:00",
        )
        _win.stock_ipod_pages.set_snapshot(snapshot)
        pages = _win.stock_ipod_pages
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.music_model.index(0, 1).data(), "GRiZ")
        self.assertEqual(
            [name for name, _getter in pages.music_model.COLUMNS],
            ["Title", "Artist", "Format", "Size", "Time", "Plays", "Skips", "Type"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(0, 0).widget(),
            pages.detail_trees["Identity"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(0, 1).widget(),
            pages.detail_trees["Storage"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(1, 0).widget(),
            pages.detail_trees["Database"],
        )
        self.assertIs(
            pages.detail_layout.itemAtPosition(1, 1).widget(),
            pages.detail_trees["SoundBox"],
        )
        self.assertEqual(
            pages.detail_trees["Identity"].topLevelItem(0).text(0), "Name")
        self.assertEqual(
            pages.detail_trees["Storage"].topLevelItem(0).text(0), "Capacity")
        for tree in pages.detail_trees.values():
            for index in range(tree.topLevelItemCount()):
                self.assertEqual(tree.topLevelItem(index).toolTip(1), "")
        identity = {
            pages.detail_trees["Identity"].topLevelItem(index).text(0):
            pages.detail_trees["Identity"].topLevelItem(index).text(1)
            for index in range(pages.detail_trees["Identity"].topLevelItemCount())
        }
        self.assertEqual(identity["Model"], "MA446")
        self.assertEqual(identity["Firmware"], "1.3")
        self.assertEqual(identity["DAC / codec"], "Wolfson WM8758BG")
        self.assertEqual(identity["Serial number"], "8M650V0HV9M")
        self.assertNotIn("serial suffix", " ".join(identity.values()).casefold())
        self.assertNotIn("macos ipod device record", " ".join(
            identity.values()).casefold())
        self.assertIn("SCSI transport 1.62", identity["Connection"])
        soundbox = {
            pages.detail_trees["SoundBox"].topLevelItem(index).text(0):
            pages.detail_trees["SoundBox"].topLevelItem(index).text(1)
            for index in range(pages.detail_trees["SoundBox"].topLevelItemCount())
        }
        self.assertEqual(soundbox["Sync state"], "Local changes ready")
        self.assertEqual(soundbox["Sync detail"], "1 curated cover changed")
        self.assertEqual(soundbox["Pending local changes"], "1")
        self.assertEqual(
            soundbox["Last committed"], "2026-08-01T03:55:00-05:00")
        pages.set_page(2)
        playlist_count = pages.playlist_tree.topLevelItemCount()
        identity_count = pages.detail_trees["Identity"].topLevelItemCount()
        pages.set_loading(preserve_page=True)
        self.assertEqual(pages.stack.currentIndex(), 2)
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.playlist_tree.topLevelItemCount(), playlist_count)
        self.assertEqual(
            pages.detail_trees["Identity"].topLevelItemCount(), identity_count)
        pages.set_error("temporary refresh failure", preserve_snapshot=True)
        self.assertEqual(pages.music_model.rowCount(), 1)
        self.assertEqual(
            pages.playlist_tree.topLevelItemCount(), playlist_count)
        from desktop.stock_device_view import CompositeDeviceData, StockIpodPages
        composite_manifest = {
            "schema": 3,
            "profile": device_manifest.COMPOSITE_EXPORT_PROFILE,
            "playlist_order": ["managed", "rockbox"],
            "playlists": {
                "managed": {
                    "name": "testinggggg",
                    "folder_path": [],
                    "tracks": ["safe", "unsafe"],
                },
                "rockbox": {
                    "name": "Rockbox Only",
                    "folder_path": [],
                    "tracks": ["unsafe"],
                },
            },
            "files": {
                "/iPod_Control/Music/F05/SDMM.wav": {
                    "kind": "shared-stock-media",
                    "owned": True,
                    "dbid": 2,
                    "title": "More Hype",
                    "artist": "GRiZ",
                    "size": 14_515_286,
                },
                "/Music/Fixture/Unsafe/Unsafe.wav": {
                    "kind": "rockbox-media",
                    "owned": True,
                    "title": "Unsafe",
                    "artist": "Fixture",
                    "size": 1_024,
                },
            },
        }
        dual = StockIpodPages()
        dual.set_snapshot(
            snapshot,
            CompositeDeviceData(composite_manifest, {
                "managed": (
                    "/iPod_Control/Music/F05/SDMM.wav",
                    "/Music/Fixture/Unsafe/Unsafe.wav",
                ),
                "rockbox": ("/Music/Fixture/Unsafe/Unsafe.wav",),
            }, True, 9),
        )
        dual_database = {
            dual.detail_trees["Database"].topLevelItem(index).text(0):
            dual.detail_trees["Database"].topLevelItem(index).text(1)
            for index in range(
                dual.detail_trees["Database"].topLevelItemCount())
        }
        self.assertEqual(
            dual.detail_trees["Database"].headerItem().text(0),
            "DUAL LIBRARY",
        )
        self.assertEqual(dual_database["Managed tracks"], "2")
        self.assertEqual(dual_database["Stock-visible tracks"], "1")
        self.assertEqual(dual_database["Rockbox-only tracks"], "1")
        self.assertEqual(
            dual_database["Rockbox database"],
            "Auto update on · 9 cache files",
        )
        self.assertNotIn("Stock smart playlists", dual_database)
        self.assertEqual(dual.music_model.rowCount(), 2)
        dual_types = {
            dual.music_model.index(row, 7).data()
            for row in range(dual.music_model.rowCount())
        }
        self.assertEqual(dual_types, {"Both", "Rockbox"})
        self.assertEqual(
            [dual.playlist_tree.topLevelItem(row).text(2)
             for row in range(dual.playlist_tree.topLevelItemCount())],
            ["Both", "Rockbox", "Stock"],
        )
        self.assertEqual(
            dual.playlist_tree.topLevelItem(0).child(1).text(2), "Rockbox")
        self.assertEqual(
            dual.playlist_tree.topLevelItem(0).data(0, Qt.UserRole),
            "managed:managed",
        )

        pending = StockIpodPages()
        pending.set_snapshot(
            snapshot,
            CompositeDeviceData(
                composite_manifest,
                {"managed": (
                    "/iPod_Control/Music/F05/SDMM.wav",
                    "/Music/Fixture/Unsafe/Unsafe.wav",
                )},
                True,
                9,
                True,
            ),
        )
        pending_database = {
            pending.detail_trees["Database"].topLevelItem(index).text(0):
            pending.detail_trees["Database"].topLevelItem(index).text(1)
            for index in range(
                pending.detail_trees["Database"].topLevelItemCount())
        }
        self.assertEqual(
            pending_database["Rockbox database"],
            "Auto update on · Rockbox restart pending",
        )
        dual_calls = []
        dual.delete_playlists_requested.connect(dual_calls.append)
        dual.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        dual.delete_playlist_button.click()
        self.assertEqual(
            dual_calls, [(('managed:managed', 'testinggggg'),)])
        self.assertFalse(
            bool(dual.playlist_tree.topLevelItem(2).flags()
                 & Qt.ItemIsUserCheckable))
        order_calls = []
        dual.playlist_order_requested.connect(order_calls.append)
        dual.playlist_tree.setCurrentItem(
            dual.playlist_tree.topLevelItem(1))
        self.assertTrue(dual.move_playlist_up_button.isEnabled())
        dual.move_playlist_up_button.click()
        self.assertEqual(
            [dual.playlist_tree.topLevelItem(index).data(0, Qt.UserRole)
             for index in range(2)],
            ["managed:rockbox", "managed:managed"],
        )
        self.assertTrue(dual.apply_playlist_order_button.isEnabled())
        dual.apply_playlist_order_button.click()
        self.assertEqual(order_calls, [("rockbox", "managed")])
        dual.close()
        self.assertEqual(pages.playlist_tree.columnCount(), 3)
        self.assertEqual(
            [pages.playlist_tree.headerItem().text(index) for index in range(3)],
            ["Playlist / Track", "Tracks", "Type"],
        )
        self.assertEqual(pages.playlist_tree.topLevelItemCount(), 3)
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).text(0),
            "testinggggg")
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).data(0, Qt.UserRole),
            str(huge_playlist_id))
        self.assertEqual(
            pages.playlist_tree.topLevelItem(0).child(0).text(0),
            "1. More Hype")
        self.assertEqual(pages.playlist_tree.topLevelItem(2).text(0), "Videos")
        self.assertEqual(
            pages.playlist_tree.topLevelItem(2).text(2), "System playlist")
        standalone = StockIpodPages()
        calls = []
        standalone.delete_playlists_requested.connect(calls.append)
        standalone.set_snapshot(snapshot)
        self.assertFalse(standalone.delete_playlist_button.isEnabled())
        standalone.playlist_tree.setCurrentItem(
            standalone.playlist_tree.topLevelItem(0).child(0))
        self.assertFalse(standalone.delete_playlist_button.isEnabled())
        standalone.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertTrue(standalone.delete_playlist_button.isEnabled())
        self.assertEqual(
            standalone.delete_playlist_button.text(),
            "Delete checked playlist…",
        )
        standalone.playlist_tree.topLevelItem(1).setCheckState(0, Qt.Checked)
        self.assertEqual(
            standalone.delete_playlist_button.text(),
            "Delete 2 checked playlists…",
        )
        standalone.delete_playlist_button.click()
        self.assertEqual(calls, [(
            (str(huge_playlist_id), "testinggggg"),
            (str(second_playlist_id), "House"),
        )])
        standalone.playlist_tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)
        standalone.playlist_tree.topLevelItem(1).setCheckState(0, Qt.Unchecked)
        standalone.playlist_tree.topLevelItem(2).setCheckState(0, Qt.Checked)
        standalone.delete_playlist_button.click()
        self.assertEqual(calls[-1], (("5", "Videos"),))
        standalone.set_page(2)
        standalone.set_loading(preserve_page=True)
        self.assertEqual(standalone.stack.currentIndex(), 2)
        standalone.set_snapshot(snapshot)
        self.assertEqual(standalone.stack.currentIndex(), 2)
        standalone.close()
        pages._music_widths_applied = False
        pages.set_page(1)
        _app.processEvents()
        from PySide6.QtWidgets import QHeaderView
        self.assertEqual(
            pages.music_table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.Stretch,
        )
        self.assertEqual(
            pages.music_table.columnWidth(1), pages.MUSIC_FIXED_COLUMN_WIDTHS[0])
        pages.set_page(0)

    def test_confirmed_model_is_scoped_to_stable_device_identity(self):
        from types import SimpleNamespace
        from unittest import mock
        from soundbox.device_profile import DeviceProfile

        profile = DeviceProfile(
            "stock_ipod", "Stock iPod", "msdos", False, True,
            False, True, False, "", 0x400B739988A917E1)
        old = _win.settings.get("stock_ipod_models")
        try:
            with mock.patch(
                    "desktop.app.device_facts",
                    return_value=SimpleNamespace(volume_uuid="FIXTURE-UUID")):
                key = _win._stock_device_key("/Volumes/IPOD", profile)
                _win._remember_stock_model(key, "MA446LL/A")
                self.assertEqual(
                    _win._stored_stock_model("/Volumes/IPOD", profile), "MA446")
                self.assertIn("FIXTURE-UUID", key)
                self.assertNotIn(str(profile.library_persistent_id), key)
        finally:
            _win.settings["stock_ipod_models"] = old if isinstance(old, dict) else {}

    def test_drop_routes_selected_playlist_to_two_phase_stock_flow(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        calls = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_stock_ipod_sync
        _win._device_profiles[dev] = SimpleNamespace(
            kind="stock_ipod", stock_preview=True, rockbox_export=False)
        _win._begin_stock_ipod_sync = (
            lambda device, playlists: calls.append((device, playlists)))
        try:
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            self.assertEqual(calls[0][0], dev)
            self.assertEqual([p.display_path for p in calls[0][1]],
                             ["CRATES / SET ONE"])
        finally:
            _win._begin_stock_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_drop_routes_selected_playlist_to_composite_dual_flow(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        calls = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        _win._device_profiles[dev] = SimpleNamespace(
            kind="dual_boot", stock_preview=True, rockbox_export=True)
        _win._begin_dual_ipod_sync = (
            lambda device, playlists, **kwargs:
            calls.append((device, playlists, kwargs)))
        _win._confirm_dual_ipod_drop = lambda _device, _playlist: True
        try:
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            self.assertEqual(calls[0][0], dev)
            self.assertEqual([p.display_path for p in calls[0][1]],
                             ["CRATES / SET ONE"])
            self.assertTrue(calls[0][2]["preconfirmed"])
        finally:
            _win._begin_dual_ipod_sync = real
            _win._confirm_dual_ipod_drop = real_confirm
            _win._reset_dual_batch()
            _win._dual_sync_queue.pop(dev, None)
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_additional_dual_drops_queue_while_sync_is_running(self):
        from types import SimpleNamespace

        class RunningWorker:
            def isRunning(self):
                return True

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_worker = _win._dual_plan_worker
        old_device = _win._dual_active_device
        old_keys = set(_win._dual_active_playlist_keys)
        old_queue = dict(_win._dual_sync_queue)
        old_batch_keys = set(_win._dual_batch_keys)
        old_batch_names = list(_win._dual_batch_names)
        old_batch_total = _win._dual_batch_total_tracks
        real_confirm = _win._confirm_dual_ipod_drop
        real = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(
                kind="dual_boot", stock_preview=True, rockbox_export=True)
            _win._dual_plan_worker = RunningWorker()
            _win._dual_active_device = dev
            _win._dual_active_playlist_keys.clear()
            _win._dual_sync_queue.clear()
            _win._dual_batch_keys.clear()
            _win._dual_batch_names.clear()
            _win._dual_batch_total_tracks = 0
            _win._begin_dual_ipod_sync = (
                lambda device, playlists: calls.append((device, playlists)))
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: True

            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])
            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])

            self.assertEqual(calls, [])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                ["CRATES / SET ONE"],
            )
        finally:
            _win._begin_dual_ipod_sync = real
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_plan_worker = old_worker
            _win._dual_active_device = old_device
            _win._dual_active_playlist_keys = old_keys
            _win._dual_sync_queue = old_queue
            _win._dual_batch_keys = old_batch_keys
            _win._dual_batch_names = old_batch_names
            _win._dual_batch_total_tracks = old_batch_total
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_drop_queues_while_first_job_waits_for_device_scan(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        old_pending = _win._pending_device_operation
        old_queue = dict(_win._dual_sync_queue)
        real_confirm = _win._confirm_dual_ipod_drop
        real_begin = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            first = next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == "CRATES / SET ONE")
            _win._dual_batch_keys = {device_manifest.playlist_key(first)}
            _win._dual_batch_names = [first.display_path]
            _win._dual_batch_total_tracks = len(_win.lib.resolve(first))
            _win._pending_device_operation = lambda: None
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: True
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))

            _win._on_device_drop(dev, [("rb", "SET TWO")])

            self.assertEqual(calls, [])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                ["SET TWO"],
            )
            self.assertEqual(_win._dual_batch_total_tracks, 3)
        finally:
            _win._confirm_dual_ipod_drop = real_confirm
            _win._begin_dual_ipod_sync = real_begin
            _win._pending_device_operation = old_pending
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_confirmed_multi_drop_starts_one_job_and_queues_the_rest(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real_begin = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        old_queue = dict(_win._dual_sync_queue)
        calls = []
        confirmations = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            _win._confirm_dual_ipod_drop = (
                lambda _device, playlist:
                confirmations.append(playlist.display_path) or True)
            _win._begin_dual_ipod_sync = (
                lambda device, playlists, **kwargs:
                calls.append((device, playlists, kwargs)))

            _win._on_device_drop(dev, [
                ("rb", "CRATES / SET ONE"),
                ("rb", "SET TWO"),
            ])

            self.assertEqual(
                confirmations, ["CRATES / SET ONE", "SET TWO"])
            self.assertEqual(
                [item.display_path for item in calls[0][1]],
                ["CRATES / SET ONE"],
            )
            self.assertTrue(calls[0][2]["preconfirmed"])
            self.assertEqual(
                [item.display_path for item in _win._dual_sync_queue[dev]],
                ["SET TWO"],
            )
            self.assertEqual(_win._dual_batch_total_tracks, 3)
        finally:
            _win._begin_dual_ipod_sync = real_begin
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_declined_dual_drop_never_plans_or_queues(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real_begin = _win._begin_dual_ipod_sync
        real_confirm = _win._confirm_dual_ipod_drop
        old_queue = dict(_win._dual_sync_queue)
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._dual_sync_queue.clear()
            _win._reset_dual_batch()
            _win._confirm_dual_ipod_drop = lambda _device, _playlist: False
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))

            _win._on_device_drop(dev, [("rb", "CRATES / SET ONE")])

            self.assertEqual(calls, [])
            self.assertNotIn(dev, _win._dual_sync_queue)
            self.assertEqual(_win._dual_batch_total_tracks, 0)
        finally:
            _win._begin_dual_ipod_sync = real_begin
            _win._confirm_dual_ipod_drop = real_confirm
            _win._dual_sync_queue = old_queue
            _win._reset_dual_batch()
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_confirmed_dual_job_waits_for_queue_before_completion_dialog(self):
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        old_next = _win._start_next_confirmed_dual_job
        old_clear = _win.rockbox_media_cache.clear
        next_calls = []
        try:
            _win._reset_dual_batch()
            _win._dual_batch_names = ["ONE", "TWO"]
            _win._dual_batch_total_tracks = 5
            _win._dual_batch_done_tracks = 1
            _win._dual_active_preconfirmed = True
            _win._dual_active_track_total = 2
            _win._dual_active_device = dev
            _win._start_next_confirmed_dual_job = next_calls.append
            _win.rockbox_media_cache.clear = lambda: None
            result = SimpleNamespace()
            plan = SimpleNamespace()

            with mock.patch.object(QMessageBox, "information") as information:
                _win._on_dual_ipod_commit(dev, result, plan)

            information.assert_not_called()
            self.assertEqual(next_calls, [dev])
            self.assertEqual(_win._dual_batch_done_tracks, 3)
            self.assertEqual(_win._dual_batch_results, [(plan, result)])
        finally:
            _win._start_next_confirmed_dual_job = old_next
            _win.rockbox_media_cache.clear = old_clear
            _win._dual_sync_queue.pop(dev, None)
            _win._dual_active_device = ""
            _win._dual_active_playlist_keys.clear()
            _win._dual_active_preconfirmed = False
            _win._dual_active_track_total = 0
            _win._reset_dual_batch()

    def test_next_confirmed_dual_job_coalesces_every_waiting_playlist(self):
        from unittest import mock
        from PySide6.QtCore import QTimer

        dev = "/Volumes/IPOD"
        playlists = [
            next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == display_path)
            for display_path in ("CRATES / SET ONE", "SET TWO")
        ]
        old_begin = _win._begin_dual_ipod_sync
        old_plan_worker = _win._dual_plan_worker
        old_commit_worker = _win._dual_commit_worker
        calls = []
        try:
            _win._dual_plan_worker = None
            _win._dual_commit_worker = None
            _win._dual_sync_queue[dev] = list(playlists)
            _win._begin_dual_ipod_sync = (
                lambda device, selected, **kwargs:
                calls.append((device, list(selected), kwargs)))

            with mock.patch.object(
                    QTimer, "singleShot",
                    side_effect=lambda _delay, callback: callback()):
                _win._start_next_confirmed_dual_job(dev)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], dev)
            self.assertEqual(
                [playlist.display_path for playlist in calls[0][1]],
                ["CRATES / SET ONE", "SET TWO"],
            )
            self.assertTrue(calls[0][2]["preconfirmed"])
            self.assertEqual(_win._dual_sync_queue[dev], [])
        finally:
            _win._begin_dual_ipod_sync = old_begin
            _win._dual_plan_worker = old_plan_worker
            _win._dual_commit_worker = old_commit_worker
            _win._dual_sync_queue.pop(dev, None)
            _win._reset_dual_batch()

    def test_drained_dual_queue_shows_one_combined_summary(self):
        from types import SimpleNamespace
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        old_refresh = _win._refresh_device_surfaces
        old_flash = _win._flash_status
        refresh_calls = []
        flash_calls = []
        try:
            _win._reset_dual_batch()
            _win._dual_batch_names = ["ONE", "TWO"]
            _win._dual_batch_total_tracks = 5
            _win._dual_batch_done_tracks = 5
            result_one = SimpleNamespace(
                stock=SimpleNamespace(copied_tracks=2, rewritten_tracks=1),
                copied_media=3,
                deleted_orphans=0,
            )
            result_two = SimpleNamespace(
                stock=SimpleNamespace(copied_tracks=0, rewritten_tracks=0),
                copied_media=0,
                deleted_orphans=2,
            )
            _win._dual_batch_results = [
                (SimpleNamespace(), result_one),
                (SimpleNamespace(), result_two),
            ]
            _win._refresh_device_surfaces = refresh_calls.append
            _win._flash_status = flash_calls.append

            with mock.patch.object(QMessageBox, "information") as information:
                _win._start_next_confirmed_dual_job(dev)

            information.assert_called_once()
            detail = information.call_args.args[2]
            self.assertIn("2 playlist jobs complete", detail)
            self.assertIn("5 queued track entries", detail)
            self.assertEqual(refresh_calls, [dev])
            self.assertEqual(flash_calls, ["iPod synced"])
            self.assertEqual(_win._dual_batch_total_tracks, 0)
        finally:
            _win._refresh_device_surfaces = old_refresh
            _win._flash_status = old_flash
            _win._dual_sync_queue.pop(dev, None)
            _win._reset_dual_batch()

    def test_sync_routes_managed_dual_playlists_to_composite_reconcile(self):
        from types import SimpleNamespace
        from unittest import mock
        from soundbox import manifest as device_manifest

        dev = "/Volumes/IPOD"
        events = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
        _win._begin_dual_ipod_sync = (
            lambda device, playlists, **kwargs:
            events.append(("begin", device, playlists, kwargs)))
        payload = {
            "playlist_order": ["cust:SET ONE"],
            "playlists": {"cust:SET ONE": {}},
        }
        try:
            with (
                mock.patch.object(
                    _win, "_confirm_ipod_sync",
                    side_effect=lambda _dev: events.append(("confirm",)) or True),
                mock.patch.object(
                    device_manifest,
                    "load_composite",
                    side_effect=lambda _dev: events.append(("load",)) or payload),
            ):
                _win._sync_device(dev)
            self.assertEqual(
                [event[0] for event in events],
                ["confirm", "load", "begin"],
            )
            begin = events[2]
            self.assertEqual(begin[1], dev)
            self.assertEqual([p.display_path for p in begin[2]],
                             ["CRATES / SET ONE"])
            self.assertTrue(begin[3]["reconcile"])
            self.assertTrue(begin[3]["confirmed"])
            self.assertEqual(begin[3]["action"], "sync")
        finally:
            _win._begin_dual_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_device_playlist_order_routes_to_ordered_composite_reconcile(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QMessageBox

        dev = "/Volumes/IPOD"
        playlists = [
            next(
                playlist for playlist in _win.lib.playlists
                if playlist.display_path == display_path)
            for display_path in ("CRATES / SET ONE", "SET TWO")
        ]
        keys = [device_manifest.playlist_key(item) for item in playlists]
        manifest = {
            "playlist_order": list(reversed(keys)),
            "playlists": {key: {} for key in keys},
        }
        old_current = _win._current_device_path
        old_profile = _win._device_profiles.get(dev)
        old_begin = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._current_device_path = dev
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._begin_dual_ipod_sync = (
                lambda device, selected, **kwargs:
                calls.append((device, list(selected), kwargs)))
            with (
                mock.patch.object(
                    device_manifest, "load_composite", return_value=manifest),
                mock.patch.object(
                    QMessageBox, "question", return_value=QMessageBox.Yes),
            ):
                _win._apply_composite_playlist_order(tuple(reversed(keys)))

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], dev)
            self.assertEqual(
                [device_manifest.playlist_key(item) for item in calls[0][1]],
                list(reversed(keys)),
            )
            self.assertTrue(calls[0][2]["reconcile"])
            self.assertTrue(calls[0][2]["respect_selected_order"])
            self.assertTrue(calls[0][2]["confirmed"])
            self.assertEqual(calls[0][2]["action"], "order")
        finally:
            _win._current_device_path = old_current
            _win._begin_dual_ipod_sync = old_begin
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_declined_dual_sync_does_not_start_planning(self):
        from types import SimpleNamespace

        dev = "/Volumes/IPOD"
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_dual_ipod_sync
        calls = []
        try:
            _win._device_profiles[dev] = SimpleNamespace(kind="dual_boot")
            _win._begin_dual_ipod_sync = (
                lambda *args, **kwargs: calls.append((args, kwargs)))
            with (
                mock.patch.object(_win, "_confirm_ipod_sync", return_value=False),
                mock.patch.object(
                    device_manifest, "load_composite") as load_composite,
            ):
                _win._sync_device(dev)
            self.assertEqual(calls, [])
            load_composite.assert_not_called()
        finally:
            _win._begin_dual_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_sync_routes_managed_stock_playlists_to_stock_reconcile(self):
        from types import SimpleNamespace
        from unittest import mock

        dev = "/Volumes/IPOD"
        calls = []
        old_profile = _win._device_profiles.get(dev)
        real = _win._begin_stock_ipod_sync
        _win._device_profiles[dev] = SimpleNamespace(kind="stock_ipod")
        _win._begin_stock_ipod_sync = (
            lambda device, playlists, *, reconcile=False:
            calls.append((device, playlists, reconcile)))
        shadow = SimpleNamespace(playlists={"cust:SET ONE": {}})
        try:
            with (
                mock.patch.object(_win, "_stock_device_key", return_value="fixture"),
                mock.patch.object(
                    _win.stock_ipod_shadow_store, "load", return_value=shadow),
            ):
                _win._sync_device(dev)
            self.assertEqual(calls[0][0], dev)
            self.assertEqual([p.display_path for p in calls[0][1]],
                             ["CRATES / SET ONE"])
            self.assertTrue(calls[0][2])
        finally:
            _win._begin_stock_ipod_sync = real
            if old_profile is None:
                _win._device_profiles.pop(dev, None)
            else:
                _win._device_profiles[dev] = old_profile

    def test_device_scan_surfaces_stock_shadow_changes_as_sync_needed(self):
        from types import SimpleNamespace
        from unittest import mock
        from desktop.workers import DeviceScanWorker
        from soundbox.stock_ipod_info import stock_ipod_device_key
        from soundbox.stock_ipod_shadow import StockIpodSyncStatus

        identity = stock_ipod_device_key("UUID", 123)
        profile = SimpleNamespace(
            kind="stock_ipod",
            model="",
            library_persistent_id=123,
        )
        facts = SimpleNamespace(
            volume_uuid="UUID", hardware_serial="SERIAL")
        worker = DeviceScanWorker(
            _win.lib,
            [],
            artist_overrides={"1": "Edited artist"},
            title_overrides={"1": "Edited title"},
            stock_shadow_store=object(),
            stock_models={identity: "MA446"},
        )
        results = []
        worker.done.connect(results.append)
        with (
            mock.patch("desktop.workers.list_devices", return_value=[
                ("IPOD", "/Volumes/IPOD", 1000)]),
            mock.patch("desktop.workers.device_profile", return_value=profile),
            mock.patch("desktop.workers.device_facts", return_value=facts),
            mock.patch(
                "desktop.workers.engine.device_drift",
                return_value=None,
            ) as engine_drift,
            mock.patch(
                "desktop.workers.inspect_stock_ipod_sync_status",
                return_value=StockIpodSyncStatus(
                    "local_changes", "playlist changed", 2)),
        ):
            worker.run()
        self.assertEqual(results[0][0][3], 2)
        drift_call = engine_drift.call_args
        self.assertEqual(
            drift_call.kwargs["artist_overrides"], {"1": "Edited artist"})
        self.assertEqual(
            drift_call.kwargs["title_overrides"], {"1": "Edited title"})

    def test_stock_commit_prompt_defaults_to_cancel(self):
        source = (Path(__file__).parents[1] / "desktop" / "app.py").read_text()
        self.assertIn('"Commit stock iPod sync?"', source)
        self.assertIn("QMessageBox.Cancel,\n        )", source)

    def test_dual_sync_prompt_is_compact_and_defaults_to_cancel(self):
        source = (Path(__file__).parents[1] / "desktop" / "app.py").read_text()
        self.assertIn('"Sync iPod?"', source)
        self.assertIn('"Ready to sync iPod.', source)
        self.assertNotIn(
            "The no-write plan passed its stock and Rockbox checks", source)

    def test_stock_playlist_delete_prompt_is_explicit_and_defaults_to_cancel(self):
        source = (Path(__file__).parents[1] / "desktop" / "app.py").read_text()
        self.assertIn('"Delete stock iPod playlist?"', source)
        self.assertIn("Only songs unused by every remaining user playlist", source)
        self.assertIn("Deleted device media is not retained in recovery", source)
        device_source = (
            Path(__file__).parents[1] / "desktop" / "stock_device_view.py"
        ).read_text()
        self.assertIn("Delete checked playlists…", device_source)
        self.assertIn("Delete this playlist from iPod…", device_source)


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


class TrackRemovalMenu(unittest.TestCase):
    def _menu(self, ids):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(_win)
        _win._add_track_removal_actions(menu, ids)
        return menu

    def tearDown(self):
        _win.custom.pop("MENU TEST", None)
        _win._refresh_custom()
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()

    def test_rekordbox_playlist_offers_both_scopes_and_routes_them(self):
        item = _win._find_tree_item("CRATES / SET ONE")
        _win.tree.setCurrentItem(item)
        _app.processEvents()
        with (
            mock.patch.object(_win, "_remove_from_rb_playlist") as playlist_remove,
            mock.patch.object(_win, "_delete_tracks") as collection_remove,
        ):
            menu = self._menu(["1"])
            actions = menu.actions()
            self.assertEqual([a.text() for a in actions], [
                "Remove from Playlist", "Remove from Collection"])
            actions[0].trigger()
            actions[1].trigger()
            playlist_remove.assert_called_once_with("CRATES / SET ONE", ["1"])
            collection_remove.assert_called_once_with(["1"])

    def test_soundbox_playlist_offers_both_scopes(self):
        from desktop.trees import CUSTOM_ROLE
        _win.custom["MENU TEST"] = ["1", "2"]
        _win._refresh_custom()
        item = next(
            _win.sb_tree.topLevelItem(i)
            for i in range(_win.sb_tree.topLevelItemCount())
            if _win.sb_tree.topLevelItem(i).data(0, CUSTOM_ROLE) == "MENU TEST")
        _win.sb_tree.setCurrentItem(item)
        _app.processEvents()
        self.assertEqual([a.text() for a in self._menu(["1", "2"]).actions()], [
            "Remove 2 tracks from Playlist",
            "Remove 2 tracks from Collection",
        ])

    def test_collection_offers_only_collection_scope(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual([a.text() for a in self._menu(["1"]).actions()], [
            "Remove from Collection"])


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
        it = _win._rules.topLevelItem(0)  # combined mixdown markers
        it.setCheckState(0, Qt.Unchecked)  # fires _on_rule_toggled
        self.assertIn("mixdown", _win.settings["title_rules_off"])
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

    def test_plain_bracket_tag_is_labeled_and_applied_as_exact_drop(self):
        from soundbox.metadata import tidy_title
        _win._word_edit.setText("(Original Mix)")
        _win._add_drop_word()
        labels = [_win._rules.topLevelItem(i).text(0)
                  for i in range(_win._rules.topLevelItemCount())]
        self.assertIn("Drop “(Original Mix)”  (yours)", labels)
        self.assertEqual(tidy_title("LOUD (Original Mix)", _win._title_junk()),
                         "LOUD")
        self.assertEqual(tidy_title("LOUD (Original Mix Extended)",
                                    _win._title_junk()),
                         "LOUD (Original Mix Extended)")


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
    def test_steady_transport_updates_do_no_widget_work(self):
        """The 20 Hz position path must not rebuild unchanged button state.

        Rebuilding it anyway allocated a QPixmap and QIcon per cue slot
        twenty times a second and cost eight display frames per second at
        maximum zoom in the real app -- each one a doubled scroll step that
        reads as a shake. Measured, not theoretical: see docs/PLAYER.md.
        """
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb.resize(900, 140)
            calls = []
            real_refresh = pb.cue_panel._refresh
            pb.cue_panel._refresh = lambda: (
                calls.append(1), real_refresh())[1]
            pb._update_transport()
            calls.clear()
            for _ in range(40):
                pb._update_transport()
            self.assertEqual(
                calls, [],
                "steady transport updates rebuilt the cue panel")
            # A real state change must still land.
            pb._src_path = "/tmp/x.wav"
            pb._playback_available = True
            pb._update_transport()
            self.assertEqual(len(calls), 1)
        finally:
            pb.deleteLater()
            _app.processEvents()

    def test_native_rendering_suppresses_wasted_qt_repaints(self):
        """paintEvent early-returns under Metal, so a repaint draws nothing."""
        from desktop.player import WaveformBar
        wave = WaveformBar()
        try:
            painted = []
            wave.update = lambda *a, **k: painted.append(1)
            wave.set_native_rendering(True)
            painted.clear()
            wave.set_timing(1234.0, 200_000)
            self.assertEqual(
                painted, [], "Qt repainted while Metal owned the pixels")
            wave.set_native_rendering(False)
            painted.clear()
            wave.set_timing(2345.0, 200_000)
            self.assertEqual(len(painted), 1)
        finally:
            wave.deleteLater()
            _app.processEvents()

    def test_cue_pages_align_to_waveform_and_deck_buttons_are_circles(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb.resize(900, 140)
            pb.show()
            _app.processEvents()
            wave_top = pb.wave.mapTo(pb, QPoint(0, 0)).y()
            cue_top = pb.cue_panel.hot_slots[0].mapTo(pb, QPoint(0, 0)).y()
            self.assertLessEqual(abs(cue_top - wave_top), 1)
            self.assertFalse(hasattr(pb, "time"))
            self.assertFalse(hasattr(pb.cue_panel, "time"))
            self.assertEqual(pb.cue_btn.width(), pb.cue_btn.height())
            self.assertEqual(pb.play_btn.width(), pb.play_btn.height())
            self.assertEqual(pb.cue_btn.size(), pb.play_btn.size())
            selector_top = pb.beat_selector_btn.mapTo(
                pb, QPoint(0, 0)).y()
            cue_top = pb.cue_btn.mapTo(pb, QPoint(0, 0)).y()
            self.assertGreaterEqual(
                cue_top - (selector_top + pb.beat_selector_btn.height()), 7)
        finally:
            pb.close()
            pb.shutdown_waveforms()

    def test_cue_panel_has_two_eight_slot_pages_and_emits_gate_edges(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb._playback_available = True
        try:
            self.assertEqual(len(pb.cue_panel.hot_slots), 8)
            self.assertEqual(len(pb.cue_panel.memory_slots), 8)
            self.assertEqual(pb.cue_panel.pages.count(), 2)
            cues = [
                {"kind": "hot", "slot": 0, "position_ms": 5_000,
                 "type": "single", "label": "Intro", "color": [255, 59, 125]},
                {"kind": "memory", "slot": 7, "position_ms": 17_145,
                 "type": "loop", "label": "Mix", "color": [0, 0, 0]},
            ]
            hits = []
            presses = []
            releases = []
            pb.cue_panel.cue_requested.connect(hits.append)
            pb.cue_panel.cue_pressed.connect(
                lambda kind, slot, position:
                presses.append((kind, slot, position)))
            pb.cue_panel.cue_released.connect(
                lambda kind, slot: releases.append((kind, slot)))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.cue_panel.set_cues(cues)
            pb.cue_panel.set_loaded(True)
            pb.cue_panel.hot_slots[0].click()
            pb.cue_panel.memory_tab.click()
            pb.cue_panel.memory_slots[7].click()
            self.assertEqual(pb.cue_panel.pages.currentIndex(), 1)
            self.assertEqual(hits, [5_000, 17_145])
            self.assertEqual(
                presses, [("hot", 0, 5_000), ("memory", 7, 17_145)])
            self.assertEqual(releases, [("hot", 0), ("memory", 7)])
            self.assertIn("Intro", pb.cue_panel.hot_slots[0].toolTip())
            self.assertIn("Loop", pb.cue_panel.memory_slots[7].toolTip())
        finally:
            pb.shutdown_waveforms()

    def test_waveform_cues_use_fixed_slot_color_and_red_memory_identity(self):
        from PySide6.QtGui import QImage
        from desktop.player import WaveformBar

        wave = WaveformBar()
        wave.resize(900, 132)
        wave.set_zoom(64.0)
        wave.set_timing(60_000, 120_000)
        imported_hot = [220, 40, 90]
        slot_hot = (255, 55, 111)
        wave.set_analysis({
            "heights": [0.4] * 180,
            "colors": [[7, 1, 1]] * 180,
            "sample_rate": 150.0,
            "beats": [],
            "cues": [
                {"kind": "hot", "slot": 0, "position_ms": 58_000,
                 "color": imported_hot},
                # A stored non-red color must never leak into memory markers.
                {"kind": "memory", "slot": 0, "position_ms": 62_000,
                 "color": [20, 80, 240]},
            ],
        })
        image = QImage(wave.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        wave.render(image)

        overview_hot_x = round(58_000 / 120_000 * 900)
        overview_box_left = round(overview_hot_x - 9)
        self.assertEqual(
            image.pixelColor(overview_box_left, 1).name(), "#050608")
        self.assertEqual(
            image.pixelColor(overview_box_left + 2, 3).getRgb()[:3],
            slot_hot)
        overview_memory_x = round(62_000 / 120_000 * 900)
        self.assertEqual(
            image.pixelColor(overview_memory_x, 25).getRgb()[:3],
            (255, 52, 52))
        # The slightly larger arrow reaches the lane edge, hiding any
        # coincident red grid/stem instead of leaving a one-pixel sliver.
        self.assertIn(
            image.pixelColor(overview_memory_x, 27).name(),
            ("#050608", "#ff3434"))

        detail_hot_x = 900 // 2 + round((58_000 - 60_000) / 1000 * 64)
        self.assertEqual(
            image.pixelColor(detail_hot_x - 9, 52).getRgb()[:3],
            slot_hot)
        self.assertEqual(
            image.pixelColor(detail_hot_x, 49).name(), "#050608")
        detail_memory_x = (
            900 // 2 + round((62_000 - 60_000) / 1000 * 64))
        self.assertEqual(
            image.pixelColor(detail_memory_x, 129).getRgb()[:3],
            (255, 52, 52))
        self.assertIn(
            image.pixelColor(detail_memory_x, 131).name(),
            ("#050608", "#ff3434"))

    def test_session_cue_is_orange_in_overview_and_detail(self):
        from PySide6.QtGui import QImage
        from desktop.player import WaveformBar

        wave = WaveformBar()
        wave.resize(900, 132)
        wave.set_zoom(64.0)
        wave.set_timing(60_000, 120_000)
        wave.set_analysis({
            "heights": [0.4] * 180,
            "colors": [[7, 1, 1]] * 180,
            "sample_rate": 150.0,
            "beats": [],
            "cues": [],
        })
        wave.set_session_cue(58_000)
        image = QImage(wave.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        wave.render(image)

        overview_x = round(58_000 / 120_000 * 900)
        detail_x = 900 // 2 + round((58_000 - 60_000) / 1000 * 64)
        # Session CUE is arrow-only: the waveform body has no orange stem.
        self.assertNotEqual(
            image.pixelColor(overview_x, 10).name(), "#f08a19")
        self.assertEqual(
            image.pixelColor(overview_x, 22).name(), "#f08a19")
        self.assertNotEqual(
            image.pixelColor(detail_x, 80).name(), "#f08a19")
        self.assertEqual(
            image.pixelColor(detail_x, 125).name(), "#f08a19")
        self.assertIn(
            image.pixelColor(detail_x, 131).name(),
            ("#050608", "#f08a19"))

    def test_hot_cue_a_through_h_palette_is_exact_and_ignores_imported_rgb(self):
        from desktop.native_deck import NativeDeckBackend
        from desktop.player import _HOT_CUE_FALLBACKS

        expected = (
            "#ff376f", "#45acdb", "#7dc13d", "#aa72ff",
            "#30d26e", "#e0641b", "#305aff", "#c3af04",
        )
        self.assertEqual(_HOT_CUE_FALLBACKS, expected)
        imported = {"kind": "hot", "color": [1, 2, 3]}
        self.assertEqual(
            [NativeDeckBackend._cue_rgb(imported, slot)
             for slot in range(8)],
            [
                (255, 55, 111), (69, 172, 219), (125, 193, 61),
                (170, 114, 255), (48, 210, 110), (224, 100, 27),
                (48, 90, 255), (195, 175, 4),
            ],
        )

    def test_hot_and_memory_cues_gate_and_space_latches_playback(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        pb._playback_available = True
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]

            def position(target):
                pb._pos = target
                events.append(("seek", target))

            pb._set_position = position

            def play():
                state["playing"] = True
                events.append(("play",))

            def pause(after=None):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._play_clean = play
            pb._pause_clean = pause
            pb.cue_panel.set_cues([
                {"kind": "hot", "slot": 3, "position_ms": 12_345},
                {"kind": "memory", "slot": 0, "position_ms": 23_456},
            ])
            pb.cue_panel.set_loaded(True)

            pb.cue_panel.hot_slots[3].pressed.emit()
            self.assertTrue(state["playing"])
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
            self.assertEqual(events[-2:], [("seek", 12_345), ("play",)])
            pb.cue_panel.hot_slots[3].released.emit()
            self.assertFalse(state["playing"])
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(events[-2:], [("pause",), ("seek", 12_345)])

            pb.cue_panel.memory_slots[0].pressed.emit()
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
            pb.toggle()  # Space/play while held latches the preview.
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "❚❚")
            pb.cue_panel.memory_slots[0].released.emit()
            self.assertTrue(state["playing"])
            self.assertTrue(pb._cue_preview_latched)
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            pb.toggle()
            self.assertFalse(state["playing"])
            self.assertFalse(pb._cue_preview_latched)
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")

            pb._move_clean(34_567)
            pb.cue_btn.pressed.emit()
            # The first CUE press after a paused manual move only relocates the
            # transient orange marker. It does not start a preview.
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertFalse(state["playing"])
            self.assertEqual(pb.wave._session_cue_ms, 34_567)
            pb.cue_btn.released.emit()
            pb.cue_btn.pressed.emit()
            self.assertTrue(pb.cue_btn.property("active"))
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.wave._session_cue_ms, 34_567)
            pb.toggle()
            pb.cue_btn.released.emit()
            self.assertTrue(state["playing"])
            self.assertEqual(pb._cue_ms, 34_567)
            self.assertTrue(pb._cue_preview_latched)
            self.assertFalse(pb.cue_btn.property("active"))
            self.assertTrue(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "❚❚")
            pb.toggle()
            self.assertFalse(state["playing"])
            self.assertFalse(pb.play_btn.property("playing"))
            self.assertEqual(pb.play_btn.text(), "▶")
        finally:
            pb.shutdown_waveforms()

    def test_cue_press_during_latch_starts_a_new_momentary_gate(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        pb._playback_available = True
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]
            pb._set_position = lambda target: events.append(("seek", target))

            def play():
                state["playing"] = True
                events.append(("play",))

            def pause(after=None):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._play_clean = play
            pb._pause_clean = pause
            pb.cue_panel.set_cues([
                {"kind": "hot", "slot": 0, "position_ms": 1_000},
                {"kind": "hot", "slot": 1, "position_ms": 2_000},
            ])
            pb.cue_panel.set_loaded(True)
            pb.cue_panel.hot_slots[0].pressed.emit()
            pb.toggle()
            pb.cue_panel.hot_slots[0].released.emit()
            self.assertTrue(pb._cue_preview_latched)

            pb.cue_panel.hot_slots[1].pressed.emit()
            self.assertFalse(pb._cue_preview_latched)
            self.assertTrue(state["playing"])
            pb.cue_panel.hot_slots[1].released.emit()
            self.assertFalse(state["playing"])
            self.assertEqual(events[-1], ("seek", 2_000))
        finally:
            pb.shutdown_waveforms()

    def test_space_latch_survives_release_during_pending_seek_fade(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        state = {"playing": True}
        pending = []
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]
            pb._pause_clean = lambda after=None: pending.append(after)
            pb._set_position = lambda target: events.append(("seek", target))

            def play():
                state["playing"] = True
                events.append(("play",))

            pb._play_clean = play
            pb._start_cue_preview(9_876, ("hot", 3))
            pb.toggle()
            pb._release_cue_preview(("hot", 3))
            self.assertTrue(pb._cue_preview_latched)
            self.assertEqual(len(pending), 1)

            state["playing"] = False  # the real fade has now reached silence
            pending.pop()()
            self.assertTrue(state["playing"])
            self.assertEqual(events, [("seek", 9_876), ("play",)])
        finally:
            pb.shutdown_waveforms()

    def test_live_database_cues_override_stale_or_missing_waveform_sidecars(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        try:
            live = [{
                "kind": "hot", "slot": 3, "position_ms": 44_000,
                "label": "Drop", "color": [170, 114, 255],
            }]
            stale = [{
                "kind": "hot", "slot": 4, "position_ms": 44_000,
                "label": "Wrong shifted slot", "color": [1, 2, 3],
            }]
            pb._live_cues = live
            pb._apply_analysis({
                "beats": [], "heights": [], "colors": [],
                "sample_rate": 150.0, "cues": stale,
            })
            self.assertEqual(pb.wave._cues, live)
            self.assertIsNotNone(pb.cue_panel._cues["hot"][3])
            self.assertIsNone(pb.cue_panel._cues["hot"][4])

        finally:
            pb.close()
            pb.shutdown_waveforms()

    def test_track_navigation_icons_are_exact_mirrors(self):
        from PySide6.QtCore import QSize
        from desktop.player import _track_nav_icon

        previous = _track_nav_icon(-1).pixmap(QSize(18, 14)).toImage()
        following = _track_nav_icon(1).pixmap(QSize(18, 14)).toImage()
        self.assertEqual(previous.size(), following.size())
        for y in range(previous.height()):
            for x in range(previous.width()):
                self.assertEqual(
                    previous.pixelColor(x, y),
                    following.pixelColor(previous.width() - 1 - x, y),
                )

    def test_scrub_disarmed_until_track_loaded(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        w.resize(400, 44)
        w.set_timing(10_000, 30_000)
        hits = []
        w.seeked.connect(hits.append)
        # Main-wave navigation begins only after the deck is armed.
        class Ev:
            def __init__(self, x=200.0, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w.mousePressEvent(Ev())
        w.mouseReleaseEvent(Ev())        # disarmed → ignored
        self.assertEqual(hits, [])
        w.set_active(True)
        w.mousePressEvent(Ev())
        self.assertEqual(hits, [])        # no jump on mouse-down
        w.mouseReleaseEvent(Ev())        # stationary grab lands where held
        self.assertEqual(hits, [10_000 / 30_000])
        w.mousePressEvent(Ev())
        w.mouseMoveEvent(Ev(108.0, Qt.LeftButton))
        w.mouseReleaseEvent(Ev(108.0))
        self.assertEqual(len(hits), 2)

    def test_play_track_arms_wave_and_rebinding_is_safe(self):
        pb = _win.player_bar
        self.assertFalse(pb.wave._active)
        pb.play_track("1", "/tmp/nonexistent.wav", title="X — Y")
        self.assertTrue(pb.wave._active)
        self.assertEqual(_win.now_playing.toolTip(), "")
        self.assertIn("X", _win.now_playing.text())
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

    def test_16_beat_jump_math_and_boundaries(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._dur = 30_000
            self.assertEqual(pb._beat_delta_ms(), 8_000)  # 16 beats at 120 BPM
            pb._pos = 7_999
            self.assertIsNone(pb._beat_jump_target(-1))
            self.assertEqual(pb._beat_jump_target(1), 15_999)
            pb._pos = 22_001
            self.assertEqual(pb._beat_jump_target(-1), 14_001)
            self.assertIsNone(pb._beat_jump_target(1))
        finally:
            pb.shutdown_waveforms()

    def test_16_beat_jump_uses_exact_rekordbox_grid(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._playback_available = True
            pb._dur = 60_000
            # Deliberately uneven grid: proves this is timestamps, not headline BPM.
            pb._beat_grid = [i * 500 + (40 if i >= 10 else 0) for i in range(40)]
            pb._pos = 5_140  # 100ms into grid beat at 5.040s
            self.assertEqual(pb._beat_jump_target(1), 13_140)
            self.assertIsNone(pb._beat_jump_target(-1))  # fewer than 16 behind
            pb._pos = 13_140
            self.assertEqual(pb._beat_jump_target(-1), 5_140)
        finally:
            pb.shutdown_waveforms()

    def test_jump_selector_supports_beats_and_bars(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        changes = []
        pb.jump_mode_changed.connect(changes.append)
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._dur = 120_000
            pb._pos = 20_000
            pb.set_jump_mode("4 BEATS")
            self.assertEqual(pb._beat_delta_ms(), 2_000)
            self.assertEqual(pb._beat_jump_target(1), 22_000)
            pb.set_jump_mode("8 BARS")
            self.assertEqual(pb._beat_delta_ms(), 16_000)
            self.assertEqual(pb._beat_jump_target(-1), 4_000)
            self.assertEqual(changes, ["4 BEATS", "8 BARS"])
            self.assertEqual(pb.beat_selector_btn.text(), "8 BARS")
        finally:
            pb.shutdown_waveforms()

    def test_beat_buttons_disable_at_track_edges(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb._playback_available = True
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._bpm = 120.0
            pb._on_dur(30_000)
            pb._on_pos(0)
            self.assertFalse(pb.beat_back_btn.isEnabled())
            self.assertTrue(pb.beat_forward_btn.isEnabled())
            pb._on_pos(30_000)
            self.assertTrue(pb.beat_back_btn.isEnabled())
            self.assertFalse(pb.beat_forward_btn.isEnabled())
        finally:
            pb.shutdown_waveforms()

    def test_paused_cue_holds_preview_then_returns(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PAUSED
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

            def setPosition(self, ms):
                self.positions.append(ms)

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._pause_clean = lambda after=None: (
                fake.pause(), after() if after is not None else None)
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb._set_session_cue(12_345)
            pb._pos = 13_000
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 12_345)
            self.assertEqual(fake.plays, 1)
            self.assertTrue(pb._cue_preview_active)
            pb._pos = 13_000  # playback advanced while the button was held
            pb.cue_btn.released.emit()
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.positions, [12_345, 12_345])
            self.assertEqual(pb._pos, 12_345)
            self.assertFalse(pb._cue_preview_active)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_paused_manual_move_relocates_session_cue_once(self):
        from desktop.player import PlayerBar, SessionCueState

        pb = PlayerBar()
        state = {"playing": False}
        events = []
        try:
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 60_000
            pb.is_playing = lambda: state["playing"]

            def position(target):
                pb._pos = target
                events.append(("seek", target))

            def play():
                state["playing"] = True
                events.append(("play",))

            def pause(after=None):
                state["playing"] = False
                events.append(("pause",))
                if after is not None:
                    after()

            pb._set_position = position
            pb._play_clean = play
            pb._pause_clean = pause
            pb._set_session_cue(1_000)

            pb._move_clean(20_000)
            self.assertEqual(
                pb._session_cue_state,
                SessionCueState.RELOCATION_PENDING)
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 20_000)
            self.assertEqual(pb._session_cue_state, SessionCueState.SET)
            self.assertFalse(pb._cue_preview_active)
            self.assertFalse(state["playing"])
            self.assertEqual(events, [("seek", 20_000)])
            pb.cue_btn.released.emit()

            pb.cue_btn.pressed.emit()
            self.assertTrue(pb._cue_preview_active)
            self.assertTrue(state["playing"])
            self.assertEqual(events[-2:], [("seek", 20_000), ("play",)])
            pb.cue_btn.released.emit()
            self.assertFalse(state["playing"])

            pb._move_clean(31_000)
            self.assertEqual(
                pb._session_cue_state,
                SessionCueState.RELOCATION_PENDING)
            pb.cue_btn.pressed.emit()
            self.assertEqual(pb._cue_ms, 31_000)
            self.assertFalse(pb._cue_preview_active)
            self.assertFalse(state["playing"])
        finally:
            pb.shutdown_waveforms()

    def test_stationary_playing_wave_grab_pauses_and_resumes_exactly(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 20_000
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def setPosition(self, ms):
                self.pos = ms
                self.positions.append(ms)

            def position(self):
                return self.pos

            def playbackRate(self):
                return 1.0

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

        class Ev:
            def position(self):
                return type("P", (), {
                    "x": lambda _self: 300.0,
                    "y": lambda _self: 100.0,
                })()

            def accept(self):
                pass

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            ramps = []
            pb._animate_gain = (
                lambda end, duration, completion=None:
                ramps.append((end, duration, completion)))
            pb._src_path = "/tmp/loaded.wav"
            pb._playback_available = True
            pb._dur = 60_000
            pb.wave.resize(600, 132)
            pb.wave.set_active(True)
            pb.wave.set_timing(20_000, 60_000)
            pb.wave.mousePressEvent(Ev())
            self.assertTrue(pb.wave.is_dragging())
            self.assertTrue(pb._deck_drag_active)
            self.assertFalse(pb._pause_pending)
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.positions, [])
            pb.wave.mouseReleaseEvent(Ev())
            self.assertEqual(fake.positions[-1], 20_000)
            self.assertEqual(fake.pauses, 1)
            self.assertEqual(fake.plays, 1)
            self.assertEqual(
                [(end, duration) for end, duration, _completion in ramps],
                [(pb._deck_volume, pb.FADE_IN_MS)],
            )
            self.assertFalse(pb._deck_drag_active)
            self.assertEqual(pb.wave._position_ms, 20_000)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_playing_waveform_drag_pauses_then_seeks_once_and_resumes(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 10_000
                self.positions = []
                self.plays = 0
                self.pauses = 0

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return self.pos

            def setPosition(self, ms):
                self.pos = ms
                self.positions.append(ms)

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

            def pause(self):
                self.pauses += 1
                self.state = PlaybackState.PAUSED

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            ramps = []
            pb._animate_gain = (
                lambda end, duration, completion=None:
                ramps.append((end, duration, completion)))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._transport_play_active = True
            pb._anchor_transport(10_000, True)
            pb._on_wave_drag_started()
            self.assertEqual(fake.pauses, 1)
            self.assertFalse(pb._pause_pending)
            self.assertEqual(pb.audio.volume(), 0.0)
            self.assertFalse(pb._transport_running)
            self.assertEqual(ramps, [])
            pb._on_state(PlaybackState.PAUSED)
            self.assertTrue(pb._transport_play_active)

            pb._seek(8_000 / 30_000)  # release's one authoritative seek
            self.assertEqual(fake.positions, [])  # held until release
            pb._on_wave_drag_finished()
            self.assertEqual(fake.positions[-1], 8_000)
            self.assertEqual(fake.plays, 1)
            self.assertEqual(fake.state, PlaybackState.PLAYING)
            self.assertTrue(pb._transport_play_active)
            self.assertEqual(
                [(end, duration) for end, duration, _completion in ramps],
                [(pb._deck_volume, pb.FADE_IN_MS)],
            )
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_clean_pause_pins_backend_to_exact_presented_position(self):
        from desktop.player import PlayerBar
        from PySide6.QtTest import QTest

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.pos = 9_900
                self.holds = 0

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return self.pos

            def setPosition(self, ms):
                self.pos = ms

            def begin_visual_hold(self):
                self.holds += 1
                return 10_037.25

            def pause(self):
                self.state = PlaybackState.PAUSED

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._animate_gain = (
                lambda _end, _duration, completion=None:
                setattr(pb, "_gain_completion", completion))
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._anchor_transport(10_000, True)
            QTest.qWait(12)
            pb._pause_clean()
            pb._on_gain_finished()
            # Resume starts from the exact newest Metal drawable, never from
            # the backend's stale coarse 9.9-second position.
            self.assertEqual(fake.pos, 10_037.25)
            self.assertEqual(fake.holds, 1)
            self.assertEqual(pb._pos, fake.pos)
            self.assertFalse(pb._transport_running)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_transport_clock_interpolates_between_backend_ticks(self):
        from desktop.player import PlayerBar
        from PySide6.QtTest import QTest

        class FakePlayer:
            def playbackState(self):
                return PlaybackState.PLAYING

            def playbackRate(self):
                return 1.0

        pb = PlayerBar()
        real_player = pb.player
        try:
            pb.player = FakePlayer()
            pb._dur = 10_000
            pb._anchor_transport(1_000, True)
            QTest.qWait(35)
            # The fake backend is frozen; only the monotonic transport clock
            # can advance this value between backend position signals.
            self.assertGreaterEqual(pb._transport_position(), 1_025)
            self.assertLess(pb._transport_position(), 1_150)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_activation_resynchronizes_audio_buttons_and_waveform(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.state = PlaybackState.PLAYING
                self.synced = 0

            def synchronize(self):
                self.synced += 1

            def playbackState(self):
                return self.state

            def playbackRate(self):
                return 1.0

            def position(self):
                return 4321

            def set_visual_position(self, _ms, _preview):
                pass

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._dur = 30_000
            pb._pos = 1000
            pb._transport_play_active = False
            pb.resynchronize_transport()
            self.assertEqual(fake.synced, 1)
            self.assertEqual(pb._pos, 4321)
            self.assertTrue(pb._transport_play_active)
            self.assertEqual(pb.play_btn.text(), "❚❚")

            fake.state = PlaybackState.PAUSED
            pb.resynchronize_transport()
            self.assertEqual(pb._pos, 4321)
            self.assertFalse(pb._transport_play_active)
            self.assertEqual(pb.play_btn.text(), "▶")
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_new_transport_intent_rejects_stale_gain_completion(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        called = []
        try:
            pb._gain_completion = lambda: called.append("stale")
            pb._gain_epoch = pb._transport_epoch
            pb._begin_transport_intent()
            pb._on_gain_finished()
            self.assertEqual(called, [])
        finally:
            pb.shutdown_waveforms()

    def test_transport_has_no_python_repaint_timer(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        try:
            self.assertFalse(hasattr(pb, "_scroll_timer"))
        finally:
            pb.shutdown_waveforms()

    def test_backend_drift_is_slewed_not_applied_as_a_tick(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def playbackState(self):
                return PlaybackState.PLAYING

            def playbackRate(self):
                return 1.0

        pb = PlayerBar()
        real_player = pb.player
        try:
            pb.player = FakePlayer()
            pb._dur = 10_000
            pb._anchor_transport(1_000, True)
            before = pb._transport_anchor_ms
            pb._on_backend_pos(1_100)
            # A normal backend correction queues a slew; it cannot jump the
            # waveform in the signal handler.
            self.assertEqual(pb._transport_anchor_ms, before)
            self.assertGreater(pb._transport_slew_ms, 0)
            self.assertEqual(pb._transport_anchor_ms, before)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_new_track_waits_then_starts_on_first_grid_beat(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.state = PlaybackState.PAUSED
                self.positions = []
                self.plays = 0

            def playbackState(self):
                return self.state

            def setPosition(self, ms):
                self.positions.append(ms)

            def play(self):
                self.plays += 1
                self.state = PlaybackState.PLAYING

        pb = PlayerBar()
        pb._playback_available = True
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._pending_autoplay = True
            pb._dur = 0  # analysis wins the race; media is not loaded yet
            pb._apply_analysis({
                "beats": [[1, 875, 140.0], [2, 1304, 140.0]],
                "heights": [], "colors": [],
                "provenance": {"grid": "rekordbox"},
            })
            self.assertEqual(fake.plays, 0)
            pb._on_dur(60_000)
            self.assertEqual(fake.positions, [875])
            self.assertEqual(fake.plays, 1)
            self.assertEqual(pb._pos, 875)
            self.assertEqual(pb._cue_ms, 875)
            self.assertEqual(pb.wave._session_cue_ms, 875)
            self.assertFalse(pb._pending_autoplay)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_load_only_waits_then_pauses_on_first_grid_beat(self):
        from desktop.player import PlayerBar

        class FakePlayer:
            def __init__(self):
                self.positions = []
                self.plays = 0

            def playbackState(self):
                return PlaybackState.PAUSED

            def setPosition(self, ms):
                self.positions.append(ms)

            def play(self):
                self.plays += 1

        pb = PlayerBar()
        real_player = pb.player
        try:
            fake = FakePlayer()
            pb.player = fake
            pb._src_path = "/tmp/loaded.wav"
            pb._pending_autoplay = True
            pb._pending_should_play = False
            pb._dur = 0
            pb._apply_analysis({
                "beats": [[1, 875, 140.0], [2, 1304, 140.0]],
                "heights": [], "colors": [],
                "provenance": {"grid": "rekordbox"},
            })
            self.assertEqual(fake.plays, 0)
            pb._on_dur(60_000)
            self.assertEqual(fake.positions, [875])
            self.assertEqual(fake.plays, 0)
            self.assertEqual(pb._pos, 875)
            self.assertEqual(pb._cue_ms, 875)
            self.assertFalse(pb._pending_autoplay)
        finally:
            pb.player = real_player
            pb.shutdown_waveforms()

    def test_rgb_raster_is_stable_and_song_anchored(self):
        from desktop.player import WaveformBar, _rasterize_rgb
        heights = [0.1, 0.8, 0.2, 0.6, 0.4, 1.0]
        colors = [[1, 2, 3], [4, 5, 6], [7, 1, 2],
                  [2, 7, 1], [3, 4, 5], [14, 0, 0]]
        columns = _rasterize_rgb(heights, colors, 6.0, 3.0)
        # Fixed 2-sample bins choose the local peak once; viewport position
        # never participates in color selection.
        self.assertEqual(columns, [
            [0.8, [4, 5, 6]], [0.6, [2, 7, 1]], [1.0, [14, 0, 0]],
        ])
        w = WaveformBar()
        w.PIXELS_PER_SECOND = 3.0
        w.set_analysis({
            "heights": heights, "colors": colors, "sample_rate": 6.0,
            "columns": columns,
            "_detail_bands": bytes([
                1, 2, 3,
                4, 5, 127,
                6, 7, 8,
                9, 10, 11,
                12, 13, 14,
                15, 16, 17,
            ]),
        })
        before = [(height, color.rgb()) for height, color in w._rgb_columns]
        w.set_timing(250, 10_000)
        w.set_timing(750, 10_000)
        after = [(height, color.rgb()) for height, color in w._rgb_columns]
        self.assertEqual(after, before)
        self.assertEqual(w._song_column(1000), 3)
        self.assertEqual(w._rgb_detail[1][0], 1.0)

    def test_current_waveform_visibility_shoulder_preserves_true_zero(self):
        from desktop.player import _display_waveform_height

        values = [
            _display_waveform_height(value / 65535.0)
            for value in (0, 1, 2, 6, 66, 65535)
        ]
        self.assertEqual(values[0], 0.0)
        self.assertGreater(values[1], 0.0)
        self.assertGreater(values[3], 0.004)
        self.assertAlmostEqual(values[-1], 0.84)
        self.assertEqual(values, sorted(values))

    def test_qt_waveform_preserves_dedicated_rgb_despite_band_master(self):
        from desktop.player import WaveformBar

        colors = [[14, 0, 0], [0, 7, 0], [0, 0, 7]]
        w = WaveformBar()
        w.set_analysis({
            "heights": [0.5, 0.5, 0.5],
            "colors": colors,
            "sample_rate": 150.0,
            "_spectral_24": bytes(
                [255] * 8 + [0] * 16
                + [0] * 8 + [255] * 8 + [0] * 8
                + [0] * 16 + [255] * 8),
        })
        self.assertEqual(w._rgb_colors, colors)

    def test_waveform_builds_bar_beat_positions_from_imported_grid(self):
        from desktop.player import WaveformBar

        w = WaveformBar()
        w.set_analysis({
            "beats": [
                [1, 500, 120.0], [2, 1000, 120.0],
                [3, 1500, 120.0], [4, 2000, 120.0],
                [1, 2500, 120.0], [2, 3000, 120.0],
            ],
        })
        self.assertEqual(w._beat_positions, [
            (500, 1, 1), (1000, 1, 2), (1500, 1, 3),
            (2000, 1, 4), (2500, 2, 1), (3000, 2, 2),
        ])

    def test_qt_fallback_draws_every_beat_as_opaque_timing_ink(self):
        source = (
            Path(__file__).parents[1] / "desktop" / "player.py"
        ).read_text()
        grid = source.split("def _paint_grid", 1)[1].split(
            "@staticmethod", 1)[0]
        self.assertIn("QColor(255, 55, 88)", grid)
        self.assertIn("QColor(120, 135, 158)", grid)
        self.assertNotIn("QColor(255, 55, 88, 175)", grid)
        self.assertNotIn("QColor(120, 135, 158, 85)", grid)

    def test_waveform_motion_keeps_subpixel_phase_at_120hz(self):
        from desktop.player import WaveformBar
        w = WaveformBar()
        # At 92 px/s, 120 Hz advances 0.736 logical pixels per frame. Integer
        # columns would duplicate some frames; the continuous song coordinate
        # preserves a distinct phase for every refresh.
        xs = [w._song_x(i * 8) for i in range(12)]
        self.assertEqual(len(set(xs)), len(xs))
        for a, b in zip(xs, xs[1:]):
            self.assertAlmostEqual(b - a, 0.736, places=6)

    def test_scrolling_waveform_seek_is_relative_to_fixed_playhead(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 92)
        w.set_active(True)
        w.set_timing(10_000, 30_000)
        hits = []
        w.seeked.connect(hits.append)
        # Center is current time; 92px right is exactly +1 second.
        self.assertEqual(w._time_at_x(200, 200), 10.0)
        self.assertEqual(w._time_at_x(292, 200), 11.0)
        # Grab the waveform and pull it left by 92px to reveal +1 second.
        w.mousePressEvent(Ev(200))
        w.mouseMoveEvent(Ev(108, Qt.LeftButton))
        w.mouseReleaseEvent(Ev(108))
        self.assertAlmostEqual(hits[-1], 11 / 30, places=4)
        self.assertEqual(w._position_ms, 11_000)

    def test_overview_click_seeks_to_absolute_song_position(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, y):
                self._x = x
                self._y = y

            def position(self):
                return type("P", (), {
                    "x": lambda _self: self._x,
                    "y": lambda _self: self._y,
                })()

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 132)
        w.set_active(True)
        w.set_timing(10_000, 30_000)
        previews, seeks = [], []
        w.previewed.connect(previews.append)
        w.seeked.connect(seeks.append)
        w.mousePressEvent(Ev(300, 10))
        self.assertEqual(previews, [22_500])
        w.mouseReleaseEvent(Ev(300, 10))
        self.assertEqual(seeks, [0.75])
        self.assertEqual(w._position_ms, 22_500)

    def test_zoom_controls_scale_only_the_detail_waveform(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, y):
                self._x, self._y = x, y

            def position(self):
                return type("P", (), {
                    "x": lambda _self: self._x,
                    "y": lambda _self: self._y,
                })()

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 132)
        w.set_timing(10_000, 30_000)
        changes, seeks = [], []
        w.zoom_changed.connect(changes.append)
        w.seeked.connect(seeks.append)
        overview_target = w._overview_time_at_x(300)
        self.assertEqual(w.PIXELS_PER_SECOND, 92.0)
        w.mousePressEvent(Ev(50, 40))  # plus
        self.assertEqual(w.PIXELS_PER_SECOND, 128.0)
        self.assertEqual(w._overview_time_at_x(300), overview_target)
        self.assertAlmostEqual(w._time_at_x(328, 200), 11.0)
        w.mousePressEvent(Ev(20, 40))  # minus
        self.assertEqual(w.PIXELS_PER_SECOND, 92.0)
        w.set_zoom(2000.0)
        self.assertEqual(w.PIXELS_PER_SECOND, 1536.0)
        self.assertEqual(w._overview_time_at_x(300), overview_target)
        self.assertAlmostEqual(w._time_at_x(1736, 200), 11.0)
        self.assertEqual(changes, [128.0, 92.0, 1536.0])
        self.assertEqual(seeks, [])

    def test_zoom_scale_is_one_bounded_canonical_sequence(self):
        from desktop.player import WaveformBar, _ZOOM_LEVELS

        w = WaveformBar()
        changes = []
        w.zoom_changed.connect(changes.append)
        for scale in _ZOOM_LEVELS:
            w.set_zoom(scale)
            self.assertEqual(w.PIXELS_PER_SECOND, scale)
        self.assertEqual(_ZOOM_LEVELS[0], 24.0)
        self.assertEqual(_ZOOM_LEVELS[-1], 1536.0)
        w.set_zoom(-100)
        self.assertEqual(w.PIXELS_PER_SECOND, 24.0)
        w.set_zoom(50_000)
        self.assertEqual(w.PIXELS_PER_SECOND, 1536.0)

    def test_waveform_drag_is_anchored_and_releases_exactly(self):
        from desktop.player import WaveformBar

        class Ev:
            def __init__(self, x, buttons=Qt.NoButton):
                self.x = x
                self._buttons = buttons

            def position(self):
                return type("P", (), {"x": lambda _self: self.x})()

            def buttons(self):
                return self._buttons

            def accept(self):
                pass

        w = WaveformBar()
        w.resize(400, 92)
        w.set_active(True)
        w.set_analysis({
            "beats": [[1, 8_000, 140.0], [2, 9_000, 140.0],
                      [3, 10_000, 140.0]],
        })
        w.set_timing(10_000, 30_000)
        hits = []
        previews = []
        w.seeked.connect(hits.append)
        w.previewed.connect(previews.append)
        w.mousePressEvent(Ev(200))
        w.mouseMoveEvent(Ev(290, Qt.LeftButton))
        self.assertAlmostEqual(w._position_ms, 9_021.739, places=3)
        self.assertEqual(hits, [])  # no decoder seek while the hand is moving
        w.mouseMoveEvent(Ev(380, Qt.LeftButton))
        # Still calculated from the original 10s mouse-down position. The old
        # compounding implementation would have run away to roughly 7 seconds.
        self.assertAlmostEqual(w._position_ms, 8_043.478, places=3)
        self.assertEqual(hits, [])
        self.assertAlmostEqual(previews[0], 9_021.739, places=3)
        self.assertAlmostEqual(previews[1], 8_043.478, places=3)
        w.mouseReleaseEvent(Ev(380))
        # One exact decoder seek at the timestamp under the hand. There is no
        # release-time magnet that can make the waveform slip several pixels.
        self.assertAlmostEqual(hits[0] * 30_000, 8_043.478, places=3)

    def test_native_drag_preview_is_forwarded_before_release(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.visual = []

            def set_visual_position(self, ms, preview):
                self.visual.append((ms, preview))

            def playbackState(self):
                return PlaybackState.PAUSED

        pb = PlayerBar()
        real = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._pos = 10_000
            pb._on_wave_drag_started()
            pb._on_wave_preview(8_750)
            self.assertEqual(fake.visual, [
                (10_000, True), (8_750, True),
            ])
            pb._deck_drag_target = 8_750
            pb._paint_position(10_100)
            # A late backend/fade tick cannot overwrite the held destination
            # between mouse-up and the decoder's one authoritative seek.
            self.assertEqual(fake.visual, [
                (10_000, True), (8_750, True),
            ])
        finally:
            pb.player = real
            pb.shutdown_waveforms()

    def test_stationary_grab_uses_exact_native_hold_not_coarse_python_tick(self):
        from desktop.player import PlayerBar

        class FakeNative:
            def __init__(self):
                self.positions = []
                self.visual = []

            def begin_visual_hold(self):
                return 10_037.25

            def set_visual_position(self, ms, preview):
                self.visual.append((ms, preview))

            def setPosition(self, ms):
                self.positions.append(ms)

            def playbackState(self):
                return PlaybackState.PAUSED

            def playbackRate(self):
                return 1.0

            def position(self):
                return 10_000

        pb = PlayerBar()
        real = pb.player
        try:
            fake = FakeNative()
            pb.player = fake
            pb._dur = 30_000
            pb._pos = 10_000
            pb.wave._duration_ms = 30_000
            pb.wave._drag_origin_ms = 10_000
            pb.wave._drag_preview_ms = 10_000

            pb._on_wave_drag_started()

            self.assertEqual(pb._pos, 10_037.25)
            self.assertEqual(pb.wave._drag_origin_ms, 10_037.25)
            self.assertEqual(pb.wave._drag_preview_ms, 10_037.25)
            # Atomic native capture owns the initial freeze; there is no second
            # setPreview call at the stale 10,000 ms Python poll.
            self.assertEqual(fake.visual, [])

            pb._deck_drag_target = 10_037.25
            pb._on_wave_drag_finished()
            self.assertEqual(fake.positions, [10_037.25])
        finally:
            pb.player = real
            pb.shutdown_waveforms()

    def test_track_arrows_follow_visible_row_order(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _win.model.set_filter("")
        _app.processEvents()
        self.assertEqual(_win._player_order(), ["2", "1", "3"])
        played = []
        real = _win._play_track
        _win._play_track = lambda t: played.append(t.track_id)
        try:
            _win.player_bar._track_id = "1"
            _win._play_relative(-1)
            _win._play_relative(1)
        finally:
            _win._play_track = real
            _win.player_bar._track_id = None
        self.assertEqual(played, ["2", "3"])

    def test_track_buttons_emit_direction_requests(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        hits = []
        try:
            pb._track_id = "loaded"
            pb.set_track_navigation(True, True)
            pb.previous_requested.connect(lambda: hits.append(-1))
            pb.next_requested.connect(lambda: hits.append(1))
            pb.previous_btn.click()
            pb.next_btn.click()
            self.assertEqual(hits, [-1, 1])
        finally:
            pb.shutdown_waveforms()


class QtTrapAudit(unittest.TestCase):
    """Regression pins for the 2026-07-03 Qt-trap audit."""

    def test_interaction_deferral_does_not_wake_every_waveform_frame(self):
        """Only the waveform host resize owns native geometry/display work."""
        class Worker:
            def __init__(self):
                self.seconds = []

            def defer_for_interaction(self, seconds):
                self.seconds.append(seconds)

        worker = Worker()
        player = _win.player_bar.player
        wakes = []
        old_worker = getattr(_win, "_wf_fill_worker", None)
        old_request = getattr(player, "request_display", None)
        _win._wf_fill_worker = worker
        player.request_display = lambda: wakes.append(True)
        try:
            _win._defer_library_prep(0.75)
            self.assertEqual(worker.seconds, [0.75])
            self.assertEqual(wakes, [])
        finally:
            _win._wf_fill_worker = old_worker
            if old_request is None:
                del player.request_display
            else:
                player.request_display = old_request

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

    def test_c_key_routes_press_and_release_to_momentary_cue(self):
        calls = []
        real_policy = _win._space_should_play
        real_press = _win.player_bar._cue_press
        real_release = _win.player_bar._cue_release
        _win._space_should_play = lambda _focus: True
        _win.player_bar._cue_press = lambda: calls.append("press")
        _win.player_bar._cue_release = lambda: calls.append("release")
        try:
            press = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.NoModifier)
            release = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
            self.assertTrue(_win.eventFilter(_win.table, press))
            self.assertTrue(_win.eventFilter(_win.table, release))
            self.assertEqual(calls, ["press", "release"])
        finally:
            _win._space_should_play = real_policy
            _win.player_bar._cue_press = real_press
            _win.player_bar._cue_release = real_release
            _win._cue_key_down = False

    def test_c_claims_shortcut_override_and_swallows_repeat_keypresses(self):
        calls = []
        real_policy = _win._space_should_play
        real_press = _win.player_bar._cue_press
        real_release = _win.player_bar._cue_release
        _win._space_should_play = lambda _focus: True
        _win.player_bar._cue_press = lambda: calls.append("press")
        _win.player_bar._cue_release = lambda: calls.append("release")
        try:
            override = QKeyEvent(
                QEvent.ShortcutOverride, Qt.Key_C, Qt.NoModifier)
            press = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.NoModifier)
            repeat = QKeyEvent(
                QEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c", True, 2)
            release = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
            self.assertTrue(_win.eventFilter(_win.table, override))
            self.assertTrue(override.isAccepted())
            self.assertTrue(_win.eventFilter(_win.table, press))
            self.assertTrue(_win.eventFilter(_win.table, repeat))
            self.assertTrue(_win.eventFilter(_win.table, release))
            self.assertEqual(calls, ["press", "release"])
        finally:
            _win._space_should_play = real_policy
            _win.player_bar._cue_press = real_press
            _win.player_bar._cue_release = real_release
            _win._cue_key_down = False

    def test_browser_views_do_not_offer_text_input_to_macos(self):
        """A held C must not enter Cocoa's accent-character composition path.

        ShortcutOverride alone cannot prevent that native input-method path;
        the non-text browser widgets themselves must opt out. Inline delegate
        editors remain real text widgets with IME support.
        """
        for view in (_win.table, _win.tree, _win.sb_tree, _win.device_tree,
                     _win.artists_view):
            self.assertFalse(
                view.testAttribute(Qt.WA_InputMethodEnabled),
                type(view).__name__,
            )

        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QLineEdit, QStyleOptionViewItem
        from desktop.trees import CellEditDelegate
        edit_model = QStandardItemModel()
        edit_model.appendRow(QStandardItem("editable"))
        delegate = CellEditDelegate()
        editor = delegate.createEditor(
            _win.table.viewport(), QStyleOptionViewItem(),
            edit_model.index(0, 0))
        try:
            self.assertIsInstance(editor, QLineEdit)
            self.assertTrue(editor.testAttribute(Qt.WA_InputMethodEnabled))
        finally:
            editor.deleteLater()

    def test_command_c_is_not_stolen_by_cue_shortcut(self):
        calls = []
        real_press = _win.player_bar._cue_press
        _win.player_bar._cue_press = lambda: calls.append("press")
        try:
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.MetaModifier)
            self.assertFalse(_win.eventFilter(_win.search, event))
            self.assertEqual(calls, [])
        finally:
            _win.player_bar._cue_press = real_press

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

    def test_device_destructive_guards_watch_the_real_export_worker(self):
        from pathlib import Path

        source = (
            Path(__file__).parents[1] / "desktop" / "device_browser.py"
        ).read_text()
        self.assertIn('"export_worker", "_resync_worker"', source)
        self.assertNotIn('"_export_worker"', source)


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
    def test_columns_share_exact_playlist_boundaries(self):
        from desktop.model import COLUMNS

        _win._show_collection()
        _app.processEvents()
        table_header = _win.table.horizontalHeader()
        expected = {
            column: (
                table_header.sectionViewportPosition(column),
                table_header.sectionSize(column),
            )
            for column in range(len(COLUMNS))
            if not _win.table.isColumnHidden(column)
        }

        _win._show_artists()
        _app.processEvents()
        artist_header = _win.artists_view.header()
        self.assertEqual(_win.artists_view.columnCount(), len(COLUMNS))
        self.assertEqual(
            [artist_header.model().headerData(i, Qt.Horizontal)
             for i in range(len(COLUMNS))],
            ["Artist / Track", *(label for _key, label, _hidden in COLUMNS[1:])],
        )
        actual = {
            column: (
                artist_header.sectionViewportPosition(column),
                artist_header.sectionSize(column),
            )
            for column in range(len(COLUMNS))
            if not _win.artists_view.isColumnHidden(column)
        }
        self.assertEqual(actual, expected)

    def test_resizing_either_view_keeps_shared_boundaries(self):
        column = 1  # Artist
        original = _win.table.columnWidth(column)
        try:
            _win.table.setColumnWidth(column, original + 17)
            _app.processEvents()
            self.assertEqual(
                _win.artists_view.columnWidth(column), original + 17)
            _win.artists_view.setColumnWidth(column, original + 9)
            _app.processEvents()
            self.assertEqual(_win.table.columnWidth(column), original + 9)
        finally:
            _win.table.setColumnWidth(column, original)

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
        t = _win._selected_track()      # browser selection source of truth
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

    def test_title_focus_clears_search_and_falls_back_to_collection(self):
        from desktop.app import COLLECTION_ROLE

        playlist = _win._find_tree_item("SET TWO")
        self.assertIsNotNone(playlist)
        _win.tree.setCurrentItem(playlist)  # contains only track 2
        _win.search.setText("alpha")
        _win.player_bar._track_id = "1"
        try:
            _win._focus_now_playing()
            self.assertEqual(_win.search.text(), "")
            self.assertTrue(
                _win.sb_tree.currentItem().data(0, COLLECTION_ROLE))
            idx = _win.table.currentIndex()
            self.assertTrue(idx.isValid())
            self.assertEqual(_win.model._rows[idx.row()]["id"], "1")
        finally:
            _win.player_bar._track_id = None

    def test_title_focus_preserves_current_view_when_track_is_present(self):
        playlist = _win._find_tree_item("CRATES / SET ONE")
        self.assertIsNotNone(playlist)
        _win.tree.setCurrentItem(playlist)
        _win.player_bar._track_id = "1"
        try:
            _win._focus_now_playing()
            self.assertIs(_win.tree.currentItem(), playlist)
            idx = _win.table.currentIndex()
            self.assertEqual(_win.model._rows[idx.row()]["id"], "1")
        finally:
            _win.player_bar._track_id = None


class AutoplaySemantics(unittest.TestCase):
    def setUp(self):
        self._saved = (
            _win._autoplay_mode,
            list(_win._autoplay_order),
            _win._autoplay_index,
            set(_win._autoplay_random_seen),
            list(_win._autoplay_random_queue),
        )
        self._real_save = _win._save_state
        _win._save_state = lambda: None
        _win._set_autoplay_mode("", save=False)

    def tearDown(self):
        _win._save_state = self._real_save
        (mode, order, index, seen, queue) = self._saved
        _win._set_autoplay_mode(mode, save=False)
        _win._autoplay_order = order
        _win._autoplay_index = index
        _win._autoplay_random_seen = seen
        _win._autoplay_random_queue = queue

    def test_three_header_modes_are_mutually_exclusive_and_toggle_off(self):
        for mode, button in _win._autoplay_buttons.items():
            button.click()
            self.assertEqual(_win._autoplay_mode, mode)
            self.assertTrue(button.property("active"))
            self.assertEqual(
                sum(bool(b.property("active"))
                    for b in _win._autoplay_buttons.values()),
                1,
            )
            button.click()
            self.assertEqual(_win._autoplay_mode, "")
            self.assertFalse(button.property("active"))

    def test_up_and_down_keep_the_playlist_that_started_the_track(self):
        playlist = _win._find_tree_item("CRATES / SET ONE")
        _win.tree.setCurrentItem(playlist)
        _app.processEvents()
        _win._capture_autoplay_context("3")
        self.assertEqual(_win._autoplay_order, ["3", "1"])

        # Browse elsewhere after playback starts; the frozen origin remains.
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        real_playable = _win._autoplay_playable
        _win._autoplay_playable = lambda _index: True
        try:
            _win._set_autoplay_mode("down", save=False)
            self.assertEqual(_win._next_autoplay_index(), 1)
            _win._autoplay_index = 1
            _win._set_autoplay_mode("up", save=False)
            self.assertEqual(_win._next_autoplay_index(), 0)
        finally:
            _win._autoplay_playable = real_playable

    def test_random_is_a_shuffle_bag_without_repeats(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        order = [row["id"] for row in _win.model._rows]
        _win._capture_autoplay_context(order[0])
        _win._set_autoplay_mode("random", save=False)
        _win._autoplay_rng.seed(7)
        real_playable = _win._autoplay_playable
        _win._autoplay_playable = lambda _index: True
        played = [order[0]]
        try:
            for _ in range(len(order) - 1):
                index = _win._next_autoplay_index()
                self.assertIsNotNone(index)
                _win._autoplay_index = index
                track_id = order[index]
                _win._autoplay_random_seen.add(track_id)
                played.append(track_id)
            self.assertEqual(len(set(played)), len(order))
            # A fresh cycle cannot immediately repeat the just-finished song.
            next_index = _win._next_autoplay_index()
            self.assertIsNotNone(next_index)
            self.assertNotEqual(order[next_index], played[-1])
        finally:
            _win._autoplay_playable = real_playable

    def test_terminal_advance_keeps_context_and_rejects_a_stale_end(self):
        _win._autoplay_order = ["3", "1"]
        _win._autoplay_index = 0
        _win._set_autoplay_mode("down", save=False)
        real_playable = _win._autoplay_playable
        real_open = _win._open_track
        current = _win.player_bar._track_id
        opened = []
        _win._autoplay_playable = lambda _index: True
        _win._open_track = lambda track, autoplay, capture_autoplay: (
            opened.append(
                (track.track_id, autoplay, capture_autoplay)) or True)
        _win.player_bar._track_id = "3"
        try:
            _win._advance_autoplay("old-track")
            self.assertEqual(opened, [])
            _win._advance_autoplay("3")
            self.assertEqual(opened, [("1", True, False)])
            self.assertEqual(_win._autoplay_order, ["3", "1"])
            self.assertEqual(_win._autoplay_index, 1)
        finally:
            _win._autoplay_playable = real_playable
            _win._open_track = real_open
            _win.player_bar._track_id = current

    def test_only_ordinary_transport_end_requests_advance(self):
        pb = _win.player_bar
        hits = []
        on_end = hits.append
        pb.track_ended.connect(on_end)
        saved = (
            pb._track_id,
            pb._transport_play_active,
            pb._cue_preview_active,
            pb._cue_preview_latched,
            pb._pause_pending,
        )
        try:
            pb._transport_play_active = True
            pb._track_id = "ended-track"
            pb._cue_preview_active = False
            pb._cue_preview_latched = False
            pb._pause_pending = False
            pb._on_end_of_media()
            _app.processEvents()
            self.assertEqual(hits, ["ended-track"])

            pb._transport_play_active = False
            pb._on_end_of_media()
            _app.processEvents()
            self.assertEqual(hits, ["ended-track"])
        finally:
            (
                pb._track_id,
                pb._transport_play_active,
                pb._cue_preview_active,
                pb._cue_preview_latched,
                pb._pause_pending,
            ) = saved
            pb.track_ended.disconnect(on_end)


class SpaceSemantics(unittest.TestCase):
    """Space controls only the loaded deck and never opens browser selection."""

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

    def test_different_track_highlighted_still_toggles_loaded_deck(self):
        _win.player_bar._track_id = "1"       # something else is loaded
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])

    def test_same_track_highlighted_toggles(self):
        _win.player_bar._track_id = "2"
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [1])

    def test_nothing_loaded_is_a_noop(self):
        _win.player_bar._track_id = None
        self._select("2")
        _win._toggle_player()
        self.assertEqual(self._played, [])
        self.assertEqual(self._toggled, [])


class DoubleClickSemantics(unittest.TestCase):
    """Browser mouse activation is the sole direct load gesture."""

    def setUp(self):
        self._loaded = []
        self._played = []
        self._orig_load, self._orig_play = _win._load_track, _win._play_track
        _win._load_track = lambda t: self._loaded.append(t.track_id)
        _win._play_track = lambda t: self._played.append(t.track_id)
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))  # Collection
        _app.processEvents()

    def tearDown(self):
        _win._load_track, _win._play_track = self._orig_load, self._orig_play

    def test_table_double_click_loads_without_playing(self):
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == "2")
        _win._play_row(_win.model.index(row, 0))
        self.assertEqual(self._loaded, ["2"])
        self.assertEqual(self._played, [])

    def test_artist_double_click_loads_without_playing(self):
        from desktop.app import ARTIST_TRACK_ROLE
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(1))  # Artists
        _app.processEvents()
        top = _win.artists_view.topLevelItem(0)
        top.setExpanded(True)
        child = top.child(0)
        self.assertIsNotNone(child)
        self.assertTrue(child.data(0, ARTIST_TRACK_ROLE))
        _win._artists_double(child, 0)
        self.assertEqual(len(self._loaded), 1)
        self.assertEqual(self._played, [])


class QtWaveformFallback(unittest.TestCase):
    """AAC/ALAC receive the same canonical V4 analysis via QAudioDecoder."""

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
        from desktop.player import qt_detail_peaks
        path = f"{self.dir}/{name}"
        detail = qt_detail_peaks(path)
        self.assertGreater(detail["detail_rate"], 6100)
        self.assertLess(detail["detail_rate"], 6200)
        self.assertEqual(
            detail["height_encoding"], "linear-u16le")
        import numpy as np
        heights = np.frombuffer(detail["detail_heights"], dtype="<u2")
        midpoint = len(heights) // 2
        self.assertGreater(int(max(heights[:midpoint - 30])), 63_000)
        self.assertLess(int(max(heights[midpoint + 30:])), 3_100)

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
    on Collection ready to browse (transport keys, arrows), not typing into search."""

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
        self._saved_waveform_rechecks = _win._waveform_rechecks_enabled
        _win._waveform_rechecks_enabled = False
        _win._waveform_recheck_timer.stop()
        _win.imported = {}

    def tearDown(self):
        for tid in list(_win.imported):
            _win.lib.tracks_by_id.pop(tid, None)
        _win.imported = self._saved_imported
        _win.custom = self._saved_custom
        _win._refresh_custom()
        _win._reload_current_view()  # later classes must not see imported rows
        _win._save_state()
        _win._waveform_recheck_timer.stop()
        _win._waveform_rechecks_enabled = self._saved_waveform_rechecks
        if self._saved_waveform_rechecks:
            _win._schedule_waveform_recheck()
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
        self.assertTrue(_win._waveform_rechecks_enabled)
        self.assertTrue(_win._waveform_recheck_timer.isActive())

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
        self._saved_waveform_rechecks = _win._waveform_rechecks_enabled
        _win._waveform_rechecks_enabled = False
        _win._waveform_recheck_timer.stop()
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
        _win._waveform_recheck_timer.stop()
        _win._waveform_rechecks_enabled = self._saved_waveform_rechecks
        if self._saved_waveform_rechecks:
            _win._schedule_waveform_recheck()
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
        self.assertTrue(_win._waveform_rechecks_enabled)

    def test_fix_links_noop_when_nothing_missing(self):
        _win._apply_relinks({})
        _win.imported[self.tid]["path"] = str(self.new)  # already healthy
        _win._fix_links()  # must not start a worker or pop a dialog
        self.assertEqual(len(getattr(_win, "_oneshots", [])), 0)


class ExportableViews(unittest.TestCase):
    """Collection and Artists are draggable playlists: whole pool, view order."""

    def test_collection_view_playlist_in_collection_order(self):
        pl = _win._view_playlist("collection")
        self.assertEqual(pl.name, "Collection")
        self.assertEqual(pl.keys, ["2", "1", "3"])  # no files → title A→Z fallback

    def test_artists_view_playlist_artist_order(self):
        pl = _win._view_playlist("artists")
        self.assertEqual(pl.name, "Artists")
        self.assertEqual(pl.keys, ["2", "3", "1"])  # Ann, Mia, Zed A→Z

    def test_resolve_jobs_handles_sbview(self):
        pls = _win._resolve_jobs([("sbview", "collection"), ("sbview", "artists"),
                                  ("sbview", "nope")])
        self.assertEqual([p.name for p in pls], ["Collection", "Artists"])

    def test_sync_playlists_includes_views(self):
        _win._sync_playlists_cache = None
        names = [p.name for p in _win._sync_playlists()]
        self.assertIn("Collection", names)
        self.assertIn("Artists", names)

    def test_sync_playlists_are_not_rebuilt_by_each_device_poll(self):
        _win._sync_playlists_cache = None
        first = _win._sync_playlists()
        real = _win._view_playlist
        _win._view_playlist = lambda _name: self.fail("rebuilt cached views")
        try:
            self.assertIs(_win._sync_playlists(), first)
        finally:
            _win._view_playlist = real

    def test_tree_drag_emits_sbview_lines(self):
        md = _win.sb_tree.mimeData([_win.sb_tree.topLevelItem(0),
                                    _win.sb_tree.topLevelItem(1)])
        self.assertEqual(md.text(), "sbview\tcollection\nsbview\tartists")

    def test_reserved_names_guard(self):
        self.assertTrue(_win._custom_name_taken("Collection"))
        self.assertTrue(_win._custom_name_taken("artists"))
        self.assertFalse(_win._custom_name_taken("LIVESETS"))


class SidebarSplit(unittest.TestCase):
    """SOUNDBOX/DEVICES header bars are splitter handles: drag to resize, sizes
    persist with the column layout."""

    def test_three_panes_and_header_bars_in_handles(self):
        from PySide6.QtWidgets import QLabel
        s = _win._sidebar_split
        self.assertEqual(s.count(), 3)
        h1 = " ".join(x.text() for x in s.handle(1).findChildren(QLabel))
        h2 = " ".join(x.text() for x in s.handle(2).findChildren(QLabel))
        self.assertIn("SOUNDBOX PLAYLISTS", h1)
        self.assertIn("DEVICES", h2)

    def test_sizes_persist_roundtrip(self):
        _win.resize(1100, 800)
        _app.processEvents()
        s = _win._sidebar_split
        s.setSizes([300, 200, 120])
        _win._save_columns()
        saved = list(_win.settings["sidebar_split"])
        self.assertEqual(len(saved), 3)
        s.setSizes([saved[0] + 80, max(48, saved[1] - 80), saved[2]])
        _win._restore_columns()
        self.assertEqual(s.sizes(), saved)

    def test_bad_saved_value_falls_back(self):
        _win.settings["sidebar_split"] = "garbage"
        _win._restore_columns()  # must not raise; classic proportions apply
        _win.settings.pop("sidebar_split", None)

    def test_devices_no_longer_fixed_height(self):
        self.assertGreater(_win.device_tree.maximumHeight(), 96)
        self.assertGreaterEqual(_win.device_tree.minimumHeight(), 48)

    def test_window_and_panes_restore_as_one_layout(self):
        original_geometry = _win.saveGeometry()
        original_setting = _win.settings.get("window_geometry")
        original_rect = _win.settings.get("window_rect")
        original_split = list(_win._sidebar_split.sizes())
        try:
            # Stay inside the offscreen platform's synthetic 800x800 display;
            # restoreGeometry intentionally clamps oversized windows.
            _win.resize(760, 700)
            _app.processEvents()
            saved_size = (_win.width(), _win.height())
            _win._sidebar_split.setSizes([280, 260, 96])
            _win._save_columns()
            saved_geometry = _win.settings["window_geometry"]
            saved_rect = list(_win.settings["window_rect"])
            saved_split = list(_win.settings["sidebar_split"])

            _win.resize(900, 650)
            _win._sidebar_split.setSizes([100, 100, 100])
            _win.settings["window_geometry"] = saved_geometry
            _win.settings["window_rect"] = saved_rect
            _win.settings["sidebar_split"] = saved_split
            _win._restore_window_geometry()
            _win._restore_columns()
            _app.processEvents()

            self.assertEqual((_win.width(), _win.height()), saved_size)
            self.assertEqual(_win._sidebar_split.sizes(), saved_split)
        finally:
            _win.restoreGeometry(original_geometry)
            _win._sidebar_split.setSizes(original_split)
            if original_setting is None:
                _win.settings.pop("window_geometry", None)
            else:
                _win.settings["window_geometry"] = original_setting
            if original_rect is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original_rect

    def test_corrupt_window_geometry_is_ignored(self):
        original = _win.settings.get("window_geometry")
        original_rect = _win.settings.get("window_rect")
        before = _win.size()
        try:
            _win.settings["window_rect"] = ["nope", 1, 2, 3]
            _win.settings["window_geometry"] = "not base64 geometry"
            _win._restore_window_geometry()  # must not raise or resize
            self.assertEqual(_win.size(), before)
        finally:
            if original is None:
                _win.settings.pop("window_geometry", None)
            else:
                _win.settings["window_geometry"] = original
            if original_rect is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original_rect

    def test_offscreen_window_rect_clamps_to_current_display(self):
        original = _win.settings.get("window_rect")
        try:
            _win.settings["window_rect"] = [99999, 99999, 900, 650]
            _win._restore_window_geometry()
            available = _app.primaryScreen().availableGeometry()
            self.assertTrue(available.contains(_win.frameGeometry().center()))
        finally:
            if original is None:
                _win.settings.pop("window_rect", None)
            else:
                _win.settings["window_rect"] = original


class ArtFetchHonesty(unittest.TestCase):
    """A broken fetch must say 'failed', never masquerade as 'no art found'."""

    def test_oneshot_passes_exception_through(self):
        from desktop.workers import _OneShotArt
        got = []
        def boom():
            raise RuntimeError("net down")
        w = _OneShotArt("1", boom)
        w.done.connect(lambda tid, res: got.append((tid, res)))
        w.run()  # call synchronously — we're testing the logic, not the thread
        self.assertEqual(got[0][0], "1")
        self.assertIsInstance(got[0][1], RuntimeError)

    def test_on_oneshot_distinguishes_failure_from_no_art(self):
        stored = []
        real = _win.art_cache
        class Cache(_FakeArt):
            def store_ok(self, tid, res):
                stored.append(tid)
        _win.art_cache = Cache({})
        try:
            _win._on_oneshot("1", RuntimeError("boom"))  # failure → no store, honest flash
            self.assertEqual(stored, [])
            self.assertIn("failed", _win.status_label.text().lower())
            _win._on_oneshot("1", None)                  # genuine miss keeps old message
            self.assertIn("No art found", _win.status_label.text())
        finally:
            _win.art_cache = real


class WaveformFill(unittest.TestCase):
    """Canonical V5 preparation is resumable, safe, and metadata-aware."""

    @classmethod
    def setUpClass(cls):
        import math, struct, tempfile, wave
        cls.dir = tempfile.mkdtemp(prefix="sb-wffill-")
        cls.paths = []
        sr = 8000
        for i in range(3):
            p = f"{cls.dir}/set{i}.wav"
            with wave.open(p, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(b"".join(
                    struct.pack("<h", int(0.6 * 32767 * math.sin(2*math.pi*220*t/sr)))
                    for t in range(sr)))  # 1s tone
            cls.paths.append(p)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        import tempfile
        from pathlib import Path
        from soundbox.library import Library, Track
        self.cache = tempfile.mkdtemp(prefix="sb-wfcache-")
        tracks = {str(i): Track(str(i), f"Set {i}", "A",
                                Path(p).as_uri(), bpm="140.00")
                  for i, p in enumerate(self.paths)}
        tracks["ghost"] = Track("ghost", "Gone", "A",
                                Path(f"{self.dir}/nope.wav").as_uri())
        self.lib = Library(tracks_by_id=tracks, playlists=[], source="x")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.cache, ignore_errors=True)

    def test_loaded_track_bypasses_bulk_playback_and_low_power_deferral(self):
        from desktop.workers import WaveformFillWorker

        worker = WaveformFillWorker(self.lib, Path(self.cache) / "track_analysis")
        worker.prioritize(self.paths[1])
        worker._active_task_path = self.paths[1]
        worker._playback_active.set()
        worker._power_policy = "low-power"
        worker.defer_for_interaction(60)
        self.assertTrue(worker._analysis_checkpoint())

    def test_prepares_canonical_v5_analysis_and_never_writes_audio(self):
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.track_analysis import load

        canonical = Path(self.cache) / "track_analysis"
        before = Path(self.paths[0]).read_bytes()
        WaveformFillWorker(self.lib, canonical).run()
        got = load(canonical, "0", self.paths[0])
        self.assertIsNotNone(got)
        self.assertEqual(
            got["_audio_detail"]["mip_divisors"],
            list(track_analysis.RANGE_MIP_DIVISORS))
        self.assertGreater(
            sum(got["_audio_detail"]["mip_counts"]), 500)
        self.assertEqual(Path(self.paths[0]).read_bytes(), before)

    def test_late_source_rewrite_is_detected_and_recooked(self):
        import math
        import struct
        import tempfile
        import wave
        from pathlib import Path
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis
        from soundbox.library import Library, Track

        with tempfile.TemporaryDirectory(prefix="sb-wf-rewrite-") as td:
            source = Path(td) / "new-track.wav"

            def write_tone(seconds):
                rate = 8000
                with wave.open(str(source), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(b"".join(
                        struct.pack(
                            "<h", int(0.5 * 32767 * math.sin(
                                2 * math.pi * 220 * frame / rate)))
                        for frame in range(rate * seconds)))

            write_tone(1)
            lib = Library(
                tracks_by_id={
                    "new": Track(
                        "new", "New", "A", source.as_uri(), bpm="140.00")},
                playlists=[], source="synthetic")
            canonical = Path(td) / "analysis"
            WaveformFillWorker(lib, canonical).run()
            self.assertTrue(track_analysis.is_ready(
                canonical, "new", source))

            # Rekordbox/tag tools may finish rewriting a newly landed container
            # after SoundBox's first cook. Its identity must become stale.
            write_tone(2)
            self.assertFalse(track_analysis.is_ready(
                canonical, "new", source))
            totals = []
            worker = WaveformFillWorker(lib, canonical)
            worker.tasks_ready.connect(totals.append)
            worker.run()
            self.assertEqual(totals, [1])
            self.assertTrue(track_analysis.is_ready(
                canonical, "new", source))
            self.assertTrue(track_analysis.verify_complete(
                canonical, "new", source))

    def test_worker_has_safe_stack_and_metadata_only_cue_refresh(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        track.source_cues = [{
            "kind": "hot", "slot": 0, "position_ms": 500,
            "end_ms": -1, "type": "single", "label": "DROP",
            "color": [255, 59, 125],
        }]
        track_analysis.store(canonical, "0", self.paths[0], {
            "detail_heights": b"\x20\x00" * 6000,
            "detail_rate": 6000.0,
            "overview_heights": b"\x20\x00",
            "overview_rgb": b"\x01\x02\x03",
            "spectral_bands": bytes(range(24)),
            "blue_detail": b"\x21",
            "rgb_detail": b"\xe0\x7c",
            "three_band": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
        })
        before = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        worker = WaveformFillWorker(self.lib, canonical)
        self.assertEqual(worker.stackSize(), worker.STACK_BYTES)
        ready = []
        worker.detail_ready.connect(ready.append)
        with patch(
                "soundbox.track_analysis.analyze_soundfile",
                side_effect=AssertionError("cue refresh decoded audio")):
            worker.run()
        after = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        self.assertEqual(after, before)
        self.assertEqual(ready, [self.paths[0]])
        self.assertEqual(
            track_analysis.load(
                canonical, "0", self.paths[0])["cues"],
            track.source_cues)

    def test_worker_refreshes_corrected_rekordbox_grid_without_audio_decode(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track = self.lib.tracks_by_id["0"]
        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only test fixture")
        track.source_analysis_path = str(dat)
        track.source_cues = []
        automatic = [[1, 250, 128.0], [2, 719, 128.0]]
        prepared = track_analysis.with_imported_cues({
            "detail_heights": b"\x20\x00" * 6000,
            "detail_rate": 6000.0,
            "overview_heights": b"\x20\x00",
            "overview_rgb": b"\x01\x02\x03",
            "spectral_bands": bytes(range(24)),
            "blue_detail": b"\x21",
            "rgb_detail": b"\xe0\x7c",
            "three_band": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
            "beats": automatic,
            "provenance": {
                "analysis": "rekordbox-import+soundbox",
                "grid": "rekordbox",
                "owner": "soundbox",
            },
        }, [])
        track_analysis.store(canonical, "0", self.paths[0], prepared)
        before = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        corrected = [[1, 375, 128.0], [2, 844, 128.0]]
        small_lib = type(self.lib)(
            tracks_by_id={"0": track}, playlists=[], source="x")
        worker = WaveformFillWorker(small_lib, canonical)
        totals = []
        ready = []
        worker.tasks_ready.connect(totals.append)
        worker.detail_ready.connect(ready.append)
        with patch(
                "soundbox.rekordbox_analysis.read_beats",
                return_value=corrected), patch(
                    "soundbox.track_analysis.analyze_soundfile",
                    side_effect=AssertionError("grid refresh decoded audio")):
            worker.run()
        after = track_analysis._read_container_manifest(
            canonical, "0", self.paths[0])[2]
        self.assertEqual(totals, [1])
        self.assertEqual(ready, [self.paths[0]])
        self.assertEqual(after, before)
        self.assertEqual(
            track_analysis.load(
                canonical, "0", self.paths[0])["beats"],
            corrected)

    def test_preparation_status_waits_until_scan_finds_real_work(self):
        from unittest.mock import patch

        saved = _win._preparing_library
        _win._preparing_library = False
        try:
            with patch.object(_win, "_start_pulse") as pulse:
                _win._on_analysis_tasks_ready(0)
                self.assertFalse(_win._preparing_library)
                pulse.assert_not_called()

                _win._on_analysis_tasks_ready(2)
                self.assertTrue(_win._preparing_library)
                pulse.assert_called_once_with("Preparing your library")
        finally:
            _win._preparing_library = saved

    def test_storage_guard_uses_pause_resume_hysteresis(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker

        canonical = Path(self.cache) / "track_analysis"
        canonical.mkdir()
        worker = WaveformFillWorker(self.lib, canonical)
        states = []
        worker.storage_paused.connect(
            lambda paused, free: states.append((paused, free)))
        free = [
            worker.MIN_FREE_BYTES - 1,
            worker.RESUME_FREE_BYTES,
        ]
        with patch(
                "shutil.disk_usage",
                side_effect=[
                    SimpleNamespace(free=value) for value in free
                ]), patch("time.sleep"):
            self.assertTrue(worker._wait_for_storage())
        self.assertEqual(states, [(True, free[0]), (False, free[1])])

    def test_hidden_tracks_remain_in_canonical_analysis(self):
        from desktop.workers import WaveformFillWorker
        from soundbox.track_analysis import is_ready
        canonical = Path(self.cache) / "track_analysis"
        self.lib.hidden_ids = {"1"}
        WaveformFillWorker(self.lib, canonical).run()
        self.assertTrue(is_ready(canonical, "1", self.paths[1]))
        self.assertTrue(is_ready(canonical, "0", self.paths[0]))

    def test_interrupted_decode_never_degrades_existing_v4(self):
        from pathlib import Path
        from unittest.mock import patch
        from desktop.workers import WaveformFillWorker
        from soundbox import track_analysis

        canonical = Path(self.cache) / "track_analysis"
        track_analysis.store(canonical, "0", self.paths[0], {
            "detail_heights": b"\x33" * 100,
            "detail_rate": 150.0,
            "overview_heights": b"\x40",
            "overview_rgb": b"\x02\x03\x04",
        })
        before = track_analysis.cache_path(
            canonical, "0").read_bytes()
        worker = WaveformFillWorker(self.lib, canonical)
        with patch(
                "soundbox.track_analysis.analyze_soundfile",
                return_value=None
        ), patch("desktop.player.qt_detail_peaks", return_value=None):
            worker.run()
        self.assertEqual(
            track_analysis.cache_path(canonical, "0").read_bytes(),
            before,
        )

    def test_autostart_wiring_starts_a_worker(self):
        real_dir = _win._track_analysis_dir
        _win._track_analysis_dir = Path(self.cache) / "track_analysis"
        saved_lib = _win.lib
        _win.lib = self.lib
        try:
            _win._maybe_fill_waveforms()
            self.assertIsNotNone(_win._wf_fill_worker)
            _win._wf_fill_worker.requestInterruption()
            _win._wf_fill_worker.wait(8000)
        finally:
            _win._track_analysis_dir = real_dir
            _win.lib = saved_lib

    def test_recurring_recheck_timer_is_bounded_and_single_shot(self):
        from desktop.app import _WAVEFORM_RECHECK_MS

        saved = _win._waveform_rechecks_enabled
        _win._waveform_recheck_timer.stop()
        try:
            _win._waveform_rechecks_enabled = True
            _win._schedule_waveform_recheck()
            self.assertTrue(_win._waveform_recheck_timer.isSingleShot())
            self.assertEqual(
                _win._waveform_recheck_timer.interval(),
                _WAVEFORM_RECHECK_MS)
            self.assertTrue(_win._waveform_recheck_timer.isActive())
        finally:
            _win._waveform_recheck_timer.stop()
            _win._waveform_rechecks_enabled = saved


class CollectionLandedOrder(unittest.TestCase):
    """Collection default order = when the FILE landed on the computer (newest
    first), unified across rekordbox + imported. Real files, staggered creation."""

    def setUp(self):
        import tempfile, time
        from pathlib import Path
        from soundbox.library import Library, Track
        self._td = tempfile.TemporaryDirectory()
        d = Path(self._td.name)
        self.paths = []
        for nm in ("old", "mid", "new"):
            p = d / f"{nm}.wav"
            p.write_bytes(b"x")
            self.paths.append(p)
            time.sleep(0.05)  # ensure distinct birth times
        tracks = {
            "1": Track("1", "Zzz Old", "A", self.paths[0].as_uri()),
            "2": Track("2", "Aaa Mid", "A", self.paths[1].as_uri()),
            "3": Track("3", "Mmm New", "A", self.paths[2].as_uri()),
        }
        self._saved_lib, self._saved_cache = _win.lib, _win._landed_cache
        _win._landed_cache = {}
        _win.lib = Library(tracks_by_id=tracks, playlists=[], source="x")

    def tearDown(self):
        _win.lib, _win._landed_cache = self._saved_lib, self._saved_cache
        self._td.cleanup()

    def test_newest_file_first_regardless_of_title(self):
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["3", "2", "1"])  # new, mid, old — not title order

    def test_landed_time_is_cached(self):
        _win._collection_tracks()
        self.assertIn(str(self.paths[0]), _win._landed_cache)

    def test_sorts_by_add_time_not_birthtime(self):
        # Sonny's case + the unified timeline: add-time (added_ts) drives the
        # order across BOTH sources, with time-of-day precision. An import with an
        # OLD file but a later add-time must sort above a rekordbox track with a
        # NEWER file but an earlier add-time.
        from soundbox.library import Track
        _win._landed_cache = {}
        _win.lib.tracks_by_id = {
            # oldest file (paths[0]) but newest add-time -> FIRST (a set imported now)
            "late_set": Track("late_set", "Set Imported Now", "Sonny",
                              self.paths[0].as_uri(),
                              added_ts=2_000.0, imported=True),
            # newest file (paths[2]) but earlier add-time -> LAST (a rekordbox track)
            "early_rb": Track("early_rb", "Rekordbox Track Earlier", "Sonny",
                             self.paths[2].as_uri(), added_ts=1_000.0),
        }
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["late_set", "early_rb"])  # by add-time, not file age

    def test_missing_add_time_falls_back_to_birthtime(self):
        # No added_ts (e.g. an XML-loaded library) -> file birthtime still orders.
        from soundbox.library import Track
        _win._landed_cache = {}
        _win.lib.tracks_by_id = {
            "1": Track("1", "Zzz Old", "A", self.paths[0].as_uri()),
            "3": Track("3", "Mmm New", "A", self.paths[2].as_uri()),
        }
        order = [t.track_id for t in _win._collection_tracks()]
        self.assertEqual(order, ["3", "1"])  # newer file first


class ArtistsDrag(unittest.TestCase):
    """Artists tab is a drag source: a track row carries its id; an artist row
    carries all its tracks — as `trk` mime a SoundBox playlist already accepts."""

    def setUp(self):
        _win.sb_tree.setCurrentItem(None)
        _win._show_artists()
        _app.processEvents()

    def test_artist_header_drags_all_its_tracks(self):
        from desktop.trees import ARTIST_ROLE
        top = _win.artists_view.topLevelItem(0)
        md = _win.artists_view.mimeData([top])
        self.assertTrue(md.text().startswith("trk\t"))
        ids = md.text().split("\t", 1)[1].split(",")
        self.assertEqual(sorted(ids), sorted(r["id"] for r in
                                             _win._artist_children[top.data(0, ARTIST_ROLE)]))

    def test_track_row_drags_its_own_id(self):
        from desktop.trees import ARTIST_TRACK_ROLE
        top = _win.artists_view.topLevelItem(0)
        top.setExpanded(True)  # lazy-fills children
        _app.processEvents()
        child = top.child(0)
        md = _win.artists_view.mimeData([child])
        self.assertEqual(md.text(), "trk\t" + child.data(0, ARTIST_TRACK_ROLE))

    def test_drop_onto_playlist_adds(self):
        from desktop.trees import ARTIST_IDS_ROLE
        _win.custom["ARTDROP"] = []
        try:
            top = _win.artists_view.topLevelItem(0)
            ids = list(top.data(0, ARTIST_IDS_ROLE))
            _win._on_tracks_dropped("ARTDROP", ids)
            self.assertEqual(_win.custom["ARTDROP"], ids)
        finally:
            _win.custom.pop("ARTDROP", None)
            _win._refresh_custom()


class ArtistsSearch(unittest.TestCase):
    """The search bar filters the Artists browser too, by the same multi-field
    query (artist name, or any of the artist's tracks)."""

    def setUp(self):
        _win.search.setText("")
        _win.sb_tree.setCurrentItem(None)
        _win._show_artists()
        _app.processEvents()

    def tearDown(self):
        _win.search.setText("")

    def _visible_artists(self):
        from desktop.trees import ARTIST_ROLE as ARTIST_ROLE_
        v = _win.artists_view
        return [v.topLevelItem(i).data(0, ARTIST_ROLE_)
                for i in range(v.topLevelItemCount()) if not v.topLevelItem(i).isHidden()]

    def test_filter_by_artist_name(self):
        _win.search.setText("ann")     # the synthetic lib has artist "Ann"
        _app.processEvents()
        vis = self._visible_artists()
        self.assertIn("Ann", vis)
        self.assertNotIn("Zed", vis)

    def test_blank_shows_all_artists(self):
        _win.search.setText("ann"); _app.processEvents()
        _win.search.setText("");    _app.processEvents()
        self.assertEqual(set(self._visible_artists()), {"Ann", "Mia", "Zed"})

    def test_switching_to_tab_applies_active_search(self):
        _win.search.setText("mia")
        _win._show_artists()           # re-enter the tab with a live query
        _app.processEvents()
        self.assertEqual(self._visible_artists(), ["Mia"])


class LibraryInstantPaint(unittest.TestCase):
    """The library snapshot round-trips through the app so open isn't blank."""

    def test_cache_library_then_load_restores_pool(self):
        import tempfile
        from pathlib import Path
        from soundbox import libcache
        with tempfile.TemporaryDirectory() as td:
            saved = _win._libcache_path
            _win._libcache_path = Path(td) / "libcache.json"
            try:
                _win._cache_library()
                for w in list(getattr(_win, "_oneshots", [])):
                    w.wait(4000)
                got = libcache.load(_win._libcache_path)
                self.assertIsNotNone(got)
                self.assertEqual(set(got.tracks_by_id), set(_win.lib.tracks_by_id))
            finally:
                _win._libcache_path = saved


class WaveformRapidLoad(unittest.TestCase):
    """Rapid track changes stay safe; canonical hits load synchronously."""

    @classmethod
    def setUpClass(cls):
        import math, struct, tempfile, wave
        from pathlib import Path
        cls.dir = tempfile.mkdtemp(prefix="sb-wfrapid-")
        cls.paths = []
        sr = 8000
        for i in range(6):
            p = Path(cls.dir) / f"t{i}.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                w.writeframes(b"".join(
                    struct.pack("<h", int(0.5 * 32767 * math.sin(2*math.pi*200*t/sr)))
                    for t in range(sr)))
            cls.paths.append(str(p))
        cls.cache = tempfile.mkdtemp(prefix="sb-wfrapid-cache-")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)
        shutil.rmtree(cls.cache, ignore_errors=True)

    def setUp(self):
        from desktop.player import PlayerBar
        self.pb = PlayerBar()

    def tearDown(self):
        self.pb.shutdown_waveforms()

    def test_canonical_v5_loads_inline_with_prebuilt_detail(self):
        from pathlib import Path
        from soundbox.track_analysis import COLOR_MODEL, store

        canonical = Path(self.cache) / "track_analysis"
        store(canonical, "v4", self.paths[0], {
            "detail_heights": b"\x20\x80\xff",
            "detail_rate": 6144.0,
            "overview_heights": b"\x40\xff",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "color_model": COLOR_MODEL,
            "beats": [[1, 625, 128.0]],
        })
        self.pb.track_analysis_dir = canonical
        self.pb._track_id = "v4"
        self.pb._src_path = self.paths[0]
        self.pb._pending_autoplay = True
        self.pb._start_waveform(self.paths[0])
        worker = self.pb._wf
        self.assertIsNotNone(worker)
        worker.wait(5000)
        _app.processEvents()
        self.assertIsNone(self.pb._wf)
        self.assertFalse(self.pb._pending_autoplay)
        self.assertIsNone(self.pb._pending_start_ms)
        self.assertEqual(self.pb._cue_ms, 0)
        self.assertEqual(self.pb.wave._beats[0][1], 0)
        self.assertEqual(list(self.pb.wave._audio_detail), [65534])
        self.assertEqual(self.pb.wave._audio_detail_rate, 768.0)

    def test_canonical_rekordbox_grid_keeps_exact_first_beat(self):
        from pathlib import Path
        from soundbox.track_analysis import COLOR_MODEL, store

        canonical = Path(self.cache) / "track_analysis"
        store(canonical, "rb-v4", self.paths[0], {
            "detail_heights": b"\x20\x80\xff",
            "detail_rate": 6144.0,
            "overview_heights": b"\x40\xff",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "color_model": COLOR_MODEL,
            "beats": [[1, 625, 128.0], [2, 1094, 128.0]],
            "provenance": {
                "analysis": "rekordbox-import+soundbox",
                "grid": "rekordbox",
                "owner": "soundbox",
            },
        })
        dat = Path(self.cache) / "ANLZ0000.DAT"
        dat.write_bytes(b"read-only marker")
        self.pb.track_analysis_dir = canonical
        self.pb._track_id = "rb-v4"
        self.pb._src_path = self.paths[0]
        self.pb._analysis_path = str(dat)
        self.pb._pending_autoplay = True
        self.pb._start_waveform(self.paths[0], str(dat))
        worker = self.pb._wf
        self.assertIsNotNone(worker)
        worker.wait(5000)
        _app.processEvents()
        self.assertIsNone(self.pb._wf)
        self.assertEqual(self.pb._pending_start_ms, 625)
        self.assertEqual(self.pb.wave._beats[0][1], 625)

    def test_rapid_switch_interrupts_previous(self):
        # Fire uncached decodes back to back; each new start interrupts the last.
        prev = None
        for p in self.paths:
            self.pb._src_path = p
            self.pb._start_waveform(p)
            if prev is not None and prev is not self.pb._wf:
                self.assertTrue(prev.isInterruptionRequested() or not _thread_alive(prev))
            prev = self.pb._wf
        self.pb.shutdown_waveforms()
        # every worker ref is cleaned up (no leaks, no running threads)
        _app.processEvents()
        self.assertTrue(all(not _thread_alive(w) for w in self.pb._wf_pending))


def _thread_alive(w):
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False


class RowCache(unittest.TestCase):
    """Computed rows are cached (instant playlist clicks), warmed off-thread,
    and invalidated when overrides/registry/junk change."""

    def setUp(self):
        _win._warm_timer.stop()
        _win._invalidate_rows()
        _win._warm_timer.stop()  # don't let the debounce fire during the test

    def tearDown(self):
        # test_warm_fills_every_track plants FAKE rows ({"id", "title": "x"}) in
        # the cache — a later class that renders a view from the cache would show
        # them (this latently broke SearchSelection once). Drop them.
        _win._row_cache.clear()
        _win._cache_gen += 1  # orphan any in-flight warm from this test
        _win._warm_timer.stop()

    def test_row_for_populates_and_reuses_cache(self):
        _win._row_cache.clear()
        t = _win.lib.tracks_by_id["1"]
        r1 = _win._row_for(t)
        self.assertIn("1", _win._row_cache)          # cached after first compute
        self.assertIsNot(r1, _win._row_cache["1"])   # returns a COPY (safe to mutate)
        r1["title"] = "MUT"
        self.assertNotEqual(_win._row_cache["1"]["title"], "MUT")  # cache untouched

    def test_invalidate_clears_and_bumps_generation(self):
        _win._row_for(_win.lib.tracks_by_id["1"])
        g = _win._cache_gen
        _win._invalidate_rows()
        self.assertEqual(_win._row_cache, {})
        self.assertEqual(_win._cache_gen, g + 1)
        _win._warm_timer.stop()

    def test_warm_fills_every_track(self):
        _win._row_cache.clear()
        built = {tid: {"id": tid, "title": "x"} for tid in _win.lib.tracks_by_id}
        _win._on_rows_warmed(built, _win._cache_gen)   # gen matches → applied
        self.assertEqual(set(_win._row_cache), set(_win.lib.tracks_by_id))

    def test_stale_warm_is_discarded(self):
        _win._row_cache.clear()
        stale_gen = _win._cache_gen - 1
        _win._on_rows_warmed({"1": {"id": "1"}}, stale_gen)
        self.assertEqual(_win._row_cache, {})          # discarded, not applied
        _win._warm_timer.stop()

    def test_title_edit_invalidates(self):
        _win._row_for(_win.lib.tracks_by_id["1"])
        _win._on_cell_edited("title", "1", "New Title")
        self.assertEqual(_win._row_cache, {})          # cache dropped on edit
        _win.titles.pop("1", None)
        _win._save_state()
        _win._warm_timer.stop()


class RefreshWhileViewingCollection(unittest.TestCase):
    """A live rekordbox refresh landing while Collection is on screen must
    re-render the visible rows in place — _refresh_custom restores the sidebar
    selection with signals BLOCKED, so without the explicit re-render in
    _apply_loaded_library a new rekordbox add stayed invisible until you
    clicked away and back (the real-world 'new songs missing' bug)."""

    def tearDown(self):
        # Full restore for later classes: fresh 3-track lib, tree rebuilt,
        # visible view re-rendered (see soundbox-gotchas on UI test hygiene).
        _win.lib = _make_lib()
        _win._build_artist_registry()
        _win._apply_settings_to_lib()
        _win._populate_tree(engine.summary(_win.lib))
        _win._reload_current_view()

    def _refresh_lands(self, fresh):
        """What _on_refresh_done does with a freshly read library (direct call —
        Qt slots swallow exceptions)."""
        _win.lib = fresh
        _win._apply_loaded_library()

    def test_new_track_appears_without_switching_views(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        self.assertEqual(len(_win.model._rows), 3)  # showing Collection
        fresh = _make_lib()
        fresh.tracks_by_id["4"] = Track("4", "Delta", "New",
                                        "file://localhost/tmp/d.wav",
                                        bpm="150.00", key="4A", total_time=200,
                                        date_added="2026-07-19")
        self._refresh_lands(fresh)
        _app.processEvents()
        self.assertIn("Delta", [r["title"] for r in _win.model._rows])
        self.assertIn("(4)", _win.sb_tree.topLevelItem(0).text(0))
        # Still parked on Collection — the refresh must not steal the view.
        from desktop.trees import COLLECTION_ROLE
        self.assertTrue(_win.sb_tree.currentItem().data(0, COLLECTION_ROLE))

    def test_selected_track_keeps_its_seat(self):
        _win.sb_tree.setCurrentItem(_win.sb_tree.topLevelItem(0))
        _app.processEvents()
        row = next(i for i, r in enumerate(_win.model._rows) if r["id"] == "3")
        _win.table.setCurrentIndex(_win.model.index(row, 0))
        self._refresh_lands(_make_lib())
        _app.processEvents()
        idx = _win.table.currentIndex()
        self.assertTrue(idx.isValid())
        self.assertEqual(_win.model._rows[idx.row()]["id"], "3")
