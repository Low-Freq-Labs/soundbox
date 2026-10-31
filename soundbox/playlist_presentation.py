"""Immediate native playlist feedback derived from durable pending intents.

The result is a UI view only. Never cache it as native source state, feed its
catalog to a writer, or export it before the existing source FIFO has drained.
"""
from copy import deepcopy
from dataclasses import replace

from .library import Playlist


def project(library, jobs):
    if not jobs or not library.source_playlist_catalog:return library
    catalog=deepcopy(library.source_playlist_catalog)
    nodes=catalog['nodes'];members=catalog['members'];base=catalog['revision']
    native_tracks={t.native_id:t for t in library.tracks_by_id.values()}
    for job in jobs:
        if (job['error'] or job['source']!=str(library.source) or job['library_id']!=library.source_library_id):break
        request=job['request']
        # A newer native snapshot may already include an interrupted commit.
        # Receipt replay/reconciliation must settle it before projecting again.
        if request['revision']!=base:break
        operation=request['operation'];identity=request['playlist_id']
        target=next((r for r in nodes if r['ID']==identity and r['rb_local_deleted']==0),None)
        if operation=='create':
            if target is not None:break
            parent=request['parent']
            if parent!='root' and not any(r['ID']==parent and r['Attribute']==1 and r['rb_local_deleted']==0 for r in nodes):break
            nodes.append(dict(ID=identity,UUID=request['playlist_uuid'],Name=request['name'],ParentID=parent,
                Seq=1+max((r['Seq'] for r in nodes if r['ParentID']==parent and r['rb_local_deleted']==0),default=0),
                Attribute=int(request['folder']),rb_local_deleted=0))
            continue
        if target is None or target['UUID']!=request['playlist_uuid']:break
        if operation=='rename':target['Name']=request['name']
        elif operation=='delete':
            deleted={identity}
            while True:
                descendants={r['ID'] for r in nodes if r['ParentID'] in deleted and r['rb_local_deleted']==0}
                if descendants<=deleted:break
                deleted|=descendants
            nodes[:]=[r for r in nodes if r['ID'] not in deleted]
            members[:]=[r for r in members if r['PlaylistID'] not in deleted]
        elif operation in ('append','remove'):
            if target['Attribute']!=0:break
            rows=sorted((r for r in members if r['PlaylistID']==identity and r['rb_local_deleted']==0),key=lambda r:r['TrackNo'])
            valid=True
            for track in request['tracks']:
                current=native_tracks.get(track['track_id'])
                if current is None or current.source_uuid!=track['uuid']:valid=False;break
            if not valid:break
            for track in request['tracks']:
                if operation=='append':
                    if not request['allow_duplicate'] and any(r['ContentID']==track['track_id'] for r in rows):continue
                    rows.append(dict(ID=track['id'],UUID=track['membership_uuid'],PlaylistID=identity,
                                     ContentID=track['track_id'],TrackNo=len(rows)+1,rb_local_deleted=0))
                else:
                    match=next((r for r in rows if r['ContentID']==track['track_id']),None)
                    if match:rows.remove(match)
            members[:]=[r for r in members if r['PlaylistID']!=identity or r['rb_local_deleted']!=0]
            for number,row in enumerate(rows,1):row['TrackNo']=number
            members.extend(rows)
    active={r['ID']:r for r in nodes if r['rb_local_deleted']==0 and r['Attribute'] in (0,1,4)}
    old={p.pid:p for p in library.playlists};aliases={t.native_id:t.track_id for t in library.tracks_by_id.values()}
    children: dict[str,list] = {}
    by_playlist: dict[str,list] = {}
    for row in active.values():children.setdefault(row['ParentID'],[]).append(row)
    for row in members:
        if row['rb_local_deleted']==0:by_playlist.setdefault(row['PlaylistID'],[]).append(row)
    result=[]
    def walk(parent,path,seen):
        for row in sorted(children.get(parent,[]),key=lambda r:r['Seq']):
            if row['ID'] in seen:raise ValueError('Native presentation folder cycle')
            if row['Attribute']==1:walk(row['ID'],path+[row['Name']],seen|{row['ID']})
            elif row['Attribute']==0:
                keys=[aliases.get(r['ContentID'],r['ContentID']) for r in sorted(by_playlist.get(row['ID'],[]),key=lambda r:r['TrackNo'])]
                result.append(Playlist(row['Name'],path,keys,row['ID']))
            elif row['ID'] in old:result.append(replace(old[row['ID']],name=row['Name'],folder_path=path))
    walk('root',[],set())
    return replace(library,playlists=result,source_playlist_catalog=catalog)
