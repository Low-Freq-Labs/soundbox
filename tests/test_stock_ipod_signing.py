"""Generated signing/layout controls; no live catalog or source dependencies."""
import io
import struct
import unittest
from unittest import mock

from clickwheel.ipod import parse_itunesdb, get_ipod_tracks
from clickwheel.ipod.itunesdb_writer.hash58 import write_hash58

from soundbox import stock_ipod
from soundbox.stock_ipod_capture import capture_stock_ipod_native
from soundbox.stock_ipod_format import inspect_stock_write_format, CLASSIC_HASH58_PROFILE, MODERN_VIDEO_PROFILE
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_native_preserve import _datasets, _list, _mhods, _rebuilt_dataset, _labels
from soundbox.stock_ipod_signing import (
    StockSigningContext, StockSigningError, normalize_firewire_guid,
    verified_stock_signing_context, sign_stock_database,
)
import tests.test_stock_ipod_catalog as catalog_fixtures

GUID = '0011223344556677'


class ClassicSigningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = catalog_fixtures.StockCatalogTests('test_name_count_recognizes_and_track_links_ignore_playlist_order')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.target/'iPod_Control/iTunes/iTunesDB'
        self.context = StockSigningContext(bytes.fromhex(GUID), bytes(range(16)), bytes(range(12)))
        raw = bytearray(self.db.read_bytes()); struct.pack_into('<I', raw, 0x10, 117)
        raw[0xD0:0xD4] = b'keep'
        self.db.write_bytes(sign_stock_database(bytes(raw), self.context))
        self.snapshot = self.read_snapshot()

    def read_snapshot(self):
        return read_stock_ipod_snapshot(self.fixture.target, filesystem='fat32', firewire_guid=GUID,
            usb_family='iPod Classic', volume_uuid='generated-classic',
            native_capture=capture_stock_ipod_native(self.fixture.target, include_artwork=True))

    def plan(self, **options):
        return stock_ipod.plan_stock_ipod_catalog_update(self.fixture.library, [self.fixture.playlist],
            self.fixture.target, snapshot=self.snapshot, model_number='', filesystem='fat32',
            firewire_guid=GUID, usb_family='iPod Classic', art_cache=self.fixture.art,
            stock_art_cache=self.fixture.stock_art, update_metadata=True, **options)

    def test_actual_guid_must_authenticate_full_primary_and_secondary_signatures(self):
        data = self.db.read_bytes()
        self.assertEqual(verified_stock_signing_context(data, GUID), self.context)
        self.assertEqual(sign_stock_database(data, self.context), data)
        self.assertTrue(self.snapshot.stock_format.signature_verified)
        self.assertEqual(self.snapshot.stock_format.database_profile, CLASSIC_HASH58_PROFILE)
        for guid in ('', 'bad', '0'*16, 'FFEEDDCCBBAA9988'):
            with self.subTest(guid=guid):
                self.assertFalse(inspect_stock_write_format(data, firewire_guid=guid).writable)
        for value in (b'', b'1234567', bytes(8), 'xyz'*6):
            with self.subTest(value=value):
                with self.assertRaises(StockSigningError): normalize_firewire_guid(value)

    def test_stale_payload_or_stale_secondary_refuses_even_with_fresh_primary(self):
        data = bytearray(self.db.read_bytes()); data[-1] ^= 1
        with self.assertRaisesRegex(StockSigningError, 'checksum does not match'):
            verified_stock_signing_context(bytes(data), GUID)
        write_hash58(data, bytes.fromhex(GUID))
        with self.assertRaisesRegex(StockSigningError, 'HASH72 signature is invalid'):
            verified_stock_signing_context(bytes(data), GUID)

    def test_no_global_device_fwid_or_hashinfo_fallback_is_consulted(self):
        with mock.patch('clickwheel.ipod.device_info.get_current_device', side_effect=AssertionError('global device')):
            plan = self.plan(display_rows={'alpha': {'artist': 'Capitalized New Artist', 'album': 'New album',
                'title': 'New title', 'comments': 'Saved comment', 'genre': 'Genre', 'year': 2026, 'bpm': 144}})
        verified_stock_signing_context(plan.rendered_database, GUID)
        parsed = parse_itunesdb(io.BytesIO(plan.rendered_database))
        alpha = next(row for row in get_ipod_tracks(parsed) if row['dbid'] == 101)
        albums = {row['AlbumIDforTrack']: row for row in parsed['mhla']}
        self.assertEqual(albums[alpha['albumID']]['Album (Used by Album Item)'], 'New album')
        self.assertEqual(albums[alpha['albumID']]['Artist (Used by Album Item)'], 'Capitalized New Artist')
        _root, datasets = _datasets(plan.rendered_database)
        artist_rows = _list(datasets[8])[1]
        matching = [row for row in artist_rows if _labels(row).get(300) == 'Capitalized New Artist']
        self.assertEqual(alpha['artistId'], struct.unpack_from('<I', matching[0], 0x10)[0])

    def test_classic_metadata_art_commit_preserves_version_and_verifies_signatures(self):
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('retained audio')):
            plan = self.plan(title_overrides={'alpha': 'Curated Alpha'})
            result = stock_ipod.commit_stock_ipod_sync(plan)
        data = self.db.read_bytes(); verified_stock_signing_context(data, GUID)
        self.assertEqual(struct.unpack_from('<IH', data, 0x10)[0], 117)
        self.assertEqual(struct.unpack_from('<H', data, 0x30)[0], 1)
        self.assertEqual(data[0xD0:0xD4], b'keep')
        self.assertEqual(result.copied_tracks, 0)
        self.assertFalse((self.fixture.target/'iPod_Control/Music').exists())

    def test_incremental_new_audio_keeps_old_ids_and_browse_indexes_are_positions(self):
        self.fixture.add_new_wave(); self.fixture.playlist.keys=['beta','new','alpha']
        before = self.snapshot
        plan = self.plan(incremental=True,title_overrides={'alpha':'ZZZ updated title'})
        self.assertEqual(len(plan.copies), 1)
        parsed = parse_itunesdb(io.BytesIO(plan.rendered_database)); tracks = get_ipod_tracks(parsed)
        self.assertEqual({row['dbid']: row['trackID'] for row in tracks if row['dbid'] in {101,102,103}},
                         {row.dbid: row.track_id for row in before.tracks})
        _root, datasets = _datasets(plan.rendered_database)
        master = next(row for row in _list(datasets[2])[1] if row[0x14])
        indices = [row for row in _mhods(master)[0] if struct.unpack_from('<I', row, 12)[0] == 52]
        for row in indices:
            count = struct.unpack_from('<I', row, 28)[0]
            positions = struct.unpack_from(f'<{count}I', row, 72)
            self.assertEqual(sorted(positions), list(range(len(tracks))))
            if struct.unpack_from('<I',row,24)[0]==3:
                self.assertEqual(list(positions),sorted(range(len(tracks)),key=lambda index:tracks[index]['title'].casefold()))
        verified_stock_signing_context(plan.rendered_database, GUID)

    def test_pending_playback_is_merged_once_and_final_signed_buffer_is_valid(self):
        playback=(b'mhdp'+struct.pack('<III',96,28,3)+bytes(80)
                  +struct.pack('<IIIIIII',2,0,0,0,0,0,0)+bytes(56))
        counts=self.db.parent/'Play Counts';counts.write_bytes(playback)
        self.snapshot=self.read_snapshot();plan=self.plan(title_overrides={'alpha':'New title'})
        stock_ipod.commit_stock_ipod_sync(plan)
        data=self.db.read_bytes();verified_stock_signing_context(data,GUID)
        alpha=next(row for row in get_ipod_tracks(parse_itunesdb(io.BytesIO(data)))if row['dbid']==101)
        self.assertEqual(alpha['playCount'],5);self.assertFalse(counts.exists())

    def set_gapped_playlist_positions(self):
        data=self.db.read_bytes();root,datasets=_datasets(data)
        for kind in (2,3):
            playlists=[]
            for row in _list(datasets[kind])[1]:
                children,offset=_mhods(row);count=struct.unpack_from('<I',row,0x10)[0]
                native=bytearray(row);position=offset
                for index in range(count):
                    size=struct.unpack_from('<I',native,position+8)[0]
                    item=bytes(native[position:position+size]);item_children,child_offset=_mhods(item)
                    cursor=position+struct.unpack_from('<I',item,4)[0]
                    for child in item_children:
                        if struct.unpack_from('<I',child,12)[0]==100:
                            struct.pack_into('<I',native,cursor+struct.unpack_from('<I',child,4)[0],3584+index*5)
                        cursor+=len(child)
                    position+=size
                playlists.append(bytes(native))
            datasets[kind]=_rebuilt_dataset(datasets[kind],datasets[kind],playlists)
        data=bytearray(root)+b''.join(datasets.values())
        self.db.write_bytes(sign_stock_database(bytes(data),self.context));self.snapshot=self.read_snapshot()
        return datasets

    def test_gapped_native_positions_stay_exact_for_metadata_and_normalize_whole_changed_playlist(self):
        before=self.set_gapped_playlist_positions()
        metadata=self.plan(title_overrides={'alpha':'New metadata'})
        _root,after=_datasets(metadata.rendered_database)
        for kind in (2,3):
            for old,new in zip(_list(before[kind])[1],_list(after[kind])[1],strict=True):
                self.assertEqual(old[_mhods(old)[1]:],new[_mhods(new)[1]:])
        self.fixture.add_new_wave();self.fixture.playlist.keys=['beta','new','alpha']
        added=self.plan(incremental=True);_root,after=_datasets(added.rendered_database)
        for kind in (2,3):
            for playlist in _list(after[kind])[1]:
                _children,offset=_mhods(playlist);values=[]
                for _ in range(struct.unpack_from('<I',playlist,0x10)[0]):
                    size=struct.unpack_from('<I',playlist,offset+8)[0]
                    values.extend(struct.unpack_from('<I',child,struct.unpack_from('<I',child,4)[0])[0]
                        for child in _mhods(playlist[offset:offset+size])[0]
                        if struct.unpack_from('<I',child,12)[0]==100)
                    offset+=size
                if playlist[0x14] or struct.unpack_from('<Q',playlist,0x1C)[0]==901:
                    self.assertEqual(values,list(range(len(values))))
                else:self.assertEqual(values,[3584+index*5 for index in range(len(values))])

    def test_normal_new_audio_export_on_verified_classic_signs_and_preserves_foreign_catalog(self):
        from soundbox.library import Playlist
        self.fixture.add_new_wave()
        selected=Playlist('New Playlist',[],['new'],pid='new-playlist')
        before=self.db.read_bytes();_root,datasets=_datasets(before)
        plan=stock_ipod.plan_stock_ipod_sync(self.fixture.library,[selected],self.fixture.target,
            model_number='',filesystem='fat32',firewire_guid=GUID,usb_family='iPod Classic')
        self.assertEqual(len(plan.copies),1);self.assertFalse(plan.rewrites or plan.media_deletes)
        verified_stock_signing_context(plan.rendered_database,GUID)
        _newroot,after=_datasets(plan.rendered_database)
        oldrows={struct.unpack_from('<Q',row,0x70)[0]:row for row in _list(datasets[1])[1]}
        newrows={struct.unpack_from('<Q',row,0x70)[0]:row for row in _list(after[1])[1]}
        for dbid,row in oldrows.items():self.assertEqual(row,newrows[dbid])
        result=stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(result.copied_tracks,1)
        verified_stock_signing_context(self.db.read_bytes(),GUID)

    def test_opaque_root_dataset_and_track_children_survive_exactly(self):
        data = self.db.read_bytes(); root, datasets = _datasets(data)
        tracks = _list(datasets[1])[1]; opaque = struct.pack('<4sIIIII', b'mhod', 24, 32, 0x7777, 42, 99) + b'opaque!!'
        altered = []
        for row in tracks:
            header = bytearray(row[:struct.unpack_from('<I', row, 4)[0]])
            struct.pack_into('<II', header, 8, len(row)+len(opaque), struct.unpack_from('<I', row, 12)[0]+1)
            altered.append(bytes(header)+row[len(header):]+opaque)
        datasets[1] = _rebuilt_dataset(datasets[1], datasets[1], altered)
        unknown = bytearray(96+92); unknown[:4]=b'mhsd'
        struct.pack_into('<III', unknown, 4, 96, len(unknown), 13)
        unknown[96:100]=b'mhlt'; struct.pack_into('<II', unknown, 100, 92, 0)
        unknown[32:40]=b'foreign!'; datasets[13]=bytes(unknown)
        raw=bytearray(root)+b''.join(datasets.values());struct.pack_into('<I',raw,8,len(raw));struct.pack_into('<I',raw,20,len(datasets))
        self.db.write_bytes(sign_stock_database(bytes(raw),self.context));self.snapshot=self.read_snapshot()
        plan=self.plan(title_overrides={'alpha':'Curated Alpha'})
        _newroot,after=_datasets(plan.rendered_database)
        self.assertEqual(after[13],bytes(unknown));self.assertEqual(list(after),list(datasets))
        for row in _list(after[1])[1]:self.assertIn(opaque,_mhods(row)[0])

    def test_modern_unhashed_video_needs_no_guid_and_keeps_version(self):
        raw=bytearray(self.db.read_bytes());struct.pack_into('<H',raw,0x30,0)
        raw[0x58:0x6C]=bytes(20);raw[0x72:0xA0]=bytes(46);self.db.write_bytes(raw)
        self.snapshot=read_stock_ipod_snapshot(self.fixture.target,filesystem='fat32',usb_family='iPod Video',
            native_capture=capture_stock_ipod_native(self.fixture.target,include_artwork=True))
        self.assertEqual(self.snapshot.stock_format.database_profile,MODERN_VIDEO_PROFILE)
        self.assertFalse(self.snapshot.stock_format.requires_fwid)
        plan=stock_ipod.plan_stock_ipod_catalog_update(self.fixture.library,[self.fixture.playlist],self.fixture.target,
            snapshot=self.snapshot,model_number='',filesystem='fat32',usb_family='iPod Video',update_metadata=True,
            title_overrides={'alpha':'Video title'})
        self.assertEqual(struct.unpack_from('<I',plan.rendered_database,0x10)[0],117)
        self.assertEqual(struct.unpack_from('<H',plan.rendered_database,0x30)[0],0)

    def test_immutable_modern_video_capture_remains_supported_and_unchanged(self):
        from scripts.check_fixtures import ipod_capture
        from soundbox.stock_ipod_format import stock_renderer_capabilities
        path=ipod_capture()/'device/iPod_Control/iTunes/iTunesDB'
        original=path.read_bytes();fmt=inspect_stock_write_format(original,usb_family='iPod Video')
        self.assertTrue(fmt.writable);self.assertFalse(fmt.requires_fwid)
        self.assertEqual(fmt.database_version,117)
        parsed=parse_itunesdb(io.BytesIO(original));tracks=get_ipod_tracks(parsed)
        infos=[stock_ipod._existing_track_info(row)for row in tracks]
        infos[0].title='Generated immutable Video compatibility proof'
        ids={row['trackID']:row['dbid']for row in tracks}
        master=next(row for row in parsed['mhlp']if row.get('isMaster'))
        output,checked=stock_ipod._render_database(original,infos,
            [stock_ipod._playlist_info(row,ids)for row in parsed['mhlp']if not row.get('isMaster')],
            [stock_ipod._playlist_info(row,ids)for row in parsed.get('mhlp_smart')or[]],
            list(parsed.get('mhlp_smart')or[]),master['Title'],stock_renderer_capabilities(fmt),
            catalog_only=True,write_format=fmt,metadata_fields={tracks[0]['dbid']:('title',)})
        self.assertEqual(struct.unpack_from('<I',output,0x10)[0],117)
        self.assertEqual(struct.unpack_from('<H',output,0x30)[0],0)
        self.assertEqual(get_ipod_tracks(checked)[0]['title'],'Generated immutable Video compatibility proof')
        _root,before=_datasets(original);_newroot,after=_datasets(output)
        self.assertEqual(list(before),list(after))
        oldrows=_list(before[1])[1];newrows=_list(after[1])[1]
        self.assertEqual(oldrows[1:],newrows[1:])
        self.assertEqual(path.read_bytes(),original)

    def test_unhashed_video_opaque_hash_bytes_are_preserved_without_new_guid_requirement(self):
        raw=bytearray(self.db.read_bytes());struct.pack_into('<H',raw,0x30,0)
        raw[0x58:0x6C]=b'opaque-unused-hash!!';raw[0x72:0xA0]=b'not-a-signature'+bytes(31)
        self.db.write_bytes(raw)
        snap=read_stock_ipod_snapshot(self.fixture.target,filesystem='fat32',usb_family='iPod Video',
            native_capture=capture_stock_ipod_native(self.fixture.target,include_artwork=True))
        self.assertTrue(snap.stock_format.writable)
        plan=stock_ipod.plan_stock_ipod_catalog_update(self.fixture.library,[self.fixture.playlist],self.fixture.target,
            snapshot=snap,model_number='',filesystem='fat32',update_metadata=True,
            title_overrides={'alpha':'Video preserved header'})
        self.assertEqual(plan.rendered_database[0x58:0x6C],raw[0x58:0x6C])
        self.assertEqual(plan.rendered_database[0x72:0xA0],raw[0x72:0xA0])


if __name__=='__main__':unittest.main()
