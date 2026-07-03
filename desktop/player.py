"""In-app audio player + real-waveform scrubber for the top bar.

Playback via QtMultimedia (WAV/AIFF/MP3/FLAC/M4A through the OS codecs). The
scrubber shows the track's actual waveform (decoded off the UI thread by
WaveformWorker → soundbox.waveform); played portion is highlighted; click to seek.
Plays local library files only — never writes anything.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal, QThread, QUrl
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

_PLAYED = QColor("#A855F7")          # Freq purple — played portion
_REST = QColor(255, 255, 255, 55)    # dim — unplayed portion
_HEAD = QColor(255, 255, 255, 200)   # playhead line
_TITLE_BG = QColor(8, 8, 12, 165)    # soft dark pill behind the now-playing title
_TITLE_FG = QColor(232, 232, 236, 235)


def _fmt(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


class WaveformWorker(QThread):
    """Decode a file's waveform peaks off the UI thread."""
    ready = Signal(str, list)  # path, peaks

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        from soundbox.waveform import peaks
        try:
            pk = peaks(self.path)
        except Exception:  # noqa: BLE001
            pk = []
        self.ready.emit(self.path, pk)


class WaveformBar(QWidget):
    """Flat real-waveform strip that doubles as the seek bar."""
    seeked = Signal(float)  # 0..1

    def __init__(self):
        super().__init__()
        self._peaks: list[float] = []
        self._pos = 0.0  # 0..1
        self._title = ""  # now-playing label painted over the waveform
        self._active = False  # no track loaded yet → scrubbing disabled
        self.setMinimumHeight(44)

    def set_active(self, on: bool):
        """Arm/disarm scrubbing. Until a track is loaded there is nothing to
        seek in — clicks are ignored and the cursor stays a plain arrow."""
        self._active = on
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)

    def set_peaks(self, peaks: list[float]):
        self._peaks = peaks or []
        self.update()

    def set_title(self, title: str):
        self._title = title or ""
        self.update()

    def set_position(self, frac: float):
        self._pos = min(1.0, max(0.0, frac))
        self.update()

    def _seek_to_x(self, x: float):
        if self.width() > 0:
            frac = min(1.0, max(0.0, x / self.width()))
            self._pos = frac  # move the playhead immediately — no lag while dragging
            self.update()
            self.seeked.emit(frac)

    def mousePressEvent(self, ev):
        if self._active:
            self._seek_to_x(ev.position().x())

    def mouseMoveEvent(self, ev):
        # Click-and-drag scrubbing: hold and slide to hunt for the exact spot.
        if self._active and ev.buttons() & Qt.LeftButton:
            self._seek_to_x(ev.position().x())

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        mid = h / 2
        px = int(self._pos * w)
        if not self._peaks:  # flat line fallback (e.g. ALAC/.m4a)
            p.setPen(_REST); p.drawLine(0, int(mid), w, int(mid))
            p.setPen(_PLAYED); p.drawLine(0, int(mid), px, int(mid))
        else:
            n = len(self._peaks)
            for x in range(w):
                bar = self._peaks[min(n - 1, int(x / w * n))] * (mid - 2)
                p.setPen(_PLAYED if x <= px else _REST)
                p.drawLine(x, int(mid - bar), x, int(mid + bar))
        p.setPen(_HEAD); p.drawLine(px, 0, px, h)
        # Now-playing title, centered in a soft dark pill so it stays readable
        # over both the purple (played) and dim (unplayed) halves.
        if self._title:
            fm = QFontMetrics(p.font())
            text = fm.elidedText(self._title, Qt.ElideRight, max(40, w - 56))
            tw, th = fm.horizontalAdvance(text), fm.height()
            pill = QRectF((w - tw) / 2 - 9, mid - th / 2 - 2, tw + 18, th + 4)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(_TITLE_BG)
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            p.setPen(_TITLE_FG)
            p.drawText(pill, Qt.AlignCenter, text)


