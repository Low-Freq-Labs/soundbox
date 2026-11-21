"""Pure, independently gated smart reparent proposals; no SQL or authority."""
from copy import deepcopy

from . import rekordbox_playlist_plan as plan


def _siblings(nodes,parent):
    rows=sorted((row for row in nodes if row.get('ParentID')==parent and row.get('rb_local_deleted')==0),
                key=lambda row:row['Seq'])
    positions=[row['Seq'] for row in rows]
    if (len(rows)>4096 or any(type(value) is not int or value<1 for value in positions)
            or len(set(positions))!=len(positions)):
        raise ValueError('Native move sibling order is ambiguous')
    # Native reparent observations retain gaps in the source parent. Moving is
    # the separate domain that understands those unique, ordered positions.
    for row in rows:plan._smart_editable(row)
    return rows


def _parent(nodes,identity,*,_limit=64):
    if identity=='root':return None
    selected=[row for row in nodes if row.get('ID')==identity]
    if len(selected)!=1 or selected[0].get('Attribute')!=1:
        raise ValueError('Native move requires an exact active ordinary folder')
    parent=selected[0];plan._ordinary(parent,folder=True)
    if sum(row.get('UUID')==parent['UUID'] for row in nodes)!=1:
        raise ValueError('Native folder UUID is ambiguous')
    seen={identity};ancestor=parent['ParentID']
    while ancestor!='root':
        if ancestor in seen or len(seen)>=_limit:raise ValueError('Native folder ancestry is ambiguous')
        seen.add(ancestor);rows=[row for row in nodes if row.get('ID')==ancestor]
        if len(rows)!=1 or rows[0].get('Attribute')!=1:raise ValueError('Native folder ancestor is unavailable')
        plan._ordinary(rows[0],folder=True);ancestor=rows[0]['ParentID']
    return parent


def context(nodes,*,identity,parent,_position=None):
    """Capture exact source/destination metadata, preserving source Seq gaps.

    Public requests append only. The private position argument reconstructs the
    retained native middle-insertion reference and grants no source API.
    """
    selected=[row for row in nodes if row.get('ID')==identity]
    if len(selected)!=1:raise ValueError('Native smart move identity is unavailable')
    target=selected[0];plan._smart_editable(target)
    if sum(row.get('UUID')==target['UUID'] for row in nodes)!=1:
        raise ValueError('Native smart move UUID is ambiguous')
    if target['Attribute']!=4:raise ValueError('Cross-parent moves require an intelligent playlist')
    if parent==target['ParentID']:raise ValueError('Same-parent moves use the accepted ordering operation')
    source=_parent(nodes,target['ParentID']);destination=_parent(nodes,parent)
    old=_siblings(nodes,target['ParentID']);new=_siblings(nodes,parent)
    if sum(row['ID']==identity for row in old)!=1 or any(row['ID']==identity for row in new):
        raise ValueError('Native move endpoint inventory is ambiguous')
    position=len(new)+1 if _position is None else _position
    if type(position) is not int or not 1<=position<=len(new)+1:raise ValueError('Invalid native destination position')
    order=[row['ID'] for row in new];order.insert(position-1,identity)
    final={key:index+1 for index,key in enumerate(order)}
    shifted=[row for row in new if row['Seq']!=final[row['ID']]]
    return dict(target=target,source_parent=source,parent=destination,source_siblings=old,
                destination_siblings=new,shifted=shifted,positions=final)


def reason(nodes,*,identity,parent):
    try:context(nodes,identity=identity,parent=parent)
    except (ValueError,KeyError,TypeError,AttributeError) as exc:return str(exc)
    return ''


def candidate(nodes,counter,*,identity,parent,timestamp,sibling_timestamps,_position=None):
    """Provisional reparent windows; independent native/source acceptance stays required.

    Fresh isolated native references establish one target counter step for
    root→folder and folder→empty nested folder. Only numerically changed
    destination positions add sibling steps; source positions remain exact.
    """
    observed=context(nodes,identity=identity,parent=parent,_position=_position)
    target=observed['target'];source=observed['source_parent'];shifted=observed['shifted']
    if set(sibling_timestamps)!={row['ID'] for row in shifted}:
        raise ValueError('Native move needs every exact changed-destination clock')
    if any(row['rb_local_usn']>counter.get('int_1',-1) for row in
           (target,*observed['source_siblings'],*observed['destination_siblings'],
            *([source] if source is not None else []),*([observed['parent']] if observed['parent'] is not None else []))):
        raise ValueError('Native move row clock exceeds the source counter')
    current=plan._counter(counter)
    moved={**target,'ParentID':parent,'Seq':observed['positions'][identity],
           'updated_at':timestamp,'rb_local_usn':current['int_1']}
    changes=[dict(table='djmdPlaylist',id=identity,before=deepcopy(target),after=moved)]
    for row in shifted:
        current=plan._counter(current)
        changes.append(dict(table='djmdPlaylist',id=row['ID'],before=deepcopy(row),
            after={**row,'Seq':observed['positions'][row['ID']],
                   'updated_at':sibling_timestamps[row['ID']],'rb_local_usn':current['int_1']}))
    changes.append(dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current))
    return dict(changes=changes,counter=current,playlist=moved,source_write_authority=False)


