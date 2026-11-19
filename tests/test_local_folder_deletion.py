"""Confirmed local deletion preserves the current browser and fresh state."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTreeWidget, QTreeWidgetItem

from desktop.model import TrackModel
from desktop.playlist_folders import PlaylistFoldersMixin
from desktop.trees import COLLECTION_ROLE, TrackTable
from soundbox.library import Library, Track
from soundbox.state_repository import StateRepository


FOLDER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class _Window(QMainWindow, PlaylistFoldersMixin):
    def __init__(self):
        super().__init__()
        self.lib = Library({key: Track(key, 'Song '+key, 'Artist', 'file:///synthetic/'+key+'.wav')
                            for key in ('A', 'B', 'C')}, [], '/synthetic/library.xml')
        self.custom = {'Set': ['A'], 'Other': ['B']}
        self.settings = dict(custom_playlist_tree=[dict(folder=FOLDER, name='Empty', children=[]),
            {'playlist': 'Set'}, {'playlist': 'Other'}])
        self.unification_controller = SimpleNamespace(state=lambda: None)
        self._view_key = 'sb:Collection'
        self._shutting_down = self._state_maintenance = False
        self.table = TrackTable(self)
        self.model = TrackModel()
        self.model.setParent(self)
        self.table.setModel(self.model)
        self.setCentralWidget(self.table)
        self.sb_tree = QTreeWidget(self)
        self.sb_tree.hide()
        self.saved = []
        self._refresh_custom = mock.Mock()
        self._flash_status = mock.Mock()
        self._stash_view = mock.Mock()
        self._select_only = mock.Mock()
        self._show_collection()
        collection = QTreeWidgetItem(['Collection'])
        collection.setData(0, COLLECTION_ROLE, True)
        self.sb_tree.addTopLevelItem(collection)

    def _save_state(self):
        self.saved.append((deepcopy(self.custom), deepcopy(self.settings)))
        return True

    def _show_collection(self):
        self.model.set_rows([track.to_row() for track in self.lib.tracks_by_id.values()])

    def _restore_view(self, key):
        self._view_key = key


class LocalFolderDeletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = _Window()
        self.addCleanup(self.retire)

    def retire(self):
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def delete(self, *, during=None, kind='folder', key=FOLDER):
        def confirm(*_args):
            if during:
                during()
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox, 'question', side_effect=confirm):
            self.window._delete_local_node(kind, key)

    def test_empty_folder_deletion_keeps_collection_rows_selection_and_model(self):
        w = self.window
        w.table.selectionModel().select(w.model.index(1, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows)
        rows = w.model._rows
        reset = mock.Mock()
        w.model.modelReset.connect(reset)
        self.delete()
        self.assertIs(w.model._rows, rows)
        self.assertEqual([row['id'] for row in w.model._rows], ['A', 'B', 'C'])
        self.assertEqual([index.row() for index in w.table.selectionModel().selectedRows()], [1])
        self.assertEqual(w.saved[-1][1]['custom_playlist_tree'],
                         [{'playlist': 'Set'}, {'playlist': 'Other'}])
        reset.assert_not_called()

    def test_deleting_the_displayed_playlist_returns_to_complete_collection(self):
        w = self.window
        w._view_key = 'sb:Set'
        w.model.set_rows([w.lib.tracks_by_id['A'].to_row()])
        self.delete(kind='playlist', key='Set')
        self.assertEqual(w._view_key, 'sb:Collection')
        self.assertEqual([row['id'] for row in w.model._rows], ['A', 'B', 'C'])
        self.assertEqual(w.custom, {'Other': ['B']})

    def test_confirmed_deletion_survives_reopening_the_real_state_repository(self):
        w = self.window
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repository = StateRepository(root / 'library.json')
        repository.load()
        def save():
            repository.save(dict(custom=w.custom, settings=w.settings))
            return True
        with mock.patch.object(w, '_save_state', side_effect=save):
            self.delete()
        reloaded = StateRepository(repository.path).load()
        self.assertEqual(reloaded['custom'], w.custom)
        self.assertEqual(reloaded['settings']['custom_playlist_tree'],
                         [{'playlist': 'Set'}, {'playlist': 'Other'}])

    def test_initial_browser_without_a_view_key_and_cancel_remain_intact(self):
        w = self.window
        del w._view_key
        rows = w.model._rows
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            w._delete_local_node('folder', FOLDER)
        self.assertFalse(w.saved)
        self.delete()
        self.assertIs(w.model._rows, rows)

    def test_unrelated_playlist_and_newer_view_are_preserved(self):
        w = self.window
        def newer_selection():
            w._view_key = 'sb:Other'
            w.model.set_rows([w.lib.tracks_by_id['B'].to_row()])
        self.delete(kind='playlist', key='Set', during=newer_selection)
        self.assertEqual(w._view_key, 'sb:Other')
        self.assertEqual([row['id'] for row in w.model._rows], ['B'])

    def test_confirmation_merges_into_fresh_unrelated_organization(self):
        w = self.window
        def newer_tree():
            w.settings['custom_playlist_tree'].append(dict(folder=OTHER, name='New', children=[]))
            w.custom['New set'] = ['C']
        self.delete(during=newer_tree)
        self.assertIn(dict(folder=OTHER, name='New', children=[]),
                      w.settings['custom_playlist_tree'])
        self.assertEqual(w.custom['New set'], ['C'])

    def test_changed_source_subtree_or_reviewed_membership_refuses_deletion(self):
        w = self.window
        for change in ('source', 'folder', 'members', 'unified', 'busy'):
            with self.subTest(change=change):
                w.lib = replace(w.lib, source='/synthetic/library.xml')
                w.settings['custom_playlist_tree'] = [dict(folder=FOLDER, name='Sets',
                    children=[{'playlist': 'Set'}]), {'playlist': 'Other'}]
                w.custom = {'Set': ['A'], 'Other': ['B']}
                w.unification_controller.state = lambda: None
                w._state_maintenance = False
                def newer_scope():
                    if change == 'source':
                        w.lib = replace(w.lib, source='/synthetic/other.xml')
                    elif change == 'folder':
                        w.settings['custom_playlist_tree'][0]['name'] = 'Renamed'
                    elif change == 'members':
                        w.custom['Set'].append('C')
                    elif change == 'unified':
                        w.unification_controller.state = lambda: {'customs_done': True}
                    else:
                        w._state_maintenance = True
                count = len(w.saved)
                self.delete(during=newer_scope)
                self.assertEqual(len(w.saved), count)
                self.assertIn('Set', w.custom)

    def test_failed_save_keeps_state_and_browser(self):
        w = self.window
        tree = deepcopy(w.settings['custom_playlist_tree'])
        rows = w.model._rows
        with mock.patch.object(w, '_save_state', return_value=False):
            self.delete()
        self.assertEqual(w.settings['custom_playlist_tree'], tree)
        self.assertIs(w.model._rows, rows)
        w._refresh_custom.assert_not_called()
