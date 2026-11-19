"""Full plan proofs leave the GUI while nested mutation still fences writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.state_commit import StateCommitter
from desktop.workers import OneShotTask
from soundbox import backup,mutation_domain,source_truth
from soundbox.state_repository import StateRepository,VerificationChanged,read_state
from soundbox.state_io import StateChangedError
from tests import test_metadata_controller as fixtures
from tests.test_metadata_async_state import SharedTags


class MetadataTruthValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.phases=fixtures.MetadataPhases();self.phases.setUp();self.addCleanup(self.phases.doCleanups)
        self.w=self.phases.w;self.controller=self.phases.controller
        old=self.controller.tags
        old.idle.disconnect(self.controller.schedule);old.changed.disconnect(self.controller.schedule)
        self.w.tag_controller=SharedTags(Path(self.phases.area));self.controller.tags=self.w.tag_controller
        self.w._state_repository=StateRepository(Path(self.phases.area)/'state/library.json')
        self.w._state_publication=False;self.coordinator=StateCommitter(self.w)
        self.addCleanup(self.cleanup)
        self.controller.schedule=mock.Mock()
        self.w._commit_metadata_state=self.coordinator.submit
        self.w._publish_metadata_library=lambda lib,_ids,**_proof:setattr(self.w,'lib',lib)

    def cleanup(self):
        self.w._shutting_down=True;self.controller.timer.stop();self.coordinator.owner.drain()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(predicate())
        self.controller.timer.stop()

    def freeze(self):
        self.phases.freeze_pair()
        self.save_gui()

    def save_gui(self):
        self.w._state_repository.save(dict(settings=self.w.settings,title_overrides=self.w.titles,
            artist_overrides=self.w.overrides,artist_aliases=self.w.aliases))

    def validated(self):
        self.assertFalse(self.controller._ensure_truth_validation())
        self.wait(lambda:not self.controller.tasks.pending)
        self.assertTrue(self.controller._ensure_truth_validation())

    def settle(self):
        self.wait(lambda:not self.coordinator.pending and not self.controller._state_commit_pending)

    def source_result(self):
        """Run the actual admitted source closure on a real QThread."""
        _kind,callback,completed=self.w.tag_controller.admitted[-1]
        worker=OneShotTask(callback,log_label='Isolated metadata source proof')
        worker.start();self.wait(worker.isFinished);worker.wait()
        result=worker.result
        self.w.tag_controller.busy=False
        completed(SimpleNamespace(error=str(result) if isinstance(result,Exception) else '',
                                  result=None if isinstance(result,Exception) else result))
        worker.deleteLater();self.settle()

    def test_real_pair_ack_and_cursor_never_validate_full_targets_on_the_gui(self):
        self.freeze();gui=threading.get_ident();calls=[];snapshots=[];validator=source_truth.validate
        def validate(descriptor,targets):
            calls.append(threading.get_ident())
            self.assertIs(type(descriptor),dict);self.assertIsInstance(targets,mutation_domain.FrozenDict)
            self.assertIsInstance(descriptor['order'],mutation_domain.FrozenList)
            self.assertTrue(all(isinstance(row,mutation_domain.FrozenDict) for row in targets.values()))
            snapshots.append((targets,descriptor['order']))
            return validator(descriptor,targets)
        progress=source_truth.validate_progress;progress_threads=[]
        def checked_progress(descriptor):
            progress_threads.append(threading.get_ident())
            return progress(descriptor)
        with mock.patch('desktop.metadata_controller.source_truth.validate',side_effect=validate), \
                mock.patch('desktop.metadata_controller.source_truth.validate_progress',side_effect=checked_progress), \
                mock.patch.object(self.w._state_repository,'verify_selected_values',
                                  wraps=self.w._state_repository.verify_selected_values) as fresh_canonical:
            self.validated()
            targets=self.w.settings[source_truth.TARGETS]
            order=self.w.settings[source_truth.MIGRATION]['order'];proof=self.controller._truth_observed_proof
            self.phases.pump();self.settle()
            for _field in ('title','artist'):
                self.phases.pump();self.source_result()
                self.phases.pump();self.source_result()
            self.phases.pump();self.settle()
            self.assertEqual(self.w.settings[source_truth.MIGRATION]['cursor'],1)
            self.assertEqual(read_state(self.w._state_repository.path)['settings'][source_truth.MIGRATION]['cursor'],1)
            self.assertIs(self.w.settings[source_truth.TARGETS],targets)
            self.assertIs(self.w.settings[source_truth.MIGRATION]['order'],order)
            self.assertEqual(self.controller._truth_observed_proof,proof)
            self.assertEqual(self.controller._migration_error(force_truth=True),'')
        self.assertEqual(fresh_canonical.call_count,5)
        self.assertEqual(len(progress_threads),5)
        self.assertNotIn(gui,progress_threads)
        self.assertEqual(len(calls),1)
        self.assertTrue(all(targets is snapshots[0][0] and order is snapshots[0][1] for targets,order in snapshots))
        self.assertNotIn(gui,calls);self.assertEqual(len(self.phases.writes),2)

    def test_normal_nested_mutators_cannot_reuse_old_static_owners(self):
        def field(row):return row['fields']
        mutations=(
            lambda row:row.__setitem__('title',row['title']),
            lambda row:row.update(title=row['title']),
            lambda row:row.setdefault('new-nested-value',[]),
            lambda row:row.__delitem__('title'),
            lambda row:row.pop('title'),lambda row:row.popitem(),lambda row:row.clear(),
            lambda row:row.__ior__({'title':row['title']}),
            lambda row:field(row).__setitem__(0,field(row)[0]),
            lambda row:field(row).__setitem__(slice(None),list(field(row))),
            lambda row:field(row).__delitem__(0),lambda row:field(row).append('bad'),
            lambda row:field(row).extend(['bad']),lambda row:field(row).insert(0,'bad'),
            lambda row:field(row).pop(),lambda row:field(row).remove(field(row)[0]),
            lambda row:field(row).clear(),lambda row:field(row).reverse(),
            lambda row:field(row).sort(),lambda row:field(row).__iadd__([]),
            lambda row:field(row).__imul__(1))
        for index,mutate in enumerate(mutations):
            with self.subTest(mutator=index):
                self.w.settings={};self.freeze();self.validated()
                old=self.controller._truth_static_proof
                targets=self.w.settings[source_truth.TARGETS]
                mutate(next(iter(targets.values())))
                with mock.patch('desktop.metadata_controller.source_truth.validate',wraps=source_truth.validate) as complete:
                    self.assertFalse(self.controller._ensure_truth_validation())
                    self.wait(lambda:not self.controller.tasks.pending)
                self.assertEqual(complete.call_count,1)
                if self.controller._truth_static_proof is not None:
                    self.assertIsNot(self.controller._truth_static_proof[2][0],old[2][0])
                self.assertFalse(self.w.tag_controller.admitted)

    def test_static_scalar_types_and_foreign_source_cannot_borrow_certification(self):
        class Text(str):pass
        for name in ('id','inputs_sha256'):
            with self.subTest(name=name):
                self.w.settings={};self.freeze();self.validated()
                descriptor=self.w.settings[source_truth.MIGRATION];descriptor[name]=Text(descriptor[name])
                self.assertFalse(self.controller._ensure_truth_validation())
                self.wait(lambda:not self.controller.tasks.pending)
                self.assertIsNone(self.controller._truth_static_proof)
        self.w.settings={};self.freeze();self.validated()
        old=self.controller._truth_static_proof
        self.w.lib=deepcopy(self.w.lib);self.w.lib.source='/foreign/master.db'
        self.assertFalse(self.controller._ensure_truth_validation())
        self.wait(lambda:not self.controller.tasks.pending)
        self.assertIsNone(self.controller._truth_static_proof)
        self.assertNotEqual(old[0][1],self.controller._truth_source_context())
        # Returning to the original source requires another complete proof.
        self.w.lib.source=old[0][1][0]
        self.w._state_repository.load()
        with mock.patch('desktop.metadata_controller.source_truth.validate',wraps=source_truth.validate) as complete:
            self.assertFalse(self.controller._ensure_truth_validation())
            self.wait(lambda:not self.controller.tasks.pending)
        self.assertEqual(complete.call_count,1)

    def test_no_static_certificate_is_installed_after_canonical_failure(self):
        self.freeze()
        with mock.patch.object(self.w._state_repository,'verify_selected_values',
                               side_effect=StateChangedError('Unsaved canonical target')):
            self.assertFalse(self.controller._ensure_truth_validation())
            self.wait(lambda:not self.controller.tasks.pending)
        self.assertIsNone(self.controller._truth_static_proof)
        self.assertFalse(self.w.tag_controller.admitted)

    def test_actual_restore_generation_forces_full_recertification_of_same_frozen_values(self):
        self.freeze();self.validated();old=self.controller._truth_static_proof
        repository=self.w._state_repository
        with tempfile.TemporaryDirectory() as area:
            selected=Path(area)/'library.json'
            selected.write_text(json.dumps(read_state(repository.path)))
            backup.restore_backup(repository.path.parent,selected)
        repository.load()
        with mock.patch('desktop.metadata_controller.source_truth.validate',wraps=source_truth.validate) as complete:
            self.assertFalse(self.controller._ensure_truth_validation())
            self.wait(lambda:not self.controller.tasks.pending)
        self.assertEqual(complete.call_count,1)
        self.assertIsNotNone(self.controller._truth_static_proof)
        self.assertNotEqual(old[0],self.controller._truth_static_proof[0])
        self.assertFalse(self.w.tag_controller.admitted)

    def test_direct_snapshot_without_proof_keeps_full_validation_and_warm_progress_is_rechecked(self):
        self.freeze();self.validated()
        descriptor,targets=self.controller._observed_truth()
        stamp=self.controller._truth_stamp(descriptor,targets)
        with mock.patch('desktop.metadata_controller.source_truth.validate',wraps=source_truth.validate) as complete:
            self.assertIsNotNone(type(self.controller)._snapshot_truth(descriptor,targets,stamp))
        self.assertEqual(complete.call_count,1)
        descriptor['active']={'target':descriptor['order'][0],'operations':{'title':'bad'}}
        with mock.patch('desktop.metadata_controller.source_truth.validate',side_effect=AssertionError('Static rescan')), \
                self.assertRaisesRegex(ValueError,'operation identity'):
            self.controller._snapshot_truth(descriptor,targets,stamp,proof=self.controller._truth_static_proof,
                context=(self.controller._truth_epoch(),self.controller._truth_source_context()))

    def test_nested_target_mutation_requires_fresh_worker_validation_before_pair_admission(self):
        self.freeze();self.validated();targets=self.w.settings[source_truth.TARGETS]
        row=next(iter(targets.values()));before=targets.revision
        row['uuid']='replaced-native-identity'
        self.assertGreater(targets.revision,before)
        self.phases.pump()
        self.assertFalse(self.controller.pending);self.assertFalse(self.coordinator.pending)
        self.wait(lambda:not self.controller.tasks.pending)
        self.assertIn('needs attention',self.controller._migration_error())
        self.assertFalse(self.controller.pending);self.assertFalse(self.w.tag_controller.admitted)
        self.assertFalse(self.phases.writes)

    def test_valid_nested_edit_is_revalidated_and_latest_value_is_durably_queued(self):
        self.freeze();self.validated()
        targets=self.w.settings[source_truth.TARGETS];old_frozen=targets.frozen_snapshot()
        next(iter(targets.values()))['title']='New preserved title'
        self.save_gui()
        self.phases.pump();self.assertFalse(self.controller.pending)
        self.wait(lambda:not self.controller.tasks.pending)
        self.assertIsNot(targets.frozen_snapshot(),old_frozen)
        self.assertNotEqual(next(iter(old_frozen.values()))['title'],'New preserved title')
        self.phases.pump();self.settle()
        self.assertEqual(self.controller.pending[0]['value'],'New preserved title')
        document=read_state(self.w._state_repository.path)
        self.assertEqual(document['settings']['pending_metadata_edits'][0]['value'],'New preserved title')
        self.assertFalse(self.phases.writes)

    def test_valid_but_unsaved_nested_edit_cannot_escape_through_a_sparse_queue_commit(self):
        self.freeze();self.validated()
        next(iter(self.w.settings[source_truth.TARGETS].values()))['title']='Not durable yet'
        self.phases.pump();self.wait(lambda:not self.controller.tasks.pending)
        self.assertIn('needs attention',self.controller._migration_error())
        self.assertFalse(self.controller.pending);self.assertFalse(self.coordinator.pending)
        saved=read_state(self.w._state_repository.path)['settings'][source_truth.TARGETS]
        self.assertNotEqual(next(iter(saved.values()))['title'],'Not durable yet')
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.phases.writes)
        self.save_gui();self.assertTrue(self.controller.retry())
        self.wait(lambda:not self.controller.tasks.pending)
        self.phases.pump();self.settle()
        self.assertEqual(self.controller.pending[0]['value'],'Not durable yet')

    def test_competing_state_version_is_retried_without_poisoning_intentions_or_native_capture(self):
        self.freeze();repository=self.w._state_repository
        with mock.patch.object(repository,'verify_selected_values',side_effect=VerificationChanged('Transient version')):
            self.assertFalse(self.controller._ensure_truth_validation())
            self.wait(lambda:not self.controller.tasks.pending)
        self.assertIsNone(self.controller._truth_observed_error)
        self.assertIsNone(self.controller._truth_observed_proof)
        self.validated();self.phases.pump();self.settle();self.phases.pump()
        before=deepcopy(self.controller.pending)
        with mock.patch.object(repository,'verify_selected_values',side_effect=VerificationChanged('Transient version')):
            self.source_result()
        self.assertEqual(self.controller.pending,before)
        self.assertFalse(self.phases.captured);self.assertFalse(self.phases.writes)
        self.controller.not_before=0;self.phases.pump();self.source_result()
        self.assertIsNotNone(self.controller.pending[0]['request'])
        self.assertFalse(self.controller.pending[0]['error'])

    def test_inplace_order_and_scalar_identity_mutations_invalidate_the_proof(self):
        for field in ('order','id','scope'):
            with self.subTest(field=field):
                self.w.settings={};self.freeze();self.validated()
                descriptor=self.w.settings[source_truth.MIGRATION]
                if field=='order':descriptor['order'][:]=descriptor['order']*2
                elif field=='scope':descriptor['scope']['library_id']='other-library'
                else:descriptor['id']='0'*32
                self.phases.pump();self.assertFalse(self.controller.pending)
                self.wait(lambda:not self.controller.tasks.pending)
                self.assertIn('needs attention',self.controller._migration_error())
                self.assertFalse(self.w.tag_controller.admitted)

    def test_mutation_while_detached_validation_is_running_cannot_install_stale_proof(self):
        self.freeze();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        validator=source_truth.validate
        def held(descriptor,targets):
            entered.set()
            if not release.wait(5):raise RuntimeError('Isolated validation was not released')
            return validator(descriptor,targets)
        with mock.patch('desktop.metadata_controller.source_truth.validate',side_effect=held):
            self.assertFalse(self.controller._ensure_truth_validation());self.wait(entered.is_set)
            targets=self.w.settings[source_truth.TARGETS]
            next(iter(targets.values()))['fields'].append('unsupported')
            release.set();self.wait(lambda:not self.controller.tasks.pending)
        self.assertIsNone(self.controller._truth_observed_proof)
        self.assertFalse(self.controller.pending);self.assertFalse(self.w.tag_controller.admitted)
        self.phases.pump();self.wait(lambda:not self.controller.tasks.pending)
        self.assertIn('needs attention',self.controller._migration_error())

    def test_actual_source_closure_rechecks_domain_after_admission_before_any_native_capture(self):
        self.freeze();self.validated();self.phases.pump();self.settle()
        self.phases.pump();self.assertTrue(self.w.tag_controller.busy)
        next(iter(self.w.settings[source_truth.TARGETS].values()))['path']='/synthetic/replacement.wav'
        self.source_result()
        self.assertFalse(self.phases.captured);self.assertFalse(self.phases.writes)
        self.assertIsNone(self.controller.pending[0]['request'])
        self.assertTrue(self.controller.pending[0]['error'])

    def test_selected_active_operation_mutation_is_detected_without_a_full_scan(self):
        self.freeze();self.validated();self.phases.pump();self.settle()
        active=self.w.settings[source_truth.MIGRATION]['active']
        active['operations']['title']='wrong'
        with mock.patch('desktop.metadata_controller.source_truth.validate',side_effect=AssertionError('GUI full scan')):
            self.assertIn('needs attention',self.controller._migration_error())
            self.phases.pump()
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.phases.writes)

    def test_valid_inplace_cursor_jump_is_attention_and_cannot_skip_an_unsaved_song(self):
        self.freeze();self.validated()
        self.w.settings[source_truth.MIGRATION]['cursor']=1
        with mock.patch('desktop.metadata_controller.source_truth.validate',side_effect=AssertionError('GUI full scan')):
            self.assertIn('needs attention',self.controller._migration_error())
            self.phases.pump()
        self.assertEqual(read_state(self.w._state_repository.path)['settings'][source_truth.MIGRATION]['cursor'],0)
        self.assertFalse(self.controller.pending);self.assertFalse(self.coordinator.pending)
        self.assertFalse(self.w.tag_controller.admitted);self.assertFalse(self.phases.writes)

    def test_observation_defensively_detaches_external_mutable_aliases(self):
        self.freeze();external=self.w.settings[source_truth.TARGETS]
        self.validated();observed=self.w.settings[source_truth.TARGETS]
        self.assertIsInstance(observed,mutation_domain.ObservedDict)
        self.assertIsNot(observed,external)
        original=deepcopy(observed.snapshot())
        next(iter(external.values()))['title']='Outside stale owner'
        self.assertEqual(observed.snapshot(),original)
        self.assertTrue(self.controller._ensure_truth_validation())

    def test_plain_legacy_host_revalidates_inplace_mutation_without_an_identity_cache(self):
        self.freeze();del self.w._commit_metadata_state
        self.assertEqual(self.controller._migration_error(),'')
        next(iter(self.w.settings[source_truth.TARGETS].values()))['uuid']='changed-after-validation'
        self.assertIn('needs attention',self.controller._migration_error())
        self.phases.pump();self.assertFalse(self.w.tag_controller.admitted)


if __name__=='__main__':unittest.main()
