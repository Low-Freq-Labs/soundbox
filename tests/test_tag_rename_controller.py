"""Exact rename/assignment custody through held real canonical workers."""
from copy import deepcopy
from contextlib import closing
from dataclasses import asdict,replace
from types import SimpleNamespace
from pathlib import Path
import sqlite3
import json
import unittest
from unittest import mock

from desktop.tag_creation_controller import TagDefinitionController,QUEUE
from desktop.tag_controller import TagEditController
from soundbox import rekordbox_tag_rename as rename
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import LibraryTag,Track,Playlist
from soundbox.state_repository import read_state
from tests import test_source_intent_admission as fixtures


FIRST='00000000-0000-4000-8000-000000000011'
SECOND='00000000-0000-4000-8000-000000000012'


class TagRenameController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=fixtures.SourceIntentAdmission();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.window=w=self.case.window;self.tags=self.case.tags
        w.lib.source_tags=[LibraryTag('11','First','Genre',FIRST,'1','1'),LibraryTag('12','Second','Genre',SECOND,'1','1')]
        w.lib.tracks_by_id={'one':Track('one','One','Artist','file:///synthetic/one.wav',source_uuid='track-one',
            source_tag_ids=['11'],source_native_id='1')}
        w.lib.source_playlist_catalog={'nodes':[dict(ID='20',Attribute=4,SmartList='opaque ID11 rule')],
            'members':[],'revision':'a'*64}
        w.lib.playlists=[Playlist('Smart',[],['one'],'20')]
        self.tags.lib=w.lib;self.tags._ready=True
        self.published=[];self.written=[];self.native_tags=list(w.lib.source_tags)
        owner=self
        class Store:
            def apply(self,request):
                saved=read_state(w._state_repository.path)['settings'][QUEUE]
                owner.assertEqual(saved[0]['request'],request,'Source write preceded exact durable rename')
                if request['operation_id'] not in owner.written:
                    with closing(sqlite3.connect(owner.case.database)) as connection:
                        connection.execute('INSERT INTO written VALUES (?)',(request['operation_id'],));connection.commit()
                    owner.written.append(request['operation_id'])
                    owner.native_tags=[replace(tag,name=request['new_name']) if tag.tag_id==request['tag_id'] else tag
                                       for tag in owner.native_tags]
                return dict(ID=request['tag_id'],UUID=request['tag_uuid'],Name=request['new_name'])
        self.controller=TagDefinitionController(w,rename_store_factory=lambda _:Store(),
            loader=lambda _:replace(w.lib,source_tags=list(self.native_tags)))
        self.addCleanup(self.controller.stop);w.tag_definition_controller=self.controller
        def publish(library):
            self.published.append(library);w.lib=library;self.tags.lib=library
        w._publish_created_library=publish

    def request(self,tag_id='11',name='Renamed'):
        tag=next(tag for tag in self.window.lib.source_tags if tag.tag_id==tag_id)
        return rename.request_for(self.window.lib.source_library_id,tag.category_id,tag.category_uuid,
            tag.tag_id,tag.source_uuid,tag.name,name)

    def deliver(self,index=0):
        _,work,completed=self.case.native[index];result=work();self.tags.busy=False
        completed(SimpleNamespace(error='',result=result));self.tags.changed.emit();self.tags.idle.emit()

    def admitted(self,request):
        self.assertTrue(self.controller.enqueue_rename(request));self.case.wait(lambda:bool(self.case.native))

    def staged_successor(self):
        self.controller.factory=lambda _:None
        request=definitions.request_for(self.window.lib.source_library_id,'1','1','Later')
        self.assertTrue(self.controller.enqueue(request))
        self.assertEqual(self.controller.admission.staged[0]['request'],request)
        return request

    def source_failed(self):
        self.tags.busy=False
        self.case.native[0][2](SimpleNamespace(error='Native source refused',result=None))
        self.tags.changed.emit();self.tags.idle.emit()

    def test_failed_source_head_and_staged_successor_preserve_exact_custody_through_error_ack(self):
        first=self.request();self.admitted(first);second=self.staged_successor()
        before=deepcopy(self.window.settings[QUEUE]);staged=deepcopy(self.controller.admission.staged)
        with self.case.held() as (entered,release):
            self.source_failed();self.assertTrue(entered.wait(2))
            self.assertTrue(self.controller.ack.pending);self.assertTrue(self.tags.source_ack_pending)
            self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE],before)
            self.assertEqual(self.controller.admission._base,before)
            self.assertEqual(self.controller.admission.staged,staged);release.set()
        self.case.wait(lambda:not self.controller.admission_pending)
        saved=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual([row['request'] for row in saved],[first,second])
        self.assertEqual([row['error'] for row in saved],['Native source refused',''])
        self.assertFalse(self.controller.admission.error);self.assertFalse(self.written)
        self.assertEqual(len(self.case.native),1);self.window._save_state.assert_not_called()

    def test_error_ack_disk_failure_retry_preserves_staged_requests_without_repeating_native_work(self):
        first=self.request();self.admitted(first);second=self.staged_successor()
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.source_failed();self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertEqual([row['request'] for row in self.controller.pending],[first,second])
        self.assertEqual(self.controller.admission._base[0]['error'],'')
        self.assertTrue(self.tags.source_ack_pending);self.assertFalse(self.published)
        self.assertTrue(self.controller.retry());self.case.wait(lambda:not self.controller.admission_pending)
        saved=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual([row['request'] for row in saved],[first,second])
        self.assertEqual(saved[0]['error'],'Native source refused')
        self.assertFalse(self.controller.admission.error);self.assertFalse(self.written)
        self.assertEqual(len(self.case.native),1);self.window._save_state.assert_not_called()

    def test_error_ack_lost_acknowledgement_reconciles_durable_error_and_exact_staged_suffix(self):
        first=self.request();self.admitted(first);second=self.staged_successor()
        original=self.window._state_repository.commit_patch
        def lost_ack(patch):
            original(patch)
            raise OSError('Acknowledgement lost after durable commit')
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=lost_ack):
            self.source_failed();self.case.wait(lambda:bool(self.controller.ack.error))
        canonical=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual(canonical[0]['error'],'Native source refused')
        self.assertEqual(self.controller.admission._base[0]['error'],'')
        self.assertTrue(self.controller.retry());self.case.wait(lambda:not self.controller.admission_pending)
        saved=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual([row['request'] for row in saved],[first,second])
        self.assertEqual([row['error'] for row in saved],['Native source refused',''])
        self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_source_switch_withheld_error_ack_requires_original_source_then_preserves_both_intents(self):
        first=self.request();self.admitted(first);second=self.staged_successor()
        original=self.window.lib
        with self.case.held() as (entered,release):
            self.source_failed();self.assertTrue(entered.wait(2))
            self.window.lib=replace(original,source='/synthetic/other.db',source_library_id='other');release.set()
        self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertFalse(self.controller.retry());self.assertTrue(self.tags.source_ack_pending)
        self.assertEqual(self.controller.admission.staged[0]['request'],second)
        self.window.lib=original;self.assertTrue(self.controller.retry())
        self.case.wait(lambda:not self.controller.admission_pending)
        saved=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual([row['request'] for row in saved],[first,second])
        self.assertEqual(saved[0]['error'],'Native source refused')
        self.assertFalse(self.published);self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_explicit_retry_clears_failed_head_and_saves_withheld_suffix_before_source_restart(self):
        first=self.request();self.admitted(first);second=self.staged_successor()
        # Let the error ACK succeed, then hold the suffix admission on disk.
        original=self.window._state_repository.commit_patch
        def commit(patch):
            jobs=patch.edits()[0].get(('settings',QUEUE),[])
            if len(jobs)==2:raise OSError('Suffix admission failed')
            return original(patch)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=commit):
            self.source_failed();self.case.wait(lambda:bool(self.controller.admission.error))
        self.assertEqual(self.window.settings[QUEUE][0]['error'],'Native source refused')
        self.assertEqual(self.controller.admission.staged[0]['request'],second)
        with self.case.held() as (entered,release):
            self.assertTrue(self.controller.retry());self.assertTrue(entered.wait(2))
            self.assertEqual(len(self.case.native),1)
            self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE][0]['error'],
                             'Native source refused')
            self.assertEqual(self.controller.admission.staged[0]['request'],second);release.set()
        self.case.wait(lambda:len(self.case.native)==2)
        saved=read_state(self.window._state_repository.path)['settings'][QUEUE]
        self.assertEqual([row['request'] for row in saved],[first,second])
        self.assertEqual([row['error'] for row in saved],['',''])
        self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_discard_failed_error_ack_reviews_and_durably_clears_head_plus_staged_suffix(self):
        from PySide6.QtWidgets import QMessageBox
        first=self.request();self.admitted(first);second=self.staged_successor()
        before=deepcopy(self.window.lib)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.source_failed();self.case.wait(lambda:bool(self.controller.ack.error))
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No) as question:
            self.assertFalse(self.controller.discard())
            self.assertEqual(question.call_args.args[-1],QMessageBox.No)
            self.assertIn('Discard 2 pending',question.call_args.args[2])
        self.assertEqual([row['request'] for row in self.controller.pending],[first,second])
        with self.case.held() as (entered,release):
            with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
                self.assertTrue(self.controller.discard())
            self.case.wait(entered.is_set);self.assertTrue(self.controller.pending);release.set()
        self.case.wait(lambda:not self.controller.admission_pending)
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE],[])
        self.assertFalse(self.controller.pending);self.assertEqual(self.window.lib,before)
        self.assertFalse(self.written);self.assertFalse(self.published);self.window._save_state.assert_not_called()

    def test_discard_after_error_ack_clears_durable_failed_head_and_unsaved_suffix_as_reviewed(self):
        from PySide6.QtWidgets import QMessageBox
        first=self.request();self.admitted(first);self.staged_successor()
        original=self.window._state_repository.commit_patch
        def commit(patch):
            if len(patch.edits()[0].get(('settings',QUEUE),[]))==2:raise OSError('Suffix could not save')
            return original(patch)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=commit):
            self.source_failed();self.case.wait(lambda:bool(self.controller.admission.error))
        self.assertFalse(self.controller.ack.pending)
        self.assertEqual(self.window.settings[QUEUE][0]['error'],'Native source refused')
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes) as question:
            self.assertTrue(self.controller.discard());self.assertIn('Discard 2 pending',question.call_args.args[2])
        self.case.wait(lambda:not self.controller.admission_pending)
        self.assertEqual(read_state(self.window._state_repository.path)['settings'][QUEUE],[])
        self.assertFalse(self.controller.pending);self.assertFalse(self.written)
        self.window._save_state.assert_not_called()

    def test_restore_during_error_ack_keeps_new_state_and_requires_explicit_old_staged_discard(self):
        from soundbox.state_repository import StateRepository
        from PySide6.QtWidgets import QMessageBox
        first=self.request();self.admitted(first);second=self.staged_successor()
        with self.case.held() as (entered,release):
            self.source_failed();self.assertTrue(entered.wait(2))
            restored=StateRepository(self.case.root/'restored/library.json')
            new_state={'settings':{'new_owner':True}}
            restored.save(new_state);self.window._state_repository=restored
            self.window.settings=deepcopy(new_state['settings']);release.set()
        self.case.wait(lambda:not self.controller.ack.pending and not self.window._state_publication)
        self.assertFalse(self.tags.source_ack_pending);self.assertFalse(self.controller.retry())
        self.assertEqual(self.controller.admission.staged[0]['request'],second)
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.assertTrue(self.controller.discard())
        self.assertFalse(self.controller.admission_pending)
        self.assertEqual(read_state(restored.path),new_state);self.assertFalse(self.published)
        self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_restore_before_failed_worker_callback_cannot_adopt_identical_head_in_new_repository(self):
        from soundbox.state_repository import StateRepository
        first=self.request();self.admitted(first)
        queue=deepcopy(self.window.settings[QUEUE])
        restored=StateRepository(self.case.root/'restored/library.json')
        new_state={'settings':{QUEUE:queue,'new_owner':True}}
        restored.save(new_state);self.window._state_repository=restored
        self.window.settings=deepcopy(new_state['settings']);self.window._state_maintenance=True
        self.source_failed()
        self.assertFalse(self.controller.ack.pending);self.assertFalse(self.case.committer.pending)
        self.assertEqual(self.window.settings,new_state['settings'])
        self.assertEqual(read_state(restored.path),new_state);self.assertFalse(self.published)
        self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_controller_takeover_before_failed_worker_callback_cannot_adopt_identical_head(self):
        self.admitted(self.request());before=deepcopy(self.window.settings)
        canonical=read_state(self.window._state_repository.path)
        self.window.tag_definition_controller=object()
        self.source_failed()
        self.assertFalse(self.controller.ack.pending);self.assertFalse(self.case.committer.pending)
        self.assertEqual(self.window.settings,before)
        self.assertEqual(read_state(self.window._state_repository.path),canonical)
        self.assertFalse(self.published);self.assertFalse(self.written);self.window._save_state.assert_not_called()

    def test_old_creation_acceptance_cannot_admit_a_rename_and_exact_target_guards_precede_staging(self):
        request=self.request();self.controller.rename_factory=None
        self.assertIn('separate acceptance',self.controller.rename_reason())
        self.assertFalse(self.controller.enqueue_rename(request));self.assertFalse(self.controller.pending)
        self.controller.rename_factory=lambda _:None
        for field,value in (('name','Changed'),('tag_uuid','00000000-0000-4000-8000-000000000099'),
                ('category_uuid','replaced'),('new_name','sEcOnD')):
            with self.subTest(field=field):
                self.assertFalse(self.controller.enqueue_rename({**request,field:value}))
                self.assertFalse(self.controller.pending)
        self.assertFalse(self.case.native)

    def test_rename_admission_and_ack_wait_for_real_durability_preserving_ids_assignments_and_rules(self):
        request=self.request();before=deepcopy(self.window.lib)
        with self.case.held() as (entered,release):
            self.assertTrue(self.controller.enqueue_rename(request));self.assertTrue(entered.wait(2))
            self.assertEqual(self.controller.pending[0]['request'],request)
            self.assertFalse(self.case.native);self.assertFalse(self.written);self.assertEqual(self.window.lib,before)
            release.set()
        self.case.wait(lambda:bool(self.case.native))
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2));self.assertFalse(self.published)
            self.assertEqual(self.window.lib,before);self.assertTrue(self.tags.source_job_owned);release.set()
        self.case.wait(lambda:not self.controller.admission_pending)
        self.assertFalse(self.controller.pending);self.assertEqual(self.window.lib.source_tags[0].name,'Renamed')
        self.assertEqual(self.window.lib.source_tags[0].source_uuid,FIRST)
        self.assertEqual(self.window.lib.source_playlist_catalog,before.source_playlist_catalog)
        self.assertEqual(self.window.lib.tracks_by_id['one'].source_tag_ids,['11'])
        self.assertEqual(self.window.lib.playlists[0].keys,['one']);self.window._save_state.assert_not_called()

    def test_storage_failure_retries_ack_without_repeating_source(self):
        request=self.request();self.admitted(request)
        with mock.patch.object(self.window._state_repository,'commit_patch',side_effect=OSError('Disk full')):
            self.deliver();self.case.wait(lambda:bool(self.controller.ack.error))
        self.assertFalse(self.published);self.assertEqual(self.written,[request['operation_id']])
        self.assertTrue(self.controller.retry());self.case.wait(lambda:not self.controller.admission_pending)
        self.assertEqual(self.written,[request['operation_id']]);self.assertEqual(len(self.published),1)
        self.window._save_state.assert_not_called()

    def test_restore_takeover_retires_only_old_ack_without_publication_or_mutating_new_state(self):
        from soundbox.state_repository import StateRepository
        request=self.request();self.admitted(request)
        with self.case.held() as (entered,release):
            self.deliver();self.assertTrue(entered.wait(2))
            restored=StateRepository(self.case.root/'restored/library.json');restored.save({'settings':{'new_owner':True}})
            self.window._state_repository=restored;self.window.settings={'new_owner':True};release.set()
        self.case.wait(lambda:not self.controller.ack.pending and not self.window._state_publication)
        self.assertFalse(self.published);self.assertFalse(self.tags.source_ack_pending)
        self.assertEqual(read_state(restored.path),{'settings':{'new_owner':True}})
        self.assertEqual(self.written,[request['operation_id']])

    def test_completed_replay_from_a_refreshed_new_label_is_verified_without_rewriting_native(self):
        request=self.request();self.admitted(request)
        # A crash/restart may load the true new label while the old captured
        # request still awaits receipt reconciliation; its identity stays exact.
        self.native_tags=[replace(tag,name='Renamed') if tag.tag_id=='11' else tag for tag in self.native_tags]
        self.written.append(request['operation_id'])
        with closing(sqlite3.connect(self.case.database)) as connection:
            connection.execute('INSERT INTO written VALUES (?)',(request['operation_id'],));connection.commit()
        self.window.lib=replace(self.window.lib,source_tags=list(self.native_tags));self.tags.lib=self.window.lib
        self.deliver();self.case.wait(lambda:not self.controller.admission_pending)
        self.assertFalse(self.controller.pending);self.assertEqual(self.written,[request['operation_id']])
        self.assertEqual(len(self.published),1)
        self.assertEqual(self.case.written(),[(request['operation_id'],)])

    def test_same_category_pending_destination_is_reserved_but_another_category_label_is_allowed(self):
        self.tags.busy=True
        self.assertTrue(self.controller.enqueue_rename(self.request(name='Reserved')))
        self.assertFalse(self.controller.enqueue_rename(self.request('12','rEsErVeD')))
        self.window.lib.source_tags.append(LibraryTag('21','Reserved','Other',
            '00000000-0000-4000-8000-000000000021','2','2'))
        self.assertEqual(self.controller.pending[0]['request']['new_name'],'Reserved')
        self.tags.busy=False
        self.assertTrue(self.controller.flush_admission())

    def test_target_assignment_waits_before_rename_while_unrelated_assignment_remains_allowed(self):
        self.tags.busy=True
        self.assertTrue(self.tags.request(['one'],'11',True))
        self.assertTrue(self.controller.rename_reason('11',FIRST))
        self.assertFalse(self.controller.rename_reason('12',SECOND))
        self.assertFalse(self.controller.enqueue_rename(self.request()))
        self.tags.admission_paused=True;self.tags.busy=False
        self.case.wait(lambda:not self.tags.tag_state_lane.pending)
        self.assertTrue(self.controller.rename_reason('11',FIRST))
        self.assertFalse(self.controller.rename_reason('12',SECOND))

    def test_pending_rename_blocks_only_exact_tag_click_and_other_tag_intent_survives_rename_ack(self):
        request=self.request();self.admitted(request)
        self.assertTrue(self.controller.assignment_reason('11',FIRST))
        self.assertFalse(self.controller.assignment_reason('12',SECOND))
        self.assertFalse(self.tags.request(['one'],'11',False))
        self.assertTrue(self.tags.request(['one'],'12',True))
        intended=self.tags.tag_state_lane.queue[-1]['request']
        self.tags.admission_paused=True
        self.deliver();self.case.wait(lambda:not self.controller.admission_pending and not self.tags.tag_state_lane.pending)
        self.assertEqual(self.window.lib.source_tags[0].name,'Renamed')
        saved=read_state(self.window._state_repository.path)['settings']['pending_tag_assignments'][0]['request']
        self.assertEqual((saved['tag_id'],saved['tag_uuid'],saved['tag_name']),('12',SECOND,'Second'))
        self.assertEqual(saved['operation_id'],intended.operation_id)
        self.assertFalse(self.controller.assignment_reason('11',FIRST))

    def test_readback_cannot_ack_wrong_label_changed_assignments_rules_or_smart_membership(self):
        before=self.window.lib;request=self.request()
        after=replace(before,source_tags=[replace(tag,name='Renamed') if tag.tag_id=='11' else tag for tag in before.source_tags])
        check=self.controller._renamed_tags_match
        self.assertTrue(check(before,after,request))
        variants=[replace(after,source_tags=before.source_tags),
            replace(after,source_playlist_catalog={**after.source_playlist_catalog,'revision':'b'*64}),
            replace(after,playlists=[Playlist('Smart',[],[],'20')]),
            replace(after,tracks_by_id={'one':replace(before.tracks_by_id['one'],source_tag_ids=[])})]
        for invalid in variants:self.assertFalse(check(before,invalid,request))

    def test_shared_rename_journal_needs_own_authority_and_dispatches_only_rename_recovery(self):
        root=self.tags._write_root(self.window.lib)
        root.mkdir(parents=True,mode=0o700)
        (root/'transaction.json').write_text(json.dumps({'format':rename.FORMAT}))
        with mock.patch.object(rename,'TagRenameStore') as store:
            with self.assertRaisesRegex(RuntimeError,'separately accepted'):
                TagEditController._recover_pending(self.tags,self.window.lib)
            store.assert_not_called()
            authority=object()
            with mock.patch.object(rename,'load_authority',return_value=authority) as load:
                TagEditController._recover_pending(self.tags,self.window.lib)
            load.assert_called_once_with(self.tags.data_root/rename.AUTHORITY_FILE,
                Path(self.tags.lib.source),
                self.tags.lib.source_library_id)
            store.assert_called_once_with(authority,root);store.return_value.recover.assert_called_once_with()

    def test_startup_can_recover_with_only_its_accepted_rename_authority_and_rejects_foreign_source(self):
        authority=rename.TagRenameAuthority(str(self.window.lib.source),self.window.lib.source_library_id,'0'*64,
            accepted=True,protocol_profile=rename.PROVISIONAL_PROTOCOL.profile)
        path=self.tags.data_root/rename.AUTHORITY_FILE;path.write_text(json.dumps(asdict(authority)))
        with mock.patch.dict(rename.VERIFIED_PROTOCOLS,{rename.PROVISIONAL_PROTOCOL.profile:rename.PROVISIONAL_PROTOCOL}):
            self.tags.recover_before_read(self.window.lib.source)
        self.tags._recover_pending.assert_called_once()
        context=self.tags._recover_pending.call_args.args[0]
        self.assertEqual((str(context.source),context.source_library_id),(str(self.window.lib.source),self.window.lib.source_library_id))
        self.tags._recover_pending.reset_mock()
        with mock.patch.dict(rename.VERIFIED_PROTOCOLS,{rename.PROVISIONAL_PROTOCOL.profile:rename.PROVISIONAL_PROTOCOL}):
            self.tags.recover_before_read('/synthetic/another.db')
        self.tags._recover_pending.assert_not_called()
