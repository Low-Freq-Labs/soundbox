"""Prepare only requested/changed inputs; merge catalogs from committed rows.

Retained inventory is the last successful export, not a fresh integrity proof.
No retained USB audio, analysis or artwork is opened during normal planning.
"""
from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
import unicodedata
import uuid

from . import cdj_source, manifest
from .cdj_transaction import CdjPlan, _hash_file, load_manifest, make_plan
from .cdj_usb import CATALOG_RENDER_POLICY, DATABASE_FILES, digest, read_library, run_renderer
from .library import Track
from .source_acquisition import SourceContract, acquire_source
from .operation_control import OperationCancelled

LOG = logging.getLogger('soundbox')


def source_epoch(path):
    value = Path(path).stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def input_signature(dto, extra, artwork):
    analysis = Path(dto['analysis_source'])
    value = dict(track=dto, extra=extra, audio=source_epoch(dto['source_path']),
                 analysis=[source_epoch(analysis.with_suffix('.'+ext)) for ext in ('DAT','EXT','2EX')],
                 artwork=artwork)
    return digest(json.dumps(value,sort_keys=True,separators=(',',':')).encode())


def native_matches(row,dto,extra,image,art_cache,track_id=None):
    """Reuse a native export's inventory by identity/metadata, without auditing media."""
    if row.get('source_id')!=dto['id'] or row.get('file_size')!=dto['file_size'] or not dto['file_size'] or dto['file_size']!=source_epoch(dto['source_path'])[2]:
        return False
    for key in ('cue_update_count','analysis_data_update_count','information_update_count'):
        if row.get(key)!=extra[key]:return False
    for native,source in (('title','title'),('artist','artist'),('bpm','bpm_x100'),('key','key'),
            ('duration','duration_sec'),('genre','genre'),('bitrate','bitrate')):
        if row.get(native)!=dto[source]:return False
    info=row.get('info',{})
    for native,source in (('album','album'),('year','year'),('releaseDate','release_date'),('comments','comment'),
            ('rating','rating'),('label','label'),('color','color_id')):
        if str(info.get(native,''))!=str(dto[source]):return False
    if str(info.get('playCount',''))!=str(dto['metadata']['play_count']):return False
    if info.get('hotCueAutoLoad')!=('On' if dto['metadata']['hot_cue_auto_load'] else 'Off'):return False
    if art_cache is not None:
        entry=dict(getattr(art_cache,'index',{}).get(track_id if track_id is not None else str(dto['id']),{}))
        if entry.get('origin')=='manual' or (entry.get('status')=='blocked' and row.get('artwork')):return False
        if image is not None and not row.get('artwork'):return False
        if not hasattr(art_cache,'index') and image is not None:return False
    return True


def artwork_paths(image_id):
    return [f'PIONEER/Artwork/{image_id//20+1:05}/{prefix}{image_id}{suffix}.jpg'
            for prefix,suffix in (('a',''),('a','_m'),('b',''),('b','_m'))]


def artwork_id(path):
    match = re.fullmatch(r'a([0-9]+)',PurePosixPath(path).stem)
    if not match:
        raise ValueError('Unsupported native artwork reference')
    return int(match[1])


def referenced_files(view):
    result = set()
    for row in view['track_rows']:
        result.add(row['audio'].lstrip('/'))
        if row['analysis']:
            base = PurePosixPath(row['analysis'].lstrip('/'))
            result.update(str(base.with_suffix('.'+ext)) for ext in ('DAT','EXT','2EX'))
        if row['artwork']:
            result.update(artwork_paths(artwork_id(row['artwork'])))
    return result


def _catalog_unchanged(catalog, view):
    """Exact pure catalog equality, including native order and duplicates."""
    by_audio = {row['audio']:int(row['id']) for row in view['track_rows']}
    if len(by_audio) != len(view['track_rows']) or len(set(by_audio.values())) != len(by_audio):
        return False
    original = [dict(id=int(row['id']),parent=int(row['parent']),name=row['title'],
                     folder=row['folder'],tracks=[by_audio[path] for _title,path in row['entries']])
                for row in view['playlists']]
    return catalog['nodes'] == original and catalog['track_ids'] == set(by_audio.values())


