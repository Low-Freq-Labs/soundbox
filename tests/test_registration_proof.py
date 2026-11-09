"""Synthetic encrypted TEMP sources/audio; no live library, fixtures or devices."""
from copy import deepcopy
from dataclasses import replace
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

from tests.support.audio import wave
from tests.support.native_library import fixture, inventory

from research.native_library import registration as reg, registration_workspace as txn
from research.native_library.registration_replay import replay
from soundbox.rekordbox_write import ConflictError, RecoveryRequired, WriteProofError, _connect, _state, _rows

_STAMP = '2026-10-30 23:00:00.123 +00:00'
_UUID = '00000000-0000-4000-8000-000000000002'
_DEVICE = '00000000-0000-4000-8000-000000000003'


class RegistrationProof(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.capture = self.root/'frozen'; self.capture.mkdir()
        fixture(self.capture)
        self.audio = self.root/'Bare reference.wav'; wave(self.audio)
        self.ws = txn.OfflineRegistrationWorkspace.capture(self.capture)
        self.addCleanup(shutil.rmtree, self.ws.root)
        self.facts = self.ws.inspect_wave(self.audio)
        self.original = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.capture.iterdir()}
        self.audio_sha = hashlib.sha256(self.audio.read_bytes()).hexdigest()

    def planned(self, **changes):
        return self.ws.plan(self.facts, **(dict(identity='2', uuid=_UUID, timestamp=_STAMP, day='2026-10-30') | changes))

    def unchanged_inputs(self):
        self.assertEqual(self.original, {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.capture.iterdir()})
        self.assertEqual(self.audio_sha, hashlib.sha256(self.audio.read_bytes()).hexdigest())

    def test_constructor_original_inode_defaults_and_committed_replay(self):
        plan = self.planned(); candidate = plan['candidate']; row = candidate['row']
        self.assertEqual(row['rb_file_id'], str(self.audio.stat().st_ino))
        self.assertEqual((row['BPM'],row['KeyID'],row['Analysed'],row['AnalysisDataPath']), (0,'0',0,''))
        self.assertEqual((row['MasterDBID'],row['DeviceID'],row['Length'],row['Title']), ('123',_DEVICE,1,'Bare reference'))
        self.assertEqual(self.ws.apply(plan), 'committed')
        current = inventory(self.ws.database)
        self.assertEqual([t for t in current['tables'] if current['tables'][t]!=candidate['baseline']['tables'][t]],
                         ['agentRegistry','djmdContent'])
        self.assertEqual(self.ws.apply(json.loads(json.dumps(plan))), 'already-committed')
        self.assertEqual(inventory(self.ws.database), current)
        self.assertFalse(self.ws.database.read_bytes().startswith(b'SQLite format 3'))
        self.assertEqual((self.ws.root/'masterPlaylists6.xml').read_bytes(), (self.capture/'masterPlaylists6.xml').read_bytes())
        self.assertFalse(self.ws.transaction.exists())
        self.unchanged_inputs()

    def test_real_sql_rollback_leaves_complete_database_unchanged(self):
        plan = self.planned(); before = inventory(self.ws.database)
        conn = _connect(self.ws.database)
        try:
            conn.execute('BEGIN IMMEDIATE'); reg._apply(conn, plan['candidate']); conn.rollback()
            self.assertEqual(_state(conn), before)
        finally:
            conn.close()
        self.unchanged_inputs()

    def test_tampered_row_counter_component_or_baseline_never_mutates(self):
        original = self.planned(); before = inventory(self.ws.database)
        for change in ('Title','BPM','counter','baseline','audio','sha256','extra'):
            plan = deepcopy(original)
            if change in ('Title','BPM'):plan['candidate']['row'][change]='bad'
            elif change=='counter':plan['candidate']['counter_after']['updated_at']='bad'
            elif change=='baseline':plan['candidate']['baseline']['schema']='bad'
            elif change=='audio':plan['candidate']['audio']['seconds']=2
            elif change=='sha256':plan['candidate']['audio']['sha256']='0'*64
            else:plan['candidate']['extra']='bad'
            with self.subTest(change=change), self.assertRaises(WriteProofError):
                self.ws.apply(plan)
            self.assertEqual(inventory(self.ws.database), before)
            self.assertFalse(self.ws.transaction.exists())

    def test_stale_unknown_table_and_source_epoch_fail_before_publication(self):
        plan = self.planned(); conn = _connect(self.ws.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'11'")
        finally:conn.close()
        changed = inventory(self.ws.database)
        with self.assertRaises(ConflictError):self.ws.apply(plan)
        self.assertEqual(inventory(self.ws.database), changed)
        plan = self.planned()
        os.utime(self.audio, ns=(self.audio.stat().st_atime_ns, self.audio.stat().st_mtime_ns+1))
        with self.assertRaises(ConflictError):self.ws.apply(plan)
        self.assertFalse(self.ws.transaction.exists())

    def test_uncaptured_digest_and_superseded_intent_cannot_be_published(self):
        forged=replace(self.facts,sha256='0'*64)
        with self.assertRaises(ConflictError):
            self.ws.plan(forged,identity='2',uuid=_UUID,timestamp=_STAMP,day='2026-10-30')
        before=inventory(self.ws.database)
        first=self.planned()
        second=self.planned(timestamp='2026-10-30 23:00:00.124 +00:00')
        with self.assertRaises(ConflictError):self.ws.apply(first)
        self.assertEqual(inventory(self.ws.database),before)
        self.assertEqual(self.ws.apply(second),'committed')
        self.unchanged_inputs()

    def test_collision_including_deleted_path_requires_identity_reconciliation(self):
        for column, value in (('ID','2'),('UUID',_UUID),('FolderPath',str(self.audio))):
            conn = _connect(self.ws.database)
            try:conn.execute('UPDATE djmdContent SET "'+column+'"=?,rb_local_deleted=1 WHERE Title=\'Existing\'', (value,))
            finally:conn.close()
            with self.subTest(column=column), self.assertRaises(ConflictError):self.planned()
            conn = _connect(self.ws.database)
            try:conn.execute("UPDATE djmdContent SET ID='1',UUID='old',FolderPath='/never/open.wav',rb_local_deleted=0")
            finally:conn.close()

    def test_distinct_files_with_identical_audio_get_distinct_records(self):
        self.ws.apply(self.planned())
        other = self.root/'Different reference.wav'; shutil.copyfile(self.audio, other)
        facts = self.ws.inspect_wave(other)
        self.assertEqual(facts.sha256,self.facts.sha256)
        self.ws.apply(self.ws.plan(facts, identity='3',uuid='00000000-0000-4000-8000-000000000004',timestamp=_STAMP,day='2026-10-30'))
        conn = _connect(self.ws.database, readonly=True)
        try:self.assertEqual({r['FolderPath'] for r in _rows(conn,'djmdContent') if r['ID'] in ('2','3')},{str(self.audio),str(other)})
        finally:conn.close()

    def test_bad_framing_clock_identity_schema_and_aliases_fail_closed(self):
        bad = self.root/'Unsupported.wav'; wave(bad,rate=48000)
        with self.assertRaises(WriteProofError):reg.inspect_wave(bad,self.root/'scratch')
        bad.write_bytes(self.audio.read_bytes()+b'extra')
        with self.assertRaises(WriteProofError):reg.inspect_wave(bad,self.root/'scratch')
        alias = self.root/'alias.wav'; alias.symlink_to(self.audio)
        with self.assertRaises(WriteProofError):reg.inspect_wave(alias,self.root/'scratch')
        for change in (dict(identity='02'),dict(uuid='bad'),dict(timestamp='2026-10-30'),dict(day='2026-02-30')):
            with self.subTest(change=change), self.assertRaises(WriteProofError):self.planned(**change)
        conn = _connect(self.ws.database)
        try:conn.execute('ALTER TABLE djmdContent ADD COLUMN future TEXT')
        finally:conn.close()
        with self.assertRaises(WriteProofError):self.planned()

    def test_unmarked_relocated_and_aliased_workspaces_cannot_write(self):
        with self.assertRaises(WriteProofError):txn.OfflineRegistrationWorkspace(self.capture)
        alias = self.root/'alias'; alias.symlink_to(self.ws.root,target_is_directory=True)
        with self.assertRaises(WriteProofError):txn.OfflineRegistrationWorkspace(alias)
        copied = self.root/'soundbox-registration-copied'; shutil.copytree(self.ws.root,copied)
        with self.assertRaises(WriteProofError):txn.OfflineRegistrationWorkspace(copied)
        plan=self.planned(); conn=_connect(self.capture/'master.db')
        try:
            with self.assertRaises(WriteProofError):reg._apply(conn,plan['candidate'])
        finally:conn.close()
        self.unchanged_inputs()

    def test_xml_and_unjournaled_sql_companions_block(self):
        (self.ws.root/'masterPlaylists6.xml').write_bytes(b'changed')
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        (self.ws.root/'masterPlaylists6.xml').write_bytes((self.capture/'masterPlaylists6.xml').read_bytes())
        (self.ws.root/'working.db-journal').write_bytes(b'unknown')
        with self.assertRaises(RecoveryRequired):self.ws.recover()

    def test_prejournal_staging_crash_abandons_only_disposable_payloads(self):
        before=inventory(self.ws.database)
        self.ws.transaction.mkdir(mode=0o700)
        txn._copy_backup(self.ws.database,self.ws.transaction/'before.db')
        txn._copy_backup(self.ws.database,self.ws.transaction/'after.db')
        (self.ws.transaction/'after.db-journal').write_bytes(b'partial staging journal')
        self.assertEqual(self.ws.recover(),'abandoned-preparation')
        self.assertEqual(inventory(self.ws.database),before)
        self.unchanged_inputs()

    def test_replay_does_not_hide_unrelated_native_registry_changes(self):
        plan=self.planned();self.ws.apply(plan)
        native=self.root/'native-after';native.mkdir()
        shutil.copyfile(self.ws.database,native/'master.db')
        shutil.copyfile(self.capture/'masterPlaylists6.xml',native/'masterPlaylists6.xml')
        conn=_connect(native/'master.db')
        try:
            conn.execute("UPDATE agentRegistry SET updated_at='native-only-time' WHERE registry_id='notificationNextFetch'")
            state=_state(conn)
        finally:conn.close()
        (native/'state.json').write_text(json.dumps(state))
        report_path=self.root/'report.json'
        report=replay(self.capture,native,self.audio,report_path)
        self.assertFalse(report['complete_semantic_match'])
        self.assertEqual(report['mismatched_reference_tables'],['agentRegistry'])
        self.assertTrue(report['constructor_exact_native_row'])
        self.assertEqual(report['registry_remaining_delta']['agentRegistry'][0]['id'],'notificationNextFetch')
        self.assertFalse(report['source_write_authority'])
        self.assertTrue(report['protected_capture_bytes_unchanged'])
        with self.assertRaises(ValueError):replay(self.capture,native,self.audio,report_path)
        self.unchanged_inputs()

    def crash(self, phase, *, spill=False):
        plan = self.planned(); (self.ws.root/'plan.json').write_text(json.dumps(plan))
        script = '''import json,os,sys
from pathlib import Path
from research.native_library import registration_workspace as m
w=m.OfflineRegistrationWorkspace(Path(sys.argv[1]))
if sys.argv[3]=='spill':
 original=m._connect
 def connect(*args,**kwargs):
  c=original(*args,**kwargs)
  if not kwargs.get('readonly',False):
   c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=1')
  return c
 m._connect=connect
def fault(phase):
 if phase==sys.argv[2]:os._exit(73)
m._checkpoint=fault
w.apply(json.loads((w.root/'plan.json').read_text()))
'''
        result = subprocess.run([sys.executable,'-c',script,str(self.ws.root),phase,'spill' if spill else 'normal'],capture_output=True,text=True)
        self.assertEqual(result.returncode,73,result.stdout+result.stderr)
        return plan

    def test_actual_process_exit_recovery_at_every_durable_boundary(self):
        initial = inventory(self.ws.database)
        for phase, outcome in (('prepared','abandoned-preparation'),('journal-ready','rolled-back'),
                               ('sql-applied','rolled-back'),('sql-committed','committed'),
                               ('receipt-ready','committed'),('retired',None)):
            with self.subTest(phase=phase):
                if self.ws.transaction.exists():self.ws.recover()
                # Each subprocess gets a fresh, independently encrypted working copy.
                for suffix in ('','-journal','-wal','-shm'):
                    path=self.ws.database.with_name(self.ws.database.name+suffix)
                    if path.exists():path.unlink()
                txn._copy_backup(self.ws.root/'baseline.db',self.ws.database)
                (self.ws.root/'last-result.json').unlink(missing_ok=True)
                plan = self.crash(phase)
                self.ws=txn.OfflineRegistrationWorkspace(self.ws.root)
                self.assertEqual(self.ws.recover(),outcome)
                if phase in ('prepared','journal-ready','sql-applied'):
                    self.assertEqual(inventory(self.ws.database),initial)
                    self.assertEqual(self.ws.apply(plan),'committed')
                else:
                    self.assertEqual(self.ws.apply(plan),'already-committed')
                self.assertEqual(self.ws.recover(),None)
        self.unchanged_inputs()

    def test_actual_spilled_hot_journal_recovers_without_restoring_a_database(self):
        before=inventory(self.ws.database)
        plan=self.crash('sql-applied',spill=True)
        journal=self.ws.database.with_name(self.ws.database.name+'-journal')
        self.assertTrue(journal.exists())
        self.assertNotEqual(journal.read_bytes()[:8],b'\0'*8)
        inode=self.ws.database.stat().st_ino
        self.ws=txn.OfflineRegistrationWorkspace(self.ws.root)
        self.assertEqual(self.ws.recover(),'rolled-back')
        self.assertEqual(self.ws.database.stat().st_ino,inode)
        self.assertEqual(inventory(self.ws.database),before)
        self.assertEqual(self.ws.apply(plan),'committed')
        self.unchanged_inputs()

    def test_corrupt_recovery_payload_or_unknown_database_preserves_evidence(self):
        self.crash('journal-ready')
        evidence=self.ws.transaction/'after.db'; saved=evidence.read_bytes()
        evidence.write_bytes(saved[:-1]+bytes([saved[-1]^1]))
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertTrue(self.ws.transaction.exists())
        evidence.write_bytes(saved)
        conn=_connect(self.ws.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'22'")
        finally:conn.close()
        changed=inventory(self.ws.database)
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertEqual(inventory(self.ws.database),changed)
        self.assertTrue(self.ws.transaction.exists())
        self.unchanged_inputs()

    def test_trigger_side_effect_is_denied_and_preparation_abandoned(self):
        conn=_connect(self.ws.database)
        try:conn.execute("CREATE TRIGGER unrelated AFTER INSERT ON djmdContent BEGIN UPDATE unknown_table SET opaque=X'33'; END")
        finally:conn.close()
        plan=self.planned(); before=inventory(self.ws.database)
        with self.assertRaises(Exception) as error:self.ws.apply(plan)
        self.assertIn('not authorized',str(error.exception))
        self.assertEqual(inventory(self.ws.database),before)
        self.assertEqual(self.ws.recover(),'abandoned-preparation')
        self.assertEqual(inventory(self.ws.database),before)

    def test_changed_source_at_commit_rolls_back_then_reconciles(self):
        plan=self.planned(); before=inventory(self.ws.database)
        def change(phase):
            if phase=='sql-applied':
                os.utime(self.audio,ns=(self.audio.stat().st_atime_ns,self.audio.stat().st_mtime_ns+1))
        with mock.patch.object(txn,'_checkpoint',side_effect=change), self.assertRaises(ConflictError):
            self.ws.apply(plan)
        self.assertEqual(self.ws.recover(),'rolled-back')
        self.assertEqual(inventory(self.ws.database),before)
        self.unchanged_inputs()


if __name__=='__main__':
    unittest.main()
