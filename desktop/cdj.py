"""Native CDJ planning/review/publication inside the shared device worker lane."""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass, replace
import threading

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .devices import device_facts, device_target_profile, human


def _cdj_publication_value(state, completed, fraction=0):
    index=min(max(0,completed),len(state['weights']))
    written=state['prefix'][index]
    if index<len(state['weights']):written+=state['weights'][index]*fraction
    return 750+round(220*written/max(1,state['prefix'][-1]))


def _cdj_progress(host, worker, state, stage, done, total, label):
    """Render measured CDJ work only while this worker owns its presentation."""
    if (getattr(host,'_cdj_worker',None) is not worker or getattr(host,'_shutting_down',False)
            or getattr(host,'_cancelling',False)):
        return
    if type(done)is not int or type(total)is not int or done<0 or total<0:return
    fraction=min(done,total)/total if total else 0
    byte_counts=False
    if stage=='CDJ checking':
        message='Checking USB files';value=650+round(100*fraction)
    elif stage=='CDJ saving':
        message='Saving USB update';value=750
    elif stage=='CDJ publishing':
        state['published']=done;state['publication_total']=total
        message='Updating USB';value=_cdj_publication_value(state,done)
    elif stage=='CDJ copying':
        song=label.startswith('Contents/')
        message=('Copying '+Path(label).name) if song else 'Writing USB library'
        value=_cdj_publication_value(state,state['published'],fraction)
        byte_counts=True
    elif stage=='CDJ finalizing':
        message='Saving USB library';value=970
    elif stage=='CDJ cleanup':
        message='Finishing USB update';value=980+round(10*fraction)
    elif stage=='CDJ finished':
        message='Finishing USB update';value=990
    else:return
    state['value']=max(state['value'],min(990,value))
    host._set_device_progress(state['value'])
    host._start_pulse(message,keep_count=True)
    host._set_status_count(done,total,byte_counts=byte_counts)
    units=(human(done)+' / '+human(total)) if byte_counts else (str(done)+' / '+str(total))
    host.status_label.setToolTip(message+('\n'+Path(label).name if label.startswith('Contents/') else '')
                                 +('\n'+units if total else ''))


def _source_edits_pending(host):
    controller=getattr(host,'playlist_controller',None)
    imports=getattr(host,'shared_import_controller',None)
    analysis=getattr(host,'native_analysis_controller',None)
    prep=getattr(host,'prep_controller',None)
    media=getattr(host,'media_controller',None)
    definitions=getattr(host,'tag_definition_controller',None)
    metadata=getattr(host,'metadata_controller',None)
    return bool(host.tag_controller.owned or controller and
                (controller.pending or controller.queue_error()) or imports and
                (imports.pending or imports.queue_error()) or analysis and analysis.pending or prep and
                (prep.pending or getattr(prep,'admission_pending',False) or getattr(prep,'admission_error',''))
                or media and (media.scan_again or media.tasks.pending or any(not j['error'] for j in media.pending))
                or definitions and (definitions.pending or definitions.queue_error())
                or metadata and (metadata.pending or metadata.queue_error() or getattr(metadata,'migration_pending',False))
                or getattr(host.tag_controller,'pending',[])
                or getattr(host.tag_controller,'queue_error',lambda:'')()
                or getattr(host,'settings',{}).get('analysis_jobs')
                or getattr(host,'_analysis_worker',None) is not None
                or getattr(getattr(host,'_analysis_owner',None),'pending',0))


def _source_edits_error(host):
    """A saved refusal needs attention; polling cannot finish that generation."""
    for name in ('metadata_controller','playlist_controller','shared_import_controller',
                 'tag_definition_controller','prep_controller','tag_controller','native_analysis_controller'):
        controller=getattr(host,name,None)
        if controller is None:continue
        error=getattr(controller,'queue_error',lambda:'')()
        if isinstance(error,str) and error:return error
        pending=getattr(controller,'pending',None)
        if isinstance(pending,list):
            error=next((job.get('error') for job in pending if isinstance(job,dict) and job.get('error')),'')
            if isinstance(error,str) and error:return error
    metadata=getattr(host,'metadata_controller',None)
    if metadata and (metadata.pending or getattr(metadata,'migration_pending',False)):
        if getattr(metadata,'background_paused',False):
            return 'Background song updates are paused; resume them before exporting'
        reason=getattr(metadata,'reason',lambda:'')()
        from .metadata_controller import EDITING_DISABLED_REASON
        if reason==EDITING_DISABLED_REASON:
            return 'Title and artist updates cannot be saved to this library yet'
        if isinstance(reason,str) and reason.startswith('Switch to the original library'):
            return reason
    return ''


def _fail_source_export(host,error,*,waiting_attr=None,dev=None):
    if waiting_attr:setattr(host,waiting_attr,None)
    # A source wait owns no device. Another command may now own the progress UI.
    if not host._device_operation_owned():
        host._stop_pulse();host.cancel_btn.setVisible(False)
    host._flash_status('Export blocked — '+error,20)
    if dev is not None and getattr(host,'_export_batch',None):
        host._export_target_result(dev,RuntimeError(error));host._maybe_finish_export_target()


class CdjWorker(QThread):
    progress = Signal(int, int, str)
    detail_progress = Signal(str, object, object, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.result = None
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def run(self):
        from soundbox.operation_control import operation_scope
        try:
            with operation_scope(self.stop,self.detail_progress.emit):
                self.result = self.operation(self.stop.is_set, self.progress.emit)
        except Exception as exc:
            self.result = exc


def _delete_playlists(native):
    rows={}
    for row in native.playlists:
        identity=str(row.id)
        if identity in rows:raise ValueError('USB playlist identities are ambiguous; refresh the USB')
        rows[identity]=row
    result={}
    for identity,row in rows.items():
        if row.folder or identity in {'','0','root'}:continue
        names=[row.title];parent=str(row.parent);seen={identity}
        while parent not in {'','0','root'}:
            if parent in seen or parent not in rows or not rows[parent].folder:
                raise ValueError('USB playlist folders are ambiguous; refresh the USB')
            seen.add(parent);names.insert(0,rows[parent].title);parent=str(rows[parent].parent)
        result[identity]=' / '.join(names)
    return result


class CdjDeleteChoices(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Delete USB playlists');self.resize(440,360)
        layout=QVBoxLayout(self);layout.addWidget(QLabel('Choose playlists to delete from the USB.'))
        self.tree=QTreeWidget();self.tree.setHeaderLabel('Playlist');layout.addWidget(self.tree)
        for identity,title in rows.items():
            item=QTreeWidgetItem([title]);item.setData(0,Qt.UserRole,identity)
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(0,Qt.Unchecked)
            self.tree.addTopLevelItem(item)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Continue')
        buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.tree.itemChanged.connect(lambda *_:buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.playlist_ids)))
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    @property
    def playlist_ids(self):
        return tuple(str(self.tree.topLevelItem(at).data(0,Qt.UserRole))
                     for at in range(self.tree.topLevelItemCount())
                     if self.tree.topLevelItem(at).checkState(0)==Qt.Checked)


