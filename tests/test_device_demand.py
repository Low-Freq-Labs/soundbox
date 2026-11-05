"""Startup and passive UI work never construct disconnected device surfaces."""
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from PySide6.QtCore import QEvent, QPointF, QSize, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtTest import QTest

from desktop.app import MainWindow
from desktop.export_dialog import ExportSelectionMixin
from desktop.trees import DEVPATH_ROLE


class DeviceDemand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, *, providers=None):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        # Suppress even the constructor's discovery request; every target below
        # is a synthetic profile and no native source/provider is admitted.
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('live source')))
        win = MainWindow(providers=providers, isolated_dir=Path(area.name))
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.shutdown)
        return win

    def test_passive_startup_resize_theme_and_shutdown_keep_device_engines_absent(self):
        with (
            mock.patch('desktop.app.UsbDevicePages', side_effect=AssertionError('USB surface before demand')),
            mock.patch('desktop.app.StockIpodPages', side_effect=AssertionError('iPod surface before demand')),
        ):
            win = self.window()
            win.show(); self.app.processEvents()
            win.resize(1300, 820); self.app.processEvents()
            win._sync_artist_columns(); win._fit_browser_columns(fill=True)
            win._apply_theme_color('#123456', save=False)
            win._update_device_action_state()
            self.assertFalse(win.device_sync_btn.isEnabled())
            self.assertFalse(win.eject_btn.isEnabled())
            for name in ('usb_device_pages', 'stock_ipod_pages', 'stock_art_cache',
                         'rockbox_media_cache', 'rockbox_art_cache', 'stock_ipod_shadow_store'):
                self.assertNotIn(name, vars(win))
            self.assertTrue(hasattr(win, '_settings_tabs'))
            self.assertEqual(win._settings_tabs.count(), 6)
            win.shutdown()
            self.assertNotIn('usb_device_pages', vars(win))

    def select(self, win, profile):
        path = '/synthetic/device'
        win._device_profiles[path] = profile
        item = QTreeWidgetItem(['Synthetic']); item.setData(0, DEVPATH_ROLE, path)
        win.device_tree.addTopLevelItem(item)
        return item

    def test_usb_view_constructs_only_usb_and_retains_shared_column_geometry(self):
        win = self.window()
        item = self.select(win, SimpleNamespace(kind='generic_usb', has_stock_ipod=False))
        win.table.setColumnWidth(0, 417)
        win._on_device_click(item, 0)
        pages = win.usb_device_pages
        self.assertIs(win.device_content_stack.widget(2), pages)
        self.assertEqual(win.device_content_stack.currentIndex(), 2)
        self.assertEqual(pages.playlist_tree.columnWidth(0), win.table.columnWidth(0))
        self.assertNotIn('stock_ipod_pages', vars(win))
        self.assertIs(win.usb_device_pages, pages)
        self.assertEqual(win.device_content_stack.count(), 3)

    def test_ipod_view_constructs_only_ipod_before_queued_read(self):
        win = self.window()
        item = self.select(win, SimpleNamespace(kind='stock_ipod', has_stock_ipod=True))
        with mock.patch.object(win, '_read_stock_device_view') as reader:
            win._on_device_click(item, 0)
            reader.assert_called_once()
        self.assertIs(win.device_content_stack.widget(1), win.stock_ipod_pages)
        self.assertEqual(win.device_content_stack.currentIndex(), 1)
        self.assertNotIn('usb_device_pages', vars(win))

    def test_settings_ready_before_show_first_open_uses_current_preferences_and_preserves_view(self):
        win = self.window()
        self.assertFalse(win.isVisible())
        self.assertEqual([win._settings_tabs.tabText(i) for i in range(win._settings_tabs.count())],
                         ['General', 'Analysis', 'My Settings', 'Keyboard', 'Graveyard', 'Connections'])
        self.assertEqual(len(win._global_my_settings.controls), 44)
        self.assertIsNotNone(win._keyboard_page)
        self.assertIsNotNone(win._analysis_controls)
        win._apply_theme_text('custom', '#112233', save=False)
        win.settings['hide_untracked'] = True
        self.assertTrue(hasattr(win, '_settings_tabs'))
        with mock.patch.object(win, '_settings_page', side_effect=AssertionError('First click built Settings')):
            win._open_settings()
        page = win.body_stack.widget(1)
        self.assertEqual(win.body_stack.currentIndex(), 1)
        self.assertEqual(win._theme_text_mode.currentData(), 'custom')
        self.assertEqual(win._theme_text_color.text(), '#112233')
        self.assertTrue(win._chk_hide.isChecked())
        win._close_settings(); win._open_settings()
        self.assertIs(win.body_stack.widget(1), page)
        self.assertEqual(win.body_stack.count(), 2)
        self.assertNotIn('usb_device_pages', vars(win))
        self.assertNotIn('stock_ipod_pages', vars(win))

    def test_export_without_capable_targets_never_builds_playlist_inventory_or_dialog(self):
        host = SimpleNamespace(lib=SimpleNamespace(source='fixture', source_library_id='fixture'),
                               _device_profiles={}, _device_operation_owned=lambda _: False,
                               _flash_status=mock.Mock(),
                               _export_playlists=mock.Mock(side_effect=AssertionError('undemanded playlist inventory')))
        with mock.patch('desktop.export_dialog.ExportDialog', side_effect=AssertionError('disconnected Export dialog')):
            ExportSelectionMixin._show_export_dialog(host)
        host._export_playlists.assert_not_called()
        host._flash_status.assert_called_once()

    def test_unfocused_shortcuts_do_not_treat_absent_graveyard_as_selection(self):
        win = self.window()
        with mock.patch.object(QApplication, 'focusWidget', return_value=None):
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier)
            win.eventFilter(win.table, event)
            win._dispatch_keyboard_action('browser.load')
        self.assertTrue(hasattr(win, '_grave'))
        self.assertTrue(hasattr(win, '_settings_tabs'))

    def test_construction_events_skip_input_work_and_read_event_type_once(self):
        win = self.window()
        class CountedEvent(QEvent):
            calls = 0
            def type(self):
                self.calls += 1
                return super().type()
        event = CountedEvent(QEvent.StyleChange)
        with mock.patch.object(win, '_defer_library_prep') as defer, \
                mock.patch.object(win._keyboard_router, 'handle') as keyboard:
            self.assertFalse(win.eventFilter(win.table, event))
        self.assertEqual(event.calls, 1)
        defer.assert_not_called(); keyboard.assert_not_called()
        self.assertTrue(hasattr(win, '_settings_tabs'))
        self.assertNotIn('usb_device_pages', vars(win))

    def test_geometry_and_now_playing_mouse_routes_survive_event_filter_fast_path(self):
        win = self.window(); win._browser_columns_shown = True
        viewport = win.table.viewport()
        with mock.patch.object(win, '_fit_browser_columns') as fit, \
                mock.patch.object(win, '_defer_library_prep') as defer:
            win.eventFilter(viewport, QEvent(QEvent.Show))
            fit.assert_called_once_with(fill=True, available=viewport.width())
            # A hidden viewport receives its geometry on Show; Resize still
            # defers background preparation regardless of its visibility.
            win.eventFilter(viewport, QResizeEvent(QSize(701, 300), QSize(700, 300)))
            defer.assert_called_once_with(0.75)
        release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(2, 2), QPointF(2, 2),
                              Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        with mock.patch.object(win, '_focus_now_playing') as focus:
            self.assertTrue(win.eventFilter(win.now_playing, release))
        focus.assert_called_once()

    def test_key_release_reaches_router_with_ready_settings(self):
        win = self.window()
        event = QKeyEvent(QEvent.KeyRelease, Qt.Key_C, Qt.NoModifier)
        with mock.patch.object(QApplication, 'focusWidget', return_value=win.table), \
                mock.patch.object(win, '_space_should_play', return_value=True), \
                mock.patch.object(win._keyboard_router, 'handle', return_value=True) as keyboard:
            self.assertTrue(win.eventFilter(win.table, event))
        keyboard.assert_called_once_with(event, True)
        self.assertTrue(hasattr(win, '_settings_tabs'))

    def test_saved_global_usb_profile_does_not_construct_device_surface(self):
        from soundbox import my_settings
        win = self.window()
        win._open_settings()
        profile = my_settings.values(my_settings.new_files({}).items())
        win._save_global_my_settings(profile)
        self.assertEqual(win.settings['cdj_my_settings'], profile)
        self.assertEqual(win._global_my_settings.profile, profile)
        self.assertNotIn('usb_device_pages', vars(win))

    def start_connection(self, win, service):
        factory = win.providers.connections
        self.assertIs(factory.create.return_value, service)
        # Only admit this controlled provider operation. The window remains an
        # isolated fixture for every other source/device/background path.
        with mock.patch.object(win, '_isolated', False), \
                mock.patch('desktop.app.QStandardPaths.isTestModeEnabled', return_value=False):
            win._prepare_connections()
            win._prepare_connections()
        return factory

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(2)
        self.assertTrue(predicate())

    def test_connection_refresh_without_settings_stays_owned_and_settings_can_cancel(self):
        from soundbox.providers import Providers
        entered = Event()

        def refresh(stop, progress):
            entered.set()
            stop.wait(3)

        service = SimpleNamespace(refresh_initial=mock.Mock(side_effect=refresh),
                                  status=lambda: dict(connected=True, count=3))
        win = self.window(providers=Providers('personal', actions=('connections.soundcloud',),
            connections=SimpleNamespace(create=mock.Mock(return_value=service))))
        factory = self.start_connection(win, service)
        self.assertTrue(entered.wait(3))
        self.assertTrue(hasattr(win, '_settings_tabs'))
        worker = win._connection_initial_worker
        self.assertEqual(win._connection_tasks.pending, 1)
        win._open_settings()
        section = win._soundcloud_section
        self.assertIs(section._startup_worker, worker)
        self.assertFalse(section.refresh_button.isEnabled())
        section._cancel()
        self.wait_for(lambda: win._connection_initial_worker is None)
        self.assertTrue(worker.cancellation.is_set())
        self.assertTrue(win._connection_initial_complete)
        self.assertIsNone(win._connection_initial_result)
        self.assertTrue(section.refresh_button.isEnabled())
        self.assertEqual(win._connection_tasks.pending, 0)
        factory.create.assert_called_with(win._data_file.parent)
        service.refresh_initial.assert_called_once()
        with mock.patch.object(win, '_isolated', False), \
                mock.patch('desktop.app.QStandardPaths.isTestModeEnabled', return_value=False):
            win._prepare_connections()
        service.refresh_initial.assert_called_once()

    def test_connection_failure_before_settings_open_is_retained_without_retry(self):
        from soundbox.providers import ConnectionFailure, Providers
        service = SimpleNamespace(refresh_initial=mock.Mock(side_effect=ConnectionFailure('Offline fixture')),
                                  status=lambda: dict(connected=True, count=3))
        win = self.window(providers=Providers('personal', actions=('connections.soundcloud',),
            connections=SimpleNamespace(create=mock.Mock(return_value=service))))
        factory = self.start_connection(win, service)
        self.wait_for(lambda: getattr(win, '_connection_initial_complete', False))
        self.assertTrue(hasattr(win, '_settings_tabs'))
        win._open_settings()
        self.assertEqual(win._soundcloud_section.status.text(), 'Offline fixture')
        self.assertTrue(win._soundcloud_section.refresh_button.isEnabled())
        service.refresh_initial.assert_called_once()
        self.assertGreaterEqual(factory.create.call_count, 1)
