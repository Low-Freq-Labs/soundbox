"""Owned Qt-free helper lifetime and explicit inherited pipe admission."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import threading
from collections.abc import Sequence
from typing import Any

from .worker_protocol import PipeChannel, ProtocolError


def _pipe() -> tuple[int, int]:
    originals = os.pipe()
    result: list[int] = []
    try:
        for fd in originals:
            result.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3))
    except BaseException:
        for fd in result:
            os.close(fd)
        raise
    finally:
        for fd in originals:
            os.close(fd)
    return result[0], result[1]


class WorkerProcess:
    """Start/close on an owning background lane, after admitted jobs drain.

    A helper error never chooses a synchronous replacement. Its descriptors
    close admission and its process remains owned until an actual joined exit.
    """
    def __init__(self, mode: str, root: Path, *, command: Sequence[str] | None = None,
                 startup_timeout: float = 5):
        if mode not in ('cache', 'native-read') or not Path(root).is_absolute():
            raise ValueError('Invalid worker mode or owned root')
        if startup_timeout <= 0:
            raise ValueError('Worker startup timeout must be positive')
        self.mode, self.root = mode, Path(root)
        self.command = tuple(command) if command is not None else None
        self.startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self._channel: PipeChannel | None = None
        self._process: subprocess.Popen | None = None
        self._ready: Any = None
        self._failed: Exception | None = None
        self._closed = False

    @property
    def process(self) -> subprocess.Popen | None:
        return self._process

    @property
    def channel(self) -> PipeChannel:
        if not self.available or self._channel is None:
            raise ProtocolError('The background helper is unavailable; retry the saved operation')
        return self._channel

    @property
    def available(self) -> bool:
        return bool(not self._closed and self._failed is None and self._ready is not None
                    and self._process is not None and self._process.poll() is None)

    def _close_pipes(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.close()

    def invalidate(self, error: Exception) -> None:
        """Stop protocol admission while keeping the actor owned for join."""
        with self._lock:
            self._failed = error
            self._close_pipes()

    def start(self) -> Any:
        with self._lock:
            if self._closed:
                raise ProtocolError('The background helper has closed admission')
            if (self._failed is not None or self._ready is not None
                    and self._process is not None and self._process.poll() is not None):
                # Existing controller Retry reaches here on its background
                # lane. Retire the exact failed actor before any replacement;
                # an explicitly closed owner above can never respawn.
                self._close_pipes()
                if self._process is not None:
                    try:
                        self._process.wait(timeout=self.startup_timeout)
                    except subprocess.TimeoutExpired as exc:
                        raise ProtocolError('The previous helper is still finishing; retry shortly') from exc
                self._process = None
                self._failed = None
                self._ready = None
            if self._ready is not None:
                if not self.available:
                    raise ProtocolError('The background helper exited before the requested work')
                return self._ready
            request_read, request_write = _pipe()
            try:
                response_read, response_write = _pipe()
            except BaseException:
                os.close(request_read); os.close(request_write)
                raise
            self._channel = PipeChannel(response_read, request_write)
            prefix = self.command
            if prefix is None:
                prefix = ((sys.executable,) if getattr(sys, 'frozen', False) else
                          (sys.executable, str(Path(__file__).resolve().parents[1] / 'soundbox_app.py')))
            arguments = [*prefix, '--soundbox-worker', self.mode, str(self.root),
                         str(request_read), str(response_write)]
            try:
                self._process = subprocess.Popen(arguments, pass_fds=(request_read, response_write),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                self._failed = exc
                self._close_pipes()
                raise ProtocolError('Could not launch the background helper') from exc
            finally:
                os.close(request_read); os.close(response_write)
            try:
                ready, _size = self._channel.receive(timeout=self.startup_timeout)
                if not isinstance(ready, tuple) or ready[:2] != ('ready', self.mode):
                    raise ProtocolError('The background helper startup protocol changed')
                self._ready = ready
                return ready
            except Exception as exc:
                self._failed = exc
                self._close_pipes()
                raise ProtocolError('The background helper did not become ready') from exc

    def close(self) -> None:
        """Graceful stop/EOF and actual join; never terminate an owned actor."""
        with self._lock:
            process = self._process
            if process is None:
                self._closed = True
                self._close_pipes()
                return
            try:
                if not self._closed and self._failed is None and process.poll() is None and self._channel is not None:
                    self._channel.send(('stop',))
                    reply, _size = self._channel.receive(timeout=self.startup_timeout)
                    if reply != ('stopped', self.mode):
                        raise ProtocolError('The background helper did not acknowledge shutdown')
            finally:
                self._closed = True
                self._close_pipes()
                try:
                    process.wait(timeout=self.startup_timeout)
                except subprocess.TimeoutExpired as exc:
                    # Keep the handle: a later close still owns and joins it.
                    raise ProtocolError('The background helper is still finishing shutdown') from exc
