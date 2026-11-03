"""Native-reference playlist XML edits in memory; no file/publication API.

Patch only the selected NODE token or append one root node. Retained entries,
unknown attributes, product version, comments, whitespace and all other bytes
remain intact. Timestamp is an independent clock value, never a SQL conversion.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_NODE = re.compile(rb'<NODE\b(?:"[^"]*"|\'[^\']*\'|[^\'">])*/>')
_CLOSE = re.compile(rb'(?m)^[\t ]*</PLAYLISTS[\t ]*>')
_TIME = re.compile(rb'(\bTimestamp\s*=\s*)([\'"])([0-9]+)\2')
_PARSE_SESSION: ContextVar[OrderedDict|None]=ContextVar('soundbox_playlist_xml_parse_session',default=None)


@contextmanager
def parsing_session():
    """Reuse four exact immutable XML inputs during one pure plan only."""
    if _PARSE_SESSION.get() is not None:
        yield;return
    token=_PARSE_SESSION.set(OrderedDict())
    try:yield
    finally:_PARSE_SESSION.reset(token)


def _id(value):
    if (type(value) is not str or not value.isascii() or not value.isdecimal()
            or not 0 < int(value) < 2**32 or str(int(value)) != value):
        raise ValueError('Expected canonical uint32 playlist identity')
    return int(value)


def _parse(payload):
    if type(payload) is not bytes:raise ValueError('Unverified playlist XML framing')
    cache=_PARSE_SESSION.get()
    if cache is not None and payload in cache:
        cache.move_to_end(payload)
        return cache[payload]
    result=_parse_uncached(payload)
    if cache is not None:
        cache[payload]=result
        if len(cache)>4:cache.popitem(last=False)
    return result


def _parse_uncached(payload):
    if (type(payload) is not bytes or len(payload) > 4*1024*1024
            or re.search(rb'<!\s*(?:DOCTYPE|ENTITY)\b', payload, re.I)):
        raise ValueError('Unverified playlist XML framing')
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError('Invalid playlist XML') from exc
    lists = root.findall('PLAYLISTS')
    if (root.tag != 'MASTER_PLAYLIST' or root.get('Version') != '3.0.0'
            or root.get('AutomaticSync') != '0' or len(lists) != 1
            or any(e.tag != 'NODE' or len(e) for e in lists[0])):
        raise ValueError('Unverified playlist XML schema/sync state')
    tokens = list(_NODE.finditer(payload));closes = list(_CLOSE.finditer(payload))
    if len(tokens) != len(lists[0]) or len(closes) != 1:
        raise ValueError('Unverified playlist XML node framing')
    rows = {}
    for node, token in zip(lists[0], tokens, strict=True):
        try:
            identity = int(node.attrib['Id'], 16)
        except (ValueError, KeyError) as exc:
            raise ValueError('Invalid XML playlist identity') from exc
        # Other library kinds can carry 64-bit IDs. Preserve those opaque rows;
        # only a selected local-library target is constrained to uint32 below.
        if not 0 < identity < 2**64 or identity in rows:
            raise ValueError('Ambiguous XML playlist identity')
        # A token appearing inside an opaque field/comment must not be mistaken
        # for the parsed node. Independently parse each lexical token.
        if ET.fromstring(token.group()).attrib != node.attrib:
            raise ValueError('XML lexical/semantic node mismatch')
        rows[identity] = (MappingProxyType(dict(node.attrib)), token.span())
    return MappingProxyType(rows), closes[0]


def identities(payload):
    return frozenset(_parse(payload)[0])


def _target(payload, playlist_id):
    rows, _ = _parse(payload)
    try:
        attributes, span = rows[_id(playlist_id)]
    except KeyError as exc:
        raise ValueError('Expected XML playlist disappeared') from exc
    if (attributes.get('Attribute') not in ('0','1')
            or any(attributes.get(k)!=v for k,v in (('Lib_Type','0'),('CheckType','0')))):
        raise ValueError('Only local regular playlist/folder XML is accepted')
    try:
        parent=int(attributes.get('ParentId',''),16)
    except ValueError as exc:raise ValueError('Unverified XML parent identity') from exc
    if not 0<=parent<2**32:raise ValueError('Unverified XML parent identity')
    old = attributes.get('Timestamp', '')
    if not old.isascii() or not old.isdecimal() or str(int(old)) != old:
        raise ValueError('Unverified XML clock')
    return attributes, span


def append_node(payload, playlist_id, parent='root', attribute=0):
    rows, closing = _parse(payload)
    identity = _id(playlist_id)
    if identity in rows:
        raise ValueError('Playlist ID collides with a retained XML entry')
    newline = b'\r\n' if payload[:closing.start()].endswith(b'\r\n') else b'\n'
    if closing.start() and not payload[:closing.start()].endswith(newline):
        raise ValueError('Unverified XML append location')
    parent_id=0 if parent=='root' else _id(parent)
    if attribute not in (0,1):raise ValueError('Unverified XML node type')
    node = (f'    <NODE Id="{identity:X}" ParentId="{parent_id:X}" Attribute="{attribute}" Timestamp="0" '
            'Lib_Type="0" CheckType="0"/>').encode()+newline
    result = payload[:closing.start()]+node+payload[closing.start():]
    _target(result, playlist_id)
    if identities(result) != identities(payload) | {identity}:
        raise ValueError('XML append changed unrelated identities')
    return result


def append_root(payload, playlist_id):
    return append_node(payload,playlist_id)


def touch(payload, playlist_id, timestamp):
    attributes, (start, end) = _target(payload, playlist_id)
    if type(timestamp) is not int or not int(attributes['Timestamp']) < timestamp < 2**63:
        raise ValueError('Expected a newer independent XML clock value')
    token = payload[start:end]
    replacement, count = _TIME.subn(lambda m:m[1]+m[2]+str(timestamp).encode()+m[2], token)
    if count != 1:
        raise ValueError('Unverified XML timestamp token')
    result = payload[:start]+replacement+payload[end:]
    _target(result, playlist_id)
    return result


def retain_deleted(payload, playlist_id):
    _target(payload, playlist_id)
    return payload


def validate_transition(before, after, changes, *, missing_targets=(),touched_playlists=()):
    """Prove the complete lexical XML delta is confined to planned root IDs."""
    final, _ = _parse(after)
    original, _ = _parse(before)
    created = [];targets: dict[str, None] = {};deleted = set()
    for identity in touched_playlists:_target(before,identity);targets[identity]=None
    for change in changes:
        if change['table'] not in ('djmdPlaylist', 'djmdSongPlaylist'):
            continue
        old, new = change['before'], change['after']
        rows = [r for r in (old, new) if r is not None]
        if change['table'] == 'djmdPlaylist':
            if old is not None and new is not None:
                fields={k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}
                if fields<={'Seq','updated_at','rb_local_usn'}:
                    continue  # Native sibling renumbering leaves its XML untouched.
            identity = change['id']
            if any(r.get('ID') != identity or r.get('Attribute') not in (0,1) for r in rows):
                raise ValueError('Unverified XML playlist operation')
            if old is None:created.append((identity,new['ParentID'],new['Attribute']))
            if new is None or new.get('rb_local_deleted')==1:deleted.add(identity)
        else:
            ids = {r['PlaylistID'] for r in rows}
            if len(ids) != 1:raise ValueError('Unproven membership transfer')
            identity = ids.pop()
        _id(identity);targets[identity] = None
    if {r[0] for r in created} & deleted:
        raise ValueError('Unproven create/delete batch')
    candidate = before
    for identity,parent,attribute in created:candidate = append_node(candidate,identity,parent,attribute)
    for identity in targets:
        if _id(identity) not in original and identity in missing_targets:
            if _id(identity) not in final:continue
            if not isinstance(missing_targets,dict):raise ValueError('Legacy XML parent/type proof is missing')
            parent,attribute=missing_targets[identity]
            candidate=append_node(candidate,identity,parent,attribute)
        if identity in deleted:
            retain_deleted(candidate, identity);continue
        attributes, _ = _target(candidate, identity)
        try:timestamp = int(final[_id(identity)][0]['Timestamp'])
        except (KeyError, ValueError) as exc:raise ValueError('Missing XML target clock') from exc
        if timestamp != int(attributes['Timestamp']):candidate = touch(candidate, identity, timestamp)
    if candidate != after:
        raise ValueError('XML transition changed unplanned bytes')
