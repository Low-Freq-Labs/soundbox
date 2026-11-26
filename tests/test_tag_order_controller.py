"""Native tag order shares durable FIFO/readback without borrowing other authority."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.tag_creation_controller import TagDefinitionController,QUEUE
from soundbox import rekordbox_tag_order as order, rekordbox_tag_definitions as definitions
from soundbox.library import LibraryTag,Track
from tests import test_tag_creation_controller as fixtures


class NativeTagOrderController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=fixtures.Window(self.root);self.addCleanup(self.window.deleteLater)
        self.tags=[LibraryTag('11','Dubstep','Genre','00000000-0000-4000-8000-000000000011','1','1'),
                   LibraryTag('12','Riddim','Genre','00000000-0000-4000-8000-000000000012','1','1')]
        self.window.lib.source_tags=list(self.tags)
        self.window.lib.tracks_by_id={'track':Track('track','Song','','file:///never-open/song.wav',source_tag_ids=['11'])}
        self.applied=[];owner=self
        class Store:
            def apply(self,request):owner.applied.append(deepcopy(request));return dict(ID=request['tag_id'])
        self.fresh=lambda:replace(self.window.lib,source_tags=list(reversed(self.tags)))
        self.controller=TagDefinitionController(self.window,order_store_factory=lambda _:Store(),loader=lambda _:self.fresh())
        self.addCleanup(self.controller.stop)
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))

    def request(self):return order.request_for('library',self.tags,['12','11'])

    def test_create_delete_authority_never_admits_native_reorder_and_public_source_refuses(self):
        self.controller.order_factory=None;self.controller.factory=lambda _:object();self.controller.deletion_factory=lambda _:object()
        self.assertIn('separate acceptance',self.controller.order_reason())
        self.assertFalse(self.controller.enqueue_order(self.request()));self.assertFalse(self.window.saved)
        self.controller.order_factory=lambda _:object();self.window.lib.source_kind='rekordbox-xml'
        self.assertFalse(self.controller.enqueue_order(self.request()));self.assertFalse(self.window.saved)

    def test_optimistic_native_order_is_visible_while_sql_waits_for_exact_durable_admission(self):
        held=[];commit=self.window._commit_tag_state
        self.window._commit_tag_state=lambda *args,**kwargs:held.append((args,kwargs)) or True
        request=self.request();self.assertTrue(self.controller.enqueue_order(request))
        self.assertEqual(self.window.tag_controller.definition_update_status,'Reordering tags…')
        self.assertEqual([tag.tag_id for tag in self.controller.native_order(self.tags)],['12','11'])
        self.assertEqual([tag.tag_id for tag in self.window.lib.source_tags],['11','12'])
        self.assertTrue(self.controller.admission_pending);self.assertNotIn(QUEUE,self.window.settings)
        self.controller.pump();self.assertFalse(self.window.tag_controller.admitted);self.assertFalse(self.applied)
        self.window._commit_tag_state=commit;args,kwargs=held[0];commit(*args,**kwargs)
        self.controller.timer.stop();self.controller.pump();self.assertEqual(len(self.window.tag_controller.admitted),1)
        self.window.tag_controller.deliver()
        self.assertEqual(self.applied,[request]);self.assertEqual(self.controller.pending,[])
        self.assertEqual([tag.tag_id for tag in self.window.lib.source_tags],['12','11'])
        self.assertEqual(self.window.lib.tracks_by_id['track'].source_tag_ids,['11'])

    def test_changed_source_order_or_definition_identity_refuses_before_queue_or_native_job(self):
        request=self.request()
        for tags in (list(reversed(self.tags)),[replace(self.tags[0],source_uuid='replacement'),self.tags[1]],
                     [replace(self.tags[0],name='Renamed'),self.tags[1]]):
            self.window.lib.source_tags=tags
            self.assertFalse(self.controller.enqueue_order(request));self.assertFalse(self.controller.pending)
        self.assertFalse(self.window.saved);self.assertFalse(self.window.tag_controller.admitted)

    def test_wrong_order_or_replaced_uuid_readback_retains_failed_intention_without_publication(self):
        self.fresh=lambda:replace(self.window.lib,source_tags=self.tags)
        request=self.request();self.assertTrue(self.controller.enqueue_order(request))
        self.controller.timer.stop();self.controller.pump();self.window.tag_controller.deliver()
        self.assertTrue(self.controller.pending[0]['error']);self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertFalse(self.window.published);self.assertEqual(self.window.lib.source_tags,self.tags)
        self.assertEqual(self.controller.native_order(self.tags),self.tags)

    def test_pending_create_or_delete_blocks_new_order_capture(self):
        tag=self.tags[0]
        for request in (
                definitions.request_for('library','1','1','New Tag'),
                definitions.deletion_request_for('library','1','1',tag.tag_id,tag.source_uuid,tag.name)):
            with self.subTest(action=request.get('action','create')):
                self.window.settings[QUEUE]=[dict(source=str(self.window.lib.source),library_id='library',
                    request=request,error='')]
                self.assertIn('Finish pending',self.controller.order_reason())
                self.assertFalse(self.controller.enqueue_order(self.request()))
                self.assertEqual(len(self.controller.pending),1)
