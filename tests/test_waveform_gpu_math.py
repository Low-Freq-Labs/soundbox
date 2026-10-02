"""Run production Metal helpers headlessly against independent numerical oracles.

These tests need no Cocoa session, live track, cache or removable device. On
supported macOS a compiler/GPU failure is a failure, not a silent skip. Full
native drawables still have a separate Cocoa gate.
"""
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).parents[1]


def audit_shader(source):
    shader = source.split('@R"METAL(', 1)[1].split(')METAL"', 1)[0]
    start = shader.index('        float tilePositions[kAttributeProfileCapacity];')
    block = shader[start:shader.index('        float heights[kAttributeProfileCapacity];', start)]
    expression = re.search(r'intervalEncodedColors\[i\]\s*=\s*(.*?);', shader, re.S)[1]
    outer = re.search(r'const float outerHeight =.*?;', shader, re.S)[0]
    return shader + r'''
kernel void audit_math(device const float *inputs [[buffer(0)]],
                       device float *outputs [[buffer(1)]], uint id [[thread_position_in_grid]]) {
    device const float *a = inputs + id * 64;
    device float *out = outputs + id * 16;
    for (uint i = 0; i < 16; ++i) out[i] = 0;
    uint op = uint(a[0]);
    if (op == 0) {
        CoverageMoments m = clipped_linear_coverage_moments(a[1], a[2], a[3], a[4]);
        out[0] = m.area; out[1] = m.firstMoment;
    } else if (op == 1) {
        Uniforms u{}; u.waveformMode = uint(a[1]); u.pixelsPerSecond = a[2];
        float leftTile = a[3], samplesPerPixel = a[4], rightTile = leftTile + samplesPerPixel;
        PROFILE
        out[0] = float(profileCount);
        for (uint i = 0; i < profileCount; ++i) out[i+1] = tilePositions[i];
    } else if (op == 2) {
        uint count = uint(a[1]);
        float lower[7], upper[7], positions[7]; float3 colors[7];
        for (uint i = 0; i < 7; ++i) {
            lower[i] = a[8+i]; upper[i] = a[15+i]; positions[i] = a[22+i];
            colors[i] = float3(a[29+i*3], a[30+i*3], a[31+i*3]);
        }
        IntervalSurface v = resolve_interval_surface(lower, upper, colors, positions,
            count, a[2], a[3], a[4], a[5] != 0);
        out[0] = v.coverage;
        out[1] = v.premultipliedLinearColor.r;
        out[2] = v.premultipliedLinearColor.g;
        out[3] = v.premultipliedLinearColor.b;
        out[4] = interval_profile_coverage(lower, upper, positions, count, a[2], a[3], a[4], 1.0);
    } else if (op == 4) {
        device const WaveSample *samples = (device const WaveSample *)(a + 8);
        WaveTap tap = three_band_detail_tap(samples, 0, uint(a[1]),
            uint(a[2]), a[3], 0, a[4]);
        out[0] = tap.weightedColor.r;
        out[1] = tap.weightedColor.g;
        out[2] = tap.weightedColor.b;
        out[3] = tap.height;
    } else if (op == 3) {
        WaveAttribute attribute;
        attribute.colorAndUpper = float4(a[1], a[2], a[3], a[4]);
        attribute.lowerAndColorWeight = float4(a[5], a[6], 0, 0);
        Uniforms u{}; u.waveformMode = 2;
        float upperHeight = attribute.colorAndUpper.a;
        float lowerHeight = attribute.lowerAndColorWeight.x;
        OUTER
        float3 color = EXPRESSION;
        out[0] = color.r; out[1] = color.g; out[2] = color.b;
    }
}
'''.replace('PROFILE', block).replace('OUTER', outer).replace('EXPRESSION', expression)


