"""Bounded readiness retry for macOS cloud File Provider source reads only.

EAUTH/permissions and other I/O failures are not retried. ENEEDAUTH can also be
persistent: two waits are only a chance for a waking provider to load its account,
never permission to accept unread bytes or reuse a stale source proof.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Callable

from .operation_control import OperationCancelled, checkpoint, record_metric

LOG = logging.getLogger("soundbox")
_CLOUD_STORAGE = Path.home() / "Library" / "CloudStorage"
_RETRY_DELAYS = (3.0, 3.0)


def _wait(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while True:
        checkpoint()
        if should_cancel is not None and should_cancel():
            raise OperationCancelled("Source read cancelled while waiting for cloud storage")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


class ProviderReadRetry:
    def __init__(self) -> None:
        self.retries = 0

    def wait_after(self, error: OSError, source: Path, *,
                   should_cancel: Callable[[], bool] | None = None) -> bool:
        if (sys.platform != "darwin" or error.errno != 81
                or not source.is_relative_to(_CLOUD_STORAGE)
                or self.retries >= len(_RETRY_DELAYS)):
            return False
        delay = _RETRY_DELAYS[self.retries]
        self.retries += 1
        LOG.info("Cloud source not ready; retry %d/%d after %.1fs: %s",
                 self.retries, len(_RETRY_DELAYS), delay, source)
        record_metric("source_readiness_retries")
        _wait(delay, should_cancel)
        return True


def source_read_error(source: Path, error: BaseException) -> str:
    return (f"Cannot read source: {source}\n{error}\n"
            "If this file is stored in a cloud drive, check that the drive is "
            "connected and the file is available, then retry the operation.")
