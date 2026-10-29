"""Captured DSP, shared audio identity, source safety and partial-result rules."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np

from soundbox import analysis
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository, InvalidState


class CapturedAnalysis(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.audio = self.root/'clicks.wav'
        rate = 44100
        samples = np.zeros(rate * 16, dtype=np.float32)
        for start in range(0, len(samples), rate // 2):
            n = np.arange(220, dtype=np.float32)
            samples[start:start+220] = np.sin(n*.7)*(1-n/220)*.8
        with wave.open(str(self.audio),'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes((samples*30000).astype('<i2').tobytes())
        self.track = Track('sb-original','Clicks','Test',self.audio.as_uri(),imported=True)
        self.lib = Library({self.track.track_id:self.track},[],'synthetic')

    def detect(self, **preferences):
        return analysis.analyze(self.lib,self.track,{**analysis.DEFAULTS,**preferences})

    def test_real_engine_outputs_milliseconds_bpm_and_keeps_canonical_bytes(self):
        before = self.audio.read_bytes()
        result = self.detect(key=False,first_cue=True)
        self.assertAlmostEqual(result['bpm'],120,delta=.1)
        self.assertGreater(len(result['beats']),25)
        self.assertAlmostEqual(result['beats'][2][2],120,delta=.1)
        self.assertTrue(400 <= result['beats'][1][1]-result['beats'][0][1] <= 600)
        self.assertEqual(result['first_cue'],result['beats'][0][1])
        self.assertEqual(result['sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(self.audio.read_bytes(),before)
        self.assertEqual(result['epoch'],analysis.identity(self.audio.stat()))

    def record(self):
        return dict(engine=analysis.ENGINE,path=str(self.audio),sha256=hashlib.sha256(self.audio.read_bytes()).hexdigest(),
            size=self.audio.stat().st_size,epoch=analysis.identity(self.audio.stat()),duration=16,
            beats=[[1,125,120],[2,625,120]],bpm=120,key='8B',key_name='C',first_cue=125)

    def test_direct_import_and_later_rekordbox_identity_share_saved_analysis(self):
        record = self.record()
        values = {analysis.result_key(self.audio):record}
        native = Track('new-native-id','Clicks','Test',self.audio.as_uri(),source_uuid='new-uuid')
        self.assertEqual(analysis.saved_result(values,native),record)
        result = analysis.playback_data({'beats':[], 'cues':[]},record,self.audio)
        self.assertEqual(result['beats'],record['beats'])
        self.assertEqual(result['cues'][0]['position_ms'],125)
        self.assertEqual(result['provenance']['grid'],'soundbox-detected')
        source = dict(beats=[[1,0,100]],cues=[dict(kind='memory',position_ms=750)],provenance={'grid':'rekordbox'})
        self.assertEqual(analysis.playback_data(source,record,self.audio),source)
        manual = {**source,'provenance':{'user_data':'soundbox'}}
        self.assertEqual(analysis.playback_data(manual,record,self.audio),manual)
        legacy_manual = {**source, 'provenance': {'grid':'soundbox-manual'}}
        self.assertEqual(analysis.playback_data(legacy_manual,record,self.audio),legacy_manual)
        self.audio.write_bytes(b'changed')
        self.assertEqual(analysis.playback_data({},record,self.audio),{})

    def test_durable_result_and_key_only_merge_survive_state_round_trip(self):
        old = self.record()
        current = {**old,'bpm':None,'beats':None,'key':'9A','first_cue':None}
        merged = analysis.merge_result(old,current,dict(bpm=False,key=True))
        self.assertEqual(merged['beats'],old['beats']); self.assertEqual(merged['key'],'9A')
        repo = StateRepository(self.root/'library.json'); repo.load()
        repo.save({'settings':{'analysis_results':{analysis.result_key(self.audio):merged}}})
        self.assertEqual(StateRepository(repo.path).load()['settings']['analysis_results'][analysis.result_key(self.audio)],merged)
        invalid = deepcopy(merged); invalid['beats'][0] = [1,120,5000]
        with self.assertRaises(InvalidState):
            repo.save({'settings':{'analysis_results':{analysis.result_key(self.audio):invalid}}})
        self.assertEqual(StateRepository(repo.path).load()['settings']['analysis_results'][analysis.result_key(self.audio)],merged)
        previous = repo.path.read_bytes()
        with mock.patch('soundbox.state_repository._MAX_STATE_BYTES', len(previous)+64):
            with self.assertRaisesRegex(InvalidState, 'safety limit'):
                repo.save({'settings': {'large_result': 'x'*(len(previous)+128)}})
        self.assertEqual(repo.path.read_bytes(), previous)

    def test_cancel_does_not_publish_a_result_or_change_audio(self):
        before = self.audio.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            analysis.analyze(self.lib,self.track,should_cancel=lambda:True)
        self.assertEqual(before,self.audio.read_bytes())

    def test_source_change_during_native_work_rejects_result(self):
        record = self.record()
        def changed(*_args,**_kwargs):
            self.audio.write_bytes(b'replacement')
            return record
        with mock.patch('soundbox.cdj_usb.run_renderer',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'Source changed'):
                self.detect()

    def test_invalid_settings_and_unknown_result_rows_fail_closed(self):
        for prefs in ({'bpm_range':True},{'bpm_range':10},{'key':'yes'},{'process':'Turbo'}):
            with self.assertRaises(ValueError):analysis.options(prefs)
        for rows in ([[1,0,120],[2,0,120]], [[1,0,120],[2,20000,120]], [[1,0,float('nan')]]):
            record = self.record();record['beats']=rows
            with self.assertRaises(ValueError):analysis.validate_result(record)
