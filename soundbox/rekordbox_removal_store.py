"""Confirmed Collection + recoverable file disposal on the shared source lane.

Requires its own accepted library/profile authority and an exact prepared plan.
No source-audio disposal occurs during preparation. GUI confirmation binds the
whole plan hash; each file goes through the OS Trash with an interruption tag.
"""
from dataclasses import dataclass,asdict
from collections import Counter
from contextlib import contextmanager
import ctypes
import errno
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from uuid import uuid4,UUID

from . import rekordbox_removal_plan as planner
from .analysis import identity
from . import mac_xattrs as xattrs
from typing import Any
from .rekordbox_analysis_store import shared_root
from .rekordbox_cue_store import CueWriteAuthority,PilotCueStore
from .rekordbox_tags import NATIVE_VERSION,_schema
from .rekordbox_write import (_connect,_identity,_rows,_quote,_json,_digest,_cell,_regular,
    _inactive_journal_proof,encrypted_backup,WriteProofError,ConflictError,RecoveryRequired)
from .source_acquisition import acquire_source,SourceContract,SourceAcquisitionError
from .state_io import atomic_bytes,sync_dir
from .native_transaction import hash_evidence
from .device_staging import exclusive_rename, exclusive_rename_from_path

FORMAT='soundbox-native-removal-v1'
AUTHORITY_FORMAT='soundbox-removal-authority-v1'
TAG='com.lowfreqlabs.soundbox.removal'
def confirmation(plan):
    values: dict[str,Any]=dict(selections=plan['selections'],audio=[{k:a[k] for k in ('path','size','sha256')} for a in plan['audio']])
    if plan.get('native_only'):values['native_only']=True
    return _digest(values)


COLUMNS=frozenset(('rb_local_deleted','rb_data_status','rb_local_usn','updated_at','TrackNo'))


@dataclass(frozen=True)
class RemovalAuthority:
    database:str
    library_id:str
    schema_sha256:str
    accepted_reference_sha256:str
    accepted:bool
    application_version:str=NATIVE_VERSION
    unsynced_accepted:bool=False

    def __post_init__(self):
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,'removal',self.application_version)
        if (self.accepted is not True or type(self.unsynced_accepted) is not bool
                or not isinstance(self.accepted_reference_sha256,str) or len(self.accepted_reference_sha256)!=64
                or any(c not in '0123456789abcdef' for c in self.accepted_reference_sha256)):
            raise WriteProofError('Collection removal requires its separate native acceptance')


def load_authority(path,source,library_id):
    try:
        _regular(Path(path));body=json.loads(Path(path).read_bytes())
        if body.pop('format',None)!=AUTHORITY_FORMAT:return None
        authority=RemovalAuthority(**body)
        return authority if (authority.database,authority.library_id)==(str(source),library_id) else None
    except (OSError,ValueError,TypeError,WriteProofError):return None


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _entity(info):return info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode)


def _fd_sha(handle):
    os.lseek(handle,0,os.SEEK_SET);digest=hashlib.sha256()
    while block:=os.read(handle,1024*1024):digest.update(block)
    return digest.hexdigest()


@contextmanager
def _restore_parent(path, expected=None):
    """Pin every existing destination ancestor; recovery never recreates one."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise RecoveryRequired('Recovery path is not absolute')
    handle=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.parent.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=handle)
            os.close(handle);handle=child
        current=_entity(os.fstat(handle))
        if expected is not None and tuple(expected)!=current:
            raise RecoveryRequired('Recovery parent changed since confirmation; originals and journal retained')
        expected=current
        def guard():
            if path.parent.resolve(strict=True)!=path.parent or _entity(path.parent.lstat())!=expected:
                raise RecoveryRequired('Recovery parent changed; originals and journal retained')
        guard();yield handle,path.name,guard
        guard()
    finally:os.close(handle)


def _capture_recovery_parent(path,epoch):
    """Bind the selected leaf's existing parent into the immutable review."""
    with _restore_parent(path) as (parent,name,guard):
        if _entity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=(*epoch[:2],stat.S_IFREG):
            raise ConflictError('Removal file changed while capturing its recovery parent')
        guard();return list(_entity(os.fstat(parent)))


def _recovery_parent(plan,path):
    # A fresh directory observation cannot renew an interrupted operation's
    # original parent authority, including when the replacement is empty.
    parents=plan.get('recovery_parents')
    if parents is None:
        raise RecoveryRequired('Legacy removal recovery has no recorded original parent; preserve originals and journal for review')
    return parents[str(path)]


def _require_recovery_parents(plan,xml):
    paths=[*(audio['path'] for audio in plan['audio']),
           *(resource['path'] for resource in plan['resources']),str(xml)]
    for path in paths:
        expected=_recovery_parent(plan,path)
        try:
            with _restore_parent(path,expected):pass
        except OSError as exc:
            raise RecoveryRequired('Recorded removal recovery parent is unavailable; originals and journal retained') from exc


def _exclusive_restore(source,handle,name):
    if isinstance(source,tuple):
        exclusive_rename(source[0],source[1],handle,name);return
    # Trash may permit exact-file access while refusing a directory descriptor.
    # Keep that source's open file identity; destination remains descriptor-bound.
    exclusive_rename_from_path(Path(source),handle,name)


def _fd_tag(handle):
    call=ctypes.CDLL(None,use_errno=True).fgetxattr
    call.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
    call.restype=ctypes.c_ssize_t
    size=call(handle,TAG.encode(),None,0,0,0)
    if size<0:
        code=ctypes.get_errno()
        if code==getattr(errno,'ENOATTR',93):return None
        raise OSError(code,os.strerror(code))
    if size>4096:raise RecoveryRequired('Recovery attribute exceeds its bound')
    buffer=ctypes.create_string_buffer(size)
    if call(handle,TAG.encode(),buffer,size,0,0)!=size:raise RecoveryRequired('Recovery attribute changed during read')
    return buffer.raw[:size]


def _restore_fd_tag(handle,old):
    library=ctypes.CDLL(None,use_errno=True)
    if old is None:
        remove=library.fremovexattr;remove.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int];remove.restype=ctypes.c_int
        result=remove(handle,TAG.encode(),0)
        if result and ctypes.get_errno()==getattr(errno,'ENOATTR',93):return
    else:
        raw=base64.b64decode(old,validate=True);buffer=ctypes.create_string_buffer(raw)
        write=library.fsetxattr
        write.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int];write.restype=ctypes.c_int
        result=write(handle,TAG.encode(),buffer,len(raw),0,0)
    if result:
        code=ctypes.get_errno();raise OSError(code,os.strerror(code))


