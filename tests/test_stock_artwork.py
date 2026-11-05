"""Prepared stock-iPod artwork cache and binary layout tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from clickwheel.ipod.itunesdb_writer import TrackInfo

from soundbox.stock_artwork import (
    FORMATS,
    StockArtworkCache,
    StockArtworkError,
    render_stock_artwork,
    validate_stock_artwork,
    StockArtworkSegment,
    _combined_sha256,
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

    def test_combined_digest_verifies_each_payload_in_its_only_read(self):
        paths = [self.root / "one.rgb565", self.root / "two.rgb565"]
        values = [b"first" * 100, b"second" * 100]
        segments = []
        for path, value in zip(paths, values):
            path.write_bytes(value)
            segments.append(StockArtworkSegment(path, len(value), hashlib.sha256(value).hexdigest()))
        opened = []
        original_open = Path.open

        def observed(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", observed):
            combined = _combined_sha256(segments)
        self.assertEqual(combined, hashlib.sha256(b"".join(values)).hexdigest())
        self.assertEqual(opened, paths)
        paths[1].write_bytes(b"x" * len(values[1]))
        with self.assertRaisesRegex(StockArtworkError, "content"):
            _combined_sha256(segments)

    def test_corrupt_cached_payload_is_rebuilt(self):
        prepared = self.cache.prepare(self.image)
        victim = prepared.payload(1028).path
        victim.write_bytes(b"x" * victim.stat().st_size)
        rebuilt = self.cache.prepare(self.image)
        self.assertEqual(victim.stat().st_size, 100 * 100 * 2)
        self.assertEqual(rebuilt.cache_key, prepared.cache_key)

    def test_source_index_can_be_published_once_after_a_batch(self):
        self.cache.prepare(self.image, verify_cache=False, save_index=False)
        index = self.root / "cache" / "ma446-video-rgb565-v1" / "source-index.json"
        self.assertFalse(index.exists())

        self.cache.flush_source_index()

        self.assertTrue(index.exists())
        reopened = StockArtworkCache(self.root / "cache")
        self.assertEqual(
            reopened.prepare(self.image, verify_cache=False).cache_key,
            self.cache.prepare(self.image, verify_cache=False).cache_key,
        )

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
