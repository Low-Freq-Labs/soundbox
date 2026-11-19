"""Versioned, bounded per-song payloads for the canonical state repository.

The small library manifest and its changed records publish through StateArea as
one transaction. Portable exports/history materialize ordinary standalone JSON.
This is canonical state, never a disposable cache or native source authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

DIRECTORY = '.state-records'
MARKER = '_soundbox_record_store'
MAX_BYTES = 64 * 1024**2
MAX_RECORDS = 100_000
NAMESPACES = {'analysis_results': 'map', 'pending_prep_edits': 'list',
              'native_analysis_jobs': 'list', 'source_truth_targets': 'map',
              'source_truth_alias_targets': 'map', 'source_truth_artist_origins': 'map'}
_DIGEST = re.compile('[0-9a-f]{64}')


def encode(value: Any) -> bytes:
    return json.dumps(value, indent=2, allow_nan=False).encode('utf-8')


def entry_cost(kind: str, key: str, embedded_bytes: int) -> int:
    """Exact pretty-JSON cost beyond an empty settings map/list, per entry.

    Nonempty containers additionally contribute four bytes once. embedded_bytes
    includes the record's six-space continuation indent, but no leading indent.
    """
    return embedded_bytes + (10 + len(encode(key)) if kind == 'map' else 8)


def identity(path: Path) -> tuple:
    mode = path.lstat()
    if not stat.S_ISREG(mode.st_mode):
        raise ValueError(f'Canonical state record must be a regular file: {path.name}')
    return (mode.st_dev, mode.st_ino, mode.st_size, mode.st_mtime_ns, mode.st_ctime_ns)


def record_path(path: Path, digest: str) -> Path:
    if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
        raise ValueError('Invalid canonical state record digest')
    folder = path.parent / DIRECTORY
    if folder.is_symlink():
        raise ValueError('Canonical state records cannot be a symlink')
    return folder / f'{digest}.json'


def capture_file(record: Path) -> tuple[bytes, tuple]:
    before = identity(record)
    if before[2] > MAX_BYTES:
        raise ValueError('Canonical state record exceeds the 64 MiB safety limit')
    with record.open('rb') as source:
        opened = os.fstat(source.fileno())
        opened_id = (opened.st_dev, opened.st_ino, opened.st_size,
                     opened.st_mtime_ns, opened.st_ctime_ns)
        raw = source.read(MAX_BYTES + 1)
        closed = os.fstat(source.fileno())
        closed_id = (closed.st_dev, closed.st_ino, closed.st_size,
                     closed.st_mtime_ns, closed.st_ctime_ns)
    if before != opened_id or before != closed_id or before != identity(record):
        raise ValueError('Canonical state record changed while reading')
    if len(raw) > MAX_BYTES:
        raise ValueError('Canonical state record exceeds the 64 MiB safety limit')
    return raw, before


def capture(path: Path, digest: str) -> tuple[bytes, tuple]:
    raw, before = capture_file(record_path(path, digest))
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Canonical state record content does not match its manifest')
    return raw, before


def expand(path: Path, physical: dict) -> tuple[dict, dict[str, tuple], dict]:
    """Read only explicitly referenced records; unknown directories are untouched."""
    if (set(physical) != {MARKER, 'settings', 'revision', 'document', 'records'}
            or type(physical[MARKER]) is not int or physical[MARKER] != 1
            or physical['settings'] != [] or not isinstance(physical['document'], dict)
            or not isinstance(physical['records'], dict)
            or not isinstance(physical['revision'], str) or len(physical['revision']) != 32
            or any(c not in '0123456789abcdef' for c in physical['revision'])):
        raise ValueError('Invalid canonical state record manifest')
    document = physical['document']
    settings = document.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('Canonical state settings must be an object')
    epochs, cache = {}, {}
    count = 0
    empty = dict(document)
    empty['settings'] = dict(settings)
    for namespace, specification in physical['records'].items():
        if namespace not in NAMESPACES or not isinstance(specification, dict):
            raise ValueError('Invalid canonical state namespace')
        empty['settings'][namespace] = {} if NAMESPACES[namespace] == 'map' else []
    total = len(encode(empty))
    for namespace, specification in physical['records'].items():
        if (namespace not in NAMESPACES or settings.get(namespace, False) is not None
                or not isinstance(specification, dict)
                or set(specification) != {'kind', 'entries'}
                or specification['kind'] != NAMESPACES[namespace]
                or not isinstance(specification['entries'], list)):
            raise ValueError('Invalid canonical state namespace')
        entries = specification['entries']
        count += len(entries)
        if count > MAX_RECORDS:
            raise ValueError('Canonical state contains too many records')
        values: Any = {} if specification['kind'] == 'map' else []
        if entries:
            total += 4
        for entry in entries:
            if (not isinstance(entry, list) or len(entry) != 2
                    or type(entry[0]) is not str):
                raise ValueError('Invalid canonical state record reference')
            key, digest = entry
            # Validate before dict membership: malformed JSON may contain an
            # unhashable list/object where a digest string belongs.
            record_path(path, digest)
            if specification['kind'] == 'map' and key in values:
                raise ValueError('Duplicate canonical state record key')
            if digest not in cache:
                raw, epoch = capture(path, digest)
                cache[digest] = json.loads(raw)
                if encode(cache[digest]) != raw:
                    raise ValueError('Canonical state record is not in its declared encoding')
                # Six spaces account for embedding this standalone record in
                # settings' map/list. Retain the cost so save-time size checks
                # never reserialize untouched beat arrays.
                epochs[digest] = (*epoch, len(raw) + 6 * raw.count(b'\n'))
            else:
                raw = encode(cache[digest])
            total += entry_cost(specification['kind'], key, epochs[digest][5])
            if total > MAX_BYTES:
                raise ValueError('Library state exceeds the 64 MiB safety limit')
            # Separate equal records retain independent mutable logical ownership.
            value = copy.deepcopy(cache[digest])
            if specification['kind'] == 'map':
                values[key] = value
            else:
                if key != str(len(values)):
                    raise ValueError('Canonical state queue order changed')
                values.append(value)
        settings[namespace] = values
    return document, epochs, physical['records']


def references(specifications: dict) -> set[str]:
    return {digest for row in specifications.values() for _, digest in row['entries']}


def validate_epochs(path: Path, epochs: dict[str, tuple]) -> dict[str, tuple]:
    """Fresh epochs gate reuse; drift requires exact bytes, never a stat-only proof."""
    if not epochs:
        return {}
    folder = path.parent / DIRECTORY
    before = folder.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError('Canonical state records must remain a plain directory')
    current = {}
    for digest, prior in epochs.items():
        if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
            raise ValueError('Invalid canonical state record digest')
        record = folder / f'{digest}.json'
        observed = identity(record)
        if observed != prior[:5]:
            raw, observed = capture_file(record)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Canonical state record content does not match its manifest')
        current[digest] = (*observed, prior[5])
    after = folder.lstat()
    if ((before.st_dev, before.st_ino, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_mode)):
        raise ValueError('Canonical state record directory changed while validating')
    return current
