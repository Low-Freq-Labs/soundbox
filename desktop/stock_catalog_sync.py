"""Explicit selected-playlist Sync against the current cached Apple catalog."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
from typing import Any

from PySide6.QtWidgets import QMessageBox

from soundbox import alias_intents
from soundbox.export_snapshot import capture_export, source_paths
from soundbox.manifest import playlist_key
from soundbox.operation_control import OperationCancelled
from soundbox.stock_ipod_catalog import (CATALOG_METADATA_FIELDS, catalog_match_labels,
                                        catalog_source_playlist, compare_stock_catalog)
from soundbox.stock_ipod_playlist_binding import PlaylistBinding,normalize_playlist_name
from soundbox.stock_ipod_catalog_state import StockCatalogWitness
from .devices import device_facts, device_fs
from .workers import OneShotTask


# A cold/changed eligibility observation hands the same request to full capture
# only after the read-only worker has joined and released device ownership.
_FULL_STOCK_CAPTURE = object()


def _playlists(host, *, names=(), keys=()):
    if host.lib is None:return ()
    rows=list(host._export_playlists(include_views=False))
    requested={normalize_playlist_name(value) for value in names}
    cached=getattr(host,'_sync_playlists_cache',None)
    for view,title in (('collection','Collection'),('artists','Artists')):
        key='cust:'+title
        if key not in keys and normalize_playlist_name(title) not in requested:continue
        prepared=[row for row in cached or () if playlist_key(row)==key]
        row=prepared[0] if len(prepared)==1 else host._view_playlist(view)
        if row is not None:rows.append(row)
    return tuple({playlist_key(row):row for row in rows}.values())


def _display(host, selected):
    tracks={track.track_id:track for playlist in selected for track in host.lib.resolve(playlist)}
    return _display_tracks(host, tracks)


def _display_tracks(host, tracks):
    return {tid:{'id':tid,**{field:row.get(field,'') for field in CATALOG_METADATA_FIELDS},
                 'bpm_estimated':row.get('bpm_estimated',False)}
            for tid,track in tracks.items() for row in (host._row_for(track),)}


def _witness(host, device):
    return getattr(host,'_stock_catalog_witness_cache',{}).get(device)


def has_sync_source(host, device):
    """Cheap current playlist headers; no song/member/metadata or media work.

    This labels an action, never authorizes Sync. Remembered keys must retain
    their exact current source and device identity; only unbound keys may use
    the existing unique-name affordance before the action's full comparison.
    """
    library=host.lib;cached=getattr(host,'_stock_view_cache',{}).get(device)
    if library is None or cached is None:return False
    regular=[row for row in cached[0].playlists if row.kind=='Playlist']
    device_counts=Counter(row.playlist_id for row in regular)
    if (not regular or any(type(identity) is not int or identity<=0 or count!=1
                           for identity,count in device_counts.items())):return False
    device_names=Counter(normalize_playlist_name(row.title) for row in regular)
    nodes:dict[str,list[Mapping]]=defaultdict(list)
    if library.source_kind=='rekordbox-db':
        for row in library.source_playlist_catalog.get('nodes',()):
            if not isinstance(row,Mapping):return False
            if row.get('rb_local_deleted',0)==0:nodes[str(row.get('ID',''))].append(row)
    headers=[]
    for playlist in library.playlists:
        identity=str(playlist.pid or '');native_uuid='';valid=True
        if identity and library.source_kind=='rekordbox-db':
            matches=nodes.get(identity,[])
            valid=len(matches)==1 and isinstance(matches[0].get('UUID'),str) and bool(matches[0]['UUID'])
            if valid:
                native_uuid=matches[0]['UUID']
                valid='\0' not in native_uuid and len(native_uuid)<=4096
        headers.append((playlist_key(playlist),identity,native_uuid,playlist.name,valid))
    # An adopted native row retains the custom export key. Its saved custom
    # shadow is the same source, while duplicate native rows stay ambiguous.
    native_keys={row[0] for row in headers}
    headers.extend(('cust:'+name,'','',name,True) for name in host.custom
                   if 'cust:'+name not in native_keys)
    keys={row[0] for row in headers}
    for name in ('Collection','Artists'):
        if 'cust:'+name not in keys:headers.append(('cust:'+name,'','',name,True))
    source_keys=Counter(row[0] for row in headers)
    source_names=Counter(normalize_playlist_name(row[3]) for row in headers)
    witness=_witness(host,device);bindings:tuple[PlaylistBinding,...]=()
    if (isinstance(witness,StockCatalogWitness) and witness.device_key==cached[0].identity_key
            and witness.source==(str(library.source),library.source_kind,library.source_library_id)):
        bindings=witness.bindings
    by_key:dict[str,list[PlaylistBinding]]=defaultdict(list)
    owners:dict[int,set[str]]=defaultdict(set)
    for binding in bindings:
        by_key[binding.key].append(binding);owners[binding.device_playlist_id].add(binding.key)
    for key,native,native_uuid,name,valid in headers:
        if not valid or source_keys[key]!=1:continue
        remembered=by_key.get(key,())
        if remembered:
            if len(remembered)!=1:continue
            binding=remembered[0];target=binding.device_playlist_id
            if ((binding.native_id,binding.uuid)==(native,native_uuid)
                    and device_counts[target]==1 and owners[target]=={key}):return True
            continue  # A stale saved key cannot borrow a same-name replacement.
        normalized=normalize_playlist_name(name)
        if source_names[normalized]==1 and device_names[normalized]==1:
            target=next(row.playlist_id for row in regular
                        if normalize_playlist_name(row.title)==normalized)
            if not owners[target] or owners[target]=={key}:return True
    return False


def needs_sync(host, device):
    """A pending cached observation changes the primary action, not eligibility."""
    drift=getattr(host,'_drift_cache',{}).get(device)
    if type(drift) is int:
        pending=drift>0
    else:
        cached=getattr(host,'_stock_view_cache',{}).get(device)
        pending=bool(cached and getattr(cached[0],'sync_state','') in
                     {'local_changes','reconcile_required'})
    return pending and has_sync_source(host,device)


@dataclass(frozen=True)
class StockExportScope:
    """Exact prior associations for the stock Export's additional managed work."""
    source:tuple[str,str,str]
    device_key:str
    database_sha256:str
    artwork_sha256:str
    bindings:tuple[PlaylistBinding,...]
    witness:bytes
    removed_keys:tuple[str,...]=()

    def require_current(self,host,device):
        cached=getattr(host,'_stock_view_cache',{}).get(device)
        witness=_witness(host,device)
        if (host.lib is None or cached is None or not isinstance(witness,StockCatalogWitness)
                or (str(host.lib.source),host.lib.source_kind,host.lib.source_library_id)!=self.source
                or (cached[0].identity_key,cached[0].database_sha256,cached[0].artwork_database_sha256 or '')
                    !=(self.device_key,self.database_sha256,self.artwork_sha256)
                or witness.encode()!=self.witness):
            raise ValueError('The managed iPod playlist links changed; start Export again')
        available={playlist_key(row):row for row in _playlists(host,keys=self.removed_keys)}
        active={str(row.get('ID','')) for row in host.lib.source_playlist_catalog.get('nodes',())
                if row.get('rb_local_deleted',0)==0}
        if any(binding.key in available or binding.native_id and binding.native_id in active
               for binding in self.bindings if binding.key in self.removed_keys):
            raise ValueError('A removed source playlist was replaced; start Export again')

    def require_shadow(self,shadow):
        if (shadow is None or shadow.identity_key!=self.device_key
                or shadow.database_sha256!=self.database_sha256
                or shadow.artwork_database_sha256!=self.artwork_sha256
                or set(shadow.playlists)!={row.key for row in self.bindings}):
            raise ValueError('The managed iPod baseline changed; refresh before exporting and syncing')
        for binding in self.bindings:
            row=shadow.playlists[binding.key]
            if (str(row.get('pid') or ''),str(row.get('device_name') or row.get('name') or ''))!=(binding.native_id,binding.name):
                raise ValueError('The managed iPod playlist baseline is unavailable')

    def require_plan(self,plan):
        if (plan.database_sha256_before!=self.database_sha256 or plan.identity_key!=self.device_key
                or self.removed_keys and not plan.shadow_fast_path):
            raise ValueError('The iPod changed while its combined Export and Sync was being prepared')


