"""Frozen alias generations and unaliased origins, without source/file access.

An origin is an exact song/source reference, not permission to overwrite a
different native artist. Only acknowledged or still-queued own values bridge
the original automatic artist through native title/artist publication.
"""
from copy import deepcopy
from collections import Counter
import json
from uuid import uuid4

from . import metadata,metadata_provenance as provenance,source_truth as truth
from .paths import decode_location

NEXT='source_truth_alias_next'
TARGETS='source_truth_alias_targets'
ORIGINS='source_truth_artist_origins'
FORMAT='soundbox-source-alias-v1'


def key(scope,track):
    return truth._sha([scope,track['local_id'],track['native_id'],track['uuid'],track['path']])


def validate_origins(origins):
    if not isinstance(origins,dict) or len(origins)>2*truth._MAX_TRACKS:raise ValueError('Invalid saved artist origins')
    for identity,row in origins.items():
        required={'scope','local_id','native_id','uuid','path','automatic','native_artist','automatic_title','native_title'}
        if (not isinstance(row,dict) or not required<=set(row) or set(row)-required-{'title_shield','provenance'}
                or any(type(row.get(field)) is not str for field in ('local_id','native_id','uuid','path','automatic','native_artist',
                                                                   'automatic_title','native_title'))
                or type(row.get('title_shield','')) is not str):
            raise ValueError('Invalid saved artist origin')
        truth._validate_scope(row['scope'])
        if 'provenance' in row:provenance.validate(row['provenance'])
        if identity!=key(row['scope'],row):raise ValueError('Artist origin identity changed')


def relocated_origins(scope,tracks,existing):
    """Move one exact same-row origin across an acknowledged native relocation.

    This transfers undo provenance only. Requests, receipts and their paths are
    never arguments or results. Both native fields must still match our ACK.
    """
    validate_origins(existing)
    native_ids=Counter(track['native_id'] for track in tracks)
    uuids=Counter(track['uuid'] for track in tracks)
    candidates:dict[tuple[str,str,str],list[tuple[str,dict]]]={}
    for identity,row in existing.items():
        if row['scope']==scope:
            reference=(row['local_id'],row['native_id'],row['uuid'])
            candidates.setdefault(reference,[]).append((identity,row))
    result=existing
    for track in tracks:
        identity=key(scope,track)
        if (identity in existing or not track['uuid'] or track.get('issue')
                or native_ids[track['native_id']]!=1 or uuids[track['uuid']]!=1):continue
        matches=candidates.get((track['local_id'],track['native_id'],track['uuid']),[])
        if len(matches)!=1:continue
        prior_key,prior=matches[0]
        if (track['name'],track['artist'])!=(prior['native_title'],prior['native_artist']):continue
        if result is existing:result=dict(existing)
        result.pop(prior_key)
        result[identity]={**prior,'path':track['path']}
    validate_origins(result)
    return result


def library_origins(library,existing):
    tracks=[dict(local_id=track.track_id,native_id=track.native_id,uuid=track.source_uuid,
        path=track.source_native_path or str(track.path),name=track.name,artist=track.artist,
        issue=library.source_binding_issues.get(track.track_id,'')) for track in library.tracks_by_id.values()]
    return relocated_origins(truth.scope(library),tracks,existing)


def artist_pins(library,local_ids):
    """Scalar identities for an existing artist-acceptance dialog, before exec."""
    scope=truth.scope(library)
    if not isinstance(local_ids,(list,tuple)) or len(local_ids)>truth._MAX_TRACKS:
        raise ValueError('Invalid artist selection')
    if any(type(value) is not str or not value for value in local_ids) or len(set(local_ids))!=len(local_ids):
        raise ValueError('Ambiguous artist selection')
    native_ids=Counter(track.native_id for track in library.tracks_by_id.values())
    uuids=Counter(track.source_uuid for track in library.tracks_by_id.values() if track.source_uuid)
    tracks={}
    for identity in local_ids:
        track=library.tracks_by_id.get(identity)
        if (track is None or track.track_id!=identity or identity in library.source_binding_issues
                or native_ids[track.native_id]!=1 or track.source_uuid and uuids[track.source_uuid]!=1):
            raise ValueError('Refresh the artist selection before accepting changes')
        tracks[identity]=dict(native_id=track.native_id,uuid=track.source_uuid,
            path=track.source_native_path or str(track.path),local_path=str(track.path))
    return dict(scope=scope,tracks=tracks)


