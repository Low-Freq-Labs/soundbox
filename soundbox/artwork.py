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


def _file_exists(path: Path, presence: dict[Path, bool] | None = None) -> bool:
    if presence is None:
        return path.is_file()
    if path not in presence:
        presence[path] = path.is_file()
    return presence[path]


@dataclass
class ArtResult:
    data: bytes
    mime: str
    confidence: float
    source_url: str
    matched_title: str


@dataclass(frozen=True)
class CoverSnapshot:
    """One displayed cover, bound to its exact metadata and bytes."""
    entry: dict
    data: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True, eq=False)
class ArtworkSelection:
    """Opaque selected artwork intent; strong owners prevent identity reuse.

    This is an in-memory publication fence, not a proof of image-file bytes.
    Exporters retain their normal worker-side image acquisition and freshness.
    """
    _cache: Any
    _generation: int
    _rows: tuple
    _edge_pool: tuple
    _edge_seed: str


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
                self._selection_exclusions = self.source_exclusions()
                self._revision = self._area.art_revision
            before = (self.index, self._defaults, self._default_assignments,
                      self._default_track_ids, self._selection_exclusions)
            # Mutations replace/remove individual entries. Detach namespace
            # maps for rollback without cloning every unrelated cover, photo
            # and assignment for a one-track change (or an unchanged balance).
            self.index = dict(self.index)
            self._defaults = {
                **self._defaults,
                "photos": dict(self._defaults["photos"]),
                "assignments": dict(self._defaults["assignments"]),
            }
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
                 self._default_track_ids, self._selection_exclusions) = before
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
        self._selection_exclusions = self.source_exclusions()

    def _selection_rows(self, track_ids) -> tuple[tuple, bool]:
        from .art_sources import excluded_source
        rows = []
        edge = False
        for track_id in dict.fromkeys(track_ids):
            entry = self.index.get(track_id)
            state = ((entry or {}).get('status'), (entry or {}).get('ext'), (entry or {}).get('src'))
            ok = state[0] == 'ok'
            excluded = bool(ok and excluded_source(
                url=state[2] or '', excluded_accounts=self._selection_exclusions))
            photo_id = self._default_assignments.get(track_id) if not ok else None
            photo = self._defaults['photos'].get(photo_id) if photo_id is not None else None
            edge = edge or not ok and photo is None
            rows.append((track_id, entry, state, excluded, photo_id, photo))
        return tuple(rows),edge

    @_art_read
    def selection_token(self, track_ids) -> ArtworkSelection:
        """Capture only selected canonical owners/policy, without filesystem IO."""
        rows,edge=self._selection_rows(track_ids)
        pool = tuple(self._defaults['photos'].items()) if edge else ()
        return ArtworkSelection(self, self._area.generation, rows, pool,
                                self._defaults['seed'] if edge else '')

    def _selection_pool_current(self, token: ArtworkSelection) -> bool:
        # Canonical mutations detach the photos map under the area lock. One
        # exact comparison per immutable owner covers all indexed row tokens;
        # strong owners prevent object-ID reuse and rollback retains its map.
        photos=self._defaults['photos'];seed=self._defaults['seed']
        cached=getattr(self,'_selection_pool_check',None)
        if (cached is not None and cached[0] is token._edge_pool and cached[1] is photos
                and cached[2]==token._edge_seed and cached[3]==seed):return bool(cached[4])
        valid=(seed==token._edge_seed and len(photos)==len(token._edge_pool)
               and all(key==old_key and owner is old_owner for (key,owner),(old_key,old_owner)
                       in zip(photos.items(),token._edge_pool)))
        self._selection_pool_check=(token._edge_pool,photos,token._edge_seed,seed,valid)
        return valid

    @_art_read
    def selection_current(self, token: ArtworkSelection) -> bool:
        """Check selected intent on any thread; never read, decode or hash images."""
        if (not isinstance(token, ArtworkSelection) or token._cache is not self
                or token._generation != self._generation or self._generation != self._area.generation
                or self._revision != self._area.art_revision or getattr(self, '_writing', False)):
            return False
        rows,edge=self._selection_rows(row[0] for row in token._rows)
        return ((self._selection_pool_current(token) if edge else
                 not token._edge_pool and token._edge_seed=='')
                and all(row[0] == old[0] and row[1] is old[1] and row[2:5] == old[2:5]
                        and row[5] is old[5] for row, old in zip(rows, token._rows)))

    @_art_read
    def split_selection(self, token: ArtworkSelection) -> dict[str, ArtworkSelection]:
        """Index captured single-track intents in one pass, sharing fallback data.

        Splitting grants no freshness: each returned token still uses
        ``selection_current``. A changed track can fail independently while
        unchanged tracks retain their original selected intent.
        """
        if (not isinstance(token, ArtworkSelection) or token._cache is not self
                or token._generation != self._generation or self._generation != self._area.generation
                or self._revision != self._area.art_revision or getattr(self, '_writing', False)):
            raise ValueError('Artwork selection no longer belongs to the current cache generation')
        return {row[0]: ArtworkSelection(self, token._generation, (row,),
                    token._edge_pool if row[2][0] != 'ok' and row[5] is None else (),
                    token._edge_seed if row[2][0] != 'ok' and row[5] is None else '')
                for row in token._rows}

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

    def _present(self, path: Path, presence: dict[Path, bool] | None = None) -> bool:
        relative = str(path.relative_to(self._area.root))
        if relative in self._changes:
            return self._changes[relative] is not None
        return _file_exists(path, presence)

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

    def _valid_default_ids(self, presence: dict[Path, bool] | None = None) -> list[str]:
        return [
            pid for pid in self._defaults["photos"]
            if self._present(self._default_photo_path(pid), presence)
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
        return self._default_image_path(track_id)

    def _default_image_path(
            self, track_id: str, *, presence: dict[Path, bool] | None = None,
            pool: dict[str, tuple[str, ...]] | None = None) -> Path | None:
        if not self._is_default_eligible(track_id):
            return None
        photo_id = self._default_assignments.get(track_id)
        if photo_id is not None:
            path = self._default_photo_path(photo_id)
            if _file_exists(path, presence):
                return path
        photos = pool.get('ids') if pool is not None else None
        if photos is None:
            photos = tuple(sorted(self._valid_default_ids(presence)))
            if pool is not None:
                pool['ids'] = photos
        if not photos:
            return None
        # Safe edge fallback for a caller outside the configured live library
        # (or a pool file removed outside SoundBox). The desktop path is
        # preconfigured and therefore uses the exact-balanced map above.
        seed = self._defaults["seed"]
        digest = hashlib.sha256(
            f"{seed}\0edge\0{track_id}".encode("utf-8")).digest()
        photo_id = photos[int.from_bytes(digest[:8], "big") % len(photos)]
        path = self._default_photo_path(photo_id)
        return path if _file_exists(path, presence) else None

    @_art_read
    def status(self, track_id: str) -> str | None:
        return (self.index.get(track_id) or {}).get("status")  # "ok" | "none" | None

    @_art_read
    def status_kind(self, track_id: str) -> str:
        """UI status: cover/avatar/default/miss/blocked/unfetched."""
        exclusions = (self.source_exclusions()
                      if (self.index.get(track_id) or {}).get('status') == 'ok' else ())
        return self._status_kind(track_id, exclusions)

    @_art_read
    def status_kinds(self, track_ids) -> dict[str, str]:
        """One guarded policy snapshot for a presentation batch; never write authority."""
        track_ids = tuple(track_ids)
        exclusions = (self.source_exclusions() if any(
            (self.index.get(tid) or {}).get('status') == 'ok' for tid in track_ids) else ())
        # Thousands of rows can share the same few fallback photos. Observe
        # their availability once per presentation batch, never across batches
        # or in the standalone image/export readers.
        presence: dict[Path, bool] = {}
        pool: dict[str, tuple[str, ...]] = {}
        return {tid: self._status_kind(tid, exclusions, presence=presence, pool=pool)
                for tid in track_ids}

    def _status_kind(
            self, track_id: str, exclusions: tuple[str, ...], *,
            presence: dict[Path, bool] | None = None,
            pool: dict[str, tuple[str, ...]] | None = None) -> str:
        from .art_sources import excluded_source
        e = self.index.get(track_id) or {}
        if (e or {}).get('status') == 'ok' and excluded_source(
                url=e.get('src', ''), excluded_accounts=exclusions):
            return "blocked"  # quarantined, without rewriting the historical row
        if (e or {}).get("status") == "ok":
            return "avatar" if self._is_avatar(e) else "cover"
        if self._default_image_path(track_id, presence=presence, pool=pool) is not None:
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
        if self.source_excluded(track_id):
            return None
        return self._stored_image_path(track_id)

    def _stored_image_path(self, track_id: str) -> Path | None:
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
        from .art_sources import excluded_source
        if excluded_source(url=res.source_url, excluded_accounts=self.source_exclusions()):
            raise ValueError("This artwork uploader is excluded")
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

    def _rebalance_unpublished(self) -> bool:
        before = self._defaults["assignments"]
        self._rebuild_default_assignments(persist=False)
        after = self._defaults["assignments"]
        # Match the ordinary rebuilder's decision to create/update its manifest,
        # including an externally absent manifest and an empty final pool.
        return after != before and (bool(after) or self._defaults_index_path.exists())

    @_art_mutation
    def store_many(self, track_ids, res: ArtResult, *, origin: str = "manual",
                   avatar_only: bool = False) -> list[str]:
        """Normalize one picture and publish the affected group atomically."""
        from .art_sources import excluded_source
        identities = tuple(dict.fromkeys(track_ids))
        if not identities:
            return []
        exclusions = self.source_exclusions()
        affected = [tid for tid in identities if not avatar_only or
                    (self._is_avatar(self.index.get(tid) or {}) and
                     self._status_kind(tid, exclusions) == "avatar")]
        if not affected:
            return []
        if excluded_source(url=res.source_url, excluded_accounts=exclusions):
            raise ValueError("This artwork uploader is excluded")
        from . import artencode
        # Every staged path shares this one immutable normalized owner; staging
        # does not retain one decoded image or independent payload per song.
        data = bytes(artencode.ensure_device_safe(res.data))
        ext = "jpg" if artencode.is_device_safe(data) else ("png" if "png" in res.mime else "jpg")
        defaults_changed = False
        for index, tid in enumerate(affected):
            was_eligible = self._is_default_eligible(tid)
            self._write(self.root / f"{tid}.{ext}", data)
            self.index[tid] = {"status": "ok", "ext": ext,
                               "src": res.source_url, "conf": round(res.confidence, 2),
                               "origin": "auto" if origin == "auto" else "manual"}
            # Stable balancing is path-dependent when eligibility changes.
            # Preserve those in-memory steps; unchanged avatar replacements
            # need only the first idempotent balance, never repeated JSON saves.
            if index == 0 or was_eligible:
                defaults_changed = self._rebalance_unpublished() or defaults_changed
        self._save()
        if defaults_changed:
            self._save_defaults()
        return affected

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
            self.index[tid] = {**e, "ext": "jpg"}
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
    def store_auto_if_unfetched(self, track_id: str, res: ArtResult | None) -> bool:
        """Publish a background result only while the track is still unfetched.

        A manual edit, removal or miss recorded during the network request wins.
        The check and publication share the state lock, including other caches.
        """
        if track_id in self.index:
            return False
        from .art_sources import excluded_source
        if res is not None and excluded_source(
                url=res.source_url, excluded_accounts=self.source_exclusions()):
            return False
        if res is None:
            self.store_none(track_id)
        else:
            self.store_ok(track_id, res, origin="auto", rebalance_defaults=False)
        return True

    @_art_read
    def cover_snapshot(self, track_id: str) -> CoverSnapshot:
        # Quarantined bytes remain available for exact recovery, never display/export.
        path = self._stored_image_path(track_id)
        if path is None:
            raise ValueError("This track no longer has a cover to review")
        return CoverSnapshot(copy.deepcopy(self.index[track_id]), path.read_bytes())

    @_art_read
    def export_image_snapshot(self, track_id: str) -> bytes | None:
        path = self.image_path(track_id)
        return path.read_bytes() if path is not None else None

    def _review_paths(self, track_id: str) -> tuple[Path, Path]:
        if not track_id or Path(track_id).name != track_id or track_id in (".", ".."):
            raise ValueError("Invalid artwork track ID")
        paths = (self.root / "review" / f"{track_id}.json",
                 self.root / "review" / f"{track_id}.image")
        for path in paths:
            self._area.path(str(path.relative_to(self._area.root)))
        return paths

    def _require_cover(self, track_id: str, expected: CoverSnapshot) -> None:
        current = self.cover_snapshot(track_id)
        if current.entry != expected.entry or current.digest != expected.digest:
            raise ValueError("The cover changed since it was displayed. Select the track again.")

    @_art_read
    def has_review_undo(self, track_id: str) -> bool:
        receipt, _ = self._review_paths(track_id)
        return receipt.is_file()

    @_art_read
    def source_exclusions(self) -> tuple[str, ...]:
        path = self.root / "source-policy.json"
        self._area.path(str(path.relative_to(self._area.root)))
        if not path.exists():
            return ()
        from .art_sources import parse_exclusions
        return parse_exclusions(json.loads(path.read_text()))

    @_art_read
    def source_excluded(self, track_id: str) -> bool:
        from .art_sources import excluded_source
        entry = self.index.get(track_id) or {}
        return entry.get("status") == "ok" and excluded_source(
            url=entry.get("src", ""), excluded_accounts=self.source_exclusions())

    @_art_read
    def source_excluded_ids(self, track_ids) -> frozenset[str]:
        """One current policy observation for read-only batch eligibility.

        Publication still checks the current policy and cover under its own
        mutation lock; this observation is never approval to replace artwork.
        """
        from .art_sources import excluded_source
        entries = [(tid, self.index.get(tid) or {}) for tid in track_ids]
        entries = [(tid, entry) for tid, entry in entries if entry.get("status") == "ok"]
        if not entries:
            return frozenset()
        exclusions = self.source_exclusions()
        return frozenset(tid for tid, entry in entries if excluded_source(
            url=entry.get("src", ""), excluded_accounts=exclusions))

    @_art_read
    def excluded_accounts(self) -> tuple[str, ...]:
        """Known blocked handles: explicit preferences and observed seller sources."""
        from .art_sources import account_name, excluded_source
        custom = self.source_exclusions()
        accounts = set(custom)
        entries = list(self.index.values())
        for path in (self.root / "source-recovery").glob("*.json"):
            self._area.path(str(path.relative_to(self._area.root)))
            value = json.loads(path.read_text())
            if isinstance(value, dict) and isinstance(value.get("before"), dict):
                entries.append(value["before"])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source = entry.get("src", "")
            if excluded_source(url=source, excluded_accounts=custom) and (account := account_name(source)):
                accounts.add(account)
        return tuple(sorted(accounts))

    @_art_read
    def kept_covers(self) -> dict:
        path = self.root / "review-kept.json"
        self._area.path(str(path.relative_to(self._area.root)))
        if not path.exists():
            return {}
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("Invalid artwork review decisions")
        return value

    @_art_mutation
    def keep_review(self, track_id: str, expected: CoverSnapshot, artist: str, title: str) -> None:
        self._require_cover(track_id, expected)
        kept = self.kept_covers()
        kept[track_id] = {"entry": expected.entry, "digest": expected.digest, "artist": artist, "title": title}
        self._write(self.root / "review-kept.json", json.dumps(kept, ensure_ascii=False).encode())

    @_art_mutation
    def apply_excluded_profile(self, track_id: str, expected: CoverSnapshot,
                               res: ArtResult, artist: str) -> bool:
        """Authorized source policy: hide seller art, use a verified artist avatar.

        Preserve the historical bytes/index and any existing review recovery in
        a separate small transaction, so later human Undo restores the profile
        cover and never silently restores a quarantined seller image.
        """
        from . import artencode
        from .art_sources import profile_source
        current = self.index.get(track_id) or {}
        if not self.source_excluded(track_id) and current.get("fallback") != "excluded-source":
            return False  # a manual edit during the request wins
        self._require_cover(track_id, expected)
        if not profile_source(res.source_url) or res.confidence < .9 or not artist.strip():
            return False
        safe = artencode.ensure_device_safe(res.data)
        if not artencode.is_device_safe(safe):
            return False
        receipt, original = self._review_paths(track_id)
        recovery = self.root / "source-recovery"
        policy_receipt = recovery / receipt.name
        policy_image = recovery / original.name
        for path in (policy_receipt, policy_image):
            self._area.path(str(path.relative_to(self._area.root)))
        if not policy_receipt.exists():
            self._write(policy_image, expected.data)
            self._write(policy_receipt, json.dumps({
                "before": expected.entry, "before_sha256": expected.digest,
                "reason": "excluded-source", "artist": artist}, ensure_ascii=False).encode())
        else:
            prior = json.loads(policy_receipt.read_text())
            if hashlib.sha256(policy_image.read_bytes()).hexdigest() != prior["before_sha256"]:
                raise ValueError("Source recovery image changed; profile replacement stopped")
        if receipt.exists():
            archive_receipt = recovery / f"{track_id}.review.json"
            archive_image = recovery / f"{track_id}.review.image"
            if archive_receipt.exists() or archive_image.exists():
                raise ValueError("Existing source/review recovery needs inspection")
            self._write(archive_receipt, receipt.read_bytes())
            self._write(archive_image, original.read_bytes())
            self._write(receipt, None)
            self._write(original, None)
        self.store_ok(track_id, ArtResult(safe, "image/jpeg", res.confidence,
                                          res.source_url, res.matched_title), origin="auto")
        self.index[track_id].update(fallback="excluded-source", fallback_artist=artist)
        self._save()
        if expected.entry.get("ext", "jpg") != "jpg":
            self._write(self.root / f"{track_id}.{expected.entry['ext']}", None)
        return True

    @_art_mutation
    def exclude_source(self, url: str) -> str:
        """Persist an explicit uploader exclusion, without changing any cover."""
        from .art_sources import account_name
        account = account_name(url)
        if not account:
            raise ValueError("This suggestion has no SoundCloud uploader account")
        accounts = sorted(set(self.source_exclusions()) | {account})
        self._write(self.root / "source-policy.json", json.dumps(
            {"schema": 1, "accounts": accounts}, ensure_ascii=False).encode())
        self._selection_exclusions = tuple(accounts)
        return account

    @_art_mutation
    def allow_source(self, account: str) -> None:
        accounts = sorted(set(self.source_exclusions()) - {account})
        self._write(self.root / "source-policy.json", json.dumps(
            {"schema": 1, "accounts": accounts}, ensure_ascii=False).encode())
        self._selection_exclusions = tuple(accounts)

    @_art_mutation
    def apply_review(self, track_id: str, expected: CoverSnapshot, res: ArtResult, *,
                     context: tuple[str, str] | None = None) -> None:
        """Explicit approval only. Preserve one exact original per changed track.

        Receipt, recovery bytes and new cover publish in one state transaction.
        Further review replacements retain the first original, without a chain
        of images. External edits invalidate that recovery instead of overwriting it.
        """
        from . import artencode
        self._require_cover(track_id, expected)
        safe = artencode.ensure_device_safe(res.data)
        if not artencode.is_device_safe(safe):
            raise ValueError("The suggested image could not be decoded")
        receipt, original = self._review_paths(track_id)
        if receipt.exists():
            recovery = json.loads(receipt.read_text())
            if (recovery["after"] != expected.entry or
                    recovery["after_sha256"] != expected.digest or
                    hashlib.sha256(original.read_bytes()).hexdigest() != recovery["before_sha256"]):
                raise ValueError("An earlier review recovery no longer matches. No artwork was changed.")
        else:
            recovery = {"before": expected.entry, "before_sha256": expected.digest}
            self._write(original, expected.data)
        self.store_ok(track_id, ArtResult(safe, "image/jpeg", res.confidence,
                                         res.source_url, res.matched_title))
        if expected.entry.get("ext", "jpg") != "jpg":
            self._write(self.root / f"{track_id}.{expected.entry['ext']}", None)
        recovery.update(after=copy.deepcopy(self.index[track_id]),
                        after_sha256=hashlib.sha256(safe).hexdigest())
        self._write(receipt, json.dumps(recovery, ensure_ascii=False).encode())
        if context is not None:
            kept = self.kept_covers()
            kept[track_id] = {"entry": copy.deepcopy(self.index[track_id]),
                              "digest": recovery["after_sha256"], "artist": context[0], "title": context[1]}
            self._write(self.root / "review-kept.json", json.dumps(kept, ensure_ascii=False).encode())

    @_art_mutation
    def undo_review(self, track_id: str, expected: CoverSnapshot) -> None:
        """Restore the exact pre-review bytes/metadata; refuse stale recovery."""
        self._require_cover(track_id, expected)
        receipt, original = self._review_paths(track_id)
        recovery = json.loads(receipt.read_text())
        data = original.read_bytes()
        if (recovery["after"] != expected.entry or
                recovery["after_sha256"] != expected.digest or
                hashlib.sha256(data).hexdigest() != recovery["before_sha256"]):
            raise ValueError("The cover or its Undo copy changed. No artwork was changed.")
        before = recovery["before"]
        old_path = self.root / f"{track_id}.{before.get('ext', 'jpg')}"
        if old_path.name != f"{track_id}.{before.get('ext', 'jpg')}":
            raise ValueError("Invalid recovery image path")
        self._write(old_path, data)
        if expected.entry.get("ext", "jpg") != before.get("ext", "jpg"):
            self._write(self.root / f"{track_id}.{expected.entry.get('ext', 'jpg')}", None)
        self.index[track_id] = before
        self._save()
        self._write(receipt, None)
        self._write(original, None)

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
        """Retry misses and explicitly automatic avatars only.

        Manual and historically unrecorded images are protected, including
        profile pictures. Confidence and URL shape cannot prove user intent.
        """
        weak = [tid for tid, e in self.index.items()
                if (e or {}).get("status") == "none" or
                (e.get("origin") == "auto" and e.get("fallback") != "excluded-source" and self._is_avatar(e))]
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
    def block_many(self, track_ids, *, avatar_only: bool = False) -> list[str]:
        """Block a group in one recovery-backed transaction, keeping newer covers."""
        identities = tuple(dict.fromkeys(track_ids))
        exclusions = self.source_exclusions() if avatar_only and identities else ()
        affected = [tid for tid in identities if not avatar_only or
                    (self._is_avatar(self.index.get(tid) or {}) and
                     self._status_kind(tid, exclusions) == "avatar")]
        defaults_changed = False
        for index, tid in enumerate(affected):
            entry = self.index.get(tid) or {}
            was_eligible = self._is_default_eligible(tid)
            if entry.get("ext"):
                self._write(self.root / f"{tid}.{entry['ext']}", None)
            self.index[tid] = {"status": "blocked"}
            if index == 0 or not was_eligible:
                defaults_changed = self._rebalance_unpublished() or defaults_changed
        if affected:
            self._save()
            if defaults_changed:
                self._save_defaults()
        return affected

    @_art_mutation
    def _save(self):
        self._write(self._index_path, json.dumps(self.index).encode("utf-8"))
