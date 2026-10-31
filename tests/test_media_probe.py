"""Header probes skip PCM payloads and reject incompatible/truncated framing."""
from fractions import Fraction
from pathlib import Path
import struct
import tempfile
import unittest

from soundbox.media_probe import read_media_header


def wave(path,*,extensible=False,subtype=1):
    bits=24 if extensible else 16;rate=48000 if extensible else 44100;align=2*bits//8
    fmt=struct.pack('<HHIIHH',65534 if extensible else 1,2,rate,rate*align,align,bits)
    if extensible:fmt+=struct.pack('<HHI',22,bits,3)+struct.pack('<I',subtype)+bytes.fromhex('00001000800000aa00389b71')
    chunks=b'JUNK'+struct.pack('<I',28)+bytes(28)+b'fmt '+struct.pack('<I',len(fmt))+fmt
    chunks+=b'data'+struct.pack('<I',rate*align)+bytes(rate*align)
    path.write_bytes(b'RIFF'+struct.pack('<I',4+len(chunks))+b'WAVE'+chunks)


class MediaProbe(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def test_classic_junk_and_extensible_pcm_headers_keep_exact_frame_duration(self):
        for extensible in (False,True):
            path=self.root/'reference.wav';wave(path,extensible=extensible)
            before=path.read_bytes();h=read_media_header(path)
            self.assertEqual(h.duration,Fraction(1));self.assertEqual(h.channels,2)
            self.assertEqual(h.wave_format_tag,65534 if extensible else 1)
            self.assertEqual(h.codec,'pcm_s24le' if extensible else 'pcm_s16le')
            self.assertEqual(path.read_bytes(),before)

    def test_float_disguised_as_extensible_pcm_truncation_and_ambiguous_chunks_fail(self):
        path=self.root/'bad.wav';wave(path,extensible=True,subtype=3)
        with self.assertRaises(ValueError):read_media_header(path)
        wave(path);raw=path.read_bytes();path.write_bytes(raw[:-1])
        with self.assertRaises(ValueError):read_media_header(path)
        raw+=b'data'+struct.pack('<I',0);raw=raw[:4]+struct.pack('<I',len(raw)-8)+raw[8:];path.write_bytes(raw)
        with self.assertRaises(ValueError):read_media_header(path)

    def test_aiff_sample_count_and_flac_streaminfo_are_exact_without_decoding(self):
        comm=struct.pack('>hIh',2,48000,24)+bytes.fromhex('400ebb80000000000000')
        data=b'COMM'+struct.pack('>I',18)+comm+b'SSND'+struct.pack('>I',288008)+bytes(288008)
        aiff=self.root/'reference.aiff';aiff.write_bytes(b'FORM'+struct.pack('>I',len(data)+4)+b'AIFF'+data)
        h=read_media_header(aiff);self.assertEqual((h.codec,h.duration,h.sample_rate),('pcm_s24be',Fraction(1),48000))
        info=struct.pack('>HH',4096,4096)+bytes(6)+((44100<<44)|(1<<41)|(15<<36)|44100).to_bytes(8,'big')+bytes(16)
        flac=self.root/'reference.flac';flac.write_bytes(b'fLaC\x80\0\0\x22'+info)
        h=read_media_header(flac);self.assertEqual((h.codec,h.duration,h.channels),('flac',Fraction(1),2))

    def test_m4a_with_no_audio_track_and_unknown_suffix_do_not_guess_a_codec(self):
        for name in ('empty.m4a','empty.bin'):
            path=self.root/name;path.write_bytes(bytes(32))
            with self.assertRaises(ValueError):
                read_media_header(path)
