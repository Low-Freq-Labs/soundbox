"""Composite single-copy stock + Rockbox planning for a dual-boot MA446 iPod.

The stock-safe subset lives once below ``iPod_Control/Music`` and is indexed by
Apple's database. Rockbox-only media lives once below ``/Music``. Rockbox m3u8
playlists span both locations. Per-file external artwork provides exact fast
paths for known viewports plus a dimensionless full-screen-height fallback so
switching themes never requires a new source-art strategy.

Planning never writes the target. It may prepare disposable, content-addressed
Mac-side media/art derivatives. Commit copies unreferenced Rockbox files first,
publishes the recovery-first stock transaction, publishes Rockbox playlists,
and writes the unified manifest last. A post-stock failure rolls the stock phase
back from its exact recovery point and removes only this plan's new files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, cast

from . import artencode
from . import metadata
from . import manifest as legacy_manifest
from . import playlists as pl_mod
from . import rockbox_config
from . import sync as sync_mod
from .library import Library, Playlist
from .rockbox_media import (
    InterruptedFileVerifier,
    NORMALIZED_MEDIA_PROFILE,
    PlannedRockboxMedia,
    RockboxArtworkCache,
    RockboxMediaCache,
)
from .stock_artwork import StockArtworkCache
from .stock_ipod_shadow import StockIpodShadowStore
from .stock_ipod import (
    StockIpodCommitResult,
    StockIpodSyncPlan,
    commit_stock_ipod_sync,
    plan_stock_ipod_sync,
    rollback_committed_stock_ipod_sync,
)


DUAL_MANIFEST_SCHEMA = legacy_manifest.COMPOSITE_SCHEMA
DUAL_EXPORT_PROFILE = legacy_manifest.COMPOSITE_EXPORT_PROFILE
ROCKBOX_SUPPORTED_SUFFIXES = frozenset({
    ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".wav",
})
# Rockbox's Playlist Catalogue can sort by modification date but has no
# independent playlist display-title metadata. Stable, spaced FAT-safe mtimes
# let its visible leaf filenames follow the same manifest sequence as iTunesDB.
_PLAYLIST_ORDER_EPOCH = 946_684_800  # 2000-01-01 UTC; representable by FAT32
_PLAYLIST_ORDER_STEP = 4             # wider than FAT's two-second granularity
_PLAYLIST_MTIME_TOLERANCE = 2.1
_MAX_MANAGED_PLAYLIST_BYTES = 1024 * 1024


class DualIpodError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_exact_appledouble(
    session: sync_mod.DeviceSession,
    relative_paths: set[Path],
) -> int:
    """Remove only sidecars corresponding to files this transaction wrote."""
    removed = 0
    for relative in relative_paths:
        sidecar = relative.with_name(f"._{relative.name}")
        try:
            candidate = session.guard(sidecar)
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        except OSError:
            # The payload and manifest are already verified. Finder clutter is
            # non-critical and can be retried by the normal device cleaner.
            continue
    return removed


def _rockbox_clutter(root: Path) -> tuple[DualOrphanDelete, ...]:
    """Plan exact Mac sidecar/temp cleanup below Rockbox-managed roots.

    Finder can create AppleDouble entries for both payloads and their parent
    directories.  An exact-payload scrub therefore misses directory sidecars
    after the last managed track is removed.  These files are not media and can
    leave an otherwise empty Rockbox file tree looking populated.
    """
    output: list[DualOrphanDelete] = []
    for name in ("Music", "Playlists"):
        base = root / name
        if not base.exists():
            continue
        if base.is_symlink() or not base.is_dir():
            raise DualIpodError(f"Rockbox {name} root is not a regular directory")
        try:
            candidates = tuple(base.rglob("*"))
        except OSError as exc:
            raise DualIpodError(f"Could not inspect Rockbox {name} clutter") from exc
        for candidate in candidates:
            if not (
                candidate.name.startswith("._")
                or candidate.name.endswith(".sbpart")
            ):
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise DualIpodError(
                    f"Rockbox clutter path is not a regular file: {candidate}")
            output.append(DualOrphanDelete(
                candidate.relative_to(root),
                candidate.stat().st_size,
                _sha256(candidate),
                "rockbox-clutter",
            ))
    return tuple(output)


def _rockbox_interrupted_writes(root: Path) -> tuple[DualOrphanDelete, ...]:
    """Return incomplete SoundBox payload writes that require reconciliation.

    Finder may recreate ``._*`` AppleDouble metadata immediately after a
    successful export.  Those files are safe to remove opportunistically, but
    their presence cannot honestly mean the library is out of sync.  A leftover
    ``*.sbpart`` file, by contrast, is an interrupted SoundBox write and should
    keep requesting repair.
    """
    return tuple(
        item for item in _rockbox_clutter(root)
        if item.relative_path.name.endswith(".sbpart")
    )


def _clean_empty_rockbox_dirs(session: sync_mod.DeviceSession) -> int:
    """Best-effort removal of empty directories below /Music and /Playlists."""
    removed = 0
    for name in ("Music", "Playlists"):
        base = session.guard(Path(name))
        if not base.is_dir() or base.is_symlink():
            continue
        try:
            directories = sorted(
                (path for path in base.rglob("*")
                 if path.is_dir() and not path.is_symlink()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
        except OSError:
            continue
        for directory in directories:
            try:
                session.guard(directory.relative_to(session.target)).rmdir()
                removed += 1
            except OSError:
                continue
    return removed


@dataclass(frozen=True)
class DualOmission:
    track_id: str
    title: str
    source: Path
    reason: str


@dataclass(frozen=True)
class DualFileCopy:
    source: Path
    relative_path: Path
    size: int
    sha256: str
    kind: str
    media_plan: PlannedRockboxMedia | None = field(
        default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DualFileAdoption:
    """One exact completed payload recovered from an interrupted transaction.

    The file existed before this retry began, so rollback must leave it in
    place.  It becomes owned only when the retry publishes its manifest last.
    """

    relative_path: Path
    size: int
    sha256: str
    kind: str


@dataclass(frozen=True)
class DualFileRewrite:
    source: Path
    relative_path: Path
    size_before: int
    sha256_before: str
    size: int
    sha256: str
    kind: str
    media_plan: PlannedRockboxMedia | None = field(
        default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DualFileDelete:
    relative_path: Path
    size_before: int
    sha256_before: str
    kind: str


@dataclass(frozen=True)
class DualOrphanDelete:
    """One unreferenced managed payload removed transactionally."""

    relative_path: Path
    size_before: int
    sha256_before: str
    kind: str


@dataclass(frozen=True)
class DualPlaylistWrite:
    relative_path: Path
    payload: bytes = field(repr=False)
    sha256: str
    playlist_key: str
    desired_mtime: float
    changed: bool = True
    size_before: int | None = None
    sha256_before: str | None = None
    mtime_before: float | None = None
    order_changed: bool = True


@dataclass(frozen=True)
class DualPlaylistAddition:
    """Recognized Rockbox membership to persist after a successful commit."""

    playlist_key: str
    track_ids: tuple[str, ...]


@dataclass(frozen=True)
class DualIpodSyncPlan:
    target: Path
    stock: StockIpodSyncPlan
    media_copies: tuple[DualFileCopy, ...]
    artwork_copies: tuple[DualFileCopy, ...]
    file_adoptions: tuple[DualFileAdoption, ...]
    file_rewrites: tuple[DualFileRewrite, ...]
    file_deletes: tuple[DualFileDelete, ...]
    orphan_deletes: tuple[DualOrphanDelete, ...]
    playlists: tuple[DualPlaylistWrite, ...]
    media_plans: tuple[PlannedRockboxMedia, ...]
    config_update: rockbox_config.RockboxConfigPlan
    manifest_payload: bytes = field(repr=False)
    manifest_payload_before: bytes | None = field(default=None, repr=False)
    manifest_sha256_before: str | None = None
    playlist_additions: tuple[DualPlaylistAddition, ...] = ()
    missing: tuple[DualOmission, ...] = ()
    unsupported: tuple[DualOmission, ...] = ()
    stock_safe_sources: int = 0
    rockbox_only_sources: int = 0
    sanitized_sources: int = 0
    generation: int = 1
    retained_stale_files: int = 0
    rockbox_media_cache: RockboxMediaCache | None = field(
        default=None, repr=False, compare=False)
    interrupted_verifier: InterruptedFileVerifier | None = field(
        default=None, repr=False, compare=False)

    @property
    def rockbox_bytes_to_copy(self) -> int:
        return (
            sum(item.size for item in self.media_copies + self.artwork_copies)
            + sum(item.size for item in self.file_rewrites)
        )

    @property
    def recovered_bytes(self) -> int:
        return sum(item.size for item in self.file_adoptions)

    @property
    def manifest_changed(self) -> bool:
        return hashlib.sha256(self.manifest_payload).hexdigest() != (
            self.manifest_sha256_before or "")

    @property
    def progress_total(self) -> int:
        """Number of managed audio files this commit will verify or mutate."""
        return (
            sum(
                1 for item in self.file_adoptions
                if item.kind == "rockbox-media"
            )
            + sum(
                1 for item in self.media_copies
                if item.kind == "rockbox-media"
            )
            + sum(
                1 for item in self.file_rewrites
                if item.kind == "rockbox-media"
            )
            + len(self.stock.copies)
            + len(self.stock.rewrites)
            + sum(
                1 for item in self.orphan_deletes
                if item.kind in {"shared-stock-media", "rockbox-media"}
            )
        )

    @property
    def requires_commit(self) -> bool:
        return bool(
            self.stock.requires_commit
            or self.media_copies
            or self.artwork_copies
            or self.file_adoptions
            or self.file_rewrites
            or self.file_deletes
            or self.orphan_deletes
            or any(item.changed for item in self.playlists)
            or any(item.order_changed for item in self.playlists)
            or self.config_update.changed
            or self.manifest_changed
        )

    def summary(self) -> dict[str, Any]:
        stock = self.stock.summary()
        return {
            "profile": DUAL_EXPORT_PROFILE,
            "stock_safe_sources": self.stock_safe_sources,
            "rockbox_only_sources": self.rockbox_only_sources,
            "missing_sources": len(self.missing),
            "unsupported_sources": len(self.unsupported),
            "sanitized_sources": self.sanitized_sources,
            "stock_new_tracks": stock["new_tracks"],
            "recovered_stock_tracks": stock["recovered_tracks"],
            "stock_removed_tracks": stock["removed_tracks"],
            "stock_rewritten_tracks": stock["rewritten_tracks"],
            "stock_reused_tracks": stock["reused_tracks"],
            "stock_final_tracks": stock["final_tracks"],
            "stock_bytes_to_copy": stock["bytes_to_copy"],
            "stock_artwork_bytes": stock["artwork_bytes_to_copy"],
            "rockbox_media_files": len(self.media_copies),
            "rockbox_artwork_files": len(self.artwork_copies),
            "recovered_rockbox_media_files": sum(
                1 for item in self.file_adoptions
                if item.kind == "rockbox-media"),
            "recovered_rockbox_artwork_files": sum(
                1 for item in self.file_adoptions
                if item.kind == "rockbox-artwork"),
            "recovered_rockbox_bytes": self.recovered_bytes,
            "rockbox_rewritten_files": len(self.file_rewrites),
            "rockbox_deleted_playlists": sum(
                1 for item in self.file_deletes if item.kind == "playlist"),
            "orphan_media_files": sum(
                1 for item in self.orphan_deletes
                if item.kind in {"shared-stock-media", "rockbox-media"}),
            "orphan_artwork_files": sum(
                1 for item in self.orphan_deletes
                if item.kind == "rockbox-artwork") + sum(
                1 for item in self.file_deletes if item.kind == "stock-artwork"),
            "rockbox_clutter_files": sum(
                1 for item in self.orphan_deletes
                if item.kind == "rockbox-clutter"),
            "orphan_bytes_to_delete": sum(
                item.size_before for item in self.orphan_deletes) + sum(
                item.size_before for item in self.file_deletes
                if item.kind == "stock-artwork"),
            "rockbox_bytes_to_copy": self.rockbox_bytes_to_copy,
            "rockbox_playlists": len(self.playlists),
            "rockbox_playlist_writes": sum(
                1 for item in self.playlists if item.changed),
            "rockbox_settings_updated": self.config_update.changed,
            "generation": self.generation,
            "retained_stale_files": self.retained_stale_files,
        }


@dataclass(frozen=True)
class DualIpodCommitResult:
    stock: StockIpodCommitResult
    copied_media: int
    copied_artwork: int
    recovered_media: int
    recovered_artwork: int
    playlists: int
    manifest_sha256: str
    deleted_orphans: int = 0
    deleted_orphan_bytes: int = 0
    cleanup_failures: tuple[str, ...] = ()


def _matching_interrupted_recovery(
    root: Path,
    manifest: dict[str, Any] | None,
    manifest_sha256: str | None,
    _expected: dict[str, int],
) -> Path | None:
    """Find a failed generation that can authorize exact-file adoption.

    A successful generation changes the manifest hash, so only a recovery
    directory named for the still-current baseline can qualify. The requested
    playlist set is intentionally allowed to change between attempts: only the
    overlap whose freshly planned path, size, and SHA-256 match is adopted.
    """
    if manifest is None or manifest_sha256 is None:
        return None
    recovery_root = root / ".soundbox" / "dual-recovery"
    if not recovery_root.is_dir() or recovery_root.is_symlink():
        return None
    generation = int(manifest.get("generation") or 0) + 1
    suffix = f"-{manifest_sha256[:12]}"
    try:
        candidates = sorted(
            (
                item for item in recovery_root.iterdir()
                if item.name.endswith(suffix)
                and item.is_dir()
                and not item.is_symlink()
            ),
            key=lambda item: item.name,
            reverse=True,
        )
    except OSError:
        return None
    for candidate in candidates:
        journal = candidate / "plan.json"
        if not journal.is_file() or journal.is_symlink():
            continue
        try:
            raw = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if raw.get("profile") != DUAL_EXPORT_PROFILE:
            continue
        if int(raw.get("generation") or 0) != generation:
            continue
        recorded_baseline = str(raw.get("baseline_manifest_sha256") or "")
        if recorded_baseline and recorded_baseline != manifest_sha256:
            continue
        return candidate
    return None


def _plan_recoverable_file(
    live: Path,
    *,
    relative_path: Path,
    size: int,
    sha256: str,
    kind: str,
) -> DualFileAdoption:
    """Identify a recovery candidate without streaming it during planning.

    The exact SHA-256 check is intentionally deferred to the commit preflight,
    before the first device write.  Planning needs only enough information to
    reject an obvious conflict and present the confirmation promptly.
    """
    if live.is_symlink() or not live.is_file():
        raise DualIpodError(
            f"Interrupted {kind} payload is not a regular file: {live}")
    if live.stat().st_size != size:
        raise DualIpodError(
            f"Interrupted {kind} payload size does not match the fresh plan: "
            f"{live}")
    return DualFileAdoption(relative_path, size, sha256, kind)


def _read_existing_manifest(
    root: Path,
) -> tuple[dict[str, Any] | None, bytes | None, str | None]:
    path = legacy_manifest.manifest_path(root)
    if not path.exists():
        return None, None, None
    if not path.is_file():
        raise DualIpodError("SoundBox manifest path is not a regular file")
    try:
        payload = path.read_bytes()
        data = legacy_manifest.load_composite(root)
    except (OSError, ValueError) as exc:
        raise DualIpodError("Existing SoundBox manifest is unreadable") from exc
    return data, payload, hashlib.sha256(payload).hexdigest()


def composite_device_drift(
    library: Library,
    target: str | Path,
    extra: list[Playlist] | None = None,
    *,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
) -> legacy_manifest.Drift | None:
    """Diff schema-3 ownership against the currently exportable Rockbox view."""
    root = Path(target)
    manifest = legacy_manifest.load_composite(root)
    manifest_path = legacy_manifest.manifest_path(root)
    manifest_sha256 = (
        _sha256(manifest_path) if manifest_path.is_file() else None)
    interrupted_recovery = _matching_interrupted_recovery(
        root, manifest, manifest_sha256, {})
    if not manifest.get("playlists"):
        if interrupted_recovery is not None or _rockbox_interrupted_writes(root):
            drift = legacy_manifest.Drift()
            drift.changed.append((
                "interrupted-transaction",
                "Interrupted iPod sync recovery",
            ))
            return drift
        return None
    current = list(library.playlists) + list(extra or [])
    effective, _missing, _unsupported = _filtered_playlists(library, current)
    library_count = len(library.playlists)
    filtered_library = Library(
        library.tracks_by_id,
        effective[:library_count],
        library.source,
        source_kind=library.source_kind,
    )
    drift = legacy_manifest.diff(
        manifest,
        filtered_library,
        effective[library_count:],
        expected_profile=DUAL_EXPORT_PROFILE,
    )
    effective_by_key = {
        legacy_manifest.playlist_key(playlist): playlist
        for playlist in effective
    }
    changed_keys = {str(key) for key, *_rest in drift.changed}
    for key, raw in (manifest.get("playlists") or {}).items():
        playlist = effective_by_key.get(str(key))
        if playlist is None or str(key) in changed_keys or not isinstance(raw, dict):
            continue
        current_entry = _composite_entry_for_effective_playlist(
            library,
            playlist,
            artist_overrides=artist_overrides,
            title_overrides=title_overrides,
        )
        if current_entry.get("metadata_sig") != raw.get("metadata_sig"):
            drift.changed.append((str(key), playlist.name))
            changed_keys.add(str(key))
    for key in _changed_managed_playlist_keys(root, manifest):
        if key in changed_keys:
            continue
        playlist_row = (manifest.get("playlists") or {}).get(key) or {}
        drift.changed.append((
            key,
            str(playlist_row.get("name") or "Rockbox playlist"),
        ))
        changed_keys.add(key)
    baseline_upgrade_needed = False
    for raw in (manifest.get("files") or {}).values():
        if (
            not isinstance(raw, dict)
            or raw.get("kind") not in {"shared-stock-media", "rockbox-media"}
        ):
            continue
        profile_changed = (
            str(raw.get("media_profile") or "")
            != NORMALIZED_MEDIA_PROFILE
        )
        required = (
            raw.get("source_size"),
            raw.get("source_mtime_ns"),
            raw.get("source_ctime_ns"),
        )
        if any(value is None for value in required):
            baseline_upgrade_needed = True
            continue
        try:
            source = Path(str(raw.get("source") or "")).resolve(strict=True)
            stat = source.stat()
            source_changed = (
                stat.st_size != int(str(required[0]))
                or stat.st_mtime_ns != int(str(required[1]))
                or stat.st_ctime_ns != int(str(required[2]))
            )
        except (OSError, TypeError, ValueError):
            source_changed = True
        if not source_changed and not profile_changed:
            continue
        owners = [str(value) for value in (raw.get("playlists") or ())]
        if not owners:
            owners = ["managed-media"]
        for key in owners:
            if key in changed_keys:
                continue
            playlist_row = (manifest.get("playlists") or {}).get(key) or {}
            drift.changed.append((
                key,
                str(playlist_row.get("name") or "Managed media"),
            ))
            changed_keys.add(key)
    if baseline_upgrade_needed and "media-baseline" not in changed_keys:
        drift.changed.append((
            "media-baseline",
            "Incremental media baseline",
        ))
    active_art_sides = set(rockbox_config.theme_album_art_sides(root))
    managed_art_sides = {
        int(side) for side in (manifest.get("rockbox_artwork_sides") or ())
        if str(side).isdigit() and 1 <= int(side) <= 240
    }
    if not active_art_sides.issubset(managed_art_sides):
        drift.changed.append(("rockbox-theme-art", "Rockbox theme artwork"))
    if not rockbox_config.settings_ready(root):
        drift.changed.append(("rockbox-settings", "Rockbox settings"))
    if interrupted_recovery is not None:
        drift.changed.append((
            "interrupted-transaction",
            "Interrupted iPod sync recovery",
        ))
    if _rockbox_interrupted_writes(root):
        drift.changed.append(("rockbox-clutter", "Rockbox cleanup"))
    return drift


def _relative_manifest_path(device_path: str) -> Path:
    text = str(device_path or "").replace("\\", "/")
    if not text.startswith("/"):
        raise DualIpodError(f"Manifest path is not device-absolute: {device_path!r}")
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise DualIpodError(f"Manifest path is unsafe: {device_path!r}")
    return Path(*parts)


def _managed_playlist_rows(
    manifest: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Index the one owned M3U8 row allowed for each playlist identity."""
    output: dict[str, tuple[str, dict[str, Any]]] = {}
    for device_path, raw in (manifest.get("files") or {}).items():
        if not isinstance(raw, dict) or raw.get("kind") != "playlist":
            continue
        identity = str(raw.get("source") or "")
        if not identity or identity in output or not bool(raw.get("owned")):
            raise DualIpodError(
                f"Composite manifest has ambiguous playlist ownership for "
                f"{identity!r}")
        if identity not in (manifest.get("playlists") or {}):
            raise DualIpodError(
                f"Composite playlist ownership has no snapshot: {identity!r}")
        _relative_manifest_path(str(device_path))
        output[identity] = (str(device_path), raw)
    return output


