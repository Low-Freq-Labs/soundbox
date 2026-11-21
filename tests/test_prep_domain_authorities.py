"""Real encrypted prep saves keep independent Cue/Grid write permissions."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import unittest
from unittest import mock

from PySide6.QtTest import QTest

from soundbox import rekordbox_cue_store as cues, rekordbox_grid_store as grids
from soundbox import rekordbox_db, rekordbox_prep_read, rekordbox_tags, rekordbox_write
from soundbox.rekordbox_cues import CueEdit, CueIdentities, plan_cue_edit
from soundbox.rekordbox_grid import read_grid
from tests import test_deferred_prep as desktop_fixture
from tests import test_rekordbox_cue_store as source_fixture


class IndependentPrepAuthorities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_fixture.DurablePrep.setUpClass()

    def setUp(self):
        self.case=desktop_fixture.DurablePrep();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.native=self.case.native;self.window=self.case.window;self.controller=self.case.controller
        self.tid=self.case.tid;area=self.native.shared.data_root.resolve()
        self.source=area/'native/master.db';self.source.parent.mkdir()
        conn=source_fixture.fixture(self.source)
        raw=deepcopy(self.native.state.track);raw['ID']='2'
        uuid=raw['UUID'];relative=f'/PIONEER/USBANLZ/{uuid[:3]}/{uuid[3:]}/ANLZ0000.DAT'
        raw['AnalysisDataPath']=relative
        columns={row[1] for row in conn.execute('PRAGMA table_info(djmdContent)')}
        for key,value in raw.items():
            if key not in columns:
                conn.execute(f'ALTER TABLE djmdContent ADD COLUMN "{key}" '+('INTEGER' if type(value) is int else 'TEXT'))
        conn.execute('UPDATE djmdContent SET '+','.join(f'"{k}"=?' for k in raw)+' WHERE ID=?',tuple(raw.values())+('2',))
        self.schema=rekordbox_tags._schema(conn);conn.close()
        self.dat=self.source.parent/'share'/relative.lstrip('/');self.dat.parent.mkdir(parents=True)
        self.dat.write_bytes(self.native.dat);self.ext=self.dat.with_suffix('.EXT');self.ext.write_bytes(self.native.ext)
        self.root=area/'native-recovery';self.native.shared._write_root=lambda _:self.root
        self.enterContext(mock.patch.object(cues,'_closed'))
        self.enterContext(mock.patch.object(cues,'_installed_version',return_value=rekordbox_tags.NATIVE_VERSION))
        self.enterContext(mock.patch.object(rekordbox_tags,'_assert_closed'))
        self.enterContext(mock.patch.object(rekordbox_tags,'_installed_version',return_value=rekordbox_tags.NATIVE_VERSION))
        conn=rekordbox_write._connect(self.source,readonly=True)
        try:snapshot=rekordbox_prep_read.cue_snapshot(cues.PilotCueStore._rows,conn,'2')
        finally:conn.close()
        track=replace(self.window.lib.tracks_by_id[self.tid],source_native_id='2',
            source_uuid=uuid,source_analysis_path=relative,**rekordbox_db.cue_snapshot_fields(snapshot))
        self.window.lib=replace(self.window.lib,source=self.source,source_library_id='synthetic-library',tracks_by_id={self.tid:track})
        self.native.shared.lib=self.window.lib
        self.native.grid._stores.clear();self.controller._cue_stores.clear()
        self.native.shared._requires_cue_authority=True;self.native.shared._cue_authority=None
        self.native.grid._requires_authority=True;self.native.grid._authority=None
        self.cue_authority=cues.CueLibraryAuthority(str(self.source),'synthetic-library',self.schema,True)
        self.grid_authority=grids.GridLibraryAuthority(str(self.source),'synthetic-library',self.schema,True)

    def authority(self,domain):
        authority=getattr(self,domain+'_authority')
        (self.native.shared.data_root/f'rekordbox-{domain}-authority.json').write_text(json.dumps(asdict(authority)))
        if domain=='grid':
            self.native.grid._authority=authority;self.native.grid._factory=self.native.grid._make_store
        else:
            self.native.shared._cue_authority=authority;self.native.shared._cue_store_factory=self.native.shared._make_cue_store

    def state(self):
        conn=rekordbox_write._connect(self.source,readonly=True)
        try:return rekordbox_write._state(conn)
        finally:conn.close()

    def settled(self):
        # Unlike DurablePrep's fake stores, each phase here opens real encrypted
        # DB connections and the save verifies a real backup/recovery receipt.
        # Join the two owned workers; a poll count is not a save-latency oracle.
        owner=self.native.shared.owner
        def diagnostic():
            pending=self.controller.pending
            return dict(pending=len(pending),prepared=bool(pending and pending[0]['prepared']),
                error=pending[0]['error'] if pending else '',busy=self.native.shared.busy,
                owner_pending=owner.pending,receipts=sorted(p.name for p in self.root.glob('*.json')))
        def dispatch_until(condition):
            for _ in range(20):
                if condition():return
                self.case.app.processEvents()
                if condition():return
                QTest.qWait(1)
            self.fail('Prep event delivery did not progress: '+repr(diagnostic()))
        for phase in ('capture','save'):
            dispatch_until(lambda:bool(owner._workers))
            self.assertEqual(owner.pending,1,repr(diagnostic()))
            worker=next(iter(owner._workers))
            self.assertEqual(self.native.shared._busy_kind,'prep',repr(diagnostic()))
            self.assertTrue(worker.wait(30_000),phase+' worker did not join: '+repr(diagnostic()))
            self.assertFalse(worker.error,worker.error)
            result=worker.result
            self.assertIsInstance(result,dict)
            self.assertTrue(('prepared' if phase=='capture' else 'row') in result,repr(diagnostic()))
            # TaskOwner must deliver its actual finished callback; no stop/drain
            # or injected acknowledgement may close admission between phases.
            dispatch_until(lambda:worker not in owner._workers)
            if phase=='capture':
                self.assertEqual(len(self.controller.pending),1,repr(diagnostic()))
                self.assertEqual(self.controller.pending[0]['prepared'],result['prepared'])
                durable=json.loads((self.native.shared.data_root/'state.json').read_text())
                self.assertEqual(durable[desktop_fixture.prep_edits.KEY][0]['prepared'],result['prepared'])
        self.assertFalse(self.controller.pending,repr(diagnostic()))
        self.assertFalse(self.native.shared.busy,repr(diagnostic()))
        self.assertEqual(owner.pending,0,repr(diagnostic()))

    def seed_cue(self):
        """Generate an existing source cue without admitting a desktop writer."""
        conn=rekordbox_write._connect(self.source)
        try:
            snapshot=rekordbox_prep_read.cue_snapshot(cues.PilotCueStore._rows,conn,'2')
            counter=cues.PilotCueStore._rows(conn,'agentRegistry'," WHERE registry_id='localUpdateCount'")[0]
            changes=plan_cue_edit(snapshot,counter,CueEdit('2',snapshot.track['UUID'],snapshot.revision,'hot',0,'add',500),
                identities=CueIdentities('200','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333'),
                now=datetime(2026,11,13,tzinfo=timezone.utc))
            for change in changes:
                if change.before is None:
                    columns=','.join(f'"{key}"' for key in change.after)
                    conn.execute(f'INSERT INTO {change.table} ({columns}) VALUES ('+','.join('?' for _ in change.after)+')',tuple(change.after.values()))
                else:
                    key='registry_id' if change.table=='agentRegistry' else 'ID'
                    conn.execute(f'UPDATE {change.table} SET '+','.join(f'"{field}"=?' for field in change.after)+f' WHERE {key}=?',tuple(change.after.values())+(change.key,))
            fresh=rekordbox_prep_read.cue_snapshot(cues.PilotCueStore._rows,conn,'2')
        finally:conn.close()
        self.window.lib.tracks_by_id[self.tid]=replace(self.window.lib.tracks_by_id[self.tid],**rekordbox_db.cue_snapshot_fields(fresh))
        return fresh

    def test_grid_only_authority_saves_and_reads_cues_without_granting_cue_writes(self):
        existing=self.seed_cue()
        self.authority('grid')
        forbidden=self.native.shared._cue_store_factory=mock.Mock(side_effect=AssertionError('No cue writer permission'))
        before=self.state();original=self.dat.read_bytes()
        self.assertEqual(self.controller.reason(self.tid,grid=True),'')
        self.assertIn('Cue editing is not enabled',self.controller.reason(self.tid))
        self.assertTrue(self.controller.request_grid(self.tid,'shift',1));self.settled()
        after=self.state()
        self.assertEqual(after,before)
        self.assertNotEqual(self.dat.read_bytes(),original)
        self.assertEqual(read_grid(self.dat.read_bytes())[0].time_ms,read_grid(original)[0].time_ms+1)
        forbidden.assert_not_called()
        store=self.native.grid._store_for(self.window.lib,self.window.lib.tracks_by_id[self.tid])
        self.assertEqual(store.cue_snapshot().cues,existing.cues)
        self.assertEqual(store.cue_snapshot().aggregate,existing.aggregate)
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].source_cues[0]['position_ms'],500)
        with self.assertRaisesRegex(rekordbox_write.WriteProofError,'no cue-write authority'):
            store.lease.apply(CueEdit('2',self.grid_authority.bind('2',self.native.state.track['UUID']).track_uuid,'','hot',0,'add',777))
        self.assertFalse(self.controller.request_cue(self.tid,'hot',0,'add',777))

    def test_cue_only_authority_saves_without_reading_or_granting_grid_writes(self):
        self.authority('cue')
        forbidden=self.native.grid._factory=mock.Mock(side_effect=AssertionError('No grid writer permission'))
        original=(self.dat.read_bytes(),self.ext.read_bytes());before=self.state()
        self.assertEqual(self.controller.reason(self.tid),'')
        with mock.patch.object(cues.PilotCueStore,'grid_snapshot',
                               side_effect=AssertionError('Cue writes must not acquire unrelated ANLZ')) as grid_read:
            self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777));self.settled()
        grid_read.assert_not_called()
        self.assertEqual((self.dat.read_bytes(),self.ext.read_bytes()),original)
        fresh=cues.PilotCueStore(self.cue_authority.bind('2'),self.root).snapshot()
        self.assertEqual(fresh.cues[0]['InMsec'],777)
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].source_cues[0]['position_ms'],777)
        self.assertEqual({t for t in before['tables'] if before['tables'][t]!=self.state()['tables'][t]},
                         {'djmdCue','contentCue','djmdContent','agentRegistry'})
        forbidden.assert_not_called()
        self.assertIn('Grid editing is not enabled',self.controller.reason(self.tid,grid=True))
        self.assertFalse(self.controller.request_grid(self.tid,'shift',1))

    def test_db_only_cue_save_preserves_unreadable_native_grid_and_private_cache(self):
        self.authority('cue');self.dat.write_bytes(b'corrupt native analysis; do not rewrite')
        before=deepcopy(self.window.settings['analysis_results'])
        self.assertTrue(self.controller.request_cue(self.tid,'hot',0,'add',777));self.settled()
        self.assertEqual(self.dat.read_bytes(),b'corrupt native analysis; do not rewrite')
        self.assertEqual(self.window.settings['analysis_results'],before)
        self.assertEqual(cues.PilotCueStore(self.cue_authority.bind('2'),self.root).snapshot().cues[0]['InMsec'],777)

    def test_native_cue_with_existing_waveform_checks_grid_only_once_for_cache_reconciliation(self):
        self.authority('cue')
        target=self.case._grid_cache(self.window.player_bar._analysis_parts['beats'])
        original=(self.dat.read_bytes(),self.ext.read_bytes(),target.read_bytes())
        snapshot=cues.PilotCueStore.grid_snapshot
        with mock.patch.object(cues.PilotCueStore,'grid_snapshot',autospec=True,
                               side_effect=snapshot) as reads:
            self.assertTrue(self.controller.request_cue(self.tid,'memory',0,'add',777));self.settled()
        self.assertEqual(reads.call_count,1)
        self.assertEqual((self.dat.read_bytes(),self.ext.read_bytes(),target.read_bytes()),original)
        self.assertEqual(self.window.lib.tracks_by_id[self.tid].source_cues[0]['position_ms'],777)


if __name__=='__main__':unittest.main()