def stock_export_selection(host,device,requested):
    """Add only exact linked stock lists; clean contracts remain reusable.

    Exact linked lists are included even before the asynchronous drift badge
    catches an edit. This is not a name-based adoption of unrelated native
    playlists. Device publication
    still proves its complete fresh baseline independently.
    """
    profile=getattr(host,'_device_profiles',{}).get(device)
    cached=getattr(host,'_stock_view_cache',{}).get(device)
    selected=tuple(requested or ())
    if host.lib is None:return selected,None
    if profile is None or profile.kind!='stock_ipod' or not selected or cached is None:return selected,None
    drift=getattr(host,'_drift_cache',{}).get(device)
    pending=(drift>0 if type(drift)is int else
             getattr(cached[0],'sync_state','') in {'local_changes','reconcile_required'})
    witness=_witness(host,device);snapshot=cached[0]
    source=(str(host.lib.source),host.lib.source_kind,host.lib.source_library_id)
    if (not isinstance(witness,StockCatalogWitness) or witness.source!=source
            or witness.device_key!=snapshot.identity_key
            or witness.database_sha256!=snapshot.database_sha256
            or witness.artwork_database_sha256!=(snapshot.artwork_database_sha256 or '')):
        if pending:raise ValueError('Pending managed Sync needs current iPod playlist links; refresh before exporting')
        return selected,None
    payload=witness.encode()
    if not witness.bindings:return selected,None
    targets=defaultdict(list)
    names=Counter(row.title for row in snapshot.playlists if row.kind=='Playlist')
    for row in snapshot.playlists:
        if row.kind=='Playlist':targets[row.playlist_id].append(row)
    owners=Counter(row.device_playlist_id for row in witness.bindings)
    keys={row.key for row in witness.bindings}|{playlist_key(row) for row in selected}
    available={playlist_key(row):row for row in _playlists(host,keys=keys)}
    nodes=defaultdict(list)
    for row in host.lib.source_playlist_catalog.get('nodes',()):nodes[str(row.get('ID',''))].append(row)
    extra=[];removed=[]
    for binding in witness.bindings:
        matches=targets[binding.device_playlist_id]
        if (owners[binding.device_playlist_id]!=1 or len(matches)!=1
                or matches[0].title!=binding.name
                or tuple(entry.dbid for entry in matches[0].entries)!=tuple(dbid for _local,dbid in binding.members)):
            raise ValueError('A managed iPod playlist changed; refresh before exporting')
        current=available.get(binding.key)
        if current is None:
            if names[binding.name]!=1:
                raise ValueError('A removed managed playlist shares its name with another iPod playlist')
            old=nodes.get(binding.native_id,()) if binding.native_id else ()
            if any(row.get('rb_local_deleted',0)==0 or row.get('UUID')!=binding.uuid for row in old):
                raise ValueError('A managed source playlist was replaced or is unavailable')
            removed.append(binding.key)
            continue
        identity=catalog_source_playlist(host.lib,current)
        if (identity.identity_error or (identity.key,identity.native_id,identity.uuid)
                !=(binding.key,binding.native_id,binding.uuid)):
            raise ValueError('A managed source playlist identity changed; start Export again')
        extra.append(current)
    # Existing device order remains stable; explicitly new selections follow it.
    union={playlist_key(row):row for row in extra}
    union.update((playlist_key(row),row) for row in selected)
    scope=StockExportScope(source,snapshot.identity_key,snapshot.database_sha256,
        snapshot.artwork_database_sha256 or '',witness.bindings,payload,tuple(removed))
    return tuple(union.values()),scope


