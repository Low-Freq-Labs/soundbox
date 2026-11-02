import tempfile
import unittest
from pathlib import Path
import wave
import hashlib
import json
import os
from types import SimpleNamespace
from unittest import mock

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

    def test_compact_rgb_packs_directly_into_all_pwv5_channels(self):
        packed = track_analysis._pack_rgb_detail(
            np.ones(4),
            np.asarray([
                [14, 0, 0],
                [0, 7, 0],
                [0, 0, 7],
                [14, 5, 7],
            ], dtype=np.uint8),
        )
        self.assertEqual(packed, bytes.fromhex("e07c 1c7c 03fc f7fc"))

    def test_frequency_dependent_peak_holds_are_25_3_and_1_ms(self):
        rows = np.zeros((61, 3), dtype=np.float32)
        rows[30] = 1
        held = track_analysis._temporal_rgb_envelopes(rows, 1000.0)
        self.assertEqual(np.flatnonzero(held[:, 0]).tolist(),
                         list(range(18, 43)))
        self.assertEqual(np.flatnonzero(held[:, 1]).tolist(),
                         [29, 30, 31])
        self.assertEqual(np.flatnonzero(held[:, 2]).tolist(), [30])

    def test_rgb_pipeline_delay_is_explicit_and_clamps_at_zero(self):
        rows = np.ones((100, 3), dtype=np.float32)
        sampled = track_analysis._resample_rgb_envelopes(
            rows, 1000.0, 150.0, 12,
            track_analysis.MP3_COLOR_PIPELINE_DELAY)
        np.testing.assert_array_equal(sampled, np.ones((12, 3)))

    def test_only_mp3_keeps_the_proven_decoder_timeline_correction(self):
        for suffix in (".wav", ".aif", ".aiff", ".flac", ".aac", ".m4a"):
            self.assertEqual(
                track_analysis._color_pipeline_delay(f"x{suffix}"), 0.0)
        self.assertEqual(
            track_analysis._color_pipeline_delay("x.MP3"),
            track_analysis.MP3_COLOR_PIPELINE_DELAY)

    def test_mp3_timeline_correction_is_exactly_seven_rgb_rows(self):
        rows = np.arange(1000, dtype=np.float32)
        envelopes = np.stack((rows, rows, rows), axis=1)
        lossless = track_analysis._resample_rgb_envelopes(
            envelopes, 1000.0, 150.0, 20)
        mp3 = track_analysis._resample_rgb_envelopes(
            envelopes, 1000.0, 150.0, 20,
            track_analysis.MP3_COLOR_PIPELINE_DELAY)
        np.testing.assert_allclose(lossless[:-7], mp3[7:])

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

    def _write_v4(self, cache: Path, track_id: str, data: dict) -> Path:
        source = self.audio
        sections = {
            "detail": bytes(data["detail_heights"]),
            "detail_lower": bytes(data["detail_lower_heights"]),
            "detail_minimum": bytes(data["detail_minimum_values"]),
            "detail_maximum": bytes(data["detail_maximum_values"]),
            "overview_heights": bytes(data["overview_heights"]),
            "overview_rgb": bytes(data["overview_rgb"]),
            "spectral_24": bytes(data["spectral_bands"]),
            "blue_detail": bytes(data["blue_detail"]),
            "rgb_detail": bytes(data["rgb_detail"]),
            "three_band": bytes(data["three_band"]),
        }
        section_rows, payload = track_analysis._encode_sections(sections)
        stat = source.stat()
        manifest = {
            "schema": track_analysis.LEGACY_SCHEMA,
            "track_id": str(track_id),
            "source": {
                "path": str(source.resolve()),
                "fingerprint": track_analysis.audio_fingerprint(source),
                "identity": f"{stat.st_size}:{stat.st_mtime_ns}",
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            },
            "provenance": dict(data["provenance"]),
            "stream_provenance": dict(data["stream_provenance"]),
            "detail_rate": float(data["detail_rate"]),
            "color_rate": float(data["sample_rate"]),
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.LEGACY_WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "display_render_model": track_analysis.DISPLAY_RENDER_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
            "range_encoding": track_analysis.RANGE_ENCODING,
            "beats": list(data["beats"]),
            "cues": [{"kind": "hot", "slot": 2, "position_ms": 500}],
            "grid_import": {},
            "cue_import": {},
            "sections": section_rows,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        }
        encoded = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True).encode()
        cache.mkdir(parents=True, exist_ok=True)
        target = track_analysis.legacy_cache_path(cache, track_id)
        target.write_bytes(
            track_analysis._HEADER.pack(
                track_analysis._LEGACY_MAGIC, len(encoded))
            + encoded + payload)
        return target

    def test_round_trip_is_source_keyed_and_checksummed(self):
        cache = self.root / "analysis"
        data = {
            "detail_heights": b"\x01\x02\x03",
            "detail_rate": 6144.0,
            "overview_heights": b"\x10\x80",
            "overview_rgb": b"\x01\x02\x03\x04\x05\x06",
            "spectral_bands": bytes(range(48)),
            "blue_detail": b"\x21\x42",
            "rgb_detail": b"\x12\x34\x56\x78",
            "three_band": b"\x01\x02\x03\x04\x05\x06",
            "sample_rate": 150.0,
            "color_model": track_analysis.COLOR_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "display_render_model":
                track_analysis.DISPLAY_RENDER_MODEL,
            "beats": [[1, 125, 140.0]],
            "cues": [{"kind": "hot", "slot": 0, "position_ms": 125}],
        }
        self.assertIsNotNone(
            track_analysis.store(cache, "rb-42", self.audio, data))
        got = track_analysis.load(cache, "rb-42", self.audio)
        self.assertEqual(got["schema"], 5)
        self.assertEqual(got["color_model"], track_analysis.COLOR_MODEL)
        self.assertEqual(got["beats"], data["beats"])
        self.assertEqual(
            got["_audio_detail"]["encoding"],
            track_analysis.RANGE_MIP_ENCODING)
        self.assertEqual(
            got["_audio_detail"]["mip_divisors"],
            list(track_analysis.RANGE_MIP_DIVISORS))
        self.assertEqual(got["_detail_bands"], data["three_band"])
        self.assertEqual(got["_blue_detail"], data["blue_detail"])
        self.assertEqual(got["_rgb_detail"], data["rgb_detail"])
        self.assertEqual(
            track_analysis.load_manifest(
                cache, "rb-42", self.audio)["display_render_model"],
            track_analysis.DISPLAY_RENDER_MODEL)
        self.assertEqual(got["colors"], [[1, 2, 3], [4, 5, 6]])
        self.audio.write_bytes(self.audio.read_bytes() + b"x")
        self.assertIsNone(
            track_analysis.load(cache, "rb-42", self.audio))

    def test_timestamp_only_drift_adopts_identity_without_rewriting_cache(self):
        cache = self.root / "analysis"
        data = track_analysis.analyze_soundfile(self.audio, bpm_hint=120)
        self.assertIsNotNone(data)
        target = track_analysis.store(cache, "mtime", self.audio, data)
        self.assertIsNotNone(target)
        before = target.read_bytes()
        cache_mtime = target.stat().st_mtime_ns
        stat = self.audio.stat()
        os.utime(self.audio, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

        self.assertFalse(track_analysis.is_ready(
            cache, "mtime", self.audio))
        self.assertTrue(track_analysis.adopt_source_identity(
            cache, "mtime", self.audio))
        self.assertTrue(track_analysis.is_ready(
            cache, "mtime", self.audio))
        self.assertIsNotNone(track_analysis.load(
            cache, "mtime", self.audio))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(target.stat().st_mtime_ns, cache_mtime)

    def test_changed_boundary_bytes_never_adopt_new_source_identity(self):
        cache = self.root / "analysis"
        data = track_analysis.analyze_soundfile(self.audio, bpm_hint=120)
        self.assertIsNotNone(data)
        self.assertIsNotNone(
            track_analysis.store(cache, "changed", self.audio, data))
        raw = bytearray(self.audio.read_bytes())
        raw[0] ^= 0x01
        self.audio.write_bytes(raw)

        self.assertFalse(track_analysis.adopt_source_identity(
            cache, "changed", self.audio))
        self.assertFalse(track_analysis.is_ready(
            cache, "changed", self.audio))

    def test_dataless_placeholder_reuses_matching_size_without_hydration(self):
        manifest = {"source": {"identity": "5:1", "size": 5}}
        stat = SimpleNamespace(
            st_size=5, st_mtime_ns=2,
            st_flags=track_analysis._SF_DATALESS)
        with mock.patch(
                "soundbox.track_analysis._get_identity_xattr") as getxattr:
            self.assertTrue(track_analysis._source_identity_matches(
                Path("cache.sba5"), manifest, stat))
        getxattr.assert_not_called()

    def test_dataless_source_probe_is_stat_only_and_fails_closed(self):
        stat = SimpleNamespace(st_flags=track_analysis._SF_DATALESS)
        with mock.patch.object(Path, "stat", return_value=stat):
            self.assertTrue(track_analysis.is_dataless_source("cloud.wav"))
        with mock.patch.object(Path, "stat", side_effect=OSError):
            with self.assertRaises(OSError):
                track_analysis.is_dataless_source("missing.wav")

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

    def test_discard_records_removes_only_exact_v4_v5_cache_files(self):
        cache = self.root / "analysis"
        cache.mkdir()
        current = track_analysis.cache_path(cache, "grave")
        legacy = track_analysis.legacy_cache_path(cache, "grave")
        keep = track_analysis.cache_path(cache, "active")
        current.write_bytes(b"v5-cache")
        legacy.write_bytes(b"old")
        keep.write_bytes(b"keep")

        removed, reclaimed = track_analysis.discard_records(
            cache, ("grave", "grave", "missing"))

        self.assertEqual((removed, reclaimed), (2, 11))
        self.assertFalse(current.exists())
        self.assertFalse(legacy.exists())
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(
            track_analysis.discard_records(cache, ("grave",)), (0, 0))

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
        detail = metadata["sections"]["detail_minimum_d8"]
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
        self.assertEqual(
            result["waveform_model"], track_analysis.WAVEFORM_MODEL)
        self.assertEqual(
            result["height_encoding"], track_analysis.HEIGHT_ENCODING)
        self.assertEqual(
            result["range_encoding"], track_analysis.RANGE_ENCODING)
        self.assertEqual(
            len(result["detail_minimum_values"]),
            len(result["detail_heights"]))
        self.assertEqual(
            len(result["detail_maximum_values"]),
            len(result["detail_heights"]))
        count = len(result["overview_heights"]) // 2
        self.assertEqual(len(result["overview_rgb"]), count * 3)
        self.assertEqual(len(result["spectral_bands"]), count * 24)
        self.assertEqual(len(result["three_band"]), count * 3)
        self.assertEqual(len(result["blue_detail"]), count)
        self.assertEqual(len(result["rgb_detail"]), count * 2)
        self.assertEqual(
            result["three_band_model"], track_analysis.THREE_BAND_MODEL)
        self.assertEqual(
            result["display_waveform_model"],
            track_analysis.DISPLAY_WAVEFORM_MODEL)
        self.assertEqual(
            result["display_render_model"],
            track_analysis.DISPLAY_RENDER_MODEL)
        self.assertTrue(any(result["overview_rgb"]))
        self.assertEqual(result["color_model"], track_analysis.COLOR_MODEL)
        self.assertGreaterEqual(len(result["beats"]), 3)
        self.assertAlmostEqual(result["beats"][0][2], 120, delta=1)

    def test_v5_mips_are_exact_song_anchored_extrema_and_bounded(self):
        minimum = np.asarray(
            [-8, -2, -7, 0, -1, -9, -3, -4, -6], dtype="<i2")
        maximum = np.asarray(
            [1, 7, 2, 6, 3, 4, 9, 5, 8], dtype="<i2")
        sections, count = track_analysis._range_mip_sections(
            minimum.tobytes(), maximum.tobytes(), (8, 13))
        self.assertEqual(count, 9)
        np.testing.assert_array_equal(
            np.frombuffer(sections["detail_minimum_d8"], dtype="<i2"),
            [-9, -6])
        np.testing.assert_array_equal(
            np.frombuffer(sections["detail_maximum_d8"], dtype="<i2"),
            [9, 8])
        np.testing.assert_array_equal(
            np.frombuffer(sections["detail_minimum_d13"], dtype="<i2"),
            [-9])
        np.testing.assert_array_equal(
            np.frombuffer(sections["detail_maximum_d13"], dtype="<i2"),
            [9])
        one_hour = track_analysis.projected_v5_bytes(3600)
        self.assertLess(one_hour, 64 * 1024 ** 2)

    def test_v4_migration_matches_direct_v5_and_retires_only_after_audit(self):
        data = track_analysis.analyze_soundfile(self.audio, bpm_hint=120)
        self.assertIsNotNone(data)
        migrated_cache = self.root / "migrated"
        direct_cache = self.root / "direct"
        legacy = self._write_v4(migrated_cache, "same", data)
        self.assertTrue(legacy.is_file())
        self.assertFalse(track_analysis.is_ready(
            migrated_cache, "same", self.audio))

        self.assertTrue(track_analysis.migrate_v4(
            migrated_cache, "same", self.audio))
        self.assertFalse(legacy.exists())
        self.assertTrue(track_analysis.is_ready(
            migrated_cache, "same", self.audio))
        self.assertTrue(track_analysis.verify_complete(
            migrated_cache, "same", self.audio))

        self.assertIsNotNone(track_analysis.store(
            direct_cache, "same", self.audio, data))
        migrated = track_analysis.load_detail(
            migrated_cache, "same", self.audio)
        direct = track_analysis.load_detail(
            direct_cache, "same", self.audio)
        self.assertEqual(
            migrated["_audio_detail"]["range_min_mips"],
            direct["_audio_detail"]["range_min_mips"])
        self.assertEqual(
            migrated["_audio_detail"]["range_max_mips"],
            direct["_audio_detail"]["range_max_mips"])
        migrated_manifest = track_analysis.load_manifest(
            migrated_cache, "same", self.audio)
        self.assertEqual(
            migrated_manifest["cues"],
            [{"kind": "hot", "slot": 2, "position_ms": 500}])

    def test_corrupt_v5_never_authorizes_legacy_retirement(self):
        data = track_analysis.analyze_soundfile(self.audio, bpm_hint=120)
        cache = self.root / "retirement"
        legacy = self._write_v4(cache, "guarded", data)
        self.assertIsNotNone(track_analysis.store(
            cache, "guarded", self.audio, data))
        current = track_analysis.cache_path(cache, "guarded")
        damaged = bytearray(current.read_bytes())
        damaged[-1] ^= 0x40
        current.write_bytes(damaged)
        self.assertFalse(track_analysis.retire_legacy_record(
            cache, "guarded", self.audio))
        self.assertTrue(legacy.exists())

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
            actual["detail_lower_heights"],
            expected["detail_lower_heights"])
        self.assertEqual(
            actual["detail_minimum_values"],
            expected["detail_minimum_values"])
        self.assertEqual(
            actual["detail_maximum_values"],
            expected["detail_maximum_values"])
        self.assertEqual(
            actual["overview_heights"], expected["overview_heights"])
        self.assertEqual(actual["overview_rgb"], expected["overview_rgb"])
        self.assertEqual(actual["spectral_bands"], expected["spectral_bands"])
        self.assertEqual(actual["three_band"], expected["three_band"])

    def test_owned_chunks_preserve_the_exact_analysis_contract(self):
        copied = track_analysis.StreamingAnalyzer(48_000)
        copied.feed(self.signal)
        expected = copied.finish(bpm_hint=120)

        owned = track_analysis.StreamingAnalyzer(48_000)
        owned.feed(self.signal.copy(), owned=True)
        actual = owned.finish(bpm_hint=120)
        for field in (
                "detail_heights", "detail_lower_heights",
                "detail_minimum_values", "detail_maximum_values",
                "overview_heights", "overview_rgb",
                "spectral_bands", "three_band", "beats"):
            self.assertEqual(actual[field], expected[field])

    def test_rgb_pipeline_does_not_change_ultra_silhouette_geometry(self):
        analyzer = track_analysis.StreamingAnalyzer(48_000)
        analyzer.feed(self.signal)
        result = analyzer.finish()
        window = round(48_000 / track_analysis.ULTRA_RATE)
        keep = len(self.signal) - len(self.signal) % window
        upper = np.maximum(self.signal[:keep], 0).reshape(
            -1, window).max(axis=1)
        lower = np.maximum(-self.signal[:keep], 0).reshape(
            -1, window).max(axis=1)
        signed_rows = self.signal[:keep].reshape(-1, window)
        minimum = signed_rows.min(axis=1)
        maximum = signed_rows.max(axis=1)
        if keep < len(self.signal):
            upper = np.append(
                upper, np.maximum(self.signal[keep:], 0).max())
            lower = np.append(
                lower, np.maximum(-self.signal[keep:], 0).max())
            minimum = np.append(minimum, self.signal[keep:].min())
            maximum = np.append(maximum, self.signal[keep:].max())
        expected_upper, expected_lower = (
            track_analysis.StreamingAnalyzer._u16_polarity_bytes(
                upper, lower, 99.9))
        self.assertEqual(
            result["detail_heights"], expected_upper)
        self.assertEqual(
            result["detail_lower_heights"], expected_lower)
        expected_minimum, expected_maximum = (
            track_analysis.StreamingAnalyzer._i16_range_bytes(
                minimum, maximum, 99.9))
        self.assertEqual(
            result["detail_minimum_values"], expected_minimum)
        self.assertEqual(
            result["detail_maximum_values"], expected_maximum)

    def test_ultra_geometry_preserves_independent_positive_and_negative_sides(
            self):
        signal = np.concatenate((
            np.full(16, 0.75, dtype=np.float32),
            np.full(16, -0.25, dtype=np.float32),
        ))
        analyzer = track_analysis.StreamingAnalyzer(48_000)
        analyzer.feed(signal)
        result = analyzer.finish()
        upper = np.frombuffer(result["detail_heights"], dtype="<u2")
        lower = np.frombuffer(result["detail_lower_heights"], dtype="<u2")
        self.assertEqual(len(upper), len(lower))
        self.assertFalse(np.array_equal(upper, lower))
        self.assertGreater(int(upper.max()), int(lower.max()))

    def test_overview_uses_complete_nonoverlapping_color_clock_cells(self):
        rate = 48_000
        hop = round(rate / track_analysis.COLOR_RATE)
        signal = np.zeros(hop * 2, dtype=np.float32)
        signal[0] = 0.5
        signal[hop + 10] = 1.0
        analyzer = track_analysis.StreamingAnalyzer(rate)
        analyzer.feed(signal)
        result = analyzer.finish()
        overview = np.frombuffer(
            result["overview_heights"], dtype="<u2")
        self.assertEqual(len(overview), 2)
        self.assertAlmostEqual(
            int(overview[0]) / int(overview[1]), 0.5, delta=0.01)

    def test_only_current_rgb_and_waveform_models_are_background_ready(self):
        cache = self.root / "analysis"
        base = {
            "detail_heights": b"\x20" * 100,
            "detail_rate": 6144.0,
            "overview_heights": b"\x80",
            "overview_rgb": b"\x0e\x00\x00",
            "blue_detail": b"\x21",
            "rgb_detail": b"\x12\x34",
            "three_band": b"\x01\x02\x03",
        }
        track_analysis.store(cache, "old", self.audio, base)
        self.assertFalse(track_analysis.is_ready(
            cache, "old", self.audio))
        track_analysis.store(cache, "new", self.audio, {
            **base,
            "overview_heights": b"\x80\x00",
            "color_model": track_analysis.COLOR_MODEL,
            "waveform_model": track_analysis.WAVEFORM_MODEL,
            "three_band_model": track_analysis.THREE_BAND_MODEL,
            "display_waveform_model":
                track_analysis.DISPLAY_WAVEFORM_MODEL,
            "height_encoding": track_analysis.HEIGHT_ENCODING,
        })
        self.assertTrue(track_analysis.is_ready(
            cache, "new", self.audio))

    def test_midpoint_geometry_keeps_a_channel_safe_antiphase_floor(self):
        rate = 48_000
        t = np.arange(rate, dtype=np.float32) / rate
        left = 0.25 * np.sin(2 * np.pi * 440 * t)
        stereo = np.stack((left, -left), axis=1)
        analyzer = track_analysis.StreamingAnalyzer(rate)
        analyzer.feed(stereo)
        result = analyzer.finish()
        detail = np.frombuffer(
            result["detail_heights"], dtype="<u2")
        overview = np.frombuffer(
            result["overview_heights"], dtype="<u2")
        self.assertGreater(int(detail.max()), 60_000)
        self.assertGreater(int(overview.max()), 60_000)

    def test_ordinary_stereo_geometry_follows_midpoint_not_louder_channel(self):
        rate = 48_000
        frames = rate
        left = np.ones(frames, dtype=np.float32)
        right = np.zeros(frames, dtype=np.float32)
        stereo = np.stack((left, right), axis=1)
        analyzer = track_analysis.StreamingAnalyzer(rate)
        analyzer.feed(stereo)
        result = analyzer.finish()
        detail = np.frombuffer(result["detail_heights"], dtype="<u2")
        # Normalization makes this constant test full-scale; compare against a
        # second full-midpoint region in one analysis to preserve the ratio.
        split = np.concatenate((
            stereo[:frames // 2],
            np.ones((frames // 2, 2), dtype=np.float32),
        ))
        analyzer = track_analysis.StreamingAnalyzer(rate)
        analyzer.feed(split)
        mixed = np.frombuffer(
            analyzer.finish()["detail_heights"], dtype="<u2")
        first = np.median(mixed[:len(mixed) // 2])
        second = np.median(mixed[len(mixed) // 2:])
        self.assertAlmostEqual(first / second, 0.5, delta=0.02)
        self.assertEqual(int(detail.max()), 65535)

    def test_stereo_midpoint_geometry_is_chunk_invariant(self):
        rate = 48_000
        t = np.arange(rate, dtype=np.float32) / rate
        left = 0.25 * np.sin(2 * np.pi * 440 * t)
        right = -0.18 * np.sin(2 * np.pi * 440 * t + 0.4)
        stereo = np.stack((left, right), axis=1)
        whole = track_analysis.StreamingAnalyzer(rate)
        whole.feed(stereo)
        expected = whole.finish()
        chunked = track_analysis.StreamingAnalyzer(rate)
        for start in range(0, len(stereo), 7919):
            chunked.feed(stereo[start:start + 7919])
        actual = chunked.finish()
        for field in (
                "detail_heights", "overview_heights", "overview_rgb",
                "spectral_bands", "blue_detail", "rgb_detail",
                "three_band", "beats"):
            self.assertEqual(actual[field], expected[field])

    def test_native_three_band_rows_use_measured_low_mid_high_order(self):
        rate = 48_000
        t = np.arange(rate * 2, dtype=np.float32) / rate
        strongest = []
        for frequency in (60, 1000, 8000):
            analyzer = track_analysis.StreamingAnalyzer(rate)
            analyzer.feed(0.5 * np.sin(2 * np.pi * frequency * t))
            rows = np.frombuffer(
                analyzer.finish()["three_band"],
                dtype=np.uint8).reshape(-1, 3)
            strongest.append(int(np.argmax(rows[50:].mean(axis=0))))
        self.assertEqual(strongest, [0, 1, 2])

    def test_u16_geometry_keeps_quiet_signal_that_a_byte_discards(self):
        peaks = np.asarray([1.0, 0.001, 0.0], dtype=np.float32)
        legacy = np.frombuffer(
            track_analysis.StreamingAnalyzer._bytes(peaks, 100),
            dtype=np.uint8)
        current = np.frombuffer(
            track_analysis.StreamingAnalyzer._u16_bytes(peaks, 100),
            dtype="<u2")
        self.assertEqual(int(legacy[1]), 0)
        self.assertGreater(int(current[1]), 0)
        self.assertEqual(int(current[2]), 0)

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
            "_detail_bands": b"\x11\x22\x33",
            "_blue_detail": b"\x9f",
            "_rgb_detail": b"\xe0\x7c",
        }
        got = track_analysis.merge_import(native, imported)
        self.assertEqual(got["detail_heights"], b"ultra")
        self.assertEqual(got["beats"], imported["beats"])
        self.assertEqual(got["overview_rgb"], bytes([1, 2, 3]))
        self.assertEqual(got["three_band"], imported["_detail_bands"])
        self.assertEqual(got["blue_detail"], imported["_blue_detail"])
        self.assertEqual(got["rgb_detail"], imported["_rgb_detail"])
        self.assertEqual(
            got["three_band_model"], track_analysis.THREE_BAND_MODEL)
        self.assertEqual(
            got["display_waveform_model"],
            track_analysis.DISPLAY_WAVEFORM_MODEL)
        self.assertEqual(
            got["display_render_model"],
            track_analysis.DISPLAY_RENDER_MODEL)
        self.assertEqual(
            got["stream_provenance"]["three_band"],
            "rekordbox-anlz-pwv7")
        self.assertEqual(got["provenance"]["grid"], "rekordbox")
        self.assertEqual(
            got["grid_import"]["fingerprint"],
            track_analysis.grid_fingerprint(imported["beats"]))
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
            after["_audio_detail"]["range_min_mips"],
            before["_audio_detail"]["range_min_mips"])
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
        self.assertEqual(
            got["grid_import"]["fingerprint"],
            track_analysis.grid_fingerprint(imported))

    def test_later_rekordbox_grid_edit_refreshes_only_manifest(self):
        cache = self.root / "analysis"
        automatic = [[1, 246, 128.0], [2, 715, 128.0]]
        track_analysis.store(cache, "corrected-grid", self.audio, {
            "detail_heights": b"\x10\x20",
            "detail_rate": 6144.0,
            "overview_heights": b"\x30",
            "overview_rgb": b"\x01\x02\x03",
            "color_model": track_analysis.COLOR_MODEL,
            "beats": automatic,
            # Deliberately model an old V4 record with no grid_import field.
            "provenance": {
                "analysis": "rekordbox-import+soundbox",
                "grid": "rekordbox",
                "owner": "soundbox",
            },
        })
        before = track_analysis._read_container_manifest(
            cache, "corrected-grid", self.audio)
        self.assertIsNotNone(before)
        self.assertFalse(track_analysis.grid_needs_sync(
            cache, "corrected-grid", self.audio, automatic))

        corrected = [[1, 375, 128.0], [2, 844, 128.0]]
        self.assertTrue(track_analysis.grid_needs_sync(
            cache, "corrected-grid", self.audio, corrected))
        self.assertTrue(track_analysis.update_grid_source(
            cache, "corrected-grid", self.audio, corrected))

        after = track_analysis._read_container_manifest(
            cache, "corrected-grid", self.audio)
        self.assertEqual(after[2], before[2])
        got = track_analysis.load(cache, "corrected-grid", self.audio)
        self.assertEqual(got["beats"], corrected)
        self.assertEqual(
            got["grid_import"]["fingerprint"],
            track_analysis.grid_fingerprint(corrected))
        self.assertFalse(track_analysis.grid_needs_sync(
            cache, "corrected-grid", self.audio, corrected))

    def test_user_grid_and_cues_override_import_without_touching_analysis(self):
        cache = self.root / "analysis"
        track_analysis.store(cache, "editable", self.audio, {
            "detail_heights": b"\x10\x20",
            "detail_rate": 6144.0,
            "beats": [[1, 100, 120.0]],
            "cues": [],
        })
        before = track_analysis.load(cache, "editable", self.audio)
        cues = [{"kind": "hot", "slot": 0, "position_ms": 500}]
        self.assertTrue(track_analysis.update_user_data(
            cache, "editable", self.audio,
            beats=[[1, 250, 128.0]], cues=cues))
        got = track_analysis.load(cache, "editable", self.audio)
        self.assertEqual(got["beats"], [[1, 250, 128.0]])
        self.assertEqual(got["cues"], cues)
        self.assertEqual(
            got["_audio_detail"]["range_min_mips"],
            before["_audio_detail"]["range_min_mips"])
        self.assertEqual(got["provenance"]["grid"], "soundbox-manual")
        self.assertEqual(got["provenance"]["user_data"], "soundbox")
        self.assertFalse(got["grid_import"])

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
        before = track_analysis.load(cache, "owned", self.audio)
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
        self.assertNotEqual(
            got["_audio_detail"]["range_min_mips"],
            before["_audio_detail"]["range_min_mips"])
        self.assertEqual(got["beats"], user_beats)
        self.assertEqual(got["cues"], user_cues)
        self.assertEqual(got["provenance"]["grid"], "soundbox-manual")
        self.assertEqual(got["provenance"]["user_data"], "soundbox")

    def test_verified_native_grid_confirmation_preserves_every_waveform_payload_byte(self):
        cache=self.root/'analysis';raw=[[2,461,130.0],[3,923,130.0]];published=[[1,0,130.0],*raw]
        track_analysis.store(cache,'confirmed',self.audio,dict(detail_heights=b'wave',detail_rate=6144.0,
            overview_heights=b'\x20',overview_rgb=b'\x0e\x00\x00',beats=raw))
        before=track_analysis._read_container_manifest(cache,'confirmed',self.audio)[2]
        self.assertTrue(track_analysis.confirm_published_grid(cache,'confirmed',self.audio,published,raw))
        after=track_analysis._read_container_manifest(cache,'confirmed',self.audio)
        self.assertEqual(after[2],before)
        self.assertEqual(after[1]['beats'],published)
        self.assertEqual(after[1]['provenance']['grid'],'rekordbox')
        newer=[[1,100,130.0],[2,561,130.0]]
        self.assertTrue(track_analysis.update_user_data(cache,'confirmed',self.audio,beats=newer,cues=[]))
        exact=track_analysis._read_container_manifest(cache,'confirmed',self.audio)
        self.assertFalse(track_analysis.confirm_published_grid(cache,'confirmed',self.audio,published,raw))
        self.assertEqual(track_analysis._read_container_manifest(cache,'confirmed',self.audio),exact)

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
