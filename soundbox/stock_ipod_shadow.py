"""Disposable Mac-side acceleration state for guarded stock-iPod sync.

The connected iPod remains the source of truth.  A shadow is usable only when
its stable library identity and small live database fingerprints still match;
otherwise callers fall back to the complete read-only reconciliation path.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = 1
PROFILE = "ma446-stock-shadow-v1"


def _safe_name(identity_key: str) -> str:
    return hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:32]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8") + b"\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class StockIpodShadow:
    identity_key: str
    model: str
    serial_number: str
    generation: int
    committed_at: str
    database_sha256: str
    artwork_database_sha256: str
    ithmb: dict[str, dict[str, int]]
    sources: dict[str, dict[str, Any]]
    artwork: dict[str, dict[str, Any]]
    playlists: dict[str, dict[str, Any]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StockIpodShadow | None":
        if data.get("schema") != SCHEMA or data.get("profile") != PROFILE:
            return None
        required_maps = ("ithmb", "sources", "artwork", "playlists")
        if any(not isinstance(data.get(key), dict) for key in required_maps):
            return None
        identity_key = str(data.get("identity_key") or "")
        database_sha256 = str(data.get("database_sha256") or "")
        if not identity_key or len(database_sha256) != 64:
            return None
        return cls(
            identity_key=identity_key,
            model=str(data.get("model") or ""),
            serial_number=str(data.get("serial_number") or ""),
            generation=max(0, int(data.get("generation") or 0)),
            committed_at=str(data.get("committed_at") or ""),
            database_sha256=database_sha256,
            artwork_database_sha256=str(
                data.get("artwork_database_sha256") or ""),
            ithmb={str(key): dict(value) for key, value in data["ithmb"].items()
                   if isinstance(value, dict)},
            sources={str(key): dict(value) for key, value in data["sources"].items()
                     if isinstance(value, dict)},
            artwork={str(key): dict(value) for key, value in data["artwork"].items()
                     if isinstance(value, dict)},
            playlists={str(key): dict(value) for key, value in data["playlists"].items()
                       if isinstance(value, dict)},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "profile": PROFILE,
            "identity_key": self.identity_key,
            "model": self.model,
            "serial_number": self.serial_number,
            "generation": self.generation,
            "committed_at": self.committed_at,
            "database_sha256": self.database_sha256,
            "artwork_database_sha256": self.artwork_database_sha256,
            "ithmb": self.ithmb,
            "sources": self.sources,
            "artwork": self.artwork,
            "playlists": self.playlists,
        }


@dataclass(frozen=True)
class StockIpodSyncStatus:
    state: str
    detail: str
    local_changes: int = 0
    committed_at: str = ""


class StockIpodShadowStore:
    """Atomic per-device shadow storage below SoundBox AppData."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path_for(self, identity_key: str) -> Path:
        return self.root / _safe_name(identity_key) / "manifest.json"

    def load(self, identity_key: str) -> StockIpodShadow | None:
        if not identity_key:
            return None
        path = self.path_for(identity_key)
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(raw, dict):
            return None
        shadow = StockIpodShadow.from_dict(raw)
        return shadow if shadow and shadow.identity_key == identity_key else None

    def save(self, shadow: StockIpodShadow) -> None:
        if not shadow.identity_key:
            raise ValueError("A stock-iPod shadow requires a stable identity key")
        _atomic_json(self.path_for(shadow.identity_key), shadow.as_dict())

    def committed(
        self,
        *,
        identity_key: str,
        model: str,
        serial_number: str,
        database_sha256: str,
        artwork_database_sha256: str,
        ithmb: dict[str, dict[str, int]],
        sources: dict[str, dict[str, Any]],
        artwork: dict[str, dict[str, Any]],
        playlists: dict[str, dict[str, Any]],
    ) -> StockIpodShadow:
        before = self.load(identity_key)
        shadow = StockIpodShadow(
            identity_key=identity_key,
            model=model,
            serial_number=serial_number,
            generation=(before.generation if before else 0) + 1,
            committed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            database_sha256=database_sha256,
            artwork_database_sha256=artwork_database_sha256,
            ithmb=ithmb,
            sources=sources,
            artwork=artwork,
            playlists=playlists,
        )
        self.save(shadow)
        return shadow
