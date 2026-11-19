"""Cooperative local Rockbox preparation keeps sources and complete cache exact."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest import mock
import wave

from soundbox import rockbox_media as media
from soundbox.operation_control import OperationCancelled, operation_scope


class _CancelReader:
    def __init__(self, handle, event):
        self.handle = handle
        self.event = event
        self.bytes_read = 0

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()

    def read(self, size=-1):
        block = self.handle.read(size)
        self.bytes_read += len(block)
        if block:
            self.event.set()
        return block


class RockboxPrepareCancel(unittest.TestCase):
    def setUp(self):
        self.area = TemporaryDirectory(prefix='soundbox-rockbox-prepare-cancel-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.source = self.root / 'generated.wav'
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b'\x01\0\x02\0' * (512 * 1024))
        self.source_bytes = self.source.read_bytes()
        self.cache = media.RockboxMediaCache(self.root / 'cache')
        self.addCleanup(self.assert_source_unchanged)

    def assert_source_unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def cache_files(self):
        return {path.relative_to(self.cache.root): path.read_bytes()
                for path in self.cache.root.rglob('*') if path.is_file()}

    def test_cancel_before_essence_read_stops_actual_normalization_without_cache_payload(self):
        event = Event()
        original = media._hash_ranges
        calls = []
        def stop(path, ranges):
            calls.append(path)
            event.set()
            return original(path, ranges)
        with mock.patch.object(media, '_hash_ranges', side_effect=stop), operation_scope(event):
            with self.assertRaises(OperationCancelled):
                self.cache.prepare(self.source, title='Title', artist='Artist')
        self.assertEqual(len(calls), 1, 'Preparation continued to a later full essence pass')
        self.assertEqual(self.cache_files(), {})

    def test_hash_and_exact_copy_loops_stop_after_one_bounded_read(self):
        payload = self.root / 'generated.bin'
        payload.write_bytes(b'x' * (3 * 1024 * 1024 + 127))
        real_open = Path.open
        for name in ('_sha256', '_hash_ranges', '_copy_exact'):
            with self.subTest(operation=name):
                event = Event()
                readers = []
                def opened(path, *args, **kwargs):
                    handle = real_open(path, *args, **kwargs)
                    if path == payload:
                        handle = _CancelReader(handle, event)
                        readers.append(handle)
                    return handle
                destination = io.BytesIO()
                with mock.patch.object(Path, 'open', new=opened), operation_scope(event):
                    with self.assertRaises(OperationCancelled):
                        if name == '_sha256':
                            media._sha256(payload)
                        elif name == '_hash_ranges':
                            media._hash_ranges(payload, [(0, payload.stat().st_size)])
                        else:
                            with payload.open('rb') as source:
                                media._copy_exact(source, destination, payload.stat().st_size, payload)
                self.assertEqual(len(readers), 1)
                self.assertGreater(readers[0].bytes_read, 0)
                self.assertLessEqual(readers[0].bytes_read, 1024 * 1024)
                self.assertLessEqual(len(destination.getvalue()), 1024 * 1024)

    def test_cancel_during_actual_riff_rewrite_removes_unpublished_derivatives(self):
        event = Event()
        original = media._copy_exact
        readers = []
        def copying(source, destination, size, path):
            if size > 1024 * 1024:
                source = _CancelReader(source, event)
                readers.append(source)
            return original(source, destination, size, path)
        with mock.patch.object(media, '_copy_exact', side_effect=copying), operation_scope(event):
            with self.assertRaises(OperationCancelled):
                self.cache.prepare(self.source, title='Title', artist='Artist')
        self.assertEqual(len(readers), 1)
        self.assertLessEqual(readers[0].bytes_read, 1024 * 1024)
        self.assertEqual(self.cache_files(), {})

    def test_cancelled_warm_verification_preserves_complete_cached_bytes(self):
        self.cache.prepare(self.source, title='Title', artist='Artist')
        before = self.cache_files()
        self.assertTrue(before)
        event = Event()
        original = media._sha256
        def stop(path):
            event.set()
            return original(path)
        with mock.patch.object(media, '_sha256', side_effect=stop), operation_scope(event):
            with self.assertRaises(OperationCancelled):
                self.cache.prepare(self.source, title='Title', artist='Artist')
        self.assertEqual(self.cache_files(), before)

    def test_uncancelled_cold_and_warm_payloads_and_profile_stay_exact(self):
        with operation_scope(Event()):
            cold = self.cache.prepare(self.source, title='Title', artist='Artist')
        cold_bytes = cold.payload.read_bytes()
        before = self.cache_files()
        with operation_scope(Event()):
            warm = self.cache.prepare(self.source, title='Title', artist='Artist')
        self.assertEqual(warm.payload, cold.payload)
        self.assertEqual(warm.profile, cold.profile)
        self.assertEqual(warm.payload.read_bytes(), cold_bytes)
        self.assertEqual(warm.payload_sha256, hashlib.sha256(cold_bytes).hexdigest())
        self.assertEqual(media._display_tags(warm.payload), ('Title', 'Artist'))
        self.assertEqual(media._audio_essence(self.source), media._audio_essence(warm.payload))
        self.assertEqual(self.cache_files(), before)
