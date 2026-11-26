"""Bounded RAM continuations over existing durable single-field intentions.

No new source protocol: loss of this owner falls back to ordinary receipt replay.
Only the final complete fresh Library supplies acknowledgement values.
"""
from copy import deepcopy


def identity(job):
    return tuple(job[key] for key in ('id','source','library_id','local_id','native_id','track_uuid','path','field','value'))


class MetadataReadbackGroup:
    def __init__(self, jobs, epoch, scope):
        self.jobs = tuple(deepcopy(jobs))
        self.identities = tuple(identity(job) for job in self.jobs)
        self.epoch = epoch
        self.scope = scope
        self.token = object()
        self.reserved = False
        self.applied = {}
        self.library = None
        self.readback = None
        self.acknowledged = set()
        self.staged = set()
        self.stage_before = None
        self.staging = False
        self.stage_paths = set()

    def current(self, pending, epoch, scope):
        if self.epoch != epoch or self.scope != scope:
            return False
        expected = [value for value in self.identities if value[0] not in self.acknowledged]
        return len(pending) >= len(expected) and all(identity(job) == value
            and not job['error'] and not job['retry'] and 'companion_conflict' not in job
            for job,value in zip(pending,expected))

    def next_index(self, pending):
        if self.library is not None:
            return 0
        return next(index for index,job in enumerate(pending) if job['id'] not in self.applied)

    def record(self, job, retirement):
        if identity(job) not in self.identities or job['request'] is None:
            raise ValueError('Metadata group lost its exact durable request')
        self.applied[job['id']] = deepcopy(retirement)

    def result_for(self, job):
        result = dict(self.readback)
        matches = result['group_selections'].get((job['native_id'],job['track_uuid'],job['path']),())
        result['match_count'] = len(matches)
        result['selected'] = matches[0] if len(matches)==1 else None
        result['retirement'] = self.applied[job['id']]
        return result


def prefix(pending, limit):
    result = []
    cells = set()
    for job in pending[:limit]:
        if (job['error'] or job['retry'] or not job['track_uuid'] or 'companion_conflict' in job
                or result and (job['source'],job['library_id']) != (result[0]['source'],result[0]['library_id'])):
            break
        cell = job['native_id'],job['track_uuid'],job['path'],job['field']
        if cell in cells:
            break
        result.append(job);cells.add(cell)
    return result


def staged_patch(window, paths):
    """Collapse overlapping ACK leaves against the final detached staged roots."""
    roots={'settings':window.settings,'title_overrides':window.titles,'artist_overrides':window.overrides}
    selected=[]
    for path in sorted(paths,key=lambda row:(len(row),row)):
        if not any(path[:len(parent)]==parent for parent in selected):selected.append(path)
    replacements={};removals=[]
    for path in selected:
        value=roots;missing=False
        for part in path:
            if part not in value:
                missing=True;break
            value=value[part]
        if missing:removals.append(path)
        else:replacements[path]=value
    return replacements,removals
