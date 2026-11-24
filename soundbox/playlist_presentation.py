"""Immediate native playlist feedback derived from durable pending intents.

The result is a UI view only. Never cache it as native source state, feed its
catalog to a writer, or export it before the existing source FIFO has drained.
"""
from dataclasses import replace
from collections import defaultdict, deque
import math

from .library import Playlist
from .rekordbox_playlist_store import command_requests,tree_bindings,CREATE_ORDINARY_OPERATIONS,CREATE_SMART_OPERATIONS
from .rekordbox_write import WriteProofError
from . import playlist_creation_placement


def copy_catalog(catalog):
    """Copy native SQL row mappings without recursively copying scalar cells.

    SQLite cells are immutable numbers/text/BLOB/None. Projection assigns only
    top-level fields; independent maps preserve the authoritative source rows.
    This remains a presentation copy and never grants publication authority.
    """
    return {**catalog,'nodes':[dict(row) for row in catalog['nodes']],
            'members':[dict(row) for row in catalog['members']]}


def project(library, jobs):
    if not jobs or not library.source_playlist_catalog:return library
    catalog=copy_catalog(library.source_playlist_catalog)
    nodes=catalog['nodes'];members=catalog['members'];base=catalog['revision']
    native_tracks={t.native_id:t for t in library.tracks_by_id.values()}
    pending_smart={}
    def place_created(job,request):
        proof=job.get('creation_placement')
        if proof is None:return True
        try:return playlist_creation_placement.project(catalog,request,proof,library.source,library.source_library_id)
        except (ValueError,KeyError,TypeError):return False
    def commands():
        for job in jobs:
            if (job['error'] or job['source']!=str(library.source) or job['library_id']!=library.source_library_id
                    or job['request']['revision']!=base):return
            if job['request']['operation']=='delete_tree':
                try:
                    if tree_bindings(catalog,job['request']['playlist_id'])!=job['request']['descendants']:return
                except (ValueError,KeyError,WriteProofError):return
            for command in command_requests(job['request']):yield {**job,'request':command}
    for job in commands():
        if (job['error'] or job['source']!=str(library.source) or job['library_id']!=library.source_library_id):break
        request=job['request']
        # A newer native snapshot may already include an interrupted commit.
        # Receipt replay/reconciliation must settle it before projecting again.
        if request['revision']!=base:break
        operation=request['operation'];identity=request['playlist_id']
        target=next((r for r in nodes if r['ID']==identity and r['rb_local_deleted']==0),None)
        if 'creation_placement' in job:
            try:
                playlist_creation_placement.validate(job['creation_placement'],request,library.source,library.source_library_id)
                if not playlist_creation_placement.anchor_matches(
                        playlist_creation_placement.anchor_from(job['creation_placement']),catalog,
                        library.source,library.source_library_id):break
            except (ValueError,KeyError,TypeError):break
        if operation in CREATE_ORDINARY_OPERATIONS:
            if target is not None:break
            parent=request['parent']
            if parent!='root' and not any(r['ID']==parent and r['Attribute']==1 and r['rb_local_deleted']==0 for r in nodes):break
            if operation!='create':
                from .rekordbox_playlist_create import positions
                if parent!='root' and next(r for r in nodes if r['ID']==parent)['UUID']!=request['parent_uuid']:break
                try:siblings=positions(nodes,parent)
                except (ValueError,KeyError,TypeError):break
                for index,sibling in enumerate(siblings,1):sibling['Seq']=index
                sequence=len(siblings)+1
            else:sequence=1+max((r['Seq'] for r in nodes if r['ParentID']==parent and r['rb_local_deleted']==0),default=0)
            nodes.append(dict(ID=identity,UUID=request['playlist_uuid'],Name=request['name'],ParentID=parent,
                Seq=sequence,
                Attribute=int(request['folder']),rb_local_deleted=0))
            if not place_created(job,request):break
            continue
        if operation in CREATE_SMART_OPERATIONS:
            if target is not None:break
            tags={(tag.tag_id,tag.source_uuid,tag.name,tag.category,tag.category_id,tag.category_uuid)
                  for tag in library.source_tags}
            if any((tag['id'],tag['uuid'],tag['name'],tag['category'],tag['category_id'],tag['category_uuid'])
                   not in tags for tag in request['tags']):break
            parent=request['parent']
            if parent!='root' and not any(row['ID']==parent and row['UUID']==request['parent_uuid']
                    and row['Attribute']==1 and row['rb_local_deleted']==0 for row in nodes):break
            siblings=sorted((row for row in nodes if row['ParentID']==parent and row['rb_local_deleted']==0),
                            key=lambda row:row['Seq'])
            if operation=='create_smart' and parent!='root':
                for row in siblings:row['Seq']+=1
                sequence=1
            else:
                if operation=='create_smart_normalized':
                    for position,row in enumerate(siblings,1):row['Seq']=position
                sequence=1+max((row['Seq'] for row in siblings),default=0)
            nodes.append(dict(ID=identity,UUID=request['playlist_uuid'],Name=request['name'],ParentID=parent,
                Seq=sequence,Attribute=4,rb_local_deleted=0))
            if not place_created(job,request):break
            selected={tag['id'] for tag in request['tags']}
            matching=[track for track in library.tracks_by_id.values() if not track.imported and track.source_uuid
                      and selected.intersection(track.source_tag_ids or ())]
            def recency(track):
                assigned=selected.intersection(track.source_tag_ids or ())
                return max((stamp for tag,stamp in track.source_tag_times.items() if tag in assigned
                    and type(stamp) in (int,float) and math.isfinite(stamp) and 0<=stamp<253402300800),default=-1)
            matching.sort(key=recency,reverse=True)
            keys=[track.track_id for track in matching]
            pending_smart[identity]=Playlist(request['name'],[],keys,identity)
            continue
        if target is None or target['UUID']!=request['playlist_uuid']:break
        if operation in {'rename_smart','reorder_smart'} and (
                target['Attribute']!=request['smart_attribute'] or target.get('SmartList')!=request['smart_rule']
                or target['ParentID']!=request['parent']):break
        if operation=='move':
            from .rekordbox_playlist_move import node_context
            try:
                placement=node_context(nodes,identity=identity,parent=request['parent'],
                    anchor_id=request['anchor_id'] or None,after=request['after'])
            except (ValueError,KeyError,TypeError):break
            if (target['Attribute']!=request['node_attribute'] or target.get('SmartList')!=request['smart_rule']
                    or target['ParentID']!=request['source_parent'] or placement['descendants']!=request['move_descendants']
                    or (placement['source_parent']['UUID'] if placement['source_parent'] else '')!=request['source_parent_uuid']
                    or (placement['parent']['UUID'] if placement['parent'] else '')!=request['parent_uuid']
                    or (placement['anchor']['UUID'] if placement['anchor'] else '')!=request['anchor_uuid']
                    or (placement['anchor']['Attribute'] if placement['anchor'] else None)!=request['anchor_attribute']):break
            if placement['changed']:
                for sibling in placement['destination_siblings']:
                    sibling['Seq']=placement['positions'][sibling['ID']]
                target['ParentID']=request['parent'];target['Seq']=placement['positions'][identity]
            continue
        if operation=='move_smart':
            destination=next((row for row in nodes if row['ID']==request['parent']
                              and row['rb_local_deleted']==0),None)
            original_parent=next((row for row in nodes if row['ID']==request['source_parent']
                                  and row['rb_local_deleted']==0),None)
            if (target['Attribute']!=4 or target.get('SmartList')!=request['smart_rule']
                    or target['ParentID']!=request['source_parent'] or request['parent']==request['source_parent']
                    or request['parent']!='root' and (destination is None or destination['Attribute']!=1
                        or destination['UUID']!=request['parent_uuid'])
                    or request['source_parent']!='root' and (original_parent is None or original_parent['Attribute']!=1
                        or original_parent['UUID']!=request['source_parent_uuid'])):break
            from .rekordbox_playlist_move import context as move_context
            try:movement=move_context(nodes,identity=identity,parent=request['parent'])
            except (ValueError,KeyError,TypeError):break
            # Native moves preserve the old parent's Seq gaps. Normalize only
            # the destination order; SQL/XML clocks remain engine-owned.
            for sibling in movement['destination_siblings']:
                sibling['Seq']=movement['positions'][sibling['ID']]
            target['ParentID']=request['parent'];target['Seq']=movement['positions'][identity]
            continue
        if operation in ('rename','rename_smart'):target['Name']=request['name']
        elif operation in ('reorder','reorder_smart'):
            anchor=next((r for r in nodes if r['ID']==request['anchor_id'] and r['rb_local_deleted']==0),None)
            if (anchor is None or anchor['UUID']!=request['anchor_uuid'] or target['ParentID']!=request['parent']
                    or anchor['ParentID']!=target['ParentID'] or target['Attribute'] not in ((0,1,4) if operation=='reorder_smart' else (0,1))
                    or anchor['Attribute'] not in ((0,1,4) if operation=='reorder_smart' else (0,1))):break
            siblings=sorted((r for r in nodes if r['ParentID']==target['ParentID'] and r['rb_local_deleted']==0),key=lambda r:r['Seq'])
            if [r['Seq'] for r in siblings]!=list(range(1,len(siblings)+1)):break
            reordered=[r for r in siblings if r['ID']!=target['ID']]
            position=next(i for i,r in enumerate(reordered) if r['ID']==anchor['ID'])+int(request['after'])
            reordered.insert(position,target)
            for position,row in enumerate(reordered,1):row['Seq']=position
        elif operation=='delete_smart':
            parent = next((row for row in nodes if row['ID'] == request['parent']), None)
            if (target['Attribute'] != 4 or target.get('SmartList') != request['smart_rule']
                    or target['ParentID'] != request['parent']
                    or request['parent'] != 'root' and
                    (parent is None or parent['UUID'] != request['parent_uuid'] or parent['Attribute'] != 1)):
                break
            # A durable smart deletion removes only this computed list. Never
            # recurse, materialize memberships or alter the verified Library.
            nodes.remove(target)
            for row in nodes:
                if (row['ParentID'] == target['ParentID'] and row['rb_local_deleted'] == 0
                        and row['Seq'] > target['Seq']):row['Seq'] -= 1
        elif operation=='delete':
            deleted={identity}
            while True:
                descendants={r['ID'] for r in nodes if r['ParentID'] in deleted and r['rb_local_deleted']==0}
                if descendants<=deleted:break
                deleted|=descendants
            nodes[:]=[r for r in nodes if r['ID'] not in deleted]
            members[:]=[r for r in members if r['PlaylistID'] not in deleted]
            for row in nodes:
                if (row['ParentID'] == target['ParentID'] and row['rb_local_deleted'] == 0
                        and row['Seq'] > target['Seq']):row['Seq'] -= 1
        elif operation in ('append','remove'):
            if target['Attribute']!=0:break
            rows=sorted((r for r in members if r['PlaylistID']==identity and r['rb_local_deleted']==0),key=lambda r:r['TrackNo'])
            valid=True
            for track in request['tracks']:
                current=native_tracks.get(track['track_id'])
                if current is None or current.source_uuid!=track['uuid']:valid=False;break
            if not valid:break
            removed = set()
            by_id = {row['ID']: row for row in rows}
            unique_ids = len(by_id) == len(rows)
            by_content: defaultdict[str, deque[dict]] = defaultdict(deque)
            if operation == 'remove':
                for row in rows:by_content[row['ContentID']].append(row)
            for track in request['tracks']:
                if operation=='append':
                    if not request['allow_duplicate'] and any(r['ContentID']==track['track_id'] for r in rows):continue
                    rows.append(dict(ID=track['id'],UUID=track['membership_uuid'],PlaylistID=identity,
                                     ContentID=track['track_id'],TrackNo=len(rows)+1,rb_local_deleted=0))
                else:
                    if not unique_ids:
                        # Preserve the prior first-match behavior for malformed
                        # duplicate-ID snapshots; never collapse their occurrences.
                        match = next((row for row in rows if row['ContentID'] == track['track_id']
                                      and ('membership_id' not in track or (row['ID'] == track['membership_id']
                                           and row['UUID'] == track['source_membership_uuid']))), None)
                        if match is None:valid=False;break
                        rows.remove(match)
                        continue
                    if 'membership_id' in track:
                        match = by_id.get(track['membership_id'])
                        if (match is not None and (match['ContentID'] != track['track_id']
                                or match['UUID'] != track['source_membership_uuid'] or match['ID'] in removed)):
                            match = None
                    else:
                        candidates = by_content[track['track_id']]
                        while candidates and candidates[0]['ID'] in removed:candidates.popleft()
                        match = candidates.popleft() if candidates else None
                    if match is None:
                        valid=False;break
                    removed.add(match['ID'])
            if not valid:break
            if removed:rows = [row for row in rows if row['ID'] not in removed]
            members[:]=[r for r in members if r['PlaylistID']!=identity or r['rb_local_deleted']!=0]
            for number,row in enumerate(rows,1):row['TrackNo']=number
            members.extend(rows)
    active={r['ID']:r for r in nodes if r['rb_local_deleted']==0 and r['Attribute'] in (0,1,4)}
    old={p.pid:p for p in library.playlists};old.update(pending_smart)
    aliases={t.native_id:t.track_id for t in library.tracks_by_id.values()}
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
