"""Real transport/gesture controls over a silent fake deck; no audio or sources."""
import unittest
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


class PlayerGestureFreshness(unittest.TestCase):
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
