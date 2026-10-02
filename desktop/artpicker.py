"""Manual cover-art picker: a thumbnail grid of SoundCloud candidates for one
track (the human judges — no confidence gate, unlike the auto-matcher), plus
paste-a-URL and choose-a-file escape hatches for the truly unfindable.

The dialog only CHOOSES; the caller does the real fetch/store (through
ArtCache.store_ok, which normalizes everything to Rockbox-safe baseline JPEG).
`choice` after exec():
    {"kind": "thumb", "cand": {title, uploader, thumb, url, ...}}
    {"kind": "page",  "url": str}
    {"kind": "file",  "path": str}
    None = cancelled
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from soundbox.providers import ArtworkProvider
from .task_owner import TaskOwner

_COLS = 4
_THUMB = 150  # grid icon size; we download the light t200 variant for the grid


class _CandidateSearch(QThread):
    """Searches SoundCloud and streams (candidate, small-thumb bytes) results.
    Network stays off the UI thread; pixmaps are built in the GUI-side slot."""
    found = Signal(dict, bytes)
    done = Signal(int)  # how many results total

    def __init__(self, artist: str, title: str, provider: ArtworkProvider):
        super().__init__()
        self.provider = provider
        self.artist = artist
        self.title = title
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if self._stop:
            return
        try:
            cands = self.provider.search_candidates(self.artist, self.title)
        except Exception:  # noqa: BLE001 — offline/blocked: URL + file still work
            cands = []
        n = 0
        for c in cands:
            if self._stop:
                break
            try:
                got = self.provider.preview_image(c)
            except Exception:  # provider failures leave the local picker usable
                got = None
            if self._stop:
                break
            if got:
                n += 1
                self.found.emit(c, got)
        if not self._stop:
            self.done.emit(n)


class ArtPickerDialog(QDialog):
    def __init__(self, artist: str, title: str, parent=None, *,
                 provider: ArtworkProvider | None = None):
        super().__init__(parent)
        self.setWindowTitle("Choose cover art")
        self.resize(740, 560)
        self.provider = provider
        self.choice: dict | None = None
        self._count = 0

        v = QVBoxLayout(self)
        head = QLabel(f"Cover art for:  {artist or 'Unknown Artist'} — {title}")
        head.setWordWrap(True)
        v.addWidget(head)

        self.status = QLabel("Searching SoundCloud…")
        v.addWidget(self.status)

        grid_host = QWidget()
        self.grid = QGridLayout(grid_host)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        v.addWidget(scroll, 1)

        # Escape hatches: a pasted SoundCloud URL, or any local image file.
        row = QHBoxLayout()
        self.url_edit = QLineEdit()
        # A TRACK url yields its cover; an ARTIST PROFILE url yields the avatar —
        # the escape hatch when an uploader's exclusive carries junk cover art.
        self.url_edit.setPlaceholderText(
            "Paste a SoundCloud URL — track = its cover, artist profile = avatar…")
        self.url_edit.returnPressed.connect(self._use_url)
        row.addWidget(self.url_edit, 1)
        use = QPushButton("Use URL")
        use.clicked.connect(self._use_url)
        row.addWidget(use)
        pick = QPushButton("Choose image file…")
        pick.clicked.connect(self._use_file)
        row.addWidget(pick)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        v.addLayout(row)

        self._tasks = TaskOwner(self)
        self._worker = None
        if provider is not None:
            self._worker = _CandidateSearch(artist, title, provider)
            self._worker.found.connect(self._add_candidate)
            self._worker.done.connect(self._search_done)
            self._tasks.adopt(self._worker)
            self._worker.start()
        else:
            self.status.setText("Choose an image from your Mac.")
            scroll.hide()
            self.url_edit.hide()
            use.hide()
            self.resize(620, 160)

    def shutdown(self):
        """Keep events alive until the exact search worker has retired."""
        self._tasks.drain()

    def done(self, result):
        self._tasks.stop()
        super().done(result)

    def closeEvent(self, ev):
        self._tasks.stop()
        super().closeEvent(ev)

    def _add_candidate(self, cand: dict, img: bytes):
        if self._tasks.stopping:
            return
        pm = QPixmap()
        if not pm.loadFromData(img):
            return
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        btn.setIcon(QIcon(pm))
        btn.setIconSize(QSize(_THUMB, _THUMB))
        btn.setFixedSize(_THUMB + 20, _THUMB + 52)
        t, u = cand["title"], cand["uploader"]
        btn.setText(f"{t[:22]}…" if len(t) > 23 else t)
        btn.setToolTip(f"{t}\nby {u}\n{cand['url']}")
        btn.clicked.connect(lambda _=False, c=cand: self._use_thumb(c))
        self.grid.addWidget(btn, self._count // _COLS, self._count % _COLS)
        self._count += 1
        self.status.setText(f"{self._count} result(s) — click one, or use a URL/file below")

    def _search_done(self, n: int):
        if self._tasks.stopping:
            return
        if n == 0:
            self.status.setText("No SoundCloud results — paste a track URL or choose a file.")

    def _use_thumb(self, cand: dict):
        if self.provider is None:
            return
        self.choice = {"kind": "thumb", "cand": cand}
        self.accept()

    def _use_url(self):
        if self.provider is None:
            return
        url = self.url_edit.text().strip()
        if url:
            self.choice = {"kind": "page", "url": url}
            self.accept()

    def _use_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cover image", "", "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if path:
            self.choice = {"kind": "file", "path": path}
            self.accept()