def _changed_managed_playlist_keys(
    root: Path,
    manifest: dict[str, Any],
) -> set[str]:
    """Find bounded M3U8 size drift without repeatedly hashing every playlist.

    Rockbox's catalogue append writes at least one new path line, so its size is
    sufficient for the frequent connection poll.  The confirmed planner later
    reads and hashes only changed small payloads before accepting anything.
    """
    changed: set[str] = set()
    for identity, (device_path, row) in _managed_playlist_rows(manifest).items():
        live = root / _relative_manifest_path(device_path)
        try:
            if live.is_symlink() or not live.is_file():
                changed.add(identity)
                continue
            size = live.stat().st_size
            expected = int(row.get("size") or -1)
        except (OSError, TypeError, ValueError):
            changed.add(identity)
            continue
        if size != expected:
            changed.add(identity)
    return changed


def _read_managed_m3u8(payload: bytes, *, playlist_key: str) -> list[str]:
    if len(payload) > _MAX_MANAGED_PLAYLIST_BYTES:
        raise DualIpodError(
            f"Rockbox playlist is too large to reconcile safely: {playlist_key}")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DualIpodError(
            f"Rockbox playlist is not valid UTF-8: {playlist_key}") from exc
    output: list[str] = []
    for raw in text.splitlines():
        value = raw.strip().replace("\\", "/")
        if not value or value.startswith("#"):
            continue
        if not value.startswith("/"):
            raise DualIpodError(
                f"Rockbox playlist contains a non-absolute path: {playlist_key}")
        parts = [part for part in value.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise DualIpodError(
                f"Rockbox playlist contains an unsafe path: {playlist_key}")
        output.append("/" + "/".join(parts))
    return output


def _merge_recognized_rockbox_additions(
    library: Library,
    playlists: list[Playlist],
    root: Path,
    manifest: dict[str, Any],
    *,
    adoptable_playlist_keys: set[str],
) -> tuple[
    tuple[DualPlaylistAddition, ...],
    dict[str, tuple[Path, int, str]],
]:
    """Adopt only append-only, manifest-owned Rockbox playlist additions.

    Unknown paths are deliberately not imports.  Appended duplicates are
    omitted from the desired playlist, causing the ordinary transaction to
    rewrite the M3U8 back to one occurrence.  The returned exact live baselines
    authorize only these already-parsed playlist files for this plan.
    """
    by_key = {
        legacy_manifest.playlist_key(playlist): playlist
        for playlist in playlists
    }
    track_by_source: dict[str, str | None] = {}
    available_track_ids: set[str] = set()
    for track_id, track in library.tracks_by_id.items():
        try:
            source = str(track.path.resolve(strict=False))
        except OSError:
            source = str(track.path)
        if source in track_by_source:
            track_by_source[source] = None
        else:
            track_by_source[source] = track_id
        try:
            if (
                track.path.is_file()
                and track.path.suffix.lower() in ROCKBOX_SUPPORTED_SUFFIXES
            ):
                available_track_ids.add(track_id)
        except OSError:
            pass

    track_by_device_path: dict[str, str] = {}
    for device_path, raw in (manifest.get("files") or {}).items():
        if (
            not isinstance(raw, dict)
            or raw.get("kind") not in {"shared-stock-media", "rockbox-media"}
            or not bool(raw.get("owned"))
        ):
            continue
        normalized = "/" + "/".join(
            _relative_manifest_path(str(device_path)).parts)
        source_text = str(raw.get("source") or "")
        try:
            source = str(Path(source_text).resolve(strict=False))
        except OSError:
            source = source_text
        mapped_track_id = track_by_source.get(source)
        if mapped_track_id:
            key = normalized.casefold()
            previous = track_by_device_path.get(key)
            if previous is not None and previous != mapped_track_id:
                raise DualIpodError(
                    f"Composite media path maps to two library tracks: {normalized}")
            track_by_device_path[key] = mapped_track_id

    additions: list[DualPlaylistAddition] = []
    approved: dict[str, tuple[Path, int, str]] = {}
    hidden = set(getattr(library, "hidden_ids", ()) or ())
    removals = getattr(library, "removed_by_playlist", {}) or {}
    for identity, (device_path, row) in _managed_playlist_rows(manifest).items():
        relative = _relative_manifest_path(device_path)
        live = root / relative
        try:
            if live.is_symlink() or not live.is_file():
                raise DualIpodError(
                    f"Managed Rockbox playlist is missing: {device_path}")
            size = live.stat().st_size
            if size > _MAX_MANAGED_PLAYLIST_BYTES:
                raise DualIpodError(
                    f"Rockbox playlist is too large to reconcile safely: {identity}")
            payload = live.read_bytes()
        except OSError as exc:
            raise DualIpodError(
                f"Could not read managed Rockbox playlist: {device_path}") from exc
        digest = hashlib.sha256(payload).hexdigest()
        try:
            expected_size = int(row.get("size") or -1)
        except (TypeError, ValueError) as exc:
            raise DualIpodError(
                f"Composite playlist baseline is invalid: {identity}") from exc
        expected_hash = str(row.get("sha256") or "")
        if size == expected_size and digest == expected_hash:
            continue

        playlist = by_key.get(identity)
        snapshot = (manifest.get("playlists") or {}).get(identity)
        if playlist is None:
            # Reconcile is authoritatively deleting this managed identity.  Its
            # exact current M3U8 is recovery-backed; no line is imported and no
            # unowned media path gains cleanup authority.
            approved[identity] = (relative, size, digest)
            continue
        if not isinstance(snapshot, dict):
            raise DualIpodError(
                f"Changed Rockbox playlist is unavailable in SoundBox: {identity}")
        baseline = snapshot.get("tracks")
        if not isinstance(baseline, list):
            raise DualIpodError(
                f"Composite playlist snapshot is invalid: {identity}")
        baseline_ids = [str(track_id) for track_id in baseline]
        actual_ids: list[str] = []
        for path in _read_managed_m3u8(payload, playlist_key=identity):
            mapped_track_id = track_by_device_path.get(path.casefold())
            if not mapped_track_id:
                raise DualIpodError(
                    "Rockbox playlist contains an unrecognized song; automatic "
                    f"device imports are not enabled: {path}")
            actual_ids.append(mapped_track_id)
        if actual_ids[:len(baseline_ids)] != baseline_ids:
            raise DualIpodError(
                "Rockbox playlist changed by deletion or reordering; only "
                f"recognized appended songs can sync automatically: {identity}")

        appended = actual_ids[len(baseline_ids):]
        if not appended:
            raise DualIpodError(
                f"Rockbox playlist changed outside SoundBox: {identity}")
        present = set(playlist.keys)
        present.update(baseline_ids)
        accepted: list[str] = []
        removed = set(removals.get(playlist.pid or "", ()))
        for track_id in appended:
            # A slow double-add is harmless: the device write is approved, but
            # the duplicate is not added to the desired/app-side membership.
            if track_id in present:
                continue
            if track_id not in available_track_ids:
                raise DualIpodError(
                    "Rockbox playlist addition maps to a currently unavailable "
                    f"source file: {track_id}")
            if track_id in hidden or track_id in removed:
                raise DualIpodError(
                    "Rockbox addition conflicts with a SoundBox removal; restore "
                    f"the song first: {track_id}")
            accepted.append(track_id)
            present.add(track_id)
        if accepted:
            if identity not in adoptable_playlist_keys:
                raise DualIpodError(
                    "Rockbox additions cannot be adopted into this derived "
                    f"SoundBox view: {identity}")
            playlist.keys.extend(accepted)
            additions.append(DualPlaylistAddition(identity, tuple(accepted)))
        approved[identity] = (relative, size, digest)
    return tuple(additions), approved


def _combined_playlists(
    selected: list[Playlist],
    existing: dict[str, Any] | None,
    *,
    reconcile: bool = False,
    respect_selected_order: bool = False,
) -> list[Playlist]:
    """Accumulate new drops while preserving earlier playlist snapshots.

    A normal drag changes only the playlists explicitly selected this time.
    Previously managed playlists are reconstructed from the committed manifest,
    so adding a crate cannot silently apply unrelated library drift.
    """
    incoming: dict[str, Playlist] = {}
    for playlist in selected:
        key = legacy_manifest.playlist_key(playlist)
        if key in incoming:
            raise DualIpodError(f"Playlist was selected twice: {playlist.display_path}")
        incoming[key] = playlist
    if reconcile:
        if existing is None:
            raise DualIpodError(
                "Composite Sync requires an existing ownership manifest")
        prior_keys = set((existing.get("playlists") or {}).keys())
        unknown = set(incoming) - prior_keys
        if unknown:
            raise DualIpodError(
                "Composite Sync may reconcile only previously managed playlists")
        if respect_selected_order:
            return list(selected)
        order = existing.get("playlist_order")
        if not isinstance(order, list):
            order = list((existing.get("playlists") or {}).keys())
        return [incoming[str(key)] for key in order if str(key) in incoming]
    if existing is None:
        return list(selected)

    prior = existing.get("playlists") or {}
    order = existing.get("playlist_order")
    if not isinstance(order, list) or set(map(str, order)) != set(prior):
        order = list(prior)
    output: list[Playlist] = []
    seen: set[str] = set()
    for raw_key in order:
        key = str(raw_key)
        row = prior.get(key)
        if not isinstance(row, dict):
            raise DualIpodError(f"Manifest playlist row is invalid: {key}")
        current_playlist: Playlist | None = incoming.get(key)
        if current_playlist is None:
            tracks = row.get("tracks")
            folder = row.get("folder_path")
            if not isinstance(tracks, list) or not isinstance(folder, list):
                raise DualIpodError(f"Manifest playlist snapshot is invalid: {key}")
            current_playlist = Playlist(
                str(row.get("name") or ""),
                [str(part) for part in folder],
                [str(track_id) for track_id in tracks],
                pid=str(row.get("pid") or ""),
            )
        output.append(current_playlist)
        seen.add(key)
    output.extend(
        playlist for key, playlist in incoming.items() if key not in seen)
    return output


def _device_playlist_names(playlists: list[Playlist]) -> list[str]:
    """Choose concise, unique titles shared by stock and Rockbox views.

    The leaf rekordbox name is what a listener expects to see (``Dubstep``,
    not ``GENRES / Dubstep``).  Folder context is retained in the ownership
    manifest and is used only to disambiguate genuinely duplicated leaf names.
    """
    leaf_counts: dict[str, int] = {}
    for playlist in playlists:
        leaf = str(playlist.name or "Untitled playlist").strip()
        leaf_counts[leaf.casefold()] = leaf_counts.get(leaf.casefold(), 0) + 1
    output = [
        (
            str(playlist.name or "Untitled playlist").strip()
            if leaf_counts[
                str(playlist.name or "Untitled playlist").strip().casefold()
            ] == 1
            else playlist.display_path
        )
        for playlist in playlists
    ]
    folded = [name.casefold() for name in output]
    if len(set(folded)) != len(folded):
        duplicate = next(
            name for name in output if folded.count(name.casefold()) > 1)
        raise DualIpodError(
            "Managed iPod playlist titles must be unique, including folder "
            f"context: {duplicate}")
    return output


def _previous_rows_by_source(
    existing: dict[str, Any] | None,
    kind: str,
) -> dict[str, tuple[str, dict[str, Any]]]:
    output: dict[str, tuple[str, dict[str, Any]]] = {}
    for device_path, raw in ((existing or {}).get("files") or {}).items():
        if not isinstance(raw, dict) or raw.get("kind") != kind:
            continue
        source = str(raw.get("source") or "")
        if not source or source in output:
            raise DualIpodError(
                f"Composite manifest has ambiguous {kind} ownership for {source!r}")
        _relative_manifest_path(str(device_path))
        output[source] = (str(device_path), raw)
    return output


def _verify_owned_before(
    root: Path,
    device_path: str,
    row: dict[str, Any],
) -> tuple[Path, int, str]:
    if not bool(row.get("owned", False)):
        raise DualIpodError(f"Refusing to replace unowned device file: {device_path}")
    return _verify_manifest_file_before(root, device_path, row)


def _verify_manifest_file_before(
    root: Path,
    device_path: str,
    row: dict[str, Any],
) -> tuple[Path, int, str]:
    """Verify exact manifest bytes without broadening rewrite ownership."""
    relative = _relative_manifest_path(device_path)
    live = root / relative
    try:
        size = live.stat().st_size
    except OSError as exc:
        raise DualIpodError(f"Managed device file is missing: {device_path}") from exc
    expected_size = int(row.get("size") or -1)
    expected_hash = str(row.get("sha256") or "")
    actual_hash = _sha256(live) if size == expected_size else ""
    if size != expected_size or actual_hash != expected_hash:
        raise DualIpodError(f"Managed device file changed outside SoundBox: {device_path}")
    return relative, size, actual_hash


def _manifest_file_baseline(
    root: Path,
    device_path: str,
    row: dict[str, Any],
) -> tuple[Path, int, str]:
    """Validate ownership/path/size without streaming the whole device file.

    Composite media replacement is source-authoritative and atomic. The old
    payload is renamed into the transaction recovery tree immediately before
    replacement, so planning does not need a sustained USB read merely to show
    the confirmation dialog. Small playlists and artwork continue to use exact
    planning-time hashes; managed-media cleanup also requires an exactly
    reproducible source-side payload.
    """
    relative = _relative_manifest_path(device_path)
    live = root / relative
    try:
        size = live.stat().st_size
    except OSError as exc:
        raise DualIpodError(f"Managed device file is missing: {device_path}") from exc
    expected_size = int(row.get("size") or -1)
    expected_hash = str(row.get("sha256") or "")
    if size != expected_size or len(expected_hash) != 64:
        raise DualIpodError(
            f"Managed device file baseline is invalid: {device_path}")
    return relative, size, expected_hash


def _filtered_playlists(
    library: Library,
    selected: list[Playlist],
) -> tuple[list[Playlist], list[DualOmission], list[DualOmission]]:
    output: list[Playlist] = []
    missing: dict[str, DualOmission] = {}
    unsupported: dict[str, DualOmission] = {}
    for playlist in selected:
        keys: list[str] = []
        for track in library.resolve(playlist):
            source = track.path
            try:
                exists = source.is_file()
            except OSError:
                exists = False
            if not exists:
                missing.setdefault(track.track_id, DualOmission(
                    track.track_id, track.name, source, "source file is missing"))
                continue
            if source.suffix.lower() not in ROCKBOX_SUPPORTED_SUFFIXES:
                unsupported.setdefault(track.track_id, DualOmission(
                    track.track_id,
                    track.name,
                    source.resolve(strict=True),
                    f"{source.suffix.lstrip('.').upper() or 'unknown'} is not in "
                    "the validated Rockbox 4.0 audio profile",
                ))
                continue
            keys.append(track.track_id)
        output.append(Playlist(
            playlist.name, list(playlist.folder_path), keys, pid=playlist.pid))
    return output, list(missing.values()), list(unsupported.values())


def _effective_display_metadata(
    library: Library,
    track: Any,
    source: Path,
    *,
    derive_artists: bool,
    artist_overrides: dict | None,
    title_overrides: dict | None,
) -> tuple[str, str]:
    """Use the same curated title/artist that the stock database receives."""
    override = (title_overrides or {}).get(track.track_id)
    artist = metadata.effective_artist(
        track.track_id,
        override or track.name,
        track.artist,
        artist_overrides,
        derive_artists,
        cast("metadata.ArtistRegistry | None", library.artist_registry),
        stem=source.stem,
    )
    title = metadata.effective_title(
        track.track_id,
        track.name,
        artist,
        title_overrides,
        getattr(library, "title_junk", None),
    )
    return title or source.stem, artist


def _playlist_metadata_sig(
    library: Library,
    playlist: Playlist,
    *,
    artist_overrides: dict | None,
    title_overrides: dict | None,
) -> str:
    """Hash effective Rockbox-visible title/artist in exact playlist order."""
    digest = hashlib.sha1()
    for track in library.resolve(playlist):
        source = track.path.resolve(strict=True)
        title, artist = _effective_display_metadata(
            library,
            track,
            source,
            derive_artists=True,
            artist_overrides=artist_overrides,
            title_overrides=title_overrides,
        )
        for value in (track.track_id, title, artist):
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def _composite_entry_for_effective_playlist(
    library: Library,
    playlist: Playlist,
    *,
    artist_overrides: dict | None,
    title_overrides: dict | None,
) -> dict[str, Any]:
    row = legacy_manifest.entry_for(
        playlist,
        getattr(library, "hidden_ids", frozenset()),
        getattr(library, "removed_by_playlist", None),
        export_profile=DUAL_EXPORT_PROFILE,
    )
    row["metadata_sig"] = _playlist_metadata_sig(
        library,
        playlist,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
    )
    return row


def composite_playlist_entry(
    library: Library,
    playlist: Playlist,
    *,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
) -> dict[str, Any]:
    """Return the exact schema-3 baseline for one current source playlist."""
    effective, _missing, _unsupported = _filtered_playlists(library, [playlist])
    return _composite_entry_for_effective_playlist(
        library,
        effective[0],
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
    )


def _playlist_writes(
    target: Path,
    selected: list[Playlist],
    device_names: list[str],
    entries_by_playlist: dict[int, list[pl_mod.M3UEntry]],
    existing: dict[str, Any] | None,
    *,
    reconcile: bool = False,
    approved_external: dict[str, tuple[Path, int, str]] | None = None,
) -> tuple[list[DualPlaylistWrite], list[DualFileDelete]]:
    output: list[DualPlaylistWrite] = []
    used: dict[str, str] = {}
    catalogue_layout_migration = bool(
        existing is not None
        and existing.get("profile") != DUAL_EXPORT_PROFILE
    )
    prior_by_key = _managed_playlist_rows(existing or {})
    approved_external = approved_external or {}
    order_width = max(2, len(str(len(selected))))
    for index, (playlist, device_name) in enumerate(zip(
            selected, device_names, strict=True)):
        identity = legacy_manifest.playlist_key(playlist)
        prior = prior_by_key.get(identity)
        # Rockbox's Playlist Catalogue scans direct children of /Playlists and
        # the physical iPod has proven to display them alphabetically even when
        # date sorting is requested.  A zero-padded Rockbox-only filename is
        # therefore the durable catalogue-order authority.  Stock titles and
        # manifest device names remain clean and unnumbered.
        catalogue_name = (
            f"{index + 1:0{order_width}d} - {device_name}")
        desired_relative = pl_mod.flat_playlist_path(
            Path("Playlists"), [], catalogue_name)
        relative = desired_relative
        key = str(relative).replace(os.sep, "/").casefold()
        owner = used.get(key)
        if owner is not None and owner != identity:
            suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:6]
            relative = relative.with_name(
                f"{relative.stem}_{suffix}{relative.suffix}")
            key = str(relative).replace(os.sep, "/").casefold()
        used[key] = identity
        payload = pl_mod.render_m3u8(entries_by_playlist[index]).encode("utf-8")
        payload_hash = hashlib.sha256(payload).hexdigest()
        live = target / relative
        desired_mtime = float(
            _PLAYLIST_ORDER_EPOCH + index * _PLAYLIST_ORDER_STEP)
        changed = True
        size_before: int | None = None
        hash_before: str | None = None
        mtime_before: float | None = None
        order_changed = True
        if prior is not None:
            approved_before = approved_external.get(identity)
            if approved_before is None:
                relative_before, size_before, hash_before = _verify_owned_before(
                    target, prior[0], prior[1])
            else:
                relative_before, size_before, hash_before = approved_before
                if relative_before != _relative_manifest_path(prior[0]):
                    raise DualIpodError(
                        f"Approved Rockbox playlist path changed: {identity}")
            mtime_before = (target / relative_before).stat().st_mtime
            if relative_before != relative:
                if live.exists():
                    raise DualIpodError(
                        f"Renamed Rockbox playlist destination already exists: {live}")
                size_before = None
                hash_before = None
            else:
                changed = not (
                    size_before == len(payload) and hash_before == payload_hash)
                order_changed = (
                    abs(mtime_before - desired_mtime)
                    > _PLAYLIST_MTIME_TOLERANCE)
        elif live.exists():
            raise DualIpodError(
                f"Refusing existing Rockbox playlist without dual ownership: {live}")
        output.append(DualPlaylistWrite(
            relative,
            payload,
            payload_hash,
            identity,
            desired_mtime,
            changed,
            size_before,
            hash_before,
            mtime_before,
            order_changed,
        ))
    desired_by_identity = {
        item.playlist_key: item.relative_path for item in output}
    deletes: list[DualFileDelete] = []
    for identity, (device_path, row) in prior_by_key.items():
        if (
            not reconcile
            and not catalogue_layout_migration
            and identity not in desired_by_identity
        ):
            continue
        if desired_by_identity.get(identity) != _relative_manifest_path(device_path):
            approved_before = approved_external.get(identity)
            if approved_before is None:
                prior_relative, size, digest = _verify_owned_before(
                    target, device_path, row)
            else:
                prior_relative, size, digest = approved_before
                if prior_relative != _relative_manifest_path(device_path):
                    raise DualIpodError(
                        f"Approved Rockbox playlist path changed: {identity}")
            deletes.append(DualFileDelete(
                prior_relative, size, digest, "playlist"))
    return output, deletes


