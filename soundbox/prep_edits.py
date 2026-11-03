"""Durable working intentions, not a second musical library or source authority."""
from copy import deepcopy
import hashlib
import json
import math
from uuid import UUID, uuid4

from .rekordbox_grid import Beat, transform_beats

KEY = 'pending_prep_edits'


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _uuid(value):
    try:return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:return False


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cues(values):
    return sorted([dict(kind=c['kind'], slot=c['slot'], position_ms=c['position_ms'],
                        end_ms=c.get('end_ms', -1), type=c.get('type', 'single'),
                        label=c.get('label', c.get('comment', ''))) for c in values or []],
                  key=lambda c: (c['kind'], c['slot']))


def cue_edit(values, page, slot, operation, position=None):
    if page not in ('hot', 'memory') or type(slot) is not int or not 0 <= slot < 8:
        raise ValueError('Invalid cue slot')
    result = cues(values)
    target = next((c for c in result if (c['kind'], c['slot']) == (page, slot)), None)
    if operation == 'delete':
        if target is None:return result
        result.remove(target)
    elif operation == 'add':
        if target is not None:return result
        if type(position) is not int or position < 0:raise ValueError('Invalid cue position')
        if page == 'memory' and any(c['kind'] == page and c['position_ms'] == position for c in result):return result
        result.append(dict(kind=page, slot=slot, position_ms=position, end_ms=-1, type='single', label=''))
    else:raise ValueError('Invalid cue operation')
    memory = sorted((c for c in result if c['kind'] == 'memory'), key=lambda c: c['position_ms'])
    for i, cue in enumerate(memory):cue['slot'] = i
    if page=='memory' and operation=='add' and len(memory) > 8:raise ValueError('All memory cue slots are occupied')
    return cues([c for c in result if c['kind'] == 'hot']+memory)


def grid_edit(rows, operation, value, duration, *, native=False):
    before = tuple(Beat(r[0], round(r[2]*100), r[1]) for r in rows)
    end = math.floor(duration*1000)
    after = transform_beats(before, operation, value, end_bounds_ms=(end, end))
    # Native shifts retain the terminal grid row even after it moves past EOF.
    # That row may return inside the audio on a later left nudge.
    if not native:after = tuple(b for b in after if b.time_ms <= end)
    if not after:raise ValueError('Grid edit extends beyond the captured audio')
    return [[b.number, b.time_ms, b.tempo/100] for b in after]


def operation(job, kind, action, value=0, *, page='', slot=0, position=None):
    result = deepcopy(job)
    before = result['grid'] if kind == 'grid' else result['cues']
    after = (grid_edit(before, action, value, result['duration'],native=result['version']==2) if kind == 'grid'
             else cue_edit(before, page, slot, action, position))
    if after == before:return result
    if kind == 'cue' and position is not None and position > result['duration']*1000:
        raise ValueError('Cue is outside the loaded audio')
    result['operations'].append(dict(id=str(uuid4()), kind=kind, operation=action, value=value,
        page=page, slot=slot, position=position, before=digest(before), after=digest(after)))
    result['grid' if kind == 'grid' else 'cues'] = after
    validate([result])
    return result


