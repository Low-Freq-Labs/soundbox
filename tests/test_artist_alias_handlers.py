"""Existing artist commands atomically retain source-bound native intentions."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QDialog

from desktop.app import MainWindow
from desktop.suggest import MergeDialog
from soundbox import alias_intents
from tests import test_metadata_controller as metadata_fixture


class ArtistAliasCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):metadata_fixture.MetadataPhases.setUpClass()

    def setUp(self):
        self.case=metadata_fixture.MetadataPhases();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.w=self.case.w;self.w.metadata_controller=self.case.controller
        self.w._populate_tree=mock.Mock();self.w._reload_current_view=mock.Mock()
        self.w._refresh_devices=mock.Mock();self.w._stop_pulse=mock.Mock()
        self.w._set_status_text=mock.Mock()
        self.w._apply_merges=lambda accepts,unmerges:MainWindow._apply_merges(self.w,accepts,unmerges)

    def test_native_merge_and_unmerge_use_durable_controller_without_reporting_native_completion(self):
        w=self.w
        with mock.patch('desktop.app.QInputDialog.getItem',return_value=('Canonical',True)):
            MainWindow._merge_artist(w,'Original Artist')
        self.assertEqual(w.aliases,{'Original Artist':'Canonical'})
        first=w.settings[alias_intents.NEXT]['id']
        self.assertEqual(len(w.saved),1)
        self.assertIn(alias_intents.NEXT,w.saved[-1]['settings'])
        self.assertIn('pending',w._flash_status.call_args.args[0])
        MainWindow._remove_alias(w,'Original Artist')
        self.assertEqual(w.aliases,{})
        self.assertNotEqual(w.settings[alias_intents.NEXT]['id'],first)
        self.assertEqual(len(w.saved),2)
        self.assertFalse(self.case.writes)

    def test_native_bulk_merge_is_one_atomic_generation_and_failed_save_preserves_exact_owners(self):
        w=self.w;w.aliases={'old':'Previous'}
        MainWindow._apply_merges(w,[('Variant','Canonical'),('Other','Canonical')],['old'])
        self.assertEqual(w.aliases,{'Variant':'Canonical','Other':'Canonical'})
        self.assertEqual(len(w.saved),1)
        self.assertIn(alias_intents.NEXT,w.settings)
        before_settings=w.settings;before_aliases=w.aliases;w.save_ok=False
        w._populate_tree.reset_mock();w._reload_current_view.reset_mock()
        MainWindow._apply_merges(w,[('Replacement','Target')],['Variant'])
        self.assertIs(w.settings,before_settings);self.assertIs(w.aliases,before_aliases)
        w._populate_tree.assert_not_called();w._reload_current_view.assert_not_called()

    def test_legacy_bulk_failed_save_preserves_aliases_and_does_not_publish_success(self):
        w=self.w;w.lib=replace(w.lib,source='/synthetic/library.xml',source_kind='rekordbox-xml')
        previous=w.aliases={'old':'Previous'};w.save_ok=False
        MainWindow._apply_merges(w,[('Variant','Canonical')],['old'])
        self.assertIs(w.aliases,previous)
        w._populate_tree.assert_not_called();w._reload_current_view.assert_not_called()
        w._flash_status.assert_not_called()

    def test_merge_picker_source_change_rejects_old_choice_but_same_identity_refresh_is_valid(self):
        w=self.w;original=w.lib
        def choose_other(*_args):
            w.lib=replace(original,source='/different/master.db',source_library_id='456')
            return 'Canonical',True
        with mock.patch('desktop.app.QInputDialog.getItem',side_effect=choose_other):
            MainWindow._merge_artist(w,'Original Artist')
        self.assertEqual(w.aliases,{});self.assertFalse(w.saved)
        self.assertNotIn(alias_intents.NEXT,w.settings)
        w.lib=original
        def choose_same(*_args):
            w.lib=replace(original)
            return 'Canonical',True
        with mock.patch('desktop.app.QInputDialog.getItem',side_effect=choose_same):
            MainWindow._merge_artist(w,'Original Artist')
        self.assertEqual(w.aliases,{'Original Artist':'Canonical'})

    def test_bulk_dialog_source_change_and_stale_scan_do_not_apply_to_replacement_library(self):
        w=self.w;original=w.lib
        result=({'Original Artist':1},[('Original Artist','Canonical',.95)])
        def choose(dialog):
            dialog.accepts=[('Original Artist','Canonical')];dialog.unmerges=[]
            w.lib=replace(original,source='/different/master.db',source_library_id='456')
            return QDialog.Accepted
        with mock.patch.object(MergeDialog,'exec',choose):MainWindow._on_merge_scan(w,result)
        self.assertEqual(w.aliases,{});self.assertFalse(w.saved)
        w._stop_pulse.reset_mock()
        with mock.patch.object(MergeDialog,'exec',side_effect=AssertionError('Stale scan must not show')):
            MainWindow._on_merge_scan(w,result,source_scope=(str(original.source),original.source_kind,original.source_library_id))
        w._stop_pulse.assert_not_called();self.assertFalse(w.saved)


    def test_stale_merge_scan_retires_only_its_own_status_and_cannot_erase_a_newer_pulse(self):
        w=self.w;original=w.lib;worker=object();w._scan_worker=worker
        w._status_generation=7;w._pulse_word='Scanning'
        w.lib=replace(original,source='/different/master.db',source_library_id='456')
        scope=(str(original.source),original.source_kind,original.source_library_id)
        result=({},[])
        with mock.patch.object(MergeDialog,'exec',side_effect=AssertionError('Stale scan must not show')):
            MainWindow._on_merge_scan(w,result,source_scope=scope,worker=worker,status_generation=7)
        w._set_status_text.assert_called_once_with('Ready')
        w._set_status_text.reset_mock();w._status_generation=8;w._pulse_word='Exporting'
        MainWindow._on_merge_scan(w,result,source_scope=scope,worker=worker,status_generation=7)
        w._set_status_text.assert_not_called();w._stop_pulse.assert_not_called()
        w.lib=original
        MainWindow._on_merge_scan(w,result,source_scope=scope,worker=worker,status_generation=7)
        w._set_status_text.assert_not_called();w._stop_pulse.assert_not_called()
        w._flash_status.assert_not_called()


    def prepare_artist_acceptance(self):
        w=self.w;w.hidden=set();track=w.lib.tracks_by_id['local-song']
        w.lib=replace(w.lib,tracks_by_id={'local-song':replace(track,name='Unidentified recording',artist='')})
        self.provider=SimpleNamespace(suggest_artist=mock.Mock(side_effect=AssertionError('Provider must never run in this test')))
        w.providers=SimpleNamespace(allows=lambda _action:True,artists=self.provider)
        return w

    def test_accepted_artist_values_use_one_durable_native_generation_with_no_provider_call(self):
        w=self.prepare_artist_acceptance()
        with mock.patch('desktop.suggest.SuggestDialog') as factory:
            dialog=factory.return_value;dialog.accepts=[('local-song','Accepted Artist')]
            MainWindow._suggest_artists(w)
            dialog.shutdown.assert_called_once();dialog.deleteLater.assert_called_once()
        self.assertEqual(w.overrides,{'local-song':'Accepted Artist'})
        self.assertIn(alias_intents.NEXT,w.settings);self.assertEqual(len(w.saved),1)
        self.assertIn('pending',w._flash_status.call_args.args[0])
        self.assertFalse(self.case.writes);self.provider.suggest_artist.assert_not_called()

    def test_accepted_artist_dialog_rejects_replaced_source_song_or_path_before_any_save(self):
        w=self.prepare_artist_acceptance();original=w.lib;track=original.tracks_by_id['local-song']
        for changed in (None,replace(original,source='/different/master.db'),
                        replace(original,source_library_id='456'),
                        replace(original,tracks_by_id={'local-song':replace(track,source_native_id='2')}),
                        replace(original,tracks_by_id={'local-song':replace(track,source_uuid='00000000-0000-4000-8000-000000000099')}),
                        replace(original,tracks_by_id={'local-song':replace(track,location_uri='file:///changed/song.wav')}),
                        replace(original,tracks_by_id={'local-song':replace(track,source_native_path='/changed/native.wav')})):
            with self.subTest(changed=changed):
                w.lib=original
                with mock.patch('desktop.suggest.SuggestDialog') as factory:
                    dialog=factory.return_value;dialog.accepts=[('local-song','Accepted Artist')]
                    dialog.exec.side_effect=lambda:setattr(w,'lib',changed)
                    MainWindow._suggest_artists(w)
                self.assertEqual(w.overrides,{});self.assertFalse(w.saved)
                self.assertNotIn(alias_intents.NEXT,w.settings)
        self.provider.suggest_artist.assert_not_called()

    def test_artist_acceptance_allows_refreshed_same_identity_and_rejects_failed_durable_save(self):
        w=self.prepare_artist_acceptance();previous_settings=w.settings;previous_artists=w.overrides
        with mock.patch('desktop.suggest.SuggestDialog') as factory:
            dialog=factory.return_value;dialog.accepts=[('local-song','Accepted Artist')]
            dialog.exec.side_effect=lambda:setattr(w,'lib',replace(w.lib,tracks_by_id={
                key:replace(track) for key,track in w.lib.tracks_by_id.items()}))
            w.save_ok=False;MainWindow._suggest_artists(w)
            self.assertIs(w.settings,previous_settings);self.assertIs(w.overrides,previous_artists)
            w._populate_tree.assert_not_called();w._flash_status.assert_not_called()
            w.save_ok=True;MainWindow._suggest_artists(w)
        self.assertEqual(w.overrides,{'local-song':'Accepted Artist'})
        self.assertIn(alias_intents.NEXT,w.settings);self.provider.suggest_artist.assert_not_called()

    def test_legacy_artist_acceptance_failed_save_restores_exact_override_owner(self):
        w=self.prepare_artist_acceptance();w.lib=replace(w.lib,source='/synthetic/library.xml',source_kind='rekordbox-xml')
        original=w.overrides;w.save_ok=False
        with mock.patch('desktop.suggest.SuggestDialog') as factory:
            factory.return_value.accepts=[('local-song','Accepted Artist')]
            MainWindow._suggest_artists(w)
        self.assertIs(w.overrides,original);w._populate_tree.assert_not_called();w._flash_status.assert_not_called()
        self.provider.suggest_artist.assert_not_called()


if __name__=='__main__':unittest.main()
