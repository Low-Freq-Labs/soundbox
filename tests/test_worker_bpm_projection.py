"""Worker-only disposable fallback BPM projection from source-bound headers."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np

from desktop.workers import WaveformFillWorker
from soundbox import analysis, track_analysis as ta
from soundbox.library import Library, Track


class WorkerBpmProjection(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.source = self.root / 'audio.wav'
        with wave.open(str(self.source), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes((np.sin(np.arange(8000) * .2) * 10000).astype('<i2').tobytes())
        self.cache = self.root / 'analysis'
        self.track = Track('logical', 'Test', '', self.source.as_uri(),
                           size=self.source.stat().st_size, source_native_id='42', source_uuid='identity')
        self.lib = Library({'logical': self.track}, [], 'synthetic',
                           source_kind='rekordbox-db', source_library_id='library')
        self.prepared = ta.analyze_soundfile(self.source)
        self.prepared['beats'] = [[1, 0, 120.0], [2, 500, 120.0], [3, 1000, 120.0]]
        self.record = ta.store(self.cache, 'logical', self.source, self.prepared)

    def worker(self):
        worker = WaveformFillWorker(self.lib, self.cache)
        worker.DELAY = 0
        received = []
        worker.bpm_ready.connect(received.append)
        return worker, received

    def test_completed_inventory_hits_publish_identity_bound_tempo_without_audio_or_payload_reads(self):
        real_open = Path.open
        source = self.source
        record = self.record
        class HeaderReader:
            def __init__(self, handle):self.handle = handle
            def __enter__(self):return self
            def __exit__(self, *args):return self.handle.__exit__(*args)
            def read(self, size=-1):
                if size < 0:raise AssertionError('Full waveform payload read')
                return self.handle.read(size)
            def __getattr__(self, name):return getattr(self.handle, name)
        def opening(path, *args, **kwargs):
            if path == source:raise AssertionError('Source audio opened')
            handle = real_open(path, *args, **kwargs)
            return HeaderReader(handle) if path == record else handle
        for _ in range(2):
            worker, got = self.worker()
            with mock.patch.object(Path, 'open', opening), mock.patch.object(ta, '_load_sections', side_effect=AssertionError('Payload read')):
                worker.run()
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0], dict(track_id='logical', path=str(source), track_size=self.track.size,
                source='synthetic', source_kind='rekordbox-db', library_id='library', native_id='42',
                uuid='identity', size=self.track.size, epoch=analysis.identity(source.stat()),
                bpm=120.0, provenance='soundbox-zero-anchor'))

    def test_native_bpm_native_analysis_and_hidden_rows_do_not_project_fallback(self):
        for track in (replace(self.track, bpm='125.00'), replace(self.track, source_analysis_path='native.DAT')):
            worker, got = self.worker()
            with mock.patch.object(ta, 'load_manifest', side_effect=AssertionError('Unneeded cache read')):
                worker._publish_bpm(track)
            self.assertEqual(got, [])
        self.lib.hidden_ids.add('logical')
        worker, got = self.worker()
        worker._publish_bpm(self.track)
        self.assertEqual(got, [])

    def test_discovery_survives_an_import_while_scanning_its_library_snapshot(self):
        worker, _got = self.worker()
        original = ta.is_ready
        def importing(*args):
            self.lib.tracks_by_id['new-import'] = replace(self.track, track_id='new-import')
            return original(*args)
        with mock.patch.object(ta, 'is_ready', side_effect=importing):
            worker.run()
        self.assertIn('new-import', self.lib.tracks_by_id)

    def test_missing_stale_and_malformed_headers_clear_previous_estimate(self):
        for kind in ('missing', 'stale', 'malformed', 'manual', 'native'):
            with self.subTest(kind=kind):
                prepared = dict(self.prepared)
                if kind == 'malformed':prepared['beats'] = [[1, 0, float('nan')], [2, 500, 120]]
                if kind in ('manual', 'native'):
                    prepared['provenance'] = dict(grid='soundbox-manual' if kind == 'manual' else 'rekordbox')
                ta.store(self.cache, 'logical', self.source, prepared)
                if kind == 'missing':self.record.unlink()
                if kind == 'stale':
                    stat = self.source.stat()
                    os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
                worker, got = self.worker()
                worker._publish_bpm(self.track)
                self.assertEqual(len(got), 1)
                self.assertIsNone(got[0]['bpm'])

    def test_source_change_during_header_observation_clears_estimate(self):
        worker, got = self.worker()
        original = ta.load_manifest
        def changed(*args):
            manifest = original(*args)
            stat = self.source.stat()
            os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            return manifest
        with mock.patch.object(ta, 'load_manifest', changed):
            worker._publish_bpm(self.track)
        self.assertIsNone(got[0]['bpm'])

    def test_known_track_size_mismatch_does_not_project_newer_cache(self):
        worker, got = self.worker()
        stale = replace(self.track, size=self.track.size + 1)
        worker._publish_bpm(stale)
        self.assertEqual(got[0]['track_size'], stale.size)
        self.assertEqual(got[0]['size'], self.track.size)
        self.assertEqual(got[0]['epoch'][2], self.track.size)
        self.assertIsNone(got[0]['bpm'])

    def test_newly_completed_record_replaces_initial_miss_without_saved_analysis(self):
        self.record.unlink()
        worker, got = self.worker()
        with mock.patch.object(ta, 'analyze_soundfile', return_value=self.prepared) as analyze:
            worker.run()
        self.assertEqual([row['bpm'] for row in got], [None, 120.0])
        analyze.assert_called_once()
        self.assertEqual(self.track.bpm, '')
        self.assertEqual(self.track.source_analysis_path, '')


if __name__ == '__main__':
    unittest.main()
