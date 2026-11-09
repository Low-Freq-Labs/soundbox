"""Atomic publication for disposable, Mac-side derivative/cache files.

This preserves the cache writer's existing file-fsync/replace contract. Durable
AppData transactions use state_io; device writes require their guarded session.
"""
from pathlib import Path
from collections.abc import Callable
import os
import stat
from uuid import uuid4


def atomic_cache_bytes(path: Path, data: bytes, *, sync_file: bool = True,
                       before_replace: Callable[[], None] | None = None) -> None:
    """Publish through an owned temporary; retain each cache's sync policy.

    A caller's final source-epoch guard may raise before publication. It runs
    after staging, preserving the original validation boundary for waveforms.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ''
    owned = None
    try:
        for _ in range(8):
            temporary = f'.soundbox-cache-{os.getpid()}-{uuid4().hex}.sbpart'
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o666, dir_fd=directory)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError('Could not reserve a private cache temporary')
        with os.fdopen(descriptor, 'wb') as handle:
            details = os.fstat(handle.fileno())
            owned = details.st_dev, details.st_ino
            handle.write(data)
            handle.flush()
            if sync_file:
                os.fsync(handle.fileno())
        if before_replace is not None:
            before_replace()
        current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != owned:
            raise OSError('Cache temporary changed before publication')
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            if owned is not None:
                try:
                    current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == owned:
                        os.unlink(temporary, dir_fd=directory)
        finally:
            os.close(directory)
