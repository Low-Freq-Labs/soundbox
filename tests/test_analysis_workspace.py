"""DB/directory recovery with synthetic encrypted TEMP metadata and packet bytes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_fixture_analysis as fixture_data
from test_registration_proof import fixture as database_fixture
from research.native_library import analysis_workspace as txn
from research.cdj_export.playlist_transaction import _inventory
from soundbox.rekordbox_write import _connect,_json,_quote,_state,ConflictError,RecoveryRequired
from soundbox.state_io import atomic_bytes


def fixture(root):
    root.mkdir(parents=True);database_fixture(root)
    data=fixture_data.InitialFixtureAnalysis();data.setUp();planned=data.plan()
    conn=_connect(root/'master.db')
    try:
        conn.execute('UPDATE djmdProperty SET DBID=?',('456',))
        conn.execute('UPDATE agentRegistry SET int_1=41 WHERE registry_id=\'localUpdateCount\'')
        cols=list(data.track)
        conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in cols)+') VALUES ('+','.join('?' for _ in cols)+')',list(data.track.values()))
        conn.execute('DROP TABLE contentFile')
        for table in ('contentFile','djmdMixerParam'):
            row=next(c['after'] for c in planned['changes'] if c['table']==table)
            columns=[_quote(k)+' '+('INTEGER' if type(v) is int else 'TEXT')+(' PRIMARY KEY' if k=='ID' else '') for k,v in row.items()]
            conn.execute('CREATE TABLE '+_quote(table)+' ('+','.join(columns)+')')
        conn.execute('CREATE TABLE djmdKey(ID TEXT PRIMARY KEY,ScaleName TEXT,rb_local_deleted INTEGER)')
        conn.execute("INSERT INTO djmdKey VALUES ('789','8B',0)")
        conn.execute('CREATE TABLE djmdCue(ID TEXT PRIMARY KEY,ContentID TEXT)')
        conn.execute('CREATE TABLE contentCue(ID TEXT PRIMARY KEY,ContentID TEXT)')
        (root/'state.json').write_bytes(_json(_state(conn)))
    finally:conn.close()
    return data


class AnalysisDirectoryTransaction(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.frozen=self.root/'frozen';self.data=fixture(self.frozen)
        self.ws=txn.OfflineAnalysisWorkspace.capture(self.frozen);self.addCleanup(shutil.rmtree,self.ws.root)
        self.plan=self.ws.plan('123',self.data.audio,self.data.images,share_root='/explicit/library/share',seeds=self.data.seeds)
        self.initial={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.frozen.iterdir()}

    def inputs_unchanged(self):
        self.assertEqual(self.initial,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.frozen.iterdir()})

    def test_atomic_directory_then_db_commit_and_idempotent_readback(self):
        self.assertEqual(self.ws.apply(),'committed')
        self.assertEqual(_inventory(self.ws.database),self.plan['after'])
        stage,target=self.ws._locations(self.plan)
        self.assertFalse(stage.exists());self.assertEqual({p.name for p in target.iterdir()},set(txn.NAMES))
        before={p.name:p.stat().st_ino for p in target.iterdir()};database=self.ws.database.read_bytes()
        self.assertEqual(self.ws.apply(),'already-committed')
        self.assertEqual(before,{p.name:p.stat().st_ino for p in target.iterdir()})
        self.assertEqual(self.ws.database.read_bytes(),database)
        self.assertFalse(self.ws.journal.exists());self.inputs_unchanged()

    def test_occupied_target_and_xml_change_never_publish(self):
        stage,target=self.ws._locations(self.plan)
        missing=[];parent=target
        while not parent.exists():missing.append(parent);parent=parent.parent
        for parent in reversed(missing):parent.mkdir(mode=0o700)
        (target/'unowned').write_bytes(b'keep')
        with self.assertRaises(ConflictError):self.ws.apply()
        self.assertEqual((target/'unowned').read_bytes(),b'keep')
        self.assertFalse(self.ws.journal.exists())
        shutil.rmtree(target)
        (self.ws.root/'masterPlaylists6.xml').write_bytes(b'external XML')
        with self.assertRaises(RecoveryRequired):self.ws.apply()
        self.inputs_unchanged()

    def test_unknown_db_or_replaced_published_file_blocks_cleanup(self):
        def fail(phase):
            if phase=='files-published':raise RuntimeError('interrupted')
        with mock.patch.object(txn,'_checkpoint',side_effect=fail),self.assertRaises(RuntimeError):self.ws.apply()
        stage,target=self.ws._locations(self.plan)
        payload=target/'ANLZ0000.DAT';saved=payload.read_bytes();payload.write_bytes(saved+b'foreign')
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertTrue(self.ws.journal.exists());self.assertEqual(payload.read_bytes(),saved+b'foreign')
        payload.write_bytes(saved)
        conn=_connect(self.ws.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'44'")
        finally:conn.close()
        changed=_inventory(self.ws.database)
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertEqual(_inventory(self.ws.database),changed);self.assertTrue(target.exists())
        self.inputs_unchanged()

    def test_corrupt_backup_or_plan_identity_never_opens_recovery(self):
        def fail(phase):
            if phase=='journal-ready':raise RuntimeError('interrupted')
        with mock.patch.object(txn,'_checkpoint',side_effect=fail),self.assertRaises(RuntimeError):self.ws.apply()
        after=self.ws.root/'after.db';saved=after.read_bytes();after.write_bytes(saved[:-1]+bytes([saved[-1]^1]))
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertTrue(self.ws.journal.exists());after.write_bytes(saved)
        record=json.loads(self.ws.journal.read_bytes());record['directory_id']=[0,0]
        atomic_bytes(self.ws.journal,_json(record))
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.inputs_unchanged()

    def test_sql_trigger_cannot_modify_unrelated_data(self):
        # Re-capture an explicit synthetic input with the trigger in its verified
        # baseline. The SQL authorizer must still reject its side effect.
        original=self.root/'trigger';data=fixture(original)
        conn=_connect(original/'master.db')
        try:
            conn.execute("CREATE TRIGGER unrelated AFTER UPDATE OF BPM ON djmdContent BEGIN UPDATE unknown_table SET opaque=X'55'; END")
            (original/'state.json').write_bytes(_json(_state(conn)))
        finally:conn.close()
        work=txn.OfflineAnalysisWorkspace.capture(original);self.addCleanup(shutil.rmtree,work.root)
        before=_inventory(work.database)
        with self.assertRaises(Exception) as error:work.plan('123',data.audio,data.images,share_root='/explicit/library/share',seeds=data.seeds)
        self.assertIn('not authorized',str(error.exception));self.assertEqual(_inventory(work.database),before)

    def test_real_process_exit_across_directory_and_sql_boundaries(self):
        script='''
import os,sys,json
from pathlib import Path
from research.native_library import analysis_workspace as m
w=m.OfflineAnalysisWorkspace(Path(sys.argv[1]))
original=m._connect
mode=sys.argv[3]
def connect(*args,**kwargs):
 c=original(*args,**kwargs)
 if Path(args[0])==w.database and not kwargs.get('readonly',False):
  if mode=='wal':c.execute('PRAGMA journal_mode=WAL')
  if mode=='spill':c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=1')
 return c
m._connect=connect
def crash(phase):
 if phase==sys.argv[2]:os._exit(73)
m._checkpoint=crash
w.apply()
'''
        phases=['journal-ready','stage-created',*(f'file-{name}' for name in txn.NAMES),'files-published','sql-applied','sql-committed','receipt-ready','journal-retired']
        for i,(phase,mode) in enumerate([(p,'delete') for p in phases]+[('sql-applied','wal'),('sql-committed','wal'),('sql-applied','spill')]):
            with self.subTest(phase=phase,mode=mode):
                frozen=self.root/str(i);data=fixture(frozen)
                work=txn.OfflineAnalysisWorkspace.capture(frozen);self.addCleanup(shutil.rmtree,work.root)
                plan=work.plan('123',data.audio,data.images,share_root='/explicit/library/share',seeds=data.seeds)
                result=subprocess.run([sys.executable,'-c',script,str(work.root),phase,mode],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                work=txn.OfflineAnalysisWorkspace(work.root)
                committed=phase in ('sql-committed','receipt-ready','journal-retired')
                outcome=work.recover()
                self.assertEqual(outcome,None if phase=='journal-retired' else 'committed' if committed else 'rolled-back')
                self.assertEqual(_inventory(work.database),plan['after'] if committed else work.metadata['before'])
                stage,target=work._locations(plan)
                self.assertFalse(stage.exists());self.assertEqual(target.exists(),committed)
                self.assertEqual(work.apply(),'already-committed' if committed else 'committed')
        self.inputs_unchanged()


if __name__=='__main__':unittest.main()
