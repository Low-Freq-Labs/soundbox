"""Explicit captured-audio analysis. Results are durable SoundBox data.

No desktop source-write authority is inferred from detection. The saved canonical
path/audio digest lets direct imports and later rekordbox identities share results.
Existing native/manual grids and source cues remain authoritative on playback.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile

from .source_acquisition import SourceContract, acquire_source

BPM_RANGES = ((70,180),(48,95),(58,115),(68,135),(78,155),(88,175),
              (98,195),(108,215),(118,235),(128,255))
DEFAULTS = dict(bpm=True, key=True, bpm_range=0, first_cue=False, process='Normal')
LEGACY_ENGINE = 'rbxport-b739d369-constant-v1'
ADAPTIVE_ENGINE = 'rbxport-b739d369-adaptive-v2'
PREVIOUS_ENGINE = 'rbxport-b739d369-whole-v3'
ENGINE = 'rbxport-b739d369-whole-v4'
_ENGINES = frozenset((LEGACY_ENGINE, ADAPTIVE_ENGINE, PREVIOUS_ENGINE, ENGINE))
AUTOMATIC_MUSIC_MAX_SECONDS = 15 * 60


def automatic_music_allowed(seconds):
    try:value=float(seconds or 0)
    except (TypeError,ValueError):return False
    return math.isfinite(value) and 0<=value<=AUTOMATIC_MUSIC_MAX_SECONDS


def options(value=None):
    raw = value if isinstance(value, dict) else {}
    result = {**DEFAULTS, **{k:v for k,v in raw.items() if k in DEFAULTS}}
    if any(type(result[k]) is not bool for k in ('bpm','key','first_cue')):
        raise ValueError('Invalid analysis selection')
    if type(result['bpm_range']) is not int or not 0 <= result['bpm_range'] < len(BPM_RANGES):
        raise ValueError('Invalid BPM range')
    if result['process'] not in ('Performance','Normal','Power saving'):
        raise ValueError('Invalid analysis process preference')
    return result


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def result_key(path):
    return hashlib.sha256(str(path).encode()).hexdigest()


def saved_result(results, track):
    """Cheap presentation lookup; worker revalidation grants playback use."""
    record = results.get(result_key(track.path))
    if not isinstance(record, dict) or record.get('path') != str(track.path):
        return None
    if track.size and record.get('size') != track.size:
        return None
    return record


def validate_result(record):
    if (type(record.get('engine')) is not str or record.get('engine') not in _ENGINES or not isinstance(record.get('sha256'), str)
            or len(record['sha256']) != 64 or any(c not in '0123456789abcdef' for c in record['sha256'])
            or not isinstance(record.get('epoch'), list) or len(record['epoch']) != 5
            or any(type(n) is not int or n < 0 for n in record['epoch'])
            or not isinstance(record.get('path'), str) or not Path(record['path']).is_absolute()
            or type(record.get('size')) is not int or record['size'] != record['epoch'][2]):
        raise ValueError('Invalid captured analysis identity')
    if any(type(record.get(field,record['engine'])) is not str or record.get(field,record['engine']) not in _ENGINES
           for field in ('grid_engine','key_engine')):
        raise ValueError('Invalid analysis component provenance')
    duration = record.get('duration')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 4 <= duration <= 86400:
        raise ValueError('Invalid analysis duration')
    beats = record.get('beats') or []
    if not isinstance(beats, list) or len(beats) > 400000:
        raise ValueError('Invalid detected grid')
    previous = -1
    for row in beats:
        if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int or row[0] not in (1,2,3,4)
                or type(row[1]) is not int or not previous < row[1] <= duration * 1000
                or type(row[2]) not in (int,float) or not math.isfinite(row[2]) or not 1 <= row[2] <= 655.35):
            raise ValueError('Invalid detected beat row')
        previous = row[1]
    bpm = record.get('bpm')
    if bpm is not None and (type(bpm) not in (int,float) or not math.isfinite(bpm) or not 1 <= bpm <= 655.35):
        raise ValueError('Invalid detected BPM')
    key = record.get('key')
    if key is not None and key not in {str(i)+letter for i in range(1,13) for letter in ('A','B')}:
        raise ValueError('Invalid detected key')
    if record.get('first_cue') is not None and (not beats or record['first_cue'] != beats[0][1]):
        raise ValueError('Invalid first-beat Memory Cue')
    from .local_grid import validate_draft
    validate_draft(record)
    from .local_cues import validate
    validate(record)


def _source_context(library, track):
    if library.source_kind != 'rekordbox-db' or track.imported:
        return {}
    from pyrekordbox import Rekordbox6Database
    from .rekordbox_db import _copy_stable_snapshot, _deleted
    with tempfile.TemporaryDirectory(prefix='soundbox-analysis-db-') as folder:
        target = Path(folder)/'master.db'
        _copy_stable_snapshot(Path(library.source), target)
        db = Rekordbox6Database(path=str(target))
        try:
            properties = list(db.get_property())
            row = db.get_content(ID=track.native_id)
            if (len(properties) != 1 or str(properties[0].DBID) != library.source_library_id
                    or row is None or _deleted(row) or str(row.UUID) != track.source_uuid
                    or Path(row.FolderPath).resolve(strict=True) != track.path.resolve(strict=True)):
                raise ValueError('Source track changed; refresh before analyzing')
            flags = row.Analysed
            if type(flags) is not int or flags < 0:
                raise ValueError('Unknown source analysis flags')
            if flags & 0x80:
                raise ValueError('Analysis Lock is on')
            context=dict(library_id=library.source_library_id, uuid=track.source_uuid,
                        flags=flags, bpm=row.BPM, key=row.KeyID,
                        analysis_updated=row.AnalysisUpdated, analysis_path=row.AnalysisDataPath)
            if track.source_native_id and not row.AnalysisDataPath:context['bare_registration']=True
            return context
        finally:
            db.close()


def analyze(library, track, preferences=None, *, should_cancel=lambda:False,automatic=False):
    from .cdj_usb import run_renderer
    prefs = options(preferences)
    if automatic and not automatic_music_allowed(track.total_time):return None
    if not (prefs['bpm'] or prefs['key']):
        raise ValueError('Select BPM / Grid or Key')
    source_context = _source_context(library, track)
    source = track.path.expanduser().resolve(strict=True)
    if source.suffix.lower() not in ('.wav','.aif','.aiff','.mp3','.flac','.m4a','.aac','.mp4'):
        raise ValueError('Unsupported analysis audio format')
    with tempfile.TemporaryDirectory(prefix='soundbox-analysis-') as folder:
        root = Path(folder).resolve()
        with acquire_source(SourceContract(source), root, should_cancel=should_cancel) as snapshot:
            if automatic:
                # Newly dropped files initially have unknown duration. Inspect
                # the immutable capture before invoking BPM/key DSP.
                from .media_probe import read_media_header
                duration=float(read_media_header(snapshot.path).duration)
                if not automatic_music_allowed(duration):return None
            low, high = BPM_RANGES[prefs['bpm_range']]
            request = root/'analysis.json'
            request.write_text(json.dumps(dict(audio=str(snapshot.path), bpm=prefs['bpm'], key=prefs['key'],
                                              min_bpm=low, max_bpm=high)))
            result = run_renderer('analyze', root, request, should_cancel=should_cancel,
                                  process_nice={'Performance':0,'Normal':10,'Power saving':18}[prefs['process']])
            if should_cancel():
                raise RuntimeError('Analysis cancelled')
            verified_epoch = identity(snapshot.observation)
            if identity(source.stat()) != verified_epoch:
                # Import-time bookmark creation and cloud hydration may finish
                # while DSP runs on its immutable local snapshot. Revalidate
                # those exact bytes, rather than discard completed detection or
                # accept new timestamps without a content proof.
                try:
                    with acquire_source(SourceContract(source,snapshot.size,snapshot.sha256),root,
                                        should_cancel=should_cancel) as checked:
                        verified_epoch = identity(checked.observation)
                except (OSError, RuntimeError) as exc:
                    if should_cancel(): raise RuntimeError('Analysis cancelled') from exc
                    raise ValueError('Source changed during analysis; retry after refreshing') from exc
            if identity(source.stat()) != verified_epoch or source_context != _source_context(library, track):
                raise ValueError('Source changed during analysis; retry after refreshing')
            result.update(engine=ENGINE, path=str(source), sha256=snapshot.sha256, size=snapshot.size,
                          epoch=verified_epoch, source_context=source_context,
                          grid_engine=ENGINE, key_engine=ENGINE,
                          first_cue=(result['beats'][0][1] if prefs['first_cue'] and result.get('beats') else None))
            # Silence/too little rhythmic evidence must not become a fabricated grid.
            if prefs['bpm'] and (not result.get('beats') or result.get('confidence', 0) < .5):
                result.update(bpm=None, beats=[], first_cue=None)
            validate_result(result)
            return result


def merge_result(previous, current, preferences):
    """Unchecked or inconclusive components preserve results for the same bytes."""
    result = deepcopy(current)
    if previous and previous.get('sha256') == current['sha256']:
        if previous.get('grid_draft'):
            result['grid_draft'] = deepcopy(previous['grid_draft'])
        if previous.get('cue_draft') is not None:
            result['cue_draft'] = deepcopy(previous['cue_draft'])
        if not preferences['bpm'] or not current.get('beats'):
            for key in ('bpm','beats','confidence','first_cue','tempo_policy'):
                result[key] = deepcopy(previous.get(key))
            result['grid_engine'] = previous.get('grid_engine',previous['engine'])
        if not preferences['key'] or current.get('key') is None:
            for key in ('key','key_name'):
                result[key] = previous.get(key)
            result['key_engine'] = previous.get('key_engine',previous['engine'])
    validate_result(result)
    return result


def playback_data(data, record, path, *, verification_cache=None, should_cancel=lambda: False):
    """Worker-only overlay. Preserve native/manual grids and every source cue."""
    if not record:
        return data
    try:
        validate_result(record)
        source = Path(path)
        if str(source) != record['path']:
            return data
        verified_epoch = identity(source.stat())
        if verified_epoch != record['epoch']:
            if verification_cache is None:
                return data
            # Cloud hydration/bookmarks can change ctime after detection. Prove
            # the original bytes; never accept a changed timestamp on faith or
            # replace the user's completed analysis with a fallback estimate.
            def captured_hash(canonical):
                with tempfile.TemporaryDirectory(prefix='soundbox-analysis-identity-') as area:
                    with acquire_source(SourceContract(canonical), Path(area),
                                        should_cancel=should_cancel) as snapshot:
                        return snapshot.sha256
            digest, _stat = verification_cache.verify(source, captured_hash)
            if digest != record['sha256']:
                return data
            verified_epoch = identity(_stat)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return data
    provenance = data.get('provenance') or {}
    if provenance.get('grid') in {'rekordbox', 'soundbox-manual'} or provenance.get('user_data') == 'soundbox':
        return data
    if not record.get('beats'):
        return data
    from .local_grid import effective_result
    resolved = effective_result(record)
    cues = data.get('cues') or []
    captured_cues=not record.get('source_context') or (record.get('source_context') or {}).get('bare_registration') is True
    if record.get('cue_draft') is not None and captured_cues:
        from .local_cues import cues as draft_cues
        cues = draft_cues(record)
    if (not cues and record.get('cue_draft') is None and record.get('first_cue') is not None
            and captured_cues):
        cues = [dict(kind='memory', slot=0, position_ms=record['first_cue'], comment='First analyzed beat')]
    return {**data, 'beats': deepcopy(resolved['beats']), 'cues': cues,
            'provenance': {**provenance,
                           'grid':'soundbox-manual' if record.get('grid_draft') else 'soundbox-detected',
                           'analysis_engine':record.get('grid_engine',record['engine'])},
            '_detected_bpm':resolved['bpm'], '_detected_sha256':record['sha256'],
            '_detected_epoch':verified_epoch}
