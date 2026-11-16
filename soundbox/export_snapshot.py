"""Immutable GUI-to-export capture, separate from native/device authority.

Only selected musical records enter explicit USB exports. Firmware reconciliation
may need the complete lightweight identity inventory to resolve its managed set
after reading the device. Neither path copies native cue/catalog graphs: native
CDJ metadata is independently reacquired by its existing source reader.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path

from .library import Library, Playlist, Track
from .metadata import ArtistRegistry


def source_paths(library: Library, standalone=()) -> tuple[Path, ...]:
    """Complete overlap boundary, including unselected and hidden sources.

    This captures path values only; it performs no stat, resolve or audio read.
    A materialized selection retains the original full boundary independently
    of its smaller tracks dictionary.
    """
    paths = [*getattr(library, 'export_source_paths', ()), Path(library.source)]
    for track in (*library.tracks_by_id.values(), *standalone):
        paths.append(track.path)
        if track.source_analysis_path:
            paths.append(Path(track.source_analysis_path))
        native_path = getattr(track, 'source_native_path', '')
        if native_path:
            paths.append(Path(native_path))
    return tuple(dict.fromkeys(paths))


@dataclass(frozen=True)
class ExportInputs:
    """Worker-owned materialization; no objects refer to the live GUI model."""
    library: Library
    playlists: tuple[Playlist, ...]
    standalone: tuple[Track, ...]
    extra: tuple[Playlist, ...]
    titles: dict
    artists: dict


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
        registry = data.pop('registry')
        info, facts = data.pop('info_overrides'), data.pop('media_facts')
        data['playlists'] = [Playlist(**row) for row in data['playlists']]
        data['hidden_ids'] = set(data['hidden_ids'])
        library = Library(tracks, **data)
        if registry is not None:
            library.artist_registry = ArtistRegistry(registry['canon'], registry['trusted'])
        setattr(library, 'export_source_paths', self.sources)
        setattr(library, 'info_overrides', info)
        setattr(library, 'media_facts', facts)
        return ExportInputs(library, playlists, standalone, extra, titles, artists)


def capture_export(library: Library, playlists=(), *, standalone=(), extra=(),
                   titles=None, artists=None, complete=False) -> ExportSnapshot:
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
    registry = library.artist_registry
    if registry is not None and not isinstance(registry, ArtistRegistry):
        raise TypeError('Export requires a supported captured artist registry')
    data = dict(
        tracks=[track_row(track) for track in captured.values()],
        selected=[playlist_row(playlist) for playlist in playlists],
        standalone=[track_row(track) for track in standalone],
        extra=[playlist_row(playlist) for playlist in extra],
        playlists=[playlist_row(playlist) for playlist in native],
        source=str(library.source), source_kind=library.source_kind,
        source_library_id=library.source_library_id,
        hide_untracked=library.hide_untracked, hide_nokey=library.hide_nokey,
        hide_short=library.hide_short, hide_short_secs=library.hide_short_secs,
        hidden_ids=list(library.hidden_ids & ids),
        removed_by_playlist={key: value for key, value in library.removed_by_playlist.items()
                             if complete or key in selected_pids},
        title_junk=library.title_junk,
        registry=(dict(canon=registry._canon, trusted=list(registry._trusted))
                  if isinstance(registry, ArtistRegistry) else None),
        titles={key: value for key, value in (titles or {}).items() if key in ids},
        artists={key: value for key, value in (artists or {}).items() if key in ids},
        info_overrides={key: value for key, value in getattr(library, 'info_overrides', {}).items() if key in ids},
        media_facts={key: value for key, value in getattr(library, 'media_facts', {}).items() if key in ids},
    )
    return ExportSnapshot(json.dumps(data, separators=(',', ':'), allow_nan=False).encode(),
                          source_paths(library, standalone))
