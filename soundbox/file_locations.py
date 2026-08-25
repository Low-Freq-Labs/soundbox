"""Persistent, precise locations for SoundBox-referenced source audio.

Normal paths are the fast path.  A small macOS Core Foundation bookmark is the
persistent identity path: unlike a pathname, Apple can resolve it after the
file or one of its parent folders moves.  No directory walk is involved.

Older ``library.json`` records predate bookmarks.  For those only, or when a
bookmark cannot follow a cross-volume copy, one batched Spotlight metadata
query asks for exact filename + recorded byte size.  Ambiguous results remain
missing; the explicit folder-picker relinker is the final fallback.
"""

from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BOOKMARK_KEY = "file_bookmark"
_CF_PATH = (
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_RESOLVE_OPTIONS = (1 << 8) | (1 << 9)  # no UI, no volume mounting
_PATH_BUFFER_BYTES = 32_768
_SPOTLIGHT_QUERY_BYTES = 48_000
_SPOTLIGHT_TIMEOUT_SECONDS = 12


class _CoreFoundation:
    """The narrow CFURL bookmark ABI used here, loaded without PyObjC."""

    def __init__(self) -> None:
        cf = ctypes.CDLL(_CF_PATH)
        index = ctypes.c_long
        flags = ctypes.c_ulong

        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, index, ctypes.c_ubyte]
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateBookmarkData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, flags, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        cf.CFURLCreateBookmarkData.restype = ctypes.c_void_p
        cf.CFURLCreateByResolvingBookmarkData.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, flags, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_void_p)]
        cf.CFURLCreateByResolvingBookmarkData.restype = ctypes.c_void_p
        cf.CFURLGetFileSystemRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_char_p, index]
        cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_ubyte
        cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        cf.CFDataGetLength.restype = index
        cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_ubyte)
        cf.CFDataCreate.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), index]
        cf.CFDataCreate.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cf = cf

    def release(self, value: int | None) -> None:
        if value:
            self.cf.CFRelease(value)


_CORE_FOUNDATION: _CoreFoundation | None = None
_CORE_FOUNDATION_FAILED = False


def _core_foundation() -> _CoreFoundation | None:
    global _CORE_FOUNDATION, _CORE_FOUNDATION_FAILED
    if sys.platform != "darwin" or _CORE_FOUNDATION_FAILED:
        return None
    if _CORE_FOUNDATION is None:
        try:
            _CORE_FOUNDATION = _CoreFoundation()
        except (AttributeError, OSError):
            _CORE_FOUNDATION_FAILED = True
            return None
    return _CORE_FOUNDATION


def create_bookmark(path: str | Path) -> str | None:
    """Return opaque base64 CFURL bookmark data without reading audio bytes."""
    bridge = _core_foundation()
    source = Path(path)
    if bridge is None or not source.is_file():
        return None
    raw = os.fsencode(source)
    url = bridge.cf.CFURLCreateFromFileSystemRepresentation(
        None, raw, len(raw), False)
    if not url:
        return None
    error = ctypes.c_void_p()
    data = None
    try:
        data = bridge.cf.CFURLCreateBookmarkData(
            None, url, 0, None, None, ctypes.byref(error))
        if not data:
            return None
        size = int(bridge.cf.CFDataGetLength(data))
        pointer = bridge.cf.CFDataGetBytePtr(data)
        if size <= 0 or not pointer:
            return None
        encoded = base64.b64encode(ctypes.string_at(pointer, size))
        return encoded.decode("ascii")
    finally:
        bridge.release(data)
        bridge.release(url)
        bridge.release(error.value)


def resolve_bookmark(encoded: object) -> Path | None:
    """Resolve one bookmark without UI, mounting volumes, or searching paths."""
    bridge = _core_foundation()
    if bridge is None or not isinstance(encoded, str) or not encoded:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    array = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    data = bridge.cf.CFDataCreate(None, array, len(raw))
    if not data:
        return None
    stale = ctypes.c_ubyte()
    error = ctypes.c_void_p()
    url = None
    try:
        url = bridge.cf.CFURLCreateByResolvingBookmarkData(
            None, data, _RESOLVE_OPTIONS, None, None,
            ctypes.byref(stale), ctypes.byref(error))
        if not url:
            return None
        buffer = ctypes.create_string_buffer(_PATH_BUFFER_BYTES)
        if not bridge.cf.CFURLGetFileSystemRepresentation(
                url, True, buffer, len(buffer)):
            return None
        return Path(os.fsdecode(buffer.value))
    finally:
        bridge.release(url)
        bridge.release(data)
        bridge.release(error.value)


