"""Synthetic TEMP DB/XML only; real subprocess exits exercise recovery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox.rekordbox_write import _connect, _rows, _state, ConflictError, RecoveryRequired, WriteProofError
from soundbox.state_io import atomic_bytes
from research.cdj_export import playlist_lifecycle as lifecycle, playlist_xml as xml
from research.cdj_export import playlist_transaction as txn

_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<MASTER_PLAYLIST Version="3.0.0" AutomaticSync="0">
  <PRODUCT Name="rekordbox" Version="6.8.4" Company="Pioneer DJ"/>
  <!-- preserve this unknown note -->
  <PLAYLISTS>
    <NODE Id="3E8" ParentId="0" Attribute="0" Timestamp="123000" Lib_Type="0" CheckType="0" Opaque="keep"/>
    <NODE Id="A5E632758597262C" ParentId="0" Attribute="0" Timestamp="0" Lib_Type="1" CheckType="0"/>
  </PLAYLISTS>
</MASTER_PLAYLIST>
'''


def fixture(root):
    database = root/'master.db'
    conn = _connect(database, create=True)
    try:
        conn.executescript('''
            CREATE TABLE djmdProperty(DBID TEXT PRIMARY KEY, DBVersion TEXT);
            INSERT INTO djmdProperty VALUES ('synthetic-playlists','6000');
            CREATE TABLE djmdPlaylist(ID TEXT PRIMARY KEY,Seq INTEGER,Name TEXT,
                ImagePath TEXT,Attribute INTEGER,ParentID TEXT,SmartList TEXT,UUID TEXT,
                rb_data_status INTEGER,rb_local_data_status INTEGER,rb_local_deleted INTEGER,
                rb_local_synced INTEGER,usn INTEGER,rb_local_usn INTEGER,created_at TEXT,updated_at TEXT);
            INSERT INTO djmdPlaylist VALUES ('1000',1,'Existing',NULL,0,'root',NULL,
                '00000000-0000-4000-8000-000000000001',0,0,0,0,NULL,20,'created','created');
            CREATE TABLE djmdSongPlaylist(ID TEXT PRIMARY KEY,PlaylistID TEXT,ContentID TEXT,
                TrackNo INTEGER,UUID TEXT,rb_data_status INTEGER,rb_local_data_status INTEGER,
                rb_local_deleted INTEGER,rb_local_synced INTEGER,usn INTEGER,rb_local_usn INTEGER,
                created_at TEXT,updated_at TEXT);
            CREATE TABLE agentRegistry(registry_id TEXT PRIMARY KEY,int_1 INTEGER,updated_at TEXT);
            INSERT INTO agentRegistry VALUES ('localUpdateCount',40,'counter-clock');
            INSERT INTO agentRegistry VALUES ('notificationNextFetch',77,'opaque-clock');
            CREATE TABLE djmdContent(ID TEXT PRIMARY KEY,UUID TEXT,Title TEXT,FolderPath TEXT,
                rb_local_deleted INTEGER,rb_data_status INTEGER,rb_local_data_status INTEGER,
                rb_local_synced INTEGER,usn INTEGER);
            INSERT INTO djmdContent VALUES ('track','uuid','Keep','/must-never-open/audio.wav',0,0,0,0,NULL);
            INSERT INTO djmdContent VALUES ('track-two','uuid-two','Keep two','/must-never-open/other.wav',0,0,0,0,NULL);
            CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER);
            CREATE TABLE djmdSongMyTag(ID TEXT PRIMARY KEY,MyTagID TEXT,ContentID TEXT,TrackNo INTEGER,
                UUID TEXT,rb_data_status INTEGER,rb_local_data_status INTEGER,rb_local_deleted INTEGER,
                rb_local_synced INTEGER,usn INTEGER,rb_local_usn INTEGER,created_at TEXT,updated_at TEXT);
            CREATE TABLE opaque_table(ID TEXT PRIMARY KEY,data BLOB);
            INSERT INTO opaque_table VALUES ('opaque',X'0011ff');
        ''')
        state = _state(conn)
    finally:conn.close()
    (root/'state.json').write_text(json.dumps(state))
    (root/'masterPlaylists6.xml').write_bytes(_XML)


def planned(workspace, *, rename=False, huge=False):
    conn = _connect(workspace.database, readonly=True)
    try:
        state = _state(conn);playlists = _rows(conn,'djmdPlaylist')
        counter = next(r for r in _rows(conn,'agentRegistry') if r['registry_id']=='localUpdateCount')
    finally:conn.close()
    before_xml = workspace.xml.read_bytes()
    if rename:
        target = next(r for r in playlists if r['ID']=='1000')
        plan = lifecycle.rename(target,counter,name='Renamed'+('x'*2_000_000 if huge else ''),timestamp='updated')
        after_xml = xml.touch(before_xml,'1000',123001)
    else:
        plan = lifecycle.create(playlists,counter,identity='2000',
            uuid='00000000-0000-4000-8000-000000000002',name='Untitled Playlist',timestamp='new')
        after_xml = xml.append_root(before_xml,'2000')
    return plan['changes'],after_xml,dict(expected_before=state,
        expected_xml_sha256=hashlib.sha256(before_xml).hexdigest())


class NativePlaylistXml(unittest.TestCase):
    def test_only_selected_timestamp_changes_and_creation_preserves_opaque_64bit_node(self):
        changed = xml.touch(_XML,'1000',123001)
        self.assertEqual(changed,_XML.replace(b'Timestamp="123000"',b'Timestamp="123001"'))
        created = xml.append_root(_XML,'2000')
        self.assertIn(b'Id="A5E632758597262C"',created)
        self.assertEqual(xml.identities(created),xml.identities(_XML)|{2000})
        self.assertIs(xml.retain_deleted(_XML,'1000'),_XML)

    def test_historical_collision_sync_states_duplicate_aliases_and_entities_reject(self):
        for payload in (_XML.replace(b'AutomaticSync="0"',b'AutomaticSync="1"'),
                        _XML.replace(b'</PLAYLISTS>',b'<NODE Id="3e8"/></PLAYLISTS>'),
                        b'<!DOCTYPE x [<!ENTITY y "z">]>'+_XML):
            with self.assertRaises(ValueError):xml.append_root(payload,'2000')
        with self.assertRaises(ValueError):xml.append_root(_XML,'1000')
        with self.assertRaises(ValueError):xml.touch(_XML,'1000',122999)
        with self.assertRaises(ValueError):xml.touch(_XML,str(int('A5E632758597262C',16)),1)

    def test_xml_transition_cannot_erase_retained_rows_or_change_unrelated_product_bytes(self):
        plan = [dict(table='djmdPlaylist',id='1000',before=dict(ID='1000',ParentID='root',Attribute=0),
                     after=dict(ID='1000',ParentID='root',Attribute=0))]
        changed = xml.touch(_XML,'1000',123001)
        xml.validate_transition(_XML,changed,plan)
        with self.assertRaises(ValueError):xml.validate_transition(_XML,changed.replace(b'6.8.4',b'6.8.7'),plan)
        with self.assertRaises(ValueError):xml.validate_transition(_XML,b'',plan)


class OfflinePlaylistPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name).resolve();fixture(self.source)
        self.source_bytes = {p.name:p.read_bytes() for p in self.source.iterdir()}
        self.workspace = txn.OfflinePlaylistWorkspace.capture(self.source)
        self.addCleanup(shutil.rmtree,self.workspace.root,True)

    def tearDown(self):
        self.assertEqual({p.name:p.read_bytes() for p in self.source.iterdir()},self.source_bytes)

    def test_commit_verifies_complete_pair_encryption_and_source_immutability(self):
        w = self.workspace;changes,after,base = planned(w)
        operation = w.apply(changes,after,**base)
        self.assertTrue(operation)
        self.assertEqual(w.xml.read_bytes(),after)
        self.assertNotEqual(w.database.read_bytes()[:16],b'SQLite format 3\0')
        self.assertFalse(w.transaction.exists())
        self.assertEqual(json.loads((w.root/'last-result.json').read_text())['outcome'],'committed')
        self.assertIsNone(txn.OfflinePlaylistWorkspace(w.root).recover())

    def test_native_wal_header_is_consolidated_only_in_disposable_backups(self):
        conn = _connect(self.source/'master.db')
        try:conn.execute('PRAGMA journal_mode=WAL')
        finally:conn.close()
        self.source_bytes = {p.name:p.read_bytes() for p in self.source.iterdir()}
        w = txn.OfflinePlaylistWorkspace.capture(self.source)
        self.addCleanup(shutil.rmtree,w.root,True)
        changes,after,base = planned(w)
        w.apply(changes,after,**base)
        self.assertEqual(w.xml.read_bytes(),after)
        self.assertFalse((w.root/'baseline.db-wal').exists())

    def test_stale_db_and_xml_plans_cannot_publish(self):
        w = self.workspace;changes,after,base = planned(w)
        atomic_bytes(w.xml,w.xml.read_bytes().replace(b'unknown note',b'newer note'))
        db_before = w.database.read_bytes();xml_before = w.xml.read_bytes()
        with self.assertRaises(ConflictError):w.apply(changes,after,**base)
        self.assertEqual((w.database.read_bytes(),w.xml.read_bytes()),(db_before,xml_before))
        self.assertFalse(w.transaction.exists())
        atomic_bytes(w.xml,_XML)
        conn = _connect(w.database)
        try:conn.execute("UPDATE djmdContent SET Title='newer'")
        finally:conn.close()
        with self.assertRaises(ConflictError):w.apply(changes,after,**base)

    def test_bad_xml_plan_redirect_and_competing_owner_reject(self):
        w = self.workspace;changes,after,base = planned(w)
        with self.assertRaises(ValueError):w.apply(changes,after.replace(b'6.8.4',b'7.0'),**base)
        with w._lock():
            with self.assertRaises(WriteProofError):txn.OfflinePlaylistWorkspace(w.root).recover()
        other = w.root/'external.xml';other.write_bytes(w.xml.read_bytes())
        w.xml.unlink();w.xml.symlink_to(other)
        with self.assertRaises(WriteProofError):w.apply(changes,after,**base)

    def interrupted(self, phase):
        w = self.workspace;changes,after,base = planned(w)
        def fail(actual):
            if actual==phase:raise RuntimeError('interrupted')
        with mock.patch.object(txn,'_checkpoint',side_effect=fail):
            with self.assertRaises(RuntimeError):w.apply(changes,after,**base)
        return after

    def test_unknown_db_blocks_xml_rollback_and_damaged_payload_blocks_all_recovery(self):
        w = self.workspace;after = self.interrupted('xml-published')
        conn = _connect(w.database)
        try:conn.execute("UPDATE djmdContent SET Title='external change'")
        finally:conn.close()
        db_bytes = w.database.read_bytes()
        with self.assertRaises(RecoveryRequired):w.recover()
        self.assertEqual((w.database.read_bytes(),w.xml.read_bytes()),(db_bytes,after))
        (w.transaction/'before.xml').write_bytes(b'damaged')
        with self.assertRaises(RecoveryRequired):w.recover()
        self.assertEqual((w.database.read_bytes(),w.xml.read_bytes()),(db_bytes,after))

    def test_impossible_committed_db_before_xml_pair_is_preserved(self):
        w = self.workspace;self.interrupted('sql-committed')
        atomic_bytes(w.xml,_XML);db_bytes = w.database.read_bytes()
        with self.assertRaises(RecoveryRequired):w.recover()
        self.assertEqual((w.database.read_bytes(),w.xml.read_bytes()),(db_bytes,_XML))

    def test_process_exit_matrix_including_wal_and_spilled_rollback_journal(self):
        child = '''
import os,sys
from pathlib import Path
from research.cdj_export import playlist_transaction as t
from test_playlist_transaction import planned
w=t.OfflinePlaylistWorkspace(Path(sys.argv[1]))
phase,mode=sys.argv[2:4]
original=t._connect
def configured(path,**kwargs):
 c=original(path,**kwargs)
 if path==w.database and not kwargs.get('readonly',False):
  if mode=='wal':c.execute('PRAGMA journal_mode=WAL')
  if mode=='spill':c.execute('PRAGMA cache_size=5');c.execute('PRAGMA cache_spill=ON')
 return c
t._connect=configured
def crash(actual):
 if actual==phase:os._exit(73)
t._checkpoint=crash
changes,after,base=planned(w,rename=mode=='spill',huge=mode=='spill')
w.apply(changes,after,**base)
'''
        cases = [('prepared','delete'),('journal-ready','delete'),('sql-applied','delete'),
                 ('xml-published','delete'),('sql-committed','delete'),('receipt-ready','delete'),
                 ('retired','delete'),('sql-applied','wal'),('sql-committed','wal'),
                 ('xml-published','spill')]
        for phase,mode in cases:
            with self.subTest(phase=phase,mode=mode):
                w = txn.OfflinePlaylistWorkspace.capture(self.source)
                self.addCleanup(shutil.rmtree,w.root,True)
                env = {**os.environ,'PYTHONPATH':str(Path.cwd())+os.pathsep+str(Path.cwd()/'tests')}
                result = subprocess.run([sys.executable,'-c',child,str(w.root),phase,mode],
                    env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                outcome = txn.OfflinePlaylistWorkspace(w.root).recover()
                if phase=='retired':self.assertIsNone(outcome)
                elif phase=='prepared':self.assertEqual(outcome,'abandoned-preparation')
                elif phase in ('sql-committed','receipt-ready'):self.assertEqual(outcome,'committed')
                else:self.assertEqual(outcome,'rolled-back')
                self.assertFalse(w.transaction.exists());self.assertFalse(w.retired.exists())
                if phase in ('prepared','journal-ready','sql-applied','xml-published'):
                    self.assertEqual(txn._inventory(w.database),w.metadata['baseline'])
                    self.assertEqual(w.xml.read_bytes(),_XML)
