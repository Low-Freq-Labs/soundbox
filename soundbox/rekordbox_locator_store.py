"""Identity-preserving locator/type updates for verified converted desktop audio.

This is a separate write domain. Registration's INSERT permission is never used.
The explicit authority binds either named tracks or an accepted native profile.
Encrypted pairs, a SQL column allowlist and the shared source lease protect saves.
"""
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import UUID,uuid4

from .analysis import identity
from .cdj_compatibility import inspect
from .native_registration_plan import encoded_row
from .rekordbox_analysis_store import shared_root
from .rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from .rekordbox_tags import NATIVE_VERSION,_schema
from .rekordbox_write import (ConflictError,RecoveryRequired,WriteProofError,_connect,
    _identity,_inactive_journal_proof,_integrity,_json,_quote,_regular,_state,encrypted_backup)
from .state_io import atomic_bytes,copy_state,sync_dir
from .rekordbox_playlist_store import _sha

FORMAT='soundbox-native-media-locator-v1'
AUTHORITY_FORMAT='soundbox-media-locator-authority-v1'
FIELDS=frozenset(('FolderPath','FileNameL','FileType','rb_file_id','rb_local_usn','updated_at'))
PAYLOADS=('before.db','after.db','before.xml','after.xml')


@dataclass(frozen=True)
class LocatorAuthority:
    database:str
    library_id:str
    schema_sha256:str
    authorization_sha256:str
    track_ids:tuple[str,...]=()
    native_reference_sha256:str=''
    application_version:str=NATIVE_VERSION

    def __post_init__(self):
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'media-locator',self.application_version)
        digests=(self.authorization_sha256,)+( (self.native_reference_sha256,) if self.native_reference_sha256 else ())
        if (any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in digests)
                or not isinstance(self.track_ids,tuple) or len(set(self.track_ids))!=len(self.track_ids)
                or any(not v.isdecimal() for v in self.track_ids)
                or not self.track_ids and not self.native_reference_sha256):
            raise WriteProofError('Locator updates require explicit track scope or native acceptance')


def load_authority(path,source,library_id):
    try:
        _regular(Path(path));body=json.loads(Path(path).read_bytes())
        if body.pop('format',None)!=AUTHORITY_FORMAT:return None
        body['track_ids']=tuple(body.get('track_ids',()))
        result=LocatorAuthority(**body)
        return result if (result.database,result.library_id)==(str(source),library_id) else None
    except (OSError,ValueError,TypeError,WriteProofError):return None


def _authorizer(action,table,column,database,trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        return (sqlite3.SQLITE_OK if action==sqlite3.SQLITE_UPDATE and database=='main' and not trigger
                and (table=='djmdContent' and column in FIELDS or table=='agentRegistry' and column=='int_1')
                else sqlite3.SQLITE_DENY)
    return (sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY)


def _matches(conn,changes,side):
    return all(encoded_row(conn,c['table'],c['id'])==c[side] for c in changes)


def _apply(conn,changes):
    conn.set_authorizer(_authorizer)
    try:
        for c in changes:
            if encoded_row(conn,c['table'],c['id'])!=c['before']:raise ConflictError('Locator source row changed')
            fields={k:v for k,v in c['after'].items() if c['before'][k]!=v}
            if c['table']=='djmdContent':
                if not fields.keys()<=FIELDS:raise WriteProofError('Locator update exceeds its column scope')
                key='ID'
            elif c['table']=='agentRegistry' and c['id']=='localUpdateCount' and fields.keys()=={'int_1'}:
                key='registry_id'
            else:raise WriteProofError('Locator update exceeds its table scope')
            if conn.execute('UPDATE '+_quote(c['table'])+' SET '+','.join(_quote(k)+'=?' for k in fields)
                            +' WHERE '+_quote(key)+'=?',(*fields.values(),c['id'])).rowcount!=1:
                raise ConflictError('Locator target disappeared')
    finally:conn.set_authorizer(None)


def _fault(_phase):pass


class _Lease(PilotCueStore):
    def __init__(self,owner):
        self.owner=owner;a=owner.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,'media-locator',a.application_version),owner.root)
    def _record(self):return self.owner._record()
    def _guard(self):
        super()._guard();s=self.database.stat()
        if [s.st_dev,s.st_ino]!=self.owner.file_id:raise ConflictError('Locator database was replaced')
        _regular(self.owner.xml)
        if self.owner.xml.resolve()!=self.owner.xml:raise WriteProofError('Locator XML redirects')