def _catalog_bytes_unchanged(native, committed):
    """Bind every captured database/profile byte to the committed generation."""
    if (not committed or committed.get('render_policy') != CATALOG_RENDER_POLICY
            or not set(DATABASE_FILES) <= dict(native.files).keys()):
        return False
    return all(committed['files'].get(path,{}).get('sha256') == digest(raw)
               and committed['files'][path]['size'] == len(raw) for path,raw in native.files)


def _catalog(library, playlists, previous, all_playlists, additive, standalone=()):
    from .cdj_export import _selected, _stable_id
    old = previous['view']
    foreign_playlists = set(previous.get('foreign_playlists', []))
    keep={int(p['id']) for p in old['playlists'] if additive or int(p['id']) in foreign_playlists}
    for _ in range(64):
        parents={int(p['parent']) for p in old['playlists'] if int(p['id']) in keep and p['parent']!='0'}
        if parents<=keep:break
        keep.update(parents)
    retained_nodes=[p for p in old['playlists'] if int(p['id']) in keep]
    nodes = {int(p['id']):dict(id=int(p['id']),parent=int(p['parent']),name=p['title'],
                    folder=p['folder'],tracks=[]) for p in retained_nodes}
    by_audio = {t['audio']:int(t['id']) for t in old['track_rows']}
    for p in retained_nodes:
        nodes[int(p['id'])]['tracks'] = [by_audio[path] for _title,path in p['entries']]
    bindings = {int(p['source_id']):int(p['id']) for p in old['playlists'] if 'source_id' in p}
    next_playlist = int(previous['next_playlist_id'])
    jobs = {manifest.playlist_key(p):p for p in playlists} if additive else _selected(
        library,playlists,[*library.playlists,*all_playlists],previous)
    selection = {p['key']:deepcopy(p) for p in previous['selection']} if additive else {}
    by_source = {int(t['source_id']):int(t['id']) for t in old['track_rows'] if int(t['master_db_id'])==int(library.source_library_id)}
    classic={int(t['id']):t for t in old.get('classic_track_rows',[])}
    def track_identity(t):return int(t['master_db_id']),int(t['source_id']),t['audio']
    for row in old['track_rows']:
        peer=classic.get(int(row['id']))
        if int(row['master_db_id'])==int(library.source_library_id) and peer is not None and track_identity(peer)!=track_identity(row):
            # A numeric ID can already name a different recording in Classic.
            # Allocate a fresh selected ID rather than overwrite that recording.
            by_source.pop(int(row['source_id']),None)
    candidates_by_name: dict[tuple, set[int]] = {}
    for identity, existing in nodes.items():
        if identity in foreign_playlists:
            candidates_by_name.setdefault((existing['name'], existing['parent'], existing['folder']), set()).add(identity)
    next_track = int(previous['next_track_id'])
    tracks: dict[int, Track] = {}
    used_sources = set()
    # Existing source->device bindings are exact manifest identities, not titles.
    touched_nodes=set()
    def node(source_id, name, parent, folder):
        nonlocal next_playlist
        if source_id not in bindings:
            candidates=list(candidates_by_name.get((name,parent,folder),()))
            if len(candidates)==1:bindings[source_id]=candidates[0]
            else:bindings[source_id] = next_playlist;next_playlist += 1
        identity = bindings[source_id]
        touched_nodes.add(identity)
        existing = nodes.get(identity)
        if existing and existing['folder'] != folder:
            raise ValueError('CDJ playlist identity collision')
        if identity in foreign_playlists:
            if existing:
                candidates_by_name[(existing['name'],existing['parent'],existing['folder'])].discard(identity)
            candidates_by_name.setdefault((name,parent,folder),set()).add(identity)
        nodes[identity] = dict(id=identity,parent=parent,name=name,folder=folder,
                              tracks=existing['tracks'] if existing and folder else [])
        return identity
    claimed = {}
    for old in previous['selection']:
        claimed[_stable_id('playlist:'+old['key'])] = old['key']
        for at in range(len(old['folder'])):
            key = 'folder:'+'/'.join(old['folder'][:at+1])
            claimed[_stable_id(key)] = key
    for key,playlist in jobs.items():
        parent = 0
        for at,name in enumerate(playlist.folder_path):
            folder_key = 'folder:'+'/'.join(playlist.folder_path[:at+1])
            source_id = _stable_id(folder_key)
            if source_id in claimed and claimed[source_id]!=folder_key:
                raise ValueError('CDJ playlist identity collision')
            claimed[source_id] = folder_key
            parent = node(source_id,name,parent,True)
        source_id = _stable_id('playlist:'+key)
        if source_id in claimed and claimed[source_id]!=key:
            raise ValueError('CDJ playlist identity collision')
        claimed[source_id] = key
        identity = node(source_id,playlist.name,parent,False)
        members = []
        for track in library.resolve(playlist):
            tid = int(track.native_id)
            if tid in tracks and tracks[tid].track_id != track.track_id:
                raise ValueError('Selected logical tracks share an ambiguous native identity')
            tracks[tid] = track;used_sources.add(tid)
            if tid not in by_source:
                by_source[tid] = next_track;next_track += 1
            members.append(by_source[tid])
        nodes[identity]['tracks'] = members
        selection[key] = dict(key=key,pid=playlist.pid,name=playlist.name,folder=playlist.folder_path)
    for track in standalone:
        tid=int(track.native_id)
        if tid in tracks and tracks[tid].track_id != track.track_id:
            raise ValueError('Selected logical tracks share an ambiguous native identity')
        tracks[tid]=track;used_sources.add(tid)
        if tid not in by_source:
            by_source[tid]=next_track;next_track+=1
    # The native merge prunes only removed selected members with no remaining
    # playlist references. Unrelated collection-only records remain intact.
    final_tracks = set(by_audio.values())
    final_tracks.update(by_source[tid] for tid in used_sources)
    final_tracks.update(previous.get('foreign_tracks',[]))
    final_tracks.update(tid for node in nodes.values() for tid in node['tracks'])
    reverse = {identity:source for source,identity in bindings.items()}
    return dict(nodes=list(nodes.values()),tracks=list(tracks.values()),selection=list(selection.values()),
        by_source=by_source,reverse=reverse,track_ids=final_tracks,next_track=next_track,
        next_playlist=next_playlist,jobs=list(jobs.values()),db_id=int(library.source_library_id),
        touched_nodes=touched_nodes)


