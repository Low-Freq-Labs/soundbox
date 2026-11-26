"""Pure local playlist plans; native captures define counters and row lifetimes.

No SQL/publication. Ordinary mutations exclude intelligent playlists; their
create/delete primitives have separate measured profiles below. Cloud USNs and
unknown statuses remain excluded. Historical/deleted rows survive unchanged
unless selected active local rows become native deletion markers.
"""
from copy import deepcopy
from uuid import UUID
from .rekordbox_write import _digest


_IMMUTABLE_CELLS=(str,int,float,bool,bytes,type(None))


def _flat_row(row):
    return all(type(key) is str and type(value) in _IMMUTABLE_CELLS for key,value in row.items())


def _copy_row(row):
    """Detach raw SQL cells without recursive scalar-copy dispatch.

    Snapshot before checking types. This changes allocation only, never native
    admission; nested values and custom containers retain deepcopy semantics.
    """
    if type(row) is dict:
        snapshot=dict(row)
        if _flat_row(snapshot):return snapshot
    return deepcopy(row)


def _copy_rows(rows):
    """Retain deepcopy's repeated-row aliasing and general fallback behavior."""
    if type(rows) is not list:return deepcopy(rows)
    copied={};result=[]
    for row in rows:
        if type(row) is not dict:return deepcopy(rows)
        identity=id(row)
        if identity not in copied:
            snapshot=dict(row)
            if not _flat_row(snapshot):return deepcopy(rows)
            copied[identity]=snapshot
        result.append(copied[identity])
    return result


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


def smart_tag_rule(identity, tag_ids, *, match='any'):
    """Native-shaped positive My Tag OR rule, without SQL or write authority.

    The final rule shape has a frozen native reference. Formatting grants no
    source authority; create/delete protocols require their own capabilities.
    """
    def uint32(value):
        if (type(value) is not str or not value.isascii() or not value.isdecimal()
                or not 0 < int(value) < 2**32 or str(int(value)) != value):
            raise ValueError('Expected canonical native smart-playlist identity')
        return int(value)
    number = uint32(identity)
    signed_identity = number if number < 2**31 else number - 2**32
    if match != 'any':
        raise ValueError('Only ANY/OR My Tag matching has a native creation reference')
    if isinstance(tag_ids, (str, bytes)):
        raise ValueError('Select explicit My Tag identities')
    values = tuple(tag_ids)
    if not 1 <= len(values) <= 64:
        raise ValueError('Select one to 64 distinct My Tags')
    numbers = [uint32(value) for value in values]
    if len(set(numbers)) != len(numbers):
        raise ValueError('Select one to 64 distinct My Tags')
    conditions = []
    for number in numbers:
        # Existing native smart-list reads normalize signed 32-bit tag IDs back
        # to their canonical uint32 identity. Names never enter the rule XML.
        signed = number if number < 2**31 else number - 2**32
        conditions.append('<CONDITION PropertyName="myTag" Operator="8" ValueUnit="" '
                          f'ValueLeft="{signed}" ValueRight=""/>')
    # One positive condition has identical ALL/ANY semantics. Preserve the
    # observed native single-condition wire value; multiple tags use OR.
    logical = 1 if len(values) == 1 else 2
    return (f'<NODE Id="{signed_identity}" LogicalOperator="{logical}" AutomaticUpdate="0">'
            + ''.join(conditions) + '</NODE>')


def smart_parent_reason(playlists, parent='root'):
    """Pure placement support diagnostic, separate from source permission."""
    if not isinstance(playlists, (list, tuple)) or any(not isinstance(row, dict) for row in playlists):
        return 'Native smart-playlist placement metadata is unavailable'
    if parent == 'root':
        return ''
    parents = [row for row in playlists if row.get('ID') == parent]
    if len(parents) != 1:
        return 'The smart-playlist folder is unavailable or ambiguous'
    try:
        _ordinary(parents[0], folder=True)
        if parents[0].get('Attribute') != 1:
            raise ValueError('Folder')
        siblings = _siblings(playlists, parent)
        if not siblings:
            return 'Smart creation in an empty folder needs its native placement reference'
        if len(siblings) > 4096:
            return 'Smart creation would exceed the bounded folder placement scope'
        for sibling in siblings:
            local(sibling)
            if sibling.get('Attribute') != 4:
                return 'Smart creation in a mixed folder needs its native placement reference'
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'This smart-playlist folder has unverified native placement metadata'
    return ''


