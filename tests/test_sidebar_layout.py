"""Hidden shared-sidebar sizing, presentation and sparse width preferences."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QStackedWidget,
                              QPushButton, QStyle, QStyleOptionViewItem, QTableView,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from desktop.app import MainWindow
from desktop.devices import DeviceFacts
from desktop.model import TrackModel
from desktop.state_commit import PREFERENCE_KEYS
from soundbox.device_operations import DeviceOperationCoordinator
from soundbox.device_profile import DeviceProfile


class SidebarLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        no_windows = mock.patch.object(
            QWidget, 'show', side_effect=AssertionError('Visible synthetic window'))
        no_windows.start()
        self.addCleanup(no_windows.stop)
        self.window = QMainWindow()
        w = self.window
        w.resize(1100, 700)
        w.settings = {'source_curation': {'pins': {'song': {'title': 'preserved'}}}}
        w._settings_dirty_keys = set()
        w._settings_save_timer = QTimer(w)
        w._settings_save_timer.setSingleShot(True)
        w._settings_save_timer.setInterval(400)
        w._shutting_down = False
        w._state_maintenance = False
        w._state_committer = SimpleNamespace(queue_preferences=mock.Mock(return_value=True))
        w._flash_status = mock.Mock()
        for name in ('_new_playlist_menu', '_rb_header_menu', '_on_playlist_select',
                     '_collapse_siblings', '_local_folder_expanded', '_native_append',
                     '_native_reorder', '_native_move', '_native_place', '_native_import_files', '_on_tracks_dropped',
                     '_move_custom', '_move_local_node', '_import_files', '_playlist_menu',
                     '_device_menu', '_on_device_click', '_on_device_drop',
                     '_sync_artist_columns', '_fit_browser_columns'):
            setattr(w, name, mock.Mock())
        for name in ('_section_header', '_sidebar', '_browser_body', '_save_sidebar_width',
                     '_restore_sidebar_width', '_save_columns', '_restore_columns',
                     '_schedule_settings_save', '_flush_settings_preferences',
                     '_device_label', '_device_tip'):
            setattr(w, name, lambda *args, _name=name, **kwargs:
                    getattr(MainWindow, _name)(w, *args, **kwargs))
        def content():
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            w.table = QTableView()
            w.table.setModel(TrackModel([]))
            layout.addWidget(w.table)
            return panel
        w._main_stack = content
        center = QWidget()
        outer = QVBoxLayout(center)
        outer.setContentsMargins(0, 0, 0, 0)
        w.body_stack = QStackedWidget()
        outer.addWidget(w.body_stack)
        w.setCentralWidget(center)
        w.body_stack.addWidget(w._browser_body())
        w._restore_columns()
        self.addCleanup(self.retire)

    def retire(self):
        self.window._settings_save_timer.stop()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def activate(self):
        for widget in (self.window, self.window.centralWidget(), self.window.body_stack,
                       self.window._browser_split.widget(0)):
            if widget.layout() is not None:
                widget.layout().activate()
        # Rendering a hidden widget delivers its pending resize events without
        # showing a window; QTreeView then fits its header to the real viewport.
        self.window.render(QPixmap(self.window.size()))
        self.app.processEvents()

    def test_shared_column_resizes_horizontally_without_collapsing_vertical_sections(self):
        w = self.window
        self.activate()
        self.assertEqual(w._browser_split.orientation(), Qt.Horizontal)
        self.assertFalse(w._browser_split.childrenCollapsible())
        self.assertEqual(w._sidebar_split.orientation(), Qt.Vertical)
        self.assertFalse(w._sidebar_split.childrenCollapsible())
        self.assertEqual(w._sidebar_split.count(), 2)
        before_vertical = w._sidebar_split.sizes()
        w._browser_split.moveSplitter(430, 1)
        self.activate()
        self.assertEqual(w._browser_split.sizes()[0], 430)
        self.assertEqual(w.tree.width(), w.device_tree.width())
        self.assertEqual(w._sidebar_split.sizes(), before_vertical)
        w.resize(1300, 700)
        self.activate()
        self.assertEqual(w._browser_split.sizes()[0], 430)
        w._browser_split.setSizes([0, 1100])
        self.assertGreaterEqual(w._browser_split.sizes()[0], 160)
        self.assertGreaterEqual(w._browser_split.sizes()[1], 320)

    def test_drag_admits_only_the_debounced_width_preference(self):
        w = self.window
        self.assertIn('sidebar_width', PREFERENCE_KEYS)
        preserved = deepcopy(w.settings['source_curation'])
        for width in (320, 380, 440):
            w._browser_split.moveSplitter(width, 1)
        self.assertEqual(w.settings['sidebar_width'], 440)
        self.assertEqual(w._settings_dirty_keys, {'sidebar_width'})
        self.assertTrue(w._settings_save_timer.isActive())
        w._state_committer.queue_preferences.assert_not_called()
        self.assertTrue(w._flush_settings_preferences())
        w._state_committer.queue_preferences.assert_called_once_with(
            {'sidebar_width': 440}, frozenset())
        self.assertEqual(w.settings['source_curation'], preserved)

    def test_saved_width_and_vertical_heights_restore_together(self):
        w = self.window
        w._browser_split.moveSplitter(420, 1)
        w._sidebar_split.setSizes([500, 120])
        w._save_columns()
        saved = deepcopy(w.settings)
        sizes = w._sidebar_split.sizes()
        self.assertEqual(saved['sidebar_width'], 420)
        w._browser_split.moveSplitter(220, 1)
        w._sidebar_split.setSizes([240, 200])
        w.settings = saved
        w._restore_columns()
        self.assertEqual(w._browser_split.sizes()[0], 420)
        self.assertEqual(w._sidebar_split.sizes(), sizes)

    def test_bad_width_defaults_and_oversized_width_keeps_content_visible(self):
        w = self.window
        for value in ('wide', None, True, -3, 80):
            with self.subTest(value=value):
                w.settings['sidebar_width'] = value
                w._restore_sidebar_width()
                self.assertEqual(w._browser_split.sizes()[0], 272)
        w.settings['sidebar_width'] = 2 ** 128
        w._restore_sidebar_width()
        self.assertGreaterEqual(w._browser_split.sizes()[0], 160)
        self.assertGreaterEqual(w._browser_split.sizes()[1], 320)

    def test_narrow_sidebar_elides_labels_and_wider_sidebar_reveals_them(self):
        w = self.window
        playlist = 'A long readable playlist name with 212 songs'
        device = 'SONNY · iTunes/Rockbox iPod · 1.0 TB'
        QTreeWidgetItem(w.tree, [playlist])
        QTreeWidgetItem(w.device_tree, [device])
        drawn = []
        draw_text = QPainter.drawText
        def record(painter, *args):
            if args and isinstance(args[-1], str):
                drawn.append(args[-1])
            return draw_text(painter, *args)
        for width, elided in ((180, True), (650, False)):
            with self.subTest(width=width):
                w._browser_split.moveSplitter(width, 1)
                self.activate()
                for tree in (w.tree, w.device_tree):
                    self.assertEqual(tree.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                    self.assertEqual(tree.textElideMode(), Qt.ElideRight)
                    self.assertEqual(tree.columnWidth(0), tree.viewport().width())
                option = QStyleOptionViewItem()
                option.initFrom(w.tree)
                option.rect = QRect(0, 0, w.tree.viewport().width(), 24)
                option.widget = w.tree
                pixmap = QPixmap(w.tree.viewport().width(), 24)
                painter = QPainter(pixmap)
                try:
                    drawn.clear()
                    with mock.patch.object(QPainter, 'drawText', new=record):
                        w.tree.itemDelegate().paint(painter, option, w.tree.model().index(0, 0))
                finally:
                    painter.end()
                self.assertEqual(len(drawn), 1)
                if elided:
                    self.assertTrue(drawn[0].endswith('…'))
                    self.assertNotEqual(drawn[0], playlist)
                else:
                    self.assertEqual(drawn[0], playlist)
                item_option = QStyleOptionViewItem()
                item_option.initFrom(w.device_tree)
                item_option.rect = QRect(0, 0, w.device_tree.viewport().width(), 24)
                item_option.widget = w.device_tree
                w.device_tree.itemDelegate().initStyleOption(
                    item_option, w.device_tree.model().index(0, 0))
                text_rect = w.device_tree.style().subElementRect(
                    QStyle.SE_ItemViewItemText, item_option, w.device_tree)
                rendered = item_option.fontMetrics.elidedText(device, Qt.ElideRight, text_rect.width())
                self.assertEqual(rendered != device, elided)
                self.assertEqual(w.tree.topLevelItem(0).text(0), playlist)
                self.assertEqual(w.device_tree.topLevelItem(0).text(0), device)

    def test_device_labels_keep_total_capacity_and_explicit_ipod_types(self):
        for kind, label in (('stock_ipod', 'iTunes iPod'), ('rockbox', 'Rockbox iPod'),
                            ('dual_boot', 'iTunes/Rockbox iPod'), ('generic_usb', 'USB')):
            for drift in (None, 0, 3):
                with self.subTest(kind=kind, drift=drift):
                    actual = self.window._device_label('SONNY', 10 ** 12, drift, 'msdos',
                        SimpleNamespace(kind=kind, label='ignored legacy label'))
                    self.assertEqual(actual, f'SONNY · {label} · 1.0 TB')
                    self.assertNotIn('free', actual)
                    self.assertNotIn('  ', actual)

    def test_device_scan_uses_cached_capacity_and_refreshes_label_in_place(self):
        w = self.window
        for name, value in dict(
                _set_device_poll_interval=mock.Mock(), _update_export_preparation=mock.Mock(),
                _usb_view_cache={}, _usb_view_requests=set(), _device_mount_tokens={},
                _device_coordinator=DeviceOperationCoordinator(), _stock_view_cache={},
                _stock_catalog_witness_cache={}, _stock_view_failures={}, _dual_sync_queue={},
                _stock_view_warming=set(), _stock_view_pending={}, _current_device_path='',
                _current_device_role='', _pending_device_operation=None,
                _exporting=False, _deleting=False, lib=None,
                _EJECT_BLOCKING_WORKERS=MainWindow._EJECT_BLOCKING_WORKERS,
                _finish_ipod_loading_status=mock.Mock()).items():
            setattr(w, name, value)
        for name in ('_device_operation_owned', '_update_device_action_state'):
            setattr(w, name, lambda *args, _name=name, **kwargs:
                    getattr(MainWindow, _name)(w, *args, **kwargs))
        w.device_sync_btn=QPushButton('Export',w)
        w.eject_btn=QPushButton('Eject',w)
        profile = DeviceProfile('generic_usb', 'USB', 'exfat', False, False, True, False, False)
        row = [('SONNY', '/generated/USB', 2 * 10 ** 9, 0, profile)]
        with mock.patch('desktop.app.device_facts', return_value=DeviceFacts(capacity_bytes=10 ** 12)) as facts, \
                mock.patch('desktop.app.device_fs', return_value='exfat'):
            MainWindow._on_devices_scanned(w, row, mount_tokens={'/generated/USB': 123})
        facts.assert_called_once_with('/generated/USB', cached_only=True)
        item = w.device_tree.topLevelItem(0)
        self.assertEqual(item.text(0), 'SONNY · USB · 1.0 TB')
        self.assertIn('Capacity: 1.0 TB', item.toolTip(0))
        self.assertIn('2.0 GB free', item.toolTip(0))
        self.assertFalse(w.device_sync_btn.isEnabled())
        self.assertFalse(w.eject_btn.isEnabled())
        w.device_tree.setCurrentItem(item)
        with mock.patch('desktop.app.device_facts', return_value=DeviceFacts(capacity_bytes=2 * 10 ** 12)) as facts, \
                mock.patch('desktop.app.device_fs', return_value='exfat'):
            MainWindow._on_devices_scanned(w, row, mount_tokens={'/generated/USB': 123})
        facts.assert_called_once_with('/generated/USB', cached_only=True)
        self.assertIs(w.device_tree.topLevelItem(0), item)
        self.assertIs(w.device_tree.currentItem(), item)
        self.assertEqual(item.text(0), 'SONNY · USB · 2.0 TB')
        self.assertFalse(w.device_sync_btn.isEnabled())
        self.assertFalse(w.eject_btn.isEnabled())
