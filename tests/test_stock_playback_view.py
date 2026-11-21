"""Device history refresh preserves the current UI and exact byte facts."""
from dataclasses import replace
from decimal import Decimal
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from desktop.app import MainWindow
from desktop.stock_device_view import StockIpodPages
from soundbox.stock_ipod_info import playback_refresh_compatible
from tests import test_device_navigation_publication as fixture


class StockPlaybackView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DeviceNavigationPublication.setUpClass()

    def setUp(self):
        self.case = fixture.DeviceNavigationPublication()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.win = self.case.window
        _item, self.pages, self.tree = self.case.select_device('stock_ipod')

    def test_history_publication_patches_totals_without_resetting_current_interaction(self):
        w = self.win; pages = self.pages
        old = w._stock_snapshot
        root = self.tree.topLevelItem(0)
        root.setCheckState(0, Qt.Checked)
        pages.music_table.setCurrentIndex(pages.music_model.index(0, 0))
        song_index = pages.music_table.currentIndex()
        seat = self.case.seat(pages, self.tree)
        revision = pages._sync_revision
        fresh = replace(old, play_counts_sha256='a' * 64,
            tracks=tuple(replace(row, play_count=row.play_count + 3, skip_count=row.skip_count + 2,
                rating=80, bookmark_time=12000, pending_plays=3, pending_skips=2) for row in old.tracks),
            pending_playback_records=1, playback_status='1 pending playback record(s)',
            sync_state='up_to_date', sync_local_changes=0)
        reset = QSignalSpy(pages.music_model.modelReset)
        changed = QSignalSpy(pages.music_model.dataChanged)
        with mock.patch.object(pages, 'set_snapshot', side_effect=AssertionError('History reset the device views')):
            MainWindow._on_stock_device_view(w, w._current_device_path, (fresh, None))
        self.assertEqual(self.case.seat(pages, self.tree), seat)
        self.assertEqual(root.checkState(0), Qt.Checked)
        self.assertEqual(pages._sync_revision, revision)
        self.assertEqual(pages.music_table.currentIndex(), song_index)
        self.assertEqual((reset.count(), changed.count()), (0, 1))
        self.assertEqual(pages.music_model.data(pages.music_model.index(0, 5)), '3')
        self.assertEqual(pages.music_model.data(pages.music_model.index(0, 6)), '2')
        self.assertEqual(w._stock_snapshot.tracks[0].bookmark_time, 12000)

    def test_fast_path_refuses_catalog_or_track_identity_changes(self):
        old = self.win._stock_snapshot
        for fresh in (replace(old, database_sha256='b' * 64),
                      replace(old, tracks=tuple(replace(row, title='Changed title') for row in old.tracks)),
                      replace(old, playlists=())):
            with self.subTest(fresh=fresh):
                self.assertFalse(playback_refresh_compatible(old, fresh))
        fresh = replace(old, tracks=tuple(replace(row, title='Changed') for row in old.tracks))
        self.assertFalse(self.pages.set_playback_snapshot(fresh))

    def test_storage_totals_use_common_decimal_units_on_initial_and_history_refresh(self):
        pages = StockIpodPages(); self.addCleanup(pages.deleteLater)
        snapshot = replace(self.win._stock_snapshot, capacity_bytes=1_024_300_000_000,
                           free_bytes=1_000_000_000_000)
        pages.set_snapshot(snapshot)
        def values():
            tree = pages.detail_trees['Storage']
            return {tree.topLevelItem(i).text(0): tree.topLevelItem(i).text(1)
                    for i in range(tree.topLevelItemCount())}
        shown = values()
        self.assertEqual([shown[key] for key in ('Capacity', 'Used', 'Free')],
                         ['1,024.3 GB', '24.3 GB', '1,000.0 GB'])
        current = replace(snapshot, free_bytes=snapshot.free_bytes - 160_000_000,
                          play_counts_sha256='c' * 64)
        self.assertTrue(pages.set_playback_snapshot(current))
        shown = values()
        self.assertEqual([shown[key] for key in ('Capacity', 'Used', 'Free')],
                         ['1,024.3 GB', '24.5 GB', '999.8 GB'])
        tree = pages.detail_trees['Storage']
        free_row = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                        if tree.topLevelItem(i).text(0) == 'Free')
        self.assertEqual(free_row.toolTip(1), f'{current.free_bytes:,} bytes')
        number = lambda key: Decimal(shown[key].split()[0].replace(',', ''))
        self.assertEqual(number('Capacity'), number('Used') + number('Free'))
        self.assertEqual(snapshot.capacity_bytes, current.capacity_bytes)
        self.assertEqual({shown[key].split()[1] for key in ('Capacity', 'Used', 'Free')}, {'GB'})
