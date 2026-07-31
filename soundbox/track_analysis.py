"""SoundBox-owned canonical track analysis.

V5 is deliberately independent from rekordbox's files.  Existing ANLZ data can
seed a record once, but every persisted byte is written beneath SoundBox
AppData and keyed to the source audio identity.  The container is sectioned,
individually Zstandard-compressed and checksummed so a damaged derived cache is
always a clean miss rather than partially trusted state.

The public ``load`` result intentionally matches the player analysis shape:
beat rows, RGB overview rows, cues, and an ``_audio_detail`` envelope. V5 stores
the exact song-anchored signed min/max mip levels used at the supported player
zooms, rather than four redundant 6144 Hz base streams. V4 records remain
playable and can be migrated atomically; a direct audio cook and a V4 migration
produce byte-identical V5 waveform sections.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
from typing import Callable, Iterable

SCHEMA = 5
LEGACY_SCHEMA = 4
ULTRA_RATE = 6144.0
COLOR_RATE = 150.0
SPECTRAL_BANDS = 24
COLOR_MODEL = "soundbox-rgb-filterbank-v2"
WAVEFORM_MODEL = "soundbox-polarity-range-mips-i16-v5"
LEGACY_WAVEFORM_MODEL = "soundbox-polarity-range-u16-v4"
THREE_BAND_MODEL = "soundbox-three-band-u7-v2"
DISPLAY_WAVEFORM_MODEL = "rekordbox-blue-rgb-three-band-v1"
DISPLAY_RENDER_MODEL = "rekordbox-native-mode-render-v3"
HEIGHT_ENCODING = "linear-u16le"
RANGE_ENCODING = "linear-i16le"
MP3_COLOR_PIPELINE_DELAY = 7.0 / COLOR_RATE
CHANNEL_SAFETY_FLOOR = 1.0 / 16.0
RANGE_MIP_DIVISORS = (8, 13, 18, 25, 35, 50, 66, 99, 132, 192, 256)
RANGE_MIP_ENCODING = "song-anchored-extrema-i16le-v1"
_MAGIC = b"SBA5"
_LEGACY_MAGIC = b"SBA4"
_HEADER = struct.Struct("<4sI")
_EXPORTABLE = {".mp3", ".wav", ".aif", ".aiff", ".flac"}
_PLAYER_ONLY = {".aac", ".alac", ".m4a", ".mp4"}


def _rbj_biquad(kind: str, cutoff: float, sample_rate: float):
    """Return one Q=1/sqrt(2) RBJ low/high-pass section.

    The dedicated RGB analyzer deliberately uses overlapping time-domain
    filters rather than remapping independently normalized FFT bands.  Keeping
    coefficient construction here makes that persisted analysis contract
    explicit and testable without involving either renderer.
    """
    import numpy as np

    rate = float(sample_rate)
    frequency = min(max(float(cutoff), 1.0), rate * 0.499)
    omega = 2 * np.pi * frequency / rate
    cosine = float(np.cos(omega))
    alpha = float(np.sin(omega)) / np.sqrt(2.0)
    a0 = 1.0 + alpha
    if kind == "lowpass":
        b0 = (1.0 - cosine) * 0.5
        b1 = 1.0 - cosine
        b2 = b0
    elif kind == "highpass":
        b0 = (1.0 + cosine) * 0.5
        b1 = -(1.0 + cosine)
        b2 = b0
    else:
        raise ValueError(f"Unsupported biquad kind: {kind}")
    return np.asarray([
        b0 / a0,
        b1 / a0,
        b2 / a0,
        1.0,
        (-2.0 * cosine) / a0,
        (1.0 - alpha) / a0,
    ], dtype=np.float64)


def _rgb_transfer(low, mid, high):
    """Convert overlapping band envelopes to full-range SoundBox RGB.

    Hue is local to each output column: the strongest band establishes the
    scale and the other two retain their true relationship to it.  The
    nonlinear interaction keeps low/mid overlap from turning pale yellow,
    slightly restrains green when red/blue are already strong, and gives the
    high-frequency path its characteristic vivid blue/cyan reach.
    """
    import numpy as np

    energies = np.stack((
        np.asarray(low, dtype=np.float64),
        np.asarray(mid, dtype=np.float64),
        np.asarray(high, dtype=np.float64),
    ), axis=-1)
    peak = np.max(energies, axis=-1, keepdims=True)
    color = np.divide(
        energies * 255.0,
        peak,
        out=np.zeros_like(energies),
        where=peak > 0,
    )
    red = color[..., 0]
    green = color[..., 1]
    blue = color[..., 2]
    overlap = np.where(
        blue < 64.0,
        red * green / (3.0 * 255.0) * (1.0 - blue / 64.0),
        0.0,
    )
    red = red - overlap
    green = green - overlap
    # Green suppression intentionally observes blue before its final boost.
    green = green * (
        1.0 - (3.0 / 1280.0) * np.minimum(
            128.0, np.maximum(blue, red)))
    blue = blue * 1.3
    return np.clip(np.stack((red, green, blue), axis=-1), 0, 255)


def _pack_rgb3(color, signal_peak=None) -> bytes:
    """Pack full-range RGB into the compact player color language.

    Red occupies the same effective three bits as green and blue but is stored
    doubled because the existing renderer/import boundary represents that
    channel on a 0..14 scale.  A non-silent sub-quantum column retains the tied
    strongest hue instead of disappearing to black.
    """
    import numpy as np

    full = np.asarray(color, dtype=np.float64).reshape(-1, 3)
    if not full.size:
        return b""
    quantized = np.clip(np.floor(full / 32.0), 0, 7).astype(np.uint8)
    if signal_peak is None:
        active = np.max(full, axis=1) > 0
    else:
        active = np.asarray(signal_peak, dtype=np.float64).reshape(-1) > 0
    dark = active & (np.max(quantized, axis=1) == 0)
    if np.any(dark):
        brightest = np.max(full[dark], axis=1, keepdims=True)
        quantized[dark] = (full[dark] == brightest).astype(np.uint8)
    quantized[:, 0] *= 2
    return bytes(quantized.tobytes())


def _temporal_rgb_envelopes(color_ms, frames_per_second: float):
    """Apply the frequency-dependent peak holds used by the RGB language."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d  # type: ignore[import-untyped]

    rows = np.asarray(color_ms, dtype=np.float32).reshape(-1, 3)
    if not rows.size:
        return rows
    low_radius = max(1, round(float(frames_per_second) * 0.012))
    mid_radius = max(1, round(float(frames_per_second) * 0.001))
    return np.stack((
        maximum_filter1d(
            rows[:, 0], size=low_radius * 2 + 1, mode="nearest"),
        maximum_filter1d(
            rows[:, 1], size=mid_radius * 2 + 1, mode="nearest"),
        rows[:, 2],
    ), axis=1)


def _resample_rgb_envelopes(
        envelopes, source_rate: float, output_rate: float, count: int,
        delay_seconds: float = 0.0):
    """Resample the 1 ms envelopes onto persisted RGB row timestamps."""
    import numpy as np

    rows = np.asarray(envelopes, dtype=np.float32).reshape(-1, 3)
    if not rows.size or count <= 0 or source_rate <= 0 or output_rate <= 0:
        return np.zeros((max(0, int(count)), 3), dtype=np.float32)
    positions = (
        np.arange(count, dtype=np.float64) * source_rate / output_rate
        - max(0.0, float(delay_seconds)) * source_rate
    )
    positions[np.abs(positions) < 1e-9] = 0.0
    source_rows = np.arange(len(rows), dtype=np.float64)
    return np.stack([
        np.interp(
            positions, source_rows, rows[:, channel],
            # The filterbank's group delay belongs to its sampling clock, not
            # to the track timeline.  Padding it with black created an audible
            # wedge at 0:00.  Clamp the boundary to the first analyzed hue so
            # every persisted row still shares the audio/grid/cue time origin.
            left=float(rows[0, channel]), right=float(rows[-1, channel]))
        for channel in range(3)
    ], axis=1)


def _resample_peak_envelopes(
        envelopes, source_rate: float, output_rate: float, count: int,
        delay_seconds: float = 0.0):
    """Peak-reduce band energy over each persisted 150 Hz time cell.

    A point sample is sufficient for hue, but it throws away short snare and
    clap energy between two output timestamps. The three-band silhouette owns
    the complete output cell, just like the compact amplitude envelope. A
    centered maximum filter followed by song-clock sampling keeps the operation
    vectorized for library preparation while retaining those short attacks.
    """
    import numpy as np
    from scipy.ndimage import maximum_filter1d

    rows = np.asarray(envelopes, dtype=np.float32).reshape(-1, 3)
    if not rows.size or count <= 0 or source_rate <= 0 or output_rate <= 0:
        return np.zeros((max(0, int(count)), 3), dtype=np.float32)
    cell_width = float(source_rate) / float(output_rate)
    filtered = maximum_filter1d(
        rows,
        size=max(1, int(np.ceil(cell_width))),
        axis=0,
        mode="nearest",
    )
    centers = (
        (np.arange(count, dtype=np.float64) + 0.5) * cell_width
        - max(0.0, float(delay_seconds)) * source_rate
    )
    indices = np.clip(np.rint(centers).astype(np.int64), 0, len(rows) - 1)
    return filtered[indices]


