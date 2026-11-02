"""Accepted saved grid/key/waveform publication on the shared source worker lane.

A separate library/profile authority is required. Audio, gain, cues, playlists and
XML remain unchanged. Exact paired encrypted evidence and directory ownership
support restart recovery; the fixed research pilots keep their original limits.
"""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from .rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from .rekordbox_write import (ConflictError, RecoveryRequired, WriteProofError, _connect, _identity,
    _inactive_journal_proof, _json, _quote, _regular, _state, _rows, _cell, _backup,
    assert_rekordbox_closed, encrypted_backup)
from .state_io import atomic_bytes, copy_state, sync_dir
from .rekordbox_playlist_store import _sha
from . import native_analysis_plan as planner
from .native_registration_plan import _uuid
from .analysis import validate_result
from .local_grid import effective_result
from .native_media import MediaIdentity, epoch
from .source_acquisition import SourceContract, acquire_source
from .media_probe import read_media_header
from .cdj_usb import run_renderer

FORMAT = 'soundbox-native-analysis-v1'
PLAN_FORMAT = 'soundbox-native-analysis-plan-v1'
AUTHORITY_FORMAT='soundbox-analysis-authority-v1'
NAMES=('ANLZ0000.DAT','ANLZ0000.EXT','ANLZ0000.2EX')
PAYLOADS=('before.db','after.db','before.xml','after.xml',*NAMES)
TABLES=('djmdContent','agentRegistry','contentFile','djmdMixerParam')

@dataclass(frozen=True)
class AnalysisAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted_reference_sha256: str
    accepted: bool
    application_version: str = NATIVE_VERSION

    def __post_init__(self):
        if self.accepted is not True:raise WriteProofError('Native analysis requires separate accepted authority')
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'analysis',self.application_version)
        if (not isinstance(self.accepted_reference_sha256,str) or len(self.accepted_reference_sha256)!=64
                or any(c not in '0123456789abcdef' for c in self.accepted_reference_sha256)):
            raise WriteProofError('Unknown native analysis acceptance')


def load_authority(path,source,library_id):
    try:
        _regular(Path(path));body=json.loads(Path(path).read_bytes())
        if body.pop('format',None)!=AUTHORITY_FORMAT:return None
        a=AnalysisAuthority(**body)
        return a if (a.database,a.library_id)==(str(source),library_id) else None
    except (OSError,ValueError,TypeError,KeyError,WriteProofError):return None


def shared_root(data_root,source,library_id):
    key=hashlib.sha256((str(source)+'\0'+library_id).encode()).hexdigest()[:24]
    return Path(data_root)/'rekordbox_recovery/tag-writes'/key


def _paths(source,output,data_root):
    for path in (source,output,data_root):
        if not path.is_absolute() or path!=path.resolve() or path.is_relative_to('/Volumes'):
            raise WriteProofError('Explicit canonical desktop analysis paths required')
    if (output.parent!=data_root/'native-analysis-plans' or source.name!='master.db' or output.is_relative_to(source.parent) or source.is_relative_to(output)
            or data_root.is_relative_to(source.parent) or source.is_relative_to(data_root)):
        raise WriteProofError('Analysis paths overlap the source')


def _fault(_phase):
    """Synthetic TEMP crash tests only."""


def _encoded(row):return {k:_cell(v) for k,v in row.items()}


def _base(conn,tid):
    tracks=[_encoded(r) for r in _rows(conn,'djmdContent') if r['ID']==tid]
    counters=[_encoded(r) for r in _rows(conn,'agentRegistry') if r['registry_id']=='localUpdateCount']
    if len(tracks)!=1 or len(counters)!=1:raise ConflictError('Analysis track/counter is ambiguous')
    return dict(track=tracks[0],counter=counters[0],keys=_rows(conn,'djmdKey'),files=_rows(conn,'contentFile'),
        mixers=_rows(conn,'djmdMixerParam'),cues=[r for r in _rows(conn,'djmdCue') if r['ContentID']==tid],
        aggregates=[r for r in _rows(conn,'contentCue') if r['ContentID']==tid])


