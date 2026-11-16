"""Pure reconstruction of the observed ASCII native title/artist protocol.

No connection, source writer, audio tagger or authority loader.
The bounded profiles cover analysed ASCII/empty-album records. The original
profile stays local-only; the separately admitted active profile also preserves
synced rows with no remote USN. Reference counts include every artist role.
Adapters still need schema binding, concurrency, recovery and native acceptance.
"""
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from soundbox.rekordbox_write import ConflictError, WriteProofError

LOCAL_PROFILE='ascii-local-analysed-empty-album-v1'
ACTIVE_PROFILE='ascii-active-analysed-empty-album-v2'
PROFILES=frozenset((LOCAL_PROFILE,ACTIVE_PROFILE))


def _text(value, *, empty=False):
    if (not isinstance(value, str) or not (empty or value)
            or len(value)>255 or any(ord(c)<32 or ord(c)>126 for c in value)
            or value!=value.strip()):
        raise WriteProofError('Unreferenced metadata text profile')


def _clock(value):
    try:
        parsed=datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f +00:00')
        if len(value)!=30 or parsed.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]+' +00:00'!=value:
            raise ValueError('Not the observed native millisecond clock')
    except (ValueError, TypeError) as exc:
        raise WriteProofError('Explicit native UTC millisecond clock required') from exc


def construct(target, counter, artists, references, *, field, value, timestamp, artist_seed=None,
              profile=LOCAL_PROFILE):
    """Construct a single observed save from exact captured rows/clock seeds.

    `references` is the full before-inventory count for each referenced artist,
    including ArtistID, OrgArtistID, RemixerID, ComposerID and AlbumArtistID.
    These supplied facts are not a source-save authorization or fresh proof.
    """
    if type(profile) is not str or profile not in PROFILES:
        raise WriteProofError('Unreferenced native metadata profile')
    if field not in ('title','artist'):
        raise WriteProofError('Only referenced title/artist saves are modeled')
    _text(value,empty=field=='artist');_clock(timestamp)
    if (any(type(target.get(key)) is not int for key in
            ('rb_local_deleted','rb_local_synced','rb_data_status','rb_local_data_status','Analysed'))
            or target.get('rb_local_deleted')!=0
            or target.get('rb_local_synced') not in ((0,1) if profile==ACTIVE_PROFILE else (0,))
            or target.get('rb_data_status')!=0 or target.get('rb_local_data_status')!=0
            or target.get('usn') is not None or target.get('Analysed')!=105
            or target.get('AlbumID') not in (None,'')
            or not isinstance(target.get('TrackInfoUpdated'),str)
            or not target['TrackInfoUpdated'].isdigit()
            or int(target['TrackInfoUpdated'])<2
            or counter.get('registry_id')!='localUpdateCount'
            or type(counter.get('int_1')) is not int or not 0<=counter['int_1']<2**63-5
            or type(target.get('rb_local_usn')) is not int
            or not 0<=target['rb_local_usn']<=counter['int_1']):
        raise WriteProofError('Unreferenced native metadata row state')
    actors={a['ID']:a for a in artists}
    if len(actors)!=len(artists) or len({a['UUID'] for a in artists})!=len(artists):
        raise ConflictError('Ambiguous artist identities')
    after=deepcopy(target);created=None;removed=None;next_usn=counter['int_1']
    if field=='title':
        if artist_seed is not None:raise WriteProofError('Title save has no artist seed')
        after['Title']=value
    else:
        if target.get('AlbumID') not in (None,''):
            raise WriteProofError('Nonempty album metadata needs its own reference')
        old_id=target.get('ArtistID')
        if old_id:
            if old_id not in actors or type(references.get(old_id)) is not int or references[old_id]<1:
                raise ConflictError('Missing full prior artist reference inventory')
        matches=[a for a in artists if a['Name']==value] if value else []
        if len(matches)>1 or any(a.get('rb_local_deleted')!=0 for a in matches):
            raise ConflictError('Ambiguous or historical artist name')
        if matches:
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
        if old_id and old_id!=new_id and references[old_id]==1:
            prior=actors[old_id]
            if (any(type(prior.get(k)) is not int or prior[k]!=0 for k in
                    ('rb_data_status','rb_local_data_status','rb_local_deleted','rb_local_synced'))
                    or prior.get('usn') is not None or type(prior.get('rb_local_usn')) is not int
                    or not 0<=prior['rb_local_usn']<=counter['int_1']):
                raise WriteProofError('Unreferenced historical/synced artist retirement is unproven')
            if created is not None:
                raise WriteProofError('Combined artist creation/retirement needs a separate reference')
            removed=deepcopy(prior);next_usn+=1
        after['ArtistID']=new_id
        after['AlbumID']=''
    if after==target:
        return dict(before=deepcopy(target),after=after,counter_before=deepcopy(counter),
                    counter_after=deepcopy(counter),artist_created=None,artist_removed=None)
    next_usn+=2
    after.update(TrackInfoUpdated=str(int(target['TrackInfoUpdated'])+1),
                 rb_local_usn=next_usn,updated_at=timestamp)
    after_counter=deepcopy(counter);after_counter['int_1']=next_usn
    return dict(before=deepcopy(target),after=after,counter_before=deepcopy(counter),
                counter_after=after_counter,artist_created=created,artist_removed=removed)
