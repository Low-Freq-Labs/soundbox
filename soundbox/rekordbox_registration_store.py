"""Accepted bare registrations with encrypted evidence and shared source ownership.

This domain inserts new content rows and advances only the local native counter.
Exact-path reuse never edits an existing song. Audio, cues, analysis, playlists
and XML remain immutable. A separate explicit library/version authority is
required; Cue transport supplies guards/locking, never registration permission.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from . import native_registration_plan as planner
from .native_media import MediaIdentity
from .rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from .rekordbox_tags import NATIVE_VERSION,_schema
from .rekordbox_write import (ConflictError,RecoveryRequired,WriteProofError,_connect,_digest,_identity,
    _inactive_journal_proof,_integrity,_json,_quote,_regular,encrypted_backup)
from .rekordbox_playlist_store import _sha
from .state_io import atomic_bytes,copy_state,sync_dir

FORMAT='soundbox-native-registration-v1'
AUTHORITY_FORMAT='soundbox-registration-authority-v1'
PAYLOADS=('before.db','after.db','before.xml','after.xml')


@dataclass(frozen=True)
class RegistrationAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted_reference_sha256: str
    accepted: bool
    application_version: str = NATIVE_VERSION
    file_types: tuple[int,...] = (1,4,5,6,11,12)
    retain_full_backups: bool = False

    def __post_init__(self):
        if self.accepted is not True:raise WriteProofError('Registration requires separate native acceptance')
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'registration-authority',self.application_version)
        if (not isinstance(self.accepted_reference_sha256,str) or len(self.accepted_reference_sha256)!=64
                or any(c not in '0123456789abcdef' for c in self.accepted_reference_sha256)
                or not isinstance(self.file_types,tuple) or not self.file_types
                or any(type(v) is not int or v not in (1,4,5,6,11,12) for v in self.file_types)
                or tuple(sorted(set(self.file_types)))!=self.file_types or type(self.retain_full_backups) is not bool):
            raise WriteProofError('Unknown registration authority profile')


def load_authority(path,source,library_id):
    try:
        path=Path(path);_regular(path);data=json.loads(path.read_bytes())
        if data.pop('format',None)!=AUTHORITY_FORMAT:return None
        data['file_types']=tuple(data.get('file_types',()))
        authority=RegistrationAuthority(**data)
        return authority if authority.database==str(source) and authority.library_id==library_id else None
    except (OSError,TypeError,ValueError,KeyError,WriteProofError):return None


def _authorizer(action,table,column,database,trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main':return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_INSERT and table=='djmdContent':return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE and table=='agentRegistry' and column=='int_1':return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_FUNCTION,
                  sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _affected(changes):
    first: dict[tuple[str,str],dict|None]={}
    final: dict[tuple[str,str],dict|None]={}
    for c in changes:
        key=(c['table'],c['id']);first.setdefault(key,c['before']);final[key]=c['after']
    return first,final


def _matches(conn,rows):
    return all(planner.encoded_row(conn,table,key)==row for (table,key),row in rows.items())


class _Lease(PilotCueStore):
    def __init__(self,store):
        self.store=store;a=store.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,'registration',a.application_version),store.root)

    def _record(self):return self.store._record()

    def _guard(self):
        super()._guard();s=self.store.database.stat()
        if (s.st_dev,s.st_ino)!=self.store.file_id:raise ConflictError('Registration database was replaced')
        self.store._guard_xml()


class RegistrationStore:
    def __init__(self,authority:RegistrationAuthority,recovery_root):
        if not isinstance(authority,RegistrationAuthority):raise WriteProofError('Separate registration authority required')
        self.authority=authority;self.database=Path(authority.database);self.root=Path(recovery_root).absolute()
        if (self.root!=self.root.resolve() or self.root.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.root)):
            raise WriteProofError('Registration recovery redirects or overlaps the source')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.xml=self.database.with_name('masterPlaylists6.xml');self.journal=self.root/'transaction.json'
        s=self.database.stat();self.file_id=(s.st_dev,s.st_ino);self.lease=_Lease(self)

    def _guard_xml(self):
        _regular(self.xml)
        if self.xml!=self.xml.resolve() or self.xml.stat().st_uid!=os.getuid():raise WriteProofError('Registration XML redirects')

    def _scope(self,request):
        planner.validate_request(request);a=self.authority
        if (request['source']!=a.database or request['library_id']!=a.library_id or request['schema_sha256']!=a.schema_sha256
                or any(MediaIdentity.from_dict(c['audio']).file_type not in a.file_types for c in request['tracks'])):
            raise WriteProofError('Registration request exceeds its accepted library/format scope')

    def _plan(self,conn,request):
        self._scope(request)
        if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
            raise ConflictError('Registration library/schema changed')
        return planner.build_plan(conn,request)

    def _apply_sql(self,conn,changes):
        conn.set_authorizer(_authorizer)
        try:
            for c in changes:
                if c['table']=='djmdContent' and c['before'] is None:
                    row=c['after']
                    if (row['ID']!=c['id'] or conn.execute('SELECT COUNT(*) FROM djmdContent WHERE ID=? OR UUID=? OR FolderPath=?',
                        (row['ID'],row['UUID'],row['FolderPath'])).fetchone()!=(0,)):
                        raise ConflictError('Native registration identity/path is occupied')
                    columns=list(row)
                    conn.execute('INSERT INTO djmdContent ('+','.join(_quote(k) for k in columns)+') VALUES ('+
                                 ','.join('?' for _ in columns)+')',[row[k] for k in columns])
                elif c['table']=='agentRegistry' and c['id']=='localUpdateCount':
                    if (planner.encoded_row(conn,'agentRegistry','localUpdateCount')!=c['before']
                            or {k:v for k,v in c['before'].items() if k!='int_1'}!={k:v for k,v in c['after'].items() if k!='int_1'}
                            or c['after']['int_1']!=c['before']['int_1']+1):
                        raise ConflictError('Native registration counter changed')
                    if conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                                    (c['after']['int_1'],c['before']['int_1'])).rowcount!=1:
                        raise ConflictError('Native registration counter changed during publication')
                else:raise WriteProofError('Registration cannot update or delete existing musical data')
        finally:conn.set_authorizer(None)

    def _validate_record(self,record):
        a=self.authority;s=self.database.stat()
        if (record['format']!=FORMAT or record['source']!=str(self.database) or record['library_id']!=a.library_id
                or record['schema_sha256']!=a.schema_sha256 or record['application_version']!=a.application_version
                or record['source_file_id']!=[s.st_dev,s.st_ino] or set(record['payload_sha256'])!=set(PAYLOADS)):
            raise RecoveryRequired('Unknown registration recovery identity')
        self._scope(record['request']);archive=self.root/record['archive']
        if (archive.parent!=self.root or archive!=archive.resolve() or archive.name!='registration-'+record['request']['operation_id']
                or archive.stat().st_mode&0o077):raise RecoveryRequired('Registration archive redirects')
        for name in PAYLOADS:
            if _sha(archive/name)!=record['payload_sha256'][name]:raise RecoveryRequired('Registration backup changed')
        if record['payload_sha256']['before.xml']!=record['payload_sha256']['after.xml']:
            raise RecoveryRequired('Registration cannot change XML')
        _regular(archive/'operation.json')
        if json.loads((archive/'operation.json').read_bytes())!=record:raise RecoveryRequired('Registration archive identity differs')
        with tempfile.TemporaryDirectory(prefix='soundbox-registration-recovery-') as tmp:
            root=Path(tmp).resolve();shutil.copyfile(archive/'before.db',root/'before.db')
            shutil.copyfile(archive/'after.db',root/'after.db')
            before=_connect(root/'before.db',readonly=True);after=_connect(root/'after.db',readonly=True)
            try:
                plan=self._plan(before,record['request'])
                if plan!={k:record[k] for k in ('changes','bindings','guards')}:
                    raise RecoveryRequired('Registration recovery differs from its durable intention')
                _integrity(before);_integrity(after);first,final=_affected(plan['changes'])
                if not _matches(before,first) or not _matches(after,final):raise RecoveryRequired('Registration paired targets differ')
            finally:before.close();after.close()
        return record

    def _record(self):
        try:
            _regular(self.journal);return self._validate_record(json.loads(self.journal.read_bytes()))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise RecoveryRequired('Registration recovery is incomplete; evidence preserved') from exc

    def _recover_locked(self):
        if not self.journal.exists() and not self.journal.is_symlink():return None
        record=self._record();self._guard_xml();xml_sha=_sha(self.xml)
        if xml_sha!=record['payload_sha256']['before.xml']:raise RecoveryRequired('Registration XML changed after interruption')
        conn=self.lease._connect()
        try:
            conn.execute('BEGIN IMMEDIATE');first,final=_affected(record['changes']);self.lease._guard()
            if _sha(self.xml)!=xml_sha:raise RecoveryRequired('XML changed during registration recovery')
            if _matches(conn,final):outcome='committed'
            elif _matches(conn,first):outcome='rolled-back'
            else:raise RecoveryRequired('Registration is outside its exact affected recovery pair')
            conn.rollback()
        finally:conn.close()
        self.lease._guard()
        receipt={**record,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database)}
        archive=self.root/record['archive'];atomic_bytes(archive/'result.json',_json(receipt))
        atomic_bytes(self.root/'last-result.json',_json(receipt));_fault('receipt-ready')
        self.journal.unlink();sync_dir(self.root);_fault('journal-retired')
        if outcome=='committed':self._rotate(archive)
        return outcome

    def recover(self):
        with self.lease._lock():return self._recover_locked()

    def _bindings_current(self,bindings):
        # A later native metadata edit is normal. Replay checks identity/path,
        # never rolls a completed song back to its original bare fields.
        with encrypted_backup(self.database) as snapshot:
            conn=_connect(snapshot,readonly=True)
            try:
                for b in bindings:
                    row=planner.encoded_row(conn,'djmdContent',b['native_id'])
                    if not row or row['UUID']!=b['native_uuid'] or row['FolderPath']!=b['canonical'] or row['rb_local_deleted']!=0:
                        raise ConflictError('A completed registration was removed or rebound; do not recreate it automatically')
            finally:conn.close()
        return bindings

    def apply(self,request):
        request=json.loads(_json(request));self._scope(request)
        with self.lease._lock():
            self._recover_locked();self._resume_retirement();self._guard_xml()
            archive=self.root/('registration-'+request['operation_id'])
            record=None
            if archive.exists() or archive.is_symlink():
                _regular(archive/'operation.json');record=json.loads((archive/'operation.json').read_bytes())
                if _digest(record['request'])!=_digest(request):raise RecoveryRequired('Registration operation identity was reused')
                result=archive/'result.json'
                if result.exists():
                    _regular(result);receipt=json.loads(result.read_bytes())
                    if {k:receipt.get(k) for k in record}!=record:raise RecoveryRequired('Registration receipt identity differs')
                    if receipt.get('outcome')!='committed':raise ConflictError('Refresh captured media and renew the rolled-back registration intent')
                    self._rotate(archive);return self._bindings_current(record['bindings'])
                # A crash after staging but before journal publication can only
                # resume after complete evidence validation and exact live BEFORE.
                self._validate_record(record)
            audio=[MediaIdentity.from_dict(c['audio']) for c in request['tracks']]
            for facts in audio:facts.revalidate()
            if record is None:
                xml_bytes=self.xml.read_bytes()
                with encrypted_backup(self.database) as snapshot:
                    conn=_connect(snapshot,readonly=True)
                    try:plan=self._plan(conn,request)
                    finally:conn.close()
                    if not plan['changes']:return self._bindings_current(plan['bindings'])
                    archive.mkdir(mode=0o700);copy_state(snapshot,archive/'before.db')
                    atomic_bytes(archive/'before.xml',xml_bytes);atomic_bytes(archive/'after.xml',xml_bytes)
                    with tempfile.TemporaryDirectory(prefix='soundbox-registration-stage-') as tmp:
                        after=Path(tmp).resolve()/'after.db';shutil.copyfile(snapshot,after);conn=_connect(after)
                        try:
                            conn.execute('BEGIN IMMEDIATE');self._apply_sql(conn,plan['changes']);conn.commit();_integrity(conn)
                            if not _matches(conn,_affected(plan['changes'])[1]):raise WriteProofError('Staged registration readback differs')
                        finally:conn.close()
                        copy_state(after,archive/'after.db')
                a=self.authority;s=self.database.stat()
                record=dict(format=FORMAT,source=str(self.database),source_file_id=[s.st_dev,s.st_ino],library_id=a.library_id,
                    schema_sha256=a.schema_sha256,application_version=a.application_version,archive=archive.name,request=request,
                    **plan,retain_full_backups=a.retain_full_backups,
                    retention_predecessor_sha256=_sha(self.root/'registration-latest.json') if (self.root/'registration-latest.json').exists() else None,
                    payload_sha256={n:_sha(archive/n) for n in PAYLOADS})
                atomic_bytes(archive/'operation.json',_json(record));sync_dir(archive);sync_dir(self.root);_fault('payload-ready')
            conn=self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE');self.lease._guard();first,final=_affected(record['changes'])
                if not _matches(conn,first) or _sha(self.xml)!=record['payload_sha256']['before.xml']:
                    raise ConflictError('Native registration base changed before save')
                if any(planner.encoded_row(conn,'djmdContent',g['id'])!=g['row'] for g in record['guards']):
                    raise ConflictError('An existing native path changed before reuse')
                for facts in audio:facts.revalidate()
                atomic_bytes(self.journal,_json(record));_fault('journal-ready')
                self._apply_sql(conn,record['changes'])
                if not _matches(conn,final):raise WriteProofError('Registration live readback differs')
                _fault('sql-applied');self.lease._guard()
                for facts in audio:facts.revalidate()
                if _sha(self.xml)!=record['payload_sha256']['before.xml']:raise ConflictError('XML changed before registration commit')
                conn.commit();_fault('sql-committed')
            except BaseException:conn.rollback();raise
            finally:conn.close()
            if self._recover_locked()!='committed':raise RecoveryRequired('Registration completion did not verify')
            return record['bindings']

    def _retention(self):
        path=self.root/'registration-latest.json'
        if not path.exists() and not path.is_symlink():return None
        _regular(path);r=json.loads(path.read_bytes());a=self.authority
        if (set(r)!={'format','source','library_id','schema_sha256','application_version','current','retiring'}
                or r.get('format')!='soundbox-registration-retention-v1' or r.get('source')!=str(self.database)
                or r.get('library_id')!=a.library_id or r.get('schema_sha256')!=a.schema_sha256
                or r.get('application_version')!=a.application_version):raise RecoveryRequired('Unknown registration retention identity')
        return r

    def _receipt(self,entry):
        archive=self.root/entry['archive']
        if archive.parent!=self.root or archive!=archive.resolve() or not archive.name.startswith('registration-'):
            raise RecoveryRequired('Registration retention redirects')
        if _sha(archive/'operation.json')!=entry['operation_sha256']:raise RecoveryRequired('Registration retained operation changed')
        record=json.loads((archive/'operation.json').read_bytes());_regular(archive/'result.json')
        result=json.loads((archive/'result.json').read_bytes());a=self.authority
        if (record.get('format')!=FORMAT or record.get('source')!=str(self.database) or record.get('library_id')!=a.library_id
                or record.get('schema_sha256')!=a.schema_sha256 or record.get('application_version')!=a.application_version
                or archive.name!='registration-'+record['request']['operation_id']
                or {k:result.get(k) for k in record}!=record or result.get('outcome')!='committed'):
            raise RecoveryRequired('Registration retention lacks a committed receipt')
        return archive,record

    def _resume_retirement(self):
        if self.authority.retain_full_backups:return
        pointer=self._retention()
        if not pointer:return
        self._receipt(pointer['current']);entry=pointer.get('retiring')
        if entry is None:return
        if entry==pointer['current']:raise RecoveryRequired('Latest registration backup cannot retire itself')
        if self.journal.exists():raise RecoveryRequired('Resolve registration before retiring backups')
        archive,record=self._receipt(entry)
        if record.get('retain_full_backups') is not False:raise RecoveryRequired('Native acceptance backups are protected')
        marker=archive/'retired.json';expected=dict(format='soundbox-retired-registration-payloads-v1',
            operation_sha256=entry['operation_sha256'],payload_sha256={n:record['payload_sha256'][n] for n in ('before.db','after.db')})
        if marker.exists() or marker.is_symlink():
            _regular(marker)
            if json.loads(marker.read_bytes())!=expected:raise RecoveryRequired('Unknown retired registration evidence')
        else:
            for name,digest in expected['payload_sha256'].items():
                if _sha(archive/name)!=digest:raise RecoveryRequired('Registration backup changed before retirement')
            atomic_bytes(marker,_json(expected))
        for name,digest in expected['payload_sha256'].items():
            path=archive/name
            if not path.exists() and not path.is_symlink():continue
            if _sha(path)!=digest:raise RecoveryRequired('Registration backup changed during retirement')
            path.unlink()
        sync_dir(archive);atomic_bytes(self.root/'registration-latest.json',_json({**pointer,'retiring':None}))

    def _rotate(self,archive):
        if self.authority.retain_full_backups:return
        prior=self._retention();entry=dict(archive=archive.name,operation_sha256=_sha(archive/'operation.json'))
        _,record=self._receipt(entry)
        if record.get('retain_full_backups') is not False or (archive/'retired.json').exists():return
        if prior and prior['current']==entry:self._resume_retirement();return
        previous_hash=_sha(self.root/'registration-latest.json') if prior else None
        if record['retention_predecessor_sha256']!=previous_hash:return
        a=self.authority;pointer=dict(format='soundbox-registration-retention-v1',source=str(self.database),library_id=a.library_id,
            schema_sha256=a.schema_sha256,application_version=a.application_version,current=entry,retiring=prior['current'] if prior else None)
        atomic_bytes(self.root/'registration-latest.json',_json(pointer));self._resume_retirement()


def _fault(_phase):
    """Synthetic TEMP-source crash tests only; never fault-inject a real library."""
