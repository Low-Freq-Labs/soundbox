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
        if not isinstance(job,dict) or set(job) not in (FIELDS,FIELDS|{'waveform'}) or job['format']!=FORMAT:
            raise ValueError('Saved analysis intent is invalid')
        if type(job.get('waveform',False)) is not bool:raise ValueError('Invalid waveform rebuild intention')
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
        identity=(job['source'],job['library_id'],job['track_id'])
        if identity in tracks:raise ValueError('Duplicate pending analysis track')
        ids.add(job['id']);tracks.add(identity)
    return jobs


def matches(job,library,track):
    return (job['source']==str(library.source or '') and job['library_id']==str(library.source_library_id or '')
            and job['track_id']==track.track_id and job['path']==str(track.path)
            and (job['source_uuid']==track.source_uuid or
                 not job['source_uuid'] and track.source_native_id and not track.source_analysis_path))


def admit(jobs,library,tracks,preferences,*,retry_failed=True,rebuild_waveform=False):
    validate(jobs);result=deepcopy(jobs);prefs=options(preferences)
    for track in tracks:
        source=str(library.source or '');library_id=str(library.source_library_id or '')
        prior=next((j for j in result if (j['source'],j['library_id'],j['track_id'])==(source,library_id,track.track_id)),None)
        if prior and matches(prior,library,track) and (prior['state']=='queued' or not retry_failed):
            if rebuild_waveform:prior['waveform']=True
            continue
        if prior:result.remove(prior)
        result.append(dict(format=FORMAT,id=str(uuid4()),source=source,library_id=library_id,
            track_id=track.track_id,path=str(track.path),source_uuid=track.source_uuid,preferences=dict(prefs),state='queued',error='',
            waveform=bool(rebuild_waveform)))
    validate(result);return result


def finish(jobs,identity):
    validate(jobs)
    if not any(j['id']==identity for j in jobs):raise ValueError('Analysis intention was replaced before completion')
    return [deepcopy(j) for j in jobs if j['id']!=identity]


def failed(jobs,identity,error):
    validate(jobs);result=deepcopy(jobs)
    for job in result:
        if job['id']==identity:
            job.update(state='failed',error=str(error).replace('\0','')[:4096]);return result
    return result
