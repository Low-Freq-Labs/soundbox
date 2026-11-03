"""Sidebar icon choices are local playlist preferences, independent of DB saves."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMainWindow,QTreeWidgetItem

from desktop.playlist_folders import PlaylistFoldersMixin
from desktop.rekordbox_playlists import NativePlaylistsMixin
from desktop.trees import (UnifiedPlaylistTree,ARTISTS_ROLE,COLLECTION_ROLE,CUSTOM_ROLE,
    CUSTOM_FOLDER_ROLE,NATIVE_TREE_ROLE,PATH_ROLE,RB_NODE_KIND_ROLE,RB_NODE_ROLE,
    sidebar_choice_icon,sidebar_row_geometry)
from soundbox.library import Library


class _Window(NativePlaylistsMixin,PlaylistFoldersMixin,QMainWindow):
    def __init__(self,path):
        super().__init__();self.settings={};self.custom={'Local':['song']};self.save_ok=True
        self.state_file=path;self._flash_status=mock.Mock();self._refresh_custom=mock.Mock()
        self.lib=Library({},[],Path('/private/icon-fixture/master.db'),source_kind='rekordbox-db',
            source_library_id='library-one',source_playlist_catalog={'nodes':[
                dict(ID='11',UUID='playlist-one',Attribute=0,rb_local_deleted=0),
                dict(ID='12',UUID='playlist-two',Attribute=4,rb_local_deleted=0),
                dict(ID='13',UUID='folder-one',Attribute=1,rb_local_deleted=0)]})
        self.tree=self.sb_tree=UnifiedPlaylistTree();self.setCentralWidget(self.tree)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu);self.resize(380,320)
        def item(label,roles,parent=None):
            result=QTreeWidgetItem([label])
            for role,value in roles.items():result.setData(0,role,value)
            if parent:parent.addChild(result)
            else:self.tree.addTopLevelItem(result)
            return result
        self.collection=item('Collection',{COLLECTION_ROLE:True})
        self.artists=item('♪  Artists',{ARTISTS_ROLE:True})
        self.folder=item('Folder',{NATIVE_TREE_ROLE:True,RB_NODE_ROLE:'13',RB_NODE_KIND_ROLE:1})
        self.playlist=item('♪  First',{NATIVE_TREE_ROLE:True,RB_NODE_ROLE:'11',RB_NODE_KIND_ROLE:0,PATH_ROLE:'First'})
        self.smart=item('♪  Smart',{NATIVE_TREE_ROLE:True,RB_NODE_ROLE:'12',RB_NODE_KIND_ROLE:4,PATH_ROLE:'Smart'},self.folder)
        self.local=item('★  Local',{CUSTOM_ROLE:'Local'})
        self.local_folder=item('Local folder',{CUSTOM_FOLDER_ROLE:'fixed-folder'})
        self.folder.setExpanded(True);self.show()

    def _save_state(self):
        if not self.save_ok:return False
        self.state_file.write_text(json.dumps(self.settings));return True

    def index(self,item):return self.tree.indexFromItem(item)

    def icon_point(self,item):
        box,_=sidebar_row_geometry(self.tree.visualItemRect(item),self.tree.indentation())
        return box.center().toPoint()


class PlaylistIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.window=_Window(Path(self.temp.name)/'settings.json');self.app.processEvents()
        self.addCleanup(self.close)

    def close(self):
        self.window.close();self.window.deleteLater();self.app.processEvents()

    def test_only_playlists_have_icon_choices_and_artists_is_fixed_person(self):
        w=self.window;t=w.tree
        self.assertEqual(t.sidebar_icon_kind(w.index(w.collection)),'home')
        self.assertEqual(t.sidebar_icon_kind(w.index(w.artists)),'person')
        self.assertEqual(t.sidebar_icon_kind(w.index(w.playlist)),'note')
        self.assertEqual(t.sidebar_icon_kind(w.index(w.local)),'star')
        for fixed in (w.collection,w.artists,w.folder,w.local_folder):
            with self.subTest(label=fixed.text(0)):
                self.assertIsNone(t.playlist_icon_item_at(w.icon_point(fixed)))
                self.assertIsNone(w._playlist_icon_menu(t,fixed))
        for playlist in (w.playlist,w.smart,w.local):
            self.assertIs(t.playlist_icon_item_at(w.icon_point(playlist)),playlist)
            self.assertIsNone(t.playlist_icon_item_at(t.visualItemRect(playlist).center()))

    def test_menu_persists_each_choice_without_navigation_or_source_requests(self):
        w=self.window;t=w.tree;t.setCurrentItem(w.local)
        before=deepcopy(w.lib.source_playlist_catalog);changed=[]
        t.currentItemChanged.connect(lambda *args:changed.append(args))
        for label,kind in (('Star','star'),('Music note','note'),('Vinyl crate','crate')):
            menu=w._playlist_icon_menu(t,w.playlist)
            self.assertEqual([a.text() for a in menu.actions()],['Star','Music note','Vinyl crate'])
            next(a for a in menu.actions() if a.text()==label).trigger()
            self.assertEqual(t.sidebar_icon_kind(w.index(w.playlist)),kind)
            saved=json.loads(w.state_file.read_text())
            self.assertEqual(saved['playlist_icons'][w._playlist_icon_key(w.index(w.playlist))],kind)
        self.assertIs(t.currentItem(),w.local);self.assertEqual(changed,[])
        self.assertEqual(w.lib.source_playlist_catalog,before)
        self.assertNotIn('rekordbox_playlist_edits',w.settings)
        w.settings=json.loads(w.state_file.read_text())
        self.assertEqual(t.sidebar_icon_kind(w.index(w.playlist)),'crate')

    def test_right_button_on_icon_preserves_selection_and_text_keeps_regular_menu(self):
        w=self.window;t=w.tree;t.setCurrentItem(w.local)
        QTest.mouseClick(t.viewport(),Qt.RightButton,pos=w.icon_point(w.playlist))
        self.assertIs(t.currentItem(),w.local)
        with mock.patch('desktop.rekordbox_playlists.Menu.exec') as popup, \
                mock.patch.object(w,'_native_menu',return_value=SimpleNamespace(exec=mock.Mock())) as regular:
            w._native_tree_menu(w.icon_point(w.playlist))
            popup.assert_called_once();regular.assert_not_called()
            w._native_tree_menu(t.visualItemRect(w.playlist).center())
            regular.assert_called_once_with(w.playlist)

    def test_uuid_and_library_scope_prevent_reusing_another_playlist_preference(self):
        w=self.window;index=w.index(w.playlist)
        menu=w._playlist_icon_menu(w.tree,w.playlist);menu.actions()[2].trigger()
        key=w._playlist_icon_key(index);self.assertEqual(w.tree.sidebar_icon_kind(index),'crate')
        w.lib.source_playlist_catalog={'nodes':[{**r,'UUID':'new-uuid'} if r['ID']=='11' else r
            for r in w.lib.source_playlist_catalog['nodes']]}
        self.assertNotEqual(w._playlist_icon_key(index),key)
        self.assertEqual(w.tree.sidebar_icon_kind(index),'note')
        menu.actions()[0].trigger()
        self.assertEqual(w.settings['playlist_icons'],{key:'crate'})
        w.lib.source_library_id='another-library'
        self.assertEqual(w.tree.sidebar_icon_kind(index),'note')
        w.lib.source=Path('/private/another-library/master.db')
        self.assertEqual(w.tree.sidebar_icon_kind(index),'note')

    def test_unavailable_catalog_defers_icon_changes_and_keeps_default(self):
        w=self.window;index=w.index(w.playlist)
        menu=w._playlist_icon_menu(w.tree,w.playlist)
        w.lib.source_playlist_catalog=None
        self.assertEqual(w.tree.sidebar_icon_kind(index),'note')
        self.assertIsNone(w._playlist_icon_menu(w.tree,w.playlist))
        menu.actions()[2].trigger()
        self.assertNotIn('playlist_icons',w.settings)
        self.assertFalse(w.state_file.exists())

    def test_failed_local_persistence_does_not_change_the_visible_icon(self):
        w=self.window;w.save_ok=False
        menu=w._playlist_icon_menu(w.tree,w.playlist);menu.actions()[2].trigger()
        self.assertNotIn('playlist_icons',w.settings)
        self.assertEqual(w.tree.sidebar_icon_kind(w.index(w.playlist)),'note')
        self.assertFalse(w.state_file.exists())

    def test_local_rename_and_delete_move_or_remove_only_their_own_preferences(self):
        w=self.window;key=w._local_playlist_icon_key('Local')
        w._playlist_icon_menu(w.tree,w.local).actions()[2].trigger()
        other='unrelated-library-key';w.settings['playlist_icons'][other]='star'
        nodes=[dict(playlist='Renamed')]
        self.assertTrue(w._commit_local_tree(nodes,{'Renamed':['song']},icon_rename=('Local','Renamed')))
        renamed=w._local_playlist_icon_key('Renamed')
        self.assertEqual(w.settings['playlist_icons'],{renamed:'crate',other:'star'})
        self.assertNotIn(key,w.settings['playlist_icons'])
        self.assertTrue(w._commit_local_tree([],{}))
        self.assertEqual(w.settings['playlist_icons'],{other:'star'})

    def test_person_and_crate_vectors_have_visible_distinct_pixels(self):
        images={kind:sidebar_choice_icon(kind).pixmap(20,20).toImage() for kind in ('person','crate','note','star')}
        encoded=[]
        for image in images.values():
            alpha=bytes(image.constBits());self.assertGreater(sum(alpha),0);encoded.append(alpha)
        self.assertEqual(len(set(encoded)),4)
