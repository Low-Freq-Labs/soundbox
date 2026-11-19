"""Durable effective iPod export generations, independent of native backlog end."""
from copy import deepcopy
from dataclasses import dataclass,replace
import json
from pathlib import Path
from types import MappingProxyType

from PySide6.QtCore import QTimer

from soundbox import alias_intents,analysis,manifest,prep_edits,source_truth
from soundbox.export_snapshot import capture_export
from soundbox.library import Playlist
from soundbox.operation_control import checkpoint
from soundbox.stock_ipod_catalog import CATALOG_METADATA_FIELDS
from .devices import device_facts
from .ipod_export_fences import (capture_fences,require_fences,refresh_physical_fences,
                                 selected_media_jobs,SelectedExportError,SelectedExportWait)
from .state_commit import StateCommitter


class ChooseIpodPlaylists(SelectedExportError):
    choose_playlists=True


def effective_rows(host,ids):
    """Project requested cached rows only; callers separately establish durability."""
    return {tid:{'id':tid,**{field:row.get(field,'') for field in CATALOG_METADATA_FIELDS},
                 'bpm_estimated':row.get('bpm_estimated',False)}
            for tid in dict.fromkeys(ids) if tid in host.lib.tracks_by_id
            for row in (host._row_for(host.lib.tracks_by_id[tid]),)}


def _source(host):return (str(host.lib.source),host.lib.source_kind,host.lib.source_library_id) if host.lib else None


def _mount(host,dev):
    return (host._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid)


def _repository(host):
    repository=host._state_repository
    return repository,repository.generation,repository.area.generation


def _native_nodes(host):return {str(row.get('ID','')):row for row in host.lib.source_playlist_catalog.get('nodes',())}


def _available(host):
    return {manifest.playlist_key(row):row for row in host._export_playlists()}


def _read_rockbox_manifest(dev):
    from soundbox.engine import capture_device_drift_manifest
    result=capture_device_drift_manifest(dev)
    if result is not None:
        # The engine's compatibility parser intentionally recovers old input.
        # A new frozen precondition must identify the real schema, not a default.
        raw=json.loads(result[1])
        if (not isinstance(raw,dict) or type(raw.get('schema')) is not int or raw['schema']!=manifest.SCHEMA
                or not isinstance(raw.get('playlists'),dict) or not isinstance(raw.get('files'),dict)):
            raise SelectedExportError('This iPod catalog is not a supported Rockbox-only catalog')
    return result


def _resolve(host,request):
    available=_available(host);nodes=_native_nodes(host)
    pins=request.get('selection')
    if pins is None:
        cached=getattr(host,'_stock_view_cache',{}).get(request['dev'])
        if request['kind']=='rockbox':
            observed=request.get('managed_manifest')
            baseline=observed[0] if observed is not None else {}
            keys=baseline.get('playlist_order') or baseline.get('playlists') or {}
            # Schema 2's existing explicit Sync contract binds managed keys to
            # the current source. It is not historical native/source authority.
            pins=[(str(key),'','') for key in keys if str(key) in available]
        elif request['kind']=='dual' and cached and cached[1] is not None:
            baseline=cached[1].manifest
            keys=list(baseline.get('playlist_order') or baseline.get('playlists') or {})
            pins=[(str(key),'','') for key in keys if str(key) in available]
        else:
            witness=getattr(host,'_stock_catalog_witness_cache',{}).get(request['dev'])
            if (witness is None or cached is None or witness.device_key!=cached[0].identity_key
                    or witness.source!=_source(host)):
                raise ChooseIpodPlaylists('Choose current playlists to export; this older iPod baseline has no exact source association')
            pins=[(row.key,row.native_id,row.uuid) for row in witness.bindings]
        if not pins:raise ChooseIpodPlaylists('Choose current playlists to export to this iPod')
    selected=[]
    for key,native,uuid in pins:
        retained=request.get('device_selection',{}).get(key)
        if retained is not None:
            if key in available:raise SelectedExportError('A retained device playlist now has a different current source; choose it again')
            selected.append(Playlist(**retained));continue
        row=available.get(key)
        if row is None:raise SelectedExportError('Selected playlist changed; choose its current row again')
        if native:
            node=nodes.get(native)
            if (str(row.pid)!=native or node is None or node.get('UUID')!=uuid or node.get('rb_local_deleted',0)):
                raise SelectedExportError('Selected native playlist identity changed; choose it again')
        selected.append(row)
    if request.get('options',{}).get('action') in {'delete','order'} and any(
            tid not in host.lib.tracks_by_id for playlist in selected for tid in playlist.effective_keys):
        raise SelectedExportError('A retained iPod playlist has unavailable source songs; its device contents were preserved')
    return selected


