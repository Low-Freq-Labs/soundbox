"""Qt signal adapter for Soundbox's native CoreAudio + Metal deck.

The dylib owns the real-time audio callback and display-synchronized renderer.
Python never runs on the audio or Metal threads.  Qt Multimedia remains in the
application solely for AAC/ALAC waveform decoding; it is not a playback engine.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import sys
import time
from enum import IntEnum
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication

LOG = logging.getLogger("soundbox")
_HOT_CUE_FALLBACKS = (
    (255, 55, 111), (69, 172, 219), (125, 193, 61), (170, 114, 255),
    (48, 210, 110), (224, 100, 27), (48, 90, 255), (195, 175, 4),
)
_MEMORY_CUE_RGB = (255, 52, 52)
_WAVEFORM_MODES = {"BLUE": 1, "RGB": 2, "3BAND": 3}
_NATIVE_ABI_VERSION = 4
_NATIVE_GEOMETRY_EVENTS = tuple(
    event_type
    for event_type in (
        QEvent.Resize,
        QEvent.Show,
        getattr(QEvent.Type, "DevicePixelRatioChange", None),
        getattr(QEvent.Type, "ScreenChangeInternal", None),
    )
    if event_type is not None
)


def _repair_pwv5_visibility(
        packed: bytes, overview_heights: bytes,
        height_encoding: str) -> bytes:
    """Give audible zero-height PWV5 rows a color carrier, without recooking.

    Rekordbox PWV5 may encode a row as all zero even when SoundBox's independent
    decoded envelope proves that the audio is nonzero. Metal correctly keeps
    the decoded contour, but an all-zero PWV5 row also has zero color weight,
    so that real contour is painted black and appears to vanish. Copying only
    the nearest valid PWV5 color bits and assigning the minimum representable
    PWV5 height keeps the exact audio geometry while making it visible.
    """
    width = 2 if height_encoding == "linear-u16le" else 1
    count = min(len(packed) // 2, len(overview_heights) // width)
    if count <= 0:
        return packed

    values = [
        int.from_bytes(packed[index * 2:index * 2 + 2], "big")
        for index in range(count)
    ]
    audible = [
        int.from_bytes(
            overview_heights[index * width:(index + 1) * width],
            "little" if width == 2 else "big") > 0
        for index in range(count)
    ]
    valid = [(value & 0xff80) != 0 for value in values]
    if not any(valid):
        return packed

    previous = [-1] * count
    source = -1
    for index, is_valid in enumerate(valid):
        if is_valid:
            source = index
        previous[index] = source
    following = [-1] * count
    source = -1
    for index in range(count - 1, -1, -1):
        if valid[index]:
            source = index
        following[index] = source

    repaired = bytearray(packed)
    changed = False
    for index, value in enumerate(values):
        if not audible[index] or (value & 0x007c) != 0:
            continue
        color_bits = value & 0xff80
        if color_bits == 0:
            before = previous[index]
            after = following[index]
            nearest = (
                after if before < 0
                else before if after < 0
                else before if index - before <= after - index
                else after
            )
            if nearest < 0:
                continue
            color_bits = values[nearest] & 0xff80
        repaired[index * 2:index * 2 + 2] = (
            color_bits | 0x0004).to_bytes(2, "big")
        changed = True
    return bytes(repaired) if changed else packed


def _repair_soundbox_pwv5_color(
        packed: bytes, overview_rgb: bytes) -> bytes:
    """Restore old SoundBox fallback color from its canonical compact rows.

    Early SoundBox V5 cooks fed compact overview channel codes back through a
    display-range quantizer. The cached overview is already correct, and both
    streams share the exact 150 Hz clock, so replace only PWV5's color bits and
    retain every cached height/control bit. Rekordbox-authored PWV5 never calls
    this repair.
    """
    count = min(len(packed) // 2, len(overview_rgb) // 3)
    if count <= 0:
        return packed
    import numpy as np

    values = np.frombuffer(packed, dtype=">u2", count=count)
    colors = np.frombuffer(
        overview_rgb, dtype=np.uint8, count=count * 3
    ).reshape(-1, 3).astype(np.uint16)
    color_bits = (
        ((np.minimum(colors[:, 0], 14) & 0x0e) << 12)
        | (np.minimum(colors[:, 1], 7) << 10)
        | (np.minimum(colors[:, 2], 7) << 7)
    )
    repaired = (color_bits | (values & 0x007f)).astype(
        ">u2", copy=False).tobytes()
    return repaired + packed[count * 2:]


class PlaybackState(IntEnum):
    """Backend-neutral transport states shared by the deck and its UI."""

    STOPPED = 0
    PLAYING = 1
    PAUSED = 2


class _Snapshot(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("playback_state", ctypes.c_uint32),
        ("position_frames", ctypes.c_uint64),
        ("duration_frames", ctypes.c_uint64),
        ("sample_rate", ctypes.c_double),
        ("underruns", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("ramp_completed", ctypes.c_uint64),
        ("error_code", ctypes.c_int32),
        ("error_message", ctypes.c_char * 256),
    ]


class _FrameTraceStatus(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("active", ctypes.c_uint32),
        ("max_frames", ctypes.c_uint32),
        ("submitted_frames", ctypes.c_uint32),
        ("gpu_completed_frames", ctypes.c_uint32),
        ("presentation_completed_frames", ctypes.c_uint32),
        ("crop_width_pixels", ctypes.c_uint32),
        ("crop_height_pixels", ctypes.c_uint32),
        ("bytes_per_row", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("capture_bytes", ctypes.c_uint64),
    ]


def _library_candidates() -> list[Path]:
    candidates: list[Path] = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        root = Path(frozen)
        candidates.extend([
            root / "native" / "libsoundbox_deck.dylib",
            root / "libsoundbox_deck.dylib",
        ])
    repo = Path(__file__).resolve().parent.parent
    candidates.extend([
        repo / "build" / "native" / "libsoundbox_deck.dylib",
        repo / "native" / "libsoundbox_deck.dylib",
    ])
    return candidates


def native_library_path() -> Path | None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None
    return next((p for p in _library_candidates() if p.is_file()), None)


def native_available() -> bool:
    app = QGuiApplication.instance()
    if app is not None and QGuiApplication.platformName() != "cocoa":
        return False
    path = native_library_path()
    if path is None:
        return False
    try:
        lib = ctypes.CDLL(str(path))
        lib.sb_deck_abi_version.restype = ctypes.c_uint32
        lib.sb_deck_is_supported.restype = ctypes.c_int
        return (
            lib.sb_deck_abi_version() == _NATIVE_ABI_VERSION
            and bool(lib.sb_deck_is_supported())
        )
    except (OSError, AttributeError):
        return False


def _bind(path: Path):
    lib = ctypes.CDLL(str(path))
    handle = ctypes.c_void_p
    lib.sb_deck_abi_version.restype = ctypes.c_uint32
    lib.sb_deck_is_supported.restype = ctypes.c_int
    lib.sb_deck_create.argtypes = [ctypes.c_void_p]
    lib.sb_deck_create.restype = handle
    lib.sb_deck_destroy.argtypes = [handle]
    lib.sb_deck_load.argtypes = [handle, ctypes.c_char_p]
    lib.sb_deck_load.restype = ctypes.c_int
    lib.sb_deck_begin_track.argtypes = [handle, ctypes.c_uint64]
    lib.sb_deck_begin_track.restype = ctypes.c_int
    lib.sb_deck_play.argtypes = [handle]
    lib.sb_deck_pause.argtypes = [handle]
    lib.sb_deck_stop.argtypes = [handle]
    lib.sb_deck_seek_frames.argtypes = [handle, ctypes.c_uint64]
    lib.sb_deck_set_gain.argtypes = [handle, ctypes.c_float]
    lib.sb_deck_ramp_gain.argtypes = [
        handle, ctypes.c_float, ctypes.c_double,
    ]
    lib.sb_deck_ramp_gain.restype = ctypes.c_uint64
    lib.sb_deck_set_preview_frame.argtypes = [
        handle, ctypes.c_uint64, ctypes.c_int,
    ]
    lib.sb_deck_begin_preview_hold.argtypes = [handle]
    lib.sb_deck_begin_preview_hold.restype = ctypes.c_uint64
    lib.sb_deck_begin_waveform_update.argtypes = [handle]
    lib.sb_deck_end_waveform_update.argtypes = [handle]
    lib.sb_deck_set_zoom.argtypes = [handle, ctypes.c_float]
    lib.sb_deck_set_zoom.restype = ctypes.c_float
    lib.sb_deck_request_display.argtypes = [handle]
    lib.sb_deck_sync_view_geometry.argtypes = [handle]
    lib.sb_deck_set_analysis.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.sb_deck_set_analysis.restype = ctypes.c_int
    lib.sb_deck_set_packed_analysis_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.sb_deck_set_packed_analysis_for_generation.restype = ctypes.c_int
    lib.sb_deck_set_packed_overview_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.sb_deck_set_packed_overview_for_generation.restype = ctypes.c_int
    lib.sb_deck_set_packed_overview_u16_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.sb_deck_set_packed_overview_u16_for_generation.restype = ctypes.c_int
    lib.sb_deck_set_detail_analysis.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_detail_analysis.restype = ctypes.c_int
    lib.sb_deck_set_detail_bands.argtypes = [
        handle, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    lib.sb_deck_set_detail_bands.restype = ctypes.c_int
    lib.sb_deck_set_waveform_streams.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_waveform_streams.restype = ctypes.c_int
    lib.sb_deck_set_waveform_mode.argtypes = [handle, ctypes.c_uint32]
    lib.sb_deck_set_waveform_mode.restype = ctypes.c_int
    lib.sb_deck_waveform_mode.argtypes = [handle]
    lib.sb_deck_waveform_mode.restype = ctypes.c_uint32
    lib.sb_deck_set_spectral_analysis.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_spectral_analysis.restype = ctypes.c_int
    lib.sb_deck_set_audio_detail.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail.restype = ctypes.c_int
    lib.sb_deck_set_audio_detail_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail_for_generation.restype = ctypes.c_int
    lib.sb_deck_set_audio_detail_u16_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail_u16_for_generation.restype = ctypes.c_int
    lib.sb_deck_set_audio_detail_polarity_u16_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail_polarity_u16_for_generation.restype = (
        ctypes.c_int)
    lib.sb_deck_set_audio_detail_polarity_range_u16_i16_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail_polarity_range_u16_i16_for_generation.restype = (
        ctypes.c_int)
    lib.sb_deck_set_audio_detail_range_mips_i16_for_generation.argtypes = [
        handle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_double,
    ]
    lib.sb_deck_set_audio_detail_range_mips_i16_for_generation.restype = (
        ctypes.c_int)
    lib.sb_deck_set_cues.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
    ]
    lib.sb_deck_set_cues.restype = ctypes.c_int
    lib.sb_deck_set_session_cue.argtypes = [
        handle, ctypes.c_uint32, ctypes.c_int,
    ]
    lib.sb_deck_set_session_cue.restype = ctypes.c_int
    lib.sb_deck_snapshot.argtypes = [handle, ctypes.POINTER(_Snapshot)]
    lib.sb_deck_snapshot.restype = ctypes.c_int
    lib.sb_deck_has_ended.argtypes = [handle]
    lib.sb_deck_has_ended.restype = ctypes.c_int
    lib.sb_deck_visual_frame.argtypes = [handle]
    lib.sb_deck_visual_frame.restype = ctypes.c_uint64
    lib.sb_deck_presented_frame.argtypes = [handle]
    lib.sb_deck_presented_frame.restype = ctypes.c_uint64
    lib.sb_deck_scheduled_frame.argtypes = [handle]
    lib.sb_deck_scheduled_frame.restype = ctypes.c_uint64
    lib.sb_deck_analysis_sample_count.argtypes = [handle]
    lib.sb_deck_analysis_sample_count.restype = ctypes.c_uint64
    lib.sb_deck_analysis_sample_rate.argtypes = [handle]
    lib.sb_deck_analysis_sample_rate.restype = ctypes.c_double
    lib.sb_deck_detail_sample_count.argtypes = [handle]
    lib.sb_deck_detail_sample_count.restype = ctypes.c_uint64
    lib.sb_deck_detail_sample_rate.argtypes = [handle]
    lib.sb_deck_detail_sample_rate.restype = ctypes.c_double
    lib.sb_deck_detail_peak_height.argtypes = [handle]
    lib.sb_deck_detail_peak_height.restype = ctypes.c_double
    lib.sb_deck_audio_detail_mip_count.argtypes = [handle]
    lib.sb_deck_audio_detail_mip_count.restype = ctypes.c_uint64
    lib.sb_deck_audio_detail_buffer_bytes.argtypes = [handle]
    lib.sb_deck_audio_detail_buffer_bytes.restype = ctypes.c_uint64
    lib.sb_deck_cue_marker_count.argtypes = [handle]
    lib.sb_deck_cue_marker_count.restype = ctypes.c_uint64
    lib.sb_deck_pixels_per_second.argtypes = [handle]
    lib.sb_deck_pixels_per_second.restype = ctypes.c_double
    lib.sb_deck_display_callback_count.argtypes = [handle]
    lib.sb_deck_display_callback_count.restype = ctypes.c_uint64
    lib.sb_deck_display_average_fps.argtypes = [handle]
    lib.sb_deck_display_average_fps.restype = ctypes.c_double
    lib.sb_deck_display_max_gap_ms.argtypes = [handle]
    lib.sb_deck_display_max_gap_ms.restype = ctypes.c_double
    lib.sb_deck_presentation_error_mean_ms.argtypes = [handle]
    lib.sb_deck_presentation_error_mean_ms.restype = ctypes.c_double
    lib.sb_deck_presentation_error_stddev_ms.argtypes = [handle]
    lib.sb_deck_presentation_error_stddev_ms.restype = ctypes.c_double
    lib.sb_deck_presentation_error_max_ms.argtypes = [handle]
    lib.sb_deck_presentation_error_max_ms.restype = ctypes.c_double
    lib.sb_deck_reset_display_stats.argtypes = [handle]
    lib.sb_deck_begin_frame_trace.argtypes = [
        handle, ctypes.c_uint32, ctypes.c_uint32,
    ]
    lib.sb_deck_begin_frame_trace.restype = ctypes.c_int
    lib.sb_deck_frame_trace_status.argtypes = [
        handle, ctypes.POINTER(_FrameTraceStatus),
    ]
    lib.sb_deck_frame_trace_status.restype = ctypes.c_int
    lib.sb_deck_finish_frame_trace.argtypes = [
        handle, ctypes.c_char_p,
    ]
    lib.sb_deck_finish_frame_trace.restype = ctypes.c_int
    lib.sb_deck_cancel_frame_trace.argtypes = [handle]
    lib.sb_deck_view_geometry.argtypes = [
        handle,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.sb_deck_view_geometry.restype = ctypes.c_int
    return lib


class NativeGainControl(QObject):
    """Volume control for the native deck."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self._player = player
        self._volume = 0.9

    def setVolume(self, volume: float):
        self._volume = min(1.0, max(0.0, float(volume)))
        self._player.set_gain(self._volume)

    def volume(self) -> float:
        return self._volume


