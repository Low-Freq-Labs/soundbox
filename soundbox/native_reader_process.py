"""Read-only native snapshots on an eagerly prepared, Qt-free process."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from . import library_codec
from .library import Library
from .worker_process import WorkerProcess
from .worker_protocol import ProtocolError

MODE = 'native-read'
VERSION = 1


def _matches(value: Any, expected: tuple) -> bool:
    return (type(value) is tuple and len(value) == len(expected)
            and all(type(actual) is type(wanted) and actual == wanted
                    for actual, wanted in zip(value, expected)))


class NativeReaderProcess:
    """Called on the existing source QThread; this API never writes a source.

    Native writers still own recovery, authority, source admission and the
    closed-application guard. Each request makes the unchanged fresh reader
    take its own stable read-only DB/WAL snapshot. No result is cached here.
    """
    def __init__(self, root: Path, *, process: WorkerProcess | None = None):
        self.worker = process or WorkerProcess(MODE, root)
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.worker.available

    def start(self) -> None:
        ready = self.worker.start()
        if not _matches(ready, ('ready', MODE, VERSION, library_codec.SCHEMA)):
            error = ProtocolError('Native reader schema changed; retry after installing a consistent application')
            self.worker.invalidate(error)
            raise error

    def read_metadata(self, previous: Library) -> Library:
        scope = (str(previous.source), previous.source_kind, previous.source_library_id)
        if previous.source_kind != 'rekordbox-db' or not Path(scope[0]).is_absolute():
            raise ProtocolError('Native metadata read needs its exact source scope')

        def rows():
            for local, track in previous.tracks_by_id.items():
                yield (local, track.track_id, track.native_id, track.source_uuid,
                       track.location_uri, track.source_native_path, track.imported)

        return self._read('metadata', scope,
            (('paths', len(previous.tracks_by_id), rows()),
             ('bindings', len(previous.source_binding_issues), previous.source_binding_issues.items())))

    def read_live(self, source: str | Path | None = None, *, retain_tracks: dict | None = None) -> Library:
        from .rekordbox_db import REKORDBOX_DB
        path = str(Path(source or REKORDBOX_DB).expanduser())
        retained = retain_tracks or {}
        return self._read('live', (path,), (('retained', len(retained), retained.items()),))

    def _read(self, operation: str, scope: tuple, sections) -> Library:
        with self._lock:
            self.start()
            channel = self.worker.channel
            nonce = uuid4().hex
            def exchange(message: tuple) -> Any:
                channel.send(message)
                response, _size = channel.receive()
                if isinstance(response, tuple) and response[:3] == ('error', VERSION, nonce):
                    raise ProtocolError('Could not read the native library: ' + str(response[3]))
                return response

            try:
                counts = tuple((name, count) for name, count, _rows in sections)
                response = exchange(('begin', VERSION, nonce, operation, scope, counts))
                if not _matches(response, ('ack', VERSION, nonce, 0)):
                    raise ProtocolError('Native reader did not acknowledge its exact source')
                sequence = 0
                for name, _count, values in sections:
                    pending = []
                    for row in values:
                        pending.append(row)
                        if len(pending) < library_codec.BATCH_RECORDS:
                            continue
                        sequence += 1
                        if not _matches(exchange(('input', VERSION, nonce, sequence, name, pending)), ('ack', VERSION, nonce, sequence)):
                            raise ProtocolError('Native reader input order changed')
                        pending = []
                    if pending:
                        sequence += 1
                        if not _matches(exchange(('input', VERSION, nonce, sequence, name, pending)), ('ack', VERSION, nonce, sequence)):
                            raise ProtocolError('Native reader input order changed')
                response = exchange(('read', VERSION, nonce))
                if type(response) is not tuple or len(response) != 4 or not _matches(response[:3], ('library', VERSION, nonce)):
                    raise ProtocolError('Native reader returned no complete library')
                receiver = library_codec.LibraryReceiver(response[3], max_records=None)
                channel.send(('ack', VERSION, nonce, 0))
                sequence = 0
                while True:
                    response, _size = channel.receive()
                    if _matches(response, ('done', VERSION, nonce, sequence)):
                        result = receiver.finish()
                        if operation == 'metadata' and (
                                str(result.source), result.source_kind, result.source_library_id) != scope:
                            raise ProtocolError('Native reader source identity changed')
                        if operation == 'live' and Path(result.source) != Path(scope[0]):
                            raise ProtocolError('Native reader returned another source')
                        return result
                    sequence += 1
                    if (type(response) is not tuple or len(response) != 7
                            or not _matches(response[:4], ('part', VERSION, nonce, sequence))):
                        raise ProtocolError('Native reader output order changed')
                    receiver.add(response[4], response[5], response[6])
                    channel.send(('ack', VERSION, nonce, sequence))
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                self.worker.invalidate(exc)
                raise ProtocolError('Native library verification failed; its pending changes are preserved: ' + str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self.worker.close()
