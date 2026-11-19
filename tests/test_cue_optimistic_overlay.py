"""Immediate usable cues over held durable acknowledgements and track switches."""
from dataclasses import replace
import threading
import time
import unittest
from unittest import mock

from desktop.native_deck import PlaybackState
from soundbox import analysis, prep_edits
from tests import test_cue_gesture_hotpath as fixture


class OptimisticCueOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.CueGestureHotpath.setUpClass()

    def setUp(self):fixture.CueGestureHotpath.setUp(self)
    restore_deck=fixture.CueGestureHotpath.restore_deck
    wait=fixture.CueGestureHotpath.wait

    def hold_commit(self):
        entered,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        commit=self.window._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise RuntimeError('held cue fixture timed out')
            return commit(patch)
        self.enterContext(mock.patch.object(self.window._state_repository,'commit_patch',side_effect=held))
        return entered,release

    def switch(self, tid):
        """Actual MainWindow load routing; only the silent deck load is bounded."""
        pb=self.player
        def load(identity,path,**kwargs):
            pb._track_id=identity;pb._src_path=str(path);pb._pending_autoplay=False
            pb._live_cues=None;pb.cue_panel.set_cues([])
            pb.refresh_live_cues(identity,path,kwargs['cues'])
        with mock.patch.object(pb,'play_track',side_effect=load):
            self.assertTrue(self.window._open_track(self.window.lib.tracks_by_id[tid],False))

    def add_track(self):
        w=self.window
        source=w.lib.tracks_by_id['A']
        path=source.path.with_name('other.wav');path.write_bytes(b'other generated cue identity')
        w.lib.tracks_by_id['B']=replace(source,track_id='B',location_uri=path.as_uri(),
            source_uuid='uuid-B',source_native_id='2')

    def test_hot_and_memory_cues_are_usable_before_any_canonical_ack(self):
        entered,release=self.hold_commit()
        w=self.window;pb=self.player
        pb.cue_panel._press('hot',0);pb.cue_panel._release('hot',0)
        self.wait(entered.is_set)
        self.assertFalse(w.prep_controller.pending)
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_123)
        pb.cue_panel._press('hot',0)
        self.assertEqual(self.deck.seeks,[20_123])
        self.assertEqual(self.deck.state,PlaybackState.PLAYING)
        pb.cue_panel._release('hot',0)
        self.assertEqual(self.deck.state,PlaybackState.PAUSED)
        self.deck._position=31_456
        pb.cue_panel._press('memory',0);pb.cue_panel._release('memory',0)
        self.assertEqual(pb.cue_panel._cues['memory'][0]['position_ms'],31_456)
        pb.cue_panel._press('memory',0)
        self.assertEqual(self.deck.seeks[-1],31_456)
        pb.cue_panel._release('memory',0)
        release.set();self.wait(lambda:not w.prep_controller.admission_pending)
        self.assertEqual(len(w.prep_controller.pending[0]['operations']),2)

    def test_held_a_ack_allows_b_cue_and_a_b_reload_without_overlay_loss(self):
        entered,release=self.hold_commit();self.add_track()
        w=self.window;pb=self.player
        pb.cue_panel._press('hot',0);pb.cue_panel._release('hot',0)
        self.wait(entered.is_set)
        self.switch('B');self.assertEqual(pb.cue_panel._cues['hot'],[None]*8)
        self.deck._position=34_567
        pb.cue_panel._press('hot',1);pb.cue_panel._release('hot',1)
        self.assertEqual(pb.cue_panel._cues['hot'][1]['position_ms'],34_567)
        self.assertFalse(w.prep_controller.pending)
        self.switch('A');self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_123)
        self.assertIsNone(pb.cue_panel._cues['hot'][1])
        self.switch('B');self.assertIsNone(pb.cue_panel._cues['hot'][0])
        release.set();self.wait(lambda:not w.prep_controller.admission_pending)
        self.assertEqual([job['track_id'] for job in w.prep_controller.pending],['A','B'])
        self.assertEqual(pb.current_track(),'B')
        self.assertEqual(pb.cue_panel._cues['hot'][1]['position_ms'],34_567)
        self.assertEqual(self.deck.seeks,[])

    def test_failed_a_save_retains_overlay_while_b_remains_editable_then_retry_exactly(self):
        self.add_track();w=self.window;pb=self.player
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('fixture full')):
            self.assertTrue(w._edit_cue('hot',0,'add'))
            first=w.prep_controller.state_lane.overlays['A']['job']['operations'][0]['id']
            self.wait(lambda:not w.prep_controller.admission_pending)
        self.assertTrue(w.prep_controller.admission_error)
        self.switch('B');self.deck._position=36_789
        self.assertTrue(w._edit_cue('memory',0,'add'))
        self.wait(lambda:not w.prep_controller.admission_pending)
        self.switch('A')
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],20_123)
        pb.cue_panel._press('hot',0);self.assertEqual(self.deck.seeks[-1],20_123)
        pb.cue_panel._release('hot',0)
        w.prep_controller.retry();self.wait(lambda:not w.prep_controller.admission_pending)
        saved={job['track_id']:job for job in w.prep_controller.pending}
        self.assertEqual(saved['A']['operations'][0]['id'],first)
        self.assertEqual(saved['B']['cues'][0]['position_ms'],36_789)
        self.assertFalse(w.prep_controller.admission_error)

    def test_replacement_uuid_or_repository_epoch_refuses_new_gesture_and_hides_old_overlay(self):
        for change in ('uuid','repository'):
            with self.subTest(change=change):
                w=self.window;controller=w.prep_controller;pb=self.player
                controller.tags.busy=True
                original=w.lib.tracks_by_id['A'];repository=w._state_repository
                self.assertTrue(w._edit_cue('hot',0,'add'))
                row=controller.state_lane.queue[-1]
                if change=='uuid':w.lib.tracks_by_id['A']=replace(original,source_uuid='replacement-uuid')
                else:repository.generation+=1
                self.assertFalse(w._edit_cue('hot',1,'add'))
                self.assertEqual(controller.cue_rows('A'),[])
                self.assertEqual(row['job']['cues'][0]['position_ms'],20_123)
                controller.tags.busy=False
                self.wait(lambda:not controller.admission_pending)
                self.assertEqual(pb.cue_panel._cues['hot'],[None]*8)
                self.assertTrue(controller.state_lane.failed)
                # Retire only this synthetic subcase before restoring its binding.
                controller.state_lane.failed.clear();controller.state_lane.overlays.clear()
                controller.errors.clear();w.lib.tracks_by_id['A']=original
                if change=='repository':repository.generation-=1

    def test_playing_rapid_cues_coalesce_state_admission_and_restyle_only_changed_slot(self):
        w=self.window;pb=self.player;controller=w.prep_controller
        controller.tags.busy=True
        self.deck.state=PlaybackState.PLAYING
        buttons=[*pb.cue_panel.hot_slots,*pb.cue_panel.memory_slots]
        counts=[self.enterContext(mock.patch.object(button,'setStyleSheet',wraps=button.setStyleSheet))
                for button in buttons]
        elapsed=[]
        for slot in range(8):
            self.deck._position=20_123+slot*1111
            start=time.perf_counter_ns()
            pb.cue_panel._press('hot',slot);pb.cue_panel._release('hot',slot)
            elapsed.append((time.perf_counter_ns()-start)/1e6)
            self.assertEqual(pb.cue_panel._cues['hot'][slot]['position_ms'],20_123+slot*1111)
        self.assertEqual(self.deck.state,PlaybackState.PLAYING)
        self.assertEqual(self.deck.seeks,[])
        self.assertEqual(len(controller.state_lane.queue),1)
        self.assertEqual(len(controller.state_lane.queue[0]['job']['operations']),8)
        self.assertEqual([value.call_count for value in counts],[1]*8+[0]*8)
        self.assertFalse(controller.pending)
        self.gesture_ms=elapsed
        controller.tags.busy=False
        self.wait(lambda:not controller.admission_pending)

    def test_imported_cue_uses_sparse_draft_ack_without_cloning_analysis_grid(self):
        w=self.window;controller=w.prep_controller
        track=w.lib.tracks_by_id['A']
        track=replace(track,imported=True,source_analysis_path='',source_native_id='',source_uuid='')
        w.lib.tracks_by_id['A']=track
        beats=fixture._UnreadGrid([[1,0,120],[2,500,120]])
        record=dict(engine=analysis.ENGINE,path=str(track.path),epoch=analysis.identity(track.path.stat()),
            size=track.path.stat().st_size,sha256='a'*64,duration=600,bpm=120,key='8A',first_cue=None,beats=list(beats))
        results={analysis.result_key(track.path):record}
        w.settings['analysis_results']=results
        w._state_repository.save({'settings':w.settings})
        record['beats']=beats
        self.assertTrue(w._edit_cue('hot',0,'add'))
        self.assertEqual(self.player.cue_panel._cues['hot'][0]['position_ms'],20_123)
        self.assertIs(w.settings['analysis_results'],results)
        self.wait(lambda:not controller.admission_pending)
        self.assertFalse(controller.admission_error,controller.admission_error)
        after=w.settings['analysis_results'][analysis.result_key(track.path)]
        self.assertEqual(after['cue_draft']['cues'][0]['position_ms'],20_123)
        self.assertIs(after['beats'],beats)
        self.assertEqual(controller.pending[0]['grid'],[])
        prep_edits.validate(controller.pending)
        self.save.assert_not_called()

    def test_native_binding_fields_are_atomic_bounded_and_legacy_jobs_remain_readable(self):
        controller=self.window.prep_controller
        controller.tags.busy=True
        self.assertTrue(self.window._edit_cue('hot',0,'add'))
        job=controller.state_lane.queue[0]['job']
        self.assertEqual((job['native_id'],job['source_uuid'],job['native_path']),
            ('1','uuid-A',str(self.window.lib.tracks_by_id['A'].native_path)))
        for missing in ('native_id','source_uuid','native_path'):
            with self.subTest(missing=missing):
                malformed={key:value for key,value in job.items() if key!=missing}
                with self.assertRaises(ValueError):prep_edits.validate([malformed])
        for key,value in (('native_id','x'*129),('source_uuid',3),('native_path','relative.wav')):
            with self.subTest(field=key):
                with self.assertRaises(ValueError):prep_edits.validate([{**job,key:value}])
        legacy={key:value for key,value in job.items() if key not in ('native_id','source_uuid','native_path')}
        prep_edits.validate([legacy])
        controller.tags.busy=False
        self.wait(lambda:not controller.admission_pending)

    def test_source_selection_and_restore_refuse_queued_and_failed_overlay_owners(self):
        w=self.window;controller=w.prep_controller
        controller.tags.busy=True
        self.assertTrue(w._edit_cue('hot',0,'add'))
        self.assertEqual(w._source_selection_reason('/synthetic/other.db'),
                         'Finish cue/grid saves before changing library sources')
        self.assertFalse(w._begin_state_maintenance())
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('fixture full')):
            controller.tags.busy=False
            self.wait(lambda:not controller.admission_pending)
        self.assertEqual(w._source_selection_reason('/synthetic/other.db'),
                         'Cue/grid save needs attention before changing library sources')
        self.assertFalse(w._begin_state_maintenance())
        self.assertIn('needs attention',w._pending_save_reason())
        self.assertEqual(self.player.cue_panel._cues['hot'][0]['position_ms'],20_123)

    def test_new_cue_never_clones_an_existing_pending_grid_or_an_unrelated_job(self):
        w=self.window;controller=w.prep_controller
        self.assertTrue(w._edit_cue('hot',0,'add'))
        self.wait(lambda:not controller.admission_pending)
        grid=fixture._UnreadGrid([[1,0,120],[2,500,120]])
        job={**controller.pending[0],'grid':grid}
        unrelated={**job,'track_id':'unrelated','grid':fixture._UnreadGrid(grid)}
        w.settings[prep_edits.KEY]=[job,unrelated]
        controller.tags.busy=True
        self.deck._position=31_456
        self.assertTrue(w._edit_cue('hot',1,'add'))
        self.assertIs(controller.state_lane.queue[0]['job']['grid'],grid)
        self.assertEqual(self.player.cue_panel._cues['hot'][1]['position_ms'],31_456)
        controller.tags.busy=False
        self.wait(lambda:not controller.admission_pending)
