"""One-intent source adapter exercised only on synthetic encrypted TEMP sources."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support.native_library import fixture, source_fixture
from tests.support.audio import wave
from research.native_library import registration_pilot as pilot
from research.cdj_export.playlist_transaction import _inventory, _sha
from soundbox import rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.rekordbox_write import _connect, ConflictError, RecoveryRequired, WriteProofError
from soundbox.state_io import atomic_bytes


__all__ = ['fixture', 'source_fixture']


class RegistrationSourcePilot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source';source_fixture(self.source)
        self.audio=self.root/pilot.REFERENCE_NAME;wave(self.audio,seconds=32)
        self.sha=hashlib.sha256(self.audio.read_bytes()).hexdigest()
        for obj,name,value in ((pilot,'REFERENCE_SHA256',self.sha),
                               (pilot,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (cues,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (tags,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (tags,'_assert_closed',lambda:None),
                               (cues,'_closed',lambda:None),
                               (pilot,'assert_rekordbox_closed',lambda:None),
                               (pilot,'_apps_closed',lambda:None)):
            patch=mock.patch.object(obj,name,value);patch.start();self.addCleanup(patch.stop)
        self.folder=self.root/'plan'
        self.record=pilot.prepare(self.source/'master.db',self.audio,self.folder,data_root=self.root/'appdata')
        self.approval=self.root/'approval.json';self.approve()
        self.audio_epoch=self.audio.stat()

    def approve(self,**overrides):
        body=dict(format=pilot.APPROVAL_FORMAT,approved=True,plan_sha256=_sha(self.folder/'plan.json'),
                  operation_id=self.record['operation_id'],source=str(self.source/'master.db'))
        atomic_bytes(self.approval,json.dumps(body|overrides).encode())

    def store(self):return pilot.RegistrationPilot(self.folder,self.approval)

    def test_exact_approved_registration_preserves_xml_audio_and_all_unrelated_data(self):
        store=self.store();xml=store.xml.read_bytes();xml_epoch=store.xml.stat()
        self.assertEqual(store.apply(),self.record['native_id'])
        self.assertEqual(_inventory(store.database),self.record['after'])
        self.assertEqual(store.xml.read_bytes(),xml)
        self.assertEqual(store.xml.stat().st_ino,xml_epoch.st_ino)
        self.assertEqual(store.xml.stat().st_mtime_ns,xml_epoch.st_mtime_ns)
        self.assertEqual(self.audio.stat().st_ino,self.audio_epoch.st_ino)
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(),self.sha)
        self.assertEqual(self.store().apply(),'already-completed')
        self.assertFalse(store.journal.exists())
        receipt=json.loads((store.archive/'result.json').read_text())
        self.assertFalse(receipt['native_acceptance'])
        self.assertEqual(receipt['format'],pilot.FORMAT)

    def test_absent_or_foreign_approval_and_running_apps_cannot_open_source_sql(self):
        before=(self.source/'master.db').read_bytes()
        self.approve(plan_sha256='0'*64)
        with self.assertRaises(WriteProofError):self.store()
        self.approval.unlink()
        with self.assertRaises(OSError):self.store()
        self.approve();store=self.store()
        with mock.patch.object(pilot,'_apps_closed',side_effect=WriteProofError('owner open')),mock.patch.object(store.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):store.apply()
            connection.assert_not_called()
        self.assertEqual((self.source/'master.db').read_bytes(),before)

    def test_only_the_generated_reference_is_in_scope(self):
        audio=self.audio.read_bytes();self.audio.write_bytes(audio[:-1]+b'\1')
        with self.assertRaises(WriteProofError):
            pilot.prepare(self.source/'master.db',self.audio,self.root/'rejected',data_root=self.root/'appdata')
        self.assertEqual(_inventory(self.source/'master.db'),self.record['before'])

    def test_unknown_source_changes_or_changed_audio_cannot_publish(self):
        store=self.store();conn=_connect(store.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'66'")
        finally:conn.close()
        changed=_inventory(store.database)
        with self.assertRaises(ConflictError):store.apply()
        self.assertEqual(_inventory(store.database),changed)
        self.assertFalse(store.journal.exists())

    def test_source_changes_after_journal_roll_back_and_reconcile(self):
        store=self.store()
        def change(phase):
            if phase=='sql-applied':os.utime(self.audio,ns=(self.audio.stat().st_atime_ns,self.audio.stat().st_mtime_ns+1))
        with mock.patch.object(pilot,'_fault',side_effect=change),self.assertRaises(ConflictError):store.apply()
        self.assertEqual(store.recover(),'rolled-back')
        self.assertEqual(_inventory(store.database),self.record['before'])
        self.assertFalse(store.journal.exists())

    def test_other_domain_or_grid_recovery_blocks_before_source_sql(self):
        store=self.store();atomic_bytes(store.journal,b'{"format":"other-domain"}')
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.apply()
        connection.assert_not_called()
        store.journal.unlink();(store.root/'grid-transaction').mkdir(mode=0o700)
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.apply()
        connection.assert_not_called()

    def test_pending_registration_blocks_existing_writers_and_corruption_preserves_source(self):
        store=self.store()
        def fail(phase):
            if phase=='sql-applied':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=fail),self.assertRaises(RuntimeError):store.apply()
        (store.archive/'after.db').write_bytes(b'damaged')
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.recover()
        connection.assert_not_called()
        self.assertTrue(store.journal.exists())
        self.assertEqual(_inventory(store.database),self.record['before'])

    def test_shared_owner_blocks_existing_tag_writer_and_unknown_xml_is_preserved(self):
        store=self.store()
        old=tags.LiveTagStore(tags.TagWriteAuthority(str(store.database),self.record['library_id'],
                              self.record['schema_sha256']),store.root)
        with store.lease._lock(),self.assertRaises(WriteProofError):old.check_ready()
        def fail(phase):
            if phase=='sql-applied':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=fail),self.assertRaises(RuntimeError):store.apply()
        store.xml.write_bytes(b'external native XML')
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(RecoveryRequired):store.recover()
        connection.assert_not_called()
        self.assertEqual(store.xml.read_bytes(),b'external native XML')
        self.assertTrue(store.journal.exists())

    def test_memory_plan_tampering_is_rejected_before_source_connection(self):
        store=self.store();store.plan['native_id']='17'
        with mock.patch.object(store.lease,'_connect') as connection,self.assertRaises(WriteProofError):store.apply()
        connection.assert_not_called()

    def test_real_exit_recovers_journal_commit_receipt_wal_and_spill(self):
        child='''
import os,sys,json
from pathlib import Path
from research.native_library import registration_pilot as p
from research.cdj_export import playlist_pilot as base
from soundbox import rekordbox_cue_store as cues
p.REFERENCE_SHA256=sys.argv[5]
cues._installed_version=lambda:'6.8.7.0303'
cues._closed=lambda:None
p._apps_closed=lambda:None
store=p.RegistrationPilot(Path(sys.argv[1]),Path(sys.argv[2]))
phase,mode=sys.argv[3:5]
original=store.lease._connect
def connect():
 c=original()
 if mode=='wal':c.execute('PRAGMA journal_mode=WAL')
 if mode=='spill':c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=1')
 return c
store.lease._connect=connect
def fault(name):
 if name==phase:
  if mode=='spill':
   journal=store.database.with_name('master.db-journal')
   (store.folder/'spill.json').write_text(json.dumps({'nonzero_header':any(journal.read_bytes()[:8])}))
  os._exit(73)
p._fault=fault;base._fault=fault
store.apply()
'''
        for i,(phase,mode) in enumerate((('payload-ready','delete'),('journal-ready','delete'),('sql-applied','delete'),
                                         ('sql-committed','delete'),('receipt-ready','delete'),('journal-retired','delete'),
                                         ('sql-applied','wal'),('sql-committed','wal'),('sql-applied','spill'))):
            with self.subTest(phase=phase,mode=mode):
                area=self.root/str(i);source=area/'source';source_fixture(source)
                folder=area/'plan';record=pilot.prepare(source/'master.db',self.audio,folder,data_root=area/'appdata')
                approval=area/'approval.json'
                atomic_bytes(approval,json.dumps(dict(format=pilot.APPROVAL_FORMAT,approved=True,
                    plan_sha256=_sha(folder/'plan.json'),operation_id=record['operation_id'],source=record['source'])).encode())
                result=subprocess.run([sys.executable,'-c',child,str(folder),str(approval),phase,mode,self.sha],
                                      capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                store=pilot.RegistrationPilot(folder,approval)
                committed=phase in ('sql-committed','receipt-ready','journal-retired')
                expected='committed' if committed else None if phase=='payload-ready' else 'rolled-back'
                self.assertEqual(store.recover(),expected)
                self.assertEqual(_inventory(store.database),record['after' if committed else 'before'])
                self.assertFalse(store.journal.exists())
                if mode=='spill':self.assertTrue(json.loads((folder/'spill.json').read_text())['nonzero_header'])
                self.assertEqual(store.apply(),'already-completed' if committed else record['native_id'])


if __name__=='__main__':unittest.main()
