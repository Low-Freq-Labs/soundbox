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
    def test_changed_waveform_capture_is_rejected_before_detector_runs(self):
        with mock.patch('soundbox.cdj_usb.run_renderer') as renderer:
            with self.assertRaisesRegex(RuntimeError, 'captured bytes differ'):
                analysis.analyze(self.lib, self.track, dict(bpm=True, key=False), expected_sha256='f'*64)
        renderer.assert_not_called()

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

    def test_automatic_long_sets_skip_dsp_even_when_import_duration_was_unknown(self):
        from types import SimpleNamespace
        before=self.audio.read_bytes()
        for known in (0,901):
            self.track.total_time=known
            with mock.patch('soundbox.media_probe.read_media_header',return_value=SimpleNamespace(duration=901)) as header, \
                    mock.patch('soundbox.cdj_usb.run_renderer',side_effect=AssertionError('No automatic BPM/key for a long set')):
                self.assertIsNone(analysis.analyze(self.lib,self.track,automatic=True))
                self.assertEqual(header.call_count,1 if known==0 else 0)
        self.assertEqual(self.audio.read_bytes(),before)

    def test_explicit_long_analysis_remains_available_without_removing_saved_data(self):
        self.track.total_time=901
        with mock.patch('soundbox.cdj_usb.run_renderer',return_value={**self.record(),'confidence':1}) as renderer:
            result=analysis.analyze(self.lib,self.track)
        renderer.assert_called_once();self.assertEqual(result['bpm'],120)

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

    def test_manual_grid_over_inconclusive_detection_reloads_with_identity_and_provenance_guards(self):
        record = {**self.record(), 'bpm': None, 'beats': [], 'first_cue': None,
                  'confidence': .2}
        fallback = dict(beats=[[1,0,100]], cues=[dict(kind='memory',position_ms=750)],
                        provenance={'grid':'soundbox-zero-anchor'})
        self.assertIs(analysis.playback_data(fallback,record,self.audio),fallback)
        rows = [[1,0,140],[2,429,140]]
        record['grid_draft'] = dict(origin='manual',base_sha256=record['sha256'],
                                    revision=1,beats=rows,bpm=140)
        before = deepcopy(record)
        result = analysis.playback_data(fallback,record,self.audio)
        self.assertEqual(result['beats'],rows)
        self.assertIsNot(result['beats'],rows)
        self.assertEqual(result['_detected_bpm'],140)
        self.assertEqual(result['provenance']['grid'],'soundbox-manual')
        self.assertEqual(result['cues'],fallback['cues'])
        self.assertEqual(record,before)
        for provenance in ({'grid':'rekordbox'}, {'grid':'soundbox-manual'},
                           {'user_data':'soundbox'}):
            with self.subTest(provenance=provenance):
                protected = {**fallback,'provenance':provenance}
                self.assertIs(analysis.playback_data(protected,record,self.audio),protected)
        changed_epoch = deepcopy(record)
        changed_epoch['epoch'][-1] += 1
        self.assertIs(analysis.playback_data(fallback,changed_epoch,self.audio),fallback)

    def test_durable_result_and_key_only_merge_survive_state_round_trip(self):
        old = self.record()
        old['tempo_policy'] = dict(raw_bpm=119.9,candidate_bpm=120,selected=True)
        current = {**old,'bpm':None,'beats':None,'key':'9A','first_cue':None,'tempo_policy':None}
        merged = analysis.merge_result(old,current,dict(bpm=False,key=True))
        self.assertEqual(merged['beats'],old['beats']); self.assertEqual(merged['key'],'9A')
        self.assertEqual(merged['tempo_policy'],old['tempo_policy'])
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

    def test_legacy_results_remain_playable_and_partial_reanalysis_keeps_component_provenance(self):
        old={**self.record(),'engine':analysis.LEGACY_ENGINE}
        analysis.validate_result(old)
        repo=StateRepository(self.root/'library.json');repo.load()
        repo.save({'settings':{'analysis_results':{analysis.result_key(self.audio):old}}})
        restored=StateRepository(repo.path).load()['settings']['analysis_results'][analysis.result_key(self.audio)]
        self.assertEqual(restored,old)
        self.assertEqual(analysis.playback_data({},restored,self.audio)['provenance']['analysis_engine'],analysis.LEGACY_ENGINE)
        current={**self.record(),'key':'9A','key_engine':analysis.ENGINE,'grid_engine':analysis.ENGINE}
        merged=analysis.merge_result(old,current,dict(bpm=False,key=True))
        self.assertEqual(merged['beats'],old['beats'])
        self.assertEqual((merged['grid_engine'],merged['key_engine']),(analysis.LEGACY_ENGINE,analysis.ENGINE))
        for invalid in ({'engine':[]},{'grid_engine':{}},{'key_engine':'unrecognized'}):
            with self.assertRaises(ValueError):analysis.validate_result({**current,**invalid})

    def test_inconclusive_reanalysis_preserves_existing_grid_key_and_first_cue_for_same_bytes(self):
        old={**self.record(),'engine':analysis.LEGACY_ENGINE}
        current={**self.record(),'bpm':None,'beats':[],'confidence':0,'key':None,'key_name':None,'first_cue':None}
        merged=analysis.merge_result(old,current,dict(bpm=True,key=True))
        self.assertEqual((merged['bpm'],merged['beats'],merged['key'],merged['first_cue']),
                         (old['bpm'],old['beats'],old['key'],old['first_cue']))
        self.assertEqual((merged['grid_engine'],merged['key_engine']),(analysis.LEGACY_ENGINE,analysis.LEGACY_ENGINE))
        changed={**current,'sha256':'0'*64}
        fresh=analysis.merge_result(old,changed,dict(bpm=True,key=True))
        self.assertEqual(fresh['beats'],[]);self.assertIsNone(fresh['key'])

    def test_analysis_detects_a_real_tempo_change_after_the_previous_five_minute_cutoff(self):
        rate=8000;duration=420;change=310
        samples=np.zeros(rate*duration,dtype=np.float32)
        starts=list(np.arange(0,change,.5))+list(np.arange(change,duration,.4))
        n=np.arange(160,dtype=np.float32)
        kick=np.sin(n*1.1)*(1-n/160)*.8
        for second in starts:
            start=int(second*rate);samples[start:start+len(kick)]+=kick[:len(samples)-start]
        with wave.open(str(self.audio),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
            out.writeframes((samples*30000).astype('<i2').tobytes())
        before=hashlib.sha256(self.audio.read_bytes()).hexdigest()
        result=self.detect(key=False)
        self.assertEqual(result['analysis_scope'],'full')
        self.assertEqual(result['decoded_frames'],len(samples))
        self.assertEqual(result['analyzed_frames'],len(samples))
        early=[r[2] for r in result['beats'] if 60_000<r[1]<180_000]
        late=[r[2] for r in result['beats'] if 360_000<r[1]<410_000]
        self.assertTrue(early and late)
        self.assertAlmostEqual(float(np.median(early)),120,delta=1)
        self.assertAlmostEqual(float(np.median(late)),150,delta=1)
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(),before)

    def test_truncated_tail_after_the_previous_sample_cap_is_rejected(self):
        from soundbox.cdj_usb import run_renderer
        rate=8000;actual=301;declared=400
        with wave.open(str(self.audio),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
            out.writeframes(bytes(actual*rate*2))
        raw=bytearray(self.audio.read_bytes());raw[4:8]=(36+declared*rate*2).to_bytes(4,'little')
        raw[40:44]=(declared*rate*2).to_bytes(4,'little');self.audio.write_bytes(raw)
        request=self.root/'request.json'
        import json
        request.write_text(json.dumps(dict(audio=str(self.audio),bpm=True,key=False,min_bpm=70,max_bpm=180)))
        with self.assertRaisesRegex(RuntimeError,'declared duration'):
            run_renderer('analyze',self.root,request)
        self.assertEqual(self.audio.read_bytes(),raw)

    def test_real_detector_prefers_supported_integer_but_retains_measured_fractional_tempo(self):
        rate=8000;duration=120
        for bpm in (120.0,119.9,120.13,140.1):
            samples=np.zeros(rate*duration,dtype=np.float32)
            n=np.arange(160,dtype=np.float32)
            click=np.sin(n*1.1)*(1-n/160)*.8
            for second in np.arange(.2,duration,60/bpm):
                start=round(second*rate)
                samples[start:start+len(click)]+=click[:len(samples)-start]
            with wave.open(str(self.audio),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
                out.writeframes((samples*30000).astype('<i2').tobytes())
            result=self.detect(key=False)
            self.assertAlmostEqual(result['bpm'],bpm,delta=.02,msg=str(result.get('tempo_policy')))
            if bpm==120:
                self.assertEqual(result['bpm'],120)
                self.assertTrue(result['tempo_policy']['selected'])
                self.assertTrue(all(row[2]==120 for row in result['beats']))
            elif result.get('tempo_policy'):
                self.assertFalse(result['tempo_policy']['selected'])

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

    def test_bookmark_metadata_change_during_detection_keeps_result_after_exact_byte_recapture(self):
        record = {**self.record(),'confidence':1}
        before = self.audio.read_bytes()
        def bookmark(*_args,**_kwargs):
            self.audio.chmod(0o600)
            return record
        with mock.patch('soundbox.cdj_usb.run_renderer',side_effect=bookmark), \
                mock.patch('soundbox.analysis.acquire_source',wraps=analysis.acquire_source) as capture:
            result = self.detect()
        self.assertEqual(capture.call_count,2)
        self.assertEqual(capture.call_args.args[0].sha256,hashlib.sha256(before).hexdigest())
        self.assertEqual(result['bpm'],120)
        self.assertEqual(result['epoch'],analysis.identity(self.audio.stat()))
        self.assertEqual(self.audio.read_bytes(),before)

    def test_invalid_settings_and_unknown_result_rows_fail_closed(self):
        for prefs in ({'bpm_range':True},{'bpm_range':10},{'key':'yes'},{'process':'Turbo'}):
            with self.assertRaises(ValueError):analysis.options(prefs)
        for rows in ([[1,0,120],[2,0,120]], [[1,0,120],[2,20000,120]], [[1,0,float('nan')]]):
            record = self.record();record['beats']=rows
            with self.assertRaises(ValueError):analysis.validate_result(record)
