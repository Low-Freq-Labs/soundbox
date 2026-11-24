"""Compact final effects of accepted reverse-removal and append commands.

This is a plan encoding, not another native mutation profile. Descending removal
keeps each deleted occurrence's TrackNo unchanged; only the last survivor rewrite
is observable in the committed transaction. Historical unmarked plans retain
their original reconstruction in the playlist store.
"""
from collections import Counter
from copy import deepcopy

from . import rekordbox_playlist_plan as planner
from .rekordbox_write import ConflictError, WriteProofError

ENCODING = 'membership-replay-v1'


def validate(request):
    """Admit only the bounded, identity-preserving command composition."""
    if request.get('plan_encoding') != ENCODING or len(request['requests']) != 2:
        raise WriteProofError('Unknown compact playlist plan encoding')
    remove, append = request['requests']
    if (remove['operation'] != 'remove' or append['operation'] != 'append' or not remove['tracks']
            or any(remove[key] != append[key] for key in ('playlist_id', 'playlist_uuid', 'parent'))
            or not append['allow_duplicate']
            or any('membership_id' not in track for track in remove['tracks'])
            or Counter(track['track_id'] for track in remove['tracks'])
                != Counter(track['track_id'] for track in append['tracks'])):
        raise WriteProofError('Compact replay requires exact remove/append occurrences')


def compose(playlist, members, counter, remove, append, contents, occupied):
    """Reproduce every final field of the original sequential child plans."""
    validate(dict(plan_encoding=ENCODING, requests=[remove, append]))
    rows = planner._base(playlist, members, counter)
    by_id = {row['ID']: row for row in rows}
    selected = []
    for track in remove['tracks']:
        row = by_id.get(track['membership_id'])
        if (row is None or row['UUID'] != track['source_membership_uuid']
                or row['ContentID'] != track['track_id']):
            raise ConflictError('The captured playlist occurrence changed')
        selected.append(row)
    positions = [row['TrackNo'] for row in selected]
    if positions != sorted(set(positions), reverse=True):
        raise WriteProofError('Compact removal must follow descending source positions')
    for track in (*remove['tracks'], *append['tracks']):
        content = contents.get(track['track_id'])
        if (content is None or content['UUID'] != track['uuid']
                or content['rb_local_deleted'] != 0):
            raise ConflictError('Native Collection track identity changed')
    reserved = set(occupied)
    for track in append['tracks']:
        if track['id'] in reserved or track['membership_uuid'] in reserved:
            raise ConflictError('Native membership identity collides with history')
        reserved.update((track['id'], track['membership_uuid']))

    count = len(selected)
    selected_ids = {row['ID'] for row in selected}
    kept = [row for row in rows if row['ID'] not in selected_ids]
    # Every removal advances once, plus one survivor group unless empty;
    # every append advances once. Preserve the counter row's opaque fields.
    removal_steps = 2 * count - int(not kept)
    if counter['int_1'] + removal_steps + count >= 2**63:
        raise WriteProofError('Native playlist counter is exhausted')
    current = {**counter, 'int_1': counter['int_1'] + removal_steps}
    survivors = {row['ID']: {**row, 'TrackNo': index,
                 'updated_at': remove['survivor_timestamp'],
                 'rb_local_usn': current['int_1']}
                 for index, row in enumerate(kept, 1)}
    after = [deepcopy(survivors.get(row['ID'], row))
             for row in members if row['ID'] not in selected_ids]
    for index, row in enumerate(selected):
        deleted = planner._deleted(row,
            {**counter, 'int_1': counter['int_1'] + 2 * index + 1}, remove['timestamp'])
        if deleted is not None:
            after.append(deleted)
    for index, track in enumerate(append['tracks'], len(kept) + 1):
        # Reuse the accepted constructor with an empty local set so repeated
        # appends never copy the growing playlist or its historical rows.
        child = planner.append(playlist, [], current, contents[track['track_id']],
            identity=track['id'], uuid=track['membership_uuid'],
            timestamp=append['timestamp'], allow_duplicate=True)
        after.append({**child['members'][0], 'TrackNo': index})
        current = child['counter']
    result = planner._result(members, after, counter, current)
    changes = result['changes']
    inserted = {change['id']: change for change in changes if change['before'] is None}
    # SQLite's unsorted read order is not musical order, but retain it too:
    # the original append child inserts occurrences in captured append order.
    result['changes'] = [change for change in changes
                         if change['before'] is not None and change['table'] != 'agentRegistry']
    result['changes'].extend(inserted[track['id']] for track in append['tracks'])
    result['changes'].extend(change for change in changes if change['table'] == 'agentRegistry')
    return result
