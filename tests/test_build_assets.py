"""The actual packaged ICNS follows the fresh white default, not legacy artwork."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from scripts.build_assets import build_icon
from desktop import app_icon, theme


class BundleAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actual_icns_contains_white_default_pixels_and_preserves_authored_source(self):
        source = Path(__file__).resolve().parents[1] / 'assets/icon_1024.png'
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='soundbox-build-icon-test-') as area:
            path = build_icon(Path(area) / 'Soundbox.icns')
            raw = path.read_bytes()
            self.assertEqual(raw[:4], b'icns')
            self.assertEqual(struct.unpack('>I', raw[4:8])[0], len(raw))
            offset = 8; images = {}
            while offset < len(raw):
                kind, size = struct.unpack('>4sI', raw[offset:offset+8])
                self.assertGreaterEqual(size, 8)
                self.assertLessEqual(offset + size, len(raw))
                image = QImage.fromData(raw[offset+8:offset+size])
                if not image.isNull(): images[kind] = image
                offset += size
            self.assertEqual(offset, len(raw))
            self.assertTrue({b'ic07', b'ic08', b'ic09', b'ic10'} <= images.keys())
            actual = images[b'ic10'].convertToFormat(QImage.Format_RGBA8888)
            expected = app_icon.accented_image(theme.DEFAULT_ACCENT, 1024).convertToFormat(QImage.Format_RGBA8888)
            self.assertEqual(theme.DEFAULT_ACCENT, '#ffffff')
            # iconutil adds sRGB metadata; prove all RGBA pixels, not metadata.
            self.assertEqual(bytes(actual.constBits()), bytes(expected.constBits()))
            self.assertEqual(actual.pixelColor(round((128+18)*1024/292),
                                               round((3+18)*1024/292)).name(), '#ffffff')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
