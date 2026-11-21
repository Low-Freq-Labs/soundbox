"""Immediate exact-selection tag gestures with ordered background admission."""
from collections import deque
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, QEventLoop, QTimer

from soundbox.state_io import StateChangedError


class TagStateLane(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.queue = deque()
        self.failed = []
        self.active = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        controller.idle.connect(self.schedule)
        controller.changed.connect(self.schedule)

    @property
    def enabled(self):
        return callable(getattr(self.controller._tag_intent_owner, '_commit_tag_state', None))

    @property
    def pending(self):
        return bool(self.queue) or self.active

    @property
    def error(self):
        return str(self.failed[0][1]) if self.failed else ''

    def _bindings(self, ids, lib=None):
        lib = lib or self.controller.lib
        return tuple((tid, track.native_id, track.source_uuid, str(track.path), str(track.native_path))
                     for tid in ids if (track := lib.tracks_by_id.get(tid)) is not None)

    def _bound(self, row):
        c = self.controller
        lib = c.lib
        definitions=getattr(c._tag_intent_owner,'tag_definition_controller',None)
        available=getattr(definitions,'available_tags',None)
        tags=available(lib.source_tags) if lib and callable(available) else lib.source_tags if lib else ()
        tag = next((tag for tag in tags if tag.tag_id == row['request'].tag_id), None)
        dependency=row['job'].get('tag_creation_dependency')
        pending=getattr(definitions,'pending_creation',lambda *_args:None)(row['request'].tag_id,row['request'].tag_uuid)
        dependency_current=(dependency is None and pending is None or dependency is not None and
            (pending is None or getattr(definitions,'creation_dependency',lambda *_args:None)(
                row['request'].tag_id,row['request'].tag_uuid)==dependency))
        return bool(c._tag_state_epoch() == row['epoch']
            and c._tag_source_current(row['job'])
            and dependency_current
            and (dependency is None or definitions is not None and not definitions.queue_error())
            and tag is not None and (tag.source_uuid, tag.name) ==
                (row['request'].tag_uuid, row['request'].tag_name)
            and (dependency is None or (tag.category_id,tag.category_uuid)==
                 (dependency['category_id'],dependency['category_uuid']))
            and self._bindings(row['ids']) == row['bindings']
            and self._bindings(row['ids'], getattr(c._tag_intent_owner, 'lib', lib)) == row['bindings'])

    def admit(self, job, request, assignment, ids):
        if len(self.queue) + len(self.controller.pending) >= 256 or self.failed:
            return False
        row = dict(job=job, request=request, assignment=assignment, ids=tuple(ids),
                   bindings=self._bindings(ids), epoch=self.controller._tag_state_epoch())
        self.queue.append(row)
        self.controller.changed.emit()
        self.schedule()
        return True

    def requests(self):
        rows = [*(row for row, _error in self.failed), *self.queue]
        return tuple(row['request'] for row in rows if self._bound(row))

    def schedule(self):
        if self.queue and not self.active and not self.failed:
            self.timer.start(0)

    def pump(self):
        from .tag_assignment_intents import KEY
        c = self.controller
        if self.active or not self.queue or self.failed:
            return
        owner = c._tag_intent_owner
        definitions=getattr(owner,'tag_definition_controller',None)
        for row in self.queue:
            dependency=row['job'].get('tag_creation_dependency')
            if dependency is None or not self._bound(row):continue
            pending=getattr(definitions,'pending_creation',lambda *_args:None)(dependency['tag_id'],dependency['tag_uuid'])
            if pending is not None and pending['error']:
                error=OSError('New tag creation needs attention — '+pending['error'])
                self.failed.extend((queued,error) for queued in self.queue);self.queue.clear()
                c.failed.emit('My Tag intentions could not be saved: '+str(error));c.changed.emit();c.idle.emit()
                return
        if (c.busy or c.checking or c.state_commit_owned or c.tag_ack_pending or c.source_ack_pending
                or getattr(owner, '_state_publication', False)):
            self.timer.start(25)
            return
        rows = tuple(self.queue)
        previous = owner.settings.get(KEY)
        def current():
            return owner.settings.get(KEY) is previous and all(self._bound(row) for row in rows)
        def failed(error):
            self.active = False
            self.failed.extend((row, error) for row in self.queue)
            self.queue.clear()
            c.failed.emit('My Tag intentions could not be saved: ' + str(error))
            c.changed.emit(); c.idle.emit()
        if not current():
            failed(StateChangedError('Library or selected tag/song identities changed before saving'))
            return
        for row in rows:
            dependency=row['job'].get('tag_creation_dependency')
            if dependency is None:continue
            pending=getattr(definitions,'pending_creation',lambda *_args:None)(dependency['tag_id'],dependency['tag_uuid'])
            if pending is not None and pending['error']:
                failed(OSError('New tag creation needs attention — '+pending['error']));return
            if pending is not None and not definitions.creation_durable(dependency):
                definitions.admission.schedule();self.timer.start(25);return
        jobs = [*(previous or []), *(deepcopy(row['job']) for row in rows)]
        def committed():
            self.active = False
            for row in rows:
                if not self.queue or self.queue[0] is not row:
                    failed(StateChangedError('My Tag gesture order changed before publication'))
                    return
                self.queue.popleft()
                c._tag_recency_assignments[row['request'].operation_id] = row['assignment']
            c._tag_ack_conflict = False
            c.changed.emit()
            self.schedule(); c._schedule_tag_intent()
        self.active = True
        try:
            accepted = owner._commit_tag_state({('settings', KEY): jobs}, (), committed, failed,
                                               current=current)
        except (OSError, ValueError, RuntimeError) as exc:
            failed(exc)
            return
        if not accepted:
            self.active = False
            self.timer.start(25)

    def retry(self):
        if self.pending or not self.failed:
            return False
        if any(isinstance(error, StateChangedError) or getattr(error, 'durable', False)
               for _row, error in self.failed):
            return False
        self.queue.extend(row for row, _error in self.failed)
        self.failed.clear()
        self.controller.changed.emit(); self.schedule()
        return True

    def discard(self):
        from PySide6.QtWidgets import QMessageBox
        from soundbox.state_repository import read_state
        from .tag_assignment_intents import KEY, validate_job
        c = self.controller; owner = c._tag_intent_owner
        captured = tuple(self.failed); epoch = c._tag_state_epoch()
        def current():
            return bool(captured and tuple(self.failed) == captured and c._tag_state_epoch() == epoch
                and not self.active and not self.queue and not c.busy and not c.checking
                and not c._closing and not c.owner.stopping
                and not c.tag_ack_pending and not c.state_commit_owned
                and not getattr(owner, '_state_publication', False)
                and not getattr(owner, '_state_maintenance', False)
                and not getattr(owner, '_shutting_down', False)
                and getattr(owner, '_refresh_worker', None) is None)
        if not current():return False
        if QMessageBox.question(c.parent(), 'Discard unsaved My Tag clicks',
                'Discard these unsaved tag clicks? Assignments already saved in rekordbox remain saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        if not current():return False
        repository = getattr(owner, '_state_repository', None)
        try:
            queue = owner.settings.get(KEY, [])
            if repository is not None and hasattr(repository, 'path'):
                with repository.area.lock:
                    if not current() or repository.area.journal.exists() or repository.recovery is not None:
                        return False
                    queue = read_state(repository.path).get('settings', {}).get(KEY, [])
                    if not current():return False
            if not isinstance(queue, list) or len(queue) > 256:
                raise ValueError('Saved My Tag queue needs attention')
            for job in queue:
                request = validate_job(job)
                if request.library_id != job['library_id'] or not Path(job['source']).is_absolute():
                    raise ValueError('Saved My Tag queue binding needs attention')
            owner.settings[KEY] = queue
        except (OSError, ValueError, TypeError, KeyError) as exc:
            c.failed.emit('Saved tag intentions were retained: ' + str(exc))
            return False
        self.failed.clear()
        c.changed.emit(); c.idle.emit()
        return True

    def drain(self):
        if not self.pending or self.failed:
            return
        loop = QEventLoop()
        timer = QTimer(); timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if not self.pending or self.failed else self.schedule())
        timer.start(); self.schedule()
        loop.exec(QEventLoop.ExcludeUserInputEvents)
        timer.stop()
