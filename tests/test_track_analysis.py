import tempfile
import unittest
from pathlib import Path
import wave

import numpy as np

from soundbox import track_analysis


class TrackAnalysisStore(unittest.TestCase):
    def test_rgb_filter_coefficients_are_stable_rbj_butterworth_sections(self):
        low = track_analysis._rbj_biquad("lowpass", 100, 48_000)
        high = track_analysis._rbj_biquad("highpass", 1500, 48_000)
        np.testing.assert_allclose(low, [
            0.000042443368140232844,
            0.00008488673628046569,
            0.000042443368140232844,
            1.0,
            -1.981488509144573,
            0.9816582826171341,
        ], rtol=1e-12, atol=1e-14)
        np.testing.assert_allclose(high, [
            0.8703307793103346,
            -1.7406615586206693,
            0.8703307793103346,
            1.0,
            -1.7237761727625094,
            0.7575469444788291,
        ], rtol=1e-12, atol=1e-14)

    def test_rgb_transfer_and_compact_quantization_are_exact(self):
        energy = np.asarray([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 1.0, 1.0],
            [1.0, 0.5, 0.0],
        ])
        full = track_analysis._rgb_transfer(
            energy[:, 0], energy[:, 1], energy[:, 2])
        np.testing.assert_allclose(full, [
            [255.0, 0.0, 0.0],
            [0.0, 255.0, 0.0],
            [0.0, 0.0, 255.0],
            [255.0, 178.5, 255.0],
            [212.5, 59.5, 0.0],
        ])
        self.assertEqual(
            list(track_analysis._pack_rgb3(full, energy.max(axis=1))),
            [14, 0, 0, 0, 7, 0, 0, 0, 7, 14, 5, 7, 12, 1, 0],
        )
        # Active colors below one quantization step retain their tied hue.
        self.assertEqual(
            list(track_analysis._pack_rgb3(
                np.asarray([[2.0, 2.0, 1.0]]), np.asarray([1.0]))),
            [2, 1, 0],
        )

    def test_frequency_dependent_peak_holds_are_25_3_and_1_ms(self):
        rows = np.zeros((61, 3), dtype=np.float32)
        rows[30] = 1
        held = track_analysis._temporal_rgb_envelopes(rows, 1000.0)
        self.assertEqual(np.flatnonzero(held[:, 0]).tolist(),
                         list(range(18, 43)))
        self.assertEqual(np.flatnonzero(held[:, 1]).tolist(),
                         [29, 30, 31])
        self.assertEqual(np.flatnonzero(held[:, 2]).tolist(), [30])

    def test_rgb_pipeline_delay_clamps_to_first_valid_hue_at_zero(self):
        rows = np.ones((100, 3), dtype=np.float32)
        sampled = track_analysis._resample_rgb_envelopes(
            rows, 1000.0, 150.0, 12)
        np.testing.assert_array_equal(sampled, np.ones((12, 3)))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = self.root / "track.wav"
        rate = 48_000
        seconds = 2
        t = np.arange(rate * seconds, dtype=np.float32) / rate
        signal = (
            .45 * np.sin(2 * np.pi * 70 * t)
            + .25 * np.sin(2 * np.pi * 900 * t)
            + .15 * np.sin(2 * np.pi * 6000 * t))
        self.signal = signal
        pcm = np.clip(signal * 32767, -32768, 32767).astype("<i2")
        with wave.open(str(self.audio), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(pcm.tobytes())

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_is_source_keyed_and_checksummed(self):
        cache = self.root / "analysis"
        data = {
            "detail_heights": b"\x01\x02\x03",
            "detail_rate": 6144.0,
            "overview_heights": b"\x10\x80",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "spectral_bands": bytes(range(48)),
            "sample_rate": 150.0,
            "color_model": track_analysis.COLOR_MODEL,
            "beats": [[1, 125, 140.0]],
            "cues": [{"kind": "hot", "slot": 0, "position_ms": 125}],
        }
        self.assertIsNotNone(
            track_analysis.store(cache, "rb-42", self.audio, data))
        got = track_analysis.load(cache, "rb-42", self.audio)
        self.assertEqual(got["schema"], 4)
        self.assertEqual(got["color_model"], track_analysis.COLOR_MODEL)
        self.assertEqual(got["beats"], data["beats"])
        self.assertEqual(got["_audio_detail"]["heights"], b"\x01\x02\x03")
        self.assertEqual(got["colors"], [[1, 2, 3], [4, 5, 6]])
        self.audio.write_bytes(self.audio.read_bytes() + b"x")
        self.assertIsNone(
            track_analysis.load(cache, "rb-42", self.audio))

    def test_corrupt_section_is_a_clean_miss(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "x", self.audio, {
            "detail_heights": b"\x20" * 100,
            "detail_rate": 6144.0,
        })
        path = track_analysis.cache_path(cache, "x")
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 0x55
        path.write_bytes(damaged)
        self.assertIsNone(track_analysis.load(cache, "x", self.audio))

    def test_selective_sections_survive_unrelated_corruption(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "selective", self.audio, {
            "detail_heights": b"\x20" * 100,
            "detail_rate": 6144.0,
            "overview_heights": b"\x80" * 8,
            "overview_rgb": b"\x0e\x01\x02" * 8,
            "spectral_bands": b"\x01" * 24,
        })
        path = track_analysis.cache_path(cache, "selective")
        raw = bytearray(path.read_bytes())
        _magic, manifest_size = track_analysis._HEADER.unpack_from(raw)
        start = track_analysis._HEADER.size
        import json
        metadata = json.loads(raw[start:start + manifest_size])
        detail = metadata["sections"]["detail"]
        raw[start + manifest_size + detail["offset"]] ^= 0x55
        path.write_bytes(raw)
        self.assertIsNotNone(track_analysis.load_manifest(
            cache, "selective", self.audio))
        self.assertIsNotNone(track_analysis.load_overview(
            cache, "selective", self.audio))
        self.assertIsNone(track_analysis.load_detail(
            cache, "selective", self.audio))
        self.assertFalse(track_analysis.verify_complete(
            cache, "selective", self.audio))

    def test_legacy_black_audible_rows_inherit_nearest_valid_hue(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "repair", self.audio, {
            "detail_heights": b"\x20",
            "detail_rate": 6144.0,
            "overview_heights": b"\x80\x80\x00\x80",
            "overview_rgb": (
                b"\x00\x00\x00"
                b"\x00\x00\x00"
                b"\x00\x00\x00"
                b"\x0e\x02\x01"),
            "spectral_bands": b"",
        })
        overview = track_analysis.load_overview(
            cache, "repair", self.audio)
        self.assertEqual(
            overview["_overview_rgb"],
            b"\x0e\x02\x01\x0e\x02\x01\x00\x00\x00\x0e\x02\x01")

    def test_native_analysis_has_ultra_rgb_and_24_band_master(self):
        result = track_analysis.analyze_soundfile(self.audio, bpm_hint=120)
        self.assertIsNotNone(result)
        self.assertGreater(len(result["detail_heights"]), 10_000)
        self.assertGreater(result["detail_rate"], 5_500)
        self.assertGreater(len(result["overview_heights"]), 250)
        count = len(result["overview_heights"])
        self.assertEqual(len(result["overview_rgb"]), count * 3)
        self.assertEqual(len(result["spectral_bands"]), count * 24)
        self.assertTrue(any(result["overview_rgb"]))
        self.assertEqual(result["color_model"], track_analysis.COLOR_MODEL)
        self.assertGreaterEqual(len(result["beats"]), 3)
        self.assertAlmostEqual(result["beats"][0][2], 120, delta=1)

    def test_native_analysis_can_cancel_inside_the_current_track(self):
        calls = []

        def checkpoint():
            calls.append(1)
            return len(calls) < 2

        self.assertIsNone(track_analysis.analyze_soundfile(
            self.audio, checkpoint=checkpoint))
        self.assertEqual(len(calls), 2)

    def test_synthetic_tones_select_low_mid_and_high_color_paths(self):
        rate = 48_000
        t = np.arange(rate * 2, dtype=np.float32) / rate
        means = []
        for frequency in (60, 1000, 8000):
            analyzer = track_analysis.StreamingAnalyzer(rate)
            analyzer.feed(0.5 * np.sin(2 * np.pi * frequency * t))
            result = analyzer.finish()
            colors = np.frombuffer(
                result["overview_rgb"], dtype=np.uint8).reshape(-1, 3)
            normalized = colors[50:].astype(np.float32)
            normalized[:, 0] /= 2
            means.append(normalized.mean(axis=0))
        self.assertEqual(int(np.argmax(means[0])), 0)
        self.assertEqual(int(np.argmax(means[1])), 1)
        self.assertEqual(int(np.argmax(means[2])), 2)

    def test_streaming_chunk_boundaries_do_not_change_rgb_or_geometry(self):
        whole = track_analysis.StreamingAnalyzer(48_000)
        whole.feed(self.signal)
        expected = whole.finish(bpm_hint=120)

        chunked = track_analysis.StreamingAnalyzer(48_000)
        starts = range(0, len(self.signal), 7919)
        for start in starts:
            chunked.feed(self.signal[start:start + 7919])
        actual = chunked.finish(bpm_hint=120)
        self.assertEqual(actual["detail_heights"], expected["detail_heights"])
        self.assertEqual(
            actual["overview_heights"], expected["overview_heights"])
        self.assertEqual(actual["overview_rgb"], expected["overview_rgb"])
        self.assertEqual(actual["spectral_bands"], expected["spectral_bands"])

    def test_owned_chunks_preserve_the_exact_analysis_contract(self):
        copied = track_analysis.StreamingAnalyzer(48_000)
        copied.feed(self.signal)
        expected = copied.finish(bpm_hint=120)

        owned = track_analysis.StreamingAnalyzer(48_000)
        owned.feed(self.signal.copy(), owned=True)
        actual = owned.finish(bpm_hint=120)
        for field in (
                "detail_heights", "overview_heights", "overview_rgb",
                "spectral_bands", "beats"):
            self.assertEqual(actual[field], expected[field])

    def test_rgb_pipeline_does_not_change_ultra_silhouette_geometry(self):
        analyzer = track_analysis.StreamingAnalyzer(48_000)
        analyzer.feed(self.signal)
        result = analyzer.finish()
        window = round(48_000 / track_analysis.ULTRA_RATE)
        keep = len(self.signal) - len(self.signal) % window
        expected = np.abs(self.signal[:keep]).reshape(-1, window).max(axis=1)
        if keep < len(self.signal):
            expected = np.append(expected, np.abs(self.signal[keep:]).max())
        self.assertEqual(
            result["detail_heights"],
            track_analysis.StreamingAnalyzer._bytes(expected, 99.9),
        )

    def test_only_current_rgb_model_is_background_ready(self):
        cache = self.root / "analysis"
        base = {
            "detail_heights": b"\x20" * 100,
            "detail_rate": 6144.0,
            "overview_heights": b"\x80",
            "overview_rgb": b"\x0e\x00\x00",
        }
        track_analysis.store(cache, "old", self.audio, base)
        self.assertFalse(track_analysis.is_ready(
            cache, "old", self.audio))
        track_analysis.store(cache, "new", self.audio, {
            **base, "color_model": track_analysis.COLOR_MODEL,
        })
        self.assertTrue(track_analysis.is_ready(
            cache, "new", self.audio))

    def test_beat_grid_uses_numpy_only_and_recovers_pulse_tempo(self):
        rate = 150.0
        flux = np.zeros(int(rate * 12), dtype=np.float32)
        flux[23::75] = 1
        beats = track_analysis._beat_grid(flux, rate)
        self.assertGreater(len(beats), 20)
        self.assertEqual(beats[0][0], 1)
        self.assertEqual(beats[0][1], 0)
        self.assertAlmostEqual(beats[0][2], 120, delta=1)

    def test_soundbox_rgb_wins_while_rekordbox_grid_and_cues_seed_record(self):
        native = {
            "detail_heights": b"ultra",
            "detail_rate": 6144.0,
            "overview_heights": b"\x01",
            "overview_rgb": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "beats": [[1, 0, 100.0]],
        }
        imported = {
            "heights": [.5],
            "colors": [[10, 20, 30]],
            "beats": [[1, 250, 128.0]],
            "cues": [{"kind": "memory", "slot": 0, "position_ms": 500}],
            "sample_rate": 150.0,
        }
        got = track_analysis.merge_import(native, imported)
        self.assertEqual(got["detail_heights"], b"ultra")
        self.assertEqual(got["beats"], imported["beats"])
        self.assertEqual(got["overview_rgb"], bytes([1, 2, 3]))
        self.assertEqual(got["provenance"]["grid"], "rekordbox")
        self.assertFalse(got["provenance"]["rekordbox_runtime_dependency"])

    def test_legacy_grid_metadata_migrates_without_rewriting_waveform(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "legacy-grid", self.audio, {
            "detail_heights": b"\x10\x20\x30",
            "detail_rate": 6144.0,
            "overview_heights": b"\x20",
            "overview_rgb": b"\x0e\x00\x00",
            "color_model": track_analysis.COLOR_MODEL,
            "beats": [
                [1, 375, 120.0],
                [2, 875, 120.0],
                [3, 1375, 120.0],
            ],
            "provenance": {"analysis": "soundbox", "owner": "soundbox"},
        })
        before = track_analysis.load(cache, "legacy-grid", self.audio)
        self.assertTrue(track_analysis.grid_needs_sync(
            cache, "legacy-grid", self.audio))
        self.assertTrue(track_analysis.update_grid_source(
            cache, "legacy-grid", self.audio, []))
        after = track_analysis.load(cache, "legacy-grid", self.audio)
        self.assertEqual(after["beats"], [
            [1, 0, 120.0],
            [2, 500, 120.0],
            [3, 1000, 120.0],
        ])
        self.assertEqual(
            after["_audio_detail"]["heights"],
            before["_audio_detail"]["heights"])
        self.assertEqual(
            after["provenance"]["grid"], "soundbox-zero-anchor")
        self.assertFalse(track_analysis.grid_needs_sync(
            cache, "legacy-grid", self.audio))

    def test_grid_metadata_migration_uses_exact_rekordbox_rows(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "rb-grid", self.audio, {
            "detail_heights": b"\x10",
            "detail_rate": 6144.0,
            "beats": [[1, 500, 120.0]],
            "provenance": {"analysis": "soundbox", "owner": "soundbox"},
        })
        imported = [[1, 246, 128.0], [2, 715, 128.1]]
        self.assertTrue(track_analysis.update_grid_source(
            cache, "rb-grid", self.audio, imported))
        got = track_analysis.load(cache, "rb-grid", self.audio)
        self.assertEqual(got["beats"], imported)
        self.assertEqual(got["provenance"]["grid"], "rekordbox")

    def test_user_grid_and_cues_override_import_without_touching_analysis(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "editable", self.audio, {
            "detail_heights": b"\x10\x20",
            "detail_rate": 6144.0,
            "beats": [[1, 100, 120.0]],
            "cues": [],
        })
        cues = [{"kind": "hot", "slot": 0, "position_ms": 500}]
        self.assertTrue(track_analysis.update_user_data(
            cache, "editable", self.audio,
            beats=[[1, 250, 128.0]], cues=cues))
        got = track_analysis.load(cache, "editable", self.audio)
        self.assertEqual(got["beats"], [[1, 250, 128.0]])
        self.assertEqual(got["cues"], cues)
        self.assertEqual(got["_audio_detail"]["heights"], b"\x10\x20")
        self.assertEqual(got["provenance"]["grid"], "soundbox-manual")
        self.assertEqual(got["provenance"]["user_data"], "soundbox")

    def test_analyzer_upgrade_preserves_soundbox_owned_grid_and_cues(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "owned", self.audio, {
            "detail_heights": b"old",
            "detail_rate": 6144.0,
            "overview_heights": b"\x20",
            "overview_rgb": b"\x0e\x00\x00",
        })
        user_beats = [[1, 375, 128.0]]
        user_cues = [{"kind": "hot", "slot": 2, "position_ms": 750}]
        self.assertTrue(track_analysis.update_user_data(
            cache, "owned", self.audio,
            beats=user_beats, cues=user_cues))
        track_analysis.store(cache, "owned", self.audio, {
            "detail_heights": b"new-analysis",
            "detail_rate": 6144.0,
            "overview_heights": b"\x80",
            "overview_rgb": b"\x00\x00\x07",
            "color_model": track_analysis.COLOR_MODEL,
            "beats": [[1, 0, 100.0]],
            "cues": [],
        })
        got = track_analysis.load(cache, "owned", self.audio)
        self.assertEqual(got["_audio_detail"]["heights"], b"new-analysis")
        self.assertEqual(got["beats"], user_beats)
        self.assertEqual(got["cues"], user_cues)
        self.assertEqual(got["provenance"]["grid"], "soundbox-manual")
        self.assertEqual(got["provenance"]["user_data"], "soundbox")

    def test_imported_cues_sync_manifest_without_touching_payload(self):
        cache = self.root / "analysis"
        initial = [{"kind": "hot", "slot": 0, "position_ms": 100}]
        data = track_analysis.with_imported_cues({
            "detail_heights": b"\x10\x20",
            "detail_rate": 6144.0,
            "spectral_bands": bytes(range(24)),
        }, initial)
        track_analysis.store(cache, "sync", self.audio, data)
        before = track_analysis._read_container_manifest(
            cache, "sync", self.audio)
        self.assertIsNotNone(before)
        before_payload = before[2]
        changed = [{"kind": "memory", "slot": 0, "position_ms": 250}]
        self.assertTrue(track_analysis.imported_cues_need_sync(
            cache, "sync", self.audio, changed))
        self.assertTrue(track_analysis.update_imported_cues(
            cache, "sync", self.audio, changed))
        after = track_analysis._read_container_manifest(
            cache, "sync", self.audio)
        self.assertEqual(after[2], before_payload)
        got = track_analysis.load(cache, "sync", self.audio)
        self.assertEqual(got["cues"], changed)
        self.assertEqual(got["cue_import"]["source"], "rekordbox-db")
        self.assertFalse(track_analysis.imported_cues_need_sync(
            cache, "sync", self.audio, changed))

    def test_authoritative_empty_cues_clear_import_but_not_user_cues(self):
        cache = self.root / "analysis"
        imported = [{"kind": "hot", "slot": 0, "position_ms": 100}]
        track_analysis.store(
            cache, "clear", self.audio,
            track_analysis.with_imported_cues({
                "detail_heights": b"\x10",
                "detail_rate": 6144.0,
            }, imported))
        self.assertTrue(track_analysis.update_imported_cues(
            cache, "clear", self.audio, []))
        self.assertEqual(
            track_analysis.load(cache, "clear", self.audio)["cues"], [])
        user = [{"kind": "hot", "slot": 1, "position_ms": 200}]
        self.assertTrue(track_analysis.update_user_data(
            cache, "clear", self.audio, cues=user))
        self.assertFalse(track_analysis.imported_cues_need_sync(
            cache, "clear", self.audio, []))
        self.assertFalse(track_analysis.update_imported_cues(
            cache, "clear", self.audio, []))
        self.assertEqual(
            track_analysis.load(cache, "clear", self.audio)["cues"], user)

    def test_export_allowlist_excludes_player_only_set_rips(self):
        for suffix in ("mp3", "wav", "aif", "aiff", "flac"):
            self.assertTrue(
                track_analysis.export_eligibility(f"x.{suffix}")["eligible"])
        for suffix in ("m4a", "aac", "alac", "mp4"):
            result = track_analysis.export_eligibility(f"x.{suffix}")
            self.assertFalse(result["eligible"])
            self.assertIn("Player only", result["reason"])
