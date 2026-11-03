"""Durable direct-import intentions; native authority stays in each store."""
from copy import deepcopy
from uuid import uuid4

from .analysis import options
from .native_identity import Binding, _canonical
from .native_registration_plan import _native_id, _uuid, validate_request
from .rekordbox_write import WriteProofError

KEY = 'shared_import_jobs'
BINDINGS = 'native_track_bindings'


def import_preferences(value):
    return {**options(value),'automatic':bool(isinstance(value,dict) and value.get('automatic') is True)}


def validate(jobs):
    if not isinstance(jobs,list) or len(jobs)>256:
        raise WriteProofError('Saved import queue is invalid; evidence preserved')
    identities=set()
    for job in jobs:
        if not isinstance(job,dict) or set(job)-{'media_refreshes','media_ready'}!={'id','source','library_id','tracks','destination',
                'preferences','request','bindings','membership','error','retry'}:
            raise WriteProofError('Unknown shared import intention')
        if type(job.get('media_ready',False)) is not bool:
            raise WriteProofError('Invalid import media preparation progress')
        if type(job.get('media_refreshes',0)) is not int or job.get('media_refreshes',0) not in (0,1):
            raise WriteProofError('Invalid captured-media refresh budget')
        _uuid(job['id']);_canonical(job['source']);_native_id(job['library_id'])
        if job['id'] in identities:raise WriteProofError('Duplicate import operation')
        identities.add(job['id'])
        if not isinstance(job['tracks'],list) or not 1<=len(job['tracks'])<=10000:
            raise WriteProofError('Invalid import tracks')
        ids=set();paths=set()
        for track in job['tracks']:
            if (not isinstance(track,dict) or set(track)!={'id','path'} or not isinstance(track['id'],str)
                    or not track['id'] or '\0' in track['id'] or track['id'] in ids or track['path'] in paths):
                raise WriteProofError('Ambiguous shared import tracks')
            _canonical(track['path']);ids.add(track['id']);paths.add(track['path'])
        dest=job['destination']
        if not isinstance(dest,dict) or dest.get('kind') not in ('collection','native','custom'):
            raise WriteProofError('Unknown import destination')
        if dest['kind']=='collection':
            if set(dest)!={'kind'}:raise WriteProofError('Unknown Collection destination')
        elif dest['kind']=='custom':
            if set(dest)!={'kind','name'} or not isinstance(dest['name'],str) or not dest['name']:
                raise WriteProofError('Unknown SoundBox playlist destination')
        else:
            if set(dest)!={'kind','id','uuid'}:raise WriteProofError('Unknown native destination')
            _native_id(dest['id']);_uuid(dest['uuid'])
        if not isinstance(job['preferences'],dict) or import_preferences(job['preferences'])!=job['preferences']:
            raise WriteProofError('Invalid captured import preferences')
        if not isinstance(job['error'],str) or '\0' in job['error']:
            raise WriteProofError('Invalid import failure')
        if type(job['retry']) is not bool:raise WriteProofError('Invalid import retry')
        request=job['request']
        if request is not None:
            validate_request(request)
            if (request['source']!=job['source']
                    or request['library_id']!=job['library_id']
                    or [(c['local_id'],c['audio']['path']) for c in request['tracks']]!=[(t['id'],t['path']) for t in job['tracks']]):
                raise WriteProofError('Prepared import changed its admitted identity')
        if not isinstance(job['bindings'],list):raise WriteProofError('Unknown import receipts')
        for value in job['bindings']:
            b=Binding(**value);b.validate()
            if (b.source,b.library_id)!=(job['source'],job['library_id']) or (b.local_id,b.canonical) not in {
                    (t['id'],t['path']) for t in job['tracks']}:
                raise WriteProofError('Registration receipt belongs to another import')
        if job['bindings'] and (request is None or len(job['bindings'])!=len(ids)
                or {b['local_id'] for b in job['bindings']}!=ids):
            raise WriteProofError('Incomplete registration receipts')
        if job['membership'] is not None:
            from .rekordbox_playlist_store import validate_request as validate_membership
            validate_membership(job['membership'])
            m=job['membership']
            if (dest['kind']!='native' or not job['bindings'] or m['operation']!='append'
                    or (m['playlist_id'],m['playlist_uuid'])!=(dest['id'],dest['uuid'])
                    or [(t['track_id'],t['uuid']) for t in m['tracks']]!=[
                        (b['native_id'],b['native_uuid']) for b in job['bindings']]):
                raise WriteProofError('Membership changed its admitted destination/identities')
    return jobs


def admit(jobs, library, tracks, destination, preferences):
    validate(jobs)
    job=dict(id=str(uuid4()),source=str(library.source),library_id=library.source_library_id,
        tracks=[dict(id=t.track_id,path=str(t.path)) for t in tracks],destination=deepcopy(destination),
        preferences=import_preferences(preferences),request=None,bindings=[],membership=None,error='',retry=False,
        media_refreshes=0,media_ready=False)
    result=[*deepcopy(jobs),job];validate(result)
    return result


def remember(existing, bindings):
    """No automatic curation merge or rebinding of another logical/native ID."""
    if not isinstance(existing,list):raise WriteProofError('Saved native bindings are invalid')
    result=deepcopy(existing)
    for value in bindings:
        binding=Binding(**value);binding.validate()
        for prior in result:
            other=Binding(**prior);other.validate()
            if (other.source,other.library_id)!=(binding.source,binding.library_id):continue
            if other.local_id==binding.local_id or other.native_id==binding.native_id:
                if other!=binding:raise WriteProofError('Native binding conflicts with saved curation; preserve both identities')
                break
        else:result.append(deepcopy(value))
    return result


def guard_curation(bindings, *, keyed=(), memberships=()):
    """Existing independent native-ID curation needs a reviewed merge."""
    for binding in bindings:
        native,local=binding['native_id'],binding['local_id']
        if native!=local and (any(native in values for values in keyed)
                or any(native in values for values in memberships)):
            raise WriteProofError('Existing native identity has SoundBox curation; preserve both identities for review')


def project(library, values, *, unavailable=None):
    from .native_identity import project as project_library
    if library.source_kind!='rekordbox-db':return library
    bindings=[]
    if not isinstance(values,list):raise WriteProofError('Saved native bindings are invalid')
    for value in values:
        b=Binding(**value);b.validate()
        if (b.source,b.library_id)==(str(library.source),library.source_library_id):bindings.append(b)
    return project_library(library,bindings,unavailable=unavailable)


def present(library, pending):
    """Logical rows only; catalog/revision/native memberships stay exact."""
    from dataclasses import replace
    validate(pending)
    if not pending:return library
    playlists=list(library.playlists)
    for job in pending:
        dest=job['destination']
        if (job['error'] or dest['kind']!='native'
                or (job['source'],job['library_id'])!=(str(library.source),library.source_library_id)):
            continue
        if not any(n['ID']==dest['id'] and n['UUID']==dest['uuid'] and n['rb_local_deleted']==0
                   for n in library.source_playlist_catalog.get('nodes',[])):continue
        for index,playlist in enumerate(playlists):
            if playlist.pid!=dest['id']:continue
            keys=list(playlist.keys)
            for track in job['tracks']:
                if track['id'] in library.tracks_by_id and track['id'] not in keys:keys.append(track['id'])
            playlists[index]=replace(playlist,keys=keys)
    return replace(library,playlists=playlists)
