"""Resumable playlist migration plan. Publication uses existing accepted FIFOs."""
from copy import deepcopy
from hashlib import sha256

from . import playlist_folders

KEY = 'playlist_unification'


def artist_key(name):
    return 'artist:'+sha256(name.encode()).hexdigest()


def new(source, library_id):
    return dict(schema=1, source=str(source), library_id=library_id, nodes={},
                customs_done=False, error='')


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'schema','source','library_id','nodes','customs_done','error'}
            or value['schema']!=1 or not isinstance(value['source'],str)
            or not isinstance(value['library_id'],str) or not value['library_id']
            or type(value['customs_done']) is not bool or not isinstance(value['error'],str)
            or not isinstance(value['nodes'],dict) or len(value['nodes'])>16384):
        raise ValueError('The saved playlist merge needs attention; its evidence is preserved')
    from uuid import UUID
    identities=set()
    for key,node in value['nodes'].items():
        if (not isinstance(key,str) or not isinstance(node,dict) or set(node)!={'id','uuid'}
                or not isinstance(node['id'],str) or not node['id'].isascii()
                or not node['id'].isdecimal() or not 0<int(node['id'])<2**32
                or str(int(node['id']))!=node['id'] or str(UUID(node['uuid']))!=node['uuid']
                or node['id'] in identities):
            raise ValueError('Ambiguous playlist merge identity; original playlists are preserved')
        identities.add(node['id'])
    return value


def matches(value, library):
    return bool(value and library and (value['source'],value['library_id'])==
                (str(library.source),library.source_library_id))


def specifications(custom, tree, artist_tracks=None):
    """One-time custom hierarchy only; Artists is an ephemeral SoundBox view.

    The old third argument remains accepted for private read-only audit helpers;
    it is deliberately ignored and can never produce native artist commands.
    """
    result=[]
    def walk(nodes,parent):
        for node in nodes:
            if 'folder' in node:
                key='folder:'+node['folder']
                result.append(dict(key=key,name=node['name'],parent=parent,folder=True,ids=[]))
                walk(node['children'],key)
            else:
                name=node['playlist']
                result.append(dict(key='custom:'+name,name=name,parent=parent,folder=False,ids=list(custom[name])))
    walk(playlist_folders.reconcile(custom,tree),'')
    return result


def hidden_native_roots(value,catalog=None):
    """Hide only exact archived generated IDs, never a native name match.

    Old artist bindings remain recovery evidence until their separately
    confirmed source cleanup. They never activate further artist writes.
    """
    if not value:return set()
    known=({row['ID']:row['UUID'] for row in catalog.get('nodes',[]) if row['rb_local_deleted']==0}
           if catalog is not None else None)
    hidden={node['id'] for key,node in value['nodes'].items()
            if (key=='artists' or key.startswith('artist:'))
            and (known is None or known.get(node['id'])==node['uuid'])}
    if not value['customs_done']:
        hidden|={node['id'] for key,node in value['nodes'].items() if key.startswith(('custom:','folder:'))}
    return hidden


def bind(value,key,request):
    result=deepcopy(value)
    result['nodes'][key]=dict(id=request['playlist_id'],uuid=request['playlist_uuid'])
    validate(result)
    return result
