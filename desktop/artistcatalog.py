"""Artist Pictures catalog: every artist whose tracks are wearing an
auto-selected AVATAR (the fallback when no real cover was found), with the
actual picture shown — so the auto-picks are never a black box. Fixing an
artist applies to ALL of that artist's avatar tracks at once; real covers are
never touched.

The dialog STAYS OPEN across changes (close it yourself when done). Each action
is handed to the app via `on_action(choice, done)`; the app applies it (fetches
off-thread where needed) and calls `done(ok, new_image_path)` back on the main
thread so the row updates in place.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)
from .thumbnails import CatalogThumbnails

_ROLE = Qt.UserRole + 31  # (artist, [tids])


class ArtistCatalogDialog(QDialog):
    def __init__(self, groups, on_action=None, parent=None, *, allow_url=False,
                 rename_bindings=("Ctrl+R",)):
        """`groups` = [(artist, [track_ids], image_path_or_None), ...]
        `on_action(choice_dict, done_cb)` applies a change; done_cb(ok, img)."""
        super().__init__(parent)
        self.setWindowTitle("Artist pictures")
        self.resize(680, 620)
        self._allow_url = allow_url
        self._on_action = on_action
        self._closed = False
        self._busy = False

        v = QVBoxLayout(self)
        head = QLabel(f"{len(groups)} artist(s) are using an auto-selected "
                      "profile picture as cover art. Fix as many as you like — "
                      "each change applies to all of that artist's avatar "
                      "tracks. Real track covers are never touched.")
        head.setWordWrap(True)
        v.addWidget(head)

        self.status = QLabel("")
        v.addWidget(self.status)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Artist", "Tracks using it"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setIconSize(QSize(96, 96))  # big enough to actually judge
        self._thumbnails = CatalogThumbnails(self.list)
        self.list.header().resizeSection(0, 440)
        for artist, tids, img in groups:
            it = QTreeWidgetItem([artist, str(len(tids))])
            it.setData(0, _ROLE, (artist, tids))
            it.setSizeHint(0, QSize(0, 104))  # tall rows for the 96px art
            self.list.addTopLevelItem(it)
            if img is not None:
                self._thumbnails.set_image(it, img)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        v.addWidget(self.list, 1)

        # ⌘R renames the selected artist (SoundBox-side alias — rekordbox
        # untouched; applies everywhere: display, folders, future exports).
        for binding in rename_bindings:
            QShortcut(QKeySequence(binding), self, activated=self._rename)

        row = QHBoxLayout()
        row.addStretch(1)
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.clicked.connect(self._rename)
        row.addWidget(self.rename_btn)
        self.url_btn = QPushButton("Set from URL…")
        self.url_btn.clicked.connect(self._use_url)
        row.addWidget(self.url_btn)
        self.url_btn.setVisible(allow_url)
        self.file_btn = QPushButton("Choose image file…")
        self.file_btn.clicked.connect(self._use_file)
        row.addWidget(self.file_btn)
        self.delete_btn = QPushButton("Delete avatar")
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(self.delete_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self._sync_buttons()

    # ---- selection / plumbing ----
    def _current(self):
        if self._closed or self._busy:
            return None
        it = self.list.currentItem()
        return it.data(0, _ROLE) if it else None

    def _sync_buttons(self, busy=None):
        if busy is not None:
            self._busy = busy
        on = self._current() is not None
        for b in (self.url_btn, self.file_btn, self.delete_btn, self.rename_btn):
            b.setEnabled(on)

    def _dispatch(self, choice: dict):
        """Hand one change to the app; update the row in place when it lands.
        The dialog stays open — fix the whole catalog in one sitting."""
        if self._on_action is None or self._closed or self._busy:
            return
        item = self.list.currentItem()
        self.status.setText(f"Applying to {choice['artist']}…")
        self._sync_buttons(busy=True)

        def done(ok: bool, img=None):
            if self._closed:
                return
            self._sync_buttons(busy=False)
            if not ok:
                self.status.setText(f"No picture found for {choice['artist']}.")
                return
            if choice["kind"] == "delete":
                # No avatar left to show — the row leaves the catalog. The art
                # is BLOCKED (⊘): it will never be auto-refetched.
                self.status.setText(f"{choice['artist']} avatar deleted "
                                    "(blocked — won't come back) ✓")
                if item is not None:
                    self._thumbnails.remove(item)
                    self.list.takeTopLevelItem(
                        self.list.indexOfTopLevelItem(item))
                return
            self.status.setText(f"{choice['artist']} updated ✓")
            if item is not None:
                if choice["kind"] == "rename":
                    item.setText(0, choice["new"])
                    item.setData(0, _ROLE, (choice["new"], choice["tids"]))
                elif img is not None:
                    self._thumbnails.set_image(item, img)

        self._on_action(choice, done)

    def done(self, result):
        self._closed = True
        self._thumbnails.clear()
        super().done(result)

    # ---- actions ----
    def _rename(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        new_name, ok = QInputDialog.getText(
            self, "Rename artist",
            f"Rename “{artist}” everywhere in SoundBox "
            "(rekordbox is never touched):", text=artist)
        new_name = (new_name or "").strip()
        if ok and new_name and new_name != artist:
            self._dispatch({"kind": "rename", "artist": artist, "tids": tids,
                            "new": new_name})

    def _use_url(self):
        if not self._allow_url:
            return
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        url, ok = QInputDialog.getText(
            self, "Artist picture",
            f"SoundCloud URL for {artist} "
            "(artist profile = avatar, track = its cover):")
        if ok and url.strip():
            self._dispatch({"kind": "url", "artist": artist, "tids": tids,
                            "url": url.strip()})

    def _use_file(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose picture for {artist}", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if path:
            self._dispatch({"kind": "file", "artist": artist, "tids": tids,
                            "path": path})

    def _delete(self):
        cur = self._current()
        if not cur:
            return
        artist, tids = cur
        self._dispatch({"kind": "delete", "artist": artist, "tids": tids})
