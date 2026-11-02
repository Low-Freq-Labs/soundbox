"""Common original CDJ-2000/CDJ-3000 format checks and copy-only rendering.

This module never replaces source files or updates native records. Existing-song
conversion needs a coordinated media/technical-metadata publisher and native
acceptance; bare registration and initial analysis do not authorize those edits.
"""
from dataclasses import asdict
from pathlib import Path
import os
import hashlib
import struct
import subprocess
import tempfile
import time

from .media_probe import read_media_header
from .wave_conversion import layout,render as render_float

PROFILE='cdj-2000-and-3000-v1'


def inspect(path):
    """Inspect real container/codec headers, never filename or cached RB fields."""
    path=Path(path)
    from mutagen import MutagenError
    try:
        header=read_media_header(path)
    except (ValueError,MutagenError) as exc:
        info=layout(path) if path.suffix.lower()=='.wav' else None
        if info is None:
            return dict(profile=PROFILE,status='unchecked',reason=str(exc))
        if info['channels'] not in (1,2):return dict(profile=PROFILE,status='unsupported',reason='Automatic downmixing needs a channel-layout choice')
        return dict(profile=PROFILE,status='convert',reason='32-bit floating-point WAVE',
                    codec='float32',sample_rate=info['rate'],channels=info['channels'],bits=32,
                    duration=info['frames']/info['rate'])
    facts=asdict(header);facts['duration']=float(header.duration)
    reason=''
    if header.channels not in (1,2):
        return dict(profile=PROFILE,status='unsupported',reason='Automatic downmixing needs a channel-layout choice',**facts)
    if header.codec in ('mp3','aac'):
        # MPEG-1 Layer III and MPEG-4 AAC LC overlap both published profiles.
        # Other clocks/versions are conversion candidates, not false passes.
        if header.codec=='mp3':
            from mutagen.mp3 import MPEGInfo
            with path.open('rb') as stream:info_mpeg=MPEGInfo(stream)
            valid=info_mpeg.version==1 and 32000<=info_mpeg.bitrate<=320000
        else:
            from mutagen.mp4 import MP4Info
            from mutagen.mp4._atom import Atoms
            with path.open('rb') as stream:
                info_mp4=MP4Info();info_mp4.load(Atoms(stream),stream)
            valid=16000<=info_mp4.bitrate<=320000
        if not valid or header.sample_rate not in (44100,48000):
            reason='Compressed audio parameters outside the common player profile'
    elif header.family not in ('wav','aiff'):
        reason='This lossless container is unsupported on the original CDJ-2000'
    elif header.sample_rate not in (44100,48000):
        reason='PCM sample rate exceeds the common 44.1/48 kHz profile'
    elif header.wave_format_tag==65534:
        reason='Canonical PCM WAVE header needed for the conservative common profile'
    return dict(profile=PROFILE,status='convert' if reason else 'compatible',reason=reason,**facts)


