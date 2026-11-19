"""Prepared artwork and read-only layout planning for stock iPods.

The curated JPEG cache is the source of truth. This module stores disposable,
content-addressed Video/Classic RGB565 profiles and assembles an ArtworkDB/ithmb
layout without writing the mounted device. Observed descriptors choose layouts.

Clickwheel remains the pinned binary-format renderer.  Its public image
converter and pinned ArtworkDB chunk builders are wrapped here so SoundBox can
feed already-prepared pixels instead of modifying audio tags or re-decoding
JPEGs during a device sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from collections.abc import Callable
import hashlib
import heapq
import json
import os
import struct
from pathlib import Path
import tempfile
from typing import Any

from .preparation_inventory import PreparationInventory, signature
from .operation_control import checkpoint

PROFILE = "ma446-video-rgb565-v1"
FORMATS: dict[int, tuple[int, int]] = {
    1028: (100, 100),
    1029: (200, 200),
}

CLASSIC_PROFILE = "classic-rgb565-1055-1060-1061-v1"
CLASSIC_55_PROFILE = "classic-rgb565-1055-1060-1061-55-stride56-v1"
CLASSIC_FORMATS = {1055: (128, 128), 1060: (320, 320), 1061: (56, 56)}
CLASSIC_55_FORMATS = {1055: (128, 128), 1060: (320, 320), 1061: (55, 55)}
_PROFILES = {PROFILE: FORMATS, CLASSIC_PROFILE: CLASSIC_FORMATS, CLASSIC_55_PROFILE: CLASSIC_55_FORMATS}


def _profile(value: str) -> str:
    value = PROFILE if value == 'video-rgb565-1028-1029-v1' else value
    if value not in _PROFILES:raise StockArtworkError('Unsupported artwork pixel profile')
    return value


def _pixel_size(formats, format_id):
    width,height = formats[format_id]
    return (width + width % 2) * height * 2


def _database_profile(data: bytes) -> str:
    from .stock_ipod_format import _artwork_formats
    try:observed = _artwork_formats(data)
    except (ValueError,struct.error) as exc:
        raise StockArtworkError('ArtworkDB format count/size validation failed: '+str(exc)) from exc
    for profile,formats in _PROFILES.items():
        if observed == {key:_pixel_size(formats,key) for key in formats}:return profile
    raise StockArtworkError('Existing ArtworkDB pixel formats are unsupported')


class StockArtworkError(RuntimeError):
    """A prepared-artwork cache entry or staged layout is invalid."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
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
class StockArtworkPayload:
    format_id: int
    path: Path
    size: int
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class PreparedStockArtwork:
    cache_key: str
    source_sha256: str
    source_size: int
    payloads: tuple[StockArtworkPayload, ...]
    profile: str = PROFILE

    def payload(self, format_id: int) -> StockArtworkPayload:
        for item in self.payloads:
            if item.format_id == format_id:
                return item
        raise StockArtworkError(
            f"Prepared artwork {self.cache_key} lacks format {format_id}")


@dataclass(frozen=True)
class StockArtworkSegment:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class StockIthmbPlan:
    relative_path: Path
    segments: tuple[StockArtworkSegment, ...]
    size: int
    sha256: str
    before_size: int = 0
    before_mtime_ns: int = 0


@dataclass(frozen=True)
class StockArtworkRangeWrite:
    """One fixed-size payload written only to an unreferenced ithmb slot."""

    format_id: int
    relative_path: Path
    offset: int
    segment: StockArtworkSegment


@dataclass(frozen=True)
class StockArtworkSlot:
    image_id: int
    song_id: int
    source_size: int
    offsets: tuple[tuple[int, int], ...]
    cache_key: str = ""
    raw: bytes = field(default=b"", repr=False, compare=False)
    track_dbids: tuple[int, ...] = ()

    def offset(self, format_id: int) -> int:
        for current, value in self.offsets:
            if current == format_id:
                return value
        raise StockArtworkError(
            f"Artwork image {self.image_id} lacks format {format_id}")


class _FreeArtworkSlots:
    """Consume ascending free offsets while indexing only pixels already read."""

    def __init__(self, offsets: list[int]):
        self.available = set(offsets)
        self.order = deque(offsets)
        self.unread = iter(offsets)
        self.matches: dict[str, list[int]] = {}
        self.failed: list[int] = []

    def pop_matching(self, digest: str, read_digest: Callable[[int], str | None]) -> int | None:
        matches = self.matches.setdefault(digest, [])
        while matches and matches[0] not in self.available:
            heapq.heappop(matches)
        candidate = matches[0] if matches else None
        failed = []
        for offset in self.failed:
            if offset not in self.available:
                continue
            # A prior transient failure before a cached match still needs a
            # fresh attempt, just as the original ascending search did.
            if candidate is not None and offset > candidate:
                failed.append(offset)
                continue
            value = read_digest(offset)
            if value is None:
                failed.append(offset)
            else:
                heapq.heappush(self.matches.setdefault(value, []), offset)
                if value == digest:
                    candidate = offset
        self.failed = failed
        if candidate is None:
            for offset in self.unread:
                if offset not in self.available:
                    continue
                value = read_digest(offset)
                if value is None:
                    self.failed.append(offset)
                else:
                    heapq.heappush(self.matches.setdefault(value, []), offset)
                    if value == digest:
                        candidate = offset
                        break
        if candidate is not None:
            self.available.remove(candidate)
            heapq.heappop(matches)
        return candidate

    def pop_first(self) -> int | None:
        while self.order and self.order[0] not in self.available:
            self.order.popleft()
        if not self.order:
            return None
        offset = self.order.popleft()
        self.available.remove(offset)
        return offset


@dataclass(frozen=True)
class RenderedStockArtwork:
    artwork_db: bytes
    ithmb_files: tuple[StockIthmbPlan, ...]
    linked_tracks: int
    unique_images: int
    range_writes: tuple[StockArtworkRangeWrite, ...] = ()
    slots: tuple[StockArtworkSlot, ...] = ()
    incremental: bool = False
    publish_artwork_db: bool = True
    updated_dbids: tuple[int, ...] = ()
    profile: str = PROFILE
    preserved_links: tuple[tuple[int, int, int], ...] = ()

    @property
    def bytes_to_copy(self) -> int:
        if self.incremental:
            pixels = sum(item.segment.size for item in self.range_writes)
            return pixels + (len(self.artwork_db) if self.publish_artwork_db else 0)
        return len(self.artwork_db) + sum(item.size for item in self.ithmb_files)