def _pack_three_band_u7(low, mid, high, percentile: float = 99.5) -> bytes:
    """Pack SoundBox display geometry in measured low/mid/high order.

    One shared per-track ceiling preserves the true relationship among low,
    mid, and high energy; normalizing each channel independently would make
    filter leakage look as strong as the intended tone and reverse kick/snare
    identity. Seven useful bits match Rekordbox's PWV7 precision and already
    exceed the vertical resolution of the deck. A real nonzero band keeps code
    1 so quiet signal cannot collapse into false silence.
    """
    import numpy as np

    rows = np.stack((
        np.asarray(low, dtype=np.float64),
        np.asarray(mid, dtype=np.float64),
        np.asarray(high, dtype=np.float64),
    ), axis=-1)
    if not rows.size:
        return b""
    positive = rows[rows > 0]
    ceiling = (
        float(np.percentile(positive, percentile))
        if positive.size else 1.0)
    encoded = np.clip(
        np.rint(rows / max(ceiling, 1e-12) * 127.0),
        0, 127,
    ).astype(np.uint8)
    encoded[(rows > 0) & (encoded == 0)] = 1
    return encoded.tobytes()


def _pack_blue_detail(heights, percentile: float = 99.9) -> bytes:
    """Pack a SoundBox BLUE fallback in PWV3's height/luminance layout."""
    import numpy as np

    values = np.asarray(heights, dtype=np.float64)
    if not values.size:
        return b""
    positive = values[values > 0]
    ceiling = (
        float(np.percentile(positive, percentile))
        if positive.size else 1.0)
    normalized = np.clip(values / max(ceiling, 1e-12), 0, 1)
    height = np.rint(normalized * 31).astype(np.uint8)
    height[(values > 0) & (height == 0)] = 1
    # PWV3's upper three bits are the classic blue-wave luminance. Retain
    # dynamic contrast without changing the geometry in the lower five bits.
    luminance = np.rint(np.sqrt(normalized) * 7).astype(np.uint8)
    packed: bytes = ((luminance << 5) | height).tobytes()
    return packed


def _pack_rgb_detail(heights, rgb, percentile: float = 99.9) -> bytes:
    """Pack a SoundBox RGB fallback in PWV5's exact 16-bit layout."""
    import numpy as np

    values = np.asarray(heights, dtype=np.float64)
    colors = np.asarray(rgb, dtype=np.float64).reshape(-1, 3)
    count = min(len(values), len(colors))
    if not count:
        return b""
    values = values[:count]
    colors = colors[:count]
    positive = values[values > 0]
    ceiling = (
        float(np.percentile(positive, percentile))
        if positive.size else 1.0)
    height = np.rint(
        np.clip(values / max(ceiling, 1e-12), 0, 1) * 31
    ).astype(np.uint16)
    height[(values > 0) & (height == 0)] = 1
    # PWV5 gives red four effective bits (stored at bits 15..12) and
    # green/blue three. This is the inverse of NativeDeckBackend._screen_rgb.
    red = np.rint(np.clip(colors[:, 0], 0, 255) / 18).astype(np.uint16)
    green = np.rint(np.clip(colors[:, 1], 0, 255) / 36).astype(np.uint16)
    blue = np.rint(np.clip(colors[:, 2], 0, 255) / 36).astype(np.uint16)
    packed = (
        (np.clip(red, 0, 14) << 12)
        | (np.clip(green, 0, 7) << 10)
        | (np.clip(blue, 0, 7) << 7)
        | (height << 2)
    ).astype(">u2")
    return packed.tobytes()


def _beat_grid(flux, rate: float, bpm_hint: float = 0) -> list[list]:
    """Derive a constant-tempo grid from the compact onset envelope.

    This intentionally stays in NumPy.  Pulling librosa/numba into a PySide
    worker caused LLVM's first JIT finalization to exhaust macOS's 512 KiB
    QThread stack and terminate the frozen app.  FFT autocorrelation supplies
    the tempo without any runtime JIT. This is only the SoundBox fallback:
    unlike an imported Rekordbox grid, it is deliberately anchored at 0:00.
    """
    import numpy as np

    onset = np.asarray(flux, dtype=np.float32).reshape(-1)
    if onset.size < 2 or not np.isfinite(rate) or rate <= 0:
        return []
    onset = np.nan_to_num(onset, copy=False)
    onset = np.maximum(onset - np.median(onset), 0)
    onset = np.convolve(
        onset, np.asarray([1, 2, 3, 2, 1], dtype=np.float32) / 9,
        mode="same")

    if bpm_hint > 0 and np.isfinite(bpm_hint):
        tempo = float(np.clip(bpm_hint, 40.0, 240.0))
        period = max(1, round(rate * 60.0 / tempo))
        tempo = rate * 60.0 / period
    else:
        # Analyze at most the first hour; that is ample musical evidence and
        # bounds the temporary FFT for very long recordings.
        onset = onset[:max(2, round(rate * 3600))]
        minimum = max(1, round(rate * 60.0 / 220.0))
        maximum = min(onset.size - 1, round(rate * 60.0 / 50.0))
        if maximum <= minimum or not np.any(onset):
            return []
        size = 1 << (2 * onset.size - 1).bit_length()
        spectrum = np.fft.rfft(onset, n=size)
        correlation = np.fft.irfft(
            spectrum * np.conj(spectrum), n=size)[:onset.size]
        lags = np.arange(minimum, maximum + 1)
        # Normalize for the decreasing number of overlapping samples, then
        # prefer the common DJ-range multiple when harmonics are close.
        scores = correlation[lags] / np.maximum(onset.size - lags, 1)
        best = int(lags[int(np.argmax(scores))])
        tempo = rate * 60.0 / best
        while tempo < 70.0 and best > 1:
            best = max(1, round(best / 2))
            tempo = rate * 60.0 / best
        while tempo > 180.0:
            best *= 2
            tempo = rate * 60.0 / best
        period = best

    return [
        [index % 4 + 1, round(frame / rate * 1000), round(tempo, 2)]
        for index, frame in enumerate(range(0, onset.size, period))
    ]


def export_eligibility(path: str | Path) -> dict:
    """Return the explicit future-CDJ export contract for one source file."""
    suffix = Path(path).suffix.lower()
    if suffix in _EXPORTABLE:
        return {
            "eligible": True,
            "format": suffix.lstrip("."),
            "reason": "Supported SoundBox CDJ export source",
        }
    if suffix in _PLAYER_ONLY:
        return {
            "eligible": False,
            "format": suffix.lstrip("."),
            "reason": "Player only; AAC/ALAC sources are never CDJ-exported",
        }
    return {
        "eligible": False,
        "format": suffix.lstrip("."),
        "reason": "Unsupported CDJ export source format",
    }


def audio_fingerprint(path: str | Path) -> str:
    """Stable, inexpensive content identity without hashing multi-GB sets."""
    source = Path(path)
    st = source.stat()
    digest = hashlib.sha256()
    digest.update(f"{st.st_size}|{st.st_mtime_ns}".encode())
    with source.open("rb") as handle:
        digest.update(handle.read(128 * 1024))
        if st.st_size > 256 * 1024:
            handle.seek(max(0, st.st_size - 128 * 1024))
            digest.update(handle.read(128 * 1024))
    return digest.hexdigest()


def _record_name(track_id: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(track_id)).strip("-")[:32]
    identity = hashlib.sha256(str(track_id).encode()).hexdigest()[:20]
    return f"{readable or 'track'}-{identity}.sba5"


def cache_path(cache_dir: str | Path, track_id: str) -> Path:
    return Path(cache_dir) / _record_name(track_id)


def legacy_cache_path(cache_dir: str | Path, track_id: str) -> Path:
    """Return the V4 compatibility path without treating it as V5-ready."""
    return cache_path(cache_dir, track_id).with_suffix(".sba4")


def _record_candidates(
        cache_dir: str | Path, track_id: str
) -> tuple[tuple[Path, bytes, int], ...]:
    return (
        (cache_path(cache_dir, track_id), _MAGIC, SCHEMA),
        (legacy_cache_path(cache_dir, track_id), _LEGACY_MAGIC, LEGACY_SCHEMA),
    )


