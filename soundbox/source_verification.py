"""Disposable, resumable full-hash proofs for immutable local source files.

This cache proves source content only. It never confers device ownership or
replaces commit-time source/payload validation. Each completed proof survives a
later planning failure without retaining a second copy of the audio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Callable

from .rockbox_media import _atomic_write
from .operation_control import checkpoint, record_metric

LOG = logging.getLogger(__name__)


def _identity(stat: os.stat_result) -> list[int]:
    return [stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


class SourceVerificationCache:
    def __init__(self, root: Path):
        self.root = root / "source-verification-v1"
        self.hits = 0
        self.reads = 0
        self.bytes_read = 0

    def verify(self, source: Path, hash_file: Callable[[Path], str]
               ) -> tuple[str, os.stat_result]:
        source = source.resolve(strict=True)
        stat = source.stat()
        key = hashlib.sha256(os.fsencode(source)).hexdigest()
        path = self.root / f"{key}.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            digest = row.get("sha256", "")
            if (row.get("source") == str(source)
                    and row.get("identity") == _identity(stat)
                    and isinstance(digest, str) and len(digest) == 64
                    and all(c in "0123456789abcdef" for c in digest)):
                self.hits += 1
                record_metric("source_proof_hits")
                return digest, stat
        except (OSError, ValueError, TypeError, AttributeError):
            pass

        for attempt in range(2):
            checkpoint()
            stat = source.stat()
            digest = hash_file(source)
            self.reads += 1
            self.bytes_read += stat.st_size
            record_metric("source_proof_reads")
            record_metric("canonical_bytes_read", stat.st_size)
            after = source.stat()
            if _identity(stat) == _identity(after):
                break
            # Cloud hydration can alter ctime on the first read. Only another
            # complete hash against a stable identity may certify those bytes.
            if attempt:
                raise RuntimeError(f"Source changed while verifying: {source}")
        try:
            _atomic_write(path, json.dumps({
                "source": str(source), "identity": _identity(stat),
                "sha256": digest,
            }, sort_keys=True).encode("utf-8"))
        except OSError:
            # Persistence is acceleration, never a requirement for correctness.
            LOG.warning("Could not save source verification for %s", source)
        return digest, stat
