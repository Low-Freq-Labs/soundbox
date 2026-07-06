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

from PySide6.QtCore import QEvent, QProcess, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QLineEdit,
    QFileDialog, QFileSystemModel, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QStackedWidget,
    QTableView, QTreeView, QTreeWidgetItem, QVBoxLayout, QWidget,
)


from soundbox import artfetch, engine, metadata, rekordbox_db
from soundbox.rekordbox_xml import Playlist
from . import theme
from .artpicker import ArtPickerDialog
from .model import COLUMNS, TrackModel
from .player import PlayerBar

from .device_browser import DeviceBrowserMixin
from .devices import device_fs, human
from .settings_page import SettingsPageMixin
from .trees import (
    ARTIST_ROLE, ARTISTS_ROLE, COLLECTION_ROLE, CUSTOM_ROLE, DEVPATH_ROLE, PATH_ROLE,
    ArtistsTree, CellEditDelegate, DeviceTree, PlaylistTree, SoundBoxTree, TrackTable,
)
ARTIST_TRACK_ROLE = Qt.UserRole + 9  # a track row inside the Artists browser

from .workers import (
    ArtFillWorker, DeleteWorker, DeviceScanWorker, ExportWorker,
    RefreshWorker, ResyncWorker, _OneShotArt, _running,
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
}