def _restore_audio(audio,trashed,*,parent_identity):
    path=Path(audio['path']);handle=os.open(trashed if trashed is not None else path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        original=os.fstat(handle);old_tag=base64.b64decode(audio['original_tag'],validate=True) if audio['original_tag'] is not None else None
        tag=_fd_tag(handle)
        if (not stat.S_ISREG(original.st_mode) or original.st_nlink!=1 or original.st_uid!=os.getuid()
                or (original.st_dev,original.st_ino)!=tuple(audio['epoch'][:2])
                or _fd_sha(handle)!=audio['sha256'] or identity(original)!=identity(os.fstat(handle))
                or tag not in ((audio['tag'].encode(),) if trashed is not None else (audio['tag'].encode(),old_tag))):
            raise RecoveryRequired('Trashed audio changed before restoration')
        with _restore_parent(path,parent_identity) as (parent,name,guard):
            guard()
            if trashed is not None:
                _fault('audio-restore-ready');guard()
                try:_exclusive_restore(trashed,parent,name)
                except OSError as exc:
                    raise RecoveryRequired('Audio recovery destination is occupied or unavailable; original Trash file and journal retained') from exc
            guard();named=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if _entity(named)!=_entity(original) or _fd_sha(handle)!=audio['sha256']:
                raise RecoveryRequired('Audio recovery publication changed; all occupants and journal retained')
            if tag!=old_tag:_restore_fd_tag(handle,audio['original_tag'])
            os.fsync(handle);os.fsync(parent)
            if _entity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=_entity(os.fstat(handle)):
                raise RecoveryRequired('Restored audio was replaced; journal retained')
    finally:os.close(handle)


def _restore_absent_bytes(path,raw,*,parent_identity):
    """Only fill a proved vacancy; a later external occupant always wins."""
    with _restore_parent(path,parent_identity) as (parent,name,guard):
        temporary='.soundbox-removal-'+uuid4().hex+'.tmp'
        handle=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            view=memoryview(raw)
            while view:
                count=os.write(handle,view)
                if count<=0:raise OSError('Recovery write made no progress')
                view=view[count:]
            os.fsync(handle);guard();_fault('resource-restore-ready');guard()
            try:_exclusive_restore((parent,temporary),parent,name)
            except OSError as exc:raise RecoveryRequired('Resource recovery destination changed; backup and journal retained') from exc
            guard()
            if (_entity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=_entity(os.fstat(handle))
                    or _fd_sha(handle)!=hashlib.sha256(raw).hexdigest()):
                raise RecoveryRequired('Resource recovery publication changed; journal retained')
            os.fsync(parent)
        finally:
            try:
                if _entity(os.stat(temporary,dir_fd=parent,follow_symlinks=False))==_entity(os.fstat(handle)):
                    os.unlink(temporary,dir_fd=parent)
            except FileNotFoundError:pass
            os.close(handle)


def _encode(row):return {k:_cell(v) for k,v in row.items()} if row is not None else None


def _decode(row):return {k:bytes.fromhex(v['blob']) if isinstance(v,dict) and set(v)=={'blob'} else v for k,v in row.items()}


def _row(conn,table,key):
    field='registry_id' if table=='agentRegistry' else 'ID'
    cursor=conn.execute(f'SELECT * FROM {_quote(table)} WHERE {_quote(field)}=?',(key,));names=[v[0] for v in cursor.description]
    rows=list(cursor)
    if len(rows)>1:raise WriteProofError('Native removal row identity is ambiguous')
    return _encode(dict(zip(names,rows[0]))) if rows else None


def _edges(changes):
    before: dict[tuple[str,str],Any]={};after: dict[tuple[str,str],Any]={}
    for change in changes:
        key=(change['table'],change['id']);before.setdefault(key,change['before']);after[key]=change['after']
    return before,after


def _matches(conn,expected):return all(_row(conn,t,key)==row for (t,key),row in expected.items())


def _authorizer(action,table,column,database,trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main':return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_DELETE and table in planner.TABLES and table!='agentRegistry':return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE:
            if table=='agentRegistry' and column=='int_1':return sqlite3.SQLITE_OK
            if table in planner.TABLES and table!='agentRegistry' and column in COLUMNS:return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,sqlite3.SQLITE_FUNCTION,
                  sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _apply(conn,changes):
    for change in changes:
        table,key=change['table'],change['id'];before,after=change['before'],change['after']
        if table not in planner.TABLES or _row(conn,table,key)!=before:raise ConflictError('Native removal baseline changed')
        field='registry_id' if table=='agentRegistry' else 'ID'
        if after is None:conn.execute(f'DELETE FROM {_quote(table)} WHERE {_quote(field)}=?',(key,))
        else:
            decoded=_decode(after);columns=[k for k in after if after[k]!=(before or {}).get(k)]
            allowed={'int_1'} if table=='agentRegistry' else COLUMNS
            if not columns or not set(columns)<=allowed:raise WriteProofError('Removal plan changes an unaccepted native field')
            conn.execute(f'UPDATE {_quote(table)} SET '+','.join(_quote(k)+'=?' for k in columns)+
                         f' WHERE {_quote(field)}=?',tuple(decoded[k] for k in columns)+(key,))
        if _row(conn,table,key)!=after:raise WriteProofError('Native removal did not match the intended row')


def _audio_regular(path):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid()
            or path.resolve(strict=True)!=path or path.is_relative_to('/Volumes')
            or path.suffix.lower() not in ('.wav','.wave','.mp3','.aif','.aiff','.flac','.m4a','.aac')):
        raise WriteProofError('Disposal requires one explicitly selected regular desktop audio file')
    return info


def _missing_proof(path):
    """Prove ENOENT against the nearest existing directory, without mkdir/read.

    Old downloads often lose their whole containing folder. A missing parent is
    still a known absent file; permissions, non-directory occupants and redirects
    are not absence. Bind its existing ancestor so later replacement fails closed.
    """
    path=Path(path)
    if not path.is_absolute() or path.resolve(strict=False)!=path:
        raise WriteProofError('A missing audio path redirects')
    ancestor=path
    for _ in range(128):
        try:info=ancestor.lstat()
        except FileNotFoundError:
            ancestor=ancestor.parent
            continue
        if (ancestor==path or not stat.S_ISDIR(info.st_mode)
                or ancestor.resolve(strict=True)!=ancestor):
            raise ConflictError('A reviewed missing audio path is occupied or redirects')
        return dict(ancestor=str(ancestor),device=info.st_dev,inode=info.st_ino)
    raise WriteProofError('A missing audio path exceeds the directory bound')


def _fault(_phase):pass


class _Lease(PilotCueStore):
    def __init__(self,store):
        self.store=store;a=store.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,'removal',a.application_version),store.root)
    def _record(self):return self.store._record()
    def _guard(self):
        super()._guard();s=self.database.stat()
        if (s.st_dev,s.st_ino)!=self.store.file_id:raise ConflictError('Native removal database was replaced')
        if not self.store.xml.exists() and self.store.journal.exists():
            _record,plan,folder=self.store._record()
            marker=self.store._xml_marker(folder,plan)
            if marker is None:raise RecoveryRequired('Missing playlist XML has no owned removal claim')
            with _restore_parent(self.store.xml,_recovery_parent(plan,self.store.xml)) as (parent,_name,_guard):
                locations=self.store._xml_view(parent,plan,marker)
                if not locations:raise RecoveryRequired('Missing playlist XML has no preserved owned entity')
        else:_regular(self.store.xml)
        if self.store.xml.resolve()!=self.store.xml:raise WriteProofError('Native removal XML redirects')