def validate(queue):
    if not isinstance(queue, list) or len(queue) > 256:raise ValueError('Invalid pending prep queue')
    seen = set()
    for job in queue:
        if (not isinstance(job, dict) or job.get('version') not in (1,2)
                or not all(isinstance(job.get(k), str) for k in ('id','track_id','source','library_id','path','sha256','error'))
                or not job['track_id'] or job['track_id'] in seen or not job['path'].startswith('/')
                or not isinstance(job.get('epoch'),list) or len(job['epoch']) != 5 or any(type(n) is not int or n < 0 for n in job['epoch'])
                or not _uuid(job['id']) or type(job.get('native_ready')) is not bool
                or type(job.get('duration')) not in (int,float) or not math.isfinite(job['duration']) or not 0 < job['duration'] <= 86400
                or not isinstance(job.get('grid'), list) or len(job['grid']) > 400000
                or not isinstance(job.get('cues'), list) or len(job['cues']) > 32
                or not isinstance(job.get('operations'), list) or not 0 < len(job['operations']) <= 2048
                or job.get('prepared') is not None and not isinstance(job['prepared'], dict)):
            raise ValueError('Invalid pending prep identity')
        if job['version']==2 and not job['native_ready']:raise ValueError('Native prep requires registered analysis')
        if (type(job.get('identity_pending',False)) is not bool or job.get('identity_pending') and
                (job['version']!=1 or not _hex(job['sha256']) or job['prepared'] is not None)):
            raise ValueError('Invalid pending prep audio proof')
        seen.add(job['track_id'])
        if job['sha256'] and not _hex(job['sha256']):
            raise ValueError('Invalid pending audio digest')
        cue_ids = set()
        for cue in job['cues']:
            if (not isinstance(cue,dict) or cue.get('kind') not in ('hot','memory')
                    or type(cue.get('slot')) is not int or not 0 <= cue['slot'] < 32
                    or cue['kind']=='hot' and cue['slot'] >= 16
                    or type(cue.get('position_ms')) is not int or not 0 <= cue['position_ms'] <= job['duration']*1000
                    or type(cue.get('end_ms')) is not int
                    or cue.get('type') not in ('single','loop') or not isinstance(cue.get('label'),str)
                    or (cue['type']=='single' and cue['end_ms']!=-1)
                    or (cue['type']=='loop' and not cue['position_ms'] < cue['end_ms'] <= job['duration']*1000)
                    or (cue['kind'],cue['slot']) in cue_ids):
                raise ValueError('Invalid pending cue')
            cue_ids.add((cue['kind'],cue['slot']))
        previous = -1
        for row in job['grid']:
            if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int or row[0] not in (1,2,3,4)
                    or type(row[1]) is not int or not previous < row[1] <= (0xffffffff if job['version']==2 else job['duration']*1000)
                    or type(row[2]) not in (int,float) or not math.isfinite(row[2]) or not 1 <= row[2] <= 655.35):
                raise ValueError('Invalid pending grid')
            previous = row[1]
        ids = set()
        for op in job['operations']:
            if (not isinstance(op,dict) or op.get('kind') not in ('grid','cue')
                    or not _uuid(op.get('id')) or op['id'] in ids
                    or op.get('operation') not in (('shift','downbeat','bpm','double','halve') if op['kind']=='grid' else ('add','delete'))
                    or type(op.get('value')) is not int
                    or any(not _hex(op.get(k)) for k in ('before','after'))
                    or op['kind']=='cue' and (op.get('page') not in ('hot','memory')
                        or type(op.get('slot')) is not int or not 0 <= op['slot'] < 8
                        or op.get('position') is not None and (type(op['position']) is not int or not 0 <= op['position'] <= job['duration']*1000)
                        or op['operation']=='add' and op.get('position') is None)):
                raise ValueError('Invalid pending prep operation')
            ids.add(op['id'])
        prepared=job['prepared']
        if prepared is not None:
            first=job['operations'][0];request=prepared.get('request')
            if (prepared.get('operation_id')!=first['id'] or prepared.get('kind')!=first['kind']
                    or not isinstance(request,dict) or not _hex(request.get('revision'))
                    or not isinstance(request.get('track_id'),str) or not request['track_id']
                    or not _uuid(request.get('track_uuid')) or request.get('operation')!=first['operation']):
                raise ValueError('Invalid prepared prep request')
            expected=({'track_id','track_uuid','revision','operation','value','nudges'} if first['kind']=='grid'
                      else {'track_id','track_uuid','revision','page','slot','operation','position_ms','cue_id','cue_uuid'})
            if (set(request)!=expected or first['kind']=='grid' and (request['value']!=first['value'] or request['nudges'] not in ([],()))
                    or first['kind']=='cue' and (request['page']!=first['page'] or request['slot']!=first['slot']
                        or request['position_ms']!=first['position']
                        or not all(isinstance(request[k],str) for k in ('cue_id','cue_uuid')))):
                raise ValueError('Prepared prep request differs from the intention')
    return queue


def upgrade_native(job, rows, cue_rows, *, committed=False):
    """Replay proven legacy intentions with native EOF semantics; never source SQL.

    A committed prepared request must already have a verified native receipt.
    Positive shift is exactly invertible, then its BEFORE digest proves the base.
    Other ambiguous committed transformations retain their evidence for review.
    """
    validate([job]);result=deepcopy(job)
    if result['version']==2:return result
    grid=deepcopy(rows);cue_values=cues(cue_rows);first=result['operations'][0]
    if committed:
        if first['kind']!='grid' or first['operation']!='shift' or first['value']<=0:
            raise ValueError('Legacy committed prep needs an exact invertible base')
        grid=grid_edit(grid,'shift',-first['value'],result['duration'],native=True)
        if digest(grid)!=first['before']:raise ValueError('Legacy prep original grid cannot be proven')
    legacy_grid=deepcopy(grid);legacy_cues=deepcopy(cue_values)
    for op in result['operations']:
        is_grid=op['kind']=='grid'
        before=legacy_grid if is_grid else legacy_cues
        if digest(before)!=op['before']:raise ValueError('Legacy prep intention chain changed')
        after=(grid_edit(before,op['operation'],op['value'],result['duration']) if is_grid else
               cue_edit(before,op['page'],op['slot'],op['operation'],op['position']))
        if digest(after)!=op['after']:raise ValueError('Legacy prep operation cannot be verified')
        native_before=grid if is_grid else cue_values
        native_after=(grid_edit(grid,op['operation'],op['value'],result['duration'],native=True) if is_grid else after)
        op['before']=digest(native_before);op['after']=digest(native_after)
        if is_grid:legacy_grid=after;grid=native_after
        else:legacy_cues=after;cue_values=native_after
    if (any(op['kind']=='grid' for op in job['operations']) and legacy_grid!=job['grid']
            or legacy_cues!=cues(job['cues'])):
        raise ValueError('Legacy prep final view changed')
    result.update(version=2,native_ready=True,grid=grid,cues=cue_values,error='')
    validate([result]);return result
