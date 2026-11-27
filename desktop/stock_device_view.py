"""Read-only Device / Songs / Playlists pages for stock and dual iPods."""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QMimeData, QModelIndex, QPersistentModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGridLayout,
    QHeaderView,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .choices import Menu as QMenu, exec_transient_menu

if TYPE_CHECKING:
    from soundbox.stock_ipod_info import StockIpodSnapshot
from soundbox.manifest import composite_library_counts

from .devices import human
from .device_playlists import create_playlist_page
from .trees import BrowserDetailTree, TrackBrowserTree, _start_chip_drag
from . import theme


_ORDER_KEY = Qt.UserRole + 3


class DevicePlaylistTree(TrackBrowserTree):
    """Only same-view, freshly captured parent drags can request an order save."""
    MIME = 'application/x-soundbox-device-playlist-order-v1'

    def __init__(self, pages):
        super().__init__(pages)
        self.pages = pages
        self._drag_origin = None
        self._drag_seat = None
        self._drag_active = False
        self._insert_y = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(False)

    def _seat(self):
        return (self.currentItem(), self.currentColumn(), self.selectedItems(),
                [(self.topLevelItem(i), self.topLevelItem(i).isExpanded())
                 for i in range(self.topLevelItemCount())], self.verticalScrollBar().value())

    def _restore_seat(self, seat):
        current, column, selected, expanded, scroll = seat
        for item, value in expanded:
            if item.treeWidget() is self:item.setExpanded(value)
        if current is not None and current.treeWidget() is self:
            self.setCurrentItem(current, column, QItemSelectionModel.NoUpdate)
        self.clearSelection()
        for item in selected:
            if item.treeWidget() is self:item.setSelected(True)
        self.verticalScrollBar().setValue(scroll)

    def mousePressEvent(self, event):
        self._drag_seat = self._seat()
        item = self.itemAt(event.position().toPoint())
        self._drag_origin = (QPersistentModelIndex(self.indexFromItem(item))
            if event.button() == Qt.LeftButton and item is not None and item.parent() is None
            and item.data(0, _ORDER_KEY) else None)
        self._drag_position = event.position().toPoint()
        super().mousePressEvent(event)
        if self._container_press is None:self._drag_origin = None

    def mouseMoveEvent(self, event):
        if (self._drag_origin is not None and self._drag_origin.isValid()
                and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self._drag_position).manhattanLength()
                    >= QApplication.startDragDistance()):
            self.startDrag(Qt.MoveAction)
            return
        super().mouseMoveEvent(event)

    def _order_mime(self, item):
        mime = QMimeData()
        mime.setData(self.MIME, json.dumps(dict(revision=self.pages._sync_revision,
            order=self.pages._managed_playlist_order(), identity=item.data(0, _ORDER_KEY))).encode())
        return mime

    def startDrag(self, _actions):
        origin = self._drag_origin
        self._drag_origin = None
        self._container_press = None
        if origin is None or not origin.isValid() or not self.pages._playlist_order_ready():return
        item = self.itemFromIndex(QModelIndex(origin))
        seat = self._drag_seat
        revision = self.pages._sync_revision
        try:
            self._drag_active = True
            _start_chip_drag(self, self._order_mime(item), item.text(0), Qt.MoveAction)
        finally:
            self._drag_active = False
            self._insert_y = None
            self.viewport().update()
            if seat is not None and revision == self.pages._sync_revision:self._restore_seat(seat)

    def _drag_request(self, event):
        if (event.source() is not self or not self.pages._playlist_order_ready()
                or not event.mimeData().hasFormat(self.MIME)):return None
        try:
            raw = bytes(event.mimeData().data(self.MIME))
            if len(raw) > 64 * 1024:return None
            captured = json.loads(raw)
            order = self.pages._managed_playlist_order()
            if (captured['revision'] != self.pages._sync_revision or captured['order'] != list(order)
                    or captured['identity'] not in order):return None
            return captured['identity'], order
        except (ValueError, TypeError, KeyError):return None

    def _destination(self, position, identity):
        item = self.itemAt(position)
        while item is not None and item.parent() is not None:item = item.parent()
        if item is None:
            items = [self.topLevelItem(i) for i in range(self.topLevelItemCount())
                     if self.topLevelItem(i).data(0, _ORDER_KEY)]
            if not items:return None
            item = items[-1];after = True
        else:
            after = position.y() > self.visualItemRect(item).center().y()
        anchor = item.data(0, _ORDER_KEY)
        if not anchor or anchor == identity:return None
        last = item
        if after:
            while last.isExpanded() and last.childCount():last = last.child(last.childCount() - 1)
        return anchor, after, self.visualItemRect(last).bottom() if after else self.visualItemRect(item).top()

    def dragEnterEvent(self, event):
        event.acceptProposedAction() if self._drag_request(event) is not None else event.ignore()

    def dragMoveEvent(self, event):
        request = self._drag_request(event)
        destination = self._destination(event.position().toPoint(), request[0]) if request else None
        self._insert_y = destination[2] if destination else None
        self.viewport().update()
        event.acceptProposedAction() if destination else event.ignore()

    def dragLeaveEvent(self, event):
        self._insert_y = None;self.viewport().update();event.accept()

    def dropEvent(self, event):
        request = self._drag_request(event)
        destination = self._destination(event.position().toPoint(), request[0]) if request else None
        self._insert_y = None;self.viewport().update()
        if destination is None:event.ignore();return
        identity, previous = request;anchor, after, _y = destination
        order = [key for key in previous if key != identity]
        order.insert(order.index(anchor) + int(after), identity)
        if tuple(order) == previous:event.ignore();return
        self.pages._drop_playlist_order(tuple(order))
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._insert_y is not None:
            painter = QPainter(self.viewport());painter.setPen(QColor(theme.PURPLE))
            painter.drawLine(0, self._insert_y, self.viewport().width(), self._insert_y)
            painter.end()


