"""Cached mount hints never probe mounted paths or grant device authority."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest import mock

from desktop import mount_watch


class MountWatch(unittest.TestCase):
    def test_cached_table_filters_paths_and_ignores_free_space(self):
        calls = []

        def getfsstat(rows, size, flags):
            calls.append((size, flags))
            if rows is None:
                return 3
            for row, path, source, fsid in zip(rows, (b'/', b'/Volumes/IPOD', b'/Volumes/X/nested'),
                                               (b'/dev/root', b'/dev/disk9s2', b'/dev/other'),
                                               (1, 9, 10)):
                row.mntonname = path
                row.mntfromname = source
                row.fsid[0] = fsid
                row.bavail = len(calls)  # mutable counters are not identity
            return 3

        with mock.patch.object(mount_watch, '_getfsstat', getfsstat), \
                mock.patch('os.stat', side_effect=AssertionError('must not open mounts')):
            self.assertEqual(mount_watch.mounted_volume_snapshot(),
                             (('/Volumes/IPOD', 9, 0, '/dev/disk9s2'),))
            self.assertEqual(mount_watch.mounted_volume_snapshot(),
                             (('/Volumes/IPOD', 9, 0, '/dev/disk9s2'),))
        self.assertTrue(all(flags == 2 for _, flags in calls))
        self.assertEqual(ctypes.sizeof(mount_watch._StatFS64), 2168)

    def test_failure_and_truncation_are_unknown_not_empty_mounts(self):
        for results in ((-1,), (4097,), (1, -1), (0, 16)):
            with self.subTest(results=results), \
                    mock.patch.object(mount_watch, '_getfsstat', side_effect=results):
                self.assertIsNone(mount_watch.mounted_volume_snapshot())
        with mock.patch.object(mount_watch, '_getfsstat', return_value=0):
            self.assertEqual(mount_watch.mounted_volume_snapshot(), ())

    def test_mount_transition_bypasses_directory_timestamp_and_probe_backoff(self):
        from desktop.app import MainWindow

        calls = []
        host = SimpleNamespace(_mount_snapshot=(),
                               _on_volume_mount_changed=lambda: calls.append('discover'))
        mounted = (('/Volumes/IPOD', 9, 0, '/dev/disk9s2'),)
        remounted = (('/Volumes/IPOD', 11, 0, '/dev/disk10s2'),)
        with mock.patch('desktop.app.mounted_volume_snapshot',
                        side_effect=[(), mounted, mounted, None, remounted, ()]):
            for _ in range(6):
                MainWindow._poll_mount_table(host)
        self.assertEqual(calls, ['discover', 'discover', 'discover'])
        self.assertEqual(host._mount_snapshot, ())