def _comparison(host, selected, snapshot, kind, witness):
    display=_display(host,selected)
    labels=catalog_match_labels(host.lib,list(selected),host.settings.get(alias_intents.ORIGINS,{}))
    return compare_stock_catalog(host.lib,list(selected),snapshot,display_rows=display,
        match_labels=labels,catalog_witness=witness,incremental=kind=='stock_ipod',
        artist_overrides=host.overrides,title_overrides=host.titles)


def _associations(host, device, selections=None, source_playlists=None):
    cached=host._stock_view_cache.get(device);profile=host._device_profiles.get(device)
    if host.lib is None or cached is None or profile is None:return ()
    snapshot=cached[0];witness=_witness(host,device)
    targets={str(row.playlist_id):row for row in snapshot.playlists if row.kind=='Playlist'}
    if len(targets)!=sum(row.kind=='Playlist' for row in snapshot.playlists):return ()
    if selections is None:
        wanted=set(targets)
    else:
        wanted=set()
        for identity,title,count in selections:
            target=targets.get(str(identity))
            if target is None or (target.title,len(target.entries))!=(title,count):return ()
            wanted.add(str(identity))
        if len(wanted)!=len(selections):return ()
    if source_playlists is not None:
        keys={playlist_key(row) for row in source_playlists}
        possible=tuple(row for row in _playlists(host,keys=keys) if playlist_key(row) in keys)
        if len(possible)!=len(source_playlists):return ()
    else:
        names={normalize_playlist_name(targets[key].title) for key in wanted}
        bound=set()
        if (witness is not None and witness.device_key==snapshot.identity_key
                and witness.source==(str(host.lib.source),host.lib.source_kind,host.lib.source_library_id)):
            bound={(row.key,row.native_id,row.uuid) for row in witness.bindings
                   if str(row.device_playlist_id) in wanted}
        possible=tuple(row for row in _playlists(host,names=names,keys={key for key,_native,_uuid in bound})
            if normalize_playlist_name(row.name) in names or bound and
            (lambda identity:(identity.key,identity.native_id,identity.uuid) in bound)(catalog_source_playlist(host.lib,row)))
    if not possible:return ()
    comparison=_comparison(host,possible,snapshot,profile.kind,witness)
    by_key={playlist_key(row):row for row in possible}
    output=[]
    for recognized in comparison.recognition:
        identity=str(recognized.device_playlist_id)
        if recognized.recognized and identity in wanted:
            target=targets[identity]
            output.append(((identity,target.title,len(target.entries)),by_key[recognized.playlist_key]))
    if selections is not None and {row[0][0] for row in output}!=wanted:return ()
    if source_playlists is not None and len(output)!=len(source_playlists):return ()
    return tuple(output)


