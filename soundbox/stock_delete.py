"""Metadata-only entity guards for explicitly confirmed native iPod deletion.

These witnesses authorize removal of current indexed file entities, not content
integrity or ordinary Sync cleanup. No audio payload is read here.
"""
from __future__ import annotations

from collections.abc import Callable
import ctypes
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import stat
import sys
from typing import TYPE_CHECKING, cast
import unicodedata

from .device_staging import _owned

if TYPE_CHECKING:
    from .sync import DeviceSession


class IndexedDeleteError(RuntimeError):
    pass


def path_key(value: str | Path) -> str:
    return unicodedata.normalize('NFD', str(value).replace('\\', '/')).casefold()


def _epoch(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def _regular(info: os.stat_result, device: int) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_dev == device


@dataclass(frozen=True)
class IndexedFile:
    relative_path: Path
    epoch: tuple[int, ...]
    parent_identity: tuple[int, int]
    dbids: tuple[int, ...] = ()

    @property
    def size(self) -> int:
        return self.epoch[2]


def capture(session: DeviceSession, relative: Path, *,
            dbids: tuple[int, ...] = ()) -> IndexedFile | None:
    """Capture a no-follow native entity without opening its audio payload."""
    try:
        with session.parent_fd(relative) as (parent, name):
            parent_stat = os.fstat(parent)
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not _regular(info, session.mount_identity[0]):
                raise IndexedDeleteError(f'Indexed deletion target is not a regular file with one link: {relative}')
            return IndexedFile(relative, _epoch(info),
                               (parent_stat.st_dev, parent_stat.st_ino), dbids)
    except FileNotFoundError:
        session.revalidate()
        return None


def verify(session: DeviceSession, item: IndexedFile) -> None:
    current = capture(session, item.relative_path, dbids=item.dbids)
    if current != item:
        raise IndexedDeleteError(f'Indexed file changed after confirmation: {item.relative_path}')


@lru_cache(maxsize=1)
def _rename_call() -> Callable[[int, bytes, int, bytes, int], int]:
    if sys.platform != 'darwin':
        raise OSError('Native indexed deletion requires macOS exclusive rename')
    call = ctypes.CDLL(None, use_errno=True).renameatx_np
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                     ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    return cast(Callable[[int, bytes, int, bytes, int], int], call)


def _exclusive_move(parent: int, name: str, target_parent: int, target_name: str) -> None:
    if _rename_call()(parent, os.fsencode(name), target_parent, os.fsencode(target_name), 4):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)


def retire(session: DeviceSession, item: IndexedFile, claim: Path) -> None:
    """Claim without replacement, prove the original open entity, then unlink.

    A foreign leaf arriving after validation is restored without replacement or
    retained at its recorded claim. It is never disposed as our original file.
    """
    with session.parent_fd(item.relative_path) as (parent, name):
        parent_info = os.fstat(parent)
        if (parent_info.st_dev, parent_info.st_ino) != item.parent_identity:
            raise IndexedDeleteError(f'Indexed parent changed: {item.relative_path}')
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(handle)
            if not _regular(info, session.mount_identity[0]) or _epoch(info) != item.epoch:
                raise IndexedDeleteError(f'Indexed file changed before cleanup: {item.relative_path}')
            with session.parent_fd(claim, create=True) as (holding, claim_name):
                session.revalidate()
                _exclusive_move(parent, name, holding, claim_name)
                try:
                    moved = os.fstat(handle)
                    if (not _owned(holding, claim_name, handle)
                            or moved.st_size != info.st_size
                            or moved.st_mtime_ns != info.st_mtime_ns
                            or moved.st_mode != info.st_mode):
                        raise IndexedDeleteError(f'Indexed file was replaced during cleanup: {item.relative_path}')
                    with session.parent_fd(item.relative_path) as (current, _):
                        current_info = os.fstat(current)
                        if (current_info.st_dev, current_info.st_ino) != item.parent_identity:
                            raise IndexedDeleteError(f'Indexed parent changed during cleanup: {item.relative_path}')
                    session.revalidate()
                    os.unlink(claim_name, dir_fd=holding)
                except Exception as error:
                    try:
                        _exclusive_move(holding, claim_name, parent, name)
                    except OSError as restore_error:
                        raise IndexedDeleteError(
                            f'{error}; claimed file retained at {claim}: {restore_error}') from error
                    raise
        finally:
            os.close(handle)
