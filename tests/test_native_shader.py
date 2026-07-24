"""Source-level contracts for the runtime-compiled Metal waveform shader."""

from pathlib import Path
import unittest


class NativeWaveformShader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (
            Path(__file__).parents[1] / "native" / "SoundboxDeck.mm"
        ).read_text()
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
        self.assertIn("log2(max(1.0, sampleFootprint))", helper)
        self.assertIn("const uint lowLevel", helper)
        self.assertIn("const uint highLevel", helper)
        self.assertIn("const float lodMix", helper)
        self.assertIn("result.coverage = mix(", helper)
        self.assertIn("result.weightedColor = mix(", helper)
        self.assertIn("result.colorWeight = mix(", helper)

    def test_audio_detail_uses_the_same_continuous_lod_rule(self):
        helper = self.shader.split(
            "inline float filtered_envelope_coverage", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertIn("log2(max(1.0, sampleFootprint))", helper)
        self.assertIn("const uint lowLevel", helper)
        self.assertIn("const uint highLevel", helper)
        self.assertIn("return mix(lowCoverage, highCoverage, lodMix)", helper)

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
            self.assertIn(
                "const float tapOffsets[4] = "
                "{-0.375, -0.125, 0.125, 0.375}", helper)
            self.assertIn("for (uint tap = 0; tap < 4; ++tap)", helper)
            self.assertIn(
                "baseLocal + tapOffsets[tap] * sampleFootprint", helper)
            self.assertIn("* 0.25", helper)
        filtered = self.shader.split(
            "inline WaveSurface filtered_wave_surface", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertGreaterEqual(
            filtered.count("integrated_wave_level("), 2)
        self.assertGreaterEqual(
            filtered.count("integrated_envelope_level("), 2)

    def test_subpixel_color_is_amplitude_weighted_before_compositing(self):
        wave = self.shader.split(
            "inline WaveSurface integrated_wave_level", 1
        )[1].split(
            "inline float integrated_envelope_level", 1
        )[0]
        self.assertIn("pair.colorA * pair.heightA", wave)
        self.assertIn("pair.colorB * pair.heightB", wave)
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertGreaterEqual(
            fragment.count("/ max("), 2)

    def test_all_silhouettes_filter_final_coverage(self):
        self.assertEqual(self.shader.count("inline float pair_coverage"), 1)
        self.assertGreaterEqual(self.shader.count("pair_coverage("), 3)
        self.assertNotIn(".height = mix(", self.shader)

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
            self.shader.count("vivid_wave_coverage("), 3)

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
            "inline EnvelopePair envelope_pair", 1
        )[1].split(
            "inline float pair_coverage", 1
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

    def test_scrolling_lane_is_rasterized_once_at_the_final_pixel(self):
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
        self.assertIn("displayLatestTarget_ <= now", hold)
        self.assertIn("displayPreviousTarget_ <= now", hold)
        self.assertIn("kFreshPresentationWindow", hold)
        self.assertIn("frame = displayLatestFrame_", hold)
        self.assertIn("frame = visualFrameNow()", hold)

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
