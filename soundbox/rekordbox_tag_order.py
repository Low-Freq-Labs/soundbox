"""Separately admitted native My Tag order; no definition or assignment edits.

Saved native references prove adjacent swaps of historical synced and local
leaves in one category. Larger gestures compose that bounded primitive, rather
than inventing a native bulk-reorder protocol. Categories and history stay exact.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from . import rekordbox_tag_definitions as definitions
from .library import LibraryTag
from .rekordbox_cue_store import CueWriteAuthority
from .rekordbox_tags import NATIVE_VERSION
from .rekordbox_write import ConflictError, RecoveryRequired, WriteProofError, _json

AUTHORITY_FORMAT='soundbox-tag-order-authority-v1'
FORMAT='soundbox-native-tag-order-v1'
MAX_TAGS=256
MAX_SWAPS=256
_FIELDS=frozenset(('action','operation_id','library_id','category_id','category_uuid',
                  'tag_id','tag_uuid','name','before','order','clocks'))


@dataclass(frozen=True)
class OrderProtocol:
    profile:str
    primitive:str='adjacent-synced-leaf-swap'
    increment:int=2
    null_usn_rows:bool=False


HISTORICAL_ORDER_PROFILE=OrderProtocol('rekordbox-6.8.7.0303-historical-leaf-adjacent-order-v1')
# The separate Favorites/Zyko up-and-restore reference supplies local0 and
# synced1 NULL-remote-clock leaves; all flags are retained, never normalized.
NATIVE_ORDER_PROFILE=OrderProtocol('rekordbox-6.8.7.0303-local-historical-leaf-adjacent-order-v2',
                                  primitive='adjacent-leaf-swap',null_usn_rows=True)
VERIFIED_PROTOCOLS={profile.profile:profile for profile in (HISTORICAL_ORDER_PROFILE,NATIVE_ORDER_PROFILE)}


@dataclass(frozen=True)
class TagOrderAuthority:
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
                          'tag-order-authority',self.application_version)
        if (type(self.accepted) is not bool or self.format!=AUTHORITY_FORMAT
                or type(self.protocol_profile) is not str
                or not isinstance(self.pilot_operation_ids,tuple)
                or any(not definitions._operation_id(value) for value in self.pilot_operation_ids)):
            raise WriteProofError('Invalid native tag-order authority')


def load_authority(path:Path,source:Path,library_id:str):
    try:
        data=definitions._read_json(path,64*1024)
        if data.get('format')!=AUTHORITY_FORMAT:return None
        data['pilot_operation_ids']=tuple(data.get('pilot_operation_ids',()))
        authority=TagOrderAuthority(**data)
        if (authority.database!=str(source) or authority.library_id!=library_id
                or not authority.accepted or authority.protocol_profile not in VERIFIED_PROTOCOLS):return None
        return authority
    except (OSError,ValueError,TypeError,KeyError,AttributeError,WriteProofError):return None


def _swaps(before,desired):
    current=list(before);result=[]
    for index,identity in enumerate(desired):
        position=current.index(identity)
        while position>index:
            left,right=current[position-1:position+1]
            result.append((left,right));current[position-1:position+1]=[right,left];position-=1
            if len(result)>MAX_SWAPS:raise WriteProofError('This native tag-order gesture exceeds its bounded swap limit')
    return result


def _clock(value):
    if not isinstance(value,str):return False
    try:
        parsed=datetime.strptime(value,'%Y-%m-%d %H:%M:%S.%f +00:00')
        return parsed.year>=1970 and parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'==value
    except ValueError:return False


def validate_request(request):
    if not isinstance(request,dict) or set(request)!=_FIELDS:raise WriteProofError('Invalid captured native tag-order request')
    before,order,clocks=request['before'],request['order'],request['clocks']
    if (request['action']!='reorder' or not definitions._operation_id(request['operation_id'])
            or any(not definitions._text(request[key],256) for key in
                   ('library_id','category_id','category_uuid','tag_id','tag_uuid','name'))
            or not isinstance(before,list) or not 2<=len(before)<=MAX_TAGS
            or not isinstance(order,list) or len(order)!=len(before)):
        raise WriteProofError('Invalid native tag-order identities')
    seen=set();uuids=set()
    for row in before:
        if (not isinstance(row,dict) or set(row)!={'id','uuid','name'}
                or any(not definitions._text(row[key],256) for key in row)
                or row['id'] in seen or row['uuid'] in uuids):raise WriteProofError('Ambiguous native tag-order bindings')
        seen.add(row['id']);uuids.add(row['uuid'])
    if (any(type(identity) is not str for identity in order) or len(set(order))!=len(order)
            or set(order)!=seen or order==[row['id'] for row in before]):
        raise WriteProofError('Native tag order requires one exact changed permutation')
    target=next((row for row in before if row['id']==request['tag_id']),None)
    if not target or (target['uuid'],target['name'])!=(request['tag_uuid'],request['name']):
        raise WriteProofError('Native tag-order target was replaced')
    swaps=_swaps([row['id'] for row in before],order)
    if (not isinstance(clocks,list) or len(clocks)!=len(swaps)
            or any(not isinstance(pair,list) or len(pair)!=2 or not all(_clock(clock) for clock in pair) for pair in clocks)):
        raise WriteProofError('Native tag-order row clocks were not captured exactly')
    return request


def request_for(library_id,tags,desired_ids,*,step_clocks=None):
    """Capture one category's exact active order from a native Library snapshot."""
    tags=list(tags);desired=list(desired_ids);by_id={tag.tag_id:tag for tag in tags}
    if (len(by_id)!=len(tags) or len(desired)!=len(tags) or set(desired)!=set(by_id)
            or len(set(desired))!=len(desired)):raise WriteProofError('The tag catalog changed; capture its order again')
    groups:dict[tuple[str,str],list[LibraryTag]]={}
    for tag in tags:groups.setdefault((tag.category_id,tag.category_uuid),[]).append(tag)
    changed=[]
    if [by_id[key].category_id for key in desired]!=[tag.category_id for tag in tags]:
        raise WriteProofError('Native tag reordering cannot move tags between categories')
    for context,group in groups.items():
        ids=[tag.tag_id for tag in group];ordered=[key for key in desired if key in set(ids)]
        if ordered!=ids:changed.append((context,group,ordered))
    if len(changed)!=1:raise WriteProofError('Native tag reordering requires one changed category')
    (category_id,category_uuid),group,order=changed[0]
    before=[dict(id=tag.tag_id,uuid=tag.source_uuid,name=tag.name) for tag in group]
    swaps=_swaps([row['id'] for row in before],order)
    target=by_id[swaps[0][1]]
    if step_clocks is None:
        step_clocks=[[datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
                      for _ in range(2)] for _ in swaps]
    request=dict(action='reorder',operation_id=uuid4().hex,library_id=library_id,
        category_id=category_id,category_uuid=category_uuid,tag_id=target.tag_id,
        tag_uuid=target.source_uuid,name=target.name,before=before,order=order,clocks=deepcopy(step_clocks))
    return validate_request(request)


