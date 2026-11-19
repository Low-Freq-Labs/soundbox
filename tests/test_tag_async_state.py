"""My Tag source acknowledgement uses real TEMP canonical deltas and Qt ownership."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame
from PySide6.QtWidgets import QMessageBox

from desktop.app import MainWindow
from desktop.state_commit import StateCommitter
from desktop.tag_assignment_intents import KEY, _VerifiedTagReadback
from desktop.tag_controller import TagEditController
from soundbox import backup, smart_playlist_order as order
from soundbox.state_repository import StateRepository, read_state
from tests.test_smart_playlist_order import library
from tests import test_smart_playlist_order as order_fixtures


class TagAsyncState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.w=QFrame();w=self.w
        w.settings={};w.titles={};w.overrides={};w.aliases={}
        w._state_publication=False;w._state_maintenance=False;w._shutting_down=False
        w._state_repository=StateRepository(self.root/'state/library.json')
        w._state_committer=None;w._refresh_worker=None;w.lib=library()
        self.source=deepcopy(w.lib);self.writes=[];self.receipts={};self.saves=[]
        self.saved=[];self.errors=[];self.main=threading.get_ident()
        outer=self
        class Store:
            def apply(self,request):
                if request.operation_id in outer.receipts:return 0
                outer.assertTrue(read_state(w._state_repository.path)['settings'][KEY])
                outer.writes.append(request)
                for track in outer.source.tracks_by_id.values():
                    if any(t.track_id==track.native_id for t in request.tracks):
                        tags=set(track.source_tag_ids)
                        if request.present:tags.add(request.tag_id)
                        else:tags.discard(request.tag_id)
                        track.source_tag_ids=sorted(tags)
                if request.present:outer.source.playlists[0].keys.extend(t.track_id for t in request.tracks)
                outer.receipts[request.operation_id]=True
        self.store=Store()
        self.loader=lambda _:deepcopy(self.source)
        def save():
            self.saves.append(deepcopy(w.settings))
            w._state_repository.save(dict(settings=w.settings,title_overrides=w.titles,
                artist_overrides=w.overrides,artist_aliases=w.aliases))
            return True
        w._save_state=save;save();self.saves.clear()
        w.tag_controller=TagEditController(self.root/'state',w,store_factory=lambda _:self.store,
            loader=lambda path:self.loader(path),intent_owner=w)
        self.tags=w.tag_controller
        w._state_committer=StateCommitter(w)
        w._commit_tag_state=lambda *args,**kw:MainWindow._commit_tag_state(w,*args,**kw)
        self.tags.configure(w.lib,True)
        self.tags.saved.connect(self.publish);self.tags.failed.connect(self.errors.append)
        self.addCleanup(self.close)

    def publish(self,fresh):
        document=read_state(self.w._state_repository.path)
        self.assertEqual(document['settings'][KEY],[])
        self.assertEqual(document['settings'].get(order.KEY),self.w.settings.get(order.KEY))
        self.saved.append(fresh)

    def close(self):
        self.w._shutting_down=True
        self.tags.shutdown();self.w._state_committer.owner.drain()
        self.w.deleteLater();self.app.processEvents()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:QTest.qWait(2)
        self.assertTrue(predicate(),'Tag/canonical task did not settle')

    def settled(self):self.wait(lambda:not self.tags.busy and not self.tags.tag_ack_pending and not self.w._state_committer.pending)

    @contextmanager
    def held_commit(self):
        entered,release=threading.Event(),threading.Event()
        original=self.w._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise RuntimeError('TEMP commit release missing')
            return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=held):
            try:yield entered,release
            finally:release.set();self.settled()

    def request(self):self.assertTrue(self.tags.request(['one'],'11',True))

    def test_one_worker_ack_clears_queue_and_promotes_order_before_publication_without_gui_scan(self):
        main=self.main
        class WorkerMap(dict):
            def values(self):
                if threading.get_ident()==main:raise AssertionError('GUI performed native inventory scan')
                return super().values()
        def loader(_):
            fresh=deepcopy(self.source);fresh.tracks_by_id=WorkerMap(fresh.tracks_by_id);return fresh
        self.loader=loader
        commits=[];original=self.w._state_repository.commit_patch
        def record(patch):
            self.assertNotEqual(threading.get_ident(),main)
            commits.append(patch.edits());return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=record):
            self.request();self.settled()
        self.assertEqual(len(self.saves),1)  # durable admission only
        self.assertEqual(len(commits),1);self.assertEqual(len(self.saved),1)
        self.assertEqual(self.w.settings[order.KEY]['sequence'],1)
        self.assertTrue(any('uuid-one' in ranks for ranks in self.w.settings[order.KEY]['playlists'].values()))
        self.assertFalse(self.tags.tag_ack_pending);self.assertFalse(self.tags.owned)

    def test_held_ack_keeps_preview_native_lane_and_old_state_then_merges_newer_preferences(self):
        with self.held_commit() as (entered,release):
            self.request();self.wait(entered.is_set)
            self.assertTrue(self.tags.state_commit_owned);self.assertTrue(self.tags.tag_ack_pending)
            self.assertTrue(self.tags.owned);self.assertFalse(self.saved)
            self.assertTrue(self.w.settings[KEY]);self.assertNotIn(order.KEY,self.w.settings)
            self.assertEqual(self.tags.selected_tags(['one']),[{'11'}])
            self.assertFalse(self.tags.retry());self.assertFalse(self.tags.discard())
            self.w.settings={**self.w.settings,'quantize':False}
            self.assertTrue(self.w._state_committer.queue_preferences({'quantize':False}))
            release.set()
        self.assertEqual(self.w.settings['quantize'],False)
        self.assertEqual(read_state(self.w._state_repository.path)['settings']['quantize'],False)
        self.assertEqual(len(self.saved),1);self.assertEqual(len(self.saves),1)

    def test_failed_ack_keeps_durable_request_and_retry_replays_receipt_without_second_native_mutation(self):
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('TEMP disk full')):
            self.request();self.settled()
        document=read_state(self.w._state_repository.path)
        request=deepcopy(self.w.settings[KEY][0]['request'])
        self.assertEqual(document['settings'][KEY][0]['request'],request)
        self.assertFalse(document['settings'][KEY][0]['error'])
        self.assertNotIn(order.KEY,self.w.settings);self.assertFalse(self.saved)
        self.assertTrue(self.w.settings[KEY][0]['error']);self.assertEqual(len(self.saves),1)
        self.assertTrue(self.tags.retry());self.wait(lambda:bool(self.saved));self.settled()
        self.assertEqual(len(self.writes),1);self.assertEqual(self.writes[0].operation_id,request['operation_id'])
        self.assertEqual(len(self.saved),1);self.assertFalse(self.w.settings[KEY])
        self.assertEqual(self.w.settings[order.KEY]['sequence'],1)

    def test_all_agreeing_aliases_are_accepted_but_one_conflicting_alias_retains_intent(self):
        for fault in (False,True):
            with self.subTest(fault=fault):
                self.source=library();self.source.tracks_by_id['alias']=replace(
                    self.source.tracks_by_id['one'],track_id='alias',source_native_id='one',
                    source_uuid='replacement' if fault else 'uuid-one')
                self.w.lib=library();self.tags.configure(self.w.lib,True)
                self.w.settings={};self.w._save_state();self.saves.clear()
                self.receipts={};self.saved.clear();self.errors.clear()
                self.request();self.settled()
                self.assertEqual(bool(self.saved),not fault)
                self.assertEqual(bool(self.w.settings[KEY]),fault)
                if fault:self.assertNotIn(order.KEY,self.w.settings)

    def test_latest_order_state_before_ack_is_promoted_once_without_losing_prior_promotions(self):
        # Hold the source readback, change display recency before the worker ACK.
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        def loader(_):
            entered.set()
            if not release.wait(5):raise RuntimeError('TEMP readback release missing')
            return deepcopy(self.source)
        self.loader=loader;self.request();self.wait(entered.is_set)
        self.w.settings[order.KEY]={'version':1,'sequence':8,'playlists':{'unrelated':{'other-uuid':8}}}
        # This fixture emulates an independently durable prior display history.
        self.w._save_state();release.set();self.settled()
        self.assertEqual(self.w.settings[order.KEY]['sequence'],9)
        self.assertEqual(self.w.settings[order.KEY]['playlists']['unrelated'],{'other-uuid':8})
        self.assertEqual(len(self.saved),1)

    def test_newer_ram_queue_after_commit_is_not_cleared_or_replaced_by_old_error(self):
        with self.held_commit() as (entered,release):
            self.request();self.wait(entered.is_set)
            newer=deepcopy(self.w.settings[KEY]);newer[0]['request']['operation_id']='f'*32
            self.w.settings={**self.w.settings,KEY:newer}
            release.set()
        self.assertEqual(self.w.settings[KEY],newer);self.assertFalse(self.saved)
        self.assertIn('newer library/view state',self.errors[-1])
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][KEY],[])
        self.assertEqual(len(self.saves),1)

    def test_newer_ram_order_after_commit_preserves_it_and_never_reports_uncommitted_failure(self):
        with self.held_commit() as (entered,release):
            self.request();self.wait(entered.is_set)
            newer={'version':1,'sequence':20,'playlists':{'new':{'new-uuid':20}}}
            self.w.settings={**self.w.settings,order.KEY:newer}
            release.set()
        self.assertIs(self.w.settings[order.KEY],newer);self.assertFalse(self.saved)
        self.assertIn('were saved',self.errors[-1]);self.assertNotIn('local acknowledgement failed',self.errors[-1])
        self.assertTrue(self.w.settings[KEY]);self.assertFalse(read_state(self.w._state_repository.path)['settings'][KEY])
        # An explicit retry checkpoints the retained original receipt, then
        # promotes against this latest history rather than restoring old maps.
        self.assertTrue(self.tags.retry());self.wait(lambda:bool(self.saved));self.settled()
        self.assertEqual(self.w.settings[order.KEY]['sequence'],21)
        self.assertEqual(self.w.settings[order.KEY]['playlists']['new'],{'new-uuid':20})
        self.assertEqual(len(self.writes),1)

    def test_source_change_after_durable_commit_retains_current_view_and_original_intent(self):
        with self.held_commit() as (entered,release):
            self.request();self.wait(entered.is_set)
            foreign=replace(self.tags.lib,source=Path('/synthetic/foreign.db'))
            self.tags.lib=foreign;self.w.lib=foreign
            release.set()
        self.assertIs(self.w.lib,foreign);self.assertFalse(self.saved)
        self.assertTrue(self.w.settings[KEY]);self.assertNotIn(order.KEY,self.w.settings)
        self.assertFalse(read_state(self.w._state_repository.path)['settings'][KEY])
        self.assertIn('were saved',self.errors[-1])

    def test_owner_source_switch_without_controller_reconfigure_cannot_publish_original_ack(self):
        with self.held_commit() as (entered,release):
            self.request();self.wait(entered.is_set)
            foreign=replace(self.w.lib,source_library_id='different-library')
            self.w.lib=foreign;release.set()
        self.assertIs(self.w.lib,foreign);self.assertFalse(self.saved)
        self.assertTrue(self.w.settings[KEY]);self.assertNotIn(order.KEY,self.w.settings)

    def test_actual_restore_before_delivery_preserves_even_an_identical_restored_intent(self):
        original=self.w._state_repository.commit_patch;finished=threading.Event()
        def hold_delivery(patch):
            result=original(patch);finished.set();return result
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=hold_delivery):
            self.request()
            # Dispatch the native completion, then stop dispatching Qt until
            # the canonical worker is done. Its queued callback has not run.
            self.wait(lambda:self.tags._tag_ack_inflight)
            self.assertTrue(finished.wait(2))
            retained=deepcopy(self.w.settings[KEY]);selected=self.root/'selected';selected.mkdir()
            (selected/'library.json').write_text(json.dumps({'settings':{KEY:retained,'restored':True}}))
            backup.restore_backup(self.w._state_repository.path.parent,selected)
            loaded=self.w._state_repository.load();self.w.settings=loaded['settings']
            self.settled()
        self.assertEqual(self.w.settings,{KEY:retained,'restored':True})
        self.assertFalse(self.saved);self.assertEqual(len(self.writes),1)
        self.assertEqual(read_state(self.w._state_repository.path)['settings'],self.w.settings)

    def test_invalid_display_history_does_not_block_verified_source_ack_or_erase_old_order(self):
        history={'corrupt':'retained'};self.w.settings[order.KEY]=history;self.w._save_state()
        self.request();self.settled()
        self.assertEqual(len(self.saved),1);self.assertFalse(self.w.settings[KEY])
        self.assertEqual(self.w.settings[order.KEY],history)
        self.assertIn('previous order retained',self.tags.order_error)

    def test_busy_coordinator_retains_private_ack_until_idle_without_repeating_native_readback(self):
        commit=self.w._commit_tag_state;calls=[]
        def delayed(*args,**kw):
            calls.append(1)
            if len(calls)==1:return False
            return commit(*args,**kw)
        self.w._commit_tag_state=delayed
        self.request();self.wait(lambda:bool(calls))
        self.assertTrue(self.tags.tag_ack_pending);self.assertFalse(self.saved)
        self.tags.idle.emit();self.settled()
        self.assertEqual(len(self.saved),1);self.assertEqual(len(self.writes),1)

    def test_worker_proof_for_different_request_cannot_clear_retained_head(self):
        with mock.patch.object(self.tags,'_pump_tag_intent'):self.request()
        job=deepcopy(self.w.settings[KEY][0])
        from desktop.tag_assignment_intents import restore_request
        request=restore_request(job['request'])
        wrong=replace(request,present=False)
        fresh=deepcopy(self.source)
        self.tags._finish_tag_intent(type('Task',(),dict(result=_VerifiedTagReadback(
            fresh,str(fresh.source),wrong,None),error=''))(),job)
        self.settled()
        self.assertTrue(self.w.settings[KEY]);self.assertFalse(self.saved)
        self.assertIn('readback differs',self.errors[-1])

    def test_shutdown_drains_accepted_ack_without_early_completion_or_late_source_admission(self):
        self.request();self.tags.shutdown();self.w._state_committer.owner.drain()
        self.assertFalse(self.tags.tag_ack_pending);self.assertFalse(self.w.settings[KEY])
        self.assertFalse(self.saved)  # Closed UI never receives publication.
        self.assertEqual(len(self.writes),1)
        self.assertFalse(self.tags.request(['one'],'11',False))
        self.assertFalse(read_state(self.w._state_repository.path)['settings'][KEY])

    def test_discard_yes_cannot_clear_intent_admitted_by_nested_dialog_events(self):
        with mock.patch.object(self.tags,'_pump_tag_intent'):self.request()
        reviewed=deepcopy(self.w.settings[KEY]);entered,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def loader(_):
            entered.set()
            if not release.wait(5):raise RuntimeError('TEMP dialog source release missing')
            return deepcopy(self.source)
        self.loader=loader
        def answer(*_args):
            self.tags._pump_tag_intent();self.wait(entered.is_set)
            return QMessageBox.Yes
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question',side_effect=answer):
            self.assertFalse(self.tags.discard())
        self.assertEqual(self.w.settings[KEY],reviewed);self.assertEqual(len(self.saves),1)
        release.set();self.wait(lambda:bool(self.saved));self.settled()
        self.assertEqual(len(self.writes),1);self.assertFalse(self.w.settings[KEY])

    def test_discard_yes_does_not_replace_a_different_reviewed_queue_or_scope(self):
        with mock.patch.object(self.tags,'_pump_tag_intent'):self.request()
        reviewed=deepcopy(self.w.settings[KEY]);newer=deepcopy(reviewed)
        newer[0]['error']='newer attention'
        def answer(*_args):self.w.settings[KEY]=newer;return QMessageBox.Yes
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question',side_effect=answer):
            self.assertFalse(self.tags.discard())
        self.assertIs(self.w.settings[KEY],newer);self.assertEqual(len(self.saves),1)
        self.w.settings[KEY]=reviewed
        def source_changed(*_args):
            self.w.lib=replace(self.w.lib,source_library_id='replacement');return QMessageBox.Yes
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question',side_effect=source_changed):
            self.assertFalse(self.tags.discard())
        self.assertEqual(self.w.settings[KEY],reviewed);self.assertFalse(self.writes)

    def test_source_error_plus_failed_local_error_note_never_claims_native_save(self):
        with mock.patch.object(self.store,'apply',side_effect=OSError('TEMP source refused before write')):
            with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('TEMP local storage failed')):
                self.request();self.settled()
        self.assertFalse(self.writes);self.assertFalse(self.saved)
        self.assertIn('source refused before write',self.errors[-1])
        self.assertNotIn('saved in rekordbox',self.errors[-1])
        self.assertEqual(len(self.saves),1);self.assertTrue(self.w.settings[KEY])

    def test_actual_app_publication_uses_committed_order_without_second_save_or_inventory_walk(self):
        fixture=order_fixtures.SmartPlaylistTagHandoff();self.addCleanup(fixture.doCleanups)
        host=fixture.host();host.tag_controller.durable_assignment_ack=True
        host.tag_controller.order_error=''
        host.model.set_rows([dict(id='one',title='Zebra')]);fixture.select(host,'one')
        MainWindow._assign_tag(host,'11',True)
        fresh=order_fixtures.verified(host.lib)
        host.settings[order.KEY]=order.record(None,host._tag_order_context[2],fresh)
        with mock.patch.object(order,'record',side_effect=AssertionError('GUI derived native order twice')):
            with mock.patch.object(host,'_save_state',side_effect=AssertionError('GUI saved full state after ACK')):
                MainWindow._tag_saved(host,fresh)
        self.assertIs(host.lib,fresh);self.assertEqual(host.settings[order.KEY]['sequence'],1)
        self.assertEqual(host._selected_ids(),['one'])
