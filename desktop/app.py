"""Soundbox desktop app (PySide6) — native rekordbox → Rockbox/iPod exporter.

Flow:
  • On launch the LIVE rekordbox database is read (read-only snapshot; an XML
    export is the fallback via the REKORDBOX header menu).
  • Browse rekordbox playlists (accordion), the built-in Collection view, or
    custom SoundBox playlists (drag tracks from the table into them).
  • DRAG a playlist/folder onto an external device → it exports IMMEDIATELY.
    One EXPORTING bar aggregates every queued playlist, including drops made
    mid-export. iPods track managed-playlist drift; generic USBs export only
    explicitly selected playlists and never compute automatic sync status.
  • Curation: inline title/artist edits (⌘R / right-click), cover-art column +
    picker, artist merge + suggestion windows, per-track delete/remove with a
    restorable Graveyard, full-library .zip backup/restore in Settings.

Artists are derived and titles follow saved user rules. Only external/ejectable
volumes are shown as targets. Native desktop edits use separately accepted,
recoverable writer domains; playback and exporters preserve source audio.
See AGENTS.md and the domain documentation for the exact safety contracts.
"""

from __future__ import annotations

from copy import deepcopy

import logging
import json
import hashlib
from math import cos, isfinite, pi, sin
import os
import random
import sys
import time
from dataclasses import replace
from functools import cached_property, wraps
from pathlib import Path
from typing import TYPE_CHECKING

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


