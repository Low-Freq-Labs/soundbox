"""The form routes only admitted folder appends through the durable shared FIFO."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.rekordbox_playlists import QUEUE
from soundbox.library import Playlist
from soundbox import rekordbox_playlist_store as store
from soundbox.state_repository import read_state
from tests import test_source_intent_admission as admission
import test_smart_append_creation as engine_fixture


class SmartAppendDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):admission.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=admission.SourceIntentAdmission();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.native=engine_fixture.SmartAppendCreation();self.native.setUp();self.addCleanup(self.native.doCleanups)
        self.window=w=self.case.window;self.controller=self.case.playlist;self.tags=self.case.tags
        self.writer=self.native.writer
        w.lib=replace(w.lib,source=str(self.native.f.database),source_library_id=self.native.authority.library_id,
            source_tags=self.native.f.tags,source_playlist_catalog=deepcopy(self.native.catalog),
            playlists=[Playlist('Existing',[],[],'1000'),Playlist('Existing', ['Existing'],[],'2001')])
        self.tags.lib=w.lib;self.controller.factory=lambda _:self.writer
        self.tags.readback_library=lambda _:replace(w.lib,source_playlist_catalog=self.writer.snapshot(),
            playlists=[Playlist(row['Name'],[],[],row['ID']) for row in self.writer.snapshot()['nodes']
                       if row['Attribute'] in (0,4) and row['rb_local_deleted']==0])

    def request(self,name):return store.request_for(self.window.lib.source_playlist_catalog,'create_smart_append',
        name=name,parent='2000',tags=self.window.lib.source_tags)

    def deliver(self,index=0):
        _,work,completed=self.case.native[index];result=work();self.tags.busy=False
        completed(SimpleNamespace(error='',result=result));self.tags.changed.emit();self.tags.idle.emit()

    def test_mixed_folder_needs_separate_capability_and_empty_parent_remains_guarded(self):
        self.controller.factory=None
        old=replace(self.native.f.authority,smart_create_accepted=True,smart_create_profile=store.SMART_CREATE_PROFILE)
        with mock.patch('desktop.rekordbox_playlists.load_authority',return_value=old):
            self.assertEqual(self.controller.smart_creation_operation('root'),('create_smart',''))
            operation,reason=self.controller.smart_creation_operation('2000')
            self.assertFalse(operation);self.assertIn('separate acceptance',reason)
            self.assertFalse(self.controller.enqueue(self.request('Blocked')))
        with mock.patch('desktop.rekordbox_playlists.load_authority',return_value=self.native.authority):
            self.assertEqual(self.controller.smart_creation_operation('2000'),('create_smart_append',''))
            empty=deepcopy(self.window.lib.source_playlist_catalog)
            empty['nodes']=[node for node in empty['nodes'] if node['ParentID']!='2000']
            operation,reason=self.controller.smart_creation_operation('2000',empty)
            self.assertFalse(operation);self.assertIn('empty',reason)
        self.assertFalse(self.case.native);self.assertNotIn(QUEUE,self.window.settings)

    def test_append_waits_for_canonical_admission_and_verified_ack_then_publishes_exact_native_row(self):
        request=self.request('Native append');before=self.native.inspect()
        with self.case.held() as (entered,release):
            self.assertTrue(self.controller.enqueue(request));self.assertTrue(entered.wait(2))
            self.assertEqual(self.controller.pending[0]['request'],request)
            self.assertFalse(self.case.native);self.assertEqual(self.native.inspect(),before);release.set()
        self.case.wait(lambda:bool(self.case.native))
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE][0]['request'],request)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2));self.assertFalse(self.window.published)
            self.assertTrue(self.tags.source_job_owned);release.set()
        self.case.wait(lambda:not self.controller.admission_pending)
        self.assertFalse(self.controller.pending);self.assertEqual(len(self.window.published),1)
        created=next(row for row in self.native.inspect()[1] if row['ID']==request['playlist_id'])
        self.assertEqual((created['Name'],created['ParentID'],created['Seq']),('Native append','2000',2))
        self.assertEqual([row for row in self.native.inspect()[1] if row['ID']!=created['ID']],before[1])
        self.window._save_state.assert_not_called()

    def test_staged_append_renews_parent_clock_and_keeps_target_identity_through_prior_commit(self):
        first=self.request('First append');self.assertTrue(self.controller.enqueue(first))
        self.case.wait(lambda:bool(self.case.native));second=self.request('Second append')
        self.assertTrue(self.controller.enqueue(second));self.deliver()
        self.case.wait(lambda:len(self.case.native)==2)
        renewed=self.controller.pending[0]['request']
        self.assertEqual(renewed['operation'],'create_smart_append')
        self.assertEqual(renewed['operation_id'],second['operation_id'])
        self.assertEqual(renewed['playlist_uuid'],second['playlist_uuid'])
        self.assertGreater(renewed['smart_clocks']['parent_xml_timestamp'],first['smart_clocks']['parent_xml_timestamp'])
        self.deliver(1);self.case.wait(lambda:not self.controller.admission_pending)
        self.assertFalse(self.controller.pending)
        rows={row['ID']:row for row in self.native.inspect()[1]}
        self.assertEqual((rows[first['playlist_id']]['Seq'],rows[second['playlist_id']]['Seq']),(2,3))
        self.window._save_state.assert_not_called()
