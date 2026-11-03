"""Captured waveform → BPM → key precedes native publication admission."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import wave

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.analysis_priority import AnalysisWaveformWorker
from soundbox import analysis, analysis_jobs, track_analysis
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository


class WaveformPriority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.audio = self.root / 'song.wav'
        rate = 8000
        samples = np.zeros(rate * 4, dtype='<i2')
        for start in range(0, len(samples), rate // 2):
            samples[start:start+80] = (np.sin(np.arange(80) * .4) * 20000).astype('<i2')
        with wave.open(str(self.audio), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())
        self.track = Track('sb-local', 'Song', '', self.audio.as_uri(), imported=True,
                           size=self.audio.stat().st_size)
        self.lib = Library({self.track.track_id: self.track}, [], 'synthetic')
        self.cache = self.root / 'cache'

    def test_waveform_is_built_from_capture_then_checkpointed_to_exact_original_bytes(self):
        seen = []
        original = track_analysis.analyze_soundfile
        def build(path, *args):
            self.assertNotEqual(Path(path), self.audio)
            seen.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
            return original(path, *args)
        worker = AnalysisWaveformWorker(self.lib, self.track, {'id': 'wave'}, self.cache, mock.Mock())
        with mock.patch.object(track_analysis, 'analyze_soundfile', side_effect=build):worker.run()
        self.assertIsInstance(worker.result, dict)
        self.assertEqual(seen, [worker.result['sha256']])
        self.assertEqual(worker.result['sha256'], hashlib.sha256(self.audio.read_bytes()).hexdigest())
        self.assertEqual(worker.result['epoch'], analysis.identity(self.audio.stat()))
        self.assertTrue(track_analysis.is_ready(self.cache, self.track.track_id, self.audio))
        worker.prepare_cache.assert_called_once_with(self.track.track_id)

    def test_real_audio_change_during_waveform_build_does_not_publish_a_waveform(self):
        original = track_analysis.analyze_soundfile
        def changed(*args):
            data = original(*args)
            with self.audio.open('ab') as handle:handle.write(b'changed')
            return data
        worker = AnalysisWaveformWorker(self.lib, self.track, {'id': 'wave'}, self.cache, lambda _tid: None)
        with mock.patch.object(track_analysis, 'analyze_soundfile', side_effect=changed):worker.run()
        self.assertIsInstance(worker.result, Exception)
        self.assertFalse(track_analysis.is_ready(self.cache, self.track.track_id, self.audio))

    def test_metadata_only_change_is_proved_without_losing_completed_waveform(self):
        original = track_analysis.analyze_soundfile
        def metadata(*args):
            data = original(*args)
            os.chmod(self.audio, 0o640)
            return data
        worker = AnalysisWaveformWorker(self.lib, self.track, {'id': 'wave'}, self.cache, lambda _tid: None)
        with mock.patch.object(track_analysis, 'analyze_soundfile', side_effect=metadata):worker.run()
        self.assertIsInstance(worker.result, dict)
        self.assertTrue(track_analysis.is_ready(self.cache, self.track.track_id, self.audio))

    def test_audio_change_at_store_boundary_cannot_bind_old_waveform_to_changed_bytes(self):
        previous=track_analysis.store(self.cache,self.track.track_id,self.audio,
                                      track_analysis.analyze_soundfile(self.audio))
        original_bytes=previous.read_bytes()
        original=track_analysis.store
        def changed(*args,**kwargs):
            with self.audio.open('r+b') as handle:
                handle.seek(100);handle.write(b'\x01\x02')
            return original(*args,**kwargs)
        worker=AnalysisWaveformWorker(self.lib,self.track,{'id':'wave'},self.cache,lambda _tid:None)
        with mock.patch.object(track_analysis,'store',side_effect=changed):worker.run()
        self.assertIsInstance(worker.result,Exception)
        self.assertFalse(track_analysis.is_ready(self.cache,self.track.track_id,self.audio))
        self.assertEqual(previous.read_bytes(),original_bytes)

    def test_window_runs_waveform_then_bpm_then_key_and_saves_each_dependency(self):
        from desktop.app import MainWindow
        with mock.patch('soundbox.rekordbox_db.db_available', return_value=False), \
                mock.patch.object(MainWindow, '_refresh_devices'):
            win = MainWindow(isolated_dir=self.root / 'state')
        self.addCleanup(lambda: (win.shutdown(), win.close(), win.deleteLater(), self.app.processEvents()))
        win.lib = self.lib; win.tag_controller.configure(win.lib, False)
        win.grid_controller.configure(); win._view_key = 'sb:Collection'
        win._waveform_rechecks_enabled = False; win._waveform_recheck_timer.stop()
        win.model.set_rows([win._row_for(self.track)])
        events = []
        original = track_analysis.analyze_soundfile
        def build(*args):events.append('waveform'); return original(*args)
        def detect(_lib, track, prefs, **kwargs):
            phase = 'bpm' if prefs['bpm'] else 'key'
            events.append(phase)
            saved = StateRepository(win._data_file).load()['settings']
            job = saved[analysis_jobs.KEY][0]
            self.assertIn('waveform_sha256', job)
            self.assertTrue(track_analysis.is_ready(win._track_analysis_dir, track.track_id, track.path))
            if phase == 'key':
                self.assertEqual(saved['analysis_results'][analysis.result_key(track.path)]['bpm'], 120)
            sha = hashlib.sha256(track.path.read_bytes()).hexdigest()
            self.assertEqual(kwargs['expected_sha256'], sha)
            return dict(engine=analysis.ENGINE, path=str(track.path), epoch=analysis.identity(track.path.stat()),
                size=track.path.stat().st_size, sha256=sha, duration=4, bpm=120 if prefs['bpm'] else None,
                beats=[[1,0,120],[2,500,120]] if prefs['bpm'] else [],
                key='8A' if prefs['key'] else None, first_cue=None)
        with mock.patch.object(track_analysis, 'analyze_soundfile', side_effect=build), \
                mock.patch.object(analysis, 'analyze', side_effect=detect):
            win._analyze_tracks([self.track.track_id])
            end = time.monotonic() + 10
            while win.settings.get(analysis_jobs.KEY) and time.monotonic() < end:QTest.qWait(5)
        self.assertEqual(events, ['waveform','bpm','key'])
        self.assertEqual(win.settings.get(analysis_jobs.KEY), [])
        self.assertEqual(win.model._rows[0]['bpm'], '120.00')
        self.assertEqual(win.model._rows[0]['key'], '8A')
        self.assertFalse(win._analysis_priority_pending())

    def test_prerequisite_conversion_blocks_waveform_until_durable_media_checkpoint(self):
        from types import SimpleNamespace
        from desktop.analysis_priority import AnalysisPriorityMixin
        pending = dict(source='synthetic', library_id='', tracks=[dict(id=self.track.track_id)], request=None)
        host = SimpleNamespace(lib=self.lib, settings={'shared_import_jobs': [pending]},
            shared_import_controller=SimpleNamespace(schedule=mock.Mock()), _analysis_worker=None,
            _analysis_source_busy=lambda: False)
        job = dict(id='job', waveform_sha256='a'*64)
        self.assertFalse(AnalysisPriorityMixin._analysis_prerequisite_ready(host, self.track, job))
        host.shared_import_controller.schedule.assert_called_once()
        pending['media_ready'] = True
        self.assertTrue(AnalysisPriorityMixin._analysis_prerequisite_ready(host, self.track, job))

    def test_failed_compatibility_prerequisite_retires_only_detection_priority(self):
        from types import SimpleNamespace
        from desktop.analysis_priority import AnalysisPriorityMixin
        queued = analysis_jobs.admit([], self.lib, [self.track], {})
        pending = dict(source='synthetic', library_id='', tracks=[dict(id=self.track.track_id)],
                       request=None, error='Unsupported audio')
        host = SimpleNamespace(lib=self.lib,
            settings={'shared_import_jobs': [pending], analysis_jobs.KEY: queued},
            shared_import_controller=SimpleNamespace(schedule=mock.Mock()),
            _analysis_worker=None, _analysis_source_busy=lambda: False, _save_state=mock.Mock(return_value=True))
        self.assertFalse(AnalysisPriorityMixin._analysis_prerequisite_ready(host, self.track, queued[0]))
        self.assertEqual(host.settings[analysis_jobs.KEY][0]['state'], 'failed')
        self.assertEqual(host.settings['shared_import_jobs'], [pending])
        self.assertEqual(host.settings[analysis_jobs.KEY][0]['path'], str(self.audio))

    def test_repeated_metadata_changes_during_cache_encoding_stay_pending_without_redecoding(self):
        from soundbox.native_media import METADATA_PENDING
        original = track_analysis.store
        modes = iter((0o640, 0o600))
        def metadata(*args, **kwargs):
            os.chmod(self.audio, next(modes))
            return original(*args, **kwargs)
        worker = AnalysisWaveformWorker(self.lib, self.track, {'id': 'wave'}, self.cache, lambda _tid: None)
        with mock.patch.object(track_analysis, 'store', side_effect=metadata), \
                mock.patch.object(track_analysis, 'analyze_soundfile', wraps=track_analysis.analyze_soundfile) as decode:
            worker.run()
        self.assertEqual(str(worker.result), METADATA_PENDING)
        self.assertEqual(decode.call_count, 1)

    def test_waveform_only_intent_finishes_without_starting_either_detector(self):
        from desktop.app import MainWindow
        with mock.patch('soundbox.rekordbox_db.db_available', return_value=False), \
                mock.patch.object(MainWindow, '_refresh_devices'):
            win = MainWindow(isolated_dir=self.root / 'wave-only-state')
        self.addCleanup(lambda: (win.shutdown(), win.close(), win.deleteLater(), self.app.processEvents()))
        win.lib = self.lib; win.tag_controller.configure(win.lib, False)
        win.grid_controller.configure(); win._view_key = 'sb:Collection'
        win.settings[analysis_jobs.KEY] = analysis_jobs.admit([], self.lib, [self.track], dict(bpm=False, key=False))
        with mock.patch.object(analysis, 'analyze', side_effect=AssertionError('Wave-only must not run detectors')):
            win._resume_analysis_jobs()
            end = time.monotonic() + 10
            while win.settings.get(analysis_jobs.KEY) and time.monotonic() < end:QTest.qWait(5)
        self.assertEqual(win.settings[analysis_jobs.KEY], [])
        self.assertTrue(track_analysis.is_ready(win._track_analysis_dir, self.track.track_id, self.audio))


if __name__ == '__main__':unittest.main()
