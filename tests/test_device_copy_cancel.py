"""Cooperative copy cancellation preserves previous bytes and recovery authority."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest import mock

from soundbox import device_staging
from soundbox.operation_control import (
    OperationCancelled, cancellation_disabled, checkpoint, operation_scope,
    phase_progress, record_metric,
)
from soundbox.sync import DeviceSession


class DeviceCopyCancellation(unittest.TestCase):
    def test_cancelled_stream_stops_at_a_bounded_read_without_native_copy(self):
        stop = Event()

        class Source(io.BytesIO):
            def read(self, size=-1):
                block = super().read(size)
                stop.set()
                return block

        source, output = Source(b'x' * (3 * 1024 * 1024)), io.BytesIO()
        with operation_scope(stop), mock.patch('posix._fcopyfile') as native:
            with self.assertRaises(OperationCancelled):
                device_staging.copy_stream(source, output)
        native.assert_not_called()
        self.assertEqual(source.tell(), 1024 * 1024)
        self.assertEqual(output.getvalue(), b'')

    def test_cancel_after_staging_flush_preserves_destination_and_removes_owned_stage(self):
        with tempfile.TemporaryDirectory() as area:
            target = Path(area).resolve()
            destination = target / 'selected'
            destination.write_bytes(b'previous generation')
            session = DeviceSession(target, [], create=False)
            stop = Event()
            sync = os.fsync

            def cancel_after_flush(fd):
                sync(fd)
                stop.set()

            with operation_scope(stop), mock.patch.object(device_staging.os, 'fsync', side_effect=cancel_after_flush):
                with self.assertRaises(OperationCancelled):
                    session.atomic_write(destination, b'cancelled replacement')
            self.assertEqual(destination.read_bytes(), b'previous generation')
            self.assertEqual(list(target.glob('.soundbox-*.sbpart')), [])

    def test_recovery_finishes_with_progress_and_metrics_then_restores_cancel(self):
        with tempfile.TemporaryDirectory() as area:
            target = Path(area).resolve()
            session = DeviceSession(target, [], create=False)
            stop = Event(); stop.set()
            progress = []
            with self.assertLogs('soundbox', level='INFO') as logs:
                with operation_scope(stop, lambda *values: progress.append(values)):
                    record_metric('before_recovery')
                    with cancellation_disabled():
                        session.atomic_write(target / 'database', b'fully restored database')
                        phase_progress('Recovering', 1, 1, 'database')
                        record_metric('restored_database')
                    with self.assertRaises(OperationCancelled):
                        checkpoint()
            self.assertEqual((target / 'database').read_bytes(), b'fully restored database')
            self.assertEqual(progress, [('Recovering', 1, 1, 'database')])
            self.assertEqual(len(logs.output), 1)
            self.assertIn('before_recovery', logs.output[0])
            self.assertIn('restored_database', logs.output[0])

    def test_copy_rejects_changed_captured_digest_before_publication(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'sources' / 'captured'
            source.parent.mkdir()
            source.write_bytes(b'changed payload')
            session = DeviceSession(root / 'device', [source])
            destination = session.target / 'selected'
            destination.write_bytes(b'previous generation')
            with self.assertRaisesRegex(OSError, 'Source payload changed'):
                session.atomic_copy(source, destination,
                                    expected_sha256=hashlib.sha256(b'captured payload').hexdigest())
            self.assertEqual(destination.read_bytes(), b'previous generation')
            self.assertEqual(source.read_bytes(), b'changed payload')
            self.assertEqual(list(session.target.glob('.soundbox-*.sbpart')), [])
