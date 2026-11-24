"""Real transport/gesture controls over a silent fake deck; no audio or sources."""
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from desktop.native_deck import PlaybackState
from desktop.player import PlayerBar


class Deck:
    def __init__(self, playing):
        self.state = PlaybackState.PLAYING if playing else PlaybackState.PAUSED
        self.pos = 20_000
        self.positions = []
        self.plays = 0
        self.visual = []

    def playbackState(self): return self.state
    def playbackRate(self): return 1.0
    def position(self): return self.pos
    def pause(self): self.state = PlaybackState.PAUSED
    def play(self):
        self.state = PlaybackState.PLAYING
        self.plays += 1
    def stop(self):
        self.state = PlaybackState.STOPPED
        self.pos = 0
    def setPosition(self, position):
        self.pos = position
        self.positions.append(position)
    def setSource(self, _url):
        self.pos = 0
        self.state = PlaybackState.PAUSED
    def begin_track(self, _generation): pass
    def set_cues(self, _cues): pass
    def set_visual_position(self, position, preview): self.visual.append((position, preview))


def mouse(kind, x=300, y=100):
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton,
                       Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton,
                       Qt.NoModifier)


class _PlayerFixture:
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def player(self, playing=True):
        with mock.patch('desktop.player.NativeDeckBackend', side_effect=RuntimeError('silent test deck')):
            pb = PlayerBar()
        original = pb.player
        deck = Deck(playing)
        pb.player = deck
        pb._playback_available = True
        pb._native_error = ''
        pb._track_id = 'A'
        pb._src_path = '/synthetic/A.wav'
        pb._dur = 60_000
        pb._pos = 20_000
        pb._ordinary_playback_requested = playing
        pb._transport_play_active = playing
        pb._start_waveform = mock.Mock()
        pb._animate_gain = lambda _end, _duration, completion=None: completion() if completion else None
        pb.wave.resize(600, 200)
        pb.wave.set_active(True)
        pb.wave.set_timing(20_000, 60_000)
        def cleanup():
            pb.player = original
            pb.shutdown()
            pb.deleteLater()
        self.addCleanup(cleanup)
        return pb, deck

    def grab(self, pb, overview=False):
        pb.wave.mousePressEvent(mouse(QEvent.MouseButtonPress, y=10 if overview else 100))
        self.assertTrue(pb._deck_drag_active)
        self.assertTrue(pb.wave.is_dragging())

    def release(self, pb, overview=False):
        pb.wave.mouseReleaseEvent(mouse(QEvent.MouseButtonRelease, y=10 if overview else 100))


