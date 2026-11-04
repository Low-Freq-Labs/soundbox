"""Durable pending detection intents; completed musical data stays elsewhere.

An active job remains queued on disk until its result and retirement are saved
together. A restart can therefore retry unfinished work without losing an intent
or repeating a committed result. These records grant no native edit authority.
"""
from copy import deepcopy
from pathlib import Path
from uuid import UUID,uuid4

from .analysis import options

KEY='analysis_jobs'
FORMAT='soundbox-analysis-job-v1'
FIELDS={'format','id','source','library_id','track_id','path','source_uuid','preferences','state','error'}


def validate(jobs):
    if not isinstance(jobs,list) or len(jobs)>100000:raise ValueError('Saved analysis queue is invalid')
    ids=set();tracks=set()
    for job in jobs:
        if not isinstance(job,dict) or not FIELDS<=set(job)<=FIELDS|{
                'waveform','automatic','phase','component_sha256','waveform_sha256','waveform_epoch'} or job['format']!=FORMAT:
            raise ValueError('Saved analysis intent is invalid')
        if type(job.get('waveform',False)) is not bool:raise ValueError('Invalid waveform rebuild intention')
        if type(job.get('automatic',False)) is not bool:raise ValueError('Invalid automatic analysis intention')
        try:valid_id=str(UUID(job['id']))==job['id'] and UUID(job['id']).version==4
        except (ValueError,TypeError,AttributeError):valid_id=False
        if not valid_id or job['id'] in ids:raise ValueError('Ambiguous analysis operation identity')
        for k in ('source','library_id','track_id','path','source_uuid','error'):
            if not isinstance(job[k],str) or '\0' in job[k] or len(job[k])>8192:
                raise ValueError('Invalid analysis identity text')
        path=Path(job['path'])
        if not job['track_id'] or not path.is_absolute() or '..' in path.parts or str(path)!=job['path']:
            raise ValueError('Canonical analysis path and track identity required')
        if job['state'] not in ('queued','failed') or (job['state']=='queued' and job['error']):
            raise ValueError('Unknown analysis intent state')
        if not isinstance(job['preferences'],dict) or options(job['preferences'])!=job['preferences']:
            raise ValueError('Analysis intent preferences changed')
        if 'phase' in job and (job['phase'] not in ('bpm','key') or not job['preferences'][job['phase']]):
            raise ValueError('Invalid analysis component phase')
        digest=job.get('component_sha256')
        if digest is not None and (job.get('phase')!='key' or not isinstance(digest,str)
                or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('Invalid captured analysis component identity')
        if job.get('phase')=='key' and job['preferences']['bpm'] and digest is None:
            raise ValueError('Completed BPM identity is required before key detection')
        if 'waveform_sha256' in job or 'waveform_epoch' in job:
            wave=job.get('waveform_sha256');epoch=job.get('waveform_epoch')
            if (not isinstance(wave,str) or len(wave)!=64 or any(c not in '0123456789abcdef' for c in wave)
                    or not isinstance(epoch,list) or len(epoch)!=5
                    or any(type(n) is not int or n<0 for n in epoch) or epoch[2]<=0
                    or digest is not None and digest!=wave):
                raise ValueError('Invalid prepared waveform identity')
        identity=(job['source'],job['library_id'],job['track_id'])
        if identity in tracks:raise ValueError('Duplicate pending analysis track')
        ids.add(job['id']);tracks.add(identity)
    return jobs


def matches(job,library,track):
    return (job['source']==str(library.source or '') and job['library_id']==str(library.source_library_id or '')
            and job['track_id']==track.track_id and job['path']==str(track.path)
            and (job['source_uuid']==track.source_uuid or
                 not job['source_uuid'] and track.source_native_id and not track.source_analysis_path))


def phase(job):
    """Legacy queued dictionaries start at their first requested component."""
    return job.get('phase') or ('bpm' if job['preferences']['bpm'] else
                               'key' if job['preferences']['key'] else None)


def component_preferences(job):
    component=phase(job)
    if component is None:return dict(job['preferences'])
    return {**job['preferences'],'bpm':component=='bpm','key':component=='key',
            'first_cue':job['preferences']['first_cue'] if component=='bpm' else False}


def advance(jobs,identity,sha256):
    """Atomically checkpoint BPM, keeping the original preference/intent wire."""
    validate(jobs)
    index=next((i for i,j in enumerate(jobs) if j['id']==identity),None)
    if index is None:raise ValueError('Analysis intention was replaced before completion')
    job=jobs[index]
    if job['state']!='queued':raise ValueError('Analysis intention no longer permits component completion')
    if (not isinstance(sha256,str) or len(sha256)!=64 or any(c not in '0123456789abcdef' for c in sha256)
            or (job.get('component_sha256') or job.get('waveform_sha256')) not in (None,sha256)):
        raise ValueError('Audio identity changed between preparation phases; saved results are preserved')
    if phase(job)=='bpm' and job['preferences']['key']:
        result=deepcopy(jobs);job=result[index]
        job.update(phase='key',component_sha256=sha256)
        validate(result)
        return result
    # The unchanged queue has already passed validation in this synchronous
    # operation. Detach survivors once without re-copying the retiring record.
    return _finish_validated(jobs,identity)


def wave_ready(jobs,identity,sha256,epoch):
    """Checkpoint an exact captured waveform without completing detection."""
    validate(jobs);result=deepcopy(jobs)
    job=next((j for j in result if j['id']==identity),None)
    if job is None:raise ValueError('Analysis intention was replaced before waveform completion')
    if job['state']!='queued':raise ValueError('Analysis intention no longer permits waveform completion')
    if job.get('waveform_sha256') not in (None,sha256):
        raise ValueError('Audio identity changed during waveform preparation; saved results are preserved')
    job.update(waveform_sha256=sha256,waveform_epoch=deepcopy(epoch))
    validate(result)
    return result


def admit(jobs,library,tracks,preferences,*,retry_failed=True,rebuild_waveform=False,automatic=False):
    validate(jobs);copied=deepcopy(jobs);prefs=options(preferences)
    by_track={(j['source'],j['library_id'],j['track_id']):j for j in copied}
    del copied
    for track in tracks:
        if automatic:
            from .analysis import automatic_music_allowed
            if not automatic_music_allowed(track.total_time):continue
        source=str(library.source or '');library_id=str(library.source_library_id or '')
        key=(source,library_id,track.track_id);prior=by_track.get(key)
        if prior and matches(prior,library,track) and (prior['state']=='queued' or not retry_failed):
            if rebuild_waveform:prior['waveform']=True
            continue
        resume=(prior and matches(prior,library,track) and prior.get('phase')=='key'
                and prior.get('component_sha256') and prior['preferences']==prefs)
        # Removing/reinserting keeps the original queue's retry append order
        # without a list scan or retaining replaced records through the batch.
        if prior:by_track.pop(key)
        created=dict(format=FORMAT,id=str(uuid4()),source=source,library_id=library_id,
            track_id=track.track_id,path=str(track.path),source_uuid=track.source_uuid,preferences=dict(prefs),state='queued',error='',
            waveform=bool(rebuild_waveform))
        by_track[key]=created
        if prefs['bpm'] or prefs['key']:created['phase']='bpm' if prefs['bpm'] else 'key'
        if resume and prior is not None:
            created.update(phase='key',component_sha256=prior['component_sha256'])
            for field in ('waveform_sha256','waveform_epoch'):
                if field in prior:created[field]=deepcopy(prior[field])
        if automatic:created['automatic']=True
    result=list(by_track.values())
    validate(result);return result


def finish(jobs,identity):
    validate(jobs)
    return _finish_validated(jobs,identity)


def _finish_validated(jobs,identity):
    if not any(j['id']==identity for j in jobs):raise ValueError('Analysis intention was replaced before completion')
    return [deepcopy(j) for j in jobs if j['id']!=identity]


def failed(jobs,identity,error):
    validate(jobs);result=deepcopy(jobs)
    for job in result:
        if job['id']==identity:
            job.update(state='failed',error=str(error).replace('\0','')[:4096]);return result
    return result
