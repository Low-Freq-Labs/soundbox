"""Device parent drags save one exact order without retargeting songs or Videos."""
from dataclasses import replace
import json
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTreeWidgetItem

from desktop.stock_device_view import CompositeDeviceData, StockIpodPages
from soundbox.manifest import COMPOSITE_EXPORT_PROFILE
from soundbox.stock_ipod_info import StockIpodTrackSnapshot
from tests.test_stock_first_export_actions import FAVORITES, VIDEOS, snapshot


class Drop:
    def __init__(self, tree, mime, position, *, source=None):
        self.tree, self.mime, self.point = tree, mime, QPointF(position)
        self.origin = tree if source is None else source
        self.accepted = False

    def source(self):return self.origin
    def mimeData(self):return self.mime
    def position(self):return self.point
    def acceptProposedAction(self):self.accepted = True
    def ignore(self):self.accepted = False


class DevicePlaylistDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pages = StockIpodPages(); self.tree = self.pages.playlist_tree
        self.saved = []; self.pages.playlist_order_requested.connect(self.saved.append)
        self.first = replace(FAVORITES, playlist_id=2**63 + 17, title='First')
        self.second = replace(FAVORITES, playlist_id=22, title='Second')
        self.third = replace(FAVORITES, playlist_id=33, title='Third')
        self.snapshot = snapshot(self.first, self.second, self.third, VIDEOS)
        self.pages.set_snapshot(self.snapshot)
        self.pages.set_page(2); self.pages.resize(650, 500); self.pages.show()
        self.app.processEvents()
        self.addCleanup(self.pages.deleteLater); self.addCleanup(self.pages.close)

    def drop(self, source, target, *, after=False, mime=None):
        rect = self.tree.visualItemRect(target)
        event = Drop(self.tree, mime or self.tree._order_mime(source),
                     QPoint(rect.left() + 100, rect.bottom() - 1 if after else rect.top() + 1))
        self.tree.dropEvent(event)
        return event

    def test_stock_drop_uses_unsigned_native_ids_and_auto_saves_once_without_buttons(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        names = [button.text() for button in self.pages.findChildren(QPushButton)]
        self.assertFalse({'Move up', 'Move down', 'Apply order…'} & set(names))
        self.assertTrue(self.drop(first, second, after=True).accepted)
        order = ('22', str(2**63 + 17), '33')
        self.assertEqual(self.saved, [order]); self.assertEqual(self.pages._managed_playlist_order(), order)
        self.assertFalse(self.pages._playlist_order_ready())
        self.assertFalse(self.drop(second, first, after=True).accepted)
        self.assertEqual(self.saved, [order])

    def test_reorder_and_refusal_preserve_checks_expansion_song_current_and_system_slots(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        videos = self.tree.takeTopLevelItem(3); self.tree.insertTopLevelItem(1, videos)
        song = QTreeWidgetItem(first, ['A selected song'])
        first.setExpanded(True); first.setCheckState(0, Qt.Checked)
        self.tree.setCurrentItem(song); song.setSelected(True)
        self.app.processEvents()
        self.assertTrue(self.drop(first, second, after=True).accepted)
        self.assertIs(self.tree.topLevelItem(1), videos)
        self.assertTrue(first.isExpanded()); self.assertEqual(first.checkState(0), Qt.Checked)
        self.assertIs(self.tree.currentItem(), song); self.assertEqual(self.tree.selectedItems(), [song])
        self.pages.restore_playlist_order()
        self.assertEqual(self.pages._managed_playlist_order(), (str(2**63 + 17), '22', '33'))
        self.assertIs(self.tree.topLevelItem(1), videos)
        self.assertIs(self.tree.currentItem(), song); self.assertEqual(self.tree.selectedItems(), [song])
        self.assertTrue(self.pages._playlist_order_ready())

    def test_system_song_external_ambiguous_busy_and_stale_drags_never_save(self):
        first, second, videos = self.tree.topLevelItem(0), self.tree.topLevelItem(1), self.tree.topLevelItem(3)
        self.assertFalse(self.drop(first, videos).accepted)
        self.assertFalse(self.drop(videos, first).accepted)
        song = QTreeWidgetItem(first, ['Song']); first.setExpanded(True)
        self.assertFalse(self.drop(song, second).accepted)
        mime = self.tree._order_mime(first)
        event = Drop(self.tree, mime, self.tree.visualItemRect(second).center(), source=object())
        self.tree.dropEvent(event); self.assertFalse(event.accepted)
        self.pages.set_playlist_actions_available(False, 'Busy')
        self.assertFalse(self.drop(first, second).accepted)
        self.pages.set_playlist_actions_available(True)
        self.assertFalse(self.drop(first, second, mime=mime).accepted)
        duplicate = QTreeWidgetItem(self.tree, ['Duplicate'])
        duplicate.setData(0, Qt.UserRole + 3, '22')
        self.assertFalse(self.drop(first, second).accepted)
        self.assertEqual(self.saved, [])

    def test_snapshot_refresh_invalidates_a_drag_and_settles_a_published_order(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        mime = self.tree._order_mime(first)
        self.pages.set_snapshot(self.snapshot)
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        self.assertFalse(self.drop(first, second, mime=mime).accepted)
        self.drop(first, second, after=True)
        self.pages.set_snapshot(snapshot(self.second, self.first, self.third, VIDEOS))
        self.pages.restore_playlist_order()
        self.assertEqual(self.pages._managed_playlist_order(), ('22', str(2**63 + 17), '33'))
        self.assertTrue(self.pages._playlist_order_ready())

    def test_fresh_order_snapshot_preserves_exact_song_checks_and_expansion_after_positional_restore(self):
        track = StockIpodTrackSnapshot(1, 7, 'Song', 'Artist', '', '', 'WAV', 100, 1000, 1411,
                                      44100, 0, 0, 0, 0, 0, ':iPod_Control:Music:Song.wav', 0, 0)
        original = snapshot(self.first, self.second, self.third, VIDEOS, tracks=(track,))
        self.pages.set_snapshot(original)
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        first.setCheckState(0, Qt.Checked); first.setExpanded(True)
        self.tree.setCurrentItem(first.child(0)); first.child(0).setSelected(True)
        self.app.processEvents()
        self.drop(first, second, after=True)
        fresh = replace(original, playlists=(self.second, self.first, self.third, VIDEOS))
        self.pages.set_snapshot(fresh)
        moved = self.tree.topLevelItem(1)
        self.assertEqual(moved.checkState(0), Qt.Checked); self.assertTrue(moved.isExpanded())
        self.assertIs(self.tree.currentItem(), moved.child(0))
        # The controller's older saved row positions are reapplied first.
        self.tree.setCurrentItem(self.tree.topLevelItem(0)); moved.setExpanded(False)
        self.pages.restore_playlist_order_seat()
        self.assertIs(self.tree.currentItem(), moved.child(0))
        self.assertEqual(self.tree.selectedItems(), [moved.child(0)]); self.assertTrue(moved.isExpanded())

    def test_equivalent_drop_is_a_noop_and_blank_end_drop_keeps_videos_anchored(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        self.assertFalse(self.drop(first, second).accepted); self.assertEqual(self.saved, [])
        event = Drop(self.tree, self.tree._order_mime(first), QPoint(100, self.tree.viewport().height() - 2))
        self.tree.dropEvent(event)
        self.assertTrue(event.accepted)
        self.assertEqual(self.saved, [('22', '33', str(2**63 + 17))])
        self.assertEqual(self.tree.topLevelItem(3).text(0), 'Videos')

    def test_composite_payload_uses_source_keys_and_foreign_rows_are_not_draggable(self):
        composite = CompositeDeviceData(dict(schema=3, profile=COMPOSITE_EXPORT_PROFILE, files={}, playlists={
            'a': dict(name='First', folder_path=[], tracks=[]),
            'b': dict(name='Second', folder_path=[], tracks=[])}, playlist_order=['a', 'b']), {})
        self.pages.set_snapshot(self.snapshot, composite)
        first, second, foreign = self.tree.topLevelItem(0), self.tree.topLevelItem(1), self.tree.topLevelItem(2)
        self.assertFalse(self.drop(foreign, first).accepted)
        self.assertTrue(self.drop(second, first).accepted)
        self.assertEqual(self.saved, [('b', 'a')])

    def test_actual_parent_press_and_motion_start_drag_without_toggling_or_losing_song_focus(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        song = QTreeWidgetItem(first, ['Selected song']); first.setExpanded(True)
        self.tree.setCurrentItem(song); song.setSelected(True); self.app.processEvents()
        point = self.tree.visualItemRect(first).center()
        def execute(tree, mime, _label, actions):
            self.assertIs(tree, self.tree); self.assertEqual(actions, Qt.MoveAction)
            self.assertEqual(json.loads(bytes(mime.data(tree.MIME)))['identity'], str(2**63 + 17))
            self.assertTrue(self.drop(first, second, after=True, mime=mime).accepted)
        with mock.patch('desktop.stock_device_view._start_chip_drag', side_effect=execute) as drag:
            QTest.mousePress(self.tree.viewport(), Qt.LeftButton, pos=point)
            event = QMouseEvent(QEvent.MouseMove, QPointF(point + QPoint(20, 0)), QPointF(point + QPoint(20, 0)),
                                Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(self.tree.viewport(), event)
            drag.assert_called_once()
            QTest.mouseRelease(self.tree.viewport(), Qt.LeftButton, pos=point)
        self.assertTrue(first.isExpanded()); self.assertIs(self.tree.currentItem(), song)
        self.assertEqual(self.tree.selectedItems(), [song]); self.assertEqual(len(self.saved), 1)

    def test_read_only_loading_error_and_preserved_snapshot_refusal_close_drag_admission(self):
        self.pages.set_snapshot(snapshot(self.first, self.second, writable=False))
        self.assertFalse(self.pages._playlist_order_ready())
        self.pages.set_snapshot(self.snapshot)
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        self.drop(first, second, after=True)
        self.pages.set_error('Rejected', preserve_snapshot=True)
        self.assertEqual(self.pages._managed_playlist_order(), (str(2**63 + 17), '22', '33'))
        self.pages.set_loading(); self.assertFalse(self.pages._playlist_order_ready())

    def test_late_refusal_cannot_restore_a_newer_order_gesture(self):
        first, second = self.tree.topLevelItem(0), self.tree.topLevelItem(1)
        self.drop(first, second, after=True)
        previous = self.pages.playlist_order_token
        self.pages.restore_playlist_order(previous)
        self.drop(first, second, after=True)
        current = self.pages.playlist_order_token
        self.assertIsNot(previous, current)
        self.pages.restore_playlist_order(previous)
        self.assertIs(self.pages.playlist_order_token, current)
        self.assertEqual(self.pages._managed_playlist_order(), ('22', str(2**63 + 17), '33'))
        self.pages.restore_playlist_order(current)
        self.assertEqual(self.pages._managed_playlist_order(), (str(2**63 + 17), '22', '33'))
