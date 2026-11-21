"""Independent normalized ordinary creation; generated encrypted TEMP data only."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest import mock

import test_source_playlists as fixtures
from soundbox import rekordbox_playlist_store as store,rekordbox_playlist_create as create
from soundbox import rekordbox_playlist_plan as plan,rekordbox_playlist_xml as xml
from soundbox.rekordbox_write import _connect,_state,_rows,WriteProofError,ConflictError


class NormalizedPlaylistCreation(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SourcePlaylists('test_catalog_snapshot_reads_committed_wal_without_rewriting_source_or_rendering_backups')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.database=self.f.database
        conn=_connect(self.database)
        try:
            old=store.row(conn,'djmdPlaylist','1000');sibling={**old,'ID':'2000','UUID':'00000000-0000-4000-8000-000000002000',
                'Seq':3,'Attribute':4,'Name':'Preserved smart','SmartList':plan.smart_tag_rule('2000',['1']),'rb_local_synced':1}
            conn.execute('INSERT INTO djmdPlaylist ('+','.join(sibling)+') VALUES('+','.join('?' for _ in sibling)+')',tuple(sibling.values()))
        finally:conn.close()
        self.xml=self.f.writer.xml;self.xml.write_bytes(xml.append_smart_node(self.xml.read_bytes(),'2000'))
        self.authority=replace(self.f.authority,normalized_create_accepted=True,normalized_create_profile=store.NORMALIZED_CREATE_PROFILE)
        self.writer=store.PlaylistStore(self.authority,self.f.root/'normalized-recovery')

    def inspect(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn),{name:_rows(conn,name) for name in ('djmdPlaylist','agentRegistry','djmdSongPlaylist')}
        finally:conn.close()

    def request(self,**options):
        return store.request_for(self.writer.snapshot(),'create_normalized',name=options.pop('name','New ordinary'),**options)

    def test_old_crud_and_flag_without_exact_profile_refuse_before_lease_or_backup(self):
        request=self.request();before=self.inspect(),self.xml.read_bytes()
        for authority in (self.f.authority,replace(self.f.authority,normalized_create_accepted=True)):
            writer=store.PlaylistStore(authority,self.f.root/'blocked')
            with self.subTest(authority=authority),mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('Lease entered')),\
                    mock.patch.object(store,'encrypted_backup',side_effect=AssertionError('Backup entered')):
                with self.assertRaises(WriteProofError):writer.apply(request)
                with self.assertRaises(WriteProofError):writer.prepare_retry(request)
        self.assertEqual((self.inspect(),self.xml.read_bytes()),before)

    def test_normalized_command_cannot_borrow_ordinary_batch_permission(self):
        request=self.request();batch=dict(operation='batch',operation_id='a'*32,revision=request['revision'],requests=[request])
        writer=store.PlaylistStore(self.f.authority,self.f.root/'batch-blocked')
        with mock.patch.object(writer.lease,'_lock',side_effect=AssertionError('Batch entered source lease')),\
                mock.patch.object(writer.lease,'_connect',side_effect=AssertionError('Batch opened source')):
            with self.assertRaises(WriteProofError):writer.apply(batch)
        with self.assertRaises(WriteProofError):store.batch_for(self.writer.snapshot(),[request])

    def test_gapped_root_append_uses_ordinal_positions_and_preserves_every_other_field_and_xml(self):
        before,rows=self.inspect();old={r['ID']:r for r in rows['djmdPlaylist']};raw=self.xml.read_bytes()
        counter=next(r['int_1'] for r in rows['agentRegistry'] if r['registry_id']=='localUpdateCount')
        self.assertTrue(store.normalized_creation_required(self.writer.snapshot(),'root'))
        request=self.request();observed,edge=self.writer.apply_with_transition(request)
        after,current=self.inspect();new={r['ID']:r for r in current['djmdPlaylist']};created=new.pop(request['playlist_id'])
        self.assertEqual((created['Seq'],created['Name'],created['rb_local_usn']),(3,'New ordinary',counter+3))
        self.assertEqual(new['1000'],old['1000'])
        self.assertEqual(new['2000'],{**old['2000'],'Seq':2,'updated_at':request['normalized_create_clocks']['sibling_timestamps']['2000'],
                                      'rb_local_usn':counter+2})
        self.assertEqual(edge,(request['revision'],observed['revision']))
        for table,digest in before['tables'].items():
            if table not in ('djmdPlaylist','agentRegistry'):self.assertEqual(after['tables'][table],digest,table)
        expected=xml.touch(xml.append_node(raw,created['ID']),created['ID'],request['xml_timestamp'])
        self.assertEqual(self.xml.read_bytes(),expected)
        unchanged=self.database.read_bytes(),self.xml.read_bytes();self.writer.apply(request)
        self.assertEqual((self.database.read_bytes(),self.xml.read_bytes()),unchanged)

    def test_default_name_skips_name_window_and_preserves_zero_new_xml_timestamp(self):
        request=self.request(name='Untitled Playlist');self.writer.apply(request)
        _,rows=self.inspect();created=next(row for row in rows['djmdPlaylist'] if row['ID']==request['playlist_id'])
        counter=next(row['int_1'] for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        self.assertEqual((created['rb_local_usn'],counter),(41,42))
        self.assertEqual(xml._target(self.xml.read_bytes(),created['ID'])[0]['Timestamp'],'0')

    def test_contiguous_append_exactly_preserves_the_existing_constructor_name_plan(self):
        _state_before,rows=self.inspect();nodes=[row for row in rows['djmdPlaylist'] if row['ID']=='1000']
        counter=next(row for row in rows['agentRegistry'] if row['registry_id']=='localUpdateCount')
        stamp='2026-11-20 00:00:00.000 +00:00';uuid='00000000-0000-4000-8000-000000003000'
        first=plan.create(nodes,counter,identity='3000',uuid=uuid,name='Untitled Playlist',timestamp=stamp)
        named=plan.rename(first['playlist'],first['counter'],name='Named',timestamp=stamp)
        proposed=create.candidate(nodes,counter,identity='3000',uuid=uuid,name='Named',parent='root',timestamp=stamp,
            position_timestamp=stamp,name_timestamp=stamp,sibling_timestamps={})
        self.assertEqual(proposed['changes'],first['changes']+named['changes'])
        self.assertFalse(proposed['source_write_authority'])

    def test_failed_source_publication_recovers_before_then_retry_keeps_new_id_and_profile(self):
        before=self.inspect(),self.xml.read_bytes();request=self.request()
        def interrupted(stage):
            if stage=='xml-published':raise RuntimeError('Held normalized constructor')
        with mock.patch.object(store,'_fault',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'Held normalized'):
            self.writer.apply(request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual((self.inspect(),self.xml.read_bytes()),before)
        renewed=self.writer.prepare_retry(request)
        for key in ('playlist_id','playlist_uuid','parent','parent_uuid','normalized_create_profile'):
            self.assertEqual(renewed[key],request[key])
        self.writer.apply(renewed)

    def test_clock_renewal_keeps_all_sql_phases_coherent_and_preserves_chained_xml(self):
        request=self.request();request['xml_timestamp']+=100000
        stamp='2026-11-20 01:00:00.000 +00:00'
        with mock.patch.object(store,'_timestamps',return_value=(stamp,1)):
            renewed=store.renew_normalized_create_request(request,self.writer.snapshot())
        self.assertEqual(renewed['timestamp'],stamp);self.assertEqual(renewed['survivor_timestamp'],stamp)
        self.assertEqual(renewed['normalized_create_clocks']['position_timestamp'],stamp)
        self.assertGreaterEqual(renewed['xml_timestamp'],request['xml_timestamp'])
        store.validate_request(renewed)

    def test_unknown_folder_tracks_profile_and_position_clocks_fail_before_source_work(self):
        request=self.request()
        for change in ({'folder':True},{'normalized_create_profile':'unknown'}, {'parent_uuid':'wrong'}):
            with self.subTest(change=change),self.assertRaises(WriteProofError):store.validate_request({**request,**change})
        changed=deepcopy(request);changed['normalized_create_clocks']['sibling_timestamps']={}
        before=self.inspect(),self.xml.read_bytes()
        with self.assertRaises(ValueError):self.writer.apply(changed)
        self.assertEqual((self.inspect(),self.xml.read_bytes()),before)
        changed=deepcopy(request);changed['revision']='f'*64
        with self.assertRaises(ConflictError):self.writer.apply(changed)
