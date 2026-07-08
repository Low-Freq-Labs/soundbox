"""Library snapshot cache — a plain-JSON dump of the last-loaded rekordbox
library so the app can paint instantly on open instead of showing a blank
window until the live DB read finishes.

This is a pure CACHE (derived from rekordbox), kept separate from the user's
`library.json` (overrides / playlists / settings). It holds only the raw track
+ playlist data needed for the first paint; the live refresh that runs right
after replaces it with current data, and re-snapshots. Loading tolerates any
corruption/schema drift by returning None — a bad cache never blocks startup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .rekordbox_xml import Library, Playlist, Track

SCHEMA = 1

# The Track fields worth persisting (everything the browser/export needs).
# `path`/`duration_str`/`file_type` are computed properties, not stored.
_TRACK_FIELDS = (
    "track_id", "name", "artist", "location_uri", "bpm", "key", "total_time",
    "date_added", "genre", "kind", "size", "bitrate", "rating", "year", "imported",
)
_PLAYLIST_FIELDS = ("name", "folder_path", "keys", "key_type", "pid")


def dump(lib: Library, path: str | Path) -> None:
    """Serialize a Library to `path` atomically. Best-effort — a failure just
    means next open paints from the previous snapshot (or blank)."""
    data = {
        "schema": SCHEMA,
        "source": str(lib.source_xml),
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
        if data.get("schema") != SCHEMA:
            return None
        tracks = {}
        for row in data["tracks"]:
            t = Track(**dict(zip(_TRACK_FIELDS, row)))
            tracks[t.track_id] = t
        playlists = [Playlist(**dict(zip(_PLAYLIST_FIELDS, row)))
                     for row in data["playlists"]]
        if not tracks:
            return None
        return Library(tracks_by_id=tracks, playlists=playlists,
                       source_xml=data.get("source", "cache"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
