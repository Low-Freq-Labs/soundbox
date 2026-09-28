"""Qt-independent lifecycle authority for one serialized device I/O lane.

Repair/drift state is deliberately absent: it cannot make an idle mount busy.
Result publication never releases a worker; only its owner's finished callback
may release the lane. Engine DeviceSessions retain per-write filesystem guards.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
import logging
import time
from threading import Event
from uuid import uuid4


class Phase(StrEnum):
    DISCOVERING = "discovering"
    INSPECTING = "inspecting"
    READY = "ready"
    PLANNING = "planning"
    EXECUTING = "executing"
    ROLLING_BACK = "rolling back"
    FINALIZING = "finalizing"
    EJECTING = "ejecting"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class DeviceSessionIdentity:
    path: str
    mount_token: int
    library_identity: str = ""
    mount_epoch: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class DeviceCommand:
    target: DeviceSessionIdentity
    intent: str
    playlists: tuple[str, ...] = ()
    confirmation_scope: str = ""
    operation_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class DeviceOperationResult:
    committed: bool = False
    cancelled: bool = False
    failure: str = ""
    recovery: str = "not needed"
    changed_files: int = 0
    refresh: str = "not requested"


@dataclass
class DeviceOperationState:
    command: DeviceCommand
    phase: Phase
    worker: object
    cancellation: Event = field(default_factory=Event)
    progress: tuple[int, int, str] = (0, 0, "")
    result: DeviceOperationResult | None = None
    started: float = field(default_factory=time.monotonic)


class DeviceOperationCoordinator:
    def __init__(self) -> None:
        self.mounts: dict[str, DeviceSessionIdentity] = {}
        self.active: DeviceOperationState | None = None
        self.queued: deque[DeviceCommand] = deque()
        self.stopping = False
        self.eject_pending: str = ""
        self.last_result: DeviceOperationResult | None = None

    @property
    def busy(self) -> bool:
        return self.active is not None

    @property
    def polling_allowed(self) -> bool:
        return not (self.busy or self.stopping or self.eject_pending)

    def observe(self, identity: DeviceSessionIdentity) -> None:
        previous = self.mounts.get(identity.path)
        if (previous is not None and previous.mount_token == identity.mount_token
                and previous.library_identity == identity.library_identity):
            return
        self.mounts[identity.path] = identity
        if previous is not None and previous != identity:
            self.queued = deque(c for c in self.queued if c.target.path != identity.path)
            if self.active is not None and self.active.command.target.path == identity.path:
                self.active.cancellation.set()

    def disconnect(self, path: str) -> None:
        self.mounts.pop(path, None)
        self.queued = deque(c for c in self.queued if c.target.path != path)
        if self.active is not None and self.active.command.target.path == path:
            self.active.cancellation.set()

    def current(self, command: DeviceCommand) -> bool:
        return (not command.target.path or
                self.mounts.get(command.target.path) == command.target)

    def begin(self, command: DeviceCommand, worker: object, phase: Phase) -> DeviceOperationState:
        if self.stopping or self.active is not None:
            raise RuntimeError("Device I/O lane is already owned or stopping")
        if not self.current(command):
            raise RuntimeError("Device mount changed; request a new operation")
        self.active = DeviceOperationState(command, phase, worker)
        return self.active

    def publish(self, worker: object, result: DeviceOperationResult) -> bool:
        if self.active is None or self.active.worker is not worker:
            return False
        self.active.result = result
        return self.current(self.active.command)

    def finished(self, worker: object) -> bool:
        if self.active is None or self.active.worker is not worker:
            return False
        current = self.current(self.active.command)
        logging.getLogger("soundbox").info("Device phase %s operation=%s seconds=%.3f",
            self.active.phase, self.active.command.operation_id,
            time.monotonic() - self.active.started)
        if self.active.result is not None:
            self.last_result = self.active.result
        if self.active.result and (self.active.result.cancelled or self.active.result.failure):
            self.queued.clear()
        self.active = None
        return current

    def cancel(self, *, shutdown: bool = False) -> None:
        self.queued.clear()
        self.stopping |= shutdown
        if self.active is not None:
            self.active.cancellation.set()

    def enqueue(self, command: DeviceCommand) -> None:
        if self.stopping or not self.current(command):
            raise RuntimeError("Queued command has no current mount authority")
        self.queued.append(command)

    def next_batch(self) -> tuple[DeviceCommand, ...]:
        if self.active is not None or self.stopping:
            return ()
        while self.queued and not self.current(self.queued[0]):
            self.queued.popleft()
        if not self.queued:
            return ()
        first = self.queued.popleft()
        batch = [first]
        if first.intent == "export" and first.confirmation_scope:
            while self.queued:
                following = self.queued[0]
                if (following.target != first.target or following.intent != "export"
                        or following.confirmation_scope != first.confirmation_scope):
                    break
                batch.append(self.queued.popleft())
        return tuple(batch)


def adapter_result(command: DeviceCommand, result: object) -> DeviceOperationResult:
    """Normalize existing engine reports without changing their public APIs."""
    from .operation_control import OperationCancelled
    from .report import SyncReport

    if isinstance(result, (Exception, str)):
        return DeviceOperationResult(
            committed=bool(getattr(result, "committed", False)),
            cancelled=isinstance(result, OperationCancelled),
            failure=str(result),
            recovery=str(getattr(result, "recovery_status", "not needed")),
        )
    reports = []
    if isinstance(result, SyncReport):
        reports = [result]
    elif command.intent == "export" and isinstance(result, list):
        reports = [report for _target, report in result]
    if reports:
        errors = [str(report) for report in reports if isinstance(report, str)]
        errors.extend(error for report in reports if isinstance(report, SyncReport)
                      for playlist in report.playlists for error in playlist.errors)
        cancelled = any(report.cancelled for report in reports if isinstance(report, SyncReport))
        changed = sum(playlist.copied for report in reports if isinstance(report, SyncReport)
                      for playlist in report.playlists)
        return DeviceOperationResult(
            committed=not errors and not cancelled, cancelled=cancelled,
            failure="; ".join(errors), changed_files=changed,
            recovery="partial filesystem export" if errors or cancelled else "not needed")
    return DeviceOperationResult(
        committed=command.intent.startswith("commit"),
        changed_files=sum(int(getattr(result, field, 0) or 0) for field in
                          ("copied_tracks", "rewritten_tracks", "copied_media", "copied_artwork")),
    )
