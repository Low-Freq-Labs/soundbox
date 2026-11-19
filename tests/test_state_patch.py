"""Worker-owned canonical deltas; disposable state only, no Qt/source actions."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from soundbox import backup, state_records
from soundbox import state_repository
from soundbox.state_io import StateChangedError
from soundbox.state_repository import InvalidState, StateCommit, StateRepository, read_state


class StatePatches(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = StateRepository(self.root / 'live/library.json')
        self.repository.save({'title_overrides': {'song': 'Original'}, 'settings': {
            'source_truth_artist_origins': {'song': {'title': 'Original'}, 'other': {'title': 'Keep'}},
            'pending_metadata_edits': [{'id': 'one', 'value': 'Original'}],
            'view': {'expanded': ['Keep']}, 'obsolete': True}, 'unknown': {'preserved': [-0.0, True]}})

    def patch(self):
        return self.repository.prepare_patch({('title_overrides', 'song'): 'Saved',
            ('settings', 'source_truth_artist_origins', 'song'): {'title': 'Saved'},
            ('settings', 'pending_metadata_edits'): []}, [('settings', 'obsolete')])

    def test_prepare_uses_no_area_lock_or_document_traversal(self):
        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError('Preparation acquired the state lock')
            def __exit__(self, *_args):
                pass
        with mock.patch.object(self.repository.area, 'lock', ForbiddenLock()), mock.patch(
                'soundbox.state_repository.validate_state', side_effect=AssertionError('Full validation on admission')):
            patch = self.patch()
        replacements, removals = patch.edits()
        self.assertEqual(replacements[('title_overrides', 'song')], 'Saved')
        self.assertEqual(removals, (('settings', 'obsolete'),))

    def test_worker_commit_detaches_input_and_receipt_without_exposing_other_state(self):
        incoming = [{'id': 'two', 'value': 'Frozen'}]
        patch = self.repository.prepare_patch({('settings', 'pending_metadata_edits'): incoming})
        incoming[0]['value'] = 'Later GUI edit'
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(self.repository.commit_patch(patch)))
        worker.start(); worker.join()
        self.assertEqual(len(outcomes), 1)
        commit = outcomes[0]
        self.assertIsInstance(commit, StateCommit)
        self.assertEqual(commit.backup_warning, '')
        replacements, removals = commit.patch.edits()
        self.assertEqual(set(replacements), {('settings', 'pending_metadata_edits')})
        self.assertEqual(removals, ())
        replacements[('settings', 'pending_metadata_edits')][0]['value'] = 'Receipt changed'
        self.assertEqual(read_state(self.repository.path)['settings']['pending_metadata_edits'][0]['value'], 'Frozen')
        self.assertEqual(commit.patch.edits()[0][('settings', 'pending_metadata_edits')][0]['value'], 'Frozen')

    def test_owned_leaf_changes_preserve_unrelated_records_and_unknown_values(self):
        before = read_state(self.repository.path)
        physical = json.loads(self.repository.path.read_bytes())
        digest = dict(physical['records']['source_truth_artist_origins']['entries'])['other']
        record = state_records.record_path(self.repository.path, digest)
        identity = state_records.identity(record)
        self.repository.commit_patch(self.patch())
        after = read_state(self.repository.path)
        self.assertEqual(after['title_overrides'], {'song': 'Saved'})
        self.assertEqual(after['settings']['source_truth_artist_origins']['other'], {'title': 'Keep'})
        self.assertEqual(after['settings']['view'], before['settings']['view'])
        self.assertEqual(after['unknown'], before['unknown'])
        self.assertNotIn('obsolete', after['settings'])
        self.assertEqual(state_records.identity(record), identity)

    def test_top_level_removal_is_not_remerged_from_previous_document(self):
        patch = self.repository.prepare_patch({}, [('unknown',)])
        self.repository.commit_patch(patch)
        self.assertNotIn('unknown', read_state(self.repository.path))

    def test_removing_absent_nested_leaf_does_not_create_empty_owners(self):
        before = read_state(self.repository.path)
        patch = self.repository.prepare_patch({}, [('settings', 'absent', 'song')])
        self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path), before)

    def test_competing_successful_save_even_noop_invalidates_patch(self):
        patch = self.patch()
        self.repository.save({})
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)

    def test_load_invalidates_patch(self):
        patch = self.patch()
        self.repository.load()
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)

    def test_lock_free_capture_during_load_is_obsolete_after_load_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = state_repository._read_document
        def delayed(path):
            entered.set()
            if not release.wait(3):raise RuntimeError('Load test timed out')
            return original(path)
        with mock.patch('soundbox.state_repository._read_document', side_effect=delayed):
            worker = threading.Thread(target=self.repository.load)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                patch = self.patch()
            finally:
                release.set(); worker.join()
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)

    def test_other_repository_and_replay_are_rejected(self):
        patch = self.patch()
        other = StateRepository(self.repository.path); other.load()
        with self.assertRaises(StateChangedError):
            other.commit_patch(patch)
        self.repository.commit_patch(patch)
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)

    def test_delivery_fence_is_lock_free_and_rejects_newer_owner_or_generation(self):
        commit = self.repository.commit_patch(self.patch())
        other = StateRepository(self.repository.path); other.load()
        self.assertFalse(other.current_commit(commit))
        with mock.patch.object(self.repository.area, 'lock', None):
            self.assertTrue(self.repository.current_commit(commit))
        self.repository.save({})
        self.assertFalse(self.repository.current_commit(commit))
        commit = self.repository.commit_patch(self.patch())
        selected = self.root / 'selected-fence'; selected.mkdir()
        (selected / 'library.json').write_text('{}')
        backup.restore_backup(self.repository.path.parent, selected)
        self.assertFalse(self.repository.current_commit(commit))

    def test_restore_generation_invalidates_patch_and_preserves_restored_state(self):
        patch = self.patch()
        selected = self.root / 'selected'; selected.mkdir()
        (selected / 'library.json').write_text('{"title_overrides":{"song":"Restored"}}')
        backup.restore_backup(self.repository.path.parent, selected)
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path)['title_overrides']['song'], 'Restored')

    def test_invalid_or_overlapping_paths_and_nonfinite_values_are_rejected_before_worker(self):
        for replacements, removals in (({(): 1}, ()), ({('settings',): {}}, [('settings', 'view')]),
                ({('settings', 'view'): {}}, [('settings', 'view')]), ({('settings', 'bad'): float('nan')}, ()),
                ({}, ()), ({('settings', ''): 1}, ())):
            with self.subTest(replacements=replacements, removals=removals), self.assertRaises(InvalidState):
                self.repository.prepare_patch(replacements, removals)

    def test_non_dictionary_path_and_invalid_document_do_not_mutate_private_base(self):
        before = read_state(self.repository.path)
        for replacements in ({('settings', 'pending_metadata_edits', 'zero'): 1},
                             {('settings', 'waveform_zoom'): -1}):
            patch = self.repository.prepare_patch(replacements)
            with self.assertRaises(InvalidState):
                self.repository.commit_patch(patch)
            self.assertEqual(read_state(self.repository.path), before)
        self.repository.commit_patch(self.patch())

    def test_publication_failure_preserves_exact_state_and_same_recipe_can_retry(self):
        patch = self.patch(); before = read_state(self.repository.path)
        with mock.patch.object(self.repository.area, 'publish', side_effect=OSError('Injected full disk')):
            with self.assertRaisesRegex(OSError, 'full disk'):
                self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path), before)
        self.repository.commit_patch(patch)
        self.assertEqual(read_state(self.repository.path)['title_overrides']['song'], 'Saved')

    def test_external_manifest_change_refuses_to_overwrite_it(self):
        patch = self.patch()
        original = self.repository.path.read_bytes()
        self.repository.path.write_bytes(original + b'\n')
        changed = self.repository.path.read_bytes()
        with self.assertRaises(StateChangedError):
            self.repository.commit_patch(patch)
        self.assertEqual(self.repository.path.read_bytes(), changed)

    def test_fresh_record_corruption_guard_survives_async_delta(self):
        patch = self.patch(); physical = json.loads(self.repository.path.read_bytes())
        digest = physical['records']['source_truth_artist_origins']['entries'][0][1]
        record = state_records.record_path(self.repository.path, digest)
        record.write_bytes(record.read_bytes() + b'\n')
        before = self.repository.path.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            self.repository.commit_patch(patch)
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_prepublication_second_record_guard_survives_async_delta(self):
        patch = self.patch(); physical = json.loads(self.repository.path.read_bytes())
        digest = physical['records']['source_truth_artist_origins']['entries'][0][1]
        record = state_records.record_path(self.repository.path, digest)
        before = self.repository.path.read_bytes()
        def corrupt(*, records_checked=False):
            self.assertTrue(records_checked)
            record.write_bytes(record.read_bytes() + b'\n')
        with mock.patch.object(self.repository, '_backup', side_effect=corrupt):
            with self.assertRaises((ValueError, OSError)):
                self.repository.commit_patch(patch)
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_competing_worker_commits_have_one_winner(self):
        patches = [self.repository.prepare_patch({('title_overrides', 'song'): value})
                   for value in ('First', 'Second')]
        barrier = threading.Barrier(3)
        outcomes = []
        def commit(patch):
            barrier.wait()
            try:
                outcomes.append(self.repository.commit_patch(patch))
            except StateChangedError as exc:
                outcomes.append(exc)
        workers = [threading.Thread(target=commit, args=(patch,)) for patch in patches]
        for worker in workers:worker.start()
        barrier.wait()
        for worker in workers:worker.join()
        self.assertEqual(sum(isinstance(value, StateCommit) for value in outcomes), 1)
        self.assertEqual(sum(isinstance(value, StateChangedError) for value in outcomes), 1)

    def test_changed_legacy_base_during_composition_refuses_publication(self):
        repository = StateRepository(self.root / 'legacy/library.json')
        repository.save({'custom': {'Original': []}})
        patch = repository.prepare_patch({('custom', 'Added'): ['song']})
        external = b'{"custom":{"Independent":[]}}'
        with mock.patch.object(repository, '_backup', side_effect=lambda: repository.path.write_bytes(external)):
            with self.assertRaises(StateChangedError):
                repository.commit_patch(patch)
        self.assertEqual(repository.path.read_bytes(), external)

    def test_malformed_or_duplicate_immutable_recipe_is_rejected(self):
        patch = self.patch()
        for payload in (b'[]', b'{"set":[],"remove":[]}', b'{"set":[[["settings"],1],[["settings"],2]],"remove":[]}',
                        b'{"set":[[["settings"],1]],"remove":[["settings","child"]]}'):
            with self.subTest(payload=payload), self.assertRaises(InvalidState):
                self.repository.commit_patch(replace(patch, _payload=payload))


if __name__ == '__main__':
    unittest.main()
