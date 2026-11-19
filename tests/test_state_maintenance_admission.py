"""Backup/restore never closes dependencies of an accepted session cue/grid."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.analysis import AnalysisMixin, AnalysisWorker
from desktop.analysis_priority import AnalysisPriorityMixin
from desktop.settings_page import SettingsPageMixin
from soundbox import analysis, analysis_jobs
from soundbox.state_repository import StateRepository, read_state
from tests import test_prep_grid as grid_fixtures
from tests import test_state_maintenance_cancellation as cancellation_fixtures


class MaintenanceSessionAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def session(self):
        fixture=grid_fixtures.GridInterface();fixture.app=self.app;fixture.setUp()
        def close():
            fixture.shared.analysis_admission=lambda:False
            fixture.doCleanups()
        self.addCleanup(close)
        events=[]
        host=cancellation_fixtures.MaintenanceCancellation.host(self,None,events)
        host.lib=fixture.shared.lib;host.tag_controller=fixture.shared;host.grid_controller=fixture.grid
        host.settings={analysis_jobs.KEY:analysis_jobs.admit([],host.lib,[host.lib.tracks_by_id['7']],{})}
        directory=self.enterContext(tempfile.TemporaryDirectory())
        host._state_repository=StateRepository(Path(directory)/'library.json')
        host._state_repository.save({'settings':host.settings})
        host._save_state=mock.Mock(side_effect=lambda:host._state_repository.save({'settings':host.settings}) or True)
        host._analysis_priority_pending=lambda:AnalysisPriorityMixin._analysis_priority_pending(host)
        host._analysis_source_priority=lambda:AnalysisPriorityMixin._analysis_source_priority(host)
        host._analysis_offer_source_turn=lambda library:AnalysisPriorityMixin._analysis_offer_source_turn(host,library)
        fixture.shared.analysis_admission=host._analysis_source_priority
        fixture.shared.source_admitted=lambda:AnalysisPriorityMixin._analysis_source_admitted(host)
        host._analysis_queue=[];host._analysis_source_turn=None
        host._analysis_owner=SimpleNamespace(stopping=False)
        host._schedule_next_analysis=mock.Mock()
        actor=AnalysisWorker(host.lib,[host.lib.tracks_by_id['7']],analysis.options({}),{})
        host._analysis_worker=actor
        self.addCleanup(actor.deleteLater)
        return host,fixture,events,actor

    def check_refusal_then_normal_drain(self,kind):
        host,fixture,events,actor=self.session()
        if kind=='cue':
            track=host.lib.tracks_by_id['7']
            occupied={cue['slot'] for cue in track.source_cues if cue['kind']=='hot'}
            slot=next(value for value in range(8) if value not in occupied)
            self.assertTrue(fixture.shared.request_cue('7','hot',slot,'add',500))
            queue=fixture.shared._cue_queue;head=queue[0]
        else:
            self.assertTrue(fixture.grid.request('7','shift',1))
            queue=fixture.grid._queue;head=queue[0]
        self.assertTrue(fixture.shared.owned);self.assertFalse(fixture.shared.busy)
        host._settings_save_timer.start(120000);host._dev_timer.start(120000)
        timers=[host._settings_save_timer,host._dev_timer,host._warm_timer,
                host._waveform_recheck_timer,host._volume_refresh_timer]
        timer_states=[(timer.isActive(),timer.interval()) for timer in timers]
        settings=host.settings;jobs=host.settings[analysis_jobs.KEY]
        before=deepcopy(jobs);manifest=host._state_repository.path.read_bytes()
        for restoring in (False,True):
            self.assertFalse(SettingsPageMixin._begin_state_maintenance(host,restoring=restoring))
            self.assertFalse(host._state_maintenance);self.assertFalse(host._state_publication)
            self.assertEqual(events,[]);host._save_state.assert_not_called()
            host._flash_status.assert_called_with('Finish cue/grid updates before backup/restore',8)
            self.assertIs(host.settings,settings);self.assertIs(host.settings[analysis_jobs.KEY],jobs)
            self.assertIs(host._analysis_worker,actor);self.assertFalse(actor.stop.is_set())
            self.assertIs(queue[0],head);self.assertEqual(jobs,before)
            self.assertEqual([(timer.isActive(),timer.interval()) for timer in timers],timer_states)
            self.assertEqual(host._state_repository.path.read_bytes(),manifest)
        # Cancellation completion outside maintenance uses the existing fair
        # turn to drain accepted native work. Its durable detection intent and
        # FIFO survive unchanged; no global scheduling or authority bypass.
        actor.cancel();AnalysisMixin._analysis_retired(host,actor)
        if kind=='grid':fixture.grid._debounce.stop();fixture.grid._ready_tail()
        fixture.wait_for(lambda:not fixture.shared.owned)
        self.assertEqual(queue,[]);self.assertEqual(host.settings[analysis_jobs.KEY],before)
        self.assertEqual(read_state(host._state_repository.path)['settings'][analysis_jobs.KEY],before)
        self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
        self.assertTrue(host._state_maintenance);self.assertTrue(host._state_publication)
        self.assertEqual(read_state(host._state_repository.path)['settings'][analysis_jobs.KEY],before)
        self.assertEqual(host._save_state.call_count,1)
        host._end_state_maintenance()

    def test_accepted_cue_during_analysis_preserves_admission_until_normal_fair_drain(self):
        self.check_refusal_then_normal_drain('cue')

    def test_debounced_grid_during_analysis_preserves_admission_until_normal_fair_drain(self):
        self.check_refusal_then_normal_drain('grid')
