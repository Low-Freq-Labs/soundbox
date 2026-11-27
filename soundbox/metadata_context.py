"""Prepared exact-source manual display inputs; never native edit authority.

The ordinary registry inventory prepares these sparse immutable rows once.
Consumers perform one source/track lookup and never scan persisted origins.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .library import Library, Track
from . import metadata_provenance as provenance

Scope = tuple[str, str, str]
Pin = tuple[str, str, str, str]


def scope(library: Library) -> Scope:
    return str(library.source), library.source_kind, library.source_library_id


def pin(track: Track) -> Pin:
    return track.track_id, track.native_id, track.source_uuid, track.source_native_path or str(track.path)


@dataclass(frozen=True, slots=True)
class ManualRow:
    identity: Pin
    native_title: str
    native_artist: str
    title: str | None
    artist: str | None


def prepare_row(track: Track, source: Scope, origin: Any) -> ManualRow | None:
    """Called only for changed detached registry rows; malformed owners refuse."""
    if not isinstance(origin, dict) or source[1] != 'rekordbox-db' or not source[2] or not track.source_uuid:
        return None
    expected = dict(source=source[0], kind=source[1], library_id=source[2])
    if (origin.get('scope') != expected
            or tuple(origin.get(field) for field in ('local_id', 'native_id', 'uuid', 'path')) != pin(track)):
        return None
    recipe = origin.get('provenance')
    if recipe is None:
        return None
    try:
        provenance.validate(recipe)
    except (ValueError, TypeError, KeyError):
        return None
    values: list[str | None] = []
    for field, native in (('title', track.name), ('artist', track.artist)):
        ack = recipe['acks'].get(field)
        manual = provenance.manual(origin, field)
        values.append(manual if manual is not None and ack is not None
                      and native == origin.get('native_' + field) == ack['value'] else None)
    if values == [None, None]:
        return None
    return ManualRow(pin(track), track.name, track.artist, *values)


@dataclass(frozen=True, slots=True)
class ManualContext:
    """Immutable presentation snapshot; safe to share across detached copies."""
    source: Scope
    _rows: Mapping[str, ManualRow]
    signature: tuple

    def __init__(self, source: Scope, rows: dict[str, ManualRow]):
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, '_rows', MappingProxyType(dict(rows)))
        object.__setattr__(self, 'signature', (source, tuple((key, row.identity, row.native_title, row.native_artist,
                                        row.title, row.artist) for key, row in sorted(rows.items()))))

    def __deepcopy__(self, _memo):
        return self

    def values(self, library: Library, track: Track, native_pair: tuple[str, str] | None = None) -> tuple[str | None, str | None]:
        if (scope(library) != self.source or library.tracks_by_id.get(track.track_id) is not track
                or track.track_id in library.source_binding_issues):
            return None, None
        row = self._rows.get(track.track_id)
        if row is None or row.identity != pin(track):
            return None, None
        title, artist = native_pair if native_pair is not None else (track.name, track.artist)
        return (row.title if title == row.native_title else None,
                row.artist if artist == row.native_artist else None)


def values(library: Library | None, track: Track, native_pair: tuple[str, str] | None = None) -> tuple[str | None, str | None]:
    context = library.metadata_context if library is not None else None
    return context.values(library, track, native_pair) if isinstance(context, ManualContext) and library is not None else (None, None)


def signature(library: Library) -> tuple:
    context = library.metadata_context
    return context.signature if isinstance(context, ManualContext) and context.source == scope(library) else (scope(library), ())
