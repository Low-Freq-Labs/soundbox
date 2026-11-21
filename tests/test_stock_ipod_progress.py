"""Measured stock export stages use generated media and disposable catalogs."""
import hashlib
import os
from pathlib import Path
from threading import Event
import unittest
from unittest import mock

from soundbox import device_staging, stock_ipod
from soundbox.library import Track
from soundbox.operation_control import operation_scope
from soundbox.sync import DeviceSession
from tests import test_stock_ipod as stock_fixture
from tests import test_stock_ipod_catalog as catalog_fixture


class StockExportProgress(unittest.TestCase):
    def setUp(self):
        self.fixture = stock_fixture.StockIpodSync()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def plan(self):
        f = self.fixture
        return stock_ipod.plan_stock_ipod_sync(f.library, [f.playlist], f.ipod,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            art_cache=f.art_cache, stock_art_cache=f.stock_art_cache)

    @staticmethod
    def stage(events, name):
        return [(done, total, label) for phase, done, total, label in events if phase == name]

    def test_read_only_plan_reports_selected_work_including_omitted_and_repeated_members(self):
        f = self.fixture
        unsupported = f.sources / 'not-opened.m4a'
        unsupported.write_bytes(b'Unsupported stock container; do not acquire')
        f.library.tracks_by_id['unsupported'] = Track('unsupported', 'Unsupported', '', unsupported.as_uri())
        f.playlist.keys += ['new', 'unsupported']
        before = f._snapshot(f.ipod)
        events = []
        with operation_scope(Event(), lambda *event: events.append(event)):
            plan = self.plan()
        stages = self.stage(events, 'iTunes planning')
        self.assertTrue(stages, events)
        self.assertEqual(stages[0][0], 0)
        self.assertGreater(stages[-1][1], len(f.library.resolve(f.playlist)))
        self.assertEqual(stages[-1][0], stages[-1][1])
        self.assertEqual([row[0] for row in stages], sorted(row[0] for row in stages))
        self.assertEqual(len(plan.copies), 1)
        self.assertEqual([row.track_id for row in plan.omissions], ['unsupported'])
        self.assertFalse(self.stage(events, 'iTunes media'))
        self.assertEqual(f._snapshot(f.ipod), before)

    def test_complete_plan_reports_fixed_compact_phase_timings_without_target_changes(self):
        f=self.fixture
        before=f._snapshot(f.ipod)
        with mock.patch('soundbox.operation_control.logging.getLogger') as logger:
            with operation_scope(Event()):self.plan()
        metrics=next(call.args[1] for call in logger.return_value.info.call_args_list
                     if call.args[0]=='Device operation metrics: %s')
        stages=('capture','sources','inventory','playlists','prune','metadata','artwork','render','finalize')
        values=[metrics['stock_plan_'+name+'_us'] for name in stages]
        self.assertTrue(all(type(value) is int and value>=0 for value in values))
        self.assertAlmostEqual(sum(values),metrics['stock_plan_total_us'],delta=10)
        self.assertEqual(f._snapshot(f.ipod),before)

    def test_verified_physical_media_and_art_bytes_precede_database_and_cleanup(self):
        f = self.fixture
        f.playlist.keys += ['new']  # A repeated membership must not count another physical copy.
        plan = self.plan()
        database = plan.target / plan.database_relative_path
        before = database.read_bytes()
        events = []

        def progress(phase, done, total, label):
            events.append((phase, done, total, label))
            if phase == 'iTunes media' and done:
                verified = 0
                for item in plan.copies:
                    destination = f.ipod / item.relative_path
                    if destination.is_file():
                        self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), item.sha256)
                        verified += item.size
                self.assertLessEqual(done, verified)
                self.assertEqual(database.read_bytes(), before)
            if phase == 'iTunes database':
                self.assertEqual(database.read_bytes(), plan.rendered_database if done else before)

        committed = []
        with operation_scope(Event(), progress):
            result = stock_ipod.commit_stock_ipod_sync(plan, committed.append)
        media = self.stage(events, 'iTunes media')
        art = self.stage(events, 'iTunes artwork')
        catalog = self.stage(events, 'iTunes database')
        cleanup = self.stage(events, 'iTunes cleanup')
        preflight = self.stage(events, 'Checking iPod files')
        self.assertTrue(preflight, events)
        self.assertTrue(all(type(value) is int for row in preflight for value in row[:2]))
        self.assertEqual(preflight[-1][:2], (len(plan.copies), len(plan.copies)))
        self.assertLess(next(i for i, row in enumerate(events) if row[0] == 'Checking iPod files'),
                        next(i for i, row in enumerate(events) if row[0] == 'iTunes media'))
        self.assertTrue(media, events)
        self.assertEqual(media[0][:2], (0, sum(item.size for item in plan.copies)))
        self.assertEqual(media[-1][:2], (plan.bytes_to_copy, plan.bytes_to_copy))
        copying = self.stage(events, 'iTunes copying')
        verifying = self.stage(events, 'iTunes verifying')
        self.assertEqual(copying[0][:2], (0, 2 * plan.bytes_to_copy))
        self.assertEqual(copying[-1][:2], (plan.bytes_to_copy, 2 * plan.bytes_to_copy))
        self.assertEqual(verifying[-1][:2], (2 * plan.bytes_to_copy, 2 * plan.bytes_to_copy))
        self.assertLess(events.index(next(row for row in events if row[0] == 'iTunes verifying')),
                        events.index(next(row for row in events if row[0] == 'iTunes media' and row[1])))
        self.assertEqual(art[-1][:2], (plan.artwork_bytes_to_copy, plan.artwork_bytes_to_copy))
        self.assertEqual([row[:2] for row in catalog], [(0, 1), (1, 1)])
        self.assertEqual(cleanup[-1][0], cleanup[-1][1])
        self.assertEqual(committed, [item.relative_path for item in plan.copies])
        self.assertEqual(result.copied_tracks, 1)
        primary = [phase for phase, *_ in events if phase.startswith('iTunes ')]
        self.assertLess(primary.index('iTunes media'), primary.index('iTunes artwork'))
        self.assertLess(primary.index('iTunes artwork'), primary.index('iTunes database'))
        self.assertLess(primary.index('iTunes database'), primary.index('iTunes cleanup'))

    def test_corrupt_copied_payload_never_advances_verified_media_bytes(self):
        f = self.fixture
        plan = self.plan()
        database = plan.target / plan.database_relative_path
        before = database.read_bytes()
        original = device_staging._rename_new
        events = []

        def corrupt(parent, temporary, name):
            original(parent, temporary, name)
            if Path(name).suffix == '.wav':
                handle = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.write(handle, b'corrupt')
                    os.fsync(handle)
                finally:
                    os.close(handle)

        with operation_scope(Event(), lambda *event: events.append(event)), \
                mock.patch.object(device_staging, '_rename_new', corrupt), \
                self.assertRaisesRegex(OSError, 'Published new media failed verification'):
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual([done for done, _, _ in self.stage(events, 'iTunes media')], [0])
        self.assertTrue(self.stage(events, 'iTunes verifying'))
        self.assertTrue(all(done < total for done, total, _ in self.stage(events, 'iTunes verifying')))
        self.assertFalse(self.stage(events, 'iTunes database'))
        self.assertFalse(self.stage(events, 'iTunes cleanup'))
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse((f.ipod / plan.copies[0].relative_path).exists())

    def test_database_publication_failure_never_reports_database_completion(self):
        plan = self.plan()
        database = plan.target / plan.database_relative_path
        before = database.read_bytes()
        original = DeviceSession.atomic_write
        events = []

        def fail(session, destination, data):
            if Path(destination) == database:
                raise OSError('Generated database publication failure')
            return original(session, destination, data)

        with operation_scope(Event(), lambda *event: events.append(event)), \
                mock.patch.object(DeviceSession, 'atomic_write', fail), \
                self.assertRaisesRegex(OSError, 'Generated database publication failure'):
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual([done for done, _, _ in self.stage(events, 'iTunes database')], [0])
        self.assertFalse(self.stage(events, 'iTunes cleanup'))
        self.assertEqual(database.read_bytes(), before)

    def test_large_new_file_reports_copy_and_readback_chunks_before_verified_ack(self):
        f = self.fixture
        stock_fixture._wave(f.new_source, 1_048_576, 200)
        plan = self.plan()
        events = []
        # Make throttled delivery deterministic while using real multi-chunk IO.
        clock = iter(index * 0.25 for index in range(20000))
        with operation_scope(Event(), lambda *event: events.append(event)), \
                mock.patch.object(stock_ipod.time, 'monotonic', side_effect=lambda: next(clock)):
            result = stock_ipod.commit_stock_ipod_sync(plan)
        total = plan.bytes_to_copy
        copying = self.stage(events, 'iTunes copying')
        verifying = self.stage(events, 'iTunes verifying')
        self.assertTrue(any(0 < done < total for done, _, _ in copying))
        self.assertTrue(any(total < done < 2 * total for done, _, _ in verifying))
        self.assertTrue(all(type(value) is int for row in copying + verifying for value in row[:2]))
        self.assertTrue(all(count == 2 * total for _, count, _ in copying + verifying))
        verified_ack = next(index for index, row in enumerate(events) if row[0] == 'iTunes media' and row[1])
        readback_end = next(index for index, row in enumerate(events)
                           if row[0] == 'iTunes verifying' and row[1] == 2 * total)
        self.assertLess(readback_end, verified_ack)
        self.assertEqual(result.copied_tracks, 1)

    def test_many_fast_members_do_not_flood_primary_progress_callbacks(self):
        f = self.fixture
        f.playlist.keys = ['new'] * 3300
        events = []
        with operation_scope(Event(), lambda *event: events.append(event)), \
                mock.patch.object(stock_ipod.time, 'monotonic', return_value=42.0):
            plan = self.plan()
        primary = self.stage(events, 'iTunes planning')
        self.assertLessEqual(len(primary), 8)
        self.assertEqual(primary[-1][0], primary[-1][1])
        self.assertEqual(len(plan.copies), 1)

    def test_catalog_planning_reports_bounded_stages_without_reacquiring_audio(self):
        fixture = catalog_fixture.StockCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        database = fixture.target / 'iPod_Control/iTunes/iTunesDB'
        before = database.read_bytes()
        events = []
        with operation_scope(Event(), lambda *event: events.append(event)), \
                mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Catalog read audio')):
            fixture.plan()
        stages = self.stage(events, 'iTunes planning')
        self.assertEqual(stages[0][:2], (0, 4))
        self.assertEqual(stages[-1][:2], (4, 4))
        self.assertEqual(database.read_bytes(), before)
