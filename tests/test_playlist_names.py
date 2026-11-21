"""Unique playlist names span source folders, custom lists and queued intentions."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import unittest
from unittest import mock

from desktop.playlist_names import playlist_name_taken
from soundbox.library import Library, LibraryTag, Playlist
from soundbox.playlist_names import unique_playlist_names
from soundbox.rekordbox_playlist_store import request_for
from soundbox import playlist_unification as merge
from tests import test_ui_source_playlists as fixture


class PlaylistNamePolicy(unittest.TestCase):
    def setUp(self):
        self.library = Library({}, [Playlist('DUBSTEP', ['GENRES'], [])], '/synthetic/source.xml',
                               source_library_id='library', source_playlist_catalog={'nodes': [], 'members': [], 'revision': 'a' * 64})
        self.window = SimpleNamespace(lib=self.library, custom={'Favorites': []}, settings={})

    def test_pidless_xml_native_and_custom_names_share_normalized_namespace(self):
        for name in (' dubstep ', 'DuBsTeP', ' FAVORITES ', 'Collection', 'Artists'):
            with self.subTest(name=name):self.assertTrue(playlist_name_taken(self.window, name))
        self.library.playlists.append(Playlist('Café', ['OTHER'], [], 'native'))
        self.assertTrue(playlist_name_taken(self.window, ' cafe\u0301 '))
        self.assertFalse(playlist_name_taken(self.window, 'New name'))
        self.assertFalse(playlist_name_taken(self.window, 'favorites', custom_name='Favorites'))

    def test_rendered_leaf_duplicates_refuse_folder_case_and_unicode_disambiguation(self):
        for names in (['Dubstep', ' DUBSTEP '], ['Café', 'Cafe\u0301'], ['', 'Valid']):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'must be unique'):
                unique_playlist_names(names)
        self.assertEqual(unique_playlist_names([' DUBSTEP ', 'House']), ['DUBSTEP', 'House'])

    def test_queued_smart_creation_and_errored_head_reserve_future_names(self):
        catalog = self.library.source_playlist_catalog
        first = request_for(catalog, 'create', name='Blocked head')
        tag = LibraryTag('201', 'Tag', 'Genre', str(uuid4()), '200', str(uuid4()))
        later = request_for(catalog, 'create_smart', name='Queued Smart', tags=[tag])
        pending = [dict(source=str(self.library.source), library_id='library', request=first, error='Needs attention'),
                   dict(source=str(self.library.source), library_id='library', request=later, error='')]
        self.assertTrue(playlist_name_taken(self.window, ' QUEUED SMART ', pending=pending))
        pending[1]['library_id'] = 'different'
        self.assertFalse(playlist_name_taken(self.window, 'Queued Smart', pending=pending))

    def test_rename_excludes_only_its_exact_identity_and_merge_binding(self):
        identity, uuid = '1000', str(uuid4())
        self.library.playlists = [Playlist('Favorites', [], [], identity)]
        self.assertTrue(playlist_name_taken(self.window, 'favorites', native_id=identity, native_uuid=uuid))
        state = merge.new(self.library.source, self.library.source_library_id)
        state['nodes']['custom:Favorites'] = dict(id=identity, uuid=uuid)
        self.window.settings[merge.KEY] = state
        self.assertFalse(playlist_name_taken(self.window, 'favorites', native_id=identity, native_uuid=uuid))
        self.assertTrue(playlist_name_taken(self.window, 'favorites', native_id=identity, native_uuid=str(uuid4())))
        self.library.playlists.append(Playlist('FAVORITES', ['OTHER'], [], 'other'))
        self.assertTrue(playlist_name_taken(self.window, 'favorites', native_id=identity, native_uuid=uuid))

    def completed_migration(self, name='Favorites', identity='1000'):
        self.library.source_kind = 'rekordbox-db'
        state = merge.new(self.library.source, self.library.source_library_id)
        state['nodes']['custom:'+name] = dict(id=identity, uuid=str(uuid4()))
        state['customs_done'] = True
        self.window.settings[merge.KEY] = state
        return state

    def test_unpublished_migration_mapping_checks_its_own_name_without_mutating_settings(self):
        state = merge.new(self.library.source, self.library.source_library_id)
        self.window.settings[merge.KEY] = state
        proposed = deepcopy(state)
        uuid = str(uuid4())
        proposed['nodes']['custom:Favorites'] = dict(id='1000', uuid=uuid)
        original = deepcopy(self.window.settings)
        self.assertTrue(playlist_name_taken(self.window, 'Favorites', native_id='1000', native_uuid=uuid))
        self.assertFalse(playlist_name_taken(self.window, 'Favorites', native_id='1000', native_uuid=uuid,
                                            unification_state=proposed))
        self.assertEqual(self.window.settings, original)
        self.library.playlists.append(Playlist('Favorites', [], [], 'other'))
        self.assertTrue(playlist_name_taken(self.window, 'Favorites', native_id='1000', native_uuid=uuid,
                                           unification_state=proposed))

    def test_completed_migration_archive_does_not_reserve_deleted_or_renamed_name(self):
        state = self.completed_migration()
        self.window.custom['Favorites'] = ['preserved-song', 'preserved-song']
        original = deepcopy((self.window.custom, self.window.settings))
        self.assertFalse(playlist_name_taken(self.window, ' FAVORITES '))
        self.library.playlists.append(Playlist('Renamed', [], [], '1000'))
        self.assertFalse(playlist_name_taken(self.window, 'favorites'))
        self.assertTrue(playlist_name_taken(self.window, 'renamed'))
        self.assertEqual((self.window.custom, self.window.settings), original)
        self.assertIs(self.window.settings[merge.KEY], state)

    def test_deleted_smart_tombstone_and_migrated_archive_allow_zyko_recreation(self):
        state = self.completed_migration('ZYKO', '3107002670')
        self.window.custom['ZYKO'] = ['preserved-song']
        self.library.source_playlist_catalog['nodes'] = [dict(
            ID='3107002670', UUID=state['nodes']['custom:ZYKO']['uuid'],
            Name='ZYKO', Attribute=4, rb_local_deleted=1)]
        self.assertFalse(playlist_name_taken(self.window, ' zyko '))
        self.library.source_playlist_catalog['nodes'][0]['rb_local_deleted'] = 0
        self.assertTrue(playlist_name_taken(self.window, 'ZYKO'))

    def test_active_native_and_unbound_custom_names_still_reserve_after_migration(self):
        self.completed_migration()
        self.library.playlists.append(Playlist('FAVORITES', [], [], 'different-native-id'))
        self.assertTrue(playlist_name_taken(self.window, 'favorites'))
        self.window.custom['Independent'] = ['other-song']
        self.assertTrue(playlist_name_taken(self.window, ' INDEPENDENT '))

    def test_unfinished_invalid_other_source_and_xml_migrations_do_not_release_custom(self):
        state = self.completed_migration()
        for change in ('unfinished', 'invalid', 'other-source', 'xml'):
            with self.subTest(change=change):
                altered = deepcopy(state)
                self.library.source_kind = 'rekordbox-db'
                if change == 'unfinished':altered['customs_done'] = False
                elif change == 'invalid':altered['nodes']['custom:Favorites']['uuid'] = 'invalid'
                elif change == 'other-source':altered['library_id'] = 'different-library'
                else:self.library.source_kind = 'rekordbox-xml'
                self.window.settings[merge.KEY] = altered
                self.assertTrue(playlist_name_taken(self.window, 'favorites'))

    def test_pending_creation_reserves_released_name_even_behind_failed_head(self):
        self.completed_migration()
        catalog = self.library.source_playlist_catalog
        pending = [dict(source=str(self.library.source), library_id=self.library.source_library_id,
                        request=request_for(catalog, 'create', name='FAVORITES'), error='Save needs attention')]
        self.assertTrue(playlist_name_taken(self.window, ' favorites ', pending=pending))
        pending.clear()
        self.assertFalse(playlist_name_taken(self.window, 'favorites', pending=pending))

    def test_projected_delete_releases_archive_name_and_failed_delete_keeps_live_claim(self):
        from soundbox.playlist_presentation import project
        self.completed_migration()
        self.library.source_playlist_catalog = fixture.native_catalog('Favorites')
        self.library.playlists = [Playlist('Favorites', [], [], '1000')]
        pending = [dict(source=str(self.library.source), library_id=self.library.source_library_id,
                        request=request_for(self.library.source_playlist_catalog, 'delete', identity='1000'), error='')]
        self.window._native_presentation = lambda:project(self.library, pending)
        self.assertFalse(playlist_name_taken(self.window, 'Favorites', pending=pending))
        pending[0]['error'] = 'Deletion not saved'
        self.assertTrue(playlist_name_taken(self.window, 'Favorites', pending=pending))


class PlaylistNameAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.NativePlaylistGestures.setUpClass()

    def setUp(self):
        self.case = fixture.NativePlaylistGestures(); self.case.setUp()
        def finish():
            try:self.case.tearDown()
            finally:self.case.doCleanups()
        self.addCleanup(finish)
        self.case.controller.schedule = mock.Mock()

    def test_queued_creation_cannot_admit_a_second_normalized_name_in_another_folder(self):
        case = self.case; catalog = case.window.lib.source_playlist_catalog
        first = request_for(catalog, 'create', name='Future')
        self.assertTrue(case.controller.enqueue(first))
        saves = case.window._save_state.call_count
        second = request_for(catalog, 'create', name=' FUTURE ')
        self.assertFalse(case.controller.enqueue(second))
        self.assertEqual(case.window._save_state.call_count, saves)
        self.assertEqual(len(case.controller.pending), 1); self.assertEqual(case.calls, [])

    def test_case_only_self_rename_is_admitted_but_other_native_playlist_collision_is_not(self):
        case = self.case; catalog = case.window.lib.source_playlist_catalog
        self.assertTrue(case.controller.enqueue(request_for(catalog, 'rename', identity='1000', name='LIST')))
        other = replace(case.window.lib.playlists[0], pid='1001', name='Elsewhere', folder_path=['OTHER'])
        case.window.lib.playlists.append(other)
        catalog['nodes'].append({**catalog['nodes'][0], 'ID': '1001', 'UUID': str(uuid4()),
                                 'Name': 'Elsewhere', 'Seq': 2})
        self.assertFalse(case.controller.enqueue(request_for(catalog, 'rename', identity='1000', name=' ELSEWHERE ')))
        self.assertEqual(len(case.controller.pending), 1); self.assertEqual(case.calls, [])
