"""Tag-assignment recency for smart-playlist presentation only.

Native memberships/rules, Collection timestamps and export order stay untouched.
The stored ranks grant no native edit or playlist authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any
from xml.etree import ElementTree

from .library import Library, Track

KEY = 'smart_playlist_recency'
_MAX_ENTRIES = 100_000
_MAX_LISTS = 4096
_MAX_SEQUENCE = 2**63 - 1


@dataclass(frozen=True)
class Assignment:
    source: str
    library_id: str
    tag_id: str
    tag_uuid: str
    targets: tuple[tuple[str, str], ...]
    playlists: tuple[tuple[str, str, str], ...]


def _smart_nodes(library: Library) -> dict[str, dict]:
    nodes = library.source_playlist_catalog.get('nodes', [])
    result: dict[str, dict] = {}
    duplicates = set()
    for node in nodes:
        if (node.get('Attribute') == 4 and node.get('rb_local_deleted') == 0
                and isinstance(node.get('ID'), str) and node.get('UUID')):
            identity = node['ID']
            if identity in result:
                duplicates.add(identity)
            result[identity] = node
    return {identity: node for identity, node in result.items() if identity not in duplicates}


def _positive_tags(rule: Any) -> set[str]:
    if (not isinstance(rule, str) or len(rule) > 65536
            or '<!DOCTYPE' in rule.upper() or '<!ENTITY' in rule.upper()):
        return set()
    try:
        root = ElementTree.fromstring(rule)
        if root.tag != 'NODE' or root.get('LogicalOperator') not in ('1', '2'):
            return set()
        tags = set()
        for condition in root.findall('CONDITION'):
            if condition.get('PropertyName') == 'myTag' and condition.get('Operator') == '8':
                try:
                    native_id = int(condition.get('ValueLeft', ''))
                except ValueError:
                    continue
                if native_id < 0:
                    native_id += 2**32
                if 0 <= native_id < 2**32:
                    tags.add(str(native_id))
        return tags
    except (ElementTree.ParseError, ValueError):
        pass
    return set()


def _contains_tag(rule: Any, tag_id: str) -> bool:
    return tag_id in _positive_tags(rule)


def capture(library: Library | None, selected_ids: list[str], tag_id: str,
            present: bool) -> Assignment | None:
    """Capture only actual additions and positive matching rules, without I/O."""
    if (not present or library is None or library.source_kind != 'rekordbox-db'
            or not library.source_library_id):
        return None
    tags = [tag for tag in library.source_tags if tag.tag_id == tag_id and tag.source_uuid]
    if len(tags) != 1:
        return None
    targets = []
    for identity in dict.fromkeys(selected_ids):
        track = library.tracks_by_id.get(identity)
        if (track is not None and not track.imported and track.source_uuid
                and track.source_tag_ids is not None and tag_id not in track.source_tag_ids
                and identity not in library.source_binding_issues):
            targets.append((track.native_id, track.source_uuid))
    playlists = tuple((identity, node['UUID'], node['SmartList'])
                      for identity, node in _smart_nodes(library).items()
                      if _contains_tag(node.get('SmartList'), tag_id))
    if not targets or not playlists:
        return None
    return Assignment(str(library.source), library.source_library_id, tag_id, tags[0].source_uuid,
                      tuple(targets), playlists)


def _scope(library: Library, identity: str, uuid: str, rule: str) -> str:
    positive = _positive_tags(rule)
    tags = sorted((tag.tag_id, tag.source_uuid) for tag in library.source_tags
                  if tag.tag_id in positive)
    return json.dumps([str(library.source), library.source_library_id, identity, uuid,
                       hashlib.sha256(rule.encode('utf-8')).hexdigest(), tags],
                      separators=(',', ':'))


def _state(value: Any) -> dict:
    if value is None:
        return dict(version=1, sequence=0, playlists={})
    if (not isinstance(value, dict) or set(value) != {'version', 'sequence', 'playlists'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['sequence']) is not int or not 0 <= value['sequence'] <= _MAX_SEQUENCE
            or not isinstance(value['playlists'], dict) or len(value['playlists']) > _MAX_LISTS):
        raise ValueError('Saved smart-playlist display order is invalid; previous order retained')
    count = 0
    for scope, ranks in value['playlists'].items():
        if (not isinstance(scope, str) or len(scope) > 32768 or not isinstance(ranks, dict)):
            raise ValueError('Saved smart-playlist display order is invalid; previous order retained')
        count += len(ranks)
        if count > _MAX_ENTRIES:
            raise ValueError('Saved smart-playlist display order exceeds its safety limit')
        for uuid, rank in ranks.items():
            if (not isinstance(uuid, str) or not uuid or len(uuid) > 256 or '\0' in uuid
                    or type(rank) is not int or not 1 <= rank <= value['sequence']):
                raise ValueError('Saved smart-playlist display order is invalid; previous order retained')
    return value


def record(previous: Any, assignment: Assignment | None, verified: Library) -> Any:
    """Return detached changed maps only after exact native membership readback."""
    if (assignment is None or verified.source_kind != 'rekordbox-db'
            or (str(verified.source), verified.source_library_id)
                != (assignment.source, assignment.library_id)):
        return previous
    if not any((tag.tag_id, tag.source_uuid) == (assignment.tag_id, assignment.tag_uuid)
               for tag in verified.source_tags):
        return previous
    targets = set(assignment.targets)
    tracks = {(track.native_id, track.source_uuid): track for track in verified.tracks_by_id.values()
              if track.source_tag_ids is not None and assignment.tag_id in track.source_tag_ids
              and track.track_id not in verified.source_binding_issues and not track.imported}
    targets.intersection_update(tracks)
    if not targets:
        return previous
    nodes = _smart_nodes(verified)
    playlists = {playlist.pid: playlist for playlist in verified.playlists}
    promotions = {}
    for identity, uuid, rule in assignment.playlists:
        node = nodes.get(identity)
        playlist = playlists.get(identity)
        if (node is None or playlist is None or node['UUID'] != uuid or node.get('SmartList') != rule):
            continue
        members = {(track.native_id, track.source_uuid) for key in playlist.keys
                   if (track := verified.tracks_by_id.get(key)) is not None}
        matched = {target[1] for target in targets & members}
        if matched:
            promotions[_scope(verified, identity, uuid, rule)] = matched
    if not promotions:
        return previous
    state = _state(previous)
    if state['sequence'] == _MAX_SEQUENCE:
        raise ValueError('Smart-playlist display-order clock is exhausted; previous order retained')
    sequence = state['sequence'] + 1
    ranks = dict(state['playlists'])
    for scope, uuids in promotions.items():
        ranks[scope] = {**ranks.get(scope, {}), **dict.fromkeys(uuids, sequence)}
    result = dict(version=1, sequence=sequence, playlists=ranks)
    _state(result)
    return result


def order_rows(rows: list[dict], library: Library, playlist_id: str, value: Any) -> list[dict]:
    """Latest matching native assignment first; stable ties and legacy fallback.

    Source dates cover assignments made before installation and in rekordbox.
    Local ranks only break date ties or order rows whose source has no dates.
    Header sorts run later. No source membership or Collection order is changed.
    """
    if library.source_kind != 'rekordbox-db' or not library.source_library_id:
        return rows
    node = _smart_nodes(library).get(playlist_id)
    if node is None or not isinstance(node.get('SmartList'),str):
        return rows
    tag_ids = _positive_tags(node['SmartList'])
    tag_ids.intersection_update(tag.tag_id for tag in library.source_tags)
    if not tag_ids:
        return rows
    playlist = next((item for item in library.playlists if item.pid == playlist_id), None)
    if playlist is None:
        return rows
    members = set(playlist.keys)
    try:
        ranks = _state(value)['playlists'].get(_scope(library, playlist_id, node['UUID'],node['SmartList']), {})
    except ValueError:
        ranks = {}
    def rank(row: dict) -> tuple[float, int]:
        track: Track | None = library.tracks_by_id.get(row.get('id', ''))
        if (track is None or track.track_id not in members or track.imported
                or not track.source_uuid or track.track_id in library.source_binding_issues):
            return (-1, 0)
        assigned = tag_ids.intersection(track.source_tag_ids or ())
        dates = [timestamp for tag_id, timestamp in track.source_tag_times.items()
                 if tag_id in assigned and type(timestamp) in (int, float)
                 and 0 <= timestamp < 253402300800 and math.isfinite(timestamp)]
        return (max(dates, default=-1), ranks.get(track.source_uuid, 0) if assigned else 0)
    ranked = [(rank(row), row) for row in rows]
    if not any(date >= 0 or local for (date, local), _ in ranked):
        return rows
    return [row for _, row in sorted(ranked, key=lambda item: item[0], reverse=True)]
