"""Encrypted multi-format copy transactions retain domain and recovery guards."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from research.native_library import media_workspace as media, registration as wav, registration_workspace as base
from soundbox.rekordbox_write import _connect,ConflictError,RecoveryRequired,WriteProofError
from tests.support.audio import wave
from tests.support.native_library import fixture, inventory


class MediaWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.frozen=self.root/'frozen';self.frozen.mkdir();fixture(self.frozen)
        self.audio=self.root/'reference.wav';wave(self.audio)
        self.ws=media.OfflineMediaRegistrationWorkspace.capture(self.frozen)
        self.addCleanup(shutil.rmtree,self.ws.root,True)
        self.facts=self.ws.inspect_media(self.audio)

    def plan(self):
        return self.ws.plan(self.facts,identity='2',uuid='00000000-0000-4000-8000-000000000002',
                            timestamp='2026-10-31 04:00:00.123 +00:00',day='2026-10-31')

    def test_durable_json_intent_replays_once_with_original_inode_and_audio_unchanged(self):
        raw=self.audio.read_bytes();plan=json.loads(json.dumps(self.plan()))
        self.assertEqual(self.ws.apply(plan),'committed')
        self.assertEqual(self.ws.apply(plan),'already-committed')
        self.assertEqual(plan['candidate']['row']['rb_file_id'],str(self.audio.stat().st_ino))
        self.assertEqual(self.audio.read_bytes(),raw)
        self.assertFalse(self.ws.database.read_bytes().startswith(b'SQLite format 3'))

    def test_wav_and_simple_media_markers_cannot_publish_into_other_workspace_domains(self):
        plan=self.plan();conn=_connect(self.ws.database)
        try:
            with self.assertRaises(WriteProofError):wav._apply(conn,plan['candidate'])
            from research.native_library.media_registration import apply_temp
            with self.assertRaises(WriteProofError):apply_temp(conn,plan['candidate'])
        finally:conn.close()
        with self.assertRaises(WriteProofError):base.OfflineRegistrationWorkspace(self.ws.root)
        with self.assertRaises(WriteProofError):self.ws.inspect_wave(self.audio)

    def test_changed_digest_superseded_plan_and_opaque_row_conflict_cannot_mutate(self):
        plan=self.plan();before=inventory(self.ws.database)
        forged=deepcopy(plan);forged['candidate']['row']['FileType']=6
        with self.assertRaises(ConflictError):self.ws.apply(forged)
        with self.assertRaises(ConflictError):
            self.ws.plan(replace(self.facts,sha256='0'*64),identity='2',uuid=plan['candidate']['row']['UUID'],
                         timestamp='2026-10-31 04:00:00.123 +00:00',day='2026-10-31')
        self.assertEqual(inventory(self.ws.database),before)
        conn=_connect(self.ws.database)
        try:conn.execute("UPDATE unknown_table SET opaque=X'1234'")
        finally:conn.close()
        changed=inventory(self.ws.database)
        with self.assertRaises(WriteProofError):self.ws.apply(plan)
        self.assertEqual(inventory(self.ws.database),changed)

    def crash(self,phase,mode='normal'):
        plan=self.plan();(self.ws.root/'issued.json').write_text(json.dumps(plan))
        if mode=='wal':
            conn=_connect(self.ws.database)
            try:conn.execute('PRAGMA journal_mode=WAL')
            finally:conn.close()
        script='''import json,os,sys
from pathlib import Path
from research.native_library import media_workspace as media,registration_workspace as base
w=media.OfflineMediaRegistrationWorkspace(Path(sys.argv[1]))
if sys.argv[3]=='spill':
 original=base._connect
 def connect(*a,**k):
  c=original(*a,**k)
  if not k.get('readonly',False):c.execute('PRAGMA cache_size=1');c.execute('PRAGMA cache_spill=1')
  return c
 base._connect=connect
def fault(phase):
 if phase==sys.argv[2]:os._exit(73)
base._checkpoint=fault
w.apply(json.loads((w.root/'issued.json').read_text()))
'''
        result=subprocess.run([sys.executable,'-c',script,str(self.ws.root),phase,mode],capture_output=True,text=True)
        self.assertEqual(result.returncode,73,result.stdout+result.stderr)
        return plan

    def test_real_process_exit_and_spilled_wal_recovery_preserve_the_exact_pair(self):
        modes=[('prepared','normal','abandoned-preparation'),('journal-ready','normal','rolled-back'),
               ('sql-applied','normal','rolled-back'),('sql-committed','normal','committed'),
               ('receipt-ready','normal','committed'),('retired','normal',None),
               ('sql-applied','spill','rolled-back'),('sql-applied','wal','rolled-back'),('sql-committed','wal','committed')]
        initial=inventory(self.ws.database)
        for phase,mode,outcome in modes:
            with self.subTest(phase=phase,mode=mode):
                area=self.ws.root
                self.ws=media.OfflineMediaRegistrationWorkspace.capture(self.frozen)
                shutil.rmtree(area)
                self.facts=self.ws.inspect_media(self.audio)
                # Cleanup tracks the last independently owned workspace too.
                self.addCleanup(shutil.rmtree,self.ws.root,True)
                plan=self.crash(phase,mode);inode=self.ws.database.stat().st_ino
                self.ws=media.OfflineMediaRegistrationWorkspace(self.ws.root)
                self.assertEqual(self.ws.recover(),outcome)
                self.assertEqual(self.ws.database.stat().st_ino,inode)
                if outcome in ('abandoned-preparation','rolled-back'):
                    self.assertEqual(inventory(self.ws.database),initial)
                    self.assertEqual(self.ws.apply(plan),'committed')
                else:self.assertEqual(self.ws.apply(plan),'already-committed')

    def test_corrupt_recovery_evidence_is_preserved_and_never_opened_as_a_repair(self):
        self.crash('journal-ready');path=self.ws.transaction/'after.db';raw=path.read_bytes()
        path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaises(RecoveryRequired):self.ws.recover()
        self.assertTrue(self.ws.transaction.exists())

    def test_trigger_side_effects_are_denied_before_working_mutation(self):
        conn=_connect(self.ws.database)
        try:conn.execute("CREATE TRIGGER side_effect AFTER INSERT ON djmdContent BEGIN UPDATE unknown_table SET opaque=X'99'; END")
        finally:conn.close()
        plan=self.plan();before=inventory(self.ws.database)
        with self.assertRaises(Exception):self.ws.apply(plan)
        self.assertEqual(inventory(self.ws.database),before)
        self.assertEqual(self.ws.recover(),'abandoned-preparation')
