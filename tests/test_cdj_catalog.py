"""Pure catalog reconciliation preserves native identities and ambiguity guards."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from soundbox.cdj_catalog import current_baseline
from soundbox.cdj_export import _stable_id
from soundbox.cdj_incremental import _catalog
from soundbox.library import Library, Playlist, Track


def song(identity, source, master=42):
    return dict(id=str(identity),source_id=source,master_db_id=master,
                audio=f'/Contents/{identity}.wav',analysis='',artwork='')


def playlist(identity, title, members=(), *, parent=0, folder=False, source=None):
    row=dict(id=str(identity),parent=str(parent),title=title,folder=folder,
             entries=[(str(t),f'/Contents/{t}.wav') for t in members])
    if source is not None:
        row['source_id']=source
    return row


def baseline(playlists, tracks=()):
    return dict(db_id=42,selection=[],files={},view=dict(playlists=playlists,track_rows=list(tracks)),
                foreign_playlists=[int(p['id']) for p in playlists if 'source_id' not in p],
                next_playlist_id=100,next_track_id=100)


class CdjCatalogTests(unittest.TestCase):
    def library(self, playlists):
        tracks={str(i):Track(str(i),f'Track {i}','Fixture',f'file:///synthetic/{i}.wav') for i in (1,2)}
        return Library(tracks,playlists,'synthetic.db',source_library_id='42')

    def catalog(self, jobs, previous):
        before=deepcopy(previous)
        result=_catalog(self.library(jobs),jobs,previous,(),True)
        self.assertEqual(previous,before)
        return result

    def test_native_playlist_and_folder_adoption_keeps_ids_order_and_duplicates(self):
        job=Playlist('Set',['Folder'],keys=['2','1','2'],pid='native')
        previous=baseline([playlist(10,'Folder',folder=True),playlist(11,'Set',[1,2],parent=10)],
                          [song(1,1),song(2,2)])
        result=self.catalog([job],previous)
        self.assertEqual(result['nodes'],[
            dict(id=10,parent=0,name='Folder',folder=True,tracks=[]),
            dict(id=11,parent=10,name='Set',folder=False,tracks=[2,1,2])])
        self.assertEqual(result['next_playlist'],100)

    def test_ambiguous_names_and_other_library_members_get_distinct_new_playlists(self):
        jobs=[Playlist('Ambiguous',[],keys=['1'],pid='a'),
              Playlist('Foreign recording',[],keys=['2'],pid='b')]
        previous=baseline([playlist(10,'Ambiguous',[1]),playlist(11,'Ambiguous',[1]),
                           playlist(12,'Foreign recording',[3])],[song(1,1),song(3,2,master=77)])
        result=self.catalog(jobs,previous)
        nodes={n['id']:n for n in result['nodes']}
        self.assertEqual(nodes[10]['tracks'],[1])
        self.assertEqual(nodes[11]['tracks'],[1])
        self.assertEqual(nodes[12]['tracks'],[3])
        self.assertEqual(nodes[100]['name'],'Ambiguous')
        self.assertEqual(nodes[101]['name'],'Foreign recording')
        self.assertEqual(nodes[101]['tracks'],[100])

    def test_moved_binding_is_removed_from_old_name_candidates(self):
        renamed=Playlist('Renamed',[],keys=['1'],pid='renamed')
        replacement=Playlist('Original',[],keys=['2'],pid='new')
        previous=baseline([playlist(10,'Original',[1],source=_stable_id('playlist:renamed'))],
                          [song(1,1),song(2,2)])
        # A previously bound native row can still be listed as foreign by an
        # older inventory. Reconciliation must use its current name/parent.
        previous['foreign_playlists']=[10]
        result=self.catalog([renamed,replacement],previous)
        nodes={n['id']:n for n in result['nodes']}
        self.assertEqual(nodes[10]['name'],'Renamed')
        self.assertEqual(nodes[100]['name'],'Original')

    def reconcile(self, old, new):
        previous=baseline(old)
        native=SimpleNamespace(catalog=dict(playlists=new,track_rows=[]),files=())
        before=deepcopy((previous,native.catalog))
        result=current_baseline(native,previous,42)
        self.assertEqual((previous,native.catalog),before)
        return {p['id']:p.get('source_id') for p in result['view']['playlists']}

    def test_binding_matches_full_folder_path_and_kind_when_native_ids_change(self):
        old=[playlist(1,'A',folder=True),playlist(2,'Set',parent=1,source=101),
             playlist(3,'B',folder=True),playlist(4,'Set',parent=3,source=102)]
        new=[playlist(11,'A',folder=True),playlist(12,'Set',parent=11),
             playlist(13,'B',folder=True),playlist(14,'Set',parent=13),
             playlist(15,'Set',parent=11,folder=True)]
        self.assertEqual(self.reconcile(old,new),{'11':None,'12':101,'13':None,'14':102,'15':None})

    def test_duplicate_paths_remain_unbound_and_exact_identity_fallback_survives_rename(self):
        old=[playlist(1,'Duplicate',[1],source=101),playlist(2,'Old name',[2,1,2],source=102)]
        new=[playlist(3,'Duplicate',[1]),playlist(4,'Duplicate',[1]),
             playlist(2,'New name',[2,1,2])]
        self.assertEqual(self.reconcile(old,new),{'3':None,'4':None,'2':102})

    def test_fallback_rejects_changed_members_and_empty_unmatched_playlist(self):
        old=[playlist(1,'Before',[1,2],source=101),playlist(2,'Empty',source=102)]
        new=[playlist(1,'After',[2,1]),playlist(2,'Also renamed')]
        self.assertEqual(self.reconcile(old,new),{'1':None,'2':None})
