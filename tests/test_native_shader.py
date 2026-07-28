"""Source-level contracts for the runtime-compiled Metal waveform shader."""

from pathlib import Path
import unittest


class NativeWaveformShader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        cls.source = source
        cls.shader = source.split('@R"METAL(', 1)[1].split(")METAL\"", 1)[0]

    def test_every_zoom_uses_one_continuous_waveform_path(self):
        self.assertIn("inline WaveSurface filtered_wave_surface", self.shader)
        self.assertIn(
            "inline float filtered_envelope_coverage", self.shader)
        self.assertNotIn("if (u.pixelsPerSecond >", self.shader)
        self.assertNotIn("if (u.pixelsPerSecond <", self.shader)
        self.assertEqual(
            self.shader.count("fragment float4 wave_surface_fragment"), 1)
        self.assertNotIn("wave_source_fragment", self.shader)
        self.assertNotIn("wave_display_fragment", self.shader)

    def test_geometry_and_color_share_continuous_lod(self):
        helper = self.shader.split(
            "inline WaveSurface filtered_wave_surface", 1
        )[1].split(
            "inline float filtered_envelope_coverage", 1
        )[0]
        # ONE level. Blending two power-of-two mip lattices scrolled them at
        # the same speed with different quantization, and they beat against
        # each other -- bad edges at exactly the zooms whose fractional LOD
        # sat near 0.62, clean at those near 0.05.
        self.assertIn("biased_lod(lodFootprint, mipCount)", helper)
        self.assertIn("integrated_wave_level(", helper)
        self.assertNotIn("lowLevel", helper)
        self.assertNotIn("highLevel", helper)
        self.assertNotIn("lodMix", helper)

    def test_audio_detail_uses_the_same_continuous_lod_rule(self):
        helper = self.shader.split(
            "inline float filtered_envelope_coverage", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertIn("biased_lod(lodFootprint, mipCount)", helper)
        self.assertIn("integrated_envelope_level(", helper)
        self.assertNotIn("lowLevel", helper)
        self.assertNotIn("lodMix", helper)

    def test_current_geometry_keeps_u16_precision_until_display_upload(self):
        self.assertIn(
            "constexpr float kWaveformDisplayExponent = 0.80f;",
            self.source)
        self.assertIn(
            "constexpr float kWaveformVisibilityShoulder = 1.0f / 128.0f;",
            self.source)
        self.assertIn(
            "constexpr float kWaveformVisibilityKnee = 4.0f / 65535.0f;",
            self.source)
        visibility = self.source.split(
            "static float displayWaveformHeight", 1
        )[1].split("struct WaveSample", 1)[0]
        self.assertIn("if (encodedHeight == 0)", visibility)
        self.assertIn("return 0.0f;", visibility)
        self.assertIn("1.0f - std::exp(", visibility)
        self.assertIn(
            "struct AudioEnvelopeSample { ushort height; };",
            self.shader)
        self.assertGreaterEqual(self.shader.count("/ 65535.0"), 3)
        self.assertIn("setAudioDetail16(", self.source)
        self.assertIn("setPackedOverview16(", self.source)

    def test_every_lod_integrates_the_physical_pixel_before_blending(self):
        wave = self.shader.split(
            "inline WaveSurface integrated_wave_level", 1
        )[1].split(
            "inline float integrated_envelope_level", 1
        )[0]
        envelope = self.shader.split(
            "inline float integrated_envelope_level", 1
        )[1].split(
            "inline WaveSurface filtered_wave_surface", 1
        )[0]
        for helper in (wave, envelope):
            self.assertIn("const uint tapCount = kIntegrationTaps", helper)
            self.assertIn("for (uint tap = 0; tap < tapCount; ++tap)", helper)
            self.assertIn("integration_offset(tap, tapCount)", helper)
            self.assertIn(
                "baseLocal + offset * sampleFootprint", helper)
        filtered = self.shader.split(
            "inline WaveSurface filtered_wave_surface", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertEqual(filtered.count("integrated_wave_level("), 1)
        self.assertEqual(filtered.count("integrated_envelope_level("), 1)

    def test_no_temporal_shutter_blurs_the_moving_waveform(self):
        """The eye tracks a scrolling waveform, so rendered blur only smears.

        Under smooth pursuit the waveform is stationary on the retina and a
        shutter destroys exactly the detail it is meant to protect, while the
        display's own sample-and-hold already supplies persistence blur.
        """
        for banned in (
                "motionIntegration", "MotionIntegration", "tentMix",
                "TentMix", "MotionFootprint",
                "smoothedPresentationInterval_"):
            self.assertNotIn(banned, self.source, f"{banned} came back")
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split("vertex Out grid_vertex", 1)[0]
        self.assertIn(
            "waveIntegrationFootprint = waveSpatialFootprint", fragment)
        self.assertIn(
            "audioIntegrationFootprint = audioSpatialFootprint", fragment)

    def test_lod_is_footprint_matched_and_never_negatively_biased(self):
        """A pixel cannot display more than about one envelope value.

        `kLodBias` was 2 -- selecting a mip two levels sharper -- on the
        theory that supersampling buys mip bias back as detail. It does not.
        A negative bias puts four to eight independent values under one
        physical pixel; the surplus becomes content above the pixel Nyquist,
        which is per-frame flicker, not detail. Measured on a real track as
        the share of coverage energy above the pixel Nyquist, bias 2 scored
        0.062 mean / 0.147 worst against bias 0's 0.029 / 0.066, worse at
        every one of the thirteen zooms. This is why the middle zooms were
        fuzzy while both extremes looked fine.
        """
        self.assertIn("constant uint kIntegrationTaps = 8u;", self.shader)
        self.assertIn("constant float kLodBias = 0.0;", self.shader)
        helper = self.shader.split(
            "inline uint biased_lod", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("ceil(log2(max(1.0, lodFootprint)) - kLodBias)", helper)
        self.assertNotIn("floor(log2(", helper)
        self.assertNotIn("round(log2(", helper)
        self.assertIn("0.0, float(mipCount - 1)", helper)
        # At most ONE envelope value per physical pixel. Above that the
        # surplus is the hairy contour that rewrites itself every frame.
        import math
        for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576,
                     864, 1152, 1536):
            fp = 6300.0 / (zoom * 2.0)
            level = max(0.0, math.ceil(math.log2(max(1.0, fp))))
            per_pixel = fp / 2 ** level
            self.assertLessEqual(
                per_pixel, 1.0,
                f"zoom {zoom} puts {per_pixel:.2f} values under one pixel")

    def test_pixel_taps_stay_flat_weighted(self):
        """A box across the pixel keeps a moving contour crisp, not smeared.

        Only the colour helper still carries weights: silhouette coverage is
        now exact area under the tap profile, which needs no weighting.
        """
        body = self.shader.split(
            "inline WaveSurface integrated_wave_level", 1
        )[1].split("\n}", 1)[0]
        self.assertNotIn("* 0.25", body)
        self.assertIn("const float weight = 1.0;", body)
        self.assertIn("totalWeight += weight", body)
        self.assertIn("/= totalWeight", body)

    def test_midpoint_taps_are_symmetric_about_the_pixel_center(self):
        """count == 4 reproduces {-0.375, -0.125, 0.125, 0.375} exactly."""
        helper = self.shader.split(
            "inline float integration_offset", 1
        )[1].split("\n}", 1)[0]
        self.assertIn(
            "(float(tap) + 0.5) / float(count) - 0.5", helper)
        for tap, expected in enumerate((-0.375, -0.125, 0.125, 0.375)):
            self.assertAlmostEqual((tap + 0.5) / 4 - 0.5, expected)
        offsets = [(t + 0.5) / 8 - 0.5 for t in range(8)]
        self.assertAlmostEqual(sum(offsets), 0.0)

    def test_lod_selection_uses_only_the_spatial_footprint(self):
        """Temporal footprint driving LOD made maximum zoom blocky."""
        for name in (
                "inline WaveSurface filtered_wave_surface",
                "inline float filtered_envelope_coverage"):
            body = self.shader.split(name, 1)[1].split("\n}", 1)[0]
            lod = body.split("biased_lod(", 1)[1].split(")", 1)[0]
            self.assertIn("lodFootprint", lod)
            self.assertNotIn("integrationFootprint", lod)

    def test_early_out_bound_comes_from_a_max_mip_level(self):
        """Pyramid levels are maxima, so a coarse bin bounds every tap."""
        helper = self.shader.split(
            "inline float envelope_span_bound", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("ceil(log2(max(span, 1.0)))", helper)
        self.assertIn("envelope_support_max(", helper)
        self.assertIn("bound = max(bound, supportMax)", helper)
        self.assertIn("-halfSpan : halfSpan", helper)
        coverage = self.shader.split(
            "inline float filtered_envelope_coverage", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("envelope_span_bound(", coverage)
        self.assertIn(
            "distance > bound * heightScale + antialias", coverage)

    def test_subpixel_color_is_amplitude_weighted_before_compositing(self):
        wave = self.shader.split(
            "inline WaveSurface integrated_wave_level", 1
        )[1].split(
            "inline float integrated_envelope_level", 1
        )[0]
        self.assertIn("sample.weightedColor * weight", wave)
        self.assertIn("sample.colorWeight * weight", wave)
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertGreaterEqual(
            fragment.count("/ max("), 2)

    def test_direct_fallback_uses_positive_cubic_reconstruction(self):
        """The compact/overview fallback keeps its non-overshooting filter."""
        weights = self.shader.split(
            "inline float4 cubic_bspline_weights", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("(1.0 - 3.0 * t + 3.0 * t2 - t3) / 6.0", weights)
        self.assertIn("(4.0 - 6.0 * t2 + 3.0 * t3) / 6.0", weights)
        self.assertIn(
            "(1.0 + 3.0 * t + 3.0 * t2 - 3.0 * t3) / 6.0", weights)
        self.assertIn("t3 / 6.0", weights)
        for name in ("inline WaveTap wave_tap",
                     "inline EnvelopeTap envelope_tap"):
            body = self.shader.split(name, 1)[1].split("\n}", 1)[0]
            self.assertIn("cubic_bspline_weights(", body)
            self.assertIn(
                "for (int support = -1; support <= 2; ++support)", body)
            self.assertNotIn("mix(", body)

        # The four-tap kernel is a convex combination at every phase: it
        # cannot invent a negative envelope or overshoot a real peak.
        for step in range(101):
            t = step / 100
            t2 = t * t
            t3 = t2 * t
            reference = (
                (1 - 3 * t + 3 * t2 - t3) / 6,
                (4 - 6 * t2 + 3 * t3) / 6,
                (1 + 3 * t + 3 * t2 - 3 * t3) / 6,
                t3 / 6,
            )
            self.assertTrue(all(weight >= 0 for weight in reference))
            self.assertAlmostEqual(sum(reference), 1.0)

    def test_all_silhouettes_filter_final_coverage(self):
        """The direct fallback integrates coverage, never blended heights.

        The per-tap `pair_coverage` mean was replaced by `profile_coverage`
        because averaging N near-binary tests quantizes an edge to N+1 levels
        and makes the contour wiggle as the taps slide. The fixed song-lattice
        path deliberately interpolates height attributes before calculating
        final-pixel coverage once; this contract protects the older direct
        fallback and overview path.
        """
        self.assertEqual(
            self.shader.count("inline float profile_coverage"), 1)
        self.assertGreaterEqual(self.shader.count("profile_coverage("), 3)
        self.assertNotIn("pair_coverage", self.shader)
        fallback = self.shader.split(
            "constant uint kIntegrationTaps", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertNotIn(".height = mix(", fallback)

    def test_main_lane_uses_a_fixed_song_attribute_lattice(self):
        kernel = self.shader.split(
            "kernel void wave_attribute_kernel", 1
        )[1].split(
            "constant uint kIntegrationTaps", 1
        )[0]
        self.assertIn("device float4 *attributes [[buffer(5)]]", kernel)
        self.assertIn("detail_lod(", kernel)
        self.assertIn("sharp_wave_tap(", kernel)
        self.assertIn("sharp_envelope_tap(", kernel)
        lod = self.shader.split(
            "inline uint detail_lod", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("ceil(log2(max(1.0, footprint)))", lod)
        self.assertNotIn("floor(log2(", lod)

    def test_mid_zoom_stability_kernel_preserves_close_detail(self):
        kernel = self.shader.split(
            "kernel void wave_attribute_kernel", 1
        )[1].split(
            "constant uint kIntegrationTaps", 1
        )[0]
        self.assertIn(
            "1.0 - smoothstep(\n"
            "        384.0, 576.0, u.pixelsPerSecond)", kernel)
        self.assertIn("sharp_wave_tap(", kernel)
        self.assertIn("const WaveTap smoothWave = wave_tap(", kernel)
        self.assertIn("sharp_envelope_tap(", kernel)
        self.assertIn(
            "const EnvelopeTap smoothDetail = envelope_tap(", kernel)
        self.assertIn(
            "sharpDetail.height, stableDetail, stableKernel", kernel)

    def test_only_64_and_184_receive_the_narrow_physical_pixel_filter(self):
        helper = self.shader.split(
            "inline float targeted_stop_filter", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("abs(pixelsPerSecond - 64.0)", helper)
        self.assertIn("abs(pixelsPerSecond - 184.0)", helper)
        self.assertIn("smoothstep(0.0, 8.0", helper)
        self.assertIn("smoothstep(0.0, 12.0", helper)

        kernel = self.shader.split(
            "kernel void wave_attribute_kernel", 1
        )[1].split(
            "constant uint kIntegrationTaps", 1
        )[0]
        self.assertIn(
            "0.5 / max(1.0, u.pixelsPerSecond * scale)", kernel)
        self.assertIn("waveLocal - waveFilterOffset", kernel)
        self.assertIn("waveLocal + waveFilterOffset", kernel)
        self.assertIn(
            "leftSmoothWave.height\n"
            "            + 2.0 * smoothWave.height\n"
            "            + rightSmoothWave.height", kernel)
        self.assertIn("detailLocal - detailFilterOffset", kernel)
        self.assertIn("detailLocal + detailFilterOffset", kernel)

        def stop_weight(zoom):
            def tapered(distance, radius):
                t = min(1.0, max(0.0, distance / radius))
                return 1.0 - t * t * (3.0 - 2.0 * t)
            return max(
                tapered(abs(zoom - 64.0), 8.0),
                tapered(abs(zoom - 184.0), 12.0))

        levels = (
            24, 32, 48, 64, 92, 128, 184,
            256, 384, 576, 864, 1152, 1536)
        self.assertEqual(
            [zoom for zoom in levels if stop_weight(zoom) > 0.0],
            [64, 184])
        self.assertEqual(stop_weight(64), 1.0)
        self.assertEqual(stop_weight(184), 1.0)

    def test_close_kernel_is_interpolating_monotone_cubic(self):
        scalar = self.shader.split(
            "inline float monotone_hermite", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("monotone_slope(d0, d1)", scalar)
        self.assertIn("monotone_slope(d1, d2)", scalar)
        self.assertIn(
            "clamp(value, min(left, right), max(left, right))", scalar)

        values = []
        for phase in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
            before, left, right, after = 0.2, 0.4, 0.9, 0.7
            d0, d1, d2 = (
                left - before, right - left, after - right)

            def slope(a, b):
                return 0.0 if a * b <= 0 else 2 * a * b / (a + b)

            m0, m1 = slope(d0, d1), slope(d1, d2)
            t, t2, t3 = phase, phase * phase, phase ** 3
            value = (
                (2 * t3 - 3 * t2 + 1) * left
                + (t3 - 2 * t2 + t) * m0
                + (-2 * t3 + 3 * t2) * right
                + (t3 - t2) * m1
            )
            values.append(value)
            self.assertGreaterEqual(value, min(left, right))
            self.assertLessEqual(value, max(left, right))
        self.assertAlmostEqual(values[0], left)
        self.assertAlmostEqual(values[-1], right)

    def test_all_zooms_use_subpixel_song_attribute_motion(self):
        native = self.source
        self.assertIn(
            "attributeSamplesPerPixel =\n"
            "            kWaveAttributeSmoothSamplesPerPixel;", native)
        self.assertNotIn("kWaveAttributeStableZoom", native)
        fragment = self.shader.split(
            "if (u.waveAttributeValid != 0", 1
        )[1].split(
            "const float secondOffset", 1
        )[0]
        self.assertIn("mix(", fragment)
        self.assertNotIn("round(tileX)", fragment)

    def test_attribute_surface_stores_premultiplied_color_not_coverage(self):
        kernel = self.shader.split(
            "kernel void wave_attribute_kernel", 1
        )[1].split(
            "constant uint kIntegrationTaps", 1
        )[0]
        self.assertIn(
            "attributes[id] = float4(color * height, height)", kernel)
        self.assertNotIn("profile_coverage(", kernel)
        self.assertNotIn("vivid_wave_coverage(", kernel)

        fragment = self.shader.split(
            "if (u.waveAttributeValid != 0", 1
        )[1].split(
            "const float secondOffset", 1
        )[0]
        self.assertIn("weightedColor += attribute.rgb * weight", fragment)
        self.assertNotIn(
            "weightedColor += attribute.rgb * attribute.a * weight",
            fragment)
        fragment = self.shader.split(
            "if (u.waveAttributeValid != 0", 1
        )[1].split(
            "const float secondOffset", 1
        )[0]
        self.assertIn(
            "constant uint kAttributeProfileSamples = 5u;", self.shader)
        self.assertIn("linear_attribute(", fragment)
        self.assertIn(
            "profile_coverage(\n"
            "            heights, kAttributeProfileSamples", fragment)
        self.assertIn("saturate(coverage)", fragment)

    def test_edge_coverage_is_continuous_not_a_tap_average(self):
        """A quantized edge is what "wiggly moving edges" actually is."""
        helper = self.shader.split(
            "inline float profile_coverage", 1
        )[1].split("\n}", 1)[0]
        # Exact linear crossing between adjacent taps, with a one-pixel
        # vertical band as the floor so a flat segment still antialiases.
        self.assertIn("(hi + band - threshold) / max(hi - lo, 2.0 * band)",
                      helper)
        self.assertIn("float(count - 1)", helper)
        for name in (
                "inline WaveSurface integrated_wave_level",
                "inline float integrated_envelope_level"):
            body = self.shader.split(name, 1)[1].split("\n}", 1)[0]
            self.assertIn("float heights[kIntegrationTaps]", body)
            self.assertIn("profile_coverage(", body)

    def _coverage(self, heights, threshold, band=0.0063):
        """Reference implementation of the shader's exact-area model."""
        total = 0.0
        for a, b in zip(heights, heights[1:]):
            lo, hi = min(a, b), max(a, b)
            total += min(1.0, max(0.0, (hi + band - threshold)
                                  / max(hi - lo, 2.0 * band)))
        return total / max(1, len(heights) - 1)

    def test_sliding_taps_move_edge_coverage_smoothly(self):
        """The old mean could only step in 1/8ths; this must not step."""
        import math
        # A contour sweeping past the threshold as playback advances.
        steps = []
        for phase in range(200):
            shift = phase / 200.0
            heights = [
                0.5 + 0.25 * math.sin((i / 8.0 + shift) * 2 * math.pi)
                for i in range(8)
            ]
            steps.append(self._coverage(heights, 0.5))
        jumps = [abs(b - a) for a, b in zip(steps, steps[1:])]
        # Averaging eight binary taps would produce 0.125 jumps.
        self.assertLess(
            max(jumps), 0.05,
            f"edge coverage still steps: largest jump {max(jumps):.3f}")

    def test_flat_profile_still_antialiases_vertically(self):
        """With no horizontal slope the band must supply a soft edge."""
        flat = [0.5] * 8
        self.assertAlmostEqual(self._coverage(flat, 0.5), 0.5, places=5)
        self.assertEqual(self._coverage(flat, 0.9), 0.0)
        self.assertEqual(self._coverage(flat, 0.1), 1.0)

    def test_composite_is_opaque_without_an_outer_ghost(self):
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertGreaterEqual(fragment.count(", 1.0);"), 2)
        self.assertNotIn("bodyCoverage", self.shader)
        self.assertNotIn("outerAlpha", self.shader)
        self.assertNotIn("0.42", self.shader)

    def test_thin_waveform_color_is_vivid_without_expanding_height(self):
        helper = self.shader.split(
            "inline float vivid_wave_coverage", 1
        )[1].split(
            "vertex Out wave_surface_vertex", 1
        )[0]
        self.assertIn("saturate(coverage)", helper)
        self.assertIn("1.0 - inverse * inverse * inverse", helper)
        self.assertNotIn("max(coverage", helper)
        self.assertNotIn(".height", helper)
        self.assertGreaterEqual(
            self.shader.count("vivid_wave_coverage("), 2)

    def test_scrolling_lane_uses_exact_edge_coverage_at_every_zoom(self):
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertEqual(fragment.count("saturate(coverage)"), 2)
        self.assertNotIn("scrolling_wave_coverage", self.shader)
        # The small overview stays vivid; the scrolling lane uses physical
        # coverage at every zoom. Height and hue reconstruction are untouched.
        self.assertIn(
            "vivid_wave_coverage(surface.coverage)", fragment)

    def test_every_beat_grid_line_is_opaque_over_waveform_colors(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        self.assertIn("const float a = 1.0f;", native)
        self.assertNotIn("downbeat ? 1.0f : 0.32f", native)
        self.assertNotIn("downbeat ? 0.82f : 0.32f", native)

    def test_scrolling_grid_lines_use_analytic_subpixel_coverage(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        helper = native.split(
            "static void addGridRect", 1
        )[1].split(
            "static void addGridTriangle", 1
        )[0]
        self.assertIn("bool analyticVertical = false", helper)
        self.assertIn("const float fringe = analyticVertical ? 0.5f", helper)
        self.assertIn("lineX0, lineHalf", helper)
        self.assertEqual(native.count("false, true);"), 2)

        grid = self.shader.split(
            "vertex Out grid_vertex", 1
        )[1].split(
            ")METAL\"", 1
        )[0]
        self.assertIn(
            "p.lineDistancePoints * max(1.0, u.drawableScale)", grid)
        self.assertIn(
            "p.lineHalfWidthPoints * max(1.0, u.drawableScale)", grid)
        self.assertIn(
            "in.lineHalfWidthPixels + 0.5\n"
            "            - abs(in.lineDistancePixels)", grid)
        self.assertIn("alpha *= coverage", grid)
        self.assertIn(
            "display_srgb_to_linear(in.color.rgb)", grid)
        self.assertNotIn("round(", grid)

    def test_final_wave_and_grid_compositing_is_linear_light(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        self.assertIn(
            "metalLayer_.pixelFormat = MTLPixelFormatBGRA8Unorm_sRGB",
            native,
        )
        self.assertIn(
            "pass.colorAttachments[0].clearColor = MTLClearColorMake(\n"
            "            0.004024717, 0.004391442, 0.006048833, 1.0)",
            native,
        )
        self.assertIn("inline float3 display_srgb_to_linear", self.shader)
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertEqual(
            fragment.count("display_srgb_to_linear("), 4)
        self.assertNotIn("displayCoverage", self.shader)

    def test_color_pyramid_is_amplitude_weighted(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        pyramid = native.split(
            "static std::vector<WaveSample> buildWavePyramid", 1
        )[1].split(
            "static WaveSample packedWaveSample", 1
        )[0]
        self.assertIn("a.r * aEnergy + b.r * bEnergy", pyramid)
        self.assertIn("nextEnergy.push_back(energy)", pyramid)
        self.assertIn("std::max(a.height, b.height)", pyramid)

    def test_cue_shader_supports_absolute_overview_and_bottom_arrows(self):
        grid = self.shader.split("vertex Out grid_vertex", 1)[1]
        self.assertIn("p.overview != 0", grid)
        self.assertIn("u.durationSeconds * 1000.0", grid)
        self.assertIn("p.yPixels < 0.0", grid)
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        cues = native.split("bool setCues", 1)[1].split(
            "bool setDetailAnalysis", 1)[0]
        self.assertIn("addCueLetter(", cues)
        self.assertIn("0.204f", cues)
        self.assertIn("addGridTriangle(", cues)
        self.assertIn("0.02f, 0.024f, 0.031f", cues)
        self.assertIn("-6.0f, kOverviewHeight", cues)
        self.assertIn("6.0f, -0.01f", cues)

    def test_session_cue_has_arrow_only_in_overview_and_detail(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        session = native.split("bool setSessionCue", 1)[1].split(
            "bool setCues", 1)[0]
        self.assertIn("0.941f", session)
        self.assertIn("0.541f", session)
        self.assertNotIn("addGridRect(", session)
        self.assertEqual(session.count("addGridTriangle("), 2)
        self.assertIn("kOverviewHeight", session)
        self.assertIn("-5.5f", session)
        self.assertIn("sessionCueBuffer_", native)
        self.assertIn("sb_deck_set_session_cue", native)

    def test_native_does_not_remix_the_persisted_band_master(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        spectral = native.split(
            "bool setSpectralAnalysis", 1
        )[1].split(
            "bool setAudioDetail", 1
        )[0]
        self.assertIn("return false;", spectral)
        self.assertNotIn("newBufferWithBytes", spectral)
        self.assertNotIn("soundboxSpectralColor", native)

    def test_audio_detail_uses_integer_anchor_plus_local_phase(self):
        self.assertIn("uint audioDetailCurrentIndex;", self.shader)
        self.assertIn("float audioDetailCurrentPhase;", self.shader)
        helper = self.shader.split(
            "inline EnvelopeTap envelope_tap", 1
        )[1].split(
            "inline float envelope_support_max", 1
        )[0]
        self.assertIn("const uint anchor = baseAnchor / divisor", helper)
        audio = self.shader.split(
            "if (u.audioDetailSampleCount > 0", 1
        )[1].split("if (coverage <= 0.0)", 1)[0]
        self.assertIn("u.audioDetailCurrentPhase", audio)
        self.assertIn("u.audioDetailCurrentIndex", audio)
        self.assertNotIn(
            "songSeconds * u.audioDetailSamplesPerSecond", audio)

    def test_color_uses_integer_anchor_plus_local_phase(self):
        self.assertIn("uint waveCurrentIndex;", self.shader)
        self.assertIn("float waveCurrentPhase;", self.shader)
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "if (u.audioDetailSampleCount > 0", 1
        )[0]
        self.assertIn("u.waveCurrentIndex", fragment)
        self.assertIn("u.waveCurrentPhase", fragment)

    def test_scrolling_lane_composites_coverage_once_at_the_final_pixel(self):
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertIn("filtered_wave_surface(", fragment)
        self.assertIn("filtered_envelope_coverage(", fragment)
        self.assertNotIn("texture2d<", fragment)
        self.assertNotIn(".sample(", fragment)
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        draw = native.split("void draw(CAMetalDisplayLinkUpdate", 1)[1].split(
            "private:", 1)[0]
        self.assertIn(
            "[encoder setRenderPipelineState:pipeline_]", draw)
        self.assertIn(
            "[compute setComputePipelineState:attributePipeline_]", draw)
        self.assertIn(
            "[encoder setFragmentBuffer:", draw)
        self.assertIn("attributeNeedsPrint", draw)
        self.assertIn("availableMargin * 0.5", draw)
        self.assertGreaterEqual(
            native.count("waveformRevision_.fetch_add"), 5)
        self.assertNotIn("waveTile", native)
        self.assertNotIn("displayPipeline_", native)
        self.assertNotIn("blitCommandEncoder", draw)
        self.assertIn(
            "wave.colorAttachments[0].pixelFormat = metalLayer_.pixelFormat",
            native,
        )

    def test_direct_motion_uses_continuous_physical_pixel_coordinates(self):
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertIn("in.position.x / scale", fragment)
        self.assertGreaterEqual(
            fragment.count("u.pixelsPerSecond * scale"), 2)
        self.assertNotIn("horizontalScale", fragment)
        self.assertNotIn("tileCurrentOffsetTexels", self.shader)

    def test_high_velocity_motion_keeps_the_physical_pixel_footprint(self):
        """Superseded the temporal shutter: the pixel is the whole window."""
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertIn(
            "waveSpatialFootprint, waveIntegrationFootprint", fragment)
        self.assertIn(
            "audioSpatialFootprint, audioIntegrationFootprint", fragment)
        self.assertNotIn("if (u.pixelsPerSecond >", fragment)

    def test_grid_uses_integer_milliseconds_plus_local_phase(self):
        self.assertIn(
            "struct GridVertex { uint songMillis; float songMillisPhase;",
            self.shader)
        self.assertIn(
            "float(int(p.songMillis) - int(u.currentMillis))",
            self.shader)
        self.assertIn(
            "p.songMillisPhase - u.currentMillisPhase", self.shader)
        grid = self.shader.split("vertex Out grid_vertex", 1)[1]
        self.assertNotIn(
            "p.songSeconds - u.currentSeconds", grid)

    def test_audio_clock_corrections_change_velocity_not_position(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        clock = native.split(
            "void updateVisualClock", 1
        )[1].split(
            "void createMetalView", 1
        )[0]
        self.assertIn("visualEffectiveRate_", clock)
        self.assertIn(
            "visualAnchorFrame_.store(expected", clock)
        self.assertNotIn("visualAnchorFrame_.fetch_add", clock)

    def test_display_motion_uses_the_future_presentation_timestamp(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        self.assertIn(
            "predictedFrame(update.targetPresentationTimestamp)", native)

    def test_mouse_hold_uses_the_drawable_already_presented(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        hold = native.split("uint64_t beginPreviewHold()", 1)[1].split(
            "void beginWaveformUpdate", 1
        )[0]
        self.assertIn("presentedFrames_->latestTime <= now", hold)
        self.assertIn("kFreshPresentationWindow", hold)
        self.assertIn("frame = presentedFrames_->latestFrame", hold)
        self.assertIn(
            "presentedFrames_->scheduledFrame", hold)
        self.assertIn("frame = visualFrameNow()", hold)
        draw = native.split(
            "void draw(CAMetalDisplayLinkUpdate", 1
        )[1].split(
            "private:", 1
        )[0]
        self.assertIn("addPresentedHandler", draw)
        self.assertIn("drawable.presentedTime", draw)
        self.assertIn("actualTime <= 0.0", draw)
        self.assertIn("preferredFrameLatency = 1.0f", native)

    def test_staged_waveform_buffers_have_one_atomic_visible_commit(self):
        native = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
        header = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.h"
        ).read_text()
        self.assertIn("sb_deck_begin_waveform_update", header)
        self.assertIn("sb_deck_end_waveform_update", header)
        self.assertIn(
            "waveformUpdateDepth_.load(std::memory_order_acquire) > 0",
            native)
        self.assertIn(
            "waveformUpdateDepth_.fetch_add(1", native)


if __name__ == "__main__":
    unittest.main()
