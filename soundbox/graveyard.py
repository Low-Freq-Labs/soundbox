"""Read-only Graveyard identities and bounded filesystem accounting.

Historical rows are display/review evidence, never active Collection tracks or
native edit authority. All native SQL reads use a private stable snapshot.
"""
from copy import deepcopy
from pathlib import Path
import stat
import tempfile
import hashlib

from .analysis import identity,result_key
from .rekordbox_db import _camelot, _copy_stable_snapshot
from .rekordbox_write import _connect, _identity, _rows, _digest, _cell


RECOVERED_FORMAT='soundbox-graveyard-identities-v1'


def archive_path(root,source,library_id):
    return Path(root)/'graveyard-identities'/(result_key(str(source)+'\0'+str(library_id))+'.json')


def _display(row, artists, albums, genres, keys):
    path=Path(row['FolderPath'])
    return dict(id=row['ID'],title=row.get('Title') or path.stem,
        artist=artists.get(str(row.get('ArtistID')),''),album=albums.get(str(row.get('AlbumID')),''),
        genre=genres.get(str(row.get('GenreID')),''),bpm=f"{row['BPM']/100:.2f}" if row.get('BPM') else '',
        key=_camelot(keys.get(str(row.get('KeyID')),'')),time=_duration(int(row.get('Length') or 0)),
        format=path.suffix.lstrip('.').upper(),size=int(row.get('FileSize') or 0),
        location=str(path),sampleRate=str(row.get('SampleRate') or ''),bitDepth=str(row.get('BitDepth') or ''),
        bitrate=str(row.get('BitRate') or ''),rating=str(row.get('Rating') or 0),year=str(row.get('ReleaseYear') or 0),
        comments=row.get('Commnt') or '',playCount=str(row.get('DJPlayCount') or 0),dateCreated=row.get('DateCreated') or '')


