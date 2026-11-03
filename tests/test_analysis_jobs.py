"""Pending detection survives serialization without authority or stale rebinding."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from soundbox import analysis_jobs as jobs
from soundbox.library import Library,Track


class AnalysisJobs(unittest.TestCase):
    def test_automatic_policy_is_durable_and_skips_known_long_sets(self):
        from soundbox.library import Library,Track
        track=Track('long','Set','','file:///not-read/set.m4a',total_time=901)
        lib=Library({'long':track},[],'synthetic')
        self.assertEqual(jobs.admit([],lib,[track],None,automatic=True),[])
        track.total_time=0
        queued=jobs.admit([],lib,[track],None,automatic=True)
        self.assertTrue(queued[0]['automatic']);jobs.validate(queued)
        manual=jobs.admit([],lib,[track],None)
        self.assertNotIn('automatic',manual[0]);jobs.validate(manual)

    def setUp(self):
        self.track=Track('stable','Example','',Path('/captured/example.wav').as_uri(),imported=True)
        self.lib=Library({'stable':self.track},[],'synthetic')

    def test_admission_roundtrip_duplicate_request_and_atomic_result_retirement(self):
        value=jobs.admit([],self.lib,[self.track],dict(bpm=True,key=False))
        saved=json.loads(json.dumps(value));self.assertEqual(jobs.validate(saved),value)
        self.assertEqual(jobs.admit(saved,self.lib,[self.track],dict(bpm=False,key=True)),saved)
        self.assertEqual(jobs.finish(saved,saved[0]['id']),[])
        self.assertEqual(saved,value)

    def test_source_path_track_or_uuid_changes_never_match_a_saved_intention(self):
        value=jobs.admit([],self.lib,[self.track],{});job=value[0]
        self.assertTrue(jobs.matches(job,self.lib,self.track))
        for change in ({'path':'/elsewhere/example.wav'},{'source':'other'},{'library_id':'different'},
                       {'track_id':'other'},{'source_uuid':'new-uuid'}):
            self.assertFalse(jobs.matches({**job,**change},self.lib,self.track))

    def test_manual_waveform_request_survives_restart_and_upgrades_an_active_automatic_job(self):
        queued=jobs.admit([],self.lib,[self.track],{})
        legacy=deepcopy(queued);legacy[0].pop('waveform');jobs.validate(legacy)
        manual=jobs.admit(legacy,self.lib,[self.track],{},rebuild_waveform=True)
        self.assertTrue(manual[0]['waveform']);self.assertEqual(manual[0]['id'],queued[0]['id'])
        saved=json.loads(json.dumps(manual));jobs.validate(saved);self.assertTrue(saved[0]['waveform'])
        self.assertFalse(queued[0]['waveform'])

    def test_failure_does_not_restart_automatically_and_explicit_retry_gets_a_new_identity(self):
        queued=jobs.admit([],self.lib,[self.track],{})
        failed=jobs.failed(queued,queued[0]['id'],'Unreadable file')
        self.assertEqual(jobs.admit(failed,self.lib,[self.track],{},retry_failed=False),failed)
        retry=jobs.admit(failed,self.lib,[self.track],{},retry_failed=True)
        self.assertEqual(len(retry),1);self.assertNotEqual(retry[0]['id'],queued[0]['id'])
        self.assertEqual(retry[0]['state'],'queued')
        with self.assertRaises(ValueError):jobs.finish(retry,queued[0]['id'])

    def test_unknown_or_ambiguous_saved_evidence_is_rejected_without_rewriting_it(self):
        value=jobs.admit([],self.lib,[self.track],{})
        cases=[{'not':'a queue'},[value[0],value[0]],[{**value[0],'state':'completed'}],
               [{**value[0],'path':'/untrusted/../example.wav'}],[{**value[0],'preferences':{'unexpected':True}}]]
        for case in cases:
            original=deepcopy(case)
            with self.assertRaises(ValueError):jobs.validate(case)
            self.assertEqual(case,original)

    def test_components_checkpoint_without_changing_preferences_and_resume_key_after_retry(self):
        queued=jobs.admit([],self.lib,[self.track],dict(first_cue=True))
        identity=queued[0]['id'];prefs=deepcopy(queued[0]['preferences'])
        self.assertEqual(jobs.component_preferences(queued[0]),{**prefs,'key':False})
        wave=jobs.wave_ready(queued,identity,'a'*64,[1,2,100,3,4])
        key=jobs.advance(wave,identity,'a'*64)
        self.assertEqual(key[0]['preferences'],prefs)
        self.assertEqual(jobs.component_preferences(key[0]),{**prefs,'bpm':False,'first_cue':False})
        saved=json.loads(json.dumps(key));jobs.validate(saved)
        failed=jobs.failed(saved,identity,'Key failed')
        retry=jobs.admit(failed,self.lib,[self.track],prefs,retry_failed=True)
        self.assertNotEqual(retry[0]['id'],identity)
        self.assertEqual((retry[0]['phase'],retry[0]['component_sha256'],retry[0]['waveform_epoch']),
                         ('key','a'*64,[1,2,100,3,4]))
        self.assertEqual(jobs.advance(retry,retry[0]['id'],'a'*64),[])
        self.assertEqual(queued[0]['phase'],'bpm')
        self.assertNotIn('waveform_sha256',queued[0])

    def test_legacy_wire_stays_valid_and_component_identity_never_rebases(self):
        queued=jobs.admit([],self.lib,[self.track],{})
        queued[0].pop('phase');original=deepcopy(queued)
        jobs.validate(queued)
        self.assertEqual(queued,original)
        self.assertEqual(jobs.phase(queued[0]),'bpm')
        disabled={**queued[0],'preferences':{**queued[0]['preferences'],'bpm':False,'key':False}}
        jobs.validate([disabled])
        self.assertFalse(jobs.component_preferences(disabled)['key'])
        ready=jobs.wave_ready(queued,queued[0]['id'],'b'*64,[1,2,100,3,4])
        for operation in (lambda:jobs.advance(ready,ready[0]['id'],'a'*64),
                          lambda:jobs.wave_ready(ready,ready[0]['id'],'a'*64,[1,2,100,3,4])):
            with self.assertRaisesRegex(ValueError,'identity changed'):operation()
        invalid=[{**original[0],'phase':'key'},
                 {**original[0],'waveform_sha256':'b'*64},
                 {**original[0],'waveform_sha256':'b'*64,'waveform_epoch':[1,2,True,3,4]}]
        for job in invalid:
            with self.assertRaises(ValueError):jobs.validate([job])

    def test_waveform_only_intent_never_enables_a_detector_component(self):
        queued=jobs.admit([],self.lib,[self.track],dict(bpm=False,key=False))
        self.assertNotIn('phase',queued[0])
        self.assertIsNone(jobs.phase(queued[0]))
        self.assertFalse(jobs.component_preferences(queued[0])['bpm'])
        self.assertFalse(jobs.component_preferences(queued[0])['key'])
        ready=jobs.wave_ready(queued,queued[0]['id'],'c'*64,[1,2,100,3,4])
        self.assertEqual(jobs.finish(ready,queued[0]['id']),[])
