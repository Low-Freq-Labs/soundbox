"""Exact sparse creation ACK/discard survives held storage and lost publication."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidget,QMessageBox

from desktop.tag_controller import TagEditController
from desktop.tag_creation_controller import QUEUE
from soundbox.library import LibraryTag
from soundbox.state_repository import StateRepository,read_state
from tests import test_source_intent_admission as fixtures


class SourceCreationAck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.SourceIntentAdmission.setUpClass()

    def setUp(self):
        self.case=c=fixtures.SourceIntentAdmission();c.setUp();self.addCleanup(c.doCleanups)
        c.tag.schedule=mock.Mock()

    def source_ready(self):
        c=self.case;request=c.request()
        self.assertTrue(c.tag.enqueue(request));c.wait(lambda:not c.tag.admission.pending)
        c.tag.timer.stop();c.tag.pump();self.assertEqual(len(c.native),1)
        c.tag.loader=lambda _:replace(c.window.lib,source_tags=[*c.window.lib.source_tags,
            LibraryTag(request['tag_id'],request['name'],'Genre',request['tag_uuid'],'1','1')])
        result=c.native[0][1]()
        self.assertEqual(c.written(),[(request['operation_id'],)])
        return request,result

    def complete(self,result):
        self.case.tags.busy=False
        self.case.native[0][2](SimpleNamespace(error='',result=result))

    def canonical(self):return read_state(self.case.window._state_repository.path)['settings'].get(QUEUE,[])

    def test_native_creation_ack_keeps_gui_responsive_and_source_reserved_until_durable(self):
        c=self.case;request,result=self.source_ready()
        navigation=QListWidget(c.window);navigation.addItems(['Collection','Selected playlist']);navigation.setCurrentRow(0)
        with c.held() as (entered,release):
            self.complete(result);self.assertTrue(entered.wait(2))
            self.assertTrue(c.tag.ack.pending);self.assertTrue(c.tags.source_ack_pending)
            self.assertFalse(c.window.published);self.assertEqual(self.canonical()[0]['request'],request)
            QTest.keyClick(navigation,Qt.Key_Down);QTest.qWait(15)
            self.assertEqual(navigation.currentRow(),1)
            self.assertFalse(TagEditController._run_source_job(c.tags,'other',lambda:None,lambda _:None))
            c.window._save_state.assert_not_called();release.set()
        self.assertEqual(self.canonical(),[]);self.assertEqual(len(c.window.published),1)
        self.assertFalse(c.tags.source_ack_pending);self.assertFalse(c.tag.ack.pending)

    def test_ack_storage_retry_does_not_repeat_source_sql_and_discard_is_available_for_own_hold(self):
        c=self.case;request,result=self.source_ready()
        with mock.patch.object(c.window._state_repository,'commit_patch',side_effect=OSError('ACK disk full')):
            self.complete(result);c.wait(lambda:bool(c.tag.ack.error))
        from desktop.intent_review import capture_discard
        self.assertIsNotNone(capture_discard(c.tag));self.assertTrue(c.tags.source_ack_pending)
        self.assertEqual(self.canonical()[0]['request'],request);self.assertFalse(c.window.published)
        self.assertTrue(c.tag.retry());c.wait(lambda:not c.tag.ack.pending)
        self.assertEqual(c.written(),[(request['operation_id'],)]);self.assertEqual(self.canonical(),[])
        self.assertFalse(c.tags.source_ack_pending);c.window._save_state.assert_not_called()

    def test_throwing_publication_continuation_cannot_strand_the_logical_source_ack_token(self):
        c=self.case;_request,result=self.source_ready()
        c.window._publish_created_library=mock.Mock(side_effect=RuntimeError('Generated view publication failure'))
        self.complete(result);c.wait(lambda:not c.committer.pending)
        self.assertFalse(c.tag.ack.pending);self.assertFalse(c.tags.source_ack_pending)
        self.assertEqual(self.canonical(),[]);self.assertEqual(len(c.written()),1)
        c.window._publish_created_library.assert_called_once()

    def test_verified_retention_drops_only_readback_and_preserves_original_durable_request(self):
        c=self.case;request,result=self.source_ready()
        with mock.patch.object(c.window._state_repository,'commit_patch',side_effect=OSError('ACK disk full')):
            self.complete(result);c.wait(lambda:bool(c.tag.ack.error))
        captured=c.tag.ack.retention_capture();self.assertIsNotNone(captured)
        self.assertFalse(c.tag.ack.release_retained(captured,[{'unrelated':'unknown'}]))
        with c.window._state_repository.area.lock:
            canonical=self.canonical()
            c.window._state_repository.verify_selected_values({('settings',QUEUE):canonical})
        self.assertTrue(c.tag.ack.release_retained(captured,canonical))
        self.assertEqual(self.canonical()[0]['request'],request)
        self.assertFalse(c.tag.ack.pending);self.assertFalse(c.tags.source_ack_pending)
        self.assertFalse(c.window.published);self.assertEqual(len(c.written()),1)

    def base_and_failed_stage(self):
        c=self.case;base_request=c.request('Already durable')
        base=dict(source=str(c.window.lib.source),library_id='library',request=base_request,error='')
        repository=c.window._state_repository
        repository.commit_patch(repository.prepare_patch({('settings',QUEUE):[base]}));c.window.settings[QUEUE]=[deepcopy(base)]
        with mock.patch.object(repository,'commit_patch',side_effect=OSError('Stage disk full')):
            self.assertTrue(c.tag.enqueue(c.request('Unsaved new intention')));c.wait(lambda:bool(c.tag.admission.error))
        return base

    def test_discard_reviews_both_nonempty_durable_base_and_failed_stage_before_clearing(self):
        c=self.case;base=self.base_and_failed_stage()
        self.assertEqual(self.canonical(),[base]);self.assertEqual(len(c.tag.pending),2)
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.assertFalse(c.tag.discard())
        self.assertEqual(self.canonical(),[base])
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes) as question:
            self.assertTrue(c.tag.discard());self.assertIn('Discard 2 pending tag change(s)?',question.call_args.args[2])
        c.wait(lambda:not c.tag.admission.pending)
        self.assertEqual(self.canonical(),[]);self.assertFalse(c.native)

    def test_discard_refuses_hidden_canonical_jobs_outside_exact_reviewed_base_and_stage(self):
        c=self.case;base=self.base_and_failed_stage();hidden=dict(base,request=c.request('Hidden durable owner'))
        repository=c.window._state_repository
        repository.commit_patch(repository.prepare_patch({('settings',QUEUE):[base,hidden]}))
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):self.assertTrue(c.tag.discard())
        c.wait(lambda:bool(c.tag.admission.error) and not c.tag.admission.active)
        self.assertEqual(self.canonical(),[base,hidden]);self.assertTrue(c.tag.admission.pending)
        self.assertFalse(c.native)

    def test_staged_second_creation_rebases_only_across_its_own_successful_sparse_ack(self):
        c=self.case;first,result=self.source_ready();second=c.request('Second native tag')
        self.assertTrue(c.tag.enqueue(second));self.assertEqual(len(c.tag.admission.staged),1)
        self.complete(result);c.wait(lambda:not c.tag.ack.pending)
        c.wait(lambda:not c.tag.admission.pending)
        self.assertEqual([row['request'] for row in self.canonical()],[second])
        self.assertEqual(c.written(),[(first['operation_id'],)])
        self.assertFalse(c.tag.admission.error);c.window._save_state.assert_not_called()

    def test_restore_takeover_retires_only_old_ack_token_without_mutating_new_queue_or_publishing(self):
        c=self.case;_request,result=self.source_ready()
        with mock.patch.object(c.window._state_repository,'commit_patch',side_effect=OSError('ACK disk full')):
            self.complete(result);c.wait(lambda:bool(c.tag.ack.error))
        replacement=dict(source=str(c.window.lib.source),library_id='library',request=c.request('Restored owner'),error='')
        restored=StateRepository(c.root/'restored/library.json')
        restored.save(dict(settings={QUEUE:[replacement]},title_overrides={},artist_overrides={},artist_aliases={}))
        c.window._state_repository=restored;c.window.settings[QUEUE]=[deepcopy(replacement)]
        c.tags.changed.emit()
        self.assertFalse(c.tag.ack.pending);self.assertFalse(c.tags.source_ack_pending)
        self.assertEqual(self.canonical(),[replacement]);self.assertEqual(c.window.settings[QUEUE],[replacement])
        self.assertFalse(c.window.published);self.assertEqual(len(c.written()),1)

    def test_source_switch_keeps_ack_hold_but_controller_takeover_releases_only_its_old_token(self):
        c=self.case;_request,result=self.source_ready()
        with mock.patch.object(c.window._state_repository,'commit_patch',side_effect=OSError('ACK disk full')):
            self.complete(result);c.wait(lambda:bool(c.tag.ack.error))
        before=deepcopy(c.window.settings[QUEUE]);library=c.window.lib
        c.window.lib=replace(library,source='/synthetic/other.db');c.tags.changed.emit()
        self.assertTrue(c.tag.ack.pending);self.assertTrue(c.tags.source_ack_pending)
        c.window.tag_definition_controller=object();c.tags.changed.emit()
        self.assertFalse(c.tag.ack.pending);self.assertFalse(c.tags.source_ack_pending)
        self.assertEqual(c.window.settings[QUEUE],before);self.assertFalse(c.window.published)
