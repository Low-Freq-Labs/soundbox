"""Artwork pass eligibility batches reads; publication remains freshly guarded."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from desktop.workers import ArtFillWorker
from soundbox.artwork import ArtCache, ArtResult
from soundbox.library import Library, Track


class ArtworkEligibility(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)

    def library(self, count):
        tracks = {str(index): Track(str(index), f"Song {index}", "Artist",
                                   (self.root/f"missing-{index}.wav").as_uri())
                  for index in range(count)}
        return Library(tracks, [], "synthetic")

    def test_existing_covers_and_three_missing_new_tracks_read_policy_once(self):
        root = self.root/'art'
        root.mkdir()
        index = {str(i): dict(status='ok', ext='jpg', src=f'https://soundcloud.com/artist/song-{i}')
                 for i in range(3000)}
        (root/'index.json').write_text(json.dumps(index))
        policy = root/'source-policy.json'
        policy.write_text(json.dumps(dict(schema=1, accounts=['excluded-account'])))
        cache = ArtCache(root)
        policy = cache.root/'source-policy.json'
        library = self.library(3003)
        provider = SimpleNamespace(fetch_profile_art=mock.Mock(), fetch_best=mock.Mock())
        reads = []
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == policy:
                reads.append(path)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, 'read_text', read), \
                mock.patch.object(cache, 'source_excluded', side_effect=AssertionError('per-track policy read')):
            worker = ArtFillWorker(library, cache, {}, {}, provider=provider)
            worker.DELAY = 0
            worker._fill()
        self.assertEqual(len(reads), 1)
        provider.fetch_profile_art.assert_not_called()
        provider.fetch_best.assert_not_called()
        self.assertEqual(worker.changed_track_ids, set())
        self.assertEqual(cache.index, index)

    def test_batch_is_fresh_each_pass_and_skips_policy_when_no_successful_art(self):
        cache = ArtCache(self.root/'art')
        cache.index = {'a': dict(status='ok', src='https://soundcloud.com/artist/song'),
                       'b': dict(status='none', src='https://soundcloud.com/artist/song')}
        self.assertEqual(cache.source_excluded_ids(iter(('a', 'b'))), frozenset())
        cache.exclude_source('https://soundcloud.com/artist/song')
        self.assertEqual(cache.source_excluded_ids(('a', 'b')), frozenset(('a',)))
        with mock.patch.object(cache, 'source_exclusions', side_effect=AssertionError('unneeded policy')):
            self.assertEqual(cache.source_excluded_ids(('b', 'missing')), frozenset())

    def test_changed_policy_during_request_wins_over_batched_eligibility(self):
        cache = ArtCache(self.root/'art')
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'orange').save(image, 'JPEG')
        source = 'https://soundcloud.com/artist/song'
        cache.store_ok('0', ArtResult(image.getvalue(), 'image/jpeg', 1, source, 'Song'))
        cache.exclude_source(source)
        original = (cache.root/'0.jpg').read_bytes()
        other = ArtCache(cache.root)

        def fetch(_artist):
            other.allow_source('artist')
            return ArtResult(image.getvalue(), 'image/jpeg', 1,
                             'https://soundcloud.com/artist', 'Artist')

        provider = SimpleNamespace(fetch_profile_art=fetch, fetch_best=mock.Mock())
        worker = ArtFillWorker(self.library(1), cache, {}, {}, provider=provider)
        worker.DELAY = 0
        worker._fill()
        self.assertEqual(worker.changed_track_ids, set())
        self.assertEqual((cache.root/'0.jpg').read_bytes(), original)
        self.assertNotIn('fallback', cache.index['0'])
        provider.fetch_best.assert_not_called()

    def test_stopped_pass_never_reads_policy_or_fetches(self):
        cache = ArtCache(self.root/'art')
        provider = SimpleNamespace(fetch_profile_art=mock.Mock(), fetch_best=mock.Mock())
        worker = ArtFillWorker(self.library(1), cache, {}, {}, provider=provider)
        worker.stop()
        with mock.patch.object(cache, 'source_excluded_ids', side_effect=AssertionError('stopped read')):
            worker._fill()
        provider.fetch_profile_art.assert_not_called()
        provider.fetch_best.assert_not_called()
