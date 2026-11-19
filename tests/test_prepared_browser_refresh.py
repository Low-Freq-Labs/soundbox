"""Compatible native ACKs retain prepared rows without stale visibility/data."""
from dataclasses import replace
import threading
import unittest
from unittest import mock

from PySide6.QtCore import Qt

from desktop import prepared_browser
from soundbox import metadata, metadata_provenance, shared_imports
from soundbox.library import Library, LibraryTag, Track
from tests import test_prepared_native_publication as fixtures


class PreparedBrowserRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparedPublication.setUpClass()

    def setUp(self):
        fixture=fixtures.PreparedPublication()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.window=fixture.window

    def publish(self, library, ids=('2',)):
        self.window._publish_created_library(library,changed_ids=ids)

    def test_same_display_ack_reuses_model_and_only_prepares_affected_rows(self):
        w=self.window
        w.titles['2']='Frozen title'
        w._invalidate_rows(['2']);w._reload_current_view();w._prepare_artists()
        before=w.model._rows
        selected=w.model._rows[w.table.currentIndex().row()]['id']
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],name='Song 2 native',source_cue_revision='fresh CAS')})
        with mock.patch.object(w,'_row_for',wraps=w._row_for) as rows, \
                mock.patch.object(w,'_filter_artists',wraps=w._filter_artists) as refilter, \
                mock.patch.object(w,'_landed_time',wraps=w._landed_time) as landed:
            self.publish(fresh)
        self.assertTrue(rows.call_args_list)
        self.assertEqual({call.args[0].track_id for call in rows.call_args_list},{'2'})
        self.assertLessEqual(rows.call_count,2)
        self.assertIs(w.model._rows,before)
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['id'],selected)
        self.assertIs(w.tag_controller.lib,w.lib)
        self.assertEqual(w.lib.tracks_by_id['2'].source_cue_revision,'fresh CAS')
        self.assertIs(w._artists_library,w.lib)
        refilter.assert_not_called()
        self.assertEqual({call.args[0].track_id for call in landed.call_args_list},{'2'})

    def test_changed_row_reapplies_filter_sort_and_matches_complete_render(self):
        w=self.window
        w.model.set_filter('Revised')
        w.model.sort(0,Qt.DescendingOrder)
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],name='Revised title')})
        with mock.patch.object(w,'_row_for',wraps=w._row_for) as rows:
            self.publish(fresh)
        self.assertEqual({call.args[0].track_id for call in rows.call_args_list},{'2'})
        self.assertEqual([row['id'] for row in w.model._rows],['2'])
        actual=[prepared_browser.clean_row(row) for row in w.model._rows]
        w._show_collection()
        self.assertEqual([prepared_browser.clean_row(row) for row in w.model._rows],actual)
        self.assertEqual(w.model._filter,'revised')
        self.assertEqual(w.model._sort,('title',True))

    def test_compatible_scalar_publication_retains_both_sidebar_item_owners(self):
        w=self.window
        native=w.tree.topLevelItem(2)
        collection=w.sb_tree.topLevelItem(0)
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],name='Updated row')})
        with mock.patch.object(w,'_populate_tree',wraps=w._populate_tree) as populate:
            self.publish(fresh)
        populate.assert_not_called()
        self.assertIs(w.tree.topLevelItem(2),native)
        self.assertIs(w.sb_tree.topLevelItem(0),collection)
        self.assertEqual(next(row for row in w.model._rows if row['id']=='2')['title'],'Updated row')

    def test_changed_smart_membership_updates_the_retained_native_tree(self):
        w=self.window
        fresh=replace(w.lib,playlists=[replace(w.lib.playlists[0],keys=['1','1','2'])])
        with mock.patch.object(w,'_populate_tree',wraps=w._populate_tree) as populate:
            self.publish(fresh,())
        populate.assert_called_once()
        self.assertTrue(any('Smart' in item.text(0) and '(3)' in item.text(0)
            for item in w.tree.findItems('',Qt.MatchContains|Qt.MatchRecursive)))

    def test_sparse_collection_preserves_unicode_raw_sort_ties(self):
        w=self.window
        w.lib.tracks_by_id['1']=replace(w.lib.tracks_by_id['1'],name='ß')
        w.lib.tracks_by_id['2']=replace(w.lib.tracks_by_id['2'],name='ss')
        w._apply_loaded_library();w._show_collection()
        expected=[row['id'] for row in w.model._all_rows]
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_cue_revision='fresh')})
        self.publish(fresh)
        self.assertEqual([row['id'] for row in w.model._all_rows],expected)

    def test_sparse_raw_title_reorders_match_complete_render_with_equal_title_ties(self):
        w=self.window
        for name in ('Song 1','ß','ss','Song 3'):
            with self.subTest(name=name):
                fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
                    '2':replace(w.lib.tracks_by_id['2'],name=name)})
                self.publish(fresh)
                actual=[prepared_browser.clean_row(row) for row in w.model._all_rows]
                w._show_collection()
                self.assertEqual([prepared_browser.clean_row(row) for row in w.model._all_rows],actual)

    def test_global_registry_change_retains_complete_refresh(self):
        w=self.window
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],artist='New trusted artist')})
        with mock.patch.object(w,'_row_for',wraps=w._row_for) as rows:
            self.publish(fresh)
        self.assertEqual({call.args[0].track_id for call in rows.call_args_list},set(w.lib.tracks_by_id))
        self.assertEqual(next(row for row in w.model._rows if row['id']=='2')['artist'],'New trusted artist')

    def test_unpainted_local_invalidation_does_not_reuse_collection_rows(self):
        w=self.window
        w.titles['1']='A newer local title'
        w._invalidate_rows(['1'])
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_cue_revision='fresh')})
        with mock.patch.object(w,'_row_for',wraps=w._row_for) as rows:
            self.publish(fresh)
        self.assertIn('1',{call.args[0].track_id for call in rows.call_args_list})
        self.assertEqual(next(row for row in w.model._rows if row['id']=='1')['title'],'A newer local title')

    def test_visibility_and_tag_assignment_changes_refresh_all_current_rows(self):
        w=self.window
        w.hidden.add('1')
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_tag_ids=['42'])},
            source_tags=[LibraryTag('42','Fresh tag')])
        with mock.patch.object(w,'_row_for',wraps=w._row_for) as rows:
            self.publish(fresh,())
        self.assertEqual({row['id'] for row in w.model._rows},{'2','3'})
        self.assertEqual({call.args[0].track_id for call in rows.call_args_list},{'2','3'})
        self.assertEqual(next(row for row in w.model._rows if row['id']=='2')['myTags'],'Fresh tag')

    def test_other_domain_scalar_invalidation_refreshes_input_inventory(self):
        w=self.window
        w.lib.tracks_by_id['1']=replace(w.lib.tracks_by_id['1'],bpm='145')
        w._invalidate_rows(['1']);w._reload_current_view();w._prepare_artists()
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_cue_revision='fresh')})
        self.publish(fresh)
        self.assertEqual(w._display_track_inputs['1'][4],'145')
        self.assertEqual(next(row for row in w.model._rows if row['id']=='1')['bpm'],'145')

    def test_in_place_unlisted_track_change_cannot_hide_behind_current_library_match(self):
        w=self.window
        w.lib.tracks_by_id['1'].name='A newer mutable raw title'
        w.lib.tracks_by_id['1'].bpm='145'
        w.lib.tracks_by_id['1'].info['comments']='Fresh mutable comment'
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_cue_revision='fresh')})
        self.publish(fresh)
        row=next(row for row in w.model._rows if row['id']=='1')
        self.assertEqual(row['title'],'A newer mutable raw title')
        self.assertEqual(row['bpm'],'145')
        self.assertEqual(row['comments'],'Fresh mutable comment')
        self.assertEqual(w._display_track_inputs['1'][1],'A newer mutable raw title')
        actual=[prepared_browser.clean_row(row) for row in w.model._rows]
        w._show_collection()
        self.assertEqual([prepared_browser.clean_row(row) for row in w.model._rows],actual)

    def test_registry_votes_reuse_order_independent_counts_but_rebuild_changes(self):
        w=self.window
        previous=w.lib.artist_registry
        w.lib=replace(w.lib,tracks_by_id=dict(reversed(list(w.lib.tracks_by_id.items()))))
        with mock.patch('desktop.app.metadata.build_registry',wraps=metadata.build_registry) as build:
            w._build_artist_registry(invalidate_rows=False)
            build.assert_not_called()
            self.assertIs(w.lib.artist_registry,previous)
            w.lib.tracks_by_id['2']=replace(w.lib.tracks_by_id['2'],artist='Another artist')
            w._build_artist_registry(invalidate_rows=False)
            self.assertGreater(build.call_count,0)

    def test_context_detaches_catalog_occurrences_and_nested_organization(self):
        w=self.window
        w.settings['playlist_icons']={'folder':{'color':'blue'}}
        before=prepared_browser.context(w)
        w.settings['playlist_icons']['folder']['color']='red'
        self.assertNotEqual(prepared_browser.context(w),before)
        before=prepared_browser.context(w)
        w.lib.playlists[0].keys[:]=['1','1','2']
        self.assertNotEqual(prepared_browser.context(w),before)
        before=prepared_browser.context(w)
        w.lib.source_playlist_catalog['nodes'][0]['Name']='Renamed smart playlist'
        self.assertNotEqual(prepared_browser.context(w),before)

    def test_exact_worker_projection_owner_bypasses_gui_projection(self):
        w=self.window
        owner=[];w.settings[shared_imports.BINDINGS]=owner
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],source_cue_revision='fresh')})
        with mock.patch('desktop.app.shared_imports.project',side_effect=AssertionError('GUI reprojection')):
            self.assertTrue(w._on_refresh_done(fresh,prepared_native_ids=('2',),
                projected_bindings_owner=owner,projected_scope=(str(fresh.source),fresh.source_library_id)))
        self.assertEqual(w.lib.tracks_by_id['2'].source_cue_revision,'fresh')

    def test_absent_worker_projection_proof_uses_normal_projection(self):
        w=self.window
        owner=[];w.settings[shared_imports.BINDINGS]=owner
        fresh=replace(w.lib)
        with mock.patch('desktop.app.shared_imports.project',wraps=shared_imports.project) as project:
            self.assertTrue(w._on_refresh_done(fresh,prepared_native_ids=()))
        project.assert_called_once_with(fresh,owner,unavailable={})

    def test_stale_worker_projection_owner_or_scope_refuses_before_curation(self):
        w=self.window
        owner=[];w.settings[shared_imports.BINDINGS]=owner
        previous=w.lib;fresh=replace(previous)
        for proof_owner,scope in ((list(owner),(str(fresh.source),fresh.source_library_id)),
                                  (owner,('/changed/source.db',fresh.source_library_id))):
            with self.subTest(scope=scope,exact_owner=proof_owner is owner):
                with mock.patch.object(w,'_accept_source_curation') as curate, \
                        mock.patch('desktop.app.shared_imports.project',side_effect=AssertionError('stale projection')):
                    self.assertFalse(w._on_refresh_done(fresh,prepared_native_ids=(),
                        projected_bindings_owner=proof_owner,projected_scope=scope))
                curate.assert_not_called()
                self.assertIs(w.lib,previous)

    def test_prepared_cache_fingerprint_runs_in_engine_helper_not_gui(self):
        from soundbox import browser_cache
        w=self.window;gui=threading.get_ident();threads=[]
        fingerprint=browser_cache.fingerprint
        service=w._cache_process;write=service.write
        def measured(*args,**kwargs):
            threads.append(threading.get_ident())
            return write(*args,**kwargs)
        fresh=replace(w.lib,tracks_by_id={**w.lib.tracks_by_id,
            '2':replace(w.lib.tracks_by_id['2'],name='A fresh cached title')})
        with mock.patch.object(service,'write',side_effect=measured), \
                mock.patch('desktop.app.browser_cache.fingerprint',side_effect=AssertionError('No parent fingerprint')):
            self.publish(fresh)
            worker=w._libcache_worker
            self.assertIsNotNone(worker);self.assertTrue(worker.wait(4000))
            self.assertTrue(threads);self.assertTrue(all(identity!=gui for identity in threads))
        self.assertTrue(service.available)
        key=fingerprint(w._display_inputs)
        cached=browser_cache.load(w._data_file.with_name('browsercache.json'),key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached['2']['title'],'A fresh cached title')
        # A worker never adopts its key onto a possibly newer GUI generation.
        self.assertIsNone(w._browser_cache_key)

    def test_unpainted_cache_generation_cannot_publish_rows_under_an_old_fingerprint(self):
        w=self.window
        path=w._data_file.with_name('browsercache.json')
        before=path.read_bytes() if path.exists() else None
        w._invalidate_rows(['1'])
        with mock.patch('desktop.app.browser_cache.fingerprint',side_effect=AssertionError('stale input key')), \
                mock.patch('desktop.app.browser_cache.dump') as dump:
            w._start_library_cache(w.lib)
            self.assertTrue(w._libcache_worker.wait(4000))
        dump.assert_not_called()
        self.assertEqual(path.read_bytes() if path.exists() else None,before)

    def test_state_maintenance_preserves_cache_owner_admission_after_restore_boundary(self):
        w=self.window;owner=w._cache_tasks;service=w._cache_process
        with mock.patch.object(w,'_save_state',return_value=True):
            self.assertTrue(w._begin_state_maintenance(restoring=True))
        self.assertIs(w._cache_tasks,owner)
        self.assertFalse(owner.stopping)
        self.assertIs(w._cache_process,service)
        w._end_state_maintenance()
        w._start_library_cache(w.lib)
        self.assertTrue(w._libcache_worker.wait(4000))
        self.assertTrue(service.available)
        fixtures.PreparedPublication.app.processEvents()
        self.assertIsNone(w._libcache_worker)


