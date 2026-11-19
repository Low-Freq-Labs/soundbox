"""Cooperative cancellation scoped to a worker, disabled during recovery."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from typing import Callable, Iterator


class CommittedOperationError(RuntimeError):
    committed = True

    def __init__(self, message: str, result: object = None):
        super().__init__(message)
        self.result = result


class OperationCancelled(RuntimeError):
    pass


_progress: ContextVar[Callable[[str, int, int, str], None] | None] = ContextVar("device_progress", default=None)


_metrics: ContextVar[dict[str, int] | None] = ContextVar("device_metrics", default=None)


_stop: ContextVar[Event | None] = ContextVar("device_stop", default=None)


@contextmanager
def operation_scope(event: Event | None, progress: Callable[[str, int, int, str], None] | None = None) -> Iterator[None]:
    metrics_token = _metrics.set({})
    token = _stop.set(event)
    progress_token = _progress.set(progress)
    try:
        yield
    finally:
        metrics = _metrics.get()
        if metrics:
            logging.getLogger("soundbox").info("Device operation metrics: %s", metrics)
        _metrics.reset(metrics_token)
        _stop.reset(token)
        _progress.reset(progress_token)


def checkpoint() -> None:
    event = _stop.get()
    if event is not None and event.is_set():
        raise OperationCancelled("Operation cancelled at a safe boundary")


def cancellation_enabled() -> bool:
    """Whether this operation has a cooperative cancellation owner."""
    return _stop.get() is not None


@contextmanager
def cancellation_disabled() -> Iterator[None]:
    """Finish a commit/recovery boundary without losing its progress or metrics."""
    token = _stop.set(None)
    try:
        yield
    finally:
        _stop.reset(token)


def phase_progress(phase: str, done: int, total: int, label: str) -> None:
    callback = _progress.get()
    if callback is not None:
        callback(phase, done, total, label)


def record_metric(name: str, value: int = 1, *, peak: bool = False) -> None:
    metrics = _metrics.get()
    if metrics is not None:
        previous = metrics.get(name, 0)
        metrics[name] = max(previous, value) if peak else previous + value
