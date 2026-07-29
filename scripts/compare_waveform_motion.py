"""Measure song-registered waveform redraw across a position phase sweep.

The renderer harness can capture consecutive source-audio-frame positions.
This tool translates every later image back to frame zero by the exact
physical-pixel travel, then measures only the residual redraw. A rigid
song-anchored waveform approaches zero; source/raster aliasing remains visible
after registration even when playback timing itself is exact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import shift


BACKGROUND = np.asarray((13.0, 14.0, 18.0), dtype=np.float32)


def _load_frames(
        root: Path, lane_top: int, mode: str | None = None,
) -> list[np.ndarray]:
    pattern = f"*-{mode}-frame*.png" if mode else "*-frame*.png"
    paths = sorted(root.glob(pattern))
    if len(paths) < 2:
        raise RuntimeError(f"need at least two phase frames under {root}")
    frames = [
        np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)[lane_top:]
        for path in paths
    ]
    shape = frames[0].shape
    if any(frame.shape != shape for frame in frames):
        raise RuntimeError("phase frames do not share one drawable geometry")
    return frames


def compare(
        frames: list[np.ndarray],
        pixels_per_second: float,
        sample_rate: float,
        frame_step: int,
        drawable_scale: float,
) -> dict[str, float | int]:
    base = frames[0]
    travel_per_frame = (
        pixels_per_second * drawable_scale * frame_step / sample_rate
    )
    border = int(np.ceil(travel_per_frame * (len(frames) - 1))) + 2
    if border * 2 >= base.shape[1]:
        raise RuntimeError("phase sweep travels beyond the drawable")
    interior = np.s_[:, border:-border]
    visible_changes = []
    solid_changes = []
    silhouette_xor = []
    base_distance = np.max(np.abs(base - BACKGROUND), axis=2)
    for index, frame in enumerate(frames[1:], 1):
        aligned = shift(
            frame,
            shift=(0.0, index * travel_per_frame, 0.0),
            order=1,
            mode="nearest",
            prefilter=False,
        )
        aligned_distance = np.max(
            np.abs(aligned - BACKGROUND), axis=2)
        difference = np.max(np.abs(base - aligned), axis=2)[interior]
        visible = (
            (base_distance >= 4.0) | (aligned_distance >= 4.0)
        )[interior]
        solid = (
            (base_distance >= 32.0) & (aligned_distance >= 32.0)
        )[interior]
        if np.any(visible):
            visible_changes.append(difference[visible])
        if np.any(solid):
            solid_changes.append(difference[solid])
        base_silhouette = (base_distance >= 12.0)[interior]
        aligned_silhouette = (aligned_distance >= 12.0)[interior]
        silhouette_xor.append(float(np.mean(
            base_silhouette != aligned_silhouette)))
    visible = np.concatenate(visible_changes)
    solid = np.concatenate(solid_changes)
    return {
        "frames": len(frames),
        "travel_per_source_frame_pixels": round(travel_per_frame, 9),
        "visible_mean_code_change": round(float(np.mean(visible)), 6),
        "visible_p99_code_change": round(
            float(np.percentile(visible, 99)), 6),
        "solid_mean_code_change": round(float(np.mean(solid)), 6),
        "silhouette_xor_percent": round(
            float(np.mean(silhouette_xor) * 100.0), 6),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--pixels-per-second", type=float, required=True)
    parser.add_argument("--sample-rate", type=float, default=44_100.0)
    parser.add_argument("--frame-step", type=int, default=1)
    parser.add_argument("--drawable-scale", type=float, default=2.0)
    parser.add_argument("--lane-top", type=int, default=98)
    parser.add_argument("--mode", choices=("BLUE", "RGB", "3BAND"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(
        compare(
            _load_frames(args.frames, args.lane_top, args.mode),
            args.pixels_per_second,
            args.sample_rate,
            args.frame_step,
            args.drawable_scale,
        ),
        indent=2,
        sort_keys=True,
    ))