def _image_mapping(previous, native, partial, folder):
    by_digest = {}
    existing_ids = {artwork_id(s.artwork) for s in native.songs if s.artwork}
    existing_ids.update(artwork_id(t['artwork']) for t in native.catalog.get('classic_track_rows',[]) if t.get('artwork'))
    for identity in existing_ids:
        paths = artwork_paths(identity)
        if all(previous['files'].get(p,{}).get('owned') for p in paths):
            by_digest[tuple(previous['files'][p]['sha256'] for p in paths)] = identity
    next_id = max(existing_ids|set(native.catalog.get('reserved_image_ids',[])),default=0)+1
    mapping = {}
    transfers: list[tuple[Path,str]] = []
    for identity in sorted({artwork_id(s.artwork) for s in partial.songs if s.artwork}):
        paths = artwork_paths(identity)
        hashes = tuple(_hash_file(folder/p) for p in paths)
        target = by_digest.get(hashes)
        if target is None:
            target = next_id;next_id += 1;by_digest[hashes] = target
            transfers.extend((folder/p,new) for p,new in zip(paths,artwork_paths(target)))
        mapping[str(identity)] = dict(id=target,path='/'+artwork_paths(target)[0])
    return mapping,transfers


def _placements(changed, catalog, previous, reuse=None):
    # Match the pinned renderer's FAT/NFC naming and 48-character directories.
    def safe(value):
        value = ''.join('_' if c in '/\\:*?"<>|' or ord(c)<32 else c
                        for c in unicodedata.normalize('NFC',value)).rstrip('. ')
        return value or 'Unknown'
    def name(value):
        value = safe(value)
        if len(value.encode())<=120:
            return value
        suffix = PurePosixPath(value).suffix
        if not suffix or len(suffix.encode())>=120:
            suffix = ''
        stem = value[:-len(suffix)] if suffix else value
        encoded = stem.encode()[:120-len(suffix.encode())]
        return (encoded.decode(errors='ignore').rstrip('. ') or 'Unknown')+suffix
    def key(value):
        return unicodedata.normalize('NFD',value).casefold()
    def analysis(audio):
        encoded = audio.encode('utf-16-le');value = 0
        for at in range(0,len(encoded),2):
            value = (value*0x34f5501d+int.from_bytes(encoded[at:at+2],'little')*0x93b6)&0xffffffff
        value %= 0x30d43
        shard = sum(((value>>bit)&1)<<i for i,bit in enumerate((0,2,6,7,9,13,16)))
        return f'/PIONEER/USBANLZ/P{shard:03X}/{value:08X}'
    inventory_rows=[*previous['view'].get('classic_track_rows',[]),*previous['view']['track_rows']]
    audio_used:dict[str,tuple[int,int]|None] = {key(t['audio']):(int(t['master_db_id']),int(t['source_id'])) for t in inventory_rows}
    analysis_used:dict[str,tuple[int,int]|None] = {key(str(PurePosixPath(t['analysis']).parent)):(int(t['master_db_id']),int(t['source_id'])) for t in inventory_rows if t['analysis']}
    for row in inventory_rows:
        if not previous['files'].get(row['audio'].lstrip('/'),{}).get('owned'):
            audio_used[key(row['audio'])] = None
        if row['analysis']:
            base = PurePosixPath(row['analysis'].lstrip('/'))
            if any(not previous['files'].get(str(base.with_suffix('.'+ext)),{}).get('owned') for ext in ('DAT','EXT','2EX')):
                analysis_used[key(str(base.parent))] = None
    result = {}
    for dto in changed:
        tid = int(dto['id']);device_id = catalog['by_source'][tid]
        owner=(catalog['db_id'],tid)
        if reuse and tid in reuse:
            audio,anlz=reuse[tid]
            if analysis(audio)==anlz:
                result[tid]=(audio,anlz)
                continue
            reuse.pop(tid)
        artist = safe(dto['artist'] or 'UnknownArtist')[:48].rstrip('. ') or 'Unknown'
        album = safe(dto['album'] or 'UnknownAlbum')[:48].rstrip('. ') or 'Unknown'
        leaf = name(Path(dto['source_path']).name)
        stem,suffix = PurePosixPath(leaf).stem,PurePosixPath(leaf).suffix
        for attempt in range(4096):
            filename = leaf if not attempt else name(f'sb{device_id}-{attempt}-'+stem+suffix)
            audio = f'/Contents/{artist}/{album}/{filename}'
            anlz = analysis(audio)
            if audio_used.get(key(audio),owner)==owner and analysis_used.get(key(anlz),owner)==owner:
                audio_used[key(audio)] = owner;analysis_used[key(anlz)] = owner
                result[tid] = (audio,anlz)
                break
        else:
            raise ValueError('CDJ destination collision space exhausted')
    return result


