"""Independently admitted existing My Tag labels; assignments/rules stay exact.

Native rename/restore references prove the three supported active leaf shapes.
Creation, deletion and order acceptance never enable this separate authority.
Registering a measured protocol does not provision exact-library acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from . import rekordbox_tag_definitions as definitions
from .rekordbox_cue_store import CueWriteAuthority
from .rekordbox_tags import NATIVE_VERSION
from .rekordbox_write import ConflictError, RecoveryRequired, WriteProofError

AUTHORITY_FILE='rekordbox-tag-definition-rename-authority.json'
AUTHORITY_FORMAT='soundbox-tag-definition-rename-authority-v1'
FORMAT='soundbox-native-tag-definition-rename-v1'
_FIELDS=frozenset(('action','operation_id','library_id','category_id','category_uuid',
                  'tag_id','tag_uuid','name','new_name','timestamp'))


@dataclass(frozen=True)
class RenameProtocol:
    profile:str
    increment:int=1
    historical_rows:bool=True
    local_synced_rows:bool=True
    local_unsynced_rows:bool=True

    def __post_init__(self):
        if (not definitions._text(self.profile,128) or type(self.increment) is not int or self.increment!=1
                or any(type(getattr(self,key)) is not bool for key in
                       ('historical_rows','local_synced_rows','local_unsynced_rows'))):
            raise WriteProofError('Invalid proposed tag-rename protocol')


PROVISIONAL_PROTOCOL=RenameProtocol('unaccepted-active-leaf-rename-proposal-v1')
# Six independent native rename/restore windows prove local0/synced0,
# local0/synced1 and historical257/synced1 leaves. Only Name, updated_at and
# local USN change; the complete counter advances1 and every other row/XML is
# exact. Encrypted TEMP compilation matches all six complete source states.
NATIVE_RENAME_PROFILE=RenameProtocol('rekordbox-6.8.7.0303-active-leaf-rename-v1')
VERIFIED_PROTOCOLS:dict[str,RenameProtocol]={NATIVE_RENAME_PROFILE.profile:NATIVE_RENAME_PROFILE}


@dataclass(frozen=True)
class TagRenameAuthority:
    database:str
    library_id:str
    schema_sha256:str
    accepted:bool=False
    application_version:str=NATIVE_VERSION
    protocol_profile:str=''
    pilot_operation_ids:tuple[str,...]=()
    format:str=AUTHORITY_FORMAT

    def __post_init__(self):
        CueWriteAuthority(self.database,self.library_id,self.schema_sha256,
                          'tag-definition-rename-authority',self.application_version)
        if (type(self.accepted) is not bool or self.format!=AUTHORITY_FORMAT
                or type(self.protocol_profile) is not str or len(self.protocol_profile)>128
                or '\0' in self.protocol_profile or not isinstance(self.pilot_operation_ids,tuple)
                or any(not definitions._operation_id(value) for value in self.pilot_operation_ids)):
            raise WriteProofError('Invalid native tag-rename authority')


def load_authority(path:Path,source:Path,library_id:str):
    try:
        data=definitions._read_json(path,64*1024)
        if data.get('format')!=AUTHORITY_FORMAT:return None
        data['pilot_operation_ids']=tuple(data.get('pilot_operation_ids',()))
        authority=TagRenameAuthority(**data)
        if (authority.database!=str(source) or authority.library_id!=library_id
                or not authority.accepted or authority.protocol_profile not in VERIFIED_PROTOCOLS):return None
        return authority
    except (OSError,ValueError,TypeError,KeyError,AttributeError,WriteProofError):return None


def _clock(value):
    try:
        if type(value) is not str:return False
        parsed=datetime.strptime(value,'%Y-%m-%d %H:%M:%S.%f +00:00')
        return (parsed.year>=1970
                and parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'==value)
    except ValueError:return False


def validate_request(request):
    if not isinstance(request,dict) or set(request)!=_FIELDS:
        raise WriteProofError('Invalid captured native tag-rename request')
    try:
        identity=request['tag_id']
        bound=(type(identity) is str and len(identity)<=10 and identity.isascii() and identity.isdecimal()
               and 0<int(identity)<2**32 and str(int(identity))==identity
               and str(UUID(request['tag_uuid']))==request['tag_uuid'])
    except (ValueError,TypeError,AttributeError):bound=False
    if (request['action']!='rename' or not definitions._operation_id(request['operation_id']) or not bound
            or any(not definitions._text(request[key],256) for key in ('library_id','category_id','category_uuid'))
            or not definitions._text(request['name']) or not definitions._text(request['new_name'])
            or request['name']==request['new_name'] or not _clock(request['timestamp'])):
        raise WriteProofError('The captured tag label, identity or rename clock is invalid')
    return request


def request_for(library_id,category_id,category_uuid,tag_id,tag_uuid,name,new_name,*,now=None):
    clock=now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock,datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise WriteProofError('An explicit timezone-aware tag-rename clock is required')
    return validate_request(dict(action='rename',operation_id=uuid4().hex,library_id=library_id,
        category_id=category_id,category_uuid=category_uuid,tag_id=tag_id,tag_uuid=tag_uuid,name=name,new_name=new_name,
        timestamp=clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'))


def _target_profile(row,counter,protocol):
    historical=(protocol.historical_rows and type(row['rb_data_status']) is int and row['rb_data_status']==257
        and type(row['rb_local_synced']) is int and row['rb_local_synced']==1
        and type(row['usn']) is int and 0<=row['usn']<2**63)
    local=(type(row['rb_data_status']) is int and row['rb_data_status']==0 and row['usn'] is None
        and type(row['rb_local_synced']) is int and row['rb_local_synced'] in (0,1)
        and (protocol.local_synced_rows if row['rb_local_synced'] else protocol.local_unsynced_rows))
    if (not (historical or local) or any(type(row[key]) is not int or row[key]!=0
                                       for key in ('rb_local_data_status','rb_local_deleted'))
            or type(row['rb_local_usn']) is not int or not 0<=row['rb_local_usn']<=counter['int_1']):
        raise WriteProofError('This tag synchronization state needs an independent native rename reference')


def plan_rename(request,category,siblings,counter,protocol:RenameProtocol):
    """Copy-only proposal; neither a proposed protocol nor a plan grants writes."""
    validate_request(request)
    if not isinstance(protocol,RenameProtocol):raise WriteProofError('An explicit proposed rename protocol is required')
    if (not isinstance(counter,dict) or counter.get('registry_id')!='localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0<=counter['int_1']<2**63-protocol.increment
            or not isinstance(category,dict) or set(category)!=definitions._COLUMNS
            or (category['ID'],category['UUID'])!=(request['category_id'],request['category_uuid'])
            or not definitions._historical_category_profile(category,counter)
            or category['ParentID']!='root' or not isinstance(siblings,list)
            or len(siblings)>definitions._MAX_SIBLINGS):
        raise ConflictError('The selected native tag category or counter changed')
    if any(not isinstance(row,dict) or set(row)!=definitions._COLUMNS or row['ParentID']!=category['ID']
           or type(row['Attribute']) is not int or row['Attribute']!=0
           or type(row['rb_local_deleted']) is not int or row['rb_local_deleted'] not in (0,1)
           or not row['rb_local_deleted'] and not definitions._text(row['Name']) for row in siblings):
        raise WriteProofError('Unverified native tag category inventory')
    targets=[row for row in siblings if row['ID']==request['tag_id'] or row['UUID']==request['tag_uuid']]
    if (len(targets)!=1 or (targets[0]['ID'],targets[0]['UUID'],targets[0]['Name'])!=
            (request['tag_id'],request['tag_uuid'],request['name']) or targets[0]['rb_local_deleted']!=0):
        raise ConflictError('The selected tag label or native identity changed')
    target=targets[0];_target_profile(target,counter,protocol)
    if any(row['ID']!=target['ID'] and row['rb_local_deleted']==0
           and row['Name'].casefold()==request['new_name'].casefold() for row in siblings):
        raise ConflictError('An active tag with this name already exists in the selected category')
    number=counter['int_1']+protocol.increment
    return [dict(table='djmdMyTag',key=target['ID'],before=deepcopy(target),
                 after={**target,'Name':request['new_name'],'updated_at':request['timestamp'],'rb_local_usn':number}),
            dict(table='agentRegistry',key='localUpdateCount',before=deepcopy(counter),after={**counter,'int_1':number})]


class TagRenameStore(definitions.TagDefinitionStore):
    authority_type=TagRenameAuthority
    record_format=FORMAT
    receipt_directory='tag-rename-receipts'

    def _protocol(self):
        protocol=VERIFIED_PROTOCOLS.get(self.authority.protocol_profile)
        if self.authority.accepted is not True or protocol is None:
            raise WriteProofError('Native tag renaming requires independent protocol and exact-library acceptance')
        return protocol

    @staticmethod
    def _validate_request(request):return validate_request(request)

    @staticmethod
    def _plan(request,category,siblings,counter,protocol,dependencies=None):
        if dependencies is not None:raise WriteProofError('Tag renaming cannot admit other source edits')
        return plan_rename(request,category,siblings,counter,protocol)

    @staticmethod
    def _after_siblings(siblings,changes):
        return [changes[0]['after'] if row['ID']==changes[0]['key'] else row for row in siblings]

    @staticmethod
    def _identity_available(conn,request):return None

    def _snapshot(self,conn,request):
        captured=super()._snapshot(conn,request)
        rows=conn.execute('SELECT ID,UUID,ParentID FROM djmdMyTag WHERE ID=? OR UUID=? LIMIT 2',
                          (request['tag_id'],request['tag_uuid'])).fetchall()
        if len(rows)!=1 or rows[0]!=(request['tag_id'],request['tag_uuid'],request['category_id']):
            raise ConflictError('The native tag rename identity is ambiguous or changed')
        return captured

    def _replay(self,conn,request):
        path=self._receipt_path(request['operation_id'])
        if not path.exists():return None
        receipt=self._read_record(path,pending=False)
        if receipt.get('outcome')!='committed' or receipt['request']!=request:
            raise RecoveryRequired('The saved native tag rename belongs to another intention')
        category=self._row(conn,'djmdMyTag',request['category_id'])
        target=self._row(conn,'djmdMyTag',request['tag_id'])
        counter=self._row(conn,'agentRegistry','localUpdateCount')
        if (target!=receipt['changes'][0]['after'] or not category or not counter
                or (category['ID'],category['UUID'])!=(request['category_id'],request['category_uuid'])
                or category['ParentID']!='root' or not definitions._historical_category_profile(category,counter)
                or type(counter.get('int_1')) is not int or counter['int_1']<receipt['changes'][-1]['after']['int_1']):
            raise RecoveryRequired('The previously renamed native tag changed; preserve its receipt')
        self.lease._guard();return deepcopy(target)

    @staticmethod
    def _authorizer(action,table,column,database,trigger):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
            if trigger or database!='main' or action!=sqlite3.SQLITE_UPDATE:return sqlite3.SQLITE_DENY
            if table=='djmdMyTag' and column in ('Name','updated_at','rb_local_usn'):return sqlite3.SQLITE_OK
            if table=='agentRegistry' and column=='int_1':return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,
                      sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _apply_changes(conn,request,changes):
        target=changes[0]['after']
        conn.execute('UPDATE djmdMyTag SET Name=?,updated_at=?,rb_local_usn=? WHERE ID=?',
                     (target['Name'],target['updated_at'],target['rb_local_usn'],target['ID']))
        conn.execute('UPDATE agentRegistry SET int_1=? WHERE registry_id=?',
                     (changes[-1]['after']['int_1'],'localUpdateCount'))
        return target