def _projection_selection(host,request,selected):
    output={manifest.playlist_key(row):row for row in selected};baseline=None
    if request['kind']=='dual':
        cached=getattr(host,'_stock_view_cache',{}).get(request['dev'])
        if cached is None:raise SelectedExportWait('Waiting for managed iPod playlist details')
        if cached and cached[1] is not None:
            baseline=deepcopy(cached[1].manifest);available=_available(host)
            for key in (baseline.get('playlists',{}) if request.get('options',{}).get('action') not in {'delete','order'} else ()):
                if str(key) in available:output[str(key)]=available[str(key)]
    return list(output.values()),baseline


@dataclass(frozen=True)
class IpodExportCapture:
    snapshot:object
    display:bytes
    selection:bytes
    track_total:int
    source:tuple
    repository:tuple
    target:tuple
    device:str
    track_bindings:tuple
    playlist_bindings:tuple
    fences:bytes
    artwork:object=None
    manifest_payload:bytes|None=None
    requested_none:bool=False
    canonical_paths:tuple=()
    manifest_checked:bool=False
    artwork_by_id:object=None
    manifest_profile:str=''
    manifest_read_payload:bytes|None=None
    media_transitions:object=None

    @property
    def selected_playlists(self):return tuple(Playlist(**row) for row in json.loads(self.selection))

    @property
    def playlists(self):return self.selected_playlists

    @property
    def expected_manifest(self):return json.loads(self.manifest_payload) if self.manifest_payload is not None else None

    @property
    def expected_manifest_absent(self):
        payload=self.manifest_read_payload if self.manifest_profile=='rockbox' else self.manifest_payload
        return self.manifest_checked and payload is None

    @property
    def expected_manifest_payload(self):return self.manifest_read_payload if self.manifest_profile=='rockbox' else None

    def materialize(self):return self.snapshot.materialize()

    def verify_worker(self):
        if self.manifest_profile=='rockbox':
            from soundbox.device_manifest_capture import read_device_manifest
            if read_device_manifest(self.device)!=self.manifest_read_payload:
                raise SelectedExportError('The managed Rockbox catalog changed before export')
        data=json.loads(self.fences)
        paths=[]
        for path in data['paths']:
            checkpoint();paths.append(str(Path(path).resolve(strict=False)))
        require_fences(data,canonical_paths=paths)
        from soundbox.media_transition import capture_media_transitions,MediaTransitionPending
        jobs=selected_media_jobs(data,paths)
        if self.source[1]!='rekordbox-db':
            return replace(self,canonical_paths=tuple(paths))
        try:
            if self.media_transitions is None:
                proof=capture_media_transitions(data['media_root'],*data['scope'],jobs)
            else:
                self.media_transitions.require_current();proof=self.media_transitions
        except MediaTransitionPending as exc:
            errors=[f"{Path(row['path']).name}: {row['error']}" for row in jobs if row['error']]
            if errors:raise SelectedExportError('Source replacement or locator save needs attention — '+'; '.join(errors)) from exc
            raise SelectedExportWait(str(exc)) from exc
        return replace(self,canonical_paths=tuple(paths),media_transitions=proof)

    def validate_artwork(self,art_cache,track_id):
        token=self.artwork_by_id.get(track_id) if self.artwork_by_id is not None else None
        if token is None or not art_cache.selection_current(token):
            raise SelectedExportError('Selected artwork changed during export; Sync its newer cover afterward')
        return True

    def require_current(self,host,dev):
        if dev!=self.device or _source(host)!=self.source or _repository(host)!=self.repository or _mount(host,dev)!=self.target:
            raise SelectedExportError('Library or connected iPod changed; choose the export again')
        for tid,native,uuid,path,native_path in self.track_bindings:
            track=host.lib.tracks_by_id.get(tid)
            if track is None or (track.native_id,track.source_uuid,str(track.path),str(track.native_path))!=(native,uuid,path,native_path):
                raise SelectedExportError('Selected song identity changed before export')
        nodes=_native_nodes(host)
        for identity,uuid in self.playlist_bindings:
            if identity not in nodes or nodes[identity].get('UUID')!=uuid or nodes[identity].get('rb_local_deleted',0):
                raise SelectedExportError('Selected native playlist changed before export')
        if self.manifest_checked and self.manifest_profile=='dual':
            cached=getattr(host,'_stock_view_cache',{}).get(dev)
            payload=(json.dumps(cached[1].manifest,sort_keys=True,allow_nan=False).encode()
                     if cached and cached[1] is not None else None)
            if cached is None or payload!=self.manifest_payload:
                raise SelectedExportError('The managed iPod catalog changed before export')
        if self.artwork is not None and not host.art_cache.selection_current(self.artwork):
            raise SelectedExportError('Selected artwork changed; review it before exporting')
        data=json.loads(self.fences);current=refresh_physical_fences(host,data)
        require_fences(current,canonical_paths=self.canonical_paths)
        if selected_media_jobs(current,self.canonical_paths)!=selected_media_jobs(data,self.canonical_paths):
            raise SelectedExportError('Selected source transition changed; choose the export again')
        return True


