"""Disposable prepared display rows, never musical data or source authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

SCHEMA = 2
_FIELDS = {'id', 'title', 'artist', 'bpm', 'key', 'time', 'dateAdded', 'genre', 'format', 'bitrate'}
from .track_info import FIELDS as INFO_FIELDS
_FIELDS.update(key for key,_label in INFO_FIELDS)


def fingerprint(inputs: tuple) -> str:
    # Algorithm version is explicit: metadata-policy changes must bump SCHEMA.
    raw = json.dumps([SCHEMA, sorted(inputs[0]), *inputs[1:]],
                     ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _digest(rows: dict) -> str:
    digest = hashlib.sha256()
    for tid in sorted(rows):
        digest.update(json.dumps([tid, rows[tid]], sort_keys=True, ensure_ascii=True,
                                 allow_nan=False, separators=(',', ':')).encode() + b'\n')
    return digest.hexdigest()


def load(path: Path, key: str) -> dict[str, dict] | None:
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024**2:
            return None
        data = json.loads(path.read_bytes())
        if data['schema'] != SCHEMA or data['inputs'] != key:
            return None
        rows = data['rows']
        if not isinstance(rows, dict) or any(
                not isinstance(row, dict) or set(row) != _FIELDS
                or row['id'] != tid or any(not isinstance(v, str) for v in row.values())
                for tid, row in rows.items()):
            return None
        if _digest(rows) != data['sha256']:
            return None
        return rows
    except (OSError, ValueError, KeyError, TypeError):
        return None


def dump(path: Path, key: str, rows: dict) -> None:
    if path.is_symlink():
        return
    data = {'schema': SCHEMA, 'inputs': key, 'rows': rows,
            'sha256': _digest(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.browser-cache-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            # One row per JSON encoder call avoids a full-library GIL hold.
            handle.write('{"schema":' + str(SCHEMA) + ',"inputs":' + json.dumps(key)
                         + ',"sha256":' + json.dumps(data['sha256']) + ',"rows":{')
            for index, (tid, row) in enumerate(rows.items()):
                handle.write((',' if index else '') + json.dumps(tid) + ':' + json.dumps(row))
            handle.write('}}')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
