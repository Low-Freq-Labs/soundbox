"""Pure identity proposals never read source paths, decode audio or migrate state."""
from dataclasses import replace
import unittest

from research.native_library.identity import Binding, resolve
from soundbox.rekordbox_write import WriteProofError

_UUID='00000000-0000-4000-8000-000000000001'


class SharedIdentity(unittest.TestCase):
    def setUp(self):
        self.arguments=dict(local_id='sb-existing',canonical='/canonical/song.wav',
                            source='/explicit/master.db',library_id='123')
        self.row=dict(ID='42',UUID=_UUID,FolderPath='/canonical/song.wav',rb_local_deleted=0,
                      Title='Name is not identity',opaque={'retain':[1,2]})

    def test_existing_native_identity_is_reused_and_stable_local_id_retained(self):
        result=resolve(**self.arguments,native_rows=[self.row])
        self.assertEqual(result.action,'reuse')
        self.assertEqual(result.binding.local_id,'sb-existing')
        self.assertEqual(result.binding.native_id,'42')
        self.assertEqual(resolve(**self.arguments,native_rows=[self.row],previous=result.binding),result)

    def test_new_file_proposes_registration_without_allocating_identity_or_writing(self):
        result=resolve(**self.arguments,native_rows=[])
        self.assertEqual(result.action,'register')
        self.assertIsNone(result.binding)

    def test_same_title_or_identical_audio_at_different_paths_does_not_merge(self):
        for other in ('/other/song.wav','/canonical/other.wav'):
            row={**self.row,'FolderPath':other,'sha256':'same-exact-audio'}
            self.assertEqual(resolve(**self.arguments,native_rows=[row]).action,'register')

    def test_ambiguous_active_rows_keep_every_candidate_without_choosing_newest(self):
        second={**self.row,'ID':'43','UUID':'00000000-0000-4000-8000-000000000002'}
        result=resolve(**self.arguments,native_rows=[self.row,second])
        self.assertEqual(result.action,'conflict')
        self.assertEqual([r['ID'] for r in result.candidates],['42','43'])

    def test_tombstone_is_not_resurrected_or_silently_registered_again(self):
        old={**self.row,'rb_local_deleted':1}
        self.assertEqual(resolve(**self.arguments,native_rows=[old]).action,'conflict')
        self.assertEqual(resolve(**self.arguments,native_rows=[old,self.row]).action,'conflict')

    def test_lost_or_changed_binding_does_not_create_a_duplicate(self):
        previous=resolve(**self.arguments,native_rows=[self.row]).binding
        self.assertEqual(resolve(**self.arguments,native_rows=[],previous=previous).action,'conflict')
        for change in (dict(native_id='43'),dict(canonical='/moved/song.wav'),dict(library_id='124')):
            with self.subTest(change=change):
                result=resolve(**self.arguments,native_rows=[self.row],previous=replace(previous,**change))
                self.assertEqual(result.action,'conflict')

    def test_existing_logical_binding_requires_review_before_alias_or_curation_merge(self):
        other=Binding('sb-other','/explicit/master.db','123','42',_UUID,'/canonical/song.wav')
        result=resolve(**self.arguments,native_rows=[self.row],other_bindings=[other])
        self.assertEqual(result.action,'conflict')
        self.assertIn('reviewed',result.reason)

    def test_returned_candidate_is_detached_from_frozen_input(self):
        result=resolve(**self.arguments,native_rows=[self.row])
        result.candidates[0]['opaque']['retain'].clear()
        self.assertEqual(self.row['opaque']['retain'],[1,2])

    def test_unknown_history_or_noncanonical_identity_is_rejected(self):
        for value in (None,True,2,'0'):
            with self.subTest(value=value):
                self.assertEqual(resolve(**self.arguments,native_rows=[{**self.row,'rb_local_deleted':value}]).action,'conflict')
        with self.assertRaises(WriteProofError):resolve(**{**self.arguments,'canonical':'/canonical/../song.wav'},native_rows=[])
        with self.assertRaises(WriteProofError):resolve(**self.arguments,native_rows=[{**self.row,'UUID':'unknown'}])


if __name__=='__main__':unittest.main()
