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
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QStandardPaths

LOG = logging.getLogger("soundbox")


def setup_logging() -> Path | None:
    """Console + production file log; Qt test mode never touches the real log."""
    if LOG.handlers:
        return None
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOG.addHandler(sh)
    logpath = None
    log_dir = None
    if not QStandardPaths.isTestModeEnabled():
        try:
            log_dir = Path.home() / "Library" / "Logs" / "Soundbox"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "soundbox.log")
            fh.setFormatter(fmt)
            LOG.addHandler(fh)
            logpath = log_dir / "soundbox.log"
        except OSError:
            log_dir = None

    def _hook(t, v, tb):
        LOG.error("UNCAUGHT EXCEPTION", exc_info=(t, v, tb))
        sys.__excepthook__(t, v, tb)

    sys.excepthook = _hook
    if log_dir is not None:
        try:
            # C-level crashes (Qt fatals, codec faults) dump Python stacks into
            # the log too — otherwise a native abort leaves nothing to debug.
            import faulthandler
            faulthandler.enable(open(log_dir / "crash-stacks.log", "a"))
        except OSError:
            pass
    # PACKAGED app (Finder launch): stderr goes NOWHERE, and PySide prints
    # swallowed slot exceptions to stderr — so a broken button clicks silently.
    # Route stderr into the log so every field failure leaves a trace.
    if getattr(sys, "frozen", False) and log_dir is not None:
        try:
            sys.stderr = sys.stdout = open(
                log_dir / "soundbox.log", "a", buffering=1,
            )
        except OSError:
            pass
    return logpath

from PySide6.QtCore import QEvent, QPointF, QProcess, QRect, QSize, Qt, QThread, QTimer
from PySide6.QtGui import (
    QColor, QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QLineEdit,
    QFileDialog, QFileSystemModel, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTableView, QTreeView, QTreeWidgetItem, QVBoxLayout, QWidget,
)


from soundbox import (
    artfetch, engine, imports, libcache, manifest as device_manifest, metadata,
    paths, rekordbox_db, rockbox_config,
)
from soundbox.device_profile import plan_stock_ipod_export
from soundbox.dual_ipod import (
    DualIpodSyncPlan,
    commit_dual_ipod_sync,
    plan_dual_ipod_sync,
)
from soundbox.library import Playlist
from soundbox.stock_artwork import StockArtworkCache
from soundbox.stock_ipod import (
    StockIpodDeletePlan, StockIpodSyncPlan,
    commit_stock_ipod_playlist_delete, commit_stock_ipod_sync,
    inspect_stock_ipod_sync_status,
    normalize_model_number, plan_stock_ipod_playlist_delete,
    plan_stock_ipod_sync, supports_stock_write,
)
from soundbox.stock_ipod_info import (
    StockIpodSnapshot, confirmed_model_from_recovery,
    read_stock_ipod_snapshot, stock_ipod_device_key,
)
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache
from . import theme
from .artpicker import ArtPickerDialog
from .model import (
    COLUMNS, TrackModel, row_display_value, row_matches as _row_matches,
)
from .player import PlayerBar

from .device_browser import DeviceBrowserMixin
from .devices import device_facts, device_fs, human
from .settings_page import SettingsPageMixin
from .stock_device_view import CompositeDeviceData, StockIpodPages
from .trees import (
    ARTIST_IDS_ROLE, ARTIST_ROLE, ARTIST_TRACK_ROLE, ARTISTS_ROLE, COLLECTION_ROLE,
    CUSTOM_ROLE, DEVPATH_ROLE, PATH_ROLE,
    ArtistsTree, CellEditDelegate, DeviceTree, PlaylistTree, SoundBoxTree, TrackTable,
)

from .workers import (
    ArtFillWorker, DeleteWorker, DeviceScanWorker, ExportWorker,
    RefreshWorker, ResyncWorker, StockArtworkPrepWorker, WaveformFillWorker,
    _OneShotArt, _running,
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
    "stock_ipod_models": {},  # stable device identity -> confirmed model
}

_AUTOPLAY_MODES = {"up", "random", "down"}

