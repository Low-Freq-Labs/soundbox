"""Frozen effective SoundBox metadata and identity-pinned curation partitions.

These are durable intentions, never a source-write authority. Plans preserve
both fields from one immutable input; publication cannot rederive an artist
after stripping the artist edge from the native title.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence
from uuid import uuid4

from . import metadata
from .paths import decode_location

TARGETS='source_truth_targets'
MIGRATION='source_truth_migration'
CURATION='source_curation'
_FORMAT='soundbox-source-truth-v1'
_SCOPED=('title_overrides','artist_overrides','artist_aliases','hidden_tracks',
         'playlist_removals','playlist_additions','custom')
_SCOPED_SETTINGS=('track_info','custom_playlist_tree','custom_playlist_expanded','playlist_unification')
_MAX_TRACKS=10000
_MAX_BYTES=32*1024**2


def _encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
def _sha(value):return hashlib.sha256(_encode(value)).hexdigest()


def scope(library):
    if library is None or not Path(str(library.source)).is_absolute():raise ValueError('A stable source path is required')
    value=dict(source=str(library.source),kind=library.source_kind,library_id=library.source_library_id)
    _validate_scope(value);return value


def _validate_scope(value):
    if (not isinstance(value,dict) or set(value)!={'source','kind','library_id'}
            or any(type(item) is not str or len(item)>4096 or '\0' in item for item in value.values())
            or not Path(value['source']).is_absolute() or not value['kind']
            or value['kind']=='rekordbox-db' and not value['library_id']):
        raise ValueError('Invalid saved source scope')


def scope_key(value):return _sha(value)


def capture(library,*,titles,artists,aliases,settings):
    """Detach only metadata/identity scalars, without reading source/audio files."""
    if library.source_kind!='rekordbox-db' or not library.source_library_id:
        raise ValueError('Source truth requires a verified native library identity')
    if len(library.tracks_by_id)>_MAX_TRACKS:raise ValueError('Source truth exceeds its bounded library inventory')
    tracks=[dict(local_id=t.track_id,native_id=t.native_id,uuid=t.source_uuid,
        path=t.source_native_path or str(t.path),location_uri=t.location_uri,name=t.name,artist=t.artist,
        issue=getattr(library,'source_binding_issues',{}).get(t.track_id,''),
        native_file_type=t.info.get('nativeFileType'),native_analysis_flags=t.info.get('nativeAnalysisFlags'))
        for t in library.tracks_by_id.values()]
    payload=dict(format=_FORMAT,scope=scope(library),tracks=tracks,titles=titles,artists=artists,aliases=aliases,
        rules_off=metadata.normalize_title_rules_off(settings.get('title_rules_off')),
        words=settings.get('title_words',[]),info=settings.get('track_info',{}))
    raw=_encode(payload)
    if len(raw)>_MAX_BYTES:raise ValueError('Frozen source truth exceeds its metadata safety bound')
    return raw


def build(raw,*,migration_id=None,include_equal=False):
    """Compute both intended fields once. Unknown identities never become new songs."""
    if type(raw) is not bytes or len(raw)>_MAX_BYTES:raise ValueError('Invalid frozen metadata payload')
    data=json.loads(raw)
    if data.get('format')!=_FORMAT or not isinstance(data.get('tracks'),list) or len(data['tracks'])>_MAX_TRACKS:
        raise ValueError('Invalid frozen source metadata inventory')
    tracks=data['tracks'];titles=data['titles'];artists=data['artists'];aliases=data['aliases']
    for values in (titles,artists,aliases):
        if not isinstance(values,dict) or any(type(k) is not str or type(v) is not str for k,v in values.items()):
            raise ValueError('Invalid frozen source curation')
    derivation=data.get('derivation',{})
    def inputs(track):return derivation.get(_sha([data['scope'],track['local_id'],track['native_id'],track['uuid'],track['path']]),track)
    bases=[metadata.derive_artist(inputs(t)['name'],inputs(t)['artist'])[0] for t in tracks]
    trusted=[inputs(t)['artist'] for t in tracks if inputs(t)['artist']]+[value for value in artists.values() if value.strip()]
    registry=metadata.build_registry(bases,trusted,aliases)
    junk=metadata.junk_patterns(data['rules_off'],data['words'])
    native_ids=Counter(t['native_id'] for t in tracks if t['uuid'])
    uuids=Counter(t['uuid'] for t in tracks if t['uuid'])
    migration_id=migration_id or uuid4().hex
    if type(migration_id) is not str or len(migration_id)!=32 or any(c not in '0123456789abcdef' for c in migration_id):
        raise ValueError('Invalid source-truth migration identity')
    targets={};order=[];unresolved=[];counts:Counter[str]=Counter()
    local_ids=set()
    for track in tracks:
        local=track['local_id']
        derived=inputs(track)
        if local in local_ids:raise ValueError('Duplicate frozen logical track identity')
        local_ids.add(local)
        display_artist=metadata.effective_artist(local,titles.get(local) or derived['name'],derived['artist'],
            artists,registry=registry,stem=decode_location(track['location_uri']).stem)
        title=(titles[local].strip() if titles.get(local,'').strip() else metadata.tidy_title(derived['title_base'],junk)
               if 'title_base' in derived else metadata.effective_title(local,derived['name'],display_artist,titles,junk))
        info=deepcopy(data['info'].get(local,{}))
        if 'title' in info:title=info.pop('title')
        if 'artist' in info:display_artist=info.pop('artist')
        if type(title) is not str or type(display_artist) is not str:raise ValueError('Visible metadata overlay must be text')
        artist='' if display_artist==metadata.UNKNOWN_ARTIST else display_artist
        differences=[field for field,current,desired in (('title',track['name'],title),('artist',track['artist'],artist))
                     if current!=desired]
        issue=('Native identity is unavailable' if not track['uuid'] else
               'Native identity is ambiguous' if native_ids[track['native_id']]!=1 or uuids[track['uuid']]!=1 else
               'Native binding needs reconciliation' if track['issue'] else '')
        if not differences and not info and not issue:
            counts['already_equal']+=1
            if not include_equal:continue
        key=_sha([data['scope'],local,track['native_id'],track['uuid'],track['path']])
        target=dict(format=_FORMAT,migration_id=migration_id,local_id=local,native_id=track['native_id'],
            uuid=track['uuid'],path=track['path'],scope=data['scope'],title=title,artist=artist,
            display_artist=display_artist,fields=differences,info=deepcopy(info),error=issue,
            native_file_type=track['native_file_type'],native_analysis_flags=track['native_analysis_flags'])
        targets[key]=target
        if issue:unresolved.append(key);counts['unresolved']+=1
        else:order.append(key)
        for field in differences:counts[field]+=1
        if info:counts['track_info']+=1
    for local in sorted((set(titles)|set(artists)|set(data['info']))-local_ids):
        key=_sha([data['scope'],'missing',local])
        targets[key]=dict(format=_FORMAT,migration_id=migration_id,local_id=local,native_id='',uuid='',path='',
            scope=data['scope'],title=titles.get(local,''),artist=artists.get(local,''),display_artist=artists.get(local,''),
            fields=[],info=deepcopy(data['info'].get(local,{})),error='Saved curation has no current native track',
            native_file_type=None,native_analysis_flags=None)
        unresolved.append(key);counts['unresolved']+=1
    descriptor=dict(format=_FORMAT,id=migration_id,scope=data['scope'],inputs_sha256=hashlib.sha256(raw).hexdigest(),
        order=order,unresolved=unresolved,cursor=0,active=None,counts=dict(counts))
    validate(descriptor,targets)
    return descriptor,targets


def validate_static(descriptor,targets):
    """Complete immutable-plan semantics; this result alone admits no writes."""
    if (not isinstance(descriptor,dict) or descriptor.get('format')!=_FORMAT or not isinstance(targets,dict)
            or not isinstance(descriptor.get('order'),list) or not isinstance(descriptor.get('unresolved'),list)
            or len(targets)>2*_MAX_TRACKS
            or len(set(descriptor['order']+descriptor['unresolved']))!=len(descriptor['order'])+len(descriptor['unresolved'])
            or set(descriptor['order']+descriptor['unresolved'])!=set(targets)):
        raise ValueError('Invalid frozen source-truth descriptor')
    _validate_scope(descriptor.get('scope'))
    for field,length in (('id',32),('inputs_sha256',64)):
        value=descriptor.get(field)
        if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid frozen source-truth identity')
    ordered=set(descriptor['order'])
    for key,row in targets.items():
        if (type(key) is not str or len(key)!=64 or any(c not in '0123456789abcdef' for c in key)
                or not isinstance(row,dict) or row.get('format')!=_FORMAT
                or row.get('migration_id')!=descriptor.get('id') or row.get('scope')!=descriptor.get('scope')
                or any(type(row.get(field)) is not str for field in ('local_id','native_id','uuid','path','title','artist','display_artist','error'))
                or not isinstance(row.get('fields'),list) or set(row['fields'])-{'title','artist'}
                or len(set(row['fields']))!=len(row['fields'])
                or not isinstance(row.get('info'),dict)):
            raise ValueError('Frozen source-truth target identity changed')
        expected=([row['scope'],'missing',row['local_id']] if not row['native_id'] and not row['uuid'] and not row['path']
                  else [row['scope'],row['local_id'],row['native_id'],row['uuid'],row['path']])
        if key!=_sha(expected):raise ValueError('Frozen source-truth key differs from its source identity')
        if key in ordered and (not row['uuid'] or not row['native_id'] or not Path(row['path']).is_absolute() or row['error']):
            raise ValueError('Unresolved metadata target cannot enter the writer FIFO')
    return descriptor


def validate_progress(descriptor):
    """Check each current cursor/active operation, without visiting all targets."""
    if (not isinstance(descriptor,dict) or not isinstance(descriptor.get('order'),list)
            or type(descriptor.get('cursor')) is not int
            or not 0<=descriptor['cursor']<=len(descriptor['order'])):
        raise ValueError('Invalid frozen source-truth descriptor')
    active=descriptor.get('active')
    if active is not None and (not isinstance(active,dict)
            or descriptor['cursor']>=len(descriptor['order']) or descriptor['order'][descriptor['cursor']]!=active.get('target')
            or not isinstance(active.get('operations'),dict) or set(active['operations'])-{'title','artist'}):
        raise ValueError('Invalid active source-truth row pair')
    if active is not None and any(type(value) is not str or len(value)!=32 or any(c not in '0123456789abcdef' for c in value)
                                  for value in active['operations'].values()):
        raise ValueError('Invalid source-truth operation identity')
    return descriptor


def validate(descriptor,targets):
    """Public callers always receive the complete static and progress checks."""
    validate_static(descriptor,targets)
    return validate_progress(descriptor)


def _curation(document,*,detach=True):
    settings=document.get('settings',{})
    result=dict(top={key:document.get(key,{}) for key in _SCOPED},
                settings={key:settings[key] for key in _SCOPED_SETTINGS if key in settings})
    return deepcopy(result) if detach else result


def _ids(curation):
    top=curation['top'];ids=set(top['title_overrides'])|set(top['artist_overrides'])|set(top['hidden_tracks'])
    ids.update(curation['settings'].get('track_info',{}))
    for namespace in ('custom','playlist_additions','playlist_removals'):
        for values in top[namespace].values():ids.update(values)
    return ids


def _pins(library,ids):
    pins={};unbound=[]
    for local in sorted(ids):
        track=library.tracks_by_id.get(local)
        if track and track.source_uuid:
            pins[local]=dict(native_id=track.native_id,uuid=track.source_uuid,path=track.source_native_path or str(track.path))
        else:unbound.append(local)
    return pins,unbound


@dataclass(frozen=True,slots=True)
class _CurationPinIndex:
    library:object
    scope:tuple
    native:Mapping[str,tuple[str,...]]


def prepare_pin_index(library,*,rows=None):
    """Capture complete native occurrences on the unpublished readback worker.

    An explicit row iterator lets a caller fuse an existing complete pass. It
    must still visit every mapping entry once, with its exact current Track.
    The result is a readback fact, never native write or source authority.
    """
    identity=tuple(scope(library).items());count=len(library.tracks_by_id)
    native:dict[str,list[str]]={};seen=set()
    for local,track in library.tracks_by_id.items() if rows is None else rows:
        if local in seen or library.tracks_by_id.get(local) is not track:
            raise ValueError('Curation readback inventory changed while preparing its pins')
        seen.add(local)
        if track.source_uuid:native.setdefault(track.native_id,[]).append(track.source_uuid)
    if len(seen)!=count or len(library.tracks_by_id)!=count or tuple(scope(library).items())!=identity:
        raise ValueError('Curation readback inventory is incomplete or changed')
    return _CurationPinIndex(library,identity,
        MappingProxyType({key:tuple(values) for key,values in native.items()}))


def _verify_pins(header,library,pin_index=None):
    native:Mapping[str,Sequence[str]]
    if pin_index is None:
        captured:dict[str,list[str]]={}
        for track in library.tracks_by_id.values():
            if track.source_uuid:captured.setdefault(track.native_id,[]).append(track.source_uuid)
        native=captured
    else:
        if (type(pin_index) is not _CurationPinIndex or pin_index.library is not library
                or pin_index.scope!=tuple(scope(library).items())):
            raise ValueError('Prepared curation pins belong to another readback or source')
        native=pin_index.native
    for local,pin in header['pins'].items():
        occupied=library.tracks_by_id.get(local)
        if occupied is not None and (occupied.native_id,occupied.source_uuid)!=(pin['native_id'],pin['uuid']):
            raise ValueError('A logical track ID with saved curation now refers to another native identity')
        matches=native.get(pin['native_id'],[])
        if matches and (len(matches)!=1 or matches[0]!=pin['uuid']):
            raise ValueError('A native identity with saved curation was replaced; preserve the original source')
    for local in header['unbound']:
        if local in native or local in library.tracks_by_id and library.tracks_by_id[local].source_uuid:
            raise ValueError('An unbound saved track ID cannot adopt a newly appearing native identity')
    nodes={node['ID']:node for node in library.source_playlist_catalog.get('nodes',[]) if node.get('rb_local_deleted')==0}
    for identity,pin in header.get('playlist_pins',{}).items():
        current=nodes.get(identity)
        if current and _playlist_pin(current)!=pin:
            raise ValueError('A native playlist with saved curation was replaced or changed its smart rule')
    if set(header.get('unbound_playlists',()))&set(nodes):
        raise ValueError('An unbound playlist ID cannot adopt a newly appearing native identity')


def _playlist_pin(node):
    return dict(uuid=node['UUID'],attribute=node['Attribute'],
                rule_sha256=_sha(node.get('SmartList')) if node['Attribute']==4 else '')


def _playlist_header(library,curation,previous=None):
    ids=set(curation['top']['playlist_removals'])|set(curation['top']['playlist_additions'])
    bindings=curation['settings'].get('playlist_unification',{});expected={}
    if (isinstance(bindings,dict) and (bindings.get('source'),bindings.get('library_id'))==
            (str(library.source),library.source_library_id)):
        expected={node['id']:node['uuid'] for node in bindings.get('nodes',{}).values()}
        ids.update(expected)
    nodes={node['ID']:node for node in library.source_playlist_catalog.get('nodes',[]) if node.get('rb_local_deleted')==0}
    pins={};unbound=[]
    for identity in sorted(ids):
        node=nodes.get(identity)
        if node and node.get('UUID'):
            if identity in expected and node['UUID']!=expected[identity]:
                raise ValueError('A saved custom/native playlist binding was replaced')
            pins[identity]=_playlist_pin(node)
        elif previous and identity in previous.get('playlist_pins',{}):
            pins[identity]=deepcopy(previous['playlist_pins'][identity])
        else:unbound.append(identity)
    return dict(playlist_pins=pins,unbound_playlists=unbound)


def _refresh_pins(header,library,ids):
    pins,unbound=_pins(library,ids)
    for local,pin in header.get('pins',{}).items():
        if local in ids and local not in pins:pins[local]=deepcopy(pin)
    return pins,[local for local in unbound if local not in pins]


def _validate_header(header,*,partition=False):
    if not isinstance(header,dict):raise ValueError('Invalid saved source curation header')
    _validate_scope(header.get('scope'))
    for namespace in ('pins','playlist_pins'):
        values=header.get(namespace)
        if not isinstance(values,dict):raise ValueError('Invalid saved source curation pins')
        for identity,pin in values.items():
            fields=('native_id','uuid','path') if namespace=='pins' else ('uuid','rule_sha256')
            if type(identity) is not str or not isinstance(pin,dict) or any(type(pin.get(k)) is not str for k in fields):
                raise ValueError('Invalid saved source curation identity')
            if namespace=='playlist_pins' and type(pin.get('attribute')) is not int:
                raise ValueError('Invalid saved playlist type')
    for namespace in ('unbound','unbound_playlists'):
        if not isinstance(header.get(namespace),list) or any(type(value) is not str for value in header[namespace]):
            raise ValueError('Invalid saved unbound source identities')
    if partition:
        curation=header.get('curation')
        if (not isinstance(curation,dict) or set(curation)!={'top','settings'}
                or not isinstance(curation['top'],dict) or set(curation['top'])!=set(_SCOPED)
                or not isinstance(curation['settings'],dict)
                or any(not isinstance(value,dict) for value in curation['top'].values())):
            raise ValueError('Invalid archived source curation')


def stage_scope(document,incoming,*,previous=None,bootstrap=False,pin_index=None):
    """Pure staged state; caller commits selection+curation in ONE save.

    A new source with conflicting numeric IDs never inherits or silently clears
    another source's work. Known partitions can switch safely by exact pins.
    """
    incoming_scope=scope(incoming);current=document.get('settings',{}).get(CURATION)
    result=dict(document);result['settings']=dict(document.get('settings',{}))
    # Same-source refresh only reads these maps to select fresh identity pins.
    # Detach them only when they actually become an archived partition below.
    curation=_curation(document,detach=False)
    if current is None:
        populated=any(curation['top'].values()) or any(curation['settings'].values())
        if populated and not bootstrap and (previous is None or scope(previous)!=incoming_scope):
            raise ValueError('Existing curation must be bound to its known original source before switching')
        basis=previous if previous is not None and scope(previous)==incoming_scope else incoming
        pins,unbound=_pins(basis,_ids(curation));header=dict(scope=incoming_scope,pins=pins,unbound=unbound,
                                                         **_playlist_header(basis,curation))
        _verify_pins(header,incoming,pin_index)
        result['settings'][CURATION]=dict(format=_FORMAT,active=header,partitions={})
        return result
    if (not isinstance(current,dict) or set(current)!={'format','active','partitions'} or current['format']!=_FORMAT
            or not isinstance(current['partitions'],dict) or len(current['partitions'])>16):
        raise ValueError('Saved source curation partitions are invalid; preserve them')
    _validate_header(current['active'])
    for identity,partition in current['partitions'].items():
        _validate_header(partition,partition=True)
        if identity!=scope_key(partition['scope']):raise ValueError('Saved source partition key changed')
    state=deepcopy(current);active=state['active']
    if active['scope']==incoming_scope:
        _verify_pins(active,incoming,pin_index)
        pins,unbound=_refresh_pins(active,incoming,_ids(curation));active.update(pins=pins,unbound=unbound)
        active.update(_playlist_header(incoming,curation,active))
    else:
        if previous is None or scope(previous)!=active['scope']:
            raise ValueError('The previous source is unavailable; curation cannot be retargeted')
        _verify_pins(active,previous)
        pins,unbound=_refresh_pins(active,previous,_ids(curation))
        state['partitions'][scope_key(active['scope'])]=dict(scope=active['scope'],pins=pins,unbound=unbound,
            **_playlist_header(previous,curation,active),curation=deepcopy(curation))
        existing=state['partitions'].pop(scope_key(incoming_scope),None)
        if existing is None:
            incoming_ids={track.native_id for track in incoming.tracks_by_id.values()}
            if incoming_ids&({pin['native_id'] for pin in pins.values()}|set(unbound)):
                raise ValueError('Another source reuses saved native track IDs; the original curation is retained')
            incoming_playlists={node['ID'] for node in incoming.source_playlist_catalog.get('nodes',[])}
            old_playlists=set(state['partitions'][scope_key(active['scope'])]['playlist_pins'])
            if incoming_playlists&(old_playlists|set(active.get('unbound_playlists',()))):
                raise ValueError('Another source reuses saved native playlist IDs; the original curation is retained')
            empty:dict[str,dict]=dict(top={key:{} for key in _SCOPED},settings={})
            existing=dict(scope=incoming_scope,pins={},unbound=[],playlist_pins={},unbound_playlists=[],curation=empty)
        if existing['scope']!=incoming_scope:raise ValueError('Saved source partition identity changed')
        _verify_pins(existing,incoming,pin_index)
        for key,value in existing['curation']['top'].items():result[key]=deepcopy(value)
        for key in _SCOPED_SETTINGS:result['settings'].pop(key,None)
        result['settings'].update(deepcopy(existing['curation']['settings']))
        state['active']={key:deepcopy(existing[key]) for key in ('scope','pins','unbound','playlist_pins','unbound_playlists')}
    result['settings'][CURATION]=state
    return result
