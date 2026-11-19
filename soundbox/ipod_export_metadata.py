"""Frozen iPod musical metadata; never source-write or media ownership proof."""
from __future__ import annotations

from collections.abc import Mapping

from .library import Library
from .stock_ipod_catalog import catalog_metadata


def freeze_display_rows(
    library: Library, rows: Mapping[str, Mapping] | None, *,
    titles: dict | None = None, artists: dict | None = None,
) -> dict[str, dict] | None:
    """Validate only supplied song projections and discard nonmusical graphs."""
    if rows is None:
        return None
    output = {}
    for local, display in rows.items():
        track = library.tracks_by_id.get(local)
        if track is None:
            continue
        row = dict(catalog_metadata(track, library, artist_overrides=artists,
            title_overrides=titles, display_rows={local: display}))
        if 'bpm' not in row:
            row['bpm_estimated'] = True
        output[local] = row
    return output


def frozen_pair(rows: Mapping[str, Mapping] | None, local: str) -> tuple[str, str] | None:
    if rows is None or local not in rows:
        return None
    return str(rows[local]['title']), str(rows[local]['artist'])
