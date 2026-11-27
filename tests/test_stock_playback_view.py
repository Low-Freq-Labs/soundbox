"""Device history refresh preserves the current UI and exact byte facts."""
from dataclasses import replace
from decimal import Decimal
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from desktop.app import MainWindow
from desktop.stock_device_view import CompositeDeviceData, StockIpodPages
from soundbox.manifest import COMPOSITE_EXPORT_PROFILE
from soundbox.stock_ipod_format import ARTWORK_PROFILE, DATABASE_PROFILE, StockWriteFormat
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

    def test_equal_snapshot_delivery_preserves_live_items_and_controller_bookkeeping(self):
        w = self.win; pages = self.pages
        old = w._stock_snapshot
        root = self.tree.topLevelItem(0)
        root.setCheckState(0, Qt.Checked)
        pages.music_table.setCurrentIndex(pages.music_model.index(0, 0))
        pages.music_table.setColumnWidth(1, 321)
        width = pages.music_table.columnWidth(1)
        seat = self.case.seat(pages, self.tree)
        revision = pages._sync_revision
        reset = QSignalSpy(pages.music_model.modelReset)
        with mock.patch.object(w, '_restore_stock_device_view',
                               side_effect=AssertionError('Overwrote live seat')), \
                mock.patch.object(w, '_update_device_action_state') as actions, \
                mock.patch.object(w, '_schedule_stock_artwork') as artwork:
            MainWindow._on_stock_device_view(w, w._current_device_path, (replace(old), None))
        self.assertEqual(reset.count(), 0)
        self.assertIs(self.tree.topLevelItem(0), root)
        self.assertEqual(self.case.seat(pages, self.tree), seat)
        self.assertEqual(root.checkState(0), Qt.Checked)
        self.assertEqual(pages._sync_revision, revision)
        self.assertEqual(pages.music_table.columnWidth(1), width)
        self.assertEqual(actions.call_count, 2)
        artwork.assert_called_once()

    def test_collection_to_cached_stock_device_reuses_complete_tree(self):
        root = self.tree.topLevelItem(0)
        root.setCheckState(0, Qt.Checked)
        self.win._show_collection()
        reset = QSignalSpy(self.pages.music_model.modelReset)
        item = self.win.device_tree.currentItem()
        self.win._on_device_click(item, 0)
        self.assertEqual(reset.count(), 0)
        self.assertIs(self.tree.topLevelItem(0), root)
        self.assertEqual(root.checkState(0), Qt.Checked)

    def test_status_patch_tracks_complete_presentation_values(self):
        old = self.win._stock_snapshot
        self.pages.set_sync_status('changed', 'Fresh prepared status', 7, 'Now')
        fresh = replace(old, sync_state='changed', sync_detail='Fresh prepared status',
                        sync_local_changes=7, sync_committed_at='Now')
        self.assertTrue(self.pages.set_snapshot(fresh))
        self.assertFalse(self.pages.set_snapshot(old))
        tree = self.pages.detail_trees['SoundBox']
        values = {tree.topLevelItem(i).text(0): tree.topLevelItem(i).text(1)
                  for i in range(tree.topLevelItemCount())}
        self.assertEqual(values['Sync detail'], old.sync_detail)

    def test_playlist_order_change_rebuilds_before_first_complete_frame(self):
        old = self.win._stock_snapshot
        second = replace(old.playlists[0], playlist_id=100, title='Second set')
        before = replace(old, playlists=(*old.playlists, second))
        self.pages.set_snapshot(before)
        after = replace(before, playlists=tuple(reversed(before.playlists)))
        self.assertFalse(self.pages.set_snapshot(after))
        self.assertEqual([self.tree.topLevelItem(i).text(0) for i in range(2)],
                         ['Second set', 'Device set'])

    def test_complete_subtrees_attach_without_per_child_action_scans(self):
        old = self.win._stock_snapshot
        playlists = tuple(replace(old.playlists[0], playlist_id=100+i,
            title=f'Generated set {i}', entries=old.playlists[0].entries * 100)
            for i in range(40))
        fresh = replace(old, playlists=playlists, filesystem='MS-DOS FAT32',
            stock_format=StockWriteFormat(DATABASE_PROFILE, ARTWORK_PROFILE))
        changes = QSignalSpy(self.tree.itemChanged)
        self.assertFalse(self.pages.set_snapshot(fresh))
        self.assertEqual(self.tree.topLevelItemCount(), 40)
        self.assertEqual(sum(self.tree.topLevelItem(i).childCount()
                             for i in range(40)), 4000)
        # Only final parent drag-readiness changes may signal; child role and
        # flag preparation must never notify the attached catalog's handlers.
        self.assertLessEqual(changes.count(), 40)
        for i in range(40):
            child = self.tree.topLevelItem(i).child(99)
            self.assertEqual(child.data(0, Qt.UserRole + 4),
                             (old.tracks[0].dbid, '/iPod_Control/Music/F00/song.wav'))
        before = changes.count()
        self.assertTrue(self.pages.set_snapshot(replace(fresh)))
        self.assertEqual(changes.count(), before)
        self.assertFalse(self.pages.delete_playlist_button.isEnabled())
        self.tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.assertEqual(changes.count(), before+1)
        self.assertTrue(self.pages.delete_playlist_button.isEnabled())
        self.assertEqual(self.pages.delete_playlist_button.text(), 'Delete checked playlist…')

    def test_same_catalog_hash_does_not_hide_changed_complete_snapshot_values(self):
        old = self.win._stock_snapshot
        changes = (
            dict(writable=False), dict(filesystem='Unknown'),
            dict(identity_conflict=True), dict(model_source='Remembered: receipt'),
            dict(stock_format=StockWriteFormat(DATABASE_PROFILE, ARTWORK_PROFILE)),
            dict(playback_status='Fresh history', pending_playback_records=3),
            dict(tracks=(replace(old.tracks[0], play_count=7),)),
            dict(sync_state='changed', sync_detail='Fresh result', sync_local_changes=4),
            dict(artwork_database_sha256='new-art'), dict(free_bytes=123),
            dict(tracks=(replace(old.tracks[0], location=':iPod_Control:Music:F00:new.wav'),)),
            dict(playlists=(replace(old.playlists[0], entries=()),)),
            dict(playlists=(replace(old.playlists[0], title='Reordered'), old.playlists[0])),
        )
        for change in changes:
            with self.subTest(change=change):
                self.pages.set_snapshot(old)
                fresh = replace(old, **change)
                self.assertEqual(old.database_sha256, fresh.database_sha256)
                reset = QSignalSpy(self.pages.music_model.modelReset)
                self.assertFalse(self.pages.set_snapshot(fresh))
                self.assertEqual(reset.count(), 1)
                self.assertTrue(self.pages.set_snapshot(replace(fresh)))

    def test_composite_cache_is_detached_from_nested_mutable_inputs(self):
        old = self.win._stock_snapshot
        path = '/iPod_Control/Music/F00/song.wav'
        composite = CompositeDeviceData(dict(schema=3, profile=COMPOSITE_EXPORT_PROFILE,
            files={path: dict(kind='shared-stock-media', dbid=1)},
            playlists={'one': dict(name='Device set', tracks=['1'])},
            playlist_order=['one']), {'one': (path,)})
        self.assertFalse(self.pages.set_snapshot(old, composite))
        self.assertTrue(self.pages.set_snapshot(replace(old), replace(composite)))
        composite.manifest['playlists']['one']['name'] = 'New name'
        self.assertFalse(self.pages.set_snapshot(old, composite))
        self.assertEqual(self.tree.topLevelItem(0).text(0), 'New name')
        composite.playlist_entries['one'] = ()
        self.assertFalse(self.pages.set_snapshot(old, composite))
        self.assertEqual(self.tree.topLevelItem(0).childCount(), 0)
        for fresh in (replace(composite, rockbox_database_auto_update=True),
                      replace(composite, rockbox_database_refresh_pending=True),
                      replace(composite, rockbox_database_files=4),
                      replace(composite, changed_playlist_keys=frozenset({'one'}))):
            self.pages.set_snapshot(old, composite)
            self.assertFalse(self.pages.set_snapshot(old, fresh))

    def test_loading_error_and_pending_order_require_complete_republication(self):
        old = self.win._stock_snapshot
        for method, options in ((self.pages.set_loading, dict(preserve_page=True)),
                                (self.pages.set_loading, {}),
                                (lambda **kw: self.pages.set_error('Generated failure', **kw),
                                 dict(preserve_snapshot=True)),
                                (lambda **kw: self.pages.set_error('Generated failure', **kw), {})):
            self.pages.set_snapshot(old)
            method(**options)
            self.assertFalse(self.pages.set_snapshot(old))
            self.assertTrue(self.pages.set_snapshot(old))
        self.pages._pending_playlist_order = ('99',)
        self.pages._pending_playlist_order_token = ('generated',)
        self.assertFalse(self.pages.set_snapshot(old))
        self.assertIsNone(self.pages._pending_playlist_order)

    def test_reuse_reapplies_readiness_before_controller_profile_gating(self):
        ready = replace(self.win._stock_snapshot, filesystem='MS-DOS FAT32',
            stock_format=StockWriteFormat(DATABASE_PROFILE, ARTWORK_PROFILE))
        self.pages.set_snapshot(ready)
        self.assertTrue(self.pages._playlist_delete_available)
        self.pages.set_playlist_deletion_available(False)
        self.pages.set_playlist_actions_available(False, 'Busy')
        self.assertTrue(self.pages.set_snapshot(replace(ready)))
        self.assertTrue(self.pages._playlist_delete_available)
        self.assertFalse(self.pages.sync_playlist_button.isEnabled())
        self.assertEqual(self.pages.sync_playlist_button.toolTip(), 'Busy')

    def test_compatible_playback_patch_retains_complete_value_reuse(self):
        fresh = replace(self.win._stock_snapshot, play_counts_sha256='fresh-history',
                        tracks=(replace(self.win._stock_snapshot.tracks[0], play_count=3),))
        self.assertTrue(self.pages.set_playback_snapshot(fresh))
        self.assertTrue(self.pages.set_snapshot(replace(fresh)))
        # A direct model-compatible patch cannot bless unrelated identity changes.
        changed = replace(fresh, identity_conflict=True)
        self.assertTrue(self.pages.set_playback_snapshot(changed))
        self.assertFalse(self.pages.set_snapshot(changed))

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
