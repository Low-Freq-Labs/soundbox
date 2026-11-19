"""Device cancellation cannot interrupt a composite generation's recovery."""
from pathlib import Path
import struct
import threading
import unittest
from unittest import mock

from soundbox import dual_ipod, manifest
from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.sync import DeviceSession
from tests import test_dual_ipod as fixtures


class DualCancellationRecovery(unittest.TestCase):
    def test_pending_cancel_restores_prior_catalog_counts_manifest_and_managed_files(self):
        fixture = fixtures.DualIpodSyncTests('test_incremental_playlist_rewrite_rolls_back_on_manifest_failure')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        dual_ipod.commit_dual_ipod_sync(fixture._plan())
        root = fixture.ipod.resolve()
        database = root / 'iPod_Control/iTunes/iTunesDB'
        counts = database.with_name('Play Counts')
        counts.write_bytes(b'mhdp' + struct.pack('<III', 96, 28, 1) + bytes(80)
                           + struct.pack('<IIIIIII', 2, 0, 0, 0, 0, 0, 0))
        native_before = database.read_bytes();counts_before = counts.read_bytes()
        manifest_path = root / '.soundbox/manifest.json'
        manifest_before = manifest_path.read_bytes()
        owned = manifest.load_composite(root)
        managed_before = {name: (root / name.lstrip('/')).read_bytes()
                          for name in owned['files']}
        artwork = root / 'iPod_Control/Artwork'
        art_before = {path.name: path.read_bytes() for path in artwork.glob('*') if path.is_file()}
        source_before = {path: path.read_bytes() for path in (fixture.safe, fixture.unsafe)}
        fixture.playlist.keys = ['unsafe', 'safe']
        changed = fixture._plan(title_overrides={'safe': 'Changed for cancellation proof'})
        self.assertTrue(changed.stock.requires_commit)
        self.assertTrue(any(item.changed for item in changed.playlists))
        event = threading.Event();write = DeviceSession.atomic_write

        def cancel_at_manifest(session, destination, data):
            if Path(destination) == manifest_path:
                event.set()
                raise OperationCancelled('Generated cancellation before manifest publication')
            return write(session, destination, data)

        with operation_scope(event), mock.patch.object(DeviceSession, 'atomic_write', cancel_at_manifest):
            with self.assertRaisesRegex(OperationCancelled, 'Generated cancellation') as failure:
                dual_ipod.commit_dual_ipod_sync(changed)
        self.assertEqual(failure.exception.recovery_status, 'rolled back')
        self.assertEqual(database.read_bytes(), native_before)
        self.assertEqual(counts.read_bytes(), counts_before)
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        for name, payload in managed_before.items():
            self.assertEqual((root / name.lstrip('/')).read_bytes(), payload)
        for name, payload in art_before.items():
            actual = (artwork / name).read_bytes()
            self.assertEqual(actual[:len(payload)] if name.endswith('.ithmb') else actual, payload)
        for path, payload in source_before.items():
            self.assertEqual(path.read_bytes(), payload)
