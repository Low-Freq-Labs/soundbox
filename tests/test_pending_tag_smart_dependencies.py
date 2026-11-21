"""Future My Tags fence smart creation behind durable, verified prerequisites."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from desktop.rekordbox_playlists import QUEUE, PlaylistEditController
from desktop.tag_creation import NewSmartPlaylistDialog
from desktop.tag_creation_controller import QUEUE as DEFINITIONS
from soundbox import rekordbox_playlist_plan as planner, rekordbox_playlist_store as store
from soundbox.library import LibraryTag, Playlist
from soundbox.state_repository import read_state
from tests import test_source_intent_admission as fixture


class PendingTagSmartDependencies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=fixture.SourceIntentAdmission();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.w=self.case.window;self.tags=self.case.tags;self.definitions=self.case.tag
        self.smart=self.case.playlist;self.w.tag_definition_controller=self.definitions
        self.w.playlist_controller=self.smart
        # Keep the real source-admission barrier until both canonical intents
        # settle; the tests then release it and execute controlled source jobs.
        self.tags.admission_paused=True
        self.addCleanup(setattr,self.tags,'admission_paused',False)
        self.source=self.w.lib;self.published=[];self.smart_applied=[]
        self.definition=self.case.request('R&B')
        r=self.definition
        self.future=LibraryTag(r['tag_id'],r['name'],'Genre',r['tag_uuid'],r['category_id'],r['category_uuid'])
        owner=self
        class DefinitionStore:
            def apply(self,request):
                canonical=read_state(owner.w._state_repository.path)['settings'][DEFINITIONS]
                owner.assertEqual(canonical[0]['request'],request)
                with closing(sqlite3.connect(owner.case.database)) as conn:
                    conn.execute('INSERT INTO written VALUES (?)',(request['operation_id'],));conn.commit()
                owner.source=replace(owner.source,source_tags=[*owner.source.source_tags,owner.future])
                return dict(ID=request['tag_id'])
        class SmartStore:
            def apply(self,request):
                saved=read_state(owner.w._state_repository.path)['settings']
                owner.assertEqual(saved[DEFINITIONS],[])
                owner.assertEqual(saved[QUEUE][0]['request'],request)
                owner.assertIsNone(owner.definitions.pending_creation(owner.future.tag_id,owner.future.source_uuid))
                owner.assertIn(owner.future,owner.w.lib.source_tags)
                owner.assertIn(owner.future,owner.tags.lib.source_tags)
                owner.smart_applied.append(deepcopy(request))
                with closing(sqlite3.connect(owner.case.database)) as conn:
                    conn.execute('INSERT INTO written VALUES (?)',(request['operation_id'],));conn.commit()
                catalog=deepcopy(owner.w.lib.source_playlist_catalog)
                catalog['nodes'].append(dict(ID=request['playlist_id'],UUID=request['playlist_uuid'],
                    Name=request['name'],ParentID=request['parent'],Seq=len(catalog['nodes'])+1,
                    Attribute=4,rb_local_deleted=0,SmartList=planner.smart_tag_rule(request['playlist_id'],
                    [tag['id'] for tag in request['tags']])))
                catalog['revision']=planner.revision(catalog['nodes'],catalog['members'])
                owner.source=replace(owner.source,source_playlist_catalog=catalog,
                    playlists=[Playlist(row['Name'],[],[],row['ID']) for row in catalog['nodes']])
                return deepcopy(catalog)
            def committed_transition(self,request):return request['revision'],owner.source.source_playlist_catalog['revision']
        self.definitions.factory=lambda _:DefinitionStore();self.definitions.loader=lambda _:self.source
        self.smart.factory=lambda _:SmartStore();self.tags.readback_library=lambda _:self.source
        def publish(library):
            self.w.lib=library;self.tags.lib=library;self.published.append(library)
        self.w._publish_created_library=publish

    def request(self,name='R&B songs'):
        return store.request_for(self.w.lib.source_playlist_catalog,'create_smart',name=name,tags=[self.future])

    def enqueue_both(self):
        self.assertTrue(self.definitions.enqueue(self.definition))
        request=self.request();self.assertTrue(self.smart.enqueue(request))
        self.case.wait(lambda:not self.definitions.admission_pending and not self.smart.admission_pending)
        self.tags.admission_paused=False;self.tags.idle.emit()
        self.case.wait(lambda:len(self.case.native)==1)
        self.assertEqual(self.case.native[0][0],'tag-definitions')
        return request

    def deliver(self,index,*,error=''):
        _kind,work,completed=self.case.native[index]
        result=None if error else work();self.tags.busy=False
        completed(SimpleNamespace(error=error,result=result));self.tags.changed.emit();self.tags.idle.emit()

    def test_smart_admission_waits_for_durable_definition_then_source_waits_for_publication(self):
        before=deepcopy(self.w.lib)
        with self.case.held() as (entered,release):
            self.assertTrue(self.definitions.enqueue(self.definition));self.assertTrue(entered.wait(2))
            request=self.request();self.assertTrue(self.smart.enqueue(request))
            self.assertTrue(self.smart.admission.staged)
            self.assertEqual(self.smart.pending[0]['tag_creation_dependencies'][0]['operation_id'],self.definition['operation_id'])
            self.assertNotIn(QUEUE,read_state(self.w._state_repository.path)['settings'])
            self.assertFalse(self.case.native);self.assertEqual(self.w.lib,before)
            self.assertTrue(self.smart.has_pending_tag_creation_dependency(self.definition['operation_id']))
            release.set()
        self.case.wait(lambda:not self.smart.admission_pending)
        self.tags.admission_paused=False;self.tags.idle.emit()
        self.case.wait(lambda:len(self.case.native)==1)
        saved=read_state(self.w._state_repository.path)['settings']
        self.assertEqual(saved[DEFINITIONS][0]['request'],self.definition)
        self.assertEqual(saved[QUEUE][0]['request'],request)
        self.assertEqual(self.smart._tag_creation_wait(self.smart.pending[0]),(True,''))
        self.assertFalse(self.case.written());self.assertEqual(self.w.lib,before)
        with self.case.held() as (entered,release):
            self.deliver(0);self.assertTrue(entered.wait(2))
            self.assertNotIn(self.future,self.w.lib.source_tags)
            self.assertEqual(len(self.case.native),1);self.smart.pump()
            self.assertEqual(len(self.case.native),1);release.set()
        self.case.wait(lambda:len(self.case.native)==2)
        self.assertEqual(self.case.native[1][0],'playlists')
        self.deliver(1);self.case.wait(lambda:not self.smart.pending and not self.smart.admission_pending)
        self.assertEqual(self.case.written(),[(self.definition['operation_id'],),(request['operation_id'],)])
        self.assertEqual(len(self.smart_applied),1)
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][QUEUE],[])
        self.w._save_state.assert_not_called()

    def test_failed_definition_marks_exact_smart_intention_for_durable_attention_without_source_call(self):
        request=self.enqueue_both();proof=deepcopy(self.smart.pending[0]['tag_creation_dependencies'])
        self.deliver(0,error='Generated definition failure')
        self.case.wait(lambda:bool(self.smart.pending[0]['error']) and not self.smart.admission_pending)
        saved=read_state(self.w._state_repository.path)['settings'][QUEUE][0]
        self.assertEqual(saved['request'],request);self.assertEqual(saved['tag_creation_dependencies'],proof)
        self.assertIn('Generated definition failure',saved['error'])
        self.assertEqual(len(self.case.native),1);self.assertFalse(self.smart_applied);self.assertFalse(self.case.written())
        self.assertTrue(self.smart.has_pending_tag_creation_dependency(self.definition['operation_id']))

    def test_definition_admission_failure_cannot_leave_a_durable_orphan_smart_request(self):
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('No space for definition')) as commit:
            self.assertTrue(self.definitions.enqueue(self.definition))
            request=self.request();self.assertTrue(self.smart.enqueue(request))
            self.case.wait(lambda:bool(self.definitions.admission_error) and bool(self.smart.admission_error))
        self.assertEqual(commit.call_count,1)
        saved=read_state(self.w._state_repository.path)['settings']
        self.assertNotIn(DEFINITIONS,saved);self.assertNotIn(QUEUE,saved)
        self.assertEqual(self.smart.pending[0]['request'],request)
        self.assertIn('No space for definition',self.smart.admission_error)
        self.assertFalse(self.case.native);self.assertFalse(self.case.written())

    def test_earlier_failed_definition_gives_later_tag_smart_request_retainable_attention(self):
        earlier=self.case.request('Earlier tag')
        self.assertTrue(self.definitions.enqueue(earlier))
        self.assertTrue(self.definitions.enqueue(self.definition))
        request=self.request();self.assertTrue(self.smart.enqueue(request))
        self.case.wait(lambda:not self.definitions.admission_pending and not self.smart.admission_pending)
        self.tags.admission_paused=False;self.tags.idle.emit()
        self.case.wait(lambda:len(self.case.native)==1)
        self.deliver(0,error='Earlier source definition failed')
        self.case.wait(lambda:bool(self.smart.pending[0]['error']) and not self.smart.admission_pending)
        saved=read_state(self.w._state_repository.path)['settings'][QUEUE][0]
        self.assertEqual(saved['request'],request)
        self.assertEqual(saved['tag_creation_dependencies'][0]['operation_id'],self.definition['operation_id'])
        self.assertIn('Earlier source definition failed',saved['error'])
        self.assertFalse(self.smart_applied);self.assertFalse(self.case.written())

    def test_missing_or_replaced_creation_is_not_adopted_after_restart(self):
        self.enqueue_both();job=deepcopy(self.smart.pending[0])
        self.smart.timer.stop();self.definitions.timer.stop()
        original=deepcopy(self.w.settings[DEFINITIONS])
        for kind in ('missing','replacement'):
            with self.subTest(kind=kind):
                changed=deepcopy(original)
                if kind=='missing':changed=[]
                else:changed[0]['request']['operation_id']='f'*32
                self.w.settings[DEFINITIONS]=changed
                restarted=PlaylistEditController(self.w,store_factory=lambda _:None)
                self.addCleanup(restarted.stop)
                waiting,error=restarted._tag_creation_wait(job)
                self.assertFalse(waiting);self.assertTrue(error)
                self.assertEqual(restarted.pending[0]['request'],job['request'])
        self.w.settings[DEFINITIONS]=original
        self.assertFalse(self.smart_applied);self.assertEqual(len(self.case.native),1)

    def test_persisted_dependency_rejects_mismatched_source_operation_and_tag_bindings(self):
        self.enqueue_both();original=deepcopy(self.w.settings[QUEUE])
        for field,value in (('source','/different/master.db'),('library_id','other'),('operation_id','invalid'),
                            ('tag_uuid','replaced'),('name','Other'),('category_uuid','other')):
            with self.subTest(field=field):
                self.w.settings[QUEUE]=deepcopy(original)
                self.w.settings[QUEUE][0]['tag_creation_dependencies'][0][field]=value
                self.assertTrue(self.smart.queue_error())
                self.smart.pump();self.assertFalse(self.smart_applied)
        self.w.settings[QUEUE]=original

    def test_legacy_verified_tag_request_keeps_ordinary_worker_freshness_path(self):
        known=self.w.lib.source_tags[0]
        request=store.request_for(self.w.lib.source_playlist_catalog,'create_smart',name='Existing',tags=[known])
        self.assertTrue(self.smart.enqueue(request))
        self.tags.admission_paused=False;self.tags.idle.emit()
        self.case.wait(lambda:len(self.case.native)==1)
        self.assertNotIn('tag_creation_dependencies',self.smart.pending[0])
        self.assertEqual(self.case.native[0][0],'playlists')
        self.assertEqual(self.case.native[0][1].__name__,'work')


class PendingTagSmartForm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_creation_placement_ui as forms
        forms.CreationPlacement.setUpClass()

    def test_real_form_can_capture_literal_pending_tag_before_either_intention_is_durable(self):
        from tests import test_creation_placement_ui as forms
        from soundbox import rekordbox_tag_definitions as definitions
        case=forms.CreationPlacement();case.setUp();self.addCleanup(case.doCleanups)
        w=case.w;creator=w.tag_definition_controller;creator.factory=lambda _:None
        before=deepcopy(w.lib)
        category=w.lib.source_tag_categories[0]
        request=definitions.request_for(w.lib.source_library_id,category.category_id,category.source_uuid,'R&B')
        observed=[]
        def accepted(dialog):
            matches=[dialog.tags.item(index) for index in range(dialog.tags.count())
                     if dialog.tags.item(index).data(Qt.UserRole)==(request['tag_id'],request['tag_uuid'])]
            self.assertEqual(len(matches),1);self.assertEqual(matches[0].text(),'R&B')
            matches[0].setCheckState(Qt.Checked);dialog.name.setText('R&B songs')
            observed.append(True);return QDialog.Accepted
        with case.case.held_canonical() as (entered,release):
            self.assertTrue(creator.enqueue(request));self.assertTrue(entered.wait(2))
            with mock.patch.object(NewSmartPlaylistDialog,'exec',new=accepted):
                self.assertTrue(w._new_smart_playlist())
            job=case.controller.pending[0]
            self.assertEqual(job['request']['tags'][0]['name'],'R&B')
            self.assertEqual(job['tag_creation_dependencies'][0]['operation_id'],request['operation_id'])
            target=job['request']['playlist_id']
            self.assertIsNotNone(w._find_tree_item('',target))
            projected=w._native_presentation()
            self.assertEqual(next(playlist.keys for playlist in projected.playlists if playlist.pid==target),[])
            self.assertEqual(projected.source_tags,before.source_tags)
            self.assertIs(projected.tracks_by_id,w.lib.tracks_by_id)
            self.assertEqual(w.lib,before)
            self.assertNotIn(QUEUE,read_state(w._state_repository.path)['settings'])
            # A replacement parent operation cannot lend its same ID/name to
            # this older pending row or its cached projection.
            original=request['operation_id'];request['operation_id']='f'*32
            creator.admission.staged[0]['request']['operation_id']='f'*32
            try:
                self.assertFalse(any(playlist.pid==target for playlist in w._native_presentation().playlists))
            finally:
                request['operation_id']=original
                creator.admission.staged[0]['request']['operation_id']=original
            case.case.source.assert_not_called();release.set()
        saved=case.case.durable()
        self.assertEqual(saved[0]['request']['name'],'R&B songs')
        self.assertEqual(saved[0]['request']['tags'][0]['id'],request['tag_id'])
        self.assertEqual(w.lib,before);self.assertEqual(observed,[True])


class PendingPlaylistPreparedArt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_creation_placement_ui as forms
        forms.CreationPlacement.setUpClass()

    def setUp(self):
        from tests import test_creation_placement_ui as forms
        self.case=forms.CreationPlacement();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.w=self.case.w

    def test_removal_and_reorder_reuse_complete_rendered_art_and_keep_surviving_selection(self):
        w=self.w
        w.model.set_rows([dict(id='t',title='One',art='cover'),dict(id='u',title='Two',art='artist'),
                          dict(id='removed',title='Three',art='')])
        w.table.selectAll();w.table.selectionModel().setCurrentIndex(w.model.index(1,0),
            w.table.selectionModel().SelectionFlag.NoUpdate)
        source=deepcopy(w.lib)
        with (mock.patch.object(w,'_playlist_rows',return_value=[dict(id='u',title='Two'),dict(id='t',title='One')]),
              mock.patch.object(w.art_cache,'status_kinds',side_effect=AssertionError('Artwork read waits on canonical lock'))):
            w._native_reload_pending_playlist('Folder / Smart')
        self.assertEqual([(row['id'],row['art']) for row in w.model._all_rows],[('u','artist'),('t','cover')])
        self.assertEqual(set(w._selected_ids()),{'t','u'})
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'],'u')
        self.assertEqual(w.lib,source)

    def test_unknown_art_retains_the_ordinary_complete_refresh_instead_of_inventing_a_status(self):
        w=self.w;w.model.set_rows([dict(id='t',title='One',art='cover')])
        with (mock.patch.object(w,'_playlist_rows',return_value=[dict(id='u',title='New row')]),
              mock.patch.object(w,'_reload_current_view') as fallback):
            w._native_reload_pending_playlist('Folder / Smart')
        fallback.assert_called_once_with()
        self.assertEqual(w.model._all_rows,[dict(id='t',title='One',art='cover')])

    def test_device_page_does_not_rebuild_the_hidden_desktop_view(self):
        w=self.w;before=w.model._all_rows
        with (mock.patch.object(w.stack,'currentIndex',return_value=1),
              mock.patch.object(w,'_playlist_rows',side_effect=AssertionError('Hidden desktop rows requested')),
              mock.patch.object(w,'_reload_current_view',side_effect=AssertionError('Hidden desktop refresh'))):
            w._native_reload_pending_playlist('Folder / Smart')
        self.assertIs(w.model._all_rows,before)

    def test_readback_only_tag_publication_retry_stays_available_without_a_fake_discard(self):
        w=self.w;original=w.tag_definition_controller
        retry=mock.Mock();discard=mock.Mock()
        failed=SimpleNamespace(pending=[],publication_retry_pending=True,publication_retry_ready=True,
                               retry=retry,discard=discard)
        w.tag_definition_controller=failed
        self.addCleanup(setattr,w,'tag_definition_controller',original)
        menu=w._native_menu(None);self.addCleanup(menu.deleteLater)
        actions={action.text():action for action in menu.actions()}
        self.assertIn('Retry tag changes',actions)
        self.assertTrue(actions['Retry tag changes'].isEnabled())
        self.assertNotIn('Discard pending tag changes',actions)
        actions['Retry tag changes'].trigger();retry.assert_called_once_with()
        discard.assert_not_called()

    def test_exact_failed_tag_ack_can_retry_with_owned_children_without_enabling_discard(self):
        w=self.w;original=w.tag_definition_controller
        retry=mock.Mock();discard=mock.Mock()
        failed=SimpleNamespace(pending=[dict(error='ACK needs retry')],publication_retry_pending=False,
                               acknowledgement_retry_ready=True,retry=retry,discard=discard)
        w.tag_definition_controller=failed
        self.addCleanup(setattr,w,'tag_definition_controller',original)
        with mock.patch('desktop.intent_review.capture_discard',return_value=None):
            menu=w._native_menu(None)
        self.addCleanup(menu.deleteLater)
        actions={action.text():action for action in menu.actions()}
        self.assertTrue(actions['Retry tag changes'].isEnabled())
        self.assertFalse(actions['Discard pending tag changes'].isEnabled())
        actions['Retry tag changes'].trigger();retry.assert_called_once_with()
        discard.assert_not_called()


if __name__=='__main__':unittest.main()
