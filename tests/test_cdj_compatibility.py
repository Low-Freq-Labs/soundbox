"""Copy-only common-player conversion preserves timing and the source bytes."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from soundbox import cdj_compatibility as compatibility
from soundbox.media_probe import read_media_header


class CommonPlayerFormats(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()

    def audio(self,name,rate=44100,subtype='PCM_24'):
        path=self.root/name
        t=np.arange(rate)/rate
        samples=np.stack((.5*np.sin(2*np.pi*1000*t),.25*np.cos(2*np.pi*1000*t)),axis=1)
        sf.write(path,samples,rate,subtype=subtype)
        return path,samples

    def test_standard_pcm_is_accepted_and_float_high_rate_and_flac_are_candidates(self):
        for name,rate,subtype,expected in (('normal.wav',44100,'PCM_24','compatible'),
                ('normal.aiff',48000,'PCM_16','compatible'),('float.wav',44100,'FLOAT','convert'),
                ('high.wav',96000,'PCM_16','convert'),('lossless.flac',44100,'PCM_24','convert')):
            with self.subTest(name=name):
                path,_=self.audio(name,rate,subtype)
                before=path.read_bytes();self.assertEqual(compatibility.inspect(path)['status'],expected)
                self.assertEqual(path.read_bytes(),before)

    def test_float_and_flac_copies_preserve_gain_channels_and_every_sample(self):
        for name,subtype in (('float.wav','FLOAT'),('lossless.flac','PCM_24')):
            with self.subTest(name=name):
                source,_=self.audio(name,subtype=subtype);before=hashlib.sha256(source.read_bytes()).digest()
                target=self.root/(source.stem+'-converted.wav');compatibility.render(source,target)
                actual,rate=sf.read(target,dtype='float64');expected,_=sf.read(source,dtype='float64')
                self.assertEqual(rate,44100);self.assertEqual(actual.shape,expected.shape)
                self.assertLessEqual(np.max(np.abs(actual-expected)),1/8388608)
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
                self.assertEqual(compatibility.inspect(target)['status'],'compatible')

    def test_extended_pcm_header_normalization_preserves_ordered_metadata_and_audio_bytes(self):
        from test_media_probe import wave
        from test_wave_conversion import other_chunks
        source=self.root/'extended.wav';wave(source,extensible=True);raw=source.read_bytes()
        target=self.root/'canonical.wav';result=compatibility.render(source,target)
        self.assertEqual(source.read_bytes(),raw);self.assertEqual(other_chunks(target),other_chunks(source))
        expected,rate=sf.read(source,dtype='int32');actual,actual_rate=sf.read(target,dtype='int32')
        np.testing.assert_array_equal(actual,expected);self.assertEqual(actual_rate,rate)
        self.assertEqual(read_media_header(target).wave_format_tag,1)
        self.assertEqual(result['measurements']['max_error'],0)

    def test_resampling_keeps_duration_pitch_channel_order_and_source_bytes(self):
        for rate,expected_rate in ((96000,48000),(88200,44100)):
            with self.subTest(rate=rate):
                source,_=self.audio(f'high{rate}.wav',rate=rate,subtype='PCM_16');raw=source.read_bytes()
                target=self.root/f'converted{rate}.wav';result=compatibility.render(source,target)
                samples,actual_rate=sf.read(target,dtype='float64');header=read_media_header(target)
                self.assertEqual((actual_rate,header.channels,header.bits),(expected_rate,2,24))
                self.assertEqual(float(header.duration),1);self.assertEqual(source.read_bytes(),raw)
                frequency=np.fft.rfftfreq(len(samples),1/actual_rate)[np.argmax(np.abs(np.fft.rfft(samples[:,0])))]
                self.assertEqual(frequency,1000);self.assertAlmostEqual(np.max(samples[200:-200,0]),.5,places=3)
                self.assertAlmostEqual(np.max(samples[200:-200,1]),.25,places=3)
                self.assertEqual(result['measurements']['gain'],1)

    def test_cancellation_unknown_input_and_destination_collision_never_touch_source(self):
        source,_=self.audio('source.wav',subtype='FLOAT');raw=source.read_bytes();target=self.root/'out.wav'
        with self.assertRaisesRegex(RuntimeError,'cancelled'):compatibility.render(source,target,should_cancel=lambda:True)
        self.assertFalse(target.exists());target.write_bytes(b'occupant')
        with self.assertRaises(ValueError):compatibility.render(source,target)
        self.assertEqual(target.read_bytes(),b'occupant');self.assertEqual(source.read_bytes(),raw)
        unknown=self.root/'not-audio.mp3';unknown.write_bytes(bytes(100))
        self.assertEqual(compatibility.inspect(unknown)['status'],'unchecked')
