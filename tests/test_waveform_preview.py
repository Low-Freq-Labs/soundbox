"""Hermetic packed-data preview checks; no live analysis/audio discovery."""
import unittest

import numpy as np

from scripts.preview_rekordbox_waveforms import decode, current_height_curve


class WaveformPreview(unittest.TestCase):
    def test_packed_height_and_color_fields_remain_independent(self):
        data = decode(bytes([0, 255]), bytes.fromhex("e000 03fc"), bytes([0, 64, 127, 255, 128, 1]))
        self.assertEqual(data["blue"], [0.0, 1.0])
        self.assertEqual(data["rgb"], [0.0, 1.0])
        self.assertEqual(data["colors"][0], [1.0, 0.0, 0.0])
        self.assertEqual(data["colors"][1], [0.0, 0.0, 7 / 8.75])
        np.testing.assert_allclose(data["bands"], [[0, 64 / 127, 1], [1, 0, 1 / 127]])

    def test_incomplete_rows_fail_instead_of_showing_a_partial_waveform(self):
        for values in ((b"", b"00", b"000"), (b"0", b"0", b"000"),
                       (b"0", b"00", b"00")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                decode(*values)

    def test_height_curve_preserves_silence_but_changes_authored_midlevel(self):
        result = current_height_curve([0, 0.5, 1])
        self.assertEqual(result[0], 0)
        self.assertAlmostEqual(result[2], 0.84)
        self.assertLess(result[1], 0.5)
