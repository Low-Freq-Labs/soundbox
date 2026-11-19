"""Hermetic analysis controls and queued worker retirement; no live source/devices."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest import mock

from PySide6.QtCore import QStandardPaths
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.analysis import AnalysisControls, AnalysisWorker, AnalysisMixin
from desktop.task_owner import TaskOwner
from soundbox.library import Library, Track
from soundbox import analysis, analysis_jobs, local_grid, prep_edits


class AnalysisUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_have_requested_choices_and_safe_defaults(self):
        controls = AnalysisControls(automatic=True)
        self.assertTrue(controls.bpm.isChecked()); self.assertTrue(controls.key.isChecked())
        self.assertFalse(controls.auto.isChecked()); self.assertFalse(controls.first_cue.isChecked())
        self.assertFalse(controls.phrase.isEnabled())
        self.assertEqual(controls.bpm_range.count(),10)
        self.assertEqual(controls.bpm_range.currentText(),'70–180')
        self.assertEqual([controls.process.itemText(i) for i in range(3)],['Performance','Normal','Power saving'])
        controls.deleteLater()

    def worker(self):
        track = Track('sb-fixture','Fixture','Test',Path('/nonexistent-fixture.wav').as_uri(),imported=True)
        return AnalysisWorker(Library({track.track_id:track},[],'synthetic'),[track],None,{})

    def record(self, path='/canonical/song.wav'):
        return dict(engine=analysis.ENGINE,path=path,size=100,epoch=[1,2,100,3,4],sha256='a'*64,
                    duration=10,bpm=120,key='8A',first_cue=None,
                    beats=[[i%4+1,i*500,120] for i in range(20)])

    def test_dispatch_snapshots_map_without_copying_unrelated_saved_songs(self):
        class Untouched(dict):
            def __deepcopy__(self,memo):raise AssertionError('Unrelated analysis copied')
        worker=self.worker();track=worker.tracks[0];lib=worker.library
        jobs=analysis_jobs.admit([],lib,[track],{})
        records={analysis.result_key(track.path):self.record(str(track.path)),
                 'unrelated':Untouched(self.record('/unrelated.wav'))}
        host=SimpleNamespace(_shutting_down=False,_analysis_worker=None,
            _analysis_owner=SimpleNamespace(stopping=False,adopt=mock.Mock()),
            tag_controller=SimpleNamespace(owned=False),_refresh_worker=None,
            _device_operation_owned=lambda:False,lib=lib,
            settings={analysis_jobs.KEY:jobs,'analysis_results':records},
            _analysis_queue=[(lib,track,{},jobs[0]['id'])],_analysis_retired=mock.Mock())
        with mock.patch('desktop.analysis.AnalysisWorker') as factory:
            AnalysisMixin._start_next_analysis(host)
        captured=factory.call_args.args[3]
        self.assertIsNot(captured,records)
        self.assertIs(captured['unrelated'],records['unrelated'])
        key=analysis.result_key(track.path);prior=records[key]
        records[key]=local_grid.edit(prior,'shift',1)
        records.pop('unrelated')
        self.assertIs(captured[key],prior)
        self.assertIn('unrelated',captured)
        self.assertNotIn('grid_draft',prior)

    def test_worker_selects_canonical_alias_record_and_retirement_keeps_newest_draft(self):
        class Untouched(dict):
            def __deepcopy__(self,memo):raise AssertionError('Unrelated analysis copied')
        worker=self.worker();worker.preferences=analysis.options(dict(bpm=False,key=True))
        record=local_grid.edit(self.record(),'shift',1)
        original=deepcopy(record);key=analysis.result_key(record['path'])
        live={key:record,'unrelated':Untouched(self.record('/unrelated.wav'))}
        worker.previous=dict(live)
        current={**self.record(),'bpm':None,'beats':[],'key':'9A'}
        def detected(*args,**kwargs):
            live[key]=local_grid.edit(live[key],'shift',1)
            return current
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=detected):worker.run()
        self.assertEqual(record,original)
        self.assertEqual(worker.result[key]['grid_draft'],original['grid_draft'])
        result=analysis.merge_result(live[key],worker.result[key],worker.preferences)
        self.assertEqual(result['grid_draft'],live[key]['grid_draft'])
        self.assertEqual(result['key'],'9A')
        worker.result[key]['grid_draft']['beats'][0][1]=42
        self.assertEqual(record,original)

    def test_retirement_preserves_prep_ownership_and_newest_draft_on_save_failure(self):
        from desktop.prep_controller import PrepEditController
        from uuid import uuid4

        class Untouched(dict):
            def __deepcopy__(self,memo):raise AssertionError('Unrelated prep job copied')

        class PrepHost:
            attach_result=PrepEditController.attach_result
            _record=PrepEditController._record
            job=PrepEditController.job

            @property
            def pending(self):return self.window.settings.get(prep_edits.KEY,[])

        for saved in (False,True):
            with self.subTest(saved=saved):
                worker=self.worker();track=worker.tracks[0];lib=worker.library
                worker.preferences=analysis.options(dict(bpm=False,key=True))
                record=self.record(str(track.path));key=analysis.result_key(track.path)
                newest=local_grid.edit(local_grid.edit(record,'shift',1),'shift',1)
                newest_before=deepcopy(newest)
                worker.result={key:{**record,'key':'9A','beats':[],'bpm':None}}
                jobs=analysis_jobs.admit([],lib,[track],worker.preferences)
                worker.job_id=jobs[0]['id']
                seed=dict(version=1,id=str(uuid4()),track_id=track.track_id,path=str(track.path),
                    epoch=list(record['epoch']),native_ready=False,sha256='',duration=10,
                    source='',library_id='',grid=deepcopy(record['beats']),cues=[],
                    operations=[],prepared=None,error='')
                selected=prep_edits.operation(seed,'cue','add',page='hot',slot=0,position=777)
                selected_before=deepcopy(selected)
                unrelated=Untouched({**deepcopy(selected),'track_id':'other',
                                     'id':str(uuid4()),'path':'/other.wav'})
                queue=[selected,unrelated]
                prep=PrepHost()
                host=SimpleNamespace(_shutting_down=False,_state_maintenance=False,_state_publication=False,
                    _analysis_owner=SimpleNamespace(stopping=False),lib=lib,prep_controller=prep,
                    settings={analysis_jobs.KEY:jobs,'analysis_results':{key:newest},prep_edits.KEY:queue},
                    _save_state=mock.Mock(return_value=saved),_invalidate_rows=mock.Mock(),
                    _refresh_analysis_row=mock.Mock(),_flash_status=mock.Mock(),
                    _schedule_next_analysis=mock.Mock(),player_bar=SimpleNamespace(current_track=lambda:None))
                prep.window=host
                AnalysisMixin._analysis_retired(host,worker)
                self.assertEqual(selected,selected_before)
                self.assertEqual(newest,newest_before)
                self.assertIs(queue[0],selected)
                self.assertIs(queue[1],unrelated)
                if saved:
                    after=host.settings[prep_edits.KEY]
                    self.assertIsNot(after,queue)
                    self.assertIsNot(after[0],selected)
                    self.assertIs(after[1],unrelated)
                    self.assertEqual(after[0]['sha256'],record['sha256'])
                    result=host.settings['analysis_results'][key]
                    self.assertEqual(result['grid_draft'],newest['grid_draft'])
                    self.assertEqual(result['cue_draft']['cues'][0]['position_ms'],777)
                    self.assertEqual(result['key'],'9A')
                    self.assertNotIn(worker.job_id,[j['id'] for j in host.settings[analysis_jobs.KEY]])
                else:
                    self.assertIs(host.settings[prep_edits.KEY],queue)
                    self.assertIs(host.settings['analysis_results'][key],newest)
                    self.assertIs(host.settings[analysis_jobs.KEY],jobs)
                    host._invalidate_rows.assert_not_called()
                    self.assertEqual(selected['sha256'],'')

    def wait(self, condition):
        deadline = time.monotonic()+3
        while not condition() and time.monotonic()<deadline:
            QTest.qWait(5)
        self.assertTrue(condition())

    def test_success_publishes_only_after_finished_and_joins_without_reentrant_drain(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: save(w.result))
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            worker.start(); worker.wait()
            save.assert_not_called()
            self.wait(lambda: owner.pending==0)
        save.assert_called_once()

    def test_cancel_after_worker_finished_before_delivery_cannot_save(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: None if owner.stopping else save(w.result))
        with mock.patch('desktop.analysis.analysis.analyze',return_value={'path':'/fixture'}), \
             mock.patch('desktop.analysis.analysis.merge_result',return_value={'bound':'result'}):
            worker.start(); worker.wait(); owner.drain()
        save.assert_not_called()
        self.assertEqual(owner.pending,0)

    def test_failure_keeps_previous_data_and_allows_retry(self):
        save = mock.Mock(return_value=True)
        owner = TaskOwner()
        worker = self.worker()
        owner.adopt(worker, lambda w: save(w.result) if not isinstance(w.result,Exception) else None)
        with mock.patch('desktop.analysis.analysis.analyze',side_effect=ValueError('Analysis Lock is on')):
            worker.start(); worker.wait()
            self.assertIn('Analysis Lock',str(worker.result))
            self.wait(lambda: owner.pending==0)
        save.assert_not_called()
