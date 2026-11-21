"""Leaf-name migration preserves foreign, changed and cancelled playlist files."""
import hashlib
from threading import Event
import unittest
from unittest import mock

from soundbox import engine, manifest, sync
from soundbox.library import Playlist
from soundbox.playlist_retirement import PENDING_RETIREMENT, legacy_flat_playlists
from tests import test_sync as fixture


class RockboxPlaylistNameMigration(unittest.TestCase):
    def setUp(self):
        self.case = fixture.TestSyncEndToEnd(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        # This older fixture owns its TemporaryDirectory through tearDown.
        self.addCleanup(self.case.tearDown)
        self.target = self.case.target
        self.playlist = Playlist('DUBSTEP', ['GENRES'], ['1'], 'leaf-test')
        self.lib = self.case.lib; self.lib.playlists = [self.playlist]
        report = sync.run(self.lib, [self.playlist], self.target, layout='flat', dry_run=False,
                          concise_playlists=False)
        self.assertFalse(report.cancelled); self.assertFalse(report.playlists[0].errors)
        self.old = self.target / 'Playlists/GENRES - DUBSTEP.m3u8'
        self.new = self.target / 'Playlists/DUBSTEP.m3u8'
        self.old_bytes = self.old.read_bytes()
        (self.target / '.rockbox').mkdir()
        self.foreign = self.target / 'Playlists/External.m3u8'
        self.foreign.write_bytes(b'User-owned playlist\n')

    def test_explicit_resync_publishes_leaf_before_retiring_exact_old_owned_file_and_then_converges(self):
        drift = engine.device_drift(self.lib, self.target)
        self.assertEqual(drift.count(), 1)
        real = manifest.record_export; observations = []
        def publish(*args, **kwargs):
            observations.append((self.old.exists(), self.new.exists()))
            return real(*args, **kwargs)
        with mock.patch.object(manifest, 'record_export', side_effect=publish):
            report = sync.resync(self.lib, self.target)
        self.assertFalse(report.cancelled); self.assertFalse(report.playlists[0].errors)
        self.assertEqual(observations, [(True, True), (False, True)])
        self.assertFalse(self.old.exists()); self.assertEqual(self.new.read_bytes(), self.old_bytes)
        self.assertEqual(self.foreign.read_bytes(), b'User-owned playlist\n')
        files = manifest.load(self.target)['files']
        self.assertNotIn('/Playlists/GENRES - DUBSTEP.m3u8', files)
        self.assertIn('/Playlists/DUBSTEP.m3u8', files)
        self.assertFalse(engine.device_drift(self.lib, self.target).any())
        with mock.patch.object(sync, 'run', side_effect=AssertionError('Clean migration repeated')):
            self.assertEqual(sync.resync(self.lib, self.target).playlists, [])

    def test_same_size_user_edit_to_owned_old_file_is_kept_with_attention(self):
        changed = bytearray(self.old_bytes); changed[-1] ^= 1; self.old.write_bytes(changed)
        with mock.patch('soundbox.playlist_retirement.stock_delete.retire',
                        side_effect=AssertionError('Changed bytes authorized retirement')):
            report = sync.resync(self.lib, self.target)
        self.assertTrue(report.playlists[0].errors)
        self.assertIn('preserved', ' '.join(report.playlists[0].errors))
        self.assertEqual(self.old.read_bytes(), bytes(changed)); self.assertTrue(self.new.exists())
        self.assertIn('/Playlists/GENRES - DUBSTEP.m3u8', manifest.load(self.target)['files'])

    def test_unowned_legacy_flat_file_is_not_migration_authority(self):
        current = manifest.load(self.target)
        current['files']['/Playlists/GENRES - DUBSTEP.m3u8']['owned'] = False
        manifest.save(self.target, current)
        self.assertEqual(legacy_flat_playlists(current, [self.playlist]), {})
        self.assertFalse(engine.device_drift(self.lib, self.target).any())
        report = sync.run(self.lib, [self.playlist], self.target, layout='flat', dry_run=False)
        self.assertFalse(report.playlists[0].errors); self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_cancel_after_new_publication_keeps_old_artifact(self):
        cancelled = Event(); real = manifest.record_export
        def publish(*args, **kwargs):
            result = real(*args, **kwargs); cancelled.set(); return result
        with mock.patch.object(manifest, 'record_export', side_effect=publish):
            report = sync.resync(self.lib, self.target, should_cancel=cancelled.is_set)
        self.assertTrue(report.cancelled); self.assertTrue(self.new.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)
        self.assertEqual(self.foreign.read_bytes(), b'User-owned playlist\n')

    def test_foreign_entry_replacement_after_hash_is_never_retired(self):
        from soundbox import stock_delete
        real = stock_delete.retire
        def replace_before_claim(session, item, claim):
            replacement = self.target / 'replacement.m3u8'
            replacement.write_bytes(b'Foreign replacement')
            replacement.replace(self.old)
            return real(session, item, claim)
        with mock.patch.object(stock_delete, 'retire', side_effect=replace_before_claim):
            report = sync.resync(self.lib, self.target)
        self.assertTrue(report.playlists[0].errors)
        self.assertEqual(self.old.read_bytes(), b'Foreign replacement')
        self.assertTrue(self.new.exists())

    def test_modified_or_replaced_old_file_survives_repeated_resync_after_source_rename_and_move(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.playlist.name = 'BASS'; self.playlist.folder_path = ['NEW GENRES']
                changed = bytes(byte ^ 1 for byte in self.old_bytes)
                if replacement:
                    foreign = self.target / 'replacement.m3u8'
                    foreign.write_bytes(changed); foreign.replace(self.old)
                else:
                    self.old.write_bytes(changed)
                for _ in range(2):
                    report = sync.resync(self.lib, self.target)
                    self.assertTrue(report.playlists[0].errors)
                    self.assertEqual(self.old.read_bytes(), changed)
                    current = manifest.load(self.target)
                    row = current['files']['/Playlists/GENRES - DUBSTEP.m3u8']
                    self.assertEqual(row[PENDING_RETIREMENT], dict(
                        source=self.playlist.pid, path='Playlists/GENRES - DUBSTEP.m3u8',
                        size=len(self.old_bytes), sha256=hashlib.sha256(self.old_bytes).hexdigest()))
                    self.assertEqual(current['playlists'][self.playlist.pid]['name'], 'BASS')
                self.assertEqual(self.foreign.read_bytes(), b'User-owned playlist\n')

    def test_transient_retirement_failure_retries_original_proof_after_further_source_changes(self):
        self.playlist.name = 'BASS'; self.playlist.folder_path = ['NEW GENRES']
        with mock.patch('soundbox.playlist_retirement.stock_delete.retire', side_effect=OSError('Try again')):
            report = sync.resync(self.lib, self.target)
        self.assertTrue(report.playlists[0].errors); self.assertTrue(self.old.exists())
        self.playlist.name = 'DEEP'; self.playlist.folder_path = ['ANOTHER FOLDER']
        report = sync.resync(self.lib, self.target)
        self.assertFalse(report.cancelled); self.assertFalse(report.playlists[0].errors)
        self.assertFalse(self.old.exists())
        self.assertEqual((self.target / 'Playlists/DEEP.m3u8').read_bytes(), self.old_bytes)
        self.assertNotIn('/Playlists/GENRES - DUBSTEP.m3u8', manifest.load(self.target)['files'])
        self.assertEqual(self.foreign.read_bytes(), b'User-owned playlist\n')

    def test_cancelled_publication_persists_original_retirement_proof_and_retry_finishes(self):
        self.playlist.name = 'BASS'; self.playlist.folder_path = ['NEW GENRES']
        cancelled = Event(); real = manifest.record_export
        def publish(*args, **kwargs):
            row = kwargs['managed_files']['/Playlists/GENRES - DUBSTEP.m3u8']
            self.assertIn(PENDING_RETIREMENT, row)
            result = real(*args, **kwargs); cancelled.set(); return result
        with mock.patch.object(manifest, 'record_export', side_effect=publish):
            report = sync.resync(self.lib, self.target, should_cancel=cancelled.is_set)
        self.assertTrue(report.cancelled); self.assertTrue(self.old.exists())
        self.assertIn(PENDING_RETIREMENT,
                      manifest.load(self.target)['files']['/Playlists/GENRES - DUBSTEP.m3u8'])
        report = sync.resync(self.lib, self.target)
        self.assertFalse(report.cancelled); self.assertFalse(report.playlists[0].errors)
        self.assertFalse(self.old.exists())
        self.assertEqual((self.target / 'Playlists/BASS.m3u8').read_bytes(), self.old_bytes)

    def test_generic_prune_preserves_pending_rows_even_with_invalid_proof_and_no_source(self):
        current = manifest.load(self.target)
        path = '/Playlists/GENRES - DUBSTEP.m3u8'
        current['files'][path].update(playlists=[], **{PENDING_RETIREMENT: {'unknown': 'proof'}})
        session = sync.DeviceSession(self.target, [], create=False)
        for media in (False, True):
            self.assertEqual(sync._prune_manifest_owned(session, current['files'], media=media), 0)
            self.assertIn(path, current['files'])
            self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_failed_new_publication_retains_old_proof_before_source_metadata_is_updated(self):
        self.playlist.name = 'BASS'; self.playlist.folder_path = ['NEW GENRES']
        real = sync.DeviceSession.atomic_write
        def refuse_playlist(session, destination, payload):
            if destination.suffix == '.m3u8':
                raise OSError('Playlist publication unavailable')
            return real(session, destination, payload)
        with mock.patch.object(sync.DeviceSession, 'atomic_write', new=refuse_playlist):
            report = sync.resync(self.lib, self.target)
        self.assertTrue(report.playlists[0].errors)
        self.assertFalse((self.target / 'Playlists/BASS.m3u8').exists())
        current = manifest.load(self.target)
        self.assertEqual(current['playlists'][self.playlist.pid]['name'], 'BASS')
        self.assertIn(PENDING_RETIREMENT, current['files']['/Playlists/GENRES - DUBSTEP.m3u8'])
        changed = bytes(byte ^ 1 for byte in self.old_bytes); self.old.write_bytes(changed)
        for _ in range(2):
            report = sync.resync(self.lib, self.target)
            self.assertTrue(report.playlists[0].errors)
            self.assertEqual(self.old.read_bytes(), changed)
        self.assertEqual(self.foreign.read_bytes(), b'User-owned playlist\n')

    def test_source_rename_cannot_overwrite_a_pending_foreign_old_path(self):
        self.playlist.name = 'BASS'; self.playlist.folder_path = ['NEW GENRES']
        changed = bytes(byte ^ 1 for byte in self.old_bytes); self.old.write_bytes(changed)
        report = sync.resync(self.lib, self.target)
        self.assertTrue(report.playlists[0].errors)
        self.playlist.name = 'GENRES - DUBSTEP'
        sync.resync(self.lib, self.target)
        self.assertEqual(self.old.read_bytes(), changed)
        report = sync.run(self.lib, [self.playlist], self.target, layout='flat', dry_run=False)
        self.assertTrue(report.playlists[0].errors)
        self.assertEqual(self.old.read_bytes(), changed)
        self.assertIn(PENDING_RETIREMENT,
                      manifest.load(self.target)['files']['/Playlists/GENRES - DUBSTEP.m3u8'])