class StockArtworkCache:
    """Disposable content-addressed derivatives, isolated by pixel profile."""

    def __init__(self, root: str | Path, *, profile: str = PROFILE):
        self.profile = _profile(profile)
        self.formats = _PROFILES[self.profile]
        self.root = Path(root)
        self._source_index_path = self.root / self.profile / "source-index.json"
        try:
            raw = json.loads(self._source_index_path.read_text(encoding="utf-8"))
            self._source_index = {
                key: row for key, row in (raw.items() if isinstance(raw, dict) else ())
                if isinstance(row, dict)
                and all(type(row.get(key)) is int
                        for key in ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns'))
                and isinstance(row.get("source_sha256"), str)
                and len(row["source_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in row["source_sha256"])
            }
        except (OSError, ValueError):
            self._source_index = {}
        self._source_index_dirty = False
        self._preparation = PreparationInventory(self.root / self.profile / 'preparation-index.json')

    def for_profile(self, profile: str) -> StockArtworkCache:
        selected = _profile(profile)
        return self if selected == self.profile else StockArtworkCache(self.root, profile=selected)

    @property
    def format_ids(self) -> tuple[int, ...]:return tuple(self.formats)

    def preparation_signature(self, image_path: str | Path) -> str | None:
        """Metadata scheduling hint only; selected exports still verify pixels."""
        try:
            path = Path(image_path).resolve(strict=True)
            row = self._source_index.get(str(path)) or {}
            digest = row.get('source_sha256', '')
            if not isinstance(digest, str) or len(digest) != 64:
                return None
            entry = self._entry(self._cache_key(digest))
            paths = [path, entry / 'manifest.json']
            paths += [entry / f'F{format_id}.rgb565' for format_id in self.formats]
            return signature(paths, [1, self.profile, list(self.formats.items())])
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def is_prepared(self, image_path: str | Path) -> bool:
        return self._preparation.matches(str(image_path), self.preparation_signature(image_path))

    def remember_prepared(self, image_path: str | Path, before: str | None) -> None:
        self._preparation.remember(str(image_path), before, self.preparation_signature(image_path))

    def _save_source_index(self) -> None:
        if not self._source_index_dirty:
            return
        _atomic_write(
            self._source_index_path,
            json.dumps(
                self._source_index, indent=2, sort_keys=True,
            ).encode("utf-8") + b"\n",
        )
        self._source_index_dirty = False

    def flush_source_index(self) -> None:
        """Persist pending source-stat lookups after a preparation batch."""
        self._save_source_index()
        self._preparation.flush()

    def _remember_source(self, path: Path, stat: os.stat_result, sha256: str) -> None:
        key = str(path)
        row = {
            "dev": stat.st_dev,
            "ino": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "source_sha256": sha256,
        }
        if self._source_index.get(key) != row:
            self._source_index[key] = row
            self._source_index_dirty = True

    def _entry(self, cache_key: str) -> Path:
        return self.root / self.profile / cache_key[:2] / cache_key

    def _cache_key(self, source_sha256: str) -> str:
        material = f"{self.profile}:{source_sha256}".encode("ascii")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _source_epoch(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def _capture_source(cls, source: Path) -> tuple[bytes, os.stat_result]:
        for _attempt in range(2):
            checkpoint()
            if source.resolve(strict=True) != source:
                raise StockArtworkError(f'Curated artwork identity changed: {source}')
            before = source.stat()
            with source.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                data = handle.read(before.st_size + 1)
                closed = os.fstat(handle.fileno())
            checkpoint()
            after = source.stat()
            if source.resolve(strict=True) != source:
                raise StockArtworkError(f'Curated artwork identity changed: {source}')
            if (cls._source_epoch(before) == cls._source_epoch(opened)
                    == cls._source_epoch(closed) == cls._source_epoch(after)
                    and len(data) == after.st_size):
                return data, after
        raise StockArtworkError(f'Curated artwork remained unstable: {source}')

    def _load(
        self,
        cache_key: str,
        *,
        source_sha256: str,
        source_size: int,
        verify_hashes: bool,
    ) -> PreparedStockArtwork | None:
        entry = self._entry(cache_key)
        manifest_path = entry / "manifest.json"
        try:
            if manifest_path.stat().st_size > 16 * 1024:
                return None
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("profile") != self.profile
                or manifest.get("source_sha256") != source_sha256
                or int(manifest.get("source_size", -1)) != source_size
            ):
                return None
        except (OSError, ValueError, TypeError, OverflowError):
            return None
        payloads: list[StockArtworkPayload] = []
        rows = manifest.get("formats")
        if not isinstance(rows, list):
            return None
        try:
            by_id = {int(row.get("format_id") or 0): row for row in rows if isinstance(row, dict)}
        except (ValueError, TypeError, OverflowError):
            return None
        for format_id, (width, height) in self.formats.items():
            row = by_id.get(format_id)
            if row is None:
                return None
            path = entry / f"F{format_id}.rgb565"
            expected_size = _pixel_size(self.formats, format_id)
            expected_hash = str(row.get("sha256") or "")
            try:
                size = int(row.get("size") or -1)
                if size != expected_size or path.stat().st_size != size:
                    return None
                if verify_hashes and _sha256_file(path) != expected_hash:
                    return None
            except (OSError, ValueError, TypeError, OverflowError):
                return None
            payloads.append(StockArtworkPayload(
                format_id, path, size, expected_hash, width, height))
        return PreparedStockArtwork(
            cache_key, source_sha256, source_size, tuple(payloads), self.profile)

    def prepare(
        self,
        image_path: str | Path,
        *,
        verify_cache: bool = True,
        save_index: bool = True,
    ) -> PreparedStockArtwork:
        """Prepare one JPEG; sync callers verify pixels, warmers may trust metadata."""
        checkpoint()
        path = Path(image_path)
        try:
            canonical = path.resolve(strict=True)
            stat = canonical.stat()
        except OSError as exc:
            raise StockArtworkError(f"Could not inspect curated artwork {path}: {exc}") from exc
        indexed = self._source_index.get(str(canonical)) or {}
        indexed_sha = str(indexed.get("source_sha256") or "")
        if (
            len(indexed_sha) == 64
            and tuple(indexed.get(key) for key in
                      ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns')) == self._source_epoch(stat)
        ):
            indexed_key = self._cache_key(indexed_sha)
            ready = self._load(
                indexed_key,
                source_sha256=indexed_sha,
                source_size=stat.st_size,
                verify_hashes=verify_cache,
            )
            if ready is not None:
                checkpoint()
                if path.resolve(strict=True) != canonical or canonical.resolve(strict=True) != canonical:
                    raise StockArtworkError(f'Curated artwork identity changed: {path}')
                if self._source_epoch(canonical.stat()) == self._source_epoch(stat):
                    return ready
        try:
            data, stat = self._capture_source(canonical)
            if path.resolve(strict=True) != canonical:
                raise StockArtworkError(f'Curated artwork identity changed: {path}')
        except OSError as exc:
            raise StockArtworkError(f"Could not read curated artwork {path}: {exc}") from exc
        source_sha256 = _sha256_bytes(data)
        source_size = len(data)
        checkpoint()
        cache_key = self._cache_key(source_sha256)
        ready = self._load(
            cache_key,
            source_sha256=source_sha256,
            source_size=source_size,
            verify_hashes=verify_cache,
        )
        if ready is not None:
            self._remember_source(canonical, stat, source_sha256)
            if save_index:
                self._save_source_index()
            return ready

        # Cache hits need no image-conversion runtime. In particular, keep
        # NumPy/Clickwheel's artwork writer out of ordinary desktop startup.
        from clickwheel.ipod.artworkdb_writer.rgb565 import convert_art_for_ipod  # type: ignore[import-untyped]

        entry = self._entry(cache_key)
        rows: list[dict[str, Any]] = []
        for format_id, (width, height) in self.formats.items():
            checkpoint()
            if width % 2:
                from PIL import Image
                from clickwheel.ipod.artworkdb_writer.rgb565 import image_from_bytes, rgb888_to_rgb565
                image = image_from_bytes(data)
                if image is None:raise StockArtworkError('Curated artwork could not be decoded')
                resized = image.resize((width, height), Image.Resampling.LANCZOS)
                converted = {'data':rgb888_to_rgb565(resized,width,height,stride=width+1)}
            else:
                converted = convert_art_for_ipod(data, format_id)
            if converted is None:
                raise StockArtworkError(
                    f"Could not convert curated artwork {path} to format {format_id}")
            payload = bytes(converted["data"])
            expected = _pixel_size(self.formats, format_id)
            if len(payload) != expected:
                raise StockArtworkError(
                    f"Format {format_id} produced {len(payload)} bytes; expected {expected}")
            payload_path = entry / f"F{format_id}.rgb565"
            _atomic_write(payload_path, payload)
            rows.append({
                "format_id": format_id,
                "width": width,
                "height": height,
                "size": len(payload),
                "sha256": _sha256_bytes(payload),
            })
        manifest = {
            "profile": self.profile,
            "source_sha256": source_sha256,
            "source_size": source_size,
            "formats": rows,
        }
        _atomic_write(
            entry / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        prepared = self._load(
            cache_key,
            source_sha256=source_sha256,
            source_size=source_size,
            verify_hashes=True,
        )
        if prepared is None:
            raise StockArtworkError(f"Prepared artwork failed validation: {path}")
        self._remember_source(canonical, stat, source_sha256)
        if save_index:
            self._save_source_index()
        return prepared

    def preserve_preconverted(
        self,
        formats: dict[int, bytes],
        *,
        source_size: int,
    ) -> PreparedStockArtwork:
        """Adopt verified device pixels into the disposable local cache."""
        if set(formats) != set(self.formats):
            raise StockArtworkError(
                f"Existing artwork formats are incomplete: {sorted(formats)}")
        digest = hashlib.sha256()
        for format_id in sorted(formats):
            digest.update(format_id.to_bytes(4, "little"))
            digest.update(formats[format_id])
        source_sha256 = digest.hexdigest()
        cache_key = self._cache_key(source_sha256)
        ready = self._load(
            cache_key,
            source_sha256=source_sha256,
            source_size=source_size,
            verify_hashes=True,
        )
        if ready is not None:
            return ready
        entry = self._entry(cache_key)
        rows = []
        for format_id, (width, height) in self.formats.items():
            payload = bytes(formats[format_id])
            expected = _pixel_size(self.formats, format_id)
            if len(payload) != expected:
                raise StockArtworkError(
                    f"Existing format {format_id} has {len(payload)} bytes; expected {expected}")
            _atomic_write(entry / f"F{format_id}.rgb565", payload)
            rows.append({
                "format_id": format_id,
                "width": width,
                "height": height,
                "size": len(payload),
                "sha256": _sha256_bytes(payload),
            })
        _atomic_write(
            entry / "manifest.json",
            json.dumps({
                "profile": self.profile,
                "source_sha256": source_sha256,
                "source_size": source_size,
                "formats": rows,
            }, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        prepared = self._load(
            cache_key,
            source_sha256=source_sha256,
            source_size=source_size,
            verify_hashes=True,
        )
        if prepared is None:
            raise StockArtworkError("Preserved artwork failed cache validation")
        return prepared


def _combined_sha256(segments: list[StockArtworkSegment]) -> str:
    digest = hashlib.sha256()
    for segment in segments:
        if segment.size != segment.path.stat().st_size:
            raise StockArtworkError(f"Prepared payload changed size: {segment.path}")
        segment_digest = hashlib.sha256()
        size = 0
        with segment.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checkpoint()
                digest.update(chunk)
                segment_digest.update(chunk)
                size += len(chunk)
        if size != segment.size or segment_digest.hexdigest() != segment.sha256:
            raise StockArtworkError(f"Prepared payload changed content: {segment.path}")
    return digest.hexdigest()


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise StockArtworkError("ArtworkDB ended inside a 16-bit field")
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise StockArtworkError("ArtworkDB ended inside a 32-bit field")
    return int.from_bytes(data[offset:offset + 4], "little")


def _u64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise StockArtworkError("ArtworkDB ended inside a 64-bit field")
    return int.from_bytes(data[offset:offset + 8], "little")


def _datasets(data: bytes):
    header = _u32(data,4)
    if header < 132 or _u32(data,8) != len(data):raise StockArtworkError('ArtworkDB root framing is invalid')
    cursor = header;result=[];seen=set()
    for _ in range(_u32(data,20)):
        if data[cursor:cursor+4] != b'mhsd':raise StockArtworkError('ArtworkDB dataset is invalid')
        length,total,kind = _u32(data,cursor+4),_u32(data,cursor+8),_u16(data,cursor+12)
        if length < 16 or total < length or cursor+total > len(data) or kind in seen:
            raise StockArtworkError('ArtworkDB dataset framing is invalid')
        seen.add(kind);result.append((kind,data[cursor:cursor+length],data[cursor+length:cursor+total]))
        cursor += total
    if cursor != len(data) or seen != {1,2,3}:raise StockArtworkError('ArtworkDB dataset inventory is unsupported')
    return data[:header],result


def _records(data: bytes):
    _root,datasets = _datasets(data)
    body = next(body for kind,_header,body in datasets if kind == 1)
    if body[:4] != b'mhli':raise StockArtworkError('ArtworkDB image list is unavailable')
    length = _u32(body,4);cursor=length;result={}
    if length < 12 or length > len(body):raise StockArtworkError('ArtworkDB image list framing is invalid')
    for _ in range(_u32(body,8)):
        if body[cursor:cursor+4] != b'mhii':raise StockArtworkError('ArtworkDB image entry is invalid')
        header,total,identity = _u32(body,cursor+4),_u32(body,cursor+8),_u32(body,cursor+16)
        if header < 52 or total < header or cursor+total > len(body) or not identity or identity in result:
            raise StockArtworkError('ArtworkDB image entry framing is invalid')
        result[identity] = body[cursor:cursor+total];cursor+=total
    if cursor != len(body):raise StockArtworkError('ArtworkDB image list has trailing data')
    return body[:length],result


def _children(record: bytes):
    cursor = _u32(record,4);result=[]
    for _ in range(_u32(record,12)):
        if record[cursor:cursor+4] != b'mhod':raise StockArtworkError('ArtworkDB image child is invalid')
        header,total,kind = _u32(record,cursor+4),_u32(record,cursor+8),_u16(record,cursor+12)
        if header < 24 or total < header or cursor+total > len(record):
            raise StockArtworkError('ArtworkDB image child framing is invalid')
        result.append((kind,record[cursor:cursor+total]));cursor+=total
    if cursor != len(record):raise StockArtworkError('ArtworkDB image entry has trailing data')
    return result


def _images(record: bytes, formats):
    result={}
    for kind,child in _children(record):
        if kind != 2:continue  # Preserve opaque native children, including Classic MHAF.
        ni = _u32(child,4)
        if child[ni:ni+4] != b'mhni':raise StockArtworkError('ArtworkDB image location is invalid')
        header,total = _u32(child,ni+4),_u32(child,ni+8)
        fid,offset,size = _u32(child,ni+16),_u32(child,ni+20),_u32(child,ni+24)
        if header < 44 or ni+total != len(child) or total < header or fid not in formats or fid in result:
            raise StockArtworkError('ArtworkDB image location framing/format is invalid')
        width,height = formats[fid]
        vertical,horizontal = struct.unpack_from('<hh',child,ni+28)
        if (size != _pixel_size(formats,fid) or min(vertical,horizontal) < 0
                or not 0 < _u16(child,ni+34) <= width-horizontal
                or not 0 < _u16(child,ni+32) <= height-vertical):
            raise StockArtworkError('ArtworkDB image pixel dimensions are invalid')
        name = ni+header
        if _u32(child,ni+12) != 1 or child[name:name+4] != b'mhod' or _u16(child,name+12) != 3:
            raise StockArtworkError('ArtworkDB image filename is unavailable')
        name_header,name_total = _u32(child,name+4),_u32(child,name+8)
        text = name+name_header;count = _u32(child,text)
        if (name_header < 24 or name+name_total != len(child) or text+12+count > len(child)
                or child[text+4] != 2 or count > 256):
            raise StockArtworkError('ArtworkDB image filename framing is invalid')
        if child[text+12:text+12+count].decode('utf-16-le') != f':F{fid}_1.ithmb':
            raise StockArtworkError('ArtworkDB image targets an unsupported ithmb filename')
        result[fid]=(offset,size)
    if set(result) != set(formats):raise StockArtworkError('ArtworkDB image formats are incomplete')
    return result


def _preserve_artwork(reference: bytes, rendered: bytes, updated: set[int], *, relocate=False) -> bytes:
    """Retain native root/list/opaque children; replace only selected image geometry."""
    if not reference:return rendered
    root,datasets = _datasets(reference);list_header,before = _records(reference)
    _new_list,after = _records(rendered);records=[]
    order = [identity for identity in before if identity in after]
    order.extend(identity for identity in after if identity not in before)
    for identity in order:
        new = after[identity]
        old=before.get(identity)
        if old is None:records.append(new);continue
        if _u64(old,20) != _u64(new,20):raise StockArtworkError('Artwork image changed its song identity')
        changed = identity in updated
        if not changed and not relocate:records.append(old);continue
        old_header,new_header = _u32(old,4),_u32(new,4)
        if old_header != new_header:raise StockArtworkError('Artwork image header format changed')
        images={_u32(child,_u32(child,4)+16):child for kind,child in _children(new) if kind == 2}
        children=[]
        for kind,child in _children(old):
            if kind == 2:
                candidate=images.pop(_u32(child,_u32(child,4)+16))
                if not changed:
                    preserved=bytearray(child);ni=_u32(child,4);new_ni=_u32(candidate,4)
                    if child[ni+24:ni+28] != candidate[new_ni+24:new_ni+28]:
                        raise StockArtworkError('Preserved artwork pixel format changed')
                    preserved[ni+20:ni+24]=candidate[new_ni+20:new_ni+24]
                    candidate=bytes(preserved)
                children.append(candidate)
            else:children.append(child)
        if images:raise StockArtworkError('Artwork image format inventory changed')
        header=bytearray(old[:old_header]);body=b''.join(children)
        if changed:header[48:52]=new[48:52]
        struct.pack_into('<II',header,8,old_header+len(body),len(children))
        records.append(bytes(header)+body)
    image_header=bytearray(list_header);struct.pack_into('<I',image_header,8,len(records))
    chunks=[]
    for kind,header,body in datasets:
        if kind == 1:body=bytes(image_header)+b''.join(records)
        selected=bytearray(header);struct.pack_into('<I',selected,8,len(header)+len(body))
        chunks.append(bytes(selected)+body)
    output=bytearray(root);body=b''.join(chunks)
    struct.pack_into('<I',output,8,len(output)+len(body))
    struct.pack_into('<I',output,28,max(_u32(reference,28),_u32(rendered,28)))
    return bytes(output)+body


def _artwork_links(tracks, existing):
    """Capture explicit native MHIT links, independently of MHII's first owner.

    Music may point several tracks at one image. Its stored song ID remains
    unchanged even when the original owner no longer uses that image.
    """
    references: dict[int, set[int]] = {}
    native: dict[int, tuple[int, int, int]] = {}
    seen = set()
    for track in tracks:
        dbid = int(getattr(track, 'dbid', 0) or 0)
        if dbid in seen:
            raise StockArtworkError('Artwork tracks have duplicate database identities')
        seen.add(dbid)
        image_id = int(getattr(track, 'mhii_link', 0) or 0)
        if not image_id:
            continue
        slot = existing.get(image_id)
        if not dbid or slot is None:
            raise StockArtworkError(
                f'Existing artwork link {image_id} for track {dbid} is invalid')
        references.setdefault(image_id, set()).add(dbid)
        native[dbid] = (dbid, image_id, slot.song_id)
    return references, native


def _retained_links(tracks, native):
    return tuple(sorted(native[dbid] for track in tracks
        if (dbid := int(getattr(track, 'dbid', 0) or 0)) in native
        and int(getattr(track, 'mhii_link', 0) or 0) == native[dbid][1]))


def read_stock_artwork_slots(
    existing_artwork_dir: str | Path,
) -> tuple[bytes, dict[int, StockArtworkSlot], dict[int, tuple[int, int]]]:
    """Read ArtworkDB linkage/offsets without reading any ithmb pixels."""
    artwork_dir = Path(existing_artwork_dir)
    database = artwork_dir / "ArtworkDB"
    if not database.is_file():
        return b"", {}, {}
    data = database.read_bytes()
    if len(data) < 132 or data[:4] != b"mhfd":
        raise StockArtworkError("Existing ArtworkDB has an invalid MHFD header")

    profile = _database_profile(data)
    formats = _PROFILES[profile]

    slots: dict[int, StockArtworkSlot] = {}
    _list,records = _records(data)
    for image_id,raw in records.items():
        images = _images(raw,formats)
        slots[image_id] = StockArtworkSlot(image_id,_u64(raw,20),_u32(raw,48),
            tuple(sorted((fid,row[0]) for fid,row in images.items())),raw=raw)

    files: dict[int, tuple[int, int]] = {}
    for format_id, (width, height) in formats.items():
        path = artwork_dir / f"F{format_id}_1.ithmb"
        if not path.is_file():
            if slots:
                raise StockArtworkError(
                    f"Existing artwork format {format_id} is incomplete")
            continue
        stat = path.stat()
        slot_size = _pixel_size(formats, format_id)
        if stat.st_size % slot_size:
            raise StockArtworkError(
                f"Existing format {format_id} is not fixed-slot aligned")
        files[format_id] = (stat.st_size, stat.st_mtime_ns)
    for slot in slots.values():
        for format_id, offset in slot.offsets:
            size = _pixel_size(formats, format_id)
            if format_id not in files or offset % size or offset + size > files[format_id][0]:
                raise StockArtworkError(
                    f"Existing artwork image {slot.image_id} points outside format {format_id}")
    return data, slots, files


def validate_stock_artwork(
    rendered: RenderedStockArtwork,
    tracks: list[Any],
) -> None:
    """Validate every MHIT→MHII→MHNI→ithmb link without touching a device."""
    formats = _PROFILES[_profile(rendered.profile)]
    plans = {
        int(item.relative_path.name.removeprefix("F").split("_", 1)[0]): item
        for item in rendered.ithmb_files
    }
    if set(plans) != set(formats):
        raise StockArtworkError(
            f"Rendered artwork formats are incomplete: {sorted(plans)}")
    for format_id, (width, height) in formats.items():
        plan = plans[format_id]
        expected_name = f"F{format_id}_1.ithmb"
        if plan.relative_path.name != expected_name:
            raise StockArtworkError(f"Unexpected ithmb filename: {plan.relative_path}")
        expected_segment = _pixel_size(formats, format_id)
        if any(segment.size != expected_segment for segment in plan.segments):
            raise StockArtworkError(f"Format {format_id} has an invalid segment size")
        if not rendered.incremental and plan.size != sum(
                segment.size for segment in plan.segments):
            raise StockArtworkError(f"Format {format_id} has an invalid total size")
    if rendered.incremental:
        ranges: dict[int, list[tuple[int, int]]] = {
            format_id: [] for format_id in formats}
        for write in rendered.range_writes:
            write_plan = plans.get(write.format_id)
            if write_plan is None or write.relative_path != write_plan.relative_path:
                raise StockArtworkError("Incremental artwork targets an unknown ithmb")
            expected = _pixel_size(formats, write.format_id)
            if (
                write.segment.size != expected
                or write.offset % expected
                or write.offset < 0
                or write.offset + expected > write_plan.size
            ):
                raise StockArtworkError("Incremental artwork range is invalid")
            ranges[write.format_id].append((write.offset, write.offset + expected))
        for current in ranges.values():
            ordered = sorted(current)
            if any(after[0] < before[1] for before, after in zip(ordered, ordered[1:])):
                raise StockArtworkError("Incremental artwork ranges overlap")
    else:
        segment_counts = {len(plan.segments) for plan in plans.values()}
        if len(segment_counts) != 1:
            raise StockArtworkError("Rendered ithmb files have mismatched image counts")

    data = rendered.artwork_db
    if len(data) < 132 or data[:4] != b"mhfd":
        raise StockArtworkError("Rendered ArtworkDB has an invalid MHFD header")
    if _u32(data, 8) != len(data) or _u32(data, 20) != 3:
        raise StockArtworkError("Rendered ArtworkDB has an invalid root length/count")

    _list,records = _records(data)
    image_entries: dict[int,int] = {}
    image_offsets: dict[int, tuple[tuple[int, int], ...]] = {}
    for image_id,raw in records.items():
        image_entries[image_id] = _u64(raw,20)
        images = _images(raw,formats)
        image_offsets[image_id] = tuple(sorted((fid, row[0]) for fid, row in images.items()))
        for fid,(offset,size) in images.items():
            if offset % size or offset+size > plans[fid].size:
                raise StockArtworkError('Artwork image points outside its ithmb file')

    native_links = {(dbid, image_id): song_id
                    for dbid, image_id, song_id in rendered.preserved_links}
    if len(native_links) != len(rendered.preserved_links):
        raise StockArtworkError('Preserved artwork link witnesses are ambiguous')
    linked: dict[int, set[int]] = {}
    for track in tracks:
        image_id = int(getattr(track, "mhii_link", 0) or 0)
        if not image_id:
            continue
        dbid = int(getattr(track, "dbid", 0) or 0)
        if (
            (image_id not in image_entries
             or image_entries[image_id] != dbid
             and native_links.get((dbid, image_id)) != image_entries[image_id])
            or int(getattr(track, "artwork_count", 0) or 0) != 1
        ):
            raise StockArtworkError("MHIT artwork link does not match its MHII song ID")
        linked.setdefault(image_id, set()).add(dbid)
    if any(dbid not in linked.get(image_id, ()) or image_entries.get(image_id) != song_id
           for (dbid, image_id), song_id in native_links.items()):
        raise StockArtworkError('Preserved artwork link changed its native identity')
    if rendered.slots:
        slot_ids = {slot.image_id for slot in rendered.slots}
        if len(slot_ids) != len(rendered.slots) or slot_ids != set(image_entries):
            raise StockArtworkError('Artwork slot inventory does not match its records')
        for slot in rendered.slots:
            if (slot.song_id != image_entries[slot.image_id]
                    or slot.source_size != _u32(records[slot.image_id], 48)
                    or slot.offsets != image_offsets[slot.image_id]
                    or slot.track_dbids and (len(set(slot.track_dbids)) != len(slot.track_dbids)
                        or set(slot.track_dbids) != linked[slot.image_id])):
                raise StockArtworkError('Artwork slot references do not match their native links')
    unique_offsets = {
        tuple(slot.offset(format_id) for format_id in sorted(formats))
        for slot in rendered.slots
    } if rendered.slots else set()
    if (
        set(linked) != set(image_entries)
        or sum(map(len, linked.values())) != rendered.linked_tracks
        or (
            rendered.slots
            and len(unique_offsets) != rendered.unique_images
        )
        or (
            not rendered.slots
            and len(plans[min(formats)].segments) != rendered.unique_images
        )
    ):
        raise StockArtworkError("Rendered artwork linkage/counts failed validation")


def render_stock_artwork(
    tracks: list[Any],
    prepared_by_dbid: dict[int, PreparedStockArtwork],
    *,
    cache: StockArtworkCache,
    existing_artwork_dir: str | Path,
) -> RenderedStockArtwork | None:
    """Plan the selected pixel profile and update staged TrackInfo links."""
    profile = cache.profile
    formats = _PROFILES[profile]
    if any(_profile(item.profile) != profile for item in prepared_by_dbid.values()):
        raise StockArtworkError('Prepared artwork belongs to a different pixel profile')
    from clickwheel.ipod.artworkdb_writer.artwork_writer import (  # type: ignore[import-untyped]
        ArtworkEntry, _read_existing_artwork, _write_mhfd, _write_mhla,
        _write_mhlf, _write_mhli, _write_mhsd,
    )

    artwork_dir = Path(existing_artwork_dir)
    artwork_db_path = artwork_dir / "ArtworkDB"
    reference, existing_slots, _files = read_stock_artwork_slots(artwork_dir)
    if reference and _database_profile(reference) != profile:
        raise StockArtworkError('Existing artwork belongs to a different pixel profile')
    references, native_links = _artwork_links(tracks, existing_slots)
    existing = _read_existing_artwork(str(artwork_db_path), str(artwork_dir))
    originals: dict[int, PreparedStockArtwork] = {}

    def preserved(image_id):
        if image_id not in originals:
            raw = existing.get(image_id)
            if not raw:
                raise StockArtworkError(f'Existing artwork link {image_id} cannot be preserved')
            originals[image_id] = cache.preserve_preconverted(
                {int(key): bytes(value) for key, value in raw['formats'].items()},
                source_size=existing_slots[image_id].source_size)
        return originals[image_id]

    artwork_for_dbid: dict[int, PreparedStockArtwork] = dict(prepared_by_dbid)
    for track in tracks:
        dbid = int(getattr(track, "dbid", 0) or 0)
        if not dbid or dbid in artwork_for_dbid:
            continue
        link = int(getattr(track, "mhii_link", 0) or 0)
        if link:
            artwork_for_dbid[dbid] = preserved(link)

    if not artwork_for_dbid:
        return None

    reserved_ids = {
        int(getattr(track, "mhii_link", 0) or 0)
        for track in tracks
        if int(getattr(track, "mhii_link", 0) or 0) > 0
    }
    used_ids: set[int] = set()
    next_id = 100
    unique_payloads: dict[str, PreparedStockArtwork] = {}
    payload_offsets: dict[str, dict[int, int]] = {}
    segments_by_format: dict[int, list[StockArtworkSegment]] = {
        format_id: [] for format_id in formats}
    offsets = {format_id: 0 for format_id in formats}
    entries: list[ArtworkEntry] = []
    entries_by_id: dict[int, ArtworkEntry] = {}
    format_offsets_map: dict[int, dict[int, int]] = {}
    cache_keys: dict[int, str] = {}
    final_references: dict[int, list[int]] = {}
    updated_images: set[int] = set()

    for track in tracks:
        dbid = int(getattr(track, "dbid", 0) or 0)
        prepared = artwork_for_dbid.get(dbid)
        if prepared is None:
            continue
        old_id = int(getattr(track, "mhii_link", 0) or 0)
        old = existing_slots.get(old_id)
        keep_old = old is not None and all(
            prepared.payload(fid).sha256 == preserved(old_id).payload(fid).sha256
            for fid in formats)
        cache_key = prepared.cache_key
        if keep_old:
            assert old is not None
            image_id = old_id
            song_id = old.song_id
            source_size = old.source_size
            prepared = preserved(old_id)
        else:
            if old is not None and old.song_id == dbid and references[old_id] == {dbid}:
                image_id = old_id
            else:
                while next_id in used_ids or next_id in reserved_ids:
                    next_id += 1
                image_id = next_id
                next_id += 1
            song_id = dbid
            source_size = prepared.source_size
            updated_images.add(image_id)
        used_ids.add(image_id)

        if prepared.cache_key not in payload_offsets:
            unique_payloads[prepared.cache_key] = prepared
            art_offsets: dict[int, int] = {}
            for format_id in formats:
                payload = prepared.payload(format_id)
                art_offsets[format_id] = offsets[format_id]
                segments_by_format[format_id].append(StockArtworkSegment(
                    payload.path, payload.size, payload.sha256))
                offsets[format_id] += payload.size
            payload_offsets[prepared.cache_key] = art_offsets
        format_offsets_map[image_id] = dict(payload_offsets[prepared.cache_key])

        if image_id not in entries_by_id:
            entry = ArtworkEntry(
                img_id=image_id,
                song_id=song_id,
                art_hash=prepared.cache_key,
                src_img_size=source_size,
                track_dbids=[],
            )
            entry.formats = {
                format_id: {
                    "width": prepared.payload(format_id).width,
                    "height": prepared.payload(format_id).height,
                    "size": prepared.payload(format_id).size,
                }
                for format_id in formats
            }
            entries.append(entry)
            entries_by_id[image_id] = entry
            cache_keys[image_id] = cache_key
        entries_by_id[image_id].track_dbids.append(dbid)
        final_references.setdefault(image_id, []).append(dbid)
        track.mhii_link = image_id
        if not keep_old:
            track.artwork_count = 1
            track.artwork_size = source_size

    if not entries:
        return None

    ithmb_files: list[StockIthmbPlan] = []
    for format_id in sorted(formats):
        segments = segments_by_format[format_id]
        ithmb_files.append(StockIthmbPlan(
            relative_path=Path("iPod_Control") / "Artwork" / f"F{format_id}_1.ithmb",
            segments=tuple(segments),
            size=sum(item.size for item in segments),
            sha256=_combined_sha256(segments),
        ))

    mhli = _write_mhli(entries, format_offsets_map)
    mhla = _write_mhla()
    image_sizes = {
        format_id: _pixel_size(formats, format_id)
        for format_id, (width, height) in formats.items()
    }
    mhlf = _write_mhlf(sorted(formats), image_sizes)
    artwork_db = _write_mhfd(
        [_write_mhsd(1, mhli), _write_mhsd(2, mhla), _write_mhsd(3, mhlf)],
        max(used_ids) + 1,
        reference,
    )
    artwork_db = _preserve_artwork(reference,artwork_db,updated_images,relocate=True)
    rendered = RenderedStockArtwork(
        artwork_db=artwork_db,
        ithmb_files=tuple(ithmb_files),
        linked_tracks=sum(map(len, final_references.values())),
        unique_images=len(unique_payloads),
        slots=tuple(
            StockArtworkSlot(
                entry.img_id,
                entry.song_id,
                entry.src_img_size,
                tuple(sorted(format_offsets_map[entry.img_id].items())),
                cache_keys[entry.img_id],
                track_dbids=tuple(final_references[entry.img_id]),
            )
            for entry in entries
        ),
        profile=profile,
        updated_dbids=tuple(sorted(prepared_by_dbid)),
        preserved_links=_retained_links(tracks, native_links),
    )
    validate_stock_artwork(rendered, tracks)
    return rendered


def render_incremental_stock_artwork(
    tracks: list[Any],
    prepared_by_dbid: dict[int, PreparedStockArtwork],
    *,
    existing_artwork_dir: str | Path,
    known_artwork: dict[str, dict[str, Any]] | None = None,
    expected_artwork_database: bytes | None = None,
) -> RenderedStockArtwork | None:
    """Render an ArtworkDB while touching only currently unreferenced slots.

    Fresh native slot metadata and optional prior cache identify the baseline.
    Existing image IDs and offsets remain stable. Changed pixels use a free slot or append
    at EOF, so the currently published database remains valid until the new
    ArtworkDB/iTunesDB pair is published.
    """
    from clickwheel.ipod.artworkdb_writer.artwork_writer import (
        ArtworkEntry, _write_mhfd, _write_mhla, _write_mhlf, _write_mhli, _write_mhsd,
    )

    artwork_dir = Path(existing_artwork_dir)
    reference, existing, file_state = read_stock_artwork_slots(artwork_dir)
    if expected_artwork_database is not None and reference != expected_artwork_database:
        raise StockArtworkError("ArtworkDB changed before incremental rendering")
    if not reference:
        raise StockArtworkError(
            "Incremental artwork requires an existing validated ArtworkDB")
    profile = _database_profile(reference)
    formats = _PROFILES[profile]
    if any(_profile(item.profile) != profile for item in prepared_by_dbid.values()):
        raise StockArtworkError('Prepared artwork belongs to a different pixel profile')
    if set(file_state) != set(formats):
        raise StockArtworkError("Incremental artwork files are incomplete")
    references, native_links = _artwork_links(tracks, existing)

    known = known_artwork or {}
    current_ids = set(existing)
    reserved_ids = set(current_ids)
    used_ids: set[int] = set()
    next_id = max(100,_u32(reference,28))

    referenced: dict[int, set[int]] = {format_id: set() for format_id in formats}
    for slot in existing.values():
        for format_id, offset in slot.offsets:
            referenced[format_id].add(offset)
    free: dict[int, _FreeArtworkSlots] = {}
    final_sizes = {format_id: file_state[format_id][0] for format_id in formats}
    for format_id, (width, height) in formats.items():
        slot_size = _pixel_size(formats, format_id)
        free[format_id] = _FreeArtworkSlots([
            offset for offset in range(0, final_sizes[format_id], slot_size)
            if offset not in referenced[format_id]
        ])

    pixel_hashes: dict[tuple[int, int], str] = {}

    def pixel_hash(format_id: int, offset: int, size: int) -> str | None:
        """Hash one existing fixed-size slot at most once during this plan."""
        cache_key = (format_id, offset)
        digest = pixel_hashes.get(cache_key)
        if digest is not None:
            return digest
        path = artwork_dir / f"F{format_id}_1.ithmb"
        try:
            with path.open("rb") as source:
                source.seek(offset)
                pixels = source.read(size)
        except OSError:
            return None
        if len(pixels) != size:
            return None
        digest = _sha256_bytes(pixels)
        pixel_hashes[cache_key] = digest
        return digest

    def pixels_match(
        slot: StockArtworkSlot,
        prepared: PreparedStockArtwork,
    ) -> bool:
        """Prove an unindexed local JPEG already owns the live device pixels."""
        offsets = dict(slot.offsets)
        if set(offsets) != set(formats):
            return False
        for format_id, offset in offsets.items():
            payload = prepared.payload(format_id)
            if pixel_hash(format_id, offset, payload.size) != payload.sha256:
                return False
        return True

    def matching_free_offset(
        format_id: int,
        payload: StockArtworkPayload,
    ) -> int | None:
        """Consume a free slot whose existing pixels already match payload."""
        return free[format_id].pop_matching(payload.sha256,
            lambda offset: pixel_hash(format_id, offset, payload.size))

    offsets_by_cache: dict[str, dict[int, int]] = {}
    for dbid_text, row in known.items():
        try:
            image_id = int(row.get("image_id") or 0)
            cache_key = str(row.get("cache_key") or "")
            offsets = {
                int(key): int(value)
                for key, value in dict(row.get("offsets") or {}).items()
            }
            live = existing.get(image_id)
            if (
                cache_key and live is not None
                and int(dbid_text) in references.get(image_id, ())
                and offsets == dict(live.offsets)
            ):
                offsets_by_cache.setdefault(cache_key, offsets)
        except (TypeError, ValueError):
            continue

    entries: list[ArtworkEntry] = []
    format_offsets_map: dict[int, dict[int, int]] = {}
    slots: list[StockArtworkSlot] = []
    writes: list[StockArtworkRangeWrite] = []
    updated_dbids: list[int] = []
    newly_allocated: dict[str, dict[int, int]] = {}
    entries_by_id: dict[int, ArtworkEntry] = {}
    final_references: dict[int, list[int]] = {}
    cache_keys: dict[int, str] = {}
    updated_images: set[int] = set()

    for track in tracks:
        dbid = int(getattr(track, "dbid", 0) or 0)
        old_id = int(getattr(track, "mhii_link", 0) or 0)
        old = existing.get(old_id)
        prepared = prepared_by_dbid.get(dbid)
        known_row = known.get(str(dbid)) or {}
        known_key = str(known_row.get("cache_key") or "")

        if prepared is None and old is None:
            continue

        cache_key = known_key
        keep_old = old is not None and (prepared is None or (
            known_key == prepared.cache_key
            and int(known_row.get('image_id') or 0) == old.image_id
            and dict(old.offsets) == {
                int(key): int(value)
                for key, value in dict(known_row.get('offsets') or {}).items()
            }) or pixels_match(old, prepared))
        if keep_old:
            assert old is not None
            offsets = dict(old.offsets)
            source_size = old.source_size
            image_id = old.image_id
            song_id = old.song_id
            if prepared is not None:
                cache_key = prepared.cache_key
                offsets_by_cache.setdefault(cache_key, offsets)
        else:
            assert prepared is not None
            if old is not None and old.song_id == dbid and references[old_id] == {dbid}:
                image_id = old_id
            else:
                while next_id in used_ids or next_id in reserved_ids:
                    next_id += 1
                image_id = next_id
                next_id += 1
            song_id = dbid
            cache_key = prepared.cache_key
            cached_offsets = (
                offsets_by_cache.get(cache_key) or newly_allocated.get(cache_key))
            if cached_offsets is None:
                offsets = {}
                for format_id in sorted(formats):
                    payload = prepared.payload(format_id)
                    matched_offset = matching_free_offset(format_id, payload)
                    if matched_offset is not None:
                        offset = matched_offset
                    else:
                        available = free[format_id].pop_first()
                        if available is not None:
                            offset = available
                        else:
                            offset = final_sizes[format_id]
                            final_sizes[format_id] += payload.size
                    offsets[format_id] = offset
                    if matched_offset is None:
                        writes.append(StockArtworkRangeWrite(
                            format_id=format_id,
                            relative_path=(
                                Path("iPod_Control") / "Artwork"
                                / f"F{format_id}_1.ithmb"
                            ),
                            offset=offset,
                            segment=StockArtworkSegment(
                                payload.path, payload.size, payload.sha256),
                        ))
                newly_allocated[cache_key] = offsets
            else:
                offsets = cached_offsets
            source_size = prepared.source_size

        used_ids.add(image_id)
        format_offsets_map[image_id] = dict(offsets)
        if image_id not in entries_by_id:
            entry = ArtworkEntry(
                img_id=image_id,
                song_id=song_id,
                art_hash=cache_key or f"preserved-{image_id}",
                src_img_size=source_size,
                track_dbids=[],
            )
            entry.formats = {
                format_id: {
                    "width": formats[format_id][0],
                    "height": formats[format_id][1],
                    "size": _pixel_size(formats, format_id),
                }
                for format_id in formats
            }
            entries.append(entry)
            entries_by_id[image_id] = entry
            cache_keys[image_id] = cache_key
        entries_by_id[image_id].track_dbids.append(dbid)
        final_references.setdefault(image_id, []).append(dbid)
        if prepared is not None and (old is None or old.image_id != image_id
                or old.source_size != source_size or old.offsets != tuple(sorted(offsets.items()))):
            updated_dbids.append(dbid)
            updated_images.add(image_id)
        track.mhii_link = image_id
        if not keep_old:
            track.artwork_count = 1
            track.artwork_size = source_size

    if not entries:
        return None
    slots = [StockArtworkSlot(entry.img_id, entry.song_id, entry.src_img_size,
             tuple(sorted(format_offsets_map[entry.img_id].items())), cache_keys[entry.img_id],
             track_dbids=tuple(final_references[entry.img_id])) for entry in entries]

    mhli = _write_mhli(entries, format_offsets_map)
    mhla = _write_mhla()
    image_sizes = {
        format_id: _pixel_size(formats, format_id)
        for format_id, (width, height) in formats.items()
    }
    mhlf = _write_mhlf(sorted(formats), image_sizes)
    artwork_db = _write_mhfd(
        [_write_mhsd(1, mhli), _write_mhsd(2, mhla), _write_mhsd(3, mhlf)],
        max(used_ids) + 1,
        reference,
    )
    plans = tuple(
        StockIthmbPlan(
            relative_path=(
                Path("iPod_Control") / "Artwork" / f"F{format_id}_1.ithmb"),
            segments=(),
            size=final_sizes[format_id],
            sha256="",
            before_size=file_state[format_id][0],
            before_mtime_ns=file_state[format_id][1],
        )
        for format_id in sorted(formats)
    )
    unique_offsets = {
        tuple(slot.offset(format_id) for format_id in sorted(formats))
        for slot in slots
    }
    artwork_db = _preserve_artwork(reference,artwork_db,updated_images)
    rendered = RenderedStockArtwork(
        artwork_db=artwork_db,
        ithmb_files=plans,
        linked_tracks=sum(map(len, final_references.values())),
        unique_images=len(unique_offsets),
        range_writes=tuple(writes),
        slots=tuple(slots),
        incremental=True,
        publish_artwork_db=(artwork_db != reference),
        profile=profile,
        updated_dbids=tuple(updated_dbids),
        preserved_links=_retained_links(tracks, native_links),
    )
    validate_stock_artwork(rendered, tracks)
    return rendered
