"""Immutable GUI-to-export capture, separate from native/device authority.

Only selected musical records enter explicit USB exports. Firmware reconciliation
may need the complete lightweight identity inventory to resolve its managed set
after reading the device. Neither path copies native cue/catalog graphs: native
CDJ metadata is independently reacquired by its existing source reader.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import json
import math
from pathlib import Path

from .library import Library, Playlist, Track
from .metadata import ArtistRegistry


def _removal_memberships(values, selected=None) -> dict[str, list[str]]:
    """Freeze only this typed membership domain; musical values stay strict."""
    if not isinstance(values, Mapping):
        raise ValueError('Export playlist exclusions require a membership mapping')
    output = {}
    for key, members in values.items():
        if selected is not None and key not in selected:
            continue
        if (type(key) is not str or not isinstance(members, (set, frozenset, list, tuple))
                or any(type(member) is not str for member in members)):
            raise ValueError('Export playlist exclusions require string membership identities')
        output[key] = sorted(set(members))
    return output


def source_paths(library: Library, standalone=()) -> tuple[Path, ...]:
    """Complete overlap boundary, including unselected and hidden sources.

    This captures path values only; it performs no stat, resolve or audio read.
    A materialized selection retains the original full boundary independently
    of its smaller tracks dictionary.
    """
    paths: dict[str, Path] = {}
    def add(raw):
        if str(raw) in paths:
            return
        path = raw if isinstance(raw, Path) else Path(raw)
        # Path already performs lexical dot/separator normalization. POSIX
        # string identity retains its equality rules without repeatedly
        # constructing and hashing identical analysis/native Path objects.
        paths.setdefault(str(path), path)
    for path in getattr(library, 'export_source_paths', ()):
        add(path)
    add(library.source)
    for track in (*library.tracks_by_id.values(), *standalone):
        add(track.path)
        if track.source_analysis_path:
            add(track.source_analysis_path)
        native_path = getattr(track, 'source_native_path', '')
        if native_path:
            add(native_path)
    return tuple(paths.values())


@dataclass(frozen=True)
class ExportInputs:
    """Worker-owned materialization; no objects refer to the live GUI model."""
    library: Library
    playlists: tuple[Playlist, ...]
    standalone: tuple[Track, ...]
    extra: tuple[Playlist, ...]
    titles: dict
    artists: dict
    display_rows: dict[str, dict] | None = None


@dataclass(frozen=True)
class ExportSnapshot:
    """Only bytes and immutable paths cross the worker-admission boundary."""
    payload: bytes
    sources: tuple[Path, ...]

    def materialize(self) -> ExportInputs:
        data = json.loads(self.payload)
        tracks = {row['track_id']: Track(**row) for row in data.pop('tracks')}
        playlists = tuple(Playlist(**row) for row in data.pop('selected'))
        extra = tuple(Playlist(**row) for row in data.pop('extra'))
        standalone = tuple(Track(**row) for row in data.pop('standalone'))
        titles, artists = data.pop('titles'), data.pop('artists')
        display_rows = data.pop('display_rows', None)
        registry = data.pop('registry')
        info, facts = data.pop('info_overrides'), data.pop('media_facts')
        data['playlists'] = [Playlist(**row) for row in data['playlists']]
        data['hidden_ids'] = set(data['hidden_ids'])
        data['removed_by_playlist'] = {key: set(members) for key, members in
            _removal_memberships(data['removed_by_playlist']).items()}
        library = Library(tracks, **data)
        if registry is not None:
            library.artist_registry = ArtistRegistry(registry['canon'], registry['trusted'])
        setattr(library, 'export_source_paths', self.sources)
        setattr(library, 'info_overrides', info)
        setattr(library, 'media_facts', facts)
        return ExportInputs(library, playlists, standalone, extra, titles, artists, display_rows)


def _identity_headers(library: Library, required: set[str], retained: set[str], *, complete: bool) -> dict:
    """Retain tiny native headers; optional unrelated identities fail closed."""
    nodes = []
    optional: dict[str, list[dict]] = {}
    invalid: set[str] = set()
    for node in library.source_playlist_catalog.get('nodes', ()):
        if not isinstance(node, Mapping):
            if complete:
                continue
            raise ValueError('Export playlist identity has an invalid native node')
        identity = str(node.get('ID', ''))
        if identity not in required and identity not in retained:
            continue
        selected = {}
        for name in ('ID', 'UUID', 'rb_local_deleted'):
            value = node.get(name, 0 if name == 'rb_local_deleted' else None)
            if (value is not None and type(value) not in (str, int, float, bool)
                    or type(value) is float and not math.isfinite(value)):
                if identity in required:
                    raise ValueError('Export playlist identity has an invalid native scalar')
                invalid.add(identity)
                break
            selected[name] = value
        else:
            if identity in required:
                nodes.append(selected)
            else:
                optional.setdefault(identity, []).append(selected)
    for identity, rows in optional.items():
        active = [row for row in rows if row.get('rb_local_deleted', 0) == 0]
        if (identity not in invalid and len(active) == 1
                and isinstance(active[0]['UUID'], str) and active[0]['UUID']
                and '\0' not in active[0]['UUID'] and len(active[0]['UUID']) <= 4096):
            nodes.extend(rows)
    return {'nodes': nodes}


def capture_export(library: Library, playlists=(), *, standalone=(), extra=(),
                   titles=None, artists=None, complete=False,
                   playlist_identities=False, display_rows=None) -> ExportSnapshot:
    """Freeze selected data before dispatch; never copy unrelated cue graphs.

    ``complete`` is reserved for firmware planners that discover their retained
    managed selection in the device worker. Keep every track's display/identity
    in that case, including hidden tracks and relocated historical identities.
    """
    playlists, standalone, extra = tuple(playlists), tuple(standalone), tuple(extra)
    chosen = {key for playlist in (*playlists, *extra) for key in playlist.effective_keys}
    chosen.update(track.track_id for track in standalone)
    selected_pids = {playlist.pid for playlist in playlists}
    native = (library.playlists if complete else
              [playlist for playlist in library.playlists if playlist.pid and playlist.pid in selected_pids])
    # Native playlist proof needs raw membership identities, including hidden
    # members; presentation resolve() alone would omit those necessary records.
    chosen.update(key for playlist in native for key in playlist.effective_keys)
    captured = (library.tracks_by_id if complete else
                {key: library.tracks_by_id[key] for key in chosen if key in library.tracks_by_id})
    # These are display/edit caches, never CDJ authority. The native reader
    # supplies authoritative cues/tags, and firmware planners do not use them.
    omitted = {'source_cues', 'source_cue_refs', 'source_tag_ids', 'source_tag_times'}
    track_fields = tuple(field.name for field in fields(Track) if field.name not in omitted)
    playlist_fields = tuple(field.name for field in fields(Playlist))
    def track_row(track):
        return {name: getattr(track, name) for name in track_fields}
    def playlist_row(playlist):
        return {name: getattr(playlist, name) for name in playlist_fields}
    ids = set(captured) | {track.track_id for track in standalone}
    from .ipod_export_metadata import freeze_display_rows
    frozen_display = freeze_display_rows(library, display_rows, titles=titles, artists=artists)
    registry = library.artist_registry
    if registry is not None and not isinstance(registry, ArtistRegistry):
        raise TypeError('Export requires a supported captured artist registry')
    catalog = {}
    if playlist_identities and library.source_kind == 'rekordbox-db':
        # Complete iPod exports may preserve prior playlist associations too.
        # Keep only ID/UUID/deleted scalars, never source membership/parent graphs.
        native_ids = {str(playlist.pid) for playlist in (*playlists, *extra) if playlist.pid}
        retained_ids = {str(playlist.pid) for playlist in native if playlist.pid} - native_ids if complete else set()
        catalog = _identity_headers(library, native_ids, retained_ids, complete=complete)
    data = dict(
        tracks=[track_row(track) for track in captured.values()],
        selected=[playlist_row(playlist) for playlist in playlists],
        standalone=[track_row(track) for track in standalone],
        extra=[playlist_row(playlist) for playlist in extra],
        playlists=[playlist_row(playlist) for playlist in native],
        source=str(library.source), source_kind=library.source_kind,
        source_library_id=library.source_library_id,
        source_playlist_catalog=catalog,
        hide_untracked=library.hide_untracked, hide_nokey=library.hide_nokey,
        hide_short=library.hide_short, hide_short_secs=library.hide_short_secs,
        hidden_ids=list(library.hidden_ids & ids),
        removed_by_playlist=_removal_memberships(library.removed_by_playlist,
            None if complete else selected_pids),
        title_junk=library.title_junk,
        registry=(dict(canon=registry._canon, trusted=list(registry._trusted))
                  if isinstance(registry, ArtistRegistry) else None),
        titles={key: value for key, value in (titles or {}).items() if key in ids},
        artists={key: value for key, value in (artists or {}).items() if key in ids},
        display_rows=frozen_display,
        info_overrides={key: value for key, value in getattr(library, 'info_overrides', {}).items() if key in ids},
        media_facts={key: value for key, value in getattr(library, 'media_facts', {}).items() if key in ids},
    )
    return ExportSnapshot(json.dumps(data, separators=(',', ':'), allow_nan=False).encode(),
                          source_paths(library, standalone))
