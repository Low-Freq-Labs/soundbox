"""Sparse metadata derivation and acknowledgement ownership; no source access.

The outer artist-origin record supplies the exact source/song identity. These
records never rebind a request or grant permission to change native metadata.
"""
from copy import deepcopy
from uuid import uuid4

from . import metadata

FIELDS=('title','artist')
FORMAT='soundbox-metadata-provenance-v1'


def copy_values(values):return dict(values)


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'format','raw','title_base','edits','acks'}
            or value['format']!=FORMAT or not isinstance(value['raw'],dict)
            or set(value['raw'])!=set(FIELDS) or any(type(v) is not str for v in value['raw'].values())
            or type(value['title_base']) is not str or not isinstance(value['edits'],dict)
            or set(value['edits'])!=set(FIELDS) or not isinstance(value['acks'],dict)
            or set(value['acks'])-set(FIELDS)):
        raise ValueError('Invalid metadata derivation provenance')
    for token in value['edits'].values():
        if type(token) is not str or token and (len(token)!=32 or any(c not in '0123456789abcdef' for c in token)):
            raise ValueError('Invalid metadata edit ownership')
    for ack in value['acks'].values():
        if (not isinstance(ack,dict) or not {'operation','value','token','manual','physical','superseded'}<=set(ack)
                or set(ack)-{'operation','value','token','manual','physical','superseded','overlay'}
                or any(type(ack[k]) is not str for k in ('operation','value','token'))
                or len(ack['operation'])!=32 or any(c not in '0123456789abcdef' for c in ack['operation'])
                or ack['manual'] is not None and type(ack['manual']) is not str
                or ack['physical'] is not None and type(ack['physical']) is not str
                or ack.get('overlay') is not None and type(ack['overlay']) is not str
                or type(ack['superseded']) is not bool):
            raise ValueError('Invalid metadata acknowledgement ownership')
        token=ack['token']
        if token and (len(token)!=32 or any(c not in '0123456789abcdef' for c in token)):
            raise ValueError('Invalid metadata acknowledgement token')
    return value


def seed(origin,*,title,artist,display_artist,cleanup=True,registry=None):
    """Capture original scalar inputs before the first SoundBox source write."""
    if 'provenance' in origin:return origin
    return {**origin,'provenance':dict(format=FORMAT,raw=dict(title=title,artist=artist),
        title_base=metadata.neat_title(metadata.cleanup_credit_pair(title,artist,registry=registry)[0],display_artist) if cleanup else title,
        edits=dict.fromkeys(FIELDS,''),acks={})}


def edit(origin,fields):
    if 'provenance' not in origin:return origin
    value=origin['provenance'];edits=dict(value['edits'])
    for field in fields:edits[field]=uuid4().hex
    return {**origin,'provenance':{**value,'edits':edits}}


def snapshot(origin,field,values,overlay=None):
    """Attach ownership to a frozen intention, never to the native request."""
    value=origin.get('provenance')
    if value is None:return None
    physical=values.get(origin['local_id'])
    selected=None if field=='title' and physical==origin.get('title_shield') else physical
    if selected is None:selected=manual(origin,field)
    return dict(token=value['edits'][field],manual=overlay if overlay is not None else selected,
                physical=physical,overlay=overlay)


def validate_snapshot(value):
    if (not isinstance(value,dict) or not {'token','manual','physical'}<=set(value)
            or set(value)-{'token','manual','physical','overlay'}
            or type(value['token']) is not str
            or value['token'] and (len(value['token'])!=32 or any(c not in '0123456789abcdef' for c in value['token']))
            or any(v is not None and type(v) is not str for k,v in value.items() if k!='token')):
        raise ValueError('Invalid metadata curation ownership')


def acknowledge(origin,job):
    """One-row update. A later local token cannot be acknowledged by this job."""
    value=origin.get('provenance');captured=job.get('curation')
    if value is None or captured is None:return origin
    validate_snapshot(captured)
    field=job['field']
    if captured['token']!=value['edits'][field]:return origin
    ack=dict(operation=job['id'],value=job['value'],**captured,superseded=False)
    return {**origin,'provenance':{**value,'acks':{**value['acks'],field:ack}}}


