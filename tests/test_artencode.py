"""Rockbox-safe cover art: baseline-JPEG detection, re-encode, cache normalization."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from soundbox import artencode
from soundbox.artfetch import ArtCache, ArtResult


def _img_bytes(fmt: str, size=(64, 64), mode="RGB", **save_kw) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, size, (200, 40, 40) if mode == "RGB" else (200, 40, 40, 128)).save(
        buf, fmt, **save_kw)
    return buf.getvalue()


class KindDetection(unittest.TestCase):
    def test_baseline_jpeg(self):
        self.assertEqual(artencode.kind(_img_bytes("JPEG")), "baseline")
        self.assertTrue(artencode.is_device_safe(_img_bytes("JPEG")))

    def test_progressive_jpeg(self):
        data = _img_bytes("JPEG", progressive=True)
        self.assertEqual(artencode.kind(data), "progressive")
        self.assertFalse(artencode.is_device_safe(data))

    def test_png(self):
        self.assertEqual(artencode.kind(_img_bytes("PNG")), "png")

    def test_garbage(self):
        self.assertEqual(artencode.kind(b"not an image at all"), "unknown")


class Reencode(unittest.TestCase):
    def test_progressive_becomes_baseline(self):
        out = artencode.to_baseline_jpeg(_img_bytes("JPEG", progressive=True))
        self.assertEqual(artencode.kind(out), "baseline")

    def test_png_with_alpha_becomes_baseline(self):
        out = artencode.to_baseline_jpeg(_img_bytes("PNG", mode="RGBA"))
        self.assertEqual(artencode.kind(out), "baseline")

    def test_oversize_capped(self):
        from PIL import Image
        out = artencode.to_baseline_jpeg(_img_bytes("JPEG", size=(1200, 800)))
        w, h = Image.open(io.BytesIO(out)).size
        self.assertLessEqual(max(w, h), artencode.MAX_SIDE)

    def test_undecodable_returns_none(self):
        self.assertIsNone(artencode.to_baseline_jpeg(b"junk"))

    def test_ensure_passthrough_when_safe(self):
        data = _img_bytes("JPEG")
        self.assertIs(artencode.ensure_device_safe(data), data)

    def test_ensure_converts_unsafe(self):
        out = artencode.ensure_device_safe(_img_bytes("PNG"))
        self.assertEqual(artencode.kind(out), "baseline")


class CacheNormalization(unittest.TestCase):
    def _res(self, data, mime):
        return ArtResult(data=data, mime=mime, confidence=0.9,
                         source_url="https://soundcloud.com/a/t", matched_title="t")

    def test_store_ok_normalizes_png_to_jpg(self):
        with tempfile.TemporaryDirectory() as td:
            cache = ArtCache(Path(td))
            cache.store_ok("42", self._res(_img_bytes("PNG"), "image/png"))
            p = cache.image_path("42")
            self.assertEqual(p.suffix, ".jpg")
            self.assertEqual(artencode.kind(p.read_bytes()), "baseline")

    def test_store_ok_normalizes_progressive(self):
        with tempfile.TemporaryDirectory() as td:
            cache = ArtCache(Path(td))
            cache.store_ok("7", self._res(_img_bytes("JPEG", progressive=True), "image/jpeg"))
            self.assertEqual(artencode.kind(cache.image_path("7").read_bytes()), "baseline")

    def test_normalize_all_migrates_legacy_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # A legacy cache written before normalization existed: one PNG,
            # one progressive JPEG, one already-safe baseline.
            (root / "1.png").write_bytes(_img_bytes("PNG"))
            (root / "2.jpg").write_bytes(_img_bytes("JPEG", progressive=True))
            (root / "3.jpg").write_bytes(_img_bytes("JPEG"))
            (root / "index.json").write_text(json.dumps({
                "1": {"status": "ok", "ext": "png"},
                "2": {"status": "ok", "ext": "jpg"},
                "3": {"status": "ok", "ext": "jpg"},
                "4": {"status": "none"},
            }))
            cache = ArtCache(root)
            checked, fixed = cache.normalize_all()
            self.assertEqual(checked, 3)
            self.assertEqual(fixed, 2)
            for tid in ("1", "2", "3"):
                p = cache.image_path(tid)
                self.assertEqual(p.suffix, ".jpg")
                self.assertEqual(artencode.kind(p.read_bytes()), "baseline")
            self.assertFalse((root / "1.png").exists())  # old PNG cleaned up
            # Second run is flag-filed → instant no-op.
            self.assertEqual(cache.normalize_all(), (0, 0))


if __name__ == "__main__":
    unittest.main()
