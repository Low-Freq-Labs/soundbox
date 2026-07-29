"""In-app transport + read-only Rekordbox/audio waveform deck.

PlayerBar uses the native CoreAudio/Metal backend. Qt Multimedia is retained
only for QAudioDecoder so AAC/ALAC files can receive the same canonical V4
analysis as formats handled by libsndfile. Plays local library files only and
never writes Rekordbox analysis or source audio.

See ``docs/PLAYER.md`` for interaction semantics, architecture, renderer
invariants, validation references, and the cue/grid roadmap.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right
from enum import Enum
from math import ceil, exp, floor
import sys
from time import perf_counter

from PySide6.QtCore import (
    QElapsedTimer, QPointF, QRectF, QSize, Qt, Signal, QThread, QTimer, QUrl,
)
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QMenu, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)
from .native_deck import (
    NativeGainControl, NativeDeckBackend, PlaybackState,
    UnavailableDeckBackend,
)

_PLAYED = QColor("#A855F7")          # Freq purple — played portion
_REST = QColor(255, 255, 255, 55)    # dim — unplayed portion
_HEAD = QColor(255, 255, 255, 200)   # playhead line
_WAVE_BG = QColor("#0d0e12")
_PIXELS_PER_SECOND = 92.0
_OVERVIEW_HEIGHT = 28
_RULER_TOP = 31
_ZOOM_TOP = 49
_HEIGHT_ENCODING_U16 = "linear-u16le"
_WAVEFORM_DISPLAY_EXPONENT = 1.4
_WAVEFORM_DISPLAY_SCALE = 0.84
_WAVEFORM_VISIBILITY_SHOULDER = 1.0 / 128.0
_WAVEFORM_VISIBILITY_KNEE = 4.0 / 65535.0
_WAVEFORM_MODES = ("BLUE", "RGB", "3BAND")
# The last stop is intentionally close to Rekordbox's maximum: at the
# 140–175 BPM range common in this library, a typical detail deck shows about
# three beats across its full width. These are renderer scales, not rebuilt
# caches, so switching levels remains instant.
_ZOOM_LEVELS = (
    24.0, 32.0, 48.0, 64.0, 92.0, 128.0, 184.0, 256.0,
    384.0, 576.0, 864.0, 1152.0, 1536.0,
)
_HOT_CUE_FALLBACKS = (
    "#ff376f", "#45acdb", "#7dc13d", "#aa72ff",
    "#30d26e", "#e0641b", "#305aff", "#c3af04",
)
_MEMORY_CUE_COLOR = QColor("#ff3434")


def _display_waveform_height(linear: float) -> float:
    """Map current linear geometry to a visible, continuous display height."""
    value = max(0.0, min(1.0, float(linear)))
    if value == 0.0:
        return 0.0
    companded = value ** _WAVEFORM_DISPLAY_EXPONENT
    visibility = 1.0 - exp(-value / _WAVEFORM_VISIBILITY_KNEE)
    return min(
        1.0,
        _WAVEFORM_DISPLAY_SCALE * (
            companded
        + _WAVEFORM_VISIBILITY_SHOULDER
        * (1.0 - companded) * visibility),
    )
_SESSION_CUE_COLOR = QColor("#f08a19")

_JUMP_MODES = {
    "4 BEATS": 4,
    "8 BEATS": 8,
    "16 BEATS": 16,
    "4 BARS": 16,
    "8 BARS": 32,
    "16 BARS": 64,
}


def _track_nav_icon(direction: int) -> QIcon:
    """Crisp previous/next glyphs rendered as exact mirrored twins."""
    pixmap = QPixmap(36, 28)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#F5F6FA"))
    # Draw the approved previous-track shape once. Pixel-mirroring the finished
    # raster guarantees that the next-track icon cannot drift by a point or
    # acquire a differently antialiased edge.
    painter.drawPolygon(QPolygonF([
        QPointF(3.0, 7.0), QPointF(10.5, 2.5), QPointF(10.5, 11.5),
    ]))
    painter.drawRoundedRect(QRectF(12.0, 2.5, 2.0, 9.0), 0.7, 0.7)
    painter.end()
    if direction > 0:
        image = pixmap.toImage().flipped(Qt.Horizontal)
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


def _fmt(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _rasterize_rgb(heights: list, colors: list, sample_rate: float,
                   pixels_per_second: float = _PIXELS_PER_SECOND) -> list:
    """Downsample PWV5 once into stable, song-anchored display columns.

    The old renderer chose a peak from a moving sample window for every screen
    pixel on every frame. At 150 source samples/sec that made the chosen color
    change as the fractional window moved, producing visible shimmer. These
    bins are anchored to absolute song time, so playback only translates them.
    """
    n = min(len(heights), len(colors))
    if not n or sample_rate <= 0 or pixels_per_second <= 0:
        return []
    count = ceil(n * pixels_per_second / sample_rate)
    columns = []
    for column in range(count):
        start = int(column * sample_rate / pixels_per_second)
        end = min(n, max(start + 1, int(
            (column + 1) * sample_rate / pixels_per_second)))
        peak = max(range(start, end), key=heights.__getitem__)
        columns.append([float(heights[peak]), list(colors[peak])])
    return columns


def _apply_loaded_grid_contract(
        data: dict, analysis_path: str = "") -> dict:
    """Zero-anchor a legacy fallback before its bulk migration completes.

    File type is irrelevant. A valid relevant Rekordbox grid remains exact;
    any track without one uses SoundBox's fallback with downbeat 1 at 0:00.
    """
    if not data or analysis_path:
        return data
    provenance = dict(data.get("provenance") or {})
    if provenance.get("grid") in {
            "rekordbox", "soundbox-zero-anchor", "soundbox-manual"}:
        return data
    current = list(data.get("beats") or [])
    if not current:
        return data
    try:
        first = int(current[0][1])
    except (IndexError, TypeError, ValueError):
        return data
    if first == 0:
        return data
    beats = []
    for row in current:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        migrated = list(row)
        try:
            migrated[1] = max(0, int(migrated[1]) - first)
        except (TypeError, ValueError):
            continue
        beats.append(migrated)
    if not beats:
        return data
    return {
        **data,
        "beats": beats,
        "provenance": {
            **provenance,
            "grid": "soundbox-zero-anchor",
            "owner": "soundbox",
            "rekordbox_runtime_dependency": False,
        },
    }


def qt_detail_peaks(path: str, checkpoint=None) -> dict | None:
    """Full V4 analysis through Qt/FFmpeg for AAC/ALAC and friends.

    Signed stereo PCM is fed to the same streaming analyzer as WAV/AIFF/
    MP3/FLAC, so player-only set rips retain channel-safe waveform geometry,
    24-band RGB, and beat-grid data. They remain explicitly ineligible for CDJ
    export.
    """
    from PySide6.QtCore import (
        QCoreApplication, QElapsedTimer, QEventLoop, QTimer,
    )
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
    import numpy as np
    from soundbox.track_analysis import StreamingAnalyzer

    dec = QAudioDecoder()
    fmt = QAudioFormat()
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    fmt.setChannelCount(2)
    # 24,576 Hz gives the ultra tier exactly four PCM samples per 6,144 Hz
    # envelope row while retaining enough bandwidth for useful RGB analysis.
    fmt.setSampleRate(24576)
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(path)))

    loop = QEventLoop()
    failed: list[str] = []
    done: list[int] = []
    analyzer: list[StreamingAnalyzer] = []
    pending: list = []
    pending_samples = [0]
    drain_scheduled = [False]
    chunk_samples = 24_576

    def finish():
        if not done:
            done.append(1)
        loop.quit()

    def feed_pending(force: bool = False):
        if not pending or (not force and pending_samples[0] < chunk_samples):
            return True
        if checkpoint is not None and not checkpoint():
            finish()
            return False
        values = (
            pending[0] if len(pending) == 1
            else np.concatenate(pending))
        pending.clear()
        pending_samples[0] = 0
        if not analyzer:
            analyzer.append(StreamingAnalyzer(24576.0))
        analyzer[0].feed(values, owned=True)
        return checkpoint is None or checkpoint()

    def drain():
        drain_scheduled[0] = False
        if QThread.currentThread().isInterruptionRequested():
            finish()
            return
        if checkpoint is not None and not checkpoint():
            finish()
            return
        # Consume at most one analysis chunk per event-loop turn. This bounds
        # GIL ownership and lets playback, cancellation, and power-policy
        # changes preempt an hour-long AAC/ALAC decode.
        while dec.bufferAvailable() and pending_samples[0] < chunk_samples:
            buf = dec.read()
            f = buf.format()
            n = buf.byteCount()
            try:
                raw = bytes(buf.constData())[:n]
            except AttributeError:
                raw = bytes(buf.data())[:n]
            sf = f.sampleFormat()
            if sf == QAudioFormat.SampleFormat.Float:
                values = np.frombuffer(raw, dtype=np.float32)
            elif sf == QAudioFormat.SampleFormat.Int16:
                values = (
                    np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            elif sf == QAudioFormat.SampleFormat.Int32:
                values = (
                    np.frombuffer(raw, dtype=np.int32).astype(np.float32)
                    / 2147483648.0
                )
            else:
                values = (
                    np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128
                ) / 128.0
            channels = max(1, f.channelCount())
            if channels > 1:
                values = values.reshape(-1, channels)
            if not analyzer:
                analyzer.append(StreamingAnalyzer(
                    float(f.sampleRate() or 24576)))
            pending.append(np.asarray(values, dtype=np.float32))
            pending_samples[0] += len(values)
        if not feed_pending():
            return
        if dec.bufferAvailable() and not drain_scheduled[0]:
            drain_scheduled[0] = True
            QTimer.singleShot(0, drain)

    def decoder_finished():
        drain()
        feed_pending(force=True)
        finish()

    dec.bufferReady.connect(drain)
    dec.finished.connect(decoder_finished)
    dec.error.connect(lambda *_: (failed.append(dec.errorString()), finish()))
    # Set rips can be hours long. Shutdown interruption and synchronous decoder
    # errors are the real cancellation paths; this is only a last-resort guard
    # against a backend that neither finishes nor errors.
    QTimer.singleShot(6 * 60 * 60 * 1000, finish)
    dec.start()
    if not done and dec.error() == QAudioDecoder.Error.NoError:
        loop.exec()
    dec.stop()
    elapsed = QElapsedTimer()
    elapsed.start()
    while dec.isDecoding() and elapsed.elapsed() < 2000:
        QCoreApplication.processEvents()
        QThread.msleep(5)
    drain()
    feed_pending(force=True)
    if failed or not analyzer:
        return None
    result = analyzer[0].finish()
    if not result:
        return None
    return result


def _thread_running(w) -> bool:
    """Safe isRunning() — False even if the C++ QThread was already deleted."""
    try:
        return w is not None and w.isRunning()
    except RuntimeError:
        return False


class WaveformWorker(QThread):
    """Build or load one canonical SoundBox V4 analysis record."""

    manifest_ready = Signal(str, int, dict)
    overview_ready = Signal(str, int, dict)
    detail_ready = Signal(str, int, dict)

    def __init__(self, path: str, analysis_path: str = "",
                 track_id: str = "", track_analysis_dir=None,
                 generation: int = 0, native_backend=None):
        super().__init__()
        self.path = path
        self.analysis_path = analysis_path
        self.track_id = track_id
        self.track_analysis_dir = track_analysis_dir
        self.generation = int(generation)
        self.native_backend = native_backend

    def _load_stages(self) -> tuple[bool, bool, bool]:
        from soundbox.track_analysis import (
            load_detail, load_manifest, load_overview,
        )

        manifest = load_manifest(
            self.track_analysis_dir, self.track_id, self.path)
        if manifest is None:
            return False, False, False
        manifest = _apply_loaded_grid_contract(
            manifest, self.analysis_path)
        self.manifest_ready.emit(self.path, self.generation, manifest)
        if self.isInterruptionRequested():
            return True, False, False
        overview = load_overview(
            self.track_analysis_dir, self.track_id, self.path)
        if self.isInterruptionRequested():
            return True, overview is not None, False
        detail = load_detail(
            self.track_analysis_dir, self.track_id, self.path)
        if overview is not None and self.native_backend is not None:
            combined = {**manifest, **overview}
            if (detail is not None
                    and hasattr(
                        self.native_backend, "set_complete_analysis")):
                overview_ok, detail_ok = (
                    self.native_backend.set_complete_analysis(
                        combined, detail, self.generation))
                if overview_ok:
                    overview["_native_uploaded"] = True
                if detail_ok:
                    detail["_native_uploaded"] = True
            elif self.native_backend.set_overview(
                    combined, self.generation):
                overview["_native_uploaded"] = True
        if overview is not None:
            self.overview_ready.emit(
                self.path, self.generation, overview)
        if detail is not None:
            if (self.native_backend is not None
                    and not detail.get("_native_uploaded")):
                self.native_backend.set_audio_detail(
                    detail.get("_audio_detail"), self.generation)
                detail["_native_uploaded"] = True
            self.detail_ready.emit(self.path, self.generation, detail)
        return True, overview is not None, detail is not None

    def run(self):
        try:
            if not self.track_analysis_dir or not self.track_id:
                self.manifest_ready.emit(self.path, self.generation, {})
                return
            from soundbox.track_analysis import (
                analyze_soundfile, merge_import, store as track_store,
            )
            present = self._load_stages()
            if not all(present):
                checkpoint = lambda: not self.isInterruptionRequested()
                native = (
                    analyze_soundfile(self.path, checkpoint=checkpoint)
                    or qt_detail_peaks(self.path, checkpoint=checkpoint)
                )
                if self.isInterruptionRequested():
                    return
                imported = None
                if self.analysis_path:
                    from soundbox.rekordbox_analysis import read
                    imported = read(self.analysis_path)
                prepared = merge_import(native, imported)
                if prepared:
                    track_store(
                        self.track_analysis_dir, self.track_id,
                        self.path, prepared)
                    if not self.isInterruptionRequested():
                        self._load_stages()
        except Exception:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.manifest_ready.emit(self.path, self.generation, {})


class SessionCueState(str, Enum):
    UNSET = "unset"
    SET = "set"
    RELOCATION_PENDING = "relocation_pending"
    PREVIEWING = "previewing"
    LATCHED_PLAYBACK = "latched_playback"


class WaveformBar(QWidget):
    """Zoomed scrolling waveform with rekordbox RGB detail and beat grid."""
    seeked = Signal(float)  # 0..1
    previewed = Signal(float)  # live drag timestamp; decoder still seeks once
    drag_started = Signal()
    drag_finished = Signal()
    zoom_changed = Signal(float)
    PIXELS_PER_SECOND = _PIXELS_PER_SECOND

    def __init__(self):
        super().__init__()
        self._native_rendering = False
        self._peaks: list[float] = []
        self._pos = 0.0  # 0..1
        self._position_ms = 0
        self._duration_ms = 0
        self._rgb_heights: list[float] = []
        self._rgb_colors: list[list[int]] = []
        self._rgb_rate = 150.0
        self._rgb_columns: list[tuple[float, QColor]] = []
        self._rgb_detail: list[tuple[float, QColor]] = []
        self._audio_detail = b""
        self._audio_detail_rate = 0.0
        self._audio_detail_scale = 255.0
        self._audio_detail_companded = False
        self._beats: list[list[float]] = []  # [beat-in-bar, position-ms, bpm]
        self._beat_times: list[int] = []
        self._beat_positions: list[tuple[int, int, int]] = []
        self._cues: list[dict] = []
        self._session_cue_ms: int | None = None
        self._overview_cache_width = 0
        self._overview_columns: list[tuple[float, QColor]] = []
        self._active = False  # no track loaded yet → scrubbing disabled
        self._overview_drag = False
        self._zoom_level = _ZOOM_LEVELS.index(_PIXELS_PER_SECOND)
        self._drag_origin_x = 0.0
        self._drag_origin_ms: float | None = None
        self._drag_preview_ms: float | None = None
        self._drag_moved = False
        # paintEvent fills every pixel. Declaring that explicitly prevents the
        # previous translucent waveform frame from surviving in the backing
        # store and visually blending with the next one.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        # Whole-track overview → ruler → zoomed waveform, each with its own
        # pixels so navigation never crowds the beat labels or detail view.
        self.setMinimumHeight(132)
        self.set_zoom(_PIXELS_PER_SECOND, emit=False)

    def set_native_rendering(self, active: bool):
        """Let the child Metal NSView own pixels while Qt keeps mouse input."""
        self._native_rendering = bool(active)
        self.update()

    def _repaint(self):
        """Repaint only when Qt actually owns these pixels.

        ``paintEvent`` early-returns while the Metal child view is rendering,
        so a Qt repaint composites the window and produces nothing. The
        transport calls ``set_timing`` twenty times a second, and that waste
        measured at roughly three dropped display frames per second at
        maximum zoom in the real app -- each one a doubled scroll step.
        Metal already draws the playhead, grid, cues, and zoom buttons.
        """
        if self._native_rendering:
            return
        self.update()

    def set_active(self, on: bool):
        """Arm/disarm scrubbing. Until a track is loaded there is nothing to
        seek in — clicks are ignored and the cursor stays a plain arrow."""
        self._active = on
        self.setCursor(Qt.OpenHandCursor if on else Qt.ArrowCursor)

    def set_zoom(self, pixels_per_second: float, emit: bool = True):
        """Select a stable detail scale; overview geometry never changes."""
        value = float(pixels_per_second)
        index = min(
            range(len(_ZOOM_LEVELS)),
            key=lambda i: abs(_ZOOM_LEVELS[i] - value))
        changed = index != self._zoom_level
        self._zoom_level = index
        self.PIXELS_PER_SECOND = _ZOOM_LEVELS[index]
        self._repaint()
        if emit and changed:
            self.zoom_changed.emit(self.PIXELS_PER_SECOND)

    def _step_zoom(self, direction: int):
        index = min(
            len(_ZOOM_LEVELS) - 1,
            max(0, self._zoom_level + (1 if direction > 0 else -1)))
        self.set_zoom(_ZOOM_LEVELS[index])

    @staticmethod
    def _zoom_button_at(x: float, y: float) -> int:
        if not (_RULER_TOP <= y < _ZOOM_TOP):
            return 0
        if 0 <= x < 26:
            return -1
        if 30 <= x < 56:
            return 1
        return 0

    def is_dragging(self) -> bool:
        """True while the user owns the visual playhead with the mouse."""
        return self._drag_origin_ms is not None

    def set_peaks(self, peaks: list[float]):
        self._peaks = peaks or []
        self._repaint()

    def set_analysis(self, data: dict):
        """Install read-only PQTZ/PWV5 data for the loaded track."""
        self.set_audio_detail(data.get("_audio_detail"))
        packed_heights = data.get("_overview_heights")
        packed_rgb = data.get("_overview_rgb")
        if (isinstance(packed_heights, bytes)
                and isinstance(packed_rgb, bytes)
                and self._native_rendering):
            # Metal already owns the packed overview. Keep the Qt input/model
            # side compact instead of expanding a long set into nested lists.
            self._rgb_heights = []
            self._rgb_colors = []
        elif (isinstance(packed_heights, bytes)
              and isinstance(packed_rgb, bytes)):
            encoding = str(data.get("_height_encoding") or "")
            if encoding == _HEIGHT_ENCODING_U16:
                values = array("H")
                values.frombytes(
                    packed_heights[:len(packed_heights) // 2 * 2])
                if sys.byteorder != "little":
                    values.byteswap()
                scale = 65535.0
            else:
                values = packed_heights
                scale = 255.0
            count = min(len(values), len(packed_rgb) // 3)
            self._rgb_heights = [
                (_display_waveform_height(value / scale)
                 if encoding == _HEIGHT_ENCODING_U16
                 else value / scale)
                for value in values[:count]]
            self._rgb_colors = [
                [packed_rgb[i], packed_rgb[i + 1], packed_rgb[i + 2]]
                for i in range(0, count * 3, 3)
            ]
        else:
            self._rgb_heights = list(data.get("heights") or [])
            self._rgb_colors = list(data.get("colors") or [])
        self._rgb_rate = float(data.get("sample_rate") or 150.0)
        prepared_detail = data.get("_detail_columns")
        if prepared_detail is None:
            prepared_detail = [
                (float(height), self._rgb_color(color))
                for height, color in zip(
                    self._rgb_heights, self._rgb_colors)
            ]
            bands = data.get("_detail_bands")
            if (isinstance(bands, bytes)
                    and len(bands) >= len(prepared_detail) * 3):
                prepared_detail = [
                    (
                        max(bands[i * 3] & 0x7f,
                            bands[i * 3 + 1] & 0x7f,
                            bands[i * 3 + 2] & 0x7f) / 127,
                        color,
                    )
                    for i, (_height, color) in enumerate(prepared_detail)
                ]
            data["_detail_columns"] = prepared_detail
        self._rgb_detail = prepared_detail
        prepared_columns = data.get("_display_columns")
        if prepared_columns is None:
            columns = data.get("columns")
            if columns is None:
                columns = _rasterize_rgb(
                    self._rgb_heights, self._rgb_colors, self._rgb_rate,
                    self.PIXELS_PER_SECOND)
            prepared_columns = [
                (float(row[0]), self._rgb_color(row[1]))
                for row in columns
                if isinstance(row, (list, tuple)) and len(row) >= 2
            ]
            # PlayerBar keeps a small eight-track LRU of these runtime dicts.
            # Retaining the Qt-ready columns there makes toggling among recent
            # tracks effectively free without bloating the persistent cache.
            data["_display_columns"] = prepared_columns
        self._rgb_columns = prepared_columns
        self._overview_cache_width = 0
        self._overview_columns = []
        self._beats = list(data.get("beats") or [])
        self.set_cues(list(data.get("cues") or []), repaint=False)
        prepared_times = data.get("_beat_times")
        if prepared_times is None:
            prepared_times = sorted(
                int(row[1]) for row in self._beats
                if isinstance(row, (list, tuple)) and len(row) >= 2
            )
            data["_beat_times"] = prepared_times
        self._beat_times = prepared_times
        self._beat_positions = []
        bar = 0
        for index, row in enumerate(self._beats):
            if len(row) < 2:
                continue
            beat = int(row[0])
            if beat not in (1, 2, 3, 4):
                beat = index % 4 + 1
            if beat == 1:
                bar += 1
            elif bar == 0:
                bar = 1
            self._beat_positions.append((int(row[1]), bar, beat))
        self._repaint()

    def set_cues(self, cues: list[dict], repaint: bool = True):
        """Install cue markers without changing waveform or grid ownership."""
        prepared = []
        for cue in cues or []:
            if not isinstance(cue, dict) or cue.get("kind") not in (
                    "hot", "memory"):
                continue
            try:
                position = max(0, int(cue.get("position_ms", 0)))
                slot = int(cue.get("slot", -1))
            except (TypeError, ValueError):
                continue
            if cue.get("kind") == "hot" and not 0 <= slot < 8:
                continue
            prepared.append({**cue, "position_ms": position, "slot": slot})
        self._cues = sorted(
            prepared,
            key=lambda cue: (
                int(cue["position_ms"]),
                0 if cue["kind"] == "hot" else 1,
                int(cue["slot"]),
            ),
        )
        if repaint:
            self._repaint()

    def set_session_cue(self, position_ms: int | None):
        """Show the loaded deck's transient CUE point without persisting it."""
        self._session_cue_ms = (
            None if position_ms is None else max(0, int(position_ms)))
        self._repaint()

    def set_audio_detail(self, detail):
        """Install an additive Soundbox-owned high-resolution envelope."""
        if isinstance(detail, dict):
            heights = detail.get("heights")
            rate = float(detail.get("rate") or 0)
            if isinstance(heights, bytes) and heights and rate > 0:
                encoding = str(detail.get("encoding") or "")
                if encoding == _HEIGHT_ENCODING_U16:
                    values = array("H")
                    values.frombytes(heights[:len(heights) // 2 * 2])
                    if sys.byteorder != "little":
                        values.byteswap()
                    self._audio_detail = values
                    self._audio_detail_scale = 65535.0
                    self._audio_detail_companded = True
                else:
                    self._audio_detail = heights
                    self._audio_detail_scale = 255.0
                    self._audio_detail_companded = False
                self._audio_detail_rate = rate
                self._repaint()
                return
        self._audio_detail = b""
        self._audio_detail_rate = 0.0
        self._audio_detail_scale = 255.0
        self._audio_detail_companded = False
        self._repaint()

    def set_position(self, frac: float):
        self._pos = min(1.0, max(0.0, frac))
        if self._duration_ms:
            self._position_ms = int(self._pos * self._duration_ms)
        self._repaint()

    def set_timing(self, position_ms: int | float, duration_ms: int):
        self._position_ms = max(0, position_ms)
        self._duration_ms = max(0, duration_ms)
        self._pos = (self._position_ms / self._duration_ms
                     if self._duration_ms else 0.0)
        self._repaint()

    def _snap_to_beat(self, target_ms: int) -> int:
        """Magnetize close scrubs to rekordbox's exact beat timestamp."""
        if not self._beat_times:
            return target_ms
        i = bisect_right(self._beat_times, target_ms)
        nearby = self._beat_times[max(0, i - 1):min(len(self._beat_times), i + 1)]
        if not nearby:
            return target_ms
        closest = min(nearby, key=lambda ms: abs(ms - target_ms))
        threshold_ms = round(7 / self.PIXELS_PER_SECOND * 1000)
        return closest if abs(closest - target_ms) <= threshold_ms else target_ms

    def _bounded_ms(self, target: int | float) -> float:
        return min(self._duration_ms, max(0, target))

    def _preview_to_ms(self, target: int | float):
        """Move only the display while dragging; decoding stays untouched."""
        target = self._bounded_ms(target)
        self._drag_preview_ms = target
        self.set_timing(target, self._duration_ms)
        self.previewed.emit(target)

    def reanchor_drag(self, position_ms: int | float):
        """Replace the coarse Qt mouse-down time with Metal's exact hold."""
        if self._drag_origin_ms is None:
            return
        position = self._bounded_ms(position_ms)
        self._drag_origin_ms = position
        self._drag_preview_ms = position
        self.set_timing(position, self._duration_ms)

    def _seek_to_ms(self, target: int | float, snap: bool = True):
        if not self._duration_ms:
            return
        target = self._bounded_ms(target)
        if snap:
            target = self._snap_to_beat(round(target))
        self.set_timing(target, self._duration_ms)
        self.seeked.emit(target / self._duration_ms)

    def mousePressEvent(self, ev):
        point = ev.position()
        point_y = point.y() if hasattr(point, "y") else _ZOOM_TOP
        zoom_direction = self._zoom_button_at(point.x(), point_y)
        if zoom_direction:
            self._step_zoom(zoom_direction)
            ev.accept()
            return
        if not self._active or not self._duration_ms:
            return
        self._drag_origin_x = ev.position().x()
        self._drag_origin_ms = self._position_ms
        self._drag_preview_ms = self._position_ms
        self._drag_moved = False
        self._overview_drag = point_y < _OVERVIEW_HEIGHT
        self.setCursor(Qt.ClosedHandCursor)
        if self._overview_drag:
            # The overview remains an absolute click/drag navigation surface.
            # Its first preview therefore owns transport immediately.
            self.drag_started.emit()
            self._preview_to_ms(self._overview_time_at_x(point.x()))
        else:
            # A playing detail waveform freezes and begins its clean pause as
            # soon as the hand closes. Release always lands/resumes at the held
            # timestamp, even when the pointer never crosses the drag threshold.
            self.drag_started.emit()
        ev.accept()

    def mouseMoveEvent(self, ev):
        if (not self._active or self._drag_origin_ms is None
                or not ev.buttons() & Qt.LeftButton):
            return
        if self._overview_drag:
            self._drag_moved = True
            self._preview_to_ms(self._overview_time_at_x(ev.position().x()))
            ev.accept()
            return
        delta_x = ev.position().x() - self._drag_origin_x
        if delta_x != 0:
            self._drag_moved = True
        if self._drag_moved:
            # Grab semantics: drag the waveform right to reveal earlier audio,
            # left to reveal later audio. Always calculate from mouse-down so
            # repeated move events never compound or accelerate. This is a
            # VISUAL preview only: asking the decoder to seek on every mouse
            # event made the hand interaction stutter and queued stale seeks.
            target = (
                self._drag_origin_ms
                - delta_x / self.PIXELS_PER_SECOND * 1000.0)
            self._preview_to_ms(target)
        ev.accept()

    def mouseReleaseEvent(self, ev):
        if self._drag_origin_ms is None:
            return
        overview = self._overview_drag
        if overview:
            target = self._overview_time_at_x(ev.position().x())
        else:
            target = (self._drag_preview_ms
                      if self._drag_preview_ms is not None
                      else self._drag_origin_ms)
        self._drag_origin_ms = None
        self._drag_preview_ms = None
        self._drag_moved = False
        self._overview_drag = False
        self.setCursor(Qt.OpenHandCursor if self._active else Qt.ArrowCursor)
        if target is not None:
            # Every completed grab lands on the exact held timestamp. A
            # playing deck resumes only after its one authoritative seek.
            self._seek_to_ms(target, snap=False)
            self.drag_finished.emit()
        ev.accept()

    def _overview_time_at_x(self, x: float) -> int:
        """Absolute song timestamp under the whole-track overview."""
        if not self._duration_ms or self.width() <= 1:
            return 0
        fraction = min(1.0, max(0.0, float(x) / self.width()))
        return round(fraction * self._duration_ms)

    def paintEvent(self, _ev):
        if self._native_rendering:
            return
        p = QPainter(self)
        # The built-in display is 120 Hz / 2× Retina. Integer coordinates made
        # 92 px/s motion advance in obvious whole-logical-pixel stair-steps.
        # Fractional coordinates resolve at physical-pixel precision; leaving
        # antialiasing off keeps the RGB waveform razor-sharp and comfortably
        # inside an 8.33 ms frame budget.
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        mid = _ZOOM_TOP + (h - _ZOOM_TOP) / 2
        head_x = w // 2
        p.fillRect(self.rect(), _WAVE_BG)
        self._paint_overview(p, w)
        if self._rgb_columns:
            self._paint_rgb(p, head_x, mid)
        elif self._peaks and self._duration_ms:
            self._paint_fallback(p, head_x, mid)
        else:
            p.setPen(_REST); p.drawLine(0, int(mid), w, int(mid))
        # Grid belongs over the waveform, as on a deck. Drawing it last keeps
        # beat/downbeat markers legible now that RGB columns are fully opaque.
        self._paint_grid(p, head_x, h)
        self._paint_cues(p, head_x, h)
        self._paint_current_beat(p, head_x)
        self._paint_zoom_controls(p)

        # Fixed playhead: the waveform and beat grid move beneath it.
        p.setPen(QPen(QColor(46, 51, 61, 185), 1))
        p.drawLine(0, _OVERVIEW_HEIGHT, w, _OVERVIEW_HEIGHT)
        p.drawLine(0, _ZOOM_TOP - 1, w, _ZOOM_TOP - 1)
        if self._duration_ms:
            overview_x = self._position_ms / self._duration_ms * w
            p.setPen(QPen(QColor("#18c8ff"), 1))
            p.drawLine(QPointF(overview_x, 1),
                       QPointF(overview_x, _OVERVIEW_HEIGHT - 1))
        p.setPen(QPen(QColor("#18c8ff"), 1))
        p.drawLine(head_x, _ZOOM_TOP, head_x, h)
        p.setBrush(QColor("#18c8ff"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([
            QPointF(head_x - 5, _ZOOM_TOP),
            QPointF(head_x + 5, _ZOOM_TOP),
            QPointF(head_x, _ZOOM_TOP + 7),
        ]))

    def _time_at_x(self, x: int, head_x: int) -> float:
        return self._position_ms / 1000 + (x - head_x) / self.PIXELS_PER_SECOND

    @staticmethod
    def _rgb_color(raw: list[int]) -> QColor:
        """Expand PWV5's compact channel intensities into a vivid screen color."""
        r, g, b = (list(raw) + [0, 0, 0])[:3]
        rr = min(255, int(r * 18))
        gg = min(255, int(g * 36))
        bb = min(255, int(b * 36))
        brightest = max(rr, gg, bb)
        if 0 < brightest < 155:
            boost = 155 / brightest
            rr, gg, bb = (min(255, int(v * boost)) for v in (rr, gg, bb))
        return QColor(rr, gg, bb)

    def _paint_rgb(self, p: QPainter, head_x: int, mid: float):
        audio_detail = (
            self._audio_detail
            if self._audio_detail_rate > 0
            else b""
        )
        color_detail = self._rgb_detail or self._rgb_columns
        color_rate = (
            self._rgb_rate if self._rgb_detail else _PIXELS_PER_SECOND
        )
        n = len(audio_detail) if audio_detail else len(color_detail)
        if not n:
            return
        samples_per_second = (
            self._audio_detail_rate if audio_detail else color_rate
        )
        half = max(4.0, (self.height() - _ZOOM_TOP) / 2 - 7)
        for x in range(self.width()):
            seconds = self._time_at_x(x, head_x)
            position = seconds * samples_per_second
            column = floor(position)
            if not 0 <= column < n:
                continue
            footprint = max(
                1.0, samples_per_second / self.PIXELS_PER_SECOND)
            start = max(0, floor(position - footprint * 0.5))
            end = min(n, max(start + 1, ceil(
                position + footprint * 0.5)))
            if footprint <= 1.0:
                phase = position - column
                if audio_detail:
                    a_height = (
                        audio_detail[column] / self._audio_detail_scale)
                    b_height = (
                        audio_detail[min(column + 1, n - 1)]
                        / self._audio_detail_scale)
                else:
                    a_height = color_detail[column][0]
                    b_height = color_detail[min(column + 1, n - 1)][0]
                height = a_height + (b_height - a_height) * phase
            elif audio_detail:
                height = (
                    max(audio_detail[start:end]) / self._audio_detail_scale)
            else:
                height = max(
                    row[0] for row in color_detail[start:end])
            if audio_detail and self._audio_detail_companded:
                height = _display_waveform_height(height)
            color_position = seconds * color_rate
            color_column = floor(color_position)
            if not 0 <= color_column < len(color_detail):
                continue
            color_footprint = max(
                1.0, color_rate / self.PIXELS_PER_SECOND)
            color_start = max(
                0, floor(color_position - color_footprint * 0.5))
            color_end = min(
                len(color_detail),
                max(color_start + 1, ceil(
                    color_position + color_footprint * 0.5)))
            color_window = color_detail[color_start:color_end]
            energy = sum(max(0.0, row[0]) for row in color_window)
            if energy > 0:
                color = QColor(
                    round(sum(
                        row[1].red() * max(0.0, row[0])
                        for row in color_window) / energy),
                    round(sum(
                        row[1].green() * max(0.0, row[0])
                        for row in color_window) / energy),
                    round(sum(
                        row[1].blue() * max(0.0, row[0])
                        for row in color_window) / energy),
                )
            else:
                color = color_detail[color_column][1]
            if color_footprint <= 1.0 and color_column + 1 < len(color_detail):
                phase = color_position - color_column
                next_color = color_detail[color_column + 1][1]
                color = QColor(
                    round(color.red() + (
                        next_color.red() - color.red()) * phase),
                    round(color.green() + (
                        next_color.green() - color.green()) * phase),
                    round(color.blue() + (
                        next_color.blue() - color.blue()) * phase),
                )
            amp = max(1.0, height * half)
            p.setPen(color)
            p.drawLine(QPointF(x, mid - amp), QPointF(x, mid + amp))

    def _song_x(self, position_ms: int | float) -> float:
        """Continuous absolute raster coordinate for one song timestamp."""
        return float(position_ms) / 1000 * self.PIXELS_PER_SECOND

    def _song_column(self, position_ms: int) -> int:
        """Nearest absolute raster column for one song timestamp."""
        return int(self._song_x(position_ms) + 0.5)

    def _paint_fallback(self, p: QPainter, head_x: int, mid: float):
        """Scrolling amplitude-only fallback for XML/imported tracks."""
        n = len(self._peaks)
        half = max(4.0, (self.height() - _ZOOM_TOP) / 2 - 7)
        for x in range(self.width()):
            sec = self._time_at_x(x, head_x)
            idx = int(sec / (self._duration_ms / 1000) * n)
            if not 0 <= idx < n:
                continue
            amp = max(1.0, self._peaks[idx] * half)
            p.setPen(_PLAYED if x <= head_x else _REST)
            p.drawLine(x, int(mid - amp), x, int(mid + amp))

    def _paint_overview(self, p: QPainter, width: int):
        """Stable whole-track envelope used for absolute song navigation."""
        if width <= 0:
            return
        if self._rgb_columns:
            if self._overview_cache_width != width:
                source = self._rgb_columns
                overview = []
                for x in range(width):
                    start = x * len(source) // width
                    end = max(start + 1, (x + 1) * len(source) // width)
                    end = min(len(source), end)
                    overview.append(max(
                        source[start:end], key=lambda row: row[0]))
                self._overview_columns = overview
                self._overview_cache_width = width
            center = _OVERVIEW_HEIGHT / 2
            half = center - 3
            for x, (height, color) in enumerate(self._overview_columns):
                amp = max(1.0, height * half)
                p.setPen(color)
                p.drawLine(QPointF(x, center - amp),
                           QPointF(x, center + amp))
            return
        if not self._peaks:
            p.setPen(_REST)
            p.drawLine(0, _OVERVIEW_HEIGHT // 2,
                       width, _OVERVIEW_HEIGHT // 2)
            return
        center = _OVERVIEW_HEIGHT / 2
        half = center - 3
        for x in range(width):
            start = x * len(self._peaks) // width
            end = min(
                len(self._peaks),
                max(start + 1, (x + 1) * len(self._peaks) // width))
            amp = max(1.0, max(self._peaks[start:end]) * half)
            p.setPen(_REST)
            p.drawLine(QPointF(x, center - amp),
                       QPointF(x, center + amp))

    def _paint_grid(self, p: QPainter, head_x: int, height: int):
        if not self._beats:
            return
        center = self._song_x(self._position_ms)
        for ms, bar, beat in self._beat_positions:
            # Use the same continuous song coordinate as the RGB raster, so
            # waveform and grid translate as one phase-locked surface.
            x = head_x + self._song_x(ms) - center
            if not 0 <= x < self.width():
                continue
            if beat == 1:
                color = QColor(255, 55, 88)
                p.setPen(QPen(color, 1))
                p.drawLine(QPointF(x, _ZOOM_TOP), QPointF(x, height))
            else:
                p.setPen(QPen(QColor(120, 135, 158), 1))
                p.drawLine(QPointF(x, _ZOOM_TOP), QPointF(x, height))

    @staticmethod
    def _hot_cue_color(cue: dict) -> QColor:
        try:
            slot = max(0, min(7, int(cue.get("slot", 0))))
        except (TypeError, ValueError):
            slot = 0
        # Imported RGB remains part of the canonical cue record for export,
        # while the deck always uses one stable visual identity per A–H slot.
        return QColor(_HOT_CUE_FALLBACKS[slot])

    @staticmethod
    def _bounded_marker_left(anchor: float, marker_width: float,
                             total_width: int) -> float:
        return min(
            max(0.0, anchor - marker_width / 2),
            max(0.0, total_width - marker_width),
        )

    def _paint_hot_cue(self, p: QPainter, cue: dict, anchor: float,
                       top: float, marker_width: float,
                       marker_height: float, line_bottom: float):
        color = self._hot_cue_color(cue)
        p.setPen(QPen(color, 1))
        p.drawLine(
            QPointF(anchor, top + marker_height),
            QPointF(anchor, line_bottom))
        left = self._bounded_marker_left(
            anchor, marker_width, self.width())
        box = QRectF(left, top, marker_width, marker_height)
        p.setPen(QPen(QColor("#050608"), 1))
        p.setBrush(color)
        p.drawRect(box)
        p.setPen(QColor("#050608"))
        try:
            slot = max(0, min(7, int(cue.get("slot", 0))))
        except (TypeError, ValueError):
            slot = 0
        p.drawText(box, Qt.AlignCenter, chr(ord("A") + slot))

    @staticmethod
    def _paint_memory_cue(p: QPainter, anchor: float,
                          apex_y: float, base_y: float):
        p.setPen(QPen(QColor("#050608"), 1))
        p.setBrush(_MEMORY_CUE_COLOR)
        p.drawPolygon(QPolygonF([
            QPointF(anchor, apex_y),
            QPointF(anchor - 6, base_y),
            QPointF(anchor + 6, base_y),
        ]))

    def _paint_cues(self, p: QPainter, head_x: int, height: int):
        """Draw hot-cue identity and memory arrows in both waveform scales."""
        if self._duration_ms <= 0:
            return
        center = self._song_x(self._position_ms)
        if self._session_cue_ms is not None:
            position = self._session_cue_ms
            overview_x = position / self._duration_ms * self.width()
            detail_x = head_x + self._song_x(position) - center
            self._paint_session_cue_arrow(
                p, overview_x, _OVERVIEW_HEIGHT - 10.0,
                _OVERVIEW_HEIGHT + 0.5)
            if -7 <= detail_x <= self.width() + 7:
                self._paint_session_cue_arrow(
                    p, detail_x, height - 10.0, height + 0.5)
        for cue in self._cues:
            position = int(cue["position_ms"])
            overview_x = position / self._duration_ms * self.width()
            detail_x = head_x + self._song_x(position) - center
            if cue["kind"] == "hot":
                self._paint_hot_cue(
                    p, cue, overview_x, 0.0, 18.0, 15.0,
                    _OVERVIEW_HEIGHT - 1.0)
                if -12 <= detail_x <= self.width() + 12:
                    self._paint_hot_cue(
                        p, cue, detail_x, float(_ZOOM_TOP),
                        22.0, 17.0, float(height))
            else:
                self._paint_memory_cue(
                    p, overview_x,
                    _OVERVIEW_HEIGHT - 11.0, _OVERVIEW_HEIGHT + 0.5)
                if -7 <= detail_x <= self.width() + 7:
                    self._paint_memory_cue(
                        p, detail_x, height - 11.0, height + 0.5)

    @staticmethod
    def _paint_session_cue_arrow(
            p: QPainter, anchor: float, apex_y: float, base_y: float):
        p.setPen(QPen(QColor("#050608"), 1))
        p.setBrush(_SESSION_CUE_COLOR)
        p.drawPolygon(QPolygonF([
            QPointF(anchor, apex_y),
            QPointF(anchor - 5.5, base_y),
            QPointF(anchor + 5.5, base_y),
        ]))

    def _paint_current_beat(self, p: QPainter, head_x: int):
        """Show one uncluttered bar.beat count, anchored to the playhead."""
        if not self._beat_positions:
            return
        index = bisect_right(self._beat_times, self._position_ms) - 1
        if index < 0:
            return
        _ms, bar, beat = self._beat_positions[
            min(index, len(self._beat_positions) - 1)]
        left, right = str(bar), str(beat)
        color = QColor("#18c8ff")
        p.setPen(color)
        p.drawText(QPointF(
            head_x - 1 - p.fontMetrics().horizontalAdvance(left),
            _RULER_TOP + 14), left)
        p.fillRect(QRectF(
            head_x - 1, _RULER_TOP + 11, 2, 2), color)
        p.drawText(QPointF(head_x + 2, _RULER_TOP + 14), right)

    def _paint_zoom_controls(self, p: QPainter):
        """Small screen-anchored controls; only the detail view is scaled."""
        for direction, rect in (
                (-1, QRectF(0, _RULER_TOP + 1, 26, 16)),
                (1, QRectF(30, _RULER_TOP + 1, 26, 16))):
            enabled = (
                self._zoom_level > 0 if direction < 0
                else self._zoom_level < len(_ZOOM_LEVELS) - 1)
            border = QColor(82, 88, 103, 210) if enabled else QColor(55, 59, 69)
            glyph = QColor(205, 210, 220) if enabled else QColor(78, 82, 92)
            p.fillRect(rect, QColor(17, 19, 25, 235))
            p.setPen(QPen(border, 1))
            p.drawRect(rect)
            p.setPen(QPen(glyph, 1))
            center = rect.center()
            p.drawLine(
                QPointF(center.x() - 4, center.y()),
                QPointF(center.x() + 4, center.y()))
            if direction > 0:
                p.drawLine(
                    QPointF(center.x(), center.y() - 4),
                    QPointF(center.x(), center.y() + 4))


class CuePanel(QWidget):
    """Two compact 2×4 pages for read-only Rekordbox hot and memory cues."""

    cue_pressed = Signal(str, int, int)  # kind, slot, absolute milliseconds
    cue_released = Signal(str, int)      # kind, slot
    # Compatibility observer for callers that only need the chosen position.
    cue_requested = Signal(int)
    _HOT_COLORS = _HOT_CUE_FALLBACKS

    def __init__(self):
        super().__init__()
        self.setObjectName("CuePanel")
        self.setFixedWidth(198)
        self._loaded = False
        self._cues = {"hot": [None] * 8, "memory": [None] * 8}

        self.pages = QStackedWidget()
        self.pages.setObjectName("CuePages")
        self.hot_slots = self._page("hot")
        self.memory_slots = self._page("memory")

        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(2)
        self.hot_tab = self._tab("HOT CUE", 0)
        self.memory_tab = self._tab("MEM CUE", 1)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.hot_tab)
        group.addButton(self.memory_tab)
        self.hot_tab.setChecked(True)
        tabs.addWidget(self.hot_tab)
        tabs.addWidget(self.memory_tab)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.pages, 1)
        layout.addLayout(tabs)
        self._refresh()

    def _page(self, kind: str) -> list[QPushButton]:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(3)
        slots = []
        for slot in range(8):
            button = QPushButton()
            button.setObjectName("CueSlot")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(20)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.pressed.connect(
                lambda k=kind, s=slot: self._press(k, s))
            button.released.connect(
                lambda k=kind, s=slot: self._release(k, s))
            grid.addWidget(button, slot // 2, slot % 2)
            slots.append(button)
        for row in range(4):
            grid.setRowStretch(row, 1)
        self.pages.addWidget(page)
        return slots

    def _tab(self, text: str, page: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("CueTab")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(18)
        button.clicked.connect(
            lambda _checked=False, index=page: self.pages.setCurrentIndex(index))
        return button

    def set_loaded(self, loaded: bool):
        loaded = bool(loaded)
        if loaded == self._loaded:
            return
        self._loaded = loaded
        self._refresh()

    def set_cues(self, cues: list[dict]):
        self._cues = {"hot": [None] * 8, "memory": [None] * 8}
        for cue in cues or []:
            kind = cue.get("kind")
            try:
                slot = int(cue.get("slot", -1))
            except (TypeError, ValueError):
                continue
            if kind in self._cues and 0 <= slot < 8:
                self._cues[kind][slot] = cue
        self._refresh()

    def _refresh(self):
        for kind, buttons in (
                ("hot", self.hot_slots), ("memory", self.memory_slots)):
            for slot, button in enumerate(buttons):
                cue = self._cues[kind][slot]
                label = chr(ord("A") + slot) if kind == "hot" else str(slot + 1)
                button.setProperty("filled", cue is not None)
                if cue is None:
                    button.setText(f"{label}   —")
                    button.setIcon(QIcon())
                    button.setToolTip(
                        f"{'Hot' if kind == 'hot' else 'Memory'} cue {label}")
                    button.setEnabled(False)
                else:
                    position = max(0, int(cue.get("position_ms", 0)))
                    button.setText(f"{label}  {_fmt(position)}")
                    raw_color = (_MEMORY_CUE_COLOR if kind == "memory"
                                 else self._HOT_COLORS[slot])
                    color = (QColor(*raw_color) if isinstance(raw_color, list)
                             else QColor(raw_color))
                    pixmap = QPixmap(12, 12)
                    pixmap.setDevicePixelRatio(2.0)
                    pixmap.fill(color)
                    button.setIcon(QIcon(pixmap))
                    button.setIconSize(QSize(6, 6))
                    details = [cue.get("label") or "",
                               f"{'Loop' if cue.get('type') == 'loop' else 'Cue'}"
                               f" at {_fmt(position)}",
                               "Hold to preview · Space latches"]
                    button.setToolTip(" · ".join(part for part in details if part))
                    button.setEnabled(self._loaded)
                button.style().unpolish(button)
                button.style().polish(button)

    def _press(self, kind: str, slot: int):
        cue = self._cues[kind][slot]
        if self._loaded and cue is not None:
            position = max(0, int(cue.get("position_ms", 0)))
            self.cue_requested.emit(position)
            self.cue_pressed.emit(kind, slot, position)

    def _release(self, kind: str, slot: int):
        cue = self._cues[kind][slot]
        if self._loaded and cue is not None:
            self.cue_released.emit(kind, slot)


class PlayerBar(QWidget):
    """DJ-style transport deck backed exclusively by CoreAudio + Metal."""

    FADE_OUT_MS = 24
    FADE_IN_MS = 28

    playback_error = Signal(str)  # surfaced by the app's status bar
    previous_requested = Signal()  # MainWindow owns the visible track order
    next_requested = Signal()
    track_ended = Signal(str)
    now_playing_changed = Signal(str)
    playback_active_changed = Signal(bool)
    source_changed = Signal(str)
    jump_mode_changed = Signal(str)
    waveform_zoom_changed = Signal(float)
    load_diagnostic = Signal(str, float)  # phase, milliseconds since load intent

    def __init__(
            self, jump_mode: str = "16 BEATS",
            waveform_zoom: float = _PIXELS_PER_SECOND,
            waveform_mode: str = "3BAND"):
        super().__init__()
        self.track_analysis_dir = None  # canonical SoundBox-owned V4 records
        self._deck_volume = 0.9
        self._gain_completion = None
        self._gain_epoch = 0
        self._transport_epoch = 0
        self._pause_pending = False
        self._pause_clock = QElapsedTimer()
        self._pause_floor_ms = 0
        self._deck_drag_active = False
        self._deck_drag_resume = False
        self._deck_drag_target: float | None = None
        self._track_id: str | None = None
        self._src_path: str | None = None
        self._analysis_path = ""
        self._live_cues: list[dict] | None = None
        self._dur = 0
        self._pos = 0
        self._bpm = 0.0
        self._cue_ms = 0
        self._session_cue_state = SessionCueState.UNSET
        self._cue_preview_active = False
        self._cue_preview_latched = False
        self._cue_preview_target = 0
        self._cue_preview_control: tuple[str, int] | None = None
        self._cue_preview_token = 0
        # This is deliberately transport intent, not backend playing state.
        # Momentary cue preview runs the decoder without visually engaging
        # PLAY; Space while held promotes that preview into transport playback.
        self._transport_play_active = False
        # Sentinel: never equal to a real signature, so the first call applies.
        self._transport_button_state: tuple | None = None
        self._pending_autoplay = False
        self._pending_should_play = True
        self._pending_start_ms: int | None = None
        self._beat_grid: list[int] = []
        self._analysis_generation = 0
        self._analysis_parts: dict = {}
        self._load_started = 0.0
        self._jump_mode = "16 BEATS"
        self._jump_beats = 16
        self._waveform_mode = (
            waveform_mode if waveform_mode in _WAVEFORM_MODES else "3BAND")
        # This monotonic clock keeps transport text/buttons coherent between
        # authoritative native snapshots. Metal uses the CoreAudio clock itself.
        self._transport_clock = QElapsedTimer()
        self._transport_anchor_ms = 0.0
        self._transport_running = False
        self._transport_slew_ms = 0.0
        self._wf: WaveformWorker | None = None
        # Strong refs to EVERY in-flight decode worker until it finishes — a
        # rapid track change starts a new one before the old returns, and
        # dropping the old Python ref mid-run GC's a running QThread → SIGABRT.
        self._wf_pending: list[WaveformWorker] = []

        # Compact transport stack, modeled after a CDJ/rekordbox deck:
        # track navigation → selectable beat/bar jump → cue/play. MainWindow handles track
        # order; all within-track operations stay local to this player.
        self.previous_btn = self._button("", "TransportBtn", "Previous track")
        self.next_btn = self._button("", "TransportBtn", "Next track")
        self.previous_btn.setIcon(_track_nav_icon(-1))
        self.next_btn.setIcon(_track_nav_icon(1))
        self.previous_btn.setIconSize(QSize(18, 14))
        self.next_btn.setIconSize(QSize(18, 14))
        self.beat_back_btn = self._button("◀", "TransportBtn", "Jump backward")
        self.beat_forward_btn = self._button("▶", "TransportBtn", "Jump forward")
        self.cue_btn = self._button("CUE", "CueBtn",
                                    "Hold to preview session cue · Space latches · C")
        self.play_btn = self._button("▶", "DeckPlayBtn", "Play / pause")
        self.cue_btn.setProperty("active", False)
        self.play_btn.setProperty("playing", False)
        for b in (self.previous_btn, self.next_btn, self.beat_back_btn,
                  self.beat_forward_btn):
            b.setFixedSize(42, 26)
        self.cue_btn.setFixedSize(42, 42)
        self.play_btn.setFixedSize(42, 42)
        self.previous_btn.clicked.connect(
            lambda _checked=False: self.previous_requested.emit())
        self.next_btn.clicked.connect(
            lambda _checked=False: self.next_requested.emit())
        self.beat_back_btn.clicked.connect(lambda: self._beat_jump(-1))
        self.beat_forward_btn.clicked.connect(lambda: self._beat_jump(1))
        self.cue_btn.pressed.connect(self._cue_press)
        self.cue_btn.released.connect(self._cue_release)
        self.play_btn.clicked.connect(self.toggle)

        self.beat_selector_btn = self._button(
            "16 BEATS", "BeatSelectorBtn", "Choose beat/bar jump length")
        self.beat_selector_btn.setFixedHeight(17)
        self._jump_menu = QMenu(self.beat_selector_btn)
        for label in _JUMP_MODES:
            action = self._jump_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, mode=label: self.set_jump_mode(mode))
        self.beat_selector_btn.setMenu(self._jump_menu)
        self.set_jump_mode(jump_mode, emit=False)

        deck = QWidget()
        deck.setObjectName("TransportDeck")
        # The top-bar's former 14px outer inset made this group look shifted
        # right inside its visual section. Keep the divider in the same place,
        # but let the deck own that width and center the controls within it.
        deck.setFixedWidth(116)
        dl = QVBoxLayout(deck)
        dl.setContentsMargins(13, 0, 14, 0)
        dl.setSpacing(2)
        dl.addStretch(1)
        nav = QHBoxLayout(); nav.setSpacing(5)
        nav.addWidget(self.previous_btn); nav.addWidget(self.next_btn)
        dl.addLayout(nav)
        jump = QHBoxLayout(); jump.setSpacing(5)
        jump.addWidget(self.beat_back_btn); jump.addWidget(self.beat_forward_btn)
        dl.addLayout(jump)
        dl.addWidget(self.beat_selector_btn)
        dl.addSpacing(5)
        bottom = QHBoxLayout(); bottom.setSpacing(5)
        bottom.addWidget(self.cue_btn); bottom.addWidget(self.play_btn)
        dl.addLayout(bottom)
        dl.addStretch(1)

        self.wave = WaveformBar()
        self.wave.setMinimumHeight(132)
        self.wave.set_zoom(waveform_zoom, emit=False)
        self.wave.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)  # eat all slack
        self.cue_panel = CuePanel()
        self.cue_panel.cue_pressed.connect(self._marker_cue_press)
        self.cue_panel.cue_released.connect(self._marker_cue_release)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(deck, 0)
        lay.addSpacing(12)
        lay.addWidget(self.wave, 1)
        lay.addSpacing(10)
        lay.addWidget(self.cue_panel, 0)

        self._playback_available = True
        self._native_error = ""
        try:
            self.player = NativeDeckBackend(self.wave, self)
        except Exception as exc:  # noqa: BLE001
            self._playback_available = False
            self._native_error = (
                "Player disabled: the native CoreAudio/Metal deck could not "
                f"initialize ({exc}). Library management remains available."
            )
            self.player = UnavailableDeckBackend(self._native_error, self)
        self.audio = NativeGainControl(self.player, self)
        self.audio.setVolume(self._deck_volume)
        self.player.errorOccurred.connect(self._on_error)
        self.wave.set_native_rendering(self._playback_available)
        applied_zoom = self.player.set_zoom(self.wave.PIXELS_PER_SECOND)
        self.wave.set_zoom(applied_zoom, emit=False)
        if hasattr(self.player, "set_waveform_mode"):
            self.player.set_waveform_mode(self._waveform_mode)

        self.wave.seeked.connect(self._seek)
        self.wave.previewed.connect(self._on_wave_preview)
        self.wave.drag_started.connect(self._on_wave_drag_started)
        self.wave.drag_finished.connect(self._on_wave_drag_finished)
        self.wave.zoom_changed.connect(self._on_wave_zoom_changed)
        self.player.positionChanged.connect(self._on_backend_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.endOfMedia.connect(self._on_end_of_media)
        self._update_transport()
        self.set_track_navigation(False, False)
        if self._native_error:
            QTimer.singleShot(
                0, lambda: self.playback_error.emit(self._native_error))

    # ---- public API ----
    @staticmethod
    def _button(text: str, object_name: str, tooltip: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(object_name)
        b.setToolTip(tooltip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def set_waveform_mode(self, mode: str):
        normalized = str(mode).upper()
        if normalized not in _WAVEFORM_MODES:
            normalized = "3BAND"
        self._waveform_mode = normalized
        if hasattr(self.player, "set_waveform_mode"):
            self.player.set_waveform_mode(normalized)

    @staticmethod
    def _set_dynamic_property(
            button: QPushButton, name: str, value: bool):
        value = bool(value)
        if button.property(name) == value:
            return
        button.setProperty(name, value)
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _sync_deck_button_states(self):
        """Keep cue gating visually separate from transport playback."""
        if not hasattr(self, "cue_btn") or not hasattr(self, "play_btn"):
            return
        # The glyph follows the same explicit transport intent as the purple
        # fill. Momentary cue preview may run the decoder, but it must continue
        # to show Play until Space promotes it into normal playback.
        self.play_btn.setText(
            "❚❚" if self._transport_play_active else "▶")
        self._set_dynamic_property(
            self.cue_btn, "active", self._cue_preview_active)
        self._set_dynamic_property(
            self.play_btn, "playing", self._transport_play_active)

    def current_track(self) -> str | None:
        return self._track_id

    def set_jump_mode(self, mode: str, emit: bool = True):
        """Select the grid-aware transport jump and refresh edge availability."""
        mode = mode if mode in _JUMP_MODES else "16 BEATS"
        changed = mode != self._jump_mode
        self._jump_mode = mode
        self._jump_beats = _JUMP_MODES[mode]
        if hasattr(self, "beat_selector_btn"):
            self.beat_selector_btn.setText(mode)
            self.beat_back_btn.setToolTip(f"Jump back {mode.lower()}")
            self.beat_forward_btn.setToolTip(f"Jump forward {mode.lower()}")
        if hasattr(self, "play_btn"):
            self._update_transport()
        if emit and changed:
            self.jump_mode_changed.emit(mode)

    def set_waveform_zoom(self, pixels_per_second: float, emit: bool = True):
        previous = self.wave.PIXELS_PER_SECOND
        self.wave.set_zoom(pixels_per_second, emit=False)
        if hasattr(self.player, "set_zoom"):
            applied = self.player.set_zoom(self.wave.PIXELS_PER_SECOND)
            self.wave.set_zoom(applied, emit=False)
        if emit and self.wave.PIXELS_PER_SECOND != previous:
            self.waveform_zoom_changed.emit(
                float(self.wave.PIXELS_PER_SECOND))

    def _on_wave_zoom_changed(self, pixels_per_second: float):
        if hasattr(self.player, "set_zoom"):
            pixels_per_second = self.player.set_zoom(pixels_per_second)
            self.wave.set_zoom(pixels_per_second, emit=False)
        self.waveform_zoom_changed.emit(float(pixels_per_second))

    def is_playing(self) -> bool:
        return self.player.playbackState() == PlaybackState.PLAYING

    def play_track(self, track_id: str, path, title: str = "", bpm=None,
                   analysis_path: str = "", autoplay: bool = True,
                   cues: list[dict] | None = None):
        """Load a track, optionally starting once its first grid beat is known.

        ``autoplay=False`` still waits for prepared analysis and positions the
        paused deck on the first rekordbox beat. MainWindow uses that path for
        double-click; previous/next track navigation retains autoplay.
        """
        path = str(path)
        changed = (track_id != self._track_id or path != self._src_path
                   or analysis_path != self._analysis_path)
        if changed and (self.is_playing() or self._pause_pending):
            # Never tear a live decoder/source out from under an audible
            # buffer. Fade to silence, phase-lock the stopped transport, then
            # perform the source change and its normal clickless fade-in.
            self._pause_clean(lambda: self.play_track(
                track_id, path, title=title, bpm=bpm,
                analysis_path=analysis_path, autoplay=autoplay, cues=cues))
            return
        self._live_cues = list(cues) if cues is not None else None
        if changed:
            self._load_started = perf_counter()
            self._analysis_generation += 1
            generation = self._analysis_generation
            self._track_id = track_id
            self._src_path = path
            self.source_changed.emit(path)
            self._analysis_path = analysis_path or ""
            self._pos = 0
            self._dur = 0
            self._cue_ms = 0
            self._session_cue_state = SessionCueState.UNSET
            self._analysis_parts = {}
            if hasattr(self.player, "begin_track"):
                self.player.begin_track(generation)
            self._set_session_cue(None)
            self._transport_play_active = False
            self._reset_cue_preview()
            self._pending_autoplay = True
            self._pending_should_play = autoplay
            self._pending_start_ms = None
            self._beat_grid = []
            self.cue_panel.set_cues([])
            self._anchor_transport(0, False)
            self.player.setSource(QUrl.fromLocalFile(path))
            self._mark_load_phase("click-to-source")
            self.wave.set_analysis({})
            if (hasattr(self.player, "set_analysis")
                    and not hasattr(self.player, "begin_track")):
                self.player.set_analysis({})
            self.wave.set_peaks([])
            self.wave.set_timing(0, 0)
            self._start_waveform(path, self._analysis_path)
        if self._live_cues is not None:
            self.wave.set_cues(self._live_cues)
            self.cue_panel.set_cues(self._live_cues)
            if hasattr(self.player, "set_cues"):
                self.player.set_cues(self._live_cues)
        try:
            self._bpm = float(bpm or 0)
        except (TypeError, ValueError):
            self._bpm = 0.0
        self.now_playing_changed.emit(title)
        self.wave.set_active(True)  # a track is loaded — scrubbing is now live
        self._update_transport()
        if not changed and autoplay:
            self._transport_play_active = True
            self._sync_deck_button_states()
            self._play_clean()
        elif not self._analysis_path:
            # Imported/non-rekordbox tracks have no grid to wait for.
            self._pending_start_ms = 0
            self._try_pending_autoplay()

    def set_track_navigation(self, can_previous: bool, can_next: bool):
        """MainWindow tells us whether adjacent tracks exist in the active view."""
        loaded = self._track_id is not None
        self.previous_btn.setEnabled(loaded and can_previous)
        self.next_btn.setEnabled(loaded and can_next)

    def _on_error(self, _err, err_string: str):
        self.playback_error.emit(err_string or "Playback error")

    def _animate_gain(self, end: float, duration_ms: int, completion=None):
        self._gain_epoch = self._transport_epoch
        self._gain_completion = completion
        ramp = getattr(self.player, "ramp_gain", None)
        if ramp is None:  # narrow protocol accommodation for hermetic test fakes
            self.audio.setVolume(end)
            self._on_gain_finished()
        else:
            ramp(end, duration_ms, self._on_gain_finished)

    def _on_gain_finished(self):
        completion = self._gain_completion
        self._gain_completion = None
        if (completion is not None
                and self._gain_epoch == self._transport_epoch):
            completion()

    def _begin_transport_intent(self) -> int:
        """Invalidate every completion created by an older user command."""
        self._transport_epoch += 1
        return self._transport_epoch

    def _mark_load_phase(self, phase: str):
        if self._load_started:
            self.load_diagnostic.emit(
                phase, (perf_counter() - self._load_started) * 1000.0)

    def _play_clean(self):
        """Start/resume with a short click-prevention gain envelope."""
        if not self._src_path or not self._playback_available:
            return
        self._gain_completion = None
        self._pause_pending = False
        self._pause_clock.invalidate()
        if not self.is_playing():
            self.audio.setVolume(0.0)
            self.player.play()
        self._animate_gain(self._deck_volume, self.FADE_IN_MS)

    def _pause_clean(self, after=None):
        """Fade out, pause, and pin backend + visual clocks to one position."""
        if self._pause_pending:
            if after is not None:
                self._gain_epoch = self._transport_epoch
                self._gain_completion = lambda: self._complete_pause(after)
            return
        if not self.is_playing():
            if after is not None:
                after()
            return
        self._pause_pending = True
        self._pause_floor_ms = max(
            self._pos, self._transport_position(), self.player.position())
        self._pause_clock.restart()
        self._animate_gain(
            0.0, self.FADE_OUT_MS,
            lambda: self._complete_pause(after))

    def _complete_pause(self, after=None):
        # Capture Metal's exact newest committed/scheduled frame at the END of
        # the fade. The ordinary Python/backend positions are millisecond
        # snapshots and can trail the drawable by one refresh; seeking back to
        # either made pause settle a few pixels to the right and then snap
        # left. Native pause now shares the exact-frame hold used by waveform
        # grabbing. Non-native backends retain the interpolated fallback.
        exact_hold = None
        if hasattr(self.player, "begin_visual_hold"):
            exact_hold = float(self.player.begin_visual_hold())
        elapsed = (self._pause_clock.nsecsElapsed() / 1_000_000
                   if self._pause_clock.isValid() else 0.0)
        rate = max(0.0, float(self.player.playbackRate()))
        fade_end = round(self._pause_floor_ms + elapsed * rate)
        target = exact_hold if exact_hold is not None else max(
                fade_end,
                self._transport_position() if self._transport_running else 0,
                self.player.position(),
            )
        if self._dur:
            target = min(target, self._dur)
        self.player.pause()
        self.player.setPosition(target)
        self.audio.setVolume(self._deck_volume)  # silent while paused; ready for cue
        self._pause_pending = False
        self._pause_clock.invalidate()
        self._on_pos(target)
        if (after is None
                and not self._cue_preview_active
                and not self._cue_preview_latched
                and not self._deck_drag_active):
            self._arm_session_cue_relocation()
        if after is not None:
            after()

    def toggle(self):
        self._begin_transport_intent()
        if self._cue_preview_active:
            # Pressing play/Space while a cue is held converts the momentary
            # preview into normal playback. Releasing the cue will no longer
            # stop or return it.
            self._cue_preview_latched = True
            self._session_cue_state = SessionCueState.LATCHED_PLAYBACK
            self._transport_play_active = True
            if not self.is_playing() and not self._pause_pending:
                self._play_clean()
            self._sync_deck_button_states()
            return
        if self._cue_preview_latched:
            # The next play/Space press ends latched playback at its current
            # position; it deliberately does not jump back to the cue.
            self._cue_preview_latched = False
            self._session_cue_state = (
                SessionCueState.SET if self._src_path
                else SessionCueState.UNSET)
            self._cue_preview_control = None
            self._cue_preview_token += 1
            self._transport_play_active = False
            if self.is_playing() or self._pause_pending:
                self._pause_clean()
            self._sync_deck_button_states()
            return
        if self._pause_pending:
            self._transport_play_active = True
            self._play_clean()  # a quick second press cancels the pending pause
        elif self.is_playing():
            self._transport_play_active = False
            self._pause_clean()
        elif self._src_path:
            self._transport_play_active = True
            self._play_clean()
        self._sync_deck_button_states()

    def _cue_press(self):
        """Gate playback from the session cue until release or a Space latch.

        Cue state is session-only. Nothing is written to rekordbox or audio.
        """
        if not self._src_path:
            return
        self._begin_transport_intent()
        if (not self.is_playing() and not self._pause_pending
                and self._session_cue_state
                == SessionCueState.RELOCATION_PENDING):
            self._set_session_cue(self._pos)
            return
        self._start_cue_preview(self._cue_ms, ("session", -1))

    def _cue_release(self):
        """Release the physical CUE control without disturbing another cue."""
        self._release_cue_preview(("session", -1))

    def _marker_cue_press(self, kind: str, slot: int, target: int):
        """Gate playback from one imported hot or memory cue."""
        self._begin_transport_intent()
        self._start_cue_preview(target, (kind, slot))

    def _marker_cue_release(self, kind: str, slot: int):
        self._release_cue_preview((kind, slot))

    def _start_cue_preview(
            self, target: int, control: tuple[str, int]):
        if not self._src_path:
            return
        target = max(0, min(int(target), self._dur) if self._dur else int(target))
        self._cue_preview_token += 1
        token = self._cue_preview_token
        self._cue_preview_active = True
        self._cue_preview_latched = False
        if control == ("session", -1):
            self._session_cue_state = SessionCueState.PREVIEWING
        self._transport_play_active = False
        self._cue_preview_target = target
        self._cue_preview_control = control

        def begin():
            # A very quick release or another cue press can arrive during the
            # 24 ms fade. Never let that stale completion start playback.
            if (token != self._cue_preview_token
                    or not (self._cue_preview_active
                            or self._cue_preview_latched)
                    or self._cue_preview_control != control):
                return
            self._set_position(target)
            self._play_clean()

        if self.is_playing() or self._pause_pending:
            self._pause_clean(begin)
        else:
            begin()
        self._update_transport()

    def _release_cue_preview(self, control: tuple[str, int]):
        if (not self._cue_preview_active
                or self._cue_preview_control != control):
            return
        self._begin_transport_intent()
        target = self._cue_preview_target
        self._cue_preview_active = False
        self._sync_deck_button_states()
        if self._cue_preview_latched:
            # Keep the pending start token/control valid if Space was pressed
            # during the clickless seek fade and the button was released before
            # playback actually began.
            return
        if control == ("session", -1):
            self._session_cue_state = SessionCueState.SET
        self._cue_preview_control = None
        self._cue_preview_token += 1

        def finish():
            self._set_position(target)

        if self.is_playing() or self._pause_pending:
            self._pause_clean(finish)
        else:
            finish()

    def _reset_cue_preview(self):
        self._cue_preview_active = False
        self._cue_preview_latched = False
        self._cue_preview_target = 0
        self._cue_preview_control = None
        self._cue_preview_token += 1
        if self._src_path and self._session_cue_state in {
                SessionCueState.PREVIEWING,
                SessionCueState.LATCHED_PLAYBACK,
        }:
            self._session_cue_state = SessionCueState.SET
        self._sync_deck_button_states()

    def cue(self):
        """Compatibility entry point for a programmatic cue press."""
        self._cue_press()

    def stop(self):
        self._begin_transport_intent()
        self._gain_completion = None
        self._pause_pending = False
        self._pause_clock.invalidate()
        self._transport_play_active = False
        self._reset_cue_preview()
        self.audio.setVolume(self._deck_volume)
        self.player.stop()

    def resynchronize_transport(self):
        """Rejoin controls to the authoritative native state on activation."""
        if hasattr(self.player, "synchronize"):
            self.player.synchronize()
        backend_position = self.player.position()
        backend_playing = self.is_playing()
        if not self._pause_pending:
            self._anchor_transport(backend_position, backend_playing)
            self._paint_position(backend_position, update_transport=False)
        if (not self._cue_preview_active
                and not self._cue_preview_latched
                and not self._pause_pending):
            self._transport_play_active = backend_playing
        self._update_transport()

    # ---- internals ----
    def _beat_delta_ms(self) -> int:
        """Milliseconds in the selected beat/bar jump at headline BPM."""
        return (round(self._jump_beats * 60_000 / self._bpm)
                if self._bpm > 0 else 0)

    def _beat_jump_target(self, direction: int) -> int | None:
        if self._beat_grid:
            # Preserve the playhead's offset within its current beat while using
            # rekordbox's exact timestamps. This remains correct across a
            # variable-tempo grid instead of assuming one headline BPM.
            anchor = max(0, bisect_right(self._beat_grid, self._pos) - 1)
            target_idx = anchor + (
                -self._jump_beats if direction < 0 else self._jump_beats)
            if not 0 <= target_idx < len(self._beat_grid):
                return None
            offset = self._pos - self._beat_grid[anchor]
            target = self._beat_grid[target_idx] + offset
            if target < 0 or not self._dur or target > self._dur:
                return None
            return target
        delta = self._beat_delta_ms()
        target = self._pos + (-delta if direction < 0 else delta)
        if not delta or target < 0 or not self._dur or target > self._dur:
            return None
        return target

    def _beat_jump(self, direction: int):
        target = self._beat_jump_target(direction)
        if target is None:
            return
        self._move_clean(target)

    def _update_transport(self):
        # The transport position path calls this twenty times a second, but
        # every output here is discrete state that changes far more rarely.
        # Re-applying it anyway cost eight display frames per second at
        # maximum zoom in the real app -- each one a doubled scroll step that
        # reads as a shake. Nothing here may run unless something changed.
        loaded = bool(self._src_path) and self._playback_available
        state = (
            loaded,
            loaded and self._beat_jump_target(-1) is not None,
            loaded and self._beat_jump_target(1) is not None,
        )
        if state != self._transport_button_state:
            self._transport_button_state = state
            self.play_btn.setEnabled(loaded)
            self.cue_btn.setEnabled(loaded)
            if hasattr(self, "cue_panel"):
                self.cue_panel.set_loaded(loaded)
            self.beat_back_btn.setEnabled(state[1])
            self.beat_forward_btn.setEnabled(state[2])
        # Cue/play glyph state carries its own change guards and must never be
        # gated behind the signature above: a cue press changes it without
        # changing anything else here.
        self._sync_deck_button_states()

    def _seek(self, frac: float):
        if self._dur > 0:
            target = frac * self._dur
            if self._deck_drag_active:
                self._deck_drag_target = target
            else:
                self._move_clean(target)

    def _set_position(self, target: int | float):
        self.player.setPosition(target)
        self._on_pos(target)

    def _move_clean(self, target: int | float):
        """Move once at silence, resuming only if the deck was playing."""
        if self.is_playing() and not self._pause_pending:
            def finish():
                self._set_position(target)
                self._play_clean()
            self._pause_clean(finish)
        else:
            self._set_position(target)
            self._arm_session_cue_relocation()

    def _arm_session_cue_relocation(self):
        """Make exactly the next paused deck-CUE press set, not preview."""
        if (self._src_path and not self.is_playing()
                and not self._pause_pending):
            self._session_cue_state = SessionCueState.RELOCATION_PENDING

    def _on_wave_drag_started(self):
        if self._deck_drag_active:
            return
        self._deck_drag_active = True
        self._deck_drag_target = None
        self._deck_drag_resume = self.is_playing() and not self._pause_pending
        # Freeze Metal under the hand at its own continuous render clock, then
        # make that exact frame the waveform's relative-drag origin. The older
        # Python poll can lag between frames and caused a small click-side jerk.
        if hasattr(self.player, "begin_visual_hold"):
            exact = float(self.player.begin_visual_hold())
            self._pos = max(0, min(exact, self._dur) if self._dur else exact)
            self.wave.reanchor_drag(self._pos)
        elif hasattr(self.player, "set_visual_position"):
            self.player.set_visual_position(self._pos, True)
        if self._deck_drag_resume:
            # A hand taking the moving waveform owns transport immediately.
            # The ordinary pause envelope intentionally advances to its fade
            # endpoint; using it here lets audio keep travelling after Metal
            # has frozen. Cancel any older ramp, hard-mute, and stop the native
            # clock synchronously at mouse-down. Release still performs the
            # sole decoder seek and uses the normal click-free fade-in.
            self._gain_completion = None
            self._pause_pending = False
            self._pause_clock.invalidate()
            self.audio.setVolume(0.0)
            self.player.pause()
        self._anchor_transport(self._pos, False)

    def _on_wave_preview(self, target: float):
        if (self._deck_drag_active
                and hasattr(self.player, "set_visual_position")):
            self.player.set_visual_position(target, True)

    def _after_drag_paused(self):
        if not self._deck_drag_active and self._deck_drag_target is not None:
            self._finish_wave_drag()

    def _on_wave_drag_finished(self):
        self._deck_drag_active = False
        if not self._pause_pending:
            self._finish_wave_drag()

    def _finish_wave_drag(self):
        target = self._deck_drag_target
        resume = self._deck_drag_resume
        self._deck_drag_target = None
        self._deck_drag_resume = False
        if target is None:
            return
        self._set_position(target)
        if resume:
            self._transport_play_active = True
            self._play_clean()
            self._sync_deck_button_states()
        else:
            self.audio.setVolume(self._deck_volume)
            self._arm_session_cue_relocation()

    def _on_pos(self, ms: int):
        """Apply an authoritative seek/position immediately."""
        self._anchor_transport(ms, self.is_playing())
        self._paint_position(ms)

    def _paint_position(self, ms: int, update_transport: bool = True):
        self._pos = max(0, ms)
        # While the mouse owns the waveform, backend/timer updates continue to
        # keep the transport clock honest but cannot fight the drag preview.
        visual_owned = (
            self.wave.is_dragging() or self._deck_drag_target is not None)
        if not visual_owned:
            self.wave.set_timing(ms, self._dur)
        if not visual_owned and hasattr(self.player, "set_visual_position"):
            self.player.set_visual_position(ms, False)
        if update_transport:
            self._update_transport()

    def _anchor_transport(self, ms: int | float, running: bool):
        self._transport_anchor_ms = float(max(0, ms))
        self._transport_running = running
        self._transport_slew_ms = 0.0
        if running:
            self._transport_clock.restart()
        else:
            self._transport_clock.invalidate()

    def _transport_position(self) -> int:
        pos = self._transport_anchor_ms
        if self._transport_running and self._transport_clock.isValid():
            pos += self._transport_clock.elapsed() * self.player.playbackRate()
        if self._dur:
            pos = min(pos, self._dur)
        return max(0, round(pos))

    def _on_backend_pos(self, ms: int):
        """Keep the visual clock phase-locked to the real audio transport."""
        if not self._transport_running or not self.is_playing():
            self._anchor_transport(ms, False)
            self._paint_position(ms)
            return
        estimate = self._transport_position()
        drift = ms - estimate
        if abs(drift) > 400:
            # A seek, decoder discontinuity, or wake from sleep: hard re-anchor.
            self._anchor_transport(ms, True)
        else:
            # Never apply backend drift as a one-frame position jump. The 60 Hz
            # tick slews this correction at an imperceptible rate instead.
            self._transport_slew_ms = float(drift)
        self._paint_position(self._transport_position())

    def _on_dur(self, ms: int):
        self._dur = ms
        self.wave.set_timing(self._pos, ms)
        self._update_transport()
        self._try_pending_autoplay()

    def _on_state(self, st):
        if (st != PlaybackState.PLAYING
                and not self._pause_pending
                and not self._deck_drag_active
                and not self._cue_preview_active
                and not self._cue_preview_latched):
            self._transport_play_active = False
        self._sync_deck_button_states()
        self.playback_active_changed.emit(st == PlaybackState.PLAYING)
        if st == PlaybackState.PLAYING:
            # Start interpolation from the backend's exact launch/seek point.
            self._anchor_transport(self.player.position(), True)
            self._mark_load_phase("first-audible-output")
        else:
            if self._transport_running:
                estimate = self._transport_position()
                backend = self.player.position()
                stopped = backend if abs(backend - estimate) > 120 else estimate
                self._anchor_transport(stopped, False)
                self._paint_position(stopped)

    def _on_end_of_media(self):
        """Forward only a genuine ordinary-playback terminal edge."""
        ordinary_transport = (
            self._transport_play_active
            and not self._cue_preview_active
            and not self._cue_preview_latched
            and not self._pause_pending)
        track_id = self._track_id
        if ordinary_transport and track_id:
            QTimer.singleShot(
                0, lambda ended=track_id: self.track_ended.emit(ended))

    def _try_pending_autoplay(self):
        """Set a newly selected track on its first grid beat, then maybe play."""
        if not self._pending_autoplay or self._pending_start_ms is None:
            return
        # A nonzero seek is only reliable after Qt has loaded the local media
        # and reported its duration. Grid parsing and media loading race.
        if self._pending_start_ms > 0 and self._dur <= 0:
            return
        start = max(0, self._pending_start_ms)
        if self._dur:
            start = min(start, self._dur)
        should_play = self._pending_should_play
        self._pending_autoplay = False
        self._pending_should_play = True
        self._pending_start_ms = None
        self._set_session_cue(start)
        self.player.setPosition(start)
        self._on_pos(start)
        if should_play:
            self._transport_play_active = True
            self._sync_deck_button_states()
            self._play_clean()
        else:
            self._transport_play_active = False
            self._sync_deck_button_states()

    def _set_session_cue(self, position_ms: int | None):
        """Keep both renderers on one ephemeral deck-CUE timestamp."""
        if position_ms is not None:
            self._cue_ms = max(0, int(position_ms))
            self._session_cue_state = SessionCueState.SET
        else:
            self._session_cue_state = SessionCueState.UNSET
        self.wave.set_session_cue(position_ms)
        if hasattr(self.player, "set_session_cue"):
            self.player.set_session_cue(position_ms)

    def _start_waveform(self, path: str, analysis_path: str = ""):
        # Interrupt the previous decode first: rapid track changes must not pile
        # up multi-second AAC/ALAC analysis threads.
        if self._wf is not None and _thread_running(self._wf):
            self._wf.requestInterruption()
        w = WaveformWorker(
            path, analysis_path=analysis_path,
            track_id=self._track_id or "",
            track_analysis_dir=self.track_analysis_dir,
            generation=self._analysis_generation,
            native_backend=(
                self.player if getattr(self.player, "is_native", False)
                else None))
        self._wf = w
        self._wf_pending.append(w)  # strong ref until finished (never GC mid-run)
        w.manifest_ready.connect(
            lambda src, gen, data, ap=analysis_path:
            self._on_analysis_manifest(src, ap, gen, data))
        w.overview_ready.connect(
            lambda src, gen, data, ap=analysis_path:
            self._on_analysis_overview(src, ap, gen, data))
        w.detail_ready.connect(
            lambda src, gen, data, ap=analysis_path:
            self._on_analysis_detail(src, ap, gen, data))
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
    def shutdown(self):
        """Release workers plus any native audio/display threads."""
        self.shutdown_waveforms()
        self.player.close()

    def _analysis_is_current(
            self, path: str, analysis_path: str, generation: int) -> bool:
        return (
            generation == self._analysis_generation
            and path == self._src_path
            and analysis_path == self._analysis_path
        )

    def _on_analysis_manifest(
            self, path: str, analysis_path: str,
            generation: int, data: dict):
        if not self._analysis_is_current(path, analysis_path, generation):
            return
        prepared = _apply_loaded_grid_contract(data, self._analysis_path)
        if self._live_cues is not None:
            prepared = {**prepared, "cues": list(self._live_cues)}
        self._analysis_parts = dict(prepared)
        # The manifest is transport-critical: it positions the paused deck on
        # the first exact grid row without waiting for waveform decompression.
        self.wave.set_analysis(prepared)
        cues = list(prepared.get("cues") or [])
        self.cue_panel.set_cues(cues)
        if hasattr(self.player, "set_cues"):
            self.player.set_cues(cues)
        self._beat_grid = self.wave._beat_times
        self._mark_load_phase("manifest-ready")
        self._mark_load_phase("cues-grid-visible")
        if self._pending_autoplay:
            self._pending_start_ms = self._beat_grid[0] if self._beat_grid else 0
            self._try_pending_autoplay()
        self._update_transport()

    def _on_analysis_overview(
            self, path: str, analysis_path: str,
            generation: int, data: dict):
        if not self._analysis_is_current(path, analysis_path, generation):
            return
        self._analysis_parts.update(data)
        self.wave.set_analysis(self._analysis_parts)
        if (hasattr(self.player, "set_analysis")
                and not data.get("_native_uploaded")):
            self.player.set_analysis(self._analysis_parts, generation)
        self._mark_load_phase("first-colored-overview")

    def _on_analysis_detail(
            self, path: str, analysis_path: str,
            generation: int, data: dict):
        if not self._analysis_is_current(path, analysis_path, generation):
            return
        self._analysis_parts.update(data)
        detail = data.get("_audio_detail")
        self.wave.set_audio_detail(detail)
        if (hasattr(self.player, "set_audio_detail")
                and not data.get("_native_uploaded")):
            self.player.set_audio_detail(detail, generation)
        self._mark_load_phase("ultra-detail-promoted")

    def _apply_analysis(self, data: dict):
        data = _apply_loaded_grid_contract(data, self._analysis_path)
        if self._live_cues is not None:
            data = dict(data)
            data["cues"] = list(self._live_cues)
        self.wave.set_analysis(data)
        self.cue_panel.set_cues(list(data.get("cues") or []))
        if hasattr(self.player, "set_analysis"):
            self.player.set_analysis(data)
        # WaveformBar already validated/sorted these timestamps. Reusing that
        # exact list avoids walking a thousand-beat grid twice on every load.
        self._beat_grid = self.wave._beat_times
        self._update_transport()
        if self._pending_autoplay:
            self._pending_start_ms = self._beat_grid[0] if self._beat_grid else 0
            self._try_pending_autoplay()

    def refresh_audio_detail(self, path: str):
        """Apply a just-finished canonical V4 record to the current deck."""
        if path != self._src_path:
            return
        if self.track_analysis_dir and self._track_id:
            self._start_waveform(path, self._analysis_path)
