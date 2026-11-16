"""Independently admitted native title/artist saves; never audio retagging.

The measured ASCII/local/analysed/empty-album profile is deliberately bounded.
An encrypted current baseline and exact semantic transition protect the common
writer journal. Completed operation receipts survive subsequent baseline reuse.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from uuid import UUID,uuid4

from . import native_metadata_plan as planner
from .rekordbox_analysis_store import shared_root
from .rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from .rekordbox_tags import NATIVE_VERSION,_schema,_file_sha
from .rekordbox_write import (ConflictError,RecoveryRequired,WriteProofError,_cell,_connect,
    _digest,_identity,_inactive_journal_proof,_json,_quote,_regular)
from .state_io import atomic_bytes,copy_state,sync_dir

FORMAT='soundbox-native-metadata-v1'
ACK_FORMAT='soundbox-native-metadata-ack-v1'
AUTHORITY_FORMAT='soundbox-native-metadata-authority-v1'
PROFILE=planner.LOCAL_PROFILE
SYNCED_PROFILE=planner.ACTIVE_PROFILE
_MAX_RECORD=16*1024**2
_FIELDS=frozenset(('Title','ArtistID','AlbumID','TrackInfoUpdated','rb_local_usn','updated_at'))
_REQUEST=frozenset(('operation_id','library_id','track_id','track_uuid','path','field','expected','value','timestamp','artist_seed','artist_pins'))


@dataclass(frozen=True)
class MetadataAuthority:
    database:str
    library_id:str
    schema_sha256:str
    accepted:bool=False
    profile:str=''
    pilot_operation_ids:tuple[str,...]=()
    application_version:str=NATIVE_VERSION
    format:str=AUTHORITY_FORMAT

    def __post_init__(self):
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'metadata-authority',self.application_version)
        if (type(self.accepted) is not bool or self.format!=AUTHORITY_FORMAT or type(self.profile) is not str
                or not isinstance(self.pilot_operation_ids,tuple)
                or any(not _operation(value) for value in self.pilot_operation_ids)):
            raise WriteProofError('Invalid independently accepted metadata authority')


def _operation(value):
    return type(value) is str and len(value)==32 and all(c in '0123456789abcdef' for c in value)


def _native_id(value):
    if (type(value) is not str or not value.isascii() or not value.isdecimal()
            or not 0<int(value)<2**32 or str(int(value))!=value):
        raise WriteProofError('Invalid native metadata identity')


def validate_request(request):
    if not isinstance(request,dict) or set(request)!=_REQUEST:
        raise WriteProofError('Unknown metadata save request')
    _native_id(request['track_id'])
    try:valid_uuid=str(UUID(request['track_uuid']))==request['track_uuid']
    except (ValueError,TypeError,AttributeError):valid_uuid=False
    if (not valid_uuid or not _operation(request['operation_id']) or request['field'] not in ('title','artist')
            or type(request['library_id']) is not str or not request['library_id']
            or type(request['path']) is not str or not Path(request['path']).is_absolute() or '\0' in request['path']
            or type(request['expected']) is not str or len(request['expected'])>4096 or '\0' in request['expected']):
        raise WriteProofError('Metadata target, expected value or field is invalid')
    if (type(request['value']) is not str or '\0' in request['value'] or len(request['value'])>4096
            or request['field']=='title' and not request['value']):
        raise WriteProofError('Invalid desired native metadata value')
    planner._clock(request['timestamp'])
    pins=request['artist_pins']
    if not isinstance(pins,dict) or len(pins)>8 or request['field']=='title' and pins:
        raise WriteProofError('Invalid bounded metadata artist pins')
    for identity,uuid in pins.items():
        _native_id(identity)
        if type(uuid) is not str or not uuid or len(uuid)>4096 or '\0' in uuid:
            raise WriteProofError('Invalid metadata artist UUID pin')
    seed=request['artist_seed']
    if seed is not None:
        if request['field']!='artist' or not isinstance(seed,dict) or set(seed)!={'ID','UUID','timestamp'}:
            raise WriteProofError('Unknown metadata artist seed')
        _native_id(seed['ID']);planner._clock(seed['timestamp'])
        try:valid=str(UUID(seed['UUID']))==seed['UUID'] and UUID(seed['UUID']).version==4
        except (ValueError,TypeError,AttributeError):valid=False
        if not valid or seed['timestamp']>request['timestamp']:
            raise WriteProofError('Invalid metadata artist identity/clock')
        if pins.get(seed['ID'])!=seed['UUID']:
            raise WriteProofError('The captured artist seed requires its exact identity pin')
    return request


def request_for(library_id,track_id,track_uuid,path,field,value,expected,*,now=None,artist_seed=None,operation_id=None,artist_pins=None):
    clock=now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:raise WriteProofError('An aware metadata clock is required')
    stamp=clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
    return validate_request(dict(operation_id=operation_id or uuid4().hex,library_id=library_id,track_id=track_id,
        track_uuid=track_uuid,path=str(path),field=field,value=value,expected=expected,timestamp=stamp,
        artist_seed=deepcopy(artist_seed),artist_pins=deepcopy(artist_pins or {})))


def new_artist_seed(*,now=None):
    clock=now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:raise WriteProofError('An aware artist clock is required')
    return dict(ID=str(secrets.randbelow(2**32-1)+1),UUID=str(uuid4()),
                timestamp=clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00')


def _read_json(path,limit=_MAX_RECORD):
    _regular(path)
    def epoch(info):return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns
    before=epoch(path.stat())
    if before[2]>limit:raise RecoveryRequired('Metadata proof exceeds its read bound')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as handle:
        if epoch(os.fstat(handle.fileno()))!=before:raise RecoveryRequired('Metadata proof changed before reading')
        raw=handle.read(limit+1)
        if len(raw)>limit or epoch(os.fstat(handle.fileno()))!=before or epoch(path.stat())!=before:
            raise RecoveryRequired('Metadata proof changed while reading')
    return json.loads(raw)


def load_authority(path,source,library_id):
    try:
        data=_read_json(Path(path),65536)
        if data.get('format')!=AUTHORITY_FORMAT:return None
        data['pilot_operation_ids']=tuple(data.get('pilot_operation_ids',()))
        authority=MetadataAuthority(**data)
        return authority if ((authority.database,authority.library_id)==(str(source),library_id)
                             and authority.accepted and authority.profile in planner.PROFILES) else None
    except (OSError,ValueError,TypeError,KeyError,AttributeError,WriteProofError):return None


def _rows(conn,table,where='',args=()):
    cursor=conn.execute('SELECT * FROM '+_quote(table)+where,args);names=[v[0] for v in cursor.description]
    return [{key:_cell(value) for key,value in zip(names,row,strict=True)} for row in cursor]


def _one(conn,table,key):
    pk='registry_id' if table=='agentRegistry' else 'ID'
    values=_rows(conn,table,' WHERE '+_quote(pk)+'=?',(key,))
    if len(values)>1:raise ConflictError('Ambiguous metadata row')
    return values[0] if values else None


def _value(snapshot,field):
    target=snapshot['target']
    if field=='title':return target['Title']
    identity=target['ArtistID']
    if not identity:return ''
    matches=[row for row in snapshot['artists'] if row['ID']==identity]
    if len(matches)!=1:raise ConflictError('The source artist is unavailable or ambiguous')
    return matches[0]['Name']


def _snapshot(conn,request):
    target=_one(conn,'djmdContent',request['track_id'])
    counter=_one(conn,'agentRegistry','localUpdateCount')
    if request['field']=='title':return dict(target=target,counter=counter,artists=[],references={})
    wanted=set(request['artist_pins'])
    if target and target.get('ArtistID'):wanted.add(target['ArtistID'])
    criteria=[];args=[]
    if wanted:criteria.append('ID IN ('+','.join('?' for _ in wanted)+')');args.extend(sorted(wanted))
    if request['value']:criteria.append('Name=?');args.append(request['value'])
    uuids=set(request['artist_pins'].values())
    if uuids:criteria.append('UUID IN ('+','.join('?' for _ in uuids)+')');args.extend(sorted(uuids))
    actors=_rows(conn,'djmdArtist',' WHERE ('+' OR '.join(criteria)+') ORDER BY ID LIMIT ?',
                 (*args,9)) if criteria else []
    if len(actors)>8:raise WriteProofError('Metadata artist matches are ambiguous or exceed the bounded dependency scope')
    selected_uuids={actor['UUID'] for actor in actors}
    if selected_uuids:
        actors=_rows(conn,'djmdArtist',' WHERE UUID IN ('+','.join('?' for _ in selected_uuids)+') ORDER BY ID LIMIT ?',
                     (*sorted(selected_uuids),9))
        if len(actors)>8:raise WriteProofError('Metadata artist UUIDs are ambiguous')
    wanted.update(actor['ID'] for actor in actors)
    references:dict[str,int]={}
    for table,columns in (('djmdContent',('ArtistID','OrgArtistID','RemixerID','ComposerID')),
                          ('djmdAlbum',('AlbumArtistID',))):
        for column in columns:
            if not wanted:continue
            rows=conn.execute('SELECT '+_quote(column)+',COUNT(*) FROM '+_quote(table)
                +' WHERE '+_quote(column)+' IN ('+','.join('?' for _ in wanted)+') GROUP BY '+_quote(column),tuple(sorted(wanted)))
            for identity,count in rows:references[identity]=references.get(identity,0)+count
    return dict(target=target,counter=counter,artists=actors,references=references)


def _plan(snapshot,request,*,profile=PROFILE):
    if type(profile) is not str or profile not in planner.PROFILES:
        raise WriteProofError('Unreferenced native metadata profile')
    validate_request(request);target=snapshot['target']
    if (not target or type(target.get('rb_local_deleted')) is not int or target['rb_local_deleted']!=0
            or target['UUID']!=request['track_uuid'] or target['FolderPath']!=request['path']
            or _value(snapshot,request['field'])!=request['expected']):
        raise ConflictError('Native metadata identity/path/value changed; refresh before saving')
    if request['field']=='artist':
        _require_artist_identity(snapshot)
        if any(request['artist_pins'].get(actor['ID'])!=actor['UUID'] for actor in snapshot['artists']):
            raise ConflictError('Native artist identities changed after metadata request capture')
    # Exact already-present values require no publisher/profile extrapolation.
    # In particular, do not normalize NULL artist/album IDs during a no-op.
    if request['value']==request['expected']:return [],deepcopy(snapshot)
    if request['field']=='artist':
        if request['artist_seed'] and snapshot['references'].get(request['artist_seed']['ID']):
            raise ConflictError('The new artist identity already has native references')
        matches=[row for row in snapshot['artists'] if row['Name']==request['value']]
        for actor in matches:
            if (any(type(actor.get(key)) is not int or actor[key]!=0 for key in
                    ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced'))
                    or actor.get('usn') is not None or type(actor.get('rb_local_usn')) is not int
                    or not 0<=actor['rb_local_usn']<=snapshot['counter']['int_1']):
                raise WriteProofError('Reusing this artist synchronization profile is not proven')
    proposed=planner.construct(target,snapshot['counter'],snapshot['artists'],snapshot['references'],
        field=request['field'],value=request['value'],timestamp=request['timestamp'],artist_seed=request['artist_seed'],
        profile=profile)
    changes=[]
    if proposed['artist_created']:
        actor=proposed['artist_created'];changes.append(dict(table='djmdArtist',key=actor['ID'],before=None,after=actor))
    if proposed['artist_removed']:
        actor=proposed['artist_removed'];changes.append(dict(table='djmdArtist',key=actor['ID'],before=actor,after=None))
    if proposed['before']!=proposed['after']:
        changes.append(dict(table='djmdContent',key=target['ID'],before=target,after=proposed['after']))
        changes.append(dict(table='agentRegistry',key='localUpdateCount',before=proposed['counter_before'],after=proposed['counter_after']))
    after=deepcopy(snapshot);after['target']=proposed['after'];after['counter']=proposed['counter_after']
    if proposed['artist_created']:after['artists'].append(proposed['artist_created'])
    if proposed['artist_removed']:after['artists']=[row for row in after['artists'] if row['ID']!=proposed['artist_removed']['ID']]
    after['artists'].sort(key=lambda row:row['ID'])
    old,new=target['ArtistID'],after['target']['ArtistID']
    if old!=new:
        if old:
            after['references'][old]-=1
            if not after['references'][old]:after['references'].pop(old)
        if new:after['references'][new]=after['references'].get(new,0)+1
    return changes,after


def _require_artist_identity(snapshot):
    actors=snapshot['artists'];uuids=[row['UUID'] for row in actors]
    if len(uuids)!=len(set(uuids)):
        raise ConflictError('Native artist UUIDs are not unique')
    identity=snapshot['target']['ArtistID']
    if identity:
        actor=next((row for row in actors if row['ID']==identity),None)
        if not actor or type(actor.get('rb_local_deleted')) is not int or actor['rb_local_deleted']!=0:
            raise ConflictError('The selected native artist is inactive or unavailable')


def _authorizer(action,table,column,database,trigger,*,field=None):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main':return sqlite3.SQLITE_DENY
        columns=({'Title','TrackInfoUpdated','rb_local_usn','updated_at'} if field=='title' else
                 _FIELDS-{'Title'} if field=='artist' else frozenset())
        if table=='djmdContent' and action==sqlite3.SQLITE_UPDATE and column in columns:return sqlite3.SQLITE_OK
        if table=='djmdArtist' and field=='artist' and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_DELETE):return sqlite3.SQLITE_OK
        if table=='agentRegistry' and action==sqlite3.SQLITE_UPDATE and column=='int_1':return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY


def _apply(conn,changes,field):
    conn.set_authorizer(lambda *args:_authorizer(*args,field=field))
    try:
        for change in changes:
            table,key=change['table'],change['key'];before,after=change['before'],change['after']
            if _one(conn,table,key)!=before:raise ConflictError('Metadata row changed before its exact save')
            if before is None:
                fields=list(after)
                conn.execute('INSERT INTO '+_quote(table)+' ('+','.join(map(_quote,fields))+') VALUES ('
                    +','.join('?' for _ in fields)+')',tuple(after[field] for field in fields))
            elif after is None:conn.execute('DELETE FROM '+_quote(table)+' WHERE ID=?',(key,))
            else:
                fields=[field for field in after if after[field]!=before[field]]
                pk='registry_id' if table=='agentRegistry' else 'ID'
                conn.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(field)+'=?' for field in fields)
                    +' WHERE '+_quote(pk)+'=?',(*[after[field] for field in fields],key))
    finally:conn.set_authorizer(None)


def _fault(_phase):pass


def validate_retirement(entry):
    if (not isinstance(entry,dict) or set(entry)!={'source','library_id','request','proof_sha256','marker_sha256'}
            or type(entry['source']) is not str or not Path(entry['source']).is_absolute()
            or type(entry['proof_sha256']) is not str or len(entry['proof_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in entry['proof_sha256'])
            or type(entry['marker_sha256']) is not str or len(entry['marker_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in entry['marker_sha256'])):
        raise RecoveryRequired('Invalid metadata receipt retirement intention')
    request=validate_request(entry['request'])
    if entry['library_id']!=request['library_id']:raise RecoveryRequired('Metadata retirement library changed')
    return entry


def _compact_marker(record,entry):
    request=entry['request']
    if (record.get('format')!=FORMAT or record.get('source')!=entry['source']
            or record.get('library_id')!=entry['library_id'] or record.get('request')!=request
            or type(record.get('profile')) is not str or record['profile'] not in planner.PROFILES
            or record.get('outcome')!='committed'):
        raise RecoveryRequired('Metadata retirement differs from its committed proof')
    changes,after=_plan(record['before'],request,profile=record['profile'])
    if changes!=record['changes'] or after!=record['after']:
        raise RecoveryRequired('Metadata retirement semantic proof changed')
    target=after['target'];actor=next((row for row in after['artists'] if row['ID']==target['ArtistID']),None)
    return dict(format=ACK_FORMAT,source=record['source'],library_id=record['library_id'],
        schema_sha256=record['schema_sha256'],application_version=record['application_version'],
        source_file_id=record['source_file_id'],profile=record['profile'],operation_id=request['operation_id'],
        request_sha256=_digest(request),track_id=request['track_id'],track_uuid=request['track_uuid'],
        path=request['path'],field=request['field'],value=request['value'],
        artist_id=target['ArtistID'] if request['field']=='artist' else None,
        artist_uuid=actor['UUID'] if actor else None,counter_floor=after['counter']['int_1'],
        proof_sha256=entry['proof_sha256'])


def retire_acknowledged_receipt(data_root,entry):
    """Pure private receipt I/O after a durable GUI ACK; never source access."""
    validate_retirement(entry);request=entry['request']
    root=shared_root(Path(data_root).absolute(),entry['source'],entry['library_id'])
    folder=root/'metadata-receipts';path=folder/(request['operation_id']+'.json')
    if folder.is_symlink() or folder!=folder.resolve():raise RecoveryRequired('Metadata receipts redirect')
    _regular(path);record=_read_json(path)
    if record.get('format')==ACK_FORMAT:
        if (_digest(record)!=entry['marker_sha256'] or record.get('proof_sha256')!=entry['proof_sha256']
                or record.get('request_sha256')!=_digest(request)
                or record.get('source')!=entry['source'] or record.get('library_id')!=entry['library_id']):
            raise RecoveryRequired('Compacted metadata receipt differs from its durable ACK')
        return
    if _file_sha(path)!=entry['proof_sha256']:raise RecoveryRequired('Acknowledged metadata proof changed before retirement')
    marker=_compact_marker(record,entry)
    if _digest(marker)!=entry['marker_sha256']:raise RecoveryRequired('Metadata retirement marker differs from its durable fingerprint')
    atomic_bytes(path,_json(marker));sync_dir(folder)


class _Lease(PilotCueStore):
    def __init__(self,store):
        self.store=store;a=store.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,'metadata-transaction',a.application_version),store.root)
    def _guard(self):
        self.store._accepted();super()._guard()
        info=self.database.stat()
        if (info.st_dev,info.st_ino)!=self.store.file_id:raise ConflictError('Metadata source file was replaced')
        self.store._xml_sha()
    def _record(self):return self.store._record()
    def apply(self,_edit):raise WriteProofError('Metadata infrastructure has no cue-write authority')
    def snapshot(self):raise WriteProofError('Metadata infrastructure has no cue-read API')


class SourceMetadataStore:
    def __init__(self,authority,data_root):
        if type(authority) is not MetadataAuthority:raise WriteProofError('Metadata requires its own accepted authority')
        self.authority=authority;self.database=Path(authority.database)
        self.root=shared_root(Path(data_root).absolute(),self.database,authority.library_id)
        self.xml=self.database.with_name('masterPlaylists6.xml');self.journal=self.root/'transaction.json'
        info=self.database.stat();self.file_id=info.st_dev,info.st_ino;self.lease=_Lease(self)
        self._verified_before=None

    def _accepted(self):
        if not self.authority.accepted or self.authority.profile not in planner.PROFILES:
            raise WriteProofError('Native metadata requires separate exact-profile/library acceptance')

    def _scope(self,request):
        self._accepted();validate_request(request)
        if (request['library_id']!=self.authority.library_id or self.authority.pilot_operation_ids
                and request['operation_id'] not in self.authority.pilot_operation_ids):
            raise WriteProofError('Metadata request exceeds its exact library/pilot authority')

    def _xml_sha(self):
        _regular(self.xml)
        if self.xml!=self.xml.resolve() or self.xml.stat().st_uid!=os.getuid():raise WriteProofError('Metadata XML redirects')
        return _file_sha(self.xml)

    def _record(self):
        try:
            record=_read_json(self.journal);self._scope(record['request'])
            if (record['format']!=FORMAT or record['source']!=str(self.database)
                    or record['library_id']!=self.authority.library_id or record['profile']!=self.authority.profile
                    or record['schema_sha256']!=self.authority.schema_sha256
                    or record['source_file_id']!=list(self.file_id) or record['application_version']!=self.authority.application_version):
                raise RecoveryRequired('Metadata recovery belongs to another source/profile')
            changes,after=_plan(record['before'],record['request'],profile=record['profile'])
            if changes!=record['changes'] or after!=record['after']:
                raise RecoveryRequired('Metadata recovery exceeds its captured semantic transition')
            self.lease._verify_backup(record['baseline_sha256'])
            key=record['baseline_sha256'],_digest(record)
            if key!=self._verified_before:
                with tempfile.TemporaryDirectory(prefix='soundbox-metadata-before-') as area:
                    path=Path(area).resolve()/'baseline.db';copy_state(self.root/'baseline.db',path)
                    if _file_sha(path)!=record['baseline_sha256']:raise RecoveryRequired('Metadata baseline changed')
                    conn=_connect(path,readonly=True)
                    try:
                        if (_identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256
                                or _snapshot(conn,record['request'])!=record['before']):
                            raise RecoveryRequired('Metadata semantic BEFORE differs from verified encrypted bytes')
                    finally:conn.close()
                self._verified_before=key
            return record
        except (OSError,ValueError,TypeError,KeyError,AttributeError,WriteProofError) as exc:
            raise RecoveryRequired('Metadata recovery is invalid; preserve its evidence') from exc

    def _receipt_path(self,operation):
        if not _operation(operation):raise WriteProofError('Invalid metadata receipt identity')
        folder=self.root/'metadata-receipts'
        if folder.is_symlink() or folder!=folder.resolve():raise RecoveryRequired('Metadata receipts redirect')
        folder.mkdir(mode=0o700,exist_ok=True)
        if folder.stat().st_mode&0o077:raise RecoveryRequired('Metadata receipts are not private')
        path=folder/(operation+'.json')
        if path.exists() or path.is_symlink():_regular(path)
        return path

    def retirement_entry(self,request):
        """Return a proof fingerprint for the controller's next atomic ACK."""
        self._scope(request);path=self._receipt_path(request['operation_id'])
        if not path.exists():return None
        record=_read_json(path)
        proof=record.get('proof_sha256') if record.get('format')==ACK_FORMAT else _file_sha(path)
        entry=dict(source=str(self.database),library_id=self.authority.library_id,
                   request=deepcopy(request),proof_sha256=proof,marker_sha256='0'*64)
        entry['marker_sha256']=_digest(record if record.get('format')==ACK_FORMAT else _compact_marker(record,entry))
        validate_retirement(entry)
        if record.get('format')!=ACK_FORMAT:_compact_marker(record,entry)
        return entry

    def _acknowledged(self,conn,marker,request):
        expected={'format','source','library_id','schema_sha256','application_version','source_file_id','profile',
            'operation_id','request_sha256','track_id','track_uuid','path','field','value','artist_id','artist_uuid',
            'counter_floor','proof_sha256'}
        if (set(marker)!=expected or marker['format']!=ACK_FORMAT or marker['source']!=str(self.database)
                or marker['library_id']!=self.authority.library_id or marker['schema_sha256']!=self.authority.schema_sha256
                or marker['application_version']!=self.authority.application_version or marker['source_file_id']!=list(self.file_id)
                or marker['profile']!=self.authority.profile or marker['request_sha256']!=_digest(request)
                or any(marker[key]!=request[key] for key in ('operation_id','track_id','track_uuid','path','field','value'))
                or type(marker['counter_floor']) is not int or marker['counter_floor']<0
                or type(marker['proof_sha256']) is not str or len(marker['proof_sha256'])!=64
                or any(c not in '0123456789abcdef' for c in marker['proof_sha256'])):
            raise RecoveryRequired('Acknowledged metadata marker differs from its request/authority')
        current=_snapshot(conn,request);target=current['target']
        if (not target or target['UUID']!=request['track_uuid'] or target['FolderPath']!=request['path']
                or type(target.get('rb_local_deleted')) is not int or target['rb_local_deleted']!=0
                or _value(current,request['field'])!=request['value'] or current['counter']['int_1']<marker['counter_floor']):
            raise RecoveryRequired('Acknowledged native metadata changed; preserve its marker')
        if request['field']=='artist':
            _require_artist_identity(current)
            actor=next((row for row in current['artists'] if row['ID']==target['ArtistID']),None)
            if (target['ArtistID']!=marker['artist_id'] or (actor['UUID'] if actor else None)!=marker['artist_uuid']):
                raise RecoveryRequired('Acknowledged native artist identity changed')
        elif marker['artist_id'] is not None or marker['artist_uuid'] is not None:
            raise RecoveryRequired('Title marker exceeds its artist scope')
        self.lease._guard();return target

    def _recover(self,conn):
        if not self.journal.exists():return None
        record=self._record();conn.execute('BEGIN IMMEDIATE')
        try:
            self.lease._guard();current=_snapshot(conn,record['request'])
            if self._xml_sha()!=record['xml_sha256']:raise RecoveryRequired('Metadata XML changed during recovery')
            if current==record['after']:outcome='committed'
            elif current==record['before']:outcome='rolled-back'
            else:raise RecoveryRequired('Unexpected metadata state; exact recovery evidence retained')
            receipt={**record,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
            if outcome=='committed':atomic_bytes(self._receipt_path(record['request']['operation_id']),_json(receipt))
            atomic_bytes(self.root/'last-result.json',_json(receipt));self.journal.unlink();sync_dir(self.root)
            conn.commit();return outcome
        except BaseException:conn.rollback();raise

    def recover(self):
        self._accepted()
        with self.lease._lock():
            conn=self.lease._connect()
            try:return self._recover(conn)
            finally:conn.close()

    def capture_request(self,track_id,track_uuid,path,field,value,expected=None,*,operation_id=None):
        """Capture native identities/clock seeds, without publishing the change.

        The caller must persist this exact request before calling apply. A GUI
        gesture can be durable already while this worker phase obtains the
        independently guarded native artist inventory.
        """
        self._accepted()
        with self.lease._lock():
            conn=self.lease._connect()
            try:
                self._recover(conn);conn.execute('BEGIN')
                request=request_for(self.authority.library_id,track_id,track_uuid,path,field,value,expected or '',
                                    operation_id=operation_id)
                observed=_snapshot(conn,request)
                if observed['target'] is None:raise ConflictError('The native metadata track is missing')
                if expected is None:request['expected']=_value(observed,field)
                if field=='artist':request['artist_pins']={row['ID']:row['UUID'] for row in observed['artists']}
                if field=='artist' and value and not any(row['Name']==value for row in observed['artists']):
                    seed=new_artist_seed();request['artist_pins'][seed['ID']]=seed['UUID']
                    request=request_for(self.authority.library_id,track_id,track_uuid,path,field,value,request['expected'],
                        artist_seed=seed,operation_id=request['operation_id'],artist_pins=request['artist_pins'])
                    observed=_snapshot(conn,request)
                self._scope(request);_plan(observed,request,profile=self.authority.profile);self.lease._guard()
                return request
            finally:conn.close()

    def retry_request(self,request):
        """Reconcile before refreshing an uncommitted explicit intent's CAS."""
        self._scope(request)
        with self.lease._lock():
            conn=self.lease._connect()
            try:
                self._recover(conn)
                path=self._receipt_path(request['operation_id'])
                if path.exists():
                    marker=_read_json(path)
                    if marker.get('format')==ACK_FORMAT:self._acknowledged(conn,marker,request)
                    return deepcopy(request)
            finally:conn.close()
        return self.capture_request(request['track_id'],request['track_uuid'],request['path'],
            request['field'],request['value'],operation_id=request['operation_id'])

    def apply(self,request):
        request=deepcopy(request);self._scope(request)
        with self.lease._lock():
            conn=self.lease._connect()
            try:
                self._recover(conn);path=self._receipt_path(request['operation_id'])
                if path.exists():
                    receipt=_read_json(path)
                    if receipt.get('format')==ACK_FORMAT:return self._acknowledged(conn,receipt,request)
                    if (receipt.get('format')!=FORMAT or receipt.get('source')!=str(self.database)
                            or receipt.get('source_file_id')!=list(self.file_id) or receipt.get('request')!=request
                            or receipt.get('outcome')!='committed' or receipt.get('profile')!=self.authority.profile
                            or receipt.get('library_id')!=self.authority.library_id
                            or receipt.get('application_version')!=self.authority.application_version
                            or receipt.get('schema_sha256')!=self.authority.schema_sha256):
                        raise RecoveryRequired('Metadata receipt differs from its captured authority/request')
                    changes,after=_plan(receipt['before'],request,profile=receipt['profile'])
                    if changes!=receipt['changes'] or after!=receipt['after']:
                        raise RecoveryRequired('Metadata receipt exceeds its exact semantic transition')
                    current=_snapshot(conn,request);target=current['target']
                    if (not target or target['UUID']!=request['track_uuid'] or target['FolderPath']!=request['path']
                            or target['rb_local_deleted']!=0 or _value(current,request['field'])!=request['value']
                            or current['counter']['int_1']<receipt['after']['counter']['int_1']):
                        raise RecoveryRequired('Previously saved native metadata changed; preserve its receipt')
                    if request['field']=='artist' and target['ArtistID']:
                        _require_artist_identity(current)
                        expected=next((row for row in after['artists'] if row['ID']==after['target']['ArtistID']),None)
                        actor=next((row for row in current['artists'] if row['ID']==target['ArtistID']),None)
                        if (target['ArtistID']!=after['target']['ArtistID'] or not expected or not actor
                                or actor['UUID']!=expected['UUID']):
                            raise RecoveryRequired('The saved native artist identity was replaced')
                    self.lease._guard();return target
                conn.execute('BEGIN IMMEDIATE');self.lease._guard()
                before=_snapshot(conn,request);changes,after=_plan(before,request,profile=self.authority.profile)
                if not changes:conn.rollback();return after['target']
                self.lease._last_counter=None;self.lease._backup(before['counter']['int_1'])
                record=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,
                    schema_sha256=self.authority.schema_sha256,application_version=self.authority.application_version,
                    source_file_id=list(self.file_id),profile=self.authority.profile,request=request,before=before,after=after,
                    changes=changes,baseline_sha256=self.lease._backup_sha,xml_sha256=self._xml_sha())
                payload=_json(record)
                if len(payload)>_MAX_RECORD:raise WriteProofError('Metadata proof exceeds its bounded record size')
                atomic_bytes(self.journal,payload);_fault('journal-ready');self.lease._guard()
                _apply(conn,changes,request['field'])
                if _snapshot(conn,request)!=after:raise WriteProofError('Native metadata after-readback differs')
                if self._xml_sha()!=record['xml_sha256']:raise ConflictError('Metadata XML changed before commit')
                self.lease._guard();_fault('before-commit');conn.commit();_fault('after-commit')
                conn.close();conn=None;conn=self.lease._connect()
                if self._recover(conn)!='committed':raise RecoveryRequired('Metadata save could not be independently verified')
                return deepcopy(after['target'])
            except BaseException:
                if conn is not None:conn.rollback()
                raise
            finally:
                if conn is not None:conn.close()
