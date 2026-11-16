"""Direct file imports use durable intentions on the shared native source lane."""
from copy import deepcopy
from types import SimpleNamespace
import time
from pathlib import Path
from uuid import UUID,uuid5

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from .task_owner import DeferredCall

from desktop.native_timing import phase
from soundbox import rekordbox_db, shared_imports as jobs, wave_conversion, analysis, analysis_jobs, imports,media_conversion_jobs
from soundbox.rekordbox_registration_store import RegistrationStore, load_authority
from soundbox.rekordbox_playlist_store import (PlaylistStore, load_authority as playlist_authority,
    request_for, validate_request as validate_playlist_request)
from soundbox.native_registration_plan import validate_request as validate_registration_request
from soundbox.native_media import metadata_identity_proof, METADATA_PENDING
from soundbox.rekordbox_write import ConflictError,WriteProofError, assert_rekordbox_closed


class SharedImportController(QObject):
    def __init__(self, window, *, registration_factory=None, playlist_factory=None, loader=None):
        super().__init__(window)
        self.window=window;self.tags=window.tag_controller;self.closing=False;self.cancelling=False
        self.registration_factory=registration_factory;self.playlist_factory=playlist_factory
        self.loader=loader or rekordbox_db.load_live
        self.not_before=0.0
        self.conversion_tracks=set()
        self._analysis_deferred=set()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump)
        self._pump_wakeup=DeferredCall(lambda:self.pump(),self)
        self.tags.idle.connect(self.schedule)

    @property
    def pending(self):
        return self.window.settings.get(jobs.KEY,[])

    def queue_error(self):
        try:jobs.validate(self.pending)
        except (WriteProofError,KeyError,TypeError,ValueError) as exc:return str(exc)
        return ''

    def converting(self, track_id):return track_id in self.conversion_tracks

    @property
    def source_priority(self):
        if (self.queue_error() or not self.pending or self.pending[0]['error']
                or self.pending[0]['membership'] is None):return False
        try:local=self._local_analysis(self.pending[0])
        except ValueError:return False
        return not (local or self._analysis_priority())

    def _analysis_priority(self):
        w=self.window
        return getattr(w,'_analysis_source_priority',getattr(w,'_analysis_priority_pending',lambda:False))()

    def _local_analysis(self, job):
        pending=self.window.settings.get(analysis_jobs.KEY,[])
        analysis_jobs.validate(pending)
        identities={(t['id'],t['path']) for t in job['tracks']}
        return [value for value in pending
            if (value['source'],value['library_id'])==(job['source'],job['library_id'])
            and (value['track_id'],value['path']) in identities]

    def status(self, text):
        if self.tags.registration_update_status!=text:
            previous=self.tags.registration_update_status
            self.tags.registration_update_status=text;self.tags.changed.emit()
            if not text and getattr(self.window,'_prep_update_message',None)==previous:
                self.window._prep_update_message='';self.window._set_status_text('Ready')

    def progress_text(self):
        return ('Updating playlist…' if self.pending and self.pending[0]['destination']['kind']=='native'
                else 'Adding songs…')

    def schedule(self):
        if not self.closing:self._pump_wakeup.schedule()

    def _registration(self, lib):
        if self.registration_factory:return self.registration_factory(lib)
        authority=load_authority(self.tags.data_root/'rekordbox-registration-authority.json',lib.source,lib.source_library_id)
        if authority is None:raise WriteProofError('Native import registration is awaiting library acceptance')
        return RegistrationStore(authority,self.tags._write_root(lib))

    def _playlist(self, lib):
        if self.playlist_factory:return self.playlist_factory(lib)
        authority=playlist_authority(self.tags.data_root/'rekordbox-playlist-authority.json',lib.source,lib.source_library_id)
        if authority is None:raise WriteProofError('Native playlist editing is not enabled for this library')
        return PlaylistStore(authority,self.tags._write_root(lib))

    def pump(self):
        w=self.window
        if self.closing or self.cancelling or w._shutting_down or not self.pending:return
        error=self.queue_error()
        if error:self.status(error);return
        self._analysis_deferred.intersection_update(value['id'] for value in self.pending)
        job_index=0;job=deepcopy(self.pending[0])
        try:head_analysis=self._local_analysis(job)
        except ValueError as exc:
            self.status('Import waiting for analysis — '+str(exc));return
        # A fair turn never relaxes an already prepared cue/grid reservation.
        # Compatibility prerequisites and committed-receipt reconciliation keep
        # their old bypass; no new registration/membership SQL may pass it.
        prepared_reservation=bool(self.tags.prep_priority)
        analysis_priority=bool(head_analysis or self._analysis_priority() or prepared_reservation)
        # A later drop may need compatibility preparation before its waveform,
        # while the first drop is already waiting for that local work. Only
        # this file-only prerequisite may bypass the queue; source SQL stays FIFO.
        if analysis_priority and (job['error'] or job['id'] in self._analysis_deferred
                or job['request'] is None and job.get('media_ready',False)):
            lib=w.lib
            if lib:
                for index,value in enumerate(self.pending[1:],1):
                    if (not value['error'] and value['request'] is None and not value.get('media_ready',False)
                            and (value['source'],value['library_id'])==(str(lib.source),lib.source_library_id)):
                        job_index=index;job=deepcopy(value);break
        if (job['error']=='Only integer PCM WAVE is supported' and job['request'] is None
                and not job['bindings'] and not job['membership']):
            # The old unsupported-float failure is now a recoverable import
            # stage. Keep the same job/order/destination and durable identity.
            previous=self.pending;queue=list(previous);queue[job_index]={**queue[job_index],'error':'','retry':False}
            w.settings[jobs.KEY]=queue
            if not w._save_state():w.settings[jobs.KEY]=previous;return
            job=deepcopy(queue[job_index])
        if job['error']:self.status('Import needs attention — '+job['error']);return
        if time.monotonic()<self.not_before:self.timer.start();return
        source_owned=(getattr(w,'_analysis_source_busy',lambda:self.tags.owned)()
                      if analysis_priority else self.tags.source_job_owned)
        if (source_owned or w._refresh_worker is not None or w._state_maintenance
                or getattr(w,'_analysis_worker',None) is not None
                or getattr(getattr(w,'_import_tasks',None),'pending',0)
                or w._device_operation_owned()):self.timer.start();return
        lib=w.lib
        if not lib or not self.tags.verified or lib.source_kind!='rekordbox-db':return
        if (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id']):
            self.status('Import pending in its original rekordbox library');return
        if any(t['id'] in getattr(lib,'source_binding_issues',{}) for t in job['tracks']):
            self.fail('This song’s native identity changed; its original record and saved edits are preserved',job['id']);return
        try:local_analysis=self._local_analysis(job)
        except ValueError as exc:
            self.status('Import waiting for analysis — '+str(exc));return
        analysis_priority=bool(analysis_priority or local_analysis)
        analysis_waiting=('Import waiting for cue/grid saves' if prepared_reservation else
                         'Import waiting for analysis — retry the failed song'
                          if any(pending['state']=='failed' for pending in local_analysis)
                          else 'Import waiting for analysis')
        if analysis_priority and job['request'] is None and job.get('media_ready',False):
            self.status(analysis_waiting)
            self.timer.start();return
        playlists=getattr(w,'playlist_controller',None)
        if not self.source_priority and playlists and playlists.pending and not (
                analysis_priority and job['request'] is None and not job.get('media_ready',False)):
            self.timer.start();return
        if not self.registration_factory and load_authority(self.tags.data_root/'rekordbox-registration-authority.json',
                lib.source,lib.source_library_id) is None:
            self.status('Import pending — native registration awaits library acceptance');return
        expected={t['id']:w.settings.get('analysis_results',{}).get(analysis.result_key(t['path']),{}).get('sha256')
                  for t in job['tracks']}
        media_done=deepcopy(w.settings.get(media_conversion_jobs.DONE,{}))
        try:acknowledged=wave_conversion.acknowledged(w.settings,job)
        except ValueError as exc:self.fail(str(exc),job['id']);return
        for tid,digest in acknowledged.items():
            if expected.get(tid) and expected[tid]!=digest:
                self.fail('Saved analysis differs from the converted WAVE; original retained',job['id']);return
            expected[tid]=digest
        # Conversion completion and media readiness are separate durable saves.
        # Reconstruct the receipt's exact byte pin on every pass/restart, also
        # when no BPM/key result exists to supply an audio digest.
        media_receipts={}
        try:
            if not isinstance(media_done,dict):raise ValueError('Unknown conversion receipts')
            for item in job['tracks']:
                path=item['path'];operation=str(uuid5(UUID(job['id']),path))
                done=media_done.get(path)
                if not done or done['operation_id']!=operation:
                    done=next((value for old_path,value in media_done.items()
                        if value.get('target')==path
                        and value['operation_id']==str(uuid5(UUID(job['id']),old_path))),None)
                if done:
                    digest=done['after_sha256']
                    if (not isinstance(digest,str) or len(digest)!=64
                            or any(char not in '0123456789abcdef' for char in digest)):
                        raise ValueError('Unknown converted-media digest')
                    if expected.get(item['id']) and expected[item['id']]!=digest:
                        raise WriteProofError('Saved analysis differs from the compatible WAVE')
                    expected[item['id']]=digest;media_receipts[item['id']]=done
        except (AttributeError,KeyError,TypeError,ValueError):
            self.fail('Saved compatible-media receipt is invalid; import preserved',job['id']);return
        except WriteProofError as exc:self.fail(str(exc),job['id']);return
        def guarded_work():
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            store=self._registration(lib)
            if job_index and (job['request'] is not None or job.get('media_ready',False)):
                raise WriteProofError('Only compatibility preparation may bypass pending imports')
            # A prepared request may already have committed before its local
            # acknowledgement. Reconcile those effects even while local DSP
            # has priority, without admitting a new registration or membership.
            committed=None
            if analysis_priority and job['request'] is not None:
                if not job['bindings']:
                    committed=store.committed_bindings(job['request'])
                    if not committed:return dict(deferred=True)
                elif job['destination']['kind']=='native':
                    if job['membership'] is None:return dict(deferred=True)
                    playlist=self._playlist(lib)
                    playlist.recover()
                    archive=playlist.root/('playlist-'+job['membership']['operation_id'])
                    if not archive.exists():return dict(deferred=True)
            if job['retry'] and not analysis_priority:
                if not job['bindings'] and job['request'] is not None:
                    with phase('registration_retry'):
                        return dict(request=store.prepare_retry(job['request']))
                if job['membership'] is not None:
                    with phase('import_membership_retry'):
                        return dict(membership=self._playlist(lib).prepare_retry(job['membership']))
            if job['request'] is None and not job.get('media_ready',False):
                from soundbox.media_conversion_store import ConversionStore,load_authority as media_authority
                authority=media_authority(self.tags.data_root/'rekordbox-media-conversion-authority.json',lib.source,lib.source_library_id)
                if authority:
                    media=ConversionStore(authority,self.tags.data_root)
                    for item in job['tracks']:
                        path=Path(item['path'])
                        operation=str(uuid5(UUID(job['id']),str(path)))
                        if item['id'] in media_receipts:continue
                        track=lib.tracks_by_id.get(item['id'])
                        native=[dict(id=track.native_id,uuid=track.source_uuid)] if track and track.source_uuid else []
                        getattr(w,'_prepare_grid_cache',lambda _tid:None)(item['id'])
                        with phase('import_media_conversion'):
                            receipt=media.convert(path,operation,native=native)
                        if not receipt.get('unchanged'):
                            intent=dict(id=operation,path=str(path),ids=[item['id']],native=native,
                                        source=job['source'],library_id=job['library_id'],error='')
                            return dict(media_conversion=receipt,media_intent=intent)
                with phase('import_conversion'):
                    converted=store.convert_imports(job['tracks'],self.tags.data_root/'wave-conversion-originals',
                        job['id'],expected=expected,acknowledged=acknowledged,
                        before_replace=getattr(w,'_prepare_grid_cache',lambda _tid:None))
                if converted:return dict(conversions=converted)
                return dict(media_ready=True)
            if job['request'] is None:
                pinned=[{**t,**({'sha256':expected[t['id']]} if expected.get(t['id']) else {})} for t in job['tracks']]
                with phase('registration_prepare'):
                    return dict(request=store.prepare(pinned,operation_id=job['id']))
            if not job['bindings']:
                try:
                    with phase('registration_apply'):
                        result=committed if committed is not None else store.apply(job['request'])
                except ConflictError as exc:
                    if (str(exc)!='Captured media identity changed' or job.get('media_refreshes',0)
                            or not store.media_metadata_drift_only(job['request'])):raise
                    return dict(request=store.prepare_retry(job['request'],preserve_audio=True),media_refresh=True)
                bindings=[{k:v for k,v in b.items() if k!='registered'} for b in result]
                with phase('registration_readback'):
                    refreshed=self.loader(lib.source)
                if job['destination']['kind']=='collection':
                    # The same worker already observed the complete current
                    # library after the verified registration. Confirm its
                    # exact bindings, then persist them and retire the intent
                    # together; a separate no-write phase needs no second read.
                    if (str(refreshed.source),refreshed.source_library_id)!=(job['source'],job['library_id']):
                        raise WriteProofError('Import readback belongs to a different native library')
                    jobs.project(refreshed,bindings)
                    return dict(bindings=bindings,done=True,library=refreshed)
                return dict(bindings=bindings,library=refreshed)
            if job['destination']['kind']=='native':
                with phase('registration_binding_replay'):
                    store.apply(job['request'])  # verify the completed source binding
                playlist=self._playlist(lib)
                if job['membership'] is None:
                    with phase('import_destination_catalog'):
                        catalog=playlist.snapshot()
                    dest=job['destination']
                    target=next((r for r in catalog['nodes'] if r['ID']==dest['id'] and r['UUID']==dest['uuid']
                        and r['Attribute']==0 and r['rb_local_deleted']==0),None)
                    if target is None:raise WriteProofError('Import destination was deleted or replaced; original destination retained')
                    tracks=[SimpleNamespace(track_id=b['native_id'],source_uuid=b['native_uuid']) for b in job['bindings']]
                    return dict(membership=request_for(catalog,'append',identity=dest['id'],tracks=tracks))
                with phase('import_membership_before'):
                    before=playlist.snapshot()
                with phase('import_membership_apply'):
                    after=playlist.apply(job['membership'])
                with phase('import_membership_readback'):
                    refreshed=self.loader(lib.source)
                # Only this freshly verified operation may rebase app intents
                # admitted against its own exact prior catalog. A replay after
                # later native changes supplies no such causal proof.
                rebase=((before['revision'],after['revision'])
                        if before['revision']==job['membership']['revision']
                        and refreshed.source_playlist_catalog.get('revision')==after['revision'] else None)
                if rebase is None:
                    transition=getattr(playlist,'committed_transition',None)
                    proof=transition(job['membership']) if transition else None
                    if proof and proof[1]==refreshed.source_playlist_catalog.get('revision'):rebase=proof
                return dict(done=True,library=refreshed,catalog_rebase=rebase)
            # Readback is part of completion. A crash/save failure replays the
            # exact stored requests, never a fresh append or new content ID.
            return dict(done=True,library=self.loader(lib.source))
        def work():
            # Select only facts from this exact validated durable request. The
            # worker-scoped proof handles ctime-only drift; every store guard
            # still checks canonical identity, authority, source and payloads.
            if job['request'] is None:return guarded_work()
            validate_registration_request(job['request'])
            try:
                with metadata_identity_proof(claim['audio'] for claim in job['request']['tracks']):
                    return guarded_work()
            except ConflictError as exc:
                if str(exc)!=METADATA_PENDING:raise
                return dict(metadata_waiting=True)
        def completed(worker):
            self.conversion_tracks.clear()
            if worker.error:self.fail(worker.error,job['id']);return
            value=worker.result
            if value.get('metadata_waiting'):
                self.not_before=time.monotonic()+2
                self.status('Verifying audio…')
                if not self.closing:self.timer.start()
                return
            if value.get('deferred'):
                self._analysis_deferred.add(job['id'])
                self.status(analysis_waiting)
                if not self.closing:self.timer.start()
                return
            if value.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Import pending — quit rekordbox to save');self.timer.start();return
            if not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id']):
                self.fail('Saved in the original library; switch back to finish the import',job['id']);return
            previous=dict(w.settings);previous_imported=w.imported;queue=list(self.pending)
            if len(queue)<=job_index or queue[job_index]['id']!=job['id']:
                self.fail('Import intention changed during save; receipts preserved',job['id']);return
            queue[job_index]=dict(queue[job_index])
            try:
                if 'media_conversion' in value:
                    updated,imported=media_conversion_jobs.acknowledge(w.settings,w.imported,value['media_intent'],value['media_conversion'])
                    w.settings.clear();w.settings.update(updated);w.imported=imported
                    queue=list(w.settings[jobs.KEY]);queue[job_index]=dict(queue[job_index])
                if 'conversions' in value:
                    updated,imported=wave_conversion.acknowledge(w.settings,w.imported,value['conversions'])
                    w.settings.clear();w.settings.update(updated);w.imported=imported
                for key in ('request','bindings','membership','media_ready'):
                    if key in value:queue[job_index][key]=value[key]
                if value.get('media_refresh'):queue[job_index]['media_refreshes']=1
                queue[job_index]['retry']=False
                if value.get('catalog_rebase'):
                    old_revision,new_revision=value['catalog_rebase']
                    clocks={job['membership']['playlist_id']:job['membership']['xml_timestamp']}
                    if 'rekordbox_playlist_edits' in w.settings:
                        w.settings['rekordbox_playlist_edits']=deepcopy(w.settings['rekordbox_playlist_edits'])
                    for later in w.settings.get('rekordbox_playlist_edits',[]):
                        if (isinstance(later,dict) and later.get('source')==job['source']
                                and later.get('library_id')==job['library_id']
                                and isinstance(later.get('request'),dict)
                                and later['request'].get('revision')==old_revision):
                            request=later['request']
                            try:validate_playlist_request(request)
                            except (WriteProofError,KeyError,TypeError,ValueError):continue
                            request['revision']=new_revision
                            target=request['playlist_id']
                            if request['operation']!='create':
                                if target in clocks:request['xml_timestamp']=max(request['xml_timestamp'],clocks[target]+1)
                                if request['xml_timestamp']>=2**63:raise WriteProofError('Native playlist XML clock is exhausted')
                                clocks[target]=request['xml_timestamp']
                if 'bindings' in value:
                    jobs.guard_curation(value['bindings'],keyed=[
                        getattr(w,'titles',{}),getattr(w,'overrides',{}),getattr(w,'hidden',set()),
                        w.settings.get('track_info',{})],memberships=[
                        *getattr(w,'custom',{}).values(),*getattr(w,'removals',{}).values(),
                        *getattr(w,'playlist_additions',{}).values()])
                    w.settings[jobs.BINDINGS]=jobs.remember(w.settings.get(jobs.BINDINGS,[]),value['bindings'])
                jobs.validate(queue)
                w.settings[jobs.KEY]=queue[:job_index]+queue[job_index+1:] if value.get('done') else queue
                with phase('import_acknowledgement_save'):
                    if not w._save_state():raise OSError('Could not persist import progress; retry uses the same native receipts')
            except (WriteProofError,TypeError,ValueError,OSError) as exc:
                w.settings.clear();w.settings.update(previous);w.imported=previous_imported;self.fail(str(exc),job['id']);return
            self._analysis_deferred.discard(job['id'])
            if 'conversions' in value and not self.closing and not w._shutting_down:
                imports.inject(w.lib,w.imported,repair_missing=False)
                pb=getattr(w,'player_bar',None)
                for receipt in value['conversions']:
                    if pb and pb.current_track()==receipt['track_id']:
                        record=w.settings.get('analysis_results',{}).get(analysis.result_key(receipt['path']))
                        pb.detected_analysis=record
                        if record:pb._analysis_parts.update(_detected_sha256=record['sha256'],_detected_epoch=record['epoch'])
                        pb.refresh_audio_detail(receipt['path'])
            if 'media_conversion' in value and not self.closing and not w._shutting_down:
                imports.inject(w.lib,w.imported,repair_missing=False)
                pb=getattr(w,'player_bar',None);intent=value['media_intent'];receipt=value['media_conversion']
                if pb and pb.current_track() in intent['ids']:
                    target=receipt.get('target',receipt['path'])
                    if target!=receipt['path']:pb._src_path=target
                    record=w.settings.get('analysis_results',{}).get(analysis.result_key(target))
                    if record:pb.detected_analysis=record;pb._analysis_parts.update(_detected_sha256=record['sha256'],_detected_epoch=record['epoch'])
                    pb.refresh_audio_detail(target)
                if receipt.get('target',receipt['path'])!=receipt['path']:
                    w._analysis_queue=[entry for entry in w._analysis_queue if entry[1].track_id not in intent['ids']]
                    w._resume_analysis_jobs()
            if 'library' in value and not self.closing and not w._shutting_down:
                with phase('import_acknowledgement_refresh'):
                    bindings=value.get('bindings',job.get('bindings',[]))
                    w._on_refresh_done(value['library'],metadata_changed_ids=tuple(
                        binding['native_id'] for binding in bindings))
                if (not self.tags.verified or any(t['id'] in getattr(w.lib,'source_binding_issues',{})
                                                for t in job['tracks'])):
                    self.fail('Native identity needs reconciliation; import receipts preserved',job['id']);return
            self.status(self.progress_text() if self.pending else '')
            finish=getattr(w,'_finish_edit_status',None)
            if not self.pending and finish:finish(quiet=True)
            self.not_before=0.0
            self.schedule()
        self.status(self.progress_text())
        if job['request'] is None and not job.get('media_ready',False):
            self.conversion_tracks={t['id'] for t in job['tracks']}
        if not self.tags.run_registration_job(work,completed):self.conversion_tracks.clear()

    def fail(self, error, job_id=None):
        self.timer.stop();w=self.window
        queue=list(self.pending)
        if queue and not self.queue_error():
            index=next((i for i,value in enumerate(queue) if value['id']==job_id),None) if job_id else 0
            if index is not None:
                queue[index]={**queue[index],'error':str(error).replace('\0','')[:4096]}
                w.settings[jobs.KEY]=queue;w._save_state()
        self.status('Import needs attention — '+str(error))
        self.window._native_pending_changed(rebuild=True)
        self.window._native_reconcile_source()

    def retry(self):
        if self.queue_error() or not self.pending or self.tags.owned:return
        # Already prepared operations retain their identities. Stale/rolled-back
        # requests require reconciliation, never a blind regenerated append.
        before=self.pending;queue=list(before);queue[0]={**queue[0],'error':'','retry':True}
        if queue[0]['request'] is None:queue[0]['media_ready']=False
        self.window.settings[jobs.KEY]=queue
        if self.window._save_state():self.schedule()
        else:self.window.settings[jobs.KEY]=before

    def discard(self):
        w=self.window
        if (self.closing or self.cancelling or not self.pending or self.queue_error() or self.tags.owned
                or w._state_maintenance or w._shutting_down):return
        pending=deepcopy(self.pending)
        if any(j['request'] is not None for j in pending) and (
                not w.lib or not self.tags.verified or w._refresh_worker is not None or w._device_operation_owned()
                or any((j['source'],j['library_id'])!=(str(w.lib.source),w.lib.source_library_id) for j in pending)):
            self.status('Refresh the original library and wait for its current operation before cancelling prepared imports');return
        self.cancelling=True;self.timer.stop()
        self.tags.admission_paused=True
        confirmed=QMessageBox.question(w,'Discard pending imports',
                f'Stop saving {len(self.pending)} pending import(s)?\n'
                'Songs already saved in rekordbox stay there. Files remain in SoundBox Collection.',
                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes
        if not confirmed:
            self.tags.admission_paused=False;self.cancelling=False
            self.tags.idle.emit();self.schedule();return
        if self.closing or w._shutting_down:
            self.tags.admission_paused=False;self.cancelling=False;return
        lib=w.lib
        def finish(bindings,library=None):
            self.tags.admission_paused=False;self.cancelling=False
            before=dict(w.settings)
            try:
                if self.pending[:len(pending)]!=pending:raise WriteProofError('Import queue changed during cancellation; intentions retained')
                jobs.guard_curation(bindings,keyed=[getattr(w,'titles',{}),getattr(w,'overrides',{}),
                    getattr(w,'hidden',set()),w.settings.get('track_info',{})],memberships=[
                    *getattr(w,'custom',{}).values(),*getattr(w,'removals',{}).values(),
                    *getattr(w,'playlist_additions',{}).values()])
                w.settings[jobs.BINDINGS]=jobs.remember(w.settings.get(jobs.BINDINGS,[]),bindings)
                w.settings[jobs.KEY]=deepcopy(self.pending[len(pending):])
                if not w._save_state():raise OSError('Could not persist import cancellation; intentions retained')
            except (WriteProofError,TypeError,ValueError,OSError) as exc:
                w.settings.clear();w.settings.update(before);self.fail(str(exc));return
            self.timer.stop();self.status('Imports pending' if self.pending else '')
            if library is not None and not self.closing and not w._shutting_down:w._on_refresh_done(library)
            if not self.closing and not w._shutting_down:w._native_pending_changed(rebuild=True)
            self.tags.idle.emit();self.schedule()
        if not any(j['request'] is not None for j in pending):finish([]);return
        if (not lib or not self.tags.verified
                or any((j['source'],j['library_id'])!=(str(lib.source),lib.source_library_id) for j in pending)):
            self.tags.admission_paused=False;self.cancelling=False
            self.fail('Library changed during cancellation; return to the original library and discard again');return
        def work():
            self.tags._recover_pending(lib)
            store=self._registration(lib);bindings=[]
            for job in pending:
                with phase('registration_cancel_reconcile'):
                    known=job['bindings'] or (store.committed_bindings(job['request']) if job['request'] else [])
                bindings.extend({k:v for k,v in binding.items() if k!='registered'} for binding in known)
            with phase('import_cancel_readback'):
                return bindings,self.loader(lib.source)
        def completed(worker):
            self.cancelling=False
            if worker.error:self.fail(worker.error);return
            if self.pending[:len(pending)]!=pending:
                self.fail('Import queue changed during cancellation; intentions retained');return
            finish(*worker.result)
        def start():
            if self.closing or w._shutting_down:
                self.tags.admission_paused=False;self.cancelling=False;return
            if w._refresh_worker is not None or w._device_operation_owned():
                self.status('Waiting to cancel pending imports…');QTimer.singleShot(100,self,start);return
            self.tags.admission_paused=False
            if not self.tags.run_registration_job(work,completed,cancelling=True):
                self.cancelling=False;self.status('Wait for the current library edit before cancelling imports');return
        start()

    def stop(self):
        self.closing=True;self.tags.admission_paused=False;self.timer.stop()
        self._pump_wakeup.stop()
