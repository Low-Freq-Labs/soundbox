"""Cached playlist correspondence and occurrence deltas, never media authority.

Callers supply independently validated song links. A playlist association does
not prove audio bytes, authorize file removal, or give an unknown entry an owner.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from . import manifest
from .library import Library, Playlist
from .playlist_names import normalize_playlist_name

MAX_MEMBERS = 16_384


@dataclass(frozen=True)
class SourcePlaylist:
    key: str
    native_id: str
    uuid: str
    name: str
    members: tuple[str, ...]
    identity_error: str = ""
    label: str = ""
    identities: tuple[tuple[str, str, str, str, str], ...] = ()


@dataclass(frozen=True)
class DevicePlaylist:
    playlist_id: int
    name: str
    members: tuple[int, ...]


@dataclass(frozen=True)
class PlaylistBinding:
    key: str
    native_id: str
    uuid: str
    device_playlist_id: int
    name: str
    members: tuple[tuple[str, int], ...]
    source_label: str = ""
    source_members: tuple[tuple[str, str, str, str, str], ...] = ()

    @property
    def has_omissions(self) -> bool:
        return bool(self.source_members and
                    Counter(row[0] for row in self.source_members) != Counter(local for local, _dbid in self.members))


@dataclass(frozen=True)
class PlaylistOccurrence:
    local_id: str
    dbid: int | None
    source_index: int | None
    device_index: int | None


@dataclass(frozen=True)
class PlaylistBindingComparison:
    source: SourcePlaylist
    device_playlist_id: int | None = None
    binding: PlaylistBinding | None = None
    basis: str = ""
    retained: tuple[PlaylistOccurrence, ...] = ()
    added: tuple[PlaylistOccurrence, ...] = ()
    removed: tuple[PlaylistOccurrence, ...] = ()
    reordered: bool = False
    renamed: bool = False
    unknown_device_ids: tuple[int, ...] = ()
    unmapped_source_ids: tuple[str, ...] = ()
    ambiguous_source_ids: tuple[str, ...] = ()
    detail: str = ""
    unexpected_device_ids: tuple[int, ...] = ()

    @property
    def desired_name(self) -> str:
        return self.source.name.strip()

    @property
    def recognized(self) -> bool:
        return self.device_playlist_id is not None

    @property
    def membership_safe(self) -> bool:
        return (self.recognized and not self.unknown_device_ids and not self.unexpected_device_ids
                and not self.ambiguous_source_ids)

    @property
    def needs_sync(self) -> bool:
        return self.recognized and bool(self.added or self.removed or self.reordered or self.renamed
                                        or self.unknown_device_ids or self.unexpected_device_ids)


def source_playlist_identity(library: Library, playlist: Playlist) -> SourcePlaylist:
    """Freeze selected effective membership and the actual native playlist UUID."""
    identity = str(playlist.pid or "")
    uuid = ""; error = ""
    if identity and library.source_kind == 'rekordbox-db':
        nodes = library.source_playlist_catalog.get('nodes', ())
        matches = [row for row in nodes if str(row.get('ID', '')) == identity
                   and row.get('rb_local_deleted', 0) == 0]
        if len(matches) != 1 or not isinstance(matches[0].get('UUID'), str) or not matches[0]['UUID']:
            error = 'The native playlist identity is unavailable or ambiguous'
        else:
            uuid = matches[0]['UUID']
    tracks = tuple(library.resolve(playlist))
    return SourcePlaylist(manifest.playlist_key(playlist), identity, uuid, playlist.name,
                          tuple(track.track_id for track in tracks), error, playlist.display_path,
                          tuple((track.track_id, track.native_id, track.source_uuid,
                                 track.source_native_path or str(track.path), str(track.path)) for track in tracks))


def _text(value: object, *, nonempty: bool = False) -> bool:
    return type(value) is str and '\0' not in value and len(value) <= 4096 and (not nonempty or bool(value))


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def playlist_binding_from_dict(data: object) -> PlaylistBinding:
    """Strict disposable-witness decoder; duplicate song occurrences stay legal."""
    fields = set(PlaylistBinding.__dataclass_fields__)
    if (not isinstance(data, Mapping) or not fields - {'source_label', 'source_members'} <= set(data)
            or not set(data) <= fields):
        raise ValueError('Invalid playlist binding')
    if (not _text(data['key'], nonempty=True) or not _text(data['native_id'])
            or not _text(data['uuid']) or not _text(data['name']) or not _text(data.get('source_label', ''))
            or not _positive(data['device_playlist_id'])
            or not isinstance(data['members'], (list, tuple)) or len(data['members']) > MAX_MEMBERS):
        raise ValueError('Invalid playlist binding identity')
    members = []; links: dict[str, int] = {}; reverse: dict[int, str] = {}
    for pair in data['members']:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not _text(pair[0], nonempty=True) or not _positive(pair[1])):
            raise ValueError('Invalid playlist binding occurrence')
        local, dbid = pair
        if (local in links and links[local] != dbid or dbid in reverse and reverse[dbid] != local):
            raise ValueError('Ambiguous playlist binding song correspondence')
        links[local] = dbid; reverse[dbid] = local; members.append((local, dbid))
    source_members = _source_members(data.get('source_members', ()))
    if source_members:
        expected = Counter(row[0] for row in source_members)
        if Counter(local for local, _dbid in members) - expected:
            raise ValueError('Exported occurrences exceed the source playlist baseline')
    return PlaylistBinding(data['key'], data['native_id'], data['uuid'],
                           data['device_playlist_id'], data['name'], tuple(members),
                           data.get('source_label', ''), source_members)


def _source_members(rows) -> tuple[tuple[str, str, str, str, str], ...]:
    if not isinstance(rows, (tuple, list)) or len(rows) > MAX_MEMBERS:
        raise ValueError('Invalid source playlist occurrence baseline')
    output: list[tuple[str, str, str, str, str]] = []
    identities: dict[str, tuple[str, str, str, str, str]] = {}
    for row in rows:
        if (not isinstance(row, (tuple, list)) or len(row) != 5
                or not _text(row[0], nonempty=True) or any(not _text(value) for value in row[1:])
                or any(not value.startswith('/') for value in row[3:])):
            raise ValueError('Invalid source playlist occurrence identity')
        identity = row[0], row[1], row[2], row[3], row[4]
        if row[0] in identities and identities[row[0]] != identity:
            raise ValueError('Ambiguous source playlist occurrence identity')
        identities[row[0]] = identity; output.append(identity)
    return tuple(output)


def _desired_occurrences(source: SourcePlaylist, binding: PlaylistBinding | None) -> tuple[tuple[int, str], ...]:
    """Ignore only exact unchanged occurrences deliberately omitted at export.

    This is an advisory delta, never a codec or media-ownership certificate.
    Explicit Sync of an omission-bearing binding must reevaluate source media
    through the fresh full planner; the current source playlist stays intact.
    """
    if binding is None or not binding.source_members or not source.identities:
        return tuple(enumerate(source.members))
    exported = Counter(local for local, _dbid in binding.members)
    omitted: Counter[tuple[str, str, str, str, str]] = Counter()
    for identity in binding.source_members:
        if exported[identity[0]]:
            exported[identity[0]] -= 1
        else:
            omitted[identity] += 1
    desired = []
    for index, (local, identity) in enumerate(zip(source.members, source.identities, strict=True)):
        if omitted[identity]:
            omitted[identity] -= 1
        else:
            desired.append((index, local))
    return tuple(desired)


def _validate_inputs(sources: Sequence[SourcePlaylist], devices: Sequence[DevicePlaylist]) -> None:
    for source in sources:
        if (not _text(source.key, nonempty=True) or not _text(source.native_id) or not _text(source.uuid)
                or not _text(source.name) or not _text(source.identity_error) or not _text(source.label)
                or not isinstance(source.members, tuple) or len(source.members) > MAX_MEMBERS
                or any(not _text(local, nonempty=True) for local in source.members)):
            raise ValueError('Invalid source playlist identity')
        identities = _source_members(source.identities)
        if identities and tuple(row[0] for row in identities) != source.members:
            raise ValueError('Source playlist occurrence identities changed')
    for device in devices:
        if (not _positive(device.playlist_id) or not _text(device.name)
                or not isinstance(device.members, tuple) or len(device.members) > MAX_MEMBERS
                or any(not _positive(dbid) for dbid in device.members)):
            raise ValueError('Invalid device playlist identity')


def _delta(source: SourcePlaylist, device: DevicePlaylist, links: Mapping[str, int],
           ambiguous: set[str], binding: PlaylistBinding | None, basis: str) -> PlaylistBindingComparison:
    inverse = {dbid: local for local, dbid in links.items()}
    positions: dict[str, deque[int]] = defaultdict(deque)
    unknown = []; unexpected = []; protected = set()
    baseline = Counter(binding.members if binding else ())
    desired_occurrences = _desired_occurrences(source, binding)
    desired_members = tuple(local for _index, local in desired_occurrences)
    desired = Counter(desired_members); occurrences: Counter[tuple[str, int]] = Counter()
    for index, dbid in enumerate(device.members):
        local = inverse.get(dbid)
        if local is None:unknown.append(dbid)
        else:
            pair = (local, dbid)
            if occurrences[pair] >= max(baseline[pair], desired[local]):
                unexpected.append(dbid);protected.add(index)
            positions[local].append(index);occurrences[pair] += 1
    retained = []; added = []; used = set()
    for index, local in desired_occurrences:
        linked_dbid = links.get(local)
        if positions[local]:
            device_index = positions[local].popleft(); used.add(device_index)
            retained.append(PlaylistOccurrence(local, linked_dbid, index, device_index))
        else:
            added.append(PlaylistOccurrence(local, linked_dbid, index, None))
    removed = tuple(PlaylistOccurrence(inverse[dbid], dbid, None, index)
                    for index, dbid in enumerate(device.members)
                    if index not in used and index not in protected and dbid in inverse)
    order = [occurrence.device_index for occurrence in retained if occurrence.device_index is not None]
    if binding is None:
        # Playlist identity does not depend on proving every song. Retain only
        # selected, mapped occurrences; foreign/unknown entries gain no baseline
        # and still block membership replacement on the next comparison.
        binding = PlaylistBinding(source.key, source.native_id, source.uuid, device.playlist_id,
                                  device.name, tuple((inverse[dbid], dbid)
                                      for index, dbid in enumerate(device.members) if index in used))
    desired_name = source.name.strip()
    return PlaylistBindingComparison(source, device.playlist_id, binding, basis, tuple(retained),
        tuple(added), removed, order != sorted(order), device.name != desired_name, tuple(unknown),
        tuple(dict.fromkeys(local for local in desired_members if local not in links)),
        tuple(dict.fromkeys(local for local in desired_members if local in ambiguous)),
        'Unknown device entries are preserved; membership needs reconciliation' if unknown else
        'External device playlist additions are preserved; membership needs reconciliation' if unexpected else
        'Song correspondence is ambiguous' if any(local in ambiguous for local in desired_members) else '',
        tuple(unexpected))


def compare_playlist_bindings(
        sources: Sequence[SourcePlaylist], devices: Sequence[DevicePlaylist], *,
        track_links: Mapping[str, int], bindings: Sequence[PlaylistBinding] = ()) -> tuple[PlaylistBindingComparison, ...]:
    """Associate selected playlists, then compare occurrences by proven song IDs.

    Normalized name/count is a bootstrap candidate only. A count-different
    bootstrap needs at least one existing occurrence linked to a selected song.
    Unknown/external entries still prevent member replacement. A saved binding
    survives source count/order/name changes; unknown device songs block member
    replacement and remain available for separate safe metadata operations.
    """
    _validate_inputs(sources, devices)
    if any(not _text(local, nonempty=True) or not _positive(dbid) for local, dbid in track_links.items()):
        raise ValueError('Invalid cached song correspondence')
    claims = Counter(track_links.values())
    ambiguous = {local for local, dbid in track_links.items() if claims[dbid] != 1}
    links = {local: dbid for local, dbid in track_links.items() if local not in ambiguous}
    inverse = {dbid: local for local, dbid in links.items()}
    by_key: dict[str, list[PlaylistBinding]] = defaultdict(list)
    bound_owners: dict[int, set[str]] = defaultdict(set)
    for saved in bindings:
        saved = playlist_binding_from_dict({field: getattr(saved, field) for field in PlaylistBinding.__dataclass_fields__})
        by_key[saved.key].append(saved); bound_owners[saved.device_playlist_id].add(saved.key)
    device_ids = Counter(device.playlist_id for device in devices)
    device_by_name: dict[str, list[DevicePlaylist]] = defaultdict(list)
    device_by_id: dict[int, list[DevicePlaylist]] = defaultdict(list)
    for device in devices:
        device_by_name[normalize_playlist_name(device.name)].append(device)
        device_by_id[device.playlist_id].append(device)
    source_keys = Counter(source.key for source in sources)
    source_names = Counter(normalize_playlist_name(source.name) for source in sources)
    result = []
    for source in sources:
        def refused(detail):return PlaylistBindingComparison(source, detail=detail)
        if source.identity_error:
            result.append(refused(source.identity_error));continue
        if source_keys[source.key] != 1:
            result.append(refused('Source playlist identity is ambiguous'));continue
        remembered = by_key.get(source.key, [])
        binding = None; target = None; basis = ''
        if remembered:
            if len(remembered) != 1:
                result.append(refused('Saved playlist association is ambiguous'));continue
            binding = remembered[0]
            if (binding.native_id, binding.uuid) != (source.native_id, source.uuid):
                result.append(refused('The source playlist identity changed; the saved association is retained'));continue
            matches = device_by_id.get(binding.device_playlist_id, [])
            if len(matches) != 1:
                result.append(refused('The associated device playlist is missing or ambiguous'));continue
            target = matches[0];basis = 'binding'
        else:
            name = normalize_playlist_name(source.name)
            named = device_by_name.get(name, [])
            if source_names[name] != 1 or len(named) > 1:
                result.append(refused('The normalized playlist name is ambiguous'));continue
            if len(named) == 1 and len(named[0].members) == len(source.members):
                target = named[0];basis = 'name-count'
            else:
                selected_members = set(source.members)
                if (len(named) == 1
                        and any(dbid in inverse and inverse[dbid] in selected_members for dbid in named[0].members)):
                    target = named[0];basis = 'name-linked-members'
            if target is None:
                result.append(refused('No unique playlist name/count or cached member correspondence'));continue
        if device_ids[target.playlist_id] != 1 or bound_owners[target.playlist_id] - {source.key}:
            result.append(refused('The device playlist identity has another or ambiguous association'));continue
        result.append(_delta(source, target, links, ambiguous, binding, basis))
    owners = Counter(row.device_playlist_id for row in result if row.recognized)
    return tuple(replace(row, device_playlist_id=None, binding=None,
                         detail='Selected source playlists compete for the same device playlist')
                 if row.recognized and owners[row.device_playlist_id] > 1 else row for row in result)
