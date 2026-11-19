"""In-memory classic HASH58/HASH72 verification and final-buffer signing.

Never consult a global device registry, copy an old checksum, or write HashInfo.
The exact observed FireWire ID must authenticate the original captured catalog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import struct


class StockSigningError(ValueError):
    pass


@dataclass(frozen=True)
class StockSigningContext:
    firewire_id: bytes = field(repr=False)
    hash72_iv: bytes = field(default=b'', repr=False)
    hash72_random: bytes = field(default=b'', repr=False)


def normalize_firewire_guid(value: str | bytes) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        text = str(value or '').strip()
        if text.startswith(('0x', '0X')):
            text = text[2:]
        if len(text) != 16:
            raise StockSigningError('An observed 16-character iPod USB/FireWire ID is required for HASH58')
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise StockSigningError('The observed iPod USB/FireWire ID is not hexadecimal') from exc
    if len(raw) != 8 or raw == bytes(8):
        raise StockSigningError('An observed nonzero 8-byte iPod FireWire ID is required for HASH58')
    return raw.hex().upper()


def verified_stock_signing_context(database: bytes, firewire_guid: str | bytes) -> StockSigningContext:
    from clickwheel.ipod.itunesdb_writer.hash58 import write_hash58  # type: ignore[import-untyped]
    if (len(database) < 0xF4 or database[:4] != b'mhbd'
            or struct.unpack_from('<H', database, 0x30)[0] != 1):
        raise StockSigningError('A complete HASH58 classic iTunesDB is required')
    firewire_id = bytes.fromhex(normalize_firewire_guid(firewire_guid))
    expected = bytearray(database)
    write_hash58(expected, firewire_id)
    if not hmac.compare_digest(expected[0x58:0x6C], database[0x58:0x6C]):
        raise StockSigningError('The captured iTunesDB checksum does not match this iPod USB/FireWire ID')
    if database[0x72:0x74] != b'\x01\x00':
        return StockSigningContext(firewire_id)
    from clickwheel.ipod.itunesdb_writer.hash72 import (  # type: ignore[import-untyped]
        extract_hash_info_to_dict, _compute_itunesdb_sha1, _hash_generate,
    )
    try:
        values = extract_hash_info_to_dict(database)
        if not values:
            raise StockSigningError('The existing HASH72 signature cannot be recovered')
        signature = _hash_generate(_compute_itunesdb_sha1(bytearray(database)),
                                   values['iv'], values['rndpart'])
    except (ImportError, ValueError) as exc:
        raise StockSigningError(f'The existing HASH72 signature cannot be verified: {exc}') from exc
    # Pinned extraction recovers an IV from the first AES block. Full signature
    # equality additionally proves the second block and its SHA1/random tail.
    if not hmac.compare_digest(signature, database[0x72:0xA0]):
        raise StockSigningError('The existing HASH72 signature is invalid')
    return StockSigningContext(firewire_id, values['iv'], values['rndpart'])


def sign_stock_database(database: bytes, context: StockSigningContext) -> bytes:
    """Sign only the final, fully preserved buffer; verify before returning it."""
    from clickwheel.ipod.itunesdb_writer.hash58 import write_hash58
    result = bytearray(database)
    if len(result) < 0xF4 or result[:4] != b'mhbd':
        raise StockSigningError('A complete classic catalog is required for signing')
    struct.pack_into('<H', result, 0x30, 1)
    if context.hash72_iv:
        from clickwheel.ipod.itunesdb_writer.hash72 import (
            _compute_itunesdb_sha1, _hash_generate,
        )
        result[0x72:0xA0] = _hash_generate(_compute_itunesdb_sha1(result),
                                         context.hash72_iv, context.hash72_random)
    write_hash58(result, context.firewire_id)
    signed = bytes(result)
    verified_stock_signing_context(signed, context.firewire_id)
    return signed
