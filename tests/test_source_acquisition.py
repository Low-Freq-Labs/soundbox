"""Cloud capture failure injection without cloud accounts or removable hardware."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soundbox.source_acquisition import (
    SourceAcquisitionError, SourceContract, acquire_source,
)
from soundbox.rockbox_media import RockboxMediaCache, RockboxMediaError
from tests.test_rockbox_media import _tagged_mp3


class AcquisitionTests(unittest.TestCase):
    def test_exact_digest_accepts_eviction_metadata_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "CRANKDAT.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")
            plan = cache.plan(source, title="Live", artist="CRANKDAT")
            os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1))
            prepared = cache.materialize(plan)
            self.assertEqual(prepared.payload_sha256, plan.payload_sha256)
            cache.discard(prepared)
            self.assertFalse(list(cache.root.rglob("*.mp3")))

    def test_preparation_and_copy_never_reopen_canonical_after_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")
            opened = []
            actual_open = Path.open

            def guarded(path, *args, **kwargs):
                if path == source:
                    opened.append(path)
                    if len(opened) > 1:
                        raise OSError("cloud evicted after capture")
                return actual_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", guarded):
                prepared = cache.prepare(source, title="New", artist="Artist")
                copied = root / "device.mp3"
                copied.write_bytes(prepared.payload.read_bytes())
                self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(),
                                 prepared.payload_sha256)
            self.assertEqual(len(opened), 1)
            cache.discard(prepared)

    def test_same_size_edit_with_preserved_mtime_fails_exact_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")
            plan = cache.plan(source, title="New", artist="Artist")
            stat = source.stat()
            data = bytearray(source.read_bytes())
            data[-1] ^= 1
            source.write_bytes(data)
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(RockboxMediaError):
                cache.materialize(plan)
            self.assertFalse(list(cache.root.rglob("*.mp3")))

    def test_unknown_digest_allows_one_hydration_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.wav"
            source.write_bytes(b"audio")
            calls = []

            def hydrate(count):
                calls.append(count)
                if len(calls) == 1:
                    stat = source.stat()
                    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

            with acquire_source(SourceContract(source), root / "stage", on_bytes=hydrate) as snap:
                self.assertEqual(snap.path.read_bytes(), b"audio")
            self.assertEqual(calls, [5, 5])
            self.assertFalse(list((root / "stage").iterdir()))

    def test_continuing_change_and_authentication_failure_leave_no_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.wav"
            source.write_bytes(b"audio")

            def change(_count):
                stat = source.stat()
                os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

            with self.assertRaisesRegex(SourceAcquisitionError, "unstable"):
                with acquire_source(SourceContract(source), root / "stage", on_bytes=change):
                    self.fail("unstable capture accepted")
            actual_open = Path.open

            def denied(path, *args, **kwargs):
                if path == source:
                    raise PermissionError("account unavailable")
                return actual_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", denied), self.assertRaises(SourceAcquisitionError):
                with acquire_source(SourceContract(source), root / "stage"):
                    self.fail("authentication failure accepted")
            self.assertFalse(list((root / "stage").iterdir()))

    def test_receipt_reuses_preparation_and_corruption_forces_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.mp3"
            _tagged_mp3(source)
            cache = RockboxMediaCache(root / "cache")
            first = cache.plan(source, title="T", artist="A")
            with mock.patch.object(cache, "prepare", side_effect=AssertionError("unexpected capture")):
                self.assertEqual(cache.plan(source, title="T", artist="A"), first)
            receipt = next((cache.root / "payload-receipts-v1").glob("*.json"))
            receipt.write_text('{"broken":true}')
            with mock.patch.object(cache, "prepare", wraps=cache.prepare) as prepare:
                self.assertEqual(cache.plan(source, title="T", artist="A"), first)
            self.assertEqual(prepare.call_count, 1)
            self.assertFalse(list(cache.root.rglob("*.mp3")))

    def test_insufficient_space_does_not_open_source(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "track.wav"
            source.write_bytes(b"audio")
            with mock.patch("soundbox.source_acquisition.os.statvfs",
                            return_value=SimpleNamespace(f_bavail=0, f_frsize=1)):
                with self.assertRaisesRegex(SourceAcquisitionError, "space"):
                    with acquire_source(SourceContract(source), root / "stage"):
                        self.fail("capture should not start")
            self.assertFalse(list((root / "stage").iterdir()))
