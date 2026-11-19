"""Reuse compatible native snapshots without acquiring media or edit authority."""
from operator import attrgetter, ne

_PREPARED_FIELDS=('track_id','native_id','source_uuid','source_native_path','location_uri',
                 'bpm','key','total_time','size','kind','bitrate','rating','year',
                 'date_added','added_ts','genre','imported','source_analysis_path','info')
_PREPARED_INPUTS=attrgetter(*(field for field in _PREPARED_FIELDS if field!='added_ts'))
_DISPLAY_INPUTS=attrgetter('name','artist')


def reuse_prepared_imports(previous,incoming,imported,*,changed_ids=None):
    """Reuse ready imports only when the complete media inventory agrees.

    The incoming snapshot remains authoritative for native musical metadata and
    smart memberships. Structural/media changes keep ordinary full publication.
    No source path is opened or resolved by this presentation check.
    """
    if (previous is None or incoming.source_kind!='rekordbox-db'
            or (str(previous.source),previous.source_kind,previous.source_library_id)!=
               (str(incoming.source),incoming.source_kind,incoming.source_library_id)):
        return False
    additions={key:track for key,track in previous.tracks_by_id.items()
               if key not in incoming.tracks_by_id and key in imported
               and track.imported and not track.source_uuid}
    if set(incoming.tracks_by_id)|set(additions)!=set(previous.tracks_by_id):return False
    import_times=[];changed=set()
    for key,old in previous.tracks_by_id.items():
        new=incoming.tracks_by_id.get(key,additions.get(key))
        # Gather attributes in C, retaining each original != comparison (even
        # non-reflexive scalar values) rather than tuple identity shortcuts.
        if any(map(ne,_PREPARED_INPUTS(old),_PREPARED_INPUTS(new))):return False
        if changed_ids is not None and _DISPLAY_INPUTS(old)!=_DISPLAY_INPUTS(new):changed.add(key)
        if old.added_ts!=new.added_ts:
            # Native adoption keeps the original SoundBox import time. Match
            # imports.inject's existing scalar rule without probing media.
            record=imported.get(key,{})
            if (not new.source_native_id or new.imported
                    or key in incoming.source_binding_issues or not record.get('added_ts')):
                return False
            try:timestamp=float(record['added_ts'])
            except (TypeError,ValueError,OverflowError):return False
            if old.added_ts!=timestamp:return False
            import_times.append((new,timestamp))
    for track,timestamp in import_times:track.added_ts=timestamp
    incoming.tracks_by_id.update(additions)
    if changed_ids is not None:changed_ids.update(changed)
    return True
