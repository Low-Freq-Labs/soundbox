"""Import-only conversion: signal, metadata, atomic recovery and musical edits."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

import numpy as np
import soundfile as sf

from soundbox import analysis, local_grid, local_cues, wave_conversion as conversion, prep_edits
from soundbox.media_probe import read_media_header


def wave(path, samples=None, *, rate=44100, channels=2, extensible=False):
    if samples is None:samples=np.tile(np.array([-.75,-.125,0,.125,.75],dtype='<f4'),rate*channels//5+1)[:rate*channels]
    samples=np.asarray(samples,dtype='<f4')
    fmt=struct.pack('<HHIIHH',65534 if extensible else 3,channels,rate,rate*channels*4,channels*4,32)
    if extensible:fmt+=struct.pack('<HHI',22,32,3)+bytes.fromhex('0300000000001000800000aa00389b71')
    metadata=[(b'JUNK',b'a'),(b'LIST',b'INFOIART'+struct.pack('<I',8)+b'artist\0\0'),
              (b'LIST',b'INFOINAM'+struct.pack('<I',6)+b'title\0'),
              (b'bext',bytes(602)),(b'cue ',struct.pack('<I',0)),(b'fact',struct.pack('<I',len(samples)//channels))]
    chunks=[*metadata[:2],(b'fmt ',fmt),*metadata[2:],(b'data',samples.tobytes()),(b'ID3 ',b'tag')]
    packed=b''.join(k+struct.pack('<I',len(v))+v+(b'\0' if len(v)&1 else b'') for k,v in chunks)
    path.write_bytes(b'RIFF'+struct.pack('<I',len(packed)+4)+b'WAVE'+packed)
    return metadata,samples


def other_chunks(path):
    raw=path.read_bytes();position=12;values=[]
    while position<len(raw):
        kind=raw[position:position+4];length=struct.unpack_from('<I',raw,position+4)[0]
        if kind not in (b'fmt ',b'data'):values.append(raw[position:position+8+length+(length&1)])
        position+=8+length+(length&1)
    return values


class WaveConversion(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();(self.root/'music').mkdir()
        self.path=self.root/'music/song.wav';self.recovery=self.root/'originals';self.op=str(uuid4())

    def convert(self, **kwargs):return conversion.convert(self.path,self.recovery,self.op,'sb-song',**kwargs)

    def test_pcm24_preserves_gain_frames_metadata_and_original_backup(self):
        _,samples=wave(self.path);before=self.path.read_bytes();metadata=other_chunks(self.path)
        receipt=self.convert(expected_sha256=hashlib.sha256(before).hexdigest())
        header=read_media_header(self.path);actual,rate=sf.read(self.path,dtype='float64',always_2d=True)
        self.assertEqual((header.bits,header.duration,header.wave_format_tag),(24,Fraction(1),1))
        self.assertEqual(rate,44100);self.assertLessEqual(np.max(np.abs(actual.reshape(-1)-samples)),1/8388608)
        self.assertEqual(other_chunks(self.path),metadata);self.assertEqual(receipt['gain'],1)
        originals=list(self.recovery.rglob('original.wav'));self.assertEqual(len(originals),1)
        self.assertEqual(originals[0].read_bytes(),before)
        self.assertEqual(receipt['after_sha256'],hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(list(self.path.parent.glob('.soundbox-wave-*')))

    def test_extensible_and_multichannel_block_edges_keep_every_frame(self):
        for channels,extensible in ((1,False),(2,True),(3,True)):
            with self.subTest(channels=channels):
                samples=np.linspace(-.99,.99,300003*channels,dtype='<f4')
                wave(self.path,samples,channels=channels,extensible=extensible)
                out=self.root/'out.wav';receipt=conversion.render(self.path,out)
                actual,rate=sf.read(out,dtype='float64',always_2d=True)
                self.assertEqual(actual.shape,(300003,channels));self.assertEqual(rate,44100)
                self.assertLessEqual(np.max(np.abs(actual.reshape(-1)-samples)),1/8388608)
                self.assertEqual(receipt['gain'],1)

    def test_overrange_is_untouched_unless_gain_reduction_is_explicit(self):
        wave(self.path,np.array([-1.33,1.2,-.5,.5],dtype='<f4'));before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'gain reduction is required'):self.convert()
        self.assertEqual(self.path.read_bytes(),before)
        receipt=self.convert(reduce_gain=True)
        actual,_=sf.read(self.path,dtype='float64');expected=np.array([-1.33,1.2,-.5,.5],dtype='<f4').reshape(-1,2)*receipt['gain']
        self.assertLessEqual(np.max(np.abs(actual-expected)),1/8388608)
        self.assertLess(receipt['gain_db'],0);self.assertLessEqual(np.abs(actual).max(),1)

    def test_explicit_same_gain_clipping_matches_the_auditioned_pcm24_samples(self):
        samples=np.array([-1.33,1.2,-1,1,-.5,.5],dtype='<f4')
        wave(self.path,samples);before=self.path.read_bytes();receipt=self.convert(clip_overs=True)
        actual,_=sf.read(self.path,dtype='float64',always_2d=True)
        expected=np.rint(samples.astype(np.float64)*8388608).clip(-8388608,8388607)/8388608
        np.testing.assert_array_equal(actual.reshape(-1),expected)
        self.assertEqual(receipt['gain'],1);self.assertEqual(receipt['gain_db'],0)
        self.assertEqual(receipt['clipped_samples'],2)
        self.assertEqual(next(self.recovery.rglob('original.wav')).read_bytes(),before)

    def test_nonfinite_and_incomplete_samples_never_replace_source(self):
        for samples in ([0,np.nan],[0,np.inf]):
            wave(self.path,samples);before=self.path.read_bytes()
            with self.assertRaisesRegex(ValueError,'non-finite'):conversion.render(self.path,self.root/'out.wav')
            self.assertEqual(self.path.read_bytes(),before)
        wave(self.path);raw=self.path.read_bytes();self.path.write_bytes(raw[:-1])
        with self.assertRaises(ValueError):self.convert()
        self.assertEqual(self.path.read_bytes(),raw[:-1])

    def test_integer_wave_and_other_containers_are_untouched_without_backups(self):
        sf.write(self.path,np.zeros((100,2)),44100,subtype='PCM_16');before=self.path.read_bytes()
        self.assertIsNone(self.convert());self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.recovery.exists())
        other=self.root/'music/song.flac';sf.write(other,np.zeros((100,2)),44100)
        self.assertIsNone(conversion.convert(other,self.recovery,self.op,'sb-flac'))
        # Ordinary supported media on a device retains its existing read-only
        # import route. Only an actual source rewrite is forbidden there.
        relative=Path.is_relative_to
        def on_device(path,parent):
            return True if path==self.path and parent=='/Volumes' else relative(path,parent)
        with mock.patch.object(Path,'is_relative_to',on_device):
            self.assertIsNone(self.convert())
            wave(self.path);floating=self.path.read_bytes()
            with self.assertRaisesRegex(ValueError,'Device originals'):self.convert()
            self.assertEqual(self.path.read_bytes(),floating)

    def test_each_crash_prefix_recovers_without_duplicate_replacement(self):
        for phase in ('backup','prepared','replaced','committed'):
            with self.subTest(phase=phase):
                self.op=str(uuid4());wave(self.path);before=self.path.read_bytes()
                def fail(current):
                    if current==phase:raise RuntimeError('power lost')
                with mock.patch.object(conversion,'_fault',side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError,'power lost'):self.convert()
                receipt=self.convert();after=self.path.read_bytes();inode=self.path.stat().st_ino
                self.assertEqual(self.convert()['after_sha256'],receipt['after_sha256'])
                self.assertEqual(self.path.stat().st_ino,inode);self.assertEqual(self.path.read_bytes(),after)
                self.assertEqual((self.recovery/self.op/conversion.result_key(self.path)/'original.wav').read_bytes(),before)

    def test_changed_source_or_native_guard_does_not_get_overwritten(self):
        wave(self.path);original=self.path.read_bytes()
        def changed(phase):
            if phase=='prepared':self.path.write_bytes(original+b'other owner')
        with mock.patch.object(conversion,'_fault',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed before replacement'):self.convert()
        self.assertEqual(self.path.read_bytes(),original+b'other owner')
        with self.assertRaisesRegex(ValueError,'changed during conversion recovery'):self.convert()
        self.op=str(uuid4());wave(self.path)
        with self.assertRaisesRegex(RuntimeError,'native owner'):
            self.convert(guard=lambda:(_ for _ in ()).throw(RuntimeError('native owner')))
        self.assertEqual(self.path.read_bytes(),original)

    def test_symlink_hardlink_redirected_recovery_and_wrong_digest_fail_closed(self):
        wave(self.path);original=self.path.read_bytes()
        with self.assertRaises(RuntimeError):self.convert(expected_sha256='0'*64)
        linked=self.path.with_name('linked.wav');os.link(self.path,linked)
        with self.assertRaisesRegex(ValueError,'one link'):self.convert()
        linked.unlink();moved=self.path.with_name('original.wav');self.path.rename(moved);self.path.symlink_to(moved)
        with self.assertRaisesRegex(ValueError,'Canonical'):self.convert()
        self.assertEqual(moved.read_bytes(),original)

    def test_tampered_backup_stage_or_receipt_is_never_recovered_over_source(self):
        for damaged in ('original.wav','stage','receipt'):
            with self.subTest(damaged=damaged):
                self.op=str(uuid4());wave(self.path);original=self.path.read_bytes()
                with mock.patch.object(conversion,'_fault',side_effect=lambda p:(_ for _ in ()).throw(RuntimeError()) if p=='prepared' else None):
                    with self.assertRaises(RuntimeError):self.convert()
                folder=self.recovery/self.op/conversion.result_key(self.path)
                receipt=json.loads((folder/'receipt.json').read_bytes())
                if damaged=='stage':Path(receipt['stage']).write_bytes(b'bad')
                elif damaged=='receipt':
                    receipt['path']='/other/song.wav';(folder/'receipt.json').write_text(json.dumps(receipt))
                else:(folder/damaged).write_bytes(b'bad')
                with self.assertRaises(ValueError):self.convert()
                self.assertEqual(self.path.read_bytes(),original)

    def record(self):
        return dict(engine=analysis.ENGINE,path=str(self.path),sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            epoch=analysis.identity(self.path.stat()),size=self.path.stat().st_size,duration=8.,bpm=120.,key='1A',
            beats=[[i%4+1,i*500,120.] for i in range(16)],first_cue=None,source_context={})

    def test_latest_manual_grid_cues_and_pending_intents_survive_rebinding_and_replay(self):
        wave(self.path,np.tile(np.array([-.5,.5],dtype='<f4'),44100*8));record=self.record()
        before=local_cues.edit(local_grid.edit(record,'shift',1),'hot',1,'add',1234)
        imported={'sb-song':dict(path=str(self.path),size=record['size'],time=8)}
        intent=dict(version=1,id=str(uuid4()),track_id='sb-song',source='/native/master.db',library_id='123',
            path=str(self.path),sha256=record['sha256'],epoch=record['epoch'],duration=8.,native_ready=False,
            grid=before['beats'],cues=[],operations=[],prepared=None,error='')
        intent=prep_edits.operation(intent,'cue','add',page='hot',slot=2,position=2500)
        settings=dict(analysis_results={analysis.result_key(self.path):before},pending_prep_edits=[intent])
        baseline=deepcopy(settings);receipt=self.convert()
        updated,imports=conversion.acknowledge(settings,imported,[receipt])
        rebound=updated['analysis_results'][analysis.result_key(self.path)]
        for field in ('beats','bpm','key','first_cue'):self.assertEqual(rebound[field],before[field])
        for field in ('grid_draft','cue_draft'):
            self.assertEqual({k:v for k,v in rebound[field].items() if k!='base_sha256'},
                             {k:v for k,v in before[field].items() if k!='base_sha256'})
        self.assertEqual(rebound['conversion_origin']['sha256'],record['sha256'])
        self.assertEqual(updated['pending_prep_edits'][0]['operations'],intent['operations'])
        self.assertEqual(settings,baseline);self.assertEqual(imported['sb-song']['size'],record['size'])
        self.assertEqual(imports['sb-song']['size'],self.path.stat().st_size)
        again=conversion.acknowledge(updated,imports,[receipt]);self.assertEqual(again,(updated,imports))

    def test_unrelated_analysis_and_prepared_native_edits_cannot_be_rebound(self):
        wave(self.path,np.zeros(44100*8*2));record=self.record();receipt=self.convert()
        changed={**record,'sha256':'f'*64}
        with self.assertRaisesRegex(ValueError,'different conversion bytes'):conversion.rebind(changed,receipt)
        changed={**record,'duration':9.}
        with self.assertRaisesRegex(ValueError,'timing differs'):conversion.rebind(changed,receipt)
