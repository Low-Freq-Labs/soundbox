"""Real owned source lane and durable analysis handoff with an isolated GUI."""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock
import json

from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication,QFrame
from desktop.native_analysis import NativeAnalysisController,KEY,DONE,digest,validate
from desktop import native_analysis as native
from desktop.tag_controller import TagEditController
from soundbox.analysis import ENGINE,result_key
from soundbox.library import Library,Track
from soundbox import native_media


class ScopedMetadataIdentityProof(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.path=(Path(self.area.name)/'source.wav').resolve();self.path.write_bytes(b'private fixture audio')
        self.record=dict(path=str(self.path),epoch=list(native_media.epoch(self.path.stat())),
            size=self.path.stat().st_size,sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.audio=native_media.MediaIdentity(str(self.path),tuple(self.record['epoch']),self.record['sha256'],
            32,'wav','pcm_s16le',32,1,1)
        self.path.chmod(0o600)

    def test_default_is_strict_and_context_cache_requires_fresh_full_epoch(self):
        with self.assertRaises(native.ConflictError):self.audio.revalidate()
        with mock.patch.object(native_media,'acquire_source',wraps=native_media.acquire_source) as capture:
            with native_media.metadata_identity_proof([self.record]):
                self.audio.revalidate();self.audio.revalidate()
                self.assertEqual(capture.call_count,1)
                self.path.chmod(0o644);self.audio.revalidate()
                self.assertEqual(capture.call_count,2)
            with self.assertRaises(native.ConflictError):self.audio.revalidate()
            with native_media.metadata_identity_proof([self.record]):self.audio.revalidate()
            self.assertEqual(capture.call_count,3)
        self.assertEqual(self.audio.epoch,tuple(self.record['epoch']))

    def test_context_cannot_authorize_another_digest_or_cross_threads(self):
        with native_media.metadata_identity_proof([{**self.record,'sha256':'0'*64}]), \
                mock.patch.object(native_media,'acquire_source',side_effect=AssertionError('Outside exact scope')):
            with self.assertRaises(native.ConflictError):self.audio.revalidate()
        errors=[]
        def other_thread():
            try:self.audio.revalidate()
            except native.ConflictError as exc:errors.append(str(exc))
        with native_media.metadata_identity_proof([self.record]):
            thread=threading.Thread(target=other_thread);thread.start();thread.join()
            self.assertEqual(errors,[native.CTIME_CONFLICT])

    def test_inode_and_mtime_changes_reject_before_source_capture(self):
        for kind in ('mtime','inode'):
            with self.subTest(kind=kind):
                stat=self.path.stat()
                if kind=='mtime':os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1))
                else:
                    replacement=self.path.with_name('replacement.wav');replacement.write_bytes(self.path.read_bytes())
                    os.utime(replacement,ns=(stat.st_atime_ns,self.record['epoch'][3]));replacement.replace(self.path)
                with native_media.metadata_identity_proof([self.record]), \
                        mock.patch.object(native_media,'acquire_source',side_effect=AssertionError('Changed identity')):
                    with self.assertRaises(native.ConflictError):self.audio.revalidate()

    def test_changing_ctime_proof_is_bounded_and_remains_pending(self):
        original=native_media.acquire_source;changes=[]
        @contextmanager
        def changing(*args,**kwargs):
            with original(*args,**kwargs) as captured:yield captured
            changes.append(True);self.path.chmod(0o644 if len(changes)%2 else 0o600)
        with native_media.metadata_identity_proof([self.record]), \
                mock.patch.object(native_media,'acquire_source',changing):
            with self.assertRaisesRegex(native.ConflictError,native.METADATA_PENDING):self.audio.revalidate()
        self.assertEqual(len(changes),2)


class NativeAnalysisQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup);root=Path(self.area.name)
        w=self.window=QFrame();w.settings={};w._shutting_down=False;w._refresh_worker=None;w._state_maintenance=False
        w._device_operation_owned=lambda:False;w._analysis_worker=None
        t=Track('sb-one','One','','file:///synthetic/one.wav',size=5644844,source_native_id='123',source_uuid='00000000-0000-4000-8000-000000000123')
        w.lib=Library({t.track_id:t},[],Path('/synthetic/master.db'),source_kind='rekordbox-db',source_library_id='456')
        self.record=dict(engine=ENGINE,path=str(t.path),size=t.size,sha256='0'*64,epoch=[1,42,t.size,100,100],
            duration=32.0,bpm=120.0,key='8B',beats=[[i%4+1,249+500*i,120.0] for i in range(64)],first_cue=None)
        w.settings['analysis_results']={result_key(t.path):deepcopy(self.record)}
        w.tag_controller=TagEditController(root,w,store_factory=lambda _:None);w.tag_controller.lib=w.lib;w.tag_controller.verified=True
        w.tag_controller._recover_pending=mock.Mock();self.persisted=[];self.save_ok=True
        def save():self.persisted.append(deepcopy(w.settings));return self.save_ok
        w._save_state=save;w._flash_status=mock.Mock();w._invalidate_rows=mock.Mock();w._refresh_analysis_row=mock.Mock()
        w._cache_library=mock.Mock();w._sync_grid=mock.Mock();w._sync_cue_editing=mock.Mock()
        w.grid_controller=SimpleNamespace(configure=mock.Mock());w.player_bar=SimpleNamespace(current_track=lambda:None)
        self.calls=[];self.block=None;self.error=None
        def factory(job):
            self.calls.append(deepcopy(job))
            self.assertTrue(self.persisted[-1][KEY]);self.assertTrue(w.tag_controller.owned)
            if self.block:self.block.wait(3)
            if self.error:raise self.error
            return dict(row=dict(BPM=12000,BitRate=1411,Length=32,SampleRate=44100,BitDepth=16),key='8B',analysis_path='/synthetic/share/ANLZ0000.DAT',
                cue_fields=dict(source_cues=[],source_cue_revision='verified-cues',source_cue_refs=[]),beats=deepcopy(self.record['beats']))
        self.controller=NativeAnalysisController(w,factory=factory);w.native_analysis_controller=self.controller
        patch=mock.patch('desktop.native_analysis.assert_rekordbox_closed');patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.close)

    def close(self):
        self.controller.stop()
        if not self.window.tag_controller.owner.pending:self.window.tag_controller.busy=False
        if self.block:self.block.set()
        self.window.tag_controller.shutdown();self.window.deleteLater();self.app.processEvents()

    def wait(self,condition):
        loop=QEventLoop();poll=QTimer();poll.setInterval(5);poll.timeout.connect(lambda:loop.quit() if condition() else None)
        limit=QTimer();limit.setSingleShot(True);limit.timeout.connect(loop.quit);poll.start();limit.start(5000)
        if not condition():loop.exec()
        poll.stop();limit.stop();self.assertTrue(condition())

    def add_second(self):
        original=self.window.lib.tracks_by_id['sb-one']
        track=replace(original,track_id='sb-two',name='Two',location_uri='file:///synthetic/two.wav',
            source_native_id='124',source_uuid='00000000-0000-4000-8000-000000000124')
        self.window.lib.tracks_by_id[track.track_id]=track
        record={**deepcopy(self.record),'path':str(track.path)}
        self.window.settings['analysis_results'][result_key(track.path)]=record
        return track

    def add_cbr(self):
        original=self.window.lib.tracks_by_id['sb-one']
        track=replace(original,track_id='sb-cbr',name='CBR',location_uri='file:///synthetic/cbr.mp3',
            source_native_id='125',source_uuid='00000000-0000-4000-8000-000000000125')
        self.window.lib.tracks_by_id[track.track_id]=track
        self.window.settings['analysis_results'][result_key(track.path)]={**deepcopy(self.record),'path':str(track.path)}
        return track

    def ctime_job(self, *, failed=False, prepared=True):
        path=(Path(self.area.name)/'one.wav').resolve();path.write_bytes(b'captured private fixture')
        original=self.window.lib.tracks_by_id['sb-one']
        track=replace(original,location_uri=path.as_uri(),size=path.stat().st_size)
        self.window.lib.tracks_by_id[track.track_id]=track
        self.record={**self.record,'path':str(path),'size':track.size,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'epoch':native.analysis.identity(path.stat())}
        self.window.settings['analysis_results']={result_key(path):deepcopy(self.record)}
        self.window.tag_controller.busy=True;self.controller.discover()
        job=self.controller.pending[0]
        if failed:job['error']=native.CTIME_CONFLICT
        self.controller.factory=None
        self.window._prepare_grid_cache=mock.Mock();self.window._track_analysis_dir=Path(self.area.name)/'cache'
        folder=self.window.tag_controller.data_root/'native-analysis-plans'/job['id']
        plan=dict(format=native.WAV_PLAN_FORMAT,operation_id=job['id'],source=job['source'],
            library_id=job['library_id'],track_id=job['native_id'],track_uuid=job['uuid'],
            result=native.publication_record(job['record']))
        if prepared:
            folder.mkdir(parents=True,mode=0o700);(folder/'plan.json').write_text(json.dumps(plan))
        path.chmod(0o600)
        self.assertNotEqual(native.analysis.identity(path.stat())[-1],self.record['epoch'][-1])
        return path,folder,plan

    @contextmanager
    def ctime_backend(self, folder, plan, *, recurring=False, recovery=None):
        events=[];test=self
        audio=native_media.MediaIdentity(self.record['path'],tuple(self.record['epoch']),self.record['sha256'],
            32,'wav','pcm_s16le',32,1,1)
        class Store:
            def __init__(self,authority,where):
                test.assertEqual(where,folder);self.plan=json.loads((where/'plan.json').read_bytes())
            def apply(self):
                events.append('apply')
                audio.revalidate()
                if recurring:Path(audio.path).chmod(0o644)
                audio.revalidate()
            def recover(self):
                events.append('recover');return recovery
            def discard_unstarted(self):
                raise AssertionError('Metadata drift must retain the immutable plan and recovery')
        def prepare(authority,tid,uuid,record,where,**kwargs):
            events.append('prepare');test.assertEqual(record,plan['result'])
            where.mkdir(parents=True,mode=0o700)
            (where/'plan.json').write_text(json.dumps(plan))
            audio.revalidate()
        saved=dict(row=dict(BPM=12000,BitRate=1411,Length=32,SampleRate=44100,BitDepth=16),key='8B',
            analysis_path='/synthetic/share/ANLZ0000.DAT',beats=deepcopy(self.record['beats']),
            cue_fields=dict(source_cues=[],source_cue_revision='verified-cues',source_cue_refs=[]))
        with mock.patch.object(native,'load_authority',return_value=object()), \
                mock.patch.object(native,'prepare',side_effect=prepare), \
                mock.patch.object(native,'AnalysisStore',Store),mock.patch.object(native,'retire_confirmed'), \
                mock.patch.object(native,'readback',return_value=saved), \
                mock.patch.object(native.track_analysis,'confirm_published_grid'):
            yield events

    def test_failed_ctime_plan_is_reconsidered_once_with_exact_bytes_and_original_result(self):
        path,folder,plan=self.ctime_job(failed=True)
        original=deepcopy(self.record);raw=(folder/'plan.json').read_bytes()
        with self.ctime_backend(folder,plan,recurring=True) as events, \
                mock.patch.object(native_media,'acquire_source',wraps=native_media.acquire_source) as proof:
            self.controller.discover()
            self.assertTrue(self.controller.pending[0]['ctime_rechecked'])
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            self.assertEqual(proof.call_count,2)
            self.assertTrue(all(call.args[0].sha256==original['sha256'] for call in proof.call_args_list))
        self.assertEqual(events,['apply']);self.assertEqual((folder/'plan.json').read_bytes(),raw)
        self.assertEqual(self.window.settings['analysis_results'][result_key(path)],original)
        self.assertEqual(self.window.settings[DONE]['sb-one'],digest(original))
        self.assertTrue(any(s[KEY] and s[KEY][0].get('ctime_rechecked') is True for s in self.persisted))

    def test_ctime_proof_covers_preparation_and_apply_without_rerender_or_new_epoch(self):
        _path,folder,plan=self.ctime_job(prepared=False)
        with self.ctime_backend(folder,plan) as events, \
                mock.patch.object(native_media,'acquire_source',wraps=native_media.acquire_source) as proof:
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            self.assertEqual(proof.call_count,1)
        self.assertEqual(events,['prepare','apply'])
        self.assertEqual(json.loads((folder/'plan.json').read_bytes())['result'],plan['result'])

    def test_changed_bytes_cannot_discard_or_reprepare_ctime_plan(self):
        path,folder,plan=self.ctime_job(failed=True)
        stat=path.stat();path.write_bytes(b'X'*stat.st_size)
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with self.ctime_backend(folder,plan) as events:
            self.controller.discover();self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
            self.assertIn('captured bytes differ',self.controller.pending[0]['error'])
        self.assertEqual(events,['apply']);self.assertTrue((folder/'plan.json').is_file())
        self.assertTrue(self.controller.pending[0]['ctime_rechecked'])

    def test_changed_mtime_does_not_read_audio_or_renew_ctime_plan(self):
        path,folder,plan=self.ctime_job(failed=True)
        stat=path.stat();os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1))
        with self.ctime_backend(folder,plan) as events, \
                mock.patch.object(native_media,'acquire_source',side_effect=AssertionError('Not ctime-only')):
            self.controller.discover();self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertEqual(events,['apply']);self.assertTrue((folder/'plan.json').is_file())

    def test_rechecked_nonmetadata_conflict_is_not_blindly_requeued_after_restart(self):
        path,folder,plan=self.ctime_job(failed=True)
        stat=path.stat();os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1))
        with self.ctime_backend(folder,plan) as events:
            self.controller.discover();self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
            before=deepcopy(self.controller.pending)
            self.controller.stop();self.controller=NativeAnalysisController(self.window)
            self.window.native_analysis_controller=self.controller
            for _ in range(3):self.controller.discover();self.controller.pump();self.app.processEvents()
            self.assertEqual(self.controller.pending,before)
        self.assertEqual(events,['apply']);self.assertTrue((folder/'plan.json').is_file())

    def test_old_error_reconsideration_must_be_durable_before_source_worker_admission(self):
        _path,folder,plan=self.ctime_job(failed=True)
        with self.ctime_backend(folder,plan) as events:
            self.save_ok=False;self.controller.discover()
            self.assertNotIn('ctime_rechecked',self.controller.pending[0])
            self.window.tag_controller.busy=False;self.controller.pump()
            self.assertEqual(events,[])
        for marker in (False,'again',None):
            with self.subTest(marker=marker),self.assertRaises(ValueError):
                validate([{**self.controller.pending[0],'ctime_rechecked':marker}])

    def test_metadata_still_changing_stays_quietly_pending_with_backoff(self):
        _path,folder,plan=self.ctime_job()
        @contextmanager
        def changing(_records):raise native.ConflictError(native.METADATA_PENDING);yield
        with self.ctime_backend(folder,plan) as events, \
                mock.patch.object(native,'metadata_identity_proof',changing):
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:self.controller.not_before>0 and not self.window.tag_controller.owned)
            self.assertEqual(self.controller.pending[0]['error'],'')
            self.assertEqual(self.window.tag_controller.analysis_update_status,'Verifying audio…')
            self.window._flash_status.assert_not_called()
            self.controller.pump();self.app.processEvents();self.assertEqual(events,[])

    def test_unfinished_detection_blocks_only_matching_native_publication_in_all_states(self):
        for state in ('queued','failed'):
            with self.subTest(state=state):
                track=self.window.lib.tracks_by_id['sb-one']
                jobs=native.analysis_jobs.admit([],self.window.lib,[track],native.analysis.DEFAULTS)
                if state=='failed':jobs=native.analysis_jobs.failed(jobs,jobs[0]['id'],'Key needs attention')
                self.window.settings[native.analysis_jobs.KEY]=jobs
                self.controller.discover();self.assertEqual(self.controller.pending,[])
        self.window.settings[native.analysis_jobs.KEY]=[]
        self.window.tag_controller.busy=True;self.controller.discover()
        preserved=deepcopy(self.controller.pending[0]);self.add_second()
        self.window.settings[native.analysis_jobs.KEY]=jobs
        self.controller.discover();self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:'sb-two' in self.window.settings.get(DONE,{}) and not self.window.tag_controller.owned)
        self.assertEqual(self.controller.pending,[preserved])
        self.assertEqual([j['local_id'] for j in self.calls],['sb-two'])

    def test_large_discovery_and_pump_validate_detection_queue_once_per_pass(self):
        w=self.window;first=w.lib.tracks_by_id['sb-one']
        tracks=[first]+[replace(first,track_id=f'fixture-{i}',location_uri=f'file:///synthetic/{i}.wav')
                        for i in range(3199)]
        w.lib.tracks_by_id={t.track_id:t for t in tracks}
        detection=native.analysis_jobs.admit([],w.lib,tracks[:100],{})
        w.settings[native.analysis_jobs.KEY]=detection;w.tag_controller.busy=True
        with mock.patch.object(native.analysis_jobs,'validate',wraps=native.analysis_jobs.validate) as checked:
            self.controller.discover();self.assertEqual(checked.call_count,1)
        self.assertEqual(self.controller.pending,[])
        w.settings[native.analysis_jobs.KEY]=[];self.controller.discover()
        self.assertEqual(len(self.controller.pending),1)
        w.settings[native.analysis_jobs.KEY]=detection
        with mock.patch.object(native.analysis_jobs,'validate',wraps=native.analysis_jobs.validate) as checked:
            self.controller.pump();self.assertEqual(checked.call_count,1)
        self.assertEqual(self.calls,[])

    def test_status_and_busy_churn_do_not_rescan_a_current_library_or_saved_results(self):
        self.window.tag_controller.busy=True;self.controller.discover();self.app.processEvents()
        class ReadyTracks(dict):
            def items(self):raise AssertionError('Status event traversed the library')
            def values(self):raise AssertionError('Status event traversed the library')
        self.window.lib.tracks_by_id=ReadyTracks(self.window.lib.tracks_by_id)
        with mock.patch.object(native.analysis,'saved_result',side_effect=AssertionError('Status event read saved analysis')):
            for count in range(5):
                self.window.tag_controller.metadata_update_status=f'Metadata phase {count}'
                self.window.tag_controller.changed.emit();self.app.processEvents()
        self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.calls,[])

    def test_metadata_snapshot_and_unrelated_settings_replacement_reuse_discovery_observation(self):
        self.window.tag_controller.busy=True;self.controller.discover();self.app.processEvents()
        self.window.lib=replace(self.window.lib,tracks_by_id={tid:replace(track,name='Saved name',artist='Saved artist')
            for tid,track in self.window.lib.tracks_by_id.items()})
        self.window.settings={**self.window.settings,'metadata_receipt_acks':[{'unrelated':'status'}]}
        with mock.patch.object(native.analysis,'saved_result',side_effect=AssertionError('Metadata re-ran analysis discovery')):
            self.window.tag_controller.configure(self.window.lib,True);self.app.processEvents()
        self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.calls,[])

    def test_new_result_owner_and_in_place_result_addition_both_wake_discovery(self):
        for replace_owner in (False,True):
            with self.subTest(replace_owner=replace_owner):
                self.window.tag_controller.busy=True
                self.window.settings.pop(KEY,None)
                self.window.settings['analysis_results']={};self.controller.discover();self.app.processEvents()
                self.assertEqual(self.controller.pending,[])
                if replace_owner:
                    self.window.settings['analysis_results']={result_key('/synthetic/one.wav'):deepcopy(self.record)}
                else:
                    self.window.settings['analysis_results'][result_key('/synthetic/one.wav')]=deepcopy(self.record)
                self.window.tag_controller.changed.emit();self.app.processEvents()
                self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.calls,[])

    def test_detection_and_prep_retirement_wake_candidate_discovery(self):
        w=self.window;w.tag_controller.busy=True;track=w.lib.tracks_by_id['sb-one']
        w.settings[native.analysis_jobs.KEY]=native.analysis_jobs.admit([],w.lib,[track],{})
        self.controller.discover();self.app.processEvents();self.assertEqual(self.controller.pending,[])
        w.settings[native.analysis_jobs.KEY]=[]
        w.settings['pending_prep_edits']=[{'track_id':'sb-one','error':'Prep needs attention'}]
        w.prep_controller=SimpleNamespace(job=lambda tid:next((job for job in
            w.settings['pending_prep_edits'] if job['track_id']==tid),None))
        w.tag_controller.changed.emit();self.app.processEvents();self.assertEqual(self.controller.pending,[])
        w.settings['pending_prep_edits']=[]
        w.tag_controller.changed.emit();self.app.processEvents();self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.calls,[])

    def test_explicit_discovery_still_observes_in_place_result_and_media_changes(self):
        self.window.tag_controller.busy=True
        record=self.window.settings['analysis_results'][result_key('/synthetic/one.wav')]
        record['key']=None;self.controller.discover();self.app.processEvents();self.assertEqual(self.controller.pending,[])
        record['key']='8B'
        self.add_second();self.controller.discover()
        self.assertEqual({job['local_id'] for job in self.controller.pending},{'sb-one','sb-two'})
        self.assertEqual(self.calls,[])

    def test_failed_scheduled_discovery_save_can_retry_without_another_input_mutation(self):
        w=self.window;w.tag_controller.busy=True
        w.settings['analysis_results']={};self.controller.discover();self.app.processEvents()
        w.settings['analysis_results']={result_key('/synthetic/one.wav'):deepcopy(self.record)}
        self.save_ok=False;w.tag_controller.changed.emit();self.app.processEvents()
        self.assertEqual(self.controller.pending,[])
        self.save_ok=True;w.tag_controller.changed.emit();self.app.processEvents()
        self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.calls,[])

    def test_changed_authority_file_wakes_discovery_but_does_not_cache_write_permission(self):
        w=self.window;w.tag_controller.busy=True;self.controller.factory=None
        authority=w.tag_controller.data_root/'rekordbox-analysis-authority.json'
        with mock.patch.object(native,'load_authority',return_value=None), \
                mock.patch.object(native.cbr,'load_authority',return_value=None):
            self.controller.discover();self.app.processEvents();self.assertEqual(self.controller.pending,[])
        authority.write_bytes(b'isolated scheduling marker')
        with mock.patch.object(native,'load_authority',return_value=object()), \
                mock.patch.object(native.cbr,'load_authority',return_value=None):
            w.tag_controller.changed.emit();self.app.processEvents();self.assertEqual(len(self.controller.pending),1)
        with mock.patch.object(native,'load_authority',return_value=None), \
                mock.patch.object(native.cbr,'load_authority',return_value=None):
            w.tag_controller.busy=False;self.controller.pump()
        self.assertIn('acceptance',self.controller.pending[0]['error'])
        self.assertEqual(self.calls,[])

    def test_discovery_failure_and_retry_reuse_unmodified_pending_analysis_record(self):
        class ImmutableRecord(dict):
            def __deepcopy__(self,_memo):raise AssertionError('Unrelated analysis payload was copied')
        self.window.tag_controller.busy=True;self.controller.discover()
        job=self.controller.pending[0];record=ImmutableRecord(job['record']);job['record']=record
        self.window._save_state=mock.Mock(return_value=True)
        self.controller.discover()
        self.assertIs(self.controller.pending[0]['record'],record)
        self.controller.fail('Synthetic refusal',job['id'])
        self.assertIs(self.controller.pending[0]['record'],record)
        self.controller.retry()
        self.assertIs(self.controller.pending[0]['record'],record)

    def test_detection_index_is_fresh_and_keeps_exact_source_path_uuid_matching(self):
        w=self.window;track=w.lib.tracks_by_id['sb-one'];w.tag_controller.busy=True
        job=native.analysis_jobs.admit([],w.lib,[track],{})[0]
        for change in ({'source':'other'},{'library_id':'other'},{'track_id':'other'},
                       {'path':'/synthetic/other.wav'},{'source_uuid':'other'}):
            with self.subTest(change=change):
                w.settings[KEY]=[];w.settings[native.analysis_jobs.KEY]=[deepcopy(job)]
                self.controller.discover();self.assertEqual(self.controller.pending,[])
                w.settings[native.analysis_jobs.KEY]=[{**job,**change}]
                self.controller.discover();self.assertEqual(len(self.controller.pending),1)
        legacy={**job,'source_uuid':''}
        w.settings[KEY]=[];w.settings[native.analysis_jobs.KEY]=[legacy]
        self.controller.discover();self.assertEqual(self.controller.pending,[])

    def test_corrupt_detection_queue_keeps_publication_blocked_without_rewriting(self):
        w=self.window;track=w.lib.tracks_by_id['sb-one'];w.tag_controller.busy=True
        job=native.analysis_jobs.admit([],w.lib,[track],{})[0]
        for queue in ([job,deepcopy(job)],[{**job,'state':'completed'}],{'broken':True}):
            with self.subTest(queue=queue):
                before=deepcopy(queue);w.settings[native.analysis_jobs.KEY]=queue
                self.controller.discover();self.assertEqual(self.controller.pending,[])
                self.assertEqual(w.settings[native.analysis_jobs.KEY],before)
        self.assertEqual(self.calls,[])

    def test_pending_analysis_priority_defers_publication_until_detector_can_resume(self):
        self.window._analysis_priority_pending=lambda:True
        self.controller.discover();self.app.processEvents();self.assertEqual(self.calls,[])
        self.assertTrue(self.controller.pending)
        self.window._analysis_priority_pending=lambda:False
        self.controller.pump();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.calls),1)

    def test_source_turn_publishes_ready_song_while_other_song_keeps_detection_dependency(self):
        from types import MethodType
        from desktop.analysis_priority import AnalysisPriorityMixin
        w=self.window;tags=w.tag_controller
        tags.busy=True;self.add_second();self.controller.discover()
        track=w.lib.tracks_by_id['sb-one']
        detection=native.analysis_jobs.admit([],w.lib,[track],native.analysis.DEFAULTS)
        w.settings[native.analysis_jobs.KEY]=detection
        w._analysis_priority_pending=lambda:True
        w._analysis_source_priority=MethodType(AnalysisPriorityMixin._analysis_source_priority,w)
        w._analysis_source_admitted=MethodType(AnalysisPriorityMixin._analysis_source_admitted,w)
        tags.analysis_priority=w._analysis_priority_pending
        tags.analysis_admission=w._analysis_source_priority;tags.source_admitted=w._analysis_source_admitted
        tags.busy=False
        AnalysisPriorityMixin._analysis_offer_source_turn(w,w.lib)
        self.wait(lambda:'sb-two' in w.settings.get(DONE,{}) and not tags.owned)
        self.assertEqual([j['local_id'] for j in self.calls],['sb-two'])
        self.assertEqual(self.controller.pending[0]['local_id'],'sb-one')
        self.assertEqual(w.settings[native.analysis_jobs.KEY],detection)

    def test_committed_initial_analysis_acknowledges_while_newer_key_job_is_failed(self):
        path,folder,plan=self.ctime_job()
        track=self.window.lib.tracks_by_id['sb-one']
        detected=native.analysis_jobs.admit([],self.window.lib,[track],native.analysis.DEFAULTS)
        detected=native.analysis_jobs.advance(detected,detected[0]['id'],self.record['sha256'])
        detected=native.analysis_jobs.failed(detected,detected[0]['id'],'New key detection failed')
        self.window.settings[native.analysis_jobs.KEY]=deepcopy(detected)
        newer=deepcopy(self.record)
        newer['grid_draft']=dict(origin='manual',base_sha256=newer['sha256'],revision=1,bpm=121.0,
            beats=[[n,ms,121.0] for n,ms,_bpm in newer['beats']])
        self.window.settings['analysis_results'][result_key(path)]=newer
        with self.ctime_backend(folder,plan,recovery='committed') as events, \
                mock.patch.object(native,'prepare',side_effect=AssertionError('Receipt replay must not prepare')):
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(events,['recover'])
        self.window.tag_controller._recover_pending.assert_called_once_with(self.window.lib)
        self.assertEqual(self.window.settings[native.analysis_jobs.KEY],detected)
        self.assertEqual(self.window.settings['analysis_results'][result_key(path)],newer)
        self.assertEqual(self.window.settings[DONE]['sb-one'],digest(self.record))

    def test_unstarted_prepared_analysis_is_checked_once_without_apply_during_detection(self):
        _path,folder,plan=self.ctime_job()
        track=self.window.lib.tracks_by_id['sb-one']
        detected=native.analysis_jobs.admit([],self.window.lib,[track],native.analysis.DEFAULTS)
        self.window.settings[native.analysis_jobs.KEY]=detected
        self.window._analysis_priority_pending=lambda:True
        with self.ctime_backend(folder,plan) as events, \
                mock.patch.object(native,'prepare',side_effect=AssertionError('Wait for terminal detection')):
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:bool(self.controller._detection_reconciled) and not self.window.tag_controller.owned)
            for _ in range(4):self.controller.pump();self.app.processEvents()
        self.assertEqual(events,['recover'])
        self.assertTrue(self.controller.pending);self.assertEqual(self.controller.pending[0]['error'],'')
        self.assertNotIn(DONE,self.window.settings)

    def test_no_prepared_plan_checks_recovery_once_then_healthy_job_waits_for_global_priority(self):
        self.window.tag_controller.busy=True;self.controller.discover();self.add_second();self.controller.discover()
        track=self.window.lib.tracks_by_id['sb-one']
        self.window.settings[native.analysis_jobs.KEY]=native.analysis_jobs.admit(
            [],self.window.lib,[track],native.analysis.DEFAULTS)
        self.window._analysis_priority_pending=lambda:True
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:bool(self.controller._detection_reconciled) and not self.window.tag_controller.owned)
        for _ in range(4):self.controller.pump();self.app.processEvents()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.window.tag_controller._recover_pending.call_count,1)
        self.window._analysis_priority_pending=lambda:False
        self.controller.pump()
        self.wait(lambda:'sb-two' in self.window.settings.get(DONE,{}) and not self.window.tag_controller.owned)
        self.assertEqual([j['local_id'] for j in self.calls],['sb-two'])
        self.assertEqual(self.controller.pending[0]['local_id'],'sb-one')

    def test_mixed_wav_cbr_fifo_persists_explicit_profile_before_owned_work(self):
        self.window.tag_controller.busy=True;self.add_cbr();self.controller.discover()
        queued=deepcopy(self.controller.pending)
        self.assertNotIn('profile',queued[0]);self.assertEqual(queued[1]['profile'],native.cbr.PROFILE)
        self.assertEqual(self.persisted[-1][KEY],queued);validate(queued)
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([job['id'] for job in self.calls],[job['id'] for job in queued])
        self.assertEqual(self.window._refresh_analysis_row.call_args_list,
                         [mock.call('sb-one'),mock.call('sb-cbr')])

    def test_cbr_restart_keeps_profile_identity_and_newer_local_draft(self):
        self.window.tag_controller.busy=True;track=self.add_cbr();self.controller.discover()
        wav=self.controller.pending.pop(0);job=deepcopy(self.controller.pending[0])
        self.window.settings['analysis_results'].pop(result_key('/synthetic/one.wav'))
        factory=self.controller.factory;self.controller.stop()
        self.controller=NativeAnalysisController(self.window,factory=factory)
        self.window.native_analysis_controller=self.controller
        self.block=threading.Event();self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:bool(self.calls))
        newer=deepcopy(self.window.settings['analysis_results'][result_key(track.path)])
        newer['grid_draft']=dict(origin='manual',base_sha256=newer['sha256'],revision=1,bpm=121.0,
            beats=[[n,ms+1,bpm] for n,ms,bpm in newer['beats']])
        self.window.settings['analysis_results'][result_key(track.path)]=newer
        self.block.set();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(self.calls,[job]);self.assertEqual(job['profile'],native.cbr.PROFILE)
        self.assertEqual(self.window.settings['analysis_results'][result_key(track.path)],newer)
        self.assertEqual(self.window.settings[DONE][track.track_id],digest(job['record']))
        self.assertNotIn(wav['local_id'],self.window.settings[DONE])

    def test_absent_cbr_authority_keeps_existing_intent_without_wav_fallback(self):
        self.window.tag_controller.busy=True;self.add_cbr();self.controller.discover()
        self.window.settings[KEY]=[deepcopy(self.controller.pending[1])];identity=self.controller.pending[0]['id']
        self.window.settings['analysis_results'].pop(result_key('/synthetic/one.wav'))
        self.controller.factory=None;self.window.tag_controller.busy=False
        with mock.patch.object(native,'load_authority',return_value=object()), \
                mock.patch.object(native.cbr,'load_authority',return_value=None) as cbr, \
                mock.patch.object(native,'prepare') as wav_prepare:
            self.controller.pump();self.app.processEvents()
        wav_prepare.assert_not_called();cbr.assert_called()
        self.assertEqual(self.controller.pending[0]['id'],identity)
        self.assertIn('acceptance',self.controller.pending[0]['error']);self.assertEqual(self.calls,[])

    def test_new_cbr_discovery_waits_for_its_own_authority_and_unknown_profiles_fail(self):
        self.add_cbr();self.controller.factory=None;self.window.tag_controller.busy=True
        with mock.patch.object(native,'load_authority',return_value=object()), \
                mock.patch.object(native.cbr,'load_authority',return_value=None):
            self.controller.discover()
        self.assertEqual([job['local_id'] for job in self.controller.pending],['sb-one'])
        original=deepcopy(self.controller.pending[0]);bad=deepcopy(original);bad['profile']='unaccepted-vbr'
        with self.assertRaises(ValueError):validate([bad])
        self.assertEqual(self.window.settings['analysis_results'][result_key('/synthetic/cbr.mp3')]['key'],'8B')

    def test_real_routing_uses_concrete_format_and_keeps_legacy_mp3_intent_on_wav(self):
        self.window.tag_controller.busy=True;self.add_cbr();self.controller.discover()
        legacy=deepcopy(self.controller.pending[1]);legacy.pop('profile')
        self.window.settings[KEY]=[legacy];self.controller.factory=None
        self.window.settings['analysis_results'].pop(result_key('/synthetic/one.wav'))
        self.window._prepare_grid_cache=mock.Mock();self.window._track_analysis_dir=Path(self.area.name)/'cache'
        events=[];wav_authority=object();cbr_authority=object()
        result=dict(row=dict(BPM=12000,BitRate=1411,Length=32,SampleRate=44100,BitDepth=16),key='8B',
            analysis_path='/synthetic/share/ANLZ0000.DAT',beats=deepcopy(self.record['beats']),
            cue_fields=dict(source_cues=[],source_cue_revision='verified-cues',source_cue_refs=[]))
        def prepare(authority,tid,uuid,record,folder,**kwargs):
            events.append(('prepare-wav',authority));folder.mkdir(parents=True,mode=0o700)
            (folder/'plan.json').write_text(json.dumps(dict(format=native.WAV_PLAN_FORMAT,operation_id=legacy['id'],
                source=legacy['source'],library_id=legacy['library_id'],track_id=tid,track_uuid=uuid,result=record)))
        class Store:
            def __init__(self,authority,folder):
                events.append(('store-wav',authority));self.plan=json.loads((folder/'plan.json').read_bytes())
            def apply(self):events.append(('apply-wav',None))
        with mock.patch.object(native,'load_authority',return_value=wav_authority), \
                mock.patch.object(native.cbr,'load_authority',return_value=cbr_authority), \
                mock.patch.object(native,'prepare',side_effect=prepare), \
                mock.patch.object(native,'load_plan',side_effect=AssertionError('Store already verified the full plan')), \
                mock.patch.object(native,'AnalysisStore',Store),mock.patch.object(native,'retire_confirmed'), \
                mock.patch.object(native,'readback',return_value=result), \
                mock.patch.object(native.track_analysis,'confirm_published_grid'), \
                mock.patch.object(native.cbr,'prepare') as forbidden:
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            forbidden.assert_not_called()
        self.assertEqual(events,[('prepare-wav',wav_authority),('store-wav',wav_authority),('apply-wav',None)])
        folder=self.window.tag_controller.data_root/'native-analysis-plans'/legacy['id']
        with self.assertRaisesRegex(native.WriteProofError,'profile differs'):
            native.prepared_publication(folder,native.cbr.PROFILE)

    def test_externally_analyzed_cbr_retains_failed_intent_and_other_song_can_finish(self):
        self.window.tag_controller.busy=True;track=self.add_cbr();self.controller.discover()
        self.window.settings[KEY].reverse();original=self.controller.factory
        def rejected(job):
            if job.get('profile')==native.cbr.PROFILE:
                raise native.ConflictError('CBR components require an unchanged bare unlocked native registration')
            return original(job)
        self.controller.factory=rejected;self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:'sb-one' in self.window.settings.get(DONE,{}) and not self.window.tag_controller.owned)
        self.assertEqual(len(self.controller.pending),1)
        self.assertEqual(self.controller.pending[0]['local_id'],track.track_id)
        self.assertIn('unchanged bare',self.controller.pending[0]['error'])
        self.assertNotIn(track.track_id,self.window.settings[DONE])

    def test_concrete_cbr_receipt_replays_without_preparing_and_local_ack_retry_keeps_identity(self):
        self.window.tag_controller.busy=True;self.add_cbr();self.controller.discover()
        job=deepcopy(self.controller.pending[1]);self.window.settings[KEY]=[job];self.controller.factory=None
        folder=self.window.tag_controller.data_root/'native-analysis-plans'/job['id'];folder.mkdir(parents=True,mode=0o700)
        plan=dict(format=native.cbr.PLAN_FORMAT,operation_id=job['id'],source=job['source'],
                  library_id=job['library_id'],track_id=job['native_id'],track_uuid=job['uuid'],record='unused',
                  result=native.publication_record(job['record']))
        (folder/'plan.json').write_text(json.dumps(plan))
        self.window._prepare_grid_cache=mock.Mock();self.window._track_analysis_dir=Path(self.area.name)/'cache'
        saved=dict(row=dict(BPM=12000,BitRate=320,Length=32,SampleRate=44100,BitDepth=16),key='8B',
            analysis_path='/synthetic/cbr/ANLZ0000.DAT',beats=deepcopy(job['record']['beats']),
            cue_fields=dict(source_cues=[],source_cue_revision='verified-cues',source_cue_refs=[]))
        authority=object();store=mock.Mock();store.plan=plan;store.apply.return_value='already-completed'
        original=self.window._save_state;failed=[False]
        def save():
            if KEY in self.window.settings and not self.window.settings[KEY] and not failed[0]:
                failed[0]=True;return False
            return original()
        self.window._save_state=save
        with mock.patch.object(native.cbr,'load_authority',return_value=authority), \
                mock.patch.object(native.cbr,'load_plan',side_effect=AssertionError('Store already verified the full plan')), \
                mock.patch.object(native.cbr,'CBRAnalysisStore',return_value=store) as constructor, \
                mock.patch.object(native.cbr,'prepare') as prepare, \
                mock.patch.object(native.cbr,'retire_confirmed'), \
                mock.patch.object(native,'readback',return_value=saved), \
                mock.patch.object(native.track_analysis,'confirm_published_grid'), \
                mock.patch.object(native,'AnalysisStore') as wav:
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:self.controller.pending and bool(self.controller.pending[0]['error'])
                      and not self.window.tag_controller.owned)
            self.assertEqual(self.controller.pending[0]['id'],job['id'])
            self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
            self.assertEqual(constructor.call_args_list,[mock.call(authority,folder)]*2)
            self.assertEqual(store.apply.call_count,2);prepare.assert_not_called();wav.assert_not_called()
        self.assertEqual(self.window.settings[DONE]['sb-cbr'],digest(job['record']))
        self.assertEqual(self.window.lib.tracks_by_id['sb-cbr'].bitrate,'320')
        self.window._refresh_analysis_row.assert_called_once_with('sb-cbr')

    def test_constructor_verified_plan_must_match_durable_job_before_any_apply(self):
        self.window.tag_controller.busy=True;self.add_cbr();self.controller.discover()
        job=deepcopy(self.controller.pending[1]);self.window.settings[KEY]=[job];self.controller.factory=None
        folder=self.window.tag_controller.data_root/'native-analysis-plans'/job['id'];folder.mkdir(parents=True,mode=0o700)
        plan=dict(format=native.cbr.PLAN_FORMAT,operation_id=job['id'],source=job['source'],
            library_id=job['library_id'],track_id=job['native_id'],track_uuid=job['uuid'],
            result=native.publication_record(job['record']))
        (folder/'plan.json').write_text(json.dumps(plan));store=mock.Mock();store.plan={**plan,'track_id':'999'}
        with mock.patch.object(native.cbr,'load_authority',return_value=object()), \
                mock.patch.object(native.cbr,'CBRAnalysisStore',return_value=store):
            self.window.tag_controller.busy=False;self.controller.pump()
            self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        store.apply.assert_not_called();self.assertEqual(self.controller.pending[0]['id'],job['id'])
        self.assertIn('durable song intent',self.controller.pending[0]['error'])

    def test_failed_song_does_not_block_another_import_or_lose_its_intent(self):
        self.window.tag_controller.busy=True;self.controller.discover()
        failed=self.controller.pending[0];failed['error']='Audio bytes changed'
        before=deepcopy(failed);track=self.add_second();self.controller.discover()
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:track.track_id in self.window.settings.get(DONE,{}) and not self.window.tag_controller.owned)
        self.assertEqual([j['local_id'] for j in self.calls],['sb-two'])
        self.assertEqual(self.controller.pending,[before])
        self.assertFalse(self.window.lib.tracks_by_id['sb-one'].source_analysis_path)
        self.assertNotIn('sb-one',self.window.settings[DONE])
        self.assertTrue(self.window.lib.tracks_by_id['sb-two'].source_analysis_path)

    def test_second_failed_song_marks_its_own_intent_and_preserves_first_failure(self):
        self.window.tag_controller.busy=True;self.controller.discover()
        self.controller.pending[0]['error']='First failed';before=deepcopy(self.controller.pending[0])
        self.add_second();self.controller.discover();self.error=RuntimeError('Second failed')
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:all(j['error'] for j in self.controller.pending) and not self.window.tag_controller.owned)
        self.assertEqual(self.controller.pending[0],before)
        self.assertEqual(self.controller.pending[1]['error'],'Second failed')
        self.assertEqual([j['local_id'] for j in self.calls],['sb-two'])

    def test_obsolete_uniform_grid_refusal_is_revalidated_once_without_manual_retry(self):
        self.window.tag_controller.busy=True;self.controller.discover()
        job=self.controller.pending[0];identity=job['id']
        job['error']='Packet grid must equal the complete saved grid, including its exact endpoint'
        self.controller.discover()
        self.assertFalse(self.controller.pending[0]['error'])
        self.assertEqual(self.controller.pending[0]['id'],identity)
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([j['id'] for j in self.calls],[identity])

    def test_failed_retry_persistence_can_revalidate_when_storage_recovers(self):
        self.window.tag_controller.busy=True;self.controller.discover()
        job=self.controller.pending[0]
        job['error']='Packet grid must equal the complete saved grid, including its exact endpoint'
        self.save_ok=False;self.controller.discover()
        self.assertTrue(self.controller.pending[0]['error'])
        self.save_ok=True;self.controller.discover()
        self.assertFalse(self.controller.pending[0]['error'])
        self.window.tag_controller.busy=False;self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.calls),1)

    def test_real_grid_mismatch_is_not_retried_in_a_loop(self):
        message='Packet grid must equal the complete saved grid, including its exact endpoint'
        self.error=RuntimeError(message);self.controller.discover()
        self.wait(lambda:len(self.calls)==2 and self.controller.pending[0]['error'] and not self.window.tag_controller.owned)
        for _ in range(5):
            self.controller.discover();self.controller.pump();self.app.processEvents()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.controller.pending[0]['error'],message)

    def test_finished_result_is_durable_before_worker_and_updates_only_the_finished_song(self):
        old=deepcopy(self.record);self.controller.discover()
        self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.window.lib.tracks_by_id['sb-one'].source_analysis_path,'/synthetic/share/ANLZ0000.DAT')
        self.assertEqual(self.window.settings[DONE]['sb-one'],digest(old))
        self.assertEqual(self.window.settings['analysis_results'][result_key('/synthetic/one.wav')],old)
        self.window._refresh_analysis_row.assert_called_once_with('sb-one')
        self.controller.discover();self.app.processEvents();self.assertEqual(len(self.calls),1)

    def test_persistence_failure_never_admits_source_work_and_shutdown_keeps_unstarted_intent(self):
        self.save_ok=False;self.controller.discover();self.app.processEvents();self.assertEqual(self.calls,[])
        self.assertEqual(self.controller.pending,[])
        self.save_ok=True;self.window.tag_controller.busy=True;self.controller.discover()
        self.assertEqual(len(self.controller.pending),1)
        before=deepcopy(self.controller.pending);self.controller.stop();self.app.processEvents()
        self.assertEqual(self.controller.pending,before);self.assertEqual(self.calls,[])
        self.window.tag_controller.busy=False

    def test_native_owner_waits_without_redetection_then_resumes(self):
        from soundbox.rekordbox_write import WriteProofError
        with mock.patch('desktop.native_analysis.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')):
            self.controller.discover();self.wait(lambda:self.controller.timer.isActive() and not self.window.tag_controller.owned)
        self.assertEqual(len(self.controller.pending),1);self.assertEqual(self.calls,[])
        self.controller.not_before=0;self.controller.pump();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual(len(self.calls),1)

    def test_other_source_identity_or_failed_publication_stays_durable(self):
        self.error=RuntimeError('Changed audio');self.controller.discover()
        self.wait(lambda:self.controller.pending and self.controller.pending[0]['error'] and not self.window.tag_controller.owned)
        self.assertIn('Changed audio',self.controller.pending[0]['error'])
        self.assertFalse(self.window.lib.tracks_by_id['sb-one'].source_analysis_path)
        self.assertNotIn(DONE,self.window.settings)

    def test_partial_analysis_or_existing_native_grid_is_never_overwritten(self):
        self.window.settings['analysis_results'][result_key('/synthetic/one.wav')]['key']=None
        self.controller.discover();self.app.processEvents();self.assertFalse(self.controller.pending)
        self.window.settings['analysis_results'][result_key('/synthetic/one.wav')]['key']='8B'
        self.window.lib.tracks_by_id['sb-one'].source_analysis_path='/existing/native.DAT'
        self.controller.discover();self.app.processEvents();self.assertEqual(self.calls,[])

    def test_waiting_owner_does_not_spin_the_source_lane(self):
        from soundbox.rekordbox_write import WriteProofError
        with mock.patch('desktop.native_analysis.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')) as closed:
            self.controller.discover();self.wait(lambda:self.controller.not_before>0 and not self.window.tag_controller.owned)
            count=closed.call_count
            for _ in range(5):self.controller.pump();self.app.processEvents()
            self.assertEqual(closed.call_count,count)
        self.assertEqual(self.calls,[])

    def test_saved_native_result_with_failed_local_receipt_replays_the_same_intent(self):
        opaque={'nested':['preserve alias']}
        self.window.settings.update(future=opaque,alias=opaque)
        originals=self.window.settings['analysis_results']
        original=self.window._save_state;failed=[False]
        def save():
            if KEY in self.window.settings and not self.window.settings[KEY] and not failed[0]:
                failed[0]=True;return False
            return original()
        self.window._save_state=save
        self.controller.discover();self.wait(lambda:self.controller.pending and self.controller.pending[0]['error'] and not self.window.tag_controller.owned)
        self.assertIs(self.window.settings['future'],opaque)
        self.assertIs(self.window.settings['alias'],opaque)
        self.assertIs(self.window.settings['analysis_results'],originals)
        operation=self.controller.pending[0]['id'];self.assertEqual(self.calls[0]['id'],operation)
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([j['id'] for j in self.calls],[operation,operation])
        self.assertEqual(self.window.settings[DONE]['sb-one'],digest(self.record))
        self.assertIs(self.window.settings['future'],opaque)

    def test_completed_source_worker_persists_receipt_while_quitting(self):
        self.block=threading.Event();self.controller.discover();self.wait(lambda:bool(self.calls))
        self.controller.stop();self.window._shutting_down=True;self.block.set()
        self.wait(lambda:not self.window.tag_controller.owned)
        self.assertFalse(self.controller.pending);self.assertIn('sb-one',self.window.settings[DONE])
        self.window._refresh_analysis_row.assert_not_called()

    def test_local_receipt_exception_restores_namespaces_and_keeps_committed_intent(self):
        original=self.window._save_state;raised=False
        opaque={'nested':['exact']};self.window.settings.update(future=opaque,alias=opaque)
        originals=self.window.settings['analysis_results']
        def save():
            nonlocal raised
            if KEY in self.window.settings and not self.window.settings[KEY] and not raised:
                raised=True;raise OSError('Synthetic receipt storage failure')
            return original()
        self.window._save_state=save
        self.controller.discover()
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']) and not self.window.tag_controller.owned)
        self.assertTrue(raised);self.assertNotIn('sb-one',self.window.settings.get(DONE,{}))
        self.assertIs(self.window.settings['analysis_results'],originals)
        self.assertIs(self.window.settings['future'],opaque);self.assertIs(self.window.settings['alias'],opaque)
        self.assertEqual(len(self.calls),1)