def _duration(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours else f"{minutes}:{seconds:02d}")


def _storage_sizes(capacity: int, free: int) -> dict[str, str]:
    """Use one decimal unit so terabyte rounding cannot hide used space."""
    divisor, unit = 1, 'B'
    for candidate, label in ((1_000, 'KB'), (1_000_000, 'MB'), (1_000_000_000, 'GB')):
        if capacity >= candidate:
            divisor, unit = candidate, label
    total = (max(0, capacity) * 10 + divisor // 2) // divisor
    available = (max(0, free) * 10 + divisor // 2) // divisor
    # Balance the displayed rounded totals; the underlying byte facts stay exact.
    values = {'Capacity': total, 'Used': max(0, total - available), 'Free': available}
    return {key: f'{value / 10:,.1f} {unit}' for key, value in values.items()}


def _regular_user_playlists(snapshot: StockIpodSnapshot):
    """Return owner-created lists, excluding Apple's empty smart aliases."""
    smart_titles = {
        item.title.casefold()
        for item in snapshot.playlists
        if item.kind == "Smart playlist"
    }
    return tuple(
        item for item in snapshot.playlists
        if item.kind == "Playlist"
        and not (not item.entries and item.title.casefold() in smart_titles)
    )


def _managed_playlists(snapshot: StockIpodSnapshot):
    """Return user lists plus the narrowly removable Apple Videos index."""
    regular = _regular_user_playlists(snapshot)
    videos = tuple(
        item for item in snapshot.playlists
        if item.kind == "Smart playlist" and item.title.casefold() == "videos"
    )
    return (*regular, *videos)


@dataclass(frozen=True)
class CompositeDeviceData:
    """Validated schema-3 metadata prepared by the device read worker."""

    manifest: dict[str, Any]
    playlist_entries: dict[str, tuple[str, ...]]
    rockbox_database_auto_update: bool = False
    rockbox_database_files: int = 0
    rockbox_database_refresh_pending: bool = False
    changed_playlist_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DeviceSongRow:
    title: str
    artist: str
    filetype: str
    size: int
    length_ms: int
    play_count: int
    skip_count: int
    location: str
    dbid: int
    availability: str


@dataclass(frozen=True)
class DevicePlaylistRow:
    identity: str
    title: str
    entries: tuple[DeviceSongRow, ...]
    availability: str
    managed: bool
    stock_playlist_id: str = ""
    sync_needed: bool = False


def _stock_device_path(location: str) -> str:
    text = str(location or "").replace("\\", "/")
    if text.startswith(":"):
        return "/" + text.lstrip(":").replace(":", "/")
    return "/" + text.lstrip("/")


def _stock_song(track, availability: str = "iTunes") -> DeviceSongRow:
    return DeviceSongRow(
        track.title,
        track.artist,
        track.filetype,
        track.size,
        track.length_ms,
        track.play_count,
        track.skip_count,
        _stock_device_path(track.location),
        track.dbid,
        availability,
    )


def _composite_song_rows(
        snapshot: StockIpodSnapshot,
        composite: CompositeDeviceData | None,
) -> tuple[DeviceSongRow, ...]:
    if composite is None:
        return tuple(_stock_song(track) for track in snapshot.tracks)
    stock_by_dbid = {track.dbid: track for track in snapshot.tracks if track.dbid}
    stock_by_path = {
        _stock_device_path(track.location): track for track in snapshot.tracks}
    claimed_dbids: set[int] = set()
    output: list[DeviceSongRow] = []
    for device_path, raw in composite.manifest["files"].items():
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "")
        if kind not in {"shared-stock-media", "rockbox-media"}:
            continue
        dbid = int(raw.get("dbid") or 0)
        stock_track = stock_by_dbid.get(dbid) or stock_by_path.get(device_path)
        if stock_track is not None:
            claimed_dbids.add(stock_track.dbid)
            output.append(_stock_song(stock_track, "iTunes/Rockbox"))
            continue
        suffix = Path(str(device_path)).suffix.lstrip(".").upper()
        output.append(DeviceSongRow(
            str(raw.get("title") or Path(str(device_path)).stem),
            str(raw.get("artist") or ""),
            suffix,
            int(raw.get("size") or 0),
            0,
            0,
            0,
            str(device_path),
            dbid,
            "Rockbox",
        ))
    output.extend(
        _stock_song(track)
        for track in snapshot.tracks
        if track.dbid not in claimed_dbids
    )
    return tuple(sorted(
        output,
        key=lambda item: (
            item.title.casefold(), item.artist.casefold(), item.location.casefold()),
    ))


def _composite_playlist_rows(
        snapshot: StockIpodSnapshot,
        composite: CompositeDeviceData | None,
        songs: tuple[DeviceSongRow, ...],
) -> tuple[DevicePlaylistRow, ...]:
    if composite is None:
        by_dbid = {item.dbid: item for item in songs if item.dbid}
        return tuple(DevicePlaylistRow(
            str(playlist.playlist_id),
            playlist.title,
            tuple(
                by_dbid[entry.dbid] for entry in playlist.entries
                if entry.dbid in by_dbid),
            "iTunes",
            True,
            str(playlist.playlist_id),
        ) for playlist in _managed_playlists(snapshot))

    by_path = {item.location: item for item in songs}
    stock_playlists = {
        playlist.title: playlist
        for playlist in _regular_user_playlists(snapshot)
    }
    output: list[DevicePlaylistRow] = []
    playlist_rows = composite.manifest["playlists"]
    order = composite.manifest.get("playlist_order")
    if not isinstance(order, list):
        order = list(playlist_rows)
    for raw_key in order:
        key = str(raw_key)
        row = playlist_rows.get(key)
        if not isinstance(row, dict):
            continue
        title = str(row.get("device_name") or " / ".join([
            *map(str, row.get("folder_path") or []),
            str(row.get("name") or "Untitled playlist"),
        ]))
        stock_playlist = stock_playlists.pop(title, None)
        changed = key in composite.changed_playlist_keys
        entries = () if changed else tuple(
            by_path[path] for path in composite.playlist_entries.get(key, ())
            if path in by_path)
        output.append(DevicePlaylistRow(
            f"managed:{key}",
            title,
            entries,
            "iTunes/Rockbox" if stock_playlist is not None else "Rockbox",
            True,
            str(stock_playlist.playlist_id) if stock_playlist is not None else "",
            changed,
        ))
    stock_songs = {item.dbid: item for item in songs if item.dbid}
    output.extend(DevicePlaylistRow(
        "",
        playlist.title,
        tuple(
            stock_songs[entry.dbid] for entry in playlist.entries
            if entry.dbid in stock_songs),
        "iTunes",
        False,
        str(playlist.playlist_id),
    ) for playlist in stock_playlists.values())
    return tuple(output)


class StockMusicModel(QAbstractTableModel):
    """Immutable adapter over the unified device-song view."""

    COLUMNS = (
        ("Title", lambda item: item.title),
        ("Artist", lambda item: item.artist or "—"),
        ("Format", lambda item: item.filetype or "—"),
        ("Size", lambda item: human(item.size)),
        ("Time", lambda item: _duration(item.length_ms)),
        ("Plays", lambda item: str(item.play_count)),
        ("Skips", lambda item: str(item.skip_count)),
        ("Type", lambda item: item.availability),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks = ()

    def set_snapshot(
            self,
            snapshot: StockIpodSnapshot | None,
            composite: CompositeDeviceData | None = None,
    ):
        self.beginResetModel()
        self.tracks = (
            _composite_song_rows(snapshot, composite)
            if snapshot is not None else ())
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()):
        return len(self.tracks)

    def set_playback(self, snapshot: StockIpodSnapshot) -> bool:
        """Patch observed play/skip totals without resetting selection or scroll."""
        rows = _composite_song_rows(snapshot, None)
        if len(rows) != len(self.tracks) or any(
                replace(new, play_count=old.play_count, skip_count=old.skip_count) != old
                for old, new in zip(self.tracks, rows)):
            return False
        changed = [index for index, (old, new) in enumerate(zip(self.tracks, rows)) if old != new]
        self.tracks = rows
        if changed:
            self.dataChanged.emit(self.index(changed[0], 5), self.index(changed[-1], 6), [Qt.DisplayRole])
        return True

    def columnCount(self, _parent=QModelIndex()):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.tracks)):
            return None
        if role == Qt.DisplayRole:
            return self.COLUMNS[index.column()][1](self.tracks[index.row()])
        if role == Qt.TextAlignmentRole and index.column() in {3, 4, 5, 6}:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            track = self.tracks[index.row()]
            return (
                f"{track.title}\n"
                f"{track.artist or 'Artist not stored on device'}\n"
                f"{track.location}\nDBID {track.dbid}"
            )
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section][0]
        return None


