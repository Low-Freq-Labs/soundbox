"""Quiet preference writes wait for canonical admission and joined readback."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
import weakref
from unittest import mock

from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from desktop.usb_device_view import UsbSnapshot
from desktop.usb_settings import QUEUE,UsbSettingsController
from soundbox.cdj_usb import CdjLibrary
from soundbox.device_operations import DeviceOperationCoordinator,DeviceSessionIdentity
from soundbox.device_profile import DeviceProfile
from soundbox.state_repository import StateRepository,read_state
from tests.test_tag_creation_controller import Window


class UsbAutoSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=w=Window(self.root)
        w.lib=replace(w.lib,source=str(self.root/'generated-source.xml'),source_kind='xml')
        w.titles={};w.overrides={};w.aliases={};w._state_publication=False
        w._state_repository=StateRepository(self.root/'state/library.json')
        w._state_repository.save(dict(settings={'unrelated':{'keep':[1,2]}},title_overrides={},artist_overrides={},artist_aliases={}))
        w.settings={'unrelated':{'keep':[1,2]}}
        w.tag_controller=TagEditController(self.root,w,store_factory=lambda _:None)
        w.tag_controller.lib=w.lib
        w._state_committer=StateCommitter(w)
        w._commit_tag_state=lambda *a,**k:MainWindow._commit_tag_state(w,*a,**k)
        w._device_coordinator=DeviceOperationCoordinator()
        w._device_mount_tokens={};w._device_profiles={};w._usb_view_cache={}
        w._usb_settings_worker=None;w._current_device_path='';w._current_device_role=''
        w.cancel_btn=QPushButton(w);w.cancel_btn.hide();w._update_device_action_state=mock.Mock()
        w._start_device_worker=lambda worker,dev,intent:MainWindow._start_device_worker(w,worker,dev,intent)
        w._finish_worker=lambda attr,worker:MainWindow._finish_worker(w,attr,worker)
        w._present_usb_device=mock.Mock();w._start_pulse=mock.Mock();w._stop_pulse=mock.Mock()
        w.usb_device_pages=mock.Mock();w.usb_device_pages.settings_binding=None
        self.facts={};self.started=[];self.written=[];self.release=Event();self.release.set();self.entered=Event()
        self.failure='';self.external_name='External baseline'
        def operation(job,sources,guard):
            self.started.append((deepcopy(job),sources))
            def run(cancel,_progress):
                canonical=read_state(w._state_repository.path)['settings'].get(QUEUE,[])
                self.assertTrue(any(row['operation_id']==job['operation_id'] for row in canonical))
                self.entered.set()
                if not self.release.wait(4):raise RuntimeError('Held owned fixture timed out')
                if cancel() or not guard():raise RuntimeError('Captured session changed')
                if self.failure:raise OSError(self.failure)
                current=w._usb_view_cache[job['device']]
                self.written.append(deepcopy(job['changes']))
                native=replace(current.native,name=job['changes'].get('device_name',self.external_name),
                    background_plus=job['changes'].get('background_plus',current.native.background_plus))
                return replace(current,native=native)
            return run
        self.factory=operation
        self.controller=w.usb_settings_controller=UsbSettingsController(w,operation_factory=operation,
            facts_factory=lambda dev:self.facts[dev])
        self.binding=self.attach('USB-A','uuid-A',7)
        w.usb_device_pages.settings_binding=self.binding
        self.addCleanup(self.close)

    def attach(self,name,uuid,token):
        w=self.window;dev=str(self.root/name)
        self.facts[dev]=DeviceFacts(volume_uuid=uuid,writable=True)
        w._device_profiles[dev]=DeviceProfile('generic_usb','USB','exfat',False,False,False,False,False)
        w._device_mount_tokens[dev]=token
        w._device_coordinator.observe(DeviceSessionIdentity(dev,token))
        snapshot=UsbSnapshot(Path(dev),token,self.facts[dev],native=CdjLibrary((),'Original',0,0,(),0,()))
        w._usb_view_cache[dev]=snapshot
        return snapshot,'generic_usb'

    def close(self):
        self.release.set();self.controller.stop()
        worker=self.window._usb_settings_worker
        if worker is not None:worker.cancel();worker.wait();self.app.processEvents()
        self.window._state_committer.owner.drain();self.window.tag_controller.owner.drain()
        self.window.deleteLater();self.app.processEvents()

    def wait(self,condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:self.app.processEvents();QTest.qWait(1)
        self.assertTrue(condition(),'Owned USB settings fixture did not settle')

    def settled(self):
        return not self.controller.admission_pending and not self.window._state_committer.pending

    @contextmanager
    def held_commit(self):
        entered,release=Event(),Event();original=self.window._state_repository.commit_patch
        def commit(patch):
            entered.set()
            if not release.wait(4):raise RuntimeError('Held canonical commit fixture timed out')
            return original(patch)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=commit):
            try:yield entered,release
            finally:release.set();self.wait(self.settled)

    def test_admission_precedes_usb_work_and_ack_follows_join_without_footer(self):
        w=self.window;c=self.controller;self.release.clear()
        with self.held_commit() as (entered,release):
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}))
            self.assertTrue(entered.wait(2));self.assertTrue(c.admission_pending)
            c.pump();self.assertFalse(self.started);self.assertNotIn(QUEUE,w.settings)
            release.set()
        self.wait(lambda:self.entered.is_set())
        worker=w._usb_settings_worker
        self.assertIs(w._device_coordinator.active.worker,worker)
        self.assertTrue(w.cancel_btn.isHidden())
        self.assertTrue(c.busy);self.assertEqual(len(read_state(w._state_repository.path)['settings'][QUEUE]),1)
        self.release.set();self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertIsNone(w._usb_settings_worker);self.assertFalse(w._device_coordinator.busy)
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Chosen')
        w._start_pulse.assert_not_called();w._stop_pulse.assert_not_called();w._flash_status.assert_not_called()
        self.assertEqual(read_state(w._state_repository.path)['settings']['unrelated'],{'keep':[1,2]})

    def test_rapid_durable_edits_coalesce_latest_fields_and_preserve_fresh_unrelated_baseline(self):
        c=self.controller;w=self.window
        self.release.clear()
        self.assertTrue(c.enqueue(self.binding,{'device_name':'First'}))
        self.assertTrue(c.enqueue(self.binding,{'device_name':'Latest','background_plus':3}));self.wait(self.settled)
        self.assertEqual(len(read_state(w._state_repository.path)['settings'][QUEUE]),2)
        self.release.set()
        self.wait(lambda:not c.pending)
        self.assertEqual(self.written,[{'device_name':'Latest','background_plus':3}])
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Latest')
        # A later sparse color delta must preserve a name changed externally.
        binding=(w._usb_view_cache[str(self.binding[0].target)],'generic_usb')
        self.assertTrue(c.enqueue(binding,{'background_plus':2}));self.wait(lambda:not c.pending)
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,self.external_name)

    def test_later_edit_while_worker_held_is_durable_and_never_lost_by_head_ack(self):
        c=self.controller;w=self.window;self.release.clear()
        self.assertTrue(c.enqueue(self.binding,{'device_name':'First'}));self.wait(lambda:self.entered.is_set())
        self.assertTrue(c.enqueue(self.binding,{'device_name':'Newer'}));self.wait(self.settled)
        self.assertEqual(len(read_state(w._state_repository.path)['settings'][QUEUE]),2)
        self.release.set();self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(self.written,[{'device_name':'First'},{'device_name':'Newer'}])
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Newer')

    def test_gesture_during_held_ack_keeps_its_separate_durable_tail(self):
        c=self.controller;w=self.window;entered,release=Event(),Event()
        original=w._state_repository.commit_patch
        def commit(patch):
            replacements,_=patch.edits()
            if replacements.get(('settings',QUEUE))==[] and not entered.is_set():
                entered.set()
                if not release.wait(4):raise RuntimeError('Held ACK fixture timed out')
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=commit):
            try:
                self.assertTrue(c.enqueue(self.binding,{'device_name':'First'}))
                self.wait(lambda:entered.is_set())
                self.assertTrue(c.enqueue(self.binding,{'device_name':'Newer'}))
                self.assertTrue(c.admission.pending)
                self.assertEqual(len(read_state(w._state_repository.path)['settings'][QUEUE]),1)
            finally:release.set()
            self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(self.written,[{'device_name':'First'},{'device_name':'Newer'}])
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Newer')

    def test_remount_after_join_during_canonical_ack_keeps_new_cache_and_retires_only_saved_intent(self):
        c=self.controller;w=self.window;entered,release=Event(),Event()
        original=w._state_repository.commit_patch
        def commit(patch):
            replacements,_=patch.edits()
            if replacements.get(('settings',QUEUE))==[]:
                entered.set()
                if not release.wait(4):raise RuntimeError('Held ACK fixture timed out')
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=commit):
            try:
                self.assertTrue(c.enqueue(self.binding,{'device_name':'Saved original USB'}))
                self.wait(lambda:entered.is_set())
                self.assertIsNone(w._usb_settings_worker)
                fresh=self.attach('USB-A','new-occupant-uuid',8)
                w.usb_device_pages.settings_binding=fresh
            finally:release.set()
            self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertIs(w._usb_view_cache[str(fresh[0].target)],fresh[0])
        self.assertEqual(self.written,[{'device_name':'Saved original USB'}])
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],[])
        w._present_usb_device.assert_not_called()

    def test_failure_is_durable_inline_and_retry_cannot_target_another_usb(self):
        c=self.controller;w=self.window;self.failure='Owned write failure'
        self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}))
        self.wait(lambda:bool(c.pending and c.pending[0]['error']) and self.settled())
        canonical=read_state(w._state_repository.path)['settings'][QUEUE]
        self.assertEqual(canonical[0]['error'],'Owned write failure');self.assertFalse(c.busy)
        from desktop.quit_saves import failed_intentions
        self.assertEqual(failed_intentions(w.settings)[QUEUE],canonical)
        self.assertTrue(w.usb_device_pages.set_settings_error.called)
        other=self.attach('USB-B','uuid-B',9)
        self.assertFalse(c.retry(other));self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],canonical)
        self.failure='';self.assertTrue(c.retry(self.binding));self.wait(lambda:not c.pending)
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Chosen')

    def test_admission_storage_failure_preserves_ram_intention_without_writing_usb(self):
        c=self.controller;w=self.window
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=OSError('No space for intent')):
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}))
            self.wait(lambda:bool(c.admission_error))
        self.assertEqual(c.pending[0]['changes'],{'device_name':'Chosen'})
        self.assertFalse(self.started);self.assertNotIn(QUEUE,read_state(w._state_repository.path)['settings'])
        self.assertFalse(c.flush_admission())
        self.assertTrue(c.retry(self.binding));self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(self.written,[{'device_name':'Chosen'}])

    def test_joined_nested_failure_hands_off_error_without_retaining_worker(self):
        class AnnotatedError(RuntimeError):
            def __init__(self,*args):
                super().__init__(*args);self.detail={'owned':[1,2,3]}
        w=self.window;c=self.controller;attempts=[];workers=[];handoffs=[];joins=[]
        def factory(job,_sources,guard):
            attempt=len(attempts);attempts.append(deepcopy(job['changes']))
            def operation(_cancel,_progress):
                if not guard():raise RuntimeError('Generated session changed')
                if attempt==0:
                    try:raise ValueError('Generated nested cause')
                    except ValueError as cause:raise AnnotatedError('Generated outer error',23) from cause
                self.written.append(deepcopy(job['changes']))
                current=w._usb_view_cache[job['device']]
                return replace(current,native=replace(current.native,name=job['changes']['device_name']))
            return operation
        c.factory=factory
        start=w._start_device_worker;finish=w._finish_worker;completed=c._finished
        def started(worker,device,intent):
            workers.append(weakref.ref(worker));return start(worker,device,intent)
        def joined(attr,worker):
            payload=worker.result;finish(attr,worker)
            joins.append(worker.isFinished() and worker.wait(0) and worker.result is payload)
        def delivered(worker):
            error=worker.result
            if isinstance(error,AnnotatedError):
                before=(type(error),error.args,str(error),deepcopy(error.__dict__),
                        error.__traceback__,error.__cause__,error.__context__,error.__suppress_context__)
                completed(worker)
                after=(type(error),error.args,str(error),deepcopy(error.__dict__),
                       error.__traceback__,error.__cause__,error.__context__,error.__suppress_context__)
                handoffs.append(dict(preserved=before==after,traceback=before[4] is not None,
                    cause=isinstance(error.__cause__,ValueError) and str(error.__cause__)=='Generated nested cause',
                    context=error.__context__ is error.__cause__,detail=error.detail,
                    args=error.args,payload_released=worker.result is None,
                    no_failed_readback=c._completed[1] is None))
            else:
                completed(worker)
        w._start_device_worker=started;w._finish_worker=joined;c._finished=delivered
        self.assertTrue(c.enqueue(self.binding,{'device_name':'Recovered'}))
        self.wait(lambda:bool(c.pending and c.pending[0]['error']) and self.settled())
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE][0]['error'],
                         "('Generated outer error', 23)")
        self.assertEqual(handoffs,[dict(preserved=True,traceback=True,cause=True,context=True,
            detail={'owned':[1,2,3]},args=('Generated outer error',23),payload_released=True,no_failed_readback=True)])
        self.assertEqual(joins,[True])
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertIsNone(workers[0](),'Joined failure retained its QThread wrapper')
        self.assertTrue(c.retry(self.binding));self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(attempts,[{'device_name':'Recovered'}]*2)
        self.assertEqual(self.written,[{'device_name':'Recovered'}])
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Recovered')
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],[])
        self.assertEqual(joins,[True,True])
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertTrue(all(reference() is None for reference in workers))

    def test_failed_joined_success_ack_retries_without_second_usb_write_and_has_quit_proof(self):
        c=self.controller;w=self.window;original=w._state_repository.commit_patch
        def commit(patch):
            replacements,_=patch.edits()
            if replacements.get(('settings',QUEUE))==[]:raise OSError('ACK disk failure')
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=commit):
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}))
            self.wait(lambda:bool(c.ack.error))
        self.assertIsNone(w._usb_settings_worker);self.assertFalse(w._device_coordinator.busy)
        self.assertEqual(self.written,[{'device_name':'Chosen'}])
        captured=c.ack.retention_capture();self.assertIsNotNone(captured)
        self.assertTrue(c.ack.retention_current(captured))
        w._state_repository.verify_selected_values({('settings',QUEUE):captured['before']})
        self.assertTrue(c.retry(self.binding));self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(self.written,[{'device_name':'Chosen'}])
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],[])

    def test_default_operation_uses_one_joined_worker_for_plan_commit_and_fresh_readback(self):
        import threading
        c=self.controller;w=self.window;c.factory=c._operation
        w._data_file=self.root/'state/library.json'
        plan=mock.Mock();phases=[];main_thread=threading.get_ident()
        def record(name):
            self.assertNotEqual(threading.get_ident(),main_thread)
            self.assertIs(w._device_coordinator.active.worker,w._usb_settings_worker)
            phases.append(name)
        def planning(*args,**kwargs):
            record('plan');self.assertEqual(args,(str(self.binding[0].target),{'device_name':'Chosen'}))
            self.assertEqual(kwargs['sources'],(Path(w.lib.source),))
            with mock.patch('desktop.usb_settings.source_paths',side_effect=AssertionError('Repeated full namespace scan')):
                for _ in range(20):self.assertFalse(kwargs['should_cancel']())
            return plan
        def committing(actual,**kwargs):
            record('commit');self.assertIs(actual,plan)
            with mock.patch('desktop.usb_settings.source_paths',side_effect=AssertionError('Repeated full namespace scan')):
                for _ in range(20):self.assertFalse(kwargs['should_cancel']())
        def read(*args):
            record('read');return replace(self.binding[0],native=replace(self.binding[0].native,name='Chosen'))
        with mock.patch('soundbox.cdj_export.plan_settings',side_effect=planning), \
             mock.patch('soundbox.cdj_transaction.commit',side_effect=committing), \
             mock.patch('desktop.usb_device_view.read_native_usb_snapshot',side_effect=read):
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}));self.wait(lambda:not c.pending)
        self.assertEqual(phases,['plan','commit','read']);plan.close.assert_called_once_with()
        self.assertIsNone(w._usb_settings_worker)

    def test_restart_never_replays_saved_mount_session_until_explicit_same_uuid_retry(self):
        c=self.controller;w=self.window
        with mock.patch.object(c,'schedule'):
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Chosen'}));self.wait(self.settled)
        c.stop();self.controller=w.usb_settings_controller=UsbSettingsController(w,
            operation_factory=self.factory,facts_factory=lambda dev:self.facts[dev])
        c=self.controller;c.schedule()
        self.wait(lambda:bool(c.pending and c.pending[0]['error']) and self.settled())
        self.assertFalse(self.written)
        remounted=self.attach('USB-A','uuid-A',8)
        self.assertTrue(c.retry(remounted));self.wait(lambda:not c.pending)
        self.assertEqual(self.written,[{'device_name':'Chosen'}])

    def test_finished_but_undelivered_old_mount_success_cannot_replace_new_mount_cache(self):
        c=self.controller;w=self.window;self.release.clear()
        self.assertTrue(c.enqueue(self.binding,{'device_name':'Old session'}))
        self.wait(lambda:self.entered.is_set());worker=w._usb_settings_worker
        self.release.set();self.assertTrue(worker.wait(2000))
        self.assertEqual(worker.result.native.name,'Old session')
        self.assertIs(w._usb_settings_worker,worker)  # finished delivery is still owned
        fresh=self.attach('USB-A','uuid-A',8);w.usb_device_pages.settings_binding=fresh
        self.wait(lambda:bool(c.pending and c.pending[0]['error']) and self.settled())
        self.assertIs(w._usb_view_cache[str(fresh[0].target)],fresh[0])
        self.assertIsNone(w._usb_settings_worker);self.assertFalse(w._device_coordinator.busy)
        self.assertEqual(w.usb_device_pages.set_settings_error.call_args.args[1],fresh)
        self.assertTrue(read_state(w._state_repository.path)['settings'][QUEUE][0]['error'])

    def test_real_held_frame_never_borrows_same_token_new_mount_epoch(self):
        from desktop.usb_device_view import UsbDevicePages
        c=self.controller;w=self.window;dev=str(self.binding[0].target)
        pages=w.usb_device_pages=UsbDevicePages(w)
        old=w._device_coordinator.mounts[dev]
        pages.set_snapshot(self.binding[0],name='Generated',profile=w._device_profiles[dev],
                           free=500,drift=None,attachment=old)
        original_capture=c._capture(self.binding)
        self.assertIsNotNone(original_capture)
        c._captures['prior-runtime-capture']=original_capture
        w._device_coordinator.disconnect(dev)
        w._device_coordinator.observe(DeviceSessionIdentity(dev,self.binding[0].mount_token))
        fresh_mount=w._device_coordinator.mounts[dev]
        self.assertNotEqual(fresh_mount.mount_epoch,old.mount_epoch)
        # Even a surviving cache pointer cannot upgrade the old visible frame.
        self.assertFalse(c.enqueue(self.binding,{'device_name':'Stale cached frame'}))
        w._usb_view_cache.pop(dev)
        # The old runtime capture and held widget frame grant no replacement
        # mount authority after discovery evicts that cache either.
        self.assertFalse(c.enqueue(self.binding,{'device_name':'Stale held frame'}))
        w.usb_device_pages=mock.Mock(settings_binding=None)
        self.assertFalse(c.enqueue(self.binding,{'device_name':'Stale runtime capture'}))
        w.usb_device_pages=pages
        self.assertFalse(c.pending);self.assertFalse(self.started)
        fresh=replace(self.binding[0],native=replace(self.binding[0].native,name='Fresh read'))
        w._usb_view_cache[dev]=fresh
        pages.set_snapshot(fresh,name='Generated',profile=w._device_profiles[dev],
                           free=500,drift=None,attachment=fresh_mount)
        self.assertTrue(c.enqueue((fresh,'generic_usb'),{'device_name':'New gesture'}))
        self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertEqual(self.written,[{'device_name':'New gesture'}])

    def test_mount_source_path_growth_and_restore_fence_old_worker_publication(self):
        for change in ('mount','source','paths','restore'):
            with self.subTest(change=change):
                # Each loop owns a fresh controller/queue; prior payloads are
                # completed before resetting this generated canonical fixture.
                c=self.controller;w=self.window;self.release.clear();self.entered.clear()
                binding=(w._usb_view_cache[str(self.binding[0].target)],'generic_usb')
                self.assertTrue(c.enqueue(binding,{'device_name':change}));self.wait(lambda:self.entered.is_set())
                before=len(self.written);original_lib=w.lib
                if change=='mount':
                    w._device_coordinator.disconnect(str(binding[0].target))
                    replacement=self.attach('USB-A','uuid-A',binding[0].mount_token+1)
                elif change=='source':w.lib=replace(w.lib,source=str(self.root/'other.xml'))
                elif change=='paths':
                    from soundbox.library import Track
                    w.lib=replace(w.lib,tracks_by_id={'new':Track('new','Generated','Artist',(self.root/'new-path.wav').as_uri())})
                else:
                    # Simulate the already-completed Restore epoch boundary;
                    # production admission blocks beginning this while owned.
                    restored=StateRepository(self.root/'restored/library.json')
                    restored.save(dict(settings={'restored':True},title_overrides={},artist_overrides={},artist_aliases={}))
                    w._state_repository=restored;w.settings={'restored':True}
                self.release.set();self.wait(lambda:w._usb_settings_worker is None)
                self.assertEqual(len(self.written),before)
                self.assertNotEqual(w._usb_view_cache[str(binding[0].target)].native.name,change)
                if change=='restore':
                    self.assertEqual(read_state(w._state_repository.path)['settings'],{'restored':True})
                    self.assertFalse(c.pending);break
                self.wait(lambda:bool(c.pending and c.pending[0]['error']) and self.settled())
                w.lib=original_lib
                current=replacement if change=='mount' else binding
                self.assertTrue(c.retry(current));self.wait(lambda:not c.pending)

    def test_read_only_and_import_path_owner_refuse_without_canonical_intent(self):
        c=self.controller;w=self.window;dev=str(self.binding[0].target)
        self.facts[dev]=replace(self.facts[dev],writable=False)
        self.assertFalse(c.enqueue(self.binding,{'device_name':'Read only'}))
        self.facts[dev]=replace(self.facts[dev],writable=True)
        w._import_tasks=mock.Mock(pending=1)
        self.assertFalse(c.enqueue(self.binding,{'device_name':'Import owned'}));del w._import_tasks
        self.assertFalse(c.pending);self.assertNotIn(QUEUE,read_state(w._state_repository.path)['settings'])

    def test_other_joined_device_owner_delays_usb_write_but_never_rejects_durable_gesture(self):
        from desktop.cdj import CdjWorker
        c=self.controller;w=self.window
        other_binding=self.attach('USB-B','uuid-B',9)
        entered,release=Event(),Event();self.addCleanup(release.set)
        def operation(cancel,_progress):
            entered.set()
            if not release.wait(4):raise RuntimeError('Other owned device fixture timed out')
            return None
        other=CdjWorker(operation);w._other_device_worker=other
        def finished():
            w._finish_worker('_other_device_worker',other);c.schedule()
        other.finished.connect(finished)
        w._start_device_worker(other,str(other_binding[0].target),'inspect')
        self.wait(lambda:entered.is_set())
        try:
            self.assertTrue(c.enqueue(self.binding,{'device_name':'Accepted behind other owner'}))
            self.wait(self.settled)
            self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE][0]['changes'],
                             {'device_name':'Accepted behind other owner'})
            c.pump();self.assertFalse(self.started);self.assertFalse(self.written)
            self.assertIs(w._device_coordinator.active.worker,other)
        finally:release.set()
        self.wait(lambda:not c.pending and not c.admission_pending)
        self.assertIsNone(w._other_device_worker)
        self.assertEqual(self.written,[{'device_name':'Accepted behind other owner'}])
        self.assertEqual(w._usb_view_cache[str(self.binding[0].target)].native.name,'Accepted behind other owner')

    def test_invalid_saved_queue_and_unknown_preferences_are_preserved(self):
        c=self.controller;w=self.window
        w.settings[QUEUE]=[{'future':'opaque'}];before=deepcopy(w.settings[QUEUE])
        document=read_state(w._state_repository.path);document['settings'][QUEUE]=deepcopy(before)
        w._state_repository.save(document)
        self.assertTrue(c.queue_error());c.pump()
        self.assertEqual(w.settings[QUEUE],before);self.assertFalse(self.started)
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],before)
        self.assertIn('need attention',w.usb_device_pages.set_settings_error.call_args.args[0])
        w.settings[QUEUE]={'future':'opaque'};before=deepcopy(w.settings[QUEUE])
        document['settings'][QUEUE]=deepcopy(before);w._state_repository.save(document)
        w.usb_device_pages.set_settings_error.reset_mock();c.schedule()
        self.wait(lambda:w.usb_device_pages.set_settings_error.called)
        self.assertEqual(w.settings[QUEUE],before);self.assertFalse(self.started)
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],before)
        w.settings.pop(QUEUE)
        for invalid in ({'file':'/etc/anything'},{'background_plus':True},{'my_settings':{'unknown':{}}}):
            self.assertFalse(c.enqueue(self.binding,invalid))
        self.assertFalse(c.pending)

    def test_complete_window_export_and_eject_reenable_only_after_final_ack(self):
        from PySide6.QtCore import QStandardPaths
        from tests.test_device_navigation_publication import DeviceNavigationPublication
        previous_mode=QStandardPaths.isTestModeEnabled();QStandardPaths.setTestModeEnabled(True)
        self.addCleanup(lambda:QStandardPaths.setTestModeEnabled(previous_mode))
        fixture=DeviceNavigationPublication('test_metadata_ack_and_full_refresh_preserve_every_device_page_without_a_stack_flip')
        fixture.setUpClass();fixture.setUp();self.addCleanup(fixture.doCleanups)
        w=fixture.window;dev=str(self.binding[0].target)
        w._device_profiles[dev]=self.window._device_profiles[dev]
        w._device_mount_tokens[dev]=self.binding[0].mount_token
        w._device_coordinator.observe(DeviceSessionIdentity(dev,self.binding[0].mount_token))
        w._usb_view_cache[dev]=self.binding[0]
        w._usb_device_rows[dev]=('Generated',500,None,w._device_profiles[dev])
        w._current_device_path=dev;w._current_device_role=''
        entered,release=Event(),Event();self.addCleanup(release.set)
        tail_entered,tail_release=Event(),Event();self.addCleanup(tail_release.set)
        calls=[]
        def factory(job,sources,guard):
            def operation(cancel,_progress):
                waiting=tail_release if job['changes']['device_name']=='Newer' else release
                (tail_entered if waiting is tail_release else entered).set()
                if not waiting.wait(4):raise RuntimeError('Held main window fixture timed out')
                if cancel() or not guard():raise RuntimeError('Main window session changed')
                calls.append(job['changes'])
                snapshot=w._usb_view_cache[dev]
                return replace(snapshot,native=replace(snapshot.native,name=job['changes']['device_name']))
            return operation
        controller=w.usb_settings_controller=UsbSettingsController(w,operation_factory=factory,
            facts_factory=lambda _dev:self.facts[dev])
        self.addCleanup(controller.stop)
        w._update_device_action_state()
        self.assertTrue(w.eject_btn.isEnabled());self.assertTrue(w.device_sync_btn.isEnabled())
        self.assertTrue(controller.enqueue(self.binding,{'device_name':'First'}))
        self.wait(lambda:entered.is_set())
        self.assertFalse(w.eject_btn.isEnabled());self.assertFalse(w.device_sync_btn.isEnabled())
        self.assertTrue(w._source_selection_reason())
        self.assertFalse(w._begin_state_maintenance(restoring=True))
        self.assertFalse(w._state_maintenance)
        self.assertIn('USB settings',w._pending_save_reason())
        self.assertTrue(controller.enqueue(self.binding,{'device_name':'Newer'}))
        release.set();self.wait(lambda:tail_entered.is_set())
        self.assertFalse(w.eject_btn.isEnabled());self.assertFalse(w.device_sync_btn.isEnabled())
        tail_release.set();self.wait(lambda:not controller.pending and not controller.admission_pending)
        self.assertEqual(calls,[{'device_name':'First'},{'device_name':'Newer'}])
        self.assertTrue(w.eject_btn.isEnabled());self.assertTrue(w.device_sync_btn.isEnabled())