def retire_overlay(origin,job,info):
    """Retire only this ACK's exact older Track Info scalar; keep other fields."""
    value=origin.get('provenance');ack=value['acks'].get(job['field']) if value else None
    overlay=ack.get('overlay') if ack else None
    if (not ack or ack['operation']!=job['id'] or ack['token']!=value['edits'][job['field']]
            or overlay is None or info.get(job['local_id'],{}).get(job['field'])!=overlay):return info,False
    row=dict(info[job['local_id']]);row.pop(job['field']);result=dict(info)
    if row:result[job['local_id']]=row
    else:result.pop(job['local_id'])
    return result,True


def manual(origin,field):
    """Fulfilled manual input remains an undo/alias base, not a visible mask."""
    value=origin.get('provenance')
    if not value:return None
    ack=value['acks'].get(field)
    return (ack['manual'] if ack and not ack['superseded'] and ack['token']==value['edits'][field] else None)


def registry_inputs(row,scope,origin):
    """Exact scalar raw inputs; the caller supplies an unambiguous origin."""
    name,artist=row['name'],row['artist']
    try:
        if (not isinstance(origin,dict) or origin['scope']!=scope or any(origin[field]!=row[field]
                for field in ('local_id','native_id','uuid','path'))):return name,artist,None
        value=origin.get('provenance')
        if value is None:return name,artist,None
        validate(value)
        if any(type(origin[field]) is not str for field in ('native_title','native_artist')):return name,artist,None
    except (ValueError,TypeError,KeyError):return name,artist,None
    if name==origin['native_title']:name=value['raw']['title']
    if artist==origin['native_artist']:artist=value['raw']['artist']
    return name,artist,manual(origin,'artist') if row['artist']==origin['native_artist'] else None


def display_registry_inputs(track,scope,origin):
    return registry_inputs(dict(local_id=track.track_id,native_id=track.native_id,uuid=track.source_uuid,
        path=track.source_native_path or str(track.path),name=track.name,artist=track.artist),scope,origin)


def reconcile(origin,*,title,artist,titles,artists,owned=(),junk=None,copy_map=None,fields=FIELDS,retired_overlay=False,cleanup=True):
    """Reconcile one freshly verified exact row, preserving all unACKed work.

    Only callers holding the current identity may pass an origin. ``owned`` is
    the bounded FIFO's exact-bound (field,value) pairs, never a library scan.
    Return unchanged owners for a no-op to avoid dirtying state records.
    """
    value=origin.get('provenance')
    if value is None:return origin,titles,artists
    if copy_map is None:copy_map=lambda _field,values:copy_values(values)
    result=origin;maps={'title':titles,'artist':artists}
    for field,current in (('title',title),('artist',artist)):
        if field not in fields:continue
        ack=value['acks'].get(field)
        if (field,current) in owned:continue
        local=origin['local_id'];physical=maps[field].get(local)
        matched=bool(ack and not ack['superseded'] and ack['token']==value['edits'][field])
        can_hold=(matched and current==ack['value'] and
                  (physical==ack['physical'] and physical is not None or retired_overlay))
        natural=metadata.effective_title(local,title,artist,junk=junk,cleanup=cleanup) if can_hold and field=='title' else current
        held=(matched and current==ack['value'] and ack['manual'] is not None and
              (can_hold or physical is None)) if field=='artist' else can_hold and natural!=current
        if held and retired_overlay:
            maps[field]=copy_map(field,maps[field]);maps[field][local]=current;physical=current
            if result is origin:result=deepcopy(origin)
            result['provenance']['acks'][field]['physical']=current
        elif held and field=='artist' and physical is None:
            physical=ack['manual'];maps[field]=copy_map(field,maps[field]);maps[field][local]=physical
            if result is origin:result=deepcopy(origin)
            result['provenance']['acks'][field]['physical']=physical
        if matched and physical==ack['physical'] and physical is not None and not held:
            maps[field]=copy_map(field,maps[field]);maps[field].pop(local)
        generated=(field=='title' and physical==origin.get('title_shield') and bool(physical)
            and physical==origin['native_title'] and current!=origin['native_title']
            and not any(name==field for name,_value in owned))
        if generated:
            maps[field]=copy_map(field,maps[field]);maps[field].pop(local,None)
        if held and field=='title' and result.get('title_shield')!=physical:
            if result is origin:result=deepcopy(origin)
            result['title_shield']=physical
        if generated:
            if result is origin:result=deepcopy(origin)
            result['title_shield']=''
        if current!=origin['native_'+field]:
            if result is origin:result=deepcopy(origin)
            if ack:result['provenance']['acks'][field]['superseded']=True
            result['provenance']['raw'][field]=current
            result['native_'+field]=current
            if field=='artist':result['automatic']=metadata.derive_artist(title,current)[0] if cleanup else current
            else:
                result['provenance']['title_base']=metadata.neat_title(title,artist) if cleanup else title
                result['automatic_title']=metadata.effective_title(local,title,artist,junk=junk,cleanup=cleanup)
        if matched and field=='title' and result.get('title_shield') and physical==result['title_shield']:
            if not held and result.get('title_shield'):
                if result is origin:result=deepcopy(origin)
                result['title_shield']=''
    return result,maps['title'],maps['artist']


