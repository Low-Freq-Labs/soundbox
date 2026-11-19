"""Internal engine-only dispatch, before desktop and macOS preference setup."""
from pathlib import Path
import sys

from .worker_protocol import PipeChannel


def main(arguments: list[str]) -> int:
    if len(arguments) != 4 or arguments[0] not in ('cache', 'native-read'):
        return 2
    channel = None
    try:
        mode, directory, read_fd, write_fd = arguments
        root = Path(directory)
        if not root.is_absolute():
            return 2
        channel = PipeChannel(int(read_fd), int(write_fd))
        if any(name == 'soundbox.macos_defaults' or name.startswith(('desktop', 'PySide6'))
               for name in sys.modules):
            return 2
        if mode == 'cache':
            from .cache_process import serve as cache_serve
            return cache_serve(channel, root)
        else:
            from .native_reader_worker import serve as native_serve
            native_serve(channel, root)
            return 0
    except Exception:
        return 1
    finally:
        if channel is not None:
            channel.close()
