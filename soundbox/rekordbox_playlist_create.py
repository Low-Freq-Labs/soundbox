"""Independently gated constructors sharing exact normalized-placement mechanics."""
from copy import deepcopy

from . import rekordbox_playlist_plan as plan,rekordbox_playlist_move as move


def context(nodes,parent,*,_position=None,_parent_limit=64):
    destination=move._parent(nodes,parent,_limit=_parent_limit)
    siblings=move._siblings(nodes,parent)
    position=len(siblings)+1 if _position is None else _position
    if type(position) is not int or not 1<=position<=len(siblings)+1:
        raise ValueError('Invalid normalized creation position')
    shifted={row['ID']:index+1+int(index+1>=position) for index,row in enumerate(siblings)
             if row['Seq']!=index+1+int(index+1>=position)}
    return dict(parent=destination,siblings=siblings,position=position,shifted=shifted)


def required(current,parent):
    selected=positions(current['nodes'],parent)
    return [row['Seq'] for row in selected]!=list(range(1,len(selected)+1))


def positions(nodes,parent):
    """Read-only projection/admission arithmetic; grants no source-state proof."""
    if parent!='root':
        owners=[row for row in nodes if row.get('ID')==parent and row.get('rb_local_deleted')==0]
        if len(owners)!=1 or owners[0].get('Attribute')!=1:raise ValueError('Creation folder is unavailable')
    selected=sorted((row for row in nodes if row.get('ParentID')==parent and row.get('rb_local_deleted')==0),
                    key=lambda row:row['Seq'])
    seq=[row['Seq'] for row in selected]
    if (len(selected)>4096 or any(type(value) is not int or value<1 for value in seq)
            or len(set(seq))!=len(seq) or len({row['ID'] for row in selected})!=len(selected)):
        raise ValueError('Creation sibling order is ambiguous')
    return selected


def _positioned_constructor(nodes,counter,*,identity,uuid,parent,timestamp,position_timestamp,
                            sibling_timestamps,smart=False,folder=False,_position=None):
    """Constructor + changed placement rows + name; never grants source scope.

    Ordinary contiguous root append reproduces its accepted constructor/name.
    The retained gapped folder insertion additionally proves one target move and
    one counter per numerically normalized sibling, in original Seq order.
    Smart construction starts after the greatest ROOT Seq: its actual gapped
    native frame then moves the new node to the final ordinal before peers.
    Ordinary legacy profiles retain their original reconstruction semantics.
    """
    selected=context(nodes,parent,_position=_position,_parent_limit=4096 if smart or folder else 64)
    roots=move._siblings(nodes,'root')
    if smart and max((row['Seq'] for row in roots),default=0)>=2**32-1:
        raise ValueError('Native smart constructor root position exceeds its observed integer range')
    first=plan._created_node(nodes,counter,identity=identity,uuid=uuid,
                            name='Untitled Intelligent List' if smart else 'Untitled Folder' if folder else 'Untitled Playlist',
                            timestamp=timestamp,parent='root',folder=folder,
                            sequence=1+max((row['Seq'] for row in roots),default=0) if smart else len(roots)+1)
    if smart:
        first['playlist']={**first['playlist'],'Attribute':4}
        first['changes'][0]['after']=first['playlist']
    changes=deepcopy(first['changes']);new=first['playlist'];current=first['counter'];prior=deepcopy(current)
    if (new['ParentID'],new['Seq'])!=(parent,selected['position']):
        current=plan._counter(current)
        placed={**new,'ParentID':parent,'Seq':selected['position'],'updated_at':position_timestamp,'rb_local_usn':current['int_1']}
        changes.append(dict(table='djmdPlaylist',id=identity,before=deepcopy(new),after=placed));new=placed
    if set(sibling_timestamps)!=set(selected['shifted']):raise ValueError('Normalized constructor needs exact changed-sibling clocks')
    for row in selected['siblings']:
        if row['ID'] not in selected['shifted']:continue
        current=plan._counter(current)
        changes.append(dict(table='djmdPlaylist',id=row['ID'],before=deepcopy(row),
            after={**row,'Seq':selected['shifted'][row['ID']],'updated_at':sibling_timestamps[row['ID']],
                   'rb_local_usn':current['int_1']}))
    if current!=prior:
        changes.append(dict(table='agentRegistry',id='localUpdateCount',before=prior,after=current))
    return dict(changes=changes,playlist=new,counter=current,source_write_authority=False,
                positioned=(new['ParentID'],new['Seq'])!=(first['playlist']['ParentID'],first['playlist']['Seq']))


def candidate(nodes,counter,*,identity,uuid,name,parent,timestamp,position_timestamp,
              sibling_timestamps,name_timestamp,folder=False,_position=None):
    first=_positioned_constructor(nodes,counter,identity=identity,uuid=uuid,parent=parent,
        timestamp=timestamp,position_timestamp=position_timestamp,sibling_timestamps=sibling_timestamps,folder=folder,_position=_position)
    named=plan.rename(first['playlist'],first['counter'],name=name,timestamp=name_timestamp)
    return dict(first,changes=[*first['changes'],*named['changes']],playlist=named['playlist'],counter=named['counter'])


def smart_candidate(nodes,counter,*,identity,uuid,name,parent,tag_ids,timestamp,position_timestamp,
                    sibling_timestamps,definition_timestamp,_position=None):
    """Provisional smart constructor/placement/definition; no native authority.

    Shares mechanical placement with the ordinary native oracle. Its separate
    smart acceptance never follows from an ordinary constructor permission.
    """
    first=_positioned_constructor(nodes,counter,identity=identity,uuid=uuid,parent=parent,
        timestamp=timestamp,position_timestamp=position_timestamp,sibling_timestamps=sibling_timestamps,
        smart=True,_position=_position)
    defined=plan.smart_definition_window(first['playlist'],first['counter'],name=name,
        rule=plan.smart_tag_rule(identity,tag_ids),timestamp=definition_timestamp)
    return dict(first,changes=[*first['changes'],*defined['changes']],playlist=defined['playlist'],counter=defined['counter'])
