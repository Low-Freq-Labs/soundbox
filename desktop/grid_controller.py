"""Immediate deck grids with grouped saves in the shared source worker lane."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from soundbox import rekordbox_db, track_analysis
from soundbox.rekordbox_grid import (
    Beat, GridEdit, clear_secondary_grid, read_grid, transform_beats, transform_grid,
)
from soundbox.rekordbox_grid_store import PilotGridStore, load_grid_authority
from soundbox.rekordbox_write import ConflictError

LOG = logging.getLogger('soundbox')


@dataclass
class _Group:
    track_id: str
    track_uuid: str
    path: str
    operation: str
    value: int = 0
    nudges: list[int] = field(default_factory=list)
    ready: bool = True
    created: float = field(default_factory=monotonic)


class GridEditController(QObject):
    edit_started = Signal(str)
    changed = Signal(str)
    saved = Signal(object, str)
    failed = Signal(str)
    rejected = Signal(str)

    def __init__(self, shared, parent=None, *, store_factory=None, cache_dir=None,
                 prepare_cache=None):
        super().__init__(parent)
        self.shared = shared
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.prepare_cache = prepare_cache
        self._factory = store_factory or self._make_store
        self._requires_authority = store_factory is None
        self._authority = None
        self.current = ''
        self._identity = None
        self._snapshots = {}
        self._preview_bases = {}
        self._stores = {}
        self._errors = {}
        self._queue = []
        self._active = None
        self._probe_wanted = None
        self._closing = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._ready_tail)
        shared.idle.connect(self._drain)

    def configure(self):
        lib = self.shared.lib
        identity = (str(lib.source), lib.source_library_id) if lib else None
        if identity != self._identity or not self._queue:
            self._snapshots.clear()
            self._preview_bases.clear()
            self._stores.clear()
            self._errors.clear()
        self._identity = identity
        self._authority = (load_grid_authority(self.shared.data_root / 'rekordbox-grid-authority.json',
                                              Path(lib.source), lib.source_library_id)
                           if self._requires_authority and lib and self.shared.verified
                           and lib.source_kind == 'rekordbox-db' else None)
        self.set_current(self.current, force=True)

    def _make_store(self, lib, track):
        authority = load_grid_authority(self.shared.data_root / 'rekordbox-grid-authority.json',
                                       Path(lib.source), lib.source_library_id)
        if authority is None:
            raise RuntimeError('Grid editing is not enabled for this rekordbox library')
        return PilotGridStore(authority.bind(track.track_id, track.source_uuid),
                              self.shared._write_root(lib))

    def _store_for(self, lib, track):
        key = (str(lib.source), lib.source_library_id, track.track_id, track.source_uuid)
        if key not in self._stores:
            self._stores[key] = self._factory(lib, track)
        while len(self._stores) > 8:
            old = next(iter(self._stores))
            if old == key:
                break
            self._stores.pop(old)
        return self._stores[key]

    def _track(self, tid):
        return self.shared.lib.tracks_by_id.get(tid) if self.shared.lib else None

    def _source_reason(self, tid):
        if self._closing or self.shared.owner.stopping:
            return 'Finishing grid edits…'
        lib, track = self.shared.lib, self._track(tid)
        if not lib or not self.shared.verified or lib.source_kind != 'rekordbox-db':
            return 'Refresh a rekordbox library to edit its grid'
        if self._requires_authority and self._authority is None:
            return 'Grid editing is not enabled for this rekordbox library'
        if (not track or track.imported or not track.source_uuid
                or not track.source_analysis_path or not track.source_cue_revision):
            return 'Load an analyzed rekordbox track to edit its grid'
        return ''

    def reason(self, tid, *, allow_checking=False):
        reason = self._source_reason(tid)
        if reason:
            return reason
        if tid in self._errors:
            return self._errors[tid]
        snapshot = self._snapshots.get(tid)
        if snapshot is None:
            return '' if allow_checking else 'Checking saved grid…'
        if snapshot.track['Analysed'] & 0x80:
            return 'Analysis Lock is on in rekordbox'
        return ''

    def bpm_reason(self, tid):
        reason = self.reason(tid, allow_checking=True)
        if reason:
            return reason
        if self._requires_authority and not getattr(self._authority, 'manual_bpm_accepted', False):
            return 'Manual BPM editing awaits native acceptance for this library'
        snapshot = self._snapshots.get(tid)
        if snapshot is not None:
            if snapshot.content_file is None:
                return 'Manual BPM requires verified primary analysis-file metadata'
            if len({b.tempo for b in read_grid(snapshot.dat)}) != 1:
                return 'Manual BPM requires a constant-tempo grid'
        return ''

    def set_current(self, tid, *, force=False):
        tid = str(tid or '')
        changed = tid != self.current
        self.current = tid
        if not self._source_reason(tid) and (changed or force or tid not in self._snapshots):
            if not any(g.track_id == tid for g in self._queue):
                self._errors.pop(tid, None)
                self._probe_wanted = tid
                self._drain()
        self.changed.emit(tid)

    def _view_bytes(self, tid):
        snapshot = self._snapshots.get(tid)
        if snapshot is None:
            return None
        dat, ext = snapshot.dat, snapshot.ext
        bpm = snapshot.track['BPM'] / 100
        for group in self._queue:
            if group.track_id == tid:
                dat, ext = transform_grid(dat, ext, group.operation, group.value,
                                           tuple(group.nudges))
                if group.operation in ('double', 'halve', 'bpm'):
                    bpm = read_grid(dat)[0].tempo / 100
        return dat, ext, bpm

    def view(self, tid):
        result = self._view_bytes(tid)
        if result is None:
            preview = self._preview_bases.get(tid)
            if preview is None:
                return None
            beats, bpm = preview
            for group in self._queue:
                if group.track_id == tid:
                    beats = transform_beats(beats, group.operation, group.value, tuple(group.nudges))
                    if group.operation in ('double', 'halve', 'bpm'):
                        bpm = beats[0].tempo / 100
            return [[b.number, b.time_ms, b.tempo / 100] for b in beats], bpm
        dat, _ext, bpm = result
        return [[b.number, b.time_ms, b.tempo / 100] for b in read_grid(dat)], bpm

    def pending(self, tid):
        return any(group.track_id == tid for group in self._queue)

    def request(self, tid, operation, value=0, *, preview_rows=None):
        if self.reason(tid, allow_checking=True):
            return False
        if operation == 'bpm' and self.bpm_reason(tid):
            self.rejected.emit(self.bpm_reason(tid))
            return False
        track = self._track(tid)
        current = self._view_bytes(tid)
        try:
            if (operation not in ('downbeat', 'shift', 'double', 'halve', 'bpm')
                    or type(value) is not int):
                raise ValueError('Invalid grid operation or millisecond value')
            after = transform_grid(current[0], current[1], operation, value) if current else None
            if current is None:
                if tid not in self._preview_bases and preview_rows:
                    beats = tuple(Beat(r[0], round(r[2] * 100), r[1]) for r in preview_rows)
                    # Validation and every edit use the same native row arithmetic.
                    transform_beats(beats, operation, value)
                    self._preview_bases[tid] = beats, float(track.bpm or 0)
                preview = self.view(tid)
                if preview is not None:
                    beats = tuple(Beat(r[0], round(r[2] * 100), r[1]) for r in preview[0])
                    transform_beats(beats, operation, value)
        except Exception as exc:  # invalid operation is a local, definite rejection
            self.rejected.emit(str(exc))
            return False
        if current is not None and after == current[:2]:
            if operation == 'downbeat':
                LOG.info('Grid bar start already matches: track=%s target_ms=%s', tid, value)
            return True
        if current is None:
            LOG.info('Grid edit waiting for saved-grid check: track=%s operation=%s target_ms=%s',
                     tid, operation, value)
        tail = self._queue[-1] if self._queue else None
        if (operation == 'shift' and tail is not self._active and tail is not None
                and tail.track_id == tid and tail.operation == 'shift' and not tail.ready
                and monotonic() - tail.created < 1.0):
            tail.nudges.append(value)
        else:
            if tail is not None:
                tail.ready = True
            self._debounce.stop()
            tail = _Group(tid, track.source_uuid, str(track.path), operation,
                          0 if operation == 'shift' else value,
                          [value] if operation == 'shift' else [], operation != 'shift')
            self._queue.append(tail)
        if not tail.ready:
            remaining = max(0, int(1000 - (monotonic() - tail.created) * 1000))
            self._debounce.start(min(250, remaining))
        self._set_owned()
        self.edit_started.emit(tid)
        self.changed.emit(tid)  # immediate paint precedes any worker admission
        self._drain()
        return True

    def _set_owned(self):
        self.shared.grid_waiting = bool(self._queue or self._probe_wanted)

    def _ready_tail(self):
        if self._queue:
            self._queue[-1].ready = True
        self._drain()

    def _validate_snapshot(self, snapshot, cue_snapshot, track):
        if (snapshot.track != cue_snapshot.track or cue_snapshot.revision != track.source_cue_revision
                or snapshot.track['UUID'] != track.source_uuid
                or snapshot.track['FolderPath'] != str(track.path)):
            raise ConflictError('Track changed in rekordbox; refresh the library before editing')
        if (type(snapshot.track.get('Analysed')) is not int or snapshot.track['Analysed'] < 0
                or type(snapshot.track.get('FileType')) is not int
                or snapshot.track['FileType'] not in (1, 11, 12)):
            raise ConflictError('This track has an unsupported grid format or analysis flags')
        if snapshot.track['Analysed'] & 0x80:
            raise ConflictError('Analysis Lock is on in rekordbox')
        read_grid(snapshot.dat)
        clear_secondary_grid(snapshot.ext)

    def _drain(self):
        if self._active is not None or self.shared.busy or self.shared.checking or self.shared.owner.stopping:
            return
        if self._queue and self._queue[0].ready:
            head = self._queue[0]
            if head.track_id not in self._snapshots:
                # Keep the captured intent while the source identity/lock/grid
                # check finishes. No preview or write may use an unchecked base.
                self._start_probe(head.track_id)
            else:
                self._start_save(head)
        elif self._probe_wanted is not None and not self._closing:
            self._start_probe()
        self._set_owned()

    def _start_probe(self, tid=None):
        tid = self._probe_wanted if tid is None else tid
        if self._probe_wanted == tid:
            self._probe_wanted = None
        track, lib, identity = self._track(tid), self.shared.lib, self._identity
        if track is None:
            return
        self._active = ('probe', tid)

        def load():
            self.shared._recover_pending(lib)
            snapshot = self._store_for(lib, track).snapshot()
            cues = self.shared._cue_store_factory(lib, tid).snapshot()
            self._validate_snapshot(snapshot, cues, track)
            return snapshot

        def finished(worker):
            self._active = None
            current = self._track(tid)
            affected = {g.track_id for g in self._queue}
            if (identity != self._identity or current is None
                    or current.source_uuid != track.source_uuid
                    or str(current.path) != str(track.path)):
                worker.error = 'Grid source changed while checking; refresh the library'
            if identity == self._identity and current is not None:
                if worker.error:
                    self._errors[tid] = worker.error
                else:
                    preview = self._preview_bases.get(tid)
                    if preview is not None and preview[0] != read_grid(worker.result.dat):
                        worker.error = 'Saved grid changed; refresh the track before editing'
                        self._errors[tid] = worker.error
                    else:
                        self._preview_bases.pop(tid, None)
                    self._snapshots.pop(tid, None)
                    if not worker.error:
                        self._snapshots[tid] = worker.result
                    protected = {g.track_id for g in self._queue} | {self.current}
                    for old_id in list(self._snapshots):
                        if len(self._snapshots) <= 8:
                            break
                        if old_id not in protected:
                            self._snapshots.pop(old_id)
                    if not worker.error:
                        self._errors.pop(tid, None)
                    try:
                        self._view_bytes(tid)  # validate captured edits against the checked base
                    except Exception as exc:
                        worker.error = str(exc)
                        self._errors[tid] = worker.error
            if worker.error and tid in affected:
                self._queue.clear()
                self._probe_wanted = None
                self._debounce.stop()
            self._set_owned()
            for changed_id in affected | {tid}:
                self.changed.emit(changed_id)
            if worker.error and tid in affected:
                self.shared.last_operation = 'grid'
                self.failed.emit(worker.error)
            QTimer.singleShot(0, self._drain)

        if not self.shared.run_grid_job(load, finished):
            self._active = None
            self._probe_wanted = tid

    def _start_save(self, group):
        track, lib, identity = self._track(group.track_id), self.shared.lib, self._identity
        base = self._snapshots[group.track_id]
        self._active = group

        def save():
            if self.prepare_cache is not None:
                self.prepare_cache(group.track_id)
            if track.source_uuid != group.track_uuid or str(track.path) != group.path:
                raise ConflictError('Grid source changed before saving')
            self.shared._recover_pending(lib)
            store = self._store_for(lib, track)
            cue_store = self.shared._cue_store_factory(lib, group.track_id)
            before, cue_before = store.snapshot(), cue_store.snapshot()
            self._validate_snapshot(before, cue_before, track)
            if (before.dat, before.ext) != (base.dat, base.ext):
                raise ConflictError('Saved grid changed; refresh the track before editing')
            request = GridEdit(group.track_id, group.track_uuid, before.revision,
                                group.operation, group.value, tuple(group.nudges))
            store.apply(request)
            after, cues = store.snapshot(), cue_store.snapshot()
            if after.track != cues.track:
                raise ConflictError('Track changed during grid read-back; refresh the library')
            rows = [[b.number, b.time_ms, b.tempo / 100] for b in read_grid(after.dat)]
            if self.cache_dir is not None:
                track_analysis.update_grid_source(self.cache_dir, group.track_id, track.path, rows)
            return after, rekordbox_db.cue_snapshot_fields(cues)

        def finished(worker):
            self._active = None
            self.shared.last_operation = 'grid'
            affected = {g.track_id for g in self._queue}
            current = self._track(group.track_id)
            if not worker.error and (identity != self._identity or current is None
                                     or current.source_uuid != group.track_uuid):
                worker.error = 'Grid source changed while saving; refresh the library'
            if worker.error:
                self._queue.clear()
                self._probe_wanted = None
                self._debounce.stop()
                self._errors[group.track_id] = worker.error
            else:
                after, fields = worker.result
                self._snapshots[group.track_id] = after
                self._queue.pop(0)
                updated = replace(current, bpm=f"{after.track['BPM'] / 100:.2f}", **fields)
                self.shared.lib = replace(self.shared.lib,
                    tracks_by_id={**self.shared.lib.tracks_by_id, group.track_id: updated})
            self._set_owned()
            for tid in affected:
                self.changed.emit(tid)
            if worker.error:
                self.failed.emit(worker.error)
            else:
                self.saved.emit(self.shared.lib, group.track_id)
            QTimer.singleShot(0, self._drain)

        if not self.shared.run_grid_job(save, finished):
            self._active = None

    def shutdown(self):
        self._closing = True
        self._probe_wanted = None
        self._debounce.stop()
        for group in self._queue:
            group.ready = True
        loop = QEventLoop()
        self.shared.idle.connect(loop.quit)
        try:
            self._drain()
            while self._queue or self._active is not None:
                loop.exec(QEventLoop.ExcludeUserInputEvents)
                self._drain()
        finally:
            self.shared.idle.disconnect(loop.quit)
        self._set_owned()
