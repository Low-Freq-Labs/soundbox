"""Pure native-reference playlist lifecycle plans; no publication API.

Only unsynced ordinary playlists are modeled. Creation is append-at-root, with
separate creation and rename counters as observed in native 6.8.7 captures.
"""
from copy import deepcopy
from uuid import UUID

from soundbox.rekordbox_write import _digest
from .playlist_membership import _unsynced


def _counter(counter):
    if (counter.get('registry_id') != 'localUpdateCount'
            or type(counter.get('int_1')) is not int
            or not 0 <= counter['int_1'] < 2**63-1):
        raise ValueError('Unknown or exhausted native counter')
    return {**counter, 'int_1': counter['int_1']+1}


def _name(name):
    if not isinstance(name, str) or not name.strip() or '\0' in name:
        raise ValueError('A nonempty playlist name is required')
    return name


def create(playlists, counter, *, identity, uuid, name, timestamp):
    """Append a root playlist, retaining deleted history and all existing rows."""
    if (not isinstance(identity, str) or not identity.isascii()
            or not identity.isdecimal() or not 0 < int(identity) < 2**32
            or str(int(identity)) != identity):
        raise ValueError('Native playlist identity must be canonical uint32')
    if str(UUID(uuid)) != uuid:
        raise ValueError('Native playlist UUID must be canonical')
    if any(r.get('ID') == identity or r.get('UUID') == uuid for r in playlists):
        raise ValueError('Playlist identity collides with existing history')
    siblings = [r for r in playlists if r.get('ParentID') == 'root'
                and r.get('rb_local_deleted') == 0]
    if (any(type(r.get('Seq')) is not int for r in siblings)
            or sorted(r['Seq'] for r in siblings) != list(range(1, len(siblings)+1))):
        raise ValueError('Root playlist order is ambiguous')
    current = _counter(counter)
    row = dict(ID=identity, Seq=len(siblings)+1, Name=_name(name), ImagePath=None,
        Attribute=0, ParentID='root', SmartList=None, UUID=uuid, rb_data_status=0,
        rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0, usn=None,
        rb_local_usn=current['int_1'], created_at=timestamp, updated_at=timestamp)
    return dict(revision=_digest([playlists, counter]),
        changes=[dict(table='djmdPlaylist', id=identity, before=None, after=row),
                 dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter), after=current)],
        playlist=row, counter=current, source_write_authority=False)


def rename(playlist, counter, *, name, timestamp):
    """Rename an ordinary playlist without replacing identity or creation time."""
    _unsynced(playlist)
    if playlist.get('Attribute') != 0 or playlist.get('SmartList') not in ('', None):
        raise ValueError('Only ordinary playlist renaming has a reference proof')
    if not playlist.get('ID') or not playlist.get('UUID'):
        raise ValueError('Playlist identity is unavailable')
    name = _name(name)
    # No-op counter behavior needs its own native reference; reject rather than
    # guessing whether an unchanged edit advances counters or timestamps.
    if name == playlist.get('Name'):
        raise ValueError('An unchanged rename has no native reference proof')
    current = _counter(counter)
    row = deepcopy(playlist)
    row.update(Name=name, updated_at=timestamp, rb_local_usn=current['int_1'])
    return dict(revision=_digest([playlist, counter]),
        changes=[dict(table='djmdPlaylist', id=row['ID'], before=deepcopy(playlist), after=row),
                 dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter), after=current)],
        playlist=row, counter=current, source_write_authority=False)


def delete_last(playlists, members, counter, *, identity):
    """Observed last-root deletion with exactly one unsynced membership.

    Empty/multiple-member deletion, folders and sibling renumbering require
    separate references. The two counter increments are specific to this case;
    no ordering of the now-absent rows' intermediate counters is inferred.
    """
    targets = [r for r in playlists if r.get('ID') == identity]
    if len(targets) != 1:
        raise ValueError('Expected one exact playlist identity')
    target = targets[0]
    _unsynced(target)
    if (target.get('Attribute') != 0 or target.get('SmartList') not in ('', None)
            or target.get('ParentID') != 'root'):
        raise ValueError('Only ordinary root deletion has a reference')
    active = [r for r in playlists if r.get('ParentID') == 'root' and r.get('rb_local_deleted') == 0]
    if (any(type(r.get('Seq')) is not int for r in active)
            or sorted(r['Seq'] for r in active) != list(range(1, len(active)+1))
            or target['Seq'] != len(active)):
        raise ValueError('Sibling renumbering has no deletion reference')
    if (len(members) != 1 or members[0].get('PlaylistID') != identity
            or members[0].get('TrackNo') != 1 or not members[0].get('ID')):
        raise ValueError('Deletion requires the observed single-member baseline')
    _unsynced(members[0])
    current = _counter(_counter(counter))
    return dict(revision=_digest([playlists, members, counter]),
        changes=[dict(table='djmdSongPlaylist', id=members[0]['ID'], before=deepcopy(members[0]), after=None),
                 dict(table='djmdPlaylist', id=identity, before=deepcopy(target), after=None),
                 dict(table='agentRegistry', id='localUpdateCount', before=deepcopy(counter), after=current)],
        counter=current, source_write_authority=False)