def derive(row,*,origin,title_override,artist_override,info,context,junk,cleanup=True):
    """One detached dirty scalar row; global registry construction is external."""
    display_registry,automatic_registry=context
    if not cleanup:
        if origin is None:
            origin=dict(scope=row['scope'],**{field:row[field] for field in ('local_id','native_id','uuid','path')},
                automatic=row['artist'],native_artist=row['artist'],automatic_title=row['name'],
                native_title=row['name'],title_shield='')
            origin=seed(origin,title=row['name'],artist=row['artist'],display_artist=row['artist'],cleanup=False)
        artist=artist_override if artist_override is not None else manual(origin,'artist')
        title=title_override if title_override!=origin.get('title_shield') else None
        if title is None:title=manual(origin,'title')
        title=title.strip() if title and title.strip() else row['name']
        artist=artist.strip() if artist and artist.strip() else row['artist']
        if display_registry is not None:artist=display_registry.explicit_alias(artist)
        title=info.get('title',title);artist=info.get('artist',artist)
        return title,'' if artist==metadata.UNKNOWN_ARTIST and artist!=row['artist'] else artist,artist,{
            **origin,'automatic':row['artist'],'automatic_title':row['name']}
    automatic=metadata.effective_artist(row['local_id'],row['name'],row['artist'],registry=automatic_registry,
                                        stem=row['stem'])
    if origin is None:
        origin=dict(scope=row['scope'],**{field:row[field] for field in ('local_id','native_id','uuid','path')},
            automatic=automatic,native_artist=row['artist'],automatic_title='',native_title=row['name'],title_shield='')
        shown=metadata.effective_artist(row['local_id'],row['name'],row['artist'],
            {row['local_id']:artist_override} if artist_override is not None else None,
            registry=display_registry,stem=row['stem'])
        origin=seed(origin,title=row['name'],artist=row['artist'],display_artist=shown,registry=display_registry)
    recipe=origin.get('provenance')
    own_title=row['name']==origin['native_title'];own_artist=row['artist']==origin['native_artist']
    raw_title=recipe['raw']['title'] if recipe and own_title else row['name']
    raw_artist=recipe['raw']['artist'] if recipe and own_artist else row['artist']
    paired_title,paired_artist=metadata.cleanup_credit_pair(raw_title,raw_artist,registry=display_registry)
    automatic=metadata.effective_artist(row['local_id'],raw_title,raw_artist,registry=automatic_registry,stem=row['stem'])
    selected_artist=artist_override if artist_override is not None else manual(origin,'artist')
    if selected_artist is not None:automatic=origin['automatic']
    shown=metadata.effective_artist(row['local_id'],raw_title,raw_artist,
        {row['local_id']:selected_artist} if selected_artist is not None else None,
        registry=display_registry,stem=row['stem'])
    selected_title=title_override if title_override!=origin.get('title_shield') else None
    if selected_title is None:selected_title=manual(origin,'title')
    base=(recipe['title_base'] if recipe and own_title and (paired_title,paired_artist)==(raw_title,raw_artist)
          else metadata.neat_title(paired_title,shown))
    if recipe and own_title and automatic!=origin['automatic'] and selected_artist is None:
        base=metadata.neat_title(paired_title,shown)
        origin={**origin,'provenance':{**recipe,'title_base':base}}
    automatic_title=metadata.canonical_title_credits(metadata.tidy_title(base,junk),display_registry)
    title=selected_title.strip() if selected_title and selected_title.strip() else automatic_title
    title=info.get('title',title);shown=info.get('artist',shown)
    origin={**origin,'automatic':automatic,'automatic_title':automatic_title}
    return title,'' if shown==metadata.UNKNOWN_ARTIST else shown,shown,origin
