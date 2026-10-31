"""Pure initial-analysis row protocol for the one observed generated-WAV case.

No source API, arbitrary codecs, key interning or general gain/endpoint policy.
Opaque mixer measurements are reused only for their exact proven audio digest.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import struct
from pathlib import Path
from urllib.parse import quote

from soundbox.rekordbox_grid import AnalysisImage, read_grid
from soundbox.rekordbox_write import ConflictError, WriteProofError
from .registration import WaveIdentity, _native_id, _uuid
from .identity import _canonical

REFERENCE_SHA = 'f3c0536e1637361847a5982aaf54dd4a0430d183a7be9b562ae999cf3bcbd711'
# Native measurement cache for exactly REFERENCE_SHA; not a DSP formula.
REFERENCE_GAIN = 2.5765464305877686
REFERENCE_PEAK = 0.5338134765625
FILE_ORDER = ('.2EX', '.DAT', '.EXT')


def _stamp(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
    except (TypeError, ValueError) as exc:
        raise WriteProofError('Explicit native analysis clock required') from exc
    if parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00' != value:
        raise WriteProofError('Noncanonical analysis clock')


def _packet_shape(images, filename):
    fixed = {
        b'PVBR':(16,1620,'00000000'),
        b'PWAV':(20,420,'0000019000010000'), b'PWV2':(20,120,'0000006400010000'),
        b'PWV3':(24,4824,'00000001000012c000960000'),
        b'PWV5':(24,9624,'00000002000012c000960305'),
        b'PWV4':(24,7224,'00000006000004b000000000'),
        b'PWV7':(24,14424,'00000003000012c000960000'),
        b'PWV6':(20,3620,'00000003000004b0'), b'PWVC':(14,20,'0000'),
    }
    for raw in images.values():
        image = AnalysisImage.parse(raw)
        if len(image.header) != 28 or image.header[12:] != bytes.fromhex('00000001000100000001000000000000'):
            raise WriteProofError('Unverified initial PMAI header')
        for section in image.sections:
            kind = section.kind
            if kind in fixed:
                header, size, extra = fixed[kind]
                if (section.header_size,len(section.raw),section.raw[12:header]) != (header,size,bytes.fromhex(extra)):
                    raise WriteProofError('Unverified waveform/header dimensions')
            if kind == b'PVBR' and any(section.raw[16:]):
                raise WriteProofError('This PCM WAV has no proven nonzero VBR table')
            if kind == b'PWVC' and section.raw[14:] != bytes.fromhex('00bb012c01f4'):
                raise WriteProofError('Unknown opaque waveform configuration')
            if kind in (b'PCOB',b'PCO2'):
                allowed = ({bytes.fromhex('0000000100000000ffffffff'),bytes.fromhex('0000000000000000ffffffff')}
                           if kind==b'PCOB' else {bytes.fromhex('0000000100000000'),bytes(8)})
                if len(section.raw) != section.header_size or section.raw[12:] not in allowed:
                    raise WriteProofError('Initial packet cannot invent embedded cues')
            if kind == b'PQT2':
                if (section.header_size != 56 or len(section.raw) not in (56,184)
                        or section.raw[12:24] != bytes.fromhex('000000000100000200000000')
                        or len(section.raw) != 56+2*struct.unpack_from('>I',section.raw,40)[0]
                        or (len(section.raw)==56 and any(section.raw[24:]))):
                    raise WriteProofError('Unverified initial secondary-grid shape')
        for kind in (b'PCOB',b'PCO2'):
            members = [s for s in image.sections if s.kind == kind]
            if members and [s.raw[12:16] for s in members] != [bytes.fromhex('00000001'),bytes(4)]:
                raise WriteProofError('Initial cue-list identities/order changed')
        ppth = image.section(b'PPTH')
        if (ppth.header_size != 16 or struct.unpack_from('>I',ppth.raw,12)[0] != len(ppth.raw)-16
                or ppth.raw[16:] != ('?/'+filename+'\0').encode('utf-16-be')):
            raise WriteProofError('Initial PPTH must name the exact canonical source filename')


def plan(base, audio: WaveIdentity, images, *, share_root, seeds):
    """Construct from verified BEFORE rows, packet bytes and identity/clock seeds.

    base holds track/counter plus complete key/file/mixer row lists and target
    cue/aggregate lists. Source publication/recovery are deliberately separate.
    """
    audio.validate(); _canonical(share_root)
    if audio.sha256 != REFERENCE_SHA or audio.seconds != 32:
        raise WriteProofError('Only the exact generated32-second source has this initial-analysis proof')
    if set(images) != set(FILE_ORDER) or any(type(v) is not bytes for v in images.values()):
        raise WriteProofError('An exact DAT/EXT/2EX packet is required')
    track = base['track']; counter = base['counter']; tid = track['ID']; uuid = track['UUID']
    _native_id(tid); _uuid(uuid)
    if (track['FolderPath'] != audio.path or track['FileSize'] != audio.epoch[2]
            or track['rb_file_id'] != str(audio.epoch[1]) or track['FileType'] != 11
            or track['BPM'] != 0 or track['KeyID'] != '0' or track['Analysed'] != 0
            or track['AnalysisDataPath'] != '' or track['ContentLink'] != 14 or track['Length'] != 32
            or track['AnalysisUpdated'] is not None or track['TrackInfoUpdated'] is not None
            or track['CueUpdated'] is not None or track['SamplerTrackInfo'] != 0
            or track['DisableQuantize'] is not None
            or any(track[k] != 0 for k in ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced'))
            or track['usn'] is not None or base['cues'] or base['aggregates']):
        raise ConflictError('Initial analysis requires the exact verified bare fixture state')
    if any(row['ContentID'] == tid for row in (*base['files'], *base['mixers'])):
        raise ConflictError('An initial file/mixer registration already exists')
    keys = [r for r in base['keys'] if r['ScaleName'] == '8B' and r['rb_local_deleted'] == 0]
    if len(keys) != 1:
        raise WriteProofError('The existing native 8B key must resolve unambiguously; no interning is proven')
    _native_id(keys[0]['ID'])
    if counter['registry_id'] != 'localUpdateCount' or type(counter['int_1']) is not int or not 0 <= counter['int_1'] < 2**63-20:
        raise WriteProofError('Invalid initial-analysis counter')
    if set(seeds) != {'mixer_id','mixer_uuid','mixer_stamp','track_stamp','file_uuids','file_stamps'}:
        raise WriteProofError('Unknown analysis identity/clock seeds')
    if set(seeds['file_uuids']) != set(FILE_ORDER) or set(seeds['file_stamps']) != set(FILE_ORDER):
        raise WriteProofError('All three native file identity/clock seeds are required')
    clocks = [seeds['mixer_stamp'], seeds['track_stamp'], *(seeds['file_stamps'][ext] for ext in FILE_ORDER)]
    for stamp in clocks: _stamp(stamp)
    if clocks != sorted(clocks) or seeds['track_stamp'] < track['updated_at']:
        raise WriteProofError('Initial-analysis clocks are not ordered after the verified base')
    identities = [seeds['mixer_id'], seeds['mixer_uuid'], *seeds['file_uuids'].values()]
    for identity in identities: _uuid(identity)
    if len(set(identities)) != len(identities) or uuid in identities:
        raise WriteProofError('Analysis UUID seeds must be distinct')
    if any(r['ID'] in identities or r['UUID'] in identities for r in (*base['mixers'], *base['files'])):
        raise ConflictError('Analysis identity seed collides with existing history')
    expected_kinds = {
        '.DAT':[b'PPTH',b'PVBR',b'PQTZ',b'PWAV',b'PWV2',b'PCOB',b'PCOB'],
        '.EXT':[b'PPTH',b'PWV3',b'PCOB',b'PCOB',b'PCO2',b'PCO2',b'PQT2',b'PWV5',b'PWV4'],
        '.2EX':[b'PPTH',b'PWV7',b'PWV6',b'PWVC'],
    }
    for ext, raw in images.items():
        image = AnalysisImage.parse(raw)
        if [s.kind for s in image.sections] != expected_kinds[ext]:
            raise WriteProofError('Unverified initial-analysis section layout')
    _packet_shape(images,Path(audio.path).name)
    beats = read_grid(images['.DAT'])
    if (len(beats) != 64 or any(b.tempo != 12000 for b in beats)
            or [b.time_ms for b in beats] != [249+500*i for i in range(64)]
            or [b.number for b in beats] != [i%4+1 for i in range(64)]):
        raise WriteProofError('This initial proof binds the exact captured120 BPM/249ms grid')
    clock = counter['int_1']
    status = dict(rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0, usn=None)
    gain = struct.unpack('>HH', struct.pack('>f', REFERENCE_GAIN))
    peak = struct.unpack('>HH', struct.pack('>f', REFERENCE_PEAK))
    mixer = dict(ID=seeds['mixer_id'],ContentID=tid,GainHigh=gain[0],GainLow=gain[1],PeakHigh=peak[0],PeakLow=peak[1],
        UUID=seeds['mixer_uuid'],**status,rb_local_usn=clock+13,created_at=seeds['mixer_stamp'],updated_at=seeds['mixer_stamp'])
    prefix = '/PIONEER/USBANLZ/'+uuid[:3]+'/'+uuid[3:]+'/ANLZ0000'
    updated = {**track, 'BPM':12000,'Length':31,'BitRate':1411,'BitDepth':16,'SampleRate':44100,
               'KeyID':keys[0]['ID'],'AnalysisDataPath':prefix+'.DAT','ContentLink':787982,'Analysed':105,
               'AnalysisUpdated':'1','TrackInfoUpdated':'2','rb_local_usn':clock+17,'updated_at':seeds['track_stamp']}
    files = []
    for offset, ext in enumerate(FILE_ORDER,18):
        path = prefix+ext; raw = images[ext]; stamp = seeds['file_stamps'][ext]
        files.append(dict(ID=uuid+'_'+quote(path,safe=''),ContentID=tid,Path=path,Hash=hashlib.md5(raw).hexdigest(),Size=len(raw),
            rb_local_path=str(Path(share_root)/path.lstrip('/')),rb_insync_hash=None,rb_insync_local_usn=None,
            rb_file_hash_dirty=0,rb_local_file_status=0,rb_in_progress=0,rb_process_type=0,rb_temp_path=None,
            rb_priority=50,rb_file_size_dirty=0,UUID=seeds['file_uuids'][ext],**status,rb_local_usn=clock+offset,
            created_at=stamp,updated_at=stamp))
    occupied = {r['ID'] for r in base['files']}
    if any(r['ID'] in occupied for r in files):
        raise ConflictError('Initial analysis file history collides')
    changes = [dict(table='djmdMixerParam',id=mixer['ID'],before=None,after=mixer),
               dict(table='djmdContent',id=tid,before=deepcopy(track),after=updated),
               *(dict(table='contentFile',id=r['ID'],before=None,after=r) for r in files),
               dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after={**counter,'int_1':clock+20})]
    return dict(changes=changes, source_sha256=audio.sha256, source_write_authority=False,
                scope='One exact generated-WAV initial full analysis; no general gain/endpoint/codec/partial-analysis policy')
