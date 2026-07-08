"""Settings page (mixin for MainWindow).

Layout: TWO centered fixed-width columns inside one scroll area —
LIBRARY + ARTIST MERGES + BACKUP on the left, GRAVEYARD + TITLE CLEANUP on the
right. The reusable framework: `_settings_section(title)` headers +
`_settings_row(title, control, subtitle, indent)` rows — add future settings by
dropping in more rows/sections, never hand-place widgets. Sub-options HIDE when
their parent toggle is off. Panel widgets (graveyard/rules lists) refresh every
time the gear opens.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

_GRAVE_ROLE = Qt.UserRole + 11  # ("del", tid, None) | ("rem", tid, pid)

import logging

from soundbox import engine, metadata

LOG = logging.getLogger("soundbox")


class SettingsPageMixin:
    def _apply_settings_to_lib(self):
        """Push current settings onto the loaded library (a pure display/export
        filter — never modifies rekordbox)."""
        if self.lib is not None:
            self.lib.hide_untracked = bool(self.settings.get("hide_untracked"))
            self.lib.hide_nokey = bool(self.settings.get("hide_nokey", True))
            self.lib.hide_short = bool(self.settings.get("hide_short", True))
            self.lib.hide_short_secs = int(self.settings.get("hide_short_secs", 5))
            self.lib.hidden_ids = set(self.hidden)  # deleted-from-SoundBox tracks
            self.lib.removed_by_playlist = {k: set(v)
                                            for k, v in self.removals.items() if v}
            self.lib.title_junk = self._title_junk()
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

        head = QWidget(); head.setObjectName("DeviceHeader")
        head.setAttribute(Qt.WA_StyledBackground, True); head.setFixedHeight(40)
        hh = QHBoxLayout(head); hh.setContentsMargins(14, 0, 14, 0); hh.setSpacing(10)
        back = QPushButton("←  Back"); back.setObjectName("BackBtn")
        back.setCursor(Qt.PointingHandCursor); back.clicked.connect(self._close_settings)
        hh.addWidget(back, 0)
        title = QLabel("SETTINGS"); title.setObjectName("SectionLabel")
        hh.addWidget(title, 1)
        v.addWidget(head)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        cols = QHBoxLayout(host)
        cols.setContentsMargins(30, 6, 30, 30); cols.setSpacing(40)
        cols.setAlignment(Qt.AlignTop)
        cols.addStretch(1)  # stretches both sides center the two columns

        # RIGHT column (added second) — the GRAVEYARD: every Collection-deleted track (and
        # per-playlist removal), with its date. Sort either column to find the
        # oldest/newest; right-click (or double-click) to resurrect.
        left = QWidget(); left.setFixedWidth(440)  # both date + track stay visible
        gl = QVBoxLayout(left); gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(2)
        gl.setAlignment(Qt.AlignTop)
        gl.addWidget(self._settings_section("GRAVEYARD"))
        self._grave = QTreeWidget()
        self._grave.setObjectName("Graveyard")
        self._grave.setHeaderLabels(["Track", "Deleted"])
        self._grave.setRootIsDecorated(False)
        self._grave.setAlternatingRowColors(True)
        self._grave.setSortingEnabled(True)  # click headers: oldest/newest first
        self._grave.sortByColumn(1, Qt.DescendingOrder)
        self._grave.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._grave.setFixedHeight(196)  # matches the LIBRARY group's footprint
        self._grave.header().resizeSection(0, 296)
        self._grave.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grave.customContextMenuRequested.connect(self._grave_menu)
        self._grave.itemDoubleClicked.connect(lambda *_: self._grave_restore_selected())
        gl.addWidget(self._grave)
        gsub = QLabel("Tracks deleted from Collection (and removed from playlists) "
                      "rest here — rekordbox and disk untouched. Double-click or "
                      "right-click to restore.")
        gsub.setObjectName("SettingsSub"); gsub.setWordWrap(True)
        gl.addWidget(gsub)

        # --- TITLE CLEANUP (named, toggleable rules — no regex soup) --------
        gl.addWidget(self._settings_section("TITLE CLEANUP"))
        self._rules = QTreeWidget()
        self._rules.setObjectName("TitleRules")
        self._rules.setHeaderHidden(True)
        self._rules.setRootIsDecorated(False)
        self._rules.setFixedHeight(214)
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
                      "iPod filenames). Words drop only from the END of a title "
                      "or a whole (bracket) — never mid-title. Advanced? Type a "
                      "regex — it runs as-is, like the built-ins. Manual renames "
                      "always win.")
        rsub.setObjectName("SettingsSub"); rsub.setWordWrap(True)
        gl.addWidget(rsub)
        self._refresh_title_rules()


        col = QWidget(); col.setFixedWidth(440)
        bl = QVBoxLayout(col); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(2)
        bl.setAlignment(Qt.AlignTop)
        cols.addWidget(col, 0, Qt.AlignTop)     # LIBRARY + BACKUP on the left
        cols.addWidget(left, 0, Qt.AlignTop)    # GRAVEYARD on the right
        cols.addStretch(1)

        # --- LIBRARY -------------------------------------------------------
        bl.addWidget(self._settings_section("LIBRARY"))
        self._chk_hide = QCheckBox(); self._chk_hide.toggled.connect(self._on_hide_toggled)
        bl.addWidget(self._settings_row(
            "Hide cue and placeholder tracks", self._chk_hide,
            "Skips rekordbox cue markers and stubs — never real tracks."))

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

        # --- ARTIST PICTURES -------------------------------------------------
        bl.addWidget(self._settings_section("ARTIST PICTURES"))
        cat = QPushButton("Open catalog…"); cat.setCursor(Qt.PointingHandCursor)
        cat.clicked.connect(self._open_artist_catalog)
        bl.addWidget(self._settings_row(
            "Auto-selected artist pictures", cat,
            "See which profile picture each avatar-fallback artist got, and "
            "swap it (URL / file / re-fetch) — applies to all their avatar "
            "tracks. Real covers are never touched."))

        self._sync_settings_controls()
        self._refresh_graveyard()
        scroll.setWidget(host)
        v.addWidget(scroll, 1)
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
        if g.selectedItems():
            n = len(g.selectedItems())
            menu.addAction("Restore" if n == 1 else f"Restore {n} tracks",
                           self._grave_restore_selected)
        if g.topLevelItemCount():
            menu.addAction("Restore All", self._restore_hidden)
        if not menu.isEmpty():
            menu.exec(g.mapToGlobal(pos))

    def _grave_restore_selected(self):
        picks = [it.data(0, _GRAVE_ROLE) for it in self._grave.selectedItems()]
        picks = [x for x in picks if x]
        if not picks:
            return
        for kind, tid, pid in picks:
            if kind == "del":
                self.hidden.discard(tid)
                self.hidden_dates.pop(tid, None)
            else:
                bag = self.removals.get(pid) or {}
                bag.pop(tid, None)
                if not bag:
                    self.removals.pop(pid, None)
        self._after_track_edit(f"Restored {len(picks)} ✓")
        self._refresh_graveyard()

    def _backup_library(self):
        """Full backup → one .zip (library.json + history + art cache + README),
        zipped OFF the UI thread (the art cache is ~200MB)."""
        import time as _time
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from soundbox import backup
        from .workers import OneShotTask, _running

        LOG.info("Back Up clicked (worker running: %s)",
                 _running(self._backup_worker))
        if _running(self._backup_worker):
            self._flash_status("Backup already running…", 5)
            return
        self._save_state()  # the zip should contain the very latest state
        self.raise_(); self.activateWindow()  # the save SHEET attaches to us
        dest, _ = QFileDialog.getSaveFileName(
            self, "Back up SoundBox library",
            str(Path.home() / "Desktop" /
                f"soundbox-backup-{_time.strftime('%Y-%m-%d')}.zip"),
            "SoundBox backup (*.zip)")
        LOG.info("Back Up dest: %r", dest)
        if not dest:
            return
        data_dir = self._data_file.parent
        w = OneShotTask(lambda: backup.export_backup(data_dir, dest))
        self._backup_worker = w
        w.done.connect(self._on_backup_done)
        w.finished.connect(lambda ww=w: self._finish_worker("_backup_worker", ww))
        self._start_pulse("BACKING UP")
        w.start()

    def _on_backup_done(self, res):
        from PySide6.QtWidgets import QMessageBox
        self._stop_pulse()
        if isinstance(res, Exception):
            self.status_label.setText("Ready")
            QMessageBox.warning(self, "Backup failed", str(res))
        else:
            self._flash_status("Backed up ✓", 8)

    def _restore_from_backup(self):
        """Restore a full backup (.zip, or a library.json inside a backup
        folder). Current state is snapshotted first — never destructive."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from soundbox import backup
        from .workers import OneShotTask, _running

        if _running(self._backup_worker):
            return
        src, _ = QFileDialog.getOpenFileName(
            self, "Restore SoundBox library", "",
            "SoundBox backup (*.zip *.json)")
        if not src:
            return
        if QMessageBox.question(
                self, "Restore from backup",
                "Replace your current SoundBox library with this backup?\n\n"
                "Your fixes, playlists and settings will be swapped for the "
                "backup's. The current state is snapshotted into the history "
                "first, so nothing is lost.") != QMessageBox.Yes:
            return
        data_dir = self._data_file.parent
        w = OneShotTask(lambda: backup.restore_backup(data_dir, src))
        self._backup_worker = w
        w.done.connect(self._on_restore_done)
        w.finished.connect(lambda ww=w: self._finish_worker("_backup_worker", ww))
        self._start_pulse("RESTORING")
        w.start()

    def _on_restore_done(self, res):
        from PySide6.QtWidgets import QMessageBox
        from soundbox import artfetch
        self._stop_pulse()
        if isinstance(res, Exception):
            self.status_label.setText("Ready")
            QMessageBox.warning(self, "Restore failed", str(res))
            return
        # Reload the world from the restored files: fresh art cache index +
        # full state reload (which also re-reads the live rekordbox library).
        self.art_cache = artfetch.ArtCache(self._data_file.parent / "art")
        self._load_state()
        self._flash_status("Restored ✓", 8)

    # ---------- title cleanup ----------
    def _title_junk(self) -> list:
        return metadata.junk_patterns(self.settings.get("title_rules_off"),
                                      self.settings.get("title_words"))

    def _refresh_title_rules(self):
        g = self._rules
        off = set(self.settings.get("title_rules_off") or [])
        g.blockSignals(True)
        g.clear()
        for rid, label, _pat in metadata.BUILTIN_TITLE_RULES:
            it = QTreeWidgetItem([label])
            it.setData(0, _GRAVE_ROLE, ("rule", rid))
            it.setCheckState(0, Qt.Unchecked if rid in off else Qt.Checked)
            g.addTopLevelItem(it)
        for w in self.settings.get("title_words") or []:
            label = (f"Drop “{w}”  (yours)" if metadata.is_plain_word(w)
                     else f"Pattern  {w}  (yours, regex)")
            it = QTreeWidgetItem([label])
            it.setData(0, _GRAVE_ROLE, ("word", w))
            it.setCheckState(0, Qt.Checked)
            g.addTopLevelItem(it)
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
        """Plain words get the safe end/bracket semantics; anything with regex
        syntax runs as a raw case-insensitive regex (power users) — validated
        HERE so a typo'd pattern is rejected loudly, never silently ignored."""
        w = self._word_edit.text().strip()
        if not w:
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
        for w, val in ((self._chk_hide, self.settings.get("hide_untracked")),
                       (self._chk_nokey, self.settings.get("hide_nokey", True)),
                       (self._chk_short, self.settings.get("hide_short", True))):
            w.blockSignals(True); w.setChecked(bool(val)); w.blockSignals(False)
        self._set_secs_label()
        self._sync_settings_enabled()

    def _apply_and_refresh(self):
        """Persist settings, push them to the library, and re-render the view + counts."""
        self._save_state()
        self._apply_settings_to_lib()
        if self.lib is not None:
            self._populate_tree(engine.summary(self.lib))  # refresh sidebar counts
            self._reload_current_view()

