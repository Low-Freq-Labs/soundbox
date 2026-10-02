"""Read macOS's cached mount table without opening any mounted filesystem.

getfsstat64(MNT_NOWAIT) returns the kernel's retained table (see getfsstat(2)).
This is a discovery hint only: diskutil and DeviceSession still establish all
external-device identity and write authority. No free-space values are used.
"""
from __future__ import annotations

import ctypes
import os
import sys


class _StatFS64(ctypes.Structure):
    # Darwin sys/mount.h __DARWIN_STRUCT_STATFS64; explicit 64-bit ABI.
    _fields_ = [
        ("bsize", ctypes.c_uint32), ("iosize", ctypes.c_int32),
        ("blocks", ctypes.c_uint64), ("bfree", ctypes.c_uint64),
        ("bavail", ctypes.c_uint64), ("files", ctypes.c_uint64),
        ("ffree", ctypes.c_uint64), ("fsid", ctypes.c_int32 * 2),
        ("owner", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32), ("subtype", ctypes.c_uint32),
        ("fstypename", ctypes.c_char * 16),
        ("mntonname", ctypes.c_char * 1024),
        ("mntfromname", ctypes.c_char * 1024),
        ("flags_ext", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 7),
    ]


def _load_getfsstat():
    if sys.platform != "darwin":
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).getfsstat64
        fn.argtypes = [ctypes.POINTER(_StatFS64), ctypes.c_int, ctypes.c_int]
        fn.restype = ctypes.c_int
        return fn
    except (OSError, AttributeError):
        return None


_getfsstat = _load_getfsstat()
MountSnapshot = tuple[tuple[str, int, int, str], ...]


def mounted_volume_snapshot() -> MountSnapshot | None:
    """A bounded cached-table read; None means unavailable, never unmounted."""
    if _getfsstat is None:
        return None
    nowait = 2
    count = _getfsstat(None, 0, nowait)
    if count < 0 or count > 4096:
        return None
    # Leave room for concurrent mounts; a filled buffer is incomplete, so retry
    # on the next timer rather than publishing false removals from truncation.
    rows = (_StatFS64 * (count + 16))()
    used = _getfsstat(rows, ctypes.sizeof(rows), nowait)
    if used < 0 or used >= len(rows):
        return None
    result = []
    for row in rows[:used]:
        path = os.fsdecode(row.mntonname)
        if path.startswith("/Volumes/") and "/" not in path[len("/Volumes/"):]:
            result.append((path, row.fsid[0], row.fsid[1], os.fsdecode(row.mntfromname)))
    return tuple(sorted(result))
