"""Direct file imports use durable intentions on the shared native source lane."""
from copy import deepcopy
from types import SimpleNamespace
import time
from pathlib import Path
from uuid import UUID,uuid5

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox import rekordbox_db, shared_imports as jobs, wave_conversion, analysis, imports,media_conversion_jobs
from soundbox.rekordbox_registration_store import RegistrationStore, load_authority
from soundbox.rekordbox_playlist_store import (PlaylistStore, load_authority as playlist_authority,
    request_for, validate_request as validate_playlist_request)
from soundbox.rekordbox_write import ConflictError,WriteProofError, assert_rekordbox_closed


class SharedImportController(QObject):
    def __init__(self, window, *, registration_factory=None, playlist_factory=None, loader=None):
        super().__init__(window)
        self.window=window;self.tags=window.tag_controller;self.closing=False;self.cancelling=False
        self.registration_factory=registration_factory;self.playlist_factory=playlist_factory
        self.loader=loader or rekordbox_db.load_live
        self.not_before=0.0
        self.conversion_tracks=set()
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump)
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
        return bool(not self.queue_error() and self.pending and not self.pending[0]['error']
                    and self.pending[0]['membership'] is not None)

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
        if not self.closing:QTimer.singleShot(0,self.pump)

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
        job=deepcopy(self.pending[0])
        if (job['error']=='Only integer PCM WAVE is supported' and job['request'] is None
                and not job['bindings'] and not job['membership']):
            # The old unsupported-float failure is now a recoverable import
            # stage. Keep the same job/order/destination and durable identity.
            previous=deepcopy(self.pending); queue=deepcopy(previous);queue[0].update(error='',retry=False)
            w.settings[jobs.KEY]=queue
            if not w._save_state():w.settings[jobs.KEY]=previous;return
            job=deepcopy(queue[0])
        if job['error']:self.status('Import needs attention — '+job['error']);return
        if time.monotonic()<self.not_before:self.timer.start();return
        if (self.tags.owned or w._refresh_worker is not None or w._state_maintenance
                or getattr(w,'_analysis_worker',None) is not None
                or getattr(getattr(w,'_import_tasks',None),'pending',0)
                or w._device_operation_owned()):self.timer.start();return
        lib=w.lib
        if not lib or not self.tags.verified or lib.source_kind!='rekordbox-db':return
        if (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id']):
            self.status('Import pending in its original rekordbox library');return
        if any(t['id'] in getattr(lib,'source_binding_issues',{}) for t in job['tracks']):
            self.fail('This song’s native identity changed; its original record and saved edits are preserved');return
        playlists=getattr(w,'playlist_controller',None)
        if not self.source_priority and playlists and playlists.pending:
            self.timer.start();return
        if not self.registration_factory and load_authority(self.tags.data_root/'rekordbox-registration-authority.json',
                lib.source,lib.source_library_id) is None:
            self.status('Import pending — native registration awaits library acceptance');return
        expected={t['id']:w.settings.get('analysis_results',{}).get(analysis.result_key(t['path']),{}).get('sha256')
                  for t in job['tracks']}
        media_done=deepcopy(w.settings.get(media_conversion_jobs.DONE,{}))
        try:acknowledged=wave_conversion.acknowledged(w.settings,job)
        except ValueError as exc:self.fail(str(exc));return
        for tid,digest in acknowledged.items():
            if expected.get(tid) and expected[tid]!=digest:
                self.fail('Saved analysis differs from the converted WAVE; original retained');return
            expected[tid]=digest
        def work():
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            store=self._registration(lib)
            if job['retry']:
                if not job['bindings'] and job['request'] is not None:
                    return dict(request=store.prepare_retry(job['request']))
                if job['membership'] is not None:
                    return dict(membership=self._playlist(lib).prepare_retry(job['membership']))
            if job['request'] is None:
                from soundbox.media_conversion_store import ConversionStore,load_authority as media_authority
                authority=media_authority(self.tags.data_root/'rekordbox-media-conversion-authority.json',lib.source,lib.source_library_id)
                if authority:
                    media=ConversionStore(authority,self.tags.data_root)
                    for item in job['tracks']:
                        path=Path(item['path'])
                        operation=str(uuid5(UUID(job['id']),str(path)))
                        done=media_done.get(str(path))
                        if not done or done['operation_id']!=operation:
                            done=next((value for old_path,value in media_done.items()
                                if value.get('target')==str(path)
                                and value['operation_id']==str(uuid5(UUID(job['id']),old_path))),None)
                        if done:
                            if expected.get(item['id']) and expected[item['id']]!=done['after_sha256']:
                                raise WriteProofError('Saved analysis differs from the compatible WAVE')
                            expected[item['id']]=done['after_sha256'];continue
                        track=lib.tracks_by_id.get(item['id'])
                        native=[dict(id=track.native_id,uuid=track.source_uuid)] if track and track.source_uuid else []
                        getattr(w,'_prepare_grid_cache',lambda _tid:None)(item['id'])
                        receipt=media.convert(path,operation,native=native)
                        if not receipt.get('unchanged'):
                            intent=dict(id=operation,path=str(path),ids=[item['id']],native=native,
                                        source=job['source'],library_id=job['library_id'],error='')
                            return dict(media_conversion=receipt,media_intent=intent)
                converted=store.convert_imports(job['tracks'],self.tags.data_root/'wave-conversion-originals',
                    job['id'],expected=expected,acknowledged=acknowledged,
                    before_replace=getattr(w,'_prepare_grid_cache',lambda _tid:None))
                if converted:return dict(conversions=converted)
                pinned=[{**t,**({'sha256':expected[t['id']]} if expected.get(t['id']) else {})} for t in job['tracks']]
                return dict(request=store.prepare(pinned,operation_id=job['id']))
            if not job['bindings']:
                try:result=store.apply(job['request'])
                except ConflictError as exc:
                    if (str(exc)!='Captured media identity changed' or job.get('media_refreshes',0)
                            or not store.media_metadata_drift_only(job['request'])):raise
                    return dict(request=store.prepare_retry(job['request'],preserve_audio=True),media_refresh=True)
                bindings=[{k:v for k,v in b.items() if k!='registered'} for b in result]
                return dict(bindings=bindings,library=self.loader(lib.source))
            if job['destination']['kind']=='native':
                store.apply(job['request'])  # verify the completed source binding
                playlist=self._playlist(lib)
                if job['membership'] is None:
                    catalog=playlist.snapshot();dest=job['destination']
                    target=next((r for r in catalog['nodes'] if r['ID']==dest['id'] and r['UUID']==dest['uuid']
                        and r['Attribute']==0 and r['rb_local_deleted']==0),None)
                    if target is None:raise WriteProofError('Import destination was deleted or replaced; original destination retained')
                    tracks=[SimpleNamespace(track_id=b['native_id'],source_uuid=b['native_uuid']) for b in job['bindings']]
                    return dict(membership=request_for(catalog,'append',identity=dest['id'],tracks=tracks))
                before=playlist.snapshot()
                after=playlist.apply(job['membership'])
                refreshed=self.loader(lib.source)
                # Only this freshly verified operation may rebase app intents
                # admitted against its own exact prior catalog. A replay after
                # later native changes supplies no such causal proof.
                rebase=((before['revision'],after['revision'])
                        if before['revision']==job['membership']['revision']
                        and refreshed.source_playlist_catalog.get('revision')==after['revision'] else None)
                return dict(done=True,library=refreshed,catalog_rebase=rebase)
            # Readback is part of completion. A crash/save failure replays the
            # exact stored requests, never a fresh append or new content ID.
            return dict(done=True,library=self.loader(lib.source))
        def completed(worker):
            self.conversion_tracks.clear()
            if worker.error:self.fail(worker.error);return
            value=worker.result
            if value.get('waiting'):
                self.not_before=time.monotonic()+2
                self.status('Import pending — quit rekordbox to save');self.timer.start();return
            if not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id']):
                self.fail('Saved in the original library; switch back to finish the import');return
            previous=deepcopy(w.settings);previous_imported=deepcopy(w.imported);queue=deepcopy(self.pending)
            if not queue or queue[0]['id']!=job['id']:
                self.fail('Import intention changed during save; receipts preserved');return
            try:
                if 'media_conversion' in value:
                    updated,imported=media_conversion_jobs.acknowledge(w.settings,w.imported,value['media_intent'],value['media_conversion'])
                    w.settings.clear();w.settings.update(updated);w.imported=imported
                    queue=deepcopy(w.settings[jobs.KEY])
                if 'conversions' in value:
                    updated,imported=wave_conversion.acknowledge(w.settings,w.imported,value['conversions'])
                    w.settings.clear();w.settings.update(updated);w.imported=imported
                for key in ('request','bindings','membership'):
                    if key in value:queue[0][key]=value[key]
                if value.get('media_refresh'):queue[0]['media_refreshes']=1
                queue[0]['retry']=False
                if value.get('catalog_rebase'):
                    old_revision,new_revision=value['catalog_rebase']
                    clocks={job['membership']['playlist_id']:job['membership']['xml_timestamp']}
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
                w.settings[jobs.KEY]=queue[1:] if value.get('done') else queue
                if not w._save_state():raise OSError('Could not persist import progress; retry uses the same native receipts')
            except (WriteProofError,TypeError,ValueError,OSError) as exc:
                w.settings.clear();w.settings.update(previous);w.imported=previous_imported;self.fail(str(exc));return
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
                w._on_refresh_done(value['library'])
                if (not self.tags.verified or any(t['id'] in getattr(w.lib,'source_binding_issues',{})
                                                for t in job['tracks'])):
                    self.fail('Native identity needs reconciliation; import receipts preserved');return
            self.status(self.progress_text() if self.pending else '')
            finish=getattr(w,'_finish_edit_status',None)
            if not self.pending and finish:finish(quiet=True)
            self.not_before=0.0
            self.schedule()
        self.status(self.progress_text())
        if job['request'] is None:self.conversion_tracks={t['id'] for t in job['tracks']}
        if not self.tags.run_registration_job(work,completed):self.conversion_tracks.clear()

    def fail(self, error):
        self.timer.stop();w=self.window
        queue=deepcopy(self.pending)
        if queue and not self.queue_error():
            queue[0]['error']=str(error).replace('\0','')[:4096];w.settings[jobs.KEY]=queue;w._save_state()
        self.status('Import needs attention — '+str(error))
        self.window._native_pending_changed(rebuild=True)
        self.window._native_reconcile_source()

    def retry(self):
        if self.queue_error() or not self.pending or self.tags.owned:return
        # Already prepared operations retain their identities. Stale/rolled-back
        # requests require reconciliation, never a blind regenerated append.
        before=deepcopy(self.pending);queue=deepcopy(before);queue[0].update(error='',retry=True)
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
            before=deepcopy(w.settings)
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
                known=job['bindings'] or (store.committed_bindings(job['request']) if job['request'] else [])
                bindings.extend({k:v for k,v in binding.items() if k!='registered'} for binding in known)
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
