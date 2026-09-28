"""GUI-owned worker lifetime through queued finished delivery and actual join.

Adopt before start. A stop request closes admission permanently; cancellation is
cooperative, and drain keeps Qt dispatching until every adopted worker retires.
This is a lifetime primitive, not the device-operation scheduler.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QEventLoop, Signal, Slot, Qt


class TaskOwner(QObject):
    drained = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.stopping = False
        self._workers: dict[QThread, Callable[[QThread], None] | None] = {}

    @property
    def pending(self) -> int:
        return len(self._workers)

    def adopt(self, worker: QThread,
              retired: Callable[[QThread], None] | None = None) -> None:
        if self.stopping:
            raise RuntimeError("Cannot start work on a stopping task owner")
        if worker in self._workers or worker.isRunning() or worker.isFinished():
            raise RuntimeError("Adopt a fresh worker before starting it")
        self._workers[worker] = retired
        worker.finished.connect(self._retire, Qt.QueuedConnection)

    @Slot()
    def _retire(self) -> None:
        worker = self.sender()
        if worker not in self._workers:
            return
        # finished is emitted before thread-local cleanup; join that too before
        # releasing resources that the worker may still reference.
        worker.wait()
        callback = self._workers[worker]
        try:
            if callback is not None:
                callback(worker)
        finally:
            del self._workers[worker]
            worker.deleteLater()
            if not self._workers:
                self.drained.emit()

    def stop(self) -> None:
        self.stopping = True
        for worker in tuple(self._workers):
            if hasattr(worker, "cancel"):
                worker.cancel()
            elif hasattr(worker, "stop"):
                worker.stop()
            else:
                worker.requestInterruption()

    def drain(self) -> None:
        self.stop()
        if not self._workers:
            return
        loop = QEventLoop()
        self.drained.connect(loop.quit)
        try:
            loop.exec(QEventLoop.ExcludeUserInputEvents)
        finally:
            self.drained.disconnect(loop.quit)