def smart_delete_context(playlists, members, counter, *, identity, uuid, parent, parent_uuid, rule):
    """Capture exact read-only deletion inputs; no native effects are inferred.

    Creation proof does not establish deletion counters, row lifetime, sibling
    clocks or XML retention. The opaque rule is pinned without evaluating it.
    """
    matches = [row for row in playlists if row.get('ID') == identity]
    if len(matches) != 1:
        raise ValueError('Smart playlist disappeared or is ambiguous')
    target = matches[0]
    local(target)
    if (target.get('Attribute') != 4 or target.get('UUID') != uuid
            or target.get('ParentID') != parent or target.get('SmartList') != rule
            or rule is not None and (type(rule) is not str or '\0' in rule)):
        raise ValueError('Smart playlist changed before deletion')
    if sum(row.get('UUID') == uuid for row in playlists) != 1:
        raise ValueError('Smart playlist UUID is ambiguous')
    _counter(counter)
    if target['rb_local_usn'] > counter['int_1']:
        raise ValueError('Smart playlist clock exceeds its source counter')
    if any(row.get('PlaylistID') == identity for row in members):
        raise ValueError('Smart playlist has unverified stored membership history')
    if any(row.get('ParentID') == identity for row in playlists):
        raise ValueError('Smart playlist unexpectedly contains child playlists')
    folder = None
    if parent == 'root':
        if parent_uuid != '':
            raise ValueError('Root smart playlist has no parent UUID')
    else:
        parents = [row for row in playlists if row.get('ID') == parent]
        if len(parents) != 1 or parents[0].get('UUID') != parent_uuid:
            raise ValueError('Smart playlist parent changed before deletion')
        folder = parents[0]
        _ordinary(folder, folder=True)
        if folder.get('Attribute') != 1 or folder['rb_local_usn'] > counter['int_1']:
            raise ValueError('Smart playlist parent is not a verified local folder')
    siblings = _siblings(playlists, parent)
    return dict(target=deepcopy(target), parent=deepcopy(folder), siblings=deepcopy(siblings),
                counter=deepcopy(counter), source_write_authority=False)


def smart_delete_reason(playlists, identity):
    """Bound the observed active local target and following-sibling envelope."""
    targets = [row for row in playlists if row.get('ID') == identity]
    if len(targets) != 1:
        return 'Smart playlist is missing or ambiguous'
    target = targets[0]
    try:
        local(target)
        if (target.get('Attribute') != 4 or type(target.get('SmartList')) is not str or not target['SmartList']):
            return 'This smart playlist needs a separate native deletion reference'
        siblings = _siblings(playlists, target['ParentID'])
        following = [row for row in siblings if row['Seq'] > target['Seq']]
        if len(following) > 4096:
            return 'Smart deletion exceeds its bounded sibling scope'
        for sibling in following:
            local(sibling)
            if sibling.get('Attribute') not in (0, 1, 4):
                return 'Deleting beside this playlist type needs a native reference'
    except (ValueError, TypeError, KeyError):
        return 'Smart playlist deletion metadata is unverified'
    return ''


