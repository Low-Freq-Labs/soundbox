"""Existing-record relocation proofs on encrypted TEMP libraries only."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_registration_pilot import source_fixture
from test_registration_proof import wave
from soundbox import rekordbox_locator_store as locator,rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import _connect,_state,ConflictError,RecoveryRequired


class LocatorWrites(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.source=self.root/'source';source_fixture(self.source)
        self.original=self.root/'original.flac';self.original.write_bytes(b'verified original fixture')
        self.target=self.root/'song.wav';wave(self.target)
        conn=_connect(self.source/'master.db')
        try:
            conn.execute("UPDATE djmdContent SET UUID='uuid-one',FileType=5,FileNameL='song.flac',rb_local_synced=1,usn=NULL WHERE ID='1'")
            schema=tags._schema(conn)
        finally:conn.close()
        for obj,key in ((cues,'_closed'),(tags,'_assert_closed')):self.enterContext(mock.patch.object(obj,key,return_value=None))
        for obj in (cues,tags):self.enterContext(mock.patch.object(obj,'_installed_version',return_value=tags.NATIVE_VERSION))
        self.authority=locator.LocatorAuthority(str(self.source/'master.db'),'123',schema,'0'*64,('1',))
        self.writer=locator.LocatorStore(self.authority,self.root/'data')
        self.tracks=[dict(id='1',uuid='uuid-one',before_path='/never/open.wav',target=str(self.target),original=str(self.original),
            before_sha256=locator._sha(self.original),after_sha256=locator._sha(self.target))]

    def snapshot(self):
        conn=_connect(self.source/'master.db',readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def test_existing_track_is_updated_without_new_identity_or_musical_or_playlist_changes(self):
        before=self.snapshot();xml=self.writer.xml.read_bytes();raw=self.target.read_bytes()
        plan=self.writer.prepare(self.tracks);self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.writer.apply(plan),'committed');after=self.snapshot()
        self.assertEqual({k:v for k,v in before['tables'].items() if k not in ('djmdContent','agentRegistry')},
                         {k:v for k,v in after['tables'].items() if k not in ('djmdContent','agentRegistry')})
        conn=_connect(self.source/'master.db',readonly=True)
        try:
            row=locator.encoded_row(conn,'djmdContent','1');self.assertEqual(row['UUID'],'uuid-one')
            self.assertEqual((row['FolderPath'],row['FileNameL'],row['FileType'],row['rb_file_id']),
                             (str(self.target),'song.wav',11,str(self.target.stat().st_ino)))
        finally:conn.close()
        self.assertEqual(self.writer.xml.read_bytes(),xml);self.assertEqual(self.target.read_bytes(),raw)
        self.assertEqual(self.writer.apply(plan),'committed');self.assertFalse(self.writer.journal.exists())

    def test_changed_row_audio_or_authority_blocks_before_source_sql(self):
        plan=self.writer.prepare(self.tracks);before=self.snapshot();self.target.write_bytes(b'user replacement')
        with mock.patch.object(self.writer.lease,'_connect') as connection,self.assertRaises(ConflictError):self.writer.apply(plan)
        connection.assert_not_called();self.assertEqual(self.snapshot(),before)
        with self.assertRaises(Exception):locator.LocatorAuthority(str(self.source/'master.db'),'123',self.authority.schema_sha256,'0'*64)

    def test_changed_source_row_is_preserved_and_sql_cannot_touch_musical_fields(self):
        plan=self.writer.prepare(self.tracks);conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE djmdContent SET Title='new user title' WHERE ID='1'")
        finally:conn.close()
        before=self.snapshot()
        with self.assertRaises(ConflictError):self.writer.apply(plan)
        self.assertEqual(self.snapshot(),before)
        conn=_connect(self.source/'master.db')
        try:
            conn.set_authorizer(locator._authorizer)
            with self.assertRaises(Exception):conn.execute("UPDATE djmdContent SET BPM=999 WHERE ID='1'")
        finally:conn.close()

    def test_process_exit_before_and_after_sql_commit_recovers_exactly_including_wal(self):
        for phase,mode in (('journal','delete'),('sql','delete'),('committed','delete'),('receipt','delete'),('sql','wal'),('committed','wal')):
            with self.subTest(phase=phase,mode=mode):
                conn=_connect(self.source/'master.db')
                try:conn.execute("UPDATE djmdContent SET FolderPath='/never/open.wav',FileType=5,FileNameL='song.flac',rb_file_id=NULL WHERE ID='1'")
                finally:conn.close()
                plan=self.writer.prepare(self.tracks);before=self.snapshot()
                path=self.root/'child.json';path.write_text(json.dumps(dict(authority=asdict(self.authority),data=str(self.root/'data'),plan=plan,phase=phase,mode=mode)))
                code='''import json,os,sys
from pathlib import Path
from soundbox import rekordbox_locator_store as s,rekordbox_cue_store as c,rekordbox_tags as t
b=json.loads(Path(sys.argv[1]).read_bytes());c._closed=lambda:None;t._assert_closed=lambda:None
c._installed_version=t._installed_version=lambda:t.NATIVE_VERSION
a=b['authority'];a['track_ids']=tuple(a['track_ids']);w=s.LocatorStore(s.LocatorAuthority(**a),b['data'])
old=w.lease._connect
def connect():
 conn=old()
 if b['mode']=='wal':conn.execute('PRAGMA journal_mode=WAL')
 return conn
w.lease._connect=connect;s._fault=lambda p:os._exit(73) if p==b['phase'] else None
w.apply(b['plan'])
'''
                result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,73,result.stderr)
                committed=phase in ('committed','receipt')
                self.assertEqual(self.writer.recover(),'committed' if committed else 'rolled-back')
                self.assertEqual(self.snapshot(),plan['after'] if committed else before)
                self.assertFalse(self.writer.journal.exists())

    def test_unknown_journal_or_changed_backup_never_opens_source_connection(self):
        plan=self.writer.prepare(self.tracks);self.writer.journal.write_text('{"format":"unknown"}')
        with mock.patch.object(self.writer.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):self.writer.apply(plan)
        connection.assert_not_called();self.writer.journal.unlink()
        (self.writer.root/plan['archive']/'after.db').write_bytes(b'bad')
        with mock.patch.object(self.writer.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):self.writer.apply(plan)
        connection.assert_not_called()
