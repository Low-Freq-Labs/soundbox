"""Read-only Device / Songs / Playlists pages for stock and dual iPods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from soundbox.manifest import composite_library_counts

from .devices import human


def _duration(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours else f"{minutes}:{seconds:02d}")


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
    rockbox_settings_ready: bool = False
    rockbox_database_files: int = 0
    rockbox_database_refresh_pending: bool = False


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


def _stock_device_path(location: str) -> str:
    text = str(location or "").replace("\\", "/")
    if text.startswith(":"):
        return "/" + text.lstrip(":").replace(":", "/")
    return "/" + text.lstrip("/")


def _stock_song(track, availability: str = "Stock") -> DeviceSongRow:
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
            output.append(_stock_song(stock_track, "Both"))
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
            ("System playlist" if (
                playlist.kind == "Smart playlist"
                and playlist.title.casefold() == "videos") else "Stock"),
            True,
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
        entries = tuple(
            by_path[path] for path in composite.playlist_entries.get(key, ())
            if path in by_path
        )
        output.append(DevicePlaylistRow(
            f"managed:{key}",
            title,
            entries,
            "Both" if stock_playlist is not None else "Rockbox",
            True,
        ))
    stock_songs = {item.dbid: item for item in songs if item.dbid}
    output.extend(DevicePlaylistRow(
        "",
        playlist.title,
        tuple(
            stock_songs[entry.dbid] for entry in playlist.entries
            if entry.dbid in stock_songs),
        "Stock",
        False,
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
        self.playlist_tree.currentItemChanged.connect(
            self._update_playlist_order_buttons)
        playlist_layout.addWidget(self.playlist_tree)
        playlist_actions = QHBoxLayout()
        playlist_actions.setContentsMargins(12, 0, 12, 10)
        self.delete_playlist_button = QPushButton("Delete checked playlists…")
        self.delete_playlist_button.setObjectName("Ghost")
        self.delete_playlist_button.setEnabled(False)
        self.delete_playlist_button.clicked.connect(self._request_playlist_delete)
        playlist_actions.addWidget(self.delete_playlist_button)
        self.move_playlist_up_button = QPushButton("Move up")
        self.move_playlist_up_button.setObjectName("Ghost")
        self.move_playlist_up_button.clicked.connect(
            lambda: self._move_selected_playlist(-1))
        playlist_actions.addWidget(self.move_playlist_up_button)
        self.move_playlist_down_button = QPushButton("Move down")
        self.move_playlist_down_button.setObjectName("Ghost")
        self.move_playlist_down_button.clicked.connect(
            lambda: self._move_selected_playlist(1))
        playlist_actions.addWidget(self.move_playlist_down_button)
        self.apply_playlist_order_button = QPushButton("Apply order…")
        self.apply_playlist_order_button.setObjectName("Ghost")
        self.apply_playlist_order_button.clicked.connect(
            self._request_playlist_order)
        playlist_actions.addWidget(self.apply_playlist_order_button)
        playlist_actions.addStretch(1)
        playlist_layout.addLayout(playlist_actions)
        self.stack.addWidget(playlist_page)
        self._playlist_delete_available = False
        self._playlist_order_available = False
        self._committed_playlist_order: tuple[str, ...] = ()
        self._has_snapshot = False

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
        if preserve_page and self._has_snapshot:
            # A refresh is not a new truth yet. Keep the complete last verified
            # Device/Songs/Playlists snapshot visible until the worker can
            # replace all three views atomically.
            self.set_page(current_page)
            return
        self._music_widths_applied = False
        self._has_snapshot = False
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._playlist_order_available = False
        self._committed_playlist_order = ()
        self._update_playlist_order_buttons()
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Reading iPod…", "Read-only database scan"])
        self.confirm_model_button.setVisible(False)
        self.set_page(current_page if preserve_page else 0)

    def set_error(self, message: str, *, preserve_snapshot: bool = False):
        if preserve_snapshot and self._has_snapshot:
            return
        self._has_snapshot = False
        self.music_model.set_snapshot(None)
        self.playlist_tree.clear()
        self._playlist_delete_available = False
        self.delete_playlist_button.setEnabled(False)
        self._playlist_order_available = False
        self._committed_playlist_order = ()
        self._update_playlist_order_buttons()
        self._clear_details()
        QTreeWidgetItem(self.general_tree, ["Device details unavailable", str(message)])
        self.confirm_model_button.setVisible(False)
        self.set_page(0)

    def set_snapshot(
            self,
            snapshot: StockIpodSnapshot,
            composite: CompositeDeviceData | None = None,
    ):
        self._has_snapshot = True
        self._clear_details()
        model_label = snapshot.model or "Not confirmed"
        firmware_label = snapshot.firmware_version or "Not reported"
        serial_label = snapshot.serial_number or "Not reported"
        # Keep identity/catalog provenance on the immutable snapshot for
        # validation and diagnostics. The dashboard only needs the actual
        # model, firmware, serial, and identified component values.
        dac_label = snapshot.dac_name or "Not identified"
        connection_label = snapshot.protocol or "Not reported"
        if snapshot.transport_revision:
            connection_label += f" · SCSI transport {snapshot.transport_revision}"
        used = max(0, snapshot.capacity_bytes - snapshot.free_bytes)
        user_playlists = _regular_user_playlists(snapshot)
        regular_count = len(user_playlists)
        smart_count = sum(1 for item in snapshot.playlists if item.kind == "Smart playlist")
        composite_counts = (
            composite_library_counts(composite.manifest)
            if composite is not None else None)
        song_rows = _composite_song_rows(snapshot, composite)
        playlist_rows = _composite_playlist_rows(
            snapshot, composite, song_rows)
        capability = (
            "Validated guarded MP3/WAV sync"
            if supports_stock_write(snapshot.model)
            else "Read-only until a validated model is confirmed"
        )
        sync_labels = {
            "up_to_date": "Up to date",
            "local_changes": "Local changes ready",
            "reconcile_required": "Reconciliation required",
            "untracked": "Not yet tracked on this Mac",
            "composite_untracked": "Not synced yet",
        }
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
        database_header = self.detail_trees["Database"].headerItem()
        database_header.setText(
            0, "DUAL LIBRARY" if composite_counts else "DATABASE")
        if composite_counts is None:
            database_rows = (
                ("Indexed tracks", str(len(snapshot.tracks))),
                ("User playlists", str(regular_count)),
                ("Smart playlists", str(smart_count)),
                ("Version", f"0x{snapshot.database_version:X}"),
                ("Checksum scheme", str(snapshot.checksum_scheme)),
                ("Playback state", snapshot.playback_status),
                ("Recovery points", str(snapshot.recovery_count)),
                ("SHA-256", snapshot.database_sha256),
            )
        else:
            database_rows = (
                ("Managed tracks", str(composite_counts.managed_tracks)),
                ("Stock-visible tracks", str(composite_counts.stock_visible_tracks)),
                ("Rockbox-only tracks", str(composite_counts.rockbox_only_tracks)),
                ("Managed playlists", str(composite_counts.managed_playlists)),
                ("Rockbox database", (
                    "Auto update on · Rockbox restart pending"
                    if (
                        composite.rockbox_settings_ready
                        and composite.rockbox_database_refresh_pending
                    ) else (
                    f"Auto update on · {composite.rockbox_database_files} cache files"
                    if composite.rockbox_settings_ready
                    else "Auto update needs enabling"))),
                ("iTunesDB", (
                    f"0x{snapshot.database_version:X} · checksum "
                    f"{snapshot.checksum_scheme}")),
                ("Playback state", snapshot.playback_status),
                ("Recovery points", str(snapshot.recovery_count)),
                ("iTunesDB SHA-256", snapshot.database_sha256),
            )
        self._set_detail_rows("Database", database_rows)
        self._set_detail_rows("SoundBox", (
            ("Stock sync", capability),
            ("Sync state", sync_labels.get(snapshot.sync_state, snapshot.sync_state)),
            ("Sync detail", snapshot.sync_detail),
            ("Pending local changes", str(snapshot.sync_local_changes)),
            ("Last committed", snapshot.sync_committed_at or "Never"),
            ("Supported audio", "MP3 · PCM16/44.1-kHz stereo WAV"),
            ("Commit policy", "Recovery first · media atomic · iTunesDB last"),
        ))
        self.confirm_model_button.setText(
            "Change confirmed model…" if snapshot.model else "Confirm model…")
        self.confirm_model_button.setVisible(True)

        self.music_model.set_snapshot(snapshot, composite)
        self._playlist_delete_available = bool(
            snapshot.writable and supports_stock_write(snapshot.model))
        self.playlist_tree.clear()
        for playlist in playlist_rows:
            parent = QTreeWidgetItem(
                self.playlist_tree,
                [playlist.title, str(len(playlist.entries)),
                 playlist.availability],
            )
            parent.setData(0, Qt.UserRole, playlist.identity)
            if playlist.managed:
                # Keep deletion choice independent from row focus/expansion so
                # several managed playlists can be selected together.
                parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.Unchecked)
            else:
                parent.setFlags(parent.flags() & ~Qt.ItemIsUserCheckable)
            for index, entry in enumerate(playlist.entries, 1):
                QTreeWidgetItem(
                    parent,
                    [f"{index}. {entry.title}", "", entry.availability],
                )
        self._playlist_order_available = bool(
            composite is not None
            and snapshot.writable
            and supports_stock_write(snapshot.model)
        )
        self._committed_playlist_order = self._managed_playlist_order()
        self._update_delete_playlist_button()
        self._update_playlist_order_buttons()

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
        output: list[str] = []
        for index in range(self.playlist_tree.topLevelItemCount()):
            identity = str(
                self.playlist_tree.topLevelItem(index).data(0, Qt.UserRole) or "")
            if identity.startswith("managed:"):
                output.append(identity.removeprefix("managed:"))
        return tuple(output)

    def _update_playlist_order_buttons(self, *_args):
        item = self._selected_playlist_item()
        identity = (
            str(item.data(0, Qt.UserRole) or "") if item is not None else "")
        managed = self._playlist_order_available and identity.startswith("managed:")
        managed_items = [
            self.playlist_tree.topLevelItem(index)
            for index in range(self.playlist_tree.topLevelItemCount())
            if str(self.playlist_tree.topLevelItem(index).data(
                0, Qt.UserRole) or "").startswith("managed:")
        ]
        position = managed_items.index(item) if managed and item in managed_items else -1
        self.move_playlist_up_button.setEnabled(managed and position > 0)
        self.move_playlist_down_button.setEnabled(
            managed and 0 <= position < len(managed_items) - 1)
        self.apply_playlist_order_button.setEnabled(
            self._playlist_order_available
            and self._managed_playlist_order() != self._committed_playlist_order)

    def _move_selected_playlist(self, direction: int):
        item = self._selected_playlist_item()
        if item is None or not self._playlist_order_available:
            return
        managed_items = [
            self.playlist_tree.topLevelItem(index)
            for index in range(self.playlist_tree.topLevelItemCount())
            if str(self.playlist_tree.topLevelItem(index).data(
                0, Qt.UserRole) or "").startswith("managed:")
        ]
        if item not in managed_items:
            return
        position = managed_items.index(item)
        destination = position + (-1 if direction < 0 else 1)
        if not 0 <= destination < len(managed_items):
            return
        absolute = self.playlist_tree.indexOfTopLevelItem(item)
        target_absolute = self.playlist_tree.indexOfTopLevelItem(
            managed_items[destination])
        moved = self.playlist_tree.takeTopLevelItem(absolute)
        if absolute < target_absolute:
            target_absolute -= 1
        insert_at = target_absolute if direction < 0 else target_absolute + 1
        self.playlist_tree.insertTopLevelItem(insert_at, moved)
        self.playlist_tree.setCurrentItem(moved)
        self._update_playlist_order_buttons()

    def _request_playlist_order(self):
        order = self._managed_playlist_order()
        if (
            not self._playlist_order_available
            or not order
            or order == self._committed_playlist_order
        ):
            return
        self.playlist_order_requested.emit(order)

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
