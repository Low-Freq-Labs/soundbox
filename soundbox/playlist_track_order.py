"""Reposition regular-playlist occurrences using accepted membership commands."""
from collections import Counter
from copy import deepcopy

from .rekordbox_playlist_store import batch_for, request_for
from .playlist_membership_replay import ENCODING
from .rekordbox_write import WriteProofError


def copy_intents(value):
    """Detach validated flat membership envelopes without copying each scalar.

    All other values retain ordinary deepcopy semantics. This copies data only;
    callers still validate intentions, source ownership and durable admissions.
    """
    memo = {}
    scalar_types = (str, int, bool, type(None))
    def seed(request):
        if (type(request) is not dict or any(type(key) is not str for key in request)
                or request.get('operation') != 'batch' or request.get('plan_encoding') != ENCODING
                or set(request) != {'operation_id', 'operation', 'revision', 'requests', 'plan_encoding'}
                or type(request['requests']) is not list or len(request['requests']) != 2
                or any(type(cell) not in scalar_types for key, cell in request.items() if key != 'requests')):return
        children = []
        for child in request['requests']:
            if (type(child) is not dict or any(type(key) is not str for key in child)
                    or type(child.get('tracks')) is not list
                    or any(type(cell) not in scalar_types for key, cell in child.items() if key != 'tracks')):return
            tracks = child['tracks']
            if any(type(track) is not dict or any(type(key) is not str for key in track)
                   or any(type(cell) not in scalar_types for cell in track.values())
                   for track in tracks):return
            children.append({**child, 'tracks': [dict(track) for track in tracks]})
        if [child.get('operation') for child in children] != ['remove', 'append']:return
        memo[id(request)] = {**request, 'requests': children}
    if type(value) is dict:
        seed(value); seed(value.get('request'))
    elif type(value) is list:
        for job in value:
            if type(job) is dict:seed(job.get('request'))
    return deepcopy(value, memo)


def request(library, identity, selected, anchor=None, *, after=False):
    """Return one atomic remove/append batch plus old/new occurrence pairs.

    `selected` and `anchor` are captured full native membership rows. Keep the
    longest desired prefix already in source-relative order; rebuild only its
    complement, including hidden entries and duplicates. Native removal still
    updates survivor clocks; the marked replay derives those final rows without
    retaining the accepted composition's quadratic intermediate change chain.
    """
    catalog = library.source_playlist_catalog
    targets = [row for row in catalog['nodes'] if row['ID'] == identity and row['rb_local_deleted'] == 0]
    if len(targets) != 1 or targets[0]['Attribute'] != 0:
        raise WriteProofError('Only regular playlists have an authored song order')
    rows = sorted((row for row in catalog['members']
                   if row['PlaylistID'] == identity and row['rb_local_deleted'] == 0),
                  key=lambda row: row['TrackNo'])
    by_id = {row['ID']: row for row in rows}
    if len(by_id) != len(rows) or [row['TrackNo'] for row in rows] != list(range(1, len(rows) + 1)):
        raise WriteProofError('Refresh the playlist before moving its songs')
    selected_ids = {row['ID'] for row in selected}
    if not selected_ids or len(selected_ids) != len(selected):
        raise WriteProofError('Choose distinct playlist entries to move')
    for captured in [*selected, *([anchor] if anchor is not None else [])]:
        current = by_id.get(captured['ID'])
        if current is None or any(current.get(key) != captured.get(key)
                                  for key in ('UUID', 'PlaylistID', 'ContentID')):
            raise WriteProofError('The dragged playlist entries changed; choose them again')
    boundary = (rows.index(by_id[anchor['ID']]) + int(after)) if anchor is not None else len(rows)
    insertion = sum(row['ID'] not in selected_ids for row in rows[:boundary])
    moving = [row for row in rows if row['ID'] in selected_ids]
    remaining = [row for row in rows if row['ID'] not in selected_ids]
    desired = remaining[:insertion] + moving + remaining[insertion:]
    positions = {row['ID']: index for index, row in enumerate(rows)}
    prefix, previous = 0, -1
    for row in desired:
        position = positions[row['ID']]
        if position <= previous:break
        prefix += 1; previous = position
    if prefix == len(rows):
        return None, []
    retained = {row['ID'] for row in desired[:prefix]}
    old_tail = [row for row in rows if row['ID'] not in retained]
    new_tail = desired[prefix:]
    if len(old_tail) * 2 > 16384:
        raise WriteProofError('This move exceeds the accepted playlist batch size')
    if Counter(row['ContentID'] for row in old_tail) != Counter(row['ContentID'] for row in new_tail):
        raise WriteProofError('Playlist movement must preserve every song occurrence')
    tracks = {track.native_id: track for track in library.tracks_by_id.values()}
    if any(row['ContentID'] not in tracks or not tracks[row['ContentID']].source_uuid
           or tracks[row['ContentID']].imported for row in old_tail):
        raise WriteProofError('Refresh every native song identity before moving playlist entries')
    removed = list(reversed(old_tail))
    remove = request_for(catalog, 'remove', identity=identity,
                         tracks=[tracks[row['ContentID']] for row in removed])
    # Every removed occurrence came from the unique, ordered current catalog
    # above. Attach those exact pins without rescanning the complete library
    # once per occurrence; writer admission/readback still independently checks them.
    for selected, row in zip(remove['tracks'], removed, strict=True):
        selected.update(membership_id=row['ID'], source_membership_uuid=row['UUID'])
    append = request_for(catalog, 'append', identity=identity, allow_duplicate=True,
                         tracks=[tracks[row['ContentID']] for row in new_tail])
    batch = batch_for(catalog, [remove, append], plan_encoding=ENCODING)
    return batch, list(zip(new_tail, batch['requests'][1]['tracks'], strict=True))
