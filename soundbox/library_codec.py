"""Primitive source-library values for worker IPC, never source authority.

The artist registry is derived presentation and is rebuilt by its owner. All
Track/Playlist fields and the other Library facts survive this boundary.
"""
from __future__ import annotations

from dataclasses import fields
from operator import attrgetter
from pathlib import Path
from typing import Any, Iterator

from .library import Library, LibraryTag, Playlist, TagCategory, Track
from .worker_protocol import ProtocolError

BATCH_RECORDS = 128
TRACK_FIELDS = tuple(field.name for field in fields(Track))
PLAYLIST_FIELDS = tuple(field.name for field in fields(Playlist))
TAG_FIELDS = tuple(field.name for field in fields(LibraryTag))
CATEGORY_FIELDS = tuple(field.name for field in fields(TagCategory))
_track = attrgetter(*TRACK_FIELDS)
_playlist = attrgetter(*PLAYLIST_FIELDS)
_tag = attrgetter(*TAG_FIELDS)
_category = attrgetter(*CATEGORY_FIELDS)
_COLLECTIONS = frozenset(('tracks_by_id', 'playlists', 'source_tags', 'source_tag_categories',
    'source_playlist_catalog', 'source_binding_issues', 'hidden_ids', 'removed_by_playlist', 'title_junk'))
_SCALARS = tuple(field.name for field in fields(Library)
                 if field.name not in _COLLECTIONS | {'artist_registry', 'source'})
SCHEMA = ('library-v1-keyed', TRACK_FIELDS, PLAYLIST_FIELDS, TAG_FIELDS, CATEGORY_FIELDS, _SCALARS)


def _track_record(item):
    key, track = item
    return key, _track(track)


def sections(library: Library) -> Iterator[tuple[str, str, Any, Any]]:
    yield 'tracks', '', library.tracks_by_id.items(), _track_record
    yield 'playlists', '', library.playlists, _playlist
    yield 'tags', '', library.source_tags, _tag
    yield 'categories', '', library.source_tag_categories, _category
    yield 'hidden', '', library.hidden_ids, None
    yield 'removals', '', library.removed_by_playlist.items(), None
    yield 'bindings', '', library.source_binding_issues.items(), None
    yield 'junk', '', library.title_junk or (), None
    for name, value in library.source_playlist_catalog.items():
        if isinstance(value, list):
            yield 'catalog', name, value, None


def describe(library: Library) -> dict:
    return dict(schema=SCHEMA, source=(isinstance(library.source, Path), str(library.source)),
        scalars=tuple(getattr(library, name) for name in _SCALARS),
        junk_none=library.title_junk is None,
        catalog=tuple((name, isinstance(value, list), None if isinstance(value, list) else value)
                      for name, value in library.source_playlist_catalog.items()),
        counts=tuple((kind, field, len(values)) for kind, field, values, _getter in sections(library)))


def batches(library: Library) -> Iterator[tuple[str, str, list]]:
    """Bound record count; transport fragmentation preserves large exact values."""
    for kind, field, values, getter in sections(library):
        pending: list = []
        for value in values:
            pending.append(getter(value) if getter is not None else value)
            if len(pending) == BATCH_RECORDS:
                yield kind, field, pending
                pending = []
        if pending:
            yield kind, field, pending


class LibraryReceiver:
    def __init__(self, descriptor: dict, *, max_records: int | None):
        if (not isinstance(descriptor, dict) or set(descriptor) !=
                {'schema', 'source', 'scalars', 'junk_none', 'catalog', 'counts'}
                or descriptor['schema'] != SCHEMA):
            raise ProtocolError('Library worker schema changed')
        source = descriptor['source']
        scalars = descriptor['scalars']
        if (not isinstance(source, tuple) or len(source) != 2 or type(source[0]) is not bool
                or not isinstance(source[1], str) or not isinstance(scalars, tuple)
                or len(scalars) != len(_SCALARS) or type(descriptor['junk_none']) is not bool):
            raise ProtocolError('Invalid library worker header')
        self.expected: dict[tuple[str, str], int] = {}
        for kind, field, count in descriptor['counts']:
            if (type(kind) is not str or type(field) is not str or type(count) is not int
                    or count < 0 or max_records is not None and count > max_records
                    or (kind, field) in self.expected):
                raise ProtocolError('Invalid library worker counts')
            self.expected[kind, field] = count
        required = {(kind, '') for kind in ('tracks', 'playlists', 'tags', 'categories',
                                           'hidden', 'removals', 'bindings', 'junk')}
        catalog = {}
        for name, is_rows, value in descriptor['catalog']:
            if type(name) is not str or type(is_rows) is not bool or name in catalog:
                raise ProtocolError('Invalid native catalog header')
            catalog[name] = [] if is_rows else value
            if is_rows:
                required.add(('catalog', name))
        if set(self.expected) != required:
            raise ProtocolError('Library worker sections changed')
        self.received = dict.fromkeys(self.expected, 0)
        self.library = Library(tracks_by_id={}, playlists=[],
            source=Path(source[1]) if source[0] else source[1], **dict(zip(_SCALARS, scalars)),
            source_playlist_catalog=catalog, title_junk=None if descriptor['junk_none'] else [])

    def add(self, kind: str, field: str, rows: list) -> None:
        key = (kind, field)
        if (key not in self.expected or not isinstance(rows, list)
                or not 0 < len(rows) <= BATCH_RECORDS
                or self.received[key] + len(rows) > self.expected[key]):
            raise ProtocolError('Library worker batch exceeds its captured counts')
        lib = self.library
        if kind in ('tracks', 'playlists', 'tags', 'categories'):
            names, constructor = {'tracks': (TRACK_FIELDS, Track), 'playlists': (PLAYLIST_FIELDS, Playlist),
                                  'tags': (TAG_FIELDS, LibraryTag), 'categories': (CATEGORY_FIELDS, TagCategory)}[kind]
            for row in rows:
                identity = None
                if kind == 'tracks':
                    if (not isinstance(row, tuple) or len(row) != 2
                            or type(row[0]) is not str):
                        raise ProtocolError('Invalid library worker mapping key')
                    identity, row = row
                if not isinstance(row, tuple) or len(row) != len(names):
                    raise ProtocolError('Library worker record fields changed')
                value: Any = constructor(**dict(zip(names, row)))
                if kind == 'tracks':
                    assert identity is not None
                    if identity in lib.tracks_by_id:
                        raise ProtocolError('Duplicate library worker track identity')
                    lib.tracks_by_id[identity] = value
                else:
                    getattr(lib, {'playlists': 'playlists', 'tags': 'source_tags',
                                  'categories': 'source_tag_categories'}[kind]).append(value)
        elif kind == 'catalog':
            lib.source_playlist_catalog[field].extend(rows)
        elif kind == 'hidden':
            lib.hidden_ids.update(rows)
        elif kind == 'junk':
            if lib.title_junk is None:
                raise ProtocolError('Unexpected title rules in library worker batch')
            lib.title_junk.extend(rows)
        else:
            target = lib.removed_by_playlist if kind == 'removals' else lib.source_binding_issues
            for name, value in rows:
                if name in target:
                    raise ProtocolError('Duplicate library worker mapping identity')
                target[name] = value
        self.received[key] += len(rows)

    def finish(self) -> Library:
        if self.received != self.expected:
            raise ProtocolError('Incomplete library worker snapshot')
        if len(self.library.hidden_ids) != self.expected['hidden', '']:
            raise ProtocolError('Duplicate hidden library identity')
        return self.library
