"""Real generated FAT native deletion; no physical disks, state or source audio."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from clickwheel.ipod import get_ipod_tracks, read_ipod
from clickwheel.ipod.ipod_models import capabilities_for_family_gen
from clickwheel.ipod.itunesdb_writer import ChecksumType, PlaylistInfo, write_itunesdb

from soundbox import stock_delete, stock_ipod
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.stock_artwork import (
    PROFILE, CLASSIC_PROFILE, CLASSIC_55_PROFILE, render_stock_artwork,
)
from soundbox.stock_ipod_signing import StockSigningContext, sign_stock_database, verified_stock_signing_context
from soundbox.sync import DeviceSession
from tests import test_device_staging_filesystems as image_fixture
from tests import test_stock_ipod as native_fixture


class StockDeleteFilesystems(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the reviewed image lifecycle with a separate plain owner;
        # do not discover/inherit or mutate another TestCase's test state.
        owner = SimpleNamespace(_command=image_fixture.DeviceStagingFilesystems._command,
                                addClassCleanup=cls.addClassCleanup)
        owner._retire_images = lambda: image_fixture.DeviceStagingFilesystems._retire_images.__func__(owner)
        image_fixture.DeviceStagingFilesystems.setUpClass.__func__(owner)
        cls.image_owner = owner
        cls.mount = next(row['mount'] for row in owner.images if row['name'] == 'fat32')

    def fixture(self, profile=PROFILE):
        fixture = native_fixture.StockIpodSync('test_playlist_delete_removes_only_newly_unreferenced_track_and_media')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        target = self.mount / (self._testMethodName + '-' + profile)
        shutil.copytree(fixture.ipod, target);fixture.ipod = target
        track = stock_ipod._existing_track_info(get_ipod_tracks(read_ipod(target))[0])
        cache = fixture.stock_art_cache.for_profile(profile)
        rendered = render_stock_artwork([track], {101: cache.prepare(fixture.art_cache.image_path('old'))},
            cache=cache, existing_artwork_dir=fixture.base / 'no-native-art')
        art = target / 'iPod_Control/Artwork';art.mkdir()
        (art / 'ArtworkDB').write_bytes(rendered.artwork_db)
        for item in rendered.ithmb_files:
            (art / item.relative_path.name).write_bytes(b''.join(part.path.read_bytes() for part in item.segments))
        self.assertTrue(write_itunesdb(target, [track], backup=False,
            force_checksum=ChecksumType.NONE,
            capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen'),
            playlists=[PlaylistInfo('Native only', [101], playlist_id=202)]))
        classic = profile != PROFILE
        database = target / 'iPod_Control/iTunes/iTunesDB'
        if classic:
            context = StockSigningContext(bytes.fromhex('0011223344556677'), bytes(range(16)), bytes(range(12)))
            raw = bytearray(database.read_bytes());struct.pack_into('<I', raw, 0x10, 117)
            database.write_bytes(sign_stock_database(bytes(raw), context))
        fixture.library.tracks_by_id.clear()
        common = dict(model_number='', filesystem='fat32', stock_art_cache=cache,
            usb_family='iPod Classic' if classic else 'iPod Video',
            firewire_guid='0011223344556677' if classic else '')
        return fixture, database, common

    def test_verified_new_copy_failure_cleans_only_its_exact_fat_entity(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                fixture = native_fixture.StockIpodSync('test_database_write_failure_removes_only_new_media')
                fixture.setUp(); self.addCleanup(fixture.doCleanups)
                target = self.mount / (self._testMethodName + str(replacement))
                shutil.copytree(fixture.ipod, target)
                plan = stock_ipod.plan_stock_ipod_sync(
                    fixture.library, [fixture.playlist], target,
                    model_number='MA446', filesystem='fat32', usb_family='iPod Video')
                destination = target / plan.copies[0].relative_path
                database = target / plan.database_relative_path
                before = database.read_bytes()
                write = DeviceSession.atomic_write
                foreign = b'foreign generated FAT copy'

                def replace_after_ack(relative):
                    if replacement:
                        path = target / relative
                        incoming = path.with_suffix('.foreign')
                        incoming.write_bytes(foreign); incoming.replace(path)

                def fail_catalog(session, path, data):
                    if Path(path).name == 'iTunesDB':
                        raise OSError('Generated FAT database failure')
                    return write(session, path, data)

                with mock.patch.object(DeviceSession, 'atomic_write', fail_catalog):
                    with self.assertRaises(stock_ipod.StockIpodError if replacement else OSError) as error:
                        stock_ipod.commit_stock_ipod_sync(plan, on_track_committed=replace_after_ack)
                self.assertEqual(database.read_bytes(), before)
                receipts = list((target / '.soundbox/stock-ipod-recovery').glob('*/plan.json'))
                self.assertEqual(len(receipts), 1)
                import json
                receipt = json.loads(receipts[0].read_bytes())
                if replacement:
                    self.assertEqual(error.exception.recovery_status, 'recovery required')
                    self.assertEqual(destination.read_bytes(), foreign)
                    self.assertEqual(receipt['transaction_state'], 'recovery required')
                    self.assertTrue((receipts[0].parent / 'iTunesDB.before').exists())
                else:
                    self.assertEqual(error.exception.recovery_status, 'rolled back')
                    self.assertFalse(destination.exists())
                    self.assertTrue(receipt['payload_retirement']['complete'])

    @contextmanager
    def no_media_or_pixel_reads(self, target):
        opened = Path.open
        music = target / 'iPod_Control/Music'
        art = target / 'iPod_Control/Artwork'

        def guarded(path, *args, **kwargs):
            if path.is_relative_to(music) or path.parent == art and path.suffix == '.ithmb':
                raise AssertionError(f'Delete read an orphan payload: {path}')
            return opened(path, *args, **kwargs)

        with mock.patch.object(Path, 'open', guarded):
            yield

    def test_video_and_signed_classic_delete_native_entities_without_media_reads(self):
        for profile in (PROFILE, CLASSIC_PROFILE, CLASSIC_55_PROFILE):
            with self.subTest(profile=profile):
                fixture, database, common = self.fixture(profile)
                original_mac = fixture.existing_source.read_bytes()
                with self.no_media_or_pixel_reads(fixture.ipod):
                    plan = stock_ipod.plan_stock_ipod_playlist_delete(fixture.ipod, [202], **common)
                    self.assertEqual(plan.artwork_before, ())
                    self.assertEqual(len(plan.artwork_delete_entities), 3 if profile == PROFILE else 4)
                    result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
                self.assertEqual(result.deleted_media_files, 1)
                self.assertFalse(result.cleanup_failures)
                self.assertFalse((fixture.ipod / 'iPod_Control/Music/F00/AAAA.wav').exists())
                self.assertEqual(get_ipod_tracks(read_ipod(fixture.ipod)), [])
                self.assertFalse((fixture.ipod / 'iPod_Control/Artwork/ArtworkDB').exists())
                self.assertEqual(fixture.existing_source.read_bytes(), original_mac)
                if profile != PROFILE:
                    verified_stock_signing_context(database.read_bytes(), common['firewire_guid'])

    def test_real_fat_cancel_before_publication_and_during_commit_are_truthful(self):
        fixture, database, common = self.fixture()
        plan = stock_ipod.plan_stock_ipod_playlist_delete(fixture.ipod, [202], **common)
        before = database.read_bytes();event = threading.Event()

        def progress(phase, done, _total, _label):
            if phase == 'Saving iPod playlists' and done == 0:
                event.set()

        with operation_scope(event, progress), self.assertRaises(OperationCancelled):
            stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(database.read_bytes(), before)
        event.clear();write = DeviceSession.atomic_write

        def cancel_inside_database(session, destination, data):
            if Path(destination).name == 'iTunesDB':
                event.set()
            return write(session, destination, data)

        with operation_scope(event), mock.patch.object(DeviceSession, 'atomic_write', cancel_inside_database):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertTrue(result.cleanup_cancelled)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertEqual(get_ipod_tracks(read_ipod(fixture.ipod)), [])
        self.assertTrue((fixture.ipod / 'iPod_Control/Music/F00/AAAA.wav').exists())
        self.assertTrue((fixture.ipod / 'iPod_Control/Artwork/ArtworkDB').exists())

    def test_real_fat_late_regular_replacement_survives_exclusive_claim(self):
        fixture, _database, common = self.fixture()
        plan = stock_ipod.plan_stock_ipod_playlist_delete(fixture.ipod, [202], **common)
        move = stock_delete._exclusive_move;swapped = []

        def replace_during_claim(parent, name, holding, claim):
            if not swapped:
                swapped.append(True)
                os.unlink(name, dir_fd=parent)
                handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                with os.fdopen(handle, 'wb') as output:
                    output.write(b'foreign FAT recording')
            return move(parent, name, holding, claim)

        with mock.patch.object(stock_delete, '_exclusive_move', side_effect=replace_during_claim):
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 0)
        self.assertTrue(result.cleanup_failures)
        self.assertEqual((fixture.ipod / 'iPod_Control/Music/F00/AAAA.wav').read_bytes(), b'foreign FAT recording')

    def test_real_fat_empty_indexed_file_survives_rename_identity_transition(self):
        fixture, _database, common = self.fixture()
        media = fixture.ipod / 'iPod_Control/Music/F00/AAAA.wav'
        media.write_bytes(b'')
        with self.no_media_or_pixel_reads(fixture.ipod):
            plan = stock_ipod.plan_stock_ipod_playlist_delete(fixture.ipod, [202], **common)
            result = stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_media_files, 1)
        self.assertEqual(result.deleted_bytes, 0)
        self.assertFalse(result.cleanup_failures)
        self.assertFalse(media.exists())
