"""Compact signed detail supplement for complete imported rekordbox analysis.

No filter bank or beat detection runs here. Cells are anchored to absolute
sample boundaries, independent of decoder chunking, at exactly 1000 cells/sec.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IMPORTED_COLOR_MODEL = "rekordbox-pwv5-rgb-v1"
DETAIL_RATE = 1000.0
DETAIL_MODEL = "soundbox-rekordbox-supplement-i16-v1"
DETAIL_DIVISORS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64)


@dataclass(frozen=True)
class MP3Origin:
    """Bounded, validated MPEG-1 LAME header facts; never an extension guess."""
    lead_frames: int = 0
    decoded_frames: int = 0
    sample_rate: int = 0


def mp3_origin(path: str | Path) -> MP3Origin:
    """Identify the untrimmed analysis clock for tagged MPEG-1 Layer III.

    Unknown headers fail closed to the existing decoded clock. A consumer must
    also match decoded length and rate before using this hint. LAME delay is
    encoder delay; HIP's synthesis adds 528+1 samples. The Info/Xing packet is
    another MPEG frame on the imported analysis clock.
    """
    source = Path(path)
    if source.suffix.lower() != '.mp3':
        return MP3Origin()
    try:
        with source.open('rb') as handle:
            header = handle.read(10)
            offset = 0
            if header[:3] == b'ID3':
                if any(value & 128 for value in header[6:10]):
                    return MP3Origin()
                offset = 10 + sum(v << s for v, s in zip(header[6:10], (21, 14, 7, 0)))
                if header[5] & 0x10:
                    offset += 10
            handle.seek(offset)
            raw = handle.read(4096)
        if len(raw) < 200:
            return MP3Origin()
        word = int.from_bytes(raw[:4], 'big')
        # MPEG-1, layer III; no free-format or reserved bitrate/rate.
        if word & 0xfffe0000 != 0xfffa0000:
            return MP3Origin()
        bitrate_index = (word >> 12) & 15
        rate_index = (word >> 10) & 3
        if bitrate_index in (0, 15) or rate_index == 3:
            return MP3Origin()
        rate = (44100, 48000, 32000)[rate_index]
        bitrate = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)[bitrate_index]
        frame_size = 144000 * bitrate // rate + ((word >> 9) & 1)
        mono = (word >> 6) & 3 == 3
        cursor = 4 + (17 if mono else 32)
        if raw[cursor:cursor+4] not in (b'Info', b'Xing'):
            return MP3Origin()
        flags = int.from_bytes(raw[cursor+4:cursor+8], 'big')
        if not flags & 1 or flags & ~15:
            return MP3Origin()
        cursor += 8
        frame_count = int.from_bytes(raw[cursor:cursor+4], 'big')
        cursor += 4
        for flag, size in ((2, 4), (4, 100), (8, 4)):
            if flags & flag:
                cursor += size
        if cursor + 36 > frame_size or raw[cursor:cursor+4] != b'LAME':
            return MP3Origin()
        # Require the following packet to be a compatible MPEG-1 layer III.
        following = int.from_bytes(raw[frame_size:frame_size+4], 'big')
        if following & 0xfffe0c00 != word & 0xfffe0c00:
            return MP3Origin()
        packed = int.from_bytes(raw[cursor+21:cursor+24], 'big')
        delay, padding = packed >> 12, packed & 4095
        frames = frame_count * 1152 - delay - padding
        if not (0 < delay <= 2304 and 529 <= padding <= 4095 and frames > 0):
            return MP3Origin()
        return MP3Origin(1152 + delay + 529, frames, rate)
    except (OSError, ValueError, IndexError):
        return MP3Origin()


def complete_import(data: dict | None) -> bool:
    """Only complete, consistent packed streams bypass fallback analysis."""
    if (not data or not data.get('beats')
            or float(data.get('sample_rate') or 0) != 150.0):
        return False
    blue, rgb, bands = (data.get(name) for name in
                        ('_blue_detail', '_rgb_detail', '_detail_bands'))
    if not (isinstance(blue, bytes) and blue and isinstance(rgb, bytes)
            and rgb and isinstance(bands, bytes) and bands):
        return False
    return (len(blue) == len(rgb) // 2 == len(bands) // 3
            and len(rgb) % 2 == len(bands) % 3 == 0)



class DetailSupplement:
    def __init__(self, sample_rate: int):
        import numpy as np
        if sample_rate < 1000:
            raise ValueError('sample rate must support 1-ms cells')
        self.rate = int(sample_rate)
        self._tail = np.empty(0, dtype=np.float32)
        self._cell = 0
        self._minimum: list[bytes] = []
        self._maximum: list[bytes] = []

    def feed(self, samples) -> None:
        import numpy as np
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim > 1:
            values = values.mean(axis=1)
        values = np.nan_to_num(values, copy=True)
        values = np.concatenate((self._tail, values))
        origin = self._cell * self.rate // 1000
        # End indices are computed on the song clock; 44/45-sample cells at
        # 44.1 kHz must not reset their phase at each decoder block.
        last = ((origin + len(values) + 1) * 1000 - 1) // self.rate
        count = max(0, last - self._cell)
        if count:
            starts = (np.arange(self._cell, self._cell + count + 1, dtype=np.int64) * self.rate // 1000) - origin
            used = int(starts[-1])
            self._append(np.minimum.reduceat(values[:used], starts[:-1]),
                         np.maximum.reduceat(values[:used], starts[:-1]))
            self._cell += count
            values = values[used:]
        self._tail = values

    def _append(self, minimum, maximum):
        import numpy as np
        self._minimum.append(np.rint(np.clip(minimum, -1, 1) * 32767).astype('<i2').tobytes())
        self._maximum.append(np.rint(np.clip(maximum, -1, 1) * 32767).astype('<i2').tobytes())

    def finish(self) -> tuple[bytes, bytes]:
        if len(self._tail):
            self._append([self._tail.min()], [self._tail.max()])
            self._tail = self._tail[:0]
        return b''.join(self._minimum), b''.join(self._maximum)


def analyze_imported(path: str | Path, imported: dict,
                     checkpoint: Callable[[], bool] | None = None) -> dict | None:
    """Decode only the supplemental contour; retain imported streams exactly."""
    if not complete_import(imported):
        return None
    import numpy as np
    import soundfile as sf
    from soundbox import track_analysis as ta
    try:
        source = Path(path)
        before = source.stat()
        hint = mp3_origin(source)
        with sf.SoundFile(source) as audio:
            lead = hint.lead_frames if (audio.frames == hint.decoded_frames and audio.samplerate == hint.sample_rate) else 0
            detail = DetailSupplement(audio.samplerate)
            if lead:
                detail.feed(np.zeros(lead, dtype=np.float32))
            while True:
                if checkpoint is not None and not checkpoint():
                    return None
                block = audio.read(131072, dtype='float32', always_2d=True)
                if not len(block):
                    break
                detail.feed(block)
        minimum, maximum = detail.finish()
        after = source.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (checkpoint is not None and not checkpoint())):
            return None
        if not minimum:
            return None
        # Overview uses the imported packed RGB amplitude, not another FFT.
        packed = np.frombuffer(imported['_rgb_detail'], dtype='>u2')
        heights = ((packed & 0x7c) >> 2).astype(float) / 31
        red = ((packed >> 13) & 7).astype(float) / 7
        green = ((packed >> 10) & 7).astype(float) / 7
        blue = ((packed >> 7) & 7).astype(float) / 7
        result = ta.merge_import({
            'detail_minimum_values': minimum, 'detail_maximum_values': maximum,
            'detail_rate': DETAIL_RATE, 'waveform_model': DETAIL_MODEL,
            'height_encoding': ta.HEIGHT_ENCODING,
            'color_model': IMPORTED_COLOR_MODEL,
            'overview_heights': np.rint(heights * 65535).astype('<u2').tobytes(),
            'overview_rgb': np.rint(np.stack((red, green, blue), axis=1) * 255).astype('u1').tobytes(),
            'sample_rate': 150.0,
            'timeline_lead_frames': lead,
            'timeline_sample_rate': detail.rate,
        }, imported)
        if result is not None:
            result.pop('spectral_bands', None)
        return result
    except (OSError, ValueError, RuntimeError):
        return None
