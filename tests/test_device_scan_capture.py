"""Detected targets request playlist snapshots; empty scans do no export work."""
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt

from desktop.workers import DeviceScanWorker


class DeviceScanCapture(unittest.TestCase):
    def setUp(self):
        self.path = '/synthetic/soundbox-test-usb'
        self.profile = SimpleNamespace(kind='rockbox', has_stock_ipod=False)
        self.enterContext(mock.patch('desktop.workers.list_devices', return_value=[
            ('USB', self.path, 1000)]))
        self.enterContext(mock.patch('desktop.workers.device_profile', return_value=self.profile))
        self.enterContext(mock.patch('desktop.devices.discovery_needs_retry', return_value=False))
        self.enterContext(mock.patch('desktop.usb_device_view.read_usb_snapshot',
                                     return_value=SimpleNamespace(mount_token=0)))

    def _provide(self, worker, **kwargs):
        worker.provide_playlists((), lib=object(), artist_overrides={},
                                 title_overrides={}, **kwargs)

    def test_empty_scan_never_requests_playlist_capture(self):
        worker = DeviceScanWorker(None, defer_playlists=True)
        capture = mock.Mock()
        worker.playlists_requested.connect(capture, Qt.DirectConnection)
        with mock.patch('desktop.workers.list_devices', return_value=[]):
            worker.run()
        capture.assert_not_called()
        self.assertEqual(worker.results, [])
        self.assertFalse(worker.stock_detected)

    def test_detected_target_waits_for_one_complete_snapshot_before_drift(self):
        worker = DeviceScanWorker(None, defer_playlists=True)
        library, extra = object(), object()
        events = []
        worker.discovered.connect(lambda rows: events.append('detected'), Qt.DirectConnection)

        def provide(request):
            self.assertIs(request, worker)
            events.append('captured')
            request.provide_playlists([extra], lib=library,
                artist_overrides={'one': 'Artist'}, title_overrides={'one': 'Title'})

        def drift(lib, path, extras, **kwargs):
            self.assertEqual(events, ['detected', 'captured'])
            self.assertIs(lib, library)
            self.assertEqual(path, self.path)
            self.assertEqual(extras, [extra])
            self.assertEqual(kwargs, {'artist_overrides': {'one': 'Artist'},
                                      'title_overrides': {'one': 'Title'}})
            return SimpleNamespace(count=lambda: 2)

        worker.playlists_requested.connect(provide, Qt.DirectConnection)
        with mock.patch('desktop.workers.engine.device_drift', side_effect=drift) as check:
            worker.run()
        check.assert_called_once()
        self.assertEqual(worker.results[0][3], 2)
        self.assertFalse(worker.stock_detected)

    def test_stock_detection_precedes_capture_and_accepts_lazy_shadow_store(self):
        profile = SimpleNamespace(kind='stock_ipod', has_stock_ipod=True)
        shadow = object()
        worker = DeviceScanWorker(None, defer_playlists=True)

        def provide(request):
            self.assertTrue(request.stock_detected)
            request.provide_playlists((), lib=object(), artist_overrides={},
                title_overrides={}, stock_shadow_store=shadow)

        worker.playlists_requested.connect(provide, Qt.DirectConnection)
        with (
            mock.patch('desktop.workers.device_profile', return_value=profile),
            mock.patch.object(worker, '_effective_profile', return_value=profile),
            mock.patch('desktop.workers.engine.device_drift',
                       return_value=SimpleNamespace(count=lambda: 2)),
        ):
            worker.run()
        self.assertIs(worker.stock_shadow_store, shadow)
        self.assertEqual(worker.results[0][3], 2)

    def test_capture_failure_keeps_pending_status_and_snapshot(self):
        worker = DeviceScanWorker(object(), defer_playlists=True)
        worker.playlists_requested.connect(
            lambda request: self._provide(request, error=ValueError('capture failed')),
            Qt.DirectConnection)
        with mock.patch('desktop.workers.engine.device_drift') as drift:
            worker.run()
        drift.assert_not_called()
        self.assertIsNone(worker.results[0][3])
        self.assertIn(self.path, worker.usb_snapshots)

    def _waiting_worker(self):
        worker = DeviceScanWorker(None, defer_playlists=True)
        requested = Event()
        worker.playlists_requested.connect(lambda _: requested.set(), Qt.DirectConnection)
        worker.start()
        self.addCleanup(lambda: (worker.cancel(), worker.wait()))
        self.assertTrue(requested.wait(2), 'Worker never requested playlist capture')
        return worker

    def test_cancel_wakes_capture_without_gui_delivery(self):
        worker = self._waiting_worker()
        worker.cancel()
        self.assertTrue(worker.wait(1000), 'Cancelled capture remained blocked')
        self.assertIsNone(worker.results)

    def test_coordinator_cancellation_stops_capture_without_gui_delivery(self):
        worker = self._waiting_worker()
        worker.cancellation.set()
        self.assertTrue(worker.wait(1000), 'Coordinator cancellation remained blocked')
        self.assertIsNone(worker.results)

    def test_interruption_stops_capture_without_gui_delivery(self):
        worker = self._waiting_worker()
        worker.requestInterruption()
        self.assertTrue(worker.wait(1000), 'Interrupted capture remained blocked')
        self.assertIsNone(worker.results)

    def test_direct_worker_keeps_explicit_playlists_without_capture(self):
        library, extra = object(), object()
        worker = DeviceScanWorker(library, [extra])
        capture = mock.Mock()
        worker.playlists_requested.connect(capture, Qt.DirectConnection)
        with mock.patch('desktop.workers.engine.device_drift', return_value=None) as drift:
            worker.run()
        capture.assert_not_called()
        self.assertIs(drift.call_args.args[0], library)
        self.assertEqual(drift.call_args.args[2], [extra])
        self.assertEqual(worker.results[0][3], 0)
