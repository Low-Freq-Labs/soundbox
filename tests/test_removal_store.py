"""Confirmed disposal and crash recovery on synthetic TEMP music/libraries only."""
from dataclasses import asdict,replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support.native_library import source_fixture
from tests.support.audio import wave
from soundbox import rekordbox_removal_store as store,rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import _connect,_state,WriteProofError,ConflictError,RecoveryRequired

UUID='00000000-0000-4000-8000-000000000001'
FLAGS=dict(rb_local_deleted=0,rb_local_synced=1,rb_data_status=0,rb_local_data_status=0,usn=None,rb_local_usn=30,updated_at='before')


def fixture(root):
    source=root/'source';source_fixture(source);audio=root/'song.wav';wave(audio)
    conn=_connect(source/'master.db')
    try:
        conn.execute('UPDATE djmdContent SET UUID=?,FolderPath=? WHERE ID=\'1\'',(UUID,str(audio)))
        for key,value in FLAGS.items():conn.execute('UPDATE djmdContent SET "'+key+'"=?',(value,))
        for table,extras in (('contentFile',dict(Path='TEXT')),('contentCue',{}),('djmdCue',{}),
                ('djmdSongPlaylist',dict(PlaylistID='TEXT',TrackNo='INTEGER')),('djmdSongHistory',dict(HistoryID='TEXT',TrackNo='INTEGER')),
                ('djmdMixerParam',{})):
            conn.execute('DROP TABLE IF EXISTS '+table)
            fields=dict(ID='TEXT PRIMARY KEY',ContentID='TEXT',**extras,**{k:'INTEGER' if type(v) is int else 'TEXT' for k,v in FLAGS.items()})
            conn.execute('CREATE TABLE '+table+' ('+','.join(k+' '+v for k,v in fields.items())+')')
        conn.execute("DELETE FROM djmdPlaylist");conn.execute("INSERT INTO djmdPlaylist VALUES ('2','Keep playlist')")
        for table,extra in (('contentFile',dict(Path='/PIONEER/USBANLZ/000/00000-0000-4000-8000-000000000001/ANLZ0000.DAT')),
                ('contentCue',{}),('djmdCue',{}),('djmdSongPlaylist',dict(PlaylistID='2',TrackNo=3)),
                ('djmdSongHistory',dict(HistoryID='7',TrackNo=5)),('djmdMixerParam',{})):
            row=dict(ID=table+'-target',ContentID='1',**FLAGS,**extra)
            if table=='djmdCue':row['rb_local_synced']=0
            conn.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
            if table in ('djmdSongPlaylist','djmdSongHistory'):
                row.update(ID=table+'-later',ContentID='9',TrackNo=8)
                conn.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        schema=tags._schema(conn)
    finally:conn.close()
    (source/'masterPlaylists6.xml').write_bytes(b'<MASTER_PLAYLIST Version="3.0.0" AutomaticSync="0">\n<PLAYLISTS>\n    <NODE Id="2" ParentId="0" Attribute="0" Timestamp="1" Lib_Type="0" CheckType="0"/>\n</PLAYLISTS>\n</MASTER_PLAYLIST>')
    resource=source/'share/PIONEER/USBANLZ/000/00000-0000-4000-8000-000000000001/ANLZ0000.DAT'
    resource.parent.mkdir(parents=True);resource.write_bytes(b'opaque-analysis')
    return source,audio,resource,schema


def fake_trash(root):
    def move(path):
        destination=root/path.name
        if destination.exists():raise OSError('occupied fake Trash')
        path.rename(destination);return destination
    return move


