"""Small source intentions become writable only after canonical acknowledgement."""
from copy import deepcopy

from PySide6.QtCore import QObject, QTimer


class SourceIntentAdmission(QObject):
    """Stage one queue's bounded intentions without serializing the library UI.

    The controller exposes staged rows for feedback, but must block its native
    pump while ``pending``. Only StateCommitter's durable continuation releases
    that barrier. The original source and restore generation never rebase.
    """
    def __init__(self, controller, key, changed, *, current_owner):
        super().__init__(controller)
        self.controller=controller;self.window=controller.window
        self.key=key;self.changed=changed;self.staged=[]
        self.current_owner=current_owner
        self.active=False;self.error='';self.stopping=False
        self._scope=None;self._base:list[dict]=[];self._discarding=False
        self._uncertain=[]
        self._paired={};self._paired_before={}
        self.timer=QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        controller.tags.idle.connect(self.schedule)

    def _context(self):
        w=self.window;lib=w.lib;repository=getattr(w,'_state_repository',None)
        return ((str(lib.source),lib.source_kind,lib.source_library_id) if lib else None,
                repository,getattr(repository,'generation',None),
                getattr(getattr(repository,'area',None),'generation',None))

    @property
    def pending(self):return bool(self.staged) or self.active or self._discarding

    @property
    def rows(self):
        return [{**job,'error':self.error or job.get('error','')} for job in deepcopy(self.staged)]

    def stage(self,job,*,paired_settings=None):
        if self.stopping or self.error or self._discarding:return False
        paired=deepcopy(paired_settings or {})
        if paired and (self.active or self._paired):return False
        if any(type(key) is not str or not key or key==self.key for key in paired):
            raise ValueError('Paired source intentions require separate settings keys')
        if not self.staged:
            self._scope=self._context();self._base=deepcopy(self.window.settings.get(self.key,[]))
            self._uncertain=[]
        elif self._scope!=self._context():return False
        for key,value in paired.items():
            self._paired_before[key]=(key in self.window.settings,deepcopy(self.window.settings.get(key)))
            self._paired[key]=value
        self.staged.append(deepcopy(job));self.schedule();return True

    def schedule(self):
        if self.pending and not self.active and not self.error and not self.stopping:
            self.timer.start(0)

    def _current(self):
        return (self.current_owner() and self._scope==self._context() and not self.window._state_maintenance
                and self.window.settings.get(self.key,[])==self._base
                and all((key in self.window.settings,self.window.settings.get(key))==before
                        for key,before in self._paired_before.items()))

    def pump(self,*,allow_shutdown=False):
        w=self.window
        if not self.pending or self.active or self.error or self.stopping:return False
        ack=getattr(self.controller,'ack',None)
        if ack is not None and ack.pending:
            self.timer.start(50);return False
        if w._shutting_down and not allow_shutdown:return False
        if not self._current():
            self._failed(RuntimeError('The library or pending intentions changed before saving'))
            return False
        batch=deepcopy(self.staged);discarding=self._discarding
        paired=deepcopy(self._paired)
        desired=[] if discarding else [*self._base,*batch]
        def current():
            return (self._current() and self.staged[:len(batch)]==batch
                    and self._discarding==discarding)
        self.active=True
        def committed():
            self.active=False
            del self.staged[:len(batch)]
            self._base=deepcopy(w.settings.get(self.key,[]))
            self._uncertain=[]
            self._paired={};self._paired_before={}
            self._discarding=False;self.error=''
            self.changed('discarded' if discarding else 'saved')
            self.schedule()
        def failed(error):
            self.active=False;self._failed(error)
        try:
            allowed=[]
            for value in (self._base,*self._uncertain):
                if value not in allowed:allowed.append(deepcopy(value))
            replacements={} if discarding else {('settings',key):value for key,value in paired.items()}
            replacements[('settings',self.key)]=desired
            accepted=w._commit_tag_state(replacements,(),committed,failed,current=current,
                                        expected_queues={self.key:allowed})
        except (OSError,ValueError,TypeError) as exc:
            failed(exc);return False
        if not accepted:
            self.active=False
            if not self.stopping:self.timer.start(50)
        elif self.pending and desired not in self._uncertain:self._uncertain.append(deepcopy(desired))
        return bool(accepted)

    def advance(self,before,after,*,staged=None,expected_staged=None):
        """Rebase only the durable edge published by this controller's ACK."""
        if not self.staged:return
        if self.active or self._base!=before or expected_staged is not None and self.staged!=expected_staged:
            self._failed(RuntimeError('Pending source intentions changed during acknowledgement'));return
        self._base=deepcopy(after);self._uncertain=[]
        if staged is not None:self.staged=deepcopy(staged)
        self.schedule()

    def _failed(self,error):
        self.error=str(error) or 'The pending intention could not be saved'
        self.timer.stop();self.changed('failed')

    def retry(self):
        if not self.pending or self.active or self.stopping:return False
        if not self._current():
            self._failed(RuntimeError('Return to the original library; restored state cannot accept an older intention'))
            return False
        self.error='';self.schedule();return True

    def discard(self):
        if not self.pending or self.active or self.stopping:return False
        if self._scope is None:return False
        context=self._context()
        if self._scope[1:]!=context[1:]:
            # A restore owns a different canonical generation. Its queues are
            # retained; no older RAM intention may remove their contents.
            self.staged=[];self._discarding=False;self.error='';self.timer.stop()
            self._paired={};self._paired_before={}
            self.changed('discarded');return True
        if self._scope[0]!=context[0]:
            self._failed(RuntimeError('Return to the original library to discard its pending intention'))
            return False
        self.error='';self._discarding=True;self.schedule();return True

    def flush(self):
        self.timer.stop()
        if self.error:return False
        while self.pending:
            committer=getattr(self.window,'_state_committer',None)
            if committer is not None and committer.owner.pending:
                committer.owner.drain()
                if self.error:return False
                if not self.pending:break
            if not self.active and not self.pump(allow_shutdown=True):return False
            committer=getattr(self.window,'_state_committer',None)
            if committer is None:return False
            committer.owner.drain()
            if self.error:return False
        return True

    def stop(self):
        self.stopping=True;self.timer.stop()


