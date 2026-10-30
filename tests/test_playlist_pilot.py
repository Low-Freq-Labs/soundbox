"""Approved single-intent pilot on synthetic TEMP sources; no live-state reads."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_playlist_transaction import fixture
from research.cdj_export import playlist_pilot as pilot, playlist_pilot_plan as plans
from research.cdj_export.playlist_transaction import _inventory, _sha
from soundbox import rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.rekordbox_write import _connect, _rows, ConflictError, RecoveryRequired, WriteProofError
from soundbox.state_io import atomic_bytes


class PilotProcessGuard(unittest.TestCase):
    def test_only_explicit_empty_process_result_allows_admission(self):
        with mock.patch.object(pilot.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            pilot._apps_closed()
        for code,output,error in ((0,'123\n',''),(2,'','error'),(1,'unexpected','')):
            with self.subTest(code=code), mock.patch.object(pilot.subprocess,'run',
                    return_value=subprocess.CompletedProcess([],code,output,error)):
                with self.assertRaises(WriteProofError):pilot._apps_closed()
        with mock.patch.object(pilot.subprocess,'run',side_effect=OSError('unavailable')):
            with self.assertRaises(WriteProofError):pilot._apps_closed()


class ApprovedPlaylistPilot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve();self.source = self.base/'source';self.source.mkdir()
        fixture(self.source)
        for target,attr,value in ((plans,'_installed_version',tags.NATIVE_VERSION),
                                  (cues,'_installed_version',tags.NATIVE_VERSION),
                                  (tags,'_installed_version',tags.NATIVE_VERSION)):
            patch = mock.patch.object(target,attr,return_value=value);patch.start();self.addCleanup(patch.stop)
        for target,attr in ((cues,'_closed'),(tags,'_assert_closed'),
                            (plans,'assert_rekordbox_closed'),(pilot,'_apps_closed')):
            patch = mock.patch.object(target,attr,return_value=None);patch.start();self.addCleanup(patch.stop)
        self.folder = self.base/'plan'
        self.record = plans.prepare(self.source/'master.db',self.folder,['track','track-two'],
            data_root=self.base/'appdata')
        self.approval = self.base/'approval.json'
        self.approve()

    def approve(self, **overrides):
        record = dict(format=pilot.APPROVAL_FORMAT,approved=True,
            plan_sha256=_sha(self.folder/'plan.json'),source=str(self.source/'master.db'),
            operation_id=self.record['operation_id'])
        atomic_bytes(self.approval,json.dumps({**record,**overrides}).encode())

    def store(self):
        return pilot.PlaylistPilot(self.folder,self.approval)

    def test_one_approved_create_name_two_track_request_commits_and_cannot_repeat(self):
        store = self.store();before = _inventory(store.database)
        identity = store.apply()
        self.assertEqual(identity,self.record['playlist_id'])
        self.assertEqual(_inventory(store.database),self.record['after'])
        self.assertEqual(store.xml.read_bytes(),(self.folder/'after.xml').read_bytes())
        self.assertEqual(before,self.record['before'])
        conn = _connect(store.database,readonly=True)
        try:
            rows = [r for r in _rows(conn,'djmdSongPlaylist') if r['PlaylistID']==identity]
            self.assertEqual([r['ContentID'] for r in sorted(rows,key=lambda r:r['TrackNo'])],['track','track-two'])
        finally:conn.close()
        before_repeat = store.database.read_bytes(),store.xml.read_bytes()
        self.assertEqual(self.store().apply(),'already-completed')
        self.assertEqual((store.database.read_bytes(),store.xml.read_bytes()),before_repeat)
        self.assertFalse(store.journal.exists())
        receipt = json.loads((store.archive/'result.json').read_bytes())
        self.assertFalse(receipt['native_acceptance'])
        self.assertEqual(receipt['outcome'],'committed')

    def test_foreign_or_missing_approval_and_running_app_cannot_write_source(self):
        original = (self.source/'master.db').read_bytes(),(self.source/'masterPlaylists6.xml').read_bytes()
        self.approve(plan_sha256='0'*64)
        with self.assertRaises(WriteProofError):self.store()
        self.approval.unlink()
        with self.assertRaises(OSError):self.store()
        self.approve();store = self.store()
        with mock.patch.object(pilot,'_apps_closed',side_effect=WriteProofError('SoundBox open')), \
                mock.patch.object(store.lease,'_connect') as source_connection:
            with self.assertRaises(WriteProofError):store.apply()
            source_connection.assert_not_called()
        self.assertEqual(((self.source/'master.db').read_bytes(),
                          (self.source/'masterPlaylists6.xml').read_bytes()),original)
        changed = self.store();changed.plan['playlist_name'] = 'unapproved memory mutation'
        with mock.patch.object(changed.lease,'_connect') as connection:
            with self.assertRaises(WriteProofError):changed.apply()
            connection.assert_not_called()


    def test_unreviewed_intent_and_unrelated_xml_edit_are_rejected(self):
        record = json.loads((self.folder/'plan.json').read_bytes())
        record['playlist_name'] = 'different intent'
        atomic_bytes(self.folder/'plan.json',json.dumps(record).encode())
        self.approve()
        with self.assertRaises(WriteProofError):self.store()

    def test_pending_pilot_blocks_older_domains_and_corrupt_archive_blocks_recovery(self):
        store = self.store()
        def stop(phase):
            if phase=='xml-published':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=stop):
            with self.assertRaises(RuntimeError):store.apply()
        after_xml = store.xml.read_bytes()
        old = tags.LiveTagStore(tags.TagWriteAuthority(str(store.database),
            self.record['library_id'],self.record['schema_sha256']),store.root)
        with self.assertRaises(RecoveryRequired):old.check_ready()
        (store.archive/'after.xml').write_bytes(b'damaged')
        with mock.patch.object(store.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):store.recover()
            connection.assert_not_called()
        self.assertEqual(store.xml.read_bytes(),after_xml)
        self.assertTrue(store.journal.exists())

    def test_unknown_source_state_cannot_trigger_xml_rollback(self):
        store = self.store()
        def stop(phase):
            if phase=='xml-published':raise RuntimeError('interrupted')
        with mock.patch.object(pilot,'_fault',side_effect=stop):
            with self.assertRaises(RuntimeError):store.apply()
        after_xml = store.xml.read_bytes()
        conn = _connect(store.database)
        try:conn.execute("UPDATE djmdContent SET Title='unrelated native change'")
        finally:conn.close()
        unknown = _inventory(store.database)
        with self.assertRaises(RecoveryRequired):store.recover()
        self.assertEqual(_inventory(store.database),unknown)
        self.assertEqual(store.xml.read_bytes(),after_xml)

    def test_stale_xml_and_unresolved_grid_payloads_do_not_open_publication(self):
        store = self.store()
        atomic_bytes(store.xml,store.xml.read_bytes().replace(b'unknown note',b'external note'))
        external = store.xml.read_bytes()
        with self.assertRaises(ConflictError):store.apply()
        self.assertEqual(store.xml.read_bytes(),external)
        self.assertFalse(store.journal.exists())
        (store.root/'grid-transaction').mkdir(mode=0o700)
        with mock.patch.object(store.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):store.apply()
            connection.assert_not_called()

    def test_process_exit_recovery_shared_journal_wal_and_spill(self):
        child = '''
import os,sys,json
from pathlib import Path
from research.cdj_export import playlist_pilot as p
from soundbox import rekordbox_cue_store as cues
cues._installed_version=lambda:'6.8.7.0303'
cues._closed=lambda:None
p._apps_closed=lambda:None
store=p.PlaylistPilot(Path(sys.argv[1]),Path(sys.argv[2]))
phase,mode=sys.argv[3:5]
original=store.lease._connect
def configured():
 c=original()
 if mode=='wal':c.execute('PRAGMA journal_mode=WAL')
 if mode=='spill':c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=ON')
 return c
store.lease._connect=configured
def crash(actual):
 if actual==phase:
  journal=store.database.with_name('master.db-journal')
  if mode=='spill':
   header=journal.read_bytes()[:8] if journal.exists() else b''
   (Path(sys.argv[1])/'fault-observation.json').write_text(json.dumps(
    {'journal_exists':journal.exists(),'nonzero_header':any(header)}))
  os._exit(73)
p._fault=crash
store.apply()
'''
        cases = [('payload-ready','delete'),('journal-ready','delete'),('sql-applied','delete'),
                 ('xml-published','delete'),('sql-committed','delete'),('receipt-ready','delete'),
                 ('journal-retired','delete'),('xml-published','wal'),('sql-committed','wal'),
                 ('xml-published','spill')]
        for index,(phase,mode) in enumerate(cases):
            with self.subTest(phase=phase,mode=mode):
                case = self.base/str(index);source = case/'source';source.mkdir(parents=True)
                fixture(source)
                folder = case/'plan'
                record = plans.prepare(source/'master.db',folder,['track','track-two'],data_root=case/'appdata')
                approval = case/'approval.json'
                atomic_bytes(approval,json.dumps(dict(format=pilot.APPROVAL_FORMAT,approved=True,
                    plan_sha256=_sha(folder/'plan.json'),source=record['source'],
                    operation_id=record['operation_id'])).encode())
                result = subprocess.run([sys.executable,'-c',child,str(folder),str(approval),phase,mode],
                    env={**os.environ,'PYTHONPATH':str(Path.cwd())},capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                store = pilot.PlaylistPilot(folder,approval)
                outcome = store.recover()
                committed = phase in ('sql-committed','receipt-ready','journal-retired')
                self.assertEqual(outcome,'committed' if committed else None if phase=='payload-ready' else 'rolled-back')
                self.assertEqual(_inventory(store.database),record['after' if committed else 'before'])
                self.assertEqual(store.xml.read_bytes(),(folder/('after.xml' if committed else 'before.xml')).read_bytes())
                self.assertFalse(store.journal.exists())
                if mode=='spill':
                    observed = json.loads((folder/'fault-observation.json').read_bytes())
                    self.assertTrue(observed['journal_exists'])
                    self.assertTrue(observed['nonzero_header'])

    def test_shared_owner_and_other_domain_journal_block_before_native_sql(self):
        store = self.store()
        authority = tags.TagWriteAuthority(str(store.database),self.record['library_id'],
            self.record['schema_sha256'])
        old = tags.LiveTagStore(authority,store.root)
        with store.lease._lock():
            with self.assertRaises(WriteProofError):old.check_ready()
        atomic_bytes(store.journal,b'{"format":"unrecognized-other-domain"}')
        with mock.patch.object(store.lease,'_connect') as connection:
            with self.assertRaises(RecoveryRequired):store.apply()
            connection.assert_not_called()
        self.assertEqual(store.journal.read_bytes(),b'{"format":"unrecognized-other-domain"}')

    def test_stale_entire_db_or_xml_and_payload_redirect_cannot_publish(self):
        store = self.store()
        conn = _connect(store.database)
        try:conn.execute("UPDATE djmdContent SET Title='external edit'")
        finally:conn.close()
        xml_before = store.xml.read_bytes();db_before = store.database.read_bytes()
        with self.assertRaises(ConflictError):store.apply()
        self.assertEqual((store.database.read_bytes(),store.xml.read_bytes()),(db_before,xml_before))
        self.assertFalse(store.journal.exists())
        saved = self.folder/'saved-after.xml';shutil.move(self.folder/'after.xml',saved)
        (self.folder/'after.xml').symlink_to(saved)
        with self.assertRaises(WriteProofError):self.store()