def _canonical_projection(host,playlists,ids):
    expected={};missing=[];repository=host._state_repository;settings=host.settings
    def value(path,data,*,present=True):
        if present:expected[path]=deepcopy(data)
        else:missing.append(path)
    def item(path,container,key):value((*path,key),container.get(key),present=key in container)
    def default(path,data):
        if repository.contains_selected_path(path) or data:expected[path]=deepcopy(data)
        else:missing.append(path)
    default(('artist_aliases',),getattr(host,'aliases',{}))
    for key in ('title_rules_off','title_words'):item(('settings',),settings,key)
    for tid in ids:
        for namespace,attribute in (('title_overrides','titles'),('artist_overrides','overrides'),('imported','imported')):
            item((namespace,),getattr(host,attribute,{}),tid)
        hidden=getattr(host,'hidden',set())
        value(('hidden_tracks',tid),getattr(host,'hidden_dates',{}).get(tid,''),present=tid in hidden)
        item(('settings','track_info'),settings.get('track_info',{}),tid)
        track=host.lib.tracks_by_id.get(tid)
        if track is None:continue
        key=analysis.result_key(track.path);record=settings.get('analysis_results',{}).get(key)
        path=('settings','analysis_results',key)
        if record is None:missing.append(path)
        else:
            for field in ('path','size','sha256','epoch','bpm','key','duration'):
                item(path,record,field)
            draft=record.get('grid_draft')
            if not draft:missing.append((*path,'grid_draft'))
            else:
                for field in ('revision','base_sha256','origin','bpm'):item((*path,'grid_draft'),draft,field)
        scope=source_truth.scope(host.lib) if host.lib.source_kind=='rekordbox-db' else None
        if scope:
            identity=alias_intents.key(scope,dict(local_id=tid,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path or str(track.path)))
            for key in (source_truth.TARGETS,alias_intents.TARGETS,alias_intents.ORIGINS):
                item(('settings',key),settings.get(key,{}),identity)
            next_plan=settings.get(alias_intents.NEXT)
            if next_plan and next_plan.get('state')=='building':
                item(('settings',alias_intents.NEXT,'edits'),next_plan.get('edits',{}),identity)
                raw=settings.get(alias_intents.TARGETS,{}).get('inputs:'+next_plan['id'],{}).get('raw',{})
                item(('settings',alias_intents.TARGETS,'inputs:'+next_plan['id'],'raw','visible'),raw.get('visible',{}),identity)
    for key in (source_truth.MIGRATION,alias_intents.NEXT):
        record=settings.get(key)
        if record is None:missing.append(('settings',key))
        else:
            for field in ('id','scope','cursor','format','state'):item(('settings',key),record,field)
    selected=set(ids)
    for key,identity in (('pending_metadata_edits','local_id'),(prep_edits.KEY,'track_id')):
        for index,row in enumerate(settings.get(key,())):
            if not isinstance(row,dict) or row.get(identity) not in selected:continue
            path=('settings',key,index)
            for field in ('id','source','library_id',identity,'path','native_id','track_uuid','source_uuid','native_path','field','value'):
                item(path,row,field)
            if key==prep_edits.KEY:
                grid=row.get('grid',[])
                if grid:value((*path,'grid',0),grid[0])
                else:missing.append((*path,'grid',0))
                item(path,row,'version');item(path,row,'operations')
    for playlist in playlists:
        if playlist.pid:
            for namespace,attribute in (('playlist_removals','removals'),('playlist_additions','playlist_additions')):
                container=getattr(host,attribute,{})
                default((namespace,playlist.pid),container.get(playlist.pid,{}) if namespace=='playlist_removals' else container.get(playlist.pid,[]))
        if not playlist.pid and playlist.name in getattr(host,'custom',{}):item(('custom',),host.custom,playlist.name)
    # Distinct selected local identities may intentionally reference one file.
    # Their shared analysis absence is one read assertion, not duplicate paths.
    return expected,tuple(dict.fromkeys(missing))


