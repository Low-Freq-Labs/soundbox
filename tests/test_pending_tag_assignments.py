"""Future tag gestures retain exact creation custody before native publication."""
from copy import deepcopy
from dataclasses import replace
import threading
import unittest
from unittest import mock

from PySide6.QtTest import QTest

from desktop.app import MainWindow
from desktop.quit_saves import failed_intentions
from desktop.tag_assignment_intents import KEY, validate_job
from desktop.tag_creation_controller import QUEUE, TagDefinitionController
from desktop.tag_controller import TagEditController
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import LibraryTag, TagCategory
from soundbox.state_repository import StateRepository, read_state
from tests import test_tag_async_state as fixture


class PendingTagAssignments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.TagAsyncState.setUpClass()

    def setUp(self):
        self.case=fixture.TagAsyncState();self.case.setUp()
        self.w=self.case.w;self.tags=self.case.tags
        self.w._flash_status=mock.Mock()
        self.w.lib.source_tag_categories=[TagCategory('1','Genre','1')]
        self.case.source.source_tag_categories=list(self.w.lib.source_tag_categories)
        self.tags.saved.disconnect(self.case.publish)
        self.tags.saved.connect(self.publish)
        self.w._publish_created_library=self.publish
        self.events=[];self.creation_error='';outer=self
        class Store:
            def apply(self,request):
                jobs=read_state(outer.w._state_repository.path)['settings'][QUEUE]
                outer.assertEqual(jobs[0]['request'],request)
                if outer.creation_error:raise OSError(outer.creation_error)
                if not any(tag.tag_id==request['tag_id'] for tag in outer.case.source.source_tags):
                    outer.case.source.source_tags.append(LibraryTag(request['tag_id'],request['name'],
                        'Genre',request['tag_uuid'],request['category_id'],request['category_uuid']))
                outer.events.append('create')
                return dict(ID=request['tag_id'])
        self.definitions=TagDefinitionController(self.w,store_factory=lambda _lib:Store(),
            loader=lambda _path:deepcopy(self.case.source))
        self.w.tag_definition_controller=self.definitions
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        apply=self.case.store.apply
        def assign(request):
            self.assertFalse(self.w.settings.get(QUEUE))
            self.assertIsNone(self.definitions.pending_creation(request.tag_id,request.tag_uuid))
            self.assertTrue(any(tag.tag_id==request.tag_id for tag in self.tags.lib.source_tags))
            for target in request.tracks:
                self.assertEqual(request.tag_id in self.case.source.tracks_by_id[target.track_id].source_tag_ids,
                                 target.expected_present)
            self.events.append('assign' if request.present else 'remove')
            apply(request)
        self.case.store.apply=assign
        self.addCleanup(self.close)

    def close(self):
        self.definitions.flush_admission();self.definitions.stop()
        self.case.doCleanups()

    def publish(self,library):
        self.w.lib=library;self.tags.configure(library,True)

    def wait(self,predicate):
        for _ in range(4000):
            if predicate():return
            QTest.qWait(2)
        self.fail('Future tag operation did not settle')

    def creation(self,name='R&B'):
        request=definitions.request_for(self.w.lib.source_library_id,'1','1',name)
        self.assertTrue(self.definitions.enqueue(request))
        return request

    def settled(self):
        self.wait(lambda:not self.definitions.pending and not self.definitions.admission_pending
            and not self.tags.pending and not self.tags.tag_state_lane.pending
            and not self.tags.tag_ack_pending and not self.tags.busy and not self.w._state_committer.pending)

    def test_staged_tag_is_usable_before_durability_then_native_creation_precedes_rapid_gestures(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=self.w._state_repository.commit_patch
        def held(patch):
            if not entered.is_set():entered.set();release.wait(4)
            return original(patch)
        before=deepcopy(self.w.lib)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=held):
            request=self.creation()
            self.wait(entered.is_set)
            tags=self.definitions.available_tags()
            self.assertEqual(tags[-1].name,'R&B')
            self.assertEqual(self.w.lib.source_tags,before.source_tags)
            self.assertFalse(self.w.settings.get(QUEUE))
            for desired in (True,False,True):
                self.assertTrue(self.tags.request(['one','two'],request['tag_id'],desired))
                self.assertEqual(request['tag_id'] in self.tags.selected_tags(['one'])[0],desired)
            self.assertFalse(self.events)
            self.assertFalse(self.w.settings.get(KEY))
            for row in self.tags.tag_state_lane.queue:
                self.assertEqual(row['job']['tag_creation_dependency']['operation_id'],request['operation_id'])
                validate_job(row['job'])
            release.set();self.settled()
        self.assertEqual(self.events,['create','assign','remove','assign'])
        self.assertTrue(all(request['tag_id'] in self.case.source.tracks_by_id[tid].source_tag_ids
                            for tid in ('one','two')))
        self.assertNotIn(request['tag_id'],self.case.source.tracks_by_id['three'].source_tag_ids)

    def test_durable_child_yields_only_dispatch_reservation_and_restarts_with_original_creation(self):
        self.tags.prep_priority=True
        request=self.creation();self.wait(lambda:not self.definitions.admission_pending)
        self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        self.assertTrue(self.tags.owned)
        self.assertFalse(self.tags.assignment_reserved)
        state=read_state(self.w._state_repository.path)
        self.assertEqual(state['settings'][KEY][0]['tag_creation_dependency']['operation_id'],request['operation_id'])
        factory=self.definitions.factory;loader=self.definitions.loader
        self.definitions.stop();self.tags.shutdown()
        self.w._state_repository=StateRepository(self.w._state_repository.path)
        self.w.settings=self.w._state_repository.load()['settings']
        self.tags=TagEditController(self.case.root/'state',self.w,
            store_factory=lambda _lib:self.case.store,loader=lambda _path:deepcopy(self.case.source),intent_owner=self.w)
        self.case.tags=self.tags;self.w.tag_controller=self.tags
        self.tags.prep_priority=True
        self.tags.saved.connect(self.publish);self.tags.configure(deepcopy(self.w.lib),True)
        self.definitions=TagDefinitionController(self.w,store_factory=factory,loader=loader)
        self.w.tag_definition_controller=self.definitions
        self.assertFalse(self.tags.assignment_reserved)
        self.assertEqual(self.tags.selected_tags(['one']),[{request['tag_id']}])
        self.tags.prep_priority=False;self.definitions.schedule();self.tags._schedule_tag_intent();self.settled()
        self.assertEqual(self.events,['create','assign'])

    def test_creation_ack_reservation_holds_canonical_admission_without_delaying_checkbox_preview(self):
        self.tags.prep_priority=True
        request=self.creation();self.wait(lambda:not self.definitions.admission_pending)
        token=object();self.tags.reserve_source_ack(token)
        try:
            self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
            self.assertEqual(self.tags.selected_tags(['one']),[{request['tag_id']}])
            self.tags.tag_state_lane.pump();QTest.qWait(40)
            self.assertFalse(self.tags.tag_state_lane.active)
            self.assertFalse(self.w.settings.get(KEY))
            self.assertFalse(read_state(self.w._state_repository.path)['settings'].get(KEY))
            self.assertFalse(self.events)
        finally:self.tags.release_source_ack(token)
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        self.assertTrue(self.w.settings[KEY])
        self.tags.prep_priority=False;self.definitions.schedule();self.tags._schedule_tag_intent();self.settled()
        self.assertEqual(self.events,['create','assign'])

    def test_failed_creation_ack_exposes_ram_child_failure_and_parent_retry_without_discard(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        factory=self.definitions.factory
        class HeldStore:
            def apply(_self,request):
                entered.set();release.wait(4)
                return factory(self.w.lib).apply(request)
        self.definitions.factory=lambda _lib:HeldStore()
        original=self.w._commit_tag_state
        def fail_ack(replacements,removals,committed,failed,**kwargs):
            if replacements.get(('settings',QUEUE))==[]:
                failed(OSError('Generated definition ACK failure'));return True
            return original(replacements,removals,committed,failed,**kwargs)
        with mock.patch.object(self.w,'_commit_tag_state',side_effect=fail_ack):
            request=self.creation();self.wait(entered.is_set)
            self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
            operation=self.tags.tag_state_lane.queue[0]['request'].operation_id
            release.set()
            self.wait(lambda:self.definitions.ack.error and self.tags.tag_state_lane.failed and not self.tags.busy)
            self.assertTrue(self.tags.source_ack_pending)
            self.assertFalse(self.tags.tag_state_lane.pending)
            self.assertFalse(self.w.settings.get(KEY))
            self.assertEqual(self.tags.tag_state_lane.failed[0][0]['request'].operation_id,operation)
            self.assertEqual(self.tags.selected_tags(['one']),[{request['tag_id']}])
            self.assertTrue(self.definitions.acknowledgement_retry_ready)
        self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.ack.pending and not self.definitions.pending and not self.tags.busy)
        self.assertEqual(self.events,['create'])
        self.assertTrue(self.tags.retry());self.settled()
        self.assertEqual(self.events,['create','assign']);self.assertEqual(self.case.writes[-1].operation_id,operation)

    def test_failed_creation_durably_retains_dependent_head_for_quit_and_explicit_retry(self):
        self.tags.prep_priority=True
        request=self.creation();self.wait(lambda:not self.definitions.admission_pending)
        self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        operation=self.tags.pending[0]['request']['operation_id']
        self.creation_error='Generated creation storage failure'
        self.tags.prep_priority=False;self.definitions.schedule()
        self.wait(lambda:self.tags.pending and self.tags.pending[0]['error'] and not self.tags.tag_ack_pending
                  and not self.definitions.admission_pending and not self.w._state_committer.pending)
        retained=failed_intentions(self.w.settings)
        self.assertEqual(set(retained),{KEY,QUEUE})
        self.assertEqual(MainWindow._pending_save_reason(self.w,retained=retained),'')
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][KEY],retained[KEY])
        self.assertEqual(self.tags.pending[0]['request']['operation_id'],operation)
        self.assertFalse(self.events)
        self.assertIn(request['tag_id'],[tag.tag_id for tag in self.definitions.available_tags()])
        with mock.patch.object(self.w,'close',return_value=True) as closed:
            self.assertTrue(MainWindow._defer_quit_for_saves(self.w,auto_close=True))
            self.wait(lambda:closed.called)
            closed.assert_called_once_with()
            self.assertEqual(set(self.w._quit_saves.retained()),{KEY,QUEUE})
            self.assertEqual(read_state(self.w._state_repository.path)['settings'][KEY],retained[KEY])
        self.creation_error=''
        self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.pending and not self.definitions.admission_pending)
        self.assertTrue(self.tags.retry());self.settled()
        self.assertEqual(self.events,['create','assign'])
        self.assertEqual(self.case.writes[-1].operation_id,operation)

    def test_replacement_definition_operation_cannot_adopt_durable_child(self):
        self.tags.prep_priority=True
        request=self.creation();self.wait(lambda:not self.definitions.admission_pending)
        self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        original=deepcopy(self.tags.pending[0])
        replacement={**request,'operation_id':'f'*32}
        self.w.settings[QUEUE]=[{**self.definitions.pending[0],'request':replacement}]
        state,message=self.tags._tag_definition_state(original)
        self.assertEqual(state,'error');self.assertIn('identity changed',message)
        self.assertFalse(self.tags.assignment_reserved)
        self.w.settings[QUEUE]=[{**self.definitions.pending[0],'request':request}]
        self.tags.prep_priority=False;self.definitions.schedule();self.tags._schedule_tag_intent();self.settled()
        self.assertEqual(self.events,['create','assign'])

    def test_preceding_failed_definition_is_retainable_attention_for_later_child(self):
        self.tags.prep_priority=True
        first=self.creation('First');second=self.creation('Second')
        self.wait(lambda:not self.definitions.admission_pending)
        self.assertTrue(self.tags.request(['one'],second['tag_id'],True))
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        self.creation_error='Earlier definition failed'
        self.tags.prep_priority=False;self.definitions.schedule()
        self.wait(lambda:self.tags.pending and self.tags.pending[0]['error'] and not self.tags.tag_ack_pending
                  and not self.definitions.admission_pending and not self.w._state_committer.pending)
        self.assertIn(self.creation_error,self.tags.pending[0]['error'])
        self.assertEqual(set(failed_intentions(self.w.settings)),{KEY,QUEUE})
        self.assertEqual(self.tags.pending[0]['tag_creation_dependency']['operation_id'],second['operation_id'])
        self.assertEqual(self.definitions.pending[0]['request']['operation_id'],first['operation_id'])
        self.creation_error='';self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.pending and not self.definitions.admission_pending)
        self.assertTrue(self.tags.retry());self.settled()
        self.assertEqual(self.events,['create','create','assign'])

    def test_staged_projection_cannot_cross_a_restored_repository_generation(self):
        self.tags.prep_priority=True
        request=definitions.request_for(self.w.lib.source_library_id,'1','1','Restored')
        job=dict(source=str(self.w.lib.source),library_id=self.w.lib.source_library_id,
                 request=request,error='')
        self.assertTrue(self.definitions.admission.stage(job))
        self.assertIsNotNone(self.definitions.pending_creation(request['tag_id']))
        original=self.w._state_repository
        self.w._state_repository=StateRepository(original.path)
        self.w._state_repository.load()
        self.assertIsNone(self.definitions.pending_creation(request['tag_id']))
        self.assertNotIn(request['tag_id'],[tag.tag_id for tag in self.definitions.available_tags()])
        self.w._state_repository=original
        self.tags.prep_priority=False;self.definitions.admission.schedule();self.definitions.schedule();self.settled()

    def test_definition_admission_failure_retains_ram_gesture_for_explicit_retry(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def failing(patch):
            entered.set();release.wait(4);raise OSError('Generated definition admission full')
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=failing):
            request=self.creation();self.wait(entered.is_set)
            self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
            operation=self.tags.tag_state_lane.queue[0]['request'].operation_id
            release.set()
            self.wait(lambda:self.definitions.admission.error and self.tags.tag_state_lane.failed)
            self.assertFalse(self.w.settings.get(KEY));self.assertFalse(self.events)
            self.assertEqual(self.tags.tag_state_lane.failed[0][0]['request'].operation_id,operation)
            self.assertEqual(self.tags.selected_tags(['one']),[{request['tag_id']}])
        self.assertTrue(self.definitions.retry());self.assertTrue(self.tags.retry());self.settled()
        self.assertEqual(self.events,['create','assign']);self.assertEqual(self.case.writes[-1].operation_id,operation)

    def test_post_ack_publication_failure_retains_child_and_retry_reads_without_recreating(self):
        self.tags.prep_priority=True
        request=self.creation();self.wait(lambda:not self.definitions.admission_pending)
        self.assertTrue(self.tags.request(['one'],request['tag_id'],True))
        self.wait(lambda:not self.tags.tag_state_lane.pending)
        operation=self.tags.pending[0]['request']['operation_id']
        with mock.patch.object(self.w,'_publish_created_library',side_effect=RuntimeError('Generated publication failure')):
            self.tags.prep_priority=False;self.definitions.schedule()
            self.wait(lambda:self.definitions.publication_retry_pending and not self.definitions.admission_pending
                and self.tags.pending and self.tags.pending[0]['error'] and not self.tags.tag_ack_pending
                and not self.tags.busy and not self.w._state_committer.pending)
        self.assertFalse(self.definitions.pending)
        self.assertEqual(self.events,['create'])
        self.assertEqual(self.w.settings[QUEUE],[])
        self.assertEqual(self.tags.pending[0]['request']['operation_id'],operation)
        self.assertTrue(self.definitions.publication_retry_ready)
        with mock.patch.object(self.w,'close',return_value=True) as closed:
            self.assertTrue(MainWindow._defer_quit_for_saves(self.w,auto_close=True))
            self.wait(lambda:closed.called)
            self.assertEqual(set(self.w._quit_saves.retained()),{KEY})
        self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.publication_retry_pending and not self.tags.busy)
        self.assertEqual(self.events,['create'])
        self.assertTrue(any(tag.tag_id==request['tag_id'] for tag in self.w.lib.source_tags))
        self.assertTrue(self.tags.retry());self.settled()
        self.assertEqual(self.events,['create','assign']);self.assertEqual(self.case.writes[-1].operation_id,operation)

    def test_post_ack_publication_error_without_child_can_quit_or_retry_readback(self):
        with mock.patch.object(self.w,'_publish_created_library',side_effect=RuntimeError('Generated publication failure')):
            request=self.creation()
            self.wait(lambda:self.definitions.publication_retry_pending and not self.definitions.admission_pending
                      and not self.tags.busy and not self.w._state_committer.pending)
        self.assertFalse(self.definitions.pending)
        self.assertEqual(self.w.settings[QUEUE],[])
        self.assertEqual(MainWindow._pending_save_reason(self.w),'')
        self.assertFalse(MainWindow._defer_quit_for_saves(self.w,auto_close=True))
        self.assertTrue(self.tags.owned)  # Export/ordinary refresh still needs publication.
        self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.publication_retry_pending and not self.tags.busy)
        self.assertFalse(self.tags.owned)
        self.assertEqual(self.events,['create'])
        self.assertTrue(any(tag.tag_id==request['tag_id'] for tag in self.w.lib.source_tags))

    def test_post_ack_publication_retry_preserves_a_later_definition_suffix(self):
        self.tags.prep_priority=True
        first=self.creation('First');second=self.creation('Second')
        self.wait(lambda:not self.definitions.admission_pending)
        original_second=deepcopy(self.definitions.pending[1]['request'])
        with mock.patch.object(self.w,'_publish_created_library',side_effect=RuntimeError('First publication failed')):
            self.tags.prep_priority=False;self.definitions.schedule()
            self.wait(lambda:self.definitions.publication_retry_pending and not self.definitions.admission_pending
                and self.definitions.pending and self.definitions.pending[0]['error']
                and not self.tags.busy and not self.w._state_committer.pending)
        self.assertEqual(self.events,['create'])
        self.assertEqual(self.definitions.pending[0]['request'],original_second)
        self.assertEqual(set(failed_intentions(self.w.settings)),{QUEUE})
        self.assertEqual(MainWindow._pending_save_reason(self.w,retained=failed_intentions(self.w.settings)),'')
        self.assertTrue(self.definitions.publication_retry_ready)
        self.assertTrue(self.definitions.retry())
        self.wait(lambda:not self.definitions.publication_retry_pending and not self.tags.busy)
        self.assertEqual(self.events,['create'])
        self.assertEqual(self.definitions.pending[0]['request'],original_second)
        self.assertNotEqual(first['operation_id'],second['operation_id'])
        self.assertTrue(self.definitions.retry());self.settled()
        self.assertEqual(self.events,['create','create'])


class ImmediateTagWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_tag_order_preferences import TagWindowPreferences
        TagWindowPreferences.setUpClass()

    def test_actual_new_tag_form_and_checkbox_are_usable_before_native_creation_ack(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtWidgets import QDialog
        from desktop.tag_creation import NewTagDialog
        from tests.test_tag_order_preferences import TagWindowPreferences
        case=TagWindowPreferences();case.setUp()
        self.addCleanup(case.doCleanups)
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        case.native_categories();w=case.w;tags=w.tag_controller;definitions_controller=w.tag_definition_controller
        source=deepcopy(w.lib);events=[]
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        class DefinitionStore:
            def apply(_self,request):
                entered.set();release.wait(5)
                events.append('create')
                source.source_tags.append(LibraryTag(request['tag_id'],request['name'],'Genre',
                    request['tag_uuid'],request['category_id'],request['category_uuid']))
                return dict(ID=request['tag_id'])
        class AssignmentStore:
            def apply(_self,request):
                self.assertFalse(w.settings.get(QUEUE))
                events.append('assign' if request.present else 'remove')
                for target in request.tracks:
                    for identity,track in list(source.tracks_by_id.items()):
                        if track.native_id==target.track_id:
                            assigned=set(track.source_tag_ids)
                            self.assertEqual(request.tag_id in assigned,target.expected_present)
                            if request.present:assigned.add(request.tag_id)
                            else:assigned.discard(request.tag_id)
                            source.tracks_by_id[identity]=replace(track,source_tag_ids=sorted(assigned))
                return len(request.tracks)
        definitions_controller.factory=lambda _lib:DefinitionStore()
        definitions_controller.loader=lambda _path:deepcopy(source)
        tags._requires_authority=False;tags._ready=True;tags._store_factory=lambda _lib:AssignmentStore()
        tags._store=None;tags._loader=lambda _path:deepcopy(source)
        w.table.selectAll();selected=set(w._selected_ids())
        self.assertTrue(w._accept_source_curation(w.lib));self.assertTrue(w._save_state())
        def accept(dialog):
            dialog.name.setText('R&B');return QDialog.Accepted
        with mock.patch.object(NewTagDialog,'exec',new=accept):self.assertTrue(w._new_tag())
        case.wait(entered.is_set)
        job=definitions_controller.pending[0];tag_id=job['request']['tag_id']
        self.assertNotIn(tag_id,[tag.tag_id for tag in w.lib.source_tags])
        self.assertIn(tag_id,w.my_tags.checkboxes)
        self.assertEqual(w.my_tags.checkboxes[tag_id].accessibleName(),'R&B')
        self.assertEqual(set(w._selected_ids()),selected)

        with mock.patch.object(tags,'probe'):
            w.my_tags.popup.popup(w.my_tags.mapToGlobal(QPoint(0,w.my_tags.height())))
        case.app.processEvents()
        for desired in (True,False,True):
            box=w.my_tags.checkboxes[tag_id]
            QTest.mouseClick(box,Qt.LeftButton,pos=QPoint(8,box.height()//2))
            self.assertEqual(box.checkState(),Qt.Checked if desired else Qt.Unchecked)
            self.assertEqual(set(w._selected_ids()),selected)
        self.assertFalse(events)
        release.set();definitions_controller.schedule();tags._schedule_tag_intent()
        case.wait(lambda:not definitions_controller.pending and not definitions_controller.admission_pending
            and not tags.pending and not tags.tag_state_lane.pending and not tags.tag_ack_pending
            and not tags.busy and not w._state_committer.pending)
        self.assertEqual(events,['create','assign','remove','assign'])
        self.assertTrue(all(tag_id in source.tracks_by_id[identity].source_tag_ids for identity in selected))
        self.assertEqual(set(w._selected_ids()),selected)


if __name__=='__main__':unittest.main()
