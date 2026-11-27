"""Exact artwork pixels and bounded reproduction of Qt's plugin/GIL lock cycle."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.check_tests import qt_test_python_command

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication


def _encoded(fmt="PNG"):
    image = Image.new("RGBA", (97, 61))
    image.putdata([(x * 13 % 256, y * 19 % 256, (x + y) * 7 % 256,
                    (x * y) % 256) for y in range(61) for x in range(97)])
    if fmt not in {"PNG", "TIFF", "WEBP", "ICO"}:
        image = image.convert("RGB")
    if fmt == "XBM":
        image = image.convert("1")
    output = io.BytesIO()
    image.save(output, fmt)
    return output.getvalue()


def _large_encoded(fmt, side):
    image = Image.effect_noise((side, side), 64).convert("RGB")
    output = io.BytesIO()
    image.save(output, fmt)
    return output.getvalue()


def _selection(data):
    return SimpleNamespace(snapshot=SimpleNamespace(data=data, digest="captured"), cached=None)


def _probe(*, legacy=False):
    """Run only in a disposable subprocess: the old implementation deadlocks."""
    from threading import Event
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPixmap
    from desktop import artreview, track_info

    app = QApplication.instance() or QApplication([])
    entered = Event()

    class SlowSeek(QBuffer):
        def seek(self, pos):
            # Qt's WBMP probe calls virtual seek under its image plugin mutex.
            # Yield here so GUI loadFromData takes the GIL and waits for that
            # mutex. On the old path neither thread can make progress afterward.
            entered.set()
            time.sleep(.1)
            return super().seek(pos)

    def old_read(data, *, bounds=None):
        buffer = SlowSeek()
        buffer.setData(data)
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        if bounds is not None:
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(bounds, Qt.KeepAspectRatio))
        return reader.read()

    if legacy:
        artreview.read_image = track_info.read_image = old_read
    # Also catch regression to the original inlined QBuffer implementation.
    artreview.QBuffer = track_info.QBuffer = SlowSeek
    artreview.QIODevice = track_info.QIODevice = QIODevice
    artreview.QImageReader = track_info.QImageReader = QImageReader
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(1)
    png = _encoded()
    samples = (b"not an image", _large_encoded("JPEG", 500), _large_encoded("PNG", 2500))
    workers = []
    decoding_ticks = 0
    try:
        for factory in (lambda data: artreview._ReviewCover(_selection(data)),
                        lambda data: track_info.ArtworkWorker(lambda: data)):
            for data in samples:
                entered.clear()
                worker = factory(data)
                workers.append(worker)
                worker.start()
                if data == b"not an image":
                    until = time.monotonic() + .3
                    while worker.isRunning() and not entered.is_set() and time.monotonic() < until:
                        time.sleep(.001)
                pixmap = QPixmap()
                assert pixmap.loadFromData(png)
                until = time.monotonic() + 2
                while worker.isRunning() and time.monotonic() < until:
                    before = len(ticks)
                    app.processEvents()
                    decoding_ticks += len(ticks) - before
                    time.sleep(.001)
                assert worker.wait(1000), "decoder worker did not finish"
                assert worker.image.isNull() == (data == b"not an image")
                assert not entered.is_set(), "decoder called a Python QIODevice virtual"
        assert decoding_ticks >= 2, decoding_ticks
    finally:
        timer.stop()
        for worker in workers:
            worker.requestInterruption()
            assert worker.wait(1000)


class ImageDecode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_both_workers_avoid_plugin_lock_gil_cycle_with_gui_decode_and_heartbeat(self):
        completed = subprocess.run(
            qt_test_python_command("from tests.test_image_decode import _probe; _probe()"),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_qt_formats_and_worker_scaling_preserve_exact_pixels(self):
        from desktop.artreview import _ReviewCover
        from desktop.track_info import ArtworkWorker
        supported = {bytes(fmt).decode().upper() for fmt in QImageReader.supportedImageFormats()}
        cases = [(fmt, _encoded(fmt)) for fmt in
                 ("PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP", "ICO", "PPM", "XBM")
                 if fmt in supported or (fmt == "JPEG" and "JPG" in supported)]
        cases += [("SVG", b'<svg xmlns="http://www.w3.org/2000/svg" width="97" height="61">'
                          b'<rect width="97" height="61" fill="#7843bc"/></svg>'),
                  ("WBMP", b"\0\0\x08\x02\xaa\x55"), ("invalid", b"not an image")]
        for fmt, data in cases:
            for side in (None, 43):
                with self.subTest(fmt=fmt, side=side):
                    buffer = QBuffer()
                    buffer.setData(data)
                    buffer.open(QIODevice.ReadOnly)
                    reader = QImageReader(buffer)
                    bounds = QSize(side, side) if side else None
                    if bounds is not None:
                        size = reader.size()
                        if size.isValid():
                            reader.setScaledSize(size.scaled(bounds, Qt.KeepAspectRatio))
                    expected = reader.read()
                    # TIFF's reader destructor still accesses its device.
                    del reader
                    if bounds is not None and not expected.isNull():
                        expected = expected.scaled(bounds, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    worker = _ReviewCover(_selection(data)) if side is None else ArtworkWorker(lambda: data, side)
                    worker.run()
                    self.assertEqual(worker.image, expected)
                    if fmt != "invalid":
                        self.assertFalse(expected.isNull())

    def test_private_snapshot_removed_after_success_failure_and_decoder_exception(self):
        from desktop import image_decode
        with tempfile.TemporaryDirectory() as scratch:
            original = image_decode.NamedTemporaryFile
            def snapshot(**kwargs):
                temporary = original(dir=scratch, **kwargs)
                self.assertEqual(Path(temporary.name).stat().st_mode & 0o777, 0o600)
                return temporary
            with patch.object(image_decode, "NamedTemporaryFile", side_effect=snapshot):
                self.assertFalse(image_decode.read_image(_encoded()).isNull())
                self.assertEqual(list(Path(scratch).iterdir()), [])
                self.assertTrue(image_decode.read_image(b"not an image").isNull())
                self.assertEqual(list(Path(scratch).iterdir()), [])
                with patch.object(image_decode, "_read_path", side_effect=ValueError("decoder")):
                    with self.assertRaisesRegex(ValueError, "decoder"):
                        image_decode.read_image(_encoded())
                self.assertEqual(list(Path(scratch).iterdir()), [])
            path = Path(scratch) / "existing.png"
            path.write_bytes(_encoded())
            with patch.object(image_decode, "NamedTemporaryFile", side_effect=AssertionError("path copied")):
                self.assertFalse(image_decode.read_image(path).isNull())

    def test_failed_scratch_does_not_publish_and_cancelled_review_avoids_decode(self):
        from desktop.artreview import _ReviewCover
        from desktop.track_info import ArtworkWorker
        from desktop import image_decode
        for worker in (_ReviewCover(_selection(_encoded())), ArtworkWorker(_encoded)):
            with patch.object(image_decode, "NamedTemporaryFile", side_effect=OSError("disk full")):
                worker.run()
            self.assertTrue(worker.image.isNull())
        worker = _ReviewCover(_selection(_encoded()))
        worker.stop()
        with patch("desktop.artreview.read_image", side_effect=AssertionError("cancelled decode")):
            worker.run()
        self.assertTrue(worker.image.isNull())

    def test_info_interruption_after_resolution_avoids_decode(self):
        from desktop.track_info import ArtworkWorker
        def resolve():
            worker.requestInterruption()
            return _encoded()
        worker = ArtworkWorker(resolve)
        with patch("desktop.track_info.read_image", side_effect=AssertionError("cancelled decode")) as decode:
            worker.start()
            self.assertTrue(worker.wait(3000))
        decode.assert_not_called()
        self.assertTrue(worker.image.isNull())