def _canonical_pcm(source,target,facts):
    """Normalize only fmt; retain all ordered metadata and exact PCM payload."""
    size=source.stat().st_size;chunks=[]
    with source.open('rb') as stream:
        position=12
        while position<size:
            stream.seek(position);raw=stream.read(8);kind=raw[:4];length=struct.unpack('<I',raw[4:])[0]
            chunks.append((kind,position+8,length));position+=8+length+(length&1)
        total=12+sum(8+(16 if k==b'fmt ' else n)+(0 if k==b'fmt ' else n&1) for k,_,n in chunks)
        digest=hashlib.sha256()
        with target.open('xb') as output:
            output.write(b'RIFF'+struct.pack('<I',total-8)+b'WAVE')
            for kind,offset,length in chunks:
                if kind==b'fmt ':
                    channels,rate,bits=facts['channels'],facts['sample_rate'],facts['bits'];align=channels*bits//8
                    fmt=struct.pack('<HHIIHH',1,channels,rate,rate*align,align,bits)
                    output.write(b'fmt '+struct.pack('<I',16)+fmt)
                else:
                    stream.seek(offset-8);remaining=8+length+(length&1)
                    while remaining:
                        block=stream.read(min(remaining,1024*1024))
                        if not block:raise ValueError('PCM source changed during header normalization')
                        output.write(block);remaining-=len(block)
                    if kind==b'data':
                        stream.seek(offset);remaining=length
                        while remaining:
                            block=stream.read(min(remaining,1024*1024))
                            if not block:raise ValueError('PCM source changed during payload verification')
                            digest.update(block);remaining-=len(block)
            output.flush();os.fsync(output.fileno())
    from .media_probe import _chunks
    with target.open('rb') as stream:
        offset,remaining=_chunks(stream,target.stat().st_size,'<')[b'data'];stream.seek(offset)
        actual=hashlib.sha256()
        while remaining:
            block=stream.read(min(remaining,1024*1024))
            if not block:raise ValueError('Converted PCM payload is truncated')
            actual.update(block);remaining-=len(block)
    if actual.digest()!=digest.digest():raise ValueError('Header normalization changed PCM samples')
    return dict(frames=round(facts['duration']*facts['sample_rate']),sample_rate=facts['sample_rate'],
                channels=facts['channels'],gain=1,clipped_samples=0,max_error=0,audio_payload_sha256=digest.hexdigest())


def render(source,target,*,should_cancel=lambda:False):
    """Prepare a verified PCM24 WAVE copy; source remains byte-identical.

    Keep supported clocks; halve 88.2/96 kHz to 44.1/48 kHz. CoreAudio's best
    sample-rate converter preserves duration. No normalization, tempo/key
    detection or native analysis/cue edits. Independent packed-sample readback
    uses the same gain/clipping policy Sonny auditioned for the float importer.
    """
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve() or target.exists() or target.suffix.lower()!='.wav':
        raise ValueError('Conversion requires a vacant, separate WAVE destination')
    facts=inspect(source)
    if facts['status'] not in ('compatible','convert'):
        raise ValueError(facts['reason'])
    rate=facts['sample_rate'];rate=rate if rate in (44100,48000) else 48000 if rate%48000==0 else 44100
    if facts['channels'] not in (1,2):raise ValueError('Automatic channel downmix is not supported')
    if should_cancel():raise RuntimeError('Conversion cancelled')
    bits=24
    if facts.get('wave_format_tag')==65534 and rate==facts['sample_rate']:
        result=_canonical_pcm(source,target,facts);bits=facts['bits']
    elif facts.get('codec')=='float32' and rate==facts['sample_rate']:
        result=render_float(source,target,clip_overs=True)
    else:
        with tempfile.TemporaryDirectory(prefix='soundbox-cdj-convert-',dir=target.parent) as area:
            decoded=Path(area)/'decoded.wav'
            command=['/usr/bin/afconvert','-f','WAVE','-d',f'LEF32@{rate}',
                     '-r','127','--src-complexity','bats',str(source),str(decoded)]
            with tempfile.TemporaryFile() as output:
                process=subprocess.Popen(command,stdout=output,stderr=output)
                try:
                    deadline=time.monotonic()+300
                    while process.poll() is None:
                        if should_cancel():raise RuntimeError('Conversion cancelled')
                        if time.monotonic()>deadline:raise RuntimeError('Audio conversion exceeded its time budget')
                        time.sleep(.05)
                    if process.returncode:
                        output.seek(0);raise ValueError('CoreAudio conversion failed: '+output.read(4096).decode(errors='replace'))
                finally:
                    if process.poll() is None:process.terminate();process.wait()
            result=render_float(decoded,target,clip_overs=True)
    after=read_media_header(target)
    if (after.sample_rate!=rate or after.channels!=facts['channels'] or after.bits!=bits
            or abs(float(after.duration)-facts['duration'])>1/rate):
        raise ValueError('Converted audio changed duration or channel framing')
    if should_cancel():raise RuntimeError('Conversion cancelled')
    with target.open('rb') as stream:os.fsync(stream.fileno())
    return dict(profile=PROFILE,before=facts,after={**asdict(after),'duration':float(after.duration)},measurements=result)
