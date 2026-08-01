"""Prepared artwork and read-only layout planning for stock iPods.

The curated JPEG cache is the source of truth.  This module stores disposable,
content-addressed RGB565 derivatives for the physically validated MA446 and
assembles a future ArtworkDB/ithmb layout without writing the mounted device.

Clickwheel remains the pinned binary-format renderer.  Its public image
converter and pinned ArtworkDB chunk builders are wrapped here so SoundBox can
feed already-prepared pixels instead of modifying audio tags or re-decoding
JPEGs during a device sync.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from clickwheel.ipod.artworkdb_writer.artwork_writer import (  # type: ignore[import-untyped]
    ArtworkEntry,
    _read_existing_artwork,
    _write_mhfd,
    _write_mhla,
    _write_mhlf,
    _write_mhli,
    _write_mhsd,
)
from clickwheel.ipod.artworkdb_writer.rgb565 import (  # type: ignore[import-untyped]
    IPOD_STRIDE_OVERRIDE,
    convert_art_for_ipod,
)


PROFILE = "ma446-video-rgb565-v1"
FORMATS: dict[int, tuple[int, int]] = {
    1028: (100, 100),
    1029: (200, 200),
}


class StockArtworkError(RuntimeError):
    """A prepared-artwork cache entry or staged layout is invalid."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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


@dataclass(frozen=True)
class RenderedStockArtwork:
    artwork_db: bytes
    ithmb_files: tuple[StockIthmbPlan, ...]
    linked_tracks: int
    unique_images: int

    @property
    def bytes_to_copy(self) -> int:
        return len(self.artwork_db) + sum(item.size for item in self.ithmb_files)


