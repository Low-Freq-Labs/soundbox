"""Library snapshot cache — a plain-JSON dump of the last-loaded source library
so the app can paint instantly instead of showing a blank window until the
authoritative reader finishes.

This is a pure derived CACHE, kept separate from the user's
`library.json` (overrides / playlists / settings). It holds only the raw track
+ playlist data needed for the first paint; the source refresh that runs right
after replaces it with current data and re-snapshots. Loading tolerates any
corruption/schema drift by returning None — a bad cache never blocks startup.
"""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from .library import Library, LibraryTag, Playlist, TagCategory, Track

SCHEMA = 13
_READABLE_SCHEMAS = {4, 5, 6, 7, 8, 9, 10, 11, 12, SCHEMA}
_MAX_CACHE_BYTES = 256 * 1024**2
_MAX_TAG_TIMES = 16_384

# The Track fields worth persisting (everything the browser/export needs).
# `path`/`duration_str`/`file_type` are computed properties, not stored.
# added_ts MUST be here — it drives the Collection newest-added sort; without it
# the instant-paint snapshot loses it and every track falls back to birthtime.
_TRACK_FIELDS = (
    "track_id", "name", "artist", "location_uri", "bpm", "key", "total_time",
    "date_added", "added_ts", "genre", "kind", "size", "bitrate", "rating", "year",
    "imported", "source_analysis_path", "source_cues", "source_uuid", "source_tag_ids",
    "source_cue_revision", "source_cue_refs",
    "info", "source_native_id", "source_native_path",
    "source_tag_times",
)
_PLAYLIST_FIELDS = ("name", "folder_path", "keys", "pid")
_TEXT_FIELDS = frozenset((
    "track_id", "name", "artist", "location_uri", "bpm", "key", "date_added",
    "genre", "kind", "bitrate", "rating", "year", "source_analysis_path",
    "source_uuid", "source_cue_revision", "source_native_id", "source_native_path",
))


def _track_values(row, fields):
    if not isinstance(row, list) or not 4 <= len(row) <= len(_TRACK_FIELDS):
        return None
    values = dict(zip(fields, row))
    if any(not isinstance(values[key], str) for key in _TEXT_FIELDS if key in values):
        return None
    for key in ('total_time', 'added_ts', 'size'):
        value = values.get(key, 0)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
    if type(values.get('imported', False)) is not bool or not isinstance(values.get('info', {}), dict):
        return None
    for key in ('source_cues', 'source_cue_refs'):
        cues = values.get(key)
        if cues is not None and (
                not isinstance(cues, list) or any(not isinstance(cue, dict) for cue in cues)):
            return None
    tags = values.get('source_tag_ids')
    if tags is not None and (not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)):
        return None
    times = values.get('source_tag_times', {})
    if not isinstance(times, dict) or len(times) > _MAX_TAG_TIMES:
        return None
    assigned = set(tags or ())
    if any(not isinstance(tag, str) or not 0 < len(tag) <= 4096 or tag not in assigned
           or type(timestamp) not in (int, float) or not math.isfinite(timestamp)
           or not 0 <= timestamp < 253402300800 for tag, timestamp in times.items()):
        return None
    if 'source_cue_refs' in values and values['source_cue_refs'] is None:
        return None
    return values if values['track_id'] else None


def _playlist_values(row, schema):
    if not isinstance(row, list):
        return None
    if schema == 4:
        if len(row) != 5:
            return None
        values = dict(name=row[0], folder_path=row[1], keys=row[2], pid=row[4])
    else:
        if not 3 <= len(row) <= len(_PLAYLIST_FIELDS):
            return None
        values = dict(zip(_PLAYLIST_FIELDS, row))
    if (not isinstance(values['name'], str)
            or not isinstance(values.get('pid', ''), str)
            or any(not isinstance(values[key], list)
                   or any(not isinstance(value, str) for value in values[key])
                   for key in ('folder_path', 'keys'))):
        return None
    return values


