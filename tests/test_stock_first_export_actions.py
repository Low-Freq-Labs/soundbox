"""Hidden iPod actions distinguish a first Export from selected playlist Sync."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from desktop.stock_device_view import StockIpodPages
from soundbox.stock_ipod_format import ARTWORK_PROFILE, DATABASE_PROFILE, StockWriteFormat
from soundbox.stock_ipod_info import (
    StockIpodPlaylistEntry, StockIpodPlaylistSnapshot, StockIpodSnapshot,
)


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
        pages = StockIpodPages()
        self.addCleanup(pages.deleteLater)
        self.exports = []; self.syncs = []; self.deletions = []
        pages.export_requested.connect(lambda:self.exports.append('choose'))
        pages.sync_requested.connect(self.syncs.append)
        pages.delete_playlists_requested.connect(self.deletions.append)
        pages.set_snapshot(snapshot(*playlists, **changes))
        return pages

    def assert_export(self, pages):
        self.assertEqual(pages.sync_playlist_button.text(), 'Export playlists…')
        self.assertTrue(pages.sync_playlist_button.isEnabled())
        before = list(self.syncs)
        exports = list(self.exports)
        pages.sync_playlist_button.click()
        self.assertEqual(self.exports, [*exports, 'choose'])
        self.assertEqual(self.syncs, before)

    def test_empty_catalog_offers_first_export_without_selection(self):
        pages = self.pages()
        self.assertEqual(pages.playlist_tree.topLevelItemCount(), 0)
        self.assert_export(pages)

    def test_system_videos_only_offers_export_and_preserves_separate_delete(self):
        from PySide6.QtCore import Qt
        pages = self.pages(VIDEOS)
        item = pages.playlist_tree.topLevelItem(0)
        pages.playlist_tree.setCurrentItem(item)
        self.assertIsNone(pages._sync_selection())
        self.assert_export(pages)
        item.setCheckState(0, Qt.Checked)
        pages.delete_playlist_button.click()
        self.assertEqual(self.deletions, [(('5', 'Videos'),)])

    def test_unselected_manual_playlist_still_offers_an_additional_export(self):
        pages = self.pages(FAVORITES)
        self.assertIsNone(pages.playlist_tree.currentItem())
        self.assert_export(pages)

    def test_selected_manual_playlist_keeps_exact_sync_identity_and_raw_count(self):
        pages = self.pages(FAVORITES, VIDEOS)
        item = pages.playlist_tree.topLevelItem(0)
        self.assertEqual(item.childCount(), 0)  # Raw native membership is still one.
        pages.playlist_tree.setCurrentItem(item)
        self.assertEqual(pages.sync_playlist_button.text(), 'Sync playlist…')
        self.assertTrue(pages.sync_playlist_button.isEnabled())
        pages.sync_playlist_button.click()
        self.assertEqual(self.syncs, [(str(FAVORITES_ID), 'Favorites', 1)])
        self.assertEqual(self.exports, [])
        pages.playlist_tree.setCurrentItem(pages.playlist_tree.topLevelItem(1))
        self.assert_export(pages)

    def test_clearing_selection_or_deleted_playlist_snapshot_returns_to_export(self):
        for replace_catalog in (False, True):
            with self.subTest(replace_catalog=replace_catalog):
                pages = self.pages(FAVORITES, VIDEOS)
                pages.playlist_tree.setCurrentItem(pages.playlist_tree.topLevelItem(0))
                self.assertEqual(pages.sync_playlist_button.text(), 'Sync playlist…')
                if replace_catalog:pages.set_snapshot(snapshot(VIDEOS))
                else:pages.playlist_tree.setCurrentItem(None)
                self.assert_export(pages)

    def test_controller_gate_blocks_export_and_sync_then_restores_current_choice(self):
        pages = self.pages(FAVORITES, VIDEOS)
        for item in (None, pages.playlist_tree.topLevelItem(0)):
            with self.subTest(selected=item is not None):
                pages.playlist_tree.setCurrentItem(item)
                pages.set_playlist_actions_available(False, 'Finishing the current device operation')
                self.assertFalse(pages.sync_playlist_button.isEnabled())
                self.assertIn('current device operation', pages.sync_playlist_button.toolTip())
                pages.sync_playlist_button.click()
                self.assertEqual((self.exports, self.syncs), ([], []))
                pages.set_playlist_actions_available(True)
                self.assertTrue(pages.sync_playlist_button.isEnabled())
                self.assertEqual(pages.sync_playlist_button.text(),
                    'Sync playlist…' if item is not None else 'Export playlists…')

    def test_read_only_loading_and_failed_snapshot_never_offer_writes(self):
        pages = self.pages(VIDEOS, writable=False)
        pages.set_playlist_actions_available(True)
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        self.assertIn('read-only', pages.sync_playlist_button.toolTip())
        pages.sync_playlist_button.click()
        pages.set_snapshot(snapshot(VIDEOS))
        pages.set_loading()
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        pages.sync_playlist_button.click()
        pages.set_error('Generated read failure')
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        pages.sync_playlist_button.click()
        self.assertEqual((self.exports, self.syncs), ([], []))

    def test_preserved_snapshot_refresh_keeps_export_but_does_not_override_busy_gate(self):
        pages = self.pages(VIDEOS)
        pages.set_page(2)
        pages.set_loading(preserve_page=True)
        self.assertEqual(pages.stack.currentIndex(), 2)
        self.assert_export(pages)
        pages.set_playlist_actions_available(False, 'Wait for the current operation')
        pages.set_snapshot(snapshot(VIDEOS))
        self.assertFalse(pages.sync_playlist_button.isEnabled())
        self.assertEqual(pages.stack.currentIndex(), 2)

    def test_system_context_offers_export_and_replaced_snapshot_rejects_stale_action(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                pages = self.pages(VIDEOS)
                item = pages.playlist_tree.topLevelItem(0)
                def choose(menu, _position):
                    self.assertEqual(menu.actions()[0].text(), 'Export playlists…')
                    self.assertTrue(menu.actions()[0].isEnabled())
                    if stale:pages.set_snapshot(snapshot(FAVORITES))
                    menu.actions()[0].trigger()
                with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=item), \
                     mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
                    pages._playlist_context_menu(QPoint(1, 1))
                self.assertEqual(self.exports, [] if stale else ['choose'])
                self.assertEqual(self.syncs, [])

    def test_context_action_cannot_bypass_a_new_controller_busy_gate(self):
        for selected in (VIDEOS, FAVORITES):
            with self.subTest(selected=selected.title):
                pages = self.pages(selected)
                item = pages.playlist_tree.topLevelItem(0)
                def choose(menu, _position):
                    self.assertTrue(menu.actions()[0].isEnabled())
                    pages.set_playlist_actions_available(False, 'A different device operation started')
                    menu.actions()[0].trigger()
                with mock.patch.object(pages.playlist_tree, 'itemAt', return_value=item), \
                     mock.patch('desktop.stock_device_view.exec_transient_menu', side_effect=choose):
                    pages._playlist_context_menu(QPoint(1, 1))
                self.assertEqual((self.exports, self.syncs), ([], []))
