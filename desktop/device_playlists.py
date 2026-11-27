"""Shared playlist-page construction; each device retains its own write rules."""
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QHBoxLayout, QPushButton, QTreeWidget,
    QVBoxLayout, QWidget,
)


@dataclass(frozen=True)
class DevicePlaylistPage:
    page: QWidget
    tree: QTreeWidget
    primary: QPushButton
    delete: QPushButton


def create_playlist_page(tree: QTreeWidget, *, selection_mode=QAbstractItemView.SingleSelection):
    """Keep the stock and USB frame, columns and action placement identical."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    tree.setObjectName('StockDevicePlaylists')
    tree.setHeaderLabels(['Playlist / Track', 'Tracks', 'Type'])
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    tree.setSelectionMode(selection_mode)
    tree.setContextMenuPolicy(Qt.CustomContextMenu)
    tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
    header = tree.header()
    header.setDefaultAlignment(Qt.AlignCenter)
    header.setStretchLastSection(False)
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setSectionResizeMode(0, QHeaderView.Stretch)
    header.setFixedHeight(26)
    tree.setColumnWidth(1, 70)
    tree.setColumnWidth(2, 150)
    layout.addWidget(tree)
    actions = QHBoxLayout()
    actions.setContentsMargins(12, 0, 12, 10)
    primary = QPushButton('Export')
    delete = QPushButton('Delete checked playlists…')
    for button in (primary, delete):
        button.setObjectName('Ghost')
        button.setEnabled(False)
        actions.addWidget(button)
    actions.addStretch(1)
    layout.addLayout(actions)
    return DevicePlaylistPage(page, tree, primary, delete)
