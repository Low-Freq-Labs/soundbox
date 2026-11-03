"""Pure local playlist plans; native captures define counters and row lifetimes.

No SQL/publication. Cloud USNs, unknown statuses and intelligent playlists remain
outside this protocol. Historical/deleted rows survive unchanged unless selected
active local rows become native deletion markers.
"""
from copy import deepcopy
from uuid import UUID
from .rekordbox_write import _digest


def local(row):
    if (any(type(row.get(k)) is not int or row[k]!=0 for k in
            ('rb_data_status','rb_local_data_status','rb_local_deleted'))
            or type(row.get('rb_local_synced')) is not int
            or row['rb_local_synced'] not in (0,1) or row.get('usn') is not None):
        raise ValueError('Unverified native playlist sync metadata')
    if type(row.get('rb_local_usn')) is not int or not 0<=row['rb_local_usn']<2**63:
        raise ValueError('Unverified native playlist local counter')
    try:
        if str(UUID(row.get('UUID')))!=row.get('UUID'):raise ValueError('UUID')
    except (ValueError,TypeError,AttributeError) as exc:
        raise ValueError('Unverified native playlist UUID') from exc


def _counter(counter):
    if (counter.get('registry_id')!='localUpdateCount' or type(counter.get('int_1')) is not int
            or not 0<=counter['int_1']<2**63-1):
        raise ValueError('Unknown or exhausted native counter')
    return {**counter,'int_1':counter['int_1']+1}


def _name(name):
    if not isinstance(name,str) or not name.strip() or '\0' in name:
        raise ValueError('A nonempty playlist name is required')
    return name


def _ordinary(row, *, folder=False):
    local(row)
    if row.get('Attribute') not in ((0,1) if folder else (0,)) or row.get('SmartList') not in ('',None):
        raise ValueError('Only regular local playlists and folders can be managed')
    if not row.get('ID') or not row.get('UUID'):
        raise ValueError('Native playlist identity is unavailable')


def _siblings(playlists,parent):
    rows=sorted((r for r in playlists if r.get('ParentID')==parent
                 and r.get('rb_local_deleted')==0),key=lambda r:r['Seq'])
    if [r['Seq'] for r in rows]!=list(range(1,len(rows)+1)):
        raise ValueError('Native sibling order is ambiguous')
    return rows


def _base(playlist,members,counter):
    _ordinary(playlist);_counter(counter)
    if any(r.get('PlaylistID')!=playlist['ID'] for r in members):
        raise ValueError('Membership belongs to another playlist')
    if len({r['ID'] for r in members})!=len(members):
        raise ValueError('Native membership identity is ambiguous')
    rows=sorted((deepcopy(r) for r in members if r.get('rb_local_deleted')==0),key=lambda r:r['TrackNo'])
    if [r['TrackNo'] for r in rows]!=list(range(1,len(rows)+1)):
        raise ValueError('Native song order is ambiguous')
    for row in rows:local(row)
    return rows


def _changes(table,before,after):
    left={r['ID']:r for r in before};right={r['ID']:r for r in after}
    return [dict(table=table,id=key,before=deepcopy(left.get(key)),after=deepcopy(right.get(key)))
            for key in sorted(left.keys()|right.keys()) if left.get(key)!=right.get(key)]


def _result(before,members,counter,current,*,playlists_before=(),playlists_after=()):
    changes=_changes('djmdSongPlaylist',before,members)
    changes+=_changes('djmdPlaylist',playlists_before,playlists_after)
    if current!=counter:
        changes.append(dict(table='agentRegistry',id='localUpdateCount',
                            before=deepcopy(counter),after=current))
    return dict(changes=changes,members=members,counter=current,source_write_authority=False)


def create(playlists,counter,*,identity,uuid,name,timestamp,parent='root',folder=False):
    if (not isinstance(identity,str) or not identity.isascii() or not identity.isdecimal()
            or not 0<int(identity)<2**32 or str(int(identity))!=identity or str(UUID(uuid))!=uuid):
        raise ValueError('Invalid native playlist identity')
    if any(r.get('ID')==identity or r.get('UUID')==uuid for r in playlists):
        raise ValueError('Native playlist identity collides with retained history')
    if parent!='root':
        targets=[r for r in playlists if r.get('ID')==parent]
        if len(targets)!=1 or targets[0].get('Attribute')!=1:
            raise ValueError('Parent is not an active native folder')
        _ordinary(targets[0],folder=True)
    siblings=_siblings(playlists,parent);current=_counter(counter)
    row=dict(ID=identity,Seq=len(siblings)+1,Name=_name(name),ImagePath=None,
             Attribute=int(folder),ParentID=parent,SmartList=None,UUID=uuid,rb_data_status=0,
             rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None,
             rb_local_usn=current['int_1'],created_at=timestamp,updated_at=timestamp)
    return dict(changes=[dict(table='djmdPlaylist',id=identity,before=None,after=row),
                         dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current)],
                playlist=row,counter=current,source_write_authority=False)


