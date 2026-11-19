"""Accepted source permission and paused backlog scheduling are independent."""
from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import time
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.metadata_controller import (ACKS,BACKGROUND_PAUSED_STATUS,EDITING_DISABLED_REASON,
    KEY,PAUSE_KEY,MetadataController)
from soundbox import rekordbox_metadata_store as native,source_truth
from tests import test_metadata_controller as fixtures


class MetadataBackgroundPause(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.phases=fixtures.MetadataPhases();self.phases.setUp()
        self.addCleanup(self.phases.doCleanups)
        self.w=self.phases.w;self.controller=self.phases.controller

    def accepted_default(self):
        self.controller.stop()
        controller=MetadataController(self.w)
        self.addCleanup(controller.stop)
        authority=native.MetadataAuthority(str(self.w.lib.source),self.w.lib.source_library_id,
            '0'*64,True,native.ALBUM_PROFILE)
        path=Path(self.phases.area)/'rekordbox-metadata-authority.json'
        path.write_text(json.dumps(asdict(authority)))
        return controller,path

    def test_real_default_permission_remains_accepted_while_pause_is_durable_and_scoped(self):
        controller,path=self.accepted_default();permission=path.read_bytes()
        self.assertEqual(controller.reason(),'')
        self.assertTrue(controller.set_background_paused(True))
        paused=deepcopy(self.w.settings);self.assertTrue(controller.background_paused)
        self.assertEqual(controller.reason(),'')
        self.assertEqual(path.read_bytes(),permission)
        self.assertEqual(self.w.tag_controller.metadata_update_status,BACKGROUND_PAUSED_STATUS)
        fresh=MetadataController(self.w);self.addCleanup(fresh.stop)
        self.assertTrue(fresh.background_paused);self.assertTrue(fresh.can_quit_with_unadmitted_metadata())
        original=self.w.lib
        self.w.lib=replace(original,source='/synthetic/other/master.db',source_library_id='456')
        self.assertFalse(fresh.background_paused)
        self.assertEqual(fresh.reason(),EDITING_DISABLED_REASON)
        self.assertTrue(fresh.set_background_paused(True))
        self.assertEqual(len(self.w.settings[PAUSE_KEY]),2)
        self.assertTrue(fresh.set_background_paused(False))
        self.assertEqual(self.w.settings,paused)
        self.w.lib=original;self.assertTrue(fresh.background_paused)
        self.assertEqual(path.read_bytes(),permission)

    def test_pause_and_resume_failed_persistence_restore_the_exact_prior_owner(self):
        controller=self.controller
        self.w.save_ok=False
        self.assertFalse(controller.set_background_paused(True))
        self.assertNotIn(PAUSE_KEY,self.w.settings)
        self.assertFalse(controller.background_paused)
        self.w.save_ok=True;self.assertTrue(controller.set_background_paused(True))
        prior=self.w.settings[PAUSE_KEY]
        self.w.save_ok=False;self.assertFalse(controller.set_background_paused(False))
        self.assertIs(self.w.settings[PAUSE_KEY],prior)
        self.assertTrue(controller.background_paused)
        self.assertFalse(self.w.tag_controller.admitted)

    def test_admitted_pair_drains_then_paused_backlog_never_dispatches_or_saves_on_signal_storm(self):
        p=self.phases;track=self.w.lib.tracks_by_id['local-song']
        self.w.lib.tracks_by_id['other']=replace(track,track_id='other',source_native_id='2',
            source_uuid='00000000-0000-4000-8000-000000000002',name='Other Artist - Other Song',
            artist='',location_uri='file:///synthetic/other.wav')
        descriptor,targets=p.freeze_pair();self.assertEqual(len(descriptor['order']),2)
        p.pump();p.pump();self.assertTrue(self.w.tag_controller.busy)
        self.assertTrue(self.controller.set_background_paused(True))
        p.deliver();p.pump();p.deliver()
        p.pump();p.deliver();p.pump();p.deliver()
        self.assertFalse(self.controller.pending);self.assertEqual(len(p.writes),2)
        p.pump()
        self.assertEqual(self.w.settings[source_truth.MIGRATION]['cursor'],1)
        self.assertIsNone(self.w.settings[source_truth.MIGRATION]['active'])
        self.assertTrue(self.controller.migration_pending)
        self.assertTrue(self.controller.can_quit_with_unadmitted_metadata())
        before=deepcopy(self.w.settings);saves=len(self.w.saved);admissions=len(self.w.tag_controller.admitted)
        for _ in range(100):
            self.w.tag_controller.changed.emit();self.w.tag_controller.idle.emit();p.pump()
        self.assertEqual(self.w.settings,before);self.assertEqual(len(self.w.saved),saves)
        self.assertEqual(len(self.w.tag_controller.admitted),admissions)
        self.assertFalse(self.controller.timer.isActive())
        self.assertEqual(self.w.settings[source_truth.TARGETS],targets)
        self.assertTrue(self.controller.set_background_paused(False));p.pump()
        self.assertEqual({job['local_id'] for job in self.controller.pending},{'other'})
        self.assertEqual(self.w.settings[source_truth.MIGRATION]['cursor'],1)

    def test_direct_user_edit_still_captures_saves_and_acknowledges_while_backlog_is_paused(self):
        p=self.phases;self.assertTrue(self.controller.set_background_paused(True))
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
        p.pump();p.deliver();p.pump();p.deliver()
        self.assertEqual(len(p.writes),1);self.assertEqual(p.native['title'],'Desired Title')
        self.assertFalse(self.controller.pending);self.assertTrue(self.controller.background_paused)
        self.assertEqual(self.w.tag_controller.metadata_update_status,BACKGROUND_PAUSED_STATUS)
        self.assertFalse(self.controller.timer.isActive())

    def test_paused_pending_request_still_requires_its_own_permission(self):
        p=self.phases;self.assertTrue(self.controller.set_background_paused(True))
        self.assertTrue(self.controller.enqueue('local-song',{'title':'Desired Title'}))
        p.pump();p.deliver();self.assertIsNotNone(self.controller.pending[0]['request'])
        before=deepcopy(self.w.settings);self.controller.factory=None
        p.pump()
        self.assertEqual(self.w.tag_controller.metadata_update_status,EDITING_DISABLED_REASON)
        self.assertEqual(self.w.settings,before);self.assertFalse(p.writes)
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())

    def test_completed_read_only_build_waits_for_resume_without_publishing_or_restarting(self):
        self.assertTrue(self.controller.begin_source_truth())
        worker=next(iter(self.controller.tasks._workers))
        self.assertTrue(worker.wait(5000))
        self.assertTrue(self.controller.set_background_paused(True))
        before=deepcopy(self.w.settings);saves=len(self.w.saved)
        deadline=time.monotonic()+5
        while self.controller.tasks.pending and time.monotonic()<deadline:QTest.qWait(5)
        self.assertFalse(self.controller.tasks.pending)
        self.assertEqual(self.w.settings,before);self.assertEqual(len(self.w.saved),saves)
        self.assertEqual(self.w.settings[source_truth.MIGRATION]['format'],'soundbox-source-truth-building-v1')
        self.assertFalse(self.w.tag_controller.admitted)
        self.assertTrue(self.controller.set_background_paused(False))
        self.controller.timer.stop();self.controller.pump()
        deadline=time.monotonic()+5
        while self.controller.tasks.pending and time.monotonic()<deadline:QTest.qWait(5)
        self.assertFalse(self.controller.tasks.pending)
        self.assertEqual(self.w.settings[source_truth.MIGRATION]['format'],'soundbox-source-truth-v1')

    def test_invalid_saved_pause_fails_closed_without_granting_permission_or_losing_intentions(self):
        self.phases.freeze_pair()
        self.w.settings[PAUSE_KEY]=[{'source':str(self.w.lib.source)}]
        before=deepcopy(self.w.settings)
        self.assertIn('pause needs attention',self.controller.queue_error())
        self.assertFalse(self.controller.set_background_paused(False))
        self.phases.pump()
        self.assertEqual(self.w.settings,before);self.assertFalse(self.w.tag_controller.admitted)
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())

    def test_pause_cannot_hide_a_missing_pair_ack_or_poll_and_rewrite_its_error(self):
        self.phases.freeze_pair();self.phases.pump()
        self.assertTrue(self.controller.set_background_paused(True))
        self.w.settings[KEY]=[]
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
        self.phases.pump()
        error=self.w.settings[source_truth.MIGRATION]['error']
        self.assertIn('acknowledgements are incomplete',error)
        self.assertEqual(self.w.tag_controller.metadata_update_status,error)
        saves=len(self.w.saved)
        for _ in range(20):self.controller.schedule();self.phases.pump()
        self.assertEqual(len(self.w.saved),saves)
        self.assertFalse(self.controller.timer.isActive())
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())

    def test_paused_ack_receipt_still_compacts_before_becoming_idle_without_another_source_job(self):
        p=self.phases;p.retirement_entries()
        self.assertTrue(self.controller.set_background_paused(True))
        p.prepared();p.pump();p.deliver()
        self.assertEqual(len(self.w.settings[ACKS]),1)
        self.assertFalse(self.controller.can_quit_with_unadmitted_metadata())
        admissions=len(self.w.tag_controller.admitted)
        with mock.patch('desktop.metadata_controller.native.retire_acknowledged_receipt') as compact:
            p.pump();self.assertEqual(self.controller.tasks.pending,1)
            self.assertTrue(next(iter(self.controller.tasks._workers)).wait(5000))
            deadline=time.monotonic()+5
            while self.controller.tasks.pending and time.monotonic()<deadline:QTest.qWait(5)
            compact.assert_called_once()
        self.assertEqual(self.w.settings[ACKS],[])
        self.assertEqual(len(self.w.tag_controller.admitted),admissions)
        self.assertFalse(self.controller.timer.isActive())
        self.assertTrue(self.controller.can_quit_with_unadmitted_metadata())
