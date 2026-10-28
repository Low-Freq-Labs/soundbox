"""Bounded read-only PCM WAVE endpoint evidence for ambiguous source-grid EOF.

Never reads audio payloads, decodes, changes source files, or grants write authority.
Only exact integral-millisecond endpoints are accepted by this native-tested path.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import struct

from .rekordbox_write import ConflictError, WriteProofError


def _epoch(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def capture_pcm_endpoint(source: str) -> dict:
    path = Path(source)
    if not path.is_absolute() or path != path.resolve():
        raise WriteProofError('Exact grid endpoint requires an unchanged canonical audio path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _epoch(path.stat()) != _epoch(before):
            raise ConflictError('Audio identity changed before endpoint capture')
        digest = hashlib.sha256()
        def read(offset, length):
            raw = os.pread(fd, length, offset)
            if len(raw) != length:
                raise WriteProofError('Truncated PCM endpoint header')
            digest.update(struct.pack('>QQ', offset, length) + raw)
            return raw
        header = read(0, 12)
        if header[:4] != b'RIFF' or header[8:] != b'WAVE':
            raise WriteProofError('Ambiguous grid endpoint requires a verified PCM WAVE header')
        end = struct.unpack_from('<I', header, 4)[0] + 8
        if end != before.st_size:
            raise WriteProofError('PCM WAVE container length does not match its source')
        offset, fmt, payload = 12, None, None
        for _ in range(1024):
            if offset == end:
                break
            if offset + 8 > end:
                raise WriteProofError('Truncated PCM chunk framing')
            chunk = read(offset, 8)
            kind, size = struct.unpack('<4sI', chunk)
            start = offset + 8
            if start + size + (size & 1) > end:
                raise WriteProofError('PCM chunk extends beyond its source')
            if kind == b'fmt ':
                if fmt is not None or size < 16:
                    raise WriteProofError('Missing or ambiguous PCM format')
                fmt = read(start, min(size, 40))
            elif kind == b'data':
                if payload is not None:
                    raise WriteProofError('Multiple PCM audio payloads')
                payload = (start, size)
            offset = start + size + (size & 1)
        else:
            raise WriteProofError('PCM endpoint exceeds the bounded header inventory')
        if fmt is None or payload is None:
            raise WriteProofError('PCM WAVE lacks a format or audio payload')
        encoding, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', fmt)
        if encoding == 0xfffe:
            pcm_guid = bytes.fromhex('0100000000001000800000aa00389b71')
            if (len(fmt) < 40 or struct.unpack_from('<H', fmt, 16)[0] < 22
                    or not 0 < struct.unpack_from('<H', fmt, 18)[0] <= bits
                    or fmt[24:40] != pcm_guid):
                raise WriteProofError('Unverified extensible PCM endpoint format')
        elif encoding != 1:
            raise WriteProofError('Exact grid endpoint supports uncompressed PCM WAVE only')
        if (not 1 <= channels <= 32 or not 1 <= rate <= 768000 or bits not in (8, 16, 24, 32)
                or align != channels * (bits // 8) or byte_rate != rate * align
                or not payload[1] or payload[1] % align):
            raise WriteProofError('Inconsistent PCM endpoint metadata')
        frames = payload[1] // align
        if frames * 1000 % rate:
            raise WriteProofError('Disputed grid endpoint lacks an exact native-tested millisecond boundary')
        after = os.fstat(fd)
        if _epoch(before) != _epoch(after) or _epoch(path.stat()) != _epoch(after):
            raise ConflictError('Audio changed during endpoint capture')
        return {'format': 'pcm-wave-integral-ms-v1', 'path': str(path), 'epoch': _epoch(after),
                'header_sha256': digest.hexdigest(), 'frames': frames, 'sample_rate': rate,
                'end_ms': frames * 1000 // rate}
    finally:
        os.close(fd)
