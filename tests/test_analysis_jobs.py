"""Pending detection survives serialization without authority or stale rebinding."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from soundbox import analysis_jobs as jobs
from soundbox.library import Library,Track


class AnalysisJobs(unittest.TestCase):
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
