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


class RefillDeck(Deck):
    """Native Play can remain PAUSED until a later PCM refill."""
    def __init__(self):
        super().__init__(False)
        self.requested = False
        self.pauses = 0

    def play(self):
        self.requested = True
        self.plays += 1

    def pause(self):
        self.requested = False
        self.pauses += 1
        self.state = PlaybackState.PAUSED

    def stop(self):
        self.requested = False
        super().stop()

    def refill(self):
        if self.requested:
            self.state = PlaybackState.PLAYING


class PendingNativePlayback(_PlayerFixture, unittest.TestCase):
    def test_second_play_explicit_pause_and_stop_cancel_post_ready_native_start(self):
        for command in ('toggle', 'pause', 'stop', 'latched-cue'):
            with self.subTest(command=command):
                pb, _ = self.player(False)
                deck = RefillDeck(); pb.player = deck
                pb._update_transport()
                self.assertTrue(pb.play_btn.isEnabled())
                self.assertTrue(pb._waveform_ready)
                self.assertFalse(pb._pending_autoplay)
                if command == 'latched-cue':
                    pb._marker_cue_press('hot', 0, 7_123)
                    pb.play_btn.click()
                    self.assertTrue(pb._cue_preview_latched)
                    pb._marker_cue_release('hot', 0)
                else:
                    pb.play_btn.click()
                self.assertTrue(deck.requested)
                self.assertEqual(deck.state, PlaybackState.PAUSED)
                if command in ('toggle', 'latched-cue'):
                    pb.play_btn.click()
                else:
                    getattr(pb, command)()
                self.assertFalse(deck.requested)
                self.assertFalse(pb._transport_play_active)
                self.assertFalse(pb._wants_ordinary_playback())
                self.assertEqual(pb.play_btn.text(), '▶')
                deck.refill()
                self.assertNotEqual(deck.state, PlaybackState.PLAYING)
                if command != 'stop':
                    self.assertGreater(deck.pauses, 0)

    def test_paused_refill_poll_and_focus_do_not_erase_requested_play(self):
        pb, _ = self.player(False)
        deck = RefillDeck(); pb.player = deck
        pb.toggle()
        pb._on_state(PlaybackState.PAUSED)
        pb.resynchronize_transport()
        self.assertTrue(pb._transport_play_active)
        self.assertTrue(pb._wants_ordinary_playback())
        self.assertEqual(pb.play_btn.text(), '❚❚')
        deck.refill()
        pb._on_state(deck.state)
        self.assertTrue(pb.is_playing())
        self.assertEqual(deck.plays, 1)

    def test_new_source_replaces_pending_native_play_without_inheriting_its_request(self):
        pb, _ = self.player(False)
        deck = RefillDeck(); pb.player = deck
        original = deck.setSource

        def source(url):
            deck.requested = False
            original(url)

        deck.setSource = source
        pb.toggle()
        pb.play_track('B', '/synthetic/B.wav', autoplay=False)
        deck.refill()
        self.assertEqual(pb.current_track(), 'B')
        self.assertFalse(deck.requested)
        self.assertNotEqual(deck.state, PlaybackState.PLAYING)


