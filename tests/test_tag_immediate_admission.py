"""Immediate tag gestures with held durable commits and generated native rows."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop.prep_tags import MyTagsButton
from desktop.state_commit import StatePublicationChangedError
from desktop.tag_assignment_intents import KEY
from desktop.tag_controller import TagEditController
from soundbox.library import LibraryTag
from soundbox.state_io import StateChangedError
from soundbox.state_repository import StateRepository, apply_state_patch, read_state
from test_prep_tags import _library


class _HeldState:
    """The sparse committer's admission/current/publication contract, held by GUI."""
    def __init__(self, library):
        self.settings = {}; self.lib = library; self.commit = None
        self.persisted = []; self.submissions = []
        self._state_repository = SimpleNamespace(generation=1, area=SimpleNamespace(generation=1))
        self._save_state = mock.Mock(side_effect=AssertionError('GUI full-document save'))

    def _commit_tag_state(self, replacements, removals, committed, failed, *, current):
        if self.commit is not None:
            return False
        self.submissions.append(deepcopy(replacements))
        self.commit = (deepcopy(replacements), tuple(removals), committed, failed, current)
        return True

    def complete(self, error=None):
        if self.commit is None:
            raise AssertionError('No held commit')
        replacements, removals, committed, failed, current = self.commit
        self.commit = None
        if error is not None:
            failed(error)
        elif not current():
            failed(StateChangedError('Captured state changed before publication'))
        else:
            self.settings = apply_state_patch({'settings': self.settings}, replacements, removals)['settings']
            self.persisted.append(deepcopy(self.settings))
            committed()


class ImmediateTagAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = _library()
        self.source.source_tags.append(LibraryTag('other', 'Favorites', '', 'other-uuid'))
        self.source.tracks_by_id = {tid: replace(track, source_tag_ids=[])
                                   for tid, track in self.source.tracks_by_id.items()}
        self.source.tracks_by_id['3'] = replace(self.source.tracks_by_id['2'], track_id='3',
                                               source_uuid='u3', location_uri='file:///not/read/three.wav')
        self.state = _HeldState(deepcopy(self.source))
        self.calls = []; self.errors = []; self.saved = []
        self.release = threading.Event(); self.release.set()
        self.started = threading.Event()
        outer = self

        class Store:
            def apply(self, request):
                # Native mutation can begin only after this exact request is durable.
                assert outer.state.persisted
                assert outer.state.settings[KEY][0]['request']['operation_id'] == request.operation_id
                outer.started.set()
                if not outer.release.wait(10):
                    raise RuntimeError('Isolated native fixture was not released')
                for target in request.tracks:
                    track = outer.source.tracks_by_id[target.track_id]
                    assigned = set(track.source_tag_ids)
                    assert (request.tag_id in assigned) == target.expected_present
                    if request.present: assigned.add(request.tag_id)
                    else: assigned.discard(request.tag_id)
                    outer.source.tracks_by_id[target.track_id] = replace(track, source_tag_ids=sorted(assigned))
                outer.calls.append(request)
                return len(request.tracks)

        self.controller = TagEditController(self.root, store_factory=lambda _: Store(),
            loader=lambda _: deepcopy(self.source), intent_owner=self.state)
        self.controller.configure(deepcopy(self.source), True)
        def saved(library):
            self.saved.append(library)
            self.state.lib = library
            self.controller.configure(library, True)
        self.controller.saved.connect(saved)
        self.controller.failed.connect(self.errors.append)
        self.addCleanup(self.cleanup_controller)

    def cleanup_controller(self):
        self.release.set()
        for _ in range(1000):
            if self.state.commit is not None: self.state.complete()
            if not self.controller.busy and not self.controller.tag_ack_pending and not self.controller.tag_state_lane.pending:
                break
            QTest.qWait(5)
        self.controller.shutdown()
        self.controller.deleteLater()

    def wait(self, predicate):
        for _ in range(1000):
            if predicate(): return
            QTest.qWait(5)
        self.fail('Held tag workflow did not reach the expected boundary')

    def settle(self, count):
        for _ in range(1000):
            if self.state.commit is not None: self.state.complete()
            if (len(self.calls) == count and not self.controller.pending
                    and not self.controller.tag_state_lane.pending and not self.controller.busy
                    and not self.controller.tag_ack_pending):
                self.assertFalse(self.errors)
                self.state._save_state.assert_not_called()
                return
            QTest.qWait(5)
        self.fail('Tag workflow did not settle: ' + repr(self.errors))

    def test_bulk_checks_immediately_before_canonical_admission_or_native_write(self):
        button = MyTagsButton(); self.addCleanup(button.deleteLater)
        ids = ['1', '2', '3']
        self.assertTrue(self.controller.request(ids, 'other', True))
        button.set_state(self.source.source_tags, self.controller.selected_tags(ids))
        self.assertEqual(button.checkboxes['other'].checkState(), Qt.Checked)
        self.assertEqual(self.controller.selected_tags(ids), [{'other'}] * 3)
        self.assertFalse(self.calls); self.assertFalse(self.state.persisted)
        self.assertNotIn(KEY, self.state.settings)
        self.assertTrue(self.controller.owned)
        self.state._save_state.assert_not_called()
        self.settle(1)
        self.assertEqual([target.track_id for target in self.calls[0].tracks], ids)
        self.assertTrue(all(track.source_tag_ids == ['other'] for track in self.source.tracks_by_id.values()))

    def test_rapid_same_track_multitag_and_toggle_preserve_exact_fifo_preconditions(self):
        gestures = [('tag', True), ('other', True), ('tag', False), ('tag', True)]
        for tag, present in gestures:
            self.assertTrue(self.controller.request(['2'], tag, present))
        self.assertEqual(self.controller.selected_tags(['2']), [{'tag', 'other'}])
        self.assertFalse(self.calls)
        self.settle(4)
        self.assertEqual([(request.tag_id, request.present) for request in self.calls], gestures)
        self.assertEqual([request.tracks[0].expected_present for request in self.calls], [False, False, True, False])
        self.assertEqual(set(self.source.tracks_by_id['2'].source_tag_ids), {'tag', 'other'})
        self.assertEqual(len({request.operation_id for request in self.calls}), 4)

    def test_new_selection_and_more_gestures_remain_usable_while_native_write_waits(self):
        self.release.clear()
        self.assertTrue(self.controller.request(['2'], 'tag', True))
        self.wait(lambda: self.state.commit is not None)
        self.state.complete()
        self.wait(self.started.is_set)
        self.assertEqual(self.controller.reason(['1']), '')
        self.assertTrue(self.controller.request(['1'], 'other', True))
        self.assertTrue(self.controller.request(['2'], 'tag', False))
        self.assertEqual(self.controller.selected_tags(['1', '2']), [{'other'}, set()])
        self.release.set()
        self.settle(3)
        self.assertEqual([request.tracks[0].track_id for request in self.calls], ['2', '1', '2'])
        self.assertEqual(self.source.tracks_by_id['2'].source_tag_ids, [])

    def test_older_admission_callback_preserves_gestures_captured_while_commit_is_held(self):
        self.assertTrue(self.controller.request(['1'], 'tag', True))
        self.wait(lambda: self.state.commit is not None)
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.state.complete()
        self.assertEqual(len(self.controller.pending), 1)
        self.assertEqual(len(self.controller.tag_state_lane.queue), 1)
        self.assertEqual(self.controller.selected_tags(['1', '2']), [{'tag'}, {'other'}])
        self.settle(2)
        self.assertEqual([request.tag_id for request in self.calls], ['tag', 'other'])

    def test_admission_failure_retains_bound_preview_and_retries_without_native_write(self):
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.wait(lambda: self.state.commit is not None)
        self.state.complete(OSError('Generated full disk'))
        self.assertFalse(self.calls); self.assertFalse(self.controller.pending)
        self.assertEqual(len(self.controller.tag_state_lane.failed), 1)
        self.assertEqual(self.controller.selected_tags(['2']), [{'other'}])
        self.assertIn('Generated full disk', self.controller.reason(['2']))
        self.assertTrue(self.controller.retry())
        self.errors.clear()
        self.settle(1)
        self.assertEqual(self.source.tracks_by_id['2'].source_tag_ids, ['other'])

    def test_failed_ram_discard_requires_confirmation_and_preserves_canonical_queue(self):
        repository = StateRepository(self.root / 'canonical/library.json')
        repository.save({'settings': {KEY: []}})
        self.state._state_repository = repository
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.wait(lambda: self.state.commit is not None)
        jobs = deepcopy(self.state.commit[0][('settings', KEY)])
        jobs[0]['error'] = 'Independent durable intention needs attention'
        self.state.complete(OSError('Generated full disk'))
        self.state.settings = {KEY: deepcopy(jobs), 'newer_ram': {'keep': True}}
        repository.save({'settings': {KEY: jobs, 'disk_only': True}})
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question', return_value=QMessageBox.No) as question:
            self.assertFalse(self.controller.discard())
        self.assertEqual(question.call_args.args[-1], QMessageBox.No)
        self.assertTrue(self.controller.tag_state_lane.failed)
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question', return_value=QMessageBox.Yes):
            self.assertTrue(self.controller.discard())
        self.assertFalse(self.controller.tag_state_lane.failed)
        self.assertEqual(self.state.settings, {KEY: jobs, 'newer_ram': {'keep': True}})
        self.assertEqual(read_state(repository.path)['settings'][KEY], jobs)
        self.assertFalse(self.calls)
        self.state._save_state.assert_not_called()

    def test_failed_durable_admission_discard_recovers_hidden_intent_and_preserves_other_ram(self):
        repository = StateRepository(self.root / 'canonical/library.json')
        repository.save({'settings': {KEY: []}})
        self.state._state_repository = repository
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.wait(lambda: self.state.commit is not None)
        jobs = deepcopy(self.state.commit[0][('settings', KEY)])
        repository.save({'settings': {KEY: jobs, 'disk_only': True}})
        self.state.persisted.append({KEY: deepcopy(jobs)})
        self.state.complete(StatePublicationChangedError('Durable result lost its GUI owner'))
        self.state.settings['newer_ram'] = {'keep': True}
        self.assertNotIn(KEY, self.state.settings)
        with mock.patch('desktop.tag_assignment_intents.QMessageBox.question', return_value=QMessageBox.Yes):
            self.assertTrue(self.controller.discard())
        self.assertFalse(self.controller.tag_state_lane.failed)
        self.assertEqual(self.state.settings, {KEY: jobs, 'newer_ram': {'keep': True}})
        self.assertEqual(read_state(repository.path)['settings'][KEY], jobs)
        self.assertFalse(self.calls)
        self.state._save_state.assert_not_called()

    def assert_changed_admission_rejected(self, change):
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.wait(lambda: self.state.commit is not None)
        change()
        self.state.complete()
        self.assertFalse(self.calls); self.assertNotIn(KEY, self.state.settings)
        self.assertEqual(self.controller.selected_tags(['2']), [set()])
        self.assertTrue(self.controller.tag_state_lane.failed)
        self.assertTrue(self.controller.owned)
        self.assertFalse(self.controller.retry())

    def test_changed_track_identity_rejects_held_admission_and_cannot_retarget_retry(self):
        def change():
            self.controller.lib.tracks_by_id['2'] = replace(self.controller.lib.tracks_by_id['2'], source_uuid='replacement')
        self.assert_changed_admission_rejected(change)

    def test_changed_tag_definition_rejects_held_admission(self):
        def change():
            self.controller.lib.source_tags[1] = replace(self.controller.lib.source_tags[1], name='Replacement')
        self.assert_changed_admission_rejected(change)

    def test_changed_owner_source_rejects_held_admission(self):
        def change():
            self.state.lib = replace(self.state.lib, source_library_id='replacement-library')
        self.assert_changed_admission_rejected(change)

    def test_changed_repository_generation_rejects_held_admission(self):
        def change():
            self.state._state_repository.area.generation += 1
        self.assert_changed_admission_rejected(change)

    def test_ram_queue_bound_preserves_every_accepted_gesture(self):
        for index in range(256):
            self.assertTrue(self.controller.request(['2'], 'other', index % 2 == 0))
        self.assertFalse(self.controller.request(['2'], 'other', True))
        self.assertEqual(len(self.controller.tag_state_lane.queue), 256)
        self.assertFalse(self.calls); self.assertFalse(self.state.persisted)
        self.assertEqual(self.controller.selected_tags(['2']), [set()])
        self.state._state_repository.area.generation += 1
        self.controller.tag_state_lane.pump()
        self.assertEqual(len(self.controller.tag_state_lane.failed), 256)

    def test_restore_generation_blocks_stale_native_ack_without_overwriting_new_state(self):
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.wait(lambda: self.state.commit is not None)
        self.state.complete()
        self.wait(lambda: self.controller.tag_ack_pending and self.state.commit is not None)
        self.state._state_repository.generation += 1
        self.state._state_repository.area.generation += 1
        self.state.settings = {KEY: [], 'restored': {'keep': True}}
        expected = deepcopy(self.state.settings)
        self.state.complete()
        self.assertEqual(self.state.settings, expected)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.saved)
        self.assertTrue(self.controller._tag_ack_conflict)

    def test_shutdown_materializes_ram_intention_before_retaining_unstarted_native_work(self):
        self.assertTrue(self.controller.request(['2'], 'other', True))
        self.assertNotIn(KEY, self.state.settings)
        timer = QTimer(); timer.setInterval(5)
        timer.timeout.connect(lambda: self.state.complete() if self.state.commit is not None else None)
        timer.start()
        try:
            self.controller.shutdown()
        finally:
            timer.stop()
        self.assertEqual(len(self.state.settings[KEY]), 1)
        self.assertEqual(self.state.settings[KEY][0]['request']['tracks'][0]['track_id'], '2')
        self.assertFalse(self.controller.tag_state_lane.pending)
        self.assertFalse(self.calls)
        self.state._save_state.assert_not_called()
