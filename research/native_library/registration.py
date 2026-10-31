"""Bare registration proof for one observed untagged WAV shape; no live writer.

Pure row construction is separate from the marked-TEMP transaction adapter.
This does not prove general codecs, tagged metadata, analysis or native acceptance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re
import sqlite3
import struct
import tempfile

from soundbox.rekordbox_write import (
    ConflictError, WriteProofError, _cell, _digest, _directory, _quote, _regular, _rows, _state,
)
from soundbox.source_acquisition import SourceContract, acquire_source
from soundbox import native_registration_plan as native_rows

_DEFAULTS = native_rows._DEFAULTS
_FIELDS = native_rows._FIELDS
_native_id = native_rows._native_id
_uuid = native_rows._uuid
build_row = native_rows.build_row



@dataclass(frozen=True)
class WaveIdentity:
    path: str
    epoch: tuple[int, ...]
    sha256: str
    seconds: int

    @classmethod
    def from_dict(cls, value):
        if set(value) != {'path', 'epoch', 'sha256', 'seconds'}:
            raise WriteProofError('Unknown source-identity fields')
        result = cls(value['path'], tuple(value['epoch']), value['sha256'], value['seconds'])
        result.validate()
        return result

    def validate(self):
        path = Path(self.path)
        if (not path.is_absolute() or str(path) != self.path or '..' in path.parts
                or path.suffix.lower() != '.wav' or '\0' in self.path
                or len(self.epoch) != 5 or any(type(n) is not int or n < 0 for n in self.epoch)
                or self.epoch[1] == 0 or type(self.seconds) is not int or self.seconds <= 0
                or self.epoch[2] != 44 + self.seconds * 176400
                or not isinstance(self.sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.sha256)):
            raise WriteProofError('Unverified reference WAV identity')

    def revalidate(self):
        self.validate()
        path = Path(self.path)
        if path != path.resolve(strict=True) or path.is_symlink() or epoch(path.stat()) != self.epoch:
            raise ConflictError('Registration source identity changed')


def epoch(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inspect_wave(path, scratch):
    """Hash/capture once; parse the disposable copy, preserving the ORIGINAL inode."""
    path = Path(path).absolute()
    if path != path.resolve(strict=True) or path.is_symlink() or not path.is_file():
        raise WriteProofError('A canonical regular WAV source is required')
    if path.suffix.lower() != '.wav':
        raise WriteProofError('Only the observed WAV shape is supported')
    with acquire_source(SourceContract(path), Path(scratch)) as captured:
        with captured.path.open('rb') as stream:
            header = stream.read(44)
        if len(header) != 44 or header[:4] != b'RIFF' or header[8:16] != b'WAVEfmt ':
            raise WriteProofError('Only untagged reference WAV framing is proven')
        size, fmt_size, fmt, channels, rate, byte_rate, align, bits = struct.unpack_from('<I8xIHHIIHH', header, 4)
        data_size = struct.unpack_from('<I', header, 40)[0]
        if ((fmt_size, fmt, channels, rate, byte_rate, align, bits) != (16, 1, 2, 44100, 176400, 4, 16)
                or header[36:40] != b'data' or size + 8 != captured.size
                or data_size + 44 != captured.size or data_size % 176400):
            raise WriteProofError('Only integral untagged PCM16 stereo44.1kHz WAV is proven')
        result = WaveIdentity(str(path), epoch(captured.observation), captured.sha256, data_size // 176400)
    result.revalidate()
    return result








def plan(conn, audio, *, identity, uuid, timestamp, day):
    properties = _rows(conn, 'djmdProperty')
    if len(properties) != 1 or properties[0].get('DBVersion') != '6000':
        raise WriteProofError('Unverified library/version')
    properties = properties[0]
    columns = {r[1] for r in conn.execute('PRAGMA table_info(djmdContent)')}
    if columns != _FIELDS:
        raise WriteProofError('Unverified content columns')
    registry = _rows(conn, 'agentRegistry')
    if len({r['registry_id'] for r in registry}) != len(registry):
        raise WriteProofError('Ambiguous registry identity')
    counters = [r for r in registry if r['registry_id'] == 'localUpdateCount']
    if len(counters) != 1:
        raise WriteProofError('Missing or ambiguous native counter')
    counter = {key: _cell(value) for key, value in counters[0].items()}
    row = build_row(audio, identity=identity, uuid=uuid, library=properties['DBID'],
                    device=properties['DeviceID'], timestamp=timestamp, day=day, counter=counter['int_1'])
    existing = _rows(conn, 'djmdContent')
    if any(r['ID'] == identity or r['UUID'] == uuid or r['FolderPath'] == audio.path for r in existing):
        raise ConflictError('Existing content ID, UUID or path requires identity reconciliation')
    return dict(audio=asdict(audio), row=row, counter_before=counter,
                counter_after={**counter, 'int_1': counter['int_1'] + 1}, baseline=_state(conn))


def validate_plan(conn, candidate):
    try:
        audio = WaveIdentity.from_dict(candidate['audio'])
        row = candidate['row']
        expected = plan(conn, audio, identity=row['ID'], uuid=row['UUID'],
                        timestamp=row['created_at'], day=row['StockDate'])
        # JSON serialization normalizes tuple/list epochs; both representations
        # must bind the same immutable fact set and exact row values.
        if _digest(expected) != _digest(candidate):
            raise ConflictError('Registration plan does not match its complete baseline')
        return audio
    except (KeyError, TypeError, ValueError) as exc:
        raise WriteProofError('Invalid registration plan') from exc


def _authorizer(action, table, column, database, trigger):
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
        if database != 'main' or trigger:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_INSERT and table == 'djmdContent':
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_UPDATE and table == 'agentRegistry' and column == 'int_1':
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION,
                  sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _apply(conn, candidate):
    """Internal TEMP SQL only; the workspace owns transaction/recovery boundaries."""
    databases = conn.execute('PRAGMA database_list').fetchall()
    if len(databases) != 1 or databases[0][1] != 'main':
        raise WriteProofError('Registration cannot use attached databases')
    path = Path(databases[0][2])
    root = path.parent.parent if path.parent.name == 'transaction' else path.parent
    if (not path.is_absolute() or path != path.resolve() or path.name not in ('working.db', 'after.db')
            or root != root.resolve() or not root.name.startswith('soundbox-registration-')
            or not root.is_relative_to(Path(tempfile.gettempdir()).resolve())):
        raise WriteProofError('Registration SQL requires an original marked TEMP workspace')
    _directory(root); _regular(path); _regular(root/'workspace.json')
    marker = json.loads((root/'workspace.json').read_bytes())
    if marker.get('format') != 'soundbox-offline-registration-v1' or marker.get('root') != str(root):
        raise WriteProofError('Registration SQL workspace marker changed')
    _regular(root/'planned-intent.json')
    admitted = json.loads((root/'planned-intent.json').read_bytes())
    if _digest(admitted.get('candidate')) != _digest(candidate):
        raise ConflictError('Registration SQL differs from its durable admitted intention')
    validate_plan(conn, candidate)
    row = candidate['row']
    columns = list(row)
    conn.set_authorizer(_authorizer)
    try:
        conn.execute('INSERT INTO djmdContent (' + ','.join(_quote(c) for c in columns)
                     + ') VALUES (' + ','.join('?' for _ in columns) + ')', list(row.values()))
        cursor = conn.execute("UPDATE agentRegistry SET int_1=? WHERE registry_id='localUpdateCount' AND int_1=?",
                              (candidate['counter_after']['int_1'], candidate['counter_before']['int_1']))
        if cursor.rowcount != 1:
            raise ConflictError('Counter changed during registration')
    finally:
        conn.set_authorizer(None)


def verify(before_conn, after_conn, candidate):
    validate_plan(before_conn, candidate)
    before, after = candidate['baseline'], _state(after_conn)
    if before['schema'] != after['schema'] or before['metadata'] != after['metadata']:
        raise WriteProofError('Registration changed schema or database metadata')
    for table in before['tables'].keys() | after['tables'].keys():
        if table not in ('djmdContent', 'agentRegistry') and before['tables'].get(table) != after['tables'].get(table):
            raise WriteProofError('Registration changed an unrelated table')
    for table, key, addition in (('djmdContent', 'ID', candidate['row']),
                                 ('agentRegistry', 'registry_id', candidate['counter_after'])):
        left, right = ([{k: _cell(v) for k, v in r.items()} for r in _rows(conn, table)]
                       for conn in (before_conn, after_conn))
        expected = {r[key]: r for r in left}
        if len(expected) != len(left):
            raise WriteProofError('Ambiguous source row identity')
        expected[addition[key]] = addition
        if len(right) != len(expected) or {r[key]: r for r in right} != expected:
            raise WriteProofError('Registration changed an unplanned row or field')
    return after
