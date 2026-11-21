"""One durable bulk-tag gesture on the existing joined native source lane."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox.rekordbox_tags import SelectedTagTrack, TagWriteRequest
from soundbox import smart_playlist_order

KEY = 'pending_tag_assignments'
_DEPENDENCY_FIELDS={'source','library_id','operation_id','tag_id','tag_uuid','name','category_id','category_uuid'}


def validate_job(job):
    """Legacy jobs stay valid; future definitions retain their own operation."""
    if (not isinstance(job,dict) or set(job) not in (
            {'source','library_id','request','error'},
            {'source','library_id','request','error','tag_creation_dependency'})
            or type(job['source']) is not str or not Path(job['source']).is_absolute()
            or type(job['error']) is not str or type(job['library_id']) is not str):
        raise ValueError('Invalid tag assignment intention')
    request=restore_request(job['request'])
    if request.library_id!=job['library_id']:raise ValueError('Invalid tag assignment source binding')
    dependency=job.get('tag_creation_dependency')
    if 'tag_creation_dependency' in job:
        if (not isinstance(dependency,dict) or set(dependency)!=_DEPENDENCY_FIELDS
                or any(type(value) is not str or not value for value in dependency.values())
                or (dependency['source'],dependency['library_id'],dependency['tag_id'],dependency['tag_uuid'],dependency['name'])
                    !=(job['source'],job['library_id'],request.tag_id,request.tag_uuid,request.tag_name)
                or len(dependency['operation_id'])!=32
                or any(c not in '0123456789abcdef' for c in dependency['operation_id'])
                or dependency['operation_id']==request.operation_id):
            raise ValueError('Invalid new-tag dependency identity')
    return request


@dataclass(frozen=True)
class _VerifiedTagReadback:
    # Private unpublished worker result, consumed once before GUI publication.
    # All native aliases were verified; this is not a published-Library cache.
    library: object
    source: str
    request: TagWriteRequest
    promotions: smart_playlist_order.Promotions | None


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
        self._tag_recency_assignment = None
        self._tag_recency_assignments = {}
        self._tag_ack = None
        self._tag_ack_inflight = False
        self._tag_ack_conflict = False
        self.order_error = ''
        self._tag_timer = QTimer(self)
        self._tag_timer.setSingleShot(True)
        self._tag_timer.timeout.connect(self._pump_tag_intent)
        self.idle.connect(self._schedule_tag_intent)
        from .tag_state import TagStateLane
        self.tag_state_lane = TagStateLane(self)

    @property
    def pending(self):
        return self._tag_intent_owner.settings.get(KEY, []) if self._tag_intent_owner else []

    @property
    def durable_assignment_ack(self):
        return callable(getattr(self._tag_intent_owner, '_commit_tag_state', None))

    @property
    def tag_ack_pending(self):
        return self._tag_ack is not None or self._tag_ack_inflight

    def _tag_source_current(self, job):
        libraries = (self.lib, getattr(self._tag_intent_owner, 'lib', self.lib))
        return all(lib is not None and lib.source_kind == 'rekordbox-db'
                   and (str(lib.source), lib.source_library_id) == (job['source'], job['library_id'])
                   for lib in libraries)

    def _tag_state_epoch(self):
        repository = getattr(self._tag_intent_owner, '_state_repository', None)
        return (repository, repository.generation, repository.area.generation) if repository is not None else None

    def queue_error(self):
        try:
            if not isinstance(self.pending, list) or len(self.pending) > 256:
                raise ValueError('Invalid queue')
            for job in self.pending:
                validate_job(job)
        except (ValueError, KeyError, TypeError):
            return 'Saved My Tag assignment needs attention; intention preserved'
        return ''

    @property
    def assignment_owned(self):
        if (self._closing or getattr(self._tag_intent_owner,'_state_maintenance',False)
                or getattr(self._tag_intent_owner,'_shutting_down',False)):
            return False
        return bool(self.lib and self.verified and not self.queue_error() and self.pending
                    and not self._tag_ack_conflict and not self.pending[0]['error'] and (str(self.lib.source), self.lib.source_library_id)
                    == (self.pending[0]['source'], self.pending[0]['library_id']))

    @property
    def assignment_reserved(self):
        return self.assignment_owned and self._tag_definition_state(self.pending[0])[0]=='ready'

    def _tag_definition_state(self,job):
        """Return ready/waiting/error without borrowing a future native row."""
        request=restore_request(job['request']);dependency=job.get('tag_creation_dependency')
        definitions=getattr(self._tag_intent_owner,'tag_definition_controller',None)
        if dependency is not None and (definitions is None or getattr(definitions,'queue_error',lambda:'')()):
            return 'error','The saved new-tag creation needs attention; assignment intention retained'
        pending=getattr(definitions,'pending_creation',lambda *_args:None)(request.tag_id,request.tag_uuid)
        if pending is not None:
            captured=getattr(definitions,'creation_dependency',lambda *_args:None)(request.tag_id,request.tag_uuid)
            if dependency is None or captured!=dependency:
                return 'error','The new tag creation identity changed; assignment intention retained'
            if pending['error']:
                return 'error','New tag creation needs attention — '+pending['error']
            return 'waiting','Creating the new tag before saving its assignments'
        if dependency is None:return 'ready',''
        libraries=(self.lib,getattr(self._tag_intent_owner,'lib',self.lib))
        if all(library is not None and any((tag.tag_id,tag.source_uuid,tag.name,tag.category_id,tag.category_uuid)==
                tuple(dependency[key] for key in ('tag_id','tag_uuid','name','category_id','category_uuid'))
                for tag in library.source_tags)
               for library in libraries):return 'ready',''
        return 'error','The selected tag is unavailable; assignment intention retained'

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

    def _queue_tag_request(self, request, assignment=None, ids=()):
        job = dict(source=str(self.lib.source), library_id=request.library_id,
                   request=asdict(request), error='')
        job['request']['tracks'] = [asdict(track) for track in request.tracks]
        definitions=getattr(self._tag_intent_owner,'tag_definition_controller',None)
        dependency=getattr(definitions,'creation_dependency',lambda *_args:None)(request.tag_id,request.tag_uuid)
        if dependency is not None:job['tag_creation_dependency']=dependency
        try:validate_job(job)
        except (ValueError,TypeError,KeyError) as exc:
            self.failed.emit(str(exc));return False
        if self.tag_state_lane.enabled:
            return self.tag_state_lane.admit(job, request, assignment, ids)
        if not self._persist_tag_intent([deepcopy(job)]):
            return False
        self._tag_recency_assignment = (request.operation_id, assignment)
        self._tag_ack_conflict = False
        self._pending = request
        self.changed.emit()
        self._pump_tag_intent()
        return True

    def _schedule_tag_intent(self):
        if self.pending and (not self._closing or self._tag_ack is not None) and not self._tag_timer.isActive():
            self._tag_timer.start(0)

    def _pump_tag_intent(self):
        from .tag_controller import device_blocks_library_work
        if self._tag_ack is not None:
            self._try_tag_ack()
            return
        if self.tag_state_lane.pending and not self._closing:
            self.tag_state_lane.schedule()
            return
        if self._closing or self._tag_ack_conflict or not self.pending or self.queue_error() or self.pending[0]['error']:
            return
        if not self._tag_source_current(self.pending[0]):return
        state,message=self._tag_definition_state(self.pending[0])
        if state=='waiting':
            definitions=getattr(self._tag_intent_owner,'tag_definition_controller',None)
            if definitions is not None:definitions.schedule()
            self._tag_timer.start(100)
            return
        if state=='error':
            self._tag_ack=(deepcopy(self.pending[0]),None,None,message,self._tag_state_epoch(),None)
            self._try_tag_ack()
            return
        if (self.busy or self.checking or self.prep_priority or self.admission_paused
                or getattr(self,'source_ack_pending',False)
                or getattr(self,'state_commit_owned',False)
                or self._analysis_blocks_source() or self.owner.stopping
                or getattr(self._tag_intent_owner,'_state_maintenance',False)
                or getattr(self._tag_intent_owner,'_shutting_down',False)
                or getattr(self._tag_intent_owner,'_refresh_worker',None) is not None
                or self._tag_intent_owner is not None and device_blocks_library_work(self._tag_intent_owner)):
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
        promotions = None
        if isinstance(library, _VerifiedTagReadback):
            worker.result = None  # Consume the unpublished proof before any callbacks.
            proof = library;library = proof.library
            valid = (proof.source == job['source'] and proof.request == request
                     and library.source_kind == 'rekordbox-db'
                     and (str(library.source), library.source_library_id) == (job['source'], job['library_id']))
            promotions = proof.promotions
        else:
            valid = verified_readback(library, job['source'], request) if not worker.error else False
        if not worker.error and not valid:
            worker.error = 'My Tag source readback differs from the saved intention; retry to reconcile'
        if not worker.error and not self._tag_source_current(job):
            worker.error = 'My Tags saved in the original library; switch back to acknowledge'
        if self.durable_assignment_ack:
            self._tag_ack = (job, library, promotions, worker.error, self._tag_state_epoch(), getattr(worker, 'timing', None))
            self._try_tag_ack()
            return
        if not worker.error and not self._persist_tag_intent(self.pending[1:]):
            worker.error = 'My Tags saved in rekordbox; local acknowledgement failed. Retry after fixing storage.'
        self._pending = None
        if worker.error:
            failed = {**job, 'error': worker.error[:4096]}
            remaining = [failed, *self.pending[1:]]
            if not self._persist_tag_intent(remaining) and self._tag_intent_owner:
                self._tag_intent_owner.settings[KEY] = remaining
            self._ready = False;self._probe_error = worker.error
            if not self.owner.stopping:self.failed.emit(worker.error)
        else:
            self._ready = True;self._probe_error = ''
            if not self.owner.stopping and not self._closing:self.saved.emit(library)
        self.changed.emit();self.idle.emit()

    def _try_tag_ack(self):
        """One ordered queue/order delta; admission never publishes completion."""
        if self._tag_ack is None or self._tag_ack_inflight:
            return
        job, library, promotions, error, epoch, timing = self._tag_ack
        if not self.pending or self.pending[0] != job:
            self._tag_ack = None;self._pending = None
            self.changed.emit();self.idle.emit()
            return
        if not error and not self._tag_source_current(job):
            error = 'My Tags saved in the original library; switch back to acknowledge'
        owner = self._tag_intent_owner
        tail = self.pending[1:]
        replacements = {('settings', KEY): [{**job, 'error': error[:4096]}, *tail] if error else tail}
        self.order_error = ''
        previous = owner.settings.get(smart_playlist_order.KEY)
        order_pins = None
        if not error:
            try:
                after = smart_playlist_order.record_prepared(previous, promotions)
                if after is not previous:
                    if previous is None:
                        replacements[('settings', smart_playlist_order.KEY)] = after
                    else:
                        replacements[('settings', smart_playlist_order.KEY, 'sequence')] = after['sequence']
                        for scope, uuids in promotions.ranks:
                            for uuid in uuids:
                                replacements[('settings', smart_playlist_order.KEY, 'playlists', scope, uuid)] = after['sequence']
                    # Exact affected scalar pins catch in-place edits as well as
                    # owner replacement; unrelated newer preferences merge.
                    order_pins = None if previous is None else (
                        previous['version'], previous['sequence'],
                        tuple((scope, uuid, previous['playlists'].get(scope, {}).get(uuid))
                              for scope, uuids in promotions.ranks for uuid in uuids))
            except ValueError as exc:
                self.order_error = 'Tags saved; ' + str(exc)
        has_order = any(path[:2] == ('settings', smart_playlist_order.KEY) for path in replacements)
        def current():
            if self._tag_state_epoch() != epoch:
                return False
            if not self.pending or self.pending[0] != job:
                return False
            if not error and not self._tag_source_current(job):
                return False
            if not has_order:
                return True
            value = owner.settings.get(smart_playlist_order.KEY)
            if value is not previous:
                return False
            if order_pins is None:
                return value is None
            try:
                version, sequence, ranks = order_pins
                return (type(value['version']) is int and value['version'] == version
                        and type(value['sequence']) is int and value['sequence'] == sequence
                        and all(type(value['playlists'].get(scope, {}).get(uuid)) is type(rank)
                                and value['playlists'].get(scope, {}).get(uuid) == rank
                                for scope, uuid, rank in ranks))
            except (KeyError, TypeError, AttributeError):
                return False
        self._tag_ack_inflight = True
        def finish(failure=None):
            self._tag_ack_inflight = False;self._tag_ack = None;self._pending = None
            message = error
            if failure is not None:
                self._tag_ack_conflict = True
                if error:
                    message = error + ('; newer local state was retained' if getattr(failure, 'durable', False)
                                       else '; the local failure note could not be saved. Intention retained.')
                else:
                    message = ('My Tags saved in rekordbox; ' + str(failure) if getattr(failure, 'durable', False)
                               else 'My Tags saved in rekordbox; local acknowledgement failed. Retry after fixing storage.')
                # The canonical request is retained on storage failure. Do not
                # persist a second full document, or overwrite a newer RAM head.
                if self._tag_state_epoch() == epoch and self.pending and self.pending[0] == job:
                    owner.settings[KEY] = [{**job, 'error': message[:4096]}, *self.pending[1:]]
            if message:
                self._ready = False;self._probe_error = message
                if not self.owner.stopping and not self._closing:self.failed.emit(message)
            else:
                self._ready = True;self._probe_error = ''
                self._tag_recency_assignment = None
                self._tag_recency_assignments.pop(job['request']['operation_id'], None)
                if not self.owner.stopping and not self._closing:
                    from .native_timing import phase
                    def publish():
                        with phase('publish_library'):
                            self.saved.emit(library)
                    if timing is None:publish()
                    else:timing.publication(publish)
            self.changed.emit();self.idle.emit()
        try:
            accepted = owner._commit_tag_state(replacements, (), lambda:finish(), finish, current=current)
        except (OSError, ValueError, RuntimeError) as exc:
            finish(exc);return
        if not accepted:
            self._tag_ack_inflight = False
            if not self._closing:self._tag_timer.start(500)

    def retry(self):
        if self.tag_state_lane.failed:
            return self.tag_state_lane.retry()
        if self._closing or self.busy or self.checking or self.tag_ack_pending or not self.pending or self.queue_error():
            return False
        if not self._persist_tag_intent([{**self.pending[0], 'error': ''}, *self.pending[1:]]):
            return False
        self._tag_ack_conflict = False
        self._schedule_tag_intent()
        return True

    def discard(self):
        if self.tag_state_lane.failed:
            return self.tag_state_lane.discard()
        if self._closing or self.busy or self.checking or self.tag_ack_pending or not self.pending or self.queue_error():
            return False
        reviewed = deepcopy(self.pending);epoch = self._tag_state_epoch()
        def current():
            owner = self._tag_intent_owner
            return (not self._closing and not self.owner.stopping and not self.busy and not self.checking
                    and not self.tag_ack_pending and not self.state_commit_owned
                    and not getattr(owner, '_state_maintenance', False)
                    and not getattr(owner, '_shutting_down', False)
                    and not getattr(owner, '_state_publication', False)
                    and getattr(owner, '_refresh_worker', None) is None
                    and self._tag_state_epoch() == epoch and self.pending == reviewed
                    and self._tag_source_current(reviewed[0]))
        if not current():return False
        if QMessageBox.question(self.parent(), 'Discard pending My Tag assignment',
                'Discard this pending intention? Assignments already saved in rekordbox remain saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        if not current():return False
        if not self._persist_tag_intent([]):return False
        self._tag_ack_conflict = False
        self._pending = None;self._tag_timer.stop();self.changed.emit();self.idle.emit()
        return True

    @staticmethod
    def new_operation_id():
        return uuid4().hex
