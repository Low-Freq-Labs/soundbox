"""Measure a native SoundBox render against one supplied Rekordbox capture.

The comparison is deliberately about the visible waveform silhouette. It
registers the images at the playhead, removes the SoundBox overview/ruler,
normalizes for the different vertical crop heights, and writes an outline
overlay in which green is Rekordbox and magenta is SoundBox.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def _color_mask(image: np.ndarray) -> np.ndarray:
    high = image.max(axis=2)
    low = image.min(axis=2)
    chroma = high - low
    return ((high > 55) & (chroma > 20)) | (low > 155)


def _median5(values: np.ndarray) -> np.ndarray:
    padded = np.pad(values, (2, 2), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 5)
    return np.median(windows, axis=1)


def _silhouette(
        image: np.ndarray, center_y: int,
        top: int = 0, bottom: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    bottom = image.shape[0] if bottom is None else bottom
    mask = _color_mask(image[top:bottom])
    local_center = center_y - top
    upper = np.zeros(image.shape[1], dtype=np.float64)
    lower = np.zeros(image.shape[1], dtype=np.float64)
    for x in range(image.shape[1]):
        rows = np.flatnonzero(mask[:, x])
        above = rows[rows <= local_center]
        below = rows[rows >= local_center]
        if above.size:
            upper[x] = local_center - above.min()
        if below.size:
            lower[x] = below.max() - local_center
    # Cue/grid stems are one-sided or only a few pixels wide. A short median
    # removes them without rounding the much wider musical transients.
    return _median5(upper), _median5(lower)


def _red_playhead(image: np.ndarray) -> int:
    red = (
        (image[:, :, 0] > 145)
        & (image[:, :, 0] > image[:, :, 1] * 1.8)
        & (image[:, :, 0] > image[:, :, 2] * 1.5)
    )
    scores = red.sum(axis=0)
    middle = slice(image.shape[1] // 3, image.shape[1] * 2 // 3)
    return int(np.argmax(scores[middle]) + image.shape[1] // 3)


def _normalize(upper: np.ndarray, lower: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.concatenate((upper, lower))
    positive = values[values > 0]
    scale = float(np.percentile(positive, 99.0)) if positive.size else 1.0
    return np.clip(upper / max(scale, 1e-9), 0.0, 1.0), np.clip(
        lower / max(scale, 1e-9), 0.0, 1.0)


def _register(
        ref_upper: np.ndarray,
        ref_lower: np.ndarray,
        got_upper: np.ndarray,
        got_lower: np.ndarray,
        reference_playhead: int,
        candidate_playhead: int,
        requested_scale: float | None,
        requested_offset: float | None,
        optimize: bool,
) -> tuple[float, float]:
    """Fit only screenshot scale and center-time offset, never amplitude."""
    xs = np.arange(ref_upper.size, dtype=np.float64)
    candidate_xs = np.arange(got_upper.size, dtype=np.float64)

    def score(scale: float, offset: float) -> float:
        source_x = (
            candidate_playhead
            + (xs - reference_playhead) * scale
            + offset
        )
        valid = (source_x >= 0) & (source_x < got_upper.size)
        if valid.mean() < 0.80:
            return float("inf")
        sampled_upper = np.interp(source_x[valid], candidate_xs, got_upper)
        sampled_lower = np.interp(source_x[valid], candidate_xs, got_lower)
        return float(np.mean(np.abs(np.concatenate((
            ref_upper[valid] - sampled_upper,
            ref_lower[valid] - sampled_lower,
        )))))

    if requested_scale is not None and requested_offset is not None:
        return requested_scale, requested_offset
    scale = requested_scale if requested_scale is not None else 1.0
    offset = requested_offset if requested_offset is not None else 0.0
    if not optimize:
        return scale, offset

    scale_radius = 0.0 if requested_scale is not None else 0.10
    offset_radius = 0.0 if requested_offset is not None else 120.0
    for scale_step, offset_step in ((0.005, 4.0), (0.001, 1.0)):
        scales = [scale] if scale_radius == 0.0 else np.arange(
            scale - scale_radius, scale + scale_radius + scale_step / 2,
            scale_step)
        offsets = [offset] if offset_radius == 0.0 else np.arange(
            offset - offset_radius, offset + offset_radius + offset_step / 2,
            offset_step)
        best = min(
            ((score(float(candidate_scale), float(candidate_offset)),
              float(candidate_scale), float(candidate_offset))
             for candidate_scale in scales
             for candidate_offset in offsets),
            key=lambda item: item[0],
        )
        _error, scale, offset = best
        scale_radius = 0.0 if requested_scale is not None else scale_step * 2
        offset_radius = (
            0.0 if requested_offset is not None else offset_step * 2)
    return scale, offset


def compare(args: argparse.Namespace) -> dict[str, float | int]:
    reference = np.asarray(Image.open(args.reference).convert("RGB"))
    candidate = np.asarray(Image.open(args.candidate).convert("RGB"))
    reference_center = args.reference_center_y or reference.shape[0] // 2
    candidate_center = args.candidate_center_y or round(204 / 310 * candidate.shape[0])
    candidate_top = args.candidate_top or round(98 / 310 * candidate.shape[0])

    ref_upper, ref_lower = _normalize(*_silhouette(
        reference, reference_center))
    got_upper, got_lower = _normalize(*_silhouette(
        candidate, candidate_center, candidate_top))

    reference_playhead = args.reference_playhead_x
    if reference_playhead is None:
        reference_playhead = _red_playhead(reference)
    candidate_playhead = args.candidate_playhead_x
    if candidate_playhead is None:
        candidate_playhead = candidate.shape[1] // 2
    horizontal_scale, center_offset = _register(
        ref_upper, ref_lower, got_upper, got_lower,
        reference_playhead, candidate_playhead,
        args.horizontal_scale, args.center_offset,
        not args.no_optimize,
    )
    xs = np.arange(reference.shape[1], dtype=np.float64)
    source_x = (
        candidate_playhead
        + (xs - reference_playhead) * horizontal_scale
        + center_offset
    )
    valid = (source_x >= 0) & (source_x < candidate.shape[1])
    candidate_xs = np.arange(candidate.shape[1], dtype=np.float64)
    got_upper = np.interp(source_x, candidate_xs, got_upper)
    got_lower = np.interp(source_x, candidate_xs, got_lower)

    ref = np.concatenate((ref_upper[valid], ref_lower[valid]))
    got = np.concatenate((got_upper[valid], got_lower[valid]))
    mae = float(np.mean(np.abs(ref - got)))
    correlation = float(np.corrcoef(ref, got)[0, 1])
    threshold = 0.08
    intersection = np.minimum(ref, got).sum()
    union = np.maximum(ref, got).sum()
    ink_iou = float(intersection / max(union, 1e-9))
    cutout_agreement = float(np.mean(
        (ref < threshold) == (got < threshold)))

    height = 300
    center = height // 2
    canvas = Image.new("RGB", (reference.shape[1], height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def points(values: np.ndarray, sign: int) -> list[tuple[int, int]]:
        return [
            (int(x), int(round(center + sign * value * (center - 5))))
            for x, value in enumerate(values)
        ]

    draw.line(points(ref_upper, -1), fill=(45, 255, 110), width=2)
    draw.line(points(ref_lower, 1), fill=(45, 255, 110), width=2)
    draw.line(points(got_upper, -1), fill=(255, 55, 190), width=1)
    draw.line(points(got_lower, 1), fill=(255, 55, 190), width=1)
    draw.line(
        [(reference_playhead, 0), (reference_playhead, height - 1)],
        fill=(0, 180, 255), width=1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.out)

    return {
        "reference_playhead_x": reference_playhead,
        "candidate_playhead_x": candidate_playhead,
        "horizontal_scale": round(horizontal_scale, 6),
        "center_offset_pixels": round(center_offset, 3),
        "silhouette_mae": round(mae, 6),
        "silhouette_correlation": round(correlation, 6),
        "ink_iou": round(ink_iou, 6),
        "cutout_agreement": round(cutout_agreement, 6),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-center-y", type=int)
    parser.add_argument("--candidate-center-y", type=int)
    parser.add_argument("--candidate-top", type=int)
    parser.add_argument("--reference-playhead-x", type=int)
    parser.add_argument("--candidate-playhead-x", type=int)
    parser.add_argument("--horizontal-scale", type=float)
    parser.add_argument("--center-offset", type=float)
    parser.add_argument(
        "--no-optimize", action="store_true",
        help="align playheads only; do not fit screenshot scale/time offset")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(compare(parse_args()), indent=2, sort_keys=True))
