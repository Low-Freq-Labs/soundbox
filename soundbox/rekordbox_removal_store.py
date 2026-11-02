"""Confirmed Collection + recoverable file disposal on the shared source lane.

Requires its own accepted library/profile authority and an exact prepared plan.
No source-audio disposal occurs during preparation. GUI confirmation binds the
whole plan hash; each file goes through the OS Trash with an interruption tag.
"""
from dataclasses import dataclass,asdict
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

FORMAT='soundbox-native-removal-v1'
AUTHORITY_FORMAT='soundbox-removal-authority-v1'
TAG='com.lowfreqlabs.soundbox.removal'
def confirmation(plan):
    return _digest(dict(selections=plan['selections'],audio=[{k:a[k] for k in ('path','size','sha256')} for a in plan['audio']]))


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
        _regular(self.store.xml)
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
        cache_key=(str(folder),versions)
        if self._plan_cache is not None and self._plan_cache[0]==cache_key:return self._plan_cache[1]
        plan=json.loads(path.read_bytes())
        if (plan.get('format')!=FORMAT or plan.get('authority')!=asdict(self.authority)
                or plan.get('operation_id')!=folder.name or plan.get('source_file_id')!=list(self.file_id)):
            raise RecoveryRequired('Removal plan does not match its explicit authority/source')
        for name in ('before.db','after.db','before.xml','after.xml'):
            p=folder/name;_regular(p)
            if _sha(p)!=plan['payloads'][name]:raise RecoveryRequired('Native removal recovery bytes changed')
        if {t['id'] for t in plan['targets']}!={s['native_id'] for s in plan['selections'] if s['native_active']}:
            raise RecoveryRequired('Native removal targets differ from the reviewed selection')
        if ({a['path'] for a in plan['audio']}|set(plan.get('missing',[]))|{r['path'] for r in plan.get('retained',[])})!={s['path'] for s in plan['selections']}:
            raise RecoveryRequired('Audio disposal targets differ from the reviewed selection')
        with tempfile.TemporaryDirectory(prefix='soundbox-removal-validation-') as temp:
            local=Path(temp).resolve()/'master.db';shutil.copyfile(folder/'before.db',local);conn=_connect(local,readonly=True)
            try:
                tables=self._catalog(conn)
                native_rows={r['ID']:r for r in tables['djmdContent']}
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
                for row in tables['contentFile']:
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
        for index,resource in enumerate(plan['resources']):
            expected=folder/'resources'/str(index)
            if Path(resource['backup'])!=expected or expected.resolve()!=expected or _sha(expected)!=resource['sha256']:
                raise RecoveryRequired('Removal native-resource backup changed')
        self._plan_cache=(cache_key,plan,_sha(path))
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

    def prepare(self,entries):
        """Capture only; originals remain untouched until exact GUI confirmation."""
        if not isinstance(entries,list) or not 1<=len(entries)<=10000:raise WriteProofError('Select tracks to delete')
        with self.lease._lock():
            self._recover_locked();self._resume_retirement();self.lease._guard()
            database_epoch=identity(self.database.stat());wal_epoch=self._wal_epoch();before_xml=self.xml.read_bytes()
            op=str(uuid4());folder=self.data_root/'native-removal-plans'/op;folder.mkdir(parents=True,mode=0o700)
            with self._snapshot() as snapshot:
                conn=_connect(snapshot,readonly=True)
                try:
                    tables=self._catalog(conn);all_content=tables['djmdContent'];by_id={r['ID']:r for r in all_content}
                    targets=[];audio=[];selections=[];seen_paths=set();selected_native=set();missing=[];missing_proofs={}
                    for entry in entries:
                        tid=entry['id'];native_id=entry['native_id'];row=by_id.get(native_id)
                        known=entry.get('native',{})
                        if not entry.get('path'):raise WriteProofError('This Graveyard entry has no verified file path')
                        if row and row['UUID']!=known.get('uuid'):raise ConflictError('Graveyard native identity changed')
                        if row and not row['rb_local_deleted']:
                            if row['rb_local_synced']==0 and not self.authority.unsynced_accepted:
                                raise WriteProofError('Newer native-record removal awaits acceptance')
                            targets.append(dict(id=native_id,uuid=row['UUID'],path=row['FolderPath']));selected_native.add(native_id)
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
                    for row in all_content:
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
                    for selected in selections:selected['keep_file']=selected['path'] in retained_by_path
                    built=planner.build(tables,targets,before_xml,compact=True)
                    changes=built['changes']
                    resources: list[dict]=[];share=self.database.parent/'share'
                    for row in tables['contentFile']:
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
                    plan=dict(format=FORMAT,operation_id=op,authority=asdict(self.authority),source_file_id=list(self.file_id),
                        changes=changes,compacted=True,audio=audio,missing=missing,missing_proofs=missing_proofs,retained=list(retained_by_path.values()),
                        resources=resources,selections=selections,playlists=built['playlists'],
                        targets=targets,timestamp=built['timestamp'],xml_clocks=built['xml_clocks'],
                        playlist_labels=[next((r['Name'] for r in tables['djmdPlaylist'] if r['ID']==pid),pid) for pid in built['playlists']],
                        database_epoch=database_epoch,wal_epoch=wal_epoch,
                        payloads={name:_sha(folder/name) for name in ('before.db','after.db','before.xml','after.xml')})
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

    @staticmethod
    def _restore_tag(path,old):
        if old is None:
            try:xattrs.remove(path,TAG)
            except OSError as exc:
                import errno
                if exc.errno not in (getattr(errno,'ENOATTR',93),getattr(errno,'ENODATA',61)):raise
        else:xattrs.set(path,TAG,base64.b64decode(old,validate=True))

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
        if source.is_file() and not known:return None
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

    def _recover_locked(self):
        if not self.journal.exists():return None
        record,plan,folder=self._record();progress=self._progress(folder,plan)
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
                if location is not None and _sha(location)!=audio['sha256']:raise RecoveryRequired('Committed disposal audio changed')
        expected_xml=folder/('after.xml' if committed else 'before.xml')
        current_xml=self.xml.read_bytes()
        if current_xml not in ((folder/'before.xml').read_bytes(),(folder/'after.xml').read_bytes()):
            raise RecoveryRequired('Native playlist XML changed during disposal recovery')
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
                    if trashed or _sha(path)!=audio['sha256']:raise RecoveryRequired('Audio recovery occupant changed')
                elif not trashed:raise RecoveryRequired('Interrupted disposal file cannot be located exactly')
            for resource in plan['resources']:
                path=Path(resource['path']);backup=Path(resource['backup']);_regular(backup)
                if _sha(backup)!=resource['sha256'] or path.is_symlink() or path.exists() and _sha(path)!=resource['sha256']:
                    raise RecoveryRequired('Native resource recovery differs')
            for audio in plan['audio']:
                path=Path(audio['path']);trashed=locations[audio['path']]
                if trashed is not None:os.rename(trashed,path);sync_dir(path.parent)
                self._restore_tag(path,audio['original_tag'])
            for resource in plan['resources']:
                path=Path(resource['path'])
                if not path.exists():
                    path.parent.mkdir(parents=True,exist_ok=True);atomic_bytes(path,Path(resource['backup']).read_bytes())
        if current_xml!=expected_xml.read_bytes():atomic_bytes(self.xml,expected_xml.read_bytes())
        result=dict(format=FORMAT,source=str(self.database),library_id=self.authority.library_id,
            source_file_id=list(self.file_id),operation_id=plan['operation_id'],plan_sha256=record['plan_sha256'],
            selection_sha256=confirmation(plan),outcome='committed' if committed else 'rolled-back',
            trash={path:str(location) for path,location in locations.items() if location is not None},
            inactive_sqlite_journal=_inactive_journal_proof(self.database))
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
                    if identity(_audio_regular(path))!=verified_epochs[audio['path']]:raise ConflictError('Audio changed immediately before disposal')
                    xattrs.set(path,TAG,audio['tag'].encode())
                    _fault('tagged-'+str(index))
                    destination=Path(self.trash(path))
                    _fault('trashed-'+str(index))
                    progress['trash'][audio['path']]=str(destination);atomic_bytes(folder/'progress.json',_json(progress))
                    if self._trash_location(audio,str(destination))!=destination:raise RecoveryRequired('OS Trash did not return the exact tagged file')
                for index,resource in enumerate(plan['resources']):
                    self.lease._guard();path=Path(resource['path'])
                    if (path.resolve()!=path or _sha(path)!=resource['sha256'] or
                            _sha(Path(resource['backup']))!=resource['sha256']):raise ConflictError('Native resource or recovery changed before removal')
                    # Verified exact backup already exists; preserve it permanently.
                    path.unlink();sync_dir(path.parent);_fault('resource-'+str(index))
                atomic_bytes(self.xml,(folder/'after.xml').read_bytes());_fault('xml')
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
