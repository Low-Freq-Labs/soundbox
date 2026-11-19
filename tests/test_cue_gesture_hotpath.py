"""Cue buttons over the real main-window graph; silent deck and isolated state."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.cdj import _source_edits_error, _source_edits_pending
from desktop.native_deck import NativeGainControl, PlaybackState, UnavailableDeckBackend
from soundbox import analysis
from soundbox.library import Library, Track
from soundbox.providers import Providers
from soundbox.state_repository import read_state


class _Deck(UnavailableDeckBackend):
    def __init__(self, parent):
        super().__init__('silent cue fixture', parent)
        self.state = PlaybackState.PAUSED
        self._position = 20_123
        self.seeks = []

    def playbackState(self):return self.state
    def play(self):
        self.state = PlaybackState.PLAYING
        self.playbackStateChanged.emit(self.state)
    def pause(self):
        self.state = PlaybackState.PAUSED
        self.playbackStateChanged.emit(self.state)
    def synchronize(self):self.positionChanged.emit(self._position)
    def setPosition(self, position):
        self.seeks.append(position)
        super().setPosition(position)


class _UnreadGrid(list):
    def __deepcopy__(self, _memo):
        raise AssertionError('A cue gesture copied the unrelated loaded grid')


class CueGestureHotpath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live',
                                     side_effect=AssertionError('live source')))
        self.enterContext(mock.patch('desktop.player.NativeDeckBackend',
                                     side_effect=RuntimeError('silent cue fixture')))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        audio = root/'fixture.wav'
        audio.write_bytes(b'synthetic identity, never decoded')
        w = self.window = MainWindow(providers=Providers('public'), isolated_dir=root/'state')
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.shutdown)
        for controller, method in ((w.graveyard_controller, 'prepare_library'),
                                   (w.native_analysis_controller, 'discover'),
                                   (w.media_controller, 'discover')):
            self.enterContext(mock.patch.object(controller, method))
        for method in ('_cache_library', '_arm_waveform_rechecks', '_schedule_stock_artwork',
                       '_resume_analysis_jobs', '_maybe_fill_waveforms', '_read_stock_device_view'):
            self.enterContext(mock.patch.object(w, method))
        track = Track('A', 'Fixture', 'Artist', audio.as_uri(), source_uuid='uuid-A',
            source_native_id='1', source_cues=[], source_cue_revision='a'*64,
            source_analysis_path='/synthetic/ANLZ0000.DAT')
        w.lib = Library({'A': track}, [], '/synthetic/master.db', source_kind='rekordbox-db',
                        source_library_id='fixture')
        w._rekordbox_source_verified = True
        w._apply_loaded_library()
        w.tag_controller._requires_cue_authority = False
        w.grid_controller._requires_authority = False
        self.enterContext(mock.patch.object(w.prep_controller, 'schedule'))
        self.save = self.enterContext(mock.patch.object(w, '_save_state', return_value=True))
        pb = self.player = w.player_bar
        self.original = pb.player
        self.deck = _Deck(pb)
        pb.player = self.deck
        self.original_audio = pb.audio
        pb.audio = NativeGainControl(self.deck, pb)
        self.deck.positionChanged.connect(pb._on_pos)
        self.deck.playbackStateChanged.connect(pb._on_state)
        self.addCleanup(self.restore_deck)
        pb._playback_available = True
        pb._native_error = ''
        pb._track_id = 'A'
        pb._src_path = str(audio)
        pb._dur = 600_000
        pb._pos = 20_123
        pb._pending_autoplay = False
        pb.cue_panel.set_loaded(True)
        pb._analysis_parts = {'beats': _UnreadGrid([[1, 0, 120], [2, 500, 120]])}
        pb._beat_grid = [0, 500, 20_000, 20_500]
        w._sync_cue_editing()

    def restore_deck(self):
        self.player.player = self.original
        self.player.audio = self.original_audio

    def wait(self, condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(condition())

    def test_empty_press_is_immediate_and_saves_only_cue_in_background(self):
        w=self.window;pb=self.player
        pb.quantize_enabled=True
        before=pb._analysis_parts['beats']
        track=w.lib.tracks_by_id['A']
        record={'path':str(track.path),'beats':before}
        results={analysis.result_key(track.path):record}
        w.settings['analysis_results']=results
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        commit=w._state_repository.commit_patch
        captured=[]
        def held(patch):
            captured.append(patch.edits())
            entered.set()
            if not release.wait(5):raise AssertionError('state fixture timed out')
            return commit(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=held), \
                mock.patch.object(pb,'refresh_live_grid',side_effect=AssertionError('unrelated grid upload')):
            pb.cue_panel._press('hot',0)
            pb.cue_panel._release('hot',0)
            self.assertTrue(w.prep_controller.admission_pending)
            self.assertTrue(_source_edits_pending(w))
            self.assertFalse(w.prep_controller.pending)
            self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_000)
            self.wait(entered.is_set)
            active=w.prep_controller.state_lane.queue[0]
            w.prep_controller.retry()
            self.assertIs(w.prep_controller.state_lane.queue[0],active)
            self.assertEqual(len(w.prep_controller.state_lane.queue),1)
            heartbeat=[]
            QTimer.singleShot(0,lambda:heartbeat.append(True))
            self.wait(lambda:bool(heartbeat))
            self.assertTrue(w.prep_controller.admission_pending)
            self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_000)
            self.assertIn('cue/grid',w._pending_save_reason())
            self.assertFalse(w._begin_state_maintenance())
            release.set()
            self.wait(lambda:not w.prep_controller.admission_pending)
        job=w.prep_controller.pending[0]
        self.assertEqual(job['grid'],[])
        self.assertEqual(job['cues'][0]['position_ms'],20_000)
        self.assertEqual([op['kind'] for op in job['operations']],['cue'])
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_000)
        self.assertIs(pb._analysis_parts['beats'],before)
        self.assertIs(w.settings['analysis_results'],results)
        self.assertIs(w.settings['analysis_results'][analysis.result_key(track.path)],record)
        self.assertEqual(set(captured[0][0]),{('settings','pending_prep_edits')})
        self.assertEqual(self.deck.seeks,[])
        self.assertEqual(self.deck.state,PlaybackState.PAUSED)
        self.save.assert_not_called()

    def test_failed_async_creation_keeps_usable_pad_and_original_pending_queue(self):
        queue=[]
        self.window.settings['pending_prep_edits']=queue
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('fixture full')):
            self.player.cue_panel._press('memory',0)
            self.player.cue_panel._release('memory',0)
            self.wait(lambda:not self.window.prep_controller.admission_pending)
        self.assertIs(self.window.settings['pending_prep_edits'],queue)
        self.assertEqual(self.player.cue_panel._cues['memory'][0]['position_ms'],20_123)
        self.assertEqual(self.deck.seeks,[])
        self.assertEqual(self.deck.state,PlaybackState.PAUSED)
        self.assertTrue(self.window.prep_controller.state_lane.failed)
        self.assertIn('needs attention',self.window._pending_save_reason())
        self.assertEqual(_source_edits_error(self.window),'fixture full')
        self.assertTrue(_source_edits_pending(self.window))
        self.save.assert_not_called()
        self.window.prep_controller.retry()
        self.wait(lambda:not self.window.prep_controller.admission_pending)
        self.assertFalse(self.window.prep_controller.admission_error)
        self.assertEqual(self.player.cue_panel._cues['memory'][0]['position_ms'],20_123)

    def test_shutdown_drains_accepted_intention_before_closing_the_shared_writer(self):
        self.player.cue_panel._press('hot',0)
        self.player.cue_panel._release('hot',0)
        self.assertTrue(self.window.prep_controller.admission_pending)
        self.window.shutdown()
        state=read_state(self.window._state_repository.path)
        job=state['settings']['pending_prep_edits'][0]
        self.assertEqual(job['cues'][0]['position_ms'],20_123)
        self.assertIsNone(job['prepared'])
        self.assertFalse(self.window.prep_controller.admission_pending)

    def test_changed_source_keeps_original_intention_for_reconciliation_without_publishing_pad(self):
        self.player.cue_panel._press('hot',0)
        self.window.lib.source='/synthetic/other.db'
        self.wait(lambda:not self.window.prep_controller.admission_pending)
        self.assertFalse(self.window.prep_controller.pending)
        self.assertEqual(self.player.cue_panel._cues['hot'],[None]*8)
        self.assertTrue(self.window.prep_controller.admission_error)
        self.assertEqual(self.window.prep_controller.state_lane.failed[0][0]['job']['cues'][0]['position_ms'],20_123)

    def test_queued_positions_remain_fifo_and_newer_track_selection_does_not_steal_cues(self):
        w=self.window;pb=self.player
        for slot,position in enumerate((20_123,31_456)):
            self.deck._position=position
            pb.cue_panel._press('hot',slot)
            pb.cue_panel._release('hot',slot)
        self.assertEqual([cue['position_ms'] for cue in pb.cue_panel._cues['hot'] if cue],
                         [20_123,31_456])
        pb._track_id='newer'
        pb.cue_panel.set_cues([])  # New track-load clears its own surface.
        self.wait(lambda:not w.prep_controller.admission_pending)
        self.assertEqual([cue['position_ms'] for cue in w.prep_controller.pending[0]['cues']],
                         [20_123,31_456])
        self.assertEqual(len(w.prep_controller.pending[0]['operations']),2)
        self.assertEqual(pb.current_track(),'newer')
        self.assertEqual(pb.cue_panel._cues['hot'],[None]*8)
        self.assertEqual(self.deck.seeks,[])

    def test_rapid_set_next_cue_allocates_distinct_unsaved_slots_at_each_captured_position(self):
        for position in (20_123,31_456,42_789):
            self.deck._position=position
            self.assertTrue(self.window._set_next_cue())
        self.assertEqual([cue['position_ms'] for cue in self.player.cue_panel._cues['hot'] if cue],
                         [20_123,31_456,42_789])
        self.wait(lambda:not self.window.prep_controller.admission_pending)
        cues=self.window.prep_controller.pending[0]['cues']
        self.assertEqual([(cue['slot'],cue['position_ms']) for cue in cues],
                         [(0,20_123),(1,31_456),(2,42_789)])
        self.assertEqual(self.deck.seeks,[])

    def test_delete_after_accepted_creation_preserves_both_intentions_without_a_preview(self):
        self.assertTrue(self.window._edit_cue('hot',0,'add'))
        self.assertTrue(self.window._edit_cue('hot',0,'delete'))
        self.assertEqual(self.player.cue_panel._cues['hot'],[None]*8)
        self.wait(lambda:not self.window.prep_controller.admission_pending)
        job=self.window.prep_controller.pending[0]
        self.assertEqual([op['operation'] for op in job['operations']],['add','delete'])
        self.assertEqual(job['cues'],[])
        self.assertEqual(self.player.cue_panel._cues['hot'],[None]*8)
        self.assertEqual(self.deck.seeks,[])

    def test_filled_hot_and_memory_hold_go_directly_to_transport_without_edit_lane(self):
        self.player.cue_panel.set_cues([dict(kind=kind, slot=0, position_ms=7_123)
                                       for kind in ('hot', 'memory')])
        self.save.side_effect = AssertionError('filled cue tried to save')
        with mock.patch.object(self.window.prep_controller, 'reason',
                               side_effect=AssertionError('filled cue checked edit queue')):
            for kind in ('hot', 'memory'):
                with self.subTest(kind=kind):
                    self.deck.seeks.clear()
                    self.player.cue_panel._press(kind, 0)
                    self.assertEqual(self.deck.state, PlaybackState.PLAYING)
                    self.assertEqual(self.deck.seeks, [7_123])
                    self.player.cue_panel._release(kind, 0)
                    self.assertEqual(self.deck.state, PlaybackState.PAUSED)
                    self.assertEqual(self.deck.seeks, [7_123, 7_123])
        self.save.assert_not_called()