def default_sync_selection(host, dev):
    """Existing unique cached associations only; no ordinal or media ownership."""
    try:return tuple(selection for selection,_source in _associations(host,dev))
    except (ValueError,TypeError,KeyError):return ()


def source_sync_selection(host, dev, source_playlists):
    if not source_playlists:return ()
    try:return tuple(selection for selection,_source in _associations(
        host,dev,source_playlists=tuple(source_playlists)))
    except (ValueError,TypeError,KeyError):return ()


def _choose_export(host, device):
    """An absent association offers Export without nesting an owned batch."""
    if host._shutting_down or host._state_maintenance:return
    batch=getattr(host,'_export_batch',None)
    if batch is not None:
        target=batch.get('active')
        if target and target.get('path')==device:
            host._export_target_result(device,RuntimeError(
                'No current playlist association is available; choose a playlist in Export'))
            host._maybe_finish_export_target()
        return
    host._show_export_dialog(preferred=device)


def request_device_sync(host, dev):
    if host._shutting_down or host._state_maintenance:return
    selected=default_sync_selection(host,dev)
    if not selected:
        _choose_export(host,dev)
        return
    request_sync(host,selected,device=dev)


def _selected(host, keys):
    rows=_playlists(host,keys=keys)
    result=[]
    for key in keys:
        matches=[row for row in rows if playlist_key(row)==key]
        if len(matches)!=1:return None
        result.append(matches[0])
    return tuple(result)


def _source(host, keys):
    selected=_selected(host,keys)
    if selected is None:return None
    identities=tuple((catalog_source_playlist(host.lib,row),tuple(row.effective_keys)) for row in selected)
    rows=_display(host,selected)
    tracks={track.track_id:track for row in selected for track in host.lib.resolve(row)}
    return ((str(host.lib.source),host.lib.source_kind,host.lib.source_library_id),identities,
        tuple((tid,track.native_id,track.source_uuid,str(track.path),str(track.native_path),track.size,track.total_time,
               json.dumps(rows[tid],sort_keys=True,allow_nan=False)) for tid,track in tracks.items()),
        source_paths(host.lib))


def _source_still_matches(host, keys, captured):
    """Later musical edits advance independently of this frozen generation."""
    if (str(host.lib.source), host.lib.source_kind, host.lib.source_library_id) != captured[0]:
        return False
    selected = _selected(host, keys)
    if selected is None or len(selected) != len(captured[1]):
        return False
    for playlist, (previous, _members) in zip(selected, captured[1]):
        current = catalog_source_playlist(host.lib, playlist)
        if ((current.key, current.native_id, current.uuid, current.identity_error)
                != (previous.key, previous.native_id, previous.uuid, previous.identity_error)):
            return False
    tracks = {row[0]: host.lib.tracks_by_id.get(row[0]) for row in captured[2]}
    if any(track is None for track in tracks.values()):
        return False
    current_tracks = tuple((tid, track.native_id, track.source_uuid, str(track.path), str(track.native_path),
        track.size, track.total_time)
        for tid, track in tracks.items())
    return current_tracks == tuple(row[:-1] for row in captured[2]) and source_paths(host.lib) == captured[3]


def _repository_token(host):
    repository = getattr(host, '_state_repository', None)
    return (repository, repository.generation, repository.area.generation) if repository is not None else None


def _mount(host, device):
    return (host._device_mount_tokens.get(device,0),device_facts(device,cached_only=True).volume_uuid)


def _batch_owned(host, request):
    batch=getattr(host,'_export_batch',None)
    return bool(request.batch is not None and batch is request.batch
                and batch.get('active') is request.batch_target)


def _end_deferred_catalog(host, request, message, *, cancelled=False):
    """Retire this unstarted request without borrowing another device owner."""
    if host._shutting_down:return False
    if request.batch is not None:
        if not _batch_owned(host, request):return False
        from .export_dialog import DeviceRequestNotStarted
        error=OperationCancelled(message) if cancelled else DeviceRequestNotStarted(message)
        error.recovery_status='not needed'
        host._export_target_result(request.device, error)
    elif getattr(host, '_export_batch', None) is not None:
        return False
    active = host._device_coordinator.active
    pending = getattr(host, '_pending_device_operation', None)
    other_operation = ((active is not None and active.phase != 'discovering')
                       or bool(getattr(host,'_source_sync_waiting',None)))
    formatting = bool(getattr(host, '_usb_format_device', ''))
    if (not other_operation
            and (pending is None or getattr(pending, '_catalog_request', None) is request)):
        host._exporting = False;host._cancelling = False
        if not formatting:
            host._stop_pulse(force=True)
            host.progress.setVisible(False);host.cancel_btn.setVisible(False)
        host._update_device_action_state()
    if request.batch is not None:
        host._maybe_finish_export_target()
    elif not other_operation and not formatting:
        host._flash_status(message, 15)
    return True


