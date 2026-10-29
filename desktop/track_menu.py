"""Deck options and captured single-track actions in the prep toolbar."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QActionGroup, QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QToolButton

from soundbox.beat_count import MODES
from . import theme
from .devices import device_facts


class TrackMenuButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('GridEditButton')
        self.setFixedSize(44, 28)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName('Track menu')
        self.setToolTip('Track display, USB export and analysis')

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.TEXT_HI), 1.5, Qt.SolidLine, Qt.RoundCap))
        for offset in (-5, 0, 5):
            y = self.height() / 2 + offset
            painter.drawLine(QPointF(self.width() / 2 - 7, y), QPointF(self.width() / 2 + 7, y))


class TrackMenuMixin:
    def _track_menu(self):
        menu = QMenu(self)
        colors = menu.addMenu('Change waveform color')
        group = QActionGroup(colors)
        for mode, title in (('BLUE', 'Blue'), ('RGB', 'RGB'), ('3BAND', '3Band')):
            action = colors.addAction(title)
            action.setCheckable(True)
            action.setChecked(self.settings.get('waveform_mode', '3BAND') == mode)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=mode: self._on_waveform_mode_changed(value))
        counts = menu.addMenu('Beat Count Display')
        group = QActionGroup(counts)
        for mode, title in enumerate(MODES):
            action = counts.addAction(title)
            action.setCheckable(True)
            action.setChecked(self.settings.get('beat_count_mode', 0) == mode)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=mode: self._set_beat_count_mode(value))
        track_id = self.player_bar.current_track()
        track = self.lib.tracks_by_id.get(track_id) if self.lib else None
        exports = menu.addMenu('Export to USB')
        for dev, profile in self._device_profiles.items():
            facts = device_facts(dev, cached_only=True)
            if profile.kind != 'generic_usb' or not getattr(profile, 'cdj_export', False):
                continue
            target = (self._device_mount_tokens.get(dev, 0), facts.volume_uuid)
            action = exports.addAction(dev.rsplit('/', 1)[-1])
            action.setEnabled(bool(track and not track.imported and facts.writable and facts.volume_uuid
                                   and not self._device_operation_owned()))
            action.triggered.connect(lambda _checked=False, path=dev, captured=target, song=track:
                self._begin_cdj_export(path, standalone=(song,), from_drop=True, drop_target=captured))
        if not exports.actions():
            exports.addAction('Connect a writable FAT32 or exFAT USB').setEnabled(False)
        analyze = menu.addAction('Analyze Track')
        analyze.setEnabled(bool(track and not self._shutting_down and not self._state_maintenance))
        analyze.triggered.connect(lambda: self._analyze_tracks((track_id,)))
        return menu

    def _show_track_menu(self):
        menu = self._track_menu()
        menu.exec(self.track_menu_btn.mapToGlobal(self.track_menu_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _set_beat_count_mode(self, mode, *, save=True):
        mode = mode if type(mode) is int and 0 <= mode <= 2 else 0
        self.settings['beat_count_mode'] = mode
        self.player_bar.set_beat_count_mode(mode)
        if save:
            self._schedule_settings_save()
