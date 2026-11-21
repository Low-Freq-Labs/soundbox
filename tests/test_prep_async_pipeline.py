"""Native planners retain prepared/ACK durability across the asynchronous UI lane."""
from copy import deepcopy
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import prep_edits
from tests import test_deferred_prep as fixture


class AsyncPrepPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.DurablePrep.setUpClass()

    def setUp(self):
        self.case=fixture.DurablePrep();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.window=self.case.window;self.controller=self.case.controller
        self.window._state_repository=SimpleNamespace(generation=0,area=SimpleNamespace(generation=0))
        self.commits=[]
        def submit(replacements,removals,committed,failed,*,current):
            self.assertEqual(set(replacements),{('settings',prep_edits.KEY)})
            self.assertEqual(removals,())
            self.commits.append((deepcopy(replacements[('settings',prep_edits.KEY)]),committed,failed,current))
            return True
        self.window._commit_tag_state=submit

    def wait_commit(self):
        self.case.wait(lambda:bool(self.commits))
        self.assertTrue(self.controller.admission_pending)

    def finish(self, error=None):
        queue,committed,failed,current=self.commits.pop(0)
        self.assertTrue(current())
        if error is not None:failed(error);return
        self.window.settings[prep_edits.KEY]=queue
        self.assertTrue(self.window._save_state())
        committed()

    def test_native_write_waits_for_prepared_commit_and_source_view_waits_for_ack_commit(self):
        tid=self.case.tid
        self.assertTrue(self.controller.request_cue(tid,'hot',0,'add',777))
        self.wait_commit()
        self.assertFalse(self.controller.pending)
        self.assertEqual(self.case.writes,[])
        self.assertEqual(self.window.player_bar.refresh_live_cues.call_args.args[2][0]['position_ms'],777)
        self.finish()
        self.wait_commit()
        self.assertTrue(self.controller.busy)
        self.assertIsNone(self.controller.pending[0]['prepared'])
        self.assertEqual(self.case.writes,[])
        self.finish()
        self.wait_commit()
        self.assertEqual(len(self.case.writes),1)

        self.assertIsNotNone(self.controller.pending[0]['prepared'])
        self.assertEqual(self.window.lib.tracks_by_id[tid].source_cues,[])
        self.window.lib.tracks_by_id[tid]=replace(self.window.lib.tracks_by_id[tid],name='Newer title')
        self.finish()
        self.assertFalse(self.controller.pending)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.window.lib.tracks_by_id[tid].source_cues[0]['position_ms'],777)
        self.assertEqual(self.window.lib.tracks_by_id[tid].name,'Newer title')
        self.assertEqual(len(self.case.writes),1)

    def test_audio_identity_renewal_uses_selected_durable_queue_before_publication(self):
        track=self.case.bare_track()
        self.case.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertTrue(self.controller.request_grid(self.case.tid,'shift',1))
        captured=deepcopy(self.controller.pending)
        self.assertTrue(captured[0]['identity_pending'])
        self.case.native.shared.busy=False
        with mock.patch.object(self.window,'_save_state',
                               side_effect=AssertionError('Identity proof must save only its queue delta')):
            self.controller.timer.stop();self.controller.pump()
            self.wait_commit()
        self.assertEqual(self.controller.pending,captured)
        self.assertNotIn('_detected_epoch',self.window.player_bar._analysis_parts)
        self.assertEqual(self.case.writes,[])
        self.finish()
        self.assertNotIn('identity_pending',self.controller.pending[0])
        self.assertEqual(self.window.player_bar._analysis_parts['_detected_epoch'],
                         self.controller.pending[0]['epoch'])
        self.assertEqual(self.case.writes,[])

    def test_newer_cue_retains_native_upgrade_when_identity_proof_ack_was_waiting(self):
        track=self.case.bare_track()
        submit=self.window._commit_tag_state
        del self.window._commit_tag_state
        self.case.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertTrue(self.controller.request_cue(self.case.tid,'hot',0,'add',777))
        self.assertEqual(self.controller.pending[0]['version'],1)
        self.window._commit_tag_state=submit
        self.case.native.shared.busy=False
        self.controller.timer.stop();self.controller.pump()
        self.wait_commit()
        self.assertTrue(self.controller.request_cue(self.case.tid,'hot',1,'add',1555))
        self.finish()  # The completed identity proof upgrades the native job.
        self.assertEqual(self.controller.pending[0]['version'],2)
        self.wait_commit()
        queued=self.commits[0][0][0]
        self.assertEqual(queued['version'],2)
        self.assertTrue(queued['native_ready'])
        self.assertNotIn('identity_pending',queued)
        self.finish()
        for _ in range(4):
            self.wait_commit();self.finish()
        self.assertFalse(self.controller.pending)
        self.assertEqual([kind for kind,_ in self.case.writes],['cue','cue'])
        self.assertEqual(sorted(row['InMsec'] for row in self.case.native.state.cues),[777,1555])

    def test_state_failure_after_queue_ownership_changes_releases_logical_busy(self):
        self.assertTrue(self.controller.request_cue(self.case.tid,'hot',0,'add',777))
        self.wait_commit();self.finish()
        self.wait_commit()
        self.assertTrue(self.controller.busy)
        _queue,_committed,failed,current=self.commits.pop(0)
        self.window.settings[prep_edits.KEY]=[]
        self.assertFalse(current())
        failed(ValueError('fixture queue replaced'))
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.admission_pending)
        self.assertIn('fixture queue replaced',self.controller.errors[self.case.tid])
        self.assertEqual(self.case.writes,[])
    def test_failed_ack_keeps_exact_receipt_and_retry_does_not_duplicate_native_cue(self):
        tid=self.case.tid
        self.assertTrue(self.controller.request_cue(tid,'hot',0,'add',777))
        for _ in range(2):self.wait_commit();self.finish()
        self.wait_commit()
        self.assertEqual(len(self.case.writes),1)

        prepared=deepcopy(self.controller.pending[0]['prepared'])
        self.finish(OSError('fixture ACK full'))
        self.wait_commit();self.finish()
        self.assertEqual(self.controller.pending[0]['prepared'],prepared)
        self.assertTrue(self.controller.pending[0]['error'])
        self.controller.retry()
        self.wait_commit();self.finish()
        self.wait_commit();self.finish()
        self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.case.writes),1)

    def test_failed_newer_intention_retains_proven_source_prefix_for_exact_retry(self):
        tid=self.case.tid
        self.case.gate=threading.Event()
        self.assertTrue(self.controller.request_cue(tid,'hot',0,'add',777))
        for _ in range(2):self.wait_commit();self.finish()
        self.case.wait(lambda:len(self.case.writes)==1)
        first=self.controller.pending[0]['operations'][0]['id']
        self.assertTrue(self.controller.request_cue(tid,'hot',1,'add',1555))
        captured=self.controller.state_lane.staged[tid]
        second=captured['operations'][-1]['id']
        self.case.gate.set()
        self.wait_commit();self.finish()  # the older source acknowledgement
        self.wait_commit()
        self.assertEqual([op['id'] for op in self.commits[0][0][0]['operations']],[second])
        self.finish(OSError('fixture newer intent full'))
        self.assertIn(first,self.controller.state_lane.retired[captured['id']])
        self.controller.retry()
        for _ in range(3):self.wait_commit();self.finish()
        self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.case.writes),2)
        self.assertEqual(sorted(cue['InMsec'] for cue in self.case.native.state.cues),[777,1555])

    def test_older_native_ack_keeps_newer_delete_overlay_and_other_track_admission(self):
        tid=self.case.tid
        self.case.gate=threading.Event()
        self.assertTrue(self.controller.request_cue(tid,'hot',0,'add',777))
        for _ in range(2):self.wait_commit();self.finish()
        self.case.wait(lambda:len(self.case.writes)==1)
        self.assertTrue(self.controller.request_cue(tid,'hot',0,'delete',None))
        track=self.window.lib.tracks_by_id[tid]
        other_path=track.path.with_name('queued-other.wav');other_path.write_bytes(b'other generated track')
        other=replace(track,track_id='other',source_native_id='other-native',source_uuid='other-uuid',
                      location_uri=other_path.as_uri())
        self.window.lib.tracks_by_id['other']=other
        self.assertTrue(self.controller.request_cue('other','memory',0,'add',1234))
        pending_id=self.controller.state_lane.overlays['other']['job']['operations'][0]['id']
        self.assertEqual(self.controller.cue_rows(tid),[])
        self.case.gate.set()
        self.wait_commit();self.finish()  # Durable native ACK of the older add.
        self.assertEqual(self.window.lib.tracks_by_id[tid].source_cues[0]['position_ms'],777)
        self.assertEqual(self.controller.cue_rows(tid),[])
        self.assertEqual(self.window.player_bar.refresh_live_cues.call_args.args[2],[])
        self.assertEqual(self.controller.requested_cues('other')[0]['position_ms'],1234)
        self.assertEqual(self.controller.state_lane.overlays['other']['job']['operations'][0]['id'],pending_id)
        # Keep this control at the admission boundary; the other generated track
        # has no physical native-store fixture and must never enter source SQL.
        self.controller.stop()
        self.controller.state_lane.timer.stop()
        self.controller.state_lane.queue.clear();self.controller.state_lane.overlays.clear()