def title_bindings(library,*,allow_drafts=False):
    """Current unambiguous native rows eligible for local title protection."""
    if library is None:return set()
    tracks=list(library.tracks_by_id.values());scope=truth.scope(library)
    local_ids=Counter(track.track_id for track in tracks)
    native_ids=Counter(track.native_id for track in tracks)
    uuids=Counter(track.source_uuid for track in tracks)
    return {key(scope,dict(local_id=track.track_id,native_id=track.native_id,uuid=track.source_uuid,
        path=track.source_native_path or str(track.path))) for track in tracks
        if (track.source_uuid and uuids[track.source_uuid]==1 or allow_drafts and not track.source_uuid)
        and local_ids[track.track_id]==1 and native_ids[track.native_id]==1
        and track.track_id not in library.source_binding_issues}


def capture(library,*,titles,artists,aliases,settings,visible,owned,previous,owned_titles=None,origin_artists=None,
            changed_artists=None,policy=False):
    data=json.loads(truth.capture(library,titles=titles,artists=artists,aliases=aliases,settings=settings))
    origins=relocated_origins(data['scope'],data['tracks'],settings.get(ORIGINS,{}))
    result=dict(payload=data,origins=origins,visible=visible,owned=owned,previous=previous,owned_titles=owned_titles or {},
                origin_artists=data['artists'] if origin_artists is None else origin_artists,changed_artists=changed_artists,
                policy=policy)
    raw=truth._encode(result)
    if len(raw)>truth._MAX_BYTES:raise ValueError('Frozen alias intentions exceed the metadata safety bound')
    return raw


def origins(payload,existing,owned,owned_titles=None):
    """Derive automatic artists before aliases or destructive title cleanup."""
    existing=relocated_origins(payload['scope'],payload['tracks'],existing)
    scope=payload['scope'];bases=[];trusted=[];reusable={}
    for track in payload['tracks']:
        identity=key(scope,track);prior=existing.get(identity)
        if prior and track['artist'] in [prior['native_artist'],*owned.get(identity,[])]:
            reusable[identity]=prior['automatic'];base=prior['automatic']
        else:base=metadata.derive_artist(track['name'],track['artist'])[0]
        bases.append(base)
        if track['artist']:trusted.append(reusable.get(identity,track['artist']))
    trusted.extend(value for value in payload['artists'].values() if value.strip())
    registry=metadata.build_registry(bases,trusted)
    display_registry=metadata.build_registry(bases,trusted,payload['aliases'])
    junk=metadata.junk_patterns(payload['rules_off'],payload['words'])
    result=deepcopy(existing)
    for track in payload['tracks']:
        identity=key(scope,track)
        automatic=reusable.get(identity)
        if automatic is None:
            automatic=metadata.effective_artist(track['local_id'],payload['titles'].get(track['local_id']) or track['name'],
                track['artist'],registry=registry,stem=decode_location(track['location_uri']).stem)
            retained=payload.get('retained',{}).get(identity)
            if retained and not track['artist'] and automatic==metadata.UNKNOWN_ARTIST:
                automatic=retained
        prior=existing.get(identity)
        shown=payload.get('retained',{}).get(identity) or metadata.effective_artist(track['local_id'],
            payload['titles'].get(track['local_id']) or track['name'],track['artist'],payload['artists'],
            registry=display_registry,stem=decode_location(track['location_uri']).stem)
        shown=payload['info'].get(track['local_id'],{}).get('artist',shown)
        automatic_title=(prior['automatic_title'] if prior and track['name'] in
            [prior['native_title'],*(owned_titles or {}).get(identity,[])] else
            metadata.effective_title(track['local_id'],track['name'],shown,junk=junk))
        result[identity]=dict(scope=scope,**{field:track[field] for field in ('local_id','native_id','uuid','path')},
                              automatic=automatic,native_artist=track['artist'],
                              automatic_title=automatic_title,native_title=track['name'],
                              title_shield=(prior.get('title_shield','') if prior and
                                  payload['titles'].get(track['local_id'])==prior.get('title_shield') else ''))
        if prior and 'provenance' in prior:result[identity]['provenance']=prior['provenance']
        elif not prior:
            result[identity]=provenance.seed(result[identity],title=track['name'],artist=track['artist'],display_artist=shown)
    validate_origins(result)
    return result


