"""Quiet durable USB preference intentions on the shared device worker lane.

The source FIFO owns admission/ACK; the existing CDJ planner/transaction owns
all filesystem writes. Runtime captures never survive a mount or Restore epoch.
"""
from copy import deepcopy
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject,QTimer

from soundbox.cdj_usb import DISPLAY_FIELDS
from soundbox.device_operations import adapter_result
from soundbox.export_snapshot import source_paths
from soundbox.operation_control import OperationCancelled
from .cdj import CdjWorker,_format_paths_owned
from .devices import device_facts,device_target_profile
from .source_intent_admission import SourceIntentAdmission,SourceQueueAcknowledgement
from .usb_device_view import UsbDevicePages,UsbSnapshot

QUEUE='usb_settings_edits'
_KEYS={'operation_id','device','role','uuid','token','mount_epoch','source','source_kind',
       'library_id','repository_generation','changes','error'}


def validate_changes(changes):
    """Bound only offered preference deltas; engine validation remains final."""
    if type(changes) is not dict or not changes or len(changes)>14:
        raise ValueError('USB settings require a nonempty bounded preference delta')
    for key,value in changes.items():
        if key=='device_name':
            if type(value) is not str or len(value)>128 or '\x00' in value:raise ValueError('Invalid USB name')
        elif key in ('background_plus','background_classic'):
            if type(value) is not int or not 0<=value<=8:raise ValueError('Invalid USB color')
        elif key in DISPLAY_FIELDS:
            if type(value) is not str or value not in tuple(label for label,_ in DISPLAY_FIELDS[key][1]):
                raise ValueError('Invalid USB display preference')
        elif key in ('categories','sorts'):
            if (type(value) is not list or len(value)>256 or len(set(value))!=len(value)
                    or any(type(item) is not int or not 1<=item<=65535 for item in value)):
                raise ValueError('Invalid USB browse order')
        elif key=='sub_column':
            if value is not None and (type(value) is not int or not 1<=value<=65535):raise ValueError('Invalid USB column')
        elif key=='colors':
            if (type(value) is not dict or not value or not set(value)<=set('12345678')
                    or any(type(text) is not str or len(text)>128 or '\x00' in text for text in value.values())):
                raise ValueError('Invalid USB color comments')
        elif key=='my_settings':
            from soundbox import my_settings
            if my_settings.validate(value)!=value or not value:raise ValueError('Invalid player settings')
        else:raise ValueError('Unknown USB preference')
    if len(json.dumps(changes,allow_nan=False).encode('utf-8'))>32768:raise ValueError('USB settings are too large')
    return deepcopy(changes)


def _merge(before,after):
    result=deepcopy(before)
    for key,value in after.items():
        if key=='my_settings':
            target=result.setdefault(key,{})
            for name,fields in value.items():target.setdefault(name,{}).update(fields)
        elif key=='colors':result.setdefault(key,{}).update(value)
        else:result[key]=deepcopy(value)
    return result


def _verified(snapshot,job,changes):
    if (not isinstance(snapshot,UsbSnapshot) or snapshot.error or snapshot.notice or snapshot.native is None
            or str(snapshot.target)!=job['device'] or snapshot.mount_token!=job['token']
            or snapshot.facts.volume_uuid!=job['uuid']):return False
    native=snapshot.native;preferences=native.preferences
    for key,value in changes.items():
        if key=='my_settings':
            from soundbox import my_settings
            actual=my_settings.values(native.files)
            if any(actual.get(name,{}).get(field)!=chosen for name,fields in value.items() for field,chosen in fields.items()):return False
        elif key in ('categories','sorts'):
            actual=[row[1] for row in sorted(preferences.get(key,()),key=lambda row:row[3]) if row[4]]
            if actual!=value:return False
        elif key=='colors':
            actual={str(identity):text for identity,text in preferences.get('colors',())}
            if any(actual.get(identity)!=text for identity,text in value.items()):return False
        elif key=='sub_column':
            if preferences.get(key)!=value:return False
        elif key in DISPLAY_FIELDS:
            if dict(native.display).get(key)!=value:return False
        elif getattr(native,'name' if key=='device_name' else key,None)!=value:return False
    return True


