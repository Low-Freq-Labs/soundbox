"""Maintenance cooperates with real worker cancellation protocols, without audio."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication

from desktop.analysis import AnalysisWorker
from desktop.analysis_priority import AnalysisWaveformWorker
from desktop.settings_page import SettingsPageMixin
from soundbox import analysis
from soundbox.library import Library, Track


class MaintenanceCancellation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def host(self,worker,events):
        host=SimpleNamespace(_state_maintenance=False,_state_publication=False,_backup_worker=None,
            _device_coordinator=SimpleNamespace(active=None),_state_repository=SimpleNamespace(writable=True),
            _shutting_down=False,_analysis_worker=worker,tag_controller=SimpleNamespace(owned=False),
            _art_worker=None,_import_location_worker=None,_waveform_rechecks_enabled=False,
            setEnabled=lambda value:events.append(('enabled',value)),_start_pulse=lambda _:None,
            _stop_pulse=lambda:None,_start_art_fill=lambda:None,_maybe_fill_waveforms=lambda:None,
            _repair_import_locations=lambda:None,_flash_status=mock.Mock())
        for name in ('_settings_save_timer','_dev_timer','_warm_timer','_waveform_recheck_timer','_volume_refresh_timer'):
            setattr(host,name,QTimer())
        host._save_state=lambda:events.append(('save',None)) or True
        host._end_state_maintenance=lambda:SettingsPageMixin._end_state_maintenance(host)
        self.addCleanup(host._end_state_maintenance)
        return host

    def test_actual_analysis_workers_with_stop_events_cancel_before_join_and_final_save(self):
        track=Track('one','Generated','Artist','file:///synthetic/never-open.wav')
        library=Library({'one':track},[],'/synthetic/master.db')
        with tempfile.TemporaryDirectory() as directory:
            workers=(AnalysisWorker(library,[track],analysis.options({}),{}),
                AnalysisWaveformWorker(library,track,{'id':'generated'},Path(directory),lambda _:None))
            for worker in workers:
                with self.subTest(worker=type(worker).__name__):
                    events=[];host=self.host(worker,events)
                    self.assertFalse(callable(worker.stop));self.assertTrue(callable(worker.cancel))
                    with mock.patch('desktop.workers._running',side_effect=lambda candidate:
                            candidate is worker and not worker.stop.is_set()), \
                            mock.patch.object(worker,'wait',side_effect=lambda:events.append(('joined',None)) or True):
                        self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
                    self.assertTrue(worker.stop.is_set());self.assertFalse(worker.isRunning())
                    self.assertEqual(events,[('enabled',False),('joined',None),('save',None)])
                    self.assertTrue(host._state_publication)
                    host._end_state_maintenance();worker.deleteLater()

    def test_callable_cancel_then_stop_then_interruption_fallbacks(self):
        for mode in ('cancel-first','stop-only','noncallable-cancel','events-only','interruption-only'):
            with self.subTest(mode=mode):
                worker=QThread();events=[];active=[True];host=self.host(worker,events)
                def release(name):
                    events.append((name,None));active[0]=False
                cancel=mock.Mock(side_effect=lambda:release('cancel'))
                stop=mock.Mock(side_effect=lambda:release('stop'))
                if mode=='cancel-first':worker.cancel=cancel;worker.stop=stop
                elif mode=='stop-only':worker.stop=stop
                elif mode=='noncallable-cancel':worker.cancel=None;worker.stop=stop
                elif mode=='events-only':worker.cancel=None;worker.stop=threading.Event()
                with mock.patch('desktop.workers._running',side_effect=lambda candidate:candidate is worker and active[0]), \
                        mock.patch.object(worker,'requestInterruption',side_effect=lambda:release('interruption')), \
                        mock.patch.object(worker,'wait',side_effect=lambda:events.append(('joined',None)) or True):
                    self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
                selected='cancel' if mode=='cancel-first' else 'stop' if mode in ('stop-only','noncallable-cancel') else 'interruption'
                self.assertEqual(events,[('enabled',False),(selected,None),('joined',None),('save',None)])
                if mode=='cancel-first':cancel.assert_called_once();stop.assert_not_called()
                host._end_state_maintenance();worker.deleteLater()

    def test_actual_finished_delivery_and_join_precede_the_durable_maintenance_boundary(self):
        started=threading.Event();cancelled=threading.Event();events=[]
        class Worker(QThread):
            def __init__(self):super().__init__();self.stop=cancelled
            def cancel(self):events.append(('cancel',None));cancelled.set()
            def run(self):started.set();cancelled.wait(2)
        worker=Worker();host=self.host(worker,events)
        worker.finished.connect(lambda:events.append(('finished',None)),Qt.QueuedConnection)
        worker.start();self.assertTrue(started.wait(1))
        try:
            self.assertTrue(SettingsPageMixin._begin_state_maintenance(host))
            self.assertFalse(worker.isRunning());self.assertTrue(cancelled.is_set())
            self.assertEqual(events,[('enabled',False),('cancel',None),('finished',None),('save',None)])
            self.assertTrue(host._state_publication)
        finally:
            cancelled.set();worker.wait();host._end_state_maintenance();worker.deleteLater()
