"""Integrity checks for the private six-image waveform acceptance corpus.

Pixel acceptance is performed by ``scripts/render_waveform_reference.py`` with
the real native Metal deck. These tests deliberately do not replace rendering
with a correlation score; they ensure the harness stays bound to the exact two
source WAVs, imported caches, modes, positions, and untouched reference PNGs.
"""

import json
from pathlib import Path
import struct
import unittest

import numpy as np
from PIL import Image

from scripts.render_waveform_reference import (
    DEFAULT_CACHE_ROOT,
    DEFAULT_REFERENCE_ROOT,
    REFERENCE_CASES,
)
from scripts.compare_waveform_motion import compare as compare_motion
from soundbox import track_analysis


EXPECTED_SOURCES = {
    "33638604": "Bad Decisions w SUMA (FINAL FR).wav",
    "54231927": "Sonny - High & Scared (final master).wav",
}
EXPECTED_WINDOWS = {
    "33638604": (25_597, 768.0),
    "54231927": (27_000, 1536.0),
}


def _manifest(track_id: str) -> tuple[dict, Path]:
    matches = list(DEFAULT_CACHE_ROOT.glob(f"{track_id}-*.sba4"))
    if len(matches) != 1:
        raise AssertionError(
            f"expected one real cache for {track_id}, found {matches}")
    path = matches[0]
    with path.open("rb") as handle:
        magic, size = struct.unpack(
            "<4sI", handle.read(track_analysis._HEADER.size))
        if magic != b"SBA4":
            raise AssertionError(f"bad cache magic: {path}")
        return json.loads(handle.read(size)), path


@unittest.skipUnless(
    DEFAULT_REFERENCE_ROOT.is_dir() and DEFAULT_CACHE_ROOT.is_dir(),
    "private Rekordbox waveform acceptance corpus is not installed",
)
class RekordboxReferenceWaveforms(unittest.TestCase):
    def test_corpus_is_exactly_two_real_tracks_by_three_modes(self):
        self.assertEqual(len(REFERENCE_CASES), 6)
        self.assertEqual(
            {(track_id, mode) for track_id, mode, _position, _zoom, _file
             in REFERENCE_CASES},
            {(track_id, mode)
             for track_id in EXPECTED_SOURCES
             for mode in ("BLUE", "RGB", "3BAND")},
        )
        for track_id, mode, position_ms, zoom, filename in REFERENCE_CASES:
            with self.subTest(track=track_id, mode=mode):
                self.assertEqual(position_ms % 1, 0)
                self.assertGreater(position_ms, 20_000)
                self.assertEqual(
                    (position_ms, zoom), EXPECTED_WINDOWS[track_id])
                with Image.open(DEFAULT_REFERENCE_ROOT / filename) as image:
                    self.assertGreater(image.width, 2_800)
                    self.assertGreater(image.height, 275)

    def test_harness_sources_and_streams_are_the_real_imported_records(self):
        section_for_mode = {
            "BLUE": "blue_detail",
            "RGB": "rgb_detail",
            "3BAND": "three_band",
        }
        manifests = {}
        for track_id in EXPECTED_SOURCES:
            manifests[track_id], _path = _manifest(track_id)
        for track_id, mode, _position_ms, _zoom, _filename in REFERENCE_CASES:
            manifest = manifests[track_id]
            with self.subTest(track=track_id, mode=mode):
                source = Path(manifest["source"]["path"])
                self.assertTrue(source.is_file())
                self.assertEqual(source.name, EXPECTED_SOURCES[track_id])
                self.assertEqual(
                    manifest["provenance"]["analysis"],
                    "rekordbox-import+soundbox")
                self.assertGreater(
                    manifest["sections"][section_for_mode[mode]]["raw_size"],
                    20_000,
                )

    def test_reference_palette_contains_rekordbox_authored_colors(self):
        exact_required = {
            "BLUE": ((255, 255, 255),),
            "RGB": ((255, 0, 0), (0, 125, 225)),
            "3BAND": (
                (0, 85, 225),
                (180, 105, 10),
                (245, 235, 215),
                (210, 220, 250),
            ),
        }
        for _track_id, mode, _position_ms, _zoom, filename in REFERENCE_CASES:
            with Image.open(DEFAULT_REFERENCE_ROOT / filename) as image:
                colors = set(image.convert("RGB").get_flattened_data())
            with self.subTest(mode=mode, file=filename):
                for color in exact_required[mode]:
                    self.assertIn(color, colors)
                if mode == "BLUE":
                    self.assertTrue(any(
                        blue > 180 and green > 80 and red < 80
                        for red, green, blue in colors
                    ))


class WaveformMotionMetric(unittest.TestCase):
    def test_identical_registered_frames_have_zero_residual(self):
        frame = np.full((12, 20, 3), (13, 14, 18), dtype=np.float32)
        frame[3:9, 5:15] = (255, 166, 0)
        result = compare_motion(
            [frame, frame.copy(), frame.copy()],
            pixels_per_second=1536.0,
            sample_rate=44_100.0,
            frame_step=0,
            drawable_scale=2.0,
        )
        self.assertEqual(result["visible_mean_code_change"], 0.0)
        self.assertEqual(result["solid_mean_code_change"], 0.0)
        self.assertEqual(result["silhouette_xor_percent"], 0.0)