def delete_smart(playlists, members, counter, *, identity, uuid, parent, parent_uuid, rule,
                 sibling_timestamps, timestamp=None):
    """Observed local hard-delete or synced tombstone, then sibling compaction.

    Unsynced targets and survivors share C+1. Synced targets retain their native
    tombstone at C+1; survivors/global counter use C+2. XML remains unchanged.
    """
    context = smart_delete_context(playlists, members, counter, identity=identity, uuid=uuid,
                                  parent=parent, parent_uuid=parent_uuid, rule=rule)
    reason = smart_delete_reason(playlists, identity)
    if reason:
        raise ValueError(reason)
    target = context['target']
    following = [row for row in context['siblings'] if row['Seq'] > target['Seq']]
    if (not isinstance(sibling_timestamps, dict)
            or set(sibling_timestamps) != {row['ID'] for row in following}
            or any(type(clock) is not str for clock in sibling_timestamps.values())):
        raise ValueError('Smart deletion needs exact following-sibling clocks')
    current = _counter(counter)
    synced = target['rb_local_synced'] == 1
    if synced and type(timestamp) is not str:
        raise ValueError('Synced smart deletion needs its captured tombstone clock')
    changes = [dict(table='djmdPlaylist', id=identity, before=target,
                    after=_deleted(target, current, timestamp) if synced else None)]
    if synced:
        current = _counter(current)
    for sibling in following:
        if sibling['rb_local_usn'] > counter['int_1']:
            raise ValueError('Smart sibling clock exceeds its source counter')
        changes.append(dict(table='djmdPlaylist', id=sibling['ID'], before=sibling,
            after={**sibling, 'Seq': sibling['Seq'] - 1, 'rb_local_usn': current['int_1'],
                   'updated_at': sibling_timestamps[sibling['ID']]}))
    changes.append(dict(table='agentRegistry', id='localUpdateCount',
                        before=deepcopy(counter), after=current))
    return dict(changes=changes, counter=current, source_write_authority=False,
                window=('synced-smart-tombstone-and-sibling-shift+2' if synced
                        else 'unsynced-smart-delete-and-sibling-shift+1'))


def _window_counter(counter, count):
    current = deepcopy(counter)
    for _ in range(count):
        current = _counter(current)
    return current


def create_smart_constructor(playlists, counter, *, identity, uuid, timestamp,
                             parent='root', position_timestamp=None, sibling_timestamps=None):
    """Observed constructor/placement window, not inferred individual SQL steps.

    Root: +1, appended, NULL rule and identical creation/update clock. Observed
    nonempty smart-only folder: front row's final clock is C+2, followed by
    existing siblings at C+3..C+N+2. No unobserved intermediate Seq/C+1 row is
    manufactured. Referenced rules/UUIDs/native flags remain exact.
    """
    reason = smart_parent_reason(playlists, parent)
    if reason:
        raise ValueError(reason)
    first = create(playlists, counter, identity=identity, uuid=uuid,
                   name='Untitled Intelligent List', timestamp=timestamp, parent=parent)
    new = {**first['playlist'], 'Attribute': 4, 'SmartList': None}
    if parent == 'root':
        if position_timestamp is not None or sibling_timestamps:
            raise ValueError('Root smart creation does not reposition existing siblings')
        first['changes'][0]['after'] = new
        return {**first, 'playlist': new, 'window': 'root-constructor+1'}
    siblings = _siblings(playlists, parent)
    if (not isinstance(sibling_timestamps, dict)
            or set(sibling_timestamps) != {row['ID'] for row in siblings}
            or type(position_timestamp) is not str):
        raise ValueError('Exact smart-folder placement clocks are required')
    current = _window_counter(counter, 2)
    new = {**new, 'Seq': 1, 'updated_at': position_timestamp, 'rb_local_usn': current['int_1']}
    changes = [dict(table='djmdPlaylist', id=identity, before=None, after=new)]
    for sibling in siblings:
        current = _counter(current)
        after = {**sibling, 'Seq': sibling['Seq'] + 1,
                 'updated_at': sibling_timestamps[sibling['ID']], 'rb_local_usn': current['int_1']}
        changes.append(dict(table='djmdPlaylist', id=sibling['ID'], before=deepcopy(sibling), after=after))
    changes.append(dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter), after=current))
    return dict(changes=changes, playlist=new, counter=current, source_write_authority=False,
                window='folder-front-placement+N+2')


