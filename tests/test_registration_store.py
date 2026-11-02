"""Ordinary registration transport is tested only on synthetic encrypted TEMP DBs."""
from dataclasses import asdict,replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from test_registration_pilot import source_fixture
from test_registration_proof import wave
from soundbox import native_media as media,native_registration_plan as plan,rekordbox_registration_store as store
from soundbox import rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.native_paths import capture_aliases
from soundbox.rekordbox_write import _connect,_state,ConflictError,RecoveryRequired,WriteProofError
from research.cdj_export.playlist_transaction import _inventory


class RegistrationStore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.source=self.root/'source';source_fixture(self.source)
        self.audio=self.root/'new.wav';wave(self.audio)
        for obj,name,value in ((cues,'_installed_version',lambda:tags.NATIVE_VERSION),(cues,'_closed',lambda:None),
                (tags,'_installed_version',lambda:tags.NATIVE_VERSION),(tags,'_assert_closed',lambda:None)):
            p=mock.patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        conn=_connect(self.source/'master.db')
        try:schema=tags._schema(conn)
        finally:conn.close()
        self.authority=store.RegistrationAuthority(str(self.source/'master.db'),'123',schema,'0'*64,True)
        self.writer=store.RegistrationStore(self.authority,self.root/'recovery')
        self.facts=media.capture_media(self.audio,self.root/'scratch');self.request=self.make_request()

    def make_request(self,*,facts=None,local='sb-original',identity='2'):
        return dict(format=plan.REQUEST_FORMAT,operation_id=str(uuid4()),source=str(self.source/'master.db'),
            library_id='123',schema_sha256=self.authority.schema_sha256,
            tracks=[plan.make_claim(local,facts or self.facts,native_id=identity,native_uuid=str(uuid4()),
                timestamp='2026-10-31 04:00:00.123 +00:00',day='2026-10-31')])

    def test_corrupt_retention_cannot_delete_the_latest_pair_or_reach_source_sql(self):
        self.writer.apply(self.request)
        pointer=self.writer.root/'registration-latest.json'
        value=json.loads(pointer.read_bytes());value['retiring']=value['current']
        pointer.write_text(json.dumps(value));before=_inventory(self.writer.database)
        archive=self.writer.root/value['current']['archive']
        hashes={name:store._sha(archive/name) for name in ('before.db','after.db')}
        with mock.patch.object(self.writer.lease,'_connect',side_effect=AssertionError('No source SQL before retention validation')):
            with self.assertRaises(RecoveryRequired):self.writer.apply(self.make_request())
        self.assertEqual(_inventory(self.writer.database),before)
        self.assertEqual({name:store._sha(archive/name) for name in hashes},hashes)

    def test_separate_explicit_authority_and_domain_format_are_mandatory(self):
        with self.assertRaises(WriteProofError):replace(self.authority,accepted=False)
        with self.assertRaises(WriteProofError):replace(self.authority,file_types=(19,))
        path=self.root/'authority.json';path.write_text(json.dumps(asdict(self.authority)))
        self.assertIsNone(store.load_authority(path,self.source/'master.db','123'))
        path.write_text(json.dumps(dict(format=store.AUTHORITY_FORMAT,**asdict(self.authority))))
        self.assertEqual(store.load_authority(path,self.source/'master.db','123'),self.authority)

    def test_real_registration_commit_replay_and_later_native_metadata_preserve_the_song(self):
        audio=self.audio.read_bytes();xml=self.writer.xml.read_bytes();before=_inventory(self.writer.database)
        bindings=self.writer.apply(self.request)
        self.assertEqual(bindings[0]['local_id'],'sb-original');self.assertEqual(bindings[0]['native_id'],'2')
        self.assertTrue(bindings[0]['registered']);self.assertFalse(self.writer.journal.exists())
        after=_inventory(self.writer.database)
        self.assertEqual([t for t in before['tables'] if before['tables'][t]!=after['tables'][t]],['agentRegistry','djmdContent'])
        self.assertEqual(self.writer.apply(self.request),bindings)
        conn=_connect(self.writer.database)
        try:conn.execute("UPDATE djmdContent SET Title='Native edit',BPM=12300 WHERE ID='2'")
        finally:conn.close()
        edited=_inventory(self.writer.database);self.assertEqual(self.writer.apply(self.request),bindings)
        self.assertEqual(_inventory(self.writer.database),edited)
        self.assertEqual(self.audio.read_bytes(),audio);self.assertEqual(self.writer.xml.read_bytes(),xml)

    def test_native_removal_after_commit_never_resurrects_a_song(self):
        self.writer.apply(self.request);conn=_connect(self.writer.database)
        try:conn.execute("DELETE FROM djmdContent WHERE ID='2'")
        finally:conn.close()
        current=_inventory(self.writer.database)
        with self.assertRaises(ConflictError):self.writer.apply(self.request)
        self.assertEqual(_inventory(self.writer.database),current)

    def test_alias_reuse_is_read_only_and_rechecks_the_alias_before_source_sql(self):
        alias=self.root/'legacy';alias.symlink_to(self.root,target_is_directory=True)
        raw=str(alias/self.audio.name)
        conn=_connect(self.writer.database)
        try:
            conn.execute("UPDATE djmdContent SET FolderPath=?,UUID=?,MasterDBID='123',FileType=11 WHERE ID='1'",(raw,str(uuid4())))
            cursor=conn.execute('SELECT * FROM djmdContent');names=[c[0] for c in cursor.description]
            rows=[dict(zip(names,r,strict=True)) for r in cursor.fetchall()]
        finally:conn.close()
        request=self.make_request();request['tracks'][0]['aliases']=capture_aliases(rows,self.facts)
        before=_inventory(self.writer.database);bindings=self.writer.apply(request)
        self.assertEqual(bindings[0]['native_id'],'1');self.assertEqual(bindings[0]['native_path'],raw)
        self.assertEqual(_inventory(self.writer.database),before)
        alias.unlink();alias.symlink_to(self.source,target_is_directory=True)
        with mock.patch.object(self.writer.lease,'_connect') as c:
            with self.assertRaises((ConflictError,OSError)):self.writer.apply(request)
            c.assert_not_called()

    def test_native_app_open_and_audio_drift_block_before_source_sql(self):
        with mock.patch.object(cues,'_closed',side_effect=WriteProofError('native open')),mock.patch.object(self.writer.lease,'_connect') as c:
            with self.assertRaises(WriteProofError):self.writer.apply(self.request)
            c.assert_not_called()
        os.utime(self.audio,ns=(self.audio.stat().st_atime_ns,self.audio.stat().st_mtime_ns+1))
        with mock.patch.object(self.writer.lease,'_connect') as c:
            with self.assertRaises(ConflictError):self.writer.apply(self.request)
            c.assert_not_called()

    def test_mid_write_audio_drift_rolls_back_without_full_database_restore(self):
        initial=_inventory(self.writer.database);inode=self.writer.database.stat().st_ino
        def drift(phase):
            if phase=='sql-applied':os.utime(self.audio,ns=(self.audio.stat().st_atime_ns,self.audio.stat().st_mtime_ns+1))
        with mock.patch.object(store,'_fault',side_effect=drift),self.assertRaises(ConflictError):self.writer.apply(self.request)
        self.assertEqual(self.writer.recover(),'rolled-back');self.assertEqual(_inventory(self.writer.database),initial)
        self.assertEqual(self.writer.database.stat().st_ino,inode)

    def test_unknown_shared_domain_or_changed_payload_preserves_recovery_and_blocks_sql(self):
        self.writer.journal.write_text('{"format":"foreign"}')
        with mock.patch.object(self.writer.lease,'_connect') as c:
            with self.assertRaises(RecoveryRequired):self.writer.apply(self.request)
            c.assert_not_called()
        self.writer.journal.unlink()
        def fail(phase):
            if phase=='sql-applied':raise RuntimeError('interrupted')
        with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):self.writer.apply(self.request)
        archive=self.writer.root/('registration-'+self.request['operation_id']);(archive/'after.db').write_bytes(b'bad')
        with mock.patch.object(self.writer.lease,'_connect') as c:
            with self.assertRaises(RecoveryRequired):self.writer.recover()
            c.assert_not_called()
        self.assertTrue(self.writer.journal.exists())

    def test_bounded_retention_keeps_latest_pair_and_all_operation_receipts(self):
        self.writer.apply(self.request);first=self.writer.root/('registration-'+self.request['operation_id'])
        other=self.root/'second.wav';wave(other);facts=media.capture_media(other,self.root/'scratch')
        request=self.make_request(facts=facts,local='sb-second',identity='3');self.writer.apply(request)
        second=self.writer.root/('registration-'+request['operation_id'])
        self.assertFalse((first/'before.db').exists());self.assertFalse((first/'after.db').exists())
        for name in ('operation.json','result.json','before.xml','after.xml','retired.json'):self.assertTrue((first/name).exists())
        self.assertTrue((second/'before.db').exists());self.assertTrue((second/'after.db').exists())
        self.assertEqual(self.writer.apply(self.request)[0]['native_id'],'2')
        self.assertTrue((second/'before.db').exists())

    def test_registration_holds_the_existing_tag_lane_and_pending_domain_blocks_its_sql(self):
        tag=tags.LiveTagStore(tags.TagWriteAuthority(str(self.writer.database),'123',self.authority.schema_sha256),self.writer.root)
        with self.writer.lease._lock(),self.assertRaises(WriteProofError):tag.check_ready()
        def fail(phase):
            if phase=='sql-applied':raise RuntimeError('interrupted')
        with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):self.writer.apply(self.request)
        with mock.patch.object(tag,'_connect') as c:
            with self.assertRaises(RecoveryRequired):tag.check_ready()
            c.assert_not_called()

    def test_native_acceptance_pairs_are_excluded_from_ordinary_payload_retirement(self):
        writer=store.RegistrationStore(replace(self.authority,retain_full_backups=True),self.writer.root)
        writer.apply(self.request);archive=writer.root/('registration-'+self.request['operation_id'])
        self.assertTrue((archive/'before.db').exists());self.assertTrue((archive/'after.db').exists())
        self.assertFalse((writer.root/'registration-latest.json').exists())

    def test_actual_process_exit_recovers_journal_wal_spill_and_receipt_boundaries(self):
        child='''import os,json,sys
from pathlib import Path
from soundbox import rekordbox_registration_store as s,rekordbox_cue_store as c
c._closed=lambda:None;c._installed_version=lambda:'6.8.7.0303'
a=json.loads(Path(sys.argv[1]).read_text());a['file_types']=tuple(a['file_types'])
w=s.RegistrationStore(s.RegistrationAuthority(**a),Path(sys.argv[2]));mode=sys.argv[4]
original=w.lease._connect
def connect():
 conn=original()
 if mode=='wal':conn.execute('PRAGMA journal_mode=WAL')
 if mode=='spill':conn.execute('PRAGMA cache_size=1');conn.execute('PRAGMA cache_spill=1')
 return conn
w.lease._connect=connect
def fault(phase):
 if phase==sys.argv[3]:os._exit(73)
s._fault=fault
w.apply(json.loads(Path(sys.argv[5]).read_text()))
'''
        phases=[('payload-ready','delete'),('journal-ready','delete'),('sql-applied','delete'),('sql-committed','delete'),
                ('receipt-ready','delete'),('journal-retired','delete'),('sql-applied','wal'),('sql-committed','wal'),('sql-applied','spill')]
        for i,(phase,mode) in enumerate(phases):
            with self.subTest(phase=phase,mode=mode):
                area=self.root/str(i);source=area/'source';source_fixture(source)
                conn=_connect(source/'master.db')
                try:schema=tags._schema(conn);initial=_state(conn)
                finally:conn.close()
                authority=replace(self.authority,database=str(source/'master.db'),schema_sha256=schema)
                req={**self.request,'operation_id':str(uuid4()),'source':str(source/'master.db'),'schema_sha256':schema}
                (area/'authority.json').write_text(json.dumps(asdict(authority)));(area/'request.json').write_text(json.dumps(req))
                r=subprocess.run([sys.executable,'-c',child,str(area/'authority.json'),str(area/'recovery'),phase,mode,str(area/'request.json')],
                                 capture_output=True,text=True,timeout=60)
                self.assertEqual(r.returncode,73,r.stderr)
                w=store.RegistrationStore(authority,area/'recovery');committed=phase in ('sql-committed','receipt-ready','journal-retired')
                expected=None if phase in ('payload-ready','journal-retired') else 'committed' if committed else 'rolled-back'
                self.assertEqual(w.recover(),expected)
                if not committed:self.assertEqual(_inventory(w.database),initial)
                if not committed and phase!='payload-ready':req={**req,'operation_id':str(uuid4())}
                self.assertEqual(w.apply(req)[0]['native_id'],'2');self.assertFalse(w.journal.exists())
