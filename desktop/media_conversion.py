"""Background format audit and per-song recoverable conversion; no user controls."""
from copy import deepcopy
import json
import hashlib
import logging
import math
from pathlib import Path
import stat
from uuid import uuid4

from PySide6.QtCore import QObject,QTimer

from soundbox import analysis,cdj_compatibility as compatibility,media_conversion_jobs as jobs,imports
from soundbox.media_conversion_store import ConversionStore,load_authority
from soundbox.rekordbox_write import assert_rekordbox_closed,WriteProofError
from soundbox.state_io import atomic_bytes
from .task_owner import TaskOwner
from .workers import OneShotTask

LOG=logging.getLogger('soundbox')
_CACHE_SCHEMA=1


def _cache_digest(entries):
    return hashlib.sha256(json.dumps(entries,sort_keys=True,allow_nan=False,
                                    separators=(',',':')).encode()).hexdigest()


def _load_format_cache(path,source,library_id):
    """Technical display hints only; source publication always inspects anew."""
    try:
        if path.is_symlink() or path.stat().st_size>16*1024**2:return {}
        data=json.loads(path.read_bytes());entries=data['entries']
        if (data.get('schema')!=_CACHE_SCHEMA or data.get('source')!=source
                or data.get('library_id')!=library_id or not isinstance(entries,dict)
                or _cache_digest(entries)!=data.get('sha256')):return {}
        result={}
        profiles={('wav','pcm_s16le'):(16,),('wav','pcm_s24le'):(24,),
                  ('aiff','pcm_s16be'):(16,),('aiff','pcm_s24be'):(24,),
                  ('mp3','mp3'):(0,),('m4a','aac'):(0,16,24,32)}
        for canonical,entry in entries.items():
            if (not isinstance(canonical,str) or not Path(canonical).is_absolute()
                    or str(Path(canonical))!=canonical or '..' in Path(canonical).parts
                    or '\0' in canonical or not isinstance(entry,dict)):continue
            epoch,facts=entry.get('epoch'),entry.get('facts')
            if (not isinstance(epoch,list) or len(epoch)!=5
                    or any(type(value) is not int or value<0 for value in epoch)
                    or not epoch[1] or not epoch[2] or not isinstance(facts,dict)
                    or facts.get('profile')!=compatibility.PROFILE or facts.get('status')!='compatible'
                    or facts.get('sample_rate') not in (44100,48000) or facts.get('channels') not in (1,2)
                    or facts.get('bits') not in profiles.get((facts.get('family'),facts.get('codec')),())
                    or type(facts.get('duration')) not in (int,float)
                    or not math.isfinite(facts['duration']) or facts['duration']<=0):continue
            # Never reuse cached track membership/native IDs. A fresh scan
            # binds these facts to current tracks only after exact stat checks.
            result[canonical]=dict(epoch=epoch,facts=facts)
        return result
    except (OSError,ValueError,KeyError,TypeError):return {}


def _save_format_cache(path,source,library_id,entries):
    try:
        if path.is_symlink():return
        data=dict(schema=_CACHE_SCHEMA,source=source,library_id=library_id,
                  entries=entries,sha256=_cache_digest(entries))
        atomic_bytes(path,json.dumps(data,allow_nan=False).encode())
    except (OSError,ValueError,TypeError) as exc:
        LOG.warning('Technical media cache could not be saved: %s',exc)


