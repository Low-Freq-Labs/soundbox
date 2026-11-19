"""Selection-scoped Graveyard review and durable confirmed disposal."""
from copy import deepcopy
import json
import hashlib
import time
from pathlib import Path
from uuid import UUID,uuid4

from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox import shared_imports,rekordbox_removal_plan,rekordbox_db,native_identity
from soundbox.rekordbox_removal_store import RemovalStore,load_authority,confirmation
from soundbox.rekordbox_write import _regular,ConflictError
from .devices import human

KEY='graveyard_delete_intent'
DONE='graveyard_disposals'
REVIEW='graveyard_delete_reviews'


def move_to_trash(path):
    from soundbox.mac_trash import move_confirmed
    # A deterministic destination survives process loss without asking macOS
    # for permission to enumerate the user's private Trash directory.
    return move_confirmed(path)


class RemovalController(QObject):
    def __init__(self,window,*,factory=None,review=None,collection_enabled=False,collection_loader=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=factory;self.review=review or self.confirm;self.preparing=False;self.closing=False
        # Native-only removal remains unavailable until its paired Restore path
        # has an independently established preservation contract.
        self.collection_enabled=collection_enabled is True
        self.collection_loader=collection_loader or (lambda source:rekordbox_db.load_live(source,resolve_media_paths=False))
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(400)
        self.timer.timeout.connect(self.pump);self.tags.idle.connect(self.schedule)
        self.tags.changed.connect(self.schedule)

    def schedule(self,*_args):
        pending=self.window.settings.get(REVIEW,[])
        confirmed=self.window.settings.get(KEY)
        valid=isinstance(pending,list) and all(isinstance(j,dict) for j in pending)
        lib=self.window.lib
        matches=lambda j:bool(lib and j.get('source')==str(lib.source) and j.get('library_id')==lib.source_library_id)
        self.tags.foreground_waiting=bool(not self.closing and valid and
            (any(not j.get('error') and matches(j) for j in pending)
             or isinstance(confirmed,dict) and not confirmed.get('error') and matches(confirmed)))
        if not self.closing and (pending or confirmed):self.timer.start()

    def store(self,lib):
        if self.factory:return self.factory(lib)
        authority=load_authority(self.tags.data_root/'rekordbox-removal-authority.json',lib.source,lib.source_library_id)
        if authority is None:raise RuntimeError('Collection deletion awaits its separate native acceptance')
        return RemovalStore(authority,self.tags.data_root,trash=move_to_trash)

    def status(self,text):self.window._flash_status(text,12)

    def capture_collection(self,ids):
        captured=rekordbox_removal_plan.collection_selection(self.window.lib,ids)
        row_for=getattr(self.window,'_row_for',None)
        if row_for:
            fields={'title','artist','album','genre','bpm','key','time','format','size','location',
                    'sampleRate','bitDepth','bitrate','rating','year','comments','playCount','dateCreated'}
            for entry in captured['entries']:
                row=row_for(self.window.lib.tracks_by_id[entry['id']])
                entry['display']={key:value for key,value in row.items() if key in fields}
        return captured

    def _collection_current(self,captured):
        try:
            if not isinstance(captured,dict) or set(captured)!={'source','library_id','entries'}:return False
            fresh=rekordbox_removal_plan.collection_selection(self.window.lib,[entry['id'] for entry in captured['entries']])
            def pins(value):
                return {**value,'entries':[{key:entry[key] for key in ('id','native_id','native','path','local_path')}
                                          for entry in value['entries']]}
            return pins(fresh)==pins(captured)
        except (ValueError,KeyError,TypeError,RuntimeError):return False

    def request_collection(self,captured):
        """Queue a bound native-only review; production cutover stays disabled."""
        if not self.collection_enabled:
            self.status('Native Collection removal is waiting for its verified Restore path');return False
        if not self._collection_current(captured):
            self.status('The Collection selection changed; select the songs again');return False
        w=self.window;ids=[entry['id'] for entry in captured['entries']]
        reason=self.reason(ids)
        if reason:self.status(reason);return False
        previous=w.settings.get(REVIEW);queue=deepcopy(previous or [])
        if not isinstance(queue,list) or len(queue)>=256 or any(not isinstance(job,dict) for job in queue):
            self.status('The saved removal queue needs attention');return False
        existing=next((job for job in queue if job.get('native_only') and job.get('entries')==captured['entries']
            and (job.get('source'),job.get('library_id'))==(captured['source'],captured['library_id'])),None)
        if existing:existing['error']=''
        else:queue.append(dict(id=str(uuid4()),source=captured['source'],library_id=captured['library_id'],
            ids=ids,entries=deepcopy(captured['entries']),native_only=True,error=''))
        w.settings[REVIEW]=queue
        if not w._save_state():
            if previous is None:w.settings.pop(REVIEW,None)
            else:w.settings[REVIEW]=previous
            self.status('Collection removal was not queued because its selection could not be saved');return False
        self.status('Preparing Collection removal review…');self.schedule();return True

    @staticmethod
    def _retain_collection_history(plan,data_root,area):
        from soundbox import graveyard
        from .graveyard import _retain_archive
        path=graveyard.archive_path(data_root,plan['authority']['database'],plan['authority']['library_id'])
        remembered={}
        if path.exists():
            _regular(path)
            if path.stat().st_size>4*1024*1024:raise ValueError('Graveyard history exceeds its bound')
            saved=json.loads(path.read_bytes())
            if (saved.get('format'),saved.get('source'),saved.get('library_id'))!=(graveyard.RECOVERED_FORMAT,
                    plan['authority']['database'],plan['authority']['library_id']):raise ValueError('Graveyard history belongs to another source')
            remembered=saved['tracks']
        for local,row in plan['history'].items():
            prior=remembered.get(local)
            if prior and (prior['id'],prior['uuid'])!=(row['id'],row['uuid']):
                raise ValueError('Historical Collection identity changed; its original record is preserved')
            remembered[local]=row
        _retain_archive(area,path.relative_to(data_root),
            dict(format=graveyard.RECOVERED_FORMAT,source=plan['authority']['database'],
                 library_id=plan['authority']['library_id'],tracks=remembered))

    @staticmethod
    def _collection_readback(store,plan,job,loader,data_root,area,baseline):
        library=loader(Path(job['source']))
        selected=[entry['native_id'] for entry in plan['selections']]
        # The native reader keys raw source records by native ID, before local
        # import projection. Check only this bounded selected set in the worker.
        if (library.source_kind!='rekordbox-db'
                or (str(library.source),library.source_library_id)!=(job['source'],job['library_id'])
                or (str(baseline.source),baseline.source_library_id)!=(job['source'],job['library_id'])
                or any(library.tracks_by_id.get(native_id) is not None for native_id in selected)):
            raise RuntimeError('Native Collection readback still contains a selected identity')
        # Reuse only already-known lexical/canonical distinctions. Re-resolving
        # every surviving audio path is unnecessary for a DB-only removal.
        for local,uri,raw in native_identity.canonical_path_updates(library,baseline):
            track=library.tracks_by_id[local]
            track.location_uri=uri;track.source_native_path=raw
        receipt=store.verify_removed(job['folder'],job['sha256'],job['selection_sha256'])
        RemovalController._retain_collection_history(plan,data_root,area)
        return {**receipt,'library':library,'absent_native_ids':selected}

    def reason(self,ids):
        w=self.window;ids=set(ids)
        if not w.lib or w.lib.source_kind!='rekordbox-db' or not self.tags.verified:return 'Load the verified rekordbox library first'
        if w._state_maintenance or not w._state_repository.writable:return 'Library state is not writable'
        if self.closing or w._shutting_down:return 'Finishing library work…'
        for key,field in (('pending_prep_edits','track_id'),):
            if any(j.get(field) in ids and j.get('error') for j in w.settings.get(key,[])):
                return 'A selected song has a save error; resolve it before deleting'
        return ''

    def request(self):
        """Persist the clicked selection now; background ownership is not a veto."""
        w=self.window
        from .settings_page import _GRAVE_ROLE
        ids=list(dict.fromkeys(data[1] for item in w._grave.selectedItems() if (data:=item.data(0,_GRAVE_ROLE))))
        prior=w.settings.get(KEY)
        if not ids and prior:ids=prior['ids']
        if not ids:return
        reason=self.reason(ids)
        if reason:self.status(reason);return
        previous=deepcopy(w.settings.get(REVIEW,[]));queue=deepcopy(previous)
        if not isinstance(queue,list) or len(queue)>=256 or any(not isinstance(j,dict) for j in queue):
            self.status('The saved Delete queue needs attention');return
        existing=next((j for j in queue if j.get('ids')==ids and j.get('source')==str(w.lib.source)
                       and j.get('library_id')==w.lib.source_library_id),None)
        if existing:existing['error']=''
        else:queue.append(dict(id=str(uuid4()),source=str(w.lib.source),library_id=w.lib.source_library_id,ids=ids,error=''))
        w.settings[REVIEW]=queue
        if not w._save_state():
            if previous:w.settings[REVIEW]=previous
            else:w.settings.pop(REVIEW,None)
            self.status('Delete was not queued because its selection could not be saved');return
        self.status('Preparing deletion review…');self.schedule();self.pump()

    def _review_done(self,identity):
        w=self.window;previous=deepcopy(w.settings.get(REVIEW,[]))
        w.settings[REVIEW]=[j for j in previous if j['id']!=identity]
        if not w._save_state():w.settings[REVIEW]=previous;return False
        self.schedule();return True

    def _prepare_review(self,job):
        w=self.window;ids=job['ids'];lib=w.lib;native_only=job.get('native_only',False)
        if (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id']):
            self.status('Delete belongs to the original library; switch back to review it');return
        if native_only and not self._collection_current(dict(source=job['source'],library_id=job['library_id'],entries=job['entries'])):
            self.status('The original Collection selection changed; its removal stays queued');return
        if not native_only and not set(ids)<=w.graveyard_controller.ids():
            self._review_done(job['id']);self.status('The Graveyard selection changed; nothing was deleted');return
        if any(j.get('track_id') in ids for j in w.settings.get('pending_prep_edits',[])):
            self.schedule();return
        if any(any(t['id'] in ids for t in j['tracks']) for j in w.settings.get('shared_import_jobs',[])):
            self.schedule();return
        entries=({entry['id']:deepcopy(entry) for entry in job['entries']} if native_only else
                 {tid:deepcopy(w.graveyard_controller.entries[tid]) for tid in ids if tid in w.graveyard_controller.entries})
        imported={} if native_only else deepcopy(w.imported)
        bindings=[] if native_only else deepcopy(w.settings.get(shared_imports.BINDINGS,[]))
        prior=deepcopy(w.settings.get(KEY))
        area=w._state_repository.area;data_root=self.tags.data_root;loader=self.collection_loader
        if prior and (set(prior['ids'])!=set(ids) or prior.get('native_only',False)!=native_only):prior=None
        prepared_store=None
        def work():
            nonlocal prepared_store
            self.tags._recover_pending(lib)
            absent=set(ids)-entries.keys()
            if absent:
                from soundbox import graveyard
                remembered={};archive=graveyard.archive_path(self.tags.data_root,lib.source,lib.source_library_id)
                if archive.exists():
                    _regular(archive)
                    if archive.stat().st_size>4*1024*1024:raise ValueError('Graveyard identity archive exceeds its bound')
                    body=json.loads(archive.read_bytes())
                    if (body.get('format')!=graveyard.RECOVERED_FORMAT or body.get('source')!=str(lib.source)
                            or body.get('library_id')!=lib.source_library_id):raise ValueError('Graveyard identity archive belongs to another library')
                    remembered=body['tracks']
                entries.update(graveyard.catalog(lib.source,lib.source_library_id,absent,imported=imported,
                    bindings=bindings,remembered=remembered))
            selected=[entries.get(tid,dict(id=tid,path='')) for tid in ids]
            skipped=[e['id'] for e in selected if not e.get('path') or not e.get('display')]
            dismiss=[e for e in selected if e['id'] in skipped and e.get('native_absent')
                     and e.get('proof',{}).get('kind')=='native-absent-snapshot']
            selected=[e for e in selected if e['id'] not in skipped]
            if not selected:return dict(empty=True,skipped=skipped,dismiss=dismiss)
            store=self.store(lib);prepared_store=store
            if prior:
                try:saved=store.completed(prior['folder'],prior['sha256'],prior['selection_sha256'])
                except ConflictError:saved=None
                if saved:
                    if native_only:
                        plan=json.loads((Path(prior['folder'])/'plan.json').read_bytes())
                        return dict(completed=self._collection_readback(store,plan,prior,loader,data_root,area,lib))
                    return dict(completed=saved)
            prepared=store.prepare(selected,native_only=True) if native_only else store.prepare(selected)
            return {**prepared,'skipped':skipped}
        def finished(worker):
            self.preparing=False
            if worker.error:
                for current in w.settings.get(REVIEW,[]):
                    if current['id']==job['id']:current['error']=worker.error
                w._save_state();self.schedule();self.status(worker.error);return
            result=worker.result
            if result.get('empty'):
                if result.get('dismiss'):
                    self._dismiss_unresolved(job,lib,result)
                else:
                    self._review_done(job['id']);self.status('These entries have no verified file paths; nothing was deleted')
                return
            if result.get('completed'):
                self._review_done(job['id']);self.acknowledge(prior,result['completed']);return
            if self.closing or w._shutting_down:return
            if not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(str(lib.source),lib.source_library_id):
                self.status('The library changed; Delete stays queued for its original library');return
            if native_only and not self._collection_current(dict(source=job['source'],library_id=job['library_id'],entries=job['entries'])):
                self.status('The original Collection selection changed; its removal stays queued');return
            if not native_only and not set(ids)<=w.graveyard_controller.ids():
                self._review_done(job['id']);self.status('The Graveyard selection changed; nothing was deleted');return
            self.tags.admission_paused=True
            try:accepted=self.review(result,result['skipped'])
            finally:self.tags.admission_paused=False
            if not accepted:
                self._review_done(job['id'])
                def clean():return prepared_store.discard_unstarted(result['folder'],result['sha256'])
                def cleaned(task):
                    if task.error:self.status('Cancelled; review backup retained — '+task.error)
                self.tags.run_removal_job(clean,cleaned)
                return
            before=deepcopy(w.settings)
            w.settings[KEY]=dict(folder=result['folder'],sha256=result['sha256'],selection_sha256=confirmation(result['plan']),
                source=str(lib.source),library_id=lib.source_library_id,ids=[e['id'] for e in result['plan']['selections']],error='')
            if native_only:w.settings[KEY]['native_only']=True
            w.settings[REVIEW]=[j for j in w.settings.get(REVIEW,[]) if j['id']!=job['id']]
            if not w._save_state():
                w.settings.clear();w.settings.update(before);self.status('Delete was not started because its confirmation could not be saved');return
            self._pause_selected_analysis(w.settings[KEY]['ids']);self.schedule()
        self.preparing=True
        if not self.tags.run_removal_job(work,finished):self.preparing=False;self.schedule()

    def _pause_selected_analysis(self,ids):
        w=self.window;ids=set(ids);worker=getattr(w,'_analysis_worker',None)
        if worker is not None and any(t.track_id in ids for t in worker.tracks):worker.cancel()
        w._analysis_queue=[item for item in getattr(w,'_analysis_queue',[]) if item[1].track_id not in ids]

    def _dismiss_unresolved(self,job,lib,result):
        w=self.window
        if (self.closing or w._shutting_down or not w.lib
                or (str(w.lib.source),w.lib.source_library_id)!=(str(lib.source),lib.source_library_id)
                or not set(job['ids'])<=w.graveyard_controller.ids()):return
        self.tags.admission_paused=True
        try:accepted=self.review(result,result['skipped'])
        finally:self.tags.admission_paused=False
        if not accepted:self._review_done(job['id']);return
        previous=deepcopy(w.settings);done=deepcopy(w.settings.get(DONE,{}))
        for entry in result['dismiss']:
            done[entry['id']]=dict(kind='unresolved-entry-dismissal',source=str(lib.source),library_id=lib.source_library_id,
                                   native_id=entry['native_id'],proof=entry['proof'])
        w.settings[DONE]=done
        w.settings[REVIEW]=[j for j in w.settings.get(REVIEW,[]) if j['id']!=job['id']]
        if not w._save_state():
            w.settings.clear();w.settings.update(previous);self.status('The stale entries could not be saved; please retry');return
        w._refresh_graveyard();self.schedule()
        self.status(f"Removed {len(result['dismiss'])} stale Graveyard entries; no audio files were identified or deleted")

    def confirm(self,result,skipped):
        if result.get('plan',{}).get('native_only'):
            plan=result['plan'];dialog=QMessageBox(self.window);dialog.setWindowTitle('Remove from native Collection?')
            dialog.setText(f"Remove {len(plan['selections'])} selected songs from rekordbox Collection and its playlists?")
            dialog.setInformativeText('Every audio file, native analysis packet and artwork file stays exactly where it is. SoundBox keeps the saved musical data and historical identities.')
            dialog.setDetailedText('\n\n'.join(selected['title']+'\n'+selected['path'] for selected in plan['selections']))
            dialog.setStandardButtons(QMessageBox.Cancel)
            remove=dialog.addButton('Remove from Collection',QMessageBox.DestructiveRole)
            dialog.setDefaultButton(QMessageBox.Cancel);dialog.setEscapeButton(QMessageBox.Cancel)
            dialog.exec();return dialog.clickedButton() is remove
        if result.get('empty') and result.get('dismiss'):
            dialog=QMessageBox(self.window);dialog.setWindowTitle('Remove stale Graveyard entries?')
            dialog.setText(f"Remove {len(result['dismiss'])} stale entries from the Graveyard?")
            dialog.setInformativeText('These track IDs are already absent from rekordbox Collection and have no verified file paths. Only their Graveyard entries will be cleared. No audio files will be deleted.')
            dialog.setDetailedText('\n'.join(e['id'] for e in result['dismiss']))
            dialog.setStandardButtons(QMessageBox.Cancel)
            remove=dialog.addButton('Remove stale entries',QMessageBox.DestructiveRole)
            dialog.setDefaultButton(QMessageBox.Cancel);dialog.setEscapeButton(QMessageBox.Cancel)
            dialog.exec();return dialog.clickedButton() is remove
        plan=result['plan'];files=len(plan['audio']);size=sum(a['size'] for a in plan['audio'])
        count=len(plan['selections']);dialog=QMessageBox(self.window)
        dialog.setWindowTitle('Delete from Collection and disk?');dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"Remove {count} track{'s' if count!=1 else ''} from rekordbox Collection and all its playlists?")
        text=f"Move {files} file{'s' if files!=1 else ''} ({human(size)}) to macOS Trash. You can recover them there; storage is freed when Trash is emptied. SoundBox history and saved musical data stay."
        if plan.get('missing'):text+=f"\n{len(plan['missing'])} files are already missing; their Collection entries will still be removed."
        if plan.get('retained'):text+=f"\n{len(plan['retained'])} shared files will be KEPT because remaining Collection tracks still use them."
        if skipped:text+=f"\n{len(skipped)} entries have no verified path and will stay in the Graveyard."
        dialog.setInformativeText(text)
        lines=[]
        for s in plan['selections']:
            lines.append(s['title']+'\n'+s['path']+ ('\nAlready absent from Collection' if not s['native_active'] else '\nCollection ID '+s['native_id'])
                + ('\nKEEP FILE — used by another Collection entry' if s.get('keep_file') else ''))
        for retained in plan.get('retained',[]):
            lines.append('Shared file kept: '+retained['path']+'\nRemaining entries: '+', '.join(ref['title']+' (ID '+ref['id']+')' for ref in retained['references']))
        lines.append('Affected rekordbox playlists: '+(', '.join(plan.get('playlist_labels',plan['playlists'])) or 'None'))
        if skipped:lines.append('Unresolved entries retained: '+', '.join(skipped))
        dialog.setDetailedText('\n\n'.join(lines));dialog.setStandardButtons(QMessageBox.Cancel)
        delete=dialog.addButton('Delete from Collection and disk',QMessageBox.DestructiveRole)
        dialog.setDefaultButton(QMessageBox.Cancel);dialog.setEscapeButton(QMessageBox.Cancel)
        dialog.exec();return dialog.clickedButton() is delete

    def pump(self,*,retry=False):
        w=self.window;job=deepcopy(w.settings.get(KEY));reviews=w.settings.get(REVIEW,[])
        if self.closing or w._shutting_down:return
        if self.preparing or self.tags.source_job_owned or w._refresh_worker is not None or w._device_operation_owned():
            self.schedule();return
        if w.lib is None or not self.tags.verified or w._state_maintenance:
            self.schedule();return
        if not self.collection_enabled and (isinstance(job,dict) and job.get('native_only')
                or any(isinstance(review,dict) and review.get('native_only') for review in reviews)):
            self.status('Native Collection removal is waiting for its verified Restore path');return
        if reviews and (not job or job.get('error')):
            review=next((j for j in reviews if isinstance(j,dict) and not j.get('error')
                         and j.get('source')==str(w.lib.source) and j.get('library_id')==w.lib.source_library_id),None)
            if review is None:return
            try:
                fields={'id','source','library_id','ids','error'}
                valid=(isinstance(review,dict) and set(review) in (fields,fields|{'native_only','entries'})
                    and ('native_only' not in review or review['native_only'] is True and isinstance(review['entries'],list)
                        and [entry['id'] for entry in review['entries']]==review['ids'])
                    and all(isinstance(review[k],str) for k in ('id','source','library_id','error'))
                    and str(UUID(review['id']))==review['id'] and UUID(review['id']).version==4
                    and isinstance(review['ids'],list) and 1<=len(review['ids'])<=10000
                    and all(isinstance(tid,str) and tid for tid in review['ids'])
                    and len(set(review['ids']))==len(review['ids']))
            except (ValueError,TypeError,KeyError):valid=False
            if not valid:
                self.status('Saved Delete selection is invalid; no files were touched');return
            if not review['error']:self._prepare_review(deepcopy(review))
            return
        if not job:return
        expected={'folder','sha256','selection_sha256','source','library_id','ids','error'}
        if (not isinstance(job,dict) or set(job) not in (expected,expected|{'native_only'})
                or 'native_only' in job and job['native_only'] is not True or not isinstance(job['ids'],list)
                or not 1<=len(job['ids'])<=10000 or any(not isinstance(tid,str) or not tid for tid in job['ids'])
                or len(set(job['ids']))!=len(job['ids'])
                or any(not isinstance(job[k],str) for k in expected-{'ids'})
                or any(len(job[k])!=64 or any(c not in '0123456789abcdef' for c in job[k]) for k in ('sha256','selection_sha256'))):
            self.status('Saved deletion intent is invalid; no files were touched');return
        if job.get('error') and not retry:return
        reason=self.reason(job['ids'])
        if reason:
            if retry:self.status(reason)
            self.schedule();return
        worker=getattr(w,'_analysis_worker',None)
        if worker is not None and any(t.track_id in job['ids'] for t in worker.tracks):
            self._pause_selected_analysis(job['ids']);self.schedule();return
        if any(j.get('track_id') in job['ids'] for j in w.settings.get('pending_prep_edits',[])):
            self.schedule();return
        lib=w.lib
        area=w._state_repository.area;data_root=self.tags.data_root;loader=self.collection_loader
        if (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id']):self.status('Deletion belongs to the original library; switch back to finish');return
        def work():
            self.tags._recover_pending(lib)
            store=self.store(lib)
            path=Path(job['folder'])/'plan.json';_regular(path);raw=path.read_bytes();plan=json.loads(raw)
            if hashlib.sha256(raw).hexdigest()!=job['sha256'] or [s['id'] for s in plan['selections']]!=job['ids']:
                raise RuntimeError('The confirmed deletion selection changed; evidence retained')
            if bool(plan.get('native_only'))!=job.get('native_only',False):raise RuntimeError('The confirmed removal file policy changed')
            result=store.apply(job['folder'],job['sha256'],job['selection_sha256'])
            if job.get('native_only'):
                return self._collection_readback(store,plan,job,loader,data_root,area,lib)
            return result
        def finished(worker):
            if worker.error:
                if self._fail(job,worker.error):w._resume_analysis_jobs()
                return
            self.acknowledge(job,worker.result)
        if not self.tags.run_removal_job(work,finished):self.schedule()
        else:self.status('Deleting selected songs…')

    def _retains(self,job):
        current=self.window.settings.get(KEY)
        return (isinstance(current,dict) and isinstance(job,dict)
                and {**current,'error':''}=={**job,'error':''})

    def _fail(self,job,message):
        retained=self._retains(job)
        if retained:
            self.window.settings[KEY]={**self.window.settings[KEY],'error':message}
            self.window._save_state();self.schedule()
        self.status('Delete needs attention — '+message)
        return retained

    def acknowledge(self,job,result):
        w=self.window
        if not self._retains(job):
            self.status('The original deletion finished; its receipt is preserved')
            return
        if not w.lib or (str(w.lib.source),w.lib.source_library_id)!=(job['source'],job['library_id']):
            self._fail(job,'Saved in the original library; switch back to finish Delete')
            return
        before=deepcopy(w.settings);old_hidden=set(w.hidden)
        path=Path(job['folder'])/'plan.json';_regular(path);raw=path.read_bytes();plan=json.loads(raw)
        if result.get('outcome')!='committed' or result.get('plan_sha256')!=job['sha256']:
            self.status('Deletion is unconfirmed; the Graveyard entries are retained');return
        if hashlib.sha256(raw).hexdigest()!=job['sha256'] or [s['id'] for s in plan['selections']]!=job['ids']:
            self.status('Deletion receipt selection changed; the Graveyard entries are retained');return
        if plan.get('native_only'):
            library=result.get('library')
            selected=[entry['native_id'] for entry in plan['selections']]
            if (not result.get('native_only') or not library or library.source_kind!='rekordbox-db'
                    or (str(library.source),library.source_library_id)!=(job['source'],job['library_id'])
                    or result.get('absent_native_ids')!=selected):
                self._fail(job,'Native Collection removal readback changed; the intention is preserved');return
            before_dates=dict(w.hidden_dates)
            w.settings.pop(KEY,None);w.hidden.update(job['ids'])
            for tid in job['ids']:w.hidden_dates.setdefault(tid,time.strftime('%Y-%m-%d %H:%M'))
            for key,field in (('analysis_jobs','track_id'),('native_analysis_jobs','local_id')):
                w.settings[key]=[entry for entry in w.settings.get(key,[]) if entry.get(field) not in job['ids']]
            if not w._save_state():
                w.settings.clear();w.settings.update(before);w.hidden.clear();w.hidden.update(old_hidden)
                w.hidden_dates.clear();w.hidden_dates.update(before_dates)
                self._fail(job,'Removed from native Collection; local acknowledgement failed. Retry to finish');return
            if w.player_bar.current_track() in job['ids']:w.player_bar.stop()
            if not self.closing and not w._shutting_down:
                w._publish_created_library(library)
                w._after_track_edit(f"Removed {len(job['ids'])} from native Collection; files kept",
                                    persist=False,refresh_devices=False)
            self.schedule();return
        done=deepcopy(w.settings.get(DONE,{}))
        for selected in plan['selections']:
            done[selected['id']]=dict(operation_id=result['operation_id'],path=selected['path'],
                trash=result['trash'].get(selected['path'],''),kept=selected.get('keep_file',False),plan_sha256=job['sha256'])
        w.settings[DONE]=done;w.settings.pop(KEY,None);w.hidden.update(job['ids'])
        for key,field in (('analysis_jobs','track_id'),('native_analysis_jobs','local_id')):
            w.settings[key]=[j for j in w.settings.get(key,[]) if j.get(field) not in job['ids']]
        if not w._save_state():
            w.settings.clear();w.settings.update(before);w.hidden.clear();w.hidden.update(old_hidden)
            self.status('Files were deleted; the local receipt could not be saved. Retry Delete to finish');return
        if w.player_bar.current_track() in job['ids']:w.player_bar.stop()
        w.graveyard_controller.entries={tid:e for tid,e in w.graveyard_controller.entries.items() if tid not in job['ids']}
        if not self.closing and not w._shutting_down:
            kept=len(plan.get('retained',[]))
            message=f"Removed {len(job['ids'])} tracks"
            if plan['audio']:message+=' — files are in Trash'
            if kept:message+=f"; kept {kept} shared file{'s' if kept!=1 else ''}"
            w._after_track_edit(message,persist=False,refresh_devices=False)
        self.schedule()

    def stop(self):self.closing=True;self.timer.stop();self.tags.foreground_waiting=False
