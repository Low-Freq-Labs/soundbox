"""Fresh reviewed-queue admission after a destructive confirmation dialog."""
from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class _Review:
    jobs: list
    source: tuple | None
    epoch: tuple


def _source(window):
    library=window.lib
    return ((str(library.source),library.source_kind,library.source_library_id)
            if library is not None else None)


def _epoch(window):
    repository=getattr(window,'_state_repository',None)
    return (repository,getattr(repository,'generation',None),
            getattr(getattr(repository,'area',None),'generation',None))


def _idle(controller):
    window=controller.window
    owned=controller.tags.owned
    ack=getattr(controller,'ack',None)
    if ack is not None and ack.error:
        only=getattr(controller.tags,'source_ack_only',None)
        if callable(only) and only(ack.token):owned=False
    return (not controller.closing and not window._shutting_down
            and not window._state_maintenance and not owned
            and not getattr(window,'_state_publication',False)
            and getattr(window,'_refresh_worker',None) is None
            and bool(controller.pending) and not controller.queue_error())


def capture_discard(controller):
    """Capture only reviewed intents; hold no source/state lock during input."""
    if not _idle(controller):return None
    return _Review(deepcopy(controller.pending),_source(controller.window),_epoch(controller.window))


def discard_is_current(controller,review):
    """A modal event loop grants no permission over a newer queue or owner."""
    return (_idle(controller) and _source(controller.window)==review.source
            and _epoch(controller.window)==review.epoch and controller.pending==review.jobs)
