"""Read-only Device / Music / Playlists pages for classic stock iPods."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from soundbox.stock_ipod import supports_stock_write
from soundbox.stock_ipod_info import StockIpodSnapshot

from .devices import human


def _duration(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


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


class StockMusicModel(QAbstractTableModel):
    """Small immutable adapter over one parsed device snapshot."""

    COLUMNS = (
        ("Title", lambda item: item.title),
        ("Artist", lambda item: item.artist or "—"),
        ("Format", lambda item: item.filetype or "—"),
        ("Size", lambda item: human(item.size)),
        ("Time", lambda item: _duration(item.length_ms)),
        ("Plays", lambda item: str(item.play_count)),
        ("Skips", lambda item: str(item.skip_count)),
        ("Pending", lambda item: (
            f"+{item.pending_plays} play / +{item.pending_skips} skip"
            if item.pending_plays or item.pending_skips else "—")),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks = ()

    def set_snapshot(self, snapshot: StockIpodSnapshot | None):
        self.beginResetModel()
        self.tracks = snapshot.tracks if snapshot is not None else ()
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()):
        return len(self.tracks)

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
    # Title stretches with the window; the remaining operational columns keep
    # predictable widths. Album and the internal device path intentionally stay
    # out of this view—the tooltip still exposes the path for diagnostics.
    MUSIC_FIXED_COLUMN_WIDTHS = (150, 75, 80, 60, 55, 55, 130)

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
        music_header.setStretchLastSection(False)
        music_header.setSectionResizeMode(QHeaderView.Interactive)
        music_header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in enumerate(self.MUSIC_FIXED_COLUMN_WIDTHS, 1):
            self.music_table.setColumnWidth(column, width)
        self._music_widths_applied = False
        self.stack.addWidget(self.music_table)

        playlist_page = QWidget()
        playlist_layout = QVBoxLayout(playlist_page)
        playlist_layout.setContentsMargins(0, 0, 0, 0)
        playlist_layout.setSpacing(8)
        self.playlist_tree = QTreeWidget()
        self.playlist_tree.setObjectName("StockDevicePlaylists")
        self.playlist_tree.setHeaderLabels(["Playlist / Track", "Tracks", "Type"])
        self.playlist_tree.setAlternatingRowColors(True)
        self.playlist_tree.setUniformRowHeights(True)
        self.playlist_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.playlist_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_tree.customContextMenuRequested.connect(
            self._playlist_context_menu)
        playlist_header = self.playlist_tree.header()
        playlist_header.setStretchLastSection(False)
        playlist_header.setSectionResizeMode(QHeaderView.Interactive)
        playlist_header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.playlist_tree.setColumnWidth(1, 70)
        self.playlist_tree.setColumnWidth(2, 110)
        self.playlist_tree.itemChanged.connect(
            self._update_delete_playlist_button)
        playlist_layout.addWidget(self.playlist_tree)
        playlist_actions = QHBoxLayout()
        playlist_actions.setContentsMargins(12, 0, 12, 10)
        self.delete_playlist_button = QPushButton("Delete checked playlists…")
        self.delete_playlist_button.setObjectName("Ghost")
        self.delete_playlist_button.setEnabled(False)
        self.delete_playlist_button.clicked.connect(self._request_playlist_delete)
        playlist_actions.addWidget(self.delete_playlist_button)
        playlist_actions.addStretch(1)
        playlist_layout.addLayout(playlist_actions)
        self.stack.addWidget(playlist_page)
        self._playlist_delete_available = False

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
        tree = QTreeWidget()
        tree.setObjectName("StockDeviceDetails")
        tree.setHeaderLabels([title, "Details"])
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
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

    def set_loading(self, *, preserve_page: bool = False):
        current_page = self.stack.currentIndex()
        self._music_widths_applied = False
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Reading stock iPod…", "Read-only database scan"])
        self.confirm_model_button.setVisible(False)
        self.set_page(current_page if preserve_page else 0)

    def set_error(self, message: str):
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Device details unavailable", str(message)])
        self.confirm_model_button.setVisible(False)
        self.set_page(0)

    def set_snapshot(self, snapshot: StockIpodSnapshot):
        self._clear_details()
        model_label = snapshot.model or "Not confirmed"
        if snapshot.model_source:
            model_label += f" · {snapshot.model_source}"
        firmware_label = snapshot.firmware_version or "Not reported"
        if snapshot.firmware_source:
            firmware_label += f" · {snapshot.firmware_source}"
        serial_label = snapshot.serial_number or "Not reported"
        if snapshot.serial_source:
            serial_label += f" · {snapshot.serial_source}"
        # Keep catalog provenance on the immutable snapshot for validation and
        # diagnostics, but the dashboard only needs the identified component.
        dac_label = snapshot.dac_name or "Not identified"
        connection_label = snapshot.protocol or "Not reported"
        if snapshot.transport_revision:
            connection_label += f" · SCSI transport {snapshot.transport_revision}"
        used = max(0, snapshot.capacity_bytes - snapshot.free_bytes)
        user_playlists = _regular_user_playlists(snapshot)
        managed_playlists = _managed_playlists(snapshot)
        regular_count = len(user_playlists)
        smart_count = sum(1 for item in snapshot.playlists if item.kind == "Smart playlist")
        capability = (
            "Validated guarded MP3/WAV sync"
            if supports_stock_write(snapshot.model)
            else "Read-only until a validated model is confirmed"
        )
        self._set_detail_rows("Identity", (
            ("Name", snapshot.volume_name),
            ("Family", snapshot.family),
            ("Generation", snapshot.generation),
            ("Model", model_label),
            ("Firmware", firmware_label),
            ("DAC / codec", dac_label),
            ("Serial number", serial_label),
            ("Connection", connection_label),
            ("Library persistent ID", f"{snapshot.library_persistent_id:016X}"),
            ("Volume UUID", snapshot.volume_uuid),
        ))
        self._set_detail_rows("Storage", (
            ("Capacity", human(snapshot.capacity_bytes)),
            ("Used", human(used)),
            ("Free", human(snapshot.free_bytes)),
            ("Indexed media", human(snapshot.media_bytes)),
            ("Filesystem", snapshot.filesystem or "Unknown"),
            ("Volume access", "Writable" if snapshot.writable else "Read-only"),
            ("Device", snapshot.device_identifier),
            ("Mount", str(snapshot.target)),
        ))
        self._set_detail_rows("Database", (
            ("Indexed tracks", str(len(snapshot.tracks))),
            ("User playlists", str(regular_count)),
            ("Smart playlists", str(smart_count)),
            ("Version", f"0x{snapshot.database_version:X}"),
            ("Checksum scheme", str(snapshot.checksum_scheme)),
            ("Playback state", snapshot.playback_status),
            ("Recovery points", str(snapshot.recovery_count)),
            ("SHA-256", snapshot.database_sha256),
        ))
        self._set_detail_rows("SoundBox", (
            ("Stock sync", capability),
            ("Supported audio", "MP3 · PCM16/44.1-kHz stereo WAV"),
            ("Commit policy", "Recovery first · media atomic · iTunesDB last"),
        ))
        self.confirm_model_button.setText(
            "Change confirmed model…" if snapshot.model else "Confirm model…")
        self.confirm_model_button.setVisible(True)

        self.music_model.set_snapshot(snapshot)
        self._playlist_delete_available = bool(
            snapshot.writable and supports_stock_write(snapshot.model))
        self.playlist_tree.clear()
        for playlist in managed_playlists:
            # Store as text: some real Apple IDs are larger than both Qt's
            # signed int signal and QVariant's signed 64-bit integer range.
            system_playlist = (
                playlist.kind == "Smart playlist"
                and playlist.title.casefold() == "videos")
            count = "Dynamic" if playlist.dynamic else str(len(playlist.entries))
            parent = QTreeWidgetItem(
                self.playlist_tree,
                [playlist.title, count,
                 "System playlist" if system_playlist else playlist.kind],
            )
            # The first column is the roomy playlist/track title column. Keep
            # deletion choice independent from row focus/expansion so several
            # playlists can be selected without losing their checks.
            parent.setData(0, Qt.UserRole, str(playlist.playlist_id))
            parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.Unchecked)
            for index, entry in enumerate(playlist.entries, 1):
                QTreeWidgetItem(
                    parent,
                    [f"{index}. {entry.title}", "", ""],
                )
        self._update_delete_playlist_button()

    def _selected_playlist_item(self) -> QTreeWidgetItem | None:
        item = self.playlist_tree.currentItem()
        if item is not None and item.parent() is not None:
            item = item.parent()
        return item

    @staticmethod
    def _playlist_identity(item: QTreeWidgetItem) -> tuple[str, str] | None:
        playlist_id = str(item.data(0, Qt.UserRole) or "")
        if not playlist_id.isdecimal() or int(playlist_id) <= 0:
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

    def _request_playlist_delete(self):
        self._emit_playlist_delete(self._checked_playlist_items())

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
        action = menu.addAction("Delete this playlist from iPod…")
        action.setEnabled(
            self._playlist_delete_available
            and self._playlist_identity(item) is not None)
        action.triggered.connect(
            lambda _checked=False, selected=item:
            self._emit_playlist_delete([selected]))
        menu.exec(self.playlist_tree.viewport().mapToGlobal(position))
