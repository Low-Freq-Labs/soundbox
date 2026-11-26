"""Selected native CDJ playlist deletion; source and other firmware stay separate."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import tempfile

from .cdj_catalog import current_baseline
from .cdj_incremental import referenced_files
from .cdj_transaction import CdjSession,make_plan,read_manifest,recover
from .cdj_usb import DATABASE_FILES,digest,read_library,run_renderer
from .operation_control import checkpoint,OperationCancelled


def _selected_rows(view,ids):
    rows=view['playlists'];counts=Counter(int(row['id']) for row in rows)
    by_id={int(row['id']):row for row in rows}
    if any(counts[value]!=1 or by_id[value]['folder'] for value in ids):
        raise ValueError('Select existing CDJ playlists, without folders or ambiguous identities')
    classic=view.get('classic_playlists',[])
    classic_counts=Counter(int(row['id']) for row in classic)
    for row in classic:
        identity=int(row['id'])
        if identity not in ids:continue
        current=by_id[identity]
        if classic_counts[identity]!=1 or any(row[key]!=current[key] for key in ('title','parent','folder')):
            raise ValueError('The selected playlist identity differs between native CDJ catalogs')
    return by_id


def plan_playlist_delete(target,playlist_ids,*,token,volume_uuid,target_role='generic_usb',
                         sources=(),expected_catalog_files=None,recovery_root=None,
                         should_cancel=lambda:False):
    """Prepare exact selected IDs; publication uses the ordinary CDJ commit."""
    checkpoint()
    if should_cancel():raise OperationCancelled('CDJ playlist deletion cancelled')
    ids=[]
    for value in playlist_ids:
        if isinstance(value,bool) or not isinstance(value,(str,int)):
            raise ValueError('Invalid CDJ playlist identity')
        identity=int(value)
        if not 0<identity<=0xffffffff:raise ValueError('Invalid CDJ playlist identity')
        ids.append(identity)
    if not ids or len(set(ids))!=len(ids):raise ValueError('Select distinct CDJ playlists to delete')
    selected=set(ids)
    session=CdjSession(target,sources,token=token,volume_uuid=volume_uuid,target_role=target_role)
    recover(session,recovery_root,retain_new=True)
    saved,baseline_manifest=read_manifest(session)
    native=read_library(target,token,should_cancel=should_cancel)
    if native is None or not set(DATABASE_FILES)<=dict(native.files).keys():
        raise ValueError('Playlist deletion requires both native CDJ library formats')
    if expected_catalog_files is not None:
        expected=dict(expected_catalog_files)
        for path in DATABASE_FILES:
            value=expected.get(path)
            if isinstance(value,bytes):value=digest(value)
            if value!=digest(dict(native.files)[path]):
                raise ValueError('The CDJ library changed after playlist selection; select it again')
    previous=current_baseline(native,saved,saved['db_id'] if saved else 0)
    before=previous['view'];rows=_selected_rows(before,selected)
    workspace=tempfile.TemporaryDirectory(prefix='soundbox-cdj-delete-')
    try:
        root=Path(workspace.name).resolve()
        for path,raw in native.files:
            local=root/'baseline'/path;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(raw)
        # Selected-only merge preserves each firmware's unrelated raw rows,
        # and retires only selected members unused by either remaining catalog.
        spec: dict[str,object]=dict(baseline=str(root/'baseline'),partial=None,output=str(root/'candidate'),
            playlists=[],track_ids=[],artwork={},my_tags=[],preserve_playlists=[],
            selected_only=True,selected_track_ids=[],selected_playlist_ids=ids)
        request=root/'delete.json';request.write_text(json.dumps(spec))
        view=run_renderer('merge',root,request,should_cancel=should_cancel)
        if any(int(row['id']) in selected for row in view['playlists']):
            raise ValueError('Selected CDJ playlist deletion was not rendered')
        for row in view['playlists']:
            old=rows.get(int(row['id']))
            if old is not None and 'source_id' in old:row['source_id']=old['source_id']
        for path,raw in native.files:
            if path not in DATABASE_FILES:
                local=root/'candidate'/path;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(raw)
        needed=referenced_files(view)|referenced_files({'track_rows':view.get('classic_track_rows',[])})
        removed=referenced_files({'track_rows':view.pop('removed_track_rows',[])})-needed
        removed={path for path in removed if previous['files'].get(path,{}).get('owned') is True}
        present={p.relative_to(root/'candidate').as_posix() for p in (root/'candidate').rglob('*') if p.is_file()}
        retained=set(previous['files'])-present-removed
        from .cdj_export import _stable_id
        deleted_sources={int(rows[identity]['source_id']) for identity in ids if 'source_id' in rows[identity]}
        selection=[row for row in previous['selection'] if _stable_id('playlist:'+row['key']) not in deleted_sources]
        plan=make_plan(session,root/'candidate',selection=selection,db_id=previous['db_id'],view=view,
            workspace=workspace,should_cancel=should_cancel,recovery_root=recovery_root,
            retained_files=retained,catalog_baseline=previous,expected_manifest=baseline_manifest,removed_files=removed)
        for key in ('next_track_id','next_playlist_id','tag_signature','render_policy'):
            if key in previous:plan.manifest[key]=previous[key]
        tracks={int(row['id']) for row in view['track_rows']}
        playlists={int(row['id']) for row in view['playlists']}
        source_ids={str(row['source_id']) for row in view['track_rows'] if int(row['master_db_id'])==previous['db_id']}
        plan.manifest['track_inputs']={key:value for key,value in previous.get('track_inputs',{}).items() if key in source_ids}
        keys={row['key'] for row in selection}
        plan.manifest['playlist_hints']={key:value for key,value in previous.get('playlist_hints',{}).items() if key in keys}
        plan.manifest['foreign_tracks']=[identity for identity in previous.get('foreign_tracks',[]) if identity in tracks]
        plan.manifest['foreign_playlists']=[identity for identity in previous.get('foreign_playlists',[]) if identity in playlists]
        return plan
    except BaseException:
        workspace.cleanup();raise