def create_smart(playlists, counter, *, identity, uuid, name, tag_ids, timestamp,
                 definition_timestamp, parent='root', position_timestamp=None, sibling_timestamps=None):
    """Compose constructor/placement and one observed combined definition window.

    The native definition-save window advances +2 whether only its Name or only
    its rule differs. It is one measured window, never a claim of two particular
    internal SQL setters; creation publishes the final Name and My Tag rule as
    one transaction. Existing intelligent lists have no mutation opcode.
    """
    first = create_smart_constructor(playlists, counter, identity=identity, uuid=uuid,
        timestamp=timestamp, parent=parent, position_timestamp=position_timestamp,
        sibling_timestamps=sibling_timestamps)
    definition = smart_definition_window(first['playlist'], first['counter'], name=name,
        rule=smart_tag_rule(identity, tag_ids), timestamp=definition_timestamp)
    return dict(changes=[*first['changes'], *definition['changes']], playlist=definition['playlist'],
                counter=definition['counter'], source_write_authority=False,
                windows=[first['window'], 'combined-name-rule-definition+2'])


def smart_append_parent_reason(playlists, parent):
    """Measured append into a nonempty local folder; no sibling mutation."""
    if parent == 'root':return 'Root smart creation uses its existing constructor profile'
    try:
        parents=[row for row in playlists if row['ID']==parent]
        if len(parents)!=1 or parents[0]['Attribute']!=1:
            return 'The smart-playlist folder is unavailable'
        _ordinary(parents[0],folder=True)
        siblings=_siblings(playlists,parent)
        if not siblings:return 'Smart creation in an empty folder needs its native constructor reference'
        if len(siblings)>4096:return 'Smart creation exceeds the bounded folder inventory'
        for row in siblings:
            local(row)
            if row['Attribute'] not in (0,1,4):return 'The folder contains an unverified native playlist type'
    except (KeyError,TypeError,ValueError,AttributeError):
        return 'The smart-playlist folder has unverified native metadata'
    return ''


def create_smart_append(playlists,counter,*,identity,uuid,name,tag_ids,timestamp,definition_timestamp,parent):
    """Native direct folder append +1, then the measured new-definition +2."""
    reason=smart_append_parent_reason(playlists,parent)
    if reason:raise ValueError(reason)
    first=create(playlists,counter,identity=identity,uuid=uuid,name='Untitled Intelligent List',
                 timestamp=timestamp,parent=parent)
    new={**first['playlist'],'Attribute':4,'SmartList':None}
    first['changes'][0]['after']=new
    definition=smart_definition_window(new,first['counter'],name=name,
        rule=smart_tag_rule(identity,tag_ids),timestamp=definition_timestamp)
    return dict(changes=[*first['changes'],*definition['changes']],playlist=definition['playlist'],
        counter=definition['counter'],source_write_authority=False,
        windows=['nonempty-folder-append-constructor+1','combined-name-rule-definition+2'])


def smart_definition_window(playlist, counter, *, name, rule, timestamp):
    """Pure observed +2 window; source exposes this only inside new creation.

    This also permits an exact TEMP reference replay of name-only/rule-only
    native observations. It creates no opcode for modifying an existing smart
    list and cannot grant source authority.
    """
    local(playlist)
    if (playlist.get('Attribute') != 4 or not isinstance(rule, str) or not rule or '\0' in rule
            or playlist['rb_local_usn'] > counter['int_1']):
        raise ValueError('Unverified intelligent-definition window')
    current = _window_counter(counter, 2)
    after = {**playlist, 'Name': _name(name), 'SmartList': rule,
             'updated_at': timestamp, 'rb_local_usn': current['int_1']}
    return dict(changes=[dict(table='djmdPlaylist', id=playlist['ID'], before=deepcopy(playlist), after=after),
                        dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter), after=current)],
                playlist=after, counter=current, source_write_authority=False)


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
    rows=sorted((_copy_row(r) for r in members if r.get('rb_local_deleted')==0),key=lambda r:r['TrackNo'])
    if [r['TrackNo'] for r in rows]!=list(range(1,len(rows)+1)):
        raise ValueError('Native song order is ambiguous')
    for row in rows:local(row)
    return rows


def _changes(table,before,after):
    left={r['ID']:r for r in before};right={r['ID']:r for r in after}
    return [dict(table=table,id=key,before=_copy_row(left.get(key)),after=_copy_row(right.get(key)))
            for key in sorted(left.keys()|right.keys()) if left.get(key)!=right.get(key)]