def dump(lib: Library, path: str | Path) -> None:
    """Serialize a Library to `path` atomically. Best-effort — a failure just
    means next open paints from the previous snapshot (or blank)."""
    data = {
        "schema": SCHEMA,
        "source": str(lib.source),
        "source_kind": lib.source_kind,
        "source_library_id": lib.source_library_id,
        "source_playlist_catalog": lib.source_playlist_catalog,
        "source_tags": [[tag.tag_id, tag.name, tag.category, tag.source_uuid, tag.category_id, tag.category_uuid]
                        for tag in lib.source_tags],
        "source_tag_categories": [[category.category_id, category.name, category.source_uuid]
                                  for category in lib.source_tag_categories],
        "tracks": ([getattr(t, f) for f in _TRACK_FIELDS]
                   for t in lib.tracks_by_id.values()),
        "playlists": ([getattr(p, f) for f in _PLAYLIST_FIELDS]
                      for p in lib.playlists),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # Encode one record at a time: the C encoder otherwise holds the GIL for the
    # entire library. Bounded writes also let model callbacks run between chunks.
    with tmp.open('w', encoding='utf-8') as handle:
        handle.write('{')
        for field_index, (key, value) in enumerate(data.items()):
            handle.write((', ' if field_index else '') + json.dumps(key) + ': ')
            if key=='source_playlist_catalog' and isinstance(value,dict) and value:
                handle.write('{')
                for i,(name,rows) in enumerate(value.items()):
                    handle.write((', ' if i else '')+json.dumps(name)+': ')
                    _write_rows(handle,rows) if isinstance(rows,list) else handle.write(json.dumps(rows))
                handle.write('}')
            elif isinstance(value,(list,Iterator)):
                _write_rows(handle,value)
            else:
                handle.write(json.dumps(value))
        handle.write('}')
    os.replace(tmp, p)


def _write_rows(handle,rows):
    handle.write('[')
    chunks,size=[],0
    for index,row in enumerate(rows):
        encoded=(', ' if index else '')+json.dumps(row)
        chunks.append(encoded);size+=len(encoded)
        if size>=32*1024:
            handle.write(''.join(chunks));chunks,size=[],0
    if chunks:handle.write(''.join(chunks))
    handle.write(']')


def load(path: str | Path) -> Library | None:
    """Reconstruct the cached Library, or None on any problem (missing, corrupt,
    wrong schema) — the caller falls back to the live read alone."""
    try:
        # This derived cache is optional. Bound allocation before parsing, and
        # reject a growing/partial file rather than pushing malformed values
        # into the first UI paint. No cache observation is source authority.
        p = Path(path)
        details = p.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_CACHE_BYTES:
            return None
        with p.open('rb') as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > _MAX_CACHE_BYTES:
                return None
            payload = handle.read(size + 1)
        if len(payload) != size:
            return None
        data = json.loads(payload.decode('utf-8'))
        if (not isinstance(data, dict) or not isinstance(data.get('tracks'), list)
                or not isinstance(data.get('playlists'), list)
                or any(not isinstance(data.get(key, ''), str)
                       for key in ('source', 'source_kind', 'source_library_id'))):
            return None
        schema = data.get("schema")
        if schema not in _READABLE_SCHEMAS:
            return None
        catalog=data.get('source_playlist_catalog',{})
        if not isinstance(catalog,dict):return None
        if catalog:
            from .rekordbox_playlist_plan import revision
            if (set(catalog)!={'nodes','members','revision'}
                    or not isinstance(catalog['nodes'],list) or not isinstance(catalog['members'],list)
                    or any(not isinstance(row,dict) or not {'ID','Name','ParentID','Seq','Attribute','rb_local_deleted'}<=row.keys() for row in catalog['nodes'])
                    or any(not isinstance(row,dict) or not {'ID','PlaylistID','ContentID','TrackNo','rb_local_deleted'}<=row.keys() for row in catalog['members'])
                    or revision(catalog['nodes'],catalog['members'])!=catalog['revision']):return None
        tracks = {}
        fields = (_TRACK_FIELDS[:20] if schema < 7 else _TRACK_FIELDS[:22] if schema == 7
                  else _TRACK_FIELDS[:-1] if schema < 12 else _TRACK_FIELDS)
        for row in data["tracks"]:
            values = _track_values(row, fields)
            if values is None:
                return None
            t = Track(**values)
            if t.track_id in tracks:
                return None
            tracks[t.track_id] = t
        playlists = []
        for row in data['playlists']:
            values = _playlist_values(row, schema)
            if values is None:
                return None
            playlists.append(Playlist(**values))
        tags = data.get('source_tags', [])
        if (not isinstance(tags, list) or any(not isinstance(row, list)
                or not 2 <= len(row) <= 6 or any(not isinstance(value, str) for value in row)
                for row in tags)):
            return None
        categories = data.get('source_tag_categories', []) if schema >= 13 else []
        if (not isinstance(categories, list) or len(categories) > 1024
                or any(not isinstance(row, list) or len(row) != 3
                       or any(not isinstance(value, str) or len(value) > 4096 for value in row)
                       for row in categories)
                or len({row[0] for row in categories}) != len(categories)):
            return None
        if not tracks:
            return None
        return Library(
            tracks_by_id=tracks,
            playlists=playlists,
            source=data.get("source", "cache"),
            source_kind=data.get("source_kind", "cache"),
            source_library_id=data.get("source_library_id", ""),
            source_playlist_catalog=data.get("source_playlist_catalog", {}),
            source_tags=[LibraryTag(*(row if schema >= 13 else row[:4])) for row in tags],
            source_tag_categories=[TagCategory(*row) for row in categories],
        )
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        return None
