"""Frozen historical SBA4 reader/conversion oracle for generated TEMP fixtures.

The application has no SBA4 runtime path. These original algorithms retain the
independent migration, corruption and selected-section checks without packaging
obsolete container handling. No discovery or live-state access occurs here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

from soundbox import track_analysis as current
from soundbox.waveform_sections import decode_sections as _decode_sections
from soundbox.track_analysis import (
    _source_identity_matches, COLOR_RATE, WAVEFORM_MODEL,
    DISPLAY_RENDER_MODEL, HEIGHT_ENCODING, RANGE_ENCODING, is_ready, store,
)

LEGACY_SCHEMA = 4
LEGACY_WAVEFORM_MODEL = "soundbox-polarity-range-u16-v4"
_LEGACY_MAGIC = b"SBA4"
_HEADER = struct.Struct("<4sI")


def legacy_cache_path(cache_dir: str | Path, track_id: str) -> Path:
    return current.cache_path(cache_dir, track_id).with_suffix(".sba4")


def _record_candidates(cache_dir, track_id):
    return ((legacy_cache_path(cache_dir, track_id), _LEGACY_MAGIC, LEGACY_SCHEMA),)


def _read_manifest_header(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> tuple[Path, dict, int] | None:
    """Read only the header and JSON manifest for one source-keyed record."""
    source = Path(audio_path)
    stat = source.stat()
    for target, expected_magic, expected_schema in _record_candidates(
            cache_dir, track_id):
        try:
            with target.open("rb") as handle:
                header = handle.read(_HEADER.size)
                if len(header) != _HEADER.size:
                    continue
                magic, manifest_size = _HEADER.unpack(header)
                if magic != expected_magic or manifest_size <= 0:
                    continue
                encoded = handle.read(manifest_size)
                if len(encoded) != manifest_size:
                    continue
            manifest = json.loads(encoded)
            if (manifest.get("schema") != expected_schema
                    or manifest.get("track_id") != str(track_id)
                    or not _source_identity_matches(
                        target, manifest, stat)):
                continue
            return target, manifest, _HEADER.size + manifest_size
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _load_sections(
        cache_dir: str | Path, track_id: str, audio_path: str | Path,
        names: tuple[str, ...]) -> tuple[dict, dict[str, bytes]] | None:
    """Read, decompress, and SHA-256 validate only the named sections."""
    import zstandard as zstd

    record = _read_manifest_header(cache_dir, track_id, audio_path)
    if record is None:
        return None
    target, manifest, payload_offset = record
    rows = manifest.get("sections")
    if not isinstance(rows, dict):
        return None
    decoded: dict[str, bytes] = {}
    decoder = zstd.ZstdDecompressor()
    with target.open("rb") as handle:
        file_size = target.stat().st_size
        for name in names:
            row = rows.get(name)
            if not isinstance(row, dict):
                return None
            try:
                offset = int(row["offset"])
                size = int(row["size"])
                raw_size = int(row["raw_size"])
                expected = str(row["sha256"])
            except (KeyError, TypeError, ValueError):
                return None
            start = payload_offset + offset
            if (offset < 0 or size < 0 or raw_size < 0
                    or start < payload_offset or start + size > file_size):
                return None
            handle.seek(start)
            compressed = handle.read(size)
            if len(compressed) != size:
                return None
            try:
                unpacked = decoder.decompress(
                    compressed, max_output_size=raw_size)
            except zstd.ZstdError:
                return None
            if (len(unpacked) != raw_size
                    or hashlib.sha256(unpacked).hexdigest() != expected):
                return None
            decoded[name] = unpacked
    return manifest, decoded


def load_detail(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path, *, include_spectral: bool = True) -> dict | None:
    """Original SBA4 selected-section reader, retained only as a test oracle."""
    try:
        header = _read_manifest_header(cache_dir, track_id, audio_path)
        if header is None:
            return None
        manifest = header[1]
        loaded = _load_sections(
            cache_dir, track_id, audio_path,
            ("detail",) + (("spectral_24",) if include_spectral else ())
            + ("blue_detail", "rgb_detail", "three_band"))
        if loaded is None:
            return None
        manifest, sections = loaded
        lower = sections["detail"]
        if (manifest.get("waveform_model") == LEGACY_WAVEFORM_MODEL
                and "detail_lower" in (manifest.get("sections") or {})):
            lower_loaded = _load_sections(
                cache_dir, track_id, audio_path, ("detail_lower",))
            if lower_loaded is None:
                return None
            lower = lower_loaded[1]["detail_lower"]
        range_rows = {}
        section_names = manifest.get("sections") or {}
        if (
                manifest.get("waveform_model") == LEGACY_WAVEFORM_MODEL
                and "detail_minimum" in section_names
                and "detail_maximum" in section_names):
            range_loaded = _load_sections(
                cache_dir, track_id, audio_path,
                ("detail_minimum", "detail_maximum"))
            if range_loaded is None:
                return None
            range_rows = {
                "range_min": range_loaded[1]["detail_minimum"],
                "range_max": range_loaded[1]["detail_maximum"],
            }
        return {
            "_audio_detail": {
                "rate": float(manifest.get("detail_rate") or 0),
                "heights": sections["detail"],
                "lower_heights": lower,
                "encoding": str(manifest.get("height_encoding") or ""),
                **range_rows,
            },
            "_spectral_24": sections.get("spectral_24", b""),
            "_blue_detail": sections["blue_detail"],
            "_rgb_detail": sections["rgb_detail"],
            "_detail_bands": sections["three_band"],
        }
    except Exception:  # noqa: BLE001 - one bad section is a clean miss
        return None


def verify_complete(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Audit every section and the complete compressed payload checksum."""
    try:
        record = _read_manifest_header(cache_dir, track_id, audio_path)
        if record is None:
            return False
        target, manifest, payload_offset = record
        names = tuple((manifest.get("sections") or {}).keys())
        if not names or _load_sections(
                cache_dir, track_id, audio_path, names) is None:
            return False
        with target.open("rb") as handle:
            handle.seek(payload_offset)
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == manifest.get("payload_sha256")
    except Exception:  # noqa: BLE001 - audit reports false for every bad record
        return False