class UsbSettingsController(QObject):
    def __init__(self,window,*,operation_factory=None,facts_factory=None):
        super().__init__(window)
        self.window=window;self.tags=window.tag_controller
        self.factory=operation_factory or self._operation
        self.facts=facts_factory or (lambda dev:device_facts(dev,cached_only=True))
        self.closing=False;self._captures={};self._completed=None;self._active=None
        self._not_before=0.0
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.pump)
        self.admission=SourceIntentAdmission(self,QUEUE,self._admission_changed,current_owner=self._owner)
        self.ack=SourceQueueAcknowledgement(self,QUEUE)
        self.tags.idle.connect(self.schedule)
        QTimer.singleShot(0,self,self.schedule)

    def _owner(self):return getattr(self.window,'usb_settings_controller',self) is self

    @property
    def pending(self):
        queue=self.window.settings.get(QUEUE,[])
        return deepcopy([*(queue if isinstance(queue,list) else []),*self.admission.rows])

    @property
    def admission_pending(self):return self.admission.pending or self.ack.pending

    @property
    def admission_error(self):return self.ack.error or self.admission.error or self.queue_error()

    @property
    def busy(self):
        queue=self.window.settings.get(QUEUE,[])
        return bool(self._active or self._completed or self.admission_pending
                    or isinstance(queue,list) and any(not isinstance(row,dict) or not row.get('error') for row in queue))

    def flush_admission(self):return self.ack.flush() and self.admission.flush()

    def queue_error(self):
        try:
            queue=self.window.settings.get(QUEUE,[])
            if type(queue) is not list or len(queue)+len(self.admission.staged)>256:raise ValueError()
            seen=set()
            for job in self.pending:
                if type(job) is not dict or set(job)!=_KEYS:raise ValueError()
                for key in ('operation_id','device','role','uuid','mount_epoch','source','source_kind','library_id','error'):
                    if type(job[key]) is not str or len(job[key])>4096:raise ValueError()
                if (not job['operation_id'] or job['operation_id'] in seen or not job['uuid'] or not job['mount_epoch']
                        or job['role'] not in ('generic_usb','ipod_usb') or not Path(job['device']).is_absolute()
                        or not Path(job['source']).is_absolute() or not job['source_kind']
                        or type(job['token']) is not int or job['token']<=0
                        or type(job['repository_generation']) is not int or job['repository_generation']<0):raise ValueError()
                seen.add(job['operation_id']);validate_changes(job['changes'])
        except (ValueError,TypeError,KeyError,OverflowError):return 'Saved USB settings need attention; intentions are preserved'
        return ''

    def _scope(self):return self.admission._context()

    def _capture(self,binding):
        if type(binding) is not tuple or len(binding)!=2:return None
        snapshot,role=binding;w=self.window
        if not isinstance(snapshot,UsbSnapshot) or role not in ('generic_usb','ipod_usb') or snapshot.native is None or snapshot.error or snapshot.notice:return None
        dev=str(snapshot.target);mount=w._device_coordinator.mounts.get(dev)
        pages=vars(w).get('usb_device_pages')
        held=getattr(pages,'settings_binding',None) if pages is not None else None
        held_current=(isinstance(held,tuple) and len(held)==2 and held[0] is snapshot and held[1]==role)
        if (isinstance(pages,UsbDevicePages) and held_current
                and pages._presentation_attachment!=mount):return None
        owned=any(captured['snapshot'] is snapshot and captured['role']==role and captured['mount']==mount
                  for captured in self._captures.values())
        if w._usb_view_cache.get(dev) is not snapshot and not held_current and not owned:return None
        if (not w.lib or self._scope()[1] is None or type(self._scope()[2]) is not int
                or mount is None or mount.path!=dev or mount.mount_token!=snapshot.mount_token
                or w._device_mount_tokens.get(dev)!=snapshot.mount_token or not snapshot.facts.volume_uuid):return None
        captured=dict(scope=self._scope(),snapshot=snapshot,role=role,mount=mount,
                      sources=tuple(source_paths(w.lib)))
        return captured

    def _guard(self,job,captured,*,check_paths=True):
        w=self.window
        try:
            mount=w._device_coordinator.mounts.get(job['device'])
            profile=device_target_profile(w._device_profiles.get(job['device']),'' if job['role']=='generic_usb' else job['role'])
            facts=self.facts(job['device'])
            active=w._device_coordinator.active
            return bool(self._owner() and not self.closing and not w._shutting_down and not w._state_maintenance
                and not _format_paths_owned(w) and self._scope()==captured['scope']
                and (job['source'],job['source_kind'],job['library_id'])==captured['scope'][0]
                and job['repository_generation']==captured['scope'][2]
                and job['uuid']==captured['snapshot'].facts.volume_uuid
                and (not check_paths or tuple(source_paths(w.lib))==captured['sources'])
                and mount==captured['mount'] and mount.mount_epoch==job['mount_epoch']
                and mount.mount_token==job['token'] and w._device_mount_tokens.get(job['device'])==job['token']
                and profile is not None and profile.kind==job['role'] and profile.cdj_export
                and facts.writable is True and facts.volume_uuid==job['uuid']
                and not (active is not None and active.worker is getattr(w,'_usb_settings_worker',None)
                         and active.cancellation.is_set())
                and not w._device_coordinator.stopping and not w._device_coordinator.eject_pending
                and job['device'] not in getattr(w,'_usb_format_volumes',()))
        except (AttributeError,TypeError,ValueError,OSError,RuntimeError):return False

    def enqueue(self,binding,changes):
        try:changes=validate_changes(changes)
        except (ValueError,TypeError,OverflowError) as error:
            self._attention(str(error),binding);return False
        captured=self._capture(binding)
        if captured is None or self.queue_error() or len(self.pending)>=256 or self.admission_error:return False
        scope=captured['scope'];snapshot,role=binding
        job=dict(operation_id=uuid4().hex,device=str(snapshot.target),role=role,uuid=snapshot.facts.volume_uuid,
                 token=snapshot.mount_token,mount_epoch=captured['mount'].mount_epoch,source=scope[0][0],
                 source_kind=scope[0][1],library_id=scope[0][2],repository_generation=scope[2],changes=changes,error='')
        if not self._guard(job,captured,check_paths=False):
            self._attention('USB or library changed; review the settings and retry',binding);return False
        self._captures[job['operation_id']]=captured
        if not self.admission.stage(job):self._captures.pop(job['operation_id'],None);return False
        self._not_before=monotonic()+.25
        self.admission.pump();self._actions_changed();self.schedule();return True

    def _admission_changed(self,outcome):
        if outcome=='failed':self._attention('USB setting NOT saved — '+self.admission.error,self._binding())
        else:self.schedule()
        self._actions_changed()

    def _actions_changed(self):
        self.window._update_device_action_state()

    def _binding(self,job=None):
        job=job or (self.pending[0] if self.pending else None)
        if not isinstance(job,dict):return None
        pages=vars(self.window).get('usb_device_pages')
        current=getattr(pages,'settings_binding',None) if pages is not None else None
        if (isinstance(current,tuple) and len(current)==2 and isinstance(current[0],UsbSnapshot)
                and current[1]==job.get('role') and current[0].facts.volume_uuid==job.get('uuid')):
            # Attention can offer a fresh same-USB Retry after remount. This
            # presentation binding grants no authority to replay the old job.
            return current
        captured=self._captures.get(job.get('operation_id')) if job else None
        if captured:return (captured['snapshot'],captured['role'])
        if job and {'device','uuid','token','role'}<=set(job):
            snapshot=self.window._usb_view_cache.get(job['device'])
            if (isinstance(snapshot,UsbSnapshot) and snapshot.facts.volume_uuid==job['uuid']
                    and snapshot.mount_token==job['token']):return snapshot,job['role']
        return None

    def _attention(self,message,binding):
        pages=vars(self.window).get('usb_device_pages')
        if pages is None or not isinstance(binding,tuple) or len(binding)!=2:return
        current=getattr(pages,'settings_binding',None)
        if not isinstance(current,tuple) or len(current)!=2:return
        snapshot,role=binding;visible,visible_role=current
        if (not isinstance(snapshot,UsbSnapshot) or not isinstance(visible,UsbSnapshot)
                or role!=visible_role or str(snapshot.target)!=str(visible.target)
                or snapshot.mount_token!=visible.mount_token
                or snapshot.facts.volume_uuid!=visible.facts.volume_uuid):return
        pages.set_settings_error(str(message)[:4096],binding)

    def schedule(self):
        if self.closing or not self._owner():return
        if self.pending or self._completed or self.queue_error():
            delay=0 if self._completed is not None else max(0,round((self._not_before-monotonic())*1000))
            self.timer.start(delay)

    def pump(self):
        w=self.window
        if self.closing or not self._owner() or self._active or self.admission_pending:return
        error=self.queue_error()
        if error:
            pages=vars(w).get('usb_device_pages')
            self._attention(error,self._binding() or getattr(pages,'settings_binding',None));return
        if self._completed is not None:
            self._acknowledge();return
        if not self.pending:return
        queue=deepcopy(w.settings.get(QUEUE,[]));head=queue[0]
        if head['error']:self._attention(head['error'],self._binding(head));return
        captured=self._captures.get(head['operation_id'])
        if captured is None or not self._guard(head,captured):
            self._fail(queue[:1],'USB mount or library session changed; use Retry on the original USB');return
        if w._device_coordinator.busy or getattr(w,'_refresh_worker',None) is not None:
            self.timer.start(100);return
        if monotonic()<self._not_before:self.schedule();return
        batch=[];changes={}
        for job in queue:
            capture=self._captures.get(job['operation_id'])
            if (job['error'] or capture is None or capture['sources']!=captured['sources']
                    or not self._guard(job,capture,check_paths=False)
                    or any(job[key]!=head[key] for key in ('device','role','uuid','token','mount_epoch','source','source_kind','library_id'))):break
            batch.append(job);changes=_merge(changes,job['changes'])
        guard=lambda check_paths=True:self._guard(head,captured,check_paths=check_paths)
        try:operation=self.factory({**deepcopy(head),'changes':changes},captured['sources'],guard)
        except Exception as exc:self._fail(batch,str(exc));return
        worker=CdjWorker(operation);self._active=(worker,batch,captured,changes)
        w._usb_settings_worker=worker
        worker.finished.connect(lambda:self._finished(worker))
        try:w._start_device_worker(worker,head['device'],'commit-usb-settings')
        except Exception as exc:
            if worker.isRunning() or worker.isFinished():
                worker.cancel();return  # Its finished callback still owns join.
            w._device_coordinator.finished(worker)
            self._active=None;w._usb_settings_worker=None;worker.deleteLater();self._fail(batch,str(exc))

    def _operation(self,job,sources,guard):
        def operation(cancel,progress):
            from soundbox import cdj_export,cdj_transaction
            from .usb_device_view import read_native_usb_snapshot
            def current():
                if cancel() or not guard():raise OperationCancelled('USB settings session changed')
            current()
            plan=cdj_export.plan_settings(job['device'],job['changes'],token=job['token'],volume_uuid=job['uuid'],
                sources=sources,target_role=job['role'],should_cancel=lambda:cancel() or not guard(False),
                recovery_root=self.window._data_file.parent/'cdj-recovery')
            try:
                current();cdj_transaction.commit(plan,should_cancel=lambda:cancel() or not guard(False),on_progress=progress)
                current();snapshot=read_native_usb_snapshot(job['device'],job['token'],self.facts(job['device']))
                current()
                if not _verified(snapshot,job,job['changes']):raise RuntimeError('USB settings readback did not match the saved changes')
                return snapshot
            finally:plan.close()
        return operation

    def _finished(self,worker):
        active=self._active
        if active is None or active[0] is not worker:return
        _worker,batch,captured,changes=active;w=self.window
        result=worker.result;command=getattr(worker,'device_command',None)
        if command is not None:w._device_coordinator.publish(worker,adapter_result(command,result))
        # The canonical ACK and cache publication occur only after this join.
        w._finish_worker('_usb_settings_worker',worker)
        # The joined result now belongs to this consumer. An exception's
        # traceback can retain run's QThread wrapper through worker.result.
        worker.result=None;self._active=None
        if captured['scope'][1:]!=self._scope()[1:] or not self._owner():
            self._actions_changed();return
        error=str(result) if isinstance(result,Exception) else ''
        if not error and (not self._guard(batch[0],captured) or not _verified(result,batch[0],changes)):
            error='USB settings completed in an older session; refresh and retry the original USB'
        self._completed=(batch,None if error else result,error,captured)
        self.schedule()

    def _fail(self,batch,message):
        captured=self._captures.get(batch[0]['operation_id'])
        self._completed=(batch,None,str(message)[:4096],captured)
        self._acknowledge()

    def _acknowledge(self):
        batch,result,error,captured=self._completed;w=self.window
        previous=deepcopy(w.settings.get(QUEUE,[]))
        if previous[:len(batch)]!=batch:
            self._attention('USB settings queue changed; saved intentions need review',self._binding(batch[0]));return
        if error:desired=[{**job,'error':error} for job in previous[:len(batch)]]+previous[len(batch):]
        else:desired=previous[len(batch):]
        original_scope=self._scope()
        def current():
            # The joined worker already verified its original USB readback.
            # A later remount cannot authorize cache publication, but need not
            # strand this completed operation's purely canonical ACK forever.
            return self._scope()==original_scope and self._owner()
        def committed():
            self._completed=None
            try:
                if error:self._attention(error,self._binding(batch[0]))
                else:
                    if captured is not None and self._guard(batch[0],captured):
                        w._usb_view_cache[batch[0]['device']]=result
                        role=getattr(w,'_current_device_role','') or 'generic_usb'
                        if getattr(w,'_current_device_path','')==batch[0]['device'] and role==batch[0]['role']:
                            w._present_usb_device(batch[0]['device'])
                        self._attention('',(result,batch[0]['role']))
                    for job in batch:self._captures.pop(job['operation_id'],None)
            finally:self._actions_changed();self.schedule()
        def failed(failure):self._attention('USB settings acknowledgement needs retry — '+str(failure),self._binding(batch[0]))
        self.ack.start(previous,desired,committed,failed,current=current)

    def retry(self,binding):
        if self.closing or self.queue_error() or not self.pending:return False
        captured=self._capture(binding)
        if captured is None:return False
        head=self.pending[0];snapshot,role=binding;scope=captured['scope']
        if (head['uuid']!=snapshot.facts.volume_uuid or head['role']!=role
                or (head['source'],head['source_kind'],head['library_id'])!=scope[0]):return False
        if self.ack.pending:return self.ack.retry()
        if self.admission.pending:return self.admission.retry()
        previous=deepcopy(self.window.settings.get(QUEUE,[]));head=previous[0]
        renewed=[]
        for job in previous:
            if any(job[key]!=head[key] for key in ('uuid','role','source','source_kind','library_id')):break
            renewed.append({**job,'device':str(snapshot.target),'token':snapshot.mount_token,
                 'mount_epoch':captured['mount'].mount_epoch,'repository_generation':scope[2],'error':''})
        if not self._guard(renewed[0],captured,check_paths=False):return False
        def committed():
            for job in renewed:self._captures[job['operation_id']]=captured
            self._completed=None;self._not_before=monotonic()+.25;self.schedule()
        return self.ack.start(previous,[*renewed,*previous[len(renewed):]],committed,
            lambda error:self._attention('USB retry NOT saved — '+str(error),binding))

    def stop(self):
        self.closing=True;self.timer.stop();self.admission.stop();self.ack.stop()
        if self._active:self._active[0].cancel()