def _build(host,request):
    selected=_resolve(host,request);projected,baseline=_projection_selection(host,request,selected)
    data=capture_fences(host,projected);require_fences(data)
    ids=list(dict.fromkeys(key for p in projected for key in p.effective_keys))
    rows=effective_rows(host,ids)
    frozen=capture_export(host.lib,selected,extra=host._sync_playlists(),titles=host.titles,
        artists=host.overrides,complete=True,playlist_identities=True,display_rows=rows)
    bindings=tuple((tid,t.native_id,t.source_uuid,str(t.path),str(t.native_path))
        for tid in ids if (t:=host.lib.tracks_by_id.get(tid)) is not None)
    nodes=_native_nodes(host)
    native=tuple((str(p.pid),nodes[str(p.pid)]['UUID']) for p in projected
        if p.pid and str(p.pid) in nodes and host.lib.source_kind=='rekordbox-db')
    selected_payload=json.dumps([dict(name=p.name,folder_path=p.folder_path,keys=list(p.effective_keys),
        pid=p.pid,export_key=p.export_key) for p in selected],allow_nan=False).encode()
    capture=IpodExportCapture(frozen,json.dumps(rows,allow_nan=False).encode(),selected_payload,
        sum(len(host.lib.resolve(p)) for p in selected),request['source'],request['repository'],request['target'],
        request['dev'],bindings,native,json.dumps(data,allow_nan=False).encode(),
        host.art_cache.selection_token(ids),
        json.dumps(baseline,sort_keys=True,allow_nan=False).encode() if baseline is not None else None,
        request['selection'] is None)
    capture=replace(capture,manifest_checked=request['kind'] in {'dual','rockbox'},manifest_profile=request['kind'],
        manifest_read_payload=(request['managed_manifest'][1]
            if request['kind']=='rockbox' and request.get('managed_manifest') is not None else None),
        artwork_by_id=MappingProxyType(host.art_cache.split_selection(capture.artwork)))
    expected,missing=_canonical_projection(host,projected,ids)
    return capture,expected,missing


