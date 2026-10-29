"""Portable library schema, recovery and durable persistence, independent of Qt."""
from __future__ import annotations

import copy
import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_io import StateChangedError, atomic_bytes, state_area


_MAX_STATE_BYTES = 64 * 1024**2


class InvalidState(ValueError):
    pass


def validate_state(data: Any) -> dict:
    if not isinstance(data, dict):
        raise InvalidState('Library must be a JSON object')
    maps = ('custom', 'imported', 'artist_overrides', 'title_overrides',
            'playlist_removals', 'playlist_additions', 'artist_aliases', 'settings')
    for key in maps:
        if key in data and not isinstance(data[key], dict):
            raise InvalidState(f'{key} must be an object')
    for key in ('custom', 'playlist_additions'):
        for values in data.get(key, {}).values():
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise InvalidState(f'{key} memberships must be lists of track IDs')
    for key in ('artist_overrides', 'title_overrides', 'artist_aliases'):
        if any(not isinstance(v, str) for v in data.get(key, {}).values()):
            raise InvalidState(f'{key} values must be text')
    for values in [data.get('hidden_tracks', {}), *data.get('playlist_removals', {}).values()]:
        if isinstance(values, list):
            valid = all(isinstance(v, str) for v in values)
        elif isinstance(values, dict):
            valid = all(isinstance(v, str) for v in values.values())
        else:
            valid = False
        if not valid:
            raise InvalidState('Hidden/removal entries must contain track IDs and dates')
    if 'last_xml' in data and not isinstance(data['last_xml'], str):
        raise InvalidState('last_xml must be text')
    for row in data.get('imported', {}).values():
        if not isinstance(row, dict):
            raise InvalidState('Imported track records must be objects')
        for field in ('path', 'name', 'artist', 'bookmark'):
            if field in row and not isinstance(row[field], str):
                raise InvalidState(f'Imported {field} must be text')
    settings = data.get('settings', {})
    if 'custom_playlist_tree' in settings:
        from .playlist_folders import validate
        try:validate(settings['custom_playlist_tree'])
        except (ValueError, TypeError) as exc:raise InvalidState('Invalid SoundBox playlist folders') from exc
    if 'custom_playlist_expanded' in settings and (not isinstance(settings['custom_playlist_expanded'], list)
            or any(not isinstance(key, str) for key in settings['custom_playlist_expanded'])):
        raise InvalidState('Invalid expanded playlist folders')
    for key in ('hide_untracked', 'hide_nokey', 'hide_short'):
        if key in settings and not isinstance(settings[key], bool):
            raise InvalidState(f'{key} must be a boolean')
    for key in ('hide_short_secs', 'waveform_zoom'):
        if key in settings and (type(settings[key]) not in (int, float)
                                or not math.isfinite(settings[key]) or settings[key] < 0):
            raise InvalidState(f'{key} must be a finite nonnegative number')
    for key in ('title_rules_off', 'title_words'):
        if key in settings and (not isinstance(settings[key], list)
                                or any(not isinstance(v, str) for v in settings[key])):
            raise InvalidState(f'{key} must be a list of text')
    for key in ('stock_ipod_models', 'stock_ipod_identity'):
        if key in settings and not isinstance(settings[key], dict):
            raise InvalidState(f'{key} must be an object')
    for row in data.get('imported', {}).values():
        for key in ('time', 'size', 'added_ts'):
            if key in row and (type(row[key]) not in (int, float)
                               or not math.isfinite(row[key]) or row[key] < 0):
                raise InvalidState(f'Imported {key} must be a finite nonnegative number')
    if 'analysis_results' in settings:
        from .analysis import validate_result, result_key
        results = settings['analysis_results']
        if not isinstance(results, dict):
            raise InvalidState('analysis_results must be an object')
        for key, record in results.items():
            if not isinstance(record, dict):
                raise InvalidState('Analysis results must be objects')
            try:
                validate_result(record)
                if key != result_key(record['path']):
                    raise ValueError('Analysis result path binding changed')
            except (ValueError, TypeError, KeyError) as exc:
                raise InvalidState('Invalid saved analysis result') from exc
    # Unknown namespaces and settings remain byte-value-equivalent on roundtrip.
    return copy.deepcopy(data)


