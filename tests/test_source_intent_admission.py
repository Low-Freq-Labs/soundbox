"""Generated canonical workers fence source creation without blocking the GUI."""
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QMessageBox

from desktop.app import MainWindow
from desktop.rekordbox_playlists import PlaylistEditController, QUEUE as PLAYLIST_QUEUE
from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from desktop.tag_creation_controller import TagDefinitionController, QUEUE as TAG_QUEUE
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import LibraryTag
from soundbox.rekordbox_playlist_store import request_for
from soundbox.state_repository import StateRepository, read_state
from tests import test_tag_creation_controller as fixtures


class SourceIntentAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window=w=fixtures.Window(self.root)
        w.titles={};w.overrides={};w.aliases={};w._state_publication=False
        w._native_pending_changed=mock.Mock();w._native_reconcile_source=mock.Mock()
        w.lib.source_tags=[LibraryTag('11','Favorite','Genre','eleven','1','1')]
        w.lib.source_playlist_catalog=dict(nodes=[],members=[],revision='a'*64)
        w._state_repository=StateRepository(self.root/'state/library.json')
        w._state_repository.save(dict(settings={},title_overrides={},artist_overrides={},artist_aliases={}))
        w.tag_controller=TagEditController(self.root,w,store_factory=lambda _:None)
        self.tags=w.tag_controller;self.tags.verified=True;self.tags.lib=w.lib
        self.tags._recover_pending=mock.Mock();self.tags.readback_library=mock.Mock(return_value=w.lib)
        self.committer=w._state_committer=StateCommitter(w)
        w._commit_tag_state=lambda *args,**kwargs:MainWindow._commit_tag_state(w,*args,**kwargs)
        w._save_state=mock.Mock(side_effect=AssertionError('Creation serialized the whole UI state'))
        self.native=[]
        def admit(kind,work,completed):
            self.tags.busy=True;self.native.append((kind,work,completed));return True
        self.tags._run_source_job=admit
        self.database=self.root/'generated-source.sqlite'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('CREATE TABLE written (request TEXT)');connection.commit()
        owner=self
        class Store:
            def apply(self,request):
                key=TAG_QUEUE if 'tag_id' in request else PLAYLIST_QUEUE
                canonical=read_state(w._state_repository.path)['settings'][key]
                owner.assertTrue(any(row['request']==request for row in canonical), 'Source write preceded durable intention')
                with closing(sqlite3.connect(owner.database)) as connection:
                    connection.execute('INSERT INTO written VALUES (?)',(request['operation_id'],))
                    connection.commit()
                return {} if key==TAG_QUEUE else w.lib.source_playlist_catalog
        self.tag=TagDefinitionController(w,store_factory=lambda _:Store(),loader=lambda _:w.lib)
        self.playlist=PlaylistEditController(w,store_factory=lambda _:Store())
        self.addCleanup(self.close)
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        self.enterContext(mock.patch('desktop.rekordbox_playlists.assert_rekordbox_closed'))

    def close(self):
        self.tag.stop();self.playlist.stop();self.committer.owner.drain();self.tags.owner.drain()
        self.window.deleteLater();self.app.processEvents()

    def wait(self,condition):
        end=time.monotonic()+4
        while not condition() and time.monotonic()<end:self.app.processEvents();QTest.qWait(1)
        self.assertTrue(condition(),'Admission did not settle')

    def request(self,name='New Tag'):return definitions.request_for('library','1','1',name)

    def written(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute('SELECT request FROM written').fetchall()

    @contextmanager
    def held(self):
        entered,release=threading.Event(),threading.Event()
        original=self.window._state_repository.commit_patch
        def commit(patch):
            entered.set()
            if not release.wait(4):raise RuntimeError('Generated commit fixture timed out')
            return original(patch)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=commit):
            try:yield entered,release
            finally:
                release.set();self.wait(lambda:not self.committer.pending)

    def test_tag_feedback_and_navigation_are_immediate_but_source_sql_waits_for_durable_ack(self):
        navigation=QListWidget(self.window);navigation.addItems(['Collection','Favorite']);navigation.setCurrentRow(0)
        ticks=[];timer=QTimer();timer.setInterval(1);timer.timeout.connect(lambda:ticks.append(1))
        request=self.request();before=deepcopy(request)
        with self.held() as (entered,release):
            self.assertTrue(self.tag.enqueue(request));self.assertTrue(entered.wait(2))
            self.assertTrue(self.tag.admission_pending);self.assertEqual(self.tag.pending[0]['request'],request)
            visible=self.tag.pending;visible[0]['request']['name']='An unrelated display owner'
            self.assertEqual(self.tag.pending[0]['request'],request)
            self.assertEqual(self.tags.definition_update_status,'Creating tag…')
            self.assertNotIn(TAG_QUEUE,self.window.settings)
            self.assertNotIn(TAG_QUEUE,read_state(self.window._state_repository.path)['settings'])
            timer.start();QTest.keyClick(navigation,Qt.Key_Down);self.wait(lambda:bool(ticks));timer.stop()
            self.tag.pump();self.assertFalse(self.native);self.assertFalse(self.written())
            self.assertEqual(navigation.currentRow(),1);self.assertTrue(ticks)
            self.window._save_state.assert_not_called();release.set()
        self.wait(lambda:bool(self.native))
        self.native[0][1]();self.assertEqual(self.written(),[(request['operation_id'],)])
        self.assertEqual(request,before)

    def test_smart_create_and_ordinary_gesture_project_immediately_but_wait_for_durability(self):
        request=request_for(self.window.lib.source_playlist_catalog,'create_smart',name='New Smart',tags=self.window.lib.source_tags)
        with self.held() as (entered,release):
            self.assertTrue(self.playlist.enqueue(request));self.assertTrue(entered.wait(2))
            self.assertEqual(self.playlist.pending[0]['request'],request)
            self.window._native_pending_changed.assert_called()
            self.assertEqual(self.tags.playlist_update_status,'Creating smart playlist…')
            ordinary=request_for(self.window.lib.source_playlist_catalog,'create',name='Ordinary')
            self.assertTrue(self.playlist.enqueue(ordinary))
            self.assertEqual([job['request'] for job in self.playlist.pending],[request,ordinary])
            self.playlist.pump();self.assertFalse(self.native);self.window._save_state.assert_not_called()
            release.set()
        self.wait(lambda:bool(self.native));self.native[0][1]()
        self.assertEqual(self.written(),[(request['operation_id'],)])

    def test_storage_failure_keeps_exact_intention_until_explicit_retry(self):
        request=self.request()
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.assertTrue(self.tag.enqueue(request));self.wait(lambda:bool(self.tag.admission_error))
        self.assertFalse(self.native);self.assertEqual(self.tag.pending[0]['request'],request)
        self.assertNotIn(TAG_QUEUE,self.window.settings)
        for _ in range(4):self.tags.idle.emit();self.app.processEvents();self.tag.pump()
        self.assertFalse(self.native);self.assertTrue(self.tag.retry())
        self.wait(lambda:not self.tag.admission_pending);self.wait(lambda:bool(self.native))
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][TAG_QUEUE][0]['request'],request)
        self.window._save_state.assert_not_called()

    def test_source_switch_after_durability_refuses_publication_and_retry_does_not_duplicate(self):
        original=self.window.lib;request=self.request()
        with self.held() as (entered,release):
            self.assertTrue(self.tag.enqueue(request));self.assertTrue(entered.wait(2))
            self.window.lib=replace(original,source='/synthetic/other.db',source_library_id='other')
            release.set()
        self.assertTrue(self.tag.admission_error);self.assertFalse(self.native)
        self.assertNotIn(TAG_QUEUE,self.window.settings);self.assertFalse(self.tag.retry())
        self.assertFalse(self.tag.admission.discard())
        self.window.lib=original;self.assertTrue(self.tag.retry());self.wait(lambda:not self.tag.admission_pending)
        rows=read_state(self.window._state_repository.path)['settings'][TAG_QUEUE]
        self.assertEqual([row['request'] for row in rows],[request])

    def test_discard_defaults_to_no_and_clears_ambiguous_durable_stage_through_sparse_commit(self):
        original=self.window.lib
        with self.held() as (entered,release):
            self.assertTrue(self.tag.enqueue(self.request()));self.assertTrue(entered.wait(2))
            self.window.lib=replace(original,source='/synthetic/other.db');release.set()
        self.window.lib=original
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No) as question:
            self.assertFalse(self.tag.discard());self.assertEqual(question.call_args.args[-1],QMessageBox.No)
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.assertTrue(self.tag.discard())
        self.wait(lambda:not self.tag.admission_pending)
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][TAG_QUEUE],[])
        self.assertFalse(self.native);self.assertEqual(self.tag.pending,[])

    def test_restore_generation_blocks_retry_and_discard_cannot_remove_new_owner_queue(self):
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.assertTrue(self.tag.enqueue(self.request()));self.wait(lambda:bool(self.tag.admission_error))
        self.window._state_repository.area.generation+=1
        self.assertFalse(self.tag.retry())
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.assertTrue(self.tag.discard())
        self.assertFalse(self.tag.admission_pending);self.assertNotIn(TAG_QUEUE,self.window.settings)
        self.assertFalse(self.native)

    def test_failed_discard_retry_retries_discard_without_admitting_native_creation(self):
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.assertTrue(self.tag.enqueue(self.request()));self.wait(lambda:bool(self.tag.admission_error))
            with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.assertTrue(self.tag.discard())
            self.wait(lambda:bool(self.tag.admission_error) and not self.tag.admission.active)
        self.assertTrue(self.tag.retry());self.wait(lambda:not self.tag.admission_pending)
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][TAG_QUEUE],[])
        self.assertFalse(self.native);self.assertFalse(self.written())

    def test_multiple_captures_remain_ordered_and_shutdown_flush_retains_all_before_stop(self):
        first,second=self.request('First'),self.request('Second')
        with self.held() as (entered,release):
            self.assertTrue(self.tag.enqueue(first));self.assertTrue(entered.wait(2))
            self.assertTrue(self.tag.enqueue(second));self.assertEqual(len(self.tag.pending),2)
            self.window._shutting_down=True;release.set()
            self.assertTrue(self.tag.flush_admission())
        self.tag.stop()
        rows=read_state(self.window._state_repository.path)['settings'][TAG_QUEUE]
        self.assertEqual([row['request'] for row in rows],[first,second]);self.assertFalse(self.native)
        self.assertFalse(self.tag.admission_pending);self.window._save_state.assert_not_called()

    def test_shutdown_flush_drains_a_preexisting_shared_commit_before_admitting_its_queue(self):
        request=self.request();completed=mock.Mock();failed=mock.Mock()
        with self.held() as (entered,release):
            self.assertTrue(self.committer.submit({('settings','theme_accent'):'#123456'},(),completed,failed))
            self.assertTrue(entered.wait(2));self.assertTrue(self.tag.enqueue(request))
            self.assertFalse(self.tag.admission.active);self.assertTrue(self.tag.admission_pending)
            self.window._shutting_down=True;release.set()
            self.assertTrue(self.tag.flush_admission())
        completed.assert_called_once();failed.assert_not_called()
        state=read_state(self.window._state_repository.path)['settings']
        self.assertEqual(state['theme_accent'],'#123456')
        self.assertEqual(state[TAG_QUEUE][0]['request'],request);self.assertFalse(self.native)

    def test_replaced_controller_or_changed_request_cannot_release_native_barrier(self):
        for change in ('owner','request'):
            with self.subTest(change=change):
                if self.tag.admission.pending:
                    self.assertTrue(self.tag.admission.discard())
                    self.wait(lambda:not self.tag.admission_pending)
                request=self.request(change)
                with self.held() as (entered,release):
                    self.assertTrue(self.tag.enqueue(request));self.assertTrue(entered.wait(2))
                    if change=='owner':self.window.tag_definition_controller=object()
                    else:self.tag.admission.staged[0]['request']['name']='Changed while saving'
                    release.set()
                self.assertTrue(self.tag.admission_error);self.assertFalse(self.native)
                if change=='owner':del self.window.tag_definition_controller
