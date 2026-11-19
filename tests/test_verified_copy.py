"""New unindexed copies: one actual readback, exclusive publication and live FD proof."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest import mock

from soundbox import device_staging, sync, stock_ipod
from soundbox.operation_control import OperationCancelled, cancellation_disabled, operation_scope
from tests import test_stock_ipod as stock_fixture


class FileWrapper:
    def __init__(self, raw): self.raw = raw
    def __getattr__(self, key): return getattr(self.raw, key)
    def __enter__(self): self.raw.__enter__(); return self
    def __exit__(self, *args): return self.raw.__exit__(*args)


class VerifiedNewCopy(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        self.root = Path(area.name).resolve()
        self.source = self.root / 'source' / 'captured.wav'
        self.source.parent.mkdir()
        self.payload = bytes(range(251)) * 9000
        self.source.write_bytes(self.payload)
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.session = sync.DeviceSession(self.root / 'target', [self.source])
        self.destination = self.session.target / 'Music' / 'NEW.wav'

    def copy(self, **options):
        return self.session.atomic_copy_verified(self.source, self.destination,
            expected_size=len(self.payload), expected_sha256=self.sha, **options)

    def stages(self): return list(self.session.target.rglob('.soundbox-*.sbpart'))

    def test_one_source_copy_and_one_original_fd_readback_without_any_hash_reopen(self):
        reads = {'source': 0, 'device': 0}; fdopen = os.fdopen
        class Observed(FileWrapper):
            def read(self, size=-1):
                data = self.raw.read(size); reads[self.kind] += len(data); return data
        def opened(fd, mode, *args, **kwargs):
            handle = Observed(fdopen(fd, mode, *args, **kwargs))
            handle.kind = 'device' if mode == 'w+b' else 'source'
            return handle
        with mock.patch.object(device_staging.os, 'fdopen', side_effect=opened), \
                mock.patch.object(sync, '_sha256', side_effect=AssertionError('Hash reopened payload')), \
                operation_scope(Event()):
            with self.copy() as receipt:
                self.assertIsInstance(receipt, sync.VerifiedCopyReceipt)
                self.assertTrue(receipt.require_current(self.session, self.destination,
                    size=len(self.payload), sha256=self.sha))
        self.assertEqual(reads, {'source': len(self.payload), 'device': len(self.payload)})
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertEqual(self.stages(), [])
        with self.assertRaises(device_staging.PublicationOwnershipLost):
            receipt.require_current(self.session, self.destination, size=len(self.payload), sha256=self.sha)

    def test_receipt_is_exact_to_session_path_size_and_digest(self):
        other = sync.DeviceSession(self.session.target, [self.source], create=False)
        with self.copy() as receipt:
            for session, path, size, sha in (
                    (other, self.destination, len(self.payload), self.sha),
                    (self.session, self.destination.with_name('OTHER.wav'), len(self.payload), self.sha),
                    (self.session, self.destination, len(self.payload) + 1, self.sha),
                    (self.session, self.destination, len(self.payload), '0' * 64)):
                with self.subTest(path=path, size=size, sha=sha), \
                        self.assertRaises(device_staging.PublicationOwnershipLost):
                    receipt.require_current(session, path, size=size, sha256=sha)
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_bad_expected_contract_or_existing_occupant_never_publishes(self):
        for size, sha in ((True, self.sha), (len(self.payload), 'missing'),
                          (len(self.payload) + 1, self.sha), (len(self.payload), '0' * 64)):
            with self.subTest(size=size, sha=sha), self.assertRaises((ValueError, OSError)):
                with self.session.atomic_copy_verified(self.source, self.destination,
                        expected_size=size, expected_sha256=sha): self.fail('Bad copy yielded')
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.stages(), [])
        self.destination.parent.mkdir(exist_ok=True)
        self.destination.write_bytes(b'foreign existing file')
        with self.assertRaises(FileExistsError):
            with self.copy(): self.fail('Existing occupant replaced')
        self.assertEqual(self.destination.read_bytes(), b'foreign existing file')

    def test_exclusive_publication_does_not_replace_a_racing_occupant(self):
        rename = device_staging._rename_new
        def racing(parent, temporary, name):
            if name == self.destination.name:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as output: output.write(b'foreign race')
            return rename(parent, temporary, name)
        with mock.patch.object(device_staging, '_rename_new', side_effect=racing), self.assertRaises(FileExistsError):
            with self.copy(): self.fail('Race replaced')
        self.assertEqual(self.destination.read_bytes(), b'foreign race')
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertEqual(self.stages(), [])

    def mutate_output(self, *, read_error=False, corrupt=False, short=False):
        fdopen = os.fdopen
        class Output(FileWrapper):
            def write(self, data):
                if short: data = data[:max(1, len(data) // 2)]
                if corrupt and data: data = bytes([data[0] ^ 1]) + bytes(data[1:])
                return self.raw.write(data)
            def read(self, size=-1):
                if read_error: raise OSError('Generated device readback failure')
                return self.raw.read(size)
        def opened(fd, mode, *args, **kwargs):
            output = fdopen(fd, mode, *args, **kwargs)
            return Output(output) if mode == 'w+b' else output
        return mock.patch.object(device_staging.os, 'fdopen', side_effect=opened)

    def test_copy_corruption_flush_and_readback_failure_retire_only_own_unindexed_payload(self):
        for failure in ('corrupt', 'flush', 'readback'):
            with self.subTest(failure=failure):
                patch = (mock.patch.object(device_staging.os, 'fsync', side_effect=OSError('Generated flush failure'))
                         if failure == 'flush' else self.mutate_output(corrupt=failure == 'corrupt', read_error=failure == 'readback'))
                with patch, self.assertRaises(OSError):
                    with self.copy(): self.fail('Damaged output yielded')
                self.assertFalse(self.destination.exists())
                self.assertEqual(self.stages(), [])
                self.assertEqual(self.source.read_bytes(), self.payload)

    def test_same_epoch_temp_and_postrename_byte_flip_are_caught_by_real_readback(self):
        rename = device_staging._rename_new
        epoch = sync._copy_epoch
        def coarse(info):
            values = epoch(info)
            return (*values[:3], 0, 0, *values[5:])
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                def flipping(parent, temporary, name):
                    if name == self.destination.name and temporary.startswith('.soundbox-'):
                        if phase == 'after': rename(parent, temporary, name)
                        fd = os.open(name if phase == 'after' else temporary, os.O_RDWR, dir_fd=parent)
                        with os.fdopen(fd, 'r+b') as output:
                            first = output.read(1); output.seek(0); output.write(bytes([first[0] ^ 1]))
                        if phase == 'before': return rename(parent, temporary, name)
                        return None
                    return rename(parent, temporary, name)
                with mock.patch.object(device_staging, '_rename_new', side_effect=flipping), \
                        mock.patch.object(sync, '_copy_epoch', side_effect=coarse), \
                        self.assertRaisesRegex(OSError, 'failed verification'):
                    with self.copy(): self.fail('Same-epoch corruption yielded')
                self.assertFalse(self.destination.exists())
                self.assertEqual(self.stages(), [])

    def test_postpublication_replacement_is_retained_with_recovery_required(self):
        rename = device_staging._rename_new
        displaced = self.destination.with_name('OWN-DISPLACED.wav')
        def racing(parent, temporary, name):
            rename(parent, temporary, name)
            if name == self.destination.name:
                os.rename(name, displaced.name, src_dir_fd=parent, dst_dir_fd=parent)
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as output: output.write(b'foreign replacement')
        with mock.patch.object(device_staging, '_rename_new', side_effect=racing), \
                self.assertRaises(device_staging.PublicationOwnershipLost) as error:
            with self.copy(): self.fail('Replacement yielded')
        self.assertEqual(error.exception.recovery_status, 'recovery required')
        self.assertEqual(self.destination.read_bytes(), b'foreign replacement')
        self.assertEqual(displaced.read_bytes(), self.payload)

    def test_cleanup_claim_detects_late_foreign_leaf_and_restores_without_unlink(self):
        rename = device_staging._rename_new
        displaced = self.destination.with_name('OWN-DISPLACED.wav')
        def racing(parent, temporary, name):
            if temporary == self.destination.name:
                os.rename(temporary, displaced.name, src_dir_fd=parent, dst_dir_fd=parent)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as output: output.write(b'foreign at cleanup')
            return rename(parent, temporary, name)
        with self.mutate_output(read_error=True), \
                mock.patch.object(device_staging, '_rename_new', side_effect=racing), \
                self.assertRaises(device_staging.PublicationOwnershipLost):
            with self.copy(): self.fail('Read failure yielded')
        self.assertEqual(self.destination.read_bytes(), b'foreign at cleanup')
        self.assertEqual(displaced.read_bytes(), self.payload)

    def test_cleanup_restore_race_retains_both_foreign_entities_and_exact_claim(self):
        rename = device_staging._rename_new
        displaced = self.destination.with_name('OWN-DISPLACED.wav')
        def racing(parent, temporary, name):
            if temporary == self.destination.name:
                os.rename(temporary, displaced.name, src_dir_fd=parent, dst_dir_fd=parent)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as output: output.write(b'first foreign')
            elif temporary.startswith('.soundbox-rejected-') and name == self.destination.name:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as output: output.write(b'second foreign')
            return rename(parent, temporary, name)
        with self.mutate_output(read_error=True), \
                mock.patch.object(device_staging, '_rename_new', side_effect=racing), \
                self.assertRaisesRegex(device_staging.PublicationOwnershipLost, 'claimed evidence retained') as error:
            with self.copy(): self.fail('Read failure yielded')
        self.assertEqual(error.exception.recovery_status, 'recovery required')
        self.assertEqual(self.destination.read_bytes(), b'second foreign')
        self.assertEqual(displaced.read_bytes(), self.payload)
        claims = list(self.destination.parent.glob('.soundbox-rejected-*.sbpart'))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].read_bytes(), b'first foreign')

    def test_source_replacement_during_copy_preserves_new_source_and_never_publishes(self):
        fdopen = os.fdopen; source = self.source; original = self.payload
        class Input(FileWrapper):
            changed = False
            def read(self, size=-1):
                data = self.raw.read(size)
                if not self.changed:
                    self.changed = True
                    source.rename(source.with_suffix('.retained'))
                    source.write_bytes(b'new user source')
                return data
        def opened(fd, mode, *args, **kwargs):
            raw = fdopen(fd, mode, *args, **kwargs)
            return Input(raw) if mode == 'rb' else raw
        with mock.patch.object(sync.os, 'fdopen', side_effect=opened), self.assertRaisesRegex(OSError, 'Source payload changed'):
            with self.copy(): self.fail('Changed source yielded')
        self.assertEqual(self.source.read_bytes(), b'new user source')
        self.assertEqual(self.source.with_suffix('.retained').read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_cancel_before_publish_and_during_readback_clean_own_file_without_verified_endpoint(self):
        for phase in ('copying', 'verifying'):
            with self.subTest(phase=phase):
                stop = Event(); events = []
                def progress(name, done, total):
                    events.append((name, done, total))
                    if name == phase and done == 0: stop.set()
                with operation_scope(stop), self.assertRaises(OperationCancelled):
                    with self.copy(on_progress=progress): self.fail('Cancelled copy yielded')
                self.assertFalse(self.destination.exists())
                self.assertNotIn(('verifying', len(self.payload), len(self.payload)), events)
                self.assertEqual(self.stages(), [])

    def test_short_writes_and_throttled_real_progress_and_recovery_cancel_policy(self):
        events = []; stop = Event(); stop.set()
        with operation_scope(stop), cancellation_disabled(), self.mutate_output(short=True), \
                mock.patch.object(sync.time, 'monotonic', return_value=42.0):
            with self.copy(on_progress=lambda *row: events.append(row)): pass
        size = len(self.payload)
        self.assertEqual(events, [('copying', 0, size), ('copying', size, size),
                                  ('verifying', 0, size), ('verifying', size, size)])
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertTrue(all(type(value) is int for row in events for value in row[1:]))


class VerifiedStockCopy(unittest.TestCase):
    def setUp(self):
        self.fixture = stock_fixture.StockIpodSync(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def plan(self):
        f = self.fixture
        return stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video')

    def test_stock_new_copy_does_not_reopen_published_audio_for_a_second_hash(self):
        plan = self.plan(); source_hash = stock_ipod._sha256; f = self.fixture
        def guarded(path):
            self.assertFalse(Path(path).is_relative_to(plan.target / 'iPod_Control/Music'))
            return source_hash(path)
        events = []
        with mock.patch.object(stock_ipod, '_sha256', side_effect=guarded), \
                operation_scope(Event(), lambda *row: events.append(row)):
            result = stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(result.copied_tracks, 1)
        for item in plan.copies:
            self.assertEqual(hashlib.sha256((f.ipod / item.relative_path).read_bytes()).hexdigest(), item.sha256)
        child = [(name, done, total) for name, done, total, *_ in events if name in ('iTunes copying', 'iTunes verifying')]
        self.assertTrue(child)
        self.assertEqual(child[-1], ('iTunes verifying', 2 * plan.bytes_to_copy, 2 * plan.bytes_to_copy))
        self.assertEqual([done for _, done, _ in child], sorted(done for _, done, _ in child))

    def test_stock_missing_receipt_refuses_progress_and_retains_native_database(self):
        plan = self.plan(); f = self.fixture; events = []
        database = f.ipod / plan.database_relative_path; before = database.read_bytes()
        original = sync.DeviceSession.atomic_copy_verified
        @contextmanager
        def missing(session, *args, **kwargs):
            with original(session, *args, **kwargs): yield None
        with mock.patch.object(sync.DeviceSession, 'atomic_copy_verified', missing), \
                operation_scope(Event(), lambda *row: events.append(row)), \
                self.assertRaises(stock_ipod.StockIpodError) as error:
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(error.exception.recovery_status, 'recovery required')
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse((f.ipod / plan.copies[0].relative_path).exists())
        self.assertEqual([done for name, done, _, _ in events if name == 'iTunes media'], [0])