def _result(before,members,counter,current,*,playlists_before=(),playlists_after=()):
    changes=_changes('djmdSongPlaylist',before,members)
    changes+=_changes('djmdPlaylist',playlists_before,playlists_after)
    if current!=counter:
        changes.append(dict(table='agentRegistry',id='localUpdateCount',
                            before=_copy_row(counter),after=current))
    return dict(changes=changes,members=members,counter=current,source_write_authority=False)


def _created_node(playlists,counter,*,identity,uuid,name,timestamp,parent,folder,sequence):
    if (not isinstance(identity,str) or not identity.isascii() or not identity.isdecimal()
            or not 0<int(identity)<2**32 or str(int(identity))!=identity or str(UUID(uuid))!=uuid):
        raise ValueError('Invalid native playlist identity')
    if any(r.get('ID')==identity or r.get('UUID')==uuid for r in playlists):
        raise ValueError('Native playlist identity collides with retained history')
    if type(sequence) is not int or sequence<1:raise ValueError('Invalid constructor position')
    current=_counter(counter)
    row=dict(ID=identity,Seq=sequence,Name=_name(name),ImagePath=None,
             Attribute=int(folder),ParentID=parent,SmartList=None,UUID=uuid,rb_data_status=0,
             rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None,
             rb_local_usn=current['int_1'],created_at=timestamp,updated_at=timestamp)
    return dict(changes=[dict(table='djmdPlaylist',id=identity,before=None,after=row),
                         dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current)],
                playlist=row,counter=current,source_write_authority=False)


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
    siblings=_siblings(playlists,parent)
    return _created_node(playlists,counter,identity=identity,uuid=uuid,name=name,timestamp=timestamp,
                         parent=parent,folder=folder,sequence=len(siblings)+1)

def rename(playlist,counter,*,name,timestamp):
    _ordinary(playlist,folder=True);name=_name(name)
    if name==playlist['Name']:
        return dict(changes=[],playlist=deepcopy(playlist),counter=deepcopy(counter),source_write_authority=False)
    current=_counter(counter);after={**playlist,'Name':name,'updated_at':timestamp,'rb_local_usn':current['int_1']}
    return dict(changes=[dict(table='djmdPlaylist',id=playlist['ID'],before=deepcopy(playlist),after=after),
                         dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current)],
                playlist=after,counter=current,source_write_authority=False)


def reorder(playlists,counter,*,identity,anchor_id,after=False,timestamp,sibling_timestamp=None,_candidate_smart=False):
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
    guard = _smart_editable if _candidate_smart else lambda row:_ordinary(row,folder=True)
    guard(target);guard(anchor)
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
        guard(old_neighbor)
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


def _smart_editable(row):
    """Measured local intelligent histories; cloud/unknown statuses stay excluded."""
    if row.get('Attribute') != 4:
        _ordinary(row, folder=True);return
    local(row)
    if type(row.get('SmartList')) is not str or not row['SmartList']:
        raise ValueError('The candidate smart profile requires an exact local intelligent rule')


def smart_rename_candidate(playlist,counter,*,name,timestamp):
    """Measured local inline-name +1; definition-editor +2 stays separate."""
    _smart_editable(playlist)
    if playlist.get('Attribute') != 4:raise ValueError('Expected an intelligent playlist')
    if playlist['rb_local_usn']>counter.get('int_1',-1):raise ValueError('Intelligent playlist clock exceeds its source counter')
    if _name(name) == playlist['Name']:
        return dict(changes=[],playlist=deepcopy(playlist),counter=deepcopy(counter),source_write_authority=False)
    current=_counter(counter)
    after={**playlist,'Name':name,'updated_at':timestamp,'rb_local_usn':current['int_1']}
    return dict(changes=[dict(table='djmdPlaylist',id=playlist['ID'],before=deepcopy(playlist),after=after),
                        dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current)],
                playlist=after,counter=current,source_write_authority=False)


