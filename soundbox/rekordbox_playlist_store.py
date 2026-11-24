"""Identity-bound native-playlist DB/XML transactions.

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
import re
from pathlib import Path
import shutil
import sqlite3
import secrets
from uuid import uuid4, UUID
import tempfile

from . import rekordbox_playlist_plan as planner, rekordbox_playlist_xml as xml
from . import rekordbox_playlist_move as move_plan,rekordbox_playlist_create as create_plan
from . import playlist_membership_replay as membership_replay
from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from .rekordbox_tags import NATIVE_VERSION, _schema
from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect, _identity, _inactive_journal_proof, _integrity, _state,
    _json, _quote, _regular, _rows, encrypted_backup,
)
from .state_io import atomic_bytes, copy_state, sync_dir

FORMAT = 'soundbox-native-playlists-v1'
PAYLOADS = ('before.db','after.db','before.xml','after.xml')
MAX_BATCH_COMMANDS = 64
MAX_TREE_DELETE_COMMANDS = 256
_CANONICAL_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

SMART_CREATE_PROFILE = 'rekordbox-6.8.7.0303-mytag-smart-create-v1'
SMART_APPEND_CREATE_PROFILE = 'rekordbox-6.8.7.0303-mytag-smart-append-create-v1'
SMART_NORMALIZED_CREATE_PROFILE = 'rekordbox-6.8.7.0303-local-normalized-smart-create-v1'
CREATE_SMART_OPERATIONS = frozenset(('create_smart','create_smart_append','create_smart_normalized'))
CREATE_ORDINARY_OPERATIONS = frozenset(('create','create_normalized','create_folder_normalized'))
NORMALIZED_FOLDER_CREATE_PROFILE = 'rekordbox-6.8.7.0303-local-normalized-folder-create-v1'
NORMALIZED_CREATE_PROFILE = 'rekordbox-6.8.7.0303-local-normalized-ordinary-create-v1'
SMART_DELETE_PROFILE = 'rekordbox-6.8.7.0303-unsynced-smart-delete-v1'
SMART_LOCAL_DELETE_PROFILE = 'rekordbox-6.8.7.0303-local-smart-delete-v2'
SMART_RENAME_PROFILE = 'rekordbox-6.8.7.0303-local-smart-inline-rename-v1'
SMART_REORDER_PROFILE = 'rekordbox-6.8.7.0303-local-mixed-sibling-reorder-v1'
SMART_MOVE_PROFILE = 'rekordbox-6.8.7.0303-local-smart-cross-parent-move-v1'
NODE_MOVE_PROFILE = 'rekordbox-6.8.7.0303-local-node-placement-v1'
MOVE_OPERATIONS = frozenset(('move_smart','move'))


def _canonical_uuid(value):
    """Preserve UUID parse equality; canonical plain text needs no allocation."""
    if type(value) is str:
        return _CANONICAL_UUID.fullmatch(value) is not None
    try:
        return str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def command_requests(request):
    """Validated bounded commands; batches never widen the native write scope."""
    return request['requests'] if request.get('operation') in ('batch','delete_tree') else [request]


def tree_bindings(current, identity):
    """Exact bounded active subtree, children before their containing folder."""
    active = [row for row in current['nodes'] if row['rb_local_deleted'] == 0]
    by_id = {row['ID']: row for row in active}
    if len(by_id) != len(active) or identity not in by_id or by_id[identity]['Attribute'] != 1:
        raise WriteProofError('Expected one exact active folder for tree deletion')
    result = []
    seen: set[str] = set()
    def visit(selected):
        if selected in seen or len(seen) >= MAX_TREE_DELETE_COMMANDS:
            raise WriteProofError('Playlist deletion tree is cyclic or exceeds its bounded scope')
        seen.add(selected); node = by_id[selected]
        if node['Attribute'] not in (0,1,4):
            raise WriteProofError('Playlist deletion tree contains unverified native node types')
        children = planner._siblings(active, selected)
        if children and node['Attribute'] != 1:
            raise WriteProofError('A playlist unexpectedly contains descendant playlists')
        for child in children:visit(child['ID'])
        result.append(dict(id=node['ID'], uuid=node['UUID'], parent=node['ParentID'], attribute=node['Attribute']))
    visit(identity)
    return result


def tree_delete_for(current, identity):
    """Compose proven leaf operations; never infer native one-click tree clocks."""
    bindings = tree_bindings(current, identity)
    projected = {**current,'nodes':[dict(row) for row in current['nodes']],'members':list(current['members'])}
    requests = []
    for binding in bindings:
        operation = 'delete_smart' if binding['attribute'] == 4 else 'delete'
        requests.append(request_for(projected, operation, identity=binding['id'], parent=binding['parent']))
        target = next(row for row in projected['nodes'] if row['ID'] == binding['id'])
        projected['nodes'] = [row for row in projected['nodes'] if row['ID'] != target['ID']]
        projected['members'] = [row for row in projected['members'] if row['PlaylistID'] != target['ID']]
        for row in projected['nodes']:
            if row['ParentID'] == target['ParentID'] and row['rb_local_deleted'] == 0 and row['Seq'] > target['Seq']:
                row['Seq'] -= 1
    root = bindings[-1]
    result = dict(operation_id=uuid4().hex, operation='delete_tree', revision=current['revision'],
                  playlist_id=root['id'], playlist_uuid=root['uuid'], descendants=bindings, requests=requests)
    validate_request(result)
    return result


def renew_tree_delete_request(request, current, *, operation_id=None):
    """Renew an unstarted causal plan or explicit retry without expanding scope."""
    validate_request(request)
    if request['operation'] != 'delete_tree':
        raise WriteProofError('Tree deletion renewal requires its captured envelope')
    fresh = tree_bindings(current, request['playlist_id'])
    if {row['id']:_json(row) for row in fresh} != {row['id']:_json(row) for row in request['descendants']}:
        raise ConflictError('Playlist deletion descendants changed; capture a new explicit deletion')
    result = tree_delete_for(current,request['playlist_id'])
    if operation_id is None:
        result['operation_id'] = request['operation_id']
        previous = {child['playlist_id']:child['operation_id'] for child in request['requests']}
        for child in result['requests']:child['operation_id'] = previous[child['playlist_id']]
    else:
        result['operation_id'] = operation_id
    validate_request(result)
    return result


def batch_for(catalog,requests,*,plan_encoding=None):
    request=dict(operation_id=uuid4().hex,operation='batch',revision=catalog['revision'],requests=deepcopy(requests))
    if plan_encoding is not None:
        request['plan_encoding'] = plan_encoding
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
    # A final native smart-list capture is not a create-protocol acceptance.
    # Existing authority files default false and never gain this permission.
    smart_create_accepted: bool = False
    smart_create_profile: str = ''
    smart_append_create_accepted: bool = False
    smart_append_create_profile: str = ''
    smart_normalized_create_accepted: bool = False
    smart_normalized_create_profile: str = ''
    # Smart creation never grants mutation/deletion of an existing rule.
    smart_delete_accepted: bool = False
    smart_delete_profile: str = ''
    smart_rename_accepted: bool = False
    smart_rename_profile: str = ''
    smart_reorder_accepted: bool = False
    smart_reorder_profile: str = ''
    smart_move_accepted: bool = False
    smart_move_profile: str = ''
    node_move_accepted: bool = False
    node_move_profile: str = ''
    normalized_create_accepted: bool = False
    normalized_create_profile: str = ''
    normalized_folder_create_accepted: bool = False
    normalized_folder_create_profile: str = ''

    def __post_init__(self):
        if self.accepted is not True:
            raise WriteProofError('Playlist writing requires native acceptance')
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,
                          'playlist-authority',self.application_version)
        if (type(self.retain_full_backups) is not bool or type(self.reorder_accepted) is not bool
                or type(self.smart_create_accepted) is not bool
                or type(self.smart_append_create_accepted) is not bool
                or type(self.smart_append_create_profile) is not str or len(self.smart_append_create_profile)>128
                or '\0' in self.smart_append_create_profile
                or type(self.smart_create_profile) is not str or len(self.smart_create_profile) > 128
                or '\0' in self.smart_create_profile
                or type(self.smart_delete_accepted) is not bool
                or type(self.smart_delete_profile) is not str or len(self.smart_delete_profile) > 128
                or '\0' in self.smart_delete_profile
                or any(type(getattr(self,key+'_accepted')) is not bool
                       or type(getattr(self,key+'_profile')) is not str
                       or len(getattr(self,key+'_profile')) > 128 or '\0' in getattr(self,key+'_profile')
                       for key in ('smart_rename','smart_reorder','smart_move','node_move','normalized_create','normalized_folder_create','smart_normalized_create'))
                or not isinstance(self.pilot_playlist_ids,tuple)
                or any(not isinstance(v,str) or not v for v in self.pilot_playlist_ids)):
            raise WriteProofError('Invalid playlist pilot bindings')


def smart_creation_reason(authority):
    """Admission-only message; toggling a flag cannot bless an unknown profile."""
    if authority is None or getattr(authority, 'smart_create_accepted', False) is not True:
        return 'Native My Tag smart-playlist creation requires separate acceptance'
    if getattr(authority, 'smart_create_profile', '') != SMART_CREATE_PROFILE:
        return 'Native smart-playlist creation requires its exact separately accepted profile'
    return ''


def smart_append_creation_reason(authority):
    """Direct folder append never borrows the accepted root/front profile."""
    if authority is None or getattr(authority,'smart_append_create_accepted',False) is not True:
        return 'Native smart-playlist folder append requires separate acceptance'
    if getattr(authority,'smart_append_create_profile','')!=SMART_APPEND_CREATE_PROFILE:
        return 'Native smart-playlist folder append requires its exact separately accepted profile'
    return ''


def smart_normalized_creation_reason(authority):
    if authority is None or getattr(authority,'smart_normalized_create_accepted',False) is not True:
        return 'Native smart creation across empty or gapped folders requires separate acceptance'
    if getattr(authority,'smart_normalized_create_profile','')!=SMART_NORMALIZED_CREATE_PROFILE:
        return 'Native normalized smart creation requires its exact separately accepted profile'
    return ''


def smart_normalized_creation_parent_reason(current,parent='root'):
    try:create_plan.context(current['nodes'],parent,_parent_limit=4096)
    except (ValueError,KeyError,TypeError,AttributeError) as error:return str(error)
    return ''


def smart_append_creation_parent_reason(current,parent):
    if not isinstance(current,dict) or not isinstance(current.get('nodes'),list):
        return 'Native playlist folders are unavailable'
    return planner.smart_append_parent_reason(current['nodes'],parent)


def smart_creation_parent_reason(current, parent='root'):
    """Pure UI eligibility only; source authority and fresh guards stay separate."""
    if not isinstance(current, dict) or not isinstance(current.get('nodes'), list):
        return 'Native playlist folders are unavailable'
    try:
        if create_plan.required(current,'root'):
            return 'Root smart creation requires its separately accepted normalized constructor'
    except (ValueError,KeyError,TypeError,AttributeError) as error:return str(error)
    return planner.smart_parent_reason(current['nodes'], parent)


def smart_deletion_reason(authority, *, profile=SMART_DELETE_PROFILE):
    """Creation permission never admits this separately accepted delete profile."""
    if authority is None or getattr(authority, 'smart_delete_accepted', False) is not True:
        return 'Native smart-playlist deletion requires separate acceptance'
    accepted = getattr(authority, 'smart_delete_profile', '')
    if (profile not in (SMART_DELETE_PROFILE, SMART_LOCAL_DELETE_PROFILE)
            or accepted not in (SMART_DELETE_PROFILE, SMART_LOCAL_DELETE_PROFILE)
            or profile == SMART_LOCAL_DELETE_PROFILE and accepted != SMART_LOCAL_DELETE_PROFILE):
        return 'Native smart-playlist deletion requires its exact separately accepted profile'
    return ''


def normalized_creation_reason(authority):
    if authority is None or getattr(authority,'normalized_create_accepted',False) is not True:
        return 'Native creation across playlist position gaps requires separate acceptance'
    if getattr(authority,'normalized_create_profile','')!=NORMALIZED_CREATE_PROFILE:
        return 'Native normalized creation requires its exact separately accepted profile'
    return ''


def normalized_folder_creation_reason(authority):
    if authority is None or getattr(authority,'normalized_folder_create_accepted',False) is not True:
        return 'Native normalized folder creation requires separate acceptance'
    if getattr(authority,'normalized_folder_create_profile','')!=NORMALIZED_FOLDER_CREATE_PROFILE:
        return 'Native normalized folder creation requires its exact separately accepted profile'
    return ''


def normalized_creation_required(current,parent='root'):
    return create_plan.required(current,parent)



def smart_editing_reason(authority,operation):
    """New metadata profiles never inherit ordinary or smart-create acceptance."""
    key,profile,label = ('smart_rename',SMART_RENAME_PROFILE,'renaming') if operation=='rename_smart' else (
        'smart_reorder',SMART_REORDER_PROFILE,'mixed reordering')
    if operation=='move_smart':key,profile,label='smart_move',SMART_MOVE_PROFILE,'folder moves'
    if operation not in {'rename_smart','reorder_smart','move_smart'}:
        return 'Unknown intelligent playlist metadata operation'
    if authority is None or getattr(authority,key+'_accepted',False) is not True:
        return 'Native intelligent playlist '+label+' requires separate acceptance'
    if getattr(authority,key+'_profile','') != profile:
        return 'Native intelligent playlist '+label+' requires its exact separately accepted profile'
    return ''


def node_move_reason(authority):
    """All-node placement cannot borrow legacy smart append or ordering scope."""
    if authority is None or getattr(authority,'node_move_accepted',False) is not True:
        return 'Native playlist and folder placement requires separate acceptance'
    if getattr(authority,'node_move_profile','')!=NODE_MOVE_PROFILE:
        return 'Native node placement requires its exact separately accepted profile'
    return ''


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


def validate_collection_pins(pins):
    if (not isinstance(pins,dict)
            or any(not isinstance(key,str) or not key or len(key)>8192
                   or not isinstance(value,str) or not value or len(value)>8192
                   or '\0' in key or '\0' in value for key,value in pins.items())):
        raise WriteProofError('Invalid captured native Collection identities')


def _require_collection_pins(conn,pins):
    validate_collection_pins(pins)
    for identity,uuid in pins.items():
        track=row(conn,'djmdContent',identity)
        if not track or track['UUID']!=uuid or track['rb_local_deleted']!=0:
            raise ConflictError('Captured native Collection identity changed before playlist deletion')


def _tree_deleted_parents(request, final):
    if request.get('operation') != 'delete_tree':return None
    return {binding['id']: final[('djmdPlaylist',binding['id'])]
            for binding in request['descendants'] if binding['attribute'] == 1}


def _require_tree_references(conn, request, *, deleted):
    if request.get('operation') != 'delete_tree':return
    current = catalog(conn)
    if not deleted:
        if tree_bindings(current,request['playlist_id']) != request['descendants']:
            raise ConflictError('Playlist deletion gained or replaced a descendant; captured scope preserved')
        return
    identities={binding['id'] for binding in request['descendants']}
    if (any(row['rb_local_deleted']==0 and (row['ID'] in identities or row['ParentID'] in identities)
            for row in current['nodes'])
            or any(row['rb_local_deleted']==0 and row['PlaylistID'] in identities for row in current['members'])):
        raise ConflictError('Deleted playlist tree still contains active descendants or memberships')


def _require_creation_parents(conn, request, changes, *, after):
    first,_final=_affected(changes)
    for child in command_requests(request):
        if child['operation'] not in CREATE_ORDINARY_OPERATIONS or 'parent_uuid' not in child or child['parent']=='root':continue
        if not after and first.get(('djmdPlaylist',child['parent']),object()) is None:
            continue  # This same exact envelope creates the containing folder first.
        parent=row(conn,'djmdPlaylist',child['parent'])
        if parent is None or parent['UUID'] != child['parent_uuid'] or parent['Attribute'] != 1:
            raise ConflictError('The captured playlist folder disappeared or was replaced')
        planner._ordinary(parent,folder=True)


def _deleted_collection_ids(changes):
    first,final=_affected(changes);identities: set[str]=set()
    for key,before in first.items():
        after=final[key]
        if (key[0]=='djmdSongPlaylist' and before and before['rb_local_deleted']==0
                and (after is None or after['rb_local_deleted']!=0)):
            identities.add(before['ContentID'])
    return identities


def _deleted_collection_pins(conn,changes):
    pins={}
    for identity in _deleted_collection_ids(changes):
        track=row(conn,'djmdContent',identity)
        if not track or not track['UUID'] or track['rb_local_deleted']!=0:
            raise ConflictError('Deleted playlist membership has no exact active Collection identity')
        pins[identity]=track['UUID']
    validate_collection_pins(pins);return pins


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
            matched_after=_matches(conn,final);matched_before=_matches(conn,first)
            if not (matched_after or matched_before):
                raise RecoveryRequired('Native playlist state is outside the exact recovery pair')
            _require_tree_references(conn,record['request'],deleted=matched_after)
            _require_creation_parents(conn,record['request'],record['changes'],after=matched_after)
            for command in command_requests(record['request']):
                if command['operation'] in CREATE_SMART_OPERATIONS:
                    _require_smart_references(conn, command)
                elif command['operation'] in MOVE_OPERATIONS:
                    _require_smart_move_references(conn,command,after=matched_after)
                elif command['operation'] == 'delete_smart':
                    target = row(conn, 'djmdPlaylist', command['playlist_id'])
                    applied = (target is not None and target.get('rb_local_deleted') == 1
                               if command['smart_delete_profile'] == SMART_LOCAL_DELETE_PROFILE else target is None)
                    _require_smart_deletion_references(conn, command, deleted=applied,
                        expected_target=final[('djmdPlaylist', command['playlist_id'])],
                        deleted_parents=_tree_deleted_parents(record['request'],final) if matched_after else None)
            if _sha(self.xml)!=current_xml:
                raise RecoveryRequired('Playlist XML changed during recovery')
            if matched_after and current_xml==record['payload_sha256']['after.xml']:
                outcome='committed'
            elif matched_before:
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
        with xml.parsing_session(), self.lease._lock():return self._recover_locked()

    def committed_transition(self,request):
        """Prove one exact committed catalog edge for app-intent reconciliation.

        This grants no publication permission. A replay may rebase later app
        intents only while the current catalog/XML still equal this operation's
        independently reconstructed final state. Older retired pairs cannot
        supply that full proof and retain the ordinary stale-conflict behavior.
        """
        validate_request(request)
        with xml.parsing_session(),self.lease._lock():
            if self.journal.exists() or self.journal.is_symlink():
                raise RecoveryRequired('Finish native playlist recovery before proving a committed transition')
            self._guard_xml();self._check_scope(request)
            archive=self.root/('playlist-'+request['operation_id'])
            if not archive.exists() and not archive.is_symlink():return None
            operation=archive/'operation.json'
            archive,record=self._receipt(dict(archive=archive.name,operation_sha256=_sha(operation)))
            info=self.database.stat()
            if (archive.stat().st_mode&0o077 or record.get('request')!=request
                    or record.get('archive')!=archive.name
                    or record.get('source_file_id')!=[info.st_dev,info.st_ino]
                    or set(record.get('payload_sha256',{}))!=set(PAYLOADS)):
                raise RecoveryRequired('Playlist transition receipt identity changed')
            self._check_changes(record['changes'])
            marker=archive/'retired.json'
            retired=marker.exists() or marker.is_symlink()
            if retired:
                _regular(marker)
                expected=dict(format='soundbox-retired-playlist-payloads-v1',operation_sha256=_sha(operation),
                    payload_sha256={name:record['payload_sha256'][name] for name in ('before.db','after.db')})
                if record.get('retain_full_backups') is not False or json.loads(marker.read_bytes())!=expected:
                    raise RecoveryRequired('Playlist transition retirement proof changed')
            for name in PAYLOADS:
                payload=archive/name
                if retired and name.endswith('.db') and not payload.exists() and not payload.is_symlink():continue
                if _sha(payload)!=record['payload_sha256'][name]:
                    raise RecoveryRequired('Playlist transition payload changed')
            if retired:return None
            from .rekordbox_db import _copy_stable_snapshot
            with tempfile.TemporaryDirectory(prefix='soundbox-playlist-transition-') as area:
                before=Path(area).resolve()/'before.db';shutil.copyfile(archive/'before.db',before)
                if _sha(before)!=record['payload_sha256']['before.db']:
                    raise RecoveryRequired('Playlist transition baseline changed during capture')
                before_xml=(archive/'before.xml').read_bytes();after_xml=(archive/'after.xml').read_bytes()
                if any(hashlib.sha256(raw).hexdigest()!=record['payload_sha256'][name]
                       for name,raw in (('before.xml',before_xml),('after.xml',after_xml))):
                    raise RecoveryRequired('Playlist transition XML changed during capture')
                conn=_connect(before)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise RecoveryRequired('Playlist transition backup authority changed')
                    expected_plan=build_plan(conn,request,before_xml)
                    if (expected_plan['changes']!=record['changes']
                            or expected_plan['xml']!=after_xml):
                        raise RecoveryRequired('Playlist transition exceeds its exact committed intent')
                    conn.execute('BEGIN IMMEDIATE');conn.set_authorizer(authorizer)
                    try:apply_changes(conn,expected_plan['changes'])
                    finally:conn.set_authorizer(None)
                    conn.commit();expected_revision=catalog(conn)['revision']
                finally:conn.close()
                current=Path(area).resolve()/'current.db';_copy_stable_snapshot(self.database,current)
                conn=_connect(current,readonly=True)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise ConflictError('Playlist transition current authority changed')
                    observed=catalog(conn)
                    expected_final=_affected(expected_plan['changes'])[1]
                    _require_tree_references(conn,request,deleted=True)
                    for selected in (track for command in command_requests(request) for track in command['tracks']):
                        track=row(conn,'djmdContent',selected['track_id'])
                        if not track or track['UUID']!=selected['uuid'] or track['rb_local_deleted']!=0:return None
                    for command in command_requests(request):
                        if command['operation'] in CREATE_SMART_OPERATIONS:
                            _require_smart_references(conn, command)
                        elif command['operation'] in MOVE_OPERATIONS:
                            _require_smart_move_references(conn,command,after=True)
                        elif command['operation'] == 'delete_smart':
                            expected_target = _affected(expected_plan['changes'])[1][('djmdPlaylist', command['playlist_id'])]
                            _require_smart_deletion_references(conn, command, deleted=True,
                                expected_target=expected_target,deleted_parents=_tree_deleted_parents(request,expected_final))
                finally:conn.close()
            self.lease._guard()
            if (observed['revision']!=expected_revision
                    or _sha(self.xml)!=record['payload_sha256']['after.xml']):return None
            return request['revision'],observed['revision']

    def prepare_collection_pins(self,request):
        """Capture a delete's exact Collection retention before writer admission."""
        validate_request(request)
        if request['operation'] not in ('delete','delete_tree'):raise WriteProofError('Collection retention capture requires an exact playlist deletion')
        archive=self.root/('playlist-'+request['operation_id'])
        committed=archive.exists() or archive.is_symlink()
        from .rekordbox_db import _copy_stable_snapshot
        with xml.parsing_session(),self.lease._lock():
            if self.journal.exists() or self.journal.is_symlink():
                raise RecoveryRequired('Finish native playlist recovery before capturing Collection identities')
            self._guard_xml();self._check_scope(request)
            with tempfile.TemporaryDirectory(prefix='soundbox-playlist-pins-') as area:
                current=Path(area).resolve()/'current.db';_copy_stable_snapshot(self.database,current)
                before=current;before_xml=self.xml.read_bytes();record=None
                if committed:
                    archive,record=self._receipt(dict(archive=archive.name,operation_sha256=_sha(archive/'operation.json')))
                    info=self.database.stat()
                    if (record['request']!=request or record.get('archive')!=archive.name
                            or record.get('source_file_id')!=[info.st_dev,info.st_ino]
                            or archive.stat().st_mode&0o077
                            or set(record.get('payload_sha256',{}))!=set(PAYLOADS)):
                        raise RecoveryRequired('Playlist Collection retention request identity changed')
                    if (archive/'retired.json').exists() or (archive/'retired.json').is_symlink():
                        raise RecoveryRequired('Legacy deleted playlist identities need their exact retained before-pair')
                    for name in PAYLOADS:
                        if _sha(archive/name)!=record['payload_sha256'][name]:
                            raise RecoveryRequired('Playlist Collection retention payload changed')
                    before=Path(area).resolve()/'before.db';shutil.copyfile(archive/'before.db',before)
                    if _sha(before)!=record['payload_sha256']['before.db']:
                        raise RecoveryRequired('Playlist Collection retention baseline changed during capture')
                    before_xml=(archive/'before.xml').read_bytes()
                    if hashlib.sha256(before_xml).hexdigest()!=record['payload_sha256']['before.xml']:
                        raise RecoveryRequired('Playlist Collection retention XML changed during capture')
                conn=_connect(before,readonly=True)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise ConflictError('Playlist Collection retention authority changed')
                    plan=build_plan(conn,request,before_xml)
                    if record is not None:
                        after_xml=(archive/'after.xml').read_bytes()
                        if (plan['changes']!=record['changes'] or plan['xml']!=after_xml
                                or hashlib.sha256(after_xml).hexdigest()!=record['payload_sha256']['after.xml']):
                            raise RecoveryRequired('Playlist Collection retention exceeds its committed intent')
                    pins=_deleted_collection_pins(conn,plan['changes'])
                finally:conn.close()
                conn=_connect(current,readonly=True)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise ConflictError('Playlist Collection retention current authority changed')
                    _require_collection_pins(conn,pins)
                finally:conn.close()
            self.lease._guard();return pins

    def apply(self, request, *, collection_pins=None, expected_state=None, expected_xml_sha256=None):
        """Publish one exact durable user intent; replay is operation-ID idempotent."""
        observed,_transition=self._apply(request,collection_pins=collection_pins,
            expected_state=expected_state,expected_xml_sha256=expected_xml_sha256)
        return observed

    def apply_with_transition(self,request,*,collection_pins=None,expected_state=None,expected_xml_sha256=None):
        """Return the freshly verified edge, or independently prove an older replay.

        The fresh edge belongs only to this call's guarded commit and final
        catalog readback. No process cache grants authority to a later replay.
        """
        observed,transition=self._apply(request,collection_pins=collection_pins,
            expected_state=expected_state,expected_xml_sha256=expected_xml_sha256,prove_transition=True)
        if transition is None:transition=self.committed_transition(request)
        if transition is not None and transition!=(request['revision'],observed['revision']):transition=None
        return observed,transition

    def _apply(self,request,*,collection_pins=None,expected_state=None,expected_xml_sha256=None,prove_transition=False):
        validate_request(request)
        if ((expected_state is None)!=(expected_xml_sha256 is None)
                or expected_state is not None and (not isinstance(expected_state,dict)
                    or type(expected_xml_sha256) is not str or len(expected_xml_sha256)!=64
                    or any(char not in '0123456789abcdef' for char in expected_xml_sha256))):
            raise WriteProofError('A reviewed publication requires its complete state and XML baseline')
        expected_state=deepcopy(expected_state)
        # Recursively gate every independent child before any recovery, backup
        # or source lease, including envelopes admitted by a future caller.
        self._check_scope(request)
        replay_pins_verified=False;replay_receipt_sha=None
        if collection_pins is not None:
            validate_collection_pins(collection_pins)
            if request['operation'] not in ('delete','delete_tree'):raise WriteProofError('Captured Collection retention belongs only to playlist deletion')
            archive=self.root/('playlist-'+request['operation_id'])
            if archive.exists() or archive.is_symlink():
                replay_receipt_sha=self._retired_collection_pins(request,collection_pins)
                if replay_receipt_sha is None:
                    replay_receipt_sha=_sha(archive/'operation.json')
                    if self.prepare_collection_pins(request)!=collection_pins:
                        raise ConflictError('Committed playlist deletion Collection identities changed')
                replay_pins_verified=True
        # Guards still reread the current XML every time. Reuse only the bounded
        # immutable parse of identical bytes across this one transaction.
        with xml.parsing_session(), self.lease._lock():
            if expected_state is not None:
                if (self.journal.exists() or self.journal.is_symlink()
                        or (self.root/('playlist-'+request['operation_id'])).exists()
                        or (self.root/('playlist-'+request['operation_id'])).is_symlink()):
                    raise RecoveryRequired('A reviewed publication cannot recover or replay an older operation')
                with encrypted_backup(self.database) as baseline:
                    conn=_connect(baseline,readonly=True)
                    try:
                        if (_state(conn)!=expected_state or _sha(self.xml)!=expected_xml_sha256):
                            raise ConflictError('The reviewed complete native baseline changed before planning')
                    finally:conn.close()
            self._recover_locked();self._guard_xml()
            self._resume_retirement()
            archive=self.root/('playlist-'+request['operation_id'])
            if archive.exists():
                _regular(archive/'operation.json');prior=json.loads((archive/'operation.json').read_bytes())
                if prior['request']!=request:
                    raise RecoveryRequired('Playlist operation identity was reused')
                _regular(archive/'result.json');result=json.loads((archive/'result.json').read_bytes())
                if result=={**prior,'outcome':'committed'}:
                    if collection_pins is not None and (not replay_pins_verified
                            or _sha(archive/'operation.json')!=replay_receipt_sha):
                        raise ConflictError('Playlist deletion receipt changed during Collection identity verification; retry')
                    self._guard_smart_replay(request, changes=prior['changes'])
                    self._rotate_backups(archive)
                    return self.snapshot(collection_pins=collection_pins),None
                raise ConflictError('This playlist attempt rolled back; refresh and retry explicitly')
            self._check_scope(request)
            before_xml=self.xml.read_bytes()
            with encrypted_backup(self.database) as before:
                before_sha=_sha(before)
                conn=_connect(before,readonly=True)
                try:
                    if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                        raise ConflictError('Native playlist library/schema changed')
                    if expected_state is not None and (_state(conn)!=expected_state
                            or hashlib.sha256(before_xml).hexdigest()!=expected_xml_sha256):
                        raise ConflictError('The reviewed complete native baseline changed before planning')
                    plan=build_plan(conn,request,before_xml)
                    self._check_changes(plan['changes'])
                    if collection_pins is not None and _deleted_collection_pins(conn,plan['changes'])!=collection_pins:
                        raise ConflictError('Captured playlist deletion Collection identities changed')
                finally:conn.close()
                if not plan['changes']:
                    return plan['catalog'],(request['revision'],plan['catalog']['revision'])
                if _sha(before)!=before_sha:
                    raise RecoveryRequired('Playlist planning backup changed during verification')
                archive.mkdir(mode=0o700);copy_state(before,archive/'before.db')
                if _sha(archive/'before.db')!=before_sha:
                    raise RecoveryRequired('Playlist recovery transfer differs from verified payload')
                atomic_bytes(archive/'before.xml',before_xml)
                with tempfile.TemporaryDirectory(prefix='soundbox-playlist-after-') as area:
                    after=Path(area).resolve()/'after.db';shutil.copyfile(before,after)
                    if _sha(after)!=before_sha:
                        raise RecoveryRequired('Playlist recovery transfer differs from verified payload')
                    conn=_connect(after)
                    try:
                        conn.execute('BEGIN IMMEDIATE');conn.set_authorizer(authorizer)
                        apply_changes(conn,plan['changes']);conn.set_authorizer(None);conn.commit()
                        if prove_transition:expected_revision=catalog(conn)['revision']
                    finally:conn.close()
                    # Closing the writer first folds any private WAL. Bracket
                    # the integrity proof around the actual standalone bytes
                    # that will be archived, never a pre-checkpoint main file.
                    after_sha=_sha(after)
                    conn=_connect(after,readonly=True)
                    try:_integrity(conn)
                    finally:conn.close()
                    if _sha(after)!=after_sha:
                        raise RecoveryRequired('Playlist recovery transfer differs from verified payload')
                    copy_state(after,archive/'after.db')
                atomic_bytes(archive/'after.xml',plan['xml'])
            # Seed only from the exact private bytes proved above, checking
            # every archive transfer before any source connection/publication.
            # Later guards still hash all payloads and check live authority,
            # paths, app absence and SQL; cold recovery reconstructs normally.
            payload_sha256={'before.db':before_sha,'after.db':after_sha,
                'before.xml':hashlib.sha256(before_xml).hexdigest(),
                'after.xml':hashlib.sha256(plan['xml']).hexdigest()}
            if any(_sha(archive/name)!=digest for name,digest in payload_sha256.items()):
                raise RecoveryRequired('Playlist recovery transfer differs from verified payload')
            info=self.database.stat()
            record=dict(format=FORMAT,source=str(self.database),source_file_id=[info.st_dev,info.st_ino],
                library_id=self.authority.library_id,schema_sha256=self.authority.schema_sha256,
                application_version=self.authority.application_version,archive=archive.name,request=request,
                changes=plan['changes'],retain_full_backups=self.authority.retain_full_backups,
                retention_predecessor_sha256=(_sha(self.root/'playlist-latest.json')
                    if (self.root/'playlist-latest.json').exists() else None),
                payload_sha256=payload_sha256)
            atomic_bytes(archive/'operation.json',_json(record));sync_dir(archive);sync_dir(self.root)
            self._verified_recovery_record_key=(hashlib.sha256(_json(record)).hexdigest(),self.authority)
            conn=None;journal_started=False
            try:
                _fault('archive-prepared')
                conn=self.lease._connect()
                conn.execute('BEGIN IMMEDIATE');self.lease._guard()
                if _sha(self.xml)!=record['payload_sha256']['before.xml']:
                    raise ConflictError('Native playlist XML changed before publication')
                # The recorded counter and exact target rows bind the private
                # plan to the live source. SQL cannot mutate unrelated domains.
                first,final=_affected(plan['changes'])
                if not _matches(conn,first):raise ConflictError('Native playlist selection changed before save')
                _require_tree_references(conn,request,deleted=False)
                _require_creation_parents(conn,request,plan['changes'],after=False)
                if collection_pins is not None:_require_collection_pins(conn,collection_pins)
                for selected in (track for command in command_requests(request) for track in command['tracks']):
                    track=row(conn,'djmdContent',selected['track_id'])
                    if not track or track['UUID']!=selected['uuid'] or track['rb_local_deleted']!=0:
                        raise ConflictError('Native Collection track changed before save')
                for command in command_requests(request):
                    if command['operation'] in CREATE_SMART_OPERATIONS:
                        _require_smart_references(conn, command)
                    elif command['operation'] in MOVE_OPERATIONS:
                        _require_smart_move_references(conn,command,after=False)
                    elif command['operation'] == 'delete_smart':
                        _require_smart_deletion_references(conn, command, deleted=False)
                atomic_bytes(self.journal,_json(record));journal_started=True;_fault('journal-ready')
                conn.set_authorizer(authorizer);apply_changes(conn,plan['changes'])
                if not _matches(conn,final):raise WriteProofError('Native playlist target readback failed')
                _require_tree_references(conn,request,deleted=True)
                _require_creation_parents(conn,request,plan['changes'],after=True)
                _fault('sql-applied');self.lease._guard()
                if record['payload_sha256']['before.xml']!=record['payload_sha256']['after.xml']:
                    atomic_bytes(self.xml,plan['xml'])
                _fault('xml-published')
                self.lease._guard()
                if collection_pins is not None:_require_collection_pins(conn,collection_pins)
                for command in command_requests(request):
                    if command['operation'] in CREATE_SMART_OPERATIONS:
                        _require_smart_references(conn, command)
                    elif command['operation'] in MOVE_OPERATIONS:
                        _require_smart_move_references(conn,command,after=True)
                    elif command['operation'] == 'delete_smart':
                        _require_smart_deletion_references(conn, command, deleted=True,
                            expected_target=final[('djmdPlaylist', command['playlist_id'])],
                            deleted_parents=_tree_deleted_parents(request,final))
                if _sha(self.xml)!=record['payload_sha256']['after.xml']:
                    raise ConflictError('Native playlist XML changed before commit')
                conn.commit();_fault('sql-committed')
            except BaseException:
                if conn is not None:conn.rollback()
                if not journal_started and not self.journal.exists() and not self.journal.is_symlink():
                    # This process has not entered its SQL/XML publication
                    # boundary. Record that fact; a later explicit retry may
                    # fork the intent even if external source state changed.
                    atomic_bytes(archive/'result.json',_json({**record,'outcome':'not-started'}))
                    sync_dir(archive)
                raise
            finally:
                if conn is not None:conn.close()
            if self._recover_locked()!='committed':raise RecoveryRequired('Native playlist save did not verify')
            self._rotate_backups(archive)
            observed=(self.snapshot(_transition_request=request,_transition_changes=plan['changes'])
                      if prove_transition else self.snapshot())
            if not prove_transition:return observed,None
            self.lease._guard()
            if observed['revision']!=expected_revision or _sha(self.xml)!=record['payload_sha256']['after.xml']:
                return observed,None
            return observed,(request['revision'],observed['revision'])

    def _retired_collection_pins(self,request,pins):
        """A captured UUID intent survives verified retirement of old DB bytes.

        The permanent row receipt proves the complete deleted ContentID set;
        GUI-durable UUID intentions and a fresh source snapshot bind those IDs.
        Legacy jobs without captured intentions still need the retained pair.
        """
        archive=self.root/('playlist-'+request['operation_id'])
        if not (archive/'retired.json').exists() and not (archive/'retired.json').is_symlink():return None
        with self.lease._lock():
            if self.journal.exists() or self.journal.is_symlink():raise RecoveryRequired('Finish playlist recovery before verifying retained Collection intentions')
            self._guard_xml();self._check_scope(request)
            digest=_sha(archive/'operation.json')
            archive,record=self._receipt(dict(archive=archive.name,operation_sha256=digest))
            info=self.database.stat()
            if (archive.stat().st_mode&0o077 or record.get('request')!=request
                    or record.get('archive')!=archive.name or record.get('retain_full_backups') is not False
                    or record.get('source_file_id')!=[info.st_dev,info.st_ino]
                    or set(record.get('payload_sha256',{}))!=set(PAYLOADS)):
                raise RecoveryRequired('Retired playlist Collection identity receipt changed')
            marker=archive/'retired.json';_regular(marker)
            expected=dict(format='soundbox-retired-playlist-payloads-v1',operation_sha256=digest,
                payload_sha256={name:record['payload_sha256'][name] for name in ('before.db','after.db')})
            if json.loads(marker.read_bytes())!=expected:raise RecoveryRequired('Retired playlist Collection identity proof changed')
            for name in PAYLOADS:
                path=archive/name
                if name.endswith('.db') and not path.exists() and not path.is_symlink():continue
                if _sha(path)!=record['payload_sha256'][name]:raise RecoveryRequired('Retired playlist Collection identity payload changed')
            self._check_changes(record['changes'])
            if set(pins)!=_deleted_collection_ids(record['changes']):
                raise ConflictError('Captured Collection retention differs from the exact deleted membership receipt')
            self.snapshot(collection_pins=pins);self.lease._guard();return digest

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

        The caller must durably save this returned NEW intention before apply.
        Explicit retry may select the separately accepted normalized smart
        constructor when native moves made the original constructor ineligible.
        apply(old_request) never upgrades a request or borrows that authority.
        No new operation is generated for an already committed transaction.
        """
        validate_request(request)
        self._check_scope(request)
        with xml.parsing_session(), self.lease._lock():
            self._recover_locked();self._check_scope(request)
            archive=self.root/('playlist-'+request['operation_id'])
            if archive.exists():
                _regular(archive/'operation.json');record=json.loads((archive/'operation.json').read_bytes())
                if record['request']!=request:raise RecoveryRequired('Playlist retry identity changed')
                _regular(archive/'result.json');result=json.loads((archive/'result.json').read_bytes())
                if result=={**record,'outcome':'committed'}:
                    self._guard_smart_replay(request, changes=record['changes'])
                    return request
                if result not in ({**record,'outcome':'rolled-back'},{**record,'outcome':'not-started'}):
                    raise RecoveryRequired('Unknown playlist retry outcome; evidence preserved')
            current=self.snapshot()
            if request['operation'] in ('create_smart','create_smart_append'):
                old_reason=smart_creation_parent_reason(current,'root')
                if not old_reason:
                    old_reason=(smart_append_creation_parent_reason(current,request['parent'])
                        if request['operation']=='create_smart_append' else smart_creation_parent_reason(current,request['parent']))
                if old_reason:
                    reason=smart_normalized_creation_reason(self.authority)
                    if reason:raise WriteProofError(old_reason+'; '+reason)
                    renewed=normalized_smart_request(request,current,operation_id=uuid4().hex)
                    self._check_scope(renewed)
                    if renewed['parent']!='root':
                        attrs,_=xml._target(self.xml.read_bytes(),renewed['parent'])
                        renewed['smart_clocks']['parent_xml_timestamp']=max(renewed['xml_timestamp'],
                            renewed['smart_clocks']['parent_xml_timestamp'],int(attrs['Timestamp'])+1)
                    validate_request(renewed);return renewed
            stamp,clock=_timestamps()
            if request['operation'] in ('create_normalized','create_folder_normalized'):
                renewed=renew_normalized_create_request(request,current,operation_id=uuid4().hex)
                if renewed['parent']!='root':
                    attrs,_=xml._target(self.xml.read_bytes(),renewed['parent'])
                    renewed['normalized_create_clocks']['parent_xml_timestamp']=max(renewed['xml_timestamp'],
                        renewed['normalized_create_clocks']['parent_xml_timestamp'],int(attrs['Timestamp'])+1)
                validate_request(renewed);return renewed
            if request['operation'] in MOVE_OPERATIONS:
                renew=renew_node_move_request if request['operation']=='move' else renew_smart_move_request
                renewed=renew(request,current,operation_id=uuid4().hex)
                payload=self.xml.read_bytes()
                attrs,_=xml._target(payload,renewed['playlist_id'],created_smart=(renewed.get('node_attribute',4)==4))
                renewed['xml_timestamp']=max(renewed['xml_timestamp'],int(attrs['Timestamp'])+1)
                if renewed['parent']!='root':
                    attrs,_=xml._target(payload,renewed['parent'])
                    _move_clocks(renewed)['parent_xml_timestamp']=max(renewed['xml_timestamp'],
                        _move_clocks(renewed)['parent_xml_timestamp'],int(attrs['Timestamp'])+1)
                validate_request(renewed);return renewed
            if request['operation'] == 'delete_tree':
                return renew_tree_delete_request(request,current,operation_id=uuid4().hex)
            if request['operation'] == 'delete_smart':
                return renew_smart_delete_request(request, current, operation_id=uuid4().hex)
            if request['operation'] in CREATE_SMART_OPERATIONS:
                renewed = renew_smart_request(request, current, operation_id=uuid4().hex)
                if renewed['parent'] != 'root':
                    attributes, _ = xml._target(self.xml.read_bytes(), renewed['parent'])
                    renewed['smart_clocks']['parent_xml_timestamp'] = max(
                        renewed['smart_clocks']['parent_xml_timestamp'], int(attributes['Timestamp']) + 1)
                validate_request(renewed)
                return renewed
            if request['operation']=='batch':
                children=[{**child,'operation_id':uuid4().hex,'revision':current['revision'],
                           'timestamp':stamp,'survivor_timestamp':stamp,'xml_timestamp':clock}
                          for child in request['requests']]
                renewed = batch_for(current,children)
                if 'plan_encoding' in request:
                    renewed['plan_encoding'] = request['plan_encoding']
                    validate_request(renewed)
                return renewed
            renewed={**request,'operation_id':uuid4().hex,'revision':current['revision'],
                     'timestamp':stamp,'survivor_timestamp':stamp,'xml_timestamp':clock}
            if request['operation']=='reorder_smart':
                _,shifted,_=planner.smart_reorder_rows(current['nodes'],identity=request['playlist_id'],
                    anchor_id=request['anchor_id'],after=request['after'])
                renewed['smart_order_clocks']={row['ID']:_timestamps()[0] for row in shifted}
                renewed['survivor_timestamp']=max((stamp,*renewed['smart_order_clocks'].values()))
                renewed['smart_parent_xml_timestamp']=0 if request['parent']=='root' else max(clock,_timestamps()[1])
            return renewed

    def _guard_smart_replay(self, request, *, changes):
        """A receipt supplies idempotence, never fresh reference authority."""
        commands = [command for command in command_requests(request)
                    if command['operation'] in (*CREATE_SMART_OPERATIONS, *MOVE_OPERATIONS, 'delete_smart')]
        if not commands and request.get('operation') != 'delete_tree':
            return
        after_targets = _affected(changes)[1]
        from .rekordbox_db import _copy_stable_snapshot
        self.lease._guard()
        with tempfile.TemporaryDirectory(prefix='soundbox-smart-replay-') as area:
            captured = Path(area).resolve() / 'current.db'
            _copy_stable_snapshot(self.database, captured)
            conn = _connect(captured, readonly=True)
            try:
                if (_identity(conn) != (self.authority.library_id, '6000')
                        or _schema(conn) != self.authority.schema_sha256):
                    raise ConflictError('Smart-playlist replay source authority changed')
                for command in commands:
                    if command['operation'] in CREATE_SMART_OPERATIONS:
                        _require_smart_references(conn, command)
                    elif command['operation'] in MOVE_OPERATIONS:
                        _require_smart_move_references(conn,command,after=True)
                    else:
                        _require_smart_deletion_references(conn, command, deleted=True,
                            expected_target=after_targets[('djmdPlaylist', command['playlist_id'])],
                            deleted_parents=_tree_deleted_parents(request,after_targets))
                _require_tree_references(conn,request,deleted=True)
            finally:
                conn.close()
        self.lease._guard()

    def _check_scope(self, request):
        if request.get('operation') in ('batch','delete_tree'):
            for child in request['requests']:self._check_scope(child)
            return
        if request.get('operation')=='move':
            reason=node_move_reason(self.authority)
            if reason:raise WriteProofError(reason)
        if request.get('operation')=='create_folder_normalized':
            reason=normalized_folder_creation_reason(self.authority)
            if reason:raise WriteProofError(reason)
        if request.get('operation')=='create_normalized':
            reason=normalized_creation_reason(self.authority)
            if reason:raise WriteProofError(reason)
        if request.get('operation')=='reorder' and not self.authority.reorder_accepted:
            raise WriteProofError('Native playlist reordering requires separate acceptance')
        if request.get('operation') in {'rename_smart','reorder_smart','move_smart'}:
            reason=smart_editing_reason(self.authority,request['operation'])
            if reason:raise WriteProofError(reason)
        if request.get('operation') in CREATE_SMART_OPERATIONS:
            reason = (smart_normalized_creation_reason(self.authority) if request['operation']=='create_smart_normalized'
                      else smart_append_creation_reason(self.authority) if request['operation']=='create_smart_append'
                      else smart_creation_reason(self.authority))
            if reason:
                raise WriteProofError(reason)
        if request.get('operation') == 'delete_smart':
            reason = smart_deletion_reason(self.authority,
                profile=request['smart_delete_profile'])
            if reason:
                raise WriteProofError(reason)
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

    def snapshot(self, *, collection_pins=None,_transition_request=None,_transition_changes=None):
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
                if collection_pins is not None:_require_collection_pins(conn,collection_pins)
                if _transition_request is not None:
                    request=_transition_request
                    _require_tree_references(conn,request,deleted=True)
                    final=_affected(_transition_changes)[1]
                    for selected in (track for command in command_requests(request) for track in command['tracks']):
                        track=row(conn,'djmdContent',selected['track_id'])
                        if not track or track['UUID']!=selected['uuid'] or track['rb_local_deleted']!=0:
                            raise ConflictError('Native Collection track changed during committed readback')
                    for command in command_requests(request):
                        if command['operation'] in CREATE_SMART_OPERATIONS:
                            _require_smart_references(conn,command)
                        elif command['operation'] in MOVE_OPERATIONS:
                            _require_smart_move_references(conn,command,after=True)
                        elif command['operation']=='delete_smart':
                            _require_smart_deletion_references(conn,command,deleted=True,
                                expected_target=final[('djmdPlaylist',command['playlist_id'])],
                                deleted_parents=_tree_deleted_parents(request,final))
                return catalog(conn)
            finally:conn.close()

    def propose_smart_creation(self, request):
        """Fresh read-only proposal; grants no capability or source publisher."""
        validate_request(request)
        if request['operation'] not in CREATE_SMART_OPERATIONS:
            raise WriteProofError('Smart creation preview requires a create-only intent')
        from .rekordbox_db import _copy_stable_snapshot
        with tempfile.TemporaryDirectory(prefix='soundbox-smart-create-proposal-') as area:
            captured = Path(area).resolve() / 'master.db'
            _copy_stable_snapshot(self.database, captured)
            conn = _connect(captured, readonly=True)
            try:
                if (_identity(conn) != (self.authority.library_id, '6000')
                        or _schema(conn) != self.authority.schema_sha256):
                    raise ConflictError('Native smart-playlist read library/schema changed')
                self._guard_xml()
                return smart_creation_proposal(conn, request, self.xml.read_bytes())
            finally:
                conn.close()

    def propose_smart_metadata(self,request):
        """Read-only review candidate; neither proposal nor native capture accepts a writer."""
        validate_request(request)
        if request['operation'] not in {'rename_smart','reorder_smart'}:
            raise WriteProofError('Expected an independently marked intelligent metadata proposal')
        from .rekordbox_db import _copy_stable_snapshot
        with tempfile.TemporaryDirectory(prefix='soundbox-smart-metadata-proposal-') as area:
            captured=Path(area).resolve()/'master.db';_copy_stable_snapshot(self.database,captured)
            conn=_connect(captured,readonly=True)
            try:
                if _identity(conn)!=(self.authority.library_id,'6000') or _schema(conn)!=self.authority.schema_sha256:
                    raise ConflictError('Intelligent playlist proposal source authority changed')
                self._guard_xml()
                return {**build_plan(conn,request,self.xml.read_bytes()),'request':deepcopy(request),
                        'source_write_authority':False,'native_reference_required':request['operation']=='reorder_smart',
                        'profile':request['smart_edit_profile']}
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
    if request['operation'] not in ('batch','delete_tree'):return _single_plan(conn,request,before_xml)
    original=catalog(conn)
    if original['revision']!=request['revision']:
        raise ConflictError('Native playlist batch baseline changed; refresh and retry')
    if request['operation'] == 'delete_tree' and tree_bindings(original, request['playlist_id']) != request['descendants']:
        raise ConflictError('Playlist deletion descendants changed; the captured tree is preserved')
    if 'plan_encoding' in request:
        return _membership_replay_plan(conn, request, before_xml, original)
    current=deepcopy(original);counter=row(conn,'agentRegistry','localUpdateCount')
    # Reconstruct this private batch by exact row identity. Dict insertion order
    # matches append/replacement/deletion order in the catalog lists. Retain the
    # original first-row behavior for a malformed duplicate-ID catalog rather
    # than silently collapsing rows through an index.
    indexed={}
    for field in ('nodes','members'):
        values={r['ID']:r for r in current[field]}
        indexed[field]=values if len(values)==len(current[field]) else None
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
            by_id=indexed[field]
            if by_id is not None:
                if by_id.get(change['id'])!=change['before']:
                    raise ConflictError('Native batch row chain changed')
                if change['after'] is None:by_id.pop(change['id'],None)
                else:by_id[change['id']]=deepcopy(change['after'])
                continue
            rows=current[field];index=next((i for i,r in enumerate(rows) if r['ID']==change['id']),None)
            prior=rows[index] if index is not None else None
            if prior!=change['before']:raise ConflictError('Native batch row chain changed')
            if change['after'] is None:
                if index is not None:rows.pop(index)
            elif index is None:rows.append(deepcopy(change['after']))
            else:rows[index]=deepcopy(change['after'])
        for field,by_id in indexed.items():
            if by_id is not None:current[field]=list(by_id.values())
    current['revision']=planner.revision(current['nodes'],current['members'])
    return dict(changes=changes,xml=payload,catalog=current)


def _membership_replay_plan(conn, request, before_xml, original):
    """Compact accepted children without changing their final native effects."""
    remove, append = request['requests']
    counter = row(conn, 'agentRegistry', 'localUpdateCount')
    # Reuse the ordinary child guards and lexical XML transitions. One selected
    # occurrence per probe bounds validation; compose checks every content pin,
    # occurrence and native history before deriving the complete final rows.
    payload = before_xml
    for command in (remove, append):
        probe = {**command, 'tracks': command['tracks'][:1]}
        payload = _single_plan(conn, probe, payload,
                               current=original, counter=counter)['xml']
    identity = remove['playlist_id']
    target = next(node for node in original['nodes'] if node['ID'] == identity)
    members = [member for member in original['members'] if member['PlaylistID'] == identity]
    contents = {track_id: row(conn, 'djmdContent', track_id)
                for track_id in {track['track_id'] for track in remove['tracks']}}
    occupied = {value for member in original['members'] for value in (member['ID'], member['UUID'])}
    result = membership_replay.compose(target, members, counter, remove, append, contents, occupied)
    final = {member['ID']: member for member in result['members']}
    # Match the original batch's stable catalog insertion order as well as its
    # complete row values. Existing tombstones retain their catalog positions.
    values = [deepcopy(member) if member['PlaylistID'] != identity else final[member['ID']]
              for member in original['members']
              if member['PlaylistID'] != identity or member['ID'] in final]
    values.extend(final[track['id']] for track in append['tracks'])
    nodes = deepcopy(original['nodes'])
    current = dict(nodes=nodes, members=values, revision=planner.revision(nodes, values))
    return dict(changes=result['changes'], xml=payload, catalog=current)


def _normalized_creation_plan(conn,request,before_xml,*,current=None,counter=None):
    current=catalog(conn) if current is None else current
    if current['revision']!=request['revision']:raise ConflictError('Normalized creation baseline changed')
    smart=request['operation']=='create_smart_normalized'
    selected=create_plan.context(current['nodes'],request['parent'],
        _parent_limit=4096 if smart or request['operation']=='create_folder_normalized' else 64)
    tags=[]
    if smart:
        tags,fresh_counter=_require_smart_references(conn,request)
        if counter is not None and counter!=fresh_counter:raise ConflictError('Normalized smart counter differs from fresh source')
        counter=fresh_counter
    parent=selected['parent'];counter=row(conn,'agentRegistry','localUpdateCount') if counter is None else counter
    if parent is not None and parent['UUID']!=request['parent_uuid']:
        raise ConflictError('Normalized creation parent UUID changed')
    if any(node['rb_local_usn']>counter['int_1'] for node in
           (*move_plan._siblings(current['nodes'],'root'),*selected['siblings'],*([parent] if parent else []))):
        raise ConflictError('Normalized creation row clock exceeds source counter')
    if int(request['playlist_id']) in xml.identities(before_xml):raise ConflictError('New normalized identity collides with XML history')
    clocks=request['smart_clocks'] if smart else request['normalized_create_clocks']
    parameters=(dict(tag_ids=[tag['id'] for tag in tags],definition_timestamp=request['survivor_timestamp'])
                if smart else dict(name_timestamp=request['survivor_timestamp'],folder=request['folder']))
    proposed=(create_plan.smart_candidate if smart else create_plan.candidate)(current['nodes'],counter,
        identity=request['playlist_id'],uuid=request['playlist_uuid'],
        name=request['name'],parent=request['parent'],timestamp=request['timestamp'],
        position_timestamp=clocks['position_timestamp'],sibling_timestamps=clocks['sibling_timestamps'],
        **parameters)
    for node in (*selected['siblings'],*([parent] if parent else [])):
        attrs,_=xml._target(before_xml,node['ID'],created_smart=node['Attribute']==4)
        if (int(attrs['Attribute'])!=node['Attribute'] or int(attrs['ParentId'],16)!=
                (0 if node['ParentID']=='root' else int(node['ParentID']))):
            raise ConflictError('Normalized creation destination DB/XML differs')
    payload=(xml.append_smart_node(before_xml,request['playlist_id'],request['parent'])
             if smart else xml.append_node(before_xml,request['playlist_id'],request['parent'],int(request['folder'])))
    constructor_xml=payload
    if smart or proposed['positioned'] or request['name']!=('Untitled Folder' if request['folder'] else 'Untitled Playlist'):
        payload=xml._touch(payload,request['playlist_id'],request['xml_timestamp'],created_smart=smart)
    touched:tuple[str,...]=()
    if parent is not None:
        payload=xml.touch(payload,parent['ID'],clocks['parent_xml_timestamp']);touched=(parent['ID'],)
    first,final=_affected(proposed['changes'])
    net=[dict(table=table,id=identity,before=old,after=final[(table,identity)]) for (table,identity),old in first.items()]
    xml.validate_transition(before_xml,payload,net,touched_playlists=touched,
                            smart_created=(request['playlist_id'],) if smart else ())
    result=dict(proposed,catalog=current,xml=payload)
    if smart:result.update(tag_bindings=tags,smart_profile=request['smart_profile'],constructor_xml=constructor_xml)
    return result


def _normalized_create_clock_bundle(current,parent,*,folder=False):
    selected=create_plan.context(current['nodes'],parent,_parent_limit=4096 if folder else 64)
    stamp,clock=_timestamps();position,_=_timestamps()
    siblings={row['ID']:_timestamps()[0] for row in selected['siblings'] if row['ID'] in selected['shifted']}
    named,final_clock=_timestamps()
    return dict(timestamp=stamp,survivor_timestamp=named,xml_timestamp=final_clock,
        normalized_create_clocks=dict(position_timestamp=position,sibling_timestamps=siblings,
            parent_xml_timestamp=0 if parent=='root' else max(clock,final_clock,_timestamps()[1])))


def renew_normalized_create_request(request,current,*,operation_id=None):
    """Keep new identity/name/folder pins; renew only after proven causal admission."""
    validate_request(request)
    if request['operation'] not in ('create_normalized','create_folder_normalized'):
        raise WriteProofError('Expected normalized creation intention')
    renewed={**deepcopy(request),'revision':current['revision'],**_normalized_create_clock_bundle(current,request['parent'],folder=request['operation']=='create_folder_normalized')}
    if operation_id is not None:renewed['operation_id']=operation_id
    renewed['xml_timestamp']=max(request['xml_timestamp'],renewed['xml_timestamp'])
    if renewed['parent']!='root':
        renewed['normalized_create_clocks']['parent_xml_timestamp']=max(renewed['xml_timestamp'],
            request['normalized_create_clocks']['parent_xml_timestamp'],
            renewed['normalized_create_clocks']['parent_xml_timestamp'])
    validate_request(renewed);return renewed


def _validate_normalized_create(request):
    folder=request['operation']=='create_folder_normalized'
    expected=NORMALIZED_FOLDER_CREATE_PROFILE if folder else NORMALIZED_CREATE_PROFILE
    if (request['normalized_create_profile']!=expected or request['folder'] is not folder or request['allow_duplicate']):
        raise WriteProofError('Normalized creation requires its exact ordinary or folder profile')
    _smart_parent_uuid(request['parent'],request['parent_uuid'])
    clocks=request['normalized_create_clocks']
    if not isinstance(clocks,dict) or set(clocks)!={'position_timestamp','sibling_timestamps','parent_xml_timestamp'}:
        raise WriteProofError('Unknown normalized creation clock bundle')
    start=_smart_sql_clock(request['timestamp']);position=_smart_sql_clock(clocks['position_timestamp'])
    end=_smart_sql_clock(request['survivor_timestamp'])
    if not start<=position<=end:raise WriteProofError('Normalized creation phase clocks changed order')
    shifted=clocks['sibling_timestamps'];parent_clock=clocks['parent_xml_timestamp']
    if (not isinstance(shifted,dict) or len(shifted)>4096 or type(parent_clock) is not int
            or (parent_clock!=0 if request['parent']=='root' else not request['xml_timestamp']<=parent_clock<2**63)):
        raise WriteProofError('Invalid normalized creation destination clocks')
    for identity,clock in shifted.items():
        if (type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                or not 0<int(identity)<2**32 or str(int(identity))!=identity
                or not position<=_smart_sql_clock(clock)<=end):
            raise WriteProofError('Invalid normalized creation sibling clock')


def _require_smart_move_references(conn,request,*,after):
    target=row(conn,'djmdPlaylist',request['playlist_id'])
    expected_parent=request['parent'] if after else request['source_parent']
    if (target is None or target['UUID']!=request['playlist_uuid'] or target['Attribute']!=request.get('node_attribute',4)
            or target.get('SmartList')!=request['smart_rule'] or target['ParentID']!=expected_parent):
        raise ConflictError('Native smart move rule/identity/parent changed')
    planner._smart_editable(target)
    if request['operation']=='move':
        nodes=_rows(conn,'djmdPlaylist')
        if move_plan.subtree(nodes,request['playlist_id'])!=request['move_descendants']:
            raise ConflictError('Native moved folder descendants changed')
        if request['anchor_id']:
            anchor=row(conn,'djmdPlaylist',request['anchor_id'])
            if (anchor is None or anchor['UUID']!=request['anchor_uuid']
                    or anchor['Attribute']!=request['anchor_attribute'] or anchor['ParentID']!=request['parent']):
                raise ConflictError('Native placement anchor identity or parent changed')
            planner._smart_editable(anchor)
    counter=row(conn,'agentRegistry','localUpdateCount')
    for identity,uuid in ((request['source_parent'],request['source_parent_uuid']),(request['parent'],request['parent_uuid'])):
        if identity=='root':continue
        parent=row(conn,'djmdPlaylist',identity)
        if parent is None or parent['UUID']!=uuid or parent['Attribute']!=1:
            raise ConflictError('Native smart move folder identity changed')
        planner._ordinary(parent,folder=True)
        if parent['rb_local_usn']>counter['int_1']:raise ConflictError('Native move parent clock exceeds source counter')


def _smart_move_plan(conn,request,before_xml,*,current=None,counter=None):
    current=catalog(conn) if current is None else current
    if current['revision']!=request['revision']:raise ConflictError('Native smart move baseline changed')
    _require_smart_move_references(conn,request,after=False)
    generic=request['operation']=='move'
    arguments=dict(identity=request['playlist_id'],parent=request['parent'])
    if generic:arguments.update(anchor_id=request['anchor_id'] or None,after=request['after'])
    observed=(move_plan.node_context if generic else move_plan.context)(current['nodes'],**arguments)
    counter=row(conn,'agentRegistry','localUpdateCount') if counter is None else counter
    clocks=_move_clocks(request)
    proposed=(move_plan.node_candidate if generic else move_plan.candidate)(current['nodes'],counter,**arguments,
        timestamp=request['timestamp'],sibling_timestamps=clocks['sibling_timestamps'])
    for selected in (observed['target'],*observed['destination_siblings'],
                     *([observed['source_parent']] if observed['source_parent'] else []),
                     *([observed['parent']] if observed['parent'] else [])):
        attrs,_=xml._target(before_xml,selected['ID'],created_smart=selected['Attribute']==4)
        parent=0 if selected['ParentID']=='root' else int(selected['ParentID'])
        if int(attrs['ParentId'],16)!=parent or int(attrs['Attribute'])!=selected['Attribute']:
            raise ConflictError('Native smart move DB/XML endpoints differ')
    if not proposed['changes']:return dict(proposed,xml=before_xml,catalog=current)
    result=(xml.reparent_node(before_xml,request['playlist_id'],request['parent'],attribute=request['node_attribute'])
            if generic else xml.reparent_smart(before_xml,request['playlist_id'],request['parent']))
    result=xml._touch(result,request['playlist_id'],request['xml_timestamp'],created_smart=request.get('node_attribute',4)==4)
    touched:tuple[str,...]=()
    if request['parent']!='root':
        result=xml.touch(result,request['parent'],clocks['parent_xml_timestamp']);touched=(request['parent'],)
    movement={'node_moved' if generic else 'smart_moved':(request['playlist_id'],)}
    xml.validate_transition(before_xml,result,proposed['changes'],**movement,touched_playlists=touched)
    return dict(proposed,xml=result,catalog=current)


def _move_clocks(request):
    return request['node_move_clocks' if request['operation']=='move' else 'smart_move_clocks']


def _node_move_clock_bundle(current,identity,parent,anchor_id=None,after=False):
    observed=move_plan.node_context(current['nodes'],identity=identity,parent=parent,anchor_id=anchor_id,after=after)
    stamp,clock=_timestamps()
    shifted={row['ID']:_timestamps()[0] for row in observed['shifted']}
    parent_clock=0 if parent=='root' else max(clock,_timestamps()[1])
    return dict(timestamp=stamp,survivor_timestamp=max((stamp,*shifted.values())),xml_timestamp=clock,
        node_move_clocks=dict(sibling_timestamps=shifted,parent_xml_timestamp=parent_clock))


def renew_node_move_request(request,current,*,operation_id=None):
    """Renew measured clocks; keep endpoints, anchor, subtree and opaque rule."""
    validate_request(request)
    if request['operation']!='move':raise WriteProofError('Expected exact native node placement intention')
    result={**deepcopy(request),'revision':current['revision'],**_node_move_clock_bundle(current,
        request['playlist_id'],request['parent'],request['anchor_id'] or None,request['after'])}
    if operation_id is not None:result['operation_id']=operation_id
    validate_request(result);return result


def _smart_move_clock_bundle(current,identity,parent):
    context=move_plan.context(current['nodes'],identity=identity,parent=parent)
    stamp,clock=_timestamps()
    shifted={row['ID']:_timestamps()[0] for row in context['shifted']}
    parent_clock=0 if parent=='root' else max(clock,_timestamps()[1])
    return dict(timestamp=stamp,survivor_timestamp=max((stamp,*shifted.values())),xml_timestamp=clock,
        smart_move_clocks=dict(sibling_timestamps=shifted,parent_xml_timestamp=parent_clock))


def renew_smart_move_request(request,current,*,operation_id=None):
    """Renew clocks only after a proven edge or explicit retry; keep all pins."""
    validate_request(request)
    if request['operation']!='move_smart':raise WriteProofError('Expected exact smart move intention')
    result={**deepcopy(request),'revision':current['revision'],
            **_smart_move_clock_bundle(current,request['playlist_id'],request['parent'])}
    if operation_id is not None:result['operation_id']=operation_id
    validate_request(result);return result


def _validate_smart_move(request):
    if (request['smart_move_profile']!=SMART_MOVE_PROFILE or request['folder'] or request['allow_duplicate']
            or request['name'] or type(request['smart_rule']) is not str or not request['smart_rule']
            or len(request['smart_rule'])>1024*1024 or '\0' in request['smart_rule']):
        raise WriteProofError('Invalid independently marked smart move')
    source=request['source_parent']
    if (source!='root' and (type(source) is not str or not source.isascii() or not source.isdecimal()
            or not 0<int(source)<2**32 or str(int(source))!=source)):
        raise WriteProofError('Invalid original native move parent')
    if source==request['parent']:raise WriteProofError('Same-parent move requires ordinary ordering')
    _smart_parent_uuid(source,request['source_parent_uuid']);_smart_parent_uuid(request['parent'],request['parent_uuid'])
    clocks=request['smart_move_clocks']
    if not isinstance(clocks,dict) or set(clocks)!={'sibling_timestamps','parent_xml_timestamp'}:
        raise WriteProofError('Unknown smart move clock bundle')
    start=_smart_sql_clock(request['timestamp']);end=_smart_sql_clock(request['survivor_timestamp'])
    shifted=clocks['sibling_timestamps'];parent_clock=clocks['parent_xml_timestamp']
    if (not isinstance(shifted,dict) or len(shifted)>4096 or type(parent_clock) is not int
            or (parent_clock!=0 if request['parent']=='root' else not request['xml_timestamp']<=parent_clock<2**63)):
        raise WriteProofError('Invalid destination move clocks')
    for identity,clock in shifted.items():
        if (type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                or not 0<int(identity)<2**32 or str(int(identity))!=identity
                or not start<=_smart_sql_clock(clock)<=end):
            raise WriteProofError('Invalid destination sibling move clock')


def _validate_node_move(request):
    attribute=request['node_attribute'];rule=request['smart_rule']
    if (request['node_move_profile']!=NODE_MOVE_PROFILE or request['folder'] or request['allow_duplicate']
            or request['name'] or type(attribute) is not int or attribute not in (0,1,4)
            or attribute==4 and (type(rule) is not str or not rule or len(rule)>1024*1024 or '\0' in rule)
            or attribute!=4 and rule not in ('',None) or type(request['after']) is not bool):
        raise WriteProofError('Invalid independently marked native node placement')
    def native_id(identity):
        return (type(identity) is str and identity.isascii() and identity.isdecimal()
                and 0<int(identity)<2**32 and str(int(identity))==identity)
    source=request['source_parent']
    if source!='root' and not native_id(source):raise WriteProofError('Invalid original native node parent')
    _smart_parent_uuid(source,request['source_parent_uuid']);_smart_parent_uuid(request['parent'],request['parent_uuid'])
    anchor=request['anchor_id']
    if anchor:
        if (not native_id(anchor) or anchor==request['playlist_id'] or type(request['anchor_attribute']) is not int
                or request['anchor_attribute'] not in (0,1,4)):
            raise WriteProofError('Invalid native placement anchor')
        _smart_parent_uuid(anchor,request['anchor_uuid'])
    elif anchor!='' or request['anchor_uuid']!='' or request['anchor_attribute'] is not None or request['after']:
        raise WriteProofError('Native append cannot contain an anchor direction')
    descendants=request['move_descendants']
    if not isinstance(descendants,list) or len(descendants)>4095 or attribute!=1 and descendants:
        raise WriteProofError('Invalid native folder descendant inventory')
    seen={request['playlist_id']}
    for binding in descendants:
        if (not isinstance(binding,dict) or set(binding)!={'id','uuid','parent','attribute','rule'}
                or not native_id(binding['id']) or binding['id'] in seen or not native_id(binding['parent'])
                or type(binding['attribute']) is not int or binding['attribute'] not in (0,1,4)
                or binding['attribute']==4 and (type(binding['rule']) is not str or not binding['rule']
                    or len(binding['rule'])>1024*1024 or '\0' in binding['rule'])
                or binding['attribute']!=4 and binding['rule'] not in ('',None)):
            raise WriteProofError('Invalid native descendant identity, parent or rule')
        _smart_parent_uuid(binding['id'],binding['uuid']);seen.add(binding['id'])
    if request['parent'] in seen:raise WriteProofError('Native folder placement would create a cycle')
    clocks=request['node_move_clocks']
    if not isinstance(clocks,dict) or set(clocks)!={'sibling_timestamps','parent_xml_timestamp'}:
        raise WriteProofError('Unknown native node placement clock bundle')
    start=_smart_sql_clock(request['timestamp']);end=_smart_sql_clock(request['survivor_timestamp'])
    shifted=clocks['sibling_timestamps'];parent_clock=clocks['parent_xml_timestamp']
    if (not isinstance(shifted,dict) or len(shifted)>4096 or type(parent_clock) is not int
            or (parent_clock!=0 if request['parent']=='root' else not request['xml_timestamp']<=parent_clock<2**63)):
        raise WriteProofError('Invalid native placement destination clocks')
    for identity,clock in shifted.items():
        if not native_id(identity) or not start<=_smart_sql_clock(clock)<=end:
            raise WriteProofError('Invalid native placement sibling clock')


def _smart_metadata_plan(conn,request,before_xml,*,current=None,counter=None):
    """Independently gated candidate; no rule, membership or parent mutation."""
    current=catalog(conn) if current is None else current
    if current['revision']!=request['revision']:raise ConflictError('Native intelligent playlist revision changed')
    nodes=current['nodes'];target=next((r for r in nodes if r['ID']==request['playlist_id']),None)
    if (target is None or target['UUID']!=request['playlist_uuid']
            or target['ParentID']!=request['parent'] or target['Attribute']!=request['smart_attribute']
            or target.get('SmartList')!=request['smart_rule']):
        raise ConflictError('The captured intelligent playlist rule or identity changed')
    counter=row(conn,'agentRegistry','localUpdateCount') if counter is None else counter
    if request['operation']=='rename_smart':
        changes=planner.smart_rename_candidate(target,counter,name=request['name'],timestamp=request['timestamp'])['changes']
    else:
        anchor=next((r for r in nodes if r['ID']==request['anchor_id']),None)
        if anchor is None or anchor['UUID']!=request['anchor_uuid']:
            raise ConflictError('The captured intelligent playlist reorder anchor changed')
        changes=planner.smart_reorder_candidate(nodes,counter,identity=target['ID'],anchor_id=anchor['ID'],
            after=request['after'],timestamp=request['timestamp'],sibling_timestamps=request['smart_order_clocks'])['changes']
    modified=set()
    for change in changes:
        if change['table']!='djmdPlaylist':continue
        old=change['before'];identity=old['ID'];smart=old['Attribute']==4
        attributes,_=xml._target(before_xml,identity,created_smart=smart)
        if (int(attributes['Attribute'])!=old['Attribute']
                or int(attributes['ParentId'],16)!=(0 if old['ParentID']=='root' else int(old['ParentID']))):
            raise ConflictError('Intelligent playlist DB/XML type or parent differs')
        if smart:modified.add(identity)
    after_xml=before_xml
    touched=(target['ID'],) if changes else ()
    if changes:
        after_xml=xml._touch(before_xml,target['ID'],request['xml_timestamp'],created_smart=target['Attribute']==4)
        if request['operation']=='reorder_smart' and target['ParentID']!='root':
            parent=next((row for row in nodes if row['ID']==target['ParentID']),None)
            if parent is None or parent['Attribute']!=1:
                raise ConflictError('The captured intelligent playlist parent folder disappeared')
            planner._ordinary(parent,folder=True)
            parent_attributes,_=xml._target(before_xml,parent['ID'])
            if (parent_attributes['Attribute']!='1'
                    or int(parent_attributes['ParentId'],16)!=(0 if parent['ParentID']=='root' else int(parent['ParentID']))
                    or parent['rb_local_usn']>counter['int_1']):
                raise ConflictError('Intelligent playlist parent DB/XML or local clock differs')
            after_xml=xml.touch(after_xml,parent['ID'],request['smart_parent_xml_timestamp'])
            touched+= (parent['ID'],)
    xml.validate_transition(before_xml,after_xml,changes,smart_modified=tuple(sorted(modified)),
                            touched_playlists=touched)
    return dict(changes=changes,xml=after_xml,catalog=current)


def _single_plan(conn, request, before_xml, *, current=None,counter=None):
    from datetime import datetime
    validate_request(request)
    if request['operation'] in ('create_normalized','create_folder_normalized'):
        return _normalized_creation_plan(conn,request,before_xml,current=current,counter=counter)
    if request['operation'] in MOVE_OPERATIONS:
        return _smart_move_plan(conn,request,before_xml,current=current,counter=counter)
    if request['operation'] in CREATE_SMART_OPERATIONS:
        return _smart_creation_plan(conn, request, before_xml, current=current, counter=counter)
    if request['operation'] == 'delete_smart':
        return _smart_deletion_plan(conn, request, before_xml, current=current, counter=counter)
    if request['operation'] in {'rename_smart','reorder_smart'}:
        return _smart_metadata_plan(conn,request,before_xml,current=current,counter=counter)
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
        if 'parent_uuid' in request and request['parent']!='root':
            parent=next((node for node in nodes if node['ID']==request['parent']),None)
            if parent is None or parent['UUID']!=request['parent_uuid']:
                raise ConflictError('The captured playlist folder disappeared or was replaced')
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
                    if 'membership_id' in track:
                        matches=[r for r in matches if r['ID']==track['membership_id']
                                 and r['UUID']==track['source_membership_uuid']]
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


def _smart_parent_uuid(parent, value):
    if parent == 'root':
        if value != '':
            raise WriteProofError('Root smart-playlist parent has no native UUID')
        return
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError('UUID')
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteProofError('Smart-playlist parent UUID is unavailable') from exc


def _smart_tag_binding(tag):
    names = ('id', 'uuid', 'name', 'category', 'category_id', 'category_uuid')
    if isinstance(tag, dict):
        if set(tag) - set(names):
            raise ValueError('Unknown smart-playlist My Tag binding')
        result = {name: tag.get(name, '') for name in names}
    else:
        result = dict(id=tag.tag_id, uuid=tag.source_uuid, name=tag.name, category=tag.category,
                      category_id=getattr(tag, 'category_id', ''),
                      category_uuid=getattr(tag, 'category_uuid', ''))
    if not result['category_id'] and not result['category']:
        result['category_id'] = 'root'
    return result


def _tag_reference_uuid(value):
    # Native built-in categories include opaque identities such as Genre's
    # literal "1". They are read-only exact pins, never generated SQL UUIDs.
    if type(value) is not str or not value or len(value) > 4096 or '\0' in value:
        raise WriteProofError('Native My Tag reference UUID is unavailable')


def _tag_reference_state(tag, local_counter):
    """Observed active read-reference envelopes; grants no tag-definition write."""
    fields = ('rb_data_status', 'rb_local_data_status', 'rb_local_deleted', 'rb_local_synced', 'rb_local_usn')
    if (any(type(tag.get(field)) is not int for field in fields)
            or tag['rb_local_data_status'] != 0 or tag['rb_local_deleted'] != 0
            or not 0 <= tag['rb_local_usn'] <= local_counter < 2**63):
        raise ConflictError('Unverified active My Tag reference state')
    local = (tag['rb_data_status'] == 0 and tag['rb_local_synced'] in (0, 1) and tag.get('usn') is None)
    historical = (tag['rb_data_status'] in (256, 257) and tag['rb_local_synced'] == 1
                  and type(tag.get('usn')) is int and 0 <= tag['usn'] < 2**63)
    if not (local or historical):
        raise ConflictError('Unverified active My Tag reference state')
    _tag_reference_uuid(tag.get('UUID'))


def _validate_smart_tags(tags):
    if not isinstance(tags, list) or not 1 <= len(tags) <= 64:
        raise WriteProofError('Select one to 64 My Tags for the new playlist')
    fields = {'id', 'uuid', 'name', 'category', 'category_id', 'category_uuid'}
    identities, uuids = set(), set()
    for tag in tags:
        if (not isinstance(tag, dict) or set(tag) != fields
                or any(type(value) is not str or len(value) > 4096 or '\0' in value for value in tag.values())
                or not tag['name'].strip()):
            raise WriteProofError('Invalid smart-playlist My Tag binding')
        for name in ('id', 'category_id'):
            value = tag[name]
            if name == 'category_id' and value == 'root':
                continue
            if (not value.isascii() or not value.isdecimal()
                    or not 0 < int(value) < 2**32 or str(int(value)) != value):
                raise WriteProofError('Invalid smart-playlist My Tag identity')
        _tag_reference_uuid(tag['uuid'])
        if tag['category_id'] == 'root':
            if tag['category_uuid'] != '':
                raise WriteProofError('Root My Tag reference has no category UUID')
        else:
            _tag_reference_uuid(tag['category_uuid'])
        if ((tag['category_id'] == 'root') != (tag['category'] == '')
                or tag['id'] in identities or tag['uuid'] in uuids):
            raise WriteProofError('Ambiguous smart-playlist My Tag binding')
        identities.add(tag['id']); uuids.add(tag['uuid'])


def _fresh_smart_tags(conn, selections, counter):
    """Bind leaves and their categories to one fresh source observation."""
    _validate_smart_tags(selections)
    definitions = _rows(conn, 'djmdMyTag')
    for selected in selections:
        leaves = [tag for tag in definitions if tag.get('ID') == selected['id']]
        if len(leaves) != 1:
            raise ConflictError('Selected My Tag is missing or ambiguous')
        leaf = leaves[0]
        _tag_reference_state(leaf, counter['int_1'])
        if (leaf.get('Attribute') != 0 or leaf.get('UUID') != selected['uuid']
                or leaf.get('Name') != selected['name'] or leaf.get('ParentID') != selected['category_id']
                or sum(tag.get('UUID') == selected['uuid'] for tag in definitions) != 1):
            raise ConflictError('Selected My Tag changed; refresh before creating its playlist')
        if selected['category_id'] == 'root':
            continue
        categories = [tag for tag in definitions if tag.get('ID') == selected['category_id']]
        if len(categories) != 1:
            raise ConflictError('Selected My Tag category is missing or ambiguous')
        category = categories[0]
        _tag_reference_state(category, counter['int_1'])
        if (category.get('Attribute') != 1 or category.get('UUID') != selected['category_uuid']
                or category.get('Name') != selected['category']
                or sum(tag.get('UUID') == selected['category_uuid'] for tag in definitions) != 1):
            raise ConflictError('Selected My Tag category changed; refresh before creating its playlist')
    return deepcopy(selections)


def smart_creation_proposal(conn, request, before_xml):
    """Complete copy plan; source authority still requires its explicit profile.

    Constructor/placement and the combined Name+rule save are observed native
    windows. Their counters are not inferred individual SQL statement counts.
    This callable remains suitable for a read-only captured reference/TEMP proof.
    """
    plan = _smart_creation_plan(conn, request, before_xml)
    return dict(plan, format='soundbox-smart-playlist-create-proposal-v1',
                request=deepcopy(request), source_write_authority=False,
                provisional=True, complete_sql_plan=True)


def _smart_deletion_context(conn, request, before_xml, *, current=None, counter=None):
    """Fresh exact read-only inputs for the measured deletion primitive."""
    validate_request(request)
    if request['operation'] != 'delete_smart':
        raise WriteProofError('Smart deletion preview requires a delete-only intent')
    current = catalog(conn) if current is None else current
    if current['revision'] != request['revision']:
        raise ConflictError('Smart playlist baseline changed before deletion preview')
    context = planner.smart_delete_context(current['nodes'], current['members'],
        row(conn, 'agentRegistry', 'localUpdateCount') if counter is None else counter, identity=request['playlist_id'],
        uuid=request['playlist_uuid'], parent=request['parent'], parent_uuid=request['parent_uuid'],
        rule=request['smart_rule'])
    synced = request['smart_delete_profile'] == SMART_LOCAL_DELETE_PROFILE
    if context['target']['rb_local_synced'] != int(synced):
        raise ConflictError('Smart deletion history differs from its captured profile')
    attributes, _ = xml._target(before_xml, request['playlist_id'], created_smart=True)
    expected_parent = 0 if request['parent'] == 'root' else int(request['parent'])
    if int(attributes['ParentId'], 16) != expected_parent:
        raise ConflictError('Smart playlist deletion DB/XML parent differs')
    if request['parent'] != 'root':
        parent_attributes, _ = xml._target(before_xml, request['parent'])
        parent = context['parent']
        parent_id = 0 if parent['ParentID'] == 'root' else int(parent['ParentID'])
        if parent_attributes['Attribute'] != '1' or int(parent_attributes['ParentId'], 16) != parent_id:
            raise ConflictError('Smart playlist deletion parent DB/XML identity differs')
    return dict(context, request=deepcopy(request), xml=before_xml, catalog=current)


def smart_deletion_proposal(conn, request, before_xml):
    """Complete measured TEMP plan; this supplies no source capability."""
    return dict(_smart_deletion_plan(conn, request, before_xml), request=deepcopy(request),
                complete_sql_plan=True, source_write_authority=False)


def _require_smart_deletion_references(conn, request, *, deleted, expected_target=None, deleted_parents=None):
    counter = row(conn, 'agentRegistry', 'localUpdateCount')
    if counter is None or type(counter.get('int_1')) is not int or not 0 <= counter['int_1'] < 2**63:
        raise ConflictError('Smart deletion source counter is invalid')
    if request['parent'] != 'root':
        parent = row(conn, 'djmdPlaylist', request['parent'])
        if deleted and deleted_parents is not None and request['parent'] in deleted_parents:
            if parent != deleted_parents[request['parent']]:
                raise ConflictError('Deleted containing folder differs from its exact tree plan')
        else:
            if parent is None or parent['UUID'] != request['parent_uuid']:
                raise ConflictError('Smart deletion parent disappeared or was replaced')
            planner._ordinary(parent, folder=True)
            if parent['Attribute'] != 1 or parent['rb_local_usn'] > counter['int_1']:
                raise ConflictError('Smart deletion destination is no longer a verified folder')
    target = row(conn, 'djmdPlaylist', request['playlist_id'])
    synced = request['smart_delete_profile'] == SMART_LOCAL_DELETE_PROFILE
    if deleted:
        nodes = _rows(conn, 'djmdPlaylist')
        if synced:
            if (expected_target is None or target != expected_target
                    or target is None or target.get('UUID') != request['playlist_uuid']
                    or target.get('ParentID') != request['parent'] or target.get('SmartList') != request['smart_rule']
                    or target.get('Attribute') != 4
                    or any(type(target.get(field)) is not int or target[field] != value for field, value in
                           (('rb_data_status', 2), ('rb_local_data_status', 0),
                            ('rb_local_deleted', 1), ('rb_local_synced', 1)))
                    or target.get('usn') is not None or type(target.get('rb_local_usn')) is not int
                    or not 0 <= target['rb_local_usn'] <= counter['int_1']):
                raise ConflictError('Deleted smart-playlist tombstone changed; preserve its receipt')
        elif target is not None:
            raise ConflictError('Deleted smart-playlist identity reappeared; refresh before acknowledging')
        if (any((node.get('UUID') == request['playlist_uuid'] and node['ID'] != request['playlist_id'])
                or node.get('ParentID') == request['playlist_id'] for node in nodes)
                or any(member.get('PlaylistID') == request['playlist_id']
                       for member in _rows(conn, 'djmdSongPlaylist'))):
            raise ConflictError('Deleted smart-playlist UUID, children or membership history reappeared')
    else:
        current = catalog(conn)
        planner.smart_delete_context(current['nodes'], current['members'], counter,
            identity=request['playlist_id'], uuid=request['playlist_uuid'], parent=request['parent'],
            parent_uuid=request['parent_uuid'], rule=request['smart_rule'])
        if target is None or target['rb_local_synced'] != int(synced):
            raise ConflictError('Smart deletion history differs from its captured profile')


def _smart_deletion_plan(conn, request, before_xml, *, current=None, counter=None):
    # The measured open-native SQL window is pure/copy-only until the separate
    # capability admits its completed reference profile. Never borrow CRUD.
    preview = _smart_deletion_context(conn, request, before_xml, current=current, counter=counter)
    observed = preview['counter']
    if counter is not None and counter != observed:
        raise ConflictError('Smart deletion counter differs from its fresh baseline')
    clocks = request['smart_delete_clocks']
    following = [row for row in preview['siblings'] if row['Seq'] > preview['target']['Seq']]
    actual_clocks = [_smart_sql_clock(clocks.get(row['ID'])) for row in following]
    if actual_clocks != sorted(actual_clocks):
        raise ConflictError('Smart deletion clocks changed sibling order')
    for sibling in following:
        attributes, _ = xml._target(before_xml, sibling['ID'], created_smart=sibling['Attribute'] == 4)
        parent = 0 if request['parent'] == 'root' else int(request['parent'])
        if (int(attributes['ParentId'], 16) != parent
                or int(attributes['Attribute']) != sibling['Attribute']):
            raise ConflictError('Smart deletion sibling DB/XML identity differs')
    current = preview['catalog']
    result = planner.delete_smart(current['nodes'], current['members'], observed,
        identity=request['playlist_id'], uuid=request['playlist_uuid'],
        parent=request['parent'], parent_uuid=request['parent_uuid'], rule=request['smart_rule'],
        sibling_timestamps=clocks, timestamp=request['timestamp'])
    xml.validate_transition(before_xml, before_xml, result['changes'], smart_deleted=(request['playlist_id'],))
    return dict(result, xml=before_xml, catalog=current)


def _require_smart_references(conn, request):
    counter = row(conn, 'agentRegistry', 'localUpdateCount')
    if counter is None:
        raise ConflictError('Native local update counter is unavailable')
    if (counter.get('registry_id') != 'localUpdateCount' or type(counter.get('int_1')) is not int
            or not 0 <= counter['int_1'] < 2**63):
        raise ConflictError('Native local update counter is invalid')
    selected = _fresh_smart_tags(conn, request['tags'], counter)
    if request['parent'] != 'root':
        parent = row(conn, 'djmdPlaylist', request['parent'])
        if parent is None or parent['UUID'] != request['parent_uuid']:
            raise ConflictError('Native smart-playlist parent changed or was replaced')
        planner._ordinary(parent, folder=True)
        if parent['Attribute'] != 1 or parent['rb_local_usn'] > counter['int_1']:
            raise ConflictError('Native smart-playlist destination is no longer a folder')
    return selected, counter


def _smart_creation_plan(conn, request, before_xml, *, current=None, counter=None):
    validate_request(request)
    if request['operation'] not in CREATE_SMART_OPERATIONS:
        raise WriteProofError('Smart creation proposal requires its create-only intent')
    if request['operation']=='create_smart_normalized':
        return _normalized_creation_plan(conn,request,before_xml,current=current,counter=counter)
    current = catalog(conn) if current is None else current
    if current['revision'] != request['revision']:
        raise ConflictError('Native playlist baseline changed before smart creation preview')
    selected, observed_counter = _require_smart_references(conn, request)
    if counter is not None and counter != observed_counter:
        raise ConflictError('Smart creation counter differs from its fresh source base')
    counter = observed_counter
    append=request['operation']=='create_smart_append'
    # Reconstruct accepted historical request windows with their original
    # planner rules. Current UI eligibility additionally checks ROOT placement;
    # it cannot invalidate an older committed folder-creation receipt.
    reason = (planner.smart_append_parent_reason(current['nodes'],request['parent']) if append
              else planner.smart_parent_reason(current['nodes'],request['parent']))
    if reason:
        raise WriteProofError(reason)
    parent_xml = before_xml
    if request['parent'] != 'root':
        parents = [node for node in current['nodes'] if node['ID'] == request['parent']]
        if len(parents) != 1 or parents[0]['UUID'] != request['parent_uuid']:
            raise ConflictError('Native smart-playlist parent changed or was replaced')
        attributes, _ = xml._target(before_xml, request['parent'])
        xml_parent = 0 if parents[0]['ParentID'] == 'root' else int(parents[0]['ParentID'])
        if attributes['Attribute'] != '1' or int(attributes['ParentId'], 16) != xml_parent:
            raise ConflictError('Native smart-playlist parent DB/XML identity differs')
        siblings = planner._siblings(current['nodes'], request['parent'])
        for sibling in siblings:
            attributes, _ = xml._target(before_xml, sibling['ID'], created_smart=sibling['Attribute']==4 if append else True)
            if int(attributes['ParentId'], 16) != int(request['parent']):
                raise ConflictError('Native smart sibling DB/XML parent differs')
            if sibling['rb_local_usn'] > counter['int_1']:
                raise ConflictError('Native smart sibling clock exceeds the current source counter')
        if not append:
            placed = [_smart_sql_clock(request['smart_clocks']['position_timestamp']),
                      *[_smart_sql_clock(request['smart_clocks']['sibling_timestamps'].get(node['ID']))
                        for node in siblings]]
            if placed != sorted(placed):
                raise ConflictError('Native smart placement clocks changed sibling order')
        parent_xml = xml.touch(before_xml, request['parent'], request['smart_clocks']['parent_xml_timestamp'])
    if int(request['playlist_id']) in xml.identities(before_xml):
        raise ConflictError('Smart-playlist identity collides with XML history')
    clocks = request['smart_clocks']
    create=planner.create_smart_append if append else planner.create_smart
    extra={} if append else dict(position_timestamp=clocks.get('position_timestamp'),
                                 sibling_timestamps=clocks.get('sibling_timestamps'))
    proposed = create(current['nodes'], counter,
        identity=request['playlist_id'], uuid=request['playlist_uuid'], name=request['name'],
        tag_ids=[tag['id'] for tag in selected], timestamp=request['timestamp'],
        definition_timestamp=request['survivor_timestamp'], parent=request['parent'],**extra)
    constructor_xml = xml.append_smart_node(parent_xml, request['playlist_id'], request['parent'])
    desired_xml = xml._touch(constructor_xml, request['playlist_id'], request['xml_timestamp'], created_smart=True)
    first, final = _affected(proposed['changes'])
    # Preserve the ordered SQL chain separately. XML sees the exact absent→final
    # new row and narrowly scoped sibling position fields, never a generic
    # existing intelligent-list editor.
    xml_changes = [dict(table=table, id=identity, before=before, after=final[(table, identity)])
                   for (table, identity), before in first.items()]
    xml.validate_transition(before_xml, desired_xml, xml_changes,
        touched_playlists=(request['parent'],) if request['parent'] != 'root' else (),
        smart_created=(request['playlist_id'],))
    return dict(proposed, xml=desired_xml, constructor_xml=constructor_xml,
                catalog=current, tag_bindings=selected, smart_profile=request['smart_profile'])


def _smart_clock_bundle(current, parent, *, append=False, normalized=False):
    if normalized:
        selected=create_plan.context(current['nodes'],parent,_parent_limit=4096)
        stamp,clock=_timestamps();position,_=_timestamps()
        shifts={row['ID']:_timestamps()[0] for row in selected['siblings'] if row['ID'] in selected['shifted']}
        definition,final_clock=_timestamps()
        return dict(timestamp=stamp,survivor_timestamp=definition,xml_timestamp=final_clock,
            smart_clocks=dict(position_timestamp=position,sibling_timestamps=shifts,
                parent_xml_timestamp=0 if parent=='root' else max(clock,final_clock,_timestamps()[1])))
    stamp, clock = _timestamps()
    if append:
        reason=smart_append_creation_parent_reason(current,parent)
        if reason:raise WriteProofError(reason)
        definition,final_xml=_timestamps()
        return dict(timestamp=stamp,survivor_timestamp=definition,xml_timestamp=final_xml,
                    smart_clocks=dict(parent_xml_timestamp=clock))
    if parent == 'root':
        return dict(timestamp=stamp, survivor_timestamp=stamp, xml_timestamp=clock, smart_clocks={})
    reason = smart_creation_parent_reason(current, parent)
    if reason:
        raise WriteProofError(reason)
    siblings = planner._siblings(current['nodes'], parent)
    position, _ = _timestamps()
    shifts = {node['ID']: _timestamps()[0] for node in siblings}
    definition, final_xml = _timestamps()
    return dict(timestamp=stamp, survivor_timestamp=definition, xml_timestamp=final_xml,
        smart_clocks=dict(position_timestamp=position, sibling_timestamps=shifts, parent_xml_timestamp=clock))


def _smart_delete_clock_bundle(current, identity):
    reason = planner.smart_delete_reason(current['nodes'], identity)
    if reason:
        raise WriteProofError(reason)
    target = next(row for row in current['nodes'] if row['ID'] == identity)
    following = [row for row in planner._siblings(current['nodes'], target['ParentID'])
                 if row['Seq'] > target['Seq']]
    stamp, clock = _timestamps()
    mapping = {row['ID']: _timestamps()[0] for row in following}
    end = list(mapping.values())[-1] if mapping else stamp
    return dict(timestamp=stamp, survivor_timestamp=end, xml_timestamp=clock, smart_delete_clocks=mapping)


def renew_smart_delete_request(request, current, *, operation_id=None):
    """Persist renewed clocks only after a proven causal edge or explicit retry."""
    validate_request(request)
    if request['operation'] != 'delete_smart':
        raise WriteProofError('Smart deletion renewal requires a delete-only intent')
    result = {**deepcopy(request), 'revision': current['revision'],
              **_smart_delete_clock_bundle(current, request['playlist_id'])}
    if operation_id is not None:
        result['operation_id'] = operation_id
    validate_request(result)
    return result


def renew_smart_request(request, current, *, operation_id=None):
    """Capture a new durable clock/mapping bundle after a proven rebase or retry.

    This helper supplies no causal/source authority. The caller must first prove
    an unstarted causal edge or explicit retry and persist the returned intent
    before admission. Captured target, parent UUID and tag references stay exact.
    """
    validate_request(request)
    if request['operation'] not in CREATE_SMART_OPERATIONS:
        raise WriteProofError('Smart clock renewal requires a create-only intent')
    result = {**deepcopy(request), 'revision': current['revision'],
              **_smart_clock_bundle(current, request['parent'],append=request['operation']=='create_smart_append',
                                   normalized=request['operation']=='create_smart_normalized')}
    if operation_id is not None:
        result['operation_id'] = operation_id
    if request['operation']=='create_smart_normalized':
        result['xml_timestamp']=max(request['xml_timestamp'],result['xml_timestamp'])
        if request['parent']!='root':
            result['smart_clocks']['parent_xml_timestamp']=max(result['xml_timestamp'],
                request['smart_clocks']['parent_xml_timestamp'],result['smart_clocks']['parent_xml_timestamp'])
    validate_request(result)
    return result


def normalized_smart_request(request,current,*,operation_id=None):
    """Recapture a create-only intention under its separate normalized profile.

    Callers must have explicit retry or a proven unstarted causal edge, confirm
    the new capability and durably save this request before dispatch. Captured
    playlist ID/UUID/name, destination UUID and My Tag bindings stay exact.
    """
    validate_request(request)
    if request['operation'] not in CREATE_SMART_OPERATIONS:
        raise WriteProofError('Normalized smart renewal requires a create-only intention')
    result={**deepcopy(request),'operation':'create_smart_normalized',
        'smart_profile':SMART_NORMALIZED_CREATE_PROFILE,'revision':current['revision'],
        **_smart_clock_bundle(current,request['parent'],normalized=True)}
    if operation_id is not None:result['operation_id']=operation_id
    result['xml_timestamp']=max(request['xml_timestamp'],result['xml_timestamp'])
    if result['parent']!='root':
        result['smart_clocks']['parent_xml_timestamp']=max(result['xml_timestamp'],
            request['smart_clocks'].get('parent_xml_timestamp',0),result['smart_clocks']['parent_xml_timestamp'])
    validate_request(result);return result


def _smart_sql_clock(value):
    from datetime import datetime
    try:
        if type(value) is not str:
            raise ValueError('Clock')
        parsed = datetime.fromisoformat(value.replace(' +00:00', '+00:00'))
        canonical = parsed.strftime('%Y-%m-%d %H:%M:%S.') + f'{parsed.microsecond//1000:03d} +00:00'
        if value != canonical:
            raise ValueError('Clock')
        return parsed
    except (TypeError, ValueError, AttributeError) as exc:
        raise WriteProofError('Invalid native smart-playlist phase clock') from exc


def _validate_smart_clocks(request):
    clocks = request['smart_clocks']
    if not isinstance(clocks, dict):
        raise WriteProofError('Invalid native smart-playlist clock bundle')
    if request['operation']=='create_smart_normalized':
        _validate_normalized_create({**request,'normalized_create_profile':NORMALIZED_CREATE_PROFILE,
                                     'normalized_create_clocks':clocks})
        return
    created = _smart_sql_clock(request['timestamp'])
    definition = _smart_sql_clock(request['survivor_timestamp'])
    if created > definition:
        raise WriteProofError('Native smart definition precedes its constructor')
    if request['operation']=='create_smart_append':
        if (request['parent']=='root' or set(clocks)!={'parent_xml_timestamp'}
                or type(clocks['parent_xml_timestamp']) is not int
                or not 0<clocks['parent_xml_timestamp']<2**63):
            raise WriteProofError('Invalid native smart append-folder clock bundle')
        return
    if request['parent'] == 'root':
        if clocks:
            raise WriteProofError('Root smart creation has no folder placement clock bundle')
        return
    if set(clocks) != {'position_timestamp', 'sibling_timestamps', 'parent_xml_timestamp'}:
        raise WriteProofError('Unknown native smart-folder clock bundle')
    shifted = clocks['sibling_timestamps']
    if (not isinstance(shifted, dict) or not 1 <= len(shifted) <= 4096
            or type(clocks['parent_xml_timestamp']) is not int
            or not 0 < clocks['parent_xml_timestamp'] < 2**63):
        raise WriteProofError('Invalid native smart-folder placement clocks')
    position = _smart_sql_clock(clocks['position_timestamp'])
    if not created <= position <= definition:
        raise WriteProofError('Native smart-folder position clock is outside its phase window')
    for identity, timestamp in shifted.items():
        if (type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                or not 0 < int(identity) < 2**32 or str(int(identity)) != identity
                or not position <= _smart_sql_clock(timestamp) <= definition):
            raise WriteProofError('Invalid native smart-folder sibling clock')


def request_for(catalog, operation, *, identity=None, name='', parent='root', folder=False,
                tracks=(), allow_duplicate=False,anchor_id=None,after=False,tags=(),match='any',memberships=None):
    if operation == 'delete_tree':
        if tracks or tags or memberships is not None or folder or allow_duplicate or match != 'any':
            raise WriteProofError('Tree deletion does not admit membership or creation options')
        return tree_delete_for(catalog, identity)
    nodes=catalog['nodes'];target=next((r for r in nodes if r['ID']==identity),None)
    if operation in (*CREATE_ORDINARY_OPERATIONS, *CREATE_SMART_OPERATIONS):
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
    if operation in CREATE_ORDINARY_OPERATIONS:
        parents=[node for node in nodes if node['ID']==parent and node['rb_local_deleted']==0]
        if parent!='root' and (len(parents)!=1 or parents[0]['Attribute']!=1):
            raise WriteProofError('The selected playlist folder is no longer available')
        request['parent_uuid']='' if parent=='root' else parents[0]['UUID']
    if operation in ('create_normalized','create_folder_normalized'):
        request.update(normalized_create_profile=(NORMALIZED_FOLDER_CREATE_PROFILE if operation=='create_folder_normalized' else NORMALIZED_CREATE_PROFILE),
                       **_normalized_create_clock_bundle(catalog,parent,folder=operation=='create_folder_normalized'))
    if memberships is not None:
        if operation != 'remove' or len(memberships) != len(request['tracks']):
            raise WriteProofError('Exact membership selections belong only to playlist removal')
        for selected, track in zip(memberships, request['tracks'], strict=True):
            if (not isinstance(selected, dict) or selected.get('PlaylistID') != identity
                    or selected.get('ContentID') != track['track_id']):
                raise WriteProofError('Selected native playlist occurrence changed')
            matches = [row for row in catalog['members'] if row['PlaylistID'] == identity
                       and row['ContentID'] == track['track_id'] and row['rb_local_deleted'] == 0
                       and row['ID'] == selected.get('ID') and row['UUID'] == selected.get('UUID')]
            if len(matches) != 1:
                raise WriteProofError('Selected native playlist occurrence disappeared or was replaced')
            track.update(membership_id=selected.get('ID'), source_membership_uuid=selected.get('UUID'))
    if operation in ('reorder','reorder_smart'):
        anchor=next((r for r in nodes if r['ID']==anchor_id),None)
        if anchor is None or target is None:raise ValueError('Native reorder anchor is no longer in the library')
        request.update(parent=target['ParentID'],anchor_id=anchor_id,anchor_uuid=anchor['UUID'],after=after)
    if operation in ('rename_smart','reorder_smart'):
        assert target is not None
        request.update(parent=target['ParentID'],smart_attribute=target['Attribute'],smart_rule=target.get('SmartList'),
            smart_edit_profile=SMART_RENAME_PROFILE if operation=='rename_smart' else SMART_REORDER_PROFILE)
    if operation=='reorder_smart':
        _,shifted,_=planner.smart_reorder_rows(nodes,identity=identity,anchor_id=anchor_id,after=after)
        request['smart_order_clocks']={row['ID']:_timestamps()[0] for row in shifted}
        request['survivor_timestamp']=max((stamp,*request['smart_order_clocks'].values()))
        request['smart_parent_xml_timestamp']=0 if request['parent']=='root' else max(clock,_timestamps()[1])
    if operation=='move_smart':
        if target is None:raise WriteProofError('Native smart move target is unavailable')
        context=move_plan.context(nodes,identity=identity,parent=parent)
        request.update(parent_uuid=context['parent']['UUID'] if context['parent'] else '',
            source_parent=target['ParentID'],source_parent_uuid=context['source_parent']['UUID'] if context['source_parent'] else '',
            smart_rule=target['SmartList'],smart_move_profile=SMART_MOVE_PROFILE,
            **_smart_move_clock_bundle(catalog,identity,parent))
    if operation=='move':
        if target is None:raise WriteProofError('Native move target is unavailable')
        observed=move_plan.node_context(nodes,identity=identity,parent=parent,anchor_id=anchor_id,after=after)
        anchor=observed['anchor']
        request.update(parent_uuid=observed['parent']['UUID'] if observed['parent'] else '',
            source_parent=target['ParentID'],source_parent_uuid=observed['source_parent']['UUID'] if observed['source_parent'] else '',
            node_attribute=target['Attribute'],smart_rule=target.get('SmartList'),node_move_profile=NODE_MOVE_PROFILE,
            move_descendants=observed['descendants'],anchor_id=anchor_id or '',anchor_uuid=anchor['UUID'] if anchor else '',
            anchor_attribute=anchor['Attribute'] if anchor else None,after=after,
            **_node_move_clock_bundle(catalog,identity,parent,anchor_id,after))
    if operation in CREATE_SMART_OPERATIONS:
        parent_uuid = ''
        if parent != 'root':
            parent_row = next((node for node in nodes if node['ID'] == parent), None)
            if parent_row is None:
                raise ValueError('Native smart-playlist parent is unavailable')
            parent_uuid = parent_row['UUID']
        request.update(parent_uuid=parent_uuid,
                       tags=[_smart_tag_binding(tag) for tag in tags], match=match,
                       smart_profile=(SMART_NORMALIZED_CREATE_PROFILE if operation=='create_smart_normalized'
                           else SMART_APPEND_CREATE_PROFILE if operation=='create_smart_append' else SMART_CREATE_PROFILE),
                       **_smart_clock_bundle(catalog, parent,append=operation=='create_smart_append',
                                             normalized=operation=='create_smart_normalized'))
    elif operation == 'delete_smart':
        if tags or match != 'any':
            raise WriteProofError('Smart deletion does not admit condition edits')
        if target is None or target.get('Attribute') != 4:
            raise WriteProofError('Smart deletion requires an existing intelligent playlist')
        parent = target['ParentID']
        parents = [node for node in nodes if node['ID'] == parent]
        if parent != 'root' and len(parents) != 1:
            raise WriteProofError('Smart-playlist parent is missing or ambiguous')
        request.update(parent=parent, parent_uuid='' if parent == 'root' else parents[0]['UUID'],
                       smart_rule=target['SmartList'], smart_delete_profile=(SMART_LOCAL_DELETE_PROFILE
                           if target.get('rb_local_synced') == 1 else SMART_DELETE_PROFILE),
                       **_smart_delete_clock_bundle(catalog, identity))
    elif tags or match != 'any':
        raise ValueError('My Tag conditions belong only to new smart playlists')
    validate_request(request)
    return request



def validate_request(request):
    if isinstance(request,dict) and request.get('operation') == 'delete_tree':
        fields={'operation_id','operation','revision','playlist_id','playlist_uuid','descendants','requests'}
        if (set(request) != fields or not isinstance(request['requests'],list)
                or not 1 <= len(request['requests']) <= MAX_TREE_DELETE_COMMANDS
                or not isinstance(request['descendants'],list)
                or len(request['descendants']) != len(request['requests'])):
            raise WriteProofError('Invalid captured playlist tree deletion')
        for field, length in (('operation_id',32),('revision',64)):
            value=request[field]
            if type(value) is not str or len(value)!=length or any(char not in '0123456789abcdef' for char in value):
                raise WriteProofError('Invalid tree-deletion operation identity')
        ids=set();operations={request['operation_id']};positions={}
        for position,(binding,child) in enumerate(zip(request['descendants'],request['requests'],strict=True)):
            if (not isinstance(binding,dict) or set(binding)!={'id','uuid','parent','attribute'}
                    or type(binding['attribute']) is not int or binding['attribute'] not in (0,1,4)
                    or not isinstance(child,dict)
                    or child.get('operation') != ('delete_smart' if binding['attribute']==4 else 'delete')):
                raise WriteProofError('Tree deletion exceeds its selected leaf operations')
            validate_request(child)
            if (child['revision'] != request['revision'] or child['playlist_id'] != binding['id']
                    or child['playlist_uuid'] != binding['uuid'] or child['parent'] != binding['parent'] or binding['id'] in ids
                    or child['operation_id'] in operations):
                raise WriteProofError('Tree deletion repeats or replaces a captured native identity')
            parent=binding['parent']
            if (type(parent) is not str or parent!='root' and
                    (not parent.isascii() or not parent.isdecimal() or not 0<int(parent)<2**32 or str(int(parent))!=parent)):
                raise WriteProofError('Tree deletion has an invalid parent identity')
            ids.add(binding['id']);operations.add(child['operation_id']);positions[binding['id']]=position
        root=request['descendants'][-1]
        if (root['id'] != request['playlist_id'] or root['uuid'] != request['playlist_uuid']
                or root['attribute'] != 1 or root['parent'] in ids):
            raise WriteProofError('Tree deletion does not end at its exact selected root folder')
        by_id={binding['id']:binding for binding in request['descendants']}
        for binding in request['descendants'][:-1]:
            parent=binding['parent']
            if parent not in ids or by_id[parent]['attribute'] != 1 or positions[parent] <= positions[binding['id']]:
                raise WriteProofError('Tree deletion must remove selected descendants before their parents')
        return
    if isinstance(request,dict) and request.get('operation')=='batch':
        fields = {'operation_id','operation','revision','requests'}
        if 'plan_encoding' in request:
            fields.add('plan_encoding')
        if (set(request)!=fields
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
        if 'plan_encoding' in request:
            membership_replay.validate(request)
        return
    fields={'operation_id','operation','playlist_id','playlist_uuid','revision','name','parent','tracks',
            'timestamp','survivor_timestamp','xml_timestamp','folder','allow_duplicate'}
    if isinstance(request,dict) and request.get('operation') in CREATE_ORDINARY_OPERATIONS and 'parent_uuid' in request:
        fields|={'parent_uuid'}
    if isinstance(request,dict) and request.get('operation') in ('reorder','reorder_smart'):fields|={'anchor_id','anchor_uuid','after'}
    if isinstance(request,dict) and request.get('operation') in ('rename_smart','reorder_smart'):
        fields|={'smart_edit_profile','smart_attribute','smart_rule'}
    if isinstance(request,dict) and request.get('operation')=='reorder_smart':fields|={'smart_parent_xml_timestamp','smart_order_clocks'}
    if isinstance(request,dict) and request.get('operation') in ('create_normalized','create_folder_normalized'):
        fields|={'normalized_create_profile','normalized_create_clocks','parent_uuid'}
    if isinstance(request,dict) and request.get('operation')=='move':
        fields|={'parent_uuid','source_parent','source_parent_uuid','node_attribute','smart_rule','node_move_profile',
                 'node_move_clocks','move_descendants','anchor_id','anchor_uuid','anchor_attribute','after'}
    if isinstance(request,dict) and request.get('operation')=='move_smart':
        fields|={'parent_uuid','source_parent','source_parent_uuid','smart_rule','smart_move_profile','smart_move_clocks'}
    if isinstance(request,dict) and request.get('operation') in CREATE_SMART_OPERATIONS:
        fields|={'parent_uuid','tags','match','smart_profile','smart_clocks'}
    if isinstance(request,dict) and request.get('operation')=='delete_smart':
        fields|={'parent_uuid','smart_rule','smart_delete_profile','smart_delete_clocks'}
    if not isinstance(request,dict) or set(request)!=fields:
        raise WriteProofError('Unknown native playlist intent format')
    for key,length in (('operation_id',32),('revision',64)):
        value=request[key]
        if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
            raise WriteProofError('Invalid native playlist operation identity')
    for key in (('playlist_id','parent','anchor_id') if request['operation'] in ('reorder','reorder_smart') else ('playlist_id','parent')):
        value=request[key]
        if key=='parent' and value=='root':continue
        if (type(value) is not str or not value.isascii() or not value.isdecimal()
                or not 0<int(value)<2**32 or str(int(value))!=value):
            raise WriteProofError('Invalid native playlist target identity')
    try:
        for key in (('playlist_uuid','anchor_uuid') if request['operation'] in ('reorder','reorder_smart') else ('playlist_uuid',)):
            if not _canonical_uuid(request[key]):raise ValueError('UUID')
    except (ValueError,TypeError,AttributeError) as exc:
        raise WriteProofError('Invalid native playlist UUID') from exc
    if (request['operation'] not in ('create','create_normalized','create_folder_normalized','rename','delete','append','remove','reorder','create_smart','create_smart_append','create_smart_normalized','delete_smart','rename_smart','reorder_smart','move_smart','move')
            or type(request['folder']) is not bool or type(request['allow_duplicate']) is not bool
            or type(request['xml_timestamp']) is not int or not 0<request['xml_timestamp']<2**63
            or type(request['name']) is not str or '\0' in request['name']
            or not isinstance(request['tracks'],list) or len(request['tracks'])>16384):
        raise WriteProofError('Invalid native playlist operation')
    if (bool(request['tracks'])!=(request['operation'] in ('append','remove'))
            or request['operation'] in (*CREATE_ORDINARY_OPERATIONS,'rename',*CREATE_SMART_OPERATIONS,'rename_smart') and not request['name'].strip()):
        raise WriteProofError('Native playlist operation has an invalid selection')
    if request['operation'] in ('reorder','reorder_smart') and (type(request['after']) is not bool or request['anchor_id']==request['playlist_id']):
        raise WriteProofError('Invalid native reorder anchor')
    if request['operation'] in ('rename_smart','reorder_smart'):
        expected=SMART_RENAME_PROFILE if request['operation']=='rename_smart' else SMART_REORDER_PROFILE
        attribute=request['smart_attribute'];rule=request['smart_rule']
        if (request['smart_edit_profile']!=expected or request['folder'] or request['allow_duplicate']
                or type(attribute) is not int or attribute not in (0,1,4)
                or request['operation']=='rename_smart' and attribute!=4
                or attribute==4 and (type(rule) is not str or not rule or len(rule)>1024*1024 or '\0' in rule)
                or attribute!=4 and rule not in ('',None)):
            raise WriteProofError('Invalid intelligent playlist metadata profile or frozen rule')
    if request['operation']=='reorder_smart':
        parent_clock=request['smart_parent_xml_timestamp']
        if (type(parent_clock) is not int or
                (parent_clock!=0 if request['parent']=='root' else not request['xml_timestamp']<=parent_clock<2**63)):
            raise WriteProofError('Invalid intelligent playlist parent XML clock')
        mapping=request['smart_order_clocks']
        if not isinstance(mapping,dict) or len(mapping)>4096:
            raise WriteProofError('Intelligent ordering requires exact displaced-sibling clocks')
        start=_smart_sql_clock(request['timestamp']);end=_smart_sql_clock(request['survivor_timestamp'])
        for identity,clock in mapping.items():
            if (type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                    or not 0<int(identity)<2**32 or str(int(identity))!=identity
                    or not start<=_smart_sql_clock(clock)<=end):
                raise WriteProofError('Invalid intelligent playlist sibling clock')
    if request['operation'] in CREATE_SMART_OPERATIONS:
        if (request['folder'] or request['allow_duplicate'] or request['match'] != 'any'
                or request['smart_profile'] != (SMART_NORMALIZED_CREATE_PROFILE if request['operation']=='create_smart_normalized'
                    else SMART_APPEND_CREATE_PROFILE if request['operation']=='create_smart_append' else SMART_CREATE_PROFILE)):
            raise WriteProofError('Smart creation admits only new positive My Tag ANY/OR playlists')
        _validate_smart_tags(request['tags'])
        _smart_parent_uuid(request['parent'], request['parent_uuid'])
    if request['operation'] in CREATE_ORDINARY_OPERATIONS and 'parent_uuid' in request:
        _smart_parent_uuid(request['parent'],request['parent_uuid'])
    if request['operation'] == 'delete_smart':
        if (request['folder'] or request['allow_duplicate']
                or request['smart_delete_profile'] not in (SMART_DELETE_PROFILE, SMART_LOCAL_DELETE_PROFILE)
                or request['smart_rule'] is not None and
                (type(request['smart_rule']) is not str or len(request['smart_rule']) > 1024*1024
                 or '\0' in request['smart_rule'])):
            raise WriteProofError('Invalid or unreferenced smart-playlist deletion intent')
        _smart_parent_uuid(request['parent'], request['parent_uuid'])
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
    if request['operation'] in ('create_normalized','create_folder_normalized'):_validate_normalized_create(request)
    if request['operation']=='move_smart':_validate_smart_move(request)
    if request['operation']=='move':_validate_node_move(request)
    if request['operation'] in CREATE_SMART_OPERATIONS:
        _validate_smart_clocks(request)
    if request['operation'] == 'delete_smart':
        mapping = request['smart_delete_clocks']
        if not isinstance(mapping, dict) or len(mapping) > 4096:
            raise WriteProofError('Smart deletion requires captured following-sibling clocks')
        start = _smart_sql_clock(request['timestamp']); end = _smart_sql_clock(request['survivor_timestamp'])
        for identity, clock in mapping.items():
            if (type(identity) is not str or not identity.isascii() or not identity.isdecimal()
                    or not 0 < int(identity) < 2**32 or str(int(identity)) != identity
                    or not start <= _smart_sql_clock(clock) <= end):
                raise WriteProofError('Invalid smart deletion sibling clock')
    occurrences = set()
    for track in request['tracks']:
        base={'track_id','uuid','id','membership_uuid'}
        pinned=base|{'membership_id','source_membership_uuid'}
        if (not isinstance(track,dict) or set(track) not in
                ((base,pinned) if request['operation']=='remove' else (base,))
                or any(type(value) is not str or not value for value in track.values())):
            raise WriteProofError('Invalid native playlist track selection')
        if 'membership_id' in track:
            if (len(track['membership_id']) > 4096 or '\0' in track['membership_id']
                    or track['membership_id'] in occurrences):
                raise WriteProofError('Repeated or invalid native playlist occurrence')
            occurrences.add(track['membership_id'])
        try:
            names=('id','membership_uuid','source_membership_uuid') if 'membership_id' in track else ('id','membership_uuid')
            if any(not _canonical_uuid(track[key]) for key in names):raise ValueError('UUID')
        except (ValueError,TypeError,AttributeError) as exc:
            raise WriteProofError('Invalid native membership UUID') from exc