def _duration(seconds):
    minutes,seconds=divmod(max(0,seconds),60);hours,minutes=divmod(minutes,60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


def catalog(source,library_id,track_ids,*,imported=None,bindings=(),remembered=None,should_cancel=lambda:False):
    """One metadata snapshot and stat per distinct path; no audio hydration/read."""
    source=Path(source) if source else None;imported=imported or {};remembered=remembered or {}
    lookup={str(tid):str(tid) for tid in track_ids}
    bound_uuids={};conflicts=set()
    for binding in bindings:
        if binding.get('source')==str(source) and binding.get('library_id')==library_id and binding.get('local_id') in lookup:
            tid=binding['local_id']
            if tid in bound_uuids:conflicts.add(tid)
            bound_uuids[tid]=binding.get('native_uuid');lookup[tid]=binding['native_id']
    rows: dict[str,dict]={};capture_sha=''
    artists: dict[str,str]={};albums: dict[str,str]={};genres: dict[str,str]={};keys: dict[str,str]={}
    native_paths: dict[str,list[dict]]={}
    if source is not None:
        with tempfile.TemporaryDirectory(prefix='soundbox-graveyard-read-') as folder:
            local=Path(folder).resolve()/'master.db';_copy_stable_snapshot(source,local)
            with local.open('rb') as stream:capture_sha=hashlib.file_digest(stream,'sha256').hexdigest()
            if should_cancel():raise RuntimeError('Graveyard scan cancelled')
            conn=_connect(local,readonly=True)
            try:
                if _identity(conn)!=(library_id,'6000'):raise ValueError('Graveyard source library changed')
                rows={str(r['ID']):r for r in _rows(conn,'djmdContent')}
                for table,dest,field in (('djmdArtist',artists,'Name'),('djmdAlbum',albums,'Name'),
                                         ('djmdGenre',genres,'Name'),('djmdKey',keys,'ScaleName')):
                    dest.update({str(r['ID']):r[field] or '' for r in _rows(conn,table)})
            finally:conn.close()
        # Native aliases only need path resolution, never payload access.
        for row in rows.values():
            if should_cancel():raise RuntimeError('Graveyard scan cancelled')
            if not row.get('rb_local_deleted') and row.get('FolderPath'):
                try:path=str(Path(row['FolderPath']).resolve(strict=True))
                except (OSError,RuntimeError):continue
                native_paths.setdefault(path,[]).append(dict(id=row['ID'],uuid=row['UUID'],title=row['Title']))
    entries: dict[str,dict]={};observed={}
    for tid,native_id in lookup.items():
        if should_cancel():raise RuntimeError('Graveyard scan cancelled')
        current=rows.get(native_id);history=remembered.get(tid);record=imported.get(tid)
        if (tid in conflicts or current and (tid in bound_uuids and current['UUID']!=bound_uuids[tid]
                or history and current['UUID']!=history.get('uuid'))):
            entries[tid]=dict(id=tid,error='Native identity changed; historical data retained',path='',native_id=native_id);continue
        if current:
            raw_path=current.get('FolderPath') or '';display=_display(current,artists,albums,genres,keys)
            native=dict(id=native_id,uuid=current['UUID'],active=not bool(current['rb_local_deleted']))
        elif record:
            raw_path=record['path'];p=Path(raw_path)
            display=dict(id=tid,title=p.stem,location=raw_path,time=_duration(int(record.get('time') or 0)),
                         format=p.suffix.lstrip('.').upper(),size=record.get('size',0),artist='')
            native=dict(id=native_id,uuid='',active=False)
        elif history and history.get('id')==native_id and history.get('library_id')==library_id and history.get('source')==str(source):
            raw_path=history['path'];display=deepcopy(history['display']);native=dict(id=native_id,uuid=history['uuid'],active=False)
        else:
            entries[tid]=dict(id=tid,native_id=native_id,path='',error='No verified file path is available',
                native_absent=source is not None and native_id not in rows,
                proof=dict(kind='native-absent-snapshot',database_sha256=capture_sha) if source is not None else {})
            continue
        entry=dict(id=tid,native_id=native_id,native=native,path=raw_path,display=display,error='',exists=False)
        if current:
            entry['proof']=dict(kind='native-read-snapshot',database_sha256=capture_sha,
                                row_sha256=_digest({k:_cell(v) for k,v in current.items()}))
        elif history:entry['proof']=deepcopy(history.get('proof',{}))
        try:
            p=Path(raw_path)
            if not raw_path or not p.is_absolute():raise ValueError('Native file path is unavailable')
            canonical=p.resolve(strict=True)
            if canonical not in observed:
                s=canonical.stat()
                if not stat.S_ISREG(s.st_mode):raise ValueError('Track path is not a regular file')
                observed[canonical]=dict(canonical=str(canonical),epoch=identity(s),size=s.st_size,
                    allocated=getattr(s,'st_blocks',0)*512,links=s.st_nlink,
                    dataless=bool(getattr(s,'st_flags',0)&getattr(stat,'SF_DATALESS',0x40000000)))
            entry.update(deepcopy(observed[canonical]),exists=True)
            entry['references']=deepcopy(native_paths.get(str(canonical),[]))
        except (OSError,RuntimeError,ValueError) as exc:entry['error']=str(exc)
        entries[tid]=entry
    return entries


def totals(entries,track_ids=None):
    values=entries.values() if track_ids is None else (entries.get(tid,dict(path='',error='Unknown track')) for tid in set(track_ids))
    seen=set();size=allocated=files=unresolved=unavailable=0
    for entry in values:
        if not entry.get('path'):unresolved+=1;continue
        if not entry.get('exists'):unavailable+=1;continue
        epoch=entry['epoch'];inode=tuple(epoch[:2])
        if inode in seen:continue
        seen.add(inode);files+=1;size+=entry['size'];allocated+=entry['allocated']
    return dict(files=files,size=size,allocated=allocated,unresolved=unresolved,unavailable=unavailable)
