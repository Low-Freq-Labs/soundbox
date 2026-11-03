"""Real owned source lane and durable analysis handoff with an isolated GUI."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication,QFrame
from desktop.native_analysis import NativeAnalysisController,KEY,DONE,digest
from desktop.tag_controller import TagEditController
from soundbox.analysis import ENGINE,result_key
from soundbox.library import Library,Track


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
        original=self.window._save_state;failed=[False]
        def save():
            if KEY in self.window.settings and not self.window.settings[KEY] and not failed[0]:
                failed[0]=True;return False
            return original()
        self.window._save_state=save
        self.controller.discover();self.wait(lambda:self.controller.pending and self.controller.pending[0]['error'] and not self.window.tag_controller.owned)
        operation=self.controller.pending[0]['id'];self.assertEqual(self.calls[0]['id'],operation)
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.window.tag_controller.owned)
        self.assertEqual([j['id'] for j in self.calls],[operation,operation])
        self.assertEqual(self.window.settings[DONE]['sb-one'],digest(self.record))

    def test_completed_source_worker_persists_receipt_while_quitting(self):
        self.block=threading.Event();self.controller.discover();self.wait(lambda:bool(self.calls))
        self.controller.stop();self.window._shutting_down=True;self.block.set()
        self.wait(lambda:not self.window.tag_controller.owned)
        self.assertFalse(self.controller.pending);self.assertIn('sb-one',self.window.settings[DONE])
        self.window._refresh_analysis_row.assert_not_called()