def rename(playlist,counter,*,name,timestamp):
    _ordinary(playlist,folder=True);name=_name(name)
    if name==playlist['Name']:
        return dict(changes=[],playlist=deepcopy(playlist),counter=deepcopy(counter),source_write_authority=False)
    current=_counter(counter);after={**playlist,'Name':name,'updated_at':timestamp,'rb_local_usn':current['int_1']}
    return dict(changes=[dict(table='djmdPlaylist',id=playlist['ID'],before=deepcopy(playlist),after=after),
                         dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current)],
                playlist=after,counter=current,source_write_authority=False)


def reorder(playlists,counter,*,identity,anchor_id,after=False,timestamp,sibling_timestamp=None):
    """Compose native adjacent swaps within one existing parent.

    The November 2 native reference updates the dragged row first (+1), then
    its adjacent sibling (+2), preserving every field except Seq/local clock.
    Long moves repeat that primitive; they never infer a different multi-row
    native counter protocol or change ParentID/membership.
    """
    selected=[r for r in playlists if r.get('ID')==identity]
    anchors=[r for r in playlists if r.get('ID')==anchor_id]
    if len(selected)!=1 or len(anchors)!=1 or identity==anchor_id or type(after) is not bool:
        raise ValueError('Expected distinct exact native reorder identities')
    target,anchor=selected[0],anchors[0]
    _ordinary(target,folder=True);_ordinary(anchor,folder=True)
    if target['ParentID']!=anchor['ParentID']:
        raise ValueError('Reordering cannot transfer a playlist to another folder')
    siblings=_siblings(playlists,target['ParentID'])
    order=[r['ID'] for r in siblings];wanted=[i for i in order if i!=identity]
    wanted.insert(wanted.index(anchor_id)+int(after),identity)
    nodes=deepcopy(playlists);by_id={r['ID']:r for r in nodes}
    current=deepcopy(counter);changes: list[dict] = []
    while order.index(identity)!=wanted.index(identity):
        index=order.index(identity);step=1 if index<wanted.index(identity) else -1
        neighbor_id=order[index+step]
        old_target,old_neighbor=by_id[identity],by_id[neighbor_id]
        _ordinary(old_neighbor,folder=True)
        first=_counter(current);second=_counter(first)
        new_target={**old_target,'Seq':old_neighbor['Seq'],'updated_at':timestamp,'rb_local_usn':first['int_1']}
        new_neighbor={**old_neighbor,'Seq':old_target['Seq'],'updated_at':sibling_timestamp or timestamp,
                      'rb_local_usn':second['int_1']}
        changes.extend((dict(table='djmdPlaylist',id=identity,before=deepcopy(old_target),after=new_target),
                        dict(table='djmdPlaylist',id=neighbor_id,before=deepcopy(old_neighbor),after=new_neighbor),
                        dict(table='agentRegistry',id='localUpdateCount',before=current,after=second)))
        by_id[identity]=new_target;by_id[neighbor_id]=new_neighbor;current=second
        order[index],order[index+step]=order[index+step],order[index]
    return dict(changes=changes,playlists=[by_id[r['ID']] for r in nodes],counter=current,
                source_write_authority=False)


