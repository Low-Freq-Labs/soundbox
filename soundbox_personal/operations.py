"""Bound one provider operation without caching results or owning workers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import time
import socket
import operator
from typing import Callable, Iterator, ParamSpec, TypeVar

from soundbox.providers import ConnectionFailure


class ProviderCancelled(ConnectionFailure):
    pass


class ProviderDeadline(ConnectionFailure):
    pass


@dataclass(frozen=True)
class _Operation:
    should_cancel: Callable[[], bool]
    deadline: float


_current: ContextVar[_Operation | None] = ContextVar('provider_operation',default=None)
_P = ParamSpec('_P')
_R = TypeVar('_R')


def checkpoint():
    operation = _current.get()
    if operation is None:
        return
    if operation.should_cancel():
        raise ProviderCancelled('Artwork lookup cancelled')
    if time.monotonic() >= operation.deadline:
        raise ProviderDeadline('Artwork lookup timed out; retry when the connection improves')


def request_timeout(default):
    checkpoint()
    operation = _current.get()
    if operation is None:
        return default
    remaining = operation.deadline-time.monotonic()
    if remaining <= 0:
        raise ProviderDeadline('Artwork lookup timed out; retry when the connection improves')
    return min(default,remaining)


@contextmanager
def operation_scope(should_cancel=lambda:False, *, timeout=60.0) -> Iterator[None]:
    previous = _current.get()
    operation = _Operation(should_cancel,time.monotonic()+timeout)
    if previous is not None:
        operation = _Operation(lambda:previous.should_cancel() or should_cancel(),
                               min(previous.deadline,operation.deadline))
    token = _current.set(operation)
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _current.reset(token)


def bounded_operation(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def bounded(*args: _P.args,**kwargs: _P.kwargs) -> _R:
        if _current.get() is None:
            with operation_scope():
                return function(*args,**kwargs)
        checkpoint()
        result = function(*args,**kwargs)
        checkpoint()
        return result
    return bounded


def read_response(response, limit, *, timeout):
    """Use one buffered/socket read at a time so trickle traffic yields checks.

    urllib HTTPResponse exposes read1 and its socket through fp.raw. Refreshing
    that socket's timeout caps each blocking receive by the remaining budget;
    cancellation can only be observed after an in-flight receive returns.
    """
    data = bytearray()
    read = getattr(response,'read1',None)
    # Never bypass an adapter's read/decompression/error handling through fp.
    # Without its own one-receive API, read one byte through that adapter.
    single_byte = not callable(read)
    if single_byte:
        read = response.read
    assert callable(read)
    last_socket = last_timeout = None
    while limit is None or len(data) < limit:
        remaining = request_timeout(timeout)
        socket = None
        current = response
        for _ in range(5):
            socket = getattr(current,'_sock',None)
            if socket is not None:
                break
            current = getattr(current,'raw',None) or getattr(current,'fp',None)
            if current is None:
                break
        if socket is not None and (socket is not last_socket or remaining != last_timeout):
            socket.settimeout(remaining)
            last_socket,last_timeout = socket,remaining
        size = 1 if single_byte else 64*1024
        chunk = read(size if limit is None else min(size,limit-len(data)))
        checkpoint()
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


class ScopedResponse:
    """Keep an extractor's headers/metadata while checking streamed reads."""

    def __init__(self,response):
        self.response = response
        self._socket_guard = _guard_socket(response)

    def __getattr__(self,name):
        return getattr(self.response,name)

    def __enter__(self):
        return self

    def __exit__(self,kind,_value,_traceback):
        if kind is None:
            self.close()
        else:
            try:
                self.close()
            except Exception:
                pass  # cleanup must not mask the original operation failure

    def close(self):
        if self._socket_guard is not None:
            raw,guard = self._socket_guard
            if raw._sock is guard:
                raw._sock = guard.socket
            guard.restore_timeout()
            self._socket_guard = None
        self.response.close()

    def read(self,size=None):
        if size is not None:
            size = operator.index(size)
        limit = None if size is None or size < 0 else size
        try:
            guarded = self._socket_guard is not None
            if guarded:
                # Keep the adapter's complete transformation/error semantics;
                # checks happen at each real socket receive beneath buffering.
                checkpoint()
                data = self.response.read(size)
                checkpoint()
            else:
                data = read_response(self.response,limit,timeout=15)
            if limit is None or (not guarded and len(data) < limit):
                self.close()
            return data
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            checkpoint()
            raise

    def read1(self,size=-1):
        if size is not None:
            size = operator.index(size)
        method = getattr(self.response,'read1',None)
        if callable(method):
            return self._native_read(method,size)
        # No native one-receive API: one output byte through the adapter's
        # transforming read is the conservative short-read fallback.
        return self.read(0 if size == 0 else 1)

    def readinto(self,buffer):
        view = self._writable_bytes(buffer)
        method = getattr(self.response,'readinto',None)
        if self._socket_guard is not None and callable(method):
            return self._native_read(method,buffer)
        data = self.read(view.nbytes)
        view[:len(data)] = data
        return len(data)

    def readinto1(self,buffer):
        view = self._writable_bytes(buffer)
        method = getattr(self.response,'readinto1',None)
        if self._socket_guard is not None and callable(method):
            return self._native_read(method,buffer)
        data = self.read1(view.nbytes)
        view[:len(data)] = data
        return len(data)

    @staticmethod
    def _writable_bytes(buffer):
        view = memoryview(buffer)
        if view.readonly or not view.c_contiguous:
            raise TypeError('readinto requires a writable contiguous buffer')
        return view.cast('B')

    def _native_read(self,method,*args):
        try:
            checkpoint()
            result = method(*args)
            checkpoint()
            return result
        except BaseException:
            try:
                self.close()
            except Exception:
                pass
            checkpoint()
            raise

    def readline(self,size=-1):
        if size is not None:
            size = operator.index(size)
        method = getattr(self.response,'readline',None)
        if self._socket_guard is not None and callable(method):
            return self._native_read(method,size)
        line = bytearray()
        while size is None or size < 0 or len(line) < size:
            value = self.read(1)
            if not value:
                break
            line.extend(value)
            if value == b'\n':
                break
        return bytes(line)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readlines(self,hint=-1):
        if hint is not None:
            hint = operator.index(hint)
        method = getattr(self.response,'readlines',None)
        if self._socket_guard is not None and callable(method):
            return self._native_read(method,hint)
        lines = []
        total = 0
        for line in self:
            lines.append(line)
            total += len(line)
            if hint is not None and hint > 0 and total >= hint:
                break
        return lines


