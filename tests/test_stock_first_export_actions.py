"""iPod primary actions mirror prepared Export/Sync state without row targeting."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from desktop.stock_device_view import StockIpodPages
from soundbox.stock_ipod_format import ARTWORK_PROFILE, DATABASE_PROFILE, StockWriteFormat
from soundbox.stock_ipod_info import StockIpodPlaylistEntry, StockIpodPlaylistSnapshot, StockIpodSnapshot

FAVORITES_ID = 10_469_178_251_650_046_358
FAVORITES = StockIpodPlaylistSnapshot(FAVORITES_ID, 'Favorites', 'Playlist', False,
    (StockIpodPlaylistEntry(7, 'A manually transferred song', 'Artist'),))
VIDEOS = StockIpodPlaylistSnapshot(5, 'Videos', 'Smart playlist', True, ())


def snapshot(*playlists, **changes):
    current = StockIpodSnapshot(
        target=Path('/generated/no-device-io'), volume_name='Fixture iPod',
        volume_uuid='generated-uuid', filesystem='MS-DOS FAT32', protocol='USB',
        device_identifier='generated-device', writable=True, capacity_bytes=1000,
        free_bytes=1000, model='MA446', model_source='generated', family='iPod Video',
        generation='5.5', identity_key='generated-identity', database_id=1,
        library_persistent_id=2, database_version=0x75, checksum_scheme=0,
        database_sha256='generated', media_bytes=0, pending_playback_records=0,
        playback_status='', recovery_count=0, tracks=(), playlists=tuple(playlists),
        stock_format=StockWriteFormat(DATABASE_PROFILE, ARTWORK_PROFILE), usb_family='iPod Video')
    return replace(current, **changes)


class StockFirstExportActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pages(self, *playlists, **changes):
        pages = StockIpodPages(); self.addCleanup(pages.deleteLater)
        self.actions = []; self.deletions = []
        pages.primary_action_requested.connect(lambda:self.actions.append('primary'))
        pages.delete_playlists_requested.connect(self.deletions.append)
        pages.set_snapshot(snapshot(*playlists, **changes))
        return pages

    def assert_action(self, pages, caption='Export'):
        self.assertEqual(pages.sync_playlist_button.text(), caption)
        self.assertTrue(pages.sync_playlist_button.isEnabled())
        before = list(self.actions); pages.sync_playlist_button.click()
        self.assertEqual(self.actions, [*before, 'primary'])

    def test_empty_catalog_offers_first_export_without_selection(self):
        pages = self.pages(); self.assertEqual(pages.playlist_tree.topLevelItemCount(), 0)
        self.assert_action(pages)

    def test_system_videos_only_offers_export_and_preserves_separate_delete(self):
        pages = self.pages(VIDEOS); item = pages.playlist_tree.topLevelItem(0)
        pages.playlist_tree.setCurrentItem(item); self.assert_action(pages)
        item.setCheckState(0, Qt.Checked); pages.delete_playlist_button.click()
        self.assertEqual(self.deletions, [(('5', 'Videos'),)])

    def test_unselected_manual_playlist_still_offers_an_additional_export(self):
        pages = self.pages(FAVORITES)
        self.assertIsNone(pages.playlist_tree.currentItem()); self.assert_action(pages)

    def test_hidden_current_row_never_changes_clean_export_into_playlist_update(self):
        pages = self.pages(FAVORITES, VIDEOS); item = pages.playlist_tree.topLevelItem(0)
        self.assertEqual(item.childCount(), 0)
        pages.playlist_tree.setCurrentItem(item)
        self.assertEqual(pages.playlist_tree.selectedItems(), [])
        self.assertEqual(item.data(0, Qt.UserRole + 1), str(FAVORITES_ID))
        self.assert_action(pages)

    def test_successful_snapshot_and_restored_current_row_return_to_export(self):
        pages = self.pages(FAVORITES)
        pages.set_primary_action('Sync', True); self.assert_action(pages, 'Sync')
        pages.set_snapshot(snapshot(FAVORITES)); pages.set_primary_action('Export', True)
        pages.playlist_tree.setCurrentItem(pages.playlist_tree.topLevelItem(0))
        self.assert_action(pages)

    def test_context_uses_same_primary_action_without_capturing_clicked_identity(self):
        for caption in ('Export', 'Sync'):
            pages = self.pages(FAVORITES); pages.set_primary_action(caption, True)
            item = pages.playlist_tree.topLevelItem(0)
            def choose(menu, _position):
                self.assertEqual(menu.actions()[0].text(), caption); menu.actions()[0].trigger()
            before = len(self.actions)
            with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=item), \
                    mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
                pages._playlist_context_menu(QPoint(1, 1))
            self.assertEqual(len(self.actions), before + 1)

    def test_row_changes_do_not_change_controller_owned_pending_action(self):
        pages = self.pages(FAVORITES, VIDEOS); pages.set_primary_action('Sync', True)
        for item in (pages.playlist_tree.topLevelItem(0), pages.playlist_tree.topLevelItem(1), None):
            pages.playlist_tree.setCurrentItem(item); self.assert_action(pages, 'Sync')

    def test_controller_gate_blocks_both_actions_then_restores_exact_caption(self):
        for caption in ('Export', 'Sync'):
            pages = self.pages(FAVORITES); pages.set_primary_action(caption, False, 'Finishing the current device operation')
            self.assertFalse(pages.sync_playlist_button.isEnabled())
            self.assertIn('current device operation', pages.sync_playlist_button.toolTip())
            pages.sync_playlist_button.click(); self.assertEqual(self.actions, [])
            pages.set_primary_action(caption, True); self.assert_action(pages, caption)

    def test_read_only_loading_and_failed_snapshot_never_offer_writes(self):
        pages = self.pages(VIDEOS, writable=False); pages.set_primary_action('Export', True)
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        self.assertIn('read-only', pages.sync_playlist_button.toolTip()); pages.sync_playlist_button.click()
        pages.set_snapshot(snapshot(VIDEOS)); pages.set_loading(); pages.sync_playlist_button.click()
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        pages.set_error('Generated read failure'); pages.sync_playlist_button.click()
        self.assertFalse(pages.sync_playlist_button.isEnabled()); self.assertEqual(self.actions, [])

    def test_preserved_snapshot_refresh_does_not_override_busy_gate(self):
        pages = self.pages(VIDEOS); pages.set_page(2); pages.set_loading(preserve_page=True)
        self.assertEqual(pages.stack.currentIndex(), 2); self.assert_action(pages)
        pages.set_primary_action('Sync', False, 'Wait for the current operation')
        pages.set_snapshot(snapshot(VIDEOS))
        self.assertFalse(pages.sync_playlist_button.isEnabled()); self.assertEqual(pages.stack.currentIndex(), 2)

    def test_context_rejects_replaced_snapshot_or_changed_primary_caption(self):
        for change in ('snapshot', 'caption'):
            pages = self.pages(VIDEOS); item = pages.playlist_tree.topLevelItem(0)
            def choose(menu, _position):
                if change == 'snapshot':pages.set_snapshot(snapshot(FAVORITES))
                else:pages.set_primary_action('Sync', True)
                menu.actions()[0].trigger()
            with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=item), \
                    mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
                pages._playlist_context_menu(QPoint(1, 1))
            self.assertEqual(self.actions, [])

    def test_context_action_cannot_bypass_new_controller_busy_gate(self):
        pages = self.pages(FAVORITES); item = pages.playlist_tree.topLevelItem(0)
        def choose(menu, _position):
            pages.set_primary_action('Export', False, 'A different device operation started')
            menu.actions()[0].trigger()
        with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=item), \
                mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
            pages._playlist_context_menu(QPoint(1, 1))
        self.assertEqual(self.actions, [])
