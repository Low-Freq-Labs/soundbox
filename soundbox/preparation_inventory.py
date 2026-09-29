"""Disposable metadata-only scheduling receipts, never content/ownership proofs.

A receipt suppresses repeated background preparation only. Interactive readers
and exporters retain their independent validation. Missing/corrupt inventories
are ordinary cold starts; inaccessible inputs cannot produce a reusable receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable


def signature(paths: Iterable[Path], policy: object) -> str | None:
    rows = []
    try:
        for path in paths:
            try:
                st = path.stat()
                identity = [st.st_dev, st.st_ino, st.st_mode, st.st_size,
                            st.st_mtime_ns, st.st_ctime_ns, getattr(st, 'st_flags', 0)]
            except FileNotFoundError:
                identity = None
            rows.append([str(path), identity])
        raw = json.dumps([policy, rows], sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


class PreparationInventory:
    def __init__(self, path: Path):
        self.path = path
        try:
            if path.stat().st_size > 16 * 1024**2:
                raise ValueError('Oversized preparation inventory')
            raw = json.loads(path.read_text())
            self.rows = {k: v for k, v in raw.items()
                         if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            self.rows = {}
        self.dirty = False

    def matches(self, key: str, value: str | None) -> bool:
        return value is not None and self.rows.get(key) == value

    def remember(self, key: str, before: str | None, after: str | None) -> None:
        if before is not None and before == after and not self.matches(key, after):
            self.rows[key] = before
            self.dirty = True

    def prune(self, keys: Iterable[str]) -> None:
        keep = set(keys)
        stale = self.rows.keys() - keep
        for key in stale:
            del self.rows[key]
        self.dirty |= bool(stale)

    def flush(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.preparation-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.rows, stream, sort_keys=True)
            os.replace(name, self.path)
            self.dirty = False
        finally:
            Path(name).unlink(missing_ok=True)