class PlayerGestureFreshness(_PlayerFixture, unittest.TestCase):
    def test_stop_pause_error_and_cue_supersede_held_main_or_overview_gesture(self):
        for overview in (False, True):
            for command in ('stop', 'pause', 'error', 'cue', 'seek', 'toggle'):
                with self.subTest(overview=overview, command=command):
                    pb, deck = self.player()
                    self.grab(pb, overview)
                    if command == 'error': pb._on_error(None, 'Synthetic decode failure')
                    elif command == 'cue': pb._marker_cue_press('hot', 0, 7_123)
                    elif command == 'seek': pb.seek_ms(12_345)
                    else: getattr(pb, command)()
                    if command in ('stop', 'pause', 'error', 'toggle'):
                        self.assertNotEqual(deck.state, PlaybackState.PLAYING)
                    self.assertFalse(pb._deck_drag_active)
                    self.assertFalse(pb.wave.is_dragging())
                    before = (deck.state, deck.pos, list(deck.positions), deck.plays)
                    self.release(pb, overview)
                    pb._after_drag_paused()  # an obsolete completion is inert too
                    self.assertEqual((deck.state, deck.pos, deck.positions, deck.plays), before)
                    self.assertFalse(deck.visual[-1][1])

    def test_load_releases_old_drag_without_seeking_or_starting_replacement(self):
        for autoplay in (False, True, None):
            with self.subTest(autoplay=autoplay):
                pb, deck = self.player()
                self.grab(pb)
                pb.play_track('B', '/synthetic/B.wav', analysis_path='/synthetic/B.DAT', autoplay=autoplay)
                pb._pending_start_ms = 1_234
                pb._waveform_ready = True  # this test isolates obsolete drag release
                pb._on_dur(90_000)
                self.assertEqual(pb.current_track(), 'B')
                self.assertEqual(deck.pos, 1_234)
                self.assertEqual(deck.state == PlaybackState.PLAYING, autoplay is not False)
                before = (deck.state, deck.pos, list(deck.positions), deck.plays)
                self.release(pb)
                self.assertEqual((deck.state, deck.pos, deck.positions, deck.plays), before)

    def test_ordinary_drag_seeks_once_and_retains_play_intent_without_beat_snap(self):
        for playing in (False, True):
            for overview in (False, True):
                with self.subTest(playing=playing, overview=overview):
                    pb, deck = self.player(playing)
                    pb.wave.set_grid([[1, 20_000, 120], [2, 30_123, 120]])
                    self.grab(pb, overview)
                    pb.wave.mouseMoveEvent(mouse(QEvent.MouseMove, x=301, y=10 if overview else 100))
                    self.assertEqual(deck.positions, [])
                    target = pb.wave._drag_preview_ms
                    self.assertNotIn(target, (20_000, 30_123))
                    pb.wave.mouseReleaseEvent(mouse(QEvent.MouseButtonRelease, x=301, y=10 if overview else 100))
                    self.assertEqual(len(deck.positions), 1)
                    self.assertAlmostEqual(deck.positions[0], target)
                    self.assertEqual(deck.plays, int(playing))
                    self.assertEqual(deck.state == PlaybackState.PLAYING, playing)

    def test_manual_seek_jump_or_grab_invalidates_deferred_eof_but_fresh_eof_advances_once(self):
        for command in ('seek', 'fraction', 'jump', 'grab'):
            with self.subTest(command=command):
                pb, deck = self.player()
                ended = []
                pb.track_ended.connect(ended.append)
                pb._on_end_of_media()
                if command == 'seek': pb.seek_ms(5_000)
                elif command == 'fraction': pb._seek(.25)
                elif command == 'jump':
                    pb._bpm = 120
                    pb._jump_beats = 4
                    pb._beat_jump(1)
                else: self.grab(pb)
                self.app.processEvents()
                self.assertEqual(ended, [])
                if command == 'grab': self.release(pb)
                pb._ordinary_playback_requested = True
                pb._on_end_of_media()
                pb._on_end_of_media()
                self.app.processEvents()
                self.assertEqual(ended, ['A'])

    def test_new_drag_invalidates_old_seek_fade_and_new_stop_invalidates_resume_fade(self):
        pb, deck = self.player()
        callbacks = []
        # Keep the real epoch-tagged gain completion; only the backend ramp is held.
        del pb._animate_gain
        deck.ramp_gain = lambda _end, _duration, callback: callbacks.append(callback)
        pb.seek_ms(5_000)
        self.assertTrue(pb._pause_pending)
        self.grab(pb)
        callbacks.pop(0)()
        self.assertEqual(deck.positions, [])
        self.release(pb)
        self.assertEqual(len(deck.positions), 1)
        pb.stop()
        for callback in callbacks: callback()
        self.assertEqual(deck.state, PlaybackState.STOPPED)
        self.assertEqual(deck.plays, 1)

    def test_seeking_during_explicit_pause_fade_retires_pause_without_resuming(self):
        pb, deck = self.player()
        callbacks = []
        del pb._animate_gain
        deck.ramp_gain = lambda _end, _duration, callback: callbacks.append(callback)
        pb.pause()
        self.assertTrue(pb._pause_pending)
        pb.seek_ms(4_321)
        callbacks.pop(0)()
        self.assertFalse(pb._pause_pending)
        self.assertEqual(deck.positions[-1], 4_321)
        self.assertEqual(deck.plays, 0)
        self.assertEqual(deck.state, PlaybackState.PAUSED)


