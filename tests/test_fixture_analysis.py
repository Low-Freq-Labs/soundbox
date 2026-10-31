"""Pure initial metadata plans on synthetic packet frames; no source/file reads."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import struct
import unittest
from uuid import uuid4

from research.native_library import fixture_analysis as analysis, registration
from soundbox.rekordbox_write import ConflictError, WriteProofError


def packet(name):
    def section(kind,header=b'',payload=b''):
        return kind+struct.pack('>II',12+len(header),12+len(header)+len(payload))+header+payload
    def image(sections):
        body=b''.join(sections)
        return b'PMAI'+struct.pack('>II',28,28+len(body))+bytes.fromhex('00000001000100000001000000000000')+body
    text=('?/'+name+'\0').encode('utf-16-be');path=section(b'PPTH',struct.pack('>I',len(text)),text)
    cues=[section(b'PCOB',struct.pack('>III',kind,0,0xffffffff)) for kind in (1,0)]
    extended=[section(b'PCO2',struct.pack('>II',kind,0)) for kind in (1,0)]
    grid=section(b'PQTZ',struct.pack('>III',0,0x80000,64),b''.join(struct.pack('>HHI',i%4+1,12000,249+500*i) for i in range(64)))
    dat=image([path,section(b'PVBR',bytes(4),bytes(1604)),grid,section(b'PWAV',struct.pack('>II',400,0x10000),bytes(400)),
               section(b'PWV2',struct.pack('>II',100,0x10000),bytes(100)),*cues])
    ext=image([path,section(b'PWV3',struct.pack('>III',1,4800,0x960000),bytes(4800)),*cues,*extended,
        section(b'PQT2',bytes.fromhex('000000000100000200000000')+bytes(32)),
        section(b'PWV5',struct.pack('>III',2,4800,0x960305),bytes(9600)),section(b'PWV4',struct.pack('>III',6,1200,0),bytes(7200))])
    extra=image([path,section(b'PWV7',struct.pack('>III',3,4800,0x960000),bytes(14400)),
        section(b'PWV6',struct.pack('>II',3,1200),bytes(3600)),section(b'PWVC',bytes(2),bytes.fromhex('00bb012c01f4'))])
    return {'.DAT':dat,'.EXT':ext,'.2EX':extra}


class InitialFixtureAnalysis(unittest.TestCase):
    def setUp(self):
        self.audio=registration.WaveIdentity('/explicit/fixture.wav',(1,42,5644844,100,100),analysis.REFERENCE_SHA,32)
        self.track=registration.build_row(self.audio,identity='123',uuid=str(uuid4()),library='456',device=str(uuid4()),
             timestamp='2026-10-30 12:00:00.000 +00:00',day='2026-10-30',counter=40)
        self.track['Title']='Preserve existing display';self.track['Reserved4']='opaque native data'
        self.counter=dict(registry_id='localUpdateCount',int_1=41,updated_at='keep counter clock',extra='keep opaque')
        self.base=dict(track=self.track,counter=self.counter,keys=[dict(ID='789',ScaleName='8B',rb_local_deleted=0)],files=[],mixers=[],cues=[],aggregates=[])
        self.seeds=dict(mixer_id=str(uuid4()),mixer_uuid=str(uuid4()),mixer_stamp='2026-10-30 12:00:01.000 +00:00',
            track_stamp='2026-10-30 12:00:01.001 +00:00',file_uuids={ext:str(uuid4()) for ext in analysis.FILE_ORDER},
            file_stamps={ext:f'2026-10-30 12:00:01.{n:03d} +00:00' for n,ext in enumerate(analysis.FILE_ORDER,2)})
        self.images=packet('fixture.wav')

    def plan(self,**changes):
        return analysis.plan(changes.get('base',self.base),changes.get('audio',self.audio),changes.get('images',self.images),
                             share_root='/explicit/library/share',seeds=changes.get('seeds',self.seeds))

    def test_native_counter_groups_metadata_and_file_hashes_are_constructed(self):
        original=deepcopy(self.base);result=self.plan();changes=result['changes']
        self.assertEqual([r['after']['rb_local_usn'] for r in changes[:-1]],[54,58,59,60,61])
        self.assertEqual(changes[-1]['after'],dict(self.counter,int_1=61))
        track=changes[1]['after']
        self.assertEqual((track['BPM'],track['KeyID'],track['Length'],track['ContentLink'],track['Analysed']),(12000,'789',31,787982,105))
        self.assertEqual((track['AnalysisUpdated'],track['TrackInfoUpdated']),('1','2'))
        self.assertEqual((track['Title'],track['Reserved4']),('Preserve existing display','opaque native data'))
        mixer=changes[0]['after']
        self.assertEqual((mixer['GainHigh'],mixer['GainLow'],mixer['PeakHigh'],mixer['PeakLow']),(16420,58915,16136,43008))
        for row,ext in zip((r['after'] for r in changes[2:5]),analysis.FILE_ORDER,strict=True):
            self.assertEqual(row['Size'],len(self.images[ext]));self.assertEqual(row['Hash'],hashlib.md5(self.images[ext]).hexdigest())
            self.assertTrue(row['rb_local_path'].startswith('/explicit/library/share/PIONEER/USBANLZ/'))
            self.assertIn('%2FPIONEER%2FUSBANLZ%2F',row['ID'])
        self.assertEqual(self.base,original);self.assertFalse(result['source_write_authority'])

    def test_other_audio_or_existing_musical_data_cannot_be_overwritten(self):
        with self.assertRaises(WriteProofError):self.plan(audio=replace(self.audio,sha256='0'*64))
        for name,value in (('BPM',12000),('KeyID','789'),('AnalysisUpdated','1'),('SamplerTrackInfo',1),('rb_local_synced',1)):
            base=deepcopy(self.base);base['track'][name]=value
            with self.subTest(name=name),self.assertRaises(ConflictError):self.plan(base=base)
        for name in ('files','mixers','cues','aggregates'):
            base=deepcopy(self.base);base[name]=[dict(ContentID='123')]
            with self.subTest(name=name),self.assertRaises(ConflictError):self.plan(base=base)

    def test_ambiguous_key_uuid_history_and_backwards_clocks_fail_closed(self):
        base=deepcopy(self.base);base['keys']*=2
        with self.assertRaises(WriteProofError):self.plan(base=base)
        seeds=deepcopy(self.seeds);seeds['mixer_uuid']=seeds['mixer_id']
        with self.assertRaises(WriteProofError):self.plan(seeds=seeds)
        seeds=deepcopy(self.seeds);seeds['track_stamp']='2026-10-29 12:00:01.000 +00:00'
        with self.assertRaises(WriteProofError):self.plan(seeds=seeds)
        base=deepcopy(self.base);base['mixers']=[dict(ID=self.seeds['mixer_id'],UUID=str(uuid4()),ContentID='other')]
        with self.assertRaises(ConflictError):self.plan(base=base)

    def test_wrong_paths_frames_embedded_cues_and_opaque_config_are_rejected(self):
        with self.assertRaises(WriteProofError):self.plan(images=packet('another.wav'))
        for kind in (b'PWV3',b'PCOB',b'PWVC'):
            images=deepcopy(self.images);ext='.2EX' if kind==b'PWVC' else '.EXT'
            raw=bytearray(images[ext]);offset=raw.index(kind)
            raw[offset+(14 if kind==b'PWVC' else 16)]=1
            images[ext]=bytes(raw)
            with self.subTest(kind=kind),self.assertRaises(WriteProofError):self.plan(images=images)
        images=deepcopy(self.images);images['.DAT']=images['.DAT'][:-1]
        with self.assertRaises(WriteProofError):self.plan(images=images)


if __name__=='__main__':unittest.main()
