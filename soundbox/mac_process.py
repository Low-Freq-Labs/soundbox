"""Fresh kernel process checks through the small bundled macOS helper.

Only the loaded function interface is retained. Every call takes a new kernel
process snapshot; unknown results require the caller's independent fallback.
"""
import ctypes
from functools import lru_cache
from pathlib import Path
import sys

REKORDBOX_NAMES = (b'rekordbox', b'rekordboxAgent')


@lru_cache(maxsize=1)
def _native_function():
    if sys.platform != 'darwin':
        return None
    if getattr(sys, 'frozen', False):
        location = getattr(sys, '_MEIPASS', None)
        if not isinstance(location, (str, Path)) or not Path(location).is_absolute():
            return None
        root = Path(location)
    else:
        root = Path(__file__).resolve().parents[1] / 'build'
    path = root / 'native' / 'libsoundbox_process_guard.dylib'
    try:
        library = ctypes.CDLL(str(path))
        function = library.sb_process_names_running
        function.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t]
        function.restype = ctypes.c_int
        return function
    except (OSError, AttributeError):
        return None


def process_names_running(names):
    """Return True/False only for a complete fresh snapshot, otherwise None."""
    if (type(names) is not tuple or not 1 <= len(names) <= 16
            or any(type(name) is not bytes or not 1 <= len(name) <= 16 or b'\0' in name
                   for name in names)):
        return None
    function = _native_function()
    if function is None:
        return None
    targets = (ctypes.c_char_p * len(names))(*names)
    try:
        result = function(targets, len(names))
    except (OSError, ValueError, TypeError):
        return None
    return bool(result) if type(result) is int and result in (0, 1) else None


def rekordbox_running():
    return process_names_running(REKORDBOX_NAMES)