class PlayerBar(QWidget):
    """Play button + waveform scrubber + time, wired to a QMediaPlayer."""

    playback_error = Signal(str)  # surfaced by the app's status bar

    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.9)
        # QAudioOutput binds to the DEFAULT output device at construction and
        # does NOT follow the system when it changes (AirPods connect, display
        # audio, a DAC unplugs) — playback then goes silently to a dead device
        # until relaunch. Track device changes and rebind live. The QMediaDevices
        # INSTANCE must be kept alive or its change signals never fire (Qt quirk).
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._rebind_audio_device)
        self.player.errorOccurred.connect(self._on_error)
        self._track_id: str | None = None
        self._src_path: str | None = None
        self._dur = 0
        self._wf: WaveformWorker | None = None

        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("PlayBtn")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self.toggle)
        self.wave = WaveformBar()
        self.wave.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)  # eat all slack
        self.time = QLabel("0:00 / 0:00"); self.time.setObjectName("PlayerTime")
        # Left-aligned + width sized tight to THIS track's time (set in _on_dur) so
        # there's no internal slack and the digits can't shimmer the waveform.
        self.time.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._size_time()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(0)  # gaps set manually so play↔wave can be tighter than wave↔time
        lay.addWidget(self.play_btn, 0)
        lay.addSpacing(9)   # play → waveform (a touch closer)
        lay.addWidget(self.wave, 1)
        lay.addSpacing(16)  # waveform → time
        lay.addWidget(self.time, 0)

        self.wave.seeked.connect(self._seek)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)

    # ---- public API ----
    def current_track(self) -> str | None:
        return self._track_id

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def play_track(self, track_id: str, path, title: str = ""):
        path = str(path)
        self._rebind_audio_device()  # belt & braces: never play into a dead device
        if track_id != self._track_id or path != self._src_path:
            self._track_id = track_id
            self._src_path = path
            self.player.setSource(QUrl.fromLocalFile(path))
            self.wave.set_peaks([]); self.wave.set_position(0.0)
            self._start_waveform(path)
        self.wave.set_title(title)  # always — the label may improve after an edit
        self.wave.set_active(True)  # a track is loaded — scrubbing is now live
        self.player.play()

    def _rebind_audio_device(self):
        """Point our output at the CURRENT system default (see __init__ note)."""
        dev = QMediaDevices.defaultAudioOutput()
        if not dev.isNull() and self.audio.device() != dev:
            self.audio.setDevice(dev)

    def _on_error(self, _err, err_string: str):
        self.playback_error.emit(err_string or "Playback error")

    def toggle(self):
        if self.is_playing():
            self.player.pause()
        elif self._src_path:
            self.player.play()

    def stop(self):
        self.player.stop()

    # ---- internals ----
    def _seek(self, frac: float):
        if self._dur > 0:
            self.player.setPosition(int(frac * self._dur))

    def _on_pos(self, ms: int):
        if self._dur > 0:
            self.wave.set_position(ms / self._dur)
        self.time.setText(f"{_fmt(ms)} / {_fmt(self._dur)}")

    def _size_time(self, ms: int = 0):
        # Fit the label exactly to "M:SS / M:SS" for this track — no slack, no jitter
        # (position never has more digits than the duration).
        d = _fmt(ms)
        self.time.setFixedWidth(self.time.fontMetrics().horizontalAdvance(f"{d} / {d}") + 2)

    def _on_dur(self, ms: int):
        self._dur = ms
        self._size_time(ms)

    def _on_state(self, _st):
        self.play_btn.setText("❚❚" if self.is_playing() else "▶")

    def _start_waveform(self, path: str):
        w = WaveformWorker(path)
        self._wf = w
        w.ready.connect(self._on_waveform)
        # Drop our ref only on `finished` (after run() returns) so the QThread is
        # never GC'd on its own thread, and we never touch a deleted C++ object.
        w.finished.connect(lambda ww=w: self._wf_done(ww))
        w.start()

    def _wf_done(self, w):
        if self._wf is w:
            self._wf = None
        try:
            w.deleteLater()
        except RuntimeError:
            pass

    def _on_waveform(self, path: str, peaks: list):
        if path == self._src_path:  # ignore stale results from a track we've left
            self.wave.set_peaks(peaks)
