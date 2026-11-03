"""Identity-bound regular-playlist DB/XML transactions.

Only playlist/membership rows and the native local counter may change. Source
reads use encrypted private snapshots; the writer reads exact target identities.
XML publishes before SQL commits. Shared Tag/Cue/Grid lock/journal recovery
admits only the exact before/before, before/after or after/after affected state.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import secrets
from uuid import uuid4, UUID
import tempfile

from . import rekordbox_playlist_plan as planner, rekordbox_playlist_xml as xml
from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from .rekordbox_tags import NATIVE_VERSION, _schema
from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect, _identity, _inactive_journal_proof, _integrity,
    _json, _quote, _regular, _rows, encrypted_backup,
)
from .state_io import atomic_bytes, copy_state, sync_dir

FORMAT = 'soundbox-native-playlists-v1'
PAYLOADS = ('before.db','after.db','before.xml','after.xml')
MAX_BATCH_COMMANDS = 64


def command_requests(request):
    """Validated bounded commands; batches never widen the native write scope."""
    return request['requests'] if request.get('operation')=='batch' else [request]


def batch_for(catalog,requests):
    request=dict(operation_id=uuid4().hex,operation='batch',revision=catalog['revision'],requests=deepcopy(requests))
    validate_request(request)
    # Independent native XML clocks must advance even when create+append are
    # captured in one GUI millisecond. Capture this BEFORE durable admission;
    # publication/recovery consume these exact child requests unchanged.
    previous=0
    for child in request['requests']:
        child['xml_timestamp']=max(child['xml_timestamp'],previous+1)
        previous=child['xml_timestamp']
    validate_request(request)
    return request


@dataclass(frozen=True)
class PlaylistAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted: bool
    application_version: str = NATIVE_VERSION
    # Acceptance is explicit; selecting a database or an old pilot receipt never
    # manufactures permission. Optional pilot bindings narrow first publication.
    pilot_playlist_ids: tuple[str, ...] = ()
    # Native acceptance keeps its complete pairs. Normal editing keeps the
    # latest verified full pair plus permanent row/XML/operation receipts.
    retain_full_backups: bool = False
    # Reordering has its own native reference/acceptance. Existing CRUD
    # authorities remain readable and never silently acquire this capability.
    reorder_accepted: bool = False

    def __post_init__(self):
        if self.accepted is not True:
            raise WriteProofError('Playlist writing requires native acceptance')
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,
                          'playlist-authority',self.application_version)
        if (type(self.retain_full_backups) is not bool or type(self.reorder_accepted) is not bool
                or not isinstance(self.pilot_playlist_ids,tuple)
                or any(not isinstance(v,str) or not v for v in self.pilot_playlist_ids)):
            raise WriteProofError('Invalid playlist pilot bindings')


def load_authority(path: Path, source: Path, library_id: str):
    try:
        _regular(path);data=json.loads(path.read_bytes())
        data['pilot_playlist_ids']=tuple(data.get('pilot_playlist_ids',()))
        result=PlaylistAuthority(**data)
        return result if result.database==str(source) and result.library_id==library_id else None
    except (OSError,ValueError,KeyError,TypeError,WriteProofError):
        return None


def _sha(path):
    _regular(path)
    def identity(info):
        return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns
    before=identity(path.stat())
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        if identity(os.fstat(stream.fileno()))!=before:
            raise RecoveryRequired('Playlist evidence changed before hashing')
        result=hashlib.file_digest(stream,'sha256').hexdigest()
        if identity(os.fstat(stream.fileno()))!=before or identity(path.stat())!=before:
            raise RecoveryRequired('Playlist evidence changed during hashing')
    return result


def authorizer(action, table, column, database, trigger):
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        if trigger or database!='main':return sqlite3.SQLITE_DENY
        if table in ('djmdPlaylist','djmdSongPlaylist'):return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE and table=='agentRegistry' and column=='int_1':
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,
                  sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def row(conn, table, identity):
    if table not in ('djmdPlaylist','djmdSongPlaylist','agentRegistry','djmdContent'):
        raise WriteProofError('Unknown playlist read table')
    key='registry_id' if table=='agentRegistry' else 'ID'
    cursor=conn.execute(f'SELECT * FROM {_quote(table)} WHERE {_quote(key)}=?',(identity,))
    names=[v[0] for v in cursor.description];rows=cursor.fetchall()
    if len(rows)>1:raise ConflictError('Ambiguous native row identity')
    return dict(zip(names,rows[0],strict=True)) if rows else None


def apply_changes(conn, changes):
    for change in changes:
        table,key=change['table'],change['id']
        if (table not in ('djmdPlaylist','djmdSongPlaylist','agentRegistry')
                or table=='agentRegistry' and key!='localUpdateCount'):
            raise WriteProofError('Playlist plan exceeds its allowed tables')
        before,after=change['before'],change['after']
        if before==after or row(conn,table,key)!=before:
            raise ConflictError('Playlist target changed before save')
        pk='registry_id' if table=='agentRegistry' else 'ID'
        if after is None:
            conn.execute(f'DELETE FROM {_quote(table)} WHERE {_quote(pk)}=?',(key,))
        elif before is None:
            columns=list(after)
            conn.execute(f'INSERT INTO {_quote(table)} ({",".join(_quote(k) for k in columns)}) '
                         f'VALUES ({",".join("?" for _ in columns)})',[after[k] for k in columns])
        else:
            if set(before)!=set(after) or after[pk]!=key:
                raise WriteProofError('Playlist row/schema changed')
            columns=[k for k in after if before[k]!=after[k]]
            conn.execute(f'UPDATE {_quote(table)} SET {",".join(_quote(k)+"=?" for k in columns)} '
                         f'WHERE {_quote(pk)}=?',[after[k] for k in columns]+[key])


def _affected(changes):
    first: dict[tuple[str,str],dict|None] = {}
    final: dict[tuple[str,str],dict|None] = {}
    for change in changes:
        key=(change['table'],change['id'])
        first.setdefault(key,change['before']);final[key]=change['after']
    return first,final


def _matches(conn, expected):
    return all(row(conn,table,key)==value for (table,key),value in expected.items())


class _Lease(PilotCueStore):
    def __init__(self, store):
        self.store=store;a=store.authority
        super().__init__(CueWriteAuthority(a.database,a.library_id,a.schema_sha256,
                         'playlist-transaction',a.application_version),store.root)

    def _record(self):
        return self.store._record()

    def _guard(self):
        super()._guard()
        info=self.store.database.stat()
        if (info.st_dev,info.st_ino)!=self.store.file_id:
            raise ConflictError('Native playlist database was replaced during this operation')
        self.store._guard_xml()


class PlaylistStore:
    def __init__(self, authority: PlaylistAuthority, recovery_root: Path):
        self.authority=authority;self.database=Path(authority.database)
        self.root=Path(recovery_root).absolute()
        self.xml=self.database.with_name('masterPlaylists6.xml')
        self.journal=self.root/'transaction.json'
        info=self.database.stat();self.file_id=(info.st_dev,info.st_ino)
        self.lease=_Lease(self)
        self._verified_recovery_record_key: tuple[str,PlaylistAuthority]|None=None

    def _guard_xml(self):
        _regular(self.xml)
        if self.xml!=self.xml.resolve() or self.xml.stat().st_uid!=os.getuid():
            raise WriteProofError('Playlist XML redirects or has another owner')
        xml.identities(self.xml.read_bytes())

    def _record(self):
        try:
            _regular(self.journal);record=json.loads(self.journal.read_bytes())
            info=self.database.stat();a=self.authority
            if (record['format']!=FORMAT or record['source']!=str(self.database)
                    or record['library_id']!=a.library_id or record['schema_sha256']!=a.schema_sha256
                    or record['application_version']!=a.application_version
                    or record['source_file_id']!=[info.st_dev,info.st_ino]
                    or set(record['payload_sha256'])!=set(PAYLOADS)):
                raise RecoveryRequired('Unrecognized native playlist recovery identity')
            archive=self.root/record['archive']
            if (archive.parent!=self.root or not archive.name.startswith('playlist-')
                    or archive!=archive.resolve() or archive.stat().st_mode&0o077):
                raise RecoveryRequired('Playlist recovery archive redirects')
            for name in PAYLOADS:
                if _sha(archive/name)!=record['payload_sha256'][name]:
                    raise RecoveryRequired('Playlist recovery backup failed verification')
            _regular(archive/'operation.json')
            if json.loads((archive/'operation.json').read_bytes())!=record:
                raise RecoveryRequired('Playlist recovery record differs from its archive')
            self._check_scope(record['request'])
            semantic_key=(hashlib.sha256(_json(record)).hexdigest(),a)
            if semantic_key==self._verified_recovery_record_key:return record
            # Validate all evidence and intent on a fresh TEMP copy before the
            # live SQL connection is allowed to recover SQLite's own journal.
            with tempfile.TemporaryDirectory(prefix='soundbox-playlist-recovery-') as area:
                before=Path(area).resolve()/'before.db';shutil.copyfile(archive/'before.db',before)
                conn=_connect(before,readonly=True)
                try:
                    if (_identity(conn)!=(a.library_id,'6000') or _schema(conn)!=a.schema_sha256):
                        raise RecoveryRequired('Playlist backup library/schema does not match its authority')
                    expected=build_plan(conn,record['request'],(archive/'before.xml').read_bytes())
                    self._check_changes(expected['changes'])
                    if (expected['changes']!=record['changes']
                            or expected['xml']!=(archive/'after.xml').read_bytes()):
                        raise RecoveryRequired('Playlist recovery exceeds its original intent')
                finally:conn.close()
            # Only the expensive pure semantic reconstruction is reused. Every
            # invocation above still verifies ALL current payload bytes, exact
            # journal/archive equality, source identity and authority. A changed
            # byte/record cannot hit this one-entry instance-local memo.
            self._verified_recovery_record_key=semantic_key
            return record
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise RecoveryRequired('Playlist recovery is incomplete; evidence preserved') from exc

    def _recover_locked(self):
        if not self.journal.exists() and not self.journal.is_symlink():return None
        record=self._record();self._guard_xml();archive=self.root/record['archive']
        current_xml=_sha(self.xml)
        if current_xml not in (record['payload_sha256']['before.xml'],record['payload_sha256']['after.xml']):
            raise RecoveryRequired('Native playlist XML changed after interrupted save')
        conn=self.lease._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            first,final=_affected(record['changes']);self.lease._guard()
            if _sha(self.xml)!=current_xml:
                raise RecoveryRequired('Playlist XML changed during recovery')
            if _matches(conn,final) and current_xml==record['payload_sha256']['after.xml']:
                outcome='committed'
            elif _matches(conn,first):
                if current_xml!=record['payload_sha256']['before.xml']:
                    atomic_bytes(self.xml,(archive/'before.xml').read_bytes())
                outcome='rolled-back'
            else:raise RecoveryRequired('Native playlist state is outside the exact recovery pair')
            conn.rollback()
        finally:conn.close()
        self.lease._guard()
        atomic_bytes(self.root/'last-result.json',_json(
            {**record,'outcome':outcome,'inactive_sqlite_journal':_inactive_journal_proof(self.database)}))
        atomic_bytes(archive/'result.json',_json({**record,'outcome':outcome}))
        self.journal.unlink();sync_dir(self.root)
        if outcome=='committed':self._rotate_backups(archive)
        return outcome

    def recover(self):
        with self.lease._lock():return self._recover_locked()

    def apply(self, request):
        """Publish one exact durable user intent; replay is operation-ID idempotent."""
        validate_request(request)
        with self.lease._lock():
            self._recover_locked();self._guard_xml()
            self._resume_retirement()
            archive=self.root/('playlist-'+request['operation_id'])
            if archive.exists():
                _regular(archive/'operation.json');prior=json.loads((archive/'operation.json').read_bytes())
                if prior['request']!=request:
                    raise RecoveryRequired('Playlist operation identity was reused')
                _regular(archive/'result.json');result=json.loads((archive/'result.json').read_bytes())
                if result=={**prior,'outcome':'committed'}:
                    self._rotate_backups(archive)
                    return self.snapshot()
                raise ConflictError('This playlist attempt rolled back; refresh and retry explicitly')
            self._check_scope(request)
            before_xml=self.xml.read_bytes()
            with encrypted_backup(self.database) as before:
                conn=_connect(before,readonly=True)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise ConflictError('Native playlist library/schema changed')
                    plan=build_plan(conn,request,before_xml)
                    self._check_changes(plan['changes'])
                finally:conn.close()
                if not plan['changes']:return plan['catalog']
                archive.mkdir(mode=0o700);copy_state(before,archive/'before.db')
                atomic_bytes(archive/'before.xml',before_xml)
                with tempfile.TemporaryDirectory(prefix='soundbox-playlist-after-') as area:
                    after=Path(area).resolve()/'after.db';shutil.copyfile(before,after)
                    conn=_connect(after)
                    try:
                        conn.execute('BEGIN IMMEDIATE');conn.set_authorizer(authorizer)
                        apply_changes(conn,plan['changes']);conn.set_authorizer(None);conn.commit();_integrity(conn)
                    finally:conn.close()
                    copy_state(after,archive/'after.db')
                atomic_bytes(archive/'after.xml',plan['xml'])
            info=self.database.stat()
            record=dict(format=FORMAT,source=str(self.database),source_file_id=[info.st_dev,info.st_ino],
                library_id=self.authority.library_id,schema_sha256=self.authority.schema_sha256,
                application_version=self.authority.application_version,archive=archive.name,request=request,
                changes=plan['changes'],retain_full_backups=self.authority.retain_full_backups,
                retention_predecessor_sha256=(_sha(self.root/'playlist-latest.json')
                    if (self.root/'playlist-latest.json').exists() else None),
                payload_sha256={name:_sha(archive/name) for name in PAYLOADS})
            atomic_bytes(archive/'operation.json',_json(record));sync_dir(archive);sync_dir(self.root)
            conn=self.lease._connect()
            try:
                conn.execute('BEGIN IMMEDIATE');self.lease._guard()
                if _sha(self.xml)!=record['payload_sha256']['before.xml']:
                    raise ConflictError('Native playlist XML changed before publication')
                # The recorded counter and exact target rows bind the private
                # plan to the live source. SQL cannot mutate unrelated domains.
                first,final=_affected(plan['changes'])
                if not _matches(conn,first):raise ConflictError('Native playlist selection changed before save')
                for selected in (track for command in command_requests(request) for track in command['tracks']):
                    track=row(conn,'djmdContent',selected['track_id'])
                    if not track or track['UUID']!=selected['uuid'] or track['rb_local_deleted']!=0:
                        raise ConflictError('Native Collection track changed before save')
                atomic_bytes(self.journal,_json(record));_fault('journal-ready')
                conn.set_authorizer(authorizer);apply_changes(conn,plan['changes'])
                if not _matches(conn,final):raise WriteProofError('Native playlist target readback failed')
                _fault('sql-applied');self.lease._guard()
                if record['payload_sha256']['before.xml']!=record['payload_sha256']['after.xml']:
                    atomic_bytes(self.xml,plan['xml'])
                _fault('xml-published')
                self.lease._guard()
                if _sha(self.xml)!=record['payload_sha256']['after.xml']:
                    raise ConflictError('Native playlist XML changed before commit')
                conn.commit();_fault('sql-committed')
            except BaseException:
                conn.rollback();raise
            finally:conn.close()
            if self._recover_locked()!='committed':raise RecoveryRequired('Native playlist save did not verify')
            self._rotate_backups(archive)
            return self.snapshot()

    def _retention_record(self):
        path=self.root/'playlist-latest.json'
        if not path.exists() and not path.is_symlink():return None
        _regular(path);record=json.loads(path.read_bytes());a=self.authority
        if (record.get('format')!='soundbox-playlist-retention-v1'
                or record.get('source')!=str(self.database) or record.get('library_id')!=a.library_id
                or record.get('schema_sha256')!=a.schema_sha256
                or record.get('application_version')!=a.application_version):
            raise RecoveryRequired('Unknown playlist backup retirement; evidence preserved')
        return record

    def _receipt(self,entry):
        archive=self.root/entry['archive']
        if archive.parent!=self.root or archive!=archive.resolve():
            raise RecoveryRequired('Playlist retirement archive redirects')
        operation=archive/'operation.json';_regular(operation)
        if _sha(operation)!=entry['operation_sha256']:
            raise RecoveryRequired('Playlist retirement operation changed')
        record=json.loads(operation.read_bytes());_regular(archive/'result.json')
        result=json.loads((archive/'result.json').read_bytes());a=self.authority
        if (record.get('format')!=FORMAT or record.get('source')!=str(self.database)
                or record.get('library_id')!=a.library_id or record.get('schema_sha256')!=a.schema_sha256
                or record.get('application_version')!=a.application_version
                or result!={**record,'outcome':'committed'}):
            raise RecoveryRequired('Playlist retirement lacks an exact committed receipt')
        return archive,record

    def _resume_retirement(self):
        if self.authority.retain_full_backups:return
        pointer=self._retention_record()
        if pointer is None:return
        self._receipt(pointer['current'])
        previous=pointer.get('retiring')
        if previous is None:return
        if self.journal.exists():raise RecoveryRequired('Finish source recovery before backup retirement')
        archive,record=self._receipt(previous)
        if record.get('retain_full_backups') is not False:
            raise RecoveryRequired('Native acceptance backups are protected from retirement')
        marker=archive/'retired.json'
        expected=dict(format='soundbox-retired-playlist-payloads-v1',operation_sha256=previous['operation_sha256'],
                      payload_sha256={name:record['payload_sha256'][name] for name in ('before.db','after.db')})
        if marker.exists() or marker.is_symlink():
            _regular(marker)
            if json.loads(marker.read_bytes())!=expected:raise RecoveryRequired('Unknown retired playlist payloads')
        else:
            for name in expected['payload_sha256']:
                if _sha(archive/name)!=expected['payload_sha256'][name]:
                    raise RecoveryRequired('Completed playlist backup changed; it will not be deleted')
            atomic_bytes(marker,_json(expected))
        for name in expected['payload_sha256']:
            path=archive/name
            if not path.exists() and not path.is_symlink():continue
            if _sha(path)!=expected['payload_sha256'][name]:
                raise RecoveryRequired('Playlist backup changed during retirement')
            path.unlink()
        sync_dir(archive)
        atomic_bytes(self.root/'playlist-latest.json',_json({**pointer,'retiring':None}))

    def _rotate_backups(self,archive):
        if self.authority.retain_full_backups:return
        prior=self._retention_record()
        entry=dict(archive=archive.name,operation_sha256=_sha(archive/'operation.json'))
        _,record=self._receipt(entry)
        if record.get('retain_full_backups') is not False or (archive/'retired.json').exists():return
        if prior and prior['current']==entry:
            self._resume_retirement();return
        predecessor=record.get('retention_predecessor_sha256')
        current_hash=_sha(self.root/'playlist-latest.json') if prior else None
        if predecessor!=current_hash:return  # Replaying an older committed intent cannot retire a newer pair.
        a=self.authority
        pointer=dict(format='soundbox-playlist-retention-v1',source=str(self.database),library_id=a.library_id,
                     schema_sha256=a.schema_sha256,application_version=a.application_version,
                     current=entry,retiring=prior['current'] if prior else None)
        atomic_bytes(self.root/'playlist-latest.json',_json(pointer));self._resume_retirement()

    def prepare_retry(self,request):
        """Explicit renewed intent, or unchanged receipt replay after a local crash.

        The caller must persist this returned request before publishing it. No
        new operation is generated for an already committed source transaction.
        """
        validate_request(request)
        with self.lease._lock():
            self._recover_locked();self._check_scope(request)
            archive=self.root/('playlist-'+request['operation_id'])
            if archive.exists():
                _regular(archive/'operation.json');record=json.loads((archive/'operation.json').read_bytes())
                if record['request']!=request:raise RecoveryRequired('Playlist retry identity changed')
                _regular(archive/'result.json');result=json.loads((archive/'result.json').read_bytes())
                if result=={**record,'outcome':'committed'}:return request
                if result!={**record,'outcome':'rolled-back'}:
                    raise RecoveryRequired('Unknown playlist retry outcome; evidence preserved')
            current=self.snapshot()
            stamp,clock=_timestamps()
            if request['operation']=='batch':
                children=[{**child,'operation_id':uuid4().hex,'revision':current['revision'],
                           'timestamp':stamp,'survivor_timestamp':stamp,'xml_timestamp':clock}
                          for child in request['requests']]
                return batch_for(current,children)
            return {**request,'operation_id':uuid4().hex,'revision':current['revision'],
                    'timestamp':stamp,'survivor_timestamp':stamp,'xml_timestamp':clock}

    def _check_scope(self, request):
        if request.get('operation')=='batch':
            for child in request['requests']:self._check_scope(child)
            return
        if request.get('operation')=='reorder' and not self.authority.reorder_accepted:
            raise WriteProofError('Native playlist reordering requires separate acceptance')
        bound=self.authority.pilot_playlist_ids
        if bound and request.get('playlist_id') not in bound:
            raise WriteProofError('This acceptance pilot binds a different playlist')

    def _check_changes(self,changes):
        bound=set(self.authority.pilot_playlist_ids)
        if not bound:return
        for change in changes:
            old,new=change['before'],change['after']
            if change['table']=='djmdPlaylist' and change['id'] not in bound:
                if old is None or new is None:raise WriteProofError('Pilot cannot change another native playlist')
                fields={k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
                if not fields<={'Seq','updated_at','rb_local_usn'}:
                    raise WriteProofError('Pilot can only preserve sibling order outside its selected playlists')
            elif change['table']=='djmdSongPlaylist':
                if any(r['PlaylistID'] not in bound for r in (old,new) if r is not None):
                    raise WriteProofError('Pilot cannot change another playlist membership')

    def snapshot(self):
        # A catalog observation is a read-only DB/WAL snapshot. Publication's
        # before/after pairs keep their full encrypted backup/integrity checks;
        # do not repeat that whole renderer just to return committed rows.
        from .rekordbox_db import _copy_stable_snapshot
        with tempfile.TemporaryDirectory(prefix='soundbox-playlist-read-') as area:
            captured=Path(area).resolve()/'master.db'
            _copy_stable_snapshot(self.database,captured)
            conn=_connect(captured,readonly=True)
            try:
                if (_identity(conn)!=(self.authority.library_id,'6000')
                        or _schema(conn)!=self.authority.schema_sha256):
                    raise ConflictError('Native playlist read library/schema changed')
                return catalog(conn)
            finally:conn.close()


def _fault(_phase):
    """Synthetic fault tests only; never inject faults into Sonny's source."""


