"""Soundbox desktop app (PySide6) — native rekordbox → Rockbox/iPod exporter.

Flow:
  • On launch the LIVE rekordbox database is read (read-only snapshot; an XML
    export is the fallback via the REKORDBOX header menu).
  • Browse rekordbox playlists (accordion), the built-in Collection view, or
    custom SoundBox playlists (drag tracks from the table into them).
  • DRAG a playlist/folder onto an external device → it exports IMMEDIATELY.
    One EXPORTING bar aggregates every queued playlist, including drops made
    mid-export. Devices track drift: any library change to an exported playlist
    (rekordbox edits, SoundBox deletes/removals) shows "⚠ out of date" → Sync.
  • Curation: inline title/artist edits (⌘R / right-click), cover-art column +
    picker, artist merge + suggestion windows, per-track delete/remove with a
    restorable Graveyard, full-library .zip backup/restore in Settings.

Always tree layout (folders on device, exact names), artists derived, titles
tidied per user rules, AppleDouble cleaned — standard, not options. Only
external/ejectable volumes are ever shown as targets. rekordbox and source
audio files are NEVER modified (see soundbox.sync safety contract).
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths

LOG = logging.getLogger("soundbox")


def setup_logging() -> Path | None:
    """Console + rotating-ish file log at ~/Library/Logs/Soundbox/soundbox.log."""
    if LOG.handlers:
        return None
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOG.addHandler(sh)
    logpath = None
    try:
        d = Path.home() / "Library" / "Logs" / "Soundbox"
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(d / "soundbox.log")
        fh.setFormatter(fmt)
        LOG.addHandler(fh)
        logpath = d / "soundbox.log"
    except OSError:
        pass

    def _hook(t, v, tb):
        LOG.error("UNCAUGHT EXCEPTION", exc_info=(t, v, tb))
        sys.__excepthook__(t, v, tb)

    sys.excepthook = _hook
    try:
        # C-level crashes (Qt fatals, codec faults) dump Python stacks into the
        # log too — otherwise a native abort leaves nothing to debug from.
        import faulthandler
        faulthandler.enable(open(d / "crash-stacks.log", "a"))
    except OSError:
        pass
    # PACKAGED app (Finder launch): stderr goes NOWHERE, and PySide prints
    # swallowed slot exceptions to stderr — so a broken button clicks silently.
    # Route stderr into the log so every field failure leaves a trace.
    if getattr(sys, "frozen", False):
        try:
            sys.stderr = sys.stdout = open(d / "soundbox.log", "a", buffering=1)
        except OSError:
            pass
    return logpath

from PySide6.QtCore import QEvent, QPointF, QProcess, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QLineEdit,
    QFileDialog, QFileSystemModel, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTableView, QTreeView, QTreeWidgetItem, QVBoxLayout, QWidget,
)


from soundbox import artfetch, engine, imports, libcache, metadata, paths, rekordbox_db
from soundbox.device_profile import plan_stock_ipod_export
from soundbox.rekordbox_xml import Playlist
from . import theme
from .artpicker import ArtPickerDialog
from .model import (
    COLUMNS, TrackModel, row_display_value, row_matches as _row_matches,
)
from .player import PlayerBar

from .device_browser import DeviceBrowserMixin
from .devices import device_fs, human
from .settings_page import SettingsPageMixin
from .trees import (
    ARTIST_IDS_ROLE, ARTIST_ROLE, ARTIST_TRACK_ROLE, ARTISTS_ROLE, COLLECTION_ROLE,
    CUSTOM_ROLE, DEVPATH_ROLE, PATH_ROLE,
    ArtistsTree, CellEditDelegate, DeviceTree, PlaylistTree, SoundBoxTree, TrackTable,
)

from .workers import (
    ArtFillWorker, DeleteWorker, DeviceScanWorker, ExportWorker,
    RefreshWorker, ResyncWorker, WaveformFillWorker, _OneShotArt, _running,
)


# Wordmark as ASCII art (SOUND = white, BOX = purple), shown in the top bar.
SOUND_ART = r"""
 ____   ___  _   _ _   _ ____
/ ___| / _ \| | | | \ | |  _ \
\___ \| | | | | | |  \| | | | |
 ___) | |_| | |_| | |\  | |_| |
|____/ \___/ \___/|_| \_|____/
""".strip("\n")

BOX_ART = r"""
 ____   _____  __
| __ ) / _ \ \/ /
|  _ \| | | \  /
| |_) | |_| /  \
|____/ \___/_/\_\
""".strip("\n")


# Persisted app settings + their defaults (both-on/5s reproduces the original single
# "hide cue tracks" behavior). Merged over saved values in _load_state.
_DEFAULT_SETTINGS = {
    "hide_untracked": False,   # master: hide rekordbox cue / placeholder tracks
    "hide_nokey": True,        # sub-rule: among no-BPM, hide the keyless ones
    "hide_short": True,        # sub-rule: among no-BPM, hide very short stubs
    "hide_short_secs": 5,      # the "shorter than" threshold (seconds)
    "title_rules_off": [],     # ids of built-in title-cleanup rules turned OFF
    "title_words": [],         # user's custom drop-words (safe end/bracket semantics)
    "import_dir": "",          # last folder used by Import Audio Files…
    "beat_jump_mode": "16 BEATS",
    "waveform_zoom": 92.0,
    "waveform_mode": "3BAND",
    "autoplay_mode": "",
}

_AUTOPLAY_MODES = {"up", "random", "down"}


def _autoplay_arrow_icon(direction: int) -> QIcon:
    """Small Retina-sharp triangle matching the deck's Play glyph."""
    pixmap = QPixmap(24, 16)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#f5f6fa"))
    points = (
        [QPointF(2.0, 6.0), QPointF(6.0, 2.0), QPointF(10.0, 6.0)]
        if direction < 0
        else [QPointF(2.0, 2.0), QPointF(10.0, 2.0), QPointF(6.0, 6.0)]
    )
    painter.drawPolygon(QPolygonF(points))
    painter.end()
    return QIcon(pixmap)

