"""Saved-component proposals preserve all other metadata and reject mismatched inputs."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import struct
import unittest
from uuid import uuid4

from research.native_library import component_analysis as component
from soundbox.analysis import ENGINE
from soundbox.native_media import MediaIdentity
from soundbox.rekordbox_grid import AnalysisImage
from soundbox.rekordbox_write import ConflictError, WriteProofError
import test_fixture_analysis as fixtures


class SavedComponentPlans(unittest.TestCase):
    def setUp(self):
        old=fixtures.InitialFixtureAnalysis();old.setUp();self.base=deepcopy(old.base)
        self.audio=MediaIdentity('/explicit/ユニコード.wav',(1,42,400000,100,100),'a'*64,12,
                                  'wav','pcm_s24le',59259,4800,1)
        self.duration=self.audio.duration_numerator/self.audio.duration_denominator
        self.result=dict(engine=ENGINE,path=self.audio.path,epoch=list(self.audio.epoch),size=self.audio.epoch[2],
            sha256=self.audio.sha256,duration=self.duration,bpm=120.0,key='8B',
            beats=[[1,0,120.0],[2,500,120.0],[3,1000,120.0]],first_cue=None)
        self.base['track'].update(FolderPath=self.audio.path,FileSize=self.audio.epoch[2],Length=12)
        self.seeds=dict(track_stamp=old.seeds['track_stamp'],file_uuids=old.seeds['file_uuids'],file_stamps=old.seeds['file_stamps'])
        self.technical=dict(sample_rate=48000,channels=2,bits=24)
        columns=round(self.duration*150);self.images={}
        for ext,raw in fixtures.packet(Path(self.audio.path).name).items():
            image=AnalysisImage.parse(raw);sections=[]
            for section in image.sections:
                body=section.raw
                if section.kind in (b'PWV3',b'PWV5',b'PWV7'):
                    unit={b'PWV3':1,b'PWV5':2,b'PWV7':3}[section.kind]
                    body=body[:16]+struct.pack('>I',columns)+body[20:24]+bytes(columns*unit)
                    body=body[:8]+struct.pack('>I',len(body))+body[12:]
                elif section.kind==b'PQTZ':
                    body=body[:20]+struct.pack('>I',3)+b''.join(struct.pack('>HHI',b,round(t*100),ms) for b,ms,t in self.result['beats'])
                    body=body[:8]+struct.pack('>I',len(body))+body[12:]
                sections.append(body)
            body=b''.join(sections);self.images[ext]=image.header[:8]+struct.pack('>I',len(image.header)+len(body))+image.header[12:]+body

    def plan(self,**kwargs):
        args=dict(base=self.base,audio=self.audio,images=self.images,share_root='/explicit/share',seeds=self.seeds,
                  result=self.result,technical=self.technical);args.update(kwargs);return component.plan(**args)

    def test_24_bit_nonintegral_endpoint_unicode_and_saved_grid_without_mixer(self):
        original=deepcopy(self.base);candidate=self.plan();changes=candidate['changes'];target=changes[0]['after']
        self.assertEqual([c['table'] for c in changes],['djmdContent','contentFile','contentFile','contentFile','agentRegistry'])
        self.assertEqual((target['BPM'],target['KeyID'],target['Length'],target['BitDepth'],target['SampleRate']),(12000,'789',12,24,48000))
        self.assertEqual((target['Title'],target['Reserved4']),(original['track']['Title'],original['track']['Reserved4']))
        self.assertEqual(changes[-1]['after']['int_1'],61);self.assertEqual(self.base,original)
        self.assertNotIn('djmdMixerParam',[c['table'] for c in changes]);self.assertFalse(candidate['source_write_authority'])

    def test_changed_grid_digest_key_or_audio_framing_cannot_publish(self):
        for field,value in [('sha256','b'*64),('key','7A'),('duration',13),('first_cue',0)]:
            result=deepcopy(self.result);result[field]=value
            with self.subTest(field=field),self.assertRaises((WriteProofError,ValueError)):self.plan(result=result)
        result=deepcopy(self.result);result['beats'][1][1]+=1
        with self.assertRaises(WriteProofError):self.plan(result=result)
        with self.assertRaises(WriteProofError):self.plan(technical=dict(self.technical,bits=16))
        with self.assertRaises(WriteProofError):self.plan(audio=replace(self.audio,wave_format_tag=65534,seconds=0))

    def test_existing_musical_data_sync_identity_or_history_remain_protected(self):
        for field,value in [('Analysed',128),('BPM',12000),('KeyID','789'),('rb_local_synced',1),('CueUpdated','1')]:
            base=deepcopy(self.base);base['track'][field]=value
            with self.subTest(field=field),self.assertRaises(ConflictError):self.plan(base=base)
        for field in ('files','mixers','cues','aggregates'):
            base=deepcopy(self.base);base[field]=[dict(ContentID='123')]
            with self.subTest(field=field),self.assertRaises(ConflictError):self.plan(base=base)
        seeds=deepcopy(self.seeds);seeds['file_uuids']['.DAT']=seeds['file_uuids']['.EXT']
        with self.assertRaises(ConflictError):self.plan(seeds=seeds)
        base=deepcopy(self.base);base['keys']*=2
        with self.assertRaises(WriteProofError):self.plan(base=base)

    def test_wrong_waveform_extent_path_or_embedded_cues_fail_closed(self):
        for ext,kind,offset in [('.EXT',b'PWV3',16),('.EXT',b'PCOB',16),('.2EX',b'PWVC',14),('.DAT',b'PPTH',16)]:
            images=dict(self.images);raw=bytearray(images[ext]);raw[raw.index(kind)+offset]^=1;images[ext]=bytes(raw)
            with self.subTest(kind=kind),self.assertRaises(WriteProofError):self.plan(images=images)
        images=dict(self.images);images['.DAT']=images['.DAT'][:-1]
        with self.assertRaises(WriteProofError):self.plan(images=images)

    def test_component_uuid_collision_preserves_all_history(self):
        base=deepcopy(self.base);base['files']=[dict(ID='other',UUID=self.seeds['file_uuids']['.DAT'],ContentID='other')]
        with self.assertRaises(ConflictError):self.plan(base=base)
        base=deepcopy(self.base);base['track']['UUID']=str(uuid4())
        self.assertNotEqual(self.plan(base=base)['changes'][1]['after']['ID'],self.plan()['changes'][1]['after']['ID'])
