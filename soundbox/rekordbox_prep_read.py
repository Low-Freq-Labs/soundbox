"""Read-only selected-track cue/analysis capture shared by prep save domains.

These helpers grant no SQL or file mutation capability. The owning domain keeps
its independent authority, shared lease, recovery and transaction boundaries.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from .rekordbox_cues import CueSnapshot
from .rekordbox_grid import GridSnapshot, read_grid
from .rekordbox_write import ConflictError, WriteProofError, _regular


def cue_snapshot(rows, conn, track_id: str) -> CueSnapshot:
    tracks = rows(conn, 'djmdContent', ' WHERE ID=?', (track_id,))
    if len(tracks) != 1 or tracks[0]['rb_local_deleted']:
        raise ConflictError('The pilot track is missing or deleted')
    aggregates = rows(conn, 'contentCue', ' WHERE ContentID=?', (track_id,))
    if len(aggregates) > 1:
        raise WriteProofError('Multiple cue aggregates for the pilot track')
    cues = rows(conn, 'djmdCue', ' WHERE ContentID=?', (track_id,))
    return CueSnapshot(tracks[0], tuple(cues), aggregates[0] if aggregates else None)


def analysis_paths(database: Path, track: dict, track_id: str, track_uuid: str) -> dict[str, Path]:
    try:
        if str(UUID(track_uuid)) != track_uuid:
            raise ValueError('Noncanonical UUID')
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConflictError('Grid track/analysis identity changed') from exc
    relative = f'/PIONEER/USBANLZ/{track_uuid[:3]}/{track_uuid[3:]}/ANLZ0000.DAT'
    if (track.get('ID') != track_id or track.get('UUID') != track_uuid
            or track.get('AnalysisDataPath') != relative):
        raise ConflictError('Grid track/analysis identity changed')
    dat = database.parent / 'share' / relative.lstrip('/')
    return {'DAT': dat, 'EXT': dat.with_suffix('.EXT')}


def analysis_bytes(path: Path, recovery_root: Path) -> bytes:
    _regular(path)
    if (path != path.resolve() or path.stat().st_uid != os.getuid()
            or path.stat().st_dev != recovery_root.stat().st_dev):
        raise WriteProofError('Analysis/recovery path redirects or crosses filesystems')
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ConflictError('Analysis changed during capture')
    return data


def grid_snapshot(database: Path, recovery_root: Path, track: dict) -> GridSnapshot:
    """Read display/cache data without accepting a grid-write metadata profile."""
    paths = analysis_paths(database, track, track['ID'], track['UUID'])
    dat = analysis_bytes(paths['DAT'], recovery_root)
    read_grid(dat)
    return GridSnapshot(track, dat, analysis_bytes(paths['EXT'], recovery_root))