def append(playlist,members,counter,content,*,identity,uuid,timestamp,allow_duplicate=False):
    rows=_base(playlist,members,counter)
    if content.get('rb_local_deleted') or not content.get('ID') or not content.get('UUID'):
        raise ValueError('Native Collection track identity is unavailable')
    if not allow_duplicate and any(r['ContentID']==content['ID'] for r in rows):
        return _result(members,deepcopy(members),counter,deepcopy(counter))
    if str(UUID(identity))!=identity or str(UUID(uuid))!=uuid:
        raise ValueError('Invalid native membership identity')
    if any(r.get('ID')==identity or r.get('UUID')==uuid for r in members):
        raise ValueError('Native membership identity collides with retained history')
    current=_counter(counter)
    after=[*deepcopy(members),dict(ID=identity,PlaylistID=playlist['ID'],ContentID=content['ID'],
        TrackNo=len(rows)+1,UUID=uuid,rb_data_status=0,rb_local_data_status=0,rb_local_deleted=0,
        rb_local_synced=0,usn=None,rb_local_usn=current['int_1'],created_at=timestamp,updated_at=timestamp)]
    return _result(members,after,counter,current)


def _deleted(row,counter,timestamp):
    local(row)
    return ({**row,'rb_data_status':2,'rb_local_deleted':1,'rb_local_usn':counter['int_1'],
             'updated_at':timestamp} if row['rb_local_synced'] else None)


def remove(playlist,members,counter,membership_id,*,timestamp,survivor_timestamp=None):
    rows=_base(playlist,members,counter)
    target=next((r for r in rows if r['ID']==membership_id),None)
    if target is None:raise ValueError('Expected native membership disappeared')
    current=_counter(counter);after=deepcopy(members)
    deleted=_deleted(target,current,timestamp)
    after=[r for r in after if r['ID']!=membership_id]
    if deleted is not None:after.append(deleted)
    rows=[r for r in rows if r['ID']!=membership_id]
    if rows:
        # Native rewrites the entire surviving active set in one counter group,
        # even when removing the last song leaves every TrackNo unchanged.
        current=_counter(current)
        changed={r['ID']:{**r,'TrackNo':i,'updated_at':survivor_timestamp or timestamp,
                         'rb_local_usn':current['int_1']} for i,r in enumerate(rows,1)}
        after=[changed.get(r['ID'],r) for r in after]
    return _result(members,after,counter,current)


def delete(playlists,members,counter,*,identity,timestamp,member_timestamp=None,sibling_timestamp=None):
    targets=[r for r in playlists if r.get('ID')==identity]
    if len(targets)!=1:raise ValueError('Expected one exact native playlist identity')
    target=targets[0];_ordinary(target,folder=True)
    ids={identity}
    while True:
        extra={r['ID'] for r in playlists if r.get('ParentID') in ids and r.get('rb_local_deleted')==0}
        if extra<=ids:break
        ids|=extra
        if len(ids)>len(playlists):raise ValueError('Native folder hierarchy cycles')
    nodes=[r for r in playlists if r['ID'] in ids]
    for node in nodes:_ordinary(node,folder=True)
    selected=[r for r in members if r.get('PlaylistID') in ids and r.get('rb_local_deleted')==0]
    for item in selected:local(item)
    current=deepcopy(counter);member_after=deepcopy(members)
    legacy=bool(target['rb_local_synced'])
    # Native keeps a synced playlist tombstone before deleting its entries;
    # unsynced playlists have no retained row and delete entries first.
    # The final sibling-order group advances even when the deleted row was last.
    if legacy:
        current=_counter(current)
        removed_nodes={r['ID']:_deleted(r,current,timestamp) for r in nodes}
    if selected:
        current=_counter(current)
        removed={r['ID']:_deleted(r,current,member_timestamp or timestamp) for r in selected}
        member_after=[removed.get(r['ID'],r) for r in member_after]
        member_after=[r for r in member_after if r is not None]
    current=_counter(current)
    if not legacy:
        removed_nodes={r['ID']:_deleted(r,current,timestamp) for r in nodes}
    node_after=[]
    for old in playlists:
        if old['ID'] in ids:
            if removed_nodes[old['ID']] is not None:node_after.append(removed_nodes[old['ID']])
        elif (old.get('ParentID')==target['ParentID'] and old.get('rb_local_deleted')==0
              and old['Seq']>target['Seq']):
            local(old);node_after.append({**old,'Seq':old['Seq']-1,'updated_at':sibling_timestamp or timestamp,
                                         'rb_local_usn':current['int_1']})
        else:node_after.append(deepcopy(old))
    _siblings(playlists,target['ParentID'])
    result=_result(members,member_after,counter,current,playlists_before=playlists,playlists_after=node_after)
    result['playlists']=node_after
    return result


def revision(nodes,members):
    return _digest([sorted(nodes,key=lambda r:r['ID']),sorted(members,key=lambda r:r['ID'])])
