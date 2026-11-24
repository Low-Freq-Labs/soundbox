"""Fresh commits reuse their own exact final readback; replays retain full proof."""
from dataclasses import replace
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest import mock

import test_source_playlists as fixtures
from soundbox import rekordbox_playlist_store as store
from soundbox.rekordbox_write import RecoveryRequired,ConflictError


class FreshPlaylistTransition(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SourcePlaylists('test_catalog_snapshot_reads_committed_wal_without_rewriting_source_or_rendering_backups')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.writer=store.PlaylistStore(replace(self.f.authority,retain_full_backups=True),self.f.recovery)

    def request(self):
        return store.request_for(self.writer.snapshot(),'rename',identity='1000',name='Fresh exact commit')

    def test_new_commit_edge_uses_original_guarded_readback_without_second_reconstruction(self):
        request=self.request()
        with mock.patch.object(self.writer,'committed_transition',side_effect=AssertionError('Repeated reconstruction')), \
                mock.patch.object(store,'build_plan',wraps=store.build_plan) as plan:
            catalog,edge=self.writer.apply_with_transition(request)
        plan.assert_called_once()
        self.assertEqual(edge,(request['revision'],catalog['revision']))
        self.assertEqual(catalog,self.writer.snapshot())

    def test_fresh_plan_seed_rejects_corruption_in_each_private_or_archived_transfer_before_source_sql(self):
        for transfer in ('before.db','private-after.db','after-integrity','after.db','before.xml','after.xml'):
            with self.subTest(transfer=transfer):
                source=self.f.root/('transfer-'+transfer);shutil.copytree(self.f.database.parent,source)
                writer=store.PlaylistStore(replace(self.writer.authority,database=str(source/'master.db')),
                    self.f.root/('recovery-'+transfer))
                request=store.request_for(writer.snapshot(),'rename',identity='1000',name='Exact transfer')
                before=writer.database.read_bytes(),writer.xml.read_bytes()
                copies=store.copy_state;copyfile=shutil.copyfile;atomic=store.atomic_bytes
                integrity=store._integrity;damaged=[]
                def damage(path):
                    with Path(path).open('ab') as out:out.write(b'unverified-transfer')
                    damaged.append(transfer)
                def archived_copy(src,dst):
                    copies(src,dst)
                    if transfer in ('before.db','after.db') and Path(dst).name==transfer:damage(dst)
                def private_copy(src,dst,*args,**kwargs):
                    result=copyfile(src,dst,*args,**kwargs)
                    if (transfer=='private-after.db' and Path(dst).name=='after.db'
                            and Path(dst).parent.name.startswith('soundbox-playlist-after-')):damage(dst)
                    return result
                def xml_copy(path,data):
                    atomic(path,data)
                    if transfer in ('before.xml','after.xml') and Path(path).name==transfer:damage(path)
                def checked_integrity(conn):
                    integrity(conn)
                    if transfer=='after-integrity':damage(conn.execute('PRAGMA database_list').fetchone()[2])
                with mock.patch.object(store,'copy_state',side_effect=archived_copy), \
                        mock.patch.object(shutil,'copyfile',side_effect=private_copy), \
                        mock.patch.object(store,'atomic_bytes',side_effect=xml_copy), \
                        mock.patch.object(store,'_integrity',side_effect=checked_integrity), \
                        mock.patch.object(writer.lease,'_connect',side_effect=AssertionError('No source SQL')), \
                        self.assertRaisesRegex(RecoveryRequired,'transfer differs'):
                    writer.apply_with_transition(request)
                self.assertEqual(damaged,[transfer])
                self.assertIsNone(writer._verified_recovery_record_key)
                self.assertFalse(writer.journal.exists())
                self.assertEqual((writer.database.read_bytes(),writer.xml.read_bytes()),before)

    def test_wal_private_stage_is_checkpointed_before_its_bracketed_integrity_proof(self):
        conn=store._connect(self.f.database)
        try:self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
        finally:conn.close()
        request=self.request()
        with mock.patch.object(store,'build_plan',wraps=store.build_plan) as plan:
            catalog,edge=self.writer.apply_with_transition(request)
        plan.assert_called_once()
        self.assertEqual(edge,(request['revision'],catalog['revision']))
        archive=self.writer.root/('playlist-'+request['operation_id'])
        conn=store._connect(archive/'after.db',readonly=True)
        try:
            self.assertEqual(conn.execute('SELECT Name FROM djmdPlaylist WHERE ID=?',('1000',)).fetchone(),
                ('Fresh exact commit',))
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone(),('wal',))
        finally:conn.close()

    def test_historical_replay_always_reconstructs_exact_committed_proof(self):
        request=self.request();catalog=self.writer.apply(request)
        with mock.patch.object(self.writer,'committed_transition',wraps=self.writer.committed_transition) as proof:
            observed,edge=self.writer.apply_with_transition(request)
        proof.assert_called_once_with(request)
        self.assertEqual(observed,catalog);self.assertEqual(edge,(request['revision'],catalog['revision']))
        archive=self.writer.root/('playlist-'+request['operation_id'])
        payload=archive/'before.xml';payload.write_bytes(payload.read_bytes()+b' ')
        with self.assertRaises(RecoveryRequired):self.writer.apply_with_transition(request)

    def test_replay_with_independent_native_edit_returns_catalog_but_no_edge(self):
        request=self.request();self.writer.apply(request)
        conn=store._connect(self.f.database)
        try:conn.execute("UPDATE djmdPlaylist SET Name='Independent native edit' WHERE ID='1000'")
        finally:conn.close()
        observed,edge=self.writer.apply_with_transition(request)
        self.assertIsNone(edge);self.assertEqual(observed['nodes'][0]['Name'],'Independent native edit')

    def test_fresh_guard_failure_never_returns_edge_or_skips_recovery(self):
        request=self.request()
        def interrupted(phase):
            if phase=='xml-published':raise RuntimeError('fresh final guard')
        with mock.patch.object(store,'_fault',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'fresh final guard'):
            self.writer.apply_with_transition(request)
        self.assertEqual(self.writer.recover(),'rolled-back')
        self.assertEqual(self.writer.snapshot()['nodes'][0]['Name'],'Existing')

    def test_noop_edge_preserves_original_apply_and_proves_identity_transition(self):
        request=store.request_for(self.writer.snapshot(),'rename',identity='1000',name='Existing')
        before=self.f.database.read_bytes(),self.writer.xml.read_bytes()
        with mock.patch.object(self.writer,'committed_transition',side_effect=AssertionError('No-op has its exact current base')):
            catalog,edge=self.writer.apply_with_transition(request)
        self.assertEqual(edge,(request['revision'],request['revision']))
        self.assertEqual(catalog,self.writer.apply(request))
        self.assertEqual((self.f.database.read_bytes(),self.writer.xml.read_bytes()),before)

    def test_changed_collection_identity_after_commit_cannot_supply_a_fresh_edge(self):
        request=store.request_for(self.writer.snapshot(),'append',identity='1000',
            tracks=[SimpleNamespace(track_id='track',source_uuid='uuid')])
        def changed(phase):
            if phase=='sql-committed':
                conn=store._connect(self.f.database)
                try:conn.execute("UPDATE djmdContent SET UUID='replaced' WHERE ID='track'")
                finally:conn.close()
        with mock.patch.object(store,'_fault',side_effect=changed),self.assertRaisesRegex(ConflictError,'Collection track changed'):
            self.writer.apply_with_transition(request)
        self.assertIsNone(self.writer.committed_transition(request))