def _scope(audio,track,result):
    audio.validate();validate_result(result)
    if audio.path!=result['path'] or audio.sha256!=result['sha256'] or track['FolderPath']!=audio.path:
        raise WriteProofError('Analysis must retain its exact captured audio identity')


def _verify(before,after,changes):
    left,right=_state(before),_state(after)
    if left['schema']!=right['schema'] or left['metadata']!=right['metadata']:
        raise WriteProofError('Analysis changed schema/metadata')
    for table in left['tables'].keys()|right['tables'].keys():
        if table not in TABLES and left['tables'].get(table)!=right['tables'].get(table):
            raise WriteProofError('Analysis changed unrelated data')
    for table in TABLES:
        key='registry_id' if table=='agentRegistry' else 'ID'
        old=[_encoded(r) for r in _rows(before,table)];current=[_encoded(r) for r in _rows(after,table)]
        expected={r[key]:r for r in old}
        if len(expected)!=len(old):raise WriteProofError('Ambiguous analysis row')
        for c in changes:
            if c['table']!=table:continue
            if expected.get(c['id'])!=c['before']:raise ConflictError('Stale analysis proposal')
            expected[c['id']]=c['after']
        if len(current)!=len(expected) or {r[key]:r for r in current}!=expected:
            raise WriteProofError('Analysis changed unplanned rows')
    return right


def _copy_backup(source,destination):
    _backup(source,destination);conn=_connect(destination)
    try:
        if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
            raise WriteProofError('Cannot normalize disposable analysis copy')
    finally:conn.close()


def _execute(conn,changes):
    conn.set_authorizer(_authorizer)
    try:
        for c in changes:
            table=c['table'];key='registry_id' if table=='agentRegistry' else 'ID'
            cursor=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(c['id'],))
            columns=[v[0] for v in cursor.description]
            actual=[_encoded(dict(zip(columns,row,strict=True))) for row in cursor]
            if actual!=([] if c['before'] is None else [c['before']]):raise ConflictError('Stale analysis row')
            row=c['after']
            if set(row)!=set(columns) or row[key]!=c['id']:raise WriteProofError('Analysis schema changed')
            if c['before'] is None:
                conn.execute('INSERT INTO '+_quote(table)+' ('+','.join(_quote(k) for k in columns)+') VALUES ('+
                    ','.join('?' for _ in columns)+')',[row[k] for k in columns])
            else:
                changed=[k for k in columns if row[k]!=c['before'][k]]
                conn.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(k)+'=?' for k in changed)+' WHERE '+_quote(key)+'=?',
                             [row[k] for k in changed]+[c['id']])
    finally:conn.set_authorizer(None)


def _native_directory(path):
    info = path.lstat()
    if path != path.resolve() or not path.is_dir() or path.is_symlink() or info.st_uid != os.getuid():
        raise RecoveryRequired('Native analysis directory redirects or changed owner')


def _seeds():
    now = datetime.now(timezone.utc)
    def stamp(offset):
        return (now+timedelta(milliseconds=offset)).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
    return dict(track_stamp=stamp(1),
        file_uuids={ext:str(uuid4()) for ext in planner.FILE_ORDER},
        file_stamps={ext:stamp(i) for i,ext in enumerate(planner.FILE_ORDER,2)})


