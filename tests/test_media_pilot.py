"""The multi-format source adapter runs only against synthetic encrypted TEMP DBs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.support.native_library import source_fixture
from test_media_probe import wave
from research.native_library import media_pilot as pilot
from research.cdj_export.playlist_transaction import _inventory,_sha
from soundbox import rekordbox_cue_store as cues,rekordbox_tags as tags
from soundbox.rekordbox_write import ConflictError,RecoveryRequired,WriteProofError
from soundbox.state_io import atomic_bytes


class MediaSourcePilot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source';source_fixture(self.source)
        self.audio=[];self.references={}
        for n,extensible in enumerate((False,True)):
            name=f'synthetic{n}.wav';path=self.root/(pilot.NAME_PREFIX+name);wave(path,extensible=extensible)
            self.references[name]=hashlib.sha256(path.read_bytes()).hexdigest();self.audio.append(path)
        for obj,name,value in ((pilot,'REFERENCES',self.references),(pilot,'_installed_version',lambda:tags.NATIVE_VERSION),
                (pilot,'assert_rekordbox_closed',lambda:None),(pilot,'_apps_closed',lambda:None),
                (cues,'_installed_version',lambda:tags.NATIVE_VERSION),(cues,'_closed',lambda:None),
                (tags,'_installed_version',lambda:tags.NATIVE_VERSION),(tags,'_assert_closed',lambda:None)):
            patch=mock.patch.object(obj,name,value);patch.start();self.addCleanup(patch.stop)
        self.folder=self.root/'plan';self.record=pilot.prepare(self.source/'master.db',self.audio,self.folder,data_root=self.root/'appdata')
        self.approval=self.root/'approval.json';self.approve()

    def approve(self,**overrides):
        body=dict(format=pilot.APPROVAL_FORMAT,approved=True,plan_sha256=_sha(self.folder/'plan.json'),
                  operation_id=self.record['operation_id'],source=self.record['source'])
        atomic_bytes(self.approval,json.dumps(body|overrides).encode())

    def store(self):return pilot.MediaRegistrationPilot(self.folder,self.approval)

    def test_one_batch_registers_both_shapes_once_preserving_every_audio_byte_and_xml(self):
        audio={p:p.read_bytes() for p in self.audio};store=self.store();xml=store.xml.read_bytes();stamp=store.xml.stat()
        ids=store.apply();self.assertEqual(len(ids),2);self.assertEqual(_inventory(store.database),self.record['after'])
        self.assertEqual([c['row']['Length'] for c in self.record['candidates']],[1,0])
        self.assertEqual(self.record['candidates'][-1]['counter_after']['int_1'],42)
        self.assertEqual(self.store().apply(),'already-completed');self.assertFalse(store.journal.exists())
        self.assertEqual({p:p.read_bytes() for p in self.audio},audio)
        self.assertEqual(store.xml.read_bytes(),xml);self.assertEqual(store.xml.stat().st_mtime_ns,stamp.st_mtime_ns)
        receipt=json.loads((store.archive/'result.json').read_text());self.assertFalse(receipt['native_acceptance'])
        self.assertEqual(receipt['format'],pilot.FORMAT)

    def test_old_approval_missing_grant_and_open_apps_never_open_source_sql(self):
        for body in (dict(format='soundbox-registration-pilot-approval-v1'),dict(plan_sha256='0'*64),dict(approved=False)):
            self.approve(**body)
            with self.assertRaises(WriteProofError):self.store()
        self.approve();store=self.store()
        with mock.patch.object(pilot,'_apps_closed',side_effect=WriteProofError('owner open')),mock.patch.object(store.lease,'_connect') as c:
            with self.assertRaises(WriteProofError):store.apply()
            c.assert_not_called()
        self.assertEqual(_inventory(store.database),self.record['before'])

    def test_changed_audio_or_tampered_memory_plan_cannot_publish_any_batch_member(self):
        store=self.store();store.plan['candidates'][0]['row']['Title']='tampered'
        with mock.patch.object(store.lease,'_connect') as c:
            with self.assertRaises(WriteProofError):store.apply()
            c.assert_not_called()
        store=self.store();p=self.audio[-1];os.utime(p,ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1))
        with mock.patch.object(store.lease,'_connect') as c:
            with self.assertRaises(ConflictError):store.apply()
            c.assert_not_called()
        self.assertEqual(_inventory(store.database),self.record['before'])

    def test_mid_batch_source_drift_rolls_back_the_entire_batch_and_keeps_recovery(self):
        store=self.store()
        def drift(phase):
            if phase=='sql-applied':
                p=self.audio[1];os.utime(p,ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1))
        with mock.patch.object(pilot,'_fault',side_effect=drift),self.assertRaises(ConflictError):store.apply()
        self.assertEqual(store.recover(),'rolled-back');self.assertEqual(_inventory(store.database),self.record['before'])

    def test_unknown_domain_or_corrupt_archive_blocks_source_sql_and_preserves_evidence(self):
        store=self.store();atomic_bytes(store.journal,b'{"format":"foreign"}')
        with mock.patch.object(store.lease,'_connect') as c:
            with self.assertRaises(RecoveryRequired):store.apply()
            c.assert_not_called()
        store.journal.unlink()
        def stop(phase):
            if phase=='sql-applied':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=stop),self.assertRaises(RuntimeError):store.apply()
        (store.archive/'after.db').write_bytes(b'damaged')
        with mock.patch.object(store.lease,'_connect') as c:
            with self.assertRaises(RecoveryRequired):store.recover()
            c.assert_not_called()
        self.assertTrue(store.journal.exists())

    def test_actual_exit_recovers_delete_wal_spill_and_commit_without_duplicate_rows(self):
        script='''import os,sys,json
from pathlib import Path
from research.native_library import media_pilot as p
from research.cdj_export import playlist_pilot as base
from soundbox import rekordbox_cue_store as cues
p.REFERENCES=json.loads(sys.argv[5]);p._apps_closed=lambda:None
cues._installed_version=lambda:'6.8.7.0303';cues._closed=lambda:None
store=p.MediaRegistrationPilot(Path(sys.argv[1]),Path(sys.argv[2]))
phase,mode=sys.argv[3:5];original=store.lease._connect
def connect():
 c=original()
 if mode=='wal':c.execute('PRAGMA journal_mode=WAL')
 if mode=='spill':c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=1')
 return c
store.lease._connect=connect
def fault(name):
 if name==phase:os._exit(73)
p._fault=fault;base._fault=fault
store.apply()
'''
        steps=[('payload-ready','delete'),('journal-ready','delete'),('sql-applied','delete'),
               ('sql-committed','delete'),('receipt-ready','delete'),('journal-retired','delete'),
               ('sql-applied','wal'),('sql-committed','wal'),('sql-applied','spill')]
        for i,(phase,mode) in enumerate(steps):
            with self.subTest(phase=phase,mode=mode):
                area=self.root/str(i);source=area/'source';source_fixture(source);folder=area/'plan'
                record=pilot.prepare(source/'master.db',self.audio,folder,data_root=area/'appdata')
                approval=area/'approval.json';atomic_bytes(approval,json.dumps(dict(format=pilot.APPROVAL_FORMAT,
                    approved=True,plan_sha256=_sha(folder/'plan.json'),operation_id=record['operation_id'],source=record['source'])).encode())
                child=subprocess.run([sys.executable,'-c',script,str(folder),str(approval),phase,mode,json.dumps(self.references)],
                                     capture_output=True,text=True,timeout=60)
                self.assertEqual(child.returncode,73,child.stderr)
                store=pilot.MediaRegistrationPilot(folder,approval)
                committed=phase in ('sql-committed','receipt-ready','journal-retired')
                self.assertEqual(store.recover(),'committed' if committed else None if phase=='payload-ready' else 'rolled-back')
                self.assertEqual(_inventory(store.database),record['after' if committed else 'before'])
                result=store.apply();self.assertEqual(result,'already-completed' if committed else [c['row']['ID'] for c in record['candidates']])
