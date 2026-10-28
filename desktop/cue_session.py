"""Small in-memory cue overlay. Native identities remain writer-owned."""
from dataclasses import dataclass
from uuid import uuid4
from types import SimpleNamespace

from soundbox.rekordbox_db import _cue_color


@dataclass(frozen=True)
class CueIntent:
    track_id: str
    track_uuid: str
    path: str
    page: str
    slot: int
    operation: str
    position_ms: int | None
    key: str


def native_key(ref):
    return f"native:{ref['id']}:{ref['uuid']}"


def cue_entries(track):
    refs = {(r['kind'], r['slot']): r for r in track.source_cue_refs}
    return [dict(cue, _key=native_key(refs[(cue['kind'], cue['slot'])]))
            for cue in track.source_cues or []
            if (cue['kind'], cue['slot']) in refs]


def overlay(track, intents, aliases):
    entries = cue_entries(track)
    for intent in intents:
        if intent.track_id != track.track_id:
            continue
        key = aliases.get(intent.key, intent.key)
        if intent.operation == 'delete':
            entries = [c for c in entries if c['_key'] != key]
        else:
            entries.append(dict(kind=intent.page, slot=intent.slot,
                                position_ms=intent.position_ms, end_ms=-1,
                                type='single', label='',
                                color=_cue_color(SimpleNamespace(), intent.slot if intent.page == 'hot' else -1),
                                _key=key))
        memory = sorted((c for c in entries if c['kind'] == 'memory'),
                        key=lambda c: (c['position_ms'], c.get('end_ms', -1), c.get('label', '')))
        for slot, cue in enumerate(memory):
            cue['slot'] = slot
        entries = sorted((c for c in entries if c['kind'] == 'hot'), key=lambda c: c['slot']) + memory
    return entries


def intent_for(track, entries, page, slot, operation, position):
    if page not in ('hot', 'memory') or type(slot) is not int or not 0 <= slot < 8:
        return None
    target = next((c for c in entries if c['kind'] == page and c['slot'] == slot), None)
    if operation == 'delete':
        if target is None:
            return None
        key = target['_key']
    elif operation == 'add':
        if target is not None or type(position) is not int or position < 0:
            return None
        # Native memory cues at an existing position are a no-op. Do not create
        # a temporary duplicate which would then disappear on read-back.
        if page == 'memory' and any(c['kind'] == page and c['position_ms'] == position for c in entries):
            return None
        key = f'session:{uuid4()}'
    else:
        return None
    return CueIntent(track.track_id, track.source_uuid, str(track.path), page,
                     slot, operation, position, key)


def display(entries):
    return [{k: v for k, v in cue.items() if k != '_key'} for cue in entries]
