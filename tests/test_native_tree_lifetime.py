"""Synchronous tree publication releases borrowed Qt and Library owners."""
from contextlib import contextmanager
import gc
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.trees import NATIVE_BINDING_ROLE, PATH_ROLE, RB_NODE_ROLE, PlaylistTree
from soundbox.library import Library, Playlist


def _library(name):
    return Library({}, [Playlist(name, ['Folder'], [], '1')], '/synthetic/master.db',
                   source_kind='rekordbox-db', source_library_id='library',
                   source_playlist_catalog=dict(nodes=[
                       dict(ID='10', UUID='folder-uuid', Name='Folder', ParentID='root',
                            Seq=1, Attribute=1, rb_local_deleted=0),
                       dict(ID='1', UUID='playlist-uuid', Name=name, ParentID='10',
                            Seq=1, Attribute=0, rb_local_deleted=0)], members=[]))


def _summary(name):
    return dict(tree=dict(children=[dict(name='Folder', type='folder', pid='10',
        children=[dict(name=name, type='playlist', pid='1', attribute=0,
                       count=0, path='Folder / ' + name)])]))


@contextmanager
def _without_cyclic_gc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


class _Host:
    _make_item = MainWindow._make_item

    def __init__(self):
        self.tree = PlaylistTree()
        self.lib = _library('First')
        self.custom = {}
        self.removals = {}
        self.playlist_additions = {}
        self.settings = {}
        self.unification_controller = SimpleNamespace(state=lambda: None,
                                                       export_aliases=lambda: {})

    def _native_presentation(self):
        return self.lib

    def _native_pending_deletions(self, **_kwargs):
        return set()

    def _refresh_custom(self):
        pass

    def _native_pending_changed(self, **_kwargs):
        pass


class NativeTreeLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = _Host()
        self.addCleanup(self.retire_tree)

    def retire_tree(self):
        self.host.tree.close()
        self.host.tree.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_publication_does_not_retain_the_borrowed_root_until_cyclic_gc(self):
        with _without_cyclic_gc():
            MainWindow._populate_tree(self.host, _summary('First'))
            root = self.host.tree.invisibleRootItem()
            root_ref = weakref.ref(root)
            del root
            self.assertIsNone(root_ref())
            folder = self.host.tree.topLevelItem(0)
            item = folder.child(0)
            self.assertEqual((folder.text(0), item.text(0)), ('Folder', '♪  First  (0)'))
            self.assertEqual(item.data(0, RB_NODE_ROLE), '1')
            self.assertEqual(item.data(0, NATIVE_BINDING_ROLE),
                             dict(source='/synthetic/master.db', library_id='library',
                                  id='1', uuid='playlist-uuid', alias=None))

    def test_replaced_library_retires_without_gc_while_native_items_are_reused(self):
        with _without_cyclic_gc():
            MainWindow._populate_tree(self.host, _summary('First'))
            folder = self.host.tree.topLevelItem(0)
            item = folder.child(0)
            item.setSelected(True)
            self.host.tree.setCurrentItem(item)
            old_library = weakref.ref(self.host.lib)
            self.host.lib = _library('Second')
            MainWindow._populate_tree(self.host, _summary('Second'))
            self.assertIsNone(old_library())
            self.assertIs(self.host.tree.topLevelItem(0), folder)
            self.assertIs(folder.child(0), item)
            self.assertIs(self.host.tree.currentItem(), item)
            self.assertTrue(item.isSelected())
            self.assertEqual(item.text(0), '♪  Second  (0)')
            self.assertEqual(item.data(0, PATH_ROLE), 'Folder / Second')
            self.assertEqual(item.data(0, Qt.UserRole + 82), '1')

    def test_early_failure_restores_signals_and_updates_before_helpers_exist(self):
        tree = self.host.tree
        for blocked in (False, True):
            for updates in (False, True):
                with self.subTest(blocked=blocked, updates=updates):
                    tree.blockSignals(blocked)
                    tree.setUpdatesEnabled(updates)
                    with (_without_cyclic_gc(),
                          mock.patch.object(self.host.unification_controller, 'state',
                                            side_effect=RuntimeError('early publication failure'))):
                        with self.assertRaisesRegex(RuntimeError, 'early publication failure'):
                            MainWindow._populate_tree(self.host, _summary('First'))
                    self.assertEqual(tree.signalsBlocked(), blocked)
                    self.assertEqual(tree.updatesEnabled(), updates)


if __name__ == '__main__':
    unittest.main()