class PreparedRegistryInputs(unittest.TestCase):
    def setUp(self):
        self.library=Library({str(i):Track(str(i),'Artist - Song '+str(i),'Artist',
            f'file:///synthetic/{i}.wav',source_uuid='uuid-'+str(i),source_native_path=f'/synthetic/{i}.wav')
            for i in (1,2)},[],'/synthetic/master.db',source_kind='rekordbox-db',source_library_id='library')
        scope=dict(source=str(self.library.source),kind=self.library.source_kind,library_id='library')
        self.origins={}
        for track in self.library.tracks_by_id.values():
            origin=dict(scope=scope,local_id=track.track_id,native_id=track.native_id,
                uuid=track.source_uuid,path=track.source_native_path,
                native_title=track.name,native_artist=track.artist)
            self.origins[track.track_id]=metadata_provenance.seed(origin,title=track.name,
                artist=track.artist,display_artist=track.artist)
        self.overrides={}
        self.previous=prepared_browser.registry_inputs(self.library,self.origins,self.overrides)[2]

    def assert_matches_fresh(self):
        actual=prepared_browser.registry_inputs(self.library,self.origins,self.overrides,self.previous)
        expected=prepared_browser.registry_inputs(self.library,self.origins,self.overrides)
        self.assertEqual(actual[:2],expected[:2])
        self.previous=actual[2]

    def test_unchanged_library_owner_replacement_avoids_repeated_derivation(self):
        self.library=replace(self.library,tracks_by_id=dict(self.library.tracks_by_id))
        with mock.patch.object(prepared_browser.metadata_provenance,'display_registry_inputs',
                               side_effect=AssertionError('unchanged provenance reparsed')):
            actual=prepared_browser.registry_inputs(self.library,self.origins,self.overrides,self.previous)
        self.assertEqual(actual[2]['rows'],self.previous['rows'])
        self.assertFalse(actual[2]['changed_ids'])

    def test_in_place_nested_origin_change_rederives_only_changed_song(self):
        self.origins['2']['provenance']['raw']['artist']='A changed trusted artist'
        with mock.patch.object(prepared_browser.metadata_provenance,'display_registry_inputs',
                               wraps=metadata_provenance.display_registry_inputs) as derive:
            actual=prepared_browser.registry_inputs(self.library,self.origins,self.overrides,self.previous)
        self.assertEqual([call.args[0].track_id for call in derive.call_args_list],['2'])
        self.assertEqual(actual[:2],prepared_browser.registry_inputs(self.library,self.origins,self.overrides)[:2])
        self.assertEqual(self.previous['rows']['2'][1]['provenance']['raw']['artist'],'Artist')

    def test_scalar_identity_source_and_binding_changes_match_complete_derivation(self):
        for changes in ({'name':'Revised - Raw title'}, {'artist':'New spelling'},
                        {'source_uuid':'replaced'}, {'source_native_path':'/synthetic/moved.wav'},
                        {'location_uri':'file:///synthetic/new.wav'}):
            with self.subTest(changes=changes):
                self.library.tracks_by_id['2']=replace(self.library.tracks_by_id['2'],**changes)
                self.assert_matches_fresh()
        self.library.source_binding_issues['1']='ambiguous native binding'
        self.assert_matches_fresh()
        self.library=replace(self.library,source='/other/master.db',source_library_id='other')
        self.assert_matches_fresh()

    def test_origin_duplicates_and_override_mutations_match_complete_derivation(self):
        self.overrides['2']='An explicit artist';self.assert_matches_fresh()
        self.overrides['2']='A newer explicit artist';self.assert_matches_fresh()
        self.origins['duplicate']=self.origins['1'];self.assert_matches_fresh()
        self.origins.pop('duplicate');self.assert_matches_fresh()
        self.origins.pop('1');self.assert_matches_fresh()
