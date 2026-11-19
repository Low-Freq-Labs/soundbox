"""Keep the internal engine role Qt-free before PyInstaller's Qt hook runs.

PyInstaller6.21's PySide6 hook imports QtCore solely to create embedded qt.conf.
Engine helpers need no Qt relocation. All other bootstrap hooks and the normal
application entrypoint still run; desktop launches retain the original helper.
"""
import sys


def _prepare_worker_bootstrap():
    if sys.argv[1:2] != ['--soundbox-worker']:
        return
    from _pyi_rth_utils import qt  # type: ignore[import-not-found]  # frozen bootstrap only
    if not callable(qt.create_embedded_qt_conf):
        raise RuntimeError('The pinned Qt bootstrap helper changed')

    def no_qt_configuration(*_args, **_kwargs):
        return None

    qt.create_embedded_qt_conf = no_qt_configuration


_prepare_worker_bootstrap()
del _prepare_worker_bootstrap