def prepare(authority, track_id, track_uuid, result, output, *, data_root, operation_id, renderer=None):
    """Capture/render/validate on copies. Native SQL is admitted only by apply()."""
    if not isinstance(authority,AnalysisAuthority):raise WriteProofError('Separate analysis authority required')
    _uuid(operation_id);_uuid(track_uuid);validate_result(result)
    result=json.loads(json.dumps(result));source=Path(authority.database)
    output=Path(output).absolute();data_root=Path(data_root).absolute();_paths(source,output,data_root)
    if output.name!=operation_id:raise WriteProofError('Analysis plan must retain its durable operation identity')
    assert_rekordbox_closed();_regular(source)
    if _installed_version()!=authority.application_version:raise WriteProofError('Analysis application version changed')
    root=shared_root(data_root,source,authority.library_id)
    if (root/'transaction.json').exists() or (root/'grid-transaction').exists():raise RecoveryRequired('Pending native edit')
    xml=source.with_name('masterPlaylists6.xml');_regular(xml);xml_bytes=xml.read_bytes()
    share=source.parent/'share';_native_directory(share)
    output.mkdir(mode=0o700,parents=True)
    intent=dict(format=PLAN_FORMAT,operation_id=operation_id,track_id=track_id,track_uuid=track_uuid,result=result)
    atomic_bytes(output/'intent.json',_json(intent))
    with tempfile.TemporaryDirectory(prefix='soundbox-native-analysis-') as area:
        scratch=Path(area).resolve();before=scratch/'before.db';after=scratch/'after.db'
        with encrypted_backup(source) as copied:_copy_backup(copied,before)
        conn=_connect(before,readonly=True)
        try:
            if _identity(conn)!=(authority.library_id,'6000') or _schema(conn)!=authority.schema_sha256:
                raise ConflictError('Analysis library/schema changed')
            base=_base(conn,track_id);before_state=_state(conn)
            if base['track']['UUID']!=track_uuid or conn.execute('SELECT COUNT(*) FROM djmdContent WHERE UUID=?',(track_uuid,)).fetchone()!=(1,):
                raise ConflictError('Analysis native identity changed')
        finally:conn.close()
        with acquire_source(SourceContract(Path(result['path']),result['size'],result['sha256']),scratch/'audio') as captured:
            header=read_media_header(captured.path);duration=header.duration
            audio=MediaIdentity(result['path'],epoch(captured.observation),captured.sha256,int(duration),header.family,
                header.codec,duration.numerator,duration.denominator,header.wave_format_tag)
            technical=dict(sample_rate=header.sample_rate,channels=header.channels,bits=header.bits)
            if (header.family!='wav' or header.codec not in ('pcm_s16le','pcm_s24le') or header.wave_format_tag!=1
                    or header.channels!=2 or header.sample_rate not in (44100,48000)):
                raise WriteProofError('Native analysis publication currently supports stereo PCM16/24 WAV at44.1/48kHz')
            effective=effective_result(result)
            request=scratch/'request.json';atomic_bytes(request,_json(dict(audio=str(captured.path),original_name=Path(audio.path).name,
                beats=[dict(number=b[0],time_ms=b[1],tempo=round(b[2]*100)) for b in effective['beats']])))
            if renderer:images=renderer(scratch,request)
            else:
                run_renderer('saved-analysis',scratch,request)
                images={Path(n).suffix:(scratch/'packet'/n).read_bytes() for n in NAMES}
        seeds=_seeds();candidate=planner.plan(base,audio,images,share_root=str(share),seeds=seeds,result=result,technical=technical,native_length=True)
        shutil.copyfile(before,after);conn=_connect(after)
        try:
            conn.execute('BEGIN IMMEDIATE');_execute(conn,candidate['changes']);conn.commit()
            left=_connect(before,readonly=True)
            try:after_state=_verify(left,conn,candidate['changes'])
            finally:left.close()
        finally:conn.close()
        for src,name in ((before,'before.db'),(after,'after.db')):shutil.copyfile(src,output/name)
        plan=dict(format=PLAN_FORMAT,operation_id=operation_id,source=str(source),source_file_id=[source.stat().st_dev,source.stat().st_ino],
            library_id=authority.library_id,schema_sha256=authority.schema_sha256,application_version=authority.application_version,
            data_root=str(data_root),recovery_root=str(root),share=str(share),share_file_id=[share.stat().st_dev,share.stat().st_ino],
            audio=asdict(audio),seeds=seeds,track_id=track_id,track_uuid=track_uuid,result=result,technical=technical,
            authority=asdict(authority),before=before_state,after=after_state,candidate=candidate,source_write_authority=False)
    for name in ('before.xml','after.xml'):atomic_bytes(output/name,xml_bytes)
    for name in NAMES:atomic_bytes(output/name,images[Path(name).suffix])
    plan['payload_sha256']={n:_sha(output/n) for n in PAYLOADS};atomic_bytes(output/'plan.json',_json(plan))
    audio.revalidate();assert_rekordbox_closed()
    if xml.read_bytes()!=xml_bytes:raise ConflictError('Native XML changed during analysis preparation')
    return load_plan(output)


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Private original saved-components plan required')
    for name in (*PAYLOADS,'plan.json'): _regular(folder/name)
    record = json.loads((folder/'plan.json').read_bytes())
    if (record['format'] != PLAN_FORMAT or record['application_version'] != NATIVE_VERSION
            or record.get('source_write_authority') is not False):
        raise WriteProofError('Unknown saved-components plan/version')
    _uuid(record['operation_id']); _paths(Path(record['source']),folder,Path(record['data_root']))
    authority=AnalysisAuthority(**record['authority'])
    if (authority.database,authority.library_id,authority.schema_sha256,authority.application_version)!=(
            record['source'],record['library_id'],record['schema_sha256'],record['application_version']):
        raise WriteProofError('Analysis plan exceeds its accepted source authority')
    if (shared_root(record['data_root'],record['source'],record['library_id']) != Path(record['recovery_root'])
            or Path(record['share']) != Path(record['source']).parent/'share'):
        raise WriteProofError('Saved component must use its exact native share/recovery root')
    if set(record['payload_sha256']) != set(PAYLOADS): raise WriteProofError('Unknown analysis payloads')
    for name in PAYLOADS:
        if _sha(folder/name) != record['payload_sha256'][name]: raise RecoveryRequired('Saved-component payload changed')
    if (folder/'before.xml').read_bytes() != (folder/'after.xml').read_bytes():
        raise WriteProofError('Saved component cannot change playlist XML')
    with tempfile.TemporaryDirectory(prefix='soundbox-analysis-plan-check-') as area:
        scratch = Path(area).resolve()
        for name in ('before.db','after.db'): shutil.copyfile(folder/name,scratch/name)
        before = _connect(scratch/'before.db',readonly=True); after = _connect(scratch/'after.db',readonly=True)
        try:
            audio = MediaIdentity.from_dict(record['audio']); base = _base(before,record['track_id']); _scope(audio,base['track'],record['result'])
            if (base['track']['UUID'] != record['track_uuid'] or before.execute(
                    'SELECT COUNT(*) FROM djmdContent WHERE UUID=?',(record['track_uuid'],)).fetchone() != (1,)):
                raise WriteProofError('Saved component UUID changed or is ambiguous')
            expected = planner.plan(base,audio,{Path(n).suffix:(folder/n).read_bytes() for n in NAMES},
                                    share_root=record['share'],seeds=record['seeds'],result=record['result'],technical=record['technical'],native_length=True)
            if (_schema(before) != record['schema_sha256'] or _identity(before)[0] != record['library_id']
                    or _state(before) != record['before'] or expected != record['candidate']
                    or _verify(before,after,expected['changes']) != record['after']):
                raise WriteProofError('Saved-component plan no longer proves its exact full delta')
        finally: before.close(); after.close()
    return record



