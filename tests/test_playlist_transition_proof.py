"""Read-only committed edges require exact retained evidence and current state."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import rekordbox_playlist_store as store
from soundbox.rekordbox_write import RecoveryRequired,ConflictError
import test_source_playlists as fixtures


class PlaylistTransitionProof(unittest.TestCase):
    def test_pin_validation_does_not_add_an_arbitrary_folder_track_limit(self):
        pins={str(index):'00000000-0000-4000-8000-000000000001' for index in range(100001)}
        store.validate_collection_pins(pins)

    def setUp(self):
        fixture=fixtures.SourcePlaylists('test_catalog_snapshot_reads_committed_wal_without_rewriting_source_or_rendering_backups')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture;self.writer=fixture.writer

    def commit(self,name='Committed'):
        request=store.request_for(self.writer.snapshot(),'rename',identity='1000',name=name)
        after=self.writer.apply(request)
        return request,after

    def test_restarted_store_proves_exact_committed_edge_without_source_mutation(self):
        request,after=self.commit()
        source=self.fixture.database.read_bytes(),self.writer.xml.read_bytes()
        restarted=store.PlaylistStore(self.writer.authority,self.writer.root)
        self.assertEqual(restarted.committed_transition(request),(request['revision'],after['revision']))
        self.assertEqual((self.fixture.database.read_bytes(),self.writer.xml.read_bytes()),source)

    def test_current_native_catalog_or_xml_changes_never_supply_a_rebase(self):
        self.writer=store.PlaylistStore(replace(self.writer.authority,retain_full_backups=True),self.writer.root)
        request,_after=self.commit();conn=store._connect(self.fixture.database)
        try:conn.execute("UPDATE djmdPlaylist SET Name='External' WHERE ID='1000'")
        finally:conn.close()
        self.assertIsNone(self.writer.committed_transition(request))

    def test_changed_request_or_payload_fails_closed(self):
        request,_after=self.commit();changed={**request,'name':'Forged'}
        with self.assertRaisesRegex(RecoveryRequired,'identity changed'):self.writer.committed_transition(changed)
        archive=self.writer.root/('playlist-'+request['operation_id'])
        payload=archive/'before.xml';payload.write_bytes(payload.read_bytes()+b' ')
        with self.assertRaisesRegex(RecoveryRequired,'payload changed'):self.writer.committed_transition(request)

    def test_consistent_forged_receipt_pair_still_requires_pure_plan_reconstruction(self):
        request,_after=self.commit();archive=self.writer.root/('playlist-'+request['operation_id'])
        record=json.loads((archive/'operation.json').read_bytes())
        record['changes'][0]['after']['Name']='Forged receipt'
        (archive/'operation.json').write_text(json.dumps(record))
        (archive/'result.json').write_text(json.dumps({**record,'outcome':'committed'}))
        with self.assertRaisesRegex(RecoveryRequired,'exact committed intent'):self.writer.committed_transition(request)

    def test_valid_retired_pair_keeps_stale_conflict_behavior_and_marker_tampering_fails(self):
        request,_after=self.commit();self.commit('Later')
        archive=self.writer.root/('playlist-'+request['operation_id'])
        self.assertTrue((archive/'retired.json').is_file())
        self.assertIsNone(self.writer.committed_transition(request))
        marker=json.loads((archive/'retired.json').read_bytes());marker['operation_sha256']='0'*64
        (archive/'retired.json').write_text(json.dumps(marker))
        with self.assertRaisesRegex(RecoveryRequired,'retirement proof'):self.writer.committed_transition(request)

    def test_fresh_closed_application_guard_is_never_reused(self):
        request,_after=self.commit()
        with mock.patch.object(store.PilotCueStore,'_guard',side_effect=RuntimeError('fresh guard')):
            with self.assertRaisesRegex(RuntimeError,'fresh guard'):self.writer.committed_transition(request)

    def populated_folder(self):
        folder=store.request_for(self.writer.snapshot(),'create',name='Folder',folder=True)
        self.writer.apply(folder)
        child=store.request_for(self.writer.snapshot(),'create',name='Child',parent=folder['playlist_id'])
        self.writer.apply(child)
        tracks=[SimpleNamespace(track_id=identity,source_uuid=uuid) for identity,uuid in
                (('track','uuid'),('track-two','uuid-two'))]
        self.writer.apply(store.request_for(self.writer.snapshot(),'append',identity=child['playlist_id'],tracks=tracks))
        return store.request_for(self.writer.snapshot(),'delete',identity=folder['playlist_id'])

    def test_folder_pins_capture_descendants_before_delete_and_reconstruct_legacy_replay(self):
        request=self.populated_folder();before=self.fixture.database.read_bytes(),self.writer.xml.read_bytes()
        pins=self.writer.prepare_collection_pins(request)
        self.assertEqual(pins,{'track':'uuid','track-two':'uuid-two'})
        self.assertEqual((self.fixture.database.read_bytes(),self.writer.xml.read_bytes()),before)
        self.writer.apply(request)  # Legacy committed intent has no queue marker.
        restarted=store.PlaylistStore(self.writer.authority,self.writer.root)
        self.assertEqual(restarted.prepare_collection_pins(request),pins)
        self.assertEqual(restarted.apply(request,collection_pins=pins),self.writer.snapshot())

    def test_changed_deleted_or_incomplete_pin_bindings_block_before_source_publication(self):
        request=self.populated_folder();pins=self.writer.prepare_collection_pins(request)
        for field,value in (('UUID','replaced'),('rb_local_deleted',1)):
            conn=store._connect(self.fixture.database)
            try:conn.execute(f'UPDATE djmdContent SET {field}=? WHERE ID=?',(value,'track'))
            finally:conn.close()
            before=self.fixture.database.read_bytes(),self.writer.xml.read_bytes()
            with self.subTest(field=field),self.assertRaises(ConflictError):self.writer.apply(request,collection_pins=pins)
            self.assertEqual((self.fixture.database.read_bytes(),self.writer.xml.read_bytes()),before)
            conn=store._connect(self.fixture.database)
            try:conn.execute(f'UPDATE djmdContent SET {field}=? WHERE ID=?',('uuid' if field=='UUID' else 0,'track'))
            finally:conn.close()
        with self.assertRaises(ConflictError):self.writer.apply(request,collection_pins={})
        self.assertFalse((self.writer.root/('playlist-'+request['operation_id'])).exists())

    def test_relocated_exact_content_identity_keeps_its_captured_collection_pin(self):
        request=self.populated_folder();pins=self.writer.prepare_collection_pins(request)
        conn=store._connect(self.fixture.database)
        try:conn.execute("UPDATE djmdContent SET FolderPath='/never-open/relocated.wav' WHERE ID='track'")
        finally:conn.close()
        self.writer.apply(request,collection_pins=pins)
        self.assertEqual(self.writer.prepare_collection_pins(request),pins)

    def test_captured_durable_pins_survive_retirement_but_legacy_missing_uuid_proof_does_not(self):
        request=self.populated_folder();pins=self.writer.prepare_collection_pins(request)
        self.writer.apply(request,collection_pins=pins)
        self.writer.apply(store.request_for(self.writer.snapshot(),'rename',identity='1000',name='Later native edit'))
        archive=self.writer.root/('playlist-'+request['operation_id'])
        self.assertTrue((archive/'retired.json').is_file())
        with self.assertRaises(RecoveryRequired):self.writer.prepare_collection_pins(request)
        before=self.fixture.database.read_bytes(),self.writer.xml.read_bytes()
        self.writer.apply(request,collection_pins=pins)
        self.assertEqual((self.fixture.database.read_bytes(),self.writer.xml.read_bytes()),before)
        with self.assertRaises(ConflictError):self.writer.apply(request,collection_pins={})

    def test_delete_pin_guard_is_fresh_before_sql_and_after_xml_and_fault_recovers_exactly(self):
        request=self.populated_folder();pins=self.writer.prepare_collection_pins(request)
        before=self.writer.snapshot();xml=self.writer.xml.read_bytes();calls=0
        original=store._require_collection_pins
        def checked(conn,captured):
            nonlocal calls
            calls+=1
            original(conn,captured)
            if calls==2:raise ConflictError('Synthetic changed binding at final guard')
        with mock.patch.object(store,'_require_collection_pins',side_effect=checked):
            with self.assertRaisesRegex(ConflictError,'final guard'):self.writer.apply(request,collection_pins=pins)
        self.assertEqual(calls,2)
        self.assertEqual(self.writer.recover(),'rolled-back')
        self.assertEqual(self.writer.snapshot(),before);self.assertEqual(self.writer.xml.read_bytes(),xml)

    def test_legacy_pin_history_survives_later_native_playlist_edits_without_rebasing_or_restore(self):
        self.writer=store.PlaylistStore(replace(self.writer.authority,retain_full_backups=True),self.writer.root)
        request=self.populated_folder();pins=self.writer.prepare_collection_pins(request)
        self.writer.apply(request)  # No durable marker in this legacy intent.
        self.writer.apply(store.request_for(self.writer.snapshot(),'rename',identity='1000',name='Later native edit'))
        current=self.writer.snapshot();source=self.fixture.database.read_bytes(),self.writer.xml.read_bytes()
        self.assertIsNone(self.writer.committed_transition(request))
        self.assertEqual(self.writer.prepare_collection_pins(request),pins)
        self.assertEqual(self.writer.apply(request,collection_pins=pins),current)
        self.assertEqual((self.fixture.database.read_bytes(),self.writer.xml.read_bytes()),source)


if __name__=='__main__':unittest.main()
