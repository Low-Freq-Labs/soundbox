"""Owned iPod source waits and cancellation, without owning a device writer."""
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QTimer

from soundbox.operation_control import OperationCancelled

from .cdj import _source_edits_pending,_source_edits_error
from .devices import device_facts


def _batch_owned(host, request):
    batch=request.get('batch')
    return bool(batch is not None and getattr(host,'_export_batch',None) is batch
                and batch.get('active') is request.get('batch_target'))


def _retire_source_commands(host, commands, message, *, cancelled=False):
    """End only the captured batch; source intentions are never cancelled here."""
    ended=False
    for command in commands:
        if not _batch_owned(host,command):continue
        batch=command['batch']
        if cancelled:
            batch['cancelled']=True
            batch['pending'].clear()
        host._export_target_result(command['dev'],
            OperationCancelled(message) if cancelled else RuntimeError(message))
        ended=True
    if ended:host._maybe_finish_export_target()


def cancel_source_syncs(host):
    queue=getattr(host,'_source_sync_waiting',None)
    if queue is None:return False
    host._source_sync_waiting=None
    for command in queue:
        command['cancelled']=True
        cancel=command.get('cancel_checkpoint')
        if callable(cancel):cancel()
    retiring=[command for command in queue if _source_request_running(host,command)]
    if retiring:
        previous=getattr(host,'_cancelled_source_syncs',None) or []
        retained=[*previous,*retiring]
        host._cancelled_source_syncs=retained
        def retired():
            if getattr(host,'_cancelled_source_syncs',None) is not retained:return
            if any(_source_request_running(host,command) for command in retained):
                QTimer.singleShot(50,host,retired);return
            host._cancelled_source_syncs=None
            if host._shutting_down:return
            active=getattr(getattr(host,'_device_coordinator',None),'active',None)
            if active is None or not active.cancellation.is_set():
                host._cancelling=False
            if retire_source_presentation(host):
                host._flash_status('iPod request cancelled — no export was started',15)
            host._maybe_finish_export_target()
        QTimer.singleShot(0,host,retired)
    _retire_source_commands(host,queue,
        'iPod request cancelled before device admission. No device changes were made.',cancelled=True)
    return True


def _source_request_running(host,request):
    """Retained pointers/tokens retire only after their TaskOwner joined them."""
    token=request.get('checkpoint')
    return bool(request.get('manifest_worker') is not None or token is not None
                and getattr(getattr(host,'_state_committer',None),'token',None) is token)


def retire_source_presentation(host):
    """A completed capture may release its controls, never a successor's."""
    if (host._shutting_down or getattr(host,'_source_sync_waiting',None)
            or getattr(host,'_cancelled_source_syncs',None)
            or getattr(host,'_cdj_waiting',None) or host._device_operation_owned()
            or getattr(host,'_usb_format_device','')):return False
    host._cancelling=False
    host._stop_pulse()
    host.cancel_btn.setVisible(False)
    host.progress.setVisible(False)
    host._update_device_action_state()
    return True


def retain_source_cancellation(host):
    """A joined device result cannot retire a still-running capture's controls."""
    if not getattr(host,'_cancelled_source_syncs',None):return False
    host._cancelling=True
    host._start_pulse('Cancelling')
    host.cancel_btn.setVisible(True)
    return True



def sync_source_identity(host,dev,playlists):
    library=host.lib
    if library is None:return None
    native={row['ID']:row for row in library.source_playlist_catalog.get('nodes',[])}
    return ((str(library.source),library.source_library_id),
            (getattr(host,'_device_mount_tokens',{}).get(dev,0),device_facts(dev,cached_only=True).volume_uuid),
            tuple((playlist.pid,native[playlist.pid]['UUID'],native[playlist.pid]['rb_local_deleted'])
                  for playlist in playlists or () if playlist.pid in native))


