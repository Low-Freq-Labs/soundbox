"""Ordered read-only selected export checkpoints on actual TEMP canonical state."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from soundbox.state_repository import InvalidState,StateChangedError,StateRepository,read_state
from soundbox.device_manifest_capture import read_device_manifest
from soundbox.operation_control import OperationCancelled


class SelectedStateAbsence(unittest.TestCase):
    def setUp(self):
        self.area=self.enterContext(tempfile.TemporaryDirectory())
        self.repository=StateRepository(Path(self.area)/'library.json');self.repository.load()
        self.repository.save({'settings':{},'title_overrides':{'saved':'Title'}})

    def test_missing_leaf_and_absent_parent_are_exactly_verified(self):
        r=self.repository
        proof=r.verify_selected_values({('title_overrides','saved'):'Title'},
            missing=(('title_overrides','other'),('settings','track_info','other')))
        self.assertTrue(r.current_verification(proof))
        self.assertTrue(r.contains_selected_path(('title_overrides','saved')))
        self.assertFalse(r.contains_selected_path(('settings','track_info','other')))

    def test_present_empty_leaf_cannot_be_claimed_absent_or_defaults_substituted(self):
        self.repository.save({'settings':{'track_info':{}}})
        with self.assertRaises(StateChangedError):
            self.repository.verify_selected_values({},missing=(('settings','track_info'),))
        with self.assertRaises(StateChangedError):
            self.repository.verify_selected_values({('settings','missing'):{}})

    def test_wrong_parent_type_and_overlapping_missing_paths_are_refused(self):
        self.repository.save({'settings':{'track_info':'invalid-domain'}})
        with self.assertRaises(StateChangedError):
            self.repository.verify_selected_values({},missing=(('settings','track_info','other'),))
        with self.assertRaises(InvalidState):
            self.repository.verify_selected_values({('settings',):{}},missing=(('settings','other'),))

    def test_presence_query_never_reads_files_or_copies_values(self):
        with mock.patch.object(Path,'stat',side_effect=AssertionError('filesystem IO')), \
             mock.patch('soundbox.state_repository.copy.deepcopy',side_effect=AssertionError('copy')):
            self.assertTrue(self.repository.contains_selected_path(('title_overrides','saved')))
            self.assertFalse(self.repository.contains_selected_path(('title_overrides','other')))

    def test_readonly_indices_verify_selected_scalar_without_copying_grid(self):
        self.repository.save({'settings':{'jobs':[{'id':'selected','grid':[[1,0,140.0]]*10000}]}})
        proof=self.repository.verify_selected_values({('settings','jobs',0,'id'):'selected',
            ('settings','jobs',0,'grid',0,2):140.0},missing=(('settings','jobs',1),))
        self.assertTrue(self.repository.current_verification(proof))
        self.assertTrue(self.repository.contains_selected_path(('settings','jobs',0,'grid',0,2)))
        for part in (-1,True):
            with self.subTest(part=part),self.assertRaises(InvalidState):
                self.repository.verify_selected_values({('settings','jobs',part,'id'):'selected'})
        with self.assertRaises(InvalidState):
            self.repository.prepare_patch({('settings','jobs',0,'id'):'replacement'})

    def test_shifted_selected_record_index_refuses_instead_of_rebinding(self):
        self.repository.save({'settings':{'jobs':[{'id':'old'},{'id':'selected'}]}})
        expected={('settings','jobs',1,'id'):'selected'}
        self.repository.verify_selected_values(expected)
        self.repository.save({'settings':{'jobs':[{'id':'selected'}]}})
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)


class IpodExportCheckpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.area)
        self.window=QObject();w=self.window
        w.settings={};w.titles={'song':'Before'};w.overrides={};w.aliases={}
        w._shutting_down=False;w._state_maintenance=False;w._state_publication=False
        w._state_repository=StateRepository(self.root/'library.json');w._state_repository.load()
        w._state_repository.save({'settings':{},'title_overrides':w.titles,'artist_overrides':{},'artist_aliases':{}})
        w.tag_controller=TagEditController(self.root,w,store_factory=lambda _:None)
        self.committer=StateCommitter(w);self.addCleanup(self.close)

    def close(self):
        self.committer.owner.drain();self.window.tag_controller.owner.drain();self.window.deleteLater()
        self.app.processEvents()

    def until(self,condition):
        end=time.monotonic()+4
        while not condition() and time.monotonic()<end:self.app.processEvents();QTest.qWait(1)
        self.assertTrue(condition(),'Checkpoint did not retire')

    def capture(self):
        return self.window.titles['song'],{('title_overrides','song'):self.window.titles['song']},()

    def test_waits_only_current_native_owner_then_verifies_without_canonical_write(self):
        w=self.window;w.tag_controller.busy=True;ready=mock.Mock();failed=mock.Mock();capture=mock.Mock(side_effect=self.capture)
        before=w._state_repository.path.read_bytes();threads=[];original=w._state_repository.verify_selected_values
        def verify(*args,**kwargs):threads.append(threading.get_ident());return original(*args,**kwargs)
        with mock.patch.object(w._state_repository,'verify_selected_values',side_effect=verify):
            self.committer.queue_checkpoint(capture,ready,failed,current=lambda:True)
            capture.assert_not_called();self.assertTrue(self.committer.pending)
            w.tag_controller.busy=False;w.tag_controller.idle.emit()
            self.until(lambda:not self.committer.pending)
        ready.assert_called_once_with('Before');failed.assert_not_called();capture.assert_called_once()
        self.assertNotEqual(threads,[threading.get_ident()]);self.assertEqual(len(threads),1)
        self.assertEqual(w._state_repository.path.read_bytes(),before)
        self.assertFalse(w._state_publication);self.assertFalse(w.tag_controller.state_commit_owned)

    def test_checkpoint_captures_after_older_canonical_ack_and_keeps_that_generation(self):
        w=self.window;entered=threading.Event();release=threading.Event();original=w._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(3):raise RuntimeError('Generated commit timed out')
            return original(patch)
        ready=mock.Mock();failed=mock.Mock()
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=held):
            self.assertTrue(self.committer.submit({('title_overrides','song'):'Older ACK'},(),lambda:None,failed))
            self.assertTrue(entered.wait(2))
            self.committer.queue_checkpoint(self.capture,ready,failed,current=lambda:True)
            ready.assert_not_called();release.set();self.until(lambda:not self.committer.pending)
        ready.assert_called_once_with('Older ACK');failed.assert_not_called()
        self.assertTrue(self.committer.submit({('title_overrides','song'):'Newer edit'},(),lambda:None,failed))
        self.until(lambda:not self.committer.pending)
        self.assertEqual(read_state(w._state_repository.path)['title_overrides']['song'],'Newer edit')
        self.assertEqual(ready.call_args.args[0],'Older ACK')

    def test_unsaved_selected_curation_never_becomes_success(self):
        self.window.titles={'song':'Unsaved'};ready=mock.Mock();failed=mock.Mock()
        self.committer.queue_checkpoint(self.capture,ready,failed,current=lambda:True)
        self.until(lambda:not self.committer.pending)
        ready.assert_not_called();failed.assert_called_once()
        self.assertEqual(read_state(self.window._state_repository.path)['title_overrides']['song'],'Before')

    def test_cancelled_unstarted_checkpoint_does_not_capture_or_leak_ownership(self):
        self.window.tag_controller.busy=True;capture=mock.Mock(side_effect=self.capture);ready=mock.Mock();failed=mock.Mock()
        token=self.committer.queue_checkpoint(capture,ready,failed,current=lambda:True)
        self.assertTrue(self.committer.cancel_checkpoint(token))
        self.until(lambda:not self.committer.pending)
        capture.assert_not_called();ready.assert_not_called();failed.assert_called_once()

    def test_cancel_or_restore_during_worker_verification_refuses_delivery(self):
        for action in ('cancel','restore','source'):
            with self.subTest(action=action):
                entered=threading.Event();release=threading.Event();current=[True]
                ready=mock.Mock();failed=mock.Mock();repository=self.window._state_repository
                original=repository.verify_selected_values
                def held(*args,**kwargs):
                    entered.set()
                    if not release.wait(3):raise RuntimeError('Generated verification timed out')
                    return original(*args,**kwargs)
                with mock.patch.object(repository,'verify_selected_values',side_effect=held):
                    token=self.committer.queue_checkpoint(self.capture,ready,failed,current=lambda:current[0])
                    self.until(entered.is_set)
                    if action=='cancel':self.committer.cancel_checkpoint(token)
                    elif action=='restore':repository.area.generation+=1
                    else:current[0]=False
                    release.set();self.until(lambda:not self.committer.pending)
                ready.assert_not_called();failed.assert_called_once()
                self.assertFalse(self.window._state_publication)
                self.assertFalse(self.window.tag_controller.state_commit_owned)
                if action=='restore':repository.load()

    def test_capture_failure_retires_once_and_next_checkpoint_still_runs(self):
        self.window.tag_controller.busy=True;failed=mock.Mock();ready=mock.Mock()
        self.committer.queue_checkpoint(mock.Mock(side_effect=ValueError('Selected identity changed')),ready,failed,current=lambda:True)
        self.committer.queue_checkpoint(self.capture,ready,failed,current=lambda:True)
        self.window.tag_controller.busy=False;self.window.tag_controller.idle.emit()
        self.until(lambda:not self.committer.pending)
        failed.assert_called_once();ready.assert_called_once_with('Before')

    def test_ownership_exception_after_worker_completion_retires_without_success(self):
        ready=mock.Mock();failed=mock.Mock();state={'raise':False}
        def current():
            if state['raise']:raise RuntimeError('Ownership provider changed')
            return True
        original=self.window._state_repository.verify_selected_values
        def verify(*args,**kwargs):
            result=original(*args,**kwargs);state['raise']=True;return result
        with mock.patch.object(self.window._state_repository,'verify_selected_values',side_effect=verify), \
             self.assertLogs('soundbox',level='ERROR'):
            self.committer.queue_checkpoint(self.capture,ready,failed,current=current)
            self.until(lambda:not self.committer.pending)
        ready.assert_not_called();failed.assert_called_once()
        self.assertFalse(self.window._state_publication)
        self.assertFalse(self.window.tag_controller.state_commit_owned)

    def test_cancel_checkpoint_aborts_real_chunked_manifest_read_and_joins_before_release(self):
        target=self.root/'generated-device';(target/'.soundbox').mkdir(parents=True)
        payload=b'x'*(2*1024*1024);(target/'.soundbox/manifest.json').write_bytes(payload)
        entered=threading.Event();release=threading.Event();ready=mock.Mock();failed=mock.Mock();sizes=[]
        def metric(name,value=1,**_options):
            if name=='device_manifest_capture_bytes':
                sizes.append(value);entered.set();release.wait(3)
        before=self.window._state_repository.path.read_bytes()
        with mock.patch('soundbox.device_manifest_capture.record_metric',side_effect=metric), \
             mock.patch.object(self.window._state_repository,'verify_selected_values',
                 side_effect=AssertionError('Cancelled read reached canonical verification')):
            token=self.committer.queue_checkpoint(self.capture,ready,failed,current=lambda:True,
                validate=lambda _payload:read_device_manifest(target))
            try:
                self.until(entered.is_set);self.assertTrue(self.committer.cancel_checkpoint(token))
                self.assertTrue(self.window.tag_controller.state_commit_owned)
                release.set();self.until(lambda:not self.committer.pending)
            finally:release.set()
        ready.assert_not_called();failed.assert_called_once()
        self.assertIsInstance(failed.call_args.args[0],OperationCancelled)
        self.assertEqual(sizes,[1024*1024]);self.assertEqual(self.committer.owner.pending,0)
        self.assertFalse(self.window.tag_controller.state_commit_owned)
        self.assertFalse(self.window._state_publication)
        self.assertEqual(self.window._state_repository.path.read_bytes(),before)
        self.assertEqual((target/'.soundbox/manifest.json').read_bytes(),payload)
