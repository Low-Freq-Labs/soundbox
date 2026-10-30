"""Native-reference playlist XML edits in memory; no file/publication API.

Patch only the selected NODE token or append one root node. Retained entries,
unknown attributes, product version, comments, whitespace and all other bytes
remain intact. Timestamp is an independent clock value, never a SQL conversion.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_NODE = re.compile(rb'<NODE\b(?:"[^"]*"|\'[^\']*\'|[^\'">])*/>')
_CLOSE = re.compile(rb'(?m)^[\t ]*</PLAYLISTS[\t ]*>')
_TIME = re.compile(rb'(\bTimestamp\s*=\s*)([\'"])([0-9]+)\2')


def _id(value):
    if (type(value) is not str or not value.isascii() or not value.isdecimal()
            or not 0 < int(value) < 2**32 or str(int(value)) != value):
        raise ValueError('Expected canonical uint32 playlist identity')
    return int(value)


def _parse(payload):
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
        rows[identity] = (node.attrib, token.span())
    return rows, closes[0]


def identities(payload):
    return frozenset(_parse(payload)[0])


def _target(payload, playlist_id):
    rows, _ = _parse(payload)
    try:
        attributes, span = rows[_id(playlist_id)]
    except KeyError as exc:
        raise ValueError('Expected XML playlist disappeared') from exc
    if any(attributes.get(k) != v for k, v in
           (('ParentId','0'), ('Attribute','0'), ('Lib_Type','0'), ('CheckType','0'))):
        raise ValueError('Only the observed ordinary root XML protocol is accepted')
    old = attributes.get('Timestamp', '')
    if not old.isascii() or not old.isdecimal() or str(int(old)) != old:
        raise ValueError('Unverified XML clock')
    return attributes, span


def append_root(payload, playlist_id):
    rows, closing = _parse(payload)
    identity = _id(playlist_id)
    if identity in rows:
        raise ValueError('Playlist ID collides with a retained XML entry')
    newline = b'\r\n' if payload[:closing.start()].endswith(b'\r\n') else b'\n'
    if closing.start() and not payload[:closing.start()].endswith(newline):
        raise ValueError('Unverified XML append location')
    node = (f'    <NODE Id="{identity:X}" ParentId="0" Attribute="0" Timestamp="0" '
            'Lib_Type="0" CheckType="0"/>').encode()+newline
    result = payload[:closing.start()]+node+payload[closing.start():]
    _target(result, playlist_id)
    if identities(result) != identities(payload) | {identity}:
        raise ValueError('XML append changed unrelated identities')
    return result


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


def validate_transition(before, after, changes):
    """Prove the complete lexical XML delta is confined to planned root IDs."""
    final, _ = _parse(after)
    created = [];targets = {};deleted = set()
    for change in changes:
        if change['table'] not in ('djmdPlaylist', 'djmdSongPlaylist'):
            continue
        old, new = change['before'], change['after']
        rows = [r for r in (old, new) if r is not None]
        if change['table'] == 'djmdPlaylist':
            identity = change['id']
            if any(r.get('ID') != identity or r.get('ParentID') != 'root'
                   or r.get('Attribute') != 0 for r in rows):
                raise ValueError('Unverified XML playlist operation')
            if old is None:created.append(identity)
            if new is None:deleted.add(identity)
        else:
            ids = {r['PlaylistID'] for r in rows}
            if len(ids) != 1:raise ValueError('Unproven membership transfer')
            identity = ids.pop()
        _id(identity);targets[identity] = None
    if set(created) & deleted:
        raise ValueError('Unproven create/delete batch')
    candidate = before
    for identity in created:candidate = append_root(candidate, identity)
    for identity in targets:
        if identity in deleted:
            retain_deleted(candidate, identity);continue
        attributes, _ = _target(candidate, identity)
        try:timestamp = int(final[_id(identity)][0]['Timestamp'])
        except (KeyError, ValueError) as exc:raise ValueError('Missing XML target clock') from exc
        if timestamp != int(attributes['Timestamp']):candidate = touch(candidate, identity, timestamp)
    if candidate != after:
        raise ValueError('XML transition changed unplanned bytes')
