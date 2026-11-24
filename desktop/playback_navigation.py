"""Immutable browser occurrences and source-bound playback intentions.

These are presentation cursors, never source membership or file-read authority.
Capturing them reads model scalars only; ordinary load validation stays separate.
"""
from dataclasses import dataclass, replace


def source_scope(library):
    return str(library.source), library.source_kind, library.source_library_id


def song_pin(track):
    # Canonical SoundBox import IDs survive accepted native registration. Its
    # acquired native ID/UUID are edit facts, not a replacement audible song.
    identity = track.track_id
    if (identity.startswith('sb-') and len(identity) == 15
            and all(value in '0123456789abcdef' for value in identity[3:])):
        return identity, '', '', str(track.path), str(track.path), ''
    return (track.track_id, track.native_id, track.source_uuid,
            str(track.path), str(track.native_path), track.source_native_path)


@dataclass(frozen=True)
class Entry:
    token: tuple
    song: tuple


@dataclass(frozen=True)
class Order:
    source: tuple
    view: tuple
    entries: tuple[Entry, ...]

    def valid(self, library, index):
        if library is None or source_scope(library) != self.source or not 0 <= index < len(self.entries):
            return False
        song = self.entries[index].song
        track = library.tracks_by_id.get(song[0])
        return track is not None and song_pin(track) == song

    def cursor(self, index):
        return Cursor(self, index)


@dataclass(frozen=True)
class Cursor:
    order: Order
    index: int

    @property
    def entry(self):
        return self.order.entries[self.index]

    def locate(self, order):
        if self.order.source != order.source:
            return None
        for index, entry in enumerate(order.entries):
            if entry.song == self.entry.song and (
                    self.order.view != order.view or entry.token == self.entry.token):
                return index
        return None


def occurrence_tokens(rows, identity):
    ordinals = {}
    for row in rows:
        token = tuple(identity(row))
        if token[0] == 'track':
            ordinal = ordinals.get(token, 0)
            ordinals[token] = ordinal + 1
            token = (*token, ordinal)
        yield token


def capture(library, view, rows, identity):
    entries = []
    for row, token in zip(rows, occurrence_tokens(rows, identity)):
        track = library.tracks_by_id.get(row.get('id'))
        if track is not None:
            entries.append(Entry(token, song_pin(track)))
    return Order(source_scope(library), view, tuple(entries))


def rebind_path(order, library, previous, old_path, new_path):
    """Retain occurrences through one durably accepted local path transition.

    The completion owner supplies the exact pre-publication source/song pin.
    This does not adopt a refresh, a different song or another source domain.
    """
    source, old = previous
    if order is None or order.source != source or source_scope(library) != source:
        return order
    track = library.tracks_by_id.get(old[0])
    if track is None:
        return order
    new = song_pin(track)
    if (old[:3] != new[:3] or old[3:5] != (old_path, old_path)
            or old[5] not in ('', old_path) or new[3:5] != (new_path, new_path)
            or new[5] not in ('', new_path)):
        return order
    if not any(entry.song == old for entry in order.entries):
        return order
    return replace(order, entries=tuple(
        replace(entry, song=new) if entry.song == old else entry for entry in order.entries))


class Navigation:
    def __init__(self):
        self.sequence = 0
        self.committed = None
        self.requested = None

    def stage(self, cursor):
        self.sequence += 1
        self.requested = cursor
        return self.sequence

    def start(self, sequence):
        if sequence != self.sequence or self.requested is None:
            return False
        self.committed, self.requested = self.requested, None
        return True

    def cancel(self, sequence):
        if sequence != self.sequence or self.requested is None:
            return False
        self.requested = None
        return True
