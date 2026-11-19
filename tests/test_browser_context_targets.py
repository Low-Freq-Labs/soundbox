"""Browser menus retain the clicked song through nested Qt publication."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QLineEdit,
                               QMainWindow, QStackedWidget)

from desktop.app import MainWindow
from desktop.artists_browser import ArtistsBrowser, artist_rows
from desktop.model import COLUMNS, TrackModel
from desktop.track_menu import TrackMenuMixin
from desktop.trees import ArtistsTree, CellEditDelegate, TrackTable
from soundbox.library import Library, Track
from soundbox.providers import Providers


class _Window(QMainWindow, TrackMenuMixin):
    _table_context = MainWindow._table_context
    _artists_menu = MainWindow._artists_menu
    _table_edit_at = MainWindow._table_edit_at
    _add_track_file_actions = MainWindow._add_track_file_actions
    _add_track_reanalysis_action = MainWindow._add_track_reanalysis_action
    _add_track_removal_actions = MainWindow._add_track_removal_actions
    _native_row_identity = staticmethod(MainWindow._native_row_identity)

    def __init__(self):
        super().__init__()
        self.lib = Library({identity: Track(identity, 'Song '+identity, 'Artist '+identity,
            'file:///synthetic/'+identity+'.wav', source_uuid='uuid-'+identity)
            for identity in ('A', 'B', 'C')}, [], '/synthetic/library.xml',
            source_kind='rekordbox-xml', source_library_id='library')
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)
        self.body_stack = SimpleNamespace(currentIndex=lambda: 0)
        self.table = TrackTable()
        self.model = TrackModel()
        self.model.setParent(self)
        self.table.setModel(self.model)
        self.table.setItemDelegate(CellEditDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.artists_view = ArtistsTree()
        self.artists_view.setColumnCount(len(COLUMNS))
        self.artists_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.artists_view.setItemDelegate(CellEditDelegate(self.artists_view))
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.artists_view)
        self._artists_browser = ArtistsBrowser(self.artists_view)
        self._view_key = 'sb:Collection'
        self._shutting_down = self._state_maintenance = False
        self.custom = {'Set': ['C']}
        self.imported = {}
        self.providers = Providers('public')
        self.art_cache = SimpleNamespace(canonical_image_path=lambda _: None)
        for name in ('_open_in_finder', '_analyze_tracks', '_on_tracks_dropped', '_delete_tracks',
                     '_remove_from_custom', '_art_pick', '_flash_status'):
            setattr(self, name, mock.Mock())
        self.fill()

    def fill(self, identities=('A', 'B', 'C')):
        rows = [self.lib.tracks_by_id[identity].to_row() for identity in identities]
        self.model.set_rows(rows)
        self._artists_browser.replace(artist_rows(rows))
        self.artists_view.expandAll()


class BrowserContextTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = _Window()
        self.window.resize(920, 500)
        self.window.show()
        self.settle()
        self.addCleanup(self.retire)

    def settle(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def retire(self):
        self.window.close()
        self.window.deleteLater()
        self.settle()

    def table_menu(self, clicked='A', *, column=0, during=None, action='Edit', occurrence=0):
        w = self.window
        rows = [at for at, row in enumerate(w.model._rows) if row['id'] == clicked]
        position = w.table.visualRect(w.model.index(rows[occurrence], column)).center()
        def execute(menu, _position):
            if during:
                during()
            next(item for item in menu.actions() if item.text() == action).trigger()
        with mock.patch('desktop.app.exec_transient_menu', side_effect=execute):
            w._table_context(position)
        self.settle()

    def artist_menu(self, item, *, during=None, action='Open in Finder', submenu=False):
        w = self.window
        position = w.artists_view.visualItemRect(item).center()
        # The first column is the editable title, independent of its width.
        position.setX(20)
        def execute(menu, _position):
            if during:
                during()
            chosen = next(entry for entry in menu.actions() if entry.text() == action)
            (chosen.menu().actions()[0] if submenu else chosen).trigger()
        with mock.patch('desktop.app.exec_transient_menu', side_effect=execute):
            w._artists_menu(position)
        self.settle()

    def type_and_commit(self, view, value):
        editors = view.findChildren(QLineEdit)
        self.assertEqual(len(editors), 1)
        editor = editors[0]
        editor.selectAll()
        QTest.keyClicks(editor, value)
        self.assertTrue(editor.isModified())
        QTest.keyClick(editor, Qt.Key_Return)
        self.settle()

    def test_edit_after_nested_reset_types_into_the_original_song(self):
        w = self.window
        edits = mock.Mock()
        w.model.cell_edited.connect(edits)
        self.table_menu(during=lambda: w.model.set_rows([
            w.lib.tracks_by_id[identity].to_row() for identity in ('B', 'A', 'C')]))
        self.type_and_commit(w.table, 'Typed A edit')
        edits.assert_called_once_with('title', 'A', 'Typed A edit')

    def test_edit_refuses_a_replaced_source_uuid_path_or_native_id(self):
        w = self.window
        original = w.lib
        for changed in ('source', 'source_kind', 'source_library_id', 'source_uuid',
                        'location_uri', 'source_native_id', 'source_native_path'):
            with self.subTest(changed=changed):
                w.lib = replace(original, tracks_by_id=dict(original.tracks_by_id))
                w.fill()
                def replace_target():
                    if changed in ('source', 'source_kind', 'source_library_id'):
                        w.lib = replace(w.lib, **{changed: 'replacement-source'})
                    else:
                        value = 'file:///replacement.wav' if changed == 'location_uri' else 'replacement'
                        w.lib.tracks_by_id['A'] = replace(w.lib.tracks_by_id['A'], **{changed: value})
                self.table_menu(during=replace_target)
                self.assertFalse(w.table.findChildren(QLineEdit))

    def test_hidden_target_and_a_newer_view_are_not_replaced_by_edit(self):
        w = self.window
        self.table_menu(during=lambda: w.model.set_rows([w.lib.tracks_by_id['B'].to_row()]))
        self.assertFalse(w.table.findChildren(QLineEdit))
        w.fill()
        self.table_menu(during=lambda: setattr(w, '_view_key', 'sb:Set'))
        self.assertFalse(w.table.findChildren(QLineEdit))

    def test_native_duplicate_edit_resolves_the_exact_membership_after_reset(self):
        w = self.window
        def row(member):
            return {**w.lib.tracks_by_id['A'].to_row(), '_native_membership': dict(
                source='/synthetic/master.db', library_id='library', playlist_id='42',
                playlist_uuid='playlist-uuid', ID=member, UUID='member-'+member,
                ContentID='A', track_uuid='uuid-A')}
        first, second = row('1'), row('2')
        w.model.set_rows([first, second, w.lib.tracks_by_id['B'].to_row()])
        self.table_menu(occurrence=1, during=lambda: w.model.set_rows([second, first]))
        self.assertEqual(w.model._rows[w.table.currentIndex().row()]['_native_membership']['ID'], '2')
        self.type_and_commit(w.table, 'Second occurrence')

    def test_local_duplicate_edit_keeps_its_occurrence_through_view_sort(self):
        w = self.window
        w.fill(('A', 'B', 'A'))
        self.table_menu(occurrence=1, during=lambda: w.model.sort(0, Qt.DescendingOrder))
        self.assertIs(w.model._rows[w.table.currentIndex().row()], w.model._all_rows[2])
        self.type_and_commit(w.table, 'Later A')

    def test_cached_local_duplicate_rows_keep_the_later_shared_occurrence(self):
        w = self.window
        row = w.lib.tracks_by_id['A'].to_row()
        w.model.set_rows([row, w.lib.tracks_by_id['B'].to_row(), row])
        self.table_menu(occurrence=1, during=lambda: w.model.sort(0, Qt.DescendingOrder))
        self.assertEqual(w.table.currentIndex().row(), 2)
        QTest.keyClick(w.table.findChildren(QLineEdit)[0], Qt.Key_Escape)
        self.settle()

    def test_changed_local_duplicate_inventory_refuses_ambiguous_edit(self):
        w = self.window
        w.fill(('A', 'B', 'A'))
        self.table_menu(occurrence=1, during=lambda: w.fill(('A', 'A', 'B')))
        self.assertFalse(w.table.findChildren(QLineEdit))

    def test_same_identity_metadata_refresh_allows_genuine_edit(self):
        w = self.window
        def acknowledge():
            w.lib = replace(w.lib, tracks_by_id={**w.lib.tracks_by_id,
                'A': replace(w.lib.tracks_by_id['A'], name='Acknowledged A')})
            w.fill(('C', 'B', 'A'))
        self.table_menu(during=acknowledge)
        self.assertEqual(w.table.findChildren(QLineEdit)[0].text(), 'Acknowledged A')
        self.type_and_commit(w.table, 'New A')

    def test_right_click_outside_artists_selection_uses_clicked_song(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        w.artists_view.setCurrentItem(w._artists_browser._tracks['A'])
        self.artist_menu(w._artists_browser._tracks['B'])
        w._open_in_finder.assert_called_once_with(('B',))
        self.artist_menu(w._artists_browser._tracks['B'], action='Add to Playlist', submenu=True)
        w._on_tracks_dropped.assert_called_once_with('Set', ['B'])

    def test_right_click_on_selected_artist_song_preserves_the_multiselection(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        for identity in ('A', 'C'):
            w._artists_browser._tracks[identity].setSelected(True)
        self.artist_menu(w._artists_browser._tracks['C'])
        self.assertEqual(set(w._open_in_finder.call_args.args[0]), {'A', 'C'})
        self.assertEqual(len(w.artists_view.selectedItems()), 2)

    def test_artists_edit_after_regroup_targets_the_fresh_clicked_item(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        def regroup():
            rows = [track.to_row() for track in w.lib.tracks_by_id.values()]
            rows[1]['artist'] = 'New group'
            w._artists_browser.replace(artist_rows(rows))
            w.artists_view.expandAll()
        self.artist_menu(w._artists_browser._tracks['B'], action='Edit', during=regroup)
        self.type_and_commit(w.artists_view, 'Typed B edit')
        self.assertEqual(w._artists_browser._tracks['B'].text(0), 'Typed B edit')
        self.assertEqual(w._artists_browser._tracks['A'].text(0), 'Song A')

    def test_artist_header_uses_its_full_group_outside_an_unrelated_selection(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        rows = [track.to_row() for track in w.lib.tracks_by_id.values()]
        rows[2]['artist'] = rows[1]['artist']
        w._artists_browser.replace(artist_rows(rows))
        w.artists_view.expandAll()
        w.artists_view.setCurrentItem(w._artists_browser._tracks['A'])
        self.artist_menu(w._artists_browser._headers['Artist B'],
                         action='Add 2 to Playlist', submenu=True)
        w._on_tracks_dropped.assert_called_once_with('Set', ['B', 'C'])

    def test_artists_replaced_or_hidden_clicked_song_never_opens_an_editor(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        self.artist_menu(w._artists_browser._tracks['B'], action='Edit', during=lambda:
            w._artists_browser._tracks['B'].setHidden(True))
        self.assertFalse(w.artists_view.findChildren(QLineEdit))
        w._artists_browser._tracks['B'].setHidden(False)
        self.artist_menu(w._artists_browser._tracks['B'], action='Edit', during=lambda:
            w.lib.tracks_by_id.update(B=replace(w.lib.tracks_by_id['B'], source_uuid='replacement')))
        self.assertFalse(w.artists_view.findChildren(QLineEdit))

    def test_playlist_actions_refuse_a_changed_destination_or_membership(self):
        w = self.window
        w.stack.setCurrentWidget(w.artists_view)
        w._view_key = 'sb:Artists'
        self.artist_menu(w._artists_browser._tracks['B'], action='Add to Playlist', submenu=True,
                         during=lambda: w.custom.pop('Set'))
        w._on_tracks_dropped.assert_not_called()
        w.custom['Set'] = ['A', 'B']
        w.stack.setCurrentWidget(w.table)
        w._view_key = 'sb:Set'
        self.table_menu(action='Remove from Playlist', during=lambda: w.custom['Set'].append('C'))
        w._remove_from_custom.assert_not_called()

    def test_admitted_action_returns_its_existing_callback_result(self):
        from desktop.track_menu import capture_tracks
        w = self.window
        callback = mock.Mock(return_value=False)
        result = w._track_menu_action(capture_tracks(w, ('A',)), callback)()
        self.assertIs(result, False)
        callback.assert_called_once_with()

    def test_captured_multi_actions_refuse_a_replaced_selected_song(self):
        w = self.window
        selection = w.table.selectionModel()
        for at in (0, 1):
            selection.select(w.model.index(at, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        for action, observer in (('Open in Finder', w._open_in_finder),
                                 ('Analyze 2 Tracks', w._analyze_tracks),
                                 ('Remove 2 tracks from Collection', w._delete_tracks)):
            with self.subTest(action=action):
                w.lib.tracks_by_id['B'] = replace(w.lib.tracks_by_id['B'], source_uuid='uuid-B')
                self.table_menu(action=action, during=lambda: w.lib.tracks_by_id.update(
                    B=replace(w.lib.tracks_by_id['B'], source_uuid='replaced-B')))
                observer.assert_not_called()

    def test_clicked_artwork_is_independent_of_other_selected_rows(self):
        w = self.window
        selection = w.table.selectionModel()
        for at in (0, 1):
            selection.select(w.model.index(at, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table_menu(action='Choose cover art…', during=lambda: w.lib.tracks_by_id.update(
            B=replace(w.lib.tracks_by_id['B'], source_uuid='replaced-B')))
        w._art_pick.assert_called_once_with('A')
