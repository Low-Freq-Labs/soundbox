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
    start = shader.index('            IntervalSurface interval = resolve_interval_surface(')
    end = shader.index('        const float coverage = positioned_profile_coverage(', start)
    compositor = shader[start:end].rsplit('        }', 1)[0]
    # Execute the production final fragment branch in a compute-callable wrapper.
    # Discard is equivalent to the untouched background for this opaque surface.
    compositor = compositor.replace('discard_fragment();', 'return float4(background, 1.0);')
    overview_start = shader.index('        if (surface.valid == 0.0 || surface.coverage <= 0.0')
    overview_end = shader.index('    if (yPoints < 49.0)', overview_start)
    overview = shader[overview_start:overview_end].rsplit('    }', 1)[0]
    overview = overview.replace('discard_fragment();', 'return float4(background, 1.0);')
    wrapper = r'''
inline float4 audit_overview(float3 weighted, float weight, float coverage) {
    Uniforms u{};
    WaveSurface surface{coverage, weighted, weight, 1.0};
    float3 background = display_srgb_to_linear(float3(13.0, 14.0, 18.0) / 255.0);
    OVERVIEW
}
'''.replace('OVERVIEW', overview) + r'''
inline float4 audit_composite(thread const float *blueMinimums,
        thread const float *blueMaximums, thread const float3 *intervalEncodedColors,
        thread const float *profilePositions, uint profileCount, float signedPosition,
        float heightScale, float antialias, uint mode, float zoom,
        float3 encodedBackground) {
    Uniforms u{}; u.waveformMode = mode; u.pixelsPerSecond = zoom;
    float3 background = display_srgb_to_linear(encodedBackground);
    COMPOSITOR
}
'''.replace('COMPOSITOR', compositor)
    selection_start = shader.index('        const float detailFootprint =')
    selection_end = shader.index('        const uint detailDivisor =', selection_start)
    selection = shader[selection_start:selection_end].replace(
        'u.audioDetailMipDivisors', 'auditDivisors').replace('u.audioDetailMipCount', '12u')
    return shader + wrapper + r'''
constexpr Uniforms overview_test_uniforms(uint kind) {
    Uniforms u{};
    u.sampleCount = 4; u.waveMipCount = 1; u.waveMipCounts[0] = 4;
    u.samplesPerSecond = 150;
    u.audioDetailSampleCount = 4; u.audioDetailMipCount = 1;
    u.audioDetailMipCounts[0] = 4; u.audioDetailMipDivisors[0] = 1;
    u.audioDetailSamplesPerSecond = 150;
    if (kind == 1) u.audioDetailSampleCount = 0;
    if (kind == 2) u.threeBandActive = 1;
    return u;
}
constant Uniforms overviewTestUniforms = overview_test_uniforms(0);
constant Uniforms overviewNoDetail = overview_test_uniforms(1);
constant Uniforms overviewThreeBand = overview_test_uniforms(2);
constant uint auditDivisors[12] = {1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64};
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
        out[1] = v.premultipliedColor.r;
        out[2] = v.premultipliedColor.g;
        out[3] = v.premultipliedColor.b;
        float4 final = audit_composite(lower, upper, colors, positions,
            count, a[2], a[3], a[4], a[5] != 0 ? 1u : 2u, a[6],
            float3(a[50], a[51], a[52]));
        out[4] = final.r; out[5] = final.g; out[6] = final.b;
    } else if (op == 5) {
        Uniforms u{}; u.audioDetailSamplesPerSecond = a[1];
        u.pixelsPerSecond = a[2]; float scale = a[3];
        DETAIL_SELECTION
        out[0] = float(auditDivisors[detailLevel]);
    } else if (op == 6) {
        float3 authored = float3(a[1], a[2], a[3]);
        float3 recovered = recover_rgb_color(authored * a[4], a[4], authored);
        float3 color = mix(recovered, authored, smoothstep(92.0, 184.0, a[5]));
        out[0] = color.r; out[1] = color.g; out[2] = color.b;
    } else if (op == 4) {
        device const WaveSample *samples = (device const WaveSample *)(a + 8);
        WaveTap tap = three_band_detail_tap(samples, 0, uint(a[1]),
            uint(a[2]), a[3], 0, a[4]);
        out[0] = tap.weightedColor.r;
        out[1] = tap.weightedColor.g;
        out[2] = tap.weightedColor.b;
        out[3] = tap.height;
    } else if (op == 8) {
        device const WaveSample *samples = (device const WaveSample *)(a + 8);
        device const AudioEnvelopeSample *audio = (device const AudioEnvelopeSample *)(a + 24);
        WaveSurface v = a[4] == 1
            ? overview_wave_surface(samples, audio, overviewNoDetail, a[1], a[2], a[3], 0.5)
            : a[4] == 2
            ? overview_wave_surface(samples, audio, overviewThreeBand, a[1], a[2], a[3], 0.5)
            : overview_wave_surface(samples, audio, overviewTestUniforms, a[1], a[2], a[3], 0.5);
        float4 pixel = audit_overview(v.weightedColor, v.colorWeight, v.coverage);
        out[0] = pixel.r; out[1] = pixel.g; out[2] = pixel.b;
    } else if (op == 7) {
        float4 v = audit_overview(float3(a[1], a[2], a[3]), a[4], a[5]);
        out[0] = v.r; out[1] = v.g; out[2] = v.b;
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
'''.replace('PROFILE', block).replace('OUTER', outer).replace('EXPRESSION', expression).replace('DETAIL_SELECTION', selection)


