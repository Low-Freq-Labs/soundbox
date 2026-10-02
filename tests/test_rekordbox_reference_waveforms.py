"""Frozen supplied-reference integrity; never reads live AppData or Desktop.

This corpus supersedes the unavailable historical two-track screenshots with
Sonny's Sept 29 / Oct 1 Nuclear reference frames. Integrity and palette checks
are not exact pixel-parity acceptance; native geometry has separate oracles.
"""
import hashlib
import json
import struct
import unittest

import numpy as np
from PIL import Image

from scripts.check_fixtures import waveform_reference
from scripts.compare_waveform_motion import compare as compare_motion
from soundbox import track_analysis


class RekordboxReferenceWaveforms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.reference = waveform_reference()

    def test_corpus_contains_frozen_supplied_frames_in_all_three_modes(self):
        self.assertEqual(len(self.reference['cases']), 3)
        self.assertEqual({row['mode'] for row in self.reference['cases']},
                         {'BLUE', 'RGB', '3BAND'})
        for row in self.reference['cases']:
            with Image.open(self.root / row['image']) as image:
                self.assertEqual(image.size, (3006, 444))
                self.assertGreater(row['video_second'], 0)
                self.assertIn(row['transfer'], ('sRGB', 'BT.709 display codes'))
        self.assertEqual(self.reference['track_id'], '265095720')

    def test_frozen_source_and_cache_preserve_full_rate_imported_streams(self):
        source = self.root / self.reference['source_file']
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
            self.reference['files'][source.name]['sha256'])
        raw = (self.root / self.reference['cache_file']).read_bytes()
        magic, size = struct.unpack('<4sI', raw[:8])
        self.assertEqual(magic, b'SBA5')
        manifest = json.loads(raw[8:8+size])
        self.assertEqual(manifest['source']['size'], source.stat().st_size)
        self.assertEqual(manifest['provenance']['analysis'], 'rekordbox-import+soundbox')
        sections = track_analysis._decode_sections(raw[8+size:], manifest)
        self.assertIsNotNone(sections)
        for key, mode, width, tag in [('blue_detail', 'blue', 1, 'pwv3'),
                                      ('rgb_detail', 'rgb', 2, 'pwv5'),
                                      ('three_band', 'three_band', 3, 'pwv7')]:
            self.assertEqual(manifest['stream_provenance'][mode], 'rekordbox-anlz-'+tag)
            self.assertEqual(len(sections[key]), 29290*width)
        self.assertEqual(float(manifest['color_rate']), 150.0)

    def test_reference_palette_retains_recorded_cyan_rgb_and_band_colors(self):
        for row in self.reference['cases']:
            with Image.open(self.root / row['image']) as image:
                a = np.asarray(image.convert('RGB').crop(tuple(row['waveform_region'])))
            r, g, b = a.transpose(2, 0, 1).astype(int)
            if row['mode'] == 'BLUE':
                masks = [(r <= 24) & (g >= 235) & (b >= 235)]
            elif row['mode'] == 'RGB':
                masks = [(r > 200) & (g < 90) & (b < 90),
                         (r < 90) & (g > 100) & (b > 180)]
            else:
                masks = [(r < 90) & (g < 170) & (b > 170),
                         (r > 180) & (g > 100) & (g < 220) & (b < 90),
                         (r > 220) & (g > 220) & (b > 200)]
            with self.subTest(mode=row['mode']):
                for mask in masks:
                    self.assertGreater(int(mask.sum()), 500)


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
