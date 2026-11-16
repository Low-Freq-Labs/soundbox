"""Build a reviewable CDJ generation off-device from current SoundBox selections."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from . import cdj_source, manifest
from .library import Track
from .cdj_transaction import CdjPlan, CdjSession, load_manifest, read_manifest, make_plan
from .cdj_usb import (CATALOG_RENDER_POLICY, DATABASE_FILES, DISPLAY_FIELDS, digest, default_display, patch_display,
                      read_library, read_profiles, run_renderer)
from .source_acquisition import SourceContract, acquire_source


def _stable_id(key):
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], 'big') & 0x7fffffff or 1


def _selected(library, additions, all_playlists, previous, *, allow_empty=False):
    available = {manifest.playlist_key(p): p for p in all_playlists}
    jobs = {}
    for value in previous.get('selection', []) if previous else []:
        key = value['key']
        if key not in available:
            # A removed managed playlist is omitted in both native formats.
            continue
        jobs[key] = available[key]
    for playlist in additions:
        jobs[manifest.playlist_key(playlist)] = playlist
    if not jobs and not allow_empty and not (previous and previous.get('library_managed', True)):
        raise ValueError('Drag a playlist onto this USB to establish its CDJ library')
    return jobs


def _playlist_dto(library, jobs, tracks, previous):
    index = {t.track_id: i for i, t in enumerate(tracks)}
    nodes: dict[str, dict] = {}
    used: dict[int, str] = {}
    old = {str(p.get('source_id', '')): p for p in previous.get('view', {}).get('playlists', [])} if previous else {}
    selection = []
    for key, playlist in jobs.items():
        parent = 0
        for at, name in enumerate(playlist.folder_path):
            folder_key = 'folder:'+'/'.join(playlist.folder_path[:at+1])
            node_id = _stable_id(folder_key)
            if node_id in used and used[node_id] != folder_key:
                raise ValueError('Playlist folder identity collision')
            used[node_id] = folder_key
            nodes.setdefault(folder_key, dict(id=node_id, name=name, parent_id=parent,
                              folder=True, track_indices=[], device_id=int(old.get(str(node_id), {}).get('id', 0))))
            parent = node_id
        node_id = _stable_id('playlist:'+key)
        if node_id in used and used[node_id] != key:
            raise ValueError('Playlist identity collision')
        used[node_id] = key
        nodes[key] = dict(id=node_id, name=playlist.name, parent_id=parent, folder=False,
                          track_indices=[index[t.track_id] for t in library.resolve(playlist)],
                          device_id=int(old.get(str(node_id), {}).get('id', 0)))
        selection.append(dict(key=key, pid=playlist.pid, name=playlist.name, folder=playlist.folder_path))
    next_id = int(previous.get('next_playlist_id', 1)) if previous else 1
    for node in nodes.values():
        if not node['device_id']:
            node['device_id'] = next_id
            next_id += 1
    return list(nodes.values()), selection


def plan_export(library, playlists, target, *, token, volume_uuid, all_playlists=(),
                titles=None, artists=None, art_cache=None, should_cancel=lambda:False,
                on_progress=lambda *_:None, recovery_root=None, additive=False, global_profile=None,
                standalone=()):
    # Source containment is checked before any local preparation or device write.
    from .export_snapshot import source_paths
    selected_sources = list(source_paths(library, standalone))
    session = CdjSession(target, selected_sources, token=token, volume_uuid=volume_uuid)
    from .cdj_transaction import recover
    recover(session,recovery_root,retain_new=True)
    previous,baseline_manifest = read_manifest(session)
    if not playlists and not standalone:
        from .cdj_transaction import reusable_proof
        pending=reusable_proof(session,recovery_root)
        if pending.get('db_id')==int(library.source_library_id):
            available={manifest.playlist_key(p):p for p in [*library.playlists,*all_playlists]}
            playlists=tuple(available[p['key']] for p in pending.get('selection',[]) if p['key'] in available)
            resumed = []
            for entry in pending.get('standalone', []):
                if entry.get('library_id') != library.source_library_id:
                    continue
                track = library.tracks_by_id.get(entry.get('track_id'))
                if track is None or track.source_uuid != entry.get('uuid'):
                    raise ValueError('Interrupted USB song changed or is unavailable; export its current selection again')
                resumed.append(track)
            standalone = tuple(resumed)
    native = read_library(target,token,should_cancel=should_cancel)
    if native is not None:
        from .cdj_catalog import current_baseline
        previous = current_baseline(native,previous,int(library.source_library_id))
    if previous and (previous.get('library_managed', True) or native is not None):
        from .cdj_incremental import plan_incremental
        return plan_incremental(library, playlists, session, previous, all_playlists=all_playlists,
            titles=titles, artists=artists, art_cache=art_cache, should_cancel=should_cancel,
            on_progress=on_progress, recovery_root=recovery_root, additive=additive,native=native,
            standalone=standalone,baseline_manifest=baseline_manifest)
    jobs = _selected(library, playlists, [*library.playlists, *all_playlists], previous, allow_empty=bool(standalone))
    by_id: dict[str, Track] = {}
    for track in standalone:
        by_id.setdefault(track.track_id, track)
    for playlist in jobs.values():
        for track in library.resolve(playlist):
            by_id.setdefault(track.track_id, track)
    tracks = list(by_id.values())
    captured = cdj_source.capture_metadata(library, tracks, titles=titles, artists=artists, playlists=list(jobs.values()))
    workspace = tempfile.TemporaryDirectory(prefix='soundbox-cdj-export-')
    try:
        root = Path(workspace.name).resolve()
        inputs = root/'inputs'
        inputs.mkdir()
        if native is not None:
            for path, raw in native.files:
                local = root/'baseline'/path
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(raw)
            if DATABASE_FILES[2] in dict(native.files):
                settings_db = root/'baseline'/DATABASE_FILES[2]
            else:
                run_renderer('defaults',root,root/'baseline'/DATABASE_FILES[0])
                settings_db = root/'settings.db'
            profile_paths = [(Path(path).name, str(root/'baseline'/path))
                             for path, _raw in native.files if path not in DATABASE_FILES]
            classic_bg = native.background_classic if native.background_classic is not None else native.background_plus or 0
            tag_id = native.tag_master_id if native.source_db_ids == (captured['db_id'],) else _stable_id('soundbox-cdj-tags:'+library.source_library_id)
        else:
            run_renderer('defaults', root)
            settings_db, classic_bg = root/'settings.db', 0
            profiles = dict(read_profiles(target,token))
            if global_profile:
                from . import my_settings
                for name,raw in my_settings.new_files(global_profile).items():
                    profiles.setdefault(name,raw)
            profiles.setdefault('DEVSETTING.DAT',default_display())
            profile_paths = []
            for name,raw in profiles.items():
                profile_path = root/name
                profile_path.write_bytes(raw)
                profile_paths.append((name,str(profile_path)))
            tag_id = _stable_id('soundbox-cdj-tags:'+library.source_library_id)
        estimated = sum(Path(t['source_path']).stat().st_size for t in captured['tracks'])
        if estimated*2 + 128*1024*1024 > shutil.disk_usage(root).free:
            raise ValueError('Insufficient local disk space to prepare the complete CDJ generation')
        # Freeze each captured audio file and companion before invoking the renderer.
        # Source acquisition keeps one active canonical snapshot; no decoder or tag
        # writer is allowed to reopen canonical paths after this boundary.
        request = deepcopy(captured)
        contracts = []
        assets = []
        for i, (track, dto) in enumerate(zip(tracks, request['tracks'])):
            if should_cancel():
                raise RuntimeError('CDJ preparation cancelled')
            if track.path.suffix.lower() not in ('.wav', '.mp3', '.aif', '.aiff', '.flac', '.m4a'):
                raise ValueError(f'Unsupported CDJ audio format: {track.name}')
            canonical = Path(dto['source_path']).resolve(strict=True)
            folder = inputs/str(dto['id'])
            folder.mkdir()
            with acquire_source(SourceContract(canonical), root, should_cancel=should_cancel) as source:
                destination = folder/canonical.name
                # Same-filesystem link binds the verified captured bytes, not source audio.
                destination.hardlink_to(source.path)
                dto['source_path'] = str(destination)
                contracts.append(dict(id=track.track_id, path=str(canonical), size=source.size,
                                      sha256=source.sha256,epoch=[source.observation.st_dev,source.observation.st_ino,source.observation.st_size,source.observation.st_mtime_ns]))
            asset = {}
            analysis = Path(dto['analysis_source'])
            for extension in ('DAT', 'EXT', '2EX'):
                path = analysis.with_suffix('.'+extension)
                before = path.stat()
                if not path.is_file() or before.st_size > 64*1024*1024:
                    raise ValueError(f'Unsupported native analysis: {track.name}')
                raw = path.read_bytes()
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('Native analysis changed during capture; retry after edits finish')
                (folder/('ANLZ0000.'+extension)).write_bytes(raw)
                asset[extension] = digest(raw)
            dto['analysis_source'] = str(folder/'ANLZ0000.DAT')
            if art_cache is not None:
                raw = art_cache.export_image_snapshot(track.track_id)
                if raw is not None:
                    cover = folder/'cover.jpg'
                    cover.write_bytes(raw)
                    dto['artwork'] = str(cover)
                    asset['artwork'] = digest(raw)
            assets.append(asset)
            on_progress(i+1, len(tracks), track.name)
        check = cdj_source.capture_metadata(library, tracks, titles=titles, artists=artists, playlists=list(jobs.values()))
        if check['source_revision'] != captured['source_revision']:
            raise ValueError('Source metadata/cues/grid changed during preparation; retry after edits finish')
        next_track_id = int(previous.get('next_track_id', 1)) if previous else 1
        for dto in request['tracks']:
            # Stable device IDs are explicit output from the previous generation.
            old = next((t for t in previous.get('view', {}).get('track_rows', [])
                        if int(t['source_id']) == dto['id']), None) if previous else None
            device_id = old['id'] if old else next_track_id
            if not old:
                next_track_id += 1
            dto['device'] = dict(id=device_id, master_db_id=request['db_id'], master_content_id=dto['id'],
                                     audio=old['audio'] if old else '', analysis_dir=str(Path(old['analysis']).parent) if old else '',
                                     preserve=False, exported=True)
        request['playlists'], selection = _playlist_dto(library, jobs, tracks, previous)
        request.update(native_profile='rekordbox-6.8.7.0303', settings_database=str(settings_db),
                       settings_files=profile_paths, settings_pdb=str(root/'baseline'/DATABASE_FILES[0]) if native and DATABASE_FILES[0] in dict(native.files) else None, tag_master_db_id=tag_id,
                       classic_background=classic_bg, source_contracts=contracts)
        signature_base = dict(source_revision=captured['source_revision'],
            playlists=[{k:v for k,v in p.items() if k != 'device_id'} for p in request['playlists']],
            media=contracts, assets=assets)
        def signature(settings_signature, profiles):
            return digest(json.dumps(dict(signature_base, settings=settings_signature,
                profiles={name:digest(raw) for name,raw in profiles}), sort_keys=True, separators=(',', ':')).encode())
        if previous and native and previous.get('export_signature') == signature(native.settings_signature,
                [(Path(path).name, raw) for path,raw in native.files if path not in DATABASE_FILES]):
            for path, raw in native.files:
                if path in DATABASE_FILES and digest(raw) != previous['files'][path]['sha256']:
                    raise ValueError('Native USB database changed outside SoundBox')
            return CdjPlan(session.target, session.mount_identity, session.volume_uuid, root,
                previous, (), baseline_manifest, False,
                len(tracks), len(jobs), workspace, recovery_root)
        path = root/'request.json'
        path.write_text(json.dumps(request))
        result = run_renderer('render', root, path, root/'candidate', should_cancel=should_cancel)
        by_device_id = {p['device_id']:p['id'] for p in request['playlists']}
        for playlist in result['view']['playlists']:
            playlist['source_id'] = by_device_id[int(playlist['id'])]
        # Bind candidate audio to captured bytes in make_plan's existing hash
        # pass, rather than stream the complete local audio set twice.
        actual = {int(t['source_id']): t for t in result['view']['track_rows']}
        source_contracts: dict[str, dict] = {}
        for dto,contract in zip(request['tracks'],contracts):
            audio = actual[dto['id']]['audio'].lstrip('/')
            prior_contract = source_contracts.setdefault(audio,contract)
            if (prior_contract['size'],prior_contract['sha256']) != (contract['size'],contract['sha256']):
                raise ValueError('Generated CDJ audio aliases different captured sources')
        shutil.rmtree(inputs)
        plan = make_plan(session, root/'candidate', selection=selection,
                         db_id=request['db_id'], view=result['view'], workspace=workspace,
                         should_cancel=should_cancel, recovery_root=recovery_root,expected_manifest=baseline_manifest,
                         source_contracts=source_contracts)
        candidate_native = read_library(root/'candidate', (root/'candidate').stat().st_dev,should_cancel=should_cancel)
        plan.manifest['export_signature'] = signature(candidate_native.settings_signature,
            [(Path(path).name,raw) for path,raw in candidate_native.files if path not in DATABASE_FILES])
        plan.manifest['selection_hint'] = selection_hint(library,list(jobs.values()),titles=titles,artists=artists)
        plan.manifest['playlist_hints'] = {key:selection_hint(library,[p],titles=titles,artists=artists) for key,p in jobs.items()}
        plan.manifest['tag_signature'] = digest(json.dumps(captured['my_tags'],sort_keys=True,separators=(',',':')).encode())
        plan.manifest['render_policy'] = CATALOG_RENDER_POLICY
        plan.manifest['next_track_id'] = next_track_id
        plan.manifest['next_playlist_id'] = max([int(previous.get('next_playlist_id', 1)) if previous else 1,
            *[p['device_id']+1 for p in request['playlists']]])
        from .cdj_incremental import input_signature, source_epoch
        if any(source_epoch(c['path'])!=c['epoch'] for c in contracts):
            raise ValueError('Source audio changed after capture')
        extras = {str(e['source_id']):e for e in captured['track_extras']}
        plan.manifest['track_inputs'] = {str(dto['id']):dict(
            signature=input_signature(dto,extras[str(dto['id'])],assets[i].get('artwork','')),
            source=contracts[i]) for i,dto in enumerate(captured['tracks'])}
        remember_standalone(plan.manifest, previous, library, standalone)
        return plan
    except Exception:
        workspace.cleanup()
        raise


def remember_standalone(current, previous, library, tracks):
    entries = {(r['library_id'],r['track_id']):dict(r) for r in (previous or {}).get('standalone',[])}
    for track in tracks:
        entries[(library.source_library_id, track.track_id)] = dict(
            library_id=library.source_library_id, track_id=track.track_id, uuid=track.source_uuid)
    if entries:
        current['standalone'] = list(entries.values())


def plan_settings(target, changes, *, token, volume_uuid, sources=(), should_cancel=lambda:False, recovery_root=None):
    session = CdjSession(target, sources, token=token, volume_uuid=volume_uuid)
    from .cdj_transaction import recover
    recover(session,recovery_root,retain_new=True)
    native = read_library(target, token,should_cancel=should_cancel)
    if native is None:
        raise ValueError('Export a playlist before editing native USB settings')
    previous,baseline_manifest = read_manifest(session)
    from .cdj_catalog import current_baseline
    previous = current_baseline(native,previous,previous['db_id'] if previous else 0)
    workspace = tempfile.TemporaryDirectory(prefix='soundbox-cdj-settings-')
    try:
        root = Path(workspace.name).resolve()
        for path, raw in native.files:
            local = root/path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(raw)
        display = {key: value for key, value in changes.items() if key in DISPLAY_FIELDS}
        general = {key: value for key, value in changes.items() if key not in DISPLAY_FIELDS and key != 'my_settings'}
        allowed = {'PIONEER/DEVSETTING.DAT'} if display else set()
        if 'my_settings' in changes:
            from . import my_settings
            values = my_settings.validate(changes['my_settings'])
            for name, fields in values.items():
                path = root/'PIONEER'/name
                raw = path.read_bytes()
                # The per-USB framing/unknown bytes belong to this USB.
                path.write_bytes(my_settings.patch(name,raw,fields,expected_sha256=digest(raw)))
                allowed.add('PIONEER/'+name)
        if display:
            path = root/'PIONEER/DEVSETTING.DAT'
            path.write_bytes(patch_display(path.read_bytes(), display))
        if general:
            path = root/'changes.json'
            path.write_text(json.dumps(general))
            run_renderer('settings', root, path,should_cancel=should_cancel)
            path.unlink()
        view = run_renderer('inspect', root,should_cancel=should_cancel)
        plan = make_plan(session, root, selection=previous['selection'] if previous else [],
                         db_id=previous['db_id'] if previous else 0, view=view, workspace=workspace,
                         should_cancel=should_cancel, settings_only=True, recovery_root=recovery_root,
                         allowed_profile_changes=allowed,catalog_baseline=previous,expected_manifest=baseline_manifest,
                         expected_profile_hashes={path:digest(raw) for path,raw in native.files if path in allowed})
        if previous:
            for key in ('selection_hint','next_track_id','next_playlist_id','track_inputs','playlist_hints','tag_signature','foreign_tracks','foreign_playlists','render_policy'):
                if key in previous:
                    plan.manifest[key] = previous[key]
            # Inspector IDs are device IDs; source bindings stay in the ownership record.
            bindings = {p['id']:p.get('source_id') for p in previous['view']['playlists']}
            for p in plan.manifest['view']['playlists']:
                if bindings.get(p['id']) is not None:
                    p['source_id'] = bindings[p['id']]
        return plan
    except Exception:
        workspace.cleanup()
        raise


def plan_initialize(target, *, token, volume_uuid, sources=(), profile=None, name=None, recovery_root=None, should_cancel=lambda:False):
    """Explicit freshly formatted USB initialization; no source-library scan."""
    from . import my_settings
    session=CdjSession(target,sources,token=token,volume_uuid=volume_uuid)
    if read_library(target,token,should_cancel=should_cancel) is not None:raise ValueError('The USB already has a native library')
    _previous,baseline_manifest=read_manifest(session)
    workspace=tempfile.TemporaryDirectory(prefix='soundbox-cdj-initialize-')
    try:
        root=Path(workspace.name).resolve();request=root/'initialize.json'
        request.write_text(json.dumps({'name':name or Path(target).name}))
        view=run_renderer('initialize',root,request,root/'candidate',should_cancel=should_cancel)
        folder=root/'candidate/PIONEER'
        for filename,raw in my_settings.new_files(profile or {}).items():(folder/filename).write_bytes(raw)
        (folder/'DEVSETTING.DAT').write_bytes(default_display())
        return make_plan(session,root/'candidate',selection=[],db_id=0,view=view,workspace=workspace,
                         recovery_root=recovery_root,settings_only=True,expected_manifest=baseline_manifest,
                         should_cancel=should_cancel)
    except Exception:
        workspace.cleanup();raise


def selection_hint(library, playlists, *, titles=None, artists=None):
    """Presentation-only drift hint. Fresh source capture remains export authority."""
    return _selection_hint(library,playlists,{},titles=titles,artists=artists)


def _selection_hint(library, playlists, track_rows, *, titles=None, artists=None):
    from .metadata import effective_title, effective_artist
    rows = []
    for playlist in playlists:
        tracks = []
        for t in library.resolve(playlist):
            if t.track_id not in track_rows:
                track_rows[t.track_id]=[t.track_id, t.source_uuid, str(t.path), t.bpm, t.key,
                    t.source_cue_revision,
                    effective_title(t.track_id,t.name,t.artist,titles,library.title_junk),
                    effective_artist(t.track_id,t.name,t.artist,artists,True,library.artist_registry,t.path.stem),
                    getattr(library,'info_overrides',{}).get(t.track_id,{})]
            tracks.append(track_rows[t.track_id])
        rows.append([manifest.playlist_key(playlist),playlist.name,playlist.folder_path,tracks])
    return digest(json.dumps(rows,sort_keys=True,separators=(',', ':')).encode())


def status_hint(library, target, *, token, volume_uuid, all_playlists=(), titles=None, artists=None):
    session = CdjSession(target, [], token=token, volume_uuid=volume_uuid)
    previous = load_manifest(session)
    if not previous or not previous.get('library_managed', True):
        return None
    jobs = _selected(library, [], [*library.playlists,*all_playlists], previous)
    if 'playlist_hints' in previous:
        # One poll observes one immutable captured library/preferences input.
        # Reuse pure track presentation only within this call, across overlaps.
        track_rows: dict[str, list] = {}
        current = {key:_selection_hint(library,[p],track_rows,titles=titles,artists=artists) for key,p in jobs.items()}
        return int(len(current)!=len(previous['selection']) or current!=previous['playlist_hints'])
    return int(previous.get('selection_hint') != selection_hint(library,list(jobs.values()),titles=titles,artists=artists))


def verify(target, *, token, volume_uuid, deep=False, should_cancel=lambda:False):
    from .cdj_transaction import _hash_file
    from .cdj_usb import PROFILE_FILES
    session = CdjSession(target, [], token=token, volume_uuid=volume_uuid)
    previous = load_manifest(session)
    native = read_library(target, token)
    result: dict = dict(playlists=0,entries=0,missing=[],managed_issues=[],advisories=[])
    if previous is None:
        return result
    if native is None:
        result['managed_issues'].append('Native library databases are missing')
        return result
    result['playlists'] = len([p for p in native.playlists if not p.folder])
    result['entries'] = sum(len(p.entries) for p in native.playlists)
    for path, contract in previous['files'].items():
        if should_cancel():
            raise RuntimeError('CDJ verification cancelled')
        local = session.guard(session.target/path)
        try:
            size = local.stat().st_size
        except FileNotFoundError:
            result['managed_issues'].append(path+': missing')
            continue
        profile = path in ('PIONEER/'+name for name in PROFILE_FILES)
        if size != contract['size'] and not profile:
            result['managed_issues'].append(path+': size changed')
        elif deep and contract['owned'] and _hash_file(local,should_cancel) != contract['sha256']:
            result['advisories' if profile else 'managed_issues'].append(
                path+(': profile changed; current settings will be preserved' if profile else ': bytes changed'))
    return result
