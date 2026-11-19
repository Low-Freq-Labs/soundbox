"""Cache ownership and helper retirement preserve responsive desktop lifetime."""
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.task_owner import TaskOwner
from soundbox.library import Library, Track


class CacheDesktopLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        host = QObject()
        host._cache_tasks = TaskOwner(host)
        host._libcache_worker = host._libcache_pending = None
        host._shutting_down = False
        host._browser_cache_key = 'already-prepared-key'
        host._cache_gen = host._display_input_generation = 1
        host._display_inputs = host._browser_cache_inputs = (frozenset(), 'policy')
        host._row_cache = {'one': {'id': 'one', 'title': 'Original'}}
        host._oneshots = []
        host.lib = Library({'one': Track('one', 'Original', '', 'file:///never.wav')}, [], 'synthetic')
        for name in ('_start_library_cache', '_finish_library_cache', '_on_library_cache_done', '_cache_library'):
            setattr(host, name, getattr(MainWindow, name).__get__(host))
        host._finish_oneshot = lambda worker: host._oneshots.remove(worker)
        self.addCleanup(host.deleteLater)
        self.addCleanup(host._cache_tasks.drain)
        return host

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertTrue(predicate())

    def test_cache_service_runs_on_owned_worker_and_newest_refresh_coalesces(self):
        host = self.host();gui = threading.get_ident();entered = threading.Event();release = threading.Event();calls = []
        def write(lib, inputs, rows, *, key):
            calls.append((lib, threading.get_ident(), rows, key))
            if len(calls) == 1:
                entered.set();release.wait(3)
        host._cache_process = SimpleNamespace(available=True, write=write)
        host._cache_library();self.assertTrue(entered.wait(2))
        first = host._libcache_worker
        middle = Library({}, [], 'middle');latest = Library({}, [], 'latest')
        host.lib = middle;host._cache_library()
        host.lib = latest;host._cache_library()
        self.assertIs(host._libcache_worker, first)
        self.assertIs(host._libcache_pending, latest)
        release.set()
        self.wait_for(lambda: len(calls) == 2 and host._libcache_worker is None)
        self.assertEqual([call[0].source for call in calls], ['synthetic', 'latest'])
        self.assertTrue(all(call[1] != gui for call in calls))
        self.assertEqual(host._cache_tasks.pending, 0)
        self.assertEqual(host._oneshots, [])

    def test_unavailable_helper_never_uses_parent_cache_writer(self):
        host = self.host();host._cache_process = SimpleNamespace(available=False)
        with mock.patch('desktop.app.libcache.dump', side_effect=AssertionError('No GUI fallback')):
            host._cache_library()
        self.assertIsNone(host._libcache_worker)
        host._cache_process = SimpleNamespace(available=True, write=mock.Mock())
        host._shutting_down = True
        host._cache_library()
        host._cache_process.write.assert_not_called()
        self.assertIsNone(host._libcache_worker)

    def test_ordinary_services_prewarm_once_at_explicit_constructor_stage(self):
        host = self.host()
        host._data_file = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'library.json'
        cache, reader = mock.Mock(), mock.Mock()
        with mock.patch('soundbox.cache_process.CacheProcess', return_value=cache), \
                mock.patch('soundbox.native_reader_process.NativeReaderProcess', return_value=reader):
            MainWindow._prepare_library_processes(host)
        cache.start.assert_called_once_with();reader.start.assert_called_once_with()
        self.assertIs(host._cache_process, cache)
        self.assertIs(host.native_reader, reader)

    def test_shutdown_joins_same_timed_out_actor_off_gui_with_qt_dispatch_alive(self):
        host = self.host();gui = threading.get_ident();threads = [];ticks = []
        process = SimpleNamespace(poll=lambda: None)
        def close():
            threads.append(threading.get_ident())
            time.sleep(.025)
            if len(threads) == 1:raise RuntimeError('Helper is still finishing shutdown')
        host._cache_process = SimpleNamespace(close=close, process=process)
        host.native_reader = None
        timer = QTimer();timer.setInterval(2);timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        try:MainWindow._close_library_processes(host)
        finally:timer.stop()
        self.assertEqual(len(threads), 2)
        self.assertTrue(all(identity != gui for identity in threads))
        self.assertGreater(len(ticks), 1)
