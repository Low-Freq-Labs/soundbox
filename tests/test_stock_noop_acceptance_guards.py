"""Accepted omission-only generations cannot hide later real stock changes."""
from dataclasses import replace
from io import BytesIO
import os
import unittest
import wave

from PIL import Image

from desktop.export_dialog import captured_ipod_noop_result
from soundbox import stock_ipod
from soundbox.artwork import ArtCache, ArtResult
from soundbox.library import Track
from soundbox.stock_ipod_catalog_state import StockCatalogWitnessStore
from tests import test_export_playlist_baseline as fixtures


def write_wave(path, width=3):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(width)
        stream.setframerate(48000 if width == 3 else 44100)
        stream.writeframes(bytes(width * 2 * 441))


def cover(color):
    image=BytesIO();Image.new('RGB',(64,64),color).save(image,'JPEG')
    return ArtResult(image.getvalue(),'image/jpeg',1.,'file:///generated/manual.jpg','Manual')


class StockNoopAcceptanceGuards(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.ActualExportPlaylistBaseline();self.case.setUp()
        self.addCleanup(self.case.doCleanups);self.f=self.case.f
        self.f.playlist.keys[:]=['old']
        self.art=ArtCache((self.f.base/'no-op-state/art').resolve()).for_library(self.f.library)
        self.art.store_ok('old',cover('green'))
        self.store=StockCatalogWitnessStore(self.f.base/'no-op-witness')
        self.omissions=(self.f.base/'negative-observations').resolve()
        self.first=stock_ipod.commit_stock_ipod_sync(self.plan(None))
        self.store.save(self.first.catalog_witness)

    def rows(self):
        return {key:dict(id=key,title=track.name,artist=track.artist,album='',genre='',
                         comments='',year=0,bpm=0)
                for key,track in self.f.library.tracks_by_id.items()}

    def plan(self,witness):
        return stock_ipod.plan_stock_ipod_sync(self.f.library,[self.f.playlist],self.f.ipod,
            model_number='MA446',filesystem='fat32',usb_family='iPod Video',identity_key=self.case.key,
            shadow_store=self.case.shadow,display_rows=self.rows(),catalog_witness=witness,
            omission_cache_root=self.omissions,art_cache=self.art,stock_art_cache=self.f.stock_art_cache)

    def status(self,witness):
        native=stock_ipod.capture_stock_ipod_native(self.f.ipod,include_artwork=True)
        return stock_ipod.inspect_stock_ipod_sync_status(self.f.library,self.f.ipod,
            identity_key=self.case.key,model_number='MA446',serial_number='',shadow_store=self.case.shadow,
            catalog_witness=witness,display_rows=self.rows(),art_cache=self.art,native_capture=native)

    def native_snapshot(self):
        return {str(path.relative_to(self.f.ipod)):
                (path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns) if path.is_file() else None
                for path in self.f.ipod.rglob('*')}

    def pending_omission(self):
        self.before_shadow=self.case.shadow.load(self.case.key)
        write_wave(self.f.new_source)
        self.f.playlist.keys.append('new')
        self.assertEqual(self.status(self.first.catalog_witness).state,'local_changes')
        before=self.native_snapshot();plan=self.plan(self.first.catalog_witness)
        self.assertFalse(plan.requires_commit)
        self.assertEqual([row.track_id for row in plan.omissions],['new'])
        self.assertEqual(self.case.shadow.load(self.case.key),self.before_shadow)
        self.assertEqual(self.status(self.first.catalog_witness).state,'local_changes')
        self.assertEqual(self.native_snapshot(),before)
        return plan,before

    def accepted_omission(self):
        plan,before=self.pending_omission()
        result=captured_ipod_noop_result(plan,self.store)
        self.assertIsNotNone(result.catalog_witness)
        self.assertEqual(self.native_snapshot(),before)
        self.assertEqual(self.status(result.catalog_witness).state,'up_to_date')
        self.assertEqual(self.status(result.catalog_witness).local_changes,0)
        current=self.case.shadow.load(self.case.key)
        self.assertEqual(current.generation,self.before_shadow.generation)
        self.assertEqual(current.committed_at,self.before_shadow.committed_at)
        self.assertEqual(current.database_sha256,self.before_shadow.database_sha256)
        self.assertEqual(current.artwork_database_sha256,self.before_shadow.artwork_database_sha256)
        return plan,result,before

    def test_omission_only_addition_is_clean_after_acceptance_but_later_metadata_and_source_edits_are_dirty(self):
        _plan,result,_before=self.accepted_omission()
        track=self.f.library.tracks_by_id['old'];previous=track.name
        track.name='Later musical title'
        status=self.status(result.catalog_witness)
        self.assertEqual(status.state,'local_changes')
        self.assertIn(('song_details',1),status.change_summary)
        track.name=previous
        self.assertEqual(self.status(result.catalog_witness).state,'up_to_date')
        info=self.f.existing_source.stat()
        os.utime(self.f.existing_source,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
        status=self.status(result.catalog_witness)
        self.assertEqual(status.state,'local_changes')
        self.assertIn(('source',1),status.change_summary)

    def test_later_eligible_source_addition_still_requires_a_real_copy(self):
        _plan,result,before=self.accepted_omission()
        source=(self.f.sources/'later eligible.wav').resolve();write_wave(source,width=2)
        self.f.library.tracks_by_id['eligible']=Track('eligible','Eligible','Artist',source.as_uri(),
            source_native_id='native-eligible',source_uuid='00000000-0000-4000-8000-000000000777',
            source_native_path=str(source))
        self.f.playlist.keys.append('eligible');self.art=self.art.for_library(self.f.library)
        self.assertEqual(self.status(result.catalog_witness).state,'local_changes')
        plan=self.plan(result.catalog_witness)
        self.assertTrue(plan.requires_commit)
        self.assertEqual([row.source for row in plan.copies],[source])
        self.assertEqual(self.native_snapshot(),before)

    def test_later_manual_cover_on_exported_song_stays_dirty(self):
        _plan,result,before=self.accepted_omission()
        self.art.store_ok('old',cover('blue'))
        status=self.status(result.catalog_witness)
        self.assertEqual(status.state,'local_changes')
        self.assertIn(('artwork',1),status.change_summary)
        self.assertTrue(self.plan(result.catalog_witness).requires_commit)
        self.assertEqual(self.native_snapshot(),before)

    def test_same_bytes_source_replacement_after_planning_cannot_renew_old_epoch(self):
        plan,before=self.pending_omission()
        source=self.f.existing_source;info=source.stat();replacement=source.with_name('replacement.wav')
        replacement.write_bytes(source.read_bytes());os.utime(replacement,ns=(info.st_atime_ns,info.st_mtime_ns))
        os.replace(replacement,source)
        witness_before=self.store.path_for(self.case.key).read_bytes()
        with self.assertRaises((stock_ipod.StockIpodError,ValueError)):
            captured_ipod_noop_result(plan,self.store)
        self.assertEqual(self.case.shadow.load(self.case.key),self.before_shadow)
        self.assertEqual(self.store.path_for(self.case.key).read_bytes(),witness_before)
        self.assertEqual(self.native_snapshot(),before)

    def test_native_catalog_change_after_planning_refuses_baseline_acceptance(self):
        plan,_before=self.pending_omission()
        path=self.f.ipod/'iPod_Control/iTunes/iTunesDB'
        with path.open('ab') as handle:handle.write(b'generated external catalog change')
        changed=self.native_snapshot();witness_before=self.store.path_for(self.case.key).read_bytes()
        with self.assertRaises((stock_ipod.StockIpodError,ValueError)):
            captured_ipod_noop_result(plan,self.store)
        self.assertEqual(self.case.shadow.load(self.case.key),self.before_shadow)
        self.assertEqual(self.store.path_for(self.case.key).read_bytes(),witness_before)
        self.assertEqual(self.native_snapshot(),changed)

    def test_omitted_source_becoming_eligible_after_plan_refuses_acceptance_then_retry_copies_it(self):
        plan,before=self.pending_omission()
        witness_before=self.store.path_for(self.case.key).read_bytes()
        write_wave(self.f.new_source,width=2)
        with self.assertRaises((stock_ipod.StockIpodError,ValueError)):
            captured_ipod_noop_result(plan,self.store)
        self.assertEqual(self.case.shadow.load(self.case.key),self.before_shadow)
        self.assertEqual(self.store.path_for(self.case.key).read_bytes(),witness_before)
        self.assertEqual(self.native_snapshot(),before)
        retry=self.plan(self.first.catalog_witness)
        self.assertTrue(retry.requires_commit)
        self.assertEqual(retry.omissions,())
        self.assertEqual([row.source for row in retry.copies],[self.f.new_source.resolve()])
        self.assertEqual(self.native_snapshot(),before)

    def test_competing_mac_shadow_generation_is_preserved(self):
        plan,before=self.pending_omission()
        competing=replace(self.before_shadow,generation=self.before_shadow.generation+1)
        self.case.shadow.save(competing)
        witness_before=self.store.path_for(self.case.key).read_bytes()
        with self.assertRaises((stock_ipod.StockIpodError,ValueError)):
            captured_ipod_noop_result(plan,self.store)
        self.assertEqual(self.case.shadow.load(self.case.key),competing)
        self.assertEqual(self.store.path_for(self.case.key).read_bytes(),witness_before)
        self.assertEqual(self.native_snapshot(),before)
