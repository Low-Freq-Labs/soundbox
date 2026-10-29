"""Pure, provisional native membership plans. No database or source-write API.

Only the observed unsynced ordinary-playlist protocol is modeled. Creation,
playlist deletion, synced/cloud rows and intelligent playlists remain unproven.
"""
from copy import deepcopy
from uuid import UUID

from soundbox.rekordbox_write import _digest


def _unsynced(row):
    for name in ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced'):
        if type(row.get(name)) is not int or row[name] != 0:
            raise ValueError('Unverified playlist sync metadata')
    if row.get('usn') is not None:
        raise ValueError('Synced membership protocol is not accepted')


def _base(playlist, members, counter):
    _unsynced(playlist)
    if playlist.get('Attribute') != 0 or playlist.get('SmartList') not in ('',None):
        raise ValueError('Only ordinary playlists have a membership proof')
    if counter.get('registry_id') != 'localUpdateCount' or type(counter.get('int_1')) is not int or counter['int_1'] < 0:
        raise ValueError('Unknown native counter')
    rows = sorted(deepcopy(members),key=lambda row:row['TrackNo'])
    if any(row.get('PlaylistID') != playlist['ID'] for row in rows):
        raise ValueError('Membership belongs to another playlist')
    if [r['TrackNo'] for r in rows] != list(range(1,len(rows)+1)):
        raise ValueError('Native membership order is ambiguous')
    if len({r['ID'] for r in rows}) != len(rows) or len({r['ContentID'] for r in rows}) != len(rows):
        raise ValueError('Native membership identities are ambiguous')
    return rows, dict(counter)


def _result(playlist, before, counter, rows, current):
    left, right = {r['ID']:r for r in before}, {r['ID']:r for r in rows}
    changes = [dict(table='djmdSongPlaylist', id=key, before=left.get(key), after=right.get(key))
               for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)]
    if current != counter:
        changes.append(dict(table='agentRegistry',id='localUpdateCount',before=counter,after=current))
    return dict(revision=_digest([playlist,before,counter]),changes=changes,members=rows,counter=current,
                source_write_authority=False)


def append(playlist, members, counter, content, *, identity, uuid, timestamp):
    rows, current = _base(playlist,members,counter)
    if content.get('rb_local_deleted') or not content.get('ID') or not content.get('UUID'):
        raise ValueError('Content identity is unavailable')
    UUID(identity); UUID(uuid)
    if any(r['ID'] == identity or r.get('UUID') == uuid for r in rows):
        raise ValueError('New membership identity collides')
    if not any(r['ContentID'] == content['ID'] for r in rows):
        current['int_1'] += 1
        rows.append(dict(ID=identity,PlaylistID=playlist['ID'],ContentID=content['ID'],TrackNo=len(rows)+1,
            UUID=uuid,rb_data_status=0,rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,
            usn=None,rb_local_usn=current['int_1'],created_at=timestamp,updated_at=timestamp))
    return _result(playlist,members,counter,rows,current)


def remove(playlist, members, counter, membership_id, *, timestamp):
    rows, current = _base(playlist,members,counter)
    target = next((r for r in rows if r['ID']==membership_id),None)
    if target is None:
        raise ValueError('Expected membership disappeared')
    _unsynced(target)
    current['int_1'] += 1
    rows.remove(target)
    for index,row in enumerate(rows,1):
        if row['TrackNo'] != index:
            _unsynced(row)
            current['int_1'] += 1
            row.update(TrackNo=index,updated_at=timestamp,rb_local_usn=current['int_1'])
    return _result(playlist,members,counter,rows,current)