def catalog(conn):
    nodes=_rows(conn,'djmdPlaylist');members=_rows(conn,'djmdSongPlaylist')
    return dict(nodes=nodes,members=members,revision=planner.revision(nodes,members))


def build_plan(conn, request, before_xml):
    with xml.parsing_session():return _build_plan(conn,request,before_xml)


def _build_plan(conn, request, before_xml):
    validate_request(request)
    if request['operation']!='batch':return _single_plan(conn,request,before_xml)
    original=catalog(conn)
    if original['revision']!=request['revision']:
        raise ConflictError('Native playlist batch baseline changed; refresh and retry')
    current=deepcopy(original);counter=row(conn,'agentRegistry','localUpdateCount')
    changes=[];payload=before_xml
    for child in request['requests']:
        # Every child binds the envelope's one external catalog revision.
        # Intermediate state stays private and is checked row/counter by row;
        # do not hash the whole library for each intermediate command.
        plan=_single_plan(conn,child,payload,current=current,counter=counter)
        changes.extend(plan['changes']);payload=plan['xml']
        for change in plan['changes']:
            if change['table']=='agentRegistry':
                if counter!=change['before']:raise ConflictError('Native batch counter chain changed')
                counter=change['after'];continue
            field='nodes' if change['table']=='djmdPlaylist' else 'members'
            rows=current[field];index=next((i for i,r in enumerate(rows) if r['ID']==change['id']),None)
            prior=rows[index] if index is not None else None
            if prior!=change['before']:raise ConflictError('Native batch row chain changed')
            if change['after'] is None:
                if index is not None:rows.pop(index)
            elif index is None:rows.append(deepcopy(change['after']))
            else:rows[index]=deepcopy(change['after'])
    current['revision']=planner.revision(current['nodes'],current['members'])
    return dict(changes=changes,xml=payload,catalog=current)