class StockIpodPages(QWidget):
    """The three content pages controlled by header-level tab buttons."""

    confirm_model_requested = Signal()
    # Apple playlist IDs are unsigned 64-bit values.  A Qt ``int`` signal is
    # signed 32-bit, and QVariant cannot store values above signed 64-bit.
    # Preserve the exact decimal representation across the Qt boundary and
    # convert it back to a Python int only in the stock-iPod engine call.
    delete_playlists_requested = Signal(object)
    playlist_order_requested = Signal(object)
    primary_action_requested = Signal()
    # Title stretches with the window; the remaining operational columns keep
    # predictable widths. Album and the internal device path intentionally stay
    # out of this view—the tooltip still exposes the path for diagnostics.
    MUSIC_FIXED_COLUMN_WIDTHS = (150, 75, 80, 60, 55, 55, 150)
    SYNC_LABELS = {
        "up_to_date": "Up to date",
        "local_changes": "Local changes ready",
        "reconcile_required": "Reconciliation required",
        "untracked": "Not yet tracked on this Mac",
        "catalog_linked": "Playlists linked",
        "composite_untracked": "Not synced yet",
        "comparison_pending": "Sync status pending",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        general = QWidget()
        general_layout = QGridLayout(general)
        general_layout.setContentsMargins(16, 12, 16, 12)
        general_layout.setSpacing(10)
        self.detail_layout = general_layout
        self.detail_trees: dict[str, QTreeWidget] = {}
        for position, title in enumerate(("Identity", "Storage", "Database", "SoundBox")):
            tree = self._detail_tree(title)
            self.detail_trees[title] = tree
            general_layout.addWidget(tree, position // 2, position % 2)
        general_layout.setColumnStretch(0, 1)
        general_layout.setColumnStretch(1, 1)
        general_layout.setRowStretch(0, 10)
        general_layout.setRowStretch(1, 8)
        # Compatibility alias for callers that used the original single tree.
        self.general_tree = self.detail_trees["Identity"]
        self.confirm_model_button = QPushButton("Confirm model…")
        self.confirm_model_button.setObjectName("Ghost")
        self.confirm_model_button.clicked.connect(self.confirm_model_requested)
        general_layout.addWidget(self.confirm_model_button, 2, 0, 1, 2, Qt.AlignLeft)
        self.stack.addWidget(general)

        self.music_model = StockMusicModel(self)
        self.music_table = QTableView()
        self.music_table.setObjectName("StockDeviceMusic")
        self.music_table.setModel(self.music_model)
        self.music_table.setAlternatingRowColors(True)
        self.music_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.music_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.music_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.music_table.verticalHeader().setVisible(False)
        self.music_table.verticalHeader().setDefaultSectionSize(24)
        music_header = self.music_table.horizontalHeader()
        music_header.setDefaultAlignment(Qt.AlignCenter)
        music_header.setStretchLastSection(False)
        music_header.setSectionResizeMode(QHeaderView.Interactive)
        music_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in enumerate(self.MUSIC_FIXED_COLUMN_WIDTHS, 1):
            self.music_table.setColumnWidth(column, width)
        self._music_widths_applied = False
        self.stack.addWidget(self.music_table)

        playlist_page = create_playlist_page(DevicePlaylistTree(self))
        self.playlist_tree = playlist_page.tree
        self.playlist_tree.customContextMenuRequested.connect(
            self._playlist_context_menu)
        self.playlist_tree.itemChanged.connect(
            self._update_delete_playlist_button)
        self.playlist_tree.currentItemChanged.connect(
            self._sync_playlist_button)
        self.sync_playlist_button = playlist_page.primary
        self.sync_playlist_button.clicked.connect(self._request_primary_action)
        self.delete_playlist_button = playlist_page.delete
        self.delete_playlist_button.clicked.connect(self._request_playlist_delete)
        self.stack.addWidget(playlist_page.page)
        self._playlist_delete_available = False
        self._playlist_order_available = False
        self._pending_playlist_order = None
        self._pending_playlist_order_token = None
        self._order_refresh_seat = None
        self._snapshot_scope = None
        self._committed_playlist_order: tuple[str, ...] = ()
        self._has_snapshot = False
        self._presented_snapshot: StockIpodSnapshot | None = None
        self._presented_composite: CompositeDeviceData | None = None
        self._playlist_sync_available = False
        self._playlist_actions_available = True
        self._primary_action = 'Export'
        self._playlist_actions_reason = ""
        self._playlist_write_reason = ""
        self._sync_revision = 0

    def set_page(self, index: int):
        index = max(0, min(2, int(index)))
        self.stack.setCurrentIndex(index)
        if index == 1 and not self._music_widths_applied:
            # Qt can replace section sizes calculated while a stacked page is
            # hidden.  Apply the defaults once the music table has a real
            # viewport, then leave subsequent user resizing alone.
            QTimer.singleShot(0, self._apply_music_column_widths)

    def _apply_music_column_widths(self):
        if self.stack.currentIndex() != 1:
            return
        header = self.music_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in enumerate(self.MUSIC_FIXED_COLUMN_WIDTHS, 1):
            header.resizeSection(column, width)
        self._music_widths_applied = True

    @staticmethod
    def _detail_tree(title: str) -> QTreeWidget:
        tree = BrowserDetailTree()
        tree.setObjectName("StockDeviceDetails")
        tree.setHeaderLabels([title, "Details"])
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.header().setDefaultAlignment(Qt.AlignCenter)
        tree.header().setFixedHeight(26)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        return tree

    def _clear_details(self):
        for tree in self.detail_trees.values():
            tree.clear()

    def _set_detail_rows(self, title: str, rows: tuple[tuple[str, str], ...]):
        tree = self.detail_trees[title]
        tree.clear()
        for label, value in rows:
            shown = value or "Not reported"
            # Do not mirror visible cell text into a hover bubble. Tooltips are
            # reserved for information the UI is not already showing.
            QTreeWidgetItem(tree, [label, shown])

    def set_sync_status(self, state: str, detail: str, changes: int, committed_at: str):
        """Refresh prepared status scalars without replacing the loaded views."""
        values={
            "Sync state":self.SYNC_LABELS.get(state,state),
            "Sync detail":detail,
            "Pending local changes":str(changes),
            "Last Sync":committed_at or "Never",
        }
        self._update_detail_values('SoundBox', values)
        if self._presented_snapshot is not None:
            self._presented_snapshot = replace(self._presented_snapshot,
                sync_state=state, sync_detail=detail,
                sync_local_changes=changes, sync_committed_at=committed_at)

    def _update_detail_values(self, title, values):
        tree=self.detail_trees[title]
        for index in range(tree.topLevelItemCount()):
            item=tree.topLevelItem(index)
            if item.text(0) in values:
                shown=values[item.text(0)] or "Not reported"
                if item.text(1)!=shown:item.setText(1,shown)

    def set_playback_snapshot(self, snapshot: StockIpodSnapshot) -> bool:
        """Learn device history while preserving all current interaction state."""
        if not self._has_snapshot or not self.music_model.set_playback(snapshot):
            return False
        self._update_detail_values('Database', {'Playback state': snapshot.playback_status})
        self._update_detail_values('Storage', _storage_sizes(snapshot.capacity_bytes, snapshot.free_bytes))
        self._set_storage_tooltips(snapshot)
        self.set_sync_status(snapshot.sync_state, snapshot.sync_detail,
                             snapshot.sync_local_changes, snapshot.sync_committed_at)
        from soundbox.stock_ipod_info import playback_refresh_compatible
        previous = self._presented_snapshot
        self._presented_snapshot = (snapshot if previous is not None
            and self._presented_composite is None
            and playback_refresh_compatible(previous, snapshot) else None)
        return True

    def _set_storage_tooltips(self, snapshot: StockIpodSnapshot):
        exact = {'Capacity': snapshot.capacity_bytes, 'Free': snapshot.free_bytes,
                 'Used': max(0, snapshot.capacity_bytes - snapshot.free_bytes)}
        tree = self.detail_trees['Storage']
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            if item.text(0) in exact:
                item.setToolTip(1, f'{exact[item.text(0)]:,} bytes')

    def set_loading(self, *, preserve_page: bool = False):
        self._presented_snapshot = None
        current_page = self.stack.currentIndex()
        if preserve_page and self._has_snapshot:
            # A refresh is not a new truth yet. Keep the complete last verified
            # Device/Songs/Playlists snapshot visible until the worker can
            # replace all three views atomically.
            self.set_page(current_page)
            return
        self._music_widths_applied = False
        self._has_snapshot = False
        self._playlist_sync_available = False
        self._sync_revision += 1
        self._sync_playlist_button()
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._playlist_order_available = False
        self._committed_playlist_order = ()
        self._pending_playlist_order = None
        self._pending_playlist_order_token = None
        self._update_playlist_drag_state()
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Reading iPod…", "Read-only database scan"])
        self.confirm_model_button.setVisible(False)
        self.set_page(current_page if preserve_page else 0)

    def set_error(self, message: str, *, preserve_snapshot: bool = False):
        self._presented_snapshot = None
        if preserve_snapshot and self._has_snapshot:
            self.restore_playlist_order()
            return
        self._has_snapshot = False
        self._playlist_sync_available = False
        self._sync_revision += 1
        self._sync_playlist_button()
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._playlist_order_available = False
        self._committed_playlist_order = ()
        self._pending_playlist_order = None
        self._pending_playlist_order_token = None
        self._update_playlist_drag_state()
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Device details unavailable", str(message)])
        self.confirm_model_button.setVisible(False)
        self.set_page(0)

    def set_snapshot(
            self,
            snapshot: StockIpodSnapshot,
            composite: CompositeDeviceData | None = None,
    ) -> bool:
        """Return True for exact complete presentation reuse, False after rebuilding.

        This is a GUI value comparison, never device freshness or write authority.
        Composite manifests are mutable, so retain a detached completed value.
        """
        from soundbox.device_readiness import stock_write_readiness

        readiness = stock_write_readiness(writable=snapshot.writable,
            filesystem=snapshot.filesystem, stock_format=snapshot.stock_format,
            firewire_guid=snapshot.firewire_guid or snapshot.usb_serial)
        if (self._has_snapshot and self._pending_playlist_order is None
                and self._presented_snapshot == snapshot
                and self._presented_composite == composite):
            # Reapply readiness before controller profile gating, which can
            # disable actions independently of an otherwise identical catalog.
            self._playlist_delete_available = readiness.ready
            self._playlist_sync_available = readiness.ready
            self._playlist_order_available = readiness.ready
            self._playlist_write_reason = readiness.reason
            self.delete_playlist_button.setToolTip(readiness.reason)
            self._update_delete_playlist_button()
            self._update_playlist_drag_state()
            self._sync_playlist_button()
            return True
        self._presented_snapshot = None
        scope = str(snapshot.target), snapshot.identity_key
        self._order_refresh_seat = (self._capture_playlist_order_seat()
            if self._pending_playlist_order is not None and self._snapshot_scope == scope else None)
        self._snapshot_scope = scope
        self._has_snapshot = True
        self._sync_revision += 1
        self._clear_details()
        model_label = snapshot.model or "Not reported"
        if snapshot.identity_conflict:
            model_label = "Conflicting device details"
        elif snapshot.model_source.startswith("Remembered: "):
            model_label += " (last observed)"
        firmware_label = snapshot.firmware_version or "Not reported"
        serial_label = snapshot.serial_number or "Not reported"
        if snapshot.firmware_source.startswith("Remembered: "):
            firmware_label += " (last observed)"
        # Keep identity/catalog provenance on the immutable snapshot for
        # validation and diagnostics. The dashboard only needs the actual
        # model, firmware, serial, and identified component values.
        dac_label = snapshot.dac_name or "Not identified"
        connection_label = snapshot.protocol or "Not reported"
        if snapshot.transport_revision:
            connection_label += f" · SCSI transport {snapshot.transport_revision}"
        storage = _storage_sizes(snapshot.capacity_bytes, snapshot.free_bytes)
        user_playlists = _regular_user_playlists(snapshot)
        regular_count = len(user_playlists)
        smart_count = sum(1 for item in snapshot.playlists if item.kind == "Smart playlist")
        composite_counts = (
            composite_library_counts(composite.manifest)
            if composite is not None else None)
        song_rows = _composite_song_rows(snapshot, composite)
        playlist_rows = _composite_playlist_rows(
            snapshot, composite, song_rows)
        capability = "Sync ready" if readiness.ready else readiness.reason
        self._set_detail_rows("Identity", (
            ("Name", snapshot.volume_name),
            ("Family", snapshot.family),
            ("Generation", snapshot.generation),
            ("Model", model_label),
            ("iPod firmware", firmware_label),
            ("DAC / codec", dac_label),
            ("Serial number", serial_label),
            ("Connection", connection_label),
            ("Library persistent ID", f"{snapshot.library_persistent_id:016X}"),
            ("Volume UUID", snapshot.volume_uuid),
        ))
        self._set_detail_rows("Storage", (
            ("Capacity", storage['Capacity']),
            ("Used", storage['Used']),
            ("Free", storage['Free']),
            ("Indexed media", human(snapshot.media_bytes)),
            ("Filesystem", snapshot.filesystem or "Unknown"),
            ("Volume access", "Writable" if snapshot.writable else "Read-only"),
            ("Device", snapshot.device_identifier),
            ("Mount", str(snapshot.target)),
        ))
        self._set_storage_tooltips(snapshot)
        database_header = self.detail_trees["Database"].headerItem()
        database_header.setText(
            0, "ITUNES/ROCKBOX" if composite_counts else "ITUNES")
        if composite_counts is None:
            database_rows = (
                ("Indexed tracks", str(len(snapshot.tracks))),
                ("User playlists", str(regular_count)),
                ("Smart playlists", str(smart_count)),
                ("Version", f"0x{snapshot.database_version:X}"),
                ("Checksum scheme", str(snapshot.checksum_scheme)),
                ("Playback state", snapshot.playback_status),
            )
        else:
            database_rows = (
                ("Managed tracks", str(composite_counts.managed_tracks)),
                ("iTunes tracks", str(composite_counts.stock_visible_tracks)),
                ("Rockbox-only tracks", str(composite_counts.rockbox_only_tracks)),
                ("Managed playlists", str(composite_counts.managed_playlists)),
                ("Rockbox database", (
                    "Auto update on · Rockbox restart pending"
                    if (
                        composite.rockbox_database_auto_update
                        and composite.rockbox_database_refresh_pending
                    ) else (
                    f"Auto update on · {composite.rockbox_database_files} cache files"
                    if composite.rockbox_database_auto_update
                    else "Auto update off · user controlled"))),
                ("iTunesDB", (
                    f"0x{snapshot.database_version:X} · checksum "
                    f"{snapshot.checksum_scheme}")),
                ("Playback state", snapshot.playback_status),
            )
        self._set_detail_rows("Database", database_rows)
        self._set_detail_rows("SoundBox", (
            ("iTunes Sync", capability),
            ("Sync state", self.SYNC_LABELS.get(snapshot.sync_state, snapshot.sync_state)),
            ("Sync detail", snapshot.sync_detail),
            ("Pending local changes", str(snapshot.sync_local_changes)),
            ("Last Sync", snapshot.sync_committed_at or "Never"),
            ("New iTunes audio", "MP3 · PCM16/44.1-kHz stereo WAV"),
            ("Commit policy", "Recovery first · media atomic · iTunesDB last"),
        ))
        self.confirm_model_button.setText(
            "Change confirmed model…" if snapshot.model else "Confirm model…")
        self.confirm_model_button.setVisible(False)

        self.music_model.set_snapshot(snapshot, composite)
        self._playlist_delete_available = readiness.ready
        self._playlist_sync_available = readiness.ready
        self._playlist_write_reason = readiness.reason
        self.delete_playlist_button.setToolTip(readiness.reason)
        stock_counts = {str(row.playlist_id): len(row.entries)
                        for row in snapshot.playlists}
        regular_ids = {str(row.playlist_id) for row in _regular_user_playlists(snapshot)}
        self.playlist_tree.clear()
        parents = []
        for playlist in playlist_rows:
            # Populate detached subtrees so each child role/flag update does
            # not rescan the attached catalog through itemChanged handlers.
            parent = QTreeWidgetItem(
                [playlist.title, str(len(playlist.entries)),
                 playlist.availability],
            )
            if playlist.sync_needed:
                parent.setToolTip(2, "Sync needed")
            parent.setData(0, Qt.UserRole, playlist.identity)
            parent.setData(0, Qt.UserRole + 1, playlist.stock_playlist_id)
            parent.setData(0, Qt.UserRole + 2, stock_counts.get(playlist.stock_playlist_id, 0))
            order_key = (playlist.identity.removeprefix('managed:')
                         if composite is not None and playlist.identity.startswith('managed:') else
                         playlist.stock_playlist_id if composite is None
                         and playlist.stock_playlist_id in regular_ids
                         and playlist.stock_playlist_id.isdecimal() and int(playlist.stock_playlist_id) > 0 else '')
            parent.setData(0, _ORDER_KEY, order_key)
            parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            if playlist.managed:
                # Keep deletion choice independent from row focus/expansion so
                # several managed playlists can be selected together.
                parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.Unchecked)
            else:
                parent.setFlags(parent.flags() & ~Qt.ItemIsUserCheckable)
            for index, entry in enumerate(playlist.entries, 1):
                child = QTreeWidgetItem(
                    parent,
                    [f"{index}. {entry.title}", "", entry.availability],
                )
                child.setFlags(child.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
                child.setData(0, Qt.UserRole + 4, (entry.dbid, entry.location))
            parents.append(parent)
        self.playlist_tree.addTopLevelItems(parents)
        self._playlist_order_available = readiness.ready
        self._pending_playlist_order = None
        self._pending_playlist_order_token = None
        self._committed_playlist_order = self._managed_playlist_order()
        self._update_delete_playlist_button()
        self._update_playlist_drag_state()
        self._sync_playlist_button()
        self._apply_playlist_order_seat()
        self._presented_composite = deepcopy(composite)
        self._presented_snapshot = snapshot
        return False

    def _playlist_action_ready(self):
        return bool(self._has_snapshot and self._playlist_sync_available
                    and self._playlist_actions_available)

    def set_playlist_actions_available(self, enabled: bool, reason: str = ""):
        """The controller gates writes without changing the verified snapshot."""
        if self._playlist_actions_available != bool(enabled):self._sync_revision += 1
        self._playlist_actions_available = bool(enabled)
        self._playlist_actions_reason = str(reason)
        self._sync_playlist_button()
        self._update_playlist_drag_state()

    def set_primary_action(self, caption: str, enabled: bool, reason: str = ''):
        """Mirror the controller's exact header action without a row target."""
        if caption != self._primary_action:
            self._primary_action = caption
            self._sync_revision += 1
        self.set_playlist_actions_available(enabled, reason)

    def _sync_playlist_button(self, *_args):
        self.sync_playlist_button.setText(self._primary_action)
        self.sync_playlist_button.setEnabled(self._playlist_action_ready())
        self.sync_playlist_button.setToolTip(
            self._playlist_actions_reason if not self._playlist_actions_available else
            self._playlist_write_reason)

    def _request_primary_action(self):
        if not self._playlist_action_ready():
            return
        self.primary_action_requested.emit()

    def _selected_playlist_item(self) -> QTreeWidgetItem | None:
        item = self.playlist_tree.currentItem()
        if item is not None and item.parent() is not None:
            item = item.parent()
        return item

    @staticmethod
    def _playlist_identity(item: QTreeWidgetItem) -> tuple[str, str] | None:
        playlist_id = str(item.data(0, Qt.UserRole) or "")
        managed = playlist_id.startswith("managed:") and bool(
            playlist_id.removeprefix("managed:"))
        stock = playlist_id.isdecimal() and int(playlist_id) > 0
        if not managed and not stock:
            return None
        return playlist_id, item.text(0)

    def _checked_playlist_items(self) -> list[QTreeWidgetItem]:
        return [
            self.playlist_tree.topLevelItem(index)
            for index in range(self.playlist_tree.topLevelItemCount())
            if self.playlist_tree.topLevelItem(index).checkState(0) == Qt.Checked
            and self._playlist_identity(
                self.playlist_tree.topLevelItem(index)) is not None
        ]

    def _update_delete_playlist_button(self, *_args):
        checked = self._checked_playlist_items()
        count = len(checked)
        self.delete_playlist_button.setText(
            "Delete checked playlist…"
            if count == 1 else (
                f"Delete {count} checked playlists…"
                if count > 1 else "Delete checked playlists…"))
        self.delete_playlist_button.setEnabled(
            self._playlist_delete_available
            and count > 0)

    def set_playlist_deletion_available(self, enabled: bool):
        """Keep the dual-boot dashboard readable without bypassing ownership."""
        self._playlist_delete_available = bool(
            self._playlist_delete_available and enabled)
        self._update_delete_playlist_button()

    def _request_playlist_delete(self):
        self._emit_playlist_delete(self._checked_playlist_items())

    def _managed_playlist_order(self) -> tuple[str, ...]:
        return tuple(str(self.playlist_tree.topLevelItem(index).data(0, _ORDER_KEY))
            for index in range(self.playlist_tree.topLevelItemCount())
            if self.playlist_tree.topLevelItem(index).data(0, _ORDER_KEY))

    def _playlist_order_ready(self):
        order = self._managed_playlist_order()
        return bool(self._playlist_order_available and self._playlist_action_ready()
                    and self._pending_playlist_order is None and len(order) > 1
                    and len(set(order)) == len(order))

    def _update_playlist_drag_state(self):
        ready = self._playlist_order_ready()
        for index in range(self.playlist_tree.topLevelItemCount()):
            item = self.playlist_tree.topLevelItem(index)
            flags = item.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled
            if ready and item.data(0, _ORDER_KEY):flags |= Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
            if flags != item.flags():item.setFlags(flags)

    def _set_playlist_order(self, order):
        tree = self.playlist_tree
        items = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
        rows = {item.data(0, _ORDER_KEY): item for item in items if item.data(0, _ORDER_KEY)}
        if len(order) != len(rows) or set(order) != set(rows):return False
        seat = tree._seat()
        ordered = iter(rows[key] for key in order)
        updated = [next(ordered) if item.data(0, _ORDER_KEY) else item for item in items]
        blocked = tree.blockSignals(True)
        try:
            for _item in items:tree.takeTopLevelItem(0)
            tree.addTopLevelItems(updated)
            tree._restore_seat(seat)
        finally:
            tree.blockSignals(blocked)
        return True

    def _drop_playlist_order(self, order):
        if not self._playlist_order_ready() or order == self._committed_playlist_order:return
        if not self._set_playlist_order(order):return
        if self.playlist_tree._drag_active and self.playlist_tree._drag_seat is not None:
            self.playlist_tree._restore_seat(self.playlist_tree._drag_seat)
        self._sync_revision += 1
        self._pending_playlist_order = order
        self._pending_playlist_order_token = object()
        self._update_playlist_drag_state()
        self.playlist_order_requested.emit(order)

    @property
    def playlist_order_token(self):
        return self._pending_playlist_order_token

    def restore_playlist_order(self, token=None):
        """Reject an uncommitted gesture; a fresh snapshot settles successful writes."""
        if token is not None and token is not self._pending_playlist_order_token:return
        if self._pending_playlist_order is None:return
        self._set_playlist_order(self._committed_playlist_order)
        self._sync_revision += 1
        self._pending_playlist_order = None
        self._pending_playlist_order_token = None
        self._update_playlist_drag_state()

    @staticmethod
    def _playlist_seat_key(item):
        parent = item.parent() if item.parent() is not None else item
        identity = (str(parent.data(0, Qt.UserRole) or ''), str(parent.data(0, Qt.UserRole + 1) or ''))
        return identity, (parent.indexOfChild(item), item.data(0, Qt.UserRole + 4)) if item is not parent else None

    def _capture_playlist_order_seat(self):
        tree = self.playlist_tree
        return dict(rows={self._playlist_seat_key(tree.topLevelItem(i))[0]:
                          (tree.topLevelItem(i).isExpanded(), tree.topLevelItem(i).checkState(0))
                          for i in range(tree.topLevelItemCount())},
                    current=self._playlist_seat_key(tree.currentItem()) if tree.currentItem() is not None else None,
                    column=tree.currentColumn(), selected=[self._playlist_seat_key(item) for item in tree.selectedItems()],
                    scroll=tree.verticalScrollBar().value())

    def _apply_playlist_order_seat(self):
        seat = self._order_refresh_seat
        if seat is None:return
        tree = self.playlist_tree
        rows = {}
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            rows.setdefault(self._playlist_seat_key(item)[0], []).append(item)
        def find(key):
            if key is None:return None
            identity, child = key
            matches = rows.get(identity, ())
            if len(matches) != 1:return None
            parent = matches[0]
            if child is None:return parent
            index, reference = child
            candidate = parent.child(index)
            return candidate if candidate is not None and candidate.data(0, Qt.UserRole + 4) == reference else None
        for identity, (expanded, checked) in seat['rows'].items():
            if len(rows.get(identity, ())) != 1:continue
            parent = rows[identity][0];parent.setExpanded(expanded)
            if parent.flags() & Qt.ItemIsUserCheckable:parent.setCheckState(0, checked)
        current = find(seat['current'])
        if current is not None:tree.setCurrentItem(current, seat['column'], QItemSelectionModel.NoUpdate)
        tree.clearSelection()
        for reference in seat['selected']:
            item = find(reference)
            if item is not None:item.setSelected(True)
        tree.verticalScrollBar().setValue(seat['scroll'])

    def restore_playlist_order_seat(self):
        """Reapply exact identities after the controller restores older positional preferences."""
        self._apply_playlist_order_seat()
        self._order_refresh_seat = None

    def _emit_playlist_delete(self, items: list[QTreeWidgetItem]):
        identities = tuple(
            identity for item in items
            if (identity := self._playlist_identity(item)) is not None
        )
        if not self._playlist_delete_available or not identities:
            return
        self.delete_playlists_requested.emit(identities)

    def _playlist_context_menu(self, position):
        item = self.playlist_tree.itemAt(position)
        if item is None:
            return
        if item.parent() is not None:
            item = item.parent()
        self.playlist_tree.setCurrentItem(item)
        menu = QMenu(self.playlist_tree)
        action = menu.addAction(self._primary_action)
        revision = self._sync_revision
        action.setEnabled(self._playlist_action_ready())
        def requested(_checked=False):
            if revision != self._sync_revision or not self._playlist_action_ready():
                return
            self._request_primary_action()
        action.triggered.connect(requested)
        action = menu.addAction("Delete this playlist from iPod…")
        action.setEnabled(
            self._playlist_delete_available
            and self._playlist_identity(item) is not None)
        action.triggered.connect(
            lambda _checked=False, selected=item:
            self._emit_playlist_delete([selected]))
        exec_transient_menu(menu, self.playlist_tree.viewport().mapToGlobal(position))