def linear_color(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def encoded_color(linear):
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= .0031308, linear * 12.92,
                    1.055 * linear ** (1 / 2.4) - .055)


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
    colors = np.clip(row[29:29+3*count].reshape(-1, 3).astype(float), 0, 1)
    if row[5]:
        colors = linear_color(colors)
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


def rgb_faint_oracle(row):
    """Independent encoded-color quadrature, retained lift and final decoding."""
    integral = interval_oracle(row)
    coverage = integral[0]
    if coverage == 0:
        return linear_color(row[50:53])
    if row[6] != 384 and coverage < .18:
        coverage += 1.5 * coverage * (1-coverage/.18)**2
    return linear_color(row[50:53] * (1 - coverage)
                        + integral[1:] / integral[0] * coverage)


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

    def test_all_rgb_codes_keep_color_when_compact_height_is_zero_or_tiny(self):
        # Exhaust the 3-bit channels, not a hand-picked green/blue screenshot.
        # Constant authored hue must not depend on compact height or zoom.
        palette = np.array(list(np.ndindex(8, 8, 8)), dtype=float) / 7
        rows, expected = [], []
        for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500):
            for weight in (0, 1e-8, 1e-5, .2, 1):
                block = np.zeros((len(palette), 64))
                block[:, 0] = 6
                block[:, 1:4] = palette
                block[:, 4] = weight
                block[:, 5] = zoom
                rows.append(block)
                expected.append(palette)
        np.testing.assert_allclose(self.execute(np.vstack(rows))[:, :3],
                                   np.vstack(expected), atol=2e-6, rtol=0)

    def test_overview_quiet_color_and_silence_execute_production_compositor(self):
        palette = np.array([[1, 0, 0], [0, 1, 1], [.2, .7, 1]])
        rows, expected = [], []
        background = linear_color(np.array([13, 14, 18]) / 255)
        for weight in (0, 1e-8, 1e-5, .1, 1):
            for coverage in (.1, .5, 1):
                for color in palette:
                    row = np.zeros(64)
                    row[:6] = [7, *(color * weight), weight, coverage]
                    rows.append(row)
                    vivid = 1 - (1 - coverage) ** 3
                    expected.append(background if weight == 0 else
                                    background * (1-vivid) + linear_color(color) * vivid)
        actual = self.execute(rows)[:, :3]
        np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=0)
        old = self.source.replace('/ surface.colorWeight;', '/ max(surface.colorWeight, 0.0001);')
        self.assertNotEqual(old, self.source)
        self.assertGreater(np.max(np.abs(self.execute(rows, old)[:, :3] - expected)), .1)

    def test_overview_uses_signed_audio_when_compact_height_rounds_to_zero(self):
        colors = [[1, 0, 0], [0, 1, 1], [.2, .7, 1]]
        rows, expected = [], []
        background = linear_color(np.array([13, 14, 18]) / 255)
        for height in (0, 1, 100, 2000):
            for distance in (0, .2, .5, 1):
                for color in colors:
                    row = np.zeros(64, dtype=np.float32)
                    row[:4] = [8, 1.5, .8, distance]
                    row[8:24] = np.tile([0, *color], 4)
                    # Production packed envelopes: upper/lower/minimum/maximum.
                    row[24:32].view(np.uint16)[:] = np.tile([height, height, 0, 0], 4)
                    rows.append(row)
                    coverage = np.clip(height/65535*11-distance+.5, 0, 1) if height else 0
                    vivid = 1-(1-coverage)**3
                    expected.append(background*(1-vivid)+linear_color(color)*vivid)
        np.testing.assert_allclose(self.execute(rows)[:, :3], expected, atol=2e-6, rtol=0)
        for kind in (1, 2):  # No independent proof; PWV7 retains its own geometry.
            gated = np.array(rows)
            gated[:, 4] = kind
            np.testing.assert_allclose(self.execute(gated)[:, :3],
                                       np.tile(background, (len(rows), 1)), atol=2e-6, rtol=0)

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

    def test_rgb_384_uses_exact_visible_pixel_area_across_colors_and_signed_profiles(self):
        rows = self.interval_cases()
        rows[:, 6] = 384
        expected = np.array([rgb_faint_oracle(row) for row in rows])
        actual = self.execute(rows)[:, 4:7]
        np.testing.assert_allclose(actual, expected, atol=3e-5, rtol=0)
        # Accidentally enabling the faint shoulder at accepted C must fail
        # the independent exact-area oracle without changing geometry or hue.
        old = self.source.replace('&& u.pixelsPerSecond != 384.0', '')
        self.assertNotEqual(old, self.source)
        boosted = self.execute(rows, old)[:, 4:7]
        self.assertGreater(float(np.max(encoded_color(boosted) - encoded_color(actual))), .02)

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

    def test_blue_final_color_matches_independent_pixel_area_oracle(self):
        rows = self.interval_cases(); rows[:, 5] = 1
        expected = np.array([interval_oracle(row) for row in rows])
        actual = self.execute(rows)
        np.testing.assert_allclose(actual[:, :4], expected, atol=1e-4, rtol=0)
        np.testing.assert_allclose(actual[:, 4:7], expected[:, 1:], atol=1e-4, rtol=0)

    def test_blue_shading_is_independent_of_linear_profile_subdivision(self):
        rows = []
        for width in (.003, .03, .15, .4):
            for y in (-.3, 0, .2):
                for count in (2, 3, 5, 7):
                    positions = np.linspace(0, 1, count)
                    centers = -.6 + positions * 1.2
                    row = np.zeros(64, dtype=np.float32)
                    row[:6] = [2, count, y, 120, .5, 1]
                    row[8:8+count] = centers - width
                    row[15:15+count] = centers + width
                    row[22:22+count] = positions
                    row[29:29+count*3] = np.tile([.08, .12, .86], count)
                    rows.append(row)
        actual = self.execute(rows)[:, 4:7].reshape(-1, 4, 3)
        np.testing.assert_allclose(actual, np.repeat(actual[:, :1], 4, axis=1),
                                   atol=1e-4, rtol=0)

    def test_close_detail_uses_resolvable_extrema_at_each_drawable_scale(self):
        rows = []
        for scale in (1, 2):
            for zoom in (384, 576, 864, 1152, 1500):
                row = np.zeros(64); row[:4] = [5, 1000, zoom, scale]
                rows.append(row)
        actual = self.execute(rows)[:, 0]
        for row, divisor in zip(rows, actual):
            # Each retained cell must cover the physical source footprint,
            # allowing the established six-percent reconstruction tolerance.
            self.assertGreaterEqual(divisor, max(1, .94*row[1]/(row[2]*row[3])))
        np.testing.assert_array_equal(actual[5:], [2, 1, 1, 1, 1])

    def test_signed_crossing_retains_area_without_broad_rgb_brightness_boost(self):
        row = np.zeros(64, dtype=np.float32); row[:5] = [2, 2, 0, 60, .5]
        row[8:10] = [-.8, .6]; row[15:17] = [-.6, .8]
        row[22:24] = [0, 1]; row[29:35] = 1
        expected = interval_oracle(row)
        actual = self.execute([row])[0]
        np.testing.assert_allclose(actual[:4], expected, atol=3e-5, rtol=0)
        np.testing.assert_allclose(actual[4:7], rgb_faint_oracle(row), atol=3e-5, rtol=0)
        self.assertLess(float(actual[4]), .2)  # formerly forced to solid white

    def test_final_rgb_fragment_matches_independent_visible_color(self):
        rows = self.interval_cases()
        expected = np.array([rgb_faint_oracle(row) for row in rows])
        np.testing.assert_allclose(self.execute(rows)[:, 4:7], expected, atol=6e-5, rtol=0)

    def test_rgb_edges_blend_display_channels_before_final_attachment_decoding(self):
        # Exercise the actual final fragment branch with a nonblack background,
        # all zoom stops, partial/full coverage, mixed hues and the retained lift.
        background = np.array([13, 14, 18]) / 255
        colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, .5, 0),
                  (2/7, 6/7, 1), (.2, .2, .2), (1, 1, 1)]
        rows, expected = [], []
        for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500):
            for coverage in (0, .025, .06, .18, .5, 1):
                for color in colors:
                    row = np.zeros(64, dtype=np.float32)
                    row[:7] = [2, 2, .3 + (.5/60)*(1-2*coverage), 60, .5, 0, zoom]
                    row[8:10] = -.3; row[15:17] = .3
                    row[22:24] = [0, 1]; row[29:35] = [*color, *color]
                    row[50:53] = background
                    rows.append(row)
                    lifted = coverage
                    if zoom != 384 and coverage < .18:
                        lifted += 1.5 * coverage * (1-coverage/.18)**2
                    expected.append(linear_color(background*(1-lifted)
                                                 + np.array(color)*lifted))
        actual = self.execute(rows)[:, 4:7]
        np.testing.assert_allclose(actual, expected, atol=3e-6, rtol=0)
        old = self.source.replace(
            ': saturate(encodedColors[segment]);',
            ': display_srgb_to_linear(saturate(encodedColors[segment]));').replace(
            ': saturate(encodedColors[segment + 1]);',
            ': display_srgb_to_linear(saturate(encodedColors[segment + 1]));')
        self.assertNotEqual(old, self.source)
        self.assertGreater(float(np.max(np.abs(self.execute(rows, old)[:, 4:7]-expected))), .1)

    def test_restoring_separate_edge_averages_breaks_blue_final_fragment_oracle(self):
        row = np.zeros(64, dtype=np.float32); row[:6] = [2, 2, 0, 60, .5, 1]
        row[8:10] = [-.8, .6]; row[15:17] = [-.6, .8]
        row[22:24] = [0, 1]; row[29:35] = 1
        old = r'''
            float inverted[kAttributeProfileCapacity];
            for (uint i = 0; i < profileCount; ++i) inverted[i] = -blueMinimums[i];
            float oldCoverage = min(
                positioned_profile_coverage(blueMaximums, profilePositions, profileCount,
                    signedPosition * heightScale, heightScale, antialias),
                positioned_profile_coverage(inverted, profilePositions, profileCount,
                    -signedPosition * heightScale, heightScale, antialias));
            interval.premultipliedColor *= oldCoverage / max(interval.coverage, 1e-6);
            interval.coverage = oldCoverage;
'''
        anchor = '            if (interval.coverage <= 0.0) {'
        mutated = self.source.replace(anchor, old + anchor)
        self.assertNotEqual(mutated, self.source)
        expected = interval_oracle(row)[1:]
        fixed = self.execute([row])[0, 4:7]
        broken = self.execute([row], mutated)[0, 4:7]
        np.testing.assert_allclose(fixed, expected, atol=3e-5, rtol=0)
        self.assertGreater(float(np.max(np.abs(broken - expected))), .4)

    def test_rgb_columns_preserve_minimum_stroke_and_empty_pixels(self):
        rows = []
        for y in np.linspace(-.1, .15, 101):
            row = np.zeros(64, dtype=np.float32)
            row[:5] = [2, 2, y, 60, .5]
            row[8:10] = [0, .05]; row[15:17] = [0, .05]
            row[22:24] = [0, 1]; row[29:35] = 1
            rows.append(row)
        actual = self.execute(rows)
        displayed = encoded_color(actual[:, 4:7])
        self.assertTrue(np.all(displayed[:, 0] >= actual[:, 0] - 1e-6))
        np.testing.assert_array_equal(actual[actual[:, 0] == 0, 4:7], 0)
        self.assertGreater(np.count_nonzero(displayed[:, 0] > actual[:, 0] + .001), 5)
        visible = actual[:, 0] >= .18
        np.testing.assert_allclose(displayed[visible, 0], actual[visible, 0], atol=3e-5)

    def test_faint_rgb_shoulder_is_monotonic_bounded_and_hue_independent(self):
        rows = []
        for color in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, .5, 0)):
            for y in np.linspace(.28, .32, 81):
                row = np.zeros(64, dtype=np.float32)
                row[:5] = [2, 2, y, 60, .5]
                row[8:10] = -.3; row[15:17] = .3
                row[22:24] = [0, 1]; row[29:35] = [*color, *color]
                rows.append(row)
        actual = self.execute(rows)
        expected = np.array([rgb_faint_oracle(row) for row in rows])
        np.testing.assert_allclose(actual[:, 4:7], expected, atol=6e-5, rtol=0)
        # The red channel of the white-equivalent contour cannot reverse as
        # coverage increases, jump at the threshold, or brighten visible areas.
        for block in actual.reshape(4, 81, -1):
            channel = np.argmax(block[0, 4:7])
            values = encoded_color(block[:, 4+channel])
            self.assertTrue((np.diff(values) <= 2e-6).all())
            self.assertLessEqual(float(np.max(values-block[:, 0])), .041)
        mutation = self.source.replace('1.5 * fade * fade', '0.0 * fade * fade')
        self.assertNotEqual(mutation, self.source)
        self.assertGreater(float(np.max(encoded_color(actual[:, 4:7])
                                       - encoded_color(self.execute(rows, mutation)[:, 4:7]))), .02)
