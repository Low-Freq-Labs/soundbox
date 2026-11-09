"""Hermetic packed-data preview checks; no live analysis/audio discovery."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import numpy as np

from scripts.preview_rekordbox_waveforms import PAGE, decode, current_height_curve
from tests.support.native_source import native_deck_source


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

    def test_current_diagnostic_curve_matches_native_across_all_encoded_heights(self):
        source = native_deck_source()
        constants = '\n'.join(re.findall(
            r'constexpr float kWaveform(?:Display|Visibility)\w+ = [^;]+;', source))
        helper = 'static float displayWaveformHeight(uint16_t' + source.split(
            'static float displayWaveformHeight(uint16_t', 1)[1].split(
            'static float displayWaveformHeight(float', 1)[0]
        program = '\n'.join((
            '#include <algorithm>', '#include <cmath>', '#include <cstdint>',
            '#include <cstdio>', constants, helper,
            'int main() { for (uint32_t n = 0; n < 65536; ++n)',
            'std::printf("%a\\n", double(displayWaveformHeight(uint16_t(n)))); }',
        ))
        with tempfile.TemporaryDirectory(prefix='soundbox-preview-curve-') as area:
            root = Path(area)
            cpp, binary = root / 'curve.cpp', root / 'curve'
            cpp.write_text(program)
            subprocess.run(['clang++', '-std=c++17', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            output = subprocess.check_output([str(binary)], text=True)
        native = np.array([float.fromhex(row) for row in output.splitlines()])
        # The diagnostic uses double precision; the renderer uses float32.
        # Allow one float32 epsilon across the normalized [0, 1] domain.
        np.testing.assert_allclose(current_height_curve(np.arange(65536) / 65535),
                                   native, rtol=0, atol=np.finfo(np.float32).eps)
        exponent = re.search(r'kWaveformDisplayExponent = ([\d.]+)f;', constants)[1]
        script_exponents = re.findall(r'v\*\*([\d.]+)', PAGE)
        self.assertEqual(script_exponents, [exponent, exponent])
