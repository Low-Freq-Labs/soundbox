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
        self.assertIn("result.color = mix(", helper)

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

    def test_all_silhouettes_filter_final_coverage(self):
        self.assertEqual(self.shader.count("inline float pair_coverage"), 1)
        self.assertGreaterEqual(self.shader.count("pair_coverage("), 5)
        self.assertNotIn(".height = mix(", self.shader)

    def test_composite_is_opaque_without_an_outer_ghost(self):
        fragment = self.shader.split(
            "fragment float4 wave_surface_fragment", 1
        )[1].split(
            "vertex Out grid_vertex", 1
        )[0]
        self.assertGreaterEqual(fragment.count(", 1.0);"), 2)
        self.assertNotIn("bodyCoverage", fragment)
        self.assertNotIn("outerAlpha", fragment)
        self.assertNotIn("0.42", fragment)

    def test_thin_waveform_color_is_vivid_without_expanding_height(self):
        helper = self.shader.split(
            "inline float vivid_wave_coverage", 1
        )[1].split(
            "vertex Out wave_surface_vertex", 1
        )[0]
        self.assertIn("max(coverage, 0.72)", helper)
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


if __name__ == "__main__":
    unittest.main()
