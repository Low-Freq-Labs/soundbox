"""Disposable artwork-check metadata and bounded small comparison previews.

This is separate from curated art/ and its backup. Exact context stamps bind
cached checks; restoring/changing a cover cannot authorize a stale replacement.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

MATCHING_POLICY = 2
PREVIEW_BYTES = 8 * 1024 * 1024
PREVIEW_COUNT = 128


def context_stamp(entry: dict, digest: str, artist: str, title: str, exclusions) -> str:
    value = [MATCHING_POLICY, entry, digest, artist, title, sorted(exclusions)]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReviewScanStore:
    def __init__(self, root: Path):
        root = Path(root)
        if root.is_symlink():
            raise ValueError("Review cache cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        path = root / "checks.sqlite3"
        for candidate in (path, root / "checks.sqlite3-wal", root / "checks.sqlite3-shm", root / "checks.sqlite3-journal"):
            if candidate.is_symlink():
                raise ValueError("Review cache cannot follow symlinks")
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS epoch (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO epoch VALUES (1,0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS checks (tid TEXT PRIMARY KEY, stamp TEXT NOT NULL, epoch INTEGER NOT NULL, result TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS previews (key TEXT PRIMARY KEY, data BLOB NOT NULL, used INTEGER NOT NULL)")
        self.db.commit()
        self.epoch = int(self.db.execute("SELECT value FROM epoch WHERE id=1").fetchone()[0])

    def invalidate(self) -> None:
        """Explicit recheck: old rows remain harmless until individually replaced."""
        self.epoch += 1
        self.db.execute("UPDATE epoch SET value=? WHERE id=1", (self.epoch,))
        self.db.commit()

    def get(self, tid: str, stamp: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT result FROM checks WHERE tid=? AND stamp=? AND epoch=?",
                              (tid, stamp, self.epoch)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
            raise ValueError("Invalid review cache record")
        return value

    def put(self, tid: str, stamp: str, result: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?)",
                        (tid, stamp, self.epoch, json.dumps(result, ensure_ascii=False)))
        self.db.commit()

    @staticmethod
    def _preview_key(candidate: dict) -> str:
        return hashlib.sha256(json.dumps([candidate.get("url"), candidate.get("thumb")]).encode()).hexdigest()

    def preview_get(self, candidate: dict) -> bytes | None:
        key = self._preview_key(candidate)
        row = self.db.execute("SELECT data FROM previews WHERE key=?", (key,)).fetchone()
        if row is None or not 0 < len(row[0]) <= 512 * 1024:
            return None
        self.db.execute("UPDATE previews SET used=(SELECT COALESCE(MAX(used),0)+1 FROM previews) WHERE key=?", (key,))
        self.db.commit()
        return bytes(row[0])

    def preview_put(self, candidate: dict, data: bytes) -> None:
        if not 0 < len(data) <= 512 * 1024:
            return
        self.db.execute("INSERT OR REPLACE INTO previews VALUES (?,?,(SELECT COALESCE(MAX(used),0)+1 FROM previews))",
                        (self._preview_key(candidate), data))
        count, size = self.db.execute("SELECT COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM previews").fetchone()
        while count > PREVIEW_COUNT or size > PREVIEW_BYTES:
            key, length = self.db.execute("SELECT key,LENGTH(data) FROM previews ORDER BY used LIMIT 1").fetchone()
            self.db.execute("DELETE FROM previews WHERE key=?", (key,))
            count, size = count - 1, size - length
        self.db.commit()

    def close(self) -> None:
        self.db.close()