# New rekordbox files can be detected and cooked before rekordbox (or another
# tag editor) finishes its last container rewrite. Direct SoundBox imports can
# likewise be replaced in place after they land. The cheap V5 readiness scan
# reads only source stats and small manifests, so keep revalidating the loaded
# library without inflating waveform payloads. Only changed tracks recook.
_WAVEFORM_RECHECK_MS = 60_000


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
        self.stock_art_cache = StockArtworkCache(data_dir / "stock_artwork")
        self.rockbox_media_cache = RockboxMediaCache(data_dir / "rockbox_media")
        self.rockbox_art_cache = RockboxArtworkCache(
            data_dir / "rockbox_artwork")
        self.stock_ipod_shadow_store = StockIpodShadowStore(
            data_dir / "stock_ipod_state")
        # One-time Rockbox-safety sweep: any cached art that isn't baseline JPEG
        # (progressive/PNG shows BLANK on the iPod) is re-encoded. Flag-filed —
        # a no-op on every launch after the first, so it's fine on the main thread
        # (local SSD, not a removable device).
        checked, fixed = self.art_cache.normalize_all()
        if checked:
            LOG.info("art cache sweep: %d checked, %d re-encoded to baseline JPEG", checked, fixed)
        self._art_worker: ArtFillWorker | None = None
        self._art_restart = False  # set by re-scan to restart the fill after it stops
        self._stock_art_worker: StockArtworkPrepWorker | None = None
        self._stock_art_rescan = False
        self._stock_art_pending: set[Path] = set()
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
        self._stock_plan_worker = None
        self._stock_commit_worker = None
        self._dual_plan_worker = None
        self._dual_commit_worker = None
        self._dual_active_device = ""
        self._dual_active_playlist_keys: set[str] = set()
        self._dual_sync_queue: dict[str, list[Playlist]] = {}
        self._dual_active_preconfirmed = False
        self._dual_active_confirmed = False
        self._dual_active_action = "sync"
        self._dual_active_track_total = 0
        self._dual_batch_keys: set[str] = set()
        self._dual_batch_names: list[str] = []
        self._dual_batch_total_tracks = 0
        self._dual_batch_done_tracks = 0
        self._dual_batch_results: list[tuple[DualIpodSyncPlan, object]] = []
        self._stock_delete_plan_worker = None
        self._stock_delete_commit_worker = None
        self._stock_view_worker = None
        self._eject_verify_worker = None
        self._device_import_worker = None
        self._drift_cache: dict[str, int] = {}  # device path -> #playlists out of date
        self._device_profiles: dict[str, object] = {}
        self._current_device_path = ""
        self._stock_snapshot: StockIpodSnapshot | None = None
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
        self._pending_device_operation = None
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
        # A successful source load/import arms a recurring identity scan. It is
        # single-shot and rearmed only after the previous worker has finished,
        # so scans never overlap and a large cook cannot create a timer storm.
        self._waveform_rechecks_enabled = False
        self._waveform_recheck_timer = QTimer(self)
        self._waveform_recheck_timer.setSingleShot(True)
        self._waveform_recheck_timer.setInterval(_WAVEFORM_RECHECK_MS)
        self._waveform_recheck_timer.timeout.connect(self._maybe_fill_waveforms)
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
        self.settings["title_rules_off"] = metadata.normalize_title_rules_off(
            self.settings.get("title_rules_off"))
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
            # Keep the key application-owned if a widget shortcut is added in
            # the future. This event does not control Cocoa press-and-hold;
            # the packaged bundle policy and NoTypeAhead boundary do that.
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
            analysis_path=getattr(t, "source_analysis_path", ""),
            autoplay=autoplay,
            cues=getattr(t, "source_cues", None))
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
        for worker in (
                self._wf_fill_worker, self._art_worker, self._stock_art_worker):
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
        self.device_tab_group = QButtonGroup(self)
        self.device_tab_group.setExclusive(True)
        self.device_tab_buttons = []
        for index, label in enumerate(("Device", "Songs", "Playlists")):
            button = QPushButton(label)
            button.setObjectName("DeviceTab")
            button.setCheckable(True)
            button.setFixedHeight(26)
            button.setVisible(False)
            button.toggled.connect(
                lambda checked, i=index: (
                    self._set_stock_device_tab(i) if checked else None))
            self.device_tab_group.addButton(button, index)
            self.device_tab_buttons.append(button)
            dhh.addWidget(button)
        self.device_sync_btn = QPushButton("Sync")
        self.device_sync_btn.clicked.connect(
            lambda: self._sync_device(self._current_device_path))
        dhh.addWidget(self.device_sync_btn)
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
        self.device_content_stack = QStackedWidget()
        self.device_content_stack.addWidget(self.fs_view)
        self.stock_ipod_pages = StockIpodPages()
        self.stock_ipod_pages.confirm_model_requested.connect(
            self._confirm_current_stock_model)
        self.stock_ipod_pages.delete_playlists_requested.connect(
            self._begin_stock_playlist_delete)
        self.stock_ipod_pages.playlist_order_requested.connect(
            self._apply_composite_playlist_order)
        self.device_content_stack.addWidget(self.stock_ipod_pages)
        dl.addWidget(self.device_content_stack)
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
        # Reserve only the current operation's animated dots.  A single global
        # width for every possible flash message left a huge dead gap before
        # the iPod count and progress bar.
        fm = self.status_label.fontMetrics()
        self.status_label.setFixedWidth(fm.horizontalAdvance("Ready") + 8)
        h.addWidget(self.status_label)
        self.status_count_label = QLabel("")
        self.status_count_label.setAlignment(Qt.AlignCenter)
        self.status_count_label.setFixedWidth(
            fm.horizontalAdvance("9999/9999") + 8)
        self.status_count_label.setVisible(False)
        h.addWidget(self.status_count_label)
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        self.progress.setFixedWidth(208)
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
        self._arm_waveform_rechecks()
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        QTimer.singleShot(3250, self._maybe_prepare_stock_artwork)
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
        repaired = imports.inject(self.lib, self.imported)  # livesets rejoin the pool
        if repaired:
            LOG.info("Repaired duration/size metadata for %d imported track(s)",
                     repaired)
            self._save_state()
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
        self._arm_waveform_rechecks()
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        QTimer.singleShot(3250, self._maybe_prepare_stock_artwork)
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
        self._start_pulse("Scanning")
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
            self._arm_waveform_rechecks()
            QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
            QTimer.singleShot(3250, self._maybe_prepare_stock_artwork)
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
            self._arm_waveform_rechecks()
            QTimer.singleShot(500, self._maybe_fill_waveforms)

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
        return [Playlist(name=n, folder_path=[], keys=list(ids))
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
        return Playlist(name=name, folder_path=[], keys=keys) if keys else None

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
        # "⚠ Sync needed" state fits (it was getting truncated). Free space
        # returns once everything is synced again.
        if drift:
            return f"⛃  {name}    ⚠ Sync needed"
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
        raw_models = self.settings.get("stock_ipod_models")
        w = DeviceScanWorker(
            self.lib,
            self._sync_playlists(),
            artist_overrides=self.overrides,
            title_overrides=self.titles,
            stock_shadow_store=self.stock_ipod_shadow_store,
            stock_models=(raw_models if isinstance(raw_models, dict) else {}),
        )
        self._dev_scan_worker = w
        w.done.connect(self._on_devices_scanned)
        w.finished.connect(lambda ww=w: self._finish_worker("_dev_scan_worker", ww))
        w.start()

    def _defer_device_operation_until_scan_finishes(self, callback) -> bool:
        """Serialize a device operation behind an already-running poll.

        Setting ``_exporting`` immediately prevents the three-second timer from
        launching another scan in the gap between this worker finishing and the
        requested operation starting.
        """
        worker = getattr(self, "_dev_scan_worker", None)
        if not _running(worker):
            return False
        if self._pending_device_operation is not None:
            return True
        self._pending_device_operation = callback
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Waiting for iPod")

        def resume():
            if self._pending_device_operation is not callback:
                return
            self._pending_device_operation = None
            self._exporting = False
            self._stop_pulse()
            callback()

        worker.finished.connect(resume)
        return True

    def _on_devices_scanned(self, devs: list):
        """Apply a background device scan to the tree (main thread). devs is a list
        of (name, path, free, drift, read-only profile)."""
        devs = [
            (name, path, free, drift, self._effective_device_profile(path, profile))
            for name, path, free, drift, profile in devs
        ]
        self._drift_cache = {
            path: drift for _n, path, _f, drift, _profile in devs}
        self._device_profiles = {
            path: profile for _n, path, _f, _d, profile in devs}
        new_paths = [p for _n, p, _f, _d, _profile in devs]
        if self._current_device_path and self._current_device_path not in new_paths:
            self._current_device_path = ""
            self._stock_snapshot = None
            self.stack.setCurrentIndex(0)
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

    def _stock_device_key(self, dev: str, profile=None) -> str:
        profile = profile or self._device_profiles.get(dev)
        library_id = int(
            getattr(profile, "library_persistent_id", 0) or 0)
        return stock_ipod_device_key(
            device_facts(dev).volume_uuid, library_id)

    def _stored_stock_model(self, dev: str, profile=None) -> str:
        profile_model = normalize_model_number(
            str(getattr(profile, "model", "") or ""))
        if supports_stock_write(profile_model):
            return profile_model
        values = self.settings.get("stock_ipod_models")
        if not isinstance(values, dict):
            return ""
        model = normalize_model_number(
            str(values.get(self._stock_device_key(dev, profile)) or ""))
        return model if supports_stock_write(model) else ""

    def _effective_device_profile(self, dev: str, profile):
        if getattr(profile, "kind", "") != "stock_ipod":
            return profile
        model = self._stored_stock_model(dev, profile)
        if not model or model == getattr(profile, "model", ""):
            return profile
        if not hasattr(profile, "__dataclass_fields__"):
            return profile
        return replace(profile, model=model, stock_write=True)

    def _remember_stock_model(self, identity_key: str, model: str):
        model = normalize_model_number(model)
        if not identity_key or not supports_stock_write(model):
            return
        raw = self.settings.get("stock_ipod_models")
        values = dict(raw) if isinstance(raw, dict) else {}
        if values.get(identity_key) == model:
            return
        values[identity_key] = model
        self.settings["stock_ipod_models"] = values
        self._save_state()

    def _device_tip(self, path: str, free, profile=None) -> str:
        fs = device_fs(path) or "unknown"
        tip = f"{path}\nFilesystem: {fs}   ·   {human(free)} free"
        if profile is not None:
            tip += f"\nDetected layout: {profile.label}"
            if profile.model:
                tip += f" ({profile.model})"
            if profile.has_rockbox and profile.rockbox_export:
                if profile.kind == "dual_boot":
                    tip += (
                        "\nComposite single-copy dual sync is available: "
                        "stock-safe media is shared with Rockbox.")
                else:
                    tip += "\nRockbox filesystem export rules are active."
            elif profile.stock_preview and not profile.stock_write:
                tip += (
                    "\nStock sync requires an MP3/WAV playlist dry-run and "
                    "confirmed MA446 model number.")
            elif profile.stock_write:
                tip += "\nValidated MA446 stock-sync engine available (dry-run first)."
        if fs not in ("msdos", "fat32", "unknown"):
            tip += "\n⚠ Rockbox requires FAT32 — this device would need reformatting."
        return tip

    def _on_device_click(self, item, _col):
        dev = item.data(0, DEVPATH_ROLE)
        if dev:
            self._select_only(self.device_tree)
            self._current_device_path = dev
            self.dev_header.setText(f"Device:  {Path(dev).name}   ({dev})")
            self.fs_view.setRootIndex(self.fs_model.setRootPath(dev))
            profile = self._device_profiles.get(dev)
            stock = bool(
                profile is not None
                and getattr(profile, "has_stock_ipod", False))
            for button in self.device_tab_buttons:
                button.setVisible(stock)
            for button, label in zip(
                    self.device_tab_buttons,
                    ("Device", "Songs", "Playlists"),
                    strict=True):
                button.setText(label)
            if stock:
                self.device_content_stack.setCurrentIndex(1)
                preserve_snapshot = bool(
                    self._stock_snapshot is not None
                    and self._stock_snapshot.target == Path(dev).resolve())
                self.stock_ipod_pages.set_loading(
                    preserve_page=preserve_snapshot)
                if not preserve_snapshot:
                    self._set_stock_device_tab(0)
                self._read_stock_device_view(dev, profile)
            else:
                self.device_content_stack.setCurrentIndex(0)
            self.stack.setCurrentIndex(1)

    def _set_stock_device_tab(self, index: int):
        if not self.device_tab_buttons:
            return
        index = max(0, min(2, int(index)))
        self.device_tab_buttons[index].setChecked(True)
        self.stock_ipod_pages.set_page(index)

    def _read_stock_device_view(
            self, dev: str, profile=None, *, after_finished=None):
        if _running(self._stock_view_worker):
            if after_finished is not None:
                self._stock_view_worker.finished.connect(after_finished)
            return
        from .workers import OneShotTask
        facts = device_facts(dev)
        model = self._stored_stock_model(dev, profile)
        library = self.lib
        extra_playlists = tuple(self._sync_playlists())
        artist_overrides = dict(self.overrides)
        title_overrides = dict(self.titles)

        def read_snapshot():
            snapshot = read_stock_ipod_snapshot(
                dev,
                model_number=model,
                volume_uuid=facts.volume_uuid,
                filesystem=facts.filesystem_name or device_fs(dev),
                protocol=facts.protocol,
                device_identifier=facts.device_identifier,
                writable=facts.writable,
                capacity_bytes=facts.capacity_bytes,
                free_bytes=0,  # refresh inside worker; diskutil cache can be stale
                serial_number=getattr(facts, "hardware_serial", ""),
                serial_source=getattr(facts, "serial_source", ""),
                usb_serial=getattr(facts, "usb_serial", ""),
                firmware_version=getattr(facts, "firmware_version", ""),
                firmware_source=getattr(facts, "firmware_source", ""),
                transport_revision=getattr(facts, "transport_revision", ""),
            )
            composite_data: CompositeDeviceData | None = None
            composite_path = device_manifest.manifest_path(dev)
            if (
                getattr(profile, "kind", "") == "dual_boot"
                and composite_path.is_file()
            ):
                composite_manifest = device_manifest.load_composite(dev)
                device_manifest.composite_library_counts(composite_manifest)
                composite_data = CompositeDeviceData(
                    composite_manifest,
                    device_manifest.composite_playlist_entries(
                        dev, composite_manifest),
                    rockbox_config.settings_ready(dev),
                    rockbox_config.database_file_count(dev),
                    rockbox_config.database_refresh_pending(dev),
                )
            if library is None or not snapshot.identity_key or not snapshot.model:
                return snapshot, composite_data
            pre_composite_dual = bool(
                getattr(profile, "kind", "") == "dual_boot"
                and not device_manifest.manifest_path(dev).exists())
            if pre_composite_dual:
                return replace(
                    snapshot,
                    sync_state="composite_untracked",
                    sync_detail=(
                        "No composite playlist baseline yet; drag a playlist "
                        "onto this iPod to establish one"
                    ),
                    sync_local_changes=0,
                    sync_committed_at="",
                ), composite_data
            status = inspect_stock_ipod_sync_status(
                library,
                dev,
                identity_key=snapshot.identity_key,
                model_number=snapshot.model,
                serial_number=snapshot.serial_number,
                shadow_store=self.stock_ipod_shadow_store,
                extra_playlists=extra_playlists,
            )
            if composite_data is not None:
                drift = engine.device_drift(
                    library,
                    dev,
                    list(extra_playlists),
                    artist_overrides=artist_overrides,
                    title_overrides=title_overrides,
                )
                if drift is not None and drift.any():
                    count = drift.count()
                    return replace(
                        snapshot,
                        sync_state="local_changes",
                        sync_detail=(
                            f"{count} managed playlist change(s) ready to sync"),
                        sync_local_changes=count,
                        sync_committed_at=status.committed_at,
                    ), composite_data
            return replace(
                snapshot,
                sync_state=status.state,
                sync_detail=status.detail,
                sync_local_changes=status.local_changes,
                sync_committed_at=status.committed_at,
            ), composite_data

        worker = OneShotTask(read_snapshot)
        self._stock_view_worker = worker
        worker.done.connect(
            lambda result, device=dev: self._on_stock_device_view(device, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker("_stock_view_worker", ww))
        if after_finished is not None:
            worker.finished.connect(after_finished)
        worker.start()

    def _refresh_device_surfaces(self, dev: str):
        """Refresh an open iPod snapshot, then its sidebar state, in series."""
        profile = self._device_profiles.get(dev)
        if (
            dev != self._current_device_path
            or profile is None
            or not getattr(profile, "has_stock_ipod", False)
        ):
            self._refresh_devices()
            return
        self._exporting = True
        self.stock_ipod_pages.set_loading(preserve_page=True)

        def finish_refresh():
            self._exporting = False
            self._refresh_devices()

        self._read_stock_device_view(
            dev, profile, after_finished=finish_refresh)

    def _on_stock_device_view(self, dev: str, result):
        if dev != self._current_device_path:
            return
        if isinstance(result, Exception):
            preserve_snapshot = bool(
                self._stock_snapshot is not None
                and self._stock_snapshot.target == Path(dev).resolve())
            if not preserve_snapshot:
                self._stock_snapshot = None
            self.stock_ipod_pages.set_error(
                str(result), preserve_snapshot=preserve_snapshot)
            if preserve_snapshot:
                self._flash_status(
                    "iPod refresh failed — showing last verified state")
            return
        composite_data = None
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], StockIpodSnapshot)
            and (
                result[1] is None
                or isinstance(result[1], CompositeDeviceData)
            )
        ):
            result, composite_data = result
        if not isinstance(result, StockIpodSnapshot):
            preserve_snapshot = bool(
                self._stock_snapshot is not None
                and self._stock_snapshot.target == Path(dev).resolve())
            if not preserve_snapshot:
                self._stock_snapshot = None
            self.stock_ipod_pages.set_error(
                "Unexpected stock-iPod snapshot",
                preserve_snapshot=preserve_snapshot,
            )
            if preserve_snapshot:
                self._flash_status(
                    "iPod refresh failed — showing last verified state")
            return
        self._stock_snapshot = result
        if result.model:
            self._remember_stock_model(result.identity_key, result.model)
            profile = self._device_profiles.get(dev)
            if profile is not None:
                self._device_profiles[dev] = self._effective_device_profile(
                    dev, profile)
        self.stock_ipod_pages.set_snapshot(result, composite_data)
        profile = self._device_profiles.get(dev)
        pre_composite_dual = bool(
            getattr(profile, "kind", "") == "dual_boot"
            and not (Path(dev) / ".soundbox" / "manifest.json").exists())
        self.stock_ipod_pages.set_playlist_deletion_available(
            getattr(profile, "kind", "") == "stock_ipod"
            or pre_composite_dual
            or getattr(profile, "kind", "") == "dual_boot")

    def _confirm_current_stock_model(self):
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        if not dev or profile is None:
            return
        current = (
            self._stock_snapshot.model if self._stock_snapshot is not None
            else self._stored_stock_model(dev, profile))
        model, ok = QInputDialog.getText(
            self,
            "Confirm stock iPod model",
            "Enter the model number shown under Settings → About\n"
            "(the current validated allowlist accepts MA446 only):",
            text=current,
        )
        if not ok:
            return
        model = normalize_model_number(model)
        if not supports_stock_write(model):
            QMessageBox.warning(
                self, "Model not validated",
                f"{model or 'No model number'} is not in the current MA446 "
                "stock-write allowlist. The saved confirmation was unchanged.")
            return
        identity_key = (
            self._stock_snapshot.identity_key
            if self._stock_snapshot is not None
            else self._stock_device_key(dev, profile))
        self._remember_stock_model(identity_key, model)
        self.stock_ipod_pages.set_loading(preserve_page=True)
        self._read_stock_device_view(dev, profile)

    def _device_menu(self, pos):
        item = self.device_tree.itemAt(pos)
        if not item:
            return
        dev = item.data(0, DEVPATH_ROLE)
        menu = QMenu(self)
        menu.addAction("Browse", lambda: self._on_device_click(item, 0))
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "stock_ipod":
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
            word = "change" if n == 1 else "changes"
            menu.addAction(f"Sync — {n} {word} pending",
                           lambda: self._sync_device(dev))
        else:
            menu.addAction("Sync (check again)", lambda: self._sync_device(dev))
        menu.addAction("Eject", lambda: self._eject(dev))
        menu.exec(self.device_tree.mapToGlobal(pos))

    def _preview_stock_ipod(self, dev: str):
        """Show stock-iPod readiness without writing a byte to the device."""
        profile = self._device_profiles.get(dev)
        if profile is None:
            return
        profile = self._effective_device_profile(dev, profile)
        audio = (
            [str(t.path) for t in self.lib.tracks_by_id.values()]
            if self.lib is not None else []
        )
        plan = plan_stock_ipod_export(profile, audio)
        formats = ", ".join(
            f"{suffix}: {count}"
            for suffix, count in plan["formats"].items()) or "none"
        QMessageBox.information(
            self, "Stock iPod sync readiness",
            f"Device: {profile.label}"
            f"{f' ({profile.model})' if profile.model else ''}\n"
            f"Tracks visible to planner: {plan['track_count']}\n"
            f"Formats: {formats}\n\n"
            f"{'Ready for an explicit playlist dry-run.' if plan['writable'] else 'Not ready for a whole-library plan.'}\n"
            f"{chr(10).join(plan['blockers']) if plan['blockers'] else 'Drop a small MP3/WAV playlist onto the iPod to plan it.'}\n\n"
            "This readiness check made no changes.")

    def _stock_write_model(self, dev: str, profile=None) -> str:
        """Resolve the same fail-closed MA446 authority for every stock write."""
        model = self._stored_stock_model(dev, profile)
        if not supports_stock_write(model):
            recovered = confirmed_model_from_recovery(dev)
            if supports_stock_write(recovered):
                model = recovered
                self._remember_stock_model(
                    self._stock_device_key(dev, profile), model)
        if not supports_stock_write(model):
            model, ok = QInputDialog.getText(
                self,
                "Confirm stock iPod model",
                "Enter the model number shown under Settings → About\n"
                "(the first write allowlist accepts MA446 only):",
                text=model,
            )
            if not ok:
                return ""
        if not supports_stock_write(model):
            QMessageBox.warning(
                self,
                "Model not validated",
                f"{model or 'No model number'} is not in the initial MA446 "
                "stock-write allowlist. No files were changed.",
            )
            return ""
        model = normalize_model_number(model)
        self._remember_stock_model(self._stock_device_key(dev, profile), model)
        return model

    def _begin_stock_ipod_sync(
            self, dev: str, playlists: list[Playlist], *, reconcile: bool = False):
        """Run the expensive, read-only database/media plan off the UI thread."""
        if any(_running(getattr(self, name, None)) for name in (
                "export_worker", "_resync_worker", "_stock_plan_worker",
                "_stock_commit_worker", "_stock_delete_plan_worker",
                "_stock_delete_commit_worker", "_stock_view_worker",
                "_dual_plan_worker", "_dual_commit_worker",
                "_delete_worker")):
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        from .workers import OneShotTask
        library = self.lib
        facts = device_facts(dev)
        identity_key = self._stock_device_key(dev, profile)
        shadow = (
            self.stock_ipod_shadow_store.load(identity_key)
            if reconcile else None)
        managed_names_before = tuple(
            " / ".join([
                *map(str, row.get("folder_path") or []),
                str(row.get("name") or ""),
            ])
            for row in ((shadow.playlists if shadow else {}) or {}).values()
            if isinstance(row, dict)
        )
        managed_keys_before = tuple(
            str(key) for key in ((shadow.playlists if shadow else {}) or {}))
        managed_track_rows_before = tuple(sorted({
            (
                int(row.get("dbid") or 0),
                str(row.get("device_path") or ""),
                str(row.get("sha256") or ""),
            )
            for row in ((shadow.sources if shadow else {}) or {}).values()
            if isinstance(row, dict) and int(row.get("dbid") or 0) > 0
        }))
        worker = OneShotTask(lambda: plan_stock_ipod_sync(
            library,
            playlists,
            dev,
            model_number=model,
            filesystem=device_fs(dev),
            derive_artists=True,
            artist_overrides=self.overrides,
            title_overrides=self.titles,
            art_cache=self.art_cache,
            stock_art_cache=self.stock_art_cache,
            identity_key=identity_key,
            serial_number=getattr(facts, "hardware_serial", ""),
            shadow_store=self.stock_ipod_shadow_store,
            managed_playlist_names_before=managed_names_before,
            managed_playlist_keys_before=managed_keys_before,
            managed_track_rows_before=managed_track_rows_before,
        ))
        self._stock_plan_worker = worker
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.done.connect(
            lambda result, d=dev: self._on_stock_ipod_plan(d, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker("_stock_plan_worker", ww))
        worker.start()

    def _on_stock_ipod_plan(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(result, Exception):
            QMessageBox.warning(
                self, "Stock iPod plan refused", f"{result}\n\nNo files were changed.")
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodSyncPlan):
            QMessageBox.critical(self, "Stock iPod plan failed", "Unexpected plan result.")
            return
        summary = result.summary()
        if not result.requires_commit:
            QMessageBox.information(
                self,
                "Stock iPod is up to date",
                "The read-only comparison found no media, playlist, metadata, "
                "artwork, or playback changes to publish.",
            )
            self._flash_status("iPod up to date")
            self._refresh_devices()
            return
        omission_preview = ""
        if result.omissions:
            shown = result.omissions[:6]
            omission_preview = (
                "\n\nOMITTED FOR STOCK-FIRMWARE SAFETY:\n"
                + "\n".join(
                    f"• {item.title}: {item.reason}" for item in shown)
            )
            if len(result.omissions) > len(shown):
                omission_preview += (
                    f"\n• …and {len(result.omissions) - len(shown)} more")
        answer = QMessageBox.question(
            self,
            "Commit stock iPod sync?",
            "The read-only plan passed every structural check.\n\n"
            f"Model: {summary['model']}\n"
            f"Playlists: {', '.join(summary['playlists'])}\n"
            f"Playlist entries selected: {summary['selected_tracks']}\n"
            f"Compatible entries included: {summary['source_tracks']}\n"
            f"Incompatible entries omitted: {summary['omitted_tracks']}\n"
            f"Already on iPod: {summary['reused_tracks']}\n"
            f"Existing track metadata updated: {summary['metadata_updates']}\n"
            f"New media files: {summary['new_tracks']} "
            f"({human(summary['bytes_to_copy'])})\n"
            f"Unused tracks removed: {summary['removed_tracks']}\n"
            f"Device media removed: {summary['media_files_to_delete']} "
            f"({human(summary['bytes_to_delete'])})\n"
            f"Artwork: {summary['artwork_tracks']} track(s), "
            f"{summary['unique_artwork']} unique "
            f"({human(summary['artwork_bytes_to_copy'])})\n"
            f"Artwork pixels written: {human(summary['artwork_pixel_bytes'])}\n"
            f"Final iPod library: {summary['final_tracks']} tracks\n"
            f"Pending playback records merged: {summary['merged_play_count_entries']}"
            f"{omission_preview}\n\n"
            "Continue will copy new media, save recovery files, and atomically "
            "replace iTunesDB last. Omissions apply only to this stock iPod; "
            "Rockbox exports and source playlists remain unchanged.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            self._flash_status("iPod sync cancelled — nothing written")
            self._refresh_devices()
            return
        self._commit_stock_ipod_plan(dev, result)

    def _commit_stock_ipod_plan(self, dev: str, plan: StockIpodSyncPlan):
        from .workers import OneShotTask
        worker = OneShotTask(lambda: commit_stock_ipod_sync(plan))
        self._stock_commit_worker = worker
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Syncing iPod")
        worker.done.connect(
            lambda result, d=dev: self._on_stock_ipod_commit(d, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker("_stock_commit_worker", ww))
        worker.start()

    def _on_stock_ipod_commit(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(result, Exception):
            QMessageBox.critical(
                self, "Stock iPod sync failed",
                f"{result}\n\nSoundBox attempted an exact database/media rollback.")
        else:
            warning_parts = []
            if result.shadow_warning:
                warning_parts.append(result.shadow_warning)
            if result.cleanup_failures:
                warning_parts.append(
                    "Some now-unindexed device files could not be removed:\n"
                    + "\n".join(result.cleanup_failures))
            warning = (
                "\n\n" + "\n\n".join(warning_parts)
                if warning_parts else "")
            QMessageBox.information(
                self,
                "Stock iPod sync complete",
                f"Copied {result.copied_tracks} new track(s); reused "
                f"{result.reused_tracks}.\n\n"
                f"Updated title/artist metadata for "
                f"{result.metadata_updates} existing track(s).\n\n"
                f"Removed {result.deleted_media_files} unused media file(s) "
                f"({human(result.deleted_bytes)}).\n\n"
                f"Linked artwork for {result.artwork_tracks} track(s) "
                f"from {result.unique_artwork} unique image(s); wrote "
                f"{human(result.artwork_pixel_bytes)} of pixels.\n\n"
                f"Recovery files:\n{result.recovery_directory}\n\n"
                f"Eject from SoundBox before listening.{warning}",
            )
            self._flash_status("iPod synced")
        if isinstance(result, Exception):
            self._refresh_devices()
        else:
            self._refresh_device_surfaces(dev)

    def _begin_dual_ipod_sync(
            self,
            dev: str,
            playlists: list[Playlist],
            *,
            reconcile: bool = False,
            respect_selected_order: bool = False,
            preconfirmed: bool = False,
            confirmed: bool = False,
            action: str = "sync",
            expected_playlist_rows: dict[str, dict] | None = None,
    ):
        """Build the composite stock/Rockbox plan without target writes."""
        if any(_running(getattr(self, name, None)) for name in (
                "export_worker", "_resync_worker", "_stock_plan_worker",
                "_stock_commit_worker", "_dual_plan_worker",
                "_dual_commit_worker", "_stock_view_worker",
                "_delete_worker")):
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._pending_device_operation is not None:
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_dual_ipod_sync(
                    dev,
                    playlists,
                    reconcile=reconcile,
                    respect_selected_order=respect_selected_order,
                    preconfirmed=preconfirmed,
                    confirmed=confirmed,
                    action=action,
                    expected_playlist_rows=expected_playlist_rows,
                )):
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        self._dual_active_device = dev
        self._dual_active_playlist_keys = {
            device_manifest.playlist_key(playlist) for playlist in playlists}
        self._dual_active_preconfirmed = preconfirmed
        self._dual_active_confirmed = confirmed
        self._dual_active_action = action
        self._dual_active_track_total = sum(
            len(self.lib.resolve(playlist)) for playlist in playlists)
        from .workers import ProgressTask
        library = self.lib
        facts = device_facts(dev)
        identity_key = self._stock_device_key(dev, profile)
        worker = ProgressTask(
            lambda callback: plan_dual_ipod_sync(
                library,
                playlists,
                dev,
                model_number=model,
                filesystem=device_fs(dev),
                rockbox_media_cache=self.rockbox_media_cache,
                rockbox_art_cache=self.rockbox_art_cache,
                derive_artists=True,
                artist_overrides=self.overrides,
                title_overrides=self.titles,
                art_cache=self.art_cache,
                stock_art_cache=self.stock_art_cache,
                stock_shadow_store=self.stock_ipod_shadow_store,
                identity_key=identity_key,
                serial_number=getattr(facts, "hardware_serial", ""),
                reconcile=reconcile,
                respect_selected_order=respect_selected_order,
                on_progress=callback,
                expected_playlist_rows=expected_playlist_rows,
            ),
            log_label="Dual iPod planning",
        )
        self._dual_plan_worker = worker
        self._exporting = True
        self.progress.setRange(0, 1000)
        if preconfirmed and self._dual_batch_total_tracks > 0:
            self.progress.setValue(round(
                1000 * self._dual_batch_done_tracks
                / self._dual_batch_total_tracks))
        else:
            self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setVisible(True)
        self._set_status_count()
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.progress.connect(self._on_dual_ipod_plan_progress)
        worker.done.connect(
            lambda result, d=dev: self._on_dual_ipod_plan(d, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker("_dual_plan_worker", ww))
        worker.start()

    def _on_dual_ipod_plan_progress(
            self, done: int, total: int, _label: str):
        """Map planning into the first 30% of this operation's bar segment."""
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        fraction = done / total
        if self._dual_active_preconfirmed and self._dual_batch_total_tracks > 0:
            completed = self._dual_batch_done_tracks
            active = self._dual_active_track_total
            value = round(1000 * (
                completed + active * 0.30 * fraction
            ) / self._dual_batch_total_tracks)
        else:
            value = round(300 * fraction)
        self.progress.setRange(0, 1000)
        self.progress.setValue(max(self.progress.value(), value))
        self.progress.setVisible(True)
        # The plan and write callbacks count different work. Keeping the track
        # counter hidden here avoids a misleading backwards jump at commit.
        self._start_pulse("Planning iPod")

    def _on_dual_ipod_plan(self, dev: str, result):
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(result, Exception):
            was_preconfirmed = self._dual_active_preconfirmed
            self._exporting = False
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._dual_active_track_total = 0
            self.progress.setVisible(False)
            self._set_status_count()
            try:
                self.rockbox_media_cache.clear()
            except Exception:
                pass
            if was_preconfirmed:
                self._dual_sync_queue.pop(dev, None)
                self._reset_dual_batch()
            QMessageBox.warning(
                self,
                "Dual iPod plan refused",
                f"{result}\n\nNo device files were changed."
                + (" Remaining queued jobs were cancelled."
                   if was_preconfirmed else ""),
            )
            self._refresh_devices()
            return
        if not isinstance(result, DualIpodSyncPlan):
            was_preconfirmed = self._dual_active_preconfirmed
            self._exporting = False
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._dual_active_track_total = 0
            self.progress.setVisible(False)
            self._set_status_count()
            if was_preconfirmed:
                self._dual_sync_queue.pop(dev, None)
                self._reset_dual_batch()
            QMessageBox.critical(
                self, "Dual iPod plan failed", "Unexpected plan result.")
            return
        summary = result.summary()
        if not result.requires_commit:
            if self._dual_active_preconfirmed:
                self._dual_batch_done_tracks = min(
                    self._dual_batch_total_tracks,
                    self._dual_batch_done_tracks
                    + self._dual_active_track_total,
                )
                self._dual_batch_results.append((result, None))
                self._dual_active_device = ""
                self._dual_active_playlist_keys.clear()
                self._dual_active_preconfirmed = False
                self._dual_active_confirmed = False
                self._dual_active_action = "sync"
                self._dual_active_track_total = 0
                self._start_next_confirmed_dual_job(dev)
                return
            self._exporting = False
            action = self._dual_active_action
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self.progress.setValue(1000)
            self.progress.setVisible(False)
            self._set_status_count()
            try:
                self.rockbox_media_cache.clear()
            except Exception:
                pass
            QMessageBox.information(
                self,
                "Nothing to delete" if action == "delete" else "iPod is up to date",
                (
                    "The selected playlists are already absent and no managed "
                    "media became unreferenced."
                    if action == "delete" else
                    "The no-write plan found no stock database, media, artwork, "
                    "playlist, or ownership-manifest changes."
                ),
            )
            self._flash_status(
                "Nothing to delete" if action == "delete" else "iPod up to date")
            self._refresh_devices()
            queued = self._dual_sync_queue.pop(dev, [])
            if queued:
                worker = self._dual_plan_worker

                def start_queued():
                    QTimer.singleShot(
                        0, lambda: self._begin_dual_ipod_sync(dev, queued))

                if worker is not None:
                    worker.finished.connect(start_queued)
                else:
                    start_queued()
            return
        if self._dual_active_preconfirmed:
            self._commit_dual_ipod_plan(dev, result)
            return
        if self._dual_active_confirmed:
            self._commit_dual_ipod_plan(dev, result)
            return
        omission_lines = []
        for item in list(result.missing[:3]) + list(result.unsupported[:3]):
            omission_lines.append(f"• {item.title}: {item.reason}")
        omissions = (
            "\n\nOmitted:\n" + "\n".join(omission_lines)
            if omission_lines else "")
        added_audio = (
            len(result.stock.copies)
            + sum(1 for item in result.media_copies if item.kind == "rockbox-media")
        )
        updated_audio = (
            len(result.stock.rewrites)
            + sum(1 for item in result.file_rewrites if item.kind == "rockbox-media")
        )
        removed_audio = summary["orphan_media_files"]
        recovered_audio = (
            summary["recovered_stock_tracks"]
            + summary["recovered_rockbox_media_files"]
        )
        recovered_line = (
            f"Recovery candidates: {recovered_audio} "
            f"({human(summary['recovered_rockbox_bytes'])})\n"
            if recovered_audio else ""
        )
        playlist_word = "playlist" if summary["rockbox_playlists"] == 1 else "playlists"
        answer = QMessageBox.question(
            self,
            "Sync iPod?",
            "Ready to sync iPod.\n\n"
            f"{summary['stock_safe_sources'] + summary['rockbox_only_sources']} songs\n"
            f"• {summary['stock_safe_sources']} Both\n"
            f"• {summary['rockbox_only_sources']} Rockbox only\n\n"
            f"{summary['rockbox_playlists']} {playlist_word}\n"
            f"{recovered_line}"
            f"Audio changes: {added_audio} add · {updated_audio} update · "
            f"{removed_audio} remove\n"
            f"Playlist files updated: {summary['rockbox_playlist_writes']}\n"
            f"Rockbox database auto-update: "
            f"{'enable' if summary['rockbox_settings_updated'] else 'on'}\n"
            f"Unreferenced files removed: "
            f"{summary['orphan_media_files'] + summary['orphan_artwork_files'] + summary['rockbox_clutter_files']} "
            f"({human(summary['orphan_bytes_to_delete'])})"
            f"{omissions}\n\n"
            "SoundBox will update both libraries safely. Source files remain "
            "unchanged. Continue?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            self._exporting = False
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            try:
                self.rockbox_media_cache.clear()
            except Exception:
                pass
            self._flash_status("iPod sync cancelled — nothing written")
            self._refresh_devices()
            return
        self._commit_dual_ipod_plan(dev, result)

    def _commit_dual_ipod_plan(self, dev: str, plan: DualIpodSyncPlan):
        from .workers import ProgressTask
        worker = ProgressTask(
            lambda callback: commit_dual_ipod_sync(
                plan, on_progress=callback),
            log_label="Dual iPod commit",
        )
        self._dual_commit_worker = worker
        self._exporting = True
        total = plan.progress_total
        self.progress.setRange(0, 1000)
        if self._dual_active_preconfirmed and self._dual_batch_total_tracks > 0:
            initial = round(1000 * (
                self._dual_batch_done_tracks
                + self._dual_active_track_total * 0.30
            ) / self._dual_batch_total_tracks)
        else:
            initial = 300
        self.progress.setValue(max(self.progress.value(), initial))
        self.progress.setFormat("%p%")
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(False)
        self._start_pulse(
            "Syncing iPod",
            keep_count=(
                self._dual_batch_total_tracks > 0
                if self._dual_active_preconfirmed else total > 0),
        )
        self._set_status_count(
            self._dual_batch_done_tracks
            if self._dual_active_preconfirmed else 0,
            self._dual_batch_total_tracks
            if self._dual_active_preconfirmed else total,
        )
        worker.progress.connect(self._on_dual_ipod_progress)
        worker.done.connect(
            lambda result, d=dev, p=plan: self._on_dual_ipod_commit(
                d, result, p))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker("_dual_commit_worker", ww))
        worker.start()

    def _on_dual_ipod_progress(self, done: int, total: int, _label: str):
        total = max(0, total)
        done = max(0, min(done, total))
        if self._dual_active_preconfirmed:
            current = (
                self._dual_active_track_total * done // max(1, total))
            batch_done = min(
                self._dual_batch_total_tracks,
                self._dual_batch_done_tracks + current,
            )
            self._start_pulse(
                "Syncing iPod", keep_count=self._dual_batch_total_tracks > 0)
            self._set_status_count(
                batch_done, self._dual_batch_total_tracks)
            value = round(1000 * (
                self._dual_batch_done_tracks
                + self._dual_active_track_total * (
                    0.30 + 0.70 * done / max(1, total))
            ) / max(1, self._dual_batch_total_tracks))
            self.progress.setRange(0, 1000)
            self.progress.setValue(max(self.progress.value(), value))
            self.progress.setVisible(True)
            return
        self._start_pulse("Syncing iPod", keep_count=total > 0)
        self._set_status_count(done, total)
        value = round(300 + 700 * done / max(1, total))
        self.progress.setRange(0, 1000)
        self.progress.setValue(max(self.progress.value(), value))
        self.progress.setVisible(True)

    def _on_dual_ipod_commit(
            self, dev: str, result, plan: DualIpodSyncPlan | None = None):
        self._exporting = False
        self._stop_pulse()
        self.status_label.setText("Ready")
        try:
            self.rockbox_media_cache.clear()
        except Exception as exc:
            LOG.warning("Rockbox media staging cleanup failed: %s", exc)
        if not isinstance(result, Exception) and plan is not None:
            playlist_paths = ", ".join(
                str(item.relative_path)
                for item in getattr(plan, "playlists", ()))
            LOG.info(
                "Dual iPod generation %d committed; Rockbox playlists: %s",
                int(getattr(plan, "generation", 0)),
                playlist_paths or "none",
            )
        if self._dual_active_preconfirmed:
            if isinstance(result, Exception):
                self.progress.setVisible(False)
                self._set_status_count()
                self._show_dual_ipod_failure(result)
                self._dual_active_device = ""
                self._dual_active_playlist_keys.clear()
                self._dual_active_preconfirmed = False
                self._dual_active_confirmed = False
                self._dual_active_action = "sync"
                self._dual_active_track_total = 0
                self._dual_sync_queue.pop(dev, None)
                self._reset_dual_batch()
                self._refresh_devices()
                return
            if plan is not None:
                self._dual_batch_results.append((plan, result))
            self._dual_batch_done_tracks = min(
                self._dual_batch_total_tracks,
                self._dual_batch_done_tracks + self._dual_active_track_total,
            )
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(
                1000 * self._dual_batch_done_tracks
                / max(1, self._dual_batch_total_tracks)))
            self._set_status_count(
                self._dual_batch_done_tracks,
                self._dual_batch_total_tracks,
            )
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._dual_active_track_total = 0
            self._start_next_confirmed_dual_job(dev)
            return
        action = self._dual_active_action
        self.progress.setValue(1000)
        self.progress.setVisible(False)
        if isinstance(result, Exception):
            self._show_dual_ipod_failure(result)
        else:
            cleanup_warning = (
                "\n\nSome now-unreferenced files could not be removed:\n"
                + "\n".join(result.cleanup_failures)
                if result.cleanup_failures else "")
            QMessageBox.information(
                self,
                "iPod deletion complete"
                if action == "delete" else "iPod sync complete",
                f"Stock: copied {result.stock.copied_tracks}, sanitized "
                f"{result.stock.rewritten_tracks}, reused "
                f"{result.stock.reused_tracks}, recovered "
                f"{result.stock.recovered_tracks}.\n\n"
                f"Rockbox: copied {result.copied_media} media file(s), "
                f"recovered {result.recovered_media} completed media file(s), "
                f"{result.copied_artwork} cover(s), and "
                f"{result.playlists} playlist(s).\n\n"
                f"Removed {result.deleted_orphans} unreferenced device file(s) "
                f"({human(result.deleted_orphan_bytes)})."
                f"{cleanup_warning}\n\n"
                f"Recovery files:\n{result.stock.recovery_directory}\n\n"
                "Eject from SoundBox before listening.",
            )
            self._flash_status("iPod synced")
        if isinstance(result, Exception):
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._refresh_devices()
        else:
            self._refresh_device_surfaces(dev)
            queued = self._dual_sync_queue.pop(dev, [])
            if queued:
                worker = self._dual_commit_worker

                def start_queued():
                    self._dual_active_device = ""
                    self._dual_active_playlist_keys.clear()
                    self._dual_active_confirmed = False
                    self._dual_active_action = "sync"
                    QTimer.singleShot(
                        0, lambda: self._start_confirmed_dual_jobs(dev, queued))

                if worker is not None:
                    worker.finished.connect(start_queued)
                else:
                    start_queued()
            else:
                self._dual_active_device = ""
                self._dual_active_playlist_keys.clear()
                self._dual_active_confirmed = False
                self._dual_active_action = "sync"

    def _show_dual_ipod_failure(self, result) -> None:
        interrupted = "Device target changed or was remounted" in str(result)
        detail = (
            "The committed iPod library was left at its last verified "
            "generation. Completed whole-file copies may remain unowned; "
            "re-queue the same playlist and SoundBox will verify and reuse "
            "them automatically."
            if interrupted else
            "SoundBox attempted the coordinated stock/Rockbox rollback. "
            "The next device scan will report any remaining recovery state."
        )
        QMessageBox.critical(
            self,
            "Dual iPod sync failed",
            f"{result}\n\n{detail}",
        )

    def _reset_dual_batch(self) -> None:
        self._dual_batch_keys.clear()
        self._dual_batch_names.clear()
        self._dual_batch_total_tracks = 0
        self._dual_batch_done_tracks = 0
        self._dual_batch_results.clear()

    def _start_next_confirmed_dual_job(self, dev: str) -> None:
        queued = self._dual_sync_queue.get(dev, [])
        if queued:
            # Coalesce everything confirmed while the preceding job was busy.
            # Planning each queued playlist as a separate generation repeatedly
            # reparsed the same iTunesDB and revalidated the same device media;
            # on classic iPods that extra sustained USB traffic can provoke a
            # transport reset. A later drop can still form the next batch if it
            # arrives after this combined plan starts.
            next_playlists = list(queued)
            self._dual_sync_queue[dev] = []
            worker = (
                self._dual_commit_worker
                if _running(self._dual_commit_worker)
                else self._dual_plan_worker
            )

            def start_next():
                QTimer.singleShot(
                    0,
                    lambda: self._begin_dual_ipod_sync(
                        dev, next_playlists, preconfirmed=True),
                )

            if worker is not None and _running(worker):
                worker.finished.connect(start_next)
            else:
                start_next()
            return

        self._dual_sync_queue.pop(dev, None)
        self._exporting = False
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self.progress.setVisible(False)
        self._set_status_count()
        committed = [
            result for _plan, result in self._dual_batch_results
            if result is not None
        ]
        copied = sum(
            result.stock.copied_tracks + result.copied_media
            for result in committed)
        refreshed = sum(
            result.stock.rewritten_tracks for result in committed)
        removed = sum(result.deleted_orphans for result in committed)
        playlist_names = "\n".join(
            f"• {name}" for name in self._dual_batch_names)
        QMessageBox.information(
            self,
            "iPod queue complete",
            f"{len(self._dual_batch_names)} playlist job"
            f"{'s' if len(self._dual_batch_names) != 1 else ''} complete\n"
            f"{self._dual_batch_total_tracks} queued track entries\n\n"
            f"{playlist_names}\n\n"
            f"Media copied: {copied}\n"
            f"Media metadata refreshed: {refreshed}\n"
            f"Unreferenced files removed: {removed}\n\n"
            "Device, Songs, and Playlists are refreshing from the committed "
            "device state. Eject from SoundBox before listening.",
        )
        self._flash_status("iPod synced")
        self._refresh_device_surfaces(dev)
        self._reset_dual_batch()

    def _begin_stock_playlist_delete(self, selections):
        """Plan deletion of the checked user playlists from the stock iPod."""
        dev = self._current_device_path
        if not dev or self._stock_snapshot is None:
            return
        profile = self._device_profiles.get(dev)
        dual_boot = getattr(profile, "kind", "") == "dual_boot"
        pre_composite_dual = bool(
            dual_boot
            and not (Path(dev) / ".soundbox" / "manifest.json").exists())
        raw_selections = tuple(selections or ())
        if dual_boot and not pre_composite_dual:
            managed_keys: list[str] = []
            for raw in raw_selections:
                if not isinstance(raw, (tuple, list)) or len(raw) != 2:
                    continue
                identity = str(raw[0] or "")
                if identity.startswith("managed:"):
                    key = identity.removeprefix("managed:")
                    if key and key not in managed_keys:
                        managed_keys.append(key)
            if managed_keys:
                self._begin_composite_playlist_delete(
                    dev, tuple(managed_keys))
            return
        identities: list[tuple[int, str]] = []
        seen_ids: set[int] = set()
        for raw in raw_selections:
            if not isinstance(raw, (tuple, list)) or len(raw) != 2:
                continue
            playlist_id = str(raw[0] or "")
            if not playlist_id.isdecimal() or int(playlist_id) <= 0:
                continue
            numeric_id = int(playlist_id)
            if numeric_id in seen_ids:
                continue
            seen_ids.add(numeric_id)
            identities.append((numeric_id, str(raw[1] or "Untitled playlist")))
        if not identities:
            return
        if self._stock_snapshot.target != Path(dev).resolve():
            QMessageBox.warning(
                self, "Device changed", "Refresh the stock iPod before deleting.")
            return
        if any(_running(getattr(self, name, None)) for name in (
                "export_worker", "_resync_worker", "_stock_plan_worker",
                "_stock_commit_worker", "_stock_delete_plan_worker",
                "_stock_delete_commit_worker", "_stock_view_worker",
                "_dual_plan_worker", "_dual_commit_worker",
                "_delete_worker")):
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        from .workers import OneShotTask
        worker = OneShotTask(lambda: plan_stock_ipod_playlist_delete(
            dev,
            [playlist_id for playlist_id, _title in identities],
            model_number=model,
            filesystem=device_fs(dev),
            stock_art_cache=self.stock_art_cache,
            allow_dual_boot=pre_composite_dual,
        ))
        self._stock_delete_plan_worker = worker
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.done.connect(
            lambda result, d=dev, expected=tuple(identities):
            self._on_stock_playlist_delete_plan(d, expected, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker(
                "_stock_delete_plan_worker", ww))
        worker.start()

    def _apply_composite_playlist_order(self, requested_keys) -> None:
        """Publish the Device-tab sequence to iTunesDB and Rockbox together."""
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        if (
            not dev
            or self.lib is None
            or getattr(profile, "kind", "") != "dual_boot"
        ):
            return
        try:
            manifest = device_manifest.load_composite(dev)
            rows = manifest.get("playlists") or {}
            order = tuple(str(key) for key in requested_keys if str(key))
            if len(order) != len(set(order)) or set(order) != set(rows):
                raise ValueError(
                    "Playlist order no longer matches the committed iPod state")
            current = device_manifest.current_playlists_by_key(
                self.lib, self._sync_playlists())
            playlists = [current[key] for key in order]
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Playlist order unavailable",
                f"{exc}\n\nNo device files were changed.",
            )
            self._refresh_device_surfaces(dev)
            return
        answer = QMessageBox.question(
            self,
            "Apply iPod playlist order?",
            f"Apply this order to {len(playlists)} playlists in both the stock "
            "iPod library and Rockbox Playlist Catalogue?\n\n"
            "SoundBox will keep the visible playlist names unchanged and will "
            "not duplicate any songs.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self._begin_dual_ipod_sync(
            dev,
            playlists,
            reconcile=True,
            respect_selected_order=True,
            confirmed=True,
            action="order",
        )

    def _begin_composite_playlist_delete(
            self,
            dev: str,
            selected_keys: tuple[str, ...],
            *,
            confirmed: bool = False,
    ):
        """Remove checked schema-3 playlists through the unified planner."""
        try:
            current_manifest = device_manifest.load_composite(dev)
            prior = current_manifest.get("playlists") or {}
            order = current_manifest.get("playlist_order")
            if not isinstance(order, list):
                order = list(prior)
            selected = {str(key) for key in selected_keys if str(key)}
            unknown = selected - {str(key) for key in prior}
            if unknown:
                raise ValueError(
                    f"Selected playlist is not schema-3 managed: "
                    f"{sorted(unknown)[0]}")
            removed_keys = selected
            selected_names = []
            for key in order:
                if str(key) not in removed_keys:
                    continue
                row = prior.get(str(key)) or {}
                if isinstance(row, dict):
                    parts = [
                        *map(str, row.get("folder_path") or []),
                        str(row.get("name") or "Untitled playlist"),
                    ]
                    selected_names.append(" / ".join(filter(None, parts)))
            if not confirmed:
                multiple = len(selected_names) != 1
                shown = "\n".join(
                    f"• {name}" for name in selected_names[:8])
                if len(selected_names) > 8:
                    shown += f"\n• …and {len(selected_names) - 8} more"
                answer = QMessageBox.question(
                    self,
                    "Delete iPod playlists?"
                    if multiple else "Delete iPod playlist?",
                    f"Delete the selected playlist"
                    f"{'s' if multiple else ''} from {Path(dev).name}?\n\n"
                    f"{shown}\n\n"
                    "Songs unused by every remaining managed playlist will "
                    "also be removed from both the stock and Rockbox "
                    "libraries. Any other managed playlist changes will be "
                    "reconciled in this same operation. Planning and writing "
                    "will appear as one continuous operation. Source files "
                    "remain unchanged.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer != QMessageBox.Yes:
                    self._flash_status(
                        "iPod deletion cancelled — nothing written")
                    return
            current_by_key = {
                device_manifest.playlist_key(playlist): playlist
                for playlist in self._sync_playlists()
            }
            remaining: list[Playlist] = []
            for raw_key in order:
                key = str(raw_key)
                if key in removed_keys:
                    continue
                playlist = current_by_key.get(key)
                if playlist is None:
                    row = prior.get(key)
                    if not isinstance(row, dict):
                        raise ValueError(
                            f"Managed playlist snapshot is invalid: {key}")
                    tracks = row.get("tracks")
                    folder = row.get("folder_path")
                    if (
                        not isinstance(tracks, list)
                        or not isinstance(folder, list)
                    ):
                        raise ValueError(
                            f"Managed playlist snapshot is invalid: {key}")
                    playlist = Playlist(
                        str(row.get("name") or ""),
                        [str(part) for part in folder],
                        [str(track_id) for track_id in tracks],
                        pid=str(row.get("pid") or ""),
                    )
                    if device_manifest.playlist_key(playlist) != key:
                        raise ValueError(
                            f"Managed playlist identity is invalid: {key}")
                remaining.append(playlist)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "iPod playlist deletion refused",
                f"{exc}\n\nNo device files were changed.",
            )
            return
        self._begin_dual_ipod_sync(
            dev,
            remaining,
            reconcile=True,
            confirmed=True,
            action="delete",
        )

    def _on_stock_playlist_delete_plan(
            self, dev: str, expected: tuple[tuple[int, str], ...], result):
        self._exporting = False
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(result, Exception):
            QMessageBox.warning(
                self,
                "Stock iPod delete refused",
                f"{result}\n\nNo playlists, database records, or media files changed.",
            )
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodDeletePlan):
            QMessageBox.critical(
                self, "Stock iPod delete failed", "Unexpected plan result.")
            return
        summary = result.summary()
        planned_title = ", ".join(summary["playlists"])
        expected_ids = tuple(playlist_id for playlist_id, _title in expected)
        if tuple(result.playlist_ids) != expected_ids:
            QMessageBox.warning(
                self,
                "Playlists changed",
                "The selected playlist identities changed during planning. "
                "Nothing was modified.",
            )
            return
        multiple = len(result.playlist_names) != 1
        retained_note = (
            "No track records or media files will be removed."
            if summary["deleted_track_records"] == 0
            else "Only songs unused by every remaining user playlist will be removed."
        )
        commit_note = (
            "Continue will save database/artwork recovery files, publish the "
            "new iTunesDB, then delete the now-unreferenced device media. "
            "Deleted device media is not retained in recovery because that "
            "would not free space; the source library remains unchanged."
        )
        answer = QMessageBox.question(
            self,
            "Delete stock iPod playlists?" if multiple
            else "Delete stock iPod playlist?",
            "The read-only deletion plan passed every structural check.\n\n"
            f"{'Playlists' if multiple else 'Playlist'}: {planned_title}\n"
            f"Track records becoming unused: {summary['deleted_track_records']}\n"
            f"Media files deleted: {summary['deleted_media_files']} "
            f"({human(summary['bytes_to_delete'])})\n"
            f"Remaining user playlists: {summary['remaining_playlists']}\n"
            f"Remaining iPod library: {summary['final_tracks']} tracks\n"
            f"Pending playback records merged: "
            f"{summary['merged_play_count_entries']}\n\n"
            f"{retained_note}\n\n"
            f"{commit_note}",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            self._flash_status("iPod deletion cancelled — nothing written")
            self._refresh_devices()
            return
        self._commit_stock_playlist_delete(dev, result)

    def _commit_stock_playlist_delete(self, dev: str, plan: StockIpodDeletePlan):
        from .workers import OneShotTask
        worker = OneShotTask(lambda: commit_stock_ipod_playlist_delete(plan))
        self._stock_delete_commit_worker = worker
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Syncing iPod")
        worker.done.connect(
            lambda result, d=dev: self._on_stock_playlist_delete_commit(d, result))
        worker.finished.connect(
            lambda ww=worker: self._finish_worker(
                "_stock_delete_commit_worker", ww))
        worker.start()

    def _on_stock_playlist_delete_commit(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        self.status_label.setText("Ready")
        if isinstance(result, Exception):
            QMessageBox.critical(
                self,
                "Stock iPod deletion failed",
                f"{result}\n\nSoundBox attempted an exact database/artwork rollback.",
            )
        elif result.cleanup_failures:
            multiple = len(result.playlists) != 1
            QMessageBox.warning(
                self,
                "Playlists deleted; media cleanup incomplete" if multiple
                else "Playlist deleted; media cleanup incomplete",
                f"Deleted {'playlists' if multiple else 'playlist'}: "
                f"{', '.join(result.playlists)}\n"
                f"Removed {result.deleted_track_records} database track(s) and "
                f"{result.deleted_media_files} media file(s).\n\n"
                "The iPod database is consistent, but these unindexed files "
                f"could not be removed:\n{chr(10).join(result.cleanup_failures)}\n\n"
                f"Recovery files:\n{result.recovery_directory}",
            )
        else:
            multiple = len(result.playlists) != 1
            QMessageBox.information(
                self,
                "Stock iPod playlists deleted" if multiple
                else "Stock iPod playlist deleted",
                f"Deleted {'playlists' if multiple else 'playlist'}: "
                f"{', '.join(result.playlists)}\n"
                f"Removed {result.deleted_track_records} unused track record(s) "
                f"and {result.deleted_media_files} media file(s) "
                f"({human(result.deleted_bytes)}).\n\n"
                f"Remaining: {result.remaining_playlists} user playlist(s), "
                f"{result.remaining_tracks} track(s).\n\n"
                f"Recovery files:\n{result.recovery_directory}\n\n"
                "Eject from SoundBox before listening.",
            )
            self._flash_status(
                "iPod playlists deleted" if multiple
                else "iPod playlist deleted")
        if isinstance(result, Exception):
            self._refresh_devices()
        else:
            self._refresh_device_surfaces(dev)

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
        self._start_pulse("Verifying")
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

    def _confirm_ipod_sync(self, dev: str) -> bool:
        answer = QMessageBox.question(
            self,
            "Sync iPod?",
            f"Sync the managed library on {Path(dev).name} now?\n\n"
            "SoundBox will compare the current playlists and metadata, then "
            "apply only the required stock/Rockbox changes. Planning and "
            "writing will appear as one continuous operation. Source files "
            "remain unchanged.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _sync_device(self, dev: str):
        if self.lib is None or not dev:
            return
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "stock_ipod":
            identity_key = self._stock_device_key(dev, profile)
            shadow = self.stock_ipod_shadow_store.load(identity_key)
            if shadow is None:
                QMessageBox.information(
                    self,
                    "Stock Sync baseline unavailable",
                    "Drag a playlist onto this iPod once to establish its "
                    "guarded stock-sync baseline.",
                )
                return
            try:
                playlists = device_manifest.managed_current_playlists(
                    {"playlists": shadow.playlists},
                    self.lib,
                    self._sync_playlists(),
                )
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Stock Sync unavailable",
                    f"{exc}\n\nNo device files were changed.",
                )
                return
            self._begin_stock_ipod_sync(dev, playlists, reconcile=True)
            return
        if profile is not None and profile.kind == "dual_boot":
            if not self._confirm_ipod_sync(dev):
                self._flash_status("iPod sync cancelled — nothing written")
                return
            try:
                composite_manifest = device_manifest.load_composite(dev)
                playlists = device_manifest.managed_current_playlists(
                    composite_manifest, self.lib, self._sync_playlists())
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Composite Sync unavailable",
                    f"{exc}\n\nNo device files were changed. Drag a playlist "
                    "onto this iPod to establish composite ownership first.",
                )
                return
            self._begin_dual_ipod_sync(
                dev,
                playlists,
                reconcile=True,
                confirmed=True,
                action="sync",
            )
            return
        if self._resync_worker is not None and self._resync_worker.isRunning():
            return
        LOG.info("Sync device: %s", dev)
        w = ResyncWorker(
            self.lib, dev, self.overrides, self.titles, self.art_cache,
            self.rockbox_media_cache, self.rockbox_art_cache,
            self._sync_playlists())
        self._resync_worker = w
        w.done.connect(self._on_resync_done)
        w.finished.connect(lambda ww=w: self._finish_worker("_resync_worker", ww))
        self._exporting = True  # pauses the device poll + keeps bg art status quiet
        # NO % bar: a diff-driven sync is fast and usually just rewrites an m3u8 (the
        # bar only flashed 27→100 and lied). Animated "SYNCING…" reads honestly, like
        # DELETE.
        self.progress.setVisible(False)
        self._start_pulse("Syncing")
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
        # Playlist views expose both scopes: remove only from the current
        # playlist, or remove globally from SoundBox's Collection. Collection
        # and other derived all-track views expose only the global action.
        self._add_track_removal_actions(menu, ids)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _add_track_removal_actions(self, menu: QMenu, ids: list[str]):
        """Add the scope-explicit reversible removal actions for this view.

        rekordbox playlists receive a SoundBox-side veto only; custom playlists
        are edited directly. Collection removal hides the track from every
        SoundBox view/export but never writes rekordbox or touches source audio.
        """
        n = len(ids)
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
        menu.addAction("Remove from Collection" if n == 1
                       else f"Remove {n} tracks from Collection",
                       lambda: self._delete_tracks(ids))

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
        """Remove from SOUNDBOX ONLY — never rekordbox or the file on disk.
        The track stops appearing in every view/export and remains restorable
        from Settings, so a library cleanup pass is always reversible."""
        n = len(ids)
        if QMessageBox.question(
                self, "Remove from Collection",
                f"Remove {n} track{'s' if n > 1 else ''} from Collection?\n\n"
                "This hides them from every SoundBox view and export. rekordbox "
                "and the files on disk are NOT touched. Restore anytime in Settings."
        ) != QMessageBox.Yes:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M")  # minute precision — same-day deletes sort truthfully
        self.hidden.update(ids)
        for i in ids:
            self.hidden_dates.setdefault(i, stamp)
        self._after_track_edit(f"Removed {n} from Collection ✓")

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
        self._queue_stock_artwork(tids)
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
            self._queue_stock_artwork((tid,))

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
            self._queue_stock_artwork((tid,))
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
        QTimer.singleShot(0, lambda: self._maybe_prepare_stock_artwork(True))
        if getattr(self, "_art_restart", False):  # a re-scan asked to restart
            self._art_restart = False
            self._start_art_fill()

    def _queue_stock_artwork(self, track_ids):
        if self.lib is None:
            return
        for track_id in track_ids:
            image = self.art_cache.image_path(str(track_id))
            if image is None:
                continue
            try:
                self._stock_art_pending.add(image.resolve(strict=True))
            except OSError:
                continue
        if not _running(self._stock_art_worker):
            QTimer.singleShot(0, self._start_pending_stock_artwork)

    def _start_pending_stock_artwork(self):
        if not self._stock_art_pending or _running(self._stock_art_worker):
            return
        images = tuple(sorted(self._stock_art_pending, key=str))
        self._stock_art_pending.clear()
        self._maybe_prepare_stock_artwork(images=images)

    def _maybe_prepare_stock_artwork(
        self,
        rescan_if_running: bool = False,
        *,
        images: tuple[Path, ...] | None = None,
    ):
        """Warm the disposable stock-iPod art cache without blocking sync/UI."""
        if self.lib is None:
            return
        if _running(self._stock_art_worker):
            if rescan_if_running:
                self._stock_art_rescan = True
            return
        worker = StockArtworkPrepWorker(
            self.lib, self.art_cache, self.stock_art_cache, images)
        self._stock_art_worker = worker
        worker.set_playback_active(self.player_bar.is_playing())
        worker.finished.connect(
            lambda ww=worker: self._on_stock_artwork_finished(ww))
        worker.start(QThread.LowPriority)

    def _on_stock_artwork_finished(self, worker):
        if self._stock_art_worker is worker:
            self._stock_art_worker = None
        worker.deleteLater()
        if self._stock_art_pending:
            QTimer.singleShot(0, self._start_pending_stock_artwork)
        elif self._stock_art_rescan:
            self._stock_art_rescan = False
            QTimer.singleShot(0, self._maybe_prepare_stock_artwork)

    def _arm_waveform_rechecks(self):
        """Keep a loaded source under cheap identity watch between refreshes."""
        self._waveform_rechecks_enabled = True
        if (not _running(self._wf_fill_worker)
                and not self._waveform_recheck_timer.isActive()):
            self._waveform_recheck_timer.start()

    def _schedule_waveform_recheck(self):
        if (self._waveform_rechecks_enabled and self.lib is not None
                and not self._waveform_recheck_timer.isActive()):
            self._waveform_recheck_timer.start()

    def _maybe_fill_waveforms(self):
        """Prepare and continuously revalidate canonical V5 records."""
        if self.lib is None:
            return
        if _running(self._wf_fill_worker):
            # A live rekordbox refresh can replace the library while a pass is
            # running. Its cue fingerprints belong to the old snapshot, so
            # guarantee one fresh scan after it finishes.
            self._waveform_rescan_pending = True
            return
        # This may be the periodic timer itself or an immediate source-refresh
        # request. Either way, the next interval starts only after this pass.
        self._waveform_recheck_timer.stop()
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
        else:
            self._schedule_waveform_recheck()

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
                    pls.append(Playlist(name=val, folder_path=[], keys=list(ids)))
            elif kind == "sbview":
                pl = self._view_playlist(val)
                if pl is not None:
                    pls.append(pl)
        return pls

    @staticmethod
    def _merge_device_playlists(*groups: list[Playlist]) -> list[Playlist]:
        """Stable playlist union; a later snapshot replaces the same identity."""
        order: list[str] = []
        by_key: dict[str, Playlist] = {}
        for group in groups:
            for playlist in group:
                key = device_manifest.playlist_key(playlist)
                if key not in by_key:
                    order.append(key)
                by_key[key] = playlist
        return [by_key[key] for key in order]

    def _queue_dual_ipod_playlists(
            self, dev: str, playlists: list[Playlist]) -> int:
        """Queue already-confirmed dual-iPod jobs without touching the active plan."""
        queued = self._dual_sync_queue.get(dev, [])
        before = {
            device_manifest.playlist_key(playlist) for playlist in queued}
        active = (
            self._dual_active_playlist_keys
            if self._dual_active_device == dev else set()
        )
        additions = [
            playlist for playlist in playlists
            if device_manifest.playlist_key(playlist)
            not in before | active | self._dual_batch_keys
        ]
        self._dual_sync_queue[dev] = self._merge_device_playlists(
            queued, additions)
        if additions:
            for playlist in additions:
                key = device_manifest.playlist_key(playlist)
                self._dual_batch_keys.add(key)
                self._dual_batch_names.append(playlist.display_path)
                self._dual_batch_total_tracks += len(self.lib.resolve(playlist))
            if self._dual_active_preconfirmed:
                self.progress.setRange(
                    0, max(1, self._dual_batch_total_tracks))
                self.progress.setValue(self._dual_batch_done_tracks)
                self.progress.setVisible(True)
                self._set_status_count(
                    self._dual_batch_done_tracks,
                    self._dual_batch_total_tracks,
                )
            total = len(self._dual_sync_queue[dev])
            self._flash_status(
                f"Queued {total} iPod playlist"
                f"{'s' if total != 1 else ''}")
        return len(additions)

    def _confirm_dual_ipod_drop(
            self, dev: str, playlist: Playlist) -> bool:
        tracks = len(self.lib.resolve(playlist))
        answer = QMessageBox.question(
            self,
            "Add playlist to iPod?",
            f"Add “{playlist.display_path}” to {Path(dev).name}?\n\n"
            f"{tracks} track{'s' if tracks != 1 else ''}\n\n"
            "Yes queues this playlist for a guarded stock/Rockbox plan and "
            "sync. Updating an existing playlist also removes tracks that it "
            "no longer references unless another managed playlist still uses "
            "them. Source files remain unchanged.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _start_confirmed_dual_jobs(
            self, dev: str, playlists: list[Playlist]) -> None:
        """Start one confirmed job and retain the rest in FIFO order."""
        jobs = self._merge_device_playlists(playlists)
        if not jobs:
            return
        self._dual_batch_keys = {
            device_manifest.playlist_key(playlist) for playlist in jobs}
        self._dual_batch_names = [playlist.display_path for playlist in jobs]
        self._dual_batch_total_tracks = sum(
            len(self.lib.resolve(playlist)) for playlist in jobs)
        self._dual_batch_done_tracks = 0
        self._dual_batch_results = []
        self._dual_sync_queue[dev] = list(jobs[1:])
        self._begin_dual_ipod_sync(
            dev, [jobs[0]], preconfirmed=True)

    def _on_device_drop(self, dev: str, items: list[tuple[str, str]]):
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
            return
        pls = self._resolve_jobs(items)
        if not pls:
            return
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "stock_ipod":
            self._begin_stock_ipod_sync(dev, pls)
            return
        if profile is not None and profile.kind == "dual_boot":
            confirmed = [
                playlist for playlist in pls
                if self._confirm_dual_ipod_drop(dev, playlist)
            ]
            if not confirmed:
                return
            dual_busy = any(_running(getattr(self, name, None)) for name in (
                "_dual_plan_worker", "_dual_commit_worker"))
            if (
                dual_busy
                or self._dual_active_device
                or self._pending_device_operation is not None
            ):
                self._queue_dual_ipod_playlists(dev, confirmed)
                return
            queued = self._dual_sync_queue.pop(dev, [])
            self._start_confirmed_dual_jobs(
                dev, self._merge_device_playlists(queued, confirmed))
            return
        if profile is not None and not profile.rockbox_export:
            QMessageBox.warning(
                self, "Export unavailable",
                "This volume is not a writable FAT32 Rockbox/generic USB "
                "target. No files were changed.")
            return
        if self._exporting and self.export_worker is not None:
            self.export_worker.add(dev, pls)  # aggregate into the running export
            return
        LOG.info("Export: %d playlist(s) -> %s", len(pls), dev)
        w = ExportWorker(
            self.lib, self.overrides, self.titles, self.art_cache,
            self.rockbox_media_cache, self.rockbox_art_cache)
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
        self._start_pulse("Exporting")  # animated dots from the first frame
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
        self._waveform_rechecks_enabled = False
        for t in (getattr(self, "_dev_timer", None), getattr(self, "_pulse_timer", None),
                  getattr(self, "_warm_timer", None),
                  getattr(self, "_waveform_recheck_timer", None)):
            if t is not None:
                t.stop()
        try:
            self._save_columns()  # final widths (drag-resizes) persist at quit
        except Exception:  # noqa: BLE001 — never block shutdown
            pass
        self._art_restart = False  # don't let a pending re-scan restart during teardown
        self._stock_art_rescan = False
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
        if _running(getattr(self, "_stock_plan_worker", None)):
            self._stock_plan_worker.wait(30000)  # read-only hash/render; let it finish
        if _running(getattr(self, "_stock_commit_worker", None)):
            self._stock_commit_worker.wait(30000)  # never tear down a device commit
        if _running(getattr(self, "_dual_plan_worker", None)):
            self._dual_plan_worker.wait(30000)  # local staging + read-only render
        if _running(getattr(self, "_dual_commit_worker", None)):
            self._dual_commit_worker.wait(30000)  # never tear down composite commit
        if _running(getattr(self, "_stock_delete_plan_worker", None)):
            self._stock_delete_plan_worker.wait(30000)  # finish read-only delete plan
        if _running(getattr(self, "_stock_delete_commit_worker", None)):
            self._stock_delete_commit_worker.wait(30000)  # never tear down deletion
        if _running(getattr(self, "_stock_view_worker", None)):
            self._stock_view_worker.wait(15000)  # read-only DB/device-details scan
        if _running(getattr(self, "_eject_verify_worker", None)):
            self._eject_verify_worker.wait(15000)
        if _running(getattr(self, "_device_import_worker", None)):
            self._device_import_worker.wait(15000)
        if _running(self._art_worker):
            self._art_worker.stop(); self._art_worker.wait(6000)
        if _running(self._stock_art_worker):
            self._stock_art_worker.stop(); self._stock_art_worker.wait(10000)
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
            self._start_pulse("Finalizing")
        else:
            # Animate "EXPORTING…" alongside the % bar (idempotent — the same-word
            # guard keeps the dots from resetting on every tick).
            self._start_pulse("Exporting")

    def _set_status_count(
            self, done: int | None = None, total: int | None = None):
        visible = (
            done is not None and total is not None and int(total) > 0)
        if not visible:
            self.status_count_label.clear()
            self.status_count_label.setVisible(False)
            return
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        self.status_count_label.setText(f"{done}/{total}")
        self.status_count_label.setVisible(True)

    def _start_pulse(self, word: str, *, keep_count: bool = False):
        if not keep_count:
            self._set_status_count()
        if self._pulse_word == word and self._pulse_timer.isActive():
            return  # already animating this word — don't reset the dots
        self._pulse_word = word
        self._pulse_dots = 0
        self.status_label.setFixedWidth(
            self.status_label.fontMetrics().horizontalAdvance(word + "...") + 8)
        self.status_label.setText(word)
        self._pulse_timer.start()

    def _stop_pulse(self):
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()
        self._pulse_word = None
        self._set_status_count()

    def _pulse_tick(self):
        self._pulse_dots = (self._pulse_dots + 1) % 4
        self.status_label.setText((self._pulse_word or "") + "." * self._pulse_dots)

    def _flash_status(self, msg: str, seconds: int = 15):
        """Show a transient status that auto-clears to Ready (if not replaced)."""
        self._set_status_count()
        self.status_label.setFixedWidth(
            self.status_label.fontMetrics().horizontalAdvance(msg) + 8)
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
