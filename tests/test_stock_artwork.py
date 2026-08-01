"""Prepared stock-iPod artwork cache and binary layout tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from clickwheel.ipod.itunesdb_writer import TrackInfo

from soundbox.stock_artwork import (
    FORMATS,
    StockArtworkCache,
    StockArtworkError,
    render_stock_artwork,
    validate_stock_artwork,
)


class StockArtwork(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = StockArtworkCache(self.root / "cache")
        self.image = self.root / "curated.jpg"
        Image.new("RGB", (320, 240), (24, 90, 180)).save(
            self.image, "JPEG", quality=90)

    @staticmethod
    def _track(dbid: int) -> TrackInfo:
        return TrackInfo(
            title=f"Track {dbid}", artist="Artist",
            location=f":iPod_Control:Music:F00:{dbid}.mp3",
            size=1000, length=1000, filetype="mp3", dbid=dbid,
        )

    def test_content_addressed_cache_is_exact_and_reusable(self):
        first = self.cache.prepare(self.image)
        second_path = self.root / "same-bytes.jpg"
        second_path.write_bytes(self.image.read_bytes())
        second = self.cache.prepare(second_path)

        self.assertEqual(first.cache_key, second.cache_key)
        self.assertEqual(first.payloads, second.payloads)
        self.assertEqual(
            {item.format_id: item.size for item in first.payloads},
            {format_id: width * height * 2
             for format_id, (width, height) in FORMATS.items()},
        )

    def test_corrupt_cached_payload_is_rebuilt(self):
        prepared = self.cache.prepare(self.image)
        victim = prepared.payload(1028).path
        victim.write_bytes(b"x" * victim.stat().st_size)
        rebuilt = self.cache.prepare(self.image)
        self.assertEqual(victim.stat().st_size, 100 * 100 * 2)
        self.assertEqual(rebuilt.cache_key, prepared.cache_key)

    def test_layout_deduplicates_pixels_but_links_every_track(self):
        prepared = self.cache.prepare(self.image)
        tracks = [self._track(101), self._track(202)]
        rendered = render_stock_artwork(
            tracks,
            {101: prepared, 202: prepared},
            cache=self.cache,
            existing_artwork_dir=self.root / "empty-artwork",
        )
        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertEqual(rendered.linked_tracks, 2)
        self.assertEqual(rendered.unique_images, 1)
        self.assertEqual([item.size for item in rendered.ithmb_files], [20_000, 80_000])
        self.assertEqual(len({track.mhii_link for track in tracks}), 2)
        self.assertTrue(all(track.artwork_count == 1 for track in tracks))
        validate_stock_artwork(rendered, tracks)

    def test_validator_rejects_out_of_bounds_ithmb_plan(self):
        prepared = self.cache.prepare(self.image)
        tracks = [self._track(101)]
        rendered = render_stock_artwork(
            tracks, {101: prepared}, cache=self.cache,
            existing_artwork_dir=self.root / "empty-artwork")
        assert rendered is not None
        first = rendered.ithmb_files[0]
        broken = replace(
            rendered,
            ithmb_files=(replace(first, size=first.size - 1), *rendered.ithmb_files[1:]),
        )
        with self.assertRaisesRegex(StockArtworkError, "total size"):
            validate_stock_artwork(broken, tracks)


if __name__ == "__main__":
    unittest.main()