def plan_dual_ipod_sync(
    library: Library,
    selected: list[Playlist],
    target: str | Path,
    *,
    model_number: str,
    filesystem: str,
    rockbox_media_cache: RockboxMediaCache,
    rockbox_art_cache: RockboxArtworkCache,
    derive_artists: bool = True,
    artist_overrides: dict | None = None,
    title_overrides: dict | None = None,
    art_cache: Any | None = None,
    stock_art_cache: StockArtworkCache | None = None,
    stock_shadow_store: StockIpodShadowStore | None = None,
    identity_key: str = "",
    serial_number: str = "",
    reconcile: bool = False,
    respect_selected_order: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    expected_playlist_rows: dict[str, dict[str, Any]] | None = None,
    adoptable_playlist_keys: set[str] | None = None,
) -> DualIpodSyncPlan:
    """Build an exact, no-target-write composite plan."""
    root = Path(target).expanduser().resolve(strict=True)
    if not (root / ".rockbox").is_dir() or not (root / "iPod_Control").is_dir():
        raise DualIpodError("Composite sync requires both .rockbox and iPod_Control")
    if not selected and not reconcile:
        raise DualIpodError("Select at least one playlist for dual-boot sync")
    existing_manifest, existing_payload, manifest_hash_before = (
        _read_existing_manifest(root))
    interrupted_recovery_hint = _matching_interrupted_recovery(
        root, existing_manifest, manifest_hash_before, {})
    interrupted_verifier = (
        InterruptedFileVerifier(
            rockbox_media_cache.root, manifest_hash_before)
        if interrupted_recovery_hint is not None and manifest_hash_before
        else None
    )
    config_update = rockbox_config.plan(root)
    combined = _combined_playlists(
        selected,
        existing_manifest,
        reconcile=reconcile,
        respect_selected_order=respect_selected_order,
    )
    effective, missing, unsupported = _filtered_playlists(library, combined)
    playlist_additions: tuple[DualPlaylistAddition, ...] = ()
    approved_external_playlists: dict[str, tuple[Path, int, str]] = {}
    if reconcile and existing_manifest is not None and adoptable_playlist_keys is not None:
        playlist_additions, approved_external_playlists = (
            _merge_recognized_rockbox_additions(
                library,
                effective,
                root,
                existing_manifest,
                adoptable_playlist_keys=set(adoptable_playlist_keys),
            )
        )
    device_playlist_names = _device_playlist_names(effective)
    unique_sources: dict[Path, Any] = {}
    display_by_source: dict[Path, tuple[str, str]] = {}
    for playlist in effective:
        for track in library.resolve(playlist):
            source = track.path
            if source.is_file() and source.suffix.lower() in ROCKBOX_SUPPORTED_SUFFIXES:
                source = source.resolve(strict=True)
                display = _effective_display_metadata(
                    library,
                    track,
                    source,
                    derive_artists=derive_artists,
                    artist_overrides=artist_overrides,
                    title_overrides=title_overrides,
                )
                previous = display_by_source.get(source)
                if previous is not None and previous != display:
                    raise DualIpodError(
                        "One source file resolved to conflicting Rockbox metadata: "
                        f"{source}")
                unique_sources.setdefault(source, track)
                display_by_source[source] = display

    # Planning is intentionally visible. Each source has one media/profile
    # pass and one Rockbox-art pass; guarded playlist deletions also verify each
    # retained playlist against its committed row. The four remaining units
    # cover the stock renderer, filesystem playlist plan, playlist publication
    # plan, and final ownership reconciliation. This remains no-target-write
    # work.
    expected_rows = expected_playlist_rows or {}
    planning_total = max(
        1, len(unique_sources) * 2 + len(expected_rows) + 4)
    planning_done = 0

    def emit_planning(label: str = "") -> None:
        if on_progress is not None:
            on_progress(planning_done, planning_total, label)

    emit_planning()

    if expected_playlist_rows is not None:
        effective_by_key = {
            legacy_manifest.playlist_key(playlist): playlist
            for playlist in effective
        }
        if set(effective_by_key) != set(expected_rows):
            raise DualIpodError(
                "Another managed playlist changed or disappeared; run Sync "
                "before deleting a different playlist")
        for key, playlist in effective_by_key.items():
            actual = _composite_entry_for_effective_playlist(
                library,
                playlist,
                artist_overrides=artist_overrides,
                title_overrides=title_overrides,
            )
            expected = expected_rows[key]
            # The committed row also carries device-presentation state such as
            # ``device_name``.  That value may be added or changed by an order/
            # collision migration without changing the source playlist that
            # deletion is guarding.  Compare every canonical source snapshot
            # field, while deliberately ignoring manifest-only presentation
            # fields.  Missing canonical fields still fail closed.
            if any(
                field not in expected or expected[field] != value
                for field, value in actual.items()
            ):
                raise DualIpodError(
                    "Another managed playlist has pending changes; run Sync "
                    "before deleting a different playlist")
            planning_done += 1
            emit_planning(playlist.display_path)

    prior_media: dict[str, tuple[str, dict[str, Any]]] = {}
    if (
        existing_manifest is not None
        and existing_manifest.get("profile") == DUAL_EXPORT_PROFILE
    ):
        for kind in ("shared-stock-media", "rockbox-media"):
            for source_text, value in _previous_rows_by_source(
                    existing_manifest, kind).items():
                if source_text in prior_media:
                    raise DualIpodError(
                        "Composite manifest owns one source in both media stores: "
                        f"{source_text}")
                prior_media[source_text] = value
    planned_media = {}
    for source in unique_sources:
        prior = prior_media.get(str(source))
        planned_media[source] = rockbox_media_cache.plan_from_committed(
            source,
            title=display_by_source[source][0],
            artist=display_by_source[source][1],
            committed=prior[1] if prior is not None else None,
        )
        planning_done += 1
        emit_planning(source.name)
    payloads: dict[Path, Path | PlannedRockboxMedia] = dict(planned_media)
    stock_plan = plan_stock_ipod_sync(
        library,
        effective,
        root,
        model_number=model_number,
        filesystem=filesystem,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
        art_cache=art_cache,
        stock_art_cache=stock_art_cache,
        identity_key=identity_key,
        serial_number=serial_number,
        shadow_store=stock_shadow_store,
        media_payloads=payloads,
        rockbox_media_cache=rockbox_media_cache,
        allow_dual_boot=True,
        allow_empty_selection=bool(reconcile and existing_manifest is not None),
        playlist_names=tuple(device_playlist_names),
        managed_playlist_names_before=tuple(
            str(row.get("device_name") or " / ".join(
                [*map(str, row.get("folder_path") or []),
                 str(row.get("name") or "")]))
            for row in ((existing_manifest or {}).get("playlists") or {}).values()
            if isinstance(row, dict)
        ),
        managed_playlist_keys_before=tuple(
            str(key) for key in (
                (existing_manifest or {}).get("playlists") or {})
        ) if reconcile else (),
        managed_track_rows_before=tuple(sorted({
            (
                int(row.get("dbid") or 0),
                str(device_path),
                str(row.get("sha256") or ""),
            )
            for device_path, row in (
                ((existing_manifest or {}).get("files") or {}).items())
            if (
                reconcile
                and isinstance(row, dict)
                and row.get("kind") == "shared-stock-media"
                and int(row.get("dbid") or 0) > 0
            )
        })),
        managed_source_rows_before=tuple(sorted({
            (
                str(row.get("source") or ""),
                int(row.get("dbid") or 0),
                str(device_path),
                int(row.get("size") or -1),
                str(row.get("sha256") or ""),
            )
            for device_path, row in (
                ((existing_manifest or {}).get("files") or {}).items())
            if (
                isinstance(row, dict)
                and row.get("kind") == "shared-stock-media"
                and str(row.get("source") or "")
                and int(row.get("dbid") or 0) > 0
            )
        })),
        defer_cleanup=reconcile,
        allow_interrupted_media_adoption=(
            interrupted_recovery_hint is not None),
        interrupted_file_verifier=interrupted_verifier,
    )
    planning_done += 1
    emit_planning("Stock library")
    stock_paths = stock_plan.source_device_paths
    planned_files, entries = sync_mod.plan(
        library,
        effective,
        root,
        derive_artists=derive_artists,
        artist_overrides=artist_overrides,
        title_overrides=title_overrides,
    )
    planning_done += 1
    emit_planning("Rockbox library")

    prior_rockbox_media = _previous_rows_by_source(
        existing_manifest, "rockbox-media")
    expected_new_rockbox_media = sum(
        1
        for device_path, planned in planned_files.items()
        if (
            str(planned.canonical_source.resolve(strict=True)) not in stock_paths
            and str(planned.canonical_source.resolve(strict=True))
            not in prior_rockbox_media
        )
    )
    stock_summary = stock_plan.summary()
    interrupted_recovery = _matching_interrupted_recovery(
        root,
        existing_manifest,
        manifest_hash_before,
        {
            "stock_safe_sources": len(stock_paths),
            "rockbox_only_sources": len(unique_sources) - len(stock_paths),
            "missing_sources": len(missing),
            "unsupported_sources": len(unsupported),
            "sanitized_sources": sum(
                1 for item in planned_media.values() if item.transformed),
            "stock_new_tracks": int(stock_summary["new_tracks"]),
            "stock_final_tracks": int(stock_summary["final_tracks"]),
            "rockbox_media_files": expected_new_rockbox_media,
            "rockbox_playlists": len(effective),
        },
    )
    if stock_plan.adoptions and interrupted_recovery is None:
        raise DualIpodError(
            "Interrupted stock files matched by content, but their recovery "
            "journal does not match the fresh coordinated plan")
    safe_path_by_rockbox_path: dict[str, str] = {}
    path_replacements: dict[str, str] = {}
    media_copies: list[DualFileCopy] = []
    file_adoptions: list[DualFileAdoption] = []
    file_rewrites: list[DualFileRewrite] = []
    rockbox_path_by_source: dict[Path, str] = {}
    for device_path, planned in planned_files.items():
        source = planned.canonical_source.resolve(strict=True)
        stock_path = stock_paths.get(str(source))
        if stock_path:
            safe_path_by_rockbox_path[device_path] = stock_path
            rockbox_path_by_source[source] = stock_path
            continue
        media_plan = planned_media[source]
        payload_source = media_plan.source
        payload_hash = media_plan.payload_sha256
        prior = prior_rockbox_media.get(str(source))
        final_path = prior[0] if prior is not None else device_path
        relative = _relative_manifest_path(final_path)
        if relative.parts[0] != "Music":
            raise DualIpodError(
                f"Managed Rockbox media escaped /Music: {final_path}")
        if prior is not None:
            if not bool(prior[1].get("owned", False)):
                raise DualIpodError(
                    f"Refusing to replace unowned device file: {prior[0]}")
            relative_before, size_before, hash_before = _manifest_file_baseline(
                root, prior[0], prior[1])
            if (
                size_before != media_plan.payload_size
                or hash_before != payload_hash
            ):
                file_rewrites.append(DualFileRewrite(
                    payload_source,
                    relative_before,
                    size_before,
                    hash_before,
                    media_plan.payload_size,
                    payload_hash,
                    "rockbox-media",
                    media_plan if media_plan.transformed else None,
                ))
        else:
            live = root / relative
            if live.exists():
                if interrupted_recovery is None:
                    raise DualIpodError(
                        f"Unowned Rockbox media conflict: {live}")
                file_adoptions.append(_plan_recoverable_file(
                    live,
                    relative_path=relative,
                    size=media_plan.payload_size,
                    sha256=payload_hash,
                    kind="rockbox-media",
                ))
            else:
                media_copies.append(DualFileCopy(
                    payload_source,
                    relative,
                    media_plan.payload_size,
                    payload_hash,
                    "rockbox-media",
                    media_plan if media_plan.transformed else None,
                ))
        rockbox_path_by_source[source] = final_path
        if final_path != device_path:
            path_replacements[device_path] = final_path

    for playlist_entries in entries.values():
        for entry in playlist_entries:
            if entry.device_path in safe_path_by_rockbox_path:
                entry.device_path = safe_path_by_rockbox_path[entry.device_path]
            elif entry.device_path in path_replacements:
                entry.device_path = path_replacements[entry.device_path]

    final_path_keys: dict[str, Path] = {}
    for source, device_path in rockbox_path_by_source.items():
        key = str(device_path).replace("\\", "/").casefold()
        owner = final_path_keys.get(key)
        if owner is not None and owner != source:
            raise DualIpodError(
                f"Two sources resolve to the same composite path: {device_path}")
        final_path_keys[key] = source

    artwork_copies: list[DualFileCopy] = []
    artwork_paths: set[Path] = set()
    artwork_audio_paths: dict[Path, str] = {}
    artwork_rows: dict[Path, dict[str, Any]] = {}
    prior_files = (existing_manifest or {}).get("files") or {}
    retained_art_sides = {
        int(side) for side in ((existing_manifest or {}).get(
            "rockbox_artwork_sides") or ())
        if str(side).isdigit() and 1 <= int(side) <= 240
    }
    retained_art_sides.update(artencode.ROCKBOX_COVER_SIDES)
    retained_art_sides.update(rockbox_config.theme_album_art_sides(root))
    exact_art_sides = tuple(sorted(retained_art_sides, reverse=True))
    if art_cache is not None:
        for source, track in unique_sources.items():
            canonical_art = art_cache.image_path(track.track_id)
            if canonical_art is None:
                planning_done += 1
                emit_planning(source.name)
                continue
            audio_path = Path(rockbox_path_by_source[source].lstrip("/"))
            variants = (
                *((side, f".{side}x{side}.bmp")
                  for side in exact_art_sides),
                (artencode.ROCKBOX_FALLBACK_SIDE, ".bmp"),
            )
            for side, suffix in variants:
                image = rockbox_art_cache.prepare(
                    canonical_art, side=side, verify_cache=False)
                relative = audio_path.with_suffix(suffix)
                if relative in artwork_paths:
                    continue
                artwork_paths.add(relative)
                artwork_audio_paths[relative] = rockbox_path_by_source[source]
                live = root / relative
                device_path = "/" + str(relative).replace(os.sep, "/")
                previous = prior_files.get(device_path)
                if previous is not None:
                    if not isinstance(previous, dict) or previous.get(
                            "kind") != "rockbox-artwork":
                        raise DualIpodError(
                            f"Composite ownership kind changed at {device_path}")
                    if not bool(previous.get("owned", False)):
                        raise DualIpodError(
                            f"Refusing to replace unowned device file: {device_path}")
                    _relative_manifest_path(device_path)
                    try:
                        size_before = int(previous.get("size") or -1)
                    except (TypeError, ValueError):
                        size_before = -1
                    hash_before = str(previous.get("sha256") or "")
                    if size_before != image.size or hash_before != image.sha256:
                        # Only a planned rewrite needs the exact live-byte
                        # baseline. Unchanged art is outside this transaction;
                        # hashing thousands of tiny BMPs during every plan can
                        # reset a classic iPod's USB transport for no safety
                        # gain. The manifest and verified Mac cache already
                        # agree on the identity we intend to retain.
                        relative_before, size_before, hash_before = (
                            _verify_owned_before(root, device_path, previous))
                        image = rockbox_art_cache.prepare(
                            canonical_art, side=side, verify_cache=True)
                        file_rewrites.append(DualFileRewrite(
                            image.payload,
                            relative_before,
                            size_before,
                            hash_before,
                            image.size,
                            image.sha256,
                            "rockbox-artwork",
                        ))
                elif live.exists():
                    if interrupted_recovery is None:
                        raise DualIpodError(
                            f"Unowned Rockbox artwork conflict: {live}")
                    file_adoptions.append(_plan_recoverable_file(
                        live,
                        relative_path=relative,
                        size=image.size,
                        sha256=image.sha256,
                        kind="rockbox-artwork",
                    ))
                else:
                    image = rockbox_art_cache.prepare(
                        canonical_art, side=side, verify_cache=True)
                    artwork_copies.append(DualFileCopy(
                        image.payload, relative, image.size, image.sha256,
                        "rockbox-artwork"))
                artwork_rows[relative] = {
                    "kind": "rockbox-artwork",
                    "owned": True,
                    "source": str(canonical_art.resolve(strict=True)),
                    "size": image.size,
                    "sha256": image.sha256,
                }
            planning_done += 1
            emit_planning(source.name)
        rockbox_art_cache.flush_source_index()
    else:
        planning_done += len(unique_sources)
        emit_planning("Rockbox artwork")

    playlist_writes, file_deletes = _playlist_writes(
        root,
        effective,
        device_playlist_names,
        entries,
        existing_manifest,
        reconcile=reconcile,
        approved_external=approved_external_playlists,
    )
    planning_done += 1
    emit_planning("Playlists")
    memberships: dict[str, set[str]] = {}
    for index, playlist in enumerate(effective):
        identity = legacy_manifest.playlist_key(playlist)
        for entry in entries[index]:
            if entry.device_path:
                memberships.setdefault(entry.device_path, set()).add(identity)
    files_manifest: dict[str, dict[str, Any]] = {}
    for source, device_path in rockbox_path_by_source.items():
        media_plan = planned_media[source]
        previous = prior_files.get(device_path)
        shared = device_path.startswith("/iPod_Control/")
        files_manifest[device_path] = {
            "kind": "shared-stock-media" if shared else "rockbox-media",
            "owned": True,
            "source": str(source),
            "title": display_by_source[source][0],
            "artist": display_by_source[source][1],
            "size": media_plan.payload_size,
            "sha256": media_plan.payload_sha256,
            "source_size": media_plan.source_size,
            "source_mtime_ns": media_plan.source_mtime_ns,
            "source_ctime_ns": media_plan.source_ctime_ns,
            **(
                {"source_sha256": media_plan.source_sha256}
                if media_plan.source_sha256_verified else {}
            ),
            "source_sha256_verified": media_plan.source_sha256_verified,
            "media_profile": media_plan.profile,
            "transformed": media_plan.transformed,
            "removed_images": media_plan.removed_images,
            "playlists": sorted(memberships.get(device_path, ())),
        }
        if shared:
            files_manifest[device_path]["dbid"] = (
                stock_plan.source_device_dbids[str(source)])
    for relative, row in artwork_rows.items():
        device_path = "/" + str(relative).replace(os.sep, "/")
        files_manifest[device_path] = {
            **row,
            "playlists": sorted(memberships.get(
                artwork_audio_paths[relative], ())),
        }
    for playlist_item in playlist_writes:
        device_path = "/" + str(playlist_item.relative_path).replace(os.sep, "/")
        files_manifest[device_path] = {
            "kind": "playlist",
            "owned": True,
            "source": playlist_item.playlist_key,
            "size": len(playlist_item.payload),
            "sha256": playlist_item.sha256,
            "playlists": [playlist_item.playlist_key],
        }

    retained_stale = 0
    orphan_deletes: list[DualOrphanDelete] = []
    deleted_device_paths = {
        "/" + str(item.relative_path).replace(os.sep, "/")
        for item in file_deletes
    }
    stock_media_delete_paths = {
        item.relative_path: item for item in stock_plan.media_deletes}

    for device_path, raw in prior_files.items():
        if device_path in files_manifest or not isinstance(raw, dict):
            continue
        if device_path in deleted_device_paths:
            continue
        relative = _relative_manifest_path(str(device_path))
        kind = str(raw.get("kind") or "")
        eligible = kind in {
            "shared-stock-media", "rockbox-media", "rockbox-artwork"}
        if kind == "shared-stock-media" and relative not in stock_media_delete_paths:
            eligible = False
        if eligible:
            verifier = (
                _manifest_file_baseline
                if kind in {"shared-stock-media", "rockbox-media"}
                else _verify_manifest_file_before
            )
            verified_relative, size, digest = verifier(
                root, str(device_path), raw)
            orphan_deletes.append(DualOrphanDelete(
                verified_relative,
                size,
                digest,
                kind,
            ))
            continue
        stale = dict(raw)
        stale["playlists"] = []
        stale["stale"] = True
        files_manifest[str(device_path)] = stale
        retained_stale += 1

    planned_orphan_paths = {item.relative_path for item in orphan_deletes}
    orphan_deletes.extend(
        item for item in _rockbox_clutter(root)
        if item.relative_path not in planned_orphan_paths
    )

    planned_shared_cleanup = {
        item.relative_path for item in orphan_deletes
        if item.kind == "shared-stock-media"
    }
    if planned_shared_cleanup != set(stock_media_delete_paths):
        raise DualIpodError(
            "Stock and composite orphan-media ownership did not reconcile")

    artwork_before_by_path = {
        item.relative_path: item for item in stock_plan.artwork_before}
    for relative in stock_plan.artwork_delete_paths:
        before = artwork_before_by_path.get(relative)
        if before is None:
            raise DualIpodError(
                f"Stock artwork cleanup lacks verification: {relative}")
        file_deletes.append(DualFileDelete(
            relative,
            before.size,
            before.sha256,
            "stock-artwork",
        ))

    playlist_rows = {}
    for playlist, device_name in zip(
            effective, device_playlist_names, strict=True):
        row = _composite_entry_for_effective_playlist(
            library,
            playlist,
            artist_overrides=artist_overrides,
            title_overrides=title_overrides,
        )
        row["device_name"] = device_name
        playlist_rows[legacy_manifest.playlist_key(playlist)] = row
    playlist_order = [
        legacy_manifest.playlist_key(playlist) for playlist in effective]
    previous_generation = int(
        (existing_manifest or {}).get("generation") or 0)
    manifest_data = {
        "schema": DUAL_MANIFEST_SCHEMA,
        "profile": DUAL_EXPORT_PROFILE,
        "generation": previous_generation + 1,
        "completed_generation": previous_generation + 1,
        "playlist_order": playlist_order,
        "rockbox_artwork_sides": list(exact_art_sides),
        "playlists": playlist_rows,
        "files": files_manifest,
    }
    unchanged_manifest = bool(
        existing_manifest is not None
        and existing_manifest.get("profile") == DUAL_EXPORT_PROFILE
        and existing_manifest.get("playlist_order") == playlist_order
        and existing_manifest.get("rockbox_artwork_sides") == list(exact_art_sides)
        and existing_manifest.get("playlists") == playlist_rows
        and existing_manifest.get("files") == files_manifest
    )
    if unchanged_manifest:
        assert existing_payload is not None
        manifest_payload = existing_payload
        generation = previous_generation
    else:
        manifest_payload = json.dumps(
            manifest_data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        generation = previous_generation + 1

    planning_done += 1
    emit_planning("Ownership manifest")

    return DualIpodSyncPlan(
        target=root,
        stock=stock_plan,
        media_copies=tuple(media_copies),
        artwork_copies=tuple(artwork_copies),
        file_adoptions=tuple(file_adoptions),
        file_rewrites=tuple(file_rewrites),
        file_deletes=tuple(file_deletes),
        orphan_deletes=tuple(orphan_deletes),
        playlists=tuple(playlist_writes),
        media_plans=tuple(planned_media.values()),
        config_update=config_update,
        playlist_additions=playlist_additions,
        manifest_payload=manifest_payload,
        manifest_payload_before=existing_payload,
        manifest_sha256_before=manifest_hash_before,
        missing=tuple(missing),
        unsupported=tuple(unsupported),
        stock_safe_sources=len(stock_paths),
        rockbox_only_sources=len(rockbox_path_by_source) - len(stock_paths),
        sanitized_sources=sum(
            1 for item in planned_media.values() if item.transformed),
        generation=generation,
        retained_stale_files=retained_stale,
        rockbox_media_cache=rockbox_media_cache,
        interrupted_verifier=interrupted_verifier,
    )


def commit_dual_ipod_sync(
    plan: DualIpodSyncPlan,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> DualIpodCommitResult:
    """Commit one incremental composite generation with exact rollback."""
    total_tracks = plan.progress_total
    progress_done = 0

    def emit_progress(label: str = "") -> None:
        if on_progress is not None:
            on_progress(progress_done, total_tracks, label)

    def stock_track_committed(relative_path: Path) -> None:
        nonlocal progress_done
        progress_done += 1
        emit_progress(str(relative_path))

    manifest_path = plan.target / ".soundbox" / "manifest.json"
    config_path = plan.target / plan.config_update.relative_path
    if plan.manifest_sha256_before is None:
        if manifest_path.exists():
            raise DualIpodError("SoundBox manifest appeared after dual dry-run")
    elif not manifest_path.is_file() or _sha256(
            manifest_path) != plan.manifest_sha256_before:
        raise DualIpodError("SoundBox manifest changed after dual dry-run")
    if plan.config_update.sha256_before is None:
        if config_path.exists():
            raise DualIpodError("Rockbox config appeared after dual dry-run")
    elif (
        not config_path.is_file()
        or _sha256(config_path) != plan.config_update.sha256_before
    ):
        raise DualIpodError("Rockbox config changed after dual dry-run")
    if plan.rockbox_media_cache is None:
        raise DualIpodError("Dual plan lost its Rockbox media cache")
    for media_plan in plan.media_plans:
        try:
            plan.rockbox_media_cache.verify_source_stat(media_plan)
        except Exception as exc:
            raise DualIpodError(str(exc)) from exc
    sources = [item.source for item in (
        plan.media_copies + plan.artwork_copies + plan.file_rewrites)]
    session = sync_mod.DeviceSession(plan.target, sources, create=False)
    for adopted in plan.file_adoptions:
        destination = session.guard(adopted.relative_path)
        if (
            plan.interrupted_verifier is None
            or not plan.interrupted_verifier.verify(
                destination,
                relative_path=adopted.relative_path,
                size=adopted.size,
                sha256=adopted.sha256,
            )
        ):
            raise DualIpodError(
                "Interrupted-sync recovery file changed after dry-run: "
                f"{adopted.relative_path}")
        if adopted.kind == "rockbox-media":
            progress_done += 1
            emit_progress(str(adopted.relative_path))
            time.sleep(0.15)
    for file_item in plan.media_copies + plan.artwork_copies:
        if file_item.media_plan is not None:
            if plan.rockbox_media_cache is None:
                raise DualIpodError(
                    "Bounded dual payload lost its Rockbox staging cache")
            try:
                plan.rockbox_media_cache.verify_source(file_item.media_plan)
            except Exception as exc:
                raise DualIpodError(str(exc)) from exc
        elif file_item.source.stat().st_size != file_item.size or _sha256(
                file_item.source) != file_item.sha256:
            raise DualIpodError(
                f"Prepared dual payload changed: {file_item.source}")
        if session.guard(file_item.relative_path).exists():
            raise DualIpodError(
                f"Planned dual destination is no longer free: {file_item.relative_path}")
    for rewrite_item in plan.file_rewrites:
        if rewrite_item.media_plan is not None:
            if plan.rockbox_media_cache is None:
                raise DualIpodError(
                    "Bounded dual rewrite lost its Rockbox staging cache")
            try:
                plan.rockbox_media_cache.verify_source(rewrite_item.media_plan)
            except Exception as exc:
                raise DualIpodError(str(exc)) from exc
        elif rewrite_item.source.stat().st_size != rewrite_item.size or _sha256(
                rewrite_item.source) != rewrite_item.sha256:
            raise DualIpodError(
                f"Prepared dual rewrite changed: {rewrite_item.source}")
        destination = session.guard(rewrite_item.relative_path)
        if (
            not destination.is_file()
            or destination.stat().st_size != rewrite_item.size_before
            or (
                rewrite_item.kind != "rockbox-media"
                and _sha256(destination) != rewrite_item.sha256_before
            )
        ):
            raise DualIpodError(
                f"Managed dual file changed after dry-run: {rewrite_item.relative_path}")
    for delete_item in plan.file_deletes:
        destination = session.guard(delete_item.relative_path)
        if (
            not destination.is_file()
            or destination.stat().st_size != delete_item.size_before
            or _sha256(destination) != delete_item.sha256_before
        ):
            raise DualIpodError(
                f"Managed dual file changed after dry-run: {delete_item.relative_path}")
    for orphan in plan.orphan_deletes:
        destination = session.guard(orphan.relative_path)
        if (
            not destination.is_file()
            or destination.stat().st_size != orphan.size_before
            or (
                orphan.kind not in {"shared-stock-media", "rockbox-media"}
                and _sha256(destination) != orphan.sha256_before
            )
        ):
            raise DualIpodError(
                f"Managed orphan changed after dry-run: {orphan.relative_path}")
    for playlist_item in plan.playlists:
        destination = session.guard(playlist_item.relative_path)
        if not playlist_item.changed:
            if (
                not destination.is_file()
                or destination.stat().st_size != len(playlist_item.payload)
                or _sha256(destination) != playlist_item.sha256
            ):
                raise DualIpodError(
                    f"Managed playlist changed after dry-run: "
                    f"{playlist_item.relative_path}")
        elif playlist_item.sha256_before is None:
            if destination.exists():
                raise DualIpodError(
                    f"Planned playlist destination is no longer free: "
                    f"{playlist_item.relative_path}")
        elif (
            not destination.is_file()
            or destination.stat().st_size != playlist_item.size_before
            or _sha256(destination) != playlist_item.sha256_before
        ):
            raise DualIpodError(
                f"Managed playlist changed after dry-run: "
                f"{playlist_item.relative_path}")

    free = os.statvfs(plan.target).f_bavail * os.statvfs(plan.target).f_frsize
    changed_playlists = [item for item in plan.playlists if item.changed]
    recovery_bytes = sum(
        item.size_before for item in plan.file_rewrites
        if item.kind != "rockbox-media")
    recovery_bytes += sum(item.size_before for item in plan.file_deletes)
    recovery_bytes += sum(
        int(item.size_before or 0) for item in changed_playlists)
    recovery_bytes += len(plan.config_update.payload_before or b"")
    required = plan.rockbox_bytes_to_copy + recovery_bytes + sum(
        len(item.payload) for item in changed_playlists) + len(
        plan.config_update.payload) + len(plan.manifest_payload)
    if required > free:
        raise DualIpodError(
            f"Not enough free space for Rockbox phase: needs {required}, has {free}")

    emit_progress()
    if not plan.requires_commit:
        noop_stock_result = StockIpodCommitResult(
            recovery_directory=plan.target / ".soundbox",
            copied_tracks=0,
            recovered_tracks=0,
            reused_tracks=plan.stock.reused_track_count,
            rewritten_tracks=0,
            playlists=plan.stock.playlist_names,
            database_sha256=plan.stock.database_sha256_before,
        )
        return DualIpodCommitResult(
            stock=noop_stock_result,
            copied_media=0,
            copied_artwork=0,
            recovered_media=0,
            recovered_artwork=0,
            playlists=0,
            manifest_sha256=hashlib.sha256(plan.manifest_payload).hexdigest(),
        )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    baseline = plan.manifest_sha256_before or plan.stock.database_sha256_before
    recovery = Path(".soundbox") / "dual-recovery" / (
        f"{stamp}-{baseline[:12]}")
    recovery_abs = session.guard(recovery)
    rewritten: list[DualFileRewrite] = []
    copied: list[Path] = []
    new_playlist_paths: list[Path] = []
    rewritten_playlists: list[DualPlaylistWrite] = []
    retimestamped_playlists: list[DualPlaylistWrite] = []
    deleted_files: list[DualFileDelete] = []
    stock_result: StockIpodCommitResult | None = None
    stock_committed = False
    manifest_published = False
    config_published = False
    deleted_orphans = 0
    deleted_orphan_bytes = 0
    deleted_orphan_items: list[DualOrphanDelete] = []
    try:
        for rewrite_item in plan.file_rewrites:
            if rewrite_item.kind == "rockbox-media":
                continue
            live = session.guard(rewrite_item.relative_path)
            saved = session.guard(
                recovery / "files" / rewrite_item.relative_path.parent
                / (rewrite_item.relative_path.name + ".before"))
            session.atomic_copy(live, saved)
        for changed_playlist in changed_playlists:
            if changed_playlist.sha256_before is None:
                continue
            live = session.guard(changed_playlist.relative_path)
            saved = session.guard(
                recovery / "playlists" / changed_playlist.relative_path.parent
                / (changed_playlist.relative_path.name + ".before"))
            session.atomic_copy(live, saved)
        for delete_item in plan.file_deletes:
            live = session.guard(delete_item.relative_path)
            saved = session.guard(
                recovery / "deleted" / delete_item.relative_path.parent
                / (delete_item.relative_path.name + ".before"))
            session.atomic_copy(live, saved)
        if plan.config_update.changed and plan.config_update.payload_before is not None:
            saved = session.guard(recovery / "rockbox" / "config.cfg.before")
            session.atomic_copy(config_path, saved)
        journal = {
            **plan.summary(),
            "baseline_manifest_sha256": plan.manifest_sha256_before,
            "intended_manifest_sha256": hashlib.sha256(
                plan.manifest_payload).hexdigest(),
            "transaction_state": "copying",
        }
        session.atomic_write(
            recovery / "plan.json",
            json.dumps(journal, indent=2, sort_keys=True).encode("utf-8")
            + b"\n",
        )
        for file_item in plan.media_copies + plan.artwork_copies:
            destination = session.guard(file_item.relative_path)
            prepared = None
            copy_source = file_item.source
            try:
                if file_item.media_plan is not None:
                    assert plan.rockbox_media_cache is not None
                    prepared = plan.rockbox_media_cache.materialize(
                        file_item.media_plan)
                    copy_source = prepared.payload
                session.atomic_copy(copy_source, destination)
                copied.append(destination)
                if destination.stat().st_size != file_item.size or _sha256(
                        destination) != file_item.sha256:
                    raise DualIpodError(
                        f"Dual copy failed verification: {destination}")
            finally:
                if prepared is not None:
                    assert plan.rockbox_media_cache is not None
                    plan.rockbox_media_cache.discard(prepared)
            if file_item.kind == "rockbox-media":
                progress_done += 1
                emit_progress(str(file_item.relative_path))
                time.sleep(0.15)
        for rewrite_item in plan.file_rewrites:
            destination = session.guard(rewrite_item.relative_path)
            prepared = None
            copy_source = rewrite_item.source
            try:
                if rewrite_item.media_plan is not None:
                    assert plan.rockbox_media_cache is not None
                    prepared = plan.rockbox_media_cache.materialize(
                        rewrite_item.media_plan)
                    copy_source = prepared.payload
                if rewrite_item.kind == "rockbox-media":
                    saved = session.guard(
                        recovery / "files" / rewrite_item.relative_path.parent
                        / (rewrite_item.relative_path.name + ".before"))
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    session.revalidate()
                    os.replace(destination, saved)
                    rewritten.append(rewrite_item)
                session.atomic_copy(copy_source, destination)
                if rewrite_item.kind != "rockbox-media":
                    rewritten.append(rewrite_item)
                if destination.stat().st_size != rewrite_item.size or _sha256(
                        destination) != rewrite_item.sha256:
                    raise DualIpodError(
                        f"Dual rewrite failed verification: {destination}")
            finally:
                if prepared is not None:
                    assert plan.rockbox_media_cache is not None
                    plan.rockbox_media_cache.discard(prepared)
            if rewrite_item.kind == "rockbox-media":
                progress_done += 1
                emit_progress(str(rewrite_item.relative_path))
                time.sleep(0.15)
        if plan.stock.requires_commit:
            stock_result = commit_stock_ipod_sync(
                plan.stock,
                on_track_committed=stock_track_committed,
            )
            stock_committed = True
        session.revalidate()
        for playlist_item in changed_playlists:
            destination = session.guard(playlist_item.relative_path)
            session.atomic_write(destination, playlist_item.payload)
            if playlist_item.sha256_before is None:
                new_playlist_paths.append(destination)
            else:
                rewritten_playlists.append(playlist_item)
            if _sha256(destination) != playlist_item.sha256:
                raise DualIpodError(
                    f"Rockbox playlist failed verification: {destination}")
        for playlist_item in plan.playlists:
            if not playlist_item.order_changed:
                continue
            destination = session.guard(playlist_item.relative_path)
            retimestamped_playlists.append(playlist_item)
            os.utime(
                destination,
                (playlist_item.desired_mtime, playlist_item.desired_mtime),
            )
            if (
                abs(destination.stat().st_mtime - playlist_item.desired_mtime)
                > _PLAYLIST_MTIME_TOLERANCE
            ):
                raise DualIpodError(
                    "Rockbox playlist order timestamp failed verification: "
                    f"{destination}")
        if plan.config_update.changed:
            session.atomic_write(config_path, plan.config_update.payload)
            config_published = True
            if config_path.read_bytes() != plan.config_update.payload:
                raise DualIpodError("Rockbox config failed verification")
        for delete_item in plan.file_deletes:
            destination = session.guard(delete_item.relative_path)
            destination.unlink()
            deleted_files.append(delete_item)
        for orphan in plan.orphan_deletes:
            destination = session.guard(orphan.relative_path)
            if (
                not destination.is_file()
                or destination.stat().st_size != orphan.size_before
                or (
                    orphan.kind not in {"shared-stock-media", "rockbox-media"}
                    and _sha256(destination) != orphan.sha256_before
                )
            ):
                raise DualIpodError(
                    f"Managed orphan changed before cleanup: "
                    f"{orphan.relative_path}")
            saved = session.guard(
                recovery / "orphans" / orphan.relative_path.parent
                / (orphan.relative_path.name + ".before"))
            saved.parent.mkdir(parents=True, exist_ok=True)
            session.revalidate()
            os.replace(destination, saved)
            deleted_orphan_items.append(orphan)
            deleted_orphans += 1
            deleted_orphan_bytes += orphan.size_before
            if orphan.kind in {"shared-stock-media", "rockbox-media"}:
                progress_done += 1
                emit_progress(str(orphan.relative_path))
                time.sleep(0.15)
        session.atomic_write(manifest_path, plan.manifest_payload)
        manifest_published = True
        manifest_hash = hashlib.sha256(plan.manifest_payload).hexdigest()
        if _sha256(manifest_path) != manifest_hash:
            raise DualIpodError("Dual manifest failed verification")
        session.revalidate()
    except Exception as original_exc:
        failures: list[str] = []
        try:
            if manifest_published and plan.manifest_payload_before is not None:
                session.atomic_write(
                    manifest_path, plan.manifest_payload_before)
            elif manifest_published:
                manifest_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - preserve all failures
            failures.append(f"manifest: {exc}")
        if config_published:
            try:
                if plan.config_update.payload_before is None:
                    config_path.unlink(missing_ok=True)
                else:
                    saved = session.guard(
                        recovery / "rockbox" / "config.cfg.before")
                    session.atomic_copy(saved, config_path)
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(f"Rockbox config: {exc}")
        for changed_playlist in reversed(rewritten_playlists):
            try:
                destination = session.guard(changed_playlist.relative_path)
                saved = session.guard(
                    recovery / "playlists" / changed_playlist.relative_path.parent
                    / (changed_playlist.relative_path.name + ".before"))
                session.atomic_copy(saved, destination)
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(
                    f"playlist {changed_playlist.relative_path}: {exc}")
        for path in reversed(new_playlist_paths):
            try:
                session.guard(path).unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"playlist {path}: {exc}")
        for playlist_item in reversed(retimestamped_playlists):
            if playlist_item.mtime_before is None:
                continue
            try:
                destination = session.guard(playlist_item.relative_path)
                if destination.exists():
                    os.utime(
                        destination,
                        (playlist_item.mtime_before,
                         playlist_item.mtime_before),
                    )
            except OSError as exc:
                failures.append(
                    f"playlist order {playlist_item.relative_path}: {exc}")
        for orphan in reversed(deleted_orphan_items):
            try:
                destination = session.guard(orphan.relative_path)
                saved = session.guard(
                    recovery / "orphans" / orphan.relative_path.parent
                    / (orphan.relative_path.name + ".before"))
                destination.unlink(missing_ok=True)
                session.revalidate()
                os.replace(saved, destination)
                if (
                    destination.stat().st_size != orphan.size_before
                    or (
                        orphan.kind not in {"shared-stock-media", "rockbox-media"}
                        and _sha256(destination) != orphan.sha256_before
                    )
                ):
                    raise DualIpodError(
                        f"Restored orphan failed verification: {destination}")
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(f"orphan {orphan.relative_path}: {exc}")
        for delete_item in reversed(deleted_files):
            try:
                destination = session.guard(delete_item.relative_path)
                saved = session.guard(
                    recovery / "deleted" / delete_item.relative_path.parent
                    / (delete_item.relative_path.name + ".before"))
                session.atomic_copy(saved, destination)
                if (
                    destination.stat().st_size != delete_item.size_before
                    or _sha256(destination) != delete_item.sha256_before
                ):
                    raise DualIpodError(
                        f"Restored deleted file failed verification: {destination}")
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(
                    f"deleted file {delete_item.relative_path}: {exc}")
        if stock_committed and stock_result is not None:
            try:
                rollback_committed_stock_ipod_sync(plan.stock, stock_result)
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(f"stock rollback: {exc}")
        for rewrite_item in reversed(rewritten):
            try:
                destination = session.guard(rewrite_item.relative_path)
                saved = session.guard(
                    recovery / "files" / rewrite_item.relative_path.parent
                    / (rewrite_item.relative_path.name + ".before"))
                if rewrite_item.kind == "rockbox-media":
                    destination.unlink(missing_ok=True)
                    session.revalidate()
                    os.replace(saved, destination)
                else:
                    session.atomic_copy(saved, destination)
                if (
                    destination.stat().st_size != rewrite_item.size_before
                    or (
                        rewrite_item.kind != "rockbox-media"
                        and _sha256(destination) != rewrite_item.sha256_before
                    )
                ):
                    raise DualIpodError(
                        f"Restored dual file failed verification: {destination}")
            except Exception as exc:  # noqa: BLE001 - preserve all failures
                failures.append(
                    f"rewritten file {rewrite_item.relative_path}: {exc}")
        for path in reversed(copied):
            try:
                session.guard(path).unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"copied file {path}: {exc}")
        if failures:
            raise DualIpodError(
                "Dual sync failed and rollback was incomplete: "
                + "; ".join(failures)) from original_exc
        raise

    if stock_result is None:
        stock_result = StockIpodCommitResult(
            recovery_directory=recovery_abs,
            copied_tracks=0,
            recovered_tracks=0,
            reused_tracks=plan.stock.reused_track_count,
            rewritten_tracks=0,
            playlists=plan.stock.playlist_names,
            database_sha256=plan.stock.database_sha256_before,
        )
    written_paths = {
        *(item.relative_path for item in plan.media_copies),
        *(item.relative_path for item in plan.artwork_copies),
        *(item.relative_path for item in plan.file_rewrites),
        *(item.relative_path for item in plan.file_deletes),
        *(item.relative_path for item in plan.orphan_deletes),
        *(item.relative_path for item in changed_playlists),
        *(tuple([plan.config_update.relative_path])
          if plan.config_update.changed else ()),
        *(item.relative_path for item in plan.stock.copies),
        *(item.relative_path for item in plan.stock.rewrites),
        manifest_path.relative_to(plan.target),
    }
    _clean_exact_appledouble(session, written_paths)
    _clean_empty_rockbox_dirs(session)
    cleanup_failures: list[str] = list(stock_result.cleanup_failures)
    transient_media = [
        session.guard(
            recovery / "files" / item.relative_path.parent
            / (item.relative_path.name + ".before"))
        for item in plan.file_rewrites
        if item.kind == "rockbox-media"
    ]
    transient_media.extend(
        session.guard(
            stock_result.recovery_directory / "media" / item.relative_path.parent
            / (item.relative_path.name + ".before"))
        for item in plan.stock.rewrites
        if plan.stock.dual_boot
    )
    transient_media.extend(
        session.guard(
            recovery / "orphans" / item.relative_path.parent
            / (item.relative_path.name + ".before"))
        for item in plan.orphan_deletes
    )
    for saved in transient_media:
        try:
            saved.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_failures.append(f"transient recovery {saved}: {exc}")
    return DualIpodCommitResult(
        stock=stock_result,
        copied_media=len(plan.media_copies),
        copied_artwork=len(plan.artwork_copies),
        recovered_media=sum(
            1 for item in plan.file_adoptions
            if item.kind == "rockbox-media"),
        recovered_artwork=sum(
            1 for item in plan.file_adoptions
            if item.kind == "rockbox-artwork"),
        playlists=len(changed_playlists),
        manifest_sha256=hashlib.sha256(plan.manifest_payload).hexdigest(),
        deleted_orphans=deleted_orphans,
        deleted_orphan_bytes=deleted_orphan_bytes,
        cleanup_failures=tuple(cleanup_failures),
    )
