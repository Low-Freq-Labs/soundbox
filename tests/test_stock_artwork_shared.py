"""Native Music shared-MHII references through incremental saves and compaction."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from soundbox.stock_artwork import (
    CLASSIC_55_PROFILE, CLASSIC_PROFILE, PROFILE, StockArtworkCache,
    StockArtworkError, _children, _records, _u32, _u64,
    read_stock_artwork_slots, render_incremental_stock_artwork,
    render_stock_artwork, validate_stock_artwork,
)
from tests import test_stock_artwork as fixture
from tests.test_stock_artwork_classic import native_artwork


class SharedNativeArtwork(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name)
        self.images={}
        for name,color in (('red',(190,30,30)),('green',(30,180,30)),('blue',(30,30,190)),('yellow',(180,180,30))):
            path=self.root/(name+'.jpg');Image.new('RGB',(320,240),color).save(path,'JPEG')
            self.images[name]=path

    def fixture(self, profile):
        cache=StockArtworkCache(self.root/('cache-'+profile),profile=profile)
        owner,foreign=fixture.StockArtwork._track(101),fixture.StockArtwork._track(303)
        rendered=render_stock_artwork([owner,foreign],
            {101:cache.prepare(self.images['red']),303:cache.prepare(self.images['green'])},
            cache=cache,existing_artwork_dir=self.root/'absent')
        original=native_artwork(rendered.artwork_db)
        # Music's MHIT size and MHII original-image size are independent;
        # the current real catalog stores MHIT sizes one byte smaller.
        owner.artwork_size-=1;foreign.artwork_size-=1
        folder=self.root/('native-'+profile);folder.mkdir(exist_ok=True)
        (folder/'ArtworkDB').write_bytes(original)
        for plan in rendered.ithmb_files:
            (folder/plan.relative_path.name).write_bytes(b''.join(p.path.read_bytes() for p in plan.segments))
        alias=fixture.StockArtwork._track(202)
        alias.mhii_link=owner.mhii_link;alias.artwork_count=owner.artwork_count;alias.artwork_size=owner.artwork_size
        tracks=[owner,foreign,alias]
        return cache,tracks,folder,original

    def apply(self, rendered, folder):
        for plan in rendered.ithmb_files:
            path=folder/plan.relative_path.name
            if not rendered.incremental:
                path.write_bytes(b''.join(part.path.read_bytes() for part in plan.segments))
        for write in rendered.range_writes:
            with (folder/write.relative_path.name).open('r+b') as handle:
                handle.seek(write.offset);handle.write(write.segment.path.read_bytes())
        (folder/'ArtworkDB').write_bytes(rendered.artwork_db)

    def test_noop_keeps_shared_links_native_order_and_bytes_without_pixel_reads(self):
        for profile in (PROFILE,CLASSIC_PROFILE,CLASSIC_55_PROFILE):
            with self.subTest(profile=profile):
                _cache,tracks,folder,original=self.fixture(profile)
                before=deepcopy(tracks);opened=Path.open
                def guarded(path,*args,**kwargs):
                    if path.suffix=='.ithmb':raise AssertionError('No-op read native pixels')
                    return opened(path,*args,**kwargs)
                with mock.patch.object(Path,'open',guarded):
                    rendered=render_incremental_stock_artwork(list(reversed(tracks)),{},existing_artwork_dir=folder)
                self.assertEqual(rendered.artwork_db,original)
                self.assertFalse(rendered.publish_artwork_db);self.assertEqual(rendered.range_writes,())
                self.assertEqual(rendered.linked_tracks,3);self.assertEqual(len(rendered.slots),2)
                self.assertEqual(tracks,before)
                self.assertEqual(next(s.track_dbids for s in rendered.slots if s.image_id==tracks[0].mhii_link),(202,101))
                validate_stock_artwork(rendered,tracks)

    def test_changing_one_shared_alias_or_owner_preserves_other_native_image(self):
        for selected in (101,202):
            with self.subTest(selected=selected):
                cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
                old_id=tracks[0].mhii_link;before=_records(original)[1]
                pixels={p.name:p.read_bytes() for p in folder.glob('*.ithmb')}
                prepared=cache.prepare(self.images['blue'])
                rendered=render_incremental_stock_artwork(tracks,{selected:prepared},existing_artwork_dir=folder)
                changed=next(t for t in tracks if t.dbid==selected)
                retained=next(t for t in tracks if t.dbid==(202 if selected==101 else 101))
                self.assertNotEqual(changed.mhii_link,old_id);self.assertEqual(retained.mhii_link,old_id)
                after=_records(rendered.artwork_db)[1]
                for identity,raw in before.items():self.assertEqual(after[identity],raw)
                self.assertEqual(_u64(after[changed.mhii_link],20),selected)
                self.assertEqual(rendered.updated_dbids,(selected,))
                self.assertEqual(next(s.track_dbids for s in rendered.slots if s.image_id==old_id),(retained.dbid,))
                for write in rendered.range_writes:self.assertGreaterEqual(write.offset,len(pixels[write.relative_path.name]))
                self.assertEqual({p.name:p.read_bytes() for p in folder.glob('*.ithmb')},pixels)
                self.apply(rendered,folder);validate_stock_artwork(rendered,tracks)
                for name,payload in pixels.items():self.assertEqual((folder/name).read_bytes()[:len(payload)],payload)
                warm=render_incremental_stock_artwork(tracks,{selected:prepared},existing_artwork_dir=folder)
                self.assertFalse(warm.publish_artwork_db);self.assertEqual(warm.range_writes,())
                self.assertEqual(warm.artwork_db,rendered.artwork_db)

    def test_both_shared_tracks_can_receive_independent_art_without_live_slot_overwrite(self):
        cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
        old=tracks[0].mhii_link;sizes={p.name:p.stat().st_size for p in folder.glob('*.ithmb')}
        rendered=render_incremental_stock_artwork(tracks,
            {101:cache.prepare(self.images['blue']),202:cache.prepare(self.images['yellow'])},existing_artwork_dir=folder)
        self.assertNotEqual(tracks[0].mhii_link,tracks[2].mhii_link)
        self.assertNotIn(old,_records(rendered.artwork_db)[1])
        self.assertEqual(set(rendered.updated_dbids),{101,202})
        for write in rendered.range_writes:self.assertGreaterEqual(write.offset,sizes[write.relative_path.name])
        self.assertEqual((folder/'ArtworkDB').read_bytes(),original)
        self.apply(rendered,folder);validate_stock_artwork(rendered,tracks)
        read_stock_artwork_slots(folder)

    def test_same_pixels_do_not_rewrite_native_crops_or_relink_shared_tracks(self):
        cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
        rendered=render_incremental_stock_artwork(tracks,{101:cache.prepare(self.images['red']),202:cache.prepare(self.images['red'])},existing_artwork_dir=folder)
        self.assertEqual(rendered.artwork_db,original);self.assertEqual(rendered.range_writes,())
        self.assertEqual(rendered.updated_dbids,())
        self.assertEqual(tracks[0].mhii_link,tracks[2].mhii_link)

    def test_full_compaction_and_owner_deletion_keep_remaining_alias_and_native_padding(self):
        for delete_owner in (False,True):
            with self.subTest(delete_owner=delete_owner):
                cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
                old_id=tracks[0].mhii_link
                chosen=[t for t in reversed(tracks) if not delete_owner or t.dbid!=101]
                before_links={t.dbid:(t.mhii_link,t.artwork_count,t.artwork_size) for t in chosen}
                rendered=render_stock_artwork(chosen,{},cache=cache,existing_artwork_dir=folder)
                self.assertEqual({t.dbid:(t.mhii_link,t.artwork_count,t.artwork_size) for t in chosen},before_links)
                self.assertEqual(next(t.mhii_link for t in chosen if t.dbid==202),old_id)
                before=_records(original)[1][old_id];after=_records(rendered.artwork_db)[1][old_id]
                self.assertEqual(_u64(after,20),101)
                for (kind,old),(new_kind,new) in zip(_children(before),_children(after),strict=True):
                    self.assertEqual(kind,new_kind)
                    if kind==2:
                        old_offset,new_offset=_u32(old,4),_u32(new,4)
                        self.assertEqual(old[:old_offset+20],new[:new_offset+20])
                        self.assertEqual(old[old_offset+24:],new[new_offset+24:])
                    else:self.assertEqual(old,new)
                self.assertEqual(rendered.linked_tracks,len(chosen))
                self.apply(rendered,folder);validate_stock_artwork(rendered,chosen)
                read_stock_artwork_slots(folder)

    def test_full_relayout_splits_changed_owner_but_keeps_alias_original_record(self):
        cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
        old=tracks[0].mhii_link
        rendered=render_stock_artwork(tracks,{101:cache.prepare(self.images['blue'])},cache=cache,existing_artwork_dir=folder)
        self.assertNotEqual(tracks[0].mhii_link,old);self.assertEqual(tracks[2].mhii_link,old)
        self.assertEqual(_u64(_records(rendered.artwork_db)[1][old],20),101)
        self.assertEqual(next(s.track_dbids for s in rendered.slots if s.image_id==old),(202,))
        self.apply(rendered,folder);validate_stock_artwork(rendered,tracks)
        read_stock_artwork_slots(folder)

    def test_validator_rejects_new_alias_and_changed_captured_owner(self):
        _cache,tracks,folder,original=self.fixture(CLASSIC_55_PROFILE)
        rendered=render_incremental_stock_artwork(tracks,{},existing_artwork_dir=folder)
        forged=fixture.StockArtwork._track(909);forged.mhii_link=tracks[0].mhii_link;forged.artwork_count=1
        with self.assertRaisesRegex(StockArtworkError,'does not match'):
            validate_stock_artwork(rendered,[*tracks,forged])
        data=bytearray(rendered.artwork_db);record=_records(original)[1][tracks[0].mhii_link]
        offset=data.find(record);self.assertGreater(offset,0);struct.pack_into('<Q',data,offset+20,999)
        with self.assertRaisesRegex(StockArtworkError,'does not match|native identity'):
            validate_stock_artwork(replace(rendered,artwork_db=bytes(data)),tracks)

    def test_missing_explicit_reference_remains_refused(self):
        cache,tracks,folder,_original=self.fixture(CLASSIC_55_PROFILE)
        tracks[2].mhii_link=999999
        before=deepcopy(tracks)
        for full in (False,True):
            with self.subTest(full=full),self.assertRaisesRegex(StockArtworkError,'invalid'):
                if full:render_stock_artwork(tracks,{},cache=cache,existing_artwork_dir=folder)
                else:render_incremental_stock_artwork(tracks,{},existing_artwork_dir=folder)
        self.assertEqual(tracks,before)

    def test_rendered_slot_cannot_cache_an_alias_under_another_native_owner(self):
        _cache,tracks,folder,_original=self.fixture(CLASSIC_55_PROFILE)
        rendered=render_incremental_stock_artwork(tracks,{},existing_artwork_dir=folder)
        first,*remaining=rendered.slots
        for wrong in (replace(first,track_dbids=(303,)),replace(first,song_id=202)):
            with self.subTest(wrong=wrong),self.assertRaisesRegex(StockArtworkError,'slot references'):
                validate_stock_artwork(replace(rendered,slots=(wrong,*remaining)),tracks)
