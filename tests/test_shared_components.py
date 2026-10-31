"""Pure reconciliation; no SQL, filesystem, worker, player or AppData access."""
from copy import deepcopy
from dataclasses import replace
import unittest

from research.native_library.components import Component, Intention, resolve, resolve_components
from soundbox.rekordbox_write import ConflictError, WriteProofError


class SharedComponents(unittest.TestCase):
    def setUp(self):
        self.base=Component(('/explicit/source','library','track-uuid'),'grid','base-revision',
                            {'bpm':119.85,'beats':[[1,227,119.85],[2,728,119.85]]})
        self.wanted={'bpm':120.0,'beats':[[1,227,120.0],[2,727,120.0]]}
        self.intent=Intention(self.base,self.wanted,'manual')

    def test_new_native_revision_with_same_component_permits_pending_intention(self):
        current=replace(self.base,revision='later-revision')
        result=resolve(current,self.intent)
        self.assertEqual(result.status,'apply')
        self.assertEqual(result.current.revision,'later-revision')
        self.assertEqual(result.intended,self.wanted)

    def test_identical_committed_value_retires_draft_even_if_now_locked(self):
        current=replace(self.base,revision='later',value=self.wanted,locked=True,manual=True)
        self.assertEqual(resolve(current,self.intent).status,'satisfied')

    def test_same_component_conflict_keeps_both_values_without_clock_or_author_preference(self):
        native={'bpm':121,'beats':[[1,300,121],[2,796,121]]}
        current=replace(self.base,revision='later',value=native)
        result=resolve(current,self.intent)
        self.assertEqual(result.status,'conflict')
        self.assertEqual(result.current.value,native)
        self.assertEqual(result.intended,self.wanted)

    def test_noop_draft_does_not_mask_new_native_value(self):
        current=replace(self.base,revision='later',value=self.wanted)
        result=resolve(current,Intention(self.base,self.base.value,'manual'))
        self.assertEqual(result.status,'unchanged')
        self.assertEqual(result.current.value,self.wanted)

    def test_analysis_cannot_replace_manual_or_locked_components(self):
        for current in (replace(self.base,manual=True),replace(self.base,locked=True)):
            with self.subTest(current=current):
                self.assertEqual(resolve(current,replace(self.intent,cause='analysis')).status,'blocked')
        self.assertEqual(resolve(replace(self.base,manual=True),self.intent).status,'apply')
        self.assertEqual(resolve(replace(self.base,locked=True),self.intent).status,'blocked')

    def test_independent_key_edit_preserves_new_native_cues_and_grid(self):
        key=Component(self.base.binding,'key','key-base','7A')
        cues=Component(self.base.binding,'cues','cue-new',[{'native_id':'cue','position_ms':300}])
        current={'grid':replace(self.base,value=self.wanted,revision='grid-new'),'key':key,'cues':cues}
        original=deepcopy(current)
        result=resolve_components(current,{'key':Intention(key,'8A','manual')})
        self.assertEqual(set(result),{'key'})
        self.assertEqual(result['key'].status,'apply')
        self.assertEqual(current,original)

    def test_identity_change_or_missing_component_requires_verified_rebase(self):
        for binding in (('/other/source','library','track-uuid'),('/explicit/source','other','track-uuid'),
                        ('/explicit/source','library','different-uuid')):
            with self.subTest(binding=binding),self.assertRaises(ConflictError):
                resolve(replace(self.base,binding=binding),self.intent)
        with self.assertRaises(ConflictError):resolve_components({}, {'grid':self.intent})

    def test_mutating_result_cannot_change_current_or_original_draft(self):
        result=resolve(self.base,self.intent)
        result.current.value['beats'].clear();result.intended['beats'].clear()
        self.assertEqual(len(self.base.value['beats']),2)
        self.assertEqual(len(self.intent.value['beats']),2)

    def test_nonfinite_unknown_or_inconsistent_components_fail_closed(self):
        with self.assertRaises(WriteProofError):resolve(self.base,replace(self.intent,value=float('nan')))
        with self.assertRaises(WriteProofError):resolve(replace(self.base,revision=''),self.intent)
        with self.assertRaises(WriteProofError):resolve(self.base,replace(self.intent,cause='unknown'))
        with self.assertRaises(WriteProofError):resolve_components({'key':self.base},{})
        with self.assertRaises(ConflictError):resolve(replace(self.base,value=self.wanted),self.intent)


if __name__=='__main__':unittest.main()