class TrackLoadOwnership(_PlayerFixture, unittest.TestCase):
    def test_accepted_conversion_retargets_only_exact_owned_fade_without_new_intent(self):
        pb, deck = self.player(True)
        ramps = []
        del pb._animate_gain
        deck.ramp_gain = lambda _gain, _ms, done: ramps.append(done)
        started, cancelled = mock.Mock(), mock.Mock()
        pb.play_track('B', '/synthetic/B.wav', on_started=started, on_cancelled=cancelled)
        epoch = pb._transport_epoch
        self.assertFalse(pb.rebind_pending_path('A', '/synthetic/B.wav', '/synthetic/new.wav'))
        self.assertFalse(pb.rebind_pending_path('B', '/synthetic/stale.wav', '/synthetic/new.wav'))
        self.assertTrue(pb.rebind_pending_path('B', '/synthetic/B.wav', '/synthetic/new.wav'))
        self.assertEqual(pb._transport_epoch, epoch)
        cancelled.assert_not_called(); started.assert_not_called()
        ramps[0]()
        self.assertEqual(pb.current_track(), 'B')
        self.assertEqual(pb._src_path, '/synthetic/new.wav')
        started.assert_called_once_with(); cancelled.assert_not_called()
        self.assertFalse(pb.rebind_pending_path('B', '/synthetic/new.wav', '/synthetic/later.wav'))

    def test_deferred_load_starts_once_and_does_not_cancel_its_own_continuation(self):
        pb, deck = self.player(True)
        ramps = []
        del pb._animate_gain
        deck.ramp_gain = lambda _gain, _ms, done: ramps.append(done)
        started, cancelled = mock.Mock(), mock.Mock()
        pb.play_track('B', '/synthetic/B.wav', on_started=started, on_cancelled=cancelled)
        self.assertEqual(pb.current_track(), 'A')
        started.assert_not_called(); cancelled.assert_not_called()
        ramps[0]()
        self.assertEqual(pb.current_track(), 'B')
        started.assert_called_once_with(); cancelled.assert_not_called()
        ramps[0]()
        started.assert_called_once_with()
        pb._on_error(None, 'Accepted B later failed')
        cancelled.assert_not_called()

    def test_newer_commands_cancel_unstarted_load_once_and_old_completion_is_inert(self):
        for command in ('pause', 'stop', 'seek', 'cue', 'source', 'same-request', 'shutdown'):
            with self.subTest(command=command):
                pb, deck = self.player(True)
                ramps = []
                del pb._animate_gain
                deck.ramp_gain = lambda _gain, _ms, done: ramps.append(done)
                started, cancelled = mock.Mock(), mock.Mock()
                pb.play_track('B', '/synthetic/B.wav', on_started=started, on_cancelled=cancelled)
                stale = ramps[0]
                if command == 'seek': pb.seek_ms(5_000)
                elif command == 'cue': pb._marker_cue_press('hot', 0, 7_123)
                elif command in ('source', 'same-request'):
                    identity = 'C' if command == 'source' else 'B'
                    pb.play_track(identity, f'/synthetic/{identity}.wav')
                elif command == 'shutdown':
                    # Exercise shutdown's ownership boundary; native close is
                    # independently tested and this silent fake owns no handle.
                    deck.close = lambda: None
                    pb.shutdown()
                else: getattr(pb, command)()
                cancelled.assert_called_once_with()
                stale(); stale()
                started.assert_not_called()
                cancelled.assert_called_once_with()

    def test_same_track_direct_load_and_forced_occurrence_reload_notify_acceptance(self):
        pb, deck = self.player(False)
        started, cancelled = mock.Mock(), mock.Mock()
        generation = pb._analysis_generation
        pb.play_track('A', '/synthetic/A.wav', autoplay=False,
                      on_started=started, on_cancelled=cancelled)
        started.assert_called_once_with(); cancelled.assert_not_called()
        self.assertEqual(pb._analysis_generation, generation)
        pb._on_dur(60_000)
        started.reset_mock()
        pb.play_track('A', '/synthetic/A.wav', autoplay=False, force_reload=True,
                      on_started=started, on_cancelled=cancelled)
        started.assert_called_once_with(); cancelled.assert_not_called()
        self.assertEqual(pb._analysis_generation, generation + 1)
        self.assertEqual(deck.pos, 0)
        self.assertTrue(pb._pending_autoplay)
        self.assertFalse(pb._waveform_ready)


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

    def test_source_error_retry_reuses_inflight_surface_and_preserves_later_commands(self):
        for command in ('play', 'pause', 'stop', 'seek', 'cue-held', 'cue-release', 'source'):
            with self.subTest(command=command):
                pb, deck, ready = self.pending(False)
                pb._on_error(None, 'Synthetic source error')
                pb.play_btn.click()
                self.assertEqual(deck.plays, 0)
                self.assertTrue(pb._pending_autoplay)
                if command == 'seek': pb.seek_ms(12_345)
                elif command.startswith('cue-'):
                    pb._marker_cue_press('memory', 0, 23_456)
                    if command == 'cue-release': pb._marker_cue_release('memory', 0)
                elif command == 'source':
                    previous = (pb._src_path, pb._analysis_path, pb._analysis_generation)
                    pb.play_track('C', '/synthetic/C.wav', analysis_path='/synthetic/C.DAT', autoplay=False)
                    pb._on_analysis_detail(*previous, {'_waveform_complete': True})
                    self.assertFalse(pb._waveform_ready)
                elif command in ('pause', 'stop'): getattr(pb, command)()
                self.manifest(pb); self.complete(pb)
                if command == 'source': pb._on_dur(60_000)
                self.assertEqual(deck.plays, int(command in ('play', 'seek', 'cue-held')))
                self.assertEqual(deck.pos, 0 if command == 'stop' else 12_345 if command == 'seek'
                                 else 23_456 if command.startswith('cue-') else 625)
                self.assertEqual(ready, [True])
                self.assertEqual(pb._start_waveform.call_count, 2 if command == 'source' else 1)

    def test_decoder_retry_waits_for_new_duration_and_surface_before_exact_seek_play(self):
        for surface_first in (False, True):
            for retry_after_surface in (False, True):
                for command in ('play', 'pause', 'stop', 'seek', 'cue-held', 'cue-release'):
                    with self.subTest(surface_first=surface_first,
                                      retry_after_surface=retry_after_surface, command=command):
                        pb, _, ready = self.pending(False)
                        deck = RefillDeck(); pb.player = deck
                        pb._on_error(None, 'Synthetic decoder open error')
                        pb.seek_ms(12_345)
                        if retry_after_surface: self.complete(pb)
                        deck.source_retry_needed = True
                        retries = []

                        def retry():
                            retries.append(True)
                            deck.source_retry_needed = False
                            deck.pos = 0
                            pb._on_dur(0)
                            return True

                        deck.retry_source = retry
                        pb.play_btn.click()
                        self.assertEqual(retries, [True])
                        self.assertEqual(deck.plays, 0)
                        self.assertEqual(pb._pending_start_ms, 12_345)
                        self.assertEqual(deck.visual[-1], (12_345, True))
                        pb._on_backend_pos(0)
                        self.assertEqual(pb._pos, 12_345)
                        if command == 'seek': pb.seek_ms(23_456)
                        elif command.startswith('cue-'):
                            pb._marker_cue_press('memory', 0, 30_000)
                            if command == 'cue-release': pb._marker_cue_release('memory', 0)
                        elif command != 'play': getattr(pb, command)()
                        self.assertEqual(deck.plays, 0)
                        if surface_first or retry_after_surface:
                            if not retry_after_surface: self.complete(pb)
                            self.assertEqual(deck.plays, 0)
                            pb._on_dur(60_000)
                        else:
                            pb._on_dur(60_000)
                            self.assertEqual(deck.plays, 0)
                            self.complete(pb)
                        deck.refill()
                        self.assertEqual(deck.state == PlaybackState.PLAYING,
                                         command in ('play', 'seek', 'cue-held'))
                        self.assertEqual(deck.pos, 0 if command == 'stop' else 23_456 if command == 'seek'
                                         else 30_000 if command.startswith('cue-') else 12_345)
                        self.assertEqual(pb._start_waveform.call_count, 1)
                        # A complete surface before a failed decoder is still
                        # only a readiness notification, never audible Play.
                        self.assertTrue(ready)

    def test_post_ready_stop_then_decoder_retry_owns_zero_before_another_poll(self):
        pb, _, _ = self.pending(False)
        self.complete(pb)
        pb.seek_ms(12_345)
        pb._on_error(None, 'Synthetic source error')
        pb.stop()
        self.assertEqual(pb._pos, 0)
        deck = RefillDeck(); pb.player = deck
        deck.source_retry_needed = True

        def retry():
            deck.source_retry_needed = False
            pb._on_dur(0)
            return True

        deck.retry_source = retry
        pb.play_btn.click()
        self.assertTrue(pb._pending_autoplay)
        self.assertEqual(pb._pending_start_ms, 0)
        self.assertEqual(deck.plays, 0)
        pb._on_dur(60_000)
        self.assertEqual(deck.pos, 0)
        self.assertEqual(deck.plays, 1)
