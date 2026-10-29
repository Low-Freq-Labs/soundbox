"""Analysis controls and cancellable captured-audio jobs with owned lifetime."""
from copy import deepcopy
import threading

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget)

from soundbox import analysis
from .task_owner import TaskOwner


class AnalysisControls(QWidget):
    changed = Signal()

    def __init__(self, preferences=None, *, automatic=False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30 if automatic else 0, 16, 30 if automatic else 0, 16)
        layout.setSpacing(12)
        title = QLabel('TRACK ANALYSIS'); title.setObjectName('SettingsSection'); layout.addWidget(title)
        self.bpm = QCheckBox('BPM / Grid')
        self.key = QCheckBox('Key')
        self.phrase = QCheckBox('Phrase'); self.phrase.setEnabled(False)
        self.phrase.setToolTip('Phrase analysis is not available yet')
        for control in (self.bpm, self.key, self.phrase):
            layout.addWidget(control)
            control.toggled.connect(self.changed)
        row = QHBoxLayout(); row.addWidget(QLabel('BPM range')); row.addStretch()
        self.bpm_range = QComboBox()
        for low, high in analysis.BPM_RANGES:
            self.bpm_range.addItem(f'{low}–{high}')
        row.addWidget(self.bpm_range); layout.addLayout(row)
        self.bpm_range.currentIndexChanged.connect(self.changed)
        self.first_cue = QCheckBox('Set a Memory Cue on the first analyzed beat')
        layout.addWidget(self.first_cue); self.first_cue.toggled.connect(self.changed)
        self.auto = QCheckBox('Analyze new imports automatically')
        self.auto.setVisible(automatic)
        layout.addWidget(self.auto); self.auto.toggled.connect(self.changed)
        row = QHBoxLayout(); row.addWidget(QLabel('Analysis process')); row.addStretch()
        self.process = QComboBox(); self.process.addItems(['Performance','Normal','Power saving'])
        self.process.currentIndexChanged.connect(self.changed)
        row.addWidget(self.process); layout.addLayout(row)
        note = QLabel('Existing saved grids and cues are preserved. Detection does not rebuild waveforms.')
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


class AnalysisDialog(QDialog):
    def __init__(self, library, tracks, preferences, previous, save, *, autostart=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Analyze Track' if len(tracks) == 1 else f'Analyze {len(tracks)} Tracks')
        self.resize(520, 410)
        self.setWindowModality(Qt.WindowModal)
        self.owner = TaskOwner(self)
        self.worker = None
        self.automatic = autostart
        self.library, self.tracks, self.previous, self.save = library, tracks, previous, save
        layout = QVBoxLayout(self)
        self.controls = AnalysisControls(preferences); layout.addWidget(self.controls)
        self.status = QLabel(tracks[0].name if len(tracks) == 1 else f'{len(tracks)} tracks')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.hide(); layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self.reject)
        self.start = self.buttons.addButton('Analyze', QDialogButtonBox.ActionRole)
        self.start.clicked.connect(self.begin); layout.addWidget(self.buttons)
        if autostart: QTimer.singleShot(0, self, self.begin)

    def begin(self):
        if self.owner.stopping or self.worker is not None: return
        prefs = analysis.options(self.controls.preferences())
        if not (prefs['bpm'] or prefs['key']):
            self.status.setText('Select BPM / Grid or Key'); return
        self.controls.setEnabled(False); self.start.setEnabled(False)
        self.progress.setRange(0, 0); self.progress.show()
        self.worker = AnalysisWorker(self.library, self.tracks, prefs, self.previous)
        self.worker.progress.connect(lambda done, total, name: self.status.setText(f'{done + 1} / {total} · {name}'))
        self.owner.adopt(self.worker, self._retired); self.worker.start()

    def _retired(self, worker):
        self.worker = None
        if self.owner.stopping: return
        self.progress.hide()
        if worker.stop.is_set(): return
        if isinstance(worker.result, Exception):
            self.status.setText(str(worker.result)); self.controls.setEnabled(True); self.start.setEnabled(True)
            return
        try:
            if not self.save(worker.result):
                self.status.setText('Analysis was not saved'); return
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc)); return
        self.previous.update(worker.result)
        values = list(worker.result.values())
        details = []
        if len(values) == 1:
            if values[0].get('bpm'):
                details.append(f"BPM {values[0]['bpm']:.2f}")
            if values[0].get('key'):
                details.append('Key '+values[0]['key'])
        self.status.setText('Saved in SoundBox' + (' · '+' · '.join(details) if details else ''))
        self.buttons.button(QDialogButtonBox.Cancel).setText('Close')
        self.controls.setEnabled(True); self.start.setEnabled(True); self.start.setText('Analyze again')
        if self.automatic:
            QTimer.singleShot(0, self, self.accept)

    def done(self, result):
        self.owner.drain()
        super().done(result)


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

    def _analyze_tracks(self, track_ids, *, automatic=False):
        if (self.lib is None or self._shutting_down or self._state_maintenance
                or self.tag_controller.owned or self._refresh_worker is not None or self._device_operation_owned()):
            self._flash_status('Wait for the current library or device operation'); return
        if getattr(self, '_analysis_dialog', None) is not None: return
        tracks = [self.lib.tracks_by_id[tid] for tid in dict.fromkeys(track_ids) if tid in self.lib.tracks_by_id]
        if not tracks: return
        original = self.lib
        def save(results):
            if self._shutting_down or self._state_maintenance or self.lib is not original:
                raise ValueError('Library changed during analysis; analyze again after refreshing')
            before = self.settings.get('analysis_results', {})
            self.settings['analysis_results'] = {**before, **results}
            if not self._save_state():
                self.settings['analysis_results'] = before
                return False
            self._invalidate_rows([t.track_id for t in tracks]); self._reload_current_view()
            loaded = self.player_bar.current_track()
            if loaded in {t.track_id for t in tracks}:
                self.player_bar.detected_analysis = self._analysis_result(original.tracks_by_id[loaded])
                self.player_bar.refresh_audio_detail(str(original.tracks_by_id[loaded].path))
            self._flash_status('Analysis saved in SoundBox')
            return True
        dialog = AnalysisDialog(deepcopy(self.lib), deepcopy(tracks), self.settings.get('analysis'),
                                deepcopy(self.settings.get('analysis_results', {})), save,
                                autostart=automatic, parent=self)
        self._analysis_dialog = dialog
        dialog.finished.connect(lambda _result: self._analysis_dialog_closed(dialog))
        dialog.open()

    def _analysis_dialog_closed(self, dialog):
        if getattr(self, '_analysis_dialog', None) is dialog:
            self._analysis_dialog = None
        dialog.deleteLater()
