"""Native-only generated order transactions: no source or device audio exists."""
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import struct
import unittest
from unittest import mock

from clickwheel.ipod import get_ipod_tracks, parse_itunesdb
from clickwheel.ipod.itunesdb_writer import PlaylistInfo, SmartPlaylistPrefs, SmartPlaylistRules

from soundbox import stock_ipod
from soundbox import stock_playlist_order
from soundbox.operation_control import CommittedOperationError
from soundbox.ipod_export_witness import stored_metadata, verify_export_witness
from soundbox.stock_ipod_catalog_state import CatalogWitnessRow, StockCatalogWitness
from soundbox.stock_ipod_native_preserve import _datasets, _list
from soundbox.stock_ipod_playlist_binding import PlaylistBinding
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.stock_ipod_signing import verified_stock_signing_context
from soundbox.stock_playlist_order import plan_stock_playlist_order, commit_stock_playlist_order
from soundbox.sync import DeviceSession
from tests import test_stock_ipod_catalog as fixtures
from tests import test_stock_ipod_signing as signing
from tests.support.ipod import play_counts


class StockPlaylistOrder(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.StockCatalogTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root = self.case.target; self.db = self.root/'iPod_Control/iTunes/iTunesDB'
        self.case.smart = [PlaylistInfo('Videos', [], playlist_id=903, mhsd5_type=2,
                                      smart_prefs=SmartPlaylistPrefs(), smart_rules=SmartPlaylistRules())]
        self.case.write_catalog(); self.snapshot = self.case.read_snapshot()
        self.pc = self.db.with_name('Play Counts'); self.history = play_counts([(2,1,80,12000)]*3)
        self.pc.write_bytes(self.history)
        self.enterContext(mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('order acquired audio')))
        self.hash = stock_ipod._sha256
        def no_media_hash(path):
            if 'Music' in Path(path).parts:raise AssertionError('order hashed media')
            return self.hash(path)
        self.enterContext(mock.patch.object(stock_ipod, '_sha256', side_effect=no_media_hash))

    def plan(self, order=(902,901), **options):
        return plan_stock_playlist_order(self.root, order, snapshot=self.snapshot,
            model_number='MA446', filesystem='fat32', usb_family='iPod Video', **options)

    def test_empty_regular_system_alias_is_anchored_outside_the_draggable_domain(self):
        self.case.playlists.append(PlaylistInfo('Videos', [], playlist_id=904))
        self.case.write_catalog(); self.snapshot = self.case.read_snapshot()
        before = self.db.read_bytes()
        plan = self.plan()
        commit_stock_playlist_order(plan)
        parsed = parse_itunesdb(io.BytesIO(self.db.read_bytes()))
        self.assertEqual([row['playlistID'] for row in parsed['mhlp'] if not row.get('isMaster')],
                         [902, 901, 904])
        before_chunks = _list(_datasets(before)[1][2])[1]
        after_chunks = _list(_datasets(self.db.read_bytes())[1][2])[1]
        self.assertEqual(before_chunks[-1], after_chunks[-1])

    def test_chunk_permutation_preserves_opaque_tracks_system_playlists_and_play_counts(self):
        original = self.db.read_bytes(); plan = self.plan()
        old_root, old = _datasets(original); new_root, new = _datasets(plan.sync_plan.rendered_database)
        self.assertEqual(old_root, new_root)
        for kind in old:
            if kind not in {2,3}:self.assertEqual(old[kind], new[kind])
            else:
                old_rows = _list(old[kind])[1]; new_rows = _list(new[kind])[1]
                self.assertCountEqual(old_rows, new_rows)
                self.assertEqual(old_rows[0], new_rows[0])  # Native master remains anchored.
        result = commit_stock_playlist_order(plan)
        parsed = parse_itunesdb(io.BytesIO(self.db.read_bytes()))
        self.assertEqual([row['Title'] for row in parsed['mhlp'] if not row.get('isMaster')], ['Other','Favorites'])
        self.assertEqual(get_ipod_tracks(parsed), get_ipod_tracks(parse_itunesdb(io.BytesIO(original))))
        self.assertEqual(self.pc.read_bytes(), self.history)
        self.assertEqual((result.copied_tracks,result.rewritten_tracks,result.deleted_media_files), (0,0,0))
        self.assertEqual(result.catalog_playlist_updates, 2)
        self.assertFalse((self.root/'iPod_Control/Music').exists())
        self.assertEqual((result.recovery_directory/'iTunesDB.before').read_bytes(), original)
        self.assertFalse((result.recovery_directory/'Play Counts.consumed').exists())
        with self.assertRaisesRegex(stock_ipod.StockIpodError,'cannot enter composite rollback'):
            stock_ipod.rollback_committed_stock_ipod_sync(plan.sync_plan,result)
        self.assertEqual(self.pc.read_bytes(), self.history)

    def test_identical_order_with_pending_history_is_read_only_and_idempotent(self):
        before = self.db.read_bytes(); plan = self.plan((901,902))
        self.assertFalse(plan.requires_commit)
        with mock.patch.object(DeviceSession,'atomic_write',side_effect=AssertionError('no-op wrote device')):
            commit_stock_playlist_order(plan)
        self.assertEqual(self.db.read_bytes(),before); self.assertEqual(self.pc.read_bytes(),self.history)
        self.assertFalse((self.root/'.soundbox').exists())

    def test_exact_regular_inventory_and_uint64_identities_are_required(self):
        for order in ((901,), (901,902,903), (901,901), (901,-1), (901,2**64), (901,True), ('901',902)):
            with self.subTest(order=order), self.assertRaises(stock_ipod.StockIpodError):self.plan(order)
        with self.assertRaises(stock_ipod.StockIpodError):
            plan_stock_playlist_order(self.root,(902,901),snapshot=replace(self.snapshot,target=self.root.parent),
                                     model_number='MA446',filesystem='fat32')
        (self.root/'.rockbox').mkdir()
        with self.assertRaises(stock_ipod.StockIpodError):self.plan()

    def test_changed_catalog_sidecar_and_mount_refuse_before_writes(self):
        plan = self.plan(); before = self.db.read_bytes()
        for changed in ('database','sidecar','mount'):
            with self.subTest(changed=changed):
                candidate=plan
                if changed=='database':self.db.write_bytes(before+b'changed')
                if changed=='sidecar':self.pc.write_bytes(self.history+b'changed')
                if changed=='mount':candidate=replace(plan,sync_plan=replace(plan.sync_plan,target_mount_identity=(-1,-1)))
                with mock.patch.object(DeviceSession,'atomic_write',side_effect=AssertionError('stale plan wrote device')):
                    with self.assertRaises((ValueError,RuntimeError)):commit_stock_playlist_order(candidate)
                self.db.write_bytes(before);self.pc.write_bytes(self.history)
        self.db.write_bytes(before+b'changed')
        with self.assertRaises(stock_ipod.StockIpodError):self.plan()

    def test_preserve_sidecar_flag_cannot_admit_track_or_unknown_native_mutation(self):
        plan=self.plan(); raw=bytearray(plan.sync_plan.rendered_database)
        root, datasets=_datasets(bytes(raw)); tracks=_list(datasets[1])[1]
        offset=bytes(raw).index(tracks[0]);raw[offset+0x14]^=1
        forged=replace(plan.sync_plan,rendered_database=bytes(raw))
        with mock.patch.object(DeviceSession,'atomic_write',side_effect=AssertionError('forged plan wrote device')):
            with self.assertRaisesRegex(stock_ipod.StockIpodError,'outside playlist ordering'):stock_ipod.commit_stock_ipod_sync(forged)

    def test_duplicate_native_playlist_ids_are_refused_without_repairing_catalog(self):
        raw=bytearray(self.db.read_bytes())
        for (kind, identity), (offset, _header) in stock_ipod._catalog_playlist_headers(bytes(raw)).items():
            if kind in {2,3} and identity==902:struct.pack_into('<Q',raw,offset+0x1C,901)
        self.db.write_bytes(raw);self.snapshot=self.case.read_snapshot()
        with self.assertRaisesRegex(stock_ipod.StockIpodError,'exactly once'):self.plan()
        self.assertEqual(self.db.read_bytes(),bytes(raw))

    def test_history_change_during_commit_preparation_refuses_without_consuming_sidecar(self):
        plan=self.plan(); before=self.db.read_bytes(); write=DeviceSession.atomic_write
        def changed_after_recovery(session,path,data):
            write(session,path,data)
            if Path(path).name=='plan.json':self.pc.write_bytes(self.history+b'new history')
        with mock.patch.object(DeviceSession,'atomic_write',changed_after_recovery):
            with self.assertRaisesRegex(stock_ipod.StockIpodError,'Play Counts changed before'):
                commit_stock_playlist_order(plan)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(self.pc.read_bytes(),self.history+b'new history')

    def test_readback_failure_reports_committed_and_never_renews_comparison(self):
        plan=self.plan()
        with mock.patch.object(stock_playlist_order,'capture_stock_ipod_native',side_effect=OSError('fixture readback lost')):
            with self.assertRaisesRegex(CommittedOperationError,'order saved') as caught:
                commit_stock_playlist_order(plan)
        self.assertTrue(caught.exception.committed)
        self.assertEqual(self.db.read_bytes(),plan.sync_plan.rendered_database)

    def test_database_write_failure_keeps_sidecar_and_rolls_back(self):
        plan=self.plan(); before=self.db.read_bytes(); write=DeviceSession.atomic_write
        def failing(session,path,data):
            if Path(path).resolve()==self.db.resolve():raise OSError('generated DB write failure')
            return write(session,path,data)
        with mock.patch.object(DeviceSession,'atomic_write',failing):
            with self.assertRaisesRegex(OSError,'generated DB write failure'):commit_stock_playlist_order(plan)
        self.assertEqual(self.db.read_bytes(),before);self.assertEqual(self.pc.read_bytes(),self.history)

    def test_exact_saved_witness_and_shadow_renew_only_after_proven_order_readback(self):
        original=self.db.read_bytes(); parsed=parse_itunesdb(io.BytesIO(original))
        rows=get_ipod_tracks(parsed); by_dbid={row['dbid']:row for row in rows}
        witness=StockCatalogWitness((str(self.case.library.source),'rekordbox-xml',''), self.snapshot.identity_key,
            hashlib.sha256(original).hexdigest(),tuple(CatalogWitnessRow(str(row['dbid']),str(row['dbid']),'',
                '/unavailable/source.wav',row['dbid'],row['trackID'],row['location'],stored_metadata(row)) for row in rows),
            bindings=(PlaylistBinding('known','', '',901,'Favorites',(('102',102),('101',101))),))
        store=StockIpodShadowStore(self.case.base/'shadow')
        baseline=store.committed(identity_key=self.snapshot.identity_key,model='MA446',serial_number='',
            database_sha256=hashlib.sha256(original).hexdigest(),artwork_database_sha256='',ithmb={},
            sources={},artwork={},playlists={})
        result=commit_stock_playlist_order(self.plan(catalog_witness=witness,shadow_store=store))
        self.assertTrue(result.shadow_updated)
        self.assertEqual(store.load(self.snapshot.identity_key).database_sha256,result.database_sha256)
        self.assertEqual(store.load(self.snapshot.identity_key).sources,baseline.sources)
        verify_export_witness(result.catalog_witness,self.db.read_bytes(),'')
        self.assertEqual(result.catalog_witness.bindings,witness.bindings)
        self.assertEqual({row['dbid']:row for row in get_ipod_tracks(parse_itunesdb(io.BytesIO(self.db.read_bytes())))},by_dbid)

    def test_stale_shadow_is_not_blessed_by_native_ordering(self):
        store=StockIpodShadowStore(self.case.base/'shadow')
        baseline=store.committed(identity_key=self.snapshot.identity_key,model='MA446',serial_number='',
            database_sha256='a'*64,artwork_database_sha256='',ithmb={},sources={},artwork={},playlists={})
        result=commit_stock_playlist_order(self.plan(shadow_store=store))
        self.assertFalse(result.shadow_updated);self.assertEqual(store.load(self.snapshot.identity_key),baseline)

    def test_classic_signing_reorders_only_with_current_verified_guid(self):
        case=signing.ClassicSigningTests();case.setUp();self.addCleanup(case.doCleanups)
        root=case.fixture.target
        with self.assertRaises(stock_ipod.StockIpodError):
            plan_stock_playlist_order(root,(902,901),snapshot=case.snapshot,model_number='',filesystem='fat32',
                                      usb_family='iPod Classic',firewire_guid='FFEEDDCCBBAA9988')
        plan=plan_stock_playlist_order(root,(902,901),snapshot=case.snapshot,model_number='',filesystem='fat32',
                                      usb_family='iPod Classic',firewire_guid=signing.GUID)
        commit_stock_playlist_order(plan)
        data=case.db.read_bytes();self.assertEqual(verified_stock_signing_context(data,signing.GUID),case.context)
        self.assertEqual(data[0xD0:0xD4],b'keep')
