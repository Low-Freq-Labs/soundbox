"""Retain selected smart rows during newer tag gestures, without editing Library."""
from .tag_assignment_intents import restore_request


def _requests(controller):
    if not controller:return []
    requests=[]
    if not getattr(controller,'queue_error',lambda:'')():
        requests=[restore_request(job['request']) for job in getattr(controller,'pending',())
                  if controller._tag_source_current(job)]
    lane=getattr(controller,'tag_state_lane',None)
    if lane is not None:requests.extend(lane.requests())
    return requests


def _binding(track):
    return track.native_id,track.source_uuid,str(track.path),str(track.native_path)


def capture(host):
    key=getattr(host,'_view_key','') or ''
    if not key.startswith('rb:') or host.stack.currentIndex()!=0:return None
    library=host.lib;playlist=host._native_view_playlist(key[3:])
    if not library or playlist is None:return None
    if not any(node.get('ID')==playlist.pid and node.get('Attribute')==4
               for node in library.source_playlist_catalog.get('nodes',())):return None
    targets={(target.track_id,target.source_uuid) for request in _requests(host.tag_controller)
             for target in request.tracks}
    selected=host._selected_ids()
    tracks={tid:_binding(track) for tid in selected if (track:=library.tracks_by_id.get(tid)) is not None
            and (track.native_id,track.source_uuid) in targets}
    return dict(view=key,source=(str(library.source),library.source_library_id),tracks=tracks) if tracks else None


def rows(host,rows):
    captured=getattr(host,'_tag_selection_view',None)
    if (not captured or host.stack.currentIndex()!=0 or host._view_key!=captured['view']
            or (str(host.lib.source),host.lib.source_library_id)!=captured['source']
            or not _requests(host.tag_controller)):return rows
    from .trees import PATH_ROLE
    current=host.tree.currentItem()
    path=current.data(0,PATH_ROLE) if current is not None else None
    if path and 'rb:'+path!=captured['view']:return rows
    present={row.get('id') for row in rows}
    retained=[dict(host._row_for(track)) for tid,binding in captured['tracks'].items()
              if tid not in present and tid not in getattr(host,'hidden',())
              and (track:=host.lib.tracks_by_id.get(tid)) is not None
              and _binding(track)==binding]
    return [*rows,*retained] if retained else rows


def release(host):
    captured=getattr(host,'_tag_selection_view',None)
    if captured is None or _requests(host.tag_controller):return
    host._tag_selection_view=None
    library=host.lib
    if (host.stack.currentIndex()==0 and host._view_key==captured['view'] and library is not None
            and (str(library.source),library.source_library_id)==captured['source']):
        seat=host._browser_seat()
        host._render_current_view()
        host._restore_browser_seat(seat)