def _single_plan(conn, request, before_xml, *, current=None,counter=None):
    from datetime import datetime
    validate_request(request)
    current=catalog(conn) if current is None else current
    nodes=current['nodes'];members=current['members']
    if current['revision']!=request['revision']:
        raise ConflictError('Native playlist baseline/intent changed; refresh and retry')
    for field in ('timestamp','survivor_timestamp'):
        datetime.fromisoformat(request[field].replace(' +00:00','+00:00'))
    target_id=request['playlist_id'];op=request['operation']
    counter=row(conn,'agentRegistry','localUpdateCount') if counter is None else counter
    changes=[];after_xml=before_xml
    target=next((r for r in nodes if r['ID']==target_id),None)
    if op=='create':
        if int(target_id) in xml.identities(before_xml):raise ConflictError('Playlist ID collides with XML history')
        first=planner.create(nodes,counter,identity=target_id,uuid=request['playlist_uuid'],
            name='Untitled Folder' if request['folder'] else 'Untitled Playlist',timestamp=request['timestamp'],
            parent=request['parent'],folder=request['folder'])
        second=planner.rename(first['playlist'],first['counter'],name=request['name'],timestamp=request['timestamp'])
        changes=first['changes']+second['changes']
        after_xml=xml.append_node(before_xml,target_id,request['parent'],int(request['folder']))
    else:
        if target is None or target['UUID']!=request['playlist_uuid']:
            raise ConflictError('Native playlist identity disappeared or was replaced')
        if op=='rename':
            changes=planner.rename(target,counter,name=request['name'],timestamp=request['timestamp'])['changes']
        elif op=='reorder':
            anchor=next((r for r in nodes if r['ID']==request['anchor_id']),None)
            if anchor is None or anchor['UUID']!=request['anchor_uuid'] or target['ParentID']!=request['parent']:
                raise ConflictError('Native reorder anchor or parent changed')
            changes=planner.reorder(nodes,counter,identity=target_id,anchor_id=request['anchor_id'],
                after=request['after'],timestamp=request['timestamp'],sibling_timestamp=request['survivor_timestamp'])['changes']
        elif op=='delete':
            changes=planner.delete(nodes,members,counter,identity=target_id,timestamp=request['timestamp'],member_timestamp=request['survivor_timestamp'],sibling_timestamp=request['survivor_timestamp'])['changes']
        elif op in ('append','remove'):
            target_members=[r for r in members if r['PlaylistID']==target_id]
            occupied={v for r in members for v in (r['ID'],r['UUID'])}
            for track in request['tracks']:
                content=row(conn,'djmdContent',track['track_id'])
                if content is None or content['UUID']!=track['uuid'] or content['rb_local_deleted']!=0:
                    raise ConflictError('Native Collection track identity changed')
                if op=='append':
                    if track['id'] in occupied or track['membership_uuid'] in occupied:
                        raise ConflictError('Native membership identity collides with history')
                    proposal=planner.append(target,target_members,counter,content,identity=track['id'],
                        uuid=track['membership_uuid'],timestamp=request['timestamp'],allow_duplicate=request['allow_duplicate'])
                    occupied|={track['id'],track['membership_uuid']}
                else:
                    matches=[r for r in target_members if r['ContentID']==track['track_id'] and r['rb_local_deleted']==0]
                    if not matches:raise ConflictError('Selected song is no longer in this native playlist')
                    # One row per explicit selected occurrence. A duplicate
                    # track is removed in source order, never from another list.
                    proposal=planner.remove(target,target_members,counter,matches[0]['ID'],
                        timestamp=request['timestamp'],survivor_timestamp=request['survivor_timestamp'])
                changes+=proposal['changes'];target_members,counter=proposal['members'],proposal['counter']
        else:raise WriteProofError('Unknown native playlist operation')
    known_xml=xml.identities(before_xml)
    identity_rows=[c for c in changes if c['table']=='djmdPlaylist']
    if changes and target is not None:identity_rows.append(dict(table='djmdPlaylist',before=target))
    for change in identity_rows:
        if change['table']!='djmdPlaylist' or change['before'] is None:continue
        old=change['before']
        if old['Attribute'] not in (0,1) or int(old['ID']) not in known_xml:continue
        attributes,_=xml._target(before_xml,old['ID'])
        parent=0 if old['ParentID']=='root' else int(old['ParentID'])
        if int(attributes['ParentId'],16)!=parent or int(attributes['Attribute'])!=old['Attribute']:
            raise ConflictError('Native playlist DB/XML identity differs')
    missing={}
    for node in nodes:
        if node.get('rb_local_synced')==1 and node.get('rb_local_deleted')==0:
            try:planner.local(node)
            except ValueError:continue
            missing[node['ID']]=(node['ParentID'],node['Attribute'])
    if changes and op!='delete' and not (op=='create' and not second['changes']):
        if int(target_id) in xml.identities(after_xml):
            after_xml=xml.touch(after_xml,target_id,request['xml_timestamp'])
        elif target_id in missing:
            after_xml=xml.append_node(after_xml,target_id,*missing[target_id])
        else:
            raise ConflictError('Expected native playlist XML entry disappeared')
    xml.validate_transition(before_xml,after_xml,changes,missing_targets=missing,
                            touched_playlists=(target_id,) if op=='reorder' and changes else ())
    return dict(changes=changes,xml=after_xml,catalog=current)


