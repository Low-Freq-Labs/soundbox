"""Selected artwork preparation keeps canonical publication on the GUI owner."""
from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, Signal
from PySide6.QtWidgets import QApplication

from .task_owner import TaskOwner
from .track_info import _edit_identity, _themed_wait_cursor


def track_binding(window, track_id):
    library = window.lib
    track = library.tracks_by_id.get(track_id) if library else None
    return _edit_identity(library, track) if track is not None else None


@dataclass(frozen=True)
class _Request:
    track_id: str
    binding: tuple
    cache: object
    selection: object
    repository: object
    generations: tuple
    token: object
    extra_guard: object

    def binding_current(self, window):
        repository = getattr(window, '_state_repository', None)
        return (window.art_cache is self.cache and repository is self.repository
                and _generations(repository) == self.generations
                and track_binding(window, self.track_id) == self.binding)

    def current(self, owner):
        window = owner.window
        return (not owner.stopping and not window._shutting_down and not window._state_maintenance
                and owner.latest.get(self.track_id) is self.token
                and self.binding_current(window)
                and self.cache.selection_current(self.selection)
                and (self.extra_guard is None or self.extra_guard()))


def _generations(repository):
    return (getattr(repository, 'generation', None),
            getattr(getattr(repository, 'area', None), 'generation', None))


class ArtworkRequests(TaskOwner):
    """Join preparation before publication; latest selected choices always win."""
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.latest = {}
        self.requests = {}

    def start(self, worker, *, guard=None):
        return self._start(worker, (worker.tid,), None, lambda _tid: guard)

    def start_many(self, worker, track_ids, publish, *, guard=None):
        """Publish one prepared image to the independently unchanged targets.

        ``publish(worker, current_ids)`` also settles rejected/failed actions;
        the catalog callback owns its normal UI error and closed-dialog guards.
        """
        return self._start(worker, track_ids, publish,
            lambda tid: (lambda: guard(tid)) if guard is not None else None)

    def _start(self, worker, track_ids, publish, guard_for):
        window = self.window
        if self.stopping or window._shutting_down or window._state_maintenance:
            return False
        bindings = {tid: binding for tid in dict.fromkeys(track_ids)
                    if (binding := track_binding(window, tid)) is not None}
        if not bindings:
            return False
        try:
            selections = window.art_cache.split_selection(
                window.art_cache.selection_token(bindings))
        except ValueError:
            window._flash_status('Artwork target changed; choose the image again', 8)
            return False
        repository = getattr(window, '_state_repository', None)
        requests = tuple(_Request(tid, binding, window.art_cache,
            selections[tid], repository, _generations(repository), object(), guard_for(tid))
            for tid, binding in bindings.items())
        for request in requests:
            self.latest[request.track_id] = request.token
        for previous, previous_requests in tuple(self.requests.items()):
            if all(self.latest.get(old.track_id) is not old.token for old in previous_requests):
                previous.stop()
        self.requests[worker] = requests

        def retired(finished):
            try:
                current_ids = tuple(request.track_id for request in requests if request.current(self))
                if publish is not None:
                    if not window._shutting_down and not window._state_maintenance:
                        publish(finished, current_ids)
                elif current_ids:
                    window._on_oneshot(finished.tid, finished.result,
                        local=finished.local)
                elif (finished.requires_save and not window._shutting_down
                        and not window._state_maintenance
                        and any(self.latest.get(r.track_id) is r.token for r in requests)):
                    window._flash_status('Artwork target changed; choose the image again', 8)
            finally:
                self.requests.pop(finished, None)
                for request in requests:
                    if self.latest.get(request.track_id) is request.token:
                        self.latest.pop(request.track_id)
                window._finish_oneshot(finished, owned=True)
                self.changed.emit()

        self.adopt(worker, retired)
        window._oneshots = [*getattr(window, '_oneshots', ()), worker]
        worker.start()
        return True

    def manual_pending(self, track_id=None):
        return any(worker.requires_save and (track_id is None or any(
            request.track_id == track_id and request.binding_current(self.window)
            and self.latest.get(track_id) is request.token for request in requests))
            for worker, requests in self.requests.items())

    def wait_for_manual(self, track_id):
        """Info's first frame waits only for this song's accepted manual choice."""
        if not self.manual_pending(track_id):
            return
        loop = QEventLoop()
        def changed():
            if not self.manual_pending(track_id):
                loop.quit()
        self.changed.connect(changed)
        QApplication.setOverrideCursor(_themed_wait_cursor())
        try:
            if self.manual_pending(track_id):
                loop.exec(QEventLoop.ExcludeUserInputEvents)
        finally:
            QApplication.restoreOverrideCursor()
            self.changed.disconnect(changed)
