"""Real export/resync worker cancellation through bounded device copy I/O."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import hashlib
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.workers import ExportWorker, ResyncWorker
from soundbox import engine, manifest, sync
from soundbox.device_operations import (DeviceCommand, DeviceOperationCoordinator,
                                       DeviceSessionIdentity, Phase)
from soundbox.device_staging import copy_stream
from soundbox.library import Library, Playlist, Track
from soundbox.operation_control import checkpoint
from soundbox.report import SyncReport


class _HeldReader:
    """Keep a genuine first chunk in flight while the GUI cancels the owner."""
    def __init__(self, handle, entered, release):
        self.handle = handle
        self.entered = entered
        self.release = release
        self.bytes_read = 0

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def read(self, size=-1):
        block = self.handle.read(size)
        self.bytes_read += len(block)
        if not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError('Generated read was not released')
        return block


class ExportCancelIO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = TemporaryDirectory(prefix='soundbox-export-cancel-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.source = self.root / 'sources'
        self.source.mkdir()
        self.payloads = {}
        for identity, title, size in (('1', 'First', 32768),
                                      ('2', 'Second', 2 * 1024 * 1024 + 127),
                                      ('3', 'Retained', 16384)):
            path = self.source / (title + '.flac')
            path.write_bytes(identity.encode() * size)
            self.payloads[identity] = path
        self.catalog = self.source / 'catalog.xml'
        self.catalog.write_bytes(b'generated source identity')
        self.source_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in self.source.iterdir()}
        self.entered = Event()
        self.release = Event()
        self.workers = []
        self.addCleanup(self.retire)

    def retire(self):
        self.release.set()
        for worker in self.workers:
            worker.cancel()
            self.assertTrue(worker.wait(3000), 'Generated worker did not retire')
        self.app.processEvents()
        for worker in self.workers:
            worker.deleteLater()
        self.app.processEvents()
        self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in self.source.iterdir()}, self.source_before)

    def prepare(self, worker_class, suffix):
        target = self.root / suffix
        target.mkdir()
        tracks = {identity: Track(identity, path.stem, 'Artist', path.as_uri())
                  for identity, path in self.payloads.items()}
        playlist = Playlist('Test', [], ['1', '2'], pid='test')
        library = Library(tracks, [playlist], self.catalog, source_kind='fixture')
        if worker_class is ResyncWorker:
            playlist.keys = ['3']
            report = engine.export_playlists(library, [playlist], target, clean=False)
            self.assertFalse(report.cancelled)
            self.assertFalse(any(row.errors for row in report.playlists))
            playlist.keys = ['1', '2']
        (target / 'foreign.txt').write_bytes(b'preserve unrelated device data')
        if worker_class is ExportWorker:
            worker = ExportWorker(library)
            worker.add(str(target), [playlist], mount_token=target.stat().st_dev)
        else:
            worker = ResyncWorker(library, str(target))
        self.workers.append(worker)
        coordinator = DeviceOperationCoordinator()
        identity = DeviceSessionIdentity(str(target), target.stat().st_dev)
        coordinator.observe(identity)
        command = DeviceCommand(identity, 'export' if worker_class is ExportWorker else 'sync')
        state = coordinator.begin(command, worker, Phase.EXECUTING)
        worker.device_command = command
        worker.cancellation = state.cancellation
        self.assertIs(worker.cancellation, state.cancellation)
        return target, library, playlist, worker, coordinator

    def report(self, worker):
        if isinstance(worker, ExportWorker):
            self.assertEqual(len(worker.result), 1)
            _target, report = worker.result[0]
        else:
            report = worker.result
        self.assertIsInstance(report, SyncReport)
        return report

    def test_copy_stream_cancellation_preserves_honest_partial_exports(self):
        for worker_class in (ExportWorker, ResyncWorker):
            for via_coordinator in (False, True):
                with self.subTest(worker=worker_class.__name__, coordinator=via_coordinator):
                    self.entered.clear()
                    self.release.clear()
                    target, library, playlist, worker, coordinator = self.prepare(
                        worker_class, f'{worker_class.__name__}-{via_coordinator}')
                    copies = []
                    readers = []
                    def held_copy(source, output):
                        copies.append(source.name)
                        if len(copies) == 2:
                            reader = _HeldReader(source, self.entered, self.release)
                            readers.append(reader)
                            return copy_stream(reader, output)
                        return copy_stream(source, output)
                    with mock.patch.object(sync, 'copy_stream', side_effect=held_copy):
                        worker.start()
                        try:
                            self.assertTrue(self.entered.wait(3), 'Copy did not use the cancellable stream')
                            if via_coordinator:
                                coordinator.cancel()
                                self.assertFalse(worker._stop)
                            else:
                                worker.cancel()
                            self.assertTrue(worker.cancellation.is_set())
                            self.release.set()
                            self.assertTrue(worker.wait(5000))
                        finally:
                            self.release.set()
                            worker.wait(5000)
                    report = self.report(worker)
                    self.assertTrue(report.cancelled)
                    self.assertFalse(any(row.errors for row in report.playlists))
                    self.assertEqual(len(readers), 1)
                    self.assertGreater(readers[0].bytes_read, 0)
                    self.assertLess(readers[0].bytes_read, self.payloads['2'].stat().st_size)
                    state = manifest.load(target)
                    self.assertEqual(state['playlists'][manifest.playlist_key(playlist)]['sig'], 'partial')
                    self.assertLess(state['completed_generation'], state['generation'])
                    media = {row['source']: path for path, row in state['files'].items()
                             if row['kind'] == 'track'}
                    self.assertIn(str(self.payloads['1']), media)
                    self.assertNotIn(str(self.payloads['2']), media)
                    self.assertEqual((target / media[str(self.payloads['1'])].lstrip('/')).read_bytes(),
                                     self.payloads['1'].read_bytes())
                    self.assertFalse(list(target.rglob('Second.flac')))
                    for path in target.rglob('*.m3u8'):
                        text = path.read_text()
                        self.assertIn('# CANCELLED:', text)
                        entries = [line for line in text.splitlines() if line and not line.startswith('#')]
                        self.assertEqual(len(entries), 1)
                        self.assertTrue(entries[0].endswith('First.flac'))
                    self.assertFalse(list(target.rglob('*.sbpart')))
                    self.assertEqual((target / 'foreign.txt').read_bytes(), b'preserve unrelated device data')
                    if worker_class is ResyncWorker:
                        self.assertTrue(list(target.rglob('Retained.flac')), 'Cancelled Sync pruned retained media')
                    if worker_class is ExportWorker:
                        resumed = ExportWorker(library)
                        resumed.add(str(target), [playlist])
                    else:
                        resumed = ResyncWorker(library, str(target))
                    self.workers.append(resumed)
                    resumed.start()
                    self.assertTrue(resumed.wait(5000))
                    self.assertFalse(self.report(resumed).cancelled)
                    completed = manifest.load(target)
                    self.assertNotEqual(completed['playlists'][manifest.playlist_key(playlist)]['sig'], 'partial')
                    self.assertEqual(completed['completed_generation'], completed['generation'])
                    self.assertTrue(list(target.rglob('First.flac')))
                    self.assertTrue(list(target.rglob('Second.flac')))
                    self.assertFalse(list(target.rglob('*.sbpart')))

    def test_preplanning_cancel_is_a_cancelled_report_without_device_io(self):
        for worker_class, function in ((ExportWorker, 'export_playlists'),
                                       (ResyncWorker, 'resync_device')):
            with self.subTest(worker=worker_class.__name__):
                target, _library, _playlist, worker, _coordinator = self.prepare(
                    worker_class, f'preplanning-{worker_class.__name__}')
                before = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                def before_write(*_args, **_kwargs):
                    worker.cancel()
                    checkpoint()
                    self.fail('Cancellation checkpoint did not run')
                with mock.patch.object(engine, function, side_effect=before_write):
                    worker.start()
                    self.assertTrue(worker.wait(3000))
                self.assertTrue(self.report(worker).cancelled)
                self.assertEqual({p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}, before)

    def test_coordinator_cancel_before_start_reports_every_admitted_target(self):
        for worker_class, function in ((ExportWorker, 'export_playlists'),
                                       (ResyncWorker, 'resync_device')):
            with self.subTest(worker=worker_class.__name__):
                target, _library, _playlist, worker, coordinator = self.prepare(
                    worker_class, f'prestart-{worker_class.__name__}')
                before = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                coordinator.cancel()
                with mock.patch.object(engine, function) as engine_call:
                    worker.start()
                    self.assertTrue(worker.wait(3000))
                engine_call.assert_not_called()
                self.assertTrue(self.report(worker).cancelled)
                self.assertEqual({p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}, before)

    def test_adopting_coordinator_event_cannot_revive_a_cancelled_worker(self):
        for worker_class in (ExportWorker, ResyncWorker):
            with self.subTest(worker=worker_class.__name__):
                _target, _library, _playlist, worker, _coordinator = self.prepare(
                    worker_class, f'adoption-{worker_class.__name__}')
                worker.cancel()
                accepted = Event()
                worker.cancellation = accepted
                self.assertTrue(accepted.is_set())
                self.assertTrue(worker._should_cancel())
                worker.start()
                self.assertTrue(worker.wait(3000))
                self.assertTrue(self.report(worker).cancelled)

    def test_cancelled_growable_queue_reports_all_admitted_targets_without_io(self):
        target, library, playlist, worker, _coordinator = self.prepare(ExportWorker, 'queued-first')
        other = self.root / 'queued-second'
        worker.add(str(other), [playlist], library=library)
        worker.add(str(target), [playlist], library=library)
        worker.cancel()
        with mock.patch.object(engine, 'export_playlists') as engine_call:
            worker.start()
            self.assertTrue(worker.wait(3000))
        engine_call.assert_not_called()
        self.assertEqual([device for device, _report in worker.result],
                         [str(target), str(other)])
        self.assertTrue(all(isinstance(report, SyncReport) and report.cancelled
                            for _device, report in worker.result))
        self.assertFalse(other.exists())

    def test_uncancelled_workers_publish_complete_exports(self):
        for worker_class in (ExportWorker, ResyncWorker):
            with self.subTest(worker=worker_class.__name__):
                target, _library, playlist, worker, _coordinator = self.prepare(
                    worker_class, f'complete-{worker_class.__name__}')
                worker.start()
                self.assertTrue(worker.wait(5000))
                report = self.report(worker)
                self.assertFalse(report.cancelled)
                self.assertFalse(any(row.errors for row in report.playlists))
                state = manifest.load(target)
                self.assertNotEqual(state['playlists'][manifest.playlist_key(playlist)]['sig'], 'partial')
                self.assertEqual(state['completed_generation'], state['generation'])
                self.assertTrue(list(target.rglob('First.flac')))
                self.assertTrue(list(target.rglob('Second.flac')))
                self.assertFalse(list(target.rglob('*.sbpart')))
                self.assertEqual((target / 'foreign.txt').read_bytes(), b'preserve unrelated device data')
