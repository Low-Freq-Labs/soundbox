"""Visible browser actions and source-bound, committed playback occurrences."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt

from desktop.cdj import CdjDeviceMixin
from desktop.model import COLUMNS
from desktop.playback_navigation import song_pin, source_scope
from soundbox.library import Library, Track
from tests import test_browser_readiness as fixtures


class BrowserPlaybackNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.LibraryBrowserReadiness.setUpClass()
        cls.app = fixtures.LibraryBrowserReadiness.app

    def setUp(self):
        fixture = fixtures.LibraryBrowserReadiness()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.w = fixture.window
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        tracks = {}
        for identity, name in [('a', 'Alpha'), ('b', 'Bravo'), ('c', 'Charlie')]:
            path = root / (identity + '.wav')
            path.write_bytes(b'Generated placeholder; never decoded')
            tracks[identity] = Track(identity, name, 'Artist ' + identity, path.as_uri(),
                                     source_uuid='uuid-' + identity, added_ts=1)
        self.w.lib = Library(tracks, [], '/synthetic/source-A.xml')
        self.w._build_artist_registry()
        self.w._invalidate_rows()
        self.w._prepare_artists()
        self.rows(['a', 'a', 'b', 'c'])
        self.enterContext(mock.patch.object(self.w, '_sync_cue_editing'))
        self.enterContext(mock.patch.object(self.w, '_analysis_result', return_value=None))
        self.enterContext(mock.patch.object(self.w.player_bar, 'play_track', side_effect=self.play))
        self.pending_rebind = self.enterContext(mock.patch.object(
            self.w.player_bar, 'rebind_pending_path', side_effect=self.rebind_pending, create=True))
        self.loads = []
        self.pending = None
        self.deferred = False

    def rows(self, identities, *, native=False):
        self.w.stack.setCurrentIndex(0)
        self.w._view_key = 'sb:Authored set'
        rows = self.w._rows_for([self.w.lib.tracks_by_id[key] for key in identities])
        if native:
            for index, row in enumerate(rows):
                row['_native_occurrence'] = ('captured-membership', index)
        self.w.model.set_rows(rows)

    def play(self, identity, path, **options):
        if self.pending is not None:
            self.pending[2]['on_cancelled']()
        self.pending = identity, str(path), options
        self.loads.append(self.pending)
        if not self.deferred:
            self.start()

    def start(self):
        identity, path, options = self.pending
        self.pending = None
        self.w.player_bar._track_id = identity
        self.w.player_bar._src_path = path
        options['on_started']()

    def cancel(self):
        pending, self.pending = self.pending, None
        pending[2]['on_cancelled']()

    def rebind_pending(self, identity, old, new):
        if self.pending is None or self.pending[:2] != (identity, old):
            return False
        self.pending = identity, new, self.pending[2]
        return True

    def load_row(self, row):
        self.w._play_row(self.w.model.index(row, 0))

    def selected_row(self):
        return self.w.table.currentIndex().row()

    def test_next_and_previous_walk_duplicate_occurrences_and_restart_the_second_copy(self):
        self.load_row(0)
        self.w._play_relative(1)
        self.assertEqual(self.selected_row(), 1)
        self.assertEqual(self.w._autoplay_index, 1)
        self.assertTrue(self.loads[-1][2]['force_reload'])
        self.w._play_relative(1)
        self.assertEqual(self.selected_row(), 2)
        self.w._play_relative(1)
        self.assertEqual(self.selected_row(), 3)
        self.w._play_relative(-1)
        self.assertEqual(self.selected_row(), 2)
        self.assertEqual([value[0] for value in self.loads], ['a', 'a', 'b', 'c', 'b'])

    def test_loading_the_second_occurrence_keeps_its_highlight_and_direction(self):
        self.load_row(1)
        self.assertEqual(self.selected_row(), 1)
        self.w._highlight_now_playing('a')
        self.assertEqual(self.selected_row(), 1)
        self.assertEqual(self.w._autoplay_index, 1)
        self.w._play_relative(1)
        self.assertEqual(self.loads[-1][0], 'b')

    def test_rapid_next_uses_requested_cursor_and_stale_cancel_cannot_undo_latest_start(self):
        self.load_row(0)
        self.deferred = True
        self.w._play_relative(1)
        obsolete = self.pending[2]
        self.w._play_relative(1)
        self.assertEqual([value[0] for value in self.loads], ['a', 'a', 'b'])
        self.assertEqual(self.w._autoplay_index, 0)
        self.assertEqual(self.w.player_bar.current_track(), 'a')
        self.start()
        obsolete['on_started']()
        obsolete['on_cancelled']()
        self.assertEqual(self.w.player_bar.current_track(), 'b')
        self.assertEqual(self.selected_row(), 2)
        self.assertEqual(self.w._autoplay_index, 2)

    def test_cancelled_fade_restores_committed_cursor_without_replacing_autoplay(self):
        self.load_row(0)
        old_order = self.w._autoplay_context
        self.deferred = True
        self.w._play_relative(1)
        self.cancel()
        self.assertIs(self.w._autoplay_context, old_order)
        self.assertEqual(self.w._autoplay_index, 0)
        self.w._play_relative(1)
        self.assertEqual(self.w._navigation().requested.index, 1)

    def test_failed_preflight_and_accepted_later_error_do_not_rewind_other_occurrences(self):
        self.load_row(0)
        track = self.w.lib.tracks_by_id['b']
        missing = replace(track, location_uri='file:///generated/missing.wav')
        self.assertFalse(self.w._load_track(missing))
        self.assertEqual(self.w._autoplay_index, 0)
        self.load_row(2)
        with mock.patch.object(self.w, '_flash_status'):
            self.w._on_playback_error('Synthetic decoder error after accepted load')
        self.assertEqual(self.w._autoplay_index, 2)
        self.assertEqual(self.w._navigation().committed.index, 2)

    def test_stale_track_object_with_existing_old_file_cannot_adopt_current_identity(self):
        self.load_row(0)
        track = self.w.lib.tracks_by_id['b']
        target = track.path.with_name('replacement-b.wav')
        target.write_bytes(b'Generated replacement; never decoded')
        self.w.lib.tracks_by_id['b'] = replace(
            track, location_uri=target.as_uri(), source_uuid='replacement-b')
        self.assertTrue(track.path.is_file())
        self.assertFalse(self.w._load_track(track))
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(self.w._autoplay_index, 0)

    def test_late_start_does_not_steal_a_newer_browser_selection(self):
        self.load_row(0)
        self.deferred = True
        self.w._play_relative(1)
        self.w.table.setCurrentIndex(self.w.model.index(3, 0))
        self.start()
        self.assertEqual(self.selected_row(), 3)
        self.assertEqual(self.w._autoplay_index, 1)

    def test_same_occurrence_ack_during_fade_does_not_suppress_the_committed_highlight(self):
        self.load_row(0)
        self.deferred = True
        self.w._play_relative(1)
        before = self.w._selection_revision
        self.w._restore_browser_seat(self.w._browser_seat())
        self.assertGreater(self.w._selection_revision, before)
        self.start()
        self.assertEqual(self.selected_row(), 1)
        self.assertEqual(self.w._autoplay_index, 1)

    def test_source_replacement_blocks_navigation_and_autoplay_with_reused_ids(self):
        self.load_row(0)
        self.w._autoplay_mode = 'down'
        old = self.w.lib
        self.w.lib = replace(old, source='/synthetic/source-B.xml', tracks_by_id={
            key: replace(track, source_uuid='other-' + key) for key, track in old.tracks_by_id.items()})
        self.w._play_relative(1)
        self.w._advance_autoplay('a')
        self.assertEqual(len(self.loads), 1)
        self.w._update_player_navigation()
        self.assertFalse(self.w.player_bar.next_btn.isEnabled())

    def test_compatible_library_ack_preserves_context_but_changed_target_identity_is_not_adopted(self):
        self.load_row(1)
        old = self.w.lib
        self.w.lib = replace(old, tracks_by_id={key: replace(track, name=track.name + ' ACK')
                                             for key, track in old.tracks_by_id.items()})
        self.w._play_relative(1)
        self.assertEqual(self.loads[-1][0], 'b')
        self.w._autoplay_mode = 'down'
        self.w.lib.tracks_by_id['c'] = replace(self.w.lib.tracks_by_id['c'], source_uuid='replacement-c')
        self.assertFalse(self.w._autoplay_playable(3))

    def test_accepted_native_registration_keeps_stable_import_occurrences_and_autoplay(self):
        from soundbox.native_identity import Binding, project
        identity = 'sb-0123456789ab'
        track = replace(self.w.lib.tracks_by_id['a'], track_id=identity, source_uuid='', imported=True)
        self.w.lib = replace(self.w.lib, source_kind='rekordbox-db', source_library_id='123',
                             tracks_by_id={identity:track,'b':self.w.lib.tracks_by_id['b']})
        self.rows([identity, identity, 'b'])
        self.load_row(0)
        uuid = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        native = replace(track, track_id='42', imported=False, source_uuid=uuid)
        incoming = replace(self.w.lib, tracks_by_id={'42':native,'b':self.w.lib.tracks_by_id['b']})
        self.w.lib = project(incoming, [Binding(identity, str(incoming.source), '123', '42', uuid,
                                                 str(track.path))])
        self.assertEqual(self.w.lib.tracks_by_id[identity].native_id, '42')
        self.assertTrue(self.w._autoplay_playable(0))
        self.w._play_relative(1)
        self.assertEqual(self.w._autoplay_index, 1)
        self.assertTrue(self.loads[-1][2]['force_reload'])
        self.w._autoplay_mode = 'down'
        self.w._advance_autoplay(identity)
        self.assertEqual(self.loads[-1][0], 'b')

    def test_loading_same_song_in_another_browser_view_does_not_restart_audio(self):
        self.load_row(0)
        self.w._show_artists()
        self.w._load_track(self.w.lib.tracks_by_id['a'])
        self.assertFalse(self.loads[-1][2]['force_reload'])

    def accepted_path(self, identity):
        track = self.w.lib.tracks_by_id[identity]
        old = str(track.path)
        previous = source_scope(self.w.lib), song_pin(track)
        target = track.path.with_name('converted-' + track.path.name)
        target.write_bytes(b'Generated converted placeholder; never decoded')
        self.w.lib.tracks_by_id[identity] = replace(
            track, location_uri=target.as_uri(), source_native_path=str(target))
        self.w._rebind_playback_path(identity, old, str(target), previous=previous)
        return old, target

    def test_accepted_conversion_preserves_duplicate_cursor_order_and_decoder_ownership(self):
        self.load_row(1)
        before = self.w._autoplay_context
        old, target = self.accepted_path('a')
        self.assertEqual(self.w.player_bar._src_path, old)  # The conversion owner alone changes the decoder.
        self.assertEqual(self.w._autoplay_index, 1)
        self.assertEqual([entry.token for entry in self.w._autoplay_context.entries],
                         [entry.token for entry in before.entries])
        self.assertEqual(self.w._navigation().committed.entry.song[3], str(target))
        self.w._play_relative(-1)
        self.assertEqual(self.loads[-1][:2], ('a', str(target)))
        self.assertTrue(self.loads[-1][2]['force_reload'])
        self.assertEqual(self.w._autoplay_index, 0)

    def test_accepted_future_conversion_retains_captured_autoplay_while_browsing_elsewhere(self):
        self.load_row(1)
        self.w._autoplay_mode = 'down'
        self.w.search.setText('Charlie')
        _old, target = self.accepted_path('b')
        self.assertEqual(self.w._autoplay_order, ['a', 'a', 'b', 'c'])
        self.assertTrue(self.w._autoplay_playable(2))
        self.w._advance_autoplay('a')
        self.assertEqual(self.loads[-1][:2], ('b', str(target)))
        self.assertEqual(self.w._autoplay_index, 2)

    def test_accepted_path_change_during_fade_commits_the_rebound_requested_context(self):
        self.load_row(0)
        self.deferred = True
        self.w._play_relative(1)
        _old, target = self.accepted_path('a')
        self.assertEqual(self.w._navigation().requested.entry.song[3], str(target))
        self.pending_rebind.assert_called_once_with('a', _old, str(target))
        self.start()
        self.assertEqual(self.w.player_bar._src_path, str(target))
        self.assertEqual(self.w._autoplay_context.entries[1].song[3], str(target))
        self.assertTrue(self.w._autoplay_context.valid(self.w.lib, 1))
        self.assertEqual(self.w._autoplay_index, 1)

    def test_path_rebind_does_not_adopt_changed_source_uuid_native_id_or_unknown_paths(self):
        self.load_row(0)
        library = self.w.lib
        track = library.tracks_by_id['a']
        before = self.w._autoplay_context
        previous = source_scope(library), song_pin(track)
        target = track.path.with_name('accepted-target.wav')
        converted = replace(track, location_uri=target.as_uri(), source_native_path=str(target))
        for drift in ('source', 'uuid', 'native-id', 'old-path', 'new-path', 'native-path'):
            with self.subTest(drift=drift):
                self.w.lib = replace(library, tracks_by_id={**library.tracks_by_id, 'a': converted})
                old_path, new_path = str(track.path), str(target)
                if drift == 'source':
                    self.w.lib.source_library_id = 'another-source'
                elif drift == 'uuid':
                    self.w.lib.tracks_by_id['a'] = replace(converted, source_uuid='another-song')
                elif drift == 'native-id':
                    self.w.lib.tracks_by_id['a'] = replace(converted, source_native_id='different')
                elif drift == 'old-path':
                    old_path = '/unobserved/path.wav'
                elif drift == 'new-path':
                    new_path = '/unaccepted/target.wav'
                else:
                    self.w.lib.tracks_by_id['a'] = replace(converted, source_native_path='/foreign/native.wav')
                self.w._rebind_playback_path('a', old_path, new_path, previous=previous)
                self.assertIs(self.w._autoplay_context, before)
                self.assertEqual(self.w._navigation().committed.entry.song, song_pin(track))

    def test_filter_sort_and_stable_native_occurrence_keep_the_exact_loaded_copy(self):
        self.rows(['a', 'a', 'b', 'c'], native=True)
        self.load_row(1)
        title = next(i for i, row in enumerate(COLUMNS) if row[0] == 'title')
        self.w.table.sortByColumn(title, Qt.DescendingOrder)
        self.w._highlight_now_playing('a')
        self.assertEqual(self.w.model._rows[self.selected_row()]['_native_occurrence'],
                         ('captured-membership', 1))
        self.w.search.setText('Bravo')
        self.w._play_relative(1)
        self.assertEqual(len(self.loads), 1)
        self.w.search.clear()
        self.w.table.sortByColumn(-1, Qt.AscendingOrder)
        self.w._play_relative(1)
        self.assertEqual(self.loads[-1][0], 'b')

    def test_autoplay_commits_its_new_index_only_after_the_replacement_starts(self):
        self.load_row(0)
        self.w._autoplay_mode = 'down'
        self.deferred = True
        self.w._advance_autoplay('a')
        self.assertEqual(self.w._autoplay_index, 0)
        self.start()
        self.assertEqual(self.w._autoplay_index, 1)
        self.assertEqual(self.selected_row(), 1)
        self.w._advance_autoplay('a')
        self.assertEqual(self.pending[0], 'b')
        self.cancel()
        self.assertEqual(self.w._autoplay_index, 1)

    def test_artists_keyboard_load_uses_visible_leaf_and_group_does_not_load_stale_table(self):
        self.load_row(2)
        self.w._show_artists()
        item = self.w._artists_browser._tracks['a']
        item.parent().setExpanded(True)
        self.w.artists_view.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
        self.w._dispatch_keyboard_action('browser.load')
        self.assertEqual(self.loads[-1][0], 'a')
        self.w.artists_view.setCurrentItem(item.parent())
        self.w._dispatch_keyboard_action('browser.load')
        self.assertEqual(len(self.loads), 2)

    def test_hidden_artists_leaf_cannot_supply_info_and_loaded_fallback_stays_explicit(self):
        self.w._show_artists()
        item = self.w._artists_browser._tracks['a']
        item.parent().setExpanded(True)
        self.w.artists_view.setCurrentItem(item)
        self.w.search.setText('Bravo')
        self.assertTrue(item.isHidden())
        self.assertIsNone(self.w._selected_track())
        with mock.patch('desktop.track_info._exec_info') as info:
            self.w._open_track_info()
            info.assert_not_called()
        self.w._load_track(self.w.lib.tracks_by_id['b'])
        self.w.artists_view.setCurrentItem(item)
        self.assertIsNone(self.w._selected_track())
        opened = []
        def info(dialog):
            opened.append(dialog.row['id'])
            dialog.deleteLater()
        with mock.patch('desktop.track_info._exec_info', side_effect=info):
            self.w._open_track_info()
        self.assertEqual(opened, ['b'])

    def clear_selection(self):
        self.w.stack.setCurrentIndex(0)
        self.w.table.clearSelection()
        self.w.table.setCurrentIndex(QModelIndex())
        self.assertIsNone(self.w._selected_track())

    def test_unselected_info_keeps_the_accepted_loaded_song_in_the_same_source(self):
        self.load_row(0)
        self.clear_selection()
        self.assertIs(self.w._loaded_info_track(), self.w.lib.tracks_by_id['a'])
        opened = []
        def info(dialog):
            opened.append(dialog.row['id']);dialog.deleteLater()
        with mock.patch('desktop.track_info._exec_info', side_effect=info):
            self.w._open_track_info()
        self.assertEqual(opened, ['a'])

    def test_unselected_info_rejects_replaced_source_song_uuid_or_player_path(self):
        self.load_row(0)
        self.clear_selection()
        library = self.w.lib
        for drift in ('source', 'uuid', 'player-path'):
            with self.subTest(drift=drift):
                self.w.lib = library
                self.w.player_bar._src_path = str(library.tracks_by_id['a'].path)
                if drift == 'source':
                    self.w.lib = replace(library, source='/synthetic/other-source.xml')
                elif drift == 'uuid':
                    self.w.lib = replace(library, tracks_by_id={**library.tracks_by_id,
                        'a':replace(library.tracks_by_id['a'], source_uuid='other-a')})
                else:
                    self.w.player_bar._src_path = '/synthetic/other-a.wav'
                self.assertIsNone(self.w._loaded_info_track())
                with mock.patch('desktop.track_info._exec_info') as info:
                    self.w._open_track_info()
                    info.assert_not_called()

    def test_unselected_info_does_not_infer_an_unbound_player_id(self):
        self.clear_selection()
        self.w.player_bar._track_id = 'a'
        self.w.player_bar._src_path = str(self.w.lib.tracks_by_id['a'].path)
        self.assertIsNone(self.w._loaded_info_track())
        with mock.patch('desktop.track_info._exec_info') as info:
            self.w._open_track_info()
            info.assert_not_called()

    def test_pending_audition_does_not_clear_the_old_context_until_accepted(self):
        self.load_row(0)
        previous = self.w._autoplay_context
        self.deferred = True
        self.w._open_track(self.w.lib.tracks_by_id['b'], autoplay=None,
                           capture_autoplay=False, audition=True)
        self.assertIs(self.w._autoplay_context, previous)
        self.cancel()
        self.assertIs(self.w._autoplay_context, previous)
        self.w._open_track(self.w.lib.tracks_by_id['b'], autoplay=None,
                           capture_autoplay=False, audition=True)
        self.start()
        self.assertEqual(self.w._autoplay_order, [])

    def test_usb_load_keeps_its_bound_route_and_other_device_pages_ignore_desktop_selection(self):
        tree = SimpleNamespace(currentItem=lambda: 'usb-song')
        usb = SimpleNamespace(playlist_tree=tree, _activate_song=mock.Mock())
        stack = SimpleNamespace(currentIndex=lambda: 1, currentWidget=lambda: 'device')
        host = SimpleNamespace(stack=stack, device_content_stack=SimpleNamespace(currentIndex=lambda: 2),
                               usb_device_pages=usb, artists_view='artists', _play_row=mock.Mock())
        CdjDeviceMixin._load_browser_selection(host)
        usb._activate_song.assert_called_once_with('usb-song')
        host.device_content_stack.currentIndex = lambda: 1
        CdjDeviceMixin._load_browser_selection(host)
        host._play_row.assert_not_called()
