"""Actual window import/analysis flow, with controlled detector completion."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QMimeData, QPointF, QUrl, Qt
from PySide6.QtGui import QDropEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QTreeWidgetItem

from soundbox import analysis, analysis_jobs, imports, local_cues, local_grid, shared_imports
from soundbox.library import Library, Track, Playlist
from soundbox.source_verification import SourceVerificationCache
from soundbox.state_repository import StateRepository


class ImportAnalysisFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        with mock.patch('soundbox.rekordbox_db.db_available', return_value=False), \
                mock.patch.object(MainWindow, '_refresh_devices'):
            self.win = MainWindow(isolated_dir=self.root / 'state')
        self.win.lib = Library({}, [], 'synthetic')
        self.win.tag_controller.configure(self.win.lib, False)
        self.win.grid_controller.configure()
        self.win._view_key = 'sb:Collection'
        self.app.processEvents()
        # No artwork/codec preparation belongs to this detector/UI fixture.
        for name in ('_maybe_fill_waveforms', '_maybe_autostart_art_fill', '_maybe_prepare_stock_artwork'):
            patcher = mock.patch.object(self.win, name)
            patcher.start(); self.addCleanup(patcher.stop)
        gate=mock.patch.object(MainWindow,'_analysis_prerequisite_ready',return_value=True,create=True)
        gate.start();self.addCleanup(gate.stop)
        self.win._waveform_rechecks_enabled = False
        self.win._waveform_recheck_timer.stop()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.win.shutdown(); self.win.close(); self.win.deleteLater(); self.app.processEvents()

    def wait(self, predicate):
        end = time.monotonic() + 5
        while not predicate() and time.monotonic() < end:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def track(self, name):
        path = self.root / (name + '.wav'); path.write_bytes(b'captured fixture ' + name.encode())
        track = Track('sb-' + name, name, '', path.as_uri(), imported=True)
        self.win.lib.tracks_by_id[track.track_id] = track
        return track

    def result(self, track, bpm=120):
        return dict(engine=analysis.ENGINE, path=str(track.path), epoch=analysis.identity(track.path.stat()),
            size=track.path.stat().st_size, sha256=hashlib.sha256(track.path.read_bytes()).hexdigest(),
            duration=10, bpm=bpm, key='8A', first_cue=None,
            beats=[[(i % 4) + 1, i * 500, bpm] for i in range(21)])

    def load(self, track, record):
        pb = self.win.player_bar
        pb._track_id = track.track_id; pb._src_path = str(track.path)
        pb._pending_autoplay = False; pb._native_error = ''; pb._dur = 10000
        pb.cue_panel._loaded = True
        pb._analysis_parts = analysis.playback_data({}, record, track.path)
        return pb

    def completed_analysis(self, track, record):
        prefs=analysis.options(dict(bpm=True,key=False,first_cue=False))
        self.win.settings[analysis_jobs.KEY]=analysis_jobs.admit([],self.win.lib,[track],prefs)
        self.assertTrue(self.win._save_state())
        with mock.patch.object(self.win,'_start_next_analysis'):
            self.win._resume_analysis_jobs()
        job=self.win.settings[analysis_jobs.KEY][0]
        self.win._analysis_queue=[entry for entry in self.win._analysis_queue if entry[3]!=job['id']]
        worker=SimpleNamespace(library=self.win.lib,tracks=[track],preferences=prefs,
            result={analysis.result_key(track.path):record},job_id=job['id'],stop=threading.Event())
        self.win._analysis_worker=worker
        return worker

    def waveform_projection(self, track, bpm=140):
        from desktop.workers import WaveformFillWorker
        worker = WaveformFillWorker(self.win.lib, self.win._track_analysis_dir)
        self.win._wf_fill_worker = worker
        self.addCleanup(lambda: setattr(self.win, '_wf_fill_worker', None))
        self.addCleanup(worker.deleteLater)
        value = dict(track_id=track.track_id, path=str(track.path), track_size=track.size,
            source=str(self.win.lib.source), source_kind=self.win.lib.source_kind,
            library_id=self.win.lib.source_library_id, native_id=track.native_id,
            uuid=track.source_uuid, bpm=bpm, provenance='soundbox-zero-anchor',
            size=track.path.stat().st_size, epoch=analysis.identity(track.path.stat()))
        return worker, value

    def test_background_waveform_estimate_fills_key_only_row_without_loading_or_saving(self):
        track = self.track('inconclusive')
        record = {**self.result(track), 'bpm': None, 'beats': [], 'confidence': .18}
        self.win.settings['analysis_results'] = {analysis.result_key(track.path): record}
        self.win.model.set_rows([self.win._row_for(track)])
        before = deepcopy(self.win.settings)
        worker, value = self.waveform_projection(track)
        with mock.patch.object(self.win.player_bar, 'play_track') as load, \
                mock.patch.object(self.win, '_save_state') as save, \
                mock.patch.object(Path, 'stat', side_effect=AssertionError('GUI must not stat audio')):
            self.win._on_waveform_bpm_ready(worker, value)
        self.assertEqual(self.win.model._rows[0]['bpm'], '140.00')
        self.assertEqual(self.win.model._rows[0]['key'], '8A')
        self.assertTrue(self.win.model._rows[0]['bpm_estimated'])
        self.assertEqual(self.win._row_for(track)['bpm'], '140.00')
        self.assertEqual(self.win.settings, before)
        load.assert_not_called(); save.assert_not_called()
        # A later stale/missing cache removes the disposable estimate.
        self.win._on_waveform_bpm_ready(worker, {**value, 'bpm': None})
        self.assertFalse(self.win.model._rows[0]['bpm'])

        self.assertFalse(self.win.model._rows[0]['bpm_estimated'])

    def test_waveform_estimate_rejects_old_worker_binding_and_invalid_tempos(self):
        track = self.track('estimate-identity')
        self.win.model.set_rows([self.win._row_for(track)])
        worker, value = self.waveform_projection(track)
        for field, changed in (('path', '/different.wav'), ('track_size', 999),
                               ('uuid', 'different'), ('source', 'different'),
                               ('bpm', float('nan')), ('bpm', -1),
                               ('size', 999), ('epoch', None),
                               ('provenance', 'rekordbox')):
            with self.subTest(field=field, changed=changed):
                self.win._on_waveform_bpm_ready(worker, {**value, field: changed})
                self.assertFalse(self.win.model._rows[0]['bpm'])
        worker.lib = Library({}, [], 'different')
        self.win._on_waveform_bpm_ready(worker, value)
        self.assertFalse(self.win.model._rows[0]['bpm'])

    def test_loaded_waveform_fallback_retains_estimate_marker_and_a_miss_clears_it(self):
        track = self.track('loaded-estimate')
        record = {**self.result(track), 'bpm': None, 'beats': []}
        self.win.settings['analysis_results'] = {analysis.result_key(track.path): record}
        self.win.model.set_rows([self.win._row_for(track)])
        worker, value = self.waveform_projection(track)
        self.win._on_waveform_bpm_ready(worker, value)
        pb = self.load(track, record)
        pb._analysis_parts = dict(beats=[[1, 0, 140], [2, 429, 140]],
                                  provenance=dict(grid='soundbox-zero-anchor'))
        with mock.patch.object(pb, 'refresh_live_grid'), mock.patch.object(pb, 'refresh_live_cues'):
            self.win.prep_controller.refresh(track.track_id)
        self.assertTrue(self.win.model._rows[0]['bpm_estimated'])
        # Repair an old deck-only projection even if the stored value is unchanged.
        self.win.model.update_bpm(track.track_id, '140.00')
        self.assertFalse(self.win.model._rows[0]['bpm_estimated'])
        self.win._on_waveform_bpm_ready(worker, value)
        self.assertTrue(self.win.model._rows[0]['bpm_estimated'])
        self.win._on_waveform_bpm_ready(worker, {**value, 'bpm': None})
        self.assertFalse(self.win.model._rows[0]['bpm'])

        # A native BPM is not labelled an estimate even if its cache has a fallback.
        track.bpm = '140.00'
        with mock.patch.object(pb, 'refresh_live_grid'), mock.patch.object(pb, 'refresh_live_cues'):
            self.win.prep_controller.refresh(track.track_id)
        self.assertFalse(self.win.model._rows[0]['bpm_estimated'])

    def test_native_detector_and_manual_bpm_take_priority_over_waveform_estimate(self):
        track = self.track('estimate-priority')
        worker, value = self.waveform_projection(track)
        self.win._on_waveform_bpm_ready(worker, value)
        for native_bpm, record in (('155', None), ('', self.result(track, 120)),
                ('', {**self.result(track, 120), 'bpm': None, 'beats': [],
                    'grid_draft': dict(origin='manual', revision=1,
                        base_sha256=self.result(track)['sha256'],
                        bpm=130, beats=[[1, 0, 130], [2, 462, 130]])})):
            with self.subTest(native=native_bpm, record=bool(record)):
                current = replace(track, bpm=native_bpm)
                self.win.lib.tracks_by_id[track.track_id] = current
                self.win.settings['analysis_results'] = {analysis.result_key(track.path): record} if record else {}
                self.win._invalidate_rows([track.track_id])
                row = self.win._row_for(current)
                self.assertEqual(row['bpm'], {'155': '155'}.get(native_bpm,
                    '130.00' if record and record.get('grid_draft') else '120.00'))
                self.assertFalse(row['bpm_estimated'])
        # A refreshed Library must rebuild disposable estimates off-thread.
        self.win.lib = Library({track.track_id: track}, [], 'synthetic')
        self.win.settings['analysis_results'] = {}
        self.win._invalidate_rows()
        self.assertFalse(self.win._row_for(track)['bpm'])

    def test_waveform_estimate_updates_sorted_filtered_rows_and_preserves_selection(self):
        from PySide6.QtCore import QItemSelectionModel
        from desktop.model import COLUMNS
        first, second = self.track('estimate-first'), self.track('estimate-second')
        second.bpm = '150'
        self.win.model.set_rows([self.win._row_for(t) for t in (first, second)])
        self.win.model.sort(next(i for i, col in enumerate(COLUMNS) if col[0] == 'bpm'), Qt.DescendingOrder)
        self.win.table.selectionModel().select(self.win.model.index(0, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows)
        selected = self.win._selected_ids()
        worker, value = self.waveform_projection(first, 160)
        self.win._on_waveform_bpm_ready(worker, value)
        self.assertEqual(self.win.model._rows[0]['id'], first.track_id)
        self.assertEqual(self.win._selected_ids(), selected)
        self.win.model.set_filter('140')
        self.assertEqual(self.win.model.rowCount(), 0)
        self.win._on_waveform_bpm_ready(worker, {**value, 'bpm': 140})
        self.assertEqual([row['id'] for row in self.win.model._rows], [first.track_id])

    def test_held_grid_shortcut_repeats_durable_edits_and_stops_on_release_or_new_track(self):
        first=self.track('held-grid');record=self.result(first)
        key=analysis.result_key(first.path);self.win.settings['analysis_results']={key:record}
        pb=self.load(first,record);self.win.grid_controller.configure()
        router=self.win._keyboard_router;router.keymap.assign('grid.right','F3');router.keymap.assign('grid.right','F4')
        with mock.patch.object(QApplication,'focusWidget',return_value=self.win.table), \
                mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            router.handle(QKeyEvent(QEvent.KeyPress,Qt.Key_F3,Qt.NoModifier),True)
            router.handle(QKeyEvent(QEvent.KeyPress,Qt.Key_F4,Qt.NoModifier),True)
            start=local_grid.effective_result(self.win.settings['analysis_results'][key])['beats'][0][1]
            QTest.qWait(500)
            repeated=local_grid.effective_result(self.win.settings['analysis_results'][key])['beats'][0][1]
            self.assertGreater(repeated,start)
            router.handle(QKeyEvent(QEvent.KeyRelease,Qt.Key_F3,Qt.NoModifier),False)
            QTest.qWait(110)
            self.assertGreater(local_grid.effective_result(self.win.settings['analysis_results'][key])['beats'][0][1],repeated)
            router.handle(QKeyEvent(QEvent.KeyRelease,Qt.Key_F4,Qt.NoModifier),False)
            stopped=deepcopy(self.win.settings['analysis_results'][key]);QTest.qWait(150)
            self.assertEqual(self.win.settings['analysis_results'][key],stopped)
            router.handle(QKeyEvent(QEvent.KeyPress,Qt.Key_F3,Qt.NoModifier),True)
            second=self.track('different');self.load(second,self.result(second))
            captured=deepcopy(self.win.settings['analysis_results'][key]);QTest.qWait(400)
            self.assertEqual(self.win.settings['analysis_results'][key],captured)
            self.assertNotIn(analysis.result_key(second.path),self.win.settings['analysis_results'])
            router.release_all()

    def test_held_grid_shortcut_stops_when_typing_or_a_dialog_takes_focus(self):
        router=self.win._keyboard_router;router.keymap.assign('grid.left','F3')
        editor=QLineEdit(self.win)
        with mock.patch.object(self.win,'_edit_grid',return_value=True) as edit, \
                mock.patch.object(QApplication,'focusWidget',return_value=editor):
            router.handle(QKeyEvent(QEvent.KeyPress,Qt.Key_F3,Qt.NoModifier),True)
            QTest.qWait(450);self.assertEqual(edit.call_count,1)
            router.release_all()

    def test_background_batch_publishes_each_track_then_accepts_more_without_windows(self):
        first, second, third = [self.track(n) for n in ('first','second','third')]
        release = threading.Event(); self.addCleanup(release.set)
        self.win.model.set_rows([self.win._row_for(t) for t in (first,second,third)])
        self.win.settings['analysis'] = dict(automatic=True)
        def detect(_lib, track, _prefs, *, should_cancel,automatic=False,**_kwargs):
            if track == second:
                while not release.wait(.005):
                    if should_cancel(): raise RuntimeError('cancelled')
            return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze', side_effect=detect), \
                mock.patch.object(QDialog, 'open') as popup, \
                mock.patch.object(self.win, '_flash_status') as status:
            self.win._analyze_tracks([first.track_id, second.track_id], automatic=True)
            key = analysis.result_key(first.path)
            self.wait(lambda: key in self.win.settings.get('analysis_results', {})
                      and not any(j['track_id']==first.track_id for j in self.win.settings[analysis_jobs.KEY]))
            self.assertNotIn(analysis.result_key(second.path), self.win.settings['analysis_results'])
            self.assertEqual(self.win.model._rows[0]['bpm'], '120.00')
            self.assertEqual(self.win.model._rows[0]['key'], '8A')
            self.assertEqual(self.win.model._all_rows[0]['key'], '8A')
            self.assertEqual(StateRepository(self.win._data_file).load()['settings']['analysis_results'][key]['key'], '8A')
            # Edit the completed first song while the second is still analyzing.
            record = self.win.settings['analysis_results'][key]; pb = self.load(first, record)
            with mock.patch.object(pb.player, 'set_grid'), mock.patch.object(pb, '_update_transport'):
                self.assertTrue(self.win._edit_grid('shift', 1))
            self.win._analyze_tracks([second.track_id, third.track_id])
            self.assertEqual(len(self.win._analysis_queue), 1)
            release.set()
            self.wait(lambda: len(self.win.settings['analysis_results']) == 3 and self.win._analysis_owner.pending == 0)
            self.assertEqual(local_grid.effective_result(self.win.settings['analysis_results'][key])['beats'][0][1], 1)
            popup.assert_not_called(); status.assert_not_called()

    def test_bpm_is_durable_before_key_and_key_merge_preserves_newer_manual_grid(self):
        track=self.track('staged');key=analysis.result_key(track.path)
        release=threading.Event();self.addCleanup(release.set)
        calls=[]
        def detect(_lib,current,prefs,*,should_cancel,**kwargs):
            calls.append((dict(prefs),kwargs.get('expected_sha256')))
            record=self.result(current)
            if prefs['bpm']:
                record['key']=None
            else:
                while not release.wait(.005):
                    if should_cancel():raise RuntimeError('cancelled')
                record.update(bpm=None,beats=[],key='9A',first_cue=None)
            return record
        self.win.model.set_rows([self.win._row_for(track)])
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect), \
                mock.patch.object(self.win.native_analysis_controller,'discover') as publish:
            self.win._analyze_tracks([track.track_id])
            self.wait(lambda:len(calls)==2)
            disk=StateRepository(self.win._data_file).load()['settings']
            self.assertEqual(disk['analysis_results'][key]['bpm'],120)
            self.assertIsNone(disk['analysis_results'][key]['key'])
            self.assertEqual(disk[analysis_jobs.KEY][0]['phase'],'key')
            self.assertTrue(disk[analysis_jobs.KEY][0]['preferences']['bpm'])
            publish.assert_not_called()
            edited=local_grid.edit(self.win.settings['analysis_results'][key],'shift',1)
            self.win.settings['analysis_results'][key]=edited
            self.assertTrue(self.win._save_state())
            release.set()
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
            result=self.win.settings['analysis_results'][key]
            self.assertEqual(result['key'],'9A')
            self.assertEqual(result['grid_draft'],edited['grid_draft'])
            self.assertEqual(result['bpm'],120)
            publish.assert_called_once()
        self.assertEqual([(p['bpm'],p['key']) for p,_sha in calls],[(True,False),(False,True)])
        self.assertEqual(calls[1][1],self.result(track)['sha256'])

    def test_key_failure_retains_bpm_advances_other_tracks_and_retry_resumes_only_key(self):
        first,second=self.track('key-fails'),self.track('key-succeeds')
        calls=[]
        def detect(_lib,track,prefs,**_kwargs):
            calls.append((track.track_id,prefs['bpm'],prefs['key']))
            if track.track_id==first.track_id and prefs['key']:raise ValueError('Key unavailable')
            record=self.result(track)
            if prefs['bpm']:record['key']=None
            else:record.update(bpm=None,beats=[])
            return record
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect), \
                mock.patch.object(self.win,'_flash_status'):
            self.win._analyze_tracks([first.track_id,second.track_id])
            self.wait(lambda:self.win._analysis_owner.pending==0 and len(calls)==4)
        self.assertEqual(calls,[(first.track_id,True,False),(first.track_id,False,True),
                                (second.track_id,True,False),(second.track_id,False,True)])
        failed=deepcopy(self.win.settings[analysis_jobs.KEY][0])
        self.assertEqual((failed['state'],failed['phase']),('failed','key'))
        self.assertEqual(self.win._analysis_result(first)['bpm'],120)
        resumed=[]
        def key_only(_lib,track,prefs,**_kwargs):
            resumed.append(dict(prefs));return {**self.result(track),'bpm':None,'beats':[]}
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=key_only):
            self.win._analyze_tracks([first.track_id])
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
        self.assertEqual(len(resumed),1)
        self.assertFalse(resumed[0]['bpm']);self.assertTrue(resumed[0]['key'])
        self.assertEqual(self.win._analysis_result(first)['bpm'],120)

    def test_inconclusive_bpm_advances_key_but_changed_audio_cannot_merge_components(self):
        track=self.track('staged-identity');calls=[]
        def detect(_lib,current,prefs,**_kwargs):
            calls.append((prefs['bpm'],prefs['key']))
            record=self.result(current)
            record.update(bpm=None,beats=[],confidence=.1,key=None)
            if prefs['key']:record.update(sha256='a'*64,key='9A')
            return record
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect), \
                mock.patch.object(self.win,'_flash_status') as status:
            self.win._analyze_tracks([track.track_id])
            self.wait(lambda:self.win.settings[analysis_jobs.KEY][0]['state']=='failed')
        self.assertEqual(calls,[(True,False),(False,True)])
        record=self.win._analysis_result(track)
        self.assertIsNone(record['bpm']);self.assertIsNone(record['key'])
        self.assertEqual(record['sha256'],self.result(track)['sha256'])
        self.assertIn('identity changed',status.call_args.args[0])

    def test_prerequisite_wait_keeps_the_durable_head_and_does_not_start_detection(self):
        track=self.track('waiting-wave')
        with mock.patch.object(self.win,'_analysis_prerequisite_ready',return_value=False,create=True), \
                mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._analyze_tracks([track.track_id])
            self.assertEqual(len(self.win._analysis_queue),1)
            self.assertEqual(len(self.win.settings[analysis_jobs.KEY]),1)
            self.assertIsNone(self.win._analysis_worker)
            detect.assert_not_called()

    def test_restart_after_bpm_checkpoint_runs_only_key_with_original_preferences(self):
        from desktop.app import MainWindow
        track=self.track('restart-key');record=self.result(track);record['key']=None
        queued=analysis_jobs.admit([],self.win.lib,[track],dict(bpm=True,key=True,first_cue=True))
        self.win.settings[analysis_jobs.KEY]=analysis_jobs.advance(queued,queued[0]['id'],record['sha256'])
        self.win.settings['analysis_results']={analysis.result_key(track.path):record}
        self.assertTrue(self.win._save_state())
        self.win.shutdown();self.win.close();self.win.deleteLater();self.app.processEvents()
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False), \
                mock.patch.object(MainWindow,'_refresh_devices'):
            self.win=MainWindow(isolated_dir=self.root/'state')
        self.win.lib=Library({track.track_id:deepcopy(track)},[],'synthetic')
        self.win.settings['analysis']=dict(bpm=True,key=False)
        calls=[]
        def key_only(_lib,current,prefs,**kwargs):
            calls.append((dict(prefs),kwargs['expected_sha256']))
            return {**self.result(current),'bpm':None,'beats':[],'key':'9A'}
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=key_only):
            self.win._resume_analysis_jobs()
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
        self.assertEqual(len(calls),1)
        self.assertFalse(calls[0][0]['bpm']);self.assertTrue(calls[0][0]['key'])
        self.assertEqual(calls[0][1],record['sha256'])
        self.assertEqual((self.win._analysis_result(track)['bpm'],self.win._analysis_result(track)['key']),(120,'9A'))

    def test_changed_queued_identity_is_reported_and_preserves_the_original_intention(self):
        track=self.track('identity-before-start')
        with mock.patch.object(self.win,'_analysis_prerequisite_ready',return_value=False), \
                mock.patch('desktop.analysis.analysis.analyze') as detect, \
                mock.patch.object(self.win,'_flash_status') as status:
            self.win._analyze_tracks([track.track_id])
            original=deepcopy(self.win.settings[analysis_jobs.KEY][0])
            self.win.lib.tracks_by_id[track.track_id]=replace(track,source_uuid='changed')
            self.win._start_next_analysis()
        job=self.win.settings[analysis_jobs.KEY][0]
        self.assertEqual((job['id'],job['path'],job['source_uuid']),
                         (original['id'],original['path'],original['source_uuid']))
        self.assertEqual(job['state'],'failed')
        self.assertIn('identity changed',job['error'])
        self.assertIn('identity changed',status.call_args.args[0])
        self.assertEqual(self.win._analysis_queue,[])
        detect.assert_not_called()

    def test_checkpointed_waveform_is_not_rebuilt_again_after_key_completion(self):
        track=self.track('one-waveform');record=self.result(track);record['key']=None
        queued=analysis_jobs.admit([],self.win.lib,[track],{},rebuild_waveform=True)
        queued=analysis_jobs.wave_ready(queued,queued[0]['id'],record['sha256'],record['epoch'])
        self.win.settings[analysis_jobs.KEY]=analysis_jobs.advance(queued,queued[0]['id'],record['sha256'])
        self.win.settings['analysis_results']={analysis.result_key(track.path):record}
        with mock.patch('desktop.analysis.analysis.analyze',return_value={**record,'key':'9A'}), \
                mock.patch.object(self.win,'_maybe_fill_waveforms') as rebuild:
            self.win._resume_analysis_jobs()
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
        self.assertNotIn(track.track_id,self.win._waveform_force_pending)
        rebuild.assert_not_called()

    def test_retargeted_conversion_uses_current_path_library_and_waveform_identity(self):
        from desktop.task_owner import TaskOwner
        track=self.track('before-conversion')
        old_path=self.root/'original.flac';old_path.write_bytes(track.path.read_bytes())
        track=replace(track,location_uri=old_path.as_uri())
        self.win.lib.tracks_by_id[track.track_id]=track
        original_library=self.win.lib
        queued=analysis_jobs.admit([],original_library,[track],dict(bpm=True,key=False))
        self.win._analysis_owner=TaskOwner(self.win);self.win._analysis_worker=None
        self.win._analysis_queue=[(original_library,deepcopy(track),dict(queued[0]['preferences']),queued[0]['id'])]
        converted=self.root/'compatible.wav';converted.write_bytes(b'captured converted fixture bytes')
        current=replace(track,location_uri=converted.as_uri(),size=converted.stat().st_size)
        self.win.lib=Library({current.track_id:current},[],'synthetic')
        queued[0]['path']=str(converted)
        record=self.result(current)
        queued=analysis_jobs.wave_ready(queued,queued[0]['id'],record['sha256'],record['epoch'])
        self.win.settings[analysis_jobs.KEY]=queued
        observed=[]
        def detect(library,captured,prefs,**kwargs):
            observed.append((library,captured.path,kwargs['expected_sha256']))
            return self.result(captured)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect), \
                mock.patch.object(self.win,'_flash_status') as status:
            self.win._start_next_analysis()
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
        self.assertEqual(observed,[(self.win.lib,converted,record['sha256'])])
        self.assertEqual(self.win._analysis_result(current)['path'],str(converted))
        status.assert_not_called()

    def test_ready_waveform_only_intent_finishes_without_a_detector_or_musical_result(self):
        track=self.track('ready-wave-only')
        self.win.settings[analysis_jobs.KEY]=analysis_jobs.admit([],self.win.lib,[track],dict(bpm=False,key=False))
        with mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._resume_analysis_jobs()
        self.assertEqual(self.win.settings[analysis_jobs.KEY],[])
        self.assertEqual(self.win._analysis_queue,[])
        self.assertNotIn(analysis.result_key(track.path),self.win.settings.get('analysis_results',{}))
        detect.assert_not_called()
        self.assertEqual(StateRepository(self.win._data_file).load()['settings'][analysis_jobs.KEY],[])

    def test_ready_waveform_only_intent_retains_durable_and_ram_identity_when_save_fails(self):
        track=self.track('ready-wave-save-failure')
        pending=analysis_jobs.admit([],self.win.lib,[track],dict(bpm=False,key=False))
        self.win.settings[analysis_jobs.KEY]=deepcopy(pending)
        with mock.patch.object(self.win,'_save_state',return_value=False), \
                mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._resume_analysis_jobs()
        self.assertEqual(self.win.settings[analysis_jobs.KEY],pending)
        self.assertEqual(self.win._analysis_queue[0][3],pending[0]['id'])
        self.assertIsNone(self.win._analysis_worker)
        detect.assert_not_called()

    def test_one_failure_does_not_discard_finished_song_or_block_next_song(self):
        tracks = [self.track(n) for n in ('first','bad','last')]
        def detect(_lib, track, _prefs, **_kwargs):
            if track == tracks[1]: raise ValueError('Unreadable audio')
            return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze', side_effect=detect), \
                mock.patch.object(self.win, '_flash_status') as status:
            self.win._analyze_tracks([t.track_id for t in tracks])
            self.wait(lambda: analysis.result_key(tracks[2].path) in self.win.settings.get('analysis_results', {}))
        self.assertEqual(len(self.win.settings['analysis_results']), 2)
        self.assertIn('Unreadable audio', status.call_args.args[0])

    def test_manual_analysis_rebuilds_only_finished_waveforms_and_allows_waveform_only_preferences(self):
        first,bad=[self.track(n) for n in ('first','bad')]
        def detect(_lib,track,_preferences,**_kwargs):
            if track.track_id==bad.track_id:raise ValueError('Unreadable audio')
            return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect):
            self.win._analyze_tracks([first.track_id,bad.track_id])
            self.wait(lambda:self.win.settings[analysis_jobs.KEY][-1]['state']=='failed')
        self.assertIn(first.track_id,self.win._waveform_force_pending)
        self.assertNotIn(bad.track_id,self.win._waveform_force_pending)
        self.win.settings['analysis']=dict(bpm=False,key=False)
        with mock.patch.object(self.win,'_reanalyze_tracks') as waveform, \
                mock.patch('desktop.analysis.analysis.analyze') as detector:
            self.win._analyze_tracks([first.track_id]);waveform.assert_called_once_with([first.track_id])
            detector.assert_not_called()

    def test_restart_resumes_only_unfinished_jobs_with_the_original_preferences(self):
        from desktop.app import MainWindow
        first,second=[self.track(n) for n in ('finished','unfinished')]
        release=threading.Event();self.addCleanup(release.set)
        self.win.settings['analysis']=dict(bpm=True,key=False)
        def detect(_library,track,_preferences,*,should_cancel):
            if track.track_id==second.track_id:
                while not release.wait(.005):
                    if should_cancel():raise RuntimeError('cancelled')
            return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detect):
            self.win._analyze_tracks([first.track_id,second.track_id])
            self.wait(lambda:analysis.result_key(first.path) in self.win.settings.get('analysis_results',{}))
            self.win.shutdown();self.win.close();self.win.deleteLater();self.app.processEvents()
        disk=StateRepository(self.root/'state/library.json').load()['settings']
        self.assertEqual([j['track_id'] for j in disk[analysis_jobs.KEY]],[second.track_id])
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False),mock.patch.object(MainWindow,'_refresh_devices'):
            self.win=MainWindow(isolated_dir=self.root/'state')
        self.win.lib=Library({t.track_id:deepcopy(t) for t in (first,second)},[],'synthetic')
        self.win.settings['analysis']=dict(bpm=False,key=True)
        calls=[]
        def resumed(_library,track,preferences,**_kwargs):
            calls.append((track.track_id,preferences));return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=resumed):
            self.win._resume_analysis_jobs()
            self.wait(lambda:not self.win.settings[analysis_jobs.KEY])
        self.assertEqual([tid for tid,_prefs in calls],[second.track_id])
        self.assertTrue(calls[0][1]['bpm']);self.assertFalse(calls[0][1]['key'])
        self.assertEqual(StateRepository(self.win._data_file).load()['settings'][analysis_jobs.KEY],[])

    def test_failed_admission_never_starts_a_detector_and_foreign_source_intents_stay_pending(self):
        track=self.track('queued')
        with mock.patch.object(self.win,'_save_state',return_value=False),mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._analyze_tracks([track.track_id]);self.app.processEvents();detect.assert_not_called()
        self.assertFalse(self.win.settings.get(analysis_jobs.KEY))
        with mock.patch.object(self.win,'_device_operation_owned',return_value=True):
            self.win._analyze_tracks([track.track_id])
        pending=deepcopy(self.win.settings[analysis_jobs.KEY]);self.win.lib.source='foreign'
        with mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._resume_analysis_jobs();self.app.processEvents();detect.assert_not_called()
        self.assertEqual(self.win.settings[analysis_jobs.KEY],pending)

    def test_existing_song_fallback_does_not_veto_edits_and_cues_are_durable(self):
        track = self.track('existing'); record = self.result(track)
        key = analysis.result_key(track.path)
        self.win.settings['analysis_results'] = {key:record}
        pb = self.load(track, record)
        pb._analysis_parts['beats'] = [[1,0,119.85]]  # stale fallback delivery
        self.win.model.set_rows([self.win._row_for(track)])
        with mock.patch.object(pb.player, 'set_grid'), mock.patch.object(pb, '_update_transport'), \
                mock.patch.object(pb.player, 'set_cues'), \
                mock.patch.object(pb, 'capture_cue_position', return_value=1234), \
                mock.patch.object(pb, 'play_track') as reload_audio:
            self.assertTrue(self.win._edit_grid('shift',1))  # last row at EOF clips
            self.assertTrue(self.win._edit_grid('bpm',12100))
            self.assertTrue(self.win._edit_cue('hot',0,'add'))
            self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],1234)
            self.wait(lambda:not self.win.prep_controller.admission_pending)
            self.assertFalse(self.win.prep_controller.admission_error)
            saved = deepcopy(self.win.settings['analysis_results'])
            with mock.patch.object(self.win._state_repository, 'commit_patch', side_effect=OSError('fixture disk full')):
                self.assertTrue(self.win._edit_cue('hot',0,'delete'))
                self.assertIsNone(pb.cue_panel._cues['hot'][0])
                self.wait(lambda:not self.win.prep_controller.admission_pending)
            self.assertIn('fixture disk full',self.win.prep_controller.admission_error)
            self.assertEqual(self.win.settings['analysis_results'],saved)
            self.assertIsNone(pb.cue_panel._cues['hot'][0])
            reload_audio.assert_not_called()
        restarted = StateRepository(self.win._data_file).load()['settings']['analysis_results'][key]
        self.assertEqual(local_grid.effective_result(restarted)['bpm'],121)
        self.assertEqual(local_cues.cues(restarted)[0]['position_ms'],1234)
        late = analysis.merge_result(restarted,record,dict(bpm=True,key=True))
        self.assertEqual(local_cues.cues(late),local_cues.cues(restarted))
        self.assertEqual(analysis.playback_data({},late,track.path)['cues'],local_cues.cues(restarted))
        self.win.prep_controller.retry()
        self.wait(lambda:not self.win.prep_controller.admission_pending)
        self.assertFalse(self.win.prep_controller.admission_error)
        final=StateRepository(self.win._data_file).load()['settings']['analysis_results'][key]
        self.assertEqual(local_cues.cues(final),[])
        self.assertEqual(local_grid.effective_result(final)['bpm'],121)

    def test_set_cue_uses_first_empty_slot_on_active_page_and_preserves_existing_cues(self):
        track=self.track('next-cue');record=self.result(track);key=analysis.result_key(track.path)
        self.win.settings['analysis_results']={key:record};pb=self.load(track,record)
        self.assertTrue(self.win._save_state())
        with mock.patch.object(pb.player,'set_cues'),mock.patch.object(pb,'capture_cue_position',side_effect=[1000,2000,3000]),mock.patch.object(pb,'_update_transport'):
            self.win._dispatch_keyboard_action('cues.set');self.win._dispatch_keyboard_action('cues.set')
            pb.cue_panel.memory_tab.click();self.win._dispatch_keyboard_action('cues.set')
        self.wait(lambda:not self.win.prep_controller.admission_pending)
        self.assertFalse(self.win.prep_controller.admission_error)
        cues=local_cues.cues(StateRepository(self.win._data_file).load()['settings']['analysis_results'][key])
        self.assertEqual([(c['kind'],c['slot'],c['position_ms']) for c in cues],[('hot',0,1000),('hot',1,2000),('memory',0,3000)])
        pb.cue_panel._cues['memory']=[dict(position_ms=i) for i in range(8)]
        with mock.patch.object(self.win,'_edit_cue') as edit:self.assertFalse(self.win._set_next_cue());edit.assert_not_called()

    def test_held_grid_button_repeats_durable_moves_and_stops_on_release(self):
        track=self.track('held-grid');record=self.result(track);key=analysis.result_key(track.path)
        self.win.settings['analysis_results']={key:record};pb=self.load(track,record);self.win.show();self.app.processEvents()
        self.win._sync_grid();button=self.win.grid_edit_buttons[2];self.assertTrue(button.isEnabled())
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            QTest.mousePress(button,Qt.LeftButton);QTest.qWait(510);QTest.mouseRelease(button,Qt.LeftButton);self.app.processEvents()
            saved=deepcopy(self.win.settings['analysis_results'][key]);self.assertGreaterEqual(saved['grid_draft']['revision'],2)
            QTest.qWait(180);self.assertEqual(self.win.settings['analysis_results'][key],saved)
        self.assertEqual(StateRepository(self.win._data_file).load()['settings']['analysis_results'][key],saved)

    def test_cloud_metadata_drift_reuses_completed_analysis_only_after_exact_byte_proof(self):
        track = self.track('cloud'); record = self.result(track)
        track.path.chmod(0o600)
        self.assertNotEqual(record['epoch'],analysis.identity(track.path.stat()))
        cache = SourceVerificationCache(self.root / 'proofs')
        fallback = dict(beats=[[1,0,119.85]])
        resolved = analysis.playback_data(fallback,record,track.path,verification_cache=cache)
        self.assertEqual(resolved['beats'],record['beats'])
        self.assertEqual(cache.reads,1)
        again = analysis.playback_data(fallback,record,track.path,verification_cache=cache)
        self.assertEqual(again,resolved); self.assertEqual(cache.hits,1)
        track.path.write_bytes(b'different captured audio')
        self.assertEqual(analysis.playback_data(fallback,record,track.path,verification_cache=cache),fallback)

    def test_folder_drop_expands_nested_audio_and_playlist_import_also_joins_collection(self):
        from desktop.trees import _audio_paths
        folder = self.root / 'album'; (folder / 'nested').mkdir(parents=True)
        for rel in ('first.wav','nested/second.wav','cover.txt'):
            (folder / rel).write_bytes(b'fixture')
        md = QMimeData(); md.setUrls([QUrl.fromLocalFile(str(folder))])
        self.assertEqual(_audio_paths(md),[str(folder)])
        self.win.custom['Set'] = []
        with mock.patch.object(self.win, '_analyze_tracks') as analyze:
            self.win.settings['analysis'] = dict(automatic=False)
            self.win._import_files([str(folder)], 'Set')
            self.wait(lambda: len(self.win.imported) == 2)
            self.assertEqual(set(self.win.custom['Set']),set(self.win.imported))
            self.assertEqual({t.track_id for t in self.win._collection_tracks()},set(self.win.imported))
            analyze.assert_not_called()
        self.assertEqual(len(imports.expand_paths([folder,folder / 'first.wav'])),2)

    def test_collection_drop_is_visible_before_slow_metadata_and_clears_stale_search_sort(self):
        old = self.track('older')
        old.added_ts = 1
        self.win.model.set_rows([self.win._row_for(old)])
        self.win.search.setText('older')
        self.win.model.sort(0)
        files = [self.root / name for name in ('z-new.wav','y-new.wav')]
        for path in files: path.write_bytes(b'fixture')
        release = threading.Event(); self.addCleanup(release.set)
        def probe(path):
            release.wait(5)
            return dict(time=10,size=path.stat().st_size)
        with mock.patch('soundbox.imports.probe',side_effect=probe):
            self.win._import_files([str(p) for p in files])
            self.assertEqual(self.win.search.text(),'')
            self.assertEqual(self.win.model._sort,None)
            self.assertEqual({r['id'] for r in self.win.model._rows[:2]},set(self.win.imported))
            self.assertTrue(all(r['time']==0 for r in self.win.imported.values()))
            self.assertEqual(self.win._view_key,'sb:Collection')
            release.set()
            self.wait(lambda: all(r['time']==10 for r in self.win.imported.values()))

    def test_failed_import_save_never_admits_analysis_or_visible_unsaved_tracks(self):
        path = self.root / 'new.wav'; path.write_bytes(b'fixture')
        self.win.settings['analysis'] = dict(automatic=True)
        with mock.patch.object(self.win,'_save_state',return_value=False), \
                mock.patch.object(self.win,'_analyze_tracks') as analyze, \
                mock.patch.object(self.win,'_probe_import_metadata') as probe:
            self.win._import_files([str(path)])
        self.assertEqual(self.win.imported,{})
        self.assertEqual(self.win.lib.tracks_by_id,{})
        analyze.assert_not_called();probe.assert_not_called()
        missing=self.track('missing');missing.path.unlink()
        with mock.patch.object(self.win,'_flash_status') as status:
            self.assertFalse(self.win._queue_shared_import([missing],dict(kind='collection')))
            status.assert_called_once()

    def test_direct_native_playlist_file_drop_captures_target_and_both_collection_membership(self):
        from test_ui_source_playlists import native_catalog
        self.win.lib=Library({},[Playlist('List',[],[],'1000')],self.root/'master.db',
            source_kind='rekordbox-db',source_library_id='123',source_playlist_catalog=native_catalog())
        self.win._rekordbox_source_verified=True;self.win.tag_controller.configure(self.win.lib,True)
        self.win.settings['analysis']=dict(automatic=False)
        path=self.root/'new.wav';path.write_bytes(b'fixture')
        with mock.patch.object(self.win,'_probe_import_metadata'):
            self.win._native_import_files('1000',[str(path)])
        job=self.win.settings[shared_imports.KEY][0];tid=next(iter(self.win.imported))
        self.assertEqual(job['destination']['id'],'1000');self.assertFalse(job['preferences']['automatic'])
        self.assertEqual(job['tracks'],[dict(id=tid,path=str(path))])
        self.assertEqual([t.track_id for t in self.win._collection_tracks()],[tid])
        self.assertEqual(self.win._native_presentation().playlists[0].keys,[tid])
        self.assertEqual(self.win.lib.playlists[0].keys,[])
        saved=StateRepository(self.win._data_file).load()
        self.assertEqual(saved['settings'][shared_imports.KEY],[job])

    def test_collection_finder_drop_survives_source_refresh_deleting_hovered_row(self):
        self.win.lib=Library({},[],self.root/'master.db',source_kind='rekordbox-db',source_library_id='123')
        self.win._rekordbox_source_verified=True;self.win.tag_controller.configure(self.win.lib,True)
        self.win.settings['analysis']=dict(automatic=False)
        self.win._refresh_custom();self.win.show();self.app.processEvents()
        tree=self.win.sb_tree
        tree._set_hover(tree.topLevelItem(0))
        self.win._refresh_custom()  # native readback deletes the hovered item
        path=self.root/'collection.wav';path.write_bytes(b'fixture')
        md=QMimeData();md.setUrls([QUrl.fromLocalFile(str(path))])
        point=QPointF(tree.visualItemRect(tree.topLevelItem(0)).center())
        event=QDropEvent(point,Qt.CopyAction,md,Qt.LeftButton,Qt.NoModifier)
        with mock.patch.object(self.win,'_probe_import_metadata'):
            tree.dropEvent(event)
        self.assertTrue(event.isAccepted())
        saved=StateRepository(self.win._data_file).load()
        self.assertEqual(len(saved['imported']),1)
        job=saved['settings'][shared_imports.KEY][0]
        self.assertEqual(job['destination'],dict(kind='collection'))
        self.assertFalse(job['preferences']['automatic'])
        self.assertEqual(job['tracks'][0]['path'],str(path))

    def test_bare_native_identity_keeps_completed_grid_and_cue_drafts_editable_without_reanalysis(self):
        track=self.track('registered');record=self.result(track);key=analysis.result_key(track.path)
        self.win.settings['analysis_results']={key:record};pb=self.load(track,record)
        native=replace(track,imported=False,source_native_id='42',source_uuid='native-uuid',source_cues=[])
        self.win.lib.tracks_by_id[track.track_id]=native
        self.win.tag_controller.configure(self.win.lib,False);self.win.grid_controller.configure()
        self.assertTrue(self.win.grid_controller.is_local(track.track_id))
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'), \
                mock.patch.object(self.win,'_analyze_tracks') as analyze:
            self.assertTrue(self.win._edit_grid('shift',1))
            self.assertTrue(self.win._edit_local_cue(native,'memory',0,'add',500))
            analyze.assert_not_called()
        self.wait(lambda:not self.win.prep_controller.admission_pending)
        self.assertFalse(self.win.prep_controller.admission_error)
        after=self.win.settings['analysis_results'][key]
        self.assertEqual(after['sha256'],record['sha256']);self.assertEqual(after['beats'],record['beats'])
        self.assertEqual(local_grid.effective_result(after)['beats'][0][1],1)
        self.assertEqual(local_cues.cues(after)[0]['position_ms'],500)
        self.assertEqual(self.win.lib.tracks_by_id[track.track_id].native_id,'42')
        with mock.patch.object(pb,'clear_live_grid') as clear:
            self.win._apply_loaded_library()
            clear.assert_not_called()
        self.assertEqual(local_grid.effective_result(self.win.settings['analysis_results'][key])['beats'][0][1],1)

    def test_import_prep_is_durable_while_publication_refresh_and_device_work_are_busy(self):
        track=self.track('immediate');record=self.result(track);key=analysis.result_key(track.path)
        self.win.settings['analysis_results']={key:record};pb=self.load(track,record)
        native=replace(track,imported=False,source_native_id='42',source_uuid='native-uuid',source_cues=[])
        self.win.lib.tracks_by_id[track.track_id]=native
        self.win.tag_controller.configure(self.win.lib,False)
        self.win.tag_controller.busy=True;self.win.tag_controller._busy_kind='analysis'
        self.win._refresh_worker=object()
        with mock.patch.object(self.win,'_device_operation_owned',return_value=True), \
                mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'), \
                mock.patch.object(pb,'capture_cue_position',return_value=1234):
            self.win._sync_grid();self.win._sync_cue_editing()
            self.assertTrue(all(b.isEnabled() for b in self.win.grid_edit_buttons))
            self.assertTrue(pb.cue_panel._editable)
            self.assertTrue(self.win._edit_grid('shift',1))
            self.assertTrue(self.win._edit_cue('hot',0,'add'))
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],1234)
        self.win.tag_controller.busy=False;self.win._refresh_worker=None
        self.wait(lambda:not self.win.prep_controller.admission_pending)
        self.assertFalse(self.win.prep_controller.admission_error)
        saved=StateRepository(self.win._data_file).load()['settings']
        self.assertEqual(len(saved['pending_prep_edits'][0]['operations']),2)
        self.assertEqual(saved['pending_prep_edits'][0]['cues'][0]['position_ms'],1234)
        self.assertEqual(saved['analysis_results'][key]['beats'],record['beats'])
        self.assertEqual(local_grid.effective_result(saved['analysis_results'][key])['beats'][0][1],1)
        # Initial publication can acquire a native analysis path before the newer
        # intents finish; it must not disable controls or replace their display.
        self.win.lib.tracks_by_id[track.track_id]=replace(native,source_analysis_path='/synthetic/ANLZ.DAT')
        self.assertTrue(self.win.grid_controller.is_local(track.track_id))
        self.assertEqual(self.win.prep_controller.grid_view(track.track_id)[0][0][1],1)

    def test_prep_before_detector_completion_is_retained_when_real_result_arrives(self):
        track=self.track('before-detection');record=self.result(track);pb=self.load(track,record)
        self.assertNotIn(analysis.result_key(track.path),self.win.settings.get('analysis_results',{}))
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
            self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        self.wait(lambda:not self.win.prep_controller.admission_pending)
        self.assertFalse(self.win.prep_controller.admission_error)
        bound=self.win.prep_controller.attach_result(track,record)
        self.assertEqual(local_grid.effective_result(bound)['beats'][0][1],1)
        self.assertEqual(local_cues.cues(bound)[0]['position_ms'],777)
        self.assertEqual(bound['sha256'],record['sha256'])
        saved=StateRepository(self.win._data_file).load()['settings']['pending_prep_edits']
        self.assertEqual(saved[0]['cues'][0]['position_ms'],777)

    def test_queued_cue_keeps_new_detector_binding_and_exact_operation_ids(self):
        track=self.track('queued-binding');record=self.result(track);pb=self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
            self.win.tag_controller.busy=True
            self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        intended=deepcopy(controller.state_lane.queue[-1]['job']['operations'])
        bound=controller.attach_result(track,record)
        self.win.settings['analysis_results']={key:bound}
        self.assertTrue(self.win._save_state())
        self.assertEqual(controller.job(track.track_id)['sha256'],record['sha256'])
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],777)
        self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        self.assertFalse(controller.admission_error)
        saved=StateRepository(self.win._data_file).load()['settings']
        job=saved['pending_prep_edits'][0]
        self.assertEqual(job['operations'],intended)
        self.assertEqual((job['sha256'],job['epoch']),(record['sha256'],record['epoch']))
        self.assertEqual(local_cues.cues(saved['analysis_results'][key])[0]['position_ms'],777)
        self.assertEqual(local_grid.effective_result(saved['analysis_results'][key])['beats'][0][1],1)

    def test_queued_cue_keeps_verified_cloud_epoch_and_cleared_identity_hold(self):
        track=self.track('queued-renewal');record=self.result(track);pb=self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        self.win.settings['analysis_results']={key:record}
        self.assertTrue(self.win._save_state())
        track.path.chmod(0o600)
        self.assertNotEqual(analysis.identity(track.path.stat()),record['epoch'])
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
            self.win.tag_controller.busy=True
            try:
                self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
                intended=deepcopy(controller.state_lane.queue[-1]['job']['operations'])
                self.assertTrue(controller.state_lane.queue[-1]['job']['identity_pending'])
                def verified(work,completed):
                    completed(SimpleNamespace(result=work(),error=''))
                    return True
                with mock.patch.object(controller.tags,'run_prep_job',side_effect=verified):
                    controller._check_identity(deepcopy(controller.job(track.track_id)),self.win.lib,track)
            finally:
                # This fixture owns the fake source hold. Release it even if a
                # proof assertion fails so normal shutdown can drain its lane.
                self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        self.assertFalse(controller.admission_error)
        renewed=deepcopy(controller.job(track.track_id))
        self.assertNotIn('identity_pending',renewed)
        self.assertEqual(renewed['epoch'],analysis.identity(track.path.stat()))
        job=StateRepository(self.win._data_file).load()['settings']['pending_prep_edits'][0]
        self.assertEqual(job['operations'],intended)
        self.assertEqual((job['sha256'],job['epoch']),(renewed['sha256'],renewed['epoch']))
        self.assertNotIn('identity_pending',job)

    def test_queued_cue_cannot_borrow_identity_from_retargeted_same_job(self):
        track=self.track('queued-other-binding');record=self.result(track);pb=self.load(track,record)
        controller=self.win.prep_controller
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
            self.win.tag_controller.busy=True
            self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        before=self.win.settings['pending_prep_edits']
        changed=deepcopy(before);changed[0].update(path=str(track.path.with_name('foreign.wav')),sha256='b'*64)
        self.win.settings['pending_prep_edits']=changed
        row=controller.state_lane.queue[-1]
        try:
            with self.assertRaisesRegex(ValueError,'different source'):
                controller.state_lane._compose(row)
            self.assertEqual(row['job']['sha256'],'')
        finally:
            self.win.settings['pending_prep_edits']=before
            self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        self.assertFalse(controller.admission_error)
        self.assertEqual(controller.job(track.track_id)['sha256'],'')

    def test_detector_completion_waits_for_queued_cue_and_preserves_loaded_markers(self):
        track=self.track('detector-queued');record=self.result(track);pb=self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
        worker=self.completed_analysis(track,record)
        self.win.tag_controller.busy=True
        self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        intended=deepcopy(controller.state_lane.queue[-1]['job']['operations'])
        with mock.patch.object(self.win.native_analysis_controller,'discover'), \
                mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.win._analysis_retired(worker)
            self.assertIsNotNone(self.win._analysis_worker)
            self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
            self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],777)
            self.win.tag_controller.busy=False
            self.wait(lambda:self.win._analysis_worker is None and not controller.admission_pending)
            detect.assert_not_called()
        saved=StateRepository(self.win._data_file).load()['settings']
        self.assertEqual(saved[analysis_jobs.KEY],[])
        self.assertEqual(saved['pending_prep_edits'][0]['operations'],intended)
        self.assertEqual(saved['pending_prep_edits'][0]['sha256'],record['sha256'])
        self.assertEqual(local_grid.effective_result(saved['analysis_results'][key])['beats'][0][1],1)
        self.assertEqual(local_cues.cues(saved['analysis_results'][key])[0]['position_ms'],777)
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],777)

    def test_detector_completion_waits_for_active_cue_ack_without_redetection(self):
        track=self.track('detector-active');record=self.result(track);pb=self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        with mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.assertTrue(self.win._edit_grid('shift',1))
        worker=self.completed_analysis(track,record)
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        commit=self.win._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise RuntimeError('held canonical fixture timed out')
            return commit(patch)
        with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=held), \
                mock.patch.object(self.win.native_analysis_controller,'discover'), \
                mock.patch('desktop.analysis.analysis.analyze') as detect:
            self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
            self.wait(entered.is_set)
            self.assertTrue(self.win._state_publication)
            self.win._analysis_retired(worker)
            self.assertIsNotNone(self.win._analysis_worker)
            self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
            self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],777)
            release.set()
            self.wait(lambda:self.win._analysis_worker is None and not controller.admission_pending)
            detect.assert_not_called()
        saved=StateRepository(self.win._data_file).load()['settings']
        self.assertEqual(saved[analysis_jobs.KEY],[])
        self.assertFalse(controller.admission_error)
        self.assertEqual(len(saved['pending_prep_edits'][0]['operations']),2)
        self.assertEqual(saved['pending_prep_edits'][0]['sha256'],record['sha256'])
        self.assertEqual(local_grid.effective_result(saved['analysis_results'][key])['beats'][0][1],1)
        self.assertEqual(local_cues.cues(saved['analysis_results'][key])[0]['position_ms'],777)

    def test_real_detector_thread_retires_before_deferred_result_publication(self):
        from desktop.analysis import _CompletedAnalysis
        from shiboken6 import isValid
        track=self.track('detector-retired-thread');record=self.result(track);pb=self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        detecting,finish_detector,committing,finish_commit=(threading.Event() for _ in range(4))
        self.addCleanup(finish_detector.set);self.addCleanup(finish_commit.set)
        calls=[]
        def detector(*_args,**_kwargs):
            calls.append(True);detecting.set()
            if not finish_detector.wait(5):raise RuntimeError('detector fixture timed out')
            return record
        commit=self.win._state_repository.commit_patch
        def held(patch):
            committing.set()
            if not finish_commit.wait(5):raise RuntimeError('canonical fixture timed out')
            return commit(patch)
        self.win.settings['analysis']=dict(bpm=True,key=False,first_cue=False)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detector), \
                mock.patch.object(self.win.native_analysis_controller,'discover'), \
                mock.patch.object(pb.player,'set_grid'),mock.patch.object(pb,'_update_transport'):
            self.win._analyze_tracks([track.track_id]);self.wait(detecting.is_set)
            original_thread=self.win._analysis_worker
            self.assertTrue(self.win._edit_grid('shift',1))
            with mock.patch.object(self.win._state_repository,'commit_patch',side_effect=held):
                self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
                self.wait(committing.is_set)
                finish_detector.set()
                self.wait(lambda:self.win._analysis_owner.pending==0
                    and isinstance(self.win._analysis_worker,_CompletedAnalysis))
                self.assertTrue(self.win._state_publication)
                self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
                if isValid(original_thread):
                    self.app.sendPostedEvents(original_thread,QEvent.DeferredDelete)
                self.assertFalse(isValid(original_thread))
                # Only the detached Python completion is used from this point.
                finish_commit.set()
                self.wait(lambda:self.win._analysis_worker is None and not controller.admission_pending)
        saved=StateRepository(self.win._data_file).load()['settings']
        self.assertEqual(len(calls),1)
        self.assertEqual(saved[analysis_jobs.KEY],[])
        self.assertEqual(saved['pending_prep_edits'][0]['sha256'],record['sha256'])
        self.assertEqual(len(saved['pending_prep_edits'][0]['operations']),2)
        self.assertEqual(local_grid.effective_result(saved['analysis_results'][key])['beats'][0][1],1)
        self.assertEqual(local_cues.cues(saved['analysis_results'][key])[0]['position_ms'],777)
        self.assertEqual(pb.cue_panel._cues['hot'][0]['position_ms'],777)

    def deferred_detector_stop(self, *, owner=False):
        track=self.track('detector-owner-stop' if owner else 'detector-event-stop')
        record=self.result(track);self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        worker=self.completed_analysis(track,record)
        self.win.tag_controller.busy=True
        self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        self.win._analysis_retired(worker);completion=self.win._analysis_worker
        before=self.win._data_file.read_bytes()
        if owner:self.win._analysis_owner.stop()
        else:completion.stop.set()
        self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        cue_checkpoint=StateRepository(self.win._data_file).load()
        self.win._analysis_retired(completion)
        self.assertIsNone(self.win._analysis_worker)
        self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
        self.assertEqual(StateRepository(self.win._data_file).load(),cue_checkpoint)
        self.assertNotEqual(self.win._data_file.read_bytes(),before)  # accepted cue alone finished
        self.assertEqual(self.win.settings[analysis_jobs.KEY][0]['state'],'queued')

    def test_deferred_detector_stop_event_retains_restart_intent_without_publication(self):
        self.deferred_detector_stop()

    def test_deferred_detector_stopping_owner_retains_restart_intent_without_publication(self):
        self.deferred_detector_stop(owner=True)

    def test_deferred_detector_completion_cannot_publish_into_restored_epoch(self):
        track=self.track('detector-restored');record=self.result(track);self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        worker=self.completed_analysis(track,record)
        self.win.tag_controller.busy=True
        self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        self.win._analysis_retired(worker);completion=self.win._analysis_worker
        before=self.win._data_file.read_bytes()
        self.win._state_maintenance=True
        self.win._state_repository.generation+=1
        self.win._analysis_retired(completion)
        self.assertIsNone(self.win._analysis_worker)
        self.assertEqual(self.win._data_file.read_bytes(),before)
        self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
        self.win._state_maintenance=False;self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        self.assertTrue(controller.admission_error)
        # The isolated refusal is proved above; undo only the synthetic epoch
        # increment so normal fixture teardown can save its window preferences.
        self.win._state_repository.generation-=1

    def test_deferred_detector_completion_cannot_publish_after_shutdown(self):
        track=self.track('detector-shutdown');record=self.result(track);self.load(track,record)
        key=analysis.result_key(track.path);controller=self.win.prep_controller
        worker=self.completed_analysis(track,record)
        self.win.tag_controller.busy=True
        self.assertTrue(self.win._edit_local_cue(track,'hot',0,'add',777))
        self.win._analysis_retired(worker);completion=self.win._analysis_worker
        before=self.win._data_file.read_bytes();self.win._shutting_down=True
        self.win._analysis_retired(completion)
        self.assertIsNone(self.win._analysis_worker)
        self.assertEqual(self.win._data_file.read_bytes(),before)
        self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
        self.win._shutting_down=False;self.win.tag_controller.busy=False
        self.wait(lambda:not controller.admission_pending)
        QTest.qWait(35)
        self.assertNotIn(key,self.win.settings.get('analysis_results',{}))
        self.assertEqual(self.win.settings[analysis_jobs.KEY][0]['state'],'queued')

    def test_failed_prep_persistence_does_not_paint_or_admit_an_unsaved_edit(self):
        track=self.track('disk-full');record=self.result(track);key=analysis.result_key(track.path)
        self.win.settings['analysis_results']={key:record};pb=self.load(track,record)
        before=deepcopy(self.win.settings)
        disk_before=self.win._data_file.read_bytes() if self.win._data_file.exists() else None
        with mock.patch.object(self.win,'_save_state',return_value=False), \
                mock.patch.object(pb,'refresh_live_grid') as paint, \
                mock.patch.object(self.win,'_flash_status') as status:
            self.assertFalse(self.win._edit_grid('shift',1))
        self.assertEqual(self.win.settings,before);paint.assert_not_called()
        self.assertEqual(self.win._data_file.read_bytes() if self.win._data_file.exists() else None,disk_before)
        self.assertIn('could not be saved',status.call_args.args[0])
        # Storage recovery needs no sticky input veto or manual Retry action.
        self.assertEqual(self.win.prep_controller.reason(track.track_id),'')
        self.assertTrue(self.win._edit_grid('shift',1))
        self.assertTrue(self.win.prep_controller.pending)
        self.assertEqual(StateRepository(self.win._data_file).load()['settings']['pending_prep_edits'],
                         self.win.prep_controller.pending)

    def test_cmd_r_renames_focused_playlist_inline_and_keeps_membership(self):
        from desktop.trees import CUSTOM_ROLE
        self.win.custom={'Rap':['kept-one','kept-two']};self.win._refresh_custom();self.win.show();self.app.processEvents()
        item=next(i for i in self.win.sb_tree.findItems('',Qt.MatchContains|Qt.MatchRecursive) if i.data(0,CUSTOM_ROLE)=='Rap')
        self.win.sb_tree.setCurrentItem(item);self.win.sb_tree.setFocus();self.app.processEvents()
        with mock.patch('desktop.playlist_folders.QInputDialog.getText') as popup:
            self.win._keyboard_router.dispatch('browser.rename');self.app.processEvents()
            editor=self.win.sb_tree.findChild(QLineEdit,'PlaylistNameEditor');self.assertIsNotNone(editor)
            self.assertEqual(editor.text(),'Rap');QTest.keyClicks(editor,'Drums');QTest.keyClick(editor,Qt.Key_Return);QTest.qWait(30)
            popup.assert_not_called()
        self.assertEqual(self.win.custom,{'Drums':['kept-one','kept-two']})
        self.assertEqual(StateRepository(self.win._data_file).load()['custom'],self.win.custom)

    def test_graveyard_cmd_a_selects_all_without_running_custom_downbeat_binding(self):
        from desktop.keyboard import Keymap
        self.win._ensure_settings_page()
        grave=self.win._grave;grave.clear()
        for title in ('One','Two','Three'):grave.addTopLevelItem(QTreeWidgetItem([title,'' ]))
        self.win._keyboard_router.keymap=Keymap({'schema':1,'bindings':{'grid.downbeat':['Ctrl+A']}})
        event=QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.ControlModifier,'a')
        with mock.patch('desktop.app.QApplication.focusWidget',return_value=grave),mock.patch.object(self.win,'_edit_grid') as grid:
            self.assertTrue(self.win.eventFilter(grave,event));grid.assert_not_called()
        self.assertEqual(len(grave.selectedItems()),3)

    def test_theme_and_text_hex_commit_through_existing_save_path_without_picker(self):
        self.win.show();self.win._open_settings();self.win.activateWindow();QTest.qWait(1)
        with mock.patch('desktop.settings_page.QColorDialog') as picker, mock.patch.object(self.win,'_schedule_settings_save') as save:
            button=self.win._theme_color;button._start_edit();QTest.keyClicks(button.editor,'12ab34');QTest.keyClick(button.editor,Qt.Key_Return)
            self.assertEqual(self.win.settings['theme_accent'],'#12ab34');save.assert_called_once()
            self.win._theme_text_mode.setCurrentIndex(self.win._theme_text_mode.findData('custom'));save.reset_mock()
            button=self.win._theme_text_color;button._start_edit();QTest.keyClicks(button.editor,'#345678');QTest.keyClick(button.editor,Qt.Key_Return)
            self.assertEqual(self.win.settings['theme_text_custom'],'#345678');save.assert_called_once();picker.assert_not_called()

    def test_collection_import_saves_native_intent_and_detection_preferences_atomically(self):
        self.win.lib=Library({},[],self.root/'master.db',source_kind='rekordbox-db',source_library_id='123')
        self.win._rekordbox_source_verified=True;self.win.tag_controller.configure(self.win.lib,True)
        self.win.settings['analysis']=dict(automatic=True,bpm=True,key=False)
        path=self.root/'new.wav';path.write_bytes(b'fixture')
        with mock.patch.object(self.win,'_probe_import_metadata'),mock.patch.object(self.win,'_resume_analysis_jobs'):
            self.win._import_files([str(path)])
            saved=StateRepository(self.win._data_file).load()['settings']
            self.assertEqual(saved[shared_imports.KEY][0]['destination'],dict(kind='collection'))
            self.assertTrue(saved[shared_imports.KEY][0]['preferences']['automatic'])
            self.assertFalse(saved[analysis_jobs.KEY][0]['preferences']['key'])
            self.win.settings['analysis']=dict(automatic=False,bpm=False,key=True)
            self.assertFalse(self.win.settings[analysis_jobs.KEY][0]['preferences']['key'])

    def test_refresh_keeps_native_source_usable_after_bound_song_is_removed_in_rekordbox(self):
        from uuid import uuid4
        track=self.track('removed');record=self.result(track);key=analysis.result_key(track.path)
        self.win.imported[track.track_id]=dict(path=str(track.path),time=10,size=track.path.stat().st_size)
        self.win.settings['analysis_results']={key:record}
        source=self.root/'master.db'
        self.win.settings[shared_imports.BINDINGS]=[dict(local_id=track.track_id,source=str(source),library_id='123',
            native_id='42',native_uuid=str(uuid4()),canonical=str(track.path))]
        other=replace(self.track('other'),track_id='43',imported=False,source_uuid=str(uuid4()))
        self.win.lib=Library({'43':other},[],source,source_kind='rekordbox-db',source_library_id='123')
        self.win._rekordbox_source_verified=True;self.win._rekordbox_connection_error=''
        with mock.patch.object(self.win,'_arm_waveform_rechecks'):
            self.win._apply_loaded_library()
        self.assertTrue(self.win._rekordbox_source_verified)
        self.assertEqual(self.win._rekordbox_connection_error,'')
        self.assertNotIn(track.track_id,self.win.lib.tracks_by_id)
        self.assertEqual(self.win.lib.tracks_by_id['43'].source_uuid,other.source_uuid)
        self.assertEqual(self.win.settings['analysis_results'][key],record)
        self.assertIn(track.track_id,self.win.imported)