class _DeviceRefreshCompletion:
    """One terminal delivery for the accepted mount and finalization owner."""
    def __init__(self, window, device, identity, owner, callback):
        self.window=window;self.device=device;self.identity=identity
        self.owner=owner;self.callback=callback;self.completed=False

    def finish(self, error=None):
        if self.completed:return
        self.completed=True
        window=self.window
        if getattr(window,'_device_finalization_token',None) is not self.owner:return
        if window._shutting_down:
            if self.owner is not None:
                window._device_finalization_token=None
                window._device_finalization_refreshing=False
                window._exporting=False
            return
        if self.identity is None or window._device_coordinator.mounts.get(self.device)!=self.identity:
            error=RuntimeError(f'{Path(self.device).name} disconnected or remounted during finalization')
        if error is not None:window._device_finalization_error=error
        self.callback()


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
    QEvent, QFileSystemWatcher, QPoint, QPointF, QProcess, QRect, QRectF, QSize, Qt, QThread,
    QTimer, QItemSelectionModel, QItemSelection,
)
from PySide6.QtGui import (
    QKeySequence, QCursor,
    QColor, QFontDatabase, QIcon, QPainter, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QLineEdit,
    QFileDialog, QFileSystemModel, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QTableView, QTreeView, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from .choices import Menu as QMenu, exec_transient_menu
from .progress import ThemeProgressBar
from .device_progress import stock_phase_progress


_WINDOW_EVENT_TYPES = frozenset((
    QEvent.Quit, QEvent.Resize, QEvent.Show, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
    QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel,
    QEvent.ApplicationActivate, QEvent.ApplicationDeactivate, QEvent.MouseMove,
    QEvent.ShortcutOverride, QEvent.KeyRelease,
))


from soundbox import (
    browser_cache, engine, file_locations, imports, libcache,
    manifest as device_manifest, metadata, paths, rekordbox_db,
    rockbox_config, smart_playlist_order, track_analysis,
)
from soundbox.device_profile import plan_stock_ipod_export

if TYPE_CHECKING:
    from soundbox.dual_ipod import DualIpodSyncPlan
    from soundbox.stock_ipod import StockIpodSyncPlan, StockIpodDeletePlan
    from soundbox.stock_ipod_info import StockIpodSnapshot

from soundbox.library import Playlist
from . import theme
from .mount_watch import mounted_volume_snapshot
from .artpicker import ArtPickerDialog
from .task_owner import DeferredCall, TaskOwner
from .artists_browser import ArtistsBrowser, artist_rows
from .tag_controller import TagEditController
from .shared_imports import SharedImportController
from .media_conversion import MediaConversionController
from .graveyard import GraveyardController
from .removal import RemovalController
from .native_analysis import NativeAnalysisController
from .prep_controller import PrepEditController
from .metadata_controller import MetadataController
from soundbox import shared_imports, analysis_jobs
from .prep_tags import MyTagsButton
from .tag_creation import TagCreationMixin
from .tag_creation_controller import TagDefinitionController
from .prep_grid import BpmValue, grid_edit_buttons, quantize_button
from .grid_controller import GridEditController
from .wordmark import Wordmark
from .keyboard import KeyboardRouter, Keymap, event_binding
from .inline_drafts import add_recovery_actions, observe_inline_commit, preserve_inline_drafts
from .usb_device_view import UsbDevicePages
from .model import (
    COLUMNS, TrackModel, row_matches as _row_matches,
)
from .player import PlayerBar

from .device_browser import DeviceBrowserMixin
from .devices import device_facts, device_fs, human
from .settings_page import SettingsPageMixin
from .rekordbox_playlists import NativePlaylistsMixin, PlaylistEditController
from .track_info import TrackInfoMixin
from .labels import ElidedLabel
from .stock_device_view import CompositeDeviceData, StockIpodPages
from .trees import (
    ARTIST_ROLE, ARTIST_TRACK_ROLE, ARTISTS_ROLE, COLLECTION_ROLE,
    CUSTOM_ROLE, CUSTOM_FOLDER_ROLE, DEVPATH_ROLE, PATH_ROLE, RB_NODE_ROLE, RB_NODE_KIND_ROLE,
    ArtistsTree, CellEditDelegate, DeviceTree, UnifiedPlaylistTree, TrackTable, NATIVE_TREE_ROLE, PINNED_TREE_ROLE,
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
    """Keep the scrollbar below the full-width, freely resizable header."""
    from .browser_scrollbar import BrowserScrollBar
    view._edge_scrollbar = BrowserScrollBar(view, header)


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


def _artist_source_scope(library):
    if library is None:
        return None
    return str(library.source),library.source_kind,library.source_library_id


from .analysis import AnalysisMixin
from .analysis_priority import AnalysisPriorityMixin
from .playlist_folders import PlaylistFoldersMixin
from .cdj import CdjDeviceMixin
from .export_dialog import ExportSelectionMixin
from .track_menu import TrackMenuButton, TrackMenuMixin, capture_tracks, source_scope


def _edit_footer_text(controller):
    """Keep lifecycle busy state separate from foreground status presentation."""
    message = getattr(controller, 'footer_status_text', None)
    return message if isinstance(message, str) else getattr(controller, 'update_status_text', '')


class MainWindow(TagCreationMixin, NativePlaylistsMixin, PlaylistFoldersMixin, AnalysisMixin, AnalysisPriorityMixin, TrackInfoMixin, TrackMenuMixin, ExportSelectionMixin, CdjDeviceMixin, SettingsPageMixin, DeviceBrowserMixin, QMainWindow):
    """Compose the desktop and own its shared library and presentation state.

    Controllers coordinate source/device work; task owners retain their workers.
    Ordinary pages prepare eagerly, while device pages initialize on demand.
    """
    def __init__(self, providers=None, *, isolated_dir=None):
        super().__init__()
        self._isolated = isolated_dir is not None
        from soundbox.desktop_runtime import prepare_desktop_runtime
        self._desktop_runtime_errors = prepare_desktop_runtime()
        from soundbox.providers import runtime_providers
        self.providers = providers if providers is not None else runtime_providers()
        self.setWindowTitle("Soundbox")
        self.resize(1240, 800)
        data_dir = (Path(isolated_dir) if isolated_dir is not None else
                    Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)))
        self._data_file = data_dir / "library.json"  # canonical library state
        bind_provider = getattr(self.providers.connections, 'bind_root', None)
        if callable(bind_provider):
            bind_provider(data_dir)
        self._state_repository = StateRepository(self._data_file)
        self._state_maintenance = False
        self._state_publication = False
        self._state_committer = None
        self._libcache_path = data_dir / "libcache.json"  # last lib snapshot (instant paint)
        self.art_cache = ArtCache(data_dir / "art")  # cover-art cache
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
        self._stock_art_connected = False
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
        self._cache_process = None
        self.native_reader = None
        self._cache_tasks = TaskOwner(self)
        from .artwork_requests import ArtworkRequests
        self._art_requests = ArtworkRequests(self)
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
        self._stock_catalog_witness_cache = {}
        self._device_finalization_token = None
        self._device_finalization_mounts = {}
        self._device_finalization_refreshing = False
        self._eject_owner = None
        self._eject_session = None
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
        self._usb_format_worker = None
        self._usb_format_device = ''
        self._usb_format_label = ''
        self._cdj_plan = None
        self._cdj_waiting = None
        self._export_batch = None
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
        # Disposable display estimates, never detector results or source-save
        # requests. Workers validate cache/source identity off the GUI thread.
        self._waveform_bpm_library = None
        self._waveform_bpms: dict[str, dict] = {}
        self._cache_gen = 0
        self._warm_worker = None
        self._exporting = False
        self._deleting = False
        self._pending_device_operation = None
        self._accordion_guard = False
        self._prep_update_message = ''
        self.tag_controller = TagEditController(data_dir,self)
        self.tag_controller.analysis_priority = self._analysis_priority_pending
        self.tag_controller.analysis_admission = self._analysis_source_priority
        self.tag_controller.source_admitted = self._analysis_source_admitted
        self.tag_controller.idle.connect(self._analysis_lane_released)
        self.grid_controller = GridEditController(self.tag_controller, self,
                                                  cache_dir=self._track_analysis_dir,
                                                  prepare_cache=self._prepare_grid_cache,
                                                  local_result=self._analysis_result,
                                                  save_local=self._save_local_grid)
        # Apply saved chrome before building thousands of child widgets. Loading
        # state again later would also lose the repository's recovery receipt.
        self._initial_state = self._state_repository.load()
        self.settings.update(self._initial_state.get('settings') or {})
        self._prepare_library_processes()
        self._apply_theme_color(self.settings.get('theme_accent'), save=False)
        self.playlist_controller = PlaylistEditController(self)
        self.tag_definition_controller = TagDefinitionController(self)
        self.shared_import_controller = SharedImportController(self)
        self.native_analysis_controller = NativeAnalysisController(self)
        self.prep_controller = PrepEditController(self)
        self.metadata_controller = MetadataController(self)
        self.graveyard_controller = GraveyardController(self)
        self.removal_controller = RemovalController(self)
        self.media_controller = MediaConversionController(self)
        from .playlist_unification import PlaylistUnificationController
        self.unification_controller = PlaylistUnificationController(self)
        self._build_ui()
        self._artists_refresh = DeferredCall(self._prepare_artists, self)
        self.player_bar.analysis_cache_admitted = lambda tid: (
            not self.grid_controller.pending(tid) and not self.shared_import_controller.converting(tid)
            and not self.media_controller.converting(tid))
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
        self.grid_controller.local_saved.connect(self._local_grid_saved)
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
        self._settings_dirty_keys = set()
        self._settings_save_timer.timeout.connect(self._flush_settings_preferences)
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
        self._stock_art_timer = QTimer(self)
        self._stock_art_timer.setSingleShot(True)
        self._stock_art_timer.setInterval(3250)
        self._stock_art_timer.timeout.connect(self._maybe_prepare_stock_artwork)
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
        # Settings must be ready before the first visible frame. Construct its
        # controls before installing the application-wide input filter too.
        self._ensure_settings_page()
        if (not self._isolated and not QStandardPaths.isTestModeEnabled()
                and self.providers.connections is not None):
            # The window owns one connected-account refresh; its ready Settings
            # section attaches without doing credential/network work here.
            QTimer.singleShot(0, self, self._prepare_connections)
        # Direct imports keep opaque macOS bookmarks. Healthy paths need only a
        # cheap stat; a moved file resolves by persistent identity without a
        # filesystem walk. Legacy records use one strict indexed migration.
        QTimer.singleShot(0, self._repair_import_locations)
        self.table.setFocus()  # open browsing (transport keys, arrows), not typing
        # Startup geometry/style restoration emits thousands of child events.
        # Input routing begins once construction is complete; the native host's
        # own geometry filter remains active throughout initialization.
        self._application_event_filter_app = QApplication.instance()
        self._application_event_filter_app.installEventFilter(self)

    # Export caches read their indexes only when a detected target needs them.
    @cached_property
    def _connection_tasks(self):
        return TaskOwner(self)

    def _prepare_connections(self):
        factory = self.providers.connections
        if (self._isolated or QStandardPaths.isTestModeEnabled() or factory is None
                or self._shutting_down or getattr(self, '_connection_initial_worker', None) is not None
                or getattr(self, '_connection_initial_complete', False)):
            return
        from .workers import OneShotTask
        root = self._data_file.parent

        def refresh():
            service = factory.create(root)
            initial = getattr(service, 'refresh_initial', None)
            if callable(initial):
                initial(worker.cancellation, lambda _message: None)
            elif service.status().get('connected'):
                service.refresh(worker.cancellation, lambda _message: None)

        def retired(captured):
            if getattr(self, '_connection_initial_worker', None) is not captured:
                return
            self._connection_initial_worker = None
            self._connection_initial_complete = True
            self._connection_initial_result = (
                None if captured.cancellation.is_set() else captured.result)
            section = getattr(self, '_soundcloud_section', None)
            if section is not None:
                section.finish_startup(captured, result=self._connection_initial_result)
            if isinstance(captured.result, Exception) and not captured.cancellation.is_set():
                LOG.info('Automatic connection refresh did not complete; existing cached artwork remains usable')

        worker = OneShotTask(refresh)
        self._connection_initial_worker = worker
        self._connection_initial_complete = False
        section = getattr(self, '_soundcloud_section', None)
        if section is not None:
            section.attach_startup(worker)
        self._connection_tasks.adopt(worker, retired)
        worker.start()

    @cached_property
    def stock_ipod_pages(self):
        pages = StockIpodPages()
        pages.stack.currentChanged.connect(self._remember_stock_device_view)
        pages.delete_playlists_requested.connect(self._begin_stock_playlist_delete)
        pages.playlist_order_requested.connect(self._apply_composite_playlist_order)
        pages.primary_action_requested.connect(self._device_primary_action)
        self._replace_device_page(1, pages)
        return pages

    @cached_property
    def stock_catalog_witness_store(self):
        from soundbox.stock_ipod_catalog_state import StockCatalogWitnessStore
        return StockCatalogWitnessStore(self._data_file.parent / 'stock_catalog_state')

    @cached_property
    def usb_device_pages(self):
        pages = UsbDevicePages()
        pages.settings_requested.connect(self._apply_usb_settings)
        pages.global_requested.connect(self._promote_usb_settings)
        pages.global_load_requested.connect(self._load_global_usb_settings)
        pages.song_requested.connect(self._load_usb_song)
        saved_views = self.settings.get('device_views', {})
        pages._states = deepcopy(saved_views.get('usb', {})) if isinstance(saved_views, dict) else {}
        pages.view_changed.connect(self._save_device_views)
        pages.row_enricher = self._enrich_usb_row
        pages.playlist_tree.header().sectionResized.connect(self._on_usb_column_resized)
        _cap_browser_scrollbar(pages.playlist_tree, pages.playlist_tree.header())
        self._replace_device_page(2, pages)
        # Column fitting is passive: it sees only already-created views.
        self.__dict__['usb_device_pages'] = pages
        self._sync_artist_columns()
        return pages

    def _replace_device_page(self, index, page):
        placeholder = self.device_content_stack.widget(index)
        current = self.device_content_stack.currentWidget() is placeholder
        self.device_content_stack.insertWidget(index, page)
        self.device_content_stack.removeWidget(placeholder)
        placeholder.deleteLater()
        if current:
            self.device_content_stack.setCurrentWidget(page)

    @cached_property
    def stock_art_cache(self):
        from soundbox.stock_artwork import StockArtworkCache
        return StockArtworkCache(self._data_file.parent / "stock_artwork")

    @cached_property
    def rockbox_media_cache(self):
        from soundbox.rockbox_media import RockboxMediaCache
        return RockboxMediaCache(self._data_file.parent / "rockbox_media")

    @cached_property
    def rockbox_art_cache(self):
        from soundbox.rockbox_media import RockboxArtworkCache
        return RockboxArtworkCache(self._data_file.parent / "rockbox_artwork")

    @cached_property
    def stock_ipod_shadow_store(self):
        from soundbox.stock_ipod_shadow import StockIpodShadowStore
        return StockIpodShadowStore(self._data_file.parent / "stock_ipod_state")

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
        keyboard_page = getattr(self, '_keyboard_page', None)
        if keyboard_page is not None:
            keyboard_page.set_keymap(Keymap(self._keyboard_router.keymap.dump()))
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
        known_primary = bool(cached is not None and cached.source_kind == 'rekordbox-db'
            and Path(cached.source).resolve() == self._rekordbox_database_path().resolve())
        if cached is not None and self._accept_source_curation(cached, bootstrap=known_primary):
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
            self.open_library(self._last_xml, initial=True)
        self._sync_rekordbox_connection()

    def _save_state(self, *, invalidate_views=True) -> bool:
        """Durably publish canonical SoundBox state before acknowledging edits."""
        timer = getattr(self, "_settings_save_timer", None)
        if timer is not None:
            timer.stop()
        if invalidate_views:
            self._sync_playlists_cache = None
        try:
            if self._state_publication:
                committer=getattr(self,'_state_committer',None)
                if getattr(self,'_settings_dirty_keys',None):self._flush_settings_preferences()
                if committer is not None and committer.pending:committer.defer_save()
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
            getattr(self,'_settings_dirty_keys',set()).clear()
            committer=getattr(self,'_state_committer',None)
            if committer is not None:committer.preferences_saved()
            self.setWindowTitle("Soundbox")
            controller=getattr(self,'unification_controller',None)
            if controller:controller.schedule()
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

    def _commit_metadata_state(self,replacements,removals,committed,failed):
        """Admit a small background delta; only its callback means durable save."""
        from .state_commit import StateCommitter
        if self._state_committer is None:self._state_committer=StateCommitter(self)
        try:return self._state_committer.submit(replacements,removals,committed,failed)
        except (OSError,ValueError,TypeError) as exc:
            failed(exc);return True

    def _commit_tag_state(self,replacements,removals,committed,failed,*,current,expected_queues=None):
        """Admit a tag ACK; only the ordered durable callback publishes it."""
        from .state_commit import StateCommitter
        if self._state_committer is None:self._state_committer=StateCommitter(self)
        try:return self._state_committer.submit(replacements,removals,committed,failed,current=current,
                                                expected_queues=expected_queues)
        except (OSError,ValueError,TypeError) as exc:
            failed(exc);return True

    # ---------- settings ----------

    def _build_artist_registry(self, *, invalidate_rows=True):
        """Canonical-artist map, rebuilt each load: dedups spellings (DENNETT/
        dennett -> one artist, most-tracks spelling wins, collabs kept separate),
        rescues Unknown tracks from a known artist in the title, and gates the
        reversed 'TITLE - ARTIST' flip on TRUSTED names — rekordbox tags plus the
        user's own overrides, so every manual fix teaches the deriver."""
        if self.lib is None:
            return
        from .prepared_browser import registry_inputs
        bases,trusted,self._artist_registry_prepared=registry_inputs(
            self.lib,self.settings.get('source_truth_artist_origins',{}),self.overrides,
            getattr(self,'_artist_registry_prepared',None))
        self._artist_registry_changed_ids=self._artist_registry_prepared['changed_ids']
        previous = self.lib.artist_registry or getattr(self,'_display_registry',None)
        from collections import Counter
        # The registry votes by spelling/count, never source iteration order.
        # An acknowledged override can move the same trusted vote into its
        # origin row without changing any canonical/trusted lookup semantics.
        inputs=(frozenset(Counter(bases).items()),frozenset(Counter(trusted).items()),
                frozenset(self.aliases.items()))
        if inputs==getattr(self,'_artist_registry_inputs',None) and previous is not None:
            self.lib.artist_registry=previous
            if invalidate_rows:self._invalidate_rows()
            return
        registry = metadata.build_registry(bases, trusted, self.aliases)
        self.lib.artist_registry = previous if metadata.registry_context_equal(previous,registry) else registry
        unaliased = (metadata.build_registry(bases, trusted)
            if self.aliases and self.lib.source_kind == 'rekordbox-db' else self.lib.artist_registry)
        previous_unaliased = getattr(self,'_metadata_unaliased_registry',None)
        self._metadata_unaliased_registry = (previous_unaliased
            if metadata.registry_context_equal(previous_unaliased,unaliased) else unaliased)
        self._artist_registry_inputs=inputs
        if invalidate_rows:self._invalidate_rows()  # canonical spellings may have changed

    def _default_to_collection(self, *, force=False):
        """On open (or any load with nothing selected), land on the Collection
        view instead of an empty table."""
        if self.stack.currentIndex() == 1 and not force:
            return  # An open device is already a complete navigation choice.
        if (self.sb_tree.topLevelItemCount() and (force or
                self.tree.currentItem() is None and self.sb_tree.currentItem() is None)):
            if force:self._select_only(self.sb_tree)
            self.sb_tree.setCurrentItem(self.sb_tree.topLevelItem(0))

    def _annotate_art(self, rows: list[dict]) -> list[dict]:
        """Stamp each row with cover/avatar/default/blank status for Art."""
        if not rows:
            return rows
        kinds = self.art_cache.status_kinds(r.get('id', '') for r in rows)
        for r in rows:
            r["art"] = kinds[r.get('id', '')]
        return rows

    def _refresh_model_art(self):
        ids = {row.get('id', '') for row in self.model._all_rows}
        ids.update(row['id'] for rows in self._artist_children.values() for row in rows)
        kinds = self.art_cache.status_kinds(ids)
        self.model.refresh_art(lambda tid: kinds.get(tid, ''))
        changed = self._artists_browser.refresh_art(kinds)
        self._artist_children = self._artists_browser.children
        for track_id, kind in changed.items():
            self._artists_rows[track_id]['art'] = kind

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
        result['bpm_estimated'] = False
        from soundbox.local_grid import display_fields
        detected = display_fields(self._analysis_result(t))
        if detected:
            if (not t.bpm or (t.imported and detected.get('grid_draft'))) and detected.get('bpm'):
                result['bpm'] = f"{detected['bpm']:.2f}"
            if not t.key and detected.get('key'):
                result['key'] = detected['key']
        result.update(self.settings.get('track_info',{}).get(t.track_id,{}))
        media=getattr(self,'media_controller',None)
        actual=media.facts(t.track_id) if media else None
        if actual and str(t.path)==actual['path']:
            facts=actual['facts'];result['size']=actual['epoch'][2]
            result['sampleRate']=str(facts['sample_rate'])
            if facts.get('bits'):result['bitDepth']=str(facts['bits'])
        if t.imported and detected and detected.get('grid_draft'):
            result['bpm'] = f"{detected['bpm']:.2f}"
        prep=getattr(self,'prep_controller',None)
        if prep and prep.job(t.track_id):
            view=prep.grid_view(t.track_id)
            if view:result['bpm']=f'{view[1]:.2f}'
        if not result.get('bpm'):
            estimate = self._waveform_bpm(t)
            if estimate is not None:
                result['bpm'] = f'{estimate:.2f}'
                result['bpm_estimated'] = True
        if self.lib and t.source_tag_ids:
            result['myTags'] = ', '.join(tag.name for tag in self.lib.source_tags if tag.tag_id in t.source_tag_ids)
        metadata_controller=getattr(self,'metadata_controller',None)
        if metadata_controller:
            result.update(metadata_controller.display_fields(t))
        return result

    def _waveform_bpm_binding(self, track):
        lib = self.lib
        return (str(lib.source), lib.source_kind, lib.source_library_id,
                str(track.path), track.size, track.native_id, track.source_uuid)

    def _waveform_bpm(self, track):
        if self._waveform_bpm_library is not self.lib:
            self._waveform_bpms.clear()
            self._waveform_bpm_library = self.lib
        value = self._waveform_bpms.get(track.track_id)
        if (value and not track.source_analysis_path and not track.bpm
                and value['binding'] == self._waveform_bpm_binding(track)):
            return value['bpm']
        return None

    def _on_waveform_bpm_ready(self, worker, value):
        """Project a worker-validated estimate without touching musical state."""
        if (self._shutting_down or worker not in (self._wf_fill_worker, getattr(self, '_analysis_worker', None))
                or self.lib is None or worker.lib is not self.lib):
            return
        track = self.lib.tracks_by_id.get(value.get('track_id'))
        if track is None or track.track_id in getattr(self.lib, 'hidden_ids', ()):
            return
        binding = (value.get('source'), value.get('source_kind'), value.get('library_id'),
                   value.get('path'), value.get('track_size'), value.get('native_id'), value.get('uuid'))
        if binding != self._waveform_bpm_binding(track):
            return
        self._waveform_bpm(track)  # discard estimates from a previous Library
        bpm = value.get('bpm')
        epoch = value.get('epoch')
        if (bpm is not None and (not isinstance(bpm, (int, float))
                or isinstance(bpm, bool) or not isfinite(bpm) or not 0 < bpm <= 1000
                or value.get('provenance') != 'soundbox-zero-anchor'
                or not isinstance(epoch, list) or len(epoch) != 5
                or any(type(part) is not int for part in epoch)
                or epoch[2] != value.get('size') or epoch[2] <= 0
                or track.size and track.size != epoch[2])):
            return
        if bpm is None:
            self._waveform_bpms.pop(track.track_id, None)
        else:
            self._waveform_bpms[track.track_id] = dict(binding=binding, bpm=float(bpm))
        after = self._row_for(track)
        prepared = getattr(self, '_artists_rows', {}).get(track.track_id)
        if prepared is not None and (prepared.get('bpm'), bool(prepared.get('bpm_estimated'))) != (
                after.get('bpm'), after['bpm_estimated']):
            self._schedule_artists_refresh([track.track_id])
        displayed = next((row for row in self.model._all_rows if row.get('id') == track.track_id), None)
        if displayed is not None and (displayed.get('bpm'), bool(displayed.get('bpm_estimated'))) != (
                after.get('bpm'), after['bpm_estimated']):
            self._refresh_analysis_row(track.track_id)

    def _rows_for(self, tracks) -> list[dict]:
        return [self._row_for(t) for t in sorted(tracks,
                key=lambda t:(-self._landed_time(t),t.name.casefold()))]

    def _playlist_rows(self, display_path: str) -> list[dict]:
        """Regular lists keep authored occurrences; smart lists use recency."""
        pl = self._native_view_playlist(display_path)
        if pl is None:
            return []
        tracks = self.lib.resolve(pl)
        node = next((row for row in self._native_presentation().source_playlist_catalog.get('nodes', [])
                     if row['ID'] == pl.pid), None)
        rows = (self._rows_for(tracks) if node and node['Attribute'] == 4
                else [self._row_for(track) for track in tracks])
        rows = self._native_membership_rows(pl, rows)
        return smart_playlist_order.order_rows(rows,self.lib,pl.pid,
                                               self.settings.get(smart_playlist_order.KEY))

    def _invalidate_rows(self, track_ids=None) -> None:
        """Invalidate affected rows, or all rows for a global metadata policy.

        A generation change rejects old worker results; the next warm computes
        only missing rows and preserves unaffected cached values.
        """
        self._browser_cache_key = None  # never checkpoint rows under stale inputs
        if track_ids is None:
            self._row_cache.clear()
        else:
            track_ids = tuple(track_ids)
            for track_id in track_ids:
                self._row_cache.pop(track_id, None)
        self._sync_playlists_cache = None
        self._cache_gen += 1
        self._schedule_artists_refresh(track_ids)
        if self.lib is not None:
            self._warm_timer.start()  # debounced → one rebuild per burst

    def _do_warm(self) -> None:
        """Compute missing rows off-thread, single-flight and generation guarded."""
        if self._shutting_down or getattr(self, "_state_maintenance", False):
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
        if self._shutting_down or self._state_maintenance:
            return
        if isinstance(res, Exception):
            LOG.warning("Row-cache warm failed: %s", res)
            return
        if gen != self._cache_gen:
            self._warm_timer.start()  # inputs changed mid-warm — rebuild
            return
        self._row_cache.update(res)
        LOG.info("Row cache warmed: %d rows", len(res))

    @preserve_inline_drafts
    def _reload_current_view(self):
        """Refresh rows without stealing the user's selection or scroll seat."""
        if self.stack.currentIndex() == 1:
            # The retained desktop view key is not the active device page.
            # Its prepared data is refreshed by library publication; an ordinary
            # browser click will render it without touching device controls now.
            return
        seat=self._browser_seat()
        previous=self.model._rows
        self._render_current_view()
        if self.model._rows is not previous:self._restore_browser_seat(seat)

    def _set_refreshed_rows(self,rows):
        from .tag_selection_view import rows as tag_selection_rows
        projected=tag_selection_rows(self,rows)
        seat=self._browser_seat() if projected is not rows else None
        rows=projected
        clean=lambda row:{k:v for k,v in row.items() if k!='_hay'}
        if len(rows)==len(self.model._all_rows) and all(clean(a)==clean(b) for a,b in zip(rows,self.model._all_rows)):
            return
        self.model.set_rows(rows)
        if seat is not None:self._restore_browser_seat(seat)

    def _browser_seat(self):
        rows=self.model._rows;selection=self.table.selectionModel()
        index=self.table.currentIndex();top=self.table.indexAt(QPoint(0,0))
        identity=self._native_row_identity
        return dict(current=identity(rows[index.row()]) if index.isValid() else None,
                    column=index.column() if index.isValid() else 0,
                    selected={identity(rows[i.row()]) for i in selection.selectedRows()},
                    top=identity(rows[top.row()]) if top.isValid() else None,
                    top_row=top.row(),scroll=self.table.verticalScrollBar().value(),
                    horizontal=self.table.horizontalScrollBar().value())

    def _restore_browser_seat(self,seat):
        self._selection_revision=getattr(self,'_selection_revision',0)+1
        by_id={self._native_row_identity(row):i for i,row in enumerate(self.model._rows)}
        selection=self.table.selectionModel();blocked=selection.blockSignals(True)
        try:
            selected=QItemSelection()
            for identity in seat['selected']:
                if identity in by_id:
                    i=by_id[identity]
                    selected.select(self.model.index(i,0),self.model.index(i,self.model.columnCount()-1))
            selection.select(selected,QItemSelectionModel.ClearAndSelect)
            if seat['current'] in by_id:
                selection.setCurrentIndex(self.model.index(by_id[seat['current']],seat['column']),QItemSelectionModel.NoUpdate)
            self.table.doItemsLayout()
            scroll=seat['scroll']
            if seat['top'] in by_id:
                delta=by_id[seat['top']]-seat['top_row']
                scroll+=delta if self.table.verticalScrollMode()==QAbstractItemView.ScrollPerItem else delta*self.table.verticalHeader().defaultSectionSize()
            self.table.verticalScrollBar().setValue(scroll)
            self.table.horizontalScrollBar().setValue(seat['horizontal'])
        finally:selection.blockSignals(blocked)

    def _render_current_view(self):
        """Re-render whatever playlist is showing so a settings change takes
        effect immediately. Updates the table rows only — does NOT switch the
        visible page (so toggling on the Settings page keeps you there)."""
        if self.lib is None:
            return
        cur = self.tree.currentItem()
        if cur is not None and cur.data(0, PATH_ROLE):
            self._set_refreshed_rows(self._annotate_art(
                self._playlist_rows(cur.data(0, PATH_ROLE))))
            return
        cur = self.sb_tree.currentItem()
        if cur is not None and cur.data(0, COLLECTION_ROLE):
            self._show_collection()
            return
        if cur is not None and cur.data(0, ARTISTS_ROLE):
            self._show_artists(preserve=True)
            return
        if cur is not None and cur.data(0, CUSTOM_ROLE):
            tracks = [t for i in self.custom.get(cur.data(0, CUSTOM_ROLE), [])
                      if i not in self.hidden
                      and (t := self.lib.tracks_by_id.get(i))]
            self._set_refreshed_rows(self._annotate_art([self._row_for(track) for track in tracks]))
            return
        # A folder may own keyboard/mouse navigation while the browser keeps
        # showing its previous playlist. Refresh that actual active view.
        key = getattr(self, '_view_key', '') or ''
        if key.startswith('rb:'):
            self._set_refreshed_rows(self._annotate_art(self._playlist_rows(key[3:])))
        elif key == 'sb:Collection':self._show_collection()
        elif key == 'sb:Artists':self._show_artists(preserve=True)
        elif key.startswith('sb:') and key[3:] in self.custom:self._show_custom(key[3:])

    def showEvent(self, event):
        super().showEvent(event)
        self._browser_columns_shown = True
        self._fit_browser_columns(fill=not self.settings.get('columns_state'))

    # ---------- player ----------
    def eventFilter(self, obj, ev):
        event_type = ev.type()
        # This application-wide filter also sees every style/layout event while
        # constructing a page. Only input, browser geometry and quit belong here.
        if event_type not in _WINDOW_EVENT_TYPES:
            return super().eventFilter(obj, ev)
        if event_type in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                          QEvent.Wheel, QEvent.ApplicationDeactivate, QEvent.ApplicationActivate):
            drafts = vars(self).get('_inline_drafts')
            if drafts is not None:
                drafts.gesture += 1
                if event_type == QEvent.ApplicationDeactivate:drafts.inactive = True
                elif event_type == QEvent.ApplicationActivate:drafts.inactive = False
        tags_button = getattr(self, 'my_tags', None)
        if tags_button is not None:
            if event_type in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                tags_button.dismiss_outside(ev.globalPosition().toPoint())
            elif event_type == QEvent.ApplicationDeactivate:
                tags_button.popup.close()
        if obj is QApplication.instance() and event_type == QEvent.Quit:
            if (getattr(self,'_shutdown_waiting',False) or getattr(self,'_state_maintenance',False)
                    or self._defer_quit_for_saves(auto_close=True)):
                ev.ignore()
                return True
        usb_pages = vars(self).get('usb_device_pages')
        if (event_type in (QEvent.Resize, QEvent.Show)
                and getattr(self, '_browser_columns_shown', False)
                and (obj is self.table.viewport()
                     or obj is self.artists_view.viewport()
                     or (usb_pages is not None and obj is usb_pages.playlist_tree.viewport()))
                and (obj.isVisible() or event_type == QEvent.Show)):
            # Hidden pages can retain their old geometry until Show. Refit
            # before the browser is painted after returning from Settings.
            width = ev.size().width() if event_type == QEvent.Resize else obj.width()
            self._fit_browser_columns(fill=True, available=width)
        if (getattr(self, "now_playing", None) is obj
                and event_type == QEvent.MouseButtonRelease
                and ev.button() == Qt.LeftButton):
            self._focus_now_playing()
            return True

        # Configurable shortcuts are deck controls only from browsing surfaces. Editors,
        # search, settings trees, dialogs, and the device browser keep their
        # native text/toggle behavior. Text editing retains its standard chords.
        if event_type in (
                QEvent.KeyPress,QEvent.MouseButtonPress,QEvent.MouseButtonRelease,
                QEvent.Wheel, QEvent.Resize, QEvent.ApplicationActivate,
                QEvent.ApplicationDeactivate):
            self._defer_library_prep(
                1.0 if event_type in (
                    QEvent.ApplicationActivate,
                    QEvent.ApplicationDeactivate) else 0.75)
        elif (event_type == QEvent.MouseMove
              and getattr(ev, "buttons", lambda: Qt.NoButton)()
              & Qt.LeftButton):
            self._defer_library_prep()

        if event_type == QEvent.ApplicationActivate:
            self.player_bar.resynchronize_transport()
            self.grid_controller.set_current(self.player_bar.current_track(), force=True)
            return super().eventFilter(obj, ev)
        if event_type == QEvent.ApplicationDeactivate:
            self._keyboard_router.release_all()
            return super().eventFilter(obj, ev)
        if event_type not in (
                QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            return super().eventFilter(obj, ev)
        focus = QApplication.focusWidget()
        grave = getattr(self, '_grave', None)
        if grave is not None and focus is grave and ev.matches(QKeySequence.SelectAll):
            if event_type==QEvent.KeyPress:grave.selectAll()
            ev.accept();return True
        browsing = self._space_should_play(focus)
        if grave is not None and focus is grave:
            action = self._keyboard_router.keymap.action(event_binding(ev)) or ''
            browsing = (action.startswith(('transport.', 'cues.', 'grid.', 'wave.', 'autoplay.'))
                        or action in ('browser.load','browser.search', 'browser.select_all', 'app.settings'))
        if usb_pages is not None and focus is usb_pages.playlist_tree:
            action = self._keyboard_router.keymap.action(event_binding(ev)) or ''
            browsing = (action.startswith(('transport.', 'cues.', 'grid.', 'wave.', 'autoplay.'))
                        or action in ('browser.load', 'browser.search', 'app.settings'))
        if (self._keyboard_router.keymap.action(event_binding(ev)) == "app.settings"
                and not isinstance(focus, QLineEdit)):
            browsing = True
        browsing = (browsing and QApplication.activeModalWidget() is None
                    and QApplication.activePopupWidget() is None
                    and not (tags_button is not None and tags_button.popup.isVisible())
                    and not self._shutting_down and not self._state_maintenance)
        if (browsing and (focus in (self.tree,self.sb_tree) or obj in (self.tree,self.sb_tree))
                and ev.key() in (Qt.Key_Up,Qt.Key_Down)
                and ev.modifiers() in (Qt.NoModifier,Qt.ShiftModifier)
                and self.stack.currentIndex()==0):
            # Sidebar clicks choose a playlist; arrows browse its songs.
            if event_type==QEvent.KeyPress:
                self.table.setFocus(Qt.ShortcutFocusReason)
                self.table.keyPressEvent(ev)
            ev.accept();return True
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
        if action == 'browser.select_all':
            self._select_all_browser_tracks()
            return None
        pb = self.player_bar
        if action in ('transport.back','transport.forward'):
            return pb.begin_beat_jump(-1 if action=='transport.back' else 1)
        if action in ('grid.left', 'grid.right'):
            # One timer per held action, shared by shortcut aliases. Capture the
            # song at key-down: a held key must never start editing the next song.
            operation = action.split('.')[1]
            track_id = pb.current_track()
            timer = QTimer(self)
            timer.setSingleShot(True)
            def release_grid():
                timer.stop()
                timer.deleteLater()
            def repeat_grid():
                focus = QApplication.focusWidget()
                grave = getattr(self, '_grave', None)
                permitted = self._space_should_play(focus) or (grave is not None and focus is grave)
                if (not permitted or QApplication.activeModalWidget() is not None
                        or QApplication.activePopupWidget() is not None
                        or not self._edit_grid(operation, track_id=track_id)):
                    timer.stop()
                    return
                timer.start(80)
            timer.timeout.connect(repeat_grid)
            if self._edit_grid(operation, track_id=track_id):
                timer.start(300)
            return release_grid
        grave = getattr(self, '_grave', None)
        if action == 'browser.load' and grave is not None and QApplication.focusWidget() is grave:
            self._grave_load(grave.currentItem())
            return None
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
            "cues.set": self._set_next_cue,
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

    def _select_all_browser_tracks(self):
        grave = getattr(self, '_grave', None)
        if grave is not None and QApplication.focusWidget() is grave:
            grave.selectAll()
        elif self.stack.currentWidget() is self.artists_view:
            self.artists_view.selectAll()
        elif self.stack.currentIndex() == 0:
            self.table.selectAll()
            self._log_browser_selection('select-all')

    def _browser_selection_diagnostic(self):
        selected=self._selected_ids()
        tracks=self.lib.tracks_by_id if self.lib else {}
        bindings=tuple(sorted({(track.native_id,track.source_uuid or '') for identity in selected
                              if (track:=tracks.get(identity)) is not None}))
        return dict(view=str(getattr(self,'_view_key','') or ''),
                    full=len(self.model._all_rows),visible=len(self.model._rows),selected=len(selected),
                    filtered=bool(self.model._filter),bindings=bindings)

    def _log_browser_selection(self, action, *, before=None, tag_id=None, present=None):
        current=self._browser_selection_diagnostic()
        bindings=current['bindings']
        fingerprint=lambda values:hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()
        lost=tuple(sorted(set(before['bindings'])-set(bindings))) if before is not None else ()
        LOG.info('Browser selection: action=%s view=%s full=%d visible=%d selected=%d '
                 'native_targets=%d filtered=%s selection_sha256=%s tag_id=%s present=%s '
                 'before_selected=%s lost=%d lost_sha256=%s lost_ids=%s',
                 action,json.dumps(current['view'][:256]),current['full'],current['visible'],
                 current['selected'],len(bindings),current['filtered'],fingerprint(bindings),
                 tag_id,present,before['selected'] if before is not None else 'unknown',
                 len(lost),fingerprint(lost),json.dumps([identity for identity,_uuid in lost[:8]]))

    def _set_next_cue(self):
        panel=self.player_bar.cue_panel
        if not panel._loaded:
            self._flash_status('Load a track before setting a cue',5);return False
        kind='hot' if panel.pages.currentIndex()==0 else 'memory'
        prep=getattr(self,'prep_controller',None)
        staged=prep.requested_cues(self.player_bar.current_track()) if prep else None
        occupied=({cue['slot'] for cue in staged if cue['kind']==kind} if staged is not None
                  else {i for i,cue in enumerate(panel._cues[kind]) if cue is not None})
        slot=next((i for i in range(8) if i not in occupied),None)
        if slot is None:
            self._flash_status('All '+('Hot Cue' if kind=='hot' else 'Memory Cue')+' slots are full',5);return False
        return self._edit_cue(kind,slot,'add')

    def _defer_library_prep(self, seconds: float = 0.75):
        self._background_library_not_before=time.monotonic()+seconds
        worker = getattr(self, "_wf_fill_worker", None)
        if worker is not None:
            worker.defer_for_interaction(seconds)

    def _space_should_play(self, w) -> bool:
        """Focus policy for the Space shortcut (kept separate so it's directly
        testable — offscreen focus doesn't propagate reliably)."""
        return (w is None or w in (self,self.table,self.tree,self.sb_tree,
                                  self.device_tree,self.artists_view)
                or (not isinstance(w,QLineEdit) and self.player_bar.isAncestorOf(w)))

    def _remember_current(self, idx, _prev=None):
        """Sticky memory of the selected track id — survives filters/sorts that
        temporarily hide or reshuffle it."""
        if idx.isValid() and idx.row() < len(self.model._rows):
            tid = self.model._rows[idx.row()].get("id")
            if tid:
                self._keep_tid = tid
                self._selection_revision=getattr(self,'_selection_revision',0)+1

    def _schedule_reseat_selection(self,*_args):
        revision=getattr(self,'_selection_revision',0)
        QTimer.singleShot(0,self,lambda:self._reseat_selection()
            if revision==getattr(self,'_selection_revision',0) else None)

    def _reseat_selection(self):
        """Put the remembered track back under the cursor after the model
        reshuffled (sort toggled, search changed) — no more selection oblivion."""
        keep = getattr(self, "_keep_tid", None)
        if not keep:
            return
        current=self.table.currentIndex()
        if (current.isValid() and current.row()<len(self.model._rows)
                and self.model._rows[current.row()].get('id')==keep):
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
        seat=self._browser_seat()
        previous=self.model._rows
        self.model.set_filter(text)
        if self.model._rows is not previous:self._restore_browser_seat(seat)
        self._reseat_selection()
        self._filter_artists()
        self._filter_graveyard()

    def _search_terms(self) -> list[str]:
        return self.search.text().strip().lower().split()

    def _filter_artists(self) -> None:
        """Search the prepared Artists projection using the table's row policy."""
        self._artists_browser.filter(self._search_terms())
        self._update_player_navigation()

    def _apply_artist_child_filter(self, item) -> None:
        self._artists_browser.filter_children(item, self._search_terms())

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
            capture_autoplay: bool = True, audition: bool = False) -> bool:
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
        self._cue_audition_track = t.track_id if audition else None
        self.player_bar.play_track(
            t.track_id, t.path, title=label, bpm=t.bpm,
            analysis_path=getattr(t, "source_analysis_path", ""),
            autoplay=autoplay,
            detected_analysis=self._analysis_result(t),
            cues=(self.prep_controller.cue_rows(t.track_id)
                  if self.prep_controller.handles(t.track_id) else
                  self.tag_controller.session_cues(t.track_id)
                  if self.tag_controller.lib is self.lib else getattr(t, "source_cues", None)))
        self._sync_cue_editing()
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

        checked=set()
        renewed=False
        while True:
            while self._autoplay_random_queue:
                index = self._autoplay_random_queue.pop()
                track_id = self._autoplay_order[index]
                if (track_id not in self._autoplay_random_seen
                        and track_id not in checked):
                    checked.add(track_id)
                    if self._autoplay_playable(index):return index

            # Build the bag from scalar identities, then check only candidates
            # actually drawn. Refilling must not stat the entire music library.
            unique: dict[str, int] = {}
            for index, track_id in enumerate(self._autoplay_order):
                unique.setdefault(track_id,index)
            candidates = [
                index for track_id, index in unique.items()
                if track_id not in self._autoplay_random_seen and track_id not in checked
            ]
            if not candidates:
                if renewed:return None
                renewed=True
                current = self._autoplay_order[self._autoplay_index]
                self._autoplay_random_seen = {current}
                candidates = [
                    index for track_id, index in unique.items()
                    if track_id != current and track_id not in checked
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
                top.setExpanded(True)
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

    @observe_inline_commit
    def _on_cell_edited(self, field: str, track_id: str, value: str):
        if not track_id or field not in ('title','artist'):
            return False
        controller=getattr(self,'metadata_controller',None)
        if controller is not None and self.lib and self.lib.source_kind=='rekordbox-db':
            accepted=controller.enqueue(track_id,{field:value or ''})
            if not accepted:
                self._invalidate_rows((track_id,))
                QTimer.singleShot(0,self,self._reload_current_view)
            elif self.art_cache.status_kind(track_id)!='cover':self._art_fetch_one(track_id)
            return accepted
        store = self.overrides if field == "artist" else self.titles
        missing=object();previous=store.get(track_id,missing)
        v = (value or "").strip()
        if v:
            store[track_id] = v
        else:
            store.pop(track_id, None)  # blank reverts to auto/original
        if not self._save_state():
            if previous is missing:store.pop(track_id,None)
            else:store[track_id]=previous
            self._invalidate_rows((track_id,))
            # Inline editors update their row before delivering this signal.
            # Restore it after the editor's callback has finished, preserving
            # selection/filter/sort through the usual browser-seat refresh.
            QTimer.singleShot(0,self,self._reload_current_view)
            return False
        if field == "artist" and self.lib is not None:
            self._build_artist_registry()  # new override immediately feeds trust/canon
        else:
            self._invalidate_rows((track_id,))  # title override changed this row's computed title
        # Fixing the name should re-hunt for art unless a REAL cover is already
        # confirmed — a miss OR an avatar both deserve a retry with the fix.
        if self.lib and track_id in self.lib.tracks_by_id \
                and self.art_cache.status_kind(track_id) != "cover":
            self._art_fetch_one(track_id)
        return True

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

        self.body_stack.addWidget(self._browser_body())

        # Reserve its slot; all Settings controls are prepared during startup.
        self.body_stack.addWidget(QWidget())
        outer.addWidget(self.body_stack, 1)

        outer.addWidget(self._bottom_bar())

    def _browser_body(self):
        """Resize the shared playlist/device column without collapsing either pane."""
        split = QSplitter(Qt.Horizontal)
        split.setObjectName('BrowserSplit')
        split.setChildrenCollapsible(False)
        # Qt keeps an overlapping mouse grab area around a one-pixel handle.
        split.setHandleWidth(1)
        split.addWidget(self._sidebar())
        content = self._main_stack()
        content.setMinimumWidth(320)
        split.addWidget(content)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        self._browser_split = split
        split.splitterMoved.connect(self._save_sidebar_width)
        return split

    def _top_bar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        # Whole-track overview + beat ruler + zoomed waveform stay in separate
        # lanes. The taller scrolling lane matches the rekordbox reference;
        # the cue column and transport share the available layout height.
        # Let the layout derive its minimum so short windows cannot compress
        # the waveform underneath the prep controls.
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
        self.now_playing.setTextFormat(Qt.PlainText)
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
        self.player_bar.grid_ready.connect(lambda: self._grid_changed(self.player_bar.current_track()))
        self.player_bar.track_ready.connect(self.media_controller.discover)
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
        self.my_tags = MyTagsButton(self)
        self.my_tags.setFixedHeight(28)
        self.my_tags.setFixedWidth(self.player_bar.cue_panel.width() - 6)
        self.search.setFixedWidth(self.my_tags.width())
        self.my_tags.popup.aboutToShow.connect(self._probe_tags)
        self.my_tags.assignment_requested.connect(self._assign_tag)
        from .tag_order import save_order
        self.my_tags.order_requested.connect(lambda order, binding: save_order(self, order, binding))
        self.my_tags.creation_requested.connect(self._new_tag)
        self.my_tags.deletion_requested.connect(self._delete_tag)
        self.my_tags.rename_requested.connect(self._rename_tag)
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
            self._schedule_settings_save('quantize')

    def _sync_grid(self):
        if not hasattr(self, 'grid_edit_buttons'):
            return
        pb = self.player_bar
        blocker = ''
        if self._state_maintenance or self._shutting_down:
            blocker = 'Library operation in progress'
        elif pb._pending_autoplay or pb._native_error or not pb.cue_panel._loaded:
            blocker = 'Wait for the track to finish loading'
        elif ((pb._analysis_parts.get('provenance', {}).get('grid') == 'soundbox-manual'
               or pb._analysis_parts.get('provenance', {}).get('user_data') == 'soundbox')
                and not self.grid_controller.is_local(pb.current_track())):
            blocker = 'This track has a SoundBox manual grid'
        reason, bpm_reason = self.grid_controller.presentation_reasons(
            pb.current_track(), include_bpm=not blocker)
        reason = reason or blocker
        step = pb.grid_nudge_ms()
        for index, button in enumerate(self.grid_edit_buttons):
            button.setEnabled(not reason)
            detail = f'\n{step} ms · Shift: {step * 3} ms' if index in (1, 2) else ''
            button.setToolTip(button.accessibleName() + detail + ('\n' + reason if reason else ''))
        bpm_reason = reason or bpm_reason
        self.bpm_value.set_editable(not bpm_reason, bpm_reason)
        self._sync_prep_bpm()

    def _grid_changed(self, track_id):
        prep=getattr(self,'prep_controller',None)
        if prep and prep.handles(track_id):
            prep.refresh(track_id);self._sync_edit_status();return
        pb = self.player_bar
        track = self.tag_controller.lib.tracks_by_id.get(track_id) if self.tag_controller.lib else None
        view = self.grid_controller.view(track_id)
        provenance = pb._analysis_parts.get('provenance', {})
        manual = provenance.get('grid') == 'soundbox-manual' or provenance.get('user_data') == 'soundbox'
        if (track is not None and view is not None and not manual
                and not self.grid_controller.is_local(track_id)):
            pb.refresh_live_grid(track_id, track.path, view[0], view[1])
        self._sync_grid()
        self._sync_edit_status()

    def _edit_grid(self, operation, value=0, *, track_id=None):
        self._sync_grid()
        pb = self.player_bar
        if (self._state_maintenance or self._shutting_down
                or pb._pending_autoplay or pb._native_error
                or not pb.cue_panel._loaded):
            return False
        tid = pb.current_track()
        if (not self.grid_controller.is_local(tid)
                and (pb._analysis_parts.get('provenance', {}).get('grid') == 'soundbox-manual'
                     or pb._analysis_parts.get('provenance', {}).get('user_data') == 'soundbox')):
            return False
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
            self._update_browser_bpm(track_id, track.bpm)
        if not self.tag_controller.owned:
            self._cache_library()
            self._finish_tag_refresh()
        self._finish_edit_status()

    def _update_browser_bpm(self, track_id, bpm, *, estimated=False):
        seat = self._browser_seat()
        reset = self.model.update_bpm(track_id, bpm, estimated=estimated)
        if reset:self._restore_browser_seat(seat)

    def _sync_tags(self):
        from .tag_selection_view import release
        release(self)
        from .tag_controller import device_blocks_library_work
        if not hasattr(self, "my_tags"):
            return
        controller = self.tag_controller
        ids = self._selected_ids() if hasattr(self, "table") and self.stack.currentIndex() == 0 else []
        reason = controller.reason(ids)
        if not reason and (self._refresh_worker is not None or self._state_maintenance
                           or device_blocks_library_work(self)):
            reason = "Library operation in progress"
        lib = controller.lib
        assigned = controller.selected_tags(ids)
        creation_reason = self.tag_definition_controller.reason()
        if not creation_reason and (self._refresh_worker is not None or device_blocks_library_work(self)):
            creation_reason = 'Library operation in progress'
        deletion_reason = self.tag_definition_controller.deletion_reason()
        if not deletion_reason and (self._refresh_worker is not None or device_blocks_library_work(self)):
            deletion_reason = 'Library operation in progress'
        rename_reason = self.tag_definition_controller.rename_reason()
        if not rename_reason and (self._refresh_worker is not None or device_blocks_library_work(self)):
            rename_reason = 'Library operation in progress'
        from .tag_order import ordered_tags
        context = (str(lib.source), lib.source_kind, lib.source_library_id) if lib else None
        project_tags=(getattr(self.tag_definition_controller,'available_tags',None)
                      or self.tag_definition_controller.native_order)
        tags = (project_tags(lib.source_tags)
                if lib and lib.source_kind=='rekordbox-db' else
                ordered_tags(lib.source_tags, self.settings, context) if lib else [])
        order_reason=(self.tag_definition_controller.order_reason()
                      if lib and lib.source_kind=='rekordbox-db' else '')
        pending_creation=getattr(self.tag_definition_controller,'pending_creation',None)
        pending_creations=({tag.tag_id for tag in tags if pending_creation(tag.tag_id,tag.source_uuid)}
                           if callable(pending_creation) else set())
        if pending_creations and not order_reason:
            order_reason='Finish creating tags before reordering them'
        self.my_tags.set_state(tags, assigned, reason,
                               creation_reason=creation_reason, deletion_reason=deletion_reason,
                               deletion_context=(str(lib.source), lib.source_kind, lib.source_library_id)
                               if lib else None, order_reason=order_reason,
                               rename_reason=rename_reason, rename_context=context,
                               pending_creations=pending_creations,
                               assignment_reasons={tag.tag_id:self.tag_definition_controller.assignment_reason(
                                   tag.tag_id,tag.source_uuid) for tag in tags})
        self._sync_edit_status()

    def _sync_edit_status(self):
        if not hasattr(self, 'status_label'):
            return
        message = _edit_footer_text(self.tag_controller)
        if self._edit_status_deferred():
            if message:
                self._deferred_edit_status = message
            return
        self._deferred_edit_status = ''
        if message and (message != self._prep_update_message
                        or self.status_label.text() != message):
            self._prep_update_message = message
            self._set_status_text(message)

    def _finish_edit_status(self, *, quiet=False):
        """Only verified save callbacks can report completion, never probes."""
        if self._edit_status_deferred():
            message = _edit_footer_text(self.tag_controller)
            if message:
                self._deferred_edit_status = message
            elif self._prep_update_message or getattr(self, '_deferred_edit_status', ''):
                self._deferred_edit_status = '' if quiet else 'Done'
            if not message:
                self._prep_update_message = ''
            return
        if _edit_footer_text(self.tag_controller):
            self._sync_edit_status()
        elif self._prep_update_message:
            previous = self._prep_update_message
            self._prep_update_message = ''
            if quiet:
                if self.status_label.text() == previous:
                    self._set_status_text('Ready')
            else:
                self._flash_status('Done', 2)

    def _edit_status_deferred(self):
        """A foreground device command owns the status, including its waits."""
        return bool(getattr(self, '_cancelling', False) or self._device_operation_owned()
                    or getattr(self, '_source_sync_waiting', None) is not None
                    or getattr(self, '_cdj_waiting', None) is not None)

    def _restore_edit_status(self):
        if self._edit_status_deferred():
            return
        notice = getattr(self, '_deferred_background_notice', None)
        if notice is not None:
            self._deferred_background_notice = None
            self._deferred_edit_status = ''
            self._flash_status(*notice)
            return
        if _edit_footer_text(self.tag_controller):
            self._sync_edit_status()
        elif getattr(self, '_deferred_edit_status', ''):
            message = self._deferred_edit_status
            self._deferred_edit_status = ''
            self._prep_update_message = ''
            self._flash_status(message, 2)

    def _background_notice(self, message, seconds=15):
        """Retain attention without taking the foreground device's status row."""
        if self._edit_status_deferred():
            self._deferred_background_notice = (message, seconds)
            return
        self._flash_status(message, seconds)

    def _sync_cue_editing(self):
        pb = self.player_bar
        local_reason = self._local_cue_reason(pb.current_track())
        reason = ('Restore this track before editing cues'
                  if pb.current_track() and pb.current_track() == getattr(self, '_cue_audition_track', None)
                  else local_reason if local_reason is not None
                  else self.tag_controller.cue_reason(pb.current_track()))
        if not reason and self._state_maintenance:
            reason = 'Library operation in progress'
        pb.cue_panel.set_editable(not reason, reason)

    def _edit_cue(self, kind, slot, operation):
        pb = self.player_bar
        if self._state_maintenance or self._shutting_down:
            return False
        track_id = pb.current_track()
        if track_id and track_id == getattr(self, '_cue_audition_track', None):
            return False
        track = self.lib.tracks_by_id.get(track_id) if self.lib else None
        if not track or str(track.path) != pb._src_path:
            return False
        if operation == 'delete':
            prep=getattr(self,'prep_controller',None)
            staged=prep.requested_cues(track_id) if prep else None
            present=(any(cue['kind']==kind and cue['slot']==slot for cue in staged)
                     if staged is not None else pb.cue_panel._cues[kind][slot] is not None)
            if not present:return False
        local_reason = self._local_cue_reason(track_id)
        reason = local_reason if local_reason is not None else self.tag_controller.cue_reason(track_id)
        if reason:
            self._flash_status(reason, 10)
            return False
        position = pb.capture_cue_position() if operation == 'add' else None
        if operation == 'add' and (position is None or track_id != pb.current_track()):
            self._flash_status('Wait for the track to finish loading before setting a cue', 8)
            return False
        if operation == 'add':
            position = pb.quantize_cue_position(position)
        if local_reason is not None:
            return self._edit_local_cue(track, kind, slot, operation, position)
        return self.tag_controller.request_cue(track_id, kind, slot, operation, position)

    def _assign_tag(self, tag_id, present):
        from .tag_controller import device_blocks_library_work
        if (self._refresh_worker is not None or self._state_maintenance
                or self._shutting_down or device_blocks_library_work(self)):
            self._sync_tags()
            return
        selected=list(self._selected_ids())
        self._log_browser_selection('assign-tag',tag_id=tag_id,present=present)
        context=(str(self.lib.source),self.lib.source_library_id,
                 smart_playlist_order.capture(self.lib,selected,tag_id,present)
                 if getattr(self.tag_controller, 'durable_assignment_ack', False) is not True else None) if self.lib else None
        if self.tag_controller.request(selected, tag_id, present):
            self._tag_order_context=context
        self._sync_rekordbox_connection()
        self._sync_tags()

    def _probe_tags(self):
        from .tag_controller import device_blocks_library_work
        if (self._refresh_worker is not None or self._state_maintenance
                or self._shutting_down or device_blocks_library_work(self)):
            self._sync_tags()
            return
        self.tag_controller.probe()
        self._sync_tags()

    def _tag_saved(self, lib):
        # The worker includes a fresh authoritative snapshot; never reconstruct
        # smart memberships by patching a stale browser cache.
        context=getattr(self,'_tag_order_context',None)
        self._tag_order_context=None
        if context and (not self.lib or (str(self.lib.source),self.lib.source_library_id)!=context[:2]):
            self._finish_tag_refresh();self._finish_edit_status()
            self._flash_status('Tags saved in the original library; current library unchanged',15)
            return
        order_error=''
        if context and getattr(self.tag_controller,'durable_assignment_ack',False) is not True:
            missing=object();previous=self.settings.get(smart_playlist_order.KEY,missing)
            try:
                after=smart_playlist_order.record(None if previous is missing else previous,context[2],lib)
                if after is not (None if previous is missing else previous):
                    self.settings[smart_playlist_order.KEY]=after
                    if not self._save_state():
                        if previous is missing:self.settings.pop(smart_playlist_order.KEY,None)
                        else:self.settings[smart_playlist_order.KEY]=previous
                        order_error='Tags saved; smart-playlist display order could not be saved'
            except ValueError as exc:
                order_error='Tags saved; '+str(exc)
        elif getattr(self.tag_controller,'durable_assignment_ack',False) is True:
            order_error=getattr(self.tag_controller,'order_error','')
        seat = self._browser_seat() if self.stack.currentIndex() == 0 else None
        from .tag_selection_view import capture
        retained_selection=capture(self)
        if retained_selection is not None:self._tag_selection_view=retained_selection
        previous_rows=self.model._rows
        self._on_refresh_done(lib,prepared_native_ids=())
        if seat is not None and self.stack.currentIndex() == 0 and self.model._rows is not previous_rows:
            self._restore_browser_seat(seat)
        self._finish_tag_refresh()
        self._finish_edit_status()
        if order_error:self._flash_status(order_error,15)

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
            cues=(self.prep_controller.cue_rows(track_id)
                  if self.prep_controller.handles(track_id) else controller.session_cues(track_id))
            self.player_bar.refresh_live_cues(track_id, track.path, cues)
        self._sync_edit_status()

    def _tag_failed(self, error):
        self._tag_order_context=None
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

    def _schedule_settings_save(self, *keys):
        """Coalesce exact preference leaves without a whole-library GUI save."""
        from .state_commit import PREFERENCE_KEYS
        if not keys or any(key not in PREFERENCE_KEYS for key in keys):
            raise ValueError('Preference saves require explicit UI preference keys')
        self._settings_dirty_keys.update(keys)
        if not self._shutting_down and not self._state_maintenance:
            self._settings_save_timer.start()

    def _flush_settings_preferences(self):
        """Admit detached preference leaves; enqueueing never means durable save."""
        keys=frozenset(getattr(self,'_settings_dirty_keys',()))
        if not keys:return True
        from .state_commit import StateCommitter
        if self._state_committer is None:self._state_committer=StateCommitter(self)
        values={key:self.settings[key] for key in keys if key in self.settings}
        removals=keys-values.keys()
        if not self._state_committer.queue_preferences(values,removals):
            self._flash_status('Preferences could not be saved — retry after fixing storage',20)
            return False
        self._settings_dirty_keys.difference_update(keys)
        return True

    def _on_jump_mode_changed(self, mode: str):
        self.settings["beat_jump_mode"] = mode
        self._schedule_settings_save('beat_jump_mode')

    def _on_waveform_zoom_changed(self, pixels_per_second: float):
        self.settings["waveform_zoom"] = pixels_per_second
        self._sync_grid()
        self._schedule_settings_save('waveform_zoom')

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
            self._schedule_settings_save('autoplay_mode')

    def _on_playback_active(self, active: bool):
        """Reserve CPU/UI time for the deck while audio is running."""
        for worker in (
                self._wf_fill_worker, self._art_worker, self._stock_art_worker):
            if worker is not None:
                worker.set_playback_active(active)
        analysis_worker = getattr(self, '_analysis_worker', None)
        if hasattr(analysis_worker, 'set_playback_active'):
            analysis_worker.set_playback_active(active)
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
        panel = QWidget(); panel.setObjectName("Sidebar"); panel.setMinimumWidth(160)
        panel.setAttribute(Qt.WA_StyledBackground, True)  # paint the right divider
        v = QVBoxLayout(panel); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        playlists = QWidget()
        pv = QVBoxLayout(playlists); pv.setContentsMargins(0,0,0,0); pv.setSpacing(0)
        header = self._section_header("PLAYLISTS", "+", self._new_playlist_menu)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda pos:self._rb_header_menu(header,pos))
        pv.addWidget(header)
        self.tree = UnifiedPlaylistTree()
        self.sb_tree = self.tree  # compatibility names refer to the same model/scrollbar
        self.tree.setHeaderHidden(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.setMinimumHeight(72)
        self.tree.currentItemChanged.connect(self._on_playlist_select)
        self.tree.itemExpanded.connect(self._collapse_siblings)
        self.tree.itemExpanded.connect(lambda item:self._local_folder_expanded(item,True))
        self.tree.itemCollapsed.connect(lambda item:self._local_folder_expanded(item,False))
        self.tree.native_tracks_dropped.connect(self._native_append)
        self.tree.native_reordered.connect(self._native_reorder)
        self.tree.native_moved.connect(self._native_move)
        self.tree.native_placed.connect(self._native_place)
        self.tree.native_files_dropped.connect(self._native_import_files)
        self.tree.tracks_dropped.connect(self._on_tracks_dropped)
        self.tree.playlist_moved.connect(self._move_custom)
        self.tree.node_moved.connect(self._move_local_node)
        self.tree.files_dropped.connect(lambda name,paths:self._import_files(paths,name or None))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._playlist_menu)
        pv.addWidget(self.tree)

        self.device_tree = DeviceTree()
        self.device_tree.setHeaderHidden(True)
        self.device_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.device_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.device_tree.setTextElideMode(Qt.ElideRight)
        self.device_tree.setMinimumHeight(48)  # was fixed 96 — now user-sized
        self.device_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(self._device_menu)
        self.device_tree.itemClicked.connect(self._on_device_click)
        self.device_tree.dropped.connect(self._on_device_drop)

        split = QSplitter(Qt.Vertical)
        split.setObjectName("SidebarSplit")
        split.setChildrenCollapsible(False)
        split.addWidget(playlists)
        split.addWidget(self.device_tree)
        split.setHandleWidth(26)
        handle_layout = QVBoxLayout(split.handle(1))
        handle_layout.setContentsMargins(0,0,0,0)
        handle_layout.addWidget(self._section_header("DEVICES",top=True))
        split.setStretchFactor(0,1)
        split.setStretchFactor(1,0)
        self._sidebar_split = split
        v.addWidget(split)
        return panel

    def _on_playlist_select(self,cur,previous):
        if cur and cur.data(0,NATIVE_TREE_ROLE):self._on_rb_select(cur,previous)
        else:self._on_custom_select(cur,previous)

    def _playlist_menu(self,pos):
        item=self.tree.itemAt(pos)
        native=bool(self.lib and self.lib.source_kind=='rekordbox-db')
        if (item and item.data(0,NATIVE_TREE_ROLE)
                or native and (item is None or item.data(0,COLLECTION_ROLE) or item.data(0,ARTISTS_ROLE))):
            self._native_tree_menu(pos)
        else:self._sb_menu(pos)

    def _new_playlist_menu(self):
        if not self.lib or self.lib.source_kind!='rekordbox-db':
            self._new_soundbox_item_menu();return
        menu=QMenu(self)
        item=self.tree.currentItem()
        parent=item.data(0,RB_NODE_ROLE) if item and item.data(0,RB_NODE_KIND_ROLE)==1 else 'root'
        menu.addAction('New Playlist',lambda:self._native_new(parent or 'root'))
        row = self._native_node(item.data(0,RB_NODE_ROLE)) if item else None
        smart_parent = row['ID'] if row and row['Attribute'] == 1 else row['ParentID'] if row else 'root'
        smart = menu.addAction('New Smart Playlist', lambda:self._new_smart_playlist(smart_parent))
        smart_reason = self.playlist_controller.smart_creation_reason()
        smart.setEnabled(not smart_reason)
        smart.setToolTip(smart_reason)
        menu.addAction('New Folder',lambda:self._native_new(parent or 'root',folder=True))
        exec_transient_menu(menu, QCursor.pos())

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
        self.model.layoutChanged.connect(self._update_player_navigation)
        self.model.cell_edited.connect(self._on_cell_edited)  # inline Title/Artist edits
        self.table.setItemDelegate(CellEditDelegate(self.table))  # pre-filled editors
        # No keypress/double-click editing — a stray Space would wipe a cell. The
        # ONLY way in is the right-click menu (which calls self.table.edit()).
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.rekordbox_tracks_dropped.connect(self._native_append)
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
        self.table.setDragDropMode(QAbstractItemView.DragDrop)
        # Drop onto the browser adds to the playlist it's showing — same as the
        # sidebar. resolve_drop tells the table whether the current view is
        # writable (custom playlist / Collection) or off-limits (Artists, rb:).
        self.table.setAcceptDrops(True)
        self.table.resolve_drop = self._resolve_table_drop
        self.table.capture_reorder = self._capture_track_reorder
        self.table.track_reorder_requested.connect(self._drop_track_reorder)
        self.table.rekordbox_files_dropped.connect(self._native_import_files)
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
        hh.sortIndicatorChanged.connect(self._schedule_reseat_selection)
        pl.addWidget(self.table)
        self.stack.addWidget(page)

        # page 1: device browser
        dpage = QWidget(); dl = QVBoxLayout(dpage)
        dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        dhead = QWidget(); dhead.setObjectName("DeviceHeader")
        dhead.setAttribute(Qt.WA_StyledBackground, True); dhead.setFixedHeight(26)
        dhh = QHBoxLayout(dhead); dhh.setContentsMargins(10, 0, 8, 0)
        self.dev_header = QLabel("Device"); self.dev_header.setObjectName("DeviceTitle")
        self.dev_header.setTextFormat(Qt.PlainText)
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
            button.clicked.connect(
                lambda checked, i=index: (
                    self._set_device_tab(i, refresh=True) if checked else None))
            self.device_tab_group.addButton(button, index)
            self.device_tab_buttons.append(button)
            dhh.addWidget(button)
        self.device_sync_btn = QPushButton("Export")
        self.device_sync_btn.setEnabled(False)
        self.device_sync_btn.setObjectName("DeviceAction")
        self.device_sync_btn.setFixedHeight(22)
        self.device_sync_btn.clicked.connect(
            self._device_primary_action)
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
        # Keep stable page indexes without constructing unused device controls.
        self.device_content_stack.addWidget(QWidget())
        self.device_content_stack.addWidget(QWidget())
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
        self.artists_view.itemDoubleClicked.connect(self._artists_double)
        self.artists_view.itemChanged.connect(self._on_artist_item_edited)
        self._artists_browser = ArtistsBrowser(self.artists_view)
        self._artist_children = self._artists_browser.children
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
        # Keep progress and Cancel anchored before variable counts/phase text.
        h = QHBoxLayout(bar); h.setContentsMargins(20, 4, 12, 4); h.setSpacing(8)
        self.status_label = ElidedLabel("Ready")
        # Reserve only the current operation's animated dots.  A single global
        # width for every possible flash message left a huge dead gap before
        # the iPod count and progress bar.
        fm = self.status_label.fontMetrics()
        self.status_count_label = QLabel("")
        self.status_count_label.setAlignment(Qt.AlignCenter)
        # Reserve the full track-count width for the operation. Per-file byte
        # details live in the status tooltip, so copy/verify bursts cannot
        # replace the count.
        self.status_count_label.setMinimumWidth(
            fm.horizontalAdvance("9999/9999") + 16)
        self.status_count_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.status_count_label.setMargin(0)
        self.status_count_label.setFixedHeight(20)
        self.status_count_label.setVisible(False)
        self.progress = ThemeProgressBar()
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
        h.addWidget(self.status_count_label)
        h.addWidget(self.status_label)
        h.addStretch(1)
        return bar

    # ---------- open / load ----------
    def _accept_source_curation(self, library, *, bootstrap=False, force_save=False, native_changed_ids=()):
        """Commit source identity and curation together before publishing a view."""
        controller=getattr(self,'metadata_controller',None)
        if controller is None:
            return self._save_state() if force_save else True
        try:
            token=controller.stage_source_scope(library,previous=self.lib,bootstrap=bootstrap)
        except (ValueError,TypeError,KeyError) as exc:
            self._flash_status('Saved curation needs reconciliation — '+str(exc),20)
            return False
        try:
            refresh_token=(controller.stage_native_refresh(library,changed_ids=native_changed_ids)
                           if native_changed_ids else None)
        except (ValueError,TypeError,KeyError) as exc:
            controller.rollback_source_scope(token)
            self._flash_status('Saved curation needs reconciliation — '+str(exc),20)
            return False
        changed=refresh_token is not None or any(getattr(self,attr)!=value for attr,value in token.items())
        if (force_save or changed) and not self._save_state():
            if refresh_token is not None:controller.rollback_source_scope(refresh_token)
            controller.rollback_source_scope(token)
            return False
        return True

    def _source_selection_reason(self, path=None):
        tags = getattr(self, 'tag_controller', None)
        prep = getattr(self, 'prep_controller', None)
        if getattr(getattr(self, '_art_requests', None), 'manual_pending', lambda:False)():
            return 'Finish artwork saves before changing library sources'
        if getattr(prep, 'admission_pending', False):
            return 'Finish cue/grid saves before changing library sources'
        if getattr(prep, 'admission_error', ''):
            return 'Cue/grid save needs attention before changing library sources'
        if any(getattr(getattr(self,name,None),'admission_pending',False)
               for name in ('playlist_controller','tag_definition_controller')):
            return 'Finish or discard unsaved source intentions before changing library sources'
        if (self._refresh_worker is not None or self._state_maintenance or self._shutting_down
                or getattr(self,'_state_publication',False) or getattr(tags,'state_commit_owned',False)
                or getattr(getattr(self,'_state_committer',None),'pending',False)
                or self._device_operation_owned() or getattr(tags, 'busy', False)
                or getattr(tags, 'owned', False)
                or getattr(tags, 'checking', False) or getattr(tags, 'admission_paused', False)
                or getattr(self, '_analysis_worker', None) is not None
                or getattr(getattr(self, '_import_tasks', None), 'pending', 0)):
            return 'Finish the current library operation before changing sources'
        if path is None or self.lib is None:
            return ''  # Initial load and the chooser must allow recovery of the original source.
        current = str(Path(self.lib.source).expanduser().resolve())
        target = str(Path(path).expanduser().resolve())
        if current == target:
            return ''
        metadata_controller=getattr(self,'metadata_controller',None)
        if metadata_controller and getattr(metadata_controller,'planning_pending',False):
            return 'Finish metadata preparation before leaving this library'
        if metadata_controller and getattr(metadata_controller,'migration_pending',False):
            migration=(self.settings.get('source_truth_alias_next') if 'source_truth_alias_next' in self.settings
                       else self.settings.get('source_truth_migration'))
            scope=migration.get('scope') if isinstance(migration,dict) else None
            source=scope.get('source') if isinstance(scope,dict) else None
            if not isinstance(source,str) or not Path(source).is_absolute():
                return 'Saved metadata migration needs attention before changing sources'
            if str(Path(source).resolve())==current:
                return 'Finish the saved metadata migration before leaving this library'
        for key in ('shared_import_jobs', 'pending_prep_edits', 'rekordbox_playlist_edits',
                    'pending_tag_definitions', 'pending_tag_assignments', 'pending_metadata_edits'):
            queue = self.settings.get(key, [])
            if not queue:
                continue
            if not isinstance(queue, list):
                return 'Saved library edits need attention before changing sources'
            for job in queue:
                source = job.get('source') if isinstance(job, dict) else None
                if not isinstance(source, str) or not Path(source).is_absolute():
                    return 'Saved library edits need attention before changing sources'
                if str(Path(source).resolve()) == current:
                    return 'Finish or discard pending edits before leaving this library'
        deletion = self.settings.get('graveyard_delete_intent')
        if deletion:
            source = deletion.get('source') if isinstance(deletion, dict) else None
            if not isinstance(source, str) or str(Path(source).resolve()) == current:
                return 'Finish the pending library removal before changing sources'
        return ''

    def _open_dialog(self):
        reason = self._source_selection_reason()
        if reason:
            self._flash_status(reason, 10)
            return
        start = str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(self, "Open Rekordbox XML", start,
                                              "rekordbox XML (*.xml)")
        if path:
            self.open_library(path)

    def open_library(self, path: str, *, initial=False):
        reason = self._source_selection_reason(None if initial else path)
        if reason:
            self._flash_status(reason, 10)
            return False
        try:
            library = engine.load_library(path)
        except Exception as exc:  # parser failures also retain the previous library
            LOG.exception("Load failed: %s", path)
            QMessageBox.critical(self, "Load failed", str(exc))
            return False
        previous_path = self._last_xml
        self._last_xml = path
        if not self._accept_source_curation(library, force_save=True):
            self._last_xml = previous_path
            self._flash_status('Library selection could not be saved; the previous library is retained', 10)
            return False
        reset_navigation = not initial and source_scope(self.lib) != source_scope(library)
        self.lib = library
        LOG.info("Loaded %s: %d tracks, %d playlists",
                 Path(path).name, self.lib.track_count, len(self.lib.playlists))
        self._rekordbox_source_verified = True
        self._rekordbox_connection_error = ""
        self._apply_loaded_library(**({'reset_navigation': True} if reset_navigation else {}))
        QTimer.singleShot(0,self,self._resume_analysis_jobs)
        self._sync_rekordbox_connection()
        self._refresh_devices()
        self._cache_library()  # snapshot for the next instant paint
        self._arm_waveform_rechecks()
        self.unification_controller.schedule()
        if self.providers.allows("artwork.search"):
            QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
        self._schedule_stock_artwork()
        QTimer.singleShot(500, self._maybe_fill_waveforms)
        return True

    def _apply_loaded_library(self, *, already_projected=False,prepared_native_ids=None,
                              reset_navigation=False):
        """Prepare the loaded source without replacing the current device page.

        Source selection explicitly resets navigation; ordinary readback and
        metadata acknowledgements retain the user's latest presentation choice.
        """
        # A fresh library can have different add-times than the one it replaces
        # (the instant-paint snapshot vs the live rekordbox read), so the per-path
        # landed-time cache MUST be dropped — otherwise a birthtime computed off
        # the snapshot sticks and defeats the newest-added sort.
        try:
            unavailable={}
            if not already_projected:
                self.lib=shared_imports.project(self.lib,self.settings.get(shared_imports.BINDINGS,[]),
                                                unavailable=unavailable)
            if unavailable:
                LOG.info('Native Collection no longer contains %d bound imported track(s); other songs remain usable',
                         len(unavailable))
        except (RuntimeError,TypeError,ValueError) as exc:
            self._rekordbox_source_verified=False
            self._rekordbox_connection_error='Native identity needs reconciliation: '+str(exc)
            LOG.error('%s',self._rekordbox_connection_error)
        prepared_only=prepared_native_ids is not None
        previous_browser_generation=self._cache_gen
        self.tag_controller.configure(self.lib, self._rekordbox_source_verified)
        if not prepared_only:
            if (not self.grid_controller.pending(self.player_bar.current_track())
                    and not self.grid_controller.is_local(self.player_bar.current_track())):
                self.player_bar.clear_live_grid()
            self.grid_controller.configure()
            self._landed_cache.clear()
        self._sync_playlists_cache = None
        had_view=bool(getattr(self,'_view_key',None))
        repaired = 0 if prepared_only else imports.inject(self.lib, self.imported)  # livesets rejoin the pool
        if repaired:
            LOG.info("Repaired duration/size metadata for %d imported track(s)",
                     repaired)
            self._save_state()
        loaded = self.lib.tracks_by_id.get(self.player_bar.current_track())
        if loaded is not None:
            cue_rows=(self.prep_controller.cue_rows(loaded.track_id)
                      if self.prep_controller.handles(loaded.track_id)
                      else getattr(loaded,'source_cues',None))
            self.player_bar.refresh_live_cues(
                loaded.track_id, loaded.path, cue_rows)
        self._sync_prep_bpm()
        # Presentation reuse is independent of source authority. Cue/tag edit
        # identities are refreshed above even when every browser input matches.
        # Retain scalar native comparison inputs during the existing browser
        # inventory pass. Refresh workers reuse this owned map without cloning
        # the Library, cue/reference graphs or saved beat arrays.
        previous_track_inputs=getattr(self,'_display_track_inputs',None)
        incremental_inputs=bool(prepared_only and previous_track_inputs is not None
                                and getattr(self,'_display_input_generation',None)==previous_browser_generation
                                and len(previous_track_inputs)==len(self.lib.tracks_by_id)
                                and previous_track_inputs.keys()==self.lib.tracks_by_id.keys())
        native_metadata = dict(self._metadata_native_index) if incremental_inputs else {}
        track_inputs=dict(previous_track_inputs) if incremental_inputs else {}
        def display_input(t):
            identity = t.native_id
            pin = (t.source_uuid, t.source_native_path or str(t.path), t.name, t.artist)
            if incremental_inputs:
                if native_metadata.get(identity) is not None:native_metadata[identity]=pin
            else:native_metadata[identity] = None if identity in native_metadata else pin
            value=tuple(getattr(t, f) for f in (
                'track_id', 'name', 'artist', 'location_uri', 'bpm', 'key',
                'total_time', 'date_added', 'added_ts', 'genre', 'kind',
                'size', 'bitrate', 'rating', 'year', 'imported')) + (json.dumps(t.info,sort_keys=True),)
            track_inputs[t.track_id]=value
            return value
        if incremental_inputs:
            scalar_inputs=set(self._display_inputs[0])
            for identity in prepared_native_ids:
                track=self.lib.tracks_by_id.get(identity)
                if track is not None:
                    scalar_inputs.discard(track_inputs[identity])
                    scalar_inputs.add(display_input(track))
            scalar_inputs=frozenset(scalar_inputs)
        else:scalar_inputs=frozenset(display_input(t) for t in self.lib.tracks_by_id.values())
        display_inputs = (
            scalar_inputs,
            tuple(sorted(self.overrides.items())), tuple(sorted(self.titles.items())),
            tuple(sorted(self.aliases.items())), tuple(self._title_junk()),
        )
        reuse = display_inputs == getattr(self, '_display_inputs', None)
        previous_registry=getattr(self,'_display_registry',None)
        if reuse and not prepared_only:
            self.lib.artist_registry = self._display_registry
        else:
            if prepared_only:self._build_artist_registry(invalidate_rows=False)
            else:self._build_artist_registry()
        if prepared_only:
            # Source compatibility compares incoming/current Tracks. Retain
            # the detached painted baseline too: a mutable current Track may
            # already equal the incoming value while its cached row is older.
            extra=set(self._artist_registry_changed_ids)-set(prepared_native_ids)
            if incremental_inputs and extra:
                scalar_inputs=set(scalar_inputs)
                for identity in extra:
                    scalar_inputs.discard(track_inputs[identity])
                    scalar_inputs.add(display_input(self.lib.tracks_by_id[identity]))
                display_inputs=(frozenset(scalar_inputs),*display_inputs[1:])
                reuse=display_inputs==getattr(self,'_display_inputs',None)
            from .prepared_browser import artist_tag_inputs
            incoming_tags=artist_tag_inputs(self.lib)
            previous_tags=getattr(self,'_artists_tag_inputs',None)
            tag_changes=(set(incoming_tags) if previous_tags is None else
                         {identity for identity,labels in previous_tags.items()
                          if incoming_tags.get(identity)!=labels})
            dirty=tuple(sorted(set(prepared_native_ids)|set(self._artist_registry_changed_ids)|tag_changes))
            affected=(tuple(self.lib.tracks_by_id) if not metadata.registry_context_equal(
                previous_registry,self.lib.artist_registry) else dirty)
            self._apply_settings_to_lib(invalidate_rows=False,configure_artwork=False)
            self._invalidate_rows(affected)
        else:self._apply_settings_to_lib(invalidate_rows=not reuse)
        self._display_inputs = display_inputs
        self._display_registry = self.lib.artist_registry
        self._metadata_native_index = native_metadata
        self._display_track_inputs=track_inputs
        self._display_input_generation=self._cache_gen
        self._browser_cache_inputs=display_inputs
        # A disposable checkpoint can fingerprint these detached scalar inputs
        # in its existing worker. Complete loads still need the key here to
        # look up their prepared rows before the first interactive frame.
        self._browser_cache_key = None if prepared_only else browser_cache.fingerprint(display_inputs)
        if not reuse and not prepared_only:
            prepared_rows = browser_cache.load(
                self._data_file.with_name('browsercache.json'), self._browser_cache_key)
            if prepared_rows is not None:
                self._row_cache.update({tid: row for tid, row in prepared_rows.items()
                                        if tid in self.lib.tracks_by_id})
        from .prepared_browser import context as browser_context, artists_context
        current_browser_context=browser_context(self)
        current_artists_context=artists_context(self)
        same_registry=metadata.registry_context_equal(previous_registry,self.lib.artist_registry)
        if (prepared_only and same_registry and getattr(self,'_artists_context',None)==current_artists_context
                and getattr(self,'_artists_library',None) is not None):
            # Native/media compatibility was proved before this publication.
            # Retain the prepared Artists baseline; its existing dirty-ID path
            # still rebuilds every changed row and preserves fresh source data.
            self._artists_library=self.lib
        collection_refresh=(
            (tuple(affected),previous_browser_generation,current_browser_context)
            if prepared_only and same_registry else None)
        if (prepared_only and same_registry
                and getattr(self,'_tree_browser_context',None)==current_browser_context):
            # Source identity, visibility and complete ordered organization are
            # unchanged. Keep the existing tree items/editors/scroll position;
            # only dirty Artists rows need their ordinary prepared publication.
            self._prepare_artists(prepared_context=current_artists_context)
        else:
            self._populate_tree(engine.summary(self.lib))
        if not prepared_only:
            self.graveyard_controller.prepare_library()
            self.tag_definition_controller.schedule()
            self.metadata_controller.schedule()
            self.shared_import_controller.schedule()
            self.native_analysis_controller.discover()
            self.media_controller.discover()
            if reset_navigation:self._default_to_collection(force=True)
            else:self._default_to_collection()
        self._prepared_collection_refresh=collection_refresh
        try:
            if had_view:self._reload_current_view()
        finally:
            self._prepared_collection_refresh=None

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

    def _prepare_library_processes(self):
        """Ordinary engine services are ready before the first interaction."""
        from soundbox.cache_process import CacheProcess
        from soundbox.native_reader_process import NativeReaderProcess
        root = self._data_file.parent
        root.mkdir(parents=True, exist_ok=True)
        self._cache_process = CacheProcess(root)
        self.native_reader = NativeReaderProcess(root)
        for label, service in (('Snapshot cache', self._cache_process),
                               ('Native library reader', self.native_reader)):
            try:
                service.start()
            except (OSError, RuntimeError, ValueError) as exc:
                LOG.warning('%s helper unavailable: %s', label, exc)

    def _close_library_processes(self):
        """Join every helper off the GUI while Qt remains alive for teardown."""
        from .workers import OneShotTask
        services = [getattr(self, name, None) for name in ('_cache_process', 'native_reader')]
        services = [service for service in services if service is not None]
        if not services:
            return

        def close():
            for service in services:
                while True:
                    try:
                        service.close()
                        break
                    except (OSError, RuntimeError) as exc:
                        process = service.process if hasattr(service, 'process') else service.worker.process
                        if process is None or process.poll() is not None:
                            LOG.warning('Library helper shutdown reported: %s', exc)
                            break
                        # A timeout closes admission but retains this actor.
                        LOG.warning('Waiting for owned library helper: %s', exc)

        class JoinTask(OneShotTask):
            def cancel(self):
                # Closing admission cannot cancel the required owned join.
                pass

        owner = TaskOwner(self)
        worker = JoinTask(close)
        owner.adopt(worker)
        worker.start()
        owner.drain()
        owner.deleteLater()

    def _start_library_cache(self, lib):
        if getattr(self, "_state_maintenance", False) or getattr(self, '_shutting_down', False):
            return
        from .workers import OneShotTask
        service = getattr(self, '_cache_process', None)
        if service is None or not service.available:
            return  # Disposable caches never choose a blocking GUI fallback.
        key = getattr(self, '_browser_cache_key', None)
        inputs=(getattr(self,'_browser_cache_inputs',None)
                if getattr(self,'_display_input_generation',None)==self._cache_gen
                and getattr(self,'_browser_cache_inputs',None) is getattr(self,'_display_inputs',None)
                else None)
        rows = {tid: dict(row) for tid, row in self._row_cache.items()}
        def save():
            service.write(lib, inputs, rows, key=key)
        w = OneShotTask(save,
                        log_label="Library snapshot cache")
        self._libcache_worker = w
        self._oneshots = getattr(self, "_oneshots", [])
        self._oneshots.append(w)
        w.done.connect(self._on_library_cache_done)
        self._cache_tasks.adopt(w, self._finish_library_cache)
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

    def _find_tree_item(self, path: str, identity=None):
        """Locate a playlist tree item by its display path (PATH_ROLE)."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            if (it.data(0,RB_NODE_ROLE)==identity if identity else it.data(0,PATH_ROLE)==path):
                return it
            for i in range(it.childCount()):
                stack.append(it.child(i))
        return None

    def _populate_tree(self, summary: dict, *, presentation=None):
        self.tree._set_hover(None)
        self.tree._set_insert(None)
        presentation=self._native_presentation() if presentation is None else presentation
        if presentation is not None and presentation is not self.lib:
            summary=engine.summary(presentation)
        from .trees import NATIVE_BINDING_ROLE
        native_library=presentation or self.lib
        native_rows={row['ID']:row for row in (getattr(native_library,'source_playlist_catalog',None) or {}).get('nodes',[])}
        cur = self.tree.currentItem()
        identity_role=Qt.UserRole+82
        current_id=cur.data(0,identity_role) if cur else None
        scroll=self.tree.verticalScrollBar().value()
        horizontal=self.tree.horizontalScrollBar().value()
        blocked=self.tree.blockSignals(True)
        updates=self.tree.updatesEnabled();self.tree.setUpdatesEnabled(False)
        try:
            from soundbox.playlist_unification import hidden_native_roots
            state=self.unification_controller.state()
            pending_deletions=self._native_pending_deletions(presentation=presentation) | hidden_native_roots(
                state,presentation.source_playlist_catalog if presentation else self.lib.source_playlist_catalog)
            children=summary["tree"].get("children", [])
            def visible(node):
                if node.get('pid') in pending_deletions:return None
                return {**node,'children':[v for c in node.get('children',[]) if (v:=visible(c)) is not None]}
            projected=[v for child in children if (v:=visible(child)) is not None]
            # Reuse native items by stable ID. Saving an unrelated playlist
            # must not destroy expanded folders, editors or the user's seat.
            root=self.tree.invisibleRootItem();existing={}
            def collect(parent):
                for i in range(parent.childCount()):
                    item=parent.child(i)
                    if item.data(0,NATIVE_TREE_ROLE):existing[item.data(0,identity_role)]=item
                    collect(item)
            collect(root);wanted=set()
            aliases=self.unification_controller.export_aliases()
            def reconcile(parent,nodes,offset=0,path=''):
                for number,node in enumerate(nodes,offset):
                    full_path=path+' / '+node['name'] if path else node['name']
                    identity=node.get('pid') or node.get('path') or full_path
                    wanted.add(identity)
                    item=existing.get(identity)
                    if item is None:
                        item=self._make_item({**node,'children':[]})
                    item.setData(0,identity_role,identity)
                    item.setText(0,f"♪  {node['name']}  ({node['count']})" if node['type']=='playlist' else node['name'])
                    item.setData(0,PATH_ROLE,node.get('path') if node['type']=='playlist' else None)
                    item.setData(0,CUSTOM_ROLE,aliases.get(identity))
                    native=native_rows.get(node.get('pid'))
                    item.setData(0,RB_NODE_KIND_ROLE,native['Attribute'] if native else node.get('attribute'))
                    item.setData(0,NATIVE_BINDING_ROLE,
                        dict(source=str(native_library.source),library_id=native_library.source_library_id,
                             id=native['ID'],uuid=native['UUID'],alias=aliases.get(identity))
                        if native_library and native_library.source_kind=='rekordbox-db' and native else None)
                    item.setData(0,PINNED_TREE_ROLE,False)
                    owner=item.parent() or (root if item.treeWidget() is self.tree else None)
                    if owner is not parent or parent.indexOfChild(item)!=number:
                        if owner is not None:owner.takeChild(owner.indexOfChild(item))
                        parent.insertChild(number,item)
                    reconcile(item,node.get('children',[]),path=full_path)
            offset=sum(bool(root.child(i).data(0,COLLECTION_ROLE) or root.child(i).data(0,ARTISTS_ROLE))
                       for i in range(root.childCount()))
            reconcile(root,projected,offset)
            for identity,item in existing.items():
                if identity not in wanted:
                    owner=item.parent() or root
                    owner.takeChild(owner.indexOfChild(item))
            self._refresh_custom()
            if current_id in wanted:
                item=next((item for item in self.tree.findItems('',Qt.MatchContains|Qt.MatchRecursive)
                           if item.data(0,identity_role)==current_id),None)
                if item is not None:self.tree.setCurrentItem(item)
            active=getattr(self,'_view_key','') or ''
            if active.startswith('rb:'):
                item=self._find_tree_item('',getattr(self,'_active_native_playlist_id',None))
                if item is not None and item.data(0,PATH_ROLE):self._view_key='rb:'+item.data(0,PATH_ROLE)
            self.tree.doItemsLayout()
            self.tree.verticalScrollBar().setValue(scroll)
            self.tree.horizontalScrollBar().setValue(horizontal)
        finally:
            # Recursive closure cells must not retain borrowed Qt items or an
            # obsolete Library beyond this synchronous publication.
            visible = collect = reconcile = None
            self.tree.blockSignals(blocked)
            self.tree.setUpdatesEnabled(updates)
        self._native_pending_changed(presentation=presentation)
        from .prepared_browser import context as browser_context
        self._tree_browser_context=browser_context(self)

    def _make_item(self, node: dict) -> QTreeWidgetItem:
        it = QTreeWidgetItem()
        it.setData(0,NATIVE_TREE_ROLE,True)
        it.setData(0,RB_NODE_ROLE,node.get("pid"))
        it.setData(0,RB_NODE_KIND_ROLE,node.get("attribute"))
        alias=self.unification_controller.export_aliases().get(node.get('pid'))
        if alias:it.setData(0,CUSTOM_ROLE,alias)
        if node["type"] == "playlist":
            it.setText(0, f"♪  {node['name']}  ({node['count']})")
            it.setData(0, PATH_ROLE, node["path"])
        else:
            it.setText(0, node["name"])
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
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
        if self._source_selection_reason():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose rekordbox database", str(self._rekordbox_database_path().parent),
            "rekordbox database (*.db)")
        if path:
            self._start_rekordbox_refresh(Path(path), selection=True)

    def _use_default_rekordbox_database(self):
        if self._source_selection_reason():
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
        if selection and (reason := self._source_selection_reason(path)):
            self._flash_status(reason, 10)
            return
        if self._shutting_down or self._state_maintenance or self._refresh_worker is not None or self.tag_controller.owned:
            return
        if not self._device_operation_owned():
            self._start_pulse("Loading")
        w = RefreshWorker(path)
        w.native_reader = self.native_reader
        # A warm browser snapshot is not a completed publication checkpoint.
        # Each process's first verified source read re-derives all rows in its
        # worker, so a crash before a sparse plan saved cannot strand curation.
        if self.lib is not None and self._rekordbox_source_verified:
            w.metadata_baseline = ((str(self.lib.source), self.lib.source_kind, self.lib.source_library_id),
                                   getattr(self, '_metadata_native_index', {}))
        w.retained_tracks = {k:dict(v) for k,v in self.settings.get('rekordbox_collection_tracks',{}).items()
                             if isinstance(v,dict)}
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
                accepted = self._on_refresh_done(result, metadata_changed_ids=getattr(w,'metadata_changed_ids',()),
                                                 selection_path=selection)
                if not accepted and selection is not None:
                    error = 'Database selection could not be saved; the previous library is retained.'
            if error:
                self._on_refresh_fail(error)
            self._sync_rekordbox_connection()
        pending = self._refresh_pending
        self._refresh_pending = False
        if pending and not self._shutting_down:
            QTimer.singleShot(0, self._refresh_rekordbox)

    @preserve_inline_drafts
    def _on_refresh_done(self, lib, *, metadata_changed_ids=(), selection_path=None,prepared_native_ids=None,
                         projected_bindings_owner=None,projected_scope=None):
        try:
            if projected_scope is not None:
                if (projected_bindings_owner is None
                        or self.settings.get(shared_imports.BINDINGS) is not projected_bindings_owner
                        or projected_scope!=(str(lib.source),lib.source_library_id)):
                    raise ValueError('Song registration identities changed during metadata refresh')
            else:
                lib = shared_imports.project(lib,self.settings.get(shared_imports.BINDINGS,[]),unavailable={})
            aliases = {binding['native_id']: binding['local_id']
                for binding in self.settings.get(shared_imports.BINDINGS,[])
                if (binding['source'],binding['library_id']) == (str(lib.source),lib.source_library_id)}
            changed_ids = tuple(aliases.get(identity,identity) if aliases.get(identity,identity) in lib.tracks_by_id
                                else identity for identity in metadata_changed_ids)
            if prepared_native_ids is not None:
                from .native_publication import reuse_prepared_imports
                prepared_native_ids=tuple(aliases.get(identity,identity) for identity in prepared_native_ids)
                changed_display_ids=set()
                if selection_path is not None or not reuse_prepared_imports(
                        self.lib,lib,self.imported,changed_ids=changed_display_ids):
                    prepared_native_ids=None
                else:
                    prepared_native_ids=tuple(sorted(set(prepared_native_ids)|changed_display_ids))
        except (ValueError,TypeError,KeyError,RuntimeError) as exc:
            self._rekordbox_connection_error = 'Native identity needs reconciliation: '+str(exc)
            self._rekordbox_source_verified = False
            self.tag_controller.configure(self.lib,False)
            self._sync_rekordbox_connection()
            return False
        previous_settings = self.settings
        if selection_path is not None:
            self.settings = dict(previous_settings)
            if Path(selection_path) == rekordbox_db.REKORDBOX_DB:
                self.settings.pop('rekordbox_db_path',None)
            else:
                self.settings['rekordbox_db_path'] = selection_path
        if not self._accept_source_curation(lib,native_changed_ids=changed_ids,
                                            force_save=selection_path is not None):
            self.settings = previous_settings
            self._rekordbox_source_verified=False
            self.tag_controller.configure(self.lib,False)
            self._sync_rekordbox_connection()
            return False
        if not self._device_operation_owned():
            self._stop_pulse()
        previous_registry = self.lib.artist_registry if self.lib else None
        previous_unaliased = getattr(self,'_metadata_unaliased_registry',None)
        previous_library=self.lib
        reset_navigation = previous_library is not None and source_scope(previous_library) != source_scope(lib)
        self.lib = lib
        self._rekordbox_source_verified = True
        self._rekordbox_connection_error = ""
        media=getattr(self,'media_controller',None)
        if prepared_native_ids is not None and media is not None:
            if not media.rebind_prepared_library(previous_library,lib):media.discover()
        navigation = {'reset_navigation': True} if reset_navigation else {}
        if prepared_native_ids is None:self._apply_loaded_library(already_projected=True,**navigation)
        else:self._apply_loaded_library(already_projected=True,prepared_native_ids=prepared_native_ids,**navigation)
        controller = getattr(self,'metadata_controller',None)
        if controller is not None and changed_ids:
            # A changed canonical/trusted name can affect other raw rows (for
            # example a newly trusted name in a reversed title). Expand only
            # when global lookup semantics changed, never on unchanged refresh
            # or a scalar edit whose registry context is still identical.
            if (not metadata.registry_context_equal(previous_registry,self.lib.artist_registry)
                    or not metadata.registry_context_equal(previous_unaliased,self._metadata_unaliased_registry)):
                changed_ids = tuple(self.lib.tracks_by_id)
            controller.publish_derived(self.lib,changed_ids=changed_ids,
                context=(self.lib.artist_registry,self._metadata_unaliased_registry),verified=True)
        if prepared_native_ids is None:
            QTimer.singleShot(0,self,self._resume_analysis_jobs)
            self._refresh_devices()
        LOG.info("Loaded %d tracks / %d playlists from rekordbox",
                 lib.track_count, len(lib.playlists))
        self._sync_rekordbox_connection()
        controllers=(getattr(self,name,None) for name in (
            'playlist_controller','shared_import_controller','native_analysis_controller',
            'metadata_controller','tag_definition_controller','prep_controller','tag_controller'))
        if (self.tag_controller.update_status_text or any(controller and
                (getattr(controller,'pending',[]) or getattr(controller,'planning_pending',False)
                 or getattr(controller,'queue_error',lambda:'')())
                for controller in controllers)):
            self._sync_edit_status()
        elif not self._rekordbox_source_verified:
            self._flash_status(self._rekordbox_connection_error,20)
        elif not self._device_operation_owned() and prepared_native_ids is None and not metadata_changed_ids:
            self._flash_status("Updated ✓", 6)
        self._cache_library()  # refresh the snapshot for the next instant paint
        if prepared_native_ids is None:
            self._arm_waveform_rechecks()
            self.unification_controller.schedule()
            if self.providers.allows("artwork.search"):
                QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
            self._schedule_stock_artwork()
            QTimer.singleShot(500, self._maybe_fill_waveforms)
        return True

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
        add_recovery_actions(self, menu)
        self._native_menu_actions(menu)
        menu.addSeparator()
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
        exec_transient_menu(menu, widget.mapToGlobal(pos))

    def _set_metadata_background_paused(self,paused,expected_scope):
        lib=self.lib
        if not lib or (str(lib.source),lib.source_kind,lib.source_library_id)!=expected_scope:
            self._flash_status('Library changed; reopen its menu',8);return False
        if not self.metadata_controller.set_background_paused(paused):return False
        self._sync_edit_status()
        return True

    @_provider_action("artists.suggest")
    def _suggest_artists(self):
        """Batch queue: scan all Unknown-Artist tracks for uploader-consensus
        suggestions; accepted ones become normal overrides (which also teach the
        trusted set + reversed flip)."""
        if self.lib is None:
            return
        from .suggest import SuggestDialog
        from soundbox import alias_intents
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
        try:pins=alias_intents.artist_pins(self.lib,[tid for tid,_title in items])
        except (ValueError,KeyError,TypeError) as exc:
            self._flash_status('Refresh song identities before artist edits — '+str(exc),12)
            return
        dlg = SuggestDialog(items, self, provider=self.providers.artists)
        try:
            dlg.exec()
        finally:
            dlg.shutdown()
            accepts = tuple(dlg.accepts)
            dlg.deleteLater()
        if self._shutting_down or self._state_maintenance or not accepts:
            return
        try:
            ids=[tid for tid,_artist in accepts]
            expected={'scope':pins['scope'],'tracks':{tid:pins['tracks'][tid] for tid in ids}}
            if alias_intents.artist_pins(self.lib,ids)!=expected:raise ValueError('Song or library changed')
        except (ValueError,KeyError,TypeError):
            self._flash_status('Song or library changed; reopen artist edits',12)
            return
        controller=getattr(self,'metadata_controller',None)
        native=bool(controller is not None and self.lib and self.lib.source_kind=='rekordbox-db')
        if native:
            if not controller.change_artists(accepts,pins):return
        else:
            previous=self.overrides;self.overrides={**previous,**dict(accepts)}
            if not self._save_state():self.overrides=previous;return
            self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status(f'Artist updates pending ({len(accepts)})' if native else f'Accepted {len(accepts)} ✓',8)

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
        source_scope = _artist_source_scope(self.lib)
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
        self._start_pulse("Scanning")
        status_generation = getattr(self,'_status_generation',None)
        w.done.connect(lambda result, scope=source_scope, worker=w, generation=status_generation:
                       self._on_merge_scan(result,source_scope=scope,worker=worker,status_generation=generation))
        w.finished.connect(lambda ww=w: self._finish_worker("_scan_worker", ww))
        w.start()

    def _on_merge_scan(self, res, *, source_scope=None, worker=None, status_generation=None):
        if self._shutting_down or self._state_maintenance:
            return
        current_scope=_artist_source_scope(getattr(self,'lib',None))
        owns_status=(status_generation is None or
            status_generation==getattr(self,'_status_generation',None) and getattr(self,'_pulse_word',None)=='Scanning')
        if (source_scope is not None and source_scope!=current_scope
                or worker is not None and getattr(self,'_scan_worker',None) is not worker):
            if worker is not None and getattr(self,'_scan_worker',None) is worker and owns_status:
                self._set_status_text("Ready")
            return
        if owns_status:self._set_status_text("Ready")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Scan failed", str(res))
            return
        counts, suggestions = res
        suggestions = [x for x in suggestions if x[0] not in self.aliases]
        if not suggestions and not self.aliases:
            if owns_status:self._flash_status("No duplicates ✓", 6)
            return
        from .suggest import MergeDialog
        dlg = MergeDialog(suggestions, dict(self.aliases), counts, self)
        try:
            if dlg.exec() != MergeDialog.DialogCode.Accepted:
                return
            accepts, unmerges = tuple(dlg.accepts), tuple(dlg.unmerges)
        finally:
            dlg.deleteLater()
        if self._shutting_down or self._state_maintenance:
            return
        if _artist_source_scope(getattr(self,'lib',None))!=current_scope:
            self._flash_status('Library changed; reopen artist merges',8)
            return
        self._apply_merges(accepts, unmerges)

    def _apply_merges(self, accepts, unmerges):
        """Apply merge-window results: unmerge first, then add aliases, then
        rebuild the registry + views. No-op if nothing changed."""
        if not accepts and not unmerges:
            return
        controller=getattr(self,'metadata_controller',None)
        native=bool(controller is not None and self.lib and self.lib.source_kind=='rekordbox-db')
        if native:
            if not controller.change_aliases(accepts,unmerges):return
        else:
            previous=self.aliases;updated=dict(previous)
            for src in unmerges:updated.pop(src,None)
            for variant,target in accepts:updated[variant]=target
            self.aliases=updated
            if not self._save_state():self.aliases=previous;return
            self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status('Artist updates pending' if native else f'Merged {len(accepts)} ✓',8)

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
        self._selection_revision=getattr(self,'_selection_revision',0)+1
        if not switching:
            return
        self.table.sortByColumn(-1,Qt.AscendingOrder)
        self._selection_revision+=1
        revision=self._selection_revision
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
            QTimer.singleShot(0,self,lambda:bar.setValue(min(scroll,bar.maximum()))
                if self._view_key==key and self._selection_revision==revision else None)

    def _on_rb_select(self, cur, _prev):
        if cur is None:
            return
        path = cur.data(0, PATH_ROLE)
        if not path:
            return  # Folder navigation does not change the active playlist/view.
        self._select_only(self.tree)
        self._active_native_playlist_id=cur.data(0,RB_NODE_ROLE)
        if self.lib is None:
            return
        self._stash_view()
        self._set_refreshed_rows(self._annotate_art(self._playlist_rows(path)))
        self.stack.setCurrentIndex(0)
        self._restore_view(f"rb:{path}")

    # ---------- custom (SoundBox) playlists ----------
    def _new_custom(self):
        if self.lib and self.lib.source_kind=='rekordbox-db':self._native_new()
        else:self._new_local_playlist()

    def _custom_name_taken(self, name: str, *, exclude_custom='') -> bool:
        from .playlist_names import playlist_name_taken
        return playlist_name_taken(self, name, custom_name=exclude_custom)

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
        blocked=self.sb_tree.blockSignals(True)
        try:
            for index in range(self.sb_tree.topLevelItemCount()-1,-1,-1):
                if not self.sb_tree.topLevelItem(index).data(0,NATIVE_TREE_ROLE):self.sb_tree.takeTopLevelItem(index)
            # Built-in "Collection" pinned first: every track in one view. No
            # CUSTOM_ROLE (can't be renamed/deleted/reordered), but draggable
            # onto a device as a whole-pool playlist (sbview mime).
            tracks = self._visible_collection_tracks()
            self._prepare_artists(tracks=tracks)
            coll = QTreeWidgetItem([f"⌂  Collection  ({len(tracks)})"])
            coll.setData(0, COLLECTION_ROLE, True)
            coll.setData(0,PINNED_TREE_ROLE,True)
            self.sb_tree.insertTopLevelItem(0,coll)
            if keep_coll:
                self.sb_tree.setCurrentItem(coll)
            # Built-in "Artists": opens the BROWSER-side folder view (artist
            # names only; drop one down to see their songs). Count = distinct.
            arts = QTreeWidgetItem([f"♪  Artists  ({len(self._artist_children)})"])
            arts.setData(0, ARTISTS_ROLE, True)
            arts.setData(0,PINNED_TREE_ROLE,True)
            state=self.unification_controller.state()
            self.sb_tree.insertTopLevelItem(1,arts)
            if keep_artists:
                self.sb_tree.setCurrentItem(arts)
            if not state or not state['customs_done']:
                self._populate_custom_folders(keep_name, keep_folder)
        finally:
            self.sb_tree.blockSignals(blocked)

    def _resolve_table_drop(self):
        """What a drop on the main browser targets, or None to reject it.
        Mirrors the sidebar: a custom SoundBox playlist takes tracks + files;
        Collection imports files only (no "add", it's every track already);
        Artists remains derived. Regular native rekordbox views accept existing
        registered tracks through the guarded source lane; file drops wait for
        the separate native-registration protocol."""
        key = getattr(self, "_view_key", None)
        if not key:
            return None
        if key == "sb:Collection":
            return ("collection", "")
        if key.startswith("sb:"):
            name = key[3:]
            if name in getattr(self, "custom", {}):
                return ("playlist", name)
        if key.startswith('rb:') and self.lib and self.lib.source_playlist_catalog:
            pl=self._native_view_playlist(key[3:])
            node=self._native_node(pl.pid) if pl else None
            if node and node['Attribute']==0:return ('rekordbox',pl.pid)
        return None  # derived/unsupported views

    def _on_tracks_dropped(self, name: str, ids: list[str]):
        state=self.unification_controller.state()
        if state and state['customs_done']:
            binding=state['nodes'].get('custom:'+name)
            if binding:self._native_append(binding['id'],ids)
            else:self._flash_status('Choose a current native playlist before adding songs',8)
            return
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
        from .duplicate_dialog import DuplicateAddDialog
        box=DuplicateAddDialog(name,[self._track_label(i) for i in dupes[:6]],len(dupes),self)
        try:
            return (box.exec()==DuplicateAddDialog.DialogCode.Accepted
                    and not self._shutting_down and not self._state_maintenance)
        finally:
            box.deleteLater()

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
        if self._shutting_down or self._state_maintenance:
            return
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

    def _native_import_files(self, identity, paths):
        row=self._native_node(identity)
        if row and row['Attribute']==0:
            self._import_files(paths,_native_destination=dict(kind='native',id=identity,uuid=row['UUID']))

    def _queue_shared_import(self, tracks, destination):
        if self.lib is None or self._shutting_down or self._state_maintenance:return False
        before=self.settings.get(shared_imports.KEY,[])
        try:
            tracks=[replace(t,location_uri=t.path.resolve(strict=True).as_uri()) for t in tracks]
            self.settings[shared_imports.KEY]=shared_imports.admit(before,self.lib,tracks,destination,
                self.settings.get('analysis'))
        except (OSError,ValueError,TypeError,RuntimeError) as exc:
            self._flash_status(str(exc),15);return False
        if not self._save_state():self.settings[shared_imports.KEY]=before;return False
        self._native_pending_changed(rebuild=True)
        self.shared_import_controller.schedule();return True

    def _import_files(self, paths: list[str], playlist: str | None = None, *, _expanded=False,
                      _native_destination=None, _source_identity=None, _preferences=None):
        """Add non-rekordbox audio files to SoundBox, referenced in place.
        Float32 WAV conversion belongs to the recoverable shared-import lane.
        They join the track pool, so they
        appear in Collection automatically; `playlist` also adds them to that
        SoundBox playlist. Re-importing a file reuses its existing id (so
        overrides/art/playlist entries stick)."""
        if self.lib is None or self._shutting_down or self._state_maintenance:
            return
        source_identity=(str(self.lib.source),self.lib.source_library_id)
        preferences=shared_imports.import_preferences(self.settings.get('analysis') if _preferences is None else _preferences)
        if _source_identity is not None and _source_identity!=source_identity:
            self._flash_status('Import source changed while expanding the folder; drop it again',10);return
        if not _expanded and any(Path(p).expanduser().is_dir() for p in paths):
            from .task_owner import TaskOwner
            from .workers import OneShotTask
            if not hasattr(self, '_import_tasks'):
                self._import_tasks = TaskOwner(self)
            worker = OneShotTask(lambda: imports.expand_paths(
                paths, should_cancel=worker.cancellation.is_set), log_label='Folder import')
            def retired(job):
                if self._shutting_down or job.cancellation.is_set():
                    return
                if isinstance(job.result, Exception):
                    self._flash_status(f'Couldn’t import folder: {job.result}', 15)
                else:
                    self._import_files(job.result, playlist, _expanded=True,
                        _native_destination=_native_destination,_source_identity=source_identity,_preferences=preferences)
            self._import_tasks.adopt(worker, retired)
            worker.start()
            return
        before_imported = dict(self.imported)
        before_custom=deepcopy(self.custom)
        before_jobs=deepcopy(self.settings.get(shared_imports.KEY,[]))
        before_analysis_jobs=deepcopy(self.settings.get(analysis_jobs.KEY,[]))
        by_path={str(t.path):tid for tid,t in self.lib.tracks_by_id.items()}
        by_path.update({str(Path(rec['path']).resolve()):tid for tid,rec in self.imported.items()})
        ids: list[str] = []
        skipped: list[str] = []
        fresh_ids: list[str] = []
        for raw in paths:
            p = Path(raw).expanduser().resolve()
            if not imports.is_audio(p) or not p.is_file():
                skipped.append(p.name)
                continue
            tid = by_path.get(str(p))
            if tid is None:
                tid = imports.new_id()
                # Admit the whole drop before reading container headers or
                # creating bookmarks. Those can hydrate cloud files.
                self.imported[tid] = dict(path=str(p), added=time.strftime('%Y-%m-%d'),
                    added_ts=time.time(), time=0, size=p.stat().st_size)
                fresh_ids.append(tid)
                by_path[str(p)] = tid
            ids.append(tid)
        if ids:
            ids=list(dict.fromkeys(ids))
            if playlist:
                bag=list(self.custom.get(playlist,[]));duplicates=[tid for tid in ids if tid in bag]
                allow=bool(duplicates) and self._confirm_duplicate_add(playlist,duplicates)
                self.custom[playlist]=bag+[tid for tid in ids if allow or tid not in bag]
            if self.lib.source_kind=='rekordbox-db' and self.lib.source_library_id:
                tracks=[imports.make_track(tid,self.imported[tid]) if tid in self.imported
                        else self.lib.tracks_by_id[tid] for tid in ids]
                tracks=[replace(t,location_uri=t.path.resolve(strict=True).as_uri()) for t in tracks]
                destination=_native_destination or (dict(kind='custom',name=playlist) if playlist else dict(kind='collection'))
                try:self.settings[shared_imports.KEY]=shared_imports.admit(before_jobs,self.lib,tracks,destination,
                    preferences)
                except (ValueError,TypeError,RuntimeError) as exc:
                    self.imported=before_imported;self.custom=before_custom
                    self._flash_status(str(exc),15);return
            musical_analysis = preferences['automatic'] and (preferences['bpm'] or preferences['key'])
            if fresh_ids and (musical_analysis or self.lib.source_kind=='rekordbox-db' and self.lib.source_library_id):
                local_preferences = preferences if musical_analysis else {**preferences,'bpm':False,'key':False}
                try:self.settings[analysis_jobs.KEY]=analysis_jobs.admit(before_analysis_jobs,self.lib,
                    [imports.make_track(tid,self.imported[tid]) for tid in fresh_ids],local_preferences,
                    automatic=bool(musical_analysis))
                except ValueError as exc:
                    self.imported=before_imported;self.custom=before_custom;self.settings[shared_imports.KEY]=before_jobs
                    self._flash_status(str(exc),15);return
            if not self._save_state():
                self.imported = before_imported
                self.custom=before_custom;self.settings[shared_imports.KEY]=before_jobs
                self.settings[analysis_jobs.KEY]=before_analysis_jobs
                return
            imports.inject(self.lib, self.imported, repair_missing=False)
            self._build_artist_registry()  # imported names join the deriver
            self._refresh_custom()         # Collection count includes them now
            LOG.info("Imported %d audio files (%d skipped)", len(ids), len(skipped))
            self._flash_status(f"Imported {len(ids)} ✓", 15)
            self.shared_import_controller.schedule()
            self.media_controller.discover()
            if _native_destination:
                self._native_pending_changed(rebuild=True)
            elif playlist:
                if getattr(self,'_view_key',None)==f'sb:{playlist}':self._show_custom(playlist)
            else:
                # A Collection import must be visible immediately, even when
                # an old search/header sort would otherwise hide its new rows.
                self.search.clear()
                self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
                self.model.sort(-1)
                collection = self.sb_tree.topLevelItem(0)
                if collection is not None and collection.data(0, COLLECTION_ROLE):
                    self.sb_tree.setCurrentItem(collection)
                self._show_collection()
                self._view_key = 'sb:Collection'
                self.table.scrollToTop()
            if fresh_ids:
                QTimer.singleShot(0,self,self._resume_analysis_jobs)
            if fresh_ids:
                self._probe_import_metadata(fresh_ids)
            self._arm_waveform_rechecks()
            if self.providers.allows("artwork.search"):
                QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only
            self._schedule_stock_artwork()
            QTimer.singleShot(500, self._maybe_fill_waveforms)
        elif skipped:
            QMessageBox.warning(
                self, "Nothing imported",
                "None of the dropped files could be imported (not an audio "
                "file, or the file doesn't exist):\n\n"
                + "\n".join(f"  •  {n}" for n in skipped[:8])
                + (f"\n  …and {len(skipped) - 8} more" if len(skipped) > 8 else ""))

    def _probe_import_metadata(self, track_ids):
        from .task_owner import TaskOwner
        from .workers import OneShotTask
        if not hasattr(self, '_import_tasks'):
            self._import_tasks = TaskOwner(self)
        expected = {tid:dict(self.imported[tid]) for tid in track_ids}
        def probe():
            result = {}
            for tid, rec in expected.items():
                if worker.cancellation.is_set():
                    break
                result[tid] = imports.probe(Path(rec['path']))
            return result
        worker = OneShotTask(probe, log_label='Imported audio metadata')
        def retired(job):
            if self._shutting_down or job.cancellation.is_set():
                return
            if isinstance(job.result, Exception):
                self._flash_status(f'Couldn’t read imported audio: {job.result}', 15)
                return
            for tid, rec in job.result.items():
                current = self.imported.get(tid)
                if current is None or current.get('path') != expected[tid]['path']:
                    continue
                # Keep the original admission time/order and any newer edits.
                for key in ('time', 'size', file_locations.BOOKMARK_KEY):
                    if key in rec:
                        current[key] = rec[key]
                if self.lib is not None and (existing:=self.lib.tracks_by_id.get(tid)) is not None and existing.imported:
                    self.lib.tracks_by_id[tid] = imports.make_track(tid, current)
            self._save_state()
            self._invalidate_rows(list(job.result))
            self._reload_current_view()
        self._import_tasks.adopt(worker, retired)
        worker.start()

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
        if self._shutting_down or self._state_maintenance:
            return
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
        return artist_rows(self._annotate_art(self._rows_for(tracks)))

    def _schedule_artists_refresh(self, track_ids=None):
        self._artists_generation = None
        if track_ids is None or not hasattr(self, '_artists_dirty_ids'):
            self._artists_dirty_ids = None
        elif self._artists_dirty_ids is not None:
            self._artists_dirty_ids.update(track_ids)
        refresh = getattr(self, '_artists_refresh', None)
        if refresh is not None and not self._shutting_down:
            refresh.schedule()

    @preserve_inline_drafts
    def _prepare_artists(self, *, tracks=None, prepared_context=None):
        """Prepare the complete hidden view when library presentation changes."""
        if self._shutting_down or self._state_maintenance:
            return
        tracks = self._visible_collection_tracks() if tracks is None else tracks
        generation = (id(self.lib), self._cache_gen, tuple(track.track_id for track in tracks))
        if generation == getattr(self, '_artists_generation', None):
            return
        dirty = getattr(self, '_artists_dirty_ids', None)
        incremental = getattr(self, '_artists_library', None) is self.lib and dirty is not None
        previous = getattr(self, '_artists_rows', {}) if incremental else {}
        changed = [track for track in tracks if track.track_id not in previous
                   or dirty is None or track.track_id in dirty]
        prepared = {row['id']: row for row in self._artist_rows(changed)} if self.lib is not None else {}
        filter_changed=True
        if (incremental and previous.keys() == {track.track_id for track in tracks}
                and all((row['artist'], row['title']) == (previous[track_id]['artist'], previous[track_id]['title'])
                        for track_id, row in prepared.items())):
            from .prepared_browser import clean_row
            displayed=[row for identity,row in prepared.items() if clean_row(row)!=clean_row(previous[identity])]
            if displayed:self._artists_browser.update_rows(displayed)
            filter_changed=bool(displayed)
            self._artists_rows = {**previous, **prepared}
        else:
            # Preserve chronological ties inside identical artist/title groups.
            ordered = sorted(tracks, key=lambda track: (-self._landed_time(track), track.name.casefold()))
            rows = artist_rows([prepared.get(track.track_id) or previous[track.track_id] for track in ordered])
            self._artists_browser.replace(rows)
            self._artists_rows = {row['id']: row for row in rows}
        self._artist_children = self._artists_browser.children
        self._artists_library = self.lib
        self._artists_dirty_ids = set()
        self._artists_generation = generation
        from .prepared_browser import artists_context, artist_tag_inputs
        self._artists_context = artists_context(self) if prepared_context is None else prepared_context
        self._artists_tag_inputs = artist_tag_inputs(self.lib, tracks)
        for index in range(self.sb_tree.topLevelItemCount()):
            item = self.sb_tree.topLevelItem(index)
            if item.data(0, ARTISTS_ROLE):
                item.setText(0, f'♪  Artists  ({len(self._artist_children)})')
        if filter_changed:self._filter_artists()

    def _show_artists(self, *, preserve=False):
        """Reveal the ready Artists tree; every song already has its row."""
        if not preserve:
            self._artists_browser.reset_navigation()
        self.stack.setCurrentIndex(2)
        self._update_player_navigation()

    def _artists_menu(self, pos):
        it = self.artists_view.itemAt(pos)
        if it is None:
            return
        tid = it.data(0, ARTIST_TRACK_ROLE)
        # Selection to act on: the track row (or every track of a header row).
        selected = self.artists_view.selectedItems()
        ids = ArtistsTree._ids(selected if it in selected else [it])
        if not ids:
            return
        col = self.artists_view.columnAt(pos.x())
        menu = QMenu(self)
        binding = capture_tracks(self, ids)
        if tid and col in (0, 1):
            menu.addAction("Edit", self._artists_menu_edit(tid, col))
        if self.custom:  # add to an existing SoundBox playlist (drag also works)
            sub = menu.addMenu(f"Add {len(ids)} to Playlist" if len(ids) > 1
                               else "Add to Playlist")
            for name in self.custom:
                sub.addAction(name, self._playlist_menu_action(binding, name,
                    lambda n=name, i=list(ids): self._on_tracks_dropped(n, i)))
        self._add_track_file_actions(menu, ids, reveal=bool(tid), binding=binding)
        add_recovery_actions(self, menu)
        exec_transient_menu(menu, self.artists_view.viewport().mapToGlobal(pos))

    def _on_artist_item_edited(self, item, col):
        """Inline title/artist edits in the Artists browser — same override
        machinery as Collection (_on_cell_edited: clamp, save, registry, art
        retry). The prepared grouping refreshes after the edit callback."""
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
        from .prepared_browser import clean_row, context as browser_context
        prepared=getattr(self,'_prepared_collection_refresh',None)
        baseline=getattr(self,'_collection_prepared',None)
        if (prepared is not None and baseline is not None
                and prepared[1]==baseline[1] and prepared[2]==baseline[0]
                and baseline[2] is self.model._all_rows):
            changed=set(prepared[0]);previous=self.model._all_rows
            by_id={row['id']:row for row in previous}
            # Collection is unique by logical ID. Native playlist occurrence
            # identity/order continues through the ordinary playlist renderer.
            sort_keys=baseline[3]
            if len(by_id)==len(previous) and by_id.keys()==sort_keys.keys():
                tracks=[self.lib.tracks_by_id[identity] for identity in changed if identity in by_id]
                updates={row['id']:row for row in self._annotate_art(
                    [self._row_for(track) for track in tracks])}
                revised={track.track_id:(-self._landed_time(track),track.name.casefold(),
                                         track.name.lower(),sort_keys[track.track_id][-1]) for track in tracks}
                raw_order_changed=any(sort_keys[identity]!=key for identity,key in revised.items())
                if raw_order_changed:
                    sort_keys={**sort_keys,**revised}
                    order=sorted(by_id,key=sort_keys.__getitem__)
                else:order=list(by_id)
                same_order=all(identity==row['id'] for identity,row in zip(order,previous,strict=True))
                if (not same_order or any(clean_row(row)!=clean_row(by_id[key]) for key,row in updates.items())):
                    if not same_order or not self.model.patch_rows(updates):
                        self.model.set_rows([updates.get(identity,by_id[identity]) for identity in order])
                self._collection_prepared=(prepared[2],self._cache_gen,self.model._all_rows,sort_keys)
                self.stack.setCurrentIndex(0)
                return
        tracks=self._collection_tracks()
        self._set_refreshed_rows(self._annotate_art(self._rows_for(tracks)))
        positions={identity:index for index,identity in enumerate(self.lib.tracks_by_id)} if self.lib else {}
        sort_keys={track.track_id:(-self._landed_time(track),track.name.casefold(),track.name.lower(),
                                  positions[track.track_id]) for track in tracks}
        self._collection_prepared=(browser_context(self),self._cache_gen,self.model._all_rows,sort_keys)
        self.stack.setCurrentIndex(0)

    def _show_custom(self, name: str):
        tracks = [t for i in self.custom.get(name, [])
                  if i not in self.hidden
                  and (t := self.lib.tracks_by_id.get(i) if self.lib else None)]
        self._set_refreshed_rows(self._annotate_art([self._row_for(track) for track in tracks]))
        self.stack.setCurrentIndex(0)

    # ---------- devices ----------
    def _custom_playlists(self) -> list:
        """Synthetic Playlist objects for the user's custom SoundBox playlists so
        drift/resync recognize them by identity (they aren't in lib.playlists).
        Mirrors the `cust` branch of _resolve_jobs."""
        custom = getattr(self, "custom", None) or {}
        state=self.unification_controller.state()
        if state and state['customs_done']:
            by_id={p.pid:p for p in self.lib.playlists}
            return [replace(by_id[node['id']],export_key='cust:'+key[7:])
                    for key,node in state['nodes'].items()
                    if key.startswith('custom:') and node['id'] in by_id]
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
            [p for p in self._custom_playlists() if not p.pid] + [p for p in views if p is not None])
        return self._sync_playlists_cache

    def _device_label(
            self, name: str, capacity, drift: int | None, fs: str = "",
            profile=None) -> str:
        kind = {'stock_ipod': 'iTunes iPod', 'rockbox': 'Rockbox iPod',
                'dual_boot': 'iTunes/Rockbox iPod', 'generic_usb': 'USB'}.get(
                    getattr(profile, 'kind', ''), getattr(profile, 'label', 'USB'))
        parts = [name, kind]
        if isinstance(capacity, (int, float)) and not isinstance(capacity, bool) and capacity > 0:
            parts.append(human(capacity))
        return ' · '.join(parts)

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
        """Read connection facts, iPod drift and explicitly requested USB views.

        The scan touches the removable volume, which can block for seconds while a
        copy/delete hammers it — so it runs OFF the UI thread (DeviceScanWorker) and
        the tree is updated in `_on_devices_scanned`. Single-flight: if a scan is
        already running we skip, so the 3s timer can never pile up workers."""
        if (getattr(self, "_isolated", False)
                or getattr(self, "_state_maintenance", False)
                or getattr(self, '_usb_format_device', '')):
            return
        force = force or getattr(self, "_device_mount_refresh_pending", False)
        profiles = getattr(self, '_device_profiles', {})
        if (not force and profiles
                and all(profile.kind == 'generic_usb' for profile in profiles.values())
                and not self._usb_view_requests
                and time.monotonic() < getattr(self, '_device_retry_at', float('inf'))):
            # Source edits do not concern an idle CDJ USB. Only mount changes,
            # retryable discovery and explicit browsing need connection reads.
            return
        if not self._device_coordinator.polling_allowed:
            return
        # iPod finalization owns its dashboard refresh before success. Its
        # completed-operation hold suppresses incidental scans until another
        # explicit operation or mount change releases it.
        if self._eject_ready_device and not force:
            return
        if force:
            self._release_eject_ready()
        # While OUR export/sync/delete is hammering the stick, don't also poll it —
        # the numbers are mid-flight and the extra reads only slow the operation down
        # (contention). iPod finalization and explicit USB browsing request the
        # connection/dashboard reads they actually need after ownership ends.
        if self._exporting or self._deleting or (getattr(self,'_export_batch',None) and not force):
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
        usb_requests = frozenset(self._usb_view_requests)
        w = DeviceScanWorker(
            None,
            defer_playlists=True,
            stock_models=(raw_models if isinstance(raw_models, dict) else {}),
            known_paths=known_paths,
            known_mount_tokens=dict(self._device_mount_tokens),
            usb_views=dict(self._usb_view_cache), usb_requests=usb_requests,
        )
        self._usb_view_requests.difference_update(usb_requests)
        self._device_mount_refresh_pending = False
        self._dev_scan_worker = w
        w.discovered.connect(self._on_devices_discovered)
        w.playlists_requested.connect(self._supply_device_scan_playlists, Qt.QueuedConnection)
        w.finished.connect(lambda ww=w: self._finish_device_scan_worker(ww))
        self._start_device_worker(w, "", "discover")

    def _supply_device_scan_playlists(self, worker):
        """Capture export inputs on the GUI thread after a target is detected."""
        if (self._dev_scan_worker is not worker or self._shutting_down
                or worker.cancellation.is_set()):
            worker.cancel()
            return
        try:
            extra=self._sync_playlists()
            captures=getattr(worker,'drift_captures',{})
            options={}
            stock_witnesses={}
            if captures and self.lib is not None:
                from .ipod_export_capture import effective_rows
                from soundbox.stock_ipod_catalog_state import StockCatalogWitness
                members={path:self._managed_ipod_display_ids(captured[0],extra)
                         for path,captured in captures.items() if captured is not None}
                source=(str(self.lib.source),self.lib.source_kind,self.lib.source_library_id)
                stock_witnesses={path:witness for path,witness in
                    getattr(self,'_stock_catalog_witness_cache',{}).items()
                    if path in captures and isinstance(witness,StockCatalogWitness) and witness.source==source}
                for path,witness in stock_witnesses.items():
                    members[path]=tuple(set(members.get(path,())) | {row.local_id for row in witness.rows})
                self._ipod_drift_member_ids=members
                self._ipod_drift_display_source=(str(self.lib.source),self.lib.source_library_id)
                options['display_rows']=effective_rows(self,
                    {identity for identities in members.values() for identity in identities})
            worker.provide_playlists(
                extra, lib=self.lib,
                artist_overrides=self.overrides, title_overrides=self.titles,
                stock_shadow_store=(self.stock_ipod_shadow_store if worker.stock_detected else None),
                stock_catalog_witnesses=stock_witnesses,
                stock_catalog_store=(getattr(self,'stock_catalog_witness_store',None)
                    if worker.stock_detected else None),**options)
        except Exception as exc:  # noqa: BLE001 — status remains unknown on failure
            LOG.warning("Device playlist capture failed: %s", exc)
            worker.provide_playlists((), lib=None, artist_overrides={},
                                     title_overrides={}, error=exc)

    def _managed_ipod_display_ids(self,manifest,extra):
        """Only managed members/current additions need idle display projection."""
        ids={identity for row in manifest.get('playlists',{}).values() if isinstance(row,dict)
             for identity in (row.get('tracks',()) if isinstance(row.get('tracks',()),(list,tuple)) else ())
             if isinstance(identity,str)}
        if self.lib is not None:
            ids.update(track.track_id for playlist in device_manifest.managed_current_playlists(manifest,self.lib,extra)
                       for track in self.lib.resolve(playlist))
        return ids

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
        elif not self._eject_ready_device:
            profiles = getattr(self, '_device_profiles', {})
            # USB connection is handled by mount notifications/kernel hints.
            # A mounted CDJ USB needs no recurring source or catalog inspection.
            needs_poll = any(profile.kind != 'generic_usb' for profile in profiles.values())
            retry = time.monotonic() >= getattr(self, "_device_retry_at", float("inf"))
            if needs_poll or retry or getattr(self, '_usb_view_requests', ()):
                self._refresh_devices()

    def _set_device_poll_interval(self, devices_present: bool) -> None:
        """Keep live drift responsive without busy-scanning an empty Mac."""
        self._dev_timer.setInterval(
            _DEVICE_POLL_ACTIVE_MS
            if devices_present else _DEVICE_POLL_IDLE_MS)

    def _device_operation_owned(self, target: str | None = None) -> bool:
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
            (getattr(self, '_usb_format_device', '')
             and (not getattr(self, '_usb_format_started', False)
                  or target is None
                  or target in getattr(self, '_usb_format_volumes', (self._usb_format_device,))))
            or
            (coordinator.active is not None
             and coordinator.active.phase != Phase.DISCOVERING)
            or coordinator.eject_pending
            or coordinator.stopping
            or self._stock_view_warming
            or self._stock_view_pending
            or self._pending_device_operation is not None
            or getattr(self, '_cancelled_source_syncs', None)
            or self._exporting
            or self._deleting
            or any(getattr(self, name, None) is not None for name in owners)
        )

    def _stock_has_sync_source(self, dev: str) -> bool:
        """A cached playlist association changes the action, not media authority."""
        from .stock_catalog_sync import has_sync_source
        return has_sync_source(self, dev)

    def _device_primary_action(self):
        from .stock_catalog_sync import needs_sync
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        kind = getattr(profile, 'kind', '')
        if ((kind == 'stock_ipod' and needs_sync(self, dev))
                or (kind in {'rockbox', 'dual_boot'} and self._drift_cache.get(dev))):
            self._sync_device(dev)
        else:
            self._show_export_dialog(dev)

    def _update_device_action_state(self) -> None:
        """Make device action availability match actual transaction ownership."""
        busy = (self._device_operation_owned() or bool(getattr(self,'_export_batch',None))
                or getattr(self, '_device_finalization_refreshing', False))
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

        # Formatting reserves its own drive. The ordinary export lane remains
        # available for another connected USB, using only cached target facts.
        if (getattr(self, 'device_sync_btn', None) is not None
                and getattr(self._device_profiles.get(self._current_device_path), 'kind', '')=='generic_usb'):
            export_ready = bool(self._current_device_path) and not (
                self._device_operation_owned(self._current_device_path)
                or getattr(self, '_export_batch', None))
            self.device_sync_btn.setEnabled(export_ready)
            if export_ready:
                self.device_sync_btn.setToolTip('')

        button = getattr(self, 'device_sync_btn', None)
        if button is not None:
            from .stock_catalog_sync import needs_sync
            profile = self._device_profiles.get(self._current_device_path)
            kind = getattr(profile, 'kind', '')
            pending = kind == 'stock_ipod' and needs_sync(self, self._current_device_path)
            caption = 'Sync' if pending or (kind in {'rockbox', 'dual_boot'}
                and bool(self._drift_cache.get(self._current_device_path))) else 'Export'
            if button.text() != caption:
                button.setText(caption)
            if ready and kind in {'stock_ipod', 'dual_boot'}:
                from .devices import device_export_readiness
                cached = self._stock_view_cache.get(self._current_device_path)
                readiness = device_export_readiness(profile,
                    device_facts(self._current_device_path, cached_only=True),
                    self._device_mount_tokens.get(self._current_device_path, 0),
                    stock_snapshot=cached[0] if cached is not None else None)
                button.setEnabled(readiness.ready)
                button.setToolTip(readiness.reason)
            if self.lib is None:
                button.setEnabled(False)
                button.setToolTip('Open a library to export playlists')
            pages = self.__dict__.get('stock_ipod_pages')
            if pages is not None:
                pages.set_primary_action(button.text(),
                    button.isEnabled() and kind in {'stock_ipod', 'dual_boot'},
                    button.toolTip())

        active = self._device_coordinator.active
        if (getattr(self, "eject_btn", None) is not None
                and self._current_device_path
                and active is not None
                and active.phase in {Phase.DISCOVERING, Phase.INSPECTING}
                and not self._exporting
                and not getattr(self,'_device_finalization_refreshing',False)
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
                self._stock_catalog_witness_cache.pop(path, None)
                self._stock_view_failures.pop(path, None)
                self._dual_sync_queue.pop(path, None)
            if token:
                self._device_mount_tokens[path] = token
                self._device_coordinator.observe(DeviceSessionIdentity(path, token))
            if remounted:
                self._stock_view_pending.pop(path, None)
                self._discard_stock_view_callbacks(path, 'The iPod remounted before its refresh')
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
                    if self.stack.currentIndex() == 1:
                        self.stack.setCurrentIndex(0)
                        self._default_to_collection()
                self._device_profiles[path] = profile
                self._drift_cache.setdefault(path, None)
            if path not in current:
                item = QTreeWidgetItem([f"⛃  {name} · Checking…"])
                item.setData(0, DEVPATH_ROLE, path)
                item.setToolTip(0, "Connected — reading device details")
                item.setDisabled(True)
                self.device_tree.addTopLevelItem(item)
                current.add(path)

    def _deliver_device_scan_callbacks(self, worker):
        callbacks = getattr(worker, "_surface_refresh_callbacks", ())
        worker._surface_refresh_callbacks = []
        current=[]
        for callback in callbacks:
            owner=getattr(callback,'_finalization_owner',None)
            if getattr(self,'_device_finalization_token',None) is not owner:
                callback()  # The once wrapper retires without touching a newer owner.
            else:current.append(callback)
        callbacks=current
        if not callbacks:
            return
        # Discovery marks future dashboard work as warming before this slot.
        # That queued work owns no worker yet and must not disable completion's
        # Eject state. Restore it only after the modal callback releases its hold.
        warming = set(self._stock_view_warming)
        pending = dict(self._stock_view_pending)
        self._stock_view_warming.clear()
        self._stock_view_pending.clear()
        try:
            for callback in callbacks:
                callback()
        finally:
            for path in warming | set(pending):
                profile = self._device_profiles.get(path)
                if profile is not None and path not in self._stock_view_cache:
                    self._stock_view_pending.setdefault(path, profile)
                    if path in warming:
                        self._stock_view_warming.add(path)

    def _finish_device_scan_worker(self, worker):
        current = self._dev_scan_worker is worker
        callbacks = tuple(getattr(worker, '_operation_resume_callbacks', ()))
        worker._operation_resume_callbacks = []
        retired = False
        try:
            self._retire_device_scan_worker(worker)
            retired = True
        finally:
            cancellation = getattr(worker, 'cancellation', None)
            eligible = (retired and current and self._dev_scan_worker is None
                        and not (cancellation is not None and cancellation.is_set()))
            for callback in callbacks:
                callback(eligible)

    def _retire_device_scan_worker(self, worker):
        if self._dev_scan_worker is not worker:
            self._finish_worker("_dev_scan_worker", worker)
            self._deliver_device_scan_callbacks(worker)
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
        cancellation = getattr(worker, "cancellation", None)
        cancelled = cancellation is not None and cancellation.is_set()
        # Clear ownership before publishing any clickable row. A result emitted
        # from run() is not enough: QThread.finished is the actual boundary that
        # Eject and every other device operation use.
        self._finish_worker("_dev_scan_worker", worker)
        if cancelled or self._shutting_down or self._device_coordinator.eject_pending:
            owned_callbacks=any(getattr(callback,'_finalization_owner',None) is getattr(self,'_device_finalization_token',None)
                for callback in getattr(worker,'_surface_refresh_callbacks',()))
            if not self._shutting_down and owned_callbacks:
                self._device_finalization_error = RuntimeError("The final device scan was cancelled")
                self._deliver_device_scan_callbacks(worker)
            else:
                self._deliver_device_scan_callbacks(worker)
            return
        if results is not None:
            self._usb_view_cache.update(usb_snapshots)
            self._on_devices_scanned(results, mount_tokens=mount_tokens)
            if getattr(worker,'stock_status',None):
                self._publish_stock_sync_observations(worker)
            if not self.settings.get('cdj_my_settings'):
                native_profiles=[s for s in usb_snapshots.values() if getattr(s,'native',None) is not None]
                if len(native_profiles)==1:self._sync_usb_my_settings(native_profiles[0])
        callbacks = getattr(worker, "_surface_refresh_callbacks", ())
        owned_callbacks=any(getattr(callback,'_finalization_owner',None) is getattr(self,'_device_finalization_token',None)
            for callback in callbacks)
        if owned_callbacks and results is None:
            self._device_finalization_error = RuntimeError("The final device scan did not return a result")
        self._deliver_device_scan_callbacks(worker)
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
            if callbacks:
                # Completion's dialog owns the ready/Eject state. A different
                # newly attached iPod can preload on the next ordinary poll;
                # do not start another blocking owner after reporting success.
                self._stock_view_pending[path] = profile
                continue
            self._stock_view_warming.add(path)
            self._start_ipod_loading_status()
            self._read_stock_device_view(path, profile)
            queued = True
        if (not callbacks and not queued and self._stock_view_pending
                and self._stock_view_worker is None):
            next_dev = next(iter(self._stock_view_pending))
            profile = self._stock_view_pending.pop(next_dev)
            self._read_stock_device_view(next_dev, profile)

    def _publish_stock_sync_observations(self, worker):
        """Publish scalar status only for the same source, mount and native view."""
        library = self.lib
        scope = ((str(library.source),library.source_kind,library.source_library_id)
                 if library is not None else None)
        if scope is None or getattr(worker,'captured_source_scope',None) != scope:
            return
        updated = False
        for dev,status in getattr(worker,'stock_status',{}).items():
            cached = self._stock_view_cache.get(dev)
            token = getattr(worker,'mount_tokens',{}).get(dev,0)
            if (cached is None or not token or self._device_mount_tokens.get(dev) != token
                    or getattr(self._device_profiles.get(dev),'kind','') != 'stock_ipod'
                    or not getattr(status,'database_sha256','')):
                continue
            observed_art=getattr(status,'artwork_database_sha256',None)
            observed_playback=getattr(status,'play_counts_sha256',None)
            if (status.database_sha256 != cached[0].database_sha256
                    or observed_art is not None and observed_art != cached[0].artwork_database_sha256
                    or observed_playback is not None
                    and observed_playback != getattr(cached[0], 'play_counts_sha256', '')):
                # This exact scan already observed changed catalogs or history.
                # Reuse the serialized view lane rather than asking the user
                # to reconnect or weakening a later plan's freshness guard.
                self._stock_view_pending.setdefault(dev,self._device_profiles[dev])
                continue
            from soundbox.stock_ipod_catalog_state import StockCatalogWitness
            witness=getattr(worker,'stock_catalog_witnesses',{}).get(dev)
            if (isinstance(witness,StockCatalogWitness) and witness.source==scope
                    and witness.device_key==cached[0].identity_key
                    and witness.database_sha256==status.database_sha256
                    and (observed_art is None or witness.artwork_database_sha256==observed_art)
                    and self._stock_catalog_witness_cache.get(dev)!=witness):
                # Let the existing owned native reader publish a cold cache.
                # The next scan can then capture its exact managed row IDs.
                self._stock_view_pending.setdefault(dev,self._device_profiles[dev])
            snapshot = replace(cached[0],sync_state=status.state,sync_detail=status.detail,
                sync_local_changes=status.local_changes,sync_committed_at=status.committed_at)
            if snapshot == cached[0]:
                continue
            self._stock_view_cache[dev] = (snapshot,cached[1])
            if dev == self._current_device_path:
                self._stock_snapshot = snapshot
                self.stock_ipod_pages.set_sync_status(status.state,status.detail,
                    status.local_changes,status.committed_at)
            updated = True
        if updated:
            self._update_device_action_state()

    def _defer_device_operation_until_scan_finishes(self, callback, *, target='') -> bool:
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
        def identities():
            paths = (target,) if target else tuple(self._device_mount_tokens)
            return {path: (self._device_mount_tokens.get(path, 0),
                           device_facts(path, cached_only=True).volume_uuid) for path in paths}
        authority = identities()
        captured_batch = getattr(self, '_export_batch', None)
        captured_target = captured_batch.get('active') if captured_batch else None
        self._pending_device_operation = callback
        self._exporting = True
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Waiting for USB" if target and
            getattr(self._device_profiles.get(target),'kind','')=='generic_usb' else "Waiting for iPod")

        def resume(retired_current):
            if self._pending_device_operation is not callback:
                return
            self._pending_device_operation = None
            if (getattr(self, '_export_batch', None) is not captured_batch
                    or captured_batch is not None and captured_batch.get('active') is not captured_target):
                rejected = getattr(callback, '_device_wait_rejected', None)
                if rejected is not None:rejected()
                return
            active = self._device_coordinator.active
            if ((active is not None and active.phase != Phase.DISCOVERING)
                    or getattr(self, '_usb_format_device', '')):
                rejected = getattr(callback, '_device_wait_rejected', None)
                if rejected is not None:rejected()
                request = getattr(callback, '_catalog_request', None)
                if request is not None:
                    from .stock_catalog_sync import _end_deferred_catalog
                    _end_deferred_catalog(self, request, 'A different device operation started; retry the waiting Sync')
                elif captured_batch is not None:
                    captured_batch['error'] = 'A different device operation started; retry the waiting export'
                    self._maybe_finish_export_target()
                if active is None or active.phase == Phase.DISCOVERING:
                    self._exporting = False
                    self._cancelling = False
                return
            self._exporting = False
            self._stop_pulse()
            fresh = identities()
            if (not retired_current or self._shutting_down
                    or self._device_coordinator.eject_pending or authority != fresh):
                rejected = getattr(callback, '_device_wait_rejected', None)
                if rejected is not None:rejected()
                batch=getattr(self,'_export_batch',None)
                if batch:
                    batch['error']='Connected devices changed while waiting; choose the targets again'
                    self._maybe_finish_export_target()
                return
            try:callback()
            except Exception as exc:
                rejected = getattr(callback, '_device_wait_rejected', None)
                if rejected is not None:rejected()
                batch=getattr(self,'_export_batch',None)
                if not batch:raise
                batch['error']=str(exc)
                self._maybe_finish_export_target()

        # The scan may have emitted finished while its existing owner callback
        # is still queued. Delivery belongs to that retirement callback, so a
        # late UI request cannot miss the signal and strand the waiting state.
        callbacks = getattr(worker, '_operation_resume_callbacks', None)
        if callbacks is None:
            callbacks = []
            worker._operation_resume_callbacks = callbacks
        callbacks.append(resume)
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
        self._update_export_preparation()
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
            previous_token = self._device_mount_tokens.get(path, 0)
            remounted = bool(token and previous_token and token != previous_token)
            if remounted:
                # The preview may be skipped during a drag. This finished scan
                # independently retires the previous occupant of a reused path.
                self._stock_view_cache.pop(path, None)
                self._stock_catalog_witness_cache.pop(path, None)
                self._stock_view_failures.pop(path, None)
                self._dual_sync_queue.pop(path, None)
                profile = self._device_profiles.get(path)
                if getattr(profile, 'has_stock_ipod', False):
                    self._stock_view_warming.add(path)
                    self._stock_view_pending[path] = profile
                    self._start_ipod_loading_status()
                if self._current_device_path == path:
                    self._current_device_path = ''
                    self._stock_snapshot = None
                    if self.stack.currentIndex() == 1:
                        self.stack.setCurrentIndex(0)
                        self._default_to_collection()
            if token:
                self._device_mount_tokens[path] = token
                self._device_coordinator.observe(DeviceSessionIdentity(path, token))
            if remounted:
                self._discard_stock_view_callbacks(path, 'The iPod remounted before its refresh')
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
            self._stock_catalog_witness_cache.pop(stale, None)
            self._stock_view_failures.pop(stale, None)
            self._stock_view_warming.discard(stale)
        for stale in set(self._device_mount_tokens) - set(all_paths):
            self._device_mount_tokens.pop(stale, None)
            self._device_coordinator.disconnect(stale)
            self._stock_view_pending.pop(stale, None)
            self._discard_stock_view_callbacks(stale, 'The iPod disconnected before its refresh')
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
            if self.stack.currentIndex() == 1:
                self.stack.setCurrentIndex(0)
                self._default_to_collection()
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
                    capacity = getattr(device_facts(path, cached_only=True), 'capacity_bytes', 0)
                    it.setText(0, self._device_label(
                        name, capacity, drift, device_fs(path), profile))
                    it.setToolTip(0, self._device_tip(path, free, profile,
                                                   capacity=capacity, drift=drift))
            self._finish_ipod_loading_status()
            return
        self.device_tree.clear()
        for name, path, free, drift, profile in visible_devs:
            capacity = getattr(device_facts(path, cached_only=True), 'capacity_bytes', 0)
            it = QTreeWidgetItem([self._device_label(
                name, capacity, drift, device_fs(path), profile)])
            it.setData(0, DEVPATH_ROLE, path)
            it.setToolTip(0, self._device_tip(path, free, profile,
                                           capacity=capacity, drift=drift))
            self.device_tree.addTopLevelItem(it)
        self._finish_ipod_loading_status()

    def _stock_device_key(self, dev: str, profile=None) -> str:
        from soundbox.stock_ipod_info import stock_ipod_device_key
        profile = profile or self._device_profiles.get(dev)
        library_id = int(
            getattr(profile, "library_persistent_id", 0) or 0)
        return stock_ipod_device_key(
            device_facts(dev, cached_only=True).volume_uuid, library_id)

    def _stored_stock_model(self, dev: str, profile=None) -> str:
        from soundbox.stock_ipod_identity import normalize_display_model, hardware_model
        profile_model = normalize_display_model(
            str(getattr(profile, "model", "") or ""))
        if hardware_model(profile_model) is not None:
            return profile_model
        values = self.settings.get("stock_ipod_models")
        if not isinstance(values, dict):
            return ""
        model = normalize_display_model(
            str(values.get(self._stock_device_key(dev, profile)) or ""))
        description = hardware_model(model)
        family = getattr(device_facts(dev, cached_only=True), 'usb_family', '')
        if description is not None and family in {'iPod Video', 'iPod Classic'} and description.family != family:
            return ""
        return model if description is not None else ""

    def _effective_device_profile(self, dev: str, profile):
        if getattr(profile, "kind", "") != "stock_ipod":
            return profile
        model = self._stored_stock_model(dev, profile)
        if not model or model == getattr(profile, "model", ""):
            return profile
        if not hasattr(profile, "__dataclass_fields__"):
            return profile
        return replace(profile, model=model)

    def _remember_stock_model(self, identity_key: str, model: str):
        from soundbox.stock_ipod_identity import normalize_display_model, hardware_model
        model = normalize_display_model(model)
        if not identity_key or hardware_model(model) is None:
            return
        raw = self.settings.get("stock_ipod_models")
        values = dict(raw) if isinstance(raw, dict) else {}
        if values.get(identity_key) == model:
            return
        values[identity_key] = model
        self.settings["stock_ipod_models"] = values
        self._schedule_settings_save('stock_ipod_models')

    def _device_tip(self, path: str, free, profile=None, *, capacity=0, drift=0) -> str:
        fs = device_fs(path) or "unknown"
        size = human(capacity) if capacity > 0 else 'unavailable'
        tip = f"{path}\nFilesystem: {fs} · Capacity: {size} · {human(free)} free"
        if getattr(profile, 'kind', '') != 'generic_usb':
            if drift is None:tip += '\nSync status pending'
            elif drift:tip += '\n⚠ Sync needed'
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
                tip += "\n" + (getattr(getattr(profile, 'stock_format', None), 'reason', '')
                    or "Reading iPod database capabilities")
            elif profile.stock_write:
                tip += "\niTunes export and Sync available."
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
            self.device_sync_btn.setText('Export')
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
                        and self._stock_snapshot.target == Path(dev))
                    self.stock_ipod_pages.set_loading(
                        preserve_page=preserve_snapshot)
                    if not preserve_snapshot:
                        self._set_stock_device_tab(0)
                    self._read_stock_device_view(dev, profile)
            else:
                self.device_content_stack.setCurrentIndex(2)
                self._present_usb_device(dev)
                self._set_device_tab(self.usb_device_pages.stack.currentIndex(), refresh=True)
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

    def _set_device_tab(self, index, *, refresh=False):
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
        if refresh and self._current_device_path:
            path = self._current_device_path
            worker = self._dev_scan_worker
            if worker is not None and path in getattr(worker, 'usb_requests', ()):
                return  # One explicit read is already serving this browser.
            self._usb_view_requests.add(path)
            self._refresh_devices()

    def _set_stock_device_tab(self, index: int):
        if not self.device_tab_buttons:
            return
        index = max(0, min(2, int(index)))
        self.device_tab_buttons[index].setChecked(True)
        self.stock_ipod_pages.set_page(index)

    def _read_stock_device_view(
            self, dev: str, profile=None, *, after_finished=None):
        from soundbox.stock_ipod import inspect_stock_ipod_sync_status
        from soundbox.stock_ipod_info import read_stock_ipod_snapshot
        from soundbox.stock_ipod_capture import capture_stock_ipod_native
        if after_finished is not None:
            owner=getattr(self,'_device_finalization_token',None)
            identities=getattr(self,'_device_finalization_mounts',{})
            identity=(identities.get(dev) if owner is not None else
                      self._device_coordinator.mounts.get(dev))
            self._stock_view_callbacks.setdefault(dev, []).append(
                _DeviceRefreshCompletion(self,dev,identity,owner,after_finished))
        if (getattr(self, "_state_maintenance", False) or self._shutting_down
                or self._device_coordinator.eject_pending):
            self._discard_stock_view_callbacks(dev,'The device refresh was stopped before admission')
            return
        if (self._stock_view_worker is not None
                or self._dev_scan_worker is not None
                or self._device_coordinator.busy):
            self._stock_view_pending[dev] = profile
            return
        self._stock_view_pending.pop(dev, None)
        from .workers import OneShotTask
        model = self._stored_stock_model(dev, profile)
        library = self.lib
        extra_playlists = tuple(self._sync_playlists())
        artist_overrides = dict(self.overrides)
        title_overrides = dict(self.titles)
        display_rows=None
        display_ids=set()
        if (library is not None and getattr(self,'_ipod_drift_display_source',None)
                ==(str(library.source),library.source_library_id)):
            display_ids.update(getattr(self,'_ipod_drift_member_ids',{}).get(dev,()))
        from soundbox.stock_ipod_catalog_state import StockCatalogWitness
        prior_witness=getattr(self,'_stock_catalog_witness_cache',{}).get(dev)
        if (library is not None and isinstance(prior_witness,StockCatalogWitness)
                and prior_witness.source==(str(library.source),library.source_kind,library.source_library_id)):
            display_ids.update(row.local_id for row in prior_witness.rows)
        if display_ids:
            from .ipod_export_capture import effective_rows
            display_rows=effective_rows(self,display_ids)
        witness_store = self.stock_catalog_witness_store
        observed_witness=None

        def read_snapshot():
            nonlocal observed_witness
            facts = device_facts(dev)
            composite_capture = None
            pre_composite_dual = False
            if getattr(profile, "kind", "") == "dual_boot":
                from soundbox.sync import DeviceSession
                composite_path = DeviceSession(Path(dev), [], create=False).guard(
                    device_manifest.manifest_path(dev))
                if composite_path.exists():
                    composite_capture = device_manifest.read_composite(dev)
                else:
                    pre_composite_dual = True
            native_capture = capture_stock_ipod_native(
                dev, include_artwork=library is not None and not pre_composite_dual)
            snapshot = read_stock_ipod_snapshot(
                dev,
                model_number=model,
                observed_model_number=getattr(facts, "model_number", ""),
                observed_model_source=getattr(facts, "model_source", ""),
                identity_conflict=getattr(facts, "identity_conflict", False),
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
                firewire_guid=getattr(facts, "firewire_guid", ""),
                firmware_version=getattr(facts, "firmware_version", ""),
                firmware_source=getattr(facts, "firmware_source", ""),
                transport_revision=getattr(facts, "transport_revision", ""),
                usb_family=getattr(facts, "usb_family", ""),
                native_capture=native_capture,
            )
            observed_witness=witness_store.load(snapshot.identity_key)
            composite_data: CompositeDeviceData | None = None
            if composite_capture is not None:
                composite_manifest = composite_capture[0]
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
                native_capture=native_capture,
                catalog_witness=observed_witness,
                display_rows=display_rows,
            )
            if composite_data is not None:
                from soundbox.dual_ipod import composite_device_drift
                drift = composite_device_drift(
                    library,
                    dev,
                    list(extra_playlists),
                    artist_overrides=artist_overrides,
                    title_overrides=title_overrides,
                    captured_manifest=composite_capture,
                    display_rows=display_rows,
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

        def read_with_witness():
            snapshot, composite = read_snapshot()
            witness = observed_witness
            from soundbox.stock_ipod_catalog_state import StockCatalogWitness
            if (isinstance(witness, StockCatalogWitness) and library is not None
                    and witness.source == (str(library.source), library.source_kind, library.source_library_id)
                    and snapshot.sync_state in {'', 'untracked'}):
                from datetime import datetime, timezone
                try:
                    modified = witness_store.path_for(snapshot.identity_key).stat().st_mtime
                    snapshot = replace(snapshot, sync_committed_at=datetime.fromtimestamp(modified, timezone.utc).isoformat())
                except OSError:
                    pass
            return snapshot, composite, witness

        worker = OneShotTask(read_with_witness)
        worker._stock_view_device=dev
        worker._stock_view_callbacks=self._stock_view_callbacks.pop(dev,[])
        self._stock_view_worker = worker
        self._stock_view_device = dev
        worker.finished.connect(
            lambda ww=worker: self._finish_stock_device_view_worker(ww))
        self._start_device_worker(worker, dev, "inspect")

    def _discard_stock_view_callbacks(self, dev, message):
        """Retire queued continuations without handing them to a later mount."""
        callbacks=self._stock_view_callbacks.pop(dev,())
        for completion in callbacks:
            QTimer.singleShot(0, lambda value=completion: value.finish(RuntimeError(message)))

    def _finish_stock_device_view_worker(self, worker):
        callbacks=getattr(worker,'_stock_view_callbacks',())
        worker._stock_view_callbacks=[]
        def completed(error=None):
            for completion in callbacks:completion.finish(error)
        if self._stock_view_worker is not worker:
            self._finish_worker("_stock_view_worker", worker)
            completed(RuntimeError('The original device refresh was superseded'))
            return
        dev = getattr(worker,'_stock_view_device',self._stock_view_device)
        self._stock_view_device = ""
        result = getattr(worker, "result", None)
        command = getattr(worker, "device_command", None)
        current = command is None or self._device_coordinator.current(command)
        self._finish_worker("_stock_view_worker", worker)
        if not current:
            # Discovery owns replacement caches. An old reader may retire its
            # own continuation, never delete a new occupant's published view.
            completed(RuntimeError('The iPod disconnected or remounted during its refresh'))
            return
        if self._shutting_down or self._device_coordinator.eject_pending:
            completed(RuntimeError('The device refresh was stopped'))
            return
        owned_callbacks=any(completion.owner is getattr(self,'_device_finalization_token',None)
            for completion in callbacks)
        if isinstance(result, Exception) and self._exporting and owned_callbacks:
            self._device_finalization_error = result
        self._on_stock_device_view(dev, result)
        completed(result if isinstance(result,Exception) else None)
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
        """Refresh a stock iPod's cached catalog, then sidebar state, in series.

        A post-write completion callback runs only after both reads finish, so
        a success dialog never claims the iPod is done while Eject still has to
        wait for SoundBox's own finalization worker.
        """
        owner = getattr(self, '_device_finalization_token', None)
        identity = (getattr(self, '_device_finalization_mounts', {}).get(dev)
                    if owner is not None else self._device_coordinator.mounts.get(dev))
        def finish_refresh():
            if getattr(self, '_device_finalization_token', None) is not owner:
                return
            if self._shutting_down or identity is None or self._device_coordinator.mounts.get(dev) != identity:
                if after_finished is not None:
                    after_finished()
                return
            self._set_device_progress(surface_progress)
            self._finish_device_surface_refresh(
                after_finished, expected_dev=dev, expected_identity=identity, owner=owner)

        if identity is None or self._device_coordinator.mounts.get(dev) != identity:
            if owner is not None:
                self._device_finalization_error = RuntimeError(
                    f'{Path(dev).name} disconnected or remounted during finalization')
            if after_finished is not None:
                after_finished()
            return
        profile = self._device_profiles.get(dev)
        if (
            profile is None
            or not getattr(profile, "has_stock_ipod", False)
        ):
            finish_refresh()
            return
        self._exporting = True
        # The accepted export may have started from Collection or another
        # device. Its cached native catalog must still become current before
        # completion, without changing the page the user is viewing.
        if dev == self._current_device_path:
            self.stock_ipod_pages.set_loading(preserve_page=True)

        self._read_stock_device_view(
            dev, profile, after_finished=finish_refresh)

    def _finish_device_surface_refresh(
            self, after_finished=None, *, expected_dev: str = "",
            expected_identity=None, owner=None):
        """Run the sidebar scan and optionally wait for its worker as well."""
        if getattr(self, '_device_finalization_token', None) is not owner:
            return
        if self._shutting_down:
            if after_finished is not None:
                after_finished()
            return
        self._exporting = False
        # A new completed operation supersedes any earlier hold and must always
        # run its own final sidebar/dashboard scan before success is reported.
        self._refresh_devices(force=True)
        if after_finished is None:
            return
        worker = getattr(self, "_dev_scan_worker", None)
        completed = False
        def finish_sidebar():
            nonlocal completed
            if completed:
                return
            completed = True
            if getattr(self, '_device_finalization_token', None) is not owner:
                return
            self._exporting = False
            if expected_dev and (expected_dev not in self._device_profiles or
                    (expected_identity is not None and
                     self._device_coordinator.mounts.get(expected_dev) != expected_identity)):
                self._device_finalization_error = RuntimeError(
                    f"{Path(expected_dev).name} disconnected or remounted during finalization")
            after_finished()

        finish_sidebar._finalization_owner = owner
        if worker is None:
            finish_sidebar()
            return
        self._exporting = True

        # The worker may have emitted finished already while its owner slot is
        # queued. Attach to that slot's publication boundary, not a new signal
        # connection which could miss the emission and strand finalization.
        callbacks = getattr(worker, "_surface_refresh_callbacks", None)
        if callbacks is None:
            callbacks = []
            worker._surface_refresh_callbacks = callbacks
        callbacks.append(finish_sidebar)

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

    def _device_completion_message(self,title,text,*,attention=False,summary='',issues=()):
        from .export_dialog import device_attention_text
        issues=tuple(dict.fromkeys(str(issue).strip() for issue in issues if str(issue).strip()))
        self._last_device_completion=dict(title=title,text=text,attention=bool(attention),summary=summary,issues=issues)
        LOG.info('%s: %s',title,text)
        if issues:LOG.warning('Device operation needs attention: %s','\n'.join(issues))
        attention_text=device_attention_text(issues) if attention else ''
        batch=getattr(self,'_export_batch',None)
        if batch and batch.get('active'):
            if summary:
                target=batch['active']
                batch.setdefault('summaries',{})[target['path']]=(target.get('name','iPod'),summary)
            if attention and batch.get('outcome') not in {'failed','cancelled'}:
                batch['outcome']='attention'
            self._export_target_complete(batch['active']['path'],attention_text if attention else title+'\n'+text)
        elif not batch:
            if attention:QMessageBox.warning(self,'iPod needs attention',attention_text)

    def _finalize_ipod_operation(
            self, dev: str, on_complete, *, terminal_text: str = "Complete ✓") -> None:
        """Finish every post-write read before presenting success to the user."""
        from .export_dialog import present_ipod_failure
        owner = object()
        identity = self._device_coordinator.mounts.get(dev)
        previous = self._device_coordinator.last_result
        self._device_finalization_token = owner
        self._device_finalization_mounts = {dev: identity}
        self._device_finalization_refreshing = True
        self._exporting = True
        self._device_finalization_error = None
        self._set_device_progress(max(800, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Finalizing iPod")

        def finish():
            if self._device_finalization_token is not owner:
                return
            if self._shutting_down:
                self._device_finalization_token = None
                self._device_finalization_refreshing = False
                self._exporting = False
                return
            if identity is None or self._device_coordinator.mounts.get(dev) != identity:
                self._device_finalization_error = RuntimeError(
                    f'{Path(dev).name} disconnected or remounted during finalization')
            self._device_finalization_refreshing = False
            self._set_device_progress(1000)
            self._stop_pulse(force=True)
            finalization_error = self._device_finalization_error
            MainWindow._restore_playlist_order_drop(self,dev)
            if previous is not None and self._device_coordinator.last_result is previous:
                self._device_coordinator.last_result = replace(
                    previous, refresh="failed" if finalization_error else "complete")
            final_text = (
                "Final refresh failed" if finalization_error else terminal_text)
            self._set_status_text(final_text, force=True)
            # The committed dashboard/sidebar scan just finished. Freeze that
            # verified state through the success dialog. Release the hold on
            # dismissal so later curation edits and reconnects remain visible.
            self._eject_ready_device = (
                dev if identity is not None and self._device_coordinator.mounts.get(dev) == identity else '')
            # QMessageBox runs a nested Qt event loop. Keep polling suppressed
            # through that loop or the three-second timer can launch a fresh,
            # slow device scan while the user is still reading "Eject ready".
            self._exporting = False
            self._update_device_action_state()
            try:
                if finalization_error is not None:
                    self._export_target_result(dev,finalization_error)
                    present_ipod_failure(self,dev,"Device finalization failed",finalization_error,
                        committed=bool(previous and previous.committed),
                        guidance="Eject is available; reconnect before another write.")
                else:
                    on_complete()
            finally:
                if self._device_finalization_token is owner:
                    self._device_finalization_token = None
                    self._device_finalization_mounts = {}
                    self.progress.setVisible(False)
                    self._release_eject_ready()
                    self._maybe_finish_export_target()

        self._refresh_device_surfaces(dev, after_finished=finish)

    def _finalize_device_operations(
            self, devices, on_complete, *,
            terminal_text: str = "Complete ✓") -> None:
        """Finalize every distinct target before one multi-device result.

        ExportWorker can accept another drop while it is active, including a
        different target. The transaction is therefore not terminal until each
        touched volume has completed its own dashboard/sidebar refresh.
        """
        from .export_dialog import present_ipod_failure
        ordered = tuple(dict.fromkeys(str(dev) for dev in devices if dev))
        if not ordered:
            self.progress.setVisible(False)
            on_complete()
            return
        if len(ordered) == 1:
            self._finalize_ipod_operation(
                ordered[0], on_complete, terminal_text=terminal_text)
            return

        owner = object()
        identities = {dev: self._device_coordinator.mounts.get(dev) for dev in ordered}
        previous = self._device_coordinator.last_result
        self._device_finalization_token = owner
        self._device_finalization_mounts = identities
        self._device_finalization_refreshing = True
        self._exporting = True
        self._device_finalization_error = None
        self._set_device_progress(max(800, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Finalizing devices")

        def finish_all():
            if self._device_finalization_token is not owner:
                return
            if self._shutting_down:
                self._device_finalization_token = None
                self._device_finalization_refreshing = False
                self._exporting = False
                return
            for dev, identity in identities.items():
                if identity is None or self._device_coordinator.mounts.get(dev) != identity:
                    self._device_finalization_error = RuntimeError(
                        f'{Path(dev).name} disconnected or remounted during finalization')
            self._device_finalization_refreshing = False
            self._set_device_progress(1000)
            self._stop_pulse(force=True)
            finalization_error = self._device_finalization_error
            if previous is not None and self._device_coordinator.last_result is previous:
                self._device_coordinator.last_result = replace(
                    previous, refresh='failed' if finalization_error else 'complete')
            final_text = (
                "Final refresh failed" if finalization_error else terminal_text)
            self._set_status_text(final_text, force=True)
            hold = (
                self._current_device_path
                if self._current_device_path in ordered else ordered[-1])
            self._eject_ready_device = (
                hold if identities[hold] is not None and
                self._device_coordinator.mounts.get(hold) == identities[hold] else '')
            self._exporting = False
            self._update_device_action_state()
            try:
                if finalization_error is not None:
                    batch=getattr(self,'_export_batch',None)
                    target=batch.get('active') if batch else None
                    failed_device=target['path'] if target and target.get('path') in ordered else ordered[0]
                    present_ipod_failure(self,failed_device,"Device finalization failed",finalization_error,
                        committed=bool(previous and previous.committed),
                        guidance="Eject is available; reconnect before another write.")
                else:
                    on_complete()
            finally:
                if self._device_finalization_token is owner:
                    self._device_finalization_token = None
                    self._device_finalization_mounts = {}
                    self.progress.setVisible(False)
                    self._release_eject_ready()
                    self._maybe_finish_export_target()

        def finalize_at(index: int):
            if self._device_finalization_token is not owner:
                return
            if index >= len(ordered):
                finish_all()
                return
            start = 800 + round(200 * index / len(ordered))
            end = 800 + round(200 * (index + 1) / len(ordered))
            self._exporting = True
            self._set_device_progress(max(start, self.progress.value()))

            def finish_one():
                if self._device_finalization_token is not owner:
                    return
                self._set_device_progress(end)
                finalize_at(index + 1)

            self._refresh_device_surfaces(
                ordered[index],
                surface_progress=start + (end - start) // 2,
                after_finished=finish_one,
            )

        finalize_at(0)

    def _on_stock_device_view(self, dev: str, result):
        from soundbox.stock_ipod_info import StockIpodSnapshot
        if isinstance(result, Exception):
            self._fail_initial_ipod_preload(dev)
            if dev != self._current_device_path:
                LOG.warning("iPod dashboard preload failed for %s: %s", dev, result)
                return
            preserve_snapshot = bool(
                self._stock_snapshot is not None
                and self._stock_snapshot.target == Path(dev))
            if not preserve_snapshot:
                self._stock_snapshot = None
            self.stock_ipod_pages.set_error(
                str(result), preserve_snapshot=preserve_snapshot)
            if preserve_snapshot:
                self._flash_status(
                    "iPod refresh failed — showing last verified state")
            return
        composite_data = None
        witness = None
        witness_loaded = False
        if (
            isinstance(result, tuple)
            and len(result) in (2, 3)
            and isinstance(result[0], StockIpodSnapshot)
            and (
                result[1] is None
                or isinstance(result[1], CompositeDeviceData)
            )
        ):
            if len(result) == 3:
                witness = result[2]
                witness_loaded = True
            result, composite_data = result[:2]
        if not isinstance(result, StockIpodSnapshot):
            self._fail_initial_ipod_preload(dev)
            if dev != self._current_device_path:
                LOG.warning("Unexpected iPod dashboard preload result for %s", dev)
                return
            preserve_snapshot = bool(
                self._stock_snapshot is not None
                and self._stock_snapshot.target == Path(dev))
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
        inherited_model = result.model
        result = remember_identity(result, identity_records)
        if result.identity_conflict or inherited_model and not result.model:
            from soundbox.stock_ipod_identity import normalize_display_model
            legacy = self.settings.get('stock_ipod_models')
            if (isinstance(legacy, dict) and result.identity_key in legacy
                    and (result.identity_conflict
                         or normalize_display_model(legacy.get(result.identity_key, ''))
                         == normalize_display_model(inherited_model))):
                legacy = dict(legacy)
                legacy.pop(result.identity_key, None)
                self.settings['stock_ipod_models'] = legacy
                self._schedule_settings_save('stock_ipod_models')
        if identity_records != raw_identity:
            self.settings["stock_ipod_identity"] = identity_records
            self._schedule_settings_save('stock_ipod_identity')
        self._stock_view_failures.pop(dev, None)
        if not witness_loaded:
            witness = self._stock_catalog_witness_cache.get(dev)
        from soundbox.stock_ipod_catalog_state import StockCatalogWitness
        if not isinstance(witness, StockCatalogWitness) or witness.device_key != result.identity_key:
            witness = None
        if (witness is not None and self.lib is not None and witness.bindings
                and witness.source == (str(self.lib.source), self.lib.source_kind, self.lib.source_library_id)
                and any(binding.device_playlist_id in {row.playlist_id for row in result.playlists
                    if row.kind == 'Playlist'} for binding in witness.bindings)
                and result.sync_state in {'', 'untracked'}):
            result = replace(result, sync_state='catalog_linked',
                sync_detail='Existing playlists are linked for Sync')
        self._stock_view_cache[dev] = (result, composite_data)
        self._stock_catalog_witness_cache[dev] = witness
        art_worker = self._stock_art_worker
        if (art_worker is not None and result.stock_format is not None
                and result.stock_format.supports_artwork
                and result.stock_format.artwork_profile not in getattr(art_worker, 'profiles', ())):
            self._stock_art_rescan = True
        self._schedule_stock_artwork()
        self._stock_view_warming.discard(dev)
        self._finish_ipod_loading_status()
        self._update_device_action_state()
        if dev != self._current_device_path:
            LOG.info(
                "iPod dashboard preloaded: %s (%d tracks, %d playlists)",
                dev, len(result.tracks), len(result.playlists))
            return
        previous_snapshot = self._stock_snapshot
        from soundbox.stock_ipod_info import playback_refresh_compatible
        playback_only = (composite_data is None and isinstance(previous_snapshot, StockIpodSnapshot)
            and previous_snapshot.play_counts_sha256 != result.play_counts_sha256
            and playback_refresh_compatible(previous_snapshot, result))
        self._stock_snapshot = result
        self._restoring_stock_view = True
        try:
            if not (playback_only and self.stock_ipod_pages.set_playback_snapshot(result)):
                self.stock_ipod_pages.set_snapshot(result, composite_data)
                self._restore_stock_device_view()
                self.stock_ipod_pages.restore_playlist_order_seat()
        finally:
            self._restoring_stock_view = False
        profile = self._device_profiles.get(dev)
        self.stock_ipod_pages.set_playlist_deletion_available(
            getattr(profile, "kind", "") in {"stock_ipod", "dual_boot"})
        self._update_device_action_state()

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
                    ('Export…',lambda:self._show_export_dialog(dev)),
                    ('Import “My Settings”',lambda:self._import_usb_my_settings(dev)),
                    ('Reformat USB…',lambda:self._reformat_usb(dev)),
                    ('Eject',lambda:self._eject(dev))):
                action=menu.addAction(title,callback)
                action.setEnabled(not (self._device_operation_owned(dev) if title=='Export…' else busy))
            exec_transient_menu(menu, self.device_tree.mapToGlobal(pos))
            return
        menu.addAction("Browse", lambda: self._on_device_click(item, 0))
        busy = self._device_operation_owned()
        profile = self._device_profiles.get(dev)
        if getattr(profile, 'kind', '') in {'stock_ipod', 'dual_boot', 'rockbox'}:
            action = menu.addAction('Export playlists…', lambda: self._show_export_dialog(dev))
            action.setEnabled(not busy)
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
        if getattr(profile, 'kind', '') != 'stock_ipod' or self._stock_has_sync_source(dev):
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
        exec_transient_menu(menu, self.device_tree.mapToGlobal(pos))

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
            self, "iTunes Sync readiness",
            f"Device: {profile.label}"
            f"{f' ({profile.model})' if profile.model else ''}\n"
            f"Tracks visible to planner: {plan['track_count']}\n"
            f"Formats: {formats}\n\n"
            f"{'Ready for an explicit playlist dry-run.' if plan['writable'] else 'Not ready for a whole-library plan.'}\n"
            f"{chr(10).join(plan['blockers']) if plan['blockers'] else 'Drop a small MP3/WAV playlist onto the iPod to plan it.'}\n\n"
            "This readiness check made no changes.")

    def _stock_write_access(self, dev: str, profile=None):
        """Use observed catalog capabilities; a saved model never grants writes."""
        from soundbox.device_readiness import stock_write_readiness
        profile = profile or self._device_profiles.get(dev)
        cached = self._stock_view_cache.get(dev)
        snapshot = cached[0] if cached is not None else None
        write_format = getattr(snapshot, 'stock_format', None)
        if write_format is None:
            write_format = getattr(profile, 'stock_format', None)
        facts = device_facts(dev, cached_only=True)
        readiness = stock_write_readiness(writable=facts.writable,
            filesystem=device_fs(dev) or getattr(snapshot, 'filesystem', ''),
            stock_format=write_format,
            firewire_guid=getattr(facts, 'firewire_guid', ''))
        if not readiness.ready:
            self._export_target_result(dev,RuntimeError(readiness.reason))
            self._flash_status(readiness.reason, 15)
            return None
        return write_format

    def _begin_stock_ipod_sync(
            self, dev: str, playlists: list[Playlist] | None, *, reconcile: bool = False,
            _source_ready=False, _export_capture=None):
        """Run the expensive, read-only database/media plan off the UI thread."""
        if self._shutting_down or self._state_maintenance:
            return
        if _export_capture is None and playlists and getattr(self, '_stock_view_cache', {}).get(dev):
            from .stock_catalog_sync import request_sync, source_sync_selection
            selections = source_sync_selection(self, dev, playlists)
            if selections:
                request_sync(self, selections, device=dev, source_playlists=playlists)
                return
        if (playlists is None or not reconcile) and _export_capture is None:
            self._request_ipod_export_capture('stock',dev,playlists,reconcile=reconcile)
            return
        if _export_capture is not None:
            _export_capture.require_current(self,dev)
        from .export_wait import defer_source_sync
        if _export_capture is None and defer_source_sync(self,'stock',dev,playlists,_ready=_source_ready,reconcile=reconcile):
            return
        from soundbox.stock_ipod import StockIpodSyncPlan,plan_stock_ipod_sync
        if self._device_operation_owned():
            self._export_target_result(dev,RuntimeError('Wait for the current device operation'))
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_stock_ipod_sync(
                    dev, playlists, reconcile=reconcile,_export_capture=_export_capture)):
            return
        from .export_wait import sync_source_identity
        source_identity=sync_source_identity(self,dev,playlists)
        profile = self._device_profiles.get(dev)
        if self._stock_write_access(dev, profile) is None:
            return
        model = str(getattr(profile, 'model', '') or '')
        if source_identity!=sync_source_identity(self,dev,playlists):
            self._export_target_result(dev,RuntimeError('Library, playlist or connected iPod changed; choose the target again'))
            self._flash_status('Library, playlist or connected iPod changed; choose the target again',15)
            return
        from .cdj import _source_edits_pending
        if _export_capture is None and _source_edits_pending(self):
            defer_source_sync(self,'stock',dev,playlists,reconcile=reconcile)
            return
        self._release_eject_ready()
        from .workers import OneShotTask
        from soundbox.export_snapshot import capture_export
        captured = (_export_capture.snapshot if _export_capture is not None else
            capture_export(self.lib, playlists or (), extra=self._sync_playlists(),
                           titles=self.titles, artists=self.overrides, complete=True))
        facts = device_facts(dev, cached_only=True)
        self._validate_export_target(dev,facts)
        identity_key = self._stock_device_key(dev, profile)
        # First construction stays on the GUI owner, shared with the artwork
        # warmer; cached_property does not serialize concurrent first access.
        stock_art_cache = self.stock_art_cache
        from .export_dialog import captured_ipod_noop_result,verify_ipod_export_capture
        art_cache=self.art_cache
        witness_store=self.stock_catalog_witness_store
        previous_witness = getattr(self, '_stock_catalog_witness_cache', {}).get(dev)
        def plan():
            verify_ipod_export_capture(_export_capture,art_cache)
            inputs = captured.materialize()
            library, extra_playlists = inputs.library, inputs.extra
            facts = device_facts(dev)
            shadow = self.stock_ipod_shadow_store.load(identity_key) if reconcile else None
            selected = None if playlists is None else list(inputs.playlists)
            if selected is None:
                if shadow is None:
                    raise ValueError(
                        "Drag a playlist onto this iPod once to establish its stock-sync baseline.")
                selected = device_manifest.managed_current_playlists(
                    {"playlists": shadow.playlists}, library, extra_playlists)
            managed = (shadow.playlists if shadow else {}) or {}
            sources = (shadow.sources if shadow else {}) or {}
            prior_witness = (previous_witness if previous_witness is not None
                             else witness_store.load(identity_key))
            planned=plan_stock_ipod_sync(
                library,
                selected,
                dev,
                model_number=model,
                filesystem=device_fs(dev),
                derive_artists=True,
                artist_overrides=inputs.artists,
                title_overrides=inputs.titles,
                art_cache=self.art_cache,
                stock_art_cache=stock_art_cache,
                identity_key=identity_key,
                serial_number=getattr(facts, "hardware_serial", ""),
                firewire_guid=getattr(facts, "firewire_guid", ""),
                usb_family=getattr(facts, "usb_family", ""),
                shadow_store=self.stock_ipod_shadow_store,
                managed_playlist_names_before=tuple(
                    str(row.get('device_name') or " / ".join([
                        *map(str, row.get("folder_path") or []), str(row.get("name") or "")]))
                    for row in managed.values() if isinstance(row, dict)),
                managed_playlist_keys_before=tuple(str(key) for key in managed),
                managed_track_rows_before=tuple(sorted({
                    (int(row.get("dbid") or 0), str(row.get("device_path") or ""), str(row.get("sha256") or ""))
                    for row in sources.values() if isinstance(row, dict) and int(row.get("dbid") or 0) > 0})),
                display_rows=inputs.display_rows,
                catalog_witness=prior_witness,
                extra_playlists=tuple(inputs.extra),
            )
            verify_ipod_export_capture(_export_capture,art_cache)
            if _export_capture is not None and isinstance(planned,StockIpodSyncPlan) and not planned.requires_commit:
                return planned,captured_ipod_noop_result(planned,witness_store)
            return planned

        worker = OneShotTask(plan)
        self._stock_plan_worker = worker
        self._exporting = True
        self._set_device_progress(0)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_stock_plan_worker", ww,
                lambda result: self._on_stock_ipod_plan(d, result,_export_capture=_export_capture)))
        self._start_device_worker(worker, dev, "plan")

    def _on_stock_ipod_plan(self, dev: str, result,*,_export_capture=None):
        from soundbox.stock_ipod import StockIpodSyncPlan
        from soundbox.operation_control import OperationCancelled
        from .export_dialog import present_ipod_failure,stock_export_summary
        noop_result=None
        if isinstance(result,tuple) and len(result)==2 and isinstance(result[0],StockIpodSyncPlan):
            result,noop_result=result
        self._exporting = False
        self._cancelling = False
        self.cancel_btn.setVisible(False)
        self._stop_pulse()
        if _export_capture is not None and not isinstance(result, OperationCancelled):
            try:_export_capture.require_current(self,dev)
            except Exception as exc:result=exc
        if isinstance(result, Exception):
            self._export_target_result(dev,result)
            self.progress.setVisible(False)
            if isinstance(result, OperationCancelled):
                from .export_wait import retain_source_cancellation
                if not retain_source_cancellation(self):
                    self._flash_status('iPod export cancelled — no device changes were committed',15)
                self._update_device_action_state()
                return
            self._set_status_text("Ready")
            present_ipod_failure(self,dev,"iTunes plan refused",result,phase='plan')
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodSyncPlan):
            self.progress.setVisible(False)
            self._export_target_result(dev,RuntimeError('Unexpected iPod plan result'))
            QMessageBox.critical(self, "iTunes plan failed", "Unexpected plan result.")
            return
        if noop_result is not None:
            self._publish_ipod_export_witness(dev,noop_result)
            self._export_target_result(dev,noop_result)
        self._set_device_progress(150)
        summary_suffix,omission_details=stock_export_summary(result)
        if not result.requires_commit:
            def complete():
                self._device_completion_message(
                    "iTunes is up to date",
                    "The read-only comparison found no media, playlist, metadata, "
                    "artwork, or playback changes to publish."
                    + omission_details
                    + ('\n\n'+getattr(noop_result,'catalog_notice','') if noop_result and getattr(noop_result,'catalog_notice','') else '')
                    + ('\n\n'+noop_result.catalog_warning if noop_result and noop_result.catalog_warning else ''),
                    attention=bool(noop_result and noop_result.catalog_warning),
                    summary=summary_suffix,
                    issues=(noop_result.catalog_warning,) if noop_result and noop_result.catalog_warning else (),
                )
                self._flash_status(('iPod up to date — needs attention'
                    if noop_result and noop_result.catalog_warning else 'iPod up to date')+summary_suffix)

            self._finalize_ipod_operation(dev, complete)
            return
        self._commit_stock_ipod_plan(dev, result,_export_capture=_export_capture)

    def _commit_stock_ipod_plan(self, dev: str, plan: StockIpodSyncPlan,*,_export_capture=None):
        from soundbox.stock_ipod import commit_stock_ipod_sync
        from .workers import OneShotTask
        from .export_dialog import persist_ipod_export_witness,verify_ipod_export_capture
        if _export_capture is not None:
            try:_export_capture.require_current(self,dev)
            except Exception as exc:
                self._on_stock_ipod_plan(dev,exc);return
        store=self.stock_catalog_witness_store
        art_cache=self.art_cache
        def commit():
            verify_ipod_export_capture(_export_capture,art_cache)
            return persist_ipod_export_witness(commit_stock_ipod_sync(plan),store,plan.identity_key)
        worker = OneShotTask(commit)
        self._stock_commit_worker = worker
        self._exporting = True
        self._set_device_progress(max(150, self.progress.value()))
        self.cancel_btn.setVisible(False)
        self._start_pulse("Syncing iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_device_result_worker(
                "_stock_commit_worker", ww,
                lambda result: self._on_stock_ipod_commit(d, result,plan=plan)))
        self._start_device_worker(worker, dev, "commit")

    def _on_stock_ipod_commit(self, dev: str, result,*,plan=None):
        from soundbox.operation_control import OperationCancelled
        from .export_dialog import present_ipod_failure,device_result_warnings,stock_export_summary
        self._exporting = False
        self._cancelling = False
        self.cancel_btn.setVisible(False)
        self._stop_pulse()
        if (isinstance(result, OperationCancelled) and not getattr(result,'committed',False)
                and getattr(result,'recovery_status',None) in {None,'not needed','rolled back'}):
            self._export_target_result(dev,result)
            self.progress.setVisible(False)
            from .export_wait import retain_source_cancellation
            if not retain_source_cancellation(self):
                self._flash_status('iPod Sync cancelled — previous library kept',15)
            self._update_device_action_state()
            return
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            present_ipod_failure(self,dev,"iTunes Sync failed",result,phase='commit',critical=True)
        else:
            self._publish_ipod_export_witness(dev,result)
            self._set_device_progress(800)
            warning_parts=device_result_warnings(result)
            warning = (
                "\n\n" + "\n\n".join(warning_parts)
                if warning_parts else "")
            summary_suffix,omission_details=stock_export_summary(plan)
            def complete():
                self._device_completion_message(
                    "iTunes Sync complete",
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
                    f"Eject from SoundBox before listening.{omission_details}{warning}"
                    + ('\n\n'+getattr(result,'catalog_notice','') if getattr(result,'catalog_notice','') else ''),
                    attention=bool(warning_parts),
                    summary=summary_suffix,
                    issues=warning_parts,
                )
                self._flash_status(('iPod synced — needs attention' if warning_parts else 'iPod synced')+summary_suffix)

            self._finalize_ipod_operation(dev, complete)
        if isinstance(result, Exception):
            self._refresh_devices()

    def _publish_ipod_export_witness(self,dev,result):
        from soundbox.stock_ipod_catalog_state import StockCatalogWitness
        stock=getattr(result,'stock',result)
        witness=getattr(stock,'catalog_witness',None)
        if (isinstance(witness,StockCatalogWitness)
                and witness.device_key==self._stock_device_key(dev,self._device_profiles.get(dev))):
            self._stock_catalog_witness_cache[dev]=witness

    def _begin_dual_ipod_sync(
            self,
            dev: str,
            playlists: list[Playlist] | None,
            *,
            reconcile: bool = False,
            respect_selected_order: bool = False,
            preconfirmed: bool = False,
            confirmed: bool = False,
            action: str = "sync",
            expected_playlist_rows: dict[str, dict] | None = None,
            expected_manifest: dict | None = None,
            audit_existing: bool = False,
            _source_ready=False,
            _export_capture=None,
    ):
        """Build the composite stock/Rockbox plan without target writes."""
        if self._shutting_down or self._state_maintenance:
            MainWindow._restore_playlist_order_drop(self,dev)
            return
        new_export=(action=='sync' and (playlists is None or not reconcile)
                    and expected_manifest is None and expected_playlist_rows is None)
        if (new_export or action in {'delete','order'}) and _export_capture is None:
            self._request_ipod_export_capture('dual',dev,playlists,reconcile=reconcile,
                respect_selected_order=respect_selected_order,preconfirmed=preconfirmed,confirmed=confirmed,
                action=action,expected_playlist_rows=expected_playlist_rows,expected_manifest=expected_manifest,
                audit_existing=audit_existing)
            return
        if _export_capture is not None:
            _export_capture.require_current(self,dev)
            if expected_manifest is None:
                expected_manifest=_export_capture.expected_manifest
        from .export_wait import defer_source_sync
        if _export_capture is None and defer_source_sync(self,'dual',dev,playlists,_ready=_source_ready,reconcile=reconcile,
                respect_selected_order=respect_selected_order,preconfirmed=preconfirmed,confirmed=confirmed,
                action=action,expected_playlist_rows=expected_playlist_rows,expected_manifest=expected_manifest,
                audit_existing=audit_existing):
            return
        from soundbox.dual_ipod import DualIpodSyncPlan,plan_dual_ipod_sync
        if self._device_operation_owned():
            MainWindow._restore_playlist_order_drop(self,dev)
            self._export_target_result(dev,RuntimeError('Wait for the current device operation'))
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        def resume():
            self._begin_dual_ipod_sync(
                    dev,
                    playlists,
                    reconcile=reconcile,
                    respect_selected_order=respect_selected_order,
                    preconfirmed=preconfirmed,
                    confirmed=confirmed,
                    action=action,
                    expected_playlist_rows=expected_playlist_rows,
                    expected_manifest=expected_manifest,
                    audit_existing=audit_existing,
                    _export_capture=_export_capture)
        resume._device_wait_rejected = lambda:MainWindow._restore_playlist_order_drop(self,dev)
        if self._defer_device_operation_until_scan_finishes(resume, target=dev):
            return
        from .export_wait import sync_source_identity
        source_identity=sync_source_identity(self,dev,playlists)
        profile = self._device_profiles.get(dev)
        if self._stock_write_access(dev, profile) is None:
            MainWindow._restore_playlist_order_drop(self,dev)
            return
        model = str(getattr(profile, 'model', '') or '')
        if source_identity!=sync_source_identity(self,dev,playlists):
            MainWindow._restore_playlist_order_drop(self,dev)
            self._export_target_result(dev,RuntimeError('Library, playlist or connected iPod changed; choose the target again'))
            self._flash_status('Library, playlist or connected iPod changed; choose the target again',15)
            return
        from .cdj import _source_edits_pending
        if _export_capture is None and _source_edits_pending(self):
            defer_source_sync(self,'dual',dev,playlists,reconcile=reconcile,
                    respect_selected_order=respect_selected_order,preconfirmed=preconfirmed,confirmed=confirmed,
                    action=action,expected_playlist_rows=expected_playlist_rows,expected_manifest=expected_manifest,
                    audit_existing=audit_existing)
            return
        facts = device_facts(dev, cached_only=True)
        self._validate_export_target(dev,facts)
        self._release_eject_ready()
        self._dual_active_device = dev
        self._dual_active_playlist_keys = {
            device_manifest.playlist_key(playlist) for playlist in (playlists or ())}
        self._dual_active_preconfirmed = preconfirmed
        self._dual_active_confirmed = confirmed
        self._dual_active_action = action
        self._dual_active_track_total = (_export_capture.track_total if _export_capture is not None else
            sum(len(self.lib.resolve(playlist)) for playlist in (playlists or ())))
        from .workers import ProgressTask
        from soundbox.export_snapshot import capture_export
        captured = (_export_capture.snapshot if _export_capture is not None else
            capture_export(self.lib, playlists or (), extra=self._sync_playlists(),
                           titles=self.titles, artists=self.overrides, complete=True))
        identity_key = self._stock_device_key(dev, profile)
        adoptable_playlist_keys = {f"cust:{name}" for name in self.custom}
        state=self.unification_controller.state()
        if state and state['customs_done']:
            adoptable_playlist_keys-={'cust:'+key[7:] for key in state['nodes'] if key.startswith('custom:')}
        stock_art_cache = self.stock_art_cache
        expected_manifest = deepcopy(expected_manifest)
        expected_playlist_rows = deepcopy(expected_playlist_rows)
        from .export_dialog import captured_ipod_noop_result,verify_ipod_export_capture
        art_cache=self.art_cache
        witness_store=self.stock_catalog_witness_store
        previous_witness = getattr(self, '_stock_catalog_witness_cache', {}).get(dev)

        def plan(callback):
            verify_ipod_export_capture(_export_capture,art_cache)
            inputs = captured.materialize()
            facts = device_facts(dev)
            prior_witness = (previous_witness if previous_witness is not None
                             else witness_store.load(identity_key))
            planned=plan_dual_ipod_sync(
                inputs.library,
                None if playlists is None else list(inputs.playlists),
                dev,
                model_number=model,
                filesystem=device_fs(dev),
                rockbox_media_cache=self.rockbox_media_cache,
                rockbox_art_cache=self.rockbox_art_cache,
                derive_artists=True,
                artist_overrides=inputs.artists,
                title_overrides=inputs.titles,
                art_cache=self.art_cache,
                stock_art_cache=stock_art_cache,
                stock_shadow_store=self.stock_ipod_shadow_store,
                identity_key=identity_key,
                serial_number=getattr(facts, "hardware_serial", ""),
                firewire_guid=getattr(facts, "firewire_guid", ""),
                usb_family=getattr(facts, "usb_family", ""),
                reconcile=reconcile,
                respect_selected_order=respect_selected_order,
                on_progress=callback,
                expected_playlist_rows=expected_playlist_rows,
                extra_playlists=inputs.extra,
                expected_manifest=expected_manifest,
                expected_manifest_absent=bool(_export_capture is not None and _export_capture.expected_manifest_absent),
                adoptable_playlist_keys=adoptable_playlist_keys,
                audit_existing=audit_existing,
                display_rows=inputs.display_rows,
                catalog_witness=prior_witness,
            )
            verify_ipod_export_capture(_export_capture,art_cache)
            if _export_capture is not None and isinstance(planned,DualIpodSyncPlan) and not planned.requires_commit:
                return planned,captured_ipod_noop_result(planned,witness_store)
            return planned

        worker = ProgressTask(
            plan,
            log_label="Dual iPod planning",
        )
        worker.playlist_order_drop = getattr(self,'_playlist_order_drop',None)
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
                lambda result: self._on_dual_ipod_plan(d, result,_export_capture=_export_capture)))
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

    def _on_dual_ipod_plan(self, dev: str, result,*,_export_capture=None):
        from soundbox.dual_ipod import DualIpodSyncPlan
        from soundbox.operation_control import OperationCancelled
        from .export_dialog import present_ipod_failure
        noop_result=None
        if isinstance(result,tuple) and len(result)==2 and isinstance(result[0],DualIpodSyncPlan):
            result,noop_result=result
        self._cancelling = False
        self.cancel_btn.setVisible(False)
        self._stop_pulse()
        if _export_capture is not None and not isinstance(result, OperationCancelled):
            try:_export_capture.require_current(self,dev)
            except Exception as exc:result=exc
        if isinstance(result, Exception):
            MainWindow._restore_playlist_order_drop(self,dev)
            self._export_target_result(dev,result)
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
            if isinstance(result, OperationCancelled):
                from .export_wait import retain_source_cancellation
                if not retain_source_cancellation(self):
                    self._flash_status('iPod export cancelled — no device changes were committed',15)
                self._update_device_action_state()
                return
            present_ipod_failure(self,dev,"iTunes/Rockbox plan refused",result,phase='plan',
                guidance="Remaining queued jobs were cancelled." if was_preconfirmed else '')
            self._refresh_devices()
            return
        if not isinstance(result, DualIpodSyncPlan):
            MainWindow._restore_playlist_order_drop(self,dev)
            self._export_target_result(dev,RuntimeError('Unexpected iTunes/Rockbox plan result'))
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
                self, "iTunes/Rockbox plan failed", "Unexpected plan result.")
            return
        if noop_result is not None:
            self._publish_ipod_export_witness(dev,noop_result)
            self._export_target_result(dev,noop_result)
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
                self._dual_batch_results.append((result, noop_result))
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
                self._device_completion_message(
                    "Nothing to delete" if action == "delete" else "iPod is up to date",
                    (
                        "The selected playlists are already absent and no managed "
                        "media became unreferenced."
                        if action == "delete" else
                        "The no-write plan found no stock database, media, artwork, "
                        "playlist, or ownership-manifest changes."
                    ) + ('\n\n'+getattr(noop_result.stock,'catalog_notice','')
                         if noop_result and getattr(noop_result.stock,'catalog_notice','') else '')
                      + ('\n\n'+noop_result.stock.catalog_warning
                         if noop_result and noop_result.stock.catalog_warning else ''),
                    attention=bool(noop_result and noop_result.stock.catalog_warning),
                    issues=(noop_result.stock.catalog_warning,)
                        if noop_result and noop_result.stock.catalog_warning else (),
                )
                self._flash_status(
                    ('Nothing to delete' if action=='delete' else 'iPod up to date')
                    + (' — needs attention' if noop_result and noop_result.stock.catalog_warning else ''))
                queued = self._dual_sync_queue.pop(dev, [])
                if queued:
                    worker = self._dual_plan_worker

                    def start_queued():
                        QTimer.singleShot(
                            0, self, lambda: self._begin_dual_ipod_sync(dev, queued))

                    if worker is not None:
                        worker.finished.connect(start_queued)
                    else:
                        start_queued()

            self._finalize_ipod_operation(dev, complete)
            return
        if (self._dual_active_preconfirmed or self._dual_active_confirmed
                or self._dual_active_action=='sync'):
            self._commit_dual_ipod_plan(dev, result,_export_capture=_export_capture)
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
            f"• {summary['stock_safe_sources']} iTunes/Rockbox\n"
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
            self._export_target_result(dev,OperationCancelled('Export cancelled'))
            return
        self._commit_dual_ipod_plan(dev, result,_export_capture=_export_capture)

    def _commit_dual_ipod_plan(self, dev: str, plan: DualIpodSyncPlan,*,_export_capture=None):
        from soundbox.dual_ipod import commit_dual_ipod_sync
        from .workers import ProgressTask
        from .export_dialog import present_ipod_failure
        if _export_capture is not None:
            try:_export_capture.require_current(self,dev)
            except Exception as exc:
                self._on_dual_ipod_plan(dev,exc);return
        try:
            self._persist_rockbox_playlist_additions(plan)
        except RuntimeError as exc:
            self._export_target_result(dev,exc)
            self._exporting = False
            self._dual_active_device = ""
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = "sync"
            self._dual_active_track_total = 0
            self.progress.setVisible(False)
            self._set_status_count()
            present_ipod_failure(self,dev,"Could not save playlist update",exc,phase='plan',critical=True)
            self._refresh_devices()
            return
        from .export_dialog import persist_ipod_export_witness,verify_ipod_export_capture
        store=self.stock_catalog_witness_store
        art_cache=self.art_cache
        def commit(callback):
            verify_ipod_export_capture(_export_capture,art_cache)
            return persist_ipod_export_witness(commit_dual_ipod_sync(plan,on_progress=callback),
                                              store,plan.stock.identity_key)
        worker = ProgressTask(
            commit,
            log_label="Dual iPod commit",
        )
        worker.playlist_order_drop = getattr(self,'_playlist_order_drop',None)
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
        from soundbox.operation_control import OperationCancelled
        if isinstance(result, Exception):MainWindow._restore_playlist_order_drop(self,dev)
        self._exporting = False
        self._cancelling = False
        self.cancel_btn.setVisible(False)
        self._stop_pulse()
        if (isinstance(result, OperationCancelled) and not getattr(result,'committed',False)
                and getattr(result,'recovery_status',None) in {None,'not needed','rolled back'}):
            self._export_target_result(dev,result)
            self._dual_active_device = ''
            self._dual_active_playlist_keys.clear()
            self._dual_active_preconfirmed = False
            self._dual_active_confirmed = False
            self._dual_active_action = 'sync'
            self._dual_active_track_total = 0
            self._dual_sync_queue.pop(dev,None)
            self._reset_dual_batch()
            try:self.rockbox_media_cache.clear()
            except Exception as exc:LOG.warning('Rockbox media staging cleanup failed: %s',exc)
            self.progress.setVisible(False)
            from .export_wait import retain_source_cancellation
            if not retain_source_cancellation(self):
                self._flash_status('iPod Sync cancelled — previous library kept',15)
            self._update_device_action_state()
            return
        if not isinstance(result,Exception):self._publish_ipod_export_witness(dev,result)
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
                self._show_dual_ipod_failure(result,dev=dev)
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
            self._show_dual_ipod_failure(result,dev=dev)
        else:
            self._set_device_progress(800)
            from .export_dialog import device_result_warnings
            warnings=device_result_warnings(result)
            cleanup_warning='\n\n'+'\n'.join(warnings) if warnings else ''
            adopted_note = (
                f"\n\nAdopted {adopted_additions} Rockbox playlist "
                "addition(s)."
                if adopted_additions else ""
            )
            def complete():
                self._device_completion_message(
                    "iPod deletion complete"
                    if action == "delete" else "iPod sync complete",
                    f"iTunes: copied {result.stock.copied_tracks}, sanitized "
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
                    "Eject is ready."
                    + ('\n\n'+getattr(result.stock,'catalog_notice','') if getattr(result.stock,'catalog_notice','') else ''),
                    attention=bool(cleanup_warning),
                    issues=warnings,
                )
                self._flash_status('iPod synced — needs attention' if cleanup_warning else 'iPod synced')
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
                        0, self, lambda: self._start_confirmed_dual_jobs(dev, queued))

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

    def _show_dual_ipod_failure(self, result,*,dev=None) -> None:
        from .export_dialog import present_ipod_failure
        device=dev or getattr(self,'_dual_active_device','') or getattr(self,'_current_device_path','')
        present_ipod_failure(self,device,"iTunes/Rockbox Sync failed",result,phase='commit',critical=True)

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
                    self,
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
        from .export_dialog import device_result_notices,device_result_warnings
        warnings=tuple(dict.fromkeys(note for result in committed for note in device_result_warnings(result)))
        notices=tuple(dict.fromkeys(note for result in committed for note in device_result_notices(result)))

        def complete():
            self._device_completion_message(
                "iPod queue complete",
                f"{job_count} playlist job"
                f"{'s' if job_count != 1 else ''} complete\n"
                f"{track_count} queued track entries\n\n"
                f"{playlist_names}\n\n"
                f"Media copied: {copied}\n"
                f"Media metadata refreshed: {refreshed}\n"
                f"Unreferenced files removed: {removed}\n\n"
                "Eject is ready."
                + ('\n\n'+'\n'.join(notices) if notices else '')
                + ('\n\n'+'\n'.join(warnings) if warnings else ''),
                attention=bool(warnings),
                summary=f' · {track_count} song entries',
                issues=warnings,
            )
            self._flash_status('iPod synced — needs attention' if warnings else 'iPod synced')
            self._reset_dual_batch()

        self._finalize_ipod_operation(dev, complete)

    def _begin_stock_playlist_delete(self, selections, *, _intent=None):
        """Plan deletion of the checked user playlists from the stock iPod."""
        from soundbox.stock_ipod import plan_stock_ipod_playlist_delete
        if self._shutting_down or self._state_maintenance:
            return
        dev = _intent[0] if _intent is not None else self._current_device_path
        cached = self._stock_view_cache.get(dev)
        snapshot = cached[0] if cached is not None else self._stock_snapshot
        if not dev or snapshot is None:
            return
        profile = self._device_profiles.get(dev)
        dual_boot = getattr(profile, "kind", "") == "dual_boot"
        if dual_boot and cached is None:
            QMessageBox.warning(self, "Device changed", "Refresh the iPod before deleting.")
            return
        pre_composite_dual = bool(
            dual_boot
            and cached is not None and cached[1] is None)
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
        if snapshot.target != Path(dev):
            QMessageBox.warning(
                self, "Device changed", "Refresh the stock iPod before deleting.")
            return
        identity = self._device_coordinator.mounts.get(dev)
        intent = _intent or (dev, identity, tuple(identities), False)
        def current_intent():
            current = self._stock_view_cache.get(dev)
            observed = current[0] if current is not None else snapshot
            headers = [(row.playlist_id, row.title) for row in observed.playlists]
            return (not self._shutting_down and not self._state_maintenance
                and identity is not None and self._device_coordinator.mounts.get(dev) == intent[1]
                and observed.target == Path(dev)
                and all(headers.count(pair) == 1
                    and sum(row[0] == pair[0] for row in headers) == 1 for pair in intent[2]))
        if tuple(identities) != intent[2] or not current_intent():
            self._flash_status('The selected iPod playlists changed; choose them again', 15)
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_stock_playlist_delete(raw_selections, _intent=intent)):
            return
        profile = self._device_profiles.get(dev)
        if self._stock_write_access(dev, profile) is None:
            return
        if not intent[3]:
            titles = [title for _identity, title in identities]
            shown = [text[:117]+'…' if len(text)>120 else text for text in titles[:3]]
            label = ', '.join(shown) + (f' and {len(titles)-3} more' if len(titles)>3 else '')
            title = 'Delete iPod playlist?' if len(titles)==1 else 'Delete iPod playlists?'
            answer = QMessageBox.question(self, title,
                f'Delete {label} from {Path(dev).name}?\n\n'
                'Songs unused by other playlists will also be removed from this iPod.',
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                self._flash_status('iPod deletion cancelled — nothing changed')
                return
            intent = (*intent[:3], True)
        if not current_intent():
            self._flash_status('The selected iPod playlists changed; choose them again', 15)
            return
        profile = self._device_profiles.get(dev)
        if self._stock_write_access(dev, profile) is None:
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_stock_playlist_delete(raw_selections, _intent=intent)):
            return
        if self._device_operation_owned():
            self._flash_status('Wait for the current device operation', 15)
            return
        model = str(getattr(profile, 'model', '') or '')
        self._release_eject_ready()
        from .workers import OneShotTask
        stock_art_cache = self.stock_art_cache
        prior_witness = self._stock_catalog_witness_cache.get(dev)
        witness_store = self.stock_catalog_witness_store
        shadow_store = self.stock_ipod_shadow_store
        identity_key = snapshot.identity_key
        def plan_delete():
            witness = prior_witness if prior_witness is not None else witness_store.load(identity_key)
            from soundbox.stock_ipod_catalog_state import StockCatalogWitness
            facts = device_facts(dev)
            return plan_stock_ipod_playlist_delete(
                dev,
                [playlist_id for playlist_id, _title in identities],
                model_number=model,
                filesystem=device_fs(dev),
                stock_art_cache=stock_art_cache,
                allow_dual_boot=pre_composite_dual,
                firewire_guid=getattr(facts, "firewire_guid", ""),
                usb_family=getattr(facts, "usb_family", ""),
                identity_key=identity_key,
                serial_number=getattr(facts, "hardware_serial", ""),
                shadow_store=shadow_store,
                catalog_witness=witness if isinstance(witness, StockCatalogWitness) else None,
            )
        worker = OneShotTask(plan_delete)
        self._stock_delete_plan_worker = worker
        self._exporting = True
        self._set_device_progress(0)
        self.cancel_btn.setVisible(False)
        self._start_pulse("Planning iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev, expected=tuple(identities):
            self._finish_stock_delete_worker(
                "_stock_delete_plan_worker", ww,
                lambda result: self._on_stock_playlist_delete_plan(
                    d, expected, result, confirmed=True, expected_mount=intent[1])))
        self._start_device_worker(worker, dev, "plan-delete")

    def _cached_composite_manifest(self, dev):
        """Presentation data only; the planner freshly checks this precondition."""
        cached = self._stock_view_cache.get(dev)
        if cached is None or cached[1] is None:
            raise ValueError("Refresh the iPod playlist view before retrying")
        return deepcopy(cached[1].manifest)

    def _restore_playlist_order_drop(self, dev=None):
        """Retire only the exact optimistic dual drop, including rejected waits."""
        request = getattr(self, '_playlist_order_drop', None)
        if request is None or dev is not None and request[0] != dev:return
        self._playlist_order_drop = None
        self.stock_ipod_pages.restore_playlist_order(request[1])

    def _apply_composite_playlist_order(self, requested_keys) -> None:
        """Publish the Device-tab sequence to iTunesDB and Rockbox together."""
        from .export_dialog import device_failure_text
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        pages = self.stock_ipod_pages
        token = pages.playlist_order_token
        if getattr(profile, 'kind', '') == 'stock_ipod':
            from .stock_playlist_order import request_order
            request_order(self, requested_keys)
            return
        if (
            not dev
            or self.lib is None
            or getattr(profile, "kind", "") != "dual_boot"
        ):
            pages.restore_playlist_order(token)
            return
        try:
            manifest = self._cached_composite_manifest(dev)
            rows = manifest.get("playlists") or {}
            order = tuple(str(key) for key in requested_keys if str(key))
            if len(order) != len(set(order)) or set(order) != set(rows):
                raise ValueError(
                    "Playlist order no longer matches the committed iPod state")
            current = device_manifest.current_playlists_by_key(
                self.lib, self._sync_playlists())
            playlists = [current[key] for key in order]
        except Exception as exc:
            pages.restore_playlist_order(token)
            QMessageBox.warning(
                self,
                "Playlist order unavailable",
                device_failure_text(exc,phase='plan'),
            )
            self._refresh_device_surfaces(dev)
            return
        self._playlist_order_drop = (dev, token)
        try:
            self._begin_dual_ipod_sync(
                dev, playlists, reconcile=True, respect_selected_order=True,
                confirmed=True, action="order", expected_manifest=manifest)
        except Exception as exc:
            MainWindow._restore_playlist_order_drop(self,dev)
            QMessageBox.warning(self, "Playlist order unavailable", device_failure_text(exc,phase='plan'))

    def _begin_composite_playlist_delete(
            self,
            dev: str,
            selected_keys: tuple[str, ...],
            *,
            confirmed: bool = False,
    ):
        """Remove checked schema-3 playlists through the unified planner."""
        from .export_dialog import device_failure_text
        try:
            current_manifest = self._cached_composite_manifest(dev)
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
                        export_key=key if key.startswith('cust:') and row.get('pid') else '',
                    )
                    if device_manifest.playlist_key(playlist) != key:
                        raise ValueError(
                            f"Managed playlist identity is invalid: {key}")
                remaining.append(playlist)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "iPod playlist deletion refused",
                device_failure_text(exc,phase='plan'),
            )
            return
        self._begin_dual_ipod_sync(
            dev,
            remaining,
            reconcile=True,
            confirmed=True,
            action="delete",
            expected_manifest=current_manifest,
        )

    def _on_stock_playlist_delete_plan(
            self, dev: str, expected: tuple[tuple[int, str], ...], result, *,
            confirmed=False, expected_mount=None):
        from soundbox.stock_ipod import StockIpodDeletePlan
        from soundbox.operation_control import OperationCancelled
        from .export_dialog import present_ipod_failure
        self._exporting = False
        self._cancelling = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        if isinstance(result, OperationCancelled):
            self.progress.setVisible(False)
            self._flash_status('iPod deletion cancelled — nothing written')
            return
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            present_ipod_failure(self,dev,"iTunes delete refused",result,phase='plan')
            self._refresh_devices()
            return
        if not isinstance(result, StockIpodDeletePlan):
            self.progress.setVisible(False)
            QMessageBox.critical(
                self, "iTunes delete failed", "Unexpected plan result.")
            return
        self._set_device_progress(150)
        summary = result.summary()
        planned_title = ", ".join(summary["playlists"])
        expected_ids = tuple(playlist_id for playlist_id, _title in expected)
        if (len(result.playlist_ids) != len(result.playlist_names)
                or tuple(result.playlist_ids) != expected_ids
                or tuple(zip(result.playlist_ids,result.playlist_names,strict=True)) != expected
                or expected_mount is not None and self._device_coordinator.mounts.get(dev) != expected_mount):
            QMessageBox.warning(
                self,
                "Playlists changed",
                "The selected playlist identities changed during planning. "
                "Nothing was modified.",
            )
            return
        if confirmed:
            self._commit_stock_playlist_delete(dev, result)
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
        from soundbox.stock_ipod import commit_stock_ipod_playlist_delete
        from .export_dialog import persist_ipod_export_witness
        from .workers import OneShotTask
        witness_store = self.stock_catalog_witness_store
        identity_key = plan.catalog_witness.device_key if plan.catalog_witness is not None else ''
        worker = OneShotTask(lambda: persist_ipod_export_witness(
            commit_stock_ipod_playlist_delete(plan), witness_store, identity_key))
        self._stock_delete_commit_worker = worker
        self._exporting = True
        self._set_device_progress(max(150, self.progress.value()))
        self.cancel_btn.setVisible(True)
        self._start_pulse("Syncing iPod")
        worker.finished.connect(
            lambda ww=worker, d=dev: self._finish_stock_delete_worker(
                "_stock_delete_commit_worker", ww,
                lambda result: self._on_stock_playlist_delete_commit(d, result)))
        self._start_device_worker(worker, dev, "commit-delete")

    def _on_stock_playlist_delete_commit(self, dev: str, result):
        from soundbox.operation_control import OperationCancelled
        from .export_dialog import present_ipod_failure, device_result_notices, device_result_warnings
        self._exporting = False
        self._cancelling = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        if (isinstance(result, OperationCancelled) and not getattr(result,'committed',False)
                and getattr(result,'recovery_status',None) in {None,'not needed','rolled back'}):
            self.progress.setVisible(False)
            self._flash_status('iPod deletion cancelled — playlists unchanged')
            return
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Ready")
            present_ipod_failure(self,dev,"iTunes deletion failed",result,phase='commit',critical=True)
        else:
            self._publish_ipod_export_witness(dev,result)
            self._set_device_progress(800)
            multiple = len(result.playlists) != 1
            warnings = device_result_warnings(result)
            notices = device_result_notices(result)
            def complete():
                self._device_completion_message(
                    "iTunes playlists deleted" if multiple else "iTunes playlist deleted",
                    f"Deleted {'playlists' if multiple else 'playlist'}: "
                    f"{', '.join(result.playlists)}\n"
                    f"Removed {result.deleted_track_records} unused track "
                    f"record(s) and {result.deleted_media_files} media file(s) "
                    f"({human(result.deleted_bytes)}).\n\n"
                    f"Remaining: {result.remaining_playlists} user playlist(s), "
                    f"{result.remaining_tracks} track(s).\n\n"
                    f"Recovery files:\n{result.recovery_directory}\n\nEject is ready."
                    + ('\n\n'+'\n'.join(notices) if notices else '')
                    + ('\n\n'+'\n'.join(warnings) if warnings else ''),
                    attention=bool(warnings), issues=warnings,
                )
                self._flash_status(
                    'Playlist deleted — remaining cleanup cancelled'
                    if getattr(result, 'cleanup_cancelled', False) else
                    'Playlist deleted — needs attention' if warnings else
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
        if not audit_existing:return True
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
            "apply only the required iTunes/Rockbox changes. Planning and "
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
            self._show_export_dialog(dev)
            return
        if profile is not None and profile.kind == "stock_ipod":
            if self._stock_has_sync_source(dev):
                from .stock_catalog_sync import request_device_sync
                request_device_sync(self, dev)
                return
            self._show_export_dialog(dev)
            return
        if profile is not None and profile.kind == "dual_boot":
            if not self._confirm_ipod_sync(dev, audit_existing=audit_existing):
                self._flash_status("iPod sync cancelled — nothing written")
                return
            self._begin_dual_ipod_sync(
                dev,
                None,  # Resolve managed selections inside the device worker.
                reconcile=True,
                confirmed=True,
                action="sync",
                audit_existing=audit_existing,
            )
            return
        if profile is not None and profile.kind=='rockbox':
            self._begin_rockbox_ipod_sync(dev,None,resync=True)
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
        self._cancelling = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        issues=[f'{playlist.name}: {error}' for playlist in getattr(rep,'playlists',())
                for error in playlist.errors]
        issues.extend(f'{playlist.name}: {playlist.missing} missing song(s), {playlist.conflicts} conflicting file(s)'
                      for playlist in getattr(rep,'playlists',()) if playlist.missing or playlist.conflicts)
        if issues:
            self._export_target_result(dev,RuntimeError('; '.join(issues)))
            self.progress.setVisible(False);self._eject_ready_device=dev
            self._set_status_text('Sync needs attention')
            self._update_device_action_state()
            copied=sum(playlist.copied for playlist in rep.playlists)
            QMessageBox.warning(self,'Sync needs attention',
                f'{copied} file(s) copied. Completed files were kept; retry Sync after fixing these issues.\n\n'
                +'\n'.join(issues[:20]))
            return
        if getattr(rep, 'cancelled', False):
            self.progress.setVisible(False)
            self._eject_ready_device = dev
            self._flash_status('Sync cancelled — completed files kept')
            self._update_device_action_state()
            return
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
            usb_pages = vars(self).get('usb_device_pages')
            view = (usb_pages.playlist_tree if usb_pages is not None and usb_pages.playlist_tree.isVisible()
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
        usb_pages = vars(self).get('usb_device_pages')
        views = [self.table, self.artists_view]
        if usb_pages is not None:
            views.append(usb_pages.playlist_tree)
        for view in views:
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
            usb_pages = vars(self).get('usb_device_pages')
            columns = (resized[0],) if resized else range(len(COLUMNS))
            for column in columns:
                hidden = self.table.isColumnHidden(column)
                if self.artists_view.isColumnHidden(column) != hidden:
                    self.artists_view.setColumnHidden(column, hidden)
                width = table_header.sectionSize(column)
                if artist_header.sectionSize(column) != width:
                    artist_header.resizeSection(column, width)
                if usb_pages is not None:
                    usb = usb_pages.playlist_tree
                    usb.setColumnHidden(column,hidden)
                    if usb.columnWidth(column) != width:
                        usb.setColumnWidth(column,width)
            views = [self.artists_view]
            if usb_pages is not None:
                views.append(usb_pages.playlist_tree)
            for view in views:
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
        """Selected-track edits, Finder, analysis, artwork and removal scopes."""
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        tid = self.model._rows[idx.row()].get("id")
        if not tid:
            return
        selection = self.table.selectionModel()
        selected = selection.selectedRows() if selection else []
        selected_rows = ([self.model._rows[index.row()] for index in selected]
                         if any(index.row() == idx.row() for index in selected)
                         else [self.model._rows[idx.row()]])
        ids = [row['id'] for row in selected_rows if row.get('id')]
        key = COLUMNS[idx.column()][0]
        menu = QMenu(self)
        binding = capture_tracks(self, ids)
        clicked = capture_tracks(self, (tid,))
        if key in ("title", "artist"):
            menu.addAction("Edit", self._table_menu_edit(idx))
        if key == "artist":
            cur_artist = self.model._rows[idx.row()].get("artist") or ""
            if cur_artist and cur_artist != "Unknown Artist":
                menu.addAction(f"Merge “{cur_artist}” into…",
                               self._track_menu_action(clicked, lambda a=cur_artist: self._merge_artist(a)))
        self._add_track_file_actions(menu, ids, binding=binding)
        add_recovery_actions(self, menu)
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
                                   self._track_menu_action(binding, self._fix_links))
        self._add_track_reanalysis_action(menu, ids, binding=binding)
        menu.addSeparator()
        menu.addAction("Choose cover art…", self._track_menu_action(clicked, lambda: self._art_pick(tid)))
        if self.providers.allows("artwork.search"):
            menu.addAction("Fetch cover art now", self._track_menu_action(clicked, lambda: self._art_fetch_one(tid)))
        if (self.providers.allows('artwork.search')
                and callable(getattr(self.providers.artwork,'fetch_profile_art',None))):
            menu.addAction('Set cover art to artist profile',
                           self._track_menu_action(clicked,lambda:self._art_artist_profile(tid)))
        # A managed default is not a per-track cover and cannot be removed from
        # this menu: blocked tracks intentionally receive defaults too. Only a
        # real cover/avatar exposes the per-track Remove action.
        if self.art_cache.canonical_image_path(tid):
            menu.addAction("Remove cover art", self._track_menu_action(clicked, lambda: self._art_remove(tid)))
        menu.addSeparator()
        # Playlist views expose both scopes: remove only from the current
        # playlist, or remove globally from SoundBox's Collection. Collection
        # and other derived all-track views expose only the global action.
        self._add_track_removal_actions(menu, ids, selected_rows=selected_rows, binding=binding)
        exec_transient_menu(menu, self.table.viewport().mapToGlobal(pos))

    def _add_track_reanalysis_action(self, menu: QMenu, ids: list[str], *, binding=None):
        n = len(ids)
        binding = capture_tracks(self, ids) if binding is None else binding
        menu.addAction(
            "Analyze Track" if n == 1 else f"Analyze {n} Tracks",
            self._track_menu_action(binding, lambda selected=tuple(ids): self._analyze_tracks(selected)),
        )

    def _add_track_file_actions(
            self, menu: QMenu, ids: list[str], *, reveal: bool = True, binding=None):
        """Add source-file utilities in their stable menu order."""
        selected = tuple(ids)
        binding = capture_tracks(self, ids) if binding is None else binding
        if reveal:
            menu.addAction(
                "Open in Finder",
                self._track_menu_action(binding, lambda: self._open_in_finder(selected)),
            )

    def _add_track_removal_actions(self, menu: QMenu, ids: list[str], *, selected_rows=None, binding=None):
        """Add the scope-explicit reversible removal actions for this view.

        Native ordinary playlists capture exact source occurrences; custom
        playlists are edited directly. Collection removal hides the track from every
        SoundBox view/export but never writes rekordbox or touches source audio.
        """
        n = len(ids)
        binding = capture_tracks(self, ids) if binding is None else binding
        key = getattr(self, '_view_key', '') or ''
        rb_path = key[3:] if key.startswith('rb:') else None
        cust_name = key[3:] if key.startswith('sb:') and key[3:] in self.custom else None
        if rb_path:
            from soundbox.rekordbox_write import WriteProofError
            try:
                captured = self._native_capture_remove(rb_path, ids, selected_rows)
                error = ''
            except (ValueError, KeyError, WriteProofError) as exc:
                captured = None;error = str(exc)
            action = menu.addAction("Remove from Playlist" if n == 1 else f"Remove {n} tracks from Playlist")
            action.setEnabled(captured is not None)
            if captured is not None:
                action.triggered.connect(self._track_menu_action(binding, lambda: self._native_submit_remove(captured)))
            else:action.setToolTip(error)
        elif cust_name:
            menu.addAction("Remove from Playlist" if n == 1
                           else f"Remove {n} tracks from Playlist",
                           self._playlist_menu_action(binding, cust_name, lambda: self._remove_from_custom(cust_name, ids)))
        menu.addAction("Remove from Collection" if n == 1
                       else f"Remove {n} tracks from Collection",
                       self._track_menu_action(binding, lambda: self._delete_tracks(ids)))

    def _remove_from_rb_playlist(self, display_path: str, ids: list[str]):
        """A native playlist gesture must publish to rekordbox, never a local veto."""
        self._native_remove(display_path,ids)

    def _remove_from_custom(self, name: str, ids: list[str]):
        """Custom SoundBox playlists are ours — remove for real."""
        drop = set(ids)
        self.custom[name] = [i for i in self.custom.get(name, []) if i not in drop]
        self._refresh_custom()
        self._after_track_edit(f"Removed {len(ids)} ✓")

    def _after_track_edit(self, msg: str, *, persist=True,refresh_devices=True):
        """Persist + re-render after a delete/remove/restore: counts, current
        view, and device drift badges all reflect the new effective library."""
        if persist:self._save_state()
        self._apply_settings_to_lib()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        if refresh_devices:self._refresh_devices()
        self._refresh_graveyard()  # ALWAYS current — not just on gear-open
        self._flash_status(msg, 6)

    def _merge_artist(self, src: str):
        """Merge one spelling with durable native intentions when source-backed."""
        source_scope=_artist_source_scope(self.lib)
        reg = self.lib.artist_registry if self.lib else None
        choices = [a for a in (reg.names() if reg else []) if a != src]
        target, ok = QInputDialog.getItem(
            self, "Merge artist", f"Merge every “{src}” into:", choices, 0, True)
        target = (target or "").strip()
        if not ok or not target or target.lower() == src.lower():
            return
        if _artist_source_scope(self.lib)!=source_scope:
            self._flash_status('Library changed; reopen Merge artist',8)
            return
        controller=getattr(self,'metadata_controller',None)
        native=bool(controller is not None and self.lib and self.lib.source_kind=='rekordbox-db')
        if native:
            if not controller.change_alias(src,target):return
        else:
            previous=self.aliases;self.aliases={**previous,src:target}
            if not self._save_state():self.aliases=previous;return
            self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._refresh_devices()  # renamed artist folders can drift nothing; keep badges fresh
        self._flash_status('Artist merge pending' if native else 'Merged ✓',6)

    def _remove_alias(self, src: str):
        controller=getattr(self,'metadata_controller',None)
        native=bool(controller is not None and self.lib and self.lib.source_kind=='rekordbox-db')
        if native:
            if not controller.change_alias(src,None):return
        else:
            previous=self.aliases;self.aliases=dict(previous);self.aliases.pop(src,None)
            if not self._save_state():self.aliases=previous;return
            self._build_artist_registry()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))
            self._reload_current_view()
        self._flash_status('Artist update pending' if native else 'Unmerged ✓',6)

    def _table_edit_at(self, idx):
        self.table.setCurrentIndex(idx)
        self.table.edit(idx)

    def _rename_current(self):
        """⌘R — inline-rename the highlighted CELL (Artist cell → artist,
        else Title) in whichever browser has focus: table or Artists page."""
        focus = QApplication.focusWidget()
        for tree in (self.tree, self.sb_tree):
            if focus is tree or focus is not None and tree.isAncestorOf(focus):
                item=tree.currentItem()
                if item is None:return
                if item.data(0,NATIVE_TREE_ROLE):
                    identity=item.data(0,RB_NODE_ROLE)
                    if identity:self._native_rename(identity)
                else:
                    folder=item.data(0,CUSTOM_FOLDER_ROLE)
                    name=item.data(0,CUSTOM_ROLE)
                    if folder or name:self._rename_local_node('folder' if folder else 'playlist',folder or name)
                return
        if focus is self.artists_view:
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
        from .track_menu import capture_tracks
        ids = list(dict.fromkeys(ids))
        binding = capture_tracks(self, ids)
        if not binding.matches(self):
            return False
        n = len(ids)
        if QMessageBox.question(
                self, "Remove from Collection",
                f"Remove {n} track{'s' if n > 1 else ''} from Collection?\n\n"
                "This hides them from every SoundBox view and export. rekordbox "
                "and the files on disk are NOT touched. Restore anytime in Settings."
        ) != QMessageBox.Yes:
            return False
        if (getattr(self, '_shutting_down', False)
                or getattr(self, '_state_maintenance', False) or not binding.matches(self)):
            self._flash_status('The selected tracks changed; review them and try again.', 8)
            return False
        stamp = time.strftime("%Y-%m-%d %H:%M")  # minute precision — same-day deletes sort truthfully
        previous_hidden, previous_dates = self.hidden, self.hidden_dates
        self.hidden = self.hidden | set(ids)
        self.hidden_dates = dict(self.hidden_dates)
        for i in ids:
            self.hidden_dates.setdefault(i, stamp)
        if not self._save_state():
            self.hidden, self.hidden_dates = previous_hidden, previous_dates
            self._flash_status('Tracks were not removed; the library could not be saved. Try again.', 12)
            return False
        self._after_track_edit(f"Removed {n} from Collection ✓", persist=False)
        self._discard_graveyard_analysis(ids)
        return True

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
        kinds = self.art_cache.status_kinds(self.lib.tracks_by_id)
        for tid in self.lib.tracks_by_id:
            if kinds[tid] == "avatar":
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
        try:
            dlg.exec()  # persistent: user fixes any number of artists, then closes
        finally:
            dlg.deleteLater()

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
            dlg.deleteLater()

    def _review_art_changed(self, tid):
        self._refresh_model_art()
        self._refresh_info_artwork((tid,))
        self._queue_stock_artwork((tid,))

    def _open_default_photos(self):
        from .defaultphotos import DefaultPhotosDialog

        dlg = DefaultPhotosDialog(
            self.art_cache.default_photos(),
            on_action=self._on_default_photo_action,
            parent=self,
        )
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

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
        self._refresh_info_artwork(self.lib.tracks_by_id if self.lib is not None else ())
        if self.lib is not None:
            self._reload_current_view()
        self._refresh_devices()
        for row in self.art_cache.default_photos() if self._stock_art_connected else ():
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
        c = deepcopy(c)
        tids = tuple(c["tids"])
        if c["kind"] == "rename":
            # Same plane as a merge: alias old → new, canonical everywhere.
            self._apply_merges([(c["artist"], c["new"])], [])
            done(True, None)
            return
        if c["kind"] == "delete":
            # Block, not forget: these avatars are unwanted AND unfindable —
            # they must never crawl back on a relaunch or re-scan.
            self.art_cache.block_many(tids, avatar_only=True)
            self._refresh_model_art()
            self._refresh_info_artwork(tids)
            done(True, None)
            return
        local = c["kind"] == "file"
        if (c["kind"] not in ("file", "url") or self._shutting_down or self._state_maintenance
                or (not local and (not self.providers.allows("artwork.url")
                    or getattr(self, "_scan_worker", None) is not None))):
            done(False, None)
            return
        if not tids:
            done(False, None)
            return
        from .workers import _OneShotArt
        if local:
            path = Path(c["path"])
            def fetch():
                data = path.read_bytes()
                mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                return ArtResult(data, mime, 1.0, path.as_uri(), c["artist"])
        else:
            fetch = lambda: self.providers.artwork.fetch_by_url(c["url"])
        w = _OneShotArt(tids[0], fetch, provider=None if local else self.providers.artwork)
        w.requires_save = True
        w.local = local

        def current_artist(tid):
            track = self.lib.tracks_by_id.get(tid) if self.lib else None
            return bool(track and metadata.effective_artist(tid, track.name, track.artist,
                self.overrides, True, self.lib.artist_registry, stem=track.path.stem) == c["artist"])

        def landed(worker, current_ids):
            res = worker.result
            if not current_ids or isinstance(res, Exception) or res is None:
                if not current_ids:
                    self._flash_status('Artwork target changed; choose the image again', 8)
                elif isinstance(res, Exception):
                    QMessageBox.warning(self, "Artwork wasn't saved", str(res))
                done(False, None)
                return
            try:
                affected = self._apply_artist_picture(current_ids, res, avatar_only=True)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Artwork wasn't saved", str(exc))
                done(False, None)
                return
            done(bool(affected), self.art_cache.image_path(affected[0]) if affected else None)

        if not self._art_requests.start_many(w, tids, landed, guard=current_artist):
            w.deleteLater()
            done(False, None)

    def _apply_artist_picture(self, tids, res, *, avatar_only=False):
        """Publish one artist action atomically, preserving newer real covers."""
        affected = self.art_cache.store_many(tids, res, avatar_only=avatar_only)
        if affected:
            self._refresh_model_art()
            self._refresh_info_artwork(affected)
            self._queue_stock_artwork(affected)
        self._flash_status(f"Art set on {len(affected)} ✓", 8)
        return affected

    @_art_ui_action
    def _art_remove(self, tid):
        # BLOCK, not forget: a deliberate removal must never be undone by the
        # background fill on the next launch (it was — infuriating).
        self.art_cache.block(tid)
        self._refresh_model_art()
        self._refresh_info_artwork((tid,))

    @_art_ui_action
    def _art_pick(self, tid):
        """The manual picker: thumbnail grid of SoundCloud candidates (human
        judges, no confidence gate) + URL/file escape hatches. 100% coverage for
        the tracks the auto-matcher can't safely call."""
        t = self.lib.tracks_by_id.get(tid) if self.lib else None
        if not t:
            return
        from .artwork_requests import track_binding
        binding = track_binding(self, tid)
        title = self.titles.get(tid) or t.name
        artist = metadata.effective_artist(tid, title, t.artist, self.overrides, True,
                                           self.lib.artist_registry, stem=t.path.stem)
        dlg = ArtPickerDialog(artist, title, self, provider=self.providers.artwork)
        try:
            dlg.exec()
        finally:
            dlg.shutdown()
            choice = deepcopy(dlg.choice)
            dlg.deleteLater()
        if self._shutting_down or self._state_maintenance or not choice:
            return
        if track_binding(self, tid) != binding:
            self._flash_status('Artwork target changed; choose the image again', 8)
            return
        if choice["kind"] == "thumb" and self.providers.allows("artwork.search"):
            c = choice["cand"]
            self._run_oneshot(tid, lambda c=c: self.providers.artwork.fetch_thumb(
                c["thumb"], c["url"], c["title"]), manual=True)
        elif choice["kind"] == "page" and self.providers.allows("artwork.url"):
            self._run_oneshot(tid, lambda u=choice["url"]: self.providers.artwork.fetch_by_url(u), manual=True)
        elif choice["kind"] == "file":  # local image remains shared
            p = Path(choice["path"])
            def read():
                data = p.read_bytes()
                mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                return ArtResult(data, mime, 1.0, p.as_uri(), p.name)
            self._run_oneshot(tid, read, local=True)

    def _run_oneshot(self, tid, fn, *, guard=None, local=False, manual=False):
        if self._shutting_down or self._state_maintenance:
            return
        if self._background_status_available():
            self._set_status_text("Saving artwork…" if local else "Fetching…")
        w = _OneShotArt(tid, fn, provider=None if local else self.providers.artwork)
        w.requires_save = local or manual
        w.local = local
        if not self._art_requests.start(w, guard=guard):
            w.deleteLater()

    def _finish_oneshot(self, w, *, owned=False):
        try:
            self._oneshots.remove(w)
        except ValueError:
            pass
        if not owned:w.deleteLater()

    @_art_ui_action
    def _on_oneshot(self, tid, res, *, local=False):
        if self._shutting_down or self._state_maintenance:
            return
        if isinstance(res, Exception):
            if local:
                QMessageBox.warning(self, "Couldn't read image", str(res))
                if self._background_status_available():
                    self._flash_status('Artwork NOT saved — choose the image and retry', 20)
                return
            # Honest failure: the fetch BROKE (network, block, bug) — that is
            # not "no art found". Log the whole story; keep the flash short
            # (the status label is narrow).
            LOG.warning("Art fetch failed for %s: %s", tid, res, exc_info=res)
            if self._background_status_available():
                self._flash_status("Fetch failed ✕", 8)
        elif res:
            self.art_cache.store_ok(tid, res)
            self._refresh_info_artwork((tid,))
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

    @_provider_action('artwork.search')
    @_art_ui_action
    def _art_artist_profile(self, tid):
        fetch=getattr(self.providers.artwork,'fetch_profile_art',None)
        track=self.lib.tracks_by_id.get(tid) if self.lib else None
        if track is None or not callable(fetch):return
        def artist_for(current):
            title=self.titles.get(tid) or current.name
            return metadata.effective_artist(tid,title,current.artist,self.overrides,True,
                self.lib.artist_registry,stem=current.path.stem)
        artist=artist_for(track)
        if not artist or artist==metadata.UNKNOWN_ARTIST:
            self._flash_status('Set the track’s artist before choosing its profile picture',8);return
        def snapshot():
            try:return self.art_cache.cover_snapshot(tid)
            except ValueError:return None
        before=snapshot()
        def current():
            candidate=self.lib.tracks_by_id.get(tid) if self.lib else None
            return bool(candidate and artist_for(candidate)==artist and snapshot()==before)
        self._run_oneshot(tid,lambda:fetch(artist),guard=current)

    @_provider_action("artwork.url")
    def _art_from_url(self, tid):
        url, ok = QInputDialog.getText(
            self, "Cover art",
            "SoundCloud URL (track = its cover, artist profile = avatar):")
        if ok and url.strip():
            self._run_oneshot(tid, lambda u=url.strip(): self.providers.artwork.fetch_by_url(u), manual=True)

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
        self._refresh_info_artwork((tid,))
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
        if self.lib is None or not self._stock_art_connected:
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
        if (self._shutting_down or not self._stock_art_connected or not self._stock_art_pending
                or self._stock_art_worker is not None):
            return
        images = tuple(sorted(self._stock_art_pending, key=str))
        self._stock_art_pending.clear()
        self._maybe_prepare_stock_artwork(images=images)

    def _update_export_preparation(self):
        """Start stock preparation on attachment and retire it on disconnect."""
        connected = any(getattr(profile, 'has_stock_ipod', False)
                        for profile in self._device_profiles.values())
        previous = self._stock_art_connected
        self._stock_art_connected = connected
        if connected and not previous:
            self._schedule_stock_artwork()
        elif not connected:
            self._stock_art_timer.stop()
            self._stock_art_pending.clear()
            self._stock_art_rescan = False
            if self._stock_art_worker is not None:
                self._stock_art_worker.stop()

    def _schedule_stock_artwork(self):
        if (self._stock_art_connected and not self._shutting_down
                and not self._stock_art_timer.isActive()):
            self._stock_art_timer.start()

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
        if not self._stock_art_connected:
            return
        if self._stock_art_worker is not None:
            if rescan_if_running or self._stock_art_worker._stop:
                self._stock_art_rescan = True
            return
        profiles = set()
        for dev, profile in self._device_profiles.items():
            if not getattr(profile, 'has_stock_ipod', False):
                continue
            cached = self._stock_view_cache.get(dev)
            observed = getattr(cached[0], 'stock_format', None) if cached else getattr(profile, 'stock_format', None)
            if observed is not None and observed.supports_artwork:
                profiles.add(observed.artwork_profile)
        if not profiles:
            return
        worker = StockArtworkPrepWorker(
            self.lib, self.art_cache, self.stock_art_cache, images, profiles=tuple(sorted(profiles)))
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
        if self._analysis_priority_pending():
            self._schedule_waveform_recheck()
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
        w.bpm_ready.connect(lambda value, ww=w: self._on_waveform_bpm_ready(ww, value))
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
                    "Waveform rebuild failed ✕" if len(failed) == 1
                    else f"{len(failed)} waveform rebuilds failed ✕",
                    10,
                )
            elif forced:
                self._flash_status(
                    "Waveform rebuilt ✓" if len(forced) == 1
                    else f"{len(forced)} waveforms rebuilt ✓",
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
                self, "Waveform unavailable",
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
        exec_transient_menu(menu, self.table.horizontalHeader().mapToGlobal(pos))

    def _set_column_hidden(self, col: int, hidden: bool):
        self.table.setColumnHidden(col, hidden)
        self._fit_browser_columns(fill=True)
        self._sync_artist_columns()
        self._save_columns()  # visibility toggles persist immediately

    def _save_columns(self):
        """Persist the complete user-sized shell.

        This runs at shutdown after the user's final resize, so the next launch
        restores the outer window first and the sidebar panes second.
        """
        hh = self.table.horizontalHeader()
        self.settings["columns_state"] = bytes(hh.saveState().toBase64()).decode("ascii")
        self.settings['columns_keys'] = [key for key,_label,_hidden in COLUMNS]
        self.settings["sidebar_split"] = list(self._sidebar_split.sizes())
        self.settings['sidebar_width'] = self._browser_split.sizes()[0]
        self.settings["window_geometry"] = bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.settings["window_rect"] = [
            normal.x(), normal.y(), normal.width(), normal.height()]
        self._schedule_settings_save('columns_state','columns_keys','sidebar_split','sidebar_width',
                                     'window_geometry','window_rect')

    def _save_sidebar_width(self, *_args):
        if not hasattr(self, 'settings'):
            return
        self.settings['sidebar_width'] = self._browser_split.sizes()[0]
        self._schedule_settings_save('sidebar_width')

    def _restore_sidebar_width(self):
        # Hidden startup layouts must know the restored window size before
        # QSplitter clamps its child sizes, just as the vertical panes do.
        for widget in (self, self.centralWidget(), self.body_stack):
            if widget.layout() is not None:
                widget.layout().activate()
        split = self._browser_split
        width = self.settings.get('sidebar_width', 272)
        if type(width) is not int or width < 160:
            width = 272
        available = max(0, split.width() - split.handleWidth())
        width = min(width, max(160, available - 320))
        blocked = split.blockSignals(True)
        try:
            split.setSizes([width, max(1, available - width)])
        finally:
            split.blockSignals(blocked)

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
        self._restore_sidebar_width()
        sizes = self.settings.get("sidebar_split")
        if (isinstance(sizes, list) and len(sizes) in (2,3)
                and all(isinstance(x, int) and x >= 0 for x in sizes) and sum(sizes)):
            self._sidebar_split.setSizes([sum(sizes[:2]),sizes[2]] if len(sizes)==3 else sizes)
        else:
            self._sidebar_split.setSizes([1000, 96])  # classic proportions
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
            if kind == 'rbid':
                from .trees import native_playlist_binding
                try:
                    binding=native_playlist_binding(json.loads(val))
                    node=next((row for row in self.lib.source_playlist_catalog.get('nodes',[]) if row['ID']==binding['id']),None)
                    if ((binding['source'],binding['library_id'])!=(str(self.lib.source),self.lib.source_library_id)
                            or node is None or node['UUID']!=binding['uuid'] or node['rb_local_deleted']!=0):
                        raise ValueError('Native playlist source identity changed')
                    if binding['alias']:
                        pl=next((p for p in self._custom_playlists() if p.pid==binding['id']
                                 and p.export_key=='cust:'+binding['alias']),None)
                    else:pl=next((p for p in self.lib.playlists if p.pid==binding['id']),None)
                    if pl is None:raise ValueError('Native playlist disappeared')
                except (ValueError,TypeError,KeyError,UnicodeError):
                    self._flash_status('The selected playlist changed; select it again before exporting',15);return []
                pls.append(pl)
            elif kind == "rb":
                matches=[p for p in self.lib.playlists if p.display_path == val]
                if len(matches)!=1:
                    self._flash_status('The playlist name is ambiguous or changed; select its exact row again',15);return []
                pls.append(matches[0])
            elif kind == "cust":
                state=self.unification_controller.state()
                if state and state['customs_done']:
                    mapped=next((p for p in self._custom_playlists()
                                 if p.export_key=='cust:'+val),None)
                    if mapped:pls.append(mapped)
                    continue
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
        # The explicit drop/export action is the ordinary sync intent.
        return True

    def _start_confirmed_dual_jobs(
            self, dev: str, playlists: list[Playlist]) -> None:
        """Plan every already-confirmed playlist in one device generation."""
        if self._shutting_down or self._state_maintenance:
            return
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

    def _begin_rockbox_ipod_sync(self,dev,playlists,*,_export_capture=None,resync=False):
        """Export Rockbox from the same durable iPod snapshot as stock/dual."""
        if self._shutting_down or self._state_maintenance:return
        if _export_capture is None:
            self._request_ipod_export_capture('rockbox',dev,playlists,resync=resync)
            return
        _export_capture.require_current(self,dev)
        profile=self._device_profiles.get(dev)
        facts=device_facts(dev,cached_only=True)
        self._validate_export_target(dev,facts)
        if (getattr(profile,'kind','')!='rockbox' or not profile.rockbox_export
                or not facts.writable):
            raise ValueError('Rockbox export needs a connected writable FAT32 iPod')
        from .export_dialog import verify_ipod_export_capture
        art_cache=self.art_cache
        validate=lambda:verify_ipod_export_capture(_export_capture,art_cache)
        validate_artwork=lambda tid:_export_capture.validate_artwork(art_cache,tid)
        manifest_guard=dict(expected_manifest_payload=_export_capture.expected_manifest_payload,
                            expected_manifest_absent=_export_capture.expected_manifest_absent)
        if not resync and self._exporting and self.export_worker is not None:
            self.export_worker.add(dev,playlists,
                mount_token=self._device_mount_tokens.get(dev,0),
                snapshot=_export_capture.snapshot,estimate=_export_capture.track_total,
                validate_inputs=validate,validate_artwork=validate_artwork,**manifest_guard)
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda:self._begin_rockbox_ipod_sync(dev,playlists,_export_capture=_export_capture,resync=resync)):
            return
        if self._device_operation_owned():
            raise ValueError('Wait for the current device operation before exporting to this iPod')
        self._release_eject_ready()
        if resync:
            worker=ResyncWorker(self.lib,dev,self.overrides,self.titles,art_cache,
                self.rockbox_media_cache,self.rockbox_art_cache,
                snapshot=_export_capture.snapshot,validate_inputs=validate,validate_artwork=validate_artwork,**manifest_guard)
            self._resync_worker=worker;self._exporting=True
            worker.progress.connect(self._on_resync_progress)
            worker.finished.connect(lambda ww=worker:self._finish_device_result_worker(
                '_resync_worker',ww,lambda result:self._on_resync_done(dev,result)))
            self._set_device_progress(0);self._start_pulse('Syncing')
            self.cancel_btn.setVisible(True);self._start_device_worker(worker,dev,'sync')
            return
        worker=ExportWorker(self.lib,self.overrides,self.titles,self.art_cache,
            self.rockbox_media_cache,self.rockbox_art_cache)
        self.export_worker=worker;self._exporting=True
        worker.progress.connect(self._on_progress)
        worker.finished.connect(lambda ww=worker:self._finish_device_result_worker(
            'export_worker',ww,self._on_export_done))
        self._set_device_progress(0);self._start_pulse('Exporting')
        self.progress.setVisible(True);self.cancel_btn.setVisible(True)
        worker.add(dev,playlists,mount_token=self._device_mount_tokens.get(dev,0),
                   snapshot=_export_capture.snapshot,estimate=_export_capture.track_total,
                   validate_inputs=validate,validate_artwork=validate_artwork,**manifest_guard)
        self._start_device_worker(worker,dev,'export')

    def _on_device_drop(self, dev: str, items: list[tuple[str, str]], *, _from_export=False):
        if getattr(self,'_export_batch',None) and not _from_export:return
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
        if profile is not None and profile.kind == 'rockbox':
            self._begin_rockbox_ipod_sync(dev,pls)
            return
        if profile is not None and not profile.rockbox_export:
            QMessageBox.warning(
                self, "Export unavailable",
                "This volume is not a writable FAT32 Rockbox/generic USB "
                "target. No files were changed.")
            return
        if self._exporting and self.export_worker is not None:
            self.export_worker.add(dev, pls, mount_token=self._device_mount_tokens.get(dev, 0),
                                   library=self.lib, titles=self.titles, overrides=self.overrides)
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
        if dev and dev in getattr(self, '_usb_format_volumes', ()) and getattr(self, '_usb_format_device', ''):
            raise RuntimeError('This USB is being formatted')
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
        batch = getattr(self, '_export_batch', None)
        batch_target = batch.get('active') if batch else None
        worker.export_batch_context = (
            (batch, batch_target) if batch_target is not None
            and batch_target.get('path') == dev and phase != Phase.DISCOVERING
            else None)
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
        stock_progress = None
        if active.command.intent in {'plan-delete', 'commit-delete'} and not self._cancelling:
            self._start_pulse(stage)
            if total:
                self._set_status_count(done, total)
                base, span = (0, 150) if active.command.intent == 'plan-delete' else (150, 650)
                self._set_device_progress(base + round(span * min(max(done, 0), total) / total))
        elif (not self._cancelling and any(worker is getattr(self, name, None)
                for name in ('_stock_plan_worker', '_stock_commit_worker'))):
            stock_progress = stock_phase_progress(stage, done, total)
            if stock_progress is not None:
                self._start_pulse(stock_progress.message)
                self._set_status_count()
                if stock_progress.value is not None:
                    self._set_device_progress(max(self.progress.value(), stock_progress.value))
        # The operation's main progress callback owns the visible phase/count.
        # Small media/artwork writes can emit several phases in one UI frame;
        # replacing the primary label here resets its dots and makes the entire
        # row jump between track counts and byte totals. Keep details on hover.
        detail = f"{stage}\n{label}"
        if total:
            if stock_progress is not None and stock_progress.unit == 'work_bytes':
                detail += f"\nTransfer + verification work: {human(done)} / {human(total)}"
            elif stock_progress is not None and stock_progress.unit != 'bytes':
                detail += f"\n{done} / {total} {stock_progress.unit}"
            else:
                detail += f"\n{human(done)} / {human(total)}"
        self.status_label.setToolTip(detail)

    def _cancel_export(self):
        from .export_wait import cancel_source_syncs
        from .stock_catalog_sync import cancel_deferred_sync
        MainWindow._restore_playlist_order_drop(self)
        if cancel_deferred_sync(self):
            return
        batch=getattr(self,'_export_batch',None)
        if batch:
            batch['cancelled']=True
            batch['pending'].clear()
        cancelled_wait=cancel_source_syncs(self)
        active = self._device_coordinator.active
        physical_owner = bool(active is not None and active.phase != Phase.DISCOVERING
            or any(getattr(self,name,None) is not None for name in self._EJECT_BLOCKING_WORKERS
                   if name != '_dev_scan_worker'))
        if getattr(self,'_cancelled_source_syncs',None) and not physical_owner:
            self._cancelling = True
            self._start_pulse('Cancelling')
            return
        if cancelled_wait and not self._device_operation_owned():
            self._cancelling = False
            self._stop_pulse();self.cancel_btn.setVisible(False);self.progress.setVisible(False)
            self._flash_status('iPod request cancelled — nothing written')
            self._update_device_action_state()
            return
        if self._cdj_waiting is not None:
            self._cdj_waiting = None
            active = self._device_coordinator.active
            workers = (name for name in self._EJECT_BLOCKING_WORKERS
                       if name != '_dev_scan_worker')
            other_owner = bool(
                active is not None and active.phase != Phase.DISCOVERING
                or any(getattr(self, name, None) is not None for name in workers)
                or getattr(self, '_eject_verify_worker', None) is not None
                or self._device_coordinator.eject_pending or self._device_coordinator.stopping
                or self._stock_view_warming or self._stock_view_pending
                or self._pending_device_operation is not None
                or getattr(self, '_device_finalization_token', None) is not None)
            if not other_owner:
                self._exporting = False
                self._cancelling = False
                if not getattr(self, '_usb_format_device', ''):
                    self._stop_pulse()
                    self.progress.setVisible(False)
                    self.cancel_btn.setVisible(False)
                    self._flash_status('CDJ request cancelled — nothing written')
                self._update_device_action_state()
            self._maybe_finish_export_target()
            return
        self._cdj_waiting = None
        active = self._device_coordinator.active
        pending = self._pending_device_operation
        waiting = pending is not None
        self._pending_device_operation = None
        rejected = getattr(pending, '_device_wait_rejected', None)
        if rejected is not None:rejected()
        self._dual_sync_queue.clear()
        workers = []
        for attr in self._EJECT_BLOCKING_WORKERS:
            if attr == '_dev_scan_worker':
                continue  # Cancelling an unstarted command does not cancel an unrelated poll.
            worker = getattr(self, attr, None)
            if worker is not None and hasattr(worker, "cancel"):
                workers.append(worker)
                worker.cancel()
        if active is not None and active.phase != Phase.DISCOVERING:
            self._device_coordinator.cancel()
        if not workers and (active is None or active.phase == Phase.DISCOVERING):
            if getattr(self, '_usb_format_device', ''):
                self._exporting = False
                self._deleting = False
                self._cancelling = False
                return  # An unstarted wait cannot take the format owner's status.
            self._exporting = False
            self._deleting = False
            self._cancelling = False
            self._stop_pulse(force=True)
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            self._flash_status('Device request cancelled — nothing written' if waiting else 'Cancelled')
            self._maybe_finish_export_target()
            if batch and batch.get('active') is None:
                self._start_next_export_target()
            return
        self._cancelling = True
        self._start_pulse("Cancelling")

    def _finish_worker(self, attr: str, w):
        """Drop our reference to a finished worker on the MAIN thread (after run()
        has fully returned) and schedule its C++ deletion. This is the crux fix for
        'QThread destroyed while running' — never let the last ref drop mid-run."""
        callbacks = tuple(getattr(w, '_eject_resume_callbacks', ()))
        if isinstance(w, QThread) and w.isFinished():
            w.wait()  # finished precedes thread-local cleanup; all owners wait for both.
        self._device_coordinator.finished(w)
        if getattr(self, attr, None) is w:
            setattr(self, attr, None)
        try:
            w.deleteLater()
        except RuntimeError:
            pass  # already gone
        self._update_device_action_state()
        w._eject_resume_callbacks = []
        for callback in callbacks:
            QTimer.singleShot(0, callback)
        if (getattr(self, "_device_mount_refresh_pending", False)
                and not self._shutting_down):
            # A mount event held behind an owner need not wait for the next
            # three-second drift poll. Admission still checks every owner.
            QTimer.singleShot(0, self._refresh_devices)

    def _finish_device_result_worker(self, attr: str, worker, callback) -> None:
        """Clear QThread ownership before exposing a device result to the UI."""
        current = getattr(self, attr, None) is worker
        owned = current
        result = getattr(worker, "result", None)
        command = getattr(worker, "device_command", None)
        batch_context = getattr(worker, 'export_batch_context', None)
        batch_owned = (batch_context is not None
                       and getattr(self, '_export_batch', None) is batch_context[0]
                       and batch_context[0].get('active') is batch_context[1])
        if command is not None:
            current = current and self._device_coordinator.current(command)
            outcome=adapter_result(command,result)
            self._device_coordinator.publish(worker,outcome)
            if current and batch_owned and outcome.failure:
                self._export_target_result(command.target.path, RuntimeError(outcome.failure))
            elif current and batch_owned and outcome.cancelled:
                self._export_target_result(command.target.path, RuntimeError('Cancelled'))
        self._finish_worker(attr, worker)
        if current and isinstance(result, Exception):
            self._dual_sync_queue.clear()
        if owned and not current:
            drop=getattr(worker,'playlist_order_drop',None)
            if drop is not None and getattr(self,'_playlist_order_drop',None)==drop:
                MainWindow._restore_playlist_order_drop(self,drop[0])
            from soundbox.cdj_transaction import CdjPlan
            if isinstance(result, CdjPlan):
                result.close()
            if batch_owned:
                batch, target = batch_context
                batch['error'] = 'Device disconnected or changed — export result unavailable'
            active = self._device_coordinator.active
            if (getattr(self, attr, None) is None
                    and (batch_owned or getattr(self, '_export_batch', None) is None)
                    and (active is None or active.phase == Phase.DISCOVERING)):
                self._exporting = False
                self._cancelling = False
                if attr == '_delete_worker':
                    self._deleting = False
                if command is not None:
                    self._dual_sync_queue.pop(command.target.path, None)
                if not getattr(self, '_usb_format_device', ''):
                    self._stop_pulse(force=True)
                    self.progress.setVisible(False)
                    self.cancel_btn.setVisible(False)
                self._update_device_action_state()
            if batch_owned:
                self._maybe_finish_export_target()
        if current and not self._shutting_down:
            if command is not None and batch_owned:
                self._export_target_result(command.target.path, result)
            generation = getattr(self, '_status_generation', 0)
            callback(result)
            self._maybe_finish_export_target()
            restore = getattr(self, '_restore_edit_status', None)
            if restore is not None and getattr(self, '_status_generation', 0) == generation:
                restore()

    def _finish_stock_delete_worker(self, attr, worker, callback):
        """Retire only this Delete owner even when its obsolete result is suppressed."""
        owned = getattr(self, attr, None) is worker
        delivered = False

        def deliver(result):
            nonlocal delivered
            delivered = True
            callback(result)

        self._finish_device_result_worker(attr, worker, deliver)
        active = self._device_coordinator.active
        if (owned and not delivered and getattr(self, attr, None) is None
                and (active is None or active.phase == Phase.DISCOVERING)):
            self._exporting = False
            self._cancelling = False
            self._stop_pulse(force=True)
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            self._update_device_action_state()
            if not self._shutting_down:
                self._restore_edit_status()

    def shutdown(self):
        """Stop background work BEFORE Qt tears down — prevents the crash where
        the app quits while a worker thread is still running."""
        if getattr(self, "_shutdown_waiting", False):
            return
        for name in ('playlist_controller','tag_definition_controller'):
            flush=getattr(getattr(self,name,None),'flush_admission',None)
            if flush is not None and not flush():
                self._flash_status('Source intentions could not be saved; SoundBox remains open',20)
                return False
        self._shutdown_waiting = True
        if hasattr(self, 'my_tags'):
            self.my_tags.popup.close()
        # Shutdown dispatches events while workers drain. Stop observing the
        # entire application before child widgets or their wrappers retire;
        # deleteLater may otherwise leave this stopped window installed.
        application = vars(self).pop('_application_event_filter_app', None)
        if application is not None:
            application.removeEventFilter(self)
        usb_pages = vars(self).get('usb_device_pages')
        if usb_pages is not None:
            usb_pages._remember_view()
        if hasattr(self,'_remember_stock_device_view'):
            self._remember_stock_device_view()
        self._shutting_down = True
        self._artists_refresh.stop()
        self._export_batch = None
        if getattr(self,'playlist_controller',None) is not None:
            self.playlist_controller.stop()
        if getattr(self,'tag_definition_controller',None) is not None:
            self.tag_definition_controller.stop()
        if getattr(self,'shared_import_controller',None) is not None:
            self.shared_import_controller.stop()
        if getattr(self,'native_analysis_controller',None) is not None:
            self.native_analysis_controller.stop()
        if getattr(self,'prep_controller',None) is not None:
            self.prep_controller.stop()
        if getattr(self,'metadata_controller',None) is not None:
            self.metadata_controller.stop()
        if getattr(self,'graveyard_controller',None) is not None:
            self.graveyard_controller.shutdown()
        if getattr(self,'removal_controller',None) is not None:
            self.removal_controller.stop()
        if getattr(self,'unification_controller',None) is not None:
            self.unification_controller.stop()
        if getattr(self,'media_controller',None) is not None:
            self.media_controller.stop()
        self._cdj_waiting = None
        from .export_wait import cancel_source_syncs
        cancel_source_syncs(self)
        keyboard = getattr(self, "_keyboard_router", None)
        if keyboard is not None:
            keyboard.release_all()
        self._refresh_pending = False
        self._libcache_pending = None
        self._stock_view_pending.clear()
        for completions in getattr(self, '_stock_view_callbacks', {}).values():
            for completion in completions:
                completion.finish(RuntimeError('SoundBox is shutting down'))
        getattr(self, '_stock_view_callbacks', {}).clear()
        self._device_finalization_token = None
        self._device_finalization_mounts = {}
        self._device_finalization_refreshing = False
        self._waveform_rechecks_enabled = False
        for t in (getattr(self, "_dev_timer", None), getattr(self, "_pulse_timer", None),
                  getattr(self, "_warm_timer", None),
                  getattr(self, "_settings_save_timer", None),
                  getattr(self, "_art_status_timer", None),
                  getattr(self, "_volume_refresh_timer", None),
                  getattr(self, "_mount_table_timer", None),
                  getattr(self, "_stock_art_timer", None),
                  getattr(self, "_analysis_retry_timer", None),
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
        rejected = getattr(getattr(self, '_pending_device_operation', None), '_device_wait_rejected', None)
        if rejected is not None:rejected()
        self._pending_device_operation = None
        self._dual_sync_queue.clear()
        # Session cue edits are already visible/usable. Finish their accepted
        # FIFO before the generic owner stop closes replacement admission.
        prep=getattr(self,'prep_controller',None)
        if prep is not None:prep.drain_admissions()
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
        MainWindow._close_library_processes(self)
        MainWindow._flush_settings_preferences(self)
        committer=getattr(self,'_state_committer',None)
        if committer is not None and not committer.flush_preferences():
            LOG.error('Preferences remain unsaved during shutdown: %s',committer.preferences_error)
        if committer is not None and committer.deferred_save and self._save_state():
            committer.deferred_save=False
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

    def _pending_save_reason(self, *, retained=()):
        """Quit admission only: ordinary editing and source pumps stay open."""
        if getattr(self, '_shutting_down', False):
            return ''
        if getattr(getattr(self, '_art_requests', None), 'manual_pending', lambda:False)():
            return 'Finishing artwork saves — SoundBox remains open'
        for name in ('playlist_controller', 'tag_definition_controller'):
            controller = getattr(self, name, None)
            ack=getattr(controller,'ack',None)
            if (ack is not None and ack.key in retained and ack.retention_capture() is not None
                    and not getattr(getattr(controller,'admission',None),'pending',False)):
                continue
            error = getattr(controller, 'admission_error', '')
            if error:return 'Save needs attention — ' + str(error)
            if getattr(controller, 'admission_pending', False):
                return 'Finishing source saves — SoundBox remains open'
        tag_lane = getattr(getattr(self, 'tag_controller', None), 'tag_state_lane', None)
        if tag_lane is not None and tag_lane.error:
            return 'My Tag save needs attention — ' + tag_lane.error
        if tag_lane is not None and tag_lane.pending:
            return 'Finishing My Tag saves — SoundBox remains open'
        if getattr(getattr(self,'prep_controller',None),'admission_pending',False):
            return 'Finishing cue/grid saves — SoundBox remains open'
        prep_error=getattr(getattr(self,'prep_controller',None),'admission_error','')
        if prep_error:return 'Cue/grid save needs attention — '+prep_error
        preference_error=getattr(getattr(self,'_state_committer',None),'preferences_error',None)
        if preference_error:
            return 'Preferences save needs attention — '+str(preference_error)
        if (getattr(self,'_state_publication',False)
                or getattr(getattr(self,'tag_controller',None),'state_commit_owned',False)
                or getattr(getattr(self,'_state_committer',None),'pending',False)):
            return 'Finishing saves — SoundBox remains open'
        settings = getattr(self, 'settings', {})
        metadata_controller=getattr(self,'metadata_controller',None)
        unadmitted_metadata=bool(metadata_controller and getattr(metadata_controller,
            'can_quit_with_unadmitted_metadata',lambda:False)())
        planning = bool(metadata_controller and getattr(metadata_controller,'planning_pending',False))
        from .quit_saves import METADATA
        retained_metadata = (not planning and any(settings.get(key) for key in METADATA)
                             and all(key in retained for key in METADATA if settings.get(key)))
        if metadata_controller and not unadmitted_metadata and not retained_metadata and (planning or getattr(metadata_controller,'migration_pending',False)):
            migration=(settings.get('source_truth_alias_next') if 'source_truth_alias_next' in settings
                       else settings.get('source_truth_migration'))
            error=getattr(metadata_controller,'queue_error',lambda:'')()
            metadata_queue=settings.get('pending_metadata_edits',[])
            metadata_error=(next((job.get('error') for job in metadata_queue
                if isinstance(job,dict) and job.get('error')),'') if isinstance(metadata_queue,list)
                else 'Saved metadata queue needs attention')
            error=error or metadata_error or (migration.get('error') if isinstance(migration,dict)
                                             else '' if planning else 'Saved metadata needs attention')
            if not error and planning:
                return 'Finishing metadata saves — SoundBox remains open'
            if not error and isinstance(migration,dict):
                order=migration.get('order')
                if isinstance(order,list):
                    if migration.get('unresolved') and migration.get('cursor',0)==len(order):
                        error='Saved metadata has unresolved song identities; intentions are preserved'
                elif migration.get('format')=='soundbox-source-alias-v1':
                    if migration.get('state') not in ('building','ready'):
                        error='Saved artist updates need attention; intentions are preserved'
                elif migration.get('format')!='soundbox-source-truth-building-v1':
                    error='Saved metadata needs attention; intentions are preserved'
            return ('Save needs attention — '+str(error) if error else
                    'Finishing metadata saves — SoundBox remains open')
        for key in ('shared_import_jobs', 'analysis_jobs', 'native_analysis_jobs',
                    'pending_prep_edits', 'rekordbox_playlist_edits', 'pending_tag_definitions',
                    'pending_tag_assignments', 'pending_metadata_edits'):
            if key in retained:continue
            if key=='pending_metadata_edits' and unadmitted_metadata:continue
            queue = settings.get(key, [])
            if queue:
                if not isinstance(queue, list):
                    return 'A saved edit queue needs attention; SoundBox remains open'
                error = next((job.get('error') for job in queue
                              if isinstance(job, dict) and job.get('error')), '')
                if error:
                    return 'Save needs attention — ' + str(error)
                return 'Finishing saves — SoundBox remains open'
        removal = settings.get('graveyard_delete_intent')
        if removal and 'graveyard_delete_intent' not in retained:
            return ('Save needs attention — ' + str(removal['error'])
                    if isinstance(removal, dict) and removal.get('error')
                    else 'Finishing saves — SoundBox remains open')
        tags = getattr(self, 'tag_controller', None)
        grid = getattr(self, 'grid_controller', None)
        active_grid = getattr(grid, '_active', None)
        grid_probe = (isinstance(active_grid, tuple) and len(active_grid) == 2
                      and active_grid[0] == 'probe')
        if (getattr(self, '_analysis_worker', None) is not None
                or getattr(getattr(self, '_import_tasks', None), 'pending', 0)
                or getattr(tags, '_cue_queue', None)
                or getattr(grid, '_queue', None)
                or (active_grid is not None and not grid_probe)
                or (getattr(tags, 'busy', False)
                    and not (grid_probe and getattr(tags, '_busy_kind', '') == 'grid')
                    and getattr(tags, '_busy_kind', '') in (
                        'tags', 'cues', 'grid', 'prep', 'playlists', 'analysis',
                        'registration', 'registration-cancel', 'removal', 'media', 'tag-definitions', 'metadata'))):
            return 'Finishing saves — SoundBox remains open'
        return ''

    def _defer_quit_for_saves(self, *, auto_close=False):
        if getattr(self,'_shutting_down',False):return False
        if not MainWindow._flush_settings_preferences(self):return True
        committer=getattr(self,'_state_committer',None)
        if (committer is not None and committer.preferences_error is not None
                and (auto_close or getattr(self,'_quit_saves',None) is None)):
            committer.retry_preferences()
        guard = getattr(self, '_quit_saves', None)
        retained = guard.retained() if guard is not None else ()
        reason = MainWindow._pending_save_reason(self, retained=retained)
        if not reason:
            return False
        if auto_close and guard is None:
            from .quit_saves import QuitSaves
            guard = self._quit_saves = QuitSaves(self)
        if guard is not None:guard.defer(requested=auto_close)
        message = ('Checking saved edits before closing…' if guard is not None and guard.checkpoint is not None
                   else reason.replace('SoundBox remains open','closing when ready')
                   if guard is not None and guard.requested else reason)
        self._flash_status(message, 10)
        return True

    def closeEvent(self, event):
        if (getattr(self, "_shutdown_waiting", False)
                or getattr(self, "_state_maintenance", False)
                or self._defer_quit_for_saves(auto_close=True)):
            event.ignore()
            return
        if self.shutdown() is False:
            event.ignore()
            return
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
        controller=getattr(self,'tag_controller',None)
        playlist_status=(getattr(controller,'playlist_update_status','')
                         or getattr(controller,'registration_update_status',''))
        if text == 'Ready' and not self._edit_status_deferred():
            notice = getattr(self, '_deferred_background_notice', None)
            if notice is not None:
                self._deferred_background_notice = None
                self._deferred_edit_status = ''
                self._flash_status(*notice)
                return True
            playlist_status = _edit_footer_text(controller) or playlist_status
            if playlist_status:
                self._deferred_edit_status = ''
                self._prep_update_message = playlist_status
        if text=='Ready' and playlist_status:
            text=playlist_status
        if (text == "Ready" and self._preparing_library
                and self._background_status_available()):
            self._show_preparation_status()
            return True
        self._stop_pulse(force=force)
        self._status_generation = getattr(self, "_status_generation", 0) + 1
        self.status_label.setText(text)
        self.status_label.reserve(text)
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
        self.status_label.reserve(word + "...")
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
        cancelled = any(getattr(report, 'cancelled', False) for _device, report in reports)
        cancelled = cancelled or bool(self._cancelling and not reports)
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
                    if pr.missing or pr.conflicts:
                        issues.append(f"{pr.name}: {pr.missing} missing · {pr.conflicts} conflicts")
                    issues.extend(f'{pr.name}: {error}' for error in pr.errors)
        LOG.info("Export finished: %d job report(s), %d issue(s)", len(reports), len(issues))
        devices = [dev for dev, _rep in reports if dev and dev != "(worker)"]

        def complete():
            if issues:
                self._set_status_text('Export needs attention')
            elif cancelled:
                self._flash_status("Cancelled")
            else:
                self._set_status_text("Complete ✓")
            if not cancelled and not issues and self.stack.currentIndex() == 1:
                self.fs_view.setRootIndex(
                    self.fs_model.setRootPath(self.fs_model.rootPath()))
            if issues:
                QMessageBox.information(
                    self, "Export needs attention",
                    "Completed files were kept. Retry after fixing these issues:\n\n" + "\n".join(issues[:20]))

        if cancelled:
            self.progress.setVisible(False)
            self._eject_ready_device = devices[0] if devices else ''
            complete()
            self._update_device_action_state()
        else:
            self._finalize_device_operations(devices, complete)


def make_app() -> QApplication:
    logpath = setup_logging()
    LOG.info("Soundbox starting (log: %s)", logpath)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("LowFreqLabs")
    app.setApplicationName("Soundbox")
    app.setFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))  # real system font
    from .theme_controller import install
    install(app)
    return app


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--isolated-trash-check':
        from soundbox.mac_trash import check_synthetic
        check_synthetic(Path(sys.argv[2]).resolve())
        return
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
                from soundbox import mac_process
                from .startup_check import runtime_crypto_check, runtime_sql_check
                win.grab().save(str(report_dir / "window.png"))
                report = {"profile": win.providers.profile,
                          "modules": list(win.providers.modules),
                          "actions": list(win.providers.actions),
                          "native": bool(win.player_bar.player.is_native),
                          "native_process_guard": mac_process._native_function() is not None,
                          "native_process_query": type(mac_process.rekordbox_running()) is bool,
                          "private_sql_orm": runtime_sql_check(),
                          "private_aes": runtime_crypto_check(),
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
