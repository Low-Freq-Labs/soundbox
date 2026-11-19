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

    def unicode_writer(self):
        return metadata.SourceMetadataStore(replace(self.authority,
            profile=metadata.UNICODE_PROFILE),self.root/'data')

    def retirement_case(self,*,synced,create,local_clock=12):
        prior=('600','Previous artist','opaque search','00000000-0000-4000-8000-000000000600',
               0,0,0,synced,None,local_clock,CLOCK,CLOCK)
        keep=('900','Unrelated unused artist',None,'00000000-0000-4000-8000-000000000900',
              0,0,0,1,None,11,CLOCK,CLOCK)
        for actor in (prior,keep):self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',actor)
        if not create:
            self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('601','東京 destination','opaque destination','00000000-0000-4000-8000-000000000601',0,0,0,1,None,10,CLOCK,CLOCK))
        self.mutate("UPDATE djmdContent SET ArtistID='600' WHERE ID='1'")
        writer=metadata.SourceMetadataStore(replace(self.authority,profile=metadata.RETIREMENT_PROFILE),self.root/'data')
        return writer,prior,keep

    def assert_retirement_save(self,*,synced,create):
        writer,prior,keep=self.retirement_case(synced=synced,create=create)
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        self.assertEqual('retirement_clock' in request,bool(synced));self.assertEqual(bool(request['artist_seed']),create)
        before=self.state();xml=writer.xml.read_bytes();saved=writer.apply(request)
        self.assertEqual((saved['TrackInfoUpdated'],saved['rb_local_usn']),('3',43+int(create)))
        self.assertEqual(self.mutate("SELECT * FROM djmdArtist WHERE ID='900'"),[keep])
        old=self.mutate("SELECT * FROM djmdArtist WHERE ID='600'")
        if synced:
            expected=list(prior);expected[4]=2;expected[6]=1;expected[9]=42+int(create);expected[11]=request['retirement_clock']
            self.assertEqual(old,[tuple(expected)])
        else:self.assertEqual(old,[])
        if create:
            self.assertEqual(self.mutate('SELECT UUID,Name,rb_local_usn FROM djmdArtist WHERE ID=?',(saved['ArtistID'],)),
                [(request['artist_seed']['UUID'],'東京 destination',41)])
        else:self.assertEqual(self.mutate("SELECT Name,SearchStr,rb_local_synced,rb_local_usn,updated_at FROM djmdArtist WHERE ID='601'"),
                [('東京 destination','opaque destination',1,10,CLOCK)])
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdContent','djmdArtist','agentRegistry'})
        self.assertEqual(writer.xml.read_bytes(),xml)
        committed=self.state();writer.apply(request);self.assertEqual(self.state(),committed)

    def test_retirement_plan_matches_independent_native_component_and_main_sql_reference(self):
        import hashlib
        from soundbox import native_metadata_plan as planner
        path=Path(__file__).parent/'fixtures/native-metadata-retirement-v2.json';raw=path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'fb6eaaabe816c9addeef5b77d43397f0185f99082d00829c872178feb0fd5e54')
        for case in json.loads(raw)['cases']:
            with self.subTest(synced=case['synced'],create=case['create']):
                actors=[case['artistBefore']]+([] if case['create'] else [case['destination']])
                seed=({k:case['destination'][k] for k in ('ID','UUID')}|{'timestamp':case['destination']['created_at']}) if case['create'] else None
                options=dict(field='artist',value=case['destination']['Name'],timestamp=case['targetAfter']['updated_at'],
                             artist_seed=seed,retirement_timestamp=case['cleanupClock'] if case['synced'] else None)
                result=planner.construct(case['targetBefore'],case['counterBefore'],actors,{case['artistBefore']['ID']:1},
                                         profile=metadata.RETIREMENT_PROFILE,**options)
                self.assertEqual(result['after'],case['targetAfter']);self.assertEqual(result['counter_after'],case['counterAfter'])
                self.assertEqual(result['artist_created'],case['destination'] if case['create'] else None)
                self.assertEqual(result['artist_removed'],case['artistBefore'])
                self.assertEqual(result.get('artist_retirement_after'),case['artistAfter'])
                if case['synced'] or case['create']:
                    with self.assertRaises(WriteProofError):planner.construct(case['targetBefore'],case['counterBefore'],actors,
                        {case['artistBefore']['ID']:1},profile=metadata.UNICODE_PROFILE,**options)
                else:
                    self.assertEqual(planner.construct(case['targetBefore'],case['counterBefore'],actors,
                        {case['artistBefore']['ID']:1},profile=metadata.UNICODE_PROFILE,**options),result)

    def test_agent_twelve_hour_reference_cannot_supply_the_main_cleanup_clock(self):
        import hashlib
        from soundbox import native_metadata_plan as planner
        raw=(Path(__file__).parent/'fixtures/native-metadata-retirement.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'a5a7d511396b2306145315fc0078d4dcb4c1db7f28259c72e82defc3316371b9')
        case=next(x for x in json.loads(raw)['cases'] if x['synced'] and x['create'])
        seed={k:case['destination'][k] for k in ('ID','UUID')}|{'timestamp':case['destination']['created_at']}
        self.assertLess(case['cleanupClock'],seed['timestamp'])
        with self.assertRaisesRegex(WriteProofError,'retirement precedes artist creation'):
            planner.construct(case['targetBefore'],case['counterBefore'],[case['artistBefore']],{case['artistBefore']['ID']:1},
                field='artist',value=case['destination']['Name'],timestamp=case['targetAfter']['updated_at'],artist_seed=seed,
                retirement_timestamp=case['cleanupClock'],profile=metadata.RETIREMENT_PROFILE)

    def album_writer(self,*,synced=1,create=True,role=None):
        _,prior,keep=self.retirement_case(synced=synced,create=create)
        self.mutate('DROP TABLE djmdAlbum')
        self.mutate('''CREATE TABLE djmdAlbum(ID TEXT PRIMARY KEY,Name TEXT,AlbumArtistID TEXT,Compilation INTEGER,
            SearchStr TEXT,ImagePath TEXT,UUID TEXT,rb_data_status INTEGER,rb_local_data_status INTEGER,
            rb_local_deleted INTEGER,rb_local_synced INTEGER,usn INTEGER,rb_local_usn INTEGER,created_at TEXT,updated_at TEXT)''')
        self.mutate('INSERT INTO djmdAlbum VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('700','Album name with space ',role,0,'opaque album search','opaque image',
             '00000000-0000-4000-8000-000000000700',0,0,0,synced,None,14,CLOCK,CLOCK))
        self.mutate("UPDATE djmdContent SET AlbumID='700' WHERE ID='1'")
        schema=self.mutate("SELECT name FROM sqlite_master WHERE name='djmdAlbum'")
        self.assertEqual(schema,[('djmdAlbum',)])
        conn=_connect(self.database)
        try:authority=replace(self.authority,schema_sha256=tags._schema(conn),profile=metadata.ALBUM_PROFILE)
        finally:conn.close()
        return metadata.SourceMetadataStore(authority,self.root/'data'),prior,keep

    def test_album_plan_matches_all_twenty_four_independent_native_sequences(self):
        import hashlib
        from soundbox import native_metadata_plan as planner
        raw=(Path(__file__).parent/'fixtures/native-metadata-album-cloud.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'22bab939400ad4ef0ebcf8550ee6028795577cdd8b8f327e391a7ddedd4cc97e')
        cases=json.loads(raw)['album_cases'];self.assertEqual(len(cases),24)
        for case in cases:
            with self.subTest(synced=case['synced'],role=case['role'],create=case['create'],compilation=case['compilation']):
                actors={case['artistBefore']['ID']:case['artistBefore']}
                if not case['create']:actors[case['destination']['ID']]=case['destination']
                if case['albumRoleArtist']:actors[case['albumRoleArtist']['ID']]=case['albumRoleArtist']
                refs={case['artistBefore']['ID']:1}
                role=case['albumBefore']['AlbumArtistID']
                if role:refs[role]=refs.get(role,0)+1
                seed=({k:case['destination'][k] for k in ('ID','UUID')}|
                      {'timestamp':case['destination']['created_at']}) if case['create'] else None
                options=dict(field='artist',value=case['destination']['Name'],timestamp=case['targetAfter']['updated_at'],
                    artist_seed=seed,retirement_timestamp=case['cleanupClock'] if case['synced'] and role!=case['artistBefore']['ID'] else None,
                    album=case['albumBefore'],album_timestamp=case['albumAfter']['updated_at'])
                result=planner.construct(case['targetBefore'],case['counterBefore'],list(actors.values()),refs,
                    profile=metadata.ALBUM_PROFILE,**options)
                self.assertEqual(result['after'],case['targetAfter']);self.assertEqual(result['counter_after'],case['counterAfter'])
                self.assertEqual(result['album_after'],case['albumAfter'])
                self.assertEqual(result['artist_created'],case['destination'] if case['create'] else None)
                self.assertEqual(result.get('artist_retirement_after'),case['artistAfter'] if case['artistAfter']!=case['artistBefore'] else None)
                self.assertEqual(result['artist_removed'],case['artistBefore'] if role!=case['artistBefore']['ID'] else None)
                with self.assertRaises(WriteProofError):planner.construct(case['targetBefore'],case['counterBefore'],
                    list(actors.values()),refs,profile=metadata.RETIREMENT_PROFILE,**options)

    def test_cloud_title_plan_matches_native_steps_and_exact_large_remote_integer(self):
        from soundbox import native_metadata_plan as planner
        fixture=json.loads((Path(__file__).parent/'fixtures/native-metadata-album-cloud.json').read_bytes())
        stages=fixture['cloud_stages'];self.assertEqual(len(stages),16)
        for start in range(0,len(stages),4):
            field,revision=stages[start:start+2]
            before=dict(field['before'],usn=int(field['exactRemoteUSNBefore']))
            after=dict(revision['after'],usn=int(revision['exactRemoteUSNAfter']))
            self.assertEqual(field['exactRemoteUSNBefore'],revision['exactRemoteUSNAfter'])
            result=planner.construct(before,field['counterBefore'],[],{},field='title',value=after['Title'],
                timestamp=after['updated_at'],profile=metadata.ALBUM_PROFILE)
            self.assertEqual(result['after'],after);self.assertEqual(result['counter_after'],revision['counterAfter'])
            for profile in (metadata.PROFILE,metadata.SYNCED_PROFILE,metadata.UNICODE_PROFILE,metadata.RETIREMENT_PROFILE):
                with self.assertRaises(WriteProofError):planner.construct(before,field['counterBefore'],[],{},
                    field='title',value=after['Title'],timestamp=after['updated_at'],profile=profile)

    def test_album_save_preserves_opaque_rows_and_shared_album_artist(self):
        writer,prior,keep=self.album_writer(create=False,role='600')
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        self.assertIn('album_context',request);self.assertNotIn('retirement_clock',request)
        before=self.state();xml=writer.xml.read_bytes();saved=writer.apply(request)
        self.assertEqual((saved['AlbumID'],saved['rb_local_usn'],saved['TrackInfoUpdated']),('700',43,'3'))
        self.assertEqual(self.mutate("SELECT * FROM djmdArtist WHERE ID='600'"),[prior])
        self.assertEqual(self.mutate("SELECT * FROM djmdArtist WHERE ID='900'"),[keep])
        self.assertEqual(self.mutate("SELECT Name,AlbumArtistID,Compilation,SearchStr,ImagePath,rb_local_usn,updated_at FROM djmdAlbum WHERE ID='700'"),
            [('Album name with space ','600',0,'opaque album search','opaque image',42,request['album_context']['timestamp'])])
        after=self.state();self.assertEqual({k for k in before['tables'] if before['tables'][k]!=after['tables'][k]},
            {'djmdAlbum','djmdContent','agentRegistry'})
        self.assertEqual(writer.xml.read_bytes(),xml);writer.apply(request);self.assertEqual(self.state(),after)

    def test_album_combined_creation_retirement_recovers_and_compacts_with_separate_clocks(self):
        writer,prior,_=self.album_writer()
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        self.assertLessEqual(request['artist_seed']['timestamp'],request['album_context']['timestamp'])
        self.assertLessEqual(request['album_context']['timestamp'],request['retirement_clock'])
        self.assertLessEqual(request['retirement_clock'],request['timestamp'])
        def interrupt(phase):
            if phase=='after-commit':raise OSError('lost album save delivery')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        committed=self.state();writer=metadata.SourceMetadataStore(writer.authority,self.root/'data')
        self.assertEqual(writer.recover(),'committed');saved=writer.apply(request)
        self.assertEqual(saved['rb_local_usn'],45)
        self.assertEqual(self.mutate("SELECT AlbumArtistID,rb_local_usn,updated_at FROM djmdAlbum WHERE ID='700'"),
            [('',43,request['album_context']['timestamp'])])
        self.assertEqual(self.mutate("SELECT UUID,rb_local_deleted,rb_data_status,rb_local_usn,updated_at FROM djmdArtist WHERE ID='600'"),
            [(prior[3],1,2,44,request['retirement_clock'])])
        metadata.retire_acknowledged_receipt(self.root/'data',writer.retirement_entry(request))
        with mock.patch.object(metadata,'_apply',side_effect=AssertionError('No replay SQL')):writer.apply(request)
        self.assertEqual(self.state(),committed)
        old=metadata.SourceMetadataStore(replace(writer.authority,profile=metadata.RETIREMENT_PROFILE),self.root/'data')
        with mock.patch.object(old.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):old.apply(request)
            connection.assert_not_called()

    def test_album_stale_identity_ambiguous_lookup_and_clock_consumption_refuse_without_writes(self):
        writer,_,_=self.album_writer(create=False)
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        original=self.mutate("SELECT UUID FROM djmdAlbum WHERE ID='700'")[0][0]
        self.mutate("UPDATE djmdAlbum SET UUID='replaced' WHERE ID='700'");before=self.state()
        with self.assertRaises(ConflictError):writer.apply(request)
        self.assertEqual(self.state(),before)
        self.mutate("UPDATE djmdAlbum SET UUID=? WHERE ID='700'",(original,))
        self.mutate("INSERT INTO djmdAlbum SELECT '701',Name,AlbumArtistID,Compilation,SearchStr,ImagePath,'duplicate-name',rb_data_status,rb_local_data_status,rb_local_deleted,rb_local_synced,usn,rb_local_usn,created_at,updated_at FROM djmdAlbum WHERE ID='700'")
        before=self.state()
        with self.assertRaises(ConflictError):writer.apply(request)
        self.assertEqual(self.state(),before);self.mutate("DELETE FROM djmdAlbum WHERE ID='701'")
        missing=dict(request);missing.pop('album_context');before=self.state()
        with self.assertRaises(WriteProofError):writer.apply(missing)
        self.assertEqual(self.state(),before)
        invalid=dict(request,album_context={**request['album_context'],'timestamp':'2099-01-01 00:00:00.000 +00:00'})
        with self.assertRaises(WriteProofError):metadata.validate_request(invalid)
        def interrupt(phase):
            if phase=='before-commit':raise OSError('rollback all album and actor effects')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        self.assertEqual(self.state(),before);self.assertEqual(writer.recover(),'rolled-back')

    def test_unique_active_artist_lookup_preserves_same_name_deleted_history(self):
        actor=('600','CROMATIK','opaque active','00000000-0000-4000-8000-000000000600',0,0,0,1,None,12,CLOCK,CLOCK)
        history=('601','CROMATIK','opaque history','00000000-0000-4000-8000-000000000601',2,0,1,1,None,13,CLOCK,CLOCK)
        for row in (actor,history):self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',row)
        writer=metadata.SourceMetadataStore(replace(self.authority,profile=metadata.ALBUM_PROFILE),self.root/'data')
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','CROMATIK','')
        self.assertIsNone(request['artist_seed']);before=self.state()
        with self.assertRaises(WriteProofError):self.unicode_writer().apply(request)
        self.assertEqual(self.state(),before);saved=writer.apply(request)
        self.assertEqual(saved['ArtistID'],'600')
        self.assertEqual(self.mutate('SELECT * FROM djmdArtist ORDER BY ID'),[actor,history])
        self.mutate("UPDATE djmdContent SET ArtistID=NULL WHERE ID='1'")
        self.mutate("UPDATE djmdArtist SET rb_local_deleted=0,rb_data_status=0 WHERE ID='601'")
        before=self.state()
        with self.assertRaises(ConflictError):writer.capture_request('1',UUID,'/synthetic/song.wav','artist','CROMATIK','')
        self.assertEqual(self.state(),before)

    def cloud_writer(self):
        # The old generic registration fixture gives NULL usn TEXT affinity.
        # This new profile requires native BIGINT storage, including values above
        # JavaScript's exact-number range; do not relax the integer guard for it.
        conn=_connect(self.database)
        try:
            sql=conn.execute("SELECT sql FROM sqlite_master WHERE name='djmdContent'").fetchone()[0]
            self.assertEqual(sql.count('usn TEXT'),1)
            conn.execute('ALTER TABLE djmdContent RENAME TO temporary_content')
            conn.execute(sql.replace('usn TEXT','usn BIGINT'))
            conn.execute('INSERT INTO djmdContent SELECT * FROM temporary_content')
            conn.execute('DROP TABLE temporary_content')
            authority=replace(self.authority,schema_sha256=tags._schema(conn),profile=metadata.ALBUM_PROFILE)
        finally:conn.close()
        return metadata.SourceMetadataStore(authority,self.root/'data')

    def test_cloud_pair_preserves_exact_remote_usn_and_reused_actor_all_columns(self):
        writer=self.cloud_writer()
        remote=123456789012345678
        actor=('600','Cloud artist','opaque remote','00000000-0000-4000-8000-000000000600',257,2,0,1,remote,12,CLOCK,CLOCK)
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',actor)
        self.mutate("UPDATE djmdContent SET rb_data_status=257,rb_local_data_status=2,rb_local_synced=1,usn=? WHERE ID='1'",(remote,))
        title=writer.capture_request('1',UUID,'/synthetic/song.wav','title','Cloud 東京','Before')
        artist=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','Cloud artist','')
        before=self.state();writer.apply(title);saved=writer.apply(artist)
        self.assertEqual((saved['usn'],saved['rb_data_status'],saved['rb_local_data_status'],saved['rb_local_synced'],
                          saved['rb_local_usn'],saved['TrackInfoUpdated']),(remote,257,2,1,44,'4'))
        self.assertEqual(self.mutate('SELECT * FROM djmdArtist'),[actor])
        after=self.state();self.assertEqual({k for k in before['tables'] if before['tables'][k]!=after['tables'][k]},
            {'djmdContent','agentRegistry'})
        for request in (title,artist):
            metadata.retire_acknowledged_receipt(self.root/'data',writer.retirement_entry(request));writer.apply(request)
        self.assertEqual(self.state(),after)

    def test_cloud_incoherent_sync_states_and_future_local_clocks_remain_guarded(self):
        writer=self.cloud_writer()
        for changes in ({'rb_local_synced':0},{'usn':None},{'usn':-1},{'rb_local_data_status':1},
                        {'rb_data_status':258},{'rb_local_usn':999},{'Analysed':0},{'AlbumID':'700'}):
            self.mutate("UPDATE djmdContent SET rb_data_status=257,rb_local_data_status=0,rb_local_synced=1,usn=569,rb_local_usn=40,Analysed=105,AlbumID=NULL WHERE ID='1'")
            for key,value in changes.items():self.mutate('UPDATE djmdContent SET '+key+"=? WHERE ID='1'",(value,))
            before=self.state()
            with self.subTest(changes=changes),self.assertRaises(WriteProofError):writer.apply(self.request())
            self.assertEqual(self.state(),before)

    def test_album_authorizer_never_changes_names_identity_history_or_title_save_dependencies(self):
        import sqlite3
        for profile in (metadata.PROFILE,metadata.SYNCED_PROFILE,metadata.UNICODE_PROFILE,metadata.RETIREMENT_PROFILE,metadata.ALBUM_PROFILE):
            for field in ('title','artist'):
                for column in ('Name','UUID','SearchStr','ImagePath','usn','rb_local_synced','rb_local_deleted','Compilation','AlbumArtistID','rb_local_usn','updated_at'):
                    allowed=(profile==metadata.ALBUM_PROFILE and field=='artist'
                             and column in ('Compilation','AlbumArtistID','rb_local_usn','updated_at'))
                    self.assertEqual(metadata._authorizer(sqlite3.SQLITE_UPDATE,'djmdAlbum',column,'main',None,field=field,profile=profile),
                                     sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY)
        for action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_DELETE):
            self.assertEqual(metadata._authorizer(action,'djmdAlbum','Name','main',None,field='artist',profile=metadata.ALBUM_PROFILE),sqlite3.SQLITE_DENY)

    def test_retirement_profile_existing_artist_local_cleanup(self):
        self.assert_retirement_save(synced=0,create=False)

    def test_retirement_profile_existing_artist_synced_history(self):
        self.assert_retirement_save(synced=1,create=False)

    def test_retirement_profile_combined_new_artist_and_local_cleanup(self):
        self.assert_retirement_save(synced=0,create=True)

    def test_retirement_profile_combined_new_artist_and_synced_history(self):
        self.assert_retirement_save(synced=1,create=True)

    def test_retirement_profile_null_old_clock_failed_delivery_recovery_and_compact_ack(self):
        writer,prior,_=self.retirement_case(synced=1,create=True,local_clock=None)
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        def interrupt(phase):
            if phase=='after-commit':raise OSError('lost retirement delivery')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        committed=self.state()
        with self.assertRaises(RecoveryRequired):self.unicode_writer().recover()
        self.assertEqual(self.state(),committed)
        writer=metadata.SourceMetadataStore(writer.authority,self.root/'data');self.assertEqual(writer.recover(),'committed')
        writer.apply(request);self.assertEqual(self.state(),committed)
        metadata.retire_acknowledged_receipt(self.root/'data',writer.retirement_entry(request))
        marker=json.loads(writer._receipt_path(request['operation_id']).read_bytes());self.assertEqual(marker['profile'],metadata.RETIREMENT_PROFILE)
        with self.assertRaises(WriteProofError):self.unicode_writer().apply(request)
        writer.apply(request);self.assertEqual(self.state(),committed)
        self.assertEqual(self.mutate("SELECT SearchStr,UUID,rb_local_deleted,rb_local_usn FROM djmdArtist WHERE ID='600'"),
                         [(prior[2],prior[3],1,43)])

    def test_retirement_profile_preserves_artist_still_referenced_by_another_role_or_album(self):
        writer,prior,_=self.retirement_case(synced=1,create=False)
        self.mutate("UPDATE djmdContent SET ComposerID='600' WHERE ID='1'")
        self.mutate("INSERT INTO djmdAlbum VALUES ('700','600')")
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        self.assertNotIn('retirement_clock',request);saved=writer.apply(request)
        self.assertEqual(saved['rb_local_usn'],42);self.assertEqual(saved['ComposerID'],'600')
        self.assertEqual(self.mutate("SELECT * FROM djmdArtist WHERE ID='600'"),[prior])
        self.assertEqual(self.mutate('SELECT * FROM djmdAlbum'),[('700','600')])

    def test_retirement_clock_consumption_counter_bound_and_rollback_are_strict(self):
        writer,_,_=self.retirement_case(synced=1,create=False)
        request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 destination','Previous artist')
        missing=dict(request);missing.pop('retirement_clock');before=self.state()
        with self.assertRaises(WriteProofError):writer.apply(missing)
        self.assertEqual(self.state(),before)
        invalid=dict(request,retirement_clock='2020-01-01 00:00:00.000 +00:00',field='title')
        with self.assertRaises(WriteProofError):metadata.validate_request(invalid)
        self.mutate("UPDATE agentRegistry SET int_1=2147483643 WHERE registry_id='localUpdateCount'");large=self.state()
        with self.assertRaises(WriteProofError):writer.apply(request)
        self.assertEqual(self.state(),large)
        self.mutate("UPDATE agentRegistry SET int_1=40 WHERE registry_id='localUpdateCount'")
        def interrupt(phase):
            if phase=='before-commit':raise OSError('rolled back retirement')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        self.assertEqual(self.state(),before);self.assertEqual(writer.recover(),'rolled-back')
        self.mutate("UPDATE djmdContent SET ComposerID='600' WHERE ID='1'");shared=self.state()
        with self.assertRaises(WriteProofError):writer.apply(request)
        self.assertEqual(self.state(),shared)

    def test_artist_update_authorizer_is_exact_profile_field_and_column_bound(self):
        import sqlite3
        allowed={'rb_local_deleted','rb_data_status','rb_local_usn','updated_at'}
        for profile in (metadata.PROFILE,metadata.SYNCED_PROFILE,metadata.UNICODE_PROFILE,metadata.RETIREMENT_PROFILE):
            for field in ('title','artist'):
                for column in ('Name','UUID','SearchStr','usn','rb_local_synced',*sorted(allowed)):
                    expected=sqlite3.SQLITE_OK if profile==metadata.RETIREMENT_PROFILE and field=='artist' and column in allowed else sqlite3.SQLITE_DENY
                    self.assertEqual(metadata._authorizer(sqlite3.SQLITE_UPDATE,'djmdArtist',column,'main',None,
                                                         field=field,profile=profile),expected)
        for database,trigger in (('temp',None),('main','unowned_trigger')):
            self.assertEqual(metadata._authorizer(sqlite3.SQLITE_UPDATE,'djmdArtist','rb_local_deleted',database,trigger,
                field='artist',profile=metadata.RETIREMENT_PROFILE),sqlite3.SQLITE_DENY)

    def test_existing_profile_journal_reconstruction_keeps_legacy_numeric_revision_semantics(self):
        self.mutate("UPDATE djmdContent SET TrackInfoUpdated='002' WHERE ID='1'")
        request=self.request(value='Legacy saved title');before=self.state()
        with self.assertRaises(WriteProofError):self.unicode_writer().apply(request)
        self.assertEqual(self.state(),before)
        def interrupt(phase):
            if phase=='after-commit':raise OSError('lost legacy metadata delivery')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):self.writer.apply(request)
        committed=self.state()
        self.writer=metadata.SourceMetadataStore(self.authority,self.root/'data')
        self.assertEqual(self.writer.recover(),'committed')
        self.writer.apply(request);self.assertEqual(self.state(),committed)
        self.assertEqual(self.mutate("SELECT Title,TrackInfoUpdated FROM djmdContent WHERE ID='1'"),
                         [('Legacy saved title','3')])
        metadata.retire_acknowledged_receipt(self.root/'data',self.writer.retirement_entry(request))
        self.writer.apply(request);self.assertEqual(self.state(),committed)

    def test_unicode_pair_preserves_codepoints_without_retagging_or_normalizing_opaque_data(self):
        writer=self.unicode_writer()
        self.mutate('UPDATE djmdContent SET rb_local_synced=1 WHERE ID=?',('1',))
        title='Björk 東京 — e\u0301 / 🎵';artist='Björk 東京 e\u0301'
        before=self.state();xml=writer.xml.read_bytes()
        title_request=writer.capture_request('1',UUID,'/synthetic/song.wav','title',title,'Before')
        artist_request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist',artist,'')
        writer.apply(title_request);saved=writer.apply(artist_request)
        self.assertEqual((saved['Title'],saved['TrackInfoUpdated'],saved['rb_local_synced'],saved['rb_local_usn']),
                         (title,'4',1,45))
        self.assertEqual(self.mutate('SELECT Name FROM djmdArtist WHERE ID=?',(saved['ArtistID'],)),[(artist,)])
        self.assertEqual(writer.xml.read_bytes(),xml)
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdContent','djmdArtist','agentRegistry'})
        committed=self.state();writer.apply(title_request);writer.apply(artist_request)
        self.assertEqual(self.state(),committed)

    def test_unicode_title_preserves_existing_album_and_related_artist_rows_under_separate_profile(self):
        self.mutate('INSERT INTO djmdAlbum VALUES (?,?)',('500',None))
        self.mutate('UPDATE djmdContent SET AlbumID=?,rb_local_synced=1 WHERE ID=?',('500','1'))
        request=self.request(value='é'*255);before=self.state();writer=self.unicode_writer()
        for profile in (metadata.PROFILE,metadata.SYNCED_PROFILE):
            old=metadata.SourceMetadataStore(replace(self.authority,profile=profile),self.root/'data')
            with self.assertRaises(WriteProofError):old.apply(request)
            self.assertEqual(self.state(),before)
        saved=writer.apply(request)
        self.assertEqual((saved['Title'],saved['AlbumID'],saved['TrackInfoUpdated'],saved['rb_local_usn']),
                         ('é'*255,'500','3',42))
        self.assertEqual(self.mutate('SELECT * FROM djmdAlbum'),[('500',None)])
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdContent','agentRegistry'})

    def test_unicode_profile_reuses_synced_artist_without_modifying_its_identity_flags_or_clock(self):
        actor=('600','Björk 東京',None,'00000000-0000-4000-8000-000000000600',0,0,0,1,None,12,CLOCK,CLOCK)
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',actor)
        writer=self.unicode_writer();request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist',actor[1],'')
        self.assertIsNone(request['artist_seed']);before=self.state()
        with self.assertRaises(WriteProofError):self.writer.apply(request)
        self.assertEqual(self.state(),before)
        saved=writer.apply(request)
        self.assertEqual((saved['ArtistID'],saved['TrackInfoUpdated'],saved['rb_local_usn']),('600','3',42))
        self.assertEqual(self.mutate('SELECT * FROM djmdArtist WHERE ID=?',('600',)),[actor])
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdContent','agentRegistry'})

    def test_unicode_profile_recovery_and_compact_ack_remain_profile_bound(self):
        writer=self.unicode_writer();request=self.request(value='東京 e\u0301')
        def interrupt(phase):
            if phase=='after-commit':raise OSError('lost unicode metadata delivery')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(request)
        committed=self.state()
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertEqual(self.state(),committed)
        writer=self.unicode_writer();self.assertEqual(writer.recover(),'committed')
        writer.apply(request);self.assertEqual(self.state(),committed)
        entry=writer.retirement_entry(request);metadata.retire_acknowledged_receipt(self.root/'data',entry)
        self.assertEqual(json.loads(writer._receipt_path(request['operation_id']).read_bytes())['profile'],metadata.UNICODE_PROFILE)
        with self.assertRaises(RecoveryRequired):self.writer.apply(request)
        writer.apply(request);self.assertEqual(self.state(),committed)

    def test_unicode_profile_first_legacy_metadata_save_uses_native_zero_revision_without_analysis(self):
        writer=self.unicode_writer()
        for analysed in (0,105):
            for revision in (None,'0','1'):
                with self.subTest(analysed=analysed,revision=revision):
                    self.mutate('UPDATE djmdContent SET Title=?,Analysed=?,TrackInfoUpdated=? WHERE ID=?',
                                ('Before',analysed,revision,'1'))
                    before=self.state();counter=self.mutate("SELECT int_1 FROM agentRegistry WHERE registry_id='localUpdateCount'")[0][0]
                    request=self.request(value='東京 e\u0301')
                    for profile in (metadata.PROFILE,metadata.SYNCED_PROFILE):
                        old=metadata.SourceMetadataStore(replace(self.authority,profile=profile),self.root/'data')
                        with self.assertRaises(WriteProofError):old.apply(request)
                        self.assertEqual(self.state(),before)
                    saved=writer.apply(request)
                    self.assertEqual((saved['Title'],saved['Analysed'],saved['TrackInfoUpdated'],saved['rb_local_usn']),
                                     ('東京 e\u0301',analysed,str(int(revision or '0')+1),counter+2))
                    self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                                     {'djmdContent','agentRegistry'})
                    committed=self.state();writer.apply(request);self.assertEqual(self.state(),committed)

    def test_unicode_bare_paired_save_lost_delivery_recovery_and_retirement_preserve_first_revision(self):
        self.mutate('UPDATE djmdContent SET Analysed=0,TrackInfoUpdated=NULL WHERE ID=?',('1',))
        writer=self.unicode_writer();xml=writer.xml.read_bytes()
        artist=writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京 e\u0301','')
        def interrupt(phase):
            if phase=='after-commit':raise OSError('lost first bare metadata delivery')
        with mock.patch.object(metadata,'_fault',side_effect=interrupt),self.assertRaises(OSError):writer.apply(artist)
        committed=self.state();writer=self.unicode_writer();self.assertEqual(writer.recover(),'committed')
        writer.apply(artist);self.assertEqual(self.state(),committed)
        row=self.mutate('SELECT Analysed,TrackInfoUpdated,rb_local_usn FROM djmdContent WHERE ID=?',('1',))
        self.assertEqual(row,[(0,'1',43)])
        title=self.request(value='Bare title 東京');saved=writer.apply(title)
        self.assertEqual((saved['Analysed'],saved['TrackInfoUpdated'],saved['rb_local_usn']),(0,'2',45))
        for request in (artist,title):
            metadata.retire_acknowledged_receipt(self.root/'data',writer.retirement_entry(request))
        committed=self.state();writer.apply(artist);writer.apply(title);self.assertEqual(self.state(),committed)
        self.assertEqual(writer.xml.read_bytes(),xml)

    def test_unicode_profile_read_only_artist_reuse_preserves_null_local_clock(self):
        actor=('600','Legacy actor',None,'00000000-0000-4000-8000-000000000600',0,0,0,1,None,None,CLOCK,CLOCK)
        self.mutate('INSERT INTO djmdArtist VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',actor)
        writer=self.unicode_writer();request=writer.capture_request('1',UUID,'/synthetic/song.wav','artist',actor[1],'')
        before=self.state()
        with self.assertRaises(WriteProofError):self.writer.apply(request)
        self.assertEqual(self.state(),before)
        saved=writer.apply(request)
        self.assertEqual((saved['ArtistID'],saved['TrackInfoUpdated'],saved['rb_local_usn']),('600','3',42))
        self.assertEqual(self.mutate('SELECT * FROM djmdArtist WHERE ID=?',('600',)),[actor])
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdContent','agentRegistry'})

    def test_unicode_profile_does_not_guess_cloud_album_artist_or_control_character_protocols(self):
        writer=self.unicode_writer()
        for value in ('東京\x00','東京\nline','東京\u200d','\ud800','é'*256):
            with self.subTest(value=repr(value)):
                before=self.state()
                with self.assertRaises(WriteProofError):writer.apply(self.request(value=value))
                self.assertEqual(self.state(),before)
        for column,value in (('Analysed',1),('TrackInfoUpdated',''),('TrackInfoUpdated','-1'),
                             ('TrackInfoUpdated','01'),('TrackInfoUpdated','2147483647'),('TrackInfoUpdated','٢'),('usn',1),
                             ('rb_local_synced',2),('rb_data_status',257)):
            with self.subTest(column=column):
                self.mutate('UPDATE djmdContent SET '+column+'=? WHERE ID=?',(value,'1'));before=self.state()
                with self.assertRaises(WriteProofError):writer.apply(self.request(value='東京'))
                self.assertEqual(self.state(),before)
                self.mutate("UPDATE djmdContent SET Analysed=105,TrackInfoUpdated='2',usn=NULL,rb_local_synced=0,rb_data_status=0 WHERE ID='1'")
        self.mutate('UPDATE djmdContent SET AlbumID=? WHERE ID=?',('500','1'));before=self.state()
        with self.assertRaises(WriteProofError):writer.capture_request('1',UUID,'/synthetic/song.wav','artist','東京','')
        self.assertEqual(self.state(),before)

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