def cancel_deferred_sync(host):
    """Cancel only the captured, not-yet-started catalog request."""
    callback = getattr(host, '_pending_device_operation', None)
    request = getattr(callback, '_catalog_request', None)
    if request is None:return False
    if request.batch is not None and not _batch_owned(host, request):return False
    if request.batch is None and getattr(host, '_export_batch', None) is not None:return False
    host._pending_device_operation = None
    if request.batch is not None:
        request.batch['cancelled'] = True
        request.batch['pending'].clear()
    return _end_deferred_catalog(host, request,
        'Playlist Sync cancelled before planning. No device changes were made.',cancelled=True)


def _defer_catalog(host, request, callback):
    callback._catalog_request = request
    return host._defer_device_operation_until_scan_finishes(callback)


@dataclass(frozen=True)
class CatalogRequest:
    device:str
    selections:tuple
    keys:tuple[str,...]
    source:Any
    mount:tuple
    kind:str
    snapshot:Any
    captured:Any
    display:bytes
    labels:bytes
    witness:Any
    artwork:Any
    batch:Any=None
    batch_target:Any=None
    repository:Any=None

    def current(self, host, *, check_artwork=True):
        profile=host._device_profiles.get(self.device)
        cached=host._stock_view_cache.get(self.device)
        try:
            return bool(host.lib is not None and profile is not None and profile.kind==self.kind
                and (self.batch is None or _batch_owned(host,self) and not self.batch.get('cancelled',False))
                and (not check_artwork or host.art_cache.selection_current(self.artwork))
                and cached is not None and self.mount==_mount(host,self.device)
                and (cached[0].identity_key,cached[0].database_sha256,cached[0].artwork_database_sha256,
                     cached[0].stock_format,cached[0].usb_family,
                     getattr(cached[0],'firewire_guid',''),cached[0].usb_serial)==
                    (self.snapshot.identity_key,self.snapshot.database_sha256,self.snapshot.artwork_database_sha256,
                     self.snapshot.stock_format,self.snapshot.usb_family,
                     getattr(self.snapshot,'firewire_guid',''),self.snapshot.usb_serial)
                and _repository_token(host)==self.repository
                and _source_still_matches(host,self.keys,self.source))
        except (ValueError,TypeError,KeyError):return False


def request_sync(host, selected, *, device=None, confirmed=False, source_playlists=None):
    if host._shutting_down or host._state_maintenance or host.lib is None:return
    if device is None and host.stack.currentIndex()!=1:return
    device=device or host._current_device_path
    profile=host._device_profiles.get(device);cached=host._stock_view_cache.get(device)
    if profile is None or profile.kind not in {'stock_ipod','dual_boot'} or cached is None:return
    try:
        selections=(selected,) if isinstance(selected,(tuple,list)) and len(selected)==3 and isinstance(selected[0],(str,int)) else tuple(selected or ())
        associations=_associations(host,device,selections or None,source_playlists)
        if not associations:
            _choose_export(host,device)
            return
        selections=tuple(row[0] for row in associations);sources=tuple(row[1] for row in associations)
        keys=tuple(playlist_key(row) for row in sources)
        witness=_witness(host,device)
        display=_display(host,sources)
        labels=catalog_match_labels(host.lib,list(sources),host.settings.get(alias_intents.ORIGINS,{}))
        batch=getattr(host,'_export_batch',None)
        batch_target=batch.get('active') if batch else None
        if not batch_target or batch_target.get('path')!=device:batch=None;batch_target=None
        request=CatalogRequest(device,selections,keys,_source(host,keys),_mount(host,device),profile.kind,cached[0],
            capture_export(host.lib,sources,titles=host.titles,artists=host.overrides,
                           playlist_identities=True),
            json.dumps(display,allow_nan=False).encode(),json.dumps(labels,allow_nan=False).encode(),witness,
            host.art_cache.selection_token(display),batch,batch_target,_repository_token(host))
    except (ValueError,TypeError,KeyError) as exc:
        host._flash_status(str(exc),15);return
    from .export_wait import defer_selected_artwork
    art_owner=getattr(host.art_cache,'scope_owner',host.art_cache)
    def ready_artwork():
        # Metadata/membership remain the original detached generation. Only the
        # selected manual choice accepted before this request was still saving.
        fresh=replace(request,artwork=host.art_cache.selection_token(json.loads(request.display)))
        _begin(host,fresh,confirmed)
    if defer_selected_artwork(host,device,display,
            current=lambda:(getattr(host.art_cache,'scope_owner',host.art_cache) is art_owner
                            and request.current(host,check_artwork=False)),
            ready=ready_artwork,
            failed=lambda error:_end_deferred_catalog(host,request,str(error)),
            batch=request.batch,batch_target=request.batch_target):return
    _begin(host,request,confirmed)