def smart_reorder_rows(playlists,*,identity,anchor_id,after=False):
    """Exact local target plus displaced siblings, in native old-Seq order."""
    selected=[row for row in playlists if row['ID']==identity]
    anchors=[row for row in playlists if row['ID']==anchor_id]
    if len(selected)!=1 or len(anchors)!=1 or identity==anchor_id or type(after) is not bool:
        raise ValueError('Expected distinct exact native reorder identities')
    target,anchor=selected[0],anchors[0];_smart_editable(target);_smart_editable(anchor)
    if target['ParentID']!=anchor['ParentID']:
        raise ValueError('Reordering cannot transfer a playlist to another folder')
    siblings=_siblings(playlists,target['ParentID']);order=[row['ID'] for row in siblings]
    wanted=[key for key in order if key!=identity];wanted.insert(wanted.index(anchor_id)+int(after),identity)
    final={key:index+1 for index,key in enumerate(wanted)}
    shifted=[row for row in siblings if row['ID']!=identity and row['Seq']!=final[row['ID']]]
    for row in shifted:_smart_editable(row)
    return target,shifted,final


def smart_reorder_candidate(playlists,counter,*,identity,anchor_id,after=False,timestamp,sibling_timestamps):
    """Measured native direct move: target first, then shifted rows in old Seq order."""
    target,shifted,final=smart_reorder_rows(playlists,identity=identity,anchor_id=anchor_id,after=after)
    if any(row['rb_local_usn']>counter.get('int_1',-1) for row in (target,*shifted)):
        raise ValueError('Intelligent playlist clock exceeds its source counter')
    if set(sibling_timestamps)!={row['ID'] for row in shifted}:
        raise ValueError('Intelligent ordering requires every exact displaced sibling clock')
    current=deepcopy(counter);changes=[]
    if target['Seq']!=final[identity]:
        for row in (target,*shifted):
            current=_counter(current)
            changed={**row,'Seq':final[row['ID']],
                     'updated_at':timestamp if row['ID']==identity else sibling_timestamps[row['ID']],
                     'rb_local_usn':current['int_1']}
            changes.append(dict(table='djmdPlaylist',id=row['ID'],before=deepcopy(row),after=changed))
        changes.append(dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current))
    return dict(changes=changes,counter=current,source_write_authority=False)


def ordinary_request_reason(catalog,request):
    """Reject unaccepted intelligent effects before a desktop FIFO is persisted."""
    if request['operation'] not in {'rename','reorder','delete','append','remove'}:return ''
    nodes=catalog['nodes'];target=next((row for row in nodes if row['ID']==request['playlist_id']),None)
    if target is None:return 'The selected playlist disappeared; refresh and choose it again'
    try:
        def ordinary_kind(row):
            if row.get('Attribute') not in (0,1) or row.get('SmartList') not in ('',None):
                raise ValueError('Intelligent metadata has a separate profile')
        ordinary_kind(target)
        if request['operation']=='reorder':
            siblings=_siblings(nodes,target['ParentID']);ids=[row['ID'] for row in siblings]
            if request['anchor_id'] not in ids:raise ValueError('The reorder anchor changed parent')
            start,end=sorted((ids.index(target['ID']),ids.index(request['anchor_id'])))
            for row in siblings[start:end+1]:ordinary_kind(row)
    except (ValueError,KeyError,TypeError):
        return 'Existing intelligent playlist changes require their separate native acceptance'
    return ''


def append(playlist,members,counter,content,*,identity,uuid,timestamp,allow_duplicate=False):
    rows=_base(playlist,members,counter)
    if content.get('rb_local_deleted') or not content.get('ID') or not content.get('UUID'):
        raise ValueError('Native Collection track identity is unavailable')
    if not allow_duplicate and any(r['ContentID']==content['ID'] for r in rows):
        return _result(members,_copy_rows(members),counter,_copy_row(counter))
    if str(UUID(identity))!=identity or str(UUID(uuid))!=uuid:
        raise ValueError('Invalid native membership identity')
    if any(r.get('ID')==identity or r.get('UUID')==uuid for r in members):
        raise ValueError('Native membership identity collides with retained history')
    current=_counter(counter)
    after=[*_copy_rows(members),dict(ID=identity,PlaylistID=playlist['ID'],ContentID=content['ID'],
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
    current=_counter(counter);after=_copy_rows(members)
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
