"""Accepted analysis transport against synthetic encrypted TEMP sources only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

import test_analysis_workspace as fixtures
from test_registration_proof import wave
from soundbox import rekordbox_analysis_store as store, rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.analysis import ENGINE
from soundbox.native_media import capture_media
from soundbox.rekordbox_write import _connect,_json,_state,_LINK_COLUMNS,ConflictError,RecoveryRequired,WriteProofError
from soundbox.state_io import atomic_bytes


class AnalysisPublication(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name).resolve()
        for obj,name,value in ((store,'_installed_version',lambda:tags.NATIVE_VERSION),
                               (cues,'_installed_version',lambda:tags.NATIVE_VERSION),(cues,'_closed',lambda:None),
                               (store,'assert_rekordbox_closed',lambda:None)):
            patch=mock.patch.object(obj,name,value);patch.start();self.addCleanup(patch.stop)
        self.source,self.authority,self.folder=self.prepare(self.root/'case')

    def prepare(self,root,*,extra=False,converted=False):
        source=root/'source';data=fixtures.fixture(source);(source/'share').mkdir(mode=0o700)
        audio_path=root/'Actual saved grid.wav';wave(audio_path,seconds=32)
        conversion_receipt=None;original_identity=None
        if converted:
            import hashlib
            import numpy as np
            import soundfile as sf
            from soundbox import analysis,wave_conversion
            sf.write(audio_path,np.tile([[-1.33,1.2]],(44100*32,1)),44100,subtype='FLOAT')
            original_identity=dict(sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                size=audio_path.stat().st_size,epoch=analysis.identity(audio_path.stat()))
            conversion_receipt=wave_conversion.convert(audio_path,root/'appdata/wave-originals',str(uuid4()),
                'sb-converted',expected_sha256=original_identity['sha256'],clip_overs=True)
        audio=capture_media(audio_path,root/'audio')
        conn=_connect(source/'master.db')
        try:
            conn.execute('CREATE TABLE djmdSongMyTag ('+','.join('"'+c+'" TEXT' for c in _LINK_COLUMNS)+')')
            conn.execute('CREATE TABLE djmdMyTag(ID TEXT PRIMARY KEY,UUID TEXT,Attribute INTEGER,rb_local_deleted INTEGER)')
            conn.execute('UPDATE djmdContent SET FolderPath=?,FileSize=?,rb_file_id=? WHERE ID=?',
                         (audio.path,audio.epoch[2],str(audio.epoch[1]),'123'))
            if extra:
                from soundbox.rekordbox_write import _rows,_quote
                base=next(r for r in _rows(conn,'djmdContent') if r['ID']=='123')
                for tid in ('124','125'):
                    other_path=root/(tid+'.wav');wave(other_path,seconds=32);other=capture_media(other_path,root/('capture-'+tid))
                    row=dict(base,ID=tid,UUID=str(uuid4()),FolderPath=other.path,FileSize=other.epoch[2],rb_file_id=str(other.epoch[1]))
                    columns=list(row)
                    conn.execute('INSERT INTO djmdContent ('+','.join(_quote(c) for c in columns)+') VALUES ('+
                                 ','.join('?' for _ in columns)+')',list(row.values()))
            schema=tags._schema(conn)
        finally:conn.close()
        authority=store.AnalysisAuthority(str(source/'master.db'),'456',schema,'0'*64,True)
        result=dict(engine=ENGINE,path=audio.path,sha256=audio.sha256,size=audio.epoch[2],epoch=list(audio.epoch),duration=32.0,
            bpm=120.0,key='8B',beats=[[i%4+1,249+500*i,120.0] for i in range(64)],first_cue=None)
        if converted:
            from soundbox.wave_conversion import rebind
            result=rebind({**result,**original_identity},conversion_receipt)
        op=str(uuid4());folder=root/'appdata/native-analysis-plans'/op
        store.prepare(authority,'123',data.track['UUID'],result,folder,data_root=root/'appdata',operation_id=op)
        return source,authority,folder

    def instance(self):return store.AnalysisStore(self.authority,self.folder)

    def state(self):
        conn=_connect(self.source/'master.db',readonly=True)
        try:return _state(conn)
        finally:conn.close()

    def test_saved_music_and_packet_commit_together_and_replay_is_idempotent(self):
        obj=self.instance();audio=Path(obj.plan['audio']['path']);before=audio.read_bytes();xml=obj.xml.read_bytes()
        self.assertEqual(obj.apply(),'123');self.assertEqual(self.state(),obj.plan['after'])
        self.assertEqual(obj.plan['candidate']['changes'][0]['after']['Length'],31)
        self.assertEqual(audio.read_bytes(),before);self.assertEqual(obj.xml.read_bytes(),xml)
        self.assertEqual(obj.apply(),'already-completed');self.assertFalse(obj.journal.exists())
        self.assertEqual({p.name for p in obj._locations()[1].iterdir()},set(store.NAMES))
        self.assertNotIn('djmdMixerParam',[c['table'] for c in obj.plan['candidate']['changes']])

    def test_converted_float_pcm24_publishes_saved_grid_key_and_waveforms_without_redetection(self):
        self.source,self.authority,self.folder=self.prepare(self.root/'converted',converted=True)
        obj=self.instance();path=Path(obj.plan['audio']['path']);before=path.read_bytes()
        self.assertEqual(obj.apply(),'123');self.assertEqual(path.read_bytes(),before)
        row=obj.plan['candidate']['changes'][0]['after']
        self.assertEqual((row['BPM'],row['BitDepth'],row['SampleRate']),(12000,24,44100))
        from desktop.native_analysis import readback
        saved=readback(self.source/'master.db','123',row['UUID'])
        self.assertEqual(saved['key'],'8B');self.assertEqual(len(saved['beats']),64)
        self.assertEqual(saved['beats'][0],[1,249,120.0])
        from soundbox.rekordbox_analysis import read
        packet=read(saved['analysis_path'])
        self.assertTrue(packet['_blue_detail']);self.assertTrue(packet['_rgb_detail']);self.assertTrue(packet['_detail_bands'])
        self.assertEqual(obj.apply(),'already-completed')

    def test_foreign_authority_source_scope_and_changed_plan_cannot_publish(self):
        obj=self.instance()
        foreign=store.AnalysisAuthority(self.authority.database,'789',self.authority.schema_sha256,'0'*64,True)
        with self.assertRaises(WriteProofError):store.AnalysisStore(foreign,self.folder)
        original=(self.folder/'plan.json').read_bytes();body=json.loads(original);body['technical']['bits']=24
        atomic_bytes(self.folder/'plan.json',_json(body))
        with self.assertRaises(WriteProofError):self.instance()
        atomic_bytes(self.folder/'plan.json',original)
        with mock.patch.object(cues,'_closed',side_effect=WriteProofError('owner open')),mock.patch.object(obj.lease,'_connect') as sql:
            with self.assertRaises(WriteProofError):obj.apply()
            sql.assert_not_called()
        self.assertEqual(self.state(),obj.plan['before'])

    def test_changed_audio_source_occupied_target_and_unknown_recovery_fail_closed(self):
        obj=self.instance();stage,target=obj._locations();target.parent.mkdir(parents=True,mode=0o700);target.mkdir()
        (target/'other').write_bytes(b'keep')
        with self.assertRaises(ConflictError):obj.apply()
        self.assertEqual((target/'other').read_bytes(),b'keep');(target/'other').unlink();target.rmdir()
        audio=Path(obj.plan['audio']['path']);os.utime(audio,ns=(audio.stat().st_atime_ns,audio.stat().st_mtime_ns+1))
        with self.assertRaises(ConflictError):obj.apply()
        atomic_bytes(obj.journal,b'{"format":"other-domain"}')
        with self.assertRaises(RecoveryRequired):obj.recover()
        self.assertEqual(self.state(),obj.plan['before'])

    def test_interrupted_files_recover_without_touching_unrelated_changes(self):
        obj=self.instance()
        def fail(phase):
            if phase=='files-published':raise RuntimeError('exit')
        with mock.patch.object(store,'_fault',side_effect=fail),self.assertRaises(RuntimeError):obj.apply()
        self.assertTrue(obj.journal.exists());self.assertEqual(obj.recover(),'rolled-back')
        self.assertEqual(self.state(),obj.plan['before']);self.assertFalse(obj._locations()[1].exists())
        self.assertEqual(obj.apply(),'123')
        conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE unknown_table SET opaque=X'7788'")
        finally:conn.close()
        changed=self.state();self.assertEqual(obj.apply(),'already-completed');self.assertEqual(self.state(),changed)

    def test_retention_keeps_first_reference_latest_pair_and_all_permanent_receipts(self):
        from soundbox.rekordbox_write import _rows
        source,authority,folder=self.prepare(self.root/'retention',extra=True)
        first=store.AnalysisStore(authority,folder);first.apply();store.retire_confirmed(first,[])
        confirmed=[first.plan['operation_id']];objects=[first]
        for tid in ('124','125'):
            conn=_connect(source/'master.db',readonly=True)
            try:row=next(r for r in _rows(conn,'djmdContent') if r['ID']==tid)
            finally:conn.close()
            audio=capture_media(Path(row['FolderPath']),self.root/('copy-'+tid))
            result=dict(first.plan['result'],path=audio.path,sha256=audio.sha256,epoch=list(audio.epoch),size=audio.epoch[2])
            op=str(uuid4());folder=first.folder.parent/op
            store.prepare(authority,tid,row['UUID'],result,folder,data_root=Path(first.plan['data_root']),operation_id=op)
            obj=store.AnalysisStore(authority,folder);obj.apply();store.retire_confirmed(obj,confirmed)
            objects.append(obj);confirmed.append(op)
        self.assertTrue((objects[0].archive/'before.db').exists())
        self.assertFalse((objects[1].archive/'before.db').exists())
        self.assertTrue((objects[2].archive/'before.db').exists())
        self.assertFalse((objects[0].folder/'before.db').exists())
        for obj in objects:
            self.assertTrue((obj.archive/'result.json').exists());self.assertTrue((obj.archive/'ANLZ0000.DAT').exists())
        store.retire_confirmed(objects[-1],confirmed)
        self.assertTrue((objects[-1].archive/'before.db').exists())

    def test_stale_unstarted_plan_can_renew_without_reverting_new_native_work(self):
        obj=self.instance();conn=_connect(self.source/'master.db')
        try:conn.execute("UPDATE unknown_table SET opaque=X'99'")
        finally:conn.close()
        changed=self.state()
        with self.assertRaises(ConflictError):obj.apply()
        plan=obj.plan
        self.assertTrue(obj.discard_unstarted());self.assertFalse(obj.folder.exists())
        store.prepare(self.authority,'123',plan['track_uuid'],plan['result'],self.folder,
                      data_root=Path(plan['data_root']),operation_id=plan['operation_id'])
        renewed=self.instance();self.assertEqual(renewed.plan['before'],changed)
        self.assertEqual(renewed.apply(),'123');self.assertFalse(renewed.discard_unstarted())
        self.assertEqual(self.state(),renewed.plan['after'])

    def test_actual_process_exit_recovers_sql_and_directory_prefixes(self):
        script='''
import os,sys,json
from pathlib import Path
from soundbox import rekordbox_analysis_store as s,rekordbox_cue_store as c,rekordbox_tags as t
s._installed_version=lambda:t.NATIVE_VERSION;c._installed_version=lambda:t.NATIVE_VERSION;c._closed=lambda:None
folder=Path(sys.argv[1]);plan=s.load_plan(folder);a=s.AnalysisAuthority(**plan['authority']);obj=s.AnalysisStore(a,folder)
def crash(phase):
 if phase==sys.argv[2]:os._exit(73)
s._fault=crash;obj.apply()
'''
        for index,phase in enumerate(('journal-ready','stage-created','file-ANLZ0000.DAT','files-published','sql-applied','sql-committed','receipt-ready','journal-retired')):
            with self.subTest(phase=phase):
                source,authority,folder=self.prepare(self.root/str(index));obj=store.AnalysisStore(authority,folder)
                result=subprocess.run([sys.executable,'-c',script,str(folder),phase],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,73,result.stderr)
                expected='committed' if phase in ('sql-committed','receipt-ready','journal-retired') else 'rolled-back'
                self.assertEqual(obj.recover(),expected)
                conn=_connect(source/'master.db',readonly=True)
                try:self.assertEqual(_state(conn),obj.plan['after' if expected=='committed' else 'before'])
                finally:conn.close()
                self.assertFalse(obj.journal.exists())
