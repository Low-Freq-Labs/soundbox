"""Prepare a single reviewed playlist pilot on copies; no source write here."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
from uuid import uuid4

from soundbox.rekordbox_tags import NATIVE_VERSION, _installed_version, _schema
from soundbox.rekordbox_write import (
    WriteProofError, ConflictError, _connect, _identity, _integrity, _json, _regular,
    _rows, _state, assert_rekordbox_closed, encrypted_backup,
)
from soundbox.state_io import atomic_bytes
from . import playlist_lifecycle as lifecycle, playlist_membership as membership, playlist_xml as xml
from .playlist_replay import _apply
from .playlist_transaction import _copy_backup, _projection, _sha, _verify_projection

FORMAT = 'soundbox-playlist-pilot-plan-v1'
PAYLOADS = ('before.db','after.db','before.xml','after.xml')


def pilot_authorizer(action, table, column, database, trigger):
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


@contextmanager
def copied_database(path):
    _regular(path)
    with tempfile.TemporaryDirectory(prefix='soundbox-playlist-plan-check-') as folder:
        target = Path(folder).resolve()/'master.db'
        shutil.copyfile(path,target)
        yield target


def sql_clock():
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%d %H:%M:%S.')+f'{now.microsecond//1000:03d} +00:00'


def shared_root(data_root, source, library_id):
    key = hashlib.sha256((str(source)+'\0'+library_id).encode()).hexdigest()[:24]
    return Path(data_root)/'rekordbox_recovery'/'tag-writes'/key


def prepare(source, output, track_ids, *, data_root, name='SoundBox Bridge Pilot'):
    """Read one explicit closed source and produce encrypted before/after files."""
    source = Path(source).expanduser().absolute()
    output = Path(output).expanduser().absolute()
    data_root = Path(data_root).expanduser().absolute()
    if (source != source.resolve() or source.name != 'master.db' or source.is_relative_to('/Volumes')
            or output != output.resolve() or output.is_relative_to(source.parent)
            or source.is_relative_to(output) or data_root != data_root.resolve()
            or data_root.is_relative_to(source.parent) or source.is_relative_to(data_root)):
        raise WriteProofError('Pilot paths overlap or redirect')
    _regular(source)
    if _installed_version() != NATIVE_VERSION:
        raise WriteProofError('Native playlist reference version changed')
    assert_rekordbox_closed()
    if name!='SoundBox Bridge Pilot' or len(track_ids) != 2 or len(set(track_ids)) != 2:
        raise WriteProofError('This pilot binds exactly two existing distinct tracks')
    xml_path = source.with_name('masterPlaylists6.xml');_regular(xml_path)
    xml_before = xml_path.read_bytes();used_xml = xml.identities(xml_before)
    output.mkdir(parents=True,mode=0o700)
    refs = []
    with encrypted_backup(source) as captured:
        conn = _connect(captured,readonly=True)
        try:
            library_id, _ = _identity(conn);schema = _schema(conn);before = _state(conn)
            recovery_root = shared_root(data_root,source,library_id)
            if ((recovery_root/'transaction.json').exists()
                    or (recovery_root/'grid-transaction').exists()):
                raise WriteProofError('Resolve existing native-edit recovery before preparing the pilot')
            playlists = _rows(conn,'djmdPlaylist')
            counter, = [r for r in _rows(conn,'agentRegistry') if r['registry_id']=='localUpdateCount']
            if any(r['Name']==name and r['rb_local_deleted']==0 for r in playlists):
                raise ConflictError('Pilot playlist name already exists')
            contents = []
            for identity in track_ids:
                cursor = conn.execute('SELECT * FROM djmdContent WHERE ID=?',(identity,))
                columns = [item[0] for item in cursor.description]
                matches = [dict(zip(columns,row,strict=True)) for row in cursor]
                if len(matches)!=1 or matches[0]['rb_local_deleted']!=0 or not matches[0]['UUID']:
                    raise ConflictError('Pilot content identity is missing/deleted')
                row = matches[0];contents.append(row)
                membership._unsynced(row)
                refs.append(dict(track_id=row['ID'],uuid=row['UUID'],title=row['Title']))
            occupied = {str(r['ID']) for r in playlists}|{str(i) for i in used_xml}
            identity = str(secrets.randbelow(2**32-1)+1)
            while identity in occupied:identity = str(secrets.randbelow(2**32-1)+1)
            created = lifecycle.create(playlists,counter,identity=identity,uuid=str(uuid4()),
                name='Untitled Playlist',timestamp=sql_clock())
            renamed = lifecycle.rename(created['playlist'],created['counter'],name=name,timestamp=sql_clock())
            changes = [*created['changes'],*renamed['changes']]
            xml_after = xml.append_root(xml_before,identity)
            xml_time = time.time_ns()//1_000_000
            xml_after = xml.touch(xml_after,identity,xml_time)
            members = [];counter = renamed['counter']
            for content in contents:
                plan = membership.append(renamed['playlist'],members,counter,content,
                    identity=str(uuid4()),uuid=str(uuid4()),timestamp=sql_clock())
                changes.extend(plan['changes']);members,counter = plan['members'],plan['counter']
                xml_time = max(xml_time+1,time.time_ns()//1_000_000)
                xml_after = xml.touch(xml_after,identity,xml_time)
            projected = _projection(conn,changes)
        finally:conn.close()
        shutil.copyfile(captured,output/'before.db');os.chmod(output/'before.db',0o600)
        with tempfile.TemporaryDirectory(prefix='soundbox-playlist-plan-stage-') as folder:
            staged = Path(folder).resolve()/'after.db';_copy_backup(captured,staged)
            conn = _connect(staged)
            try:
                conn.execute('BEGIN IMMEDIATE');conn.set_authorizer(pilot_authorizer)
                _apply(conn,changes);conn.set_authorizer(None)
                after = _verify_projection(conn,before,projected);conn.commit();_integrity(conn)
            finally:conn.close()
            shutil.copyfile(staged,output/'after.db');os.chmod(output/'after.db',0o600)
    xml.validate_transition(xml_before,xml_after,changes)
    atomic_bytes(output/'before.xml',xml_before);atomic_bytes(output/'after.xml',xml_after)
    info = source.stat()
    record = dict(format=FORMAT,operation_id=uuid4().hex,source=str(source),
        data_root=str(data_root),recovery_root=str(recovery_root),library_id=library_id,schema_sha256=schema,
        application_version=NATIVE_VERSION,source_file_id=[info.st_dev,info.st_ino],
        playlist_id=identity,playlist_uuid=renamed['playlist']['UUID'],playlist_name=name,
        content_refs=refs,changes=changes,before=before,after=after,
        payload_sha256={p:_sha(output/p) for p in PAYLOADS},source_write_authority=False)
    atomic_bytes(output/'plan.json',_json(record))
    assert_rekordbox_closed()
    return record


def validate_intent(conn, record, xml_before):
    """Regenerate the exact approved create/name/two-existing-tracks request."""
    changes = record['changes']
    if (len(changes)!=8 or record['application_version']!=NATIVE_VERSION
            or record['playlist_name']!='SoundBox Bridge Pilot'):
        raise WriteProofError('Pilot scope is exactly create/name/two additions')
    if shared_root(record['data_root'],record['source'],record['library_id'])!=Path(record['recovery_root']):
        raise WriteProofError('Pilot does not use the shared native-edit recovery root')
    playlists = _rows(conn,'djmdPlaylist')
    if int(record['playlist_id']) in xml.identities(xml_before):
        raise ConflictError('Pilot ID collides with retained XML history')
    counter, = [r for r in _rows(conn,'agentRegistry') if r['registry_id']=='localUpdateCount']
    created_row = changes[0]['after'];renamed_row = changes[2]['after']
    created = lifecycle.create(playlists,counter,identity=record['playlist_id'],uuid=record['playlist_uuid'],
        name='Untitled Playlist',timestamp=created_row['created_at'])
    renamed = lifecycle.rename(created['playlist'],created['counter'],name=record['playlist_name'],
        timestamp=renamed_row['updated_at'])
    expected = [*created['changes'],*renamed['changes']]
    members = [];counter = renamed['counter']
    refs = record['content_refs']
    if len(refs)!=2 or len({r['track_id'] for r in refs})!=2:
        raise WriteProofError('Ambiguous pilot content selection')
    existing = _rows(conn,'djmdSongPlaylist')
    for index,ref in enumerate(refs):
        cursor = conn.execute('SELECT * FROM djmdContent WHERE ID=?',(ref['track_id'],))
        columns = [v[0] for v in cursor.description]
        content, = [dict(zip(columns,row,strict=True)) for row in cursor]
        if content['UUID']!=ref['uuid'] or content['Title']!=ref['title'] or content['rb_local_deleted']!=0:
            raise ConflictError('Pilot content identity changed')
        membership._unsynced(content)
        row = changes[4+index*2]['after']
        if any(old['ID']==row['ID'] or old['UUID']==row['UUID'] for old in existing):
            raise ConflictError('Pilot membership identity collides')
        appended = membership.append(renamed['playlist'],members,counter,content,
            identity=row['ID'],uuid=row['UUID'],timestamp=row['created_at'])
        expected.extend(appended['changes']);members,counter = appended['members'],appended['counter']
    if changes!=expected:
        raise WriteProofError('Pilot changes exceed the reviewed request')


def load_plan(folder):
    folder = Path(folder).absolute()
    if folder != folder.resolve() or folder.stat().st_mode & 0o077:
        raise WriteProofError('Pilot plan must remain in its original private directory')
    _regular(folder/'plan.json')
    record = json.loads((folder/'plan.json').read_bytes())
    if (record.get('format')!=FORMAT or set(record.get('payload_sha256',{}))!=set(PAYLOADS)
            or not record.get('changes') or len(record.get('content_refs',[]))!=2):
        raise WriteProofError('Invalid pilot plan')
    operation = record.get('operation_id','')
    if len(operation)!=32 or any(c not in '0123456789abcdef' for c in operation):
        raise WriteProofError('Invalid pilot operation identity')
    for field in ('source','data_root','recovery_root'):
        path = Path(record[field])
        if not path.is_absolute() or path!=path.resolve():
            raise WriteProofError('Pilot source/recovery path redirects')
    if (Path(record['source']).name!='master.db'
            or not isinstance(record.get('source_file_id'),list)
            or len(record['source_file_id'])!=2
            or any(type(v) is not int or v<0 for v in record['source_file_id'])):
        raise WriteProofError('Invalid pilot source identity')
    for name in PAYLOADS:
        if _sha(folder/name)!=record['payload_sha256'][name]:
            raise WriteProofError('Pilot plan payload changed')
    xml.validate_transition((folder/'before.xml').read_bytes(),(folder/'after.xml').read_bytes(),record['changes'])
    with copied_database(folder/'before.db') as before:
        conn = _connect(before,readonly=True)
        try:
            if _state(conn)!=record['before'] or _identity(conn)!=(record['library_id'],'6000') or _schema(conn)!=record['schema_sha256']:
                raise WriteProofError('Pilot baseline identity/inventory mismatch')
            validate_intent(conn,record,(folder/'before.xml').read_bytes())
            projected = _projection(conn,record['changes'])
        finally:conn.close()
    with copied_database(folder/'after.db') as after:
        conn = _connect(after,readonly=True)
        try:
            if _verify_projection(conn,record['before'],projected)!=record['after']:
                raise WriteProofError('Pilot result inventory mismatch')
        finally:conn.close()
    return record
