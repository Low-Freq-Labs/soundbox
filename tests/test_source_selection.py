"""A source chooser cannot cross accepted work or publish an unsaved selection."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.app import MainWindow
from soundbox.library import Library,Track


class Window:
    _source_selection_reason=MainWindow._source_selection_reason
    _accept_source_curation=MainWindow._accept_source_curation
    open_library=MainWindow.open_library
    _open_dialog=MainWindow._open_dialog

    def __init__(self):
        self.lib=Library({},[],'/synthetic/a/master.db')
        self.settings={}
        self._last_xml='/synthetic/old.xml'
        self.tag_controller=SimpleNamespace(busy=False,checking=False,admission_paused=False)
        self._refresh_worker=None
        self._analysis_worker=None
        self._state_maintenance=False
        self._shutting_down=False
        self._device_operation_owned=lambda:False
        self._flash_status=mock.Mock()
        self._save_state=mock.Mock(return_value=False)
        self._apply_loaded_library=mock.Mock()


class SourceSelection(unittest.TestCase):
    def scoped_window(self):
        from desktop.metadata_controller import MetadataController
        w=Window()
        track=Track('1','Original','Artist','file:///synthetic/one.wav',
            source_uuid='00000000-0000-4000-8000-000000000001')
        w.lib=Library({'1':track},[],'/synthetic/a/master.db',source_kind='rekordbox-db',source_library_id='a')
        w.titles={'1':'My cleaned title'};w.overrides={'1':'My artist'};w.aliases={'Old':'My artist'}
        w.custom={'Set':['1']};w.removals={};w.playlist_additions={};w.hidden=set();w.hidden_dates={}
        owner=SimpleNamespace(window=w)
        w.metadata_controller=SimpleNamespace(
            stage_source_scope=lambda *a,**kw:MetadataController.stage_source_scope(owner,*a,**kw),
            rollback_source_scope=lambda token:MetadataController.rollback_source_scope(owner,token))
        w._save_state.return_value=True
        self.assertTrue(w._accept_source_curation(w.lib))
        w._save_state.reset_mock()
        return w

    def test_selection_and_partition_commit_are_one_save_and_roll_back_exact_owners_together(self):
        w=self.scoped_window();old_lib=w.lib;old_settings=w.settings;old_titles=w.titles;old_artists=w.overrides
        other=Library({'2':Track('2','Other','Other','file:///synthetic/two.wav',
            source_uuid='00000000-0000-4000-8000-000000000002')},[],
            '/synthetic/b/master.db',source_kind='rekordbox-db',source_library_id='b')
        captured=[]
        def save():
            captured.append((w._last_xml,w.settings['source_curation']['active']['scope']['source'],dict(w.titles)))
            return False
        w._save_state.side_effect=save
        with mock.patch('desktop.app.engine.load_library',return_value=other):
            self.assertFalse(w.open_library(str(other.source)))
        self.assertEqual(captured,[(str(other.source),str(other.source),{})])
        self.assertIs(w.lib,old_lib);self.assertIs(w.settings,old_settings)
        self.assertIs(w.titles,old_titles);self.assertIs(w.overrides,old_artists)
        self.assertEqual(w._last_xml,'/synthetic/old.xml')
        w._apply_loaded_library.assert_not_called()

    def test_reused_native_id_cannot_overwrite_current_curation_before_publication(self):
        from dataclasses import replace
        w=self.scoped_window();old_settings=w.settings;old_titles=w.titles
        replacement=replace(w.lib,tracks_by_id={'1':replace(w.lib.tracks_by_id['1'],
            source_uuid='00000000-0000-4000-8000-000000000002')})
        self.assertFalse(w._accept_source_curation(replacement))
        self.assertIs(w.settings,old_settings);self.assertIs(w.titles,old_titles)
        w._save_state.assert_not_called()

    def test_equal_scope_refresh_rechecks_identity_without_rewriting_curation(self):
        w=self.scoped_window()
        self.assertTrue(w._accept_source_curation(w.lib))
        w._save_state.assert_not_called()

    def test_owned_source_work_blocks_both_chooser_and_direct_xml_entry(self):
        for field in ('busy','checking','admission_paused'):
            with self.subTest(field=field):
                w=Window();setattr(w.tag_controller,field,True);original=w.lib
                with mock.patch('desktop.app.QFileDialog.getOpenFileName') as chooser, \
                        mock.patch('desktop.app.engine.load_library') as loader:
                    w._open_dialog();self.assertFalse(w.open_library('/synthetic/new.xml'))
                chooser.assert_not_called();loader.assert_not_called()
                self.assertIs(w.lib,original);self.assertEqual(w._last_xml,'/synthetic/old.xml')
                w._save_state.assert_not_called()

    def test_source_work_admitted_during_chooser_is_checked_again_before_parse(self):
        w=Window();original=w.lib
        def choose(*_):
            w.tag_controller.busy=True
            return '/synthetic/new.xml',''
        with mock.patch('desktop.app.QFileDialog.getOpenFileName',side_effect=choose), \
                mock.patch('desktop.app.engine.load_library') as loader:
            w._open_dialog()
        loader.assert_not_called();self.assertIs(w.lib,original)

    def test_unstarted_accepted_edits_block_leaving_their_current_library(self):
        for key in ('pending_tag_assignments','pending_metadata_edits','pending_prep_edits',
                    'rekordbox_playlist_edits','pending_tag_definitions','shared_import_jobs'):
            with self.subTest(key=key):
                w=Window();w.settings[key]=[dict(source=str(w.lib.source),library_id='a')]
                with mock.patch('desktop.app.engine.load_library') as loader:
                    self.assertFalse(w.open_library('/synthetic/b/library.xml'))
                loader.assert_not_called()
                self.assertEqual(w._source_selection_reason(w.lib.source),'')

    def test_existing_wrong_source_can_return_to_the_original_pending_library(self):
        w=Window();w.lib=Library({},[],'/synthetic/b/master.db')
        w.settings['pending_tag_assignments']=[dict(source='/synthetic/a/master.db',library_id='a')]
        self.assertEqual(w._source_selection_reason('/synthetic/a/master.db'),'')
        w.lib=None
        self.assertEqual(w._source_selection_reason('/synthetic/a/master.db'),'')

    def test_failed_selection_save_retains_previous_library_path_and_presentation(self):
        w=Window();original=w.lib;new=Library({},[],'/synthetic/new.xml')
        with mock.patch('desktop.app.engine.load_library',return_value=new):
            self.assertFalse(w.open_library('/synthetic/new.xml'))
        self.assertIs(w.lib,original);self.assertEqual(w._last_xml,'/synthetic/old.xml')
        w._apply_loaded_library.assert_not_called();w._save_state.assert_called_once()

    def test_malformed_pending_identity_refuses_source_change_without_losing_it(self):
        w=Window();queue=[dict(source='../relative.db')];w.settings['pending_metadata_edits']=queue
        self.assertIn('attention',w._source_selection_reason(Path('/synthetic/b/master.db')))
        self.assertIs(w.settings['pending_metadata_edits'],queue)

    def test_new_durable_editor_queues_participate_in_quit_admission(self):
        for key in ('pending_tag_assignments','pending_metadata_edits'):
            w=Window();w.settings[key]=[dict(source=str(w.lib.source),error='storage failed')]
            self.assertIn('storage failed',MainWindow._pending_save_reason(w))

    def test_unresolved_or_malformed_migration_reports_attention_without_promising_progress(self):
        w=Window();w.metadata_controller=SimpleNamespace(migration_pending=True)
        w.settings['source_truth_migration']=dict(format='soundbox-source-truth-v1',error='',
            cursor=0,order=[],unresolved=['retained-song'])
        self.assertIn('unresolved',MainWindow._pending_save_reason(w))
        w.settings['source_truth_migration']['order']=None
        self.assertIn('attention',MainWindow._pending_save_reason(w))
        w.settings['source_truth_migration']=dict(format='soundbox-source-truth-building-v1',error='')
        self.assertIn('Finishing metadata',MainWindow._pending_save_reason(w))

    def test_alias_only_saved_work_blocks_leaving_but_permits_return_to_its_original_source(self):
        w=Window();w.metadata_controller=SimpleNamespace(migration_pending=True)
        for state in ('building','ready'):
            with self.subTest(state=state):
                w.settings={'source_truth_alias_next':dict(format='soundbox-source-alias-v1',
                    state=state,error='',scope={'source':'/synthetic/a/master.db'})}
                self.assertIn('Finish',w._source_selection_reason('/synthetic/b/master.db'))
                self.assertEqual(w._source_selection_reason('/synthetic/a/master.db'),'')
                self.assertIn('Finishing metadata',MainWindow._pending_save_reason(w))
        w.lib=Library({},[],'/synthetic/b/master.db')
        self.assertEqual(w._source_selection_reason('/synthetic/a/master.db'),'')

    def test_alias_worker_or_validation_error_reports_attention_instead_of_endless_finishing(self):
        w=Window();w.metadata_controller=SimpleNamespace(migration_pending=True)
        w.settings={'source_truth_alias_next':dict(format='soundbox-source-alias-v1',
            state='building',error='Alias plan storage failed',scope={'source':str(w.lib.source)})}
        self.assertIn('Alias plan storage failed',MainWindow._pending_save_reason(w))
        w.settings['source_truth_alias_next']={}
        w.settings['source_truth_migration']=dict(format='soundbox-source-truth-v1',
            scope={'source':str(w.lib.source)},cursor=0,order=[],unresolved=[])
        self.assertIn('attention',w._source_selection_reason('/synthetic/b/master.db'))
        self.assertIn('attention',MainWindow._pending_save_reason(w))
        w.metadata_controller.queue_error=lambda:'Saved origin identity changed'
        self.assertIn('Saved origin identity changed',MainWindow._pending_save_reason(w))

    def test_alias_or_migration_failure_reports_the_native_queue_error_before_finishing(self):
        w=Window();w.metadata_controller=SimpleNamespace(migration_pending=True,queue_error=lambda:'')
        for plan in ('source_truth_alias_next','source_truth_migration'):
            with self.subTest(plan=plan):
                w.settings={plan:dict(format='soundbox-source-alias-v1',state='ready',error=''),
                    'pending_metadata_edits':[dict(error='Native metadata conflict; retry required')]}
                self.assertIn('Native metadata conflict',MainWindow._pending_save_reason(w))
                self.assertNotIn('Finishing',MainWindow._pending_save_reason(w))