class UnavailableDeckBackend(QObject):
    """Inert backend that keeps the library usable after native init failure."""

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    endOfMedia = Signal()
    errorOccurred = Signal(object, str)
    is_native = False

    def __init__(self, reason: str, parent=None):
        super().__init__(parent)
        self.reason = str(reason)
        self._position = 0

    def setAudioOutput(self, _audio):
        pass

    def setSource(self, _source):
        self._position = 0
        self.positionChanged.emit(0)
        self.durationChanged.emit(0)

    def play(self):
        self.errorOccurred.emit(None, self.reason)

    def pause(self):
        pass

    def stop(self):
        pass

    def setPosition(self, ms: int):
        self._position = max(0, int(ms))
        self.positionChanged.emit(self._position)

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return 0

    def playbackRate(self) -> float:
        return 1.0

    def playbackState(self):
        return PlaybackState.STOPPED

    def ramp_gain(self, _gain: float, _duration_ms: int, completion=None):
        if completion is not None:
            completion()

    def set_gain(self, _gain: float):
        pass

    def set_visual_position(self, _ms: int, _preview: bool):
        pass

    def begin_visual_hold(self) -> int:
        return self._position

    def set_zoom(self, pixels_per_second: float):
        return float(pixels_per_second)

    def set_analysis(self, _analysis, _generation: int = 0):
        pass

    def begin_track(self, _generation: int):
        pass

    def set_overview(self, _analysis, _generation: int = 0):
        pass

    def set_complete_analysis(
            self, overview, detail, _generation: int = 0):
        self.set_overview(overview, _generation)
        self.set_audio_detail(detail, _generation)
        return (True, True)

    def set_cues(self, _cues):
        pass

    def set_session_cue(self, _position_ms):
        pass

    def set_audio_detail(self, _detail, _generation: int = 0):
        pass

    def synchronize(self):
        pass

    def close(self):
        pass