def _begin(host, request, confirmed):
    if host._shutting_down or host._state_maintenance or not request.current(host):
        _end_deferred_catalog(host, request,
            'The library or connected iPod changed before Sync started. No device changes were made.')
        return
    if host._device_operation_owned():
        _end_deferred_catalog(host, request,
            'Another device operation started before playlist Sync. No Sync changes were made.')
        QMessageBox.warning(host,'Device busy','Wait for the current device operation.');return
    if _defer_catalog(host, request, lambda:_begin(host,request,confirmed)):return
    access=host._stock_write_access(request.device,host._device_profiles[request.device])
    if access is None or not access.writable or not request.current(host):
        _end_deferred_catalog(host, request,
            'The library or connected iPod is no longer ready for playlist Sync. No device changes were made.')
        return
    _prepare(host,request)


def _prepare(host, request):
    if host._shutting_down or host._state_maintenance or not request.current(host):
        _end_deferred_catalog(host, request,
            'The library or connected iPod changed before Sync started. No device changes were made.')
        return
    if host._device_operation_owned():
        _end_deferred_catalog(host, request,
            'Another device operation started before playlist Sync. No Sync changes were made.')
        QMessageBox.warning(host,'Device busy','Wait for the current device operation.');return
    if _defer_catalog(host, request, lambda:_prepare(host,request)):return
    access=host._stock_write_access(request.device,host._device_profiles[request.device])
    if access is None or not access.writable or not request.current(host):
        _end_deferred_catalog(host, request,
            'The library or connected iPod is no longer ready for playlist Sync. No device changes were made.')
        return
    store=host.stock_catalog_witness_store
    stock_art_cache=host.stock_art_cache;art_cache=host.art_cache
    omission_cache_root=host._data_file.parent/'stock_source_observations'
    check_omissions=(request.kind=='stock_ipod' and isinstance(request.witness,StockCatalogWitness)
                     and any(row.key in request.keys and row.has_omissions for row in request.witness.bindings))
    shadow_store=host.stock_ipod_shadow_store if check_omissions else None
    media_cache=host.rockbox_media_cache if request.kind=='dual_boot' else None
    filesystem=device_fs(request.device)
    host._validate_export_target(request.device,device_facts(request.device,cached_only=True))
    if host._shutting_down or host._state_maintenance or host._device_operation_owned() or not request.current(host):
        _end_deferred_catalog(host, request,
            'The library or connected iPod changed before Sync started. No device changes were made.')
        return
    host._release_eject_ready()
    warnings=[]
    def current_artwork():
        if not art_cache.selection_current(request.artwork):
            raise RuntimeError('The selected artwork changed. Review it and start Sync again.')
    def save_witness(stock):
        witness=getattr(stock,'catalog_witness',None)
        if witness is not None:
            try:store.save(witness)
            except Exception as exc:warnings.append('Sync completed; its optional comparison cache could not be saved: '+str(exc))
    def plan(cancelled):
        current_artwork()
        inputs=request.captured.materialize()
        if check_omissions:
            from soundbox.stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE
            from soundbox.stock_sync_reuse import stock_selection_reusable,stock_shadow_selection_current
            omissions=NegativeSourceOmissions(root=omission_cache_root,profile=STOCK_SOURCE_PROFILE)
            shadow=shadow_store.load(request.snapshot.identity_key) if shadow_store is not None else None
            reusable=stock_selection_reusable(inputs.library,list(inputs.playlists),request.snapshot,
                request.witness,shadow,omissions)
            omissions.flush()
            if cancelled():raise OperationCancelled('Playlist Sync cancelled before source eligibility capture')
            if not reusable or not stock_shadow_selection_current(inputs.library,list(inputs.playlists),shadow):
                return _FULL_STOCK_CAPTURE
        result:Any
        options=dict(snapshot=request.snapshot,model_number=request.snapshot.model or '',filesystem=filesystem,
            art_cache=art_cache,stock_art_cache=stock_art_cache,update_metadata=True,
            artist_overrides=inputs.artists,title_overrides=inputs.titles,
            display_rows=json.loads(request.display),match_labels=json.loads(request.labels),
            catalog_witness=request.witness,usb_family=getattr(request.snapshot,'usb_family',''),
            firewire_guid=getattr(request.snapshot,'firewire_guid','') or request.snapshot.usb_serial)
        if request.kind=='dual_boot':
            from soundbox.dual_ipod import plan_dual_ipod_catalog_update
            if media_cache is None:raise RuntimeError('The iPod media cache is unavailable')
            result=plan_dual_ipod_catalog_update(inputs.library,list(inputs.playlists),request.device,
                rockbox_media_cache=media_cache,**options)
            stock=result.stock
        else:
            from soundbox.stock_ipod import plan_stock_ipod_catalog_update
            result=plan_stock_ipod_catalog_update(inputs.library,list(inputs.playlists),request.device,
                incremental=True,omission_cache_root=omission_cache_root,**options)
            stock=result
        current_artwork()
        actual={str(row.device_playlist_id) for row in stock.catalog_recognition if row.recognized}
        if actual!={row[0] for row in request.selections}:
            raise RuntimeError('The recognized iPod playlists changed; select them again')
        if cancelled():raise OperationCancelled('Playlist Sync cancelled before device commit')
        if check_omissions and result.requires_commit:
            # Only a genuine no-op skips full Sync. Changes retain its owned
            # media/pruning rules and renew the complete committed shadow.
            return _FULL_STOCK_CAPTURE
        if not result.requires_commit:
            from soundbox.stock_ipod import verify_stock_ipod_catalog_noop
            verify_stock_ipod_catalog_noop(stock)
            current_artwork()
            if cancelled():raise OperationCancelled('Playlist Sync cancelled before saving its comparison')
            save_witness(stock)
        return result
    _start(host,request,plan,'_stock_plan_worker',
        'plan-catalog-probe' if check_omissions else 'plan-catalog',
        lambda result:_planned(host,request,result,save_witness,warnings),
        'Checking playlist changes' if check_omissions else 'Preparing playlist Sync')


