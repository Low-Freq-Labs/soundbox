"""Pending musical grid edits for unregistered imports, never native authority.

The captured detector record remains intact. A separate revisioned draft uses
the same row arithmetic as the accepted native editor and follows the captured
path/digest when a track later acquires a native identity.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .rekordbox_grid import Beat, transform_beats


def validate_draft(record):
    draft = record.get('grid_draft')
    if draft is None:
        return
    if (not isinstance(draft, dict) or type(draft.get('revision')) is not int
            or not 1 <= draft['revision'] <= 2**63 - 1
            or draft.get('base_sha256') != record['sha256']
            or draft.get('origin') != 'manual' or not draft.get('beats')):
        raise ValueError('Invalid manual grid draft')
    # Reuse the ordinary captured-grid validation without recursing into drafts.
    from .analysis import validate_result
    candidate = {**record, 'beats': draft['beats'], 'bpm': draft.get('bpm'), 'first_cue': None}
    candidate.pop('grid_draft', None)
    validate_result(candidate)
    if candidate['bpm'] is None:
        raise ValueError('Manual grid draft requires a BPM')


def effective_result(record):
    """Resolve a draft for consumers while retaining the original estimate."""
    if record is None or not record.get('grid_draft'):
        return record
    validate_draft(record)
    draft = record['grid_draft']
    return {**record, 'beats': deepcopy(draft['beats']), 'bpm': draft['bpm']}


def edit(record, operation, value=0):
    from .analysis import validate_result
    validate_result(record)
    current = effective_result(record)
    rows = current.get('beats') or []
    if not rows:
        raise ValueError('Analyze BPM / Grid before editing this track')
    # PQTZ represents tempo in hundredths; reject unrepresentable input rather
    # than changing arbitrary supplied floating-point rows without a contract.
    beats = tuple(Beat(row[0], round(row[2] * 100), row[1]) for row in rows)
    end = math.floor(record['duration'] * 1000)
    changed = transform_beats(beats, operation, value, end_bounds_ms=(end, end))
    # A detected row can sit exactly at EOF. Translating it past the captured
    # end discards that trailing row, just as negative shifts discard rows
    # before zero; it must not prevent an otherwise ordinary nudge/downbeat.
    changed = tuple(beat for beat in changed if beat.time_ms <= end)
    if changed == beats:
        return record
    if not changed or len(changed) > 400000:
        raise ValueError('Grid edit extends beyond the captured audio')
    result = deepcopy(record)
    result['grid_draft'] = dict(origin='manual', base_sha256=record['sha256'],
        revision=(record.get('grid_draft') or {}).get('revision', 0) + 1,
        beats=[[b.number, b.time_ms, b.tempo / 100] for b in changed],
        bpm=changed[0].tempo / 100 if operation in ('bpm', 'double', 'halve') else current['bpm'])
    validate_result(result)
    return result
