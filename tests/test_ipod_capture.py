"""Required offline database regression; never writes the original capture or sources.

SOUNDBOX_IPOD_CAPTURE can override the preserved private capture containing device/ and
inventory.json. Omitted historical audio is represented by sparse size fixtures;
opening those fixtures as audio is an error. Only generated new audio is read.
These tests do not establish hardware or historical-audio integrity.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import ast
import tempfile
import unittest
from unittest import mock
import wave
from types import SimpleNamespace
from PIL import Image

from clickwheel.ipod import get_ipod_tracks, parse_itunesdb
from soundbox import dual_ipod, stock_ipod
from soundbox.library import Library, Playlist, Track
from soundbox.operation_control import OperationCancelled, operation_scope
from threading import Event
from soundbox.rockbox_media import RockboxArtworkCache, RockboxMediaCache
from soundbox.sync import DeviceSession


class CapturedIpodLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.check_fixtures import ipod_capture
        cls.capture = ipod_capture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='soundbox-captured-ipod-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.device = self.base / 'device'
        for directory in ('iPod_Control/iTunes', 'iPod_Control/Artwork', 'iPod_Control/Device'):
            source = self.capture / 'device' / directory
            if source.exists():
                shutil.copytree(source, self.device / directory)
        for path in self.device.rglob('*'):
            if path.is_file():
                path.chmod(0o600)
        (self.device / '.rockbox').mkdir()
        (self.device / '.rockbox/config.cfg').write_bytes(
            (self.capture / 'config.after.cfg').read_bytes())
        self.omitted = set()
        for row in json.loads((self.capture / 'inventory.json').read_text()):
            relative = Path(row['path'])
            if (relative.parts[:2] != ('iPod_Control', 'Music')
                    or row.get('omitted') != 'audio payload'):
                continue
            target = self.device / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as stream:
                stream.truncate(row['size'])
            self.omitted.add(target)
        real_open = Path.open

        def guarded(path, mode='r', *args, **kwargs):
            if path in self.omitted and ('r' in mode or '+' in mode):
                raise AssertionError('Attempted to read omitted historical audio')
            return real_open(path, mode, *args, **kwargs)

        self.enterContext(mock.patch.object(Path, 'open', guarded))
        for module in (dual_ipod, stock_ipod):
            self.enterContext(mock.patch.object(module, '_pace_classic_usb'))
        self.source = self.base / 'sources' / 'fixture.wav'
        self.source.parent.mkdir()
        with wave.open(str(self.source), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b'\x01\x00' * 2 * 882)
        self.playlist = Playlist('Offline lifecycle fixture', [], ['fixture'], pid='offline-fixture')
        self.library = Library({'fixture': Track(
            'fixture', 'Offline fixture', 'SoundBox', self.source.as_uri())},
            [self.playlist], self.source.parent)
        self.media = RockboxMediaCache(self.base / 'media-cache')
        self.art = RockboxArtworkCache(self.base / 'art-cache')
        self.before_db = (self.device / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        self.initial_count = len(get_ipod_tracks(parse_itunesdb(str(
            self.device / 'iPod_Control/iTunes/iTunesDB'))))
        self.assertGreater(self.initial_count, 1000)

    def plan(self):
        return dual_ipod.plan_dual_ipod_sync(
            self.library, [self.playlist], self.device,
            model_number='MA446', filesystem='msdos',
            rockbox_media_cache=self.media, rockbox_art_cache=self.art,
            art_cache=getattr(self, "cover_cache", None),
            derive_artists=False, reconcile=(self.device / ".soundbox/manifest.json").exists())

    def test_commit_eviction_and_unchanged_followup(self):
        plan = self.plan()
        st = self.source.stat()
        os.utime(self.source, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
        with operation_scope(None):
            result = dual_ipod.commit_dual_ipod_sync(plan)
        self.assertTrue(result.manifest_sha256)
        self.assertEqual(len(get_ipod_tracks(parse_itunesdb(str(
            self.device / 'iPod_Control/iTunes/iTunesDB')))), self.initial_count + 1)
        previous_open = Path.open

        def no_source_read(path, mode='r', *args, **kwargs):
            if path == self.source and ('r' in mode or '+' in mode):
                raise AssertionError('unchanged sync reread canonical audio')
            return previous_open(path, mode, *args, **kwargs)

        with mock.patch.object(Path, 'open', no_source_read), mock.patch.object(
                self.media, 'prepare', side_effect=AssertionError('unchanged payload rebuilt')):
            following = self.plan()
        self.assertFalse(following.requires_commit)
        self.assertEqual(following.stock.database_sha256_before,
                         hashlib.sha256(following.stock.rendered_database).hexdigest())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'AAC fixture encoder unavailable')
    def test_aac_eviction_at_materialization_uses_one_capture_per_track(self):
        aac = self.source.with_name('cloud-live-set.m4a')
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=0.25', '-c:a', 'aac', str(aac)],
                       check=True, capture_output=True)
        self.library.tracks_by_id['aac'] = Track('aac', 'Live set fixture', 'SoundBox', aac.as_uri())
        self.playlist.keys.append('aac')
        plan = self.plan()
        self.assertEqual(plan.rockbox_only_sources, 1)
        original = self.media.materialize

        def evict(media):
            st = media.source.stat()
            os.utime(media.source, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
            return original(media)

        with self.assertLogs('soundbox', level='INFO') as logs:
            with mock.patch.object(self.media, 'materialize', evict), operation_scope(None):
                dual_ipod.commit_dual_ipod_sync(plan)
        metrics = ast.literal_eval(next(record.getMessage().split(': ', 1)[1]
            for record in logs.records if record.getMessage().startswith('Device operation metrics: ')))
        self.assertEqual(metrics['acquisitions'], 2)
        self.assertEqual(metrics['canonical_bytes_read'], self.source.stat().st_size + aac.stat().st_size)
        self.assertLessEqual(metrics['peak_audio_staging_bytes'],
                            2 * max(self.source.stat().st_size, aac.stat().st_size) + 4096)
        self.assertTrue((self.device / '.soundbox/manifest.json').exists())

    def test_late_publication_failure_restores_real_database(self):
        plan = self.plan()
        original = DeviceSession.atomic_write

        def fail_manifest(session, path, data):
            if path.name == 'manifest.json':
                raise OSError('injected publication failure')
            return original(session, path, data)

        with mock.patch.object(DeviceSession, 'atomic_write', fail_manifest):
            with self.assertRaisesRegex(Exception, 'injected publication failure'):
                dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual((self.device / 'iPod_Control/iTunes/iTunesDB').read_bytes(), self.before_db)
        self.assertFalse((self.device / '.soundbox/manifest.json').exists())

    def test_eviction_between_planning_stages(self):
        dual_ipod.commit_dual_ipod_sync(self.plan())
        original = dual_ipod.plan_stock_ipod_sync

        def evict(*args, **kwargs):
            st = self.source.stat()
            os.utime(self.source, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
            return original(*args, **kwargs)

        with mock.patch.object(dual_ipod, 'plan_stock_ipod_sync', evict):
            plan = self.plan()
        dual_ipod.commit_dual_ipod_sync(plan)
        self.assertFalse(self.plan().requires_commit)

    def test_copy_cannot_reopen_canonical_source_after_materialization(self):
        plan = self.plan()
        materialize = self.media.materialize
        original_open = Path.open
        captured = []

        def acquire(*args, **kwargs):
            result = materialize(*args, **kwargs)
            captured.append(result)
            return result

        def guard(path, mode='r', *args, **kwargs):
            if captured and path == self.source and ('r' in mode or '+' in mode):
                raise AssertionError('canonical source reopened after capture')
            return original_open(path, mode, *args, **kwargs)

        with mock.patch.object(self.media, 'materialize', acquire), mock.patch.object(Path, 'open', guard):
            dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].payload.exists())

    def test_cancel_after_stock_publication_rolls_back_and_retry_succeeds(self):
        plan = self.plan()
        stop = Event()
        original = DeviceSession.atomic_write

        def cancel(session, path, data):
            original(session, path, data)
            if path.name == 'iTunesDB':
                stop.set()

        with mock.patch.object(DeviceSession, 'atomic_write', cancel), operation_scope(stop):
            with self.assertRaises(OperationCancelled):
                dual_ipod.commit_dual_ipod_sync(plan)
        self.assertEqual((self.device / 'iPod_Control/iTunes/iTunesDB').read_bytes(), self.before_db)
        self.assertFalse((self.device / '.soundbox/manifest.json').exists())
        dual_ipod.commit_dual_ipod_sync(self.plan())
        self.assertFalse(self.plan().requires_commit)

    def test_cancel_after_manifest_is_committed(self):
        plan = self.plan()
        stop = Event()
        original = DeviceSession.atomic_write

        def cancel(session, path, data):
            original(session, path, data)
            if path.name == 'manifest.json':
                stop.set()

        with mock.patch.object(DeviceSession, 'atomic_write', cancel), operation_scope(stop):
            result = dual_ipod.commit_dual_ipod_sync(plan)
        self.assertTrue(result.manifest_sha256)
        journal = next((self.device / '.soundbox/dual-recovery').glob('*/plan.json'))
        self.assertEqual(json.loads(journal.read_text())['transaction_state'], 'committed')

    def test_missing_rockbox_tracks_and_covers_repair_against_real_database(self):
        cover = self.base / 'cover.jpg'
        Image.new('RGB', (160, 160), (20, 50, 90)).save(cover)
        self.cover_cache = SimpleNamespace(image_path=lambda tid: cover if tid != 'fixture' else None)
        wps = self.device / '.rockbox/wps/fixture.wps'
        wps.parent.mkdir()
        wps.write_text('%Cl(0,0,130,130)\n%Cl(0,0,30,30)\n')
        with (self.device / '.rockbox/config.cfg').open('a') as handle:
            handle.write('wps: /.rockbox/wps/fixture.wps\n')
        for tid in ('unsafe-one', 'unsafe-two'):
            path = self.source.with_name(tid + '.wav')
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(2)
                output.setsampwidth(3)
                output.setframerate(44100)
                output.writeframes(b'\x01\x00\x00' * 2 * 882)
            self.library.tracks_by_id[tid] = Track(tid, tid, 'Fixture', path.as_uri())
            self.playlist.keys.append(tid)
        dual_ipod.commit_dual_ipod_sync(self.plan())
        manifest_path = self.device / '.soundbox/manifest.json'
        baseline = manifest_path.read_bytes()
        missing = []
        for name, row in json.loads(baseline)['files'].items():
            if row['kind'] in {'rockbox-media', 'rockbox-artwork'}:
                path = self.device / name.lstrip('/')
                missing.append((path, path.read_bytes()))
                path.unlink()
        self.assertEqual(len(missing), 12)
        plan = self.plan()
        self.assertEqual(len(plan.media_copies), 2)
        self.assertEqual(len(plan.artwork_copies), 10)
        dual_ipod.commit_dual_ipod_sync(plan)
        for path, content in missing:
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse(self.plan().requires_commit)

    def test_capture_database_is_unchanged(self):
        path = self.capture / 'device/iPod_Control/iTunes/iTunesDB'
        inventory = json.loads((self.capture / 'inventory.json').read_text())
        row = next(r for r in inventory if r['path'] == 'iPod_Control/iTunes/iTunesDB')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])