def _start(host, request, operation, attribute, intent, complete, label):
    worker:OneShotTask
    worker=OneShotTask(lambda:operation(worker.cancellation.is_set));setattr(host,attribute,worker)
    host._exporting=True;host._set_device_progress(0 if intent.startswith('plan') else 150);host._start_pulse(label)
    def finished(result):
        if (intent.startswith('plan') and worker.cancellation.is_set()
                and not isinstance(result,Exception)):
            _failed(host,request,OperationCancelled('Playlist Sync cancelled. No changes were committed.'))
        else:complete(result)
    delivered=False
    def completed(result):
        nonlocal delivered
        delivered=True;finished(result)
    def retire():
        owned=getattr(host,attribute,None) is worker
        if intent.startswith('plan') and worker.cancellation.is_set() and not isinstance(worker.result,Exception):
            setattr(worker,'result',OperationCancelled('Playlist Sync cancelled. No changes were committed.'))
        host._finish_device_result_worker(attribute,worker,completed)
        if (owned and not delivered and host._device_coordinator.active is None
                and host._stock_plan_worker is None and host._stock_commit_worker is None):
            if request.batch is not None and _batch_owned(host,request) and not host._shutting_down:
                _failed(host,request,'The connected iPod changed or disconnected. Playlist Sync was stopped.')
                host._maybe_finish_export_target()
                return
            if request.batch is not None and getattr(host,'_export_batch',None) is not None:return
            host._exporting=False;host._cancelling=False
            if not host._shutting_down:
                host._stop_pulse(force=True);host.progress.setVisible(False);host.cancel_btn.setVisible(False)
                host._update_device_action_state()
    worker.finished.connect(retire)
    try:host._start_device_worker(worker,request.device,intent)
    except Exception as exc:
        host._finish_worker(attribute,worker);_failed(host,request,exc)


def _failed(host, request, error, *, committed=False):
    from .export_dialog import device_failure_text
    cancelled=(isinstance(error,OperationCancelled) and not committed
        and getattr(error,'recovery_status',None) in {None,'not needed','rolled back'})
    message=str(error)+('\n\nDevice changes were committed; reconnect before trying again.' if committed else '')
    if request.batch is not None:
        if not _batch_owned(host,request):return
        failure=error if isinstance(error,Exception) else RuntimeError(message)
        if committed and not getattr(failure,'committed',False):
            from soundbox.operation_control import CommittedOperationError
            failure=CommittedOperationError(message)
        host._export_target_result(request.device,failure)
    if host._device_coordinator.active is not None:return
    host._exporting=False;host._cancelling=False;host._stop_pulse(force=True)
    host.progress.setVisible(False);host.cancel_btn.setVisible(False);host._update_device_action_state()
    if request.batch is None:
        if cancelled:
            from .export_wait import retain_source_cancellation
            if not retain_source_cancellation(host):
                host._flash_status('Playlist Sync cancelled — previous library kept',15)
        else:QMessageBox.warning(host,'Playlist Sync stopped',device_failure_text(error,committed=committed))