def build(raw,*,migration_id=None):
    if type(raw) is not bytes or len(raw)>truth._MAX_BYTES:raise ValueError('Invalid frozen alias inputs')
    captured=json.loads(raw);data=deepcopy(captured['payload']);scope=data['scope']
    data['retained']={identity:row['artist'] for identity,row in captured['visible'].items()}
    origin_data={**data,'artists':captured.get('origin_artists',data['artists'])}
    provenance_rows=origins(origin_data,captured['origins'],captured['owned'],captured.get('owned_titles',{}))
    for track in data['tracks']:
        prior=provenance_rows[key(scope,track)]
        for field,values in (('title',data['titles']),('artist',data['artists'])):
            retained=provenance.manual(prior,field)
            if retained is not None:values.setdefault(track['local_id'],retained)
    if captured.get('policy'):
        derivation={};junk=metadata.junk_patterns(data['rules_off'],data['words'])
        for track in data['tracks']:
            identity=key(scope,track);prior=provenance_rows[identity];recipe=prior.get('provenance')
            if recipe is None:continue
            # Only known own values may bridge back to original raw inputs.
            # Independent source changes must already be reconciled, or use
            # the freshly observed field instead of stale derivation evidence.
            title_known=track['name'] in [prior['native_title'],*captured.get('owned_titles',{}).get(identity,[])]
            artist_known=track['artist'] in [prior['native_artist'],*captured['owned'].get(identity,[])]
            derived=dict(name=recipe['raw']['title'] if title_known else track['name'],
                         artist=recipe['raw']['artist'] if artist_known else track['artist'])
            if title_known:derived['title_base']=recipe['title_base']
            derivation[identity]=derived
            if data['titles'].get(track['local_id'])==prior.get('title_shield'):
                data['titles'].pop(track['local_id'],None)
            automatic_title=metadata.tidy_title(derived['title_base'],junk) \
                if 'title_base' in derived else metadata.effective_title(track['local_id'],derived['name'],prior['automatic'],
                    junk=junk)
            provenance_rows[identity]={**prior,'automatic_title':automatic_title}
        data['derivation']=derivation
        descriptor,targets=truth.build(truth._encode(data),migration_id=migration_id or uuid4().hex,include_equal=True)
        for identity,row in targets.items():
            if identity in provenance_rows:
                row['curation']={field:provenance.snapshot(provenance_rows[identity],field,
                    captured['payload']['titles' if field=='title' else 'artists'],
                    captured['payload']['info'].get(row['local_id'],{}).get(field)) for field in ('title','artist')}
        _carry_previous(captured,data,descriptor,targets)
        return descriptor,targets,provenance_rows
    # Raw NAME derivation must remain NAME derivation: new trusted artists can
    # correct a reversed title elsewhere. Substitute an automatic tag only for
    # an exact known own publication, or an own pending artist after title loss.
    tags={}
    derivation={}
    for track in data['tracks']:
        identity=key(scope,track);prior=captured['origins'].get(identity)
        owned=captured['owned'].get(identity,[])
        known=prior is not None and track['artist'] in [prior['native_artist'],*owned]
        automatic=provenance_rows[identity]['automatic']
        if prior and 'provenance' in prior:
            name,artist,_manual=provenance.registry_inputs(track,scope,prior)
            tags[identity]=artist;derivation[identity]=dict(name=name,artist=artist)
        else:
            tags[identity]=(automatic if (known and track['artist'] and track['artist']!=automatic
                                         or not track['artist'] and owned) else track['artist'])
    bases=[metadata.derive_artist(derivation.get(key(scope,track),track)['name'],tags[key(scope,track)])[0]
           for track in data['tracks']]
    trusted=[value for value in tags.values() if value]+list(data['artists'].values())
    registry=metadata.build_registry(bases,trusted,data['aliases'])
    automatic_registry=metadata.build_registry(bases,trusted)
    desired={track['local_id']:metadata.effective_artist(track['local_id'],data['titles'].get(track['local_id']) or
        derivation.get(key(scope,track),track)['name'],
        tags[key(scope,track)],data['artists'],registry=registry,stem=decode_location(track['location_uri']).stem)
        for track in data['tracks']}
    manual_locals={local for local,value in data['artists'].items() if value.strip()}
    data['artists']=desired
    data['derivation']=derivation
    junk=metadata.junk_patterns(data['rules_off'],data['words'])
    for track in data['tracks']:
        identity=key(scope,track);prior=provenance_rows[identity]
        raw_track=derivation.get(identity,track)
        automatic=metadata.effective_artist(track['local_id'],raw_track['name'],tags[identity],
            registry=automatic_registry,stem=decode_location(track['location_uri']).stem)
        provenance_rows[identity]={**prior,'automatic':prior['automatic'] if track['local_id'] in manual_locals else automatic}
        retained=captured['visible'].get(identity)
        if (retained and captured.get('changed_artists') is not None and track['local_id'] not in captured['changed_artists']
                and desired[track['local_id']]!=retained['artist'] and 'provenance' in prior):
            base=metadata.neat_title(raw_track['name'],desired[track['local_id']])
            provenance_rows[identity].update(provenance={**prior['provenance'],'title_base':base},
                automatic_title=metadata.tidy_title(base,junk))
        if retained and (captured.get('changed_artists') is None or track['local_id'] in captured['changed_artists']
                         or desired[track['local_id']]==retained['artist']):
            data['titles'][track['local_id']]=retained['title']
    descriptor,targets=truth.build(truth._encode(data),migration_id=migration_id or uuid4().hex,include_equal=True)
    for identity,row in targets.items():
        if identity in provenance_rows:
            row['curation']={field:provenance.snapshot(provenance_rows[identity],field,
                captured['payload']['titles' if field=='title' else 'artists'],
                captured['payload']['info'].get(row['local_id'],{}).get(field)) for field in ('title','artist')}
        if identity in captured['owned']:
            # An admitted predecessor may still change this artist after the
            # capture. A no-op comparison now cannot discard its successor.
            if any(value!=row['artist'] for value in captured['owned'][identity]):
                row['fields']=list(dict.fromkeys([*row['fields'],'artist']))
    _carry_previous(captured,data,descriptor,targets)
    return descriptor,targets,provenance_rows


