"""Native CoreAudio/Metal deck contract and source-safety tests."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from PySide6.QtCore import QUrl
from desktop.native_deck import PlaybackState
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from desktop.native_deck import (
    NativeDeckBackend, _repair_pwv5_visibility,
    native_available, native_library_path,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WaveformStreamRepair(unittest.TestCase):
    def test_audible_zero_pwv5_rows_receive_only_a_minimum_color_carrier(self):
        packed = bytes.fromhex("e07c 0000 0000 0384")
        overview = struct.pack("<HHHH", 100, 20, 0, 30)
        repaired = _repair_pwv5_visibility(
            packed, overview, "linear-u16le")
        self.assertEqual(
            repaired,
            bytes.fromhex("e07c e004 0000 0384"))

    def test_repair_is_track_agnostic_and_preserves_true_silence(self):
        packed = bytes.fromhex("e07c 0000 0384")
        overview = struct.pack("<HHH", 100, 0, 30)
        self.assertEqual(
            _repair_pwv5_visibility(
                packed, overview, "linear-u16le"),
            packed)


class NativeDeckContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not native_available():
            raise unittest.SkipTest("arm64 macOS 14+ native deck unavailable")
        cls.temp = tempfile.TemporaryDirectory(prefix="sb-native-deck-")
        cls.audio = Path(cls.temp.name) / "contract.wav"
        rate = 44100
        with wave.open(str(cls.audio), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(b"".join(
                struct.pack(
                    "<hh",
                    int(0.15 * 32767 * math.sin(2 * math.pi * 220 * i / rate)),
                    int(0.15 * 32767 * math.sin(2 * math.pi * 330 * i / rate)),
                )
                for i in range(rate * 2)
            ))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.host = QWidget()
        self.host.resize(800, 100)
        self.host.show()
        self.deck = NativeDeckBackend(self.host)
        self.deck.set_gain(0.0)  # real output path, silent test

    def tearDown(self):
        self.deck.close()
        self.host.close()
        self.app.processEvents()

    def _wait_until(self, predicate, timeout_ms=2000):
        elapsed = 0
        while not predicate() and elapsed < timeout_ms:
            QTest.qWait(10)
            elapsed += 10
        self.assertTrue(predicate(), f"condition missed after {timeout_ms} ms")

    def test_library_exists_and_has_stable_abi(self):
        self.assertTrue(native_library_path().is_file())

    def test_load_play_seek_ramp_and_teardown_leave_source_untouched(self):
        before = _sha256(self.audio)
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: 1900 <= self.deck.duration() <= 2100)
        self.assertEqual(self.deck.playbackState(), PlaybackState.PAUSED)

        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        self._wait_until(lambda: self.deck.position() >= 40)
        self.deck.pause()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PAUSED)

        self.deck.setPosition(750)
        self._wait_until(lambda: 740 <= self.deck.position() <= 760)
        completed = []
        self.deck.play()
        self.deck.ramp_gain(0.0, 24, lambda: completed.append(True))
        self._wait_until(lambda: completed)
        self.deck.pause()

        self.assertEqual(errors, [])
        self.assertEqual(_sha256(self.audio), before)

    def test_pause_state_is_immediate_for_source_change_continuations(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        self.deck.pause()
        self.assertEqual(
            self.deck.playbackState(), PlaybackState.PAUSED)

    def test_terminal_edge_is_distinct_from_manual_pause(self):
        ended = []
        self.deck.endOfMedia.connect(lambda: ended.append(True))
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        self.deck.pause()
        QTest.qWait(180)
        self.assertEqual(ended, [])

        self.deck.setPosition(1900)
        self.deck.play()
        self._wait_until(lambda: ended, timeout_ms=1000)
        self.assertEqual(len(ended), 1)
        self.assertEqual(self.deck.playbackState(), PlaybackState.PAUSED)

    def test_rapid_source_replacement_stays_live(self):
        """Source replacement may race an in-flight CoreAudio callback."""
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        source = QUrl.fromLocalFile(str(self.audio))
        for _ in range(40):
            self.deck.setSource(source)
            self.deck.play()
            QTest.qWait(3)

        self.deck.setSource(source)
        self.deck.play()
        self._wait_until(lambda: self.deck.duration() > 0)
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        self._wait_until(lambda: self.deck.position() >= 40)
        self.deck.pause()
        self.assertEqual(errors, [])

    def test_full_resolution_analysis_upload_and_preview(self):
        self.deck.set_analysis({
            "heights": [0.2, 0.8, 0.5, 1.0],
            "colors": [[1, 2, 7], [14, 1, 1], [2, 7, 2], [8, 2, 7]],
            "sample_rate": 150.0,
            "beats": [[1, 500, 120.0], [2, 1000, 120.0]],
            "cues": [
                {"kind": "hot", "slot": 0, "position_ms": 600,
                 "color": [220, 40, 90]},
                {"kind": "memory", "slot": 0, "position_ms": 900,
                 "color": [20, 80, 240]},
            ],
        })
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 2)
        self.assertTrue(self.deck.set_session_cue(750))
        # The transient deck CUE is a separate identity, never imported cue
        # data and never counted/persisted with hot or memory cues.
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 2)
        self.assertTrue(self.deck.set_session_cue(None))
        self.deck.set_visual_position(750, True)
        QTest.qWait(30)
        self.deck.set_visual_position(750, False)

    def test_blue_rgb_and_three_band_streams_switch_without_recooking(self):
        detail = {
            "_blue_detail": b"\x21\x42\x63\x7f",
            "_rgb_detail": b"\xe0\x7c\x1c\x40\x03\x80\x7c\x20",
            "_detail_bands": bytes(
                [100, 20, 5, 20, 100, 5, 5, 20, 120, 40, 50, 60]),
        }
        self.assertTrue(self.deck._set_waveform_streams(detail, 150.0))
        for mode in ("BLUE", "RGB", "3BAND"):
            self.assertEqual(self.deck.set_waveform_mode(mode), mode)
            self.assertEqual(self.deck.diagnostic_waveform_mode(), mode)
            self.assertEqual(
                self.deck.diagnostic_analysis_shape(), (4, 150.0))

    def test_mouse_hold_atomically_captures_the_continuous_visual_clock(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self.deck.play()
        self._wait_until(lambda: self.deck.position() >= 100)
        self._wait_until(
            lambda: self.deck.diagnostic_presented_frame() > 0)
        presented = self.deck.diagnostic_presented_frame()
        held_ms = self.deck.begin_visual_hold()
        held_frame = round(held_ms * 44_100 / 1000)

        self.assertGreaterEqual(held_frame, presented)
        QTest.qWait(30)
        self.assertLessEqual(
            abs(held_frame - self.deck.diagnostic_scheduled_frame()), 1)
        self.deck.set_visual_position(held_ms, False)

    def test_new_analysis_clears_previous_cue_markers(self):
        self.deck.set_analysis({
            "heights": [0.5],
            "colors": [[14, 1, 1]],
            "sample_rate": 150.0,
            "cues": [{
                "kind": "hot", "slot": 1, "position_ms": 500,
                "color": [24, 174, 232],
            }],
        })
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 1)
        self.deck.set_analysis({
            "heights": [0.5],
            "colors": [[14, 1, 1]],
            "sample_rate": 150.0,
            "cues": [],
        })
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 0)

    def test_begin_track_atomically_clears_and_rejects_stale_generation(self):
        self.assertTrue(self.deck.begin_track(41))
        packed = {
            "_overview_heights": b"\x40\x80",
            "_overview_rgb": b"\x0e\x00\x00\x00\x07\x00",
            "sample_rate": 150.0,
            "beats": [[1, 0, 120.0]],
        }
        self.assertTrue(self.deck.set_analysis(packed, 41))
        self.deck.set_audio_detail(
            {"rate": 6144.0, "heights": b"\x20\x80\xff"}, 41)
        self.deck.set_cues([{
            "kind": "hot", "slot": 0, "position_ms": 100,
        }])
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (2, 150.0))
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 1)

        self.assertTrue(self.deck.begin_track(42))
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (0, 0.0))
        self.assertEqual(self.deck.diagnostic_detail_shape(), (0, 0.0))
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 0)
        self.assertFalse(self.deck.set_analysis(packed, 41))
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (0, 0.0))
        self.assertTrue(self.deck.set_analysis(packed, 42))

    def test_fresh_analysis_promotes_one_full_resolution_surface(self):
        self.deck.set_analysis({
            "heights": [0.1, 0.2, 0.3],
            "colors": [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
            "sample_rate": 150.0,
            "columns": [[0.3, [7, 8, 9]], [0.2, [4, 5, 6]]],
        })
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (3, 150.0))
        self.assertEqual(self.deck.diagnostic_detail_shape(), (3, 150.0))

    def test_audio_detail_upgrades_resolution_without_replacing_rgb_surface(self):
        self.deck.set_analysis({
            "heights": [0.1, 0.2, 0.3],
            "colors": [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
            "sample_rate": 150.0,
            "columns": [[0.3, [7, 8, 9]], [0.2, [4, 5, 6]]],
            "_audio_detail": {
                "rate": 1536.0,
                "heights": bytes([10, 80, 160, 255, 120, 20]),
            },
        })
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (3, 150.0))
        self.assertEqual(self.deck.diagnostic_detail_shape(), (6, 1536.0))
        self.assertAlmostEqual(self.deck.diagnostic_detail_peak(), 1.0)
        # The scrolling waveform uses this peak-preserving pyramid at every
        # zoom; PWV7 is now only a fallback while the additive cache is absent.
        self.assertEqual(self.deck.diagnostic_audio_detail_mip_count(), 6)
        # Two ushort polarity peaks plus signed min/max per sample, including
        # all mip levels. The old WaveSample pyramid consumed 16 bytes/sample
        # and duplicated RGB.
        self.assertLessEqual(
            self.deck.diagnostic_audio_detail_buffer_bytes(), 128)
        for zoom in (
                24.0, 32.0, 48.0, 64.0, 92.0, 128.0, 184.0,
                256.0, 384.0, 576.0, 864.0, 1152.0, 1536.0):
            self.assertEqual(self.deck.set_zoom(zoom), zoom)
            self.assertEqual(self.deck.diagnostic_audio_detail_mip_count(), 6)

    def test_complete_analysis_commits_overview_and_ultra_as_one_surface(self):
        overview = {
            "_overview_heights": bytes([20, 80, 160, 220]),
            "_overview_rgb": bytes(
                [14, 0, 0, 12, 2, 0, 2, 7, 1, 0, 2, 7]),
            "sample_rate": 150.0,
            "beats": [[1, 0, 120.0]],
        }
        detail = {
            "rate": 6144.0,
            "heights": bytes([10, 40, 90, 180, 255, 100]),
        }

        overview_ok, detail_ok = self.deck.set_complete_analysis(
            overview, detail)

        self.assertTrue(overview_ok)
        self.assertTrue(detail_ok)
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (4, 150.0))
        self.assertEqual(self.deck.diagnostic_detail_shape(), (6, 6144.0))

    def test_u16_channel_peak_analysis_preserves_quiet_display_detail(self):
        overview = {
            "_overview_heights": struct.pack(
                "<4H", 65535, 655, 0, 32768),
            "_overview_rgb": bytes(
                [14, 0, 0, 12, 2, 0, 0, 0, 0, 0, 2, 7]),
            "_height_encoding": "linear-u16le",
            "sample_rate": 150.0,
            "beats": [[1, 0, 120.0]],
        }
        detail = {
            "rate": 6144.0,
            "heights": struct.pack("<4H", 65535, 655, 0, 32768),
            "encoding": "linear-u16le",
        }
        overview_ok, detail_ok = self.deck.set_complete_analysis(
            overview, detail)
        self.assertTrue(overview_ok)
        self.assertTrue(detail_ok)
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (4, 150.0))
        self.assertEqual(self.deck.diagnostic_detail_shape(), (4, 6144.0))
        self.assertAlmostEqual(
            self.deck.diagnostic_detail_peak(), 0.84, places=4)
        self.assertGreater(
            self.deck.diagnostic_audio_detail_buffer_bytes(), 0)

    def test_u16_audio_detail_upload_keeps_upper_and_lower_independent(self):
        detail = {
            "rate": 6144.0,
            "heights": struct.pack("<4H", 65535, 32768, 1000, 0),
            "lower_heights": struct.pack("<4H", 0, 1000, 32768, 65535),
            "encoding": "linear-u16le",
        }
        self.deck.set_audio_detail(detail)
        self.assertEqual(self.deck.diagnostic_detail_shape(), (4, 6144.0))
        self.assertAlmostEqual(
            self.deck.diagnostic_detail_peak(), 0.84, places=4)
        self.assertGreater(
            self.deck.diagnostic_audio_detail_buffer_bytes(), 0)

    def test_v5_prebuilt_range_mips_install_without_rebuilding_base_streams(self):
        base_count = 256
        divisors = [8, 13, 18, 25, 35, 50, 66, 99, 132, 192, 256]
        counts = [
            (base_count + divisor - 1) // divisor for divisor in divisors]
        detail = {
            "rate": 6144.0,
            "base_count": base_count,
            "mip_divisors": divisors,
            "mip_counts": counts,
            "range_min_mips": [
                struct.pack(f"<{count}h", *([-12_000] * count))
                for count in counts
            ],
            "range_max_mips": [
                struct.pack(f"<{count}h", *([18_000] * count))
                for count in counts
            ],
            "encoding": "song-anchored-extrema-i16le-v1",
        }
        self.deck.set_audio_detail(detail)
        self.assertEqual(
            self.deck.diagnostic_detail_shape(), (base_count, 6144.0))
        self.assertEqual(
            self.deck.diagnostic_audio_detail_mip_count(), len(divisors))
        self.assertEqual(
            self.deck.diagnostic_audio_detail_buffer_bytes(),
            sum(counts) * 8)

    def test_native_zoom_returns_the_applied_renderer_scale(self):
        self.assertEqual(self.deck.set_zoom(-100.0), 24.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 24.0)
        self.assertEqual(self.deck.set_zoom(100.0), 92.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 92.0)
        self.assertEqual(self.deck.set_zoom(112.0), 128.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 128.0)
        self.assertEqual(self.deck.set_zoom(50_000.0), 1536.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 1536.0)

    def test_rejected_visual_probes_are_not_in_the_production_abi(self):
        for name in (
                "sb_deck_set_carrier_neutral_probe",
                "sb_deck_set_stable_gap_bed_probe",
                "sb_deck_set_gap_edge_sharpness_probe",
                "sb_deck_set_rgb_color_edge_sharpness_probe"):
            self.assertFalse(hasattr(self.deck._lib, name))

    def test_24_band_master_does_not_override_dedicated_rgb(self):
        spectral = bytes(
            [255] * 8 + [0] * 16
            + [0] * 8 + [255] * 8 + [0] * 8
            + [0] * 16 + [255] * 8)
        self.deck.set_analysis({
            "heights": [0.2, 0.2, 0.2],
            "colors": [[14, 0, 0], [0, 7, 0], [0, 0, 7]],
            "sample_rate": 150.0,
            "_spectral_24": spectral,
        })
        self.assertEqual(self.deck.diagnostic_detail_shape(), (3, 150.0))
        self.deck.set_audio_detail({
            "rate": 1536.0,
            "heights": bytes([20, 40, 80, 160]),
        })
        self.assertEqual(self.deck.diagnostic_detail_shape(), (4, 1536.0))
        self.assertGreater(
            self.deck.diagnostic_audio_detail_buffer_bytes(), 0)

    def test_populated_audio_detail_holds_cadence_at_both_zoom_extremes(self):
        # Exercise a full-width deck, not only the compact integration host.
        # The direct final-pixel shader must remain inside the display budget
        # without relying on a translated intermediate texture.
        self.host.resize(1800, 190)
        self.app.processEvents()
        rate = 1536
        heights = bytes(
            255 if i % 384 in range(80, 96)
            else 120 if i % 384 in range(210, 260)
            else 18
            for i in range(rate * 30)
        )
        self.deck.set_analysis({
            "heights": [0.2, 0.7, 0.4, 1.0],
            "colors": [[1, 2, 7], [14, 0, 0], [2, 7, 2], [8, 2, 7]],
            "sample_rate": 150.0,
            "_audio_detail": {
                "rate": float(rate),
                "heights": heights,
            },
        })
        self.deck.set_visual_position(15_000, True)
        screen_rate = float(self.host.screen().refreshRate() or 60.0)
        expected = min(120.0, max(60.0, screen_rate))
        for zoom in (24.0, 1536.0):
            self.deck.set_zoom(zoom)
            QTest.qWait(150)
            self.deck.diagnostic_reset_display_stats()
            QTest.qWait(700)
            presentations, fps, max_gap_ms = (
                self.deck.diagnostic_display_stats())
            self.assertGreaterEqual(
                presentations, round(expected * 0.7 * 0.85))
            self.assertGreaterEqual(fps, expected * 0.9)
            self.assertLessEqual(max_gap_ms, 2.2 * 1000 / expected)

    def test_presentation_error_is_measured_against_the_submitted_target(self):
        # Position is predicted for the target presentation timestamp. If the
        # drawable actually reaches the screen elsewhere, that difference is
        # positional jitter the shader can never filter out, so it has to be
        # observable before anyone blames reconstruction for it.
        self.host.resize(1800, 190)
        self.app.processEvents()
        self.deck.set_analysis({
            "heights": [0.2, 0.7, 0.4, 1.0],
            "colors": [[1, 2, 7], [14, 0, 0], [2, 7, 2], [8, 2, 7]],
            "sample_rate": 150.0,
        })
        self.deck.set_zoom(1536.0)
        self.deck.set_visual_position(15_000, True)
        QTest.qWait(150)
        self.deck.diagnostic_reset_display_stats()
        self.assertEqual(
            self.deck.diagnostic_presentation_error(), (0.0, 0.0, 0.0))
        QTest.qWait(700)
        presentations, _, _ = self.deck.diagnostic_display_stats()
        self.assertGreater(presentations, 0)
        mean_ms, stddev_ms, max_ms = (
            self.deck.diagnostic_presentation_error())
        self.assertGreaterEqual(max_ms, abs(mean_ms))
        self.assertGreaterEqual(stddev_ms, 0.0)
        # A drawable cannot present a whole second away from its target; this
        # only guards the plumbing, the real number is read from the app.
        self.assertLess(max_ms, 1000.0)

    def test_visual_clock_advances_between_audio_buffer_callbacks(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        frames = []
        for _ in range(60):
            QTest.qWait(2)
            frames.append(self.deck.diagnostic_visual_frame())
        self.deck.pause()
        self.assertEqual(frames, sorted(frames))
        # A buffer-stepped clock repeats for several 2 ms samples. The Metal
        # clock must keep moving continuously between CoreAudio callbacks.
        self.assertGreater(len(set(frames)), 55)

    def test_display_link_sleeps_while_the_deck_is_static(self):
        self.deck.request_display()
        QTest.qWait(100)
        self.deck.diagnostic_reset_display_stats()
        QTest.qWait(500)
        presentations, _fps, _max_gap_ms = (
            self.deck.diagnostic_display_stats())
        self.assertLessEqual(presentations, 1)

    def test_redundant_inactive_preview_updates_do_not_wake_metal(self):
        """Coarse UI transport polling cannot interfere with display pacing."""
        self.deck.request_display()
        QTest.qWait(100)
        self.deck.diagnostic_reset_display_stats()
        for position in range(0, 1000, 5):
            self.deck.set_visual_position(position, False)
        QTest.qWait(150)
        presentations, _fps, _max_gap_ms = (
            self.deck.diagnostic_display_stats())
        self.assertLessEqual(presentations, 1)

    def test_host_resize_commits_view_and_drawable_in_the_same_turn(self):
        """Qt host, AppKit child, and Retina drawable never trail by a frame."""
        for width, height in ((517, 137), (1183, 164), (640, 132)):
            self.host.resize(width, height)
            self.app.processEvents()
            parent, view, drawable = self.deck.diagnostic_view_geometry()
            scale = float(self.host.devicePixelRatioF())
            self.assertAlmostEqual(parent, float(width), delta=0.5)
            self.assertAlmostEqual(view, parent, delta=0.5)
            self.assertAlmostEqual(drawable, parent * scale, delta=1.0)

        # The final resize wakes one corrected frame and then returns the
        # static deck to sleep; it does not restart continuous rendering.
        QTest.qWait(100)
        self.deck.diagnostic_reset_display_stats()
        self.host.resize(901, 148)
        self.app.processEvents()
        QTest.qWait(120)
        presentations, _fps, _max_gap_ms = (
            self.deck.diagnostic_display_stats())
        self.assertGreaterEqual(presentations, 1)
        self.assertLessEqual(presentations, 2)

    def test_native_snapshot_polling_is_adaptive(self):
        self.assertEqual(self.deck._poll_timer.interval(), 125)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self.assertEqual(self.deck._poll_timer.interval(), 8)
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.synchronize()
        self.assertEqual(self.deck._poll_timer.interval(), 125)
        self.deck.play()
        self.assertEqual(self.deck._poll_timer.interval(), 8)
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        self.deck.pause()
        self.deck.synchronize()
        self.assertEqual(self.deck._poll_timer.interval(), 125)

    def test_display_link_holds_screen_cadence_during_playback(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        QTest.qWait(250)
        self.deck.diagnostic_reset_display_stats()
        QTest.qWait(1000)
        presentations, fps, max_gap_ms = (
            self.deck.diagnostic_display_stats())
        screen_rate = float(self.host.screen().refreshRate() or 60.0)
        expected = min(120.0, max(60.0, screen_rate))
        self.assertGreaterEqual(presentations, round(expected * 0.9))
        self.assertGreaterEqual(fps, expected * 0.95)
        self.assertLessEqual(max_gap_ms, 2.1 * 1000 / expected)
        self.deck.pause()

    def test_bounded_frame_trace_exports_exact_pixels_and_timing_rows(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        status = self.deck.diagnostic_begin_frame_trace(4, 64)
        self.assertEqual(status["max_frames"], 4)
        self.assertEqual(status["submitted_frames"], 0)
        self.assertEqual(status["crop_width_pixels"], 64)
        self.assertLess(status["capture_bytes"], 512 * 1024 * 1024)

        self.deck.play()
        self._wait_until(
            lambda: self.deck.diagnostic_frame_trace_status()[
                "submitted_frames"] == 4)
        self.deck.pause()
        output = Path(self.temp.name) / f"frame-trace-{time.time_ns()}"
        self.deck.diagnostic_finish_frame_trace(output)

        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(
            manifest["format"], "soundbox-metal-frame-trace-v1")
        self.assertEqual(manifest["submitted_frames"], 4)
        self.assertEqual(manifest["gpu_completed_frames"], 4)
        self.assertEqual(
            (output / "frames.bgra").stat().st_size,
            manifest["frame_stride_bytes"] * 4)
        rows = (output / "frames.csv").read_text().splitlines()
        self.assertEqual(len(rows), 5)
        self.assertIn("target_presentation_timestamp", rows[0])
        self.assertIn("actual_presented_time", rows[0])

    def test_frame_trace_refuses_file_io_until_playback_stops(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.diagnostic_begin_frame_trace(2, 64)
        self.deck.play()
        self._wait_until(
            lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        output = Path(self.temp.name) / f"live-trace-{time.time_ns()}"
        with self.assertRaisesRegex(RuntimeError, "could not be finalized"):
            self.deck.diagnostic_finish_frame_trace(output)
        self.deck.pause()
        self.deck.diagnostic_cancel_frame_trace()

    def test_detail_zoom_wakes_one_static_frame_then_sleeps(self):
        self.deck.set_zoom(1536.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 1536.0)
        QTest.qWait(100)
        self.deck.diagnostic_reset_display_stats()
        QTest.qWait(500)
        presentations, _fps, _max_gap_ms = (
            self.deck.diagnostic_display_stats())
        self.assertLessEqual(presentations, 1)

    def test_rekordbox_rgb_expansion_matches_player_palette(self):
        self.assertEqual(NativeDeckBackend._screen_rgb([14, 0, 0]),
                         (252, 0, 0))
        color = NativeDeckBackend._screen_rgb([1, 2, 7])
        self.assertGreaterEqual(max(color), 155)
