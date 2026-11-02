"""Owned Qt source lane, real native planners, durable replay; no live library."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from desktop.prep_controller import PrepEditController
from soundbox import analysis, prep_edits, rekordbox_db
from soundbox.rekordbox_grid import Beat, read_grid, replace_grid
import test_rekordbox_grid as grid_fixture
from soundbox.rekordbox_write import WriteProofError
from soundbox.state_io import atomic_bytes
import test_prep_grid as prep_fixture


class DurablePrep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);prep_fixture.GridInterface.setUpClass()

    def setUp(self):
        n=self.native=prep_fixture.GridInterface();n.setUp();self.addCleanup(n.doCleanups)
        root=n.shared.data_root
        audio=root/'audio.wav';audio.write_bytes(b'private native-planner fixture')
        source=root/'master.db';source.write_bytes(b'not a database; native stores are isolated planners')
        n.state.track['FolderPath']=str(audio)
        original=next(iter(n.shared.lib.tracks_by_id.values()));self.tid=original.track_id
        track=replace(original,location_uri=audio.as_uri(),source_native_id=original.track_id,
                      **rekordbox_db.cue_snapshot_fields(n.state))
        lib=replace(n.shared.lib,source=source,tracks_by_id={self.tid:track})
        n.shared.lib=lib;n.shared._recover_pending=mock.Mock();n.grid.current='';n.grid.configure()
        w=self.window=QFrame();w.lib=lib;w.tag_controller=n.shared;w.grid_controller=n.grid
        w._shutting_down=False;w._state_maintenance=False;w._refresh_worker=None
        w._device_operation_owned=lambda:False;w._track_analysis_dir=root/'analysis-cache'
        rows=[[b.number,b.time_ms,b.tempo/100] for b in read_grid(n.dat)]
        duration=math.ceil(rows[-1][1]/1000)+2
        record=dict(engine=analysis.ENGINE,path=str(audio),epoch=analysis.identity(audio.stat()),
            size=audio.stat().st_size,sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
            duration=duration,bpm=90,key='8A',first_cue=None,beats=deepcopy(rows))
        w.settings={'analysis_results':{analysis.result_key(audio):record}}
        w._analysis_result=lambda t:analysis.saved_result(w.settings['analysis_results'],t)
        pb=w.player_bar=SimpleNamespace(current_track=lambda:self.tid,_dur=int(duration*1000),
            _analysis_parts={'beats':deepcopy(rows),'provenance':{'grid':'rekordbox'}},cue_panel=SimpleNamespace(_loaded=True))
        def grid(tid,path,beats,bpm):pb._analysis_parts['beats']=deepcopy(beats)
        pb.refresh_live_grid=grid;pb.refresh_live_cues=mock.Mock()
        for name in ('_invalidate_rows','_update_browser_bpm','_sync_grid','_sync_cue_editing','_prepare_grid_cache','_cache_library','_finish_edit_status','_flash_status'):
            setattr(w,name,mock.Mock())
        self.persisted=[];self.fail_after_commit=False
        def save():
            queue=w.settings.get(prep_edits.KEY,[])
            if self.fail_after_commit and self.writes and not queue:
                self.fail_after_commit=False;return False
            self.persisted.append(deepcopy(w.settings));atomic_bytes(root/'state.json',json.dumps(w.settings).encode());return True
        w._save_state=save;self.writes=[];self.gate=None
        grid_factory=n.grid._factory;cue_factory=n.shared._cue_store_factory
        outer=self
        class Store:
            def __init__(self,delegate,kind):self.delegate=delegate;self.kind=kind
            def snapshot(self):return self.delegate.snapshot()
            def apply(self,request):
                # No source mutation starts before the exact request is durable.
                durable=json.loads((root/'state.json').read_bytes())
                assert durable[prep_edits.KEY][0]['prepared']['request']==json.loads(json.dumps(asdict(request)))
                outer.writes.append((self.kind,request))
                if outer.gate:outer.gate.wait(5)
                self.delegate.apply(request)
                receipt=dict(format='soundbox-native-grid-v1' if self.kind=='grid' else 'soundbox-native-cues-v1',
                    source=str(source),library_id=lib.source_library_id,track_id=track.native_id,
                    source_file_id=[source.stat().st_dev,source.stat().st_ino],request=asdict(request),
                    outcome='committed',after_revision=self.snapshot().revision)
                if self.kind=='grid':
                    from soundbox.rekordbox_cue_store import _encode_row
                    snap=self.snapshot();receipt.pop('after_revision')
                    receipt.update(track_after=_encode_row(snap.track),content_file_after=_encode_row(snap.content_file),
                        files={role:dict(after=hashlib.sha256(raw).hexdigest()) for role,raw in (('DAT',snap.dat),('EXT',snap.ext))})
                recovery=n.shared._write_root(lib);recovery.mkdir(mode=0o700,parents=True,exist_ok=True)
                atomic_bytes(recovery/('grid-completed.json' if self.kind=='grid' else 'last-result.json'),json.dumps(receipt).encode())
        n.grid._stores.clear();n.grid._factory=lambda *args:Store(grid_factory(*args),'grid')
        n.shared._cue_store_factory=lambda *args:Store(cue_factory(*args),'cue')
        self.controller=PrepEditController(w);w.prep_controller=self.controller
        patch=mock.patch('desktop.prep_controller.assert_rekordbox_closed');patch.start();self.addCleanup(patch.stop)
        patch=mock.patch('desktop.prep_controller.validate_track_path');patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.close)

    def close(self):
        if self.gate:self.gate.set()
        self.controller.stop();self.native.shared.shutdown();self.window.close();self.window.deleteLater();self.app.processEvents()

    def wait(self, condition):
        for _ in range(1000):
            if condition():return
            QTest.qWait(5)
        self.fail('Deferred prep did not settle: '+repr(self.controller.pending))

    def test_cues_and_grid_wait_for_native_owner_then_confirm_exactly(self):
        self.native.shared.busy=True
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertTrue(self.controller.request_cue(self.tid,'memory',0,'add',1234))
        self.assertEqual(self.writes,[])
        with mock.patch('desktop.prep_controller.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')):
            self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
            self.wait(lambda:not self.controller.busy and self.controller.timer.isActive())
            self.assertEqual(self.writes,[]);self.assertEqual(len(self.controller.pending[0]['operations']),3)
        self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual([k for k,_ in self.writes],['grid','cue','cue'])
        self.assertEqual(read_grid(self.native.dat)[0].time_ms,40)
        fields=rekordbox_db.cue_snapshot_fields(self.native.state)
        self.assertEqual(sorted(c['position_ms'] for c in fields['source_cues']),[777,1234])

    def test_committed_save_with_failed_local_ack_replays_receipt_without_duplicate(self):
        self.fail_after_commit=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']))
        self.assertEqual(len(self.writes),1);self.assertIsNotNone(self.controller.pending[0]['prepared'])
        self.assertFalse(self.controller.request_cue(self.tid,'hot',1,'add',888))
        self.controller.stop();self.native.shared.prep_priority=False
        self.window.settings=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.controller=PrepEditController(self.window);self.window.prep_controller=self.controller
        # Startup must reserve a committed receipt before another publisher's
        # earlier-connected idle callback can acquire the shared lane.
        self.assertTrue(self.native.shared.prep_priority)
        self.assertFalse(self.native.shared.run_analysis_job(lambda:None,lambda _:None))
        self.assertIn('Confirming cue/grid',self.native.shared.reason([self.tid]))
        self.assertFalse(self.native.shared.request([self.tid],'any-tag',True))
        self.assertIn('Confirming cue/grid',self.native.shared.cue_reason(self.tid))
        self.assertFalse(self.native.shared.request_cue(self.tid,'hot',1,'add',888))
        self.assertIn('Confirming cue/grid',self.native.grid._source_reason(self.tid))
        # The central cue starter must also respect the receipt reservation,
        # even if called from a late queued callback with no cue work left.
        self.native.shared._start_next_cue()
        self.assertFalse(self.native.shared.busy)
        self.controller.retry();self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual(len(self.writes),1);self.assertEqual(len(self.native.state.cues),1)

    def test_newer_edit_while_source_is_saving_survives_shutdown_and_restart(self):
        self.gate=threading.Event();self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.wait(lambda:bool(self.writes))
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        target=deepcopy(self.controller.job(self.tid)['grid'])
        self.controller.stop();self.gate.set();self.wait(lambda:not self.native.shared.busy)
        self.assertEqual(len(self.controller.pending[0]['operations']),1)
        self.window.settings=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.controller=PrepEditController(self.window);self.window.prep_controller=self.controller
        self.controller.pump();self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual([[b.number,b.time_ms,b.tempo/100] for b in read_grid(self.native.dat)],target)
        self.assertEqual(len(self.writes),2)

    def test_initial_analysis_acknowledges_only_its_captured_grid_and_keeps_newer_edit(self):
        w=self.window;track=w.lib.tracks_by_id[self.tid]
        w.lib.tracks_by_id[self.tid]=replace(track,source_analysis_path='')
        self.native.shared.busy=True
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        record=deepcopy(w._analysis_result(w.lib.tracks_by_id[self.tid]))
        first=deepcopy(self.controller.job(self.tid)['grid'])
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        target=deepcopy(self.controller.job(self.tid)['grid'])
        # Initial analysis publishes its frozen first edit through its separate
        # accepted adapter; the newer click is still a pending grid intention.
        self.native.dat=replace_grid(self.native.dat,tuple(Beat(r[0],round(r[2]*100),r[1]) for r in first))
        self.native.file=grid_fixture.content_file(self.native.state.track,self.native.dat)
        self.controller.ack_initial(self.tid,record);w._save_state()
        self.assertEqual(len(self.controller.pending[0]['operations']),1)
        self.assertEqual(self.controller.grid_view(self.tid)[0],target)
        w.lib.tracks_by_id[self.tid]=track
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual(len(self.writes),1)
        self.assertEqual([[b.number,b.time_ms,b.tempo/100] for b in read_grid(self.native.dat)],target)

    def test_unknown_post_commit_receipt_never_duplicates_or_discards_the_intention(self):
        self.fail_after_commit=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']))
        root=self.native.shared._write_root(self.window.lib);path=root/'last-result.json'
        receipt=json.loads(path.read_bytes());receipt['after_revision']='0'*64
        atomic_bytes(path,json.dumps(receipt).encode())
        self.controller.retry()
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']))
        self.assertEqual(len(self.writes),1);self.assertEqual(len(self.native.state.cues),1)
        self.assertEqual(len(self.controller.pending[0]['operations']),1)
        self.assertIn('reconciliation',self.controller.pending[0]['error'])

    def test_changed_library_during_source_commit_keeps_exact_prepared_intention(self):
        self.gate=threading.Event()
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.wait(lambda:bool(self.writes))
        self.window.lib=replace(self.window.lib,source_library_id='a different native library')
        self.gate.set()
        self.wait(lambda:bool(self.controller.pending[0]['error']))
        job=self.controller.pending[0]
        self.assertIsNotNone(job['prepared']);self.assertEqual(len(job['operations']),1)
        self.assertIn('original library',job['error']);self.assertEqual(len(self.writes),1)
        durable=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.assertEqual(durable[prep_edits.KEY],self.controller.pending)

    def test_invalid_saved_request_never_enters_the_source_worker_or_discards_intentions(self):
        self.native.shared.busy=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        job=self.controller.pending[0];job['operations'][0]['slot']=-1
        before=deepcopy(self.controller.pending)
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.assertFalse(self.controller.busy);self.assertEqual(self.writes,[])
        self.assertEqual(self.controller.pending,before)
        self.assertIn('Invalid pending prep operation',self.native.shared.prep_update_status)
        self.window.settings[prep_edits.KEY]=None
        self.assertIsNone(self.controller.job(self.tid))
        self.assertIn('Invalid pending prep queue',self.controller.reason(self.tid))
        self.controller.pump();self.assertTrue(self.native.shared.prep_priority)

    def test_native_terminal_beat_survives_right_then_left_nudges_and_cues_keep_saving(self):
        original=deepcopy(self.window.player_bar._analysis_parts['beats'])
        self.window.player_bar._dur=original[-1][1]
        self.native.shared.busy=True
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.assertEqual(len(self.controller.job(self.tid)['grid']),len(original))
        self.assertGreater(self.controller.job(self.tid)['grid'][-1][1],self.window.player_bar._dur)
        self.assertTrue(self.controller.request_grid(self.tid,'shift',-1))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual([[b.number,b.time_ms,b.tempo/100] for b in read_grid(self.native.dat)],original)
        self.assertEqual([kind for kind,_ in self.writes],['grid','grid','cue'])

    def test_legacy_committed_eof_mismatch_reconciles_its_exact_receipt_without_reapplying(self):
        self.native.shared.busy=True
        track=self.window.lib.tracks_by_id[self.tid]
        original=deepcopy(self.window.player_bar._analysis_parts['beats'])
        self.window.player_bar._dur=original[-1][1]
        job=self.controller._new(track);job['version']=1
        job=prep_edits.operation(job,'grid','shift',1)
        grid,_cue=self.controller._stores(self.window.lib,track)
        from soundbox.rekordbox_grid import GridEdit
        request=GridEdit(track.native_id,track.source_uuid,grid.snapshot().revision,'shift',1)
        job['prepared']=dict(operation_id=job['operations'][0]['id'],kind='grid',request=json.loads(json.dumps(asdict(request))))
        self.window.settings[prep_edits.KEY]=[job];self.window._save_state()
        grid.apply(request)
        job['error']='Saved prep readback differs from the intended component'
        self.window._save_state()
        self.native.shared.busy=False;self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(len(read_grid(self.native.dat)),len(original))
        self.assertFalse(self.native.shared.prep_priority)

    def test_real_grid_receipt_file_hash_mismatch_preserves_the_prepared_operation(self):
        self.fail_after_commit=True
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']))
        path=self.native.shared._write_root(self.window.lib)/'grid-completed.json'
        receipt=json.loads(path.read_bytes());self.assertNotIn('after_revision',receipt)
        receipt['files']['DAT']['after']='0'*64;atomic_bytes(path,json.dumps(receipt).encode())
        self.controller.retry()
        self.wait(lambda:bool(self.controller.pending[0]['error']))
        self.assertEqual(len(self.writes),1);self.assertIn('reconciliation',self.controller.pending[0]['error'])

    def test_ordinary_native_track_captures_grid_and_cues_while_source_lane_is_reserved(self):
        track=self.window.lib.tracks_by_id[self.tid]
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_native_id='')
        self.native.shared.busy=True;self.native.shared.prep_priority=True
        self.assertTrue(self.controller.handles(self.tid))
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertEqual(len(self.controller.pending[0]['operations']),2);self.assertEqual(self.writes,[])
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)

    def test_ordinary_native_track_can_capture_cues_before_its_analysis_is_ready(self):
        track=self.window.lib.tracks_by_id[self.tid]
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_native_id='',source_analysis_path=None)
        self.assertTrue(self.controller.handles(self.tid))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.controller.timer.stop();self.controller.pump()
        self.assertEqual(self.writes,[]);self.assertEqual(self.controller.pending[0]['cues'][0]['position_ms'],777)
        self.assertFalse(self.controller.pending[0]['error'])
