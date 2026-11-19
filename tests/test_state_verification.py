"""Worker domain validation binds private canonical values and fresh disk guards."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from soundbox import backup,state_records,state_repository
from soundbox.mutation_domain import observe
from soundbox.state_io import StateChangedError
from soundbox.state_repository import StateRepository,VerificationChanged,read_state


class SelectedStateVerification(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.repository=StateRepository(self.root/'state/library.json')
        self.document={'settings':{'source_truth_targets':{'one':{'title':'First'},'two':{'title':'Second'}},
            'source_truth_migration':{'id':'fixture','cursor':0,'active':None,'scope':{'source':'fixture'},'order':['one','two']},
            'view':'Collection'}}
        self.repository.save(self.document)

    def expected(self):
        values=self.document['settings']
        return {('settings','source_truth_targets'):deepcopy(values['source_truth_targets']),
                **{('settings','source_truth_migration',key):deepcopy(value)
                   for key,value in values['source_truth_migration'].items()}}

    def test_exact_values_produce_opaque_lock_free_current_proof(self):
        proof=self.repository.verify_selected_values(self.expected())
        self.assertFalse(hasattr(proof,'values'));self.assertFalse(hasattr(proof,'document'))
        with mock.patch.object(self.repository.area,'lock',None):
            self.assertTrue(self.repository.current_verification(proof))
        other=StateRepository(self.repository.path);other.load()
        self.assertFalse(other.current_verification(proof))

    def test_immutable_selected_values_reuse_leaves_but_keep_fresh_canonical_guards(self):
        expected=self.expected()
        for path,value in tuple(expected.items()):
            if isinstance(value,(dict,list)):expected[path]=observe(value).frozen_snapshot()
        frozen=expected[('settings','source_truth_targets')]
        self.assertIs(deepcopy(expected)[('settings','source_truth_targets')],frozen)
        proof=self.repository.verify_selected_values(expected)
        self.assertTrue(self.repository.current_verification(proof))
        digest=next(iter(self.repository._record_epochs));record=state_records.record_path(self.repository.path,digest)
        record.write_bytes(record.read_bytes()+b'\n')
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)

    def frozen_expected(self):
        return {path:observe(value).frozen_snapshot() if isinstance(value,(dict,list)) else value
                for path,value in self.expected().items()}

    def test_positive_frozen_witness_survives_only_unchanged_private_cow_owners(self):
        expected=self.frozen_expected();path=('settings','source_truth_targets');frozen=expected[path]
        calls=[];compare=state_repository._same_json
        def counted(left,right):
            if left is frozen:calls.append(right)
            return compare(left,right)
        with mock.patch.object(state_repository,'_same_json',side_effect=counted):
            first=self.repository.verify_selected_values(expected)
            private=calls[0];self.assertEqual(len(calls),1)
            patch=self.repository.prepare_patch({('settings','source_truth_migration','cursor'):1,
                ('settings','view'):'Artists'})
            self.repository.commit_patch(patch)
            self.assertFalse(self.repository.current_verification(first))
            expected[('settings','source_truth_migration','cursor')]=1
            with mock.patch.object(state_records,'validate_epochs',wraps=state_records.validate_epochs) as guard, \
                    mock.patch.object(self.repository,'_identity',wraps=self.repository._identity) as identity:
                proof=self.repository.verify_selected_values(expected)
            self.assertEqual(guard.call_count,1)
            self.assertGreaterEqual(identity.call_count,2)
            self.assertTrue(self.repository.current_verification(proof))
            self.assertEqual(len(calls),1)
            self.assertIs(self.repository._verification_leaves[path][1],private)
            # A new immutable owner must establish its own complete comparison.
            expected[path]=observe(self.document['settings']['source_truth_targets']).frozen_snapshot()
            with mock.patch.object(state_repository,'_same_json',wraps=compare) as fresh_compare:
                self.repository.verify_selected_values(expected)
            self.assertTrue(any(call.args[0] is expected[path] for call in fresh_compare.call_args_list))

    def test_mutable_values_never_borrow_a_frozen_witness_even_if_previously_equal(self):
        frozen=self.frozen_expected();self.repository.verify_selected_values(frozen)
        expected=self.expected();path=('settings','source_truth_targets')
        with mock.patch.object(state_repository,'_same_json',wraps=state_repository._same_json) as compare:
            self.repository.verify_selected_values(expected)
        self.assertTrue(any(isinstance(call.args[0],dict) and call.args[0]==expected[path]
                            for call in compare.call_args_list))
        expected[path]['one']['title']='Unsaved newer intention'
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)
        self.repository.verify_selected_values(frozen)

    def test_canonical_replacement_and_reload_remove_old_witnesses(self):
        expected=self.frozen_expected();path=('settings','source_truth_targets')
        self.repository.verify_selected_values(expected)
        changed=deepcopy(self.document);changed['settings']['source_truth_targets']['one']['title']='New canonical'
        self.repository.save(changed)
        self.assertNotIn(path,self.repository._verification_leaves)
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)
        self.repository.load();self.assertEqual(self.repository._verification_leaves,{})

    def test_external_writer_and_record_drift_refuse_a_warm_positive_witness(self):
        expected=self.frozen_expected();self.repository.verify_selected_values(expected)
        external=StateRepository(self.repository.path);external.load()
        external.save({'settings':{**self.document['settings'],'view':'External'}})
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)
        self.repository.load();self.repository.verify_selected_values(expected)
        digest=next(iter(self.repository._record_epochs));record=state_records.record_path(self.repository.path,digest)
        record.write_bytes(record.read_bytes()+b'\n')
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)

    def test_generation_change_retires_witness_and_never_accepts_old_frozen_owner(self):
        expected=self.frozen_expected();self.repository.verify_selected_values(expected)
        selected=self.root/'fresh';selected.mkdir();(selected/'library.json').write_text('{}')
        backup.restore_backup(self.repository.path.parent,selected)
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)
        self.assertEqual(self.repository._verification_leaves,{})

    def test_failed_fresh_record_guard_never_promotes_new_witness(self):
        expected=self.frozen_expected()
        with mock.patch.object(state_records,'validate_epochs',side_effect=ValueError('Changed record')):
            with self.assertRaises(StateChangedError):self.repository.verify_selected_values(expected)
        self.assertEqual(self.repository._verification_leaves,{})

    def test_concurrent_commit_during_warm_guard_is_retryable_and_cannot_promote_old_owner(self):
        expected=self.frozen_expected();self.repository.verify_selected_values(expected)
        entered,release=threading.Event(),threading.Event();outcomes=[]
        guard=state_records.validate_epochs;worker_ids=[]
        def held(path,epochs):
            if threading.get_ident() in worker_ids:
                entered.set()
                if not release.wait(3):raise RuntimeError('Fixture timed out')
            return guard(path,epochs)
        def verify():
            worker_ids.append(threading.get_ident())
            try:outcomes.append(self.repository.verify_selected_values(expected))
            except Exception as exc:outcomes.append(exc)
        with mock.patch.object(state_records,'validate_epochs',side_effect=held):
            worker=threading.Thread(target=verify);worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.repository.commit_patch(self.repository.prepare_patch({('settings','view'):'New preference'}))
            finally:release.set();worker.join()
        self.assertEqual(len(outcomes),1)
        self.assertIsInstance(outcomes[0],VerificationChanged)

    def test_witness_retention_is_bounded_and_unrelated_sources_never_share_it(self):
        values={str(index):{'title':str(index)} for index in range(40)}
        self.repository.save({'settings':{**self.document['settings'],'many':values}})
        for key,value in values.items():
            self.repository.verify_selected_values({('settings','many',key):observe(value).frozen_snapshot()})
        self.assertLessEqual(len(self.repository._verification_leaves),32)
        other=StateRepository(self.repository.path);other.load()
        self.assertEqual(other._verification_leaves,{})

    def test_frozen_json_comparison_preserves_boolean_integer_zero_sign_and_key_order(self):
        canonical={'flag':True,'number':1,'zero':-0.0}
        self.repository.save({'settings':{**self.document['settings'],'strict':canonical}})
        path=('settings','strict')
        self.repository.verify_selected_values({path:observe(canonical).frozen_snapshot()})
        for changed in ({**canonical,'flag':1},{**canonical,'number':1.0},
                        {**canonical,'zero':0.0},dict(reversed(tuple(canonical.items())))):
            with self.subTest(changed=changed),self.assertRaises(StateChangedError):
                self.repository.verify_selected_values({path:observe(changed).frozen_snapshot()})

    def test_unsaved_valid_target_or_progress_mutation_is_attention_not_transient_retry(self):
        for path,value in ((('settings','source_truth_targets'),{'one':{'title':'Edited'},'two':{'title':'Second'}}),
                           (('settings','source_truth_migration','cursor'),1),
                           (('settings','source_truth_migration','active'),{'target':'one'})):
            with self.subTest(path=path):
                expected=self.expected();expected[path]=value
                with self.assertRaises(StateChangedError) as caught:self.repository.verify_selected_values(expected)
                self.assertNotIsInstance(caught.exception,VerificationChanged)
        self.assertEqual(read_state(self.repository.path),self.document)

    def test_missing_private_leaf_is_not_equal_to_none(self):
        with self.assertRaises(StateChangedError):
            self.repository.verify_selected_values({('settings','missing'):None})

    def test_competing_save_and_reload_invalidate_delivered_proof(self):
        proof=self.repository.verify_selected_values(self.expected())
        self.repository.save({})
        self.assertFalse(self.repository.current_verification(proof))
        proof=self.repository.verify_selected_values(self.expected())
        self.repository.load()
        self.assertFalse(self.repository.current_verification(proof))

    def test_restore_generation_invalidates_delivered_proof(self):
        proof=self.repository.verify_selected_values(self.expected());selected=self.root/'selected';selected.mkdir()
        (selected/'library.json').write_text('{}')
        backup.restore_backup(self.repository.path.parent,selected)
        self.assertFalse(self.repository.current_verification(proof))
        with self.assertRaises(StateChangedError):self.repository.verify_selected_values(self.expected())

    def test_external_manifest_and_record_drift_refuse_before_native_admission(self):
        original=self.repository.path.read_bytes();self.repository.path.write_bytes(original+b'\n')
        with self.assertRaises(StateChangedError) as caught:self.repository.verify_selected_values(self.expected())
        self.assertNotIsInstance(caught.exception,VerificationChanged)
        self.repository.path.write_bytes(original);self.repository.load()
        physical=__import__('json').loads(original)
        digest=physical['records']['source_truth_targets']['entries'][0][1]
        record=state_records.record_path(self.repository.path,digest);record.write_bytes(record.read_bytes()+b'\n')
        with self.assertRaises(StateChangedError) as caught:self.repository.verify_selected_values(self.expected())
        self.assertNotIsInstance(caught.exception,VerificationChanged)

    def test_full_compare_does_not_hold_area_lock_and_newer_owner_is_retryable(self):
        entered,release=threading.Event(),threading.Event();outcomes=[];original=state_repository._same_json
        worker_id=[]
        def compare(left,right):
            if threading.get_ident() in worker_id and not entered.is_set():
                entered.set()
                if not release.wait(3):raise RuntimeError('Verification fixture timed out')
            return original(left,right)
        def verify():
            worker_id.append(threading.get_ident())
            try:outcomes.append(self.repository.verify_selected_values(self.expected()))
            except Exception as exc:outcomes.append(exc)
        with mock.patch('soundbox.state_repository._same_json',side_effect=compare):
            worker=threading.Thread(target=verify);worker.start()
            try:
                self.assertTrue(entered.wait(3))
                # Completes while the complete worker comparison is held open.
                self.repository.save({'settings':{**self.document['settings'],'view':'Favorites'}})
                self.assertFalse(release.is_set())
            finally:release.set();worker.join()
        self.assertEqual(len(outcomes),1);self.assertIsInstance(outcomes[0],VerificationChanged)

    def test_record_retirement_during_pass_is_retryable_not_stable_corruption(self):
        entered,release=threading.Event(),threading.Event();outcomes=[];original=state_records.validate_epochs
        worker_id=[]
        def guard(path,epochs):
            if threading.get_ident() in worker_id:
                entered.set()
                if not release.wait(3):raise RuntimeError('Record guard fixture timed out')
            return original(path,epochs)
        def verify():
            worker_id.append(threading.get_ident())
            try:outcomes.append(self.repository.verify_selected_values(self.expected()))
            except Exception as exc:outcomes.append(exc)
        with mock.patch.object(state_records,'validate_epochs',side_effect=guard):
            worker=threading.Thread(target=verify);worker.start()
            try:
                self.assertTrue(entered.wait(3))
                changed=deepcopy(self.document);changed['settings']['source_truth_targets']['one']['title']='Newer'
                self.repository.save(changed)
            finally:release.set();worker.join()
        self.assertEqual(len(outcomes),1);self.assertIsInstance(outcomes[0],VerificationChanged)

    def test_observed_namespace_noop_preserves_record_and_manifest_bytes(self):
        incoming=self.repository.load();incoming['settings']['source_truth_targets']=observe(incoming['settings']['source_truth_targets'])
        original=self.repository.path.read_bytes()
        records={path.name:state_records.identity(path) for path in (self.repository.path.parent/state_records.DIRECTORY).iterdir()}
        self.repository.save(incoming)
        self.assertEqual(self.repository.path.read_bytes(),original)
        self.assertEqual({path.name:state_records.identity(path) for path in (self.repository.path.parent/state_records.DIRECTORY).iterdir()},records)
        incoming['settings']['source_truth_targets']['one']['title']='Changed'
        self.repository.save(incoming)
        self.assertEqual(read_state(self.repository.path)['settings']['source_truth_targets']['one']['title'],'Changed')

    def test_reentrant_save_cannot_acknowledge_namespace_mid_mutation(self):
        incoming=self.repository.load();targets=observe(incoming['settings']['source_truth_targets'])
        incoming['settings']['source_truth_targets']=targets;before=self.repository.path.read_bytes()
        def updates():
            with self.assertRaises(ValueError):self.repository.save(incoming)
            yield ('new',{'title':'New'})
        targets.update(updates())
        self.assertEqual(self.repository.path.read_bytes(),before)
        self.repository.save(incoming)
        self.assertIn('new',read_state(self.repository.path)['settings']['source_truth_targets'])


if __name__=='__main__':unittest.main()