def request_ipod_export(host,kind,dev,playlists,*,options=None,ready,failed):
    """Prepare one durable selected generation; bulk migration may stay paused."""
    if kind not in {'stock','dual','rockbox'}:raise ValueError('Unknown iPod export kind')
    batch=getattr(host,'_export_batch',None);target=batch.get('active') if batch else None
    if target is None or target.get('path')!=dev:batch=None;target=None
    nodes=_native_nodes(host)
    pins=None if playlists is None else tuple((manifest.playlist_key(p),str(p.pid),nodes[str(p.pid)].get('UUID',''))
        if p.pid and host.lib.source_kind=='rekordbox-db' and str(p.pid) in nodes
        else (manifest.playlist_key(p),'','') for p in playlists)
    device_selection={}
    if kind=='dual' and (options or {}).get('action') in {'delete','order'}:
        expected=(options or {}).get('expected_manifest')
        cached=getattr(host,'_stock_view_cache',{}).get(dev)
        if (playlists is None or expected is None or cached is None or cached[1] is None
                or cached[1].manifest!=expected):
            raise SelectedExportError('The managed iPod playlist selection changed; refresh it before retrying')
        available=_available(host)
        for playlist in playlists:
            if any(tid not in host.lib.tracks_by_id for tid in playlist.effective_keys):
                raise SelectedExportError('A retained iPod playlist has unavailable source songs; its device contents were preserved')
            key=manifest.playlist_key(playlist)
            if key in available:continue
            prior=expected.get('playlists',{}).get(key)
            if (not isinstance(prior,dict) or (prior.get('name'),prior.get('folder_path'),prior.get('tracks'),str(prior.get('pid') or ''))
                    !=(playlist.name,list(playlist.folder_path),list(playlist.effective_keys),str(playlist.pid or ''))):
                raise SelectedExportError('A retained device playlist no longer matches its confirmed catalog')
            device_selection[key]=dict(name=playlist.name,folder_path=list(playlist.folder_path),
                keys=list(playlist.effective_keys),pid=playlist.pid,export_key=playlist.export_key)
        pins=tuple((key,'','') if key in device_selection else (key,native,uuid) for key,native,uuid in pins)
    request=dict(kind=kind,dev=dev,source=_source(host),repository=_repository(host),target=_mount(host,dev),
        selection=pins,options=deepcopy(options or {}),batch=batch,batch_target=target,cancelled=False,
        checkpoint=None,terminal=False,device_selection=device_selection)
    waiting=getattr(host,'_source_sync_waiting',None)
    if waiting is None:waiting=[];host._source_sync_waiting=waiting
    waiting.append(request)
    if getattr(host,'_state_committer',None) is None:host._state_committer=StateCommitter(host)
    committer=host._state_committer
    def current():
        return bool(not request['cancelled'] and not request['terminal'] and not host._shutting_down
            and getattr(host,'_source_sync_waiting',None) is waiting and request in waiting
            and _source(host)==request['source'] and _repository(host)==request['repository']
            and _mount(host,dev)==request['target'] and (batch is None or
                getattr(host,'_export_batch',None) is batch and batch.get('active') is target and not batch.get('cancelled',False)))
    def remove():
        if request in waiting:waiting.remove(request)
        if getattr(host,'_source_sync_waiting',None) is waiting and not waiting:host._source_sync_waiting=None
    def reject(error):
        if request['terminal'] or request['cancelled']:return
        if isinstance(error,SelectedExportWait):
            request['checkpoint']=None;host._start_pulse(str(error));QTimer.singleShot(100,poll);return
        request['terminal']=True;remove();failed(error,request)
    def accepted(capture):
        if not current():reject(SelectedExportError('The export request changed before capture finished'));return
        request['checkpoint']=None
        def deliver():
            if request['cancelled'] or request['terminal']:return
            if not current():reject(SelectedExportError('The export request changed before delivery'));return
            request['terminal']=True;remove();ready(capture)
        QTimer.singleShot(0,deliver)
    def poll():
        if request['terminal'] or request['cancelled']:return
        if not current():reject(SelectedExportError('Library or connected iPod changed before export'));return
        if waiting[0] is not request or host._state_maintenance or host._device_operation_owned():
            QTimer.singleShot(100,poll);return
        if request['kind']=='rockbox' and not request.get('manifest_ready'):
            if not request.get('manifest_worker'):
                from .workers import OneShotTask
                def observed(task):
                    request['manifest_worker']=None
                    if request['cancelled'] or request['terminal']:return
                    if not current():reject(SelectedExportError('Library or connected iPod changed while reading its catalog'));return
                    if isinstance(task.result,Exception):reject(task.result);return
                    result=task.result
                    if result is not None and result[0].get('schema')!=manifest.SCHEMA:
                        reject(SelectedExportError('This iPod catalog is not a supported Rockbox-only catalog'));return
                    request['managed_manifest']=result;request['manifest_ready']=True
                    QTimer.singleShot(0,poll)
                worker=OneShotTask(lambda:_read_rockbox_manifest(dev))
                request['manifest_worker']=worker;committer.owner.adopt(worker,observed);worker.start()
            host._start_pulse('Reading iPod playlist details');host.cancel_btn.setVisible(True)
            return
        if request['kind']=='dual' and not getattr(host,'_stock_view_cache',{}).get(dev):
            if not request.get('view_requested'):
                request['view_requested']=True
                read=getattr(host,'_read_stock_device_view',None)
                if not callable(read):reject(SelectedExportError('Read this iPod playlist details before exporting'));return
                read(dev,host._device_profiles.get(dev))
            failure=getattr(host,'_stock_view_failures',{}).get(dev)
            if failure:reject(SelectedExportError('iPod playlist details could not be read: '+str(failure)));return
            host._start_pulse('Reading iPod playlist details');host.cancel_btn.setVisible(True)
            QTimer.singleShot(100,poll);return
        if request['checkpoint'] is not None:
            committer._pump_checkpoints();QTimer.singleShot(100,poll);return
        try:
            selected=_resolve(host,request);projected,_baseline=_projection_selection(host,request,selected)
            require_fences(capture_fences(host,projected))
        except SelectedExportWait as error:
            host._start_pulse(str(error));QTimer.singleShot(100,poll);return
        except Exception as error:reject(error);return
        host._start_pulse('Preparing iPod export');host.cancel_btn.setVisible(True)
        request['checkpoint']=committer.queue_checkpoint(lambda:_build(host,request),accepted,reject,
            current=current,validate=lambda capture:capture.verify_worker())
    def cancel():
        worker=request.get('manifest_worker')
        if worker is not None:worker.cancel()
        if request['checkpoint'] is not None:committer.cancel_checkpoint(request['checkpoint'])
    request['cancel_checkpoint']=cancel
    poll();return True
