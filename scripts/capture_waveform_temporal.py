"""Capture physical-panel and exact Metal evidence from the same playback run.

This developer harness opens one validated canonical record read-only, walks
the requested waveform modes/zooms, and records a bounded center crop of every
submitted Metal drawable. Each segment contains exact BGRA pixels plus
display-link, GPU, and actual-presentation timestamps. Source audio, rekordbox,
SoundBox AppData, and cache files are never written.

Start an iPhone 60 fps recording before the countdown. Lock focus, exposure,
white balance, and framing, and keep enough display border visible to recover
camera/display phase. The diagnostic-only top/bottom Gray-code strips identify
which 120 Hz panel refresh each camera exposure sampled.

Example:

    PYTHONPATH=. .venv/bin/python scripts/capture_waveform_temporal.py \
        33638604 --position-ms 25597 \
        --out ~/Desktop/soundbox-temporal-2026-07-29

The detailed signed BLUE/RGB contour is the product default. Neither capture
nor the optional current-source override writes the installed app or canonical
cache; offline placeholders are rejected without hydration.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication

from desktop.native_deck import NativeDeckBackend, PlaybackState
from desktop.player import WaveformBar
from scripts.render_waveform_reference import _record
from soundbox import track_analysis


UI_ZOOMS = (
    24.0, 32.0, 48.0, 64.0, 92.0, 128.0, 184.0, 256.0,
    384.0, 576.0, 864.0, 1152.0, 1500.0,
)
CLOSE_ZOOMS = (384.0, 576.0, 864.0, 1152.0, 1500.0)
MODES = ("BLUE", "RGB", "3BAND")
AUDIO_DETAIL_MIP_DIVISORS = track_analysis.RANGE_MIP_DIVISORS


def _fold_frequency(frequency: float, sample_rate: float) -> float:
    """Return the signed Nyquist-folded frequency."""
    return frequency - round(frequency / sample_rate) * sample_rate


def _signed_carrier_lattice_summary(
        samples_per_second: float,
        pixels_per_second: float,
        drawable_scale: float,
        panel_hz: float = 120.0,
        camera_fps: float = 60.0) -> dict[str, float | int]:
    """Mirror the BLUE/RGB mip choice and expose temporal aliases."""
    footprint = (
        samples_per_second
        / max(1.0, pixels_per_second * drawable_scale)
        * (0.5 if pixels_per_second < 64.0
           else 1.0)
    )
    required = max(1.0, footprint * 0.94)
    from soundbox.waveform_detail import DETAIL_DIVISORS
    divisors = DETAIL_DIVISORS if samples_per_second == 1000.0 else AUDIO_DETAIL_MIP_DIVISORS
    divisor = next(
        (
            candidate for candidate in divisors
            if candidate >= required
        ),
        divisors[-1],
    )
    carrier_hz = samples_per_second / divisor
    return {
        "samples_per_second": samples_per_second,
        "selected_divisor": divisor,
        "cells_per_physical_pixel": (
            carrier_hz
            / max(1.0, pixels_per_second * drawable_scale)
        ),
        "cells_per_panel_refresh": carrier_hz / panel_hz,
        "panel_alias_hz_signed": _fold_frequency(carrier_hz, panel_hz),
        "camera_alias_hz_signed": _fold_frequency(
            carrier_hz, camera_fps),
    }


def _process_for(app: QApplication, seconds: float):
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def _wait_for(
        app: QApplication, predicate, seconds: float, message: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    if not predicate():
        raise RuntimeError(message)


def _load_record(cache_root: Path, track_id: str, source: Path | None = None):
    cache_path, raw_manifest = _record(cache_root, track_id)
    source = source or Path(raw_manifest["source"]["path"])
    if not source.is_file():
        raise RuntimeError(f"source audio is missing: {source}")
    if track_analysis.is_dataless_source(source):
        raise RuntimeError(f"temporal capture will not hydrate audio: {source}")
    manifest = track_analysis.load_manifest(cache_root, track_id, source)
    overview = track_analysis.load_overview(cache_root, track_id, source)
    detail = track_analysis.load_detail(cache_root, track_id, source)
    if manifest is None or overview is None or detail is None:
        raise RuntimeError(f"cache did not fully validate: {cache_path}")
    return source, {**manifest, **overview}, detail


def _compress_segment(segment: Path, keep_raw: bool):
    raw = segment / "frames.bgra"
    manifest_path = segment / "manifest.json"
    if not raw.is_file() or not manifest_path.is_file():
        raise RuntimeError(f"incomplete Metal trace: {segment}")
    if keep_raw:
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(f"ffmpeg unavailable; retaining exact raw frames under {segment}")
        return
    manifest = json.loads(manifest_path.read_text())
    width = int(manifest["crop_width_pixels"])
    height = int(manifest["crop_height_pixels"])
    target = segment / "frames.mkv"
    subprocess.run([
        ffmpeg,
        "-v", "error",
        "-f", "rawvideo",
        "-pixel_format", "bgra",
        "-video_size", f"{width}x{height}",
        "-framerate", "120",
        "-i", str(raw),
        "-an",
        "-c:v", "ffv1",
        "-level", "3",
        "-g", "1",
        "-pix_fmt", "bgra",
        str(target),
    ], check=True)
    subprocess.run([
        ffmpeg, "-v", "error", "-i", str(target), "-f", "null", "-",
    ], check=True)
    raw_hash = subprocess.run([
        ffmpeg,
        "-v", "error",
        "-f", "rawvideo",
        "-pixel_format", "bgra",
        "-video_size", f"{width}x{height}",
        "-framerate", "120",
        "-i", str(raw),
        "-map", "0:v:0",
        "-c", "copy",
        "-f", "hash",
        "-hash", "sha256",
        "-",
    ], check=True, capture_output=True, text=True).stdout.strip()
    packed_hash = subprocess.run([
        ffmpeg,
        "-v", "error",
        "-i", str(target),
        "-map", "0:v:0",
        "-pix_fmt", "bgra",
        "-f", "hash",
        "-hash", "sha256",
        "-",
    ], check=True, capture_output=True, text=True).stdout.strip()
    if raw_hash != packed_hash:
        raise RuntimeError(
            f"lossless frame verification failed: {segment}")
    raw.unlink()
    manifest["lossless_video_file"] = target.name
    manifest["raw_file"] = None
    manifest["nominal_video_fps"] = 120
    manifest["decoded_bgra_sha256"] = raw_hash.removeprefix("SHA256=")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _write_timing_summary(
        segment: Path,
        audio_detail_samples_per_second: float | None = None,
        camera_fps: float = 60.0):
    with (segment / "frames.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    def values(name: str) -> list[float]:
        return [float(row[name]) for row in rows]

    def gaps(items: list[float]) -> list[float]:
        items = [value for value in items if value > 0]
        return [
            (later - earlier) * 1000.0
            for earlier, later in zip(items, items[1:])
        ]

    def distribution(items: list[float]) -> dict[str, float | None]:
        if not items:
            return {"minimum": None, "median": None, "maximum": None}
        ordered = sorted(items)
        return {
            "minimum": ordered[0],
            "median": ordered[len(ordered) // 2],
            "maximum": ordered[-1],
        }

    targets = values("target_presentation_timestamp")
    actual = values("actual_presented_time")
    gpu_start = values("gpu_start_time")
    gpu_end = values("gpu_end_time")
    source_frames = [int(row["source_frame"]) for row in rows]
    actual_gaps = gaps(actual)
    target_gaps = gaps(targets)
    dropped = [
        index for index, value in enumerate(actual)
        if value <= 0
    ]
    dropped_headroom = [
        (targets[index] - gpu_end[index]) * 1000.0
        for index in dropped
        if targets[index] > 0 and gpu_end[index] > 0
    ]
    summary = {
        "format": "soundbox-temporal-timing-summary-v1",
        "submitted_frames": len(rows),
        "dropped_presentations": len(dropped),
        "dropped_gpu_deadline_misses": sum(
            headroom < 0 for headroom in dropped_headroom),
        "dropped_gpu_headroom_ms": distribution(dropped_headroom),
        "target_gap_ms": distribution(target_gaps),
        "target_gaps_over_12_5_ms": sum(
            value > 12.5 for value in target_gaps),
        "actual_gap_ms": distribution(actual_gaps),
        "actual_gaps_over_12_5_ms": sum(
            value > 12.5 for value in actual_gaps),
        "presentation_error_ms": distribution([
            (shown - target) * 1000.0
            for shown, target in zip(actual, targets)
            if shown > 0 and target > 0
        ]),
        "gpu_duration_ms": distribution([
            (end - start) * 1000.0
            for start, end in zip(gpu_start, gpu_end)
            if start > 0 and end >= start
        ]),
        "source_frame_delta": distribution([
            float(later - earlier)
            for earlier, later in zip(source_frames, source_frames[1:])
        ]),
    }
    if (
            audio_detail_samples_per_second
            and rows
            and int(rows[0]["waveform_mode"]) != 3):
        summary["signed_carrier_lattice"] = _signed_carrier_lattice_summary(
            audio_detail_samples_per_second,
            float(rows[0]["pixels_per_second"]),
            float(rows[0]["drawable_scale"]),
            camera_fps=camera_fps,
        )
    (segment / "timing-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")


def _write_session_summary(output: Path, segments: list[dict]):
    summaries = [
        json.loads(
            (output / segment["directory"] / "timing-summary.json").read_text())
        for segment in segments
    ]
    result = {
        "format": "soundbox-temporal-session-summary-v1",
        "segments": len(summaries),
        "submitted_frames": sum(
            item["submitted_frames"] for item in summaries),
        "dropped_presentations": sum(
            item["dropped_presentations"] for item in summaries),
        "dropped_gpu_deadline_misses": sum(
            item["dropped_gpu_deadline_misses"] for item in summaries),
        "audio_underruns": sum(
            int(segment.get("audio_underruns", 0))
            for segment in segments),
        "display_link_target_gaps_over_12_5_ms": sum(
            item["target_gaps_over_12_5_ms"] for item in summaries),
        "actual_presentation_gaps_over_12_5_ms": sum(
            item["actual_gaps_over_12_5_ms"] for item in summaries),
        "largest_actual_gap_ms": max(
            item["actual_gap_ms"]["maximum"] or 0.0 for item in summaries),
        "largest_presentation_error_ms": max(
            item["presentation_error_ms"]["maximum"] or 0.0
            for item in summaries),
        "largest_gpu_duration_ms": max(
            item["gpu_duration_ms"]["maximum"] or 0.0 for item in summaries),
    }
    (output / "session-summary.json").write_text(
        json.dumps(result, indent=2) + "\n")


def capture(args: argparse.Namespace):
    output = args.out.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    source, overview, detail = _load_record(
        args.cache_root.expanduser(), args.track_id, args.source)
    audio_detail_rate = float(
        detail.get("_audio_detail", {}).get("rate") or 0.0)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    wave = WaveformBar()
    wave.resize(args.width_points, args.height_points)
    wave.setWindowFlags(Qt.WindowStaysOnTopHint)
    wave.show()
    wave.raise_()
    wave.activateWindow()
    app.processEvents()

    backend = NativeDeckBackend(wave)
    segments = []
    try:
        wave.set_native_rendering(True)
        backend.set_gain(0.0)
        backend.begin_track(1)
        backend.setSource(QUrl.fromLocalFile(str(source)))
        _wait_for(
            app,
            lambda: backend.duration() > 0,
            5.0,
            "native decoder did not publish track duration")
        if not all(backend.set_complete_analysis(overview, detail, 1)):
            raise RuntimeError("native deck rejected the validated V4 record")
        backend.sync_view_geometry()

        print(
            "READY [SIGNED-CARRIER]: "
            f"track={args.track_id} source={source.name!r}\n"
            "Camera target: 4K60 SDR/Rec.709, 1/240 shutter, fixed ISO/WB/"
            "focus, stabilization off, rigid landscape framing.\n"
            f"Start the locked iPhone {args.camera_fps} fps recording now. "
            f"The sequence begins in {args.countdown:g} seconds.")
        for remaining in range(int(args.countdown), 0, -1):
            wave.setWindowTitle(
                f"SoundBox temporal trace starts in {remaining}")
            print(f"{remaining}…", flush=True)
            _process_for(app, 1.0)

        max_frames = round(args.seconds * 120)
        expected_frame = round(
            args.position_ms * backend._sample_rate / 1000.0)
        total = len(args.modes) * len(args.zooms)
        if not args.skip_calibration:
            segment = output / "00-CAMERA-CALIBRATION"
            backend.pause()
            backend.setPosition(args.position_ms)
            backend.set_visual_position(args.position_ms, True)
            backend.set_waveform_mode("3BAND")
            backend.set_zoom(1500.0)
            backend.request_display()
            _wait_for(
                app,
                lambda: backend.diagnostic_presented_frame()
                == expected_frame,
                3.0,
                "camera calibration: fixed source frame did not present")
            wave.setWindowTitle(
                "CAMERA CALIBRATION — FIXED WAVE / 120 Hz PHASE CODE")
            print(
                "[CAL] fixed 3BAND waveform; only the phase code should move",
                flush=True)
            _process_for(app, args.stopped_seconds)
            backend.diagnostic_begin_frame_trace(
                max_frames, args.crop_width_pixels)
            _wait_for(
                app,
                lambda: backend.diagnostic_frame_trace_status()[
                    "submitted_frames"] >= max_frames,
                args.seconds * 3 + 5.0,
                "camera calibration: drawable capture did not complete")
            backend.set_visual_position(args.position_ms, False)
            _process_for(app, 0.05)
            backend.diagnostic_finish_frame_trace(segment)
            _write_timing_summary(
                segment, audio_detail_rate, args.camera_fps)
            segments.append({
                "directory": segment.name,
                "kind": "camera-calibration",
                "mode": "3BAND",
                "zoom_pixels_per_second": 1500.0,
                "start_position_ms": args.position_ms,
                "submitted_frames": max_frames,
            })
            _process_for(app, args.stopped_seconds)

        number = 0
        for mode in args.modes:
            for zoom in args.zooms:
                number += 1
                label = f"{mode}-{int(zoom)}"
                segment = output / f"{number:02d}-{label}"
                if segment.exists():
                    raise RuntimeError(f"segment already exists: {segment}")
                backend.pause()
                backend.setPosition(args.position_ms)
                backend.set_visual_position(args.position_ms, True)
                backend.set_waveform_mode(mode)
                backend.set_zoom(zoom)
                backend.request_display()
                _wait_for(
                    app,
                    lambda: backend.diagnostic_presented_frame()
                    == expected_frame,
                    3.0,
                    f"{label}: paused start frame did not present "
                    f"(expected={expected_frame}, "
                    f"presented={backend.diagnostic_presented_frame()}, "
                    f"scheduled={backend.diagnostic_scheduled_frame()})")
                wave.setWindowTitle(
                    f"{number:02d}/{total}  {mode}  {zoom:.0f} px/s  STOPPED")
                print(
                    f"[{number:02d}/{total}] {mode} {zoom:.0f} px/s: "
                    "stopped reference",
                    flush=True)
                _process_for(app, args.stopped_seconds)

                backend.set_visual_position(args.position_ms, False)
                _process_for(app, 0.05)
                wave.setWindowTitle(
                    f"{number:02d}/{total}  {mode}  {zoom:.0f} px/s  PLAYING")
                underruns_before = backend.diagnostic_underruns()
                backend.play()
                _wait_for(
                    app,
                    lambda: backend.playbackState() == PlaybackState.PLAYING,
                    2.0,
                    f"{label}: native playback did not start")
                _process_for(app, 0.1)
                status = backend.diagnostic_begin_frame_trace(
                    max_frames, args.crop_width_pixels)
                print(
                    f"    PLAYING — exact Metal crop "
                    f"{status['crop_width_pixels']}×"
                    f"{status['crop_height_pixels']}",
                    flush=True)
                _wait_for(
                    app,
                    lambda: backend.diagnostic_frame_trace_status()[
                        "submitted_frames"] >= max_frames,
                    args.seconds * 3 + 5.0,
                    f"{label}: drawable capture did not reach {max_frames}")
                backend.pause()
                _wait_for(
                    app,
                    lambda: backend.playbackState() != PlaybackState.PLAYING,
                    1.0,
                    f"{label}: native deck did not pause")
                underruns_after = backend.diagnostic_underruns()
                backend.diagnostic_finish_frame_trace(segment)
                _write_timing_summary(
                    segment, audio_detail_rate, args.camera_fps)
                status = json.loads(
                    (segment / "manifest.json").read_text())
                if (
                        status["gpu_completed_frames"] != max_frames
                        or status["presentation_completed_frames"]
                        != max_frames):
                    raise RuntimeError(
                        f"{label}: incomplete Metal callback ledger: {status}")
                segments.append({
                    "directory": segment.name,
                    "kind": "moving-waveform",
                    "mode": mode,
                    "zoom_pixels_per_second": zoom,
                    "start_position_ms": args.position_ms,
                    "submitted_frames": max_frames,
                    "audio_underruns": max(
                        0, underruns_after - underruns_before),
                })
                wave.setWindowTitle(
                    f"{number:02d}/{total}  {mode}  {zoom:.0f} px/s  HELD")
                _process_for(app, args.stopped_seconds)

        (output / "session.json").write_text(json.dumps({
            "format": "soundbox-temporal-session-v1",
            "track_id": args.track_id,
            "source_name": source.name,
            "audio_detail_samples_per_second": audio_detail_rate,
            "camera_fps_requested": args.camera_fps,
            "camera_protocol": {
                "resolution": "4K preferred; 1080p acceptable",
                "color": "SDR Rec.709",
                "shutter_seconds": "1/240",
                "exposure_iso_white_balance_focus": "manual/fixed",
                "stabilization": "off",
                "framing": "rigid landscape with display border visible",
            },
            "crop_width_pixels_requested": args.crop_width_pixels,
            "segments": segments,
        }, indent=2) + "\n")
        _write_session_summary(output, segments)
    finally:
        backend.diagnostic_cancel_frame_trace()
        backend.close()
        wave.close()
        app.processEvents()

    print("Metal capture complete; losslessly packing exact BGRA sequences…")
    for segment in (output / item["directory"] for item in segments):
        _compress_segment(segment, args.keep_raw)
    print(
        f"DONE: {len(segments)} synchronized segments under {output}\n"
        "Keep the iPhone original beside this directory; do not transcode it.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("track_id")
    parser.add_argument(
        "--source", type=Path,
        help="current source path after a move; cache identity is still validated")
    parser.add_argument("--position-ms", type=int, required=True)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=(
            Path.home()
            / "Library/Application Support/LowFreqLabs/Soundbox/"
              "track_analysis"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument(
        "--zooms", nargs="+", type=float, default=CLOSE_ZOOMS,
        help=(
            "UI zoom stops to capture; defaults to the five close-detail "
            f"stops, while the full accepted set is {UI_ZOOMS}"))
    parser.add_argument(
        "--seconds", type=float, default=1.5,
        help="playing evidence per segment; captured at up to 120 drawables/s")
    parser.add_argument("--stopped-seconds", type=float, default=0.75)
    parser.add_argument("--countdown", type=float, default=5.0)
    parser.add_argument(
        "--camera-fps",
        type=int,
        choices=(30, 60),
        default=60)
    parser.add_argument("--crop-width-pixels", type=int, default=768)
    parser.add_argument("--width-points", type=int, default=1458)
    parser.add_argument("--height-points", type=int, default=155)
    parser.add_argument(
        "--keep-raw", action="store_true",
        help="retain frames.bgra instead of replacing it with lossless FFV1")
    parser.add_argument(
        "--skip-calibration", action="store_true",
        help="omit the fixed-waveform 120 Hz camera phase calibration segment")
    args = parser.parse_args()
    if args.position_ms < 0:
        parser.error("--position-ms must be nonnegative")
    if args.seconds <= 0 or round(args.seconds * 120) < 1:
        parser.error("--seconds must capture at least one drawable")
    if round(args.seconds * 120) > 600:
        parser.error("--seconds exceeds the native 600-drawable safety bound")
    if args.stopped_seconds < 0 or args.countdown < 0:
        parser.error("timing values must be nonnegative")
    if args.crop_width_pixels < 64:
        parser.error("--crop-width-pixels must be at least 64")
    if any(zoom not in UI_ZOOMS for zoom in args.zooms):
        parser.error(f"--zooms must come from {UI_ZOOMS}")
    return args


if __name__ == "__main__":
    capture(parse_args())
