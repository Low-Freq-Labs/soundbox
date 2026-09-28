"""Canonical local artwork, results and default photos; no online providers.

All publication goes through the same state owner as library backup/restore.
Stored provenance remains readable with discovery packages absent.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Concatenate

from .state_io import StateChangedError, state_area


@dataclass
class ArtResult:
    data: bytes
    mime: str
    confidence: float
    source_url: str
    matched_title: str


def _art_mutation[**P, R](method: Callable[Concatenate[Any, P], R]) -> Callable[Concatenate[Any, P], R]:
    """Compose and publish one artwork mutation under the shared state owner."""
    @wraps(method)
    def serialized(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._area.lock:
            if getattr(self, "_writing", False):
                return method(self, *args, **kwargs)
            if self.read_only:
                raise OSError("Restore a valid library before changing artwork")
            if self._generation != self._area.generation:
                raise StateChangedError("Artwork state was restored; reload before writing")
            if self._revision != self._area.art_revision:
                self._reload_index()
                self._defaults = self._load_defaults()
                self._rebuild_default_assignments(persist=False)
                self._revision = self._area.art_revision
            before = (self.index, self._defaults, self._default_assignments,
                      self._default_track_ids)
            self.index = copy.deepcopy(self.index)
            self._defaults = copy.deepcopy(self._defaults)
            self._default_assignments = dict(self._default_assignments)
            self._changes = {}
            self._writing = True
            try:
                result = method(self, *args, **kwargs)
                if self._changes:
                    self._area.publish(self._changes)
                    self._area.art_revision += 1
                self._revision = self._area.art_revision
                return result
            except Exception:
                (self.index, self._defaults, self._default_assignments,
                 self._default_track_ids) = before
                raise
            finally:
                self._changes = {}
                self._writing = False
    return serialized


def _art_read[**P, R](method: Callable[Concatenate[Any, P], R]) -> Callable[Concatenate[Any, P], R]:
    @wraps(method)
    def serialized(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._area.lock:
            return method(self, *args, **kwargs)
    return serialized


class ArtCache:
    """Persistent per-TrackID art cache (personal, local). Stores each fetched
    image once and remembers misses so we never re-hammer for the same track."""

    def __init__(self, root: Path):
        if Path(root).is_symlink():
            raise ValueError("Artwork root cannot be a symlink")
        self.root = Path(root).resolve()
        self._area = state_area(self.root.parent if self.root.name == "art" else self.root)
        self.read_only = False
        self._generation = self._area.generation
        self._revision = self._area.art_revision
        self._changes: dict[str, bytes | Path | None] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._reload_index()
        # User-supplied generic pictures are canonical art inputs too, but are
        # deliberately separate from the per-track index.  A fallback never
        # rewrites a miss/block or masquerades as a real cover/avatar.
        self._defaults_root = self.root / "defaults"
        self._defaults_index_path = self._defaults_root / "index.json"
        self._default_track_ids: tuple[str, ...] = ()
        self._default_assignments: dict[str, str] = {}
        self._defaults = self._load_defaults()

    def _reload_index(self) -> None:
        self.index = (json.loads(self._index_path.read_text())
                      if self._index_path.exists() else {})
        if not isinstance(self.index, dict) or any(
                not isinstance(entry, dict) for entry in self.index.values()):
            raise ValueError("Invalid artwork index; restore a valid backup")

    def _write(self, path: Path, data: bytes | None) -> None:
        relative = str(path.relative_to(self._area.root))
        self._area.path(relative)
        self._changes[relative] = data

    def _present(self, path: Path) -> bool:
        relative = str(path.relative_to(self._area.root))
        if relative in self._changes:
            return self._changes[relative] is not None
        return path.is_file()

    def _load_defaults(self) -> dict:
        try:
            raw = json.loads(self._defaults_index_path.read_text(encoding="utf-8"))
            if raw.get("schema") != 1 or not isinstance(raw.get("photos"), dict):
                raise ValueError("unsupported default-photo index")
            seed = str(raw.get("seed") or "")
            if not seed:
                raise ValueError("missing default-photo seed")
            photos = {
                str(pid): dict(info)
                for pid, info in raw["photos"].items()
                if isinstance(info, dict)
            }
            raw_assignments = raw.get("assignments") or {}
            if not isinstance(raw_assignments, dict):
                raw_assignments = {}
            assignments = {
                str(tid): str(pid)
                for tid, pid in raw_assignments.items()
                if tid and pid
            }
            return {"schema": 1, "seed": seed, "photos": photos,
                    "assignments": assignments}
        except FileNotFoundError:
            return {"schema": 1, "seed": secrets.token_hex(16), "photos": {},
                    "assignments": {}}

    def _save_defaults(self) -> None:
        self._write(self._defaults_index_path,
                    json.dumps(self._defaults, indent=2).encode("utf-8"))

    def _default_photo_path(self, photo_id: str) -> Path:
        return self._defaults_root / f"{photo_id}.jpg"

    def _valid_default_ids(self) -> list[str]:
        return [
            pid for pid in self._defaults["photos"]
            if self._present(self._default_photo_path(pid))
        ]

    def _is_default_eligible(self, track_id: str) -> bool:
        # Explicit blocks ARE eligible: the user removed a bad/undesired
        # discovered image, not the generic safety-net requested for blank art.
        # Any successful real cover or artist avatar always wins.
        return (self.index.get(track_id) or {}).get("status") != "ok"

    @_art_mutation
    def configure_default_tracks(self, track_ids) -> None:
        """Define the visible/exportable assignment universe and rebalance it.

        Assignments are deterministic-random for a given pool/library and exact
        balanced (counts differ by at most one). They live only in the separate
        default-pool manifest. Pool/library changes preserve every assignment
        they can and move only the minimum needed to restore balance, avoiding
        both curated-index pollution and gratuitous device-sync churn.
        """
        self._default_track_ids = tuple(sorted({str(tid) for tid in track_ids if tid}))
        self._rebuild_default_assignments()

    def _rebuild_default_assignments(self, *, persist: bool = True) -> None:
        before = dict(self._defaults["assignments"])
        photos = self._valid_default_ids()
        eligible = [tid for tid in self._default_track_ids
                    if self._is_default_eligible(tid)]
        if not photos or not eligible:
            self._default_assignments = {}
            self._defaults["assignments"] = {}
            if persist and before and self._defaults_index_path.exists():
                self._save_defaults()
            return
        seed = self._defaults["seed"]

        def order(prefix: str, value: str) -> str:
            return hashlib.sha256(
                f"{seed}\0{prefix}\0{value}".encode("utf-8")).hexdigest()

        eligible_set, photo_set = set(eligible), set(photos)
        shuffled_photos = sorted(photos, key=lambda pid: order("photo", pid))
        base, extra = divmod(len(eligible), len(shuffled_photos))
        targets = {
            pid: base + (1 if index < extra else 0)
            for index, pid in enumerate(shuffled_photos)
        }
        assignments = {
            tid: pid for tid, pid in before.items()
            if tid in eligible_set and pid in photo_set
        }
        counts = {pid: 0 for pid in shuffled_photos}
        for pid in assignments.values():
            counts[pid] += 1

        # Preserve every still-valid assignment possible. Only tracks in an
        # over-capacity bucket move, which prevents one newly discovered real
        # cover from reshuffling the whole device library.
        unassigned = set(eligible_set - assignments.keys())
        for pid in shuffled_photos:
            surplus = counts[pid] - targets[pid]
            if surplus <= 0:
                continue
            candidates = sorted(
                (tid for tid, assigned in assignments.items() if assigned == pid),
                key=lambda tid: order(f"move:{pid}", tid),
            )[:surplus]
            for tid in candidates:
                assignments.pop(tid)
                counts[pid] -= 1
                unassigned.add(tid)

        for tid in sorted(unassigned, key=lambda value: order("track", value)):
            available = [pid for pid in shuffled_photos if counts[pid] < targets[pid]]
            if not available:  # defensive; the exact target sum should prevent this
                break
            pid = min(available, key=lambda value: order(f"assign:{tid}", value))
            assignments[tid] = pid
            counts[pid] += 1

        self._default_assignments = assignments
        self._defaults["assignments"] = dict(assignments)
        if persist and assignments != before:
            self._save_defaults()

    @_art_read
    def default_photos(self) -> list[dict]:
        """Managed pool rows for the Settings catalog."""
        counts: dict[str, int] = {}
        for pid in self._default_assignments.values():
            counts[pid] = counts.get(pid, 0) + 1
        rows = []
        for pid, info in self._defaults["photos"].items():
            path = self._default_photo_path(pid)
            if path.is_file():
                rows.append({
                    "id": pid,
                    "name": str(info.get("name") or "Default photo"),
                    "added": str(info.get("added") or ""),
                    "path": path,
                    "tracks": counts.get(pid, 0),
                })
        return sorted(rows, key=lambda row: (row["added"], row["name"].casefold()))

    @_art_mutation
    def import_default_photos(self, paths) -> dict:
        """Copy/normalize local images into the managed default-photo pool.

        The source path is never retained as a dependency.  Every accepted
        image becomes a capped baseline JPEG, then the existing stock/Rockbox
        derivative pipelines consume it exactly like canonical track art.
        """
        from . import artencode

        added = duplicates = 0
        errors: list[str] = []
        staged: list[tuple[str, bytes, str]] = []
        known = set(self._defaults["photos"])
        for raw in paths:
            source = Path(raw)
            try:
                data = source.read_bytes()
                safe = artencode.to_baseline_jpeg(data)
                if safe is None:
                    raise ValueError("not a readable image")
            except (OSError, ValueError) as exc:
                errors.append(f"{source.name}: {exc}")
                continue
            photo_id = hashlib.sha256(safe).hexdigest()
            if photo_id in known:
                duplicates += 1
                continue
            known.add(photo_id)
            staged.append((photo_id, safe, source.name))

        if staged:
            self._defaults_root.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for photo_id, safe, name in staged:
                self._write(self._default_photo_path(photo_id), safe)
                self._defaults["photos"][photo_id] = {
                    "name": name,
                    "added": stamp,
                }
                added += 1
            self._rebuild_default_assignments(persist=False)
            self._save_defaults()
        return {"added": added, "duplicates": duplicates, "errors": errors}

    @_art_mutation
    def remove_default_photos(self, photo_ids) -> int:
        """Remove only selected managed pool copies; source originals and
        per-track curated art are outside this operation."""
        removed: list[str] = []
        for photo_id in {str(pid) for pid in photo_ids}:
            if photo_id in self._defaults["photos"]:
                self._defaults["photos"].pop(photo_id)
                removed.append(photo_id)
        if not removed:
            return 0
        self._rebuild_default_assignments(persist=False)
        self._save_defaults()
        for photo_id in removed:
            self._write(self._default_photo_path(photo_id), None)
        return len(removed)

    @_art_read
    def default_image_path(self, track_id: str) -> Path | None:
        """Assigned generic fallback, never a real per-track image."""
        if not self._is_default_eligible(track_id):
            return None
        photo_id = self._default_assignments.get(track_id)
        if photo_id is not None:
            path = self._default_photo_path(photo_id)
            if path.is_file():
                return path
        photos = self._valid_default_ids()
        if not photos:
            return None
        # Safe edge fallback for a caller outside the configured live library
        # (or a pool file removed outside SoundBox). The desktop path is
        # preconfigured and therefore uses the exact-balanced map above.
        seed = self._defaults["seed"]
        digest = hashlib.sha256(
            f"{seed}\0edge\0{track_id}".encode("utf-8")).digest()
        photo_id = sorted(photos)[int.from_bytes(digest[:8], "big") % len(photos)]
        path = self._default_photo_path(photo_id)
        return path if path.is_file() else None

    @_art_read
    def status(self, track_id: str) -> str | None:
        return (self.index.get(track_id) or {}).get("status")  # "ok" | "none" | None

    @_art_read
    def status_kind(self, track_id: str) -> str:
        """UI status: cover/avatar/default/miss/blocked/unfetched."""
        e = self.index.get(track_id)
        if (e or {}).get("status") == "ok":
            return "avatar" if self._is_avatar(e) else "cover"
        if self.default_image_path(track_id) is not None:
            return "default"
        if not e:
            return ""
        if e.get("status") == "none":
            return "miss"
        if e.get("status") == "blocked":
            return "blocked"
        return ""

    @_art_read
    def canonical_image_path(self, track_id: str) -> Path | None:
        """The real per-track cover/avatar only (never a generic fallback)."""
        e = self.index.get(track_id) or {}
        if e.get("status") == "ok":
            p = self.root / f"{track_id}.{e.get('ext', 'jpg')}"
            if p.is_file():
                return p
        return None

    @_art_read
    def image_path(self, track_id: str) -> Path | None:
        return self.canonical_image_path(track_id) or self.default_image_path(track_id)

    @_art_mutation
    def store_ok(self, track_id: str, res: ArtResult, *, origin: str = "manual",
                 rebalance_defaults: bool = True):
        # Normalize to baseline JPEG at the cache boundary — Rockbox renders
        # nothing else (progressive JPEG / PNG show blank on the iPod).
        from . import artencode
        data = artencode.ensure_device_safe(res.data)
        ext = "jpg" if artencode.is_device_safe(data) else ("png" if "png" in res.mime else "jpg")
        self._write(self.root / f"{track_id}.{ext}", data)
        self.index[track_id] = {"status": "ok", "ext": ext,
                                "src": res.source_url, "conf": round(res.confidence, 2),
                                "origin": "auto" if origin == "auto" else "manual"}
        self._save()
        if rebalance_defaults:
            self._rebuild_default_assignments()

    @_art_mutation
    def rebalance_default_photos(self) -> None:
        """Recompute the exact-balanced live assignment after a bulk art pass."""
        self._rebuild_default_assignments()

    @_art_mutation
    def normalize_all(self) -> tuple[int, int]:
        """One-time sweep: re-encode any cached image that isn't baseline JPEG
        (Rockbox-safe). Idempotent + flag-filed, so later launches are instant.
        Returns (checked, fixed)."""
        from . import artencode
        flag = self.root / ".normalized-v1"
        if flag.exists():
            return 0, 0
        checked = fixed = 0
        for tid, e in list(self.index.items()):
            if (e or {}).get("status") != "ok":
                continue
            p = self.root / f"{tid}.{e.get('ext', 'jpg')}"
            if not p.is_file():
                continue
            checked += 1
            data = p.read_bytes()
            if artencode.is_device_safe(data):
                continue
            safe = artencode.to_baseline_jpeg(data)
            if not safe:
                continue  # undecodable — leave as-is rather than destroy
            self._write(self.root / f"{tid}.jpg", safe)
            if e.get("ext") != "jpg":
                self._write(p, None)
                e["ext"] = "jpg"
            fixed += 1
        if fixed:
            self._save()
        self._write(flag, b"")
        return checked, fixed

    @_art_mutation
    def store_none(self, track_id: str):
        self.index[track_id] = {"status": "none"}
        self._save()

    @_art_mutation
    def retry_misses(self) -> int:
        """Clear all 'no art found' marks so they'll be re-attempted. User-blocked
        entries are NOT touched — a deliberate removal survives every re-scan."""
        misses = [tid for tid, e in self.index.items() if (e or {}).get("status") == "none"]
        for tid in misses:
            self.index.pop(tid, None)
        self._save()
        return len(misses)

    @staticmethod
    def _is_avatar(e) -> bool:
        """An 'ok' cover that is actually the artist profile picture (fallback),
        i.e. its source is an artist page (one path segment) not a track page."""
        if (e or {}).get("status") != "ok":
            return False
        src = e.get("src") or ""
        if "soundcloud.com/" not in src:
            return False
        tail = src.split("soundcloud.com/", 1)[-1].strip("/")
        return len([x for x in tail.split("/") if x]) <= 1

    @_art_mutation
    def retry_weak(self) -> int:
        """Clear misses AND artist-avatar fallbacks so they re-attempt for a real
        track cover — keeps confirmed real covers. Deletes the stale avatar image
        so it's re-fetched cleanly. Returns how many were cleared."""
        weak = [tid for tid, e in self.index.items()
                if (e or {}).get("status") == "none" or self._is_avatar(e)]
        for tid in weak:
            e = self.index.pop(tid, None)
            if e and e.get("status") == "ok" and e.get("ext"):
                self._write(self.root / f"{tid}.{e['ext']}", None)
        self._save()
        return len(weak)

    @_art_mutation
    def forget(self, track_id: str):
        e = self.index.pop(track_id, None)
        if e and e.get("ext"):
            self._write(self.root / f"{track_id}.{e['ext']}", None)
        self._save()
        self._rebuild_default_assignments()

    @_art_mutation
    def block(self, track_id: str):
        """User removed this track's art ON PURPOSE: delete the image but keep a
        'blocked' entry so the background fill NEVER re-fetches it. Only an
        explicit per-track action (Fetch now / picker / URL) overrides — those
        go through store_ok, which overwrites the block."""
        e = self.index.get(track_id)
        if e and e.get("ext"):
            self._write(self.root / f"{track_id}.{e['ext']}", None)
        self.index[track_id] = {"status": "blocked"}
        self._save()
        self._rebuild_default_assignments()

    @_art_mutation
    def _save(self):
        self._write(self._index_path, json.dumps(self.index).encode("utf-8"))
