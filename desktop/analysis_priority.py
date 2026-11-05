"""Waveform-first local analysis; source publication waits for musical results."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading

from soundbox import analysis, analysis_jobs, shared_imports, track_analysis
from soundbox.source_acquisition import SourceContract, acquire_source
from soundbox.native_media import METADATA_PENDING
from .workers import WaveformFillWorker


class AnalysisWaveformWorker(WaveformFillWorker):
    def __init__(self, library, track, job, cache_dir, prepare_cache):
        super().__init__(library, cache_dir)
        self.library, self.track, self.job_id = library, track, job['id']
        self.cache_dir, self.prepare_cache = cache_dir, prepare_cache
        self.stop = threading.Event()
        self.result = None
        self.setStackSize(16 * 1024 ** 2)

    def cancel(self):
        self.stop.set()
        self.requestInterruption()

    def run(self):
        from .player import qt_detail_peaks
        from soundbox.rekordbox_analysis import read
        from soundbox.waveform_detail import analyze_imported, complete_import
        track = self.track
        try:
            with tempfile.TemporaryDirectory(prefix='soundbox-waveform-first-') as area:
                root = Path(area)
                with acquire_source(SourceContract(track.path), root, should_cancel=self.stop.is_set) as captured:
                    self.prepare_cache(track.track_id)
                    imported = read(track.source_analysis_path) if track.source_analysis_path else None
                    checkpoint = lambda: not self.stop.is_set() and self._analysis_checkpoint()
                    try:bpm_hint = float(track.bpm or 0)
                    except (ValueError, TypeError):bpm_hint = 0
                    data = (analyze_imported(captured.path, imported, checkpoint,
                        lambda: qt_detail_peaks(str(captured.path), checkpoint=checkpoint, supplement=True))
                        if complete_import(imported) else None)
                    if data is None and checkpoint():
                        data = track_analysis.analyze_soundfile(captured.path, bpm_hint, checkpoint)
                        if data is None and checkpoint():
                            data = qt_detail_peaks(str(captured.path), checkpoint=checkpoint)
                        if data is not None:
                            data = track_analysis.merge_import(data, imported)
                    if data is None or self.stop.is_set():
                        raise RuntimeError('Waveform preparation cancelled or unavailable')
                    if track.source_cues is not None:
                        data = track_analysis.with_imported_cues(data, track.source_cues)
                    epoch = analysis.identity(captured.observation)
                    def verify():
                        nonlocal epoch
                        for _ in range(2):
                            if analysis.identity(track.path.stat()) == epoch:return
                            with acquire_source(SourceContract(track.path, captured.size, captured.sha256),
                                                root, should_cancel=self.stop.is_set) as checked:
                                epoch = analysis.identity(checked.observation)
                        if analysis.identity(track.path.stat()) != epoch:
                            raise RuntimeError(METADATA_PENDING)
                    for attempt in range(2):
                        verify()
                        expected = list(epoch)
                        stored = track_analysis.store(self.cache_dir, track.track_id, track.path, data,
                                                      expected_epoch=epoch)
                        if stored:break
                        changed = analysis.identity(track.path.stat()) != expected
                        verify()
                        if attempt:raise RuntimeError(METADATA_PENDING if changed else 'Waveform could not be saved')
                    verify()
                    self._publish_bpm(track)
                    self.result = dict(sha256=captured.sha256, epoch=epoch)
        except Exception as exc:
            self.result = exc


class AnalysisPriorityMixin:
    def _analysis_priority_pending(self):
        if self.lib is None or self._shutting_down:
            return False
        return any(job.get('state') == 'queued' and (track := self.lib.tracks_by_id.get(job.get('track_id')))
                   is not None and analysis_jobs.matches(job, self.lib, track)
                   for job in self.settings.get(analysis_jobs.KEY, []) if isinstance(job, dict))

    def _analysis_source_busy(self):
        # Deferred cue/grid intentions must not deadlock their own local-analysis
        # prerequisite. Actual source work and identity probes still finish first.
        tags = self.tag_controller
        return bool(tags.busy or tags.checking or tags.admission_paused)

    def _analysis_prerequisite_ready(self, track, job):
        for pending in self.settings.get(shared_imports.KEY, []):
            if ((pending['source'], pending['library_id']) == (str(self.lib.source), self.lib.source_library_id)
                    and any(item['id'] == track.track_id for item in pending['tracks'])
                    and pending['request'] is None and not pending.get('media_ready')):
                self.shared_import_controller.schedule()
                if pending.get('error'):
                    previous = self.settings.get(analysis_jobs.KEY, [])
                    self.settings[analysis_jobs.KEY] = analysis_jobs.failed(previous, job['id'],
                        'Audio preparation needs attention: '+pending['error'])
                    if not self._save_state():self.settings[analysis_jobs.KEY] = previous
                return False
        if job.get('waveform_sha256'):
            return True
        if self._analysis_worker is not None or self._analysis_source_busy():
            return False
        worker = AnalysisWaveformWorker(self.lib, deepcopy(track), job,
                                        self._track_analysis_dir, self._prepare_grid_cache)
        self._analysis_worker = worker
        worker.set_playback_active(self.player_bar.is_playing())
        worker.bpm_ready.connect(lambda value, ww=worker: self._on_waveform_bpm_ready(ww, value))
        self._analysis_owner.adopt(worker, self._analysis_waveform_retired)
        worker.start()
        return False

    def _analysis_waveform_retired(self, worker):
        self._analysis_worker = None
        if self._shutting_down or self._analysis_owner.stopping:
            return
        if worker.stop.is_set():
            self._schedule_next_analysis()
            return
        if isinstance(worker.result, Exception) and str(worker.result) == METADATA_PENDING:
            self._schedule_next_analysis(2000)
            return
        pending = self.settings.get(analysis_jobs.KEY, [])
        try:
            if isinstance(worker.result, Exception):
                raise worker.result
            track = self.lib.tracks_by_id.get(worker.track.track_id) if self.lib else None
            job = next((j for j in pending if j['id'] == worker.job_id), None)
            if track is None or job is None or not analysis_jobs.matches(job, self.lib, track):
                raise ValueError('Waveform belongs to its original song; analysis intention preserved')
            updated = analysis_jobs.wave_ready(pending, worker.job_id, worker.result['sha256'], worker.result['epoch'])
            if not (job['preferences']['bpm'] or job['preferences']['key']):
                updated = analysis_jobs.finish(updated, worker.job_id)
            self.settings[analysis_jobs.KEY] = updated
            if not self._save_state():
                self.settings[analysis_jobs.KEY] = pending
                raise OSError('Waveform completion could not be saved')
            self.player_bar.refresh_audio_detail(str(track.path))
        except Exception as exc:
            self.settings[analysis_jobs.KEY] = analysis_jobs.failed(pending, worker.job_id, exc)
            if not self._save_state():
                self.settings[analysis_jobs.KEY] = pending
            self._flash_status(f'Couldn’t prepare waveform: {exc}', 15)
            # Remove this failed head from the RAM queue; other songs keep moving.
            self._analysis_queue = [entry for entry in self._analysis_queue if entry[3] != worker.job_id]
        self._schedule_next_analysis()
