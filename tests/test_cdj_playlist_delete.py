"""Real native CDJ Delete and explicit iPod USB coexistence on disposable targets."""
from copy import deepcopy
import json
import unittest
from unittest import mock

from soundbox import cdj_export,cdj_transaction as tx
from soundbox.cdj_usb import read_library
from soundbox.library import Playlist
from tests import test_cdj_integration as fixtures
from tests import test_cdj_transaction as faults


class CdjPlaylistDelete(unittest.TestCase):
    role='generic_usb'

    @classmethod
    def setUpClass(cls):fixtures.CdjIntegrationTests.setUpClass()

    def setUp(self):
        self.case=fixtures.CdjIntegrationTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups);self.target=self.case.target
        self.stock={}
        if self.role=='ipod_usb':
            for name in ('iPod_Control/iTunes/iTunesDB','iPod_Control/Artwork/ArtworkDB',
                         'iPod_Control/Artwork/F1060_1.ithmb','iPod_Control/Music/F00/SONG.wav',
                         '.rockbox/config.cfg','.soundbox/manifest.json','Music/other.wav'):
                path=self.target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
                self.stock[name]=(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)

    def assert_stock(self):
        self.assertEqual({name:((self.target/name).read_bytes(),(self.target/name).stat().st_ino,
                               (self.target/name).stat().st_mtime_ns) for name in self.stock},self.stock)

    def native(self):return read_library(self.target,self.case.token)

    def export(self,playlists,*,standalone=()):
        def capture(_library,tracks,**_kwargs):
            request=deepcopy(self.case.request);ids=[int(t.track_id) for t in tracks]
            rows={int(t['id']):t for t in request['tracks']}
            extras={int(t['source_id']):t for t in request['track_extras']}
            request['tracks']=[rows[tid] for tid in ids]
            request['track_extras']=[extras[tid] for tid in ids]
            return request
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=capture):
            plan=cdj_export.plan_export(self.case.lib,playlists,self.target,token=self.case.token,
                volume_uuid=self.case.volume,recovery_root=self.case.recovery,additive=True,
                standalone=standalone,target_role=self.role)
        self.case.plans.append(plan);tx.commit(plan);return plan

    def delete(self,native,ids,**options):
        plan=cdj_export.plan_playlist_delete(self.target,ids,token=self.case.token,
            volume_uuid=self.case.volume,target_role=self.role,recovery_root=self.case.recovery,
            expected_catalog_files=dict(native.files),**options)
        self.case.plans.append(plan);return plan

    def test_selected_delete_retains_shared_songs_and_unrelated_collection_then_retires_only_owned_orphans(self):
        self.case.extra_track('18');collection=self.case.extra_track('19')
        keep=Playlist('Keep',[],['17','18'],pid='keep');self.case.lib.playlists.append(keep)
        self.export([self.case.playlist,keep],standalone=(collection,))
        original=self.native();by_title={p.title:p for p in original.playlists if not p.folder}
        shared=next(t for t in original.songs if t.source_id=='17')
        shared_path=self.target/shared.path.lstrip('/');shared_before=(shared_path.read_bytes(),shared_path.stat().st_ino)
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=AssertionError('Delete opened source metadata')):
            first=self.delete(original,[by_title[self.case.playlist.name].id])
        self.assertFalse(first.overwritten_playlists);self.assertEqual(first.target_role,self.role)
        tx.commit(first);after=self.native()
        self.assertNotIn(self.case.playlist.name,[p.title for p in after.playlists])
        self.assertEqual(next(p for p in after.playlists if p.title=='Keep'),by_title['Keep'])
        self.assertEqual((shared_path.read_bytes(),shared_path.stat().st_ino),shared_before)
        self.assertEqual({t.source_id for t in after.songs},{'17','18','19'})
        second=self.delete(after,[by_title['Keep'].id]);self.assertTrue(any(c.retire for c in second.changes))
        tx.commit(second);final=self.native()
        self.assertEqual({t.source_id for t in final.songs},{'19'})
        self.assertFalse(shared_path.exists());self.assert_stock()

    def test_unowned_native_assets_survive_selected_catalog_deletion(self):
        self.export([self.case.playlist]);native=self.native()
        manifest_path=self.target/tx.MANIFEST;document=json.loads(manifest_path.read_bytes())
        unowned=[]
        for name,row in document['files'].items():
            if name.startswith(('Contents/','PIONEER/USBANLZ/','PIONEER/Artwork/')):
                row['owned']=False;unowned.append(name)
        manifest_path.write_bytes(tx._json(document))
        before={name:(self.target/name).read_bytes() for name in unowned}
        playlist=next(p for p in native.playlists if not p.folder)
        plan=self.delete(native,[playlist.id]);self.assertFalse(any(c.retire for c in plan.changes))
        tx.commit(plan)
        self.assertFalse(self.native().songs)
        self.assertEqual({name:(self.target/name).read_bytes() for name in unowned},before)
        self.assert_stock()

    def test_explicit_empty_folder_deletes_only_that_folder_and_never_its_ancestor(self):
        seed=Playlist('Seed',['SETS','HAMDI'],['17'],pid='seed')
        outside=Playlist('Keep empty',[],[],pid='keep-empty')
        self.export([seed,outside]);native=self.native()
        seed_row=next(row for row in native.playlists if row.title=='Seed' and not row.folder)
        tx.commit(self.delete(native,[seed_row.id]));empty=self.native()
        self.assertEqual(empty.songs,())
        hamdi=next(row for row in empty.playlists if row.title=='HAMDI')
        sets=next(row for row in empty.playlists if row.title=='SETS')
        keep=next(row for row in empty.playlists if row.title=='Keep empty')
        self.assertTrue(hamdi.folder);self.assertEqual(hamdi.parent,sets.id)
        with mock.patch('soundbox.cdj_source.capture_metadata',side_effect=AssertionError('Folder delete opened source')):
            plan=self.delete(empty,[hamdi.id])
        self.assertFalse(any(change.retire for change in plan.changes))
        tx.commit(plan);after=self.native()
        self.assertNotIn(hamdi.id,{row.id for row in after.playlists})
        self.assertEqual(next(row for row in after.playlists if row.id==sets.id),sets)
        self.assertEqual(next(row for row in after.playlists if row.id==keep.id),keep)
        for key in ('playlists','classic_playlists'):
            self.assertNotIn(int(hamdi.id),{int(row['id']) for row in after.catalog[key]})
            self.assertIn(int(sets.id),{int(row['id']) for row in after.catalog[key]})
        tx.commit(self.delete(after,[sets.id]));final=self.native()
        self.assertEqual(final.playlists,(keep,))
        self.assertEqual(final.songs,());self.assert_stock()

    def test_explicit_folder_union_preserves_outside_references_and_recovers_exact_catalogs(self):
        self.case.extra_track('18')
        selected=Playlist('Delete nested',['SETS','HAMDI'],['17','18','17'],pid='delete-nested')
        keep=Playlist('Keep',['OUTSIDE'],['17'],pid='keep-outside')
        self.export([selected,keep]);native=self.native()
        root=next(row for row in native.playlists if row.title=='SETS')
        child=next(row for row in native.playlists if row.title=='HAMDI')
        retained={row.id:row for row in native.playlists if row.title in ('OUTSIDE','Keep')}
        shared=next(row for row in native.songs if row.source_id=='17')
        shared_path=self.target/shared.path.lstrip('/')
        shared_before=(shared_path.read_bytes(),shared_path.stat().st_ino)
        before={path:raw for path,raw in native.files}
        plan=self.delete(native,[root.id,child.id])
        with self.assertRaises(faults.SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(faults.SimulatedCrash()))
        session=tx.CdjSession(self.target,[],token=self.case.token,volume_uuid=self.case.volume,target_role=self.role)
        tx.recover(session,self.case.recovery)
        self.assertEqual({path:(self.target/path).read_bytes() for path in before},before)
        stopped=[];retry=self.delete(self.native(),[root.id,child.id])
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            tx.commit(retry,should_cancel=lambda:bool(stopped),on_progress=lambda *_:stopped.append(True))
        self.assertEqual({path:(self.target/path).read_bytes() for path in before},before)
        tx.commit(self.delete(self.native(),[root.id,child.id]));after=self.native()
        self.assertEqual({row.id:row for row in after.playlists},retained)
        self.assertEqual({row.source_id for row in after.songs},{'17'})
        self.assertEqual((shared_path.read_bytes(),shared_path.stat().st_ino),shared_before)
        for key in ('playlists','classic_playlists'):
            self.assertEqual({int(row['id']) for row in after.catalog[key]},set(map(int,retained)))
        self.assert_stock()

    def test_folder_delete_preserves_classic_only_outside_playlist_and_its_shared_media(self):
        selected=Playlist('Delete nested',['SETS','HAMDI'],['17'],pid='delete-nested')
        keep=Playlist('Classic-only keep',['OUTSIDE'],['17'],pid='classic-only')
        self.export([selected,keep]);before=self.native()
        classic_path=self.target/'PIONEER/rekordbox/export.pdb';classic=classic_path.read_bytes()
        keep_row=next(row for row in before.playlists if row.title==keep.name)
        # Keep one real foreign Classic-only reference; the shared song remains
        # present throughout fixture construction and must survive deletion.
        tx.commit(self.delete(before,[keep_row.id]));classic_path.write_bytes(classic)
        divergent=self.native();root=next(row for row in divergent.playlists if row.title=='SETS')
        foreign=next(row for row in divergent.catalog['classic_playlists'] if int(row['id'])==int(keep_row.id))
        self.assertNotIn(int(keep_row.id),{int(row['id']) for row in divergent.catalog['playlists']})
        shared=divergent.songs[0];path=self.target/shared.path.lstrip('/')
        preserved=(path.read_bytes(),path.stat().st_ino)
        tx.commit(self.delete(divergent,[root.id]));after=self.native()
        self.assertEqual(next(row for row in after.catalog['classic_playlists'] if int(row['id'])==int(keep_row.id)),foreign)
        self.assertNotIn(int(keep_row.id),{int(row['id']) for row in after.catalog['playlists']})
        self.assertEqual([row.title for row in after.playlists],['OUTSIDE'])
        self.assertEqual((path.read_bytes(),path.stat().st_ino),preserved)
        self.assert_stock()

    def test_crash_and_cancel_restore_selected_catalogs_and_media_without_other_firmware_changes(self):
        self.export([self.case.playlist]);native=self.native();leaf=next(p for p in native.playlists if not p.folder)
        before={path:(self.target/path).read_bytes() for path,_raw in native.files}
        plan=self.delete(native,[leaf.id])
        with self.assertRaises(faults.SimulatedCrash):
            tx.commit(plan,on_progress=lambda *_:(_ for _ in ()).throw(faults.SimulatedCrash()))
        session=tx.CdjSession(self.target,[],token=self.case.token,volume_uuid=self.case.volume,target_role=self.role)
        tx.recover(session,self.case.recovery)
        self.assertEqual({path:(self.target/path).read_bytes() for path in before},before)
        cancelled=[];retry=self.delete(self.native(),[leaf.id])
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            tx.commit(retry,should_cancel=lambda:bool(cancelled),on_progress=lambda *_:cancelled.append(True))
        self.assertEqual({path:(self.target/path).read_bytes() for path in before},before)
        self.assert_stock()

    def test_stale_selection_invalid_ids_and_late_catalog_change_refuse(self):
        self.export([self.case.playlist]);native=self.native();leaf=next(p for p in native.playlists if not p.folder)
        original={path:(self.target/path).read_bytes() for path,_raw in native.files}
        for ids in ([],[True],[leaf.id,leaf.id],['4294967296'],['999999']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):self.delete(native,ids)
        stale=dict(native.files);path=tx.DATABASE_FILES[0];stale[path]+=b'stale selection'
        with self.assertRaisesRegex(ValueError,'changed after playlist selection'):
            cdj_export.plan_playlist_delete(self.target,[leaf.id],token=self.case.token,volume_uuid=self.case.volume,
                target_role=self.role,recovery_root=self.case.recovery,expected_catalog_files=stale)
        self.assertEqual({path:(self.target/path).read_bytes() for path in original},original)
        plan=self.delete(native,[leaf.id]);changed=self.target/tx.DATABASE_FILES[0]
        changed.write_bytes(changed.read_bytes()+b'external export')
        with self.assertRaisesRegex(ValueError,'USB changed'):tx.commit(plan)
        self.assertFalse((self.target/tx.JOURNAL).exists());self.assert_stock()

    def test_overwrite_metadata_names_only_exact_selected_existing_native_playlists(self):
        self.case.extra_track('18');other=Playlist('Unselected',[],['18'],pid='other')
        self.case.lib.playlists.append(other);self.export([self.case.playlist,other])
        plan=self.export([self.case.playlist])
        self.assertEqual(plan.overwritten_playlists,(self.case.playlist.name,))
        self.assertNotIn('Unselected',plan.overwritten_playlists)
        # Another exporter has a native library without our ownership manifest.
        # Its unique selected association still supplies an exact native ID.
        (self.target/tx.MANIFEST).unlink()
        foreign=self.export([self.case.playlist])
        self.assertEqual(foreign.overwritten_playlists,(self.case.playlist.name,))
        self.assertIn('Unselected',[p.title for p in self.native().playlists]);self.assert_stock()


