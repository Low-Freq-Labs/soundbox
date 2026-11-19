"""Selected source transitions that must settle before an iPod export capture."""
from pathlib import Path
import os

from soundbox import media_conversion_jobs,shared_imports
from soundbox.rekordbox_playlist_store import command_requests


class SelectedExportWait(RuntimeError):
    """A selected durable operation can progress; unrelated queues do not gate."""


class SelectedExportError(RuntimeError):
    """A selected operation needs explicit attention before capture."""


def _path(value):return os.path.normpath(str(value))


def capture_fences(host,playlists):
    library=host.lib;tracks={t.track_id:t for p in playlists for t in library.resolve(p)}
    nodes={str(row.get('ID','')):row for row in library.source_playlist_catalog.get('nodes',())}
    selected_nodes={str(p.pid) for p in playlists if p.pid};pending=list(selected_nodes)
    while pending:
        row=nodes.get(pending.pop());parent=str(row.get('ParentID','')) if row else ''
        if parent and parent!='root' and parent not in selected_nodes:selected_nodes.add(parent);pending.append(parent)
    prep=getattr(host,'prep_controller',None);lane=getattr(prep,'state_lane',None)
    admissions=[]
    for row in getattr(lane,'queue',()):
        if row.get('tid') in tracks and not row.get('cue_only',False):
            admissions.append(dict(tid=row['tid'],error=''))
    for row,error in getattr(lane,'failed',()):
        if row.get('tid') in tracks and not row.get('cue_only',False):
            admissions.append(dict(tid=row['tid'],error=str(error)))
    scope=(str(library.source),library.source_library_id)
    def jobs(key):
        return [row for row in host.settings.get(key,()) if isinstance(row,dict)
                and (row.get('source'),row.get('library_id'))==scope]
    media=getattr(host,'media_controller',None)
    return dict(scope=scope,ids=list(tracks),paths=list(dict.fromkeys(_path(value)
        for t in tracks.values() for value in (t.path,t.native_path))),
        media_root=str(host.tag_controller.data_root),
        native=[(t.native_id,t.source_uuid) for t in tracks.values() if t.native_id and t.source_uuid],
        playlist_ids=sorted(selected_nodes),playlist_names=[p.name for p in playlists],
        playlist_uuids={identity:str(nodes[identity].get('UUID','')) for identity in selected_nodes if identity in nodes},
        media=jobs(media_conversion_jobs.KEY),imports=jobs(shared_imports.KEY),
        structural=jobs('rekordbox_playlist_edits'),prep=admissions,
        active_media=list(getattr(media,'active',())))


def selected_media_jobs(data,canonical_paths=()):
    """Select physical transition candidates; CDJ policy never decides eligibility."""
    ids=set(data['ids']);paths=set(data['paths'])|set(canonical_paths)
    native={tuple(row) for row in data['native']}
    def selected(row):
        if _path(row.get('path','')) in paths:return True
        refs=row.get('native',())
        if any((ref.get('id'),ref.get('uuid')) in native for ref in refs):return True
        return bool(set(row.get('ids',()))&ids and (not refs or not native))
    return [row for row in data['media'] if selected(row)]


def require_fences(data,*,canonical=False,canonical_paths=()):
    """Pure comparisons; optional selected-only path resolution runs on worker."""
    ids=set(data['ids']);paths=set(data['paths'])
    paths.update(canonical_paths)
    if canonical:paths.update(str(Path(value).resolve(strict=False)) for value in paths.copy())
    def selected_destination(identity,uuid):
        identity=str(identity)
        expected=data.get('playlist_uuids',{}).get(identity,'')
        return identity in data['playlist_ids'] and (not uuid or not expected or uuid==expected)
    def waiting(row,phase):
        if row.get('error'):raise SelectedExportError(f'Selected {phase} needs attention: '+str(row['error']))
        raise SelectedExportWait(f'Waiting for selected {phase}')
    # A dormant CDJ compatibility intention is not an iPod format decision.
    # The checkpoint's worker proves absence of selected physical transition
    # evidence; active replacement/locator work still holds admission here.
    active=set(data['active_media'])
    if active&ids or any(active&set(row['ids']) for row in selected_media_jobs(data,paths)):
        raise SelectedExportWait('Waiting for selected source replacement or locator save')
    for row in data['imports']:
        dest=row.get('destination',{})
        affected=(dest.get('kind')=='native' and selected_destination(dest.get('id',''),dest.get('uuid',''))
            or dest.get('kind')=='custom' and dest.get('name') in data['playlist_names']
            or any(track.get('id') in ids or _path(track.get('path','')) in paths for track in row.get('tracks',())))
        if affected:waiting(row,'song registration or membership save')
    for row in data['structural']:
        request=row.get('request')
        if not isinstance(request,dict):raise SelectedExportError('Pending source playlist identity needs attention')
        for command in command_requests(request):
            if selected_destination(command.get('playlist_id',''),command.get('playlist_uuid','')):
                waiting(row,'playlist changes')
    for row in data['prep']:waiting(row,'local grid save')


def refresh_physical_fences(host,data):
    """Later metadata/membership edits do not cancel an accepted generation."""
    result={**data,'structural':[],'prep':[],'playlist_ids':[],'playlist_names':[]}
    scope=tuple(data['scope'])
    for output,key in (('media',media_conversion_jobs.KEY),('imports',shared_imports.KEY)):
        result[output]=[row for row in host.settings.get(key,()) if isinstance(row,dict)
            and (row.get('source'),row.get('library_id'))==scope]
    result['active_media']=list(getattr(getattr(host,'media_controller',None),'active',()))
    return result
