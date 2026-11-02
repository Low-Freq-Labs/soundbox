"""Analysis controls and cancellable captured-audio jobs with owned lifetime."""
from copy import deepcopy
import threading

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (QCheckBox,
    QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)
from .choices import ComboBox as QComboBox

from soundbox import analysis, analysis_jobs, local_cues, local_grid
from .task_owner import TaskOwner


class AnalysisControls(QWidget):
    changed = Signal()

    def __init__(self, preferences=None, *, automatic=False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30 if automatic else 0, 16, 30 if automatic else 0, 16)
        layout.setSpacing(14)
        title = QLabel('TRACK ANALYSIS'); title.setObjectName('SettingsSection'); layout.addWidget(title)
        form = QWidget()
        grid = QGridLayout(form); grid.setContentsMargins(24, 0, 24, 0)
        grid.setHorizontalSpacing(28); grid.setVerticalSpacing(20)
        for column in range(3):grid.setColumnStretch(column, 1)
        def field(label, control, row, column, span=1):
            group = QWidget(); box = QVBoxLayout(group)
            box.setContentsMargins(0, 0, 0, 0); box.setSpacing(8)
            box.addWidget(QLabel(label))
            line = QWidget(); line.setFixedHeight(30)
            line_box = QHBoxLayout(line); line_box.setContentsMargins(0, 0, 0, 0)
            if isinstance(control, QHBoxLayout):line_box.addLayout(control)
            else:line_box.addWidget(control)
            box.addWidget(line)
            grid.addWidget(group, row, column, 1, span, Qt.AlignTop)
        self.bpm = QCheckBox('BPM / Grid')
        self.key = QCheckBox('Key')
        self.phrase = QCheckBox('Phrase'); self.phrase.setEnabled(False)
        self.phrase.setToolTip('Phrase analysis is not available yet')
        choices = QHBoxLayout(); choices.setContentsMargins(0, 0, 0, 0); choices.setSpacing(20)
        for control in (self.bpm, self.key, self.phrase):
            control.setMinimumHeight(28)
            choices.addWidget(control)
            control.toggled.connect(self.changed)
        choices.addStretch()
        field('Analyze', choices, 0, 0)
        self.bpm_range = QComboBox()
        for low, high in analysis.BPM_RANGES:
            self.bpm_range.addItem(f'{low}–{high}')
        field('BPM Range', self.bpm_range, 0, 1)
        self.bpm_range.currentIndexChanged.connect(self.changed)
        self.first_cue = QCheckBox('Set at the first analyzed beat')
        self.first_cue.setMinimumHeight(28)
        field('Memory Cue', self.first_cue, 1, 0, 2)
        self.first_cue.toggled.connect(self.changed)
        self.auto = QCheckBox('Analyze automatically')
        self.auto.setMinimumHeight(28)
        self.auto.setVisible(automatic)
        if automatic:
            field('New Imports', self.auto, 1, 2)
        self.auto.toggled.connect(self.changed)
        self.process = QComboBox(); self.process.addItems(['Performance','Normal','Power saving'])
        # Reserve the shared popup's right-hand check gutter as well as text.
        # A content-only width clips BPM ranges and Power saving.
        for combo, length in ((self.bpm_range, 12), (self.process, 18)):
            combo.setMinimumContentsLength(length)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        field('Analysis Process', self.process, 0, 2)
        self.process.currentIndexChanged.connect(self.changed)
        layout.addWidget(form)
        note = QLabel('Existing saved grids and cues are preserved.')
        note.setObjectName('SettingsSub'); note.setWordWrap(True); layout.addWidget(note)
        layout.addStretch(1)
        self.set_preferences(preferences)

    def set_preferences(self, value):
        try:
            prefs = analysis.options(value)
        except ValueError:
            prefs = analysis.options()
        controls = (self.bpm, self.key, self.first_cue, self.bpm_range, self.process, self.auto)
        for control in controls: control.blockSignals(True)
        self.bpm.setChecked(prefs['bpm']); self.key.setChecked(prefs['key'])
        self.first_cue.setChecked(prefs['first_cue']); self.bpm_range.setCurrentIndex(prefs['bpm_range'])
        self.process.setCurrentText(prefs['process'])
        self.auto.setChecked(isinstance(value, dict) and value.get('automatic') is True)
        for control in controls: control.blockSignals(False)

    def preferences(self):
        return dict(bpm=self.bpm.isChecked(), key=self.key.isChecked(), first_cue=self.first_cue.isChecked(),
                    bpm_range=self.bpm_range.currentIndex(), process=self.process.currentText(),
                    automatic=self.auto.isChecked())


