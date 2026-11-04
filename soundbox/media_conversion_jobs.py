"""Durable media intents and transfer of the latest saved musical drafts."""
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from . import analysis,prep_edits

KEY='media_conversion_jobs'
DONE='media_conversion_receipts'


def validate(queue):
    if not isinstance(queue,list) or len(queue)>10000:raise ValueError('Invalid media conversion queue')
    seen=set()
    for job in queue:
        if (not isinstance(job,dict) or set(job)!={'id','source','library_id','path','ids','native','error'}
                or any(not isinstance(job[k],str) or not job[k] for k in ('id','source','library_id','path'))
                or not isinstance(job['error'],str) or not isinstance(job['ids'],list) or not job['ids']
                or any(not isinstance(tid,str) or not tid for tid in job['ids'])
                or not isinstance(job['native'],list) or job['path'] in seen):
            raise ValueError('Invalid media conversion identity')
        if str(UUID(job['id']))!=job['id'] or not Path(job['path']).is_absolute():raise ValueError('Invalid media conversion path')
        for ref in job['native']:
            if set(ref)!={'id','uuid'} or not all(isinstance(v,str) and v for v in ref.values()):raise ValueError('Invalid native conversion binding')
        seen.add(job['path'])


def acknowledge(settings,imported,job,receipt):
    """Read GUI-current state, preserving edits made during conversion."""
    # Only replacement namespaces and records belong to this acknowledgement.
    # Unknown settings and unrelated saved songs retain their original owners.
    updated,records=dict(settings),dict(imported)
    if receipt.get('unchanged'):return updated,records
    if (receipt['path']!=job['path'] or receipt['operation_id']!=job['id']):raise ValueError('Media acknowledgement identity changed')
    path=receipt['path'];target=receipt.get('target',path);before,after=receipt['before_sha256'],receipt['after_sha256']
    ids=set(job['ids'])
    for tid in job['ids']:
        record=records.get(tid)
        if record:
            if str(Path(record['path']).resolve(strict=False))!=path:continue
            record=deepcopy(record);records[tid]=record
            record['size']=receipt['size'];record['path']=target
            if target!=path:record.pop('file_bookmark',None)
    saved=updated.get('analysis_results',{});key=analysis.result_key(path)
    if key in saved:
        saved=dict(saved);updated['analysis_results']=saved
        record=saved[key];analysis.validate_result(record)
        if record['sha256'] not in (before,after):raise ValueError('Saved analysis belongs to different audio')
        duration=receipt['facts']['duration']
        if abs(record['duration']-duration)>1/receipt['facts']['sample_rate']:
            raise ValueError('Converted audio changed the saved analysis timing')
        result=deepcopy(record)
        result.setdefault('conversion_origin',dict(sha256=record['sha256'],epoch=record['epoch'],size=record['size'],duration=record['duration']))
        result.update(path=target,sha256=after,epoch=receipt['epoch'],size=receipt['size'],duration=duration)
        for field in ('grid_draft','cue_draft'):
            if result.get(field):result[field]['base_sha256']=after
        result['media_conversion']=dict(operation_id=job['id'],original=receipt['original'],before_sha256=before,after_sha256=after)
        analysis.validate_result(result)
        new_key=analysis.result_key(target)
        if new_key!=key:
            if new_key in saved:raise ValueError('The new audio path already has independent saved analysis')
            saved.pop(key)
        saved[new_key]=result
    pending=updated.get(prep_edits.KEY,[]);prep_edits.validate(pending)
    pending=list(pending)
    for index,intent in enumerate(pending):
        if intent['track_id'] not in ids:continue
        if intent['path']!=path:continue
        if (intent['prepared'] is not None
                or intent['sha256'] and intent['sha256'] not in (before,after)):
            raise ValueError('Pending musical save cannot be rebound to this conversion')
        intent=deepcopy(intent);pending[index]=intent
        intent.update(path=target,epoch=receipt['epoch'],sha256=after if intent['sha256'] else '')
    prep_edits.validate(pending)
    if prep_edits.KEY in settings:updated[prep_edits.KEY]=pending
    done=updated.get(DONE,{})
    if not isinstance(done,dict):raise ValueError('Invalid media conversion receipts')
    done=dict(done);updated[DONE]=done
    done[job['path']]=dict(operation_id=job['id'],target=target,before_sha256=before,after_sha256=after,
                         original=receipt['original'],epoch=receipt['epoch'],facts=receipt['facts'])
    if target!=path:
        from . import analysis_jobs,shared_imports
        detection=list(updated.get(analysis_jobs.KEY,[]))
        for index,pending_job in enumerate(detection):
            if pending_job['track_id'] in ids and pending_job['path']==path:
                pending_job=deepcopy(pending_job);detection[index]=pending_job;pending_job['path']=target
        if analysis_jobs.KEY in settings:updated[analysis_jobs.KEY]=detection
        imports=list(updated.get(shared_imports.KEY,[]))
        for index,pending_import in enumerate(imports):
            if any(t['id'] in ids and t['path']==path for t in pending_import['tracks']):
                if pending_import['request'] is not None or pending_import['bindings']:
                    raise ValueError('A prepared native import cannot change its audio path')
                pending_import=deepcopy(pending_import);imports[index]=pending_import
                for track in pending_import['tracks']:
                    if track['id'] in ids and track['path']==path:track['path']=target
        if shared_imports.KEY in settings:updated[shared_imports.KEY]=imports
    return updated,records
