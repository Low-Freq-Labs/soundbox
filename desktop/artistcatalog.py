"""Artist Pictures catalog: every artist whose tracks are wearing an
auto-selected AVATAR (the fallback when no real cover was found), with the
actual picture shown — so the auto-picks are never a black box. Fixing an
artist applies to ALL of that artist's avatar tracks at once; real covers are
never touched.

The dialog only CHOOSES (like the cover-art picker); the caller fetches/stores.
`choice` after exec():  {"kind": "url"|"file"|"refetch", "artist": str,
                          "tids": [track_id, ...], ...}   |   None
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

_ROLE = Qt.UserRole + 31  # (artist, [tids])


class ArtistCatalogDialog(QDialog):
    def __init__(self, groups, parent=None):
        """`groups` = [(artist, [track_ids], image_path_or_None), ...]"""
        super().__init__(parent)
        self.setWindowTitle("Artist pictures")
        self.resize(640, 560)
        self.choice: dict | None = None

        v = QVBoxLayout(self)
        head = QLabel(f"{len(groups)} artist(s) are using an auto-selected "
                      "profile picture as cover art. Pick one to change it — "
                      "the fix applies to all of that artist's avatar tracks. "
                      "Real track covers are never touched.")
        head.setWordWrap(True)
        v.addWidget(head)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Artist", "Tracks using it"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setIconSize(QSize(44, 44))
        self.list.header().resizeSection(0, 420)
        for artist, tids, img in groups:
            it = QTreeWidgetItem([artist, str(len(tids))])
            it.setData(0, _ROLE, (artist, tids))
            if img is not None:
                pm = QPixmap(str(img))
                if not pm.isNull():
                    it.setIcon(0, QIcon(pm))
            self.list.addTopLevelItem(it)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        v.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.url_btn = QPushButton("Set from URL…")
        self.url_btn.clicked.connect(self._use_url)
        row.addWidget(self.url_btn)
        self.file_btn = QPushButton("Choose image file…")
        self.file_btn.clicked.connect(self._use_file)
        row.addWidget(self.file_btn)
        self.refetch_btn = QPushButton("Re-fetch avatar")
        self.refetch_btn.clicked.connect(self._refetch)
        row.addWidget(self.refetch_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        v.addLayout(row)
        self._sync_buttons()

    def _current(self):
        it = self.list.currentItem()
        return it.data(0, _ROLE) if it else None

    def _sync_buttons(self):
        on = self._current() is not None
        for b in (self.url_btn, self.file_btn, self.refetch_btn):
            b.setEnabled(on)

    def _use_url(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        url, ok = QInputDialog.getText(
            self, "Artist picture",
            f"SoundCloud URL for {artist}\n(artist profile = avatar, "
            "track = its cover):")
        if ok and url.strip():
            self.choice = {"kind": "url", "artist": artist, "tids": tids,
                           "url": url.strip()}
            self.accept()

    def _use_file(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose picture for {artist}", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if path:
            self.choice = {"kind": "file", "artist": artist, "tids": tids,
                           "path": path}
            self.accept()

    def _refetch(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        self.choice = {"kind": "refetch", "artist": artist, "tids": tids}
        self.accept()