def _delete_scope(host):
    from soundbox.export_snapshot import source_paths
    library=host.lib
    repository=getattr(host,'_state_repository',None)
    generation=(repository,repository.generation,repository.area.generation) if repository is not None else None
    return ((str(library.source),library.source_kind,library.source_library_id),
            frozenset(source_paths(library)),generation)


@dataclass(frozen=True)
class CdjDeleteRequest:
    device: str
    token: int
    volume_uuid: str
    role: str
    source: tuple
    selection: tuple | None

    def current(self,host):
        try:
            facts=device_facts(self.device,cached_only=True)
            profile=device_target_profile(host._device_profiles.get(self.device),
                                          '' if self.role=='generic_usb' else self.role)
            return (not host._shutting_down and not getattr(host,'_state_maintenance',False)
                and host.lib is not None and profile is not None
                and profile.kind in {'generic_usb','ipod_usb'} and profile.cdj_export
                and facts.writable and facts.volume_uuid==self.volume_uuid
                and host._device_mount_tokens.get(self.device,0)==self.token
                and _delete_scope(host)==self.source
                and (self.selection is None or self.selection==(
                    getattr(host,'_current_device_path',''),getattr(host,'_current_device_role',''))))
        except (AttributeError,OSError,ValueError,TypeError):return False


def _initialize_scope(host):
    # Empty device setup uses no song metadata or native write authority. Only
    # source containment and restore/source transitions can change its scope.
    from soundbox.export_snapshot import source_paths
    library=host.lib
    repository=getattr(host,'_state_repository',None)
    return ((str(library.source),library.source_kind,library.source_library_id),
            frozenset(source_paths(library)),
            (repository,repository.area.generation) if repository is not None else None)


@dataclass(frozen=True)
class CdjInitializeRequest:
    device: str
    token: int
    volume_uuid: str
    role: str
    mount: object
    source: tuple

    def current(self,host):
        try:
            facts=device_facts(self.device,cached_only=True)
            profile=device_target_profile(host._device_profiles.get(self.device),
                                          '' if self.role=='generic_usb' else self.role)
            return (not host._shutting_down and not getattr(host,'_state_maintenance',False)
                and not getattr(host,'_export_batch',None)
                and host.lib is not None and profile is not None and profile.cdj_export
                and profile.kind in {'generic_usb','ipod_usb'} and facts.writable
                and facts.volume_uuid==self.volume_uuid
                and host._device_mount_tokens.get(self.device,0)==self.token
                and host._device_coordinator.mounts.get(self.device)==self.mount
                and _initialize_scope(host)==self.source)
        except (AttributeError,OSError,ValueError,TypeError):return False