def retire_legacy_record(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Remove one V4 only after the corresponding complete V5 is verified."""
    if not is_ready(cache_dir, track_id, audio_path):
        return False
    if not current.verify_complete(cache_dir, track_id, audio_path):
        return False
    legacy = legacy_cache_path(cache_dir, track_id)
    try:
        legacy.unlink(missing_ok=True)
        return not legacy.exists()
    except OSError:
        return False


def migrate_v4(
        cache_dir: str | Path, track_id: str,
        audio_path: str | Path) -> bool:
    """Atomically reduce one valid V4 to byte-equivalent V5 renderer mips."""
    import zstandard as zstd

    if is_ready(cache_dir, track_id, audio_path):
        return retire_legacy_record(cache_dir, track_id, audio_path)
    source = Path(audio_path)
    legacy = legacy_cache_path(cache_dir, track_id)
    try:
        packed = legacy.read_bytes()
        if len(packed) < _HEADER.size:
            return False
        magic, manifest_size = _HEADER.unpack_from(packed)
        if magic != _LEGACY_MAGIC or manifest_size <= 0:
            return False
        start = _HEADER.size
        manifest = json.loads(packed[start:start + manifest_size])
        stat = source.stat()
        if (manifest.get("schema") != LEGACY_SCHEMA
                or manifest.get("track_id") != str(track_id)
                or manifest.get("source", {}).get("identity")
                != f"{stat.st_size}:{stat.st_mtime_ns}"
                or manifest.get("waveform_model") != LEGACY_WAVEFORM_MODEL):
            return False
        payload = packed[start + manifest_size:]
        if hashlib.sha256(payload).hexdigest() != manifest.get(
                "payload_sha256"):
            return False
        sections = _decode_sections(payload, manifest)
        if sections is None:
            return False
        required = {
            "detail_minimum", "detail_maximum",
            "overview_heights", "overview_rgb", "spectral_24",
            "blue_detail", "rgb_detail", "three_band",
        }
        if not required.issubset(sections):
            return False
        prepared = {
            "detail_minimum_values": sections["detail_minimum"],
            "detail_maximum_values": sections["detail_maximum"],
            # ``store`` keeps this solely as a compatibility fallback; V5 does
            # not persist it.
            "detail_heights": sections.get("detail", b""),
            "detail_rate": float(manifest.get("detail_rate") or 0),
            "overview_heights": sections["overview_heights"],
            "overview_rgb": sections["overview_rgb"],
            "spectral_bands": sections["spectral_24"],
            "blue_detail": sections["blue_detail"],
            "rgb_detail": sections["rgb_detail"],
            "three_band": sections["three_band"],
            "sample_rate": float(manifest.get("color_rate") or COLOR_RATE),
            "color_model": str(manifest.get("color_model") or ""),
            "waveform_model": WAVEFORM_MODEL,
            "three_band_model": str(
                manifest.get("three_band_model") or ""),
            "display_waveform_model": str(
                manifest.get("display_waveform_model") or ""),
            "display_render_model": str(
                manifest.get("display_render_model")
                or DISPLAY_RENDER_MODEL),
            "height_encoding": str(
                manifest.get("height_encoding") or HEIGHT_ENCODING),
            "range_encoding": RANGE_ENCODING,
            "beats": list(manifest.get("beats") or []),
            "cues": list(manifest.get("cues") or []),
            "grid_import": dict(manifest.get("grid_import") or {}),
            "cue_import": dict(manifest.get("cue_import") or {}),
            "provenance": dict(manifest.get("provenance") or {}),
            "stream_provenance": dict(
                manifest.get("stream_provenance") or {}),
        }
        if store(cache_dir, track_id, source, prepared) is None:
            return False
        return retire_legacy_record(cache_dir, track_id, source)
    except (
            OSError, ValueError, KeyError, TypeError, zstd.ZstdError):
        return False
