"""Bounded primitive messages over explicitly owned, nonstandard pipe FDs."""
from __future__ import annotations

import marshal
import os
import select
import struct
import sys
import time
from typing import Any

FRAME_BYTES = 1024 * 1024
_FRAGMENT = 1 << 63
_CHUNK_BYTES = FRAME_BYTES - 128


class ProtocolError(RuntimeError):
    pass


class EncodingError(ProtocolError):
    """No bytes have been written for this rejected message."""


class PipeChannel:
    """No pickle, executable marshal values, standard streams or implicit IO."""
    def __init__(self, read_fd: int, write_fd: int):
        if (type(read_fd) is not int or type(write_fd) is not int
                or min(read_fd, write_fd) < 3 or read_fd == write_fd):
            raise ProtocolError('Worker pipes require distinct inherited descriptors >=3')
        self.read_fd, self.write_fd = read_fd, write_fd
        self._sent = self._received = 0

    def send(self, value: Any) -> int:
        try:
            encoded = marshal.dumps(value, allow_code=False)
        except (ValueError, TypeError) as exc:
            raise EncodingError('Worker message contains unsupported values') from exc
        sequence = self._sent + 1
        if len(encoded) <= FRAME_BYTES:
            self._write(encoded)
        else:
            count = (len(encoded) + _CHUNK_BYTES - 1) // _CHUNK_BYTES
            for index, offset in enumerate(range(0, len(encoded), _CHUNK_BYTES)):
                frame = marshal.dumps((sequence, index, count, len(encoded),
                    encoded[offset:offset + _CHUNK_BYTES]), allow_code=False)
                self._write(frame, fragmented=True)
        self._sent = sequence
        return len(encoded)

    def _write(self, encoded: bytes, *, fragmented: bool = False) -> None:
        if not 0 < len(encoded) <= FRAME_BYTES:
            raise EncodingError('Worker message exceeds the bounded frame')
        size = len(encoded) | (_FRAGMENT if fragmented else 0)
        remaining = memoryview(struct.pack('!Q', size) + encoded)
        while remaining:
            written = os.write(self.write_fd, remaining)
            if not written:
                raise ProtocolError('Worker stopped reading')
            remaining = remaining[written:]

    def receive(self, *, timeout: float | None = None) -> tuple[Any, int]:
        deadline = None if timeout is None else time.monotonic() + timeout

        def exact(size: int) -> bytes:
            parts = []
            while size:
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0 or not select.select([self.read_fd], [], [], left)[0]:
                        raise ProtocolError('Worker response timed out')
                value = os.read(self.read_fd, min(size, 64 * 1024))
                if not value:
                    raise ProtocolError('Worker closed its response pipe')
                parts.append(value)
                size -= len(value)
            return b''.join(parts)

        def frame():
            header = struct.unpack('!Q', exact(8))[0]
            size, fragmented = header & ~_FRAGMENT, bool(header & _FRAGMENT)
            if not 0 < size <= FRAME_BYTES:
                raise ProtocolError('Worker response exceeds the bounded frame')
            return exact(size), fragmented

        def decode(raw):
            try:
                return marshal.loads(raw, allow_code=False)
            except (ValueError, TypeError, EOFError) as exc:
                raise ProtocolError('Invalid primitive worker response') from exc

        raw, fragmented = frame()
        if not fragmented:
            value = decode(raw)
            self._received += 1
            return value, len(raw)
        chunks: list[bytes] = []
        total = count = None
        received = 0
        while True:
            part = decode(raw)
            if (type(part) is not tuple or len(part) != 5
                    or any(type(number) is not int for number in part[:4])
                    or part[0] != self._received + 1 or part[1] != len(chunks)
                    or type(part[4]) is not bytes):
                raise ProtocolError('Worker fragment identity or order changed')
            if total is None:
                count, total = part[2:4]
                if (not FRAME_BYTES < total <= sys.maxsize or count < 2
                        or count != (total + _CHUNK_BYTES - 1) // _CHUNK_BYTES):
                    raise ProtocolError('Invalid worker fragment resource declaration')
            if part[2:4] != (count, total):
                raise ProtocolError('Worker fragment declaration changed')
            assert count is not None and total is not None
            expected = min(_CHUNK_BYTES, total - received)
            if len(part[4]) != expected or len(chunks) >= count:
                raise ProtocolError('Worker fragment length changed')
            chunks.append(part[4]);received += len(part[4])
            if len(chunks) == count:
                if received != total:
                    raise ProtocolError('Incomplete worker primitive message')
                value = decode(b''.join(chunks))
                self._received += 1
                return value, received
            raw, fragmented = frame()
            if not fragmented:
                raise ProtocolError('Worker fragment sequence ended before its declared message')

    def close(self) -> None:
        for attribute in ('read_fd', 'write_fd'):
            fd = getattr(self, attribute)
            if fd >= 3:
                setattr(self, attribute, -1)
                os.close(fd)
