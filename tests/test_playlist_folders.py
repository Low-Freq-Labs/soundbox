"""Hierarchy persistence, invalid-state refusal and stable export identities."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from soundbox import playlist_folders as folders
from soundbox.library import Playlist
from soundbox.manifest import playlist_key
from soundbox.state_repository import StateRepository, InvalidState

FIRST = '81cf1d6c-45ac-4773-adfa-fd582b246137'
SECOND = 'ab4824a2-95c0-4be1-942e-14d101a3dc89'


class PlaylistFolders(unittest.TestCase):
    def tree(self):
        return [dict(folder=FIRST,name='Sets',children=[{'playlist':'Friday'},
            dict(folder=SECOND,name='Openers',children=[])]),{'playlist':'Favorites'}]

    def test_old_state_reconciles_without_changing_memberships_and_empty_folders_persist(self):
        custom = {'Friday':['2','1','2'],'Favorites':['3']}
        self.assertEqual(folders.reconcile(custom),[{'playlist':'Friday'},{'playlist':'Favorites'}])
        with tempfile.TemporaryDirectory() as area:
            repo=StateRepository(Path(area)/'library.json');repo.load()
            repo.save({'custom':custom,'settings':{'custom_playlist_tree':self.tree()}})
            restored=StateRepository(repo.path).load()
        self.assertEqual(restored['custom'],custom)
        self.assertEqual(restored['settings']['custom_playlist_tree'],self.tree())
        self.assertEqual(folders.reconcile({**custom,'New':[]},self.tree())[-1],{'playlist':'New'})

    def test_folder_moves_keep_song_order_and_existing_device_keys(self):
        before=self.tree();saved=deepcopy(before)
        after=folders.move(before,'playlist','Favorites',SECOND,0)
        paths=folders.paths(after)
        self.assertEqual(paths['Favorites'],['Sets','Openers'])
        original=Playlist('Favorites',[],keys=['3','1'])
        moved=Playlist('Favorites',paths['Favorites'],keys=list(original.keys))
        self.assertEqual(playlist_key(moved),playlist_key(original))
        self.assertEqual(moved.keys,['3','1'])
        self.assertEqual(before,saved)
        root=folders.move(after,'folder',SECOND,'',2)
        self.assertEqual(folders.paths(root)['Favorites'],['Openers'])

    def test_descendant_cycles_collisions_and_duplicate_membership_are_rejected(self):
        tree=self.tree();saved=deepcopy(tree)
        with self.assertRaises(ValueError):folders.move(tree,'folder',FIRST,SECOND,0)
        self.assertEqual(tree,saved)
        invalid=tree+[{'playlist':'Friday'}]
        with self.assertRaises(ValueError):folders.validate(invalid)
        invalid=tree+[dict(folder=SECOND,name='sets',children=[])]
        with self.assertRaises(ValueError):folders.validate(invalid)
        with self.assertRaises(KeyError):folders.move(tree,'playlist','Friday','missing',0)

    def test_invalid_hierarchy_save_preserves_the_complete_previous_library(self):
        with tempfile.TemporaryDirectory() as area:
            repo=StateRepository(Path(area)/'library.json');repo.load()
            repo.save({'custom':{'Friday':['1']},'title_overrides':{'1':'Curated'},'settings':{}})
            original=repo.path.read_bytes()
            with self.assertRaises(InvalidState):
                repo.save({'settings':{'custom_playlist_tree':[dict(folder='bad',name='Sets',children=[])]}})
            self.assertEqual(repo.path.read_bytes(),original)
