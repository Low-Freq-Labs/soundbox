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

from copy import deepcopy

import logging
import json
from math import cos, pi, sin
import os
import random
import sys
import time
from dataclasses import replace
from functools import wraps
from pathlib import Path

from soundbox.state_repository import StateRepository
from soundbox.artwork import ArtCache, ArtResult
from soundbox.device_operations import (
    DeviceCommand, DeviceOperationCoordinator, adapter_result,
    DeviceSessionIdentity, Phase,
)

from PySide6.QtCore import QStandardPaths

LOG = logging.getLogger("soundbox")

_DEVICE_POLL_ACTIVE_MS = 3_000
_DEVICE_POLL_IDLE_MS = 3_000


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

from PySide6.QtCore import (
    QEvent, QFileSystemWatcher, QPointF, QProcess, QRect, QRectF, QSize, Qt, QThread,
    QTimer, QItemSelectionModel,
)
from PySide6.QtGui import (
    QColor, QFontDatabase, QIcon, QPainter, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QLineEdit,
    QFileDialog, QFileSystemModel, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTableView, QTreeView, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from .choices import Menu as QMenu


from soundbox import (
    browser_cache, engine, file_locations, imports, libcache,
    manifest as device_manifest, metadata, paths, rekordbox_db,
    rockbox_config, track_analysis,
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
from .mount_watch import mounted_volume_snapshot
from .artpicker import ArtPickerDialog
from .task_owner import TaskOwner
from .tag_controller import TagEditController
from .prep_tags import MyTagsButton
from .prep_grid import BpmValue, grid_edit_buttons, quantize_button
from .grid_controller import GridEditController
from .wordmark import Wordmark
from .keyboard import KeyboardRouter, Keymap, event_binding
from .usb_device_view import UsbDevicePages
from .model import (
    COLUMNS, TrackModel, row_display_value, row_matches as _row_matches,
)
from .player import PlayerBar

from .device_browser import DeviceBrowserMixin
from .devices import device_facts, device_fs, human
from .settings_page import SettingsPageMixin
from .track_info import TrackInfoMixin
from .stock_device_view import CompositeDeviceData, StockIpodPages
from .trees import (
    ARTIST_IDS_ROLE, ARTIST_ROLE, ARTIST_TRACK_ROLE, ARTISTS_ROLE, COLLECTION_ROLE,
    CUSTOM_ROLE, CUSTOM_FOLDER_ROLE, DEVPATH_ROLE, PATH_ROLE,
    ArtistsTree, CellEditDelegate, DeviceTree, PlaylistTree, SoundBoxTree, TrackTable,
)

from .workers import (
    ArtFillWorker, DeleteWorker, DeviceScanWorker, ExportWorker,
    RefreshWorker, ResyncWorker, StockArtworkPrepWorker, WaveformFillWorker,
    _OneShotArt, _running,
)


# One device transaction owns one 0..1000 meter. Composite planning verifies
# and prepares every source and is often substantial work, so it receives a
# truthful 30% instead of being squeezed into the old 15% startup sliver.
_DUAL_PLAN_PROGRESS_END = 300
_DEVICE_WORK_PROGRESS_END = 800
_DUAL_PLAN_SHARE_OF_PREFINAL = (
    _DUAL_PLAN_PROGRESS_END / _DEVICE_WORK_PROGRESS_END)


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
    "quantize": False,
    "beat_count_mode": 0,
    "theme_accent": theme.DEFAULT_ACCENT,
    "theme_text_mode": "white",
    "theme_text_custom": theme.DEFAULT_ACCENT_TEXT,
    "stock_ipod_models": {},  # stable device identity -> confirmed model
    "stock_ipod_identity": {},  # observed display facts, never write authority
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

class InfoPlaceholderButton(QPushButton):
    """Reserved information control; paint from live theme roles, not an asset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoPlaceholder")
        self.setFixedSize(44, 28)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("Track information")
        self.setToolTip("Track information · Coming soon")
        self.setEnabled(False)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QRectF(self.rect()).center()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TEXT_DIM))
        painter.drawEllipse(center, 8, 8)
        painter.setBrush(QColor(theme.BG))
        # Draw the ink itself around the same center, without font bearings or
        # a baseline shifting the letter relative to its enclosing circle.
        painter.drawEllipse(QRectF(center.x() - 1, center.y() - 5, 2, 2))
        painter.drawRoundedRect(QRectF(center.x() - 1, center.y() - 1.2, 2, 6), .5, .5)


class SettingsButton(QPushButton):
    """A centered 16-point vector gear matching the prep controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GearBtn")
        self.setFixedSize(44, 28)
        self.setAccessibleName("Settings")
        self.setToolTip("Settings")
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QRectF(self.rect()).center()
        color = theme.PURPLE_LIGHT if self.underMouse() and self.isEnabled() else theme.TEXT_DIM
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        points = []
        for tooth in range(8):
            for phase, radius in ((-.5, 6), (-.28, 8), (.28, 8), (.5, 6)):
                angle = (tooth + phase) * pi / 4
                points.append(QPointF(center.x() + radius * cos(angle),
                                     center.y() + radius * sin(angle)))
        painter.drawPolygon(QPolygonF(points))
        painter.setBrush(QColor(theme.BG))
        painter.drawEllipse(center, 2.8, 2.8)


def _cap_browser_scrollbar(view, header):
    """Reserve the header row above the actual vertical scrollbar hit area."""
    cap = QWidget(view)
    cap.setObjectName("BrowserScrollCap")
    cap.setAttribute(Qt.WA_StyledBackground, True)
    cap.setFixedHeight(header.height())
    view.addScrollBarWidget(cap, Qt.AlignTop)


class FooterBar(QWidget):
    """Draw the single full-width divider above the footer."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(0, 0, self.width() - 1, 0)


def _art_ui_action(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (OSError, ValueError) as exc:
            LOG.error("Artwork was not saved: %s", exc)
            self._flash_status("Artwork NOT saved — check space/permissions and retry", 20)
            QMessageBox.warning(self, "Artwork wasn't saved", str(exc))
            return None
    return guarded


def _provider_action(action):
    """Gate direct commands as well as menus and delayed callbacks."""
    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            if (not self.providers.allows(action)
                    or self._shutting_down
                    or getattr(self, "_state_maintenance", False)):
                return None
            return method(self, *args, **kwargs)
        return guarded
    return decorate


from .analysis import AnalysisMixin
from .playlist_folders import PlaylistFoldersMixin
from .cdj import CdjDeviceMixin
from .track_menu import TrackMenuButton, TrackMenuMixin


class MainWindow(PlaylistFoldersMixin, AnalysisMixin, TrackInfoMixin, TrackMenuMixin, CdjDeviceMixin, SettingsPageMixin, DeviceBrowserMixin, QMainWindow):
    def __init__(self, providers=None, *, isolated_dir=None):
        super().__init__()
        self._isolated = isolated_dir is not None
        from soundbox.providers import runtime_providers
        self.providers = providers if providers is not None else runtime_providers()
        self.setWindowTitle("Soundbox")
        self.resize(1240, 800)
        data_dir = (Path(isolated_dir) if isolated_dir is not None else
                    Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)))
        self._data_file = data_dir / "library.json"  # portable SoundBox library
        self._state_repository = StateRepository(self._data_file)
        self._state_maintenance = False
        self._state_publication = False
        self._libcache_path = data_dir / "libcache.json"  # last lib snapshot (instant paint)
        self.art_cache = ArtCache(data_dir / "art")  # cover-art cache
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
        self._waveform_force_pending: set[str] = set()
        self._preparing_library = False
        self._prep_power_policy = "ac"
        self._prep_storage_pause: tuple[int, int] | None = None
        self._prep_progress: tuple[int, int] | None = None
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
        # Stable source-playlist pid -> recognized tracks appended in Rockbox.
        # This is a SoundBox overlay only; rekordbox remains read-only.
        self.playlist_additions: dict[str, list[str]] = {}
        self.aliases: dict[str, str] = {}    # artist merges: variant -> target
        self.settings: dict = dict(_DEFAULT_SETTINGS)
        self._keyboard_router = KeyboardRouter(self._dispatch_keyboard_action)
        self.export_worker: ExportWorker | None = None
        self._backup_worker = None  # OneShotTask for backup zip / restore
        self._default_photo_worker = None  # managed pool image normalization
        self._refresh_worker: RefreshWorker | None = None
        self._refresh_pending = False
        self._rekordbox_source_verified = False
        self._rekordbox_connection_error = ""
        self._libcache_worker = None
        self._libcache_pending = None
        self._import_location_worker = None
        self._shutting_down = False
        self._resync_worker: ResyncWorker | None = None
        self._dev_scan_worker: DeviceScanWorker | None = None
        self._device_mount_refresh_pending = False
        self._volume_directory_stamp = None
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
        self._stock_view_device = ""
        self._stock_view_pending: dict[str, object] = {}
        self._stock_view_callbacks: dict[str, list] = {}
        self._stock_view_cache: dict[
            str, tuple[StockIpodSnapshot, CompositeDeviceData | None]] = {}
        self._stock_view_warming: set[str] = set()
        self._stock_view_failures: dict[str, str] = {}
        self._device_coordinator = DeviceOperationCoordinator()
        self._eject_verify_worker = None
        self._eject_ready_device = ""
        self._device_import_worker = None
        self._device_verify_worker = None
        self._device_finalization_error: Exception | None = None
        self._drift_cache: dict[str, int | None] = {}  # None means not yet checked
        self._device_profiles: dict[str, object] = {}
        self._usb_view_cache = {}
        self._usb_view_requests = set()
        self._usb_device_rows = {}
        self._device_mount_tokens: dict[str, int] = {}
        self._current_device_path = ""
        self._cdj_worker = None
        self._cdj_plan = None
        self._cdj_waiting = None
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
        self._prep_update_message = ''
        self.tag_controller = TagEditController(data_dir, self)
        self.grid_controller = GridEditController(self.tag_controller, self,
                                                  cache_dir=self._track_analysis_dir,
                                                  prepare_cache=self._prepare_grid_cache)
        # Apply saved chrome before building thousands of child widgets. Loading
        # state again later would also lose the repository's recovery receipt.
        self._initial_state = self._state_repository.load()
        self.settings.update(self._initial_state.get('settings') or {})
        self._apply_theme_color(self.settings.get('theme_accent'), save=False)
        self._build_ui()
        self.player_bar.analysis_cache_admitted = lambda tid: not self.grid_controller.pending(tid)
        self.tag_controller.changed.connect(self._sync_tags)
        self.tag_controller.changed.connect(self._sync_cue_editing)
        self.tag_controller.changed.connect(self._sync_rekordbox_connection)
        self.tag_controller.idle.connect(self._finish_tag_refresh)
        self.tag_controller.saved.connect(self._tag_saved)
        self.tag_controller.cue_saved.connect(self._cue_saved)
        self.tag_controller.cue_changed.connect(self._cue_changed)
        self.tag_controller.failed.connect(self._tag_failed)
        self.tag_controller.changed.connect(self._sync_grid)
        self.grid_controller.changed.connect(self._grid_changed)
        self.grid_controller.saved.connect(self._grid_saved)
        self.grid_controller.failed.connect(self._tag_failed)
        self.grid_controller.rejected.connect(lambda text: self._flash_status(text, 10))
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
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(400)
        self._settings_save_timer.timeout.connect(
            lambda: self._save_state(invalidate_views=False))
        self._art_status_timer = QTimer(self)
        self._art_status_timer.setSingleShot(True)
        self._art_status_timer.setInterval(500)
        self._art_status_timer.timeout.connect(self._flush_art_status)
        # A successful source load/import arms a recurring identity scan. It is
        # single-shot and rearmed only after the previous worker has finished,
        # so scans never overlap and a large cook cannot create a timer storm.
        self._waveform_rechecks_enabled = False
        self._waveform_recheck_timer = QTimer(self)
        self._waveform_recheck_timer.setSingleShot(True)
        self._waveform_recheck_timer.setInterval(_WAVEFORM_RECHECK_MS)
        self._waveform_recheck_timer.timeout.connect(self._maybe_fill_waveforms)
        # Devices are recognized on launch and as they're plugged in — no XML needed.
        try:
            self._volume_directory_stamp = os.stat('/Volumes').st_mtime_ns
        except OSError:
            self._volume_directory_stamp = None
        self._refresh_devices()
        self._dev_timer = QTimer(self)
        self._dev_timer.timeout.connect(self._poll_devices)
        self._dev_timer.start(_DEVICE_POLL_IDLE_MS)
        # Directory notifications announce attachment, but mounting over an
        # existing directory can leave /Volumes' timestamp unchanged. Watch the
        # cached kernel mount table too; neither trigger probes mounted files.
        self._volume_refresh_timer = QTimer(self)
        self._volume_refresh_timer.setSingleShot(True)
        self._volume_refresh_timer.setInterval(150)
        self._volume_refresh_timer.timeout.connect(self._on_volume_mount_changed)
        self._volume_watcher = QFileSystemWatcher(self)
        self._mount_snapshot = None
        self._mount_table_timer = QTimer(self)
        self._mount_table_timer.setInterval(250)
        self._mount_table_timer.timeout.connect(self._poll_mount_table)
        if (not QStandardPaths.isTestModeEnabled()
                and Path("/Volumes").is_dir()):
            self._volume_watcher.addPath("/Volumes")
            self._volume_watcher.directoryChanged.connect(
                lambda _path: self._volume_refresh_timer.start())
            self._mount_snapshot = mounted_volume_snapshot()
            self._mount_table_timer.start()
        self._load_state()  # restore custom playlists + last-opened XML
        # Direct imports keep opaque macOS bookmarks. Healthy paths need only a
        # cheap stat; a moved file resolves by persistent identity without a
        # filesystem walk. Legacy records use one strict indexed migration.
        QTimer.singleShot(0, self._repair_import_locations)
        self.table.setFocus()  # open browsing (transport keys, arrows), not typing
        # Startup geometry/style restoration emits thousands of child events.
        # Input routing begins once construction is complete; the native host's
        # own geometry filter remains active throughout initialization.
        QApplication.instance().installEventFilter(self)

    # ---------- persistence ----------
    def _load_state(self):
        if hasattr(self, '_initial_state'):
            data = self._initial_state
            del self._initial_state
        else:
            self._state_repository = StateRepository(self._data_file)
            data = self._state_repository.load()
        self.art_cache.read_only = not self._state_repository.writable
        self._configure_art_sources()
        recovery = self._state_repository.recovery
        if recovery is not None:
            message = (f"Restored {recovery.backup.name}. Recent edits may be missing."
                       if recovery.backup else
                       "No usable backup was found. Saving is disabled until you restore a valid backup.")
            QMessageBox.warning(self, "Library recovery",
                                f"{message}\n\nDamaged state preserved as {recovery.damaged.name}.")
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
        self.playlist_additions = {
            str(pid): [str(track_id) for track_id in values]
            for pid, values in (data.get("playlist_additions") or {}).items()
            if str(pid) and isinstance(values, list)
        }
        self.aliases = data.get("artist_aliases") or {}
        self._last_xml = data.get("last_xml") or ""
        self._rekordbox_source_verified = False
        self._rekordbox_connection_error = ""
        # User settings (extensible — this is the runway for future toggles).
        self.settings = {**_DEFAULT_SETTINGS, **(data.get("settings") or {})}
        self._apply_theme_color(self.settings.get("theme_accent"), save=False)
        self.settings["title_rules_off"] = metadata.normalize_title_rules_off(
            self.settings.get("title_rules_off"))
        self.settings.pop("title_junk", None)  # v1 raw-pattern list — superseded
        self.settings.pop("deck_engine", None)  # retired Qt playback preference
        self._keyboard_router.release_all()
        self._keyboard_router.keymap = Keymap(self.settings.get("keyboard"))
        self._keyboard_page.set_keymap(Keymap(self._keyboard_router.keymap.dump()))
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
        self._set_quantize(self.settings.get('quantize') is True, save=False)
        self._set_beat_count_mode(self.settings.get('beat_count_mode', 0), save=False)
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
        self._sync_rekordbox_connection()
        if self._isolated:
            return
        if rekordbox_db.db_available(self._rekordbox_database_path()):
            self._refresh_rekordbox()
        elif self._last_xml and Path(self._last_xml).is_file():
            self.open_library(self._last_xml)
        self._sync_rekordbox_connection()

    def _save_state(self, *, invalidate_views=True) -> bool:
        """Write the portable SoundBox library (overrides + custom playlists)."""
        timer = getattr(self, "_settings_save_timer", None)
        if timer is not None:
            timer.stop()
        if invalidate_views:
            self._sync_playlists_cache = None
        try:
            if self._state_publication:
                return False
            self._state_repository.save({
                "custom": self.custom,
                "imported": self.imported,
                "artist_overrides": self.overrides,
                "title_overrides": self.titles,
                "hidden_tracks": {i: self.hidden_dates.get(i, "") for i in sorted(self.hidden)},
                "playlist_removals": {k: dict(v) for k, v in self.removals.items() if v},
                "playlist_additions": {
                    k: list(v) for k, v in self.playlist_additions.items() if v
                },
                "artist_aliases": self.aliases,
                "last_xml": self._last_xml,
                "settings": self.settings,
            })
            self.setWindowTitle("Soundbox")
            if self._state_repository.backup_error:
                LOG.warning("Library saved, but rotating backup failed: %s",
                            self._state_repository.backup_error)
                self._flash_status("Library saved; safety backup failed — check space/permissions", 20)
            return True
        except (OSError, ValueError) as exc:
            LOG.error("Could not save library.json: %s", exc)
            self.setWindowTitle("Soundbox — Library not saved")
            self._flash_status("Library NOT saved — check available space/permissions and retry", 20)
            return False

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
        """Stamp each row with cover/avatar/default/blank status for Art."""
        kinds = self.art_cache.status_kinds(r.get('id', '') for r in rows)
        for r in rows:
            r["art"] = kinds[r.get('id', '')]
        return rows

    def _refresh_model_art(self):
        kinds = self.art_cache.status_kinds(r.get('id', '') for r in self.model._all_rows)
        self.model.refresh_art(lambda tid: kinds.get(tid, ''))

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
        result = dict(r)
        detected = self._analysis_result(t)
        if detected:
            if not t.bpm and detected.get('bpm'):
                result['bpm'] = f"{detected['bpm']:.2f}"
            if not t.key and detected.get('key'):
                result['key'] = detected['key']
        result.update(self.settings.get('track_info',{}).get(t.track_id,{}))
        if self.lib and t.source_tag_ids:
            result['myTags'] = ', '.join(tag.name for tag in self.lib.source_tags if tag.tag_id in t.source_tag_ids)
        return result

    def _rows_for(self, tracks) -> list[dict]:
        return [self._row_for(t) for t in tracks]

    def _playlist_rows(self, display_path: str) -> list[dict]:
        """Rows for a rekordbox playlist, in sacred order — via the row cache."""
        pl = next((p for p in self.lib.playlists if p.display_path == display_path),
                  None) if self.lib else None
        return self._rows_for(self.lib.resolve(pl)) if pl is not None else []

    def _invalidate_rows(self, track_ids=None) -> None:
        """Invalidate affected rows, or all rows for a global metadata policy.

        A generation change rejects old worker results; the next warm computes
        only missing rows and preserves unaffected cached values.
        """
        self._browser_cache_key = None  # never checkpoint rows under stale inputs
        if track_ids is None:
            self._row_cache.clear()
        else:
            for track_id in track_ids:
                self._row_cache.pop(track_id, None)
        self._sync_playlists_cache = None
        self._cache_gen += 1
        if self.lib is not None:
            self._warm_timer.start()  # debounced → one rebuild per burst

    def _do_warm(self) -> None:
        """Compute missing rows off-thread, single-flight and generation guarded."""
        if getattr(self, "_state_maintenance", False):
            return
        if self.lib is None or _running(self._warm_worker):
            return
        from .workers import OneShotTask
        gen = self._cache_gen
        lib, ov, ti, junk = self.lib, dict(self.overrides), dict(self.titles), self._title_junk()
        reg = lib.artist_registry
        tracks = [t for t in lib.tracks_by_id.values()
                  if t.track_id not in self._row_cache and t.track_id not in lib.hidden_ids]
        if not tracks:
            return

        def build():
            return {t.track_id: engine.track_row(t, ov, ti, registry=reg, junk=junk)
                    for t in tracks}

        w = OneShotTask(build)
        self._warm_worker = w
        w.done.connect(lambda res, g=gen: self._on_rows_warmed(res, g))
        w.finished.connect(lambda ww=w: self._finish_row_warm(ww))
        w.start()

    def _finish_row_warm(self, worker):
        self._finish_worker("_warm_worker", worker)
        if (not self._shutting_down and not self._state_maintenance and self.lib
                and (self.lib.tracks_by_id.keys() - self.lib.hidden_ids
                     - self._row_cache.keys())):
            self._warm_timer.start()

    def _on_rows_warmed(self, res, gen: int) -> None:
        if isinstance(res, Exception):
            LOG.warning("Row-cache warm failed: %s", res)
            return
        if gen != self._cache_gen:
            self._warm_timer.start()  # inputs changed mid-warm — rebuild
            return
        self._row_cache.update(res)
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
            return
        # A folder may own keyboard/mouse navigation while the browser keeps
        # showing its previous playlist. Refresh that actual active view.
        key = getattr(self, '_view_key', '') or ''
        if key.startswith('rb:'):
            self.model.set_rows(self._annotate_art(self._playlist_rows(key[3:])))
        elif key == 'sb:Collection':self._show_collection()
        elif key == 'sb:Artists':self._show_artists()
        elif key.startswith('sb:') and key[3:] in self.custom:self._show_custom(key[3:])

    def showEvent(self, event):
        super().showEvent(event)
        self._browser_columns_shown = True
        self._fit_browser_columns(fill=not self.settings.get('columns_state'))

    # ---------- player ----------
    def eventFilter(self, obj, ev):
        if (ev.type() in (QEvent.Resize, QEvent.Show)
                and getattr(self, '_browser_columns_shown', False)
                and (obj is self.table.viewport()
                     or obj is self.artists_view.viewport()
                     or obj is self.usb_device_pages.playlist_tree.viewport())
                and (obj.isVisible() or ev.type() == QEvent.Show)):
            # Hidden pages can retain their old geometry until Show. Refit
            # before the browser is painted after returning from Settings.
            width = ev.size().width() if ev.type() == QEvent.Resize else obj.width()
            self._fit_browser_columns(fill=True, available=width)
        if (getattr(self, "now_playing", None) is obj
                and ev.type() == QEvent.MouseButtonRelease
                and ev.button() == Qt.LeftButton):
            self._focus_now_playing()
            return True

        # Configurable shortcuts are deck controls only from browsing surfaces. Editors,
        # search, settings trees, dialogs, and the device browser keep their
        # native text/toggle behavior. Text editing retains its standard chords.
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
            self.grid_controller.set_current(self.player_bar.current_track(), force=True)
            paths=[path for path,profile in getattr(self,'_device_profiles',{}).items()
                   if getattr(profile,'kind','')=='generic_usb']
            if paths:
                self._usb_view_requests.update(paths)
                QTimer.singleShot(0,self._refresh_devices)
            return super().eventFilter(obj, ev)
        if ev.type() == QEvent.ApplicationDeactivate:
            self._keyboard_router.release_all()
            return super().eventFilter(obj, ev)
        if ev.type() not in (
                QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            return super().eventFilter(obj, ev)
        focus = QApplication.focusWidget()
        browsing = self._space_should_play(focus)
        if focus is self.usb_device_pages.playlist_tree:
            action = self._keyboard_router.keymap.action(event_binding(ev)) or ''
            browsing = (action.startswith(('transport.', 'cues.', 'grid.', 'wave.', 'autoplay.'))
                        or action in ('browser.load', 'browser.search', 'app.settings'))
        if (self._keyboard_router.keymap.action(event_binding(ev)) == "app.settings"
                and not isinstance(focus, QLineEdit)):
            browsing = True
        browsing = (browsing and QApplication.activeModalWidget() is None
                    and QApplication.activePopupWidget() is None
                    and not self._shutting_down and not self._state_maintenance)
        if self._keyboard_router.handle(ev, browsing):
            return True
        return super().eventFilter(obj, ev)

    def _save_keyboard(self, state) -> bool:
        previous = self.settings.get("keyboard")
        self.settings["keyboard"] = state
        if not self._save_state():
            if previous is None:
                self.settings.pop("keyboard", None)
            else:
                self.settings["keyboard"] = previous
            return False
        self._keyboard_router.release_all()
        self._keyboard_router.keymap = Keymap(state)
        return True

    def _dispatch_keyboard_action(self, action):
        pb = self.player_bar
        if action == "transport.cue":
            self._cue_key_down = True
            pb._cue_press()
            def release():
                self._cue_key_down = False
                pb._cue_release()
            return release
        if action.startswith("cues.slot."):
            panel = pb.cue_panel
            slot = int(action.rsplit(".", 1)[1])
            kind = "hot" if panel.pages.currentIndex() == 0 else "memory"
            if panel._loaded and panel._cues[kind][slot] is not None:
                for _held_action, held_release in self._keyboard_router.held.values():
                    if getattr(held_release, "cue_control", None) == (kind, slot):
                        return held_release
                button = (panel.hot_slots if kind == "hot" else panel.memory_slots)[slot]
                button.setDown(True)
                panel._press(kind, slot)
                def release_marker():
                    button.setDown(False)
                    panel._release(kind, slot)
                release_marker.cue_control = (kind, slot)
                return release_marker
            if panel._loaded and self._edit_cue(kind, slot, 'add'):
                # Keep this physical press owned until release. A save that
                # finishes while held must not turn creation into preview.
                def release_created():
                    pass
                release_created.cue_control = (kind, slot)
                return release_created
            return None
        if action.startswith('cues.delete.'):
            kind = 'hot' if pb.cue_panel.pages.currentIndex() == 0 else 'memory'
            self._edit_cue(kind, int(action.rsplit('.', 1)[1]), 'delete')
            return None
        commands = {
            "transport.play": self._toggle_player,
            "transport.previous": lambda: pb.previous_btn.click(),
            "transport.next": lambda: pb.next_btn.click(),
            "transport.back": lambda: pb.beat_back_btn.click(),
            "transport.forward": lambda: pb.beat_forward_btn.click(),
            "grid.downbeat": lambda: self._edit_grid('downbeat'),
            "grid.left": lambda: self._edit_grid('left'),
            "grid.right": lambda: self._edit_grid('right'),
            "grid.double": lambda: self._edit_grid('double'),
            "grid.halve": lambda: self._edit_grid('halve'),
            "grid.quantize": lambda: self.quantize_btn.click(),
            "cues.hot_page": lambda: pb.cue_panel.hot_tab.click(),
            "cues.memory_page": lambda: pb.cue_panel.memory_tab.click(),
            "cues.toggle_page": pb.cue_panel.toggle_page,
            "browser.rename": self._rename_current,
            "browser.load": self._load_browser_selection,
            "browser.search": lambda: self.search.setFocus(),
            "browser.playing": self._focus_now_playing,
            "library.refresh": self._refresh_rekordbox,
            "library.playlist": self._new_custom,
            "library.import": lambda: self._import_dialog(None),
            "app.settings": self._open_settings,
            "wave.zoom_in": lambda: pb.wave._step_zoom(1),
            "wave.zoom_out": lambda: pb.wave._step_zoom(-1),
            "autoplay.off": lambda: self._set_autoplay_mode(""),
        }
        if action.startswith("wave.") and action.split(".")[1] in ("BLUE", "RGB", "3BAND"):
            self._on_waveform_mode_changed(action.split(".")[1])
        elif action.startswith("autoplay.") and action != "autoplay.off":
            self._toggle_autoplay_mode(action.split(".")[1])
        elif action in commands:
            commands[action]()
        return None

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
        """Double-click a row → load that track, preserving playback intent."""
        if not index.isValid() or index.row() >= len(self.model._rows):
            return
        tid = self.model._rows[index.row()].get("id")
        t = self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None
        if t is not None:
            self._load_track(t)

    def _on_playback_error(self, msg: str):
        LOG.warning("Playback error: %s", msg)
        status = ("Cloud file unavailable — check cloud storage"
                  if msg.startswith("Cloud storage cannot provide ")
                  else "Playback error — hover for details")
        self._flash_status(status, 15)
        if self.status_label.text() == status:
            self.status_label.setToolTip(msg)

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
        """Load the adjacent visible track, preserving ordinary playback intent."""
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
            self._load_track(track)

    def _load_track(self, t):
        """Load at the first grid beat, preserving ordinary playback intent."""
        self._open_track(t, autoplay=None, capture_autoplay=True)

    def _play_track(self, t):
        """Load a track and start it at its first grid beat."""
        self._open_track(t, autoplay=True, capture_autoplay=True)

    def _open_track(
            self, t, autoplay: bool | None,
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
            detected_analysis=self._analysis_result(t),
            cues=(self.tag_controller.session_cues(t.track_id)
                  if self.tag_controller.lib is self.lib else getattr(t, "source_cues", None)))
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
        if (getattr(self, "_review_audition_active", False)
                or not self._autoplay_mode
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
            self._invalidate_rows((track_id,))  # title override changed this row's computed title
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
        upper = QGridLayout()
        upper.setContentsMargins(0, 0, 4, 0)
        upper.setHorizontalSpacing(0)
        upper.setVerticalSpacing(3)
        upper.setColumnMinimumWidth(1, 12)
        upper.setColumnMinimumWidth(3, 6)
        upper.setColumnStretch(2, 1)
        upper.setRowStretch(1, 1)
        h = QHBoxLayout(); h.setSpacing(10)
        h.setContentsMargins(14, 0, 4, 0)
        self.wordmark = Wordmark()
        h.addWidget(self.wordmark, 0, Qt.AlignVCenter)
        h.addSpacing(4)  # With layout spacing, match the logo's 14px left inset.
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
        # Keep the title inside its header lane as metadata grows.
        self.now_playing.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.now_playing.setCursor(Qt.PointingHandCursor)
        self.now_playing.setToolTip("")
        now_layout.addWidget(self.now_playing, 1)
        h.addWidget(self.now_playing_frame, 1, Qt.AlignVCenter)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search title, artist, genre…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(28)
        # Click-to-focus only: as the first focusable widget it grabbed keyboard
        # focus at launch, so the app opened "typing into search" instead of
        # ready to browse (transport keys, arrows). Startup focus goes to the table.
        self.search.setFocusPolicy(Qt.ClickFocus)
        self.search.textChanged.connect(self._on_search)
        gear = SettingsButton()
        self.settings_btn = gear
        gear.clicked.connect(self._open_settings)
        upper.addLayout(h, 0, 0, 1, 3)

        # Title and waveform share a right edge. Cues span both lanes, using
        # the space freed by moving Search and Settings into the prep strip.
        self.player_bar = PlayerBar(
            jump_mode=str(self.settings.get("beat_jump_mode", "16 BEATS")),
            waveform_zoom=float(self.settings.get("waveform_zoom", 92.0)),
            waveform_mode=str(
                self.settings.get("waveform_mode", "3BAND")),
            embedded_cues=False, embedded_transport=False)
        self.player_bar.track_analysis_dir = self._track_analysis_dir
        self.player_bar.playback_error.connect(self._on_playback_error)
        self.player_bar.now_playing_changed.connect(self._set_now_playing)
        self.player_bar.now_playing_changed.connect(lambda *_: self._sync_prep_bpm())
        self.player_bar.now_playing_changed.connect(lambda *_: self._sync_cue_editing())
        self.player_bar.now_playing_changed.connect(
            lambda *_: self.grid_controller.set_current(self.player_bar.current_track()))
        self.player_bar.track_ready.connect(lambda: self._grid_changed(self.player_bar.current_track()))
        self.player_bar.cue_panel.empty_pressed.connect(
            lambda kind, slot: self._edit_cue(kind, slot, 'add'))
        self.player_bar.playback_active_changed.connect(self._on_playback_active)
        self.player_bar.source_changed.connect(self._on_player_source_changed)
        self.player_bar.jump_mode_changed.connect(self._on_jump_mode_changed)
        self.player_bar.waveform_zoom_changed.connect(
            self._on_waveform_zoom_changed)
        self.player_bar.previous_requested.connect(lambda: self._play_relative(-1))
        self.player_bar.next_requested.connect(lambda: self._play_relative(1))
        self.player_bar.track_ended.connect(self._advance_autoplay)
        self.player_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        upper.addWidget(self.player_bar, 1, 2)
        upper.addWidget(self.player_bar.transport_deck, 1, 0, 2, 1)
        cue_column = QWidget()
        cue_column.setObjectName('CueColumn')
        cue_column.setFixedWidth(self.player_bar.cue_panel.width())
        cue_layout = QVBoxLayout(cue_column)
        cue_layout.setContentsMargins(0, 5, 0, 4)
        cue_layout.setSpacing(0)
        cue_layout.addWidget(self.player_bar.cue_panel)
        upper.addWidget(cue_column, 0, 4, 2, 1)
        outer.addLayout(upper, 1)
        prep = QWidget()
        prep.setObjectName("PrepToolbar")
        prep.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(prep)
        # This grid column shares the waveform origin and the cue column's right edge.
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(6)
        self.grid_edit_buttons = grid_edit_buttons(prep)
        for button, operation in zip(self.grid_edit_buttons,
                                     ('downbeat', 'left', 'right', 'double', 'halve')):
            button.clicked.connect(lambda _checked=False, op=operation: self._edit_grid(op))
        self.grid_edit_buttons[0].pressed.connect(
            lambda: LOG.info('Grid bar-start button pressed: track=%s', self.player_bar.current_track()))
        self.bpm_value = BpmValue(prep)
        self.bpm_value.requested.connect(
            lambda tid, value: self._edit_grid('bpm', value, track_id=tid))
        self.bpm_value.invalid.connect(lambda message: self._flash_status(message, 8))
        for index, button in enumerate(self.grid_edit_buttons):
            row.addWidget(button)
            row.addStretch(1)
            if index == 0:
                row.addWidget(self.bpm_value)
                row.addStretch(1)
        self.quantize_btn = quantize_button(prep)
        self.quantize_btn.toggled.connect(self._set_quantize)
        row.addWidget(self.quantize_btn)
        row.addStretch(1)
        self.track_menu_btn = TrackMenuButton(prep)
        self.track_menu_btn.clicked.connect(self._show_track_menu)
        row.addWidget(self.track_menu_btn)
        row.addStretch(1)
        self.playback_time = QLabel(self.player_bar.playback_time_text)
        self.playback_time.setObjectName("PlaybackTime")
        self.playback_time.setAccessibleName("Playback time")
        clock_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        clock_font.setPixelSize(13)
        self.playback_time.setFont(clock_font)
        self.playback_time.setAlignment(Qt.AlignCenter)
        self.playback_time.setFixedHeight(28)
        self.playback_time.setMinimumWidth(
            self.playback_time.fontMetrics().horizontalAdvance("00:00:00/00:00:00") + 12)
        self.player_bar.playback_time_changed.connect(self.playback_time.setText)
        row.addWidget(self.playback_time)
        row.addStretch(1)
        self.info_placeholder = InfoPlaceholderButton(prep)
        self.info_placeholder.setEnabled(True)
        self.info_placeholder.setToolTip('Track information')
        self.info_placeholder.clicked.connect(self._open_track_info)
        row.addWidget(self.info_placeholder)
        row.addStretch(1)
        self.my_tags = MyTagsButton()
        self.my_tags.setFixedHeight(28)
        self.my_tags.setFixedWidth(self.player_bar.cue_panel.width() - 6)
        self.search.setFixedWidth(self.my_tags.width())
        self.my_tags.popup.aboutToShow.connect(self._probe_tags)
        self.my_tags.assignment_requested.connect(self._assign_tag)
        row.addWidget(self.my_tags)
        row.addStretch(1)
        row.addWidget(gear)
        row.addSpacing(10)  # Gear ends at the waveform edge; Search starts at the cue slots.
        row.addWidget(self.search)
        upper.addWidget(prep, 2, 2, 1, 3)
        return bar

    def _sync_prep_bpm(self):
        track_id = self.player_bar.current_track()
        track = self.lib.tracks_by_id.get(track_id) if self.lib else None
        value = track.bpm if track is not None else self.player_bar._bpm
        if track is not None and not value:
            value = (self._analysis_result(track) or {}).get('bpm') or 0
        view = self.grid_controller.view(track_id) if track_id else None
        if view is not None:
            value = view[1]
        self.bpm_value.set_bpm(value if track_id else 0, track_id=track_id or '')

    def _set_quantize(self, enabled, *, save=True):
        enabled = enabled is True
        self.player_bar.quantize_enabled = enabled
        blocked = self.quantize_btn.blockSignals(True)
        try:
            self.quantize_btn.setChecked(enabled)
        finally:
            self.quantize_btn.blockSignals(blocked)
        self.settings['quantize'] = enabled
        if save:
            self._schedule_settings_save()

    def _sync_grid(self):
        if not hasattr(self, 'grid_edit_buttons'):
            return
        pb = self.player_bar
        reason = self.grid_controller.reason(pb.current_track(), allow_checking=True)
        if not reason and (self._refresh_worker is not None or self._state_maintenance
                           or self._device_operation_owned() or self._shutting_down):
            reason = 'Library operation in progress'
        if not reason and (pb._pending_autoplay or pb._native_error or not pb.cue_panel._loaded):
            reason = 'Wait for the track to finish loading'
        if not reason and (pb._analysis_parts.get('provenance', {}).get('grid') == 'soundbox-manual'
                           or pb._analysis_parts.get('provenance', {}).get('user_data') == 'soundbox'):
            reason = 'This track has a SoundBox manual grid'
        step = pb.grid_nudge_ms()
        for index, button in enumerate(self.grid_edit_buttons):
            button.setEnabled(not reason)
            detail = f'\n{step} ms · Shift: {step * 3} ms' if index in (1, 2) else ''
            button.setToolTip(button.accessibleName() + detail + ('\n' + reason if reason else ''))
        bpm_reason = reason or self.grid_controller.bpm_reason(pb.current_track())
        self.bpm_value.set_editable(not bpm_reason, bpm_reason)
        self._sync_prep_bpm()

    def _grid_changed(self, track_id):
        pb = self.player_bar
        track = self.tag_controller.lib.tracks_by_id.get(track_id) if self.tag_controller.lib else None
        view = self.grid_controller.view(track_id)
        provenance = pb._analysis_parts.get('provenance', {})
        manual = provenance.get('grid') == 'soundbox-manual' or provenance.get('user_data') == 'soundbox'
        if track is not None and view is not None and not manual:
            pb.refresh_live_grid(track_id, track.path, view[0], view[1])
        self._sync_grid()
        self._sync_edit_status()

    def _edit_grid(self, operation, value=0, *, track_id=None):
        self._sync_grid()
        pb = self.player_bar
        if (self._refresh_worker is not None or self._state_maintenance or self._shutting_down
                or self._device_operation_owned() or pb._pending_autoplay or pb._native_error
                or not pb.cue_panel._loaded
                or pb._analysis_parts.get('provenance', {}).get('grid') == 'soundbox-manual'
                or pb._analysis_parts.get('provenance', {}).get('user_data') == 'soundbox'):
            return False
        tid = pb.current_track()
        if track_id is not None and tid != track_id:
            return False
        track = self.tag_controller.lib.tracks_by_id.get(tid) if self.tag_controller.lib else None
        if track is None or str(track.path) != pb._src_path:
            return False
        reason = self.grid_controller.reason(tid, allow_checking=True)
        if reason:
            self._flash_status(reason, 10)
            return False
        if operation == 'downbeat':
            value = pb.capture_grid_position()
            if value is None:
                self._flash_status('Wait for the track to finish loading before setting a bar start', 8)
                return False
            LOG.info('Grid bar start requested: track=%s target_ms=%s visible_ms=%s backend_ms=%s',
                     tid, value, pb.wave._position_ms, pb.player.position())
        elif operation in ('left', 'right'):
            step = pb.grid_nudge_ms()
            if QApplication.keyboardModifiers() & Qt.ShiftModifier:
                step *= 3
            value, operation = (-step if operation == 'left' else step), 'shift'
        return self.grid_controller.request(tid, operation, value,
                                            preview_rows=pb._analysis_parts.get('beats'))

    def _prepare_grid_cache(self, track_id):
        # Source worker only: fence old preparation publications without
        # stopping playback, deleting a cache or waiting in the GUI thread.
        workers = list(self.player_bar._wf_pending)
        bulk = self._wf_fill_worker
        if bulk is not None:
            workers.append(bulk)
        for worker in workers:
            worker.cache_writes.protect(track_id)

    def _grid_saved(self, lib, track_id):
        self.lib = lib
        track = lib.tracks_by_id.get(track_id)
        if track is not None:
            self._invalidate_rows([track_id])
            selected = set(self._selected_ids())
            reset = self.model.update_bpm(track_id, track.bpm)
            if reset and selected:
                selection = self.table.selectionModel()
                for row, data in enumerate(self.model._rows):
                    if data.get('id') in selected:
                        selection.select(self.model.index(row, 0),
                                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if not self.tag_controller.owned:
            self._cache_library()
            self._finish_tag_refresh()
        self._finish_edit_status()

    def _sync_tags(self):
        if not hasattr(self, "my_tags"):
            return
        controller = self.tag_controller
        ids = self._selected_ids() if hasattr(self, "table") and self.stack.currentIndex() == 0 else []
        reason = controller.reason(ids)
        if not reason and (self._refresh_worker is not None or self._state_maintenance
                           or self._device_operation_owned()):
            reason = "Library operation in progress"
        lib = controller.lib
        assigned = controller.selected_tags(ids)
        self.my_tags.set_state(lib.source_tags if lib else [], assigned, reason)
        self._sync_edit_status()

    def _sync_edit_status(self):
        if not hasattr(self, 'status_label'):
            return
        message = self.tag_controller.update_status_text
        if message and (message != self._prep_update_message
                        or self.status_label.text() != message):
            self._prep_update_message = message
            self._set_status_text(message)

    def _finish_edit_status(self):
        """Only verified save callbacks can report completion, never probes."""
        if self.tag_controller.update_status_text:
            self._sync_edit_status()
        elif self._prep_update_message:
            self._prep_update_message = ''
            self._flash_status('Done', 2)

    def _sync_cue_editing(self):
        pb = self.player_bar
        reason = self.tag_controller.cue_reason(pb.current_track())
        if not reason and (self._refresh_worker is not None or self._state_maintenance
                           or self._device_operation_owned()):
            reason = 'Library operation in progress'
        pb.cue_panel.set_editable(not reason, reason)

    def _edit_cue(self, kind, slot, operation):
        pb = self.player_bar
        if (self._refresh_worker is not None or self._state_maintenance or self._shutting_down
                or self._device_operation_owned()):
            return False
        track_id = pb.current_track()
        track = self.lib.tracks_by_id.get(track_id) if self.lib else None
        if not track or str(track.path) != pb._src_path:
            return False
        if operation == 'delete' and pb.cue_panel._cues[kind][slot] is None:
            return False
        reason = self.tag_controller.cue_reason(track_id)
        if reason:
            self._flash_status(reason, 10)
            return False
        position = pb.capture_cue_position() if operation == 'add' else None
        if operation == 'add' and (position is None or track_id != pb.current_track()):
            self._flash_status('Wait for the track to finish loading before setting a cue', 8)
            return False
        if operation == 'add':
            position = pb.quantize_cue_position(position)
        return self.tag_controller.request_cue(track_id, kind, slot, operation, position)

    def _assign_tag(self, tag_id, present):
        if (self._refresh_worker is not None or self._state_maintenance
                or self._shutting_down or self._device_operation_owned()):
            self._sync_tags()
            return
        self.tag_controller.request(self._selected_ids(), tag_id, present)
        self._sync_rekordbox_connection()
        self._sync_tags()

    def _probe_tags(self):
        if (self._refresh_worker is not None or self._state_maintenance
                or self._shutting_down or self._device_operation_owned()):
            self._sync_tags()
            return
        self.tag_controller.probe()
        self._sync_tags()

    def _tag_saved(self, lib):
        # The worker includes a fresh authoritative snapshot; never reconstruct
        # smart memberships by patching a stale browser cache.
        selected = set(self._selected_ids()) if self.stack.currentIndex() == 0 else set()
        self._on_refresh_done(lib)
        if selected and self.stack.currentIndex() == 0:
            selection = self.table.selectionModel()
            selection.clearSelection()
            for row, data in enumerate(self.model._rows):
                if data.get("id") in selected:
                    selection.select(self.model.index(row, 0),
                                     QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self._finish_tag_refresh()
        self._finish_edit_status()

    def _cue_saved(self, lib, track_id):
        self.lib = lib
        if self.tag_controller.owned:
            self._finish_edit_status()
            return
        self._cache_library()
        self._finish_tag_refresh()
        self._finish_edit_status()

    def _cue_changed(self, track_id):
        controller = self.tag_controller
        track = controller.lib.tracks_by_id.get(track_id) if controller.lib else None
        if track is not None:
            self.player_bar.refresh_live_cues(track_id, track.path, controller.session_cues(track_id))
        self._sync_edit_status()

    def _tag_failed(self, error):
        label = (self._prep_update_message.removeprefix('Updating ').removesuffix('…')
                 or self.tag_controller.last_operation)
        self._prep_update_message = ''
        self._flash_status(f'Couldn’t update {label}: ' + error, 20)
        self._finish_tag_refresh()
        # Reconcile stale selection values after a conflict or an uncertain
        # receipt. The next write still requires the backend's recovery proof.
        if self.lib is not None and self.lib.source_kind == "rekordbox-db":
            self._start_rekordbox_refresh(Path(self.lib.source))

    def _finish_tag_refresh(self):
        self._sync_rekordbox_connection()
        self._sync_tags()
        self._sync_cue_editing()
        self._sync_grid()
        if self._refresh_pending and not self._shutting_down:
            self._refresh_pending = False
            QTimer.singleShot(0, self._refresh_rekordbox)

    def _set_now_playing(self, text: str):
        """Show artist/title in the open header lane, never over the waveform."""
        text = text or "SELECT A TRACK"
        available = max(80, self.now_playing.width() - 28)
        self.now_playing.setText(self.now_playing.fontMetrics().elidedText(
            text, Qt.ElideRight, available))
        self.now_playing.setToolTip("")

    def _schedule_settings_save(self):
        """Coalesce preferences; curation/backup/shutdown still flush immediately."""
        if not self._shutting_down and not self._state_maintenance:
            self._settings_save_timer.start()

    def _on_jump_mode_changed(self, mode: str):
        self.settings["beat_jump_mode"] = mode
        self._schedule_settings_save()

    def _on_waveform_zoom_changed(self, pixels_per_second: float):
        self.settings["waveform_zoom"] = pixels_per_second
        self._sync_grid()
        self._schedule_settings_save()

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
            self._schedule_settings_save()

    def _on_playback_active(self, active: bool):
        """Reserve CPU/UI time for the deck while audio is running."""
        for worker in (
                self._wf_fill_worker, self._art_worker, self._stock_art_worker):
            if worker is not None:
                worker.set_playback_active(active)
        # Cue gates can cross this edge several times a second. Library-wide
        # artwork reads and device scans do not belong on a transport edge.
        # Artwork completion/progress owns its presentation; mount notices and
        # the independent device timers own discovery, during playback too.

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
        self.sb_tree.node_moved.connect(self._move_local_node)
        self.sb_tree.itemExpanded.connect(lambda item:self._local_folder_expanded(item, True))
        self.sb_tree.itemCollapsed.connect(lambda item:self._local_folder_expanded(item, False))
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
                                                   self._new_soundbox_item_menu, top=True)),
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
        self.table.setObjectName("TrackTable")
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
        self.table.doubleClicked.connect(self._play_row)  # double-click preserves playback
        # Rename and transport shortcuts share the configurable keyboard router.
        self.table.setAlternatingRowColors(True)
        # Per-ITEM scrolling keeps table rows phase-locked to the sidebar's 24px
        # grid at every scroll position (top, bottom, anywhere) — pixel scrolling
        # let the two panes drift out of alignment.
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        # Keep the scrollbar beside the rows, below the capped header gutter.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        hh.setFixedHeight(26)  # match the sidebar section-header height so they line up
        _cap_browser_scrollbar(self.table, hh)
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
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._sync_tags())
        self.stack.currentChanged.connect(lambda *_: self._sync_tags())
        hh.sortIndicatorChanged.connect(
            lambda *_: QTimer.singleShot(0, self._reseat_selection))
        pl.addWidget(self.table)
        self.stack.addWidget(page)

        # page 1: device browser
        dpage = QWidget(); dl = QVBoxLayout(dpage)
        dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        dhead = QWidget(); dhead.setObjectName("DeviceHeader")
        dhead.setAttribute(Qt.WA_StyledBackground, True); dhead.setFixedHeight(26)
        dhh = QHBoxLayout(dhead); dhh.setContentsMargins(10, 0, 8, 0)
        self.dev_header = QLabel("Device"); self.dev_header.setObjectName("DeviceTitle")
        self.dev_header.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        dhh.addWidget(self.dev_header, 1)
        self.device_tab_group = QButtonGroup(self)
        self.device_tab_group.setExclusive(True)
        self.device_tab_buttons = []
        from .usb_preferences import USB_TABS
        for index, label in enumerate(USB_TABS):
            button = QPushButton(label)
            button.setObjectName("DeviceTab")
            button.setCheckable(True)
            button.setFixedHeight(22)
            button.setVisible(False)
            button.toggled.connect(
                lambda checked, i=index: (
                    self._set_device_tab(i) if checked else None))
            self.device_tab_group.addButton(button, index)
            self.device_tab_buttons.append(button)
            dhh.addWidget(button)
        self.device_sync_btn = QPushButton("Sync")
        self.device_sync_btn.setObjectName("DeviceAction")
        self.device_sync_btn.setFixedHeight(22)
        self.device_sync_btn.clicked.connect(
            lambda: self._sync_device(self._current_device_path))
        dhh.addWidget(self.device_sync_btn)
        self.eject_btn = QPushButton("⏏ Eject")
        self.eject_btn.setObjectName("DeviceAction")
        self.eject_btn.setFixedHeight(22)
        self.eject_btn.clicked.connect(self._eject_current)
        self.eject_btn.setEnabled(False)
        dhh.addWidget(self.eject_btn)
        dl.addWidget(dhead)
        self.fs_model = QFileSystemModel()
        # Browser mutations use pinned DeviceSession operations in the context
        # menu. QFileSystemModel's direct rename path cannot revalidate mounts.
        self.fs_model.setReadOnly(True)
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
        self.stock_ipod_pages.stack.currentChanged.connect(self._remember_stock_device_view)
        self.stock_ipod_pages.delete_playlists_requested.connect(
            self._begin_stock_playlist_delete)
        self.stock_ipod_pages.playlist_order_requested.connect(
            self._apply_composite_playlist_order)
        self.device_content_stack.addWidget(self.stock_ipod_pages)
        self.usb_device_pages = UsbDevicePages()
        self.usb_device_pages.settings_requested.connect(self._apply_usb_settings)
        self.usb_device_pages.global_requested.connect(self._promote_usb_settings)
        self.usb_device_pages.global_load_requested.connect(self._load_global_usb_settings)
        self.usb_device_pages.song_requested.connect(self._load_usb_song)
        saved_views = self.settings.get('device_views',{})
        self.usb_device_pages._states = deepcopy(saved_views.get('usb',{})) if isinstance(saved_views,dict) else {}
        self.usb_device_pages.view_changed.connect(self._save_device_views)
        self.usb_device_pages.row_enricher = self._enrich_usb_row
        self.usb_device_pages.playlist_tree.header().sectionResized.connect(self._on_usb_column_resized)
        _cap_browser_scrollbar(self.usb_device_pages.playlist_tree,self.usb_device_pages.playlist_tree.header())
        self.device_content_stack.addWidget(self.usb_device_pages)
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
        self.artists_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.artists_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ah = self.artists_view.header()
        ah.setSectionResizeMode(QHeaderView.Interactive)
        ah.setStretchLastSection(False)
        ah.setMinimumSectionSize(46)
        # Every column, including Artist/Track, mirrors the shared saved widths.
        ah.setFixedHeight(26)
        _cap_browser_scrollbar(self.artists_view, ah)
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
            self._on_browser_column_resized)
        ah.sectionResized.connect(self._on_artist_column_resized)
        return self.stack

    def _bottom_bar(self) -> QWidget:
        bar = FooterBar(); bar.setObjectName("BottomBar")
        bar.setFixedHeight(28)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        # A touch more left padding so status text (EXPORTING/DELETING/…) isn't jammed
        # against the window edge.
        h = QHBoxLayout(bar); h.setContentsMargins(20, 4, 12, 4); h.setSpacing(8)
        self.status_label = QLabel("Ready")
        # Reserve only the current operation's animated dots.  A single global
        # width for every possible flash message left a huge dead gap before
        # the iPod count and progress bar.
        fm = self.status_label.fontMetrics()
        self.status_label.setFixedWidth(fm.horizontalAdvance("Ready") + 8)
        h.addWidget(self.status_label)
        self.status_count_label = QLabel("")
        self.status_count_label.setAlignment(Qt.AlignCenter)
        # Reserve the full track-count width for the operation. Per-file byte
        # details live in the status tooltip, so copy/verify bursts cannot
        # replace the count or move the progress bar.
        self.status_count_label.setMinimumWidth(
            fm.horizontalAdvance("9999/9999") + 16)
        self.status_count_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.status_count_label.setMargin(0)
        self.status_count_label.setFixedHeight(20)
        self.status_count_label.setVisible(False)
        h.addWidget(self.status_count_label)
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        self.progress.setFixedWidth(208)
        self.progress.setFixedHeight(18)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setVisible(False)
        h.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName('FooterCancel')
        self.cancel_btn.setFixedHeight(20)
        self.cancel_btn.clicked.connect(self._cancel_export)
        self.cancel_btn.setVisible(False)
        h.addWidget(self.cancel_btn)
        h.addStretch(1)
        return bar

    # ---------- open / load ----------
    def _open_dialog(self):
        if self._refresh_worker is not None or self._state_maintenance or self._device_operation_owned():
            return
        start = str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(self, "Open Rekordbox XML", start,
                                              "rekordbox XML (*.xml)")
        if path:
            self.open_library(path)

    def open_library(self, path: str):
        try:
            self.lib = engine.load_library(path)
        except Exception as exc:  # parser failures also retain the previous library
            LOG.exception("Load failed: %s", path)
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        LOG.info("Loaded %s: %d tracks, %d playlists",
                 Path(path).name, self.lib.track_count, len(self.lib.playlists))
        self._last_xml = path  # remember for next launch
        self._rekordbox_source_verified = True
        self._rekordbox_connection_error = ""
        self._save_state()
        self._apply_loaded_library()
        self._sync_rekordbox_connection()
        self._refresh_devices()
        self.stack.setCurrentIndex(0)
        self._cache_library()  # snapshot for the next instant paint
        self._arm_waveform_rechecks()
        if self.providers.allows("artwork.search"):
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
        self.tag_controller.configure(self.lib, self._rekordbox_source_verified)
        if not self.grid_controller.pending(self.player_bar.current_track()):
            self.player_bar.clear_live_grid()
        self.grid_controller.configure()
        self._landed_cache.clear()
        self._sync_playlists_cache = None
        sb_cur = self.sb_tree.currentItem()  # selection BEFORE the rebuild
        repaired = imports.inject(self.lib, self.imported)  # livesets rejoin the pool
        if repaired:
            LOG.info("Repaired duration/size metadata for %d imported track(s)",
                     repaired)
            self._save_state()
        loaded = self.lib.tracks_by_id.get(self.player_bar.current_track())
        if loaded is not None:
            self.player_bar.refresh_live_cues(
                loaded.track_id, loaded.path, getattr(loaded, "source_cues", None))
        self._sync_prep_bpm()
        # Presentation reuse is independent of source authority. Cue/tag edit
        # identities are refreshed above even when every browser input matches.
        display_inputs = (
            frozenset(tuple(getattr(t, f) for f in (
                'track_id', 'name', 'artist', 'location_uri', 'bpm', 'key',
                'total_time', 'date_added', 'added_ts', 'genre', 'kind',
                'size', 'bitrate', 'rating', 'year', 'imported')) + (json.dumps(t.info,sort_keys=True),)
                for t in self.lib.tracks_by_id.values()),
            tuple(sorted(self.overrides.items())), tuple(sorted(self.titles.items())),
            tuple(sorted(self.aliases.items())), tuple(self._title_junk()),
        )
        reuse = display_inputs == getattr(self, '_display_inputs', None)
        if reuse:
            self.lib.artist_registry = self._display_registry
        else:
            self._build_artist_registry()
        self._apply_settings_to_lib(invalidate_rows=not reuse)
        self._display_inputs = display_inputs
        self._display_registry = self.lib.artist_registry
        self._browser_cache_key = browser_cache.fingerprint(display_inputs)
        if not reuse:
            prepared_rows = browser_cache.load(
                self._data_file.with_name('browsercache.json'), self._browser_cache_key)
            if prepared_rows is not None:
                self._row_cache.update({tid: row for tid, row in prepared_rows.items()
                                        if tid in self.lib.tracks_by_id})
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
        if self._libcache_worker is not None:
            # Keep only the newest authoritative library. Serializing an older
            # refresh after a newer one could otherwise leave stale instant
            # paint on the next launch even though the live UI was correct.
            # Ownership (not isRunning) is the gate: after run() returns there
            # is a short interval before Qt delivers ``finished``.
            self._libcache_pending = self.lib
            return
        self._start_library_cache(self.lib)

    def _start_library_cache(self, lib):
        if getattr(self, "_state_maintenance", False):
            return
        from .workers import OneShotTask
        path = self._libcache_path
        key = getattr(self, '_browser_cache_key', None)
        rows = {tid: dict(row) for tid, row in self._row_cache.items()}
        def save():
            libcache.dump(lib, path)
            if key is not None:
                browser_cache.dump(self._data_file.with_name('browsercache.json'), key, rows)
        w = OneShotTask(save,
                        log_label="Library snapshot cache")
        self._libcache_worker = w
        self._oneshots = getattr(self, "_oneshots", [])
        self._oneshots.append(w)
        w.done.connect(self._on_library_cache_done)
        w.finished.connect(lambda ww=w: self._finish_library_cache(ww))
        w.start()

    def _on_library_cache_done(self, result):
        if isinstance(result, Exception):
            LOG.warning("Library snapshot cache failed: %s", result)

    def _finish_library_cache(self, w):
        current = self._libcache_worker is w
        if current:
            self._libcache_worker = None
        self._finish_oneshot(w)
        if not current:
            return
        pending = self._libcache_pending
        self._libcache_pending = None
        if pending is not None and not self._shutting_down:
            self._start_library_cache(pending)

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
        active = getattr(self, '_view_key', '') or ''
        if cur is not None and not prev and active.startswith('rb:'):
            prev = active[3:]
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
            it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
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
    def _rekordbox_database_path(self) -> Path:
        return Path(self.settings.get("rekordbox_db_path") or rekordbox_db.REKORDBOX_DB).expanduser()

    def _sync_rekordbox_connection(self):
        section = getattr(self, "_rekordbox_section", None)
        if section is None:
            return
        busy = (self._refresh_worker is not None or self.tag_controller.owned or self._state_maintenance
                or self._shutting_down or self._device_operation_owned())
        source = str(self.lib.source) if self.lib else ""
        if self._refresh_worker is not None:
            status = "Reading database snapshot…"
        elif self._rekordbox_connection_error:
            status = "Last refresh failed · " + self._rekordbox_connection_error
        elif self.lib and self._rekordbox_source_verified:
            kind = "XML export" if self.lib.source_kind == "rekordbox-xml" else "Database"
            status = f"{kind} loaded · {self.lib.track_count:,} tracks · {len(self.lib.playlists):,} playlists"
        elif self.lib:
            status = "Saved snapshot · source not yet verified this session"
        else:
            status = "Not loaded · choose a database or XML export"
        section.set_state(status, str(self._rekordbox_database_path()), source,
                          busy=busy, custom=bool(self.settings.get("rekordbox_db_path")))

    def _choose_rekordbox_database(self):
        if self._refresh_worker is not None or self.tag_controller.owned or self._state_maintenance or self._device_operation_owned():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose rekordbox database", str(self._rekordbox_database_path().parent),
            "rekordbox database (*.db)")
        if path:
            self._start_rekordbox_refresh(Path(path), selection=True)

    def _use_default_rekordbox_database(self):
        if self._refresh_worker is not None or self.tag_controller.owned or self._state_maintenance or self._device_operation_owned():
            return
        self._start_rekordbox_refresh(rekordbox_db.REKORDBOX_DB, selection=True)

    def _refresh_rekordbox(self):
        if self._shutting_down or getattr(self, "_state_maintenance", False):
            return
        if self._refresh_worker is not None or self.tag_controller.owned:
            # A click during the snapshot is a request for a newer point in
            # time, not a no-op. Coalesce repeated clicks into one immediate
            # follow-up read when the current worker's finished callback lands.
            # The worker may have returned while that callback is still queued,
            # so ownership—not isRunning()—is the serialization boundary.
            self._refresh_pending = True
            return
        self._start_rekordbox_refresh(self._rekordbox_database_path())

    def _start_rekordbox_refresh(self, path: Path, *, selection=False):
        if self._shutting_down or self._state_maintenance or self._refresh_worker is not None or self.tag_controller.owned:
            return
        if not self._device_operation_owned():
            self._start_pulse("Loading")
        w = RefreshWorker(path)
        w.preflight = self.tag_controller.recover_before_read
        if selection:
            w.selection_path = str(path)
        self._refresh_worker = w
        w.finished.connect(lambda ww=w: self._finish_refresh_worker(ww))
        self._sync_rekordbox_connection()
        self._sync_tags()
        w.start()

    def _finish_refresh_worker(self, w):
        current = self._refresh_worker is w
        result, error = getattr(w, "result", None), getattr(w, "error", "")
        selection = getattr(w, "selection_path", None)
        self._finish_worker("_refresh_worker", w)
        if not current:
            return
        if not self._shutting_down:
            if result is not None:
                if selection is not None:
                    previous = dict(self.settings)
                    if Path(selection) == rekordbox_db.REKORDBOX_DB:
                        self.settings.pop("rekordbox_db_path", None)
                    else:
                        self.settings["rekordbox_db_path"] = selection
                    if not self._save_state():
                        self.settings = previous
                        result = None
                        error = "Database selection could not be saved; the previous library is retained."
                if result is not None:
                    self._on_refresh_done(result)
            if error:
                self._on_refresh_fail(error)
            self._sync_rekordbox_connection()
        pending = self._refresh_pending
        self._refresh_pending = False
        if pending and not self._shutting_down:
            QTimer.singleShot(0, self._refresh_rekordbox)

    def _on_refresh_done(self, lib):
        if not self._device_operation_owned():
            self._stop_pulse()
        self.lib = lib
        self._rekordbox_source_verified = True
        self._rekordbox_connection_error = ""
        self._apply_loaded_library()
        self._refresh_devices()
        LOG.info("Loaded %d tracks / %d playlists from rekordbox",
                 lib.track_count, len(lib.playlists))
        self._sync_rekordbox_connection()
        if not self._device_operation_owned():
            self._flash_status("Updated ✓", 6)
        self._cache_library()  # refresh the snapshot for the next instant paint
        self._arm_waveform_rechecks()
        if self.providers.allows("artwork.search"):
            QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        QTimer.singleShot(3250, self._maybe_prepare_stock_artwork)
        QTimer.singleShot(500, self._maybe_fill_waveforms)

    def _on_refresh_fail(self, err):
        self._rekordbox_connection_error = err
        self._sync_rekordbox_connection()
        if not self._device_operation_owned():
            self._set_status_text("Ready")
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
        if self.providers.allows("artwork.search"):
            if self._art_worker is not None and self._art_worker.isRunning():
                menu.addAction("Stop fetching cover art", self._stop_art_fill)
            else:
                menu.addAction("Fetch cover art (background)", self._start_art_fill)
            menu.addAction("Re-scan missing covers + automatic avatars", self._rescan_covers)
        if self.providers.allows("artists.suggest"):
            menu.addAction("Suggest artists for Unknowns…", self._suggest_artists)
        menu.addAction("Suggest artist merges…", self._suggest_merges)
        menu.exec(widget.mapToGlobal(pos))

    @_provider_action("artists.suggest")
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
        dlg = SuggestDialog(items, self, provider=self.providers.artists)
        dlg.exec()
        dlg.shutdown()  # stop/wait the scan thread BEFORE the dialog is GC'd
        if self._shutting_down or not dlg.accepts:
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
        if getattr(self, "_scan_worker", None) is not None:
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
        self._set_status_text("Ready")
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

    @_art_ui_action
    @_provider_action("artwork.search")
    def _rescan_covers(self):
        """Retry misses and explicitly automatic avatars. Manual and unrecorded
        artwork are protected, regardless of confidence or source URL."""
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
        path = cur.data(0, PATH_ROLE)
        if not path:
            return  # Folder navigation does not change the active playlist/view.
        self._select_only(self.tree)
        if self.lib is None:
            return
        self._stash_view()
        self.model.set_rows(self._annotate_art(self._playlist_rows(path)))
        self.stack.setCurrentIndex(0)
        self._restore_view(f"rb:{path}")

    # ---------- custom (SoundBox) playlists ----------
    def _new_custom(self):
        self._new_local_playlist()

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
        when the add-time is unknown (e.g. XML-loaded libraries). Only that
        fallback is cached per path; known dates never resolve media paths."""
        added = getattr(t, "added_ts", 0.0)
        if added:
            return added
        cache = self._landed_cache
        key = str(t.path)
        v = cache.get(key)
        if v is None:
            v = paths.file_landed_time(key)
            cache[key] = v
        return v

    def _visible_collection_tracks(self) -> list:
        """Filter Collection membership without sorting or resolving file paths."""
        if self.lib is None:
            return []
        lib = self.lib
        tracks = [t for t in lib.tracks_by_id.values()
                  if t.track_id not in lib.hidden_ids]
        if getattr(lib, "hide_untracked", False):
            tracks = [t for t in tracks
                      if not t.is_untracked(nokey=lib.hide_nokey, short=lib.hide_short,
                                            short_secs=lib.hide_short_secs)]
        return tracks

    def _collection_tracks(self) -> list:
        """Every loaded track (all playlists pooled), honoring the hide-untracked
        settings. Default order = newest-first by when the track was ADDED to the
        collection (see _landed_time) — one timeline across rekordbox adds and
        SoundBox imports alike. Ties break by title A→Z; tracks with no known
        add-time and a missing file (0) sort last."""
        tracks = self._visible_collection_tracks()
        return sorted(tracks, key=lambda t: (-self._landed_time(t), t.name.lower()))

    def _refresh_custom(self):
        # Preserve the current selection across the rebuild (same pattern as
        # _populate_tree) so a library refresh doesn't dump the user's view.
        cur = self.sb_tree.currentItem()
        keep_coll = bool(cur and cur.data(0, COLLECTION_ROLE))
        keep_artists = bool(cur and cur.data(0, ARTISTS_ROLE))
        keep_name = cur.data(0, CUSTOM_ROLE) if cur else None
        keep_folder = cur.data(0, CUSTOM_FOLDER_ROLE) if cur else None
        self.sb_tree.blockSignals(True)
        try:
            self.sb_tree.clear()
            # Built-in "Collection" pinned first: every track in one view. No
            # CUSTOM_ROLE (can't be renamed/deleted/reordered), but draggable
            # onto a device as a whole-pool playlist (sbview mime).
            tracks = self._visible_collection_tracks()
            coll = QTreeWidgetItem([f"⌂  Collection  ({len(tracks)})"])
            coll.setData(0, COLLECTION_ROLE, True)
            self.sb_tree.addTopLevelItem(coll)
            if keep_coll:
                self.sb_tree.setCurrentItem(coll)
            # Built-in "Artists": opens the BROWSER-side folder view (artist
            # names only; drop one down to see their songs). Count = distinct.
            generation = (self._cache_gen, tuple(t.track_id for t in tracks))
            cached_count = getattr(self, '_artist_count_cache', None)
            if cached_count is None or cached_count[0] != generation:
                names = set()
                for t in tracks:
                    cached = self._row_cache.get(t.track_id)
                    names.add(cached['artist'] if cached and 'artist' in cached else
                              metadata.effective_artist(
                                  t.track_id, self.titles.get(t.track_id) or t.name, t.artist,
                                  self.overrides, True, self.lib.artist_registry, stem=t.path.stem))
                cached_count = self._artist_count_cache = (generation, len(names))
            arts = QTreeWidgetItem([f"♪  Artists  ({cached_count[1]})"])
            arts.setData(0, ARTISTS_ROLE, True)
            self.sb_tree.addTopLevelItem(arts)
            if keep_artists:
                self.sb_tree.setCurrentItem(arts)
            self._populate_custom_folders(keep_name, keep_folder)
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
        if getattr(self, '_view_key', None) == f'sb:{name}':
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
        self._soundbox_context_menu(pos)

    # ---------- imported (non-rekordbox) audio ----------
    def _repair_import_locations(self):
        """Backfill precise locators and resolve moved direct imports at startup.

        This is intentionally not ``Fix Missing Links``: no directory is ever
        crawled automatically. Core Foundation bookmarks follow ordinary moves;
        one batched exact-name+size Spotlight query migrates legacy records or
        recovers a cross-volume copy. The explicit folder picker remains the
        fallback for anything ambiguous or not indexed.
        """
        if getattr(self, "_state_maintenance", False):
            return
        if self._import_location_worker is not None or self._shutting_down:
            return
        targets = {
            track_id: dict(rec)
            for track_id, rec in self.imported.items()
            if (not Path(str(rec.get("path", ""))).is_file()
                or not rec.get(file_locations.BOOKMARK_KEY))
        }
        if not targets:
            return
        from .workers import OneShotTask
        worker = OneShotTask(
            lambda: file_locations.repair_locations(targets),
            log_label="Imported-file location repair")
        self._import_location_worker = worker
        worker.done.connect(self._on_import_locations_repaired)
        worker.finished.connect(
            lambda ww=worker: self._finish_worker(
                "_import_location_worker", ww))
        worker.start()

    def _on_import_locations_repaired(self, result):
        if isinstance(result, Exception):
            LOG.warning("Imported-file location repair failed: %s", result)
            return
        changed = False
        moved = 0
        bookmarks = 0
        for track_id, update in result.updates.items():
            rec = self.imported.get(track_id)
            # An import/manual relink may have won while the worker ran. Never
            # overwrite newer state with a result planned against the old path.
            if rec is None or str(rec.get("path", "")) != update.expected_path:
                continue
            if update.path is not None:
                rec["path"] = str(update.path)
                moved += 1
                changed = True
            if (update.bookmark
                    and rec.get(file_locations.BOOKMARK_KEY) != update.bookmark):
                rec[file_locations.BOOKMARK_KEY] = update.bookmark
                bookmarks += 1
                changed = True
        if changed:
            self._save_state()
        if moved and self.lib is not None:
            imports.inject(self.lib, self.imported)
            self._build_artist_registry()
            self._refresh_custom()
            self._reload_current_view()
        LOG.info(
            "Imported-file location repair: %d missing, %d moved, "
            "%d locator(s) saved, %d unresolved",
            result.missing_count, moved, bookmarks, result.unresolved_count)
        if moved:
            self._flash_status(f"Relinked {moved} moved file(s) ✓", 15)

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
        fresh_ids: list[str] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if not imports.is_audio(p) or not p.is_file():
                skipped.append(p.name)
                continue
            tid = by_path.get(str(p))
            if tid is None:
                tid = imports.new_id()
                self.imported[tid] = imports.probe(p)
                fresh_ids.append(tid)
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
            if fresh_ids and self.settings.get('analysis', {}).get('automatic') is True:
                QTimer.singleShot(0, lambda captured=tuple(fresh_ids): self._analyze_tracks(captured, automatic=True))
            self._arm_waveform_rechecks()
            if self.providers.allows("artwork.search"):
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
        records (filename+size matching — see imports.find_moved). Startup has
        already tried precise bookmark/index resolution; this fallback scans
        only a folder the user explicitly chooses."""
        lost = imports.missing(self.imported)
        if not lost:
            self._flash_status("Links OK ✓", 5)
            return
        if roots is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Search folder for missing audio")
            if not folder:
                return
            roots = [Path(folder)]
        search = roots
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
                bookmark = file_locations.create_bookmark(p)
                if bookmark:
                    rec[file_locations.BOOKMARK_KEY] = bookmark
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
        self._rename_local_node('playlist', name)

    def _delete_custom(self, name: str):
        self._delete_local_node('playlist', name)

    def _on_custom_select(self, cur, _prev):
        if cur is None:
            return
        if cur.data(0, CUSTOM_FOLDER_ROLE):
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
        self._add_track_file_actions(menu, ids, reveal=bool(tid))
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
        from soundbox.playlist_folders import paths
        locations = paths(self._local_playlist_tree())
        return [Playlist(name=n, folder_path=locations.get(n, []), keys=list(ids))
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
            self, name: str, free, drift: int | None, fs: str = "",
            profile=None) -> str:
        # When a device is out of sync, HIDE the "GB free" so the full
        # "⚠ Sync needed" state fits (it was getting truncated). Free space
        # returns once everything is synced again.
        if drift is None:
            return f"⛃  {name}    Sync status pending"
        if drift:
            return f"⛃  {name}    ⚠ Sync needed"
        if fs and fs not in ("msdos", "fat32") and not getattr(profile,'cdj_export',False):
            # Rockbox runs on FAT32 only — flag HFS+/exFAT devices immediately.
            return f"⛃  {name}    ⚠ {fs.upper()} (not FAT32)"
        kind = f" · {profile.label}" if profile is not None else ""
        return f"⛃  {name}{kind}    {human(free)} free"

    def _on_volume_mount_changed(self):
        """Mount changes supersede idle/eject/playback holds, never an owner."""
        self._device_retry_delay = 3.0
        self._device_mount_refresh_pending = True
        self._refresh_devices(force=True)

    def _poll_mount_table(self):
        """Mount-over-directory events may not change /Volumes' timestamp."""
        snapshot = mounted_volume_snapshot()
        if snapshot is None or snapshot == self._mount_snapshot:
            return
        self._mount_snapshot = snapshot
        LOG.info("Kernel mount table changed; requesting device discovery")
        self._on_volume_mount_changed()

    def _refresh_devices(self, *, force: bool = False):
        """Kick a background scan of external devices (free space + drift).

        The scan touches the removable volume, which can block for seconds while a
        copy/delete hammers it — so it runs OFF the UI thread (DeviceScanWorker) and
        the tree is updated in `_on_devices_scanned`. Single-flight: if a scan is
        already running we skip, so the 3s timer can never pile up workers."""
        if (getattr(self, "_isolated", False)
                or getattr(self, "_state_maintenance", False)):
            return
        force = force or getattr(self, "_device_mount_refresh_pending", False)
        if not self._device_coordinator.polling_allowed:
            return
        # Finalization owns the last dashboard scan before Eject. Once that
        # verified result is latched, *every* incidental refresh must leave the
        # device alone; callers starting a genuinely new device operation clear
        # the latch explicitly before doing any work.
        if self._eject_ready_device and not force:
            return
        if force:
            self._release_eject_ready()
        # While OUR export/sync/delete is hammering the stick, don't also poll it —
        # the numbers are mid-flight and the extra reads only slow the operation down
        # (contention). Each op's done-handler calls _refresh_devices() right after.
        if self._exporting or self._deleting:
            return
        if self._dev_scan_worker is not None:
            return
        # A complete stock dashboard read hashes/parses iTunesDB. Never race a
        # sidebar drift reader against it on classic USB; the recurring poll is
        # the fallback and preloading explicitly requests a scan when finished.
        if self._stock_view_worker is not None:
            return
        raw_models = self.settings.get("stock_ipod_models")
        known_paths = {
            str(self.device_tree.topLevelItem(i).data(0, DEVPATH_ROLE))
            for i in range(self.device_tree.topLevelItemCount())
        } | set(self._stock_view_cache)
        if force and self._current_device_path:
            profile = self._device_profiles.get(self._current_device_path)
            if profile is not None and not getattr(profile, 'has_stock_ipod', False):
                self._usb_view_requests.add(self._current_device_path)
        usb_requests = frozenset(self._usb_view_requests)
        w = DeviceScanWorker(
            self.lib,
            self._sync_playlists(),
            artist_overrides=self.overrides,
            title_overrides=self.titles,
            stock_shadow_store=self.stock_ipod_shadow_store,
            stock_models=(raw_models if isinstance(raw_models, dict) else {}),
            known_paths=known_paths,
            known_mount_tokens=dict(self._device_mount_tokens),
            usb_views=dict(self._usb_view_cache), usb_requests=usb_requests,
        )
        self._usb_view_requests.difference_update(usb_requests)
        self._device_mount_refresh_pending = False
        self._dev_scan_worker = w
        w.discovered.connect(self._on_devices_discovered)
        w.finished.connect(lambda ww=w: self._finish_device_scan_worker(ww))
        self._start_device_worker(w, "", "discover")

    def _poll_devices(self):
        # Stat only the local /Volumes directory, not its mounted children. This
        # catches missed watcher events without opening an eject-ready device.
        try:
            stamp = os.stat("/Volumes").st_mtime_ns
        except OSError:
            stamp = None
        previous = getattr(self, "_volume_directory_stamp", None)
        self._volume_directory_stamp = stamp
        if stamp != previous:
            self._device_mount_refresh_pending = True
        if getattr(self, "_device_mount_refresh_pending", False):
            self._refresh_devices(force=True)
        elif (not self._eject_ready_device
              and (self.device_tree.topLevelItemCount()
                   or time.monotonic() >= getattr(self, "_device_retry_at", float("inf")))):
            self._refresh_devices()

    def _set_device_poll_interval(self, devices_present: bool) -> None:
        """Keep live drift responsive without busy-scanning an empty Mac."""
        self._dev_timer.setInterval(
            _DEVICE_POLL_ACTIVE_MS
            if devices_present else _DEVICE_POLL_IDLE_MS)

    def _device_operation_owned(self) -> bool:
        """Whether a real device transaction still owns interactive actions.

        The recurring read-only drift poll is excluded: Eject serializes behind
        it instead of surprising the user with a busy warning. Every mutating,
        planning, dashboard, import, delete, or eject worker remains an owner
        until its built-in QThread.finished cleanup clears the attribute.
        """
        owners = tuple(
            name for name in self._EJECT_BLOCKING_WORKERS
            if name != "_dev_scan_worker"
        ) + ("_eject_verify_worker",)
        coordinator = self._device_coordinator
        return bool(
            (coordinator.active is not None
             and coordinator.active.phase != Phase.DISCOVERING)
            or coordinator.eject_pending
            or coordinator.stopping
            or self._stock_view_warming
            or self._stock_view_pending
            or self._pending_device_operation is not None
            or self._exporting
            or self._deleting
            or any(getattr(self, name, None) is not None for name in owners)
        )

    def _update_device_action_state(self) -> None:
        """Make device action availability match actual transaction ownership."""
        busy = self._device_operation_owned()
        ready = bool(self._current_device_path) and not busy
        for button in (
                getattr(self, "eject_btn", None),
                getattr(self, "device_sync_btn", None)):
            if button is not None:
                button.setEnabled(ready)
                button.setToolTip(
                    "" if ready else (
                        "SoundBox is finishing the current device operation"
                        if busy else "Select a ready device first"))

        active = self._device_coordinator.active
        if (getattr(self, "eject_btn", None) is not None
                and self._current_device_path
                and active is not None
                and active.phase in {Phase.DISCOVERING, Phase.INSPECTING}
                and not self._device_coordinator.eject_pending):
            self.eject_btn.setEnabled(True)
            self.eject_btn.setToolTip("Eject after the current read finishes")

    def _release_eject_ready(self) -> None:
        """Release a completed operation's scan hold for new device work."""
        self._eject_ready_device = ""
        self._update_device_action_state()

    def _on_devices_discovered(self, devs: list):
        """Show discovered mounts immediately; retain ownership through finished.

        Device rows are disabled until discovery finishes. Stock dashboard
        loading never hides an otherwise recognized mounted device.
        """
        if QApplication.mouseButtons() & Qt.LeftButton:
            return
        current = {
            str(self.device_tree.topLevelItem(i).data(0, DEVPATH_ROLE))
            for i in range(self.device_tree.topLevelItemCount())
        }
        mount_tokens = dict(getattr(
            self._dev_scan_worker, "mount_tokens", {}))
        for name, path, free, profile in devs:
            token = int(mount_tokens.get(path, 0) or 0)
            previous_token = self._device_mount_tokens.get(path, 0)
            remounted = bool(
                token and previous_token and token != previous_token)
            if remounted:
                self._stock_view_cache.pop(path, None)
                self._stock_view_failures.pop(path, None)
                self._dual_sync_queue.pop(path, None)
            if token:
                self._device_mount_tokens[path] = token
                self._device_coordinator.observe(DeviceSessionIdentity(path, token))
            stock = bool(getattr(profile, "has_stock_ipod", False))
            if (stock and path not in self._stock_view_cache
                    and path not in self._stock_view_failures):
                self._stock_view_warming.add(path)
            if stock and path in self._stock_view_warming:
                self._start_ipod_loading_status()
                # Replace a same-name row with a disabled device row so its
                # old dashboard cannot be used under the new mount authority.
                for index in range(self.device_tree.topLevelItemCount() - 1, -1, -1):
                    item = self.device_tree.topLevelItem(index)
                    if item is not None and item.data(0, DEVPATH_ROLE) == path:
                        self.device_tree.takeTopLevelItem(index)
                current.discard(path)
                if self._current_device_path == path:
                    self._current_device_path = ""
                    self._stock_snapshot = None
                    self.stack.setCurrentIndex(0)
                self._device_profiles[path] = profile
                self._drift_cache.setdefault(path, None)
            if path not in current:
                item = QTreeWidgetItem([f"⛃  {name} · Checking…"])
                item.setData(0, DEVPATH_ROLE, path)
                item.setToolTip(0, "Connected — reading device details")
                item.setDisabled(True)
                self.device_tree.addTopLevelItem(item)
                current.add(path)

    def _finish_device_scan_worker(self, worker):
        if self._dev_scan_worker is not worker:
            self._finish_worker("_dev_scan_worker", worker)
            return
        if getattr(worker, "retry_discovery", True):
            delay = getattr(self, "_device_retry_delay", 3.0)
            self._device_retry_at = time.monotonic() + delay
            self._device_retry_delay = min(30.0, delay * 2)
        else:
            self._device_retry_at = float("inf")
            self._device_retry_delay = 3.0
        new_paths = tuple(getattr(worker, "new_paths", ()))
        results = getattr(worker, "results", None)
        usb_snapshots = dict(getattr(worker, 'usb_snapshots', {}))
        mount_tokens = dict(getattr(worker, "mount_tokens", {}))
        # Clear ownership before publishing any clickable row. A result emitted
        # from run() is not enough: QThread.finished is the actual boundary that
        # Eject and every other device operation use.
        self._finish_worker("_dev_scan_worker", worker)
        if self._shutting_down or self._device_coordinator.eject_pending:
            return
        if results is not None:
            self._usb_view_cache.update(usb_snapshots)
            self._on_devices_scanned(results, mount_tokens=mount_tokens)
            if not self.settings.get('cdj_my_settings'):
                native_profiles=[s for s in usb_snapshots.values() if getattr(s,'native',None) is not None]
                if len(native_profiles)==1:self._sync_usb_my_settings(native_profiles[0])
        # The first usable row already has drift. Queue a full,
        # immutable read for each newly connected stock-capable device; the
        # stock-view queue remains single-flight across classic USB.
        queued = False
        for path in new_paths:
            profile = self._device_profiles.get(path)
            if (profile is None
                    or not getattr(profile, "has_stock_ipod", False)
                    or path in self._stock_view_cache):
                continue
            self._stock_view_warming.add(path)
            self._start_ipod_loading_status()
            self._read_stock_device_view(path, profile)
            queued = True
        if not queued and self._stock_view_pending and self._stock_view_worker is None:
            next_dev = next(iter(self._stock_view_pending))
            profile = self._stock_view_pending.pop(next_dev)
            self._read_stock_device_view(next_dev, profile)

    def _defer_device_operation_until_scan_finishes(self, callback) -> bool:
        """Serialize a device operation behind an already-running poll.

        Setting ``_exporting`` immediately prevents the three-second timer from
        launching another scan in the gap between this worker finishing and the
        requested operation starting.
        """
        worker = getattr(self, "_dev_scan_worker", None)
        if worker is None:
            return False
        if self._pending_device_operation is not None:
            return True
        authority = dict(self._device_mount_tokens)
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
            if self._shutting_down or authority != self._device_mount_tokens:
                return
            callback()

        worker.finished.connect(resume)
        return True

    def _on_devices_scanned(self, devs: list, *, mount_tokens=None):
        """Apply a background device scan to the tree (main thread). devs is a list
        of (name, path, free, drift, read-only profile)."""
        # /Volumes notifications provide immediate discovery; the short timer
        # recovers missed events and retries transient probe failures.
        self._set_device_poll_interval(bool(devs))
        self._drift_cache = {
            path: drift for _n, path, _f, drift, _profile in devs}
        self._device_profiles = {
            path: profile for _n, path, _f, _d, profile in devs}
        all_paths = [p for _n, p, _f, _d, _profile in devs]
        self._usb_device_rows = {path: (name, free, drift, profile)
                                 for name, path, free, drift, profile in devs}
        self._usb_view_cache = {path: snapshot for path, snapshot in self._usb_view_cache.items()
                                if path in all_paths}
        self._usb_view_requests.intersection_update(all_paths)
        if mount_tokens is None:
            mount_tokens = dict(getattr(
                self._dev_scan_worker, "mount_tokens", {}))
        for path in all_paths:
            token = int(mount_tokens.get(path, 0) or 0)
            if token:
                self._device_mount_tokens[path] = token
                self._device_coordinator.observe(DeviceSessionIdentity(path, token))
        for path, snapshot in tuple(self._usb_view_cache.items()):
            if snapshot.mount_token != self._device_mount_tokens.get(path, 0):
                self._usb_view_cache.pop(path, None)
        if (self._current_device_path in self._usb_device_rows
                and not getattr(self._device_profiles.get(self._current_device_path), 'has_stock_ipod', False)):
            self._present_usb_device(self._current_device_path)
        stale_stock_paths = (
            set(self._stock_view_cache)
            | set(self._stock_view_warming)
        ) - set(all_paths)
        for stale in stale_stock_paths:
            self._stock_view_cache.pop(stale, None)
            self._stock_view_failures.pop(stale, None)
            self._stock_view_warming.discard(stale)
        for stale in set(self._device_mount_tokens) - set(all_paths):
            self._device_mount_tokens.pop(stale, None)
            self._device_coordinator.disconnect(stale)
            self._dual_sync_queue.pop(stale, None)
        # Reconcile any cached presentation left by a completed dashboard read.
        ready_paths = {
            path for path in self._stock_view_warming
            if path in self._stock_view_cache and path in all_paths
        }
        self._stock_view_warming.difference_update(ready_paths)
        # Publish connected rows independently of library/dashboard health.
        visible_devs = devs
        visible_paths = [row[1] for row in visible_devs]
        if (self._current_device_path
                and self._current_device_path not in visible_paths):
            self._current_device_path = ""
            self._stock_snapshot = None
            self.stack.setCurrentIndex(0)
        cur = [self.device_tree.topLevelItem(i).data(0, DEVPATH_ROLE)
               for i in range(self.device_tree.topLevelItemCount())]
        if visible_paths != cur and QApplication.mouseButtons() & Qt.LeftButton:
            # A device appeared/vanished DURING a drag (button held): rebuilding
            # the tree now would yank the drop target out from under the drag.
            # Skip this tick; the 3s poll redelivers right after the drop.
            return
        if visible_paths == cur:
            # Same devices — refresh label in place (don't disrupt an in-flight drag).
            for i, (name, path, free, drift, profile) in enumerate(visible_devs):
                it = self.device_tree.topLevelItem(i)
                if it is not None:
                    it.setDisabled(False)
                    it.setText(0, self._device_label(
                        name, free, drift, device_fs(path), profile))
                    it.setToolTip(0, self._device_tip(path, free, profile))
            self._finish_ipod_loading_status()
            return
        self.device_tree.clear()
        for name, path, free, drift, profile in visible_devs:
            it = QTreeWidgetItem([self._device_label(
                name, free, drift, device_fs(path), profile)])
            it.setData(0, DEVPATH_ROLE, path)
            it.setToolTip(0, self._device_tip(path, free, profile))
            self.device_tree.addTopLevelItem(it)
        self._finish_ipod_loading_status()

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
        if fs not in ("msdos", "fat32", "unknown") and not getattr(profile,'cdj_export',False):
            tip += "\n⚠ Rockbox requires FAT32 — this device would need reformatting."
        return tip

    def _on_device_click(self, item, _col):
        self._remember_stock_device_view()
        dev = item.data(0, DEVPATH_ROLE)
        if dev:
            self._select_only(self.device_tree)
            self._current_device_path = dev
            self.dev_header.setText(f"Device:  {Path(dev).name}   ({dev})")
            profile = self._device_profiles.get(dev)
            self.dev_header.setText(Path(dev).name)
            self.dev_header.setToolTip(dev)
            self.device_sync_btn.setText('Update Playlists' if getattr(profile,'kind','')=='generic_usb' else 'Sync')
            stock = bool(
                profile is not None
                and getattr(profile, "has_stock_ipod", False))
            from .usb_preferences import USB_TABS
            labels = ('Device', 'Songs', 'Playlists') if stock else USB_TABS
            for index, button in enumerate(self.device_tab_buttons):
                button.setVisible(index < len(labels))
                if index < len(labels):
                    button.setText(labels[index])
            if stock:
                self.device_content_stack.setCurrentIndex(1)
                cached = self._stock_view_cache.get(dev)
                if cached is not None:
                    # Connection-time preload already paid for the complete
                    # iTunesDB/dashboard read. Selecting the row is presentation
                    # only—no second removable-volume pass.
                    self._on_stock_device_view(dev, cached)
                elif dev in self._stock_view_failures:
                    self.stock_ipod_pages.set_error(self._stock_view_failures[dev], preserve_snapshot=False)
                else:
                    preserve_snapshot = bool(
                        self._stock_snapshot is not None
                        and self._stock_snapshot.target == Path(dev).resolve())
                    self.stock_ipod_pages.set_loading(
                        preserve_page=preserve_snapshot)
                    if not preserve_snapshot:
                        self._set_stock_device_tab(0)
                    self._read_stock_device_view(dev, profile)
            else:
                self.device_content_stack.setCurrentIndex(2)
                self._present_usb_device(dev)
                self._set_device_tab(self.usb_device_pages.stack.currentIndex())
            self.stack.setCurrentIndex(1)
            self._update_device_action_state()

    def _present_usb_device(self, dev):
        row = self._usb_device_rows.get(dev)
        if row is None:
            row = (Path(dev).name, None, self._drift_cache.get(dev), self._device_profiles.get(dev))
        name, free, drift, profile = row
        self.usb_device_pages.set_snapshot(self._usb_view_cache.get(dev),
                                          name=name, profile=profile, free=free, drift=drift)
        self._sync_usb_my_settings(self._usb_view_cache.get(dev))

    def _set_device_tab(self, index):
        if self.device_content_stack.currentIndex() != 2:
            self._set_stock_device_tab(index)
            return
        index = max(0, min(self.usb_device_pages.stack.count()-1, index))
        button = self.device_tab_buttons[index]
        blocked = button.blockSignals(True)
        try:
            button.setChecked(True)
        finally:
            button.blockSignals(blocked)
        self.usb_device_pages.set_page(index)
        if index == 1 and self._current_device_path:
            self._usb_view_requests.add(self._current_device_path)
            self._refresh_devices()

    def _set_stock_device_tab(self, index: int):
        if not self.device_tab_buttons:
            return
        index = max(0, min(2, int(index)))
        self.device_tab_buttons[index].setChecked(True)
        self.stock_ipod_pages.set_page(index)

    def _read_stock_device_view(
            self, dev: str, profile=None, *, after_finished=None):
        if getattr(self, "_state_maintenance", False):
            return
        if after_finished is not None:
            self._stock_view_callbacks.setdefault(dev, []).append(after_finished)
        if self._shutting_down or self._device_coordinator.eject_pending:
            return
        if (self._stock_view_worker is not None
                or self._dev_scan_worker is not None
                or self._device_coordinator.busy):
            self._stock_view_pending[dev] = profile
            return
        self._stock_view_pending.pop(dev, None)
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
                playlist_status = device_manifest.composite_playlist_status(
                    dev, composite_manifest)
                composite_data = CompositeDeviceData(
                    composite_manifest,
                    playlist_status.entries,
                    rockbox_config.database_auto_update_enabled(dev),
                    rockbox_config.database_file_count(dev),
                    rockbox_config.database_refresh_pending(dev),
                    playlist_status.changed_keys,
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
        self._stock_view_device = dev
        worker.finished.connect(
            lambda ww=worker: self._finish_stock_device_view_worker(ww))
        self._start_device_worker(worker, dev, "inspect")

    def _finish_stock_device_view_worker(self, worker):
        if self._stock_view_worker is not worker:
            self._finish_worker("_stock_view_worker", worker)
            return
        dev = self._stock_view_device
        self._stock_view_device = ""
        result = getattr(worker, "result", None)
        command = getattr(worker, "device_command", None)
        current = command is None or self._device_coordinator.current(command)
        self._finish_worker("_stock_view_worker", worker)
        if not current:
            self._stock_view_cache.pop(dev, None)
            return
        if self._shutting_down or self._device_coordinator.eject_pending:
            return
        if isinstance(result, Exception) and self._exporting:
            self._device_finalization_error = result
        self._on_stock_device_view(dev, result)
        callbacks = self._stock_view_callbacks.pop(dev, [])
        for callback in callbacks:
            callback()
        if self._stock_view_pending and not self._shutting_down:
            next_dev = next(iter(self._stock_view_pending))
            profile = self._stock_view_pending.pop(next_dev)
            QTimer.singleShot(
                0,
                lambda device=next_dev, value=profile:
                self._read_stock_device_view(device, value),
            )
            return

    def _refresh_device_surfaces(
            self, dev: str, *, after_finished=None,
            surface_progress: int = 900):
        """Refresh an open iPod snapshot, then its sidebar state, in series.

        A post-write completion callback runs only after both reads finish, so
        a success dialog never claims the iPod is done while Eject still has to
        wait for SoundBox's own finalization worker.
        """
        profile = self._device_profiles.get(dev)
        if (
            dev != self._current_device_path
            or profile is None
            or not getattr(profile, "has_stock_ipod", False)
        ):
            self._set_device_progress(surface_progress)
            self._finish_device_surface_refresh(
                after_finished, expected_dev=dev)
            return
        self._exporting = True
        self.stock_ipod_pages.set_loading(preserve_page=True)

        def finish_refresh():
            self._set_device_progress(surface_progress)
            self._finish_device_surface_refresh(
                after_finished, expected_dev=dev)

        self._read_stock_device_view(
            dev, profile, after_finished=finish_refresh)

    def _finish_device_surface_refresh(
            self, after_finished=None, *, expected_dev: str = ""):
        """Run the sidebar scan and optionally wait for its worker as well."""
        self._exporting = False
        # A new completed operation supersedes any earlier hold and must always
        # run its own final sidebar/dashboard scan before success is reported.
        self._refresh_devices(force=True)
        if after_finished is None:
            return
        worker = getattr(self, "_dev_scan_worker", None)
        if not _running(worker):
            if expected_dev and expected_dev not in self._device_profiles:
                self._device_finalization_error = RuntimeError(
                    f"{Path(expected_dev).name} disconnected during finalization")
            after_finished()
            return
        self._exporting = True

        def finish_sidebar():
            self._exporting = False
            if expected_dev and expected_dev not in self._device_profiles:
                self._device_finalization_error = RuntimeError(
                    f"{Path(expected_dev).name} disconnected during finalization")
            after_finished()

        worker.finished.connect(finish_sidebar)

    def _start_ipod_loading_status(self) -> None:
        """Own the lower-left status while an iPod is not yet clickable."""
        self._update_device_action_state()
        self._start_pulse("Loading iPod")

    def _finish_ipod_loading_status(self) -> None:
        """Return to Ready only after every warming iPod is publishable."""
        if self._stock_view_warming or self._pulse_word != "Loading iPod":
            return
        self._stop_pulse(force=True)
        self._set_status_text("Ready", force=True)
        self._update_device_action_state()

    def _fail_initial_ipod_preload(self, dev: str) -> None:
        """Keep a failed initial read hidden and let the next poll retry it."""
        if dev not in self._stock_view_warming:
            return
        self._stock_view_warming.discard(dev)
        self._stock_view_failures[dev] = "iPod library could not be read. Eject remains available."
        # Do not immediately recurse through preload -> rescan -> preload on a
        # persistently unreadable iPod. The existing three-second poll retries.
        if not self._stock_view_warming and self._pulse_word == "Loading iPod":
            self._stop_pulse(force=True)
            self._flash_status("iPod load failed — retrying", 3)
        self._update_device_action_state()

    def _finalize_ipod_operation(
            self, dev: str, on_complete, *, terminal_text: str = "Complete ✓") -> None:
        """Finish every post-write read before presenting success to the user."""
        self._exporting = True
        self._device_finalization_error = None
        self._set_device_progress(max(800, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Finalizing iPod")

        def finish():
            self._set_device_progress(1000)
            self._stop_pulse(force=True)
            finalization_error = self._device_finalization_error
            previous = self._device_coordinator.last_result
            if previous is not None:
                self._device_coordinator.last_result = replace(
                    previous, refresh="failed" if finalization_error else "complete")
            final_text = (
                "Final refresh failed" if finalization_error else terminal_text)
            self._set_status_text(final_text, force=True)
            # The committed dashboard/sidebar scan just finished. Freeze that
            # verified state through the success dialog. Release the hold on
            # dismissal so later curation edits and reconnects remain visible.
            self._eject_ready_device = dev
            # QMessageBox runs a nested Qt event loop. Keep polling suppressed
            # through that loop or the three-second timer can launch a fresh,
            # slow device scan while the user is still reading "Eject ready".
            self._exporting = False
            self._update_device_action_state()
            try:
                if finalization_error is not None:
                    QMessageBox.warning(
                        self,
                        "Device finalization failed",
                        ("Device changes were committed. " if previous and previous.committed
                         else "The device operation has stopped. ") +
                        "SoundBox could not complete its dashboard/sidebar "
                        f"refresh:\n\n{finalization_error}\n\n"
                        "Eject is available; reconnect before another write.",
                    )
                else:
                    on_complete()
            finally:
                self.progress.setVisible(False)
                self._release_eject_ready()

        self._refresh_device_surfaces(dev, after_finished=finish)

    def _finalize_device_operations(
            self, devices, on_complete, *,
            terminal_text: str = "Complete ✓") -> None:
        """Finalize every distinct target before one multi-device result.

        ExportWorker can accept another drop while it is active, including a
        different target. The transaction is therefore not terminal until each
        touched volume has completed its own dashboard/sidebar refresh.
        """
        ordered = tuple(dict.fromkeys(str(dev) for dev in devices if dev))
        if not ordered:
            self.progress.setVisible(False)
            on_complete()
            return
        if len(ordered) == 1:
            self._finalize_ipod_operation(
                ordered[0], on_complete, terminal_text=terminal_text)
            return

        self._exporting = True
        self._device_finalization_error = None
        self._set_device_progress(max(800, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Finalizing devices")

        def finish_all():
            self._set_device_progress(1000)
            self._stop_pulse(force=True)
            finalization_error = self._device_finalization_error
            final_text = (
                "Final refresh failed" if finalization_error else terminal_text)
            self._set_status_text(final_text, force=True)
            hold = (
                self._current_device_path
                if self._current_device_path in ordered else ordered[-1])
            self._eject_ready_device = hold
            self._exporting = False
            self._update_device_action_state()
            try:
                if finalization_error is not None:
                    QMessageBox.warning(
                        self,
                        "Device finalization failed",
                        "The device operation has stopped, but SoundBox could "
                        "not complete every required final dashboard/sidebar "
                        f"refresh:\n\n{finalization_error}\n\n"
                        "Eject is available; reconnect before another write.",
                    )
                else:
                    on_complete()
            finally:
                self.progress.setVisible(False)
                self._release_eject_ready()

        def finalize_at(index: int):
            if index >= len(ordered):
                finish_all()
                return
            start = 800 + round(200 * index / len(ordered))
            end = 800 + round(200 * (index + 1) / len(ordered))
            self._exporting = True
            self._set_device_progress(max(start, self.progress.value()))

            def finish_one():
                self._set_device_progress(end)
                finalize_at(index + 1)

            self._refresh_device_surfaces(
                ordered[index],
                surface_progress=start + (end - start) // 2,
                after_finished=finish_one,
            )

        finalize_at(0)

    def _on_stock_device_view(self, dev: str, result):
        if isinstance(result, Exception):
            self._fail_initial_ipod_preload(dev)
            if dev != self._current_device_path:
                LOG.warning("iPod dashboard preload failed for %s: %s", dev, result)
                return
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
            self._fail_initial_ipod_preload(dev)
            if dev != self._current_device_path:
                LOG.warning("Unexpected iPod dashboard preload result for %s", dev)
                return
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
        from soundbox.device_identity import remember_identity
        raw_identity = self.settings.get("stock_ipod_identity")
        identity_records = dict(raw_identity) if isinstance(raw_identity, dict) else {}
        result = remember_identity(result, identity_records)
        if identity_records != raw_identity:
            self.settings["stock_ipod_identity"] = identity_records
            self._save_state()
        self._stock_view_failures.pop(dev, None)
        self._stock_view_cache[dev] = (result, composite_data)
        self._stock_view_warming.discard(dev)
        self._finish_ipod_loading_status()
        if dev != self._current_device_path:
            LOG.info(
                "iPod dashboard preloaded: %s (%d tracks, %d playlists)",
                dev, len(result.tracks), len(result.playlists))
            return
        self._stock_snapshot = result
        if result.model:
            self._remember_stock_model(result.identity_key, result.model)
            profile = self._device_profiles.get(dev)
            if profile is not None:
                self._device_profiles[dev] = self._effective_device_profile(
                    dev, profile)
        self._restoring_stock_view = True
        try:
            self.stock_ipod_pages.set_snapshot(result, composite_data)
            self._restore_stock_device_view()
        finally:
            self._restoring_stock_view = False
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
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind=='generic_usb':
            busy=self._device_operation_owned()
            for title,callback in (
                    ('Update Playlists',lambda:self._begin_cdj_export(dev)),
                    ('Import “My Settings”',lambda:self._import_usb_my_settings(dev)),
                    ('Reformat USB…',lambda:self._reformat_usb(dev)),
                    ('Eject',lambda:self._eject(dev))):
                action=menu.addAction(title,callback);action.setEnabled(not busy)
            menu.exec(self.device_tree.mapToGlobal(pos))
            return
        menu.addAction("Browse", lambda: self._on_device_click(item, 0))
        busy = self._device_operation_owned()
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "stock_ipod":
            action = menu.addAction(
                "Preview stock iPod export…",
                lambda: self._preview_stock_ipod(dev))
            action.setEnabled(not busy)
        if profile is not None and profile.kind == 'generic_usb':
            recovery = menu.addAction('Recover interrupted CDJ export…', lambda: self._recover_cdj(dev))
            recovery.setEnabled(not busy)
        action = menu.addAction(
            "Verify export…", lambda: self._verify_device(dev))
        action.setEnabled(not busy)
        action = menu.addAction(
            "Deep Verify managed files…",
            lambda: self._verify_device(dev, deep=True))
        action.setEnabled(not busy)
        if profile is not None and profile.kind == "dual_boot":
            action = menu.addAction(
                "Repair managed library…",
                lambda: self._sync_device(dev, audit_existing=True))
            action.setEnabled(not busy)
        if profile is not None and not profile.has_stock_ipod:
            menu.addSeparator()
            action = menu.addAction(
                "Erase all files from device…",
                lambda: self._erase_device(dev))
            action.setEnabled(not busy)
        n = self._drift_cache.get(dev, 0)  # ≤3s-fresh from the background scan
        if n:
            word = "change" if n == 1 else "changes"
            sync_action = menu.addAction(
                f"Sync — {n} {word} pending",
                lambda: self._sync_device(dev))
        else:
            sync_action = menu.addAction(
                "Sync (check again)", lambda: self._sync_device(dev))
        sync_action.setEnabled(not busy)
        eject_action = menu.addAction("Eject", lambda: self._eject(dev))
        eject_action.setEnabled(not busy)
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
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_stock_ipod_sync(
                    dev, playlists, reconcile=reconcile)):
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        self._release_eject_ready()
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
        self._set_device_progress(0)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_stock_plan_worker", ww,
                lambda result: self._on_stock_ipod_plan(d, result)))
        self._start_device_worker(worker, dev, "plan")

    def _on_stock_ipod_plan(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            QMessageBox.warning(
                self, "Stock iPod plan refused", f"{result}\n\nNo files were changed.")
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodSyncPlan):
            self.progress.setVisible(False)
            QMessageBox.critical(self, "Stock iPod plan failed", "Unexpected plan result.")
            return
        self._set_device_progress(150)
        summary = result.summary()
        if not result.requires_commit:
            def complete():
                QMessageBox.information(
                    self,
                    "Stock iPod is up to date",
                    "The read-only comparison found no media, playlist, metadata, "
                    "artwork, or playback changes to publish.",
                )
                self._flash_status("iPod up to date")

            self._finalize_ipod_operation(dev, complete)
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
            self.progress.setVisible(False)
            self._flash_status("iPod sync cancelled — nothing written")
            self._refresh_devices()
            return
        self._commit_stock_ipod_plan(dev, result)

    def _commit_stock_ipod_plan(self, dev: str, plan: StockIpodSyncPlan):
        from .workers import OneShotTask
        worker = OneShotTask(lambda: commit_stock_ipod_sync(plan))
        self._stock_commit_worker = worker
        self._exporting = True
        self._set_device_progress(max(150, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Syncing iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_stock_commit_worker", ww,
                lambda result: self._on_stock_ipod_commit(d, result)))
        self._start_device_worker(worker, dev, "commit")

    def _on_stock_ipod_commit(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            QMessageBox.critical(
                self, "Stock iPod sync failed",
                f"{result}\n\nSoundBox attempted an exact database/media rollback.")
        else:
            self._set_device_progress(800)
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
            def complete():
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

            self._finalize_ipod_operation(dev, complete)
        if isinstance(result, Exception):
            self._refresh_devices()

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
            audit_existing: bool = False,
    ):
        """Build the composite stock/Rockbox plan without target writes."""
        if self._device_operation_owned():
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
                    audit_existing=audit_existing,
                )):
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        self._release_eject_ready()
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
        adoptable_playlist_keys = {f"cust:{name}" for name in self.custom}
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
                adoptable_playlist_keys=adoptable_playlist_keys,
                audit_existing=audit_existing,
            ),
            log_label="Dual iPod planning",
        )
        self._dual_plan_worker = worker
        self._exporting = True
        self.progress.setRange(0, 1000)
        if preconfirmed and self._dual_batch_total_tracks > 0:
            self.progress.setValue(round(
                800 * self._dual_batch_done_tracks
                / self._dual_batch_total_tracks))
        else:
            self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setVisible(True)
        self._set_status_count()
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.progress.connect(self._on_dual_ipod_plan_progress)
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_dual_plan_worker", ww,
                lambda result: self._on_dual_ipod_plan(d, result)))
        self._start_device_worker(worker, dev, "plan")

    def _on_dual_ipod_plan_progress(
            self, done: int, total: int, _label: str):
        """Map read-only planning into the first 30% of the transaction."""
        if self._cancelling:
            return
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        fraction = done / total
        if self._dual_active_preconfirmed and self._dual_batch_total_tracks > 0:
            completed = self._dual_batch_done_tracks
            active = self._dual_active_track_total
            value = round(_DEVICE_WORK_PROGRESS_END * (
                completed
                + active * _DUAL_PLAN_SHARE_OF_PREFINAL * fraction
            ) / self._dual_batch_total_tracks)
        else:
            value = round(_DUAL_PLAN_PROGRESS_END * fraction)
        self.progress.setRange(0, 1000)
        self.progress.setValue(max(self.progress.value(), value))
        self.progress.setVisible(True)
        # Internal planner units keep the percentage bar honest, but they are
        # not tracks or device writes and therefore do not belong in the
        # user-facing #/# counter. The animated phase label is sufficient.
        self._start_pulse("Planning iPod")
        self._set_status_count()

    def _on_dual_ipod_plan(self, dev: str, result):
        self._stop_pulse()
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
        self._set_device_progress(max(
            _DUAL_PLAN_PROGRESS_END, self.progress.value()))
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
            self._set_device_progress(_DUAL_PLAN_PROGRESS_END)
            self._set_status_count()
            try:
                self.rockbox_media_cache.clear()
            except Exception:
                pass

            def complete():
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

            self._finalize_ipod_operation(dev, complete)
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
        try:
            self._persist_rockbox_playlist_additions(plan)
        except RuntimeError as exc:
            self._exporting = False
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._dual_active_track_total = 0
            self.progress.setVisible(False)
            self._set_status_count()
            QMessageBox.critical(
                self,
                "Could not save playlist update",
                f"{exc}\n\nNo device files were changed.",
            )
            self._refresh_devices()
            return
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
            initial = round(_DEVICE_WORK_PROGRESS_END * (
                self._dual_batch_done_tracks
                + self._dual_active_track_total
                * _DUAL_PLAN_SHARE_OF_PREFINAL
            ) / self._dual_batch_total_tracks)
        else:
            initial = _DUAL_PLAN_PROGRESS_END
        self.progress.setValue(max(self.progress.value(), initial))
        self.progress.setFormat("%p%")
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(False)
        self._start_pulse(
            "Syncing audio" if total > 0 else "Syncing iPod",
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
        worker.finished.connect(
            lambda ww=worker, d=dev, p=plan:
            self._finish_device_result_worker(
                "_dual_commit_worker", ww,
                lambda result: self._on_dual_ipod_commit(d, result, p)))
        self._start_device_worker(worker, dev, "commit")

    def _on_dual_ipod_progress(self, done: int, total: int, _label: str):
        if self._cancelling:
            return
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
                "Syncing audio",
                keep_count=self._dual_batch_total_tracks > 0)
            self._set_status_count(
                batch_done, self._dual_batch_total_tracks)
            active_fraction = (
                _DUAL_PLAN_SHARE_OF_PREFINAL
                + (1.0 - _DUAL_PLAN_SHARE_OF_PREFINAL)
                * done / max(1, total)
            )
            value = round(_DEVICE_WORK_PROGRESS_END * (
                self._dual_batch_done_tracks
                + self._dual_active_track_total * active_fraction
            ) / max(1, self._dual_batch_total_tracks))
            self.progress.setRange(0, 1000)
            self.progress.setValue(max(self.progress.value(), value))
            self.progress.setVisible(True)
            return
        self._start_pulse(
            "Syncing audio" if total > 0 else "Syncing iPod",
            keep_count=total > 0)
        self._set_status_count(done, total)
        value = round(
            _DUAL_PLAN_PROGRESS_END
            + (_DEVICE_WORK_PROGRESS_END - _DUAL_PLAN_PROGRESS_END)
            * done / max(1, total))
        self.progress.setRange(0, 1000)
        self.progress.setValue(max(self.progress.value(), value))
        self.progress.setVisible(True)

    def _on_dual_ipod_commit(
            self, dev: str, result, plan: DualIpodSyncPlan | None = None):
        self._exporting = False
        self._stop_pulse()
        try:
            self.rockbox_media_cache.clear()
        except Exception as exc:
            LOG.warning("Rockbox media staging cleanup failed: %s", exc)
        adopted_additions = (
            sum(
                len(item.track_ids)
                for item in getattr(plan, "playlist_additions", ())
            )
            if plan is not None else 0
        )
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
                800 * self._dual_batch_done_tracks
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
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            self._show_dual_ipod_failure(result)
        else:
            self._set_device_progress(800)
            cleanup_warning = (
                "\n\nSome now-unreferenced files could not be removed:\n"
                + "\n".join(result.cleanup_failures)
                if result.cleanup_failures else "")
            adopted_note = (
                f"\n\nAdopted {adopted_additions} Rockbox playlist "
                "addition(s)."
                if adopted_additions else ""
            )
            def complete():
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
                    f"{adopted_note}"
                    f"{cleanup_warning}\n\n"
                    f"Recovery files:\n{result.stock.recovery_directory}\n\n"
                    "Eject is ready when you dismiss this message.",
                )
                self._flash_status("iPod synced")
                self._dual_active_device = ""
                self._dual_active_playlist_keys.clear()
                self._dual_active_confirmed = False
                self._dual_active_action = "sync"
        if isinstance(result, Exception):
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._refresh_devices()
        else:
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
                self._finalize_ipod_operation(dev, complete)

    def _persist_rockbox_playlist_additions(
            self, plan: DualIpodSyncPlan) -> int:
        """Durably adopt proven membership before the coordinated device write.

        If the device phase fails, Rockbox's original append is restored or
        remains live and the saved SoundBox membership makes the next Sync a
        truthful retry instead of losing the user's on-device choice.
        """
        updates = getattr(plan, "playlist_updates", ())
        if not updates and plan.playlist_additions:
            raise RuntimeError("Playlist additions have no verified SoundBox membership update")
        for item in updates:
            if not item.playlist_key.startswith("cust:"):
                raise RuntimeError("iPod additions can sync only into SoundBox playlists")
            name = item.playlist_key.removeprefix("cust:")
            if tuple(self.custom.get(name, ())) != item.before or name not in self.custom:
                raise RuntimeError("SoundBox playlist changed during planning; please Sync again")
        if not updates:
            return 0
        added = sum(len(set(item.after) - set(item.before)) for item in updates)
        for item in updates:
            self.custom[item.playlist_key.removeprefix("cust:")] = list(item.after)
        if not self._save_state():
            for item in updates:
                self.custom[item.playlist_key.removeprefix("cust:")] = list(item.before)
            raise RuntimeError(
                "SoundBox could not save the recognized Rockbox playlist "
                "update to library.json"
            )
        self._apply_settings_to_lib()
        self._refresh_custom()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        return added

    def _show_dual_ipod_failure(self, result) -> None:
        recovery = getattr(result, "recovery_status", None)
        if getattr(result, "committed", False):
            detail = "Device changes were committed; final cleanup did not complete."
        elif recovery == "not needed":
            detail = "Sync stopped before device writes. No device files were changed."
        elif recovery == "rolled back":
            detail = "The coordinated stock/Rockbox rollback completed."
        elif recovery == "recovery required":
            detail = ("The coordinated stock/Rockbox rollback was incomplete. "
                      "Keep the device recovery files; recovery needs review before retrying.")
        else:
            detail = ("Device recovery status could not be established. "
                      "Check the device status before retrying.")
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
        self._set_device_progress(800)
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
        job_count = len(self._dual_batch_names)
        track_count = self._dual_batch_total_tracks

        def complete():
            QMessageBox.information(
                self,
                "iPod queue complete",
                f"{job_count} playlist job"
                f"{'s' if job_count != 1 else ''} complete\n"
                f"{track_count} queued track entries\n\n"
                f"{playlist_names}\n\n"
                f"Media copied: {copied}\n"
                f"Media metadata refreshed: {refreshed}\n"
                f"Unreferenced files removed: {removed}\n\n"
                "Eject is ready when you dismiss this message.",
            )
            self._flash_status("iPod synced")
            self._reset_dual_batch()

        self._finalize_ipod_operation(dev, complete)

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
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_stock_playlist_delete(raw_selections)):
            return
        profile = self._device_profiles.get(dev)
        model = self._stock_write_model(dev, profile)
        if not model:
            return
        self._release_eject_ready()
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
        self._set_device_progress(0)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev, expected=tuple(identities):
            self._finish_device_result_worker(
                "_stock_delete_plan_worker", ww,
                lambda result: self._on_stock_playlist_delete_plan(
                    d, expected, result)))
        self._start_device_worker(worker, dev, "plan-delete")

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
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            QMessageBox.warning(
                self,
                "Stock iPod delete refused",
                f"{result}\n\nNo playlists, database records, or media files changed.",
            )
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodDeletePlan):
            self.progress.setVisible(False)
            QMessageBox.critical(
                self, "Stock iPod delete failed", "Unexpected plan result.")
            return
        self._set_device_progress(150)
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
            self.progress.setVisible(False)
            self._flash_status("iPod deletion cancelled — nothing written")
            self._refresh_devices()
            return
        self._commit_stock_playlist_delete(dev, result)

    def _commit_stock_playlist_delete(self, dev: str, plan: StockIpodDeletePlan):
        from .workers import OneShotTask
        worker = OneShotTask(lambda: commit_stock_ipod_playlist_delete(plan))
        self._stock_delete_commit_worker = worker
        self._exporting = True
        self._set_device_progress(max(150, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Syncing iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_stock_delete_commit_worker", ww,
                lambda result: self._on_stock_playlist_delete_commit(d, result)))
        self._start_device_worker(worker, dev, "commit-delete")

    def _on_stock_playlist_delete_commit(self, dev: str, result):
        self._exporting = False
        self._stop_pulse()
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            QMessageBox.critical(
                self,
                "Stock iPod deletion failed",
                f"{result}\n\nSoundBox attempted an exact database/artwork rollback.",
            )
        else:
            self._set_device_progress(800)
            multiple = len(result.playlists) != 1
            def complete():
                if result.cleanup_failures:
                    QMessageBox.warning(
                        self,
                        "Playlists deleted; media cleanup incomplete" if multiple
                        else "Playlist deleted; media cleanup incomplete",
                        f"Deleted {'playlists' if multiple else 'playlist'}: "
                        f"{', '.join(result.playlists)}\n"
                        f"Removed {result.deleted_track_records} database "
                        f"track(s) and {result.deleted_media_files} media file(s).\n\n"
                        "The iPod database is consistent, but these unindexed "
                        f"files could not be removed:\n"
                        f"{chr(10).join(result.cleanup_failures)}\n\n"
                        f"Recovery files:\n{result.recovery_directory}\n\n"
                        "Eject is ready when you dismiss this message.",
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Stock iPod playlists deleted" if multiple
                        else "Stock iPod playlist deleted",
                        f"Deleted {'playlists' if multiple else 'playlist'}: "
                        f"{', '.join(result.playlists)}\n"
                        f"Removed {result.deleted_track_records} unused track "
                        f"record(s) and {result.deleted_media_files} media file(s) "
                        f"({human(result.deleted_bytes)}).\n\n"
                        f"Remaining: {result.remaining_playlists} user playlist(s), "
                        f"{result.remaining_tracks} track(s).\n\n"
                        f"Recovery files:\n{result.recovery_directory}\n\n"
                        "Eject is ready when you dismiss this message.",
                    )
                self._flash_status(
                    "iPod playlists deleted" if multiple
                    else "iPod playlist deleted")

            self._finalize_ipod_operation(dev, complete)
        if isinstance(result, Exception):
            self._refresh_devices()

    def _verify_device(self, dev: str, deep: bool = False):
        """Post-export sanity: every m3u8 entry on the device must resolve to a
        real file. Walks the USB, so it runs OFF the UI thread."""
        from .workers import OneShotTask
        if self._device_operation_owned():
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._verify_device(dev, deep)):
            return
        self._release_eject_ready()
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == 'generic_usb':
            from soundbox.cdj_export import verify
            facts = device_facts(dev,cached_only=True)
            token = self._device_mount_tokens.get(dev,0)
            from .cdj import CdjWorker
            w = CdjWorker(lambda cancel,_progress: verify(dev,token=token,volume_uuid=facts.volume_uuid,deep=deep,should_cancel=cancel))
        else:
            w = OneShotTask(lambda: engine.verify_export(dev, deep=deep))
        self._device_verify_worker = w
        w.finished.connect(
            lambda ww=w, d=dev: self._finish_device_result_worker(
                "_device_verify_worker", ww,
                lambda result: self._on_verify_done(d, result)))
        self._start_pulse("Verifying")
        self._start_device_worker(w, dev, "verify")

    def _on_verify_done(self, dev: str, res):
        self._stop_pulse()
        self._set_status_text("Ready")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Verify failed", str(res))
            return
        name = Path(dev).name
        if not res["playlists"] and not res["missing"] and not res.get("managed_issues"):
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
            missing_detail = (
                f"{len(res['missing'])} of {res['entries']} playlist entries are "
                f"missing their files:\n\n{lines}{more}\n\n"
                if res["missing"] else "")
            QMessageBox.warning(
                self, "Verify export",
                f"{name}: verification found problems.\n\n{missing_detail}"
                + ("Use Repair managed library for missing owned Rockbox files. "
                   "Other integrity problems need review. Normal Sync trusts "
                   "the committed inventory."
                   if getattr(self._device_profiles.get(dev), "kind", "") == "dual_boot"
                   else "A Sync usually heals this (or re-export the affected playlists).") +
                f"{integrity}{extra}")

    def _confirm_ipod_sync(self, dev: str, *, audit_existing: bool = False) -> bool:
        answer = QMessageBox.question(
            self,
            "Repair managed library?" if audit_existing else "Sync iPod?",
            f"Sync the managed library on {Path(dev).name} now?\n\n"
            + ("SoundBox will inspect the managed inventory, reconcile missing "
               "files and clean interrupted writes. This can take longer.\n\n"
               if audit_existing else
               "SoundBox will trust its committed inventory and read playlist "
               "edits made on the iPod. Use Verify for a full file check.\n\n") +
            "SoundBox will compare the current playlists and metadata, then "
            "apply only the required stock/Rockbox changes. Planning and "
            "writing will appear as one continuous operation. Source files "
            "remain unchanged.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _sync_device(self, dev: str, *, audit_existing: bool = False):
        if self.lib is None or not dev:
            return
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "generic_usb":
            self._begin_cdj_export(dev)
            return
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
            if not self._confirm_ipod_sync(dev, audit_existing=audit_existing):
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
                audit_existing=audit_existing,
            )
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._sync_device(dev, audit_existing=audit_existing)):
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        self._release_eject_ready()
        LOG.info("Sync device: %s", dev)
        w = ResyncWorker(
            self.lib, dev, self.overrides, self.titles, self.art_cache,
            self.rockbox_media_cache, self.rockbox_art_cache,
            self._sync_playlists())
        self._resync_worker = w
        w.progress.connect(self._on_resync_progress)
        w.finished.connect(
            lambda ww=w, d=dev: self._finish_device_result_worker(
                "_resync_worker", ww,
                lambda result: self._on_resync_done(d, result)))
        self._exporting = True  # pauses the device poll + keeps bg art status quiet
        # Even a fast diff-driven sync uses the shared transaction meter: work
        # may reach 80%, while final reads and ownership release retain 80–100%.
        self._set_device_progress(0)
        self._start_pulse("Syncing")
        self._start_device_worker(w, dev, "sync")

    def _on_resync_done(self, dev: str, rep):
        self._exporting = False
        self._stop_pulse()
        self._set_device_progress(800)

        def complete():
            if isinstance(rep, str):
                LOG.error("Sync failed for %s: %s", dev, rep)
                self._set_status_text("Sync failed")
                QMessageBox.warning(self, "Sync failed", rep)
            else:
                LOG.info("Sync complete: %s", dev)
                self._flash_status("Synced ✓", 8)
                if self.stack.currentIndex() == 1:
                    self.fs_view.setRootIndex(
                        self.fs_model.setRootPath(self.fs_model.rootPath()))

        self._finalize_ipod_operation(
            dev, complete,
            terminal_text="Sync failed" if isinstance(rep, str) else "Complete ✓")

    def _on_resync_progress(self, done: int, total: int, _label: str):
        value = round(
            150 + 650 * min(max(0, done), max(1, total)) / max(1, total))
        self._set_device_progress(max(self.progress.value(), value))
        if self._cancelling:
            return
        self._start_pulse("Syncing")

    # ---------- columns ----------
    def _apply_default_columns(self):
        self._browser_column_fit = True
        try:
            for i, (_k, _l, hidden) in enumerate(COLUMNS):
                self.table.setColumnHidden(i, hidden)
            for i, w in enumerate([360, 170, 64, 70, 56, 64, 84, 80, 150, 110]):
                self.table.setColumnWidth(i, w)
        finally:
            self._browser_column_fit = False
        self._fit_browser_columns(fill=True)

    def _fit_browser_columns(self, column=None, *, fill=False, available=None):
        """Bound independent column drags; fit viewport changes separately."""
        if getattr(self, '_browser_column_fit', False):
            return
        if not getattr(self, '_browser_columns_shown', False):
            return
        header = self.table.horizontalHeader()
        visible = [i for i in range(header.count()) if not header.isSectionHidden(i)]
        if not visible:
            return
        if available is None:
            view = (self.usb_device_pages.playlist_tree if self.usb_device_pages.playlist_tree.isVisible()
                    else self.artists_view if self.artists_view.isVisible() else self.table)
            available = view.viewport().width()
        minimum = header.minimumSectionSize()
        self._browser_column_fit = True
        try:
            if 0 in visible and fill:
                other = sum(header.sectionSize(i) for i in visible if i != 0)
                header.resizeSection(0, max(minimum, available - other))
            overflow = sum(header.sectionSize(i) for i in visible) - available
            # Manual drags change only the dragged section. Window shrink or
            # newly shown columns can require reducing existing widths too.
            order = [column] if column is not None else [0, *reversed(visible)]
            for i in order:
                if overflow <= 0:
                    break
                if i not in visible:
                    continue
                width = header.sectionSize(i)
                take = min(overflow, max(0, width - minimum))
                if take:
                    header.resizeSection(i, width - take)
                    overflow -= take
        finally:
            self._browser_column_fit = False
        # Optional columns can exceed even the minimum widths. Keep every
        # selected field reachable instead of stranding it offscreen.
        policy = Qt.ScrollBarAsNeeded if len(visible)*minimum>available else Qt.ScrollBarAlwaysOff
        for view in (self.table,self.artists_view,self.usb_device_pages.playlist_tree):
            view.setHorizontalScrollBarPolicy(policy)
        self._sync_artist_columns()

    def _on_browser_column_resized(self, column, _old_size, _new_size):
        if getattr(self, '_browser_column_fit', False):
            return
        self._fit_browser_columns(column)
        self._sync_artist_columns(column)

    def _sync_artist_columns(self, *resized):
        """Mirror every table section boundary into the Artists browser."""
        if getattr(self, "_artist_column_sync", False):
            return
        self._artist_column_sync = True
        try:
            table_header = self.table.horizontalHeader()
            artist_header = self.artists_view.header()
            columns = (resized[0],) if resized else range(len(COLUMNS))
            for column in columns:
                hidden = self.table.isColumnHidden(column)
                if self.artists_view.isColumnHidden(column) != hidden:
                    self.artists_view.setColumnHidden(column, hidden)
                width = table_header.sectionSize(column)
                if artist_header.sectionSize(column) != width:
                    artist_header.resizeSection(column, width)
                usb = self.usb_device_pages.playlist_tree
                usb.setColumnHidden(column,hidden)
                if usb.columnWidth(column) != width:
                    usb.setColumnWidth(column,width)
            for view in (self.artists_view,self.usb_device_pages.playlist_tree):
                header = view.header()
                for visual in range(table_header.count()):
                    logical = table_header.logicalIndex(visual)
                    current = header.visualIndex(logical)
                    if current != visual:
                        header.moveSection(current,visual)
        finally:
            self._artist_column_sync = False

    def _on_artist_column_resized(
            self, column: int, _old_size: int, new_size: int):
        """A resize made on Artists becomes the normal shared column width."""
        if getattr(self, "_artist_column_sync", False):
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
        self._add_track_file_actions(menu, ids)
        # Imported files the user moved on disk: offer to find them again.
        # Shown from any imported row — a move usually breaks a batch at once,
        # so the action heals ALL missing links, not just the clicked row.
        if any(i in self.imported for i in ids):
            lost = imports.missing(self.imported)
            if lost:
                if self._import_location_worker is not None:
                    locating = menu.addAction(
                        f"Locating Moved Files… ({len(lost)})")
                    locating.setEnabled(False)
                else:
                    menu.addAction(f"Fix Missing Links… ({len(lost)})",
                                   self._fix_links)
        self._add_track_reanalysis_action(menu, ids)
        menu.addSeparator()
        menu.addAction("Choose cover art…", lambda: self._art_pick(tid))
        if self.providers.allows("artwork.search"):
            menu.addAction("Fetch cover art now", lambda: self._art_fetch_one(tid))
        if self.providers.allows("artwork.url"):
            menu.addAction("Set cover art from URL…", lambda: self._art_from_url(tid))
        # A managed default is not a per-track cover and cannot be removed from
        # this menu: blocked tracks intentionally receive defaults too. Only a
        # real cover/avatar exposes the per-track Remove action.
        if self.art_cache.canonical_image_path(tid):
            menu.addAction("Remove cover art", lambda: self._art_remove(tid))
        menu.addSeparator()
        # Playlist views expose both scopes: remove only from the current
        # playlist, or remove globally from SoundBox's Collection. Collection
        # and other derived all-track views expose only the global action.
        self._add_track_removal_actions(menu, ids)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _add_track_reanalysis_action(self, menu: QMenu, ids: list[str]):
        n = len(ids)
        menu.addAction(
            "Reanalyze Track" if n == 1 else f"Reanalyze {n} Tracks",
            lambda selected=tuple(ids): self._reanalyze_tracks(selected),
        )

    def _add_track_file_actions(
            self, menu: QMenu, ids: list[str], *, reveal: bool = True):
        """Add source-file utilities in their stable menu order."""
        selected = tuple(ids)
        n = len(selected)
        menu.addAction(
            "Copy File Name" if n == 1 else f"Copy {n} File Names",
            lambda: self._copy_filenames(list(selected)),
        )
        if reveal:
            menu.addAction(
                "Open in Finder",
                lambda: self._open_in_finder(selected),
            )

    def _add_track_removal_actions(self, menu: QMenu, ids: list[str]):
        """Add the scope-explicit reversible removal actions for this view.

        rekordbox playlists receive a SoundBox-side veto only; custom playlists
        are edited directly. Collection removal hides the track from every
        SoundBox view/export but never writes rekordbox or touches source audio.
        """
        n = len(ids)
        key = getattr(self, '_view_key', '') or ''
        rb_path = key[3:] if key.startswith('rb:') else None
        cust_name = key[3:] if key.startswith('sb:') and key[3:] in self.custom else None
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

    def _open_in_finder(self, ids):
        """Reveal the selected source files without opening or modifying them."""
        found: list[str] = []
        missing: list[str] = []
        seen: set[Path] = set()
        for track_id in ids:
            track = self.lib.tracks_by_id.get(str(track_id)) if self.lib else None
            if track is None:
                missing.append(str(track_id))
                continue
            path = track.path
            try:
                available = path.is_file()
            except OSError:
                available = False
            if not available:
                missing.append(str(track_id))
            elif path not in seen:
                seen.add(path)
                found.append(str(path))
        if not found:
            QMessageBox.warning(
                self, "File unavailable",
                "The selected source file is missing or unavailable.",
            )
            return
        if missing:
            LOG.warning(
                "Open in Finder skipped missing track id(s): %s",
                ", ".join(sorted(missing)),
            )
        started = self._start_finder_reveal(found)
        ok = started[0] if isinstance(started, tuple) else bool(started)
        if not ok:
            QMessageBox.warning(
                self, "Finder unavailable",
                "SoundBox could not ask Finder to reveal the selected file.",
            )
        elif missing:
            self._flash_status(
                f"Opened in Finder; skipped {len(missing)} missing ✕", 8)

    @staticmethod
    def _start_finder_reveal(found: list[str]):
        return QProcess.startDetached("/usr/bin/open", ["-R", *found])

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
        self._discard_graveyard_analysis(ids)

    def _discard_graveyard_analysis(self, track_ids) -> tuple[int, int]:
        """Drop global-Graveyard analysis without touching source audio."""
        ids = {
            str(track_id) for track_id in track_ids
            if str(track_id) and str(track_id) in self.hidden
        }
        if not ids:
            return 0, 0
        self.player_bar.cancel_analysis_cache_writes(ids)
        removed, reclaimed = track_analysis.discard_records(
            self._track_analysis_dir, ids)
        if removed:
            LOG.info(
                "Graveyard analysis cleanup: %d record(s), %.1f MiB",
                removed, reclaimed / 1024 ** 2)
        return removed, reclaimed

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
                                  parent=self, allow_url=self.providers.allows("artwork.url"),
                                  rename_bindings=self._keyboard_router.keymap.bindings("browser.rename"))
        dlg.exec()  # persistent: user fixes any number of artists, then closes

    def _configure_art_sources(self):
        configure = getattr(self.providers.artwork, "configure_source_exclusions", None)
        if callable(configure):
            configure(self.art_cache.source_exclusions())
        if getattr(self, "lib", None) is not None and not getattr(self, "_shutting_down", False):
            self._refresh_model_art()
            if any(self.art_cache.source_excluded(tid) for tid in self.lib.tracks_by_id):
                self._start_art_fill(restart=True)

    def _open_art_review(self):
        from .artreview import ArtReviewDialog
        if self.lib is None:
            self._flash_status("Load a library to review covers", 6)
            return
        rows = []
        for tid, track in self.lib.tracks_by_id.items():
            if tid in self.lib.hidden_ids or self.art_cache.status(tid) != "ok":
                continue
            title = self.titles.get(tid) or track.name
            artist = metadata.effective_artist(
                tid, title, track.artist, self.overrides, True,
                self.lib.artist_registry, stem=track.path.stem)
            origin = self.art_cache.index[tid].get("origin")
            label = {"manual": "Manual", "auto": "Automatic"}.get(origin, "Unrecorded history")
            priority = 0 if origin == "auto" else 2 if origin == "manual" else 1
            rows.append((priority, tid, artist, title, label))
        rows.sort(key=lambda row: (row[0], row[2].casefold(), row[3].casefold()))
        provider = self.providers.artwork if self.providers.allows("artwork.search") else None
        def audition(tid):
            track = self.lib.tracks_by_id.get(tid) if self.lib else None
            if track is None:
                return False
            self._review_audition_active = True
            return self._open_track(track, autoplay=True, capture_autoplay=False)
        dlg = ArtReviewDialog(self.art_cache, [row[1:] for row in rows], self, provider=provider,
                             player=self.player_bar, play_track=audition)
        dlg.audition.stopped.connect(lambda: setattr(self, "_review_audition_active", False))
        dlg.changed.connect(self._review_art_changed)
        dlg.exclusions_changed.connect(self._configure_art_sources)
        try:
            dlg.exec()
        finally:
            dlg.shutdown()
            self._review_audition_active = False

    def _review_art_changed(self, tid):
        self._refresh_model_art()
        self._queue_stock_artwork((tid,))

    def _open_default_photos(self):
        from .defaultphotos import DefaultPhotosDialog

        dlg = DefaultPhotosDialog(
            self.art_cache.default_photos(),
            on_action=self._on_default_photo_action,
            parent=self,
        )
        dlg.exec()

    def _on_default_photo_action(self, choice: dict, done):
        """Import/delete SoundBox-owned pool copies, then refresh every art
        consumer. This never changes the per-track cover index: successful
        covers/avatars keep precedence and misses/blocks remain recorded."""
        from .workers import OneShotTask

        if _running(self._default_photo_worker):
            done(False, None, "Default photos are already being updated.")
            return
        if choice["kind"] == "add":
            operation = lambda: self.art_cache.import_default_photos(choice["paths"])
        else:
            operation = lambda: self.art_cache.remove_default_photos(choice["ids"])
        worker = OneShotTask(operation)
        self._default_photo_worker = worker
        worker.done.connect(
            lambda result, c=choice, callback=done:
                self._finish_default_photo_action(c, result, callback))
        worker.finished.connect(
            lambda w=worker: self._finish_worker("_default_photo_worker", w))
        worker.start()

    def _finish_default_photo_action(self, choice: dict, result, done):
        try:
            if isinstance(result, Exception):
                raise result
            if choice["kind"] == "add":
                added = result["added"]
                duplicates = result["duplicates"]
                errors = result["errors"]
                bits = [f"Added {added}"]
                if duplicates:
                    bits.append(f"{duplicates} duplicate{'s' if duplicates != 1 else ''} skipped")
                if errors:
                    bits.append(f"{len(errors)} unreadable")
                message = " · ".join(bits) + " ✓"
                if errors:
                    QMessageBox.warning(
                        self, "Some photos weren't added", "\n".join(errors[:12]))
            else:
                removed = int(result)
                message = f"Deleted {removed} managed photo{'s' if removed != 1 else ''} ✓"
        except Exception as exc:  # noqa: BLE001 — worker returns exact failure
            QMessageBox.warning(self, "Couldn't update default photos", str(exc))
            done(False, None, "Default photos weren't changed.")
            return

        # The pool methods rebalance the already-configured live universe.
        # Repaint statuses, recompute device drift, and warm each unique
        # canonical pool JPEG through the ordinary stock-iPod derivative cache.
        self._refresh_model_art()
        if self.lib is not None:
            self._reload_current_view()
        self._refresh_devices()
        for row in self.art_cache.default_photos():
            try:
                self._stock_art_pending.add(row["path"].resolve(strict=True))
            except OSError:
                continue
        if not _running(self._stock_art_worker):
            QTimer.singleShot(0, self._start_pending_stock_artwork)
        self._flash_status(message, 8)
        done(True, self.art_cache.default_photos(), message)

    def _on_catalog_action(self, c: dict, done):
        try:
            self._apply_catalog_action(c, done)
        except (OSError, ValueError) as exc:
            LOG.error("Artist artwork was not saved: %s", exc)
            QMessageBox.warning(self, "Artwork wasn't saved", str(exc))
            done(False, None)

    def _apply_catalog_action(self, c: dict, done):
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
            self._refresh_model_art()
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
            self._apply_artist_picture(tids, ArtResult(
                data=data, mime=mime, confidence=1.0,
                source_url=Path(c["path"]).as_uri(), matched_title=c["artist"]))
            done(True, self.art_cache.image_path(tids[0]))
            return
        if (c["kind"] != "url" or not self.providers.allows("artwork.url")
                or self._shutting_down or self._state_maintenance):
            done(False, None)
            return
        # url fetch hits the network — off the UI thread, dialog stays live
        from .workers import OneShotTask
        if getattr(self, "_scan_worker", None) is not None:
            done(False, None)
            return
        w = OneShotTask(lambda u=c["url"]: self.providers.artwork.fetch_by_url(u))
        self._scan_worker = w

        def landed(res, tids=tids, done=done):
            if self._shutting_down or self._state_maintenance:
                return
            if isinstance(res, Exception) or res is None:
                done(False, None)
                return
            try:
                self._apply_artist_picture(tids, res)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Artwork wasn't saved", str(exc))
                done(False, None)
                return
            done(True, self.art_cache.image_path(tids[0]))

        w.done.connect(landed)
        w.finished.connect(lambda ww=w: self._finish_worker("_scan_worker", ww))
        w.start()

    def _apply_artist_picture(self, tids, res):
        """One picture → every avatar track of that artist (store_ok normalizes
        to Rockbox-safe baseline JPEG per track)."""
        for tid in tids:
            self.art_cache.store_ok(tid, res)
        self._refresh_model_art()
        self._queue_stock_artwork(tids)
        self._flash_status(f"Art set on {len(tids)} ✓", 8)

    @_art_ui_action
    def _art_remove(self, tid):
        # BLOCK, not forget: a deliberate removal must never be undone by the
        # background fill on the next launch (it was — infuriating).
        self.art_cache.block(tid)
        self._refresh_model_art()

    @_art_ui_action
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
        dlg = ArtPickerDialog(artist, title, self, provider=self.providers.artwork)
        dlg.exec()
        dlg.shutdown()  # stop/wait the search thread BEFORE the dialog is GC'd
        choice = dlg.choice
        if self._shutting_down or not choice:
            return
        if choice["kind"] == "thumb" and self.providers.allows("artwork.search"):
            c = choice["cand"]
            self._run_oneshot(tid, lambda c=c: self.providers.artwork.fetch_thumb(
                c["thumb"], c["url"], c["title"]))
        elif choice["kind"] == "page" and self.providers.allows("artwork.url"):
            self._run_oneshot(tid, lambda u=choice["url"]: self.providers.artwork.fetch_by_url(u))
        elif choice["kind"] == "file":  # local image remains shared
            p = Path(choice["path"])
            try:
                data = p.read_bytes()
            except OSError as exc:
                QMessageBox.warning(self, "Couldn't read image", str(exc))
                return
            mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            self.art_cache.store_ok(tid, ArtResult(
                data=data, mime=mime, confidence=1.0,
                source_url=p.as_uri(), matched_title=p.name))
            self._flash_status("Art set ✓", 5)
            self._refresh_model_art()
            self._queue_stock_artwork((tid,))

    def _run_oneshot(self, tid, fn):
        if self._shutting_down or self._state_maintenance:
            return
        if self._background_status_available():
            self._set_status_text("Fetching…")
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

    @_art_ui_action
    def _on_oneshot(self, tid, res):
        if self._shutting_down or self._state_maintenance:
            return
        if isinstance(res, Exception):
            # Honest failure: the fetch BROKE (network, block, bug) — that is
            # not "no art found". Log the whole story; keep the flash short
            # (the status label is narrow).
            LOG.warning("Art fetch failed for %s: %s", tid, res, exc_info=res)
            if self._background_status_available():
                self._flash_status("Fetch failed ✕", 8)
        elif res:
            self.art_cache.store_ok(tid, res)
            self._queue_stock_artwork((tid,))
            if self._background_status_available():
                self._flash_status("Art set ✓", 5)
        else:
            if self._background_status_available():
                self._flash_status("No art found", 5)
        self._refresh_model_art()  # update the Art column

    @_provider_action("artwork.search")
    def _art_fetch_one(self, tid):
        t = self.lib.tracks_by_id.get(tid) if self.lib else None
        if not t:
            return
        title = self.titles.get(tid) or t.name
        artist = metadata.effective_artist(tid, title, t.artist, self.overrides, True,
                                           self.lib.artist_registry, stem=t.path.stem)
        self._run_oneshot(tid, lambda: self.providers.artwork.fetch_best(artist, title))

    @_provider_action("artwork.url")
    def _art_from_url(self, tid):
        url, ok = QInputDialog.getText(
            self, "Cover art",
            "SoundCloud URL (track = its cover, artist profile = avatar):")
        if ok and url.strip():
            self._run_oneshot(tid, lambda u=url.strip(): self.providers.artwork.fetch_by_url(u))

    @_provider_action("artwork.search")
    def _maybe_autostart_art_fill(self):
        """Auto-fetch runs ONLY when the library gained tracks the cache has
        never seen (new rekordbox adds) — and the fill worker's to-do list is
        exactly those uncached tracks, so ONLY the new songs get searched.
        Everything else (re-scans, per-track fetches) is user-triggered."""
        if self.lib is None:
            return
        idx = self.art_cache.index
        visible_ids = [tid for tid in self.lib.tracks_by_id
                       if tid not in self.lib.hidden_ids]
        fresh = sum(1 for tid in visible_ids if tid not in idx)
        excluded = any(kind == 'blocked' for kind in
                       self.art_cache.status_kinds(
                           tid for tid in visible_ids
                           if (idx.get(tid) or {}).get('status') == 'ok').values())
        if fresh or excluded:
            LOG.info("Art fill autostart: %d new track(s) without cache entries", fresh)
            self._start_art_fill()

    @_provider_action("artwork.search")
    def _start_art_fill(self, restart: bool = False):
        if not self._state_repository.writable:
            return
        if getattr(self, "_state_maintenance", False):
            return
        if self.lib is None or self._shutting_down:
            return
        # A stopped thread still owns this slot until its queued finished
        # callback runs. Replacing it earlier lets that callback retire a new run.
        if self._art_worker is not None:
            # A pass is already running. For a re-scan we must restart so the
            # worker picks up the cache entries we just cleared (it locked its
            # to-do list at startup). Stop it; _on_art_finished starts the fresh one.
            if restart:
                self._art_restart = True
                self._art_worker.stop()
            return
        w = ArtFillWorker(self.lib, self.art_cache, self.overrides, self.titles,
                          provider=self.providers.artwork)
        self._art_worker = w
        w.set_playback_active(self.player_bar.is_playing())
        w.progress.connect(self._on_art_progress)
        w.failed.connect(self._on_art_persistence_failed)
        w.profile_changed.connect(self._on_profile_art_changed)
        w.finished.connect(lambda ww=w: self._on_art_finished(ww))
        w.start()

    def _on_profile_art_changed(self, tid):
        self._refresh_model_art()
        from .artreview import ArtReviewDialog
        for dialog in self.findChildren(ArtReviewDialog):
            dialog.refresh_profile(tid)

    def _on_art_persistence_failed(self, message):
        LOG.error("Artwork pass stopped: %s", message)
        self._flash_status("Artwork pass stopped: could not save — check space/permissions", 20)

    def _on_art_finished(self, worker):
        worker.deleteLater()
        if self._art_worker is not worker:
            return
        self._art_worker = None
        if self._shutting_down:
            self._art_restart = False
            return
        self._art_status_timer.start()
        self._queue_stock_artwork(getattr(worker, 'changed_track_ids', ()))
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
        if getattr(self, "_state_maintenance", False):
            return
        if (self._shutting_down or not self._stock_art_pending
                or self._stock_art_worker is not None):
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
        if getattr(self, "_state_maintenance", False):
            return
        if self.lib is None or self._shutting_down:
            return
        if self._stock_art_worker is not None:
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
        if self._shutting_down:
            return
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
        if getattr(self, "_state_maintenance", False):
            return
        if self.lib is None:
            return
        if self.tag_controller.owned:
            self._waveform_rescan_pending = True
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
        forced = set(self._waveform_force_pending)
        self._waveform_force_pending.clear()
        w = WaveformFillWorker(
            self.lib, self._track_analysis_dir,
            force_track_ids=forced,
        )
        self._wf_fill_worker = w
        w.set_playback_active(self.player_bar.is_playing())
        w.prioritize(self.player_bar._src_path or "")
        w.detail_ready.connect(lambda path: self.player_bar.refresh_audio_detail(path)
                               if not self.tag_controller.owned else None)
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
        self._prep_progress = None
        self._show_preparation_status()

    def _background_status_available(self) -> bool:
        """Background upkeep never takes over a foreground device operation."""
        return (not self._shutting_down and not self._cancelling
                and not getattr(self, '_prep_update_message', '')
                and not self._device_operation_owned()
                and self._refresh_worker is None
                and self._pulse_word not in {"Loading", "Scanning", "Searching"})

    def _show_preparation_status(self) -> None:
        if not self._preparing_library or not self._background_status_available():
            return
        if self._prep_storage_pause is not None:
            free_bytes, resume_bytes = self._prep_storage_pause
            self._set_status_text(
                f"Analysis paused · Low disk space · "
                f"{free_bytes / 1024 ** 3:.1f} GiB free")
            self.status_label.setToolTip(
                "Background analysis will resume automatically at "
                f"{resume_bytes / 1024 ** 3:.0f} GiB free. "
                "Existing library data is preserved.")
        elif self._prep_power_policy == "low-power":
            self._set_status_text("Prep paused · Low Power")
        else:
            word = ("Preparing · Battery Eco" if self._prep_power_policy == "battery"
                    else "Preparing your library")
            self._start_pulse(word, keep_count=self._prep_progress is not None)
            if self._prep_progress is not None:
                self._set_status_count(*self._prep_progress)

    def _on_analysis_progress(self, done: int, total: int):
        self._prep_progress = (done, total)
        self._show_preparation_status()

    def _on_analysis_power_policy(self, policy: str):
        self._prep_power_policy = policy
        self._show_preparation_status()

    def _on_analysis_storage_paused(
            self, paused: bool, free_bytes: int, resume_bytes: int):
        self._prep_storage_pause = (free_bytes, resume_bytes) if paused else None
        self._show_preparation_status()

    def _on_library_prep_finished(self, w):
        forced = set(getattr(w, "force_track_ids", ()))
        completed_forced = set(
            getattr(w, "completed_forced_track_ids", ()))
        self._finish_worker("_wf_fill_worker", w)
        self._preparing_library = False
        self._prep_storage_pause = None
        self._prep_progress = None
        if self._background_status_available():
            self._stop_pulse()
            failed = forced - completed_forced
            if failed:
                LOG.warning(
                    "Manual reanalysis failed for track id(s): %s",
                    ", ".join(sorted(failed)),
                )
                self._flash_status(
                    "Reanalysis failed ✕" if len(failed) == 1
                    else f"{len(failed)} reanalyses failed ✕",
                    10,
                )
            elif forced:
                self._flash_status(
                    "Reanalyzed ✓" if len(forced) == 1
                    else f"Reanalyzed {len(forced)} ✓",
                    8,
                )
            else:
                self._set_status_text("Ready")
        if getattr(self, "_waveform_rescan_pending", False):
            self._waveform_rescan_pending = False
            QTimer.singleShot(0, self._maybe_fill_waveforms)
        else:
            self._schedule_waveform_recheck()

    def _reanalyze_tracks(self, track_ids):
        """Atomically recook selected disposable SoundBox analysis records.

        Rekordbox analysis and source audio remain read-only. A successful cook
        imports whatever complete ANLZ streams exist now, then atomically
        replaces the SoundBox cache and hot-refreshes the loaded deck.
        """
        if self.lib is None:
            return
        requested = {str(track_id) for track_id in track_ids if str(track_id)}
        available = {
            track_id for track_id in requested
            if track_id not in self.hidden
            and (track := self.lib.tracks_by_id.get(track_id)) is not None
            and track.path.is_file()
        }
        if not available:
            QMessageBox.warning(
                self, "Reanalysis unavailable",
                "The selected source audio is missing or unavailable.",
            )
            return
        missing = requested - available
        if missing:
            LOG.warning(
                "Manual reanalysis skipped missing track id(s): %s",
                ", ".join(sorted(missing)),
            )
        self._waveform_force_pending.update(available)
        LOG.info(
            "Manual reanalysis requested for track id(s): %s",
            ", ".join(sorted(available)),
        )
        self._maybe_fill_waveforms()
        if _running(self._wf_fill_worker):
            self._start_pulse(
                "Reanalyzing track" if len(available) == 1
                else f"Reanalyzing {len(available)} tracks")

    def _stop_art_fill(self):
        if self._art_worker is not None and self._art_worker.isRunning():
            self._art_worker.stop()
            # Clear the stale "Art N/M" ticker (unless a device op owns the label).
            if self._background_status_available():
                self._set_status_text("Ready")

    def _on_art_progress(self, done, total):
        if not self.player_bar.is_playing():
            self._refresh_model_art()
        else:
            self._art_status_timer.start()
        if self._background_status_available() and not self._preparing_library:
            # A device op (export/sync/delete) owns the status bar; don't fight it.
            if done >= total:
                self._flash_status("Art done ✓", 6)
            else:
                self._start_pulse("Fetching artwork", keep_count=True)
                self._set_status_count(done, total)

    def _flush_art_status(self):
        if self._shutting_down:
            return
        if self.player_bar.is_playing():
            self._art_status_timer.start()
        else:
            self._refresh_model_art()

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
        self._fit_browser_columns(fill=True)
        self._sync_artist_columns()
        self._save_columns()  # visibility toggles persist immediately

    def _save_columns(self):
        """Persist the complete user-sized shell.

        This runs at shutdown after the user's final resize, so the next launch
        restores the outer window first and the three sidebar panes second.
        """
        hh = self.table.horizontalHeader()
        self.settings["columns_state"] = bytes(hh.saveState().toBase64()).decode("ascii")
        self.settings['columns_keys'] = [key for key,_label,_hidden in COLUMNS]
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
        self._browser_column_fit = True
        try:
            if hh.restoreState(QByteArray.fromBase64(state.encode("ascii"))):
                # Qt restores an older header successfully but unhides every
                # section added since it was saved. New fields are opt-in.
                keys=self.settings.get('columns_keys')
                if not isinstance(keys,list):keys=[key for key,_label,_hidden in COLUMNS[:10]]
                for i,(key,_label,hidden) in enumerate(COLUMNS):
                    if key not in keys:self.table.setColumnHidden(i,hidden)
                hh.setSortIndicator(-1, Qt.AscendingOrder)
                hh.setSectionResizeMode(QHeaderView.Interactive)
                hh.setStretchLastSection(False)
        finally:
            self._browser_column_fit = False
        self._fit_browser_columns()
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
                    from soundbox.playlist_folders import paths
                    locations = paths(self._local_playlist_tree())
                    pls.append(Playlist(name=val, folder_path=locations.get(val, []), keys=list(ids)))
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
        """Plan every already-confirmed playlist in one device generation."""
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
        self._dual_sync_queue[dev] = []
        self._begin_dual_ipod_sync(
            dev, jobs, preconfirmed=True)

    def _on_device_drop(self, dev: str, items: list[tuple[str, str]]):
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
            return
        pls = self._resolve_jobs(items)
        if not pls:
            return
        profile = self._device_profiles.get(dev)
        if profile is not None and profile.kind == "generic_usb":
            self._begin_cdj_export(dev, pls, from_drop=True)
            return
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
            self.export_worker.add(dev, pls, mount_token=self._device_mount_tokens.get(dev, 0))  # aggregate into the running export
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._on_device_drop(dev, items)):
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        self._release_eject_ready()
        LOG.info("Export: %d playlist(s) -> %s", len(pls), dev)
        w = ExportWorker(
            self.lib, self.overrides, self.titles, self.art_cache,
            self.rockbox_media_cache, self.rockbox_art_cache)
        self.export_worker = w
        self._exporting = True
        w.progress.connect(self._on_progress)
        w.finished.connect(
            lambda ww=w: self._finish_device_result_worker(
                "export_worker", ww, self._on_export_done))
        # Always start the bar at a truthful 0% before any files are processed.
        self._set_device_progress(0)
        self._start_pulse("Exporting")  # animated dots from the first frame
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        w.add(dev, pls, mount_token=self._device_mount_tokens.get(dev, 0))
        self._start_device_worker(w, dev, "export")

    def _start_device_worker(self, worker, dev: str, intent: str) -> None:
        coordinator = self._device_coordinator
        target = coordinator.mounts.get(dev)
        if target is None:
            target = DeviceSessionIdentity(dev, self._device_mount_tokens.get(dev, 0))
            if dev:
                coordinator.observe(target)
        phase = (Phase.DISCOVERING if intent == "discover" else
                 Phase.INSPECTING if intent in {"inspect", "verify", "import"} else
                 Phase.PLANNING if intent.startswith("plan") else Phase.EXECUTING)
        command = DeviceCommand(target, intent)
        state = coordinator.begin(command, worker, phase)
        worker.device_command = command
        if hasattr(worker, "cancellation"):
            worker.cancellation = state.cancellation
        worker.cancel_after_result = phase == Phase.PLANNING
        if hasattr(worker, "phase_progress"):
            worker.phase_progress.connect(
                lambda stage, done, total, label, w=worker:
                self._on_device_phase_progress(w, stage, done, total, label))
        worker.start()
        if phase in {Phase.PLANNING, Phase.EXECUTING}:
            self.cancel_btn.setVisible(True)
        self._update_device_action_state()

    def _on_device_phase_progress(self, worker, stage, done, total, label):
        active = self._device_coordinator.active
        if (active is None or active.worker is not worker
                or active.cancellation.is_set() or self._shutting_down):
            return
        active.progress = (done, total, label)
        # The operation's main progress callback owns the visible phase/count.
        # Small media/artwork writes can emit several phases in one UI frame;
        # replacing the primary label here resets its dots and makes the entire
        # row jump between track counts and byte totals. Keep details on hover.
        detail = f"{stage}\n{label}"
        if total:
            detail += f"\n{human(done)} / {human(total)}"
        self.status_label.setToolTip(detail)

    def _cancel_export(self):
        if self._cdj_waiting is not None:
            self._cdj_waiting = None
            self._stop_pulse()
            self.cancel_btn.setVisible(False)
            self._flash_status('CDJ request cancelled — nothing written')
            return
        self._cdj_waiting = None
        self._device_coordinator.cancel()
        self._pending_device_operation = None
        self._dual_sync_queue.clear()
        for attr in self._EJECT_BLOCKING_WORKERS:
            worker = getattr(self, attr, None)
            if worker is not None and hasattr(worker, "cancel"):
                worker.cancel()
        self._cancelling = True
        self._start_pulse("Cancelling")

    def _finish_worker(self, attr: str, w):
        """Drop our reference to a finished worker on the MAIN thread (after run()
        has fully returned) and schedule its C++ deletion. This is the crux fix for
        'QThread destroyed while running' — never let the last ref drop mid-run."""
        self._device_coordinator.finished(w)
        if getattr(self, attr, None) is w:
            setattr(self, attr, None)
        try:
            w.deleteLater()
        except RuntimeError:
            pass  # already gone
        self._update_device_action_state()
        if (getattr(self, "_device_mount_refresh_pending", False)
                and not self._shutting_down):
            # A mount event held behind an owner need not wait for the next
            # three-second drift poll. Admission still checks every owner.
            QTimer.singleShot(0, self._refresh_devices)

    def _finish_device_result_worker(self, attr: str, worker, callback) -> None:
        """Clear QThread ownership before exposing a device result to the UI."""
        current = getattr(self, attr, None) is worker
        result = getattr(worker, "result", None)
        command = getattr(worker, "device_command", None)
        if command is not None:
            current = current and self._device_coordinator.current(command)
            self._device_coordinator.publish(worker, adapter_result(command, result))
        self._finish_worker(attr, worker)
        if isinstance(result, Exception):
            self._dual_sync_queue.clear()
        if current and not self._shutting_down:
            callback(result)

    def shutdown(self):
        """Stop background work BEFORE Qt tears down — prevents the crash where
        the app quits while a worker thread is still running."""
        if getattr(self, "_shutdown_waiting", False):
            return
        self._shutdown_waiting = True
        if getattr(self,'usb_device_pages',None) is not None:
            self.usb_device_pages._remember_view()
        if hasattr(self,'_remember_stock_device_view'):
            self._remember_stock_device_view()
        self._shutting_down = True
        self._cdj_waiting = None
        keyboard = getattr(self, "_keyboard_router", None)
        if keyboard is not None:
            keyboard.release_all()
        self._refresh_pending = False
        self._libcache_pending = None
        self._stock_view_pending.clear()
        self._waveform_rechecks_enabled = False
        for t in (getattr(self, "_dev_timer", None), getattr(self, "_pulse_timer", None),
                  getattr(self, "_warm_timer", None),
                  getattr(self, "_settings_save_timer", None),
                  getattr(self, "_art_status_timer", None),
                  getattr(self, "_volume_refresh_timer", None),
                  getattr(self, "_mount_table_timer", None),
                  getattr(self, "_waveform_recheck_timer", None)):
            if t is not None:
                t.stop()
        try:
            self._save_columns()  # final widths (drag-resizes) persist at quit
        except Exception:  # noqa: BLE001 — never block shutdown
            pass
        self._art_restart = False  # don't let a pending re-scan restart during teardown
        self._stock_art_rescan = False
        from .reviewplayer import ReviewPlayer
        for audition in self.findChildren(ReviewPlayer):
            audition.shutdown()
        self._device_coordinator.cancel(shutdown=True)
        self._pending_device_operation = None
        self._dual_sync_queue.clear()
        # Session cue edits are already visible/usable. Finish their accepted
        # FIFO before the generic owner stop closes replacement admission.
        self.grid_controller.shutdown()
        self.tag_controller.shutdown()
        # Child dialogs/player own threads outside the window's attributes.
        # Close their admission before dispatching any queued completion work.
        task_owners = self.findChildren(TaskOwner)
        for owner in task_owners:
            owner.stop()
        # Keep every QThread referenced and keep Qt dispatching until its real
        # completion. A timeout can never grant permission to destroy a writer.
        workers = [value for value in vars(self).values()
                   if isinstance(value, QThread)]
        workers.extend(getattr(self, "_oneshots", []))
        for worker in workers:
            if not _running(worker):
                continue
            if hasattr(worker, "cancel"):
                worker.cancel()
            elif hasattr(worker, "stop"):
                worker.stop()
            else:
                worker.requestInterruption()
        if any(_running(worker) for worker in workers):
            from PySide6.QtCore import QEventLoop
            self._set_status_text("Stopping device work safely…")
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(50)
            timer.timeout.connect(
                lambda: loop.quit()
                if not any(_running(worker) for worker in workers) else None)
            timer.start()
            loop.exec()
            timer.stop()
        for owner in task_owners:
            owner.drain()
        pb = getattr(self, "player_bar", None)
        if pb is not None:
            pb.stop()
            pb.shutdown()  # workers plus native audio/display threads
        p = getattr(self, "_eject_proc", None)
        if p is not None and p.state() != QProcess.NotRunning:
            from PySide6.QtCore import QEventLoop
            loop = QEventLoop()
            p.finished.connect(loop.quit)
            loop.exec()
        self._shutdown_waiting = False

    def closeEvent(self, event):
        if (getattr(self, "_shutdown_waiting", False)
                or getattr(self, "_state_maintenance", False)):
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)

    def _on_progress(self, done: int, total: int, label: str):
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        value = round(
            150 + 650 * min(max(0, done), max(1, total)) / max(1, total))
        self._set_device_progress(max(self.progress.value(), value))
        if self._cancelling:
            return  # keep the animated "Cancelling…" status
        if total and done >= total:
            # Files copied; the engine is scrubbing the device. This is the
            # 80% work boundary, not completion; dashboard/sidebar release owns
            # the final 20% before the bar can reach 100%.
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
        fm = self.status_count_label.fontMetrics()
        self.status_count_label.setFixedWidth(max(
            fm.horizontalAdvance("9999/9999"),
            fm.horizontalAdvance(f"{total}/{total}")) + 16)
        self.status_count_label.setText(f"{done}/{total}")
        self.status_count_label.setVisible(True)

    def _set_device_progress(self, value: int) -> None:
        """Publish truthful transaction progress; 1000 is terminal only."""
        self.progress.setRange(0, 1000)
        self.progress.setValue(max(0, min(1000, int(value))))
        self.progress.setFormat("%p%")
        self.progress.setVisible(True)

    def _set_status_text(self, text: str, *, force: bool = False) -> bool:
        """Set lower-left status without stealing an initial iPod transaction.

        Device visibility is gated on the same warming set. While it is nonempty,
        every unrelated background completion/progress message is lower priority
        than the truthful `Loading iPod...` state.
        """
        if not force and self._stock_view_warming:
            self._start_ipod_loading_status()
            return False
        if (text == "Ready" and self._preparing_library
                and self._background_status_available()):
            self._show_preparation_status()
            return True
        self._stop_pulse(force=force)
        self._status_generation = getattr(self, "_status_generation", 0) + 1
        self.status_label.setText(text)
        self.status_label.setFixedWidth(
            self.status_label.fontMetrics().horizontalAdvance(text) + 8)
        self.status_label.setToolTip(text)
        self._update_device_action_state()
        return True

    def _start_pulse(self, word: str, *, keep_count: bool = False):
        if self._stock_view_warming and word != "Loading iPod":
            self._start_ipod_loading_status()
            return
        if not keep_count:
            self._set_status_count()
        if self._pulse_word == word and self._pulse_timer.isActive():
            self._update_device_action_state()
            return  # already animating this word — don't reset the dots
        self._pulse_word = word
        self._status_generation = getattr(self, "_status_generation", 0) + 1
        self._pulse_dots = 0
        self.status_label.setFixedWidth(
            self.status_label.fontMetrics().horizontalAdvance(word + "...") + 8)
        self.status_label.setText(word)
        self.status_label.setToolTip(word)
        self._pulse_timer.start()
        self._update_device_action_state()

    def _stop_pulse(self, *, force: bool = False):
        if (not force and self._stock_view_warming
                and self._pulse_word == "Loading iPod"):
            return
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()
        self._pulse_word = None
        self._set_status_count()
        self._update_device_action_state()

    def _pulse_tick(self):
        if self._pulse_word is None:
            return
        self._pulse_dots = (self._pulse_dots + 1) % 4
        self.status_label.setText((self._pulse_word or "") + "." * self._pulse_dots)

    def _flash_status(self, msg: str, seconds: int = 15):
        """Show a transient status that auto-clears to Ready (if not replaced)."""
        self._set_status_count()
        if not self._set_status_text(msg):
            return
        generation = self._status_generation
        QTimer.singleShot(
            seconds * 1000, self,
            lambda: self._set_status_text("Ready")
            if (self._status_generation == generation
                and self.status_label.text() == msg) else None,
        )

    def _on_export_done(self, reports: list):
        cancelled = self._cancelling
        self._cancelling = False
        self._exporting = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        self._set_device_progress(800)
        issues = []
        for dev, rep in reports:
            if isinstance(rep, str):
                issues.append(f"{Path(dev).name}: {rep}")
                continue
            for pr in rep.playlists:
                if pr.missing or pr.conflicts or pr.errors:
                    issues.append(f"{pr.name}: {pr.missing} missing · {pr.conflicts} conflicts")
        LOG.info("Export finished: %d job report(s), %d issue(s)", len(reports), len(issues))
        devices = [dev for dev, _rep in reports if dev and dev != "(worker)"]

        def complete():
            if cancelled:
                self._flash_status("Cancelled")
            else:
                self._set_status_text("Complete ✓")
            if self.stack.currentIndex() == 1:
                self.fs_view.setRootIndex(
                    self.fs_model.setRootPath(self.fs_model.rootPath()))
            if issues:
                QMessageBox.information(
                    self, "Export finished — with notes",
                    "Done, but note:\n\n" + "\n".join(issues[:20]))

        self._finalize_device_operations(devices, complete)


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
    if len(sys.argv) == 4 and sys.argv[1] == '--benchmark-startup':
        from .startup_check import run
        run(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
        return
    # Explicit packaged smoke check: fresh disposable state, no library/device
    # discovery, no production log. The report is the only retained output.
    if len(sys.argv) == 3 and sys.argv[1] == "--isolated-check":
        import tempfile
        import json
        report_dir = Path(sys.argv[2]).resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        QStandardPaths.setTestModeEnabled(True)
        app = make_app()
        with tempfile.TemporaryDirectory(prefix="soundbox-isolated-") as area:
            win = MainWindow(isolated_dir=area)
            app.aboutToQuit.connect(win.shutdown)
            win.show()
            def finish_check():
                win.grab().save(str(report_dir / "window.png"))
                report = {"profile": win.providers.profile,
                          "modules": list(win.providers.modules),
                          "actions": list(win.providers.actions),
                          "native": bool(win.player_bar.player.is_native),
                          "devices": win.device_tree.topLevelItemCount(),
                          "library_loaded": win.lib is not None,
                          "art_worker": win._art_worker is not None}
                (report_dir / "runtime.json").write_text(json.dumps(report, indent=2))
                app.quit()
            QTimer.singleShot(1500, finish_check)
            app.exec()
        return
    app = make_app()
    from PySide6.QtCore import QLockFile
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    state_lock = QLockFile(str(data_dir / "application.lock"))
    state_lock.setStaleLockTime(0)
    if not state_lock.tryLock(0):
        QMessageBox.warning(None, "SoundBox already running",
                            "Another SoundBox instance owns this library. Close it before opening another.")
        return
    win = MainWindow()
    app.aboutToQuit.connect(win.shutdown)  # also covers Cmd+Q / menu quit
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
