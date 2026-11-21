"""Partial stock updates retain exact old source proofs without acquiring audio."""
from dataclasses import replace
from pathlib import Path
import os
import unittest
from unittest import mock

from soundbox import stock_ipod
from soundbox.stock_ipod_shadow import StockIpodShadow, retained_source_rows
from tests import test_export_playlist_baseline as fixture


class SourceRetention(unittest.TestCase):
    def test_identity_alias_rewrite_removal_and_duplicate_proofs_never_carry(self):
        row = dict(dbid=1, device_path='iPod_Control/Music/F00/A.wav', size=4,
                   source_epoch=[1, 2, 4, 5, 6], source_sha256='a' * 64)
        shadow = StockIpodShadow('device', 'MA446', '', 1, '', 'a' * 64, '', {}, {'/source': row}, {}, {})
        before = [(1, row['device_path'], 4)]
        self.assertEqual(retained_source_rows(shadow, before, before, ()), shadow.sources)
        for after, rewrites in (([], ()), ([(1, row['device_path'], 5)], ()),
                ([(1, 'iPod_Control/Music/F00/B.wav', 4)], ()),
                (before, ['ipod_control/music/f00/a.WAV']),
                (before, ['iPod_Control/Music/./F00/A.wav']), (before + before, ())):
            with self.subTest(after=after, rewrites=rewrites):
                self.assertEqual(retained_source_rows(shadow, before, after, rewrites), {})
        duplicate = replace(shadow, sources={**shadow.sources, '/alias': dict(row)})
        self.assertEqual(retained_source_rows(duplicate, before, before, ()), {})
        # A rewrite through a different DBID still changes this physical payload.
        aliases = [*before, (2, row['device_path'], 4)]
        self.assertEqual(retained_source_rows(shadow, aliases, aliases, [row['device_path']]), {})


class ActualPartialSourceRetention(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ActualExportPlaylistBaseline(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.f = self.case.f
        self.rows = stock_ipod.freeze_display_rows(self.f.library, None)
        self.f.playlist.keys[:] = ['old', 'new']
        self.common = dict(model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            identity_key=self.case.key, shadow_store=self.case.shadow, display_rows=self.rows)
        first = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod, **self.common)
        self.first = stock_ipod.commit_stock_ipod_sync(first)
        self.before = self.case.shadow.load(self.case.key)

    def partial(self):
        self.f.playlist.keys[:] = ['new']
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            managed_playlist_keys_before=tuple(self.before.playlists),
            managed_playlist_names_before=tuple(row['device_name'] for row in self.before.playlists.values()),
            catalog_witness=self.first.catalog_witness, **self.common)
        self.second = stock_ipod.commit_stock_ipod_sync(plan)
        return self.case.shadow.load(self.case.key)

    def test_partial_commit_retains_unselected_proof_then_reuses_it_without_source_or_media_reads(self):
        old = str(self.f.existing_source.resolve()); after = self.partial()
        self.assertEqual(after.sources[old], self.before.sources[old])
        self.f.playlist.keys[:] = ['old']
        real_hash = stock_ipod._sha256
        def hashing(path, *args, **kwargs):
            if 'Music' in Path(path).parts:raise AssertionError('Retained iPod audio was hashed')
            return real_hash(path, *args, **kwargs)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Retained source acquired')), \
                mock.patch.object(stock_ipod, '_sha256', side_effect=hashing):
            plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
                catalog_witness=self.second.catalog_witness, **self.common)
        self.assertTrue(plan.shadow_fast_path); self.assertEqual(plan.copies, ())
        self.assertEqual(plan.reused_track_count, 1)

    def test_unselected_source_change_is_not_sync_drift_but_next_selected_use_reacquires(self):
        old = str(self.f.existing_source.resolve()); after = self.partial()
        observed = self.f.existing_source.stat(); raw = bytearray(self.f.existing_source.read_bytes())
        raw[-1] ^= 1; self.f.existing_source.write_bytes(raw)
        os.utime(self.f.existing_source, ns=(observed.st_atime_ns, observed.st_mtime_ns))
        self.assertEqual(after.sources[old]['source_epoch'], self.before.sources[old]['source_epoch'])
        status = stock_ipod.inspect_stock_ipod_sync_status(self.f.library, self.f.ipod,
            identity_key=self.case.key, model_number='MA446', serial_number='', shadow_store=self.case.shadow,
            catalog_witness=self.second.catalog_witness, display_rows=self.rows)
        self.assertEqual((status.state, status.local_changes), ('up_to_date', 0))
        self.f.playlist.keys[:] = ['old']; acquire = stock_ipod.acquire_source; calls = []
        def capture(contract, *args, **kwargs):
            calls.append(contract.canonical); return acquire(contract, *args, **kwargs)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=capture):
            stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
                catalog_witness=self.second.catalog_witness, **self.common)
        self.assertEqual(calls, [self.f.existing_source.resolve()])

    def test_changed_native_shadow_does_not_carry_unselected_contract(self):
        path = self.f.ipod / 'iPod_Control/iTunes/iTunesDB'
        payload = bytearray(path.read_bytes()); payload[0x20] ^= 1; path.write_bytes(payload)
        self.f.playlist.keys[:] = ['new']
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod, **self.common)
        self.assertFalse(plan.shadow_fast_path)
        self.assertNotIn(str(self.f.existing_source.resolve()), plan.shadow_sources)
