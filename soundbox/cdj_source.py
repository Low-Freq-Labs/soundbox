"""Fresh, read-only CDJ metadata capture from the existing DB/WAL snapshot API.

No browser rows, player overlays, live SQLite connection or source write API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from . import metadata, rekordbox_db
from .rekordbox_analysis import resolve_dat_path


def _number(value, *, maximum=0xFFFFFFFF):
    number = int(value or 0)
    if not 0 <= number <= maximum:
        raise ValueError('Native metadata is outside its supported range')
    return number


def _actual_technical(track,observed,path):
    """Actual header facts override stale cached technical columns only.

    Native Reload Tags/deck load can leave old FileSize/BitDepth/SampleRate in
    master.db after a compatible same-path replacement. Bind the header result
    to this exact canonical file epoch; musical/native identity stays untouched.
    Export's normal captured-byte proof still follows before device publication.
    """
    if not observed:return
    from .analysis import identity
    try:
        if (str(path.resolve(strict=True))!=observed['path']
                or identity(path.stat())!=observed['epoch'] or observed['facts']['status']!='compatible'):return
        facts=observed['facts'];bits=int(facts.get('bits') or 0);channels=int(facts['channels']);rate=int(facts['sample_rate'])
        if rate not in (44100,48000) or channels not in (1,2):return
        track['sample_rate']=rate;track['file_size']=observed['epoch'][2]
        if bits in (16,24) and facts.get('family') in ('wav','aiff'):
            track['metadata']['bit_depth']=bits;track['bitrate']=bits*channels*rate//1000
    except (OSError,ValueError,KeyError,TypeError):return


def _selected_rows(query_factory, table, track_ids):
    """Materialize selected native records in bounded exact-ID queries."""
    if not track_ids:
        return
    from sqlalchemy.orm import Query
    query = query_factory()
    if isinstance(query, Query):
        identities = sorted(track_ids)
        queries = [query.filter(table.ContentID.in_(identities[at:at+500]))
                   for at in range(0, len(identities), 500)]
    else:
        queries = [query]
    for rows in queries:
        for row in rows:
            if str(row.ContentID) in track_ids:
                yield row


def _selected_content_files(db, track_ids):
    from pyrekordbox.db6.tables import ContentFile
    return _selected_rows(db.get_content_file, ContentFile, track_ids)


def _selected_cues(db, track_ids):
    from pyrekordbox.db6.tables import DjmdCue
    return _selected_rows(db.get_cue, DjmdCue, track_ids)


def capture_metadata(library, tracks, *, titles=None, artists=None, playlists=None):
    """Return an explicit renderer DTO, bound to selected native IDs and UUIDs."""
    if library.source_kind != 'rekordbox-db':
        raise ValueError('CDJ export currently requires a native rekordbox library')
    expected = {track.native_id: track for track in tracks}
    if len(expected) != len(tracks):
        raise ValueError('Selected logical tracks share an ambiguous native identity')
    if any(track.imported or not track.source_uuid for track in tracks):
        raise ValueError('CDJ export requires native analysis and identity for every selected track')
    from pyrekordbox import Rekordbox6Database
    source = Path(library.source).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='soundbox-cdj-db-') as directory:
        copy = Path(directory) / 'master.db'
        rekordbox_db._copy_stable_snapshot(source, copy)
        db = Rekordbox6Database(path=str(copy))
        try:
            properties = list(db.get_property())
            if len(properties) != 1 or str(properties[0].DBID) != library.source_library_id:
                raise ValueError('Source library identity changed; reload before exporting')
            db_id = _number(properties[0].DBID)
            playlist_proofs = []
            if playlists is not None:
                source_ids = {p.pid for p in library.playlists if p.pid}
                selected = [p for p in playlists if p.pid in source_ids]
                if selected:
                    fresh = {p.pid:p for p in rekordbox_db._read_playlists(
                        db, rekordbox_db._active_track_ids(db), selected_ids={p.pid for p in selected})}
                    for cached in selected:
                        current = fresh.get(cached.pid)
                        native_keys=[library.tracks_by_id[key].native_id for key in cached.keys]
                        if current is None or (current.name,current.folder_path,current.keys) != (cached.name,cached.folder_path,native_keys):
                            raise ValueError('Source playlist changed; refresh the library and retry CDJ export')
                        playlist_proofs.append([current.pid,current.name,current.folder_path,current.keys])
            rows = {str(row.ID): row for row in rekordbox_db._content_rows(db, set(expected))}
            if set(rows) != set(expected):
                raise ValueError('A selected track disappeared from the source library')
            cues: dict[str, list[dict]] = {}
            for row in _selected_cues(db, set(expected)):
                tid = str(row.ContentID)
                if tid in expected and not rekordbox_db._deleted(row):
                    if row.ContentUUID != expected[tid].source_uuid:
                        raise ValueError('Cue identity does not match its selected track')
                    kind = _number(row.Kind, maximum=17)
                    if kind == 4:
                        raise ValueError('Unsupported native cue kind')
                    start = _number(row.InMsec)
                    end = int(row.OutMsec if row.OutMsec is not None else -1)
                    beats = _number(row.BeatLoopSize)
                    if end < -1:
                        raise ValueError('Invalid native cue loop endpoint')
                    if end <= start and beats >> 16 and beats & 0xffff:
                        bpm = _number(rows[tid].BPM)
                        if not bpm:
                            raise ValueError('Native beat loop has no verified tempo')
                        divisor = (beats & 0xffff)*bpm
                        end = start+((beats >> 16)*6_000_000+divisor//2)//divisor
                    cues.setdefault(tid, []).append(dict(
                        kind=kind, time_ms=start, loop_time_ms=end if end > start else None,
                        color_id=(_number(row.Color,maximum=255) if kind == 0 and row.Color not in (-1,255,None) else 0),
                        color_code=_number(row.ColorTableIndex, maximum=255),
                        comment=str(row.Comment or ''), active_loop=bool(row.ActiveLoop),
                        loop_numerator=beats >> 16, loop_denominator=beats & 0xffff))
            tags = []
            definitions = list(db.get_my_tag())
            for tag in definitions:
                if rekordbox_db._deleted(tag):
                    continue
                tags.append(dict(id=_number(tag.ID), seq=max(0, int(tag.Seq or 1)-1),
                                 name=tag.Name or '', attribute=_number(tag.Attribute, maximum=1),
                                 parent=0 if tag.ParentID in ('root', '', None) else _number(tag.ParentID)))
            _, memberships = rekordbox_db._read_tags(db,track_ids=set(expected),definitions=definitions)
            dates = {}
            for file in _selected_content_files(db,set(expected)):
                tid = str(file.ContentID)
                if tid not in expected or rekordbox_db._deleted(file):
                    continue
                if str(file.Path or '').upper().endswith('/ANLZ0000.DAT'):
                    if tid in dates:
                        raise ValueError('Ambiguous primary native analysis metadata')
                    dates[tid] = str(file.created_at or '')[:10]
            result, extras, proofs = [], [], []
            for tid, selected in expected.items():
                row = rows[tid]
                if rekordbox_db._deleted(row) or str(row.UUID) != selected.source_uuid:
                    raise ValueError('Selected track identity changed; reload before exporting')
                path = Path(row.FolderPath).expanduser().resolve(strict=True)
                if path != selected.path.expanduser().resolve(strict=True):
                    raise ValueError('Selected source path changed; reload before exporting')
                analysis = resolve_dat_path(source, row.AnalysisDataPath or '')
                if analysis is None:
                    raise ValueError(f'Native analysis is missing: {selected.name}')
                raw_title, raw_artist = row.Title or '', row.Artist.Name if row.Artist else ''
                title = metadata.effective_title(selected.track_id, raw_title, raw_artist, titles, library.title_junk)
                artist = metadata.effective_artist(selected.track_id, raw_title, raw_artist, artists,
                    True, library.artist_registry, path.stem)
                cue_rows = sorted(cues.get(tid, []), key=lambda c: (c['kind'], c['time_ms']))
                extra = dict(source_id=_number(tid), content_link=_number(row.ContentLink),
                    cue_update_count=_number(row.CueUpdated),
                    analysis_data_update_count=_number(row.AnalysisUpdated),
                    information_update_count=_number(row.TrackInfoUpdated),
                    legacy_analysis_date=dates.get(tid, ''),
                    legacy_kuvo_public=str(row.DeliveryControl or '').lower() == 'on')
                track = dict(id=_number(tid), source_path=str(path), title=title, artist=artist,
                    album=row.Album.Name if row.Album else '', genre=row.Genre.Name if row.Genre else '',
                    label=row.Label.Name if row.Label else '', key=rekordbox_db._camelot(row.Key.ScaleName if row.Key else ''),
                    comment=row.Commnt or '', date_added=str(row.DateCreated or '')[:10],
                    release_date=str(row.ReleaseDate or ''), bpm_x100=_number(row.BPM),
                    duration_sec=_number(row.Length, maximum=65535), rating=_number(row.Rating, maximum=5),
                    color_id=_number(row.ColorID, maximum=8), year=_number(row.ReleaseYear, maximum=65535),
                    bitrate=_number(row.BitRate), sample_rate=_number(row.SampleRate), file_size=_number(row.FileSize, maximum=2**63-1),
                    metadata=dict(track_number=_number(row.TrackNo), disc_number=_number(row.DiscNo, maximum=65535),
                        bit_depth=_number(row.BitDepth, maximum=65535), play_count=_number(row.DJPlayCount),
                        analysed=_number(row.Analysed), hot_cue_auto_load=str(row.HotCueAutoLoad or '').lower() == 'on',
                        date_created=str(row.DateCreated or '')[:10], isrc=row.ISRC or ''),
                    cues=cue_rows, device=None, analysis=[], analysis_source=str(analysis), source_unavailable=False,
                    artwork=None, my_tags=[_number(value) for value in memberships.get(tid, [])])
                from .track_info import exported
                exported(track,getattr(library,'info_overrides',{}).get(selected.track_id,{}))
                _actual_technical(track,getattr(library,'media_facts',{}).get(selected.track_id),path)
                result.append(track)
                extras.append(extra)
                proofs.append(dict(uuid=str(row.UUID), track=track, extra=extra))
            # The renderer receives the complete tag tree, including unassigned
            # definitions. Bind those globals as well as selected song/playlist
            # data so a second fresh capture detects every rendered input.
            digest = hashlib.sha256(json.dumps([proofs,playlist_proofs,tags], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return dict(db_id=db_id, tracks=result, track_extras=extras, my_tags=tags, source_revision=digest)
        finally:
            db.close()