def subtree(nodes, identity):
    """Bind descendants without changing their history, rules or parent links."""
    active={row['ID']:row for row in nodes if row.get('rb_local_deleted')==0}
    if len(active)!=sum(row.get('rb_local_deleted')==0 for row in nodes) or identity not in active:
        raise ValueError('Native move subtree identity is ambiguous')
    result=[];seen={identity};pending=[identity]
    while pending:
        parent=pending.pop()
        children=[row for row in active.values() if row.get('ParentID')==parent]
        if children and active[parent].get('Attribute')!=1:
            raise ValueError('A native playlist cannot contain descendant playlists')
        for row in sorted(children,key=lambda child:child['ID']):
            if row['ID'] in seen or len(seen)>=4096:
                raise ValueError('Native move subtree is cyclic or exceeds its bounded scope')
            plan._smart_editable(row);seen.add(row['ID']);pending.append(row['ID'])
            result.append(dict(id=row['ID'],uuid=row['UUID'],parent=row['ParentID'],
                               attribute=row['Attribute'],rule=row.get('SmartList')))
    return result


def node_context(nodes,*,identity,parent,anchor_id=None,after=False):
    """Exact placement of one local0/1/4 node, retaining every descendant.

    An absent anchor appends inside the destination. An anchor always belongs
    to that destination; it never implicitly chooses a parent from a row label.
    Source gaps survive reparenting. A same-parent relative no-op stays a no-op.
    """
    selected=[row for row in nodes if row.get('ID')==identity]
    if len(selected)!=1:raise ValueError('Native move identity is unavailable')
    target=selected[0];plan._smart_editable(target)
    if sum(row.get('UUID')==target['UUID'] for row in nodes)!=1:
        raise ValueError('Native move UUID is ambiguous')
    descendants=subtree(nodes,identity)
    if parent in {identity,*(row['id'] for row in descendants)}:
        raise ValueError('A folder cannot move into itself or its descendants')
    if type(after) is not bool:raise ValueError('Invalid native placement direction')
    source=_parent(nodes,target['ParentID'],_limit=4096);destination=_parent(nodes,parent,_limit=4096)
    old=_siblings(nodes,target['ParentID'])
    new=[row for row in _siblings(nodes,parent) if row['ID']!=identity]
    if sum(row['ID']==identity for row in old)!=1:
        raise ValueError('Native source move inventory is ambiguous')
    anchor=None
    if anchor_id is None:
        if after:raise ValueError('Native append has no anchor direction')
        position=len(new)
    else:
        anchors=[row for row in new if row['ID']==anchor_id]
        if len(anchors)!=1:raise ValueError('Native destination anchor is unavailable')
        anchor=anchors[0];position=new.index(anchor)+int(after)
    order=[row['ID'] for row in new];order.insert(position,identity)
    final={key:index+1 for index,key in enumerate(order)}
    changed=parent!=target['ParentID'] or order!=[row['ID'] for row in old]
    shifted=[row for row in new if changed and row['Seq']!=final[row['ID']]]
    return dict(target=target,source_parent=source,parent=destination,source_siblings=old,
                destination_siblings=new,shifted=shifted,positions=final,anchor=anchor,
                descendants=descendants,changed=changed)


def node_reason(nodes,*,identity,parent,anchor_id=None,after=False):
    try:node_context(nodes,identity=identity,parent=parent,anchor_id=anchor_id,after=after)
    except (ValueError,KeyError,TypeError,AttributeError) as exc:return str(exc)
    return ''


def node_candidate(nodes,counter,*,identity,parent,timestamp,sibling_timestamps,anchor_id=None,after=False):
    """Pure default-off placement candidate; source acceptance is independent."""
    observed=node_context(nodes,identity=identity,parent=parent,anchor_id=anchor_id,after=after)
    if set(sibling_timestamps)!={row['ID'] for row in observed['shifted']}:
        raise ValueError('Native placement needs every exact changed-destination clock')
    if not observed['changed']:
        return dict(changes=[],counter=deepcopy(counter),playlist=deepcopy(observed['target']),source_write_authority=False)
    # The independently accepted smart placement primitive and this candidate
    # share only mechanical row/counter construction, never authority flags.
    target=observed['target'];source=observed['source_parent']
    if any(row['rb_local_usn']>counter.get('int_1',-1) for row in
           (target,*observed['source_siblings'],*observed['destination_siblings'],
            *([source] if source is not None else []),*([observed['parent']] if observed['parent'] is not None else []))):
        raise ValueError('Native placement row clock exceeds the source counter')
    current=plan._counter(counter)
    moved={**target,'ParentID':parent,'Seq':observed['positions'][identity],
           'updated_at':timestamp,'rb_local_usn':current['int_1']}
    changes=[dict(table='djmdPlaylist',id=identity,before=deepcopy(target),after=moved)]
    for row in observed['shifted']:
        current=plan._counter(current)
        changes.append(dict(table='djmdPlaylist',id=row['ID'],before=deepcopy(row),
            after={**row,'Seq':observed['positions'][row['ID']],
                   'updated_at':sibling_timestamps[row['ID']],'rb_local_usn':current['int_1']}))
    changes.append(dict(table='agentRegistry',id='localUpdateCount',before=deepcopy(counter),after=current))
    return dict(changes=changes,counter=current,playlist=moved,source_write_authority=False)
