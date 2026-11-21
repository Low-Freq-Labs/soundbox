"""In-memory native-command timings; no paths, musical data or job-wire fields."""
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count
import logging
import re
import time

# Production installs handlers on ``soundbox``; timings must inherit that route.
log=logging.getLogger('soundbox.native_timing')
_ACTIVE=ContextVar('soundbox_native_command_timing',default=None)
_TOKENS=count(1)


def now():
    return time.monotonic()


def _label(value):
    if type(value) is not str or re.fullmatch('[a-z][a-z0-9_-]{0,47}',value) is None:
        raise ValueError('Native timing labels must be fixed identifiers')
    return value


@contextmanager
def phase(label):
    """Time a fixed phase within the current worker or GUI acknowledgement."""
    label=_label(label);command=_ACTIVE.get()
    if command is None:
        yield
        return
    started=now();outcome='returned'
    try:
        yield
    except BaseException:
        outcome='raised';raise
    finally:
        log.info('Native command phase: kind=%s token=%s phase=%s seconds=%.3f outcome=%s',
                 command.kind,command.token,label,now()-started,outcome)


class CommandTiming:
    def __init__(self,kind,*,admitted_at=None,enqueued_at=None):
        self.kind=_label(kind);self.token=f'{next(_TOKENS):08x}'
        self.admitted_at=now() if admitted_at is None else admitted_at
        self.enqueued_at=enqueued_at
        self.started_at=None;self.finished_at=None

    @contextmanager
    def execution(self,outcome):
        self.started_at=now();token=_ACTIVE.set(self);raised=False
        try:
            yield
        except BaseException:
            raised=True;raise
        finally:
            self.finished_at=now()
            try:
                intent_wait=('unknown' if self.enqueued_at is None else
                             f'{self.admitted_at-self.enqueued_at:.3f}')
                log.info('Native command: kind=%s token=%s seconds=%.3f dispatch_wait_seconds=%.3f '
                         'intent_wait_seconds=%s outcome=%s',self.kind,self.token,
                         self.finished_at-self.started_at,self.started_at-self.admitted_at,
                         intent_wait,'raised' if raised else outcome())
            finally:
                _ACTIVE.reset(token)

    def acknowledge(self,callback,worker):
        """Preserve the owner's callback exceptions and finished/join lifetime."""
        started=now();token=_ACTIVE.set(self);outcome='returned'
        try:
            return callback(worker)
        except BaseException:
            outcome='raised';raise
        finally:
            finished=now()
            try:
                gui_wait=('unknown' if self.finished_at is None else f'{started-self.finished_at:.3f}')
                log.info('Native command acknowledgement: kind=%s token=%s gui_wait_seconds=%s '
                         'callback_seconds=%.3f admitted_seconds=%.3f outcome=%s',
                         self.kind,self.token,gui_wait,finished-started,finished-self.admitted_at,outcome)
            finally:
                _ACTIVE.reset(token)

    def publication(self,callback):
        """Time final durable GUI publication independently of dispatch ACK."""
        started=now();token=_ACTIVE.set(self);outcome='returned'
        try:
            return callback()
        except BaseException:
            outcome='raised';raise
        finally:
            finished=now()
            try:
                wait=('unknown' if self.finished_at is None else f'{started-self.finished_at:.3f}')
                log.info('Native command publication: kind=%s token=%s post_worker_seconds=%s '
                         'publication_seconds=%.3f admitted_seconds=%.3f outcome=%s',
                         self.kind,self.token,wait,finished-started,finished-self.admitted_at,outcome)
            finally:
                _ACTIVE.reset(token)