def _authorizer(action,table,column,database,trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main': return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_INSERT and table=='contentFile': return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE:
            if table=='agentRegistry' and column=='int_1': return sqlite3.SQLITE_OK
            if table=='djmdContent' and column in ('BPM','Length','BitRate','BitDepth','SampleRate','KeyID','AnalysisDataPath',
                    'ContentLink','Analysed','AnalysisUpdated','TrackInfoUpdated','rb_local_usn','updated_at'):
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY

class _Lease(PilotCueStore):
    def __init__(self, owner):
        self.owner = owner; p = owner.plan
        super().__init__(CueWriteAuthority(p['source'],p['library_id'],p['schema_sha256'],
            'analysis:'+p['track_id'],p['application_version']),Path(p['recovery_root']))

    def _guard(self):
        self.owner._approved(); super()._guard(); self.owner._source_identity()

    def _record(self): return self.owner._record()


class AnalysisStore:
    def __init__(self, authority, folder):
        if not isinstance(authority,AnalysisAuthority):raise WriteProofError('Separate analysis authority required')
        self.authority=authority;self.folder=Path(folder).absolute();self.plan=load_plan(self.folder)
        self.plan_sha=_sha(self.folder/'plan.json');self.database=Path(self.plan['source'])
        self.xml=self.database.with_name('masterPlaylists6.xml');self.root=Path(self.plan['recovery_root'])
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.journal=self.root/'transaction.json';self.archive=self.root/('analysis-'+self.plan['operation_id'])
        self.share=Path(self.plan['share']);self._approved();self.lease=_Lease(self)

    def _approved(self):
        if (self.folder!=self.folder.resolve() or _sha(self.folder/'plan.json')!=self.plan_sha
                or json.loads((self.folder/'plan.json').read_bytes())!=self.plan
                or self.plan['authority']!=asdict(self.authority)
                or (self.authority.database,self.authority.library_id,self.authority.schema_sha256)!=(
                    self.plan['source'],self.plan['library_id'],self.plan['schema_sha256'])):
            raise WriteProofError('Analysis plan/authority changed')

    def recover(self):
        with self.lease._lock():return self._recover()

    def _snapshot(self):
        with encrypted_backup(self.database) as captured:
            conn=_connect(captured,readonly=True)
            try:return _state(conn)
            finally:conn.close()

    def discard_unstarted(self):
        """Renew only a verified copy plan that has never published anything."""
        with self.lease._lock():
            stage,target=self._locations()
            if self.journal.exists() or self.archive.exists() or stage.exists() or target.exists():return False
            allowed={*PAYLOADS,'plan.json','intent.json'}
            if {p.name for p in self.folder.iterdir()}!=allowed:
                raise RecoveryRequired('Unknown unstarted analysis preparation')
            for name in PAYLOADS:
                if _sha(self.folder/name)!=self.plan['payload_sha256'][name]:raise RecoveryRequired('Unstarted payload changed')
            for p in self.folder.iterdir():_regular(p)
            for name in (*PAYLOADS,'intent.json','plan.json'):(self.folder/name).unlink()
            self.folder.rmdir();sync_dir(self.folder.parent)
            return True

    def _source_identity(self):
        info=self.database.stat()
        if [info.st_dev,info.st_ino]!=self.plan['source_file_id']:raise ConflictError('Analysis source was replaced')
        _regular(self.xml)
        if self.xml!=self.xml.resolve() or self.xml.stat().st_uid!=os.getuid():raise WriteProofError('Native XML redirects')
        _native_directory(self.share)
        if [self.share.stat().st_dev,self.share.stat().st_ino] != self.plan['share_file_id']:
            raise RecoveryRequired('Native share was replaced')
        self._locations()

    def _manifest(self):
        return dict(format=FORMAT,plan_sha256=self.plan_sha,operation_id=self.plan['operation_id'],
            source=self.plan['source'],source_file_id=self.plan['source_file_id'],library_id=self.plan['library_id'],
            schema_sha256=self.plan['schema_sha256'],payloads=self.archive.name,payload_sha256=self.plan['payload_sha256'],
            before=self.plan['before'],after=self.plan['after'],candidate=self.plan['candidate'],
            share=self.plan['share'],share_file_id=self.plan['share_file_id'],plan_folder=str(self.folder))

    def _payloads(self):
        _native_directory(self.archive)
        if self.archive.stat().st_mode & 0o077: raise RecoveryRequired('Analysis archive is not private')
        if not {p.name for p in self.archive.iterdir()} <= {*PAYLOADS,'operation.json','result.json'}:
            raise RecoveryRequired('Unknown analysis archive payload')
        for name in (*PAYLOADS,'operation.json'): _regular(self.archive/name)
        if (self.archive/'result.json').exists() or (self.archive/'result.json').is_symlink():
            _regular(self.archive/'result.json')
            receipt = json.loads((self.archive/'result.json').read_bytes())
            if ({k:receipt.get(k) for k in self._manifest()} != self._manifest()
                    or receipt.get('outcome') not in ('committed','rolled-back')
                    or receipt.get('native_acceptance') is not False):
                raise RecoveryRequired('Unknown saved-components receipt; preserved')
        if json.loads((self.archive/'operation.json').read_bytes()) != self._manifest():
            raise RecoveryRequired('Analysis archive identity changed')
        for name in PAYLOADS:
            if _sha(self.archive/name) != self.plan['payload_sha256'][name]: raise RecoveryRequired('Analysis archive bytes changed')

    def _install_payloads(self):
        if not self.archive.exists():
            self.archive.mkdir(mode=0o700)
            atomic_bytes(self.archive/'operation.json',_json(self._manifest()))
            for name in PAYLOADS: copy_state(self.folder/name,self.archive/name)
            sync_dir(self.archive); sync_dir(self.root)
        self._payloads()

    def _record(self):
        self._approved(); _regular(self.journal); record = json.loads(self.journal.read_bytes())
        if ({k:record.get(k) for k in self._manifest()} != self._manifest()
                or set(record) != {*self._manifest(),'directory_id','cleanup_started'}
                or type(record['cleanup_started']) is not bool
                or (record['directory_id'] is not None and (not isinstance(record['directory_id'],list)
                    or len(record['directory_id']) != 2 or any(type(n) is not int or n < 0 for n in record['directory_id'])))):
            raise RecoveryRequired('Unknown domain or saved-components journal identity')
        self._payloads(); return record

    def _locations(self):
        uuid = self.plan['track_uuid']
        target = self.share/'PIONEER'/'USBANLZ'/uuid[:3]/uuid[3:]
        stage = self.share/('.soundbox-analysis-'+self.plan['operation_id'])
        for path in (stage,target,*target.parents):
            if not path.is_relative_to(self.share): continue
            if path.exists() or path.is_symlink(): _native_directory(path)
        return stage,target

    def _files(self, directory, *, complete=False):
        _native_directory(directory)
        names = {p.name for p in directory.iterdir()}
        if not names <= set(NAMES) or complete and names != set(NAMES):
            raise RecoveryRequired('Unknown or missing saved-components file')
        for path in directory.iterdir():
            _regular(path)
            if _sha(path) != self.plan['payload_sha256'][path.name]:
                raise RecoveryRequired('Owned saved-components bytes changed; preserved')

    def _finish(self, record, outcome):
        self.lease._guard()
        if (self._snapshot() != self.plan['after' if outcome=='committed' else 'before']
                or _sha(self.xml) != self.plan['payload_sha256']['before.xml']):
            raise RecoveryRequired('Saved-component DB/XML changed before recovery completion')
        receipt = {**record,'outcome':outcome,'native_acceptance':False,
                   'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
        atomic_bytes(self.archive/'result.json',_json(receipt)); atomic_bytes(self.root/'last-result.json',_json(receipt))
        _fault('receipt-ready'); self.lease._guard(); self.journal.unlink(); sync_dir(self.root); _fault('journal-retired')
        self._retire_inactive(receipt)
        return outcome

    def _retire_inactive(self, receipt):
        proof = receipt['inactive_sqlite_journal']; journal = self.database.with_name('master.db-journal')
        if proof is not None and journal.exists():
            self.lease._guard()
            if _inactive_journal_proof(self.database) != proof: raise RecoveryRequired('Inactive analysis journal changed')
            journal.unlink(); sync_dir(self.database.parent)

    def _recover(self):
        if not self.journal.exists():
            if not (self.archive/'result.json').exists(): return None
            self._payloads(); receipt = json.loads((self.archive/'result.json').read_bytes())
            if ({k:receipt.get(k) for k in self._manifest()} != self._manifest()
                    or receipt.get('outcome') not in ('committed','rolled-back')):
                raise RecoveryRequired('Unknown saved-components completion receipt')
            self._retire_inactive(receipt)
            return receipt['outcome']
        record = self._record(); stage,target = self._locations()
        if _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
            raise RecoveryRequired('Native XML changed; saved-components recovery preserved')
        conn = self.lease._connect()
        try: conn.execute('BEGIN IMMEDIATE'); current = self._snapshot()
        finally: conn.rollback(); conn.close()
        if current not in (self.plan['before'],self.plan['after']):
            raise RecoveryRequired('Native DB is outside the exact saved-components recovery pair')
        existing = [p for p in (stage,target) if p.exists() or p.is_symlink()]
        if len(existing) > 1: raise RecoveryRequired('Impossible saved-components directory prefix')
        identity = record['directory_id']
        if current == self.plan['after']:
            if existing != [target] or [target.stat().st_dev,target.stat().st_ino] != identity:
                raise RecoveryRequired('Committed analysis directory missing or replaced')
            self._files(target,complete=True); return self._finish(record,'committed')
        if existing:
            directory = existing[0]; self._files(directory)
            if identity is None:
                if directory != stage or any(directory.iterdir()): raise RecoveryRequired('Unowned analysis stage')
            elif [directory.stat().st_dev,directory.stat().st_ino] != identity:
                raise RecoveryRequired('Owned analysis directory was replaced')
            self.lease._guard(); record['cleanup_started'] = True; atomic_bytes(self.journal,_json(record))
            for path in list(directory.iterdir()):
                self.lease._guard(); self._files(directory); path.unlink(); sync_dir(directory)
            self.lease._guard(); directory.rmdir(); sync_dir(directory.parent)
        elif identity is not None and not record['cleanup_started']:
            raise RecoveryRequired('Owned analysis directory disappeared unexpectedly')
        return self._finish(record,'rolled-back')

    def apply(self):
        with self.lease._lock():
            if self._recover() == 'committed': return 'already-completed'
            MediaIdentity.from_dict(self.plan['audio']).revalidate()
            if self._snapshot() != self.plan['before'] or _sha(self.xml) != self.plan['payload_sha256']['before.xml']:
                raise ConflictError('Saved-component source changed since review')
            stage,target = self._locations()
            if any(p.exists() or p.is_symlink() for p in (stage,target)):
                raise ConflictError('Saved-component destination is occupied')
            self._install_payloads(); _fault('payload-ready')
            record = {**self._manifest(),'directory_id':None,'cleanup_started':False}
            atomic_bytes(self.journal,_json(record)); _fault('journal-ready')
            self.lease._guard(); stage.mkdir(mode=0o700); sync_dir(self.share); _fault('stage-created')
            record['directory_id'] = [stage.stat().st_dev,stage.stat().st_ino]
            atomic_bytes(self.journal,_json(record))
            for name in NAMES:
                self.lease._guard(); atomic_bytes(stage/name,(self.archive/name).read_bytes()); _fault('file-'+name)
            self._files(stage,complete=True)
            chain=[]; parent=target.parent
            while parent != self.share: chain.append(parent); parent=parent.parent
            for parent in reversed(chain):
                self.lease._guard()
                if not parent.exists(): parent.mkdir(mode=0o700); sync_dir(parent.parent)
            self.lease._guard(); self._locations()
            if target.exists() or target.is_symlink(): raise ConflictError('Analysis target appeared before publication')
            os.rename(stage,target); sync_dir(target.parent); sync_dir(self.share); _fault('files-published')
            conn = self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                if self._snapshot() != self.plan['before']: raise ConflictError('Analysis source changed before SQL')
                self._apply_sql(conn); _fault('sql-applied'); self.lease._guard()
                MediaIdentity.from_dict(self.plan['audio']).revalidate(); self._files(target,complete=True)
                if _sha(self.xml) != self.plan['payload_sha256']['before.xml']: raise ConflictError('Native XML changed')
                conn.commit(); _fault('sql-committed')
            except BaseException: conn.rollback(); raise
            finally: conn.close()
            if self._recover() != 'committed': raise RecoveryRequired('Saved-component publication did not verify')
            return self.plan['track_id']

    def _apply_sql(self, conn):
        databases = conn.execute('PRAGMA database_list').fetchall()
        if len(databases) != 1 or Path(databases[0][2]) != self.database:
            raise WriteProofError('Saved-component SQL belongs only to its explicitly approved source')
        conn.set_authorizer(_authorizer)
        try:
            for change in self.plan['candidate']['changes']:
                table=change['table']; key='registry_id' if table=='agentRegistry' else 'ID'
                cursor=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(change['id'],))
                columns=[c[0] for c in cursor.description]
                actual=[_encoded(dict(zip(columns,row,strict=True))) for row in cursor]
                if actual != ([] if change['before'] is None else [change['before']]): raise ConflictError('Stale analysis row')
                row=change['after']
                if set(columns) != set(row) or row[key] != change['id']: raise WriteProofError('Analysis row/schema changed')
                if change['before'] is None:
                    conn.execute('INSERT INTO '+_quote(table)+' ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                                 ','.join('?' for _ in columns)+')',[row[c] for c in columns])
                else:
                    cols=[c for c in columns if row[c] != change['before'][c]]
                    conn.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(c)+'=?' for c in cols)+' WHERE '+_quote(key)+'=?',
                                 [row[c] for c in cols]+[change['id']])
                check=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(change['id'],)).fetchall()
                if [_encoded(dict(zip(columns,r,strict=True))) for r in check] != [row]:
                    raise WriteProofError('Analysis source row readback differs')
        finally: conn.set_authorizer(None)




