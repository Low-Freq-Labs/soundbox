"""Ordered native prep intentions and acknowledgements on the shared state worker."""
from collections import deque

from PySide6.QtCore import QObject, QEventLoop, QTimer

from soundbox import analysis, local_cues, prep_edits as edits
from soundbox.state_io import StateChangedError


def _binding(window, tid):
    lib=window.lib
    track=lib.tracks_by_id.get(tid) if lib else None
    return ((str(lib.source),lib.source_library_id,lib.source_kind,
             track.native_id,track.source_uuid,str(track.path),str(track.native_path)) if track else None)


class PrepStateLane(QObject):
    """Immediate track-bound cue overlays, then ordered durable intentions."""
    def __init__(self, controller):
        super().__init__(controller)
        self.controller=controller;self.window=controller.window
        self.queue:deque[dict]=deque()
        self.staged={};self.retired={};self.failed=[];self.overlays={}
        self.active=False
        self.timer=QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        self.controller.tags.idle.connect(self.schedule)
        self.controller.tags.changed.connect(self.schedule)

    @property
    def enabled(self):return callable(getattr(self.window,'_commit_tag_state',None))

    @property
    def pending(self):return bool(self.queue) or self.active

    def job(self, tid):
        rows=[row for row in self.queue if row['kind']=='admit' and row['tid']==tid]
        row=self.overlays.get(tid) or (rows[-1] if rows else None)
        if row:
            if not self._bound(row):
                raise ValueError('Prep edits belong to the original track and state; intentions preserved')
            return rows[-1]['job'] if rows else row['job']
        return self.controller.job(tid)

    def _bound(self, row):
        repository=row['repository']
        return (self.window._state_repository is repository
            and (repository.generation,repository.area.generation)==row['epoch']
            and row['binding']==_binding(self.window,row['tid']))

    def cues(self, tid):
        row=self.overlays.get(tid)
        return [dict(cue) for cue in row['job']['cues']] if row and self._bound(row) else None

    def admit(self, track, before, after, *, cue_only):
        repository=self.window._state_repository
        row=dict(kind='admit',tid=track.track_id,binding=_binding(self.window,track.track_id),
            repository=repository,epoch=(repository.generation,repository.area.generation),
            before=tuple(op['id'] for op in before['operations']) if before else (),
            job=after,cue_only=cue_only)
        tail=self.queue[-1] if self.queue else None
        if (tail and tail['kind']=='admit' and tail['tid']==track.track_id
                and tail['job']['id']==after['id'] and tail['binding']==row['binding']
                and tail['epoch']==row['epoch'] and tail['repository'] is repository
                and not (self.active and self.queue[0] is tail)):
            tail['job']=after;tail['cue_only']=tail['cue_only'] and cue_only;row=tail
        else:self.queue.append(row)
        if cue_only:self.overlays[track.track_id]=row
        self.staged[track.track_id]=after
        if cue_only:self.controller.publish_cues(track.track_id)
        self.schedule();return True

    def acknowledge(self, job_id, operation_id):
        if any(row['kind']=='admit' and row['job']['id']==job_id for row in self.queue):
            self.retired.setdefault(job_id,set()).add(operation_id)

    def save(self, tid, queue, committed, failed):
        """An older in-flight source result precedes newer queued gestures."""
        if self.active:
            failed(ValueError('A prep save is already being confirmed'));return
        repository=self.window._state_repository
        self.queue.appendleft(dict(kind='save',tid=tid,binding=_binding(self.window,tid),
            repository=repository,epoch=(repository.generation,repository.area.generation),
            value=queue,committed=committed,failed=failed))
        self.pump()

    def schedule(self):
        if self.queue and not self.active:self.timer.start(0)

    def _current(self, row, previous):
        w=self.window
        return (self._bound(row)
            and w.settings.get(edits.KEY) is previous)

    def _compose(self, row):
        controller=self.controller;tid=row['tid'];after=row['job']
        current=controller.job(tid)
        if current and current['id']!=after['id']:
            raise ValueError('Prep intentions changed before this gesture could be saved')
        if current and not controller._job_matches(
                current,self.window.lib,self.window.lib.tracks_by_id.get(tid)):
            raise ValueError('Prep intentions belong to a different source; intentions preserved')
        retired=self.retired.get(after['id'],set())
        expected=tuple(value for value in row['before'] if value not in retired)
        actual=tuple(op['id'] for op in current['operations']) if current else ()
        if actual!=expected:
            raise ValueError('Prep intention order changed before this gesture could be saved')
        added=after['operations'][len(row['before']):]
        merged={**after,'operations':[*((current or {}).get('operations',())),*added],
                'prepared':current['prepared'] if current else None,
                'error':current['error'] if current else after['error']}
        if current:
            # A detector/identity proof may bind this same job while a newer
            # gesture waits for admission. That proof owns the audio metadata;
            # the queued gesture owns only its component and operation suffix.
            merged.update(sha256=current['sha256'],epoch=current['epoch'])
            if 'identity_pending' in current:merged['identity_pending']=current['identity_pending']
            else:merged.pop('identity_pending',None)
        edits.validate([{**merged,'grid':[]}]) if row['cue_only'] else edits.validate([merged])
        queue=([merged if job['track_id']==tid else job for job in controller.pending]
               if current else [*controller.pending,merged])
        edits.validate([{**job,'grid':[]} for job in queue])
        return queue

    def retry(self):
        if self.pending or not self.failed:return False
        retry=[];retained=[]
        for row,error in self.failed:
            if isinstance(error,StateChangedError) or getattr(error,'durable',False):retained.append((row,error))
            else:retry.append(row)
        self.failed=retained
        for row in retry:
            self.queue.append(row);self.staged[row['tid']]=row['job']
            self.controller.errors.pop(row['tid'],None)
        if retry:self.schedule()
        return bool(retry)

    def _retire(self, row):
        if self.queue and self.queue[0] is row:self.queue.popleft()
        self.staged={entry['tid']:entry['job'] for entry in self.queue if entry['kind']=='admit'}
        live={entry['job']['id'] for entry in self.queue if entry['kind']=='admit'}
        live.update(entry['job']['id'] for entry,_error in self.failed)
        self.retired={key:value for key,value in self.retired.items() if key in live}
        self.active=False
        self.schedule();self.controller.schedule()

    def pump(self):
        if self.active or not self.queue:return
        row=self.queue[0];controller=self.controller;w=self.window
        if (controller.tags.busy or controller.tags.checking
                or controller.tags.state_commit_owned or getattr(w,'_state_publication',False)):
            self.timer.start(25);return
        previous=w.settings.get(edits.KEY)
        def failed(error):
            if row['kind']=='admit':self.failed.append((row,error))
            self._retire(row)
            if row['kind']=='save':row['failed'](error)
            else:
                # Keep exact captured operations for an explicit retry; a
                # durable-but-stale publication must never be blindly replayed.
                controller.errors[row['tid']]=str(error)
                dependents=[entry for entry in self.queue if entry['kind']=='admit'
                            and entry['job']['id']==row['job']['id']]
                for entry in dependents:
                    self.queue.remove(entry);self.failed.append((entry,error))
                self.staged={entry['tid']:entry['job'] for entry in self.queue if entry['kind']=='admit'}
                controller._status('Prep edits need attention: '+str(error))
                w._flash_status('Cue/grid intention could not be confirmed: '+str(error),15)
                controller.publish_cues(row['tid'])
        try:
            if not self._current(row,previous):
                raise ValueError('Prep intentions belong to the original library; reload it before retrying')
            queue=self._compose(row) if row['kind']=='admit' else row['value']
            replacements={('settings',edits.KEY):queue}
            record=None
            if row['kind']=='admit' and row['cue_only'] and not row['job']['native_ready']:
                track=w.lib.tracks_by_id[row['tid']]
                record=w._analysis_result(track)
                if record:
                    if row['job']['sha256'] and record['sha256']!=row['job']['sha256']:
                        raise ValueError('Prep edits belong to different audio bytes')
                    prior=record.get('cue_draft') or {}
                    draft=dict(origin='manual',base_sha256=record['sha256'],
                        revision=prior.get('revision',0)+1,cues=edits.cues(row['job']['cues']))
                    local_cues.validate({**record,'cue_draft':draft})
                    replacements[('settings','analysis_results',analysis.result_key(track.path),'cue_draft')]=draft
        except (ValueError,OSError) as exc:
            failed(exc);return
        def committed():
            if row['kind']=='admit' and self.overlays.get(row['tid']) is row:
                self.overlays.pop(row['tid'],None)
            self._retire(row)
            if row['kind']=='save':row['committed']()
            else:controller.refresh(row['tid'],cue_only=row['cue_only'])
        self.active=True
        accepted=w._commit_tag_state(replacements,(),committed,failed,
            current=lambda:self._current(row,previous) and (record is None or
                w._analysis_result(w.lib.tracks_by_id[row['tid']]) is record))
        if not accepted:
            self.active=False;self.timer.start(25)

    def drain(self):
        """Flush already accepted intentions before source admission shuts down."""
        if not self.pending:return
        loop=QEventLoop();timer=QTimer();timer.setInterval(10)
        timer.timeout.connect(lambda:loop.quit() if not self.pending else self.schedule())
        timer.start();self.schedule()
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents);timer.stop()
