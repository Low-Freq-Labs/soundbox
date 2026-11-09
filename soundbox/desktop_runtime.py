"""Prepare ordinary desktop analysis/playback code without preparing any songs.

CLI and engine imports remain lightweight. The desktop calls this before user
interaction; attached-device backends retain their separate readiness policy.
"""
from functools import lru_cache
import importlib
import logging


DESKTOP_MODULES = ('numpy', 'scipy.signal', 'scipy.ndimage', 'soundfile', 'zstandard')
LOG = logging.getLogger('soundbox')


@lru_cache(maxsize=1)
def prepare_desktop_runtime() -> tuple[str, ...]:
    """Warm fixed local dependencies once; failures leave library management live.

    Imports only: no source discovery, audio decoding, analysis, network work or
    song-sized buffers. Consumers keep their existing dependency failure paths.
    """
    diagnostics = []
    for name in DESKTOP_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            message = f'Desktop runtime dependency unavailable ({name}): {exc}'
            LOG.warning(message)
            diagnostics.append(message)
    return tuple(diagnostics)
