"""Generated stock audio inventory keeps one record per canonical source."""
from dataclasses import replace
import io
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from PIL import Image

from clickwheel.ipod import get_ipod_tracks, read_ipod

from soundbox import stock_ipod
from soundbox.library import Library, Playlist, Track
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from soundbox.stock_artwork import read_stock_artwork_slots
from tests import test_stock_ipod as fixtures


class StockAudioReuse(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StockIpodSync();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        self.alternate=f.sources/'alternate.wav'
        shutil.copy2(f.existing_source,self.alternate)
        tracks={'old':f.library.tracks_by_id['old'],
                'alternate':Track('alternate','Alternate','Other',self.alternate.as_uri(),total_time=1)}
        self.playlist=Playlist('Distinct',[],['old','alternate'],pid='distinct')
        self.library=Library(tracks,[self.playlist],f.sources)
        self.store=StockIpodShadowStore(f.base/'shadow')
        self.options=dict(model_number='MA446',filesystem='msdos',usb_family='iPod Video',
                          identity_key='stock-ipod-v1:audio-reuse',shadow_store=self.store)

    def plan(self,library=None,selected=None,**options):
        return stock_ipod.plan_stock_ipod_sync(library or self.library,
            selected or [self.playlist],self.fixture.ipod,**{**self.options,**options})

    def seed(self,**options):
        plan=self.plan(**options)
        self.assertEqual(plan.final_track_count,2)
        self.assertEqual(len(plan.copies),1)
        stock_ipod.commit_stock_ipod_sync(plan)
        return plan

    def assert_reused(self,plan,first):
        self.assertEqual(plan.final_track_count,2)
        self.assertEqual(plan.reused_track_count,2)
        self.assertFalse(plan.copies)
        self.assertFalse(plan.rewrites)
        self.assertEqual(plan.source_device_paths,first.source_device_paths)
        for source in first.shadow_sources:
            self.assertEqual(plan.shadow_sources[source]['dbid'],first.shadow_sources[source]['dbid'])

    def reverse(self):
        playlist=replace(self.playlist,keys=list(reversed(self.playlist.keys)))
        return replace(self.library,playlists=[playlist]),[playlist]

    def test_exact_shadow_repeat_and_reorder_keep_both_records_without_audio_reads(self):
        first=self.seed();original=stock_ipod._sha256
        before=self.fixture._snapshot(self.fixture.ipod)
        def no_device_audio(path):
            if 'Music' in Path(path).parts:raise AssertionError('Retained audio was hashed')
            return original(path)
        with (mock.patch.object(stock_ipod,'acquire_source',side_effect=AssertionError('Retained source acquisition')),
              mock.patch.object(stock_ipod,'_technical_audio',side_effect=AssertionError('Retained source inspection')),
              mock.patch.object(stock_ipod,'_sha256',side_effect=no_device_audio)):
            repeated=self.plan();self.assert_reused(repeated,first)
            self.assertFalse(repeated.requires_commit)
            library,selected=self.reverse();reordered=self.plan(library,selected)
            self.assert_reused(reordered,first)
        self.assertEqual(self.fixture._snapshot(self.fixture.ipod),before)

    def test_missing_shadow_reconciles_all_equal_byte_records_even_when_reordered(self):
        first=self.seed()
        repeated=self.plan(shadow_store=None);self.assert_reused(repeated,first)
        self.assertFalse(repeated.requires_commit)
        library,selected=self.reverse()
        self.assert_reused(self.plan(library,selected,shadow_store=None),first)

    def test_invalid_shadow_falls_back_without_creating_another_equal_byte_record(self):
        first=self.seed()
        self.store.path_for(self.options['identity_key']).write_text('{invalid json')
        repeated=self.plan();self.assert_reused(repeated,first)
        self.assertFalse(repeated.shadow_fast_path)
        self.assertFalse(repeated.requires_commit)

    def test_partial_shadow_finds_the_other_existing_record(self):
        first=self.seed();saved=self.store.path_for(self.options['identity_key'])
        data=json.loads(saved.read_bytes())
        del data['sources'][str(self.alternate.resolve())]
        saved.write_text(json.dumps(data))
        library,selected=self.reverse()
        plan=self.plan(library,selected);self.assert_reused(plan,first)
        self.assertTrue(plan.shadow_fast_path)

    def test_new_equal_byte_source_cannot_steal_an_unselected_saved_source_record(self):
        first=self.seed();before={int(row['dbid']):row for row in get_ipod_tracks(read_ipod(self.fixture.ipod))}
        third=self.fixture.sources/'third.wav';shutil.copy2(self.alternate,third)
        track=Track('third','Third','Another Artist',third.as_uri(),total_time=1)
        playlist=Playlist('Additional',[],['third'],pid='additional')
        library=replace(self.library,tracks_by_id={**self.library.tracks_by_id,'third':track},
                        playlists=[self.playlist,playlist])
        plan=self.plan(library,[playlist])
        self.assertEqual(plan.final_track_count,3)
        self.assertEqual(len(plan.copies),1)
        self.assertFalse(plan.metadata_updates)
        self.assertNotIn(plan.source_device_paths[str(third.resolve())],first.source_device_paths.values())
        stock_ipod.commit_stock_ipod_sync(plan)
        after={int(row['dbid']):row for row in get_ipod_tracks(read_ipod(self.fixture.ipod))}
        # The renderer rebuilds numeric track/album indexes. Persistent DBIDs,
        # media locations, musical fields and playback history stay exact.
        stable=lambda row:{key:value for key,value in row.items() if key not in {'trackID','albumID'}}
        self.assertEqual({dbid:stable(after[dbid]) for dbid in before},
                         {dbid:stable(row) for dbid,row in before.items()})
        repeated=self.plan(library,[playlist])
        self.assertFalse(repeated.requires_commit)
        self.assertFalse(repeated.copies)
        self.assertEqual(repeated.final_track_count,3)

    def test_repeated_occurrences_across_playlists_share_the_same_source_record(self):
        one=Playlist('First',[],['old','old'],pid='first')
        two=Playlist('Second',[],['old'],pid='second')
        library=replace(self.library,playlists=[one,two])
        first=self.plan(library,[one,two]);self.assertFalse(first.copies)
        self.assertEqual(first.final_track_count,1)
        parsed=stock_ipod.parse_itunesdb(io.BytesIO(first.rendered_database))
        self.assertEqual([len(row['items']) for row in parsed['mhlp']
                          if row['Title'] in {'First','Second'}],[2,1])
        stock_ipod.commit_stock_ipod_sync(first)
        repeated=self.plan(library,[two,one])
        self.assertFalse(repeated.copies)
        self.assertEqual(repeated.final_track_count,1)

    def test_distinct_red_blue_covers_stay_with_their_source_records_after_reorder(self):
        f=self.fixture;images={}
        for key,color in [('old',(230,10,10)),('alternate',(10,10,230))]:
            path=f.base/(key+'.jpg');Image.new('RGB',(300,300),color).save(path,'JPEG')
            images[key]=path
        originals={path:hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in [f.existing_source,self.alternate,*images.values()]}
        options=dict(art_cache=fixtures._FixtureArtCache(images),stock_art_cache=f.stock_art_cache)
        first=self.seed(**options)
        cache=f.stock_art_cache.for_profile(first.stock_format.artwork_profile)
        expected={first.source_device_dbids[str(self.library.tracks_by_id[key].path.resolve())]:
                  cache.prepare(image) for key,image in images.items()}
        artwork=f.ipod/'iPod_Control/Artwork'
        before={path.name:path.read_bytes() for path in artwork.iterdir() if path.is_file()}

        def check_links_and_pixels():
            native={int(row['dbid']):row for row in get_ipod_tracks(read_ipod(f.ipod))}
            _raw,slots,_files=read_stock_artwork_slots(artwork)
            links=set()
            for dbid,prepared in expected.items():
                link=int(native[dbid]['mhiiLink']);links.add(link)
                slot=slots[link];self.assertEqual(slot.song_id,dbid)
                for payload in prepared.payloads:
                    with (artwork/f'F{payload.format_id}_1.ithmb').open('rb') as stream:
                        stream.seek(slot.offset(payload.format_id));actual=stream.read(payload.size)
                    self.assertEqual(actual,payload.path.read_bytes())
            self.assertEqual(len(links),2)

        check_links_and_pixels()
        repeated=self.plan(**options);self.assert_reused(repeated,first)
        self.assertFalse(repeated.requires_commit)
        library,selected=self.reverse();reordered=self.plan(library,selected,**options)
        self.assert_reused(reordered,first)
        self.assertEqual(reordered.summary()['artwork_pixel_bytes'],0)
        stock_ipod.commit_stock_ipod_sync(reordered)
        check_links_and_pixels()
        self.assertEqual({path.name:path.read_bytes() for path in artwork.iterdir() if path.is_file()},before)
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in originals},originals)

