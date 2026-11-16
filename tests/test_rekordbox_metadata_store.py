"""Native metadata transactions use generated encrypted TEMP libraries only."""
from dataclasses import asdict,replace
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock

from soundbox import rekordbox_metadata_store as metadata,rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import _connect,_state,ConflictError,RecoveryRequired,WriteProofError
from tests.support.native_library import source_fixture

UUID='00000000-0000-4000-8000-000000000001'
CLOCK='2026-11-12 12:00:00.000 +00:00'


class NativeMetadataStore(unittest.TestCase):
    def setUp(self):
        self.area=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.area).resolve()
        self.source=self.root/'source';source_fixture(self.source);self.database=self.source/'master.db'
        conn=_connect(self.database)
        try:
            conn.executescript('''CREATE TABLE djmdArtist(ID TEXT PRIMARY KEY,Name TEXT,SearchStr TEXT,UUID TEXT,
                rb_data_status INTEGER,rb_local_data_status INTEGER,rb_local_deleted INTEGER,rb_local_synced INTEGER,
                usn INTEGER,rb_local_usn INTEGER,created_at TEXT,updated_at TEXT);
                CREATE TABLE djmdAlbum(ID TEXT PRIMARY KEY,AlbumArtistID TEXT);
            ''')
            conn.execute('UPDATE djmdContent SET UUID=?,Title=?,FolderPath=?,Analysed=105,TrackInfoUpdated=?,rb_local_usn=40,'
                'rb_data_status=0,rb_local_data_status=0,rb_local_deleted=0,rb_local_synced=0,usn=NULL,ArtistID=NULL,AlbumID=NULL WHERE ID=?',
                (UUID,'Before','/synthetic/song.wav','2','1'))
            self.schema=tags._schema(conn)
        finally:conn.close()
        for key,value in (('_closed',None),('_installed_version',tags.NATIVE_VERSION)):
            self.enterContext(mock.patch.object(cues,key,return_value=value))
        self.authority=metadata.MetadataAuthority(str(self.database),'123',self.schema,True,metadata.PROFILE)
        self.writer=metadata.SourceMetadataStore(self.authority,self.root/'data')

    def state(self):
        conn=_connect(self.database,readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def mutate(self,sql,args=()):
        conn=_connect(self.database)
        try:return conn.execute(sql,args).fetchall()
        finally:conn.close()

    def request(self,field='title',value='After',expected='Before'):
        return metadata.request_for('123','1',UUID,'/synthetic/song.wav',field,value,expected)

    def capture(self,value,expected):
        return self.writer.capture_request('1',UUID,'/synthetic/song.wav','artist',value,expected)

    def test_old_other_domain_or_default_off_authority_never_admits_source_connection(self):
        for authority in (replace(self.authority,accepted=False),replace(self.authority,profile='unknown')):
            writer=metadata.SourceMetadataStore(authority,self.root/'data')
            with mock.patch.object(writer.lease,'_connect') as connection:
                with self.assertRaises(WriteProofError):writer.apply(self.request())
                with self.assertRaises(WriteProofError):writer.recover()
                connection.assert_not_called()
        with self.assertRaises(WriteProofError):metadata.SourceMetadataStore(object(),self.root/'other-data')
        path=self.root/'authority.json';path.write_text(json.dumps(asdict(replace(self.authority,accepted=False))))
        self.assertIsNone(metadata.load_authority(path,self.database,'123'))
        path.write_text(json.dumps(asdict(self.authority)))
        self.assertEqual(metadata.load_authority(path,self.database,'123'),self.authority)
        self.assertIsNone(metadata.load_authority(path,self.database,'456'))

    def test_title_only_updates_measured_columns_and_counter_preserving_xml_other_rows_and_audio(self):
        before=self.state();xml=self.writer.xml.read_bytes();request=self.request()
        saved=self.writer.apply(request);after=self.state()
        self.assertEqual(saved['Title'],'After');self.assertEqual(saved['rb_local_usn'],42)
        self.assertEqual(saved['TrackInfoUpdated'],'3')
        self.assertEqual({key for key in before['tables'] if before['tables'][key]!=after['tables'][key]},
                         {'djmdContent','agentRegistry'})
        self.assertEqual(self.writer.xml.read_bytes(),xml)
        self.assertEqual(self.mutate('SELECT FolderPath FROM djmdContent WHERE ID=?',('1',)),[('/synthetic/song.wav',)])
        self.assertFalse(self.writer.journal.exists())
        baseline=self.writer.root/'baseline.db'
        self.assertNotEqual(baseline.read_bytes()[:16],b'SQLite format 3\0')
        self.assertEqual(len(list(self.writer.root.glob('*.db'))),1)

    def synced_writer(self):
        self.mutate('UPDATE djmdContent SET rb_local_synced=1 WHERE ID=?',('1',))
        return metadata.SourceMetadataStore(replace(self.authority,
            profile='ascii-active-analysed-empty-album-v2'),self.root/'data')

    def test_synced_profile_paired_artist_title_save_preserves_history_and_unrelated_orphans(self):
        writer=self.synced_writer()
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('999','Unrelated unused artist',None,'00000000-0000-4000-8000-000000000999',0,0,0,0,None,3,CLOCK,CLOCK))
        self.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('orphan-album',None))
        orphan=self.mutate('SELECT * FROM djmdArtist WHERE ID=?',('999',))
        before=self.state();xml=writer.xml.read_bytes()
        artist=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','Derived Artist','')
        saved=writer.apply(artist)
        self.assertEqual((saved['rb_local_synced'],saved['rb_local_usn'],saved['TrackInfoUpdated']),(1,43,'3'))
        self.assertEqual(saved['ArtistID'],artist['artist_seed']['ID'])
        title=self.request(value='Cleaned Title');saved=writer.apply(title)
        self.assertEqual((saved['Title'],saved['rb_local_synced'],saved['rb_local_usn'],saved['TrackInfoUpdated']),
                         ('Cleaned Title',1,45,'4'))
        self.assertEqual(self.mutate('SELECT Name FROM djmdArtist WHERE ID=?',(saved['ArtistID'],)),[('Derived Artist',)])
        self.assertEqual(self.mutate('SELECT * FROM djmdArtist WHERE ID=?',('999',)),orphan)
        self.assertEqual(self.mutate('SELECT * FROM djmdAlbum'),[('orphan-album',None)])
        after=self.state()
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=after['tables'][t]},
                         {'djmdContent','djmdArtist','agentRegistry'})
        self.assertEqual(writer.xml.read_bytes(),xml)
        writer.apply(artist);writer.apply(title);self.assertEqual(self.state(),after)

    def test_synced_profile_artist_failure_restart_and_replay_keep_exact_rows_and_counter(self):
        writer=self.synced_writer()
        for phase,outcome in (('before-commit','rolled-back'),('after-commit','committed')):
            with self.subTest(phase=phase):
                if phase=='after-commit':
                    writer.apply(writer.capture_request('1',UUID,'/synthetic/song.wav','artist','','Artist before-commit'))
                request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','Artist '+phase,'')
                before=self.state()
                def interrupt(current):
                    if current==phase:raise OSError('isolated synced metadata interruption')
                with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
                writer=metadata.SourceMetadataStore(writer.authority,self.root/'data')
                self.assertEqual(writer.recover(),outcome)
                if outcome=='rolled-back':self.assertEqual(self.state(),before)
                writer.apply(request);committed=self.state()
                writer=metadata.SourceMetadataStore(writer.authority,self.root/'data')
                writer.apply(request);self.assertEqual(self.state(),committed)
                self.assertFalse(writer.journal.exists())

    def test_synced_profile_journal_and_compacted_receipt_cannot_be_reinterpreted_as_old_profile(self):
        writer=self.synced_writer();request=self.request();before=self.state()
        def interrupt(phase):
            if phase=='journal-ready':raise OSError('stop before source SQL')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertEqual(self.state(),before);self.assertTrue(writer.journal.exists())
        self.assertEqual(writer.recover(),'rolled-back');writer.apply(request)
        entry=writer.retirement_entry(request);metadata.retire_acknowledged_receipt(self.root/'data',entry)
        marker=json.loads(writer._receipt_path(request['operation_id']).read_bytes())
        self.assertEqual(marker['profile'],writer.authority.profile)
        committed=self.state()
        with self.assertRaises(RecoveryRequired):self.writer.apply(request)
        writer.apply(request);self.assertEqual(self.state(),committed)

    def test_synced_profile_still_requires_independent_permission_and_refuses_unproved_states(self):
        writer=self.synced_writer()
        disabled=metadata.SourceMetadataStore(replace(writer.authority,accepted=False),self.root/'data')
        with mock.patch.object(disabled.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):disabled.apply(self.request())
            connection.assert_not_called()
        for column,value in (('rb_local_synced',2),('rb_data_status',257),('usn',7),('AlbumID','album'),
                             ('Analysed',0),('TrackInfoUpdated','1')):
            with self.subTest(column=column):
                self.mutate('UPDATE djmdContent SET '+column+'=? WHERE ID=?',(value,'1'));before=self.state()
                with self.assertRaises(WriteProofError):writer.apply(self.request())
                self.assertEqual(self.state(),before)
                self.mutate("UPDATE djmdContent SET rb_local_synced=1,rb_data_status=0,usn=NULL,AlbumID=NULL,Analysed=105,TrackInfoUpdated='2' WHERE ID='1'")
        before=self.state()
        with self.assertRaises(WriteProofError):writer.apply(self.request(value='Unicode 東京'))
        self.assertEqual(self.state(),before)

    def test_artist_capture_is_read_only_then_creation_replay_and_orphan_retirement_use_distinct_counters(self):
        before=self.state();request=self.capture('New Artist','')
        self.assertEqual(self.state(),before);self.assertIsNotNone(request['artist_seed'])
        saved=self.writer.apply(request);self.assertEqual(saved['rb_local_usn'],43)
        actor=saved['ArtistID'];self.assertEqual(actor,request['artist_seed']['ID'])
        before=self.state();self.assertEqual(self.writer.apply(request),saved);self.assertEqual(self.state(),before)
        clear=self.capture('','New Artist');self.assertIsNone(clear['artist_seed'])
        saved=self.writer.apply(clear);self.assertEqual(saved['rb_local_usn'],46)
        self.assertEqual(self.mutate('SELECT ID FROM djmdArtist WHERE ID=?',(actor,)),[])

    def test_every_content_role_and_album_artist_reference_prevents_unshared_artist_deletion(self):
        for table,column in (('djmdContent','OrgArtistID'),('djmdContent','RemixerID'),
                             ('djmdContent','ComposerID'),('djmdAlbum','AlbumArtistID')):
            with self.subTest(table=table,column=column):
                request=self.capture('Artist '+column,'');saved=self.writer.apply(request);actor=saved['ArtistID']
                if table=='djmdContent':self.mutate('UPDATE djmdContent SET '+column+'=? WHERE ID=?',(actor,'1'))
                else:self.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('album',actor))
                before_counter=self.mutate("SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'")[0][0]
                saved=self.writer.apply(self.capture('','Artist '+column))
                self.assertEqual(saved['rb_local_usn'],before_counter+2)
                self.assertEqual(self.mutate('SELECT Name FROM djmdArtist WHERE ID=?',(actor,)),[('Artist '+column,)])
                if table=='djmdContent':self.mutate('UPDATE djmdContent SET '+column+'=NULL WHERE ID=?',('1',))
                else:self.mutate('DELETE FROM djmdAlbum WHERE ID=?',('album',))

    def test_unproven_field_text_album_synced_or_combined_artist_creation_retirement_never_writes(self):
        for field,value in (('album','Album'),):
            with self.subTest(field=field,value=value),self.assertRaises(WriteProofError):self.request(field,value)
        for value in ('Unicode 東京',' bad'):
            before=self.state()
            with self.subTest(value=value),self.assertRaises(WriteProofError):self.writer.apply(self.request(value=value))
            self.assertEqual(self.state(),before)
        for column,value in (('AlbumID','album'),('rb_local_synced',1),('Analysed',0),('TrackInfoUpdated','1')):
            self.mutate('UPDATE djmdContent SET '+column+'=? WHERE ID=?',(value,'1'));before=self.state()
            with self.assertRaises(WriteProofError):self.writer.apply(self.request())
            self.assertEqual(self.state(),before)
            self.mutate('UPDATE djmdContent SET AlbumID=NULL,rb_local_synced=0,Analysed=105,TrackInfoUpdated=? WHERE ID=?',('2','1'))
        self.writer.apply(self.capture('Original Artist',''));before=self.state()
        with self.assertRaises(WriteProofError):self.capture('Different New Artist','Original Artist')
        self.assertEqual(self.state(),before)

    def test_changed_uuid_path_expected_value_and_pilot_scope_preserve_current_native_data(self):
        original=self.request()
        for changes in ({'track_uuid':'00000000-0000-4000-8000-000000000002'},
                        {'path':'/synthetic/different.wav'},{'expected':'Other'}):
            before=self.state()
            with self.subTest(changes=changes),self.assertRaises(ConflictError):self.writer.apply({**original,**changes})
            self.assertEqual(self.state(),before)
        writer=metadata.SourceMetadataStore(replace(self.authority,pilot_operation_ids=('a'*32,)),self.root/'data')
        with mock.patch.object(writer.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):writer.apply(original)
            connection.assert_not_called()

    def test_interruption_reconciles_before_and_after_commit_with_same_request_and_one_baseline(self):
        for phase,outcome in (('before-commit','rolled-back'),('after-commit','committed')):
            with self.subTest(phase=phase):
                before=self.state();expected=self.mutate('SELECT Title FROM djmdContent WHERE ID=?',('1',))[0][0]
                request=self.request(value=phase,expected=expected)
                def interrupt(current):
                    if current==phase:raise OSError('interrupted metadata save')
                with mock.patch.object(metadata,'_fault',side_effect=interrupt):
                    with self.assertRaises(OSError):self.writer.apply(request)
                self.assertEqual(self.writer.recover(),outcome)
                if outcome=='rolled-back':self.assertEqual(self.state(),before)
                saved=self.writer.apply(request);self.assertEqual(saved['Title'],phase)
                state=self.state();self.writer.apply(request);self.assertEqual(self.state(),state)
                self.assertEqual(len(list(self.writer.root.glob('*.db'))),1)

    def test_coherent_journal_tampering_is_rejected_against_independent_encrypted_before(self):
        def interrupt(phase):
            if phase=='journal-ready':raise OSError('stop before source SQL')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt):
            with self.assertRaises(OSError):self.writer.apply(self.request())
        record=json.loads(self.writer.journal.read_bytes());record['before']['target']['Title']='Fabricated'
        record['request']['expected']='Fabricated'
        record['changes'],record['after']=metadata._plan(record['before'],record['request'])
        self.writer.journal.write_text(json.dumps(record));before=self.state()
        with mock.patch.object(self.writer.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):self.writer.recover()
            connection.assert_not_called()
        self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())

    def test_permanent_receipt_survives_new_baseline_but_not_replaced_artist_identity(self):
        first=self.capture('Artist','');saved=self.writer.apply(first)
        second=self.request(value='New title');self.writer.apply(second)
        self.assertEqual(self.writer.apply(first)['ArtistID'],saved['ArtistID'])
        self.mutate('UPDATE djmdArtist SET UUID=? WHERE ID=?',('replacement',saved['ArtistID']))
        before=self.state()
        with self.assertRaises(RecoveryRequired):self.writer.apply(first)
        self.assertEqual(self.state(),before)

    def test_a_thousand_unrelated_artists_never_enter_title_or_artist_operation_receipts(self):
        conn=_connect(self.database)
        try:
            conn.executemany('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,1,?,?)',
                [(str(10000+i),'Unrelated '+str(i),'00000000-0000-4000-8000-'+f'{10000+i:012d}',CLOCK,CLOCK)
                 for i in range(1000)])
        finally:conn.close()
        title=self.request();self.writer.apply(title)
        artist=self.capture('Selected Artist','');self.writer.apply(artist)
        for request in (title,artist):
            path=self.writer._receipt_path(request['operation_id']);record=json.loads(path.read_bytes())
            self.assertLess(path.stat().st_size,32768)
            self.assertNotIn('Unrelated ',path.read_text())
            if request['field']=='title':
                self.assertEqual(record['before']['artists'],[]);self.assertEqual(record['after']['artists'],[])
                self.assertEqual(record['before']['references'],{});self.assertEqual(record['after']['references'],{})
            else:
                self.assertEqual(record['before']['artists'],[])
                self.assertEqual([row['ID'] for row in record['after']['artists']],[artist['artist_seed']['ID']])

    def test_title_authorizer_cannot_modify_artist_reference_columns_or_artist_rows(self):
        import sqlite3
        for table,column,action in (('djmdContent','ArtistID',sqlite3.SQLITE_UPDATE),
                                    ('djmdContent','AlbumID',sqlite3.SQLITE_UPDATE),
                                    ('djmdArtist','Name',sqlite3.SQLITE_INSERT),('djmdArtist','Name',sqlite3.SQLITE_DELETE)):
            with self.subTest(table=table,column=column):
                self.assertEqual(metadata._authorizer(action,table,column,'main',None,field='title'),sqlite3.SQLITE_DENY)

    def test_already_exact_unicode_metadata_requires_no_write_profile_extrapolation_or_normalization(self):
        self.mutate('UPDATE djmdContent SET Title=?,rb_local_synced=1,Analysed=0 WHERE ID=?',('Native 東京','1'))
        before=self.state();raw=self.database.read_bytes()
        request=self.writer.capture_request('1',UUID,'/synthetic/song.wav','title','Native 東京')
        self.writer.apply(request)
        self.assertEqual(self.state(),before);self.assertEqual(self.database.read_bytes(),raw)
        self.assertFalse((self.writer.root/'baseline.db').exists())

    def test_deleted_targets_never_capture_or_acknowledge_an_already_equal_noop(self):
        request=self.request(value='Before')
        self.mutate('UPDATE djmdContent SET rb_local_deleted=1 WHERE ID=?',('1',));before=self.state()
        with self.assertRaises(ConflictError):self.writer.apply(request)
        with self.assertRaises(ConflictError):self.writer.capture_request('1',UUID,'/synthetic/song.wav','title','Before')
        self.assertEqual(self.state(),before);self.assertFalse(self.writer.journal.exists())

    def test_committed_artist_replay_refuses_inactive_or_duplicate_uuid_actor_without_writes(self):
        request=self.capture('Artist','');saved=self.writer.apply(request);actor=saved['ArtistID']
        self.mutate('UPDATE djmdArtist SET rb_local_deleted=1 WHERE ID=?',(actor,));before=self.state()
        with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertEqual(self.state(),before)

        self.mutate('UPDATE djmdArtist SET rb_local_deleted=0 WHERE ID=?',(actor,))
        uuid=self.mutate('SELECT UUID FROM djmdArtist WHERE ID=?',(actor,))[0][0]
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,1,?,?)',
                    ('999','Other Actor',uuid,CLOCK,CLOCK));before=self.state()
        with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertEqual(self.state(),before)

    def test_acknowledged_receipts_compact_without_source_access_and_old_operation_replay_never_writes(self):
        for field,value,expected in (('title','After','Before'),('artist','Artist','')):
            with self.subTest(field=field):
                request=self.request(field,value,expected) if field=='title' else self.capture(value,expected)
                self.writer.apply(request);entry=self.writer.retirement_entry(request)
                path=self.writer._receipt_path(request['operation_id']);proof_size=path.stat().st_size
                before=self.state();source_bytes=self.database.read_bytes()
                # A restart after durable ACK retains the full proof until this
                # pure local task succeeds. It must not connect or copy the DB.
                with mock.patch.object(metadata,'_connect',side_effect=AssertionError('No source access')):
                    metadata.retire_acknowledged_receipt(self.root/'data',entry)
                    metadata.retire_acknowledged_receipt(self.root/'data',entry)
                marker=json.loads(path.read_bytes());self.assertEqual(marker['format'],metadata.ACK_FORMAT)
                self.assertLess(path.stat().st_size,2048);self.assertLess(path.stat().st_size,proof_size)
                with mock.patch.object(metadata,'_apply',side_effect=AssertionError('No replay SQL')):
                    self.writer.apply(request);self.assertEqual(self.writer.retry_request(request),request)
                self.assertEqual(self.state(),before);self.assertEqual(self.database.read_bytes(),source_bytes)

    def test_retirement_preserves_tampered_full_proof_or_marker_and_refuses_marker_request_tampering(self):
        request=self.request();self.writer.apply(request);entry=self.writer.retirement_entry(request)
        path=self.writer._receipt_path(request['operation_id']);original=path.read_bytes()
        proof=json.loads(original);proof['after']['target']['Title']='Tampered';path.write_text(json.dumps(proof))
        before=path.read_bytes()
        with self.assertRaises(RecoveryRequired):metadata.retire_acknowledged_receipt(self.root/'data',entry)
        self.assertEqual(path.read_bytes(),before)
        path.write_bytes(original);metadata.retire_acknowledged_receipt(self.root/'data',entry)
        marker=json.loads(path.read_bytes());marker['value']='Tampered';path.write_text(json.dumps(marker));before=self.state()
        with self.assertRaises(RecoveryRequired):metadata.retire_acknowledged_receipt(self.root/'data',entry)
        with mock.patch.object(metadata,'_apply',side_effect=AssertionError('No replay SQL')):
            with self.assertRaises(RecoveryRequired):self.writer.apply(request)
            with self.assertRaises(RecoveryRequired):self.writer.retry_request(request)
        self.assertEqual(self.state(),before)
    def test_future_orphan_artist_clock_and_a_reused_actor_uuid_are_never_adopted(self):
        captured=self.capture('Artist','');saved=self.writer.apply(captured)
        self.mutate('UPDATE djmdArtist SET rb_local_usn=999 WHERE ID=?',(saved['ArtistID'],));before=self.state()
        with self.assertRaises(WriteProofError):self.capture('','Artist')
        self.assertEqual(self.state(),before)
        self.mutate('UPDATE djmdArtist SET rb_local_usn=41 WHERE ID=?',(saved['ArtistID'],))
        clear=self.capture('','Artist')
        self.mutate('UPDATE djmdArtist SET UUID=? WHERE ID=?',('replacement',saved['ArtistID']))
        before=self.state()
        with self.assertRaises(ConflictError):self.writer.apply(clear)
        self.assertEqual(self.state(),before)

    def test_new_actor_seed_never_adopts_a_dangling_existing_reference(self):
        seed=dict(ID='999',UUID='00000000-0000-4000-8000-000000000999',timestamp=CLOCK)
        self.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('album','999'));before=self.state()
        with mock.patch.object(metadata,'new_artist_seed',return_value=seed):
            with self.assertRaisesRegex(ConflictError,'already has native references'):self.capture('New Artist','')
        self.assertEqual(self.state(),before)

    def test_public_artist_requests_require_seed_pins_and_reject_dangling_refs_and_uuid_collision(self):
        seed=dict(ID='999',UUID='00000000-0000-4000-8000-000000000999',timestamp=CLOCK)
        kwargs=dict(artist_seed=seed,now=datetime(2026,11,12,12,tzinfo=timezone.utc))
        with self.assertRaisesRegex(WriteProofError,'exact identity pin'):
            metadata.request_for('123','1',UUID,'/synthetic/song.wav','artist','New Artist','',**kwargs)
        request=metadata.request_for('123','1',UUID,'/synthetic/song.wav','artist','New Artist','',
                                    artist_pins={seed['ID']:seed['UUID']},**kwargs)
        self.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('album',seed['ID']));before=self.state()
        with self.assertRaisesRegex(ConflictError,'already has native references'):self.writer.apply(request)
        self.assertEqual(self.state(),before)
        self.mutate('DELETE FROM djmdAlbum')
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,NULL,?,0,0,0,0,NULL,1,?,?)',
                    ('998','Unrelated',seed['UUID'],CLOCK,CLOCK));before=self.state()
        with self.assertRaises(ConflictError):self.writer.apply(request)
        self.assertEqual(self.state(),before)

    def test_real_process_loss_before_and_after_commit_recovers_encrypted_sqlite_without_redetection(self):
        script='''import json,os,sys
from pathlib import Path
from soundbox import rekordbox_metadata_store as m,rekordbox_cue_store as c,rekordbox_tags as t
c._closed=lambda:None;c._installed_version=lambda:t.NATIVE_VERSION
body=json.loads(sys.argv[1]);body['pilot_operation_ids']=tuple(body['pilot_operation_ids'])
w=m.SourceMetadataStore(m.MetadataAuthority(**body),Path(sys.argv[2]))
original=w.lease._connect
def connect():
 conn=original();conn.execute('PRAGMA cache_size=1');return conn
w.lease._connect=connect
m._fault=lambda phase:os._exit(73) if phase==sys.argv[4] else None
w.apply(json.loads(sys.argv[3]))
'''
        for phase,outcome in (('before-commit','rolled-back'),('after-commit','committed')):
            with self.subTest(phase=phase):
                expected=self.mutate('SELECT Title FROM djmdContent WHERE ID=?',('1',))[0][0]
                request=self.request(value=phase,expected=expected);before=self.state()
                result=subprocess.run([sys.executable,'-c',script,json.dumps(asdict(self.authority)),str(self.root/'data'),
                    json.dumps(request),phase],capture_output=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr.decode())
                self.assertEqual(self.writer.recover(),outcome)
                if outcome=='rolled-back':self.assertEqual(self.state(),before)
                self.writer.apply(request);after=self.state();self.writer.apply(request)
                self.assertEqual(self.state(),after)

    def test_shared_lock_other_domain_evidence_and_sql_authorizer_never_bypass_domain_limits(self):
        self.writer.journal.write_text('{"format":"another-domain"}')
        with mock.patch.object(self.writer.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):self.writer.apply(self.request())
            connection.assert_not_called()
        self.writer.journal.unlink()
        with self.writer.lease._lock():
            with self.assertRaises(WriteProofError):metadata.SourceMetadataStore(self.authority,self.root/'data').apply(self.request())
        from sqlcipher3 import dbapi2 as sqlcipher
        conn=_connect(self.database)
        try:
            conn.set_authorizer(metadata._authorizer)
            for sql in ("UPDATE djmdContent SET BPM=999 WHERE ID='1'","DELETE FROM djmdContent WHERE ID='1'",
                        "UPDATE djmdArtist SET Name='Changed'","DELETE FROM unknown_table"):
                with self.subTest(sql=sql),self.assertRaises(sqlcipher.DatabaseError):conn.execute(sql)
        finally:conn.close()


if __name__=='__main__':unittest.main()
