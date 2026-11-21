"""Current normalized playlist names, including projected native intentions."""
from soundbox.playlist_names import normalize_playlist_name


def playlist_name_taken(window, name, *, native_id='', native_uuid='', custom_name='', pending=None,
                        unification_state=None):
    wanted = normalize_playlist_name(name)
    if wanted in {'collection', 'artists'}:
        return True
    presentation = getattr(window, '_native_presentation', lambda:window.lib)()
    ignored_custom = {custom_name} if custom_name else set()
    from soundbox import playlist_unification as merge
    state = (getattr(window, 'settings', {}).get(merge.KEY)
             if unification_state is None else unification_state)
    try:
        if state is not None:merge.validate(state)
    except (ValueError, TypeError, KeyError):
        state = None
    if not merge.matches(state, getattr(window, 'lib', None)):state = None
    # Completed migration keeps the old custom memberships for recovery. Their
    # bound native playlists now own the live names, including later renames or
    # deletions; the archived custom copy must not reserve its former name.
    library = getattr(window, 'lib', None)
    if state and state['customs_done'] and library.source_kind == 'rekordbox-db':
        ignored_custom.update(key.removeprefix('custom:') for key in state['nodes']
                              if key.startswith('custom:'))
    for key, binding in (state.get('nodes', {}) if state else {}).items():
        if (key.startswith('custom:') and (binding.get('id'), binding.get('uuid')) == (native_id, native_uuid)):
            ignored_custom.add(key.removeprefix('custom:'))
    if any(key not in ignored_custom and normalize_playlist_name(key) == wanted
           for key in getattr(window, 'custom', {})):
        return True
    from soundbox.rekordbox_playlist_store import command_requests,CREATE_ORDINARY_OPERATIONS,CREATE_SMART_OPERATIONS
    if pending is None:
        pending = getattr(getattr(window, 'playlist_controller', None), 'pending', ())
    folders = {str(row.get('ID') or '') for row in
               (library.source_playlist_catalog.get('nodes', ()) if library is not None else ())
               if row.get('Attribute') == 1}
    for job in pending:
        if library is None or (job.get('source'), job.get('library_id')) != (str(library.source), library.source_library_id):
            continue
        for command in command_requests(job['request']):
            if command['operation'] in CREATE_ORDINARY_OPERATIONS and command.get('folder'):folders.add(command['playlist_id'])
            if (command['operation'] in {*CREATE_ORDINARY_OPERATIONS, *CREATE_SMART_OPERATIONS, 'rename','rename_smart'}
                    and not command.get('folder')
                    and command['playlist_id'] not in folders and command['playlist_id'] != native_id
                    and normalize_playlist_name(command['name']) == wanted):
                return True
    if presentation is None:
        return False
    if any((not native_id or str(row.pid or '') != native_id) and normalize_playlist_name(row.name) == wanted
           for row in presentation.playlists):
        return True
    return any((not native_id or str(row.get('ID') or '') != native_id) and row.get('rb_local_deleted', 0) == 0
               and row.get('Attribute', 0) in {0, 4} and normalize_playlist_name(row.get('Name') or '') == wanted
               for row in presentation.source_playlist_catalog.get('nodes', ()))
