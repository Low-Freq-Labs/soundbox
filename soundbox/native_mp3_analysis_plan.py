"""Pure CBR initial-component proposal; no SQL, publisher or source authority.

The isolated native reference proves the metadata sequence with a gain row and
secondary grid. This deliberately proposes neither gain nor a secondary grid;
the source store independently binds accepted component policy. This pure
planner preserves its original proposal wire format and cannot grant authority.
"""
from copy import deepcopy
from datetime import datetime
from fractions import Fraction
import hashlib
from pathlib import Path
import struct
from urllib.parse import quote

from soundbox.analysis import validate_result
from soundbox.local_grid import effective_result
from soundbox.native_media import MediaIdentity
from soundbox.native_registration_plan import _native_id, _uuid
from soundbox.rekordbox_grid import AnalysisImage, read_grid
from soundbox.rekordbox_write import ConflictError, WriteProofError
from .mp3_scan import _validate_scan, cbr_pvbr

PROFILE = 'research-initial-mpeg1-cbr-no-info-stereo-44100-v1'
FILE_ORDER = ('.2EX', '.DAT', '.EXT')
KINDS = {
    '.DAT': [b'PPTH', b'PVBR', b'PQTZ', b'PWAV', b'PWV2', b'PCOB', b'PCOB'],
    '.EXT': [b'PPTH', b'PWV3', b'PCOB', b'PCOB', b'PCO2', b'PCO2', b'PQT2', b'PWV5', b'PWV4'],
    '.2EX': [b'PPTH', b'PWV7', b'PWV6', b'PWVC'],
}


