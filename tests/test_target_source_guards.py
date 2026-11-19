"""Generated complete source-boundary checks without repeated target walks."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox import sync


class TargetSourceGuardTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name).resolve()
        self.target=self.base/'target';self.target.mkdir()
        self.source=self.base/'sources';self.source.mkdir()
        self.sources=[self.source/f'track-{index}.wav'for index in range(3)]

    def test_target_resolves_only_at_endpoints_every_source_parent_and_file_remain_fresh(self):
        resolve=Path.resolve;calls=[]
        def observed(path,*args,**kwargs):
            calls.append(path);return resolve(path,*args,**kwargs)
        with mock.patch.object(Path,'resolve',observed):
            sync.assert_target_safe(self.target,self.sources)
        self.assertEqual(calls.count(self.target),2)
        self.assertEqual(calls.count(self.source),len(self.sources))
        for source in self.sources:self.assertEqual(calls.count(source),1)

    def test_unselected_source_parent_containing_target_refuses(self):
        selected=self.sources[0]
        unselected=self.base/'other-library'/'source.wav'
        unsafe=unselected.parent/'device';unsafe.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
            sync.DeviceSession(unsafe,[selected,unselected],create=False)

    def test_terminal_source_symlink_into_target_refuses_even_when_lexical_parent_is_safe(self):
        inside=self.target/'audio.wav';inside.write_bytes(b'generated')
        alias=self.source/'outside.wav';alias.symlink_to(inside)
        with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
            sync.assert_target_safe(self.target,[alias])

    def test_each_repeated_source_parent_is_resolved_after_directory_retargets(self):
        alias=self.base/'source-alias';alias.symlink_to(self.source,target_is_directory=True)
        first,second=alias/'first.wav',alias/'second.wav'
        resolve=Path.resolve
        def retarget(path,*args,**kwargs):
            result=resolve(path,*args,**kwargs)
            if path==first:
                alias.unlink();alias.symlink_to(self.base,target_is_directory=True)
            return result
        with mock.patch.object(Path,'resolve',retarget):
            with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
                sync.assert_target_safe(self.target,[first,second])

    def test_target_retargeting_during_source_pass_refuses_before_admission(self):
        other=self.base/'other-target';other.mkdir()
        alias=self.base/'target-alias';alias.symlink_to(self.target,target_is_directory=True)
        resolve=Path.resolve
        def retarget(path,*args,**kwargs):
            result=resolve(path,*args,**kwargs)
            if path==self.sources[-1]:
                alias.unlink();alias.symlink_to(other,target_is_directory=True)
            return result
        with mock.patch.object(Path,'resolve',retarget):
            with self.assertRaisesRegex(RuntimeError,'target changed during'):
                sync.assert_target_safe(alias,self.sources)

    def test_existing_target_uses_one_complete_pass_new_target_uses_two(self):
        guard=sync.assert_target_safe
        with mock.patch.object(sync,'assert_target_safe',wraps=guard)as checks:
            session=sync.DeviceSession(self.target,self.sources,create=False)
        self.assertEqual(checks.call_count,1)
        self.assertEqual(checks.call_args.args[1],self.sources)
        self.assertEqual(session.target,self.target)
        fresh=self.base/'new-target'
        with mock.patch.object(sync,'assert_target_safe',wraps=guard)as checks:
            sync.DeviceSession(fresh,self.sources,create=True)
        self.assertEqual(checks.call_count,2)
        self.assertTrue(all(call.args[1]==self.sources for call in checks.call_args_list))

    def test_existing_session_refuses_target_component_replacement_during_its_complete_pass(self):
        other=self.base/'other-target';other.mkdir()
        resolve=Path.resolve
        def replace_target(path,*args,**kwargs):
            result=resolve(path,*args,**kwargs)
            if path==self.sources[-1]:
                self.target.rename(self.base/'previous-target')
                self.target.symlink_to(other,target_is_directory=True)
            return result
        with mock.patch.object(Path,'resolve',replace_target):
            with self.assertRaisesRegex(RuntimeError,'target changed during'):
                sync.DeviceSession(self.target,self.sources,create=False)

    def test_unsafe_target_is_not_created_before_complete_precreation_guard(self):
        unsafe=self.source/'new-target'
        with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
            sync.DeviceSession(unsafe,self.sources,create=True)
        self.assertFalse(unsafe.exists())

    def test_second_create_pass_rechecks_source_terminal_links_without_persistent_cache(self):
        inside=self.target/'audio.wav';inside.write_bytes(b'generated')
        source=self.source/'source.wav';outside=self.source/'outside.wav';outside.write_bytes(b'outside')
        source.symlink_to(outside)
        sync.DeviceSession(self.target,[source],create=False)
        source.unlink();source.symlink_to(inside)
        with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
            sync.DeviceSession(self.target,[source],create=False)


if __name__=='__main__':unittest.main()
