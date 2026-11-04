"""Export standby and attachment lifecycle, without real mounts or app state."""
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock


class ExportActivation(unittest.TestCase):
    def test_empty_startup_leaves_export_backends_and_indexes_unloaded(self):
        script = '''
import json, sys, tempfile
from unittest import mock
from PySide6.QtCore import QStandardPaths, QEventLoop, QTimer
QStandardPaths.setTestModeEnabled(True)
from desktop.app import MainWindow, make_app
app = make_app()
with tempfile.TemporaryDirectory(prefix='soundbox-export-standby-') as area:
    win = MainWindow(isolated_dir=area)
    app.processEvents()
    win._isolated = False
    loop = QEventLoop()
    with mock.patch('desktop.workers.list_devices', return_value=[]), \\
         mock.patch.object(win, '_sync_playlists', side_effect=AssertionError('Empty scan captured exports')):
        win._refresh_devices()
        worker = win._dev_scan_worker
        worker.finished.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)
        if worker.isRunning():
            loop.exec()
        app.processEvents()
        assert win._dev_scan_worker is None
    backends = ('soundbox.stock_ipod', 'soundbox.dual_ipod',
                'soundbox.cdj_export', 'soundbox.cdj_transaction',
                'soundbox.sync', 'soundbox.rockbox_media', 'soundbox.stock_artwork',
                'soundbox.stock_ipod_shadow')
    caches = ('stock_art_cache', 'rockbox_media_cache', 'rockbox_art_cache', 'stock_ipod_shadow_store')
    print(json.dumps(dict(loaded=[n for n in backends if n in sys.modules],
                          caches=[n for n in caches if n in win.__dict__],
                          stock_timer=win._stock_art_timer.isActive(),
                          stock_worker=win._stock_art_worker is not None)))
    win.shutdown()
'''
        run = subprocess.run([sys.executable, '-c', script],
                             cwd=Path(__file__).resolve().parents[1],
                             env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.splitlines()[-1])
        self.assertEqual(result, dict(loaded=[], caches=[], stock_timer=False, stock_worker=False))

    def test_queued_capture_runs_on_gui_thread_before_first_ready_status(self):
        script = '''
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import QObject, QStandardPaths, QThread, QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication
QStandardPaths.setTestModeEnabled(True)
from desktop.app import MainWindow
from desktop.workers import DeviceScanWorker
app = QApplication([])
library, playlist = object(), object()
threads = []
class Host(QObject):
    _supply_device_scan_playlists = MainWindow._supply_device_scan_playlists
    _shutting_down = False
    lib = library
    overrides = {'one': 'Artist'}
    titles = {'one': 'Title'}
    def _sync_playlists(self):
        threads.append(QThread.currentThread() == app.thread())
        return [playlist]
host = Host()
worker = DeviceScanWorker(None, defer_playlists=True)
host._dev_scan_worker = worker
worker.playlists_requested.connect(host._supply_device_scan_playlists, Qt.QueuedConnection)
loop = QEventLoop()
worker.finished.connect(loop.quit)
timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(loop.quit)
with mock.patch('desktop.workers.list_devices', return_value=[('USB','/synthetic-usb',100)]), \\
     mock.patch('desktop.workers.device_profile', return_value=SimpleNamespace(kind='rockbox', has_stock_ipod=False)), \\
     mock.patch('desktop.workers.engine.device_drift', return_value=SimpleNamespace(count=lambda:3)) as drift, \\
     mock.patch('desktop.usb_device_view.read_usb_snapshot', return_value=object()):
    worker.start()
    timer.start(2000)
    loop.exec()
    timer.stop()
    worker.cancel()
    assert worker.wait(1000)
    assert threads == [True], threads
    assert drift.call_args.args == (library, '/synthetic-usb', [playlist])
    assert drift.call_args.kwargs == dict(artist_overrides=host.overrides, title_overrides=host.titles)
    assert worker.results[0][3] == 3
'''
        run = subprocess.run([sys.executable, '-c', script],
                             cwd=Path(__file__).resolve().parents[1],
                             env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_stale_shutdown_or_cancelled_capture_never_builds_playlists(self):
        from desktop.app import MainWindow
        for stale, shutdown, cancelled in ((True, False, False), (False, True, False), (False, False, True)):
            worker = mock.Mock(cancellation=Event())
            if cancelled:
                worker.cancellation.set()
            host = SimpleNamespace(_dev_scan_worker=object() if stale else worker,
                                   _shutting_down=shutdown, _sync_playlists=mock.Mock())
            MainWindow._supply_device_scan_playlists(host, worker)
            host._sync_playlists.assert_not_called()
            worker.provide_playlists.assert_not_called()
            worker.cancel.assert_called_once_with()

    def host(self):
        from desktop.app import MainWindow
        host = SimpleNamespace(
            _device_profiles={}, _stock_art_connected=False,
            _stock_art_worker=None, _stock_art_rescan=False, _stock_art_pending=set(),
            _stock_art_timer=mock.Mock(), _shutting_down=False, lib=object(),
            art_cache=mock.Mock(), player_bar=mock.Mock())
        host._stock_art_timer.isActive.return_value = False
        host._schedule_stock_artwork = lambda: MainWindow._schedule_stock_artwork(host)
        return host

    def test_only_ipod_attachment_schedules_preparation_once(self):
        from desktop.app import MainWindow
        host = self.host()
        for kind in ('generic_usb', 'rockbox'):
            host._device_profiles = {'/synthetic': SimpleNamespace(kind=kind, has_stock_ipod=False)}
            MainWindow._update_export_preparation(host)
        host._stock_art_timer.start.assert_not_called()
        host._device_profiles = {'/synthetic': SimpleNamespace(kind='dual_boot', has_stock_ipod=True)}
        MainWindow._update_export_preparation(host)
        MainWindow._update_export_preparation(host)
        host._stock_art_timer.start.assert_called_once_with()
        self.assertTrue(host._stock_art_connected)

    def test_disconnected_edits_do_no_export_artwork_lookup(self):
        from desktop.app import MainWindow
        host = self.host()
        host._stock_art_pending.add(Path('/synthetic/old.jpg'))
        with mock.patch('desktop.app.StockArtworkPrepWorker') as factory:
            MainWindow._queue_stock_artwork(host, ['song'])
            MainWindow._start_pending_stock_artwork(host)
            MainWindow._maybe_prepare_stock_artwork(host)
        factory.assert_not_called()
        host.art_cache.image_path.assert_not_called()

    def test_disconnect_stops_worker_but_retains_ownership_until_finished(self):
        from desktop.app import MainWindow
        host = self.host()
        worker = mock.Mock()
        host._stock_art_worker = worker
        host._stock_art_connected = True
        host._stock_art_pending.add(Path('/synthetic/pending.jpg'))
        host._stock_art_rescan = True
        MainWindow._update_export_preparation(host)
        worker.stop.assert_called_once_with()
        self.assertIs(host._stock_art_worker, worker)
        self.assertFalse(host._stock_art_pending)
        self.assertFalse(host._stock_art_rescan)
        self.assertFalse(host._stock_art_connected)
        host._stock_art_timer.stop.assert_called_once_with()

    def test_reconnect_waits_for_retiring_worker_before_rescanning(self):
        from desktop.app import MainWindow
        host = self.host()
        worker = mock.Mock(_stop=True)
        host._stock_art_worker = worker
        host._device_profiles = {'/synthetic': SimpleNamespace(has_stock_ipod=True)}
        MainWindow._update_export_preparation(host)
        with mock.patch('desktop.app.StockArtworkPrepWorker') as factory:
            MainWindow._maybe_prepare_stock_artwork(host)
        factory.assert_not_called()
        self.assertTrue(host._stock_art_rescan)
        host._maybe_prepare_stock_artwork = mock.Mock()
        with mock.patch('desktop.app.QTimer.singleShot') as schedule:
            MainWindow._on_stock_artwork_finished(host, worker)
        self.assertIsNone(host._stock_art_worker)
        schedule.assert_called_once_with(0, host._maybe_prepare_stock_artwork)
        worker.deleteLater.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