class NativeDeckBackend(QObject):
    """Small Qt-signalled facade over the native dylib."""

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    endOfMedia = Signal()
    errorOccurred = Signal(object, str)
    is_native = True

    def __init__(self, host_widget, parent=None):
        super().__init__(parent)
        if QGuiApplication.platformName() != "cocoa":
            raise RuntimeError("Native deck requires Qt's macOS cocoa platform")
        path = native_library_path()
        if path is None:
            raise RuntimeError("Native Soundbox deck library is unavailable")
        self._lib = _bind(path)
        if self._lib.sb_deck_abi_version() != _NATIVE_ABI_VERSION:
            raise RuntimeError("Native Soundbox deck ABI mismatch")
        self._handle = self._lib.sb_deck_create(
            ctypes.c_void_p(int(host_widget.winId())))
        if not self._handle:
            raise RuntimeError("Native CoreAudio/Metal deck could not initialize")
        self._host_widget = host_widget
        self._host_widget.installEventFilter(self)
        self.sync_view_geometry()
        self._snapshot = _Snapshot()
        self._state = PlaybackState.STOPPED
        self._position_ms = 0
        self._last_position_emit = 0.0
        self._duration_ms = 0
        self._loading = False
        self._sample_rate = 44100.0
        self._last_error: tuple[int, int] = (0, 0)
        self._ramp_token = 0
        self._analysis_generation = 0
        self._end_emitted = False
        self._ramp_completion = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(125)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        LOG.info("Native CoreAudio/Metal deck active: %s", path)

    def setAudioOutput(self, _audio):
        pass

    def setSource(self, source: QUrl):
        path = source.toLocalFile()
        self._end_emitted = False
        self._loading = bool(path)
        self._set_poll_interval(8)
        self.set_cues([])
        self.set_session_cue(None)
        self._position_ms = 0
        self._duration_ms = 0
        self.positionChanged.emit(0)
        self.durationChanged.emit(0)
        self._set_state(PlaybackState.STOPPED)
        if not path or not self._lib.sb_deck_load(
                self._handle, path.encode("utf-8")):
            self.errorOccurred.emit(None, "Native deck could not load the track")

    def begin_track(self, generation: int):
        """Atomically retire every renderer buffer before source replacement."""
        self._analysis_generation = max(0, int(generation))
        return bool(self._lib.sb_deck_begin_track(
            self._handle, self._analysis_generation))

    def play(self):
        self._set_poll_interval(8)
        self._lib.sb_deck_play(self._handle)

    def pause(self):
        self._lib.sb_deck_pause(self._handle)
        # PlayerBar fade completions immediately continue into source changes.
        # Publish pause synchronously so that continuation cannot observe the
        # previous poll's PlayingState and enqueue a second, unfinishable fade
        # on an engine that is already paused.
        if self._state != PlaybackState.STOPPED:
            self._set_state(PlaybackState.PAUSED)

    def stop(self):
        self._lib.sb_deck_stop(self._handle)

    def setPosition(self, ms: int | float):
        precise_ms = max(0.0, float(ms))
        frame = round(precise_ms * self._sample_rate / 1000)
        self._lib.sb_deck_seek_frames(self._handle, frame)
        self._position_ms = round(precise_ms)
        self.positionChanged.emit(self._position_ms)

    def request_display(self):
        if self._handle:
            self._lib.sb_deck_request_display(self._handle)

    def sync_view_geometry(self):
        """Commit the Qt host size to its AppKit/Metal child immediately."""
        if self._handle:
            self._lib.sb_deck_sync_view_geometry(self._handle)

    def eventFilter(self, watched, event):
        if (watched is self._host_widget
                and event.type() in _NATIVE_GEOMETRY_EVENTS):
            self.sync_view_geometry()
        return super().eventFilter(watched, event)

    def synchronize(self):
        """Pull one authoritative native snapshot immediately."""
        self._poll(force_emit=True)

    def position(self) -> int:
        return self._position_ms

    def duration(self) -> int:
        return self._duration_ms

    def playbackRate(self) -> float:
        return 1.0

    def playbackState(self):
        return self._state

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def _set_poll_interval(self, interval: int):
        interval = max(8, int(interval))
        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)

    def diagnostic_visual_frame(self) -> int:
        """Current continuous Metal clock position, for regression tests."""
        return int(self._lib.sb_deck_visual_frame(self._handle))

    def diagnostic_presented_frame(self) -> int:
        """Source frame Metal confirms was most recently shown onscreen."""
        return int(self._lib.sb_deck_presented_frame(self._handle))

    def diagnostic_scheduled_frame(self) -> int:
        """Newest source frame already committed for presentation."""
        return int(self._lib.sb_deck_scheduled_frame(self._handle))

    def diagnostic_analysis_shape(self) -> tuple[int, float]:
        """Uploaded sample count/rate, for native cache regressions."""
        return (
            int(self._lib.sb_deck_analysis_sample_count(self._handle)),
            float(self._lib.sb_deck_analysis_sample_rate(self._handle)),
        )

    def diagnostic_detail_shape(self) -> tuple[int, float]:
        """Close-zoom Rekordbox sample count/rate."""
        return (
            int(self._lib.sb_deck_detail_sample_count(self._handle)),
            float(self._lib.sb_deck_detail_sample_rate(self._handle)),
        )

    def diagnostic_detail_peak(self) -> float:
        return float(self._lib.sb_deck_detail_peak_height(self._handle))

    def diagnostic_audio_detail_mip_count(self) -> int:
        return int(self._lib.sb_deck_audio_detail_mip_count(self._handle))

    def diagnostic_audio_detail_buffer_bytes(self) -> int:
        return int(
            self._lib.sb_deck_audio_detail_buffer_bytes(self._handle))

    def diagnostic_cue_marker_count(self) -> int:
        return int(self._lib.sb_deck_cue_marker_count(self._handle))

    def diagnostic_zoom(self) -> float:
        return float(self._lib.sb_deck_pixels_per_second(self._handle))

    def diagnostic_waveform_mode(self) -> str:
        value = int(self._lib.sb_deck_waveform_mode(self._handle))
        return next(
            (name for name, encoded in _WAVEFORM_MODES.items()
             if encoded == value),
            "",
        )

    def diagnostic_display_stats(self) -> tuple[int, float, float]:
        """Actual drawable presentations, mean FPS, and largest real gap."""
        return (
            int(self._lib.sb_deck_display_callback_count(self._handle)),
            float(self._lib.sb_deck_display_average_fps(self._handle)),
            float(self._lib.sb_deck_display_max_gap_ms(self._handle)),
        )

    def diagnostic_presentation_error(self) -> tuple[float, float, float]:
        """Mean, stddev, and worst |presentedTime - target| in ms.

        Position is predicted for the target presentation timestamp. A tight
        spread means the visible frame lands where the clock says it will, so
        residual motion complaints are reconstruction, not timing. A wide one
        means the display itself is adding positional jitter.
        """
        return (
            float(
                self._lib.sb_deck_presentation_error_mean_ms(self._handle)),
            float(
                self._lib.sb_deck_presentation_error_stddev_ms(
                    self._handle)),
            float(self._lib.sb_deck_presentation_error_max_ms(self._handle)),
        )

    def diagnostic_reset_display_stats(self):
        self._lib.sb_deck_reset_display_stats(self._handle)

    def diagnostic_underruns(self) -> int:
        """Current cumulative CoreAudio output-buffer underrun count."""
        self.synchronize()
        return int(self._snapshot.underruns)

    def diagnostic_begin_frame_trace(
            self, max_frames: int, crop_width_pixels: int = 768):
        """Arm a bounded exact-drawable capture with no display-thread I/O."""
        max_frames = int(max_frames)
        crop_width_pixels = int(crop_width_pixels)
        if max_frames < 1 or crop_width_pixels < 0:
            raise ValueError("invalid Metal frame-trace bounds")
        if not self._lib.sb_deck_begin_frame_trace(
                self._handle, max_frames, crop_width_pixels):
            raise RuntimeError(
                "Metal frame trace could not allocate its bounded capture")
        return self.diagnostic_frame_trace_status()

    def diagnostic_frame_trace_status(self) -> dict[str, int | bool]:
        status = _FrameTraceStatus()
        if not self._lib.sb_deck_frame_trace_status(
                self._handle, ctypes.byref(status)):
            raise RuntimeError("Metal frame-trace status is unavailable")
        return {
            "active": bool(status.active),
            "max_frames": int(status.max_frames),
            "submitted_frames": int(status.submitted_frames),
            "gpu_completed_frames": int(status.gpu_completed_frames),
            "presentation_completed_frames": int(
                status.presentation_completed_frames),
            "crop_width_pixels": int(status.crop_width_pixels),
            "crop_height_pixels": int(status.crop_height_pixels),
            "bytes_per_row": int(status.bytes_per_row),
            "capture_bytes": int(status.capture_bytes),
        }

    def diagnostic_finish_frame_trace(self, output_directory: Path):
        """Write a stopped trace's raw pixels and per-drawable timing ledger."""
        output = Path(output_directory).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        if not self._lib.sb_deck_finish_frame_trace(
                self._handle, str(output).encode("utf-8")):
            raise RuntimeError(
                f"Metal frame trace could not be finalized under {output}")

    def diagnostic_cancel_frame_trace(self):
        if self._handle:
            self._lib.sb_deck_cancel_frame_trace(self._handle)

    def diagnostic_view_geometry(self) -> tuple[float, float, float]:
        """Parent/view widths in points and Metal drawable width in pixels."""
        parent = ctypes.c_double()
        view = ctypes.c_double()
        drawable = ctypes.c_double()
        if not self._lib.sb_deck_view_geometry(
                self._handle,
                ctypes.byref(parent),
                ctypes.byref(view),
                ctypes.byref(drawable)):
            return (0.0, 0.0, 0.0)
        return (parent.value, view.value, drawable.value)

    def set_gain(self, gain: float):
        if self._handle:
            self._ramp_completion = None
            self._lib.sb_deck_set_gain(self._handle, float(gain))

    def ramp_gain(self, gain: float, duration_ms: int, completion=None):
        self._ramp_completion = completion
        self._set_poll_interval(8)
        self._ramp_token = self._lib.sb_deck_ramp_gain(
            self._handle, float(gain), float(duration_ms))

    def set_visual_position(self, ms: int | float, preview: bool):
        frame = round(max(0.0, float(ms)) * self._sample_rate / 1000)
        self._lib.sb_deck_set_preview_frame(
            self._handle, frame, int(bool(preview)))

    def begin_visual_hold(self) -> float:
        """Atomically freeze Metal at the exact frame already on screen."""
        frame = int(self._lib.sb_deck_begin_preview_hold(self._handle))
        precise_ms = frame / max(1.0, self._sample_rate) * 1000.0
        self._position_ms = round(max(0.0, precise_ms))
        return max(0.0, precise_ms)

    def set_zoom(self, pixels_per_second: float):
        return float(self._lib.sb_deck_set_zoom(
            self._handle, float(pixels_per_second)))

    def set_waveform_mode(self, mode: str):
        value = _WAVEFORM_MODES.get(str(mode).upper(), 3)
        self._lib.sb_deck_set_waveform_mode(self._handle, value)
        return next(
            name for name, encoded in _WAVEFORM_MODES.items()
            if encoded == value)

    @staticmethod
    def _screen_rgb(raw) -> tuple[int, int, int]:
        r, g, b = (list(raw) + [0, 0, 0])[:3]
        values = [min(255, int(r * 18)),
                  min(255, int(g * 36)),
                  min(255, int(b * 36))]
        brightest = max(values)
        if 0 < brightest < 155:
            boost = 155 / brightest
            values = [min(255, int(v * boost)) for v in values]
        return tuple(values)

    def set_analysis(self, data: dict, generation: int = 0):
        packed_heights = data.get("_overview_heights")
        packed_rgb = data.get("_overview_rgb")
        if (data.get("_height_encoding") == "linear-u16le"
                and isinstance(packed_heights, bytes)
                and isinstance(packed_rgb, bytes)):
            result = self.set_overview(data, generation)
            self._set_detail_bands(data.get("_detail_bands"))
            self._set_audio_detail(data.get("_audio_detail"), generation)
            self.set_cues(data.get("cues") or [])
            return result
        if (isinstance(packed_heights, bytes)
                and isinstance(packed_rgb, bytes)):
            count = min(len(packed_heights), len(packed_rgb) // 3)
            if count:
                beats = [
                    row for row in (data.get("beats") or [])
                    if isinstance(row, (list, tuple)) and len(row) >= 2
                ]
                beat_count = len(beats)
                seconds_arr = ((ctypes.c_double * beat_count)(
                    *(float(row[1]) / 1000 for row in beats))
                    if beat_count else None)
                beat_arr = ((ctypes.c_uint8 * beat_count)(
                    *(max(0, min(255, int(row[0]))) for row in beats))
                    if beat_count else None)
                h_arr = (ctypes.c_uint8 * count).from_buffer_copy(
                    packed_heights[:count])
                rgb_arr = (ctypes.c_uint8 * (count * 3)).from_buffer_copy(
                    packed_rgb[:count * 3])
                expected = int(generation or self._analysis_generation)
                return bool(
                    self._lib.sb_deck_set_packed_analysis_for_generation(
                        self._handle, expected, h_arr, rgb_arr, count,
                        float(data.get("sample_rate") or 150.0),
                        seconds_arr, beat_arr, beat_count))
        raw_heights = list(data.get("heights") or [])
        raw_colors = list(data.get("colors") or [])
        raw_count = min(len(raw_heights), len(raw_colors))
        columns = list(data.get("columns") or [])
        if columns:
            heights = [float(row[0]) for row in columns
                       if isinstance(row, (list, tuple)) and len(row) >= 2]
            colors = [row[1] for row in columns
                      if isinstance(row, (list, tuple)) and len(row) >= 2]
            rate = 92.0
        else:
            heights = list(data.get("heights") or [])
            colors = list(data.get("colors") or [])
            rate = float(data.get("sample_rate") or 150.0)
        count = min(len(heights), len(colors))
        beats = [row for row in (data.get("beats") or [])
                 if isinstance(row, (list, tuple)) and len(row) >= 2]
        if not count:
            self._lib.sb_deck_set_analysis(
                self._handle, None, None, 0, 0.0, None, None, 0)
            self.set_cues(data.get("cues") or [])
            return
        h_arr = (ctypes.c_float * count)(
            *(max(0.0, min(1.0, float(v))) for v in heights[:count]))
        packed = []
        for raw in colors[:count]:
            packed.extend(self._screen_rgb(raw))
        rgb_arr = (ctypes.c_uint8 * (count * 3))(*packed)
        beat_count = len(beats)
        seconds_arr = ((ctypes.c_double * beat_count)(
            *(float(row[1]) / 1000 for row in beats))
            if beat_count else None)
        beat_arr = ((ctypes.c_uint8 * beat_count)(
            *(max(0, min(255, int(row[0]))) for row in beats))
            if beat_count else None)
        self._lib.sb_deck_set_analysis(
            self._handle, h_arr, rgb_arr, count, rate,
            seconds_arr, beat_arr, beat_count)
        if columns and raw_count:
            detail_heights = (ctypes.c_float * raw_count)(
                *(max(0.0, min(1.0, float(v)))
                  for v in raw_heights[:raw_count]))
            detail_rgb = []
            for raw in raw_colors[:raw_count]:
                detail_rgb.extend(self._screen_rgb(raw))
            detail_colors = (
                ctypes.c_uint8 * (raw_count * 3))(*detail_rgb)
            self._lib.sb_deck_set_detail_analysis(
                self._handle, detail_heights, detail_colors, raw_count,
                float(data.get("sample_rate") or 150.0))
        self._set_detail_bands(data.get("_detail_bands"))
        self._set_audio_detail(data.get("_audio_detail"))
        self.set_cues(data.get("cues") or [])

    def set_overview(self, data: dict, generation: int = 0):
        """Upload the permanent compact RGB pyramid; ultra geometry joins later."""
        heights = data.get("_overview_heights")
        rgb = data.get("_overview_rgb")
        if not isinstance(heights, bytes) or not isinstance(rgb, bytes):
            return False
        encoding = str(data.get("_height_encoding") or "")
        width = 2 if encoding == "linear-u16le" else 1
        count = min(len(heights) // width, len(rgb) // 3)
        if not count:
            return False
        beats = [
            row for row in (data.get("beats") or [])
            if isinstance(row, (list, tuple)) and len(row) >= 2
        ]
        beat_count = len(beats)
        seconds = ((ctypes.c_double * beat_count)(
            *(float(row[1]) / 1000 for row in beats))
            if beat_count else None)
        beat_in_bar = ((ctypes.c_uint8 * beat_count)(
            *(max(0, min(255, int(row[0]))) for row in beats))
            if beat_count else None)
        packed_rgb = (ctypes.c_uint8 * (count * 3)).from_buffer_copy(
            rgb[:count * 3])
        expected = int(generation or self._analysis_generation)
        if encoding == "linear-u16le":
            packed_heights = (ctypes.c_uint16 * count).from_buffer_copy(
                heights[:count * 2])
            return bool(
                self._lib.sb_deck_set_packed_overview_u16_for_generation(
                    self._handle, expected, packed_heights, packed_rgb, count,
                    float(data.get("sample_rate") or 150.0),
                    seconds, beat_in_bar, beat_count))
        packed_heights = (ctypes.c_uint8 * count).from_buffer_copy(
            heights[:count])
        return bool(self._lib.sb_deck_set_packed_overview_for_generation(
            self._handle, expected, packed_heights, packed_rgb, count,
            float(data.get("sample_rate") or 150.0),
            seconds, beat_in_bar, beat_count))

    def set_complete_analysis(
            self, overview: dict, detail: dict, generation: int = 0
    ) -> tuple[bool, bool]:
        """Commit overview plus three-band detail as one visible surface."""
        self._lib.sb_deck_begin_waveform_update(self._handle)
        overview_ok = detail_ok = False
        try:
            overview_ok = bool(self.set_overview(overview, generation))
            if overview_ok:
                before = self.diagnostic_detail_shape()
                streams_ok = self._set_waveform_streams(
                    detail, float(overview.get("sample_rate") or 150.0),
                    overview)
                band_ok = streams_ok or self._set_detail_bands(
                    detail.get("_detail_bands"))
                audio_detail = detail.get("_audio_detail")
                if audio_detail is None and "heights" in detail:
                    audio_detail = detail
                self._set_audio_detail(audio_detail, generation)
                detail_ok = bool(
                    band_ok or self.diagnostic_detail_shape() != before)
        finally:
            self._lib.sb_deck_end_waveform_update(self._handle)
        return overview_ok, detail_ok

    def _set_waveform_streams(
            self, detail: dict, rate: float,
            overview: dict | None = None) -> bool:
        blue = detail.get("_blue_detail")
        rgb = detail.get("_rgb_detail")
        bands = detail.get("_detail_bands")
        if not (
                isinstance(blue, bytes) and blue
                and isinstance(rgb, bytes) and len(rgb) >= 2
                and isinstance(bands, bytes) and len(bands) >= 3):
            return False
        blue_count = len(blue)
        rgb_count = len(rgb) // 2
        band_count = len(bands) // 3
        if overview is not None:
            overview_heights = overview.get("_overview_heights")
            overview_rgb = overview.get("_overview_rgb")
            provenance = dict(overview.get("stream_provenance") or {})
            if (provenance.get("rgb") == "soundbox-analysis"
                    and isinstance(overview_rgb, bytes)):
                rgb = _repair_soundbox_pwv5_color(rgb, overview_rgb)
            if isinstance(overview_heights, bytes):
                rgb = _repair_pwv5_visibility(
                    rgb, overview_heights,
                    str(overview.get("_height_encoding") or ""))
                rgb_count = len(rgb) // 2
        blue_arr = (ctypes.c_uint8 * blue_count).from_buffer_copy(blue)
        rgb_arr = (ctypes.c_uint8 * (rgb_count * 2)).from_buffer_copy(
            rgb[:rgb_count * 2])
        band_arr = (ctypes.c_uint8 * (band_count * 3)).from_buffer_copy(
            bands[:band_count * 3])
        return bool(self._lib.sb_deck_set_waveform_streams(
            self._handle,
            blue_arr, blue_count,
            rgb_arr, rgb_count,
            band_arr, band_count,
            max(1.0, float(rate)),
        ))

    @staticmethod
    def _cue_rgb(cue: dict, slot: int) -> tuple[int, int, int]:
        if cue.get("kind") == "memory":
            return _MEMORY_CUE_RGB
        # Keep imported RGB as metadata, but render the stable A–H identity
        # palette so a letter never changes color between tracks.
        return _HOT_CUE_FALLBACKS[slot]

    def set_cues(self, cues):
        """Upload colored hot-cue boxes and red memory-cue arrows to Metal."""
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
            prepared.append((cue, position, max(0, min(255, slot))))
        if not prepared:
            self._lib.sb_deck_set_cues(
                self._handle, None, None, None, None, 0)
            return
        count = len(prepared)
        positions = (ctypes.c_uint32 * count)(
            *(min(0xffffffff, row[1]) for row in prepared))
        kinds = (ctypes.c_uint8 * count)(
            *(1 if row[0]["kind"] == "hot" else 0 for row in prepared))
        slots = (ctypes.c_uint8 * count)(*(row[2] for row in prepared))
        colors = []
        for cue, _position, slot in prepared:
            colors.extend(self._cue_rgb(cue, min(slot, 7)))
        rgb = (ctypes.c_uint8 * (count * 3))(*colors)
        self._lib.sb_deck_set_cues(
            self._handle, positions, kinds, slots, rgb, count)

    def set_session_cue(self, position_ms: int | None):
        """Upload the current non-persistent deck CUE marker to Metal."""
        active = position_ms is not None
        position = 0 if position_ms is None else max(0, int(position_ms))
        return bool(self._lib.sb_deck_set_session_cue(
            self._handle, min(0xffffffff, position), int(active)))

    def _set_detail_bands(self, bands):
        if not isinstance(bands, bytes) or len(bands) < 3:
            return False
        count = len(bands) // 3
        packed = (ctypes.c_uint8 * (count * 3)).from_buffer_copy(
            bands[:count * 3])
        return bool(self._lib.sb_deck_set_detail_bands(
            self._handle, packed, count))

    def _set_audio_detail(self, detail, generation: int = 0):
        if not isinstance(detail, dict):
            return
        rate = float(detail.get("rate") or 0)
        if rate <= 0:
            return
        expected = int(generation or self._analysis_generation)
        minimum_levels = detail.get("range_min_mips")
        maximum_levels = detail.get("range_max_mips")
        divisors = detail.get("mip_divisors")
        counts = detail.get("mip_counts")
        if (
                detail.get("encoding")
                == "song-anchored-extrema-i16le-v1"
                and isinstance(minimum_levels, list)
                and isinstance(maximum_levels, list)
                and isinstance(divisors, list)
                and isinstance(counts, list)
                and len(minimum_levels) == len(maximum_levels)
                == len(divisors) == len(counts)
                and minimum_levels):
            level_count = len(minimum_levels)
            if any(
                    not isinstance(minimum_levels[index], bytes)
                    or not isinstance(maximum_levels[index], bytes)
                    or int(counts[index]) <= 0
                    or len(minimum_levels[index]) != int(counts[index]) * 2
                    or len(maximum_levels[index]) != int(counts[index]) * 2
                    for index in range(level_count)):
                return
            flat_minimum = b"".join(minimum_levels)
            flat_maximum = b"".join(maximum_levels)
            total = len(flat_minimum) // 2
            packed_minimum = (
                ctypes.c_int16 * total).from_buffer_copy(flat_minimum)
            packed_maximum = (
                ctypes.c_int16 * total).from_buffer_copy(flat_maximum)
            packed_counts = (ctypes.c_uint32 * level_count)(
                *(int(value) for value in counts))
            packed_divisors = (ctypes.c_uint32 * level_count)(
                *(int(value) for value in divisors))
            self._lib.sb_deck_set_audio_detail_range_mips_i16_for_generation(
                self._handle,
                expected,
                packed_minimum,
                packed_maximum,
                packed_counts,
                packed_divisors,
                level_count,
                int(detail.get("base_count") or 0),
                rate,
            )
            return
        heights = detail.get("heights")
        if not isinstance(heights, bytes) or not heights:
            return
        if detail.get("encoding") == "linear-u16le":
            lower = detail.get("lower_heights")
            count = min(
                len(heights) // 2,
                len(lower) // 2 if isinstance(lower, bytes) else len(heights) // 2,
            )
            if not count:
                return
            packed = (ctypes.c_uint16 * count).from_buffer_copy(
                heights[:count * 2])
            if isinstance(lower, bytes):
                packed_lower = (
                    ctypes.c_uint16 * count).from_buffer_copy(
                        lower[:count * 2])
                minimum = detail.get("range_min")
                maximum = detail.get("range_max")
                if isinstance(minimum, bytes) and isinstance(maximum, bytes):
                    range_count = min(
                        count, len(minimum) // 2, len(maximum) // 2)
                    packed_minimum = (
                        ctypes.c_int16 * range_count).from_buffer_copy(
                            minimum[:range_count * 2])
                    packed_maximum = (
                        ctypes.c_int16 * range_count).from_buffer_copy(
                            maximum[:range_count * 2])
                    set_polarity_range = (
                        self._lib
                        .sb_deck_set_audio_detail_polarity_range_u16_i16_for_generation
                    )
                    set_polarity_range(
                        self._handle, expected, packed, packed_lower,
                        packed_minimum, packed_maximum, range_count, rate)
                    return
                self._lib.sb_deck_set_audio_detail_polarity_u16_for_generation(
                    self._handle, expected, packed, packed_lower, count, rate)
                return
            self._lib.sb_deck_set_audio_detail_u16_for_generation(
                self._handle, expected, packed, count, rate)
            return
        packed = (ctypes.c_uint8 * len(heights)).from_buffer_copy(heights)
        if expected:
            self._lib.sb_deck_set_audio_detail_for_generation(
                self._handle, expected, packed, len(heights), rate)
        else:
            self._lib.sb_deck_set_audio_detail(
                self._handle, packed, len(heights), rate)

    def set_audio_detail(self, detail, generation: int = 0):
        """Upgrade the loaded waveform when background preparation completes."""
        self._set_audio_detail(detail, generation)

    def _poll(self, force_emit: bool = False):
        if not self._handle or not self._lib.sb_deck_snapshot(
                self._handle, ctypes.byref(self._snapshot)):
            return
        snap = self._snapshot
        self._sample_rate = max(1.0, float(snap.sample_rate))
        duration = round(snap.duration_frames / self._sample_rate * 1000)
        position = round(snap.position_frames / self._sample_rate * 1000)
        states = {
            0: PlaybackState.STOPPED,
            1: PlaybackState.PLAYING,
            2: PlaybackState.PAUSED,
        }
        state = states.get(int(snap.playback_state), PlaybackState.STOPPED)
        ended = bool(self._lib.sb_deck_has_ended(self._handle))
        # CoreAudio can finish between the snapshot and the separate ended
        # read. The newer terminal flag wins over a stale PLAYING snapshot;
        # otherwise the next source waits for a fade on an already stopped
        # engine. PlayerBar defers that source swap until this poll completes.
        if ended:
            state = PlaybackState.PAUSED
        if duration != self._duration_ms:
            self._duration_ms = duration
            self.durationChanged.emit(duration)
        if duration > 0:
            self._loading = False
        if position != self._position_ms or force_emit:
            self._position_ms = position
            now = time.monotonic()
            if force_emit or now - self._last_position_emit >= 0.05:
                self._last_position_emit = now
                self.positionChanged.emit(position)
        # Publish the terminal edge while PlayerBar still retains ordinary
        # playback intent. Its slot defers playlist advancement by one event
        # turn, allowing the PAUSED snapshot below to land before source swap.
        if ended and not self._end_emitted:
            self._end_emitted = True
            self.endOfMedia.emit()
        elif not ended:
            self._end_emitted = False
        if state != self._state:
            self._set_state(state)
        if self._ramp_token and snap.ramp_completed >= self._ramp_token:
            self._ramp_token = 0
            completion, self._ramp_completion = self._ramp_completion, None
            if completion is not None:
                completion()
        active = (
            state == PlaybackState.PLAYING
            or bool(self._ramp_token)
            or self._loading)
        self._set_poll_interval(8 if active else 125)
        error_key = (int(snap.generation), int(snap.error_code))
        if snap.error_code and error_key != self._last_error:
            self._loading = False
            self._last_error = error_key
            message = bytes(snap.error_message).split(b"\0", 1)[0].decode(
                "utf-8", "replace")
            self.errorOccurred.emit(None, message or "Native deck error")

    def close(self):
        self._poll_timer.stop()
        host = getattr(self, "_host_widget", None)
        if host is not None:
            try:
                host.removeEventFilter(self)
            except RuntimeError:
                pass
            self._host_widget = None
        if self._handle:
            self._lib.sb_deck_destroy(self._handle)
            self._handle = None

    def __del__(self):
        try:
            self.close()
        except (AttributeError, RuntimeError):
            pass
