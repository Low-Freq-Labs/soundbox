"""Managed default-cover catalog.

These pictures are the final visual fallback for tracks with naturally missing
art or an explicit per-track block.  They never replace a successful track
cover or artist avatar.  The dialog edits only SoundBox-owned copies under the
art cache; source images selected from elsewhere on the Mac are never retained
as dependencies or modified.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

_ROLE = Qt.UserRole + 37


class DefaultPhotosDialog(QDialog):
    def __init__(self, rows, on_action=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Default photos")
        self.resize(680, 620)
        self._on_action = on_action

        layout = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        layout.addWidget(self.head)

        self.status = QLabel("")
        layout.addWidget(self.status)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Photo", "Tracks using it"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setIconSize(QSize(96, 96))
        self.list.header().resizeSection(0, 500)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add photos…")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add)
        buttons.addWidget(self.add_btn)
        buttons.addStretch(1)
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self._delete)
        buttons.addWidget(self.delete_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self._populate(rows)

    def _populate(self, rows) -> None:
        self.list.clear()
        for row in rows:
            item = QTreeWidgetItem([row["name"], str(row["tracks"])])
            item.setData(0, _ROLE, row["id"])
            item.setSizeHint(0, QSize(0, 104))
            pixmap = QPixmap(str(row["path"]))
            if not pixmap.isNull():
                item.setIcon(0, QIcon(pixmap))
            self.list.addTopLevelItem(item)
        count = len(rows)
        self.head.setText(
            f"{count} managed default photo{'s' if count != 1 else ''}. "
            "SoundBox distributes them randomly and evenly across tracks with "
            "missing or deliberately removed art. Real covers and artist "
            "pictures always win. Imported originals can be moved afterward.")
        self._sync_buttons()

    def _sync_buttons(self, busy: bool = False) -> None:
        self.add_btn.setEnabled(not busy)
        self.delete_btn.setEnabled(bool(self.list.selectedItems()) and not busy)
        self.close_btn.setEnabled(not busy)

    def _dispatch(self, choice: dict) -> None:
        if self._on_action is None:
            return
        self._sync_buttons(busy=True)
        self.status.setText("Updating managed photos…")

        def done(ok: bool, rows=None, message: str = "") -> None:
            self._sync_buttons(busy=False)
            if not ok:
                self.status.setText(message or "Couldn't update default photos.")
                return
            self._populate(rows or [])
            self.status.setText(message)

        self._on_action(choice, done)

    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add default photos", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if paths:
            self._dispatch({"kind": "add", "paths": paths})

    def _delete(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        count = len(items)
        if QMessageBox.question(
                self, "Delete default photos",
                f"Delete {count} managed default photo"
                f"{'s' if count != 1 else ''}?\n\n"
                "Tracks using them will be rebalanced across the remaining "
                "pool. Original source files and every real track cover or "
                "artist picture are untouched.") != QMessageBox.Yes:
            return
        self._dispatch({
            "kind": "delete",
            "ids": [item.data(0, _ROLE) for item in items],
        })
