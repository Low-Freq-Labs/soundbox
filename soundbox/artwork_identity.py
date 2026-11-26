"""Frozen source/song artwork ownership and non-destructive saved-owner adoption."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .artwork import ArtCache, ArtworkSelection, _art_mutation, _art_read


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,
                         separators=(',', ':')).encode()).hexdigest()


def source_identity(library) -> tuple[str, str, str]:
    # Lexical normalization only: identity preparation never opens a source.
    return (str(library.source_kind), str(library.source_library_id),
            str(Path(library.source).expanduser().absolute()))


def track_identity(track) -> tuple[str, ...]:
    # Native UUIDs/stable imports do not depend on presentation paths. Avoid
    # decoding thousands of unchanged URIs for ordinary metadata readback.
    if track.source_uuid or _stable_local_id(str(track.track_id)):
        return saved_track_identity(str(track.track_id), source_uuid=str(track.source_uuid))
    return saved_track_identity(str(track.track_id), source_uuid=str(track.source_uuid),
                                native_id=str(track.native_id), native_path=str(track.native_path))


def saved_track_identity(track_id: str, *, source_uuid: str = '', native_id: str = '',
                         native_path: str = '') -> tuple[str, ...]:
    """One identity rule for saved pins, live rows and archived read-only rows."""
    if _stable_local_id(track_id):
        return ('local-id', track_id)
    if source_uuid:
        return ('uuid', source_uuid)
    return ('local', native_id or track_id, native_path)


def _stable_local_id(value: str) -> bool:
    return (value.startswith('sb-') and len(value) == 15
            and all(c in '0123456789abcdef' for c in value[3:]))


def _bindings(library) -> dict[str, tuple[str, ...]]:
    return {str(tid): track_identity(track) for tid, track in library.tracks_by_id.items()}


class BoundArtCache(ArtCache):
    """A logical-ID view whose persistent rows/files belong to frozen songs.

    Rows for identities absent from this view remain in the source index. This
    matters for hidden songs, old imported aliases and later source refreshes.
    Global photos/policy retain their own independent ownership at the art root.
    """
    def __init__(self, root: Path, library, *, read_only=False):
        self.source_identity = source_identity(library)
        self._bindings = _bindings(library)
        self.source_key = _digest(self.source_identity)
        # Logical aliases own separate choices. Including their stable ID on
        # every key avoids rekeying an existing song when an alias appears.
        self._keys = {tid: _digest((identity, tid)) for tid, identity in self._bindings.items()}
        self._stored_entries: dict[str, dict] = {}
        super().__init__(root)
        self.read_only = read_only
        self._default_assignments = {tid: pid for tid, pid in self._defaults['assignments'].items()
                                     if pid in self._defaults['photos']}

    @property
    def _records_root(self) -> Path:
        return self.root / 'scoped' / self.source_key

    def _index_file(self) -> Path:
        return self._records_root / 'index.json'

    def _track_key(self, track_id: str) -> str:
        try:
            return self._keys[track_id]
        except KeyError:
            raise ValueError('Artwork song is outside the captured library') from None

    @_art_read
    def for_library(self, library):
        if (not self._retired and self._generation == self._area.generation
                and self._revision == self._area.art_revision
                and source_identity(library) == self.source_identity
                and _bindings(library) == self._bindings):
            return self
        current = BoundArtCache(self.root, library, read_only=self.read_only)
        if (not self._retired and self._generation == self._area.generation
                and current.source_identity == self.source_identity):
            current._scope_owner = self.scope_owner
        return current

    @_art_read
    def observation_view(self, library=None):
        if library is not None and (source_identity(library) != self.source_identity
                or _bindings(library) != self._bindings):
            raise ValueError('Artwork observation belongs to a different source or song binding')
        return super().observation_view(library)

    @_art_read
    def handoff_selection(self, previous: ArtworkSelection) -> ArtworkSelection | None:
        """Carry an unchanged selected choice through an unrelated row refresh."""
        old = previous._cache
        if (not isinstance(old, BoundArtCache) or old._area is not self._area
                or old.source_identity != self.source_identity
                or not old.selection_current(previous)
                or any(old._bindings.get(row[0]) != self._bindings.get(row[0])
                       or row[0] not in self._bindings for row in previous._rows)):
            return None
        current = self.selection_token((row[0] for row in previous._rows), manual=previous._manual)
        if (previous._rows != current._rows or previous._edge_pool != current._edge_pool
                or previous._edge_seed != current._edge_seed):
            return None
        return current

    def _reload_index(self) -> None:
        try:
            raw = json.loads(self._index_path.read_text())
        except FileNotFoundError:
            raw = {'schema': 1, 'source': list(self.source_identity), 'tracks': {}}
        _validate_index(raw, self.source_key)
        if raw['source'] != list(self.source_identity):
            raise ValueError('Artwork source ownership changed')
        self._stored_entries = raw['tracks']
        self.index = {tid: self._stored_entries[key]['entry']
                      for tid, key in self._keys.items() if key in self._stored_entries}

    @_art_mutation
    def _save(self):
        if self.index.keys() - self._keys.keys():
            raise ValueError('Artwork song is outside the captured library')
        tracks = dict(self._stored_entries)
        for tid, key in self._keys.items():
            if tid in self.index:
                tracks[key] = {'identity': list(self._bindings[tid]), 'logical_id': tid,
                               'entry': self.index[tid]}
            else:
                tracks.pop(key, None)
        self._write(self._index_path, json.dumps({'schema': 1,
            'source': list(self.source_identity), 'tracks': tracks}, ensure_ascii=False).encode())

    def _assignment_key(self, tid: str) -> str:
        return f'{self.source_key}:{self._track_key(tid)}'

    def _load_defaults(self) -> dict:
        raw = super()._load_defaults()
        raw['assignments'] = {tid: raw['assignments'][self._assignment_key(tid)]
                              for tid in self._keys if self._assignment_key(tid) in raw['assignments']}
        return raw

    def _save_defaults(self) -> None:
        # Preserve every other source/song assignment, even if this source has
        # temporarily fewer tracks. Pool removals still use the shared photo map.
        old = ArtCache._load_defaults(self)['assignments']
        for tid in self._keys:
            old.pop(self._assignment_key(tid), None)
        old.update({self._assignment_key(tid): pid
                    for tid, pid in self._defaults['assignments'].items()})
        self._write(self._defaults_index_path, json.dumps(
            {**self._defaults, 'assignments': old}, indent=2).encode())

    @_art_read
    def kept_covers(self) -> dict:
        raw = ArtCache.kept_covers(self)
        return {tid: raw[key] for tid, key in self._keys.items() if key in raw}

    def _save_kept(self, kept: dict) -> None:
        raw = ArtCache.kept_covers(self)
        for tid, key in self._keys.items():
            raw.pop(key, None)
            if tid in kept:
                raw[key] = kept[tid]
        ArtCache._save_kept(self, raw)

    @_art_read
    def historical_image_path(self, track_id: str, *, source_uuid: str,
                              native_path: str = '', native_id: str = '') -> Path | None:
        """Read an adopted archived song, never a replacement using its old ID."""
        from .art_sources import excluded_source
        identity = saved_track_identity(track_id, source_uuid=source_uuid,
                                         native_id=native_id, native_path=native_path)
        if track_id in self._bindings and self._bindings[track_id] != identity:
            return None
        key = _digest((identity, track_id))
        row = self._stored_entries.get(key)
        if row is not None:
            if row['identity'] != list(identity):
                return None
            entry = row['entry']
            if (entry.get('status') == 'ok' and not excluded_source(
                    url=entry.get('src', ''), excluded_accounts=self.source_exclusions())):
                path = self._records_root / f"{key}.{entry['ext']}"
                if path.is_file():
                    return path
        assignment = ArtCache._load_defaults(self)['assignments'].get(f'{self.source_key}:{key}')
        if assignment in self._defaults['photos']:
            path = self._default_photo_path(assignment)
            if path.is_file():
                return path
        return None


def _validate_index(raw, source_key: str) -> None:
    if (not isinstance(raw, dict) or raw.get('schema') != 1
            or not isinstance(raw.get('source'), list) or len(raw['source']) != 3
            or any(type(value) is not str for value in raw['source'])
            or not raw['source'][0] or not Path(raw['source'][2]).is_absolute()
            or _digest(raw['source']) != source_key or not isinstance(raw.get('tracks'), dict)):
        raise ValueError('Invalid scoped artwork source index')
    for key, row in raw['tracks'].items():
        if (type(key) is not str or len(key) != 64 or any(c not in '0123456789abcdef' for c in key)
                or not isinstance(row, dict) or not isinstance(row.get('identity'), list)
                or not row['identity'] or any(type(v) is not str for v in row['identity'])
                or (row['identity'][0] == 'uuid' and len(row['identity']) != 2)
                or (row['identity'][0] == 'local' and len(row['identity']) != 3)
                or (row['identity'][0] == 'local-id' and (len(row['identity']) != 2
                    or not _stable_local_id(row['identity'][1])))
                or row['identity'][0] not in ('uuid', 'local', 'local-id')
                or type(row.get('logical_id')) is not str
                or not row['logical_id'] or '\0' in row['logical_id']
                or key != _digest((row['identity'], row['logical_id']))
                or not isinstance(row.get('entry'), dict)
                or row['entry'].get('status') not in ('ok', 'none', 'blocked')
                or (row['entry'].get('status') == 'ok' and row['entry'].get('ext') not in ('jpg', 'png'))):
            raise ValueError('Invalid scoped artwork song row')


def _validate_owner(value) -> None:
    if (not isinstance(value, dict) or value.get('schema') != 1
            or not isinstance(value.get('source'), list) or len(value['source']) != 3
            or any(type(v) is not str for v in value['source'])
            or not Path(value['source'][2]).is_absolute()
            or not isinstance(value.get('adopted'), dict)
            or not isinstance(value.get('unmapped'), list)
            or any(type(tid) is not str for tid in value['unmapped'])
            or not isinstance(value.get('bindings'), dict)
            or not isinstance(value.get('default_assignments'), dict)):
        raise ValueError('Invalid artwork saved-owner manifest')
    tracks = {}
    for tid, identity in value['bindings'].items():
        if (not tid or not isinstance(identity, list) or not identity
                or any(type(v) is not str for v in identity)
                or identity[0] not in ('uuid', 'local', 'local-id')
                or len(identity) != (3 if identity[0] == 'local' else 2)
                or identity[0] == 'local-id' and (identity[1] != tid or not _stable_local_id(tid))):
            raise ValueError('Invalid artwork saved-library binding')
    for tid, row in value['adopted'].items():
        if (not isinstance(row, dict) or not isinstance(row.get('files'), dict)
                or type(row.get('key')) is not str):
            raise ValueError('Invalid artwork saved-song ownership')
        for filename, digest in row['files'].items():
            if (not filename or Path(filename).is_absolute() or '..' in Path(filename).parts
                    or type(digest) is not str or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('Invalid artwork adoption file proof')
        tracks[row['key']] = {'identity': row.get('identity'), 'logical_id': tid, 'entry': row.get('entry')}
        if row.get('identity') != value['bindings'].get(tid):
            raise ValueError('Artwork adoption does not match its saved binding')
    _validate_index({'schema': 1, 'source': value['source'], 'tracks': tracks}, _digest(value['source']))
    for tid, row in value['default_assignments'].items():
        if (not isinstance(row, dict) or not isinstance(row.get('identity'), list)
                or row.get('key') != _digest((row['identity'], tid))
                or row['identity'] != value['bindings'].get(tid)
                or type(row.get('photo')) is not str):
            raise ValueError('Invalid artwork default-song ownership')


def validate_scoped_artwork(root: Path) -> None:
    """Validate portable scoped ownership/image presence without acquiring state."""
    root = Path(root)
    for path in (root / 'scoped').glob('*/index.json'):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('Scoped artwork cannot be a symlink')
        raw = json.loads(path.read_text())
        _validate_index(raw, path.parent.name)
        for key, row in raw['tracks'].items():
            entry = row['entry']
            if entry['status'] == 'ok':
                image = path.parent / f"{key}.{entry['ext']}"
                if image.is_symlink() or not image.is_file():
                    raise ValueError('Missing scoped artwork image')
    owner = root / 'ownership.json'
    if owner.exists():
        if owner.is_symlink():
            raise ValueError('Artwork ownership cannot be a symlink')
        _validate_owner(json.loads(owner.read_text()))


def prepare_legacy_restore(selected_art_root: Path, merged_art_root: Path, *,
                           source_identity: tuple[str, str, str],
                           track_identities: dict[str, tuple[str, ...]]) -> dict:
    """Prepare an explicitly proved old backup's artwork in private staging.

    The backup owner supplies saved-source/identity proof. No current source is
    consulted, and unknown per-track choices reject restoration before writes.
    Existing files at the old root remain intact; only this source's active
    scoped index/decisions are replaced with the selected snapshot's semantics.
    """
    selected = Path(selected_art_root)
    merged = Path(merged_art_root)
    if (not isinstance(source_identity, (list, tuple)) or len(source_identity) != 3
            or any(type(v) is not str for v in source_identity)
            or not Path(source_identity[2]).is_absolute()):
        raise ValueError('Restoring old artwork requires a proved saved source')
    source_key = _digest(source_identity)
    folder = merged / 'scoped' / source_key
    selected_index = selected / 'index.json'
    raw = json.loads(selected_index.read_text()) if selected_index.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError('Invalid old artwork index')
    tracks = {}
    writes: dict[Path, bytes] = {}
    keys = {}
    for tid, entry in raw.items():
        identity = track_identities.get(tid)
        if (identity is None or not tid or Path(tid).name != tid or tid in ('.', '..')):
            raise ValueError('Old artwork has an unproved song owner; reconcile before restoring')
        key = _digest((identity, tid))
        keys[tid] = key
        tracks[key] = {'identity': list(identity), 'logical_id': tid, 'entry': entry}
    index = {'schema': 1, 'source': list(source_identity), 'tracks': tracks}
    _validate_index(index, source_key)
    kept_file = selected / 'review-kept.json'
    kept = json.loads(kept_file.read_text()) if kept_file.exists() else {}
    if not isinstance(kept, dict):
        raise ValueError('Invalid old artwork review decisions')
    for tid, key in keys.items():
        entry = raw[tid]
        if entry['status'] == 'ok':
            writes[folder / f"{key}.{entry['ext']}"] = (selected / f"{tid}.{entry['ext']}").read_bytes()
        for directory, suffixes in (('review', ('json', 'image')),
                                   ('source-recovery', ('json', 'image', 'review.json', 'review.image'))):
            for suffix in suffixes:
                original = selected / directory / f'{tid}.{suffix}'
                if original.exists():
                    writes[folder / directory / f'{key}.{suffix}'] = original.read_bytes()
    writes[folder / 'index.json'] = json.dumps(index, ensure_ascii=False).encode()
    writes[folder / 'review-kept.json'] = json.dumps(
        {key: kept[tid] for tid, key in keys.items() if tid in kept}, ensure_ascii=False).encode()
    defaults_path = merged / 'defaults/index.json'
    selected_defaults = selected / 'defaults/index.json'
    if selected_defaults.exists():
        defaults = json.loads(selected_defaults.read_text())
        assignments = defaults.get('assignments', {})
        translated = {}
        for tid, photo in assignments.items():
            identity = track_identities.get(tid)
            if identity is None:
                raise ValueError('Old default artwork has an unproved song owner; reconcile before restoring')
            translated[f'{source_key}:{_digest((identity, tid))}'] = photo
        # The selected global photo pool is authoritative; assignments for
        # other sources survive only when their accepted photo still exists.
        current = json.loads(defaults_path.read_text()) if defaults_path.exists() else {}
        remaining = {key: value for key, value in current.get('assignments', {}).items()
                     if not key.startswith(source_key + ':') and ':' in key
                     and value in defaults['photos']}
        writes[defaults_path] = json.dumps(
            {**defaults, 'assignments': {**assignments, **remaining, **translated}}, indent=2).encode()
    # All validation/reads precede mutation of the disposable stage. Old active
    # Undo decisions must not attach to restored covers from another snapshot.
    for directory in ('review', 'source-recovery'):
        for path in (folder / directory).glob('*'):
            if path.is_file():
                path.unlink()
    for path, data in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {'source': list(source_identity), 'adopted': len(tracks),
            'default_assignments': len(translated) if selected_defaults.exists() else 0}


def adopt_legacy(cache: ArtCache, saved_library) -> BoundArtCache:
    """Copy only proved saved-song owners, retaining every original byte/path.

    The caller must establish that ``saved_library`` is the canonical saved
    source, including imported aliases. Opening a library grants no adoption.
    """
    target = BoundArtCache(cache.root, saved_library, read_only=cache.read_only)
    _adopt(target)
    return target


@_art_mutation
def _adopt(target: BoundArtCache) -> None:
    manifest = target.root / 'ownership.json'
    if manifest.exists():
        raw = json.loads(manifest.read_text())
        _validate_owner(raw)
        if raw['source'] != list(target.source_identity):
            raise ValueError('Legacy artwork already belongs to a different saved source')
        return
    legacy = ArtCache(target.root)
    kept = legacy.kept_covers()
    adopted = {}
    for tid in sorted(set(legacy.index) & target._keys.keys()):
        key = target._track_key(tid)
        entry = copy.deepcopy(legacy.index[tid])
        if key in target._stored_entries:
            raise ValueError('Scoped artwork already exists; saved-owner adoption needs review')
        files = {}
        if entry.get('status') == 'ok':
            original = legacy._image_file(tid, entry.get('ext', 'jpg'))
            legacy._area.path(str(original.relative_to(legacy._area.root)))
            data = original.read_bytes()
            target._write(target._image_file(tid, entry.get('ext', 'jpg')), data)
            files[str(original.relative_to(target.root))] = hashlib.sha256(data).hexdigest()
        for directory, suffixes in (('review', ('json', 'image')),
                                   ('source-recovery', ('json', 'image', 'review.json', 'review.image'))):
            for suffix in suffixes:
                original = target.root / directory / f'{tid}.{suffix}'
                if original.exists():
                    legacy._area.path(str(original.relative_to(legacy._area.root)))
                    data = original.read_bytes()
                    target._write(target._records_root / directory / f'{key}.{suffix}', data)
                    files[str(original.relative_to(target.root))] = hashlib.sha256(data).hexdigest()
        target.index[tid] = entry
        adopted[tid] = {'key': key, 'identity': list(target._bindings[tid]),
                        'entry': entry, 'files': files}
    target._save()
    target._save_kept({tid: copy.deepcopy(kept[tid]) for tid in adopted if tid in kept})
    # Retain exact accepted generic choices when their saved owner is known.
    assignments = legacy._defaults['assignments']
    target._defaults['assignments'].update({tid: pid for tid, pid in assignments.items()
        if tid in target._keys and pid in target._defaults['photos']})
    target._save_defaults()
    target._write(manifest, json.dumps({'schema': 1,
        'source': list(target.source_identity), 'adopted': adopted,
        'bindings': {tid: list(identity) for tid, identity in target._bindings.items()},
        'unmapped': sorted(set(legacy.index) - adopted.keys()),
        'default_assignments': {tid: {'key': target._track_key(tid),
            'identity': list(target._bindings[tid]), 'photo': pid}
            for tid, pid in assignments.items() if tid in target._keys},
        'unmapped_default_assignments': sorted(set(assignments) - target._keys.keys()),
        'legacy_index_sha256': hashlib.sha256((target.root / 'index.json').read_bytes()).hexdigest()
            if (target.root / 'index.json').exists() else None}, ensure_ascii=False).encode())
