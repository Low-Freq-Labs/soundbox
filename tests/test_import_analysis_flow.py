"""Actual window import/analysis flow, with controlled detector completion."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from soundbox import analysis, analysis_jobs, imports, local_cues, local_grid
from soundbox.library import Library, Track
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

    def test_background_batch_publishes_each_track_then_accepts_more_without_windows(self):
        first, second, third = [self.track(n) for n in ('first','second','third')]
        release = threading.Event(); self.addCleanup(release.set)
        self.win.model.set_rows([self.win._row_for(t) for t in (first,second,third)])
        self.win.settings['analysis'] = dict(automatic=True)
        def detect(_lib, track, _prefs, *, should_cancel):
            if track == second:
                while not release.wait(.005):
                    if should_cancel(): raise RuntimeError('cancelled')
            return self.result(track)
        with mock.patch('desktop.analysis.analysis.analyze', side_effect=detect), \
                mock.patch.object(QDialog, 'open') as popup, \
                mock.patch.object(self.win, '_flash_status') as status:
            self.win._analyze_tracks([first.track_id, second.track_id], automatic=True)
            key = analysis.result_key(first.path)
            self.wait(lambda: key in self.win.settings.get('analysis_results', {}))
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
            saved = deepcopy(self.win.settings['analysis_results'])
            with mock.patch.object(self.win, '_save_state', return_value=False):
                self.assertFalse(self.win._edit_cue('hot',0,'delete'))
            self.assertEqual(self.win.settings['analysis_results'],saved)
            self.assertIsNotNone(pb.cue_panel._cues['hot'][0])
            reload_audio.assert_not_called()
        restarted = StateRepository(self.win._data_file).load()['settings']['analysis_results'][key]
        self.assertEqual(local_grid.effective_result(restarted)['bpm'],121)
        self.assertEqual(local_cues.cues(restarted)[0]['position_ms'],1234)
        late = analysis.merge_result(restarted,record,dict(bpm=True,key=True))
        self.assertEqual(local_cues.cues(late),local_cues.cues(restarted))
        self.assertEqual(analysis.playback_data({},late,track.path)['cues'],local_cues.cues(restarted))

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
