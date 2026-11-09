"""Accepted analysis transport against synthetic encrypted TEMP sources only."""
from pathlib import Path
from dataclasses import asdict
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

import test_analysis_workspace as fixtures
from tests.support.audio import wave
from soundbox import rekordbox_analysis_store as store, rekordbox_cue_store as cues, rekordbox_tags as tags
from soundbox.analysis import ENGINE
from soundbox.native_media import MediaIdentity,capture_media
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

    def test_exact_plan_reuse_still_hashes_every_payload_and_checks_profile(self):
        expected=store.load_plan(self.folder)
        with mock.patch.object(store,'_verify_plan_reconstruction',side_effect=AssertionError('Duplicate pure proof')), \
                mock.patch.object(store,'_sha',wraps=store._sha) as hashes, \
                mock.patch.object(store,'_require_profile',wraps=store._require_profile) as profile:
            self.assertEqual(store.load_plan(self.folder),expected)
            self.assertEqual(self.instance().plan,expected)
        for name in store.PAYLOADS:
            self.assertGreaterEqual(sum(call.args[0]==self.folder/name for call in hashes.call_args_list),2)
        self.assertGreaterEqual(profile.call_count,2)
        packet=self.folder/'ANLZ0000.DAT';raw=packet.read_bytes();info=packet.stat()
        packet.write_bytes(raw[:-1]+bytes([raw[-1]^1]));os.utime(packet,ns=(info.st_atime_ns,info.st_mtime_ns))
        with mock.patch.object(store,'_connect',side_effect=AssertionError('Hash check must precede SQL')), \
                self.assertRaises(RecoveryRequired):
            store.load_plan(self.folder)
        packet.write_bytes(raw)

    def test_changed_raw_plan_reconstructs_and_one_paired_proof_inventories_each_side_once(self):
        raw=(self.folder/'plan.json').read_bytes();atomic_bytes(self.folder/'plan.json',raw+b'\n')
        with mock.patch.object(store,'_state',wraps=store._state) as states:
            expected=store.load_plan(self.folder)
        self.assertEqual(states.call_count,2)
        altered=json.loads(raw);altered['candidate']['scope']='tampered copied proposal'
        atomic_bytes(self.folder/'plan.json',_json(altered))
        with self.assertRaises(WriteProofError):store.load_plan(self.folder)
        atomic_bytes(self.folder/'plan.json',raw)
        self.assertEqual(store.load_plan(self.folder),expected)

    def test_preparation_inventories_each_private_side_once_without_an_extra_before_scan(self):
        with mock.patch.object(store,'_state',wraps=store._state) as states:
            source,authority,folder=self.prepare(self.root/'single-before-scan')
        self.assertEqual(states.call_count,4)  # One staged pair and one retained-payload proof.
        obj=store.AnalysisStore(authority,folder)
        self.assertEqual(obj.apply(),'123')

    def test_narrow_base_keeps_other_song_history_collisions_and_exact_target_cues(self):
        from soundbox import native_analysis_plan as planner
        from soundbox.rekordbox_write import _quote
        import shutil
        copied=self.root/'narrow-base.db';shutil.copyfile(self.folder/'before.db',copied)
        obj=self.instance();conn=_connect(copied)
        try:
            historical=dict(obj.plan['candidate']['changes'][1]['after'],ID='other-history',ContentID='unrelated',
                            UUID=obj.plan['seeds']['file_uuids']['.DAT'])
            conn.execute('INSERT INTO contentFile ('+','.join(_quote(key) for key in historical)+') VALUES ('+
                         ','.join('?' for _ in historical)+')',list(historical.values()))
            images={Path(name).suffix:(self.folder/name).read_bytes() for name in store.NAMES}
            def proposal():
                return planner.plan(store._base(conn,'123'),MediaIdentity.from_dict(obj.plan['audio']),images,
                    share_root=obj.plan['share'],seeds=obj.plan['seeds'],result=obj.plan['result'],
                    technical=obj.plan['technical'],native_length=True)
            with self.assertRaisesRegex(ConflictError,'history'):proposal()
            conn.execute("DELETE FROM contentFile WHERE ID='other-history'")
            conn.execute("INSERT INTO djmdCue VALUES ('other-cue','unrelated')")
            self.assertEqual(store._base(conn,'123')['cues'],[])
            conn.execute("INSERT INTO djmdCue VALUES ('target-cue','123')")
            self.assertEqual([cue['ID'] for cue in store._base(conn,'123')['cues']],['target-cue'])
            with self.assertRaises(ConflictError):proposal()
        finally:conn.close()

    def test_snapshot_reuses_only_inventory_of_fresh_exact_captures_and_detaches_results(self):
        obj=self.instance()
        with (mock.patch.object(store,'encrypted_backup',wraps=store.encrypted_backup) as captures,
              mock.patch.object(store,'_state',wraps=store._state) as inventories):
            first=obj._snapshot();expected=json.loads(json.dumps(first));first['tables']['djmdContent']='caller mutation'
            self.assertEqual(obj._snapshot(),expected);self.assertEqual(inventories.call_count,1)
            stamp=obj.database.stat();conn=_connect(obj.database)
            try:conn.execute("UPDATE unknown_table SET opaque=X'778899'")
            finally:conn.close()
            os.utime(obj.database,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            changed=obj._snapshot();self.assertNotEqual(changed,expected)
            self.assertEqual(obj._snapshot(),changed);self.assertEqual(inventories.call_count,2)
            self.assertEqual(captures.call_count,4)
            with self.assertRaises(ConflictError):obj.apply()

    def test_snapshot_inventory_failure_or_corrupt_source_never_uses_a_cached_proof(self):
        from sqlcipher3 import dbapi2 as cipher
        obj=self.instance()
        with mock.patch.object(store,'_state',side_effect=WriteProofError('inventory failed')):
            with self.assertRaisesRegex(WriteProofError,'inventory failed'):obj._snapshot()
        self.assertIsNone(getattr(obj,'_verified_snapshot_state',None))
        before=obj._snapshot();raw=obj.database.read_bytes();stamp=obj.database.stat()
        changed=bytearray(raw);changed[0]^=1;obj.database.write_bytes(changed)
        os.utime(obj.database,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        with self.assertRaises((WriteProofError,cipher.DatabaseError)):obj._snapshot()
        obj.database.write_bytes(raw)
        self.assertEqual(obj._snapshot(),before)

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

    def test_adaptive_saved_grid_retains_each_tempo_and_exact_terminal_row(self):
        from copy import deepcopy
        from dataclasses import replace
        from fractions import Fraction
        from desktop.native_analysis import readback
        from soundbox import native_analysis_plan as planner
        from soundbox.native_media import MediaIdentity
        from soundbox.rekordbox_grid import read_grid, replace_grid

        baseline=self.instance();result=deepcopy(baseline.plan['result'])
        result['bpm']=155.0;result['beats']=[];clock=Fraction(0)
        while clock<=32:
            index=len(result['beats']);tempo=155 if index<40 else 150
            result['beats'].append([index%4+1,int(clock*1000),float(tempo)])
            clock+=Fraction(60,tempo)
        operation=str(uuid4());folder=self.folder.parent/operation
        store.prepare(self.authority,'123',baseline.plan['track_uuid'],result,folder,
                      data_root=Path(baseline.plan['data_root']),operation_id=operation)
        obj=store.AnalysisStore(self.authority,folder)
        images={Path(name).suffix:(folder/name).read_bytes() for name in store.NAMES}
        grid=read_grid(images['.DAT'])
        self.assertEqual([[beat.number,beat.time_ms,beat.tempo/100] for beat in grid],result['beats'])
        conn=_connect(self.folder/'before.db',readonly=True)
        try:base=store._base(conn,'123')
        finally:conn.close()
        for change in ('tempo','time','terminal'):
            changed=list(grid)
            if change=='tempo':changed[41]=replace(changed[41],tempo=14900)
            elif change=='time':changed[41]=replace(changed[41],time_ms=changed[41].time_ms+1)
            else:changed.pop()
            altered={**images,'.DAT':replace_grid(images['.DAT'],tuple(changed))}
            with self.subTest(change=change),self.assertRaisesRegex(WriteProofError,'complete saved grid'):
                planner.plan(base,MediaIdentity.from_dict(obj.plan['audio']),altered,
                             share_root=obj.plan['share'],seeds=obj.plan['seeds'],result=result,
                             technical=obj.plan['technical'],native_length=True)
        audio=Path(result['path']);original=audio.read_bytes();xml=obj.xml.read_bytes()
        self.assertEqual(obj.apply(),'123');self.assertEqual(self.state(),obj.plan['after'])
        saved=readback(self.source/'master.db','123',obj.plan['track_uuid'])
        self.assertEqual(saved['row']['BPM'],15500)
        self.assertEqual(saved['beats'],result['beats'])
        self.assertEqual(audio.read_bytes(),original);self.assertEqual(obj.xml.read_bytes(),xml)
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

    def test_legacy_authority_wire_shape_and_existing_plan_replay_stay_exact(self):
        authority_path=self.root/'authority.json'
        fields=asdict(self.authority)
        self.assertEqual(set(fields),{'database','library_id','schema_sha256','accepted_reference_sha256',
                                      'accepted','application_version'})
        self.assertEqual(self.authority.profile,store.INITIAL_WAV_PROFILE)
        atomic_bytes(authority_path,_json(dict(format=store.AUTHORITY_FORMAT,**fields)))
        loaded=store.load_authority(authority_path,self.authority.database,self.authority.library_id)
        self.assertEqual(loaded,self.authority)
        obj=store.AnalysisStore(loaded,self.folder)
        original=(self.folder/'plan.json').read_bytes()
        self.assertEqual(obj.apply(),'123');self.assertEqual(obj.apply(),'already-completed')
        self.assertEqual((self.folder/'plan.json').read_bytes(),original)
        for profile in ('mp3','unknown',store.INITIAL_WAV_PROFILE):
            atomic_bytes(authority_path,_json(dict(format=store.AUTHORITY_FORMAT,profile=profile,**fields)))
            self.assertIsNone(store.load_authority(authority_path,self.authority.database,self.authority.library_id))

    def test_legacy_authority_refuses_mp3_before_renderer_and_plan_reconstruction(self):
        from copy import deepcopy
        from fractions import Fraction
        from soundbox.media_probe import MediaHeader
        original=store.load_plan(self.folder);result=deepcopy(original['result'])
        audio_path=self.root/'Unaccepted.mp3';audio_path.write_bytes(Path(result['path']).read_bytes())
        stat=audio_path.stat();result.update(path=str(audio_path),epoch=[stat.st_dev,stat.st_ino,stat.st_size,
                                                                      stat.st_mtime_ns,stat.st_ctime_ns])
        op=str(uuid4());folder=self.root/'case/appdata/native-analysis-plans'/op
        renderer=mock.Mock()
        with mock.patch.object(store,'read_media_header',return_value=MediaHeader('mp3','mp3',Fraction(32),44100,2,0)), \
                mock.patch.object(store.planner,'plan') as planner:
            with self.assertRaisesRegex(WriteProofError,'authority accepts only'):
                store.prepare(self.authority,'123',original['track_uuid'],result,folder,
                              data_root=self.root/'case/appdata',operation_id=op,renderer=renderer)
            renderer.assert_not_called();planner.assert_not_called()
        changed=deepcopy(original)
        changed['audio'].update(path=str(audio_path),family='mp3',codec='mp3',wave_format_tag=None)
        changed['technical']['bits']=0
        atomic_bytes(self.folder/'plan.json',_json(changed))
        with mock.patch.object(store.planner,'plan') as planner:
            with self.assertRaisesRegex(WriteProofError,'authority accepts only'):store.load_plan(self.folder)
            planner.assert_not_called()
        self.assertEqual(self.state(),original['before'])

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