def _valid_candidate(path: Path, recorded_size: int) -> bool:
    try:
        return path.is_file() and (
            recorded_size <= 0 or path.stat().st_size == recorded_size)
    except OSError:
        return False


def _md_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _spotlight_terms(records: dict[str, dict]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    seen: set[tuple[str, int]] = set()
    for rec in records.values():
        old = Path(str(rec.get("path", "")))
        size = int(rec.get("size", 0) or 0)
        signature = (old.name.casefold(), size)
        if not old.name or size <= 0 or signature in seen:
            continue
        seen.add(signature)
        term = (
            f'(kMDItemFSName == "{_md_escape(old.name)}"cd '
            f'&& kMDItemFSSize == {size})')
        terms.append((term, old.name))
    return terms


def _chunk_terms(terms: list[tuple[str, str]]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for term, _name in terms:
        addition = len(term.encode("utf-8")) + (4 if current else 0)
        if current and size + addition > _SPOTLIGHT_QUERY_BYTES:
            chunks.append(" || ".join(current))
            current = []
            size = 0
        current.append(term)
        size += addition
    if current:
        chunks.append(" || ".join(current))
    return chunks


def spotlight_matches(records: dict[str, dict]) -> dict[str, Path]:
    """Strict indexed migration/fallback lookup; never walks the filesystem.

    Results require exact case-insensitive filename and recorded byte size, and
    exactly one live match.  A rename, unknown size, duplicate, stale index, or
    unavailable Spotlight database stays unresolved for the manual picker.
    """
    if sys.platform != "darwin" or not Path("/usr/bin/mdfind").is_file():
        return {}
    terms = _spotlight_terms(records)
    if not terms:
        return {}
    candidates: dict[tuple[str, int], set[Path]] = {}
    for query in _chunk_terms(terms):
        try:
            result = subprocess.run(
                ["/usr/bin/mdfind", "-0", query], capture_output=True,
                check=False, timeout=_SPOTLIGHT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0:
            continue
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            path = Path(os.fsdecode(raw))
            try:
                signature = (path.name.casefold(), path.stat().st_size)
            except OSError:
                continue
            candidates.setdefault(signature, set()).add(path)

    found: dict[str, Path] = {}
    for track_id, rec in records.items():
        old = Path(str(rec.get("path", "")))
        size = int(rec.get("size", 0) or 0)
        matches = candidates.get((old.name.casefold(), size), set())
        if size > 0 and len(matches) == 1:
            candidate = next(iter(matches))
            if _valid_candidate(candidate, size):
                found[track_id] = candidate
    return found


@dataclass(frozen=True)
class LocationUpdate:
    expected_path: str
    path: Path | None = None
    bookmark: str | None = None


@dataclass(frozen=True)
class LocationRepair:
    updates: dict[str, LocationUpdate]
    missing_count: int
    unresolved_count: int

    @property
    def moved_count(self) -> int:
        return sum(1 for update in self.updates.values()
                   if update.path is not None)


def repair_locations(imported: dict[str, dict]) -> LocationRepair:
    """Backfill bookmarks and precisely resolve moved imported files.

    This function is worker-safe: it treats ``imported`` as an immutable
    snapshot and returns guarded updates for the UI thread to apply.
    """
    updates: dict[str, LocationUpdate] = {}
    unresolved: dict[str, dict] = {}
    missing_count = 0
    for track_id, rec in imported.items():
        expected = str(rec.get("path", ""))
        old = Path(expected)
        size = int(rec.get("size", 0) or 0)
        if _valid_candidate(old, size):
            if not rec.get(BOOKMARK_KEY):
                bookmark = create_bookmark(old)
                if bookmark:
                    updates[track_id] = LocationUpdate(
                        expected_path=expected, bookmark=bookmark)
            continue

        missing_count += 1
        candidate = resolve_bookmark(rec.get(BOOKMARK_KEY))
        if candidate is not None and _valid_candidate(candidate, size):
            updates[track_id] = LocationUpdate(
                expected_path=expected, path=candidate,
                bookmark=create_bookmark(candidate))
        else:
            unresolved[track_id] = rec

    spotlight = spotlight_matches(unresolved) if unresolved else {}
    for track_id, candidate in spotlight.items():
        updates[track_id] = LocationUpdate(
            expected_path=str(unresolved[track_id].get("path", "")),
            path=candidate, bookmark=create_bookmark(candidate))

    return LocationRepair(
        updates=updates,
        missing_count=missing_count,
        unresolved_count=len(unresolved) - len(spotlight),
    )