class CdjDeviceMixin:
    def _begin_cdj_initialize(self,dev,*,target_role='generic_usb',expected_setup):
        """Configure an explicitly accepted empty CDJ library; never format."""
        if self._shutting_down or self.lib is None:return
        facts=device_facts(dev,cached_only=True);token=self._device_mount_tokens.get(dev,0)
        mount=self._device_coordinator.mounts.get(dev)
        request=CdjInitializeRequest(dev,token,facts.volume_uuid,target_role,mount,_initialize_scope(self))
        if (not token or not mount or not facts.volume_uuid or not request.current(self)
                or getattr(expected_setup,'state','')!='missing'
                or str(expected_setup.target)!=dev or expected_setup.volume_uuid!=facts.volume_uuid
                or expected_setup.target_role!=target_role
                or not expected_setup.mount_identity or expected_setup.mount_identity[0]!=token):
            self._flash_status('USB changed; reconnect it to configure CDJ export',15);return
        def deferred():
            if not request.current(self):
                self._flash_status('USB or library changed; reconnect the USB to configure it',15);return
            self._begin_cdj_initialize(dev,target_role=target_role,expected_setup=expected_setup)
        if self._defer_device_operation_until_scan_finishes(deferred,target=dev):return
        if self._device_operation_owned(dev):
            self._flash_status('USB is busy; wait for the current device operation',15);return
        from soundbox import cdj_export
        from soundbox.operation_control import OperationCancelled
        sources=tuple(request.source[1]);profile=deepcopy(self.settings.get('cdj_my_settings',{}))
        def prepare(cancel,_progress):
            if cancel() or not request.current(self):raise OperationCancelled('USB configuration cancelled')
            return cdj_export.plan_initialize(dev,token=token,volume_uuid=facts.volume_uuid,
                sources=sources,profile=profile,name=Path(dev).name,
                target_role=target_role,expected_setup=expected_setup,
                recovery_root=self._data_file.parent/'cdj-recovery',should_cancel=cancel)
        self._release_eject_ready();self._exporting=True;self._cdj_plan=None
        worker=CdjWorker(prepare);self._cdj_worker=worker
        worker.progress.connect(lambda done,total,_label:self._set_device_progress(round(650*done/max(1,total))))
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,
            lambda result:self._review_cdj_plan(dev,result,w.stop.is_set(),intent='initialize',
                                               request_current=lambda:request.current(self))))
        self._set_device_progress(0);self.progress.setVisible(True);self.cancel_btn.setVisible(True)
        self._start_pulse('Preparing CDJ USB configuration')
        self._start_device_worker(worker,dev,'plan-cdj-initialize');self._sync_cue_editing()

    def _begin_cdj_delete(self, dev, playlist_ids=None, *, target_role='generic_usb'):
        from soundbox.operation_control import OperationCancelled
        if self._shutting_down or self.lib is None:return
        selected=None if playlist_ids is None else tuple(str(value) for value in playlist_ids)
        if selected is not None and (not selected or len(set(selected))!=len(selected)):
            self._flash_status('Select USB playlists to delete',10);return
        facts=device_facts(dev,cached_only=True);token=self._device_mount_tokens.get(dev,0)
        origin=((dev,getattr(self,'_current_device_role',''))
                if getattr(self,'_current_device_path','')==dev else None)
        request=CdjDeleteRequest(dev,token,facts.volume_uuid,target_role,_delete_scope(self),origin)
        if not token or not facts.volume_uuid or not request.current(self):
            self._flash_status('Select a writable FAT32 or exFAT USB',15);return
        def deferred():
            if not request.current(self):
                self._flash_status('USB, source or selected device role changed; choose the playlists again',15)
                return
            self._begin_cdj_delete(dev,selected,target_role=target_role)
        if self._defer_device_operation_until_scan_finishes(deferred,target=dev):return
        if self._device_operation_owned(dev):
            QMessageBox.information(self,'Device busy','Wait for the current device operation.');return
        cached=self._usb_view_cache.get(dev)
        expected=(dict(cached.native.files) if selected is not None and cached is not None
                  and getattr(cached,'native',None) is not None and not getattr(cached,'error','')
                  and not getattr(cached,'notice','') and cached.mount_token==token
                  and str(cached.target)==dev and cached.facts.volume_uuid==facts.volume_uuid else None)
        def finish(result=None,**options):self._finish_cdj(dev,result,intent='delete',**options)
        def current():return request.current(self)
        def stale():return OperationCancelled('USB, source or selected device role changed; choose the playlists again')
        def read(cancel,_progress):
            from .usb_device_view import read_native_usb_snapshot
            if cancel() or not current():raise stale()
            result=read_native_usb_snapshot(dev,token,facts)
            if cancel() or not current():raise stale()
            return result
        def reviewed(snapshot):
            if isinstance(snapshot,Exception):finish(snapshot);return
            if not current():finish(stale());return
            try:
                if snapshot.error:raise ValueError(snapshot.error)
                if (str(snapshot.target)!=dev or snapshot.mount_token!=token
                        or snapshot.facts.volume_uuid!=facts.volume_uuid):
                    raise ValueError('The USB identity changed while reading its playlists')
                if snapshot.native is None:raise ValueError('No native USB playlists are available to delete')
                native=snapshot.native;rows=_delete_playlists(native)
                files=dict(native.files)
                if expected is not None and files!=expected:
                    raise ValueError('USB catalog changed after selection; refresh and choose the playlists again')
                ids=selected
                if ids is None:
                    if not rows:raise ValueError('No USB playlists are available to delete')
                    dialog=CdjDeleteChoices(rows,self)
                    try:ids=dialog.playlist_ids if dialog.exec()==QDialog.DialogCode.Accepted else ()
                    finally:dialog.deleteLater()
                if not ids:finish(OperationCancelled('USB playlist delete cancelled'));return
                if len(set(ids))!=len(ids) or any(identity not in rows for identity in ids):
                    raise ValueError('The selected USB playlists changed; refresh the USB')
                if not current():finish(stale());return
                names='\n'.join(rows[identity] for identity in ids[:12])
                if len(ids)>12:names+=f'\n…and {len(ids)-12} more'
                answer=QMessageBox.question(self,'Delete USB playlists?',
                    'Delete these playlists from the USB?\n\n'+names,
                    QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
                if answer!=QMessageBox.Yes:finish(OperationCancelled('USB playlist delete cancelled'));return
                if not current():finish(stale());return
                from soundbox import cdj_export
                recovery=self._data_file.parent/'cdj-recovery'
                def prepare(cancel,_progress):
                    if cancel() or not current():raise stale()
                    return cdj_export.plan_playlist_delete(dev,ids,token=token,volume_uuid=facts.volume_uuid,
                        target_role=target_role,sources=tuple(request.source[1]),expected_catalog_files=files,
                        recovery_root=recovery,should_cancel=cancel)
                start(prepare,lambda value,cancelled:self._review_cdj_plan(dev,value,cancelled,
                    intent='delete',request_current=current),'Preparing playlist delete','plan-cdj-delete')
            except Exception as exc:finish(exc)
        def start(operation,complete,label,intent):
            worker=CdjWorker(operation);self._cdj_worker=worker
            worker.progress.connect(lambda done,total,_label:self._set_device_progress(round(650*done/max(1,total))))
            worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,
                lambda value:complete(value,w.stop.is_set())))
            self._start_pulse(label);self._start_device_worker(worker,dev,intent)
        self._release_eject_ready();self._exporting=True;self._cdj_plan=None
        self._set_device_progress(0);self.progress.setVisible(True);self.cancel_btn.setVisible(True)
        start(read,lambda value,cancelled:finish(OperationCancelled('USB playlist delete cancelled'))
              if cancelled else reviewed(value),'Reading USB playlists','inspect-cdj-delete')
        self._sync_cue_editing()

    def _begin_cdj_export(self, dev, playlists=(), *, settings=None, from_drop=False, drop_target=None,
                          standalone=(), target_role='generic_usb'):
        from soundbox import cdj_export
        if self._shutting_down or self.lib is None:
            return
        if from_drop and drop_target is None:
            drop_target = (self._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid)
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._begin_cdj_export(dev, playlists, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone,target_role=target_role),target=dev):
            return
        if self._device_operation_owned(dev):
            QMessageBox.information(self, 'Device busy', 'Wait for the current device operation.')
            return
        error=_source_edits_error(self)
        if error:
            _fail_source_export(self,error,waiting_attr='_cdj_waiting',dev=dev)
            return
        # Accepted source edits drain before freezing a generation; a timer keeps
        # Qt delivery and the native player alive while that FIFO finishes.
        if _source_edits_pending(self):
            native_ids={p.pid for p in getattr(self.lib,'playlists',())}
            selected_native={getattr(p,'pid',None) for p in playlists} & native_ids
            native_nodes={row['ID']:row for row in getattr(self.lib,'source_playlist_catalog',{}).get('nodes',[])}
            selected_uuids={identity:native_nodes[identity]['UUID'] for identity in selected_native
                            if identity in native_nodes and native_nodes[identity].get('UUID')}
            if (getattr(self.lib,'source_kind','')=='rekordbox-db' and
                    (set(selected_uuids)!=selected_native or any(native_nodes[identity].get('rb_local_deleted')!=0
                                                               for identity in selected_native))):
                self._flash_status('Refresh the selected playlist identities before exporting',15)
                return
            source_binding=(str(self.lib.source),self.lib.source_library_id) if hasattr(self.lib,'source') else None
            request = (dev, tuple(playlists), settings, from_drop, drop_target)
            self._cdj_waiting = request
            self._start_pulse('Waiting for source edits')
            self.cancel_btn.setVisible(True)
            def ready():
                if self._shutting_down or self._cdj_waiting is not request:
                    return
                error=_source_edits_error(self)
                if error:
                    _fail_source_export(self,error,waiting_attr='_cdj_waiting',dev=dev)
                    return
                if _source_edits_pending(self):
                    QTimer.singleShot(100, ready)
                    return
                self._cdj_waiting = None
                selected=playlists
                fresh={p.pid:p for p in getattr(self.lib,'playlists',())}
                fresh_nodes={row['ID']:row for row in getattr(self.lib,'source_playlist_catalog',{}).get('nodes',[])}
                if (source_binding is not None and source_binding!=(str(self.lib.source),self.lib.source_library_id)
                        or selected_native-fresh.keys() or any(identity not in fresh_nodes
                            or fresh_nodes[identity].get('UUID')!=uuid or fresh_nodes[identity].get('rb_local_deleted')!=0
                            for identity,uuid in selected_uuids.items())):
                    self._stop_pulse();self.cancel_btn.setVisible(False)
                    self._flash_status('Library or selected playlist changed; choose the playlist again',15)
                    if getattr(self,'_export_batch',None):
                        self._export_target_result(dev,RuntimeError('Library or selected playlist changed'))
                        self._maybe_finish_export_target()
                    return
                if selected_native:
                    selected=[replace(fresh[p.pid],export_key=p.export_key) if p.pid in selected_native else p
                              for p in playlists]
                self._begin_cdj_export(dev, selected, settings=settings, from_drop=from_drop,drop_target=drop_target,standalone=standalone,target_role=target_role)
                if getattr(self,'_export_batch',None):self._maybe_finish_export_target()
            QTimer.singleShot(100, ready)
            return
        profile = device_target_profile(self._device_profiles.get(dev),'' if target_role=='generic_usb' else target_role)
        facts = device_facts(dev, cached_only=True)
        token = self._device_mount_tokens.get(dev, 0)
        if from_drop and drop_target != (token,facts.volume_uuid):
            self._flash_status('USB changed; choose the connected target again' if standalone else
                               'USB changed after the drop; drop the playlist onto the connected device again',15)
            if getattr(self,'_export_batch',None):self._export_target_result(dev,RuntimeError('USB changed; select the connected device again'))
            return
        if not profile or profile.kind not in {'generic_usb','ipod_usb'} or not getattr(profile,'cdj_export',profile.rockbox_export) or not facts.writable or not facts.volume_uuid:
            QMessageBox.warning(self, 'CDJ export unavailable', 'Select a writable FAT32 or exFAT USB.')
            return
        self._release_eject_ready()
        self._exporting = True
        self._cdj_plan = None
        recovery = self._data_file.parent/'cdj-recovery'
        if settings is None:
            from soundbox.export_snapshot import capture_export
            captured = capture_export(self.lib, playlists, standalone=standalone,
                                      titles=self.titles, artists=self.overrides,
                                      complete=not playlists and not standalone)
            # Additive publication touches only this explicit selection.
            all_playlists = ()
            global_profile = deepcopy(self.settings.get('cdj_my_settings',{}))
            def operation(cancel, progress):
                inputs = captured.materialize()
                return cdj_export.plan_export(
                    inputs.library, inputs.playlists, dev, token=token, volume_uuid=facts.volume_uuid,
                    all_playlists=all_playlists, titles=inputs.titles, artists=inputs.artists, art_cache=self.art_cache,
                    should_cancel=cancel, on_progress=progress, recovery_root=recovery, additive=True,
                    global_profile=global_profile, standalone=inputs.standalone,
                    **({'target_role':target_role} if target_role!='generic_usb' else {}))
        else:
            from soundbox.export_snapshot import source_paths
            changes = dict(settings)
            # Settings need source separation, not detached song/cue/catalog
            # data. Capture those paths now; later library edits cannot alter
            # the worker's original overlap boundary.
            sources = list(source_paths(self.lib))
            operation = lambda cancel, _progress: cdj_export.plan_settings(
                dev, changes, token=token, volume_uuid=facts.volume_uuid,
                sources=sources,
                should_cancel=cancel, recovery_root=recovery,
                **({'target_role':target_role} if target_role!='generic_usb' else {}))
        worker = CdjWorker(operation)
        self._cdj_worker = worker
        worker.progress.connect(lambda done, total, label: self._set_device_progress(
            round(650*done/max(1, total))))
        worker.finished.connect(lambda w=worker: self._finish_device_result_worker(
            '_cdj_worker', w, lambda result: self._review_cdj_plan(dev, result, w.stop.is_set(), from_drop=from_drop)))
        self._set_device_progress(0)
        self.progress.setVisible(True)
        self._start_pulse('Preparing USB settings' if settings is not None else 'Preparing CDJ export')
        self._start_device_worker(worker, dev, 'plan-cdj')
        self._sync_cue_editing()

    def _review_cdj_plan(self, dev, result, cancelled=False, *, from_drop=False,
                         intent='export', request_current=None):
        from soundbox import cdj_transaction
        from soundbox.operation_control import OperationCancelled
        def finish(value,**options):
            if intent!='export':self._finish_cdj(dev,value,intent=intent,**options)
            else:self._finish_cdj(dev,value,**options)
        if isinstance(result, Exception):
            finish(result)
            return
        if cancelled or request_current is not None and not request_current():
            result.close()
            finish(OperationCancelled('USB playlist delete cancelled' if intent=='delete' else 'CDJ preparation cancelled'))
            return
        plan = result
        overwrite=tuple(getattr(plan,'overwritten_playlists',()))
        if overwrite:
            names='\n'.join(overwrite[:12])
            if len(overwrite)>12:names+=f'\n…and {len(overwrite)-12} more'
            answer=QMessageBox.question(self,'Overwrite USB playlists?',
                'These playlists are already on the USB:\n\n'+names+
                '\n\nExport will replace them with the selected versions.',
                QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)
            if answer!=QMessageBox.Yes:
                plan.close()
                finish(OperationCancelled('USB playlist overwrite cancelled'))
                return
        if not getattr(plan,'requires_commit',bool(plan.changes)):
            plan.close()
            finish(None, unchanged=True)
            return
        def publish(cancel, progress):
            try:
                if request_current is not None and not request_current():
                    raise OperationCancelled('USB, source or selected device role changed before publication')
                return cdj_transaction.commit(plan, should_cancel=cancel, on_progress=progress)
            finally:
                plan.close()
        worker = CdjWorker(publish)
        self._cdj_worker = worker
        weights=tuple(max(1,getattr(change,'size',0)) for change in plan.changes)
        prefix=[0]
        for weight in weights:prefix.append(prefix[-1]+weight)
        state={'value':650,'published':0,'publication_total':len(plan.changes),
               'weights':weights,'prefix':tuple(prefix)}
        def file_progress(done,total,_label):
            if self._cdj_worker is not worker or getattr(self,'_cancelling',False):return
            state['value']=max(state['value'],_cdj_publication_value(state,done))
            self._set_device_progress(state['value'])
        worker.progress.connect(file_progress)
        worker.detail_progress.connect(lambda stage,done,total,label:
            _cdj_progress(self,worker,state,stage,done,total,label))
        worker.finished.connect(lambda w=worker: self._finish_device_result_worker(
            '_cdj_worker', w, finish))
        self._start_pulse('Deleting USB playlists' if intent=='delete' else
                          'Configuring CDJ USB' if intent=='initialize' else 'Exporting CDJ USB')
        self._set_device_progress(650)
        self._start_device_worker(worker, dev, 'commit-cdj-delete' if intent=='delete' else
                                  'commit-cdj-initialize' if intent=='initialize' else 'commit-cdj')

    def _finish_cdj(self, dev, result, *, cancelled=False, unchanged=False, intent='export'):
        from soundbox.cdj_transaction import CommittedCleanupPending
        from soundbox.operation_control import OperationCancelled
        saved_warning = isinstance(result, CommittedCleanupPending)
        cancelled = cancelled or isinstance(result, OperationCancelled)
        cleanup_cancelled = saved_warning and result.cleanup_cancelled
        self._exporting = False
        self._cancelling = False
        self._stop_pulse()
        self.cancel_btn.setVisible(False)
        if not cancelled and not isinstance(result,Exception):self.progress.setValue(1000)
        self.progress.setVisible(False)
        self._sync_cue_editing()
        if getattr(self,'_export_batch',None):self._export_target_result(dev,result)
        if saved_warning:
            if not cleanup_cancelled and not getattr(self,'_export_batch',None):
                QMessageBox.warning(self, 'Playlist delete saved — cleanup pending' if intent=='delete' else
                                    'USB configured — cleanup pending' if intent=='initialize' else 'CDJ export saved — cleanup pending', str(result))
            self._flash_status('Playlist delete saved — space cleanup pending' if intent=='delete' else
                               'USB configured — space cleanup pending' if intent=='initialize' else 'Playlist export saved — space cleanup pending', 15)
        elif cancelled:
            from .export_wait import retain_source_cancellation
            if not retain_source_cancellation(self):
                self._flash_status('Playlist delete cancelled — previous library kept' if intent=='delete' else
                                   'USB configuration cancelled' if intent=='initialize' else 'CDJ export cancelled — previous library kept', 15)
        elif isinstance(result, Exception):
            if not getattr(self,'_export_batch',None):QMessageBox.warning(self, 'CDJ USB operation stopped', str(result))
            self._flash_status('CDJ USB operation stopped', 15)
        else:
            self._flash_status(('No USB playlist changes needed' if unchanged else 'Playlist delete complete ✓')
                               if intent=='delete' else
                               ('USB already configured' if unchanged else 'USB configured for CDJ export ✓')
                               if intent=='initialize' else
                               ('Selected playlists already exported' if unchanged else 'Playlist export complete ✓'), 15)
        # The committed export is the result. Invalidate displayed catalog
        # rows without a final USB read, retaining the user's settings drafts.
        # A post-commit cleanup warning still changed the USB catalogs.
        # Pre-commit failures/unchanged exports keep their previous presentation.
        if (saved_warning or not isinstance(result, Exception)) and not cancelled and not unchanged:
            from dataclasses import replace
            from .usb_device_view import UsbSnapshot
            previous = self._usb_view_cache.get(dev)
            notice = ('USB updated. Space cleanup is pending; export again to finish.' if saved_warning else
                      'USB updated. Select a tab to browse its contents.')
            snapshot = (replace(previous, playlists=(), error='', notice=notice)
                        if isinstance(previous, UsbSnapshot) else
                        UsbSnapshot(Path(dev), self._device_mount_tokens.get(dev, 0),
                                    device_facts(dev, cached_only=True), notice=notice))
            self._usb_view_cache[dev] = snapshot
            self._usb_view_requests.discard(dev)
            current_profile=device_target_profile(getattr(self,'_device_profiles',{}).get(dev),
                                                  getattr(self,'_current_device_role',''))
            if dev == self._current_device_path and getattr(current_profile,'kind','') in {'generic_usb','ipod_usb'}:
                self._present_usb_device(dev)
        self._update_device_action_state()
        if getattr(self, '_usb_format_device', ''):
            self._show_usb_format_status()

    def _apply_usb_settings(self, changes):
        dev = self._current_device_path
        if dev:
            self._begin_cdj_export(dev, settings=changes,
                target_role=getattr(self,'_current_device_role','') or 'generic_usb')

    def _sync_usb_my_settings(self, snapshot):
        from soundbox import my_settings
        if getattr(snapshot, 'notice', ''):
            return
        native = snapshot.native if snapshot else None
        current = my_settings.values(native.files) if native else {}
        if current and not self.settings.get('cdj_my_settings'):
            self.settings['cdj_my_settings'] = current
            if not self._save_state(invalidate_views=False):
                self.settings.pop('cdj_my_settings',None)
                return
        profile = self.settings.get('cdj_my_settings',{})
        pages = vars(self).get('usb_device_pages')
        if pages is not None:
            pages.global_profile = deepcopy(profile)
            editor = pages.my_settings
            if editor.profile!=current or editor.global_profile!=profile:
                editor.set_profile(current,profile,preserve_edits=True)
        editor = getattr(self, '_global_my_settings', None)
        if editor is not None and editor.profile!=profile:
            editor.set_profile(profile)

    def _save_global_my_settings(self, profile):
        from soundbox import my_settings
        profile = my_settings.validate(profile)
        previous = self.settings.get('cdj_my_settings',{})
        self.settings['cdj_my_settings'] = deepcopy(profile)
        if not self._save_state(invalidate_views=False):
            self.settings['cdj_my_settings'] = previous
            return
        editor = getattr(self, '_global_my_settings', None)
        if editor is not None:
            editor.set_profile(profile)
        pages = vars(self).get('usb_device_pages')
        if pages is not None:
            pages.global_profile = deepcopy(profile)
            native = getattr(pages._snapshot,'native',None)
            pages.my_settings.set_profile(my_settings.values(native.files) if native else {},profile)
        self._flash_status('Global My Settings saved')

    def _promote_usb_settings(self, profile):
        merged = deepcopy(self.settings.get('cdj_my_settings',{}))
        for name,fields in profile.items():
            merged.setdefault(name,{}).update(fields)
        self._save_global_my_settings(merged)

    def _load_global_usb_settings(self):
        from soundbox import my_settings
        native = getattr(self.usb_device_pages._snapshot,'native',None)
        if native is None:return
        current = my_settings.values(native.files)
        changes = {name:{key:self.settings['cdj_my_settings'][name][key] for key in fields
                   if key in self.settings.get('cdj_my_settings',{}).get(name,{})}
                   for name,fields in current.items()}
        self._apply_usb_settings({'my_settings':changes})

    def _import_usb_my_settings(self, dev):
        if self._defer_device_operation_until_scan_finishes(lambda:self._import_usb_my_settings(dev)):return
        if self._device_operation_owned():return
        from soundbox import my_settings
        from soundbox.cdj_usb import read_profiles
        token=self._device_mount_tokens.get(dev,0)
        self._exporting=True;self._release_eject_ready()
        worker=CdjWorker(lambda _cancel,_progress:my_settings.values(read_profiles(dev,token)))
        self._cdj_worker=worker
        def imported(result):
            self._exporting=False;self._stop_pulse()
            if isinstance(result,Exception):self._finish_cdj(dev,result);return
            if not result:self._flash_status('No supported My Settings on this USB');return
            self._promote_usb_settings(result)
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,imported))
        self._start_pulse('Importing My Settings');self._start_device_worker(worker,dev,'inspect')

    def _reformat_usb(self, dev):
        from soundbox import cdj_export, cdj_transaction
        if self._defer_device_operation_until_scan_finishes(lambda:self._reformat_usb(dev)):return
        if self._device_operation_owned():return
        from soundbox import usb_format
        profile=self._device_profiles.get(dev)
        if profile is None or profile.kind!='generic_usb':return
        facts=device_facts(dev,cached_only=True);token=self._device_mount_tokens.get(dev,0)
        sources=[Path(self.lib.source),*[t.path for t in self.lib.tracks_by_id.values()]] if self.lib else []
        if self.lib:sources.extend(Path(t.source_analysis_path) for t in self.lib.tracks_by_id.values() if t.source_analysis_path)
        self._usb_format_device=dev
        self._usb_format_volumes=(dev,)
        self._usb_format_started=False
        self._usb_format_original_text=''
        self._release_eject_ready()
        worker=CdjWorker(lambda _cancel,_progress:usb_format.capture(dev,token=token,volume_uuid=facts.volume_uuid,sources=sources))
        def show(result):
            if isinstance(result,Exception):self._finish_usb_format(dev,result);return
            self._stop_pulse()
            self._usb_format_volumes=result.volumes
            from .usb_preferences import FormatUsbDialog
            dialog=FormatUsbDialog(result,self)
            try:
                accepted=dialog.exec()==QDialog.DialogCode.Accepted
                choice=dialog.choice if accepted else None
            finally:
                dialog.deleteLater()
            if not accepted or self._shutting_down:
                self._finish_usb_format(dev,None,cancelled=True);return
            global_profile=deepcopy(self.settings.get('cdj_my_settings',{}))
            format_name,format_filesystem=choice
            recovery=self._data_file.parent/'cdj-recovery'
            def format_and_initialize(cancel,progress):
                info=usb_format.format_usb(result,format_name,format_filesystem,sources=sources,
                    should_cancel=cancel,on_progress=progress)
                path=Path(info['MountPoint'])
                progress(0,0,'Initializing USB')
                plan=cdj_export.plan_initialize(path,token=path.stat().st_dev,volume_uuid=info['VolumeUUID'],
                    sources=sources,profile=global_profile,name=info['VolumeName'],recovery_root=recovery,
                    should_cancel=cancel)
                try:cdj_transaction.commit(plan)
                finally:plan.close()
                return str(path)
            next_worker=CdjWorker(format_and_initialize)
            self._usb_format_started=True
            self._start_usb_format_worker(next_worker,dev,
                lambda value:self._finish_usb_format(dev,value),'Formatting USB')
        self._start_usb_format_worker(worker,dev,show,'Reading USB')

    def _start_usb_format_worker(self,worker,dev,callback,label):
        """Own the erase separately so another drive can use the export lane."""
        self._usb_format_worker=worker
        worker.progress.connect(lambda _done,_total,text:
            self._usb_format_progress(dev,text) if self._usb_format_worker is worker else None)
        def finished():
            worker.wait()
            if self._usb_format_worker is not worker:return
            self._usb_format_worker=None
            worker.deleteLater()
            if self._shutting_down:
                self._usb_format_device=''
                return
            try:
                callback(worker.result)
            except Exception as exc:
                self._finish_usb_format(dev,exc)
        worker.finished.connect(finished)
        self._usb_format_progress(dev,label)
        try:
            worker.start()
        except Exception as exc:
            self._usb_format_worker=None
            worker.deleteLater()
            self._finish_usb_format(dev,exc)
            return
        self._update_device_action_state()

    def _usb_format_progress(self,dev,label):
        if dev!=getattr(self,'_usb_format_device','') or self._shutting_down:return
        native=(('Started erase','Formatting USB'),('Unmounting','Unmounting USB'),
                ('Erasing','Erasing USB'),('Mounting','Mounting USB'),
                ('Finished erase','Finishing USB format'),('Creating the partition map','Preparing USB'),
                ('Waiting for partitions','Preparing USB'),('Formatting disk','Formatting USB'))
        stages={'Reading USB','Checking USB','Formatting USB','Erasing USB',
                'Finishing USB format','Initializing USB'}
        if label not in stages:
            label=next((stage for prefix,stage in native if label.startswith(prefix)),'')
            if not label:return
        self._usb_format_label=label
        self._show_usb_format_status()

    def _show_usb_format_status(self):
        dev=getattr(self,'_usb_format_device','')
        if not dev:return
        row=self._usb_device_rows.get(dev)
        name=row[0] if row else Path(dev).name
        for at in range(self.device_tree.topLevelItemCount()):
            item=self.device_tree.topLevelItem(at)
            from .trees import DEVPATH_ROLE
            if item.data(0,DEVPATH_ROLE)==dev:
                if not self._usb_format_original_text:self._usb_format_original_text=item.text(0)
                item.setText(0,f'{name} · {self._usb_format_label}…')
        # A simultaneous export owns the footer and its Cancel control.
        if ((not getattr(self,'_usb_format_started',False) or not self._device_operation_owned(''))
                and not getattr(self,'_export_batch',None)):
            self._start_pulse(f'{self._usb_format_label} · {name}')
            self.cancel_btn.setVisible(False)

    def _finish_usb_format(self,dev,result,*,cancelled=False):
        self._usb_format_device=''
        idle=not self._device_operation_owned('') and not getattr(self,'_export_batch',None)
        if idle:self._stop_pulse()
        if isinstance(result,Exception):
            QMessageBox.warning(self,'USB format stopped',str(result))
            if idle:self._flash_status('USB format stopped',15)
        elif not cancelled and idle:
            self._flash_status('USB formatted · Global My Settings loaded',15)
        if getattr(self,'_usb_format_started',False):
            from .devices import forget_device_mount
            from .trees import DEVPATH_ROLE
            # Every old partition identity is retired, including an erase that
            # succeeded before initialization failed. Never offer stale targets
            # while another export holds discovery off.
            for volume in self._usb_format_volumes:
                forget_device_mount(volume)
                self._device_coordinator.disconnect(volume)
                for cache in (self._usb_view_cache,self._device_profiles,
                              self._device_mount_tokens,self._usb_device_rows):
                    cache.pop(volume,None)
                self._usb_view_requests.discard(volume)
                for at in range(self.device_tree.topLevelItemCount()-1,-1,-1):
                    if self.device_tree.topLevelItem(at).data(0,DEVPATH_ROLE)==volume:
                        self.device_tree.takeTopLevelItem(at)
                if self._current_device_path==volume:
                    self._current_device_path=''
                    self._current_device_role=''
                    self.stack.setCurrentIndex(0)
            self._device_mount_refresh_pending=True
        if cancelled or isinstance(result,Exception):
            row=self._usb_device_rows.get(dev)
            if row:
                from .trees import DEVPATH_ROLE
                for at in range(self.device_tree.topLevelItemCount()):
                    item=self.device_tree.topLevelItem(at)
                    if item.data(0,DEVPATH_ROLE)==dev:
                        item.setText(0,self._usb_format_original_text or row[0])
        self._update_device_action_state()
        self._sync_cue_editing()
        if getattr(self,'_device_mount_refresh_pending',False):QTimer.singleShot(0,self._refresh_devices)

    def _recover_cdj(self, dev, *, target_role='generic_usb'):
        from soundbox import cdj_transaction
        if self._device_operation_owned():
            return
        if self._defer_device_operation_until_scan_finishes(lambda:self._recover_cdj(dev,target_role=target_role)):
            return
        facts = device_facts(dev,cached_only=True)
        token = self._device_mount_tokens.get(dev,0)
        root = self._data_file.parent/'cdj-recovery'
        sources = [t.path for t in self.lib.tracks_by_id.values()] if self.lib else []
        self._exporting = True
        self._release_eject_ready()
        def inspect(_cancel,_progress):
            session = cdj_transaction.CdjSession(dev,sources,token=token,volume_uuid=facts.volume_uuid, target_role=target_role)
            return cdj_transaction.recover(session,root,dry_run=True)
        worker = CdjWorker(inspect)
        self._cdj_worker = worker
        def reviewed(result):
            if isinstance(result,Exception):
                self._finish_cdj(dev,result)
                return
            if result is None:
                self._finish_cdj(dev,None,unchanged=True)
                return
            text = (f'{Path(dev).name}\n\n'
                + ('Finish the already committed generation' if result['state']=='committed'
                   else 'Restore the previous library generation')
                + f" using its verified local receipt and {result['files']} file records?")
            if worker.stop.is_set() or QMessageBox.question(self,'Recover CDJ USB',text,
                    QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                self._finish_cdj(dev,None,cancelled=True)
                return
            def apply(_cancel,_progress):
                from soundbox.cdj_usb import digest
                session = cdj_transaction.CdjSession(dev,sources,token=token,volume_uuid=facts.volume_uuid, target_role=target_role)
                if session.mount_identity != tuple(result['mount_identity']):
                    raise ValueError('USB was remounted after recovery review')
                if digest(session.read_optional(cdj_transaction.JOURNAL,limit=8*1024*1024)) != result['journal_sha256']:
                    raise ValueError('USB recovery journal changed after review')
                cdj_transaction.recover(session,root)
            current = CdjWorker(apply)
            self._cdj_worker = current
            current.finished.connect(lambda w=current:self._finish_device_result_worker(
                '_cdj_worker',w,lambda value:self._finish_cdj(dev,value)))
            self._start_pulse('Recovering CDJ USB')
            self._start_device_worker(current,dev,'commit-cdj-recovery')
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,reviewed))
        self._start_pulse('Checking CDJ recovery')
        self._start_device_worker(worker,dev,'plan-cdj-recovery')

    def _save_device_views(self):
        saved = self.settings.get('device_views',{})
        saved = dict(saved) if isinstance(saved,dict) else {}
        saved['usb'] = deepcopy(self.usb_device_pages._states)
        self.settings['device_views'] = saved
        if not self._shutting_down:
            self._schedule_settings_save('device_views')

    def _remember_stock_device_view(self, *_):
        if getattr(self,'_restoring_stock_view',False) or getattr(self,'_current_device_role','')=='ipod_usb':
            return
        dev = self._current_device_path
        profile = self._device_profiles.get(dev)
        if not dev or not getattr(profile,'has_stock_ipod',False):
            return
        snapshot = self._stock_snapshot
        if snapshot is None or str(snapshot.target) != dev:
            return
        from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeWidget, QTreeWidgetItemIterator
        from PySide6.QtCore import Qt
        states = {}
        for view in self.stock_ipod_pages.findChildren(QAbstractItemView):
            if not isinstance(view,(QTableView,QTreeWidget)):
                continue
            key = view.objectName()
            if key == 'StockDeviceDetails':
                key += ':'+view.headerItem().text(0)
            selected = []
            index = view.currentIndex()
            while index.isValid():
                selected.insert(0,index.row())
                index = index.parent()
            states[key] = dict(scroll=view.verticalScrollBar().value(),selected=selected)
            if isinstance(view,QTreeWidget):
                expanded = []
                iterator = QTreeWidgetItemIterator(view)
                while iterator.value():
                    item = iterator.value()
                    if item.isExpanded():
                        expanded.append(str(item.data(0,Qt.UserRole)))
                    iterator += 1
                states[key]['expanded'] = expanded
        saved = dict(self.settings.get('device_views',{}))
        stock = dict(saved.get('stock',{}))
        stock[snapshot.identity_key or dev] = dict(page=self.stock_ipod_pages.stack.currentIndex(),views=states)
        saved['stock'] = stock
        self.settings['device_views'] = saved
        if not self._shutting_down:
            self._schedule_settings_save('device_views')

    def _restore_stock_device_view(self):
        snapshot = self._stock_snapshot
        if snapshot is None:
            return
        state = self.settings.get('device_views',{}).get('stock',{}).get(snapshot.identity_key or str(snapshot.target),{})
        if not state:
            return
        from PySide6.QtWidgets import QAbstractItemView, QTableView, QTreeWidget, QTreeWidgetItemIterator
        from PySide6.QtCore import Qt, QModelIndex
        self._set_stock_device_tab(max(0,min(2,int(state.get('page',0)))))
        for view in self.stock_ipod_pages.findChildren(QAbstractItemView):
            if not isinstance(view,(QTableView,QTreeWidget)):
                continue
            key = view.objectName()
            if key == 'StockDeviceDetails':
                key += ':'+view.headerItem().text(0)
            previous = state.get('views',{}).get(key,{})
            if isinstance(view,QTreeWidget):
                iterator = QTreeWidgetItemIterator(view)
                while iterator.value():
                    item = iterator.value()
                    if str(item.data(0,Qt.UserRole)) in previous.get('expanded',[]):
                        item.setExpanded(True)
                    iterator += 1
            index = QModelIndex()
            for row in previous.get('selected',[]):
                index = view.model().index(int(row),0,index)
                if not index.isValid():
                    break
            if index.isValid():
                view.setCurrentIndex(index)
            view.verticalScrollBar().setValue(int(previous.get('scroll',0)))

    def _enrich_usb_row(self, song, row):
        # A desktop artwork cache can change independently of the exported
        # cover. USB rows describe their native catalog reference only.
        row['art'] = 'cover' if song.artwork else ''

    def _load_usb_song(self, song):
        snapshot = self.usb_device_pages._snapshot
        if not hasattr(snapshot,'target') or snapshot.error or snapshot.notice:
            return
        dev = str(snapshot.target)
        if self._defer_device_operation_until_scan_finishes(lambda:self._load_usb_song(song)):
            return
        if self._device_operation_owned():
            self._flash_status('Wait for the current device operation')
            return
        from soundbox.cdj_usb import prepare_playback
        self._release_eject_ready()
        worker = CdjWorker(lambda _cancel,_progress:prepare_playback(snapshot.target,snapshot.mount_token,song))
        self._cdj_worker = worker
        def loaded(result):
            if worker.stop.is_set() or self._shutting_down:
                return
            if isinstance(result,Exception):
                self._flash_status(str(result),15)
                return
            audio,analysis = result
            track_id = f'usb:{snapshot.facts.volume_uuid}:{song.id}'
            self._capture_autoplay_context(track_id)
            title = f'{song.artist} — {song.title}' if song.artist else song.title
            self.player_bar.play_track(track_id,audio,title=title,bpm=f'{song.bpm/100:.2f}' if song.bpm else None,
                analysis_path=str(analysis) if analysis else '',autoplay=None,cues=None)
            self._sync_cue_editing()
            self._update_player_navigation()
            self._flash_status('Loaded from USB',5)
        worker.finished.connect(lambda w=worker:self._finish_device_result_worker('_cdj_worker',w,loaded))
        self._start_device_worker(worker,dev,'inspect')


    def _load_browser_selection(self):
        if self.stack.currentIndex()==1 and self.device_content_stack.currentIndex()==2:
            item = self.usb_device_pages.playlist_tree.currentItem()
            if item is not None:
                self.usb_device_pages._activate_song(item)
        elif self.stack.currentWidget() is self.artists_view:
            track = self._selected_track()
            if track is not None:
                self._load_track(track)
        elif self.stack.currentIndex() == 0:
            self._play_row(self.table.currentIndex())
