"""Qt image decoding without Python QIODevice callbacks inside plugin locks."""
from tempfile import NamedTemporaryFile

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader


def _read_path(path, bounds):
    # A filename makes Qt create its own native QFile. A Python-created QBuffer
    # can reacquire the GIL in seek() while the image plugin mutex is held,
    # deadlocking a simultaneous GUI pixmap decode that holds the GIL.
    reader = QImageReader(str(path))
    if bounds is not None:
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(bounds, Qt.KeepAspectRatio))
    return reader.read()


def read_image(source, *, bounds=None):
    """Read exact bytes or a path with Qt's unchanged format and scaling policy."""
    try:
        if isinstance(source, bytes):
            # Private 0600 scratch exists only through this decode; publication
            # still uses the captured snapshot, never a mutable artwork path.
            with NamedTemporaryFile(prefix="soundbox-image-") as snapshot:
                snapshot.write(source)
                snapshot.flush()
                return _read_path(snapshot.name, bounds)
        return _read_path(source, bounds)
    except OSError:
        return QImage()
