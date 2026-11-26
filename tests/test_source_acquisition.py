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
    def test_provider_wait_reports_only_actual_bounded_retries_before_sleep(self):
        from threading import Event
        from soundbox import source_read_retry
        from soundbox.operation_control import operation_scope
        events=[];waits=[]
        source=Path('/generated/cloud/Waiting song.wav')
        def waiting(delay,cancel):
            waits.append((delay,cancel))
            self.assertEqual(events[-1],('Waiting for cloud source',len(waits),2,source.name))
        with (mock.patch.object(source_read_retry,'is_cloud_storage_source',return_value=True),
              mock.patch.object(source_read_retry,'_wait',side_effect=waiting),
              operation_scope(Event(),lambda *args:events.append(args))):
            retry=source_read_retry.ProviderReadRetry()
            self.assertTrue(retry.wait_after(OSError(81,'Generated provider readiness'),source))
            self.assertTrue(retry.wait_after(OSError(81,'Generated provider readiness'),source))
            self.assertFalse(retry.wait_after(OSError(81,'Exhausted retry budget'),source))
            self.assertFalse(source_read_retry.ProviderReadRetry().wait_after(OSError(13,'Permission denied'),source))
            with mock.patch.object(source_read_retry,'is_cloud_storage_source',return_value=False):
                self.assertFalse(source_read_retry.ProviderReadRetry().wait_after(OSError(81,'Not cloud'),source))
        self.assertEqual(waits,[(3.0,None),(3.0,None)])
        self.assertEqual(events,[('Waiting for cloud source',1,2,source.name),
                                 ('Waiting for cloud source',2,2,source.name)])

    def test_expected_digest_never_pairs_old_bytes_with_a_replacement_or_rewrite_epoch(self):
        for replacement in (False,True):
            with self.subTest(replacement=replacement),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();source=root/'song.wav';source.write_bytes(b'audio')
                original=source.stat();changed=[]
                def change(_count):
                    if changed:return
                    changed.append(True)
                    if replacement:
                        other=root/'replacement.wav';other.write_bytes(b'other');other.replace(source)
                    else:source.write_bytes(b'other')
                    os.utime(source,ns=(original.st_atime_ns,original.st_mtime_ns))
                contract=SourceContract(source,5,hashlib.sha256(b'audio').hexdigest())
                with self.assertRaisesRegex(SourceAcquisitionError,'captured bytes differ'):
                    with acquire_source(contract,root/'stage',on_bytes=change):self.fail('Stale bytes accepted')
                self.assertFalse(list((root/'stage').iterdir()))

    def test_expected_digest_retries_one_ctime_only_change_but_rejects_continued_drift(self):
        for repeated in (False,True):
            with self.subTest(repeated=repeated),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();source=root/'song.wav';source.write_bytes(b'audio')
                changes=[]
                def change(_count):
                    changes.append(True)
                    if repeated or len(changes)==1:source.chmod(0o600 if len(changes)%2 else 0o644)
                contract=SourceContract(source,5,hashlib.sha256(b'audio').hexdigest())
                if repeated:
                    with self.assertRaisesRegex(SourceAcquisitionError,'unstable'):
                        with acquire_source(contract,root/'stage',on_bytes=change):self.fail('Unstable epoch accepted')
                else:
                    with acquire_source(contract,root/'stage',on_bytes=change) as snapshot:
                        self.assertEqual(snapshot.path.read_bytes(),b'audio')
                        self.assertEqual(snapshot.observation,source.stat())
                self.assertEqual(len(changes),2)
                self.assertFalse(list((root/'stage').iterdir()))

    def test_capture_rejects_leaf_and_parent_redirects_even_to_the_captured_inode(self):
        for parent in (False,True):
            with self.subTest(parent=parent),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();folder=root/'audio';folder.mkdir()
                source=folder/'song.wav';source.write_bytes(b'audio')
                def redirect(_count):
                    if parent:
                        moved=root/'moved';folder.rename(moved);folder.symlink_to(moved,target_is_directory=True)
                    else:
                        moved=folder/'moved.wav';source.rename(moved);source.symlink_to(moved)
                contract=SourceContract(source,5,hashlib.sha256(b'audio').hexdigest())
                with self.assertRaisesRegex(SourceAcquisitionError,'identity changed'):
                    with acquire_source(contract,root/'stage',on_bytes=redirect):self.fail('Redirect accepted')
                self.assertFalse(list((root/'stage').iterdir()))

    def test_cancellation_discards_expected_digest_capture_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();source=root/'song.wav';source.write_bytes(b'audio')
            stopped=[]
            def cancel(_count):stopped.append(True);source.chmod(0o600)
            contract=SourceContract(source,5,hashlib.sha256(b'audio').hexdigest())
            with self.assertRaisesRegex(SourceAcquisitionError,'cancelled'):
                with acquire_source(contract,root/'stage',on_bytes=cancel,should_cancel=lambda:bool(stopped)):
                    self.fail('Cancelled capture accepted')
            self.assertEqual(len(stopped),1)
            self.assertFalse(list((root/'stage').iterdir()))

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

    def test_provider_retry_discards_partial_snapshot_and_still_checks_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'track.wav'
            payload = b'a' * (1024 * 1024 + 7)
            source.write_bytes(payload)
            original_open = Path.open
            opens = []

            class WakeFailure:
                def __init__(self, handle):
                    self.handle = handle
                    self.reads = 0

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.handle.close()

                def fileno(self):
                    return self.handle.fileno()

                def read(self, size):
                    self.reads += 1
                    if self.reads == 2:
                        raise OSError(81, 'Need authenticator')
                    return self.handle.read(size)

            def opening(path, *args, **kwargs):
                handle = original_open(path, *args, **kwargs)
                if path == source:
                    opens.append(path)
                    if len(opens) == 1:
                        return WakeFailure(handle)
                return handle

            with mock.patch('soundbox.source_read_retry.sys.platform', 'darwin'), \
                    mock.patch('soundbox.source_read_retry._CLOUD_STORAGE', root), \
                    mock.patch('soundbox.source_read_retry._wait'), \
                    mock.patch.object(Path, 'open', opening):
                contract = SourceContract(source, len(payload), hashlib.sha256(payload).hexdigest())
                with acquire_source(contract, root / 'stage') as snapshot:
                    self.assertEqual(snapshot.path.read_bytes(), payload)
                    self.assertEqual(len(list((root / 'stage').rglob('*.wav'))), 1)
                self.assertEqual(len(opens), 2)
                opens.clear()
                with self.assertRaisesRegex(SourceAcquisitionError, 'captured bytes differ'):
                    with acquire_source(SourceContract(source, len(payload), '0' * 64), root / 'stage'):
                        self.fail('wrong digest accepted after provider retry')
            self.assertFalse(list((root / 'stage').iterdir()))
