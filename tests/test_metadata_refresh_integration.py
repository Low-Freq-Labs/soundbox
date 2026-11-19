"""Fresh refresh publication fences sparse metadata and source selection."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.app import MainWindow
from soundbox import alias_intents, metadata, metadata_provenance
from soundbox.library import Track
from tests import test_source_selection as fixtures


class MetadataRefreshIntegration(unittest.TestCase):
    def window(self):
        window = fixtures.SourceSelection().scoped_window()
        window.native_reader = SimpleNamespace(read_live=mock.Mock())
        window._on_refresh_done = lambda *args, **kwargs: MainWindow._on_refresh_done(window, *args, **kwargs)
        window.tag_controller.configure = mock.Mock()
        window.tag_controller.update_status_text = ''
        window.metadata_controller.pending = []
        window.metadata_controller.queue_error = lambda: ''
        window.metadata_controller.planning_pending = False
        window.metadata_controller.publish_derived = mock.Mock()
        window.metadata_controller.stage_native_refresh = mock.Mock(return_value=None)
        window._metadata_unaliased_registry = None
        window.imported = {}
        window.lib.tracks_by_id['2'] = Track('2', 'Song - ZONDO', '', 'file:///synthetic/two.wav',
            source_uuid='00000000-0000-4000-8000-000000000002')
        for name in ('_stop_pulse', '_sync_rekordbox_connection', '_resume_analysis_jobs',
                     '_refresh_devices', '_sync_edit_status', '_cache_library', '_arm_waveform_rechecks',
                     '_schedule_stock_artwork', '_maybe_fill_waveforms', '_invalidate_rows'):
            setattr(window, name, mock.Mock())
        window.providers = SimpleNamespace(allows=lambda _: False)
        window.unification_controller = SimpleNamespace(schedule=mock.Mock())
        window._apply_loaded_library.side_effect = lambda **_: MainWindow._build_artist_registry(window)
        MainWindow._build_artist_registry(window)
        return window

    def test_selection_scope_and_sparse_reconciliation_have_one_durable_commit(self):
        window = self.window()
        old_settings = window.settings
        def stage(_library, **_):
            token = {'settings': window.settings, 'titles': window.titles}
            window.settings = {**window.settings, 'reconciled': True}
            window.titles = {}
            return token
        window.metadata_controller.stage_native_refresh.side_effect = stage
        with mock.patch('desktop.app.QTimer.singleShot'):
            self.assertTrue(window._on_refresh_done(window.lib, metadata_changed_ids=['1'],
                selection_path=str(window.lib.source)))
        window._save_state.assert_called_once()
        self.assertTrue(window.settings['reconciled'])
        self.assertEqual(window.settings['rekordbox_db_path'], str(window.lib.source))
        self.assertIsNot(window.settings, old_settings)

    def test_failed_combined_save_restores_path_curation_and_previous_library_owners(self):
        window = self.window()
        old = window.lib, window.settings, window.titles, window.overrides
        def stage(_library, **_):
            token = {'settings': window.settings, 'titles': window.titles}
            window.settings = {**window.settings, 'reconciled': True}
            window.titles = {}
            return token
        window.metadata_controller.stage_native_refresh.side_effect = stage
        window._save_state.return_value = False
        incoming = replace(window.lib)
        self.assertFalse(window._on_refresh_done(incoming, metadata_changed_ids=['1'],
            selection_path=str(window.lib.source)))
        self.assertTrue(all(after is before for after, before in zip(
            (window.lib, window.settings, window.titles, window.overrides), old)))
        window._save_state.assert_called_once()
        window._apply_loaded_library.assert_not_called()
        window.metadata_controller.publish_derived.assert_not_called()

    def test_global_trust_change_includes_other_rows_whose_raw_metadata_did_not_change(self):
        window = self.window()
        track = window.lib.tracks_by_id['2']
        old = metadata.effective_artist('2', track.name, track.artist, registry=window.lib.artist_registry)
        incoming = replace(window.lib, tracks_by_id={**window.lib.tracks_by_id,
            '1': replace(window.lib.tracks_by_id['1'], artist='ZONDO')})
        with mock.patch('desktop.app.QTimer.singleShot'):
            self.assertTrue(window._on_refresh_done(incoming, metadata_changed_ids=['1']))
        new = metadata.effective_artist('2', track.name, track.artist, registry=window.lib.artist_registry)
        self.assertNotEqual(old, new)
        self.assertEqual(new, 'ZONDO')
        self.assertEqual(set(window.metadata_controller.publish_derived.call_args.kwargs['changed_ids']), {'1', '2'})

    def test_unchanged_verified_refresh_never_stages_or_starts_metadata_work(self):
        window = self.window()
        with mock.patch('desktop.app.QTimer.singleShot'):
            self.assertTrue(window._on_refresh_done(window.lib))
        window.metadata_controller.stage_native_refresh.assert_not_called()
        window.metadata_controller.publish_derived.assert_not_called()
        window._save_state.assert_not_called()

    def test_metadata_ack_publishes_fresh_snapshot_without_restarting_prepared_services(self):
        window = self.window()
        incoming = replace(window.lib, tracks_by_id={key: replace(track,
            name='Saved '+track.name, source_cues=[{'time': 3}], source_tag_ids=['tag'])
            for key, track in window.lib.tracks_by_id.items()})
        with mock.patch('desktop.app.QTimer.singleShot') as timer:
            self.assertTrue(window._on_refresh_done(incoming, prepared_native_ids=['1']))
        self.assertEqual(window.lib.tracks_by_id['1'], incoming.tracks_by_id['1'])
        self.assertEqual(set(window._apply_loaded_library.call_args.kwargs['prepared_native_ids']), {'1','2'})
        self.assertEqual(window.lib.tracks_by_id['1'].source_cues, [{'time': 3}])
        self.assertEqual(window.lib.tracks_by_id['1'].source_tag_ids, ['tag'])
        for name in ('_resume_analysis_jobs','_refresh_devices','_arm_waveform_rechecks',
                     '_schedule_stock_artwork','_maybe_fill_waveforms'):
            getattr(window, name).assert_not_called()
        timer.assert_not_called()
        window.unification_controller.schedule.assert_not_called()
        window._cache_library.assert_called_once()
        window.metadata_controller.publish_derived.assert_not_called()

    def test_metadata_ack_with_changed_media_retains_full_publication(self):
        window = self.window()
        incoming = replace(window.lib, tracks_by_id={**window.lib.tracks_by_id,
            '1': replace(window.lib.tracks_by_id['1'], bpm='140')})
        with mock.patch('desktop.app.QTimer.singleShot'):
            self.assertTrue(window._on_refresh_done(incoming, prepared_native_ids=['1']))
        window._apply_loaded_library.assert_called_once_with(already_projected=True)
        window._refresh_devices.assert_called_once()
        window._arm_waveform_rechecks.assert_called_once()
        window.unification_controller.schedule.assert_called_once()

    def test_equal_lookup_context_reuses_registry_owners_for_worker_freeze_cache(self):
        window = self.window()
        owners = window.lib.artist_registry, window._metadata_unaliased_registry
        MainWindow._build_artist_registry(window)
        self.assertIs(window.lib.artist_registry, owners[0])
        self.assertIs(window._metadata_unaliased_registry, owners[1])

    def test_cached_startup_cannot_suppress_the_first_verified_worker_pass(self):
        for verified in (False, True):
            with self.subTest(verified=verified):
                window = self.window()
                window._rekordbox_source_verified = verified
                window._metadata_native_index = {'1': ('uuid', '/synthetic/one.wav', 'Title', 'Artist')}
                window.tag_controller.owned = False
                window.tag_controller.recover_before_read = mock.Mock()
                window._start_pulse = mock.Mock()
                window._sync_tags = mock.Mock()
                worker = SimpleNamespace(metadata_baseline=None, finished=SimpleNamespace(connect=mock.Mock()),
                                         start=mock.Mock())
                with mock.patch('desktop.app.RefreshWorker', return_value=worker):
                    MainWindow._start_rekordbox_refresh(window, window.lib.source)
                if verified:
                    self.assertIs(worker.metadata_baseline[1], window._metadata_native_index)
                else:
                    self.assertIsNone(worker.metadata_baseline)
                self.assertIs(worker.native_reader, window.native_reader)
                worker.start.assert_called_once()

    def test_ram_planning_reports_finishing_and_blocks_source_exit_before_descriptor_exists(self):
        window = self.window()
        window.metadata_controller.planning_pending = True
        window.metadata_controller.migration_pending = True
        self.assertIn('preparation', window._source_selection_reason('/synthetic/other/master.db'))
        self.assertEqual(window._source_selection_reason(window.lib.source), '')
        self.assertIn('Finishing metadata', MainWindow._pending_save_reason(window))

    def test_dirty_planning_never_hides_failed_source_work_behind_finishing(self):
        for source in ('queue', 'successor', 'migration', 'validation'):
            with self.subTest(source=source):
                window = self.window()
                window.metadata_controller.planning_pending = True
                window.metadata_controller.migration_pending = True
                message = 'Native metadata save failed'
                if source == 'queue':
                    window.settings['pending_metadata_edits'] = [{'error': message}]
                elif source == 'validation':
                    window.metadata_controller.queue_error = lambda: message
                else:
                    key = 'source_truth_alias_next' if source == 'successor' else 'source_truth_migration'
                    window.settings[key] = {'error': message}
                reason = MainWindow._pending_save_reason(window)
                self.assertIn(message, reason)
                self.assertNotIn('Finishing', reason)

    def test_own_materialized_artist_does_not_turn_a_name_guess_into_native_tag_trust(self):
        window = self.window()
        original = window.lib.tracks_by_id['2']
        track = replace(original, name='ZONDO', artist='Song')
        window.lib.tracks_by_id['2'] = track
        scope = dict(source=str(window.lib.source), kind=window.lib.source_kind,
                     library_id=window.lib.source_library_id)
        origin = dict(scope=scope, local_id='2', native_id='2', uuid=track.source_uuid,
            path=str(track.path), automatic='Song', native_artist='Song',
            automatic_title='ZONDO', native_title='ZONDO', title_shield='')
        origin = metadata_provenance.seed(origin, title=original.name, artist='', display_artist='Song')
        window.settings[alias_intents.ORIGINS] = {alias_intents.key(scope, origin): origin}
        MainWindow._build_artist_registry(window)
        self.assertFalse(window.lib.artist_registry.is_trusted('Song'))
        window.lib.tracks_by_id['1'] = replace(window.lib.tracks_by_id['1'], artist='ZONDO')
        MainWindow._build_artist_registry(window)
        self.assertTrue(window.lib.artist_registry.is_trusted('ZONDO'))
        self.assertFalse(window.lib.artist_registry.is_trusted('Song'))
        self.assertEqual(metadata.effective_artist('2', original.name, '',
            registry=window.lib.artist_registry), 'ZONDO')


if __name__ == '__main__':
    unittest.main()
