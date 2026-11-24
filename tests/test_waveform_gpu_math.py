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

from tests.support.native_source import native_deck_source, waveform_shader

import numpy as np

ROOT = Path(__file__).parents[1]


def audit_shader(source):
    shader = waveform_shader(source)
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
    layers_start=shader.index('        if (u.threeBandActive != 0) {',end)
    layers=shader[layers_start:shader.index('        const float3 encodedColor =',layers_start)]
    layers=layers.split('        if (u.threeBandActive != 0) {',1)[1].rsplit('        }',1)[0]
    layers=layers.replace('discard_fragment();','return float4(background,1.0);')
    overview_start = shader.index('        if (surface.valid == 0.0 || surface.coverage <= 0.0')
    overview_end = shader.index('    if (yPoints < 49.0)', overview_start)
    overview = shader[overview_start:overview_end].rsplit('    }', 1)[0]
    overview = overview.replace('discard_fragment();', 'return float4(background, 1.0);')
    wrapper = r'''
inline float4 audit_overview(float3 weighted, float weight, float coverage, uint mode = 0u,
        float distance = 0.0, float antialias = 0.5,
        const device BlueOverviewSample *blueOverview = nullptr, uint count = 0u) {
    Uniforms u{}; u.waveformMode = mode; u.threeBandActive = mode == 3u;
    u.blueOverviewCount = count; u.widthPoints = 1.0;
    const float xPoints = 0.5;
    WaveSurface surface{coverage, weighted, weight, 1.0};
    float3 encodedBackground = float3(13.0, 14.0, 18.0) / 255.0;
    float3 background = display_srgb_to_linear(encodedBackground);
    OVERVIEW
}
'''.replace('OVERVIEW', overview) + r'''
inline float4 audit_composite(thread const float *blueMinimums,
        thread const float *blueMaximums, thread const float3 *intervalEncodedColors,
        thread const float *profilePositions, uint profileCount, float signedPosition,
        float heightScale, float antialias, uint mode, float zoom,
        float3 encodedBackground, thread const float3 *blueInnerColors,
        thread const float2 *blueRadii) {
    Uniforms u{}; u.waveformMode = mode; u.pixelsPerSecond = zoom;
    float3 background = display_srgb_to_linear(encodedBackground);
    COMPOSITOR
}
'''.replace('COMPOSITOR', compositor) + r'''
inline float4 audit_three_band_layers(thread const float *lowHeights,
        thread const float *midHeights, thread const float *highHeights,
        thread const float *profilePositions, uint profileCount,
        float distance, float heightScale, float antialias, float3 background) {
    const float3 encodedBackground=linear_to_display_srgb(background);
    LAYERS
}
'''.replace('LAYERS',layers)
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
        float lower[7], upper[7], positions[7]; float3 colors[7], inner[7]; float2 radii[7];
        for (uint i = 0; i < 7; ++i) {
            lower[i] = a[8+i]; upper[i] = a[15+i]; positions[i] = a[22+i];
            colors[i] = float3(a[29+i*3], a[30+i*3], a[31+i*3]);
            inner[i] = float3(a[53],a[54],a[55]); radii[i] = float2(a[56],a[57]);
        }
        IntervalSurface v = resolve_interval_surface(lower, upper, colors, positions,
            count, a[2], a[3], a[4], a[5] != 0, inner, radii);
        out[0] = v.coverage;
        out[1] = v.premultipliedColor.r;
        out[2] = v.premultipliedColor.g;
        out[3] = v.premultipliedColor.b;
        float4 final = audit_composite(lower, upper, colors, positions,
            count, a[2], a[3], a[4], a[5] != 0 ? 1u : 2u, a[6],
            float3(a[50], a[51], a[52]), inner, radii);
        out[4] = final.r; out[5] = final.g; out[6] = final.b;
    } else if (op == 11) {
        uint count=uint(a[1]);
        float low[7],mid[7],high[7],positions[7];
        for(uint i=0;i<7;++i){low[i]=a[8+i];mid[i]=a[15+i];high[i]=a[22+i];positions[i]=a[29+i];}
        float4 v=audit_three_band_layers(low,mid,high,positions,count,a[2],a[3],a[4],
            display_srgb_to_linear(float3(a[50],a[51],a[52])));
        out[0]=v.r;out[1]=v.g;out[2]=v.b;
        IntervalSurface area=resolve_three_band_surface(low,mid,high,positions,count,a[2],a[3],a[4]);
        out[3]=area.coverage;
        out[4]=area.premultipliedColor.r;out[5]=area.premultipliedColor.g;out[6]=area.premultipliedColor.b;
    } else if (op == 13) {
        BluePalette v = native_blue_palette(float3(a[1],a[2],a[3]));
        out[0]=v.outer.r;out[1]=v.outer.g;out[2]=v.outer.b;
        out[3]=v.inner.r;out[4]=v.inner.g;out[5]=v.inner.b;
        out[6]=v.radii.x;out[7]=v.radii.y;
    } else if (op == 14) {
        float3 v=blue_interval_color(float3(a[1],a[2],a[3]),float3(a[4],a[5],a[6]),
            float2(a[7],a[8]),a[9],a[10]);
        out[0]=v.r;out[1]=v.g;out[2]=v.b;
    } else if (op == 15) {
        BlueOverviewSample sample{a[1],a[2],a[3],a[4],a[5]};
        float3 v=native_blue_overview_color(sample,a[6],a[7],float3(a[8],a[9],a[10]));
        out[0]=v.r;out[1]=v.g;out[2]=v.b;
    } else if (op == 16) {
        float3 v;
        if (a[1]==0) v=blue_lut(kBlueOuterPurple,uint(a[2]));
        else if (a[1]==1) v=blue_lut(kBlueOuterCyan,uint(a[2]));
        else if (a[1]==2) v=blue_lut(kBlueInnerPurple,uint(a[2]));
        else v=blue_lut(kBlueInnerCyan,uint(a[2]));
        out[0]=v.r;out[1]=v.g;out[2]=v.b;
    } else if (op == 17) {
        const device BlueOverviewSample *sample=(const device BlueOverviewSample *)(a+16);
        float4 v=audit_overview(float3(.2,.4,.8)*a[1],a[1],a[2],1u,
            a[3],a[4],sample,uint(a[5]));
        out[0]=v.r;out[1]=v.g;out[2]=v.b;
    } else if (op == 18) {
        out[0]=three_band_layer_area(a[1],a[2],a[3],a[4],a[5]);
        out[1]=mean_clamped_linear(a[6],a[7]);
    } else if (op == 19) {
        const device uchar4 *pixels=(const device uchar4 *)(a+8);
        float4 v=three_band_preview_image(pixels,uint(a[1]),a[2],a[3]);
        out[0]=v.r;out[1]=v.g;out[2]=v.b;out[3]=v.a;
    } else if (op == 5) {
        Uniforms u{}; u.audioDetailSamplesPerSecond = a[1];
        u.pixelsPerSecond = a[2]; float scale = a[3]; u.waveformMode = uint(a[4]);
        DETAIL_SELECTION
        out[0] = float(auditDivisors[detailLevel]);
        out[1] = float(auditDivisors[physicalDetailLevel]);
    } else if (op == 9) {
        device const AudioEnvelopeSample *fine = (device const AudioEnvelopeSample *)(a + 16);
        float occupancy = signed_interval_occupancy(fine, 0, uint(a[1]), uint(a[2]),
            uint(a[3]), uint(a[4]), a[5], a[6], a[7]);
        float3 color = interval_occupancy_color(float3(a[8], a[9], a[10]), occupancy);
        out[0] = occupancy; out[1] = color.r; out[2] = color.g; out[3] = color.b;
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
        float4 v = audit_overview(float3(a[1], a[2], a[3]), a[4], a[5], uint(a[6]));
        out[0] = v.r; out[1] = v.g; out[2] = v.b;
    } else if (op == 10) {
        device const WaveSample *samples = (device const WaveSample *)(a + 8);
        float4 pixel = three_band_overview_fragment(samples, overviewThreeBand,
            1.5, 0.8, a[1], a[2], float3(13.0,14.0,18.0)/255.0);
        out[0]=pixel.r; out[1]=pixel.g; out[2]=pixel.b;
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
    t = (np.arange(samples)+.5)/samples
    total = np.zeros(4); span_sum = 0
    for i, span in enumerate(np.diff(positions)):
        center = centers[i]*(1-t)+centers[i+1]*t
        width = widths[i]*(1-t)+widths[i+1]*t
        coverage = np.clip((width-np.abs(y-center)+aa/scale)/(2*aa/scale), 0, 1)
        color = colors[i]*(1-t[:, None])+colors[i+1]*t[:, None]
        if row[5]:
            # Independent dense pointwise construction of the native three
            # encoded fills. No production crossing partitions/Gauss taps.
            radial = np.clip(np.abs(y-center)/np.maximum(width,1e-5), 0, 1)
            aa_rad = np.maximum(aa/scale,1e-6)/np.maximum(width,1e-5)
            inner = np.asarray(row[53:56],dtype=float)
            middle = (color+inner)/2
            color = middle*(1-radial[:,None])+color*radial[:,None]
            for radius, is_core in ((row[56],False),(row[57],True)):
                if radius <= 0:
                    continue
                alpha = np.clip((radius+np.maximum(aa_rad,1e-6)-radial)
                                /(2*np.maximum(aa_rad,1e-6)),0,1)[:,None]
                fill = inner if is_core else (inner*(1-np.clip(radial/radius,0,1)[:,None])
                                              + middle*np.clip(radial/radius,0,1)[:,None])
                color = color*(1-alpha)+fill*alpha
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
    if coverage < .18:
        coverage += 1.5 * coverage * (1-coverage/.18)**2
    return linear_color(row[50:53] * (1 - coverage)
                        + integral[1:] / integral[0] * coverage)


def three_band_joint_oracle(row, samples=16384):
    """Dense source-position samples with independent nested vertical stripes.

    Sorting the actual three heights at each point yields three disjoint
    membership stripes. Their finite pixel intersections require neither
    production inclusion/exclusion nor its analytical crossing partitions.
    """
    count=int(row[1]);distance=abs(float(row[2]));scale=float(row[3]);aa=float(row[4])
    positions=np.asarray(row[29:29+count],dtype=float)
    bands=np.array([row[offset:offset+count] for offset in (8,15,22)],dtype=float)
    palette=np.array([[0,0,0],[0,85,225],[255,166,0],[180,105,10],
                      [255,255,255],[210,220,250],[255,240,215],[245,235,215]],dtype=float)/255
    coordinate=(np.arange(samples)+.5)/samples
    half_pixel=max(aa/max(scale,1e-5),1e-6);threshold=distance/max(scale,1e-5)
    total=np.zeros(4);span_sum=0
    for index,span in enumerate(np.diff(positions)):
        if span<=0:continue
        heights=np.maximum(0,bands[:,index,None]*(1-coordinate)+bands[:,index+1,None]*coordinate).T
        order=np.argsort(heights,axis=1)
        sorted_heights=np.take_along_axis(heights,order,axis=1)
        intersections=(np.clip((sorted_heights+half_pixel-threshold)/(2*half_pixel),0,1)
                       -np.clip((-sorted_heights+half_pixel-threshold)/(2*half_pixel),0,1))
        middle_mask=7^(1<<order[:,0]);outer_mask=1<<order[:,2]
        premult=(intersections[:,:1]*palette[7]
                 +(intersections[:,1:2]-intersections[:,:1])*palette[middle_mask]
                 +(intersections[:,2:3]-intersections[:,1:2])*palette[outer_mask])
        total[0]+=intersections[:,2].mean()*span
        total[1:]+=premult.mean(0)*span;span_sum+=span
    total/=max(span_sum,1e-6)
    return total


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
        cls.source = native_deck_source()

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
            for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500):
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
        old = self.source.replace('const float firstKnot = floor(leftTile) + 1.0;',
                                  'const float firstKnot = floor(leftTile) + 2.0;')
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

    def test_rgb_overview_matches_main_faint_only_display_space_compositing(self):
        background = np.array([13, 14, 18]) / 255
        rows, expected, contrast_coverages = [], [], []
        coverages = np.unique(np.r_[np.linspace(0, 1, 401), .17999, .18, .18001])
        for weight in (0, 1e-8, .1, 1):
            for color in np.array([[1, 0, 0], [0, 1, 1], [.2, .7, 1], [.8, .1, .5]]):
                for coverage in coverages:
                    row = np.zeros(64)
                    row[:7] = [7, *(color * weight), weight, coverage, 2]
                    rows.append(row)
                    lifted = coverage * (1 + 1.5 * (1-coverage/.18)**2) if coverage < .18 else coverage
                    contrast_coverages.append(lifted)
                    expected.append(linear_color(background if weight == 0 else
                        background + (color-background)*lifted))
        actual = self.execute(rows)[:, :3]
        np.testing.assert_allclose(actual, expected, atol=3e-6, rtol=0)
        # Visible coverage is not lifted; weak edges get a bounded, monotonic
        # shoulder and true silence keeps exactly the existing background.
        lifted = np.array(contrast_coverages[:len(coverages)])
        np.testing.assert_array_equal(lifted[coverages >= .18], coverages[coverages >= .18])
        self.assertTrue((np.diff(lifted) >= 0).all())
        self.assertLess(float(np.max(lifted-coverages)), .041)
        old = self.source.replace(
            'return float4(display_srgb_to_linear(\n'
            '                mix(encodedBackground, encodedColor, coverage)), 1.0);',
            'return float4(mix(background, display_srgb_to_linear(encodedColor), '
            'vivid_wave_coverage(surface.coverage)), 1.0);')
        self.assertNotEqual(old, self.source)
        self.assertGreater(float(np.max(np.abs(self.execute(rows, old)[:, :3]-actual))), .2)

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

    def test_three_band_missing_preview_uses_stacked_resident_fallback(self):
        rows=[]
        for y in (1.25,3,8,26):
            row=np.zeros(64,dtype=np.float32);row[:3]=[10,y,.5]
            row[8:24]=np.tile([.5,1,.5,.2],4)
            rows.append(row)
        expected=np.array([[255,255,255],[255,166,0],[0,85,225],[13,14,18]])/255
        np.testing.assert_allclose(self.execute(rows)[:,:3],linear_color(expected),atol=3e-6,rtol=0)

    def test_native_three_band_preview_bitmap_sampling_is_centered_and_premultiplied(self):
        pixels=np.zeros((25,2,4),dtype=np.uint8)
        for y in range(24):
            for x in range(2):
                alpha=80+((y*11+x*53)%176)
                pixels[y,x]=[alpha//4,alpha//2,alpha*3//4,alpha]
        rows=[];expected=[]
        for x in (-.2,0,.25,.5,.75,1,1.2):
            for y in (-1,0,.25,.5,1,3.1,22.5,23.5,24,24.5,24.99,25,26):
                row=np.zeros(64,dtype=np.float32);row[:4]=[19,2,x,y]
                row[8:].view(np.uint8)[:pixels.size]=pixels.ravel();rows.append(row)
                if y<0 or y>=25:expected.append(np.zeros(4));continue
                column=np.clip(x*2-.5,0,1);source_row=np.clip(y-.5,0,24)
                xx=int(np.floor(column));yy=int(np.floor(source_row))
                dx=column-xx;dy=source_row-yy
                top=pixels[yy,xx]*(1-dx)+pixels[yy,min(xx+1,1)]*dx
                bottom=pixels[min(yy+1,24),xx]*(1-dx)+pixels[min(yy+1,24),min(xx+1,1)]*dx
                expected.append((top*(1-dy)+bottom*dy)/255)
        np.testing.assert_allclose(self.execute(rows)[:,:4],expected,atol=3e-6,rtol=0)

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
        old = self.source.replace('intervalEncodedColors[i] =\n                attribute.colorAndUpper.rgb\n                    / max(attribute.lowerAndColorWeight.y, 0.0001)',
                                  'intervalEncodedColors[i] =\n                attribute.colorAndUpper.rgb\n                    / max(outerHeight, 0.0001)')
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

    def test_rgb_384_uses_the_same_reference_edge_visibility_as_other_zooms(self):
        rows = self.interval_cases()
        rows[:, 6] = 384
        expected = np.array([rgb_faint_oracle(row) for row in rows])
        actual = self.execute(rows)[:, 4:7]
        np.testing.assert_allclose(actual, expected, atol=3e-5, rtol=0)
        # The November2 fixed reference supersedes the former 384 exception.
        # Reintroducing that exception must fail without changing geometry/hue.
        old = self.source.replace('&& interval.coverage < 0.18)',
            '&& u.pixelsPerSecond != 384.0 && interval.coverage < 0.18)')
        self.assertNotEqual(old, self.source)
        dimmed = self.execute(rows, old)[:, 4:7]
        self.assertGreater(float(np.max(encoded_color(actual) - encoded_color(dimmed))), .02)

    def test_blue_native_layers_match_encoded_pointwise_oracle_for_thin_and_wide_carriers(self):
        rows=[]
        for width in (.004,.01,.04,.15,.3,.8):
            row=np.zeros(64,dtype=np.float32);row[:6]=[2,2,0,200,.5,1]
            row[8:10]=-width;row[15:17]=width;row[22:24]=[0,1]
            row[29:35]=[51/255,134/255,206/255]*2
            row[53:58]=[220/255,1,1,.6,.25]
            rows.append(row)
        expected=np.array([interval_oracle(row) for row in rows])
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,:4],expected,atol=3e-6,rtol=0)
        np.testing.assert_allclose(actual[:,4:7],linear_color(expected[:,1:]),atol=3e-6,rtol=0)
        # Palette is spectral; increasing carrier width cannot fabricate a
        # white center solely from an unrelated amplitude knee.
        np.testing.assert_allclose(actual[2:,1:4],np.tile([220/255,1,1],(4,1)),atol=3e-6,rtol=0)
        mutated=self.source.replace('const float3 middle = (outer + inner) * 0.5;',
                                    'const float3 middle = float3(1.0);')
        self.assertNotEqual(mutated,self.source)
        probe=rows[-1].copy();probe[2]=.8*.8
        probe_expected=interval_oracle(probe)
        self.assertGreater(float(np.max(np.abs(self.execute([probe],mutated)[0,:4]-probe_expected))),.01)

    def test_blue_native_gradient_keeps_three_overwritten_encoded_fields(self):
        rows=[]
        outer=np.array([51,134,206])/255;inner=np.array([220,255,255])/255
        expected=[]
        for radial in (0,.15,.3,.55,.8,.95):
            row=np.zeros(64,dtype=np.float32);row[:6]=[2,2,radial*.8,200,.5,1]
            row[8:10]=-.8;row[15:17]=.8;row[22:24]=[0,1]
            row[29:35]=np.tile(outer,2);row[53:58]=[*inner,.6,.25]
            rows.append(row)
            middle=(outer+inner)/2
            expected.append(inner if radial<.25 else
                            inner+(middle-inner)*(radial/.6) if radial<.6 else
                            middle+(outer-middle)*radial)
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,0],1,atol=1e-6,rtol=0)
        np.testing.assert_allclose(actual[:,1:4],expected,atol=3e-6,rtol=0)
        np.testing.assert_allclose(actual[:,4:7],linear_color(expected),atol=3e-6,rtol=0)

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
        rows=self.interval_cases();rows[:,5]=1
        randomizer=np.random.default_rng(20261121)
        rows[:,53:56]=randomizer.uniform(.05,1,(len(rows),3))
        radii=np.sort(randomizer.uniform(0,1,(len(rows),2)),axis=1)
        rows[:,56:58]=radii[:,::-1]
        expected=np.array([interval_oracle(row,samples=16384) for row in rows])
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,0],expected[:,0],atol=3e-5,rtol=0)
        # The new three-overlay field uses bounded four-point quadrature.
        # Its numerical color budget is half one 8-bit display step, below
        # attachment quantization; exact geometry and analytic controls retain
        # their tighter independent bounds. This is not exact native parity.
        np.testing.assert_allclose(actual[:,1:4],expected[:,1:4],atol=.5/255,rtol=0)
        final=np.array([rgb_faint_oracle(row) for row in rows])
        np.testing.assert_allclose(encoded_color(actual[:,4:7]),encoded_color(final),
                                   atol=.5/255,rtol=0)

    def test_three_band_own_layers_keep_all_seven_native_memberships(self):
        masks=((1,0,0),(0,1,0),(1,1,0),(0,0,1),(1,0,1),(0,1,1),(1,1,1))
        palette=np.array(((0,85,225),(255,166,0),(180,105,10),(255,255,255),
                          (210,220,250),(255,240,215),(245,235,215)))/255
        rows=np.zeros((7,64),dtype=np.float32);rows[:,:5]=[11,2,10,100,.5]
        rows[:,29:31]=[0,1]
        for row,mask in zip(rows,masks,strict=True):
            for offset,present in zip((8,15,22),mask,strict=True):row[offset:offset+2]=.5*present
        np.testing.assert_allclose(self.execute(rows)[:,:3],linear_color(palette),atol=3e-6,rtol=0)

    def test_three_band_own_nested_layers_retain_low_mid_high_extents(self):
        rows=np.zeros((4,64),dtype=np.float32);rows[:,:5]=[11,2,10,100,.5]
        rows[:,8:10]=.2;rows[:,15:17]=.5;rows[:,22:24]=1;rows[:,29:31]=[0,1]
        rows[:,2]=[10,30,75,110]
        expected=np.array([[245,235,215],[255,240,215],[255,255,255],[0,0,0]])/255
        np.testing.assert_allclose(self.execute(rows)[:,:3],linear_color(expected),atol=3e-6,rtol=0)

    def test_blue_keeps_rgb_interval_coverage_including_quiet_carriers(self):
        rows=self.interval_cases();quiet=rows[:8].copy();quiet[:,8:22]*=.001
        rows=np.r_[rows,quiet];rgb=self.execute(rows)[:,0]
        blue=rows.copy();blue[:,5]=1
        np.testing.assert_allclose(self.execute(blue)[:,0],rgb,atol=3e-6,rtol=0)

    def test_three_band_joint_crossing_colors_match_independent_dense_stripes(self):
        randomizer=np.random.default_rng(20261121)
        rows=[]
        for index in range(96):
            count=2+index%6
            row=np.zeros(64,dtype=np.float32);row[:5]=[11,count,randomizer.uniform(0,75),100,.5/(1+index%2)]
            row[29:29+count]=np.r_[0,np.sort(randomizer.uniform(.01,.99,count-2)),1]
            for offset in (8,15,22):row[offset:offset+count]=randomizer.uniform(0,.9,count)
            row[50:53]=np.array([13,14,18])/255
            rows.append(row)
        expected=np.array([three_band_joint_oracle(row) for row in rows])
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,3],expected[:,0],atol=3e-5,rtol=0)
        np.testing.assert_allclose(actual[:,4:7],expected[:,1:4],atol=3e-5,rtol=0)
        final=linear_color(np.array(rows)[:,50:53]*(1-expected[:,:1])+expected[:,1:4])
        np.testing.assert_allclose(actual[:,:3],final,atol=3e-5,rtol=0)

    def test_three_band_empty_bands_and_center_pixels_have_true_finite_area(self):
        rows=[];expected=[]
        for bands in ((0,0,0),(.002,0,0),(0,.003,0),(0,0,.004),(.002,.003,.004)):
            for distance in (0,.2,.4,.8):
                row=np.zeros(64,dtype=np.float32);row[:5]=[11,2,distance,100,.5]
                for offset,height in zip((8,15,22),bands,strict=True):row[offset:offset+2]=height
                row[29:31]=[0,1];rows.append(row);expected.append(three_band_joint_oracle(row,64))
        expected=np.array(expected);actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,3:7],expected,atol=3e-6,rtol=0)
        np.testing.assert_allclose(actual[:,:3],linear_color(expected[:,1:]),atol=3e-6,rtol=0)
        np.testing.assert_array_equal(actual[:4,:7],0)
        self.assertAlmostEqual(float(actual[4,3]),.4,delta=3e-6)

    def test_three_band_shallow_slopes_are_unbiased_and_direction_independent(self):
        rows=[]
        for first,last in ((.2,.22),(.22,.2),(.21,.21),(0,0),(.001,.003)):
            row=np.zeros(64,dtype=np.float32);row[:6]=[18,first,last,21,100,.5]
            rows.append(row)
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:3,0],.5,atol=3e-6,rtol=0)
        np.testing.assert_array_equal(actual[3:,0],0)
        # The retired coverage formula returned.75 for this actual.50 area.
        old=(.22+.005-.21)/(.22-.2)
        self.assertGreater(old-float(actual[0,0]),.24)

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

    def test_every_zoom_uses_resolvable_extrema_at_each_drawable_scale(self):
        rows = []
        for scale in (1, 2):
            for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500):
                row = np.zeros(64); row[:4] = [5, 1000, zoom, scale]
                rows.append(row)
        actual = self.execute(rows)[:, 0]
        for row, divisor in zip(rows, actual):
            # Each retained cell must cover the physical source footprint,
            # allowing the established six-percent reconstruction tolerance.
            self.assertGreaterEqual(divisor, max(1, .94*row[1]/(row[2]*row[3])))
        choices = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64)
        expected = [next(d for d in choices if d >= max(1, .94*row[1]/(row[2]*row[3])))
                    for row in rows]
        np.testing.assert_array_equal(actual, expected)

    def test_logical_rgb_detail_only_changes_the_two_accepted_retina_stops(self):
        rows = []
        for mode in (1, 2, 3):
            for scale in (1, 2):
                for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384,
                             576, 864, 1152, 1500):
                    row = np.zeros(64); row[:5] = [5, 1000, zoom, scale, mode]
                    rows.append(row)
        actual = self.execute(rows)
        for row, values in zip(rows, actual):
            if row[4] == 2 and row[3] == 2 and row[2] in (576, 864):
                np.testing.assert_array_equal(values[:2], [2, 1])
            else:
                self.assertEqual(values[0], values[1])

    def test_coarse_interval_color_preserves_fine_area_against_dense_oracle(self):
        rng = np.random.default_rng(851)
        rows, expected = [], []
        for fine_divisor, coarse_divisor in ((1, 2), (8, 13), (1, 3), (2, 5)):
            for index in range(50):
                row = np.zeros(64, dtype='<f4')
                row[:8] = [9, 8, fine_divisor, index % 2, coarse_divisor, 0, 0, .009]
                row[8:11] = rng.uniform(0, 1, 3)
                bounds = np.sort(rng.integers(-30000, 30001, (8, 2)), axis=1)
                if index % 10 == 0:
                    bounds[:] = 0
                if index % 10 == 1:
                    bounds[:, 1] = bounds[:, 0]
                packed = np.zeros((8, 4), dtype='<i2'); packed[:, 2:] = bounds
                row.view('u1')[64:128] = packed.view('u1').ravel()
                begin = int(row[3]) * coarse_divisor
                points = begin + (np.arange(32768) + .5) / 32768 * coarse_divisor
                cells = (points / fine_divisor).astype(int)
                cells = cells[cells < 8]
                row[5] = bounds[cells.min():cells.max()+1, 0].min() / 32767
                row[6] = bounds[cells.min():cells.max()+1, 1].max() / 32767
                widths = np.maximum((bounds[cells, 1] - bounds[cells, 0]) / 32767, row[7])
                occupancy = min(1, widths.mean() / max(float(row[6]-row[5]), row[7]))
                background = np.array([13, 14, 18]) / 255
                expected.append([occupancy, *(background + (row[8:11]-background)*occupancy)])
                rows.append(row)
        actual = self.execute(rows)[:, :4]
        np.testing.assert_allclose(actual, expected, atol=8e-5, rtol=0)
        self.assertTrue(((actual[:, 0] >= 0) & (actual[:, 0] <= 1)).all())
        # Treating each wider interval as fully occupied recreates brightening.
        old = self.source.replace(
            'return saturate(fineArea / max(coveredSpan * coarseWidth, 1e-8));',
            'return 1.0;')
        self.assertNotEqual(old, self.source)
        self.assertGreater(float(np.max(np.abs(self.execute(rows, old)[:, :4]-actual))), .25)

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
                    if coverage < .18:
                        lifted += 1.5 * coverage * (1-coverage/.18)**2
                    expected.append(linear_color(background*(1-lifted)
                                                 + np.array(color)*lifted))
        actual = self.execute(rows)[:, 4:7]
        np.testing.assert_allclose(actual, expected, atol=3e-6, rtol=0)
        old = self.source.replace(
            'const float3 color0 = saturate(encodedColors[segment]);',
            'const float3 color0 = display_srgb_to_linear(saturate(encodedColors[segment]));').replace(
            'const float3 color1 = saturate(encodedColors[segment + 1]);',
            'const float3 color1 = display_srgb_to_linear(saturate(encodedColors[segment + 1]));')
        self.assertNotEqual(old, self.source)
        self.assertGreater(float(np.max(np.abs(self.execute(rows, old)[:, 4:7]-expected))), .1)

    def test_restoring_separate_edge_averages_breaks_blue_final_fragment_oracle(self):
        row = np.zeros(64, dtype=np.float32); row[:6] = [2, 2, 0, 60, .5, 1]
        row[8:10] = [-.8, .6]; row[15:17] = [-.6, .8]
        row[22:24] = [0, 1]; row[29:35] = 1
        row[53:58] = [1,1,1,1,1]
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
        integral = interval_oracle(row)
        coverage = integral[0]
        lifted = coverage*(1+1.5*(1-coverage/.18)**2) if coverage < .18 else coverage
        expected = linear_color(integral[1:] * lifted / coverage)
        fixed = self.execute([row])[0, 4:7]
        broken = self.execute([row], mutated)[0, 4:7]
        np.testing.assert_allclose(fixed, expected, atol=3e-5, rtol=0)
        # The native BLUE pass now decodes after encoded composition. Retain
        # the large visible contrast check in display codes, not linear light.
        self.assertGreater(float(np.max(np.abs(encoded_color(broken) - encoded_color(expected)))), .4)

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


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'native C++ preview reference requires arm64 macOS')
class ThreeBandPreviewRaster(unittest.TestCase):
    """Actual production CPU bitmap versus independently recovered native math."""
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='sb-threeband-preview-cpu-')
        cls.addClassCleanup(cls.temp.cleanup);cls.directory=Path(cls.temp.name)
        source=(ROOT/'native/SoundboxDeck.mm').read_text()
        start=source.index('static std::vector<uint8_t> threeBandOverviewPixels(')
        end=source.index('static double waveformAttributeOffset',start)
        body=source[start:end]
        code=r'''
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <utility>
#include <vector>
BODY
int main(int argc,char **argv){
 if(argc!=3)return 2;
 std::ifstream input(argv[1],std::ios::binary);
 std::vector<uint8_t> rows((std::istreambuf_iterator<char>(input)),std::istreambuf_iterator<char>());
 if(rows.size()%3)return 3;
 auto output=threeBandOverviewPixels(rows.empty()?nullptr:rows.data(),rows.size()/3);
 if(!rows.empty() && output.empty())return 4;
 std::ofstream stream(argv[2],std::ios::binary);
 stream.write(reinterpret_cast<const char*>(output.data()),output.size());
 return stream?0:5;
}
'''.replace('BODY',body)
        cpp=cls.directory/'preview.cpp';cpp.write_text(code);cls.binary=cls.directory/'preview'
        subprocess.run(['clang++','-std=c++17',str(cpp),'-o',str(cls.binary)],check=True,capture_output=True,text=True)

    @staticmethod
    def native_reference(rows, *, quantized=True):
        # Numeric facts independently recovered from create3BandsWholeImage:
        # global normalization and three opaque cumulative centered fills.
        data=np.asarray(rows,dtype=float)
        maximum=min(128,max(1,float(np.max(data[:,0]+data[:,1]+64*(1-np.cos(np.pi*data[:,2]/128))))))
        tops=np.array([24*(1-(1.2*data[:,0])/maximum),
                       24*(1-(1.2*data[:,0]+.6*data[:,1])/maximum),
                       24*(1-(1.2*data[:,0]+.6*data[:,1]+76.8*(1-np.cos(np.pi*data[:,2]/128)))/maximum)])
        tops=np.clip(tops,0,23)
        pixels=np.zeros((25,1200,4),dtype=float)
        colors=np.array([[0,85,225],[255,166,0],[255,255,255]],dtype=float)
        # Geometric rectangle/pixel intersections, white behind orange behind
        # blue, reproduce the native canvas without borrowing production code.
        for layer in (2,1,0):
            for y in range(25):
                alpha=np.clip(np.minimum(y+1,24)-np.maximum(y,tops[layer]),0,1)
                pixels[y,:,:3]=colors[layer]*alpha[:,None]+pixels[y,:,:3]*(1-alpha[:,None])
                pixels[y,:,3]=255*alpha+pixels[y,:,3]*(1-alpha)
        return np.floor(pixels+.5).astype(np.uint8) if quantized else pixels

    def render(self,rows):
        source=self.directory/'input.bin';output=self.directory/'output.rgba'
        source.write_bytes(np.asarray(rows,dtype=np.uint8).tobytes())
        result=subprocess.run([str(self.binary),str(source),str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return np.frombuffer(output.read_bytes(),np.uint8).reshape(25,1200,4)

    def test_native_global_normalization_cumulative_rows_and_canvas_match_native(self):
        fixtures=[]
        for primary in ((0,0,0),(20,0,0),(0,32,0),(0,0,64),(10,40,20)):
            data=np.zeros((1200,3),dtype=np.uint8);data[0]=primary;fixtures.append(data)
        data=np.zeros((1200,3),dtype=np.uint8)
        data[:9]=[[1,2,3],[12,42,11],[80,10,40],[127,127,127],[60,3,110],
                  [0,10,0],[5,0,1],[0,0,50],[126,1,0]]
        fixtures.append(data)
        randomizer=np.random.default_rng(20261121)
        fixtures.append(randomizer.integers(0,128,size=(1200,3),dtype=np.uint8))
        for index,data in enumerate(fixtures):
            with self.subTest(case=index):
                actual=self.render(data)
                expected=self.native_reference(data)
                delta=np.abs(actual.astype(np.int16)-expected.astype(np.int16))
                self.assertLessEqual(int(delta.max()),1)
                # Different libm/SIMD cosine paths can straddle a half-code
                # tie; any nonidentical byte must be exactly such a tie.
                different=delta>0
                if different.any():
                    unrounded=self.native_reference(data,quantized=False)[different]
                    np.testing.assert_allclose(unrounded-np.floor(unrounded),.5,atol=1e-8,rtol=0)
                np.testing.assert_array_equal(actual[24],0)
                self.assertTrue(np.all(actual[:,:,:3]<=actual[:,:,3:4]))
                # Native mini intentionally retains its single blue baseline;
                # main zero-energy coverage is tested independently above.
                np.testing.assert_array_equal(actual[23,:,3],255)

    def test_invalid_signed_preview_values_are_rejected_before_any_output(self):
        data=np.zeros((1200,3),dtype=np.uint8);data[-1,1]=128
        source=self.directory/'invalid.bin';output=self.directory/'invalid.rgba'
        source.write_bytes(data.tobytes())
        result=subprocess.run([str(self.binary),str(source),str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,4)
        self.assertFalse(output.exists())

    def test_clear_and_wrong_native_column_count_do_not_emit_a_partial_image(self):
        source=self.directory/'clear.bin';output=self.directory/'clear.rgba'
        source.write_bytes(b'')
        result=subprocess.run([str(self.binary),str(source),str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertEqual(output.read_bytes(),b'')
        source=self.directory/'wrong-count.bin';output=self.directory/'wrong-count.rgba'
        source.write_bytes(bytes(1199*3))
        result=subprocess.run([str(self.binary),str(source),str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,4);self.assertFalse(output.exists())
