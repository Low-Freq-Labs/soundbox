"""Disposable Rockbox-safe media and cover derivatives.

The canonical curated JPEGs and source audio are read-only.  Rockbox prefers
embedded artwork over a neighbouring external cover; very large embedded images
can exhaust a classic iPod's memory even when the audio stream is healthy.
This module therefore removes pictures only from a content-addressed Mac-side
audio copy and gives Rockbox 8-bit paletted BMP covers with exact fast paths
plus a dimensionless fallback for other themes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat as stat_mod
import struct
import tempfile
from dataclasses import asdict, dataclass, replace, field
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, TPE1
from mutagen.mp4 import MP4

from .operation_control import OperationCancelled, checkpoint, phase_progress, record_metric
from . import artencode


MEDIA_PROFILE = "rockbox-no-embedded-art-v1"
# Rockbox must never choose a vendor comment, album, year, or stale secondary
# tag as the visible title.  Every SoundBox-managed payload therefore carries
# one canonical title/artist pair and no other user-facing metadata.  The
# previous receipt-specific profile was narrower; keep its public name as an
# alias so callers/tests can describe the migration without perpetuating two
# policies for the same payload contract.
NORMALIZED_MEDIA_PROFILE = "rockbox-soundbox-title-artist-only-v1"
RECEIPT_SANITIZED_MEDIA_PROFILE = NORMALIZED_MEDIA_PROFILE
_LEGACY_NORMALIZED_MEDIA_PROFILES = frozenset({
    "rockbox-no-embedded-art-normalized-tags-v4",
    "rockbox-no-embedded-art-normalized-tags-no-purchase-receipts-v1",
})
# V6 could hash one source epoch and encode a later replacement. Its payload
# self-hash cannot identify that poisoned association, so do not reuse it.
ART_PROFILE = "rockbox-paletted8-bmp-universal-v7"


from .source_acquisition import SourceContract, acquire_source, SourceAcquisitionError
from .cache_io import atomic_cache_bytes as _atomic_write

class RockboxMediaError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            checkpoint()
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class InterruptedFileVerifier:
    """Fresh exact-byte proof for an interrupted file before adoption.

    FAT32/exFAT can retain every stat field across a same-size content edit.
    Even an edge fingerprint misses middle edits, so historical Mac-side
    receipts cannot authorize adoption. Read only the affected candidate once
    per call; leave old disposable receipts untouched and write no new ones.
    """

    def __init__(self, root: str | Path, baseline_sha256: str):
        baseline = str(baseline_sha256 or "").lower()
        if len(baseline) != 64 or any(
                char not in "0123456789abcdef" for char in baseline):
            raise RockboxMediaError(
                "Interrupted verification requires an exact manifest SHA-256")
        self.baseline_sha256 = baseline

    def verify(
            self,
            live: Path,
            *,
            relative_path: Path,
            size: int,
            sha256: str,
    ) -> bool:
        checkpoint()
        before = live.lstat()
        if not stat_mod.S_ISREG(before.st_mode) or before.st_size != size:
            return False
        digest = hashlib.sha256()
        read = 0
        phase_progress("Verifying interrupted file", 0, size, str(relative_path))
        record_metric("interrupted_verification_reads")
        # Refuse a last-component symlink substituted between lstat and open.
        descriptor = os.open(live, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            while True:
                checkpoint()
                block = handle.read(min(1024 * 1024, size - read + 1))
                if not block:
                    break
                digest.update(block)
                read += len(block)
                record_metric("interrupted_verification_bytes_read", len(block))
                if read > size:
                    break
            closed = os.fstat(handle.fileno())
        after = live.lstat()

        def identity(observation: os.stat_result) -> tuple[int, ...]:
            return (observation.st_dev, observation.st_ino, observation.st_size,
                    observation.st_mtime_ns, observation.st_ctime_ns)

        phase_progress("Verifying interrupted file", read, size, str(relative_path))
        return (read == size and digest.hexdigest() == sha256
                and identity(before) == identity(opened)
                == identity(closed) == identity(after)
                and stat_mod.S_ISREG(after.st_mode))


def _embedded_picture_count(path: Path) -> int:
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        raise RockboxMediaError(
            f"Could not inspect embedded artwork in {path}: {exc}") from exc
    if audio is None:
        raise RockboxMediaError(f"Could not identify audio container: {path}")
    count = len(getattr(audio, "pictures", ()) or ())
    tags = getattr(audio, "tags", None)
    if isinstance(tags, ID3):
        count += len(tags.getall("APIC"))
    elif tags is not None:
        try:
            count += len(tags.get("covr", ()) or ())
        except (AttributeError, TypeError):
            pass
    return count


def _technical_signature(path: Path) -> tuple[Any, ...]:
    try:
        audio = mutagen.File(path)
        info = getattr(audio, "info", None)
        if audio is None or info is None:
            raise ValueError("no audio information")
        # Mutagen estimates the duration of some CBR MP3s from the complete
        # file size.  Replacing a large ID3 block with the canonical
        # title/artist pair can therefore move that estimate by a few
        # milliseconds even though every MPEG audio byte is identical.  The
        # exact MP3 essence hash below is the stronger duration/content proof;
        # keep the stable decoder properties here and do not make ID3 padding
        # part of the audio contract.
        length = (
            None
            if path.suffix.lower() == ".mp3"
            else round(float(getattr(info, "length", 0.0) or 0.0), 4)
        )
        return (
            type(audio).__name__,
            length,
            int(getattr(info, "sample_rate", 0) or 0),
            int(getattr(info, "channels", 0) or 0),
            int(getattr(info, "bits_per_sample", 0) or 0),
        )
    except Exception as exc:
        raise RockboxMediaError(
            f"Could not verify audio properties in {path}: {exc}") from exc


def _hash_ranges(path: Path, ranges: list[tuple[int, int]]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for offset, size in ranges:
            checkpoint()
            digest.update(struct.pack(">Q", size))
            handle.seek(offset)
            remaining = size
            while remaining:
                checkpoint()
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RockboxMediaError(
                        f"Audio payload ended unexpectedly in {path}")
                digest.update(chunk)
                remaining -= len(chunk)
    return digest.hexdigest()


def _mp3_essence(path: Path) -> str:
    size = path.stat().st_size
    start = 0
    with path.open("rb") as handle:
        header = handle.read(10)
        if header[:3] == b"ID3" and len(header) == 10:
            raw = header[6:10]
            if any(byte & 0x80 for byte in raw):
                raise RockboxMediaError(f"Invalid ID3 size in {path}")
            start = 10 + sum(byte << shift for byte, shift in zip(
                raw, (21, 14, 7, 0), strict=True))
            if header[5] & 0x10:  # ID3v2.4 footer
                start += 10
        end = size
        if size >= 128:
            handle.seek(-128, os.SEEK_END)
            if handle.read(3) == b"TAG":
                end -= 128
    if start >= end:
        raise RockboxMediaError(f"MP3 has no audio payload: {path}")
    return _hash_ranges(path, [(start, end - start)])


def _riff_essence(path: Path) -> str:
    ranges: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        header = handle.read(12)
        if header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
            raise RockboxMediaError(f"Unsupported WAV layout: {path}")
        while True:
            chunk = handle.read(8)
            if not chunk:
                break
            if len(chunk) != 8:
                raise RockboxMediaError(f"Truncated WAV chunk header: {path}")
            name, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            offset = handle.tell()
            if name == b"data":
                ranges.append((offset, size))
            handle.seek(size + (size & 1), os.SEEK_CUR)
    if not ranges:
        raise RockboxMediaError(f"WAV has no data chunk: {path}")
    return _hash_ranges(path, ranges)


def _aiff_essence(path: Path) -> str:
    ranges: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        header = handle.read(12)
        if header[:4] != b"FORM" or header[8:12] not in {b"AIFF", b"AIFC"}:
            raise RockboxMediaError(f"Unsupported AIFF layout: {path}")
        while True:
            chunk = handle.read(8)
            if not chunk:
                break
            if len(chunk) != 8:
                raise RockboxMediaError(f"Truncated AIFF chunk header: {path}")
            name, size = chunk[:4], struct.unpack(">I", chunk[4:])[0]
            offset = handle.tell()
            if name == b"SSND":
                ranges.append((offset, size))
            handle.seek(size + (size & 1), os.SEEK_CUR)
    if not ranges:
        raise RockboxMediaError(f"AIFF has no sound-data chunk: {path}")
    return _hash_ranges(path, ranges)


def _mp4_essence(path: Path) -> str:
    ranges: list[tuple[int, int]] = []
    total = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < total:
            start = handle.tell()
            header = handle.read(8)
            if len(header) != 8:
                raise RockboxMediaError(f"Truncated MP4 atom header: {path}")
            size, name = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                large = handle.read(8)
                if len(large) != 8:
                    raise RockboxMediaError(f"Truncated MP4 large atom: {path}")
                size = struct.unpack(">Q", large)[0]
                header_size = 16
            elif size == 0:
                size = total - start
            if size < header_size or start + size > total:
                raise RockboxMediaError(f"Invalid MP4 atom size in {path}")
            if name == b"mdat":
                ranges.append((start + header_size, size - header_size))
            handle.seek(start + size)
    if not ranges:
        raise RockboxMediaError(f"MP4/M4A has no media-data atom: {path}")
    return _hash_ranges(path, ranges)


def _flac_essence(path: Path) -> str:
    with path.open("rb") as handle:
        if handle.read(4) != b"fLaC":
            raise RockboxMediaError(f"Invalid FLAC header: {path}")
        while True:
            header = handle.read(4)
            if len(header) != 4:
                raise RockboxMediaError(f"Truncated FLAC metadata: {path}")
            last = bool(header[0] & 0x80)
            size = int.from_bytes(header[1:4], "big")
            handle.seek(size, os.SEEK_CUR)
            if last:
                break
        offset = handle.tell()
    total = path.stat().st_size
    return _hash_ranges(path, [(offset, total - offset)])


def _audio_essence(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return _mp3_essence(path)
    if suffix == ".wav":
        return _riff_essence(path)
    if suffix in {".aif", ".aiff"}:
        return _aiff_essence(path)
    if suffix in {".m4a", ".mp4"}:
        return _mp4_essence(path)
    if suffix == ".flac":
        return _flac_essence(path)
    raise RockboxMediaError(
        f"Embedded-art removal is not verified for {suffix or 'this format'}: {path}")


def _strip_embedded_pictures(path: Path) -> int:
    audio = mutagen.File(path)
    if audio is None:
        raise RockboxMediaError(f"Could not identify audio container: {path}")
    count = _embedded_picture_count(path)
    if not count:
        return 0
    tags = getattr(audio, "tags", None)
    if isinstance(audio, FLAC):
        audio.clear_pictures()
        audio.save()
    elif isinstance(tags, ID3):
        tags.delall("APIC")
        audio.save()
    elif isinstance(audio, MP4) and tags is not None:
        if "covr" in tags:
            del tags["covr"]
        audio.save()
    else:
        raise RockboxMediaError(
            f"Embedded-art removal is not implemented for {type(audio).__name__}: {path}")
    return count


def _riff_info_values(path: Path) -> tuple[str, str]:
    """Read the RIFF INFO fields Rockbox actually uses for WAV metadata."""
    found = {b"INAM": "", b"IART": ""}
    with path.open("rb") as handle:
        header = handle.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RockboxMediaError(f"Unsupported WAV metadata layout: {path}")
        total = path.stat().st_size
        while handle.tell() < total:
            chunk_header = handle.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise RockboxMediaError(f"Truncated WAV chunk header: {path}")
            kind, size = chunk_header[:4], struct.unpack("<I", chunk_header[4:])[0]
            start = handle.tell()
            end = start + size
            if end > total:
                raise RockboxMediaError(f"Invalid WAV chunk size: {path}")
            if kind == b"LIST" and size >= 4 and handle.read(4) == b"INFO":
                cursor = start + 4
                while cursor + 8 <= end:
                    handle.seek(cursor)
                    subheader = handle.read(8)
                    tag, subsize = subheader[:4], struct.unpack("<I", subheader[4:])[0]
                    data_start = cursor + 8
                    data_end = data_start + subsize
                    if data_end > end:
                        raise RockboxMediaError(f"Invalid WAV INFO chunk: {path}")
                    if tag in found:
                        raw = handle.read(subsize).rstrip(b"\0")
                        found[tag] = raw.decode("utf-8", errors="replace")
                    cursor = data_end + (subsize & 1)
            handle.seek(end + (size & 1))
    return found[b"INAM"], found[b"IART"]


def _riff_list_chunk(list_type: bytes, subchunks: list[bytes]) -> bytes:
    body = list_type + b"".join(subchunks)
    return b"LIST" + struct.pack("<I", len(body)) + body + (
        b"\0" if len(body) & 1 else b"")


def _riff_info_chunk(title: str, artist: str) -> bytes:
    def field(tag: bytes, value: str) -> bytes:
        data = value.encode("utf-8") + b"\0"
        return tag + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")

    return _riff_list_chunk(
        b"INFO", [field(b"INAM", title), field(b"IART", artist)])


def _copy_exact(source, destination, size: int, path: Path) -> None:
    remaining = size
    while remaining:
        checkpoint()
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise RockboxMediaError(f"WAV ended unexpectedly: {path}")
        destination.write(chunk)
        remaining -= len(chunk)


def _write_riff_info(path: Path, title: str, artist: str) -> None:
    """Write one canonical Rockbox-readable WAV title/artist pair.

    RIFF files in the real library can contain several INFO lists plus embedded
    ID3/BWF/XMP metadata.  Rockbox does not consistently choose the first title
    across those containers on classic hardware.  The disposable derivative
    retains audio/technical chunks but drops every known metadata container,
    then inserts one small INFO list directly after ``fmt ``.
    """
    chunks: list[tuple[bytes, int, int]] = []
    metadata_chunks = {
        b"id3 ", b"ID3 ", b"bext", b"iXML", b"axml", b"XMP ", b"cart",
        b"DISP",
    }
    total = path.stat().st_size
    with path.open("rb") as source:
        header = source.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RockboxMediaError(f"Unsupported WAV metadata layout: {path}")
        while source.tell() < total:
            checkpoint()
            offset = source.tell()
            chunk_header = source.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise RockboxMediaError(f"Truncated WAV chunk header: {path}")
            kind, size = chunk_header[:4], struct.unpack("<I", chunk_header[4:])[0]
            padded = size + (size & 1)
            if source.tell() + padded > total:
                raise RockboxMediaError(f"Invalid WAV chunk size: {path}")
            if kind == b"LIST":
                body = source.read(size)
                if not body.startswith(b"INFO"):
                    chunks.append((kind, offset, 8 + padded))
            elif kind not in metadata_chunks:
                chunks.append((kind, offset, 8 + padded))
            source.seek(offset + 8 + padded)
    if not any(kind == b"fmt " for kind, _offset, _size in chunks):
        raise RockboxMediaError(f"WAV has no format chunk: {path}")
    if not any(kind == b"data" for kind, _offset, _size in chunks):
        raise RockboxMediaError(f"WAV has no data chunk: {path}")

    # Rockbox's WAV parser has a bounded metadata scan on classic hardware.
    # Keep the only fields Rockbox may display in one small INFO list directly
    # after fmt.  No competing album/year/comment/title container survives.
    info = _riff_info_chunk(title, artist)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".riff", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with path.open("rb") as source, temp.open("wb") as destination:
            destination.write(b"RIFF\0\0\0\0WAVE")
            inserted = False
            for kind, offset, size in chunks:
                checkpoint()
                source.seek(offset)
                _copy_exact(source, destination, size, path)
                if not inserted and kind == b"fmt ":
                    destination.write(info)
                    inserted = True
            if not inserted:
                destination.write(info)
            final_size = destination.tell()
            record_metric("peak_audio_staging_bytes", total + final_size, peak=True)
            if final_size - 8 > 0xFFFFFFFF:
                raise RockboxMediaError(f"WAV is too large for RIFF metadata: {path}")
            destination.seek(4)
            destination.write(struct.pack("<I", final_size - 8))
            destination.flush()
            os.fsync(destination.fileno())
        checkpoint()
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _display_tags(path: Path) -> tuple[str, str]:
    if path.suffix.lower() == ".wav":
        return _riff_info_values(path)
    audio = mutagen.File(path)
    if audio is None:
        raise RockboxMediaError(f"Could not identify audio container: {path}")
    tags = getattr(audio, "tags", None)
    if isinstance(tags, ID3):
        titles = tags.getall("TIT2")
        artists = tags.getall("TPE1")
        return (
            str(titles[0].text[0]) if titles and titles[0].text else "",
            str(artists[0].text[0]) if artists and artists[0].text else "",
        )
    if isinstance(audio, MP4):
        values = tags or {}
        title = values.get("\xa9nam") or []
        artist = values.get("\xa9ART") or []
    else:
        values = tags or {}
        title = values.get("title") or values.get("TITLE") or []
        artist = values.get("artist") or values.get("ARTIST") or []

    def first(value) -> str:
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value or "")

    return first(title), first(artist)


def _normalize_display_tags(path: Path, title: str, artist: str) -> None:
    if path.suffix.lower() == ".wav":
        _write_riff_info(path, title, artist)
        return
    audio = mutagen.File(path)
    if audio is None:
        raise RockboxMediaError(f"Could not identify audio container: {path}")
    tags = getattr(audio, "tags", None)
    if isinstance(tags, ID3) or path.suffix.lower() in {
            ".mp3", ".wav", ".aif", ".aiff"}:
        if tags is None:
            try:
                audio.add_tags()
            except Exception as exc:
                raise RockboxMediaError(
                    f"Could not add Rockbox display tags to {path}: {exc}") from exc
            tags = getattr(audio, "tags", None)
        if not isinstance(tags, ID3):
            raise RockboxMediaError(
                f"Unsupported ID3 tag container for {path}: "
                f"{type(tags).__name__}")
        tags.clear()
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TPE1(encoding=3, text=[artist]))
        if path.suffix.lower() == ".mp3":
            # v1=0 also removes a trailing ID3v1 title that could otherwise win
            # over the canonical ID3v2 pair in Rockbox.
            audio.save(v1=0)
        else:
            audio.save()
    elif isinstance(audio, FLAC):
        audio.clear()
        audio["title"] = [title]
        audio["artist"] = [artist]
        audio.save()
    elif isinstance(audio, MP4):
        if tags is None:
            audio.add_tags()
        else:
            tags.clear()
        audio["\xa9nam"] = [title]
        audio["\xa9ART"] = [artist]
        audio.save()
    else:
        raise RockboxMediaError(
            f"Rockbox display-tag normalization is not implemented for "
            f"{type(audio).__name__}: {path}")


@dataclass(frozen=True)
class PreparedRockboxMedia:
    source: Path
    payload: Path
    transformed: bool
    removed_images: int
    payload_size: int
    payload_sha256: str
    profile: str = MEDIA_PROFILE
    source_sha256: str = ""
    source_size: int = 0
    source_mtime_ns: int = 0
    source_ctime_ns: int = 0
    source_device: int = 0
    source_inode: int = 0


@dataclass(frozen=True)
class PlannedRockboxMedia:
    """Exact reproducible derivative contract without a retained payload.

    Full-library planning must stay no-write with respect to the device without
    retaining one Mac-side copy of every normalized track.  This record keeps
    the canonical source baseline and the fully verified derivative identity;
    commit may then rematerialize one payload at a time and require an exact
    match before it is allowed onto the device.
    """

    source: Path
    source_size: int
    source_mtime_ns: int
    source_ctime_ns: int
    source_sha256: str
    source_sha256_verified: bool
    transformed: bool
    removed_images: int
    payload_size: int
    payload_sha256: str
    title: str | None
    artist: str | None
    profile: str
    stock_omission: str | None = field(default=None, compare=False)
    stock_technical: tuple[int, int, int] | None = field(default=None, compare=False)
    # A committed payload contract, not a new proof of current source bytes.
    # Ordinary composite sync can retain it across ctime-only metadata churn;
    # any later materialization still acquires and verifies the exact digest.
    retained_from_manifest: bool = field(default=False, compare=False)


class RockboxMediaCache:
    """Plan-scoped audio staging with embedded pictures removed.

    Entries are content-addressed so a crash or a multi-playlist batch can reuse
    completed work, then ``clear`` discards the large derivative payloads.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def prepare(
            self, source: str | Path, *, title: str | None = None,
            artist: str | None = None, expected_sha256: str | None = None,
            expected_size: int | None = None,
    ) -> PreparedRockboxMedia:
        original = Path(source).expanduser().resolve(strict=True)
        try:
            checkpoint()
            with acquire_source(
                SourceContract(original, expected_size, expected_sha256),
                self.root / "acquisition",
            ) as snapshot:
                prepared = self._prepare_local(
                    snapshot.path, title=title, artist=artist,
                    source_hash=snapshot.sha256)
                return replace(
                    prepared, source=original,
                    source_sha256=snapshot.sha256, source_size=snapshot.size,
                    source_mtime_ns=snapshot.observation.st_mtime_ns,
                    source_ctime_ns=snapshot.observation.st_ctime_ns,
                    source_device=snapshot.observation.st_dev,
                    source_inode=snapshot.observation.st_ino)
        except OperationCancelled:
            raise
        except SourceAcquisitionError as exc:
            raise RockboxMediaError(str(exc)) from exc
        finally:
            try:
                (self.root / "acquisition").rmdir()
                self.root.rmdir()
            except OSError:
                pass

    def _prepare_local(
            self,
            source: str | Path,
            *,
            title: str | None = None,
            artist: str | None = None,
            source_hash: str,
    ) -> PreparedRockboxMedia:
        original = Path(source).expanduser().resolve(strict=True)
        checkpoint()
        phase_progress("Preparing audio", 0, original.stat().st_size, original.name)
        count = _embedded_picture_count(original)
        checkpoint()
        normalize_tags = title is not None or artist is not None
        if normalize_tags and (title is None or artist is None):
            raise RockboxMediaError(
                "Rockbox display metadata requires both title and artist")
        profile = NORMALIZED_MEDIA_PROFILE if normalize_tags else MEDIA_PROFILE
        if not count and not normalize_tags:
            entry = self.root / profile / source_hash
            entry.mkdir(parents=True, exist_ok=True)
            payload = entry / original.name
            size = original.stat().st_size
            checkpoint()
            os.replace(original, payload)
            return PreparedRockboxMedia(
                original, payload, False, 0, size, source_hash, profile)
        key = hashlib.sha256(json.dumps(
            [profile, source_hash, title or "", artist or ""],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        entry = self.root / profile / key
        payload = entry / original.name
        manifest_path = entry / "manifest.json"
        try:
            row = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                isinstance(row, dict)
                and row.get("profile") == profile
                and row.get("source_sha256") == source_hash
                and int(row.get("removed_images") or 0) == count
                and row.get("title") == (title or "")
                and row.get("artist") == (artist or "")
                and payload.is_file()
                and payload.stat().st_size == int(row.get("payload_size") or -1)
                and _sha256(payload) == row.get("payload_sha256")
                and _embedded_picture_count(payload) == 0
                and (
                    not normalize_tags
                    or _display_tags(payload) == (title, artist)
                )
                and _technical_signature(payload) == _technical_signature(original)
                and _audio_essence(payload) == _audio_essence(original)
            ):
                return PreparedRockboxMedia(
                    original, payload, True, count, payload.stat().st_size,
                    str(row["payload_sha256"]), profile)
        except (OSError, ValueError, TypeError, OverflowError, RockboxMediaError):
            pass

        record_metric("transformations")
        entry.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{original.stem}.", suffix=original.suffix, dir=entry)
        os.close(fd)
        temp = Path(temp_name)
        try:
            checkpoint()
            before_technical = _technical_signature(original)
            checkpoint()
            before_essence = _audio_essence(original)
            checkpoint()
            original_size = original.stat().st_size
            # The snapshot is disposable. Move it into the working derivative
            # once its verification facts are captured, so RIFF rewriting never
            # retains a third whole-track copy.
            os.replace(original, temp)
            removed = _strip_embedded_pictures(temp)
            checkpoint()
            if normalize_tags:
                assert title is not None and artist is not None
                _normalize_display_tags(temp, title, artist)
                checkpoint()
            if removed != count or _embedded_picture_count(temp):
                raise RockboxMediaError(
                    f"Embedded artwork remained after preparing {original}")
            if _technical_signature(temp) != before_technical:
                raise RockboxMediaError(
                    f"Audio properties changed while preparing {original}")
            if _audio_essence(temp) != before_essence:
                raise RockboxMediaError(
                    f"Audio payload changed while preparing {original}")
            if normalize_tags and _display_tags(temp) != (title, artist):
                raise RockboxMediaError(
                    f"Rockbox display tags failed verification for {original}")
            payload_hash = _sha256(temp)
            checkpoint()
            os.replace(temp, payload)
            row = {
                "profile": profile,
                "source_sha256": source_hash,
                "source_size": original_size,
                "payload_sha256": payload_hash,
                "payload_size": payload.stat().st_size,
                "removed_images": removed,
                "title": title or "",
                "artist": artist or "",
            }
            _atomic_write(
                manifest_path,
                json.dumps(row, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            )
            return PreparedRockboxMedia(
                original, payload, True, removed, payload.stat().st_size,
                payload_hash, profile)
        finally:
            temp.unlink(missing_ok=True)

    def _receipt_path(self, source: Path, title: str | None, artist: str | None) -> Path:
        key = hashlib.sha256(json.dumps(
            [str(source), NORMALIZED_MEDIA_PROFILE if title is not None else MEDIA_PROFILE,
             title, artist], ensure_ascii=False).encode()).hexdigest()
        return self.root / "payload-receipts-v1" / f"{key}.json"

    def _read_receipt(self, source: Path, title: str | None,
                      artist: str | None) -> PlannedRockboxMedia | None:
        try:
            row = json.loads(self._receipt_path(source, title, artist).read_text())
            encoded = json.dumps(row["contract"], sort_keys=True).encode()
            stat = source.stat()
            if (row["checksum"] != hashlib.sha256(encoded).hexdigest()
                    or row["identity"] != [stat.st_dev, stat.st_ino, stat.st_size,
                                            stat.st_mtime_ns, stat.st_ctime_ns]):
                return None
            raw = row["contract"]
            if (raw["source"] != str(source) or raw["title"] != title
                    or raw["artist"] != artist):
                return None
            raw["source"] = source
            if raw.get("stock_technical") is not None:
                raw["stock_technical"] = tuple(raw["stock_technical"])
            result = PlannedRockboxMedia(**raw)
            if result.profile != (NORMALIZED_MEDIA_PROFILE if title is not None else MEDIA_PROFILE):
                return None
            record_metric("payload_receipt_hits")
            logging.getLogger("soundbox").info("Payload receipt hit: %s", source.name)
            return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _save_receipt(self, planned: PlannedRockboxMedia, prepared: PreparedRockboxMedia) -> None:
        contract = asdict(planned)
        contract["source"] = str(planned.source)
        row = {
            "contract": contract,
            "checksum": hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
            "identity": [prepared.source_device, prepared.source_inode,
                         prepared.source_size, prepared.source_mtime_ns, prepared.source_ctime_ns],
        }
        try:
            _atomic_write(self._receipt_path(planned.source, planned.title, planned.artist),
                          json.dumps(row, sort_keys=True).encode())
        except OSError:
            logging.getLogger("soundbox").warning("Could not persist payload receipt: %s", planned.source)

    def plan(
            self,
            source: str | Path,
            *,
            title: str | None = None,
            artist: str | None = None,
    ) -> PlannedRockboxMedia:
        """Verify one exact derivative and discard its staged bytes.

        The returned contract is sufficient to reproduce and verify the same
        payload during commit while bounding Mac-side staging to one track.
        """
        original = Path(source).expanduser().resolve(strict=True)
        receipt = self._read_receipt(original, title, artist)
        if receipt is not None:
            return receipt
        prepared = self.prepare(original, title=title, artist=artist)
        try:
            source_hash = prepared.source_sha256
            payload_hash = prepared.payload_sha256 or source_hash
            # Validate stock policy against the same captured local bytes.
            # The stock adapter owns its allowlist; no cloud re-open is needed.
            from .stock_ipod import _stock_media_omission, _technical_audio
            omission = _stock_media_omission(prepared.payload)
            technical = None if omission else _technical_audio(prepared.payload)
            planned = PlannedRockboxMedia(
                source=prepared.source,
                source_size=prepared.source_size,
                source_mtime_ns=prepared.source_mtime_ns,
                source_ctime_ns=prepared.source_ctime_ns,
                source_sha256=source_hash,
                source_sha256_verified=True,
                transformed=prepared.transformed,
                removed_images=prepared.removed_images,
                payload_size=prepared.payload_size,
                payload_sha256=payload_hash,
                title=title,
                artist=artist,
                profile=prepared.profile,
                stock_omission=omission,
                stock_technical=technical,
            )
            self._save_receipt(planned, prepared)
            return planned
        finally:
            self.discard(prepared)

    def plan_from_committed(
            self,
            source: str | Path,
            *,
            title: str,
            artist: str,
            committed: dict[str, Any] | None,
            retain_metadata_only_drift: bool = False,
    ) -> PlannedRockboxMedia:
        """Reuse an exact committed payload identity when its source is stable.

        A legacy manifest is upgraded from its already-verified payload identity
        without regenerating a full disposable audio copy.  Once the manifest
        records an explicit derivative profile, later plans need only stat the
        canonical source.  Reopening every managed audio file merely to
        rediscover the already-committed tag policy made a Collection sync take
        minutes before its confirmation dialog. Normal composite Sync may
        retain the historical contract across ctime-only metadata changes;
        this never refreshes its original proof. Size/mtime, display or policy
        changes still require the complete verified derivative plan. Actual
        materialization always verifies the captured source and output hashes.
        """
        original = Path(source).expanduser().resolve(strict=True)
        if not isinstance(committed, dict):
            return self.plan(original, title=title, artist=artist)
        if (
            str(committed.get("source") or "") != str(original)
            or str(committed.get("title") or "") != title
            or str(committed.get("artist") or "") != artist
        ):
            return self.plan(original, title=title, artist=artist)

        try:
            payload_size = int(committed.get("size") or -1)
            payload_hash = str(committed.get("sha256") or "")
        except (TypeError, ValueError):
            return self.plan(original, title=title, artist=artist)
        if (
            payload_size < 0
            or len(payload_hash) != 64
            or any(char not in "0123456789abcdef" for char in payload_hash.lower())
        ):
            return self.plan(original, title=title, artist=artist)

        stat = original.stat()
        prior_size = committed.get("source_size")
        prior_mtime = committed.get("source_mtime_ns")
        prior_ctime = committed.get("source_ctime_ns")
        prior_hash = str(committed.get("source_sha256") or "")
        retain_baseline = (
            retain_metadata_only_drift
            and all(isinstance(value, int) and value >= 0
                    for value in (prior_size, prior_mtime, prior_ctime))
            and len(prior_hash) == 64
            and all(c in "0123456789abcdef" for c in prior_hash)
            and committed.get("source_sha256_verified") is True
            and "transformed" in committed
        )
        has_baseline = any(
            value is not None
            for value in (prior_size, prior_mtime, prior_ctime)
        )
        if has_baseline and (
            int(prior_size or -1) != stat.st_size
            or int(prior_mtime or -1) != stat.st_mtime_ns
            or (not retain_baseline and int(prior_ctime or -1) != stat.st_ctime_ns)
        ):
            return self.plan(original, title=title, artist=artist)

        committed_profile = str(committed.get("media_profile") or "")
        if committed_profile != NORMALIZED_MEDIA_PROFILE:
            # The old profiles retained album/year/comment fields.  They cannot
            # prove Rockbox will expose the SoundBox title 1:1, so migrate once
            # through the fully verified derivative path.
            return self.plan(original, title=title, artist=artist)
        desired_profile = NORMALIZED_MEDIA_PROFILE
        if len(prior_hash) == 64 and bool(
                committed.get("source_sha256_verified", True)):
            source_hash = prior_hash
            source_hash_verified = True
        else:
            source_hash = payload_hash
            source_hash_verified = False

        if "transformed" in committed:
            transformed = bool(committed.get("transformed"))
            removed_images = int(committed.get("removed_images") or 0)
        else:
            # Legacy rows have no transformation bit.  This inspection is part
            # of their one-time baseline migration, never the steady-state path.
            removed_images = _embedded_picture_count(original)
            exact_tags = _display_tags(original) == (title, artist)
            transformed = bool(
                original.suffix.lower() == ".wav"
                or removed_images > 0
                or not exact_tags
            )
        if not transformed:
            # A pass-through payload is the canonical source itself, so the
            # committed payload digest is also an exact source digest.
            source_hash = payload_hash
            source_hash_verified = True
        return PlannedRockboxMedia(
            source=original,
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
            # Do not manufacture a fresh source proof from a metadata hint.
            source_ctime_ns=int(prior_ctime or 0) if retain_baseline else stat.st_ctime_ns,
            source_sha256=source_hash,
            source_sha256_verified=source_hash_verified,
            transformed=transformed,
            removed_images=removed_images,
            payload_size=payload_size,
            payload_sha256=payload_hash,
            title=title,
            artist=artist,
            profile=desired_profile,
            stock_omission=(
                "Previously verified Rockbox-only payload"
                if committed.get("kind") == "rockbox-media" else None),
            retained_from_manifest=retain_baseline,
        )

    @staticmethod
    def verify_source_stat(planned: PlannedRockboxMedia, *,
                           allow_ctime_drift: bool = False) -> None:
        """Reject a source that changed while its incremental plan was open."""
        try:
            source = planned.source.resolve(strict=True)
            stat = source.stat()
        except OSError as exc:
            raise RockboxMediaError(
                f"Planned Rockbox source is unavailable: {planned.source}") from exc
        if (
            source != planned.source
            or stat.st_size != planned.source_size
            or stat.st_mtime_ns != planned.source_mtime_ns
            or (not allow_ctime_drift and stat.st_ctime_ns != planned.source_ctime_ns)
        ):
            raise RockboxMediaError(
                f"Rockbox source changed after dry-run: {planned.source}")

    def verify_source(self, planned: PlannedRockboxMedia) -> None:
        """Fail if the canonical source changed after the no-write plan."""
        try:
            source = planned.source.resolve(strict=True)
            stat = source.stat()
        except OSError as exc:
            raise RockboxMediaError(
                f"Planned Rockbox source is unavailable: {planned.source}") from exc
        if source != planned.source:
            raise RockboxMediaError(
                f"Planned Rockbox source identity changed: {planned.source}")
        if (
            stat.st_size != planned.source_size
            or stat.st_mtime_ns != planned.source_mtime_ns
            or stat.st_ctime_ns != planned.source_ctime_ns
            or (
                planned.source_sha256_verified
                and _sha256(source) != planned.source_sha256
            )
        ):
            raise RockboxMediaError(
                f"Rockbox source changed after dry-run: {planned.source}")

    def materialize(
            self, planned: PlannedRockboxMedia,
    ) -> PreparedRockboxMedia:
        """Recreate one planned payload and require byte-exact agreement."""
        prepared = self.prepare(
            planned.source, title=planned.title, artist=planned.artist,
            expected_sha256=(planned.source_sha256
                             if planned.source_sha256_verified else None),
            expected_size=planned.source_size)
        payload_hash = prepared.payload_sha256 or planned.payload_sha256
        if (
            prepared.source != planned.source
            or prepared.transformed != planned.transformed
            or prepared.removed_images != planned.removed_images
            or prepared.payload_size != planned.payload_size
            or payload_hash != planned.payload_sha256
            or prepared.profile != planned.profile
        ):
            self.discard(prepared)
            raise RockboxMediaError(
                f"Rockbox derivative changed after dry-run: {planned.source}")
        return prepared

    def discard(self, prepared: PreparedRockboxMedia) -> None:
        """Discard one exact transformed entry without sweeping the cache."""
        if prepared.payload == prepared.source:
            return
        profile_root = self.root / prepared.profile
        entry = prepared.payload.parent
        manifest_path = entry / "manifest.json"
        if (
            prepared.profile not in {
                MEDIA_PROFILE,
                NORMALIZED_MEDIA_PROFILE,
                RECEIPT_SANITIZED_MEDIA_PROFILE,
            }
            or entry.parent != profile_root
            or any(path.is_symlink() for path in (
                self.root, profile_root, entry, prepared.payload, manifest_path))
        ):
            raise RockboxMediaError(
                f"Refusing to discard unexpected Rockbox staging: {entry}")
        prepared.payload.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        try:
            entry.rmdir()
            profile_root.rmdir()
            self.root.rmdir()
        except OSError:
            # Another exact entry may be active in a concurrent small export.
            pass

    def clear(self) -> None:
        """Discard only this known disposable profile; never touch other state."""
        for profile in (
            MEDIA_PROFILE,
            NORMALIZED_MEDIA_PROFILE,
            *_LEGACY_NORMALIZED_MEDIA_PROFILES,
        ):
            profile_root = self.root / profile
            if profile_root.is_symlink():
                raise RockboxMediaError(
                    f"Refusing to clear symlinked Rockbox staging: {profile_root}")
            if profile_root.is_dir():
                shutil.rmtree(profile_root)
        try:
            self.root.rmdir()
        except OSError:
            pass


@dataclass(frozen=True)
class PreparedRockboxArtwork:
    source: Path
    payload: Path
    size: int
    sha256: str
    side: int
    profile: str = ART_PROFILE
    source_sha256: str = ''


class RockboxArtworkCache:
    """Content-addressed 8-bit paletted BMP cover derivatives."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._source_index_path = self.root / ART_PROFILE / "source-index.json"
        try:
            raw = json.loads(self._source_index_path.read_text(encoding="utf-8"))
            self._source_index = {
                key: row for key, row in (raw.items() if isinstance(raw, dict) else ())
                if isinstance(row, dict)
                and all(type(row.get(field)) is int
                        for field in ("dev", "ino", "size", "mtime_ns", "ctime_ns"))
                and isinstance(row.get("source_sha256"), str)
                and len(row["source_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in row["source_sha256"])
            }
        except (OSError, ValueError):
            self._source_index = {}
        self._source_index_dirty = False

    def _remember_source(
            self, source: Path, stat: os.stat_result, sha256: str) -> None:
        row = {
            "dev": stat.st_dev,
            "ino": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "source_sha256": sha256,
        }
        if self._source_index.get(str(source)) != row:
            self._source_index[str(source)] = row
            self._source_index_dirty = True

    def flush_source_index(self) -> None:
        if not self._source_index_dirty:
            return
        _atomic_write(
            self._source_index_path,
            json.dumps(
                self._source_index, indent=2, sort_keys=True,
            ).encode("utf-8") + b"\n",
        )
        self._source_index_dirty = False

    @staticmethod
    def _epoch(stat: os.stat_result) -> tuple[int, ...]:
        return (stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)

    @classmethod
    def _capture_source(cls, source: Path) -> tuple[bytes, os.stat_result]:
        for _attempt in range(2):
            checkpoint()
            if source.resolve(strict=True) != source:
                raise RockboxMediaError(f"Curated artwork identity changed: {source}")
            before = source.stat()
            with source.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                # Bound this read even if another process keeps extending the
                # image. An unstable read retries once, without encoding it.
                data = handle.read(before.st_size + 1)
                closed = os.fstat(handle.fileno())
            checkpoint()
            after = source.stat()
            if source.resolve(strict=True) != source:
                raise RockboxMediaError(f"Curated artwork identity changed: {source}")
            if (cls._epoch(before) == cls._epoch(opened)
                    == cls._epoch(closed) == cls._epoch(after)
                    and len(data) == after.st_size):
                return data, after
        raise RockboxMediaError(f"Curated artwork remained unstable: {source}")

    def _entry(self, source_hash: str, side: int) -> Path:
        key = hashlib.sha256(
            f"{ART_PROFILE}\0{side}\0{source_hash}".encode("ascii")).hexdigest()
        return self.root / ART_PROFILE / key

    def _load(
            self, original: Path, source_hash: str, side: int,
            verify_cache: bool,
    ) -> PreparedRockboxArtwork | None:
        entry = self._entry(source_hash, side)
        payload = entry / f"cover.{side}x{side}.bmp"
        manifest_path = entry / "manifest.json"
        try:
            row = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                return None
            payload_hash = row.get("sha256")
            if not (isinstance(payload_hash, str) and len(payload_hash) == 64
                    and all(c in "0123456789abcdef" for c in payload_hash)):
                return None
            if (
                row.get("profile") == ART_PROFILE
                and int(row.get("side") or 0) == side
                and row.get("source_sha256") == source_hash
                and payload.is_file()
                and payload.stat().st_size == int(row.get("size") or -1)
                and (
                    not verify_cache
                    or _sha256(payload) == payload_hash
                )
            ):
                return PreparedRockboxArtwork(
                    original, payload, payload.stat().st_size,
                    payload_hash, side, source_sha256=source_hash)
        except (OSError, ValueError, TypeError, OverflowError):
            pass
        return None

    def prepare(
            self,
            source: str | Path,
            *,
            side: int = artencode.ROCKBOX_COVER_SIDE,
            verify_cache: bool = True,
    ) -> PreparedRockboxArtwork:
        if not isinstance(side, int) or not 1 <= side <= 240:
            raise RockboxMediaError(f"Unsupported Rockbox cover side: {side}")
        original = Path(source).expanduser().resolve(strict=True)
        stat = original.stat()
        indexed = self._source_index.get(str(original)) or {}
        source_hash = str(indexed.get("source_sha256") or "")
        if (len(source_hash) == 64
                and tuple(indexed.get(field) for field in
                          ("dev", "ino", "size", "mtime_ns", "ctime_ns"))
                == self._epoch(stat)):
            ready = self._load(original, source_hash, side, verify_cache)
            if (ready is not None
                    and original.resolve(strict=True) == original
                    and self._epoch(original.stat()) == self._epoch(stat)):
                return ready

        # A warm source index alone cannot bind pixels on a cache miss. Hash
        # and encode this one stable captured buffer, including new sizes and
        # corrupt/deleted derivative repair.
        data, stat = self._capture_source(original)
        source_hash = hashlib.sha256(data).hexdigest()
        self._remember_source(original, stat, source_hash)
        ready = self._load(original, source_hash, side, verify_cache)
        if ready is not None:
            return ready
        encoded = artencode.to_rockbox_cover(data, side)
        if encoded is None:
            raise RockboxMediaError(f"Could not prepare Rockbox cover: {original}")
        entry = self._entry(source_hash, side)
        payload = entry / f"cover.{side}x{side}.bmp"
        manifest_path = entry / "manifest.json"
        entry.mkdir(parents=True, exist_ok=True)
        _atomic_write(payload, encoded)
        payload_hash = hashlib.sha256(encoded).hexdigest()
        row = {
            "profile": ART_PROFILE,
            "side": side,
            "source_sha256": source_hash,
            "size": len(encoded),
            "sha256": payload_hash,
        }
        _atomic_write(
            manifest_path,
            json.dumps(row, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        return PreparedRockboxArtwork(
            original, payload, len(encoded), payload_hash, side, source_sha256=source_hash)
