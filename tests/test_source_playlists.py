"""Native playlist source transactions over encrypted synthetic TEMP catalogs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from soundbox import rekordbox_playlist_store as store, rekordbox_cue_store as cues
from soundbox import rekordbox_playlist_plan as planner
from soundbox.rekordbox_tags import _schema, NATIVE_VERSION
from soundbox.rekordbox_write import _connect,_rows,_state,ConflictError,RecoveryRequired,WriteProofError


class SourcePlaylists(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name).resolve();source=self.root/'source';source.mkdir();fixture(source)
        self.database=source/'master.db';conn=_connect(self.database,readonly=True)
        try:self.schema=_schema(conn)
        finally:conn.close()
        self.authority=store.PlaylistAuthority(str(self.database),'synthetic-playlists',self.schema,True)
        self.recovery=self.root/'shared';self.writer=store.PlaylistStore(self.authority,self.recovery)
        for name,value in (('_closed',None),('_installed_version',NATIVE_VERSION)):
            patch=mock.patch.object(cues,name,return_value=value);patch.start();self.addCleanup(patch.stop)

    def request(self,op,**kwargs):
        return store.request_for(self.writer.snapshot(),op,**kwargs)

    def inspect(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn),{t:_rows(conn,t) for t in ('djmdPlaylist','djmdSongPlaylist','agentRegistry','djmdContent')}
        finally:conn.close()

    def test_native_folder_playlist_song_lifecycle_retains_collection_and_xml_history(self):
        original,_=self.inspect()
        folder=self.request('create',name='Folder',folder=True);self.writer.apply(folder)
        playlist=self.request('create',name='Set',parent=folder['playlist_id']);self.writer.apply(playlist)
        identity=playlist['playlist_id']
        self.writer.apply(self.request('rename',identity=identity,name='Renamed'))
        tracks=[SimpleNamespace(track_id=k,source_uuid=v) for k,v in (('track','uuid'),('track-two','uuid-two'))]
        self.writer.apply(self.request('append',identity=identity,tracks=tracks))
        self.writer.apply(self.request('remove',identity=identity,tracks=tracks[:1]))
        state,rows=self.inspect()
        active=[r for r in rows['djmdSongPlaylist'] if r['PlaylistID']==identity and r['rb_local_deleted']==0]
        self.assertEqual([(r['ContentID'],r['TrackNo']) for r in active],[('track-two',1)])
        self.assertEqual(state['tables']['djmdContent'],original['tables']['djmdContent'])
        self.assertEqual(state['tables']['opaque_table'],original['tables']['opaque_table'])
        xml_before=self.writer.xml.read_bytes()
        self.writer.apply(self.request('delete',identity=folder['playlist_id']))
        state,rows=self.inspect()
        self.assertEqual([r['Name'] for r in rows['djmdPlaylist']],['Existing'])
        self.assertEqual(rows['djmdSongPlaylist'],[])
        self.assertEqual(self.writer.xml.read_bytes(),xml_before)
        self.assertEqual(state['tables']['djmdContent'],original['tables']['djmdContent'])
        self.assertFalse(self.writer.journal.exists())

    def test_remove_legacy_last_song_retains_tombstone_and_updates_all_survivors_once(self):
        identity='1000';track=SimpleNamespace(track_id='track',source_uuid='uuid')
        for _ in range(3):self.writer.apply(self.request('append',identity=identity,tracks=[track],allow_duplicate=True))
        conn=_connect(self.database)
        try:conn.execute('UPDATE djmdSongPlaylist SET rb_local_synced=1')
        finally:conn.close()
        _,rows=self.inspect();members=rows['djmdSongPlaylist'];counter=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')
        target=max(members,key=lambda r:r['TrackNo']);before=copy.deepcopy(target)
        plan=planner.remove(rows['djmdPlaylist'][0],members,counter,target['ID'],timestamp='deleted',survivor_timestamp='survived')
        self.assertEqual(plan['counter']['int_1'],counter['int_1']+2)
        tombstone=next(r for r in plan['members'] if r['ID']==target['ID'])
        self.assertEqual((tombstone['rb_data_status'],tombstone['rb_local_deleted']),(2,1))
        self.assertEqual(tombstone['UUID'],before['UUID']);self.assertEqual(tombstone['created_at'],before['created_at'])
        survivors=[r for r in plan['members'] if r['rb_local_deleted']==0]
        self.assertEqual({r['rb_local_usn'] for r in survivors},{counter['int_1']+2})
        self.assertEqual({r['updated_at'] for r in survivors},{'survived'})
        self.assertEqual([r['TrackNo'] for r in sorted(survivors,key=lambda r:r['TrackNo'])],[1,2])

    def test_legacy_playlist_delete_preserves_rows_and_missing_xml_with_native_three_groups(self):
        one=SimpleNamespace(track_id='track',source_uuid='uuid')
        two=SimpleNamespace(track_id='track-two',source_uuid='uuid-two')
        self.writer.apply(self.request('append',identity='1000',tracks=[one,two]))
        conn=_connect(self.database)
        try:
            conn.execute('UPDATE djmdPlaylist SET rb_local_synced=1')
            conn.execute('UPDATE djmdSongPlaylist SET rb_local_synced=1')
        finally:conn.close()
        before=self.writer.xml.read_bytes()
        line=next(line for line in before.splitlines(keepends=True) if b'Id="3E8"' in line)
        self.writer.xml.write_bytes(before.replace(line,b''));before_xml=self.writer.xml.read_bytes()
        state,rows=self.inspect();counter=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')['int_1']
        self.writer.apply(self.request('delete',identity='1000'))
        after,rows=self.inspect();node=rows['djmdPlaylist'][0]
        self.assertEqual((node['rb_local_deleted'],node['rb_local_usn']),(1,counter+1))
        self.assertEqual({r['rb_local_usn'] for r in rows['djmdSongPlaylist']},{counter+2})
        self.assertEqual({r['rb_local_deleted'] for r in rows['djmdSongPlaylist']},{1})
        self.assertEqual(next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')['int_1'],counter+3)
        self.assertEqual(self.writer.xml.read_bytes(),before_xml)
        self.assertEqual(after['tables']['djmdContent'],state['tables']['djmdContent'])

    def test_older_empty_and_folder_deletions_keep_native_history_xml_and_mixed_member_lifetimes(self):
        empty=self.request('create',name='Old empty');self.writer.apply(empty)
        folder=self.request('create',name='Old folder',folder=True);self.writer.apply(folder)
        child=self.request('create',name='Inside',parent=folder['playlist_id']);self.writer.apply(child)
        other=self.request('create',name='Empty child',parent=folder['playlist_id']);self.writer.apply(other)
        tracks=[SimpleNamespace(track_id=k,source_uuid=v) for k,v in (('track','uuid'),('track-two','uuid-two'))]
        self.writer.apply(self.request('append',identity=child['playlist_id'],tracks=tracks))
        conn=_connect(self.database)
        try:
            for request in (empty,folder,child,other):
                conn.execute('UPDATE djmdPlaylist SET rb_local_synced=1 WHERE ID=?',(request['playlist_id'],))
            conn.execute('UPDATE djmdSongPlaylist SET rb_local_synced=1 WHERE ContentID=?',('track',))
        finally:conn.close()
        before,rows=self.inspect();xml=self.writer.xml.read_bytes()
        counter=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')
        proposal=planner.delete(rows['djmdPlaylist'],rows['djmdSongPlaylist'],counter,
                                identity=empty['playlist_id'],timestamp='node',sibling_timestamp='order')
        tombstone=next(r for r in proposal['playlists'] if r['ID']==empty['playlist_id'])
        shifted=next(r for r in proposal['playlists'] if r['ID']==folder['playlist_id'])
        self.assertEqual((tombstone['updated_at'],tombstone['rb_local_usn']),('node',counter['int_1']+1))
        self.assertEqual((shifted['updated_at'],shifted['rb_local_usn']),('order',counter['int_1']+2))
        self.writer.apply(self.request('delete',identity=empty['playlist_id']))
        _,rows=self.inspect();count=next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')['int_1']
        self.writer.apply(self.request('delete',identity=folder['playlist_id']))
        after,rows=self.inspect()
        nodes=[r for r in rows['djmdPlaylist'] if r['ID'] in {folder['playlist_id'],child['playlist_id'],other['playlist_id']}]
        self.assertEqual(len(nodes),3);self.assertEqual({r['rb_local_usn'] for r in nodes},{count+1})
        self.assertEqual({r['rb_local_deleted'] for r in nodes},{1})
        self.assertEqual([(r['ContentID'],r['rb_local_deleted'],r['rb_local_usn']) for r in rows['djmdSongPlaylist']], [('track',1,count+2)])
        self.assertEqual(next(r for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')['int_1'],count+3)
        self.assertEqual(self.writer.xml.read_bytes(),xml)
        self.assertEqual(after['tables']['djmdContent'],before['tables']['djmdContent'])

    def test_multi_selection_composes_single_entry_edits_and_preserves_unselected_duplicate(self):
        track=SimpleNamespace(track_id='track',source_uuid='uuid')
        self.writer.apply(self.request('append',identity='1000',tracks=[track]*3,allow_duplicate=True))
        self.writer.apply(self.request('remove',identity='1000',tracks=[track]*2))
        _,rows=self.inspect();active=[r for r in rows['djmdSongPlaylist'] if r['rb_local_deleted']==0]
        self.assertEqual([(r['ContentID'],r['TrackNo']) for r in active],[('track',1)])
        self.assertEqual(len(rows['djmdContent']),2)

    def test_untitled_create_without_rename_keeps_initial_xml_clock_zero(self):
        from soundbox import rekordbox_playlist_xml as codec
        request=self.request('create',name='Untitled Playlist');self.writer.apply(request)
        self.assertEqual(codec._target(self.writer.xml.read_bytes(),request['playlist_id'])[0]['Timestamp'],'0')

    def test_operation_receipt_prevents_duplicate_native_create_after_local_crash(self):
        request=self.request('create',name='Once');self.writer.apply(request)
        first=self.inspect();raw=self.database.read_bytes(),self.writer.xml.read_bytes()
        self.writer.apply(request)
        self.assertEqual(self.inspect(),first)
        self.assertEqual((self.database.read_bytes(),self.writer.xml.read_bytes()),raw)

    def test_stale_revision_changed_uuid_and_missing_permission_never_publish(self):
        request=self.request('rename',identity='1000',name='Changed')
        stale=copy.deepcopy(request);stale['revision']='0'*64
        baseline=self.inspect();xml=self.writer.xml.read_bytes()
        with self.assertRaises(ConflictError):self.writer.apply(stale)
        changed=copy.deepcopy(request);changed['playlist_uuid']='00000000-0000-4000-8000-000000000099'
        with self.assertRaises(ConflictError):self.writer.apply(changed)
        with self.assertRaises(WriteProofError):store.PlaylistAuthority(str(self.database),'synthetic-playlists',self.schema,False)
        self.assertEqual(self.inspect(),baseline);self.assertEqual(self.writer.xml.read_bytes(),xml)

    def test_cloud_status_and_foreign_domain_journal_fail_closed(self):
        conn=_connect(self.database)
        try:conn.execute('UPDATE djmdPlaylist SET usn=9 WHERE ID=\'1000\'')
        finally:conn.close()
        request=self.request('rename',identity='1000',name='Denied')
        with self.assertRaises(ValueError):self.writer.apply(request)
        self.writer.journal.write_text(json.dumps({'format':'another-source-domain'}))
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertTrue(self.writer.journal.exists())

    def test_unrelated_trigger_is_denied_before_any_live_write(self):
        conn=_connect(self.database)
        try:conn.execute("CREATE TRIGGER no_side_effect AFTER UPDATE ON djmdPlaylist BEGIN UPDATE opaque_table SET data=X'22'; END")
        finally:conn.close()
        # Rebind only this synthetic test's schema; production schema changes
        # never provision an authority automatically.
        conn=_connect(self.database,readonly=True)
        try:schema=_schema(conn)
        finally:conn.close()
        writer=store.PlaylistStore(store.PlaylistAuthority(str(self.database),'synthetic-playlists',schema,True),self.root/'trigger-recovery')
        request=store.request_for(writer.snapshot(),'rename',identity='1000',name='Denied')
        before=self.database.read_bytes(),writer.xml.read_bytes()
        with self.assertRaises(Exception):writer.apply(request)
        self.assertEqual((self.database.read_bytes(),writer.xml.read_bytes()),before)

    def test_corrupt_payload_unknown_xml_and_pilot_scope_block_recovery_before_sql(self):
        request=self.request('rename',identity='1000',name='New')
        def interrupt(phase):
            if phase=='xml-published':raise RuntimeError('interrupt')
        with mock.patch.object(store,'_fault',side_effect=interrupt):
            with self.assertRaises(RuntimeError):self.writer.apply(request)
        self.assertIsNotNone(self.writer._record())
        self.writer.xml.write_bytes(self.writer.xml.read_bytes().replace(b'unknown note',b'foreign note'))
        before=self.database.read_bytes(),self.writer.xml.read_bytes()
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertEqual((self.database.read_bytes(),self.writer.xml.read_bytes()),before)
        record=json.loads(self.writer.journal.read_bytes());archive=self.recovery/record['archive']
        (archive/'before.db').write_bytes(b'corrupt')
        with mock.patch.object(self.writer.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):self.writer.recover()
            connection.assert_not_called()

    def test_first_legacy_edit_creates_missing_xml_node_at_zero_then_touches_it(self):
        from soundbox import rekordbox_playlist_xml as codec
        conn=_connect(self.database)
        try:conn.execute('UPDATE djmdPlaylist SET rb_local_synced=1')
        finally:conn.close()
        before=self.writer.xml.read_bytes()
        line=next(line for line in before.splitlines(keepends=True) if b'Id="3E8"' in line)
        self.writer.xml.write_bytes(before.replace(line,b''))
        one=SimpleNamespace(track_id='track',source_uuid='uuid')
        self.writer.apply(self.request('append',identity='1000',tracks=[one]))
        self.assertEqual(codec._target(self.writer.xml.read_bytes(),'1000')[0]['Timestamp'],'0')
        two=SimpleNamespace(track_id='track-two',source_uuid='uuid-two')
        self.writer.apply(self.request('append',identity='1000',tracks=[two]))
        self.assertGreater(int(codec._target(self.writer.xml.read_bytes(),'1000')[0]['Timestamp']),0)

    def test_committed_retirement_is_bounded_and_old_receipt_replay_keeps_latest_pair(self):
        first=self.request('create',name='First');self.writer.apply(first)
        second=self.request('create',name='Second');self.writer.apply(second)
        one=self.recovery/('playlist-'+first['operation_id']);two=self.recovery/('playlist-'+second['operation_id'])
        self.assertFalse((one/'before.db').exists());self.assertTrue((one/'retired.json').exists())
        self.assertTrue((one/'before.xml').exists());self.assertTrue((one/'result.json').exists())
        self.assertTrue((two/'before.db').exists());self.assertTrue((two/'after.db').exists())
        self.writer.apply(first)
        self.assertTrue((two/'before.db').exists());self.assertTrue((two/'after.db').exists())
        pointer=json.loads((self.recovery/'playlist-latest.json').read_bytes())
        self.assertEqual(pointer['current']['archive'],two.name)

    def test_acceptance_pairs_are_never_adopted_by_normal_retirement(self):
        from dataclasses import replace
        proof=store.PlaylistStore(replace(self.authority,retain_full_backups=True),self.recovery)
        first=store.request_for(proof.snapshot(),'create',name='Native acceptance');proof.apply(first)
        self.writer.apply(self.request('create',name='Ordinary'))
        archive=self.recovery/('playlist-'+first['operation_id'])
        self.assertTrue((archive/'before.db').exists());self.assertTrue((archive/'after.db').exists())

    def test_explicit_retry_forks_rolled_back_intent_but_replays_committed_intent_unchanged(self):
        request=self.request('rename',identity='1000',name='New')
        def interrupt(phase):
            if phase=='xml-published':raise RuntimeError('interrupt')
        with mock.patch.object(store,'_fault',side_effect=interrupt):
            with self.assertRaises(RuntimeError):self.writer.apply(request)
        retry=self.writer.prepare_retry(request)
        self.assertNotEqual(retry['operation_id'],request['operation_id'])
        self.writer.apply(retry)
        self.assertEqual(self.writer.prepare_retry(retry),retry)
        before=self.database.read_bytes(),self.writer.xml.read_bytes()
        self.writer.apply(retry)
        self.assertEqual((self.database.read_bytes(),self.writer.xml.read_bytes()),before)

    def test_explicit_retry_after_external_edit_renews_the_xml_clock_as_well_as_revision(self):
        request=self.request('rename',identity='1000',name='Wanted')
        request['xml_timestamp']=1
        self.writer.apply(self.request('rename',identity='1000',name='External change'))
        with self.assertRaises(ConflictError):self.writer.apply(request)
        retry=self.writer.prepare_retry(request)
        self.assertGreater(retry['xml_timestamp'],request['xml_timestamp'])
        self.writer.apply(retry)
        _,rows=self.inspect();self.assertEqual(rows['djmdPlaylist'][0]['Name'],'Wanted')

    def test_wrong_xml_parent_cannot_be_touched_by_a_membership_edit(self):
        self.writer.xml.write_bytes(self.writer.xml.read_bytes().replace(b'ParentId="0"',b'ParentId="99"'))
        before=self.database.read_bytes(),self.writer.xml.read_bytes()
        track=SimpleNamespace(track_id='track',source_uuid='uuid')
        with self.assertRaisesRegex(ConflictError,'DB/XML identity'):
            self.writer.apply(self.request('append',identity='1000',tracks=[track]))
        self.assertEqual((self.database.read_bytes(),self.writer.xml.read_bytes()),before)

    def test_unknown_row_sync_counter_or_uuid_and_malformed_intent_clock_fail_closed(self):
        _,rows=self.inspect();node=rows['djmdPlaylist'][0]
        for fields in ({'rb_local_usn':None},{'rb_local_usn':-1},{'UUID':'unknown'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):planner.local({**node,**fields})
        request=self.request('rename',identity='1000',name='Denied');request['timestamp']=None
        with mock.patch.object(self.writer.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):self.writer.apply(request)
            connection.assert_not_called()

    def test_request_path_injection_is_rejected_before_recovery_or_source_connection(self):
        request=self.request('rename',identity='1000',name='Denied')
        request['operation_id']='../../outside'
        with mock.patch.object(self.writer.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):self.writer.apply(request)
            connection.assert_not_called()

    def test_real_process_exit_recovers_exact_sql_and_xml_pair(self):
        program=r'''
import json,os,sys
from pathlib import Path
from soundbox import rekordbox_playlist_store as s,rekordbox_cue_store as c
c._closed=lambda:None;c._installed_version=lambda:'6.8.7.0303'
data=json.loads(sys.argv[1]);data['pilot_playlist_ids']=tuple(data['pilot_playlist_ids'])
a=s.PlaylistAuthority(**data);w=s.PlaylistStore(a,Path(sys.argv[2]))
original=w.lease._connect
def small_cache():
 conn=original();conn.execute('PRAGMA cache_size=8');return conn
w.lease._connect=small_cache
s._fault=lambda phase:os._exit(79) if phase==sys.argv[4] else None
w.apply(json.loads(Path(sys.argv[3]).read_text()))
'''
        from dataclasses import asdict
        original=self.inspect()[0]
        for index,phase in enumerate(('journal-ready','sql-applied','xml-published','sql-committed')):
            with self.subTest(phase=phase):
                request=self.request('rename',identity='1000',name=f'after-{index}'+('x'*400_000))
                path=self.root/'request.json';path.write_text(json.dumps(request))
                result=subprocess.run([sys.executable,'-c',program,json.dumps(asdict(self.authority)),str(self.recovery),str(path),phase],env=os.environ.copy(),capture_output=True,text=True)
                self.assertEqual(result.returncode,79,result.stderr)
                expected='committed' if phase=='sql-committed' else 'rolled-back'
                self.assertEqual(self.writer.recover(),expected)
                self.assertFalse(self.writer.journal.exists())
                # The next shared-lane write must recognize an exact inactive
                # SQLite journal left by recovery, without removing it manually.
                self.writer.apply(self.request('rename',identity='1000',name=f'continuation-{index}'))
                actual=self.inspect()[0]
                self.assertEqual(actual['tables']['opaque_table'],original['tables']['opaque_table'])
                self.assertEqual(actual['tables']['djmdContent'],original['tables']['djmdContent'])
