"""Native smart creation acknowledges through a held canonical worker, preserving later gestures."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QTimer,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidget

from soundbox import rekordbox_playlist_plan as planner,rekordbox_playlist_store as store
from soundbox.library import Playlist
from soundbox.state_repository import read_state
from desktop.rekordbox_playlists import QUEUE
from tests import test_source_intent_admission as fixtures


class SmartCreationAcknowledgement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=fixtures.SourceIntentAdmission();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.window=w=self.case.window;self.controller=self.case.playlist;self.tags=self.case.tags
        self.catalog=deepcopy(w.lib.source_playlist_catalog);self.latest=w.lib;self.applied=[];self.published=[]
        w._publish_created_library=self.publish
        owner=self
        class Store:
            def apply(self,request):
                canonical=read_state(w._state_repository.path)['settings'][QUEUE]
                owner.assertTrue(any(row['request']==request for row in canonical))
                owner.assertEqual(request['revision'],owner.catalog['revision'])
                owner.applied.append(deepcopy(request))
                with closing(sqlite3.connect(owner.case.database)) as connection:
                    connection.execute('INSERT INTO written VALUES (?)',(request['operation_id'],));connection.commit()
                node=dict(ID=request['playlist_id'],UUID=request['playlist_uuid'],Name=request['name'],
                    ParentID=request['parent'],Seq=len(owner.catalog['nodes'])+1,Attribute=4,rb_local_deleted=0,
                    SmartList=planner.smart_tag_rule(request['playlist_id'],[tag['id'] for tag in request['tags']]))
                owner.catalog={'nodes':[*owner.catalog['nodes'],node],'members':[]}
                owner.catalog['revision']=planner.revision(owner.catalog['nodes'],[])
                owner.latest=replace(w.lib,source_playlist_catalog=deepcopy(owner.catalog),
                    playlists=[Playlist(row['Name'],[],[],row['ID']) for row in owner.catalog['nodes']])
                return deepcopy(owner.catalog)
            def committed_transition(self,request):return request['revision'],owner.catalog['revision']
        self.controller.factory=lambda _:Store();self.tags.readback_library=lambda _:self.latest

    def publish(self,library):self.published.append(library);self.window.lib=library

    def request(self,name):return store.request_for(self.window.lib.source_playlist_catalog,'create_smart',
        name=name,tags=self.window.lib.source_tags)

    def admitted(self,request):
        self.assertTrue(self.controller.enqueue(request));self.case.wait(lambda:bool(self.case.native))

    def deliver(self,index=0):
        _,work,completed=self.case.native[index];result=work();self.tags.busy=False
        completed(SimpleNamespace(error='',result=result));self.tags.changed.emit();self.tags.idle.emit()

    def test_source_success_waits_for_sparse_ack_without_blocking_navigation_or_replaying_source(self):
        request=self.request('Created smart');self.admitted(request)
        navigation=QListWidget(self.window);navigation.addItems(['Collection','New smart']);navigation.setCurrentRow(0)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2))
            self.assertTrue(self.controller.ack.pending);self.assertTrue(self.tags.source_job_owned)
            self.assertFalse(self.published);self.assertEqual(self.controller.pending[0]['request'],request)
            self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE][0]['request'],request)
            ticks=[];timer=QTimer();timer.setInterval(1);timer.timeout.connect(lambda:ticks.append(1));timer.start()
            QTest.keyClick(navigation,Qt.Key_Down);self.case.wait(lambda:bool(ticks));timer.stop()
            self.assertEqual(navigation.currentRow(),1);self.assertTrue(ticks)
            self.controller.pump();self.assertEqual(len(self.applied),1);release.set()
        self.case.wait(lambda:not self.controller.ack.pending)
        self.assertEqual(len(self.published),1);self.assertEqual(self.controller.pending,[])
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE],[])
        self.window._save_state.assert_not_called()

    def test_staged_second_create_rebases_through_first_ack_without_losing_operation_identity(self):
        first=self.request('First');self.admitted(first);second=self.request('Second')
        self.assertTrue(self.controller.enqueue(second));self.assertTrue(self.controller.admission.staged)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2));self.assertFalse(self.published)
            self.controller.pump();self.assertEqual(len(self.case.native),1);release.set()
        self.case.wait(lambda:len(self.case.native)==2)
        self.deliver(1);self.case.wait(lambda:not self.controller.pending and not self.controller.ack.pending)
        self.assertEqual([row['operation_id'] for row in self.applied],[first['operation_id'],second['operation_id']])
        self.assertEqual(self.applied[1]['playlist_id'],second['playlist_id']);self.assertEqual(self.applied[1]['playlist_uuid'],second['playlist_uuid'])
        self.assertEqual(len(self.published),2);self.window._save_state.assert_not_called()

    def test_durable_ack_after_source_switch_cannot_publish_into_new_library_and_retries_idempotently(self):
        original=self.window.lib;request=self.request('Bound to original');self.admitted(request)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2))
            self.window.lib=replace(original,source='/synthetic/other.db',source_library_id='other')
            release.set()
        self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertFalse(self.published);self.assertFalse(self.controller.retry())
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE],[])
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.window.lib=original;self.assertTrue(self.controller.retry())
        self.case.wait(lambda:not self.controller.ack.pending)
        self.assertEqual(len(self.applied),1);self.assertEqual(len(self.published),1)
        self.assertEqual(self.window.lib.source_library_id,'library');self.assertEqual(self.controller.pending,[])
        self.window._save_state.assert_not_called()

    def test_ack_storage_failure_retains_durable_head_and_explicit_retry_does_not_repeat_native_write(self):
        request=self.request('Saved in native');self.admitted(request)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.deliver();self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertFalse(self.published);self.assertEqual(len(self.applied),1)
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE][0]['request'],request)
        self.assertTrue(self.controller.retry());self.case.wait(lambda:not self.controller.ack.pending)
        self.assertEqual(len(self.applied),1);self.assertEqual(len(self.published),1)
        self.assertEqual(self.controller.pending,[]);self.window._save_state.assert_not_called()