def defer_source_sync(host,kind,dev,playlists,*,_ready=False,**options):
    """Legacy physical sync waits own one poll and consume only their command."""
    if _ready:return False
    waiting=getattr(host,'_source_sync_waiting',None)
    if waiting is None and not _source_edits_pending(host):return False
    library=host.lib
    if library is None:return True
    native={row['ID']:row for row in library.source_playlist_catalog.get('nodes',[])}
    captured=[]
    for playlist in playlists or ():
        row=native.get(playlist.pid)
        if row is not None:
            captured.append(('native',playlist.pid,row['UUID'],playlist.export_key))
        else:captured.append(('local',deepcopy(playlist)))
    batch=getattr(host,'_export_batch',None)
    batch_target=batch.get('active') if batch else None
    if not batch_target or batch_target.get('path')!=dev:batch=None;batch_target=None
    request=dict(kind=kind,dev=dev,selection=None if playlists is None else captured,
                 options=deepcopy(options),source=(str(library.source),library.source_library_id),
                 target=(host._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid),
                 batch=batch,batch_target=batch_target,legacy_source=True,terminal=False)
    if waiting is None:waiting=[];host._source_sync_waiting=waiting

    def remove():
        if request in waiting:waiting.remove(request)
        if getattr(host,'_source_sync_waiting',None) is waiting and not waiting:
            host._source_sync_waiting=None

    def finish_presentation():
        if (not getattr(host,'_source_sync_waiting',None) and not host._device_operation_owned()
                and not getattr(host,'_export_batch',None)):
            host._stop_pulse();host.cancel_btn.setVisible(False)

    def reject(error):
        if request['terminal']:return
        request['terminal']=True;remove()
        foreground=bool(getattr(host,'_source_sync_waiting',None) or host._device_operation_owned())
        if not foreground:host._stop_pulse();host.cancel_btn.setVisible(False)
        notice=getattr(host,'_background_notice',host._flash_status) if foreground else host._flash_status
        notice('Export blocked — '+str(error),20)
        _retire_source_commands(host,(request,),str(error))

    error=_source_edits_error(host)
    if error:reject(error);return True
    waiting.append(request)
    if waiting[0] is request:
        host._start_pulse('Waiting for source edits');host.cancel_btn.setVisible(True)

    def ready():
        if (request['terminal'] or host._shutting_down
                or getattr(host,'_source_sync_waiting',None) is not waiting or request not in waiting):return
        if waiting[0] is not request:
            QTimer.singleShot(100,ready);return
        error=_source_edits_error(host)
        if error:
            reject(error);return
        if (host._state_maintenance or _source_edits_pending(host) or host._device_operation_owned()):
            QTimer.singleShot(100,ready);return
        command=request;request['terminal']=True;remove();library=host.lib;selected=None
        if command['batch'] is not None and (
                not _batch_owned(host,command) or command['batch'].get('cancelled',False)):
            _retire_source_commands(host,(command,),'iPod request cancelled before device admission.')
            finish_presentation();return
        try:
            if (library is None or command['source']!=(str(library.source),library.source_library_id)
                    or command['target']!=(host._device_mount_tokens.get(command['dev'],0),
                        device_facts(command['dev'],cached_only=True).volume_uuid)):
                raise ValueError('Library or connected iPod changed; choose the target again')
            if command['selection'] is not None:
                native={row['ID']:row for row in library.source_playlist_catalog.get('nodes',[])}
                fresh={playlist.pid:playlist for playlist in library.playlists};selected=[]
                for pin in command['selection']:
                    if pin[0]=='local':selected.append(pin[1]);continue
                    _,identity,uuid,export_key=pin
                    row=native.get(identity)
                    if (row is None or row['UUID']!=uuid or row['rb_local_deleted']!=0 or identity not in fresh):
                        raise ValueError('Selected playlist changed; select its exact row again')
                    selected.append(replace(fresh[identity],export_key=export_key))
            getattr(host,f'_begin_{command["kind"]}_ipod_sync')(command['dev'],selected,
                _source_ready=True,**command['options'])
            if (_batch_owned(host,command) and not host._device_operation_owned()
                    and not getattr(host,'_cdj_waiting',None)
                    and not any(_batch_owned(host,row) for row in waiting)):
                _retire_source_commands(host,(command,),
                    'iPod request did not start or was cancelled before device admission.')
        except Exception as exc:
            host._flash_status(str(exc),15)
            _retire_source_commands(host,(command,),str(exc))
        finish_presentation()
    QTimer.singleShot(100,ready)
    return True
