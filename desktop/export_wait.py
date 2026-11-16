"""Direct iPod commands wait for accepted source edits without owning a device."""
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QTimer

from .cdj import _source_edits_pending,_source_edits_error,_fail_source_export
from .devices import device_facts


def cancel_source_syncs(host):
    queue=getattr(host,'_source_sync_waiting',None)
    if queue is None:return False
    host._source_sync_waiting=None
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
    """Capture command identity now; rebind native content after source ACKs."""
    if _ready:return False
    waiting=getattr(host,'_source_sync_waiting',None)
    if waiting is None and not _source_edits_pending(host):return False
    library=host.lib
    if library is None:return True
    error=_source_edits_error(host)
    if error:
        _fail_source_export(host,error,waiting_attr='_source_sync_waiting')
        return True
    native={row['ID']:row for row in library.source_playlist_catalog.get('nodes',[])}
    captured=[]
    for playlist in playlists or ():
        row=native.get(playlist.pid)
        if row is not None:
            captured.append(('native',playlist.pid,row['UUID'],playlist.export_key))
        else:captured.append(('local',deepcopy(playlist)))
    request=dict(kind=kind,dev=dev,selection=None if playlists is None else captured,
                 options=deepcopy(options),source=(str(library.source),library.source_library_id),
                 target=(host._device_mount_tokens.get(dev,0),device_facts(dev,cached_only=True).volume_uuid))
    if waiting is not None:
        waiting.append(request);return True
    waiting=[request];host._source_sync_waiting=waiting
    host._start_pulse('Waiting for source edits');host.cancel_btn.setVisible(True)

    def ready():
        if host._shutting_down or getattr(host,'_source_sync_waiting',None) is not waiting:return
        if not waiting:
            host._source_sync_waiting=None;return
        error=_source_edits_error(host)
        if error:
            _fail_source_export(host,error,waiting_attr='_source_sync_waiting')
            return
        if (host._state_maintenance or _source_edits_pending(host) or host._device_operation_owned()):
            QTimer.singleShot(100,ready);return
        command=waiting.pop(0);library=host.lib;selected=None
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
        except Exception as exc:
            host._flash_status(str(exc),15)
        if getattr(host,'_source_sync_waiting',None) is not waiting:return
        if waiting:QTimer.singleShot(100,ready)
        else:
            host._source_sync_waiting=None
            if not host._device_operation_owned():
                host._stop_pulse();host.cancel_btn.setVisible(False)
    QTimer.singleShot(100,ready)
    return True