class MainWindow(SettingsPageMixin, DeviceBrowserMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Soundbox")
        self.resize(1240, 800)
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        self._data_file = data_dir / "library.json"  # portable SoundBox library
        self._libcache_path = data_dir / "libcache.json"  # last lib snapshot (instant paint)
        self.art_cache = artfetch.ArtCache(data_dir / "art")  # cover-art cache
        # One-time Rockbox-safety sweep: any cached art that isn't baseline JPEG
        # (progressive/PNG shows BLANK on the iPod) is re-encoded. Flag-filed —
        # a no-op on every launch after the first, so it's fine on the main thread
        # (local SSD, not a removable device).
        checked, fixed = self.art_cache.normalize_all()
        if checked:
            LOG.info("art cache sweep: %d checked, %d re-encoded to baseline JPEG", checked, fixed)
        self._art_worker: ArtFillWorker | None = None
        self._art_restart = False  # set by re-scan to restart the fill after it stops
        self._wf_fill_worker: WaveformFillWorker | None = None
        self._track_analysis_dir = data_dir / "track_analysis"
        self._preparing_library = False
        self._prep_power_policy = "ac"
        self._landed_cache: dict[str, float] = {}  # path -> file-landing time
        self._last_xml = ""
        self.lib = None
        self.custom: dict[str, list[str]] = {}  # name -> ordered track ids
        self.imported: dict[str, dict] = {}  # sb-… id -> imported-file record
        self.overrides: dict[str, str] = {}  # track_id -> manual artist override
        self.titles: dict[str, str] = {}     # track_id -> manual title override
        self.hidden: set[str] = set()        # deleted-from-SoundBox track ids
        self.hidden_dates: dict[str, str] = {}   # tid -> "YYYY-MM-DD" (graveyard)
        self.removals: dict[str, dict] = {}  # pid -> {tid: removed-date}
        self.aliases: dict[str, str] = {}    # artist merges: variant -> target
        self.settings: dict = dict(_DEFAULT_SETTINGS)
        self.export_worker: ExportWorker | None = None
        self._backup_worker = None  # OneShotTask for backup zip / restore
        self._refresh_worker: RefreshWorker | None = None
        self._resync_worker: ResyncWorker | None = None
        self._dev_scan_worker: DeviceScanWorker | None = None
        self._delete_worker: DeleteWorker | None = None
        self._eject_verify_worker = None
        self._device_import_worker = None
        self._drift_cache: dict[str, int] = {}  # device path -> #playlists out of date
        self._device_profiles: dict[str, object] = {}
        self._sync_playlists_cache: list[Playlist] | None = None
        # Autoplay owns a frozen snapshot of the view that supplied the
        # deliberately started track. Browsing elsewhere never mutates it.
        self._autoplay_mode = ""
        self._autoplay_order: list[str] = []
        self._autoplay_index: int | None = None
        self._autoplay_random_seen: set[str] = set()
        self._autoplay_random_queue: list[int] = []
        self._autoplay_rng = random.Random()
        # Computed-row cache: track_id -> row dict (effective title/artist +
        # metadata). effective_title is ~0.17ms/track, so recomputing a 2500-
        # track playlist on every click cost ~440ms — this makes clicks instant.
        # Warmed in the background on open; invalidated (with a gen counter to
        # discard stale warms) whenever overrides/aliases/registry/junk change.
        self._row_cache: dict[str, dict] = {}
        self._cache_gen = 0
        self._warm_worker = None
        self._exporting = False
        self._deleting = False
        self._accordion_guard = False
        self._build_ui()
        QApplication.instance().installEventFilter(self)  # Space transport + C cue
        # Animated dots for transient states (FINALIZING…, Cancelling…).
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(300)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_word: str | None = None
        self._pulse_dots = 0
        self._cancelling = False
        # Debounce row-cache warming: many invalidations in one action (load →
        # build registry → apply settings) collapse into ONE background rebuild.
        self._warm_timer = QTimer(self)
        self._warm_timer.setSingleShot(True)
        self._warm_timer.setInterval(120)
        self._warm_timer.timeout.connect(self._do_warm)
        # Devices are recognized on launch and as they're plugged in — no XML needed.
        self._refresh_devices()
        self._dev_timer = QTimer(self)
        self._dev_timer.timeout.connect(self._refresh_devices)
        self._dev_timer.start(3000)
        self._load_state()  # restore custom playlists + last-opened XML
        self.table.setFocus()  # open browsing (transport keys, arrows), not typing

    # ---------- persistence ----------
    def _load_state(self):
        data = {}
        if self._data_file.exists():
            try:
                data = json.loads(self._data_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = self._recover_state()
        self.custom = data.get("custom") or {}
        self.imported = data.get("imported") or {}  # sb-… id -> file record
        self.overrides = data.get("artist_overrides") or {}
        self.titles = data.get("title_overrides") or {}
        raw_hidden = data.get("hidden_tracks") or {}
        # v1 stored a bare list (no dates); v2 stores {tid: deleted-date}.
        self.hidden_dates = ({i: "" for i in raw_hidden} if isinstance(raw_hidden, list)
                             else dict(raw_hidden))
        self.hidden = set(self.hidden_dates)
        self.removals = {pid: ({i: "" for i in v} if isinstance(v, list) else dict(v))
                         for pid, v in (data.get("playlist_removals") or {}).items()}
        self.aliases = data.get("artist_aliases") or {}
        self._last_xml = data.get("last_xml") or ""
        # User settings (extensible — this is the runway for future toggles).
        self.settings = {**_DEFAULT_SETTINGS, **(data.get("settings") or {})}
        self.settings.pop("title_junk", None)  # v1 raw-pattern list — superseded
        self.settings.pop("deck_engine", None)  # retired Qt playback preference
        # Geometry must land before the splitter sizes. Restoring the panes into
        # the old hard-coded 1240x800 frame made Qt compress them, then resizing
        # the window redistributed them a second time.
        self._restore_window_geometry()
        self.player_bar.set_jump_mode(
            str(self.settings.get("beat_jump_mode", "16 BEATS")), emit=False)
        self.player_bar.set_waveform_zoom(
            float(self.settings.get("waveform_zoom", 92.0)), emit=False)
        self.player_bar.set_waveform_mode(
            str(self.settings.get("waveform_mode", "3BAND")))
        self._set_autoplay_mode(
            str(self.settings.get("autoplay_mode", "")), save=False)
        self._sync_settings_controls()  # reflect saved values in the Settings page
        self._restore_columns()         # saved column widths/visibility
        self._refresh_custom()
        # Instant paint: render last session's library snapshot NOW so the app
        # isn't blank while the live read runs. The refresh below replaces it
        # with current data (adds/edits/deletes apply) and re-snapshots.
        cached = libcache.load(self._libcache_path)
        if cached is not None:
            self.lib = cached
            self._apply_loaded_library()
            self.stack.setCurrentIndex(0)
            LOG.info("Instant paint from snapshot: %d tracks", self.lib.track_count)
        # Auto-load on launch: live rekordbox DB first, else the last XML
        # fallback. This runs REGARDLESS of the snapshot paint above — the
        # snapshot is a stand-in until the authoritative source loads over it.
        if rekordbox_db.db_available():
            self._refresh_rekordbox()
        elif self._last_xml and Path(self._last_xml).is_file():
            self.open_library(self._last_xml)

    def _recover_state(self) -> dict:
        """library.json was unreadable. NEVER silently continue empty (the next
        save would bury weeks of curation) — restore the newest parseable
        rotating backup, keep the damaged file aside for forensics, and say so."""
        corrupt = self._data_file.with_name(
            f"library.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            self._data_file.rename(corrupt)
        except OSError:
            pass
        bdir = self._data_file.parent / "backups"
        candidates = sorted(bdir.glob("*.json"), reverse=True) if bdir.is_dir() else []
        for bk in candidates:
            try:
                data = json.loads(bk.read_text(encoding="utf-8"))
                assert isinstance(data, dict)
            except (ValueError, OSError, AssertionError):
                continue
            LOG.error("library.json was corrupt — restored backup %s "
                      "(damaged file kept as %s)", bk.name, corrupt.name)
            QMessageBox.warning(
                self, "Library recovered",
                f"library.json couldn't be read, so the newest good backup "
                f"({bk.name}) was restored.\n\nThe damaged file was kept as "
                f"{corrupt.name}. Recent edits since that backup may be missing.")
            return data
        LOG.error("library.json corrupt and NO usable backup — starting empty "
                  "(damaged file kept as %s)", corrupt.name)
        QMessageBox.warning(
            self, "Library unreadable",
            "library.json couldn't be read and no usable backup was found.\n\n"
            f"Starting fresh; the damaged file was kept as {corrupt.name} — "
            "you can also use Settings → Restore from backup.")
        return {}

    def _backup_state(self):
        """Rotating safety copies of library.json (curation is hours of work).
        At most one per hour, skipped when nothing changed, newest 10 kept —
        so the history spans days of curating, not one burst of edits."""
        try:
            if not self._data_file.exists():
                return
            bdir = self._data_file.parent / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            cur = self._data_file.read_bytes()
            old = sorted(bdir.glob("library-*.json"))
            if old and (time.time() - old[-1].stat().st_mtime < 3600
                        or old[-1].read_bytes() == cur):
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest, i = bdir / f"library-{stamp}.json", 1
            while dest.exists():  # two saves in one second must not overwrite
                dest, i = bdir / f"library-{stamp}-{i}.json", i + 1
            dest.write_bytes(cur)
            for p in sorted(bdir.glob("library-*.json"))[:-10]:
                p.unlink(missing_ok=True)
        except OSError:
            pass  # a backup hiccup must never block the real save

    def _save_state(self):
        """Write the portable SoundBox library (overrides + custom playlists)."""
        self._sync_playlists_cache = None
        self._backup_state()  # snapshot the previous good state first
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic (temp + rename): a crash mid-save must never corrupt the
            # library — it holds every override, playlist, delete and setting.
            tmp = self._data_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "custom": self.custom,
                "imported": self.imported,
                "artist_overrides": self.overrides,
                "title_overrides": self.titles,
                "hidden_tracks": {i: self.hidden_dates.get(i, "") for i in sorted(self.hidden)},
                "playlist_removals": {k: dict(v) for k, v in self.removals.items() if v},
                "artist_aliases": self.aliases,
                "last_xml": self._last_xml,
                "settings": self.settings,
            }, indent=2), encoding="utf-8")
            os.replace(tmp, self._data_file)
        except OSError as exc:
            LOG.error("Could not save library.json: %s", exc)

    # ---------- settings ----------

    def _build_artist_registry(self):
        """Canonical-artist map, rebuilt each load: dedups spellings (DENNETT/
        dennett -> one artist, most-tracks spelling wins, collabs kept separate),
        rescues Unknown tracks from a known artist in the title, and gates the
        reversed 'TITLE - ARTIST' flip on TRUSTED names — rekordbox tags plus the
        user's own overrides, so every manual fix teaches the deriver."""
        if self.lib is None:
            return
        bases = [metadata.derive_artist(t.name, t.artist)[0]
                 for t in self.lib.tracks_by_id.values()]
        trusted = ([t.artist for t in self.lib.tracks_by_id.values() if t.artist]
                   + [v for v in self.overrides.values() if v.strip()])
        self.lib.artist_registry = metadata.build_registry(bases, trusted, self.aliases)
        self._invalidate_rows()  # canonical spellings may have changed

    def _default_to_collection(self):
        """On open (or any load with nothing selected), land on the Collection
        view instead of an empty table."""
        if (self.tree.currentItem() is None
                and self.sb_tree.currentItem() is None
                and self.sb_tree.topLevelItemCount()):
            self.sb_tree.setCurrentItem(self.sb_tree.topLevelItem(0))

    def _annotate_art(self, rows: list[dict]) -> list[dict]:
        """Stamp each row with its art-cache status ('cover'/'avatar'/'miss'/'')
        for the Art column."""
        for r in rows:
            r["art"] = self.art_cache.status_kind(r.get("id", ""))
        return rows

    # ---------- computed-row cache (fast playlist clicks) ----------
    def _row_for(self, t) -> dict:
        """Cached computed row for a track, recomputed only when its inputs
        change (see _invalidate_rows). Returns a COPY so the caller can stamp
        art / build a search haystack without poisoning the cache."""
        r = self._row_cache.get(t.track_id)
        if r is None:
            r = engine.track_row(t, self.overrides, self.titles,
                                 registry=self.lib.artist_registry if self.lib else None,
                                 junk=self._title_junk())
            self._row_cache[t.track_id] = r
        return dict(r)

    def _rows_for(self, tracks) -> list[dict]:
        return [self._row_for(t) for t in tracks]

    def _playlist_rows(self, display_path: str) -> list[dict]:
        """Rows for a rekordbox playlist, in sacred order — via the row cache."""
        pl = next((p for p in self.lib.playlists if p.display_path == display_path),
                  None) if self.lib else None
        return self._rows_for(self.lib.resolve(pl)) if pl is not None else []

    def _invalidate_rows(self) -> None:
        """Something that feeds track_row changed (an override, alias, the
        registry, or title-junk) — drop the cache, bump the generation so any
        in-flight warm is discarded, and schedule a fresh background warm."""
        self._row_cache.clear()
        self._sync_playlists_cache = None
        self._cache_gen += 1
        if self.lib is not None:
            self._warm_timer.start()  # debounced → one rebuild per burst

    def _do_warm(self) -> None:
        """Precompute EVERY track's row off the UI thread so the first click on
        any playlist is already instant — no waiting. Single-flight; a stale
        result (generation moved on) is discarded and re-warmed."""
        if self.lib is None or _running(self._warm_worker):
            return
        from .workers import OneShotTask
        gen = self._cache_gen
        lib, ov, ti, junk = self.lib, dict(self.overrides), dict(self.titles), self._title_junk()
        reg = lib.artist_registry
        tracks = list(lib.tracks_by_id.values())

        def build():
            return {t.track_id: engine.track_row(t, ov, ti, registry=reg, junk=junk)
                    for t in tracks}

        w = OneShotTask(build)
        self._warm_worker = w
        w.done.connect(lambda res, g=gen: self._on_rows_warmed(res, g))
        w.finished.connect(lambda ww=w: self._finish_worker("_warm_worker", ww))
        w.start()

    def _on_rows_warmed(self, res, gen: int) -> None:
        if isinstance(res, Exception):
            LOG.warning("Row-cache warm failed: %s", res)
            return
        if gen != self._cache_gen:
            self._warm_timer.start()  # inputs changed mid-warm — rebuild
            return
        self._row_cache = res
        LOG.info("Row cache warmed: %d rows", len(res))

    def _reload_current_view(self):
        """Re-render whatever playlist is showing so a settings change takes
        effect immediately. Updates the table rows only — does NOT switch the
        visible page (so toggling on the Settings page keeps you there)."""
        if self.lib is None:
            return
        cur = self.tree.currentItem()
        if cur is not None and cur.data(0, PATH_ROLE):
            self.model.set_rows(self._annotate_art(
                self._playlist_rows(cur.data(0, PATH_ROLE))))
            return
        cur = self.sb_tree.currentItem()
        if cur is not None and cur.data(0, COLLECTION_ROLE):
            self._show_collection()
            return
        if cur is not None and cur.data(0, ARTISTS_ROLE):
            self._show_artists()
            return
        if cur is not None and cur.data(0, CUSTOM_ROLE):
            tracks = [t for i in self.custom.get(cur.data(0, CUSTOM_ROLE), [])
                      if i not in self.hidden
                      and (t := self.lib.tracks_by_id.get(i))]
            self.model.set_rows(self._annotate_art(self._rows_for(tracks)))

    # ---------- player ----------
    def eventFilter(self, obj, ev):
        if (getattr(self, "now_playing", None) is obj
                and ev.type() == QEvent.MouseButtonRelease
                and ev.button() == Qt.LeftButton):
            self._focus_now_playing()
            return True

        # Space and C are deck controls only from browsing surfaces. Editors,
        # search, settings trees, dialogs, and the device browser keep their
        # native text/toggle behavior. Modifier chords (Cmd+C) are never stolen.
        if ev.type() in (
                QEvent.Wheel, QEvent.Resize, QEvent.ApplicationActivate,
                QEvent.ApplicationDeactivate):
            self._defer_library_prep(
                1.0 if ev.type() in (
                    QEvent.ApplicationActivate,
                    QEvent.ApplicationDeactivate) else 0.75)
        elif (ev.type() == QEvent.MouseMove
              and getattr(ev, "buttons", lambda: Qt.NoButton)()
              & Qt.LeftButton):
            self._defer_library_prep()

        if ev.type() == QEvent.ApplicationActivate:
            self.player_bar.resynchronize_transport()
            return super().eventFilter(obj, ev)
        if ev.type() == QEvent.ApplicationDeactivate:
            if getattr(self, "_cue_key_down", False):
                self._cue_key_down = False
                self.player_bar._cue_release()
            return super().eventFilter(obj, ev)
        if ev.type() not in (
                QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            return super().eventFilter(obj, ev)
        focus = QApplication.focusWidget()
        no_modifier = ev.modifiers() == Qt.NoModifier
        browsing = self._space_should_play(focus)
        if (ev.key() == Qt.Key_C and no_modifier and browsing
                and ev.type() == QEvent.ShortcutOverride):
            # Cocoa's press-and-hold text service asks for this override before
            # it sends repeat key presses. Claiming it here prevents the
            # ç/ć/č/ċ character panel from ever opening over the browser.
            ev.accept()
            return True
        if (ev.key() == Qt.Key_Space and no_modifier
                and ev.type() == QEvent.KeyPress and not ev.isAutoRepeat()):
            if browsing:
                self._toggle_player()
                return True
        if ev.key() == Qt.Key_C:
            if ev.type() == QEvent.KeyRelease and getattr(
                    self, "_cue_key_down", False):
                self._cue_key_down = False
                self.player_bar._cue_release()
                return True
            if no_modifier and browsing and ev.type() == QEvent.KeyPress:
                if (not ev.isAutoRepeat()
                        and not getattr(self, "_cue_key_down", False)):
                    self._cue_key_down = True
                    self.player_bar._cue_press()
                return True
        return super().eventFilter(obj, ev)

    def _defer_library_prep(self, seconds: float = 0.75):
        worker = getattr(self, "_wf_fill_worker", None)
        if worker is not None:
            worker.defer_for_interaction(seconds)

    def _space_should_play(self, w) -> bool:
        """Focus policy for the Space shortcut (kept separate so it's directly
        testable — offscreen focus doesn't propagate reliably)."""
        return w is None or w in (self.table, self.tree, self.sb_tree,
                                  self.device_tree, self.artists_view)

    def _remember_current(self, idx, _prev=None):
        """Sticky memory of the selected track id — survives filters/sorts that
        temporarily hide or reshuffle it."""
        if idx.isValid() and idx.row() < len(self.model._rows):
            tid = self.model._rows[idx.row()].get("id")
            if tid:
                self._keep_tid = tid

    def _reseat_selection(self):
        """Put the remembered track back under the cursor after the model
        reshuffled (sort toggled, search changed) — no more selection oblivion."""
        keep = getattr(self, "_keep_tid", None)
        if not keep:
            return
        for r, row in enumerate(self.model._rows):
            if row.get("id") == keep:
                cur = self.table.currentIndex()
                col = cur.column() if cur.isValid() else 0
                self.table.setCurrentIndex(self.model.index(r, col))
                self.table.scrollTo(self.model.index(r, 0))
                return

    def _on_search(self, text: str):
        """Filter EVERY track browser at once — the table (Collection/playlist
        views) and the Artists tree — by the same multi-field query."""
        self.model.set_filter(text)
        self._reseat_selection()
        self._filter_artists()

    def _search_terms(self) -> list[str]:
        return self.search.text().strip().lower().split()

    def _filter_artists(self) -> None:
        """Show an artist in the Artists browser when the query matches the
        artist OR any of their tracks (same _haystack the table search uses, so
        artist/genre/key/bpm/format all count). Expanded rows also hide their
        non-matching tracks."""
        terms = self._search_terms()
        children = getattr(self, "_artist_children", {})
        for i in range(self.artists_view.topLevelItemCount()):
            it = self.artists_view.topLevelItem(i)
            rows = children.get(it.data(0, ARTIST_ROLE), [])
            has = (not terms) or any(_row_matches(r, terms) for r in rows)
            it.setHidden(not has)
            if it.childCount():
                self._apply_artist_child_filter(it)
        self._update_player_navigation()

    def _apply_artist_child_filter(self, item) -> None:
        terms = self._search_terms()
        rows = {r["id"]: r for r in
                getattr(self, "_artist_children", {}).get(item.data(0, ARTIST_ROLE), [])}
        for j in range(item.childCount()):
            c = item.child(j)
            r = rows.get(c.data(0, ARTIST_TRACK_ROLE))
            c.setHidden(bool(terms) and r is not None and not _row_matches(r, terms))

    def _selected_track(self):
        # Artists page showing? — the highlighted song there is the selection.
        if self.stack.currentWidget() is self.artists_view:
            it = self.artists_view.currentItem()
            tid = it.data(0, ARTIST_TRACK_ROLE) if it is not None else None
            return self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None
        idx = self.table.currentIndex()
        if not idx.isValid() or idx.row() >= len(self.model._rows):
            return None
        tid = self.model._rows[idx.row()].get("id")
        return self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None

    def _toggle_player(self):
        """Space controls only the loaded deck; browser selection is irrelevant."""
        if self.player_bar.current_track():
            self.player_bar.toggle()

    def _play_row(self, index):
        """Double-click a row → load that track without starting playback."""
        if not index.isValid() or index.row() >= len(self.model._rows):
            return
        tid = self.model._rows[index.row()].get("id")
        t = self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None
        if t is not None:
            self._load_track(t)

    def _on_playback_error(self, msg: str):
        LOG.warning("Playback error: %s", msg)
        self._flash_status("Playback error", 6)

    def _player_order(self) -> list[str]:
        """Track ids in the order the user can currently see.

        Navigation follows the active filter/sort, which makes the transport
        arrows predictable: "next" means the next row on screen. The Artists
        browser is flattened artist-by-artist in its visible order.
        """
        if self.lib is None:
            return []
        if self.stack.currentWidget() is self.artists_view:
            terms = self._search_terms()
            out: list[str] = []
            children = getattr(self, "_artist_children", {})
            for i in range(self.artists_view.topLevelItemCount()):
                item = self.artists_view.topLevelItem(i)
                if item.isHidden():
                    continue
                for row in children.get(item.data(0, ARTIST_ROLE), []):
                    if not terms or _row_matches(row, terms):
                        tid = row.get("id")
                        if tid:
                            out.append(tid)
            return out
        return [r["id"] for r in self.model._rows if r.get("id")]

    def _update_player_navigation(self):
        """Enable track arrows only when a visible adjacent track exists."""
        pb = getattr(self, "player_bar", None)
        if pb is None:
            return
        tid = pb.current_track()
        order = self._player_order() if tid else []
        try:
            idx = order.index(tid)
        except ValueError:
            pb.set_track_navigation(False, False)
            return
        pb.set_track_navigation(idx > 0, idx + 1 < len(order))

    def _play_relative(self, direction: int):
        """Play the previous/next visible track without wrapping at the ends."""
        if self.lib is None:
            return
        order = self._player_order()
        try:
            idx = order.index(self.player_bar.current_track())
        except ValueError:
            self._update_player_navigation()
            return
        target = idx + (-1 if direction < 0 else 1)
        if not 0 <= target < len(order):
            self._update_player_navigation()
            return
        track = self.lib.tracks_by_id.get(order[target])
        if track is not None:
            self._play_track(track)

    def _load_track(self, t):
        """Load a track paused at its first grid beat."""
        self._open_track(t, autoplay=False, capture_autoplay=True)

    def _play_track(self, t):
        """Load a track and start it at its first grid beat."""
        self._open_track(t, autoplay=True, capture_autoplay=True)

    def _open_track(
            self, t, autoplay: bool,
            capture_autoplay: bool = True) -> bool:
        try:
            ok = t.path.is_file()
        except OSError:
            ok = False
        if not ok:
            self._flash_status("File not on disk", 5)
            return False
        if capture_autoplay:
            self._capture_autoplay_context(t.track_id)
        src = self.titles.get(t.track_id) or t.name
        artist = metadata.effective_artist(t.track_id, src, t.artist, self.overrides,
                                           True, self.lib.artist_registry if self.lib else None,
                                           stem=t.path.stem)
        title = metadata.effective_title(t.track_id, t.name, artist, self.titles,
                                         self._title_junk())
        label = f"{artist} — {title}" if artist and artist != "Unknown Artist" else title
        self.player_bar.play_track(
            t.track_id, t.path, title=label, bpm=t.bpm,
            analysis_path=getattr(t, "analysis_path", ""),
            autoplay=autoplay,
            cues=getattr(t, "rekordbox_cues", None))
        self._highlight_now_playing(t.track_id)
        self._update_player_navigation()
        return True

    def _capture_autoplay_context(self, track_id: str):
        """Freeze the supplying playlist/view while the user browses elsewhere."""
        order = self._player_order()
        try:
            index = order.index(track_id)
        except ValueError:
            order = [track_id]
            index = 0
        self._autoplay_order = list(order)
        self._autoplay_index = index
        self._autoplay_random_seen = {track_id}
        self._autoplay_random_queue = []

    def _autoplay_playable(self, index: int) -> bool:
        if self.lib is None or not 0 <= index < len(self._autoplay_order):
            return False
        track = self.lib.tracks_by_id.get(self._autoplay_order[index])
        if track is None:
            return False
        try:
            return track.path.is_file()
        except OSError:
            return False

    def _next_autoplay_index(self) -> int | None:
        """Return one valid next index, with shuffle-bag random semantics."""
        if self._autoplay_index is None or not self._autoplay_order:
            return None
        if self._autoplay_mode in {"up", "down"}:
            step = -1 if self._autoplay_mode == "up" else 1
            index = self._autoplay_index + step
            while 0 <= index < len(self._autoplay_order):
                if self._autoplay_playable(index):
                    return index
                index += step
            return None
        if self._autoplay_mode != "random":
            return None

        while True:
            while self._autoplay_random_queue:
                index = self._autoplay_random_queue.pop()
                track_id = self._autoplay_order[index]
                if (track_id not in self._autoplay_random_seen
                        and self._autoplay_playable(index)):
                    return index

            # One shuffled cycle contains every playable song exactly once.
            # A new cycle excludes the current song, preventing an immediate
            # repeat at the boundary as well.
            unique: dict[str, int] = {}
            for index, track_id in enumerate(self._autoplay_order):
                if (track_id not in unique
                        and self._autoplay_playable(index)):
                    unique[track_id] = index
            candidates = [
                index for track_id, index in unique.items()
                if track_id not in self._autoplay_random_seen
            ]
            if not candidates:
                current = self._autoplay_order[self._autoplay_index]
                self._autoplay_random_seen = {current}
                candidates = [
                    index for track_id, index in unique.items()
                    if track_id != current
                ]
            if not candidates:
                return None
            self._autoplay_rng.shuffle(candidates)
            self._autoplay_random_queue = candidates

    def _advance_autoplay(self, ended_track_id: str):
        """Advance only after an ordinary playing transport reaches its end."""
        if (not self._autoplay_mode
                or self.lib is None
                or self.player_bar.current_track() != ended_track_id
                or self._autoplay_index is None
                or not 0 <= self._autoplay_index < len(self._autoplay_order)
                or self._autoplay_order[self._autoplay_index]
                    != ended_track_id):
            return
        index = self._next_autoplay_index()
        if index is None:
            return
        track_id = self._autoplay_order[index]
        track = self.lib.tracks_by_id.get(track_id)
        if track is None:
            return
        self._autoplay_index = index
        self._autoplay_random_seen.add(track_id)
        self._open_track(
            track, autoplay=True, capture_autoplay=False)

    def _highlight_now_playing(self, tid: str) -> bool:
        """Loading a track ALWAYS highlights it in the visible browser — a
        playing track with no visible cursor is disorienting. No-op when the
        track isn't in the current view (e.g. a different playlist)."""
        if self.stack.currentWidget() is self.artists_view:
            cur = self.artists_view.currentItem()
            if cur is not None and cur.data(0, ARTIST_TRACK_ROLE) == tid:
                return True
            name = next((a for a, rows in getattr(self, "_artist_children", {}).items()
                         if any(r["id"] == tid for r in rows)), None)
            for i in range(self.artists_view.topLevelItemCount()):
                top = self.artists_view.topLevelItem(i)
                if top.data(0, ARTIST_ROLE) != name:
                    continue
                top.setExpanded(True)  # lazy-fills the songs if needed
                for c in range(top.childCount()):
                    child = top.child(c)
                    if child.data(0, ARTIST_TRACK_ROLE) == tid:
                        self.artists_view.setCurrentItem(child)
                        self.artists_view.scrollToItem(child)
                        return True
            return False
        if self.stack.currentIndex() != 0:
            return False  # device browser etc. — nothing to highlight
        for i, r in enumerate(self.model._rows):
            if r.get("id") == tid:
                cur = self.table.currentIndex()
                if not (cur.isValid() and cur.row() == i):
                    col = cur.column() if cur.isValid() else 0
                    self.table.setCurrentIndex(self.model.index(i, col))
                self.table.scrollTo(self.model.index(i, 0))
                return True
        return False

    def _focus_now_playing(self):
        """Return the browser cursor to the exact loaded track.

        Keep the current playlist/Artists context when it contains the track;
        otherwise fall back to the SoundBox Collection, which is the reliable
        all-library home. Search is cleared because a hidden target cannot be
        meaningfully focused.
        """
        tid = self.player_bar.current_track()
        if self.lib is None or not tid or tid not in self.lib.tracks_by_id:
            return
        self.body_stack.setCurrentIndex(0)
        if self.search.text():
            self.search.clear()
        self._keep_tid = tid
        if not self._highlight_now_playing(tid):
            collection = (
                self.sb_tree.topLevelItem(0)
                if self.sb_tree.topLevelItemCount() else None)
            if collection is None or not collection.data(0, COLLECTION_ROLE):
                return
            if self.sb_tree.currentItem() is collection:
                self._show_collection()
                self._view_key = "sb:Collection"
            else:
                self.sb_tree.setCurrentItem(collection)
            self._highlight_now_playing(tid)
        self.table.setFocus()

    def _on_cell_edited(self, field: str, track_id: str, value: str):
        if not track_id:
            return
        store = self.overrides if field == "artist" else self.titles
        v = (value or "").strip()
        if v:
            store[track_id] = v
        else:
            store.pop(track_id, None)  # blank reverts to auto/original
        self._save_state()
        if field == "artist" and self.lib is not None:
            self._build_artist_registry()  # new override immediately feeds trust/canon
        else:
            self._invalidate_rows()  # title override changed this row's computed title
        # Fixing the name should re-hunt for art unless a REAL cover is already
        # confirmed — a miss OR an avatar both deserve a retry with the fix.
        if self.lib and track_id in self.lib.tracks_by_id \
                and self.art_cache.status_kind(track_id) != "cover":
            self._art_fetch_one(track_id)

    # ---------- UI ----------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._top_bar())

        # The whole middle swaps between the main app and a full-page Settings
        # screen (the gear switches pages; Settings has its own Back button).
        self.body_stack = QStackedWidget()

        # page 0: fixed sidebar + main area (no splitter — sidebar can't be hidden).
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._sidebar())
        divider = QWidget()  # full-height vertical line between sidebar and main
        divider.setObjectName("VDivider")
        divider.setFixedWidth(1)
        divider.setAttribute(Qt.WA_StyledBackground, True)
        bl.addWidget(divider)
        bl.addWidget(self._main_stack(), 1)
        self.body_stack.addWidget(body)

        # page 1: full-page Settings
        self.body_stack.addWidget(self._settings_page())
        outer.addWidget(self.body_stack, 1)

        outer.addWidget(self._bottom_bar())

    def _top_bar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        # Whole-track overview + beat ruler + zoomed waveform stay in separate
        # lanes, with enough height for the transport stack beside them.
        bar.setMinimumHeight(218)
        outer = QVBoxLayout(bar)
        # The deck begins at the true left edge so its transport section can be
        # geometrically centered; the header keeps its own breathing room.
        outer.setContentsMargins(0, 5, 4, 5)
        outer.setSpacing(3)
        h = QHBoxLayout(); h.setSpacing(10)
        h.setContentsMargins(14, 0, 18, 0)
        sound = QLabel(SOUND_ART); sound.setObjectName("AsciiSound")
        sound.setAlignment(Qt.AlignVCenter)
        box = QLabel(BOX_ART); box.setObjectName("AsciiBox")
        box.setAlignment(Qt.AlignVCenter)
        h.addWidget(sound, 0, Qt.AlignVCenter)
        h.addWidget(box, 0, Qt.AlignVCenter)
        h.addSpacing(28)
        self.now_playing_frame = QWidget()
        self.now_playing_frame.setObjectName("NowPlayingFrame")
        self.now_playing_frame.setFixedHeight(44)
        self.now_playing_frame.setMinimumWidth(80)
        self.now_playing_frame.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        now_layout = QHBoxLayout(self.now_playing_frame)
        now_layout.setContentsMargins(0, 0, 0, 0)
        now_layout.setSpacing(0)

        autoplay_stack = QWidget()
        autoplay_stack.setObjectName("AutoplayStack")
        autoplay_stack.setFixedWidth(32)
        autoplay_layout = QVBoxLayout(autoplay_stack)
        autoplay_layout.setContentsMargins(0, 0, 0, 0)
        autoplay_layout.setSpacing(0)
        self.autoplay_up_btn = QPushButton("")
        self.autoplay_up_btn.setObjectName("AutoplayUp")
        self.autoplay_up_btn.setIcon(_autoplay_arrow_icon(-1))
        self.autoplay_up_btn.setIconSize(QSize(12, 8))
        self.autoplay_random_btn = QPushButton("R")
        self.autoplay_random_btn.setObjectName("AutoplayRandom")
        self.autoplay_down_btn = QPushButton("")
        self.autoplay_down_btn.setObjectName("AutoplayDown")
        self.autoplay_down_btn.setIcon(_autoplay_arrow_icon(1))
        self.autoplay_down_btn.setIconSize(QSize(12, 8))
        self._autoplay_buttons = {
            "up": self.autoplay_up_btn,
            "random": self.autoplay_random_btn,
            "down": self.autoplay_down_btn,
        }
        tooltips = {
            "up": "Autoplay upward through the playlist",
            "random": "Autoplay every playlist track once in random order",
            "down": "Autoplay downward through the playlist",
        }
        for mode, button in self._autoplay_buttons.items():
            button.setFixedSize(32, 14 if mode != "random" else 16)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tooltips[mode])
            button.setProperty("active", False)
            button.clicked.connect(
                lambda _checked=False, value=mode:
                self._toggle_autoplay_mode(value))
            autoplay_layout.addWidget(button)
        now_layout.addWidget(autoplay_stack)

        self.now_playing = QLabel("SELECT A TRACK")
        self.now_playing.setObjectName("NowPlaying")
        self.now_playing.setAlignment(Qt.AlignCenter)
        # Long metadata must never squeeze the fixed search/settings controls.
        self.now_playing.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.now_playing.setCursor(Qt.PointingHandCursor)
        self.now_playing.setToolTip("")
        now_layout.addWidget(self.now_playing, 1)
        h.addWidget(self.now_playing_frame, 1, Qt.AlignVCenter)
        h.addSpacing(28)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search title, artist, genre…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(165)  # slimmer → more room for the waveform
        # Click-to-focus only: as the first focusable widget it grabbed keyboard
        # focus at launch, so the app opened "typing into search" instead of
        # ready to browse (transport keys, arrows). Startup focus goes to the table.
        self.search.setFocusPolicy(Qt.ClickFocus)
        self.search.textChanged.connect(self._on_search)
        h.addWidget(self.search, 0, Qt.AlignVCenter)
        h.addSpacing(4)  # tight: search↔gear
        gear = QPushButton("⚙")  # opposite end from the ASCII wordmark
        gear.setObjectName("GearBtn")
        gear.setFixedSize(42, 34)
        gear.setToolTip("Settings")
        gear.setCursor(Qt.PointingHandCursor)
        gear.clicked.connect(self._open_settings)
        h.addWidget(gear, 0, Qt.AlignVCenter)
        outer.addLayout(h)

        # Full-width deck row: controls at the left, then a zoomed waveform that
        # owns all remaining width. Logo/search/settings stay above it instead
        # of squeezing the player into the same horizontal strip.
        self.player_bar = PlayerBar(
            jump_mode=str(self.settings.get("beat_jump_mode", "16 BEATS")),
            waveform_zoom=float(self.settings.get("waveform_zoom", 92.0)),
            waveform_mode=str(
                self.settings.get("waveform_mode", "3BAND")))
        self.player_bar.track_analysis_dir = self._track_analysis_dir
        self.player_bar.playback_error.connect(self._on_playback_error)
        self.player_bar.now_playing_changed.connect(self._set_now_playing)
        self.player_bar.playback_active_changed.connect(self._on_playback_active)
        self.player_bar.source_changed.connect(self._on_player_source_changed)
        self.player_bar.jump_mode_changed.connect(self._on_jump_mode_changed)
        self.player_bar.waveform_zoom_changed.connect(
            self._on_waveform_zoom_changed)
        self.player_bar.previous_requested.connect(lambda: self._play_relative(-1))
        self.player_bar.next_requested.connect(lambda: self._play_relative(1))
        self.player_bar.track_ended.connect(self._advance_autoplay)
        self.player_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer.addWidget(self.player_bar, 1)
        return bar

    def _set_now_playing(self, text: str):
        """Show artist/title in the open header lane, never over the waveform."""
        text = text or "SELECT A TRACK"
        available = max(80, self.now_playing.width() - 28)
        self.now_playing.setText(self.now_playing.fontMetrics().elidedText(
            text, Qt.ElideRight, available))
        self.now_playing.setToolTip("")

    def _on_jump_mode_changed(self, mode: str):
        self.settings["beat_jump_mode"] = mode
        self._save_state()

    def _on_waveform_zoom_changed(self, pixels_per_second: float):
        self.settings["waveform_zoom"] = pixels_per_second
        self._save_state()

    def _toggle_autoplay_mode(self, mode: str):
        self._set_autoplay_mode("" if self._autoplay_mode == mode else mode)

    def _set_autoplay_mode(self, mode: str, save: bool = True):
        """Select at most one playlist-relative end-of-track policy."""
        mode = mode if mode in _AUTOPLAY_MODES else ""
        changed = mode != self._autoplay_mode
        self._autoplay_mode = mode
        if mode == "random" and changed:
            current = (
                self._autoplay_order[self._autoplay_index]
                if self._autoplay_index is not None
                and 0 <= self._autoplay_index < len(self._autoplay_order)
                else self.player_bar.current_track()
            )
            self._autoplay_random_seen = {current} if current else set()
            self._autoplay_random_queue = []
        for value, button in getattr(
                self, "_autoplay_buttons", {}).items():
            active = value == mode
            if button.property("active") != active:
                button.setProperty("active", active)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
        self.settings["autoplay_mode"] = mode
        if save and changed:
            self._save_state()

    def _on_playback_active(self, active: bool):
        """Reserve CPU/UI time for the deck while audio is running."""
        for worker in (self._wf_fill_worker, self._art_worker):
            if worker is not None:
                worker.set_playback_active(active)
        if not active:
            self.model.refresh_art(self.art_cache.status_kind)
            self._refresh_devices()

    def _on_player_source_changed(self, path: str):
        worker = self._wf_fill_worker
        if worker is not None:
            worker.prioritize(path)

    def _section_header(self, text: str, btn: str | None = None, on_click=None,
                        top: bool = False) -> QWidget:
        w = QWidget()
        w.setObjectName("SectionHeaderTop" if top else "SectionHeader")
        w.setFixedHeight(26)
        w.setAttribute(Qt.WA_StyledBackground, True)  # ensure QSS bg/border paint
        h = QHBoxLayout(w); h.setContentsMargins(10, 0, 6, 0); h.setSpacing(0)
        lbl = QLabel(text); lbl.setObjectName("SectionLabel")
        h.addWidget(lbl, 1)
        if btn:
            b = QPushButton(btn); b.setObjectName("SectionBtn"); b.setFixedSize(22, 18)
            if on_click:
                b.clicked.connect(on_click)
            h.addWidget(b)
        return w

    def _sidebar(self) -> QWidget:
        panel = QWidget(); panel.setObjectName("Sidebar"); panel.setFixedWidth(272)
        panel.setAttribute(Qt.WA_StyledBackground, True)  # paint the right divider
        v = QVBoxLayout(panel); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        # The SOUNDBOX and DEVICES header bars ARE the resize grips: each one is
        # mounted inside a vertical-splitter handle, so grabbing the bar and
        # dragging it up/down trades space with the section above. The REKORDBOX
        # header stays fixed at the top. Sizes persist via settings
        # ("sidebar_split", saved with the column layout at quit).
        rb_pane = QWidget()
        pv = QVBoxLayout(rb_pane); pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(0)
        rb_header = self._section_header("REKORDBOX PLAYLISTS", "↻", self._refresh_rekordbox)
        rb_header.setContextMenuPolicy(Qt.CustomContextMenu)
        rb_header.customContextMenuRequested.connect(
            lambda pos, w=rb_header: self._rb_header_menu(w, pos))
        pv.addWidget(rb_header)
        self.tree = PlaylistTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.tree.setMinimumHeight(72)
        self.tree.currentItemChanged.connect(self._on_rb_select)
        self.tree.itemExpanded.connect(self._collapse_siblings)
        pv.addWidget(self.tree)

        self.sb_tree = SoundBoxTree()
        self.sb_tree.setHeaderHidden(True)
        self.sb_tree.setMinimumHeight(48)
        self.sb_tree.currentItemChanged.connect(self._on_custom_select)
        self.sb_tree.tracks_dropped.connect(self._on_tracks_dropped)
        self.sb_tree.playlist_moved.connect(self._move_custom)
        self.sb_tree.files_dropped.connect(
            lambda name, paths: self._import_files(paths, name or None))
        self.sb_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sb_tree.customContextMenuRequested.connect(self._sb_menu)

        self.device_tree = DeviceTree()
        self.device_tree.setHeaderHidden(True)
        self.device_tree.setMinimumHeight(48)  # was fixed 96 — now user-sized
        self.device_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(self._device_menu)
        self.device_tree.itemClicked.connect(self._on_device_click)
        self.device_tree.dropped.connect(self._on_device_drop)

        split = QSplitter(Qt.Vertical)
        split.setObjectName("SidebarSplit")
        split.setChildrenCollapsible(False)
        split.addWidget(rb_pane)
        split.addWidget(self.sb_tree)
        split.addWidget(self.device_tree)
        split.setHandleWidth(26)  # exactly one section-header bar tall
        for i, header in ((1, self._section_header("SOUNDBOX PLAYLISTS", "+",
                                                   self._new_custom, top=True)),
                          (2, self._section_header("DEVICES", top=True))):
            h = split.handle(i)
            hl = QVBoxLayout(h); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(0)
            hl.addWidget(header)  # the bar fills (and paints over) the handle
        split.setStretchFactor(0, 3)  # window resizes go mostly to rekordbox…
        split.setStretchFactor(1, 2)  # …then SoundBox; devices keep their size
        split.setStretchFactor(2, 0)
        self._sidebar_split = split
        v.addWidget(split)
        return panel

    def _main_stack(self) -> QWidget:
        self.stack = QStackedWidget()

        # page 0: track table (flush at top; sidebar has its own right border).
        page = QWidget(); pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(0)
        self.table = TrackTable()
        self.model = TrackModel()
        self.table.setModel(self.model)
        self.model.modelReset.connect(self._update_player_navigation)
        self.model.cell_edited.connect(self._on_cell_edited)  # inline Title/Artist edits
        self.table.setItemDelegate(CellEditDelegate(self.table))  # pre-filled editors
        # No keypress/double-click editing — a stray Space would wipe a cell. The
        # ONLY way in is the right-click menu (which calls self.table.edit()).
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context)  # edit or art
        self.table.doubleClicked.connect(self._play_row)  # double-click = load paused
        # ⌘R renames (inline-edits the Title of) the highlighted track.
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._rename_current)
        self.table.setAlternatingRowColors(True)
        # Per-ITEM scrolling keeps table rows phase-locked to the sidebar's 24px
        # grid at every scroll position (top, bottom, anywhere) — pixel scrolling
        # let the two panes drift out of alignment.
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragOnly)
        # Drop onto the browser adds to the playlist it's showing — same as the
        # sidebar. resolve_drop tells the table whether the current view is
        # writable (custom playlist / Collection) or off-limits (Artists, rb:).
        self.table.setAcceptDrops(True)
        self.table.resolve_drop = self._resolve_table_drop
        self.table.tracks_dropped.connect(self._on_tracks_dropped)
        self.table.files_dropped.connect(
            lambda name, paths: self._import_files(paths, name or None))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)  # all columns drag-resizable
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(46)
        # Track Title (col 0) absorbs all extra width; the rest keep their size.
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setFixedHeight(26)  # match the sidebar section-header height so they line up
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        # Click-to-sort (VIEW ONLY — exports always keep sacred rekordbox order).
        # Third click clears the indicator → back to the original playlist order.
        self.table.setSortingEnabled(True)
        hh.setSortIndicatorClearable(True)
        hh.setSortIndicator(-1, Qt.AscendingOrder)
        # Selection must survive re-sorting (and search — same disease): remember
        # the selected track id, re-seat it after the model reshuffles.
        self.table.selectionModel().currentChanged.connect(self._remember_current)
        hh.sortIndicatorChanged.connect(
            lambda *_: QTimer.singleShot(0, self._reseat_selection))
        pl.addWidget(self.table)
        self.stack.addWidget(page)

        # page 1: device browser
        dpage = QWidget(); dl = QVBoxLayout(dpage)
        dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        dhead = QWidget(); dhead.setObjectName("DeviceHeader")
        dhead.setAttribute(Qt.WA_StyledBackground, True); dhead.setFixedHeight(34)
        dhh = QHBoxLayout(dhead); dhh.setContentsMargins(10, 0, 8, 0)
        self.dev_header = QLabel("Device"); self.dev_header.setObjectName("DeviceTitle")
        dhh.addWidget(self.dev_header, 1)
        eject_btn = QPushButton("⏏ Eject"); eject_btn.clicked.connect(self._eject_current)
        dhh.addWidget(eject_btn)
        dl.addWidget(dhead)
        self.fs_model = QFileSystemModel()
        self.fs_model.setReadOnly(False)  # allow in-place rename on disk
        self.fs_view = QTreeView()
        self.fs_view.setObjectName("DeviceBrowser")
        self.fs_view.setModel(self.fs_model)
        # Edits only start via the Edit action (not double-click, which expands).
        self.fs_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fs_view.setItemDelegate(CellEditDelegate(self.fs_view))  # pre-filled inline edit
        self.fs_view.setSelectionMode(QAbstractItemView.ExtendedSelection)  # shift/cmd-click
        self.fs_view.setAlternatingRowColors(True)
        self.fs_view.setUniformRowHeights(True)
        self.fs_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fs_view.customContextMenuRequested.connect(self._browser_menu)
        fh = self.fs_view.header()
        fh.setStretchLastSection(False)
        fh.setSectionResizeMode(0, QHeaderView.Stretch)  # Name grows with the window
        for c in (1, 2, 3):  # Size / Kind / Date Modified stay put (resizable)
            fh.setSectionResizeMode(c, QHeaderView.Interactive)
        self.fs_view.setColumnWidth(1, 90)
        self.fs_view.setColumnWidth(2, 120)
        self.fs_view.setColumnWidth(3, 150)
        dl.addWidget(self.fs_view)
        self.stack.addWidget(dpage)

        # page 2: Artists browser — folder rows per artist, songs on expand
        self.artists_view = ArtistsTree()
        self.artists_view.setObjectName("ArtistsBrowser")
        self.artists_view.setHeaderLabels([
            "Artist / Track",
            *(label for _key, label, _hidden in COLUMNS[1:]),
        ])
        self.artists_view.setAlternatingRowColors(True)
        self.artists_view.setUniformRowHeights(True)
        ah = self.artists_view.header()
        ah.setSectionResizeMode(QHeaderView.Interactive)
        ah.setStretchLastSection(False)
        ah.setMinimumSectionSize(46)
        # This header mirrors the normal track table exactly. Artist/Track
        # absorbs the same slack as Track Title; every other visible/hidden
        # section keeps the table's saved width.
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        ah.setFixedHeight(26)
        self.artists_view.setIndentation(16)
        # Editing works like Collection: no stray double-click editing — the
        # right-click menu / ⌘R open the editor (pre-filled, cursor at end).
        self.artists_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.artists_view.setItemDelegate(CellEditDelegate(self.artists_view))
        # Drag tracks (or a whole artist) out to a SoundBox playlist — same as
        # the Collection table; the playlist accepts the `trk` mime already.
        self.artists_view.setDragEnabled(True)
        self.artists_view.setDragDropMode(QAbstractItemView.DragOnly)
        self.artists_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.artists_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artists_view.customContextMenuRequested.connect(self._artists_menu)
        self.artists_view.itemExpanded.connect(self._fill_artist)
        self.artists_view.itemDoubleClicked.connect(self._artists_double)
        self.artists_view.itemChanged.connect(self._on_artist_item_edited)
        self.stack.addWidget(self.artists_view)

        self._apply_default_columns()
        self._sync_artist_columns()
        self.table.horizontalHeader().sectionResized.connect(
            self._sync_artist_columns)
        ah.sectionResized.connect(self._on_artist_column_resized)
        return self.stack

    def _bottom_bar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("BottomBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        # A touch more left padding so status text (EXPORTING/DELETING/…) isn't jammed
        # against the window edge.
        h = QHBoxLayout(bar); h.setContentsMargins(20, 6, 12, 6); h.setSpacing(8)
        self.status_label = QLabel("Ready")
        # Fixed width (just fits the longest message) so animating dots don't
        # shove the bar, but the gap to the bar stays tight.
        fm = self.status_label.fontMetrics()
        # Fits the longest status (animated word or art count) so nothing clips.
        w = max(
            fm.horizontalAdvance("Preparing your library..."),
            fm.horizontalAdvance("Art 9999/9999"),
        )
        self.status_label.setFixedWidth(w + 8)
        h.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        self.progress.setFixedWidth(260)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setVisible(False)
        h.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_export)
        self.cancel_btn.setVisible(False)
        h.addWidget(self.cancel_btn)
        h.addStretch(1)
        return bar

    # ---------- open / load ----------
    def _open_dialog(self):
        start = str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(self, "Open Rekordbox XML", start,
                                              "rekordbox XML (*.xml)")
        if path:
            self.open_library(path)

    def open_library(self, path: str):
        try:
            self.lib = engine.load_library(path)
        except (FileNotFoundError, OSError) as exc:
            LOG.exception("Load failed: %s", path)
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        LOG.info("Loaded %s: %d tracks, %d playlists",
                 Path(path).name, self.lib.track_count, len(self.lib.playlists))
        self._last_xml = path  # remember for next launch
        self._save_state()
        self._apply_loaded_library()
        self._refresh_devices()
        self.stack.setCurrentIndex(0)
        self._cache_library()  # snapshot for the next instant paint
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        QTimer.singleShot(500, self._maybe_fill_waveforms)

    def _apply_loaded_library(self):
        """Shared post-load wiring: fold in imported tracks, (re)build the artist
        registry, apply hide settings, repopulate the sidebar + land on
        Collection. Used by open_library, the live-refresh completion, AND the
        instant paint from the on-disk snapshot."""
        # A fresh library can have different add-times than the one it replaces
        # (the instant-paint snapshot vs the live rekordbox read), so the per-path
        # landed-time cache MUST be dropped — otherwise a birthtime computed off
        # the snapshot sticks and defeats the newest-added sort.
        self._landed_cache.clear()
        self._sync_playlists_cache = None
        sb_cur = self.sb_tree.currentItem()  # selection BEFORE the rebuild
        imports.inject(self.lib, self.imported)  # livesets rejoin the pool
        self._build_artist_registry()
        self._apply_settings_to_lib()
        self._populate_tree(engine.summary(self.lib))
        self._default_to_collection()
        # _refresh_custom restores the sidebar selection with signals BLOCKED
        # (unlike the rekordbox tree, whose re-select fires _on_rb_select), so
        # when a refresh lands while Collection / Artists / a custom playlist
        # is showing, the visible rows must be re-rendered by hand — otherwise
        # new rekordbox adds stay invisible until you click away and back.
        # sb_cur guards the first load: _default_to_collection just selected
        # Collection with signals live, so it already rendered.
        cur = self.sb_tree.currentItem()
        if sb_cur is not None and cur is not None:
            if cur.data(0, ARTISTS_ROLE):
                self._show_artists()
            else:
                self._stash_view()  # keep the seat across the fresh rows
                key = getattr(self, "_view_key", None)
                self._reload_current_view()
                if key:
                    self._view_key = None  # same view, new rows — force re-seat
                    self._restore_view(key)

    def _cache_library(self):
        """Snapshot the current library to disk OFF the UI thread, so the next
        open paints instantly. Deliberately NOT a close-time action — closing
        stays instant; the snapshot is always taken right after a fresh load."""
        if self.lib is None:
            return
        from .workers import OneShotTask
        lib, path = self.lib, self._libcache_path
        w = OneShotTask(lambda: libcache.dump(lib, path))
        self._oneshots = getattr(self, "_oneshots", [])
        self._oneshots.append(w)
        w.finished.connect(lambda ww=w: self._finish_oneshot(ww))
        w.start()

    def _find_tree_item(self, path: str):
        """Locate a playlist tree item by its display path (PATH_ROLE)."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            if it.data(0, PATH_ROLE) == path:
                return it
            for i in range(it.childCount()):
                stack.append(it.child(i))
        return None

    def _populate_tree(self, summary: dict):
        # Remember what the user was viewing so a refresh doesn't dump them onto a
        # random playlist. Signals blocked during the rebuild so no stray
        # currentItemChanged fires _on_rb_select mid-repopulate.
        prev = None
        cur = self.tree.currentItem()
        if cur is not None:
            prev = cur.data(0, PATH_ROLE)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for child in summary["tree"].get("children", []):
                self.tree.addTopLevelItem(self._make_item(child))
        finally:
            self.tree.blockSignals(False)
        self._refresh_custom()  # Collection count tracks the freshly loaded library
        # Restore the prior selection if it still exists (expand to it + re-show rows).
        if prev:
            it = self._find_tree_item(prev)
            if it is not None:
                self._accordion_guard = True
                p = it.parent()
                while p is not None:
                    p.setExpanded(True)
                    p = p.parent()
                self._accordion_guard = False
                self.tree.setCurrentItem(it)  # fires _on_rb_select → re-shows its rows
                self.tree.scrollToItem(it)
        # else: collapsed by default; accordion handles the rest

    def _make_item(self, node: dict) -> QTreeWidgetItem:
        it = QTreeWidgetItem()
        if node["type"] == "playlist":
            it.setText(0, f"♪  {node['name']}  ({node['count']})")
            it.setData(0, PATH_ROLE, node["path"])
        else:
            it.setText(0, node["name"])
            for c in node.get("children", []):
                it.addChild(self._make_item(c))
        return it

    def _collapse_siblings(self, item: QTreeWidgetItem):
        if self._accordion_guard:
            return
        self._accordion_guard = True
        parent = item.parent()
        if parent is None:
            sibs = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        else:
            sibs = [parent.child(i) for i in range(parent.childCount())]
        for s in sibs:
            if s is not item:
                s.setExpanded(False)
        self._accordion_guard = False

    # ---------- live rekordbox refresh ----------
    def _refresh_rekordbox(self):
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            return
        self._start_pulse("Loading")
        w = RefreshWorker()
        self._refresh_worker = w
        w.done.connect(self._on_refresh_done)
        w.failed.connect(self._on_refresh_fail)
        w.finished.connect(lambda ww=w: self._finish_worker("_refresh_worker", ww))
        w.start()

    def _on_refresh_done(self, lib):
        self._stop_pulse()
        self.lib = lib
        self._apply_loaded_library()
        self._refresh_devices()
        LOG.info("Loaded %d tracks / %d playlists from rekordbox",
                 lib.track_count, len(lib.playlists))
        self._flash_status("Updated ✓", 6)
        self._cache_library()  # refresh the snapshot for the next instant paint
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        QTimer.singleShot(500, self._maybe_fill_waveforms)

    def _on_refresh_fail(self, err):
        self._stop_pulse()
        self.status_label.setText("Ready")
        LOG.error("rekordbox refresh failed: %s", err)
        QMessageBox.warning(
            self, "Couldn't read rekordbox",
            "Couldn't read the rekordbox database:\n\n" + err +
            "\n\nYou can use Open XML… instead (right-click the REKORDBOX PLAYLISTS header).")

    def _rb_header_menu(self, widget, pos):
        menu = QMenu(self)
        menu.addAction("Refresh from rekordbox", self._refresh_rekordbox)
        menu.addAction("Open XML…", self._open_dialog)
        menu.addSeparator()
        if self._art_worker is not None and self._art_worker.isRunning():
            menu.addAction("Stop fetching cover art", self._stop_art_fill)
        else:
            menu.addAction("Fetch cover art (background)", self._start_art_fill)
        menu.addAction("Re-scan covers (missing + upgrade avatars)", self._rescan_covers)
        menu.addAction("Suggest artists for Unknowns…", self._suggest_artists)
        menu.addAction("Suggest artist merges…", self._suggest_merges)
        menu.exec(widget.mapToGlobal(pos))

    def _suggest_artists(self):
        """Batch queue: scan all Unknown-Artist tracks for uploader-consensus
        suggestions; accepted ones become normal overrides (which also teach the
        trusted set + reversed flip)."""
        if self.lib is None:
            return
        from .suggest import SuggestDialog
        reg = self.lib.artist_registry
        items = []
        for t in self.lib.tracks_by_id.values():
            if t.track_id in self.hidden:
                continue
            a = metadata.effective_artist(t.track_id, t.name, t.artist,
                                          self.overrides, True, reg,
                                          stem=t.path.stem)
            if a == "Unknown Artist":
                items.append((t.track_id, self.titles.get(t.track_id) or t.name))
        if not items:
            self._flash_status("No Unknowns ✓", 6)
            return
        dlg = SuggestDialog(items, self)
        dlg.exec()
        dlg.shutdown()  # stop/wait the scan thread BEFORE the dialog is GC'd
        if not dlg.accepts:
            return
        for tid, artist in dlg.accepts:
            self.overrides[tid] = artist
        self._save_state()
        self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status(f"Accepted {len(dlg.accepts)} ✓", 8)

    def _suggest_merges(self):
        """Scan the library for likely duplicate artist spellings (local, fuzzy)
        and offer them in a merge window — same accept-flow as artist
        suggestions. Nothing applies until the human says so."""
        if self.lib is None:
            return
        from .workers import OneShotTask
        if _running(getattr(self, "_scan_worker", None)):
            return
        tracks = list(self.lib.tracks_by_id.values())
        overrides = dict(self.overrides)
        reg = self.lib.artist_registry

        def scan():
            from collections import Counter
            counts = Counter(metadata.effective_artist(
                t.track_id, t.name, t.artist, overrides, True, reg,
                stem=t.path.stem) for t in tracks)
            return dict(counts), metadata.suggest_merges(dict(counts))

        w = OneShotTask(scan)
        self._scan_worker = w
        w.done.connect(self._on_merge_scan)
        w.finished.connect(lambda ww=w: self._finish_worker("_scan_worker", ww))
        self._start_pulse("SCANNING")
        w.start()

    def _on_merge_scan(self, res):
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Scan failed", str(res))
            return
        counts, suggestions = res
        suggestions = [x for x in suggestions if x[0] not in self.aliases]
        if not suggestions and not self.aliases:
            self._flash_status("No duplicates ✓", 6)
            return
        from .suggest import MergeDialog
        dlg = MergeDialog(suggestions, dict(self.aliases), counts, self)
        if dlg.exec() != MergeDialog.Accepted:
            return
        self._apply_merges(dlg.accepts, dlg.unmerges)

    def _apply_merges(self, accepts, unmerges):
        """Apply merge-window results: unmerge first, then add aliases, then
        rebuild the registry + views. No-op if nothing changed."""
        if not accepts and not unmerges:
            return
        for src in unmerges:
            self.aliases.pop(src, None)
        for variant, target in accepts:
            self.aliases[variant] = target
        self._save_state()
        self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status(f"Merged {len(accepts)} ✓", 8)

    def _rescan_covers(self):
        """One button: re-attempt tracks with no art AND upgrade artist-avatar
        fallbacks toward a real track cover. Keeps confirmed real covers, reuses
        your artist corrections, and restarts any in-progress fill so the freshly
        cleared entries actually get picked up."""
        cleared = self.art_cache.retry_weak()
        LOG.info("Re-scan: cleared %d (missing + avatars) for a fresh pass", cleared)
        self._start_art_fill(restart=True)
        self._flash_status(f"Re-scan {cleared}", 6)

    def _select_only(self, keep):
        """Keep a single selection across the three sidebar lists (rekordbox,
        SoundBox, devices) — clear the others so only one thing is ever active."""
        for tw in (self.tree, self.sb_tree, self.device_tree):
            if tw is not keep:
                tw.blockSignals(True)
                tw.clearSelection()
                tw.setCurrentItem(None)
                tw.blockSignals(False)

    # ---------- per-view place memory ----------
    def _stash_view(self):
        """Remember where you are in the CURRENT table view (selected track +
        scroll offset) before switching away — coming back to a playlist or
        Collection must never dump you at the top."""
        key = getattr(self, "_view_key", None)
        if not key:
            return
        idx = self.table.currentIndex()
        tid = None
        if idx.isValid() and idx.row() < len(self.model._rows):
            tid = self.model._rows[idx.row()].get("id")
        if not hasattr(self, "_view_memory"):
            self._view_memory: dict = {}
        self._view_memory[key] = (tid, self.table.verticalScrollBar().value())

    def _restore_view(self, key: str):
        """Right after the table is repopulated for `key`: if this is a real
        switch (not an in-place refresh), re-seat the remembered track
        (centered) or at least the old scroll offset."""
        switching = key != getattr(self, "_view_key", None)
        self._view_key = key
        if not switching:
            return
        tid, scroll = getattr(self, "_view_memory", {}).get(key, (None, 0))
        if tid:
            for i, r in enumerate(self.model._rows):
                if r.get("id") == tid:
                    idx = self.model.index(i, 0)
                    self.table.setCurrentIndex(idx)
                    self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                    return
        if scroll:
            bar = self.table.verticalScrollBar()
            QTimer.singleShot(0, lambda: bar.setValue(min(scroll, bar.maximum())))

    def _on_rb_select(self, cur, _prev):
        if cur is None:
            return
        self._select_only(self.tree)
        if self.lib is None:
            return
        path = cur.data(0, PATH_ROLE)
        if not path:
            return
        self._stash_view()
        self.model.set_rows(self._annotate_art(self._playlist_rows(path)))
        self.stack.setCurrentIndex(0)
        self._restore_view(f"rb:{path}")

    # ---------- custom (SoundBox) playlists ----------
    def _new_custom(self):
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
            return
        name, ok = QInputDialog.getText(self, "New SoundBox Playlist", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        if self._custom_name_taken(name):
            QMessageBox.information(self, "Name taken",
                                    f"“{name}” is already used (Collection and "
                                    "Artists are reserved).")
            return
        self.custom[name] = []
        self._refresh_custom()
        self._save_state()

    def _custom_name_taken(self, name: str) -> bool:
        """Custom names must not collide with each other OR the two exportable
        views — on a device they're all identified by playlist name."""
        return name in self.custom or name.casefold() in ("collection", "artists")

    def _landed_time(self, t) -> float:
        """When a track "landed" in the collection, for the newest-first sort.

        One unified timeline across both sources: the track's add-time — rekordbox
        tracks by their DB created_at, imports by their SoundBox import time (both
        carried in Track.added_ts, with time-of-day precision so a set added right
        after a track sorts above it). Falls back to the file's birthtime only
        when the add-time is unknown (e.g. XML-loaded libraries). Cached per path
        for the session — the pool re-sorts on every Collection render."""
        cache = self._landed_cache
        key = str(t.path)
        v = cache.get(key)
        if v is None:
            v = getattr(t, "added_ts", 0.0) or paths.file_landed_time(key)
            cache[key] = v
        return v

    def _collection_tracks(self) -> list:
        """Every loaded track (all playlists pooled), honoring the hide-untracked
        settings. Default order = newest-first by when the track was ADDED to the
        collection (see _landed_time) — one timeline across rekordbox adds and
        SoundBox imports alike. Ties break by title A→Z; tracks with no known
        add-time and a missing file (0) sort last."""
        if self.lib is None:
            return []
        lib = self.lib
        tracks = [t for t in lib.tracks_by_id.values()
                  if t.track_id not in lib.hidden_ids]  # deleted-from-SoundBox
        if getattr(lib, "hide_untracked", False):
            tracks = [t for t in tracks
                      if not t.is_untracked(nokey=lib.hide_nokey, short=lib.hide_short,
                                            short_secs=lib.hide_short_secs)]
        return sorted(tracks, key=lambda t: (-self._landed_time(t), t.name.lower()))

    def _refresh_custom(self):
        # Preserve the current selection across the rebuild (same pattern as
        # _populate_tree) so a library refresh doesn't dump the user's view.
        cur = self.sb_tree.currentItem()
        keep_coll = bool(cur and cur.data(0, COLLECTION_ROLE))
        keep_artists = bool(cur and cur.data(0, ARTISTS_ROLE))
        keep_name = cur.data(0, CUSTOM_ROLE) if cur else None
        self.sb_tree.blockSignals(True)
        try:
            self.sb_tree.clear()
            # Built-in "Collection" pinned first: every track in one view. No
            # CUSTOM_ROLE (can't be renamed/deleted/reordered), but draggable
            # onto a device as a whole-pool playlist (sbview mime).
            tracks = self._collection_tracks()
            coll = QTreeWidgetItem([f"⌂  Collection  ({len(tracks)})"])
            coll.setData(0, COLLECTION_ROLE, True)
            self.sb_tree.addTopLevelItem(coll)
            if keep_coll:
                self.sb_tree.setCurrentItem(coll)
            # Built-in "Artists": opens the BROWSER-side folder view (artist
            # names only; drop one down to see their songs). Count = distinct.
            counts = {}
            for r in self._artist_rows(tracks):
                counts[r["artist"]] = counts.get(r["artist"], 0) + 1
            arts = QTreeWidgetItem([f"♪  Artists  ({len(counts)})"])
            arts.setData(0, ARTISTS_ROLE, True)
            self.sb_tree.addTopLevelItem(arts)
            if keep_artists:
                self.sb_tree.setCurrentItem(arts)
            for name, ids in self.custom.items():
                it = QTreeWidgetItem([f"★  {name}  ({len(ids)})"])
                it.setData(0, CUSTOM_ROLE, name)
                self.sb_tree.addTopLevelItem(it)
                if name == keep_name:
                    self.sb_tree.setCurrentItem(it)
        finally:
            self.sb_tree.blockSignals(False)

    def _resolve_table_drop(self):
        """What a drop on the main browser targets, or None to reject it.
        Mirrors the sidebar: a custom SoundBox playlist takes tracks + files;
        Collection imports files only (no "add", it's every track already);
        Artists (derived from tags) and read-only rekordbox (rb:) views reject —
        the sacred rule means rekordbox is never written to."""
        key = getattr(self, "_view_key", None)
        if not key:
            return None
        if key == "sb:Collection":
            return ("collection", "")
        if key.startswith("sb:"):
            name = key[3:]
            if name in getattr(self, "custom", {}):
                return ("playlist", name)
        return None  # sb:Artists, rb:<path>, or anything unrecognized

    def _on_tracks_dropped(self, name: str, ids: list[str]):
        bag = self.custom.setdefault(name, [])
        dupes = [i for i in ids if i in bag]
        # Adding the same song TWICE must be a choice, never an accident:
        # ask once per drop (rekordbox allows duplicates, so we do too — but
        # only on purpose).
        allow_dupes = bool(dupes) and self._confirm_duplicate_add(name, dupes)
        for i in ids:
            if i in bag and not allow_dupes:
                continue
            bag.append(i)
        self._refresh_custom()
        self._save_state()
        cur = self.sb_tree.currentItem()
        if cur and cur.data(0, CUSTOM_ROLE) == name:
            self._show_custom(name)

    def _track_label(self, tid: str) -> str:
        """The track's effective SoundBox title (for dialogs/messages)."""
        t = self.lib.tracks_by_id.get(tid) if self.lib else None
        if t is None:
            return tid
        src = self.titles.get(tid) or t.name
        artist = metadata.effective_artist(
            tid, src, t.artist, self.overrides, True,
            self.lib.artist_registry, stem=t.path.stem)
        return metadata.effective_title(tid, t.name, artist, self.titles,
                                        self._title_junk())

    def _confirm_duplicate_add(self, name: str, dupes: list[str]) -> bool:
        """Ok/Skip prompt when a drop contains tracks already in the playlist.
        Default is Skip — Enter must never sneak a duplicate in."""
        titles = [self._track_label(i) for i in dupes[:6]]
        if len(dupes) > 6:
            titles.append(f"…and {len(dupes) - 6} more")
        listing = "\n".join(f"  •  {t}" for t in titles)
        one = len(dupes) == 1
        box = QMessageBox(self)
        box.setWindowTitle("Already in playlist")
        box.setText(f"{'This track is' if one else 'These tracks are'} "
                    f"already in “{name}”:\n\n{listing}\n\n"
                    f"Add {'it' if one else 'them'} again anyway?")
        add = box.addButton("Add Anyway", QMessageBox.AcceptRole)
        skip = box.addButton("Skip", QMessageBox.RejectRole)
        box.setDefaultButton(skip)
        box.exec()
        return box.clickedButton() is add

    def _sb_menu(self, pos):
        item = self.sb_tree.itemAt(pos)
        name = item.data(0, CUSTOM_ROLE) if item else None
        menu = QMenu(self)
        if name:
            menu.addAction("Import Audio Files…", lambda: self._import_dialog(name))
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self._rename_custom(name))
            menu.addAction("Delete", lambda: self._delete_custom(name))
        elif item is not None and item.data(0, COLLECTION_ROLE):
            menu.addAction("Import Audio Files…", lambda: self._import_dialog(None))
        else:
            return
        menu.exec(self.sb_tree.mapToGlobal(pos))

    # ---------- imported (non-rekordbox) audio ----------
    def _import_dialog(self, playlist: str | None):
        """File picker → import livesets etc. into SoundBox (and optionally
        straight into a SoundBox playlist)."""
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
            return
        start = self.settings.get("import_dir") or str(Path.home() / "Downloads")
        exts = " ".join(f"*{e}" for e in sorted(imports.AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import Audio Files", start, f"Audio files ({exts})")
        if not paths:
            return
        self.settings["import_dir"] = str(Path(paths[0]).parent)
        self._import_files(paths, playlist)

    def _import_files(self, paths: list[str], playlist: str | None = None):
        """Add non-rekordbox audio files to SoundBox. Referenced in place —
        never copied, moved or re-tagged. They join the track pool, so they
        appear in Collection automatically; `playlist` also adds them to that
        SoundBox playlist. Re-importing a file reuses its existing id (so
        overrides/art/playlist entries stick)."""
        if self.lib is None:
            return
        by_path = {rec.get("path"): tid for tid, rec in self.imported.items()}
        ids: list[str] = []
        skipped: list[str] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if not imports.is_audio(p) or not p.is_file():
                skipped.append(p.name)
                continue
            tid = by_path.get(str(p))
            if tid is None:
                tid = imports.new_id()
                self.imported[tid] = imports.probe(p)
                by_path[str(p)] = tid
            ids.append(tid)
        if ids:
            imports.inject(self.lib, self.imported)
            self._build_artist_registry()  # imported names join the deriver
            self._refresh_custom()         # Collection count includes them now
            self._save_state()
            LOG.info("Imported %d audio files (%d skipped)", len(ids), len(skipped))
            self._flash_status(f"Imported {len(ids)} ✓", 15)
            if playlist:
                self._on_tracks_dropped(playlist, ids)  # dupe-guarded add
            else:
                self._reload_current_view()  # Collection view picks them up live
            QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
            QTimer.singleShot(500, self._maybe_fill_waveforms)
        elif skipped:
            QMessageBox.warning(
                self, "Nothing imported",
                "None of the dropped files could be imported (not an audio "
                "file, or the file doesn't exist):\n\n"
                + "\n".join(f"  •  {n}" for n in skipped[:8])
                + (f"\n  …and {len(skipped) - 8} more" if len(skipped) > 8 else ""))

    def _fix_links(self, roots: list | None = None):
        """Find imported files the user moved on disk and re-point their
        records (filename+size matching — see imports.find_moved). Scans the
        old locations' nearest surviving folders; anything still missing gets
        an offer to search a folder the user picks."""
        lost = imports.missing(self.imported)
        if not lost:
            self._flash_status("Links OK ✓", 5)
            return
        search = roots or imports.search_roots(self.imported)
        self._start_pulse("Searching")
        from .workers import OneShotTask
        w = OneShotTask(lambda: imports.find_moved(lost, search))
        self._oneshots = getattr(self, "_oneshots", [])
        self._oneshots.append(w)
        w.done.connect(self._on_links_found)
        w.finished.connect(lambda ww=w: self._finish_oneshot(ww))
        w.start()

    def _on_links_found(self, result):
        self._stop_pulse()
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Fix Links failed", str(result))
            return
        self._apply_relinks(result)
        left = imports.missing(self.imported)
        if not left:
            self._flash_status(f"Relinked {len(result)} ✓", 15)
            return
        names = [Path(str(r.get("path", ""))).name for r in left.values()][:6]
        listing = "\n".join(f"  •  {n}" for n in names) + (
            f"\n  …and {len(left) - 6} more" if len(left) > 6 else "")
        if QMessageBox.question(
                self, "Some files still missing",
                f"Relinked {len(result)}. {len(left)} still missing:\n\n{listing}\n\n"
                "Search a folder for them?") == QMessageBox.Yes:
            folder = QFileDialog.getExistingDirectory(self, "Search folder")
            if folder:
                self._fix_links([Path(folder)])

    def _apply_relinks(self, found: dict):
        """Point the imported records at their new paths and refresh."""
        for tid, p in found.items():
            rec = self.imported.get(tid)
            if rec is not None:
                rec["path"] = str(p)
        if found:
            if self.lib is not None:
                imports.inject(self.lib, self.imported)
                self._refresh_custom()
                self._reload_current_view()
            self._save_state()

    def _move_custom(self, name: str, idx: int):
        """Drag-to-reorder: move a SoundBox playlist to position `idx` in the
        custom order (dicts keep insertion order — rebuild in the new one)."""
        order = list(self.custom)
        if name not in order:
            return
        old = order.index(name)
        order.remove(name)
        if idx > old:
            idx -= 1  # the dragged row itself sat above the insert line
        idx = max(0, min(idx, len(order)))
        order.insert(idx, name)
        if order == list(self.custom):
            return
        self.custom = {n: self.custom[n] for n in order}
        self._refresh_custom()
        self._save_state()

    def _rename_custom(self, name: str):
        new, ok = QInputDialog.getText(self, "Rename playlist", "New name:", text=name)
        new = new.strip()
        if ok and new and new != name and not self._custom_name_taken(new):
            self.custom = {new if k == name else k: v for k, v in self.custom.items()}
            self._refresh_custom()
            self._save_state()

    def _delete_custom(self, name: str):
        if QMessageBox.question(self, "Delete playlist",
                                f"Delete SoundBox playlist “{name}”?") != QMessageBox.Yes:
            return
        self.custom.pop(name, None)
        self._refresh_custom()
        self._save_state()
        self.model.set_rows([])

    def _on_custom_select(self, cur, _prev):
        if cur is None:
            return
        self._select_only(self.sb_tree)
        self._stash_view()
        if cur.data(0, COLLECTION_ROLE):
            self._show_collection()
            self._restore_view("sb:Collection")
            return
        if cur.data(0, ARTISTS_ROLE):
            self._show_artists()
            # Table untouched behind the Artists page; park the key so later
            # stashes can't clobber the view we just left.
            self._view_key = "sb:Artists"
            return
        name = cur.data(0, CUSTOM_ROLE)
        if name:
            self._show_custom(name)
            self._restore_view(f"sb:{name}")

    def _artist_rows(self, tracks) -> list:
        """Track rows grouped A→Z by effective artist (titles A→Z within each);
        Unknown Artist sinks to the bottom."""
        rows = self._annotate_art(self._rows_for(tracks))
        rows.sort(key=lambda r: (r["artist"] == "Unknown Artist",
                                 r["artist"].casefold(), r["title"].casefold()))
        return rows

    def _show_artists(self):
        """The Artists BROWSER page: artist names only, each expandable in place
        to reveal that artist's songs (lazy-filled on first expand)."""
        rows = self._artist_rows(self._collection_tracks())
        by_artist: dict[str, list] = {}
        for r in rows:
            by_artist.setdefault(r["artist"], []).append(r)
        self.artists_view.clear()
        self._artist_children = by_artist
        for name in by_artist:
            it = QTreeWidgetItem([
                f"{name}  ({len(by_artist[name])})",
                *([""] * (len(COLUMNS) - 1)),
            ])
            it.setData(0, ARTIST_ROLE, name)
            # Stash the id list so the whole artist can be dragged to a playlist
            # even while collapsed (children are lazy-filled only on expand).
            it.setData(0, ARTIST_IDS_ROLE, [r["id"] for r in by_artist[name]])
            it.setChildIndicatorPolicy(
                QTreeWidgetItem.ShowIndicator)  # expandable before children exist
            self.artists_view.addTopLevelItem(it)
        self._filter_artists()  # apply any active search to the fresh list
        self.stack.setCurrentIndex(2)

    def _fill_artist(self, item):
        """Lazy: first expand of an artist row fills in their songs."""
        name = item.data(0, ARTIST_ROLE)
        if not name or item.childCount():
            return
        self._artists_filling = True
        try:
            for r in getattr(self, "_artist_children", {}).get(name, []):
                c = QTreeWidgetItem([
                    str(row_display_value(r, key) or "")
                    for key, _label, _hidden in COLUMNS
                ])
                for column, (key, _label, _hidden) in enumerate(COLUMNS):
                    if key == "art":
                        c.setTextAlignment(
                            column, Qt.AlignHCenter | Qt.AlignVCenter)
                    elif key in {"bpm", "time", "bitrate"}:
                        c.setTextAlignment(
                            column, Qt.AlignRight | Qt.AlignVCenter)
                c.setData(0, ARTIST_TRACK_ROLE, r["id"])
                c.setFlags(c.flags() | Qt.ItemIsEditable)  # title/artist editable
                item.addChild(c)
        finally:
            self._artists_filling = False
        self._apply_artist_child_filter(item)  # honor an active search on expand

    def _artists_menu(self, pos):
        it = self.artists_view.itemAt(pos)
        if it is None:
            return
        tid = it.data(0, ARTIST_TRACK_ROLE)
        # Selection to act on: the track row (or every track of a header row).
        ids = ArtistsTree._ids(self.artists_view.selectedItems() or [it])
        if not ids:
            return
        col = self.artists_view.columnAt(pos.x())
        menu = QMenu(self)
        if tid and col in (0, 1):
            menu.addAction("Edit", lambda: (self.artists_view.setCurrentItem(it, col),
                                            self.artists_view.editItem(it, col)))
        if self.custom:  # add to an existing SoundBox playlist (drag also works)
            sub = menu.addMenu(f"Add {len(ids)} to Playlist" if len(ids) > 1
                               else "Add to Playlist")
            for name in self.custom:
                sub.addAction(name, lambda n=name, i=list(ids): self._on_tracks_dropped(n, i))
        menu.addAction("Copy File Name" if len(ids) == 1 else f"Copy {len(ids)} File Names",
                       lambda: self._copy_filenames(ids))
        menu.exec(self.artists_view.viewport().mapToGlobal(pos))

    def _on_artist_item_edited(self, item, col):
        """Inline title/artist edits in the Artists browser — same override
        machinery as Collection (_on_cell_edited: clamp, save, registry, art
        retry). Regrouping under a renamed artist happens on the next visit."""
        if getattr(self, "_artists_filling", False):
            return
        tid = item.data(0, ARTIST_TRACK_ROLE)
        if not tid or col not in (0, 1):
            return
        from soundbox.paths import ARTIST_MAX, TITLE_MAX
        field = "title" if col == 0 else "artist"
        v = item.text(col).strip()[:TITLE_MAX if col == 0 else ARTIST_MAX].rstrip()
        self._artists_filling = True
        try:
            item.setText(col, v)
        finally:
            self._artists_filling = False
        self._on_cell_edited(field, tid, v)

    def _artists_double(self, item, _col):
        tid = item.data(0, ARTIST_TRACK_ROLE)
        t = self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None
        if t is not None:
            self._load_track(t)

    def _show_collection(self):
        self.model.set_rows(self._annotate_art(
            self._rows_for(self._collection_tracks())))
        self.stack.setCurrentIndex(0)

    def _show_custom(self, name: str):
        tracks = [t for i in self.custom.get(name, [])
                  if i not in self.hidden
                  and (t := self.lib.tracks_by_id.get(i) if self.lib else None)]
        self.model.set_rows(self._annotate_art(self._rows_for(tracks)))
        self.stack.setCurrentIndex(0)

    # ---------- devices ----------
    def _custom_playlists(self) -> list:
        """Synthetic Playlist objects for the user's custom SoundBox playlists so
        drift/resync recognize them by identity (they aren't in lib.playlists).
        Mirrors the `cust` branch of _resolve_jobs."""
        custom = getattr(self, "custom", None) or {}
        return [Playlist(name=n, folder_path=[], keys=list(ids), key_type="0")
                for n, ids in custom.items() if ids]

    def _view_playlist(self, view: str):
        """Collection/Artists as exportable synthetic playlists: the whole
        pool, in that view's order (Collection = newest first, Artists = A→Z
        by artist). Drag one onto a device = the entire library goes over."""
        if self.lib is None:
            return None
        if view == "collection":
            keys = [t.track_id for t in self._collection_tracks()]
            name = "Collection"
        elif view == "artists":
            keys = [r["id"] for r in self._artist_rows(self._collection_tracks())]
            name = "Artists"
        else:
            return None
        return Playlist(name=name, folder_path=[], keys=keys, key_type="0") if keys else None

    def _sync_playlists(self) -> list:
        """Every SoundBox-side playlist a device could hold: customs + the two
        exportable views. Cached because the 3-second device poll must never
        rebuild/sort the entire library on the UI thread."""
        if self._sync_playlists_cache is not None:
            return self._sync_playlists_cache
        views = [self._view_playlist(v) for v in ("collection", "artists")]
        self._sync_playlists_cache = (
            self._custom_playlists() + [p for p in views if p is not None])
        return self._sync_playlists_cache

    def _device_label(
            self, name: str, free, drift: int, fs: str = "",
            profile=None) -> str:
        # When a device is out of sync, HIDE the "GB free" so the full
        # "⚠ N playlist(s) out of date" fits (it was getting truncated). Free space
        # returns once everything is synced again.
        if drift:
            word = "playlist" if drift == 1 else "playlists"
            return f"⛃  {name}    ⚠ {drift} {word} out of date"
        if fs and fs not in ("msdos", "fat32"):
            # Rockbox runs on FAT32 only — flag HFS+/exFAT devices immediately.
            return f"⛃  {name}    ⚠ {fs.upper()} (not FAT32)"
        kind = f" · {profile.label}" if profile is not None else ""
        return f"⛃  {name}{kind}    {human(free)} free"

    def _refresh_devices(self):
        """Kick a background scan of external devices (free space + drift).

        The scan touches the removable volume, which can block for seconds while a
        copy/delete hammers it — so it runs OFF the UI thread (DeviceScanWorker) and
        the tree is updated in `_on_devices_scanned`. Single-flight: if a scan is
        already running we skip, so the 3s timer can never pile up workers."""
        # While OUR export/sync/delete is hammering the stick, don't also poll it —
        # the numbers are mid-flight and the extra reads only slow the operation down
        # (contention). Each op's done-handler calls _refresh_devices() right after.
        if self._exporting or self._deleting:
            return
        player = getattr(self, "player_bar", None)
        if player is not None and player.is_playing():
            return
        if _running(getattr(self, "_dev_scan_worker", None)):
            return
        w = DeviceScanWorker(self.lib, self._sync_playlists())
        self._dev_scan_worker = w
        w.done.connect(self._on_devices_scanned)
        w.finished.connect(lambda ww=w: self._finish_worker("_dev_scan_worker", ww))
        w.start()

    def _on_devices_scanned(self, devs: list):
        """Apply a background device scan to the tree (main thread). devs is a list
        of (name, path, free, drift, read-only profile)."""
        self._drift_cache = {
            path: drift for _n, path, _f, drift, _profile in devs}
        self._device_profiles = {
            path: profile for _n, path, _f, _d, profile in devs}
        new_paths = [p for _n, p, _f, _d, _profile in devs]
        cur = [self.device_tree.topLevelItem(i).data(0, DEVPATH_ROLE)
               for i in range(self.device_tree.topLevelItemCount())]
        if new_paths != cur and QApplication.mouseButtons() & Qt.LeftButton:
            # A device appeared/vanished DURING a drag (button held): rebuilding
            # the tree now would yank the drop target out from under the drag.
            # Skip this tick; the 3s poll redelivers right after the drop.
            return
        if new_paths == cur:
            # Same devices — refresh label in place (don't disrupt an in-flight drag).
            for i, (name, path, free, drift, profile) in enumerate(devs):
                it = self.device_tree.topLevelItem(i)
                if it is not None:
                    it.setText(0, self._device_label(
                        name, free, drift, device_fs(path), profile))
                    it.setToolTip(0, self._device_tip(path, free, profile))
            return
        self.device_tree.clear()
        for name, path, free, drift, profile in devs:
            it = QTreeWidgetItem([self._device_label(
                name, free, drift, device_fs(path), profile)])
            it.setData(0, DEVPATH_ROLE, path)
            it.setToolTip(0, self._device_tip(path, free, profile))
            self.device_tree.addTopLevelItem(it)

    def _device_tip(self, path: str, free, profile=None) -> str:
        fs = device_fs(path) or "unknown"
        tip = f"{path}\nFilesystem: {fs}   ·   {human(free)} free"
        if profile is not None:
            tip += f"\nDetected layout: {profile.label}"
            if profile.model:
                tip += f" ({profile.model})"
            if profile.stock_preview and not profile.stock_write:
                tip += "\nStock iPod export is preview-only; SoundBox will not edit iTunesDB."
        if fs not in ("msdos", "fat32", "unknown"):
            tip += "\n⚠ Rockbox requires FAT32 — this device would need reformatting."
        return tip

    def _on_device_click(self, item, _col):
        dev = item.data(0, DEVPATH_ROLE)
        if dev:
            self._select_only(self.device_tree)
            self.dev_header.setText(f"Device:  {Path(dev).name}   ({dev})")
            self.fs_view.setRootIndex(self.fs_model.setRootPath(dev))
            self.stack.setCurrentIndex(1)

    def _device_menu(self, pos):
        item = self.device_tree.itemAt(pos)
        if not item:
            return
        dev = item.data(0, DEVPATH_ROLE)
        menu = QMenu(self)
        menu.addAction("Browse", lambda: self._on_device_click(item, 0))
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.stock_preview:
            menu.addAction(
                "Preview stock iPod export…",
                lambda: self._preview_stock_ipod(dev))
        menu.addAction("Verify export…", lambda: self._verify_device(dev))
        menu.addAction(
            "Deep Verify managed files…",
            lambda: self._verify_device(dev, deep=True))
        if profile is not None and not profile.has_stock_ipod:
            menu.addSeparator()
            menu.addAction(
                "Erase all files from device…",
                lambda: self._erase_device(dev))
        n = self._drift_cache.get(dev, 0)  # ≤3s-fresh from the background scan
        if n:
            word = "playlist" if n == 1 else "playlists"
            menu.addAction(f"Sync — {n} {word} out of date",
                           lambda: self._sync_device(dev))
        else:
            act = menu.addAction("Sync (up to date)")
            act.setEnabled(False)
        menu.addAction("Eject", lambda: self._eject(dev))
        menu.exec(self.device_tree.mapToGlobal(pos))

    def _preview_stock_ipod(self, dev: str):
        """Show stock-iPod readiness without writing a byte to the device."""
        profile = self._device_profiles.get(dev)
        if profile is None:
            return
        audio = (
            [str(t.path) for t in self.lib.tracks_by_id.values()]
            if self.lib is not None else []
        )
        plan = plan_stock_ipod_export(profile, audio)
        formats = ", ".join(
            f"{suffix}: {count}"
            for suffix, count in plan["formats"].items()) or "none"
        QMessageBox.information(
            self, "Stock iPod export preview",
            f"Device: {profile.label}"
            f"{f' ({profile.model})' if profile.model else ''}\n"
            f"Tracks visible to planner: {plan['track_count']}\n"
            f"Formats: {formats}\n\n"
            "Preview only. SoundBox has not changed this device and will not "
            "write iTunesDB until that exporter is independently validated.")

    def _verify_device(self, dev: str, deep: bool = False):
        """Post-export sanity: every m3u8 entry on the device must resolve to a
        real file. Walks the USB, so it runs OFF the UI thread."""
        from .workers import OneShotTask
        if _running(getattr(self, "_scan_worker", None)):
            return
        w = OneShotTask(lambda: engine.verify_export(dev, deep=deep))
        self._scan_worker = w
        w.done.connect(lambda res, d=dev: self._on_verify_done(d, res))
        w.finished.connect(lambda ww=w: self._finish_worker("_scan_worker", ww))
        self._start_pulse("VERIFYING")
        w.start()

    def _on_verify_done(self, dev: str, res):
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Verify failed", str(res))
            return
        name = Path(dev).name
        if not res["playlists"]:
            QMessageBox.information(self, "Verify export",
                                    f"No SoundBox playlists found on {name}.")
        elif not res["missing"] and not res.get("managed_issues"):
            self._flash_status("Verified ✓", 8)
            extra = "".join(f"\n\n⚠ {a}" for a in res.get("advisories", []))
            QMessageBox.information(
                self, "Verify export",
                f"{name}: {res['playlists']} playlist(s), {res['entries']} "
                f"entries — every referenced file is present. ✓{extra}")
        else:
            lines = "\n".join(f"  {pl}: {entry}" for pl, entry in res["missing"][:12])
            more = ("\n  …and "
                    f"{len(res['missing']) - 12} more" if len(res["missing"]) > 12 else "")
            extra = "".join(f"\n\n⚠ {a}" for a in res.get("advisories", []))
            integrity = "\n".join(
                f"  {issue}" for issue in res.get("managed_issues", [])[:12])
            if integrity:
                integrity = f"\n\nManaged-file integrity:\n{integrity}"
            QMessageBox.warning(
                self, "Verify export",
                f"{name}: {len(res['missing'])} of {res['entries']} entries are "
                f"MISSING their files:\n\n{lines}{more}\n\nA Sync usually "
                f"heals this (or re-export the affected playlists)."
                f"{integrity}{extra}")

    def _sync_device(self, dev: str):
        if self.lib is None or not dev:
            return
        if self._resync_worker is not None and self._resync_worker.isRunning():
            return
        LOG.info("Sync device: %s", dev)
        w = ResyncWorker(self.lib, dev, self.overrides, self.titles, self.art_cache,
                         self._sync_playlists())
        self._resync_worker = w
        w.done.connect(self._on_resync_done)
        w.finished.connect(lambda ww=w: self._finish_worker("_resync_worker", ww))
        self._exporting = True  # pauses the device poll + keeps bg art status quiet
        # NO % bar: a diff-driven sync is fast and usually just rewrites an m3u8 (the
        # bar only flashed 27→100 and lied). Animated "SYNCING…" reads honestly, like
        # DELETE.
        self.progress.setVisible(False)
        self._start_pulse("SYNCING")
        w.start()

    def _on_resync_done(self, dev: str, rep):
        self._exporting = False
        self._stop_pulse()
        self.progress.setVisible(False)
        self._refresh_devices()  # updates free space + clears the drift badge
        if isinstance(rep, str):
            LOG.error("Sync failed for %s: %s", dev, rep)
            self.status_label.setText("Sync failed")
            QMessageBox.warning(self, "Sync failed", rep)
        else:
            LOG.info("Sync complete: %s", dev)
            self._flash_status("Synced ✓", 8)
            if self.stack.currentIndex() == 1:  # refresh browser if open
                self.fs_view.setRootIndex(self.fs_model.setRootPath(self.fs_model.rootPath()))

    # ---------- columns ----------
    def _apply_default_columns(self):
        for i, (_k, _l, hidden) in enumerate(COLUMNS):
            self.table.setColumnHidden(i, hidden)
        for i, w in enumerate([360, 170, 64, 70, 56, 64, 84, 80, 150, 110]):
            self.table.setColumnWidth(i, w)

    def _sync_artist_columns(self, *_args):
        """Mirror every table section boundary into the Artists browser."""
        if getattr(self, "_artist_column_sync", False):
            return
        self._artist_column_sync = True
        try:
            table_header = self.table.horizontalHeader()
            artist_header = self.artists_view.header()
            for column in range(len(COLUMNS)):
                self.artists_view.setColumnHidden(
                    column, self.table.isColumnHidden(column))
                if column:
                    artist_header.resizeSection(
                        column, table_header.sectionSize(column))
            artist_header.setSectionResizeMode(QHeaderView.Interactive)
            artist_header.setSectionResizeMode(0, QHeaderView.Stretch)
        finally:
            self._artist_column_sync = False

    def _on_artist_column_resized(
            self, column: int, _old_size: int, new_size: int):
        """A resize made on Artists becomes the normal shared column width."""
        if getattr(self, "_artist_column_sync", False) or column == 0:
            return
        self.table.setColumnWidth(column, new_size)

    def _selected_ids(self) -> list[str]:
        sm = self.table.selectionModel()
        rows = sm.selectedRows() if sm else []
        return [i for i in (self.model._rows[r.row()].get("id") for r in rows) if i]

    def _table_context(self, pos):
        """Right-click anywhere on a track row → the real menu (2026-07-02):
        Edit (on Title/Artist), Copy File Name (the ON-DISK name — what SoundBox
        displays is irrelevant), cover-art actions, Delete (SoundBox-only hide)."""
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        tid = self.model._rows[idx.row()].get("id")
        if not tid:
            return
        ids = self._selected_ids()
        if tid not in ids:
            ids = [tid]  # right-clicked outside the selection → act on that row
        key = COLUMNS[idx.column()][0]
        menu = QMenu(self)
        if key in ("title", "artist"):
            menu.addAction("Edit", lambda: self._table_edit_at(idx))
        if key == "artist":
            cur_artist = self.model._rows[idx.row()].get("artist") or ""
            if cur_artist and cur_artist != "Unknown Artist":
                menu.addAction(f"Merge “{cur_artist}” into…",
                               lambda a=cur_artist: self._merge_artist(a))
        n = len(ids)
        menu.addAction("Copy File Name" if n == 1 else f"Copy {n} File Names",
                       lambda: self._copy_filenames(ids))
        # Imported files the user moved on disk: offer to find them again.
        # Shown from any imported row — a move usually breaks a batch at once,
        # so the action heals ALL missing links, not just the clicked row.
        if any(i in self.imported for i in ids):
            lost = imports.missing(self.imported)
            if lost:
                menu.addAction(f"Fix Missing Links… ({len(lost)})",
                               self._fix_links)
        menu.addSeparator()
        menu.addAction("Choose cover art…", lambda: self._art_pick(tid))
        menu.addAction("Fetch cover art now", lambda: self._art_fetch_one(tid))
        menu.addAction("Set cover art from URL…", lambda: self._art_from_url(tid))
        if self.art_cache.image_path(tid):
            menu.addAction("Remove cover art", lambda: self._art_remove(tid))
        menu.addSeparator()
        # Two different verbs on purpose: in COLLECTION, "Delete" kills the
        # track everywhere in SoundBox; in a PLAYLIST view, "Remove" only takes it
        # out of THAT playlist and Collection keeps it. rekordbox never changes.
        rb_cur = self.tree.currentItem()
        rb_path = rb_cur.data(0, PATH_ROLE) if rb_cur is not None else None
        sb_cur = self.sb_tree.currentItem()
        cust_name = sb_cur.data(0, CUSTOM_ROLE) if sb_cur is not None else None
        if rb_path:
            menu.addAction("Remove from Playlist" if n == 1
                           else f"Remove {n} tracks from Playlist",
                           lambda: self._remove_from_rb_playlist(rb_path, ids))
        elif cust_name:
            menu.addAction("Remove from Playlist" if n == 1
                           else f"Remove {n} tracks from Playlist",
                           lambda: self._remove_from_custom(cust_name, ids))
        else:  # Collection (or any all-tracks context)
            menu.addAction("Delete from Collection" if n == 1
                           else f"Delete {n} tracks from Collection",
                           lambda: self._delete_tracks(ids))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _remove_from_rb_playlist(self, display_path: str, ids: list[str]):
        """Take tracks out of ONE rekordbox playlist, SoundBox-side only (a veto
        list keyed by the stable pid) — Collection and other playlists keep them.
        Changes the playlist's effective sig → synced devices show drift."""
        pl = next((p for p in self.lib.playlists
                   if p.display_path == display_path), None) if self.lib else None
        if pl is None:
            return
        if not pl.pid:
            QMessageBox.information(self, "Not available",
                                    "Removing tracks needs the live rekordbox library "
                                    "(XML playlists have no stable identity).")
            return
        bag = dict(self.removals.get(pl.pid, {}))
        stamp = time.strftime("%Y-%m-%d %H:%M")  # minute precision — same-day deletes sort truthfully
        for i in ids:
            bag.setdefault(i, stamp)
        self.removals[pl.pid] = bag
        self._after_track_edit(f"Removed {len(ids)} ✓")

    def _remove_from_custom(self, name: str, ids: list[str]):
        """Custom SoundBox playlists are ours — remove for real."""
        drop = set(ids)
        self.custom[name] = [i for i in self.custom.get(name, []) if i not in drop]
        self._refresh_custom()
        self._after_track_edit(f"Removed {len(ids)} ✓")

    def _after_track_edit(self, msg: str):
        """Persist + re-render after a delete/remove/restore: counts, current
        view, and device drift badges all reflect the new effective library."""
        self._save_state()
        self._apply_settings_to_lib()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._refresh_devices()
        self._refresh_graveyard()  # ALWAYS current — not just on gear-open
        self._flash_status(msg, 6)

    def _merge_artist(self, src: str):
        """Alias one artist spelling into another ("unownn" → "UNOWN") — the
        variant can then never appear as its own artist. SoundBox-side only;
        stored in library.json, removable in Settings."""
        reg = self.lib.artist_registry if self.lib else None
        choices = [a for a in (reg.names() if reg else []) if a != src]
        target, ok = QInputDialog.getItem(
            self, "Merge artist", f"Merge every “{src}” into:", choices, 0, True)
        target = (target or "").strip()
        if not ok or not target or target.lower() == src.lower():
            return
        self.aliases[src] = target
        self._save_state()
        self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._refresh_devices()  # renamed artist folders can drift nothing; keep badges fresh
        self._flash_status("Merged ✓", 6)

    def _remove_alias(self, src: str):
        self.aliases.pop(src, None)
        self._save_state()
        self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status("Unmerged ✓", 6)

    def _table_edit_at(self, idx):
        self.table.setCurrentIndex(idx)
        self.table.edit(idx)

    def _rename_current(self):
        """⌘R — inline-rename the highlighted CELL (Artist cell → artist,
        else Title) in whichever browser has focus: table or Artists page."""
        if QApplication.focusWidget() is self.artists_view:
            it = self.artists_view.currentItem()
            if it is not None and it.data(0, ARTIST_TRACK_ROLE):
                col = self.artists_view.currentColumn()
                self.artists_view.editItem(it, col if col in (0, 1) else 0)
            return
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        col = idx.column() if COLUMNS[idx.column()][0] in ("title", "artist") else 0
        self._table_edit_at(self.model.index(idx.row(), col))

    def _copy_filenames(self, ids: list[str]):
        """The REAL file name(s) on disk — display titles are irrelevant here."""
        names = [t.path.name for i in ids
                 if self.lib and (t := self.lib.tracks_by_id.get(i))]
        if names:
            QApplication.clipboard().setText("\n".join(names))
            self._flash_status("Copied ✓", 5)

    def _delete_tracks(self, ids: list[str]):
        """Delete from SOUNDBOX ONLY — never rekordbox, never the file on disk.
        The track just stops appearing in views and exports; restorable from
        Settings — so a library cleanup pass is always reversible."""
        n = len(ids)
        if QMessageBox.question(
                self, "Delete from SoundBox",
                f"Remove {n} track{'s' if n > 1 else ''} from SoundBox?\n\n"
                "rekordbox and the files on disk are NOT touched — SoundBox just "
                "stops showing and exporting them. Restore anytime in Settings."
        ) != QMessageBox.Yes:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M")  # minute precision — same-day deletes sort truthfully
        self.hidden.update(ids)
        for i in ids:
            self.hidden_dates.setdefault(i, stamp)
        self._after_track_edit(f"Deleted {n} ✓")

    def _restore_hidden(self):
        n = len(self.hidden) + sum(len(v) for v in self.removals.values())
        self.hidden.clear()
        self.hidden_dates.clear()
        self.removals.clear()
        self._after_track_edit(f"Restored {n} ✓")

    def _artist_avatar_groups(self) -> list:
        """[(artist, [tids], sample_image_path)] for every artist whose tracks
        currently wear an AVATAR (auto-selected fallback) — the catalog data."""
        if self.lib is None:
            return []
        groups: dict[str, list[str]] = {}
        for tid in self.lib.tracks_by_id:
            if self.art_cache.status_kind(tid) == "avatar":
                t = self.lib.tracks_by_id[tid]
                artist = metadata.effective_artist(
                    tid, t.name, t.artist, self.overrides, True,
                    self.lib.artist_registry, stem=t.path.stem)
                groups.setdefault(artist, []).append(tid)
        out = []
        for artist in sorted(groups, key=str.casefold):
            tids = groups[artist]
            out.append((artist, tids, self.art_cache.image_path(tids[0])))
        return out

    def _open_artist_catalog(self):
        from .artistcatalog import ArtistCatalogDialog
        groups = self._artist_avatar_groups()
        if not groups:
            self._flash_status("No avatar art ✓", 6)
            return
        dlg = ArtistCatalogDialog(groups, on_action=self._on_catalog_action,
                                  parent=self)
        dlg.exec()  # persistent: user fixes any number of artists, then closes

    def _on_catalog_action(self, c: dict, done):
        """Apply one catalog change and call done(ok, image_path) on the main
        thread so the (still-open) dialog updates its row in place."""
        tids = c["tids"]
        if c["kind"] == "rename":
            # Same plane as a merge: alias old → new, canonical everywhere.
            self._apply_merges([(c["artist"], c["new"])], [])
            done(True, None)
            return
        if c["kind"] == "delete":
            # Block, not forget: these avatars are unwanted AND unfindable —
            # they must never crawl back on a relaunch or re-scan.
            for tid in tids:
                self.art_cache.block(tid)
            self.model.refresh_art(self.art_cache.status_kind)
            done(True, None)
            return
        if c["kind"] == "file":
            try:
                data = Path(c["path"]).read_bytes()
            except OSError as exc:
                QMessageBox.warning(self, "Couldn't read image", str(exc))
                done(False, None)
                return
            mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            self._apply_artist_picture(tids, artfetch.ArtResult(
                data=data, mime=mime, confidence=1.0,
                source_url=Path(c["path"]).as_uri(), matched_title=c["artist"]))
            done(True, self.art_cache.image_path(tids[0]))
            return
        # url fetch hits the network — off the UI thread, dialog stays live
        from .workers import OneShotTask
        if _running(getattr(self, "_scan_worker", None)):
            done(False, None)
            return
        w = OneShotTask(lambda u=c["url"]: artfetch.fetch_by_url(u))
        self._scan_worker = w

        def landed(res, tids=tids, done=done):
            if isinstance(res, Exception) or res is None:
                done(False, None)
                return
            self._apply_artist_picture(tids, res)
            done(True, self.art_cache.image_path(tids[0]))

        w.done.connect(landed)
        w.finished.connect(lambda ww=w: self._finish_worker("_scan_worker", ww))
        w.start()

    def _apply_artist_picture(self, tids, res):
        """One picture → every avatar track of that artist (store_ok normalizes
        to Rockbox-safe baseline JPEG per track)."""
        for tid in tids:
            self.art_cache.store_ok(tid, res)
        self.model.refresh_art(self.art_cache.status_kind)
        self._flash_status(f"Art set on {len(tids)} ✓", 8)

    def _art_remove(self, tid):
        # BLOCK, not forget: a deliberate removal must never be undone by the
        # background fill on the next launch (it was — infuriating).
        self.art_cache.block(tid)
        self.model.refresh_art(self.art_cache.status_kind)

    def _art_pick(self, tid):
        """The manual picker: thumbnail grid of SoundCloud candidates (human
        judges, no confidence gate) + URL/file escape hatches. 100% coverage for
        the tracks the auto-matcher can't safely call."""
        t = self.lib.tracks_by_id.get(tid) if self.lib else None
        if not t:
            return
        title = self.titles.get(tid) or t.name
        artist = metadata.effective_artist(tid, title, t.artist, self.overrides, True,
                                           self.lib.artist_registry, stem=t.path.stem)
        dlg = ArtPickerDialog(artist, title, self)
        dlg.exec()
        dlg.shutdown()  # stop/wait the search thread BEFORE the dialog is GC'd
        choice = dlg.choice
        if not choice:
            return
        if choice["kind"] == "thumb":
            c = choice["cand"]
            self._run_oneshot(tid, lambda c=c: artfetch.fetch_thumb(
                c["thumb"], c["url"], c["title"]))
        elif choice["kind"] == "page":
            self._run_oneshot(tid, lambda u=choice["url"]: artfetch.fetch_by_url(u))
        else:  # local file — store_ok normalizes it to Rockbox-safe baseline JPEG
            p = Path(choice["path"])
            try:
                data = p.read_bytes()
            except OSError as exc:
                QMessageBox.warning(self, "Couldn't read image", str(exc))
                return
            mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            self.art_cache.store_ok(tid, artfetch.ArtResult(
                data=data, mime=mime, confidence=1.0,
                source_url=p.as_uri(), matched_title=p.name))
            self._flash_status("Art set ✓", 5)
            self.model.refresh_art(self.art_cache.status_kind)

    def _run_oneshot(self, tid, fn):
        self.status_label.setText("Fetching…")
        w = _OneShotArt(tid, fn)
        self._oneshots = getattr(self, "_oneshots", [])
        self._oneshots.append(w)
        w.done.connect(self._on_oneshot)
        # Prune the ref only AFTER run() fully returns (built-in `finished`) —
        # and drop it from the list, or the list leaks dead thread wrappers.
        w.finished.connect(lambda ww=w: self._finish_oneshot(ww))
        w.start()

    def _finish_oneshot(self, w):
        try:
            self._oneshots.remove(w)
        except ValueError:
            pass
        w.deleteLater()

    def _on_oneshot(self, tid, res):
        if isinstance(res, Exception):
            # Honest failure: the fetch BROKE (network, block, bug) — that is
            # not "no art found". Log the whole story; keep the flash short
            # (the status label is narrow).
            LOG.warning("Art fetch failed for %s: %s", tid, res, exc_info=res)
            self._flash_status("Fetch failed ✕", 8)
        elif res:
            self.art_cache.store_ok(tid, res)
            self._flash_status("Art set ✓", 5)
        else:
            self._flash_status("No art found", 5)
        self.model.refresh_art(self.art_cache.status_kind)  # update the Art column

    def _art_fetch_one(self, tid):
        t = self.lib.tracks_by_id.get(tid) if self.lib else None
        if not t:
            return
        title = self.titles.get(tid) or t.name
        artist = metadata.effective_artist(tid, title, t.artist, self.overrides, True,
                                           self.lib.artist_registry, stem=t.path.stem)
        self._run_oneshot(tid, lambda: artfetch.fetch_best(artist, title))

    def _art_from_url(self, tid):
        url, ok = QInputDialog.getText(
            self, "Cover art",
            "SoundCloud URL (track = its cover, artist profile = avatar):")
        if ok and url.strip():
            self._run_oneshot(tid, lambda u=url.strip(): artfetch.fetch_by_url(u))

    def _maybe_autostart_art_fill(self):
        """Auto-fetch runs ONLY when the library gained tracks the cache has
        never seen (new rekordbox adds) — and the fill worker's to-do list is
        exactly those uncached tracks, so ONLY the new songs get searched.
        Everything else (re-scans, per-track fetches) is user-triggered."""
        if self.lib is None:
            return
        idx = self.art_cache.index
        fresh = sum(1 for tid in self.lib.tracks_by_id if tid not in idx)
        if fresh:
            LOG.info("Art fill autostart: %d new track(s) without cache entries", fresh)
            self._start_art_fill()

    def _start_art_fill(self, restart: bool = False):
        if self.lib is None:
            return
        if self._art_worker is not None and self._art_worker.isRunning():
            # A pass is already running. For a re-scan we must restart so the
            # worker picks up the cache entries we just cleared (it locked its
            # to-do list at startup). Stop it; _on_art_finished starts the fresh one.
            if restart:
                self._art_restart = True
                self._art_worker.stop()
            return
        w = ArtFillWorker(self.lib, self.art_cache, self.overrides, self.titles)
        self._art_worker = w
        w.set_playback_active(self.player_bar.is_playing())
        w.progress.connect(self._on_art_progress)
        w.finished.connect(self._on_art_finished)
        w.start()

    def _on_art_finished(self):
        w = self._art_worker
        if w is not None:
            w.deleteLater()
        self._art_worker = None
        if getattr(self, "_art_restart", False):  # a re-scan asked to restart
            self._art_restart = False
            self._start_art_fill()

    def _maybe_fill_waveforms(self):
        """Prepare every missing canonical V4 record off the UI thread."""
        if self.lib is None:
            return
        if _running(self._wf_fill_worker):
            # A live rekordbox refresh can replace the library while a pass is
            # running. Its cue fingerprints belong to the old snapshot, so
            # guarantee one fresh scan after it finishes.
            self._waveform_rescan_pending = True
            return
        w = WaveformFillWorker(self.lib, self._track_analysis_dir)
        self._wf_fill_worker = w
        w.set_playback_active(self.player_bar.is_playing())
        w.prioritize(self.player_bar._src_path or "")
        w.detail_ready.connect(self.player_bar.refresh_audio_detail)
        w.storage_paused.connect(self._on_analysis_storage_paused)
        w.tasks_ready.connect(self._on_analysis_tasks_ready)
        w.progress.connect(self._on_analysis_progress)
        w.power_policy_changed.connect(self._on_analysis_power_policy)
        w.finished.connect(lambda ww=w: self._on_library_prep_finished(ww))
        w.start()

    def _on_analysis_tasks_ready(self, total: int):
        """Announce preparation only after the background scan found work."""
        if total <= 0:
            return
        self._preparing_library = True
        if not self._exporting and not self._cancelling and not self._deleting:
            self._start_pulse("Preparing your library")

    def _on_analysis_progress(self, done: int, total: int):
        if (not self._preparing_library or self._exporting
                or self._cancelling or self._deleting):
            return
        self._stop_pulse()
        suffix = " · Eco" if self._prep_power_policy == "battery" else ""
        self.status_label.setText(f"Prep {done}/{total}{suffix}")

    def _on_analysis_power_policy(self, policy: str):
        self._prep_power_policy = policy
        if (not self._preparing_library or self._exporting
                or self._cancelling or self._deleting):
            return
        self._stop_pulse()
        if policy == "low-power":
            self.status_label.setText("Prep paused · Low Power")
        elif policy == "battery":
            self.status_label.setText("Preparing · Battery Eco")
        else:
            self._start_pulse("Preparing your library")

    def _on_analysis_storage_paused(self, paused: bool, free_bytes: int):
        if self._exporting or self._cancelling or self._deleting:
            return
        if paused:
            self._stop_pulse()
            self.status_label.setText(
                f"Analysis paused — {free_bytes / 1024 ** 3:.1f} GiB free")
        elif self._preparing_library:
            self._start_pulse("Preparing your library")

    def _on_library_prep_finished(self, w):
        self._finish_worker("_wf_fill_worker", w)
        self._preparing_library = False
        if not self._exporting and not self._cancelling and not self._deleting:
            self._stop_pulse()
            self.status_label.setText("Ready")
        if getattr(self, "_waveform_rescan_pending", False):
            self._waveform_rescan_pending = False
            QTimer.singleShot(0, self._maybe_fill_waveforms)

    def _stop_art_fill(self):
        if self._art_worker is not None and self._art_worker.isRunning():
            self._art_worker.stop()
            # Clear the stale "Art N/M" ticker (unless a device op owns the label).
            if not self._exporting and not self._cancelling and not self._deleting:
                self.status_label.setText("Ready")

    def _on_art_progress(self, done, total):
        if not self.player_bar.is_playing():
            self.model.refresh_art(self.art_cache.status_kind)
        if (not self._exporting and not self._cancelling and not self._deleting
                and not self._preparing_library):
            # A device op (export/sync/delete) owns the status bar; don't fight it.
            if done >= total:
                self._flash_status("Art done ✓", 6)
            else:
                self.status_label.setText(f"Art {done}/{total}")  # ticks every fetch

    def _header_menu(self, pos):
        menu = QMenu(self)
        for i, (_k, label, _h) in enumerate(COLUMNS):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(i))
            act.toggled.connect(lambda on, col=i: self._set_column_hidden(col, not on))
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _set_column_hidden(self, col: int, hidden: bool):
        self.table.setColumnHidden(col, hidden)
        self._sync_artist_columns()
        self._save_columns()  # visibility toggles persist immediately

    def _save_columns(self):
        """Persist the complete user-sized shell.

        This runs at shutdown after the user's final resize, so the next launch
        restores the outer window first and the three sidebar panes second.
        """
        hh = self.table.horizontalHeader()
        self.settings["columns_state"] = bytes(hh.saveState().toBase64()).decode("ascii")
        self.settings["sidebar_split"] = list(self._sidebar_split.sizes())
        self.settings["window_geometry"] = bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.settings["window_rect"] = [
            normal.x(), normal.y(), normal.width(), normal.height()]
        self._save_state()

    def _restore_window_geometry(self):
        """Restore exact size/position, clamping only if its screen disappeared."""
        rect = self.settings.get("window_rect")
        if (isinstance(rect, list) and len(rect) == 4
                and all(isinstance(value, int) for value in rect)
                and rect[2] >= 640 and rect[3] >= 480):
            wanted = QRect(*rect)
            screens = [screen.availableGeometry()
                       for screen in QApplication.screens()]
            if screens:
                screen = max(
                    screens,
                    key=lambda area: area.intersected(wanted).width()
                    * area.intersected(wanted).height())
                width = min(wanted.width(), screen.width())
                height = min(wanted.height(), screen.height())
                x = min(max(wanted.x(), screen.left()),
                        screen.right() - width + 1)
                y = min(max(wanted.y(), screen.top()),
                        screen.bottom() - height + 1)
                self.setGeometry(x, y, width, height)
                return
            self.setGeometry(wanted)
            return

        # Compatibility with the first geometry-enabled build and Qt's own
        # maximized/fullscreen encoding. New clean closes always add window_rect.
        state = self.settings.get("window_geometry")
        if not isinstance(state, str) or not state:
            return
        try:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromBase64(
                state.encode("ascii"), QByteArray.AbortOnBase64DecodingErrors))
        except (UnicodeEncodeError, ValueError):
            # Derived UI placement must never prevent the library from opening.
            return

    def _restore_columns(self):
        """Bring back the user's saved column layout + sidebar section heights
        (after settings load). Sort is deliberately NOT restored — a playlist
        opening pre-sorted would read as the wrong (non-rekordbox) order."""
        sizes = self.settings.get("sidebar_split")
        if (isinstance(sizes, list) and len(sizes) == 3
                and all(isinstance(x, int) and x >= 0 for x in sizes) and sum(sizes)):
            self._sidebar_split.setSizes(sizes)
        else:
            self._sidebar_split.setSizes([600, 400, 96])  # classic proportions
        state = self.settings.get("columns_state")
        if not state:
            self._sync_artist_columns()
            return
        from PySide6.QtCore import QByteArray
        hh = self.table.horizontalHeader()
        if hh.restoreState(QByteArray.fromBase64(state.encode("ascii"))):
            hh.setSortIndicator(-1, Qt.AscendingOrder)
            hh.setSectionResizeMode(QHeaderView.Interactive)
            hh.setSectionResizeMode(0, QHeaderView.Stretch)  # Title keeps absorbing slack
        self._sync_artist_columns()

    # ---------- export (auto on drop) ----------
    def _resolve_jobs(self, items: list[tuple[str, str]]) -> list[Playlist]:
        pls: list[Playlist] = []
        for kind, val in items:
            if kind == "rb":
                pl = next((p for p in self.lib.playlists if p.display_path == val), None)
                if pl:
                    pls.append(pl)
            elif kind == "cust":
                ids = self.custom.get(val, [])
                if ids:
                    # Top level of /Playlists (no SoundBox subfolder).
                    pls.append(Playlist(name=val, folder_path=[],
                                        keys=list(ids), key_type="0"))
            elif kind == "sbview":
                pl = self._view_playlist(val)
                if pl is not None:
                    pls.append(pl)
        return pls

    def _on_device_drop(self, dev: str, items: list[tuple[str, str]]):
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
            return
        profile = self._device_profiles.get(dev)
        if profile is not None and not profile.rockbox_export:
            if profile.stock_preview:
                self._preview_stock_ipod(dev)
            else:
                QMessageBox.warning(
                    self, "Export unavailable",
                    "This volume is not a writable FAT32 Rockbox/generic USB "
                    "target. No files were changed.")
            return
        pls = self._resolve_jobs(items)
        if not pls:
            return
        if self._exporting and self.export_worker is not None:
            self.export_worker.add(dev, pls)  # aggregate into the running export
            return
        LOG.info("Export: %d playlist(s) -> %s", len(pls), dev)
        w = ExportWorker(self.lib, self.overrides, self.titles, self.art_cache)
        self.export_worker = w
        self._exporting = True
        w.progress.connect(self._on_progress)
        w.finished_all.connect(self._on_export_done)
        # Drop our ref only on `finished` (after run() fully returns, on the main
        # thread) — NOT in _on_export_done, which runs mid-run via finished_all.
        w.finished.connect(lambda ww=w: self._finish_worker("export_worker", ww))
        # Always start the bar at a truthful 0% before any files are processed.
        est = sum(max(1, len(self.lib.resolve(p))) for p in pls)
        self.progress.setRange(0, est)
        self.progress.setValue(0)
        self._start_pulse("EXPORTING")  # animated dots from the first frame
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        w.add(dev, pls)
        w.start()

    def _cancel_export(self):
        if self.export_worker is not None:
            self.export_worker.cancel()
            self._cancelling = True
            self._start_pulse("Cancelling")

    def _finish_worker(self, attr: str, w):
        """Drop our reference to a finished worker on the MAIN thread (after run()
        has fully returned) and schedule its C++ deletion. This is the crux fix for
        'QThread destroyed while running' — never let the last ref drop mid-run."""
        if getattr(self, attr, None) is w:
            setattr(self, attr, None)
        try:
            w.deleteLater()
        except RuntimeError:
            pass  # already gone

    def shutdown(self):
        """Stop background work BEFORE Qt tears down — prevents the crash where
        the app quits while a worker thread is still running."""
        for t in (getattr(self, "_dev_timer", None), getattr(self, "_pulse_timer", None),
                  getattr(self, "_warm_timer", None)):
            if t is not None:
                t.stop()
        try:
            self._save_columns()  # final widths (drag-resizes) persist at quit
        except Exception:  # noqa: BLE001 — never block shutdown
            pass
        self._art_restart = False  # don't let a pending re-scan restart during teardown
        w = self.export_worker
        if _running(w):
            LOG.info("Shutdown during export — cancelling and waiting for worker")
            w.cancel(); w.wait(10000)
        if _running(self._refresh_worker):
            self._refresh_worker.wait(15000)  # read-only DB read; let it finish
        if _running(self._resync_worker):
            self._resync_worker.cancel(); self._resync_worker.wait(10000)
        if _running(getattr(self, "_dev_scan_worker", None)):
            self._dev_scan_worker.wait(6000)  # read-only scan; let it finish
        if _running(getattr(self, "_delete_worker", None)):
            self._delete_worker.cancel(); self._delete_worker.wait(10000)
        if _running(getattr(self, "_eject_verify_worker", None)):
            self._eject_verify_worker.wait(15000)
        if _running(getattr(self, "_device_import_worker", None)):
            self._device_import_worker.wait(15000)
        if _running(self._art_worker):
            self._art_worker.stop(); self._art_worker.wait(6000)
        if _running(getattr(self, "_wf_fill_worker", None)):
            # QAudioDecoder tracks poll interruption during decode; the
            # between-files loop check catches libsndfile tracks.
            self._wf_fill_worker.requestInterruption(); self._wf_fill_worker.wait(8000)
        if _running(getattr(self, "_scan_worker", None)):
            self._scan_worker.wait(15000)  # pure CPU scan; let it finish
        if _running(getattr(self, "_warm_worker", None)):
            self._warm_worker.wait(8000)  # row-cache warm; pure CPU, let it finish
        if _running(getattr(self, "_backup_worker", None)):
            self._backup_worker.wait(30000)  # finish the zip/restore, don't corrupt it
        for w in list(getattr(self, "_oneshots", [])):
            if _running(w):
                w.wait(8000)  # a mid-flight art fetch must not be GC'd running
        pb = getattr(self, "player_bar", None)
        if pb is not None:
            pb.stop()
            pb.shutdown()  # workers plus native audio/display threads
        p = getattr(self, "_eject_proc", None)
        if p is not None and p.state() != QProcess.NotRunning:
            p.waitForFinished(3000)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _on_progress(self, done: int, total: int, label: str):
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, total))
        if self._cancelling:
            return  # keep the animated "Cancelling…" status
        if total and done >= total:
            # Files copied; engine is scrubbing the device. Solid 100% bar +
            # animated "FINALIZING…" so it's clearly working, not frozen.
            self._start_pulse("FINALIZING")
        else:
            # Animate "EXPORTING…" alongside the % bar (idempotent — the same-word
            # guard keeps the dots from resetting on every tick).
            self._start_pulse("EXPORTING")

    def _start_pulse(self, word: str):
        if self._pulse_word == word and self._pulse_timer.isActive():
            return  # already animating this word — don't reset the dots
        self._pulse_word = word
        self._pulse_dots = 0
        self.status_label.setText(word)
        self._pulse_timer.start()

    def _stop_pulse(self):
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()
        self._pulse_word = None

    def _pulse_tick(self):
        self._pulse_dots = (self._pulse_dots + 1) % 4
        self.status_label.setText((self._pulse_word or "") + "." * self._pulse_dots)

    def _flash_status(self, msg: str, seconds: int = 15):
        """Show a transient status that auto-clears to Ready (if not replaced)."""
        self.status_label.setText(msg)
        QTimer.singleShot(
            seconds * 1000,
            lambda: self.status_label.setText("Ready") if self.status_label.text() == msg else None,
        )

    def _on_export_done(self, reports: list):
        cancelled = self._cancelling
        self._cancelling = False
        self._exporting = False
        # NOTE: don't null self.export_worker here — this runs mid-run (finished_all).
        # _finish_worker (on the thread's `finished`) drops the ref safely.
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        self.progress.setVisible(False)
        issues = []
        for dev, rep in reports:
            if isinstance(rep, str):
                issues.append(f"{Path(dev).name}: {rep}")
                continue
            for pr in rep.playlists:
                if pr.missing or pr.conflicts or pr.errors:
                    issues.append(f"{pr.name}: {pr.missing} missing · {pr.conflicts} conflicts")
        if cancelled:
            self._flash_status("Cancelled")  # auto-clears in 15s like Ejected
        else:
            self.status_label.setText("Complete ✓")  # sticks (fits the tight label)
        self._refresh_devices()  # update free space right away
        LOG.info("Export finished: %d job report(s), %d issue(s)", len(reports), len(issues))
        if self.stack.currentIndex() == 1:  # refresh browser if open
            self.fs_view.setRootIndex(self.fs_model.setRootPath(self.fs_model.rootPath()))
        if issues:
            QMessageBox.information(self, "Export finished — with notes",
                                   "Done, but note:\n\n" + "\n".join(issues[:20]))


def make_app() -> QApplication:
    logpath = setup_logging()
    LOG.info("Soundbox starting (log: %s)", logpath)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("LowFreqLabs")
    app.setApplicationName("Soundbox")
    app.setFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))  # real system font
    app.setStyleSheet(theme.QSS)
    return app


def main():
    app = make_app()
    win = MainWindow()
    app.aboutToQuit.connect(win.shutdown)  # also covers Cmd+Q / menu quit
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
