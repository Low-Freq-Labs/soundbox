"""Pure Collection-removal contract measured from native November1 references.

No source publisher or authority. Models retained history/tombstones, local rows,
playlist/history gap closure and XML clocks; never purges unrelated metadata.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import time

from . import rekordbox_playlist_xml as xml
from .rekordbox_playlist_plan import local as local_playlist
from .native_registration_plan import _native_id,_uuid
from .rekordbox_write import WriteProofError,ConflictError

TABLES=('djmdContent','contentFile','contentCue','djmdCue','djmdSongPlaylist',
        'djmdSongHistory','djmdSongMyTag','djmdMixerParam','agentRegistry')
UNSUPPORTED=('djmdActiveCensor','contentActiveCensor','djmdSongHotCueBanklist',
             'djmdSongRelatedTracks','djmdSongSampler')


def collection_selection(library,ids):
    """Detach active-source identity pins; never inspect the audio filesystem."""
    if (library is None or library.source_kind!='rekordbox-db' or not library.source_library_id
            or not Path(str(library.source)).is_absolute() or not isinstance(ids,(list,tuple))
            or not 1<=len(ids)<=10000 or any(type(tid) is not str or not tid for tid in ids)
            or len(set(ids))!=len(ids)):
        raise WriteProofError('Select exact active Collection songs from the verified native library')
    entries=[];native_ids=set();uuids=set()
    for tid in ids:
        track=library.tracks_by_id.get(tid)
        if (track is None or track.track_id!=tid or tid in library.source_binding_issues
                or not track.source_uuid or track.native_id in native_ids or track.source_uuid in uuids):
            raise WriteProofError('Refresh the exact Collection identities before removal')
        _native_id(track.native_id);_uuid(track.source_uuid)
        native_ids.add(track.native_id);uuids.add(track.source_uuid)
        path=track.source_native_path or str(track.path)
        if not Path(path).is_absolute():raise WriteProofError('An exact absolute native path is required')
        entries.append(dict(id=tid,native_id=track.native_id,native=dict(uuid=track.source_uuid),
            path=path,local_path=str(track.path),display=dict(title=track.name,artist=track.artist)))
    return dict(source=str(library.source),library_id=library.source_library_id,entries=entries)


def _active(row):
    if type(row.get('rb_local_deleted')) is not int or row['rb_local_deleted'] not in (0,1):
        raise WriteProofError('Unknown native removal state')
    return not row['rb_local_deleted']


def _profile(row):
    if (row.get('rb_data_status')!=0 or row.get('rb_local_data_status')!=0 or row.get('usn') is not None
            or type(row.get('rb_local_synced')) is not int or row['rb_local_synced'] not in (0,1)):
        raise WriteProofError('Native removal does not support this sync state')


def build(tables,targets,before_xml,*,stamps=None,xml_clocks=None,timestamp=None,compact=False):
    """Return ordered exact row changes and retained-byte XML, without SQL/I/O."""
    work=deepcopy(tables);changes: list[dict]=[];stamps=stamps or {};xml_clocks=xml_clocks or {};indices: dict[tuple[str,str],int]={}
    counter=next((r for r in work['agentRegistry'] if r['registry_id']=='localUpdateCount'),None)
    if not counter or type(counter['int_1']) is not int or not 0<=counter['int_1']<2**63-1000000:
        raise WriteProofError('Unknown native removal counter')
    initial=deepcopy(counter);clock=counter['int_1'];seen=set();affected=[];after_xml=before_xml
    now=timestamp or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'
    used_clocks: dict[str,list[int]]={}
    def stamp(table,identity):return stamps.get(table,{}).get(identity,now)
    def update(table,row,after):
        key=(table,row['ID'])
        if compact and key in indices:
            changes[indices[key]]['after']=deepcopy(after)
        else:
            indices[key]=len(changes)
            changes.append(dict(table=table,id=row['ID'],before=deepcopy(row),after=deepcopy(after)))
        if after is None:work[table].remove(row)
        else:row.clear();row.update(after)
    def remove(table,rows,usn):
        for row in rows:
            _profile(row)
            if row['rb_local_synced']==0:update(table,row,None)
            else:update(table,row,{**row,'rb_local_deleted':1,'rb_data_status':2,'rb_local_usn':usn,
                                   'updated_at':stamp(table,row['ID'])})
    def related(table,tid):return [r for r in work.get(table,[]) if str(r.get('ContentID'))==tid and _active(r)]
    def close_gaps(table,parent_field,removed):
        nonlocal clock,after_xml
        parents=list(dict.fromkeys(r[parent_field] for r in removed))
        for parent in parents:
            positions=[r['TrackNo'] for r in removed if r[parent_field]==parent]
            if any(type(n) is not int or n<=0 for n in positions) or len(positions)!=len(set(positions)):
                raise WriteProofError('Ambiguous native membership positions')
            survivors=[r for r in work[table] if r[parent_field]==parent and _active(r)]
            nums=[r['TrackNo'] for r in survivors]
            if any(type(n) is not int or n<=0 for n in nums) or len(nums)!=len(set(nums)):
                raise WriteProofError('Unverified native membership ordering')
            shifted=[(row,row['TrackNo']-sum(p<row['TrackNo'] for p in positions)) for row in survivors
                     if any(p<row['TrackNo'] for p in positions)]
            if shifted:
                clock+=1
                for row,position in shifted:
                    _profile(row)
                    update(table,row,{**row,'TrackNo':position,'rb_local_usn':clock,'updated_at':stamp(table,row['ID'])})
            if table=='djmdSongPlaylist':
                _native_id(parent)
                # Native keeps active-looking membership remnants below deleted
                # playlists, whose XML node was already removed. Dispose the
                # selected song's remnants and close native positions, preserving
                # that historical playlist row and the current live XML exactly.
                playlist_rows=[r for r in work.get('djmdPlaylist',[]) if r['ID']==parent]
                if not playlist_rows or (len(playlist_rows)==1 and playlist_rows[0].get('rb_local_deleted')==1):
                    continue
                if int(parent) not in xml.identities(after_xml):
                    # Same proven legacy-local XML admission as ordinary native
                    # playlist membership saves: preserve every retained token,
                    # append just this verified local playlist's missing node.
                    if len(playlist_rows)!=1:raise WriteProofError('Ambiguous native playlist identity')
                    node=playlist_rows[0];local_playlist(node)
                    if node['rb_local_synced']!=1 or node['Attribute']!=0:
                        raise WriteProofError('Expected native playlist XML entry disappeared')
                    after_xml=xml.append_node(after_xml,parent,node['ParentID'],node['Attribute'])
                attributes,_=xml._target(after_xml,parent)
                seed=xml_clocks.get(parent)
                if isinstance(seed,list):
                    index=len(used_clocks.get(parent,[]))
                    if index>=len(seed):raise WriteProofError('Incomplete removal XML clock sequence')
                    when=seed[index]
                else:when=seed if seed is not None else max(int(time.time()*1000),int(attributes['Timestamp'])+1)
                used_clocks.setdefault(parent,[]).append(when)
                after_xml=xml.touch(after_xml,parent,when)
                if parent not in affected:affected.append(parent)
    for target in targets:
        tid=target['id'];_native_id(tid);_uuid(target['uuid'])
        if tid in seen:raise WriteProofError('Duplicate native removal target')
        seen.add(tid)
        matches=[r for r in work['djmdContent'] if r['ID']==tid]
        if len(matches)!=1 or matches[0]['UUID']!=target['uuid'] or not _active(matches[0]):
            raise ConflictError('Collection removal target disappeared or changed')
        track=matches[0]
        if track['FolderPath']!=target['path']:raise ConflictError('Collection removal path changed')
        for table in UNSUPPORTED:
            if related(table,tid):raise WriteProofError('Native removal has unaccepted linked metadata: '+table)
        cues=related('djmdCue',tid)
        # Cue row deletion was observed locally (no transmitted cue records).
        if any(r.get('rb_local_synced')!=0 for r in cues):
            raise WriteProofError('Native removal has an unaccepted cue sync state')
        playlist=deepcopy(related('djmdSongPlaylist',tid));history=deepcopy(related('djmdSongHistory',tid))
        clock+=1;remove('djmdContent',[track],clock)
        clock+=1;remove('contentFile',related('contentFile',tid),clock)
        clock+=1;remove('contentCue',related('contentCue',tid),clock)
        if cues:clock+=1;remove('djmdCue',cues,clock)
        # Native reserves these relationship clocks even when the unsupported
        # relations are empty. Both captured profiles retain the same gaps.
        clock+=1
        clock+=1;remove('djmdSongPlaylist',related('djmdSongPlaylist',tid),clock)
        clock+=1;remove('djmdSongHistory',related('djmdSongHistory',tid),clock)
        clock+=1;remove('djmdSongMyTag',related('djmdSongMyTag',tid),clock)
        clock+=1
        clock+=1;remove('djmdMixerParam',related('djmdMixerParam',tid),clock)
        clock+=1
        close_gaps('djmdSongPlaylist','PlaylistID',playlist)
        close_gaps('djmdSongHistory','HistoryID',history)
    if seen:
        changes.append(dict(table='agentRegistry',id='localUpdateCount',before=initial,after={**initial,'int_1':clock}))
    return dict(changes=changes,after_xml=after_xml,playlists=affected,counter=clock,timestamp=now,xml_clocks=used_clocks)