class StockArtworkCache:
    """Disposable content-addressed MA446 RGB565 derivatives."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _entry(self, cache_key: str) -> Path:
        return self.root / PROFILE / cache_key[:2] / cache_key

    @staticmethod
    def _cache_key(source_sha256: str) -> str:
        material = f"{PROFILE}:{source_sha256}".encode("ascii")
        return hashlib.sha256(material).hexdigest()

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
        except (OSError, ValueError):
            return None
        if (
            manifest.get("profile") != PROFILE
            or manifest.get("source_sha256") != source_sha256
            or int(manifest.get("source_size", -1)) != source_size
        ):
            return None
        payloads: list[StockArtworkPayload] = []
        rows = manifest.get("formats")
        if not isinstance(rows, list):
            return None
        by_id = {int(row.get("format_id") or 0): row for row in rows if isinstance(row, dict)}
        for format_id, (width, height) in FORMATS.items():
            row = by_id.get(format_id)
            if row is None:
                return None
            path = entry / f"F{format_id}.rgb565"
            size = int(row.get("size") or -1)
            expected_size = width * height * 2
            expected_hash = str(row.get("sha256") or "")
            try:
                if size != expected_size or path.stat().st_size != size:
                    return None
                if verify_hashes and _sha256_file(path) != expected_hash:
                    return None
            except OSError:
                return None
            payloads.append(StockArtworkPayload(
                format_id, path, size, expected_hash, width, height))
        return PreparedStockArtwork(
            cache_key, source_sha256, source_size, tuple(payloads))

    def prepare(
        self,
        image_path: str | Path,
        *,
        verify_cache: bool = True,
    ) -> PreparedStockArtwork:
        """Prepare one JPEG; sync callers verify pixels, warmers may trust metadata."""
        path = Path(image_path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise StockArtworkError(f"Could not read curated artwork {path}: {exc}") from exc
        source_sha256 = _sha256_bytes(data)
        source_size = len(data)
        cache_key = self._cache_key(source_sha256)
        ready = self._load(
            cache_key,
            source_sha256=source_sha256,
            source_size=source_size,
            verify_hashes=verify_cache,
        )
        if ready is not None:
            return ready

        entry = self._entry(cache_key)
        rows: list[dict[str, Any]] = []
        for format_id, (width, height) in FORMATS.items():
            converted = convert_art_for_ipod(data, format_id)
            if converted is None:
                raise StockArtworkError(
                    f"Could not convert curated artwork {path} to format {format_id}")
            payload = bytes(converted["data"])
            expected = width * height * 2
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
            "profile": PROFILE,
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
        return prepared

    def preserve_preconverted(
        self,
        formats: dict[int, bytes],
        *,
        source_size: int,
    ) -> PreparedStockArtwork:
        """Adopt verified device pixels into the disposable local cache."""
        if set(formats) != set(FORMATS):
            raise StockArtworkError(
                f"Existing MA446 artwork formats are incomplete: {sorted(formats)}")
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
        for format_id, (width, height) in FORMATS.items():
            payload = bytes(formats[format_id])
            expected = width * height * 2
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
                "profile": PROFILE,
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
        if _sha256_file(segment.path) != segment.sha256:
            raise StockArtworkError(f"Prepared payload changed content: {segment.path}")
        with segment.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
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


def validate_stock_artwork(
    rendered: RenderedStockArtwork,
    tracks: list[Any],
) -> None:
    """Validate every MHIT→MHII→MHNI→ithmb link without touching a device."""
    plans = {
        int(item.relative_path.name.removeprefix("F").split("_", 1)[0]): item
        for item in rendered.ithmb_files
    }
    if set(plans) != set(FORMATS):
        raise StockArtworkError(
            f"Rendered artwork formats are incomplete: {sorted(plans)}")
    for format_id, (width, height) in FORMATS.items():
        plan = plans[format_id]
        expected_name = f"F{format_id}_1.ithmb"
        if plan.relative_path.name != expected_name:
            raise StockArtworkError(f"Unexpected ithmb filename: {plan.relative_path}")
        expected_segment = width * height * 2
        if any(segment.size != expected_segment for segment in plan.segments):
            raise StockArtworkError(f"Format {format_id} has an invalid segment size")
        if plan.size != sum(segment.size for segment in plan.segments):
            raise StockArtworkError(f"Format {format_id} has an invalid total size")
    segment_counts = {len(plan.segments) for plan in plans.values()}
    if len(segment_counts) != 1:
        raise StockArtworkError("Rendered ithmb files have mismatched image counts")

    data = rendered.artwork_db
    if len(data) < 132 or data[:4] != b"mhfd":
        raise StockArtworkError("Rendered ArtworkDB has an invalid MHFD header")
    if _u32(data, 8) != len(data) or _u32(data, 20) != 3:
        raise StockArtworkError("Rendered ArtworkDB has an invalid root length/count")

    dataset_offset = _u32(data, 4)
    image_entries: dict[int, int] = {}
    dataset_types: list[int] = []
    for _ in range(_u32(data, 20)):
        if data[dataset_offset:dataset_offset + 4] != b"mhsd":
            raise StockArtworkError("Rendered ArtworkDB has an invalid MHSD chunk")
        header_size = _u32(data, dataset_offset + 4)
        total_size = _u32(data, dataset_offset + 8)
        dataset_type = _u16(data, dataset_offset + 12)
        if total_size < header_size or dataset_offset + total_size > len(data):
            raise StockArtworkError("Rendered ArtworkDB has an invalid MHSD size")
        dataset_types.append(dataset_type)
        if dataset_type == 1:
            mhli = dataset_offset + header_size
            if data[mhli:mhli + 4] != b"mhli":
                raise StockArtworkError("Artwork image dataset lacks MHLI")
            entry_offset = mhli + _u32(data, mhli + 4)
            for _entry in range(_u32(data, mhli + 8)):
                if data[entry_offset:entry_offset + 4] != b"mhii":
                    raise StockArtworkError("Artwork image list has an invalid MHII")
                mhii_header = _u32(data, entry_offset + 4)
                mhii_total = _u32(data, entry_offset + 8)
                child_count = _u32(data, entry_offset + 12)
                image_id = _u32(data, entry_offset + 16)
                song_id = _u64(data, entry_offset + 20)
                if (
                    image_id <= 0 or image_id in image_entries
                    or mhii_total < mhii_header
                    or entry_offset + mhii_total > dataset_offset + total_size
                ):
                    raise StockArtworkError("Artwork image entry has invalid identity/size")
                image_entries[image_id] = song_id
                child_offset = entry_offset + mhii_header
                seen_formats: set[int] = set()
                for _child in range(child_count):
                    if data[child_offset:child_offset + 4] != b"mhod":
                        raise StockArtworkError("Artwork image entry has an invalid MHOD")
                    mhod_header = _u32(data, child_offset + 4)
                    mhod_total = _u32(data, child_offset + 8)
                    if _u16(data, child_offset + 12) != 2:
                        raise StockArtworkError("Artwork image entry has a non-image MHOD")
                    mhni = child_offset + mhod_header
                    if data[mhni:mhni + 4] != b"mhni":
                        raise StockArtworkError("Artwork image MHOD lacks MHNI")
                    format_id = _u32(data, mhni + 16)
                    offset = _u32(data, mhni + 20)
                    size = _u32(data, mhni + 24)
                    if format_id not in plans or format_id in seen_formats:
                        raise StockArtworkError("Artwork image has an unknown/duplicate format")
                    if size != FORMATS[format_id][0] * FORMATS[format_id][1] * 2:
                        raise StockArtworkError("Artwork image has an invalid pixel size")
                    if offset % size or offset + size > plans[format_id].size:
                        raise StockArtworkError("Artwork image points outside its ithmb file")
                    seen_formats.add(format_id)
                    if mhod_total < mhod_header or child_offset + mhod_total > entry_offset + mhii_total:
                        raise StockArtworkError("Artwork MHOD has an invalid size")
                    child_offset += mhod_total
                if seen_formats != set(FORMATS) or child_offset != entry_offset + mhii_total:
                    raise StockArtworkError("Artwork image formats/chunk lengths are incomplete")
                entry_offset += mhii_total
            if entry_offset != dataset_offset + total_size:
                raise StockArtworkError("Artwork image dataset has trailing or missing bytes")
        dataset_offset += total_size
    if dataset_types != [1, 2, 3] or dataset_offset != len(data):
        raise StockArtworkError("ArtworkDB dataset order/length failed validation")

    linked: dict[int, int] = {}
    for track in tracks:
        image_id = int(getattr(track, "mhii_link", 0) or 0)
        if not image_id:
            continue
        dbid = int(getattr(track, "dbid", 0) or 0)
        if (
            image_entries.get(image_id) != dbid
            or int(getattr(track, "artwork_count", 0) or 0) != 1
        ):
            raise StockArtworkError("MHIT artwork link does not match its MHII song ID")
        linked[image_id] = dbid
    if (
        linked != image_entries
        or len(linked) != rendered.linked_tracks
        or len(plans[min(FORMATS)].segments) != rendered.unique_images
    ):
        raise StockArtworkError("Rendered artwork linkage/counts failed validation")


def render_stock_artwork(
    tracks: list[Any],
    prepared_by_dbid: dict[int, PreparedStockArtwork],
    *,
    cache: StockArtworkCache,
    existing_artwork_dir: str | Path,
) -> RenderedStockArtwork | None:
    """Plan MA446 ArtworkDB/ithmb bytes and update staged TrackInfo links."""
    artwork_dir = Path(existing_artwork_dir)
    artwork_db_path = artwork_dir / "ArtworkDB"
    existing = _read_existing_artwork(str(artwork_db_path), str(artwork_dir))

    artwork_for_dbid: dict[int, PreparedStockArtwork] = dict(prepared_by_dbid)
    for track in tracks:
        dbid = int(getattr(track, "dbid", 0) or 0)
        if not dbid or dbid in artwork_for_dbid:
            continue
        link = int(getattr(track, "mhii_link", 0) or 0)
        raw = existing.get(link)
        if link:
            if not raw:
                raise StockArtworkError(
                    f"Existing artwork link {link} for track {dbid} cannot be preserved")
            artwork_for_dbid[dbid] = cache.preserve_preconverted(
                {int(key): bytes(value) for key, value in raw["formats"].items()},
                source_size=int(raw.get("src_img_size") or 0),
            )

    if not artwork_for_dbid:
        for track in tracks:
            track.mhii_link = 0
            track.artwork_count = 0
            track.artwork_size = 0
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
        format_id: [] for format_id in FORMATS}
    offsets = {format_id: 0 for format_id in FORMATS}
    entries: list[ArtworkEntry] = []
    format_offsets_map: dict[int, dict[int, int]] = {}

    for track in tracks:
        dbid = int(getattr(track, "dbid", 0) or 0)
        prepared = artwork_for_dbid.get(dbid)
        if prepared is None:
            track.mhii_link = 0
            track.artwork_count = 0
            track.artwork_size = 0
            continue
        old_id = int(getattr(track, "mhii_link", 0) or 0)
        if old_id > 0 and old_id not in used_ids:
            image_id = old_id
        else:
            while next_id in used_ids or next_id in reserved_ids:
                next_id += 1
            image_id = next_id
            next_id += 1
        used_ids.add(image_id)

        if prepared.cache_key not in payload_offsets:
            unique_payloads[prepared.cache_key] = prepared
            art_offsets: dict[int, int] = {}
            for format_id in FORMATS:
                payload = prepared.payload(format_id)
                art_offsets[format_id] = offsets[format_id]
                segments_by_format[format_id].append(StockArtworkSegment(
                    payload.path, payload.size, payload.sha256))
                offsets[format_id] += payload.size
            payload_offsets[prepared.cache_key] = art_offsets
        format_offsets_map[image_id] = dict(payload_offsets[prepared.cache_key])

        entry = ArtworkEntry(
            img_id=image_id,
            song_id=dbid,
            art_hash=prepared.cache_key,
            src_img_size=prepared.source_size,
            track_dbids=[dbid],
        )
        entry.formats = {
            format_id: {
                "width": prepared.payload(format_id).width,
                "height": prepared.payload(format_id).height,
                "size": prepared.payload(format_id).size,
            }
            for format_id in FORMATS
        }
        entries.append(entry)
        track.mhii_link = image_id
        track.artwork_count = 1
        track.artwork_size = prepared.source_size

    if not entries:
        return None

    ithmb_files: list[StockIthmbPlan] = []
    for format_id in sorted(FORMATS):
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
        format_id: IPOD_STRIDE_OVERRIDE.get(format_id, width) * height * 2
        for format_id, (width, height) in FORMATS.items()
    }
    mhlf = _write_mhlf(sorted(FORMATS), image_sizes)
    reference = artwork_db_path.read_bytes() if artwork_db_path.is_file() else None
    artwork_db = _write_mhfd(
        [_write_mhsd(1, mhli), _write_mhsd(2, mhla), _write_mhsd(3, mhlf)],
        max(used_ids) + 1,
        reference,
    )
    rendered = RenderedStockArtwork(
        artwork_db=artwork_db,
        ithmb_files=tuple(ithmb_files),
        linked_tracks=len(entries),
        unique_images=len(unique_payloads),
    )
    validate_stock_artwork(rendered, tracks)
    return rendered
