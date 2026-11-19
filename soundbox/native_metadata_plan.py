"""Pure reconstruction of independently bounded native metadata protocols.

No connection, source writer, audio tagger or authority loader.
The first two profiles cover analysed ASCII/empty-album records. V3 additionally
preserves Unicode text, existing albums on title-only edits and unchanged synced
artist references. Bare/legacy NULL revisions start at zero, following the
native integer conversion. V4 separately models local/synced old-artist cleanup
and combined creation, with an explicit retirement clock and native int counter.
V5 separately covers the observed album bookkeeping, active-name lookup across
deleted history and unchanged remote synchronization metadata.
Reference counts include every artist role.
Adapters still need schema binding, concurrency, recovery and native acceptance.
"""
from copy import deepcopy
from datetime import datetime
from uuid import UUID
import unicodedata

from soundbox.rekordbox_write import ConflictError, WriteProofError

LOCAL_PROFILE='ascii-local-analysed-empty-album-v1'
ACTIVE_PROFILE='ascii-active-analysed-empty-album-v2'
UNICODE_PROFILE='unicode-active-metadata-v3'
RETIREMENT_PROFILE='unicode-active-artist-retirement-v4'
ALBUM_PROFILE='unicode-album-cloud-metadata-v5'
PROFILES=frozenset((LOCAL_PROFILE,ACTIVE_PROFILE,UNICODE_PROFILE,RETIREMENT_PROFILE,ALBUM_PROFILE))


def _text(value, *, empty=False, unicode=False):
    if (not isinstance(value, str) or not (empty or value)
            or len(value)>255 or any(unicodedata.category(c).startswith('C') if unicode
                                    else ord(c)<32 or ord(c)>126 for c in value)
            or value!=value.strip()):
        raise WriteProofError('Unreferenced metadata text profile')


def _clock(value):
    try:
        parsed=datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
        if len(value)!=30 or parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'!=value:
            raise ValueError('Not the observed native millisecond clock')
    except (ValueError, TypeError) as exc:
        raise WriteProofError('Explicit native UTC millisecond clock required') from exc


def _revision(value, *, expanded):
    if not expanded:
        # Existing journals reconstruct under their original profile, including
        # its numeric-string representation. V3's stricter bound cannot change it.
        if not isinstance(value,str) or not value.isdigit() or int(value)<2:
            raise WriteProofError('Unreferenced native metadata row state')
        return int(value)
    if value is None:return 0
    if (type(value) is not str or not 1<=len(value)<=10 or not value.isascii()
            or not value.isdecimal() or str(int(value))!=value
            or not 0<=int(value)<2**31-1):
        raise WriteProofError('Unreferenced native metadata row state')
    return int(value)


def _sync_state(row, counter, *, remote=False, nullable_clock=False):
    """Observed active state, preserving remote and local clocks independently."""
    if type(counter) is not int or counter<0:return False
    keys=('rb_local_deleted','rb_local_synced','rb_data_status','rb_local_data_status')
    if any(type(row.get(key)) is not int for key in keys) or row['rb_local_deleted']!=0:
        return False
    local=(row['rb_data_status']==0 and row['rb_local_data_status']==0
           and row['rb_local_synced'] in (0,1) and row.get('usn') is None)
    cloud=(remote and row['rb_data_status']==257 and row['rb_local_data_status'] in (0,2)
           and row['rb_local_synced']==1 and type(row.get('usn')) is int and 0<row['usn']<2**63)
    clock=row.get('rb_local_usn')
    return bool((local or cloud) and (nullable_clock and clock is None
                or type(clock) is int and 0<=clock<=counter))


