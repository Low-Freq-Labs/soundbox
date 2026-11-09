"""Human artwork review. Searches/previews never publish canonical artwork."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import io
from pathlib import Path
from collections import deque
from threading import Lock, Event

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTabBar, QVBoxLayout, QWidget,
    QStackedWidget,
)
from .choices import ComboBox as QComboBox, Menu as QMenu

from soundbox.art_sources import account_name, excluded_source, same_art_source, profile_source
from soundbox.artwork import ArtResult
from soundbox.art_review_scan import ReviewScanStore, context_stamp
from soundbox.providers import provider_operation
from .artpicker import ArtPickerDialog, _CandidateSearch
from .task_owner import TaskOwner
from . import theme


def _same_art(original, preview):
    """Omit effectively identical art, including different CDN image sizes."""
    from PIL import Image, ImageChops, ImageOps, ImageStat, ImageFilter
    try:
        with Image.open(io.BytesIO(original)) as a, Image.open(io.BytesIO(preview)) as b:
            a = ImageOps.fit(a.convert("RGB"), (64, 64), method=Image.Resampling.LANCZOS)
            b = ImageOps.fit(b.convert("RGB"), (64, 64), method=Image.Resampling.LANCZOS)
            if sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 <= 3:
                return True
            # Downsampling/compression can move sharp edges enough to defeat a
            # raw pixel comparison. Compare coarse layout AND colour after blur;
            # keep the bounds narrow so another design remains a real choice.
            a = a.resize((32, 32), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1))
            b = b.resize((32, 32), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1))
            if sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 > 8:
                return False
            x, y = list(a.convert("L").get_flattened_data()), list(b.convert("L").get_flattened_data())
            mx, my = sum(x) / len(x), sum(y) / len(y)
            vx, vy = sum((v - mx) ** 2 for v in x), sum((v - my) ** 2 for v in y)
            if not vx or not vy:
                return False
            correlation = sum((u - mx) * (v - my) for u, v in zip(x, y)) / (vx * vy) ** .5
            return correlation >= .985
    except (OSError, ValueError):
        return False


def _usable(candidate, snapshot, exclusions):
    return ((candidate.get("score", 0) >= .8 or candidate.get("possible") and candidate.get("score", 0) >= .65) and
            not excluded_source(candidate.get("uploader", ""), candidate.get("url", ""),
                                excluded_accounts=exclusions) and
            not same_art_source(candidate.get("url", ""), snapshot.entry.get("src", "")))


class _ReviewScan(QThread):
    """Full-library, resumable checks. Metadata persists; previews stay bounded."""
    scanned = Signal(str, object)
    progress = Signal(int, int)
    restored = Signal(int, int, int, int)
    DELAY = .6  # only new online checks; resume/Keep/cache hits never sleep

    def __init__(self, cache, provider, rows, *, fresh=False):
        super().__init__()
        self.cache, self.provider, self.rows = cache, provider, rows
        self._stop = False
        self._cancel = Event()
        self.fresh = fresh
        self.error = ""
        self.reused = self.kept = self.failed = self.policy_skips = 0
        self._pending = deque()
        self._priority = None
        self._lock = Lock()

    def stop(self):
        self._stop = True
        self._cancel.set()

    def prioritize(self, tid):
        with self._lock:
            self._priority = tid
            for row in self._pending:
                if row[0] == tid:
                    self._pending.remove(row)
                    self._pending.appendleft(row)
                    break

    def run(self):
        store = None
        try:
            store = ReviewScanStore(self.cache.root.parent / "art_review_scan")
            if self.fresh:
                store.invalidate()
            self._run_checks(store)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if store is not None:
                store.close()

    def _saved_check(self, row, store, kept):
        tid, artist, title, _ = row
        snapshot = self.cache.cover_snapshot(tid)
        exclusions = self.cache.source_exclusions()
        stamp = context_stamp(snapshot.entry, snapshot.digest, artist, title, exclusions)
        baseline = dict(entry=snapshot.entry, digest=snapshot.digest)
        decision = dict(**baseline, artist=artist, title=title)
        result = None
        if self.cache.source_excluded(tid):
            self.policy_skips += 1
            result = dict(**baseline, choices=[], skipped="excluded")
        elif kept.get(tid) == decision:
            self.kept += 1
            result = dict(**baseline, choices=[], skipped="kept")
        elif (cached := store.get(tid, stamp)) is not None:
            self.reused += 1
            result = dict(**baseline, choices=[(c, store.preview_get(c) or b"") for c in cached["candidates"]], reused=True)
        return result, snapshot, exclusions, stamp

    def _run_checks(self, store):
        # Restore ALL exact saved contexts before any potentially slow search.
        # Otherwise a new first row can block ready comparisons later in the list.
        kept = self.cache.kept_covers()
        pending = []
        for row in self.rows:
            if self._stop:
                return
            try:
                result, _, _, _ = self._saved_check(row, store, kept)
            except Exception as exc:
                self.failed += 1
                result = dict(error=str(exc))
            if result is None:
                pending.append(row)
            else:
                self.scanned.emit(row[0], result)
        if self._stop:
            return
        with self._lock:
            self._pending = deque(pending)
            priority = self._priority
        self.prioritize(priority)
        self.restored.emit(self.reused, self.kept, self.policy_skips, len(pending))
        self.progress.emit(0, len(pending))
        i = 0
        consecutive_failures = 0
        while not self._stop:
            with self._lock:
                if not self._pending:
                    break
                row = self._pending.popleft()
            tid, artist, title, _ = row
            i += 1
            try:
                online = False
                # Recheck current decisions/bytes: a Keep or Replace made while
                # restoration/search was running must not trigger another lookup.
                result, snapshot, exclusions, stamp = self._saved_check(row, store, self.cache.kept_covers())
                if result is None:
                    online = True
                    with provider_operation(self.provider,lambda:self._stop):
                        result = self._check_track(snapshot, exclusions, artist, title)
                    if self._stop:
                        break
                    store.put(tid, stamp, dict(candidates=[c for c, _ in result["choices"]]))
                    for candidate, preview in result["choices"]:
                        store.preview_put(candidate, preview)
            except Exception as exc:
                self.failed += 1
                result = dict(error=str(exc))
            consecutive_failures = consecutive_failures + 1 if "error" in result else 0
            if self._stop:
                break
            self.scanned.emit(tid, result)
            self.progress.emit(i, len(pending))
            if consecutive_failures >= 5:
                self.error = "Artwork search is unavailable after five consecutive failures. Completed checks are saved; resume to retry."
                break
            if online:
                self._cancel.wait(self.DELAY if "error" not in result else max(self.DELAY, 2))

    def _check_track(self, snapshot, exclusions, artist, title):
        choices = []
        for candidate in self.provider.search_candidates(artist, title)[:12]:
            if self._stop:
                break
            if not _usable(candidate, snapshot, exclusions):
                continue
            preview = self.provider.preview_image(candidate)
            if preview and len(preview) <= 512 * 1024 and not _same_art(snapshot.data, preview):
                choices.append((candidate, preview))
                break
        return dict(entry=snapshot.entry, digest=snapshot.digest, choices=choices)


class _ReviewPreview(QThread):
    found = Signal(dict, bytes)

    def __init__(self, provider, candidates, preview_root=None):
        super().__init__()
        self.provider, self.candidates = provider, candidates
        self._stop = False
        self.preview_root = preview_root
        self.error = ""

    def stop(self):
        self._stop = True

    def run(self):
        store = None
        try:
            if self.preview_root is not None:
                store = ReviewScanStore(self.preview_root)
            for candidate in self.candidates[:12]:
                if self._stop:
                    break
                try:
                    data = store.preview_get(candidate) if store else None
                    if not data:
                        with provider_operation(self.provider,lambda:self._stop):
                            data = self.provider.preview_image(candidate)
                        if data and store and not self._stop:
                            store.preview_put(candidate, data)
                except Exception:
                    continue
                if data and not self._stop:
                    self.found.emit(candidate, data)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if store is not None:
                store.close()


class _Spinner(QWidget):
    def __init__(self, size=44):
        super().__init__()
        self.setFixedSize(size, size)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)

    def set_running(self, running):
        self._timer.start() if running else self._timer.stop()
        self.setVisible(running)

    def _advance(self):
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width()/2, self.height()/2)
        radius = self.width() * .33
        dot = self.width() * .055
        painter.setPen(Qt.NoPen)
        for i in range(12):
            color = QColor(theme.PURPLE_LIGHT)
            color.setAlpha(40 + ((i - self._step) % 12) * 19)
            painter.setBrush(color)
            painter.drawEllipse(int(radius), int(-dot), max(2,int(dot*2)), max(2,int(dot*2)))
            painter.rotate(30)


class _ReviewFetch(QThread):
    def __init__(self, provider, candidate):
        super().__init__()
        self.provider, self.candidate = provider, candidate
        self.result = None
        self.error = ""
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if self._stop:
            return
        try:
            c = self.candidate
            if c.get("kind") == "file":
                path = Path(c["path"])
                if path.stat().st_size > 6 * 1024**2:
                    raise ValueError("Choose an image smaller than 6 MB")
                result = ArtResult(path.read_bytes(), "image/png" if path.suffix.lower() == ".png" else "image/jpeg",
                                   1, path.as_uri(), path.name)
            elif c.get("kind") == "artist-profile":
                fetch = getattr(self.provider, "fetch_profile_art", None)
                with provider_operation(self.provider,lambda:self._stop):
                    result = fetch(c["artist"]) if callable(fetch) else None
                if result is not None and (not profile_source(result.source_url) or result.confidence < .9):
                    raise ValueError("A matching artist profile could not be verified")
            elif c.get("kind") == "page":
                with provider_operation(self.provider,lambda:self._stop):
                    result = self.provider.fetch_by_url(c["url"])
            else:
                with provider_operation(self.provider,lambda:self._stop):
                    result = self.provider.fetch_thumb(c["thumb"], c["url"], c["title"])
            if not self._stop:
                self.result = result
        except Exception as exc:
            self.error = str(exc)


class ArtReviewDialog(QDialog):
    changed = Signal(str)
    exclusions_changed = Signal()

    def __init__(self, cache, rows, parent=None, *, provider=None, player=None, play_track=None):
        """rows: (track ID, effective artist, effective title, provenance label)."""
        super().__init__(parent)
        self.cache, self.provider = cache, provider
        self._tasks = TaskOwner(self)
        self._busy = False
        self._worker = None
        self._prepare_worker = None
        self._preparing = False
        self._last_changed = None
        self._generation = 0
        self._cached = OrderedDict()
        self._metadata = {}
        self._checked = set()
        self._dismissed = set()
        self._advance_after = None
        self._rows = {row[0]: row for row in rows}
        self._suggested = set()
        self._targets = set(self._rows)  # every cover in All, regardless of origin/confidence
        self._failures = 0
        self._last_progress = (0, len(rows))
        self._saved_count = self._reviewed_count = 0
        self._snapshot = None
        self._row = None
        self.setWindowTitle("Review cover artwork")
        self.resize(1140, 760)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._views = QStackedWidget()
        outer.addWidget(self._views)
        self._loading_page = QWidget()
        loading = QVBoxLayout(self._loading_page)
        loading.addStretch()
        self._loading_spinner = _Spinner(64)
        loading.addWidget(self._loading_spinner, 0, Qt.AlignCenter)
        loading.addWidget(QLabel("Preparing cover suggestions…"), 0, Qt.AlignCenter)
        self._loading_progress = QLabel(f"Checking your whole library · {len(rows):,} covers")
        loading.addWidget(self._loading_progress, 0, Qt.AlignCenter)
        loading.addWidget(QLabel("Your covers stay unchanged. Completed checks are saved."), 0, Qt.AlignCenter)
        browse_loading = QPushButton("Browse All while checking")
        browse_loading.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        loading.addWidget(browse_loading, 0, Qt.AlignCenter)
        loading.addStretch()
        close_loading = QPushButton("Close")
        close_loading.clicked.connect(self.reject)
        loading.addWidget(close_loading, 0, Qt.AlignRight)
        self._views.addWidget(self._loading_page)
        self._review_page = QWidget()
        self._views.addWidget(self._review_page)
        layout = QVBoxLayout(self._review_page)
        intro = QLabel("Compare prepared suggestions. Keep your cover, replace it, or move to the next track.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        running = QHBoxLayout()
        self._inline_spinner = _Spinner(20)
        self._library_progress = QLabel()
        running.addWidget(self._inline_spinner)
        running.addWidget(self._library_progress, 1)
        layout.addLayout(running)
        self.tabs = QTabBar()
        self.tabs.setObjectName("ReviewTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab("Suggested")
        self.tabs.addTab("All")
        layout.addWidget(self.tabs, 0, Qt.AlignLeft)
        split = QSplitter()
        layout.addWidget(split, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 12, 0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find artist or title…")
        self.filter.textChanged.connect(self._browse_filter)
        lv.addWidget(self.filter)
        self.tracks = QListWidget()
        self.tracks.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tracks.setTextElideMode(Qt.ElideRight)
        lv.addWidget(self.tracks, 1)
        self.count = QLabel()
        self.count.setWordWrap(True)
        lv.addWidget(self.count)
        self.scan = QPushButton("Prepare suggestions")
        self.scan.clicked.connect(self._scan)
        self.scan.hide()
        self.stop_scan = QPushButton("Stop checking")
        self.stop_scan.clicked.connect(self._stop_scan)
        self.stop_scan.hide()
        lv.addWidget(self.stop_scan)
        for row in rows:
            item = QListWidgetItem(row[2])
            item.setData(Qt.UserRole, row)
            item.setToolTip(row[2])
            self.tracks.addItem(item)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.title)
        images = QHBoxLayout()
        self.current = self._image_panel(images, "Current cover")
        self.proposed = self._image_panel(images, "Suggested cover")
        rv.addLayout(images)
        self.source = QLabel()
        self.source.setWordWrap(True)
        self.source.setTextFormat(Qt.PlainText)
        self.source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.source)
        self.find = QPushButton("Check again")
        self.find.clicked.connect(self._search)
        self.find.hide()
        self.candidates = QListWidget(self)
        self.candidates.setIconSize(QSize(64, 64))
        self.candidates.currentItemChanged.connect(self._candidate_selected)
        self.candidates.hide()
        self.options = QComboBox()
        self.options.setMinimumContentsLength(15)
        self.options.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.options.currentIndexChanged.connect(self.candidates.setCurrentRow)
        self.options.hide()
        rv.addWidget(self.options)
        self.evidence = QLabel()
        self.evidence.setTextFormat(Qt.PlainText)
        self.evidence.setWordWrap(True)
        self.evidence.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.evidence)
        rv.addStretch(1)
        actions = QHBoxLayout()
        self.undo = QPushButton("Undo review change")
        self.undo.clicked.connect(self._undo)
        self.undo.hide()
        from .reviewplayer import ReviewPlayer
        self.audition = ReviewPlayer(player=player, start=play_track)
        actions.addWidget(self.audition, 1)
        self.keep = QPushButton("Keep")
        self.keep.clicked.connect(self._keep)
        actions.addWidget(self.keep)
        self.use = QPushButton("Replace")
        self.use.setAutoDefault(False)
        self.use.clicked.connect(self._approve)
        actions.addWidget(self.use)
        self.next = QPushButton("Next")
        self.next.clicked.connect(self._next)
        actions.addWidget(self.next)
        rv.addLayout(actions)
        split.addWidget(right)
        split.setSizes([370, 740])
        self.status = QLabel("Select a track to review. Searching and closing keep all artwork unchanged.")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        foot = QHBoxLayout()
        note = QLabel("Originals stay recoverable. More contains artist pictures and manual artwork choices.")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        self.more = QPushButton("More…")
        self.more.setObjectName("ReviewMoreButton")
        self.more.setAutoDefault(False)
        menu = QMenu(self.more)
        self._choose_action = menu.addAction("Choose another cover…", self._choose_other)
        self._profile_action = menu.addAction("Use artist profile…", self._use_profile)
        self._refresh_action = menu.addAction("Refresh this comparison", self._search)
        self._resume_action = menu.addAction("Resume library check", self._scan)
        self._rescan_action = menu.addAction("Recheck whole library", lambda: self._scan(fresh=True))
        self._stop_action = menu.addAction("Stop preparing comparisons", self._stop_scan)
        self._exclude_action = menu.addAction("Exclude Uploader…", self._manage_exclusions)
        self.more.setMenu(menu)
        foot.addWidget(self.more)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        foot.addWidget(close)
        layout.addLayout(foot)
        self._empty_page = QWidget()
        empty = QVBoxLayout(self._empty_page)
        empty.addStretch()
        self._empty_title = QLabel("No new suggestions")
        self._empty_title.setAlignment(Qt.AlignCenter)
        empty.addWidget(self._empty_title)
        self._empty_detail = QLabel()
        self._empty_detail.setWordWrap(True)
        self._empty_detail.setAlignment(Qt.AlignCenter)
        empty.addWidget(self._empty_detail)
        browse_empty = QPushButton("Browse All covers")
        browse_empty.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        empty.addWidget(browse_empty, 0, Qt.AlignCenter)
        retry = QPushButton("Check again / resume")
        retry.clicked.connect(self._scan)
        empty.addWidget(retry, 0, Qt.AlignCenter)
        empty.addStretch()
        close_empty = QPushButton("Close")
        close_empty.clicked.connect(self.reject)
        empty.addWidget(close_empty, 0, Qt.AlignRight)
        self._views.addWidget(self._empty_page)
        self._list_refresh = QTimer(self)
        self._list_refresh.setSingleShot(True)
        self._list_refresh.setInterval(50)
        self._list_refresh.timeout.connect(self._filter)
        self.tracks.currentItemChanged.connect(self._selected)
        self.tabs.currentChanged.connect(self._browse_filter)
        self._filter()
        self._controls()
        if self._targets and self.provider is not None:
            self._loading_spinner.set_running(True)
            self._views.setCurrentWidget(self._loading_page)
            QTimer.singleShot(0, self._scan)
        else:
            self._inline_spinner.set_running(False)
            self._loading_spinner.set_running(False)
            self._show_empty("There are no existing covers to review." if not rows else
                             "No online artwork provider is connected. All keeps your existing covers available.")

    def _show_empty(self, detail):
        if self.tabs.currentIndex() == 1:
            return
        self._loading_spinner.set_running(False)
        self._empty_detail.setText(detail)
        self._views.setCurrentWidget(self._empty_page)

    def _reveal(self):
        self._loading_spinner.set_running(False)
        self._views.setCurrentWidget(self._review_page)

    def _reason(self, tid):
        entry = self.cache.index.get(tid, {})
        if excluded_source(url=entry.get("src", ""), excluded_accounts=self.cache.source_exclusions()):
            return "Excluded uploader — this existing cover needs a look"
        if entry.get("fallback") == "excluded-source":
            return ""  # intentional artist-profile default, not a cover-matching failure
        if entry.get("origin") == "auto":
            if self.cache._is_avatar(entry):
                return "Automatic artist picture — check for a track cover"
            if entry.get("conf", 1) < .8:
                return "Low-confidence automatic match"
        return ""

    @staticmethod
    def _image_panel(layout, title):
        box = QVBoxLayout()
        box.addWidget(QLabel(title), 0, Qt.AlignCenter)
        image = QLabel("—")
        image.setAlignment(Qt.AlignCenter)
        image.setMinimumSize(300, 300)
        image.setMaximumHeight(300)
        box.addWidget(image)
        layout.addLayout(box, 1)
        return image

    @staticmethod
    def _show_image(label, data):
        pix = QPixmap()
        if pix.loadFromData(data):
            ratio=label.devicePixelRatioF()
            pix=pix.scaled(round(300*ratio),round(300*ratio),Qt.KeepAspectRatio,Qt.SmoothTransformation)
            pix.setDevicePixelRatio(ratio)
            label.setPixmap(pix)
        else:
            label.setText('Artwork unavailable')

    def _browse_filter(self, *_):
        self._advance_after = None
        self._filter()

    def _filter(self, *_):
        text = self.filter.text()
        suggested = self._suggested - self._dismissed
        self.tabs.setTabText(0, f"Suggested ({len(suggested):,})")
        self.tabs.setTabText(1, f"All ({len(self._rows):,})")
        first, visible = None, 0
        for i in range(self.tracks.count()):
            item = self.tracks.item(i)
            row = item.data(Qt.UserRole)
            show = (self.tabs.currentIndex() == 1 or row[0] in suggested) and text.casefold() in (
                row[1] + " " + row[2]).casefold()
            item.setHidden(not show)
            if show:
                visible += 1
                if first is None and (self._advance_after is None or i > self._advance_after):
                    first = item
        self.count.setText(f"{visible:,} {'to look at' if self.tabs.currentIndex() == 0 else 'covers'} · "
                           f"{len(self._checked):,} completed · {self._reviewed_count:,} previously reviewed")
        current = self.tracks.currentItem()
        if current is None or current.isHidden():
            if first:
                self.tracks.setCurrentItem(first)
            else:
                self.tracks.setCurrentRow(-1)
        if self.tabs.currentIndex() == 1:
            self._reveal()
        elif not suggested:
            if self._preparing:
                self._loading_spinner.set_running(True)
                self._views.setCurrentWidget(self._loading_page)
            else:
                self._show_empty(f"{len(self._checked):,} covers checked. No unreviewed suggestions remain. "
                                 + (f"{self._failures:,} checks could not complete; resume to retry." if self._failures else
                                    "You can still browse every cover in All."))

    def _selected(self, item, _old=None):
        self._generation += 1
        if isinstance(self._worker, (_CandidateSearch, _ReviewPreview)):
            self._worker.stop()
        self._snapshot = None
        self._row = item.data(Qt.UserRole) if item else None
        if item is not None:
            self._advance_after = None
        self.audition.set_track(self._row[0] if self._row else None)
        self.candidates.clear()
        self.options.clear()
        self.options.hide()
        self.current.clear()
        self.proposed.setText("—")
        self.source.clear()
        self.title.clear()
        self.evidence.clear()
        if self._row:
            tid, artist, title, origin = self._row
            self.title.setText(title)
            self.title.setTextFormat(Qt.PlainText)
            try:
                self._snapshot = self.cache.cover_snapshot(tid)
                if self.cache.source_excluded(tid):
                    self.current.setText("Excluded source\nArtist profile art is being prepared")
                else:
                    self._show_image(self.current, self._snapshot.data)
                origin = {"manual": "Manual", "auto": "Automatic"}.get(
                    self._snapshot.entry.get("origin"), "Unrecorded history")
                reason = self._reason(tid)
                self.source.setText(f"{origin} · protected" + (f"\n{reason}" if reason else "") + "\nCurrent source: "
                                    f"{self._snapshot.entry.get('src') or 'Not recorded'}")
                cached = self._cached.get(tid)
                if cached and cached.get("entry") == self._snapshot.entry and cached.get("digest") == self._snapshot.digest:
                    for cand, data in cached["choices"]:
                        self._found(cand, data)
                    if self.candidates.count():
                        self.candidates.setCurrentRow(0)
                    self._search_status()
                elif (meta := self._metadata.get(tid)) and meta["entry"] == self._snapshot.entry and meta["digest"] == self._snapshot.digest:
                    if meta["candidates"]:
                        self._load_preview(meta["candidates"])
                    else:
                        self._search_status()
                elif self._preparing and self._prepare_worker is not None:
                    self._prepare_worker.prioritize(tid)
                    self.status.setText("Preparing this comparison… Your current cover is unchanged.")
                elif self.provider is not None and not self.cache.source_excluded(tid) and not (self._preparing and tid in self._targets):
                    token = self._generation
                    QTimer.singleShot(0, lambda: self._search() if token == self._generation and
                                      not self._tasks.stopping else None)
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))
        self._controls()

    def refresh_profile(self, tid):
        if self._tasks.stopping:
            return
        self._cached.pop(tid, None)
        if not self._reason(tid):
            self._suggested.discard(tid)
        if not self._busy:
            self._filter()
            if self._row and self._row[0] == tid:
                self._selected(self.tracks.currentItem())

    def _controls(self):
        selected = self._snapshot is not None
        replacing = isinstance(self._worker, _ReviewFetch)
        self.tracks.setEnabled(not replacing)
        self.filter.setEnabled(not replacing)
        self.tabs.setEnabled(not replacing)
        self.candidates.setEnabled(not self._busy)
        self.find.setEnabled(selected and not self._busy and self.provider is not None)
        self.scan.setEnabled(not self._busy and self.provider is not None)
        self.more.setEnabled(not replacing)
        self.use.setEnabled(selected and not self._busy and not self.cache.read_only and
                            self.candidates.currentItem() is not None)
        self.next.setEnabled(not replacing and self._row is not None)
        self.keep.setEnabled(selected and not replacing and not self.cache.read_only)
        undo_tid = self._last_changed or (self._row[0] if self._row else None)
        self.undo.setEnabled(not self._busy and not self.cache.read_only and
                             undo_tid is not None and self.cache.has_review_undo(undo_tid))
        self._exclude_action.setEnabled(not replacing)
        self._stop_action.setEnabled(self._preparing or isinstance(self._worker, (_CandidateSearch, _ReviewPreview)))
        self._choose_action.setEnabled(selected and not self._busy)
        self._profile_action.setEnabled(selected and not self._busy and not self.cache.read_only and
                                       callable(getattr(self.provider, "fetch_profile_art", None)))
        self._refresh_action.setEnabled(selected and not self._busy and self.provider is not None)
        self._rescan_action.setEnabled(not self._preparing and not self._busy and self.provider is not None)
        self._resume_action.setEnabled(not self._preparing and self.provider is not None)

    def _search(self):
        if self._tasks.stopping or self._busy or not self._snapshot or self.provider is None:
            return
        if self.cache.source_excluded(self._row[0]):
            self._selected(self.tracks.currentItem())
            return
        if self._preparing and self._prepare_worker is not None and self._row[0] not in self._checked:
            self._prepare_worker.prioritize(self._row[0])
            return
        self.candidates.clear()
        self.options.clear()
        self.options.hide()
        self.proposed.setText("—")
        self._busy = True
        self._controls()
        self.status.setText("Finding suggestions… Your current cover stays unchanged.")
        _, artist, title, _ = self._row
        worker = _CandidateSearch(artist, title, self.provider, limit=12)
        token = self._generation
        worker.found.connect(lambda cand, data, t=token: self._found(cand, data, t))
        self._tasks.adopt(worker, lambda w, t=token: self._search_retired(w, t))
        self._worker = worker
        worker.start()

    def _found(self, cand, data, token=None):
        if self._tasks.stopping or not self._snapshot:
            return
        if token is not None and token != self._generation:
            return
        if len(data) > 512 * 1024:
            return
        if not _usable(cand, self._snapshot, self.cache.source_exclusions()) or _same_art(self._snapshot.data, data):
            return
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        liked = "Liked · " if cand.get("liked") else ""
        item = QListWidgetItem(QIcon(pix), f"{liked}{cand['title']}\n{cand['uploader']}")
        item.setData(Qt.UserRole, (cand, data))
        item.setToolTip(cand["url"])
        self.candidates.addItem(item)
        self.options.addItem(f"{cand['title']} — {cand['uploader']}")
        self.options.setVisible(self.candidates.count() > 1)
        if self.candidates.count() == 1:
            self.candidates.setCurrentRow(0)
            self._reveal()

    def _load_preview(self, candidates):
        if self._busy or self.provider is None or self._tasks.stopping:
            return
        worker = _ReviewPreview(self.provider, candidates, self.cache.root.parent / "art_review_scan")
        self._worker = worker
        self._busy = True
        token = self._generation
        worker.found.connect(lambda c, data, t=token: self._found(c, data, t))
        self._tasks.adopt(worker, lambda w, t=token: self._search_retired(w, t))
        self._controls()
        self.status.setText("Loading the prepared comparison…")
        worker.start()

    def _search_retired(self, _worker, token=None):
        if self._tasks.stopping:
            return
        self._busy = False
        self._worker = None
        self.stop_scan.hide()
        if token is not None and token != self._generation:
            self._controls()
            if self._row and self._row[0] not in self._cached:
                QTimer.singleShot(0, lambda: self._selected(self.tracks.currentItem())
                                  if not self._tasks.stopping else None)
            return
        if self._row and self._snapshot:
            tid = self._row[0]
            choices = [self.candidates.item(i).data(Qt.UserRole) for i in range(self.candidates.count())]
            self._remember(tid, dict(entry=self._snapshot.entry, digest=self._snapshot.digest, choices=choices))
            if choices or not isinstance(_worker, _ReviewPreview):
                self._metadata[tid] = dict(entry=self._snapshot.entry, digest=self._snapshot.digest,
                                           candidates=[c for c, _ in choices])
            if not getattr(_worker, "_stop", False):
                self._checked.add(tid)
            if choices:
                self._suggested.add(tid)
        self._search_status()
        if isinstance(_worker, _ReviewPreview) and not self.candidates.count():
            self.status.setText("The comparison is saved, but its preview could not load. Use More → Refresh to retry.")
        self._filter()
        if self.candidates.count():
            self.candidates.setCurrentRow(0)
        self._controls()
        if not self._preparing and self._suggested - self._dismissed:
            self._reveal()

    def _search_status(self):
        n = self.candidates.count()
        self.status.setText(f"{n} different matches to compare. Nothing has changed." if n else
                            "No safe, different match found, or search is unavailable. Current cover kept.")

    def _remember(self, tid, result):
        self._cached[tid] = result
        self._cached.move_to_end(tid)
        while len(self._cached) > 256 or sum(
                len(data) for row in self._cached.values() for _, data in row["choices"]) > 16 * 1024**2:
            self._cached.popitem(last=False)

    def _scan(self, _checked=False, *, fresh=False):
        if self._preparing or self.provider is None or self._tasks.stopping:
            return
        rows = list(self._rows.values())
        if not rows:
            self.status.setText("This list has been checked this session. Open All to check other covers.")
            return
        self._preparing = True
        self._failures = 0
        self._checked.clear()
        self._saved_count = self._reviewed_count = 0
        if fresh:
            self._cached.clear()
            self._metadata.clear()
            self._suggested.clear()
            self._dismissed.clear()
        if not self._suggested - self._dismissed:
            self._views.setCurrentWidget(self._loading_page)
            self._loading_spinner.set_running(True)
        self._inline_spinner.set_running(True)
        self._controls()
        self.status.setText("Restoring saved review progress… Your covers stay unchanged.")
        self._loading_progress.setText("Restoring saved checks and decisions…")
        self._library_progress.setText("Restoring saved checks and decisions…")
        worker = _ReviewScan(self.cache, self.provider, rows, fresh=fresh)
        worker.scanned.connect(self._scanned)
        worker.restored.connect(self._scan_restored)
        worker.progress.connect(self._scan_progress)
        self._tasks.adopt(worker, self._scan_retired)
        self._prepare_worker = worker
        worker.start()

    def _scanned(self, tid, result):
        if self._tasks.stopping:
            return
        if "error" in result:
            self._failures += 1
            return  # failed searches remain eligible for retry and are never cached as misses
        self._checked.add(tid)
        self._metadata[tid] = dict(entry=result["entry"], digest=result["digest"],
                                   candidates=[c for c, _ in result["choices"]])
        if not result.get("reused") or any(data for _, data in result["choices"]):
            self._remember(tid, result)
        if result["choices"]:
            if tid not in self._dismissed:
                self._suggested.add(tid)
        else:
            self._suggested.discard(tid)
        if not self._list_refresh.isActive():
            self._list_refresh.start()
        if self._row and self._row[0] == tid and not self._busy:
            self._selected(self.tracks.currentItem())

    def _scan_restored(self, reused, kept, excluded, pending):
        if self._tasks.stopping:
            return
        self._saved_count = reused + kept + excluded
        self._reviewed_count = kept
        self._scan_progress(0, pending)
        self._filter()

    def _scan_progress(self, i, n):
        if not self._tasks.stopping:
            self._last_progress = (i, n)
            saved = f"{self._saved_count:,} saved checks · {self._reviewed_count:,} reviewed"
            work = f"Checking remaining covers: {i:,} / {n:,}" if n else "No remaining checks"
            text = f"{saved} · {work} · {len(self._suggested - self._dismissed):,} suggestions"
            self._loading_progress.setText(text)
            self._library_progress.setText(text)

    def _scan_retired(self, worker):
        if self._tasks.stopping:
            return
        self._preparing = False
        self._prepare_worker = None
        self._inline_spinner.set_running(False)
        self._list_refresh.stop()
        self.stop_scan.hide()
        self._filter()
        if self._row and not self._busy:
            self._selected(self.tracks.currentItem())
        if worker.error:
            self.status.setText(f"Could not finish checking: {worker.error}. Your covers are unchanged.")
            if not self._suggested:
                self._show_empty(self.status.text())
        elif worker._stop:
            self._generation += 1  # cancellation must not start a deferred selection search
            self.status.setText("Check stopped. Completed suggestions remain; no covers changed.")
        else:
            self.status.setText(f"Library check finished: {len(self._checked):,} checked, {worker.kept:,} already reviewed, "
                                f"{self._failures:,} could not complete. Choose Keep or Replace.")
            if not self._suggested - self._dismissed:
                self._show_empty(self.status.text())
        self._controls()

    def _stop_scan(self):
        if self._prepare_worker:
            self._prepare_worker.stop()
        if isinstance(self._worker, (_ReviewScan, _CandidateSearch, _ReviewPreview)):
            self._worker.stop()
            self.status.setText("Stopping after the current request. Completed suggestions stay available.")

    def _candidate_selected(self, item, _old=None):
        if item:
            cand, data = item.data(Qt.UserRole)
            self._show_image(self.proposed, data)
            evidence = "From your public likes" if cand.get("liked") else "General SoundCloud search"
            if cand.get("possible"):
                evidence = cand.get("reason") or "Possible match — please compare"
            if cand.get("version_upload"):
                evidence += " · Uploader matches the credited version artist"
            elif cand.get("artist_upload"):
                evidence += " · Uploader name matches the artist"
            if cand.get("excerpt"):
                evidence += " · Artwork from a clip/preview upload"
            self.evidence.setText(f"{cand['title']}\n{cand['uploader']} · {evidence}\n{cand['url']}")
            self.options.blockSignals(True)
            self.options.setCurrentIndex(self.candidates.currentRow())
            self.options.blockSignals(False)
        self._controls()

    def _manage_exclusions(self):
        from .uploader_exclusions import UploaderExclusionsDialog
        item = self.candidates.currentItem()
        candidate = item.data(Qt.UserRole)[0] if item is not None else {}
        observed = set()
        for metadata in self._metadata.values():
            for value in metadata["candidates"]:
                if excluded_source(value.get("uploader", ""), value.get("url", "")):
                    if handle := account_name(value.get("url", "")):
                        observed.add(handle)
        dialog = UploaderExclusionsDialog(self.cache, self, selected_url=candidate.get("url", ""), observed=observed)
        dialog.changed.connect(self._exclusions_updated)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _exclusions_updated(self):
        if self._tasks.stopping:
            return
        self.exclusions_changed.emit()
        exclusions = self.cache.source_exclusions()
        def allowed(candidate):
            return not excluded_source(candidate.get("uploader", ""), candidate.get("url", ""),
                                       excluded_accounts=exclusions)
        # Keep unrelated ready comparisons and dismissals intact while removing
        # only sources the user just blocked. Publication checks exclusions again.
        for tid, metadata in self._metadata.items():
            metadata["candidates"] = [c for c in metadata["candidates"] if allowed(c)]
            if not metadata["candidates"]:
                self._suggested.discard(tid)
        for value in self._cached.values():
            value["choices"] = [(c, data) for c, data in value["choices"] if allowed(c)]
        self._filter()
        if self._row and not self._busy:
            self._selected(self.tracks.currentItem())

    def _approve(self):
        if self._busy or not self.use.isEnabled():
            return
        cand, _ = self.candidates.currentItem().data(Qt.UserRole)
        # Replace is the explicit individual approval. A second modal would
        # merely repeat the comparison the user has already just approved.
        self._start_replace(cand)

    def _start_replace(self, cand):
        if self._tasks.stopping or self._busy or not self._snapshot:
            return
        worker = _ReviewFetch(self.provider, cand)
        self._worker = worker
        self._busy = True
        self._controls()
        self.status.setText("Fetching the approved cover…")
        tid, expected = self._row[0], self._snapshot
        self._tasks.adopt(worker, lambda w: self._apply_retired(w, tid, expected))
        worker.start()

    def _choose_other(self):
        if self._busy or not self._row:
            return
        _, artist, title, _ = self._row
        dialog = ArtPickerDialog(artist, title, self, provider=self.provider)
        try:
            dialog.exec()
        finally:
            try:
                dialog.shutdown()
                choice = deepcopy(dialog.choice)
            finally:
                dialog.deleteLater()
        if self._tasks.stopping or not choice:
            return
        self._start_replace(choice["cand"] if choice["kind"] == "thumb" else choice)

    def _use_profile(self):
        if self._tasks.stopping or not self._profile_action.isEnabled() or not self._row:
            return
        tid, artist, _, _ = self._row
        expected = self._snapshot
        worker = _ReviewFetch(self.provider, dict(kind="artist-profile", artist=artist))
        self._worker = worker
        self._busy = True
        self._controls()
        self.status.setText(f"Finding the artist profile for {artist}… Your cover stays unchanged.")
        self._tasks.adopt(worker, lambda w: self._profile_retired(w, tid, artist, expected))
        worker.start()

    def _profile_retired(self, worker, tid, artist, expected):
        if self._tasks.stopping:
            return
        self._worker = None
        self._busy = False
        self._controls()
        result = worker.result
        if not self._row or self._row[0] != tid:
            self.status.setText("Selection changed. Your cover is unchanged.")
            return
        if result is None:
            self.status.setText(worker.error or "No matching artist profile found. Your cover is unchanged.")
            return
        if excluded_source(url=result.source_url, excluded_accounts=self.cache.source_exclusions()):
            self.status.setText("This artist profile is excluded. Your cover is unchanged.")
            return
        pix = QPixmap()
        if not pix.loadFromData(result.data):
            self.status.setText("The artist picture could not load. Your cover is unchanged.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Use artist profile?")
        box.setTextFormat(Qt.PlainText)
        box.setText(f"Use this artist picture for {artist}?")
        box.setInformativeText(f"{result.source_url}\nYour original picture is kept for recovery.")
        ratio=box.devicePixelRatioF()
        pix=pix.scaled(round(180*ratio),round(180*ratio),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        pix.setDevicePixelRatio(ratio)
        box.setIconPixmap(pix)
        approve = box.addButton("Use profile", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        try:
            box.exec()
            approved = box.clickedButton() is approve
        finally:
            box.deleteLater()
        if (not self._tasks.stopping and self._row and self._row[0] == tid
                and approved):
            self._apply_retired(worker, tid, expected)
        elif not self._tasks.stopping:
            self.status.setText("Artist picture canceled. Your cover is unchanged.")

    def _apply_retired(self, worker, tid, expected):
        if self._tasks.stopping:
            return
        self._busy = False
        self._worker = None
        try:
            if worker.result is None:
                raise ValueError(worker.error or "The suggested cover could not be downloaded.")
            row = self._rows[tid]
            self.cache.apply_review(tid, expected, worker.result, context=(row[1], row[2]))
        except (OSError, ValueError, KeyError) as exc:
            self.status.setText(f"Cover unchanged: {exc}")
            self._controls()
            return
        self.changed.emit(tid)
        self._last_changed = tid
        self._dismissed.add(tid)
        self._cached.pop(tid, None)
        self._next()
        if self._row and self._row[0] == tid:
            self._selected(self.tracks.currentItem())
        self._controls()
        self.status.setText("Cover replaced. The original picture is kept for recovery.")

    def _undo(self):
        if self._busy or not self.undo.isEnabled():
            return
        tid = self._last_changed or self._row[0]
        try:
            self.cache.undo_review(tid, self.cache.cover_snapshot(tid))
        except (OSError, ValueError, KeyError) as exc:
            self.status.setText(f"Cover unchanged: {exc}")
            return
        self.changed.emit(tid)
        self._last_changed = None
        self._cached.pop(tid, None)
        self._dismissed.discard(tid)
        self._suggested.add(tid)
        self._filter()
        if self._row and self._row[0] == tid:
            self._selected(self.tracks.currentItem())
        self.status.setText("Original cover and its recorded history restored exactly.")

    def _next(self):
        if self._row and self.tabs.currentIndex() == 0:
            self._advance_after = self.tracks.currentRow()
            self._dismissed.add(self._row[0])
            self._filter()
            self._controls()
            self.status.setText("Current cover kept. Dismissed from Suggested for this session.")
            return
        for row in range(self.tracks.currentRow() + 1, self.tracks.count()):
            if not self.tracks.item(row).isHidden():
                self.tracks.setCurrentRow(row)
                self.status.setText("Current cover kept. Checking the next cover automatically.")
                return
        self.status.setText("End of this list. No cover was changed by moving through tracks.")

    def _keep(self):
        if not self.keep.isEnabled() or not self._row:
            return
        tid, artist, title, _ = self._row
        try:
            self.cache.keep_review(tid, self._snapshot, artist, title)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self._dismissed.add(tid)
        self._next()
        self.status.setText("Current cover kept. It will stay out of Suggested while this cover and artist stay unchanged.")

    def done(self, result):
        self.audition.shutdown()
        self._loading_spinner.set_running(False)
        self._inline_spinner.set_running(False)
        self._list_refresh.stop()
        self._tasks.stop()
        super().done(result)

    def closeEvent(self, event):
        self.audition.shutdown()
        self._loading_spinner.set_running(False)
        self._inline_spinner.set_running(False)
        self._list_refresh.stop()
        self._tasks.stop()
        super().closeEvent(event)

    def shutdown(self):
        self.audition.shutdown()
        self._tasks.drain()
