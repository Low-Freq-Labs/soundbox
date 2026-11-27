"""Explicit dragged folder roots project destinations without editing source paths."""
from dataclasses import replace
from typing import Any

from . import manifest, playlist_folders
from .library import Playlist


def retain_projection(playlist, record, *, source_key='folder_path', destination_key='export_folder_path'):
    """Retain a prior explicit destination only for unchanged canonical ancestry."""
    if playlist.export_folder_path is not None or destination_key not in record:
        return playlist
    if record.get(source_key) != playlist.folder_path:
        raise ValueError('Export folder ancestry changed; select its current folder again')
    if not isinstance(record[destination_key], list):
        raise ValueError('Invalid retained export folder selection')
    selected = replace(playlist, export_folder_path=list(record[destination_key]))
    selected.destination_folder_path  # Validate the retained destination, not device authority.
    return selected


def project_folder_roots(library, playlists, roots, *, local_tree=()):
    """Validate bound roots and return detached, stably deduplicated selections."""
    if not isinstance(roots, list) or len(roots) > 4096:
        raise ValueError('Invalid export folder selection')
    rows: dict[str,list[dict[str,Any]]] = {}
    for node in library.source_playlist_catalog.get('nodes', ()):
        identity = str(node.get('ID', ''))
        rows.setdefault(identity, []).append(node)

    def native(identity):
        found = rows.get(str(identity), ())
        if len(found) != 1 or found[0].get('rb_local_deleted') != 0:
            raise ValueError('Selected native folder ancestry changed')
        return found[0]

    def ancestry(identity):
        result: list[dict[str,Any]] = []; seen: set[str] = set()
        parent = native(identity).get('ParentID')
        while parent not in (None, '', '0', 'root'):
            parent = str(parent)
            if parent in seen or len(seen) >= 64:
                raise ValueError('Ambiguous native folder ancestry')
            seen.add(parent); row = native(parent)
            if row.get('Attribute') != 1:
                raise ValueError('Selected native folder ancestry changed')
            result.insert(0, row); parent = row.get('ParentID')
        return result

    native_roots = []; local_roots = []
    for root in roots:
        if not isinstance(root, dict):
            raise ValueError('Invalid export folder selection')
        if set(root) == {'kind', 'binding'} and root['kind'] == 'native':
            binding = root['binding']
            if (not isinstance(binding, dict)
                    or set(binding) != {'source', 'library_id', 'id', 'uuid', 'alias'}
                    or (binding['source'], binding['library_id']) !=
                       (str(library.source), library.source_library_id)):
                raise ValueError('Selected native folder belongs to another library')
            row = native(binding['id'])
            if (row.get('Attribute') != 1 or row.get('UUID') != binding['uuid']
                    or binding['alias'] is not None):
                raise ValueError('Selected native folder identity changed')
            prefix = [*ancestry(row['ID']), row]
            native_roots.append((str(row['ID']), [str(part['Name']) for part in prefix]))
        elif set(root) == {'kind', 'id'} and root['kind'] == 'local':
            matches = [(node, path) for node, path in playlist_folders.walk(local_tree)
                       if node.get('folder') == root['id']]
            if len(matches) != 1:
                raise ValueError('Selected local folder identity changed')
            node, path = matches[0]
            names = {part['playlist'] for part, _path in playlist_folders.walk(node['children'])
                     if 'playlist' in part}
            local_roots.append(([*path, node['name']], names))
        else:
            raise ValueError('Invalid export folder selection')
    selected: dict[str,Playlist] = {}
    covered: dict[str,set[str]] = {identity:set() for identity, _prefix in native_roots}
    for playlist in playlists:
        key = manifest.playlist_key(playlist)
        offsets = []
        if playlist.pid and native_roots:
            ancestors = {str(row['ID']) for row in ancestry(playlist.pid)}
            for identity, prefix in native_roots:
                if identity in ancestors:
                    if playlist.folder_path[:len(prefix)] != prefix:
                        raise ValueError('Selected native folder path changed')
                    offsets.append(len(prefix)-1); covered[identity].add(playlist.pid)
        for prefix, names in local_roots:
            if not playlist.pid and playlist.name in names:
                if playlist.folder_path[:len(prefix)] != prefix:
                    raise ValueError('Selected local folder path changed')
                offsets.append(len(prefix)-1)
        destination = (list(playlist.folder_path[min(offsets):]) if offsets else
                       list(playlist.export_folder_path) if playlist.export_folder_path is not None else None)
        selected.setdefault(key, replace(playlist, folder_path=list(playlist.folder_path),
            keys=list(playlist.keys), added_keys=list(playlist.added_keys),
            export_folder_path=destination))
    for identity, _prefix in native_roots:
        current = {playlist.pid for playlist in library.playlists
                   if playlist.pid and identity in {str(row['ID']) for row in ancestry(playlist.pid)}}
        if covered[identity] != current:
            raise ValueError('Selected native folder contents changed; drag it again')
    return list(selected.values())
