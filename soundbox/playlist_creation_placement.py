"""Saved creation placement pins; display projection grants no native authority."""
from uuid import UUID

FORMAT='soundbox-created-playlist-placement-v1'
_ANCHOR={'source','library_id','anchor_id','anchor_uuid','anchor_attribute','parent','parent_uuid'}


def _identity(value):
    return (type(value) is str and value.isascii() and value.isdecimal()
            and 0<int(value)<2**32 and str(int(value))==value)


def _uuid(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def validate_anchor(anchor):
    if (not isinstance(anchor,dict) or set(anchor)!=_ANCHOR
            or any(type(anchor[key]) is not str or not anchor[key] or '\0' in anchor[key]
                   or len(anchor[key])>8192 for key in ('source','library_id'))
            or not _identity(anchor['anchor_id']) or not _uuid(anchor['anchor_uuid'])
            or type(anchor['anchor_attribute']) is not int or anchor['anchor_attribute'] not in (0,1,4)
            or (anchor['parent']!='root' and not _identity(anchor['parent']))
            or (anchor['parent_uuid']!='' if anchor['parent']=='root' else not _uuid(anchor['parent_uuid']))):
        raise ValueError('The captured creation position is invalid')


def anchor_matches(anchor,catalog,source,library_id):
    validate_anchor(anchor)
    if (anchor['source'],anchor['library_id'])!=(str(source),library_id):return False
    selected=[row for row in catalog['nodes'] if row.get('ID')==anchor['anchor_id']]
    if len(selected)!=1:return False
    row=selected[0]
    if (row.get('UUID'),row.get('Attribute'),row.get('ParentID'),row.get('rb_local_deleted'))!=(
            anchor['anchor_uuid'],anchor['anchor_attribute'],anchor['parent'],0):return False
    if anchor['parent']=='root':return True
    parents=[row for row in catalog['nodes'] if row.get('ID')==anchor['parent']]
    return len(parents)==1 and (parents[0].get('UUID'),parents[0].get('Attribute'),
        parents[0].get('rb_local_deleted'))==(anchor['parent_uuid'],1,0)


def placement_for(request,anchor,source,library_id):
    validate_anchor(anchor)
    proof=dict(anchor,format=FORMAT,target_id=request['playlist_id'],target_uuid=request['playlist_uuid'])
    validate(proof,request,source,library_id)
    return proof


def validate(proof,request,source,library_id):
    from .rekordbox_playlist_store import CREATE_ORDINARY_OPERATIONS,CREATE_SMART_OPERATIONS
    if (not isinstance(proof,dict) or set(proof)!=_ANCHOR|{'format','target_id','target_uuid'}
            or proof['format']!=FORMAT):raise ValueError('Unknown saved creation position')
    anchor={key:proof[key] for key in _ANCHOR};validate_anchor(anchor)
    if (request['operation'] not in CREATE_ORDINARY_OPERATIONS|CREATE_SMART_OPERATIONS
            or (proof['source'],proof['library_id'])!=(str(source),library_id)
            or (proof['target_id'],proof['target_uuid'])!=(request['playlist_id'],request['playlist_uuid'])
            or proof['target_id']==proof['anchor_id'] or request['parent']!=proof['parent']
            or request.get('parent_uuid')!=proof['parent_uuid']):
        raise ValueError('Saved creation position belongs to another playlist or library')


def anchor_from(proof):return {key:proof[key] for key in _ANCHOR}


def project(catalog,request,proof,source,library_id):
    """Place an already projected new node; never invent SQL clocks/history."""
    validate(proof,request,source,library_id)
    if not anchor_matches(anchor_from(proof),catalog,source,library_id):return False
    target=next((row for row in catalog['nodes'] if row['ID']==proof['target_id']
                 and row['UUID']==proof['target_uuid'] and row['rb_local_deleted']==0),None)
    if target is None or target['ParentID']!=proof['parent']:return False
    siblings=sorted((row for row in catalog['nodes'] if row['ParentID']==proof['parent']
        and row['rb_local_deleted']==0 and row is not target),key=lambda row:row['Seq'])
    position=next(index for index,row in enumerate(siblings) if row['ID']==proof['anchor_id'])+1
    siblings.insert(position,target)
    for position,row in enumerate(siblings,1):row['Seq']=position
    return True
