"""Adopt current native catalogs without claiming another exporter's media."""
from copy import deepcopy
import tempfile
from pathlib import Path
from .cdj_usb import DATABASE_FILES,digest,run_renderer


def map_tags(source, native, same_master):
    """Resolve library-local tag IDs into the USB tree without replacing peers."""
    existing={int(t['id']):deepcopy(t) for t in native}
    mapping={0:0};pending=deepcopy(source);result=[]
    next_id=max(existing,default=0)+1
    while pending:
        ready=[t for t in pending if int(t['parent']) in mapping]
        if not ready:raise ValueError('Invalid source My Tag hierarchy')
        for tag in ready:
            old=int(tag['id']);parent=mapping[int(tag['parent'])]
            target=old if same_master and old in existing else None
            if target is None:
                matches=[i for i,t in existing.items() if t['name']==tag['name'] and int(t['parent'])==parent and t['attribute']==tag['attribute']]
                if len(matches)==1:target=matches[0]
            if target is None:
                target=old if old not in existing else next_id
                next_id=max(next_id,target+1)
            mapped=dict(tag,id=target,parent=parent)
            if target in existing and not same_master:mapped=deepcopy(existing[target])
            existing[target]=mapped;mapping[old]=target;result.append(mapped);pending.remove(tag)
    return result,mapping


def current_baseline(native, previous, db_id):
    """Read small captured catalogs only; media ownership stays independent."""
    view=deepcopy(native.catalog)
    if not view:
        with tempfile.TemporaryDirectory(prefix='soundbox-cdj-catalog-') as folder:
            root=Path(folder)
            for path,raw in native.files:
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            view=run_renderer('inspect',root)
    result=deepcopy(previous) if previous else dict(schema=1,db_id=db_id,selection=[],files={},
        track_inputs={},playlist_hints={},view={'playlists':[],'track_rows':[]},library_managed=False)
    old=result['view']
    if result['db_id'] != db_id:
        # A different desktop library is also a normal exporter. Its old
        # bindings become foreign, while its physical files stay untouched.
        result.update(selection=[],track_inputs={},playlist_hints={})
        old=dict(playlists=[],track_rows=[])
    result['db_id']=db_id
    def full_paths(playlists):
        nodes={str(p['id']):p for p in playlists}
        result={}
        for p in playlists:
            names=[p['title']];parent=str(p['parent']);seen={str(p['id'])}
            while parent!='0':
                if parent in seen or parent not in nodes:raise ValueError('Invalid USB playlist hierarchy')
                seen.add(parent);node=nodes[parent];names.insert(0,node['title']);parent=str(node['parent'])
            result[str(p['id'])]=tuple(names)
        return result
    old_paths=full_paths(old['playlists']);current_paths=full_paths(view['playlists'])
    by_path: dict[tuple, list[dict]]={}
    by_identity: dict[str, list[dict]]={}
    for playlist in view['playlists']:
        by_path.setdefault((playlist['folder'],current_paths[str(playlist['id'])]),[]).append(playlist)
        by_identity.setdefault(str(playlist['id']),[]).append(playlist)
    bindings={}
    for before in old['playlists']:
        if 'source_id' not in before:continue
        candidates=by_path.get((before['folder'],old_paths[str(before['id'])]),[])
        if not candidates and before['entries']:
            candidates=[p for p in by_identity.get(str(before['id']),[]) if p['entries']==before['entries']]
        if len(candidates)==1:bindings[str(candidates[0]['id'])]=before['source_id']
    old_tracks={(int(t['master_db_id']),int(t['source_id']),t['audio']):t for t in old['track_rows']}
    from .cdj_incremental import referenced_files
    from .cdj_usb import PROFILE_FILES
    live_references=referenced_files(view)
    metadata=set(DATABASE_FILES)|{'PIONEER/'+name for name in PROFILE_FILES}
    for path in list(result['files']):
        if path not in metadata and path not in live_references:result['files'].pop(path)
    foreign=set(result.get('foreign_tracks',[]))
    fresh_foreign=[]
    for track in view['track_rows']:
        before=old_tracks.get((int(track['master_db_id']),int(track['source_id']),track['audio']))
        if before is None or int(before['id']) in foreign:fresh_foreign.append(int(track['id']))
        changed = before is None or {k:track[k] for k in before.keys()&track.keys() if k!='id'}!={k:before[k] for k in before.keys()&track.keys() if k!='id'}
        if changed:
            result.get('track_inputs',{}).pop(str(track['source_id']),None)
            # Another exporter can replace a known record. Forget old byte
            # claims for its assets; keep its current native references intact.
            for path in referenced_files({'track_rows':[track]}):result['files'].pop(path,None)
    present_sources={str(t['source_id']) for t in view['track_rows'] if int(t['master_db_id'])==db_id}
    result['track_inputs']={key:value for key,value in result.get('track_inputs',{}).items() if key in present_sources}
    for playlist in view['playlists']:
        if playlist['id'] in bindings:playlist['source_id']=bindings[playlist['id']]
    result['view']=view
    result['foreign_tracks']=fresh_foreign
    result['foreign_playlists']=[int(p['id']) for p in view['playlists'] if 'source_id' not in p]
    result['next_track_id']=max(result.get('next_track_id',1),max([0,*[int(t['id']) for t in view['track_rows']]])+1)
    result['next_playlist_id']=max(result.get('next_playlist_id',1),max([0,*[int(p['id']) for p in view['playlists']]])+1)
    for path,raw in native.files:
        if path in DATABASE_FILES:
            result['files'][path]=dict(size=len(raw),sha256=digest(raw),owned=bool(previous))
    return result
