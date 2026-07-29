"""Render real SoundBox waveform caches through the native Metal deck.

This is a developer acceptance harness. It never opens rekordbox, never writes
SoundBox AppData, and never starts playback. The source audio and canonical
cache are opened read-only by the same native/Python paths used by the app.

Examples:

    PYTHONPATH=. .venv/bin/python scripts/render_waveform_reference.py \
        33638604 --position-ms 25597 --zoom 768 --out /tmp/bad-decisions

    PYTHONPATH=. .venv/bin/python scripts/render_waveform_reference.py \
        54231927 --position-ms 27000 --zoom 1536 --out /tmp/high-and-scared

    PYTHONPATH=. .venv/bin/python scripts/render_waveform_reference.py \
        --corpus --out /tmp/rekordbox-waveform-corpus
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import time

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from desktop.native_deck import NativeDeckBackend
from desktop.player import WaveformBar
from soundbox import track_analysis


DEFAULT_CACHE_ROOT = (
    Path.home()
    / "Library/Application Support/LowFreqLabs/Soundbox/track_analysis"
)
DEFAULT_REFERENCE_ROOT = Path(
    "/Users/sonnypiccolo/Desktop/rekordbox wave")
REFERENCE_CASES = (
    ("33638604", "BLUE", 25_597, 768.0, "BLUE - Bad Decisions.png"),
    ("33638604", "RGB", 25_597, 768.0, "RGB - Bad Decisions .png"),
    ("33638604", "3BAND", 25_597, 768.0, "3BAND - Bad Decisions.png"),
    ("54231927", "BLUE", 27_000, 1536.0, "BLUE - HIGH & SCARED.png"),
    ("54231927", "RGB", 27_000, 1536.0, "RGB - HIGH & SCARED.png"),
    ("54231927", "3BAND", 27_000, 1536.0, "3BAND - HIGH & SCARED.png"),
)


def _record(cache_root: Path, track_id: str) -> tuple[Path, dict]:
    matches = sorted(cache_root.glob(f"{track_id}-*.sba4"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one cache for track {track_id}, found {matches}")
    cache_path = matches[0]
    with cache_path.open("rb") as handle:
        header = handle.read(track_analysis._HEADER.size)
        magic, manifest_size = struct.unpack("<4sI", header)
        if magic != b"SBA4":
            raise RuntimeError(f"invalid cache header: {cache_path}")
        manifest = json.loads(handle.read(manifest_size))
    return cache_path, manifest


def _bar_position(beats: list, bar_beat: str) -> int:
    try:
        bar_text, beat_text = bar_beat.split(".", 1)
        bar = max(1, int(bar_text))
        beat = max(1, min(4, int(beat_text)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("--bar must look like 17.3") from exc
    index = (bar - 1) * 4 + beat - 1
    if index >= len(beats):
        raise RuntimeError(
            f"bar {bar_beat} needs beat row {index}, cache has {len(beats)}")
    return int(beats[index][1])


def _wait_for(predicate, app: QApplication, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _experimental_rms_envelope(source: Path) -> dict:
    """Build a read-only 150 Hz polarity RMS stream for renderer R&D."""
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(source) as audio:
        sample_rate = float(audio.samplerate)
        window = max(1, round(sample_rate / 150.0))
        upper_rows = []
        lower_rows = []
        tail = np.empty(0, dtype=np.float32)
        while True:
            block = audio.read(262_144, dtype="float32", always_2d=True)
            if not len(block):
                break
            midpoint = block.mean(axis=1, dtype=np.float32)
            values = np.concatenate((tail, midpoint))
            keep = len(values) - len(values) % window
            if keep:
                rows = values[:keep].reshape(-1, window)
                upper_rows.append(np.sqrt(
                    np.mean(np.square(np.maximum(rows, 0.0)), axis=1)))
                lower_rows.append(np.sqrt(
                    np.mean(np.square(np.minimum(rows, 0.0)), axis=1)))
            tail = values[keep:]
        if tail.size:
            upper_rows.append(np.asarray([
                np.sqrt(np.mean(np.square(np.maximum(tail, 0.0))))
            ], dtype=np.float32))
            lower_rows.append(np.asarray([
                np.sqrt(np.mean(np.square(np.minimum(tail, 0.0))))
            ], dtype=np.float32))
    upper = np.concatenate(upper_rows)
    lower = np.concatenate(lower_rows)
    positive = np.concatenate((upper, lower))
    positive = positive[positive > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0

    def encode(rows) -> bytes:
        return np.clip(
            np.rint(rows / max(ceiling, 1e-12) * 65_535),
            0, 65_535,
        ).astype("<u2").tobytes()

    return {
        "rate": sample_rate / window,
        "heights": encode(upper),
        "lower_heights": encode(lower),
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def _experimental_smoothed_envelope(
        source: Path, method: str, window_ms: float,
        output_rate: float = 150.0) -> dict:
    """Build a symmetric amplitude envelope for geometry probes."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    import soundfile as sf

    audio, sample_rate = sf.read(
        source, dtype="float32", always_2d=True)
    hop = max(1, round(float(sample_rate) / output_rate))
    window = max(1, round(float(sample_rate) * window_ms / 1000.0))
    if method in {"rms", "crest"}:
        power = np.mean(np.square(audio), axis=1, dtype=np.float32)
        rms = np.sqrt(uniform_filter1d(
            power, size=window, mode="nearest"))
        if method == "crest":
            peak = maximum_filter1d(
                np.max(np.abs(audio), axis=1),
                size=window, mode="nearest")
            values = np.sqrt(rms * peak)
        else:
            values = rms
    elif method == "mean":
        values = uniform_filter1d(
            np.mean(np.abs(audio), axis=1, dtype=np.float32),
            size=window, mode="nearest")
    else:
        magnitude = np.max(np.abs(audio), axis=1)
        values = maximum_filter1d(
            magnitude, size=window, mode="nearest")
    rows = values[::hop]
    positive = rows[rows > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0
    encoded = np.clip(
        np.rint(rows / max(ceiling, 1e-12) * 65_535),
        0, 65_535,
    ).astype("<u2").tobytes()
    return {
        "rate": float(sample_rate) / hop,
        "heights": encoded,
        "lower_heights": encoded,
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def _experimental_polarity_smoothed_envelope(
        source: Path, method: str, window_ms: float,
        output_rate: float = 6300.0) -> dict:
    """Build a close-rate polarity envelope without modifying live caches."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    import soundfile as sf

    audio, sample_rate = sf.read(
        source, dtype="float32", always_2d=True)
    midpoint = audio.mean(axis=1, dtype=np.float32)
    sides = (
        np.maximum(midpoint, 0.0),
        np.maximum(-midpoint, 0.0),
    )
    window = max(1, round(float(sample_rate) * window_ms / 1000.0))
    hop = max(1, round(float(sample_rate) / output_rate))
    filtered = []
    for values in sides:
        if method in {"rms", "crest"}:
            rms = np.sqrt(uniform_filter1d(
                np.square(values), size=window, mode="nearest"))
            if method == "crest":
                peak = maximum_filter1d(
                    values, size=window, mode="nearest")
                result = np.sqrt(rms * peak)
            else:
                result = rms
        elif method == "mean":
            result = uniform_filter1d(
                values, size=window, mode="nearest")
        else:
            result = maximum_filter1d(
                values, size=window, mode="nearest")
        filtered.append(result[::hop])
    positive = np.concatenate(filtered)
    positive = positive[positive > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0

    def encode(values) -> bytes:
        return np.clip(
            np.rint(values / max(ceiling, 1e-12) * 65_535),
            0, 65_535,
        ).astype("<u2").tobytes()

    return {
        "rate": float(sample_rate) / hop,
        "heights": encode(filtered[0]),
        "lower_heights": encode(filtered[1]),
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def _experimental_cached_polarity_crest(
        audio_detail: dict, window_ms: float) -> dict:
    """Derive a crest envelope from the existing read-only polarity cache."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d, uniform_filter1d

    rate = float(audio_detail.get("rate") or 0.0)
    upper = np.frombuffer(
        bytes(audio_detail.get("heights") or b""), dtype="<u2").astype(
            np.float32)
    lower = np.frombuffer(
        bytes(audio_detail.get("lower_heights") or b""), dtype="<u2").astype(
            np.float32)
    count = min(len(upper), len(lower))
    if rate <= 0.0 or count == 0:
        raise RuntimeError("cached polarity detail is unavailable")
    window = max(1, round(rate * window_ms / 1000.0))
    filtered = []
    for values in (upper[:count], lower[:count]):
        rms = np.sqrt(uniform_filter1d(
            np.square(values), size=window, mode="nearest"))
        peak = maximum_filter1d(
            values, size=window, mode="nearest")
        filtered.append(np.sqrt(rms * peak))
    positive = np.concatenate(filtered)
    positive = positive[positive > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0

    def encode(values) -> bytes:
        return np.clip(
            np.rint(values / max(ceiling, 1e-12) * 65_535),
            0, 65_535,
        ).astype("<u2").tobytes()

    return {
        "rate": rate,
        "heights": encode(filtered[0]),
        "lower_heights": encode(filtered[1]),
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def _experimental_signed_range(source: Path) -> dict:
    """Build close-rate signed min/max rows for BLUE renderer R&D."""
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(
        source, dtype="float32", always_2d=True)
    midpoint = audio.mean(axis=1, dtype=np.float32)
    window = max(1, round(float(sample_rate) / track_analysis.ULTRA_RATE))
    keep = len(midpoint) - len(midpoint) % window
    rows = midpoint[:keep].reshape(-1, window)
    minimum = rows.min(axis=1)
    maximum = rows.max(axis=1)
    if keep < len(midpoint):
        minimum = np.concatenate((minimum, [midpoint[keep:].min()]))
        maximum = np.concatenate((maximum, [midpoint[keep:].max()]))
    positive = np.abs(np.concatenate((minimum, maximum)))
    positive = positive[positive > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0

    def encode(values) -> bytes:
        return np.clip(
            np.rint(values / max(ceiling, 1e-12) * 32_767),
            -32_767, 32_767,
        ).astype("<i2").tobytes()

    return {
        "range_min": encode(minimum),
        "range_max": encode(maximum),
    }


def _experimental_weighted_peak_envelope(
        source: Path, weight: str, window_ms: float) -> dict:
    """Retain block-peak edges while weighting them by short-term energy."""
    import numpy as np
    from scipy.ndimage import uniform_filter1d
    import soundfile as sf

    audio, sample_rate = sf.read(
        source, dtype="float32", always_2d=True)
    midpoint = audio.mean(axis=1, dtype=np.float32)
    channel_peak = np.max(np.abs(audio), axis=1)
    magnitude = np.maximum(
        np.abs(midpoint),
        channel_peak * track_analysis.CHANNEL_SAFETY_FLOOR)
    hop = max(1, round(float(sample_rate) / 6300.0))
    keep = len(magnitude) - len(magnitude) % hop
    peaks = magnitude[:keep].reshape(-1, hop).max(axis=1)
    window = max(1, round(float(sample_rate) * window_ms / 1000.0))
    if weight == "rms":
        energy = np.sqrt(uniform_filter1d(
            np.square(magnitude), size=window, mode="nearest"))
    else:
        energy = uniform_filter1d(
            magnitude, size=window, mode="nearest")
    sampled = energy[:keep].reshape(-1, hop).mean(axis=1)
    values = np.sqrt(peaks * sampled)
    positive = values[values > 0]
    ceiling = float(np.percentile(positive, 99.9)) if positive.size else 1.0
    encoded = np.clip(
        np.rint(values / max(ceiling, 1e-12) * 65_535),
        0, 65_535,
    ).astype("<u2").tobytes()
    return {
        "rate": float(sample_rate) / hop,
        "heights": encoded,
        "lower_heights": encoded,
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def _experimental_band_envelope(source: Path, mode: str) -> dict:
    """Build one scalar envelope from fresh SoundBox frequency analysis."""
    import numpy as np

    analyzed = track_analysis.analyze_soundfile(source)
    if analyzed is None:
        raise RuntimeError(f"could not analyze experimental bands: {source}")
    rows = np.frombuffer(
        analyzed["three_band"], dtype=np.uint8).reshape(-1, 3)
    if mode == "low":
        values = rows[:, 2]
    elif mode == "mid":
        values = rows[:, 0]
    elif mode == "high":
        values = rows[:, 1]
    else:
        values = rows.max(axis=1)
    encoded = (values.astype("<u2") * 516).tobytes()
    return {
        "rate": float(analyzed["sample_rate"]),
        "heights": encoded,
        "lower_heights": encoded,
        "encoding": track_analysis.HEIGHT_ENCODING,
    }


def render(args: argparse.Namespace) -> list[Path]:
    cache_path, raw_manifest = _record(args.cache_root, args.track_id)
    source = Path(raw_manifest["source"]["path"])
    if not source.is_file():
        raise RuntimeError(f"source audio is missing: {source}")

    manifest = track_analysis.load_manifest(
        args.cache_root, args.track_id, source)
    overview = track_analysis.load_overview(
        args.cache_root, args.track_id, source)
    detail = track_analysis.load_detail(
        args.cache_root, args.track_id, source)
    if manifest is None or overview is None or detail is None:
        raise RuntimeError(f"cache did not fully validate: {cache_path}")
    if args.experimental_envelope == "range":
        detail = {
            **detail,
            "_audio_detail": {
                **detail["_audio_detail"],
                **_experimental_signed_range(source),
            },
        }
    elif args.experimental_envelope == "rms":
        detail = {
            **detail,
            "_audio_detail": _experimental_rms_envelope(source),
        }
    elif args.experimental_envelope in {
            "rms20", "rms40", "rms80", "peak20", "peak40", "peak80"}:
        method = "rms" if args.experimental_envelope.startswith("rms") \
            else "peak"
        window_ms = float(args.experimental_envelope.removeprefix(method))
        detail = {
            **detail,
            "_audio_detail": _experimental_smoothed_envelope(
                source, method, window_ms),
        }
    elif args.experimental_envelope in {
            "rms3h", "rms7h", "rms14h",
            "peak3h", "peak7h", "peak14h",
            "crest3h", "crest7h", "mean3h", "mean7h"}:
        method = next(
            name for name in ("rms", "peak", "crest", "mean")
            if args.experimental_envelope.startswith(name))
        window_ms = float(
            args.experimental_envelope.removeprefix(method).removesuffix("h"))
        detail = {
            **detail,
            "_audio_detail": _experimental_smoothed_envelope(
                source, method, window_ms, 6300.0),
        }
    elif args.experimental_envelope in {
            "prms05", "prms1", "prms2", "prms4",
            "pcrest05", "pcrest1", "pcrest2", "pcrest4",
            "pmean05", "pmean1", "pmean2", "pmean4",
            "ppeak05", "ppeak1", "ppeak2", "ppeak4"}:
        method = next(
            name for name in ("rms", "crest", "mean", "peak")
            if args.experimental_envelope.startswith(f"p{name}"))
        window_text = args.experimental_envelope.removeprefix(f"p{method}")
        window_ms = 0.5 if window_text == "05" else float(window_text)
        detail = {
            **detail,
            "_audio_detail": _experimental_polarity_smoothed_envelope(
                source, method, window_ms),
        }
    elif args.experimental_envelope in {
            "cachecrest05", "cachecrest1", "cachecrest2", "cachecrest4"}:
        window_text = args.experimental_envelope.removeprefix("cachecrest")
        window_ms = 0.5 if window_text == "05" else float(window_text)
        detail = {
            **detail,
            "_audio_detail": _experimental_cached_polarity_crest(
                detail["_audio_detail"], window_ms),
        }
    elif args.experimental_envelope in {
            "blockrms3", "blockrms7", "blockmean3", "blockmean7"}:
        weight = "rms" if "rms" in args.experimental_envelope else "mean"
        window_ms = float(
            args.experimental_envelope.removeprefix(
                f"block{weight}"))
        detail = {
            **detail,
            "_audio_detail": _experimental_weighted_peak_envelope(
                source, weight, window_ms),
        }
    elif args.experimental_envelope == "display":
        detail = {**detail, "_audio_detail": None}
    elif args.experimental_envelope in {"low", "mid", "high", "bandmax"}:
        detail = {
            **detail,
            "_audio_detail": _experimental_band_envelope(
                source, args.experimental_envelope),
        }
    overview = {**manifest, **overview}

    app = QApplication.instance() or QApplication(sys.argv[:1])
    wave = WaveformBar()
    wave.setWindowTitle(f"SoundBox reference {args.track_id}")
    wave.setWindowFlags(
        Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    wave.resize(args.width_points, args.height_points)
    wave.move(0, 0)
    wave.show()
    wave.raise_()
    wave.activateWindow()
    app.processEvents()

    backend = NativeDeckBackend(wave)
    try:
        wave.set_native_rendering(True)
        backend.begin_track(1)
        backend.setSource(QUrl.fromLocalFile(str(source)))
        if not _wait_for(lambda: backend.duration() > 0, app):
            backend.synchronize()
        if backend.duration() <= 0:
            raise RuntimeError("native decoder did not publish track duration")
        if not all(backend.set_complete_analysis(overview, detail, 1)):
            raise RuntimeError("native deck rejected the real waveform cache")

        backend.set_zoom(args.zoom)
        position_ms = (
            _bar_position(manifest.get("beats") or [], args.bar)
            if args.bar else int(args.position_ms)
        )
        backend.sync_view_geometry()

        args.out.mkdir(parents=True, exist_ok=True)
        outputs = []
        for mode in args.modes:
            backend.set_waveform_mode(mode)
            for position_index in range(args.position_count):
                capture_position = (
                    position_ms
                    + (
                        position_index * args.position_step_frames
                        / max(1.0, backend._sample_rate) * 1000.0
                        if args.position_step_frames is not None
                        else position_index * args.position_step_ms
                    )
                )
                presented_before = backend.diagnostic_presented_frame()
                expected_frame = round(
                    capture_position * backend._sample_rate / 1000.0)
                backend.set_visual_position(capture_position, True)
                backend.request_display()
                if not _wait_for(
                        lambda: (
                            backend.diagnostic_presented_frame()
                            == expected_frame),
                        app, 3.0):
                    backend._poll()
                    error_message = bytes(
                        backend._snapshot.error_message
                    ).split(b"\0", 1)[0].decode("utf-8", "replace")
                    raise RuntimeError(
                        "native renderer did not present the requested frame "
                        f"(error={backend._snapshot.error_code}: "
                        f"{error_message or 'none'}; "
                        f"expected={expected_frame}, "
                        f"before={presented_before}, "
                        f"presented={backend.diagnostic_presented_frame()}, "
                        f"scheduled={backend.diagnostic_scheduled_frame()}, "
                        f"display={backend.diagnostic_display_stats()})")
                screen = QGuiApplication.screenAt(
                    wave.mapToGlobal(
                        QPoint(wave.width() // 2, wave.height() // 2))
                ) or QGuiApplication.primaryScreen()
                origin = wave.mapToGlobal(QPoint(0, 0))
                image = screen.grabWindow(
                    0, origin.x(), origin.y(), wave.width(), wave.height())
                if args.position_count == 1:
                    suffix = ""
                elif args.position_step_frames is not None:
                    suffix = f"-frame{position_index:03d}"
                else:
                    suffix = f"-{capture_position:g}ms"
                target = args.out / f"{args.track_id}-{mode}{suffix}.png"
                if image.isNull() or not image.save(str(target)):
                    raise RuntimeError(
                        f"could not capture native render: {target}")
                outputs.append(target)

        print(
            f"rendered track={args.track_id} source={source.name!r} "
            f"position_ms={position_ms}"
            f"..{(
                position_ms
                + (
                    (args.position_count - 1) * args.position_step_frames
                    / max(1.0, backend._sample_rate) * 1000.0
                    if args.position_step_frames is not None
                    else (args.position_count - 1) * args.position_step_ms
                )
            ):g} "
            f"zoom={backend.diagnostic_zoom():.0f} "
            f"geometry={backend.diagnostic_view_geometry()}"
        )
        return outputs
    finally:
        backend.close()
        wave.close()
        app.processEvents()


def render_corpus(args: argparse.Namespace) -> list[Path]:
    missing = [
        args.reference_root / filename
        for _track_id, _mode, _position_ms, _zoom, filename in REFERENCE_CASES
        if not (args.reference_root / filename).is_file()
    ]
    if missing:
        raise RuntimeError(f"reference corpus is incomplete: {missing}")
    outputs = []
    for track_id, mode, position_ms, zoom, filename in REFERENCE_CASES:
        case = argparse.Namespace(
            **{
                **vars(args),
                "track_id": track_id,
                "bar": None,
                "position_ms": position_ms,
                "zoom": zoom,
                "modes": (mode,),
                "out": args.out / Path(filename).stem,
            }
        )
        outputs.extend(render(case))
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("track_id", nargs="?")
    parser.add_argument(
        "--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument(
        "--reference-root", type=Path, default=DEFAULT_REFERENCE_ROOT)
    parser.add_argument(
        "--corpus", action="store_true",
        help="render the exact six supplied Rekordbox comparison cases")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--bar")
    where.add_argument("--position-ms", type=int)
    parser.add_argument(
        "--position-count", type=int, default=1,
        help="capture this many consecutive positions in the same native session")
    parser.add_argument(
        "--position-step-ms", type=int, default=1,
        help="millisecond spacing between captures when --position-count > 1")
    parser.add_argument(
        "--position-step-frames", type=int,
        help=(
            "source-audio-frame spacing between captures; overrides "
            "--position-step-ms for subpixel phase sweeps"))
    parser.add_argument("--zoom", type=float, default=92.0)
    parser.add_argument(
        "--experimental-envelope",
        choices=(
            "display", "range", "rms", "rms20", "rms40", "rms80",
            "peak20", "peak40", "peak80",
            "rms3h", "rms7h", "rms14h",
            "peak3h", "peak7h", "peak14h",
            "crest3h", "crest7h", "mean3h", "mean7h",
            "prms05", "prms1", "prms2", "prms4",
            "pcrest05", "pcrest1", "pcrest2", "pcrest4",
            "pmean05", "pmean1", "pmean2", "pmean4",
            "ppeak05", "ppeak1", "ppeak2", "ppeak4",
            "cachecrest05", "cachecrest1", "cachecrest2", "cachecrest4",
            "blockrms3", "blockrms7", "blockmean3", "blockmean7",
            "low", "mid", "high", "bandmax"),
        help="replace only geometry in memory; never writes the canonical cache")
    parser.add_argument("--width-points", type=int, default=1458)
    parser.add_argument("--height-points", type=int, default=155)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--modes", nargs="+", choices=("BLUE", "RGB", "3BAND"),
        default=("BLUE", "RGB", "3BAND"))
    args = parser.parse_args()
    if args.position_count < 1:
        parser.error("--position-count must be at least 1")
    if args.position_step_frames is not None and args.position_step_frames < 1:
        parser.error("--position-step-frames must be at least 1")
    if not args.corpus and (
            not args.track_id
            or (args.bar is None and args.position_ms is None)):
        parser.error(
            "provide TRACK_ID and either --bar/--position-ms, or use --corpus")
    return args


if __name__ == "__main__":
    options = parse_args()
    render_corpus(options) if options.corpus else render(options)