def _clock(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
    except (TypeError, ValueError) as exc:
        raise WriteProofError('Explicit component clock required') from exc
    if parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00' != value:
        raise WriteProofError('Noncanonical component clock')


def _packets(images, scan, result):
    if (not isinstance(images, dict) or set(images) != set(FILE_ORDER)
            or any(type(raw) is not bytes or not 0 < len(raw) <= 64 * 1024 * 1024
                   for raw in images.values())):
        raise WriteProofError('Exact bounded MP3 DAT/EXT/2EX bytes required')
    fixed = {b'PWAV': (20, 420, '0000019000010000'), b'PWV2': (20, 120, '0000006400010000'),
             b'PWV4': (24, 7224, '00000006000004b000000000'),
             b'PWV6': (20, 3620, '00000003000004b0'), b'PWVC': (14, 20, '0000')}
    path = ('?/' + Path(scan.path).name + '\0').encode('utf-16-be')
    columns = (scan.sample_count + 293) // 294
    for ext, raw in images.items():
        image = AnalysisImage.parse(raw)
        if (len(image.header) != 28 or image.header[12:] != bytes.fromhex('00000001000100000001000000000000')
                or [s.kind for s in image.sections] != KINDS[ext]):
            raise WriteProofError('Unverified MP3 component packet profile')
        for section in image.sections:
            kind, body = section.kind, section.raw
            if kind in fixed:
                header, size, extra = fixed[kind]
                if (section.header_size, len(body), body[12:header]) != (header, size, bytes.fromhex(extra)):
                    raise WriteProofError('Unverified MP3 waveform framing')
            if kind in (b'PWV3', b'PWV5', b'PWV7'):
                width = {b'PWV3': 1, b'PWV5': 2, b'PWV7': 3}[kind]
                if (section.header_size != 24 or len(body) != 24 + width * columns
                        or struct.unpack_from('>III', body, 12) !=
                        (width, columns, 0x960305 if kind == b'PWV5' else 0x960000)):
                    raise WriteProofError('MP3 detail differs from the scanned sample endpoint')
            if kind == b'PVBR' and body != cbr_pvbr(scan):
                raise WriteProofError('MP3 seek section differs from independently scanned CBR frames')
            if kind == b'PWVC' and body[14:] != bytes.fromhex('00bb012c01f4'):
                raise WriteProofError('Unknown opaque MP3 waveform configuration')
            if kind == b'PQTZ' and body[12:20] != bytes.fromhex('0000000000080000'):
                raise WriteProofError('Unverified MP3 primary grid header')
            if kind == b'PQT2' and (section.header_size != 56 or len(body) != 56
                    or body[12:24] != bytes.fromhex('000000000100000200000000') or any(body[24:])):
                raise WriteProofError('MP3 proposal requires explicit empty secondary grid')
            if kind == b'PPTH' and (section.header_size != 16 or body[12:16] != struct.pack('>I', len(path))
                    or body[16:] != path):
                raise WriteProofError('MP3 packet filename differs from captured source')
        for kind in (b'PCOB', b'PCO2'):
            sections = [s for s in image.sections if s.kind == kind]
            expected = [struct.pack('>III', i, 0, 0xffffffff) if kind == b'PCOB' else struct.pack('>II', i, 0)
                        for i in (1, 0)]
            if sections and (any(s.header_size != (24 if kind == b'PCOB' else 20) for s in sections)
                             or [s.raw[12:] for s in sections] != expected):
                raise WriteProofError('MP3 component proposal cannot invent embedded cues')
    beats = read_grid(images['.DAT'])
    if [(b.number, b.time_ms, b.tempo) for b in beats] != [(b[0], b[1], round(b[2] * 100)) for b in result['beats']]:
        raise WriteProofError('MP3 packet grid must equal every exact saved grid row')


def plan(base, audio: MediaIdentity, images, *, scan, share_root, seeds, result, technical):
    """Return a proposal only from independent captured frame and saved-grid facts."""
    audio.validate(); _validate_scan(scan); validate_result(result)
    result = effective_result(result)
    duration = Fraction(scan.sample_count, scan.sample_rate)
    expected_technical = dict(sample_rate=44100, channels=2, bits=16, bitrate=scan.bitrates[0])
    if (audio.family != 'mp3' or audio.codec != 'mp3' or Path(audio.path).is_relative_to('/Volumes')
            or scan.info_kind is not None or not scan.cbr
            or scan.sample_rate != 44100 or scan.channels != 2
            or (scan.path, scan.source_epoch, scan.sha256) != (audio.path, audio.epoch, audio.sha256)
            or Fraction(audio.duration_numerator, audio.duration_denominator) != duration
            or result['path'] != audio.path or result['sha256'] != audio.sha256
            or result['epoch'] != list(audio.epoch) or result['size'] != audio.epoch[2]
            or result['duration'] != float(duration)
            or not 4 <= duration <= 1800 or not result['beats'] or result['bpm'] is None or result['key'] is None
            or result.get('first_cue') is not None or result.get('cue_draft')
            or not isinstance(technical, dict) or technical != expected_technical
            or any(type(n) is not int for n in technical.values())):
        raise WriteProofError('Only exact scanned no-Info/Xing stereo 44.1kHz CBR components are proposed')
    share = Path(share_root)
    if not share.is_absolute() or '..' in share.parts or '\0' in str(share) or share.is_relative_to('/Volumes'):
        raise WriteProofError('Explicit desktop native share required')
    track, counter = base['track'], base['counter']
    tid, uuid = track['ID'], track['UUID']; _native_id(tid); _uuid(uuid)
    if (track['FolderPath'] != audio.path or track['FileSize'] != audio.epoch[2]
            or track['rb_file_id'] != str(audio.epoch[1]) or track['FileType'] != 1
            or track['Length'] != audio.seconds or track['BPM'] != 0 or track['KeyID'] != '0'
            or track['Analysed'] != 0 or track['AnalysisDataPath'] != '' or track['ContentLink'] != 14
            or any(track[k] is not None for k in ('AnalysisUpdated', 'TrackInfoUpdated', 'CueUpdated', 'DisableQuantize'))
            or any(track[k] != 0 for k in ('BitRate', 'BitDepth', 'SampleRate', 'SamplerTrackInfo',
                                         'rb_data_status', 'rb_local_data_status', 'rb_local_deleted', 'rb_local_synced'))
            or track['usn'] is not None or base['cues'] or base['aggregates']
            or any(row['ContentID'] == tid for row in (*base['files'], *base['mixers']))):
        raise ConflictError('CBR components require an unchanged bare unlocked native registration')
    keys = [row for row in base['keys'] if row['ScaleName'] == result['key'] and row['rb_local_deleted'] == 0]
    if len(keys) != 1:
        raise WriteProofError('Saved MP3 key must resolve to one existing native key')
    _native_id(keys[0]['ID']); _packets(images, scan, result)
    clock = counter['int_1']
    if counter['registry_id'] != 'localUpdateCount' or type(clock) is not int or not 0 <= clock < 2**63 - 20:
        raise WriteProofError('Invalid MP3 native counter')
    if (set(seeds) != {'track_stamp', 'file_uuids', 'file_stamps'}
            or set(seeds['file_uuids']) != set(FILE_ORDER) or set(seeds['file_stamps']) != set(FILE_ORDER)):
        raise WriteProofError('Explicit MP3 component identities and clocks required')
    clocks = [seeds['track_stamp'], *(seeds['file_stamps'][ext] for ext in FILE_ORDER)]
    for stamp in clocks:
        _clock(stamp)
    if clocks != sorted(clocks) or clocks[0] < track['updated_at']:
        raise WriteProofError('MP3 component clocks precede their source')
    identities = list(seeds['file_uuids'].values())
    for identity in identities:
        _uuid(identity)
    if len(set(identities)) != 3 or uuid in identities or any(row['UUID'] in identities for row in base['files']):
        raise ConflictError('MP3 component identity collides with native history')
    prefix = '/PIONEER/USBANLZ/' + uuid[:3] + '/' + uuid[3:] + '/ANLZ0000'
    updated = {**track, 'BPM': round(result['bpm'] * 100), 'KeyID': keys[0]['ID'],
        'AnalysisDataPath': prefix + '.DAT', 'BitRate': technical['bitrate'], 'BitDepth': 16, 'SampleRate': 44100,
        'ContentLink': 787982, 'Analysed': 105, 'AnalysisUpdated': '1', 'TrackInfoUpdated': '2',
        'rb_local_usn': clock + 17, 'updated_at': seeds['track_stamp']}
    status = dict(rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0, usn=None)
    files = []
    for offset, ext in enumerate(FILE_ORDER, 18):
        path, raw, stamp = prefix + ext, images[ext], seeds['file_stamps'][ext]
        files.append(dict(ID=uuid + '_' + quote(path, safe=''), ContentID=tid, Path=path,
            Hash=hashlib.md5(raw).hexdigest(), Size=len(raw), rb_local_path=str(share / path.lstrip('/')),
            rb_insync_hash=None, rb_insync_local_usn=None, rb_file_hash_dirty=0, rb_local_file_status=0,
            rb_in_progress=0, rb_process_type=0, rb_temp_path=None, rb_priority=50, rb_file_size_dirty=0,
            UUID=seeds['file_uuids'][ext], **status, rb_local_usn=clock + offset, created_at=stamp, updated_at=stamp))
    if any(row['ID'] in {old['ID'] for old in base['files']} for row in files):
        raise ConflictError('MP3 component file history collides')
    return dict(profile=PROFILE, source_write_authority=False, native_acceptance=False,
        source_sha256=audio.sha256, component_policy_accepted=False,
        scope='Copy-only CBR grid/key/waveform proposal; gain omitted and secondary grid empty require native acceptance',
        changes=[dict(table='djmdContent', id=tid, before=deepcopy(track), after=updated),
                 *(dict(table='contentFile', id=row['ID'], before=None, after=row) for row in files),
                 dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter),
                      after={**counter, 'int_1': clock + 20})])
