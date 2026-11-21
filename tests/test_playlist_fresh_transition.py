"""Fresh commits reuse their own exact final readback; replays retain full proof."""
from dataclasses import replace
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
        with mock.patch.object(self.writer,'committed_transition',side_effect=AssertionError('Repeated reconstruction')):
            catalog,edge=self.writer.apply_with_transition(request)
        self.assertEqual(edge,(request['revision'],catalog['revision']))
        self.assertEqual(catalog,self.writer.snapshot())

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
