"""Bounded manifest observations against disposable filesystem fixtures."""

import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest import mock

from soundbox import device_manifest_capture as capture
from soundbox.operation_control import operation_scope, OperationCancelled


class DeviceManifestCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / ".soundbox/manifest.json"
        self.path.parent.mkdir()
        self.path.write_bytes(b'{"exact": "bytes"}\n')

    def test_exact_bytes_read_once_with_no_target_changes(self):
        expected = self.path.read_bytes()
        original_open = os.open
        with mock.patch.object(capture.os, "open", wraps=original_open) as opened, \
                mock.patch.object(capture, "record_metric") as metric:
            self.assertEqual(capture.read_device_manifest(self.root), expected)
        self.assertEqual(sum(call.args[0] == "manifest.json" for call in opened.call_args_list), 1)
        self.assertEqual(sum(call.args[1] for call in metric.call_args_list
                             if call.args[0] == "device_manifest_capture_bytes"), len(expected))
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_absent_state_returns_none_without_creating_it(self):
        other = self.root / "empty-device"
        other.mkdir()
        self.assertIsNone(capture.read_device_manifest(other))
        self.assertEqual(list(other.iterdir()), [])

    def test_symlink_leaf_is_refused_even_within_device(self):
        original = self.root / "original.json"
        self.path.rename(original)
        self.path.symlink_to(original)
        with self.assertRaisesRegex(capture.ManifestCaptureError, "regular file"):
            capture.read_device_manifest(self.root)

    def test_oversized_manifest_is_refused_before_payload_open(self):
        with mock.patch.object(capture, "MAX_MANIFEST_BYTES", 4), \
                mock.patch.object(capture, "record_metric") as metric:
            with self.assertRaisesRegex(capture.ManifestCaptureError, "read limit"):
                capture.read_device_manifest(self.root)
        self.assertFalse(metric.called)

    def _replace_reader(self, mutation):
        real_fdopen = os.fdopen

        class MutatingReader:
            def __init__(self, descriptor, mode):
                self.handle = real_fdopen(descriptor, mode)
                self.first = True

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.handle.close()

            def fileno(self):
                return self.handle.fileno()

            def read(self, size):
                if self.first:
                    self.first = False
                    mutation()
                return self.handle.read(size)

        return mock.patch.object(capture.os, "fdopen", MutatingReader)

    def test_growth_during_capture_reads_only_limit_plus_one(self):
        def grow():
            with self.path.open("ab") as handle:
                handle.write(b"x" * 100)

        with mock.patch.object(capture, "MAX_MANIFEST_BYTES", 32), \
                self._replace_reader(grow), mock.patch.object(capture, "record_metric") as metric:
            with self.assertRaisesRegex(capture.ManifestCaptureError, "read limit"):
                capture.read_device_manifest(self.root)
        self.assertEqual(sum(call.args[1] for call in metric.call_args_list
                             if call.args[0] == "device_manifest_capture_bytes"), 33)

    def test_same_size_entry_replacement_during_read_is_refused(self):
        replacement = self.root / "replacement.json"
        replacement.write_bytes(b"x" * self.path.stat().st_size)
        with self._replace_reader(lambda: os.replace(replacement, self.path)):
            with self.assertRaisesRegex(capture.ManifestCaptureError, "changed during capture"):
                capture.read_device_manifest(self.root)

    def test_cancelled_capture_opens_no_payload(self):
        stop = Event()
        stop.set()
        with operation_scope(stop), mock.patch.object(capture.os, "open") as opened:
            with self.assertRaises(OperationCancelled):
                capture.read_device_manifest(self.root)
        opened.assert_not_called()