class CdjIpodUsbPlaylistDelete(CdjPlaylistDelete):
    role='ipod_usb'


class FolderDeleteGraphProof(unittest.TestCase):
    def view(self):
        rows=[dict(id=4,parent=0,title='SETS',folder=True),
              dict(id=5,parent=4,title='HAMDI',folder=True),
              dict(id=6,parent=5,title='Inside',folder=False),
              dict(id=8,parent=0,title='OUTSIDE',folder=True),
              dict(id=9,parent=8,title='Keep',folder=False)]
        return dict(playlists=rows,classic_playlists=deepcopy(rows))

    def test_explicit_roots_expand_once_without_ancestors_or_classic_only_foreign_rows(self):
        from soundbox.cdj_delete import _selected_rows
        view=self.view();view['classic_playlists'].append(dict(id=10,parent=8,title='Foreign',folder=False))
        _rows,selected=_selected_rows(view,{5,6})
        self.assertEqual(selected,{5,6});self.assertNotIn(4,selected);self.assertNotIn(10,selected)
        for key in ('playlists','classic_playlists'):
            view[key]=[row for row in view[key] if row['id']!=6]
        self.assertEqual(_selected_rows(view,{5})[1],{5})

    def test_ambiguous_parent_graphs_and_cross_catalog_selected_subtrees_refuse(self):
        from soundbox.cdj_delete import _selected_rows
        for case in ('duplicate','cycle','missing-parent','leaf-parent','float-id','boolean-parent',
                     'classic-cycle','classic-title','classic-parent','classic-folder','classic-descendant','missing-classic'):
            with self.subTest(case=case):
                view=self.view()
                if case=='duplicate':view['playlists'].append(deepcopy(view['playlists'][1]))
                elif case=='cycle':view['playlists'][0]['parent']=5
                elif case=='missing-parent':view['playlists'][1]['parent']=999
                elif case=='leaf-parent':view['playlists'][1]['parent']=9
                elif case=='float-id':view['playlists'][1]['id']=5.1
                elif case=='boolean-parent':view['playlists'][1]['parent']=True
                elif case=='classic-cycle':view['classic_playlists'][0]['parent']=5
                elif case=='classic-title':view['classic_playlists'][1]['title']='Another identity'
                elif case=='classic-parent':view['classic_playlists'][1]['parent']=8
                elif case=='classic-folder':view['classic_playlists'][1]['folder']=False
                elif case=='classic-descendant':view['classic_playlists'].append(dict(id=10,parent=5,title='Unknown',folder=False))
                elif case=='missing-classic':view['classic_playlists']=[]
                with self.assertRaises(ValueError):_selected_rows(view,{5})
