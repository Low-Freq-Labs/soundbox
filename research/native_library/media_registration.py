"""Bare multi-format registration construction/replay; no source publisher.

The original known-WAV adapter remains narrow. Facts here come from one captured
source plus an explicitly supplied development ffprobe. Native-reference replay
is required before any production probe, format policy or source authority.
"""
from fractions import Fraction
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from soundbox.source_acquisition import SourceContract, acquire_source
from soundbox.native_media import MediaIdentity, TYPES, capture_media
from soundbox.rekordbox_write import ConflictError, WriteProofError, _digest, _json, _quote, _regular, _state
from . import registration as wav

FORMAT='soundbox-media-registration-proof-v1'
PREFIX='soundbox-media-registration-'




def _wave_tag(snapshot):
    size=snapshot.stat().st_size
    with snapshot.open('rb') as handle:
        if handle.read(4)!=b'RIFF':raise WriteProofError('Only referenced RIFF WAVE framing is proven')
        handle.seek(8)
        if handle.read(4)!=b'WAVE':raise WriteProofError('Missing WAVE identity')
        for _ in range(64):
            raw=handle.read(8)
            if len(raw)!=8:break
            kind,length=struct.unpack('<4sI',raw)
            if handle.tell()+length>size:raise WriteProofError('WAVE chunk extends past EOF')
            if kind==b'fmt ':
                if not 16<=length<=40:raise WriteProofError('Unreferenced WAVE format header')
                return struct.unpack('<H',handle.read(2))[0]
            handle.seek(length+(length&1),1)
    raise WriteProofError('No bounded WAVE format header')


def inspect_media(path,scratch,*,ffprobe,expected_sha256=None):
    path=Path(path).absolute();ffprobe=Path(ffprobe).absolute()
    if path!=path.resolve(strict=True) or path.is_symlink():raise WriteProofError('Canonical media path required')
    if ffprobe!=ffprobe.resolve(strict=True):raise WriteProofError('Explicit canonical development ffprobe required')
    with acquire_source(SourceContract(path,sha256=expected_sha256),Path(scratch)) as snapshot:
        result=subprocess.run([str(ffprobe),'-v','error','-show_streams','-show_format','-of','json',str(snapshot.path)],
                              check=True,capture_output=True,text=True,timeout=30)
        data=json.loads(result.stdout);streams=[s for s in data['streams'] if s['codec_type']=='audio']
        if len(streams)!=1 or any(s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic') for s in data['streams']):
            raise WriteProofError('Exactly one audio stream without real video is required')
        stream=streams[0];codec=stream['codec_name'];names=set(data['format']['format_name'].split(','))
        family=next((name for name in ('wav','aiff','mp3','flac') if name in names),None)
        if family is None and names&{'mov','mp4','m4a'}:family='m4a'
        if (family,codec) not in TYPES:raise WriteProofError('Unreferenced media family/codec')
        duration=Fraction(data['format']['duration'])
        tag=_wave_tag(snapshot.path) if family=='wav' else None
        facts=MediaIdentity(str(path),wav.epoch(snapshot.observation),snapshot.sha256,
            0 if tag==65534 else int(duration),family,codec,duration.numerator,duration.denominator,tag)
        facts.validate();return facts




def plan(conn,audio,*,identity,uuid,timestamp,day):
    audio.validate()
    candidate=wav.plan(conn,audio,identity=identity,uuid=uuid,timestamp=timestamp,day=day)
    candidate['row']['FileType']=audio.file_type
    candidate['format']=FORMAT
    return candidate


def validate_plan(conn,candidate):
    if candidate.get('format')!=FORMAT:raise WriteProofError('Unknown media registration plan')
    audio=MediaIdentity.from_dict(candidate['audio']);row=candidate['row']
    expected=plan(conn,audio,identity=row['ID'],uuid=row['UUID'],timestamp=row['created_at'],day=row['StockDate'])
    if _digest(expected)!=_digest(candidate):raise WriteProofError('Multi-format registration intent changed')
    return audio


def apply_temp(conn,candidate):
    databases=conn.execute('PRAGMA database_list').fetchall()
    if len(databases)!=1:raise WriteProofError('No attached media databases')
    path=Path(databases[0][2]);root=path.parent
    if (path!=path.resolve() or path.name!='working.db' or not root.name.startswith(PREFIX)
            or not root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
        raise WriteProofError('Media registration SQL is original-TEMP-only')
    marker=root/'workspace.json';_regular(marker);record=json.loads(marker.read_bytes());info=root.stat()
    if record!=dict(format=FORMAT,root=str(root),root_id=[info.st_dev,info.st_ino]):
        raise WriteProofError('Relocated or unmarked media workspace')
    validate_plan(conn,candidate);conn.set_authorizer(wav._authorizer)
    try:
        row=candidate['row'];columns=list(row)
        conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                     ','.join('?' for _ in columns)+')',list(row.values()))
        conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                     (candidate['counter_after']['int_1'],candidate['counter_before']['int_1']))
    finally:conn.set_authorizer(None)


def mark(root):
    root=Path(root).absolute()
    if root!=root.resolve(strict=True):raise WriteProofError('Media workspace redirects')
    if not root.name.startswith(PREFIX) or not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise WriteProofError('Original TEMP media proof root required')
    root.chmod(0o700);info=root.stat()
    marker=root/'workspace.json'
    if marker.exists():raise WriteProofError('Media workspace already marked')
    marker.write_bytes(_json(dict(format=FORMAT,root=str(root),root_id=[info.st_dev,info.st_ino])))


def verify(before,after,candidates):
    """Strict full replay: only independently planned content inserts/counter."""
    from soundbox.rekordbox_write import _cell,_rows
    left,right=_state(before),_state(after)
    if left['schema']!=right['schema'] or left['metadata']!=right['metadata']:
        raise WriteProofError('Media registration changed schema/metadata')
    for table in left['tables']:
        if table not in ('djmdContent','agentRegistry') and left['tables'][table]!=right['tables'][table]:
            raise WriteProofError('Media registration changed unrelated rows')
    for table,key in (('djmdContent','ID'),('agentRegistry','registry_id')):
        expected={r[key]:{k:_cell(v) for k,v in r.items()} for r in _rows(before,table)}
        for candidate in candidates:
            if table=='djmdContent':
                row=candidate['row']
                if row['ID'] in expected:raise ConflictError('Existing media identity')
                expected[row['ID']]=row
            else:
                if expected['localUpdateCount']!=candidate['counter_before']:raise ConflictError('Counter replay is stale')
                expected['localUpdateCount']=candidate['counter_after']
        actual={r[key]:{k:_cell(v) for k,v in r.items()} for r in _rows(after,table)}
        if actual!=expected:raise WriteProofError('Multi-format replay has an unplanned full-table delta')
    return right


def inspect_packaged_media(path,scratch,*,expected_sha256=None):
    return capture_media(path,scratch,expected_sha256=expected_sha256)
