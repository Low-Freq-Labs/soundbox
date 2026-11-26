"""Once-per-attachment USB setup offers; observations never authorize writes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QApplication,QMessageBox
from shiboken6 import isValid

from .devices import device_facts,device_target_profile


@dataclass(frozen=True)
class _Offer:
    identity:object
    path:str
    token:int
    volume_uuid:str
    role:str
    proof:object

    @property
    def key(self):return self.identity,self.role

    @property
    def observed(self):return self.path,self.token,self.volume_uuid,self.role


class UsbSetupController(QObject):
    """Queue advisory absence observations until the ordinary UI is idle."""
    def __init__(self,window):
        super().__init__(window)
        self.window=window;self.closing=False;self.showing=False;self.dialog=None
        self.seen={};self.pending={}
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)

    def _current(self,offer):
        w=self.window;coordinator=w._device_coordinator
        if coordinator.mounts.get(offer.path)!=offer.identity:return False
        if w._device_mount_tokens.get(offer.path,0)!=offer.token:return False
        profile=device_target_profile(w._device_profiles.get(offer.path),
            '' if offer.role=='generic_usb' else offer.role)
        if profile is None or profile.kind not in {'generic_usb','ipod_usb'} or not profile.cdj_export:return False
        facts=device_facts(offer.path,cached_only=True)
        return facts.writable is True and facts.volume_uuid==offer.volume_uuid

    def _prune(self):
        for key,offer in tuple(self.seen.items()):
            # Temporary unavailable facts must not turn a declined offer into
            # another prompt. Only a different physical attachment retires it.
            if self.window._device_coordinator.mounts.get(offer.path)!=offer.identity:
                self.seen.pop(key,None);self.pending.pop(key,None)

    def known(self):
        self._prune()
        return frozenset(offer.observed for offer in self.seen.values())

    def consume(self,observations):
        if self.closing or self.window._shutting_down:return
        values=observations.values() if isinstance(observations,dict) else observations
        for value in values:
            if not isinstance(value,(tuple,list)) or len(value)!=5:continue
            path,token,volume_uuid,role,proof=value
            if (type(path)is not str or type(token)is not int or token<=0 or type(volume_uuid)is not str
                    or not volume_uuid or type(role)is not str or role not in {'generic_usb','ipod_usb'}):continue
            identity=self.window._device_coordinator.mounts.get(path)
            if identity is None:continue
            offer=_Offer(identity,path,token,volume_uuid,role,proof)
            if (getattr(proof,'state','') not in {'missing','configured','unavailable'}
                    or str(getattr(proof,'target',''))!=str(Path(path))
                    or getattr(proof,'volume_uuid',None)!=volume_uuid
                    or getattr(proof,'target_role',None)!=role or not self._current(offer)):
                continue
            if offer.key in self.seen:continue
            self.seen[offer.key]=offer
            if proof.state=='missing':self.pending[offer.key]=offer
        self.kick()

    def kick(self):
        if self.closing or self.window._shutting_down:return
        self._prune()
        if self.pending and not self.showing and not self.timer.isActive():self.timer.start(0)

    def _idle(self):
        w=self.window;c=w._device_coordinator
        return (not self.closing and not w._shutting_down and not getattr(w,'_state_maintenance',False)
            and not getattr(w,'_state_publication',False) and w.isVisible()
            and getattr(w,'lib',None) is not None
            and c.active is None and not c.stopping and not c.eject_pending
            and not w._device_operation_owned() and not getattr(w,'_device_finalization_refreshing',False)
            and not getattr(w,'_export_batch',None) and not getattr(w,'_source_sync_waiting',None)
            and not getattr(w,'_cdj_waiting',None) and not getattr(w,'_refresh_worker',None)
            and QApplication.activeModalWidget() is None)

    def _context(self):
        w=self.window;repository=getattr(w,'_state_repository',None)
        library=getattr(w,'lib',None)
        source=(str(getattr(library,'source','')),getattr(library,'source_kind',''),
                getattr(library,'source_library_id',''))
        return (getattr(w,'_current_device_path',''),getattr(w,'_current_device_role',''),repository,
                getattr(getattr(repository,'area',None),'generation',None),source)

    def poll(self):
        if self.closing or self.window._shutting_down:return
        self._prune()
        if not self.pending or self.showing:return
        if not self._idle():self.timer.start(250);return
        key,offer=next(iter(self.pending.items()));self.pending.pop(key)
        if not self._current(offer):self.kick();return
        context=self._context();w=self.window
        row=getattr(w,'_usb_device_rows',{}).get(offer.path)
        name=str(row[0]) if row else Path(offer.path).name
        box=QMessageBox(w);self.dialog=box;self.showing=True
        box.setWindowTitle('Configure USB')
        box.setText('Configure this USB for CDJ exporting?')
        box.setInformativeText(name+'\n\nCreate the basic rekordbox USB library. Existing files will be kept.')
        box.setIcon(QMessageBox.Icon.Question)
        configure=box.addButton('Configure',QMessageBox.ButtonRole.AcceptRole)
        later=box.addButton('Not now',QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later);box.setEscapeButton(later)
        accepted=False
        try:
            box.exec();accepted=isValid(box) and box.clickedButton() is configure
        finally:
            self.dialog=None;self.showing=False
            if isValid(box):box.deleteLater()
        if accepted and self._current(offer) and context==self._context() and self._idle():
            w._begin_cdj_initialize(offer.path,target_role=offer.role,expected_setup=offer.proof)
        self.kick()

    def stop(self):
        self.closing=True;self.timer.stop();self.pending.clear();self.seen.clear()
        if self.dialog is not None and isValid(self.dialog):self.dialog.reject()
