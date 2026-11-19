"""UI preferences preserve latest RAM owners without synchronous library saves."""
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.settings_page import SettingsPageMixin


class AppPreferencePersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        coordinator=SimpleNamespace(queue_preferences=mock.Mock(return_value=True),
            preferences_saved=mock.Mock(),pending=False,preferences_error=None,
            defer_save=mock.Mock())
        self.w=SimpleNamespace(settings={'quantize':False,'waveform_zoom':92,
                'source_curation':{'active':{'pins':{'song':{'title':'retained'}}}}},
            _state_committer=coordinator,_settings_dirty_keys=set(),
            _settings_save_timer=QTimer(),_shutting_down=False,_state_maintenance=False,
            _state_publication=False,_state_repository=SimpleNamespace(save=mock.Mock(),backup_error=None),
            _sync_playlists_cache=object(),custom={},imported={},overrides={},titles={},
            hidden=set(),hidden_dates={},removals={},playlist_additions={},aliases={},
            _last_xml='',setWindowTitle=mock.Mock(),_flash_status=mock.Mock())
        self.w._flush_settings_preferences=lambda:MainWindow._flush_settings_preferences(self.w)
        self.addCleanup(self.w._settings_save_timer.stop)

    def test_exact_debounced_leaves_capture_latest_values_after_ack_owner_replacement(self):
        w=self.w
        for value in (100,120,150):
            w.settings['waveform_zoom']=value
            MainWindow._schedule_settings_save(w,'waveform_zoom')
        MainWindow._schedule_settings_save(w,'quantize')
        self.assertTrue(w._settings_save_timer.isActive())
        w._state_repository.save.assert_not_called()
        w._state_committer.queue_preferences.assert_not_called()
        # A source ACK replaces settings while preserving the user's latest UI values.
        w.settings={**w.settings,'source_curation':{'active':{'pins':{'song':{'title':'fresh'}}}}}
        self.assertTrue(MainWindow._flush_settings_preferences(w))
        w._state_committer.queue_preferences.assert_called_once_with(
            {'quantize':False,'waveform_zoom':150},frozenset())
        self.assertEqual(w._settings_dirty_keys,set())
        self.assertEqual(w.settings['source_curation']['active']['pins']['song']['title'],'fresh')

    def test_missing_preference_is_explicit_leaf_removal_not_complete_settings(self):
        MainWindow._schedule_settings_save(self.w,'waveform_zoom')
        self.w.settings.pop('waveform_zoom')
        self.assertTrue(MainWindow._flush_settings_preferences(self.w))
        self.w._state_committer.queue_preferences.assert_called_once_with({},frozenset({'waveform_zoom'}))

    def test_rejected_admission_preserves_dirty_keys_and_reports_unsaved_preferences(self):
        self.w._state_committer.queue_preferences.return_value=False
        MainWindow._schedule_settings_save(self.w,'quantize')
        self.assertFalse(MainWindow._flush_settings_preferences(self.w))
        self.assertEqual(self.w._settings_dirty_keys,{'quantize'})
        self.w._flash_status.assert_called_once()
        self.w._state_repository.save.assert_not_called()

    def test_full_synchronous_checkpoint_covers_and_clears_queued_preferences(self):
        MainWindow._schedule_settings_save(self.w,'quantize')
        self.assertTrue(MainWindow._save_state(self.w))
        self.w._state_repository.save.assert_called_once()
        self.assertEqual(self.w._state_repository.save.call_args.args[0]['settings'],self.w.settings)
        self.w._state_committer.preferences_saved.assert_called_once_with()
        self.assertFalse(self.w._settings_save_timer.isActive())
        self.assertEqual(self.w._settings_dirty_keys,set())

    def test_failed_full_checkpoint_preserves_preference_intentions(self):
        MainWindow._schedule_settings_save(self.w,'quantize')
        self.w._state_repository.save.side_effect=OSError('TEMP full')
        self.assertFalse(MainWindow._save_state(self.w))
        self.assertEqual(self.w._settings_dirty_keys,{'quantize'})
        self.w._state_committer.preferences_saved.assert_not_called()

    def test_busy_full_checkpoint_queues_preferences_without_claiming_business_save(self):
        MainWindow._schedule_settings_save(self.w,'quantize')
        self.w._state_publication=True;self.w._state_committer.pending=True
        self.assertFalse(MainWindow._save_state(self.w))
        self.w._state_committer.queue_preferences.assert_called_once_with({'quantize':False},frozenset())
        self.w._state_committer.defer_save.assert_called_once_with()
        self.w._state_repository.save.assert_not_called()
        self.w._state_committer.preferences_saved.assert_not_called()

    def test_queued_preferences_block_source_and_maintenance_before_worker_starts(self):
        self.w._state_committer.pending=True
        self.w.tag_controller=SimpleNamespace();self.w._refresh_worker=None
        self.w._backup_worker=None
        self.assertIn('before changing sources',MainWindow._source_selection_reason(self.w))
        self.assertFalse(SettingsPageMixin._begin_state_maintenance(self.w))

    def test_quit_reason_distinguishes_pending_preferences_from_failed_ones(self):
        self.w._state_committer.pending=True
        self.assertIn('Finishing saves',MainWindow._pending_save_reason(self.w))
        self.w._state_committer.preferences_error=OSError('TEMP disk full')
        self.assertEqual(MainWindow._pending_save_reason(self.w),
                         'Preferences save needs attention — TEMP disk full')

    def test_musical_policy_or_unspecified_keys_cannot_enter_preference_route(self):
        for keys in ((),('source_curation',),('pending_metadata_edits',),('title_rules_off',)):
            with self.assertRaises(ValueError):MainWindow._schedule_settings_save(self.w,*keys)
        self.assertFalse(self.w._settings_save_timer.isActive())
        self.assertEqual(self.w._settings_dirty_keys,set())

    def test_observed_device_display_history_uses_only_its_preference_leaf(self):
        self.w.settings['stock_ipod_identity']={'device':{'firmware_version':'1.3','firmware_source':'observed'}}
        MainWindow._schedule_settings_save(self.w,'stock_ipod_identity')
        self.assertTrue(MainWindow._flush_settings_preferences(self.w))
        self.w._state_committer.queue_preferences.assert_called_once_with(
            {'stock_ipod_identity':self.w.settings['stock_ipod_identity']},frozenset())
        self.w._state_repository.save.assert_not_called()


if __name__=='__main__':unittest.main()
