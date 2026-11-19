"""Native CoreAudio/Metal deck contract and source-safety tests."""

from __future__ import annotations

import hashlib
import ctypes
import csv
import json
import math
import struct
import shutil
import subprocess
import tempfile
import time
import unittest

from tests.support.native_source import native_deck_source
from types import SimpleNamespace
from unittest import mock
import wave
from pathlib import Path

from PySide6.QtCore import QUrl
from desktop.native_deck import PlaybackState
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from desktop.native_deck import (
    NativeDeckBackend, _Snapshot, _repair_pwv5_visibility,
    _repair_soundbox_pwv5_color,
    native_available, native_library_path,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WaveformStreamRepair(unittest.TestCase):
    def test_soundbox_pwv5_color_is_restored_from_compact_overview(self):
        # Preserve each low seven-bit height/control payload while replacing
        # the old red-in-green fallback color with exact compact RGB codes.
        packed = bytes.fromhex("107c 1078 1074")
        overview_rgb = bytes((14, 0, 0, 2, 7, 1, 0, 2, 7))
        repaired = _repair_soundbox_pwv5_color(packed, overview_rgb)
        self.assertEqual(repaired, bytes.fromhex("e07c 3cf8 0bf4"))

    def test_audible_zero_pwv5_rows_receive_only_a_minimum_color_carrier(self):
        packed = bytes.fromhex("e07c 0000 0000 0384")
        overview = struct.pack("<HHHH", 100, 20, 0, 30)
        repaired = _repair_pwv5_visibility(
            packed, overview, "linear-u16le")
        self.assertEqual(
            repaired,
            bytes.fromhex("e07c e004 0000 0384"))

    def test_visibility_repair_matches_scalar_nearest_color_reference(self):
        import numpy as np
        rng = np.random.default_rng(41)
        for width in (1, 2):
            values = rng.integers(0, 65536, 2400, dtype=np.uint16)
            values[::3] &= 0xff83
            values[::7] = 0
            heights = rng.integers(0, 4, len(values), dtype=np.uint16)
            valid = [i for i, value in enumerate(values) if int(value) & 0xff80]
            expected = values.copy()
            for i, value in enumerate(values):
                if heights[i] and not int(value) & 0x7c:
                    color = int(value) & 0xff80
                    if not color:
                        nearest = min(valid, key=lambda j: (abs(i - j), j))
                        color = int(values[nearest]) & 0xff80
                    expected[i] = color | 4
            tail = b"x"
            self.assertEqual(_repair_pwv5_visibility(
                values.astype(">u2").tobytes() + tail,
                heights.astype("<u2" if width == 2 else "u1").tobytes(),
                "linear-u16le" if width == 2 else "u8"),
                expected.astype(">u2").tobytes() + tail)

    def test_repair_is_track_agnostic_and_preserves_true_silence(self):
        packed = bytes.fromhex("e07c 0000 0384")
        overview = struct.pack("<HHH", 100, 0, 30)
        self.assertEqual(
            _repair_pwv5_visibility(
                packed, overview, "linear-u16le"),
            packed)


class TerminalSnapshotOrdering(unittest.TestCase):
    def test_newer_end_flag_overrides_playing_snapshot_and_emits_once(self):
        snapshot = _Snapshot()
        snapshot.sample_rate = 44100
        snapshot.duration_frames = 88200
        snapshot.position_frames = 88100
        snapshot.playback_state = 1
        backend = SimpleNamespace(
            _handle=1, _snapshot=snapshot,
            _lib=SimpleNamespace(sb_deck_snapshot=lambda *_: True,
                                 sb_deck_has_ended=lambda *_: True),
            _duration_ms=2000, _position_ms=1998, _last_position_emit=0,
            _state=PlaybackState.PLAYING, _end_emitted=False,
            _ramp_token=0, _loading=False, _last_error=(0, 0),
            durationChanged=mock.Mock(), positionChanged=mock.Mock(),
            endOfMedia=mock.Mock(), _set_poll_interval=mock.Mock(),
        )
        backend._set_state = lambda value: setattr(backend, "_state", value)
        NativeDeckBackend._poll(backend)
        # A deferred auto-advance must see the stopped engine, otherwise it
        # waits for a gain ramp whose audio callback can no longer finish.
        self.assertEqual(backend._state, PlaybackState.PAUSED)
        backend._set_poll_interval.assert_called_with(125)
        NativeDeckBackend._poll(backend)
        backend.endOfMedia.emit.assert_called_once_with()


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
        self.host.move(80, 80)  # repeated Cocoa windows must not cascade off-screen
        self.host.show()
        self.host.raise_()
        self.host.activateWindow()
        self.app.processEvents()
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

    def _decoder_waits(self):
        count = self.deck._lib.sb_deck_decode_wait_returns
        count.argtypes = [ctypes.c_void_p]
        count.restype = ctypes.c_uint64
        return count(self.deck._handle)

    def _assert_decoder_parked(self):
        QTest.qWait(50)
        before = self._decoder_waits()
        QTest.qWait(150)
        self.assertEqual(self._decoder_waits(), before)

    def test_decoder_parks_unloaded_at_eof_and_resumes_commands_and_shutdown(self):
        self._assert_decoder_parked()
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() == 2000)
        self._assert_decoder_parked()
        self.deck.play()
        self._wait_until(lambda: self.deck.position() > 50)
        self.deck.setPosition(1800)
        self._wait_until(lambda: self.deck.position() >= 1990)
        self._wait_until(lambda: self.deck._end_emitted)
        self._assert_decoder_parked()
        self.deck.play()
        self._wait_until(lambda: 10 < self.deck.position() < 1000)
        self.deck.pause()
        self._assert_decoder_parked()
        self.deck.setPosition(400)
        self.deck.play()
        self._wait_until(lambda: 450 < self.deck.position() < 1200)
        self.deck.pause()
        self._assert_decoder_parked()
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        self.deck.setSource(QUrl.fromLocalFile(str(Path(self.temp.name) / 'missing.wav')))
        self._wait_until(lambda: bool(errors))
        self._assert_decoder_parked()
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() == 2000)
        self.deck.play()
        self._wait_until(lambda: self.deck.position() > 50)
        self.deck.pause()
        self._assert_decoder_parked()
        start = time.monotonic()
        self.deck.close()
        self.assertLess(time.monotonic() - start, 1)

    def test_decoder_paused_full_ring_parks_then_play_and_replacement_resume(self):
        long_audio = Path(self.temp.name) / 'paused-full-ring.wav'
        with wave.open(str(long_audio), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44100)
            out.writeframes(bytes(44100 * 30 * 4))
        self.deck.setSource(QUrl.fromLocalFile(str(long_audio)))
        self._wait_until(lambda: self.deck.duration() == 30000)
        self._assert_decoder_parked()
        self.deck.play()
        self._wait_until(lambda: self.deck.position() > 50)
        self.deck.pause()
        self._assert_decoder_parked()
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() == 2000)
        self.deck.play()
        self._wait_until(lambda: self.deck.position() > 50)
        self.deck.pause()
        self._assert_decoder_parked()

    def test_full_library_cue_retriggers_do_not_scan_and_return_within_response_budget(self):
        from desktop.app import MainWindow
        from soundbox.library import Library, Track
        with tempfile.TemporaryDirectory(prefix='sb-cue-window-') as area:
            # isolated_dir suppresses constructor discovery and source loading.
            # Keep suppression active throughout callbacks, not only startup.
            win = MainWindow(isolated_dir=area)
            win.lib = Library({str(i): Track(str(i), f'Track {i}', f'Artist {i % 100}',
                              self.audio.as_uri(), total_time=120) for i in range(3740)}, [], 'synthetic')
            pb = win.player_bar
            pb.track_analysis_dir = Path(area) / 'analysis'
            pb._deck_volume = 0
            win.show()
            try:
                win._apply_loaded_library()
                self.assertEqual(win.model.rowCount(), 3740)
                pb.play_track('1', self.audio, autoplay=True)
                self._wait_until(pb.is_playing)
                # Finish first-expose/layout and the one-time row-cache warm;
                # measure an interactive loaded deck, not window construction.
                self._wait_until(lambda: win._warm_worker is None and not win._warm_timer.isActive())
                QTest.qWait(100)
                pb.toggle()
                self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
                pb._set_session_cue(200)
                latencies = []
                with mock.patch.object(win.model, 'refresh_art') as art, \
                        mock.patch.object(win, '_refresh_devices') as devices:
                    for kind, target in [('session', 200), ('hot', 400), ('memory', 600)] * 4:
                        start = time.perf_counter()
                        pb._marker_cue_press(kind, 0, target)
                        deadline = start + .2
                        while time.perf_counter() < deadline:
                            self.app.processEvents()
                            if pb.is_playing() and target <= pb.player.position() < target + 100:
                                break
                            QTest.qWait(1)
                        latencies.append((time.perf_counter() - start) * 1000)
                        QTest.qWait(22)
                        start = time.perf_counter()
                        pb._marker_cue_release(kind, 0)
                        deadline = start + .2
                        while (pb.is_playing() or pb._pause_pending) and time.perf_counter() < deadline:
                            QTest.qWait(1)
                        latencies.append((time.perf_counter() - start) * 1000)
                        self.assertFalse(pb.is_playing())
                        self.assertEqual(pb.player.position(), target)
                    art.assert_not_called()
                    devices.assert_not_called()
                # Reject the old 24-ms fade + per-release full-library scan.
                # Allow ordinary CoreAudio/Qt scheduling variation on this Mac.
                self.assertLess(sorted(latencies)[int(len(latencies) * .95)], 60, latencies)
                self.assertLess(max(latencies), 100, latencies)
                self.assertFalse(pb._cue_preview_active)
            finally:
                win.shutdown()
                win.close()
                self.app.processEvents()

    def test_playing_pending_cues_are_visible_and_usable_before_canonical_acknowledgement(self):
        from threading import Event
        from PySide6.QtCore import QTimer
        from desktop.app import MainWindow
        from soundbox.library import Library, Track
        from soundbox.providers import Providers
        from soundbox.state_repository import read_state
        before_audio=_sha256(self.audio)
        with tempfile.TemporaryDirectory(prefix='sb-playing-pending-cues-') as area, \
                mock.patch.object(MainWindow,'_refresh_devices'), \
                mock.patch('soundbox.rekordbox_db.load_live',side_effect=AssertionError('Real source read')):
            root=Path(area)
            win=MainWindow(isolated_dir=root/'state',providers=Providers('public'))
            pb=win.player_bar;pb._deck_volume=0
            release=Event();entered=Event();heartbeat=QTimer()
            patches=[]
            for controller,method in ((win.graveyard_controller,'prepare_library'),
                                      (win.native_analysis_controller,'discover'),
                                      (win.media_controller,'discover')):
                patch=mock.patch.object(controller,method);patch.start();patches.append(patch)
            for method in ('_cache_library','_arm_waveform_rechecks','_schedule_stock_artwork',
                           '_resume_analysis_jobs','_maybe_fill_waveforms'):
                patch=mock.patch.object(win,method);patch.start();patches.append(patch)
            track=Track('pending-native','Generated native cue fixture','Artist',self.audio.as_uri(),
                total_time=2,source_native_id='1',source_uuid='00000000-0000-4000-8000-000000000001',
                source_cues=[],source_cue_revision='a'*64)
            win.lib=Library({track.track_id:track},[],root/'synthetic-master.db',
                            source_kind='rekordbox-db',source_library_id='generated-cue-library')
            win._rekordbox_source_verified=True
            win.tag_controller._requires_cue_authority=False
            win._apply_loaded_library()
            win.move(80,200);win.show()
            try:
                self.assertTrue(win._open_track(track,autoplay=True))
                self._wait_until(lambda:pb.player.duration()==2000 and not pb._pending_autoplay,5000)
                self._wait_until(lambda:not pb._waveform_tasks.pending,5000)
                QTest.qWait(100)
                pb.quantize_enabled=False
                pb.seek_ms(200)
                if not pb.is_playing():pb.toggle()
                self._wait_until(lambda:pb.is_playing() and 200<=pb.player.position()<1000)
                win._sync_cue_editing();self.assertTrue(pb.cue_panel._editable)
                self.assertTrue(win._save_state())
                commit=win._state_repository.commit_patch;captured=[];positions=[];ticks=[]
                def held(patch):
                    if ('settings','pending_prep_edits') in patch.edits()[0]:
                        entered.set()
                        if not release.wait(5):raise AssertionError('Canonical cue fixture was not released')
                    return commit(patch)
                capture=pb.capture_cue_position
                def position():
                    value=capture();positions.append(value);return value
                heartbeat.setInterval(2);heartbeat.timeout.connect(lambda:ticks.append(time.perf_counter()))
                with mock.patch.object(win._state_repository,'commit_patch',side_effect=held), \
                        mock.patch.object(win.tag_controller,'run_prep_job',return_value=False) as source_writer, \
                        mock.patch.object(pb,'capture_cue_position',side_effect=position):
                    heartbeat.start()
                    for kind in ('hot','memory'):
                        button=(pb.cue_panel.hot_slots if kind=='hot' else pb.cue_panel.memory_slots)[0]
                        self.assertTrue(button.isEnabled())
                        start=time.perf_counter();button.click()
                        captured.append((time.perf_counter()-start)*1000)
                        cue=pb.cue_panel._cues[kind][0]
                        self.assertIsNotNone(cue);self.assertEqual(cue['position_ms'],positions[-1])
                        self.assertTrue(pb.is_playing());self.assertFalse(win.prep_controller.pending)
                        self.assertFalse(release.is_set())
                        if kind=='hot':self._wait_until(entered.is_set);QTest.qWait(20)
                    self.assertEqual(pb.player.diagnostic_cue_marker_count(),2)
                    self.assertEqual(len(pb.wave._cues),2)
                    source_writer.assert_not_called()
                    self.assertFalse(read_state(win._state_repository.path).get('settings',{}).get('pending_prep_edits'))
                    auditions=[]
                    start=time.perf_counter();pb.cue_panel._press('hot',0)
                    self._wait_until(lambda:pb.is_playing() and positions[0]<=pb.player.position()<positions[0]+100)
                    auditions.append((time.perf_counter()-start)*1000);pb.cue_panel._release('hot',0)
                    pb.toggle();self._wait_until(lambda:not pb.is_playing() and not pb._pause_pending)
                    start=time.perf_counter();pb.cue_panel._press('memory',0)
                    self._wait_until(lambda:pb.is_playing() and positions[1]<=pb.player.position()<positions[1]+100)
                    auditions.append((time.perf_counter()-start)*1000);pb.cue_panel._release('memory',0)
                    self._wait_until(lambda:not pb.is_playing() and not pb._pause_pending)
                    self.assertFalse(release.is_set());self.assertTrue(win.prep_controller.admission_pending)
                    QTest.qWait(30);heartbeat.stop()
                    self.assertGreaterEqual(len(ticks),4)
                    gaps=[(b-a)*1000 for a,b in zip(ticks,ticks[1:])]
                    self.assertLess(max(captured),60,captured)
                    self.assertLess(max(auditions),100,auditions)
                    self.assertLess(max(gaps),60,gaps)
                    release.set();self._wait_until(lambda:not win.prep_controller.admission_pending,5000)
                    durable=read_state(win._state_repository.path)['settings']['pending_prep_edits'][0]
                    self.assertEqual([op['position'] for op in durable['operations']],positions)
                    self.assertEqual({cue['kind'] for cue in durable['cues']},{'hot','memory'})
                    self.assertIsNone(durable['prepared'])
                    print('optimistic_native_cue_evidence '+json.dumps(dict(
                        gesture_ms=captured,audition_ms=auditions,heartbeat_max_ms=max(gaps),
                        captured_native_positions=positions,native_markers=pb.player.diagnostic_cue_marker_count(),
                        durable_operations=len(durable['operations']),source_sql_executed=False)))
                self.assertEqual(_sha256(self.audio),before_audio)
            finally:
                heartbeat.stop();release.set();win.shutdown()
                for patch in reversed(patches):patch.stop()
                win.close();win.deleteLater()
                self.app.processEvents()

    def test_playing_hot_cues_and_navigation_preserve_intent_through_seek_fades(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb.track_analysis_dir = Path(self.temp.name) / 'hot-navigation-cache'
        pb._deck_volume = 0
        pb.show()
        try:
            pb.play_track('first', self.audio, autoplay=True)
            self._wait_until(pb.is_playing)
            for slot, target in ((0, 300), (1, 500), (2, 700)):
                pb._marker_cue_press('hot', slot, target)
                pb._marker_cue_release('hot', slot)  # release before fade/poll completes
            self._wait_until(lambda: pb.is_playing() and pb.player.position() >= 700)
            self.assertTrue(pb._ordinary_playback_requested)
            self.assertTrue(pb.play_btn.property('playing'))
            self.assertFalse(pb._cue_preview_active)
            pb.play_track('next', self.audio, autoplay=None)
            self._wait_until(lambda: pb.current_track() == 'next' and pb.is_playing())
            pb._marker_cue_press('hot', 0, 300)
            pb.toggle()  # Explicit pause during the hot-cue fade must win.
            pb._marker_cue_release('hot', 0)
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            QTest.qWait(80)
            self.assertFalse(pb.is_playing())
            pb.play_track('paused-next', self.audio, autoplay=None)
            self._wait_until(lambda: not pb._pending_autoplay)
            self.assertFalse(pb.is_playing())
            pb._marker_cue_press('hot', 1, 500)
            self._wait_until(pb.is_playing)
            pb._marker_cue_release('hot', 1)
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            self.assertEqual(pb.player.position(), 500)
        finally:
            pb.shutdown_waveforms()
            pb.close()

    def test_sub_poll_cue_taps_cancel_decoder_start_and_latest_gate_wins(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb._deck_volume = 0
        pb.track_analysis_dir = Path(self.temp.name) / 'rapid-analysis'
        try:
            pb.play_track('rapid', self.audio, autoplay=False)
            self._wait_until(lambda: pb._dur > 0 and not pb._pending_autoplay)
            for target in (200, 400, 600, 800) * 3:
                pb._marker_cue_press('hot', 0, target)
                pb._marker_cue_release('hot', 0)  # no event turn/poll between edges
            QTest.qWait(100)
            self.assertFalse(pb.is_playing())
            self.assertEqual(pb.player.position(), 800)
            self.assertFalse(pb._pause_pending)
            self.assertFalse(pb._cue_preview_active)
            pb._marker_cue_press('hot', 0, 300)
            QTest.qWait(2)
            pb._marker_cue_release('hot', 0)
            pb._marker_cue_press('memory', 0, 700)
            self._wait_until(lambda: pb.is_playing() and pb.player.position() >= 700)
            pb._marker_cue_release('hot', 0)  # stale control cannot stop latest
            self.assertTrue(pb._cue_preview_active)
            pb._marker_cue_release('memory', 0)
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            self.assertEqual(pb.player.position(), 700)
        finally:
            pb.shutdown()
            pb.close()

    def test_review_pause_seek_resume_and_cancellation_of_pending_source_change(self):
        from desktop.player import PlayerBar
        pb=PlayerBar()
        pb._deck_volume=0.0
        pb.track_analysis_dir=Path(self.temp.name)/"review-analysis"
        try:
            pb.play_track("review",self.audio,autoplay=True)
            self._wait_until(lambda: pb.is_playing() and pb._dur>0)
            pb.pause()
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            pb.seek_ms(700)
            self._wait_until(lambda: abs(pb.review_transport_state()["position"]-700)<30)
            pb.resume()
            self._wait_until(pb.is_playing)
            pb.play_track("replacement",self.audio,autoplay=True)
            self.assertTrue(pb._pause_pending)
            pb.pause()
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            QTest.qWait(80)
            self.assertEqual(pb.current_track(),"review")
            pb._pending_autoplay=True
            pb._pending_start_ms=None
            pb._pending_should_play=True
            pb.pause()
            pb._pending_start_ms=0
            pb._try_pending_autoplay()
            self.assertFalse(pb.review_transport_state()["requested"])
            self.assertFalse(pb.is_playing())
        finally:
            pb.shutdown()
            pb.close()

    def test_review_button_pauses_after_transient_state_and_during_modal_dialog(self):
        from desktop.player import PlayerBar
        from desktop.reviewplayer import ReviewPlayer
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        pb = PlayerBar()
        pb._deck_volume = 0.0
        pb.track_analysis_dir = Path(self.temp.name) / "modal-review-analysis"
        dialog = QDialog()
        dialog.setModal(True)
        audition = ReviewPlayer(player=pb, start=lambda tid: (
            pb.play_track(tid, self.audio, autoplay=True) or True))
        QVBoxLayout(dialog).addWidget(audition)
        try:
            dialog.show()
            audition.set_track("review")
            audition.play.click()
            self._wait_until(lambda: pb.is_playing() and pb._dur > 0)
            # A refill/activation PAUSED report can clear the button intent
            # before the real engine reports PLAYING again.
            pb._on_state(PlaybackState.PAUSED)
            pb._on_state(PlaybackState.PLAYING)
            self.assertFalse(pb._transport_play_active)
            audition._sync()
            self.assertEqual(audition.play.text(), "❚❚")
            audition.play.click()
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
            position = pb.player.position()
            QTest.qWait(80)
            self.assertLess(abs(pb.player.position() - position), 10)
            audition.play.click()
            self._wait_until(pb.is_playing)
            # A second click during fade-out must cancel that pause, rather
            # than leave the resume button inert until another click.
            audition.play.click()
            self.assertTrue(pb._pause_pending)
            audition.play.click()
            self._wait_until(lambda: pb.is_playing() and not pb._pause_pending)
            audition.shutdown()
            self._wait_until(lambda: not pb.is_playing() and not pb._pause_pending)
        finally:
            audition.shutdown()
            dialog.close()
            pb.shutdown()
            pb.close()

    def test_library_exists_and_has_stable_abi(self):
        self.assertTrue(native_library_path().is_file())

    def test_aac_and_alac_native_playback_and_seek(self):
        for codec in ("aac", "alac"):
            with self.subTest(codec=codec):
                path = Path(self.temp.name) / f"native-{codec}.m4a"
                subprocess.run([
                    "afconvert", "-f", "m4af", "-d", codec,
                    str(self.audio), str(path)], check=True, capture_output=True)
                before = _sha256(path)
                errors = []
                slot = lambda _, message: errors.append(message)
                self.deck.errorOccurred.connect(slot)
                try:
                    self.deck.setSource(QUrl.fromLocalFile(str(path)))
                    self.deck.play()  # Play before open/format negotiation finishes
                    self._wait_until(lambda: self.deck.position() > 200)
                    self.deck.setPosition(1000)
                    self._wait_until(lambda: self.deck.position() > 1200)
                    self.deck.pause()
                    self.assertEqual(errors, [])
                    self.assertEqual(_sha256(path), before)
                finally:
                    self.deck.errorOccurred.disconnect(slot)

    def test_failed_source_can_retry_play_after_file_becomes_readable(self):
        path = Path(self.temp.name) / "not-ready.wav"
        path.write_bytes(b"not an audio file")
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        self.deck.setSource(QUrl.fromLocalFile(str(path)))
        self.deck.play()
        self._wait_until(lambda: bool(errors))
        self.assertNotEqual(self.deck.playbackState(), PlaybackState.PLAYING)
        shutil.copyfile(self.audio, path)  # disposable recovery fixture only
        self.deck.play()  # no second selection or setSource call
        self._wait_until(lambda: self.deck.position() > 200)
        self.assertEqual(len(errors), 1)

    def test_play_from_nonzero_position_never_rewinds_visual_clock(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        for _ in range(5):
            self.deck.pause()
            self.deck.setPosition(1000)
            QTest.qWait(50)
            start = self.deck.diagnostic_visual_frame()
            self.deck.play()
            frames = [start]
            for _ in range(80):
                QTest.qWait(1)
                frames.append(self.deck.diagnostic_visual_frame())
            self.assertGreaterEqual(min(frames), start)
            self.assertEqual(frames, sorted(frames))
            self.assertGreater(frames[-1], start)

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

    def test_player_advances_after_pause_resume_and_delayed_terminal_poll(self):
        from desktop.player import PlayerBar

        pb = PlayerBar()
        pb.audio.setVolume(0.0)
        pb._deck_volume = 0.0
        ended = []
        pb.track_ended.connect(ended.append)
        try:
            pb._src_path = str(self.audio)
            pb._track_id = "native-end"
            pb.player.setSource(QUrl.fromLocalFile(str(self.audio)))
            self._wait_until(lambda: pb.player.duration() > 0)
            pb.toggle()
            self._wait_until(pb.is_playing)
            pb.toggle()
            self._wait_until(lambda: not pb._pause_pending)
            self.assertEqual(ended, [])
            pb.player.setPosition(1750)
            pb.toggle()
            self._wait_until(pb.is_playing)
            # Model a transient PAUSED/activation report while ordinary audio
            # continues, then a suspended UI that misses the terminal instant.
            pb._on_state(PlaybackState.PAUSED)
            self.assertFalse(pb._transport_play_active)
            pb.player._poll_timer.stop()
            time.sleep(0.4)
            pb.resynchronize_transport()
            self._wait_until(lambda: ended)
            self.assertEqual(ended, ["native-end"])
            pb.player.synchronize()
            QTest.qWait(150)
            self.assertEqual(ended, ["native-end"])
        finally:
            pb.shutdown()
            pb.close()

    def test_browser_replacement_keeps_playing_and_rapid_load_uses_latest_track(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb.audio.setVolume(0.0)
        pb._deck_volume = 0.0
        try:
            with mock.patch.object(pb, "_start_waveform"):
                pb.play_track("first", self.audio, autoplay=True)
                self._wait_until(lambda: pb.is_playing() and pb.player.position() > 30)
                pb.play_track("second", self.audio, autoplay=None)
                self.assertTrue(pb._pause_pending)
                pb.play_track("third", self.audio, autoplay=None)
                self._wait_until(lambda: pb.current_track() == "third" and pb.is_playing())
                self.assertTrue(pb._ordinary_playback_requested)
                self._wait_until(lambda: pb.player.position() > 60)
                pb.toggle()
                self.assertTrue(pb._pause_pending)
                # Loading during a user-requested fade to pause stays paused,
                # even though the native decoder still reports PLAYING.
                pb.play_track("paused", self.audio, autoplay=None)
                QTest.qWait(120)
                self.assertEqual(pb.current_track(), "paused")
                self.assertFalse(pb.is_playing())
                self.assertFalse(pb._ordinary_playback_requested)
        finally:
            pb.shutdown()
            pb.close()

    def test_browser_replacement_preserves_pending_play_and_exact_first_beat(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb.audio.setVolume(0.0)
        pb._deck_volume = 0.0
        try:
            with mock.patch.object(pb, "_start_waveform"):
                pb.play_track("waiting", self.audio, analysis_path="synthetic-grid", autoplay=True)
                self._wait_until(lambda: pb.player.duration() > 0)
                self.assertFalse(pb.is_playing())
                pb.play_track("latest", self.audio, analysis_path="synthetic-grid", autoplay=None)
                self.assertTrue(pb._pending_should_play)
                pb._on_analysis_manifest(str(self.audio), "synthetic-grid", pb._analysis_generation,
                                         {"beats": [[1, 375, 120.0]],
                                          "provenance": {"grid": "rekordbox"}})
                self._wait_until(pb.is_playing)
                self.assertEqual(pb._cue_ms, 375)
                self._wait_until(lambda: pb.player.position() > 400)
                # A refill/activation PAUSED snapshot must not drop ordinary intent.
                pb.player.pause()
                self.assertTrue(pb._ordinary_playback_requested)
                pb.play_track("refill", self.audio, autoplay=None)
                self._wait_until(pb.is_playing)
        finally:
            pb.shutdown()
            pb.close()

    def test_browser_replacement_does_not_latch_momentary_cue(self):
        from desktop.player import PlayerBar
        pb = PlayerBar()
        pb.audio.setVolume(0.0)
        pb._deck_volume = 0.0
        try:
            with mock.patch.object(pb, "_start_waveform"):
                pb.play_track("paused", self.audio, autoplay=None)
                self._wait_until(lambda: pb.player.duration() > 0)
                self.assertFalse(pb.is_playing())
                pb._cue_press()
                self._wait_until(pb.is_playing)
                self.assertFalse(pb._ordinary_playback_requested)
                pb.play_track("replacement", self.audio, autoplay=None)
                self._wait_until(lambda: pb.current_track() == "replacement" and not pb._pause_pending)
                QTest.qWait(100)
                self.assertFalse(pb.is_playing())
                pb._cue_release()
                self.assertFalse(pb.is_playing())
                pb._cue_press()
                self._wait_until(pb.is_playing)
                pb.toggle()  # Play latches the preview into ordinary playback.
                pb._cue_release()
                pb.play_track("latched-replacement", self.audio, autoplay=None)
                self._wait_until(lambda: pb.current_track() == "latched-replacement" and pb.is_playing())
                self.assertTrue(pb._ordinary_playback_requested)
        finally:
            pb.shutdown()
            pb.close()

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

    def test_grid_only_update_preserves_audio_wave_cues_and_native_partial_bar_numbering(self):
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.begin_track(41)
        self.assertTrue(self.deck.set_analysis({
            '_overview_heights': b'\x40' * 300, '_overview_rgb': b'\x0e\0\0' * 300,
            'sample_rate': 150.0, 'beats': [[1, 0, 90.0]]}, 41))
        self.deck.set_cues([dict(kind='hot', slot=0, position_ms=500)])
        self.deck.setPosition(500)
        self._wait_until(lambda: self.deck.position() == 500)
        shape = self.deck.diagnostic_analysis_shape(), self.deck.diagnostic_detail_shape()
        duration = self.deck.duration()
        beats = [[(i + 1) % 4 + 1, 9 + i * 2000 // 3, 90.0] for i in range(20)]
        self.assertTrue(self.deck.set_grid(beats, 41))
        self.assertEqual(self.deck.diagnostic_grid_position(9), (0, 2))
        self.assertEqual(self.deck.diagnostic_grid_position(2009), (1, 1))
        self.assertEqual(self.deck.diagnostic_grid_position(10009), (4, 1))
        self.assertFalse(self.deck.set_grid([[1, 0, 120.0]], 40))
        self.assertEqual(self.deck.diagnostic_grid_position(10009), (4, 1))
        self.assertEqual((self.deck.diagnostic_analysis_shape(), self.deck.diagnostic_detail_shape()), shape)
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 1)
        self.assertEqual((self.deck.position(), self.deck.duration()), (500, duration))
        self.assertNotEqual(self.deck.playbackState(), PlaybackState.PLAYING)
        # A late compact overview/full upload belongs to the same source
        # generation, but must not replace a newer user grid or cue identity.
        self.assertTrue(self.deck.set_analysis({
            '_overview_heights': b'\x40' * 300, '_overview_rgb': b'\x0e\0\0' * 300,
            'sample_rate': 150.0, 'beats': [[1, 0, 120.0]],
            'cues': [dict(kind='hot', slot=0, position_ms=500)]}, 41))
        self.assertEqual(self.deck.diagnostic_grid_position(10009), (4, 1))
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 1)
        self.deck.play()
        self._wait_until(lambda: self.deck.playbackState() == PlaybackState.PLAYING)
        start = self.deck.position()
        for offset in range(12):
            shifted = [[r[0], r[1] + offset, r[2]] for r in beats]
            self.assertTrue(self.deck.set_grid(shifted, 41))
            QTest.qWait(8)
        self.assertEqual(self.deck.playbackState(), PlaybackState.PLAYING)
        self.assertGreater(self.deck.position(), start + 40)
        self.assertEqual(self.deck.duration(), duration)
        self.assertEqual(self.deck.diagnostic_cue_marker_count(), 1)
        self.assertTrue(self.deck.set_grid(beats, 41, pinned=False))
        self.assertTrue(self.deck.set_analysis({
            '_overview_heights': b'\x40', '_overview_rgb': b'\x0e\0\0',
            'sample_rate': 150.0, 'beats': [[1, 0, 120.0]]}, 41))
        self.assertEqual(self.deck.diagnostic_grid_position(10009), (1, 1))
        self.deck.begin_track(42)
        self.assertTrue(self.deck.set_analysis({
            '_overview_heights': b'\x40', '_overview_rgb': b'\x0e\0\0',
            'sample_rate': 150.0, 'beats': [[1, 0, 120.0]]}, 42))
        self.assertEqual(self.deck.diagnostic_grid_position(10009), (1, 1))

    def test_memory_countdown_uses_fractional_grid_positions_and_has_no_phantom_next_cue(self):
        self.addCleanup(self.deck.set_beat_count_mode, 0)
        from soundbox.beat_count import countdown
        self.deck.begin_track(61)
        times = [100,600,1000,1500,1900,2400,2800,3300,3700,4200]
        self.assertTrue(self.deck.set_grid([[i%4+1,t,120] for i,t in enumerate(times)],61))
        self.deck.set_cues([dict(kind='hot',slot=0,position_ms=650),
                            dict(kind='memory',slot=0,position_ms=1150),
                            dict(kind='memory',slot=1,position_ms=3700)])
        for mode in (1,2):
            self.deck.set_beat_count_mode(mode)
            for position in (99,100,599,600,950,1000,1149,1150,1600,3699,3700,4201):
                expected = countdown(times,[1150,3700],position)
                observed = self.deck.diagnostic_grid_position(position)
                self.assertEqual(observed, None if expected is None else
                                 (expected,255) if mode==2 else (expected//4,expected%4))
        self.deck.set_beat_count_mode(0)
        self.assertEqual(self.deck.diagnostic_grid_position(600),(1,2))
        self.deck.begin_track(62)
        self.assertTrue(self.deck.set_grid([[i%4+1,t,120] for i,t in enumerate(times)],62))
        self.deck.set_beat_count_mode(2)
        self.assertIsNone(self.deck.diagnostic_grid_position(600))

    def test_late_color_stream_upload_cannot_replace_new_track(self):
        detail = {"_blue_detail": bytes([31]) * 150,
                  "_rgb_detail": bytes.fromhex("e47c") * 150,
                  "_detail_bands": bytes([30, 40, 50]) * 150}
        self.deck.begin_track(41)
        self.assertTrue(self.deck._set_waveform_streams(detail, 150, generation=41))
        self.deck.begin_track(42)
        self.assertFalse(self.deck._set_waveform_streams(detail, 150, generation=41))
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (0, 0.0))
        self.assertTrue(self.deck._set_waveform_streams(detail, 150, generation=42))
        self.assertEqual(self.deck.diagnostic_analysis_shape(), (150, 150.0))

    def test_native_detail_delivery_never_builds_unused_qt_envelope(self):
        from desktop.player import PlayerBar
        from soundbox import track_analysis as ta
        from soundbox.waveform_detail import DETAIL_DIVISORS
        pb = PlayerBar()
        pb.audio.setVolume(0.0)
        try:
            self.assertTrue(pb.wave._native_rendering)
            pb._src_path = str(self.audio)
            pb._track_id = "rapid-cache"
            minimum = struct.pack("<h", -18000) * 240000
            maximum = struct.pack("<h", 21000) * 240000
            sections, count = ta._range_mip_sections(minimum, maximum, DETAIL_DIVISORS)
            detail = {"rate": 1000, "base_count": count, "encoding": ta.RANGE_MIP_ENCODING,
                      "mip_divisors": list(DETAIL_DIVISORS),
                      "mip_counts": [(count + d - 1) // d for d in DETAIL_DIVISORS],
                      "range_min_mips": [sections[f"detail_minimum_d{d}"] for d in DETAIL_DIVISORS],
                      "range_max_mips": [sections[f"detail_maximum_d{d}"] for d in DETAIL_DIVISORS]}
            for generation in range(1, 5):
                pb._analysis_generation = generation
                pb.player.begin_track(generation)
                pb.player.set_audio_detail(detail, generation)
                # Production delivery runs on the UI thread. This asserts the
                # absence of O(track duration) Qt work, without a timing flake.
                with mock.patch("desktop.player.array", side_effect=AssertionError("unused Qt envelope")):
                    pb._on_analysis_detail(str(self.audio), "", generation,
                                           {"_audio_detail": detail, "_native_uploaded": True})
                self.assertEqual(pb.player.diagnostic_detail_shape(), (240000, 1000.0))
                self.assertFalse(pb.wave._audio_detail)
        finally:
            pb.shutdown()
            pb.close()

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
                256.0, 384.0, 576.0, 864.0, 1152.0, 1500.0):
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

    def test_nested_waveform_group_reveals_only_the_completed_surface(self):
        import numpy as np

        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.begin_track(200)
        self.deck.set_waveform_mode('RGB')
        self.deck.set_zoom(92)
        self.deck.set_visual_position(1000, True)
        overview = {'_overview_heights': bytes([240]) * 300,
                    '_overview_rgb': bytes([14, 0, 0]) * 300, 'sample_rate': 150.0}
        detail = {'_blue_detail': bytes([255]) * 300,
                  '_rgb_detail': bytes.fromhex('e07c') * 300,
                  '_detail_bands': bytes([60, 45, 30]) * 300,
                  '_audio_detail': {'rate': 1000.0, 'base_count': 2000,
                      'mip_divisors': [1], 'mip_counts': [2000],
                      'range_min_mips': [struct.pack('<2000h', *([-8000] * 2000))],
                      'range_max_mips': [struct.pack('<2000h', *([8000] * 2000))],
                      'encoding': 'song-anchored-extrema-i16le-v1'}}
        self.deck._lib.sb_deck_begin_waveform_update(self.deck._handle)
        self.deck._lib.sb_deck_begin_waveform_update(self.deck._handle)
        try:
            # The compact silhouette is intentionally much taller than detail.
            self.assertTrue(self.deck.set_overview(overview, 200))
            QTest.qWait(50)  # retire any frame snapshotted before the outer group
            self.deck.diagnostic_begin_frame_trace(1, 64)
            self.deck.request_display()
            QTest.qWait(100)
            self.assertEqual(self.deck.diagnostic_frame_trace_status()['gpu_completed_frames'], 0)
            self.assertEqual(self.deck.set_complete_analysis(overview, detail, 200), (True, True))
        finally:
            self.deck._lib.sb_deck_end_waveform_update(self.deck._handle)
            # The remaining outer group must still suppress the completed
            # child's wake, without consuming the final static repaint.
            self.deck.request_display()
            QTest.qWait(100)
            held = self.deck.diagnostic_frame_trace_status()['gpu_completed_frames']
            self.deck._lib.sb_deck_end_waveform_update(self.deck._handle)
        self.assertEqual(held, 0)
        self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()['presentation_completed_frames'] == 1)
        def finish(name):
            output = Path(self.temp.name) / name
            self.deck.diagnostic_finish_frame_trace(output)
            info = json.loads((output / 'manifest.json').read_text())
            return np.frombuffer((output / 'frames.bgra').read_bytes(), dtype='u1').reshape(
                info['crop_height_pixels'], 64, 4)[30:-30, 16:48, :3].copy()
        first = finish('group-first')
        self.deck.diagnostic_begin_frame_trace(1, 64)
        self.deck.request_display()
        self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()['presentation_completed_frames'] == 1)
        np.testing.assert_array_equal(first, finish('group-static'))

    def test_progressive_overview_draws_both_halves_before_detail_arrives(self):
        import numpy as np

        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        detail = {
            "_blue_detail": bytes([255]) * 300,
            "_rgb_detail": bytes.fromhex("e07c") * 300,
            "_detail_bands": bytes([100, 80, 60]) * 300,
            "_audio_detail": {
                "rate": 1000.0, "base_count": 2000,
                "mip_divisors": [1], "mip_counts": [2000],
                "range_min_mips": [struct.pack("<2000h", *([-16000] * 2000))],
                "range_max_mips": [struct.pack("<2000h", *([16000] * 2000))],
                "encoding": "song-anchored-extrema-i16le-v1",
            },
        }
        for generation, (encoding, mode) in enumerate(
                ((encoding, mode) for encoding in ("u8", "linear-u16le")
                 for mode in ("BLUE", "RGB", "3BAND")), start=100):
            with self.subTest(encoding=encoding, mode=mode):
                self.assertTrue(self.deck.begin_track(generation))
                self.deck.set_waveform_mode(mode)
                self.deck.set_zoom(92)
                overview = {
                    "_overview_heights": (bytes([160]) * 300 if encoding == "u8"
                                          else struct.pack("<300H", *([40000] * 300))),
                    "_overview_rgb": bytes([14, 0, 0]) * 300,
                    "_height_encoding": encoding,
                    "sample_rate": 150.0,
                }
                self.assertTrue(self.deck.set_overview(overview, generation))
                self.deck.set_visual_position(1000, True)
                for stage in ("overview", "complete"):
                    if stage == "complete":
                        self.assertEqual(self.deck.set_complete_analysis(
                            overview, detail, generation), (True, True))
                    self.deck.diagnostic_begin_frame_trace(1, 64)
                    self.deck.request_display()
                    self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                        "gpu_completed_frames"] == 1)
                    self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                        "presentation_completed_frames"] == 1)
                    output = Path(self.temp.name) / f"progressive-{generation}-{stage}"
                    self.deck.diagnostic_finish_frame_trace(output)
                    manifest = json.loads((output / "manifest.json").read_text())
                    pixels = np.frombuffer((output / "frames.bgra").read_bytes(), dtype="u1")
                    frame = pixels.reshape(manifest["crop_height_pixels"], 64, 4)
                    # Trace strips are at the main lane's edges and the fixed
                    # playhead is central. Sample one interior column, away
                    # from both, on each side of the waveform's zero line.
                    column = frame[30:-30, 20, :3].astype(int)
                    colored = (column.max(1) > 50) & (column.max(1) - column.min(1) > 35)
                    half = len(colored) // 2
                    upper, lower = int(colored[:half].sum()), int(colored[-half:].sum())
                    self.assertGreater(upper, 16, (stage, upper, lower))
                    self.assertGreater(lower, 16, (stage, upper, lower))
                    self.assertLessEqual(abs(upper - lower), 2, (stage, upper, lower))

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

    def test_signed_range_display_table_matches_all_original_scalar_outputs(self):
        # Frozen from the pre-lookup arm64 scalar transfer for all int16 bit
        # patterns, including -32768. Compile the actual production helper;
        # no diagnostic export or duplicate implementation enters the dylib.
        source = native_deck_source()
        start = source.index('constexpr float kWaveformDisplayExponent')
        end = source.index('\nstruct WaveSample', start)
        unit = Path(self.temp.name) / 'signed-display-table.cpp'
        executable = unit.with_suffix('')
        unit.write_text('#include <algorithm>\n#include <array>\n#include <cmath>\n'
                        '#include <cstdint>\n#include <cstdio>\n' + source[start:end] +
                        '\nint main() { const auto &table = signedWaveformDisplayTable(); '
                        'return std::fwrite(table.data(), sizeof(int16_t), table.size(), stdout) '
                        '== table.size() ? 0 : 1; }\n')
        subprocess.run(['clang++', '-O3', '-std=c++20', '-arch', 'arm64',
                        '-mmacosx-version-min=14.0', str(unit), '-o', str(executable)],
                       check=True, capture_output=True)
        encoded = subprocess.check_output([str(executable)])
        self.assertEqual(len(encoded), 65536 * 2)
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         '398c6757103c026e126094b3b969d3dbda1e54c80b0674f1cd203fd9e815ab70')

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

    @unittest.skipUnless(shutil.which("lame"), "LAME encoder fixture unavailable")
    def test_mp3_header_and_native_decoder_use_the_same_origin(self):
        from soundbox.waveform_detail import mp3_origin
        target = Path(self.temp.name) / "origin.mp3"
        subprocess.run([shutil.which("lame"), "--silent", str(self.audio), str(target)],
                       check=True, capture_output=True)
        hint = mp3_origin(target)
        self.assertGreater(hint.lead_frames, 0)
        before = _sha256(target)
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        self.deck.setSource(QUrl.fromLocalFile(str(target)))
        expected = round((hint.decoded_frames + hint.lead_frames) / hint.sample_rate * 1000)
        self._wait_until(lambda: self.deck.duration() == expected)
        self.deck.setPosition(expected - 150)
        self.deck.play()
        self._wait_until(lambda: self.deck.position() >= expected - 10)
        self.assertFalse(errors)
        self.assertEqual(_sha256(target), before)

    def test_timeline_leadin_is_in_duration_seek_and_end_contract(self):
        # A synthetic one-second lead isolates the mapping from MP3 parsing.
        # The WAV still contains exactly two seconds of unmodified audio.
        before = _sha256(self.audio)
        self.assertTrue(self.deck._lib.sb_deck_load_timeline(
            self.deck._handle, str(self.audio).encode(), 44100, 88200, 44100.0))
        self._wait_until(lambda: self.deck.duration() == 3000)
        self.deck.setPosition(500)
        self.deck.play()
        self._wait_until(lambda: self.deck.position() > 550)
        self.deck.pause()
        self.deck.setPosition(2800)
        self.deck.play()
        self._wait_until(lambda: self.deck.position() >= 2990)
        self.assertEqual(_sha256(self.audio), before)

    def test_timeline_header_decoder_mismatch_fails_closed(self):
        errors = []
        self.deck.errorOccurred.connect(lambda _, message: errors.append(message))
        self.assertTrue(self.deck._lib.sb_deck_load_timeline(
            self.deck._handle, str(self.audio).encode(), 2257, 123, 44100.0))
        self._wait_until(lambda: bool(errors))
        self.assertIn("decoder clock differs", errors[-1])
        self.assertEqual(self.deck.duration(), 0)

    def test_native_zoom_returns_the_applied_renderer_scale(self):
        self.assertEqual(self.deck.set_zoom(-100.0), 24.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 24.0)
        self.assertEqual(self.deck.set_zoom(100.0), 92.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 92.0)
        self.assertEqual(self.deck.set_zoom(112.0), 128.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 128.0)
        self.assertEqual(self.deck.set_zoom(50_000.0), 1500.0)
        self.assertEqual(self.deck.diagnostic_zoom(), 1500.0)

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

    def test_hidden_audition_plays_with_native_waveform_and_no_canonical_record(self):
        from desktop.player import PlayerBar
        from PySide6.QtCore import QEventLoop, QTimer
        with tempfile.TemporaryDirectory(prefix='sb-hidden-native-') as area:
            bar = PlayerBar()
            bar.track_analysis_dir = Path(area) / 'canonical'
            bar.analysis_cache_admitted = lambda _tid: False
            bar._deck_volume = 0
            bar.show()
            try:
                bar.play_track('hidden', self.audio, autoplay=True)
                # Run a real Qt event loop while Python analysis prepares. A
                # repeated QTest.qWait loop starves its first DSP imports.
                loop = QEventLoop(); deadline = QTimer()
                deadline.setSingleShot(True); deadline.timeout.connect(loop.quit)
                bar._waveform_tasks.drained.connect(loop.quit)
                deadline.start(5000)
                if bar._waveform_tasks.pending: loop.exec()
                deadline.stop(); bar._waveform_tasks.drained.disconnect(loop.quit)
                self.assertEqual(bar._waveform_tasks.pending, 0)
                self._wait_until(bar.is_playing)
                self.assertGreater(bar.player.diagnostic_audio_detail_buffer_bytes(), 0)
                self.assertFalse(bar.track_analysis_dir.exists())
                self.assertEqual(bar.current_track(), 'hidden')
            finally:
                bar.shutdown()
                bar.close()

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
        # macOS may show a static drawable without delivering a nonzero
        # presentedTime callback. Check the actual playhead pixels after resize
        # instead of treating a missing callback as proof of a blank surface.
        from PySide6.QtCore import QPoint
        origin = self.host.mapToGlobal(QPoint(448, 132))
        pixels = self.host.screen().grabWindow(0, origin.x(), origin.y(), 6, 6).toImage()
        self.assertFalse(pixels.isNull())
        self.assertTrue(any(
            pixels.pixelColor(x, y).red() < 100
            and pixels.pixelColor(x, y).green() > 120
            and pixels.pixelColor(x, y).blue() > 120
            for x in range(pixels.width()) for y in range(pixels.height())))
        QTest.qWait(150)
        self.deck.diagnostic_reset_display_stats()
        QTest.qWait(150)
        self.assertLessEqual(self.deck.diagnostic_display_stats()[0], 1)

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

    def test_thin_signed_carrier_does_not_gain_a_retina_point_stroke(self):
        import numpy as np

        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.set_analysis({
            "heights": [0.5] * 300,
            "colors": [[255, 0, 0]] * 300,
            "sample_rate": 150.0,
        })
        self.assertTrue(self.deck._set_waveform_streams({
            "_blue_detail": bytes([31]) * 300,
            "_rgb_detail": bytes.fromhex("e07c") * 300,
            "_detail_bands": bytes([30, 40, 50]) * 300,
        }, 150.0))
        self.deck.set_audio_detail({
            "rate": 1000.0, "base_count": 2000,
            "mip_divisors": [1], "mip_counts": [2000],
            "range_min_mips": [struct.pack("<2000h", *([100] * 2000))],
            "range_max_mips": [struct.pack("<2000h", *([100] * 2000))],
            "encoding": "song-anchored-extrema-i16le-v1",
        })
        self.deck.set_waveform_mode("RGB")
        self.deck.set_zoom(1500)
        self.deck.setPosition(500)
        self.deck.diagnostic_begin_frame_trace(4, 64)
        self.deck.play()
        self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
            "submitted_frames"] == 4)
        self.deck.pause()
        output = Path(self.temp.name) / f"thin-carrier-{time.time_ns()}"
        self.deck.diagnostic_finish_frame_trace(output)
        manifest = json.loads((output / "manifest.json").read_text())
        pixels = np.frombuffer((output / "frames.bgra").read_bytes(), dtype="u1")
        pixels = pixels.reshape(4, manifest["crop_height_pixels"], 64, 4)
        # Away from the playhead and trace markers, a constant signed point
        # must stay visible without the previous 7.2-pixel Retina stroke.
        column = pixels[:, 30:-30, 20, :3].astype(int)
        colored = (column.max(2) > 50) & (column.max(2) - column.min(2) > 35)
        counts = colored.sum(1)
        self.assertTrue(np.all(counts >= 1), counts)
        self.assertTrue(np.all(counts <= 3), counts)

    def test_rgb_coarse_footprint_does_not_paint_fine_gaps_at_full_strength(self):
        import numpy as np

        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.set_analysis({"heights": [.5] * 300,
                                "colors": [[255, 0, 0]] * 300, "sample_rate": 150.0})
        self.assertTrue(self.deck._set_waveform_streams({
            "_blue_detail": bytes([31]) * 300,
            "_rgb_detail": bytes.fromhex("e07c") * 300,
            "_detail_bands": bytes([30, 40, 50]) * 300,
        }, 150.0))
        # Positive/negative halves each occupy half the pooled interval.
        # Symmetry makes the 1/2 occupancy independent of the height transfer.
        self.deck.set_audio_detail({
            "rate": 1000.0, "base_count": 2000,
            "mip_divisors": [1, 2], "mip_counts": [2000, 1000],
            "range_min_mips": [struct.pack('<2000h', *([0, -16384] * 1000)),
                               struct.pack('<1000h', *([-16384] * 1000))],
            "range_max_mips": [struct.pack('<2000h', *([16384, 0] * 1000)),
                               struct.pack('<1000h', *([16384] * 1000))],
            "encoding": "song-anchored-extrema-i16le-v1",
        })
        self.deck.set_waveform_mode('RGB')
        for zoom in (24,32,48,64,92,576,864):
            self.deck.set_zoom(zoom)
            self.deck.set_visual_position(1000 if zoom<=92 else 500, True)
            QTest.qWait(50)
            self.deck.diagnostic_begin_frame_trace(1, 64)
            self.deck.request_display()
            self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                'presentation_completed_frames'] == 1)
            self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                'gpu_completed_frames'] == 1)
            output = Path(self.temp.name) / f'occupancy-{zoom}-{time.time_ns()}'
            self.deck.diagnostic_finish_frame_trace(output)
            manifest = json.loads((output / 'manifest.json').read_text())
            frame = np.frombuffer((output / 'frames.bgra').read_bytes(), dtype='u1').reshape(
                manifest['crop_height_pixels'], 64, 4)
            # The user selected 92 pt/s as the fixed palette reference. The
            # same song area now keeps its 87.5% linear-light contrast at every
            # zoom, including the former wide/close brightness discontinuities.
            expected = [2,2,241]
            pixel = frame[manifest['crop_height_pixels'] // 2, 20, :3].astype(int)
            self.assertLessEqual(int(np.abs(pixel - expected).max()), 2)

    def test_each_waveform_mode_keeps_its_palette_across_all_thirteen_zooms(self):
        import numpy as np
        self.host.resize(800,166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda:self.deck.duration()>0)
        self.deck.set_analysis({'heights':[.5]*300,'colors':[[255,0,0]]*300,'sample_rate':150.0})
        self.assertTrue(self.deck._set_waveform_streams({'_blue_detail':bytes([255])*300,
            '_rgb_detail':bytes.fromhex('e07c')*300,'_detail_bands':bytes([60,45,30])*300},150.0))
        self.deck.set_audio_detail({'rate':1000.0,'base_count':2000,'mip_divisors':[1,2],
            'mip_counts':[2000,1000],'range_min_mips':[struct.pack('<2000h',*([-16000]*2000)),struct.pack('<1000h',*([-16000]*1000))],
            'range_max_mips':[struct.pack('<2000h',*([16000]*2000)),struct.pack('<1000h',*([16000]*1000))],
            'encoding':'song-anchored-extrema-i16le-v1'})
        for mode in ('BLUE','RGB','3BAND'):
            self.deck.set_waveform_mode(mode);reference=None
            for zoom in (92,24,32,48,64,128,184,256,384,576,864,1152,1500):
                self.deck.set_zoom(zoom);self.deck.set_visual_position(1000,True);QTest.qWait(30)
                self.deck.diagnostic_begin_frame_trace(1,64);self.deck.request_display()
                self._wait_until(lambda:self.deck.diagnostic_frame_trace_status()['gpu_completed_frames']==1)
                self._wait_until(lambda:self.deck.diagnostic_frame_trace_status()['presentation_completed_frames']==1)
                output=Path(self.temp.name)/f'fixed-palette-{mode}-{zoom}-{time.time_ns()}'
                self.deck.diagnostic_finish_frame_trace(output)
                manifest=json.loads((output/'manifest.json').read_text())
                frame=np.frombuffer((output/'frames.bgra').read_bytes(),dtype='u1').reshape(manifest['crop_height_pixels'],64,4)
                # Sample filled interiors at several heights; no ruler, cues,
                # playhead, envelope boundaries or transparent fine gaps.
                palette=frame[[len(frame)//2-8,len(frame)//2,len(frame)//2+8],20,:3].astype(int)
                if reference is None:reference=palette
                with self.subTest(mode=mode,zoom=zoom):
                    self.assertLessEqual(int(np.abs(palette-reference).max()),2)

    def test_rgb_palette_is_not_dimmed_by_zero_compact_height(self):
        import numpy as np

        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(self.audio)))
        self._wait_until(lambda: self.deck.duration() > 0)
        self.deck.set_analysis({"heights": [.5] * 300,
                                "colors": [[255, 255, 255]] * 300,
                                "sample_rate": 150.0})
        self.deck.set_audio_detail({
            "rate": 1000.0, "base_count": 2000,
            "mip_divisors": [1], "mip_counts": [2000],
            "range_min_mips": [struct.pack("<2000h", *([-8000] * 2000))],
            "range_max_mips": [struct.pack("<2000h", *([8000] * 2000))],
            "encoding": "song-anchored-extrema-i16le-v1",
        })
        self.deck.set_waveform_mode("RGB")
        self.deck.set_visual_position(500, True)
        palettes = ((7, 0, 0), (0, 7, 0), (0, 0, 7), (7, 7, 0),
                    (7, 0, 7), (0, 7, 7), (7, 7, 7), (0, 6, 2))
        for zoom in (32, 128):
            self.deck.set_zoom(zoom)
            for red, green, blue in palettes:
                frames = []
                for height in (31, 0):
                    code = (red << 13) | (green << 10) | (blue << 7) | (height << 2)
                    self.assertTrue(self.deck._set_waveform_streams({
                        "_blue_detail": bytes([31]) * 300,
                        "_rgb_detail": code.to_bytes(2, "big") * 300,
                        "_detail_bands": bytes([30, 40, 50]) * 300,
                    }, 150.0))
                    self.deck.diagnostic_begin_frame_trace(1, 64)
                    self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                        "presentation_completed_frames"] == 1)
                    output = Path(self.temp.name) / f"palette-{time.time_ns()}"
                    self.deck.diagnostic_finish_frame_trace(output)
                    manifest = json.loads((output / "manifest.json").read_text())
                    frame = np.frombuffer((output / "frames.bgra").read_bytes(), dtype="u1")
                    frames.append(frame.reshape(manifest["crop_height_pixels"], 64, 4))
                # Signed detail owns identical geometry. This also executes the
                # complete production kernel, catching a disconnected helper.
                np.testing.assert_array_equal(frames[0][30:-30, 5:25],
                                              frames[1][30:-30, 5:25])
                self.assertGreater(int(frames[1][30:-30, 5:25, :3].max()), 150)

    def test_synthetic_drawables_translate_and_recenter_at_every_zoom(self):
        """Rendered pixels, not shader strings; no live library/audio fixture."""
        import numpy as np

        audio = Path(self.temp.name) / "render-96s.wav"
        with wave.open(str(audio), "wb") as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(44100)
            out.writeframes(bytes(96 * 44100 * 4))
        before = _sha256(audio)
        self.host.resize(800, 166)
        self.deck.setSource(QUrl.fromLocalFile(str(audio)))
        self._wait_until(lambda: self.deck.duration() >= 96000)
        count = 96 * 150
        self.deck.set_analysis({"heights": [.7] * count,
                                "colors": [[255, 0, 0]] * count,
                                "sample_rate": 150.0})
        rgb = (np.arange(count) // 11) % 3
        self.assertTrue(self.deck._set_waveform_streams({
            "_blue_detail": bytes([24]) * count,
            "_rgb_detail": np.array([0xe07c, 0x03fc, 0x1c7c], dtype="<u2")[rgb].tobytes(),
            "_detail_bands": bytes([24, 42, 58]) * count,
        }, 150.0))
        centers = .55 * np.sin(np.arange(96000) * .17)
        self.deck.set_audio_detail({
            "rate": 1000.0, "base_count": 96000,
            "mip_divisors": [1], "mip_counts": [96000],
            "range_min_mips": [np.rint((centers-.08)*32767).astype("<i2").tobytes()],
            "range_max_mips": [np.rint((centers+.08)*32767).astype("<i2").tobytes()],
            "encoding": "song-anchored-extrema-i16le-v1",
        })
        self.deck.sync_view_geometry()
        self.app.processEvents()
        parent_width, _, drawable_width = self.deck.diagnostic_view_geometry()
        scale = drawable_width / parent_width

        last_record = {}

        def capture(position):
            self.deck.set_visual_position(position, True)
            QTest.qWait(25)
            self.deck.diagnostic_begin_frame_trace(1, 512)
            self.deck.request_display()
            # Drain callbacks before the synchronous exporter; blocking the Qt
            # event loop early can strand presentation callbacks until timeout.
            self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                "presentation_completed_frames"] == 1)
            self._wait_until(lambda: self.deck.diagnostic_frame_trace_status()[
                "gpu_completed_frames"] == 1)
            output = Path(self.temp.name) / f"audit-frame-{time.time_ns()}"
            self.deck.diagnostic_finish_frame_trace(output)
            info = json.loads((output / "manifest.json").read_text())
            self.assertEqual(info["gpu_completed_frames"], 1)
            with (output / "frames.csv").open() as handle:
                last_record.clear()
                last_record.update(next(csv.DictReader(handle)))
            return np.frombuffer((output / "frames.bgra").read_bytes(), dtype="u1").reshape(
                info["crop_height_pixels"], 512, 4).copy()

        for mode in ("RGB", "BLUE", "3BAND"):
            self.deck.set_waveform_mode(mode)
            for zoom in (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500):
                with self.subTest(mode=mode, zoom=zoom):
                    self.deck.set_zoom(zoom)
                    # Include a non-whole-second origin at the reported 864
                    # stop: exact-second phases alone can miss a cell boundary.
                    for base in ((50000, 42704) if zoom == 864 else (50000,)):
                        first = capture(base)
                        first_record = dict(last_record)
                        continuous_rgb = mode == "RGB" and zoom not in (64, 128, 184, 576, 864)
                        point_rate = round(zoom)
                        common = math.gcd(44100, point_rate)
                        shift = round(point_rate / common * scale)
                        self.assertLessEqual(shift, 128)
                        moved = capture(base + 1000/common)
                        # Preview is audio-frame quantized. Choose travel that is
                        # exactly representable in BOTH samples and logical points,
                        # rather than mistaking rounded preview time for a render bug.
                        # Ignore markers, playhead and grid margins.
                        a = first[30:-30, 5+shift:105+shift, :3].astype(int)
                        b = moved[30:-30, 5:105, :3].astype(int)
                        self.assertLessEqual(int(np.abs(a-b).max()), 2)
                        self.assertGreater(int(a.max()), 40, "blank renders must not pass")
                        if mode in ("RGB", "BLUE") and base == 50000:
                            # Stay inside both the physical-pixel wide quantum
                            # and the logical-point close quantum on this screen.
                            # Opaque fill AND fine contour retain their pixels.
                            phase = capture(base + 200.0 / zoom)
                            if continuous_rgb:
                                # Read the rendered frame's own clock ledger:
                                # subpixel travel must follow source samples,
                                # without quantizing back onto physical pixels.
                                frames = (int(last_record['source_frame'])
                                          - int(first_record['source_frame']))
                                expected = frames / 44100 * zoom * scale * 4
                                actual = (float(last_record['attribute_offset'])
                                          - float(first_record['attribute_offset']))
                                self.assertAlmostEqual(actual, expected, delta=.001)
                                self.assertFalse(np.array_equal(
                                    first[30:-30, 5:105, :3],
                                    phase[30:-30, 5:105, :3]))
                            else:
                                np.testing.assert_array_equal(
                                    first[30:-30, 5:105, :3],
                                    phase[30:-30, 5:105, :3])
                        if (mode in ("RGB", "BLUE") and zoom <= 384
                                and base == 50000 and not continuous_rgb):
                            # A 0.75-physical-pixel move must advance exactly one
                            # pixel, not remain frozen at the old two-pixel Retina
                            # quantum. The aligned contour must retain its pixels.
                            one_pixel = capture(base + 750.0 / (zoom * scale))
                            delta = np.abs(
                                first[30:-30, 6:106, :3].astype(int)
                                - one_pixel[30:-30, 5:105, :3].astype(int))
                            self.assertLessEqual(int(delta.max()), 2)
                        if mode == "RGB" and zoom in (576, 864) and base == 50000:
                            # The native reference moves in LOGICAL pixels.
                            # A physical-pixel-only fix still shimmered on Retina;
                            # protect both the hold and one-point boundary.
                            held = capture(base + 400.0 / zoom)
                            np.testing.assert_array_equal(
                                first[30:-30, 5:105, :3], held[30:-30, 5:105, :3])
                            advanced = capture(base + 600.0 / zoom)
                            quantum = round(scale)
                            delta = np.abs(
                                first[30:-30, 5+quantum:105+quantum, :3].astype(int)
                                - advanced[30:-30, 5:105, :3].astype(int))
                            self.assertLessEqual(int(delta.max()), 2)
                            self.assertAlmostEqual(
                                float(last_record['attribute_offset'])
                                - float(first_record['attribute_offset']),
                                scale * 4, delta=.001)
                        capture(0)  # forces a different attribute buffer center
                        returned = capture(base)
                        np.testing.assert_array_equal(first, returned)
                        # Regenerate around a DIFFERENT nearby origin, then return
                        # while retaining that overlapping tile. Returning straight
                        # from zero only recreated the same origin and missed cell
                        # boundary rounding that changed the outline during playback.
                        for origin_offset in (-137.27, 83.47):
                            capture(0)
                            capture(base + origin_offset)
                            overlap = capture(base)
                            delta = np.abs(first.astype(int) - overlap.astype(int))
                            self.assertLessEqual(int(delta.max()), 2,
                                f"overlapping tile changed: {mode} {zoom} {origin_offset}")
        self.assertEqual(_sha256(audio), before)

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
        self.assertEqual(self.deck.diagnostic_zoom(), 1500.0)
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
