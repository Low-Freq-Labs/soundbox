"""Existing native cue metadata does not depend on a DAT/grid being available."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

from desktop.prep_controller import PrepEditController
from soundbox import rekordbox_db
from soundbox import rekordbox_cue_store as cue_store
from soundbox import rekordbox_tags
from tests import test_deferred_prep as fixture
from tests import test_rekordbox_cue_store as cue_fixture


class BareNativeCues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DurablePrep.setUpClass()

    def setUp(self):
        self.case=fixture.DurablePrep();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.window=self.case.window;self.native=self.case.native;self.tid=self.case.tid
        track=self.window.lib.tracks_by_id[self.tid]
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_analysis_path='')
        self.native.state.track.update(AnalysisDataPath='',Analysed=0)
        self.window.lib.tracks_by_id[self.tid]=replace(self.window.lib.tracks_by_id[self.tid],
            **rekordbox_db.cue_snapshot_fields(self.native.state))
        self.window.settings['analysis_results']={}
        self.window.player_bar._analysis_parts={'beats':[],'provenance':{}}
        self.grid=self.enterContext(mock.patch.object(self.window.grid_controller,'_store_for',
                                                     side_effect=AssertionError('Cue-only edit requested a grid/DAT')))

    def test_existing_native_bare_track_saves_cue_without_grid_analysis_and_survives_restart(self):
        controller=self.case.controller
        self.assertTrue(controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertEqual(controller.pending[0]['version'],2)
        self.case.wait(lambda:not controller.pending and not self.native.shared.busy)
        self.assertEqual([kind for kind,_ in self.case.writes],['cue'])
        self.grid.assert_not_called()
        self.assertEqual(self.native.state.cues[0]['InMsec'],777)
        controller.stop()
        restarted=PrepEditController(self.window);self.addCleanup(restarted.stop)
        self.assertEqual(restarted.cue_rows(self.tid)[0]['position_ms'],777)
        self.assertFalse(restarted.pending)

    def test_private_ack_failure_replays_exact_native_cue_receipt_without_grid(self):
        controller=self.case.controller;self.case.fail_after_commit=True
        self.assertTrue(controller.request_cue(self.tid,'hot',0,'add',777))
        self.case.wait(lambda:bool(controller.pending and controller.pending[0]['error']))
        self.assertEqual(len(self.case.writes),1)
        self.assertEqual(self.native.state.cues[0]['InMsec'],777)
        controller.retry()
        self.case.wait(lambda:not controller.pending and not self.native.shared.busy)
        self.assertEqual(len(self.case.writes),1)
        self.grid.assert_not_called()

    def test_already_captured_initial_analysis_keeps_its_bare_source_prerequisite(self):
        from types import SimpleNamespace
        controller=self.case.controller
        self.window.native_analysis_controller=SimpleNamespace(pending=[dict(local_id=self.tid,
            source=str(self.window.lib.source),library_id=self.window.lib.source_library_id,error='')])
        self.assertTrue(controller.request_cue(self.tid,'hot',0,'add',777))
        controller.timer.stop();controller.pump()
        self.assertFalse(self.case.writes)
        self.assertFalse(self.native.shared.busy)
        self.assertIsNone(controller.pending[0]['prepared'])
        self.assertEqual(controller.pending[0]['cues'][0]['position_ms'],777)

    def test_no_analysis_cue_view_uses_authoritative_native_rows_instead_of_empty_private_cache(self):
        track=self.window.lib.tracks_by_id[self.tid]
        cue=dict(kind='hot',slot=0,position_ms=777,end_ms=-1,type='single',label='native',color=[1,2,3])
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_cues=[cue])
        self.assertEqual(self.case.controller.cue_rows(self.tid),[cue])
        self.assertEqual(self.window.settings['analysis_results'],{})

    def test_production_prep_route_refuses_unsupported_codec_or_missing_cue_authority_before_preview(self):
        controller=self.case.controller;track=self.window.lib.tracks_by_id[self.tid]
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_cue_revision='',info={**track.info,'nativeFileType':5})
        self.assertFalse(controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertFalse(controller.pending);self.assertFalse(self.case.writes)
        self.window.lib.tracks_by_id[self.tid]=replace(track,info={**track.info,'nativeFileType':11})
        self.native.shared._requires_cue_authority=True;self.native.shared._cue_authority=None
        self.assertFalse(controller.request_cue(self.tid,'hot',0,'add',777))
        self.assertFalse(controller.pending);self.assertFalse(self.case.writes)

    def test_production_prep_grid_route_retains_analysis_lock_and_manual_bpm_authority_gates(self):
        self.enterContext(mock.patch.object(self.window.grid_controller,'parent',return_value=self.window))
        track=self.window.lib.tracks_by_id[self.tid]
        self.window.lib.tracks_by_id[self.tid]=replace(track,source_analysis_path='/synthetic/anlz',
            info={**track.info,'nativeFileType':11,'nativeAnalysisFlags':105|0x80})
        self.window.player_bar._analysis_parts['beats']=[[1,0,90],[2,667,90]]
        self.assertIn('Analysis Lock',self.window.grid_controller.reason(self.tid,allow_checking=True))
        self.assertFalse(self.case.controller.request_grid(self.tid,'shift',1))
        self.assertFalse(self.case.controller.pending)
        self.window.lib.tracks_by_id[self.tid].info['nativeAnalysisFlags']=105
        self.window.grid_controller._requires_authority=True
        from types import SimpleNamespace
        self.window.grid_controller._authority=SimpleNamespace(manual_bpm_accepted=False)
        self.assertIn('acceptance',self.window.grid_controller.bpm_reason(self.tid))

    def test_durable_bare_cue_reaches_real_encrypted_native_tables_and_fresh_snapshot(self):
        area=self.native.shared.data_root.resolve()
        source=area/'native-source/master.db';source.parent.mkdir()
        conn=cue_fixture.fixture(source)
        uuid='22222222-2222-4222-8222-222222222222'
        track=self.window.lib.tracks_by_id[self.tid]
        conn.execute('ALTER TABLE djmdContent ADD COLUMN BPM INTEGER DEFAULT 0')
        conn.execute('UPDATE djmdContent SET UUID=?,FolderPath=? WHERE ID=?',(uuid,str(track.path),'2'))
        authority=cue_store.CueWriteAuthority(str(source),'synthetic-library',rekordbox_tags._schema(conn),'2')
        conn.close()
        self.enterContext(mock.patch.object(cue_store,'_closed'))
        self.enterContext(mock.patch.object(cue_store,'_installed_version',return_value=rekordbox_tags.NATIVE_VERSION))
        store=cue_store.PilotCueStore(authority,area/'real-cue-recovery')
        snapshot=store.snapshot()
        current=replace(track,source_native_id='2',source_uuid=uuid,**rekordbox_db.cue_snapshot_fields(snapshot))
        self.window.lib=replace(self.window.lib,source=source,source_library_id='synthetic-library',tracks_by_id={self.tid:current})
        self.native.shared.lib=self.window.lib
        # The store's real, exact receipt is read by PrepEditController.
        self.native.shared._write_root=lambda _:store.root
        owner=self
        class BoundStore:
            def snapshot(self):return store.snapshot()
            def apply(self,request):
                owner.case.writes.append(('cue',request))
                return store.apply(request)
        self.native.shared._cue_store_factory=lambda *_:BoundStore()
        audio=track.path.read_bytes()
        controller=self.case.controller
        self.assertTrue(controller.request_cue(self.tid,'hot',0,'add',777))
        self.case.wait(lambda:not controller.pending and not self.native.shared.busy)
        fresh=cue_store.PilotCueStore(authority,store.root).snapshot()
        self.assertEqual((fresh.cues[0]['ContentID'],fresh.cues[0]['InMsec']),('2',777))
        self.assertEqual(rekordbox_db.cue_snapshot_fields(fresh)['source_cues'][0]['position_ms'],777)
        self.assertFalse(Path(str(source)+'-wal').exists())
        self.assertFalse(source.read_bytes().startswith(b'SQLite format 3'))
        self.assertEqual(track.path.read_bytes(),audio)
        self.assertTrue(self.window._cache_library.called)
        self.grid.assert_not_called()


if __name__=='__main__':unittest.main()