def _timestamps():
    from datetime import datetime, timezone
    now=datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%d %H:%M:%S.')+f'{now.microsecond//1000:03d} +00:00',int(now.timestamp()*1000)


def request_for(catalog, operation, *, identity=None, name='', parent='root', folder=False,
                tracks=(), allow_duplicate=False,anchor_id=None,after=False):
    nodes=catalog['nodes'];target=next((r for r in nodes if r['ID']==identity),None)
    if operation=='create':
        occupied={r['ID'] for r in nodes};identity=str(secrets.randbelow(2**32-1)+1)
        while identity in occupied:identity=str(secrets.randbelow(2**32-1)+1)
    elif target is None:raise ValueError('Native playlist is no longer in the library')
    stamp,clock=_timestamps()
    request=dict(operation_id=uuid4().hex,operation=operation,playlist_id=identity,
        playlist_uuid=target['UUID'] if target else str(uuid4()),revision=catalog['revision'],
        name=name,parent=parent,folder=bool(folder),timestamp=stamp,survivor_timestamp=stamp,
        xml_timestamp=clock,allow_duplicate=bool(allow_duplicate),
        tracks=[dict(track_id=getattr(t,'native_id',t.track_id),uuid=t.source_uuid,id=str(uuid4()),membership_uuid=str(uuid4()))
                for t in tracks])
    if operation=='reorder':
        anchor=next((r for r in nodes if r['ID']==anchor_id),None)
        if anchor is None or target is None:raise ValueError('Native reorder anchor is no longer in the library')
        request.update(parent=target['ParentID'],anchor_id=anchor_id,anchor_uuid=anchor['UUID'],after=after)
    validate_request(request)
    return request



def validate_request(request):
    if isinstance(request,dict) and request.get('operation')=='batch':
        if (set(request)!={'operation_id','operation','revision','requests'}
                or not isinstance(request['requests'],list) or not 1<=len(request['requests'])<=MAX_BATCH_COMMANDS):
            raise WriteProofError('Invalid native playlist command batch')
        for field,length in (('operation_id',32),('revision',64)):
            value=request[field]
            if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
                raise WriteProofError('Invalid native playlist batch identity')
        ids={request['operation_id']}
        selected=0
        for child in request['requests']:
            if not isinstance(child,dict) or child.get('operation') not in ('create','append','remove'):
                raise WriteProofError('Native playlist batch exceeds accepted commands')
            validate_request(child)
            if child['revision']!=request['revision'] or child['operation_id'] in ids:
                raise WriteProofError('Native playlist batch has a changed or repeated command')
            ids.add(child['operation_id'])
            selected+=len(child['tracks'])
        if selected>16384:raise WriteProofError('Native playlist batch selection is too large')
        return
    fields={'operation_id','operation','playlist_id','playlist_uuid','revision','name','parent','tracks',
            'timestamp','survivor_timestamp','xml_timestamp','folder','allow_duplicate'}
    if isinstance(request,dict) and request.get('operation')=='reorder':fields|={'anchor_id','anchor_uuid','after'}
    if not isinstance(request,dict) or set(request)!=fields:
        raise WriteProofError('Unknown native playlist intent format')
    for key,length in (('operation_id',32),('revision',64)):
        value=request[key]
        if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
            raise WriteProofError('Invalid native playlist operation identity')
    for key in (('playlist_id','parent','anchor_id') if request['operation']=='reorder' else ('playlist_id','parent')):
        value=request[key]
        if key=='parent' and value=='root':continue
        if (type(value) is not str or not value.isascii() or not value.isdecimal()
                or not 0<int(value)<2**32 or str(int(value))!=value):
            raise WriteProofError('Invalid native playlist target identity')
    try:
        for key in (('playlist_uuid','anchor_uuid') if request['operation']=='reorder' else ('playlist_uuid',)):
            if str(UUID(request[key]))!=request[key]:raise ValueError('UUID')
    except (ValueError,TypeError,AttributeError) as exc:
        raise WriteProofError('Invalid native playlist UUID') from exc
    if (request['operation'] not in ('create','rename','delete','append','remove','reorder')
            or type(request['folder']) is not bool or type(request['allow_duplicate']) is not bool
            or type(request['xml_timestamp']) is not int or not 0<request['xml_timestamp']<2**63
            or type(request['name']) is not str or '\0' in request['name']
            or not isinstance(request['tracks'],list) or len(request['tracks'])>16384):
        raise WriteProofError('Invalid native playlist operation')
    if (bool(request['tracks'])!=(request['operation'] in ('append','remove'))
            or request['operation'] in ('create','rename') and not request['name'].strip()):
        raise WriteProofError('Native playlist operation has an invalid selection')
    if request['operation']=='reorder' and (type(request['after']) is not bool or request['anchor_id']==request['playlist_id']):
        raise WriteProofError('Invalid native reorder anchor')
    from datetime import datetime
    for field in ('timestamp','survivor_timestamp'):
        value=request[field]
        try:
            if type(value) is not str:raise ValueError('Timestamp')
            parsed=datetime.fromisoformat(value.replace(' +00:00','+00:00'))
            canonical=parsed.strftime('%Y-%m-%d %H:%M:%S.')+f'{parsed.microsecond//1000:03d} +00:00'
            if value!=canonical:raise ValueError('Timestamp')
        except (ValueError,TypeError,AttributeError) as exc:
            raise WriteProofError('Invalid native playlist intent clock') from exc
    for track in request['tracks']:
        if (not isinstance(track,dict) or set(track)!={'track_id','uuid','id','membership_uuid'}
                or any(type(value) is not str or not value for value in track.values())):
            raise WriteProofError('Invalid native playlist track selection')
        try:
            if any(str(UUID(track[key]))!=track[key] for key in ('id','membership_uuid')):raise ValueError('UUID')
        except (ValueError,TypeError,AttributeError) as exc:
            raise WriteProofError('Invalid native membership UUID') from exc
