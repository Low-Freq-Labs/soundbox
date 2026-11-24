"""Compile the production display-offset policy without opening a native view.

This checks the clock-to-raster boundary, not visual acceptance. Real BREATHE
Metal comparisons live with the private temporal evidence.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.support.native_source import native_deck_source

import numpy as np


ROOT = Path(__file__).parents[1]
ZOOMS = (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500)


def offsets(function):
    program = r'''
#include <cmath>
#include <cstdint>
#include <cstdio>
FUNCTION
int main() {
    for (unsigned mode : {1u, 2u, 3u})
        for (float zoom : {24.f,32.f,48.f,64.f,92.f,128.f,184.f,
                           256.f,384.f,576.f,864.f,1152.f,1500.f})
            for (float scale : {1.f,1.5f,2.f,3.f})
                for (double origin : {0.,42704.,50000.,21600000.})
                    for (int phase=0;phase<33;++phase) {
                        double exact=(origin*zoom*scale/1000.+phase/32.)*4.;
                        int64_t tile=(int64_t)std::floor(exact/128.)*128;
                        double a=waveformAttributeOffset(mode,zoom,scale,exact,tile,4);
                        double b=waveformAttributeOffset(mode,zoom,scale,exact,tile-128,4);
                        printf("%u %.0f %.1f %.17g %lld %.17g %.17g\n",
                            mode,zoom,scale,exact,(long long)tile,a,b);
                    }
}
'''.replace('FUNCTION', function).replace('#include <cstdio>',
                                       '#include <cstdio>\n#include <initializer_list>')
    with tempfile.TemporaryDirectory(prefix='sb-wave-phase-') as area:
        root = Path(area)
        source, binary = root/'phase.cpp', root/'phase'
        source.write_text(program)
        subprocess.run(['clang++', '-std=c++17', str(source), '-o', str(binary)],
                       check=True, capture_output=True, text=True)
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    return np.array([list(map(float, row.split())) for row in result.stdout.splitlines()])


class WaveformMotionPhase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = native_deck_source()
        cls.function = 'static double waveformAttributeOffset(' + source.split(
            'static double waveformAttributeOffset(', 1)[1].split('\n}', 1)[0] + '\n}'
        cls.values = offsets(cls.function)

    def test_reported_rgb_stops_hold_then_advance_one_physical_pixel(self):
        rows = self.values[(self.values[:, 0] == 2)
                           & np.isin(self.values[:, 1], (64, 128, 184))]
        expected = np.floor(rows[:, 3]/4 + .5)*4 - rows[:, 4]
        np.testing.assert_array_equal(rows[:, 5], expected)
        self.assertLessEqual(np.max(np.abs(rows[:, 5]+rows[:, 4]-rows[:, 3])), 2)
        # Both sides of every half-pixel boundary must occur, including Retina.
        self.assertTrue(np.any(np.diff(rows[:, 5]) == 4))

    def test_blue_follows_rgb_and_three_band_keeps_continuous_motion_at_every_zoom(self):
        rows = self.values
        continuous = (rows[:, 0] == 3) | ~np.isin(rows[:, 1], (64, 128, 184, 576, 864))
        quantum = 4*np.where(rows[:, 1] <= 384, 1, rows[:, 2])
        expected = np.where(continuous, rows[:, 3],
                            np.floor(rows[:, 3]/quantum+.5)*quantum)-rows[:, 4]
        np.testing.assert_allclose(rows[:, 5], expected, atol=1e-6, rtol=0)
        self.assertEqual(set(rows[:, 1]), set(ZOOMS))
        np.testing.assert_array_equal(rows[rows[:, 0] == 1, 1:],
                                      rows[rows[:, 0] == 2, 1:])
        bands = rows[rows[:, 0] == 3]
        np.testing.assert_array_equal(bands[:, 5], bands[:, 3]-bands[:, 4])

    def test_overlapping_tile_origins_preserve_the_display_coordinate(self):
        np.testing.assert_array_equal(self.values[:, 6]-128, self.values[:, 5])

    def test_original_fractional_policy_fails_the_reported_stop_contract(self):
        mutation = self.function.replace(
            '(mode == 1 || mode == 2)\n        && (zoom == 64.0f || zoom == 128.0f || zoom == 184.0f)', 'false')
        self.assertNotEqual(mutation, self.function)
        rows = offsets(mutation)
        rows = rows[(rows[:, 0] == 2) & np.isin(rows[:, 1], (64, 128, 184))]
        expected = np.floor(rows[:, 3]/4+.5)*4-rows[:, 4]
        self.assertGreater(np.count_nonzero(np.abs(rows[:, 5]-expected) > .1), 100)