def _carry_previous(captured,data,descriptor,targets):
    present={key(data['scope'],track) for track in data['tracks']}
    previous_rows={}
    for identity,previous in captured['previous'].items():
        if 'alias_raw' in previous:
            _,rows,_=build(truth._encode(previous['alias_raw']))
            previous_rows.update(rows)
        elif 'source_raw' in previous:
            _,rows=truth.build(truth._encode(previous['source_raw']))
            previous_rows.update(rows)
        else:previous_rows[identity]=previous
    for identity,previous in previous_rows.items():
        if identity in targets or identity in present:continue
        row=deepcopy(previous);row.update(migration_id=descriptor['id'],
            error=row['error'] or 'Saved metadata identity is no longer current')
        targets[identity]=row;descriptor['unresolved'].append(identity)
    truth.validate(descriptor,targets)


def missing_inputs(payload,present):
    """Retain missing identities WITH their full frozen derivation context.

    Registry trust/canonical spelling can depend on an unrelated sibling. The
    worker derives against this original context, then retains absent rows only.
    """
    if not any(key(payload['scope'],track) not in present for track in payload['tracks']):return None
    return deepcopy(payload)


def prune(descriptor,targets):
    """Remove completed equal rows only when this unstarted generation wins."""
    descriptor=deepcopy(descriptor)
    removed={identity for identity in descriptor['order'] if not targets[identity]['fields'] and not targets[identity]['info']}
    descriptor['order']=[identity for identity in descriptor['order'] if identity not in removed]
    result={identity:row for identity,row in targets.items() if identity not in removed}
    truth.validate(descriptor,result)
    return descriptor,result


