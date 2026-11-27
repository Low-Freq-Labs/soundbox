"""Detached presentation boundaries for compatible native acknowledgements."""
from __future__ import annotations

from copy import deepcopy
from operator import attrgetter

from soundbox import metadata, metadata_context, metadata_provenance

_DISPLAY_FIELDS=('track_id','name','artist','location_uri','bpm','key','total_time',
                 'date_added','added_ts','genre','kind','size','bitrate','rating','year','imported')
_display_scalars=attrgetter(*_DISPLAY_FIELDS)


def _freeze(value):
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def context(window):
    """Capture visibility/organization without opening media or granting edits.

    Musical identity compatibility belongs to native_publication. These detached
    values also detect in-place settings/catalog changes since the last paint.
    Native playlist occurrences retain their complete ordered membership.
    """
    lib = window.lib
    if lib is None:
        return None
    return (
        str(lib.source), lib.source_kind, lib.source_library_id,
        frozenset(lib.hidden_ids), lib.hide_untracked, lib.hide_nokey,
        lib.hide_short, lib.hide_short_secs, lib.metadata_cleanup_enabled,
        tuple((p.pid, p.name, tuple(p.folder_path), tuple(p.keys), tuple(p.added_keys))
              for p in lib.playlists),
        tuple((t.tag_id, t.name, t.category, t.source_uuid, t.category_id, t.category_uuid)
              for t in lib.source_tags),
        tuple((c.category_id,c.name,c.source_uuid) for c in lib.source_tag_categories),
        tuple((key, tuple(track.source_tag_ids or ())) for key, track in lib.tracks_by_id.items()),
        _freeze((lib.source_playlist_catalog or {}).get('nodes', [])),
        _freeze(window.custom), _freeze(window.removals), _freeze(window.playlist_additions),
        tuple((key, _freeze(window.settings.get(key))) for key in (
            'custom_playlist_tree', 'custom_playlist_expanded', 'playlist_icons',
            'playlist_unification', 'rekordbox_playlist_edits', 'smart_playlist_recency')),
    )


def artists_context(window):
    """Exact visible pool for Artists, independent of native organization.

    Complete media compatibility and registry/row dirtiness are checked by the
    publication owner. Playlist membership/order and tag definitions affect the
    native tree; changed rendered tag labels have their own detached row inputs.
    """
    lib = window.lib
    if lib is None:
        return None
    return (str(lib.source), lib.source_kind, lib.source_library_id,
            tuple(track.track_id for track in window._visible_collection_tracks()),
            _freeze(window.settings.get('track_info', {})), lib.metadata_cleanup_enabled)


def artist_tag_inputs(library, tracks=None):
    """Detach the ordered labels used by complete rendered MyTags cells."""
    if library is None:
        return {}
    labels = tuple((tag.tag_id, tag.name) for tag in library.source_tags)
    tracks = library.tracks_by_id.values() if tracks is None else tracks
    return {track.track_id: (bool(track.source_tag_ids), tuple(
                label for label in labels if label[0] in (track.source_tag_ids or ())))
            for track in tracks}


def clean_row(row):
    return {key: value for key, value in row.items() if key != '_hay'}


def registry_inputs(library, origins, overrides, previous=None):
    """Reuse only unchanged detached derivation inputs, never source authority.

    Compare every scalar identity and the complete origin value on each call.
    The detached origin makes in-place provenance changes observable, while an
    unchanged row avoids repeated validation, URI parsing and artist derivation.
    """
    scope=dict(source=str(library.source),kind=library.source_kind,
               library_id=library.source_library_id)
    by_local={}
    for origin in origins.values():
        if not isinstance(origin,dict) or origin.get('scope')!=scope:
            continue
        local=origin.get('local_id')
        by_local[local]=None if local in by_local else origin
    baseline=(previous['rows'] if previous is not None and previous['scope']==scope
              and previous.get('cleanup_enabled', True)==library.metadata_cleanup_enabled else {})
    rows={};bases=[];trusted=[];changed=set();manual_rows={}
    manual_before=previous.get('manual_rows',{}) if previous is not None and previous['scope']==scope else {}
    source=metadata_context.scope(library)
    for key,track in library.tracks_by_id.items():
        origin=None if key in library.source_binding_issues else by_local.get(key)
        scalar=(_display_scalars(track),track.native_id,track.source_uuid,
                track.source_native_path,overrides.get(key))
        before=baseline.get(key)
        if before is not None and before[0]==scalar and before[1]==origin and before[4]==track.info:
            row=before
            manual=manual_before.get(key)
        else:
            changed.add(key)
            name,artist,fulfilled=(metadata_provenance.display_registry_inputs(track,scope,origin)
                if library.metadata_cleanup_enabled else (track.name,track.artist,None))
            votes=tuple(([artist] if artist else [])
                        + ([fulfilled] if fulfilled is not None and key not in overrides else []))
            base=(metadata.derive_artist(name,artist)[0] if library.metadata_cleanup_enabled else artist)
            if library.metadata_cleanup_enabled:
                base=(overrides.get(key) or '').strip() or fulfilled or base
            row=(scalar,deepcopy(origin),base,votes,deepcopy(track.info))
            manual=metadata_context.prepare_row(track,source,origin)
        if manual is not None:manual_rows[key]=manual
        rows[key]=row;bases.append(row[2]);trusted.extend(row[3])
    trusted.extend(value for value in overrides.values() if value.strip())
    library.metadata_context=metadata_context.ManualContext(source,manual_rows)
    return bases,trusted,dict(scope=scope,rows=rows,manual_rows=manual_rows,changed_ids=frozenset(changed),
                             cleanup_enabled=library.metadata_cleanup_enabled)