def cue_fingerprint(cues: list[dict]) -> str:
    """Stable identity for canonical imported cue rows."""
    encoded = json.dumps(
        list(cues), ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def grid_fingerprint(beats: list) -> str:
    """Stable identity for exact imported beat-grid rows."""
    encoded = json.dumps(
        list(beats), ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _manifest_magic(manifest: dict) -> bytes:
    return (
        _MAGIC if manifest.get("schema") == SCHEMA
        else _LEGACY_MAGIC
    )


def with_imported_cues(data: dict, cues: list[dict]) -> dict:
    """Attach authoritative read-only import metadata to analysis being stored."""
    result = dict(data)
    rows = list(cues)
    result["cues"] = rows
    result["cue_import"] = {
        "source": "rekordbox-db",
        "fingerprint": cue_fingerprint(rows),
    }
    return result


def _read_container_manifest(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> tuple[Path, dict, bytes] | None:
    """Read and validate the small manifest plus opaque compressed payload."""
    source = Path(audio_path)
    stat = source.stat()
    for target, expected_magic, expected_schema in _record_candidates(
            cache_dir, track_id):
        try:
            packed = target.read_bytes()
            if len(packed) < _HEADER.size:
                continue
            magic, manifest_size = _HEADER.unpack_from(packed)
            if magic != expected_magic or manifest_size <= 0:
                continue
            start = _HEADER.size
            manifest = json.loads(packed[start:start + manifest_size])
            if (manifest.get("schema") != expected_schema
                    or manifest.get("track_id") != str(track_id)
                    or manifest.get("source", {}).get("identity")
                    != f"{stat.st_size}:{stat.st_mtime_ns}"):
                continue
            payload = packed[start + manifest_size:]
            if (hashlib.sha256(payload).hexdigest()
                    != manifest.get("payload_sha256")):
                continue
            return target, manifest, payload
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _read_manifest_header(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> tuple[Path, dict, int] | None:
    """Read only the header and JSON manifest for one source-keyed record."""
    source = Path(audio_path)
    stat = source.stat()
    for target, expected_magic, expected_schema in _record_candidates(
            cache_dir, track_id):
        try:
            with target.open("rb") as handle:
                header = handle.read(_HEADER.size)
                if len(header) != _HEADER.size:
                    continue
                magic, manifest_size = _HEADER.unpack(header)
                if magic != expected_magic or manifest_size <= 0:
                    continue
                encoded = handle.read(manifest_size)
                if len(encoded) != manifest_size:
                    continue
            manifest = json.loads(encoded)
            if (manifest.get("schema") != expected_schema
                    or manifest.get("track_id") != str(track_id)
                    or manifest.get("source", {}).get("identity")
                    != f"{stat.st_size}:{stat.st_mtime_ns}"):
                continue
            return target, manifest, _HEADER.size + manifest_size
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def load_manifest(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> dict | None:
    """Load the small transport manifest without reading waveform payloads."""
    try:
        record = _read_manifest_header(cache_dir, track_id, audio_path)
        if record is None:
            return None
        _target, manifest, _payload_offset = record
        return {
            "schema": int(manifest.get("schema") or 0),
            "beats": list(manifest.get("beats") or []),
            "cues": list(manifest.get("cues") or []),
            "sample_rate": float(
                manifest.get("color_rate") or COLOR_RATE),
            "color_model": str(manifest.get("color_model") or ""),
            "waveform_model": str(manifest.get("waveform_model") or ""),
            "three_band_model": str(
                manifest.get("three_band_model") or ""),
            "display_waveform_model": str(
                manifest.get("display_waveform_model") or ""),
            "display_render_model": str(
                manifest.get("display_render_model")
                or DISPLAY_RENDER_MODEL),
            "height_encoding": str(manifest.get("height_encoding") or ""),
            "range_encoding": str(manifest.get("range_encoding") or ""),
            "range_mip_encoding": str(
                manifest.get("range_mip_encoding") or ""),
            "range_mip_divisors": list(
                manifest.get("range_mip_divisors") or []),
            "detail_base_count": int(
                manifest.get("detail_base_count") or 0),
            "detail_rate": float(manifest.get("detail_rate") or 0),
            "provenance": dict(manifest.get("provenance") or {}),
            "stream_provenance": dict(
                manifest.get("stream_provenance") or {}),
            "grid_import": dict(manifest.get("grid_import") or {}),
            "cue_import": dict(manifest.get("cue_import") or {}),
            "eligibility": dict(manifest.get("eligibility") or {}),
        }
    except Exception:  # noqa: BLE001 - corruption/staleness is a clean miss
        return None


def _load_sections(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        names: tuple[str, ...]) -> tuple[dict, dict[str, bytes]] | None:
    """Read, decompress, and SHA-256 validate only the named sections."""
    import zstandard as zstd

    record = _read_manifest_header(cache_dir, track_id, audio_path)
    if record is None:
        return None
    target, manifest, payload_offset = record
    rows = manifest.get("sections")
    if not isinstance(rows, dict):
        return None
    decoded: dict[str, bytes] = {}
    decoder = zstd.ZstdDecompressor()
    with target.open("rb") as handle:
        file_size = target.stat().st_size
        for name in names:
            row = rows.get(name)
            if not isinstance(row, dict):
                return None
            try:
                offset = int(row["offset"])
                size = int(row["size"])
                raw_size = int(row["raw_size"])
                expected = str(row["sha256"])
            except (KeyError, TypeError, ValueError):
                return None
            start = payload_offset + offset
            if (offset < 0 or size < 0 or raw_size < 0
                    or start < payload_offset or start + size > file_size):
                return None
            handle.seek(start)
            compressed = handle.read(size)
            if len(compressed) != size:
                return None
            try:
                unpacked = decoder.decompress(
                    compressed, max_output_size=raw_size)
            except zstd.ZstdError:
                return None
            if (len(unpacked) != raw_size
                    or hashlib.sha256(unpacked).hexdigest() != expected):
                return None
            decoded[name] = unpacked
    return manifest, decoded


def _height_values(heights: bytes, encoding: str):
    """Return a zero-copy NumPy view over one persisted height stream."""
    import numpy as np

    dtype = "<u2" if encoding == HEIGHT_ENCODING else np.uint8
    width = 2 if encoding == HEIGHT_ENCODING else 1
    return np.frombuffer(
        heights, dtype=dtype, count=len(heights) // width)


def _repair_overview_rgb(
        heights: bytes, rgb: bytes, encoding: str = "") -> bytes:
    """Repair legacy black audible rows without changing timing or geometry."""
    import numpy as np

    values = _height_values(heights, encoding)
    count = min(len(values), len(rgb) // 3)
    if not count:
        return b""
    colors = np.frombuffer(rgb, dtype=np.uint8, count=count * 3).reshape(
        count, 3)
    audible = values[:count] > 0
    valid = np.any(colors != 0, axis=1)
    targets = audible & ~valid
    if not np.any(targets) or not np.any(valid):
        return rgb[:count * 3]
    indices = np.arange(count, dtype=np.int64)
    previous = np.maximum.accumulate(np.where(valid, indices, -1))
    following = np.minimum.accumulate(
        np.where(valid, indices, count)[::-1])[::-1]
    nearest = np.where(
        previous < 0,
        following,
        np.where(
            following >= count,
            previous,
            np.where(
                indices - previous <= following - indices,
                previous, following)))
    repairable = targets & (nearest >= 0) & (nearest < count)
    if not np.any(repairable):
        return rgb[:count * 3]
    repaired = colors.copy()
    repaired[repairable] = colors[nearest[repairable]]
    return repaired.tobytes()


def load_overview(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> dict | None:
    """Load the compact colored overview, leaving ultra detail untouched."""
    try:
        loaded = _load_sections(
            cache_dir, track_id, audio_path,
            ("overview_heights", "overview_rgb"))
        if loaded is None:
            return None
        manifest, sections = loaded
        heights = sections["overview_heights"]
        encoding = str(manifest.get("height_encoding") or "")
        width = 2 if encoding == HEIGHT_ENCODING else 1
        rgb = _repair_overview_rgb(
            heights, sections["overview_rgb"], encoding)
        count = min(len(heights) // width, len(rgb) // 3)
        return {
            "sample_rate": float(
                manifest.get("color_rate") or COLOR_RATE),
            "_overview_heights": heights[:count * width],
            "_overview_rgb": rgb[:count * 3],
            "_height_encoding": encoding,
        }
    except Exception:  # noqa: BLE001 - one bad section is a clean miss
        return None


def _range_mip_sections(
        minimum: bytes, maximum: bytes,
        divisors: tuple[int, ...] = RANGE_MIP_DIVISORS,
) -> tuple[dict[str, bytes], int]:
    """Reduce base signed ranges into exact song-anchored renderer mip rows."""
    import numpy as np

    count = min(len(minimum), len(maximum)) // 2
    if count <= 0:
        return {}, 0
    minimum_values = np.frombuffer(
        minimum, dtype="<i2", count=count).astype(np.int16, copy=False)
    maximum_values = np.frombuffer(
        maximum, dtype="<i2", count=count).astype(np.int16, copy=False)
    sections: dict[str, bytes] = {}
    for divisor in divisors:
        if divisor <= 0:
            raise ValueError("range mip divisors must be positive")
        padded_count = ((count + divisor - 1) // divisor) * divisor
        padded_minimum = np.full(
            padded_count, np.iinfo(np.int16).max, dtype="<i2")
        padded_maximum = np.full(
            padded_count, np.iinfo(np.int16).min, dtype="<i2")
        padded_minimum[:count] = minimum_values
        padded_maximum[:count] = maximum_values
        sections[f"detail_minimum_d{divisor}"] = (
            padded_minimum.reshape(-1, divisor).min(axis=1)
            .astype("<i2", copy=False).tobytes())
        sections[f"detail_maximum_d{divisor}"] = (
            padded_maximum.reshape(-1, divisor).max(axis=1)
            .astype("<i2", copy=False).tobytes())
    return sections, count


def projected_v5_bytes(duration_seconds: float) -> int:
    """Conservative uncompressed V5 budget used by lifecycle tests/UI guards."""
    seconds = max(0.0, float(duration_seconds))
    base_count = int(seconds * ULTRA_RATE + 0.999999)
    overview_count = int(seconds * COLOR_RATE + 0.999999)
    range_bytes = sum(
        ((base_count + divisor - 1) // divisor) * 4
        for divisor in RANGE_MIP_DIVISORS
    )
    # overview u16 + RGB + spectral master + BLUE + RGB16 + 3BAND
    compact_bytes = overview_count * (2 + 3 + 24 + 1 + 2 + 3)
    return range_bytes + compact_bytes + 256 * 1024


def _load_v5_range_mips(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        manifest: dict,
) -> dict | None:
    divisors = tuple(int(value) for value in (
        manifest.get("range_mip_divisors") or ()))
    if divisors != RANGE_MIP_DIVISORS:
        return None
    names = tuple(
        name
        for divisor in divisors
        for name in (
            f"detail_minimum_d{divisor}",
            f"detail_maximum_d{divisor}",
        )
    )
    loaded = _load_sections(cache_dir, track_id, audio_path, names)
    if loaded is None:
        return None
    sections = loaded[1]
    minimum_levels = [
        sections[f"detail_minimum_d{divisor}"] for divisor in divisors]
    maximum_levels = [
        sections[f"detail_maximum_d{divisor}"] for divisor in divisors]
    counts = [len(row) // 2 for row in minimum_levels]
    if any(
            len(minimum_levels[index]) != len(maximum_levels[index])
            or counts[index] != (
                int(manifest.get("detail_base_count") or 0)
                + divisors[index] - 1) // divisors[index]
            for index in range(len(divisors))):
        return None
    return {
        "rate": float(manifest.get("detail_rate") or 0),
        "base_count": int(manifest.get("detail_base_count") or 0),
        "mip_divisors": list(divisors),
        "mip_counts": counts,
        "range_min_mips": minimum_levels,
        "range_max_mips": maximum_levels,
        "encoding": RANGE_MIP_ENCODING,
    }


def load_detail(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> dict | None:
    """Load ultra detail independently from manifest and overview sections."""
    try:
        header = _read_manifest_header(cache_dir, track_id, audio_path)
        if header is None:
            return None
        manifest = header[1]
        if manifest.get("schema") == SCHEMA:
            loaded = _load_sections(
                cache_dir, track_id, audio_path,
                ("spectral_24", "blue_detail", "rgb_detail", "three_band"))
            if loaded is None:
                return None
            sections = loaded[1]
            detail = _load_v5_range_mips(
                cache_dir, track_id, audio_path, manifest)
            if detail is None:
                return None
            return {
                "_audio_detail": detail,
                "_spectral_24": sections["spectral_24"],
                "_blue_detail": sections["blue_detail"],
                "_rgb_detail": sections["rgb_detail"],
                "_detail_bands": sections["three_band"],
            }
        loaded = _load_sections(
            cache_dir, track_id, audio_path,
            ("detail", "spectral_24", "blue_detail", "rgb_detail",
             "three_band"))
        if loaded is None:
            return None
        manifest, sections = loaded
        lower = sections["detail"]
        if (manifest.get("waveform_model") == LEGACY_WAVEFORM_MODEL
                and "detail_lower" in (manifest.get("sections") or {})):
            lower_loaded = _load_sections(
                cache_dir, track_id, audio_path, ("detail_lower",))
            if lower_loaded is None:
                return None
            lower = lower_loaded[1]["detail_lower"]
        range_rows = {}
        section_names = manifest.get("sections") or {}
        if (
                manifest.get("waveform_model") == LEGACY_WAVEFORM_MODEL
                and "detail_minimum" in section_names
                and "detail_maximum" in section_names):
            range_loaded = _load_sections(
                cache_dir, track_id, audio_path,
                ("detail_minimum", "detail_maximum"))
            if range_loaded is None:
                return None
            range_rows = {
                "range_min": range_loaded[1]["detail_minimum"],
                "range_max": range_loaded[1]["detail_maximum"],
            }
        return {
            "_audio_detail": {
                "rate": float(manifest.get("detail_rate") or 0),
                "heights": sections["detail"],
                "lower_heights": lower,
                "encoding": str(manifest.get("height_encoding") or ""),
                **range_rows,
            },
            "_spectral_24": sections["spectral_24"],
            "_blue_detail": sections["blue_detail"],
            "_rgb_detail": sections["rgb_detail"],
            "_detail_bands": sections["three_band"],
        }
    except Exception:  # noqa: BLE001 - one bad section is a clean miss
        return None


def verify_complete(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Audit every section and the complete compressed payload checksum."""
    try:
        record = _read_manifest_header(cache_dir, track_id, audio_path)
        if record is None:
            return False
        target, manifest, payload_offset = record
        names = tuple((manifest.get("sections") or {}).keys())
        if not names or _load_sections(
                cache_dir, track_id, audio_path, names) is None:
            return False
        with target.open("rb") as handle:
            handle.seek(payload_offset)
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == manifest.get("payload_sha256")
    except Exception:  # noqa: BLE001 - audit reports false for every bad record
        return False


def imported_cues_need_sync(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        cues: list[dict] | None) -> bool:
    """Cheaply decide whether DB cues need a metadata-only cache refresh."""
    if cues is None:
        return False
    try:
        record = _read_manifest_header(cache_dir, track_id, audio_path)
        if record is None:
            return False
        _, manifest, _ = record
        if (manifest.get("provenance") or {}).get("user_data") == "soundbox":
            return False
        imported = manifest.get("cue_import") or {}
        return (
            imported.get("source") != "rekordbox-db"
            or imported.get("fingerprint") != cue_fingerprint(list(cues))
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def grid_needs_sync(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        imported_beats: list | None = None) -> bool:
    """Identify legacy or changed Rekordbox grids from the small manifest.

    ``None`` retains the legacy-migration check. A non-empty imported grid is
    compared to the exact cached rows, so caches created from Rekordbox's first
    automatic analysis notice later manual corrections without an audio recook.
    """
    try:
        record = _read_manifest_header(cache_dir, track_id, audio_path)
        if record is None:
            return False
        manifest = record[1]
        provenance = dict(manifest.get("provenance") or {})
        if provenance.get("user_data") == "soundbox":
            return False
        if imported_beats:
            rows = list(imported_beats)
            imported = dict(manifest.get("grid_import") or {})
            cached_fingerprint = (
                imported.get("fingerprint")
                if imported.get("source") == "rekordbox-anlz"
                else grid_fingerprint(list(manifest.get("beats") or []))
            )
            return (
                provenance.get("grid") != "rekordbox"
                or cached_fingerprint != grid_fingerprint(rows)
            )
        return provenance.get("grid") not in {
            "rekordbox", "soundbox-zero-anchor",
        }
    except (OSError, ValueError, KeyError, TypeError):
        return False


def update_grid_source(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        imported_beats: list | None) -> bool:
    """Atomically migrate grid metadata without touching ultra waveform bytes.

    A non-empty parsed Rekordbox grid wins byte-for-byte. Otherwise the prior
    SoundBox constant-tempo grid keeps its spacing and is shifted so beat 1 is
    exactly 0:00. SoundBox-owned manual rows are never rewritten.
    """
    tmp: Path | None = None
    try:
        record = _read_container_manifest(cache_dir, track_id, audio_path)
        if record is None:
            return False
        target, manifest, payload = record
        provenance = dict(manifest.get("provenance") or {})
        if provenance.get("user_data") == "soundbox":
            return False
        imported = list(imported_beats or [])
        if imported:
            beats = imported
            grid = "rekordbox"
            manifest["grid_import"] = {
                "source": "rekordbox-anlz",
                "fingerprint": grid_fingerprint(beats),
            }
        else:
            current = list(manifest.get("beats") or [])
            if not current:
                return False
            first = int(current[0][1])
            beats = []
            for row in current:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                migrated = list(row)
                migrated[1] = max(0, int(migrated[1]) - first)
                beats.append(migrated)
            if not beats:
                return False
            grid = "soundbox-zero-anchor"
            manifest.pop("grid_import", None)
        manifest["beats"] = beats
        manifest["provenance"] = {
            **provenance,
            "grid": grid,
            "owner": "soundbox",
            "rekordbox_runtime_dependency": False,
        }
        encoded = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True).encode()
        tmp = target.with_name(
            f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(
            _HEADER.pack(_manifest_magic(manifest), len(encoded))
            + encoded + payload)
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001 - derived grid metadata is retryable
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def update_imported_cues(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        cues: list[dict]) -> bool:
    """Atomically replace imported cues while preserving every payload byte."""
    tmp: Path | None = None
    try:
        record = _read_container_manifest(cache_dir, track_id, audio_path)
        if record is None:
            return False
        target, manifest, payload = record
        provenance = dict(manifest.get("provenance") or {})
        if provenance.get("user_data") == "soundbox":
            return False
        rows = list(cues)
        manifest["cues"] = rows
        manifest["cue_import"] = {
            "source": "rekordbox-db",
            "fingerprint": cue_fingerprint(rows),
        }
        manifest["provenance"] = {
            **provenance,
            "owner": "soundbox",
            "cues": "rekordbox-db",
            "rekordbox_runtime_dependency": False,
        }
        encoded = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True).encode()
        tmp = target.with_name(
            f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(
            _HEADER.pack(_manifest_magic(manifest), len(encoded))
            + encoded + payload)
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001 - imported metadata is always retryable
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def is_ready(cache_dir: str | Path, track_id: str,
             audio_path: str | Path) -> bool:
    """Fast preparation check using only the small container manifest.

    Full section hashes are verified when the player actually loads a record.
    A corrupt entry that passes this cheap scan is still a clean player miss and
    is rebuilt by the on-demand worker. Scanning thousands of tracks must not
    inflate gigabytes of waveform data at startup.
    """
    try:
        source = Path(audio_path)
        target = cache_path(cache_dir, track_id)
        with target.open("rb") as handle:
            header = handle.read(_HEADER.size)
            if len(header) != _HEADER.size:
                return False
            magic, manifest_size = _HEADER.unpack(header)
            if magic != _MAGIC or manifest_size <= 0:
                return False
            manifest = json.loads(handle.read(manifest_size))
        stat = source.stat()
        sections = manifest.get("sections", {})
        divisors = tuple(
            int(value)
            for value in manifest.get("range_mip_divisors") or ())
        base_count = int(manifest.get("detail_base_count") or 0)
        ranges_ready = (
            divisors == RANGE_MIP_DIVISORS
            and base_count > 0
            and all(
                int(sections.get(
                    f"detail_minimum_d{divisor}", {}).get(
                        "raw_size") or 0)
                == ((base_count + divisor - 1) // divisor) * 2
                and int(sections.get(
                    f"detail_maximum_d{divisor}", {}).get(
                        "raw_size") or 0)
                == ((base_count + divisor - 1) // divisor) * 2
                for divisor in divisors)
        )
        return (
            manifest.get("schema") == SCHEMA
            and manifest.get("track_id") == str(track_id)
            and manifest.get("source", {}).get("identity")
            == f"{stat.st_size}:{stat.st_mtime_ns}"
            and manifest.get("color_model") == COLOR_MODEL
            and manifest.get("waveform_model") == WAVEFORM_MODEL
            and manifest.get("three_band_model") == THREE_BAND_MODEL
            and manifest.get("display_waveform_model")
            == DISPLAY_WAVEFORM_MODEL
            and manifest.get("height_encoding") == HEIGHT_ENCODING
            and manifest.get("range_encoding") == RANGE_ENCODING
            and manifest.get("range_mip_encoding") == RANGE_MIP_ENCODING
            and float(manifest.get("detail_rate") or 0) >= 5500
            and ranges_ready
            and int(sections.get("overview_rgb", {}).get("raw_size") or 0) > 0
            and int(sections.get("blue_detail", {}).get("raw_size") or 0) > 0
            and int(sections.get("rgb_detail", {}).get("raw_size") or 0) > 0
            and int(sections.get("three_band", {}).get("raw_size") or 0) > 0
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _encode_sections(sections: dict[str, bytes]) -> tuple[dict, bytes]:
    import zstandard as zstd

    compressor = zstd.ZstdCompressor(level=6, threads=0)
    manifest_sections = {}
    payload = bytearray()
    for name, raw in sections.items():
        compressed = compressor.compress(raw)
        manifest_sections[name] = {
            "offset": len(payload),
            "size": len(compressed),
            "raw_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        payload.extend(compressed)
    return manifest_sections, bytes(payload)


def _decode_sections(raw: bytes, manifest: dict) -> dict[str, bytes] | None:
    import zstandard as zstd

    decoder = zstd.ZstdDecompressor()
    sections = {}
    for name, row in manifest.get("sections", {}).items():
        try:
            start = int(row["offset"])
            end = start + int(row["size"])
            unpacked = decoder.decompress(
                raw[start:end], max_output_size=int(row["raw_size"]))
            if (len(unpacked) != int(row["raw_size"])
                    or hashlib.sha256(unpacked).hexdigest() != row["sha256"]):
                return None
            sections[name] = unpacked
        except (KeyError, TypeError, ValueError, zstd.ZstdError):
            return None
    return sections


def _fallback_signed_ranges(heights: bytes, encoding: str) -> tuple[bytes, bytes]:
    """Create a symmetric signed range for old/test inputs lacking V4 ranges."""
    import numpy as np

    values = _height_values(heights, encoding)
    if not len(values):
        return b"", b""
    scale = 32767.0 / (
        65535.0 if encoding == HEIGHT_ENCODING else 255.0)
    signed = np.clip(
        np.rint(values.astype(np.float64) * scale), 0, 32767
    ).astype("<i2")
    return (-signed).astype("<i2", copy=False).tobytes(), signed.tobytes()


def store(cache_dir: str | Path, track_id: str, audio_path: str | Path,
          data: dict) -> Path | None:
    """Atomically persist one complete, bounded-size V5 analysis record."""
    tmp: Path | None = None
    try:
        source = Path(audio_path)
        prepared = dict(data)
        # Re-analysis upgrades derived payloads in place. If a future editor has
        # already claimed canonical grid/cue ownership, those crown-jewel user
        # rows must survive every analyzer revision.
        try:
            previous = _read_container_manifest(
                cache_dir, track_id, audio_path)
        except (OSError, ValueError, KeyError, TypeError):
            previous = None
        if previous is not None:
            old_manifest = previous[1]
            old_provenance = dict(old_manifest.get("provenance") or {})
            if old_provenance.get("user_data") == "soundbox":
                prepared["beats"] = list(old_manifest.get("beats") or [])
                prepared["cues"] = list(old_manifest.get("cues") or [])
                prepared["grid_import"] = {}
                prepared["cue_import"] = {}
                prepared["provenance"] = {
                    **dict(prepared.get("provenance") or {}),
                    **old_provenance,
                    "owner": "soundbox",
                    "user_data": "soundbox",
                    "rekordbox_runtime_dependency": False,
                }
        fingerprint = audio_fingerprint(source)
        heights = bytes(prepared.get("detail_heights") or
                        (prepared.get("_audio_detail") or {}).get("heights")
                        or b"")
        range_min = bytes(
            prepared.get("detail_minimum_values")
            or (prepared.get("_audio_detail") or {}).get("range_min")
            or b"")
        range_max = bytes(
            prepared.get("detail_maximum_values")
            or (prepared.get("_audio_detail") or {}).get("range_max")
            or b"")
        detail_rate = float(
            prepared.get("detail_rate")
            or (prepared.get("_audio_detail") or {}).get("rate")
            or 0)
        overview_heights = bytes(prepared.get("overview_heights") or b"")
        overview_rgb = bytes(prepared.get("overview_rgb") or b"")
        bands = bytes(prepared.get("spectral_bands") or b"")
        blue_detail = bytes(prepared.get("blue_detail") or b"")
        rgb_detail = bytes(prepared.get("rgb_detail") or b"")
        three_band = bytes(prepared.get("three_band") or b"")
        height_encoding = str(
            prepared.get("height_encoding")
            or (prepared.get("_audio_detail") or {}).get("encoding")
            or "")
        if not range_min or not range_max:
            range_min, range_max = _fallback_signed_ranges(
                heights, height_encoding)
        range_sections, detail_base_count = _range_mip_sections(
            range_min, range_max)
        if detail_base_count <= 0 or detail_rate <= 0:
            return None
        height_width = 2 if height_encoding == HEIGHT_ENCODING else 1
        count = min(
            len(overview_heights) // height_width, len(overview_rgb) // 3)
        overview_heights = overview_heights[:count * height_width]
        overview_rgb = overview_rgb[:count * 3]
        blue_detail = blue_detail[:count]
        rgb_detail = rgb_detail[:count * 2]
        three_band = three_band[:count * 3]
        sections = {
            **range_sections,
            "overview_heights": overview_heights,
            "overview_rgb": overview_rgb,
            "spectral_24": bands,
            "blue_detail": blue_detail,
            "rgb_detail": rgb_detail,
            "three_band": three_band,
        }
        section_rows, payload = _encode_sections(sections)
        metadata = {
            "schema": SCHEMA,
            "track_id": str(track_id),
            "source": {
                "path": str(source.resolve()),
                "fingerprint": fingerprint,
                "identity": f"{source.stat().st_size}:{source.stat().st_mtime_ns}",
                "size": source.stat().st_size,
                "mtime_ns": source.stat().st_mtime_ns,
            },
            "provenance": dict(prepared.get("provenance") or {
                "analysis": "soundbox",
                "owner": "soundbox",
            }),
            "stream_provenance": dict(
                prepared.get("stream_provenance") or {
                    "detail": "soundbox-analysis",
                    "blue": "soundbox-analysis",
                    "rgb": "soundbox-analysis",
                    "three_band": "soundbox-analysis",
                }),
            "eligibility": export_eligibility(source),
            "detail_rate": detail_rate,
            "color_rate": float(prepared.get("sample_rate") or COLOR_RATE),
            "color_model": str(prepared.get("color_model") or ""),
            "waveform_model": str(prepared.get("waveform_model") or ""),
            "three_band_model": str(
                prepared.get("three_band_model") or ""),
            "display_waveform_model": str(
                prepared.get("display_waveform_model") or ""),
            "display_render_model": str(
                prepared.get("display_render_model")
                or DISPLAY_RENDER_MODEL),
            "height_encoding": height_encoding,
            "range_encoding": RANGE_ENCODING,
            "range_mip_encoding": RANGE_MIP_ENCODING,
            "range_mip_divisors": list(RANGE_MIP_DIVISORS),
            "detail_base_count": detail_base_count,
            "overview_count": count,
            "spectral_bands": SPECTRAL_BANDS,
            "beats": list(prepared.get("beats") or []),
            "cues": list(prepared.get("cues") or []),
            "grid_import": dict(prepared.get("grid_import") or {}),
            "cue_import": dict(prepared.get("cue_import") or {}),
            "sections": section_rows,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        }
        manifest = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True).encode()
        directory = Path(cache_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = cache_path(directory, track_id)
        tmp = target.with_name(
            f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(_HEADER.pack(_MAGIC, len(manifest)) + manifest + payload)
        os.replace(tmp, target)
        return target
    except Exception:  # noqa: BLE001 - derived analysis is always rebuildable
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return None


def retire_legacy_record(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Remove one V4 only after the corresponding complete V5 is verified."""
    if not is_ready(cache_dir, track_id, audio_path):
        return False
    if not verify_complete(cache_dir, track_id, audio_path):
        return False
    legacy = legacy_cache_path(cache_dir, track_id)
    try:
        legacy.unlink(missing_ok=True)
        return not legacy.exists()
    except OSError:
        return False


def migrate_v4(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Atomically reduce one valid V4 to byte-equivalent V5 renderer mips."""
    import zstandard as zstd

    if is_ready(cache_dir, track_id, audio_path):
        return retire_legacy_record(cache_dir, track_id, audio_path)
    source = Path(audio_path)
    legacy = legacy_cache_path(cache_dir, track_id)
    try:
        packed = legacy.read_bytes()
        if len(packed) < _HEADER.size:
            return False
        magic, manifest_size = _HEADER.unpack_from(packed)
        if magic != _LEGACY_MAGIC or manifest_size <= 0:
            return False
        start = _HEADER.size
        manifest = json.loads(packed[start:start + manifest_size])
        stat = source.stat()
        if (manifest.get("schema") != LEGACY_SCHEMA
                or manifest.get("track_id") != str(track_id)
                or manifest.get("source", {}).get("identity")
                != f"{stat.st_size}:{stat.st_mtime_ns}"
                or manifest.get("waveform_model") != LEGACY_WAVEFORM_MODEL):
            return False
        payload = packed[start + manifest_size:]
        if hashlib.sha256(payload).hexdigest() != manifest.get(
                "payload_sha256"):
            return False
        sections = _decode_sections(payload, manifest)
        if sections is None:
            return False
        required = {
            "detail_minimum", "detail_maximum",
            "overview_heights", "overview_rgb", "spectral_24",
            "blue_detail", "rgb_detail", "three_band",
        }
        if not required.issubset(sections):
            return False
        prepared = {
            "detail_minimum_values": sections["detail_minimum"],
            "detail_maximum_values": sections["detail_maximum"],
            # ``store`` keeps this solely as a compatibility fallback; V5 does
            # not persist it.
            "detail_heights": sections.get("detail", b""),
            "detail_rate": float(manifest.get("detail_rate") or 0),
            "overview_heights": sections["overview_heights"],
            "overview_rgb": sections["overview_rgb"],
            "spectral_bands": sections["spectral_24"],
            "blue_detail": sections["blue_detail"],
            "rgb_detail": sections["rgb_detail"],
            "three_band": sections["three_band"],
            "sample_rate": float(manifest.get("color_rate") or COLOR_RATE),
            "color_model": str(manifest.get("color_model") or ""),
            "waveform_model": WAVEFORM_MODEL,
            "three_band_model": str(
                manifest.get("three_band_model") or ""),
            "display_waveform_model": str(
                manifest.get("display_waveform_model") or ""),
            "display_render_model": str(
                manifest.get("display_render_model")
                or DISPLAY_RENDER_MODEL),
            "height_encoding": str(
                manifest.get("height_encoding") or HEIGHT_ENCODING),
            "range_encoding": RANGE_ENCODING,
            "beats": list(manifest.get("beats") or []),
            "cues": list(manifest.get("cues") or []),
            "grid_import": dict(manifest.get("grid_import") or {}),
            "cue_import": dict(manifest.get("cue_import") or {}),
            "provenance": dict(manifest.get("provenance") or {}),
            "stream_provenance": dict(
                manifest.get("stream_provenance") or {}),
        }
        if store(cache_dir, track_id, source, prepared) is None:
            return False
        return retire_legacy_record(cache_dir, track_id, source)
    except (
            OSError, ValueError, KeyError, TypeError, zstd.ZstdError):
        return False


def load(cache_dir: str | Path, track_id: str, audio_path: str | Path,
         compact: bool = False) -> dict | None:
    """Compatibility full-load API.

    Interactive player loads use ``load_manifest``/``load_overview``/
    ``load_detail`` so no phase reads or hashes unrelated sections.
    """
    try:
        result = load_manifest(cache_dir, track_id, audio_path)
        overview = load_overview(cache_dir, track_id, audio_path)
        detail = load_detail(cache_dir, track_id, audio_path)
        if result is None or overview is None or detail is None:
            return None
        if not verify_complete(cache_dir, track_id, audio_path):
            return None
        result.update(detail)
        result.update(overview)
        heights_raw = overview["_overview_heights"]
        rgb_raw = overview["_overview_rgb"]
        encoding = str(overview.get("_height_encoding") or "")
        height_values = _height_values(heights_raw, encoding)
        count = min(len(height_values), len(rgb_raw) // 3)
        if not compact:
            result["heights"] = [
                value / (65535 if encoding == HEIGHT_ENCODING else 255)
                for value in height_values[:count]]
            result["colors"] = [
                [rgb_raw[i], rgb_raw[i + 1], rgb_raw[i + 2]]
                for i in range(0, count * 3, 3)
            ]
        return result
    except Exception:  # noqa: BLE001 - corruption/staleness is a clean miss
        return None


def update_user_data(cache_dir: str | Path, track_id: str,
                     audio_path: str | Path, *,
                     beats: list | None = None,
                     cues: list | None = None) -> bool:
    """Atomically apply future SoundBox grid/cue edits without re-analysis.

    This is intentionally storage-only for now; the cue/grid editor will call
    it later.  Imported Rekordbox data therefore has a clean, tested ownership
    boundary before editing UI exists.
    """
    tmp: Path | None = None
    try:
        record = _read_container_manifest(
            cache_dir, track_id, audio_path)
        if record is None:
            return False
        target, manifest, payload = record
        if beats is not None:
            manifest["beats"] = list(beats)
            manifest.pop("grid_import", None)
        if cues is not None:
            manifest["cues"] = list(cues)
            manifest.pop("cue_import", None)
        manifest["provenance"] = {
            **dict(manifest.get("provenance") or {}),
            "owner": "soundbox",
            "user_data": "soundbox",
            **({"grid": "soundbox-manual"} if beats is not None else {}),
            "rekordbox_runtime_dependency": False,
        }
        encoded = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True).encode()
        tmp = target.with_name(
            f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(
            _HEADER.pack(_manifest_magic(manifest), len(encoded))
            + encoded + payload)
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _color_pipeline_delay(path: str | Path) -> float:
    """Return only the decoder/timeline correction proven for this format."""
    return (
        MP3_COLOR_PIPELINE_DELAY
        if Path(path).suffix.lower() == ".mp3"
        else 0.0
    )


class StreamingAnalyzer:
    """Incremental waveform/RGB/grid feature extractor for any PCM decoder."""

    def __init__(
            self, sample_rate: float, color_pipeline_delay: float = 0.0):
        import numpy as np

        self.np = np
        self.sample_rate = float(sample_rate)
        self.color_pipeline_delay = max(0.0, float(color_pipeline_delay))
        self.detail_window = max(1, round(self.sample_rate / ULTRA_RATE))
        self.detail_rate = self.sample_rate / self.detail_window
        self.color_hop = max(1, round(self.sample_rate / COLOR_RATE))
        self.n_fft = 1
        while self.n_fft < max(512, self.color_hop * 4):
            self.n_fft *= 2
        self.n_fft = min(4096, self.n_fft)
        self._detail_upper_tail = np.empty(0, dtype=np.float32)
        self._detail_lower_tail = np.empty(0, dtype=np.float32)
        self._detail_signed_tail = np.empty(0, dtype=np.float32)
        self._spectral_tail = np.empty(0, dtype=np.float32)
        self._overview_tail = np.empty(0, dtype=np.float32)
        self._color_tail = np.empty((0, 3), dtype=np.float32)
        self._detail: list = []
        self._detail_lower: list = []
        self._detail_minimum: list = []
        self._detail_maximum: list = []
        self._overview: list = []
        self._spectra: list = []
        self._color_ms: list = []
        self._last_spectrum = None
        self._flux: list = []
        self._color_window = max(1, round(self.sample_rate / 1000.0))
        self._color_ms_rate = self.sample_rate / self._color_window
        from scipy.signal import sosfilt  # type: ignore[import-untyped]
        self._sosfilt = sosfilt
        low = _rbj_biquad("lowpass", 100.0, self.sample_rate)
        mid_high = _rbj_biquad("highpass", 300.0, self.sample_rate)
        mid_low = _rbj_biquad("lowpass", 3000.0, self.sample_rate)
        high = _rbj_biquad("highpass", 1500.0, self.sample_rate)
        self._color_sos = (
            low.reshape(1, 6),
            np.stack((mid_high, mid_low)),
            high.reshape(1, 6),
        )
        self._color_zi = [
            np.zeros((len(sos), 2), dtype=np.float64)
            for sos in self._color_sos
        ]
        frequencies = np.fft.rfftfreq(self.n_fft, 1 / self.sample_rate)
        edges = np.geomspace(35.0, max(80.0, self.sample_rate / 2),
                             SPECTRAL_BANDS + 1)
        # The geometric bands are contiguous. Keep slice bounds rather than
        # integer index arrays: ``power[:, bins]`` uses NumPy advanced indexing
        # and allocated a temporary matrix for every one of the 24 bands.
        self._band_ranges = []
        for index in range(SPECTRAL_BANDS):
            bins = np.flatnonzero(
                (frequencies >= edges[index])
                & (frequencies < edges[index + 1]))
            self._band_ranges.append(
                (int(bins[0]), int(bins[-1]) + 1)
                if bins.size else (0, 0))
        self._window = np.hanning(self.n_fft).astype(np.float32)

    def feed(self, mono: Iterable[float], *, owned: bool = False) -> None:
        np = self.np
        # QAudioBuffer exposes a read-only memory view. The analyzer mutates
        # only a private sanitizing copy. Internal decoders may explicitly hand
        # over an owned float32 chunk to avoid copying it a second time.
        incoming = np.array(mono, dtype=np.float32, copy=not owned)
        if not incoming.size:
            return
        incoming = np.nan_to_num(incoming, copy=False)
        if incoming.ndim == 1:
            values = incoming.reshape(-1)
            geometry_upper = np.maximum(values, 0.0)
            geometry_lower = np.maximum(-values, 0.0)
            geometry_values = np.maximum(geometry_upper, geometry_lower)
            color_values = values
        else:
            channels = incoming.reshape(incoming.shape[0], -1)
            values = channels.mean(axis=1, dtype=np.float32)
            # Rekordbox-like silhouette detail follows the stereo midpoint,
            # not whichever channel happens to be louder. Making channel peak
            # the primary geometry inflated ordinary stereo ambience and
            # filled the valleys between attacks. A small channel-safe floor
            # still preserves fully anti-phase/wide tails in linear uint16;
            # the display shoulder makes that real signal visible later.
            midpoint_upper = np.maximum(values, 0.0)
            midpoint_lower = np.maximum(-values, 0.0)
            channel_upper = np.max(np.maximum(channels, 0.0), axis=1)
            channel_lower = np.max(np.maximum(-channels, 0.0), axis=1)
            geometry_upper = np.maximum(
                midpoint_upper, channel_upper * CHANNEL_SAFETY_FLOOR)
            geometry_lower = np.maximum(
                midpoint_lower, channel_lower * CHANNEL_SAFETY_FLOOR)
            geometry_values = np.maximum(geometry_upper, geometry_lower)
            if channels.shape[1] == 1:
                color_values = channels[:, 0]
            else:
                left = channels[:, 0]
                right = channels[:, 1]
                close = np.abs(np.abs(left) - np.abs(right)) < 0.001
                louder = np.where(np.abs(left) >= np.abs(right), left, right)
                color_values = np.where(
                    close, louder, (left + right) * 0.5).astype(np.float32)

        detail_upper = np.concatenate(
            (self._detail_upper_tail, geometry_upper))
        detail_lower = np.concatenate(
            (self._detail_lower_tail, geometry_lower))
        detail_signed = np.concatenate(
            (self._detail_signed_tail, values))
        keep = len(detail_upper) - len(detail_upper) % self.detail_window
        if keep:
            self._detail.append(
                detail_upper[:keep].reshape(
                    -1, self.detail_window).max(axis=1))
            self._detail_lower.append(
                detail_lower[:keep].reshape(
                    -1, self.detail_window).max(axis=1))
            signed_rows = detail_signed[:keep].reshape(
                -1, self.detail_window)
            self._detail_minimum.append(signed_rows.min(axis=1))
            self._detail_maximum.append(signed_rows.max(axis=1))
        self._detail_upper_tail = detail_upper[keep:]
        self._detail_lower_tail = detail_lower[keep:]
        self._detail_signed_tail = detail_signed[keep:]

        filtered = []
        for index, sos in enumerate(self._color_sos):
            row, self._color_zi[index] = self._sosfilt(
                sos, color_values, zi=self._color_zi[index])
            filtered.append(np.abs(row).astype(np.float32))
        color_samples = np.stack(filtered, axis=1)
        color_samples = np.concatenate((self._color_tail, color_samples), axis=0)
        color_keep = (
            len(color_samples)
            - len(color_samples) % self._color_window
        )
        if color_keep:
            self._color_ms.append(
                color_samples[:color_keep].reshape(
                    -1, self._color_window, 3).max(axis=1))
        self._color_tail = color_samples[color_keep:]

        # The visible 150 Hz envelope owns a 1/150-second time cell. Reusing
        # the 2048-sample FFT window here looked ahead about 46 ms, rounded
        # attacks, filled decay valleys, and omitted the final seven rows.
        # Keep geometry on its own non-overlapping clock; spectral analysis
        # may retain its wider FFT window without changing the silhouette.
        overview_samples = np.concatenate(
            (self._overview_tail, geometry_values))
        overview_keep = (
            len(overview_samples)
            - len(overview_samples) % self.color_hop
        )
        if overview_keep:
            self._overview.append(
                overview_samples[:overview_keep].reshape(
                    -1, self.color_hop).max(axis=1))
        self._overview_tail = overview_samples[overview_keep:]

        samples = np.concatenate((self._spectral_tail, values))
        frame_count = 1 + (len(samples) - self.n_fft) // self.color_hop
        if frame_count <= 0:
            self._spectral_tail = samples
            return
        # Bound each temporary strided FFT batch. Long set files stay constant
        # memory even though all compact 150 Hz results are retained.
        spectra = []
        for first in range(0, frame_count, 512):
            count = min(512, frame_count - first)
            starts = (first + np.arange(count)) * self.color_hop
            frames = np.stack(
                [samples[s:s + self.n_fft] for s in starts], axis=0)
            power = np.abs(np.fft.rfft(frames * self._window, axis=1))
            rows = np.stack([
                power[:, start:end].mean(axis=1) if end > start
                else np.zeros(count, dtype=np.float32)
                for start, end in self._band_ranges
            ], axis=1)
            spectra.append(np.log1p(rows).astype(np.float32))
        matrix = np.concatenate(spectra, axis=0)
        previous = (
            self._last_spectrum
            if self._last_spectrum is not None else matrix[0])
        prior = np.vstack((previous, matrix[:-1]))
        self._flux.append(
            np.maximum(matrix - prior, 0).mean(axis=1).astype(np.float32))
        self._last_spectrum = matrix[-1]
        self._spectra.append(matrix)
        consumed = frame_count * self.color_hop
        self._spectral_tail = samples[consumed:]

    @staticmethod
    def _bytes(values, percentile: float = 99.5) -> bytes:
        import numpy as np

        raw = np.asarray(values, dtype=np.float32)
        if not raw.size:
            return b""
        positive = raw[raw > 0]
        ceiling = float(np.percentile(positive, percentile)) if positive.size else 1
        encoded = np.clip(
            np.rint(raw / max(ceiling, 1e-9) * 255), 0, 255
        ).astype(np.uint8)
        return bytes(encoded)

    @staticmethod
    def _u16_bytes(values, percentile: float = 99.5) -> bytes:
        """Normalize linear peaks without discarding quiet musical signal.

        Uint16 retains roughly 48 dB more amplitude resolution than the legacy
        byte envelope.  The persisted values stay linear so display visibility
        can be tuned later without another audio recook.
        """
        import numpy as np

        raw = np.asarray(values, dtype=np.float32)
        if not raw.size:
            return b""
        positive = raw[raw > 0]
        ceiling = (
            float(np.percentile(positive, percentile))
            if positive.size else 1.0)
        encoded = np.clip(
            np.rint(raw / max(ceiling, 1e-9) * 65535), 0, 65535
        ).astype("<u2")
        return bytes(encoded)

    @staticmethod
    def _u16_polarity_bytes(
            upper, lower, percentile: float = 99.5) -> tuple[bytes, bytes]:
        """Normalize upper/lower peaks with one ceiling, preserving asymmetry."""
        import numpy as np

        upper_raw = np.asarray(upper, dtype=np.float32)
        lower_raw = np.asarray(lower, dtype=np.float32)
        joined = np.concatenate((upper_raw, lower_raw))
        positive = joined[joined > 0]
        ceiling = (
            float(np.percentile(positive, percentile))
            if positive.size else 1.0)

        def encode(values):
            return bytes(np.clip(
                np.rint(values / max(ceiling, 1e-9) * 65535),
                0, 65535).astype("<u2"))

        return encode(upper_raw), encode(lower_raw)

    @staticmethod
    def _i16_range_bytes(
            minimum, maximum,
            percentile: float = 99.5) -> tuple[bytes, bytes]:
        """Normalize signed min/max rows with one shared linear ceiling."""
        import numpy as np

        minimum_raw = np.asarray(minimum, dtype=np.float32)
        maximum_raw = np.asarray(maximum, dtype=np.float32)
        joined = np.abs(np.concatenate((minimum_raw, maximum_raw)))
        positive = joined[joined > 0]
        ceiling = (
            float(np.percentile(positive, percentile))
            if positive.size else 1.0)

        def encode(values):
            return bytes(np.clip(
                np.rint(values / max(ceiling, 1e-9) * 32767),
                -32767, 32767).astype("<i2"))

        return encode(minimum_raw), encode(maximum_raw)

    def finish(self, bpm_hint: float = 0) -> dict | None:
        np = self.np
        if self._detail_upper_tail.size:
            self._detail.append(np.asarray(
                [self._detail_upper_tail.max()], dtype=np.float32))
            self._detail_lower.append(np.asarray(
                [self._detail_lower_tail.max()], dtype=np.float32))
            self._detail_minimum.append(np.asarray(
                [self._detail_signed_tail.min()], dtype=np.float32))
            self._detail_maximum.append(np.asarray(
                [self._detail_signed_tail.max()], dtype=np.float32))
        if self._overview_tail.size:
            self._overview.append(np.asarray(
                [np.abs(self._overview_tail).max()], dtype=np.float32))
        if self._color_tail.size:
            self._color_ms.append(
                np.max(self._color_tail, axis=0, keepdims=True))
        if not self._detail:
            return None
        detail = np.concatenate(self._detail)
        self._detail.clear()
        detail_lower = np.concatenate(self._detail_lower)
        self._detail_lower.clear()
        detail_bytes, detail_lower_bytes = self._u16_polarity_bytes(
            detail, detail_lower, 99.9)
        del detail, detail_lower
        detail_minimum = np.concatenate(self._detail_minimum)
        self._detail_minimum.clear()
        detail_maximum = np.concatenate(self._detail_maximum)
        self._detail_maximum.clear()
        detail_minimum_bytes, detail_maximum_bytes = self._i16_range_bytes(
            detail_minimum, detail_maximum, 99.9)
        del detail_minimum, detail_maximum
        overview = (
            np.concatenate(self._overview)
            if self._overview else np.empty(0, dtype=np.float32))
        self._overview.clear()
        overview_bytes = self._u16_bytes(overview, 99.9)
        spectra = (
            np.concatenate(self._spectra, axis=0)
            if self._spectra else np.empty((0, SPECTRAL_BANDS), dtype=np.float32))
        self._spectra.clear()
        color_ms = (
            np.concatenate(self._color_ms, axis=0)
            if self._color_ms else np.empty((0, 3), dtype=np.float32))
        self._color_ms.clear()

        # The 24-band master is retained as long-term SoundBox analysis. It does
        # not drive display color: independent per-band normalization destroys
        # the true energy relationship needed for a meaningful local hue.
        if spectra.size:
            ceilings = np.percentile(spectra, 99.5, axis=0)
            normalized = np.clip(
                spectra / np.maximum(ceilings, 1e-9), 0, 1)
            bands = np.rint(normalized * 255).astype(np.uint8)
            # The visible envelope includes the short final cell that cannot
            # form a complete FFT window. Keep the private spectral master on
            # the same row clock by extending its final known spectrum.
            if len(bands) < len(overview):
                bands = np.concatenate((
                    bands,
                    np.repeat(
                        bands[-1:, :], len(overview) - len(bands), axis=0),
                ), axis=0)
            else:
                bands = bands[:len(overview)]
            spectral_bytes = bands.tobytes()
            del normalized, bands, ceilings
        else:
            spectral_bytes = b""
        del spectra

        if color_ms.size and overview.size:
            envelopes = _temporal_rgb_envelopes(
                color_ms, self._color_ms_rate)
            # Sample the 1 ms envelopes at the established analyzer timestamps.
            # The fixed seven-row pipeline latency matches the compact RGB row
            # timing while waveform geometry remains byte-for-byte unchanged.
            sampled = _resample_rgb_envelopes(
                envelopes,
                self._color_ms_rate,
                self.sample_rate / self.color_hop,
                len(overview),
                self.color_pipeline_delay,
            )
            low = sampled[:, 0]
            mid = sampled[:, 1]
            high = sampled[:, 2]
            signal_peak = np.maximum.reduce((low, mid, high))
            overview_rgb = _pack_rgb3(
                _rgb_transfer(low, mid, high), signal_peak)
            blue_detail = _pack_blue_detail(overview)
            rgb_detail = _pack_rgb_detail(
                overview, np.frombuffer(
                    overview_rgb, dtype=np.uint8).reshape(-1, 3))
            geometry_bands = _resample_peak_envelopes(
                envelopes,
                self._color_ms_rate,
                self.sample_rate / self.color_hop,
                len(overview),
                self.color_pipeline_delay,
            )
            three_band = _pack_three_band_u7(
                geometry_bands[:, 0],
                geometry_bands[:, 1],
                geometry_bands[:, 2],
            )
        else:
            overview_rgb = bytes(len(overview) * 3)
            blue_detail = bytes(len(overview))
            rgb_detail = bytes(len(overview) * 2)
            three_band = bytes(len(overview) * 3)

        flux = (
            np.concatenate(self._flux)
            if self._flux else np.empty(0, dtype=np.float32))
        self._flux.clear()
        beats = _beat_grid(
            flux, self.sample_rate / self.color_hop, bpm_hint)
        return {
            "detail_heights": detail_bytes,
            "detail_lower_heights": detail_lower_bytes,
            "detail_minimum_values": detail_minimum_bytes,
            "detail_maximum_values": detail_maximum_bytes,
            "detail_rate": self.detail_rate,
            "overview_heights": overview_bytes,
            "overview_rgb": overview_rgb,
            "spectral_bands": spectral_bytes,
            "blue_detail": blue_detail,
            "rgb_detail": rgb_detail,
            "three_band": three_band,
            "sample_rate": self.sample_rate / self.color_hop,
            "color_model": COLOR_MODEL,
            "waveform_model": WAVEFORM_MODEL,
            "three_band_model": THREE_BAND_MODEL,
            "display_waveform_model": DISPLAY_WAVEFORM_MODEL,
            "display_render_model": DISPLAY_RENDER_MODEL,
            "height_encoding": HEIGHT_ENCODING,
            "range_encoding": RANGE_ENCODING,
            "beats": beats,
            "cues": [],
            "provenance": {
                "analysis": "soundbox",
                "grid": "soundbox-zero-anchor",
                "owner": "soundbox",
            },
            "stream_provenance": {
                "detail": "soundbox-analysis",
                "blue": "soundbox-analysis",
                "rgb": "soundbox-analysis",
                "three_band": "soundbox-analysis",
            },
        }


def analyze_soundfile(
        path: str | Path, bpm_hint: float = 0,
        checkpoint: Callable[[], bool] | None = None) -> dict | None:
    """Analyze libsndfile-supported audio in bounded memory."""
    try:
        import soundfile as sf

        source = Path(path)
        with sf.SoundFile(str(source)) as audio:
            analyzer = StreamingAnalyzer(
                float(audio.samplerate),
                color_pipeline_delay=_color_pipeline_delay(source),
            )
            while True:
                if checkpoint is not None and not checkpoint():
                    return None
                block = audio.read(
                    131_072, dtype="float32", always_2d=True)
                if not len(block):
                    break
                analyzer.feed(block, owned=True)
                if checkpoint is not None and not checkpoint():
                    return None
        return analyzer.finish(bpm_hint)
    except Exception:  # noqa: BLE001 - Qt/FFmpeg handles player-only codecs
        return None


def merge_import(native: dict | None, imported: dict | None) -> dict | None:
    """Seed a SoundBox record from ANLZ once, with native ultra data winning."""
    if native is None and not imported:
        return None
    result = dict(native or {})
    source = dict(imported or {})
    for field in ("beats", "cues"):
        if source.get(field):
            result[field] = list(source[field])
    heights = source.get("heights") or []
    colors = source.get("colors") or []
    native_rgb = (
        result.get("color_model") == COLOR_MODEL
        and result.get("overview_heights")
        and result.get("overview_rgb")
    )
    if heights and colors and not native_rgb:
        count = min(len(heights), len(colors))
        result["overview_heights"] = bytes(
            max(0, min(255, round(float(value) * 255)))
            for value in heights[:count])
        result["overview_rgb"] = bytes(
            max(0, min(255, int(channel)))
            for row in colors[:count] for channel in list(row)[:3])
        result["sample_rate"] = float(source.get("sample_rate") or COLOR_RATE)
    bands = source.get("_detail_bands")
    blue_detail = source.get("_blue_detail")
    rgb_detail = source.get("_rgb_detail")
    if isinstance(blue_detail, bytes) and blue_detail:
        result["blue_detail"] = bytes(blue_detail)
    if isinstance(rgb_detail, bytes) and rgb_detail:
        result["rgb_detail"] = bytes(rgb_detail)
    if isinstance(bands, bytes) and bands:
        # PWV7 is already the exact three independent shapes Rekordbox draws.
        # Copy it once into the SoundBox-owned cache; the player never depends
        # on the Rekordbox file after this import.
        result["three_band"] = bytes(value & 0x7f for value in bands)
        result["three_band_model"] = THREE_BAND_MODEL
    if (
            result.get("blue_detail")
            and result.get("rgb_detail")
            and result.get("three_band")):
        result["display_waveform_model"] = DISPLAY_WAVEFORM_MODEL
        result["display_render_model"] = DISPLAY_RENDER_MODEL
    if isinstance(bands, bytes) and bands and not result.get("spectral_bands"):
        # Preserve the authentic three-band source in a 24-band-shaped master.
        expanded = bytearray()
        for offset in range(0, len(bands) - 2, 3):
            low, mid, high = bands[offset:offset + 3]
            expanded.extend([low] * 8 + [mid] * 8 + [high] * 8)
        result["spectral_bands"] = bytes(expanded)
    result["provenance"] = {
        "analysis": "rekordbox-import+soundbox"
                    if native else "rekordbox-import",
        "grid": (
            "rekordbox"
            if source.get("beats")
            else dict(result.get("provenance") or {}).get(
                "grid", "soundbox-zero-anchor")
        ),
        "owner": "soundbox",
        "rekordbox_runtime_dependency": False,
    }
    stream_provenance = dict(result.get("stream_provenance") or {
        "detail": "soundbox-analysis" if native else "rekordbox-anlz",
    })
    if isinstance(blue_detail, bytes) and blue_detail:
        stream_provenance["blue"] = "rekordbox-anlz-pwv3"
    if isinstance(rgb_detail, bytes) and rgb_detail:
        stream_provenance["rgb"] = "rekordbox-anlz-pwv5"
    if isinstance(bands, bytes) and bands:
        stream_provenance["three_band"] = "rekordbox-anlz-pwv7"
    result["stream_provenance"] = stream_provenance
    if source.get("beats"):
        result["grid_import"] = {
            "source": "rekordbox-anlz",
            "fingerprint": grid_fingerprint(list(source["beats"])),
        }
    return result