class RemovalStore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.source,self.audio,self.resource,schema=fixture(self.root)
        for obj,key in ((cues,'_closed'),(tags,'_assert_closed')):self.enterContext(mock.patch.object(obj,key,return_value=None))
        for obj in (cues,tags):self.enterContext(mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION))
        self.authority=store.RemovalAuthority(str(self.source/'master.db'),'123',schema,'0'*64,True,unsynced_accepted=True)
        self.trash=self.root/'trash';self.trash.mkdir()
        self.writer=store.RemovalStore(self.authority,self.root/'appdata',trash=fake_trash(self.trash),trash_root=self.trash)
        self.entry=dict(id='1',native_id='1',native=dict(uuid=UUID),path=str(self.audio),canonical=str(self.audio),exists=True,display=dict(title='Chosen'))

    def prepare(self):return self.writer.prepare([self.entry])
    def apply(self,review):return self.writer.apply(review['folder'],review['sha256'],store.confirmation(review['plan']))
    def state(self):
        conn=_connect(self.source/'master.db',readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def test_capture_is_read_only_then_confirmed_delete_preserves_unrelated_data_and_trashes_exact_audio(self):
        before=self.state();raw=self.audio.read_bytes();xml=self.writer.xml.read_bytes();review=self.prepare()
        self.assertEqual(self.state(),before);self.assertEqual(self.audio.read_bytes(),raw);self.assertEqual(self.writer.xml.read_bytes(),xml)
        result=self.apply(review);self.assertEqual(result['outcome'],'committed');self.assertFalse(self.audio.exists())
        self.assertEqual((self.trash/self.audio.name).read_bytes(),raw);self.assertFalse(self.resource.exists())
        self.assertTrue((Path(review['folder'])/'resources/0').exists());self.assertEqual(self.apply(review),result)
        after=self.state();self.assertEqual(before['tables']['unknown_table'],after['tables']['unknown_table'])
        conn=_connect(self.source/'master.db',readonly=True)
        try:
            self.assertEqual(conn.execute("SELECT rb_local_deleted FROM djmdContent WHERE ID='1'").fetchone(),(1,))
            self.assertEqual(conn.execute("SELECT TrackNo FROM djmdSongPlaylist WHERE ContentID='9'").fetchone(),(7,))
            self.assertEqual(conn.execute('SELECT count(*) FROM djmdCue').fetchone(),(0,))
        finally:conn.close()

    def test_atomic_disposal_commits_without_trash_enumeration_permission(self):
        from soundbox.mac_trash import move_confirmed,fallback_name
        self.writer.trash=lambda p:move_confirmed(p,self.trash)
        raw=self.audio.read_bytes();review=self.prepare()
        self.trash.chmod(0o300)
        try:
            self.assertEqual(self.apply(review)['outcome'],'committed')
            target=self.trash/fallback_name(self.audio,review['plan']['audio'][0]['tag'])
            self.assertEqual(target.read_bytes(),raw)
        finally:self.trash.chmod(0o700)

    def test_error_after_atomic_rename_rolls_back_without_progress_or_trash_listing(self):
        from soundbox import mac_trash
        self.writer.trash=lambda p:mac_trash.move_confirmed(p,self.trash)
        raw=self.audio.read_bytes();before=self.state();review=self.prepare()
        self.trash.chmod(0o300)
        try:
            with mock.patch.object(mac_trash,'sync_dir',side_effect=PermissionError('after-rename injected failure')):
                with self.assertRaises(PermissionError):self.apply(review)
            self.assertEqual(self.audio.read_bytes(),raw)
            self.assertEqual(self.state(),before);self.assertFalse(self.writer.journal.exists())
        finally:self.trash.chmod(0o700)

    def test_confirmation_and_audio_drift_cannot_reach_source_sql_and_shared_files_stay_usable(self):
        review=self.prepare();before=self.state()
        for args in (('0'*64,store.confirmation(review['plan'])),(review['sha256'],'0'*64)):
            with self.assertRaises(WriteProofError):self.writer.apply(review['folder'],*args)
        self.audio.write_bytes(self.audio.read_bytes()[:-1]+b'X')
        with self.assertRaises(ConflictError):self.apply(review)
        self.assertEqual(self.state(),before);self.assertTrue(self.audio.exists());self.assertFalse(self.writer.journal.exists())
        conn=_connect(self.source/'master.db')
        try:
            conn.execute("INSERT INTO djmdContent(ID,UUID,Title,FolderPath,rb_local_deleted) VALUES ('2',?,'Other',?,0)",('00000000-0000-4000-8000-000000000002',str(self.audio)))
        finally:conn.close()
        review=self.prepare();self.assertEqual(review['plan']['audio'],[])
        self.assertTrue(review['plan']['selections'][0]['keep_file']);self.assertEqual(review['plan']['retained'][0]['references'][0]['id'],'2')
        raw=self.audio.read_bytes();self.apply(review);self.assertEqual(self.audio.read_bytes(),raw)
        conn=_connect(self.source/'master.db',readonly=True)
        try:self.assertEqual(conn.execute("SELECT rb_local_deleted FROM djmdContent WHERE ID='2'").fetchone(),(0,))
        finally:conn.close()

    def test_inaccessible_unselected_alias_cannot_hide_a_shared_file_reference(self):
        blocked=self.root/'protected-alias/song.wav';conn=_connect(self.source/'master.db')
        try:conn.execute("INSERT INTO djmdContent(ID,UUID,Title,FolderPath,rb_local_deleted) VALUES ('2',?,'Kept copy',?,0)",('00000000-0000-4000-8000-000000000002',str(blocked)))
        finally:conn.close()
        resolve=Path.resolve
        def inaccessible(path,*args,**kw):
            if path==blocked:raise PermissionError('Synthetic inaccessible alias')
            return resolve(path,*args,**kw)
        before=self.state();raw=self.audio.read_bytes()
        with mock.patch.object(Path,'resolve',inaccessible),self.assertRaisesRegex(WriteProofError,'Cannot verify'):
            self.prepare()
        self.assertEqual(self.state(),before);self.assertEqual(self.audio.read_bytes(),raw);self.assertEqual(list(self.trash.iterdir()),[])

    def test_missing_whole_folder_does_not_block_available_selected_audio(self):
        missing=self.root/'gone/subfolder/song.wav'
        entry=dict(id='old',native_id='old',native=dict(uuid=''),path=str(missing),display=dict(title='Already gone'))
        raw=self.audio.read_bytes();review=self.writer.prepare([self.entry,entry])
        self.assertEqual(review['plan']['missing'],[str(missing)])
        self.assertEqual(review['plan']['missing_proofs'][str(missing)]['ancestor'],str(self.root))
        self.assertTrue(self.audio.exists());self.apply(review)
        self.assertEqual((self.trash/self.audio.name).read_bytes(),raw)
        self.assertFalse(missing.parent.exists())

    def test_legacy_synced_playlist_missing_from_xml_uses_existing_local_membership_protocol(self):
        conn=_connect(self.source/'master.db')
        try:
            # A real native library can retain an ordinary synced playlist
            # without a corresponding XML node (SWANK/DNB in Sonny's snapshot).
            for name,kind in (('UUID','TEXT'),('ParentID','TEXT'),('Attribute','INTEGER'),
                              *((k,'INTEGER' if type(v) is int else 'TEXT') for k,v in FLAGS.items())):
                conn.execute('ALTER TABLE djmdPlaylist ADD COLUMN '+name+' '+kind)
            row=dict(UUID='00000000-0000-4000-8000-000000000002',ParentID='root',Attribute=0,**FLAGS)
            conn.execute('UPDATE djmdPlaylist SET '+','.join(k+'=?' for k in row),tuple(row.values()))
        finally:conn.close()
        self.writer.xml.write_bytes(b'<MASTER_PLAYLIST Version="3.0.0" AutomaticSync="0">\n<PLAYLISTS>\n</PLAYLISTS>\n</MASTER_PLAYLIST>')
        conn=_connect(self.source/'master.db',readonly=True)
        try:authority=replace(self.authority,schema_sha256=tags._schema(conn))
        finally:conn.close()
        self.writer=store.RemovalStore(authority,self.root/'appdata',trash=fake_trash(self.trash),trash_root=self.trash)
        review=self.prepare();plan=review['plan']
        self.assertEqual(plan['playlists'],['2']);self.assertIn(b'Id="2"', (Path(review['folder'])/'after.xml').read_bytes())
        self.apply(review)
        self.assertTrue((self.trash/self.audio.name).exists())

    def test_compacted_bulk_plan_matches_every_expanded_row_counter_and_xml_clock(self):
        conn=_connect(self.source/'master.db')
        try:
            row=store._decode(store._row(conn,'djmdContent','1'))
            row.update(ID='3',UUID='00000000-0000-4000-8000-000000000003')
            conn.execute('INSERT INTO djmdContent('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
            for table,field in (('djmdSongPlaylist','PlaylistID'),('djmdSongHistory','HistoryID')):
                row=store._decode(store._row(conn,table,table+'-target'))
                row.update(ID=table+'-second',ContentID='3',TrackNo=4)
                conn.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
            tables=self.writer._catalog(conn)
        finally:conn.close()
        targets=[dict(id=tid,uuid=uuid,path=str(self.audio)) for tid,uuid in
                 (('1',UUID),('3','00000000-0000-4000-8000-000000000003'))]
        xml=self.writer.xml.read_bytes();expanded=store.planner.build(tables,targets,xml,timestamp='fixed')
        compact=store.planner.build(tables,targets,xml,timestamp='fixed',xml_clocks=expanded['xml_clocks'],compact=True)
        self.assertLess(len(compact['changes']),len(expanded['changes']))
        self.assertEqual(store._edges(compact['changes']),store._edges(expanded['changes']))
        self.assertEqual(compact['counter'],expanded['counter']);self.assertEqual(compact['after_xml'],expanded['after_xml'])

    def test_missing_ancestor_replacement_redirect_and_permission_failure_are_not_absence(self):
        missing=self.root/'gone/subfolder/song.wav'
        entry=dict(id='old',native_id='old',native=dict(uuid=''),path=str(missing),display=dict(title='Already gone'))
        review=self.writer.prepare([self.entry,entry]);before=self.state()
        missing.parent.mkdir(parents=True)
        with self.assertRaises(ConflictError):self.apply(review)
        self.assertEqual(self.state(),before);self.assertTrue(self.audio.exists())
        missing.parent.rmdir();missing.parent.parent.rmdir()
        (self.root/'gone').symlink_to(self.root/'elsewhere',target_is_directory=True)
        with self.assertRaises(WriteProofError):store._missing_proof(missing)
        (self.root/'gone').unlink()
        with mock.patch.object(Path,'lstat',side_effect=PermissionError('Denied')):
            with self.assertRaises(PermissionError):store._missing_proof(missing)

    def test_every_exception_prefix_restores_entire_audio_resources_sql_and_xml(self):
        for phase in ('tagged-0','trashed-0','resource-0','xml','sql'):
            with self.subTest(phase=phase):
                review=self.prepare();before=self.state();raw=self.audio.read_bytes();xml=self.writer.xml.read_bytes()
                def fail(current):
                    if current==phase:raise RuntimeError('injected TEMP failure')
                with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):self.apply(review)
                self.assertEqual(self.state(),before);self.assertEqual(self.audio.read_bytes(),raw)
                self.assertTrue(self.resource.exists());self.assertEqual(self.writer.xml.read_bytes(),xml);self.assertFalse(self.writer.journal.exists())

    def test_unsynced_native_rows_are_physically_removed_and_historical_only_entries_still_dispose(self):
        conn=_connect(self.source/'master.db')
        try:
            for table in store.planner.TABLES:
                if table not in ('agentRegistry','djmdSongMyTag'):conn.execute('UPDATE '+table+" SET rb_local_synced=0 WHERE "+('ID' if table=='djmdContent' else 'ContentID')+"='1'")
        finally:conn.close()
        denied=store.RemovalStore(replace(self.authority,unsynced_accepted=False),self.root/'appdata',trash=fake_trash(self.trash),trash_root=self.trash)
        with self.assertRaises(WriteProofError):denied.prepare([self.entry])
        self.apply(self.prepare());conn=_connect(self.source/'master.db',readonly=True)
        try:self.assertEqual(conn.execute("SELECT count(*) FROM djmdContent WHERE ID='1'").fetchone(),(0,))
        finally:conn.close()
        shutil.move(self.trash/self.audio.name,self.audio)
        review=self.prepare();self.assertEqual(review['plan']['changes'],[]);self.apply(review);self.assertFalse(self.audio.exists())

    def test_unrelated_or_changed_recovery_evidence_blocks_without_moving_audio(self):
        review=self.prepare();(Path(review['folder'])/'after.db').write_bytes(b'bad')
        with self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertTrue(self.audio.exists());self.assertTrue(self.resource.exists())
        self.writer.journal.write_text('{"format":"another-domain"}')
        with self.assertRaises(RecoveryRequired):self.prepare()

    def test_warm_plan_proof_hashes_every_payload_despite_repeated_stat_identity(self):
        review=self.prepare();folder=Path(review['folder']);self.writer._load(folder)
        payloads=[folder/name for name in ('before.db','after.db','before.xml','after.xml')]
        payloads.append(folder/'resources/0')
        original_identity=store.identity
        epochs={(p.stat().st_dev,p.stat().st_ino):original_identity(p.stat()) for p in payloads}
        repeated=lambda info:epochs.get((info.st_dev,info.st_ino),original_identity(info))
        # Repeated metadata is never a byte proof. Every corruption must fail
        # before native SQL or any original audio/resource mutation.
        for payload in payloads:
            with self.subTest(payload=payload.name):
                raw=payload.read_bytes();epoch=payload.stat()
                payload.write_bytes(bytes([raw[0]^1])+raw[1:])
                os.utime(payload,ns=(epoch.st_atime_ns,epoch.st_mtime_ns))
                try:
                    with mock.patch.object(store,'identity',side_effect=repeated),mock.patch.object(self.writer.lease,'_connect',side_effect=AssertionError('No source SQL')):
                        with self.assertRaises(RecoveryRequired):self.apply(review)
                finally:payload.write_bytes(raw)
                self.assertTrue(self.audio.exists());self.assertTrue(self.resource.exists())
                self.assertEqual(list(self.trash.iterdir()),[]);self.assertFalse(self.writer.journal.exists())

    def test_warm_plan_proof_rechecks_raw_plan_authority_and_source_binding(self):
        review=self.prepare();folder=Path(review['folder']);self.writer._load(folder)
        path=folder/'plan.json';raw=path.read_bytes();epoch=store.identity(path.stat())
        plan=json.loads(raw);plan['authority']['accepted_reference_sha256']='b'*64
        path.write_text(json.dumps(plan));original_identity=store.identity
        repeated=lambda info:epoch if info.st_ino==path.stat().st_ino else original_identity(info)
        with mock.patch.object(store,'identity',side_effect=repeated),self.assertRaises(RecoveryRequired):self.writer._load(folder)
        path.write_bytes(raw);self.writer._load(folder)
        self.writer.authority=replace(self.authority,accepted_reference_sha256='c'*64)
        with self.assertRaises(RecoveryRequired):self.writer._load(folder)
        self.writer.authority=self.authority;self.writer.file_id=(self.writer.file_id[0],self.writer.file_id[1]+1)
        with self.assertRaises(RecoveryRequired):self.writer._load(folder)
        self.assertTrue(self.audio.exists());self.assertTrue(self.resource.exists());self.assertFalse(self.writer.journal.exists())

    def test_warm_plan_reuses_only_semantics_and_returns_fresh_plan_data(self):
        review=self.prepare();folder=Path(review['folder']);plan=self.writer._load(folder)
        plan['changes'].clear()
        with mock.patch.object(store.planner,'build',side_effect=AssertionError('Reuse exact semantic proof')),mock.patch.object(store,'_sha',wraps=store._sha) as hashes:
            again=self.writer._load(folder)
        self.assertEqual(again['changes'],review['plan']['changes'])
        self.assertEqual({Path(call.args[0]) for call in hashes.call_args_list},
            {folder/name for name in ('before.db','after.db','before.xml','after.xml','resources/0')})

    def test_missing_audio_can_remove_collection_but_a_new_occupant_invalidates_review(self):
        self.audio.unlink();review=self.prepare();self.assertEqual(review['plan']['missing'],[str(self.audio)])
        wave(self.audio)
        with self.assertRaises(ConflictError):self.apply(review)
        self.audio.unlink();self.assertEqual(self.apply(self.prepare())['outcome'],'committed')

    def test_old_full_pairs_retire_without_losing_replay_or_latest_recovery(self):
        first=self.prepare();result=self.apply(first);first_folder=Path(first['folder'])
        shutil.move(self.trash/self.audio.name,self.audio)
        second=self.prepare();self.apply(second);second_folder=Path(second['folder'])
        self.assertFalse((first_folder/'before.db').exists());self.assertFalse((first_folder/'after.db').exists())
        self.assertTrue((first_folder/'retired.json').exists());self.assertTrue((first_folder/'resources/0').exists())
        self.assertTrue((second_folder/'before.db').exists());self.assertEqual(self.apply(first),result)
        pointer=self.writer.root/'removal-latest.json';value=json.loads(pointer.read_bytes());value['retiring']=value['current'];pointer.write_text(json.dumps(value))
        with self.assertRaises(RecoveryRequired):self.writer.recover()
        self.assertTrue((second_folder/'before.db').exists())

    def test_cancel_discards_only_unstarted_review_copies_and_preserves_originals(self):
        before=self.state();raw=self.audio.read_bytes();review=self.prepare();folder=Path(review['folder'])
        with mock.patch.object(self.writer.lease,'_connect',side_effect=AssertionError('No source SQL for Cancel')):
            self.writer.discard_unstarted(folder,review['sha256'])
        self.assertFalse(folder.exists());self.assertEqual(self.state(),before);self.assertEqual(self.audio.read_bytes(),raw)
        review=self.prepare();folder=Path(review['folder']);(folder/'foreign').write_text('keep')
        with self.assertRaises(RecoveryRequired):self.writer.discard_unstarted(folder,review['sha256'])
        self.assertTrue((folder/'before.db').exists());self.assertTrue((folder/'foreign').exists())

    def _fresh_writer(self):
        return store.RemovalStore(self.authority,self.root/'appdata',trash=fake_trash(self.trash),trash_root=self.trash)

    def _lose_process(self,review,phase,*,legacy_parents=False):
        request=self.root/'cold-removal.json'
        request.write_text(json.dumps(dict(authority=asdict(self.authority),root=str(self.root/'appdata'),
            trash=str(self.trash),review=review,legacy_parents=legacy_parents)))
        child='''import json,os,sys
from pathlib import Path
from soundbox import rekordbox_removal_store as s,rekordbox_cue_store as c
c._closed=lambda:None;c._installed_version=lambda:'6.8.7.0303'
data=json.loads(Path(sys.argv[1]).read_bytes());trash=Path(data['trash'])
def move(path):
 destination=trash/path.name;path.rename(destination);return destination
w=s.RemovalStore(s.RemovalAuthority(**data['authority']),Path(data['root']),trash=move,trash_root=trash)
# The previous writer had no parent-token admission. Exercise its saved prefix.
if data['legacy_parents']:
 s._require_recovery_parents=lambda *_:None
 s._recovery_parent=lambda *_:None
def fault(phase):
 if phase==sys.argv[2]:os._exit(73)
s._fault=fault;p=data['review'];w.apply(p['folder'],p['sha256'],s.confirmation(p['plan']))
'''
        result=subprocess.run([sys.executable,'-c',child,str(request),phase],
            capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,73,result.stderr)

    def test_cold_audio_recovery_requires_the_original_parent_before_any_restore(self):
        music=self.root/'music';music.mkdir();self.audio.rename(music/'song.wav');self.audio=music/'song.wav'
        conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET FolderPath=? WHERE ID='1'",(str(self.audio),))
        finally:conn.close()
        self.entry.update(path=str(self.audio),canonical=str(self.audio))
        review=self.prepare();before=self.state();original=self.audio.read_bytes()
        self._lose_process(review,'trashed-0')
        old_music=self.root/'original-music';music.rename(old_music);music.mkdir()
        with self.assertRaisesRegex(RecoveryRequired,'parent changed'):self._fresh_writer().recover()
        self.assertFalse(self.audio.exists());self.assertFalse((old_music/self.audio.name).exists())
        self.assertEqual((self.trash/self.audio.name).read_bytes(),original)
        self.assertTrue(self.resource.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        replacement=self.root/'retained-empty-directory';music.rename(replacement);old_music.rename(music)
        self.assertEqual(self._fresh_writer().recover()['outcome'],'rolled-back')
        self.assertEqual(self.audio.read_bytes(),original);self.assertFalse(self.writer.journal.exists())
        self.assertEqual(list(replacement.iterdir()),[])

    def test_cold_resource_parent_change_stops_before_restoring_any_audio(self):
        review=self.prepare();before=self.state();original=self.audio.read_bytes();resource=self.resource.read_bytes()
        self._lose_process(review,'resource-0')
        parent=self.resource.parent;old_parent=parent.with_name(parent.name+'-retained')
        parent.rename(old_parent);parent.mkdir()
        with self.assertRaisesRegex(RecoveryRequired,'parent changed'):self._fresh_writer().recover()
        self.assertFalse(self.audio.exists());self.assertEqual((self.trash/self.audio.name).read_bytes(),original)
        self.assertFalse(self.resource.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        parent.rmdir();old_parent.rename(parent)
        self.assertEqual(self._fresh_writer().recover()['outcome'],'rolled-back')
        self.assertEqual(self.audio.read_bytes(),original);self.assertEqual(self.resource.read_bytes(),resource)

    def test_equal_byte_live_resource_cannot_retire_its_original_cold_claim(self):
        review=self.prepare();before=self.state();original=self.resource.read_bytes();original_entity=store._entity(self.resource.stat())
        self._lose_process(review,'resource-removal-claimed')
        resource=review['plan']['resources'][0];name,_token=self.writer._resource_claim(resource,review['plan'])
        claim=self.resource.parent/name;self.assertEqual(store._entity(claim.stat()),original_entity)
        self.resource.write_bytes(original);foreign=store._entity(self.resource.stat());self.assertNotEqual(foreign,original_entity)
        with self.assertRaisesRegex(RecoveryRequired,'newer live occupant'):self._fresh_writer().recover()
        self.assertEqual(store._entity(self.resource.stat()),foreign);self.assertEqual(store._entity(claim.stat()),original_entity)
        self.assertFalse(self.audio.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        external=self.root/'retained-equal-resource';self.resource.rename(external)
        self.assertEqual(self._fresh_writer().recover()['outcome'],'rolled-back')
        self.assertEqual(store._entity(self.resource.stat()),original_entity);self.assertEqual(external.read_bytes(),original)
        self.assertFalse(claim.exists());self.assertFalse(self.writer.journal.exists())

    def test_legacy_unknown_parent_retains_cold_trash_and_actionable_journal(self):
        review=self.prepare();before=self.state();original=self.audio.read_bytes()
        review['plan'].pop('recovery_parents')
        plan=Path(review['folder'])/'plan.json';store.atomic_bytes(plan,store._json(review['plan']));review['sha256']=store._sha(plan)
        self._lose_process(review,'trashed-0',legacy_parents=True)
        with self.assertRaisesRegex(RecoveryRequired,'no recorded original parent'):self._fresh_writer().recover()
        self.assertFalse(self.audio.exists());self.assertEqual((self.trash/self.audio.name).read_bytes(),original)
        self.assertTrue(self.resource.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())

    def test_cold_tagged_audio_recovery_refuses_equal_byte_foreign_live_entity(self):
        review=self.prepare();before=self.state();original=self.audio.read_bytes();original_entity=store._entity(self.audio.stat())
        self._lose_process(review,'tagged-0')
        retained=self.root/'retained-original.wav';self.audio.rename(retained)
        self.audio.write_bytes(original);foreign=store._entity(self.audio.stat());self.assertNotEqual(foreign,original_entity)
        tag=store.xattrs.get(retained,store.TAG)
        with self.assertRaisesRegex(RecoveryRequired,'Audio recovery occupant changed'):self._fresh_writer().recover()
        self.assertEqual(store._entity(self.audio.stat()),foreign);self.assertIsNone(store.xattrs.get(self.audio,store.TAG))
        self.assertEqual(store._entity(retained.stat()),original_entity);self.assertEqual(store.xattrs.get(retained,store.TAG),tag)
        self.assertTrue(self.resource.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        external=self.root/'retained-equal-foreign.wav';self.audio.rename(external);retained.rename(self.audio)
        self.assertEqual(self._fresh_writer().recover()['outcome'],'rolled-back')
        self.assertEqual(store._entity(self.audio.stat()),original_entity);self.assertIsNone(store.xattrs.get(self.audio,store.TAG))
        self.assertEqual(store._entity(external.stat()),foreign);self.assertFalse(self.writer.journal.exists())

    def test_late_audio_rollback_collision_preserves_both_files_and_restarts_after_resolution(self):
        review=self.prepare();before=self.state();original=self.audio.read_bytes();arrived=[]
        def trash_failure(path):
            fake_trash(self.trash)(path);raise OSError('after Trash publication')
        self.writer.trash=trash_failure
        def fault(phase):
            if phase=='audio-restore-ready' and not arrived:
                self.audio.write_bytes(b'other arriving audio');arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.audio.read_bytes(),b'other arriving audio')
        self.assertEqual((self.trash/self.audio.name).read_bytes(),original)
        self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        fresh=self._fresh_writer()
        with self.assertRaises(RecoveryRequired):fresh.recover()
        external=self.root/'external-retained.wav';self.audio.rename(external)
        self.assertEqual(fresh.recover()['outcome'],'rolled-back')
        self.assertEqual(self.audio.read_bytes(),original);self.assertEqual(external.read_bytes(),b'other arriving audio')
        self.assertIsNone(store.xattrs.get(self.audio,store.TAG));self.assertFalse(fresh.journal.exists())

    def test_late_resource_arrival_before_removal_claim_is_retained_with_original_backup(self):
        review=self.prepare();before=self.state();original=self.resource.read_bytes();arrived=[]
        def fault(phase):
            if phase=='resource-removal-claimed-ready' and not arrived:
                replacement=self.root/'arriving-resource';replacement.write_bytes(b'foreign resource before claim')
                replacement.replace(self.resource);arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.resource.read_bytes(),b'foreign resource before claim')
        self.assertEqual((Path(review['folder'])/'resources/0').read_bytes(),original)
        self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        external=self.root/'retained-native-resource';self.resource.rename(external)
        fresh=self._fresh_writer();self.assertEqual(fresh.recover()['outcome'],'rolled-back')
        self.assertEqual(self.resource.read_bytes(),original);self.assertEqual(external.read_bytes(),b'foreign resource before claim')

    def test_audio_parent_replacement_refuses_without_moving_the_trash_original(self):
        music=self.root/'music';music.mkdir();self.audio.rename(music/'song.wav');self.audio=music/'song.wav'
        conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET FolderPath=? WHERE ID='1'",(str(self.audio),))
        finally:conn.close()
        self.entry.update(path=str(self.audio),canonical=str(self.audio));review=self.prepare();original=self.audio.read_bytes();arrived=[]
        old_music=self.root/'old-music'
        def trash_failure(path):fake_trash(self.trash)(path);raise OSError('after Trash publication')
        self.writer.trash=trash_failure
        def fault(phase):
            if phase=='audio-restore-ready' and not arrived:
                music.rename(old_music);music.mkdir();(music/'song.wav').write_bytes(b'foreign parent occupant');arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.audio.read_bytes(),b'foreign parent occupant')
        self.assertEqual((self.trash/'song.wav').read_bytes(),original);self.assertFalse((old_music/'song.wav').exists())
        self.assertTrue(self.writer.journal.exists())
        external=self.root/'retained-directory';music.rename(external);old_music.rename(music)
        self.assertEqual(self._fresh_writer().recover()['outcome'],'rolled-back');self.assertEqual(self.audio.read_bytes(),original)
        self.assertEqual((external/'song.wav').read_bytes(),b'foreign parent occupant')

    def test_late_resource_restore_collision_preserves_backup_and_recovery_after_audio_restored(self):
        review=self.prepare();before=self.state();original=self.resource.read_bytes();arrived=[]
        def fault(phase):
            if phase=='resource-0':raise OSError('resource phase failed')
            if phase=='resource-restore-ready' and not arrived:
                self.resource.write_bytes(b'other native resource');arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.resource.read_bytes(),b'other native resource')
        self.assertEqual((Path(review['folder'])/'resources/0').read_bytes(),original)
        self.assertTrue(self.audio.exists());self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        fresh=self._fresh_writer()
        with self.assertRaises(RecoveryRequired):fresh.recover()
        external=self.root/'external-resource.dat';self.resource.rename(external)
        self.assertEqual(fresh.recover()['outcome'],'rolled-back');self.assertEqual(self.resource.read_bytes(),original)
        self.assertEqual(external.read_bytes(),b'other native resource');self.assertFalse(fresh.journal.exists())

    def test_xml_claim_publication_collision_preserves_external_xml_and_restarts(self):
        review=self.prepare();before=self.state();original=self.writer.xml.read_bytes();arrived=[]
        def fault(phase):
            if phase=='xml-replacement-published-ready' and not arrived:
                self.writer.xml.write_bytes(b'external arriving playlist XML');arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.writer.xml.read_bytes(),b'external arriving playlist XML')
        old=self.writer.xml.parent/self.writer._xml_names(review['plan'])['original']
        self.assertEqual(old.read_bytes(),original);self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        fresh=self._fresh_writer()
        with self.assertRaises(RecoveryRequired):fresh.recover()
        external=self.root/'external-retained.xml';self.writer.xml.rename(external)
        self.assertEqual(fresh.recover()['outcome'],'rolled-back');self.assertEqual(fresh.xml.read_bytes(),original)
        self.assertEqual(external.read_bytes(),b'external arriving playlist XML');self.assertFalse(fresh.journal.exists())
        self.assertFalse(list(self.source.glob('.*.removal-*')))

    def test_xml_late_replacement_during_rollback_claim_is_never_discarded(self):
        review=self.prepare();before=self.state();original=self.writer.xml.read_bytes();rolling=[];arrived=[]
        def fault(phase):
            if phase=='xml':rolling.append(True);raise OSError('after XML publication')
            if phase=='xml-original-claimed-ready' and rolling and not arrived:
                external=self.root/'new-external.xml';external.write_bytes(b'late foreign XML');external.replace(self.writer.xml);arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        self.assertEqual(self.writer.xml.read_bytes(),b'late foreign XML')
        old=self.writer.xml.parent/self.writer._xml_names(review['plan'])['original']
        self.assertEqual(old.read_bytes(),original);self.assertEqual(self.state(),before);self.assertTrue(self.writer.journal.exists())
        external=self.root/'retained-foreign.xml';self.writer.xml.rename(external)
        fresh=self._fresh_writer();self.assertEqual(fresh.recover()['outcome'],'rolled-back')
        self.assertEqual(fresh.xml.read_bytes(),original);self.assertEqual(external.read_bytes(),b'late foreign XML')

    def test_xml_marker_parent_and_owned_payload_tampering_stop_before_recovery_mutation(self):
        review=self.prepare();arrived=[]
        def fault(phase):
            if phase=='xml-replacement-published-ready' and not arrived:
                self.writer.xml.write_bytes(b'foreign XML');arrived.append(True)
        with mock.patch.object(store,'_fault',side_effect=fault),self.assertRaises(RecoveryRequired):self.apply(review)
        marker=Path(review['folder'])/'xml-recovery.json';raw=marker.read_bytes();value=json.loads(raw)
        value['parent'][1]+=1;marker.write_text(json.dumps(value))
        with self.assertRaises(RecoveryRequired):self._fresh_writer().recover()
        marker.write_bytes(raw);old=self.writer.xml.parent/self.writer._xml_names(review['plan'])['original'];old.write_bytes(b'tampered original claim')
        with self.assertRaises(RecoveryRequired):self._fresh_writer().recover()
        self.assertEqual(self.writer.xml.read_bytes(),b'foreign XML');self.assertTrue(self.writer.journal.exists())

    def test_process_loss_recovers_exact_prefixes_including_sql_spill_and_commit(self):
        child='''import os,json,sys
from pathlib import Path
from soundbox import rekordbox_removal_store as s,rekordbox_cue_store as c
c._closed=lambda:None;c._installed_version=lambda:'6.8.7.0303'
data=json.loads(Path(sys.argv[1]).read_bytes());trash=Path(data['trash'])
def move(path):
 destination=trash/path.name;path.rename(destination);return destination
w=s.RemovalStore(s.RemovalAuthority(**data['authority']),Path(data['root']),trash=move,trash_root=trash)
original=w.lease._connect
def connect():
 conn=original()
 if sys.argv[3]=='spill':conn.execute('PRAGMA cache_size=1');conn.execute('PRAGMA cache_spill=1')
 if sys.argv[3]=='wal':conn.execute('PRAGMA journal_mode=WAL')
 return conn
w.lease._connect=connect
if sys.argv[3]=='legacy':w._settle_xml=lambda folder,plan,desired,**kwargs:s.atomic_bytes(w.xml,desired)
def fault(phase):
 if phase==sys.argv[2]:os._exit(73)
s._fault=fault;p=data['review'];w.apply(p['folder'],p['sha256'],s.confirmation(p['plan']))
'''
        for index,(phase,mode) in enumerate((('journal','delete'),('tagged-0','delete'),('trashed-0','delete'),('resource-0','delete'),('xml','delete'),('sql','delete'),('commit','delete'),('sql','spill'),('sql','wal'),('commit','wal'),('xml-claim-recorded','delete'),('xml-original-claimed','delete'),('xml-replacement-published','delete'),('xml-retirement-claimed','delete'),('xml','legacy'),('resource-removal-claimed','delete'))):
            with self.subTest(phase=phase,mode=mode):
                area=self.root/str(index);area.mkdir();source,audio,resource,schema=fixture(area);trash=area/'trash';trash.mkdir()
                authority=replace(self.authority,database=str(source/'master.db'),schema_sha256=schema)
                w=store.RemovalStore(authority,area/'appdata',trash=fake_trash(trash),trash_root=trash)
                entry={**self.entry,'path':str(audio),'canonical':str(audio)};review=w.prepare([entry]);raw=audio.read_bytes()
                p=area/'child.json';p.write_text(json.dumps(dict(authority=asdict(authority),root=str(area/'appdata'),trash=str(trash),review=review)))
                r=subprocess.run([sys.executable,'-c',child,str(p),phase,mode],capture_output=True,text=True,timeout=60)
                self.assertEqual(r.returncode,73,r.stderr);result=w.recover();self.assertEqual(result['outcome'],'committed' if phase in ('commit','xml-retirement-claimed') else 'rolled-back')
                self.assertFalse(w.journal.exists());self.assertEqual((trash/audio.name if phase in ('commit','xml-retirement-claimed') else audio).read_bytes(),raw)
                self.assertEqual(resource.exists(),phase not in ('commit','xml-retirement-claimed'))