class CompleteDeckLoading(_PlayerFixture, unittest.TestCase):
    """Held waveform delivery must never allow audio to run ahead of its surface."""
    def pending(self, autoplay=True, *, analysis_path='/synthetic/B.DAT'):
        pb, deck = self.player(False)
        pb.play_track('B', '/synthetic/B.wav', analysis_path=analysis_path, autoplay=autoplay)
        ready = []
        pb.track_ready.connect(lambda: ready.append(True))
        self.manifest(pb)
        pb._on_dur(60_000)
        self.assertEqual(deck.plays, 0)
        self.assertTrue(pb._pending_autoplay)
        self.assertFalse(ready)
        return pb, deck, ready

    @staticmethod
    def manifest(pb):
        pb._on_analysis_manifest(pb._src_path, pb._analysis_path, pb._analysis_generation,
            {'beats': [[1,625,120],[2,1125,120]], 'provenance': {'grid':'rekordbox'}})

    @staticmethod
    def complete(pb):
        detail = {'_audio_detail': {'rate':1000, 'heights':b'\x80\xff'},
                  '_waveform_complete':True, '_native_uploaded':True}
        pb._on_analysis_overview(pb._src_path, pb._analysis_path, pb._analysis_generation,
            {**detail, '_overview_heights':b'\x80\xff', '_overview_rgb':b'\x0e\0\0'*2})
        pb._on_analysis_detail(pb._src_path, pb._analysis_path, pb._analysis_generation, detail)

    def test_manifest_decoder_and_overview_never_start_before_complete_detail(self):
        for autoplay in (False,True):
            with self.subTest(autoplay=autoplay):
                pb,deck,ready=self.pending(autoplay)
                pb._on_analysis_overview(pb._src_path,pb._analysis_path,pb._analysis_generation,
                    {'_overview_heights':b'\xff','_overview_rgb':b'\x0e\0\0','_native_uploaded':True})
                self.assertEqual(deck.plays,0);self.assertFalse(ready)
                self.complete(pb)
                self.assertEqual(deck.pos,625);self.assertEqual(deck.plays,int(autoplay))
                self.assertEqual(ready,[True]);self.assertFalse(pb._pending_autoplay)

    def test_paused_first_beat_precedes_held_surface_without_a_second_completion_seek(self):
        for autoplay in (False, True):
            with self.subTest(autoplay=autoplay):
                pb, deck, ready = self.pending(autoplay)
                self.assertEqual((deck.pos, pb._pos), (625, 625))
                self.assertEqual(deck.positions, [625])
                self.assertEqual(deck.plays, 0)
                self.assertFalse(ready)
                pb._on_dur(60_000); self.manifest(pb)
                self.assertEqual(deck.positions, [625])
                self.complete(pb)
                self.assertEqual(deck.positions, [625])
                self.assertEqual(deck.plays, int(autoplay))
                self.assertEqual(ready, [True])

    def test_no_analysis_path_does_not_skip_complete_surface_gate(self):
        pb,deck,ready=self.pending(False,analysis_path='')
        pb.toggle();self.assertEqual(deck.plays,0)
        self.complete(pb);self.assertEqual(deck.plays,1);self.assertEqual(ready,[True])

    def test_pending_space_toggles_latest_intent_and_pause_stop_cancel_start(self):
        for command in ('pause','stop','space','double-space'):
            with self.subTest(command=command):
                pb,deck,ready=self.pending(True)
                if command in ('space','double-space'):
                    pb.toggle()
                    if command=='double-space':pb.toggle()
                else:getattr(pb,command)()
                self.assertEqual(deck.plays,0)
                self.manifest(pb);self.complete(pb)
                self.assertEqual(deck.plays,int(command=='double-space'))
                self.assertEqual(deck.pos,0 if command=='stop' else 625)
                self.assertEqual(ready,[True])

    def test_seek_during_pending_load_preserves_target_against_late_manifest(self):
        for autoplay in (False,True):
            with self.subTest(autoplay=autoplay):
                pb,deck,_ready=self.pending(autoplay)
                pb.seek_ms(12_345);self.manifest(pb);self.complete(pb)
                self.assertEqual(deck.pos,12_345);self.assertEqual(deck.plays,int(autoplay))

    def test_cue_release_or_stop_wins_when_complete_waveform_precedes_duration(self):
        for command in ('cue-release', 'stop'):
            with self.subTest(command=command):
                pb, deck = self.player(False)
                pb.play_track('B', '/synthetic/B.wav', analysis_path='/synthetic/B.DAT', autoplay=True)
                self.manifest(pb); self.complete(pb)
                self.assertTrue(pb._waveform_ready)
                self.assertTrue(pb._pending_autoplay)
                self.assertEqual(deck.plays, 0)
                if command == 'cue-release':
                    pb._marker_cue_press('memory', 0, 23_456)
                    pb._marker_cue_release('memory', 0)
                else: pb.stop()
                target = 23_456 if command == 'cue-release' else 0
                seeks, plays = list(deck.positions), deck.plays
                pb._on_dur(60_000)
                self.assertEqual((deck.pos, pb._pos), (target, target))
                self.assertEqual(deck.positions, seeks)
                self.assertEqual(deck.plays, plays)
                self.assertEqual(deck.state, PlaybackState.PAUSED if command == 'cue-release' else PlaybackState.STOPPED)

    def test_held_released_and_latched_cues_wait_for_surface_without_late_restart(self):
        for kind in ('hot','memory','session'):
            for gesture in ('held','released','latched'):
                with self.subTest(kind=kind,gesture=gesture):
                    pb,deck,ready=self.pending(False)
                    if kind == 'session': pb._cue_press()
                    else: pb._marker_cue_press(kind,0,23_456)
                    if gesture=='latched':pb.toggle()
                    if gesture!='held':
                        if kind == 'session': pb._cue_release()
                        else: pb._marker_cue_release(kind,0)
                    self.assertEqual(deck.plays,0)
                    self.manifest(pb);self.complete(pb)
                    self.assertEqual(deck.pos,625 if kind == 'session' else 23_456)
                    self.assertEqual(deck.plays,int(gesture!='released'))
                    self.assertEqual(ready,[True])
                    if gesture=='held':
                        if kind == 'session': pb._cue_release()
                        else: pb._marker_cue_release(kind,0)
                        self.assertEqual(deck.state,PlaybackState.PAUSED)
                    elif gesture=='latched':self.assertEqual(deck.state,PlaybackState.PLAYING)

    def test_old_surface_cannot_ready_or_play_newer_track(self):
        pb,deck,ready=self.pending(True);old=(pb._src_path,pb._analysis_path,pb._analysis_generation)
        pb.play_track('C','/synthetic/C.wav',analysis_path='/synthetic/C.DAT',autoplay=False)
        pb._on_analysis_detail(*old,{'_waveform_complete':True})
        self.assertFalse(pb._waveform_ready);self.assertEqual(deck.plays,0);self.assertFalse(ready)
        self.manifest(pb);pb._on_dur(60_000);self.complete(pb)
        self.assertEqual(deck.plays,0);self.assertEqual(ready,[True])

    def test_second_space_ends_pending_cue_latch_and_session_relocation_stays_paused(self):
        for kind in ('hot', 'memory', 'session', 'relocated-session'):
            with self.subTest(kind=kind):
                pb, deck, ready = self.pending(kind == 'relocated-session')
                if kind == 'relocated-session': pb.seek_ms(12_345)
                if kind in ('session', 'relocated-session'): pb._cue_press()
                else: pb._marker_cue_press(kind, 0, 23_456)
                if kind != 'relocated-session':
                    pb.toggle(); pb.toggle()
                if kind in ('session', 'relocated-session'): pb._cue_release()
                else: pb._marker_cue_release(kind, 0)
                self.manifest(pb); self.complete(pb)
                self.assertEqual(deck.plays, 0)
                self.assertEqual(deck.pos, 12_345 if kind == 'relocated-session' else
                                 625 if kind == 'session' else 23_456)
                self.assertEqual(ready, [True])

    def test_failed_current_surface_reports_error_and_only_explicit_play_retries(self):
        pb,deck,ready=self.pending(True);errors=[];pb.playback_error.connect(errors.append)
        worker=SimpleNamespace(path=pb._src_path,analysis_path=pb._analysis_path,
            generation=pb._analysis_generation,error='synthetic section failure',
            isInterruptionRequested=lambda:False)
        pb._start_waveform.reset_mock();pb._wf_done(worker)
        self.assertEqual(len(errors),1);self.assertIn('Waveform preparation failed',errors[0])
        self.assertEqual(deck.plays,0);self.assertFalse(ready);pb._start_waveform.assert_not_called()
        pb.toggle();pb._start_waveform.assert_called_once_with(pb._src_path,pb._analysis_path)
        self.assertEqual(deck.plays,0);self.manifest(pb);self.complete(pb)
        self.assertEqual(deck.plays,1);self.assertEqual(ready,[True])
