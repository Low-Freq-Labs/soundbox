"""Exercise the actual Metal profile builder across fractional song phases.

An alternating triangular lattice has phase-invariant area above a threshold.
Skipping its corners loses peaks even though the filter footprint is unchanged.
This checks numerical sampling, not physical display acceptance.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.support.native_source import native_deck_source


def profile_errors(source: str) -> dict[tuple[int, int], tuple[float, int]]:
    compiler = shutil.which("clang++")
    if not compiler:
        raise unittest.SkipTest("Clang required")
    block = source.split("        float tilePositions[kAttributeProfileCapacity];", 1)[1]
    block = "float tilePositions[kAttributeProfileCapacity];" + block.split(
        "        float heights[kAttributeProfileCapacity];", 1)[0]
    program = r'''
#include <cmath>
#include <cstdio>
#include <algorithm>
using uint = unsigned;
using std::floor;
constexpr uint kAttributeProfileCapacity = 7;
float triangle(float x) {
    int cell = int(floor(x));
    float phase = x - floor(x);
    return cell % 2 ? 1 - phase : phase;
}
float above(float a, float b, float level) {
    if (a == b) return a >= level ? 1.f : 0.f;
    return std::clamp((std::max(a, b) - level) / std::abs(a - b), 0.f, 1.f);
}
int main() {
    for (uint mode : {1u, 2u, 3u}) {
        for (float zoom : {24.f, 32.f, 48.f, 64.f, 92.f, 128.f, 184.f,
                           256.f, 384.f, 576.f, 864.f, 1152.f, 1500.f}) {
            float maxError = 0;
            int missing = 0;
            for (int phase = 0; phase <= 100; ++phase) {
                struct { uint waveformMode; float pixelsPerSecond; } u{mode, zoom};
                float samplesPerPixel = 4;
                float leftTile = 10.f + phase / 100.f, rightTile = leftTile + 4;
                BLOCK
                if (profileCount > kAttributeProfileCapacity || profileCount < 2
                    || tilePositions[0] != leftTile
                    || tilePositions[profileCount - 1] != rightTile) return 1;
                float area = 0;
                for (uint i = 0; i + 1 < profileCount; ++i) {
                    if (tilePositions[i + 1] <= tilePositions[i]) return 2;
                    area += (tilePositions[i + 1] - tilePositions[i])
                        * above(triangle(tilePositions[i]),
                                triangle(tilePositions[i + 1]), .75f) / 4.f;
                }
                maxError = std::max(maxError, std::abs(area - .25f));
                for (float k = floor(leftTile) + 1; k < rightTile; ++k) {
                    bool present = false;
                    for (uint i = 0; i < profileCount; ++i)
                        present |= tilePositions[i] == k;
                    if (!present) ++missing;
                }
            }
            printf("%u %.0f %.9g %d\n", mode, zoom, maxError, missing);
        }
    }
}
'''.replace("BLOCK", block)
    with tempfile.TemporaryDirectory(prefix="sb-profile-math-") as temp:
        root = Path(temp)
        cpp, binary = root / "profile.cpp", root / "profile"
        cpp.write_text(program)
        subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                       check=True, capture_output=True, text=True)
        output = subprocess.check_output([str(binary)], text=True)
    return {(int(mode), int(zoom)): (float(error), int(missing))
            for mode, zoom, error, missing in (row.split() for row in output.splitlines())}


class WaveformProfileMath(unittest.TestCase):
    def test_complete_corners_preserve_peaks_through_fractional_motion(self):
        source = native_deck_source()
        for (mode, zoom), (error, missing) in profile_errors(source).items():
            with self.subTest(mode=mode, zoom=zoom):
                self.assertLess(error, 1e-6)
                self.assertEqual(missing, 0)
