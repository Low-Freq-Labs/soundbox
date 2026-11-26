"""Native Delete prunes small art metadata without touching retained pixels."""
from contextlib import contextmanager
from dataclasses import replace
import builtins
import hashlib
import os
from pathlib import Path
import unittest
from unittest import mock

from PIL import Image
from clickwheel.ipod import get_ipod_tracks,read_ipod

from soundbox import stock_ipod
from soundbox.stock_artwork import read_stock_artwork_slots
from soundbox.sync import DeviceSession
from tests import test_stock_ipod as fixtures


class StockDeleteArtwork(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StockIpodSync();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.ipod
        self.artwork=self.root/'iPod_Control/Artwork'
        self.database=self.root/'iPod_Control/iTunes/iTunesDB'

    def seed(self,*,new_art=True):
        f=self.fixture;images={}
        for key,color in [('old',(220,10,10)),('new',(10,10,220))]:
            path=f.base/(key+'.jpg');Image.new('RGB',(300,300),color).save(path,'JPEG')
            if key=='old' or new_art:images[key]=path
        plan=stock_ipod.plan_stock_ipod_sync(f.library,[f.playlist],self.root,
            model_number='MA446',filesystem='fat32',usb_family='iPod Video',
            art_cache=fixtures._FixtureArtCache(images),stock_art_cache=f.stock_art_cache)
        stock_ipod.commit_stock_ipod_sync(plan)
        self.selected=next(int(row['playlistID']) for row in read_ipod(self.root)['mhlp']
                           if row.get('Title')=='SoundBox Test')
        return plan

    def plan(self):
        # Preservation of native pixels needs no Mac JPEG/derivative cache.
        return stock_ipod.plan_stock_ipod_playlist_delete(self.root,[self.selected],
            model_number='MA446',filesystem='fat32',usb_family='iPod Video')

    @contextmanager
    def no_pixel_io(self):
        opened,path_open,os_open=builtins.open,Path.open,os.open
        def check(path):
            if isinstance(path,(str,bytes,os.PathLike)) and Path(os.fsdecode(path)).suffix=='.ithmb':
                raise AssertionError('Delete opened retained pixel payload: '+os.fsdecode(path))
        def guarded_open(path,*args,**kwargs):check(path);return opened(path,*args,**kwargs)
        def guarded_path_open(path,*args,**kwargs):check(path);return path_open(path,*args,**kwargs)
        def guarded_os_open(path,*args,**kwargs):check(path);return os_open(path,*args,**kwargs)
        with (mock.patch.object(builtins,'open',guarded_open),mock.patch.object(Path,'open',guarded_path_open),
              mock.patch.object(os,'open',guarded_os_open)):
            yield

    def pixel_epochs(self):
        return {path.name:(path.stat().st_ino,path.stat().st_size,path.stat().st_mtime_ns,path.stat().st_ctime_ns)
                for path in self.artwork.glob('*.ithmb')}

    def test_large_retained_pixel_files_are_never_read_hashed_copied_or_written(self):
        self.seed()
        foreign=self.artwork/'unmanaged-note.bin';foreign.write_bytes(b'Unowned artwork-folder content')
        native_before=next(row for row in get_ipod_tracks(read_ipod(self.root)) if int(row['dbid'])==101)
        _raw,slots,_files=read_stock_artwork_slots(self.artwork)
        retained={key:slot for key,slot in slots.items() if slot.song_id==101}
        self.assertEqual(len(retained),1)
        prefixes={path.name:path.read_bytes() for path in self.artwork.glob('*.ithmb')}
        for path in self.artwork.glob('*.ithmb'):
            alignment=20_000 if path.name.startswith('F1028') else 80_000
            with path.open('r+b') as stream:stream.truncate(((64*1024*1024)//alignment+1)*alignment)
        before=self.pixel_epochs()
        sources={path:hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (self.fixture.existing_source,self.fixture.new_source)}
        with self.no_pixel_io():
            plan=self.plan()
            self.assertTrue(plan.rendered_artwork.incremental)
            self.assertFalse(plan.rendered_artwork.range_writes)
            self.assertEqual([item.relative_path.name for item in plan.artwork_before],['ArtworkDB'])
            self.assertEqual(plan.artwork_bytes_to_copy,len(plan.rendered_artwork.artwork_db))
            result=stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertFalse(result.cleanup_failures)
        self.assertEqual(result.remaining_tracks,1)
        self.assertEqual(self.pixel_epochs(),before)
        _raw,current,_files=read_stock_artwork_slots(self.artwork)
        self.assertEqual(set(current),set(retained))
        for key,slot in retained.items():
            self.assertEqual(current[key].raw,slot.raw)
            self.assertEqual(current[key].offsets,slot.offsets)
        for path in self.artwork.glob('*.ithmb'):
            with path.open('rb') as stream:self.assertEqual(stream.read(len(prefixes[path.name])),prefixes[path.name])
        native=get_ipod_tracks(read_ipod(self.root))
        self.assertEqual(int(native[0]['dbid']),101)
        self.assertEqual(int(native[0]['mhiiLink']),next(iter(retained)))
        for field in ('title','artist','location','playCount','rating','bookmarkTime','mhiiLink','artworkCount','artworkSize'):
            self.assertEqual(native[0].get(field),native_before.get(field),field)
        self.assertEqual(foreign.read_bytes(),b'Unowned artwork-folder content')
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},sources)

    def test_unarted_song_deletion_does_not_render_or_republish_retained_art(self):
        self.seed(new_art=False)
        before=self.pixel_epochs();database=(self.artwork/'ArtworkDB').read_bytes()
        with self.no_pixel_io(),mock.patch.object(stock_ipod,'render_incremental_stock_artwork',
                side_effect=AssertionError('Unchanged art was rendered')):
            plan=self.plan();self.assertIsNone(plan.rendered_artwork)
            self.assertFalse(plan.artwork_before)
            result=stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertFalse(result.cleanup_failures)
        self.assertEqual((self.artwork/'ArtworkDB').read_bytes(),database)
        self.assertEqual(self.pixel_epochs(),before)

    def test_database_publication_failure_restores_artworkdb_without_touching_pixels(self):
        self.seed();plan=self.plan()
        before=self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()
        write=DeviceSession.atomic_write
        def fail_database(session,path,data):
            if Path(path).resolve()==self.database.resolve():raise OSError('Generated database failure')
            return write(session,path,data)
        with self.no_pixel_io(),mock.patch.object(DeviceSession,'atomic_write',fail_database):
            with self.assertRaisesRegex(OSError,'Generated database failure'):
                stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual((self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()),before)
        self.assertEqual(len(get_ipod_tracks(read_ipod(self.root))),2)

    def test_failure_after_artworkdb_replace_restores_it_before_any_catalog_change(self):
        self.seed();plan=self.plan()
        before=self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()
        write=DeviceSession.atomic_write;failed=[]
        def fail_after_artwork(session,path,data):
            result=write(session,path,data)
            if Path(path).resolve()==(self.artwork/'ArtworkDB').resolve() and not failed:
                failed.append(True);raise OSError('Generated post-replace art failure')
            return result
        with self.no_pixel_io(),mock.patch.object(DeviceSession,'atomic_write',fail_after_artwork):
            with self.assertRaisesRegex(OSError,'Generated post-replace art failure'):
                stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertTrue(failed)
        self.assertEqual((self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()),before)

    def test_shared_image_keeps_its_original_song_owner_when_that_song_is_deleted(self):
        self.seed()
        # Native Music may deliberately share one MHII between different songs.
        # Build that valid generated graph independently of SoundBox's per-source
        # cover policy; the MHII's original song owner remains 101.
        infos=[stock_ipod._existing_track_info(row) for row in get_ipod_tracks(read_ipod(self.root))]
        owner=next(row for row in infos if row.dbid==101)
        alias=next(row for row in infos if row.dbid!=101)
        alias.mhii_link=owner.mhii_link;alias.artwork_count=owner.artwork_count;alias.artwork_size=owner.artwork_size
        self.assertTrue(fixtures.write_itunesdb(self.root,infos,backup=False,
            force_checksum=fixtures.ChecksumType.NONE,
            capabilities=fixtures.capabilities_for_family_gen('iPod Video','5.5th Gen'),
            playlists=[fixtures.PlaylistInfo('Keep Me',[101],playlist_id=202),
                       fixtures.PlaylistInfo('Alias only',[alias.dbid],playlist_id=999)]))
        graph=stock_ipod.render_incremental_stock_artwork(infos,{},existing_artwork_dir=self.artwork)
        (self.artwork/'ArtworkDB').write_bytes(graph.artwork_db)
        self.selected=202
        before=(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()
        _raw,slots,_files=read_stock_artwork_slots(self.artwork)
        self.assertEqual(len(slots),1)
        self.assertEqual(next(iter(slots.values())).song_id,101)
        with self.no_pixel_io():
            plan=self.plan()
            self.assertFalse(plan.rendered_artwork.publish_artwork_db)
            self.assertFalse(plan.artwork_before)
            result=stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual(result.deleted_track_records,1)
        self.assertEqual((self.artwork/'ArtworkDB').read_bytes(),before[0])
        self.assertEqual(self.pixel_epochs(),before[1])
        native=get_ipod_tracks(read_ipod(self.root))
        self.assertEqual(len(native),1)
        self.assertNotEqual(int(native[0]['dbid']),101)
        self.assertEqual(int(native[0]['mhiiLink']),next(iter(slots)))

    def test_changed_retained_pixel_layout_refuses_before_art_or_native_publication(self):
        self.seed();plan=self.plan();path=next(self.artwork.glob('*.ithmb'))
        with path.open('r+b') as stream:stream.truncate(path.stat().st_size+20_000)
        before=self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()
        with self.no_pixel_io(),self.assertRaisesRegex(stock_ipod.StockIpodError,'Artwork layout changed'):
            stock_ipod.commit_stock_ipod_playlist_delete(plan)
        self.assertEqual((self.database.read_bytes(),(self.artwork/'ArtworkDB').read_bytes(),self.pixel_epochs()),before)

    def test_delete_rejects_a_full_pixel_render_instead_of_concatenating_placeholders(self):
        self.seed();plan=self.plan();before=self.database.read_bytes(),self.pixel_epochs()
        invalid=replace(plan,rendered_artwork=replace(plan.rendered_artwork,incremental=False))
        with self.no_pixel_io(),self.assertRaisesRegex(stock_ipod.StockIpodError,'only prune artwork metadata'):
            stock_ipod.commit_stock_ipod_playlist_delete(invalid)
        self.assertEqual((self.database.read_bytes(),self.pixel_epochs()),before)
