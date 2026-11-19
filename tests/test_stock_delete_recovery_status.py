"""Actual Delete rollback faults carry truthful outcomes to the UI."""
from pathlib import Path
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.sync import DeviceSession
from tests import test_stock_ipod as fixture


class StockDeleteRecoveryStatus(unittest.TestCase):
    def setUp(self):
        self.f = fixture.StockIpodSync(methodName='runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.database = self.f.ipod.resolve() / 'iPod_Control/iTunes/iTunesDB'
        self.counts = self.database.with_name('Play Counts')
        self.before = self.database.read_bytes()
        self.before_counts = self.counts.read_bytes()
        self.media = {path: path.read_bytes() for path in self.f.ipod.rglob('*.wav')}
        self.sources = fixture.StockIpodSync._snapshot(self.f.sources)
        self.plan = stock_ipod.plan_stock_ipod_playlist_delete(self.f.ipod, [202],
            model_number='MA446', filesystem='msdos', usb_family='iPod Video')
        self.assertNotEqual(self.plan.rendered_database, self.before)

    def fail_after_publication(self, *, rollback_fails):
        replace = stock_ipod.os.replace
        write = DeviceSession.atomic_write
        events = []

        def failing_replace(source, target, *args, **kwargs):
            if Path(source) == self.counts:
                self.assertEqual(self.database.read_bytes(), self.plan.rendered_database)
                events.append('published_then_failed')
                raise RuntimeError('Generated playback-consumption failure after native publication')
            return replace(source, target, *args, **kwargs)

        def failing_write(session, target, data, *args, **kwargs):
            if Path(target) == self.database and data == self.before:
                events.append('rollback_attempted')
                if rollback_fails:
                    raise OSError('Generated exact database rollback failure')
            return write(session, target, data, *args, **kwargs)

        with mock.patch.object(stock_ipod.os, 'replace', side_effect=failing_replace), \
                mock.patch.object(DeviceSession, 'atomic_write', failing_write):
            with self.assertRaises(stock_ipod.StockIpodError if rollback_fails else RuntimeError) as caught:
                stock_ipod.commit_stock_ipod_playlist_delete(self.plan)
        self.assertEqual(events, ['published_then_failed', 'rollback_attempted'])
        self.assertEqual(self.counts.read_bytes(), self.before_counts)
        self.assertEqual({path: path.read_bytes() for path in self.media}, self.media)
        self.assertEqual(fixture.StockIpodSync._snapshot(self.f.sources), self.sources)
        return caught.exception

    def test_completed_database_rollback_marks_the_original_failure(self):
        error = self.fail_after_publication(rollback_fails=False)
        self.assertEqual(getattr(error, 'recovery_status', None), 'rolled back')
        self.assertEqual(self.database.read_bytes(), self.before)

    def test_failed_database_rollback_requires_recovery_and_keeps_actual_committed_bytes(self):
        error = self.fail_after_publication(rollback_fails=True)
        self.assertEqual(error.recovery_status, 'recovery required')
        self.assertIn('recovery is at', str(error))
        self.assertEqual(self.database.read_bytes(), self.plan.rendered_database)
        self.assertIsInstance(error.__cause__, RuntimeError)
