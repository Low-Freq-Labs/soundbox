"""Durable publication of finished SoundBox analysis on the shared source lane."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer

from soundbox import analysis, analysis_jobs, local_grid, track_analysis
from soundbox.native_media import metadata_identity_proof, METADATA_PENDING
from soundbox.rekordbox_analysis_store import AnalysisStore, load_authority, prepare, retire_confirmed, load_plan
from soundbox.rekordbox_analysis_store import INITIAL_WAV_PROFILE, PLAN_FORMAT as WAV_PLAN_FORMAT
from soundbox import rekordbox_cbr_analysis_store as cbr
from soundbox.rekordbox_write import (WriteProofError, assert_rekordbox_closed, encrypted_backup,
                                      _connect, _regular, ConflictError)
from soundbox.rekordbox_cues import CueSnapshot
from soundbox.rekordbox_grid import read_grid
from soundbox.rekordbox_analysis import resolve_dat_path
from soundbox.rekordbox_db import cue_snapshot_fields

KEY='native_analysis_jobs'
DONE='native_analysis_published'
RECEIPTS='native_analysis_receipts'
CTIME_CONFLICT='Captured media identity changed'


def publication(profile):
    """Explicit write profiles; legacy intents always retain the WAV v1 route."""
    if profile == INITIAL_WAV_PROFILE:
        return SimpleNamespace(profile=profile,plan_format=WAV_PLAN_FORMAT,
            authority_file='rekordbox-analysis-authority.json',load_authority=load_authority,
            prepare=prepare,load_plan=load_plan,store=AnalysisStore,retire=retire_confirmed)
    if profile == cbr.PROFILE:
        return SimpleNamespace(profile=profile,plan_format=cbr.PLAN_FORMAT,
            authority_file='rekordbox-cbr-analysis-authority.json',load_authority=cbr.load_authority,
            prepare=cbr.prepare,load_plan=cbr.load_plan,store=cbr.CBRAnalysisStore,retire=cbr.retire_confirmed)
    raise WriteProofError('Unknown native analysis profile')


def prepared_publication(folder, profile):
    """Concrete durable plans route by their format, never the audio suffix."""
    _regular(folder/'plan.json')
    record=json.loads((folder/'plan.json').read_bytes())
    formats={WAV_PLAN_FORMAT:INITIAL_WAV_PROFILE,cbr.PLAN_FORMAT:cbr.PROFILE}
    actual=formats.get(record.get('format'))
    if actual is None or actual!=profile:
        raise WriteProofError('Prepared analysis profile differs from its durable song intent')
    return publication(actual)


def digest(record):
    return hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def publication_record(record):
    """Analysis authority remains grid/key/waveform only; cues use the cue API."""
    result=deepcopy(record);result.pop('cue_draft',None);result['first_cue']=None
    return result


def validate(queue):
    if not isinstance(queue,list) or len(queue)>256:raise ValueError('Invalid native analysis queue')
    seen=set()
    for job in queue:
        fields={'id','source','library_id','local_id','native_id','uuid','record','error'}
        if (not isinstance(job,dict) or not fields<=set(job)<=fields|{'profile','ctime_rechecked'}
                or ('profile' in job and job['profile']!=cbr.PROFILE)
                or ('ctime_rechecked' in job and job['ctime_rechecked'] is not True)
                or not all(isinstance(job[k],str) and job[k] for k in ('id','source','library_id','local_id','native_id','uuid'))
                or not isinstance(job['error'],str) or job['local_id'] in seen):
            raise ValueError('Invalid native analysis identity')
        from soundbox.native_registration_plan import _uuid, _native_id
        _uuid(job['id']);_uuid(job['uuid']);_native_id(job['native_id']);analysis.validate_result(job['record'])
        seen.add(job['local_id'])


def readback(source,tid,uuid):
    with encrypted_backup(Path(source)) as copied:
        conn=_connect(copied,readonly=True)
        try:
            cursor=conn.execute('SELECT * FROM djmdContent WHERE ID=?',(tid,));cols=[v[0] for v in cursor.description]
            rows=[dict(zip(cols,r,strict=True)) for r in cursor]
            if len(rows)!=1 or rows[0]['UUID']!=uuid:raise WriteProofError('Saved analysis identity changed')
            row=rows[0]
            def selected(table):
                # Keep the native table traversal used by the original full
                # reader: a ContentID index may reorder hot refs and equal-time
                # Memory cues. Filter in SQLite, without changing that scan.
                cursor=conn.execute(f'SELECT * FROM "{table}" NOT INDEXED WHERE ContentID=?',(tid,))
                names=[item[0] for item in cursor.description]
                # Retain exact lexical matching even with a permissive source
                # collation; the SQL predicate only bounds candidate reads.
                return [r for values in cursor if (r:=dict(zip(names,values,strict=True)))['ContentID']==tid]
            cues=selected('djmdCue');aggregates=selected('contentCue')
            if len(aggregates)>1:raise WriteProofError('Saved cue identity is ambiguous')
            key=conn.execute('SELECT ScaleName FROM djmdKey WHERE ID=?',(row['KeyID'],)).fetchone()
            snapshot=CueSnapshot(row,tuple(cues),aggregates[0] if aggregates else None)
            path=resolve_dat_path(source,row['AnalysisDataPath'])
            if path:
                _regular(path)
                if path.stat().st_size>64*1024*1024:raise WriteProofError('Analysis readback exceeds its bound')
            beats=read_grid(path.read_bytes()) if path else ()
            return dict(row=row,key=key[0] if key else '',cue_fields=cue_snapshot_fields(snapshot),
                analysis_path=str(path or ''),beats=[[b.number,b.time_ms,b.tempo/100] for b in beats])
        finally:conn.close()


class NativeAnalysisController(QObject):
    def __init__(self,window,*,factory=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=factory;self.closing=False;self.active='';self.not_before=0.0
        self._grid_revalidated=set()
        self._detection_reconciled=set()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump);self.tags.idle.connect(self.schedule)
        self.tags.changed.connect(self.discover)

    @property
    def pending(self):return self.window.settings.get(KEY,[])

    def pending_track(self,tid):
        return any(isinstance(j,dict) and j.get('local_id')==tid and not j.get('error') for j in self.pending)

    def status(self,message):
        if self.tags.analysis_update_status!=message:
            self.tags.analysis_update_status=message;self.tags.changed.emit()

    def schedule(self):
        if not self.closing:QTimer.singleShot(0,self.pump)

    def _detection_index(self):
        # A synchronous discover/pump owns this observation only until it returns.
        try:jobs=analysis_jobs.validate(self.window.settings.get(analysis_jobs.KEY,[]))
        except ValueError:return None
        return {(j['source'],j['library_id'],j['track_id']):j for j in jobs}

    def _unfinished_detection(self,track,index):
        if track is None:return False
        if index is None:return True
        lib=self.window.lib
        job=index.get((str(lib.source or ''),str(lib.source_library_id or ''),track.track_id))
        return job is not None and analysis_jobs.matches(job,lib,track)

    def discover(self):
        w=self.window
        if self.closing or not w.lib or not self.tags.verified:return
        try:validate(self.pending)
        except (ValueError,WriteProofError):return
        permissions={profile:publication(profile).load_authority(self.tags.data_root/publication(profile).authority_file,
                     w.lib.source,w.lib.source_library_id) for profile in (INITIAL_WAV_PROFILE,cbr.PROFILE)}
        if not any(permission is not None for permission in permissions.values()) and self.factory is None:return
        queued={j['local_id'] for j in self.pending};queue=list(self.pending);revalidated=[]
        # Older builds rejected valid adaptive grids after exact packet equality
        # had already passed. Revalidate that one obsolete refusal once per
        # controller lifetime; the current planner still checks every row.
        obsolete='Packet grid must equal the complete saved grid, including its exact endpoint'
        for index,job in enumerate(queue):
            if 'profile' not in job and job['error']==obsolete and job['id'] not in self._grid_revalidated:
                revalidated.append(job['id']);job={**job,'error':''};queue[index]=job
            if job['error']==CTIME_CONFLICT and 'ctime_rechecked' not in job:
                job={**job,'ctime_rechecked':True,'error':''};queue[index]=job
        done=w.settings.get(DONE,{})
        if not isinstance(done,dict):return
        detection=self._detection_index()
        for tid,track in w.lib.tracks_by_id.items():
            prep=getattr(w,'prep_controller',None)
            if prep and prep.job(tid) and prep.job(tid)['error']:continue
            if (tid in queued or track is None or track.imported or track.source_analysis_path
                    or self._unfinished_detection(track,detection)
                    or not track.source_uuid
                    or tid in getattr(w.lib,'source_binding_issues',{})):continue
            record=analysis.saved_result(w.settings.get('analysis_results',{}),track)
            if not record:continue
            suffix=track.path.suffix.lower()
            profile=cbr.PROFILE if suffix=='.mp3' else INITIAL_WAV_PROFILE
            if permissions[profile] is None and self.factory is None:continue
            effective=local_grid.effective_result(record)
            if (not effective.get('beats') or effective.get('bpm') is None or effective.get('key') is None
                    or done.get(tid)==digest(record)):continue
            if len(queue)>=256:break
            job=dict(id=str(uuid4()),source=str(w.lib.source),library_id=w.lib.source_library_id,
                local_id=tid,native_id=track.native_id,uuid=track.source_uuid,record=deepcopy(record),error='')
            if profile==cbr.PROFILE:job['profile']=profile
            queue.append(job)
            queued.add(tid)
        if queue!=self.pending:
            old=w.settings.get(KEY,[]);w.settings[KEY]=queue
            if not w._save_state():w.settings[KEY]=old;return
        self._grid_revalidated.update(revalidated)
        self.schedule()

    def pump(self):
        w=self.window
        if self.closing or w._shutting_down or not self.pending:return
        try:validate(self.pending)
        except (ValueError,WriteProofError) as exc:self.fail(str(exc));return
        self._detection_reconciled.intersection_update(j['id'] for j in self.pending)
        priority=getattr(w,'_analysis_priority_pending',lambda:False)()
        ready=[];blocked=[]
        detection=self._detection_index()
        for job in self.pending:
            if job['error']:continue
            track=w.lib.tracks_by_id.get(job['local_id']) if w.lib else None
            if self._unfinished_detection(track,detection):
                if job['id'] not in self._detection_reconciled:blocked.append(job)
            else:ready.append(job)
        selected=next(iter(ready if ready and not priority else blocked),None)
        if selected is None:
            if any(not j['error'] for j in self.pending):
                self.status('Analysis pending — finishing detection')
                if ready and priority:self.timer.start()
                return
            self.status('Analysis needs attention — '+self.pending[0]['error']);return
        if (self.tags.owned or w._refresh_worker is not None or w._state_maintenance
                or w._device_operation_owned()):
            self.timer.start();return
        reconcile_only=selected in blocked
        if not reconcile_only and (getattr(w,'_analysis_worker',None) is not None or priority):
            self.timer.start();return
        if time.monotonic()<self.not_before:self.timer.start();return
        lib=w.lib
        job=deepcopy(selected);tid=job['local_id'];track=lib.tracks_by_id.get(tid) if lib else None
        if (not w.lib or not self.tags.verified or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']
                or tid in getattr(w.lib,'source_binding_issues',{}) or track is None
                or (track.native_id,track.source_uuid)!=(job['native_id'],job['uuid'])
                or str(track.path)!=job['record']['path']):
            self.fail('Analysis belongs to a different source identity',job['id']);return
        record=analysis.saved_result(w.settings.get('analysis_results',{}),track)
        folder=self.tags.data_root/'native-analysis-plans'/job['id']
        # A newer local draft can replace an unstarted intent. Once a concrete
        # plan exists its own receipt must reconcile before admitting new data.
        if not reconcile_only and record and digest(record)!=digest(job['record']) and not folder.exists():
            old=self.pending;queue=[{**j,'record':deepcopy(record)} if j['id']==job['id'] else j for j in old]
            w.settings[KEY]=queue
            if not w._save_state():w.settings[KEY]=old
            self.schedule();return
        profile=job.get('profile',INITIAL_WAV_PROFILE);backend=publication(profile)
        authority=backend.load_authority(self.tags.data_root/backend.authority_file,w.lib.source,w.lib.source_library_id)
        if authority is None and self.factory is None:self.fail('Native analysis awaits library acceptance',job['id']);return
        confirmed=tuple(w.settings.get(RECEIPTS,[]))
        payload=publication_record(job['record'])
        def work():
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            if self.factory:
                return dict(detection_pending=True) if reconcile_only else self.factory(job)
            with metadata_identity_proof([payload]):return publish()
        def publish():
            def prepare_store():
                if (folder/'plan.json').exists():return
                if folder.exists():
                    _regular(folder/'intent.json');intent=json.loads((folder/'intent.json').read_bytes())
                    if (intent['operation_id']!=job['id'] or intent['result']!=payload
                            or intent['track_id']!=job['native_id'] or intent.get('track_uuid')!=job['uuid']
                            or intent.get('format')!=backend.plan_format):raise WriteProofError('Unknown interrupted analysis preparation')
                    allowed={'intent.json','before.db','after.db','before.xml','after.xml','ANLZ0000.DAT','ANLZ0000.EXT','ANLZ0000.2EX'}
                    if folder!=folder.resolve() or folder.stat().st_mode&0o077 or any(p.name not in allowed for p in folder.iterdir()):
                        raise WriteProofError('Unknown analysis preparation files')
                    for p in folder.iterdir():_regular(p);p.unlink()
                    folder.rmdir()
                backend.prepare(authority,job['native_id'],job['uuid'],payload,folder,
                        data_root=self.tags.data_root,operation_id=job['id'])
            def intended_store():
                selected_backend=prepared_publication(folder,profile)
                store=selected_backend.store(authority,folder)
                plan=store.plan  # The constructor has already verified this exact full plan.
                if (plan['operation_id'],plan['source'],plan['library_id'],plan['track_id'],plan['track_uuid'],digest(plan['result']))!=(
                        job['id'],job['source'],job['library_id'],job['native_id'],job['uuid'],digest(payload)):
                    raise WriteProofError('Prepared analysis differs from its durable song intent')
                return store
            if reconcile_only:
                if not (folder/'plan.json').exists():return dict(detection_pending=True)
                store=intended_store()
                if store.recover()!='committed':return dict(detection_pending=True)
            else:
                prepare_store();store=intended_store()
                try:store.apply()
                except ConflictError as exc:
                    if str(exc)!='Saved-component source changed since review' or not store.discard_unstarted():raise
                    backend.prepare(authority,job['native_id'],job['uuid'],payload,folder,
                            data_root=self.tags.data_root,operation_id=job['id'])
                    store=intended_store();store.apply()
            selected_backend=prepared_publication(folder,profile)
            selected_backend.retire(store,confirmed)
            result=readback(job['source'],job['native_id'],job['uuid'])
            w._prepare_grid_cache(tid)
            track_analysis.confirm_published_grid(w._track_analysis_dir,tid,track.path,
                result['beats'],local_grid.effective_result(job['record'])['beats'])
            return result
        def completed(worker):
            self.active=''
            if worker.error:
                if worker.error==METADATA_PENDING:
                    self.not_before=time.monotonic()+2
                    self.status('Verifying audio…')
                    if not self.closing:self.timer.start()
                    return
                if worker.error=='Close rekordbox and its background agent before editing':
                    self.not_before=time.monotonic()+2
                    self.status('Analysis pending — quit rekordbox to save')
                    if not self.closing:self.timer.start()
                else:self.fail(worker.error,job['id'])
                return
            result=worker.result
            if result.get('detection_pending'):
                self._detection_reconciled.add(job['id'])
                self.status('Analysis pending — finishing detection')
                self.schedule();return
            if result.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Analysis pending — quit rekordbox to save')
                if not self.closing:self.timer.start()
                return
            if not w.lib or str(w.lib.source)!=job['source'] or w.lib.source_library_id!=job['library_id']:
                self.fail('Analysis saved in the original library; switch back to finish',job['id']);return
            old=dict(w.settings)
            prep=getattr(w,'prep_controller',None)
            try:
                w.settings[KEY]=[j for j in self.pending if j['id']!=job['id']]
                w.settings[DONE]={**w.settings.get(DONE,{}),tid:digest(job['record'])}
                w.settings[RECEIPTS]=list(dict.fromkeys([*w.settings.get(RECEIPTS,[]),job['id']]))
                if prep:prep.ack_initial(tid,job['record'])
                if not w._save_state():raise OSError('Analysis saved; local receipt could not be saved')
            except (OSError,ValueError) as exc:
                w.settings.clear();w.settings.update(old);self.fail(str(exc),job['id']);return
            current=w.lib.tracks_by_id.get(tid)
            if current and (current.native_id,current.source_uuid)==(job['native_id'],job['uuid']):
                row=result['row']
                updated=replace(current,bpm=f"{row['BPM']/100:.2f}",key=result['key'],
                    bitrate=str(row['BitRate']),total_time=int(row['Length']),
                    info={**current.info,'sampleRate':str(row['SampleRate']),'bitDepth':str(row['BitDepth'])},
                    source_analysis_path=result['analysis_path'],**result['cue_fields'])
                w.lib.tracks_by_id[tid]=updated
                if not self.closing and not w._shutting_down:
                    w._invalidate_rows([tid]);w._refresh_analysis_row(tid)
                    w.grid_controller.configure()
                    if w.player_bar.current_track()==tid and not (prep and prep.handles(tid)):
                        w.player_bar._analysis_parts['provenance']={**w.player_bar._analysis_parts.get('provenance',{}),'grid':'rekordbox'}
                        w.player_bar._analysis_parts['provenance'].pop('user_data',None)
                        w.player_bar.refresh_live_grid(tid,current.path,result['beats'],row['BPM']/100)
                    if prep:prep.refresh(tid,result)
                    w._sync_grid();w._sync_cue_editing();w._cache_library()
            self.not_before=0.0
            self.status('Updating song…' if self.pending else '')
            finish=getattr(w,'_finish_edit_status',None)
            if not self.pending and finish:finish(quiet=True)
            self.schedule()
        self.active=tid
        if not self.tags.run_analysis_job(work,completed):self.active='';self.timer.start()
        else:self.status('Verifying audio…' if job.get('ctime_rechecked') else 'Updating song…')

    def fail(self,message,job_id=None):
        self.timer.stop();w=self.window;can_continue=False
        try:
            validate(self.pending);queue=list(self.pending)
            index=next((i for i,j in enumerate(queue) if j['id']==job_id),None) if job_id else (0 if queue else None)
            target=queue[index] if index is not None else None
            if target is not None:
                queue[index]={**target,'error':str(message)[:4096]};w.settings[KEY]=queue;w._save_state()
            can_continue=any(not j['error'] for j in queue)
        except (ValueError,WriteProofError):pass
        self.status('Analysis needs attention — '+str(message))
        w._flash_status('Couldn’t share analysis: '+str(message),15)
        if can_continue:self.schedule()

    def retry(self):
        try:validate(self.pending)
        except (ValueError,WriteProofError) as exc:self.fail(str(exc));return
        old=self.pending;queue=[{**job,'error':''} for job in old]
        self.window.settings[KEY]=queue
        if not self.window._save_state():self.window.settings[KEY]=old;return
        self.not_before=0.0;self.schedule()

    def stop(self):self.closing=True;self.timer.stop()
