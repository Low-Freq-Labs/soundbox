"""Metadata-only proposals for an explicitly captured canonical import identity.

No discovery, source authority, musical reconciliation or file I/O.
Matching exact paths is intentionally separate from byte-identical-file deduplication.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .rekordbox_write import WriteProofError
from .native_registration_plan import _native_id, _uuid


@dataclass(frozen=True)
class Binding:
    local_id: str
    source: str
    library_id: str
    native_id: str
    native_uuid: str
    canonical: str

    def validate(self):
        if not isinstance(self.local_id, str) or not self.local_id:
            raise WriteProofError('Stable logical track identity is required')
        _canonical(self.source); _canonical(self.canonical)
        _native_id(self.library_id); _native_id(self.native_id); _uuid(self.native_uuid)


@dataclass(frozen=True)
class IdentityDecision:
    action: Literal['register', 'reuse', 'conflict']
    binding: Binding | None
    candidates: tuple[dict, ...]
    reason: str = ''


def _canonical(value):
    if (not isinstance(value, str) or not value or '\0' in value or not Path(value).is_absolute()
            or str(Path(value)) != value or '..' in Path(value).parts):
        raise WriteProofError('Explicit canonical path facts are required')


def resolve(*, local_id, canonical, source, library_id, native_rows, previous=None, other_bindings=()):
    """Propose reuse/register; a verified transaction must still own publication.

    Callers capture/verify the canonical path; this pure planner never resolves
    thousands of paths on disk or reads/hydrates source audio to compare names.
    """
    _canonical(canonical); _canonical(source); _native_id(library_id)
    if not isinstance(local_id, str) or not local_id:
        raise WriteProofError('Stable logical track identity is required')
    if previous is not None:
        previous.validate()
        if (previous.local_id, previous.source, previous.library_id) != (local_id, source, library_id):
            return IdentityDecision('conflict', None, (), 'Previous binding belongs to another track/source/library')
    active = []
    matching = [deepcopy(row) for row in native_rows if row.get('FolderPath') == canonical]
    for row in matching:
        if type(row.get('rb_local_deleted')) is not int or row['rb_local_deleted'] not in (0, 1):
            return IdentityDecision('conflict', None, tuple(matching), 'Unknown native row history')
        if row['rb_local_deleted'] == 0:
            _native_id(row.get('ID')); _uuid(row.get('UUID'))
            active.append(row)
    candidates = tuple(matching)
    if len(matching) > 1:
        return IdentityDecision('conflict', None, candidates, 'Multiple native rows share the captured path; preserve history')
    if not active:
        if previous is not None or matching:
            return IdentityDecision('conflict', None, candidates, 'Missing/historical native identity needs explicit reconciliation')
        return IdentityDecision('register', None, ())
    row = active[0]
    binding = Binding(local_id, source, library_id, row['ID'], row['UUID'], canonical)
    if previous is not None and previous != binding:
        return IdentityDecision('conflict', None, candidates, 'Native identity/path changed since the prior binding')
    for other in other_bindings:
        other.validate()
        if ((other.source, other.library_id, other.native_id) == (source, library_id, binding.native_id)
                and other.local_id != local_id):
            return IdentityDecision('conflict', None, candidates, 'Two logical tracks need a reviewed alias/curation merge')
    return IdentityDecision('reuse', binding, candidates)


def project(library, bindings):
    """Return a detached logical-ID view of a freshly verified native library.

    This is identity projection only: native musical fields and the raw source
    catalog remain unchanged. The caller must supply bindings from a verified
    registration/reuse receipt; this function neither reads nor writes SQL.
    Every binding is checked before any result is returned. Old imports keep
    their logical IDs for curation, custom playlists, caches and device records.
    """
    if library.source_kind != 'rekordbox-db':
        raise WriteProofError('Identity projection requires a native library')
    by_native = {}; logical = set()
    for binding in bindings:
        binding.validate()
        if (binding.source != str(library.source) or binding.library_id != library.source_library_id
                or binding.native_id in by_native or binding.local_id in logical):
            raise WriteProofError('Conflicting identity projection source or aliases')
        track = library.tracks_by_id.get(binding.native_id)
        if (track is None or track.native_id != binding.native_id or track.source_uuid != binding.native_uuid
                or str(track.path) != binding.canonical or track.imported):
            raise WriteProofError('Native track changed since its verified binding')
        if binding.local_id != binding.native_id and binding.local_id in library.tracks_by_id:
            raise WriteProofError('Logical identity is already occupied; preserve both tracks')
        by_native[binding.native_id] = binding; logical.add(binding.local_id)
    tracks = {}
    for tid, track in library.tracks_by_id.items():
        binding = by_native.get(tid)
        if binding:
            tracks[binding.local_id] = replace(track,track_id=binding.local_id,source_native_id=binding.native_id)
        else:
            tracks[tid] = replace(track)
    def key(tid):
        return by_native[tid].local_id if tid in by_native else tid
    playlists = [replace(p,keys=[key(tid) for tid in p.keys],added_keys=[key(tid) for tid in p.added_keys])
                 for p in library.playlists]
    return replace(library,tracks_by_id=tracks,playlists=playlists)
