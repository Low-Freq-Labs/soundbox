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

    def test_audio_mips_render_one_peak_preserving_silhouette(self):
        block = self.shader.split(
            "if (u.audioDetailSampleCount > 0", 1
        )[1].split(
            "if (u.pixelsPerSecond > 110.0", 1
        )[0]
        self.assertIn("filter_envelope_pixel(", block)
        self.assertIn("composite_envelope(", block)
        self.assertNotIn(".height = mix(", block)
        self.assertIn("songSeconds * u.detailSamplesPerSecond", block)

    def test_every_scrolling_waveform_path_shares_coverage_filter(self):
        # Definition + audio-detail mip + PWV fallback + stable raster.
        self.assertGreaterEqual(self.shader.count("filter_wave_pair("), 4)

    def test_minified_audio_chooses_one_output_safe_mip(self):
        audio = self.shader.split(
            "if (u.audioDetailSampleCount > 0", 1
        )[1].split(
            "if (u.pixelsPerSecond > 110.0", 1
        )[0]
        self.assertIn(
            "ceil(log2(max(1.0, sampleFootprint / 1.1)))", audio)
        self.assertNotIn("for (uint pass = 0; pass < 2;", audio)
        self.assertIn("filter_envelope_pixel(", audio)

    def test_selected_mip_is_filtered_across_one_physical_pixel(self):
        helper = self.shader.split(
            "inline FilteredEnvelope filter_envelope_pixel", 1
        )[1].split(
            "inline float4 composite_envelope", 1
        )[0]
        self.assertIn(
            "const float offsets[4] = {-0.375, -0.125, 0.125, 0.375};",
            helper)
        self.assertIn("offsets[tap] * footprint", helper)

    def test_composite_has_no_translucent_outer_ghost(self):
        helper = self.shader.split(
            "inline float4 composite_envelope", 1
        )[1].split(
            "fragment float4 wave_surface_fragment", 1
        )[0]
        self.assertIn("vivid_wave_coverage(filtered.coverage)", helper)
        self.assertNotIn("bodyCoverage", helper)
        self.assertNotIn("outerAlpha", helper)
        self.assertNotIn("0.42", helper)

    def test_thin_waveform_color_is_vivid_without_expanding_height(self):
        helper = self.shader.split(
            "inline float vivid_wave_coverage", 1
        )[1].split(
            "inline FilteredWave filter_wave_pair", 1
        )[0]
        self.assertIn("max(coverage, 0.72)", helper)
        self.assertNotIn(".height", helper)
        self.assertGreaterEqual(
            self.shader.count("vivid_wave_coverage("), 5)

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
        audio = self.shader.split(
            "if (u.audioDetailSampleCount > 0", 1
        )[1].split(
            "if (u.pixelsPerSecond > 110.0", 1
        )[0]
        self.assertIn("u.audioDetailCurrentIndex / divisor", audio)
        self.assertIn("u.audioDetailCurrentPhase", audio)
        self.assertNotIn(
            "songSeconds * u.audioDetailSamplesPerSecond", audio)

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
