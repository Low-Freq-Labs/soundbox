"""Observed iPod writer formats, independent of a remembered hardware model."""
from __future__ import annotations

from dataclasses import dataclass, replace
import struct
from .stock_ipod_signing import normalize_firewire_guid, verified_stock_signing_context, StockSigningError

DATABASE_PROFILE = "classic-itunesdb-v25-checksum0-v1"
CLASSIC_HASH58_PROFILE = 'classic-itunesdb-v117-hash58-v1'
MODERN_VIDEO_PROFILE = 'classic-itunesdb-v117-checksum0-v1'
ARTWORK_PROFILE = "video-rgb565-1028-1029-v1"
CLASSIC_ARTWORK_PROFILE = "classic-rgb565-1055-1060-1061-v1"
CLASSIC_55_ARTWORK_PROFILE = "classic-rgb565-1055-1060-1061-55-stride56-v1"
_MAX_DATABASE = 256 * 1024 * 1024


@dataclass(frozen=True)
class StockWriteFormat:
    database_profile: str = ""
    artwork_profile: str = ""
    reason: str = ""
    artwork_reason: str = ""
    database_version: int = 0
    checksum_scheme: int = 0
    firewire_guid: str = ""
    signature_verified: bool = False

    @property
    def requires_fwid(self) -> bool:
        return self.checksum_scheme == 1 or self.database_profile == CLASSIC_HASH58_PROFILE

    @property
    def writable(self) -> bool:
        return (self.database_profile in {DATABASE_PROFILE, MODERN_VIDEO_PROFILE, CLASSIC_HASH58_PROFILE}
                and not self.reason and (not self.requires_fwid or bool(self.firewire_guid)))

    @property
    def supports_artwork(self) -> bool:
        return self.writable and self.artwork_profile in {
            ARTWORK_PROFILE,CLASSIC_ARTWORK_PROFILE,CLASSIC_55_ARTWORK_PROFILE}


def _artwork_formats(data: bytes) -> dict[int, int]:
    if len(data) < 132 or len(data) > _MAX_DATABASE or data[:4] != b"mhfd":
        raise ValueError("ArtworkDB has an unsupported header")
    header, total, count = struct.unpack_from('<I', data, 4)[0], struct.unpack_from('<I', data, 8)[0], struct.unpack_from('<I', data, 20)[0]
    if header < 132 or total != len(data) or count > 16:
        raise ValueError("ArtworkDB framing is unsupported")
    offset = header; formats = {}; found = False
    for _ in range(count):
        if offset + 16 > len(data) or data[offset:offset + 4] != b'mhsd':
            raise ValueError("ArtworkDB dataset is invalid")
        ds_header, ds_total = struct.unpack_from('<II', data, offset + 4)
        if ds_header < 16 or ds_total < ds_header or offset + ds_total > len(data):
            raise ValueError("ArtworkDB dataset framing is invalid")
        if struct.unpack_from('<H', data, offset + 12)[0] == 3:
            if found:
                raise ValueError("ArtworkDB has duplicate format descriptors")
            found = True; child = offset + ds_header
            if child + 12 > offset + ds_total or data[child:child + 4] != b'mhlf':
                raise ValueError("ArtworkDB lacks its format table")
            child_header, entries = struct.unpack_from('<II', data, child + 4)
            if child_header < 12 or entries > 256:
                raise ValueError("ArtworkDB format table is invalid")
            item = child + child_header
            for _entry in range(entries):
                if item + 24 > offset + ds_total or data[item:item + 4] != b'mhif':
                    raise ValueError("ArtworkDB format entry is invalid")
                size = struct.unpack_from('<I', data, item + 8)[0]
                if size < 24 or item + size > offset + ds_total:
                    raise ValueError("ArtworkDB format entry is truncated")
                format_id, image_size = struct.unpack_from('<II', data, item + 16)
                if format_id in formats:
                    raise ValueError("ArtworkDB repeats a format descriptor")
                formats[format_id] = image_size; item += size
            if item != offset + ds_total:
                raise ValueError("ArtworkDB format table has trailing data")
        offset += ds_total
    if offset != len(data) or not found:
        raise ValueError("ArtworkDB format inventory is incomplete")
    return formats


