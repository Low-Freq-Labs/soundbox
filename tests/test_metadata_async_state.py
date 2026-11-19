"""Real Qt/coordinator/repository durability between native metadata phases."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.metadata_controller import ACKS,KEY
from desktop.rekordbox_playlists import NativePlaylistsMixin
from desktop.state_commit import StateCommitter
from desktop.tag_controller import TagEditController
from soundbox import alias_intents,shared_imports,source_truth
from soundbox.native_identity import Binding
from soundbox.state_repository import StateRepository,read_state
from tests import test_metadata_controller as fixtures


class SharedTags(fixtures.Tags):
    state_commit_owned=TagEditController.state_commit_owned
    reserve_state_commit=TagEditController.reserve_state_commit
    release_state_commit=TagEditController.release_state_commit

    def __init__(self,root):
        super().__init__(root);self._state_commit_token=None;self._closing=False;self.checking=False
        self.owner=SimpleNamespace(stopping=False,pending=0)

    def _start_next_cue(self):pass

    @property
    def owned(self):return self.busy or self.state_commit_owned

    def _run_source_job(self,*args):
        return False if self.state_commit_owned else super()._run_source_job(*args)


class AsyncMetadataState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.phases=fixtures.MetadataPhases();self.phases.setUp();self.addCleanup(self.phases.doCleanups)
        self.w=self.phases.w;self.controller=self.phases.controller
        self.w.tag_controller=SharedTags(Path(self.phases.area));self.controller.tags=self.w.tag_controller
        self.w.tag_controller.idle.connect(self.controller.schedule)
        self.w.tag_controller.changed.connect(self.controller.schedule)
        self.w._state_repository=StateRepository(Path(self.phases.area)/'state/library.json')
        self.w._state_publication=False;self.coordinator=StateCommitter(self.w)
        self.addCleanup(self.cleanup)
        self.recipes=[]

    def cleanup(self):
        self.w._shutting_down=True;self.controller.timer.stop();self.coordinator.owner.drain()

    def install(self):
        self.w._state_repository.save(dict(settings=self.w.settings,title_overrides=self.w.titles,
            artist_overrides=self.w.overrides,artist_aliases=self.w.aliases))
        def commit(replacements,removals,committed,failed):
            self.recipes.append((deepcopy(replacements),tuple(removals)))
            return self.coordinator.submit(replacements,removals,committed,failed)
        self.w._commit_metadata_state=commit
        def publish(library,changed_ids,**_proof):
            document=read_state(self.w._state_repository.path)
            self.assertEqual(document['settings'].get(KEY),self.w.settings.get(KEY))
            self.assertEqual(document['settings'].get(ACKS),self.w.settings.get(ACKS))
            self.assertEqual(document['title_overrides'],self.w.titles)
            self.assertEqual(changed_ids,('local-song',))
            if getattr(self.w,'metadata_controller',None) is self.controller:
                self.assertEqual(document['settings'][source_truth.CURATION],self.w.settings[source_truth.CURATION])
                self.assertTrue(MainWindow._accept_source_curation(self.w,library,native_changed_ids=changed_ids))
            self.w.published.append(library);self.w.lib=library
        self.w._publish_metadata_library=publish
        if self.w.settings.get(source_truth.MIGRATION):
            # Production first validates the detached frozen domain on its
            # read-only worker before admitting the tested durable phase.
            with mock.patch.object(self.controller,'schedule'):
                self.controller._ensure_truth_validation()
                self.wait(lambda:not self.controller.tasks.pending)
                self.assertTrue(self.controller._ensure_truth_validation())

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(predicate())

    def settle(self):
        self.wait(lambda:not self.coordinator.pending and not self.controller._state_commit_pending)
        self.controller.timer.stop()

    def bind_curation(self):
        self.w.metadata_controller=self.controller
        self.w.custom={};self.w.removals={};self.w.playlist_additions={}
        self.w.hidden=set();self.w.hidden_dates={}
        self.controller.stage_source_scope(self.w.lib,previous=self.w.lib)

    def finish_title(self):
        self.phases.pump();self.phases.deliver();self.settle()
        self.phases.pump();self.phases.deliver();self.settle()

    def test_retired_title_mask_pin_is_committed_with_ack_before_real_publication_guard(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.bind_curation();self.install();saves=len(self.w.saved)
        self.assertIn('local-song',self.w.settings[source_truth.CURATION]['active']['pins'])
        self.finish_title()
        self.assertFalse(self.controller.pending);self.assertEqual(len(self.w.published),1)
        self.assertNotIn('local-song',self.w.titles)
        self.assertNotIn('local-song',self.w.settings[source_truth.CURATION]['active']['pins'])
        self.assertIn(('settings',source_truth.CURATION,'active','pins','local-song'),self.recipes[-1][1])
        self.assertNotIn(('settings',source_truth.CURATION),self.recipes[-1][0])
        self.assertEqual(len(self.w.saved),saves)

    def test_retired_mask_keeps_pin_for_existing_playlist_membership_without_gui_save(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.bind_curation();self.w.custom={'Favorites':['local-song']}
        self.install();saves=len(self.w.saved);pin=deepcopy(self.w.settings[source_truth.CURATION]['active']['pins'])
        self.finish_title()
        self.assertFalse(self.w.titles);self.assertEqual(len(self.w.published),1)
        self.assertEqual(self.w.settings[source_truth.CURATION]['active']['pins'],pin)
        self.assertEqual(len(self.w.saved),saves)

    def test_artist_ack_restores_manual_override_and_its_new_pin_in_same_durable_commit(self):
        self.assertTrue(self.controller.enqueue('local-song',{'artist':'Desired Artist'}))
        # The durable intention retains its manual provenance even when a later
        # presentation reconciliation has removed the physical local mask.
        self.w.overrides={}
        self.w.settings[shared_imports.BINDINGS]=[asdict(Binding('local-song',str(self.w.lib.source),
            self.w.lib.source_library_id,'1',fixtures.UUID,'/synthetic/song.wav'))]
        self.bind_curation();self.install();saves=len(self.w.saved)
        self.assertFalse(self.w.settings[source_truth.CURATION]['active']['pins'])
        self.finish_title()
        self.assertEqual(self.w.overrides,{'local-song':'Desired Artist'})
        self.assertEqual(len(self.w.published),1);self.assertEqual(len(self.w.saved),saves)
        path=('settings',source_truth.CURATION,'active','pins','local-song')
        self.assertIn(path,self.recipes[-1][0])
        self.assertEqual(self.recipes[-1][0][path]['uuid'],fixtures.UUID)
        self.assertIn(('artist_overrides','local-song'),self.recipes[-1][0])

    def test_failed_ack_preserves_old_pin_and_exact_request_until_sparse_retry_commits(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.bind_curation();self.install()
        self.phases.pump();self.phases.deliver();self.settle()
        settings=self.w.settings;header=settings[source_truth.CURATION];titles=self.w.titles
        request=deepcopy(self.controller.pending[0]['request'])
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('TEMP disk failure')):
            self.phases.pump();self.phases.deliver();self.settle()
        self.assertIs(self.w.settings,settings);self.assertIs(self.w.settings[source_truth.CURATION],header)
        self.assertIs(self.w.titles,titles);self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertFalse(self.w.published);self.assertEqual(len(self.phases.writes),1)
        self.assertTrue(self.controller.retry());self.phases.pump();self.phases.deliver();self.settle()
        self.assertEqual(len(self.phases.writes),1);self.assertEqual(len(self.w.published),1)
        self.assertNotIn('local-song',self.w.settings[source_truth.CURATION]['active']['pins'])

    def test_invalid_saved_pin_retains_native_receipt_request_and_refuses_publication(self):
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.bind_curation();self.install()
        self.phases.pump();self.phases.deliver();self.settle()
        header=deepcopy(self.w.settings[source_truth.CURATION])
        header['active']['pins']['local-song']['uuid']='replacement-uuid'
        self.w.settings={**self.w.settings,source_truth.CURATION:header}
        settings=self.w.settings;titles=self.w.titles
        self.phases.pump();self.phases.deliver();self.settle()
        self.assertFalse(self.w.published);self.assertIs(self.w.settings,settings);self.assertIs(self.w.titles,titles)
        self.assertIsNotNone(self.controller.pending[0]['request']);self.assertEqual(len(self.phases.writes),1)
        self.assertIn('saved curation needs reconciliation',self.controller.pending[0]['error'])

    def test_real_worker_capture_commit_holds_exact_old_owners_source_lane_and_preserves_navigation(self):
        p=self.phases
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}));self.install()
        previous=self.w.settings;previous_titles=self.w.titles
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        original=self.w._state_repository.commit_patch;threads=[]
        def held(patch):
            threads.append(threading.get_ident());entered.set()
            if not release.wait(5):raise RuntimeError('TEMP state worker was not released')
            return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=held):
            p.pump();p.deliver();self.wait(entered.is_set)
            self.assertIs(self.w.settings,previous);self.assertIs(self.w.titles,previous_titles)
            self.assertIsNone(self.controller.pending[0]['request'])
            self.assertTrue(self.w.tag_controller.state_commit_owned)
            self.assertTrue(self.w._state_publication)
            self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
            for _ in range(20):p.pump();self.w.tag_controller.idle.emit()
            self.assertEqual(len(self.w.tag_controller.admitted),1);self.assertFalse(p.writes)
            self.w.settings={**self.w.settings,'view_scroll':77}
            release.set();self.settle()
        self.assertTrue(threads);self.assertNotEqual(threads[0],threading.get_ident())
        self.assertEqual(self.w.settings['view_scroll'],77)
        self.assertIsNotNone(self.controller.pending[0]['request'])
        self.assertFalse(self.w.tag_controller.state_commit_owned)
        self.assertFalse(self.w._state_publication)
        self.assertEqual(set(self.recipes[0][0]),{('settings',KEY)})
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][KEY],self.controller.pending)
        self.assertFalse(self.w.published)

    def test_real_failed_ack_keeps_exact_request_then_retry_replays_once_and_commits_sparse_provenance(self):
        p=self.phases
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}));self.install()
        p.pump();p.deliver();self.settle()
        request=deepcopy(self.controller.pending[0]['request']);before=self.w.settings;titles=self.w.titles
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=OSError('TEMP disk failure')):
            p.pump();p.deliver();self.settle()
        self.assertIs(self.w.settings,before);self.assertIs(self.w.titles,titles)
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertEqual(len(p.writes),1);self.assertFalse(self.w.published)
        self.assertIn('storage attention',self.controller.queue_error())
        for _ in range(20):p.pump()
        self.assertEqual(len(p.writes),1)
        self.assertTrue(self.controller.retry());p.pump();p.deliver();self.settle()
        self.assertEqual(len(p.writes),1);self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.w.published),1)
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][KEY],[])
        paths=set(self.recipes[-1][0])|set(self.recipes[-1][1])
        self.assertIn(('settings',KEY),paths)
        self.assertIn(('title_overrides','local-song'),paths)
        self.assertTrue(any(path[:2]==('settings',alias_intents.ORIGINS) and len(path)==3 for path in paths))
        self.assertNotIn(('settings',alias_intents.ORIGINS),paths)
        self.assertNotIn(('settings',),paths)

    def test_pair_queue_and_cursor_have_distinct_real_durable_commits_and_no_gui_save_calls(self):
        p=self.phases;p.freeze_pair();self.install();saves=len(self.w.saved)
        p.pump()
        self.assertFalse(self.controller.pending)
        self.assertIsNone(self.w.settings[source_truth.MIGRATION]['active'])
        self.assertTrue(self.controller._state_commit_pending);self.settle()
        self.assertEqual(len(self.controller.pending),2)
        self.assertIn(('settings',source_truth.MIGRATION,'active'),self.recipes[0][0])
        original=self.w._state_repository.commit_patch;cursor_edges=[]
        def commit(patch):
            edits,_removals=patch.edits();cursor=('settings',source_truth.MIGRATION,'cursor')
            selected=cursor in edits
            if selected:
                before=read_state(self.w._state_repository.path)['settings'][source_truth.MIGRATION]['cursor']
                gui_before=self.w.settings[source_truth.MIGRATION]['cursor']
            result=original(patch)
            if selected:
                after=read_state(self.w._state_repository.path)['settings'][source_truth.MIGRATION]['cursor']
                cursor_edges.append((before,after,gui_before,self.w.settings[source_truth.MIGRATION]['cursor']))
            return result
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=commit):
            for _field in ('title','artist'):
                p.pump();p.deliver();self.settle()
                p.pump();p.deliver();self.settle()
            p.pump();self.settle()
        self.assertEqual(self.w.settings[source_truth.MIGRATION]['cursor'],1)
        self.assertEqual(cursor_edges,[(0,1,0,0)])
        self.assertEqual(len(self.w.saved),saves)
        self.assertEqual(self.recipes[-1][0],{('settings',source_truth.MIGRATION,'cursor'):1,
            ('settings',source_truth.MIGRATION,'active'):None})

    def test_binding_change_during_durable_ack_keeps_receipt_and_reconciles_after_source_token_release(self):
        p=self.phases;p.retirement_entries()
        self.w.settings[shared_imports.BINDINGS]=[asdict(Binding('local-song',str(self.w.lib.source),
            self.w.lib.source_library_id,'1',fixtures.UUID,'/synthetic/song.wav'))]
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}));self.install()
        p.pump();p.deliver();self.settle();request=deepcopy(self.controller.pending[0]['request'])
        self.w._refresh_pending=False;refreshed=[]
        self.w._start_rekordbox_refresh=mock.Mock()
        self.w._refresh_rekordbox=lambda:refreshed.append(self.w.tag_controller.state_commit_owned)
        self.w._native_reconcile_source=lambda:NativePlaylistsMixin._native_reconcile_source(self.w)
        for name in ('_sync_rekordbox_connection','_sync_tags','_sync_cue_editing','_sync_grid'):
            setattr(self.w,name,lambda:None)
        self.w.tag_controller.idle.connect(lambda:MainWindow._finish_tag_refresh(self.w))
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        original=self.w._state_repository.commit_patch
        def held(patch):
            entered.set()
            if not release.wait(5):raise RuntimeError('TEMP acknowledgement was not released')
            return original(patch)
        with mock.patch.object(self.w._state_repository,'commit_patch',side_effect=held):
            p.pump();p.deliver();self.wait(entered.is_set)
            self.w.settings={**self.w.settings,shared_imports.BINDINGS:list(self.w.settings[shared_imports.BINDINGS])}
            release.set();self.settle()
        self.wait(lambda:bool(refreshed))
        self.assertEqual(refreshed,[False]);self.w._start_rekordbox_refresh.assert_not_called()
        self.assertFalse(self.w.published);self.assertFalse(self.controller.pending)
        self.assertIn('fresh song-registration check',self.controller.queue_error())
        document=read_state(self.w._state_repository.path)
        self.assertEqual(document['settings'][KEY],[])
        self.assertEqual(document['settings'][ACKS][0]['entry']['request'],request)
        self.assertEqual(len(p.writes),1)

    def test_retirement_result_waits_for_other_domain_ack_without_repeating_compaction(self):
        p=self.phases;p.retirement_entries()
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}));self.install()
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        def compact(*_args):
            entered.set()
            if not release.wait(5):raise RuntimeError('TEMP compaction was not released')
        with mock.patch('desktop.metadata_controller.native.retire_acknowledged_receipt',side_effect=compact) as retire:
            p.pump();p.deliver();self.settle();p.pump();p.deliver();self.settle()
            self.assertEqual(len(self.w.settings[ACKS]),1)
            p.pump();self.wait(entered.is_set)
            self.assertTrue(self.w.tag_controller._run_source_job('playlists',lambda:None,lambda _result:None))
            count=len(self.recipes);owners=self.w.settings
            release.set();self.wait(lambda:not self.controller.tasks.pending)
            self.assertIsNotNone(self.controller._retirement_ack)
            self.assertIs(self.w.settings,owners);self.assertEqual(len(self.recipes),count)
            self.assertFalse(self.w._state_publication);self.assertFalse(self.w.tag_controller.state_commit_owned)
            self.assertEqual(len(self.w.settings[ACKS]),1)
            p.pump();self.assertEqual(len(self.recipes),count)
            self.w.tag_controller.deliver();self.wait(lambda:not self.w.settings[ACKS]);self.settle()
            retire.assert_called_once()
        self.assertIsNone(self.controller._retirement_ack)
        self.assertEqual(self.recipes[-1][0],{('settings',ACKS):[]})

    def test_frozen_pair_canonical_admission_waits_for_other_domain_source_ownership(self):
        p=self.phases;p.freeze_pair();self.install();before=self.w.settings
        self.assertTrue(self.w.tag_controller._run_source_job('playlists',lambda:None,lambda _result:None))
        p.pump();self.assertIs(self.w.settings,before)
        self.assertFalse(self.controller.pending);self.assertFalse(self.recipes)
        self.assertFalse(self.w._state_publication)
        self.w.tag_controller.deliver();p.pump();self.settle()
        self.assertEqual(len(self.controller.pending),2)
