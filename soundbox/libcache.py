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
import os
from pathlib import Path

from .library import Library, Playlist, Track

SCHEMA = 5
_READABLE_SCHEMAS = {4, SCHEMA}

# The Track fields worth persisting (everything the browser/export needs).
# `path`/`duration_str`/`file_type` are computed properties, not stored.
# added_ts MUST be here — it drives the Collection newest-added sort; without it
# the instant-paint snapshot loses it and every track falls back to birthtime.
_TRACK_FIELDS = (
    "track_id", "name", "artist", "location_uri", "bpm", "key", "total_time",
    "date_added", "added_ts", "genre", "kind", "size", "bitrate", "rating", "year",
    "imported", "source_analysis_path", "source_cues",
)
_PLAYLIST_FIELDS = ("name", "folder_path", "keys", "pid")


def dump(lib: Library, path: str | Path) -> None:
    """Serialize a Library to `path` atomically. Best-effort — a failure just
    means next open paints from the previous snapshot (or blank)."""
    data = {
        "schema": SCHEMA,
        "source": str(lib.source),
        "source_kind": lib.source_kind,
        "tracks": [[getattr(t, f) for f in _TRACK_FIELDS]
                   for t in lib.tracks_by_id.values()],
        "playlists": [[getattr(p, f) for f in _PLAYLIST_FIELDS]
                      for p in lib.playlists],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def load(path: str | Path) -> Library | None:
    """Reconstruct the cached Library, or None on any problem (missing, corrupt,
    wrong schema) — the caller falls back to the live read alone."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        schema = data.get("schema")
        if schema not in _READABLE_SCHEMAS:
            return None
        tracks = {}
        for row in data["tracks"]:
            t = Track(**dict(zip(_TRACK_FIELDS, row)))
            tracks[t.track_id] = t
        if schema == 4:
            # V4 included the rekordbox-XML-only key_type before pid. Normalize
            # it away without sacrificing the one-time instant paint.
            playlists = [
                Playlist(name=row[0], folder_path=row[1], keys=row[2], pid=row[4])
                for row in data["playlists"]
            ]
        else:
            playlists = [Playlist(**dict(zip(_PLAYLIST_FIELDS, row)))
                         for row in data["playlists"]]
        if not tracks:
            return None
        return Library(
            tracks_by_id=tracks,
            playlists=playlists,
            source=data.get("source", "cache"),
            source_kind=data.get("source_kind", "cache"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
