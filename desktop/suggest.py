"""Human-gated artist windows.

SuggestDialog — scans Unknown-Artist tracks (one polite SoundCloud search each,
off-thread) and streams uploader-consensus suggestions into a checkbox list.
MergeDialog — reviews likely duplicate artist spellings (caller precomputes the
local fuzzy scan) plus existing merges with an Unmerge path.

NOTHING is ever auto-applied: accepting writes normal overrides/aliases, which
also feed the trusted-artist set. SuggestDialog owns a QThread — its caller
MUST call dlg.shutdown() after exec() (QThread lifetime rule)."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from soundbox.providers import ArtistProvider, provider_operation
from .task_owner import TaskOwner

_ROLE = Qt.UserRole + 21  # (track_id, suggested_artist)


class SuggestWorker(QThread):
    """Politely walks the Unknowns (one SoundCloud search each) off the UI
    thread; emits every consensus hit as it lands."""
    progress = Signal(int, int, int)     # scanned, total, hits
    found = Signal(str, str, str, float)  # tid, title, artist, confidence

    DELAY = 1.2  # seconds between searches — polite, never a burst

    def __init__(self, items, provider: ArtistProvider | None = None):
        super().__init__()
        self.provider = provider
        self.items = list(items)  # [(tid, title)]
        self._stop = False
        self._cancelled = threading.Event()

    def stop(self):
        self._stop = True
        self._cancelled.set()

    def run(self):
        if self.provider is None:
            return
        hits = 0
        total = len(self.items)
        for i, (tid, title) in enumerate(self.items, 1):
            if self._stop:
                break
            try:
                with provider_operation(self.provider,lambda:self._stop):
                    r = self.provider.suggest_artist(title)
            except Exception:  # noqa: BLE001 — offline/blocked: keep scanning
                r = None
            if self._stop:
                break
            if r:
                hits += 1
                self.found.emit(tid, title, r[0], r[1])
            self.progress.emit(i, total, hits)
            self._cancelled.wait(self.DELAY)


class SuggestDialog(QDialog):
    def __init__(self, items, parent=None, *, provider: ArtistProvider | None = None):
        super().__init__(parent)
        self.setWindowTitle("Suggest artists")
        self.resize(680, 480)
        self.accepts: list[tuple[str, str]] = []  # (tid, artist) on Accept

        v = QVBoxLayout(self)
        self.status = QLabel(f"Scanning {len(items)} Unknown-Artist tracks — "
                             "suggestions appear as they're found…")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Track", "Suggested artist", "Match"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.header().resizeSection(0, 330)
        self.list.header().resizeSection(1, 200)
        v.addWidget(self.list, 1)

        row = QHBoxLayout()
        note = QLabel("Nothing is pre-checked — tick the ones you trust; "
                      "those become normal artist overrides.")
        note.setWordWrap(True)
        row.addWidget(note, 1)
        self.stop_btn = QPushButton("Stop scanning")
        self.stop_btn.clicked.connect(self._stop_scan)
        row.addWidget(self.stop_btn)
        self.accept_btn = QPushButton("Accept checked")
        self.accept_btn.clicked.connect(self._accept_checked)
        row.addWidget(self.accept_btn)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        v.addLayout(row)

        self._tasks = TaskOwner(self)
        self._worker = SuggestWorker(items, provider)
        self._worker.found.connect(self._add_suggestion)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        if provider is not None:
            self._tasks.adopt(self._worker)
            self._worker.start()
        else:
            self.status.setText("Artist suggestions are unavailable.")
            self.stop_btn.setEnabled(False)

    def shutdown(self):
        """Keep events alive until the exact search worker has retired."""
        self._tasks.drain()

    def done(self, result):
        self._tasks.stop()
        super().done(result)

    def closeEvent(self, ev):
        self._tasks.stop()
        super().closeEvent(ev)

    def _add_suggestion(self, tid: str, title: str, artist: str, conf: float):
        if self._tasks.stopping:
            return
        it = QTreeWidgetItem([title, artist, f"{conf:.2f}"])
        it.setData(0, _ROLE, (tid, artist))
        # UNCHECKED by default (product decision): suggestions stream in while
        # the user isn't looking — never arm an accidental "Accept checked".
        it.setCheckState(0, Qt.Unchecked)
        self.list.addTopLevelItem(it)

    def _on_progress(self, scanned: int, total: int, hits: int):
        if self._tasks.stopping:
            return
        self.status.setText(f"Scanned {scanned}/{total} — {hits} suggestion(s). "
                            "You can accept at any time.")

    def _on_finished(self):
        if self._tasks.stopping:
            return
        self.stop_btn.setEnabled(False)
        if not self.list.topLevelItemCount():
            self.status.setText("Scan done — no confident suggestions. The rest "
                                "need your ears (or they're unreleased).")

    def _stop_scan(self):
        self._tasks.stop()
        self.stop_btn.setEnabled(False)

    def _accept_checked(self):
        self.accepts = [it.data(0, _ROLE)
                        for i in range(self.list.topLevelItemCount())
                        if (it := self.list.topLevelItem(i)).checkState(0) == Qt.Checked]
        self.accept()


class MergeDialog(QDialog):
    """Suggested artist merges (precomputed, local) in the same accept-window
    style as artist suggestions. Existing merges are listed too, with right-
    click → Unmerge — so a bad accept is never one-way. No worker: the caller
    scans off-thread and hands results in."""

    def __init__(self, suggestions, existing, counts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Suggest artist merges")
        self.resize(660, 500)
        self.accepts: list[tuple[str, str]] = []   # (variant, target)
        self.unmerges: list[str] = []              # variants to un-alias

        v = QVBoxLayout(self)
        head = QLabel(f"{len(suggestions)} likely duplicate spelling(s) found. "
                      "Checked ones merge — the variant can then never appear "
                      "as its own artist. Uncheck anything that's really two "
                      "different artists.")
        head.setWordWrap(True)
        v.addWidget(head)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Merge", "Into", "Match"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.header().resizeSection(0, 260)
        self.list.header().resizeSection(1, 260)
        for variant, target, ratio in suggestions:
            it = QTreeWidgetItem([f"{variant}  ({counts.get(variant, 0)})",
                                  f"{target}  ({counts.get(target, 0)})",
                                  f"{ratio:.2f}"])
            it.setData(0, _ROLE, (variant, target))
            it.setCheckState(0, Qt.Checked)
            self.list.addTopLevelItem(it)
        v.addWidget(self.list, 1)

        if existing:
            v.addWidget(QLabel("Existing merges (right-click to unmerge):"))
            self.done_list = QTreeWidget()
            self.done_list.setHeaderHidden(True)
            self.done_list.setRootIsDecorated(False)
            self.done_list.setFixedHeight(96)
            for src, target in sorted(existing.items(), key=lambda kv: kv[0].lower()):
                it = QTreeWidgetItem([f"{src}   →   {target}"])
                it.setData(0, _ROLE, ("existing", src))
                self.done_list.addTopLevelItem(it)
            self.done_list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.done_list.customContextMenuRequested.connect(self._existing_menu)
            v.addWidget(self.done_list)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Apply")
        ok.clicked.connect(self._apply)
        row.addWidget(ok)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        v.addLayout(row)

    def _existing_menu(self, pos):
        from .choices import Menu as QMenu
        it = self.done_list.itemAt(pos)
        data = it.data(0, _ROLE) if it else None
        if data and data[0] == "existing":
            menu = QMenu(self)
            menu.addAction("Unmerge", lambda: self._unmerge(it, data[1]))
            menu.exec(self.done_list.mapToGlobal(pos))

    def _unmerge(self, item, src):
        self.unmerges.append(src)
        self.done_list.takeTopLevelItem(self.done_list.indexOfTopLevelItem(item))

    def _apply(self):
        self.accepts = [it.data(0, _ROLE)
                        for i in range(self.list.topLevelItemCount())
                        if (it := self.list.topLevelItem(i)).checkState(0) == Qt.Checked]
        self.accept()
