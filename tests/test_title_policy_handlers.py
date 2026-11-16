"""Existing cleanup controls save one intention before changing presentation."""
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.settings_page import SettingsPageMixin
from soundbox.library import Library


class TitlePolicyHandlers(unittest.TestCase):
    def window(self, native=False):
        window = SimpleNamespace(
            lib=Library({}, [], '/synthetic/master.db', source_kind='rekordbox-db' if native else 'xml'),
            settings={'title_words': ['promo'], 'title_rules_off': [], 'unrelated': {'retained': True}},
            _save_state=mock.Mock(return_value=True), _refresh_title_rules=mock.Mock(),
            _apply_settings_to_lib=mock.Mock(), _populate_tree=mock.Mock(), _reload_current_view=mock.Mock())
        window._commit_title_policy = lambda changes: SettingsPageMixin._commit_title_policy(window, changes)
        return window

    def test_native_policy_uses_single_durable_generation_without_a_second_save(self):
        window = self.window(native=True)
        def commit(changes):
            window._apply_settings_to_lib.assert_not_called()
            window.settings = {**window.settings, **changes}
            return True
        window.metadata_controller = SimpleNamespace(change_title_policy=mock.Mock(side_effect=commit))
        SettingsPageMixin._remove_drop_word(window, 'promo')
        window.metadata_controller.change_title_policy.assert_called_once_with({'title_words': []})
        window._save_state.assert_not_called()
        window._apply_settings_to_lib.assert_called_once()
        window._reload_current_view.assert_called_once()

    def test_failed_native_intention_restores_control_and_keeps_old_display(self):
        window = self.window(native=True)
        window.metadata_controller = SimpleNamespace(change_title_policy=mock.Mock(return_value=False))
        old = window.settings
        self.assertFalse(window._commit_title_policy({'title_rules_off': ['drop']}))
        self.assertIs(window.settings, old)
        window._refresh_title_rules.assert_called_once()
        window._save_state.assert_not_called()
        window._apply_settings_to_lib.assert_not_called()
        window._reload_current_view.assert_not_called()

    def test_legacy_failed_save_restores_exact_owner_before_refreshing_controls(self):
        window = self.window()
        window._save_state.return_value = False
        old = window.settings
        def restore_controls():
            self.assertIs(window.settings, old)
        window._refresh_title_rules.side_effect = restore_controls
        self.assertFalse(window._commit_title_policy({'title_words': []}))
        self.assertIs(window.settings, old)
        window._save_state.assert_called_once()
        window._apply_settings_to_lib.assert_not_called()

    def test_unchanged_policy_has_no_save_generation_or_view_work(self):
        window = self.window(native=True)
        window.metadata_controller = SimpleNamespace(change_title_policy=mock.Mock())
        self.assertTrue(window._commit_title_policy({'title_words': ['promo']}))
        window.metadata_controller.change_title_policy.assert_not_called()
        window._save_state.assert_not_called()
        window._refresh_title_rules.assert_not_called()
        window._apply_settings_to_lib.assert_not_called()
        window._reload_current_view.assert_not_called()


if __name__ == '__main__':
    unittest.main()
