"""Pure grid-byte planning from native references; no file or database access.

This module grants no source-write authority. Transaction/recovery and native
acceptance must precede desktop integration. Opaque analysis sections are copied
verbatim rather than round-tripped through a general ANLZ serializer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
import struct
from typing import Any
from urllib.parse import quote

from .rekordbox_write import ConflictError, WriteProofError, _cell, _digest


@dataclass(frozen=True)
class Beat:
    number: int
    tempo: int  # native hundredths of BPM
    time_ms: int


@dataclass(frozen=True)
class GridSnapshot:
    track: dict[str, Any]
    dat: bytes
    ext: bytes
    content_file: dict[str, Any] | None = None

    @property
    def revision(self) -> str:
        data: dict[str, Any] = {'track': {k: _cell(v) for k, v in self.track.items()},
                        'dat': hashlib.sha256(self.dat).hexdigest(),
                        'ext': hashlib.sha256(self.ext).hexdigest()}
        if self.content_file is not None:
            data['content_file'] = {k: _cell(v) for k, v in self.content_file.items()}
        return _digest(data)


@dataclass(frozen=True)
class GridEdit:
    track_id: str
    track_uuid: str
    revision: str
    operation: str
    value: int = 0
    nudges: tuple[int, ...] = ()


@dataclass(frozen=True)
class GridDelta:
    table: str
    key: str
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass(frozen=True)
class GridCandidate:
    dat: bytes
    ext: bytes
    changes: tuple[GridDelta, ...]


@dataclass(frozen=True)
class AnalysisSection:
    kind: bytes
    header_size: int
    raw: bytes


@dataclass(frozen=True)
class AnalysisImage:
    header: bytes
    sections: tuple[AnalysisSection, ...]

    @classmethod
    def parse(cls, raw: bytes) -> AnalysisImage:
        if type(raw) is not bytes or len(raw) < 12 or raw[:4] != b'PMAI':
            raise WriteProofError('Invalid analysis container')
        header_size, total = struct.unpack_from('>II', raw, 4)
        if not 12 <= header_size <= total == len(raw):
            raise WriteProofError('Invalid analysis container length')
        sections = []
        offset = header_size
        while offset < total:
            if total - offset < 12:
                raise WriteProofError('Truncated analysis section')
            kind = raw[offset:offset + 4]
            header, size = struct.unpack_from('>II', raw, offset + 4)
            if (any(c < 32 or c > 126 for c in kind)
                    or not 12 <= header <= size <= total - offset):
                raise WriteProofError('Invalid analysis section length/type')
            sections.append(AnalysisSection(kind, header, raw[offset:offset + size]))
            offset += size
        return cls(raw[:header_size], tuple(sections))

    def section(self, kind: bytes) -> AnalysisSection:
        matches = [s for s in self.sections if s.kind == kind]
        if len(matches) != 1:
            raise WriteProofError('Missing or ambiguous grid section')
        return matches[0]

    def replacing(self, kind: bytes, replacement: bytes) -> bytes:
        self.section(kind)
        if len(replacement) < 12 or replacement[:4] != kind:
            raise WriteProofError('Invalid replacement section')
        header, size = struct.unpack_from('>II', replacement, 4)
        if not 12 <= header <= size == len(replacement):
            raise WriteProofError('Invalid replacement section length')
        body = b''.join(replacement if s.kind == kind else s.raw for s in self.sections)
        header_bytes = bytearray(self.header)
        struct.pack_into('>I', header_bytes, 8, len(header_bytes) + len(body))
        return bytes(header_bytes) + body


def _validated(beats: tuple[Beat, ...]) -> tuple[Beat, ...]:
    if not beats:
        raise WriteProofError('A grid needs at least one beat')
    previous = -1
    for i, beat in enumerate(beats):
        if (not isinstance(beat, Beat)
                or any(type(v) is not int for v in
                       (beat.number, beat.tempo, beat.time_ms))
                or not 1 <= beat.number <= 4 or not 1 <= beat.tempo <= 65535
                or not previous < beat.time_ms <= 0xffffffff
                or (i and beat.number != beats[i - 1].number % 4 + 1)):
            raise WriteProofError('Invalid or discontinuous primary grid')
        previous = beat.time_ms
    return beats


def read_grid(raw: bytes) -> tuple[Beat, ...]:
    section = AnalysisImage.parse(raw).section(b'PQTZ')
    if section.header_size != 24:
        raise WriteProofError('Unverified primary grid header')
    count = struct.unpack_from('>I', section.raw, 20)[0]
    if len(section.raw) != 24 + count * 8:
        raise WriteProofError('Primary grid count/length mismatch')
    return _validated(tuple(Beat(*row) for row in
                            struct.iter_unpack('>HHI', section.raw[24:])))


def replace_grid(raw: bytes, beats: tuple[Beat, ...]) -> bytes:
    read_grid(raw)
    _validated(beats)
    image = AnalysisImage.parse(raw)
    header = bytearray(image.section(b'PQTZ').raw[:24])
    struct.pack_into('>I', header, 8, 24 + 8 * len(beats))
    struct.pack_into('>I', header, 20, len(beats))
    replacement = bytes(header) + b''.join(
        struct.pack('>HHI', b.number, b.tempo, b.time_ms) for b in beats)
    return image.replacing(b'PQTZ', replacement)


def clear_secondary_grid(raw: bytes) -> bytes:
    """Native 6.8.7 invalidation, preserving every unrelated EXT byte."""
    image = AnalysisImage.parse(raw)
    section = image.section(b'PQT2')
    if (section.header_size != 56 or len(section.raw) < 56
            or section.raw[12:24] != bytes.fromhex('000000000100000200000000')
            or len(section.raw) != 56 + 2 * struct.unpack_from('>I', section.raw, 40)[0]):
        raise WriteProofError('Unverified secondary grid header/count')
    header = bytearray(section.raw[:56])
    struct.pack_into('>I', header, 8, 56)
    header[24:] = bytes(32)
    return image.replacing(b'PQT2', bytes(header))


def shift_grid(beats: tuple[Beat, ...], delta_ms: int) -> tuple[Beat, ...]:
    """Native nudges omit beats shifted before the audio origin."""
    _validated(beats)
    if type(delta_ms) is not int:
        raise WriteProofError('Grid offset must be integer milliseconds')
    return _validated(tuple(Beat(b.number, b.tempo, b.time_ms + delta_ms)
                            for b in beats if b.time_ms + delta_ms >= 0))


def set_bar_start(beats: tuple[Beat, ...], position_ms: int) -> tuple[Beat, ...]:
    """Translate the nearest beat and relabel phase, retaining earlier bars."""
    _validated(beats)
    if type(position_ms) is not int or not 0 <= position_ms <= 0xffffffff:
        raise WriteProofError('Invalid downbeat position')
    # Exact ties require a native reference before accepting either direction.
    distances = [abs(b.time_ms - position_ms) for b in beats]
    nearest = min(distances)
    if distances.count(nearest) != 1:
        raise WriteProofError('Equidistant downbeat anchor has no native reference')
    anchor = beats[distances.index(nearest)]
    shifted = shift_grid(beats, position_ms - anchor.time_ms)
    return _validated(tuple(Beat((b.number - anchor.number) % 4 + 1,
                                  b.tempo, b.time_ms) for b in shifted))


def duration_bounds(raw: bytes) -> tuple[int, int]:
    """Conservative audio-end bounds from a complete native 150-Hz stream.

    Grid edits need no source-audio decode. A candidate in the final waveform
    sampling interval remains ambiguous and is rejected rather than guessed.
    """
    section = AnalysisImage.parse(raw).section(b'PWV3')
    if section.header_size != 24:
        raise WriteProofError('Unverified detail duration header')
    width, count, rate = struct.unpack_from('>III', section.raw, 12)
    if width != 1 or rate != 0x960000 or not count or len(section.raw) != 24 + count:
        raise WriteProofError('Unverified detail duration stream')
    return (count - 1) * 1000 // 150, (count * 1000 + 149) // 150


def double_grid(beats: tuple[Beat, ...], *,
                end_bounds_ms: tuple[int, int] | None = None) -> tuple[Beat, ...]:
    _validated(beats)
    if len(beats) < 2:
        raise WriteProofError('Tempo doubling needs two verified grid rows')
    if len({b.tempo for b in beats}) != 1 or beats[0].tempo > 32767:
        raise WriteProofError('Unverified variable/out-of-range tempo doubling')
    if beats[0].time_ms * beats[0].tempo >= 6000000:
        raise WriteProofError('Unverified grid lead-in longer than one beat')
    tempo = beats[0].tempo * 2
    result = []
    for i, beat in enumerate(beats):
        number = (beat.number - 1) * 2 % 4 + 1
        result.append(Beat(number, tempo, beat.time_ms))
        if i + 1 < len(beats):
            result.append(Beat(number % 4 + 1, tempo,
                               (beat.time_ms + beats[i + 1].time_ms) // 2))
    # Native doubling also fills the available half-beat before the first row.
    # Keep integer floor arithmetic, including the 45 -> 90 BPM 705 -> 38 ms case.
    preceding = 3 * beats[0].time_ms - beats[1].time_ms
    if preceding >= 0:
        result.insert(0, Beat((result[0].number - 2) % 4 + 1, tempo,
                              preceding // 2))
    if end_bounds_ms is not None:
        lower, upper = end_bounds_ms
        if (type(lower) is not int or type(upper) is not int
                or not 0 <= lower <= upper <= 0xffffffff
                or beats[-1].time_ms > lower):
            raise WriteProofError('Unverified grid/audio end bounds')
        following = (3 * beats[-1].time_ms - beats[-2].time_ms) // 2
        if following < lower:
            result.append(Beat(result[-1].number % 4 + 1, tempo, following))
        elif following <= upper:
            raise WriteProofError('Final beat falls in an unverified audio-end interval')
    return _validated(tuple(result))


def halve_grid(beats: tuple[Beat, ...]) -> tuple[Beat, ...]:
    """Retain odd-numbered beats; anchor the first full bar at beat 1."""
    _validated(beats)
    if len({b.tempo for b in beats}) != 1 or beats[0].tempo < 2:
        raise WriteProofError('Unverified variable or unrepresentable tempo halving')
    retained = [b for b in beats if b.number in (1, 3)]
    if not retained:
        raise WriteProofError('Tempo halving would leave no verified grid')
    first = 0 if retained[0].number == 1 else 3
    return _validated(tuple(Beat((first + i) % 4 + 1, b.tempo // 2, b.time_ms)
                            for i, b in enumerate(retained)))


def set_grid_bpm(beats: tuple[Beat, ...], tempo: int, *,
                 end_bounds_ms: tuple[int, int] | None = None) -> tuple[Beat, ...]:
    """Native constant-BPM entry: rebuild rows, then extend from rounded tail."""
    _validated(beats)
    if type(tempo) is not int or not 1 <= tempo <= 65535 or len({b.tempo for b in beats}) != 1:
        raise WriteProofError('Manual BPM requires a constant grid and 0.01–655.35 BPM')
    if tempo == beats[0].tempo:
        return beats
    lower, upper = end_bounds_ms if end_bounds_ms is not None else (beats[-1].time_ms,) * 2
    if (type(lower) is not int or type(upper) is not int
            or not 0 <= lower <= upper <= 0xffffffff or beats[-1].time_ms > lower):
        raise WriteProofError('Unverified grid/audio end bounds')
    result: list[Beat] = []
    anchor, base_index = beats[0].time_ms, 0
    # The extension starts from the rounded rebuilt tail, rather than carrying
    # submillisecond phase beyond the original row count (native 45.01→90/90→91).
    for index in range(1 + (upper - anchor) * tempo // 6000000 + len(beats)):
        if index == len(beats):
            anchor, base_index = result[-1].time_ms, index - 1
        time_ms = anchor + ((index - base_index) * 12000000 + tempo) // (2 * tempo)
        if time_ms > lower:
            if time_ms <= upper:
                raise WriteProofError('Final beat falls in an unverified audio-end interval')
            break
        result.append(Beat((beats[0].number - 1 + index) % 4 + 1, tempo, time_ms))
    return _validated(tuple(result))


def transform_beats(beats: tuple[Beat, ...], operation: str, value: int = 0,
                    nudges: tuple[int, ...] = (), *,
                    end_bounds_ms: tuple[int, int] | None = None) -> tuple[Beat, ...]:
    """Shared row arithmetic; RAM previews alone may lack native end metadata."""
    if type(value) is not int:
        raise WriteProofError('Grid edit value must be an integer')
    if nudges and (operation != 'shift' or value != 0 or len(nudges) > 1024):
        raise WriteProofError('Invalid grouped grid nudges')
    if operation == 'shift':
        edited = beats
        for delta in nudges or (value,):
            edited = shift_grid(edited, delta)
    elif operation == 'downbeat':
        edited = set_bar_start(beats, value)
    elif operation == 'double' and value == 0:
        edited = double_grid(beats, end_bounds_ms=end_bounds_ms)
    elif operation == 'halve' and value == 0:
        edited = halve_grid(beats)
    elif operation == 'bpm':
        edited = set_grid_bpm(beats, value, end_bounds_ms=end_bounds_ms)
    else:
        raise WriteProofError('Invalid grid operation')
    return edited


def transform_grid(dat: bytes, ext: bytes, operation: str, value: int = 0,
                   nudges: tuple[int, ...] = ()) -> tuple[bytes, bytes]:
    """Source planning always supplies the verified native duration bounds."""
    beats = read_grid(dat)
    bounds = duration_bounds(ext) if operation in ('double', 'bpm') else None
    edited = transform_beats(beats, operation, value, nudges, end_bounds_ms=bounds)
    if edited == beats:
        return dat, ext
    return replace_grid(dat, edited), clear_secondary_grid(ext)


def plan_grid_edit(snapshot: GridSnapshot, counter: dict[str, Any], edit: GridEdit,
                   *, timestamp: str) -> GridCandidate:
    """Plan exact target-only bytes/rows; callers own authority and durability."""
    track = snapshot.track
    if (not edit.track_id or not edit.track_uuid or track.get('ID') != edit.track_id
            or track.get('UUID') != edit.track_uuid or track.get('rb_local_deleted')
            or snapshot.revision != edit.revision):
        raise ConflictError('Loaded track or grid changed; refresh before editing')
    if type(track.get('Analysed')) is not int or track['Analysed'] < 0:
        raise WriteProofError('Unverified analysis flags')
    if track['Analysed'] & 0x80:
        raise WriteProofError('Analysis Lock is on')
    if type(track.get('FileType')) is not int or track['FileType'] not in (1, 11, 12):
        raise WriteProofError('Unverified grid format')
    dat, ext = transform_grid(snapshot.dat, snapshot.ext, edit.operation, edit.value, edit.nudges)
    if (dat, ext) == (snapshot.dat, snapshot.ext):
        return GridCandidate(dat, ext, ())
    beats, edited = read_grid(snapshot.dat), read_grid(dat)
    changes: tuple[GridDelta, ...] = ()
    if edit.operation in ('double', 'halve', 'bpm'):
        # Grid translation/phase edits never change the DB BPM, even when a
        # variable-tempo grid loses its first row at the negative boundary.
        try:
            if (type(timestamp) is not str or not re.fullmatch(
                    r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \+00:00', timestamp)):
                raise ValueError('Invalid timestamp shape')
            datetime.fromisoformat(timestamp.replace(' +', '+'))
        except ValueError as exc:
            raise WriteProofError('Unverified grid metadata timestamp') from exc
        count = track.get('TrackInfoUpdated')
        if (type(track.get('BPM')) is not int or track['BPM'] != beats[0].tempo
                or not isinstance(count, str) or len(count) > 18
                or not count.isascii() or not count.isdigit()):
            raise WriteProofError('Unverified or stale BPM metadata')
        increment = 4 if edit.operation == 'bpm' else 2
        if (counter.get('registry_id') != 'localUpdateCount'
                or type(counter.get('int_1')) is not int
                or not 0 <= counter['int_1'] < 2**63 - increment):
            raise WriteProofError('Invalid grid update counter')
        after = {**track, 'BPM': edited[0].tempo, 'TrackInfoUpdated': str(int(count) + 1),
                 'rb_local_usn': counter['int_1'] + increment, 'updated_at': timestamp}
        if edit.operation == 'bpm':
            analysis = track.get('AnalysisUpdated')
            if (not isinstance(analysis, str) or not analysis.isascii() or not analysis.isdigit()
                    or len(analysis) > 18):
                raise WriteProofError('Unverified manual BPM analysis revision')
            after['AnalysisUpdated'] = str(int(analysis) + 1)
        changes = (GridDelta('djmdContent', edit.track_id, track, after),
                   GridDelta('agentRegistry', 'localUpdateCount', counter,
                             {**counter, 'int_1': counter['int_1'] + increment}))
        if edit.operation == 'bpm':
            file = snapshot.content_file
            path = track.get('AnalysisDataPath')
            if (not file or type(path) is not str or not path.startswith('/PIONEER/USBANLZ/')
                    or file.get('ID') != track['UUID'] + '_' + quote(path, safe='')
                    or file.get('ContentID') != track['ID'] or file.get('Path') != path
                    or not file.get('UUID') or not re.fullmatch('[0-9a-f]{32}', file.get('Hash') or '')
                    or type(file.get('Size')) is not int or file['Size'] <= 0
                    or any(file.get(k) != 0 for k in ('rb_data_status', 'rb_local_data_status',
                        'rb_local_deleted', 'rb_file_hash_dirty', 'rb_file_size_dirty',
                        'rb_local_file_status', 'rb_in_progress', 'rb_process_type'))
                    or file.get('rb_local_synced') not in (0, 1)
                    or any(file.get(k) is not None for k in
                           ('usn', 'rb_insync_hash', 'rb_insync_local_usn', 'rb_temp_path'))):
                raise WriteProofError('Unverified manual BPM analysis-file metadata')
            changed_file = {**file, 'Hash': hashlib.md5(dat).hexdigest(), 'Size': len(dat),
                            'rb_local_usn': counter['int_1'] + 2, 'updated_at': timestamp}
            changes += (GridDelta('contentFile', file['ID'], file, changed_file),)
    return GridCandidate(dat, ext, changes)