def plan_order(request,category,siblings,counter,protocol):
    validate_request(request)
    if protocol not in VERIFIED_PROTOCOLS.values():raise WriteProofError('An exact native tag-order profile is required')
    if (not isinstance(counter,dict) or counter.get('registry_id')!='localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0<=counter['int_1']<2**63-2*MAX_SWAPS
            or not isinstance(category,dict) or set(category)!=definitions._COLUMNS
            or (category['ID'],category['UUID'])!=(request['category_id'],request['category_uuid'])
            or not definitions._historical_category_profile(category,counter)
            or category['ParentID']!='root' or not isinstance(siblings,list)
            or len(siblings)>definitions._MAX_SIBLINGS):
        raise ConflictError('The native tag-order category/counter changed')
    active=[]
    for row in siblings:
        if (not isinstance(row,dict) or set(row)!=definitions._COLUMNS
                or row['ParentID']!=category['ID'] or row['Attribute']!=0
                or type(row['rb_local_deleted']) is not int or row['rb_local_deleted'] not in (0,1)):
            raise WriteProofError('Unverified native tag-order inventory')
        if not row['rb_local_deleted']:active.append(row)
    if any(type(row['Seq']) is not int for row in active):raise WriteProofError('Unverified native tag positions')
    active.sort(key=lambda row:row['Seq'])
    if ([row['Seq'] for row in active]!=list(range(1,len(active)+1))
            or [dict(id=row['ID'],uuid=row['UUID'],name=row['Name']) for row in active]!=request['before']):
        raise ConflictError('The native tag order or exact leaf identities changed')
    rows={row['ID']:deepcopy(row) for row in active};changes=[];number=counter['int_1']
    for (left,right),clocks in zip(_swaps([row['ID'] for row in active],request['order']),request['clocks'],strict=True):
        # The independent profiles admit only their observed row envelopes.
        for identity in (right,left):
            row=rows[identity]
            historical=(type(row.get('rb_data_status')) is int and row['rb_data_status']==257
                        and type(row.get('rb_local_synced')) is int and row['rb_local_synced']==1
                        and type(row.get('usn')) is int and 0<=row['usn']<2**63)
            local=(protocol.null_usn_rows and type(row.get('rb_data_status')) is int and row['rb_data_status']==0
                   and type(row.get('rb_local_synced')) is int and row['rb_local_synced'] in (0,1) and row.get('usn') is None)
            if (not (historical or local) or any(type(row.get(key)) is not int or row[key]!=0
                                               for key in ('rb_local_data_status','rb_local_deleted'))
                    or type(row.get('rb_local_usn')) is not int or not 0<=row['rb_local_usn']<=number):
                raise WriteProofError('This tag synchronization state needs a separate native order reference')
            try:
                if str(UUID(row['UUID']))!=row['UUID']:raise ValueError('UUID')
            except (ValueError,TypeError,AttributeError) as exc:raise WriteProofError('Unverified native tag UUID') from exc
        positions={right:rows[left]['Seq'],left:rows[right]['Seq']}
        for identity,clock in zip((right,left),clocks,strict=True):
            old=rows[identity];number+=1
            after={**old,'Seq':positions[identity],'updated_at':clock,'rb_local_usn':number}
            changes.append(dict(table='djmdMyTag',key=identity,before=deepcopy(old),after=after));rows[identity]=after
    changes.append(dict(table='agentRegistry',key='localUpdateCount',before=deepcopy(counter),after={**counter,'int_1':number}))
    return changes


