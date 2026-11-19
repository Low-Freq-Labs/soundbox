"""Confirmed native entity deletion never audits song payloads or Mac ownership."""
import builtins
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import threading
import unittest
from unittest import mock

from PIL import Image

from clickwheel.ipod import get_ipod_tracks, read_ipod
from clickwheel.ipod.itunesdb_writer import PlaylistInfo, TrackInfo, write_itunesdb, ChecksumType
from clickwheel.ipod.ipod_models import capabilities_for_family_gen

from soundbox import stock_delete, stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.sync import DeviceSession
from tests import test_stock_ipod as fixtures


class StockDeleteEntities(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StockIpodSync('test_playlist_delete_removes_only_newly_unreferenced_track_and_media')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.ipod
        self.media = self.root / 'iPod_Control/Music/F00/AAAA.wav'
        self.database = self.root / 'iPod_Control/iTunes/iTunesDB'

    def plan(self, ids=(202,)):
        return stock_ipod.plan_stock_ipod_playlist_delete(
            self.root, list(ids), model_number='', filesystem='fat32', usb_family='iPod Video',
            stock_art_cache=self.fixture.stock_art_cache)

    @contextmanager
    def no_audio_reads(self):
        raw_path_open, raw_open = Path.open, builtins.open
        root = self.root.resolve() / 'iPod_Control/Music'

        def refuse(path):
            if isinstance(path, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(path)).absolute()
                if candidate.is_relative_to(root):
                    raise AssertionError(f'Delete attempted an audio payload read: {candidate}')

        def path_open(path, *args, **kwargs):
            refuse(path)
            return raw_path_open(path, *args, **kwargs)

        def opened(path, *args, **kwargs):
            refuse(path)
            return raw_open(path, *args, **kwargs)

        with mock.patch.object(Path, 'open', path_open), mock.patch.object(builtins, 'open', opened):
            yield

    def test_native_song_with_no_mac_record_deletes_without_payload_reads(self):
        self.fixture.library.tracks_by_id.clear()
        original_mac = self.fixture.existing_source.read_bytes()
        with self.no_audio_reads():
            plan = self.plan()
            self.assertIsInstance(plan.media_deletes[0], stock_delete.IndexedFile)
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertFalse(result.cleanup_failures)
        self.assertFalse(self.media.exists())
        self.assertEqual(get_ipod_tracks(read_ipod(self.root)), [])
        self.assertEqual(self.fixture.existing_source.read_bytes(), original_mac)

    def test_large_sparse_native_playlist_has_no_audio_hash_pass(self):
        self.database.with_name('Play Counts').unlink()
        tracks = []
        size = 64 * 1024 * 1024
        for index in range(212):
            path = self.media.parent / f'GENERATED{index:04d}.wav'
            with path.open('wb') as handle:
                handle.truncate(size)
            tracks.append(TrackInfo(title=f'Native {index}', dbid=1000 + index,
                location=f':iPod_Control:Music:F00:{path.name}', size=size, length=1000,
                filetype='wav', sample_rate=44100))
        self.media.unlink()
        self.assertTrue(write_itunesdb(self.root, tracks, backup=False,
            force_checksum=ChecksumType.NONE,
            capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen'),
            playlists=[PlaylistInfo('Native device only', [t.dbid for t in tracks], playlist_id=202)]))
        with self.no_audio_reads():
            plan = self.plan()
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 212)
        self.assertEqual(result.deleted_bytes, 212 * size)
        self.assertFalse(result.cleanup_failures)

    def test_changed_replaced_or_resized_entity_refuses_before_catalog_write(self):
        plan = self.plan()
        before = self.database.read_bytes()
        current = self.media.read_bytes()
        replacement = self.media.with_name('replacement')
        replacement.write_bytes(current)
        old = self.media.stat()
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        os.replace(replacement, self.media)
        with self.assertRaisesRegex(stock_delete.IndexedDeleteError, 'changed after confirmation'):
            stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.media.read_bytes(), current)
        resized = self.plan()
        with self.media.open('ab') as output:
            output.write(b'changed size')
        with self.assertRaisesRegex(stock_delete.IndexedDeleteError, 'changed after confirmation'):
            stock_ipod.commit_stock_ipod_playlist_delete(resized)
        self.assertEqual(self.database.read_bytes(), before)

    def test_parent_redirect_and_hardlinked_original_are_not_admitted(self):
        protected = self.fixture.base / 'protected.wav'
        os.link(self.media, protected)
        with self.assertRaisesRegex(stock_delete.IndexedDeleteError, 'one link'):
            self.plan()
        protected.unlink()
        folder = self.media.parent
        folder.rename(folder.with_name('retired'))
        folder.symlink_to(folder.with_name('retired'), target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)):
            self.plan()

    def test_cancel_before_plan_or_before_database_publication_keeps_originals(self):
        event = threading.Event();event.set()
        before = self.database.read_bytes();media = self.media.read_bytes()
        with operation_scope(event), self.assertRaises(OperationCancelled):
            self.plan()
        event.clear();plan = self.plan()

        def progress(phase, done, _total, _label):
            if phase == 'Saving iPod playlists' and done == 0:
                event.set()

        with operation_scope(event, progress), self.assertRaises(OperationCancelled):
            stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.media.read_bytes(), media)

    def test_cancel_inside_database_commit_finishes_it_and_retains_pending_cleanup(self):
        plan = self.plan();event = threading.Event()
        write = DeviceSession.atomic_write

        def cancel_during_database(session, destination, data):
            if Path(destination).name == 'iTunesDB':
                event.set()
            return write(session, destination, data)

        with operation_scope(event), mock.patch.object(DeviceSession, 'atomic_write', cancel_during_database):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertTrue(result.cleanup_cancelled)
        self.assertEqual(result.deleted_track_records, 1)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertTrue(result.cleanup_failures)
        self.assertTrue(self.media.exists())
        self.assertEqual(get_ipod_tracks(read_ipod(self.root)), [])
        self.assertFalse(self.database.with_name('Play Counts').exists())

    def test_late_foreign_leaf_is_restored_without_deleting_it(self):
        plan = self.plan();move = stock_delete._exclusive_move;swapped = []

        def replace_then_claim(parent, name, holding, claim):
            if not swapped:
                swapped.append(True)
                os.unlink(name, dir_fd=parent)
                handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(handle, 'wb') as output:
                    output.write(b'foreign replacement')
            return move(parent, name, holding, claim)

        with mock.patch.object(stock_delete, '_exclusive_move', side_effect=replace_then_claim):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertTrue(result.cleanup_failures)
        self.assertEqual(self.media.read_bytes(), b'foreign replacement')

    def test_failed_foreign_restore_preserves_both_occupants_and_records_claim(self):
        plan = self.plan();move = stock_delete._exclusive_move;swapped = []

        def replace_claim_and_occupy(parent, name, holding, claim):
            if swapped:
                return move(parent, name, holding, claim)
            swapped.append(True)
            os.unlink(name, dir_fd=parent)
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            with os.fdopen(handle, 'wb') as output:
                output.write(b'foreign claimed')
            move(parent, name, holding, claim)
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            with os.fdopen(handle, 'wb') as output:
                output.write(b'newer occupant')

        with mock.patch.object(stock_delete, '_exclusive_move', side_effect=replace_claim_and_occupy):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(self.media.read_bytes(), b'newer occupant')
        self.assertEqual(result.deleted_media_files, 0)
        self.assertIn('claimed file retained', result.cleanup_failures[0])
        claims = list(result.recovery_directory.glob('claimed/*.pending'))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].read_bytes(), b'foreign claimed')

    def test_same_entity_hidden_byte_change_is_not_a_content_certificate(self):
        plan = self.plan();item = plan.media_deletes[0]
        raw = self.media.read_bytes()
        self.media.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        epoch = stock_delete._epoch

        def coarse_epoch(info):
            # FAT can retain all these metadata fields after a same-size edit.
            # The policy deletes this confirmed native entity, without claiming
            # its current payload equals a prior cryptographic digest.
            if (info.st_dev, info.st_ino) == item.epoch[:2]:
                return item.epoch
            return epoch(info)

        with mock.patch.object(stock_delete, '_epoch', side_effect=coarse_epoch):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertFalse(result.cleanup_failures)

    def test_ordinary_sync_delete_records_cannot_borrow_entity_authority(self):
        plan = self.plan();item = plan.media_deletes[0]
        forged = stock_ipod.StockMediaDelete(item.relative_path, item.size, '', item.dbids)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'indexed-file'):
            stock_ipod.commit_stock_ipod_playlist_delete(replace(plan, media_deletes=(forged,)))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertTrue(self.media.exists())

    def test_typed_entity_cannot_be_rebound_to_an_unrelated_or_retained_song(self):
        plan = self.plan();before = self.database.read_bytes()
        foreign = self.media.with_name('foreign.wav');foreign.write_bytes(b'foreign indexed-neighbor')
        entity = stock_delete.capture(DeviceSession(self.root, [], create=False),
                                      foreign.relative_to(self.root), dbids=(101,))
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'exact native song binding'):
            stock_ipod.commit_stock_ipod_playlist_delete(replace(plan, media_deletes=(entity,)))
        with self.assertRaisesRegex(stock_ipod.StockIpodError, 'unreferenced native song'):
            stock_ipod.commit_stock_ipod_playlist_delete(replace(plan, rendered_database=before))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(foreign.read_bytes(), b'foreign indexed-neighbor')
        self.assertTrue(self.media.exists())

    def test_stock_hash_stops_before_reading_a_second_chunk(self):
        payload = self.fixture.base / 'generated-hash-input'
        payload.write_bytes(b'x' * (3 * 1024 * 1024))
        event = threading.Event();opened = Path.open;reads = []

        class Observed:
            def __enter__(self):
                self.handle = opened(payload, 'rb')
                return self

            def __exit__(self, *_args):
                self.handle.close()

            def read(self, size):
                chunk = self.handle.read(size)
                reads.append(len(chunk));event.set()
                return chunk

        def observe(path, *args, **kwargs):
            return Observed() if path == payload else opened(path, *args, **kwargs)

        with operation_scope(event), mock.patch.object(Path, 'open', observe):
            with self.assertRaises(OperationCancelled):
                stock_ipod._sha256(payload)
        self.assertEqual(reads, [1024 * 1024])

    def test_cancelled_stock_failure_cannot_abort_its_artwork_recovery(self):
        fixture = self.fixture
        common = dict(model_number='', filesystem='fat32', usb_family='iPod Video',
                      art_cache=fixture.art_cache, stock_art_cache=fixture.stock_art_cache)
        first = stock_ipod.plan_stock_ipod_sync(fixture.library, [fixture.playlist], self.root, **common)
        stock_ipod.commit_stock_ipod_sync(first)
        art = self.root / 'iPod_Control/Artwork'
        before_database = self.database.read_bytes()
        before_art = (art / 'ArtworkDB').read_bytes()
        before_pixels = {path.name: path.read_bytes() for path in art.glob('*.ithmb')}
        changed = fixture.base / 'new-cover.jpg'
        Image.new('RGB', (240, 240), 'blue').save(changed, 'JPEG')
        fixture.art_cache.paths['old'] = changed
        plan = stock_ipod.plan_stock_ipod_sync(fixture.library, [fixture.playlist], self.root, **common)
        event = threading.Event();write = DeviceSession.atomic_write

        def fail_database(session, destination, data):
            if Path(destination).name == 'iTunesDB':
                event.set()
                raise OSError('generated failure with cancellation pending')
            return write(session, destination, data)

        with operation_scope(event), mock.patch.object(DeviceSession, 'atomic_write', fail_database):
            with self.assertRaisesRegex(OSError, 'generated failure with cancellation'):
                stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(self.database.read_bytes(), before_database)
        self.assertEqual((art / 'ArtworkDB').read_bytes(), before_art)
        for name, pixels in before_pixels.items():
            self.assertEqual((art / name).read_bytes()[:len(pixels)], pixels)

    def test_retained_normalized_native_path_is_not_an_orphan(self):
        # A retained native path spelling resolves to the same FAT name. No
        # media stat/read is needed to preserve that reference.
        native = get_ipod_tracks(read_ipod(self.root))[0]
        track = TrackInfo(title='Retained alias', dbid=102,
            location=':ipod_control:music:f00:aaaa.wav', size=native['size'], length=10,
            filetype='wav', sample_rate=44100)
        original = stock_ipod._existing_track_info(native)
        self.database.with_name('Play Counts').unlink()
        self.assertTrue(write_itunesdb(self.root, [original, track], backup=False,
            force_checksum=ChecksumType.NONE,
            capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen'),
            playlists=[PlaylistInfo('Delete', [101], playlist_id=202),
                       PlaylistInfo('Retain', [102], playlist_id=203)]))
        with self.no_audio_reads():
            plan = self.plan()
            self.assertEqual(plan.media_deletes, ())
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertTrue(self.media.exists())
