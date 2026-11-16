"""One durable bulk-tag gesture on the existing joined native source lane."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox.rekordbox_tags import SelectedTagTrack, TagWriteRequest

KEY = 'pending_tag_assignments'


def restore_request(raw):
    if (not isinstance(raw, dict) or set(raw) != set(TagWriteRequest.__dataclass_fields__)
            or not isinstance(raw['tracks'], list) or not 1 <= len(raw['tracks']) <= 16_384):
        raise ValueError('Invalid saved tag assignment')
    request = TagWriteRequest(**{**raw, 'tracks': tuple(SelectedTagTrack(**track) for track in raw['tracks'])})
    if (any(type(value) is not str or not value for value in
            (request.library_id, request.tag_id, request.tag_uuid, request.tag_name))
            or type(request.present) is not bool or type(request.operation_id) is not str
            or len(request.operation_id) != 32 or any(c not in '0123456789abcdef' for c in request.operation_id)
            or len({track.track_id for track in request.tracks}) != len(request.tracks)
            or any(type(track.track_id) is not str or not track.track_id
                   or type(track.source_uuid) is not str or not track.source_uuid
                   or type(track.expected_present) is not bool for track in request.tracks)):
        raise ValueError('Invalid saved tag assignment identity')
    return request


def verified_readback(library, source, request):
    if (not library or str(library.source) != source or library.source_kind != 'rekordbox-db'
            or library.source_library_id != request.library_id):
        return False
    definitions = [tag for tag in library.source_tags if tag.tag_id == request.tag_id]
    if (len(definitions) != 1 or definitions[0].source_uuid != request.tag_uuid
            or definitions[0].name != request.tag_name):
        return False
    by_native = {}
    for track in library.tracks_by_id.values():
        by_native.setdefault(track.native_id, []).append(track)
    for target in request.tracks:
        tracks = by_native.get(target.track_id, [])
        if (not tracks or any(track.source_uuid != target.source_uuid or track.source_tag_ids is None
                or (request.tag_id in track.source_tag_ids) != request.present for track in tracks)):
            return False
    return True


class TagAssignmentIntents:
    def _init_tag_intents(self, intent_owner):
        self._tag_intent_owner = intent_owner
        self._tag_waiting = False
        self._tag_timer = QTimer(self)
        self._tag_timer.setSingleShot(True)
        self._tag_timer.timeout.connect(self._pump_tag_intent)
        self.idle.connect(self._schedule_tag_intent)

    @property
    def pending(self):
        return self._tag_intent_owner.settings.get(KEY, []) if self._tag_intent_owner else []

    def queue_error(self):
        try:
            if not isinstance(self.pending, list) or len(self.pending) > 1:
                raise ValueError('Invalid queue')
            for job in self.pending:
                if (not isinstance(job, dict) or set(job) != {'source', 'library_id', 'request', 'error'}
                        or type(job['source']) is not str or not Path(job['source']).is_absolute()
                        or type(job['error']) is not str or not isinstance(job['library_id'], str)):
                    raise ValueError('Invalid intent')
                request = restore_request(job['request'])
                if request.library_id != job['library_id']:
                    raise ValueError('Invalid binding')
        except (ValueError, KeyError, TypeError):
            return 'Saved My Tag assignment needs attention; intention preserved'
        return ''

    @property
    def assignment_reserved(self):
        if (self._closing or getattr(self._tag_intent_owner,'_state_maintenance',False)
                or getattr(self._tag_intent_owner,'_shutting_down',False)):
            return False
        return bool(self.lib and self.verified and not self.queue_error() and self.pending
                    and not self.pending[0]['error'] and (str(self.lib.source), self.lib.source_library_id)
                    == (self.pending[0]['source'], self.pending[0]['library_id']))

    def _persist_tag_intent(self, jobs):
        owner = self._tag_intent_owner
        if owner is None:
            return True  # Explicit fixture factories retain their direct test API.
        missing = object();previous = owner.settings.get(KEY, missing)
        owner.settings[KEY] = jobs
        try:
            if not owner._save_state():
                raise OSError('My Tag intention could not be saved')
        except (OSError, ValueError, RuntimeError):
            if previous is missing:owner.settings.pop(KEY, None)
            else:owner.settings[KEY] = previous
            return False
        return True

    def _queue_tag_request(self, request):
        job = dict(source=str(self.lib.source), library_id=request.library_id,
                   request=asdict(request), error='')
        job['request']['tracks'] = [asdict(track) for track in request.tracks]
        try:restore_request(job['request'])
        except (ValueError,TypeError,KeyError) as exc:
            self.failed.emit(str(exc));return False
        if not self._persist_tag_intent([deepcopy(job)]):
            return False
        self._pending = request
        self.changed.emit()
        self._pump_tag_intent()
        return True

    def _schedule_tag_intent(self):
        if self.pending and not self._closing and not self._tag_timer.isActive():
            self._tag_timer.start(0)

    def _pump_tag_intent(self):
        if self._closing or not self.pending or self.queue_error() or self.pending[0]['error']:
            return
        if (self.busy or self.checking or self.prep_priority or self.admission_paused
                or self._analysis_blocks_source() or self.owner.stopping
                or getattr(self._tag_intent_owner,'_state_maintenance',False)
                or getattr(self._tag_intent_owner,'_shutting_down',False)
                or getattr(self._tag_intent_owner,'_refresh_worker',None) is not None
                or getattr(self._tag_intent_owner,'_device_operation_owned',lambda:False)()):
            self._tag_timer.start(500)
            return
        lib = self.lib;job = deepcopy(self.pending[0])
        if not lib or not self.verified or (str(lib.source), lib.source_library_id) != (job['source'], job['library_id']):
            return
        if self._requires_authority and self._authority is None:
            return
        request = restore_request(job['request'])
        self._tag_waiting = False
        self._pending = request
        self._start_tag_request(lib, request, lambda worker:self._finish_tag_intent(worker, job))

    def _finish_tag_intent(self, worker, job):
        self.busy = False;self._busy_kind = '';self.last_operation = 'tags'
        if not self.pending or self.pending[0] != job:
            self._pending = None;self.idle.emit()
            return
        if isinstance(worker.result, dict) and worker.result.get('waiting') and not worker.error:
            self._tag_waiting = True
            if not self._closing:self._tag_timer.start(2000)
            self.changed.emit()
            return
        request = restore_request(job['request'])
        library = worker.result
        if not worker.error and not verified_readback(library, job['source'], request):
            worker.error = 'My Tag source readback differs from the saved intention; retry to reconcile'
        if not worker.error and (not self.lib or (str(self.lib.source), self.lib.source_library_id)
                                 != (job['source'], job['library_id'])):
            worker.error = 'My Tags saved in the original library; switch back to acknowledge'
        if not worker.error and not self._persist_tag_intent([]):
            worker.error = 'My Tags saved in rekordbox; local acknowledgement failed. Retry after fixing storage.'
        self._pending = None
        if worker.error:
            failed = {**job, 'error': worker.error[:4096]}
            if not self._persist_tag_intent([failed]) and self._tag_intent_owner:
                self._tag_intent_owner.settings[KEY] = [failed]
            self._ready = False;self._probe_error = worker.error
            if not self.owner.stopping:self.failed.emit(worker.error)
        else:
            self._ready = True;self._probe_error = ''
            if not self.owner.stopping and not self._closing:self.saved.emit(library)
        self.changed.emit();self.idle.emit()

    def retry(self):
        if self._closing or self.busy or self.checking or not self.pending or self.queue_error():
            return False
        if not self._persist_tag_intent([{**self.pending[0], 'error': ''}]):
            return False
        self._schedule_tag_intent()
        return True

    def discard(self):
        if self._closing or self.busy or self.checking or not self.pending or self.queue_error():
            return False
        if QMessageBox.question(self.parent(), 'Discard pending My Tag assignment',
                'Discard this pending intention? Assignments already saved in rekordbox remain saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        if not self._persist_tag_intent([]):return False
        self._pending = None;self._tag_timer.stop();self.changed.emit();self.idle.emit()
        return True

    @staticmethod
    def new_operation_id():
        return uuid4().hex
