"""Generated Classic/Video pixel profiles and opaque ArtworkDB preservation."""
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image
from clickwheel.ipod.itunesdb_writer import TrackInfo

from soundbox.stock_artwork import (StockArtworkCache,StockArtworkError,CLASSIC_PROFILE,CLASSIC_55_PROFILE,
    _children,_datasets,_images,_records,_u32,_PROFILES,read_stock_artwork_slots,
    render_stock_artwork,render_incremental_stock_artwork,validate_stock_artwork)
from soundbox.stock_ipod_format import (ARTWORK_PROFILE,CLASSIC_ARTWORK_PROFILE,CLASSIC_55_ARTWORK_PROFILE,
    resolve_stock_artwork_profile)


def native_artwork(data):
    root,datasets=_datasets(data);list_header,records=_records(data)
    native=[]
    for raw in records.values():
        header=bytearray(raw[:_u32(raw,4)])
        struct.pack_into('<I',header,36,0x13579bdf)
        children=[]
        for kind,child in _children(raw):
            item=bytearray(child)
            ni=_u32(item,4)
            if kind==2 and _u32(item,ni+16)==1061:
                struct.pack_into('<h',item,ni+28,12)
                struct.pack_into('<H',item,ni+32,43)
            children.append(bytes(item))
        auxiliary=bytearray(120);auxiliary[:4]=b'mhod'
        struct.pack_into('<IIH',auxiliary,4,24,120,6)
        auxiliary[24:28]=b'mhaf';auxiliary[40:48]=b'opaque-6'
        children.append(bytes(auxiliary))
        payload=b''.join(children);struct.pack_into('<II',header,8,len(header)+len(payload),len(children))
        native.append(bytes(header)+payload)
    body=b''
    for kind,header,payload in datasets:
        if kind==1:payload=list_header+b''.join(native)
        patched=bytearray(header);patched[32:40]=b'dsopaque'
        struct.pack_into('<I',patched,8,len(patched)+len(payload))
        body+=bytes(patched)+payload
    header=bytearray(root);header[80:88]=b'rootkeep';struct.pack_into('<I',header,8,len(header)+len(body))
    return bytes(header)+body


