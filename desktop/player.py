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


def qt_peaks(path: str, buckets: int | None = None) -> list[float]:
    """Waveform peaks via QAudioDecoder — the SAME ffmpeg backend that plays
    the file, so anything audible gets a waveform (ALAC/AAC .m4a included,
    which libsndfile can't decode). Output is normalized exactly like
    soundbox.waveform.peaks, so the bar looks identical either way.

    Must run OFF the UI thread (spins a local event loop). [] on failure."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
    import numpy as np
    from soundbox.waveform import DEFAULT_BUCKETS
    buckets = buckets or DEFAULT_BUCKETS

    dec = QAudioDecoder()
    fmt = QAudioFormat()
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    fmt.setChannelCount(1)
    fmt.setSampleRate(8000)  # peaks only — small, fast, plenty for 700 buckets
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(path)))

    loop = QEventLoop()
    minis: list[float] = []  # abs-max per 256-sample mini-block
    tail = np.zeros(0, dtype=np.float32)
    failed: list[str] = []

    def drain():
        nonlocal tail
        if QThread.currentThread().isInterruptionRequested():
            finish()  # app is shutting down — abandon the decode NOW
            return
        while dec.bufferAvailable():
            buf = dec.read()
            f = buf.format()
            n = buf.byteCount()
            try:
                raw = bytes(buf.constData())[:n]
            except AttributeError:
                raw = bytes(buf.data())[:n]
            sf = f.sampleFormat()
            if sf == QAudioFormat.SampleFormat.Float:
                a = np.frombuffer(raw, dtype=np.float32)
            elif sf == QAudioFormat.SampleFormat.Int16:
                a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif sf == QAudioFormat.SampleFormat.Int32:
                a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:  # UInt8 / unknown — decode as unsigned bytes
                a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
            ch = max(1, f.channelCount())
            a = np.abs(a.reshape(-1, ch)).max(axis=1) if ch > 1 else np.abs(a)
            a = np.concatenate([tail, a])
            keep = len(a) - (len(a) % 256)
            if keep:
                minis.extend(a[:keep].reshape(-1, 256).max(axis=1).tolist())
            tail = a[keep:]

    done: list[int] = []

    def finish():
        done.append(1)
        loop.quit()

    dec.bufferReady.connect(drain)
    dec.finished.connect(finish)
    dec.error.connect(lambda *_: (failed.append(dec.errorString()), finish()))
    QTimer.singleShot(20000, finish)  # never hang the worker on a bad file
    dec.start()
    # An unreadable/undecodable file reports its error SYNCHRONOUSLY inside
    # start() — before the loop runs. Exec'ing then would wait on a quit that
    # already happened and strand this thread until process exit, where Qt
    # aborts ("QThread: Destroyed while thread is still running").
    if not done and dec.error() == QAudioDecoder.Error.NoError:
        loop.exec()
    # stop() is asynchronous — destroying the decoder while its internal
    # ffmpeg thread winds down is the same exit abort. Wait it out (bounded).
    dec.stop()
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
    et = QElapsedTimer()
    et.start()
    while dec.isDecoding() and et.elapsed() < 2000:
        QCoreApplication.processEvents()
        QThread.msleep(5)
    drain()  # buffers that landed between the last bufferReady and finished
    if tail.size:
        minis.append(float(np.abs(tail).max()))
    if failed or not minis:
        return []
    m = np.asarray(minis, dtype=np.float32)
    edges = np.linspace(0, len(m), buckets + 1).astype(int)
    out = [float(m[a:b].max()) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    hi = max(out) or 1.0
    return [min(1.0, v / hi) for v in out]


def _thread_running(w) -> bool:
    """Safe isRunning() — False even if the C++ QThread was already deleted."""
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False


class WaveformWorker(QThread):
    """Decode a file's waveform peaks off the UI thread. Cache first (repeat
    plays are instant), then libsndfile (WAV/AIFF/MP3/FLAC — cheap), then the
    Qt/ffmpeg decoder for everything else (ALAC/AAC/…): if it plays, it draws."""
    ready = Signal(str, list)  # path, peaks

    def __init__(self, path: str, cache_dir=None):
        super().__init__()
        self.path = path
        self.cache_dir = cache_dir

    def run(self):
        from soundbox.waveform import cache_load, cache_store, peaks
        try:
            pk = cache_load(self.cache_dir, self.path) if self.cache_dir else None
            if pk is None:
                pk = peaks(self.path) or qt_peaks(self.path)
                if self.cache_dir:
                    cache_store(self.cache_dir, self.path, pk)
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
        if not self._peaks:  # flat line fallback (undecodable/corrupt file)
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
        self.wave_cache_dir = None  # app points this at <data>/waveforms
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
        # Strong refs to EVERY in-flight decode worker until it finishes — a
        # rapid track change starts a new one before the old returns, and
        # dropping the old Python ref mid-run GC's a running QThread → SIGABRT.
        self._wf_pending: list[WaveformWorker] = []

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
        # Interrupt the previous decode FIRST — rapid track changes must not pile
        # up multi-second qt_peaks (.m4a) threads. It polls interruption and
        # abandons; its stale result is ignored by _on_waveform's path guard.
        if self._wf is not None and _thread_running(self._wf):
            self._wf.requestInterruption()
        # Cache hit → paint on the UI thread, no worker at all. With the
        # background fill pre-caching everything, rapid-fire loads stay smooth
        # (a small JSON read, ~sub-ms) and spawn zero decode threads.
        if self.wave_cache_dir:
            from soundbox.waveform import cache_load
            pk = cache_load(self.wave_cache_dir, path)
            if pk is not None:
                self._wf = None
                self.wave.set_peaks(pk)
                return
        w = WaveformWorker(path, cache_dir=self.wave_cache_dir)
        self._wf = w
        self._wf_pending.append(w)  # strong ref until finished (never GC mid-run)
        w.ready.connect(self._on_waveform)
        w.finished.connect(lambda ww=w: self._wf_done(ww))
        w.start()

    def _wf_done(self, w):
        if self._wf is w:
            self._wf = None
        try:
            self._wf_pending.remove(w)
        except ValueError:
            pass
        try:
            w.deleteLater()
        except RuntimeError:
            pass

    def shutdown_waveforms(self):
        """Interrupt + wait every in-flight decode before teardown (no 'QThread
        destroyed while running')."""
        for w in list(self._wf_pending):
            try:
                if _thread_running(w):
                    w.requestInterruption(); w.wait(5000)
            except RuntimeError:
                pass

    def _on_waveform(self, path: str, peaks: list):
        if path == self._src_path:  # ignore stale results from a track we've left
            self.wave.set_peaks(peaks)