class MediaConversionController(QObject):
    def __init__(self,window,*,factory=None):
        super().__init__(window);self.window=window;self.tags=window.tag_controller
        self.factory=factory;self.closing=False;self.active=set();self.tasks=TaskOwner(self)
        self.cache={};self._cache_identity=None;self.scan_again=False
        self.scan_timer=QTimer(self);self.scan_timer.setSingleShot(True);self.scan_timer.setInterval(500)
        self.scan_timer.timeout.connect(self.scan)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(1000);self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule)

    @property
    def pending(self):return self.window.settings.get(jobs.KEY,[])

    def converting(self,tid):return tid in self.active

    def facts(self,tid):
        lib=self.window.lib
        return getattr(lib,'media_facts',{}).get(tid) if lib else None

    def _publish_facts(self):
        if self.window.lib:
            self.window.lib.media_facts={t['id']:dict(path=path,epoch=e['epoch'],facts=e['facts'])
                for path,e in self.cache.items() if e['facts']['status']=='compatible' for t in e['tracks']}

    def authority(self,lib):
        return load_authority(self.tags.data_root/'rekordbox-media-conversion-authority.json',lib.source,lib.source_library_id)

    def discover(self):
        w=self.window
        if (self.closing or not w.lib or w.lib.source_kind!='rekordbox-db' or not self.tags.verified
                or not self.factory and (getattr(w,'_isolated',True) or self.authority(w.lib) is None)):return
        self.scan_again=True;self.scan_timer.start()

    def schedule(self):
        if not self.closing:self.timer.start()

    def scan(self):
        w=self.window
        if self.closing or w._shutting_down or not self.scan_again:return
        if self.tasks.pending or self.active or w._refresh_worker is not None:self.scan_timer.start();return
        lib=w.lib;hidden=set(w.hidden)|{tid for bag in w.removals.values() for tid in bag}
        tracks=[dict(id=t.track_id,path=str(t.path),native=dict(id=t.native_id,uuid=t.source_uuid) if t.source_uuid else None)
                for t in lib.tracks_by_id.values() if t.track_id not in hidden]
        identity=(str(lib.source),lib.source_library_id)
        prior=deepcopy(self.cache) if self._cache_identity==identity else {}
        load_saved=self._cache_identity!=identity
        cache_path=self.tags.data_root/'media-format-cache.json'
        self.scan_again=False
        def work():
            from mutagen import MutagenError
            known=_load_format_cache(cache_path,*identity) if load_saved else prior
            result={}
            for track in tracks:
                if worker.cancellation.is_set():break
                path=Path(track['path'])
                try:
                    path=path.resolve(strict=True);s=path.stat();epoch=analysis.identity(s);key=str(path)
                    if key in result:result[key]['tracks'].append(track);continue
                    if getattr(s,'st_flags',0)&getattr(stat,'SF_DATALESS',0x40000000):
                        facts=dict(status='deferred',reason='Cloud file is not local')
                    elif known.get(key,{}).get('epoch')==epoch and known[key]['facts'].get('status')=='compatible':
                        facts=known[key]['facts']
                    else:facts=compatibility.inspect(path)
                    result[key]=dict(epoch=epoch,facts=facts,tracks=[track])
                except (OSError,RuntimeError,ValueError,MutagenError) as exc:
                    result[str(path)]=dict(epoch=None,facts=dict(status='error',reason=str(exc)),tracks=[track])
            if not worker.cancellation.is_set():_save_format_cache(cache_path,*identity,result)
            return result
        worker=OneShotTask(work,log_label='Audio format check')
        def finished(task):
            if self.closing or w._shutting_down:return
            if w.lib is not lib:self.discover();return
            if isinstance(task.result,Exception):w._flash_status('Audio format check needs attention — '+str(task.result),10);return
            self.cache=task.result;self._cache_identity=identity
            self._publish_facts()
            queue=deepcopy(self.pending);jobs.validate(queue);known={j['path'] for j in queue}
            for job in queue:
                if (job['error'] and self.cache.get(job['path'],{}).get('facts',{}).get('status')=='compatible'
                        and (self.tags.data_root/'media-conversion-originals'/job['id']/'receipt.json').is_file()):
                    job['error']=''
            for path,entry in self.cache.items():
                if entry['facts']['status']!='convert' or path in known:continue
                ids=[t['id'] for t in entry['tracks']];native=[t['native'] for t in entry['tracks'] if t['native']]
                queue.append(dict(id=str(uuid4()),source=str(lib.source),library_id=lib.source_library_id,
                                  path=path,ids=ids,native=native,error=''));known.add(path)
            if queue!=self.pending:
                previous=w.settings.get(jobs.KEY);w.settings[jobs.KEY]=queue
                if not w._save_state():
                    if previous is None:w.settings.pop(jobs.KEY,None)
                    else:w.settings[jobs.KEY]=previous
                    w._flash_status('Audio conversion was not started because its queue could not be saved',10);return
            self.schedule()
            if self.scan_again:self.scan_timer.start()
        self.tasks.adopt(worker,finished);worker.start()

    def pump(self):
        w=self.window
        if self.closing or w._shutting_down or not self.pending:return
        try:jobs.validate(self.pending)
        except ValueError as exc:w._flash_status(str(exc),10);return
        job=next((j for j in self.pending if not j['error']),None)
        if job is None:return
        lib=w.lib
        if (not lib or not self.tags.verified or w._state_maintenance or w._refresh_worker is not None
                or w._device_operation_owned() or (str(lib.source),lib.source_library_id)!=(job['source'],job['library_id'])):
            self.schedule();return
        hidden=set(w.hidden)|{tid for bag in w.removals.values() for tid in bag}
        if not any(tid in lib.tracks_by_id and tid not in hidden for tid in job['ids']):
            previous=self.pending;w.settings[jobs.KEY]=[j for j in self.pending if j['id']!=job['id']]
            if not w._save_state():w.settings[jobs.KEY]=previous
            self.schedule();return
        # Do not race completed detection/prep publication. New clicks still
        # capture and save their RAM intents while this one source job runs.
        if any(j.get('track_id') in job['ids'] and j.get('state')!='failed' for j in w.settings.get('analysis_jobs',[])):
            self.schedule();return
        def work():
            try:assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
            try:
                self.tags._recover_pending(lib)
                store=self.factory(lib) if self.factory else ConversionStore(self.authority(lib),self.tags.data_root)
                for tid in job['ids']:w._prepare_grid_cache(tid)
                result=store.convert(job['path'],job['id'],native=job['native'])
                if result.get('native') and result.get('target',job['path'])!=job['path']:
                    from soundbox.rekordbox_locator_store import LocatorStore,load_authority as locator_authority
                    authority=locator_authority(self.tags.data_root/'rekordbox-media-locator-authority.json',lib.source,lib.source_library_id)
                    if authority is None:raise WriteProofError('Converted file is safe; its database update needs matching permission')
                    LocatorStore(authority,self.tags.data_root).relocate_conversion(result)
                return result
            except WriteProofError as exc:
                if str(exc)=='Close rekordbox and its background agent before editing':return dict(waiting=True)
                raise
        def finished(task):
            self.active.clear()
            if task.error:
                self.fail(job,task.error);return
            result=task.result
            if result.get('waiting'):self.schedule();return
            previous=dict(w.settings);old_imported=w.imported
            try:
                updated,imported=jobs.acknowledge(w.settings,w.imported,job,result)
                updated[jobs.KEY]=[j for j in updated.get(jobs.KEY,[]) if j['id']!=job['id']]
                w.settings.clear();w.settings.update(updated);w.imported=imported
                if not w._save_state():raise OSError('Converted file is safe; its local receipt could not be saved. Retry to finish')
            except (OSError,ValueError) as exc:
                w.settings.clear();w.settings.update(previous);w.imported=old_imported;self.fail(job,str(exc));return
            same_source=bool(w.lib and (str(w.lib.source),w.lib.source_library_id)==(job['source'],job['library_id']))
            if not self.closing and not w._shutting_down and same_source:
                target=result.get('target',job['path'])
                ids=set(job['ids']);native={(r['id'],r['uuid']) for r in result.get('native',[])}
                if target!=job['path']:
                    for track in w.lib.tracks_by_id.values():
                        if track.track_id in ids and (track.native_id,track.source_uuid) in native:
                            track.location_uri=Path(target).as_uri();track.source_native_path=target;track.size=result['size'];track.kind='WAV'
                if job['path'] in self.cache:
                    entry=self.cache.pop(job['path']);entry.update(epoch=result['epoch'],facts=result['facts'])
                    for track in entry['tracks']:track['path']=target
                    self.cache[target]=entry
                self._publish_facts()
                if target!=job['path']:imports.inject(w.lib,w.imported,repair_missing=False)
                pb=w.player_bar
                current=w.lib.tracks_by_id.get(pb.current_track())
                bound=(not job['native'] or current and (current.native_id,current.source_uuid) in {(ref['id'],ref['uuid']) for ref in job['native']})
                if pb.current_track() in ids and getattr(pb,'_src_path','') in (job['path'],target) and bound:
                    if target!=job['path']:pb._src_path=target
                    record=w.settings.get('analysis_results',{}).get(analysis.result_key(target))
                    if record:pb.detected_analysis=record;pb._analysis_parts.update(_detected_sha256=record['sha256'],_detected_epoch=record['epoch'])
                    pb.refresh_audio_detail(target)
                if target!=job['path']:
                    w._analysis_queue=[entry for entry in w._analysis_queue if entry[1].track_id not in ids]
                    w._resume_analysis_jobs()
                self.schedule()
        self.active=set(job['ids'])
        if not self.tags.run_media_job(work,finished):self.active.clear();self.schedule()

    def fail(self,job,message):
        queue=list(self.pending)
        for index,current in enumerate(queue):
            if current['id']==job['id']:queue[index]={**current,'error':message}
        self.window.settings[jobs.KEY]=queue
        self.window._save_state()
        if not self.closing:self.window._flash_status('Audio conversion needs attention — '+message,12)
        self.schedule()

    def stop(self):
        self.closing=True;self.timer.stop();self.scan_timer.stop();self.tasks.drain()