def read_state(path: Path) -> dict:
    if path.stat().st_size > _MAX_STATE_BYTES:
        raise InvalidState('Library JSON exceeds the 64 MiB safety limit')
    return validate_state(json.loads(path.read_text(encoding='utf-8')))


@dataclass(frozen=True)
class Recovery:
    damaged: Path
    backup: Path | None


class StateRepository:
    def __init__(self, path: Path):
        self.path = path
        self.area = state_area(path.parent)
        self.generation = self.area.generation
        self._document: dict = {}
        self.recovery: Recovery | None = None
        self.writable = True
        self.backup_error: OSError | None = None
        self._saved_payload: bytes | None = None
        self._saved_identity: tuple | None = None

    def _identity(self) -> tuple | None:
        try:
            st = self.path.stat()
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            return None

    def load(self) -> dict:
        with self.area.lock:
            self.generation = self.area.generation
            self._saved_payload = None
            self._saved_identity = None
            self.recovery = None
            self.writable = True
            if not self.path.exists():
                self._document = {}
                return {}
            try:
                data = read_state(self.path)
            except (OSError, ValueError):
                damaged = self.path.with_name(f'library.corrupt-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json')
                # Preserve the exact damaged bytes; never rename away the only
                # library before a recoverable replacement has been selected.
                atomic_bytes(damaged, self.path.read_bytes())
                data = {}
                chosen = None
                candidates = sorted((self.path.parent / 'backups').glob('*.json'),
                                    key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for candidate in candidates:
                    try:
                        data = read_state(candidate)
                    except (OSError, ValueError):
                        continue
                    chosen = candidate
                    self.area.publish({'library.json': candidate})
                    break
                self.recovery = Recovery(damaged, chosen)
                # No usable backup: allow explicit restore, but forbid ordinary
                # UI autosaves from burying a damaged library with empty state.
                self.writable = chosen is not None
            self._document = data
            return copy.deepcopy(data)

    def backup(self) -> None:
        with self.area.lock:
            if not self.path.exists():
                return
            read_state(self.path)
            directory = self.path.parent / 'backups'
            directory.mkdir(parents=True, exist_ok=True)
            current = self.path.read_bytes()
            old = sorted(directory.glob('library-*.json'))
            if old and (time.time() - old[-1].stat().st_mtime < 3600
                        or old[-1].read_bytes() == current):
                return
            dest = directory / f'library-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json'
            atomic_bytes(dest, current)
            for path in sorted(directory.glob('library-*.json'))[:-10]:
                path.unlink()

    def save(self, changes: dict) -> None:
        with self.area.lock:
            if self.generation != self.area.generation:
                raise StateChangedError('Library was restored; reload before saving')
            if not self.writable:
                raise OSError('Library recovery required; restore a valid backup before saving')
            document = validate_state({**self._document, **changes})
            payload = json.dumps(document, indent=2, allow_nan=False).encode('utf-8')
            if len(payload) > _MAX_STATE_BYTES:
                raise InvalidState('Library JSON exceeds the 64 MiB safety limit; previous state was preserved')
            if self._saved_payload is None:
                identity = self._identity()
                try:
                    existing = self.path.read_bytes()
                except OSError:
                    existing = None
                if existing == payload and identity is not None and identity == self._identity():
                    self._document = document
                    self._saved_payload = payload
                    self._saved_identity = identity
            if (payload == self._saved_payload and self._saved_identity is not None
                    and self._identity() == self._saved_identity):
                return
            self.backup_error = None
            try:
                self.backup()
            except OSError as exc:
                self.backup_error = exc
            self.area.publish({'library.json': payload})
            self._document = document
            self._saved_payload = payload
            self._saved_identity = self._identity()