class ClassicArtwork(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name);self.image=self.root/'curated.jpg'
        Image.new('RGB',(320,240),(30,80,190)).save(self.image,'JPEG')
        self.cache=StockArtworkCache(self.root/'cache')

    def tracks(self):
        return [TrackInfo(title=str(i),location=f':iPod_Control:Music:F00:{i}.mp3',
                          size=1000,length=1000,filetype='mp3',dbid=i) for i in (101,202)]

    def publish(self, rendered):
        folder=self.root/'device/iPod_Control/Artwork';folder.mkdir(parents=True,exist_ok=True)
        (folder/'ArtworkDB').write_bytes(rendered.artwork_db)
        for plan in rendered.ithmb_files:
            (folder/plan.relative_path.name).write_bytes(b''.join(part.path.read_bytes() for part in plan.segments))
        return folder

    def test_video_and_both_classic_profiles_have_separate_exact_cache_payloads(self):
        original=self.image.read_bytes();keys=set()
        for profile,sizes in ((ARTWORK_PROFILE,{1028:20000,1029:80000}),
                (CLASSIC_ARTWORK_PROFILE,{1055:32768,1060:204800,1061:6272}),
                (CLASSIC_55_ARTWORK_PROFILE,{1055:32768,1060:204800,1061:6160})):
            with self.subTest(profile=profile):
                cache=self.cache.for_profile(profile);prepared=cache.prepare(self.image)
                keys.add(prepared.cache_key)
                self.assertEqual({p.format_id:p.size for p in prepared.payloads},sizes)
                self.assertEqual(set(cache.format_ids),set(sizes))
                self.assertEqual(cache.prepare(self.image),prepared)
                if profile==CLASSIC_55_ARTWORK_PROFILE:
                    pixels=prepared.payload(1061).path.read_bytes()
                    self.assertEqual((prepared.payload(1061).width,prepared.payload(1061).height),(55,55))
                    self.assertTrue(all(pixels[row*112+110:row*112+112]==b'\0\0' for row in range(55)))
        self.assertEqual(len(keys),3);self.assertEqual(self.image.read_bytes(),original)
        self.assertIs(self.cache.for_profile(ARTWORK_PROFILE),self.cache)

    def test_only_exact_observed_classic_sizes_select_the_padded_profile(self):
        for profile in (CLASSIC_PROFILE,CLASSIC_55_PROFILE):
            cache=self.cache.for_profile(profile);tracks=self.tracks()
            rendered=render_stock_artwork(tracks,{101:cache.prepare(self.image)},cache=cache,
                                         existing_artwork_dir=self.root/'absent')
            self.assertEqual(resolve_stock_artwork_profile(rendered.artwork_db)[0],profile)
            validate_stock_artwork(rendered,tracks)
        self.assertEqual(resolve_stock_artwork_profile(None,'iPod Classic')[0],CLASSIC_ARTWORK_PROFILE)
        self.assertEqual(resolve_stock_artwork_profile(None,'iPod Video')[0],ARTWORK_PROFILE)
        corrupted=bytearray(rendered.artwork_db)
        _,datasets=_datasets(corrupted);cursor=_u32(corrupted,4)
        for kind,header,body in datasets:
            if kind==3:
                position=cursor+len(header)+_u32(body,4)
                struct.pack_into('<I',corrupted,position+20,12345);break
            cursor+=len(header)+len(body)
        self.assertFalse(resolve_stock_artwork_profile(bytes(corrupted),'iPod Classic')[0])

    def test_incremental_classic_keeps_original_headers_auxiliary_and_unselected_pixels(self):
        cache=self.cache.for_profile(CLASSIC_55_PROFILE);tracks=self.tracks()
        first=cache.prepare(self.image)
        rendered=render_stock_artwork(tracks,{101:first,202:first},cache=cache,
                                     existing_artwork_dir=self.root/'absent')
        original=native_artwork(rendered.artwork_db)
        folder=self.publish(replace(rendered,artwork_db=original))
        before_files={path.name:path.read_bytes() for path in folder.glob('*.ithmb')}
        _,before=_records(original)
        changed=self.root/'changed.jpg';Image.new('RGB',(320,240),(190,25,30)).save(changed,'JPEG')
        updated=render_incremental_stock_artwork(tracks,{101:cache.prepare(changed)},
            existing_artwork_dir=folder,expected_artwork_database=original)
        self.assertEqual(updated.profile,CLASSIC_55_PROFILE)
        self.assertEqual(updated.updated_dbids,(101,))
        _,after=_records(updated.artwork_db)
        for identity,raw in before.items():
            if int.from_bytes(raw[20:28],'little')==202:self.assertEqual(after[identity],raw)
            else:
                self.assertEqual(after[identity][36:40],raw[36:40])
                self.assertEqual([child for kind,child in _children(after[identity]) if kind==6],
                                 [child for kind,child in _children(raw) if kind==6])
        self.assertEqual(updated.artwork_db[80:88],b'rootkeep')
        old_ds={kind:(header,body) for kind,header,body in _datasets(original)[1]}
        new_ds={kind:(header,body) for kind,header,body in _datasets(updated.artwork_db)[1]}
        for kind in (2,3):self.assertEqual(new_ds[kind],old_ds[kind])
        for plan in updated.ithmb_files:self.assertEqual(plan.before_size,len(before_files[plan.relative_path.name]))
        for write in updated.range_writes:
            self.assertGreaterEqual(write.offset,len(before_files[write.relative_path.name]))
            self.assertEqual(write.segment.size,{1055:32768,1060:204800,1061:6160}[write.format_id])
        self.assertEqual({path.name:path.read_bytes() for path in folder.glob('*.ithmb')},before_files)
        self.assertEqual((folder/'ArtworkDB').read_bytes(),original)
        validate_stock_artwork(updated,tracks)

    def test_mixed_video_preparation_cannot_enter_classic_artwork(self):
        cache=self.cache.for_profile(CLASSIC_55_PROFILE);tracks=self.tracks()
        with self.assertRaisesRegex(StockArtworkError,'different pixel profile'):
            render_stock_artwork(tracks,{101:self.cache.prepare(self.image)},cache=cache,
                                 existing_artwork_dir=self.root/'absent')

    def test_corrupt_classic_cache_rebuilds_without_touching_curated_jpeg(self):
        cache=self.cache.for_profile(CLASSIC_55_PROFILE);original=self.image.read_bytes()
        prepared=cache.prepare(self.image);payload=prepared.payload(1061)
        payload.path.write_bytes(b'x'*payload.size)
        repaired=cache.prepare(self.image)
        self.assertEqual(hashlib.sha256(repaired.payload(1061).path.read_bytes()).hexdigest(),payload.sha256)
        self.assertEqual(self.image.read_bytes(),original)

    def test_full_relayout_preserves_unselected_native_padding_and_opaque_children(self):
        cache=self.cache.for_profile(CLASSIC_55_PROFILE);tracks=self.tracks()
        other=self.root/'other.jpg';Image.new('RGB',(320,240),(20,180,40)).save(other,'JPEG')
        first=render_stock_artwork(tracks,{101:cache.prepare(self.image),202:cache.prepare(other)},cache=cache,
                                   existing_artwork_dir=self.root/'absent')
        original=native_artwork(first.artwork_db)
        folder=self.publish(replace(first,artwork_db=original))
        self.assertEqual(len(read_stock_artwork_slots(folder)[1]),2)
        changed=self.root/'new.jpg';Image.new('RGB',(320,240),(220,30,20)).save(changed,'JPEG')
        final=render_stock_artwork(list(reversed(tracks)),{101:cache.prepare(changed)},cache=cache,
                                   existing_artwork_dir=folder)
        before=next(raw for raw in _records(original)[1].values() if int.from_bytes(raw[20:28],'little')==202)
        after=next(raw for raw in _records(final.artwork_db)[1].values() if int.from_bytes(raw[20:28],'little')==202)
        self.assertNotEqual(_images(before,_PROFILES[CLASSIC_55_PROFILE])[1061][0],
                            _images(after,_PROFILES[CLASSIC_55_PROFILE])[1061][0])
        self.assertEqual(after[36:40],before[36:40])
        for (kind,old),(new_kind,new) in zip(_children(before),_children(after),strict=True):
            self.assertEqual(kind,new_kind)
            if kind==2:
                old_ni,new_ni=_u32(old,4),_u32(new,4)
                self.assertEqual(old[old_ni+24:],new[new_ni+24:])
            else:self.assertEqual(old,new)
        validate_stock_artwork(final,tracks)