def construct(target, counter, artists, references, *, field, value, timestamp, artist_seed=None,
              profile=LOCAL_PROFILE,retirement_timestamp=None,album=None,album_timestamp=None):
    """Construct a single observed save from exact captured rows/clock seeds.

    `references` is the full before-inventory count for each referenced artist,
    including ArtistID, OrgArtistID, RemixerID, ComposerID and AlbumArtistID.
    These supplied facts are not a source-save authorization or fresh proof.
    """
    if type(profile) is not str or profile not in PROFILES:
        raise WriteProofError('Unreferenced native metadata profile')
    if field not in ('title','artist'):
        raise WriteProofError('Only referenced title/artist saves are modeled')
    album_profile=profile==ALBUM_PROFILE
    retirement=profile in (RETIREMENT_PROFILE,ALBUM_PROFILE)
    expanded=profile in (UNICODE_PROFILE,RETIREMENT_PROFILE,ALBUM_PROFILE)
    _text(value,empty=field=='artist',unicode=expanded);_clock(timestamp)
    if retirement_timestamp is not None:
        if not retirement or field!='artist':raise WriteProofError('Unreferenced artist retirement clock')
        _clock(retirement_timestamp)
        if retirement_timestamp>timestamp:raise WriteProofError('Artist retirement clock follows metadata clock')
    revision=_revision(target.get('TrackInfoUpdated'),expanded=expanded)
    album_id=target.get('AlbumID')
    album_allowed=album_id in (None,'') or (expanded and (field=='title' or album_profile)
        and type(album_id) is str and len(album_id)<=10 and album_id.isascii()
        and album_id.isdecimal() and str(int(album_id))==album_id and 0<int(album_id)<2**32)
    remote_target=(album_profile and _sync_state(target,counter.get('int_1',-1),remote=True)
                   and target.get('rb_data_status')==257 and target.get('Analysed')==105
                   and album_id in (None,''))
    if (any(type(target.get(key)) is not int for key in
            ('rb_local_deleted','rb_local_synced','rb_data_status','rb_local_data_status','Analysed'))
            or target.get('rb_local_deleted')!=0
            or target.get('rb_local_synced') not in ((0,1) if profile!=LOCAL_PROFILE else (0,))
            or (not remote_target and (target.get('rb_data_status')!=0
                or target.get('rb_local_data_status')!=0 or target.get('usn') is not None))
            or target.get('Analysed') not in ((0,105) if expanded else (105,))
            or not album_allowed
            or counter.get('registry_id')!='localUpdateCount'
            or type(counter.get('int_1')) is not int
            or not 0<=counter['int_1']<(2**31-6 if album_profile else 2**31-5 if retirement else 2**63-5)
            or type(target.get('rb_local_usn')) is not int
            or not 0<=target['rb_local_usn']<=counter['int_1']):
        raise WriteProofError('Unreferenced native metadata row state')
    actors={a['ID']:a for a in artists}
    if len(actors)!=len(artists) or len({a['UUID'] for a in artists})!=len(artists):
        raise ConflictError('Ambiguous artist identities')
    album_after=None
    if album is not None or album_timestamp is not None:
        if not album_profile or field!='artist' or album_id in (None,'') or not isinstance(album,dict):
            raise WriteProofError('Album context exceeds this metadata save')
        _clock(album_timestamp)
        if (album.get('ID')!=album_id or not _sync_state(album,counter['int_1'])
                or album.get('Compilation') is not None and
                    (type(album['Compilation']) is not int or album['Compilation'] not in (0,1))
                or album_timestamp>timestamp
                or retirement_timestamp is not None and album_timestamp>retirement_timestamp
                or artist_seed is not None and artist_seed['timestamp']>album_timestamp):
            raise WriteProofError('Unreferenced album state or clock sequence')
        role=album.get('AlbumArtistID')
        if role and (role not in actors or not _sync_state(actors[role],counter['int_1'],nullable_clock=True)
                     or type(references.get(role)) is not int or references[role]<1):
            raise ConflictError('Missing active album artist reference')
        album_after=deepcopy(album)
    elif album_profile and field=='artist' and album_id not in (None,''):
        raise WriteProofError('Album artist save requires its captured album context')
    after=deepcopy(target);created=None;removed=None;retired_after=None;next_usn=counter['int_1']
    if field=='title':
        if artist_seed is not None:raise WriteProofError('Title save has no artist seed')
        after['Title']=value
    else:
        if target.get('AlbumID') not in (None,'') and album_after is None:
            raise WriteProofError('Nonempty album metadata needs its own reference')
        old_id=target.get('ArtistID')
        if old_id:
            if old_id not in actors or type(references.get(old_id)) is not int or references[old_id]<1:
                raise ConflictError('Missing full prior artist reference inventory')
        matches=[a for a in artists if a['Name']==value and
                 (not album_profile or type(a.get('rb_local_deleted')) is int and a['rb_local_deleted']==0)] if value else []
        if len(matches)>1 or any(a.get('rb_local_deleted')!=0 for a in matches):
            raise ConflictError('Ambiguous or historical artist name')
        if matches:
            if album_profile and not _sync_state(matches[0],counter['int_1'],remote=True,nullable_clock=True):
                raise WriteProofError('Reusing this artist synchronization profile is not proven')
            if artist_seed is not None:raise WriteProofError('Existing artist cannot consume a new identity')
            new_id=matches[0]['ID']
        elif value:
            if not isinstance(artist_seed,dict) or set(artist_seed)!={'ID','UUID','timestamp'}:
                raise WriteProofError('New artist needs explicit identity/clock seeds')
            identity=artist_seed['ID'];uuid=artist_seed['UUID'];_clock(artist_seed['timestamp'])
            try:valid_uuid=str(UUID(uuid))==uuid
            except (ValueError, TypeError, AttributeError):valid_uuid=False
            if (not isinstance(identity,str) or not identity.isascii() or not identity.isdigit()
                    or not 0<int(identity)<2**32 or not valid_uuid
                    or identity in actors or any(a['UUID']==uuid for a in artists)):
                raise ConflictError('New artist identity collides or is invalid')
            new_id=identity;next_usn+=1
            created=dict(ID=identity,Name=value,SearchStr=None,UUID=uuid,rb_data_status=0,
                rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=None,
                rb_local_usn=next_usn,created_at=artist_seed['timestamp'],updated_at=artist_seed['timestamp'])
        else:
            if artist_seed is not None:raise WriteProofError('Clearing has no artist seed')
            new_id=''
        if album_after is not None:
            # Native order: creation, Content link, Album bookkeeping, cleanup,
            # then TrackInfoUpdated. Album names and surviving role IDs stay exact.
            album_after.update(Compilation=album['Compilation'] if album['Compilation'] is not None else 0,
                AlbumArtistID=album['AlbumArtistID'] or '',rb_local_usn=next_usn+2,updated_at=album_timestamp)
            next_usn+=1
        if old_id and old_id!=new_id and references[old_id]==1:
            prior=actors[old_id]
            if (any(type(prior.get(k)) is not int or prior[k]!=0 for k in
                    ('rb_data_status','rb_local_data_status','rb_local_deleted'))
                    or type(prior.get('rb_local_synced')) is not int
                    or prior['rb_local_synced'] not in ((0,1) if retirement else (0,))
                    or prior.get('usn') is not None
                    or not (retirement and prior.get('rb_local_usn') is None
                            or type(prior.get('rb_local_usn')) is int
                            and 0<=prior['rb_local_usn']<=counter['int_1'])):
                raise WriteProofError('Unreferenced historical/synced artist retirement is unproven')
            if created is not None and not retirement:
                raise WriteProofError('Combined artist creation/retirement needs a separate reference')
            removed=deepcopy(prior);next_usn+=1
            if retirement and prior['rb_local_synced']==1:
                if retirement_timestamp is None:raise WriteProofError('Synced artist retirement requires its captured clock')
                if artist_seed and artist_seed['timestamp']>retirement_timestamp:
                    raise WriteProofError('Artist retirement precedes artist creation')
                # Native order: optional creation, Content field, cleanup,
                # then TrackInfoUpdated. The final Content clock is last.
                retired_after={**deepcopy(prior),'rb_data_status':2,'rb_local_deleted':1,
                               'rb_local_usn':next_usn+1,'updated_at':retirement_timestamp}
        after['ArtistID']=new_id
        after['AlbumID']=album_id if album_after is not None else ''
    if retirement_timestamp is not None and retired_after is None:
        raise WriteProofError('Artist retirement clock has no synced retirement')
    if after==target:
        return dict(before=deepcopy(target),after=after,counter_before=deepcopy(counter),
                    counter_after=deepcopy(counter),artist_created=None,artist_removed=None)
    next_usn+=2
    after.update(TrackInfoUpdated=str(revision+1),
                 rb_local_usn=next_usn,updated_at=timestamp)
    after_counter=deepcopy(counter);after_counter['int_1']=next_usn
    result=dict(before=deepcopy(target),after=after,counter_before=deepcopy(counter),
                counter_after=after_counter,artist_created=created,artist_removed=removed)
    if retired_after is not None:result['artist_retirement_after']=retired_after
    if album_after is not None:result.update(album_before=deepcopy(album),album_after=album_after)
    return result