def inspect_stock_write_format(database: bytes, artwork_database: bytes | None = None, *,
                               usb_family: str = "", header_only: bool = False,
                               firewire_guid: str = "") -> StockWriteFormat:
    """Bounded header admission; fresh planners additionally check full framing.

    A matching format identifies writer behavior, never a physical MA446. Other
    checksum/database/artwork families retain a specific unsupported reason.
    """
    if len(database) < 0x50 or database[:4] != b'mhbd':
        return StockWriteFormat(reason="An uncompressed classic iTunesDB is required")
    header, total, version = (struct.unpack_from('<I', database, offset)[0] for offset in (4, 8, 0x10))
    if header < 0x50 or header > 0x10000 or total < header or total > _MAX_DATABASE:
        return StockWriteFormat(reason="The iTunesDB header framing is unsupported")
    checksum = struct.unpack_from('<H', database, 0x30)[0]
    profile = ''
    if (version, checksum) == (0x19, 0):
        profile = DATABASE_PROFILE
    elif (version, checksum) == (0x75, 0):
        profile = MODERN_VIDEO_PROFILE
    elif (version, checksum) == (0x75, 1):
        profile = CLASSIC_HASH58_PROFILE
    elif checksum:
        return StockWriteFormat(reason=f"iTunesDB checksum family {checksum} is not supported by this writer")
    else:
        return StockWriteFormat(reason=f"iTunesDB version 0x{version:X} is not supported by this writer")
    if not header_only and (total != len(database) or header > len(database)):
        return StockWriteFormat(reason="The captured iTunesDB is truncated or has unsupported trailing data")
    base = StockWriteFormat(profile, database_version=version, checksum_scheme=checksum)
    if checksum == 1:
        try:
            guid = normalize_firewire_guid(firewire_guid)
            if not header_only:
                verified_stock_signing_context(database, guid)
            base = replace(base, firewire_guid=guid, signature_verified=not header_only)
        except StockSigningError as exc:
            return replace(base, reason=str(exc))
    artwork_profile, artwork_reason = resolve_stock_artwork_profile(artwork_database, usb_family)
    return replace(base, artwork_profile=artwork_profile, artwork_reason=artwork_reason)


def resolve_stock_artwork_profile(artwork_database: bytes | None, usb_family: str = '') -> tuple[str, str]:
    """Resolve observed artwork descriptors independently of database signing."""
    if artwork_database is not None:
        try:
            formats = _artwork_formats(artwork_database)
        except (ValueError, struct.error) as exc:
            return '', str(exc)
        if formats == {1028: 20_000, 1029: 80_000}:
            return ARTWORK_PROFILE, ''
        if formats == {1055:32_768,1060:204_800,1061:6_272}:
            return CLASSIC_ARTWORK_PROFILE, ''
        if formats == {1055:32_768,1060:204_800,1061:6_160}:
            return CLASSIC_55_ARTWORK_PROFILE, ''
        shown = ', '.join(map(str, sorted(formats))) or 'empty'
        return '', f"ArtworkDB formats {shown} are not supported by this artwork writer"
    if usb_family.strip().casefold() in {'ipod video', 'video', 'ipod5,1', 'ipod5,2'}:
        return ARTWORK_PROFILE, ''
    if usb_family.strip().casefold() in {'ipod classic','classic'}:
        return CLASSIC_ARTWORK_PROFILE, ''
    return '', "Artwork output format is unknown: no supported ArtworkDB descriptors or observed family"


def stock_renderer_capabilities(write_format: StockWriteFormat):
    """The accepted catalog format's renderer options, not hardware identity."""
    if not write_format.writable:
        raise ValueError(write_format.reason or "Unsupported stock database format")
    from clickwheel.ipod.ipod_models import capabilities_for_family_gen  # type: ignore[import-untyped]
    return capabilities_for_family_gen('iPod Classic', '1st Gen') if write_format.requires_fwid else capabilities_for_family_gen('iPod Video', '5.5th Gen')
