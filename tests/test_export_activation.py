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
import json
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import QObject, QStandardPaths, QThread, QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication
QStandardPaths.setTestModeEnabled(True)
from desktop.app import MainWindow
from desktop.workers import DeviceScanWorker
from soundbox.library import Library, Playlist, Track
from soundbox.manifest import playlist_key
app = QApplication([])
playlist = Playlist('Fixture', [], keys=['one'], pid='fixture')
library = Library({'one': Track('one', 'Original', 'Artist', 'file:///generated/one.wav')},
                  [], '/generated/master.db', source_library_id='fixture')
manifest = dict(schema=2, playlists={playlist_key(playlist):dict(pid='fixture', name='Fixture', tracks=['one'])}, files={})
captured = (manifest, json.dumps(manifest).encode())
row = dict(id='one', title='Title', artist='Artist', album='', genre='', comments='',
           year='', bpm='', bpm_estimated=False)
threads, projection_threads, drift_threads, timeline = [], [], [], []
class Host(QObject):
    _supply_device_scan_playlists = MainWindow._supply_device_scan_playlists
    _managed_ipod_display_ids = MainWindow._managed_ipod_display_ids
    _shutting_down = False
    lib = library
    overrides = {'one': 'Artist'}
    titles = {'one': 'Title'}
    def _sync_playlists(self):
        threads.append(QThread.currentThread() == app.thread())
        timeline.append('capture')
        return [playlist]
    def _row_for(self, track):
        assert track is library.tracks_by_id['one']
        projection_threads.append(QThread.currentThread() == app.thread())
        return row
def compare(*_args, **_options):
    drift_threads.append(QThread.currentThread() != app.thread())
    timeline.append('drift')
    return SimpleNamespace(count=lambda:3)
host = Host()
worker = DeviceScanWorker(None, defer_playlists=True)
host._dev_scan_worker = worker
worker.playlists_requested.connect(host._supply_device_scan_playlists, Qt.QueuedConnection)
worker.done.connect(lambda _result:timeline.append('ready'), Qt.QueuedConnection)
loop = QEventLoop()
worker.finished.connect(loop.quit)
timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(loop.quit)
with mock.patch('desktop.workers.list_devices', return_value=[('USB','/synthetic-usb',100)]), \\
     mock.patch('desktop.workers.device_profile', return_value=SimpleNamespace(kind='rockbox', has_stock_ipod=False)), \\
     mock.patch('desktop.workers.engine.capture_device_drift_manifest', return_value=captured) as observation, \\
     mock.patch('desktop.workers.engine.device_drift', side_effect=compare) as drift, \\
     mock.patch('desktop.usb_device_view.read_usb_snapshot', return_value=object()):
    worker.start()
    timer.start(2000)
    loop.exec()
    timer.stop()
    worker.cancel()
    assert worker.wait(1000)
    app.processEvents()
    assert threads == [True], threads
    assert projection_threads == [True], projection_threads
    assert drift_threads == [True], drift_threads
    assert timeline == ['capture', 'drift', 'ready'], timeline
    observation.assert_called_once_with('/synthetic-usb')
    assert drift.call_args.args == (library, '/synthetic-usb', [playlist])
    assert drift.call_args.kwargs == dict(artist_overrides=host.overrides, title_overrides=host.titles,
                                         display_rows={'one':row}, captured_manifest=captured)
    assert drift.call_args.kwargs['captured_manifest'] is captured
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

    def test_video_and_classic_warm_only_their_observed_profiles_off_the_gui_thread(self):
        from desktop.app import MainWindow
        from soundbox.stock_ipod_format import (ARTWORK_PROFILE, CLASSIC_55_ARTWORK_PROFILE,
                                                DATABASE_PROFILE, StockWriteFormat)
        host=self.host();host._stock_art_connected=True
        host.stock_art_cache=mock.Mock();host._stock_view_cache={}
        for dev,profile in (('/video',ARTWORK_PROFILE),('/classic',CLASSIC_55_ARTWORK_PROFILE)):
            host._device_profiles[dev]=SimpleNamespace(has_stock_ipod=True)
            host._stock_view_cache[dev]=(SimpleNamespace(stock_format=StockWriteFormat(DATABASE_PROFILE,profile)),None)
        with mock.patch('desktop.app.StockArtworkPrepWorker') as factory:
            MainWindow._maybe_prepare_stock_artwork(host,images=(Path('/synthetic/selected.jpg'),))
        host.stock_art_cache.for_profile.assert_not_called()
        self.assertEqual(set(factory.call_args.kwargs['profiles']),{ARTWORK_PROFILE,CLASSIC_55_ARTWORK_PROFILE})
        self.assertEqual(factory.call_args.args[3],(Path('/synthetic/selected.jpg'),))

    def test_profile_worker_warms_both_namespaces_and_reuses_without_recoding(self):
        import tempfile
        from PIL import Image
        from desktop.workers import StockArtworkPrepWorker
        from soundbox.stock_artwork import StockArtworkCache
        from soundbox.stock_ipod_format import ARTWORK_PROFILE,CLASSIC_55_ARTWORK_PROFILE
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);image=root/'cover.jpg'
            Image.new('RGB',(64,64),'blue').save(image)
            original=image.read_bytes();base=StockArtworkCache(root/'cache')
            profiles=(ARTWORK_PROFILE,CLASSIC_55_ARTWORK_PROFILE)
            worker=StockArtworkPrepWorker(None,None,base,images=(image,),profiles=profiles)
            worker.run()
            for profile,formats in ((ARTWORK_PROFILE,{1028,1029}),(CLASSIC_55_ARTWORK_PROFILE,{1055,1060,1061})):
                cache=base.for_profile(profile)
                self.assertEqual({row.format_id for row in cache.prepare(image).payloads},formats)
            # Cold creation changes the derivative epoch. The next unchanged
            # pass installs its scheduling receipt; later passes skip prepare.
            worker.run()
            for profile in profiles:self.assertTrue(base.for_profile(profile).is_prepared(image))
            with mock.patch.object(StockArtworkCache,'prepare',side_effect=AssertionError('Recoded warm image')):
                worker.run()
            self.assertEqual(image.read_bytes(),original)


if __name__ == '__main__':
    unittest.main()
