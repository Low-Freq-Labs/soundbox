"""Read-only native worker; no writer, authority, Qt or callable RPC surface."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import library_codec, rekordbox_db
from .library import Library, Track
from .native_reader_process import MODE, VERSION, _matches
from .worker_protocol import PipeChannel, ProtocolError


def serve(channel: PipeChannel, _root: Path) -> None:
    # Import the ORM before ordinary interaction, without opening any source.
    from pyrekordbox import Rekordbox6Database
    if Rekordbox6Database is None:
        raise ProtocolError('The native reader is unavailable')
    channel.send(('ready', MODE, VERSION, library_codec.SCHEMA))
    job: dict[str, Any] | None = None
    while True:
        message, _size = channel.receive()
        if message == ('stop',):
            channel.send(('stopped', MODE))
            return
        nonce = message[2] if isinstance(message, tuple) and len(message) > 2 else ''
        try:
            if type(message) is not tuple or len(message) < 3 or type(message[1]) is not int or message[1] != VERSION:
                raise ProtocolError('Native reader protocol changed')
            if message[0] == 'begin':
                if job is not None or len(message) != 6:
                    raise ProtocolError('Native reader already owns an unfinished snapshot')
                _kind, _version, nonce, operation, scope, raw_counts = message
                if (type(nonce) is not str or len(nonce) != 32 or operation not in ('metadata', 'live', 'preview')
                        or not isinstance(scope, tuple) or len(scope) != (3 if operation == 'metadata' else 1)
                        or any(type(value) is not str for value in scope) or not Path(scope[0]).is_absolute()):
                    raise ProtocolError('Invalid native source scope')
                if operation == 'metadata' and scope[1] != 'rekordbox-db':
                    raise ProtocolError('Invalid native source kind')
                counts = dict(raw_counts)
                if (len(counts) != len(raw_counts) or set(counts) !=
                        ({'paths', 'bindings'} if operation == 'metadata' else {'retained'})
                        or any(type(count) is not int or count < 0 for count in counts.values())):
                    raise ProtocolError('Invalid native identity counts')
                job = dict(nonce=nonce, operation=operation, scope=scope, expected=counts,
                           received=dict.fromkeys(counts, 0), paths={}, bindings={}, retained={}, sequence=0)
                channel.send(('ack', VERSION, nonce, 0))
                continue
            if job is None or nonce != job['nonce']:
                raise ProtocolError('Native reader has no matching source request')
            if message[0] == 'input':
                if len(message) != 6 or type(message[3]) is not int or message[3] != job['sequence'] + 1:
                    raise ProtocolError('Native identity batches arrived out of order')
                _kind, _version, nonce, sequence, name, rows = message
                if (name not in job['expected'] or not isinstance(rows, list)
                        or not 0 < len(rows) <= library_codec.BATCH_RECORDS
                        or job['received'][name] + len(rows) > job['expected'][name]):
                    raise ProtocolError('Native identity batch exceeds captured counts')
                for row in rows:
                    if name == 'paths':
                        if (not isinstance(row, tuple) or len(row) != 7 or any(type(value) is not str for value in row[:6])
                                or type(row[6]) is not bool):
                            raise ProtocolError('Invalid previous native path facts')
                        local, track_id, native_id, uuid, uri, raw_path, imported = row
                        if local in job['paths']:
                            raise ProtocolError('Duplicate previous logical identity')
                        job['paths'][local] = Track(track_id, '', '', uri, source_native_id=native_id,
                            source_uuid=uuid, source_native_path=raw_path, imported=imported)
                    else:
                        if not isinstance(row, tuple) or len(row) != 2 or type(row[0]) is not str or row[0] in job[name]:
                            raise ProtocolError('Invalid native reader identity mapping')
                        if name == 'bindings' and type(row[1]) is not str:
                            raise ProtocolError('Invalid previous native binding issue')
                        if name == 'retained' and (not isinstance(row[1], dict)
                                or any(type(key) is not str or type(value) is not str for key, value in row[1].items())):
                            raise ProtocolError('Invalid retained Collection identities')
                        job[name][row[0]] = row[1]
                job['received'][name] += len(rows)
                job['sequence'] = sequence
                channel.send(('ack', VERSION, nonce, sequence))
                continue
            if message != ('read', VERSION, nonce) or job['received'] != job['expected']:
                raise ProtocolError('Native reader request is incomplete')
            scope = job['scope']
            if job['operation'] == 'metadata':
                previous = Library(job['paths'], [], scope[0], source_kind=scope[1],
                    source_library_id=scope[2], source_binding_issues=job['bindings'])
                library = rekordbox_db.load_metadata_readback(previous)
            elif job['operation'] == 'preview':
                library = rekordbox_db.load_live(Path(scope[0]), resolve_media_paths=False)
            else:
                library = rekordbox_db.load_live(Path(scope[0]), retain_tracks=job['retained'])
            if library.artist_registry is not None:
                raise ProtocolError('Native reader returned an opaque presentation object')
            channel.send(('library', VERSION, nonce, library_codec.describe(library)))
            if not _matches(channel.receive()[0], ('ack', VERSION, nonce, 0)):
                raise ProtocolError('Native library header was not acknowledged')
            sequence = 0
            for kind, field, rows in library_codec.batches(library):
                sequence += 1
                channel.send(('part', VERSION, nonce, sequence, kind, field, rows))
                if not _matches(channel.receive()[0], ('ack', VERSION, nonce, sequence)):
                    raise ProtocolError('Native library batch was not acknowledged')
            channel.send(('done', VERSION, nonce, sequence))
            job = None
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            channel.send(('error', VERSION, nonce, str(exc)[:4096]))
            job = None
