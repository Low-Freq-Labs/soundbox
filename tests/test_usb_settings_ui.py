"""Quiet settings retain exact user gestures through real MainWindow routing."""
from copy import deepcopy
from dataclasses import replace
from threading import Event
from time import monotonic
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from shiboken6 import isValid

from desktop.devices import DeviceFacts
from desktop.usb_settings import QUEUE,UsbSettingsController
from soundbox import my_settings
from soundbox.device_operations import DeviceSessionIdentity
from soundbox.state_repository import read_state
from tests import test_device_navigation_publication as fixture


class UsbSettingsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.DeviceNavigationPublication.setUpClass()

    def setUp(self):
        self.case=fixture.DeviceNavigationPublication();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.w=self.case.window;w=self.w
        _item,self.pages,_tree=self.case.select_device('generic_usb')
        self.dev=w._current_device_path;self.app=self.case.app
        self.facts=DeviceFacts(volume_uuid='device-uuid',writable=True)
        w._device_coordinator.observe(DeviceSessionIdentity(self.dev,7))
        native=w._usb_view_cache[self.dev].native
        slots=[(1,4,'genre',0,True),(2,17,'artist',1,True),(3,19,'album',2,True)]
        native=replace(native,preferences=dict(categories=deepcopy(slots),sorts=deepcopy(slots),
            sub_column=4,colors=[(1,'Pink')]),files=tuple(my_settings.new_files({}).items()))
        w._usb_view_cache[self.dev]=replace(w._usb_view_cache[self.dev],facts=self.facts,native=native)
        w._present_usb_device(self.dev)
        self.started=[];self.written=[];self.entered=[];self.holds=[]
        self.controller=UsbSettingsController(w,operation_factory=self.operation,
                                             facts_factory=lambda _dev:self.facts)
        vars(w)['usb_settings_controller']=self.controller
        self.addCleanup(self.release_workers)
        w.resize(1100,900);w.show();self.app.processEvents()

    def release_workers(self):
        for release in self.holds:release.set()
        self.controller.stop()
        worker=getattr(self.w,'_usb_settings_worker',None)
        if worker is not None:worker.cancel();worker.wait();self.app.processEvents()

    def wait(self,condition):
        end=monotonic()+7
        while not condition() and monotonic()<end:self.app.processEvents();QTest.qWait(1)
        self.assertTrue(condition(),'Generated settings fixture did not settle')

    def hold(self,index):
        while len(self.holds)<=index:
            event=Event();event.set();self.holds.append(event)
        self.holds[index].clear()

    def operation(self,job,_sources,guard):
        index=len(self.started);self.started.append(deepcopy(job))
        while len(self.holds)<=index:
            event=Event();event.set();self.holds.append(event)
        entered=Event();self.entered.append(entered)
        def run(cancel,_progress):
            queue=read_state(self.w._state_repository.path)['settings'][QUEUE]
            self.assertTrue(any(row['operation_id']==job['operation_id'] for row in queue))
            entered.set()
            if not self.holds[index].wait(6):raise RuntimeError('Owned writer fixture timed out')
            if cancel() or not guard():raise RuntimeError('Owned settings session changed')
            current=self.w._usb_view_cache[job['device']];native=current.native
            changes=job['changes'];preferences=deepcopy(native.preferences)
            for domain in ('categories','sorts'):
                if domain in changes:
                    order=changes[domain]
                    preferences[domain]=[(identity,menu,name,order.index(menu) if menu in order else seq,
                        menu in order) for identity,menu,name,seq,_visible in preferences[domain]]
            if 'sub_column' in changes:preferences['sub_column']=changes['sub_column']
            profile=my_settings.values(native.files)
            for name,fields in changes.get('my_settings',{}).items():profile.setdefault(name,{}).update(fields)
            result=replace(native,preferences=preferences,files=tuple(my_settings.new_files(profile).items()),
                name=changes.get('device_name',native.name),
                background_plus=changes.get('background_plus',native.background_plus))
            self.written.append(deepcopy(changes))
            return replace(current,native=result)
        return run

    def name(self,value,*,flush=True):
        edit=self.pages.controls['device_name'];edit.selectAll();QTest.keyClicks(edit,value)
        if flush:self.pages.flush_auto_settings()

    def settled(self):
        c=self.controller
        return not c.pending and not c.admission_pending and not self.pages._unadmitted_settings

    def choose(self,control,index):
        control.showPopup();self.app.processEvents()
        QTest.mouseClick(control.view().viewport(),Qt.LeftButton,
                         pos=control.view().visualRect(control.model().index(index,0)).center())

    def test_newer_name_during_failed_ack_retries_through_production_router(self):
        c=self.controller;w=self.w;p=self.pages
        original=w._state_repository.commit_patch
        def fail_ack(patch):
            changes,_=patch.edits()
            if changes.get(('settings',QUEUE))==[]:raise OSError('Generated ACK storage failure')
            return original(patch)
        with mock.patch.object(w._state_repository,'commit_patch',side_effect=fail_ack):
            self.name('First');self.wait(lambda:bool(c.ack.error))
            self.name('Newer')
            self.assertEqual(len(p._unadmitted_settings),1)
            self.assertIn('USB',w._pending_save_reason())
            self.assertEqual(self.written,[{'device_name':'First'}])
        QTest.mouseClick(p.settings_retry,Qt.LeftButton)
        self.wait(self.settled)
        self.assertEqual(w._usb_view_cache[self.dev].native.name,'Newer')
        self.assertEqual(p.controls['device_name'].text(),'Newer')
        self.assertEqual(read_state(w._state_repository.path)['settings'][QUEUE],[])
        self.assertEqual(self.written,[{'device_name':'First'},{'device_name':'Newer'}])

    def test_eject_waits_for_accepted_settings_before_worker_and_through_ack(self):
        c=self.controller;w=self.w;self.hold(0);observed=[]
        def eject_transport(_dev):
            observed.append((self.settled(),read_state(w._state_repository.path)['settings'][QUEUE],
                             w._usb_settings_worker))
        with mock.patch.object(w,'_begin_macos_eject',side_effect=eject_transport) as eject:
            self.name('Before Eject')
            self.assertTrue(c.busy);self.assertIsNone(getattr(w,'_usb_settings_worker',None))
            w._eject(self.dev)
            self.assertFalse(w._device_coordinator.eject_pending);eject.assert_not_called()
            self.wait(lambda:bool(self.entered and self.entered[0].is_set()))
            eject.assert_not_called();self.holds[0].set()
            self.wait(lambda:eject.called)
            eject.assert_called_once_with(self.dev)
        self.assertEqual(observed,[(True,[],None)])
        self.assertEqual(self.written,[{'device_name':'Before Eject'}])
        w._device_coordinator.eject_pending='';w._exporting=False
        w._eject_owner=None;w._eject_session=None

    def test_remount_closes_old_popup_and_cannot_borrow_a_new_attachment(self):
        w=self.w;p=self.pages;c=self.controller
        for token in (8,8):
            with self.subTest(token=token):
                original=p._settings_binding();old_mount=p._presentation_attachment
                control=p.controls['background_plus'];control.showPopup();self.app.processEvents()
                popup=control.view();self.assertTrue(popup.isVisible())
                w._device_coordinator.disconnect(self.dev)
                fresh=DeviceSessionIdentity(self.dev,token)
                w._device_coordinator.observe(fresh);w._device_mount_tokens[self.dev]=token
                self.assertNotEqual(old_mount,fresh)
                w._usb_view_cache.pop(self.dev)
                self.assertFalse(c.enqueue(original,{'background_plus':1}))
                snapshot=replace(original[0],mount_token=token)
                w._usb_view_cache[self.dev]=snapshot;w._present_usb_device(self.dev)
                self.app.processEvents()
                self.assertFalse(isValid(popup) and popup.isVisible())
                w._on_auto_usb_settings(original,{'background_plus':1})
                self.assertFalse(c.pending);self.assertEqual(self.written,[])
                self.assertEqual(len(p._unadmitted_settings),1)
                w._discard_unadmitted_usb_settings()

    def test_unadmitted_input_blocks_quit_restore_and_discard_cancels_debounce(self):
        w=self.w;p=self.pages;c=self.controller
        with mock.patch.object(c,'enqueue',return_value=False), \
                mock.patch.object(w,'_flash_status') as footer:
            self.name('Unadmitted')
            footer.assert_not_called()
        self.assertEqual(len(p._unadmitted_settings),1)
        self.assertIn('USB',w._pending_save_reason())
        with mock.patch.object(w,'_flash_status'):
            self.assertFalse(w._begin_state_maintenance(restoring=True))
            self.assertFalse(w._begin_state_maintenance(restoring=False))
        self.name('Newer debounce',flush=False)
        w._discard_unadmitted_usb_settings()
        QTest.qWait(400);self.app.processEvents()
        self.assertFalse(p._unadmitted_settings);self.assertFalse(c.pending)
        self.assertEqual(self.written,[])
        self.wait(lambda:not getattr(w._state_committer,'pending',False))
        with mock.patch.object(w,'_flash_status'):
            self.assertTrue(w._begin_state_maintenance(restoring=True))
        w._end_state_maintenance()

    def test_older_ack_keeps_newer_order_column_and_player_choices_until_tail_ack(self):
        w=self.w;p=self.pages;c=self.controller;self.hold(0);self.hold(1)
        w._set_device_tab(2);categories=p.categories
        up=next(button for button in categories.findChildren(QPushButton) if button.text()=='Up')
        categories.active.setCurrentRow(1);QTest.mouseClick(up,Qt.LeftButton)
        self.wait(lambda:bool(self.entered and self.entered[0].is_set()))
        categories.active.setCurrentRow(2);QTest.mouseClick(up,Qt.LeftButton)
        desired_order=categories.order()
        w._set_device_tab(4);column=p.column.column
        self.choose(column,column.findData(19));self.assertEqual(column.currentData(),19)
        w._set_device_tab(6);editor=p.my_settings
        self.assertTrue(editor.prepared)
        pair,control=next((pair,control) for pair,control in editor.controls.items()
                          if control.isEnabled() and control.count()>1)
        index=(control.currentIndex()+1)%control.count();chosen=control.itemData(index)
        self.choose(control,index);self.assertEqual(control.currentData(),chosen)
        self.wait(lambda:not c.admission_pending)
        self.holds[0].set();self.wait(lambda:len(self.entered)>1 and self.entered[1].is_set())
        self.assertEqual(categories.order(),desired_order)
        self.assertEqual(column.currentData(),19)
        self.assertEqual(editor.controls[pair].currentData(),chosen)
        self.assertNotEqual(w._usb_view_cache[self.dev].native.preferences['categories'],p._preferences['categories'])
        self.holds[1].set();self.wait(self.settled)
        final=w._usb_view_cache[self.dev].native
        actual_order=[row[1] for row in sorted(final.preferences['categories'],key=lambda row:row[3]) if row[4]]
        self.assertEqual(actual_order,desired_order)
        self.assertEqual(final.preferences['sub_column'],19)
        self.assertEqual(my_settings.values(final.files)[pair[0]][pair[1]],chosen)
        self.assertEqual(categories.order(),desired_order)
        self.assertEqual(column.currentData(),19)
        self.assertEqual(editor.controls[pair].currentData(),chosen)
        self.assertEqual(len(self.written),2)
