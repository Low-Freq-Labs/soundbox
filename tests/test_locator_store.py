"""Existing-record relocation proofs on encrypted TEMP libraries only."""
from dataclasses import asdict,replace
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support.native_library import source_fixture
from tests.support.audio import wave
from soundbox import rekordbox_locator_store as locator,rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import _connect,_state,ConflictError,RecoveryRequired,WriteProofError


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

    def test_recovery_memo_keeps_fresh_hashes_and_revalidates_changed_intentions(self):
        record=self.writer.prepare(self.tracks);folder=self.writer.root/record['archive']
        # Restart has no successful preparation proof to reuse.
        self.writer=locator.LocatorStore(self.authority,self.root/'data')
        original=(folder/'operation.json').read_bytes()
        with (mock.patch.object(locator,'_state',wraps=locator._state) as inventory,
              mock.patch.object(self.writer,'_plan',wraps=self.writer._plan) as reconstruct,
              mock.patch.object(locator,'_sha',wraps=locator._sha) as hashes):
            self.writer._validate(record);self.writer._validate(deepcopy(record))
            self.assertEqual(inventory.call_count,2);self.assertEqual(reconstruct.call_count,1)
            self.assertEqual(hashes.call_count,8)
            with (mock.patch.object(cues,'_closed',side_effect=WriteProofError('native open')),
                  mock.patch.object(self.writer.lease,'_connect') as source_sql,self.assertRaises(WriteProofError)):
                self.writer.apply(record)
            source_sql.assert_not_called()
            changed=deepcopy(record);changed['timestamp']='2100-01-01 00:00:00.123 +00:00'
            (folder/'operation.json').write_text(json.dumps(changed))
            self.writer.journal.write_text(json.dumps(changed))
            with mock.patch.object(self.writer.lease,'_connect') as source_sql,self.assertRaises(RecoveryRequired):self.writer.recover()
            source_sql.assert_not_called();self.writer.journal.unlink()
            self.assertEqual(reconstruct.call_count,2)
            (folder/'operation.json').write_bytes(original)
            payload=folder/'after.db';before=payload.read_bytes();payload.write_bytes(before+b'unknown')
            count=inventory.call_count
            with self.assertRaises(RecoveryRequired):self.writer._validate(record)
            self.assertEqual(inventory.call_count,count);payload.write_bytes(before)
            self.writer.authority=replace(self.authority,authorization_sha256='1'*64)
            with self.assertRaises(RecoveryRequired):self.writer._validate(record)
            self.assertEqual(inventory.call_count,count)
            self.writer.authority=self.authority
            replacement=self.root/'replaced.db';replacement.write_bytes(self.writer.database.read_bytes())
            os.replace(replacement,self.writer.database)
            with self.assertRaises(RecoveryRequired):self.writer._validate(record)
            self.assertEqual(inventory.call_count,count)

    def test_prepared_pair_reuses_its_semantics_but_hashes_all_payloads_before_sql(self):
        record=self.writer.prepare(self.tracks);folder=self.writer.root/record['archive']
        with (mock.patch.object(locator,'_state',wraps=locator._state) as inventory,
              mock.patch.object(self.writer,'_plan',wraps=self.writer._plan) as reconstruct,
              mock.patch.object(locator,'_sha',wraps=locator._sha) as hashes):
            self.writer._validate(deepcopy(record));self.writer._validate(record)
            self.assertEqual(inventory.call_count,0);self.assertEqual(reconstruct.call_count,0)
            self.assertEqual(hashes.call_count,8)
            payload=folder/'after.db';stat=payload.stat();raw=payload.read_bytes()
            payload.write_bytes(raw[:-1]+bytes((raw[-1]^1,)))
            os.utime(payload,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with mock.patch.object(self.writer.lease,'_connect') as source_sql,self.assertRaises(RecoveryRequired):
                self.writer.apply(record)
            source_sql.assert_not_called();self.assertEqual(inventory.call_count,0)

    def test_unexpected_staged_target_cannot_seed_preparation_proof(self):
        before=self.snapshot();apply=locator._apply
        def unexpected_target(conn,changes):
            apply(conn,changes)
            conn.execute("UPDATE djmdContent SET Title='unexpected staging edit' WHERE ID='1'")
        with mock.patch.object(locator,'_apply',side_effect=unexpected_target),self.assertRaises(WriteProofError):
            self.writer.prepare(self.tracks)
        self.assertIsNone(self.writer._verified_recovery_record_key)
        self.assertEqual(self.snapshot(),before)
        self.assertFalse(any(self.writer.root.glob('media-locator-*/operation.json')))

    def test_changed_prepared_record_misses_seed_and_reconstructs_its_scope(self):
        record=self.writer.prepare(self.tracks);changed=deepcopy(record)
        changed['timestamp']='2100-01-01 00:00:00.123 +00:00'
        (self.writer.root/record['archive']/'operation.json').write_text(json.dumps(changed))
        with (mock.patch.object(locator,'_state',wraps=locator._state) as inventory,
              mock.patch.object(self.writer,'_plan',wraps=self.writer._plan) as reconstruct,
              mock.patch.object(self.writer.lease,'_connect') as source_sql,self.assertRaises(RecoveryRequired)):
            self.writer.apply(changed)
        self.assertGreater(inventory.call_count,0);reconstruct.assert_called_once();source_sql.assert_not_called()

    def test_corruption_during_archive_creation_cannot_seed_or_publish_a_prepared_pair(self):
        before=self.snapshot();copy=locator.copy_state;atomic=locator.atomic_bytes
        for name in locator.PAYLOADS:
            with self.subTest(payload=name):
                def corrupt(path):
                    if path.name==name:
                        with path.open('r+b') as handle:
                            first=handle.read(1);handle.seek(0);handle.write(bytes((first[0]^1,)))
                def corrupt_copy(src,dst):
                    copy(src,dst);corrupt(dst)
                def corrupt_atomic(path,payload):
                    atomic(path,payload);corrupt(path)
                with (mock.patch.object(locator,'copy_state',side_effect=corrupt_copy),
                      mock.patch.object(locator,'atomic_bytes',side_effect=corrupt_atomic),
                      mock.patch.object(self.writer.lease,'_connect') as source_sql,
                      self.assertRaisesRegex(WriteProofError,'archive differs')):
                    self.writer.prepare(self.tracks)
                source_sql.assert_not_called()
                self.assertIsNone(self.writer._verified_recovery_record_key)
                self.assertFalse(self.writer.journal.exists())
                self.assertFalse(any(self.writer.root.glob('media-locator-*/operation.json')))
                self.assertEqual(self.snapshot(),before)
