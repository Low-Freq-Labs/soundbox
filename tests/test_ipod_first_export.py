"""First-export actions over cached TEMP iPod pages, without device/app input."""
from dataclasses import replace
import unittest
from unittest import mock

from PySide6.QtCore import QPoint

from desktop.stock_catalog_sync import default_sync_selection
from soundbox.stock_ipod_catalog_state import StockCatalogWitness
from soundbox.stock_ipod_playlist_binding import PlaylistBinding
from soundbox.stock_ipod_info import StockIpodPlaylistSnapshot
from tests import test_ipod_artwork_ui as fixture


class IpodFirstExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.IpodArtworkAdmission.setUpClass()

    def setUp(self):
        self.case = fixture.IpodArtworkAdmission()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.w = self.case.window
        profile = self.w._device_profiles[self.case.device]
        profile.filesystem = 'msdos'
        profile.stock_format = self.case.snapshot.stock_format
        self.chooser = self.enterContext(mock.patch.object(self.w, '_show_export_dialog'))

    def witness(self):
        w = self.w
        return StockCatalogWitness((str(w.lib.source), w.lib.source_kind, w.lib.source_library_id),
            self.case.snapshot.identity_key, 'a' * 64, (), bindings=(PlaylistBinding(
                'native-favorites', 'native-favorites', 'playlist-uuid', fixture.PLAYLIST_ID,
                'Favorites', (('1', 1), ('2', 2))),))

    def empty(self):
        snapshot = replace(self.case.snapshot, tracks=(), playlists=(
            StockIpodPlaylistSnapshot(15, 'Videos', 'Smart playlist', True, ()),))
        self.w._stock_view_cache[self.case.device] = (snapshot, None)
        self.w._stock_snapshot = snapshot
        self.w.stock_ipod_pages.set_snapshot(snapshot)
        self.w._update_device_action_state()
        return snapshot

    def test_deleted_last_playlist_with_old_witness_offers_export_in_both_places(self):
        w = self.w
        w._stock_catalog_witness_cache[self.case.device] = self.witness()
        with mock.patch.object(w.lib, 'resolve', side_effect=AssertionError('Header scanned songs')):
            self.empty()
            self.assertFalse(w._stock_has_sync_source(self.case.device))
        self.assertEqual(w.device_sync_btn.text(), 'Export')
        self.assertTrue(w.device_sync_btn.isEnabled())
        self.assertEqual(w.stock_ipod_pages.sync_playlist_button.text(), 'Export')
        w.device_sync_btn.click()
        w.stock_ipod_pages.sync_playlist_button.click()
        self.assertEqual(self.chooser.call_args_list, [mock.call(self.case.device)] * 2)
        self.case.plan_stock.assert_not_called()
        self.case.commit_stock.assert_not_called()

    def test_explicit_sync_on_unassociated_device_opens_export_without_a_plan_or_shadow_read(self):
        self.empty()
        with (mock.patch.object(self.w, '_begin_stock_ipod_sync') as begin,
              mock.patch.object(self.w.stock_ipod_shadow_store, 'load',
                                side_effect=AssertionError('GUI read the device shadow'))):
            self.w._sync_device(self.case.device)
        self.chooser.assert_called_once_with(self.case.device)
        begin.assert_not_called()

    def test_existing_bound_playlist_keeps_sync_after_name_and_count_changes(self):
        w = self.w
        witness = self.witness()
        w._stock_catalog_witness_cache[self.case.device] = witness
        source = w.lib.playlists[0]
        source.name = 'Renamed Favorites'
        source.keys.append('1')
        self.assertEqual((source.pid, w.lib.source_playlist_catalog['nodes'][0]['UUID']),
                         (witness.bindings[0].native_id, witness.bindings[0].uuid))
        self.assertEqual((source.name, source.keys), ('Renamed Favorites', ['1', '2', '1']))
        self.assertTrue(w._stock_has_sync_source(self.case.device))
        self.assertEqual(default_sync_selection(w, self.case.device),
                         ((str(witness.bindings[0].device_playlist_id), 'Favorites', 2),))
        # The header consumes a completed pending observation; it does not scan
        # the changed playlist to derive drift during this button update.
        w._drift_cache[self.case.device] = 2
        with mock.patch('desktop.stock_catalog_sync.request_device_sync') as sync:
            w._update_device_action_state()
            self.assertEqual(w.device_sync_btn.text(), 'Sync')
            self.assertEqual(w.stock_ipod_pages.sync_playlist_button.text(), 'Sync')
            self.assertTrue(w.device_sync_btn.isEnabled())
            w.device_sync_btn.click()
            w.stock_ipod_pages.sync_playlist_button.click()
        self.assertEqual(sync.call_args_list, [mock.call(w, self.case.device)] * 2)
        self.chooser.assert_not_called()

    def test_clean_bound_playlist_offers_same_export_in_both_positions(self):
        w = self.w
        witness = self.witness()
        w._stock_catalog_witness_cache[self.case.device] = witness
        w._drift_cache[self.case.device] = 0
        self.assertTrue(w._stock_has_sync_source(self.case.device))
        self.assertEqual(default_sync_selection(w, self.case.device),
                         ((str(witness.bindings[0].device_playlist_id), 'Favorites', 2),))
        with mock.patch('desktop.stock_catalog_sync.request_device_sync') as sync:
            w._update_device_action_state()
            self.assertEqual(w.device_sync_btn.text(), 'Export')
            self.assertTrue(w.device_sync_btn.isEnabled())
            self.assertEqual(w.stock_ipod_pages.sync_playlist_button.text(), 'Export')
            self.assertTrue(w.stock_ipod_pages.sync_playlist_button.isEnabled())
            w.device_sync_btn.click()
            w.stock_ipod_pages.sync_playlist_button.click()
        sync.assert_not_called()
        self.assertEqual(self.chooser.call_args_list, [mock.call(self.case.device)] * 2)
        self.case.plan_stock.assert_not_called()
        self.case.commit_stock.assert_not_called()
        self.assertIs(w._stock_catalog_witness_cache[self.case.device], witness)

    def test_busy_read_only_and_missing_library_disable_both_export_actions(self):
        w = self.w
        self.empty()
        w._exporting = True
        w._update_device_action_state()
        self.assertFalse(w.device_sync_btn.isEnabled())
        self.assertFalse(w.stock_ipod_pages.sync_playlist_button.isEnabled())
        w._exporting = False
        self.case.facts = replace(self.case.facts, writable=False)
        w._update_device_action_state()
        self.assertFalse(w.device_sync_btn.isEnabled())
        self.assertFalse(w.stock_ipod_pages.sync_playlist_button.isEnabled())
        self.assertTrue(w.eject_btn.isEnabled())
        self.case.facts = replace(self.case.facts, writable=True)
        library = w.lib
        try:
            w.lib = None
            w._update_device_action_state()
            self.assertFalse(w.device_sync_btn.isEnabled())
            self.assertFalse(w.stock_ipod_pages.sync_playlist_button.isEnabled())
            self.assertTrue(w.eject_btn.isEnabled())
        finally:
            w.lib = library
            w._update_device_action_state()
        self.assertTrue(w.device_sync_btn.isEnabled())
        self.assertTrue(w.stock_ipod_pages.sync_playlist_button.isEnabled())
        self.chooser.assert_not_called()

    def test_device_menu_keeps_explicit_export_and_omits_sync_for_system_only_catalog(self):
        self.empty()
        item = self.w.device_tree.currentItem()
        def choose(menu, _point):
            actions = {action.text(): action for action in menu.actions()}
            self.assertIn('Export playlists…', actions)
            self.assertFalse(any(text.startswith('Sync') for text in actions))
            actions['Export playlists…'].trigger()
        with (mock.patch.object(self.w.device_tree, 'itemAt', return_value=item),
              mock.patch('desktop.app.exec_transient_menu', side_effect=choose)):
            self.w._device_menu(QPoint(0, 0))
        self.chooser.assert_called_once_with(self.case.device)

    def test_stale_header_hint_still_falls_back_to_selection_when_no_actual_match_remains(self):
        self.empty()
        with mock.patch.object(self.w, '_stock_has_sync_source', return_value=True):
            self.w._sync_device(self.case.device)
        self.chooser.assert_called_once_with(preferred=self.case.device)
        self.case.information.assert_not_called()
        self.case.plan_stock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