def validate_next(descriptor,targets):
    if (not isinstance(descriptor,dict) or descriptor.get('format')!=FORMAT
            or descriptor.get('state') not in ('building','ready') or type(descriptor.get('error')) is not str
            or not isinstance(descriptor.get('edits'),dict) or not isinstance(targets,dict)):
        raise ValueError('Invalid saved alias generation')
    truth._validate_scope(descriptor.get('scope'))
    for field,length in (('id',32),('inputs_sha256',64)):
        value=descriptor.get(field)
        if type(value) is not str or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid alias generation identity')
    for identity,row in descriptor['edits'].items():
        if (identity!=key(descriptor['scope'],row) or row['scope']!=descriptor['scope']
                or any(type(row.get(field)) is not str for field in ('local_id','native_id','uuid','path','title','artist','display_artist'))
                or not isinstance(row.get('fields'),list) or set(row['fields'])-{'title','artist'}):
            raise ValueError('Invalid later alias edit')
    if descriptor['state']=='building':
        record=targets.get('inputs:'+descriptor['id'])
        if (len(targets)!=1 or not isinstance(record,dict) or set(record)!={'raw'}
                or not isinstance(record['raw'],dict) or truth._sha(record['raw'])!=descriptor['inputs_sha256']
                or record['raw'].get('payload',{}).get('scope')!=descriptor['scope']):
            raise ValueError('Frozen alias inputs changed')
    else:
        plan=truth.validate(descriptor.get('plan'),targets)
        if plan['id']!=descriptor['id'] or plan['scope']!=descriptor['scope']:
            raise ValueError('Alias plan identity changed')


def apply_edits(descriptor,targets,edits):
    for identity,edit in edits.items():
        if identity not in targets:continue
        row=targets[identity]
        for field in edit['fields']:row[field]=edit[field]
        if 'artist' in edit['fields']:row['display_artist']=edit['display_artist']
        row['fields']=list(dict.fromkeys([*row['fields'],*edit['fields']]))
    truth.validate(descriptor,targets)


def merge_origins(new,captured,current):
    result={**current,**new}
    for identity,row in captured.items():
        if identity not in current or identity not in new and current.get(identity)==row:result.pop(identity,None)
    for identity,row in current.items():
        if identity not in captured:result[identity]=row;continue
        if identity in new:
            for field in ('automatic','native_artist','automatic_title','native_title','title_shield','provenance'):
                if row.get(field)!=captured[identity].get(field):result[identity]={**result[identity],field:row.get(field,'')}
    validate_origins(result)
    return result


def protect_titles(targets,titles,origins,*,current_bindings,junk=None,explicit_titles=()):
    """Redundant native-desired titles prevent a second cleanup after readback.

    Mark only generated shields; an explicit user's title map always wins.
    The native desired value is identical, so this is never a local-only edit.
    """
    protected=dict(titles);provenance=dict(origins)
    for identity,row in targets.items():
        if identity not in origins or identity not in current_bindings or row.get('error'):continue
        prior=origins[identity];local=row['local_id'];shield=prior.get('title_shield','')
        manual=(local in titles and (titles[local]!=shield or local in explicit_titles))
        wanted=''
        if not manual:
            after=metadata.effective_title(local,row['title'],row['display_artist'],junk=junk)
            if after!=row['title']:wanted=row['title'];protected[local]=wanted
            elif shield and protected.get(local)==shield:protected.pop(local)
        provenance[identity]={**prior,'title_shield':wanted}
    validate_origins(provenance)
    return protected,provenance
