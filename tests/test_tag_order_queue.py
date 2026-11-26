"""Rapid tag-order FIFOs retain exact intent through admission, ACK and retries."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.tag_creation_controller import QUEUE, TagDefinitionController
from soundbox import rekordbox_tag_order as order
from soundbox.library import LibraryTag
from tests import test_tag_creation_controller as fixtures


class HeldWindow(fixtures.Window):
    def __init__(self, root):
        super().__init__(root)
        self.holding=False
        self.patches=[]

    def _commit_tag_state(self,replacements,removals,committed,failed,*,current,expected_queues=None):
        if self.holding:
            self.patches.append((deepcopy(replacements),removals,committed,failed,current,expected_queues))
            return True
        return super()._commit_tag_state(replacements,removals,committed,failed,
            current=current,expected_queues=expected_queues)

    def release(self, *, error=None):
        replacements,removals,committed,failed,current,expected=self.patches.pop(0)
        if error is not None:
            failed(error);return
        super()._commit_tag_state(replacements,removals,committed,failed,
            current=current,expected_queues=expected)

    def _publish_created_library(self,library):
        super()._publish_created_library(library)
        self.tag_controller.lib=library


class RapidNativeTagOrders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.w=HeldWindow(self.root)
        self.addCleanup(self.w.deleteLater)
        self.tags=[LibraryTag(str(index),f'Tag {index}','Genre',
            f'00000000-0000-4000-8000-{index:012d}','1','1') for index in range(11,15)]
        self.w.lib.source_tags=list(self.tags)
        self.w.tag_controller.lib=self.w.lib
        self.source=deepcopy(self.w.lib)
        self.applied=[];self.replayed=[];self.receipts={}
        self.tokens=set()
        self.w.tag_controller.reserve_source_ack=self.tokens.add
        self.w.tag_controller.release_source_ack=self.tokens.discard
        owner=self
        class Store:
            def apply(self,request):
                # Fake native effects are isolated, but their baseline and
                # durable-intention requirements are independent assertions.
                head=owner.w.settings[QUEUE][0]['request']
                owner.assertEqual(head,request)
                current=[tag for tag in owner.source.source_tags if tag.category_id==request['category_id']]
                if request['operation_id'] in owner.receipts:
                    owner.assertEqual([tag.tag_id for tag in current],request['order'])
                    owner.replayed.append(deepcopy(request))
                    return {'ID':request['tag_id']}
                owner.assertEqual([dict(id=tag.tag_id,uuid=tag.source_uuid,name=tag.name)
                    for tag in current],request['before'])
                by_id={tag.tag_id:tag for tag in current};desired=iter(request['order'])
                owner.source=replace(owner.source,source_tags=[by_id[next(desired)]
                    if tag.category_id==request['category_id'] else tag for tag in owner.source.source_tags])
                owner.applied.append(deepcopy(request));owner.receipts[request['operation_id']]=deepcopy(request)
                return {'ID':request['tag_id']}
        self.factory=lambda _:Store()
        self.c=self.controller()
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))

    def controller(self):
        controller=TagDefinitionController(self.w,order_store_factory=self.factory,
            loader=lambda _:deepcopy(self.source))
        self.w.tag_definition_controller=controller
        self.addCleanup(controller.stop)
        return controller

    def request(self, desired):
        tags=self.c.native_order(self.w.lib.source_tags)
        return order.request_for('library',tags,list(desired))

    def enqueue(self, desired):
        request=self.request(desired)
        self.assertTrue(self.c.enqueue_order(request))
        self.c.timer.stop();self.c.admission.timer.stop()
        return request

    def deliver(self):
        self.c.timer.stop();self.c.pump()
        self.assertTrue(self.w.tag_controller.busy)
        self.w.tag_controller.deliver()
        self.w.tag_controller.lib=self.w.lib
        self.c.timer.stop();self.c.admission.timer.stop()

    def ids(self, tags=None):
        return [tag.tag_id for tag in (self.c.native_order(self.w.lib.source_tags) if tags is None else tags)]

    def test_same_tag_back_and_forth_and_multiple_tags_preserve_fifo_identity(self):
        requests=[self.enqueue(ids) for ids in (
            ['12','13','11','14'],['11','12','13','14'],['14','11','12','13'],['12','14','11','13'])]
        self.assertEqual(self.ids(),['12','14','11','13'])
        self.assertEqual(self.c.queue_error(),'')
        self.assertEqual(self.c.order_reason(),'')
        self.assertLess(len({request['tag_id'] for request in requests}),len(requests))
        frozen=deepcopy(requests)
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,frozen)
        self.assertEqual(self.ids(self.source.source_tags),['12','14','11','13'])
        self.assertEqual(self.w.settings[QUEUE],[])
        self.assertEqual(self.ids(),['12','14','11','13'])

    def test_moves_during_held_canonical_admission_wait_for_their_own_durable_prefix(self):
        self.w.holding=True
        first=self.enqueue(['12','11','13','14'])
        second=self.enqueue(['14','12','11','13'])
        self.assertEqual(self.ids(),['14','12','11','13'])
        self.c.pump();self.assertFalse(self.w.tag_controller.admitted)
        self.assertNotIn(QUEUE,self.w.settings)
        self.w.release()
        self.assertEqual([job['request'] for job in self.w.settings[QUEUE]],[first])
        self.assertEqual([job['request'] for job in self.c.admission.staged],[second])
        self.c.admission.pump()
        self.assertEqual(len(self.w.patches),1)
        self.w.holding=False;self.w.release()
        self.assertEqual([job['request'] for job in self.w.settings[QUEUE]],[first,second])
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second])

    def test_held_head_ack_accepts_new_moves_and_publishes_without_a_projection_jump(self):
        first=self.enqueue(['12','11','13','14'])
        self.c.pump();self.w.holding=True
        self.w.tag_controller.deliver()
        self.assertTrue(self.c.ack.pending)
        self.assertEqual(self.applied,[first])
        second=self.enqueue(['14','12','11','13'])
        third=self.enqueue(['11','14','12','13'])
        wanted=['11','14','12','13']
        self.assertEqual(self.ids(),wanted)
        observed=[]
        self.w.tag_controller.changed.connect(lambda:observed.append(self.ids()))
        self.w.holding=False;self.w.release()
        self.assertFalse(self.c.ack.pending)
        self.assertEqual(self.c.admission._base,[])
        self.assertEqual([job['request'] for job in self.c.admission.staged],[second,third])
        self.assertEqual(self.ids(),wanted)
        self.assertTrue(all(ids==wanted for ids in observed))
        self.c.admission.pump()
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second,third])
        self.assertFalse(self.tokens)

    def test_saved_fifo_restarts_with_a_committed_unacknowledged_head(self):
        first=self.enqueue(['12','11','13','14'])
        second=self.enqueue(['14','12','11','13'])
        self.c.pump()
        # Complete the isolated source effect but keep its GUI/canonical ACK
        # undelivered, then reconstruct the controller from the saved FIFO.
        _kind,work,_completed=self.w.tag_controller.admitted[-1]
        work()
        saved=deepcopy(self.w.settings[QUEUE])
        self.w.tag_controller.busy=False;self.c.stop()
        self.w.lib=deepcopy(self.source);self.w.tag_controller.lib=self.w.lib
        self.c=self.controller()
        self.assertEqual(self.w.settings[QUEUE],saved)
        self.assertEqual(self.ids(),['14','12','11','13'])
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second])
        self.assertEqual(self.replayed,[first])
        self.assertEqual(self.w.settings[QUEUE],[])

    def test_ack_storage_failure_retains_successors_and_retry_does_not_reapply_head(self):
        first=self.enqueue(['12','11','13','14'])
        second=self.enqueue(['14','12','11','13'])
        self.c.pump();self.w.save_ok=False
        self.w.tag_controller.deliver()
        self.assertTrue(self.c.ack.error)
        self.assertEqual([job['request'] for job in self.c.pending],[first,second])
        self.assertTrue(self.c.order_reason())
        self.assertEqual(self.applied,[first])
        self.w.save_ok=True
        self.assertTrue(self.c.retry());self.c.ack.pump()
        self.assertFalse(self.c.ack.pending)
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second])
        self.assertEqual(self.replayed,[])
        self.assertFalse(self.tokens)

    def test_source_failure_retains_exact_tail_and_explicit_retry_drains_in_order(self):
        first=self.enqueue(['12','11','13','14'])
        second=self.enqueue(['14','12','11','13'])
        self.c.pump();self.w.tag_controller.deliver(error='Generated source write failure')
        self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual([job['request'] for job in self.c.pending],[first,second])
        self.assertEqual(self.applied,[])
        self.assertTrue(self.c.retry())
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second])

    def test_publication_failure_bridges_final_projection_and_retry_only_reads_source(self):
        first=self.enqueue(['12','11','13','14'])
        second=self.enqueue(['14','12','11','13'])
        self.c.pump()
        with mock.patch.object(self.w,'_publish_created_library',side_effect=RuntimeError('Generated UI failure')):
            self.w.tag_controller.deliver()
        self.assertEqual(self.applied,[first])
        self.assertEqual([job['request'] for job in self.w.settings[QUEUE]],[second])
        self.assertTrue(self.c.publication_retry_pending)
        self.assertEqual(self.ids(),['14','12','11','13'])
        self.assertTrue(self.c.order_reason())
        # Let the same queued continuation as the real event loop durably
        # retain a blocked successor before retrying the readback publication.
        self.c._record_publication_blocker()
        self.assertTrue(self.c.pending[0]['error'])
        self.assertEqual(self.c.pending[0]['request'],second)
        self.assertTrue(self.c.retry())
        self.w.tag_controller.deliver()
        self.assertFalse(self.c.publication_retry_pending)
        self.assertEqual(self.applied,[first])
        self.assertTrue(self.c.retry())
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second])
        self.assertFalse(self.tokens)

    def test_changed_saved_chain_fails_closed_without_retiring_any_intention(self):
        self.enqueue(['12','11','13','14'])
        self.enqueue(['14','12','11','13'])
        queue=deepcopy(self.w.settings[QUEUE])
        queue[1]['request']=order.request_for('library',self.tags,queue[1]['request']['order'])
        order.validate_request(queue[1]['request'])
        self.w.settings[QUEUE]=queue
        self.assertTrue(self.c.queue_error())
        self.assertTrue(self.c.order_reason())
        self.c.pump()
        self.assertFalse(self.w.tag_controller.admitted)
        self.assertEqual(self.w.settings[QUEUE],queue)
        self.assertEqual(self.applied,[])

    def test_publication_and_unsaved_tail_errors_can_retry_in_dependency_order(self):
        first=self.enqueue(['12','11','13','14'])
        self.c.pump()
        commit=self.w._commit_tag_state
        def available(*args,**kwargs):
            return False if self.w.tag_controller.busy else commit(*args,**kwargs)
        with mock.patch.object(self.w,'_commit_tag_state',side_effect=available):
            second=self.enqueue(['14','12','11','13'])
            self.assertEqual([job['request'] for job in self.c.admission.staged],[second])
            with mock.patch.object(self.w,'_publish_created_library',side_effect=RuntimeError('Generated UI failure')):
                self.w.tag_controller.deliver()
        self.assertTrue(self.c.publication_retry_pending)
        self.assertFalse(self.c.ack.pending)
        self.w.save_ok=False;self.c.admission.pump()
        self.assertTrue(self.c.admission.error)
        self.assertEqual(self.w.settings[QUEUE],[])
        self.assertEqual(self.applied,[first])
        self.w.save_ok=True
        self.assertTrue(self.c.retry())
        self.c.admission.pump()
        self.assertFalse(self.c.admission.pending)
        self.assertEqual(self.w.settings[QUEUE][0]['request'],second)
        self.assertTrue(self.c.retry())
        self.w.tag_controller.deliver()
        self.assertFalse(self.c.publication_retry_pending)
        self.deliver()
        self.assertEqual(self.applied,[first,second])
        self.assertEqual(self.w.settings[QUEUE],[])

    def test_restore_or_source_switch_fences_old_staged_projection_and_ack(self):
        self.w._state_repository=SimpleNamespace(generation=0,area=SimpleNamespace(generation=0))
        self.w.holding=True
        request=self.enqueue(['12','11','13','14'])
        self.assertEqual(self.ids(),request['order'])
        self.w._state_repository.area.generation+=1
        self.assertEqual(self.ids(),[tag.tag_id for tag in self.tags])
        self.assertTrue(self.c.order_reason())
        self.w.release()
        self.assertTrue(self.c.admission.error)
        self.assertFalse(self.w.tag_controller.admitted)
        self.assertNotIn(QUEUE,self.w.settings)
        self.assertEqual(self.c.admission.staged[0]['request'],request)

    def test_restore_fences_an_already_committed_source_heads_late_ack(self):
        self.w._state_repository=SimpleNamespace(generation=0,area=SimpleNamespace(generation=0))
        first=self.enqueue(['12','11','13','14'])
        self.c.pump();self.w.holding=True
        self.w.tag_controller.deliver()
        second=self.enqueue(['14','12','11','13'])
        self.assertEqual(self.applied,[first])
        self.w._state_repository.area.generation+=1
        self.w.settings[QUEUE]=[]
        self.w.release()
        self.assertFalse(self.c.ack.pending)
        self.assertFalse(self.tokens)
        self.assertEqual(self.w.settings[QUEUE],[])
        self.assertEqual(self.w.published,[])
        self.assertEqual(self.ids(),[tag.tag_id for tag in self.tags])
        self.assertEqual(self.c.admission.staged[0]['request'],second)

    def test_source_switch_blocks_new_order_and_does_not_project_the_old_queue(self):
        first=self.enqueue(['12','11','13','14'])
        self.w.lib=replace(self.w.lib,source=Path('/synthetic/replacement.db'),source_library_id='other-library')
        before=deepcopy(self.w.settings)
        self.assertEqual(self.ids(),[tag.tag_id for tag in self.tags])
        self.assertTrue(self.c.order_reason())
        self.assertFalse(self.c.enqueue_order(first))
        self.assertEqual(self.w.settings,before)

    def test_external_order_uuid_name_or_category_change_refuses_before_admission(self):
        self.enqueue(['12','11','13','14'])
        self.enqueue(['14','12','11','13'])
        saved=deepcopy(self.w.settings)
        for change in ('order','uuid','name','category'):
            with self.subTest(change=change):
                changed=list(self.tags)
                if change=='order':changed=[changed[index] for index in (2,1,0,3)]
                elif change=='uuid':changed[0]=replace(changed[0],source_uuid='replacement-uuid')
                elif change=='name':changed[0]=replace(changed[0],name='External rename')
                else:changed=[replace(tag,category_uuid='replacement-category') for tag in changed]
                self.w.lib=replace(self.w.lib,source_tags=changed)
                self.w.tag_controller.lib=self.w.lib
                request=order.request_for('library',changed,[tag.tag_id for tag in reversed(changed)])
                self.assertTrue(self.c.order_reason())
                self.assertFalse(self.c.enqueue_order(request))
                self.assertEqual(self.w.settings,saved)
                self.assertEqual(self.c.admission.staged,[])
                self.assertEqual(self.w.tag_controller.admitted,[])

    def test_independent_categories_keep_separate_causal_order_chains(self):
        others=[LibraryTag('21','Other first','Other','other-uuid-21','2','category-2'),
                LibraryTag('22','Other second','Other','other-uuid-22','2','category-2')]
        self.tags.extend(others)
        self.w.lib=replace(self.w.lib,source_tags=list(self.tags))
        self.w.tag_controller.lib=self.w.lib;self.source=deepcopy(self.w.lib)
        first=self.enqueue(['12','11','13','14','21','22'])
        second=self.enqueue(['12','11','13','14','22','21'])
        third=self.enqueue(['14','12','11','13','22','21'])
        self.assertEqual(self.c.queue_error(),'')
        while self.c.pending:self.deliver()
        self.assertEqual(self.applied,[first,second,third])
        self.assertEqual(self.ids(),['14','12','11','13','22','21'])

    def test_cross_category_id_or_uuid_claims_cannot_share_a_saved_order_queue(self):
        first=self.enqueue(['12','11','13','14'])
        original=deepcopy(self.w.settings[QUEUE])
        for collision in ('id','uuid'):
            with self.subTest(collision=collision):
                tags=[LibraryTag('21','Other first','Other','other-uuid-21','2','category-2'),
                      LibraryTag('22','Other second','Other','other-uuid-22','2','category-2')]
                if collision=='id':tags[0]=replace(tags[0],tag_id='11')
                else:tags[0]=replace(tags[0],source_uuid=self.tags[0].source_uuid)
                second=order.request_for('library',tags,[tag.tag_id for tag in reversed(tags)])
                self.w.settings[QUEUE]=[*deepcopy(original),dict(source=str(self.w.lib.source),
                    library_id='library',request=second,error='')]
                self.assertTrue(self.c.queue_error())
                self.assertEqual(self.w.settings[QUEUE][0]['request'],first)
                self.c.pump()
                self.assertFalse(self.w.tag_controller.admitted)