class SourceQueueAcknowledgement(QObject):
    """Sparse durable queue ACK with a logical source reservation while waiting."""
    def __init__(self,controller,key):
        super().__init__(controller);self.controller=controller;self.window=controller.window;self.key=key
        self.row=None;self.active=False;self.error='';self.stopping=False;self.token=object()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.pump)
        controller.tags.idle.connect(self.schedule)
        controller.tags.changed.connect(self.retire_stale)

    @property
    def pending(self):return self.row is not None or self.active

    def start(self,before,desired,committed,failed,*,current=None,replacements=None,removals=(),
              staged_after=None,expected_staged=None):
        if self.pending:return False
        admission=self.controller.admission
        self.row=dict(before=deepcopy(before),desired=deepcopy(desired),committed=committed,failed=failed,
            scope=admission._context(),current=current,replacements=deepcopy(replacements or {}),removals=tuple(removals),
            staged_after=deepcopy(staged_after),expected_staged=deepcopy(expected_staged),discard=False)
        admission.timer.stop()
        reserve=getattr(self.controller.tags,'reserve_source_ack',None)
        if callable(reserve):reserve(self.token)
        self.error='';self.pump(allow_shutdown=self.controller.closing or self.window._shutting_down);return True

    def schedule(self):
        if self.pending and not self.active and not self.error and not self.stopping:self.timer.start(0)

    def _current(self):
        row=self.row;w=self.window
        if row is None or row['scope']!=self.controller.admission._context() or w._state_maintenance:return False
        if not self.controller.admission.current_owner():return False
        queue=w.settings.get(self.key,[])
        # Only this ACK's RAM attention marker may differ from the captured
        # durable head. Requests, order and every tail row retain exact identity.
        equivalent=(queue==row['before'] or bool(queue and row['before'])
            and [{**queue[0],'error':row['before'][0].get('error','')},*queue[1:]]==row['before'])
        return bool(equivalent and (row['current'] is None or row['current']() is True)
            and (row['expected_staged'] is None or self.controller.admission.staged==row['expected_staged']))

    def pump(self,*,allow_shutdown=False):
        if not self.pending or self.active or self.error:return False
        w=self.window;row=self.row
        if row is None:return False
        if w._shutting_down and not allow_shutdown:return False
        if not self._current():self._failed(RuntimeError('Source queue acknowledgement lost its exact owner'));return False
        desired=[] if row['discard'] else row['desired']
        replacements={} if row['discard'] else deepcopy(row['replacements'])
        replacements[('settings',self.key)]=deepcopy(desired);self.active=True
        def committed():
            self.active=False;self.row=None;self.error=''
            admission=self.controller.admission
            try:
                if row['discard']:
                    admission.staged=[];admission.error='';admission._discarding=False
                    admission._base=[];admission._uncertain=[]
                    admission._paired={};admission._paired_before={}
                    admission.changed('discarded')
                else:
                    admission.advance(row['before'],desired,staged=row['staged_after'],expected_staged=row['expected_staged'])
                    row['committed']()
            except Exception as exc:
                from .state_commit import StatePublicationChangedError
                row['failed'](StatePublicationChangedError('Acknowledgement saved; source view refresh failed: '+str(exc)))
            finally:
                release=getattr(self.controller.tags,'release_source_ack',None)
                if callable(release):release(self.token)
                admission.schedule()
        allowed=[]
        for queue in (row['before'],row['desired']):
            if queue not in allowed:allowed.append(queue)
        if row['discard'] and [] not in allowed:allowed.append([])
        try:
            accepted=w._commit_tag_state(replacements,() if row['discard'] else row['removals'],committed,self._failed,
                current=self._current,expected_queues={self.key:allowed})
        except (OSError,ValueError,TypeError) as exc:self._failed(exc);return False
        if not accepted:self.active=False;self.timer.start(25)
        return bool(accepted)

    def _failed(self,error):
        self.active=False
        if self.retire_stale():return
        self.error=str(error) or 'Local acknowledgement failed';self.timer.stop()
        if self.row is not None:self.row['failed'](error)

    def retry(self):
        if not self.pending or self.active or self.stopping:return False
        if not self._current():return False
        self.error='';self.schedule();return True

    def discard(self):
        if self.retire_stale():return True
        if not self.pending or self.active or self.stopping or self.row is None or not self._current():return False
        self.row['discard']=True;self.error='';self.schedule();return True

    def retire_stale(self):
        row=self.row
        if (row is None or self.active or row['scope'][1:]==self.controller.admission._context()[1:]
                and self.controller.admission.current_owner()):return False
        # An explicit restore/controller takeover owns the new queue. Drop only
        # this older readback/token; neither old callbacks nor patches may run.
        self.row=None;self.error='';self.timer.stop()
        release=getattr(self.controller.tags,'release_source_ack',None)
        if callable(release):release(self.token)
        return True

    def retention_capture(self):
        """Candidate only: the caller must freshly verify one canonical queue."""
        if not self.error or self.active or self.row is None or self.controller.admission.pending or not self._current():return None
        return dict(owner=self.row,scope=self.controller.admission._context(),error=self.error,
            before=deepcopy(self.row['before']),desired=deepcopy(self.row['desired']),
            queue=deepcopy(self.window.settings.get(self.key,[])))

    def retention_current(self,captured):
        return bool(captured and self.row is captured['owner'] and self.error==captured['error']
            and not self.active and not self.controller.admission.pending
            and self.controller.admission._context()==captured['scope']
            and self.window.settings.get(self.key,[])==captured['queue'] and self._current())

    def release_retained(self,captured,canonical_queue):
        """Drop readback only after the Quit owner verifies exact durable intent.

        This method grants no canonical/source write and cannot discard unknown
        jobs. The caller owns a fresh ordered StateCommitter checkpoint proof.
        """
        if (not self.retention_current(captured)
                or canonical_queue not in (captured['before'],captured['desired'])):return False
        self.window.settings[self.key]=deepcopy(canonical_queue)
        self.row=None;self.error='';self.timer.stop()
        release=getattr(self.controller.tags,'release_source_ack',None)
        if callable(release):release(self.token)
        return True

    def flush(self):
        self.timer.stop()
        if self.error:return False
        while self.pending:
            committer=getattr(self.window,'_state_committer',None)
            if committer is not None and committer.owner.pending:
                committer.owner.drain()
                if self.error:return False
                if not self.pending:break
            if not self.active and not self.pump(allow_shutdown=True):return False
            committer=getattr(self.window,'_state_committer',None)
            if committer is None:return False
            committer.owner.drain()
            if self.error:return False
        return True

    def stop(self):self.stopping=True;self.timer.stop();self.retire_stale()