class TagOrderStore(definitions.TagDefinitionStore):
    authority_type=TagOrderAuthority
    record_format=FORMAT
    receipt_directory='tag-order-receipts'

    def _protocol(self):
        protocol=VERIFIED_PROTOCOLS.get(self.authority.protocol_profile)
        if self.authority.accepted is not True or protocol is None:
            raise WriteProofError('Native tag reordering needs separate exact-library acceptance')
        return protocol

    @staticmethod
    def _validate_request(request):return validate_request(request)

    @staticmethod
    def _plan(request,category,siblings,counter,protocol,dependencies=None):
        if dependencies is not None:raise WriteProofError('Tag order cannot admit other native domains')
        return plan_order(request,category,siblings,counter,protocol)

    @staticmethod
    def _after_siblings(siblings,changes):
        rows={row['ID']:row for row in siblings}
        for change in changes[:-1]:rows[change['key']]=change['after']
        return [rows[key] for key in sorted(rows)]

    @staticmethod
    def _identity_available(conn,request):return None

    def _replay(self,conn,request):
        path=self._receipt_path(request['operation_id'])
        if not path.exists():return None
        receipt=self._read_record(path,pending=False)
        if receipt.get('outcome')!='committed' or receipt['request']!=request:
            raise RecoveryRequired('The native tag-order receipt differs from this intention')
        category,siblings,counter=self._snapshot(conn,request)
        expected=self._after_siblings(receipt['siblings'],receipt['changes'])
        if (_json(category)!=_json(receipt['category']) or _json(sorted(siblings,key=lambda row:row['ID']))!=_json(expected)
                or type(counter.get('int_1')) is not int or counter['int_1']<receipt['changes'][-1]['after']['int_1']):
            raise RecoveryRequired('The saved native tag order changed; preserve its receipt')
        self.lease._guard();return deepcopy(next(row for row in expected if row['ID']==request['tag_id']))

    @staticmethod
    def _authorizer(action,table,column,database,trigger):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
            if trigger or database!='main' or action!=sqlite3.SQLITE_UPDATE:return sqlite3.SQLITE_DENY
            if table=='djmdMyTag' and column in ('Seq','updated_at','rb_local_usn'):return sqlite3.SQLITE_OK
            if table=='agentRegistry' and column=='int_1':return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_TRANSACTION,
                      sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_SAVEPOINT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _apply_changes(conn,request,changes):
        for change in changes[:-1]:
            row=change['after']
            conn.execute('UPDATE djmdMyTag SET Seq=?,updated_at=?,rb_local_usn=? WHERE ID=?',
                         (row['Seq'],row['updated_at'],row['rb_local_usn'],row['ID']))
        conn.execute('UPDATE agentRegistry SET int_1=? WHERE registry_id=?',
                     (changes[-1]['after']['int_1'],'localUpdateCount'))
        return next(change['after'] for change in reversed(changes[:-1]) if change['key']==request['tag_id'])
