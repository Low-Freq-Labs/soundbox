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
