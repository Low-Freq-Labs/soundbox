"""Pure observed bare-content row construction; no filesystem or source writer.

Shared by the narrow research proofs and the separately authorized registration
adapter. These helpers alone grant no authority or musical metadata publication.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from dataclasses import asdict
from uuid import UUID

from .native_media import MediaIdentity
from .rekordbox_write import ConflictError, WriteProofError, _cell, _quote, _rows

REQUEST_FORMAT='soundbox-native-registration-request-v1'

_NULL = '''ArtistID AlbumID GenreID RemixerID LabelID OrgArtistID SearchStr ComposerID
DisableQuantize Tag CueUpdated AnalysisUpdated TrackInfoUpdated Reserved2 Reserved3
Reserved4 rb_LocalFolderPath SrcID SrcTitle SrcArtistName SrcAlbumName SrcLength usn'''.split()

_EMPTY = '''FileNameS Commnt ImagePath AnalysisDataPath Subtitle ReleaseDate ModifiedByRBM
DeliveryComment Lyricist ISRC OrgFolderPath Reserved1'''.split()

_ZERO = '''BPM TrackNo BitRate BitDepth Rating ReleaseYear DJPlayCount DiscNo SampleRate
Analysed SamplerTrackInfo SamplerPlayOffset LyricStatus ServiceID rb_data_status
rb_local_data_status rb_local_deleted rb_local_synced'''.split()

_DEFAULTS = {**dict.fromkeys(_NULL), **dict.fromkeys(_EMPTY, ''), **dict.fromkeys(_ZERO, 0),
             'KeyID': '0', 'ColorID': '0', 'VideoAssociate': '0', 'SamplerGain': 0.0,
             'ExtInfo': 'null', 'HotCueAutoLoad': 'on', 'DeliveryControl': 'on', 'ContentLink': 14}

_FIELDS = frozenset(_DEFAULTS) | frozenset('''ID FolderPath FileNameL Title Length FileType
StockDate MasterDBID MasterSongID FileSize DateCreated rb_file_id DeviceID UUID
rb_local_usn created_at updated_at'''.split())

def _native_id(value):
    if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
            or str(int(value)) != value or not 0 < int(value) < 2**32):
        raise WriteProofError('Invalid native content/library ID')
    return value

def _uuid(value):
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise WriteProofError('Invalid native UUID') from exc
    if str(parsed) != value or parsed.version != 4:
        raise WriteProofError('Only canonical v4 native UUIDs are proven')
    return value

def build_row(audio, *, identity, uuid, library, device, timestamp, day, counter):
    """Observed defaults + explicit identity/clock; no reference-row copying."""
    audio.validate()
    _native_id(identity); _native_id(library); _uuid(uuid); _uuid(device)
    try:
        stamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f +00:00')
        date = datetime.strptime(day, '%Y-%m-%d')
    except (ValueError, TypeError) as exc:
        raise WriteProofError('Explicit native UTC timestamp and stock date required') from exc
    if (stamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' +00:00' != timestamp
            or date.strftime('%Y-%m-%d') != day or type(counter) is not int
            or not 0 <= counter < 2**63 - 1):
        raise WriteProofError('Noncanonical clock or invalid update counter')
    path = Path(audio.path)
    return {**_DEFAULTS, 'ID': identity, 'FolderPath': audio.path, 'FileNameL': path.name,
            'Title': path.stem, 'Length': audio.seconds, 'FileType': 11, 'StockDate': day,
            'MasterDBID': library, 'MasterSongID': identity, 'FileSize': audio.epoch[2],
            'DateCreated': day, 'rb_file_id': str(audio.epoch[1]), 'DeviceID': device,
            'UUID': uuid, 'rb_local_usn': counter + 1, 'created_at': timestamp, 'updated_at': timestamp}


def validate_request(request):
    if (not isinstance(request,dict) or set(request)!={'format','operation_id','source','library_id','schema_sha256','tracks'}
            or request['format']!=REQUEST_FORMAT):
        raise WriteProofError('Unknown native registration request')
    _uuid(request['operation_id']);_native_id(request['library_id'])
    source=Path(request['source'])
    if (not source.is_absolute() or str(source)!=request['source'] or '..' in source.parts or '\0' in request['source']
            or source.name!='master.db' or not isinstance(request['schema_sha256'],str)
            or len(request['schema_sha256'])!=64 or any(c not in '0123456789abcdef' for c in request['schema_sha256'])
            or not isinstance(request['tracks'],list) or not 1<=len(request['tracks'])<=10000):
        raise WriteProofError('Explicit bounded native registration binding required')
    identities=set();uuids=set();paths=set();local=set()
    for claim in request['tracks']:
        if not isinstance(claim,dict) or set(claim)!={'local_id','audio','native_id','native_uuid','timestamp','day'}:
            raise WriteProofError('Unknown registration track claim')
        audio=MediaIdentity.from_dict(claim['audio']);_native_id(claim['native_id']);_uuid(claim['native_uuid'])
        if not isinstance(claim['local_id'],str) or not claim['local_id'] or '\0' in claim['local_id']:
            raise WriteProofError('Stable local track identity required')
        if (claim['native_id'] in identities or claim['native_uuid'] in uuids
                or claim['local_id'] in local or audio.path in paths):
            raise ConflictError('Ambiguous registration batch identities')
        # Canonical native clock/date validation also belongs to the durable request.
        build_row(audio,identity=claim['native_id'],uuid=claim['native_uuid'],library=request['library_id'],
                  device='00000000-0000-4000-8000-000000000001',timestamp=claim['timestamp'],day=claim['day'],counter=0)
        identities.add(claim['native_id']);uuids.add(claim['native_uuid']);paths.add(audio.path);local.add(claim['local_id'])
    return request


def make_claim(local_id,audio,*,native_id,native_uuid,timestamp,day):
    return dict(local_id=local_id,audio=asdict(audio),native_id=native_id,native_uuid=native_uuid,timestamp=timestamp,day=day)


def encoded_row(conn,table,identity):
    if table not in ('djmdContent','agentRegistry'):raise WriteProofError('Unknown registration read table')
    key='registry_id' if table=='agentRegistry' else 'ID'
    cursor=conn.execute('SELECT * FROM '+_quote(table)+' WHERE '+_quote(key)+'=?',(identity,))
    rows=cursor.fetchall();names=[v[0] for v in cursor.description]
    if len(rows)>1:raise ConflictError('Ambiguous native registration row')
    return {k:_cell(v) for k,v in zip(names,rows[0],strict=True)} if rows else None


def build_plan(conn,request):
    """Plan new rows or exact-path reuse on an explicitly captured SQL copy.

    No source I/O, registration authority, audio hashing or musical-data migration
    is inferred. Source transport must bind/check authority and freshness separately.
    """
    validate_request(request);properties=_rows(conn,'djmdProperty')
    if len(properties)!=1 or properties[0]['DBID']!=request['library_id'] or properties[0]['DBVersion']!='6000':
        raise ConflictError('Registration library identity/version changed')
    if {r[1] for r in conn.execute('PRAGMA table_info(djmdContent)')}!=_FIELDS:
        raise WriteProofError('Unverified native registration content columns')
    counter=encoded_row(conn,'agentRegistry','localUpdateCount')
    if counter is None or type(counter.get('int_1')) is not int or not 0<=counter['int_1']<2**63-len(request['tracks']):
        raise WriteProofError('Invalid native registration counter')
    changes=[];bindings=[];guards=[];existing_ids=set()
    for claim in request['tracks']:
        audio=MediaIdentity.from_dict(claim['audio'])
        cursor=conn.execute('SELECT * FROM djmdContent WHERE FolderPath=?',(audio.path,))
        names=[v[0] for v in cursor.description];matching=[dict(zip(names,v,strict=True)) for v in cursor.fetchall()]
        if matching:
            if len(matching)!=1 or type(matching[0]['rb_local_deleted']) is not int or matching[0]['rb_local_deleted']!=0:
                raise ConflictError('Historical or ambiguous native path requires reconciliation')
            row=matching[0];_native_id(row['ID']);_uuid(row['UUID']);_native_id(row['MasterDBID'])
            # A transferred native song can retain its original producer DBID.
            # Physical source/library authority comes from djmdProperty; reuse
            # preserves every existing row field, including that provenance.
            if row['FileType']!=audio.file_type or row['ID'] in existing_ids:
                raise ConflictError('Native path binding conflicts with captured media or another logical track')
            existing_ids.add(row['ID']);guards.append(dict(id=row['ID'],row={k:_cell(v) for k,v in row.items()}))
            registered=False
        else:
            if conn.execute('SELECT COUNT(*) FROM djmdContent WHERE ID=? OR UUID=?',
                            (claim['native_id'],claim['native_uuid'])).fetchone()!=(0,):
                raise ConflictError('Native registration identity is already occupied')
            row=build_row(audio,identity=claim['native_id'],uuid=claim['native_uuid'],library=request['library_id'],
                device=properties[0]['DeviceID'],timestamp=claim['timestamp'],day=claim['day'],counter=counter['int_1'])
            row['FileType']=audio.file_type
            changes.append(dict(table='djmdContent',id=row['ID'],before=None,after=row))
            after={**counter,'int_1':counter['int_1']+1}
            changes.append(dict(table='agentRegistry',id='localUpdateCount',before=counter,after=after));counter=after
            registered=True
        bindings.append(dict(local_id=claim['local_id'],source=request['source'],library_id=request['library_id'],
            native_id=row['ID'],native_uuid=row['UUID'],canonical=audio.path,registered=registered))
    return dict(changes=changes,bindings=bindings,guards=guards)