def linear_color(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def interval_oracle(row, samples=4096):
    """Midpoint quadrature of pointwise interval membership and visible color.

    Does not repeat the shader's crossing partitions or moment algebra.
    """
    count = int(row[1]); y, scale, aa = row[2:5]
    low = np.minimum(row[8:8+count], row[15:15+count]).astype(float)
    high = np.maximum(row[8:8+count], row[15:15+count]).astype(float)
    centers = (low + high) / 2
    widths = np.maximum((high-low)/2, aa/scale)
    positions = row[22:22+count].astype(float)
    colors = linear_color(row[29:29+3*count].reshape(-1, 3).astype(float))
    t = (np.arange(samples)+.5)/samples
    total = np.zeros(4); span_sum = 0
    for i, span in enumerate(np.diff(positions)):
        center = centers[i]*(1-t)+centers[i+1]*t
        width = widths[i]*(1-t)+widths[i+1]*t
        coverage = np.clip((width-np.abs(y-center)+aa/scale)/(2*aa/scale), 0, 1)
        color = colors[i]*(1-t[:, None])+colors[i+1]*t[:, None]
        if row[5]:
            radial = np.clip(np.abs(y-center)/width, 0, 1)
            z = radial
            blend = (z*z*(3-2*z))[:, None]
            width_fraction = np.clip(width/.28, 0, 1)
            white = (width_fraction**2*(3-2*width_fraction))[:, None]
            center = (linear_color(np.array([0, 1, 1]))*(1-white)
                      + linear_color(np.array([237/255, 1, 1]))*white)
            color = center*(1-blend)+color*blend
        total[0] += coverage.mean()*span
        total[1:] += (color*coverage[:, None]).mean(0)*span
        span_sum += span
    return total/span_sum


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'production Metal renderer requires arm64 macOS')
class WaveformGPUMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sb-metal-math-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory/'runner'
        subprocess.run(['clang++', '-std=c++17', '-fobjc-arc', '-framework', 'Foundation',
                        '-framework', 'Metal', str(ROOT/'tests/native/shader_math_runner.mm'),
                        '-o', str(cls.binary)], check=True, capture_output=True, text=True)
        cls.source = (ROOT/'native/SoundboxDeck.mm').read_text()

    def execute(self, rows, source=None):
        rows = np.asarray(rows, dtype=np.float32)
        self.assertEqual(rows.shape[1], 64)
        shader, inputs, output = (self.directory/name for name in ('audit.metal', 'in.bin', 'out.bin'))
        shader.write_text(audit_shader(self.source if source is None else source))
        inputs.write_bytes(rows.tobytes())
        result = subprocess.run([str(self.binary), str(shader), str(inputs), str(output)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = np.frombuffer(output.read_bytes(), dtype=np.float32).reshape(len(rows), 16)
        self.assertTrue(np.isfinite(values).all())
        return values

    def test_clipped_moments_match_independent_quadrature(self):
        rng = np.random.default_rng(503)
        rows = np.zeros((300, 64), dtype=np.float32)
        rows[:, 1:3] = rng.uniform(-20, 20, (300, 2))
        rows[:, 3:5] = np.sort(rng.uniform(0, 1, (300, 2)), axis=1)
        rows[:5, 1:5] = [[0, 0, 0, 1], [1, 1, 0, 1], [.3, .3, 0, 1],
                         [-2, 3, 0, 1], [3, -2, 0, 1]]
        actual = self.execute(rows)[:, :2]
        t = rows[:, 3, None] + (np.arange(32768)+.5)/32768*(rows[:, 4]-rows[:, 3])[:, None]
        values = np.clip(rows[:, 1, None]+(rows[:, 2]-rows[:, 1])[:, None]*t, 0, 1)
        expected = np.stack([values.mean(1), (values*t).mean(1)], axis=1)*(rows[:, 4]-rows[:, 3])[:, None]
        np.testing.assert_allclose(actual, expected, atol=4e-6, rtol=0)

    def test_tiny_covered_pieces_keep_centroids_inside_the_piece(self):
        rows = np.zeros((120, 64), dtype=np.float32)
        for i, row in enumerate(rows):
            span = 10 ** (-2 - (i % 30) / 10)
            begin = np.float32(.5 + .499 * (i // 30) / 3)
            end = np.float32(begin + min(span, 1-float(begin)))
            slope = 1 / (float(end)-float(begin))
            row[1:5] = [-float(begin)*slope, (1-float(begin))*slope, begin, end]
        actual = self.execute(rows)
        t = rows[:, 3, None] + (np.arange(16384)+.5)/16384*(rows[:, 4]-rows[:, 3])[:, None]
        weights = np.clip(rows[:, 1, None].astype(float)
                          + (rows[:, 2].astype(float)-rows[:, 1])[:, None]*t, 0, 1)
        expected_centroid = (weights*t).sum(1)/weights.sum(1)
        np.testing.assert_allclose(actual[:, 1]/actual[:, 0], expected_centroid, atol=2e-6, rtol=0)

    def test_complete_profiles_preserve_every_corner_and_pixel_footprint(self):
        rows = []
        for mode in (1, 2, 3):
            for zoom in (24, 32, 48, 256, 384, 576, 864, 1152, 1500):
                if mode == 3 and zoom < 1152:
                    continue  # visually retained approximation, not asserted correct
                for base in (0, 100, 16000):
                    for phase in np.linspace(0, 1, 33):
                        row = np.zeros(64); row[:5] = [1, mode, zoom, base+phase, 4]
                        rows.append(row)
        actual = self.execute(rows)
        for row, values in zip(np.float32(rows), actual):
            left, width = row[3:5]; right = left+width
            expected = [left] + list(np.arange(np.floor(left)+1, right)) + [right]
            np.testing.assert_array_equal(values[1:1+int(values[0])], expected)

    def test_original_missing_corner_mutation_is_detected_on_gpu(self):
        row = np.zeros((1, 64)); row[0, :5] = [1, 2, 256, 10.5, 4]
        old = self.source.replace('(u.pixelsPerSecond < 64.0 || u.pixelsPerSecond >= 256.0)',
                                  '(u.pixelsPerSecond < 64.0)')
        self.assertNotEqual(old, self.source)
        actual = self.execute(row, old)[0]
        self.assertNotIn(11, actual[1:1+int(actual[0])])
        fixed = self.execute(row)[0]
        self.assertIn(11, fixed[1:1+int(fixed[0])])

    def test_color_weight_recovery_survives_signed_crossings_and_silence(self):
        rng = np.random.default_rng(705)
        bounds = rng.uniform(-1, 1, (400, 2, 2)); bounds[0] = 0
        heights = np.max(np.abs(bounds), axis=2)
        colors = rng.uniform(0, 1, (400, 2, 3))
        phases = rng.uniform(0, 1, (400, 1))
        weights = heights[:, :1]*(1-phases)+heights[:, 1:] * phases
        weighted = colors[:, 0]*heights[:, :1]*(1-phases)+colors[:, 1]*heights[:, 1:]*phases
        interpolated = bounds[:, 0]*(1-phases)+bounds[:, 1]*phases
        rows = np.zeros((400, 64)); rows[:, 0] = 3
        rows[:, 1:4] = weighted; rows[:, 4:6] = interpolated; rows[:, 6:7] = weights
        expected = weighted/np.maximum(weights, .0001)
        np.testing.assert_allclose(self.execute(rows)[:, :3], expected, atol=3e-6, rtol=0)

    def test_three_band_close_tap_preserves_independent_150_hz_layer_detail(self):
        # Alternating narrow peaks in different bands expose pair pooling,
        # inter-band normalization, or substitution of the audio silhouette.
        bands = np.array([[.9, .1, .2], [.1, .8, .2], [.2, .1, .95],
                          [.8, .2, .1], [.1, .9, .3], [.2, .1, .8]])
        heights = bands.max(1)
        samples = np.column_stack([heights, bands/heights[:, None]])
        rows, expected = [], []
        for zoom in (576, 864, 1152, 1500):
            for index in range(5):
                for phase in (0, .125, .5, .875):
                    row = np.zeros(64); row[:5] = [4, 6, index, 1+phase, zoom]
                    row[8:32] = samples.ravel()
                    rows.append(row)
                    expected.append(bands[index]*(1-phase)+bands[index+1]*phase)
        np.testing.assert_allclose(self.execute(rows)[:, :3], expected, atol=2e-6, rtol=0)

    def test_original_color_normalization_mutation_is_detected_on_gpu(self):
        row = np.zeros((1, 64)); row[0, :7] = [3, .4, .2, .1, .01, -.01, .8]
        expected = np.array([.5, .25, .125])
        fixed = self.execute(row)[0, :3]
        np.testing.assert_allclose(fixed, expected, atol=1e-6)
        old = self.source.replace('max(attribute.lowerAndColorWeight.y, 0.0001)',
                                  'max(outerHeight, 0.0001)')
        self.assertNotEqual(old, self.source)
        self.assertGreater(float(np.max(np.abs(self.execute(row, old)[0, :3]-expected))), 1)

    def interval_cases(self):
        rng = np.random.default_rng(913)
        rows = np.zeros((160, 64), dtype=np.float32); rows[:, 0] = 2
        for n, row in enumerate(rows):
            count = 2+n%6; row[1:5] = [count, rng.uniform(-1, 1), 60, .5/(1+n%3)]
            row[8:8+count] = rng.uniform(-1, 1, count)
            row[15:15+count] = rng.uniform(-1, 1, count)
            row[22:22+count] = np.r_[0, np.sort(rng.uniform(.01, .99, count-2)), 1]
            row[29:29+count*3] = rng.uniform(0, 1, count*3)
        return rows

    def test_joint_rgb_coverage_and_visible_color_match_independent_oracle(self):
        rows = self.interval_cases()
        expected = np.array([interval_oracle(row) for row in rows])
        actual = self.execute(rows)
        np.testing.assert_allclose(actual[:, :4], expected, atol=3e-5, rtol=0)
        self.assertTrue((actual[:, 1:4] <= actual[:, :1]+2e-6).all())
        self.assertTrue((actual[:, :5] >= -2e-6).all())
        self.assertTrue((actual[:, :5] <= 1+2e-6).all())

    def test_blue_thin_intervals_keep_cyan_instead_of_a_fixed_white_core(self):
        rows = []
        for width in (.004, .01, .04, .15, .3, .8):
            row = np.zeros(64, dtype=np.float32)
            row[:6] = [2, 2, 0, 200, .5, 1]
            row[8:10] = -width; row[15:17] = width
            row[22:24] = [0, 1]
            row[29:35] = [.08, .85, .86] * 2
            rows.append(row)
        values = self.execute(rows)
        np.testing.assert_allclose(values[:, 0], 1, atol=1e-6)
        # The recording contains cyan centers, not just cyan AA edge pixels.
        self.assertLess(values[0, 1], float(linear_color(np.array(24/255))))
        self.assertGreater(values[0, 2], .95)
        self.assertGreater(values[0, 3], .95)
        self.assertTrue((np.diff(values[:, 1]) >= 0).all())
        self.assertGreater(values[-1, 1], .8)
        # Reintroducing the fixed-white bug must fail the same cyan criterion.
        mutated = self.source.replace(
            'smoothstep(0.0, 0.28, halfWidth)', '1.0')
        self.assertNotEqual(mutated, self.source)
        broken = self.execute(rows, mutated)
        self.assertGreater(broken[0, 1], .8)

    def test_blue_broad_intervals_do_not_have_a_white_plateau(self):
        rows = []
        for radial in (0, .3, .6, .9):
            row = np.zeros(64, dtype=np.float32)
            row[:6] = [2, 2, radial*.8, 200, .5, 1]
            row[8:10] = -.8; row[15:17] = .8
            row[22:24] = [0, 1]
            row[29:35] = [.08, .85, .86] * 2
            rows.append(row)
        values = self.execute(rows)
        np.testing.assert_allclose(values[:, 0], 1, atol=1e-6)
        self.assertGreater(values[0, 1], .8)
        self.assertLess(values[1, 1], float(linear_color(np.array(220/255))))
        self.assertTrue((np.diff(values[:, 1]) < 0).all())
        # The earlier broad-core transfer was specifically rejected as too white.
        mutated = self.source.replace('smoothstep(0.0, 1.0, radial)',
                                      'smoothstep(0.28, 1.0, radial)')
        self.assertNotEqual(mutated, self.source)
        broken = self.execute(rows, mutated)
        self.assertGreater(broken[1, 1], float(linear_color(np.array(220/255))))

    def test_blue_is_finite_bounded_and_independent_of_segment_direction(self):
        rows = self.interval_cases(); rows[:, 5] = 1
        reverse = rows.copy()
        for row, other in zip(rows, reverse):
            count = int(row[1])
            other[8:8+count] = row[8:8+count][::-1]
            other[15:15+count] = row[15:15+count][::-1]
            other[22:22+count] = 1-row[22:22+count][::-1]
            other[29:29+count*3] = row[29:29+count*3].reshape(-1, 3)[::-1].ravel()
        values = self.execute(np.r_[rows, reverse])
        np.testing.assert_allclose(values[:len(rows), :4], values[len(rows):, :4], atol=3e-5, rtol=0)
        self.assertTrue((values[:, 1:4] <= values[:, :1]+2e-6).all())
        self.assertTrue((values[:, :5] >= -2e-6).all())

    def setUp(self):
        # Execute outside expectedFailure: GPU/compiler/fixture failures must
        # remain real errors, never be swallowed as the known math limitation.
        if self._testMethodName == "test_known_signed_coverage_approximation_matches_pointwise_area":
            row = np.zeros(64, dtype=np.float32); row[:5] = [2, 2, 0, 60, .5]
            row[8:10] = [-.8, .6]; row[15:17] = [-.6, .8]
            row[22:24] = [0, 1]; row[29:35] = 1
            self.known_actual = self.execute([row])[0, 4]
            self.known_expected = interval_oracle(row)[0]

    @unittest.expectedFailure
    def test_known_signed_coverage_approximation_matches_pointwise_area(self):
        # Tracked limitation: production replaces joint coverage with min of
        # separately integrated edges. Keep the correctness assertion visible,
        # never assert that its wrong answer is the desired result.
        self.assertAlmostEqual(float(self.known_actual), float(self.known_expected), delta=3e-5)
