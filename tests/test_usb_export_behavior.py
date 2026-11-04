"""CDJ USB connections are passive export destinations, never sync inventories."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from desktop.devices import DeviceFacts
from desktop.usb_device_view import UsbDevicePages, UsbSnapshot
from desktop.workers import DeviceScanWorker


class PassiveUsbDiscovery(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.target = str(self.root)
        self.token = self.root.stat().st_dev
        self.profile = SimpleNamespace(kind='generic_usb', has_stock_ipod=False)
        self.enterContext(mock.patch('desktop.workers.list_devices', return_value=[('USB', self.target, 400)]))
        self.enterContext(mock.patch('desktop.workers.device_profile', return_value=self.profile))
        self.enterContext(mock.patch('desktop.devices.discovery_needs_retry', return_value=False))

    def test_usb_connection_needs_no_source_capture_or_native_catalog_read(self):
        worker = DeviceScanWorker(object(), defer_playlists=True)
        capture = mock.Mock()
        worker.playlists_requested.connect(capture, Qt.DirectConnection)
        with mock.patch('soundbox.cdj_export.status_hint', side_effect=AssertionError('USB source comparison')), \
                mock.patch('soundbox.cdj_usb.metadata_stamp', side_effect=AssertionError('USB metadata polling')), \
                mock.patch('desktop.usb_device_view.read_native_usb_snapshot', side_effect=AssertionError('Unrequested catalog')):
            worker.run()
        capture.assert_not_called()
        self.assertIsNone(worker.results[0][3])
        self.assertEqual(worker.usb_snapshots, {})

    def test_browser_request_reads_once_and_idle_scan_reuses_even_error_snapshot(self):
        snapshot = UsbSnapshot(self.root, self.token, DeviceFacts(), error='fixture stale catalog')
        worker = DeviceScanWorker(None, known_paths={self.target},
                                  known_mount_tokens={self.target: self.token}, usb_requests={self.target})
        with mock.patch('desktop.usb_device_view.read_native_usb_snapshot', return_value=snapshot) as read:
            worker.run()
        read.assert_called_once()
        idle = DeviceScanWorker(None, known_paths={self.target}, known_mount_tokens={self.target: self.token},
                                usb_views=worker.usb_snapshots)
        with mock.patch('soundbox.cdj_usb.metadata_stamp', side_effect=AssertionError('Idle catalog stat')), \
                mock.patch('desktop.usb_device_view.read_native_usb_snapshot', side_effect=AssertionError('Idle catalog read')):
            idle.run()
        self.assertIs(idle.usb_snapshots[self.target], snapshot)

    def test_cancelled_discovery_stops_before_catalog_or_source_work(self):
        worker = DeviceScanWorker(None, usb_requests={self.target})
        worker.discovered.connect(lambda _: worker.cancel(), Qt.DirectConnection)
        with mock.patch('desktop.usb_device_view.read_native_usb_snapshot') as read:
            worker.run()
        read.assert_not_called()
        self.assertIsNone(worker.results)


class SimpleEject(unittest.TestCase):
    def test_eject_cancels_idle_discovery_then_runs_macos_eject_without_a_final_read(self):
        from desktop.device_browser import DeviceBrowserMixin
        class Signal:
            def __init__(self): self.callbacks = []
            def connect(self, callback): self.callbacks.append(callback)
            def emit(self):
                for callback in self.callbacks: callback()
        scan = SimpleNamespace(finished=Signal(), cancel=mock.Mock())
        host = SimpleNamespace(_dev_scan_worker=scan,
            _EJECT_BLOCKING_WORKERS=DeviceBrowserMixin._EJECT_BLOCKING_WORKERS,
            _device_coordinator=SimpleNamespace(eject_pending=''), _stock_view_pending=set(),
            _pending_device_operation=None, _start_pulse=mock.Mock(), _begin_macos_eject=mock.Mock())
        host._eject = lambda target: DeviceBrowserMixin._eject(host, target)
        with mock.patch('desktop.device_browser.QTimer.singleShot', side_effect=lambda _delay, callback: callback()), \
                mock.patch('soundbox.cdj_usb.read_library', side_effect=AssertionError('Final USB read')), \
                mock.patch('soundbox.cdj_export.verify', side_effect=AssertionError('Eject verification')):
            host._eject('/synthetic/USB')
            scan.cancel.assert_called_once()
            host._begin_macos_eject.assert_not_called()
            host._dev_scan_worker = None
            scan.finished.emit()
        host._begin_macos_eject.assert_called_once_with('/synthetic/USB')


class UsbPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_generic_usb_never_renders_sync_or_pending_status_including_exfat(self):
        pages = UsbDevicePages()
        self.addCleanup(pages.deleteLater)
        profile = SimpleNamespace(kind='generic_usb', label='USB', cdj_export=True, rockbox_export=False)
        snapshot = UsbSnapshot(Path('/synthetic/USB'), 1, DeviceFacts(filesystem_name='exFAT', writable=True))
        for drift in (None, 0, 42):
            pages.set_snapshot(snapshot, name='USB', profile=profile, free=400, drift=drift)
            tree = pages.detail_trees['SoundBox']
            text = '\n'.join(tree.topLevelItem(i).text(column)
                             for i in range(tree.topLevelItemCount()) for column in (0, 1))
            self.assertNotIn('Sync', text)
            self.assertNotIn('pending', text.lower())
            self.assertNotIn('FAT32 required', text)
            self.assertIn('Ready', text)

    def test_usb_labels_ignore_all_drift_values(self):
        from desktop.app import MainWindow
        profile = SimpleNamespace(kind='generic_usb')
        for drift in (None, 0, 42):
            label = MainWindow._device_label(None, 'TEST-1', 1000, drift, 'exfat', profile)
            self.assertNotIn('Sync', label)
            self.assertNotIn('pending', label)
            self.assertIn('TEST-1', label)

    def test_idle_usb_poll_does_not_touch_the_mounted_usb(self):
        from desktop.app import MainWindow
        calls = []
        host = SimpleNamespace(_volume_directory_stamp=10, _device_mount_refresh_pending=False,
            _eject_ready_device='', _device_retry_at=float('inf'), _usb_view_requests=set(),
            _device_profiles={'/synthetic/USB': SimpleNamespace(kind='generic_usb')},
            _refresh_devices=lambda **kwargs: calls.append(kwargs))
        with mock.patch('desktop.app.os.stat', return_value=SimpleNamespace(st_mtime_ns=10)) as stat:
            for _ in range(100):
                MainWindow._poll_devices(host)
        self.assertEqual(calls, [])
        self.assertEqual({call.args[0] for call in stat.call_args_list}, {'/Volumes'})
        host._usb_view_requests.add('/synthetic/USB')
        with mock.patch('desktop.app.os.stat', return_value=SimpleNamespace(st_mtime_ns=10)):
            MainWindow._poll_devices(host)
        self.assertEqual(calls, [{}])

    def completion_host(self, target, snapshot):
        profile = SimpleNamespace(kind='generic_usb', label='USB', cdj_export=True, rockbox_export=False)
        pages = UsbDevicePages()
        self.addCleanup(pages.deleteLater)
        pages.set_snapshot(snapshot, name='USB', profile=profile, free=400, drift=None)
        host = SimpleNamespace(_exporting=True, _cancelling=False,
            _stop_pulse=mock.Mock(), cancel_btn=mock.Mock(), progress=mock.Mock(),
            _sync_cue_editing=mock.Mock(), _flash_status=mock.Mock(),
            _usb_view_cache={target: snapshot}, _usb_view_requests={target},
            _current_device_path=target, _device_mount_tokens={target: 1},
            _refresh_devices=mock.Mock(side_effect=AssertionError('Completion scanned USB')),
            _update_device_action_state=mock.Mock(), usb_device_pages=pages)
        host._present_usb_device = lambda dev: pages.set_snapshot(host._usb_view_cache[dev],
            name='USB', profile=profile, free=400, drift=None)
        return host

    def test_export_completion_invalidates_visible_rows_without_a_usb_read_and_keeps_drafts(self):
        from desktop.cdj import CdjDeviceMixin
        from soundbox.cdj_usb import CdjLibrary, CdjPlaylist
        target = '/synthetic/USB'
        native = CdjLibrary((CdjPlaylist('1', '0', 'Old playlist', False, (('Old', '/Contents/old.wav'),)),),
                            'Before export', 0, 0, (), 0, ())
        snapshot = UsbSnapshot(Path(target), 1, DeviceFacts(volume_uuid='fixture'), native=native)
        host = self.completion_host(target, snapshot)
        edit = host.usb_device_pages.controls['device_name']
        edit.setText('Newer unsaved name')
        with mock.patch('desktop.cdj.device_facts', side_effect=AssertionError('Unrequested facts read')), \
                mock.patch.object(host.usb_device_pages.my_settings, 'set_profile',
                                  side_effect=AssertionError('Drafts reset')):
            CdjDeviceMixin._finish_cdj(host, target, None)
        self.assertFalse(host._exporting)
        self.assertTrue(host._usb_view_cache[target].notice)
        self.assertNotIn(target, host._usb_view_requests)
        self.assertIs(host.usb_device_pages.controls['device_name'], edit)
        self.assertEqual(edit.text(), 'Newer unsaved name')
        tree = host.usb_device_pages.playlist_tree
        self.assertEqual(tree.topLevelItemCount(), 1)
        self.assertIn('Select a tab', tree.topLevelItem(0).text(0))
        self.assertNotIn('Reading', tree.topLevelItem(0).text(0))
        self.assertEqual(host.usb_device_pages._songs, {})
        host._refresh_devices.assert_not_called()

    def test_notice_keeps_real_catalog_view_state_until_explicit_browsing_reloads_rows(self):
        from desktop.cdj import CdjDeviceMixin
        from soundbox.cdj_usb import CdjLibrary, CdjPlaylist, CdjSong
        target = '/synthetic/USB'
        song = CdjSong('17', '/Contents/track.wav', 'Fixture')
        native = CdjLibrary((CdjPlaylist('1', '0', 'Playlist', False, (('Fixture', song.path),)),),
                            'USB', 0, 0, (), 0, (), songs=(song,))
        snapshot = UsbSnapshot(Path(target), 1, DeviceFacts(volume_uuid='fixture'), native=native)
        host = self.completion_host(target, snapshot)
        pages = host.usb_device_pages
        item = pages.playlist_tree.topLevelItem(0)
        item.setExpanded(True)
        pages.playlist_tree.setCurrentItem(item.child(0))
        pages._remember_view()
        before = dict(pages._states['fixture'])
        CdjDeviceMixin._finish_cdj(host, target, None)
        pages.set_page(1)  # user browses the notice before the read finishes
        self.assertEqual(pages._states['fixture']['expanded'], before['expanded'])
        self.assertEqual(pages._states['fixture']['selected'], before['selected'])
        self.assertEqual(pages._states['fixture']['page'], 1)
        pages.set_snapshot(snapshot, name='USB', profile=SimpleNamespace(kind='generic_usb', cdj_export=True),
                           free=400, drift=None)
        self.assertTrue(pages.playlist_tree.topLevelItem(0).isExpanded())
        self.assertEqual(pages._token(pages.playlist_tree.currentItem()), before['selected'])
        self.assertEqual(pages.stack.currentIndex(), 1)

    def test_failed_cancelled_and_unchanged_exports_retain_snapshot_and_drafts(self):
        from desktop.cdj import CdjDeviceMixin
        target = '/synthetic/USB'
        snapshot = UsbSnapshot(Path(target), 1, DeviceFacts(volume_uuid='fixture'))
        for result, flags in ((ValueError('Preparation failed'), {}), (None, {'unchanged': True}),
                              (None, {'cancelled': True})):
            with self.subTest(result=result, flags=flags):
                host = self.completion_host(target, snapshot)
                with mock.patch('desktop.cdj.QMessageBox.warning'):
                    CdjDeviceMixin._finish_cdj(host, target, result, **flags)
                self.assertIs(host._usb_view_cache[target], snapshot)
                self.assertIs(host.usb_device_pages._snapshot, snapshot)

    def test_source_only_refresh_skips_known_usb_but_user_requests_mounts_and_retries_proceed(self):
        from desktop.app import MainWindow
        host = SimpleNamespace(_device_profiles={'/synthetic/USB': SimpleNamespace(kind='generic_usb')},
            _usb_view_requests=set(), _device_retry_at=float('inf'),
            _device_coordinator=SimpleNamespace(polling_allowed=False))
        # A skipped source refresh never even consults operation/discovery state.
        with mock.patch('desktop.app.DeviceScanWorker', side_effect=AssertionError('Source event scanned USB')):
            MainWindow._refresh_devices(host)
        host._usb_view_requests.add('/synthetic/USB')
        MainWindow._refresh_devices(host)  # reaches the lane guard without admission
        host._usb_view_requests.clear()
        host._device_retry_at = 0
        MainWindow._refresh_devices(host)
        host._device_retry_at = float('inf')
        MainWindow._refresh_devices(host, force=True)

    def test_explicit_usb_tabs_refresh_even_cached_errors_but_internal_restoration_does_not(self):
        from desktop.app import MainWindow
        target = '/synthetic/USB'
        host = SimpleNamespace(device_content_stack=SimpleNamespace(currentIndex=lambda: 2),
            usb_device_pages=SimpleNamespace(stack=SimpleNamespace(count=lambda: 7), set_page=mock.Mock()),
            device_tab_buttons=[mock.Mock() for _ in range(7)], _current_device_path=target,
            _dev_scan_worker=None, _usb_view_requests=set(),
            _usb_view_cache={target: UsbSnapshot(Path(target), 1, DeviceFacts(), error='Old read error')},
            _refresh_devices=mock.Mock())
        MainWindow._set_device_tab(host, 1)
        host._refresh_devices.assert_not_called()
        MainWindow._set_device_tab(host, 1, refresh=True)
        self.assertEqual(host._usb_view_requests, {target})
        host._refresh_devices.assert_called_once()
        # More clicks during the same requested read do not queue duplicate work.
        host._usb_view_requests.clear()
        host._dev_scan_worker = SimpleNamespace(usb_requests={target})
        MainWindow._set_device_tab(host, 1, refresh=True)
        host._refresh_devices.assert_called_once()
        self.assertEqual(host._usb_view_requests, set())

    def test_successful_eject_retires_only_its_own_rows_and_caches_without_scanning(self):
        from desktop.device_browser import DeviceBrowserMixin
        from desktop.trees import DEVPATH_ROLE
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
        target, other = '/synthetic/USB', '/synthetic/OTHER'
        tree = QTreeWidget()
        self.addCleanup(tree.deleteLater)
        for path in (target, other):
            item = QTreeWidgetItem(tree, [path]); item.setData(0, DEVPATH_ROLE, path)
        names = ('_device_profiles', '_device_mount_tokens', '_drift_cache', '_usb_device_rows',
                 '_usb_view_cache', '_stock_view_cache', '_stock_view_failures', '_stock_view_pending',
                 '_dual_sync_queue')
        host = SimpleNamespace(device_tree=tree, _device_coordinator=SimpleNamespace(disconnect=mock.Mock()),
            _usb_view_requests={target, other}, _stock_view_warming={target, other},
            _current_device_path=target, _stock_snapshot=object(), stack=mock.Mock(),
            _set_device_poll_interval=mock.Mock(), _update_device_action_state=mock.Mock(),
            _refresh_devices=mock.Mock(side_effect=AssertionError('Eject caused a device scan')),
            **{name: {target: object(), other: object()} for name in names})
        with mock.patch('desktop.devices.forget_device_mount') as forget, \
                mock.patch('desktop.device_browser.os.stat', side_effect=AssertionError('Eject stat')):
            DeviceBrowserMixin._retire_ejected_device(host, target)
        forget.assert_called_once_with(target)
        self.assertEqual(tree.topLevelItemCount(), 1)
        self.assertEqual(tree.topLevelItem(0).data(0, DEVPATH_ROLE), other)
        for name in names:
            self.assertEqual(set(getattr(host, name)), {other})
        self.assertEqual(host._usb_view_requests, {other})
        self.assertEqual(host._stock_view_warming, {other})
        self.assertEqual(host._current_device_path, '')
        host.stack.setCurrentIndex.assert_called_once_with(0)
        host._refresh_devices.assert_not_called()


if __name__ == '__main__':
    unittest.main()
