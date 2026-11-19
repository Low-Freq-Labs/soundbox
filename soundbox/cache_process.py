"""Prewarmed cache-only process; no source, audio, device or GUI authority."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import tempfile
import threading
from typing import Any, Iterator

from . import browser_cache, libcache, library_codec
from .library import Library
from .worker_process import WorkerProcess
from .worker_protocol import EncodingError, PipeChannel, ProtocolError

MODE = 'cache'
VERSION = 1
READY = ('ready', MODE, VERSION, libcache.SCHEMA, browser_cache.SCHEMA, library_codec.SCHEMA)
# Keep the existing artifact ceilings. Marshal repeats display/native facts and
# adds typed framing; its aggregate allowance must not become a smaller cache
# limit. Single records still have the explicit 1MiB transport bound.
LIBRARY_BYTES = libcache._MAX_CACHE_BYTES
BROWSER_BYTES = 64 * 1024**2
JOB_BYTES = 4 * (LIBRARY_BYTES + BROWSER_BYTES)
MAX_RECORDS = LIBRARY_BYTES // 4


class CacheError(RuntimeError):
    pass


def _matches(value: Any, expected: tuple) -> bool:
    return (type(value) is tuple and len(value) == len(expected)
            and all(type(actual) is type(wanted) and actual == wanted
                    for actual, wanted in zip(value, expected)))


def _row_batches(values) -> Iterator[list]:
    pending = []
    for value in values:
        pending.append(value)
        if len(pending) == library_codec.BATCH_RECORDS:
            yield pending
            pending = []
    if pending:
        yield pending


class CacheProcess:
    """Start before interaction; write only from its desktop-owned worker."""
    def __init__(self, root: Path, *, command=None, startup_timeout: float = 5):
        self._worker = WorkerProcess(MODE, root, command=command, startup_timeout=startup_timeout)
        self._lock = threading.Lock()
        self._nonce = 0
        self._enabled = False

    @property
    def available(self) -> bool:
        return self._enabled and self._worker.available

    @property
    def process(self):
        return self._worker.process

    def start(self) -> None:
        self._worker.root.mkdir(parents=True, exist_ok=True)
        ready = self._worker.start()
        if not _matches(ready, READY):
            error = ProtocolError('Cache worker schema changed')
            self._worker.invalidate(error)
            raise error
        self._enabled = True

    def _response(self, expected: tuple, nonce: int) -> Any:
        value, _size = self._worker.channel.receive()
        if isinstance(value, tuple) and _matches(value[:4], ('error', MODE, VERSION, nonce)):
            if len(value) != 5 or type(value[4]) is not str:
                raise ProtocolError('Invalid cache failure response')
            raise CacheError(value[4])
        if not _matches(value, expected):
            raise ProtocolError('Cache worker response identity changed')
        return value

    def write(self, library: Library, inputs: tuple | None, rows: dict, *, key: str | None = None) -> None:
        with self._lock:
            if not self.available:
                raise CacheError('Cache worker is unavailable')
            descriptor = library_codec.describe(library)
            nonce = self._nonce + 1
            header = ('begin', MODE, VERSION, nonce, descriptor, key,
                      inputs[1:] if inputs is not None else None,
                      len(inputs[0]) if inputs is not None else 0, len(rows))
            begun = False
            total = 0
            sequence = 0
            try:
                total += self._worker.channel.send(header)
                self._nonce = nonce
                begun = True
                self._response(('ack', MODE, VERSION, nonce, 0), nonce)

                def emit(kind: str, field: str, values: list) -> None:
                    nonlocal sequence, total
                    sequence += 1
                    total += self._worker.channel.send(('part', MODE, VERSION, nonce,
                                                       sequence, kind, field, values))
                    if total > JOB_BYTES:
                        raise EncodingError('Cache capture exceeds the artifact-derived budget')
                    self._response(('ack', MODE, VERSION, nonce, sequence), nonce)

                for kind, field, values in library_codec.batches(library):
                    emit(kind, field, values)
                if inputs is not None:
                    for values in _row_batches(inputs[0]):
                        emit('inputs', '', values)
                for values in _row_batches(rows.items()):
                    emit('rows', '', values)
                self._worker.channel.send(('commit', MODE, VERSION, nonce, sequence + 1))
                self._response(('done', MODE, VERSION, nonce), nonce)
            except CacheError:
                raise  # The server retired this failed job; the next nonce is usable.
            except (EncodingError, TypeError, ValueError, RuntimeError) as exc:
                # A local capture/encoding failure must release the incomplete
                # job without publishing either staged artifact.
                try:
                    if begun:
                        self._worker.channel.send(('abort', MODE, VERSION, nonce))
                        self._response(('aborted', MODE, VERSION, nonce), nonce)
                except (OSError, ProtocolError, CacheError) as abort_error:
                    self._enabled = False
                    self._worker.invalidate(abort_error)
                if isinstance(exc, ProtocolError) and not isinstance(exc, EncodingError):
                    self._enabled = False
                    self._worker.invalidate(exc)
                raise CacheError(str(exc)) from exc
            except (OSError, EOFError) as exc:
                self._enabled = False
                self._worker.invalidate(exc)
                raise CacheError('Cache worker communication failed') from exc

    def close(self) -> None:
        with self._lock:
            self._enabled = False
            self._worker.close()


def _publish(root: Path, library: Library, key: str | None, inputs: tuple | None, rows: dict) -> None:
    # A failed encoder never replaces the previous snapshot. The two caches
    # remain independently validated disposable files with their existing bytes.
    stage = Path(tempfile.mkdtemp(prefix='.derived-cache-', dir=root))
    try:
        libcache.dump(library, stage / 'libcache.json')
        if (stage / 'libcache.json').stat().st_size > LIBRARY_BYTES:
            raise CacheError('Library snapshot exceeds its existing cache ceiling')
        if key is None and inputs is not None:
            key = browser_cache.fingerprint(inputs, bounded=True)
        # Staging must preserve the original destination writer's symlink skip.
        if key is not None and not (root / 'browsercache.json').is_symlink():
            browser_cache.dump(stage / 'browsercache.json', key, rows)
            if (stage / 'browsercache.json').stat().st_size > BROWSER_BYTES:
                raise CacheError('Prepared rows exceed their existing cache ceiling')
        os.replace(stage / 'libcache.json', root / 'libcache.json')
        if (stage / 'browsercache.json').exists():
            os.replace(stage / 'browsercache.json', root / 'browsercache.json')
    finally:
        shutil.rmtree(stage)


def serve(channel: PipeChannel, root: Path) -> int:
    """Internal helper role. Root and pipe ownership come from the launcher."""
    if not root.is_absolute() or not root.is_dir():
        raise ProtocolError('Cache worker requires its existing owned data directory')
    channel.send(READY)
    last_nonce = 0
    job = None
    while True:
        value, size = channel.receive()
        if value == ('stop',):
            channel.send(('stopped', MODE))
            return 0
        nonce = 0
        try:
            if (not isinstance(value, tuple) or len(value) < 4
                    or not _matches(value[1:3], (MODE, VERSION)) or type(value[3]) is not int):
                raise ProtocolError('Invalid cache worker request')
            action, _mode, _version, nonce = value[:4]
            if action == 'begin':
                if len(value) != 9 or job is not None or nonce <= last_nonce:
                    raise ProtocolError('Cache request ownership changed')
                last_nonce = nonce
                _action, _mode, _version, _nonce, descriptor, key, tail, input_count, row_count = value
                if (key is not None and type(key) is not str or tail is not None and type(tail) is not tuple
                        or any(type(count) is not int or not 0 <= count <= MAX_RECORDS
                               for count in (input_count, row_count))
                        or tail is None and input_count):
                    raise ProtocolError('Invalid captured cache inputs')
                receiver = library_codec.LibraryReceiver(descriptor, max_records=MAX_RECORDS)
                job = dict(nonce=nonce, receiver=receiver, key=key, tail=tail,
                           input_count=input_count, row_count=row_count, inputs=[], rows={},
                           sequence=0, size=size)
                channel.send(('ack', MODE, VERSION, nonce, 0))
            elif action == 'abort':
                if len(value) != 4 or job is None or job['nonce'] != nonce:
                    raise ProtocolError('Cache abort ownership changed')
                job = None
                channel.send(('aborted', MODE, VERSION, nonce))
            elif action in ('part', 'commit'):
                if (job is None or job['nonce'] != nonce or len(value) < 5
                        or type(value[4]) is not int or value[4] != job['sequence'] + 1):
                    raise ProtocolError('Cache frame order changed')
                job['sequence'] = value[4]
                job['size'] += size
                if job['size'] > JOB_BYTES:
                    raise ProtocolError('Cache job exceeds its artifact-derived budget')
                if action == 'part':
                    if len(value) != 8:
                        raise ProtocolError('Invalid cache record frame')
                    kind, field, values = value[5:]
                    if (type(kind) is not str or type(field) is not str or not isinstance(values, list)
                            or not 0 < len(values) <= library_codec.BATCH_RECORDS):
                        raise ProtocolError('Invalid bounded cache records')
                    if kind == 'inputs':
                        if field or job['tail'] is None or len(job['inputs']) + len(values) > job['input_count']:
                            raise ProtocolError('Captured cache input count changed')
                        job['inputs'].extend(values)
                    elif kind == 'rows':
                        for identity, row in values:
                            if (type(identity) is not str or not isinstance(row, dict)
                                    or identity in job['rows'] or len(job['rows']) >= job['row_count']):
                                raise ProtocolError('Captured display row identity changed')
                            job['rows'][identity] = row
                    else:
                        job['receiver'].add(kind, field, values)
                    channel.send(('ack', MODE, VERSION, nonce, value[4]))
                else:
                    if (len(value) != 5 or len(job['inputs']) != job['input_count']
                            or len(job['rows']) != job['row_count']):
                        raise ProtocolError('Incomplete captured cache inputs')
                    library = job['receiver'].finish()
                    inputs = None if job['tail'] is None else (frozenset(job['inputs']), *job['tail'])
                    if inputs is not None and len(inputs[0]) != job['input_count']:
                        raise ProtocolError('Duplicate captured cache input')
                    _publish(root, library, job['key'], inputs, job['rows'])
                    job = None
                    channel.send(('done', MODE, VERSION, nonce))
            else:
                raise ProtocolError('Unknown cache worker operation')
        except Exception as exc:
            job = None
            channel.send(('error', MODE, VERSION, nonce, str(exc)[:4096]))
