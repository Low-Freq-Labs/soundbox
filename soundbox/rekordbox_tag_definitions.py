"""Independently gated creation and deletion of native My Tag leaves.

Isolated native constructor/name-edit references define the composed profile.
Every source operation still requires this domain's separately accepted exact-
library authority. Copy-only planners/tests never provision that authority.
Deletion removes only measured local associations and preserves smart rules.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from uuid import UUID, uuid4
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from .rekordbox_cue_store import CueWriteAuthority, PilotCueStore
from .rekordbox_tags import NATIVE_VERSION, _file_sha, _installed_version, _schema
from .rekordbox_write import (
    ConflictError, RecoveryRequired, WriteProofError, _connect as _connect_copy, _digest, _identity,
    _inactive_journal_proof, _json, _quote, _regular, _state, assert_rekordbox_closed, encrypted_backup,
)
from .state_io import atomic_bytes, copy_state, sync_dir

if TYPE_CHECKING:
    from .rekordbox_tag_order import TagOrderAuthority
    from .rekordbox_tag_rename import TagRenameAuthority

FORMAT = 'soundbox-native-tag-definitions-v1'
AUTHORITY_FORMAT = 'soundbox-tag-definition-authority-v1'
DELETE_AUTHORITY_FORMAT = 'soundbox-tag-definition-delete-authority-v1'
DELETE_FORMAT = 'soundbox-native-tag-definition-delete-v1'
_MAX_RECORD_BYTES = 16 * 1024**2
_MAX_SIBLINGS = 16_384
_COLUMNS = frozenset(('ID', 'Seq', 'Name', 'Attribute', 'ParentID', 'UUID',
    'rb_data_status', 'rb_local_data_status', 'rb_local_deleted',
    'rb_local_synced', 'usn', 'rb_local_usn', 'created_at', 'updated_at'))
_LINK_COLUMNS = frozenset(('ID', 'MyTagID', 'ContentID', 'TrackNo', 'UUID',
    'rb_data_status', 'rb_local_data_status', 'rb_local_deleted', 'rb_local_synced',
    'usn', 'rb_local_usn', 'created_at', 'updated_at'))
_REQUEST_FIELDS = frozenset(('operation_id', 'library_id', 'category_id',
    'category_uuid', 'name', 'tag_id', 'tag_uuid', 'timestamp', 'final_timestamp'))
_DELETE_REQUEST_FIELDS = (_REQUEST_FIELDS - {'final_timestamp'}) | {'action'}
_DELETE_CLOCK_FIELDS = frozenset(('target', 'siblings', 'associations'))


def _text(value, limit=255):
    return (type(value) is str and 0 < len(value) <= limit
            and value == value.strip() and all(ord(c) >= 32 and ord(c) != 127 for c in value))


@dataclass(frozen=True)
class DefinitionProtocol:
    profile: str
    create_increment: int
    naming_increment: int = 0
    default_name: str = ''

    def __post_init__(self):
        if (type(self.profile) is not str or not self.profile
                or type(self.create_increment) is not int or not 1 <= self.create_increment <= 16
                or type(self.naming_increment) is not int or not 0 <= self.naming_increment <= 16
                or type(self.default_name) is not str
                or self.naming_increment and not _text(self.default_name)):
            raise WriteProofError('Invalid tag-definition protocol profile')

    def names_new_row(self, name):
        return bool(self.naming_increment and name != self.default_name)

    def increment_for(self, name):
        return self.create_increment + (self.naming_increment if self.names_new_row(name) else 0)


# This +2 is ONLY the observed aggregate creation/name-edit delta. It is not
# isolated create evidence and is deliberately absent from VERIFIED_PROTOCOLS.
PROVISIONAL_PROTOCOL = DefinitionProtocol('unisolated-create-and-name-reference-v1', 2)
# The isolated constructor adds1; naming that newly created row adds1 and
# preserves created_at. This profile grants no general definition rename API.
NATIVE_CREATE_PROFILE = DefinitionProtocol('native-leaf-create-and-name-v1', 1, 1, 'My Comment')
VERIFIED_PROTOCOLS: dict[str, DefinitionProtocol] = {NATIVE_CREATE_PROFILE.profile: NATIVE_CREATE_PROFILE}


@dataclass(frozen=True)
class TagDefinitionAuthority:
    database: str
    library_id: str
    schema_sha256: str
    accepted: bool = False
    application_version: str = NATIVE_VERSION
    protocol_profile: str = ''
    # Source pilots can bind one captured operation without opening future ones.
    pilot_operation_ids: tuple[str, ...] = ()
    format: str = AUTHORITY_FORMAT

    def __post_init__(self):
        CueWriteAuthority(self.database, self.library_id, self.schema_sha256,
                          'tag-definition-authority', self.application_version)
        if (type(self.accepted) is not bool or self.format != AUTHORITY_FORMAT
                or type(self.protocol_profile) is not str
                or not isinstance(self.pilot_operation_ids, tuple)
                or any(not _operation_id(value) for value in self.pilot_operation_ids)):
            raise WriteProofError('Invalid tag-definition authority')


@dataclass(frozen=True)
class TagDeletionAuthority:
    """Creation/assignment acceptance is deliberately not deletion acceptance."""
    database: str
    library_id: str
    schema_sha256: str
    accepted: bool = False
    application_version: str = NATIVE_VERSION
    protocol_profile: str = ''
    pilot_operation_ids: tuple[str, ...] = ()
    format: str = DELETE_AUTHORITY_FORMAT

    def __post_init__(self):
        CueWriteAuthority(self.database, self.library_id, self.schema_sha256,
                          'tag-definition-delete-authority', self.application_version)
        if (type(self.accepted) is not bool or self.format != DELETE_AUTHORITY_FORMAT
                or type(self.protocol_profile) is not str
                or not isinstance(self.pilot_operation_ids, tuple)
                or any(not _operation_id(value) for value in self.pilot_operation_ids)):
            raise WriteProofError('Invalid tag-definition deletion authority')


# Native deletion references and independent exact-library acceptance are both
# required. No constructor/name profile, old file or request enables this domain.
@dataclass(frozen=True)
class DeletionProtocol:
    profile: str
    shift_increment: int = 1
    deletion_increment: int = 1
    synced_history: bool = False

    def __post_init__(self):
        if (not _text(self.profile, 256) or type(self.shift_increment) is not int
                or self.shift_increment != 1 or type(self.deletion_increment) is not int
                or self.deletion_increment != 1 or type(self.synced_history) is not bool):
            raise WriteProofError('Invalid tag-definition deletion protocol')


NATIVE_DELETE_PROFILE = DeletionProtocol('native-local-leaf-delete-v1')
NATIVE_HISTORY_DELETE_PROFILE = DeletionProtocol('native-local-and-synced-leaf-delete-v2', synced_history=True)
VERIFIED_DELETE_PROTOCOLS: dict[str, DeletionProtocol] = {
    NATIVE_DELETE_PROFILE.profile: NATIVE_DELETE_PROFILE,
    NATIVE_HISTORY_DELETE_PROFILE.profile: NATIVE_HISTORY_DELETE_PROFILE,
}


def _operation_id(value):
    return (type(value) is str and len(value) == 32
            and all(c in '0123456789abcdef' for c in value))


def _read_json(path, limit):
    _regular(path)
    def epoch(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    before = epoch(path.stat())
    if before[2] > limit:
        raise WriteProofError('Tag-definition metadata exceeds its read bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        if epoch(os.fstat(source.fileno())) != before:
            raise WriteProofError('Tag-definition metadata changed before reading')
        raw = source.read(limit + 1)
        if len(raw) > limit or epoch(os.fstat(source.fileno())) != before or epoch(path.stat()) != before:
            raise WriteProofError('Tag-definition metadata changed while reading')
    return json.loads(raw)


def _historical_category_profile(category, counter):
    return (all(type(category.get(field)) is int and category[field] == expected for field, expected in
                (('Attribute', 1), ('rb_data_status', 256), ('rb_local_data_status', 0),
                 ('rb_local_deleted', 0), ('rb_local_synced', 1)))
            # Remote USN is an independent sequence retained verbatim on this
            # read-only parent; it is not bounded by the local writer clock.
            and type(category.get('usn')) is int and 0 <= category['usn'] < 2**63
            and type(category.get('rb_local_usn')) is int
            and 0 <= category['rb_local_usn'] <= counter['int_1'])


def validate_request(request):
    if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
        raise WriteProofError('Invalid captured tag-definition request')
    try:
        valid_uuid = str(UUID(request['tag_uuid'])) == request['tag_uuid'] and UUID(request['tag_uuid']).version == 4
        valid_id = (type(request['tag_id']) is str and request['tag_id'] == str(int(request['tag_id']))
                    and 0 < int(request['tag_id']) < 2**32)
        clocks = [datetime.strptime(request[key], '%Y-%m-%d %H:%M:%S.%f +00:00')
                  for key in ('timestamp', 'final_timestamp')]
        valid_time = (all(clock.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00' == request[key]
                          and clock.year >= 1970 for key, clock in zip(('timestamp', 'final_timestamp'), clocks))
                      and clocks[1] >= clocks[0])
    except (ValueError, TypeError, AttributeError, OverflowError, KeyError):
        valid_uuid = valid_id = valid_time = False
    if (not _operation_id(request['operation_id']) or not valid_uuid or not valid_id or not valid_time
            or any(not _text(request[key], 256) for key in ('library_id', 'category_id', 'category_uuid'))
            or not _text(request['name'])):
        raise WriteProofError('Tag-definition identity, name or captured clock is invalid')
    return request


def request_for(library_id, category_id, category_uuid, name, *, now=None, final_now=None):
    clock = now if now is not None else datetime.now(timezone.utc)
    final_clock = final_now if final_now is not None else clock
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
           for value in (clock, final_clock)):
        raise WriteProofError('An explicit timezone-aware tag-definition clock is required')
    request = dict(operation_id=uuid4().hex, library_id=library_id,
        category_id=category_id, category_uuid=category_uuid, name=name,
        tag_id=str(secrets.randbelow(2**32 - 1) + 1), tag_uuid=str(uuid4()),
        timestamp=clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00',
        final_timestamp=final_clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00')
    return validate_request(request)


def load_authority(path: Path, source: Path, library_id: str):
    try:
        data = _read_json(path, 64 * 1024)
        if data.get('format') != AUTHORITY_FORMAT:
            return None  # Never reinterpret an old assignment/other authority.
        data['pilot_operation_ids'] = tuple(data.get('pilot_operation_ids', ()))
        authority = TagDefinitionAuthority(**data)
        if (authority.database != str(source) or authority.library_id != library_id
                or not authority.accepted or authority.protocol_profile not in VERIFIED_PROTOCOLS):
            return None
        return authority
    except (OSError, ValueError, TypeError, KeyError, AttributeError, WriteProofError):
        return None


def load_deletion_authority(path: Path, source: Path, library_id: str):
    try:
        data = _read_json(path, 64 * 1024)
        if data.get('format') != DELETE_AUTHORITY_FORMAT:
            return None
        data['pilot_operation_ids'] = tuple(data.get('pilot_operation_ids', ()))
        authority = TagDeletionAuthority(**data)
        if (authority.database != str(source) or authority.library_id != library_id
                or not authority.accepted or authority.protocol_profile not in VERIFIED_DELETE_PROTOCOLS):
            return None
        return authority
    except (OSError, ValueError, TypeError, KeyError, AttributeError, WriteProofError):
        return None


def validate_deletion_request(request):
    if (not isinstance(request, dict) or set(request) not in
            (_DELETE_REQUEST_FIELDS, _DELETE_REQUEST_FIELDS | {'clocks'})):
        raise WriteProofError('Invalid captured tag-deletion request')
    try:
        clock = datetime.strptime(request['timestamp'], '%Y-%m-%d %H:%M:%S.%f +00:00')
        valid_clock = (clock.year >= 1970 and clock.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                       + ' +00:00' == request['timestamp'])
        if 'clocks' in request:
            if not isinstance(request['clocks'], dict) or set(request['clocks']) != _DELETE_CLOCK_FIELDS:
                raise ValueError('Invalid captured deletion clocks')
            for value in request['clocks'].values():
                parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
                valid_clock &= parsed.year >= 1970 and parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00' == value
        valid_id = (type(request['tag_id']) is str and request['tag_id'] == str(int(request['tag_id']))
                    and 0 < int(request['tag_id']) < 2**32)
    except (ValueError, TypeError, KeyError, OverflowError):
        valid_clock = valid_id = False
    if (request['action'] != 'delete' or not _operation_id(request['operation_id'])
            or not valid_clock or not valid_id or not _text(request['name'])
            or any(not _text(request[key], 256)
                   for key in ('library_id', 'category_id', 'category_uuid', 'tag_uuid'))):
        raise WriteProofError('The selected tag identity or captured deletion clock is invalid')
    return request


def deletion_request_for(library_id, category_id, category_uuid, tag_id, tag_uuid, name, *, now=None,
                         target_now=None, sibling_now=None, association_now=None):
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise WriteProofError('An explicit timezone-aware tag-deletion clock is required')
    clocks = {}
    for phase, captured in (('target', target_now), ('siblings', sibling_now), ('associations', association_now)):
        captured = captured if captured is not None else clock
        if not isinstance(captured, datetime) or captured.tzinfo is None or captured.utcoffset() is None:
            raise WriteProofError('Explicit timezone-aware deletion phase clocks are required')
        clocks[phase] = captured.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00'
    return validate_deletion_request(dict(action='delete', operation_id=uuid4().hex,
        library_id=library_id, category_id=category_id, category_uuid=category_uuid,
        tag_id=tag_id, tag_uuid=tag_uuid, name=name,
        timestamp=clock.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00', clocks=clocks))


def tag_rule_reference(rule, tag_id):
    """Read-only dependency proof for both positive and negative My Tag rules."""
    if rule is None:
        return False
    try:
        if not isinstance(rule, str) or not 0 < len(rule) <= 1024**2:
            raise ValueError('Unbounded or empty intelligent rule')
        tree = ET.fromstring(rule)
        conditions = list(tree.iter('CONDITION'))
        if tree.tag != 'NODE' or not conditions:
            raise ValueError('Unknown intelligent rule shape')
        found = False
        for condition in conditions:
            if condition.get('PropertyName') == 'myTag':
                value = int(condition.attrib['ValueLeft'])
                if not -(2**31) <= value < 2**32:
                    raise ValueError('Unverified My Tag reference')
                found |= str(value % 2**32) == tag_id
        return found
    except (ET.ParseError, TypeError, ValueError, KeyError) as exc:
        raise WriteProofError('Intelligent-playlist tag references could not be verified') from exc


def plan_create(request, category, siblings, counter, protocol: DefinitionProtocol):
    """Pure proposed final-row plan; a profile here grants no source permission."""
    validate_request(request)
    if not isinstance(protocol, DefinitionProtocol):
        raise WriteProofError('An explicit proposed tag-definition protocol is required')
    increment = protocol.increment_for(request['name'])
    if (not isinstance(category, dict) or set(category) != _COLUMNS
            or category['ID'] != request['category_id'] or category['UUID'] != request['category_uuid']
            or category['Attribute'] != 1 or category['ParentID'] != 'root'
            or category['rb_local_deleted'] != 0
            or not isinstance(siblings, list) or len(siblings) >= _MAX_SIBLINGS
            or not isinstance(counter, dict) or counter.get('registry_id') != 'localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0 <= counter['int_1'] < 2**63 - increment):
        raise ConflictError('The selected category or native counter is not a supported baseline')
    # Native reference creation uses an unchanged historic built-in category:
    # status256/local0/deleted0/synced1 with historical USNs. These read-only
    # references are not permission to edit categories/cloud metadata. A blanket
    # usn=NULL rule would reject the actual unchanged native parent row.
    if not _historical_category_profile(category, counter):
        raise WriteProofError('The selected category has an unverified native synchronization profile')
    active = []
    for row in siblings:
        if (not isinstance(row, dict) or set(row) != _COLUMNS
                or row['ParentID'] != request['category_id'] or row['Attribute'] != 0
                or type(row['ID']) is not str or not row['ID'] or type(row['UUID']) is not str or not row['UUID']
                or type(row['Name']) is not str
                or type(row['rb_local_deleted']) is not int or row['rb_local_deleted'] not in (0, 1)):
            raise WriteProofError('The selected category contains unverified definition rows')
        if not row['rb_local_deleted']:
            if type(row['Seq']) is not int or not 0 < row['Seq'] < 2**31 - 1:
                raise WriteProofError('Tag ordering is not supported')
            active.append(row)
    if len({row['Seq'] for row in active}) != len(active):
        raise ConflictError('The selected category has ambiguous tag ordering')
    if any(row['Name'].casefold() == request['name'].casefold() for row in active):
        raise ConflictError('An active tag with this name already exists in the selected category')
    number = counter['int_1'] + increment
    created = dict(ID=request['tag_id'], Seq=max((row['Seq'] for row in active), default=0) + 1,
        Name=request['name'], Attribute=0, ParentID=request['category_id'], UUID=request['tag_uuid'],
        rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0,
        usn=None, rb_local_usn=number, created_at=request['timestamp'],
        updated_at=request['final_timestamp'] if protocol.names_new_row(request['name']) else request['timestamp'])
    return [{'table': 'djmdMyTag', 'key': request['tag_id'], 'before': None, 'after': created},
            {'table': 'agentRegistry', 'key': 'localUpdateCount', 'before': deepcopy(counter),
             'after': {**counter, 'int_1': number}}]


def _deletion_local(row, counter):
    if (any(type(row.get(key)) is not int or row[key] != 0 for key in
            ('rb_data_status', 'rb_local_data_status', 'rb_local_deleted', 'rb_local_synced'))
            or row.get('usn') is not None or type(row.get('rb_local_usn')) is not int
            or not 0 <= row['rb_local_usn'] <= counter['int_1']):
        raise WriteProofError('Only unsynced local tag rows have a deletion reference')
    try:
        if str(UUID(row['UUID'])) != row['UUID']:
            raise ValueError('UUID')
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise WriteProofError('The local tag has an unverified native UUID') from exc


def _deletion_synced(row, counter, *, definition=False, historical=False):
    """Only the typed historical envelopes observed in the two native deletes."""
    status = row.get('rb_data_status')
    if (any(type(row.get(key)) is not int or row[key] != expected for key, expected in
            (('rb_local_data_status', 0), ('rb_local_deleted', int(historical)), ('rb_local_synced', 1)))
            or type(status) is not int or status not in ((0, 257) if definition else ((2,) if historical else (0,)))
            or type(row.get('rb_local_usn')) is not int or not 0 <= row['rb_local_usn'] <= counter['int_1']
            or (status == 257 and (type(row.get('usn')) is not int or not 0 <= row['usn'] < 2**63))
            or (status != 257 and row.get('usn') is not None)):
        raise WriteProofError('This synced tag or assignment state needs a separate native deletion reference')
    try:
        if str(UUID(row['UUID'])) != row['UUID']:
            raise ValueError('UUID')
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise WriteProofError('The synced tag row has an unverified native UUID') from exc


def plan_delete(request, category, siblings, counter, protocol: DeletionProtocol, *, links=None):
    """Measured row lifetimes and later-sibling compaction, without authority.

    Local leaves/links hard-delete. Known synced leaves and active synced links
    tombstone; existing association history is retained. Intelligent rules stay
    byte-for-byte read dependencies. Synced target/link/shift clocks are separate.
    """
    validate_deletion_request(request)
    if not isinstance(protocol, DeletionProtocol):
        raise WriteProofError('An explicit tag-deletion protocol is required')
    if (not isinstance(category, dict) or set(category) != _COLUMNS
            or category['ID'] != request['category_id'] or category['UUID'] != request['category_uuid']
            or category['ParentID'] != 'root' or not isinstance(siblings, list)
            or len(siblings) > _MAX_SIBLINGS or not isinstance(counter, dict)
            or counter.get('registry_id') != 'localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0 <= counter['int_1'] < 2**63 - 1
            or not _historical_category_profile(category, counter)):
        raise ConflictError('The selected category or deletion counter changed or is unsupported')
    active = []
    for row in siblings:
        if (not isinstance(row, dict) or set(row) != _COLUMNS
                or row['ParentID'] != request['category_id']
                or type(row['Attribute']) is not int or row['Attribute'] != 0
                or not _text(row['ID'], 256) or not _text(row['UUID'], 256)
                or type(row['Name']) is not str or type(row['rb_local_deleted']) is not int
                or row['rb_local_deleted'] not in (0, 1)):
            raise WriteProofError('The tag category contains unverified definition rows')
        if row['rb_local_deleted'] == 0:
            if type(row['Seq']) is not int or not 0 < row['Seq'] < 2**31 - 1:
                raise WriteProofError('The tag category has unsupported active ordering')
            active.append(row)
    if (len({row['ID'] for row in siblings}) != len(siblings)
            or len({row['UUID'] for row in siblings}) != len(siblings)
            or len({row['Seq'] for row in active}) != len(active)):
        raise ConflictError('The tag category has ambiguous definition identities or ordering')
    matches = [row for row in active if row['ID'] == request['tag_id']]
    if (len(matches) != 1 or matches[0]['UUID'] != request['tag_uuid']
            or matches[0]['Name'] != request['name']):
        raise ConflictError('The selected tag identity, name or category changed')
    target = matches[0]
    synced = protocol.synced_history and target['rb_local_synced'] == 1
    if synced:
        if 'clocks' not in request:
            raise WriteProofError('Synced deletion needs independently captured phase clocks')
        _deletion_synced(target, counter, definition=True)
    else:
        _deletion_local(target, counter)
    clocks = request.get('clocks', {phase: request['timestamp'] for phase in _DELETE_CLOCK_FIELDS})
    following = sorted((row for row in active if row['Seq'] > target['Seq']), key=lambda row: row['Seq'])
    links = [] if links is None else links
    if not isinstance(links, list) or len(links) > _MAX_SIBLINGS:
        raise WriteProofError('The tag assignment inventory exceeds its supported bound')
    seen_ids, seen_uuids = set(), set()
    hard_links, synced_links = [], []
    for link in links:
        if (not isinstance(link, dict) or set(link) != _LINK_COLUMNS
                or link['MyTagID'] != target['ID'] or not _text(link['ID'], 256)
                or not _text(link['UUID'], 256) or not _text(link['ContentID'], 256) or link['TrackNo'] is not None
                or link['ID'] in seen_ids or link['UUID'] in seen_uuids):
            raise WriteProofError('The selected tag has an unverified assignment identity')
        if not synced or link['rb_local_synced'] == 0:
            _deletion_local(link, counter)
            hard_links.append(link)
        elif link['rb_local_deleted'] == 0:
            _deletion_synced(link, counter)
            synced_links.append(link)
        else:
            _deletion_synced(link, counter, historical=True)
        seen_ids.add(link['ID']);seen_uuids.add(link['UUID'])
    if synced and not synced_links:
        raise WriteProofError('Synced tag deletion without active synced assignments needs a native reference')
    amount = len(following) * protocol.shift_increment + protocol.deletion_increment + bool(synced_links)
    if counter['int_1'] >= 2**63 - amount:
        raise WriteProofError('The tag-deletion counter is exhausted')
    changes = []
    # The synced target owns C+1; local hard-deletion's counter step follows
    # its sibling shifts. These are measured final windows, not SQL order.
    number = counter['int_1'] + (protocol.deletion_increment if synced else 0)
    for row in following:
        _deletion_local(row, counter)  # Surviving synced siblings remain unproved.
        number += protocol.shift_increment
        changes.append(dict(table='djmdMyTag', key=row['ID'], before=deepcopy(row),
            after={**row, 'Seq': row['Seq'] - 1, 'rb_local_usn': number, 'updated_at': clocks['siblings']}))
    final_number = counter['int_1'] + amount
    for link in hard_links:
        changes.append(dict(table='djmdSongMyTag', key=link['ID'], before=deepcopy(link), after=None))
    for link in synced_links:
        changes.append(dict(table='djmdSongMyTag', key=link['ID'], before=deepcopy(link),
            after={**link, 'rb_data_status': 2, 'rb_local_deleted': 1, 'rb_local_usn': final_number,
                   'updated_at': clocks['associations']}))
    after_target = ({**target, 'rb_data_status': 258 if target['rb_data_status'] == 257 else 2,
                     'rb_local_deleted': 1, 'rb_local_usn': counter['int_1'] + protocol.deletion_increment,
                     'updated_at': clocks['target']} if synced else None)
    changes.append(dict(table='djmdMyTag', key=target['ID'], before=deepcopy(target), after=after_target))
    changes.append(dict(table='agentRegistry', key='localUpdateCount', before=deepcopy(counter),
                        after={**counter, 'int_1': final_number}))
    return changes


def _deleted_siblings(siblings, changes):
    by_id = {row['ID']: row for row in siblings}
    for change in changes:
        if change['table'] == 'djmdMyTag':
            if change['after'] is None:
                by_id.pop(change['key'])
            else:
                by_id[change['key']] = change['after']
    return list(by_id.values())


def _revision(category, siblings, counter, dependencies=None):
    captured = dict(category=category, siblings=sorted(siblings, key=lambda row: row['ID']), counter=counter)
    if dependencies is not None:
        captured['dependencies'] = dependencies
    return _digest(captured)


def _assert_closed():
    assert_rekordbox_closed()


def _fault(_phase):
    """Synthetic temporary-copy tests only; never inject into real libraries."""


class _Lease(PilotCueStore):
    def __init__(self, store):
        self.store = store
        authority = store.authority
        # Reuse infrastructure, not cue SQL or cue authority admission.
        super().__init__(CueWriteAuthority(authority.database, authority.library_id,
            authority.schema_sha256, 'tag-definition-transaction', authority.application_version), store.root)

    def _native_version(self):
        return _installed_version()

    def _assert_native_closed(self):
        _assert_closed()

    def _guard(self):
        self.store._protocol()
        super()._guard()
        info = self.store.database.stat()
        if (info.st_dev, info.st_ino) != self.store.file_id:
            raise ConflictError('Native tag-definition source file was replaced')

    def _record(self):
        return self.store._record()

    def apply(self, _edit):
        raise WriteProofError('This mechanical lease does not grant cue-edit authority')

    def snapshot(self):
        raise WriteProofError('Cue APIs are unavailable on the tag-definition lease')

    def _recover(self, conn):
        return self.store._recover(conn)


class TagDefinitionStore:
    authority_type: type[TagDefinitionAuthority] | type[TagDeletionAuthority] | type[TagOrderAuthority] | type[TagRenameAuthority] = TagDefinitionAuthority
    record_format = FORMAT
    receipt_directory = 'tag-definition-receipts'

    def __init__(self, authority: TagDefinitionAuthority | TagDeletionAuthority | TagOrderAuthority | TagRenameAuthority, recovery_root: Path):
        if type(authority) is not self.authority_type:
            raise WriteProofError('This tag-definition operation needs its own authority')
        self.authority = authority
        self.database = Path(authority.database)
        self.root = Path(recovery_root).absolute()
        self.journal = self.root / 'transaction.json'
        info = self.database.stat()
        self.file_id = info.st_dev, info.st_ino
        self.lease = _Lease(self)
        self._verified_before_key: tuple[str, str] | None = None

    def _protocol(self):
        protocol = VERIFIED_PROTOCOLS.get(self.authority.protocol_profile)
        if self.authority.accepted is not True or protocol is None:
            raise WriteProofError('Native tag-definition creation has not passed separate protocol/library acceptance')
        return protocol

    def _scope(self, request):
        self._validate_request(request)
        if (request['library_id'] != self.authority.library_id
                or self.authority.pilot_operation_ids and request['operation_id'] not in self.authority.pilot_operation_ids):
            raise WriteProofError('Tag-definition request is outside its exact library/pilot authority')

    @staticmethod
    def _validate_request(request):
        return validate_request(request)

    @staticmethod
    def _plan(request, category, siblings, counter, protocol, dependencies=None):
        if dependencies is not None:
            raise WriteProofError('Creation cannot admit deletion dependencies')
        return plan_create(request, category, siblings, counter, protocol)

    @staticmethod
    def _after_siblings(siblings, changes):
        return [*siblings, changes[0]['after']]

    @staticmethod
    def _after_dependencies(dependencies, changes):
        return dependencies

    @staticmethod
    def _identity_available(conn, request):
        if conn.execute('SELECT 1 FROM djmdMyTag WHERE ID=? OR UUID=?',
                        (request['tag_id'], request['tag_uuid'])).fetchone():
            raise ConflictError('Captured tag ID/UUID is already occupied; no name-based adoption is allowed')

    @staticmethod
    def _row(conn, table, key):
        pk = 'registry_id' if table == 'agentRegistry' else 'ID'
        cursor = conn.execute(f'SELECT * FROM {_quote(table)} WHERE {_quote(pk)}=?', (key,))
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        if len(rows) > 1:
            raise ConflictError('Ambiguous native row identity')
        return dict(zip(names, rows[0], strict=True)) if rows else None

    def _snapshot(self, conn, request):
        category = self._row(conn, 'djmdMyTag', request['category_id'])
        siblings = self.lease._rows(conn, 'djmdMyTag', ' WHERE ParentID=? ORDER BY ID LIMIT ?',
                                   (request['category_id'], _MAX_SIBLINGS + 1))
        if len(siblings) > _MAX_SIBLINGS:
            raise ConflictError('Tag-definition category exceeds its bounded supported inventory')
        counter = self._row(conn, 'agentRegistry', 'localUpdateCount')
        return category, siblings, counter

    def _read_record(self, path, *, pending):
        try:
            record = _read_json(path, _MAX_RECORD_BYTES)
            info = self.database.stat()
            if (record['format'] != self.record_format or record['source'] != str(self.database)
                    or record['library_id'] != self.authority.library_id
                    or record['schema_sha256'] != self.authority.schema_sha256
                    or record['application_version'] != self.authority.application_version
                    or record['source_file_id'] != [info.st_dev, info.st_ino]
                    or record['protocol'] != asdict(self._protocol())):
                raise RecoveryRequired('Tag-definition recovery belongs to another source/profile')
            request = record['request'];self._scope(request)
            dependencies = record.get('dependencies')
            expected = self._plan(request, record['category'], record['siblings'], record['counter'], self._protocol(), dependencies)
            after_siblings = self._after_siblings(record['siblings'], expected)
            if (_json(record['changes']) != _json(expected) or record['operation_id'] != request['operation_id']
                    or record['before_revision'] != _revision(record['category'], record['siblings'], record['counter'], dependencies)
                    or record['after_revision'] != _revision(record['category'], after_siblings, expected[-1]['after'],
                                                            self._after_dependencies(dependencies, expected))
                    or type(record['baseline_sha256']) is not str or len(record['baseline_sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in record['baseline_sha256'])):
                raise RecoveryRequired('Tag-definition recovery differs from its exact captured plan')
            if pending:
                self._verify_pending_before(record)
            return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError, WriteProofError) as exc:
            raise RecoveryRequired('Tag-definition recovery is damaged or outside this domain; evidence preserved') from exc

    def _record(self):
        return self._read_record(self.journal, pending=True)

    def _verify_pending_before(self, record):
        """Bind the semantic BEFORE plan to those exact encrypted backup bytes.

        Guard calls still validate record bytes and the backup's fresh identity/
        digest proof. Only this pure immutable reconstruction may be reused.
        Completed operation receipts intentionally survive baseline rotation.
        """
        sha = record['baseline_sha256']
        self.lease._verify_backup(sha)
        key = sha, _digest(record)
        if key == self._verified_before_key:
            return
        before = self.lease._baseline_identity()
        with tempfile.TemporaryDirectory(prefix='soundbox-tag-definition-before-') as td:
            captured = Path(td).resolve() / 'baseline.db'
            copy_state(self.root / 'baseline.db', captured)
            if _file_sha(captured) != sha or self.lease._baseline_identity() != before:
                raise RecoveryRequired('Tag-definition baseline changed during semantic capture')
            conn = _connect_copy(captured, readonly=True)
            try:
                if (_identity(conn) != (self.authority.library_id, '6000')
                        or _schema(conn) != self.authority.schema_sha256
                        or _revision(*self._snapshot(conn, record['request'])) != record['before_revision']):
                    raise RecoveryRequired('Tag-definition BEFORE rows do not match the verified encrypted baseline')
                self._identity_available(conn, record['request'])
            finally:
                conn.close()
        self._verified_before_key = key

    def _receipt_path(self, operation_id):
        if not _operation_id(operation_id):
            raise WriteProofError('Invalid tag-definition operation receipt')
        folder = self.root / self.receipt_directory
        if folder.is_symlink() or folder != folder.resolve():
            raise RecoveryRequired('Tag-definition receipt directory redirects')
        folder.mkdir(exist_ok=True, mode=0o700)
        if folder.stat().st_mode & 0o077:
            raise RecoveryRequired('Tag-definition receipt directory is not private')
        path = folder / f'{operation_id}.json'
        if path.exists() or path.is_symlink():
            _regular(path)
        return path

    def _recover(self, conn):
        if not self.journal.exists():
            return None
        record = self._record()
        conn.execute('BEGIN IMMEDIATE')
        try:
            self.lease._guard()
            current = _revision(*self._snapshot(conn, record['request']))
            if current == record['after_revision']:
                outcome = 'committed'
            elif current == record['before_revision']:
                outcome = 'rolled-back'
            else:
                raise RecoveryRequired('Unexpected tag-definition state; recovery evidence preserved')
            receipt = {**record, 'outcome': outcome, 'inactive_sqlite_journal': _inactive_journal_proof(self.database)}
            if outcome == 'committed':
                atomic_bytes(self._receipt_path(record['operation_id']), _json(receipt))
            atomic_bytes(self.root / 'last-result.json', _json(receipt))
            self.journal.unlink();sync_dir(self.root)
            conn.commit()
            return outcome
        except BaseException:
            conn.rollback()
            raise

    def _replay(self, conn, request):
        path = self._receipt_path(request['operation_id'])
        if not path.exists():
            return None
        receipt = self._read_record(path, pending=False)
        if receipt.get('outcome') != 'committed' or receipt['request'] != request:
            raise RecoveryRequired('Tag-definition operation receipt does not match the intention')
        row = self._row(conn, 'djmdMyTag', request['tag_id'])
        category = self._row(conn, 'djmdMyTag', request['category_id'])
        counter = self._row(conn, 'agentRegistry', 'localUpdateCount')
        if (row != receipt['changes'][0]['after'] or not category
                or category['UUID'] != request['category_uuid'] or category['Attribute'] != 1
                or category['ParentID'] != 'root' or category['rb_local_deleted'] != 0
                or not counter or type(counter['int_1']) is not int
                or not _historical_category_profile(category, counter)
                or counter['int_1'] < receipt['changes'][1]['after']['int_1']):
            raise RecoveryRequired('The previously created native tag changed; preserve its receipt')
        self.lease._guard()
        return row

    @staticmethod
    def _authorizer(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            if trigger or database != 'main':return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_INSERT and table == 'djmdMyTag':return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_UPDATE and table == 'agentRegistry' and column == 'int_1':return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION,
                      sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def check_ready(self):
        self._protocol()
        with self.lease._lock():
            conn = self.lease._connect()
            try:self._recover(conn)
            finally:conn.close()

    def recover(self):
        self._protocol()
        with self.lease._lock():
            conn = self.lease._connect()
            try:return self._recover(conn)
            finally:conn.close()

    def apply(self, request, *, expected_state=None):
        request = deepcopy(request);self._scope(request)
        if expected_state is not None and not isinstance(expected_state,dict):
            raise WriteProofError('A reviewed tag operation requires its complete logical baseline')
        expected_state=deepcopy(expected_state)
        protocol = self._protocol()
        with self.lease._lock():
            if expected_state is not None:
                # A source pilot may not recover/replay a different operation
                # before proving its complete approved BEFORE under this lease.
                if self.journal.exists() or self.journal.is_symlink():
                    raise RecoveryRequired('Reviewed tag operation requires inactive source recovery')
                if self._receipt_path(request['operation_id']).exists():
                    raise RecoveryRequired('Reviewed tag operation already has a receipt; inspect before replay')
                with encrypted_backup(self.database) as before:
                    captured=_connect_copy(before,readonly=True)
                    try:
                        if _state(captured)!=expected_state:
                            raise ConflictError('The reviewed complete tag baseline changed before publication')
                    finally:captured.close()
            conn = self.lease._connect()
            try:
                self._recover(conn)
                replay = self._replay(conn, request)
                if replay is not None:return replay
                conn.execute('BEGIN IMMEDIATE');self.lease._guard()
                if (_identity(conn) != (self.authority.library_id, '6000')
                        or _schema(conn) != self.authority.schema_sha256):
                    raise ConflictError('Tag-definition source identity/schema changed')
                captured = self._snapshot(conn, request)
                category, siblings, counter = captured[:3]
                dependencies = captured[3] if len(captured) == 4 else None
                self._identity_available(conn, request)
                changes = self._plan(request, category, siblings, counter, protocol, dependencies)
                # Cue infrastructure may retain an older protective baseline
                # across edits. This domain independently proves each exact
                # BEFORE snapshot, so every new operation needs fresh bytes.
                self.lease._last_counter = None
                self.lease._backup(counter['int_1'])
                record = dict(format=self.record_format, source=str(self.database), library_id=self.authority.library_id,
                    schema_sha256=self.authority.schema_sha256, application_version=self.authority.application_version,
                    source_file_id=list(self.file_id), protocol=asdict(protocol), operation_id=request['operation_id'],
                    baseline_sha256=self.lease._backup_sha, request=request, category=category,
                    siblings=siblings, counter=counter, changes=changes,
                    before_revision=_revision(category, siblings, counter, dependencies),
                    after_revision=_revision(category, self._after_siblings(siblings, changes), changes[-1]['after'],
                                             self._after_dependencies(dependencies, changes)))
                if dependencies is not None:
                    record['dependencies'] = dependencies
                payload = _json(record)
                if len(payload) > _MAX_RECORD_BYTES:raise WriteProofError('Tag-definition transaction exceeds its record bound')
                atomic_bytes(self.journal, payload);_fault('journal-ready');self.lease._guard()
                conn.set_authorizer(self._authorizer)
                row = self._apply_changes(conn, request, changes)
                if _revision(*self._snapshot(conn, request)) != record['after_revision']:
                    raise WriteProofError('Created tag did not match its exact proposed rows')
                self.lease._guard();_fault('before-commit');conn.commit();_fault('after-commit')
                conn.close();conn = None
                conn = self.lease._connect()
                if self._recover(conn) != 'committed':
                    raise RecoveryRequired('Tag-definition commit could not be independently verified')
                self.lease._last_counter = changes[-1]['after']['int_1']
                return deepcopy(row)
            except BaseException:
                if conn is not None:conn.rollback()
                raise
            finally:
                if conn is not None:conn.close()

    @staticmethod
    def _apply_changes(conn, request, changes):
        row = changes[0]['after'];columns = list(row)
        conn.execute(f'INSERT INTO djmdMyTag ({",".join(_quote(key) for key in columns)}) '
                     f'VALUES ({",".join("?" for _ in columns)})', tuple(row.values()))
        conn.execute('UPDATE agentRegistry SET int_1=? WHERE registry_id=?',
                     (changes[-1]['after']['int_1'], 'localUpdateCount'))
        return row


class TagDeletionStore(TagDefinitionStore):
    """Independent leaf-only deletion on the shared guarded transaction lease."""
    authority_type = TagDeletionAuthority
    record_format = DELETE_FORMAT
    receipt_directory = 'tag-deletion-receipts'

    def _protocol(self):
        protocol = VERIFIED_DELETE_PROTOCOLS.get(self.authority.protocol_profile)
        if self.authority.accepted is not True or protocol is None:
            raise WriteProofError('Native tag deletion has not passed separate protocol/library acceptance')
        return protocol

    @staticmethod
    def _validate_request(request):
        return validate_deletion_request(request)

    @staticmethod
    def _plan(request, category, siblings, counter, protocol, dependencies=None):
        if (not isinstance(dependencies, dict) or set(dependencies) != {'links', 'smart_rules'}
                or not isinstance(dependencies['smart_rules'], list)
                or len(dependencies['smart_rules']) > _MAX_SIBLINGS):
            raise WriteProofError('Invalid captured tag-deletion dependencies')
        seen = set()
        for rule in dependencies['smart_rules']:
            if (not isinstance(rule, dict) or set(rule) != {'ID', 'SmartList'}
                    or not _text(rule['ID'], 256) or rule['ID'] in seen
                    or rule['SmartList'] is not None and (not isinstance(rule['SmartList'], str)
                                                        or len(rule['SmartList']) > 1024**2)):
                raise WriteProofError('Invalid captured intelligent-rule dependency')
            seen.add(rule['ID'])
        return plan_delete(request, category, siblings, counter, protocol, links=dependencies['links'])

    @staticmethod
    def _after_siblings(siblings, changes):
        return _deleted_siblings(siblings, changes)

    @staticmethod
    def _after_dependencies(dependencies, changes):
        rows = {row['ID']: row for row in dependencies['links']}
        for change in changes:
            if change['table'] != 'djmdSongMyTag':
                continue
            if change['after'] is None:
                rows.pop(change['key'])
            else:
                rows[change['key']] = change['after']
        return {**dependencies, 'links': [rows[key] for key in sorted(rows)]}

    @staticmethod
    def _identity_available(conn, request):
        # The independently checked BEFORE snapshot binds the existing target.
        # No new identity is admitted or adopted by deletion.
        return None

    def _snapshot(self, conn, request):
        captured = super()._snapshot(conn, request)
        identities = conn.execute('SELECT ID,UUID,ParentID FROM djmdMyTag WHERE ID=? OR UUID=? LIMIT 2',
                                  (request['tag_id'], request['tag_uuid'])).fetchall()
        if (len(identities) > 1 or identities and identities[0] !=
                (request['tag_id'], request['tag_uuid'], request['category_id'])):
            raise ConflictError('The selected native tag identity is ambiguous')
        links = self.lease._rows(conn, 'djmdSongMyTag', ' WHERE MyTagID=? ORDER BY ID LIMIT ?',
                               (request['tag_id'], _MAX_SIBLINGS + 1))
        if len(links) > _MAX_SIBLINGS:
            raise WriteProofError('The tag assignment inventory exceeds its supported bound')
        cursor = conn.execute('SELECT ID,substr(SmartList,1,?) FROM djmdPlaylist '
                              'WHERE Attribute=4 AND rb_local_deleted=0 ORDER BY ID LIMIT ?',
                              (1024**2 + 1, _MAX_SIBLINGS + 1))
        count = total = 0
        rules = []
        for identity, rule in cursor:
            count += 1
            total += len(rule.encode('utf-8')) if isinstance(rule, str) else len(rule or b'')
            if count > _MAX_SIBLINGS or total > _MAX_RECORD_BYTES:
                raise WriteProofError('Intelligent-playlist reference inventory exceeds its supported bound')
            if rule is not None and (not isinstance(rule, str) or len(rule) > 1024**2):
                raise WriteProofError('Intelligent-rule dependency exceeds its supported shape')
            rules.append(dict(ID=identity, SmartList=rule))
        return (*captured, dict(links=links, smart_rules=rules))

    def _replay(self, conn, request):
        path = self._receipt_path(request['operation_id'])
        if not path.exists():
            return None
        receipt = self._read_record(path, pending=False)
        if receipt.get('outcome') != 'committed' or receipt['request'] != request:
            raise RecoveryRequired('The tag-deletion receipt does not match the intention')
        category, siblings, counter, dependencies = self._snapshot(conn, request)
        expected_target = receipt['changes'][-2]['after']
        expected_links = self._after_dependencies(receipt['dependencies'], receipt['changes'])['links']
        current_target = self._row(conn, 'djmdMyTag', request['tag_id'])
        if (_json(current_target) != _json(expected_target) or _json(dependencies['links']) != _json(expected_links)
                or not category or category['UUID'] != request['category_uuid']
                or category['ParentID'] != 'root' or not counter
                or type(counter.get('int_1')) is not int or not 0 <= counter['int_1'] < 2**63
                or not _historical_category_profile(category, counter)
                or counter['int_1'] < receipt['changes'][-1]['after']['int_1']):
            raise RecoveryRequired('The deleted native tag/category identity changed; preserve its receipt')
        self.lease._guard()
        return deepcopy(receipt['changes'][-2]['before'])

    @staticmethod
    def _authorizer(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            if trigger or database != 'main':return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_DELETE and table in ('djmdMyTag', 'djmdSongMyTag'):return sqlite3.SQLITE_OK
            if (action == sqlite3.SQLITE_UPDATE and table == 'djmdMyTag'
                    and column in ('Seq', 'rb_data_status', 'rb_local_deleted', 'rb_local_usn', 'updated_at')):return sqlite3.SQLITE_OK
            if (action == sqlite3.SQLITE_UPDATE and table == 'djmdSongMyTag'
                    and column in ('rb_data_status', 'rb_local_deleted', 'rb_local_usn', 'updated_at')):return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_UPDATE and table == 'agentRegistry' and column == 'int_1':return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION,
                      sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _apply_changes(conn, request, changes):
        for change in changes[:-1]:
            table = change['table'];row = change['after']
            if table not in ('djmdMyTag', 'djmdSongMyTag'):
                raise WriteProofError('Deletion SQL exceeds tag-definition scope')
            if row is None:
                conn.execute(f'DELETE FROM {_quote(table)} WHERE ID=?', (change['key'],))
            else:
                columns = [key for key in row if row[key] != change['before'][key]]
                conn.execute(f'UPDATE {_quote(table)} SET ' + ','.join(_quote(key) + '=?' for key in columns)
                             + ' WHERE ID=?', (*[row[key] for key in columns], change['key']))
        conn.execute('UPDATE agentRegistry SET int_1=? WHERE registry_id=?',
                     (changes[-1]['after']['int_1'], 'localUpdateCount'))
        return changes[-2]['before']
