"""Revisioned cue drafts for captured imports awaiting native registration."""
from copy import deepcopy


def validate(record):
    draft = record.get('cue_draft')
    if draft is None:
        return
    if (not isinstance(draft, dict) or draft.get('base_sha256') != record['sha256']
            or draft.get('origin') != 'manual' or type(draft.get('revision')) is not int
            or not 1 <= draft['revision'] <= 2**63 - 1
            or not isinstance(draft.get('cues'), list) or len(draft['cues']) > 16):
        raise ValueError('Invalid cue draft')
    occupied = set()
    for cue in draft['cues']:
        if (not isinstance(cue, dict) or cue.get('kind') not in ('hot', 'memory')
                or type(cue.get('slot')) is not int or not 0 <= cue['slot'] < 8
                or type(cue.get('position_ms')) is not int
                or not 0 <= cue['position_ms'] <= record['duration'] * 1000
                or cue.get('type') != 'single' or cue.get('end_ms') != -1
                or cue.get('label') != '' or (cue['kind'], cue['slot']) in occupied):
            raise ValueError('Invalid cue draft entry')
        occupied.add((cue['kind'], cue['slot']))


def cues(record):
    validate(record)
    if record.get('cue_draft') is not None:
        return deepcopy(record['cue_draft']['cues'])
    if record.get('first_cue') is not None:
        return [dict(kind='memory', slot=0, position_ms=record['first_cue'],
                     type='single', end_ms=-1, label='')]
    return []


def edit(record, kind, slot, operation, position=None):
    from .analysis import validate_result
    validate_result(record)
    if (kind not in ('hot', 'memory') or type(slot) is not int or not 0 <= slot < 8
            or operation not in ('add', 'delete')):
        raise ValueError('Invalid cue edit')
    entries = cues(record)
    target = next((c for c in entries if (c['kind'], c['slot']) == (kind, slot)), None)
    if operation == 'delete':
        if target is None:
            return record
        entries.remove(target)
    else:
        if target is not None:
            return record
        if type(position) is not int or not 0 <= position <= record['duration'] * 1000:
            raise ValueError('Cue is outside the captured audio')
        if kind == 'memory' and any(c['kind'] == kind and c['position_ms'] == position for c in entries):
            return record
        entries.append(dict(kind=kind, slot=slot, position_ms=position,
                            type='single', end_ms=-1, label=''))
    memory = sorted((c for c in entries if c['kind'] == 'memory'), key=lambda c: c['position_ms'])
    for number, cue in enumerate(memory):
        cue['slot'] = number
    entries = sorted((c for c in entries if c['kind'] == 'hot'), key=lambda c: c['slot']) + memory
    result = deepcopy(record)
    result['cue_draft'] = dict(origin='manual', base_sha256=record['sha256'],
        revision=(record.get('cue_draft') or {}).get('revision', 0) + 1, cues=entries)
    validate_result(result)
    return result