class AnalysisWorker(QThread):
    progress = Signal(int, int, str)

    def __init__(self, library, tracks, preferences, previous):
        super().__init__()
        self.library, self.tracks = library, tracks
        self.preferences, self.previous = preferences, previous
        self.stop = threading.Event()
        self.result = None

    def cancel(self):
        self.stop.set()

    def run(self):
        try:
            results = {}
            for index, track in enumerate(self.tracks):
                if self.stop.is_set(): raise RuntimeError('Analysis cancelled')
                self.progress.emit(index, len(self.tracks), track.name)
                result = analysis.analyze(self.library, track, self.preferences, should_cancel=self.stop.is_set)
                key = analysis.result_key(result['path'])
                results[key] = analysis.merge_result(self.previous.get(key), result, self.preferences)
            self.result = results
        except Exception as exc:
            self.result = exc


class AnalysisMixin:
    def _analysis_settings_page(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        self._analysis_controls = AnalysisControls(self.settings.get('analysis'), automatic=True)
        self._analysis_controls.changed.connect(self._save_analysis_preferences)
        scroll.setWidget(self._analysis_controls)
        return scroll

    def _save_analysis_preferences(self):
        self.settings['analysis'] = self._analysis_controls.preferences()
        self._schedule_settings_save()

    def _analysis_result(self, track):
        return analysis.saved_result(self.settings.get('analysis_results', {}), track)

    def _save_local_grid(self, track, before, after):
        results = self.settings.get('analysis_results', {})
        key = analysis.result_key(track.path)
        if (self._shutting_down or self._state_maintenance or results.get(key) != before
                or not (track.imported or track.source_native_id) or track.source_analysis_path):
            raise ValueError('Track changed before saving the grid; reload it before editing')
        epoch = analysis.identity(track.path.stat())
        parts = self.player_bar._analysis_parts
        if (epoch != before['epoch']
                and not (self.player_bar.current_track() == track.track_id
                         and parts.get('_detected_sha256') == before['sha256']
                         and parts.get('_detected_epoch') == epoch)):
            raise ValueError('Checking this audio file; try the edit again when it finishes loading')
        self.settings['analysis_results'] = {**results, key: after}
        if not self._save_state():
            self.settings['analysis_results'] = results
            return False
        return True

    def _local_grid_saved(self, track_id):
        track = self.lib.tracks_by_id.get(track_id)
        if track is None:
            return
        record = self._analysis_result(track)
        resolved = local_grid.effective_result(record)
        self._invalidate_rows([track_id])
        # Same in-place BPM/sort/selection handling as verified native saves.
        self._update_browser_bpm(track_id, f"{resolved['bpm']:.2f}")
        if self.player_bar.current_track() == track_id:
            self.player_bar.detected_analysis = record
            self.player_bar.refresh_live_grid(track_id, track.path, resolved['beats'], resolved['bpm'])

    def _local_cue_reason(self, track_id):
        prep=getattr(self,'prep_controller',None)
        if prep and prep.handles(track_id):return prep.reason(track_id)
        if not self.grid_controller.is_local(track_id):
            return None
        if self._shutting_down:
            return 'Finishing library edits…'
        record = self._analysis_result(self.lib.tracks_by_id[track_id])
        return '' if record and record.get('beats') else 'Analyze BPM / Grid before editing cues'

    def _edit_local_cue(self, track, kind, slot, operation, position):
        prep=getattr(self,'prep_controller',None)
        if prep and prep.handles(track.track_id):
            return prep.request_cue(track.track_id,kind,slot,operation,position)
        before = self._analysis_result(track)
        try:
            after = local_cues.edit(before, kind, slot, operation, position)
            if after == before:
                return True
            if not self._save_local_grid(track, before, after):
                return False
        except (OSError, ValueError, RuntimeError) as exc:
            self._flash_status(str(exc), 10)
            return False
        self.player_bar.detected_analysis = after
        self.player_bar.refresh_live_cues(track.track_id, track.path, local_cues.cues(after))
        return True

    def _analyze_tracks(self, track_ids, *, automatic=False):
        if self.lib is None or self._shutting_down or self._state_maintenance:
            return
        tracks = [self.lib.tracks_by_id[tid] for tid in dict.fromkeys(track_ids) if tid in self.lib.tracks_by_id]
        if not tracks: return
        prefs = analysis.options(self.settings.get('analysis'))
        if not (prefs['bpm'] or prefs['key']):
            if not automatic:self._reanalyze_tracks([track.track_id for track in tracks])
            return
        previous=self.settings.get(analysis_jobs.KEY,[])
        try:
            jobs=analysis_jobs.admit(previous,self.lib,tracks,prefs,retry_failed=not automatic,
                                     rebuild_waveform=not automatic)
        except ValueError as exc:
            self._flash_status(str(exc),15);return
        if jobs!=previous:
            self.settings[analysis_jobs.KEY]=jobs
            if not self._save_state():
                self.settings[analysis_jobs.KEY]=previous
                return
        self._resume_analysis_jobs()

    def _resume_analysis_jobs(self):
        if self.lib is None or self._shutting_down or self._state_maintenance:return
        try:jobs=analysis_jobs.validate(self.settings.get(analysis_jobs.KEY,[]))
        except ValueError as exc:
            self._flash_status(str(exc),15);return
        if not jobs:return
        owner=getattr(self,'_analysis_owner',None)
        if owner is None or (owner.stopping and owner.pending==0):
            if owner is not None:owner.deleteLater()
            self._analysis_owner=TaskOwner(self);self._analysis_queue=[];self._analysis_worker=None
        if self._analysis_owner.stopping:return
        pending={identity for _lib,_track,_prefs,identity in self._analysis_queue}
        if self._analysis_worker is not None:pending.add(self._analysis_worker.job_id)
        for job in jobs:
            track=self.lib.tracks_by_id.get(job['track_id'])
            if (job['state']=='queued' and job['id'] not in pending and track is not None
                    and analysis_jobs.matches(job,self.lib,track)):
                self._analysis_queue.append((self.lib,deepcopy(track),dict(job['preferences']),job['id']))
        self._start_next_analysis()

    def _start_next_analysis(self):
        if (self._shutting_down or self._analysis_owner.stopping
                or self._analysis_worker is not None):
            return
        if (self.tag_controller.owned or self._refresh_worker is not None
                or self._device_operation_owned()):
            QTimer.singleShot(500, self, self._start_next_analysis)
            return
        while self._analysis_queue:
            original, track, prefs, identity = self._analysis_queue.pop(0)
            deletion=self.settings.get('graveyard_delete_intent')
            if deletion and not deletion.get('error') and track.track_id in deletion['ids']:continue
            current = self.lib.tracks_by_id.get(track.track_id) if self.lib else None
            job=next((j for j in self.settings.get(analysis_jobs.KEY,[]) if j['id']==identity),None)
            if current is None or job is None or not analysis_jobs.matches(job,self.lib,current):
                continue
            worker = AnalysisWorker(original, [track], prefs,
                                    deepcopy(self.settings.get('analysis_results', {})))
            self._analysis_worker = worker
            worker.job_id=identity
            self._analysis_owner.adopt(worker, self._analysis_retired)
            worker.start()
            return

    def _analysis_retired(self, worker):
        self._analysis_worker = None
        if self._shutting_down or self._analysis_owner.stopping:
            return
        if worker.stop.is_set():
            QTimer.singleShot(0,self,self._start_next_analysis)
            return
        track = worker.tracks[0]
        try:
            if isinstance(worker.result, Exception):
                raise worker.result
            current = self.lib.tracks_by_id.get(track.track_id) if self.lib else None
            acquired_identity=bool(current and not track.source_uuid and current.source_native_id
                                   and not current.source_analysis_path)
            if (self._state_maintenance or current is None or current.path != track.path
                    or current.source_uuid != track.source_uuid and not acquired_identity
                    or self.lib.source != worker.library.source
                    or self.lib.source_library_id != worker.library.source_library_id):
                raise ValueError('Track changed during analysis; analyze it again')
            before = self.settings.get('analysis_results', {})
            pending=self.settings.get(analysis_jobs.KEY,[])
            rebuild=next((j.get('waveform',False) for j in pending if j['id']==worker.job_id),False)
            results = {key: analysis.merge_result(before.get(key), result, worker.preferences)
                       for key, result in worker.result.items()}
            prep=getattr(self,'prep_controller',None)
            prep_before=deepcopy(self.settings.get('pending_prep_edits',[]))
            if prep:
                results={key:prep.attach_result(current,result) for key,result in results.items()}
            retirement=analysis_jobs.finish(pending,worker.job_id)
            self.settings['analysis_results'] = {**before, **results}
            self.settings[analysis_jobs.KEY]=retirement
            if not self._save_state():
                self.settings['analysis_results'] = before
                self.settings[analysis_jobs.KEY]=pending
                self.settings['pending_prep_edits']=prep_before
                raise OSError('Analysis was not saved')
            self._invalidate_rows([track.track_id])
            self._refresh_analysis_row(track.track_id)
            if rebuild and track.track_id not in getattr(self,'hidden',set()):
                self._waveform_force_pending.add(track.track_id)
                self._maybe_fill_waveforms()
            publisher=getattr(self,'native_analysis_controller',None)
            if publisher:publisher.discover()
            if self.player_bar.current_track() == track.track_id:
                record = self._analysis_result(current)
                self.player_bar.detected_analysis = record
                if self.grid_controller.is_local(track.track_id) and record.get('beats'):
                    # Analysis completion changes musical metadata only. Do not
                    # restart waveform preparation or leave a fallback on deck.
                    self._local_grid_saved(track.track_id)
                    self.player_bar.refresh_live_cues(track.track_id, current.path, local_cues.cues(record))
                    if analysis.identity(current.path.stat()) == record['epoch']:
                        self.player_bar._analysis_parts.update(
                            _detected_sha256=record['sha256'], _detected_epoch=record['epoch'])
                    else:
                        self.player_bar.refresh_audio_detail(str(current.path))
                self._sync_grid()
                self._sync_cue_editing()
        except Exception as exc:
            pending=self.settings.get(analysis_jobs.KEY,[])
            try:
                failed=analysis_jobs.failed(pending,worker.job_id,exc)
                if failed!=pending:
                    self.settings[analysis_jobs.KEY]=failed
                    if not self._save_state():self.settings[analysis_jobs.KEY]=pending
            except ValueError:
                pass  # Preserve unknown evidence; never rewrite a corrupt queue.
            self._flash_status(f'Couldn’t analyze {track.name}: {exc}', 15)
        QTimer.singleShot(0, self, self._start_next_analysis)

    def _refresh_analysis_row(self, track_id):
        """Update one finished song without rebuilding the view/selection."""
        model = getattr(self, 'model', None)
        track = self.lib.tracks_by_id.get(track_id)
        if model is None or track is None:
            return
        from PySide6.QtCore import QItemSelectionModel
        row = self._row_for(track)
        selected = set(self._selected_ids())
        reset = model.update_analysis(track_id, bpm=row['bpm'], key=row['key'])
        if reset:
            selection = self.table.selectionModel()
            for index, value in enumerate(model._rows):
                if value.get('id') in selected:
                    selection.select(model.index(index, 0),
                                     QItemSelectionModel.Select | QItemSelectionModel.Rows)
