"""Background metadata stays observable without occupying foreground status."""
from pathlib import Path
import tempfile
from types import MethodType
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from desktop.app import MainWindow
from desktop.metadata_controller import ACKS, KEY, MetadataController, PAUSE_KEY
from desktop.tag_controller import TagEditController
from soundbox import source_truth
from soundbox.library import Library


class _Label(QLabel):
    def reserve(self, _text):
        pass


class BackgroundMetadataPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(QWidget, 'show', side_effect=AssertionError('Visible test window')))
        self.w = w = QWidget()
        w.settings = {}
        w.lib = Library({}, [], Path('/synthetic/master.db'),
                        source_kind='rekordbox-db', source_library_id='1')
        w._shutting_down = False
        w._state_maintenance = False
        w._stock_view_warming = set()
        w._preparing_library = False
        w._prep_update_message = ''
        w._deferred_edit_status = ''
        w._deferred_background_notice = None
        w._source_sync_waiting = None
        w._cdj_waiting = None
        w._cancelling = False
        w._stop_pulse = mock.Mock()
        w._update_device_action_state = mock.Mock()
        w._device_operation_owned = lambda: False
        w.status_label = _Label('Favorites · 211 songs', w)
        self.tags = w.tag_controller = TagEditController(root, w, store_factory=lambda _: mock.Mock())
        for name in ('_sync_edit_status', '_finish_edit_status', '_restore_edit_status',
                     '_edit_status_deferred', '_set_status_text', '_background_notice'):
            setattr(w, name, MethodType(getattr(MainWindow, name), w))
        w._flash_status = mock.Mock(side_effect=lambda text, *_: w._set_status_text(text))
        self.metadata = MetadataController(w)
        self.addCleanup(self.retire)

    def retire(self):
        self.metadata.stop()
        self.tags.busy = False
        self.tags._cue_queue.clear()
        self.tags._state_commit_token = None
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_paused_running_and_pending_metadata_never_replace_view_or_emit_done(self):
        for busy, message in ((True, 'Updating song details…'),
                              (False, 'Background song updates paused'),
                              (False, 'Metadata saves pending')):
            with self.subTest(message=message):
                self.tags.busy = busy
                self.tags._busy_kind = 'metadata'
                self.tags.metadata_update_status = message
                self.assertTrue(self.tags.update_status_text)
                self.assertEqual(self.tags.footer_status_text, '')
                self.w._sync_edit_status()
                self.w._finish_edit_status()
                self.w._restore_edit_status()
                self.assertEqual(self.w.status_label.text(), 'Favorites · 211 songs')
                self.w._flash_status.assert_not_called()
                self.w._set_status_text('Ready')
                self.assertEqual(self.w.status_label.text(), 'Ready')
                self.w.status_label.setText('Favorites · 211 songs')

    def test_quiet_canonical_checkpoint_preserves_ownership_and_lifecycle_status(self):
        token = object()
        self.assertTrue(self.tags.reserve_state_commit(token))
        self.assertTrue(self.tags.owned)
        self.assertTrue(self.tags.state_commit_owned)
        self.assertEqual(self.tags.update_status_text, 'Saving song details…')
        self.assertEqual(self.tags.footer_status_text, '')
        self.w._sync_edit_status()
        self.w._finish_edit_status()
        self.assertEqual(self.w.status_label.text(), 'Favorites · 211 songs')
        self.w._flash_status.assert_not_called()
        self.assertTrue(self.tags.release_state_commit(token))

    def test_explicit_foreground_operations_keep_their_existing_status(self):
        self.tags.metadata_update_status = 'Background song updates paused'
        for kind, text in (('playlists', 'Updating playlist…'),
                           ('media', 'Converting audio…'),
                           ('registration', 'Adding songs…'),
                           ('tags', 'Updating My Tags…')):
            with self.subTest(kind=kind):
                self.tags.busy = True
                self.tags._busy_kind = kind
                self.w._sync_edit_status()
                self.assertEqual(self.w.status_label.text(), text)

    def test_metadata_does_not_return_over_export_but_real_attention_remains_deferred(self):
        self.tags.busy = True
        self.tags._busy_kind = 'metadata'
        self.tags.metadata_update_status = 'Metadata saves pending'
        self.w._source_sync_waiting = object()
        self.w.status_label.setText('Preparing playlist export…')
        self.w._sync_edit_status()
        self.assertEqual(self.w.status_label.text(), 'Preparing playlist export…')
        self.assertEqual(self.w._deferred_edit_status, '')
        self.w._finish_edit_status()
        self.assertEqual(self.w._deferred_edit_status, '')
        self.w._deferred_edit_status = 'Done'  # An independently verified cue save.
        self.w._sync_edit_status()
        self.assertEqual(self.w._deferred_edit_status, 'Done')
        self.w._background_notice('A saved edit needs attention', 12)
        self.assertEqual(self.w.status_label.text(), 'Preparing playlist export…')
        self.w._source_sync_waiting = None
        self.w._restore_edit_status()
        self.assertEqual(self.w.status_label.text(), 'A saved edit needs attention')
        self.assertEqual(self.w._deferred_edit_status, '')
        self.w._restore_edit_status()
        self.w._flash_status.assert_called_once_with('A saved edit needs attention', 12)

    def test_menu_progress_uses_only_prepared_scalar_counts_and_retains_pause_context(self):
        class NoIteration(list):
            def __iter__(self):
                raise AssertionError('Menu scanned background songs')
            def __getitem__(self, _key):
                raise AssertionError('Menu read a background song')
        scope = source_truth.scope(self.w.lib)
        self.w.settings[source_truth.MIGRATION] = {
            'scope': scope, 'order': NoIteration(['target'] * 3121),
            'cursor': 2770, 'active': None, 'error': '',
        }
        with mock.patch.object(self.metadata, '_observed_truth', side_effect=AssertionError('Menu revalidated backlog')):
            self.assertEqual(self.metadata.background_summary,
                             'Song updates: 2,770 of 3,121 · Background')
            self.w.settings[PAUSE_KEY] = [scope]
            self.assertEqual(self.metadata.background_summary,
                             'Song updates: 2,770 of 3,121 · Paused')
            self.metadata._state_commit_error = 'Synthetic durable failure'
            self.assertIn('Needs attention', self.metadata.background_summary)

    def test_missing_library_has_no_background_progress_claim(self):
        self.w.lib = None
        self.assertEqual(self.metadata.background_summary, '')

    def test_durable_failure_is_visible_once_and_retains_menu_attention_without_migration(self):
        def reject(_replacements, _removals, _saved, failed):
            failed(OSError('Synthetic disk failure'))
            return False
        self.w._commit_metadata_state = reject
        before = self.w.settings
        self.w.settings = {'staged': 'value'}
        self.assertFalse(self.metadata._commit_background_state(
            {'settings': before}, {('settings', 'staged'): 'value'},
            committed=mock.Mock(side_effect=AssertionError('Failed state reported committed')),
            failed=mock.Mock(), message='Song details need storage attention'))
        self.assertIs(self.w.settings, before)
        self.assertFalse(self.metadata._state_commit_pending)
        self.assertEqual(self.w.status_label.text(), 'Song details need storage attention')
        self.assertEqual(self.metadata.background_summary, 'Song updates: Needs attention')
        self.metadata.pump()
        self.w._flash_status.assert_called_once_with('Song details need storage attention', 12)

    def test_truth_failure_is_deferred_behind_export_and_recovery_returns_to_quiet_status(self):
        self.w._source_sync_waiting = object()
        self.w.status_label.setText('Preparing playlist export…')
        with mock.patch.object(self.metadata, '_observed_truth', side_effect=ValueError('Bad saved truth')):
            self.assertFalse(self.metadata._ensure_truth_validation())
            self.assertFalse(self.metadata._ensure_truth_validation())
        self.w._flash_status.assert_not_called()
        self.assertEqual(self.w.status_label.text(), 'Preparing playlist export…')
        self.assertEqual(self.metadata.background_summary, 'Song updates: Needs attention')
        self.w._source_sync_waiting = None
        self.w._restore_edit_status()
        self.w._flash_status.assert_called_once_with(
            'Saved SoundBox source truth needs attention; all intentions are preserved', 12)
        self.metadata.status('Metadata saves pending')
        self.assertEqual(self.metadata.background_summary, '')
        self.assertEqual(self.tags.footer_status_text, '')

    def test_restored_failed_save_and_receipt_remain_actionable(self):
        for key in (KEY, ACKS):
            with self.subTest(domain=key):
                self.metadata.status('')
                self.w._flash_status.reset_mock()
                self.w.settings = {key: [{'error': 'A preserved save needs retry'}]}
                with (mock.patch.object(self.metadata, '_background_state_busy', return_value=False),
                      mock.patch.object(self.metadata, '_retirement_error', return_value=''),
                      mock.patch.object(self.metadata, '_migration_error', return_value=''),
                      mock.patch.object(self.metadata, 'queue_error', return_value=''),
                      mock.patch.object(self.metadata, '_background_migration_wait', return_value=False),
                      mock.patch.object(self.metadata, '_ensure_truth_validation', return_value=True)):
                    self.metadata.pump()
                    self.metadata.pump()
                self.assertIn('A preserved save needs retry', self.w.status_label.text())
                self.assertEqual(self.metadata.background_summary, 'Song updates: Needs attention')
                self.assertEqual(self.w._flash_status.call_count, 1)

    def test_paused_scheduler_cannot_dismiss_truth_failure_during_status_delivery(self):
        self.w.settings[PAUSE_KEY] = [source_truth.scope(self.w.lib)]
        with mock.patch.object(self.metadata, '_observed_truth', side_effect=ValueError('Bad saved truth')):
            self.assertFalse(self.metadata._ensure_truth_validation())
            self.metadata.schedule()
            self.assertFalse(self.metadata._ensure_truth_validation())
        self.assertEqual(self.metadata.background_summary, 'Song updates: Needs attention')
        self.w._flash_status.assert_called_once_with(
            'Saved SoundBox source truth needs attention; all intentions are preserved', 12)

    def test_late_failure_of_retired_intention_does_not_create_lifecycle_work(self):
        self.metadata.fail({'id': 'retired'}, 'A previous source check failed')
        self.assertEqual(self.tags.update_status_text, '')
        self.assertEqual(self.tags.footer_status_text, '')
        self.assertFalse(self.tags.owned)
        self.assertEqual(self.metadata.background_summary, '')
        self.w._flash_status.assert_called_once_with('A previous source check failed', 12)
