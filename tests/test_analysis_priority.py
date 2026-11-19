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

    def test_source_admission_between_each_phase_cannot_starve_remaining_analysis(self):
        from dataclasses import replace
        from PySide6.QtCore import QTimer
        from desktop.app import MainWindow
        second_path=self.root/'second.wav';second_path.write_bytes(self.audio.read_bytes())
        second=replace(self.track,track_id='sb-second',location_uri=second_path.as_uri())
        self.lib.tracks_by_id[second.track_id]=second
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False), \
                mock.patch.object(MainWindow,'_refresh_devices'):
            win=MainWindow(isolated_dir=self.root/'fair-state')
        self.addCleanup(lambda:(win.shutdown(),win.close(),win.deleteLater(),self.app.processEvents()))
        win.lib=self.lib;win.tag_controller.configure(win.lib,False)
        win.grid_controller.configure();win._view_key='sb:Collection'
        win._waveform_rechecks_enabled=False;win._waveform_recheck_timer.stop()
        events=[];sources=[];extra=[];original=track_analysis.analyze_soundfile
        def build(*args):
            self.assertFalse(win.tag_controller.busy)
            events.append('waveform');return original(*args)
        def detect(_lib,track,prefs,**kwargs):
            self.assertFalse(win.tag_controller.busy)
            component='bpm' if prefs['bpm'] else 'key';events.append(component)
            sha=hashlib.sha256(track.path.read_bytes()).hexdigest()
            return dict(engine=analysis.ENGINE,path=str(track.path),epoch=analysis.identity(track.path.stat()),
                size=track.path.stat().st_size,sha256=sha,duration=4,bpm=120 if prefs['bpm'] else None,
                beats=[[1,0,120],[2,500,120]] if prefs['bpm'] else [],
                key='8A' if prefs['key'] else None,first_cue=None)
        def source_work():
            self.assertIsNone(win._analysis_worker)
            sources.append(True);events.append('source')
        def source_finished(_worker):
            # Continually arriving source work cannot take a second turn before
            # the next local phase. Use the real shared source owner/callbacks.
            if win._analysis_priority_pending():
                extra.append(win.tag_controller.run_playlist_job(source_work,source_finished))
                QTimer.singleShot(0,win,source_pump)
        def source_pump():
            if len(sources)<6:
                win.tag_controller.run_playlist_job(source_work,source_finished)
        with mock.patch.object(win.playlist_controller,'pump',side_effect=source_pump), \
                mock.patch.object(track_analysis,'analyze_soundfile',side_effect=build), \
                mock.patch.object(analysis,'analyze',side_effect=detect):
            win._analyze_tracks([self.track.track_id,second.track_id])
            end=time.monotonic()+15
            while (win.settings.get(analysis_jobs.KEY) or win.tag_controller.busy) and time.monotonic()<end:
                QTest.qWait(5)
        self.assertEqual(events,['waveform','source','bpm','source','key','source']*2)
        self.assertEqual(len(sources),6)
        self.assertTrue(extra);self.assertFalse(any(extra))
        self.assertEqual(win.settings[analysis_jobs.KEY],[])

    def test_source_turn_requires_current_source_and_shutdown_discards_it(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from desktop.analysis_priority import AnalysisPriorityMixin
        tags=SimpleNamespace(_start_next_cue=mock.Mock(),busy=False,checking=False)
        host=SimpleNamespace(lib=self.lib,_shutting_down=False,_state_maintenance=False,
            _analysis_worker=None,tag_controller=tags,_analysis_priority_pending=lambda:True)
        host._analysis_source_turn=None
        foreign=replace(self.lib,source='other')
        AnalysisPriorityMixin._analysis_offer_source_turn(host,foreign)
        tags._start_next_cue.assert_not_called()
        self.assertTrue(AnalysisPriorityMixin._analysis_source_priority(host))
        AnalysisPriorityMixin._analysis_offer_source_turn(host,self.lib)
        self.assertFalse(AnalysisPriorityMixin._analysis_source_priority(host))
        host.lib=foreign
        self.assertTrue(AnalysisPriorityMixin._analysis_source_priority(host))
        host._analysis_worker=object();host._analysis_priority_pending=lambda:False
        # An unrelated/absent current batch retains the established foreground
        # Delete semantics; a stale worker alone cannot invent a global hold.
        self.assertFalse(AnalysisPriorityMixin._analysis_source_priority(host))
        host._analysis_worker=None;host._analysis_priority_pending=lambda:True
        host.lib=self.lib;host._shutting_down=True
        AnalysisPriorityMixin._analysis_offer_source_turn(host,self.lib)
        self.assertIsNone(host._analysis_source_turn)

    def test_source_idle_wakes_only_an_existing_live_analysis_owner(self):
        from types import SimpleNamespace
        from desktop.analysis_priority import AnalysisPriorityMixin
        host=SimpleNamespace(_shutting_down=False,_state_maintenance=False,
            _analysis_queue=[('retained intention',)],_start_next_analysis=mock.Mock())
        AnalysisPriorityMixin._analysis_lane_released(host)
        host._start_next_analysis.assert_not_called()
        host._analysis_owner=SimpleNamespace(stopping=True)
        AnalysisPriorityMixin._analysis_lane_released(host)
        host._start_next_analysis.assert_not_called()
        host._analysis_owner.stopping=False
        AnalysisPriorityMixin._analysis_lane_released(host)
        host._start_next_analysis.assert_called_once()
        host._start_next_analysis.reset_mock();host._shutting_down=True
        AnalysisPriorityMixin._analysis_lane_released(host)
        host._start_next_analysis.assert_not_called()

    def test_cancelled_and_metadata_pending_waveforms_offer_source_without_retiring_intent(self):
        from types import SimpleNamespace
        import threading
        from desktop.analysis_priority import AnalysisPriorityMixin
        from soundbox.native_media import METADATA_PENDING
        for cancelled in (False,True):
            with self.subTest(cancelled=cancelled):
                stop=threading.Event()
                if cancelled:stop.set()
                worker=SimpleNamespace(stop=stop,result=RuntimeError(METADATA_PENDING),library=self.lib)
                host=SimpleNamespace(_shutting_down=False,_analysis_owner=SimpleNamespace(stopping=False),
                    _analysis_offer_source_turn=mock.Mock(),_schedule_next_analysis=mock.Mock(),
                    settings={analysis_jobs.KEY:[{'durable':'intention'}]})
                AnalysisPriorityMixin._analysis_waveform_retired(host,worker)
                host._analysis_offer_source_turn.assert_called_once_with(self.lib)
                host._schedule_next_analysis.assert_called_once_with(*(() if cancelled else (2000,)))
                self.assertEqual(host.settings[analysis_jobs.KEY],[{'durable':'intention'}])

    def test_failed_detector_yields_after_persisting_only_its_own_failure(self):
        from types import SimpleNamespace
        from desktop.analysis import AnalysisMixin,AnalysisWorker
        jobs=analysis_jobs.admit([],self.lib,[self.track],{})
        worker=AnalysisWorker(self.lib,[self.track],{},{});worker.job_id=jobs[0]['id']
        worker.result=ValueError('Detection fixture failed')
        host=SimpleNamespace(_shutting_down=False,_state_publication=False,
            _analysis_owner=SimpleNamespace(stopping=False),
            settings={analysis_jobs.KEY:jobs},_save_state=mock.Mock(return_value=True),
            _flash_status=mock.Mock(),_analysis_offer_source_turn=mock.Mock(),_schedule_next_analysis=mock.Mock())
        AnalysisMixin._analysis_retired(host,worker)
        saved=host.settings[analysis_jobs.KEY]
        self.assertEqual(saved[0]['id'],jobs[0]['id'])
        self.assertEqual(saved[0]['state'],'failed')
        self.assertEqual(saved[0]['error'],'Detection fixture failed')
        host._save_state.assert_called_once()
        host._analysis_offer_source_turn.assert_called_once_with(self.lib)

    def test_rotating_source_turns_do_not_starve_eligible_domains_behind_continuous_cues(self):
        from types import MethodType,SimpleNamespace
        from PySide6.QtWidgets import QFrame
        from desktop.analysis_priority import AnalysisPriorityMixin
        from desktop.tag_controller import TagEditController
        host=QFrame();self.addCleanup(host.deleteLater)
        host.lib=self.lib;host._shutting_down=False;host._state_maintenance=False;host._analysis_worker=None
        host._analysis_priority_pending=lambda:True
        host._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,host)
        host._analysis_source_admitted=MethodType(AnalysisPriorityMixin._analysis_source_admitted,host)
        tags=host.tag_controller=TagEditController(self.root/'contention-state',host,store_factory=lambda _:None)
        self.addCleanup(tags.shutdown)
        tags.analysis_priority=host._analysis_priority_pending
        tags.analysis_admission=host._analysis_source_priority;tags.source_admitted=host._analysis_source_admitted
        # A sustained logical cue backlog continues to fence ordinary observers.
        # Only the selected source opportunity may admit an unrelated task.
        tags._cue_queue=[SimpleNamespace(page='hot')]
        self.addCleanup(lambda:tags._cue_queue.clear())
        seen=[]
        def pump(kind,admit):
            if host._analysis_source_priority() or tags.source_job_owned:return
            admit(lambda:seen.append(kind),lambda _worker:None)
        cue=lambda: pump('cues',lambda work,done:tags._run_source_job('cues',work,done))
        tags._start_next_cue=cue
        methods=(('grid_controller','grid',tags.run_grid_job,'_drain'),
                 ('prep_controller','prep',tags.run_prep_job,'pump'),
                 ('playlist_controller','playlists',tags.run_playlist_job,'pump'),
                 ('tag_definition_controller','tag-definitions',lambda work,done:tags._run_source_job('tag-definitions',work,done),'pump'),
                 ('metadata_controller','metadata',lambda work,done:tags._run_source_job('metadata',work,done),'pump'),
                 ('shared_import_controller','registration',tags.run_registration_job,'pump'),
                 ('native_analysis_controller','analysis',tags.run_analysis_job,'pump'),
                 ('removal_controller','removal',tags.run_removal_job,'pump'),
                 ('media_controller','media',tags.run_media_job,'pump'))
        for name,kind,admit,method in methods:
            setattr(host,name,SimpleNamespace(**{method:lambda k=kind,a=admit:pump(k,a)}))
        expected=['cues','grid','prep','playlists','tag-definitions','metadata','registration','analysis','removal','media']*2
        for count in range(len(expected)):
            AnalysisPriorityMixin._analysis_offer_source_turn(host,host.lib)
            end=time.monotonic()+3
            while tags.busy and time.monotonic()<end:QTest.qWait(5)
            self.assertFalse(tags.busy);self.assertTrue(tags.owned)
            self.assertEqual(len(seen),count+1)
        self.assertEqual(seen,expected)

    def test_fair_source_turn_preserves_prepared_reservation_and_physical_ownership(self):
        from types import MethodType
        from PySide6.QtWidgets import QFrame
        from desktop.analysis_priority import AnalysisPriorityMixin
        from desktop.tag_controller import TagEditController
        host=QFrame();self.addCleanup(host.deleteLater)
        host.lib=self.lib;host._shutting_down=False;host._state_maintenance=False;host._analysis_worker=None
        host._analysis_priority_pending=lambda:True
        host._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,host)
        tags=host.tag_controller=TagEditController(self.root/'reserved-state',host,store_factory=lambda _:None)
        self.addCleanup(tags.shutdown)
        tags.analysis_priority=host._analysis_priority_pending;tags.analysis_admission=host._analysis_source_priority
        host._analysis_source_turn=(str(self.lib.source),self.lib.source_library_id)
        tags._analysis_fair_kind='playlists';tags.prep_priority=True
        self.assertFalse(tags.run_playlist_job(lambda:None,lambda _worker:None))
        tags.prep_priority=False;tags.busy=True
        self.assertTrue(tags.source_job_owned)
        self.assertFalse(tags.run_playlist_job(lambda:None,lambda _worker:None))
        tags.busy=False;tags.admission_paused=True
        self.assertTrue(tags.source_job_owned)
        self.assertFalse(tags.run_playlist_job(lambda:None,lambda _worker:None))
        tags.admission_paused=False

    def test_offered_source_turn_cannot_start_a_real_cue_head_while_admission_is_paused(self):
        from types import MethodType
        from PySide6.QtWidgets import QFrame
        from desktop.analysis_priority import AnalysisPriorityMixin
        from desktop.cue_session import CueIntent
        from desktop.tag_controller import TagEditController
        host=QFrame();self.addCleanup(host.deleteLater)
        host.lib=self.lib;host._shutting_down=False;host._state_maintenance=False;host._analysis_worker=None
        host._analysis_priority_pending=lambda:True
        host._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,host)
        tags=host.tag_controller=TagEditController(self.root/'paused-state',host,store_factory=lambda _:None)
        self.addCleanup(tags.shutdown)
        tags.lib=self.lib;tags.analysis_priority=host._analysis_priority_pending
        tags.analysis_admission=host._analysis_source_priority;tags.admission_paused=True
        intent=CueIntent(self.track.track_id,self.track.source_uuid,str(self.audio),'hot',0,'add',200,'fixture-key')
        tags._cue_queue=[intent]
        self.addCleanup(lambda:tags._cue_queue.clear())
        AnalysisPriorityMixin._analysis_offer_source_turn(host,self.lib)
        self.app.processEvents()
        self.assertFalse(tags.busy);self.assertEqual(tags.owner.pending,0)
        self.assertEqual(tags._cue_queue,[intent])
        self.assertIsNone(host._analysis_source_turn)

    def test_prepared_reservation_suppresses_fair_cue_and_actual_grid_dispatch(self):
        from copy import deepcopy
        from types import MethodType
        from PySide6.QtWidgets import QFrame
        from desktop.analysis_priority import AnalysisPriorityMixin
        from desktop.cue_session import CueIntent
        from desktop.grid_controller import GridEditController,_Group
        from desktop.tag_controller import TagEditController
        host=QFrame();self.addCleanup(host.deleteLater)
        host.lib=self.lib;host._shutting_down=False;host._state_maintenance=False;host._analysis_worker=None
        host._analysis_priority_pending=lambda:True
        host._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,host)
        tags=host.tag_controller=TagEditController(self.root/'prep-reserved-state',host,store_factory=lambda _:None)
        self.addCleanup(tags.shutdown)
        tags.lib=self.lib;tags.analysis_priority=host._analysis_priority_pending
        tags.analysis_admission=host._analysis_source_priority
        cue=CueIntent(self.track.track_id,self.track.source_uuid,str(self.audio),'hot',0,'add',200,'fixture-key')
        tags._cue_queue=[cue]
        self.addCleanup(lambda:tags._cue_queue.clear())
        grid=host.grid_controller=GridEditController(tags,host,store_factory=lambda _lib,_track:None)
        group=_Group(self.track.track_id,self.track.source_uuid,str(self.audio),'nudge',1)
        grid._queue=[group];grid._set_owned()
        pending=deepcopy(grid._queue);tags.prep_priority=True
        host._analysis_source_turn=(str(self.lib.source),self.lib.source_library_id)
        host._analysis_source_cursor=6
        with mock.patch.object(tags,'_start_next_cue',wraps=tags._start_next_cue) as cue_start, \
                mock.patch.object(grid,'_drain',wraps=grid._drain) as grid_drain:
            AnalysisPriorityMixin._analysis_offer_source_turn(host,self.lib)
            self.app.processEvents()
            cue_start.assert_not_called();grid_drain.assert_not_called()
        # The cue entrypoint independently respects the reservation as well.
        tags._start_next_cue()
        self.assertFalse(tags.busy);self.assertEqual(tags.owner.pending,0)
        self.assertEqual(tags._cue_queue,[cue]);self.assertEqual(grid._queue,pending)
        self.assertIsNone(grid._probe_wanted);self.assertIsNone(grid._active)
        self.assertIsNone(host._analysis_source_turn)
        self.assertEqual(host._analysis_source_cursor,6)
        self.assertTrue(host._analysis_source_priority())

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
