"""Read container headers on one captured local file; never decode or retag it.

Uses the bundled Mutagen parsers for compressed headers. This is technical input
to registration, not native write authority or a completed analysis result.
"""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import struct


@dataclass(frozen=True)
class MediaHeader:
    family: str
    codec: str
    duration: Fraction
    sample_rate: int
    channels: int
    bits: int
    wave_format_tag: int | None = None


def _chunks(stream,size,byteorder):
    """Bounded RIFF/IFF directory, skipping all audio/tag payloads."""
    chunks: dict[bytes,tuple[int,int]]={};position=12
    for _ in range(256):
        if position==size:return chunks
        if position+8>size:raise ValueError('Truncated container chunk')
        stream.seek(position);raw=stream.read(8);kind=raw[:4]
        length=struct.unpack(byteorder+'I',raw[4:])[0]
        end=position+8+length
        if end>size:raise ValueError('Container chunk extends past EOF')
        if kind in chunks and kind in (b'fmt ',b'data',b'COMM',b'SSND'):
            raise ValueError('Ambiguous repeated audio container chunk')
        chunks[kind]=(position+8,length)
        position=end+(length&1)
        if end==size:return chunks
    raise ValueError('Container chunk directory exceeds the header budget')


def _pcm(stream,size,suffix):
    signature=stream.read(12)
    if suffix=='.wav':
        if signature[:4]!=b'RIFF' or signature[8:12]!=b'WAVE' or struct.unpack('<I',signature[4:8])[0]+8!=size:
            raise ValueError('Only complete RIFF WAVE framing is supported')
        chunks=_chunks(stream,size,'<');offset,length=chunks[b'fmt ']
        if not 16<=length<=40:raise ValueError('Unsupported WAVE format header')
        stream.seek(offset);fmt=stream.read(length)
        tag,channels,rate,byte_rate,align,bits=struct.unpack('<HHIIHH',fmt[:16])
        if tag==65534:
            if (length!=40 or struct.unpack_from('<HH',fmt,16)!=(22,bits)
                    or fmt[24:40]!=bytes.fromhex('0100000000001000800000aa00389b71')):
                raise ValueError('Only referenced extensible integer PCM is supported')
        elif tag!=1:raise ValueError('Only integer PCM WAVE is supported')
        if bits not in (16,24) or align!=channels*(bits//8) or byte_rate!=rate*align or not align:
            raise ValueError('Inconsistent or unreferenced PCM framing')
        count=chunks[b'data'][1]
        if count%align:raise ValueError('Incomplete PCM frame')
        return MediaHeader('wav','pcm_s16le' if bits==16 else 'pcm_s24le',
                           Fraction(count,byte_rate),rate,channels,bits,tag)
    from mutagen.aiff import read_float
    if signature[:4]!=b'FORM' or signature[8:12]!=b'AIFF' or struct.unpack('>I',signature[4:8])[0]+8!=size:
        raise ValueError('Only complete uncompressed AIFF framing is supported')
    chunks=_chunks(stream,size,'>');offset,length=chunks[b'COMM']
    if length!=18:raise ValueError('Unreferenced AIFF common header')
    stream.seek(offset);channels,frames,bits,encoded_rate=struct.unpack('>hIh10s',stream.read(18))
    value=read_float(encoded_rate);rate=int(value)
    if bits not in (16,24) or rate!=value or rate<=0:raise ValueError('Unreferenced AIFF PCM format')
    offset,length=chunks[b'SSND'];stream.seek(offset);skip,block=struct.unpack('>II',stream.read(8))
    if block!=0 or length<8+skip or length-8-skip!=frames*channels*(bits//8):
        raise ValueError('Incomplete or unreferenced AIFF samples')
    return MediaHeader('aiff','pcm_s16be' if bits==16 else 'pcm_s24be',Fraction(frames,rate),rate,channels,bits)


def read_media_header(path):
    path=Path(path);suffix=path.suffix.lower()
    try:
        with path.open('rb') as stream:
            size=path.stat().st_size
            if suffix in ('.wav','.aif','.aiff'):
                result=_pcm(stream,size,suffix)
            elif suffix=='.flac':
                from mutagen.flac import StreamInfo
                if stream.read(4)!=b'fLaC':raise ValueError('Missing native FLAC header')
                header=stream.read(4)
                if len(header)!=4 or header[0]&127 or int.from_bytes(header[1:],'big')!=34:
                    raise ValueError('Missing bounded FLAC stream info')
                raw=stream.read(34)
                if len(raw)!=34:raise ValueError('Truncated FLAC stream info')
                flac_info=StreamInfo(raw)
                if flac_info.bits_per_sample not in (16,24):raise ValueError('Unreferenced FLAC sample format')
                result=MediaHeader('flac','flac',Fraction(flac_info.total_samples,flac_info.sample_rate),
                                   flac_info.sample_rate,flac_info.channels,flac_info.bits_per_sample)
            elif suffix=='.mp3':
                from mutagen.mp3 import MPEGInfo
                mpeg_info=MPEGInfo(stream)
                if mpeg_info.layer!=3 or mpeg_info.sketchy:raise ValueError('Incomplete MPEG layer III header')
                result=MediaHeader('mp3','mp3',Fraction(str(mpeg_info.length)),mpeg_info.sample_rate,mpeg_info.channels,0)
            elif suffix=='.m4a':
                from mutagen.mp4 import MP4Info
                from mutagen.mp4._atom import Atoms
                atoms=Atoms(stream);tracks=list(atoms[b'moov'].findall(b'trak'))
                if len(tracks)!=1:raise ValueError('Exactly one M4A audio track is supported')
                handler=tracks[0][b'mdia',b'hdlr']
                if not 12<=handler.datalength<=4096:raise ValueError('Unbounded M4A handler')
                ok,data=handler.read(stream)
                if not ok or data[8:12]!=b'soun':raise ValueError('M4A contains a non-audio track')
                mdhd=tracks[0][b'mdia',b'mdhd']
                if not 24<=mdhd.datalength<=64:raise ValueError('Unbounded M4A media clock')
                ok,data=mdhd.read(stream)
                if not ok or data[0] not in (0,1):raise ValueError('Unsupported M4A media clock')
                scale,length=struct.unpack_from('>II' if data[0]==0 else '>IQ',data,12 if data[0]==0 else 20)
                mp4_info=MP4Info();mp4_info.load(atoms,stream)
                codec={'mp4a.40.2':'aac','alac':'alac'}.get(mp4_info.codec)
                if codec is None:raise ValueError('Unreferenced M4A codec')
                result=MediaHeader('m4a',codec,Fraction(length,scale),mp4_info.sample_rate,mp4_info.channels,mp4_info.bits_per_sample)
            else:raise ValueError('Unsupported media container')
        if not 0<result.duration<=86400 or not 8000<=result.sample_rate<=192000 or not 1<=result.channels<=8:
            raise ValueError('Unsupported duration, sample rate or channel count')
        return result
    except (KeyError,IndexError,struct.error,ZeroDivisionError,OverflowError) as exc:
        raise ValueError('Incomplete or inconsistent media header') from exc