class MainWindow(SettingsPageMixin, DeviceBrowserMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Soundbox")
        self.resize(1240, 800)
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        self._data_file = data_dir / "library.json"  # portable SoundBox library
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
        self._last_xml = ""
        self.lib = None
        self.custom: dict[str, list[str]] = {}  # name -> ordered track ids
        self.overrides: dict[str, str] = {}  # track_id -> manual artist override
        self.titles: dict[str, str] = {}     # track_id -> manual title override
        self.hidden: set[str] = set()        # deleted-from-SoundBox track ids
        self.hidden_dates: dict[str, str] = {}   # tid -> "YYYY-MM-DD" (graveyard)
        self.removals: dict[str, dict] = {}  # pid -> {tid: removed-date}
        self.aliases: dict[str, str] = {}    # artist merges: variant -> target
        self.settings: dict = dict(_DEFAULT_SETTINGS)  # defaults; _load_state merges saved
        self.export_worker: ExportWorker | None = None
        self._backup_worker = None  # OneShotTask for backup zip / restore
        self._refresh_worker: RefreshWorker | None = None
        self._resync_worker: ResyncWorker | None = None
        self._dev_scan_worker: DeviceScanWorker | None = None
        self._delete_worker: DeleteWorker | None = None
        self._drift_cache: dict[str, int] = {}  # device path -> #playlists out of date
        self._exporting = False
        self._deleting = False
        self._accordion_guard = False
        self._build_ui()
        QApplication.instance().installEventFilter(self)  # Space = play/pause
        # Animated dots for transient states (FINALIZING…, Cancelling…).
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(300)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_word: str | None = None
        self._pulse_dots = 0
        self._cancelling = False
        # Devices are recognized on launch and as they're plugged in — no XML needed.
        self._refresh_devices()
        self._dev_timer = QTimer(self)
        self._dev_timer.timeout.connect(self._refresh_devices)
        self._dev_timer.start(3000)
        self._load_state()  # restore custom playlists + last-opened XML
        self.table.setFocus()  # open browsing (Space-play, arrows), not typing

    # ---------- persistence ----------
    def _load_state(self):
        data = {}
        if self._data_file.exists():
            try:
                data = json.loads(self._data_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = self._recover_state()
        self.custom = data.get("custom") or {}
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
        self._sync_settings_controls()  # reflect saved values in the Settings page
        self._restore_columns()         # saved column widths/visibility
        self._refresh_custom()
        # Auto-load on launch: live rekordbox DB first, else the last XML fallback.
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
        self._backup_state()  # snapshot the previous good state first
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic (temp + rename): a crash mid-save must never corrupt the
            # library — it holds every override, playlist, delete and setting.
            tmp = self._data_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "custom": self.custom,
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

    def _reload_current_view(self):
        """Re-render whatever playlist is showing so a settings change takes
        effect immediately. Updates the table rows only — does NOT switch the
        visible page (so toggling on the Settings page keeps you there)."""
        if self.lib is None:
            return
        cur = self.tree.currentItem()
        if cur is not None and cur.data(0, PATH_ROLE):
            rows = engine.playlist_rows(self.lib, cur.data(0, PATH_ROLE),
                                        self.overrides, self.titles)
            self.model.set_rows(self._annotate_art(rows))
            return
        cur = self.sb_tree.currentItem()
        if cur is not None and cur.data(0, COLLECTION_ROLE):
            self._show_collection()
            return
        if cur is not None and cur.data(0, ARTISTS_ROLE):
            self._show_artists()
            return
        if cur is not None and cur.data(0, CUSTOM_ROLE):
            rows = []
            for i in self.custom.get(cur.data(0, CUSTOM_ROLE), []):
                t = self.lib.tracks_by_id.get(i)
                if t and i not in self.hidden:
                    rows.append(engine.track_row(
                        t, self.overrides, self.titles,
                        registry=self.lib.artist_registry,
                        junk=self._title_junk()))
            self.model.set_rows(self._annotate_art(rows))

    # ---------- player ----------
    def eventFilter(self, obj, ev):
        # Space toggles playback — but ONLY from the browsing surfaces (track
        # table + the three sidebar lists) or empty focus. Everywhere else
        # (editors, search, settings checkbox trees, dialogs, device browser)
        # Space keeps its native meaning — it must never type a space AND start
        # audio, or toggle a suggestion checkbox AND blast a track.
        if (ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Space
                and not ev.isAutoRepeat()):
            if self._space_should_play(QApplication.focusWidget()):
                self._play_selected()
                return True
        return super().eventFilter(obj, ev)

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
        """Filter the table — selection survives (see _reseat_selection)."""
        self.model.set_filter(text)
        self._reseat_selection()

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

    def _play_selected(self):
        """Space: if the highlighted track IS the loaded one, toggle
        pause/play. If a DIFFERENT track is highlighted, load it instead —
        curation flow is select → fix the name → Space, and that must play
        the track under the cursor, not pause whatever was on. Highlighting
        happens on load (in _play_track), never as a side effect of edits."""
        t = self._selected_track()
        if t is not None and t.track_id != self.player_bar.current_track():
            self._play_track(t)
            return
        if self.player_bar.current_track():
            self.player_bar.toggle()

    def _play_row(self, index):
        """Double-click a row → play that track."""
        if not index.isValid() or index.row() >= len(self.model._rows):
            return
        tid = self.model._rows[index.row()].get("id")
        t = self.lib.tracks_by_id.get(tid) if (self.lib and tid) else None
        if t is not None:
            self._play_track(t)

    def _on_playback_error(self, msg: str):
        LOG.warning("Playback error: %s", msg)
        self._flash_status("Playback error", 6)

    def _play_track(self, t):
        try:
            ok = t.path.is_file()
        except OSError:
            ok = False
        if not ok:
            self._flash_status("File not on disk", 5)
            return
        src = self.titles.get(t.track_id) or t.name
        artist = metadata.effective_artist(t.track_id, src, t.artist, self.overrides,
                                           True, self.lib.artist_registry if self.lib else None,
                                           stem=t.path.stem)
        title = metadata.effective_title(t.track_id, t.name, artist, self.titles,
                                         self._title_junk())
        label = f"{artist} — {title}" if artist and artist != "Unknown Artist" else title
        self.player_bar.play_track(t.track_id, t.path, title=label)
        self._highlight_now_playing(t.track_id)

    def _highlight_now_playing(self, tid: str):
        """Loading a track ALWAYS highlights it in the visible browser — a
        playing track with no visible cursor is disorienting. No-op when the
        track isn't in the current view (e.g. a different playlist)."""
        if self.stack.currentWidget() is self.artists_view:
            cur = self.artists_view.currentItem()
            if cur is not None and cur.data(0, ARTIST_TRACK_ROLE) == tid:
                return
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
                        return
            return
        if self.stack.currentIndex() != 0:
            return  # device browser etc. — nothing to highlight
        for i, r in enumerate(self.model._rows):
            if r.get("id") == tid:
                cur = self.table.currentIndex()
                if not (cur.isValid() and cur.row() == i):
                    col = cur.column() if cur.isValid() else 0
                    self.table.setCurrentIndex(self.model.index(i, col))
                self.table.scrollTo(self.model.index(i, 0))
                return

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
        h = QHBoxLayout(bar); h.setContentsMargins(14, 8, 22, 8); h.setSpacing(10)
        sound = QLabel(SOUND_ART); sound.setObjectName("AsciiSound")
        sound.setAlignment(Qt.AlignVCenter)
        box = QLabel(BOX_ART); box.setObjectName("AsciiBox")
        box.setAlignment(Qt.AlignVCenter)
        h.addWidget(sound, 0, Qt.AlignVCenter)
        h.addWidget(box, 0, Qt.AlignVCenter)
        h.addSpacing(14)
        self.player_bar = PlayerBar()  # waveform scrubber lives where the subtitle was
        self.player_bar.wave_cache_dir = (
            Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
            / "waveforms")  # repeat plays draw instantly
        self.player_bar.playback_error.connect(self._on_playback_error)
        self.player_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h.addWidget(self.player_bar, 1)
        h.addSpacing(4)  # tight: time↔search (padding lives inside the player instead)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search titles…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(165)  # slimmer → more room for the waveform
        # Click-to-focus only: as the first focusable widget it grabbed keyboard
        # focus at launch, so the app opened "typing into search" instead of
        # ready to browse (Space-play, arrows). Startup focus goes to the table.
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
        return bar

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

        rb_header = self._section_header("REKORDBOX PLAYLISTS", "↻", self._refresh_rekordbox)
        rb_header.setContextMenuPolicy(Qt.CustomContextMenu)
        rb_header.customContextMenuRequested.connect(
            lambda pos, w=rb_header: self._rb_header_menu(w, pos))
        v.addWidget(rb_header)
        self.tree = PlaylistTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.tree.currentItemChanged.connect(self._on_rb_select)
        self.tree.itemExpanded.connect(self._collapse_siblings)
        v.addWidget(self.tree, 3)

        v.addWidget(self._section_header("SOUNDBOX PLAYLISTS", "+", self._new_custom, top=True))
        self.sb_tree = SoundBoxTree()
        self.sb_tree.setHeaderHidden(True)
        self.sb_tree.currentItemChanged.connect(self._on_custom_select)
        self.sb_tree.tracks_dropped.connect(self._on_tracks_dropped)
        self.sb_tree.playlist_moved.connect(self._move_custom)
        self.sb_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sb_tree.customContextMenuRequested.connect(self._sb_menu)
        v.addWidget(self.sb_tree, 2)

        v.addWidget(self._section_header("DEVICES", top=True))
        self.device_tree = DeviceTree()
        self.device_tree.setHeaderHidden(True)
        self.device_tree.setFixedHeight(96)
        self.device_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(self._device_menu)
        self.device_tree.itemClicked.connect(self._on_device_click)
        self.device_tree.dropped.connect(self._on_device_drop)
        v.addWidget(self.device_tree)
        return panel

    def _main_stack(self) -> QWidget:
        self.stack = QStackedWidget()

        # page 0: track table (flush at top; sidebar has its own right border).
        page = QWidget(); pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(0)
        self.table = TrackTable()
        self.model = TrackModel()
        self.table.setModel(self.model)
        self.model.cell_edited.connect(self._on_cell_edited)  # inline Title/Artist edits
        self.table.setItemDelegate(CellEditDelegate(self.table))  # pre-filled editors
        # No keypress/double-click editing — a stray Space would wipe a cell. The
        # ONLY way in is the right-click menu (which calls self.table.edit()).
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context)  # edit or art
        self.table.doubleClicked.connect(self._play_row)  # double-click = play, everywhere
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
        self.artists_view.setHeaderLabels(
            ["Artist / Track", "Artist", "BPM", "Key", "Time"])
        self.artists_view.setAlternatingRowColors(True)
        self.artists_view.setUniformRowHeights(True)
        ah = self.artists_view.header()
        ah.setSectionResizeMode(QHeaderView.Interactive)
        ah.setStretchLastSection(False)
        ah.setMinimumSectionSize(46)
        # Same first-class layout as Collection: Artist/Track (col 0) absorbs
        # all extra width; Artist/BPM/Key/Time keep their size.
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        ah.resizeSection(1, 170)
        for c, w in ((2, 70), (3, 60), (4, 60)):
            ah.resizeSection(c, w)
        self.artists_view.setIndentation(16)
        # Editing works like Collection: no stray double-click editing — the
        # right-click menu / ⌘R open the editor (pre-filled, cursor at end).
        self.artists_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.artists_view.setItemDelegate(CellEditDelegate(self.artists_view))
        self.artists_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.artists_view.customContextMenuRequested.connect(self._artists_menu)
        self.artists_view.itemExpanded.connect(self._fill_artist)
        self.artists_view.itemDoubleClicked.connect(self._artists_double)
        self.artists_view.itemChanged.connect(self._on_artist_item_edited)
        self.stack.addWidget(self.artists_view)

        self._apply_default_columns()
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
        w = max(fm.horizontalAdvance("FINALIZING..."), fm.horizontalAdvance("Art 9999/9999"))
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
        self._build_artist_registry()
        self._apply_settings_to_lib()
        self._populate_tree(engine.summary(self.lib))
        self._default_to_collection()
        self._refresh_devices()
        self.stack.setCurrentIndex(0)
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only

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
        self._build_artist_registry()
        self._apply_settings_to_lib()
        self._populate_tree(engine.summary(lib))
        self._default_to_collection()
        self._refresh_devices()
        LOG.info("Loaded %d tracks / %d playlists from rekordbox",
                 lib.track_count, len(lib.playlists))
        self._flash_status("Updated ✓", 6)
        QTimer.singleShot(3000, self._maybe_autostart_art_fill)  # NEW tracks only

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
        rows = engine.playlist_rows(self.lib, path, self.overrides, self.titles)
        self.model.set_rows(self._annotate_art(rows))
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
        if ok and name and name not in self.custom:
            self.custom[name] = []
            self._refresh_custom()
            self._save_state()

    def _collection_tracks(self) -> list:
        """Every loaded track (all playlists pooled), honoring the hide-untracked
        settings, newest first — the curation workbench."""
        if self.lib is None:
            return []
        lib = self.lib
        tracks = [t for t in lib.tracks_by_id.values()
                  if t.track_id not in lib.hidden_ids]  # deleted-from-SoundBox
        if getattr(lib, "hide_untracked", False):
            tracks = [t for t in tracks
                      if not t.is_untracked(nokey=lib.hide_nokey, short=lib.hide_short,
                                            short_secs=lib.hide_short_secs)]
        return sorted(tracks, key=lambda t: (t.date_added or "", t.name), reverse=True)

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
            # Built-in "Collection" pinned first: every track in one view (browse/
            # curate only — no CUSTOM_ROLE, so it can't be dragged to a device,
            # dropped on, renamed, or deleted).
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
        if not name:
            return
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_custom(name))
        menu.addAction("Delete", lambda: self._delete_custom(name))
        menu.exec(self.sb_tree.mapToGlobal(pos))

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
        if ok and new and new != name and new not in self.custom:
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
        reg = self.lib.artist_registry if self.lib else None
        rows = [engine.track_row(t, self.overrides, self.titles, registry=reg,
                                 junk=self._title_junk()) for t in tracks]
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
            it = QTreeWidgetItem([f"{name}  ({len(by_artist[name])})",
                                  "", "", ""])
            it.setData(0, ARTIST_ROLE, name)
            it.setChildIndicatorPolicy(
                QTreeWidgetItem.ShowIndicator)  # expandable before children exist
            self.artists_view.addTopLevelItem(it)
        self.stack.setCurrentIndex(2)

    def _fill_artist(self, item):
        """Lazy: first expand of an artist row fills in their songs."""
        name = item.data(0, ARTIST_ROLE)
        if not name or item.childCount():
            return
        self._artists_filling = True
        try:
            for r in getattr(self, "_artist_children", {}).get(name, []):
                c = QTreeWidgetItem([r["title"], r["artist"], r.get("bpm", ""),
                                     r.get("key", ""), r.get("time", "")])
                c.setData(0, ARTIST_TRACK_ROLE, r["id"])
                c.setFlags(c.flags() | Qt.ItemIsEditable)  # title/artist editable
                item.addChild(c)
        finally:
            self._artists_filling = False

    def _artists_menu(self, pos):
        it = self.artists_view.itemAt(pos)
        if it is None or not it.data(0, ARTIST_TRACK_ROLE):
            return
        col = self.artists_view.columnAt(pos.x())
        menu = QMenu(self)
        if col in (0, 1):
            menu.addAction("Edit", lambda: (self.artists_view.setCurrentItem(it, col),
                                            self.artists_view.editItem(it, col)))
        menu.addAction("Copy File Name",
                       lambda: self._copy_filenames([it.data(0, ARTIST_TRACK_ROLE)]))
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
            self._play_track(t)

    def _show_collection(self):
        reg = self.lib.artist_registry if self.lib else None
        rows = [engine.track_row(t, self.overrides, self.titles, registry=reg,
                                 junk=self._title_junk())
                for t in self._collection_tracks()]
        self.model.set_rows(self._annotate_art(rows))
        self.stack.setCurrentIndex(0)

    def _show_custom(self, name: str):
        rows = []
        for i in self.custom.get(name, []):
            t = self.lib.tracks_by_id.get(i) if self.lib else None
            if t and i not in self.hidden:
                rows.append(engine.track_row(
                    t, self.overrides, self.titles,
                    registry=self.lib.artist_registry,
                    junk=self._title_junk()))
        self.model.set_rows(self._annotate_art(rows))
        self.stack.setCurrentIndex(0)

    # ---------- devices ----------
    def _custom_playlists(self) -> list:
        """Synthetic Playlist objects for the user's custom SoundBox playlists so
        drift/resync recognize them by identity (they aren't in lib.playlists).
        Mirrors the `cust` branch of _resolve_jobs."""
        custom = getattr(self, "custom", None) or {}
        return [Playlist(name=n, folder_path=[], keys=list(ids), key_type="0")
                for n, ids in custom.items() if ids]

    def _device_label(self, name: str, free, drift: int, fs: str = "") -> str:
        # When a device is out of sync, HIDE the "GB free" so the full
        # "⚠ N playlist(s) out of date" fits (it was getting truncated). Free space
        # returns once everything is synced again.
        if drift:
            word = "playlist" if drift == 1 else "playlists"
            return f"⛃  {name}    ⚠ {drift} {word} out of date"
        if fs and fs not in ("msdos", "fat32"):
            # Rockbox runs on FAT32 only — flag HFS+/exFAT devices immediately.
            return f"⛃  {name}    ⚠ {fs.upper()} (not FAT32)"
        return f"⛃  {name}    {human(free)} free"

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
        if _running(getattr(self, "_dev_scan_worker", None)):
            return
        w = DeviceScanWorker(self.lib, self._custom_playlists())
        self._dev_scan_worker = w
        w.done.connect(self._on_devices_scanned)
        w.finished.connect(lambda ww=w: self._finish_worker("_dev_scan_worker", ww))
        w.start()

    def _on_devices_scanned(self, devs: list):
        """Apply a background device scan to the tree (main thread). devs is a list
        of (name, path, free, drift)."""
        self._drift_cache = {path: drift for _n, path, _f, drift in devs}
        new_paths = [p for _n, p, _f, _d in devs]
        cur = [self.device_tree.topLevelItem(i).data(0, DEVPATH_ROLE)
               for i in range(self.device_tree.topLevelItemCount())]
        if new_paths != cur and QApplication.mouseButtons() & Qt.LeftButton:
            # A device appeared/vanished DURING a drag (button held): rebuilding
            # the tree now would yank the drop target out from under the drag.
            # Skip this tick; the 3s poll redelivers right after the drop.
            return
        if new_paths == cur:
            # Same devices — refresh label in place (don't disrupt an in-flight drag).
            for i, (name, path, free, drift) in enumerate(devs):
                it = self.device_tree.topLevelItem(i)
                if it is not None:
                    it.setText(0, self._device_label(name, free, drift, device_fs(path)))
                    it.setToolTip(0, self._device_tip(path, free))
            return
        self.device_tree.clear()
        for name, path, free, drift in devs:
            it = QTreeWidgetItem([self._device_label(name, free, drift, device_fs(path))])
            it.setData(0, DEVPATH_ROLE, path)
            it.setToolTip(0, self._device_tip(path, free))
            self.device_tree.addTopLevelItem(it)

    def _device_tip(self, path: str, free) -> str:
        fs = device_fs(path) or "unknown"
        tip = f"{path}\nFilesystem: {fs}   ·   {human(free)} free"
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
        menu.addAction("Verify export…", lambda: self._verify_device(dev))
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

    def _verify_device(self, dev: str):
        """Post-export sanity: every m3u8 entry on the device must resolve to a
        real file. Walks the USB, so it runs OFF the UI thread."""
        from .workers import OneShotTask
        if _running(getattr(self, "_scan_worker", None)):
            return
        w = OneShotTask(lambda: engine.verify_export(dev))
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
        elif not res["missing"]:
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
            QMessageBox.warning(
                self, "Verify export",
                f"{name}: {len(res['missing'])} of {res['entries']} entries are "
                f"MISSING their files:\n\n{lines}{more}\n\nA Sync usually "
                f"heals this (or re-export the affected playlists).{extra}")

    def _sync_device(self, dev: str):
        if self.lib is None or not dev:
            return
        if self._resync_worker is not None and self._resync_worker.isRunning():
            return
        LOG.info("Sync device: %s", dev)
        w = ResyncWorker(self.lib, dev, self.overrides, self.titles, self.art_cache,
                         self._custom_playlists())
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
        if res:
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

    def _stop_art_fill(self):
        if self._art_worker is not None and self._art_worker.isRunning():
            self._art_worker.stop()
            # Clear the stale "Art N/M" ticker (unless a device op owns the label).
            if not self._exporting and not self._cancelling and not self._deleting:
                self.status_label.setText("Ready")

    def _on_art_progress(self, done, total):
        self.model.refresh_art(self.art_cache.status_kind)  # live Art column ticks
        if not self._exporting and not self._cancelling and not self._deleting:
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
        self._save_columns()  # visibility toggles persist immediately

    def _save_columns(self):
        """Persist the table layout (column widths + hidden columns)."""
        hh = self.table.horizontalHeader()
        self.settings["columns_state"] = bytes(hh.saveState().toBase64()).decode("ascii")
        self._save_state()

    def _restore_columns(self):
        """Bring back the user's saved column layout (after settings load).
        Sort is deliberately NOT restored — a playlist opening pre-sorted would
        read as the wrong (non-rekordbox) order."""
        state = self.settings.get("columns_state")
        if not state:
            return
        from PySide6.QtCore import QByteArray
        hh = self.table.horizontalHeader()
        if hh.restoreState(QByteArray.fromBase64(state.encode("ascii"))):
            hh.setSortIndicator(-1, Qt.AscendingOrder)
            hh.setSectionResizeMode(QHeaderView.Interactive)
            hh.setSectionResizeMode(0, QHeaderView.Stretch)  # Title keeps absorbing slack

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
        return pls

    def _on_device_drop(self, dev: str, items: list[tuple[str, str]]):
        if self.lib is None:
            QMessageBox.information(self, "Open a library first",
                                   "Load your rekordbox library first (↻ on the REKORDBOX header).")
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
        for t in (getattr(self, "_dev_timer", None), getattr(self, "_pulse_timer", None)):
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
        if _running(self._art_worker):
            self._art_worker.stop(); self._art_worker.wait(6000)
        if _running(getattr(self, "_scan_worker", None)):
            self._scan_worker.wait(15000)  # pure CPU scan; let it finish
        if _running(getattr(self, "_backup_worker", None)):
            self._backup_worker.wait(30000)  # finish the zip/restore, don't corrupt it
        for w in list(getattr(self, "_oneshots", [])):
            if _running(w):
                w.wait(8000)  # a mid-flight art fetch must not be GC'd running
        pb = getattr(self, "player_bar", None)
        if pb is not None:
            pb.stop()
            if _running(getattr(pb, "_wf", None)):
                pb._wf.requestInterruption()  # breaks a mid-flight qt_peaks decode
                pb._wf.wait(5000)
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
