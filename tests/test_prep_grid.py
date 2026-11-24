"""Hermetic grid-session scheduling with the real shared Qt worker owner."""
from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import tempfile
import threading
import time
import unittest
from unittest import mock
from uuid import uuid4

from PySide6.QtWidgets import QApplication

from desktop.grid_controller import GridEditController
from desktop.tag_controller import TagEditController
from desktop.analysis_fence import AnalysisWriteFence
from soundbox import rekordbox_db
from soundbox.library import Library, Track
from soundbox.rekordbox_grid import AmbiguousGridEnd, Beat, GridSnapshot, plan_grid_edit, read_grid, transform_grid
from soundbox.rekordbox_cues import CueIdentities
from tests.test_rekordbox_grid import primary
from tests import test_rekordbox_grid as grid_fixture
from tests.test_rekordbox_cues import snapshot, plan, advance, STAMP


class GridInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = snapshot()
        self.state.track.update(Analysed=105, BPM=9000, TrackInfoUpdated='6', AnalysisUpdated='2')
        self.state.track['AnalysisDataPath'] = '/PIONEER/USBANLZ/synthetic/ANLZ0000.DAT'
        self.dat = primary(tuple(Beat((i + 3) % 4 + 1, 9000, 39 + i * 2000 // 3)
                                 for i in range(252)))
        self.original = self.dat
        self.ext = grid_fixture.GridPlanningTests.metadata_snapshot(read_grid(self.dat)).ext
        self.counter = {'registry_id': 'localUpdateCount', 'int_1': 41, 'updated_at': 'keep'}
        self.file = grid_fixture.content_file(self.state.track, self.dat)
        self.requests = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.probe_gate = None
        self.release.set()
        self.save_error = ''
        outer = self

        class GridStore:
            def cue_snapshot(self):return deepcopy(outer.state)
            def snapshot(self):
                if outer.probe_gate is not None:
                    entered, release = outer.probe_gate
                    entered.set()
                    release.wait(5)
                return GridSnapshot(deepcopy(outer.state.track), outer.dat, outer.ext,
                                    deepcopy(outer.file))
            def apply(self, request):
                outer.requests.append(request)
                outer.entered.set()
                outer.release.wait(5)
                if outer.save_error:
                    raise RuntimeError(outer.save_error)
                try:
                    candidate = plan_grid_edit(self.snapshot(), outer.counter, request, timestamp=STAMP)
                except AmbiguousGridEnd:
                    candidate = plan_grid_edit(self.snapshot(), outer.counter, request,
                                               timestamp=STAMP, exact_end_ms=128750)
                outer.dat, outer.ext = candidate.dat, candidate.ext
                if candidate.changes:
                    outer.state = replace(outer.state, track=candidate.changes[0].after)
                    outer.counter = candidate.changes[1].after
                    if len(candidate.changes) > 2:
                        outer.file = candidate.changes[2].after

        class CueStore:
            def grid_snapshot(self):return GridSnapshot(deepcopy(outer.state.track),outer.dat,outer.ext,deepcopy(outer.file))
            def snapshot(self):
                return deepcopy(outer.state)
            def apply(self, request):
                ids = CueIdentities(str(1000 + len(outer.state.cues)), str(uuid4()), str(uuid4()))
                delta = plan(outer.state, request, counter=outer.counter, identities=ids)
                outer.state = advance(outer.state, delta)
                if delta:
                    outer.counter = delta[-1].after

        track = Track('7', 'Synthetic', '', 'file:///synthetic/track', bpm='90.00',
                      source_uuid=self.state.track['UUID'], source_analysis_path='/synthetic/analysis',
                      **rekordbox_db.cue_snapshot_fields(self.state))
        lib = Library({'7': track}, [], '/synthetic/master.db', source_kind='rekordbox-db',
                       source_library_id='synthetic')
        self.shared = TagEditController(Path(temp.name), cue_store_factory=lambda *_: CueStore())
        self.shared.configure(lib, True)
        self.grid = GridEditController(self.shared, store_factory=lambda *_: GridStore())
        self.errors = []
        self.grid.failed.connect(self.errors.append)
        self.shared.failed.connect(self.errors.append)
        self.grid.configure()
        self.grid.set_current('7')
        self.wait_for(lambda: not self.grid.reason('7') and not self.shared.busy)
        self.addCleanup(self.close)

    def close(self):
        self.release.set()
        if self.probe_gate is not None:
            self.probe_gate[1].set()
        self.grid.shutdown()
        self.shared.shutdown()

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(condition())

    def test_nudges_paint_immediately_and_group_before_background_confirmation(self):
        self.release.clear()
        self.assertTrue(self.grid.request('7', 'shift', 1))
        self.assertEqual(self.grid.view('7')[0][0][1], 40)
        self.assertEqual(self.dat, self.original)
        self.grid.request('7', 'shift', 1)
        self.grid.request('7', 'shift', -1)
        self.assertEqual(self.grid.view('7')[0][0][1], 40)
        self.wait_for(self.entered.is_set)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].nudges, (1, 1, -1))
        self.assertTrue(self.shared.owned)
        self.release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(read_grid(self.dat)[0].time_ms, 40)
        self.assertEqual(self.errors, [])

    def test_native_presentation_reasons_match_admission_lock_error_and_bpm_gates(self):
        grid=self.grid;original=grid._snapshots['7']
        def same():
            expected=(grid.reason('7',allow_checking=True),grid.bpm_reason('7'))
            with mock.patch.object(grid,'_source_reason',wraps=grid._source_reason) as source:
                self.assertEqual(grid.presentation_reasons('7'),expected)
            source.assert_called_once_with('7',presentation=True)
        same()
        grid._snapshots.pop('7');same()
        grid._snapshots['7']=replace(original,track={**original.track,'Analysed':0x80});same()
        grid._snapshots['7']=original;grid._errors['7']='Fresh saved grid differs';same()
        grid._errors.clear();grid._snapshots['7']=replace(original,content_file=None);same()
        grid._snapshots['7']=replace(original,dat=primary((Beat(1,9000,0),Beat(2,9100,500))));same()
        grid._snapshots['7']=original;grid._requires_authority=True
        from types import SimpleNamespace
        grid._authority=SimpleNamespace(manual_bpm_accepted=False);same()
        grid._authority.manual_bpm_accepted=True;same()
        self.shared.admission_paused=True;same()
        self.shared.admission_paused=False

    def test_focus_style_reprobes_have_no_edit_status_or_write(self):
        entered, release = threading.Event(), threading.Event()
        self.probe_gate = entered, release
        self.grid.set_current('7', force=True)
        self.wait_for(entered.is_set)
        self.assertTrue(self.shared.busy)
        self.assertEqual(self.shared.update_status_text, '')
        self.assertEqual(self.shared.status_text, 'Checking saved grid…')
        self.assertEqual(self.requests, [])
        release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.grid.set_current('7', force=True)
        self.assertEqual(self.shared.update_status_text, '')
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.dat, self.original)

    def test_logical_id_retains_cache_and_signal_keys_while_native_grid_and_cue_stores_use_sql_id(self):
        original=self.shared.lib.tracks_by_id['7']
        logical=replace(original,track_id='sb-stable',source_native_id='7')
        self.shared.lib=replace(self.shared.lib,tracks_by_id={'sb-stable':logical})
        self.grid.configure();saved=[];self.grid.saved.connect(lambda _lib,tid:saved.append(tid))
        with mock.patch.object(self.shared,'_cue_store_factory',wraps=self.shared._cue_store_factory) as cues:
            self.grid.set_current('sb-stable');self.wait_for(lambda:not self.grid.reason('sb-stable') and not self.shared.busy)
            self.assertTrue(self.grid.request('sb-stable','double'))
            self.wait_for(lambda:not self.shared.owned)
            self.assertTrue(all(call.args[1]=='7' for call in cues.call_args_list))
        self.assertEqual(self.requests[-1].track_id,'7');self.assertEqual(saved,['sb-stable'])
        self.assertEqual(set(self.shared.lib.tracks_by_id),{'sb-stable'})

    def test_accepted_grid_and_bpm_intents_report_specific_update_status(self):
        for operation, value, message in (
                ('shift', 1, 'Updating beatgrid…'),
                ('double', 0, 'Updating BPM…'),
                ('halve', 0, 'Updating BPM…'),
                ('bpm', 9100, 'Updating BPM…')):
            self.release.clear()
            self.entered.clear()
            self.assertTrue(self.grid.request('7', operation, value))
            self.assertEqual(self.shared.update_status_text, message)
            self.wait_for(self.entered.is_set)
            self.assertEqual(self.shared.status_text, message)
            self.release.set()
            self.wait_for(lambda: not self.shared.owned)
            self.assertEqual(self.shared.update_status_text, '')
        self.assertEqual(self.errors, [])

    def test_grouped_opposite_nudges_preserve_zero_boundary_clipping(self):
        self.dat = primary(tuple(Beat(i % 4 + 1, 9000, i * 2000 // 3) for i in range(252)))
        self.grid.configure()
        self.wait_for(lambda: not self.grid.reason('7') and not self.shared.busy)
        self.grid.request('7', 'shift', -1)
        self.grid.request('7', 'shift', 1)
        self.assertEqual(len(self.grid.view('7')[0]), 251)
        self.grid.shutdown()
        self.assertEqual(len(read_grid(self.dat)), 251)
        self.assertEqual(read_grid(self.dat)[0], Beat(2, 9000, 666))

    def test_discrete_edits_are_immediate_and_rebase_on_verified_predecessor(self):
        self.release.clear()
        self.grid.request('7', 'double')
        self.assertEqual(self.grid.view('7')[1], 180.0)
        self.wait_for(self.entered.is_set)
        self.grid.request('7', 'halve')
        self.assertEqual(self.grid.view('7')[1], 90.0)
        self.release.set()
        self.grid.shutdown()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.dat, self.original)
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '90.00')
        self.assertEqual(len(self.requests), 2)
        self.assertNotEqual(self.requests[0].revision, self.requests[1].revision)

    def test_manual_bpm_updates_ram_before_save_and_restores_on_failure(self):
        self.release.clear()
        self.assertTrue(self.grid.request('7', 'bpm', 9100))
        self.assertEqual(self.grid.view('7')[1], 91.0)
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '90.00')
        self.wait_for(self.entered.is_set)
        self.save_error = 'Close rekordbox'
        confirmed = []
        self.grid.saved.connect(lambda *_: confirmed.append(True))
        self.release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(self.grid.view('7')[1], 90.0)
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '90.00')
        self.assertEqual(confirmed, [])
        self.assertIn('Close rekordbox', self.errors)

    def test_disputed_terminal_beat_queues_then_reconciles_verified_endpoint(self):
        import struct
        rows = tuple(Beat(i % 4 + 1, 13801, 54 + (i * 6000000 + 6900) // 13801)
                     for i in range(297))
        self.dat = primary(rows)
        self.ext = grid_fixture.container(
            grid_fixture.section(b'PQT2', bytes.fromhex('000000000100000200000000') + bytes(32), 56),
            grid_fixture.section(b'PWV3', struct.pack('>III', 1, 19313, 0x960000) + bytes(19313), 24))
        self.state.track['BPM'] = 13801
        self.shared.lib.tracks_by_id['7'].source_cue_revision = self.state.revision
        self.file = grid_fixture.content_file(self.state.track, self.dat)
        self.grid.set_current('7', force=True)
        self.wait_for(lambda: not self.shared.owned)
        self.release.clear()
        self.assertTrue(self.grid.request('7', 'bpm', 13800))
        self.assertEqual(self.grid.view('7')[1], 138.0)
        self.wait_for(self.entered.is_set)
        self.release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(read_grid(self.dat)[-1], Beat(1, 13800, 128750))
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '138.00')
        self.assertEqual(self.errors, [])

    def test_bpm_shorthand_normalizes_and_invalid_entry_does_not_lock_editor(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from desktop.prep_grid import BpmValue
        field = BpmValue()
        self.addCleanup(field.close)
        field.set_bpm('138.01', track_id='7')
        field.set_editable(True)
        submitted, invalid = [], []
        def accepted(tid, bpm):
            submitted.append((tid, bpm))
            field.set_bpm(bpm / 100, track_id=tid)
        field.requested.connect(accepted)
        field.invalid.connect(invalid.append)
        field.show()
        self.app.processEvents()
        for text in ('138', '138.0', '138.00', '1,38', '138'):
            field.setFocus()
            field.setText(text)
            field.setModified(True)
            QTest.keyClick(field, Qt.Key_Return)
            if text != '1,38':
                self.assertEqual(field.text(), '138.00')
        self.assertEqual(submitted, [('7', 13800)] * 4)
        self.assertEqual(len(invalid), 1)

    def test_browser_bpm_confirms_each_verified_save_without_track_reload(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        from desktop.model import TrackModel, COLUMNS
        from desktop.rekordbox_playlists import NativePlaylistsMixin
        from PySide6.QtWidgets import QTableView
        model = TrackModel([dict(id='7', bpm='90.00'), dict(id='other', bpm='140.00')])
        host = SimpleNamespace(lib=self.shared.lib, model=model, tag_controller=self.shared,
            _row_cache={'7': dict(bpm='90.00')}, _selected_ids=lambda: [],
            _cache_gen=8, _warm_timer=mock.Mock(), _sync_playlists_cache=None,
            _schedule_artists_refresh=mock.Mock(), _shutting_down=False, _state_maintenance=False,
            _cache_library=mock.Mock(), _flash_status=mock.Mock(), _finish_tag_refresh=mock.Mock(),
            _finish_edit_status=mock.Mock())
        host._invalidate_rows = lambda ids: MainWindow._invalidate_rows(host, ids)
        host.table=QTableView();host.table.setModel(model);self.addCleanup(host.table.deleteLater)
        host._native_row_identity=NativePlaylistsMixin._native_row_identity
        host._browser_seat=lambda:MainWindow._browser_seat(host)
        host._restore_browser_seat=lambda seat:MainWindow._restore_browser_seat(host,seat)
        host._update_browser_bpm = lambda tid, bpm: MainWindow._update_browser_bpm(host, tid, bpm)
        confirmed, painted = [], []
        column = next(i for i, row in enumerate(COLUMNS) if row[0] == 'bpm')
        model.dataChanged.connect(lambda a, b, roles: painted.append((a.column(), b.column())))
        def saved(lib, tid):
            MainWindow._grid_saved(host, lib, tid)
            confirmed.append(model.data(model.index(0, column)))
        self.grid.saved.connect(saved)
        self.release.clear()
        self.grid.request('7', 'bpm', 9100)
        self.wait_for(self.entered.is_set)
        self.assertEqual(self.grid.view('7')[1], 91.0)
        self.assertEqual(model.data(model.index(0, column)), '90.00')
        self.grid.request('7', 'bpm', 9000)
        self.release.set()
        self.grid.shutdown()
        self.assertEqual(confirmed, ['91.00', '90.00'])
        self.assertEqual(painted, [(column, column), (column, column)])
        self.assertNotIn('7', host._row_cache)
        self.assertEqual(host._cache_gen, 10)
        MainWindow._on_rows_warmed(host, {'7': dict(bpm='180.00')}, 8)
        self.assertNotIn('7', host._row_cache)
        self.assertEqual(self.errors, [])

    def test_bpm_editor_captures_identity_and_cancels_unsubmitted_text(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from desktop.prep_grid import BpmValue
        field = BpmValue()
        self.addCleanup(field.close)
        submitted = []
        invalid = []
        field.requested.connect(lambda tid, bpm: submitted.append((tid, bpm)))
        field.invalid.connect(invalid.append)
        field.show()
        self.app.processEvents()
        field.set_bpm('90.00', track_id='7')
        field.set_editable(True)
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, '91.03')
        field.set_bpm('90.00', track_id='7')  # A status refresh must preserve typing.
        self.assertEqual(field.text(), '91.03')
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual(submitted, [('7', 9103)])
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, '120')
        QTest.keyClick(field, Qt.Key_Escape)
        self.assertEqual(field.text(), '90.00')
        self.assertEqual(len(submitted), 1)
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, '0')
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(len(invalid), 1)
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, '125')
        field.set_bpm('140.00', track_id='other')
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual(field.text(), '140.00')
        # An Enter arriving after a track switch cannot submit the abandoned 125 BPM.
        self.assertNotIn(('other', 12500), submitted)
        field.set_editable(False, 'Analysis Lock is on')
        before = list(submitted)
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual(submitted, before)

    def test_cue_creation_remains_immediate_during_grid_save_and_uses_new_revision(self):
        self.release.clear()
        self.grid.request('7', 'double')
        self.wait_for(self.entered.is_set)
        self.assertTrue(self.shared.request_cue('7', 'hot', 0, 'add', 10003))
        self.assertEqual(self.shared.session_cues('7')[0]['position_ms'], 10003)
        self.release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '180.00')
        self.assertEqual(self.state.cues[0]['InMsec'], 10003)
        # A subsequent grid save must rebase raw track metadata after our cue save.
        self.grid.request('7', 'shift', 1)
        self.grid.shutdown()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.state.cues[0]['InMsec'], 10003)

    def test_failure_reverts_preview_and_cancels_unconfirmed_tail(self):
        self.save_error = 'Close rekordbox'
        self.release.clear()
        self.grid.request('7', 'double')
        self.wait_for(self.entered.is_set)
        self.grid.request('7', 'shift', 1)
        self.release.set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(self.errors, ['Close rekordbox'])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.grid.view('7')[1], 90.0)
        self.assertEqual(self.dat, self.original)
        self.assertEqual(self.shared.lib.tracks_by_id['7'].bpm, '90.00')

    def test_track_change_does_not_redirect_accepted_edit_and_shutdown_flushes_debounce(self):
        self.grid.request('7', 'shift', 3)
        self.grid.set_current('other-track')
        self.assertIsNone(self.grid.view('other-track'))
        self.grid.shutdown()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.requests[0].track_id, '7')
        self.assertEqual(read_grid(self.dat)[0].time_ms, 42)
        self.assertFalse(self.shared.owned)

    def test_single_bar_start_uses_paused_visible_zero_before_saved_first_beat(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        from desktop.player import PlayerBar
        from desktop.prep_grid import grid_edit_buttons
        pb = PlayerBar()
        self.addCleanup(pb.shutdown)
        pb._track_id, pb._src_path = '7', str(self.shared.lib.tracks_by_id['7'].path)
        pb._dur, pb._native_error = 168000, ''
        pb.cue_panel.set_loaded(True)
        pb.wave.set_timing(0, pb._dur)
        pb.quantize_enabled = True  # Q must never snap a grid-edit target.
        host = SimpleNamespace(player_bar=pb, grid_controller=self.grid,
            tag_controller=self.shared, _sync_grid=lambda: None,
            _refresh_worker=None, _state_maintenance=False, _shutting_down=False,
            _device_operation_owned=lambda: False, _flash_status=mock.Mock())
        with mock.patch.object(pb, 'is_playing', return_value=False), \
                mock.patch.object(pb.player, 'position', return_value=39), \
                mock.patch.object(pb.player, 'synchronize') as clock_refresh:
            button = grid_edit_buttons(pb)[0]
            button.setEnabled(True)
            accepted = []
            button.clicked.connect(lambda: accepted.append(MainWindow._edit_grid(host, 'downbeat')))
            button.click()
            self.assertEqual(accepted, [True])
            self.assertEqual(self.grid.view('7')[0][0][:2], [1, 0])
            clock_refresh.assert_not_called()
        self.grid.shutdown()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].value, 0)
        self.assertEqual(read_grid(self.dat)[0], Beat(1, 9000, 0))
        self.assertEqual(self.errors, [])

    def hold_probe(self):
        self.probe_gate = threading.Event(), threading.Event()
        self.grid.configure()
        self.wait_for(self.probe_gate[0].is_set)
        self.assertEqual(self.grid.reason('7'), 'Checking saved grid…')
        self.assertEqual(self.grid.reason('7', allow_checking=True), '')

    def test_first_mouse_click_is_retained_while_saved_grid_probe_is_running(self):
        from types import SimpleNamespace
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from desktop.app import MainWindow
        from desktop.player import PlayerBar
        from desktop.prep_grid import grid_edit_buttons
        self.hold_probe()
        pb = PlayerBar()
        self.addCleanup(pb.shutdown)
        pb._track_id, pb._src_path = '7', str(self.shared.lib.tracks_by_id['7'].path)
        pb._dur, pb._native_error = 168000, ''
        pb.cue_panel.set_loaded(True)
        pb.wave.set_timing(0, pb._dur)
        pb._analysis_parts = {'beats': [[b.number, b.time_ms, b.tempo / 100]
                                        for b in read_grid(self.original)]}
        buttons = grid_edit_buttons(pb)
        host = SimpleNamespace(player_bar=pb, grid_controller=self.grid,
            tag_controller=self.shared, _sync_prep_bpm=lambda: None,
            grid_edit_buttons=buttons, bpm_value=mock.Mock(), _refresh_worker=None,
            _state_maintenance=False, _shutting_down=False,
            _device_operation_owned=lambda: False, _flash_status=mock.Mock(),
            _sync_edit_status=mock.Mock())
        host._sync_grid = lambda: MainWindow._sync_grid(host)
        self.grid.changed.connect(lambda tid: MainWindow._grid_changed(host, tid))
        host._sync_grid()
        accepted = []
        buttons[0].clicked.connect(lambda: accepted.append(MainWindow._edit_grid(host, 'downbeat')))
        with mock.patch.object(pb, 'is_playing', return_value=False), \
                mock.patch.object(pb.player, 'set_grid', return_value=True) as native_grid:
            self.assertTrue(buttons[0].isEnabled())
            QTest.mouseClick(buttons[0], Qt.LeftButton)
            native_grid.assert_called()
        self.assertEqual(accepted, [True])
        self.assertTrue(self.grid.pending('7'))
        self.assertTrue(self.shared.owned)
        self.assertEqual(self.grid.view('7')[0][0][:2], [1, 0])
        self.assertEqual(pb._beat_grid[0], 0)
        pb.quantize_enabled = True
        self.assertEqual(pb.quantize_cue_position(1), 0)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.dat, self.original)
        pb.wave.set_timing(900, pb._dur)  # A later seek cannot change the captured edit.
        self.probe_gate[1].set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].value, 0)
        self.assertEqual(read_grid(self.dat)[0], Beat(1, 9000, 0))
        self.assertEqual(self.errors, [])

    def test_edits_waiting_for_probe_keep_fifo_order(self):
        self.hold_probe()
        rows = [[b.number, b.time_ms, b.tempo / 100] for b in read_grid(self.original)]
        self.assertTrue(self.grid.request('7', 'downbeat', 0, preview_rows=rows))
        self.assertTrue(self.grid.request('7', 'shift', 3))
        self.assertTrue(self.grid.request('7', 'double'))
        self.assertEqual(self.grid.view('7')[0][0], [1, 3, 180.0])
        self.assertEqual(self.grid.view('7')[1], 180.0)
        self.assertEqual(self.requests, [])
        self.probe_gate[1].set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual([r.operation for r in self.requests], ['downbeat', 'shift', 'double'])
        self.assertEqual(read_grid(self.dat)[0], Beat(1, 18000, 3))
        self.assertEqual(self.errors, [])

    def test_probe_lock_failure_cancels_captured_edits_without_writing(self):
        self.state.track['Analysed'] |= 0x80
        self.shared.lib.tracks_by_id['7'].source_cue_revision = self.state.revision
        self.hold_probe()
        rows = [[b.number, b.time_ms, b.tempo / 100] for b in read_grid(self.original)]
        self.grid.request('7', 'downbeat', 0, preview_rows=rows)
        self.assertEqual(self.grid.view('7')[0][0][1], 0)
        self.grid.request('7', 'double')
        self.probe_gate[1].set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertFalse(self.grid.pending('7'))
        self.assertIn('Analysis Lock', self.grid.reason('7'))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.dat, self.original)
        self.assertEqual(self.grid.view('7')[0][0][1], 39)
        self.assertEqual(len(self.errors), 1)

    def test_stale_loaded_grid_rolls_back_ram_preview_and_never_writes_source(self):
        self.hold_probe()
        rows = [[b.number, b.time_ms, b.tempo / 100] for b in read_grid(self.original)]
        self.grid.request('7', 'downbeat', 0, preview_rows=rows)
        self.assertEqual(self.grid.view('7')[0][0][1], 0)
        self.dat, self.ext = transform_grid(self.original, self.ext, 'shift', 5)
        external = self.dat
        self.probe_gate[1].set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.dat, external)
        self.assertEqual(self.grid.view('7')[0][0][1], 39)
        self.assertIn('Saved grid changed', self.grid.reason('7'))
        self.assertEqual(len(self.errors), 1)

    def test_probe_identity_conflict_cancels_captured_edits_without_writing(self):
        self.state.track['Title'] = 'Changed externally'
        self.hold_probe()
        self.grid.request('7', 'downbeat', 0)
        self.probe_gate[1].set()
        self.wait_for(lambda: not self.shared.owned)
        self.assertIn('Track changed', self.grid.reason('7'))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.dat, self.original)
        self.assertEqual(len(self.errors), 1)

    def test_shutdown_drains_captured_probe_edit_after_track_switch(self):
        from PySide6.QtCore import QTimer
        self.hold_probe()
        self.grid.request('7', 'downbeat', 0)
        self.grid.set_current('different')
        QTimer.singleShot(20, self.probe_gate[1].set)
        self.grid.shutdown()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].track_id, '7')
        self.assertEqual(read_grid(self.dat)[0], Beat(1, 9000, 0))
        self.assertFalse(self.shared.owned)
        self.assertEqual(self.errors, [])

    def test_invalid_edit_does_not_fence_preparation_or_admit_work(self):
        started = []
        self.grid.edit_started.connect(started.append)
        self.assertFalse(self.grid.request('7', 'unknown'))
        self.assertEqual(started, [])
        self.assertEqual(self.requests, [])
        self.assertFalse(self.shared.owned)

    def test_unsupported_source_flags_disable_editing_during_probe(self):
        self.state.track['Analysed'] = None
        self.shared.lib.tracks_by_id['7'].source_cue_revision = self.state.revision
        self.grid.configure()
        self.wait_for(lambda: not self.shared.owned)
        self.assertIn('unsupported', self.grid.reason('7'))
        self.assertFalse(self.grid.request('7', 'double'))
        self.assertEqual(self.requests, [])

    def test_cache_fence_waits_for_prior_publish_then_rejects_only_old_target_work(self):
        fence = AnalysisWriteFence()
        entered, release, protected = threading.Event(), threading.Event(), threading.Event()
        def old_publish():
            entered.set()
            release.wait(3)
            return True
        first = threading.Thread(target=lambda: fence.publish('7', old_publish))
        second = threading.Thread(target=lambda: (fence.protect('7'), protected.set()))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        self.assertFalse(protected.wait(.02))
        release.set()
        first.join(2)
        second.join(2)
        self.assertTrue(protected.is_set())
        self.assertFalse(fence.publish('7', lambda: self.fail('stale publication')))
        self.assertTrue(fence.publish('other', lambda: True))


if __name__ == '__main__':
    unittest.main()