def plan_incremental(library, playlists, session, previous, *, all_playlists=(), titles=None,
                     artists=None, art_cache=None, should_cancel=lambda:False,
                     on_progress=lambda *_:None, recovery_root=None, additive=False,native=None,
                     standalone=(),baseline_manifest=None):
    started = time.monotonic()
    catalog = _catalog(library,playlists,previous,all_playlists,additive,standalone)
    tracks = catalog['tracks']
    if native is None:native = read_library(session.target,session.mount_identity[0],should_cancel=should_cancel)
    if native is None or any(digest(raw)!=previous['files'][path]['sha256']
            for path,raw in native.files if path in DATABASE_FILES):
        raise ValueError('USB library changed while preparing; retry after rekordbox finishes exporting')
    captured = cdj_source.capture_metadata(library,tracks,titles=titles,artists=artists,playlists=catalog['jobs'])
    extras = {int(e['source_id']):e for e in captured['track_extras']}
    cached = previous.get('track_inputs',{})
    images, signatures, changed = {}, {}, []
    native_rows={int(t['id']):t for t in previous['view']['track_rows']}
    adopted=set()
    for track,dto in zip(tracks,captured['tracks']):
        tid = int(dto['id'])
        images[tid] = art_cache.export_image_snapshot(track.track_id) if art_cache is not None else None
        signatures[tid] = input_signature(dto,extras[tid],digest(images[tid]) if images[tid] is not None else '')
        native_id=catalog['by_source'][tid]
        if native_id not in native_rows or native_id in native.catalog.get('divergent_tracks',[]):
            changed.append(dto)
        elif cached.get(str(tid),{}).get('signature')!=signatures[tid]:
            row=native_rows.get(catalog['by_source'][tid],{})
            if (str(tid) not in cached and row.get('master_db_id')==captured['db_id']
                    and native_matches(row,dto,extras[tid],images[tid],art_cache,track.track_id)):
                adopted.add(tid)
            else:changed.append(dto)
    workspace = tempfile.TemporaryDirectory(prefix='soundbox-cdj-incremental-')
    try:
        root = Path(workspace.name).resolve()
        for path,raw in native.files:
            local = root/'baseline'/path;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(raw)
        if not (root/'baseline'/DATABASE_FILES[2]).exists():
            from .cdj_usb import CdjReadSession
            from .cdj_device_cues import read as read_cues
            reader=CdjReadSession(session.target,session.mount_identity[0])
            cue_rows={}
            try:
                for song in native.songs:
                    local=root/'classic-cues'/song.id/'ANLZ0000.DAT'
                    local.parent.mkdir(parents=True,exist_ok=True)
                    if song.analysis:
                        for ext in ('DAT','EXT'):
                            relative=str(PurePosixPath(song.analysis.lstrip('/')).with_suffix('.'+ext))
                            try:raw=reader.read(relative,limit=64*1024*1024)
                            except FileNotFoundError:continue
                            local.with_suffix('.'+ext).write_bytes(raw)
                    cue_rows[song.id]=read_cues(local)
            finally:reader.close()
            path=root/'classic-cues.json';path.write_text(json.dumps(cue_rows))
            run_renderer('upgrade',root,root/'baseline',path,should_cancel=should_cancel)
        inputs = root/'inputs';inputs.mkdir()
        required = sum(Path(t['source_path']).stat().st_size for t in changed)
        if required*2+128*1024*1024>shutil.disk_usage(root).free:
            raise ValueError('Insufficient local disk space for the new/changed CDJ tracks')
        request = deepcopy(captured)
        request['tracks'] = deepcopy(changed)
        request['track_extras'] = [extras[int(t['id'])] for t in changed]
        request['playlists'] = []
        request.update(native_profile='rekordbox-6.8.7.0303',
            settings_database=str(root/'baseline'/DATABASE_FILES[2]),settings_pdb=str(root/'baseline'/DATABASE_FILES[0]),
            settings_files=[(Path(path).name,str(root/'baseline'/path)) for path,_ in native.files if path not in DATABASE_FILES],
            classic_background=native.background_classic,tag_master_db_id=native.tag_master_id)
        contracts = {}
        reuse={}
        known_placements={}
        for dto in changed:
            tid=int(dto['id']);row=native_rows.get(catalog['by_source'][tid],{})
            if row.get('master_db_id')==captured['db_id'] and row.get('source_id')==tid and row.get('analysis'):
                known_placements[tid]=(row['audio'],str(PurePosixPath(row['analysis']).parent))
            # Native identity/counters and its media inventory are a normal
            # export baseline, not a new integrity certificate. Do not read a
            # rekordbox song back from USB merely to recognize it.
            if (row.get('master_db_id')==captured['db_id'] and row.get('source_id')==tid
                    and row.get('file_size')==dto['file_size']
                    and dto['file_size']>0 and dto['file_size']==source_epoch(dto['source_path'])[2]
                    and row.get('information_update_count')==extras[tid]['information_update_count']
                    and not previous['files'].get(row.get('audio','').lstrip('/'),{}).get('owned')
                    and row.get('analysis')):
                reuse[tid]=(row['audio'],str(PurePosixPath(row['analysis']).parent))
        placements = _placements(changed,catalog,previous,known_placements)
        reuse={tid:value for tid,value in reuse.items() if tid in known_placements}
        if changed:
            (root/'partial').mkdir()
            request['pre_staged'] = True
        for at,dto in enumerate(request['tracks']):
            if should_cancel():
                raise OperationCancelled('CDJ preparation cancelled')
            tid = int(dto['id'])
            source = Path(dto['source_path']).resolve(strict=True)
            if source.suffix.lower() not in ('.wav','.mp3','.aif','.aiff','.flac','.m4a'):
                raise ValueError(f'Unsupported CDJ audio format: {dto["title"]}')
            folder = inputs/str(tid);folder.mkdir()
            with acquire_source(SourceContract(source),root,should_cancel=should_cancel) as snapshot:
                destination = root/'partial'/placements[tid][0].lstrip('/')
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.hardlink_to(snapshot.path)
                dto['source_path'] = str(destination)
                contracts[tid] = dict(id=str(tid),path=str(source),size=snapshot.size,sha256=snapshot.sha256,
                    epoch=[snapshot.observation.st_dev,snapshot.observation.st_ino,snapshot.observation.st_size,snapshot.observation.st_mtime_ns])
            for ext in ('DAT','EXT','2EX'):
                path = Path(dto['analysis_source']).with_suffix('.'+ext)
                before = source_epoch(path)
                if before[2]>64*1024*1024:
                    raise ValueError('Unsupported native analysis size')
                raw = path.read_bytes()
                if before!=source_epoch(path):
                    raise ValueError('Native analysis changed during capture')
                (folder/('ANLZ0000.'+ext)).write_bytes(raw)
            dto['analysis_source'] = str(folder/'ANLZ0000.DAT')
            image = images[tid]
            if image is not None:
                cover = folder/'cover.jpg';cover.write_bytes(image);dto['artwork'] = str(cover)
            dto['device'] = dict(id=catalog['by_source'][tid],master_db_id=captured['db_id'],
                master_content_id=tid,audio='',analysis_dir='',preserve=False,exported=True)
            on_progress(at+1,len(changed),dto['title'])
        prepared = time.monotonic()
        partial = None
        if changed:
            path = root/'request.json';path.write_text(json.dumps(request))
            run_renderer('render',root,path,root/'partial',should_cancel=should_cancel)
            partial = read_library(root/'partial',(root/'partial').stat().st_dev,should_cancel=should_cancel)
            for song in partial.songs:
                if (song.path,str(PurePosixPath(song.analysis).parent))!=placements[int(song.source_id)]:
                    raise ValueError('Native CDJ layout disagrees with the reserved placement')
        rendered = time.monotonic()
        check = cdj_source.capture_metadata(library,tracks,titles=titles,artists=artists,playlists=catalog['jobs'])
        if captured['source_revision']!=check['source_revision']:
            raise ValueError('Source metadata/cues/grid changed during preparation; retry after edits finish')
        for dto in captured['tracks']:
            tid = int(dto['id'])
            if input_signature(dto,extras[tid],digest(images[tid]) if images[tid] is not None else '')!=signatures[tid]:
                raise ValueError('Source inputs changed during preparation')
            if tid in contracts and source_epoch(contracts[tid]['path'])!=contracts[tid]['epoch']:
                raise ValueError('Source audio changed after capture')
        art_map,transfers = _image_mapping(previous,native,partial,root/'partial') if partial else ({},[])
        from .cdj_catalog import map_tags
        mapped_tags,tag_ids=map_tags(captured['my_tags'],native.catalog.get('native_tags',[]),
                                   native.tag_master_id==captured['db_id'],native.catalog.get('classic_tags',[]))
        spec = dict(baseline=str(root/'baseline'),partial=str(root/'partial') if partial else None,
            output=str(root/'candidate'),playlists=catalog['nodes'],track_ids=sorted(catalog['track_ids']),artwork=art_map,
            selected_only=additive,
            selected_track_ids=sorted(catalog['by_source'][int(t['id'])] for t in captured['tracks']),
            selected_playlist_ids=sorted(catalog['touched_nodes']),
            my_tags=mapped_tags,additive=additive or bool(previous.get('foreign_tracks')),
            tag_assignments={str(catalog['by_source'][int(t['id'])]):[tag_ids[int(tag)] for tag in t['my_tags']] for t in captured['tracks']},
            preserve_playlists=([n['id'] for n in catalog['nodes'] if n['id'] not in catalog['touched_nodes']]
                if additive else [i for i in previous.get('foreign_playlists',[]) if i not in catalog['reverse']]))
        tag_signature = digest(json.dumps(captured['my_tags'],sort_keys=True,separators=(',',':')).encode())
        selected_repair=bool(catalog['touched_nodes'].intersection(native.catalog.get('divergent_playlists',[])))
        if (not changed and not adopted and not selected_repair and DATABASE_FILES[2] in dict(native.files)
                and previous.get('render_policy') == CATALOG_RENDER_POLICY
                and previous.get('tag_signature') == tag_signature
                and _catalog_unchanged(catalog,previous['view'])
                and _catalog_bytes_unchanged(native,load_manifest(session))):
            # Only pure reconstruction is skipped. The second source capture
            # above and ordinary make_plan's fresh target/recovery proofs below
            # still run, using the exact captured native catalog bytes.
            view = deepcopy(previous['view'])
            for relative in DATABASE_FILES:
                destination = root/'candidate'/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(root/'baseline'/relative,destination)
        else:
            path = root/'merge.json';path.write_text(json.dumps(spec))
            view = run_renderer('merge',root,path,should_cancel=should_cancel)
        for p in view['playlists']:
            if int(p['id']) in catalog['reverse']:p['source_id'] = catalog['reverse'][int(p['id'])]
        if partial:
            for song in partial.songs:
                references = ([] if int(song.source_id) in reuse else [song.path.lstrip('/')]) + [str(PurePosixPath(song.analysis.lstrip('/')).with_suffix('.'+ext)) for ext in ('DAT','EXT','2EX')]
                transfers.extend((root/'partial'/p,p) for p in references)
        for relative,raw in native.files:
            if relative not in DATABASE_FILES:
                destination = root/'candidate'/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(raw)
        for source,relative in transfers:
            destination = root/'candidate'/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            source.replace(destination)
        shutil.rmtree(inputs)
        # Profiles stay byte-identical and are carried by committed inventory.
        needed = referenced_files(view)|referenced_files({'track_rows':view.get('classic_track_rows',[])})|{p for p,_ in native.files if p not in DATABASE_FILES}
        removed = referenced_files({'track_rows':view.pop('removed_track_rows',[])})-needed
        present = {p.relative_to(root/'candidate').as_posix() for p in (root/'candidate').rglob('*') if p.is_file()}
        retained = (needed|previous['files'].keys())-present-removed
        retained &= previous['files'].keys()
        if not retained.issubset(previous['files']):
            raise ValueError('Merged catalog references unowned CDJ assets')
        plan = make_plan(session,root/'candidate',selection=catalog['selection'],db_id=captured['db_id'],
            view=view,workspace=workspace,should_cancel=should_cancel,recovery_root=recovery_root,retained_files=retained,
            source_contracts={placements[tid][0].lstrip('/'):contract for tid,contract in contracts.items() if tid not in reuse},catalog_baseline=previous,
            allowed_asset_changes={str(PurePosixPath(path.lstrip('/'))/('ANLZ0000.'+ext)) for _audio,path in reuse.values() for ext in ('DAT','EXT','2EX')},
            expected_manifest=baseline_manifest,removed_files=removed)
        # An unchanged song/playlist catalog never rewrites native databases.
        old_nodes = [(p['id'],p['parent'],p['title'],p['folder'],p['entries']) for p in previous['view']['playlists']]
        new_nodes = [(p['id'],p['parent'],p['title'],p['folder'],p['entries']) for p in view['playlists']]
        if (not changed and not adopted and not selected_repair and not removed and old_nodes==new_nodes and len(view['track_rows'])==len(previous['view']['track_rows'])
                and previous.get('tag_signature')==tag_signature and previous.get('render_policy')==CATALOG_RENDER_POLICY):
            # Player preferences are retained byte-for-byte. After a fresh
            # profile-only change, record make_plan's verified live profile
            # inventory once so the next operation can reuse reconstruction.
            # Keep current native DB hashes, never randomized rendered bytes.
            profiles = {path:dict(plan.manifest['files'][path]) for path,_raw in native.files
                        if path not in DATABASE_FILES}
            noop_manifest = dict(previous,files={**previous['files'],**profiles})
            old_manifest = plan.old_manifest
            plan.close()
            noop=CdjPlan(session.target,session.mount_identity,session.volume_uuid,root,noop_manifest,(),
                old_manifest,False,len(view['track_rows']),len(catalog['selection']),None,recovery_root)
            if noop.requires_commit:noop.manifest=dict(noop_manifest,generation=uuid.uuid4().hex)
            return noop
        from .cdj_export import remember_standalone
        remember_standalone(plan.manifest, previous, library, standalone)
        records = deepcopy(cached)
        plan.manifest['foreign_playlists']=[i for i in previous.get('foreign_playlists',[]) if i not in catalog['reverse']]
        managed_ids={catalog['by_source'][int(t.native_id)] for t in tracks}
        plan.manifest['foreign_tracks']=[i for i in previous.get('foreign_tracks',[]) if i not in managed_ids]
        for dto in captured['tracks']:
            tid = int(dto['id'])
            records[str(tid)] = dict(signature=signatures[tid],source=contracts.get(tid,cached.get(str(tid),{}).get('source')))
        ids = {str(t['source_id']) for t in view['track_rows']}
        plan.manifest['track_inputs'] = {tid:r for tid,r in records.items() if tid in ids}
        for tid in adopted:plan.manifest['track_inputs'][str(tid)]['baseline']='native catalog inventory'
        plan.manifest['next_track_id'] = catalog['next_track']
        plan.manifest['next_playlist_id'] = catalog['next_playlist']
        plan.manifest['tag_signature'] = tag_signature
        plan.manifest['render_policy'] = CATALOG_RENDER_POLICY
        from .cdj_export import selection_hint
        available = {manifest.playlist_key(p):p for p in [*library.playlists,*all_playlists]}
        tracked = [available[p['key']] for p in catalog['selection'] if p['key'] in available]
        old_tracked = [available[p['key']] for p in previous['selection'] if p['key'] in available]
        hints = dict(previous.get('playlist_hints',{}))
        if not hints and previous.get('selection_hint') == selection_hint(library,old_tracked,titles=titles,artists=artists):
            hints = {manifest.playlist_key(p):selection_hint(library,[p],titles=titles,artists=artists) for p in old_tracked}
        hints.update({manifest.playlist_key(p):selection_hint(library,[p],titles=titles,artists=artists) for p in catalog['jobs']})
        plan.manifest['playlist_hints'] = {p['key']:hints[p['key']] for p in catalog['selection'] if p['key'] in hints}
        plan.manifest['selection_hint'] = selection_hint(library,tracked,titles=titles,artists=artists)
        LOG.info('CDJ incremental: requested=%d prepared=%d retained=%d writes=%d prep=%.3fs render=%.3fs total=%.3fs',
            len(tracks),len(changed),len(view['track_rows'])-len(changed),len(plan.changes),prepared-started,rendered-prepared,time.monotonic()-started)
        return plan
    except Exception:
        workspace.cleanup()
        raise