class _SocketGuard:
    """Per-response delegation at CPython SocketIO's existing receive boundary."""

    def __init__(self,original):
        self.socket = original
        self.original_timeout = original.gettimeout()
        self._restored = False

    def __getattr__(self,name):
        return getattr(self.socket,name)

    def restore_timeout(self):
        if self._restored:
            return
        self._restored = True
        try:
            self.socket.settimeout(self.original_timeout)
        except OSError:
            pass

    def _decref_socketios(self):
        # EOF may return a keep-alive connection to its pool inside adapter.read.
        # Restore before that release, and never touch a later borrow on close.
        self.restore_timeout()
        return self.socket._decref_socketios()

    def recv_into(self,*args,**kwargs):
        self.socket.settimeout(request_timeout(15 if self.original_timeout is None else self.original_timeout))
        result = self.socket.recv_into(*args,**kwargs)
        checkpoint()
        return result

    def recv(self,*args,**kwargs):
        self.socket.settimeout(request_timeout(15 if self.original_timeout is None else self.original_timeout))
        result = self.socket.recv(*args,**kwargs)
        checkpoint()
        return result


def _guard_socket(response):
    pending = [response]
    seen: set[int] = set()
    while pending and len(seen) < 16:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if type(value) is socket.SocketIO and getattr(value,'_sock',None) is not None:
            guard = _SocketGuard(getattr(value,'_sock'))
            setattr(value,'_sock',guard)
            return value,guard
        for name in ('fp','raw','_fp'):
            child = getattr(value,name,None)
            if child is not None:
                pending.append(child)
    return None
