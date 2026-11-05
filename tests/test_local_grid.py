"""Unregistered-grid drafts: durable data, native separation and late delivery."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from soundbox import analysis, local_cues, local_grid
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository


class _LocalGridFixture:
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.audio = self.root / 'synthetic.wav'
        self.audio.write_bytes(b'synthetic captured identity; never decoded')
        self.record = dict(engine=analysis.ENGINE, path=str(self.audio),
            epoch=analysis.identity(self.audio.stat()), size=self.audio.stat().st_size,
            sha256=hashlib.sha256(self.audio.read_bytes()).hexdigest(), duration=131.536,
            bpm=119.85444071310947, key='7A', first_cue=227,
            beats=[[(i % 4) + 1, round(227 + i * 60000 / 119.85), 119.85] for i in range(262)])
        analysis.validate_result(self.record)


class LocalGridDrafts(_LocalGridFixture, unittest.TestCase):
    def test_browser_projection_preserves_draft_scalars_without_copying_beat_rows(self):
        after = local_grid.edit(self.record, 'bpm', 12000)
        before = deepcopy(after)
        expected = local_grid.effective_result(after)
        with mock.patch('soundbox.local_grid.deepcopy', side_effect=AssertionError('Browser copied beat rows')):
            display = local_grid.display_fields(after)
        self.assertEqual(display, {'bpm': expected['bpm'], 'key': expected['key'], 'grid_draft': True})
        self.assertEqual(after, before)
        self.assertIsNone(local_grid.display_fields(None))
        self.assertIs(local_grid.display_fields(self.record), self.record)
        # A malformed musical grid must still fail even though the browser
        # only consumes its BPM. Mutation cannot reuse an earlier validation.
        after['grid_draft']['beats'][-1][1] = -1
        with self.assertRaises(ValueError):
            local_grid.display_fields(after)

    def test_browser_row_keeps_native_draft_and_track_info_precedence(self):
        from types import SimpleNamespace
        from desktop.app import MainWindow
        after = local_grid.edit(self.record, 'bpm', 12000)
        track = Track('fixture', 'Synthetic', 'Artist', self.audio.as_uri(), bpm='90.00', key='8A')
        baseline = {'id': track.track_id, 'bpm': '90.00', 'key': '8A'}
        host = SimpleNamespace(lib=None, _row_cache={track.track_id: baseline},
            settings={'track_info': {track.track_id: {'bpm': '100.00', 'key': '9A'}}},
            _analysis_result=lambda _track: after, _waveform_bpm=lambda _track: 150)
        with mock.patch('soundbox.local_grid.deepcopy', side_effect=AssertionError('Browser copied beat rows')):
            native = MainWindow._row_for(host, track)
            track.imported = True
            imported = MainWindow._row_for(host, track)
        self.assertEqual((native['bpm'], native['key']), ('100.00', '9A'))
        self.assertEqual((imported['bpm'], imported['key']), ('120.00', '9A'))
        self.assertFalse(native['bpm_estimated'])
        self.assertFalse(imported['bpm_estimated'])
        self.assertEqual(baseline, {'id': track.track_id, 'bpm': '90.00', 'key': '8A'})

    def test_manual_bpm_preserves_detector_and_anchor_and_survives_restart_and_native_mapping(self):
        before = deepcopy(self.record)
        after = local_grid.edit(self.record, 'bpm', 12000)
        self.assertEqual(self.record, before)
        self.assertEqual(after['bpm'], before['bpm'])
        self.assertEqual(after['beats'], before['beats'])
        self.assertEqual(after['first_cue'], 227)
        effective = local_grid.effective_result(after)
        self.assertEqual(effective['bpm'], 120)
        self.assertEqual(effective['beats'][:3], [[1,227,120], [2,727,120], [3,1227,120]])
        self.assertTrue(all(r[2] == 120 for r in effective['beats']))
        repo = StateRepository(self.root / 'library.json'); repo.load()
        repo.save({'settings': {'analysis_results': {analysis.result_key(self.audio): after}}})
        restored = StateRepository(repo.path).load()['settings']['analysis_results']
        native = Track('native-id', 'Synthetic', '', self.audio.as_uri(), source_uuid='new-uuid')
        self.assertEqual(analysis.saved_result(restored, native), after)
        played = analysis.playback_data({'cues': [{'position_ms': 333, 'kind': 'memory'}]}, after, self.audio)
        self.assertEqual(played['beats'], effective['beats'])
        self.assertEqual(played['cues'], [{'position_ms':333, 'kind':'memory'}])
        self.assertEqual(played['provenance']['grid'], 'soundbox-manual')
        source = dict(beats=[[1,0,90]], provenance={'grid':'rekordbox'})
        self.assertEqual(analysis.playback_data(source, after, self.audio), source)
        # Deleting the optional first-beat cue must survive reload/reanalysis;
        # an explicit empty cue draft cannot fall through to auto-creation.
        deleted = local_cues.edit(after, 'memory', 0, 'delete')
        self.assertEqual(analysis.playback_data({}, deleted, self.audio)['cues'], [])
        merged = analysis.merge_result(deleted, self.record, dict(bpm=True,key=True))
        self.assertEqual(analysis.playback_data({}, merged, self.audio)['cues'], [])

    def test_late_reanalysis_keeps_latest_manual_revision_but_changed_audio_does_not_inherit_it(self):
        first = local_grid.edit(self.record, 'bpm', 12000)
        latest = local_grid.edit(first, 'shift', 3)
        stale = analysis.merge_result(first, self.record, dict(bpm=True, key=True))
        merged = analysis.merge_result(latest, stale, dict(bpm=True, key=True))
        self.assertEqual(merged['grid_draft'], latest['grid_draft'])
        self.assertEqual(merged['grid_draft']['revision'], 2)
        changed = {**self.record, 'sha256':'0'*64}
        self.assertNotIn('grid_draft', analysis.merge_result(latest, changed, dict(bpm=True, key=True)))

    def test_fractional_input_variable_grid_and_ordered_clipping_are_preserved(self):
        fractional = local_grid.edit(self.record, 'bpm', 12013)
        self.assertEqual(local_grid.effective_result(fractional)['bpm'],120.13)
        variable = deepcopy(self.record); variable['beats'][100][2] = 121
        with self.assertRaisesRegex(RuntimeError, 'constant grid'):
            local_grid.edit(variable, 'bpm',12000)
        shifted = local_grid.edit(self.record, 'shift', -228)
        restored = local_grid.edit(shifted, 'shift', 228)
        self.assertEqual(local_grid.effective_result(restored)['beats'][0][0], 2)
        self.assertNotEqual(local_grid.effective_result(restored)['beats'], self.record['beats'])
        downbeat = local_grid.edit(self.record, 'downbeat', 250)
        self.assertEqual(local_grid.effective_result(downbeat)['beats'][0], [1,250,119.85])
        doubled = local_grid.edit(self.record, 'double')
        self.assertEqual(local_grid.effective_result(doubled)['bpm'],239.7)
        halved = local_grid.edit(self.record, 'halve')
        self.assertEqual(local_grid.effective_result(halved)['bpm'],59.92)

    def test_invalid_drafts_and_changed_capture_fail_closed(self):
        after = local_grid.edit(self.record,'bpm',12000)
        for patch in ({'revision':0}, {'base_sha256':'0'*64}, {'bpm':None}, {'origin':'detector'}, {'beats':[]}):
            with self.assertRaises(ValueError):
                analysis.validate_result({**after, 'grid_draft':{**after['grid_draft'], **patch}})
        self.audio.write_bytes(b'replaced')
        self.assertEqual(analysis.playback_data({}, after, self.audio),{})


class LocalGridDesktop(_LocalGridFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_prep_field_commits_real_rows_and_browser_without_audio_reload_and_rolls_back_failed_save(self):
        from desktop.app import MainWindow
        from desktop.model import TrackModel
        with mock.patch('soundbox.rekordbox_db.db_available', return_value=False), \
                mock.patch.object(MainWindow, '_refresh_devices'):
            win = MainWindow(isolated_dir=self.root / 'app')
        try:
            track = Track('sb-local', 'Synthetic', '', self.audio.as_uri(), imported=True)
            win.lib = Library({track.track_id:track}, [], 'synthetic')
            win.settings['analysis_results'] = {analysis.result_key(self.audio):deepcopy(self.record)}
            win.tag_controller.configure(win.lib, False)
            win.grid_controller.configure()
            pb = win.player_bar
            pb._track_id = track.track_id; pb._src_path = str(self.audio)
            pb._pending_autoplay = False; pb._native_error = ''
            pb.cue_panel._loaded = True
            pb._analysis_parts = analysis.playback_data({}, self.record, self.audio)
            win.model = TrackModel([win._row_for(track)])
            with mock.patch.object(pb.player, 'set_grid') as grid, \
                    mock.patch.object(pb, '_update_transport'), \
                    mock.patch.object(pb, 'play_track') as reload_audio, \
                    mock.patch.object(win.tag_controller, 'run_grid_job') as native_job:
                self.assertTrue(win._edit_grid('bpm',12000))
                self.assertEqual(win.model._rows[0]['bpm'],'120.00')
                self.assertEqual(pb._bpm,120)
                self.assertEqual(pb._analysis_parts['beats'][1],[2,727,120])
                grid.assert_called_once()
                native_job.assert_not_called(); reload_audio.assert_not_called()
                # An old manifest's BPM must not disagree with the pinned rows.
                late = pb._with_live_grid({'beats':self.record['beats'], '_detected_bpm':119.85})
                self.assertEqual(late['_detected_bpm'],120)
                self.assertEqual(late['beats'],pb._analysis_parts['beats'])
                saved = deepcopy(win.settings['analysis_results'])
                with mock.patch.object(win, '_save_state', return_value=False):
                    self.assertFalse(win._edit_grid('bpm',12100))
                self.assertEqual(win.settings['analysis_results'],saved)
                self.assertEqual(pb._bpm,120)
                self.assertEqual(win.model._rows[0]['bpm'],'120.00')
                self.assertEqual(grid.call_count,1)
            state = StateRepository(win._state_repository.path).load()
            record = state['settings']['analysis_results'][analysis.result_key(self.audio)]
            self.assertEqual(local_grid.effective_result(record)['bpm'],120)
        finally:
            win.shutdown(); win.close(); win.deleteLater(); self.app.processEvents()
