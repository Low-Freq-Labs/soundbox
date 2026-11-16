"""Crash-safe bulk tag gestures and source readback on disposable fixtures."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.tag_assignment_intents import KEY, verified_readback
from desktop.tag_controller import TagEditController
from soundbox import rekordbox_tags as tags
from soundbox import rekordbox_write as writer
from tests.test_prep_tags import _library
from tests import test_rekordbox_tags as tag_fixture


class DurableAssignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = _library();self.calls=[];self.receipts={}
        self.state = SimpleNamespace(settings={}, _save_state=lambda:True)
        self.persisted=[];self.save_ok=True;self.fail_ack=False
        def save():
            if self.fail_ack and self.calls and not self.state.settings.get(KEY):return False
            if not self.save_ok:return False
            self.persisted.append(deepcopy(self.state.settings));return True
        self.state._save_state=save
        outer=self
        class Store:
            def apply(self, request):
                if request.operation_id in outer.receipts:return outer.receipts[request.operation_id]
                assert outer.persisted[-1][KEY][0]['request']['operation_id']==request.operation_id
                outer.calls.append(request)
                for target in request.tracks:
                    track=outer.source.tracks_by_id[target.track_id]
                    assigned=set(track.source_tag_ids)
                    if request.present:assigned.add(request.tag_id)
                    else:assigned.discard(request.tag_id)
                    outer.source.tracks_by_id[target.track_id]=replace(track,source_tag_ids=sorted(assigned))
                outer.receipts[request.operation_id]=1
                return 1
        self.store=Store()
        self.controller=TagEditController(self.root,store_factory=lambda _:self.store,
            loader=lambda _:deepcopy(self.source),intent_owner=self.state)
        self.controller.configure(deepcopy(self.source),True)
        self.saved=[];self.errors=[]
        self.controller.saved.connect(self.saved.append);self.controller.failed.connect(self.errors.append)
        self.addCleanup(lambda:self.controller.shutdown())

    def wait(self, predicate):
        for _ in range(1000):
            if predicate():return
            QTest.qWait(5)
        self.fail('Assignment did not settle')

    def test_persist_before_preview_and_ack_before_publishing_full_fresh_library(self):
        self.assertTrue(self.controller.request(['2'],'tag',True))
        self.assertEqual(self.persisted[0][KEY][0]['request']['tracks'][0]['source_uuid'],'u2')
        self.wait(lambda:not self.controller.busy)
        self.assertEqual(self.state.settings[KEY],[])
        self.assertEqual(self.saved[0].tracks_by_id['2'].source_tag_ids,['tag'])
        self.assertEqual(self.persisted[-1][KEY],[])

    def test_failed_initial_state_save_admits_no_source_and_no_preview(self):
        self.save_ok=False
        self.assertFalse(self.controller.request(['2'],'tag',True))
        self.assertFalse(self.calls)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.selected_tags(['2']),[set()])
        self.assertNotIn(KEY,self.state.settings)

    def test_failed_ack_restart_replays_same_operation_without_second_mutation(self):
        self.fail_ack=True
        self.assertTrue(self.controller.request(['2'],'tag',True))
        self.wait(lambda:bool(self.errors))
        original=deepcopy(self.controller.pending[0]['request'])
        self.assertFalse(self.saved);self.assertEqual(len(self.calls),1)
        self.controller.shutdown();self.fail_ack=False
        self.controller=TagEditController(self.root,store_factory=lambda _:self.store,
            loader=lambda _:deepcopy(self.source),intent_owner=self.state)
        self.controller.configure(deepcopy(self.source),True)
        self.assertTrue(self.controller.retry())
        self.wait(lambda:not self.controller.pending and not self.controller.busy)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0].operation_id,original['operation_id'])

    def test_successful_same_session_retry_restores_editing_readiness(self):
        self.fail_ack=True
        self.assertTrue(self.controller.request(['2'],'tag',True))
        self.wait(lambda:bool(self.errors))
        self.assertFalse(self.controller._ready)
        self.fail_ack=False
        self.assertTrue(self.controller.retry())
        self.wait(lambda:bool(self.saved))
        self.controller.configure(self.saved[-1],True)
        self.assertEqual(self.controller.reason(['2']),'')
        self.assertTrue(self.controller.request(['2'],'tag',False))
        self.wait(lambda:len(self.saved)==2)
        self.assertEqual(self.saved[-1].tracks_by_id['2'].source_tag_ids,[])

    def test_unstarted_intent_survives_teardown_and_does_not_block_loading_original_source(self):
        with mock.patch.object(self.controller,'_pump_tag_intent'):
            self.assertTrue(self.controller.request(['2'],'tag',True))
        self.controller.shutdown()
        self.assertEqual(len(self.state.settings[KEY]),1)
        self.assertFalse(self.calls)
        self.controller=TagEditController(self.root,store_factory=lambda _:self.store,
            loader=lambda _:deepcopy(self.source),intent_owner=self.state)
        self.assertFalse(self.controller.owned)
        self.controller.configure(deepcopy(self.source),False)
        self.assertFalse(self.controller.owned)
        self.controller.configure(deepcopy(self.source),True)
        self.wait(lambda:not self.controller.pending and not self.controller.busy)
        self.assertEqual(len(self.calls),1)

    def test_loader_wrong_library_or_stale_membership_cannot_acknowledge(self):
        for fault in ('library','membership','uuid'):
            with self.subTest(fault=fault):
                fresh=deepcopy(self.source)
                fresh.tracks_by_id['2']=replace(fresh.tracks_by_id['2'],source_tag_ids=['tag'])
                request=tags.TagWriteRequest('library',(tags.SelectedTagTrack('2','u2',False),),
                                             'tag','tag-uuid',True,'Dubstep')
                if fault=='library':fresh.source_library_id='replacement'
                elif fault=='membership':fresh.tracks_by_id['2'].source_tag_ids=[]
                else:fresh.tracks_by_id['2'].source_uuid='replacement'
                self.assertFalse(verified_readback(fresh,str(self.source.source),request))

    def test_logical_native_alias_selection_is_one_atomic_native_target(self):
        lib=deepcopy(self.source)
        lib.tracks_by_id['alias']=replace(lib.tracks_by_id['2'],track_id='alias',source_native_id='2')
        self.controller.configure(lib,True)
        self.assertTrue(self.controller.request(['2','alias'],'tag',True))
        self.wait(lambda:not self.controller.busy)
        self.assertEqual(len(self.calls[0].tracks),1)

    def test_restore_closes_admission_without_waiting_forever_on_an_unstarted_intent(self):
        with mock.patch.object(self.controller,'_pump_tag_intent'):
            self.assertTrue(self.controller.request(['2'],'tag',True))
        self.assertTrue(self.controller.owned)
        self.state._state_maintenance=True
        self.assertFalse(self.controller.owned)
        self.controller._pump_tag_intent()
        self.assertFalse(self.calls)
        # A restore publishes a different state namespace only after draining.
        self.state.settings={KEY:[]}
        self.controller.configure(deepcopy(self.source),True)
        self.assertEqual(self.controller.selected_tags(['2']),[set()])
        self.state._state_maintenance=False

    def test_both_operation_scope_and_conflicting_native_aliases_are_checked(self):
        lib=deepcopy(self.source)
        lib.tracks_by_id['alias']=replace(lib.tracks_by_id['2'],track_id='alias',source_native_id='2',source_uuid='different')
        self.controller.configure(lib,True)
        self.assertFalse(self.controller.request(['2','alias'],'tag',True))
        self.assertFalse(self.calls);self.assertNotIn(KEY,self.state.settings)

    def fair_host(self):
        from desktop.analysis_priority import AnalysisPriorityMixin
        host=SimpleNamespace(lib=self.controller.lib,tag_controller=self.controller,
            _shutting_down=False,_state_maintenance=False,_analysis_worker=None,
            _analysis_priority_pending=lambda:True)
        self.controller.analysis_priority=host._analysis_priority_pending
        self.controller.analysis_admission=lambda:AnalysisPriorityMixin._analysis_source_priority(host)
        self.controller.source_admitted=mock.Mock(side_effect=lambda:AnalysisPriorityMixin._analysis_source_admitted(host))
        return host,AnalysisPriorityMixin

    def test_durable_assignment_gets_one_real_source_admission_at_the_next_dsp_phase_boundary(self):
        with mock.patch.object(self.controller,'_pump_tag_intent'):
            self.assertTrue(self.controller.request(['2'],'tag',True))
        host,priority=self.fair_host()
        self.controller._pump_tag_intent()
        self.assertFalse(self.controller.busy);self.assertFalse(self.calls)
        self.assertTrue(self.controller.assignment_reserved)
        priority._analysis_offer_source_turn(host,host.lib)
        self.assertTrue(self.controller.busy)
        self.assertIsNone(host._analysis_source_turn)
        self.controller.source_admitted.assert_called_once()
        self.wait(lambda:not self.controller.busy)
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.controller.pending)

    def test_errored_or_other_source_head_does_not_take_or_block_an_unrelated_fair_turn(self):
        with mock.patch.object(self.controller,'_pump_tag_intent'):
            self.assertTrue(self.controller.request(['2'],'tag',True))
        original=deepcopy(self.controller.pending[0])
        for condition in ('error','other-source'):
            with self.subTest(condition=condition):
                job=deepcopy(original)
                if condition=='error':job['error']='attention required'
                else:job['source']='/other/master.db'
                self.state.settings[KEY]=[job]
                host,priority=self.fair_host();events=[]
                host.playlist_controller=SimpleNamespace(pump=lambda:self.controller.run_playlist_job(
                    lambda:events.append('unrelated'),lambda _:None))
                self.assertFalse(self.controller.assignment_reserved)
                priority._analysis_offer_source_turn(host,host.lib)
                self.wait(lambda:not self.controller.busy)
                self.assertEqual(events,['unrelated'])
                self.assertFalse(self.calls)
                self.assertEqual(self.controller.pending,[job])

    def test_explicit_retry_retains_operation_identity_until_its_fair_opportunity(self):
        with mock.patch.object(self.controller,'_pump_tag_intent'):
            self.assertTrue(self.controller.request(['2'],'tag',True))
        original=deepcopy(self.controller.pending[0]['request'])
        self.state.settings[KEY][0]['error']='retry me'
        host,priority=self.fair_host()
        self.assertTrue(self.controller.retry());self.controller._tag_timer.stop()
        self.assertEqual(self.controller.pending[0]['request'],original)
        self.assertTrue(self.controller.assignment_reserved)
        priority._analysis_offer_source_turn(host,host.lib)
        self.wait(lambda:not self.controller.busy)
        self.assertEqual(self.calls[0].operation_id,original['operation_id'])


class AssignmentReceipt(unittest.TestCase):
    def setUp(self):
        self.fixture=tag_fixture.NativeTagTransactions();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        self.request=replace(self.fixture.request,operation_id='a'*32)

    def test_domain_receipt_survives_shared_receipt_rotation_and_other_counter_progress(self):
        self.store.apply(self.request)
        self.fixture.mutate("UPDATE agentRegistry SET int_1=55 WHERE registry_id='localUpdateCount'")
        (self.store.root/'last-result.json').write_text('{"format":"other-domain"}')
        before=self.fixture.state()
        self.assertEqual(self.store.apply(self.request),1)
        self.assertEqual(self.fixture.state(),before)

    def test_source_commit_before_controller_ack_replays_after_recovery(self):
        def fault(phase):
            if phase=='after-commit':raise OSError('lost delivery')
        with mock.patch.object(tags,'_fault',side_effect=fault):
            with self.assertRaises(OSError):self.store.apply(self.request)
        fresh=tags.LiveTagStore(self.fixture.authority,self.store.root)
        self.assertEqual(fresh.apply(self.request),1)
        self.assertEqual(self.fixture.query("SELECT count(*) FROM djmdSongMyTag WHERE ContentID='2' AND MyTagID='11'"),[(1,)])

    def test_changed_membership_or_stable_operation_collision_never_blindly_replays(self):
        self.store.apply(self.request)
        with self.assertRaises(writer.RecoveryRequired):
            self.store.apply(replace(self.request,present=False))
        self.fixture.mutate("DELETE FROM djmdSongMyTag WHERE ContentID='2' AND MyTagID='11'")
        before=self.fixture.state()
        with self.assertRaises(writer.RecoveryRequired):self.store.apply(self.request)
        self.assertEqual(self.fixture.state(),before)

    def test_current_authority_pilot_scope_is_checked_during_completed_receipt_replay(self):
        self.store.apply(self.request)
        permission=replace(self.fixture.authority,pilot_track_id='1',pilot_tag_id='11')
        narrowed=tags.LiveTagStore(permission,self.store.root)
        with self.assertRaises(writer.WriteProofError):narrowed.apply(self.request)

    def test_replaced_source_refuses_old_receipt_but_not_a_new_explicit_intent(self):
        self.store.apply(self.request)
        replacement=self.fixture.source.with_name('replacement.db')
        shutil.copyfile(self.fixture.source,replacement);replacement.replace(self.fixture.source)
        fresh=tags.LiveTagStore(self.fixture.authority,self.store.root)
        with self.assertRaises(writer.RecoveryRequired):fresh.apply(self.request)
        request=replace(self.request,operation_id='b'*32,present=False,
                        tracks=(tags.SelectedTagTrack('2','track-two',True),))
        self.assertEqual(fresh.apply(request),1)

    def test_synced_removal_receipt_retains_exact_native_tombstone_through_retry(self):
        self.fixture.mutate("UPDATE djmdSongMyTag SET usn=NULL WHERE ID='existing'")
        permission=replace(self.fixture.authority,synced_removal=True)
        store=tags.LiveTagStore(permission,self.store.root)
        request=replace(self.request,present=False,tracks=(tags.SelectedTagTrack('1','track-one',True),))
        self.assertEqual(store.apply(request),1)
        before=self.fixture.state()
        self.assertEqual(store.apply(request),1)
        self.assertEqual(self.fixture.state(),before)
        self.assertEqual(self.fixture.query("SELECT rb_local_deleted,rb_local_synced FROM djmdSongMyTag WHERE ID='existing'"),[(1,1)])

    def test_exact_inactive_sqlite_journal_proof_allows_the_next_shared_domain(self):
        original=self.store._connect
        def persistent():
            conn=original();conn.execute('PRAGMA journal_mode=PERSIST');return conn
        with mock.patch.object(self.store,'_connect',side_effect=persistent):
            self.store.apply(self.request)
        journal=self.fixture.source.with_name('master.db-journal')
        self.assertTrue(journal.exists())
        self.assertTrue(writer._owned_inactive_journal(self.fixture.source,self.store.root,'synthetic-library'))
        self.store.check_ready()


if __name__=='__main__':unittest.main()