class LocatorStore:
    def __init__(self,authority,data_root):
        if not isinstance(authority,LocatorAuthority):raise WriteProofError('Separate locator authority required')
        self.authority=authority;self.database=Path(authority.database);self.data_root=Path(data_root).absolute()
        self.root=shared_root(self.data_root,self.database,authority.library_id)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.xml=self.database.with_name('masterPlaylists6.xml');self.journal=self.root/'transaction.json'
        s=self.database.stat();self.file_id=[s.st_dev,s.st_ino];self.lease=_Lease(self)

    def _media(self,tracks):
        for t in tracks:
            for field,digest in (('target','after_sha256'),('original','before_sha256')):
                path=Path(t[field]);_regular(path)
                if (path.resolve()!=path or path.is_relative_to('/Volumes') or path.is_relative_to(self.database.parent)
                        or path.stat().st_uid!=os.getuid() or _sha(path)!=t[digest]):
                    raise ConflictError('Converted audio or its original backup changed')
            path=Path(t['target'])
            if (path.is_relative_to(self.data_root) or path.suffix.lower()!='.wav' or inspect(path)['status']!='compatible'
                    or identity(path.stat())!=t['epoch']):raise ConflictError('Converted WAVE identity changed')

    def _plan(self,conn,tracks,timestamp):
        a=self.authority
        if _identity(conn)!=(a.library_id,'6000') or _schema(conn)!=a.schema_sha256:
            raise ConflictError('Locator library/schema changed')
        if not tracks or len({t['id'] for t in tracks})!=len(tracks):raise WriteProofError('Ambiguous locator targets')
        counter=encoded_row(conn,'agentRegistry','localUpdateCount');clock=counter['int_1'];changes=[]
        for t in tracks:
            if a.track_ids and t['id'] not in a.track_ids:raise WriteProofError('Track exceeds explicit locator scope')
            row=encoded_row(conn,'djmdContent',t['id'])
            if (not row or row['UUID']!=t['uuid'] or row['FolderPath']!=t['before_path']
                    or row['rb_local_deleted']!=0 or row['usn'] is not None or row['rb_local_synced'] not in (0,1)):
                raise ConflictError('Locator track identity or source state changed')
            occupied=conn.execute('SELECT ID FROM djmdContent WHERE FolderPath=? AND rb_local_deleted=0',(t['target'],)).fetchall()
            if any(v[0] not in {x['id'] for x in tracks} for v in occupied):raise ConflictError('Replacement path belongs to another track')
            target=Path(t['target']);after={**row,'FolderPath':str(target),'FileNameL':target.name,
                'FileType':11,'rb_file_id':str(t['epoch'][1])}
            # analysis.identity is device/inode/size/mtime/ctime; retain exact inode.
            if after==row:continue
            clock+=1;after.update(rb_local_usn=clock,updated_at=timestamp)
            changes.append(dict(table='djmdContent',id=t['id'],before=row,after=after))
        if changes:changes.append(dict(table='agentRegistry',id='localUpdateCount',before=counter,after={**counter,'int_1':clock}))
        return changes

    def prepare(self,tracks,*,operation_id=None):
        """Copies and validates only. Call apply() to publish the explicit intent."""
        with self.lease._lock():
            self._recover_locked();self.lease._guard()
            op=operation_id or str(uuid4())
            if str(UUID(op))!=op:raise WriteProofError('Invalid locator operation')
            tracks=json.loads(_json(tracks))
            for t in tracks:t['epoch']=identity(Path(t['target']).stat())
            self._media(tracks)
            folder=self.root/('media-locator-'+op);folder.mkdir(mode=0o700)
            xml=self.xml.read_bytes();timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
            with encrypted_backup(self.database) as snapshot:
                before=_connect(snapshot,readonly=True)
                try:changes=self._plan(before,tracks,timestamp);before_state=_state(before)
                finally:before.close()
                copy_state(snapshot,folder/'before.db')
                with tempfile.TemporaryDirectory(prefix='soundbox-locator-') as tmp:
                    stage=Path(tmp).resolve()/'after.db';shutil.copyfile(snapshot,stage);conn=_connect(stage)
                    try:
                        conn.execute('BEGIN IMMEDIATE');_apply(conn,changes);conn.commit();_integrity(conn);after_state=_state(conn)
                        if not _matches(conn,changes,'after'):raise WriteProofError('Locator staging readback differs')
                    finally:conn.close()
                    if (before_state['schema']!=after_state['schema'] or before_state['metadata']!=after_state['metadata']
                            or any(before_state['tables'][k]!=after_state['tables'][k] for k in before_state['tables']
                                   if k not in ('djmdContent','agentRegistry'))):raise WriteProofError('Locator staging changed unrelated data')
                    copy_state(stage,folder/'after.db')
            atomic_bytes(folder/'before.xml',xml);atomic_bytes(folder/'after.xml',xml)
            record=dict(format=FORMAT,authority=asdict(self.authority),source=str(self.database),library_id=self.authority.library_id,
                source_file_id=self.file_id,archive=folder.name,tracks=tracks,timestamp=timestamp,changes=changes,
                before=before_state,after=after_state,payload_sha256={n:_sha(folder/n) for n in PAYLOADS})
            # JSON-normalize tuple fields before comparisons across restart.
            record=json.loads(_json(record));atomic_bytes(folder/'operation.json',_json(record));return record

    def _validate(self,record):
        if (record.get('format')!=FORMAT or record.get('authority')!=json.loads(_json(asdict(self.authority)))
                or record.get('source')!=str(self.database) or record.get('library_id')!=self.authority.library_id
                or record.get('source_file_id')!=self.file_id):raise RecoveryRequired('Locator recovery identity changed')
        folder=self.root/record['archive']
        if (folder.parent!=self.root or folder.resolve()!=folder or not folder.name.startswith('media-locator-')
                or folder.stat().st_mode&0o077):raise RecoveryRequired('Locator archive redirects')
        _regular(folder/'operation.json')
        if json.loads((folder/'operation.json').read_bytes())!=record:raise RecoveryRequired('Locator intention changed')
        for n in PAYLOADS:
            _regular(folder/n)
            if _sha(folder/n)!=record['payload_sha256'][n]:raise RecoveryRequired('Locator backup changed')
        if record['payload_sha256']['before.xml']!=record['payload_sha256']['after.xml']:raise RecoveryRequired('Locator cannot change XML')
        with tempfile.TemporaryDirectory(prefix='soundbox-locator-verify-') as tmp:
            for name,side in (('before.db','before'),('after.db','after')):
                path=Path(tmp).resolve()/name;shutil.copyfile(folder/name,path);conn=_connect(path,readonly=True)
                try:
                    if _state(conn)!=record[side] or not _matches(conn,record['changes'],side):raise RecoveryRequired('Locator paired state differs')
                    if side=='before' and self._plan(conn,record['tracks'],record['timestamp'])!=record['changes']:
                        raise RecoveryRequired('Locator recovery exceeds its original intent')
                finally:conn.close()
        return record

    def _record(self):
        _regular(self.journal);return self._validate(json.loads(self.journal.read_bytes()))

    def _recover_locked(self):
        if not self.journal.exists():return None
        r=self._record();self._media(r['tracks'])
        if _sha(self.xml)!=r['payload_sha256']['before.xml']:raise RecoveryRequired('Locator XML changed')
        conn=self.lease._connect()
        try:
            conn.execute('BEGIN IMMEDIATE');self.lease._guard()
            if _matches(conn,r['changes'],'after'):outcome='committed'
            elif _matches(conn,r['changes'],'before'):outcome='rolled-back'
            else:raise RecoveryRequired('Locator is outside its exact recovery pair')
            conn.rollback()
        finally:conn.close()
        receipt={**r,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
        atomic_bytes(self.root/r['archive']/'result.json',_json(receipt));atomic_bytes(self.root/'last-result.json',_json(receipt))
        _fault('receipt');self.journal.unlink();sync_dir(self.root);return outcome

    def recover(self):
        with self.lease._lock():return self._recover_locked()

    def relocate_conversion(self,receipt):
        """Finish a durable conversion without another user-facing Relocate step."""
        from uuid import uuid5
        target=receipt.get('target',receipt['path'])
        if target==receipt['path'] or not receipt.get('native'):return receipt
        op=uuid5(UUID(receipt['operation_id']),'native-locator');index=0
        # A rollback is a valid recovery outcome. Renew its immutable intention,
        # while a completed save replays without undoing newer musical edits.
        while True:
            folder=self.root/('media-locator-'+str(op))
            if not folder.exists():break
            record=json.loads((folder/'operation.json').read_bytes());self._validate(record)
            result=folder/'result.json'
            if result.exists() and json.loads(result.read_bytes()).get('outcome')=='rolled-back':
                index+=1;op=uuid5(UUID(receipt['operation_id']),'native-locator-'+str(index));continue
            self.apply(record)
            with encrypted_backup(self.database) as snapshot:
                conn=_connect(snapshot,readonly=True)
                try:
                    for ref in receipt['native']:
                        row=encoded_row(conn,'djmdContent',ref['id'])
                        if not row or row['UUID']!=ref['uuid'] or row['FolderPath']!=target or row['FileType']!=11:
                            raise ConflictError('Completed conversion was independently relocated')
                finally:conn.close()
            return receipt
        tracks=[dict(id=ref['id'],uuid=ref['uuid'],before_path=receipt['path'],target=target,
                     original=receipt['original'],before_sha256=receipt['before_sha256'],after_sha256=receipt['after_sha256'])
                for ref in receipt['native']]
        record=self.prepare(tracks,operation_id=str(op));self.apply(record);return receipt

    def apply(self,record):
        record=json.loads(_json(record))
        with self.lease._lock():
            self._recover_locked();self._validate(record);self._media(record['tracks'])
            result=self.root/record['archive']/'result.json'
            if result.exists():
                _regular(result);r=json.loads(result.read_bytes())
                if {k:r.get(k) for k in record}!=record:raise RecoveryRequired('Locator receipt differs')
                if r.get('outcome')=='committed':return 'committed'
                raise ConflictError('Renew a rolled-back locator intention')
            conn=self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE');self.lease._guard()
                if not _matches(conn,record['changes'],'before') or _sha(self.xml)!=record['payload_sha256']['before.xml']:
                    raise ConflictError('Locator source changed before save')
                atomic_bytes(self.journal,_json(record));_fault('journal')
                _apply(conn,record['changes'])
                if not _matches(conn,record['changes'],'after'):raise WriteProofError('Locator source readback differs')
                _fault('sql');self.lease._guard();self._media(record['tracks'])
                if _sha(self.xml)!=record['payload_sha256']['before.xml']:raise ConflictError('Locator XML changed before commit')
                conn.commit();_fault('committed')
            except BaseException:conn.rollback();raise
            finally:conn.close()
            return self._recover_locked()
