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
    matches = sorted(cache_root.glob(f"{track_id}-*.sba5"))
    if not matches:
        matches = sorted(cache_root.glob(f"{track_id}-*.sba4"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one cache for track {track_id}, found {matches}")
    cache_path = matches[0]
    with cache_path.open("rb") as handle:
        header = handle.read(track_analysis._HEADER.size)
        magic, manifest_size = struct.unpack("<4sI", header)
        if magic not in {b"SBA4", b"SBA5"}:
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


def render(args: argparse.Namespace) -> list[Path]:
    cache_path, raw_manifest = _record(args.cache_root, args.track_id)
    source = args.source or Path(raw_manifest["source"]["path"])
    if not source.is_file():
        raise RuntimeError(f"source audio is missing: {source}")
    if track_analysis.is_dataless_source(source):
        raise RuntimeError(f"reference capture will not hydrate audio: {source}")

    manifest = track_analysis.load_manifest(
        args.cache_root, args.track_id, source)
    overview = track_analysis.load_overview(
        args.cache_root, args.track_id, source)
    detail = track_analysis.load_detail(
        args.cache_root, args.track_id, source)
    if manifest is None or overview is None or detail is None:
        raise RuntimeError(f"cache did not fully validate: {cache_path}")
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
        "--source", type=Path,
        help="current source path after a move; cache identity is still validated")
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