def retire_confirmed(store, confirmed_operations):
    """Keep the first native reference and latest full pair; retire duplicates.

    GUI receipt persistence supplies confirmation. Shared locking and resumable
    exact-hash retirement never remove pending or unknown recovery material.
    All permanent operation/result/packet receipts survive.
    """
    confirmed=set(confirmed_operations)
    with store.lease._lock():
        if store.journal.exists():raise RecoveryRequired('Analysis retention cannot run during recovery')
        reference=store.root/'analysis-reference.json'
        if not reference.exists():
            atomic_bytes(reference,_json(dict(format=FORMAT,operation_id=store.plan['operation_id'],source=str(store.database))))
        _regular(reference);protected=json.loads(reference.read_bytes())
        if protected.get('format')!=FORMAT or protected.get('source')!=str(store.database):
            raise RecoveryRequired('Unknown analysis reference retention')
        for archive in store.root.glob('analysis-*'):
            if not archive.is_dir() or archive.is_symlink():continue
            op=archive.name.removeprefix('analysis-')
            if op not in confirmed or op==store.plan['operation_id']:continue
            _native_directory(archive);_regular(archive/'result.json')
            result=json.loads((archive/'result.json').read_bytes())
            if (result.get('format')!=FORMAT or result.get('source')!=str(store.database)
                    or result.get('operation_id')!=op or result.get('outcome')!='committed'):
                raise RecoveryRequired('Unknown confirmed analysis archive')
            plan_folder=Path(result['plan_folder'])
            if plan_folder.parent!=store.folder.parent or plan_folder.name!=op or plan_folder!=plan_folder.resolve():
                raise RecoveryRequired('Analysis plan retention redirects')
            choices=[(plan_folder,'plan')]
            if op!=protected['operation_id']:choices.append((archive,'archive'))
            for folder,kind in choices:
                marker=folder/'retirement.json';payload={n:result['payload_sha256'][n] for n in ('before.db','after.db','before.xml','after.xml')}
                receipt=dict(format=FORMAT,operation_id=op,kind=kind,payload_sha256=payload)
                if marker.exists():
                    _regular(marker)
                    if json.loads(marker.read_bytes())!=receipt:raise RecoveryRequired('Unknown analysis retirement prefix')
                else:
                    for name,digest in payload.items():
                        if _sha(folder/name)!=digest:raise RecoveryRequired('Analysis retention payload changed')
                    atomic_bytes(marker,_json(receipt))
                for name,digest in payload.items():
                    path=folder/name
                    if path.exists() or path.is_symlink():
                        if _sha(path)!=digest:raise RecoveryRequired('Analysis retirement bytes changed')
                        store.lease._guard();path.unlink();sync_dir(folder)
