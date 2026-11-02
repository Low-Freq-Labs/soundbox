"""Immediate, durable import prep with serialized native confirmation."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer

from soundbox import analysis, local_grid, local_cues, prep_edits as edits, rekordbox_db, track_analysis
from soundbox.native_paths import validate_track_path
from soundbox.rekordbox_cues import CueEdit
from soundbox.rekordbox_grid import GridEdit, read_grid
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed, _regular, _digest


class PrepEditController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window=window; self.tags=window.tag_controller
        self.busy=False; self.closing=False; self.errors={}
        try:
            edits.validate(self.pending)
            self.tags.prep_priority=any(j['prepared'] is not None for j in self.pending)
        except ValueError:self.tags.prep_priority=True
        self.timer=QTimer(self); self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule)
        self.tags.changed.connect(self.schedule)

    @property
    def pending(self):return self.window.settings.get(edits.KEY,[])

    def job(self, tid):
        queue=self.pending
        return next((j for j in queue if isinstance(j,dict) and j.get('track_id')==tid),None) if isinstance(queue,list) else None

    def handles(self, tid):
        lib=self.window.lib; track=lib.tracks_by_id.get(tid) if lib else None
        return bool(track and (self.job(tid) or track.imported or track.source_native_id
                              or lib.source_kind=='rekordbox-db' and track.source_uuid))

    def reason(self, tid, *, grid=False):
        if self.closing or self.window._shutting_down:return 'Finishing library edits…'
        if self.window._state_maintenance:return 'Restoring library state…'
        try:edits.validate(self.pending)
        except ValueError as exc:return str(exc)
        job=self.job(tid)
        error=self.errors.get(tid) or (job or {}).get('error')
        if error:return error
        issue=getattr(self.window.lib,'source_binding_issues',{}).get(tid) if self.window.lib else None
        if issue:return str(issue)
        publisher=getattr(self.window,'native_analysis_controller',None)
        failed=next((j['error'] for j in publisher.pending if j.get('local_id')==tid and j.get('error')), '') if publisher else ''
        if failed:return failed
        if grid and not self.grid_view(tid):return 'Load a track with a beat grid before editing it'
        return ''

    def grid_view(self, tid):
        job=self.job(tid)
        if job and job['grid']:return job['grid'], job['grid'][0][2]
        w=self.window; track=w.lib.tracks_by_id.get(tid) if w.lib else None
        record=w._analysis_result(track) if track else None
        if track and track.source_analysis_path:
            pb=getattr(w,'player_bar',None)
            if pb and pb.current_track()==tid and pb._analysis_parts.get('beats'):
                rows=pb._analysis_parts['beats'];return rows,rows[0][2]
            return None
        effective=local_grid.effective_result(record)
        if effective and effective.get('beats'):return effective['beats'],effective['bpm']
        pb=getattr(w,'player_bar',None)
        if pb and pb.current_track()==tid and pb.cue_panel._loaded and pb._analysis_parts.get('beats'):
            rows=pb._analysis_parts['beats'];return rows,rows[0][2]
        return None

    def cue_rows(self, tid):
        job=self.job(tid)
        if job:return deepcopy(job['cues'])
        track=self.window.lib.tracks_by_id.get(tid) if self.window.lib else None
        if track and track.source_analysis_path:return deepcopy(track.source_cues or [])
        record=self.window._analysis_result(track) if track else None
        return local_cues.cues(record) if record else []

    def _new(self, track):
        w=self.window; record=w._analysis_result(track) if not track.source_analysis_path else None; pb=w.player_bar
        epoch=analysis.identity(track.path.stat())
        if record and epoch!=record['epoch'] and not (
                pb.current_track()==track.track_id and pb._analysis_parts.get('_detected_sha256')==record['sha256']
                and pb._analysis_parts.get('_detected_epoch')==epoch):
            raise ValueError('Loaded audio identity changed; reload this track before editing')
        view=self.grid_view(track.track_id)
        job=dict(version=2 if track.source_analysis_path else 1,id=str(uuid4()),track_id=track.track_id,path=str(track.path),epoch=epoch,
            native_ready=bool(track.source_analysis_path),
            sha256=record['sha256'] if record else '',duration=record['duration'] if record else pb._dur/1000,
            source=str(w.lib.source) if w.lib.source_kind=='rekordbox-db' else '',
            library_id=w.lib.source_library_id if w.lib.source_kind=='rekordbox-db' else '',
            grid=deepcopy(view[0]) if view else [],cues=edits.cues(track.source_cues),operations=[],prepared=None,error='')
        # A requested first-beat Memory cue is a real pending cue intention too.
        for cue in local_cues.cues(record) if record and not track.source_analysis_path else []:
            job=edits.operation(job,'cue','add',page=cue['kind'],slot=cue['slot'],position=cue['position_ms'])
        return job

    def _record(self, record, job, *, increment=True):
        if record is None or job.get('native_ready'):return record
        if job['sha256'] and record['sha256']!=job['sha256']:raise ValueError('Prep edits belong to different audio bytes')
        result=deepcopy(record)
        ids=[op['id'] for op in job['operations'] if op['kind']=='grid']
        if ids:
            prior=result.get('grid_draft') or {}
            result['grid_draft']=dict(origin='manual',base_sha256=record['sha256'],
                revision=prior.get('revision',0)+(1 if increment or not prior else 0),
                beats=deepcopy(job['grid']),bpm=job['grid'][0][2])
            result['prep_grid_ids']=ids
        if any(op['kind']=='cue' for op in job['operations']):
            prior=result.get('cue_draft') or {}
            result['cue_draft']=dict(origin='manual',base_sha256=record['sha256'],
                revision=prior.get('revision',0)+(1 if increment or not prior else 0),cues=deepcopy(job['cues']))
        analysis.validate_result(result)
        return result

    def _request(self, tid, kind, operation, value=0, **cue):
        reason=self.reason(tid,grid=kind=='grid')
        if reason:self.window._flash_status(reason,10);return False
        w=self.window; previous=deepcopy(w.settings)
        try:
            track=w.lib.tracks_by_id[tid]; old=self.job(tid); job=deepcopy(old or self._new(track))
            after=edits.operation(job,kind,operation,value,**cue)
            if after==job:return True
            record=w._analysis_result(track); updated=self._record(record,after)
            w.settings[edits.KEY]=[after if j['track_id']==tid else j for j in self.pending] if old else [*self.pending,after]
            if updated is not None:w.settings['analysis_results']={**w.settings.get('analysis_results',{}),analysis.result_key(track.path):updated}
            if not w._save_state():raise OSError('The edit could not be saved; check available space and retry')
        except (OSError,ValueError,RuntimeError) as exc:
            w.settings.clear();w.settings.update(previous);self.errors[tid]=str(exc)
            w._flash_status(str(exc),15);w._sync_grid();w._sync_cue_editing();return False
        self.refresh(tid);self.schedule();return True

    def request_grid(self, tid, operation, value):return self._request(tid,'grid',operation,value)
    def request_cue(self, tid, page, slot, operation, position):
        return self._request(tid,'cue',operation,page=page,slot=slot,position=position)

    def attach_result(self, track, record):
        job=self.job(track.track_id)
        if job is None or job.get('native_ready'):return record
        job=deepcopy(job)
        if (job['sha256'] and job['sha256']!=record['sha256'] or
                not job['sha256'] and job['epoch']!=record['epoch']):
            job['error']='Audio changed while prep edits were waiting; intentions preserved'
            self.window.settings[edits.KEY]=[job if j['track_id']==track.track_id else j for j in self.pending]
            return record
        job['sha256']=record['sha256'];job['epoch']=record['epoch']
        self.window.settings[edits.KEY]=[job if j['track_id']==track.track_id else j for j in self.pending]
        return self._record(record,job,increment=False)

    def ack_initial(self, tid, record):
        ids=set(record.get('prep_grid_ids',[])); job=self.job(tid)
        if not job or not ids:return
        job=deepcopy(job);job['operations']=[op for op in job['operations'] if op['id'] not in ids]
        self.window.settings[edits.KEY]=[job if j['track_id']==tid else j for j in self.pending
                                       if j['track_id']!=tid or job['operations']]

    def refresh(self, tid, result=None):
        w=self.window;track=w.lib.tracks_by_id.get(tid) if w.lib else None
        if track is None or self.closing or w._shutting_down:return
        w._invalidate_rows([tid]); view=self.grid_view(tid) if self.handles(tid) else None
        if view:w._update_browser_bpm(tid,f'{view[1]:.2f}')
        pb=w.player_bar
        if pb.current_track()==tid:
            pb.detected_analysis=w._analysis_result(track)
            if result and not self.job(tid):
                provenance=pb._analysis_parts.get('provenance',{})
                pb._analysis_parts['provenance']={k:v for k,v in provenance.items() if k!='user_data'}
                pb._analysis_parts['provenance']['grid']='rekordbox'
                pb.refresh_live_grid(tid,track.path,result['beats'],result['row']['BPM']/100)
            elif view:pb.refresh_live_grid(tid,track.path,*view)
            pb.refresh_live_cues(tid,track.path,self.cue_rows(tid) if self.handles(tid) else track.source_cues)
        w._sync_grid();w._sync_cue_editing()

    def schedule(self):
        if not self.closing and not self.timer.isActive():self.timer.start(0)

    def _status(self, message):
        if self.tags.prep_update_status!=message:
            self.tags.prep_update_status=message;self.tags.changed.emit()

    def _fail(self, tid, error):
        job=self.job(tid)
        if job:
            queue=deepcopy(self.pending)
            next(j for j in queue if j['track_id']==tid)['error']=str(error)[:4096]
            self.window.settings[edits.KEY]=queue;self.window._save_state()
        self.errors[tid]=str(error);self._status('Prep edits need attention: '+str(error))
        self.window._flash_status('Couldn’t save prep edits: '+str(error),15);self.refresh(tid)

    def _stores(self, lib, track):
        validate_track_path(track)
        return self.window.grid_controller._store_for(lib,track),self.tags._cue_store_factory(lib,track.native_id)

    @staticmethod
    def _identity(snapshot, track):
        if (snapshot.track['ID'],snapshot.track['UUID'],snapshot.track['FolderPath'])!=(track.native_id,track.source_uuid,str(track.native_path)):
            raise WriteProofError('Prep source identity changed')

    def _read(self, grid, cues, track):
        g,c=grid.snapshot(),cues.snapshot();self._identity(g,track);self._identity(c,track)
        if g.track!=c.track:raise WriteProofError('Prep source changed during readback')
        return g,c,dict(row=g.track,beats=[[b.number,b.time_ms,b.tempo/100] for b in read_grid(g.dat)],
                        cue_fields=rekordbox_db.cue_snapshot_fields(c))

    def _verify_receipt(self,lib,track,request,kind,snapshot):
        root=self.tags._write_root(lib);path=root/('grid-completed.json' if kind=='grid' else 'last-result.json')
        _regular(path);receipt=json.loads(path.read_bytes());identity=lib.source.stat()
        after_revision=receipt.get('after_revision')
        if kind=='grid':
            # Grid receipts bind the actual target row and DAT/EXT digests;
            # only cue receipts carry an explicit after_revision field.
            try:
                state=dict(track=receipt['track_after'],dat=receipt['files']['DAT']['after'],ext=receipt['files']['EXT']['after'])
                if receipt.get('content_file_after') is not None:state['content_file']=receipt['content_file_after']
                after_revision=_digest(state)
            except (KeyError,TypeError):after_revision=None
        if (receipt.get('format')!=('soundbox-native-grid-v1' if kind=='grid' else 'soundbox-native-cues-v1')
                or receipt.get('source')!=str(lib.source) or receipt.get('library_id')!=lib.source_library_id
                or receipt.get('track_id')!=track.native_id or receipt.get('request')!=json.loads(json.dumps(asdict(request)))
                or receipt.get('source_file_id')!=[identity.st_dev,identity.st_ino]
                or receipt.get('outcome')!='committed' or after_revision!=snapshot.revision):
            raise WriteProofError('Prep save needs reconciliation; intentions preserved')

    def pump(self):
        w=self.window
        if self.closing or w._shutting_down or self.busy:return
        try:edits.validate(self.pending)
        except ValueError as exc:
            self.tags.prep_priority=True;self._status(str(exc));return
        self.tags.prep_priority=any(j['prepared'] is not None for j in self.pending)
        if not self.pending:
            self._status('');self.window._finish_edit_status(quiet=True);return
        selected=next((j for j in self.pending if j['prepared'] is not None),None)
        selected=selected or next((j for j in self.pending if not j['error']),None)
        if selected is None:return
        job=deepcopy(selected);tid=job['track_id']
        repair=(job['version']==1 and job['prepared'] is not None
                and job['error']=='Saved prep readback differs from the intended component')
        if job['error'] and not repair:return
        if (self.tags.busy or self.tags.checking or self.tags.grid_waiting and not self.tags.prep_priority
                or w._refresh_worker is not None or w._state_maintenance or w._device_operation_owned()):
            self.timer.start(2000);return
        lib=w.lib;track=lib.tracks_by_id.get(tid) if lib else None
        if not lib or lib.source_kind!='rekordbox-db' or not self.tags.verified:
            self.timer.start(2000);return
        if not track or track.imported or not track.source_analysis_path:
            self.timer.start(2000);return
        if ((job['source'],job['library_id']) not in (('',''),(str(lib.source),lib.source_library_id))
                or str(track.path)!=job['path'] or tid in getattr(lib,'source_binding_issues',{})):
            self._fail(tid,'Prep edits belong to a different source; intentions preserved');return
        op=job['operations'][0]
        def work():
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            grid,cue_store=self._stores(lib,track);g,c,result=self._read(grid,cue_store,track)
            before=job['prepared']
            if job['version']==1 and (before is None or op['kind']=='grid'):
                committed=False
                if before is not None and g.revision!=before['request']['revision']:
                    request=GridEdit(**{**before['request'],'nudges':tuple(before['request']['nudges'])})
                    self._verify_receipt(lib,track,request,'grid',g);committed=True
                migration=dict(rows=result['beats'],cue_rows=result['cue_fields']['source_cues'],committed=committed)
                edits.upgrade_native(job,**migration)
                return dict(upgrade=migration)
            if before is None:
                values=result['beats'] if op['kind']=='grid' else edits.cues(result['cue_fields']['source_cues'])
                if edits.digest(values)!=op['before']:raise WriteProofError('Saved prep component changed; intentions preserved')
                if op['kind']=='grid':
                    desired=edits.grid_edit(values,op['operation'],op['value'],job['duration'],native=job['version']==2)
                    request=GridEdit(track.native_id,track.source_uuid,g.revision,op['operation'],op['value'])
                else:
                    desired=edits.cue_edit(values,op['page'],op['slot'],op['operation'],op['position'])
                    ref=next((r for r in result['cue_fields']['source_cue_refs'] if (r['kind'],r['slot'])==(op['page'],op['slot'])),None)
                    request=CueEdit(track.native_id,track.source_uuid,c.revision,op['page'],op['slot'],op['operation'],op['position'],
                                    ref['id'] if ref and op['operation']=='delete' else '',ref['uuid'] if ref and op['operation']=='delete' else '')
                if edits.digest(desired)!=op['after']:raise WriteProofError('Prep operation differs from the saved intention')
                return dict(prepared=dict(operation_id=op['id'],kind=op['kind'],request=json.loads(json.dumps(asdict(request)))))
            if before['operation_id']!=op['id'] or before['kind']!=op['kind']:raise WriteProofError('Prepared prep identity changed')
            snapshot=g if op['kind']=='grid' else c
            request=(GridEdit if op['kind']=='grid' else CueEdit)(**before['request'])
            if op['kind']=='grid':request=replace(request,nudges=tuple(request.nudges))
            if (request.track_id,request.track_uuid)!=(track.native_id,track.source_uuid):
                raise WriteProofError('Prepared prep target changed; intentions preserved')
            if snapshot.revision==request.revision:
                (grid if op['kind']=='grid' else cue_store).apply(request)
            else:
                self._verify_receipt(lib,track,request,op['kind'],snapshot)
            _g,_c,result=self._read(grid,cue_store,track)
            values=result['beats'] if op['kind']=='grid' else edits.cues(result['cue_fields']['source_cues'])
            if edits.digest(values)!=op['after']:raise WriteProofError('Saved prep readback differs from the intended component')
            w._prepare_grid_cache(tid)
            track_analysis.update_grid_source(w._track_analysis_dir,tid,track.path,result['beats'])
            return result
        def completed(worker):
            self.busy=False
            if worker.error:self._fail(tid,worker.error);return
            result=worker.result
            if result.get('waiting'):self.timer.start(2000);return
            current=self.job(tid)
            if not current or not current['operations'] or current['operations'][0]['id']!=op['id']:
                self._fail(tid,'Prep queue changed while saving; intentions preserved');return
            latest=w.lib.tracks_by_id.get(tid) if w.lib else None
            if not latest or (str(w.lib.source),w.lib.source_library_id,latest.native_id,latest.source_uuid,str(latest.path))!=(str(lib.source),lib.source_library_id,track.native_id,track.source_uuid,str(track.path)):
                self._fail(tid,'Prep saved in the original library; intentions preserved for reconciliation');return
            previous=deepcopy(w.settings);queue=deepcopy(self.pending);current=next(j for j in queue if j['track_id']==tid)
            if 'upgrade' in result:
                try:
                    upgraded=edits.upgrade_native(current,**result['upgrade'])
                except ValueError as exc:self._fail(tid,str(exc));return
                queue=[upgraded if j['track_id']==tid else j for j in queue]
            elif 'prepared' in result:current['prepared']=result['prepared']
            else:current['operations'].pop(0);current['prepared']=None
            queue=[j for j in queue if j['operations']];w.settings[edits.KEY]=queue
            if not w._save_state():
                w.settings.clear();w.settings.update(previous);self._fail(tid,'Prep saved state could not be confirmed locally');return
            self.tags.prep_priority=any(j['prepared'] is not None for j in queue)
            if 'upgrade' in result:
                self.errors.pop(tid,None);self.refresh(tid)
            elif 'prepared' not in result:
                w.lib.tracks_by_id[tid]=replace(latest,bpm=f"{result['row']['BPM']/100:.2f}",**result['cue_fields'])
                self.tags.lib=w.lib
                self.refresh(tid,result)
                if not queue:w.grid_controller.configure();w._cache_library()
            self.schedule()
        self.busy=True
        if not self.tags.run_prep_job(work,completed):self.busy=False;self.timer.start(2000)
        else:self._status('Updating beatgrid…' if op['kind']=='grid' else 'Updating cue…')

    def retry(self):
        self.errors.clear();queue=deepcopy(self.pending)
        for job in queue:job['error']=''
        old=self.window.settings.get(edits.KEY,[]);self.window.settings[edits.KEY]=queue
        if not self.window._save_state():self.window.settings[edits.KEY]=old;return
        self.schedule()

    def stop(self):self.closing=True;self.timer.stop()
