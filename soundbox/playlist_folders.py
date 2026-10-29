"""Local playlist organization; membership and existing device identities stay separate."""
from copy import deepcopy
from uuid import UUID


def name(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > 128 or any(ord(c) < 32 for c in value)):
        raise ValueError('Use a name of 1–128 characters without control characters')
    return value


def validate(nodes):
    folders, playlists = set(), set()
    count = 0
    def visit(items, depth):
        nonlocal count
        if not isinstance(items, list) or depth > 12:
            raise ValueError('Invalid SoundBox playlist folder hierarchy')
        siblings = set()
        for node in items:
            count += 1
            if count > 4096 or not isinstance(node, dict):
                raise ValueError('Playlist folder hierarchy is too large or invalid')
            if set(node) == {'playlist'}:
                key = node['playlist']
                if not isinstance(key, str) or not key or key in playlists:
                    raise ValueError('Ambiguous playlist folder membership')
                playlists.add(key)
            elif set(node) == {'folder', 'name', 'children'}:
                key = node['folder']
                try:
                    if str(UUID(key)) != key:raise ValueError('Noncanonical folder identity')
                except (ValueError, AttributeError, TypeError) as exc:
                    raise ValueError('Invalid playlist folder identity') from exc
                label = name(node['name'])
                if '/' in label or '\\' in label or key in folders or label.casefold() in siblings:
                    raise ValueError('Duplicate or invalid playlist folder')
                folders.add(key); siblings.add(label.casefold())
                visit(node['children'], depth + 1)
            else:
                raise ValueError('Unknown playlist folder record')
    visit(nodes, 0)


def walk(nodes, path=()):
    for node in nodes:
        yield node, path
        if 'folder' in node:
            yield from walk(node['children'], (*path, node['name']))


def reconcile(custom, nodes=None):
    """Legacy playlists remain at root; explicit empty folders survive refresh/restore."""
    result = deepcopy(nodes) if nodes is not None else []
    validate(result)
    seen = set()
    def prune(items):
        kept = []
        for node in items:
            if 'folder' in node:
                node['children'] = prune(node['children']); kept.append(node)
            elif node['playlist'] in custom:
                seen.add(node['playlist']); kept.append(node)
        return kept
    result = prune(result)
    result.extend({'playlist':key} for key in custom if key not in seen)
    validate(result)
    return result


def locate(nodes, kind, key):
    for index, node in enumerate(nodes):
        if node.get(kind) == key:return nodes, index, node
        if 'folder' in node:
            try:return locate(node['children'], kind, key)
            except KeyError:pass
    raise KeyError(key)


def children(nodes, parent):
    return locate(nodes, 'folder', parent)[2]['children'] if parent else nodes


def move(nodes, kind, key, parent, index):
    result = deepcopy(nodes)
    source, old, node = locate(result, kind, key)
    if kind == 'folder' and parent in {n.get('folder') for n, _ in walk([node])}:
        raise ValueError('A folder cannot move inside itself')
    destination = children(result, parent)
    if destination is source and index > old:index -= 1
    source.pop(old)
    destination.insert(max(0, min(index, len(destination))), node)
    validate(result)
    return result


def paths(nodes):
    return {node['playlist']:list(path) for node, path in walk(nodes) if 'playlist' in node}