class RemovalStore:
    def __init__(self,authority,data_root,*,trash=None,trash_root=None):
        if not isinstance(authority,RemovalAuthority):raise WriteProofError('Explicit removal authority required')
        self.authority=authority;self.database=Path(authority.database);self.data_root=Path(data_root).absolute()
        if (self.data_root.resolve()!=self.data_root or self.data_root.is_relative_to(self.database.parent)
                or self.database.is_relative_to(self.data_root)):raise WriteProofError('Removal recovery overlaps the source')
        self.root=shared_root(self.data_root,self.database,authority.library_id);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.xml=self.database.with_name('masterPlaylists6.xml');self.journal=self.root/'transaction.json'
        s=self.database.stat();self.file_id=(s.st_dev,s.st_ino);self.lease=_Lease(self)
        self._plan_cache: Any=None
        self.trash=trash;self.trash_root=Path(trash_root) if trash_root else Path.home()/'.Trash'

    def _guard_trash(self):
        # Native-only removal never accesses Trash, source audio or sidecars.
        # Validate this separate file-disposal boundary only when it is used.
        if self.trash_root!=self.trash_root.resolve():raise WriteProofError('Trash redirects')
        if self.trash_root.exists():
            s=self.trash_root.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid():raise WriteProofError('Trash is not owned by this user')

    def _snapshot(self):return encrypted_backup(self.database)

    def _catalog(self,conn):
        if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
            raise WriteProofError('Native removal library/schema changed')
        names={v[0] for v in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t:[_encode(r) for r in _rows(conn,t)] if t in names else [] for t in (*planner.TABLES,*planner.UNSUPPORTED,'djmdPlaylist')}

    def _load(self,folder):
        folder=Path(folder)
        if (folder.resolve()!=folder or folder.parent!=self.data_root/'native-removal-plans'
                or str(UUID(folder.name))!=folder.name):raise WriteProofError('Removal plan location changed')
        path=folder/'plan.json';_regular(path)
        versions=tuple((name,tuple(identity((folder/name).stat()))) for name in ('plan.json','before.db','after.db','before.xml','after.xml'))
        raw=path.read_bytes();plan_sha256=hashlib.sha256(raw).hexdigest();plan=json.loads(raw)
        cache_key=(str(folder),versions,plan_sha256,_json(asdict(self.authority)),self.file_id)
        if (plan.get('format')!=FORMAT or plan.get('authority')!=asdict(self.authority)
                or plan.get('operation_id')!=folder.name or plan.get('source_file_id')!=list(self.file_id)):
            raise RecoveryRequired('Removal plan does not match its explicit authority/source')
        if type(plan.get('native_only',False)) is not bool:
            raise RecoveryRequired('Unknown removal file policy')
        if plan.get('native_only'):
            targets={target['id']:target for target in plan['targets']}
            history=plan.get('history')
            if (not targets or len(targets)!=len(plan['selections'])
                    or not isinstance(history,dict) or set(history)!={selected['id'] for selected in plan['selections']}
                    or any(plan.get(key) for key in ('audio','resources','missing','missing_proofs','retained'))
                    or any(not selected.get('native_active') or not selected.get('keep_file')
                        or selected.get('missing') or selected['native_id'] not in targets
                        or (selected.get('uuid'),selected['path'])!=(targets[selected['native_id']]['uuid'],
                            targets[selected['native_id']]['path']) for selected in plan['selections'])):
                raise RecoveryRequired('Native-only removal contains file disposal or mismatched targets')
            for selected in plan['selections']:
                row=history[selected['id']]
                if (not isinstance(row,dict) or (row.get('id'),row.get('uuid'),row.get('path'),row.get('source'),row.get('library_id'))
                        !=(selected['native_id'],selected['uuid'],selected['path'],str(self.database),self.authority.library_id)
                        or not isinstance(row.get('display'),dict) or type(row['display'].get('title')) is not str):
                    raise RecoveryRequired('Native-only removal history differs from its exact selection')
        parents=plan.get('recovery_parents')
        if parents is not None:
            paths={*(audio['path'] for audio in plan['audio']),
                   *(resource['path'] for resource in plan['resources']),str(self.xml)}
            if (not isinstance(parents,dict) or set(parents)!=paths
                    or any(not isinstance(token,list) or len(token)!=3
                           or any(type(value) is not int for value in token)
                           or token[2]!=stat.S_IFDIR for token in parents.values())):
                raise RecoveryRequired('Removal recovery parent evidence differs from its exact files')
        names=('before.db','after.db','before.xml','after.xml')
        for name in names:_regular(folder/name)
        if hash_evidence((folder/name for name in names),_sha)!=[plan['payloads'][name] for name in names]:
            raise RecoveryRequired('Native removal recovery bytes changed')
        for index,resource in enumerate(plan['resources']):
            expected=folder/'resources'/str(index)
            if Path(resource['backup'])!=expected or expected.resolve()!=expected:
                raise RecoveryRequired('Removal native-resource backup changed')
            _regular(expected)
            if _sha(expected)!=resource['sha256']:raise RecoveryRequired('Removal native-resource backup changed')
        # Reuse only the captured pair's semantic reconstruction. The plan,
        # authority, paths and every recovery payload are checked freshly even
        # when filesystem timestamps repeat. Return freshly decoded plan data.
        if self._plan_cache is not None and self._plan_cache[0]==cache_key:
            self._plan_cache=(cache_key,plan,plan_sha256)
            return plan
        if {t['id'] for t in plan['targets']}!={s['native_id'] for s in plan['selections'] if s['native_active']}:
            raise RecoveryRequired('Native removal targets differ from the reviewed selection')
        if not plan.get('native_only') and ({a['path'] for a in plan['audio']}|set(plan.get('missing',[]))|{r['path'] for r in plan.get('retained',[])})!={s['path'] for s in plan['selections']}:
            raise RecoveryRequired('Audio disposal targets differ from the reviewed selection')
        with tempfile.TemporaryDirectory(prefix='soundbox-removal-validation-') as temp:
            local=Path(temp).resolve()/'master.db';shutil.copyfile(folder/'before.db',local);conn=_connect(local,readonly=True)
            try:
                tables=self._catalog(conn)
                native_rows={r['ID']:r for r in tables['djmdContent']}
                if plan.get('native_only'):
                    uuids=Counter(row['UUID'] for row in tables['djmdContent'])
                    if any(uuids[target['uuid']]!=1 for target in plan['targets']):
                        raise RecoveryRequired('Native-only removal UUID is ambiguous')
                selected_ids={t['id'] for t in plan['targets']}
                for retained in plan.get('retained',[]):
                    if not retained['references']:raise RecoveryRequired('Shared-file retention has no Collection reference')
                    for ref in retained['references']:
                        row=native_rows.get(ref['id'])
                        if not row or row!=ref['row'] or row['rb_local_deleted'] or row['ID'] in selected_ids:
                            raise RecoveryRequired('Shared-file retention identity changed')
                replay=planner.build(tables,plan['targets'],(folder/'before.xml').read_bytes(),timestamp=plan['timestamp'],xml_clocks=plan['xml_clocks'],compact=plan.get('compacted',False))
                if replay['changes']!=plan['changes'] or replay['after_xml']!=(folder/'after.xml').read_bytes():
                    raise RecoveryRequired('Removal plan does not reproduce its captured native contract')
                selected={t['id']:t['uuid'] for t in plan['targets']}
                permitted=set()
                for row in (() if plan.get('native_only') else tables['contentFile']):
                    if row['ContentID'] not in selected or row['rb_local_deleted']:continue
                    owner=selected[row['ContentID']];virtual=row['Path']
                    roots=(f'/PIONEER/USBANLZ/{owner[:3]}/{owner[3:]}/',f'/PIONEER/Artwork/{owner[:3]}/{owner[3:]}/')
                    if not any(virtual.startswith(prefix) and '/' not in virtual[len(prefix):] and virtual[len(prefix):] not in ('','.','..') for prefix in roots):
                        raise RecoveryRequired('Unaccepted native resource identity')
                    permitted.add(str(self.database.parent/'share'/virtual.lstrip('/')))
                if not {r['path'] for r in plan['resources']}<=permitted:raise RecoveryRequired('Removal resource escaped its native owner')
            finally:conn.close()
            shutil.copyfile(folder/'after.db',local);conn=_connect(local,readonly=True)
            try:
                if not _matches(conn,_edges(plan['changes'])[1]):raise RecoveryRequired('Prepared after database differs from removal rows')
            finally:conn.close()
        self._plan_cache=(cache_key,plan,plan_sha256)
        return plan

    def _record(self):
        _regular(self.journal);record=json.loads(self.journal.read_bytes())
        if (set(record)!={'format','source','library_id','source_file_id','plan_folder','plan_sha256'}
                or record['format']!=FORMAT or record['source']!=str(self.database)
                or record['library_id']!=self.authority.library_id or record['source_file_id']!=list(self.file_id)):
            raise RecoveryRequired('Unknown native removal journal; preserve all evidence')
        folder=Path(record['plan_folder']);plan=self._load(folder)
        if self._plan_cache[2]!=record['plan_sha256']:raise RecoveryRequired('Confirmed removal plan changed')
        return record,plan,folder

    def prepare(self,entries,*,native_only=False):
        """Capture only; originals remain untouched until exact GUI confirmation."""
        if type(native_only) is not bool:raise WriteProofError('Unknown removal file policy')
        if not isinstance(entries,list) or not 1<=len(entries)<=10000:raise WriteProofError('Select tracks to delete')
        with self.lease._lock():
            self._recover_locked();self._resume_retirement();self.lease._guard()
            if not native_only:self._guard_trash()
            database_epoch=identity(self.database.stat());wal_epoch=self._wal_epoch();before_xml=self.xml.read_bytes()
            op=str(uuid4());folder=self.data_root/'native-removal-plans'/op;folder.mkdir(parents=True,mode=0o700)
            with self._snapshot() as snapshot:
                conn=_connect(snapshot,readonly=True)
                try:
                    tables=self._catalog(conn);all_content=tables['djmdContent'];by_id={r['ID']:r for r in all_content}
                    native_uuids=Counter(row['UUID'] for row in all_content) if native_only else {}
                    targets=[];audio=[];selections=[];seen_paths=set();selected_native=set();missing=[];missing_proofs={}
                    for entry in entries:
                        tid=entry['id'];native_id=entry['native_id'];row=by_id.get(native_id)
                        known=entry.get('native',{})
                        if not entry.get('path'):raise WriteProofError('This Graveyard entry has no verified file path')
                        if row and row['UUID']!=known.get('uuid'):raise ConflictError('Graveyard native identity changed')
                        if native_only and (not row or row['rb_local_deleted']
                                or native_uuids[row['UUID']]!=1 or row['FolderPath']!=entry['path']
                                or not Path(entry['path']).is_absolute()):
                            raise ConflictError('The selected active Collection identity or native path changed')
                        if row and not row['rb_local_deleted']:
                            if row['rb_local_synced']==0 and not self.authority.unsynced_accepted:
                                raise WriteProofError('Newer native-record removal awaits acceptance')
                            targets.append(dict(id=native_id,uuid=row['UUID'],path=row['FolderPath']));selected_native.add(native_id)
                        if native_only:
                            assert row is not None
                            selections.append(dict(id=tid,native_id=native_id,uuid=known['uuid'],path=row['FolderPath'],
                                title=entry['display']['title'],native_active=True,missing=False,keep_file=True))
                            continue
                        raw=Path(row['FolderPath'] if row else entry['path'])
                        if not raw.is_absolute():raise WriteProofError('An absolute audio path is required')
                        canonical=raw.resolve(strict=False)
                        if entry.get('canonical') and canonical!=Path(entry['canonical']):raise ConflictError('Graveyard file alias changed')
                        if canonical.is_relative_to(self.data_root) or canonical.is_relative_to(self.database.parent):
                            raise WriteProofError('Internal SoundBox/rekordbox data cannot be disposed as music')
                        absent=False
                        try:_audio_regular(canonical)
                        except FileNotFoundError:
                            missing_proofs[str(canonical)]=_missing_proof(canonical)
                            absent=True
                        if not absent and canonical.stat().st_dev!=self.trash_root.parent.stat().st_dev:
                            raise WriteProofError('This file requires a different volume’s Trash; no deletion was started')
                        selections.append(dict(id=tid,native_id=native_id,uuid=known.get('uuid',''),path=str(canonical),
                            title=entry['display']['title'],native_active=bool(row and not row['rb_local_deleted']),missing=absent))
                        if canonical not in seen_paths:
                            if absent:missing.append(str(canonical))
                            else:
                                with acquire_source(SourceContract(canonical),folder) as captured:
                                    observed=identity(_audio_regular(canonical))
                                    if observed!=identity(captured.observation):raise ConflictError('Audio changed during deletion preparation')
                                    audio.append(dict(path=str(canonical),sha256=captured.sha256,size=captured.size,epoch=observed,
                                        tag=str(uuid4()),original_tag=self._get_tag(canonical)))
                            seen_paths.add(canonical)
                    retained_by_path: dict[str,dict]={}
                    for row in (() if native_only else all_content):
                        if row['rb_local_deleted'] or row['ID'] in selected_native:continue
                        if not row.get('FolderPath'):continue
                        try:path=Path(row['FolderPath']).resolve(strict=True)
                        except (FileNotFoundError,NotADirectoryError):
                            path=Path(row['FolderPath']).resolve(strict=False)
                        except (OSError,RuntimeError) as exc:
                            raise WriteProofError('Cannot verify an existing Collection file reference: '+str(row['Title'])) from exc
                        if path in seen_paths:
                            retained_by_path.setdefault(str(path),dict(path=str(path),references=[]))['references'].append(
                                dict(id=row['ID'],uuid=row['UUID'],title=row['Title'],row=row))
                    audio=[a for a in audio if a['path'] not in retained_by_path]
                    if not native_only:
                        for selected in selections:selected['keep_file']=selected['path'] in retained_by_path
                    built=planner.build(tables,targets,before_xml,compact=True)
                    changes=built['changes']
                    resources: list[dict]=[];share=self.database.parent/'share'
                    for row in (() if native_only else tables['contentFile']):
                        if row['ContentID'] not in selected_native or row['rb_local_deleted']:continue
                        owner=by_id[row['ContentID']]['UUID'];virtual=row['Path'];leaf=Path(virtual).name
                        roots=(f'/PIONEER/USBANLZ/{owner[:3]}/{owner[3:]}/',f'/PIONEER/Artwork/{owner[:3]}/{owner[3:]}/')
                        if not any(virtual.startswith(prefix) and '/' not in virtual[len(prefix):] and virtual[len(prefix):] not in ('','.','..') for prefix in roots):
                            raise WriteProofError('Unaccepted native removal resource path')
                        path=share/virtual.lstrip('/')
                        if not path.exists() and not path.is_symlink():continue
                        _regular(path)
                        if path.resolve()!=path or path.stat().st_size>64*1024*1024:raise WriteProofError('Native resource redirects or exceeds its bound')
                        if any(r['ContentID'] not in selected_native and not r['rb_local_deleted'] and r['Path']==virtual for r in tables['contentFile']):
                            raise WriteProofError('Native analysis/artwork is shared by an unselected track')
                        backup=folder/'resources'/str(len(resources));backup.parent.mkdir(mode=0o700,exist_ok=True)
                        shutil.copyfile(path,backup)
                        digest=_sha(path)
                        if _sha(backup)!=digest:raise ConflictError('Native resource changed during capture')
                        resources.append(dict(path=str(path),backup=str(backup),sha256=digest,epoch=identity(path.stat()),name=leaf))
                    shutil.copyfile(snapshot,folder/'before.db')
                    with tempfile.TemporaryDirectory(prefix='soundbox-removal-after-') as temp:
                        candidate=Path(temp).resolve()/'master.db';shutil.copyfile(snapshot,candidate);after=_connect(candidate)
                        try:
                            after.execute('BEGIN IMMEDIATE');after.set_authorizer(_authorizer);_apply(after,changes);after.commit()
                            if not _matches(after,_edges(changes)[1]):raise WriteProofError('Prepared native removal differs')
                        finally:after.close()
                        shutil.copyfile(candidate,folder/'after.db')
                    atomic_bytes(folder/'before.xml',before_xml);atomic_bytes(folder/'after.xml',built['after_xml'])
                    recovery_parents={entry['path']:_capture_recovery_parent(entry['path'],entry['epoch'])
                                      for entry in (*audio,*resources)}
                    recovery_parents[str(self.xml)]=_capture_recovery_parent(self.xml,identity(self.xml.stat()))
                    plan=dict(format=FORMAT,operation_id=op,authority=asdict(self.authority),source_file_id=list(self.file_id),
                        changes=changes,compacted=True,audio=audio,missing=missing,missing_proofs=missing_proofs,retained=list(retained_by_path.values()),
                        resources=resources,selections=selections,playlists=built['playlists'],
                        targets=targets,timestamp=built['timestamp'],xml_clocks=built['xml_clocks'],
                        playlist_labels=[next((r['Name'] for r in tables['djmdPlaylist'] if r['ID']==pid),pid) for pid in built['playlists']],
                        database_epoch=database_epoch,wal_epoch=wal_epoch,recovery_parents=recovery_parents,
                        payloads=dict(zip(('before.db','after.db','before.xml','after.xml'),
                            hash_evidence((folder/name for name in ('before.db','after.db','before.xml','after.xml')),_sha),strict=True)))
                    if native_only:
                        plan['native_only']=True
                        plan['history']={entry['id']:dict(id=entry['native_id'],uuid=entry['native']['uuid'],
                            path=entry['path'],source=str(self.database),library_id=self.authority.library_id,
                            display=entry['display'],proof=dict(kind='native-only-removal-plan',operation_id=op))
                            for entry in entries}
                    atomic_bytes(folder/'plan.json',_json(plan));self.lease._guard()
                    if (identity(self.database.stat())!=database_epoch or self._wal_epoch()!=wal_epoch or self.xml.read_bytes()!=before_xml):
                        raise ConflictError('Native library changed while preparing the deletion review')
                    return dict(folder=str(folder),sha256=_sha(folder/'plan.json'),plan=plan)
                finally:conn.close()

    def _wal_epoch(self):
        path=self.database.with_name(self.database.name+'-wal');return identity(path.stat()) if path.exists() else None

    @staticmethod
    def _get_tag(path):
        try:
            raw=xattrs.get(path,TAG);return base64.b64encode(raw).decode() if raw is not None else None
        except OSError as exc:
            import errno
            if exc.errno in (getattr(errno,'ENOATTR',93),getattr(errno,'ENODATA',61)):return None
            raise

    def _trash_location(self,audio,known=None,*,verify_bytes=True):
        expected=audio['tag'].encode();source=Path(audio['path'])
        candidates=[]
        from .mac_trash import fallback_name
        # Exact owned locations require no Trash-directory enumeration. This
        # also covers a crash after rename and before progress.json publication.
        direct=[self.trash_root/fallback_name(source,audio['tag']),self.trash_root/source.name]
        if known:direct.insert(0,Path(known))
        direct_matches=[]
        for path in dict.fromkeys(direct):
            if path.parent.resolve()!=self.trash_root:raise RecoveryRequired('OS disposal escaped the expected Trash')
            if not path.is_file() or path.is_symlink():continue
            try:tag=xattrs.get(path,TAG)
            except OSError:continue
            if tag==expected:
                if verify_bytes and _sha(path)!=audio['sha256']:raise RecoveryRequired('Trashed audio bytes changed')
                direct_matches.append(path)
        if len(direct_matches)>1:raise RecoveryRequired('Multiple files have the interrupted disposal identity')
        if direct_matches:return direct_matches[0]
        if known:
            path=Path(known)
            if path.parent.resolve()!=self.trash_root:raise RecoveryRequired('OS disposal escaped the expected Trash')
            if path.is_file() and not path.is_symlink():
                try:tag=xattrs.get(path,TAG)
                except OSError:tag=None
                if tag==expected:
                    if verify_bytes and _sha(path)!=audio['sha256']:raise RecoveryRequired('Trashed audio bytes changed')
                    return path
        if source.is_file():return None
        # An OS move can finish immediately before process loss. Recover only
        # the exact interruption tag, bounded by the selected original basename.
        if self.trash_root.is_dir():
            stem=source.stem;suffix=source.suffix;count=0
            fallback=fallback_name(source,audio['tag'])
            for path in self.trash_root.iterdir():
                if path.name in (source.name,fallback) or path.name.startswith(stem) and path.suffix==suffix:
                    count+=1
                    if count>1024:raise RecoveryRequired('Trash recovery filename budget exceeded')
                    candidates.append(path)
        matches=[]
        for path in dict.fromkeys(candidates):
            if path.is_symlink() or not path.is_file():continue
            try:tag=xattrs.get(path,TAG)
            except OSError:continue
            if tag==expected:
                if path.parent.resolve()!=self.trash_root:raise RecoveryRequired('Disposal escaped the expected Trash')
                if verify_bytes and _sha(path)!=audio['sha256']:raise RecoveryRequired('Trashed audio bytes changed')
                matches.append(path)
        if len(matches)>1:raise RecoveryRequired('Multiple files have the interrupted disposal identity')
        return matches[0] if matches else None

    def _progress(self,folder,plan):
        path=folder/'progress.json'
        if not path.exists():return dict(trash={},committed=False)
        _regular(path);value=json.loads(path.read_bytes())
        if set(value)!={'trash','committed'} or not isinstance(value['trash'],dict) or type(value['committed']) is not bool:
            raise RecoveryRequired('Unknown removal file progress')
        allowed={a['path'] for a in plan['audio']}
        if not set(value['trash'])<=allowed:raise RecoveryRequired('Disposal progress changed its target files')
        return value

    @staticmethod
    def _resource_claim(resource,plan):
        name='.soundbox-removal-'+plan['operation_id']+'-'+hashlib.sha256(resource['path'].encode()).hexdigest()[:16]+'.resource'
        token=dict(entity=[*resource['epoch'][:2],stat.S_IFREG],sha256=resource['sha256'])
        return name,token

    def _resource_claim_present(self,resource,plan):
        name,token=self._resource_claim(resource,plan);path=Path(resource['path'])
        with _restore_parent(path,_recovery_parent(plan,path)) as (parent,_leaf,_guard):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:return False
            self._verify_recovery_leaf(parent,name,token);return True

    def _retire_resource_claim(self,resource,plan):
        name,token=self._resource_claim(resource,plan)
        with _restore_parent(Path(resource['path']),_recovery_parent(plan,resource['path'])) as (parent,leaf,guard):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:return
            self._verify_recovery_leaf(parent,name,token);guard()
            try:os.stat(leaf,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise RecoveryRequired('Native resource recovery has a newer live occupant; original claim and journal retained')
            os.unlink(name,dir_fd=parent);os.fsync(parent)

    def _remove_resource(self,resource,plan):
        path=Path(resource['path']);name,token=self._resource_claim(resource,plan)
        with _restore_parent(path,_recovery_parent(plan,path)) as (parent,leaf,guard):
            self._move_recovery_leaf(parent,leaf,name,token,guard,'resource-removal-claimed')
            self._verify_recovery_leaf(parent,name,token);guard()
            try:os.stat(leaf,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise RecoveryRequired('Native resource reappeared during removal; original claim and journal retained')
            os.unlink(name,dir_fd=parent);os.fsync(parent)

    def _xml_names(self,plan):
        prefix='.'+self.xml.name+'.removal-'+plan['operation_id']
        return {'original':prefix+'.old','replacement':prefix+'.new'}

    def _xml_marker(self,folder,plan):
        path=folder/'xml-recovery.json'
        if not path.exists() and not path.is_symlink():return None
        _regular(path);marker=json.loads(path.read_bytes())
        if (not isinstance(marker,dict) or set(marker)!={'format','operation_id','source','parent','tokens'}
                or marker['format']!='soundbox-removal-xml-claim-v1'
                or marker['operation_id']!=plan['operation_id'] or marker['source']!=str(self.xml)
                or not isinstance(marker['tokens'],dict) or set(marker['tokens'])!={'original','replacement'}):
            raise RecoveryRequired('Unknown removal XML claim; preserve every occupant')
        with _restore_parent(self.xml,_recovery_parent(plan,self.xml)) as (parent,_name,_guard):
            if marker['parent']!=list(_entity(os.fstat(parent))):raise RecoveryRequired('Removal XML parent was replaced')
        payloads={plan['payloads']['before.xml'],plan['payloads']['after.xml']}
        for token in marker['tokens'].values():
            if (not isinstance(token,dict) or set(token)!={'entity','sha256'} or type(token['sha256']) is not str or token['sha256'] not in payloads
                    or not isinstance(token['entity'],list) or len(token['entity'])!=3
                    or any(type(v) is not int for v in token['entity']) or token['entity'][2]!=stat.S_IFREG
                    or token['entity'][0]!=marker['parent'][0]):
                raise RecoveryRequired('Removal XML claim changes its exact entity or accepted bytes')
        if len({tuple(token['entity']) for token in marker['tokens'].values()})!=2:
            raise RecoveryRequired('Removal XML claims alias each other')
        return marker

    @staticmethod
    def _verify_recovery_leaf(parent,name,token):
        handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            before=os.fstat(handle)
            if (list(_entity(before))!=token['entity'] or before.st_nlink!=1 or before.st_uid!=os.getuid()
                    or _fd_sha(handle)!=token['sha256']
                    or identity(before)!=identity(os.fstat(handle))
                    or _entity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=_entity(before)):
                raise RecoveryRequired('Removal recovery occupant changed; preserve the claim and journal')
        finally:os.close(handle)

    def _xml_view(self,parent,plan,marker):
        names=self._xml_names(plan);locations={}
        for token_key,name in ((None,self.xml.name),*((key,name+suffix) for key,name in names.items() for suffix in ('','.retired'))):
            try:info=os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:continue
            candidates=[token_key] if token_key else list(marker['tokens'])
            key=next((key for key in candidates if list(_entity(info))==marker['tokens'][key]['entity']),None)
            if key is None or key in locations:raise RecoveryRequired('Foreign or duplicate removal XML occupant; journal retained')
            self._verify_recovery_leaf(parent,name,marker['tokens'][key]);locations[key]=name
        return locations

    def _begin_xml_change(self,folder,plan,current,desired):
        names=self._xml_names(plan)
        with _restore_parent(self.xml,_recovery_parent(plan,self.xml)) as (parent,name,guard):
            if any((self.xml.parent/(leaf+suffix)).exists() or (self.xml.parent/(leaf+suffix)).is_symlink()
                   for leaf in names.values() for suffix in ('','.retired')):
                raise RecoveryRequired('Unowned removal XML sidecar is retained')
            original=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            replacement=-1;marked=False
            try:
                old=os.fstat(original)
                if (not stat.S_ISREG(old.st_mode) or old.st_nlink!=1 or old.st_uid!=os.getuid()
                        or _fd_sha(original)!=hashlib.sha256(current).hexdigest()
                        or _entity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=_entity(old)):
                    raise RecoveryRequired('Playlist XML changed before its owned claim')
                replacement=os.open(names['replacement'],os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                view=memoryview(desired)
                while view:
                    count=os.write(replacement,view)
                    if count<=0:raise OSError('XML recovery write made no progress')
                    view=view[count:]
                os.fsync(replacement);os.fsync(parent);guard()
                marker=dict(format='soundbox-removal-xml-claim-v1',operation_id=plan['operation_id'],source=str(self.xml),
                    parent=list(_entity(os.fstat(parent))),tokens={
                        'original':dict(entity=list(_entity(old)),sha256=hashlib.sha256(current).hexdigest()),
                        'replacement':dict(entity=list(_entity(os.fstat(replacement))),sha256=hashlib.sha256(desired).hexdigest())})
                atomic_bytes(folder/'xml-recovery.json',_json(marker));marked=True
                _fault('xml-claim-recorded');return marker
            finally:
                if replacement>=0:
                    if not marked:
                        try:
                            if _entity(os.stat(names['replacement'],dir_fd=parent,follow_symlinks=False))==_entity(os.fstat(replacement)):
                                os.unlink(names['replacement'],dir_fd=parent)
                        except FileNotFoundError:pass
                    os.close(replacement)
                os.close(original)

    def _move_recovery_leaf(self,parent,source,destination,token,guard,phase):
        self._verify_recovery_leaf(parent,source,token);guard();_fault(phase+'-ready');guard()
        try:exclusive_rename(parent,source,parent,destination)
        except OSError as exc:raise RecoveryRequired('Removal recovery destination is occupied; claims and journal retained') from exc
        try:self._verify_recovery_leaf(parent,destination,token);guard()
        except (OSError,WriteProofError):
            # A foreign leaf arriving after verification is claimed, never
            # discarded. Return it only into an unoccupied original slot.
            try:exclusive_rename(parent,destination,parent,source)
            except OSError:pass
            raise RecoveryRequired('Removal recovery changed during claim; all occupants and journal retained')
        os.fsync(parent);_fault(phase)

    def _settle_xml(self,folder,plan,desired,*,retain_other=False):
        marker=self._xml_marker(folder,plan)
        if marker is None:
            current=self.xml.read_bytes()
            if current not in ((folder/'before.xml').read_bytes(),(folder/'after.xml').read_bytes()):
                raise RecoveryRequired('Native playlist XML changed during disposal recovery')
            if current==desired:return
            marker=self._begin_xml_change(folder,plan,current,desired)
        wanted=hashlib.sha256(desired).hexdigest();names=self._xml_names(plan)
        with _restore_parent(self.xml,_recovery_parent(plan,self.xml)) as (parent,name,guard):
            locations=self._xml_view(parent,plan,marker)
            target=next((key for key,token in marker['tokens'].items() if token['sha256']==wanted),None)
            if target is None:raise RecoveryRequired('Removal XML recovery requests unaccepted bytes')
            current_key=next((key for key,leaf in locations.items() if leaf==name),None)
            if current_key!=target:
                if current_key is not None:
                    self._move_recovery_leaf(parent,name,names[current_key],marker['tokens'][current_key],guard,'xml-original-claimed')
                    locations[current_key]=names[current_key]
                if target not in locations:raise RecoveryRequired('Desired removal XML entity is missing; journal retained')
                self._move_recovery_leaf(parent,locations[target],name,marker['tokens'][target],guard,'xml-replacement-published')
            self._verify_recovery_leaf(parent,name,marker['tokens'][target])
            if retain_other:return
            locations=self._xml_view(parent,plan,marker)
            for key,leaf in locations.items():
                if leaf==name:continue
                retired=names[key]+'.retired'
                if leaf!=retired:self._move_recovery_leaf(parent,leaf,retired,marker['tokens'][key],guard,'xml-retirement-claimed')
                self._verify_recovery_leaf(parent,retired,marker['tokens'][key]);guard();os.unlink(retired,dir_fd=parent);os.fsync(parent)
            guard();self._verify_recovery_leaf(parent,name,marker['tokens'][target])

    def _recover_locked(self):
        if not self.journal.exists():return None
        record,plan,folder=self._record();progress=self._progress(folder,plan)
        _require_recovery_parents(plan,self.xml)
        if not plan.get('native_only'):self._guard_trash()
        # Only this already-validated owned journal admits SQLite's own hot
        # rollback. A read-copy cannot recover spilled uncommitted source pages.
        conn=self.lease._connect()
        try:
            conn.execute('BEGIN');before,after=_edges(plan['changes']);old=_matches(conn,before);new=_matches(conn,after)
        finally:conn.close()
        if not old and not new:raise RecoveryRequired('Native removal is in an unknown state; no rollback performed')
        locations={a['path']:self._trash_location(a,progress['trash'].get(a['path']),verify_bytes=not old) for a in plan['audio']}
        all_trashed=all(locations[a['path']] is not None and not Path(a['path']).exists() for a in plan['audio'])
        committed=new and (not old or progress['committed'] or all_trashed)
        if committed:
            for audio in plan['audio']:
                location=locations[audio['path']]
                if location is not None and (tuple(identity(location.lstat())[:2])!=tuple(audio['epoch'][:2])
                        or _sha(location)!=audio['sha256']):raise RecoveryRequired('Committed disposal audio changed')
        expected_xml=folder/('after.xml' if committed else 'before.xml')
        marker=self._xml_marker(folder,plan)
        if marker is None:
            current_xml=self.xml.read_bytes()
            if current_xml not in ((folder/'before.xml').read_bytes(),(folder/'after.xml').read_bytes()):
                raise RecoveryRequired('Native playlist XML changed during disposal recovery')
        else:
            with _restore_parent(self.xml,_recovery_parent(plan,self.xml)) as (parent,_name,_guard):self._xml_view(parent,plan,marker)
        for resource in plan['resources']:
            claimed=self._resource_claim_present(resource,plan)
            if claimed and (Path(resource['path']).exists() or Path(resource['path']).is_symlink()):
                raise RecoveryRequired('Native resource recovery has a newer live occupant; original claim and journal retained')
        if committed:
            if not all_trashed:raise RecoveryRequired('Native removal committed but audio was restored or moved; preserve the newer file state')
            for resource in plan['resources']:
                path=Path(resource['path'])
                if path.exists() or path.is_symlink():raise RecoveryRequired('Native removal resource reappeared after commit')
        else:
            # Verify the entire recovery set before restoring any original.
            for audio in plan['audio']:
                path=Path(audio['path']);trashed=locations[audio['path']]
                if path.is_symlink():raise RecoveryRequired('Audio recovery path redirects')
                if path.exists():
                    if (trashed or tuple(identity(path.lstat())[:2])!=tuple(audio['epoch'][:2])
                            or _sha(path)!=audio['sha256']):raise RecoveryRequired('Audio recovery occupant changed')
                elif not trashed:raise RecoveryRequired('Interrupted disposal file cannot be located exactly')
                elif tuple(identity(trashed.lstat())[:2])!=tuple(audio['epoch'][:2]):
                    raise RecoveryRequired('Trashed audio is no longer the confirmed original entity')
            for resource in plan['resources']:
                path=Path(resource['path']);backup=Path(resource['backup']);_regular(backup)
                if _sha(backup)!=resource['sha256'] or path.is_symlink() or path.exists() and _sha(path)!=resource['sha256']:
                    raise RecoveryRequired('Native resource recovery differs')
            for audio in plan['audio']:
                path=Path(audio['path']);trashed=locations[audio['path']]
                _restore_audio(audio,trashed,parent_identity=_recovery_parent(plan,path))
            for resource in plan['resources']:
                path=Path(resource['path'])
                if not path.exists():
                    if self._resource_claim_present(resource,plan):
                        claim,token=self._resource_claim(resource,plan)
                        with _restore_parent(path,_recovery_parent(plan,path)) as (parent,name,guard):
                            self._move_recovery_leaf(parent,claim,name,token,guard,'resource-original-restored')
                    else:_restore_absent_bytes(path,Path(resource['backup']).read_bytes(),
                                              parent_identity=_recovery_parent(plan,path))
        for resource in plan['resources']:self._retire_resource_claim(resource,plan)
        self._settle_xml(folder,plan,expected_xml.read_bytes())
        result=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,
            source_file_id=list(self.file_id),operation_id=plan['operation_id'],plan_sha256=record['plan_sha256'],
            selection_sha256=confirmation(plan),outcome='committed' if committed else 'rolled-back',
            trash={path:str(location) for path,location in locations.items() if location is not None},
            inactive_sqlite_journal=_inactive_journal_proof(self.database))
        if plan.get('native_only'):result['native_only']=True
        atomic_bytes(folder/'result.json',_json(result));atomic_bytes(self.root/'removal-completed.json',_json(result))
        atomic_bytes(self.root/'last-result.json',_json(result))
        self.journal.unlink();sync_dir(self.root)
        if committed:self._rotate(folder)
        return result

    def recover(self):
        with self.lease._lock():
            result=self._recover_locked();self._resume_retirement();return result

    def apply(self,folder,confirmed_sha256,confirmed_selection_sha256):
        folder=Path(folder)
        with self.lease._lock():
            self._recover_locked();self._resume_retirement()
            saved=self.completed(folder,confirmed_sha256,confirmed_selection_sha256)
            if saved:return saved
            plan=self._load(folder)
            if (_sha(folder/'plan.json')!=confirmed_sha256 or confirmation(plan)!=confirmed_selection_sha256):
                raise WriteProofError('Delete confirmation does not match this exact plan and selection')
            result=folder/'result.json'
            if result.exists():
                saved=json.loads(result.read_bytes())
                if saved['plan_sha256']!=confirmed_sha256:raise RecoveryRequired('Removal result belongs to another confirmation')
                if saved['outcome']=='committed':return saved
                raise ConflictError('The previous disposal rolled back; prepare and confirm a fresh selection')
            _require_recovery_parents(plan,self.xml)
            if not plan.get('native_only'):
                self._guard_trash()
                if self.trash is None:raise WriteProofError('A recoverable OS Trash adapter is required')
            if identity(self.database.stat())!=plan['database_epoch'] or self._wal_epoch()!=plan['wal_epoch']:
                raise ConflictError('Library changed after deletion review; prepare a fresh review')
            if self.xml.read_bytes()!=(folder/'before.xml').read_bytes():raise ConflictError('Playlist XML changed after deletion review')
            verified_epochs={}
            for retained in plan.get('retained',[]):
                canonical=Path(retained['path'])
                if canonical.resolve(strict=True)!=canonical:raise ConflictError('The retained shared file redirects')
                for ref in retained['references']:
                    if Path(ref['row']['FolderPath']).resolve(strict=True)!=canonical:raise ConflictError('The retained Collection copy’s file alias changed')
            for raw in plan.get('missing',[]):
                path=Path(raw)
                proof=plan.get('missing_proofs',{}).get(raw)
                if proof is not None:
                    changed=_missing_proof(path)!=proof
                else: # Keep previously reviewed plans recoverable.
                    changed=path.exists() or path.is_symlink() or path.parent.resolve()!=path.parent or not path.parent.is_dir()
                if changed:
                    raise ConflictError('A reviewed missing file or its directory changed')
            for audio in plan['audio']:
                path=Path(audio['path'])
                for attempt in range(2):
                    before=identity(_audio_regular(path))
                    try:
                        with acquire_source(SourceContract(path,size=audio['size'],sha256=audio['sha256']),folder) as captured:
                            after=identity(_audio_regular(path))
                            if before==after==identity(captured.observation):
                                verified_epochs[audio['path']]=after;break
                    except SourceAcquisitionError as exc:raise ConflictError('Audio changed after deletion review: '+str(path)) from exc
                    if attempt:raise ConflictError('Audio remained unstable before disposal')
            for resource in plan['resources']:
                path=Path(resource['path']);_regular(path)
                if identity(path.stat())!=resource['epoch'] or _sha(path)!=resource['sha256']:raise ConflictError('Native resource changed after deletion review')
            record=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,source_file_id=list(self.file_id),
                        plan_folder=str(folder),plan_sha256=confirmed_sha256)
            atomic_bytes(self.journal,_json(record));atomic_bytes(folder/'progress.json',_json(dict(trash={},committed=False)))
            _fault('journal')
            conn=None
            try:
                self.lease._guard();conn=self.lease._connect();conn.execute('BEGIN IMMEDIATE')
                if not _matches(conn,_edges(plan['changes'])[0]):raise ConflictError('Native removal rows changed after confirmation')
                progress: dict[str,Any]=dict(trash={},committed=False)
                for index,audio in enumerate(plan['audio']):
                    self.lease._guard();path=Path(audio['path'])
                    with _restore_parent(path,_recovery_parent(plan,path)) as (_parent,_name,parent_guard):
                        if identity(_audio_regular(path))!=verified_epochs[audio['path']]:raise ConflictError('Audio changed immediately before disposal')
                        xattrs.set(path,TAG,audio['tag'].encode())
                        _fault('tagged-'+str(index));parent_guard()
                        destination=Path(self.trash(path));parent_guard()
                        _fault('trashed-'+str(index))
                    progress['trash'][audio['path']]=str(destination);atomic_bytes(folder/'progress.json',_json(progress))
                    if self._trash_location(audio,str(destination))!=destination:raise RecoveryRequired('OS Trash did not return the exact tagged file')
                for index,resource in enumerate(plan['resources']):
                    self.lease._guard();path=Path(resource['path'])
                    if (path.resolve()!=path or _sha(path)!=resource['sha256'] or
                            _sha(Path(resource['backup']))!=resource['sha256']):raise ConflictError('Native resource or recovery changed before removal')
                    # Claim first: an external leaf arriving after verification
                    # is returned/preserved, never unlinked as our resource.
                    self._remove_resource(resource,plan);_fault('resource-'+str(index))
                self._settle_xml(folder,plan,(folder/'after.xml').read_bytes(),retain_other=True);_fault('xml')
                self.lease._guard();conn.set_authorizer(_authorizer);_apply(conn,plan['changes']);_fault('sql')
                if not _matches(conn,_edges(plan['changes'])[1]):raise WriteProofError('Native removal final rows differ')
                self.lease._guard();conn.commit();conn.close();conn=None;_fault('commit')
                progress['committed']=True;atomic_bytes(folder/'progress.json',_json(progress))
                saved=self._recover_locked()
                if not saved or saved['outcome']!='committed':raise RecoveryRequired('Removal could not be independently confirmed')
                return saved
            except BaseException:
                if conn is not None:conn.rollback();conn.close();conn=None
                self._recover_locked();raise
            finally:
                if conn is not None:conn.close()

    def completed(self,folder,plan_sha256,selection_sha256):
        """Small immutable receipts permit replay after older full DB retirement."""
        folder=Path(folder)
        if (folder.resolve()!=folder or folder.parent!=self.data_root/'native-removal-plans'
                or str(UUID(folder.name))!=folder.name):raise RecoveryRequired('Removal receipt location changed')
        path=folder/'result.json'
        if not path.exists():return None
        _regular(path);_regular(folder/'plan.json');result=json.loads(path.read_bytes())
        if (result.get('format')!=FORMAT or result.get('source')!=str(self.database)
                or result.get('library_id')!=self.authority.library_id or result.get('operation_id')!=folder.name
                or result.get('plan_sha256')!=plan_sha256 or _sha(folder/'plan.json')!=plan_sha256
                or result.get('selection_sha256')!=selection_sha256):raise RecoveryRequired('Removal receipt differs from its confirmation')
        if result.get('outcome')!='committed':raise ConflictError('The previous disposal rolled back; prepare and confirm a fresh selection')
        return result

    def verify_removed(self,folder,plan_sha256,selection_sha256):
        """Fresh active-row absence, without inspecting any original/media asset."""
        folder=Path(folder)
        with self.lease._lock():
            self._recover_locked();self.lease._guard()
            result=self.completed(folder,plan_sha256,selection_sha256)
            if result is None:raise RecoveryRequired('Collection removal has no committed receipt')
            _regular(folder/'plan.json');plan=json.loads((folder/'plan.json').read_bytes())
            if not plan.get('native_only'):raise WriteProofError('Native-only readback requires its exact file policy')
            with self._snapshot() as snapshot:
                conn=_connect(snapshot,readonly=True)
                try:
                    if (_identity(conn)!=(self.authority.library_id,'6000')
                            or _schema(conn)!=self.authority.schema_sha256):
                        raise WriteProofError('Native removal library/schema changed')
                    for target in plan['targets']:
                        row=_row(conn,'djmdContent',target['id'])
                        if row is not None and (row['UUID']!=target['uuid'] or row['rb_local_deleted']!=1):
                            raise ConflictError('The removed Collection identity was restored or replaced')
                        if conn.execute('SELECT count(*) FROM djmdContent WHERE UUID=? AND rb_local_deleted=0',
                                (target['uuid'],)).fetchone()!=(0,):
                            raise ConflictError('The selected native UUID is still active in Collection')
                finally:conn.close()
            self.lease._guard()
            return result

    def discard_unstarted(self,folder,plan_sha256):
        """Retire only exact generated review copies; never original files/data."""
        folder=Path(folder)
        # No source lease: this does not inspect or mutate the source. A review
        # can be cancelled while native apps open, but never after admission.
        self._load(folder)
        if _sha(folder/'plan.json')!=plan_sha256 or (folder/'progress.json').exists() or (folder/'result.json').exists():
            raise RecoveryRequired('A started removal cannot be discarded as a review')
        plan=self._plan_cache[1]
        allowed={'plan.json','before.db','after.db','before.xml','after.xml','resources'}
        if any(p.name not in allowed for p in folder.iterdir()):raise RecoveryRequired('Unknown files in removal review')
        resources=folder/'resources'
        if resources.exists():
            expected={Path(r['backup']) for r in plan['resources']}
            if set(resources.iterdir())!=expected:raise RecoveryRequired('Unknown review resource backup')
            for p in expected:_regular(p);p.unlink()
            resources.rmdir()
        for name in allowed-{'resources'}:(folder/name).unlink()
        folder.rmdir();sync_dir(folder.parent);self._plan_cache=None

    def _retention(self):
        path=self.root/'removal-latest.json'
        if not path.exists():return None
        _regular(path);value=json.loads(path.read_bytes())
        if (set(value)!={'format','source','library_id','current','retiring'} or value['format']!='soundbox-removal-retention-v1'
                or value['source']!=str(self.database) or value['library_id']!=self.authority.library_id):
            raise RecoveryRequired('Unknown disposal backup retention')
        for entry in (value['current'],value['retiring']):
            if entry is None:continue
            folder=self.data_root/'native-removal-plans'/entry['id']
            if set(entry)!={'id','sha256','selection_sha256'} or not self.completed(folder,entry['sha256'],entry['selection_sha256']):
                raise RecoveryRequired('Disposal retention lacks its committed receipt')
        if value['retiring']==value['current']:raise RecoveryRequired('Latest disposal recovery cannot retire itself')
        return value

    def _resume_retirement(self):
        value=self._retention()
        if not value or not value['retiring']:return
        if self.journal.exists():raise RecoveryRequired('Resolve disposal before retiring old backups')
        entry=value['retiring'];folder=self.data_root/'native-removal-plans'/entry['id']
        plan=json.loads((folder/'plan.json').read_bytes());marker=folder/'retired.json'
        expected=dict(format='soundbox-retired-removal-payloads-v1',plan_sha256=entry['sha256'],
            payloads={name:plan['payloads'][name] for name in ('before.db','after.db')})
        if marker.exists():
            _regular(marker)
            if json.loads(marker.read_bytes())!=expected:raise RecoveryRequired('Unknown retired disposal payloads')
        else:
            for name,digest in expected['payloads'].items():
                if _sha(folder/name)!=digest:raise RecoveryRequired('Disposal backup changed before retirement')
            atomic_bytes(marker,_json(expected))
        for name,digest in expected['payloads'].items():
            p=folder/name
            if not p.exists() and not p.is_symlink():continue
            _regular(p)
            if _sha(p)!=digest:raise RecoveryRequired('Disposal backup changed during retirement')
            p.unlink()
        sync_dir(folder);atomic_bytes(self.root/'removal-latest.json',_json({**value,'retiring':None}))

    def _rotate(self,folder):
        prior=self._retention();plan=json.loads((folder/'plan.json').read_bytes())
        entry=dict(id=folder.name,sha256=_sha(folder/'plan.json'),selection_sha256=confirmation(plan))
        if prior and prior['current']==entry:self._resume_retirement();return
        value=dict(format='soundbox-removal-retention-v1',source=str(self.database),library_id=self.authority.library_id,
            current=entry,retiring=prior['current'] if prior else None)
        atomic_bytes(self.root/'removal-latest.json',_json(value));self._resume_retirement()
