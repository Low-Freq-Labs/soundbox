"""Deck options and source-bound browser actions independent of row positions."""
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QActionGroup, QColor, QPainter, QPen
from PySide6.QtWidgets import QToolButton
from .choices import Menu as QMenu, exec_transient_menu

from soundbox.beat_count import MODES
from . import theme
from .devices import device_facts


def source_scope(library):
    return ((str(library.source), library.source_kind, library.source_library_id)
            if library is not None else None)


def _track_pin(track):
    if track is None:
        return None
    return (track.track_id, track.native_id, track.source_uuid,
            str(track.path), str(track.native_path), track.source_native_path)


@dataclass(frozen=True)
class TrackSelection:
    """Detached admission facts; existing writers still own write authority."""
    source: tuple | None
    pins: tuple

    def matches(self, window):
        library = window.lib
        return bool(library is not None and source_scope(library) == self.source
                    and self.pins and all(pin is not None
                        and _track_pin(library.tracks_by_id.get(identity)) == pin
                        for identity, pin in self.pins))


def capture_tracks(window, identities):
    library = window.lib
    return TrackSelection(source_scope(library), tuple(
        (identity, _track_pin(library.tracks_by_id.get(identity)) if library else None)
        for identity in dict.fromkeys(identities)))


def _playlist_scope(window, name):
    controller = getattr(window, 'unification_controller', None)
    state = controller.state() if controller is not None else None
    unified = bool(state and state['customs_done'])
    node = state.get('nodes', {}).get('custom:'+name, {}) if unified else {}
    return (tuple(window.custom[name]) if name in window.custom else None,
            unified, node.get('id'), node.get('uuid'))


def _view_scope(window):
    key = getattr(window, '_view_key', None)
    if key and key.startswith('rb:') and getattr(window, '_active_native_playlist_id', None):
        key = ('native', window._active_native_playlist_id)
    return (key, window.stack.currentIndex(), window.body_stack.currentIndex())


def _visible_item(item):
    while item is not None:
        if item.isHidden():
            return False
        item = item.parent()
    return True


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
    def _track_menu_action(self, binding, callback):
        def invoke():
            if (getattr(self, '_shutting_down', False)
                    or getattr(self, '_state_maintenance', False) or not binding.matches(self)):
                self._flash_status('The selected tracks changed; reopen this menu', 6)
                return False
            return callback()
        return invoke

    def _playlist_menu_action(self, binding, name, callback):
        scope = _playlist_scope(self, name)

        def invoke():
            if scope[0] is None or _playlist_scope(self, name) != scope:
                self._flash_status('That playlist changed; reopen this menu', 6)
                return False
            return callback()
        return self._track_menu_action(binding, invoke)

    def _table_menu_edit(self, index):
        """Capture an occurrence, then find its current visible index on action."""
        row = self.model._rows[index.row()]
        identity = row['id']
        binding = capture_tracks(self, (identity,))
        scope = _view_scope(self)
        occurrence = self._native_row_identity(row)
        column = index.column()
        rows = [value for value in self.model._all_rows if value.get('id') == identity]
        shared_ordinal = sum(value is row for value in self.model._rows[:index.row()])
        ordinal = [at for at, value in enumerate(rows) if value is row][shared_ordinal]
        count = len(rows)
        # Local duplicates have no membership UUID. Only retain their ordinal
        # while their complete membership order remains unchanged.
        order = tuple(value.get('id') for value in self.model._all_rows) if count > 1 else None

        def edit():
            target = None
            shared_ordinal = 0
            if _view_scope(self) == scope:
                if occurrence[0] == 'native-membership':
                    matches = [value for value in self.model._all_rows
                               if value.get('id') == identity and self._native_row_identity(value) == occurrence]
                    if len(matches) == 1:
                        target = matches[0]
                else:
                    current = [value for value in self.model._all_rows if value.get('id') == identity]
                    if (len(current) == count and (order is None
                            or tuple(value.get('id') for value in self.model._all_rows) == order)):
                        target = current[ordinal]
                        shared_ordinal = sum(value is target for value in current[:ordinal])
            visible = [at for at, value in enumerate(self.model._rows) if value is target]
            if shared_ordinal >= len(visible):
                self._flash_status('That song is no longer in this view; reopen its menu', 6)
                return False
            return self._table_edit_at(self.model.index(visible[shared_ordinal], column))
        return self._track_menu_action(binding, edit)

    def _artists_menu_edit(self, identity, column):
        binding = capture_tracks(self, (identity,))
        scope = _view_scope(self)

        def edit():
            item = self._artists_browser._tracks.get(identity)
            if (_view_scope(self) != scope or item is None
                    or item.treeWidget() is not self.artists_view or not _visible_item(item)):
                self._flash_status('That song is no longer in this view; reopen its menu', 6)
                return False
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.artists_view.setCurrentItem(item, column)
            return self.artists_view.editItem(item, column)
        return self._track_menu_action(binding, edit)

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
        colors.ensurePolished()
        colors.setFixedWidth(max(colors.fontMetrics().horizontalAdvance(action.text())
                                 for action in colors.actions())+48)
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
                                   and not self._device_operation_owned(dev)))
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
        exec_transient_menu(menu, self.track_menu_btn.mapToGlobal(self.track_menu_btn.rect().bottomLeft()))

    def _set_beat_count_mode(self, mode, *, save=True):
        mode = mode if type(mode) is int and 0 <= mode <= 2 else 0
        self.settings['beat_count_mode'] = mode
        self.player_bar.set_beat_count_mode(mode)
        if save:
            self._schedule_settings_save('beat_count_mode')
