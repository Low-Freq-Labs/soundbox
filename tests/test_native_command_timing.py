"""Native dispatch, worker phases and GUI acknowledgement stay distinct."""
from pathlib import Path
from copy import deepcopy
import tempfile
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication

from desktop import native_timing as timing
from desktop.tag_controller import TagEditController,_TagTask
from test_prep_tags import _library


class NativeCommandTiming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def wait(self,predicate):
        loop=QEventLoop();poll=QTimer();poll.setInterval(5)
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
        poll.start();timeout.start(5000)
        if not predicate():loop.exec()
        poll.stop();timeout.stop();self.assertTrue(predicate())

    def test_exact_dispatch_execution_phase_and_delayed_gui_acknowledgement_clocks(self):
        def work():
            with timing.phase('apply_tags'):pass
            return 42
        worker=_TagTask(work,operation='tags',admitted_at=10,enqueued_at=7)
        with mock.patch.object(timing,'now',side_effect=[20,21,23,25]),self.assertLogs(timing.log,level='INFO') as logs:
            worker.run()
        self.assertEqual(worker.result,42)
        self.assertEqual(worker.timing.started_at,20);self.assertEqual(worker.timing.finished_at,25)
        self.assertIn('phase=apply_tags seconds=2.000 outcome=returned',logs.output[0])
        self.assertIn('seconds=5.000 dispatch_wait_seconds=10.000 intent_wait_seconds=3.000',logs.output[1])
        def acknowledgement(captured):
            self.assertIs(captured,worker)
            with timing.phase('publish_library'):pass
            return 'returned value'
        with mock.patch.object(timing,'now',side_effect=[28,29,32,34]),self.assertLogs(timing.log,level='INFO') as logs:
            self.assertEqual(worker.timing.acknowledge(acknowledgement,worker),'returned value')
        self.assertIn('phase=publish_library seconds=3.000',logs.output[0])
        self.assertIn('gui_wait_seconds=3.000 callback_seconds=6.000 admitted_seconds=24.000',logs.output[1])
        self.assertTrue(all('token='+worker.timing.token in message for message in logs.output))
        with self.assertNoLogs(timing.log,level='INFO'):
            with timing.phase('outside_command'):pass

    def test_failures_preserve_original_exceptions_and_cleanup_context_without_logging_data(self):
        secret='private path /Users/person/music/title.wav'
        def fail():
            with timing.phase('apply_tags'):raise OSError(secret)
        worker=_TagTask(fail,operation='tags')
        with self.assertLogs(timing.log,level='INFO') as logs:worker.run()
        self.assertEqual(worker.error,secret)
        self.assertIn('outcome=raised',logs.output[0]);self.assertIn('outcome=failed',logs.output[1])
        self.assertNotIn(secret,'\n'.join(logs.output));self.assertIn('intent_wait_seconds=unknown',logs.output[1])
        original=RuntimeError('callback failure')
        def bad_ack(_worker):raise original
        with self.assertLogs(timing.log,level='INFO') as logs:
            with self.assertRaises(RuntimeError) as error:worker.timing.acknowledge(bad_ack,worker)
        self.assertIs(error.exception,original);self.assertIn('outcome=raised',logs.output[-1])
        with self.assertNoLogs(timing.log,level='INFO'):
            with timing.phase('outside_command'):pass

    def test_real_owned_worker_dispatches_and_acknowledges_on_gui_without_early_retirement(self):
        with tempfile.TemporaryDirectory() as area:
            controller=TagEditController(Path(area));events=[];main=threading.get_ident()
            def work():
                events.append(('worker',threading.get_ident()))
                with timing.phase('read_target'):return 9
            def finished(worker):
                events.append(('gui',threading.get_ident()))
                self.assertEqual(controller.owner.pending,1)
                self.assertFalse(worker.isRunning());self.assertEqual(worker.result,9)
            try:
                with self.assertLogs(timing.log,level='INFO') as logs:
                    self.assertTrue(controller.run_grid_job(work,finished))
                    self.wait(lambda:controller.owner.pending==0)
                self.assertNotEqual(events[0][1],main);self.assertEqual(events[1][1],main)
                self.assertIn('phase=read_target','\n'.join(logs.output))
                self.assertIn('Native command acknowledgement: kind=grid','\n'.join(logs.output))
                self.assertFalse(controller.busy)
            finally:controller.shutdown();controller.deleteLater()

    def test_tag_phases_keep_full_authoritative_refresh_and_time_synchronous_gui_publication(self):
        with tempfile.TemporaryDirectory() as area:
            events=[];library=_library();fresh=deepcopy(library)
            class Store:
                def apply(self,request):
                    events.append('apply')
                    fresh.tracks_by_id[request.tracks[0].track_id].source_tag_ids=[request.tag_id]
            def loader(_source):events.append('refresh');return fresh
            controller=TagEditController(Path(area),store_factory=lambda _:Store(),loader=loader)
            controller.configure(library,True)
            controller._recover_pending=lambda _lib:events.append('recover')
            controller.saved.connect(lambda result:events.append('gui' if result is fresh else 'wrong'))
            try:
                with self.assertLogs(timing.log,level='INFO') as logs:
                    self.assertTrue(controller.request(['2'],'tag',True))
                    self.wait(lambda:controller.owner.pending==0)
                self.assertEqual(events,['recover','apply','refresh','gui'])
                self.assertEqual(library.tracks_by_id['2'].source_tag_ids,[])
                self.assertEqual(fresh.tracks_by_id['2'].source_tag_ids,['tag'])
                messages='\n'.join(logs.output)
                for phase in ('recovery','selected_paths','store','apply_tags','refresh_library','publish_library'):
                    self.assertIn('phase='+phase+' ',messages)
                self.assertNotIn('intent_wait_seconds=unknown',messages)
                self.assertFalse(controller.busy)
            finally:controller.shutdown();controller.deleteLater()

    def test_waiting_outcome_and_fixed_labels_do_not_claim_a_completed_write(self):
        worker=_TagTask(lambda:dict(waiting=True),operation='analysis')
        with self.assertLogs(timing.log,level='INFO') as logs:worker.run()
        self.assertIn('outcome=waiting',logs.output[-1])
        with self.assertRaises(ValueError):timing.CommandTiming('/private/path')
        with self.assertRaises(ValueError):
            with timing.phase('private title with spaces'):pass
