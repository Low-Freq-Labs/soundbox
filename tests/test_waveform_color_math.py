"""Execute the shader's color recovery expression without AppKit or a GPU.

Clang vector arithmetic runs the actual extracted Metal expression. This is
an arithmetic regression, not a substitute for native drawable acceptance.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


def color_recovery_errors(source: str) -> list[float]:
    compiler = shutil.which("clang++")
    if not compiler:
        raise unittest.SkipTest("Clang vector extensions required")
    shader = source.split('@R"METAL(', 1)[1].split(')METAL"', 1)[0]
    fields = re.search(r"struct WaveAttribute \{.*?\};", shader, re.S)[0]
    expression = re.search(
        r"intervalEncodedColors\[i\]\s*=\s*(.*?);", shader, re.S)[1]
    outer = re.search(
        r"const float outerHeight =.*?;", shader, re.S)[0]
    lower_field = re.search(r"float4 (lower\w+);", fields)[1]
    # The fixtures are constant or varying authored colors on signed intervals
    # that cross zero, change their dominant boundary, or retain one polarity.
    # Endpoints carry the original amplitude used to premultiply each color.
    program = r'''
#include <algorithm>
#include <cmath>
#include <cstdio>
using float3 = float __attribute__((ext_vector_type(3)));
using float4 = float __attribute__((ext_vector_type(4)));
using std::abs;
inline float max(float a, float b) { return std::max(a, b); }
FIELDS
float3 recover(WaveAttribute attribute) {
    struct { int waveformMode = 2; } u;
    const float upperHeight = attribute.colorAndUpper.a;
    const float lowerHeight = attribute.LOWER.x;
    OUTER
    return EXPRESSION;
}
int main() {
    float errors[3] = {};
    const float bounds[][4] = {
        {-.81f, -.79f, .79f, .81f},
        {-.8f, .2f, -.2f, .8f},
        {.1f, .3f, .4f, .9f},
        {0.f, 0.f, .4f, .9f},
    };
    const float3 colors[] = {
        {1.f, 2.f/7.f, 3.f/7.f}, {.2f, .5f, 1.f}, {1.f, 1.f, 1.f}
    };
    for (auto b : bounds) for (auto color : colors) {
        float h0 = max(abs(b[0]), abs(b[1]));
        float h1 = max(abs(b[2]), abs(b[3]));
        for (int varying = 0; varying < 2; ++varying) {
            float3 other = varying ? float3{.1f, .8f, .4f} : color;
            WaveAttribute left = {
                {color.x*h0, color.y*h0, color.z*h0, b[1]},
                {b[0], h0, 0.f, 0.f}};
            WaveAttribute right = {
                {other.x*h1, other.y*h1, other.z*h1, b[3]},
                {b[2], h1, 0.f, 0.f}};
            for (int step = 0; step <= 1000; ++step) {
                float t = float(step) / 1000.f;
                WaveAttribute a = {
                    left.colorAndUpper*(1.f-t) + right.colorAndUpper*t,
                    left.LOWER*(1.f-t) + right.LOWER*t};
                float3 actual = recover(a);
                float weight = h0*(1.f-t) + h1*t;
                float3 expected =
                    (color*h0*(1.f-t) + other*h1*t) / max(weight, .0001f);
                for (int c = 0; c < 3; ++c) {
                    errors[varying] = max(errors[varying], abs(actual[c]-expected[c]));
                    errors[2] = max(errors[2], max(-actual[c], actual[c]-1.f));
                }
            }
        }
    }
    printf("%.9g %.9g %.9g\n", errors[0], errors[1], errors[2]);
}
'''
    program = (program.replace("FIELDS", fields)
               .replace("OUTER", outer).replace("EXPRESSION", expression)
               .replace("LOWER", lower_field))
    with tempfile.TemporaryDirectory(prefix="sb-color-math-") as temp:
        root = Path(temp)
        cpp = root / "recovery.cpp"
        binary = root / "recovery"
        cpp.write_text(program)
        subprocess.run(
            [compiler, "-std=c++17", "-O2", str(cpp), "-o", str(binary)],
            check=True, capture_output=True, text=True)
        result = subprocess.run(
            [str(binary)], check=True, capture_output=True, text=True)
    return [float(value) for value in result.stdout.split()]


class WaveformColorRecovery(unittest.TestCase):
    def test_authored_colors_survive_signed_boundary_interpolation(self):
        source = (Path(__file__).parents[1] / "native/SoundboxDeck.mm").read_text()
        constant, varying, overshoot = color_recovery_errors(source)
        self.assertLess(constant, 2e-6, "constant hue brightens at signed crossings")
        self.assertLess(varying, 2e-6, "color does not retain its interpolation weight")
        self.assertLess(overshoot, 2e-6, "color clips toward white before coverage")
