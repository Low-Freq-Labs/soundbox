"""Owned Qt source lane, real native planners, durable replay; no live library."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import os
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from desktop.prep_controller import PrepEditController
from soundbox import analysis, prep_edits, rekordbox_db, track_analysis
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
            def cue_snapshot(self):return self.delegate.cue_snapshot()
            def grid_snapshot(self):return self.delegate.grid_snapshot()
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
        with mock.patch('desktop.prep_controller.assert_rekordbox_closed',side_effect=WriteProofError('Close rekordbox and its background agent before editing')), \
                mock.patch('desktop.prep_controller.time.monotonic',return_value=0):
            self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
            self.wait(lambda:not self.controller.busy and self.controller.timer.isActive())
            self.assertEqual(self.writes,[]);self.assertEqual(len(self.controller.pending[0]['operations']),3)
        self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual([k for k,_ in self.writes],['grid','cue','cue'])
        self.assertEqual(read_grid(self.native.dat)[0].time_ms,40)
        fields=rekordbox_db.cue_snapshot_fields(self.native.state)
        self.assertEqual(sorted(c['position_ms'] for c in fields['source_cues']),[777,1234])

    def test_native_cue_then_grid_captures_later_ram_grid_and_replays_after_restart(self):
        self.native.shared.busy=True
        before=self.native.dat
        pb=self.window.player_bar
        publish=pb.refresh_live_grid
        pb.refresh_live_grid=mock.Mock(wraps=publish)
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        cue_job=self.controller.job(self.tid)
        self.assertEqual(cue_job['grid'],[])
        self.assertTrue(cue_job['native_ready'])
        pb.refresh_live_grid.assert_not_called()
        original=deepcopy(pb._analysis_parts['beats'])
        self.assertEqual(self.native.dat,before)
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        job=self.controller.job(self.tid)
        self.assertEqual([op['kind'] for op in job['operations']],['cue','grid'])
        self.assertEqual(job['operations'][1]['before'],prep_edits.digest(original))
        desired=deepcopy(job['grid'])
        self.assertEqual(cue_job['grid'],[])
        self.controller.stop()
        self.window.settings=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.controller=PrepEditController(self.window)
        self.window.prep_controller=self.controller
        self.native.shared.busy=False
        self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual([kind for kind,_ in self.writes],['cue','grid'])
        self.assertEqual([[b.number,b.time_ms,b.tempo/100] for b in read_grid(self.native.dat)],desired)
        self.assertTrue(any(c['InMsec']==777 for c in self.native.state.cues))

    def test_lane_release_promotes_delayed_retry_but_rechecks_all_ownership_gates(self):
        shared=self.native.shared;w=self.window;shared.busy=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.controller.timer.stop();self.controller.pump()
        self.assertGreater(self.controller.timer.remainingTime(),1000)
        device=mock.Mock(return_value=False);w._device_operation_owned=device
        gates=[(shared,'busy',True),(shared,'checking',True),(shared,'grid_waiting',True),
               (w,'_refresh_worker',object()),(w,'_state_maintenance',True),(device,'return_value',True)]
        for index,(owner,name,value) in enumerate(gates):
            if index:
                previous,attribute,_=gates[index-1]
                setattr(previous,attribute,None if attribute=='_refresh_worker' else False)
            setattr(owner,name,value)
            shared.changed.emit()
            self.assertEqual(self.controller.timer.remainingTime(),0)
            QTest.qWait(15)
            self.assertEqual(self.writes,[])
            self.assertFalse(self.controller.busy)
            self.assertGreater(self.controller.timer.remainingTime(),1000)
        device.return_value=False;shared.idle.emit()
        self.assertEqual(self.controller.timer.remainingTime(),0)
        self.wait(lambda:not self.controller.pending and not shared.busy)
        self.assertEqual([kind for kind,_ in self.writes],['cue'])

    def test_native_open_backoff_survives_repeated_lane_signals_without_worker_spin(self):
        shared=self.native.shared;shared.busy=True
        with mock.patch('desktop.prep_controller.time.monotonic',return_value=1000) as clock, \
                mock.patch('desktop.prep_controller.assert_rekordbox_closed',
                           side_effect=WriteProofError('Close rekordbox and its background agent before editing')) as closed:
            self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
            original=deepcopy(self.controller.pending)
            shared.busy=False;shared.idle.emit()
            self.wait(lambda:closed.call_count==1 and not shared.busy)
            for _ in range(8):
                shared.idle.emit();shared.changed.emit();QTest.qWait(10)
                self.assertEqual(closed.call_count,1)
                self.assertGreater(self.controller.timer.remainingTime(),1000)
            self.assertEqual(self.controller.pending,original);self.assertEqual(self.writes,[])
            clock.return_value=1002;shared.idle.emit()
            self.wait(lambda:closed.call_count==2 and not shared.busy)
            QTest.qWait(25);self.assertEqual(closed.call_count,2)
            clock.return_value=1004;closed.side_effect=None;shared.idle.emit()
            self.wait(lambda:not self.controller.pending and not shared.busy)
        self.assertEqual([kind for kind,_ in self.writes],['cue'])

    def test_committed_save_with_failed_local_ack_replays_receipt_without_duplicate(self):
        opaque={'nested':['preserve alias']}
        self.window.settings.update(future=opaque,alias=opaque)
        self.fail_after_commit=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.wait(lambda:bool(self.controller.pending and self.controller.pending[0]['error']))
        self.assertEqual(len(self.writes),1);self.assertIsNotNone(self.controller.pending[0]['prepared'])
        self.assertIs(self.window.settings['future'],opaque)
        self.assertIs(self.window.settings['alias'],opaque)
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

    def test_consecutive_durable_cues_reuse_one_identity_bound_store_across_both_phases(self):
        factory=mock.Mock(wraps=self.native.shared._cue_store_factory)
        self.native.shared._cue_store_factory=factory;self.native.shared.busy=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',1,'add',888))
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        factory.assert_called_once();self.assertEqual([kind for kind,_ in self.writes],['cue','cue'])
        self.assertEqual([cue['InMsec'] for cue in self.native.state.cues],[777,888])

    def _grid_cache(self, beats):
        track=self.window.lib.tracks_by_id[self.tid];info=track.path.stat()
        manifest=dict(schema=track_analysis.SCHEMA,track_id=self.tid,
            source=dict(identity=f'{info.st_size}:{info.st_mtime_ns}',size=info.st_size),
            provenance=dict(grid='rekordbox'),beats=beats,
            grid_import=dict(source='rekordbox-anlz',fingerprint=track_analysis.grid_fingerprint(beats)))
        payload=b'private opaque compressed waveform payload'*100
        manifest['payload_sha256']=hashlib.sha256(payload).hexdigest()
        encoded=json.dumps(manifest).encode();folder=self.window._track_analysis_dir
        folder.mkdir(parents=True,exist_ok=True);target=track_analysis.cache_path(folder,self.tid)
        target.write_bytes(track_analysis._HEADER.pack(track_analysis._MAGIC,len(encoded))+encoded+payload)
        return target

    def test_cue_commands_leave_matching_waveform_cache_byte_exact_and_unwritten(self):
        target=self._grid_cache(self.window.player_bar._analysis_parts['beats'])
        original=target.read_bytes();identity=target.stat()
        for page,operation,position in [('hot','add',777),('memory','add',888),
                                        ('hot','delete',None),('memory','delete',None)]:
            with self.subTest(page=page,operation=operation):
                self.assertTrue(self.controller.request_cue(self.tid,page,0,operation,position))
                self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
                self.assertEqual(target.read_bytes(),original)
                self.assertEqual((target.stat().st_ino,target.stat().st_mtime_ns),(identity.st_ino,identity.st_mtime_ns))
        self.window._prepare_grid_cache.assert_not_called()

    def test_cue_save_still_refreshes_an_outdated_native_grid_cache(self):
        beats=deepcopy(self.window.player_bar._analysis_parts['beats'])
        stale=deepcopy(beats);stale[-1][1]-=1;self._grid_cache(stale)
        payload=track_analysis._read_container_manifest(self.window._track_analysis_dir,self.tid,
            self.window.lib.tracks_by_id[self.tid].path)[2]
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        _target,manifest,after_payload=track_analysis._read_container_manifest(self.window._track_analysis_dir,
            self.tid,self.window.lib.tracks_by_id[self.tid].path)
        self.assertEqual(manifest['beats'],beats);self.assertEqual(after_payload,payload)
        self.window._prepare_grid_cache.assert_called_once_with(self.tid)

    def test_corrupt_disposable_grid_cache_does_not_stall_native_cue_save(self):
        target=self._grid_cache(self.window.player_bar._analysis_parts['beats'])
        target.write_bytes(b'invalid disposable cache header')
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual(self.native.state.cues[0]['InMsec'],777)
        self.assertEqual(target.read_bytes(),b'invalid disposable cache header')
        self.window._prepare_grid_cache.assert_not_called()

    def test_cue_store_cache_rechecks_authority_source_inode_native_identity_and_budget(self):
        from soundbox.rekordbox_cue_store import CueLibraryAuthority
        track=self.window.lib.tracks_by_id[self.tid];lib=self.window.lib
        factory=mock.Mock(wraps=self.native.shared._cue_store_factory)
        self.native.shared._cue_store_factory=factory;self.native.shared._requires_cue_authority=True
        permission=CueLibraryAuthority(str(lib.source),lib.source_library_id,'a'*64,True)
        with mock.patch('desktop.prep_controller.load_cue_authority',return_value=permission) as loader:
            factory.side_effect=lambda _lib,tid:SimpleNamespace(authority=loader.return_value.bind(tid))
            first=self.controller._stores(lib,track)[1]
            self.assertIs(self.controller._stores(lib,track)[1],first);factory.assert_called_once()
            loader.return_value=replace(permission,schema_sha256='b'*64)
            second=self.controller._stores(lib,track)[1];self.assertIsNot(second,first)
            replacement=Path(lib.source).with_name('replacement.db');replacement.write_bytes(Path(lib.source).read_bytes())
            replacement.replace(lib.source)
            third=self.controller._stores(lib,track)[1];self.assertIsNot(third,second)
            for index in range(10):
                self.controller._stores(lib,replace(track,source_native_id=str(100+index),source_uuid=str(index)))
            self.assertEqual(len(self.controller._cue_stores),8)
            self.assertIsNot(self.controller._stores(lib,track)[1],third)
            loader.return_value=None
            with self.assertRaisesRegex(WriteProofError,'Cue editing is not enabled'):
                self.controller._stores(lib,track)
            self.assertFalse(self.controller._cue_stores)
            self.assertEqual(loader.call_count,16)

    def test_cue_store_permission_change_during_creation_is_never_cached(self):
        from soundbox.rekordbox_cue_store import CueLibraryAuthority
        track=self.window.lib.tracks_by_id[self.tid];lib=self.window.lib
        self.native.shared._requires_cue_authority=True
        permission=CueLibraryAuthority(str(lib.source),lib.source_library_id,'a'*64,True)
        self.native.shared._cue_store_factory=mock.Mock(
            return_value=SimpleNamespace(authority=replace(permission,schema_sha256='b'*64).bind(track.native_id)))
        with mock.patch('desktop.prep_controller.load_cue_authority',return_value=permission), \
                self.assertRaisesRegex(WriteProofError,'authority changed'):
            self.controller._stores(lib,track)
        self.assertFalse(self.controller._cue_stores)

    def test_real_cached_cue_store_retains_fresh_guards_and_counter_bound_backup_reuse(self):
        from test_rekordbox_cue_store import PilotCueTransactions
        from soundbox import rekordbox_cue_store as cues
        from soundbox.rekordbox_cues import CueEdit
        fixture=PilotCueTransactions();fixture.setUp()
        try:
            lib=replace(self.window.lib,source=fixture.source,source_library_id=fixture.authority.library_id)
            track=replace(self.window.lib.tracks_by_id[self.tid],source_native_id='2',source_uuid='track-two',
                          location_uri='file:///synthetic/audio')
            self.native.shared._cue_store_factory=mock.Mock(
                side_effect=lambda *_:cues.PilotCueStore(fixture.authority,fixture.root/'cached-recovery'))
            with mock.patch.object(cues,'encrypted_backup',wraps=cues.encrypted_backup) as backup:
                first=None
                for slot in range(2):
                    store=self.controller._stores(lib,track)[1]
                    if first is None:first=store
                    self.assertIs(store,first)
                    snapshot=store.snapshot()
                    store.apply(CueEdit('2','track-two',snapshot.revision,'hot',slot,'add',5000+slot*5000))
                self.assertEqual(backup.call_count,1)
                self.assertEqual(len(first.snapshot().cues),2)
            with mock.patch.object(cues,'_closed',side_effect=WriteProofError('native owner opened')), \
                    mock.patch.object(first,'_connect') as connection, \
                    self.assertRaisesRegex(WriteProofError,'native owner opened'):
                self.controller._stores(lib,track)[1].snapshot()
            connection.assert_not_called()
        finally:fixture.doCleanups()

    def test_legacy_committed_eof_mismatch_reconciles_its_exact_receipt_without_reapplying(self):
        self.native.shared.busy=True
        track=self.window.lib.tracks_by_id[self.tid]
        original=deepcopy(self.window.player_bar._analysis_parts['beats'])
        self.window.player_bar._dur=original[-1][1]
        job=self.controller._new(track);job['version']=1
        job=prep_edits.operation(job,'grid','shift',1)
        grid,_cue=self.controller._stores(self.window.lib,track,kind='grid')
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

    def test_variable_native_grid_clipping_preserves_display_and_saved_headline_bpm(self):
        rows=(Beat(1,9000,39),Beat(2,16000,706),Beat(3,16000,1081),Beat(4,16000,1456))
        self.native.dat=replace_grid(self.native.dat,rows)
        self.native.ext=grid_fixture.GridPlanningTests.metadata_snapshot(rows).ext
        self.native.file=grid_fixture.content_file(self.native.state.track,self.native.dat)
        self.window.player_bar._analysis_parts['beats']=[[b.number,b.time_ms,b.tempo/100] for b in rows]
        self.window._update_browser_bpm.reset_mock()
        self.assertTrue(self.controller.request_grid(self.tid,'shift',-40))
        self.assertEqual(self.controller.grid_view(self.tid)[1],90)
        self.wait(lambda:not self.controller.pending and not self.native.shared.busy)
        self.assertEqual(read_grid(self.native.dat)[0].tempo,16000)
        self.assertEqual(self.native.state.track['BPM'],9000)
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].bpm,'90.00')
        self.assertEqual(self.controller.grid_view(self.tid)[1],90)
        self.assertTrue(self.window._update_browser_bpm.called)
        self.assertTrue(all(call.args[1]=='90.00' for call in self.window._update_browser_bpm.call_args_list))

    def bare_track(self):
        track=self.window.lib.tracks_by_id[self.tid]
        track=replace(track,source_analysis_path='')
        self.window.lib.tracks_by_id[self.tid]=track
        return track

    def test_background_analysis_failure_does_not_veto_durable_local_prep(self):
        self.bare_track();self.native.shared.busy=True
        self.window.native_analysis_controller=SimpleNamespace(pending=[
            dict(local_id=self.tid,error='Packet publication needs attention')])
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        durable=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.assertEqual(durable[prep_edits.KEY],self.controller.pending)
        self.assertEqual(len(self.controller.pending[0]['operations']),2)
        record=self.window._analysis_result(self.window.lib.tracks_by_id[self.tid])
        self.assertEqual(record['grid_draft']['beats'],self.controller.pending[0]['grid'])
        self.assertEqual(record['cue_draft']['cues'][0]['position_ms'],777)
        self.assertEqual(self.writes,[])
        self.native.shared.busy=False

    def test_cloud_ctime_drift_captures_new_gestures_during_owned_sha_check(self):
        from soundbox.source_acquisition import acquire_source
        track=self.bare_track();original=analysis.identity(track.path.stat())
        self.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertEqual(analysis.identity(track.path.stat())[:4],original[:4])
        self.assertNotEqual(analysis.identity(track.path.stat()),original)
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1))
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        job=self.controller.pending[0];identity=job['id']
        self.assertTrue(job['identity_pending']);self.assertEqual(job['epoch'],original)
        entered=threading.Event();self.gate=threading.Event();main_thread=threading.get_ident()
        def capture(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),main_thread)
            durable=json.loads((self.native.shared.data_root/'state.json').read_bytes())
            self.assertTrue(durable[prep_edits.KEY][0]['identity_pending'])
            entered.set();self.gate.wait(5)
            return acquire_source(*args,**kwargs)
        with mock.patch('soundbox.source_acquisition.acquire_source',side_effect=capture):
            self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
            self.wait(entered.is_set)
            self.assertTrue(self.controller.request_cue(self.tid,'hot',1,'add',888))
            self.gate.set()
            self.wait(lambda:not self.native.shared.busy and not self.controller.pending[0].get('identity_pending'))
        job=self.controller.pending[0]
        self.assertEqual(job['id'],identity);self.assertEqual(len(job['operations']),3)
        self.assertEqual(job['epoch'],analysis.identity(track.path.stat()))
        self.assertEqual(self.window.player_bar._analysis_parts['_detected_epoch'],job['epoch'])
        self.assertEqual(self.window.player_bar._analysis_parts['beats'],job['grid'])
        self.assertEqual([c['position_ms'] for c in job['cues']],[777,888])
        self.assertFalse(job['error']);self.assertEqual(self.writes,[])

    def test_cloud_audio_proof_is_durable_across_restart(self):
        track=self.bare_track();self.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertTrue(self.controller.request_cue(self.tid,'memory',0,'add',1234))
        identity=self.controller.pending[0]['id'];self.controller.stop()
        self.window.settings=json.loads((self.native.shared.data_root/'state.json').read_bytes())
        self.controller=PrepEditController(self.window);self.window.prep_controller=self.controller
        self.native.shared.busy=False;self.controller.pump()
        self.wait(lambda:not self.native.shared.busy and not self.controller.pending)
        renewed=[state[prep_edits.KEY][0] for state in self.persisted if state.get(prep_edits.KEY)
                 and not state[prep_edits.KEY][0].get('identity_pending')]
        self.assertTrue(renewed)
        job=renewed[0]
        self.assertEqual(job['id'],identity);self.assertEqual(job['cues'][0]['position_ms'],1234)
        self.assertFalse(job['error']);self.assertEqual(job['version'],2)
        self.assertEqual([kind for kind,_ in self.writes],['cue'])
        self.assertEqual(self.native.state.cues[0]['InMsec'],1234)

    def test_late_audio_proof_cannot_publish_into_another_library_same_track_id(self):
        from soundbox.source_acquisition import acquire_source
        track=self.bare_track();self.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        original=deepcopy(self.controller.pending[0]);entered=threading.Event();self.gate=threading.Event()
        def capture(*args,**kwargs):
            entered.set();self.gate.wait(5)
            return acquire_source(*args,**kwargs)
        pb=self.window.player_bar;pb.refresh_live_grid=mock.Mock();pb.refresh_live_cues.reset_mock()
        parts=deepcopy(pb._analysis_parts)
        with mock.patch('soundbox.source_acquisition.acquire_source',side_effect=capture):
            self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
            self.wait(entered.is_set)
            current=replace(track,source_uuid='11111111-1111-4111-8111-111111111111')
            self.window.lib=replace(self.window.lib,source_library_id='another-library',tracks_by_id={self.tid:current})
            self.gate.set();self.wait(lambda:not self.native.shared.busy)
        job=self.controller.pending[0]
        self.assertEqual(job['epoch'],original['epoch']);self.assertTrue(job['identity_pending'])
        self.assertEqual(job['operations'],original['operations'])
        self.assertIn('original source',job['error']);self.assertEqual(self.writes,[])
        self.assertEqual(pb._analysis_parts,parts)
        pb.refresh_live_grid.assert_not_called();pb.refresh_live_cues.assert_not_called()
        self.assertIsNone(self.controller.grid_view(self.tid))
        self.assertEqual(self.controller.cue_rows(self.tid),current.source_cues or [])
        self.assertNotIn(777,[cue['position_ms'] for cue in self.controller.cue_rows(self.tid)])
        self.controller.refresh(self.tid)
        self.assertEqual(pb._analysis_parts,parts)
        pb.refresh_live_grid.assert_not_called();pb.refresh_live_cues.assert_not_called()

    def test_audio_proof_survives_legitimate_bare_import_native_identity_acquisition(self):
        from soundbox.source_acquisition import acquire_source
        native=self.bare_track();track=replace(native,imported=True,source_native_id='',source_uuid='')
        self.window.lib.tracks_by_id[self.tid]=track;self.native.shared.busy=True
        track.path.chmod(track.path.stat().st_mode ^ 0o100)
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        entered=threading.Event();self.gate=threading.Event()
        def capture(*args,**kwargs):
            entered.set();self.gate.wait(5)
            return acquire_source(*args,**kwargs)
        with mock.patch('soundbox.source_acquisition.acquire_source',side_effect=capture):
            self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
            self.wait(entered.is_set)
            self.window.lib.tracks_by_id[self.tid]=replace(native,source_native_id='registered-native-id')
            # Registration changes both the DTO binding and its native row.
            # A cue-only source save no longer waits for an unrelated DAT.
            self.native.state=replace(self.native.state,track={**self.native.state.track,'ID':'registered-native-id'})
            self.gate.set()
            self.wait(lambda:not self.native.shared.busy and not self.controller.pending)
        self.assertEqual([kind for kind,_ in self.writes],['cue'])
        self.assertEqual(self.writes[0][1].track_id,'registered-native-id')
        self.assertEqual(self.native.state.cues[0]['ContentID'],'registered-native-id')
        self.assertEqual(self.native.state.cues[0]['InMsec'],777)

    def test_existing_prep_job_cannot_append_into_another_source_or_path(self):
        track=self.bare_track();self.native.shared.busy=True
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        original=deepcopy(self.controller.pending);record=deepcopy(self.window._analysis_result(track))
        for foreign in (replace(self.window.lib,source_library_id='another-library'),
                        replace(self.window.lib,tracks_by_id={self.tid:replace(track,location_uri=(track.path.parent/'other.wav').as_uri())})):
            with self.subTest(source=foreign.source_library_id,path=foreign.tracks_by_id[self.tid].path):
                self.window.lib=foreign
                self.assertFalse(self.controller.request_cue(self.tid,'hot',1,'add',888))
                self.assertEqual(self.controller.pending,original)
                self.assertEqual(self.window.settings['analysis_results'][analysis.result_key(track.path)],record)
        self.assertEqual(self.writes,[]);self.native.shared.busy=False

    def test_matching_stat_fields_never_accept_changed_bytes_for_prep(self):
        track=self.bare_track();self.native.shared.busy=True
        original=track.path.stat();before=track.path.read_bytes()
        track.path.write_bytes(bytes([before[0]^1])+before[1:])
        os.utime(track.path,ns=(original.st_atime_ns,original.st_mtime_ns))
        self.assertEqual(analysis.identity(track.path.stat())[:4],analysis.identity(original)[:4])
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.native.shared.busy=False;self.controller.timer.stop();self.controller.pump()
        self.wait(lambda:bool(self.controller.pending[0]['error']) and not self.native.shared.busy)
        job=self.controller.pending[0]
        self.assertTrue(job['identity_pending']);self.assertEqual(len(job['operations']),1)
        self.assertIn('captured bytes differ',job['error']);self.assertEqual(self.writes,[])

    def test_transient_identity_refusal_recovers_after_fresh_analysis(self):
        track=self.bare_track();self.native.shared.busy=True
        track.path.write_bytes(track.path.read_bytes()+b'changed')
        self.assertFalse(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertFalse(self.controller.pending);self.assertNotIn(self.tid,self.controller.errors)
        record=self.window._analysis_result(track)
        record.update(epoch=analysis.identity(track.path.stat()),size=track.path.stat().st_size,
                      sha256=hashlib.sha256(track.path.read_bytes()).hexdigest())
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertEqual(self.controller.pending[0]['cues'][0]['position_ms'],777)
        self.native.shared.busy=False
