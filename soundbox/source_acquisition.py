"""Bounded, read-only acquisition of canonical audio into disposable local state.

A digest binds captured bytes, not cloud placeholder timestamps. With no digest,
a capture must have a stable filesystem identity; one hydration retry is allowed.
No caller should reopen the canonical path after this boundary.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .operation_control import checkpoint, phase_progress, record_metric
from .source_read_retry import ProviderReadRetry, source_read_error

LOG = logging.getLogger("soundbox")


class SourceAcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceContract:
    canonical: Path
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SourceSnapshot:
    contract: SourceContract
    path: Path
    size: int
    sha256: str
    observation: os.stat_result


def _identity(stat: os.stat_result) -> tuple[int, ...]:
    return (stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


@contextmanager
def acquire_source(
    contract: SourceContract,
    root: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> Iterator[SourceSnapshot]:
    canonical = contract.canonical.expanduser().resolve(strict=True)
    if canonical != contract.canonical:
        raise SourceAcquisitionError(f"Source identity changed: {contract.canonical}")
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    total_read = 0
    attempts = 0
    unstable_captures = 0
    readiness = ProviderReadRetry()
    with tempfile.TemporaryDirectory(prefix="source-", dir=root) as directory:
        local = Path(directory) / canonical.name
        while True:
            attempts += 1
            record_metric("acquisitions")
            before = canonical.stat()
            expected_size = contract.size if contract.size is not None else before.st_size
            # Leave room for the snapshot and one working derivative. Container
            # rewriting may need modest metadata headroom; disk-full still fails
            # before replacing any destination.
            space = os.statvfs(root)
            if space.f_bavail * space.f_frsize < expected_size * 2:
                raise SourceAcquisitionError(f"Insufficient local staging space: {canonical}")
            digest = hashlib.sha256()
            size = 0
            reported = time.monotonic()
            phase_progress("Acquiring source", 0, expected_size, canonical.name)
            try:
                with canonical.open("rb") as source, local.open("wb") as destination:
                    opened = os.fstat(source.fileno())
                    while True:
                        if should_cancel is not None and should_cancel():
                            raise SourceAcquisitionError("Source acquisition cancelled")
                        checkpoint()
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        destination.write(block)
                        digest.update(block)
                        size += len(block)
                        total_read += len(block)
                        record_metric("canonical_bytes_read", len(block))
                        record_metric("peak_audio_staging_bytes", size, peak=True)
                        if on_bytes is not None:
                            on_bytes(size)
                        now = time.monotonic()
                        if now - reported >= 0.2:
                            phase_progress("Acquiring source", size, expected_size, canonical.name)
                            reported = now
                    closed = os.fstat(source.fileno())
                after = canonical.stat()
            except OSError as exc:
                local.unlink(missing_ok=True)
                if readiness.wait_after(exc, canonical, should_cancel=should_cancel):
                    # The next attempt recreates both snapshot and digest.
                    continue
                raise SourceAcquisitionError(source_read_error(canonical, exc)) from exc
            value = digest.hexdigest()
            if contract.sha256 is not None:
                if size != expected_size or value != contract.sha256:
                    raise SourceAcquisitionError(f"Source audio changed (captured bytes differ from the expected audio): {canonical}")
                # Exact bytes are authoritative even across eviction/rehydration.
                break
            stable = (_identity(before) == _identity(opened)
                      == _identity(closed) == _identity(after)
                      and size == expected_size)
            if stable:
                break
            unstable_captures += 1
            if unstable_captures >= 2:
                raise SourceAcquisitionError(f"Source remained unstable during acquisition: {canonical}")
        LOG.info("Source acquisition: %s bytes=%d attempts=%d seconds=%.3f peak_snapshot=%d",
                 canonical.name, total_read, attempts, time.monotonic() - started, size)
        yield SourceSnapshot(contract, local, size, value, after)
