"""Pure saved-grid/key/waveform plans for bare PCM WAV registrations.

This is separate from the fixed generated-WAV initial-analysis proof. It does
not manufacture automatic-gain measurements, cues, source authority or SQL.
Existing native libraries contain the same analyzed packet profile without a
mixer row. Only the explicitly supplied components are proposed here.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import struct
from urllib.parse import quote

from .analysis import validate_result
from .local_grid import effective_result
from .native_media import MediaIdentity
from .rekordbox_grid import AnalysisImage, read_grid
from .rekordbox_write import ConflictError, WriteProofError
from .native_registration_plan import _native_id, _uuid
from datetime import datetime
from fractions import Fraction

FILE_ORDER=('.2EX','.DAT','.EXT')

def _stamp(value):
    try:
        parsed=datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
    except (TypeError,ValueError) as exc:
        raise WriteProofError('Explicit native analysis clock required') from exc
    if parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'!=value:
        raise WriteProofError('Noncanonical native analysis clock')

KINDS={
    '.DAT':[b'PPTH',b'PVBR',b'PQTZ',b'PWAV',b'PWV2',b'PCOB',b'PCOB'],
    '.EXT':[b'PPTH',b'PWV3',b'PCOB',b'PCOB',b'PCO2',b'PCO2',b'PQT2',b'PWV5',b'PWV4'],
    '.2EX':[b'PPTH',b'PWV7',b'PWV6',b'PWVC'],
}


def packet_shape(images, filename, duration):
    if set(images)!=set(FILE_ORDER) or any(type(v) is not bytes for v in images.values()):
        raise WriteProofError('Exact DAT/EXT/2EX component packet required')
    columns=None
    fixed={b'PVBR':(16,1620,'00000000'),b'PWAV':(20,420,'0000019000010000'),
           b'PWV2':(20,120,'0000006400010000'),b'PWV4':(24,7224,'00000006000004b000000000'),
           b'PWV6':(20,3620,'00000003000004b0'),b'PWVC':(14,20,'0000')}
    for ext,raw in images.items():
        image=AnalysisImage.parse(raw)
        if (len(image.header)!=28 or image.header[12:]!=bytes.fromhex('00000001000100000001000000000000')
                or [s.kind for s in image.sections]!=KINDS[ext]):
            raise WriteProofError('Unverified component packet profile')
        for section in image.sections:
            kind=section.kind;body=section.raw
            if kind in fixed:
                header,size,extra=fixed[kind]
                if (section.header_size,len(body),body[12:header])!=(header,size,bytes.fromhex(extra)):
                    raise WriteProofError('Unverified component waveform header')
            if kind in (b'PWV3',b'PWV5',b'PWV7'):
                width={b'PWV3':1,b'PWV5':2,b'PWV7':3}[kind]
                if section.header_size!=24:raise WriteProofError('Unverified detail header')
                unit,count,flags=struct.unpack_from('>III',body,12)
                if (unit!=width or flags!=(0x960305 if kind==b'PWV5' else 0x960000)
                        or len(body)!=24+width*count or abs(count-duration*150)>1.01
                        or columns is not None and columns!=count):
                    raise WriteProofError('Waveform dimensions disagree with the exact captured endpoint')
                columns=count
            if kind==b'PVBR' and any(body[16:]):raise WriteProofError('PCM WAV cannot acquire a VBR table')
            if kind==b'PWVC' and body[14:]!=bytes.fromhex('00bb012c01f4'):
                raise WriteProofError('Unknown opaque waveform configuration')
            if kind==b'PQT2' and (section.header_size!=56 or len(body)!=56
                    or body[12:24]!=bytes.fromhex('000000000100000200000000') or any(body[24:])):
                raise WriteProofError('Only exact secondary-grid invalidation is proposed')
            if kind==b'PPTH' and (section.header_size!=16 or struct.unpack_from('>I',body,12)[0]!=len(body)-16
                    or body[16:]!=('?/'+filename+'\0').encode('utf-16-be')):
                raise WriteProofError('Analysis path differs from the captured source filename')
        for kind in (b'PCOB',b'PCO2'):
            sections=[s for s in image.sections if s.kind==kind]
            expected=[struct.pack('>III',i,0,0xffffffff) if kind==b'PCOB' else struct.pack('>II',i,0) for i in (1,0)]
            if sections and [s.raw[12:] for s in sections]!=expected:
                raise WriteProofError('Component publication cannot invent embedded cues')


def plan(base, audio: MediaIdentity, images, *, share_root, seeds, result, technical, native_length=False):
    audio.validate();validate_result(result);result=effective_result(result)
    duration=audio.duration_numerator/audio.duration_denominator
    if (audio.family!='wav' or audio.codec not in ('pcm_s16le','pcm_s24le') or audio.wave_format_tag!=1
            or result['sha256']!=audio.sha256 or result['path']!=audio.path or result['size']!=audio.epoch[2]
            or abs(result['duration']-duration)>0.000001 or not result['beats'] or result['bpm'] is None or result['key'] is None
            or result.get('first_cue') is not None or result.get('cue_draft')):
        raise WriteProofError('Only complete saved grid/key components for the exact PCM source are proposed')
    share=Path(share_root)
    if not share.is_absolute() or '..' in share.parts:raise WriteProofError('Explicit native share required')
    track=base['track'];counter=base['counter'];tid=track['ID'];uuid=track['UUID'];_native_id(tid);_uuid(uuid)
    if (track['FolderPath']!=audio.path or track['FileSize']!=audio.epoch[2] or track['rb_file_id']!=str(audio.epoch[1])
            or track['FileType']!=11 or track['BPM']!=0 or track['KeyID']!='0' or track['Analysed']!=0
            or track['AnalysisDataPath']!='' or track['ContentLink']!=14 or track['Length']!=audio.seconds
            or any(track[k] is not None for k in ('AnalysisUpdated','TrackInfoUpdated','CueUpdated','DisableQuantize'))
            or track['SamplerTrackInfo']!=0 or any(track[k]!=0 for k in ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced'))
            or track['usn'] is not None or base['cues'] or base['aggregates']
            or any(r['ContentID']==tid for r in (*base['files'],*base['mixers']))):
        raise ConflictError('Component publication requires an unchanged bare registration without musical data')
    if (set(technical)!={'sample_rate','channels','bits'} or technical['channels']!=2
            or technical['sample_rate'] not in (44100,48000) or technical['bits']!={'pcm_s16le':16,'pcm_s24le':24}[audio.codec]):
        raise WriteProofError('Unverified PCM component framing')
    keys=[r for r in base['keys'] if r['ScaleName']==result['key'] and r['rb_local_deleted']==0]
    if len(keys)!=1:raise WriteProofError('Saved key must resolve to one existing native key')
    _native_id(keys[0]['ID']);packet_shape(images,Path(audio.path).name,duration)
    beats=read_grid(images['.DAT'])
    expected=[(r[0],round(r[2]*100),r[1]) for r in result['beats']]
    # The saved detector grid may change tempo while its headline BPM stays
    # fixed. Bind every native row to that exact grid, including its endpoint;
    # requiring every row to equal the headline would discard valid changes.
    if [(b.number,b.tempo,b.time_ms) for b in beats]!=expected:
        raise WriteProofError('Packet grid must equal the complete saved grid, including its exact endpoint')
    if any(b.number!=beats[i-1].number%4+1 for i,b in enumerate(beats) if i):
        raise WriteProofError('Invalid saved bar numbering')
    clock=counter['int_1']
    if counter['registry_id']!='localUpdateCount' or type(clock) is not int or not 0<=clock<2**63-20:
        raise WriteProofError('Invalid native counter')
    if set(seeds)!={'track_stamp','file_uuids','file_stamps'} or set(seeds['file_uuids'])!=set(FILE_ORDER) or set(seeds['file_stamps'])!=set(FILE_ORDER):
        raise WriteProofError('Explicit component file identities and clocks required')
    clocks=[seeds['track_stamp'],*(seeds['file_stamps'][ext] for ext in FILE_ORDER)]
    for stamp in clocks:_stamp(stamp)
    if clocks!=sorted(clocks) or clocks[0]<track['updated_at']:raise WriteProofError('Component clocks precede their source')
    identities=list(seeds['file_uuids'].values())
    for identity in identities:_uuid(identity)
    if len(set(identities))!=3 or uuid in identities or any(r['UUID'] in identities for r in base['files']):
        raise ConflictError('Component identity collides with history')
    prefix='/PIONEER/USBANLZ/'+uuid[:3]+'/'+uuid[3:]+'/ANLZ0000'
    updated={**track,'BPM':round(result['bpm']*100),'KeyID':keys[0]['ID'],'AnalysisDataPath':prefix+'.DAT',
        'BitDepth':technical['bits'],'SampleRate':technical['sample_rate'],
        'BitRate':technical['bits']*technical['channels']*technical['sample_rate']//1000,
        'ContentLink':787982,'Analysed':105,'AnalysisUpdated':'1','TrackInfoUpdated':'2',
        'rb_local_usn':clock+17,'updated_at':seeds['track_stamp']}
    if native_length:
        frames=Fraction(audio.duration_numerator,audio.duration_denominator)*technical['sample_rate']
        if frames.denominator!=1 or frames.numerator<1:raise WriteProofError('Unverified PCM endpoint')
        updated['Length']=(frames.numerator-1)//technical['sample_rate']
    status=dict(rb_data_status=0,rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None)
    files=[]
    for offset,ext in enumerate(FILE_ORDER,18):
        path=prefix+ext;raw=images[ext];stamp=seeds['file_stamps'][ext]
        files.append(dict(ID=uuid+'_'+quote(path,safe=''),ContentID=tid,Path=path,Hash=hashlib.md5(raw).hexdigest(),Size=len(raw),
            rb_local_path=str(share/path.lstrip('/')),rb_insync_hash=None,rb_insync_local_usn=None,rb_file_hash_dirty=0,
            rb_local_file_status=0,rb_in_progress=0,rb_process_type=0,rb_temp_path=None,rb_priority=50,rb_file_size_dirty=0,
            UUID=seeds['file_uuids'][ext],**status,rb_local_usn=clock+offset,created_at=stamp,updated_at=stamp))
    if any(row['ID'] in {r['ID'] for r in base['files']} for row in files):raise ConflictError('Component file history collides')
    return dict(changes=[dict(table='djmdContent',id=tid,before=deepcopy(track),after=updated),
        *(dict(table='contentFile',id=r['ID'],before=None,after=r) for r in files),
        dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after={**counter,'int_1':clock+20})],
        source_sha256=audio.sha256,source_write_authority=False,
        scope='Saved BPM/grid/key and newly rendered waveforms only; no gain/cue/audio/playlist/XML changes')