def _planned(host, request, result, save_witness, warnings):
    if result is _FULL_STOCK_CAPTURE:
        if (not request.current(host) or getattr(host,'_export_batch',None) is not request.batch):
            _end_deferred_catalog(host,request,'The library, selected playlists or iPod changed before Sync')
            return
        try:
            # A later musical edit belongs to a later request, even though
            # compatible native identities can survive ordinary readback.
            if _source(host,request.keys)!=request.source:
                raise RuntimeError('The selected songs changed before source capture. Start Sync again.')
            selected=_selected(host,request.keys)
            if selected is None:raise RuntimeError('The selected playlists changed before source capture')
            host._exporting=False;host._stop_pulse(force=True)
            host._request_ipod_export_capture('stock',request.device,list(selected),reconcile=True)
        except Exception as error:_failed(host,request,error)
        return
    host._exporting=False;host._stop_pulse(force=True)
    if isinstance(result,Exception):_failed(host,request,result);return
    if not request.current(host):_failed(host,request,'The library or connected iPod changed. Select the playlists again.');return
    stock=result.stock if request.kind=='dual_boot' else result
    if not result.requires_commit:_finish(host,request,stock,None,warnings);return
    art_cache=host.art_cache
    def commit(cancelled):
        if cancelled():raise OperationCancelled('Playlist Sync cancelled before device commit')
        if not art_cache.selection_current(request.artwork):
            raise RuntimeError('The selected artwork changed. Review it and start Sync again.')
        outcome:Any
        if request.kind=='dual_boot':
            from soundbox.dual_ipod import commit_dual_ipod_sync
            outcome=commit_dual_ipod_sync(result)
        else:
            from soundbox.stock_ipod import commit_stock_ipod_sync
            outcome=commit_stock_ipod_sync(result)
        save_witness(stock)
        return outcome
    _start(host,request,commit,'_stock_commit_worker','commit-catalog',
        lambda outcome:_committed(host,request,stock,outcome,warnings),'Syncing iPod playlists')


def _committed(host, request, stock, result, warnings):
    host._exporting=False;host._stop_pulse(force=True)
    if isinstance(result,Exception):_failed(host,request,result,committed=bool(getattr(result,'committed',False)));return
    _finish(host,request,stock,result,warnings)


def _finish(host, request, stock, result, warnings):
    host._cancelling=False
    outcome=getattr(result,'stock',result)
    metadata=int(getattr(outcome,'metadata_updates',len(getattr(stock,'metadata_updates',()))) or 0)
    artwork=int(getattr(stock,'catalog_artwork_track_count',0) or 0)
    copied=int(getattr(outcome,'copied_tracks',len(getattr(stock,'copies',()))) or 0)
    retained=int(getattr(outcome,'reused_tracks',getattr(stock,'reused_track_count',0)) or 0)
    playlists=len(getattr(stock,'catalog_playlist_updates',()))
    members={identity.key:identity.members for identity,_raw in request.source[1]}
    matched=[];unmatched=0;matched_total=0
    for row in stock.catalog_recognition:
        links={match.local_id for match in row.tracks}
        occurrences=members[row.playlist_key]
        count=sum(local in links for local in occurrences)
        matched_total+=count
        matched.append(f'{row.name}: matched {count} of {len(occurrences)} songs.')
        unmatched+=len(occurrences)-count
    details='\n'.join(matched)+f'\n{metadata} song metadata record(s) updated; {artwork} cover(s) updated.\n{copied} new audio file(s); {retained} existing audio file(s) retained.\n{playlists} playlist(s) updated.'
    if unmatched:details+=f'\n{unmatched} unmatched or unsupported song(s) were left unchanged.'
    notices=list(warnings);diagnostics=[]
    for obj in (stock,outcome,result):
        if obj is None:continue
        notices.extend(str(getattr(obj,key,'')) for key in ('shadow_warning','artwork_warning','catalog_warning') if getattr(obj,key,''))
        cleanup=tuple(map(str,getattr(obj,'cleanup_failures',())))
        if cleanup:notices.append('Some device files could not be removed:\n'+'\n'.join(cleanup))
        if getattr(obj,'catalog_notice',''):diagnostics.append(str(obj.catalog_notice))
    if diagnostics:details+='\n\n'+'\n'.join(dict.fromkeys(diagnostics))
    if notices:details+='\n\n'+'\n'.join(dict.fromkeys(notices))
    if not warnings and getattr(stock,'catalog_witness',None) is not None:
        getattr(host,'_stock_catalog_witness_cache',{})[request.device]=stock.catalog_witness
    title='iPod playlists synced' if result is not None else 'No Sync changes to save'
    summary=f' · {matched_total} songs'+(f' · {unmatched} skipped' if unmatched else '')
    def complete():
        host._device_completion_message(title,details,attention=bool(notices),summary=summary,issues=notices)
        host._flash_status(title + (' — needs attention' if notices else '')
            + summary)
    host._finalize_ipod_operation(request.device,complete)
