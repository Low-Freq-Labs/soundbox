"""Revision proofs cover ordinary JSON mutations, aliases and worker races."""
from copy import deepcopy
import gc
import json
import math
import threading
import unittest
from unittest import mock
import weakref

from soundbox import mutation_domain as domain


class MutationDomains(unittest.TestCase):
    def changed(self,value,operation):
        before=value.revision
        operation(value)
        self.assertGreater(value.revision,before)
        self.assertEqual(value.revision%2,0)

    def test_all_dictionary_instance_mutators_invalidate_revision(self):
        operations=[lambda v:v.__setitem__('x',{'nested':[2]}),lambda v:v.__delitem__('a'),
                    lambda v:v.clear(),lambda v:v.pop('a'),lambda v:v.popitem(),
                    lambda v:v.setdefault('new',[]),lambda v:v.update({'new':[]}),
                    lambda v:v.__ior__({'new':[]}),lambda v:v.__init__({'new':[]})]
        for operation in operations:
            with self.subTest(operation=operation):self.changed(domain.observe({'a':1}),operation)

    def test_all_list_instance_mutators_invalidate_revision(self):
        operations=[lambda v:v.__setitem__(0,{'x':[]}),lambda v:v.__setitem__(slice(0,1),[{'x':[]}]),
                    lambda v:v.__delitem__(0),lambda v:v.__delitem__(slice(0,1)),lambda v:v.append({'x':[]}),
                    lambda v:v.extend([{'x':[]}]),lambda v:v.insert(0,{'x':[]}),lambda v:v.pop(),
                    lambda v:v.remove(1),lambda v:v.clear(),lambda v:v.reverse(),lambda v:v.sort(),
                    lambda v:v.__iadd__([{'x':[]}]),lambda v:v.__imul__(2),lambda v:v.__init__([{'x':[]}])]
        for operation in operations:
            with self.subTest(operation=operation):self.changed(domain.observe([3,1,2]),operation)

    def test_nested_descendants_and_shared_observed_aliases_stamp_every_parent(self):
        child=domain.observe({'rows':[{'x':1}]})
        first=domain.observe({'shared':child});second=domain.observe([child])
        self.assertIs(first['shared'],child);self.assertIs(second[0],child)
        before=(first.revision,second.revision,child.revision)
        child['rows'][0]['x']=2
        self.assertTrue(all(after>old for after,old in zip((first.revision,second.revision,child.revision),before)))
        self.assertEqual(first.snapshot(),{'shared':{'rows':[{'x':2}]}})

    def test_reobserving_owner_retains_identity_and_snapshot_has_plain_types(self):
        value=domain.observe({'a':[{'b':1}]})
        self.assertIs(domain.observe(value),value)
        snapshot=value.snapshot()
        self.assertIs(type(snapshot),dict);self.assertIs(type(snapshot['a']),list)
        self.assertIs(type(snapshot['a'][0]),dict)
        snapshot['a'][0]['b']=100
        self.assertEqual(value['a'][0]['b'],1)

    def test_raw_external_aliases_detach_at_initial_wrap_and_every_assignment(self):
        raw={'nested':[1]};value=domain.observe({'row':raw})
        raw['nested'].append(2);self.assertEqual(value.snapshot(),{'row':{'nested':[1]}})
        for container,operation in ((domain.observe({}),lambda v,x:v.__setitem__('row',x)),
                (domain.observe({}),lambda v,x:v.update(row=x)),(domain.observe({}),lambda v,x:v.setdefault('row',x)),
                (domain.observe([]),lambda v,x:v.append(x)),(domain.observe([]),lambda v,x:v.extend([x])),
                (domain.observe([]),lambda v,x:v.insert(0,x)),(domain.observe([None]),lambda v,x:v.__setitem__(0,x)),
                (domain.observe([None]),lambda v,x:v.__setitem__(slice(0,1),[x]))):
            with self.subTest(operation=operation):
                external={'nested':[1]};operation(container,external);before=container.snapshot();revision=container.revision
                external['nested'].append(2)
                self.assertEqual(container.snapshot(),before);self.assertEqual(container.revision,revision)

    def test_deepcopy_and_json_keep_plain_value_contract(self):
        raw={'a':[{'x':True,'y':-0.0,'z':None}]};value=domain.observe(raw)
        self.assertEqual(json.dumps(value),json.dumps(raw))
        copied=deepcopy(value);self.assertIs(type(copied),dict)
        copied['a'][0]['x']=False
        self.assertTrue(value['a'][0]['x'])

    def test_frozen_snapshot_detaches_raw_aliases_and_reuses_only_exact_nested_revision(self):
        raw={'rows':[{'x':1}]};owner=domain.observe(raw);frozen=owner.frozen_snapshot()
        self.assertIs(owner.frozen_snapshot(),frozen);self.assertIs(deepcopy(frozen),frozen)
        self.assertIs(type(frozen),domain.FrozenDict)
        self.assertIs(type(frozen['rows']),domain.FrozenList)
        raw['rows'][0]['x']=9;self.assertEqual(frozen,{'rows':[{'x':1}]})
        alias=domain.observe({'shared':owner});alias_frozen=alias.frozen_snapshot()
        owner['rows'][0]['x']=2
        self.assertIsNot(owner.frozen_snapshot(),frozen)
        self.assertIsNot(alias.frozen_snapshot(),alias_frozen)
        self.assertEqual(frozen,{'rows':[{'x':1}]})
        self.assertEqual(alias.frozen_snapshot(),{'shared':{'rows':[{'x':2}]}})
        plain=owner.snapshot();self.assertIs(type(plain),dict);self.assertIs(type(plain['rows']),list)

    def test_every_normal_frozen_dictionary_and_list_mutator_refuses(self):
        frozen=domain.observe({'a':[3,1,2]}).frozen_snapshot();rows=frozen['a']
        operations=[lambda:frozen.__setitem__('new',{}),lambda:frozen.__delitem__('a'),
            lambda:frozen.clear(),lambda:frozen.pop('a'),lambda:frozen.popitem(),
            lambda:frozen.setdefault('new',{}),lambda:frozen.update(new={}),
            lambda:frozen.__ior__({'new':{}}),lambda:frozen.__init__({'new':{}}),
            lambda:rows.__setitem__(0,9),lambda:rows.__setitem__(slice(0,1),[9]),
            lambda:rows.__delitem__(0),lambda:rows.append(9),lambda:rows.extend([9]),
            lambda:rows.insert(0,9),lambda:rows.pop(),lambda:rows.remove(1),
            lambda:rows.clear(),lambda:rows.reverse(),lambda:rows.sort(),
            lambda:rows.__iadd__([9]),lambda:rows.__imul__(2),lambda:rows.__init__([9])]
        for operation in operations:
            with self.subTest(operation=operation),self.assertRaises(TypeError):operation()
        self.assertEqual(frozen,{'a':[3,1,2]})
        self.assertEqual(json.dumps(frozen),json.dumps({'a':[3,1,2]}))
        with self.assertRaises(TypeError):hash(frozen)
        with self.assertRaises(TypeError):hash(rows)

    def test_frozen_snapshot_rejects_concurrent_or_inflight_mutation_and_keeps_old_value(self):
        owner=domain.observe({'a':[1]});prior=owner.frozen_snapshot();owner['a'].append(2)
        entered=threading.Event();release=threading.Event();outcomes=[];original=domain._freeze
        def held(value,memo):
            if value is owner:
                entered.set()
                if not release.wait(3):raise RuntimeError('Frozen snapshot fixture timeout')
            return original(value,memo)
        def freeze():
            try:outcomes.append(owner.frozen_snapshot())
            except Exception as exc:outcomes.append(exc)
        with mock.patch.object(domain,'_freeze',side_effect=held):
            worker=threading.Thread(target=freeze);worker.start()
            try:self.assertTrue(entered.wait(3));owner['a'].append(3)
            finally:release.set();worker.join()
        self.assertIsInstance(outcomes[0],domain.MutationChanged)
        self.assertEqual(prior,{'a':[1]});self.assertEqual(owner.frozen_snapshot(),{'a':[1,2,3]})
        def incoming():
            with self.assertRaises(domain.MutationChanged):owner.frozen_snapshot()
            yield ('b',4)
        owner.update(incoming());self.assertEqual(owner.frozen_snapshot(),{'a':[1,2,3],'b':4})

    def test_invalid_values_cycles_and_cyclic_observed_adoption_are_rejected(self):
        cycle={};cycle['self']=cycle
        for value in ({'bad':set()}, {'bad':object()}, {'bad':math.inf}, {'bad':math.nan}, {1:'bad'},cycle):
            with self.subTest(value=value),self.assertRaises(ValueError):domain.observe(value)
        observed=domain.observe({'child':{}})
        with self.assertRaises(ValueError):observed['child']['parent']=observed
        self.assertEqual(observed.snapshot(),{'child':{}})
        self.assertEqual(observed.revision%2,0)

    def test_exceptions_end_epoch_even_and_invalidate_old_proof(self):
        for value,operation in ((domain.observe({'a':1}),lambda v:v.__delitem__('absent')),
                (domain.observe([1]),lambda v:v.__setitem__(slice(None,None,2),[1,2])),
                (domain.observe([1]),lambda v:v.remove(2))):
            before=value.revision
            with self.assertRaises((ValueError,KeyError)):operation(value)
            self.assertGreater(value.revision,before);self.assertEqual(value.revision%2,0)
            value.snapshot()

    def test_reentrant_key_sees_odd_epoch_and_never_a_stable_partial_snapshot(self):
        value=domain.observe([3,1,2]);seen=[]
        def key(number):
            seen.append(value.revision)
            with self.assertRaises(domain.MutationChanged):value.snapshot()
            return number
        value.sort(key=key)
        self.assertTrue(seen);self.assertTrue(all(revision%2 for revision in seen))
        self.assertEqual(value.snapshot(),[1,2,3]);self.assertEqual(value.revision%2,0)

    def test_generator_coercion_is_inside_odd_epoch_and_exception_keeps_partial_changes_dirty(self):
        value=domain.observe({'a':1});before=value.revision
        def entries():
            self.assertEqual(value.revision%2,1)
            with self.assertRaises(domain.MutationChanged):value.snapshot()
            value['nested_change']=2
            yield ('new',3)
            raise RuntimeError('injected coercion failure')
        with self.assertRaisesRegex(RuntimeError,'coercion'):value.update(entries())
        self.assertGreater(value.revision,before);self.assertEqual(value.revision%2,0)
        self.assertEqual(value.snapshot(),{'a':1,'nested_change':2})

    def test_worker_snapshot_rejects_mutation_between_start_and_finish(self):
        value=domain.observe({'a':[1]});entered=threading.Event();release=threading.Event();outcomes=[]
        original=domain._plain
        def delayed(current,memo):
            if current is value:
                entered.set()
                if not release.wait(3):raise RuntimeError('Snapshot fixture timeout')
            return original(current,memo)
        def snapshot():
            try:outcomes.append(value.snapshot())
            except Exception as exc:outcomes.append(exc)
        with mock.patch.object(domain,'_plain',side_effect=delayed):
            worker=threading.Thread(target=snapshot);worker.start()
            try:
                self.assertTrue(entered.wait(3));value['a'].append(2)
            finally:release.set();worker.join()
        self.assertEqual(len(outcomes),1);self.assertIsInstance(outcomes[0],domain.MutationChanged)

    def test_worker_cannot_snapshot_during_mutation_before_content_changes(self):
        value=domain.observe({'a':1});entered=threading.Event();release=threading.Event();errors=[]
        def entries():
            entered.set()
            if not release.wait(3):raise RuntimeError('Mutation fixture timeout')
            yield ('b',2)
        def update():
            try:value.update(entries())
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=update);worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(value.revision%2,1)
            with self.assertRaises(domain.MutationChanged):value.snapshot()
        finally:release.set();worker.join()
        self.assertEqual(errors,[]);self.assertEqual(value.snapshot(),{'a':1,'b':2})

    def test_adoption_cannot_miss_a_mutation_whose_ancestry_was_already_captured(self):
        value=domain.observe({'a':1});begin_entered=threading.Event();begin_release=threading.Event()
        body_entered=threading.Event();body_release=threading.Event();adopt_started=threading.Event();adopt_done=threading.Event()
        outcomes=[];errors=[];original=domain._Revision.ancestry
        def held_ancestry(node):
            result=original(node)
            if node is value._revision_node:
                begin_entered.set()
                if not begin_release.wait(3):raise RuntimeError('Ancestry fixture timeout')
            return result
        def entries():
            body_entered.set()
            if not body_release.wait(3):raise RuntimeError('Mutation body fixture timeout')
            yield ('b',2)
        def update():
            try:value.update(entries())
            except Exception as exc:errors.append(exc)
        def adopt():
            adopt_started.set()
            try:outcomes.append(domain.observe({'child':value}))
            except Exception as exc:outcomes.append(exc)
            finally:adopt_done.set()
        with mock.patch.object(domain._Revision,'ancestry',held_ancestry):
            updater=threading.Thread(target=update);adopter=threading.Thread(target=adopt);updater.start()
            try:
                self.assertTrue(begin_entered.wait(3));adopter.start();self.assertTrue(adopt_started.wait(3))
                begin_release.set();self.assertTrue(body_entered.wait(3));self.assertTrue(adopt_done.wait(3))
                self.assertIsInstance(outcomes[0],domain.MutationChanged)
            finally:
                begin_release.set();body_release.set();updater.join()
                if adopter.ident is not None:adopter.join()
        self.assertEqual(errors,[]);self.assertEqual(value.snapshot(),{'a':1,'b':2})

    def test_sort_failure_and_nested_mutation_never_publish_an_even_partial_epoch(self):
        value=domain.observe({'rows':[3,1,2],'count':{'value':0}});before=value.revision
        def key(number):
            value['count']['value']+=1
            self.assertEqual(value.revision%2,1)
            with self.assertRaises(domain.MutationChanged):value.snapshot()
            if number==1:raise RuntimeError('Injected key failure')
            return number
        with self.assertRaisesRegex(RuntimeError,'key failure'):value['rows'].sort(key=key)
        self.assertGreater(value.revision,before);self.assertEqual(value.revision%2,0)
        self.assertGreater(value.snapshot()['count']['value'],0)

    def test_weak_parent_links_do_not_retain_old_root_or_reuse_its_proof_identity(self):
        child=domain.observe({'a':1});parent=domain.observe({'child':child})
        reference=weakref.ref(parent._revision_node)
        del parent;gc.collect()
        self.assertIsNone(reference())
        child['a']=2;self.assertEqual(child.snapshot(),{'a':2})

    def test_dead_aliased_parents_do_not_hide_live_parent_or_retain_retired_owners(self):
        child=domain.observe({'a':1})
        first=domain.observe({'child':child});second=domain.observe([child])
        retired=(weakref.ref(first._revision_node),weakref.ref(second._revision_node))
        del first,second;gc.collect()
        self.assertTrue(all(reference() is None for reference in retired))
        live=domain.observe({'child':child});alias=domain.observe([child,child])
        before=(live.revision,alias.revision)
        child['a']=2
        self.assertTrue(all(new>old for new,old in zip((live.revision,alias.revision),before)))
        self.assertEqual(live.snapshot(),{'child':{'a':2}})
        alias_ref=weakref.ref(alias._revision_node)
        del alias;gc.collect();self.assertIsNone(alias_ref())
        before=live.revision;child['a']=3
        self.assertGreater(live.revision,before)


if __name__=='__main__':unittest.main()
