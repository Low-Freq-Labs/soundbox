"""Artwork growth follows a per-file performance budget without rewriting old art."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image
from clickwheel.ipod.itunesdb_writer import TrackInfo

from soundbox import stock_artwork as art


class StockArtworkCapacity(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        self.folder = self.root / 'device/iPod_Control/Artwork'
        self.cache = art.StockArtworkCache(self.root / 'cache')
        self.prepared = []
        for index, color in enumerate(((220, 20, 30), (20, 40, 220), (30, 210, 40))):
            path = self.root / f'{index}.png'
            Image.new('RGB', (317, 193), color).save(path)
            self.prepared.append(self.cache.prepare(path))

    def tracks(self, count):
        return [TrackInfo(title=f'Generated {index}', location=f':Music:F00:{index}.mp3',
                          size=100, length=1000, filetype='mp3', dbid=101 + index)
                for index in range(count)]

    def render(self, tracks, prepared):
        return art.render_stock_artwork(tracks, prepared, cache=self.cache,
                                        existing_artwork_dir=self.folder)

    def seed(self, count):
        tracks = self.tracks(count)
        rendered = self.render(tracks, {track.dbid: self.prepared[index]
                                      for index, track in enumerate(tracks)})
        self.folder.mkdir(parents=True)
        (self.folder / 'ArtworkDB').write_bytes(rendered.artwork_db)
        for plan in rendered.ithmb_files:
            (self.folder / plan.relative_path.name).write_bytes(
                b''.join(segment.path.read_bytes() for segment in plan.segments))
        return tracks

    def before(self):
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                for path in self.folder.iterdir()}

    def test_upstream_policy_is_256_decimal_megabytes_per_file(self):
        self.assertEqual(art.ITHMB_PER_FILE_PERFORMANCE_BUDGET, 256_000_000)

    def test_full_render_refuses_before_payload_assembly_or_track_link_mutation(self):
        tracks = self.tracks(2)
        original = [(row.mhii_link, row.artwork_count, row.artwork_size) for row in tracks]
        with (mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 159_999),
              mock.patch.object(art, '_combined_sha256', side_effect=AssertionError('Payload assembly started')),
              self.assertRaisesRegex(art.StockArtworkError,
                  r'Artwork capacity: F1029_1.ithmb.*160,000.*159,999.*segmentation')):
            self.render(tracks, {101: self.prepared[0], 102: self.prepared[1]})
        self.assertEqual([(row.mhii_link, row.artwork_count, row.artwork_size) for row in tracks], original)
        self.assertFalse(self.folder.exists())

    def test_complete_stock_plan_refuses_without_changing_catalog_playback_or_media(self):
        from soundbox import stock_ipod
        from tests import test_stock_ipod as fixtures

        fixture = fixtures.StockIpodSync()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        def snapshot():
            return {str(path.relative_to(fixture.ipod)): path.read_bytes() if path.is_file() else None
                    for path in fixture.ipod.rglob('*')}

        before = snapshot()
        images = fixtures._FixtureArtCache({'old': self.root / '0.png', 'new': self.root / '1.png'})
        with (mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 159_999),
              self.assertRaisesRegex(art.StockArtworkError, r'Artwork capacity: F1029_1.ithmb.*segmentation')):
            stock_ipod.plan_stock_ipod_sync(fixture.library, [fixture.playlist], fixture.ipod,
                model_number='MA446', filesystem='fat32', usb_family='iPod Video',
                art_cache=images, stock_art_cache=fixture.stock_art_cache)
        self.assertEqual(snapshot(), before)

    def test_full_render_exact_boundary_keeps_pixels_and_dimensions(self):
        tracks = self.tracks(2)
        with mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 160_000):
            rendered = self.render(tracks, {101: self.prepared[0], 102: self.prepared[1]})
        self.assertEqual({plan.relative_path.name: plan.size for plan in rendered.ithmb_files},
                         {'F1028_1.ithmb': 40_000, 'F1029_1.ithmb': 160_000})
        for plan in rendered.ithmb_files:
            fid = 1028 if plan.relative_path.name.startswith('F1028') else 1029
            self.assertEqual(b''.join(row.path.read_bytes() for row in plan.segments),
                             b''.join(item.payload(fid).path.read_bytes() for item in self.prepared[:2]))
        art.validate_stock_artwork(rendered, tracks)

    def test_full_render_counts_effective_unique_selected_pixels(self):
        tracks = self.tracks(3)
        with mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 80_000):
            rendered = self.render(tracks, {101: self.prepared[0], 102: self.prepared[0],
                                            103: self.prepared[0], 999: self.prepared[1]})
        self.assertEqual(rendered.unique_images, 1)
        self.assertEqual(rendered.linked_tracks, 3)
        self.assertEqual([plan.size for plan in rendered.ithmb_files], [20_000, 80_000])
        art.validate_stock_artwork(rendered, tracks)

    def test_full_relayout_preserves_existing_oversized_pixels_without_growth(self):
        tracks = self.seed(2)
        before = self.before()
        with mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 1):
            rendered = self.render(tracks, {})
        self.assertEqual(self.before(), before)
        for plan in rendered.ithmb_files:
            self.assertEqual(b''.join(row.path.read_bytes() for row in plan.segments),
                             before[plan.relative_path.name][0])
        art.validate_stock_artwork(rendered, tracks)

    def test_incremental_growth_refuses_before_any_device_mutation(self):
        tracks = self.seed(1)
        before = self.before()
        links = [(row.mhii_link, row.artwork_count, row.artwork_size) for row in tracks]
        with (mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 80_000),
              self.assertRaisesRegex(art.StockArtworkError, r'Artwork capacity: F1029_1.ithmb.*segmentation')):
            art.render_incremental_stock_artwork(tracks, {101: self.prepared[1]},
                                                 existing_artwork_dir=self.folder)
        self.assertEqual(self.before(), before)
        self.assertEqual([(row.mhii_link, row.artwork_count, row.artwork_size) for row in tracks], links)

    def test_incremental_exact_growth_boundary_preserves_current_pixels(self):
        tracks = self.seed(1)
        before = self.before()
        with mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 160_000):
            rendered = art.render_incremental_stock_artwork(tracks, {101: self.prepared[1]},
                                                           existing_artwork_dir=self.folder)
        self.assertEqual(self.before(), before)
        self.assertEqual([plan.size for plan in rendered.ithmb_files], [40_000, 160_000])
        for write in rendered.range_writes:
            self.assertEqual(write.offset, len(before[write.relative_path.name][0]))
            self.assertEqual(write.segment.path.read_bytes(), self.prepared[1].payload(write.format_id).path.read_bytes())
        art.validate_stock_artwork(rendered, tracks)

    def test_oversized_retained_metadata_prune_and_read_only_admission_remain_valid(self):
        tracks = self.seed(2)
        before = self.before()
        retained = tracks[:1]
        with (mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 1),
              mock.patch.object(art, '_sha256_file', side_effect=AssertionError('Retained pixel hash'))):
            _raw, slots, files = art.read_stock_artwork_slots(self.folder)
            rendered = art.render_incremental_stock_artwork(retained, {}, existing_artwork_dir=self.folder)
        self.assertEqual(len(slots), 2)
        self.assertEqual({fid: row[0] for fid, row in files.items()}, {1028: 40_000, 1029: 160_000})
        self.assertFalse(rendered.range_writes)
        self.assertEqual(len(rendered.slots), 1)
        self.assertEqual(self.before(), before)
        art.validate_stock_artwork(rendered, retained)

    def test_oversized_free_hole_reuse_does_not_require_growth_or_compaction(self):
        tracks = self.seed(2)
        retained = tracks[:1]
        pruned = art.render_incremental_stock_artwork(retained, {}, existing_artwork_dir=self.folder)
        (self.folder / 'ArtworkDB').write_bytes(pruned.artwork_db)
        before = self.before()
        with mock.patch.object(art, 'ITHMB_PER_FILE_PERFORMANCE_BUDGET', 1):
            rendered = art.render_incremental_stock_artwork(retained, {101: self.prepared[2]},
                                                           existing_artwork_dir=self.folder)
        self.assertEqual(self.before(), before)
        self.assertEqual([plan.size for plan in rendered.ithmb_files], [40_000, 160_000])
        self.assertEqual(len(rendered.range_writes), 2)
        for write in rendered.range_writes:
            self.assertEqual(write.offset, write.segment.size)
            self.assertLessEqual(write.offset + write.segment.size, len(before[write.relative_path.name][0]))
            self.assertEqual(write.segment.path.read_bytes(), self.prepared[2].payload(write.format_id).path.read_bytes())
        art.validate_stock_artwork(rendered, retained)
