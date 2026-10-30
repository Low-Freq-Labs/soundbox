"""Settings page (mixin for MainWindow).

Layout: tabs are centered across the header with Back on the left. General has two balanced columns;
Keyboard, Graveyard, and Connections have dedicated tabs. The reusable framework:
`_settings_section(title)` headers +
`_settings_row(title, control, subtitle, indent)` rows — add future settings by
dropping in more rows/sections, never hand-place widgets. Sub-options HIDE when
their parent toggle is off. Panel widgets (graveyard/rules lists) refresh every
time the gear opens.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QStackedWidget, QTabBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from .choices import ComboBox as QComboBox, Menu as QMenu

_GRAVE_ROLE = Qt.UserRole + 11  # ("del", tid, None) | ("rem", tid, pid)

import logging

from soundbox import engine, metadata
from . import theme

LOG = logging.getLogger("soundbox")


class SettingsPageMixin:
    def _apply_theme_color(self, value, *, save=False):
        mode = self.settings.get('theme_text_mode', 'white')
        mode = mode if mode in ('white', 'black', 'custom') else 'white'
        custom = theme.normalize_accent_text(self.settings.get('theme_text_custom'))
        self.settings.update(theme_text_mode=mode, theme_text_custom=custom)
        text_color = {'white': '#ffffff', 'black': '#000000', 'custom': custom}[mode]
        color = theme.set_accent(value, text_color)
        self.settings['theme_accent'] = color
        app = QApplication.instance()
        if app.styleSheet() != theme.QSS:
            app.setStyleSheet(theme.QSS)
        if hasattr(self, 'wordmark'):
            self.wordmark.set_accent(color)
        if hasattr(self, '_theme_color'):
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(color))
            self._theme_color.setIcon(QIcon(swatch))
            self._theme_color.setText(color.upper())
        self._sync_theme_text_controls()
        self.update()
        if hasattr(self, 'my_tags'):
            self.my_tags._size_popup()
        if save:
            self._schedule_settings_save()

    def _choose_theme_color(self):
        color = QColorDialog.getColor(QColor(theme.PURPLE), self, 'Theme color')
        if color.isValid():
            self._apply_theme_color(color.name(), save=True)

    def _apply_theme_text(self, mode, custom=None, *, save=False):
        self.settings['theme_text_mode'] = mode
        if custom is not None:
            self.settings['theme_text_custom'] = theme.normalize_accent_text(custom)
        self._apply_theme_color(self.settings.get('theme_accent'), save=save)

    def _sync_theme_text_controls(self):
        if not hasattr(self, '_theme_text_mode'):
            return
        mode = self.settings.get('theme_text_mode', 'white')
        self._theme_text_mode.blockSignals(True)
        self._theme_text_mode.setCurrentIndex(max(0, self._theme_text_mode.findData(mode)))
        self._theme_text_mode.blockSignals(False)
        custom = theme.normalize_accent_text(self.settings.get('theme_text_custom'))
        swatch = QPixmap(16, 16)
        swatch.fill(QColor(custom))
        self._theme_text_color.setIcon(QIcon(swatch))
        self._theme_text_color.setText(custom.upper())
        self._theme_text_color.setVisible(mode == 'custom')

    def _choose_theme_text_color(self):
        color = QColorDialog.getColor(
            QColor(self.settings.get('theme_text_custom', theme.DEFAULT_ACCENT_TEXT)),
            self, 'Theme text color')
        if color.isValid():
            self._apply_theme_text('custom', color.name(), save=True)

    def _apply_settings_to_lib(self, *, invalidate_rows=True):
        """Push current settings onto the loaded library (a pure display/export
        filter — never modifies rekordbox)."""
        if self.lib is not None:
            self.lib.hide_untracked = bool(self.settings.get("hide_untracked"))
            self.lib.info_overrides = self.settings.get('track_info',{})
            self.lib.hide_nokey = bool(self.settings.get("hide_nokey", True))
            self.lib.hide_short = bool(self.settings.get("hide_short", True))
            self.lib.hide_short_secs = int(self.settings.get("hide_short_secs", 5))
            self.lib.hidden_ids = set(self.hidden)  # deleted-from-SoundBox tracks
            self.lib.removed_by_playlist = {k: set(v)
                                            for k, v in self.removals.items() if v}
            for playlist in self.lib.playlists:
                playlist.added_keys = list(
                    self.playlist_additions.get(playlist.pid, ()))
            self.lib.title_junk = self._title_junk()
            # The default-photo pool is balanced only across tracks that can
            # actually appear or export. Graveyard tracks stay out of its
            # assignment universe just as they stay out of SoundBox views.
            try:
                self.art_cache.configure_default_tracks(
                    tid for tid in self.lib.tracks_by_id if tid not in self.hidden)
            except (OSError, ValueError) as exc:
                LOG.error("Default artwork assignments were not saved: %s", exc)
                self._flash_status("Default artwork assignments NOT saved — check space/permissions", 20)
            if invalidate_rows:
                self._invalidate_rows()  # title-junk feeds effective_title

    def _settings_section(self, text: str) -> QWidget:
        """A section header (small caps + divider). First thing above a group of rows."""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 16, 0, 4); l.setSpacing(0)
        lbl = QLabel(text); lbl.setObjectName("SettingsSection")
        l.addWidget(lbl)
        return w

    def _settings_row(self, title: str, control: QWidget, subtitle: str = "",
                      indent: int = 0) -> QWidget:
        """One setting: title (+ optional one-line subtitle) on the left, control on
        the right. The building block — add future settings by dropping in more rows."""
        row = QWidget(); rl = QVBoxLayout(row)
        rl.setContentsMargins(indent, 5, 0, 5); rl.setSpacing(2)
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(10)
        lbl = QLabel(title); lbl.setObjectName("SettingsTitle")
        top.addWidget(lbl, 0); top.addStretch(1); top.addWidget(control, 0)
        rl.addLayout(top)
        if subtitle:
            sub = QLabel(subtitle); sub.setObjectName("SettingsSub"); sub.setWordWrap(True)
            rl.addWidget(sub)
        return row

    def _settings_page(self) -> QWidget:
        """Full-page Settings — a clean, scalable list (SECTION headers + rows). Add
        future settings via _settings_section / _settings_row; the scroll area + capped
        width keep it tidy no matter how many pile up."""
        page = QWidget()
        v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        head = QWidget(); head.setObjectName("SettingsHeader")
        head.setAttribute(Qt.WA_StyledBackground, True); head.setFixedHeight(40)
        hh = QGridLayout(head); hh.setContentsMargins(14, 0, 14, 0); hh.setSpacing(0)
        back = QPushButton("←  Back"); back.setObjectName("BackBtn")
        back.setCursor(Qt.PointingHandCursor); back.clicked.connect(self._close_settings)
        hh.addWidget(back, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        # Equal side columns keep the tabs centered on the whole header.
        for side in (0, 2):
            hh.setColumnStretch(side, 1)
            hh.setColumnMinimumWidth(side, back.sizeHint().width())
        self._settings_tabs = QTabBar()
        self._settings_tabs.setObjectName("SettingsTabs")
        self._settings_tabs.setExpanding(False)
        self._settings_tabs.currentChanged.connect(lambda _index: self._sync_rekordbox_connection())
        hh.addWidget(self._settings_tabs, 0, 1, Qt.AlignCenter)
        v.addWidget(head)
        pages = QStackedWidget()
        self._settings_tabs.currentChanged.connect(pages.setCurrentIndex)

        def add_tab(widget: QWidget, label: str) -> None:
            pages.addWidget(widget)
            self._settings_tabs.addTab(label)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        cols = QHBoxLayout(host)
        cols.setContentsMargins(30, 6, 30, 30); cols.setSpacing(40)
        cols.setAlignment(Qt.AlignTop)

        # Graveyard gets the whole tab; the track list expands with the window.
        graveyard_page = QWidget()
        gl = QVBoxLayout(graveyard_page)
        gl.setContentsMargins(30, 6, 30, 30); gl.setSpacing(8)
        gl.addWidget(self._settings_section("GRAVEYARD"))
        self._grave = QTreeWidget()
        self._grave.setObjectName("Graveyard")
        self._grave.setHeaderLabels(["Track", "Removed"])
        self._grave.setRootIsDecorated(False)
        self._grave.setAlternatingRowColors(True)
        self._grave.setSortingEnabled(True)  # click headers: oldest/newest first
        self._grave.sortByColumn(1, Qt.DescendingOrder)
        self._grave.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._grave.setMinimumHeight(240)
        from PySide6.QtWidgets import QHeaderView
        self._grave.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._grave.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._grave.header().setStretchLastSection(False)
        self._grave.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grave.customContextMenuRequested.connect(self._grave_menu)
        self._grave.itemDoubleClicked.connect(lambda item, _column: self._grave_load(item))
        gl.addWidget(self._grave, 1)
        gsub = QLabel("Tracks removed from Collection (and removed from playlists) "
                      "rest here — rekordbox and disk untouched. Double-click to load and listen. Select one or "
                      "Shift/Cmd-select several, then right-click to restore; "
                      "Cmd-A selects all.")
        gsub.setObjectName("SettingsSub"); gsub.setWordWrap(True)
        gl.addWidget(gsub)

        # --- TITLE CLEANUP (named, toggleable rules — no regex soup) --------
        left = QWidget(); left.setMinimumWidth(400); left.setMaximumWidth(600)
        gl = QVBoxLayout(left); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(2)
        gl.setAlignment(Qt.AlignTop)
        gl.addWidget(self._settings_section("TITLE CLEANUP"))
        self._rules = QTreeWidget()
        self._rules.setObjectName("TitleRules")
        self._rules.setHeaderHidden(True)
        self._rules.setRootIsDecorated(False)
        self._rules.itemChanged.connect(self._on_rule_toggled)
        self._rules.setContextMenuPolicy(Qt.CustomContextMenu)
        self._rules.customContextMenuRequested.connect(self._rules_menu)
        gl.addWidget(self._rules)
        from PySide6.QtWidgets import QLineEdit
        self._word_edit = QLineEdit()
        self._word_edit.setPlaceholderText("Add a word — or a regex — to drop… ⏎")
        self._word_edit.returnPressed.connect(self._add_drop_word)
        gl.addWidget(self._word_edit)
        rsub = QLabel("Junk auto-dropped from titles everywhere (table, player, "
                      "iPod filenames). Your words and phrases require an exact "
                      "match at the END of a title or as a whole (bracketed tag) "
                      "— never mid-title. Advanced? Type a regex — it runs as-is, "
                      "like the built-ins. Manual renames always win.")
        rsub.setObjectName("SettingsSub"); rsub.setWordWrap(True)
        gl.addWidget(rsub)
        self._refresh_title_rules()


        col = QWidget(); col.setMinimumWidth(400); col.setMaximumWidth(600)
        bl = QVBoxLayout(col); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(2)
        bl.setAlignment(Qt.AlignTop)
        cols.addWidget(col, 1, Qt.AlignTop)     # PLAYER + LIBRARY + BACKUP
        cols.addWidget(left, 1, Qt.AlignTop)    # TITLE CLEANUP + COVER ART

        # --- APPEARANCE ----------------------------------------------------
        bl.addWidget(self._settings_section("APPEARANCE"))
        appearance = QWidget()
        appearance_row = QHBoxLayout(appearance)
        appearance_row.setContentsMargins(0, 0, 0, 0)
        appearance_row.setSpacing(6)
        self._theme_color = QPushButton(theme.PURPLE.upper())
        self._theme_color.setCursor(Qt.PointingHandCursor)
        self._theme_color.setToolTip('Choose the app accent color')
        self._theme_color.clicked.connect(self._choose_theme_color)
        reset_color = QPushButton("Reset")
        reset_color.setCursor(Qt.PointingHandCursor)
        reset_color.setToolTip('Restore the default purple accent')
        reset_color.clicked.connect(lambda: self._apply_theme_color(theme.DEFAULT_ACCENT, save=True))
        appearance_row.addWidget(self._theme_color)
        appearance_row.addWidget(reset_color)
        bl.addWidget(self._settings_row("Theme color", appearance,
            'Changes selections, controls, highlights and the BOX wordmark.'))
        text_appearance = QWidget()
        text_row = QHBoxLayout(text_appearance)
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(6)
        self._theme_text_mode = QComboBox()
        # Cocoa's checked-menu gutter needs room beyond the displayed value;
        # its native popup otherwise clips even these short option labels.
        self._theme_text_mode.setMinimumContentsLength(10)
        self._theme_text_mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for label, mode in (('White', 'white'), ('Black', 'black'), ('Custom', 'custom')):
            self._theme_text_mode.addItem(label, mode)
        self._theme_text_color = QPushButton()
        self._theme_text_color.setCursor(Qt.PointingHandCursor)
        self._theme_text_color.setToolTip('Choose text color for theme-colored selections and buttons')
        self._theme_text_color.clicked.connect(self._choose_theme_text_color)
        self._theme_text_mode.currentIndexChanged.connect(
            lambda _index: self._apply_theme_text(self._theme_text_mode.currentData(), save=True))
        text_row.addWidget(self._theme_text_mode)
        text_row.addWidget(self._theme_text_color)
        bl.addWidget(self._settings_row('Text color', text_appearance,
            'Text on theme-colored selections and buttons.'))
        self._sync_theme_text_controls()

        # --- PLAYER --------------------------------------------------------
        bl.addWidget(self._settings_section("PLAYER"))
        self._waveform_mode = QWidget()
        self._waveform_mode.setObjectName("WaveformModeControl")
        mode_layout = QHBoxLayout(self._waveform_mode)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        self._waveform_mode_group = QButtonGroup(self._waveform_mode)
        self._waveform_mode_group.setExclusive(True)
        self._waveform_mode_buttons = {}
        modes = ("BLUE", "RGB", "3BAND")
        for index, mode in enumerate(modes):
            button = QPushButton(mode)
            button.setObjectName("WaveformModeButton")
            button.setProperty(
                "segment",
                "first" if index == 0
                else "last" if index == len(modes) - 1
                else "middle",
            )
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(66, 30)
            button.clicked.connect(
                lambda _checked=False, value=mode:
                    self._on_waveform_mode_changed(value))
            self._waveform_mode_group.addButton(button)
            self._waveform_mode_buttons[mode] = button
            mode_layout.addWidget(button)
        bl.addWidget(self._settings_row(
            "Waveform display", self._waveform_mode,
            "Changes immediately. All three modes are prepared together, so "
            "switching never recooks the track."))

        # --- LIBRARY -------------------------------------------------------
        bl.addWidget(self._settings_section("LIBRARY"))
        self._chk_hide = QCheckBox(); self._chk_hide.toggled.connect(self._on_hide_toggled)
        bl.addWidget(self._settings_row(
            "Hide cue and placeholder tracks", self._chk_hide,
            "Skips rekordbox cue markers and stubs. Direct SoundBox imports "
            "are always exempt."))

        self._chk_nokey = QCheckBox(); self._chk_nokey.toggled.connect(self._on_sub_toggled)
        self._row_nokey = self._settings_row("Also hide keyless entries", self._chk_nokey,
                                             indent=22)
        bl.addWidget(self._row_nokey)

        self._chk_short = QCheckBox(); self._chk_short.toggled.connect(self._on_sub_toggled)
        # Custom [−] value [+] stepper — QSS-drawn buttons, crisp everywhere (the native
        # QSpinBox arrows rendered as sloppy empty boxes under the theme). Stepper on the
        # LEFT, toggle on the RIGHT so it lines up with the toggles above.
        self._btn_minus = QPushButton("−"); self._btn_minus.setObjectName("Stepper")
        self._btn_minus.setFixedSize(26, 26); self._btn_minus.setCursor(Qt.PointingHandCursor)
        self._btn_minus.clicked.connect(lambda: self._step_secs(-1))
        self._lbl_secs = QLabel(); self._lbl_secs.setObjectName("StepperVal")
        self._lbl_secs.setAlignment(Qt.AlignCenter); self._lbl_secs.setFixedWidth(52)
        self._btn_plus = QPushButton("+"); self._btn_plus.setObjectName("Stepper")
        self._btn_plus.setFixedSize(26, 26); self._btn_plus.setCursor(Qt.PointingHandCursor)
        self._btn_plus.clicked.connect(lambda: self._step_secs(1))
        self._set_secs_label()
        shortctl = QWidget(); scl = QHBoxLayout(shortctl)
        scl.setContentsMargins(0, 0, 0, 0); scl.setSpacing(4)
        scl.addWidget(self._btn_minus, 0); scl.addWidget(self._lbl_secs, 0)
        scl.addWidget(self._btn_plus, 0); scl.addSpacing(10)
        scl.addWidget(self._chk_short, 0)
        self._row_short = self._settings_row("Also hide very short entries", shortctl,
                                             indent=22)
        bl.addWidget(self._row_short)

        # --- BACKUP --------------------------------------------------------
        bl.addWidget(self._settings_section("BACKUP"))
        exp = QPushButton("Back Up…"); exp.setCursor(Qt.PointingHandCursor)
        exp.clicked.connect(self._backup_library)
        bl.addWidget(self._settings_row(
            "Back up SoundBox library", exp,
            "One .zip with everything: your fixes, playlists, settings, the "
            "library history and the whole cover-art cache."))

        imp = QPushButton("Restore…"); imp.setCursor(Qt.PointingHandCursor)
        imp.clicked.connect(self._restore_from_backup)
        bl.addWidget(self._settings_row(
            "Restore from backup", imp,
            "Pick a backup .zip — or the library.json inside an unzipped backup "
            "folder. Your current library is snapshotted first."))

        # --- COVER ART -------------------------------------------------------
        gl.addWidget(self._settings_section("COVER ART"))
        review = QPushButton("Review covers…")
        review.setCursor(Qt.PointingHandCursor)
        review.clicked.connect(self._open_art_review)
        gl.addWidget(self._settings_row(
            "Review existing cover artwork", review,
            "Suggested highlights flagged sources and different matches. All "
            "lets you browse every cover. Approve changes one track at a time, "
            "with recoverable originals; existing artwork stays protected."))
        cat = QPushButton("Open catalog…"); cat.setCursor(Qt.PointingHandCursor)
        cat.clicked.connect(self._open_artist_catalog)
        gl.addWidget(self._settings_row(
            "Auto-selected artist pictures", cat,
            "See which profile picture each avatar-fallback artist got, and "
            "replace it or choose a local image — applies to all their avatar "
            "tracks. Real covers are never touched."))

        defaults = QPushButton("Manage photos…")
        defaults.setCursor(Qt.PointingHandCursor)
        defaults.clicked.connect(self._open_default_photos)
        gl.addWidget(self._settings_row(
            "Default photos", defaults,
            "Your managed picture pool for tracks whose art is missing or was "
            "deliberately removed. Pictures are copied into SoundBox, balanced "
            "randomly, and prepared for stock iTunes and NightPod. Real covers "
            "and artist pictures are never touched."))

        self._sync_settings_controls()
        self._refresh_graveyard()
        scroll.setWidget(host)
        add_tab(scroll, "General")
        add_tab(self._analysis_settings_page(), "Analysis")
        from .usb_preferences import MySettingsEditor
        self._global_my_settings = MySettingsEditor(global_page=True)
        self._global_my_settings.set_profile(self.settings.get('cdj_my_settings',{}))
        self._global_my_settings.save_requested.connect(self._save_global_my_settings)
        add_tab(self._global_my_settings,'My Settings')
        from .keyboard import Keymap
        from .keyboard_page import KeyboardPage
        self._keyboard_page = KeyboardPage(
            Keymap(self._keyboard_router.keymap.dump()), self._save_keyboard)
        add_tab(self._keyboard_page, "Keyboard")
        add_tab(graveyard_page, "Graveyard")
        from .connections import RekordboxSection
        connections = QWidget()
        connection_layout = QHBoxLayout(connections)
        connection_layout.setContentsMargins(30, 24, 30, 30)
        connection_layout.setSpacing(40)
        self._rekordbox_section = RekordboxSection(
            self._choose_rekordbox_database, self._use_default_rekordbox_database,
            self._open_dialog, self._refresh_rekordbox, connections)
        connection_layout.addWidget(self._rekordbox_section, 1, Qt.AlignTop)
        if self.providers.allows("connections.soundcloud") and self.providers.connections is not None:
            from .connections import SoundCloudSection
            service = self.providers.connections.create(self._data_file.parent)
            self._soundcloud_section = SoundCloudSection(
                service, connections, isolated=self._isolated)
            connection_layout.addWidget(self._soundcloud_section, 1, Qt.AlignTop)
        else:
            connection_layout.addStretch(1)
        add_tab(connections, "Connections")
        v.addWidget(pages, 1)
        self._sync_rekordbox_connection()
        return page

    # ---------- graveyard ----------
    def _refresh_graveyard(self):
        g = getattr(self, "_grave", None)
        if g is None:
            return
        by_pid = {p.pid: p.name for p in (self.lib.playlists if self.lib else [])}

        def title_of(tid):
            t = self.lib.tracks_by_id.get(tid) if self.lib else None
            return self.titles.get(tid) or (t.name if t else f"track {tid}")

        g.setSortingEnabled(False)
        g.clear()
        for tid in sorted(self.hidden):
            it = QTreeWidgetItem([title_of(tid), self.hidden_dates.get(tid, "")])
            it.setData(0, _GRAVE_ROLE, ("del", tid, None))
            g.addTopLevelItem(it)
        for pid, bag in self.removals.items():
            pname = by_pid.get(pid, "?")
            for tid, date in bag.items():
                it = QTreeWidgetItem([f"{title_of(tid)}   (from {pname})", date or ""])
                it.setData(0, _GRAVE_ROLE, ("rem", tid, pid))
                g.addTopLevelItem(it)
        g.setSortingEnabled(True)

    def _grave_menu(self, pos):
        g = self._grave
        menu = QMenu(self)
        item = g.itemAt(pos)
        if item is not None:
            menu.addAction('Load Track', lambda: self._grave_load(item))
            menu.addSeparator()
        self._add_grave_restore_action(menu)
        if not menu.isEmpty():
            menu.exec(g.mapToGlobal(pos))

    def _grave_load(self, item):
        data = item.data(0, _GRAVE_ROLE) if item is not None else None
        track = self.lib.tracks_by_id.get(data[1]) if self.lib and data else None
        if track is None:
            self._flash_status('Track is no longer in the library', 5)
            return
        if self._open_track(track, autoplay=None, capture_autoplay=False):
            self._autoplay_order = []
            self._autoplay_index = None
            self._autoplay_random_seen = set()
            self._autoplay_random_queue = []
            self._update_player_navigation()

    def _add_grave_restore_action(self, menu):
        """Expose only selection-scoped restore; never a bulk shortcut."""
        g = self._grave
        if g.selectedItems():
            n = len(g.selectedItems())
            menu.addAction("Restore" if n == 1 else f"Restore {n} tracks",
                           self._grave_restore_selected)

    def _grave_restore_selected(self):
        picks = [it.data(0, _GRAVE_ROLE) for it in self._grave.selectedItems()]
        picks = [x for x in picks if x]
        if not picks:
            return
        restored_global = set()
        for kind, tid, pid in picks:
            if kind == "del":
                self.hidden.discard(tid)
                self.hidden_dates.pop(tid, None)
                restored_global.add(tid)
            else:
                bag = self.removals.get(pid) or {}
                bag.pop(tid, None)
                if not bag:
                    self.removals.pop(pid, None)
        self._after_track_edit(f"Restored {len(picks)} ✓")
        self._refresh_graveyard()
        if restored_global:
            # Restored tracks become active again. The ordinary bounded scan
            # prepares only those whose disposable analysis is now absent.
            self._maybe_fill_waveforms()

    def _begin_state_maintenance(self, *, restoring: bool = False) -> bool:
        """Close admission, drain existing writers, then freeze UI publication.

        Qt continues dispatching completions while draining. Existing results
        land before the final save; no old callbacks survive the restore boundary.
        """
        from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer
        from .workers import _running
        if (self._state_maintenance or self._backup_worker is not None
                or self._device_coordinator.active is not None):
            self._flash_status("Wait for the current operation before backup/restore", 8)
            return False
        self._state_maintenance = True
        self._settings_save_timer.stop()
        self._maintenance_resume_art = getattr(self, "_art_worker", None) is not None
        self._maintenance_resume_waveforms = getattr(self, "_waveform_rechecks_enabled", False)
        self._maintenance_resume_locations = getattr(self, "_import_location_worker", None) is not None
        self.setEnabled(False)
        self._maintenance_timers = [(t, t.interval()) for t in (
            self._dev_timer, self._warm_timer, self._waveform_recheck_timer,
            self._volume_refresh_timer) if t.isActive()]
        for timer, _interval in self._maintenance_timers:
            timer.stop()
        self._art_restart = False
        self._stock_art_rescan = False
        self._refresh_pending = False
        self._libcache_pending = None
        workers = list(dict.fromkeys(
            [v for v in vars(self).values() if isinstance(v, QThread)]
            + list(getattr(self, "_oneshots", []))))
        for worker in workers:
            if _running(worker):
                if hasattr(worker, "stop"):
                    worker.stop()
                elif hasattr(worker, "cancel"):
                    worker.cancel()
                else:
                    worker.requestInterruption()
        self._start_pulse("Finishing background work")
        tag_busy = lambda: bool(getattr(getattr(self, "tag_controller", None), "owned",
                                       getattr(getattr(self, "tag_controller", None), "busy", False)))
        if any(_running(w) for w in workers) or tag_busy():
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(20)
            timer.timeout.connect(lambda: loop.quit()
                                  if not any(_running(w) for w in workers)
                                  and not tag_busy() else None)
            timer.start()
            loop.exec(QEventLoop.ExcludeUserInputEvents)
            timer.stop()
        for worker in workers:
            try:
                worker.wait()  # includes thread-local cleanup after finished
            except RuntimeError:
                pass  # the existing completion owner already retired it
        QCoreApplication.sendPostedEvents(None, QEvent.MetaCall)
        saved = (restoring and not self._state_repository.writable) or self._save_state()
        if self._shutting_down or not saved:
            self._end_state_maintenance()
            return False
        self._state_publication = True
        return True

    def _end_state_maintenance(self):
        self._state_publication = False
        self._state_maintenance = False
        self.setEnabled(True)
        self._stop_pulse()
        if not self._shutting_down:
            from PySide6.QtCore import QTimer
            for timer, interval in getattr(self, "_maintenance_timers", []):
                timer.start(interval)
            if self._maintenance_resume_art:
                QTimer.singleShot(0, self._start_art_fill)
            if self._maintenance_resume_waveforms:
                QTimer.singleShot(0, self._maybe_fill_waveforms)
            if self._maintenance_resume_locations:
                QTimer.singleShot(0, self._repair_import_locations)

    def _run_state_task(self, operation, *, restoring: bool):
        from .workers import OneShotTask
        if not self._begin_state_maintenance(restoring=restoring):
            return
        worker = OneShotTask(operation)
        self._backup_worker = worker
        # Consume the result only after run() and queued finished delivery.
        result = []
        worker.done.connect(result.append)

        def finished():
            worker.wait()
            self._finish_worker("_backup_worker", worker)
            self._end_state_maintenance()
            if self._shutting_down:
                return
            outcome = result[0] if result else RuntimeError("State operation returned no result")
            if restoring:
                self._on_restore_done(outcome)
            else:
                self._on_backup_done(outcome)
        worker.finished.connect(finished)
        self._start_pulse("Restoring" if restoring else "Backing up")
        worker.start()

    def _backup_library(self):
        import time
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from soundbox import backup
        dest, _ = QFileDialog.getSaveFileName(
            self, "Back up SoundBox library",
            str(Path.home() / "Desktop" / f"soundbox-backup-{time.strftime('%Y-%m-%d')}.zip"),
            "SoundBox backup (*.zip)")
        if dest:
            self._run_state_task(
                lambda: backup.export_backup(self._data_file.parent, dest), restoring=False)

    def _on_backup_done(self, res):
        from PySide6.QtWidgets import QMessageBox
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Backup failed", str(res))
        else:
            self._flash_status("Backed up ✓", 8)

    def _restore_from_backup(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from soundbox import backup
        src, _ = QFileDialog.getOpenFileName(
            self, "Restore SoundBox library", "", "SoundBox backup (*.zip *.json)")
        if not src:
            return
        if QMessageBox.question(
                self, "Restore from backup",
                "Replace your SoundBox library with this backup?\n\n"
                "Background work will finish before restoration. Your current "
                "state is retained until the complete restore succeeds; a failed "
                "restore rolls back. Editing is paused during this operation.") != QMessageBox.Yes:
            return
        self._run_state_task(
            lambda: backup.restore_backup(self._data_file.parent, src), restoring=True)

    def _on_restore_done(self, res):
        from PySide6.QtWidgets import QMessageBox
        from soundbox.artwork import ArtCache
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Restore failed", str(res))
            return
        self.art_cache = ArtCache(self._data_file.parent / "art")
        self._load_state()
        self._flash_status("Restored ✓", 8)

    # ---------- title cleanup ----------
    def _title_junk(self) -> list:
        return metadata.junk_patterns(self.settings.get("title_rules_off"),
                                      self.settings.get("title_words"))

    def _refresh_title_rules(self):
        g = self._rules
        normalized = metadata.normalize_title_rules_off(
            self.settings.get("title_rules_off"))
        self.settings["title_rules_off"] = normalized
        off = set(normalized)
        g.blockSignals(True)
        g.clear()
        for rid, label, _pat in metadata.BUILTIN_TITLE_RULES:
            it = QTreeWidgetItem([label])
            if rid == "drop":
                it.setToolTip(0, "Remove the exact (Original Mix) tag.")
            it.setData(0, _GRAVE_ROLE, ("rule", rid))
            it.setCheckState(0, Qt.Unchecked if rid in off else Qt.Checked)
            g.addTopLevelItem(it)
        for w in self.settings.get("title_words") or []:
            if metadata.is_default_title_drop(w):
                continue
            label = (f"Drop “{w}”  (custom)" if metadata.is_literal_drop(w)
                     else f"Pattern  {w}  (custom, regex)")
            it = QTreeWidgetItem([label])
            it.setData(0, _GRAVE_ROLE, ("word", w))
            it.setCheckState(0, Qt.Checked)
            g.addTopLevelItem(it)
        rows = min(8, max(1, g.topLevelItemCount()))
        g.setFixedHeight(rows * max(20, g.sizeHintForRow(0)) + 2 * g.frameWidth())
        g.blockSignals(False)

    def _on_rule_toggled(self, item, _col):
        kind, val = item.data(0, _GRAVE_ROLE)
        checked = item.checkState(0) == Qt.Checked
        if kind == "rule":
            off = set(self.settings.get("title_rules_off") or [])
            (off.discard if checked else off.add)(val)
            self.settings["title_rules_off"] = sorted(off)
        else:  # unchecking a custom word removes it
            if not checked:
                words = [w for w in self.settings.get("title_words") or [] if w != val]
                self.settings["title_words"] = words
                self._refresh_title_rules()
        self._apply_and_refresh()

    def _add_drop_word(self):
        """Plain words get safe end/bracket semantics and an exact plain tag
        like ``(Original Mix)`` keeps its brackets literal. Other regex syntax
        runs as a raw case-insensitive regex (power users) — validated HERE so
        a typo'd pattern is rejected loudly, never silently ignored."""
        w = self._word_edit.text().strip()
        if not w:
            return
        if metadata.is_default_title_drop(w):
            self.settings["title_rules_off"] = [
                rid for rid in self.settings.get("title_rules_off", []) if rid != "drop"]
            self._word_edit.clear()
            self._refresh_title_rules()
            self._apply_and_refresh()
            return
        if not metadata.valid_pattern(w):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Invalid pattern",
                                f"“{w}” isn't a valid regular expression.\n\n"
                                "Plain words are always fine; advanced entries "
                                "must be valid regex.")
            return
        words = self.settings.get("title_words") or []
        if w not in words:
            self.settings["title_words"] = words + [w]
            self._word_edit.clear()
            self._refresh_title_rules()
            self._apply_and_refresh()

    def _rules_menu(self, pos):
        it = self._rules.itemAt(pos)
        data = it.data(0, _GRAVE_ROLE) if it else None
        if data and data[0] == "word":
            menu = QMenu(self)
            menu.addAction("Remove word", lambda: self._remove_drop_word(data[1]))
            menu.exec(self._rules.mapToGlobal(pos))

    def _remove_drop_word(self, w):
        self.settings["title_words"] = [x for x in self.settings.get("title_words") or []
                                        if x != w]
        self._refresh_title_rules()
        self._apply_and_refresh()

    def _open_settings(self):
        # The gear toggles: on Settings it returns to the main view (like Back).
        on_settings = self.body_stack.currentIndex() == 1
        self._sync_rekordbox_connection()
        if not on_settings:
            self._refresh_graveyard()  # the graveyard reflects the latest deletes
            self._refresh_title_rules()
        self.body_stack.setCurrentIndex(0 if on_settings else 1)

    def _close_settings(self):
        self.body_stack.setCurrentIndex(0)

    def _on_hide_toggled(self, checked: bool):
        self.settings["hide_untracked"] = bool(checked)
        self._sync_settings_enabled()
        self._apply_and_refresh()

    def _on_sub_toggled(self, _checked=False):
        self.settings["hide_nokey"] = self._chk_nokey.isChecked()
        self.settings["hide_short"] = self._chk_short.isChecked()
        self._sync_settings_enabled()  # seconds spinner follows the 'short' rule
        self._apply_and_refresh()

    def _set_secs_label(self):
        self._lbl_secs.setText(f"{int(self.settings.get('hide_short_secs', 5))} sec")

    def _step_secs(self, delta: int):
        v = max(1, min(60, int(self.settings.get("hide_short_secs", 5)) + delta))
        self.settings["hide_short_secs"] = v
        self._set_secs_label()
        # Re-render only if this threshold can actually change what's shown.
        if self.settings.get("hide_untracked") and self.settings.get("hide_short"):
            self._apply_and_refresh()
        else:
            self._save_state()

    def _sync_settings_enabled(self):
        """Sub-rows appear only when the master toggle is on (keeps the page tidy — no
        greyed clutter); the seconds spinner is live only when the 'short' rule is on."""
        on = bool(self.settings.get("hide_untracked"))
        for r in (getattr(self, "_row_nokey", None), getattr(self, "_row_short", None)):
            if r is not None:
                r.setVisible(on)
        # The stepper is live only when the 'short' rule is on.
        short_on = getattr(self, "_chk_short", None) is not None and self._chk_short.isChecked()
        for w in (getattr(self, "_btn_minus", None), getattr(self, "_btn_plus", None),
                  getattr(self, "_lbl_secs", None)):
            if w is not None:
                w.setEnabled(short_on)

    def _sync_settings_controls(self):
        """Reflect self.settings in the (already-built) Settings widgets, without
        firing their change handlers."""
        if not hasattr(self, "_chk_hide"):
            return
        self._sync_theme_text_controls()
        if hasattr(self, '_analysis_controls'):
            self._analysis_controls.set_preferences(self.settings.get('analysis'))
        for w, val in ((self._chk_hide, self.settings.get("hide_untracked")),
                       (self._chk_nokey, self.settings.get("hide_nokey", True)),
                       (self._chk_short, self.settings.get("hide_short", True))):
            w.blockSignals(True); w.setChecked(bool(val)); w.blockSignals(False)
        mode = str(self.settings.get("waveform_mode", "3BAND")).upper()
        if mode not in {"BLUE", "RGB", "3BAND"}:
            mode = "3BAND"
        for name, button in self._waveform_mode_buttons.items():
            button.blockSignals(True)
            button.setChecked(name == mode)
            button.blockSignals(False)
        self._set_secs_label()
        self._sync_settings_enabled()

    def _on_waveform_mode_changed(self, mode: str):
        normalized = str(mode).upper()
        if normalized not in {"BLUE", "RGB", "3BAND"}:
            return
        self.settings["waveform_mode"] = normalized
        if hasattr(self, "player_bar"):
            self.player_bar.set_waveform_mode(normalized)
        self._sync_settings_controls()
        self._schedule_settings_save()

    def _apply_and_refresh(self):
        """Persist settings, push them to the library, and re-render the view + counts."""
        self._save_state()
        self._apply_settings_to_lib()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))  # refresh sidebar counts
            self._reload_current_view()
