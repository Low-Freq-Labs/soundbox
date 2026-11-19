"""Curated source epochs remain paired with the exact captured RGB565 input."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from soundbox.operation_control import OperationCancelled, operation_scope
from soundbox.stock_artwork import StockArtworkCache, StockArtworkError


class StockArtworkSourceIdentity(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name).resolve();self.source=self.root/'curated.png'
        self.red=self.image('red.png',(200,20,20));self.blue=self.image('blue.png',(20,20,200))
        self.assertEqual(len(self.red),len(self.blue))
        self.source.write_bytes(self.red);self.cache=StockArtworkCache(self.root/'cache')

    def image(self,name,color):
        path=self.root/name;Image.new('RGB',(64,64),color).save(path,'PNG',compress_level=0)
        return path.read_bytes()

    def replace(self,data):
        before=self.source.stat();path=self.root/'replacement.png';path.write_bytes(data)
        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns));os.replace(path,self.source)

    def test_equal_size_mtime_atomic_or_inplace_replacement_cannot_reuse_old_pixels(self):
        first=self.cache.prepare(self.source);original=self.source.stat()
        self.replace(self.blue)
        self.assertEqual(self.source.stat().st_mtime_ns,original.st_mtime_ns)
        self.assertNotEqual(self.source.stat().st_ino,original.st_ino)
        second=self.cache.prepare(self.source)
        self.assertEqual(second.source_sha256,hashlib.sha256(self.blue).hexdigest())
        self.assertNotEqual(second.cache_key,first.cache_key)
        self.assertNotEqual(second.payloads[0].sha256,first.payloads[0].sha256)
        epoch=self.source.stat();self.source.write_bytes(self.red)
        os.utime(self.source,ns=(epoch.st_atime_ns,epoch.st_mtime_ns))
        self.assertEqual(self.source.stat().st_ino,epoch.st_ino)
        self.assertEqual(self.cache.prepare(self.source).source_sha256,first.source_sha256)

    def test_unchanged_reopened_index_reuses_payloads_without_source_content_read(self):
        first=self.cache.prepare(self.source);cache=StockArtworkCache(self.cache.root)
        opened=Path.open
        def guarded(path,*args,**kwargs):
            if path==self.source:raise AssertionError('Warm source was reread')
            return opened(path,*args,**kwargs)
        with mock.patch.object(Path,'open',guarded):
            self.assertEqual(cache.prepare(self.source),first)

    def test_replacement_after_descriptor_open_retries_once_and_hashes_encoded_buffer(self):
        opened=Path.open;calls=[]
        def changed(path,*args,**kwargs):
            handle=opened(path,*args,**kwargs)
            if path==self.source:
                calls.append(path)
                if len(calls)==1:self.replace(self.blue)
            return handle
        with mock.patch.object(Path,'open',changed):result=self.cache.prepare(self.source)
        self.assertEqual(len(calls),2)
        self.assertEqual(result.source_sha256,hashlib.sha256(self.blue).hexdigest())
        self.assertEqual(result.payloads,self.cache.prepare(self.source).payloads)
        self.assertEqual(self.source.read_bytes(),self.blue)

    def test_twice_unstable_capture_refuses_before_encoding_or_remembering(self):
        opened=Path.open;calls=[]
        def changed(path,*args,**kwargs):
            handle=opened(path,*args,**kwargs)
            if path==self.source:
                calls.append(path);self.replace(self.blue if len(calls)%2 else self.red)
            return handle
        with mock.patch.object(Path,'open',changed),mock.patch(
                'clickwheel.ipod.artworkdb_writer.rgb565.convert_art_for_ipod',
                side_effect=AssertionError('Unstable pixels were encoded')):
            with self.assertRaisesRegex(StockArtworkError,'remained unstable'):
                self.cache.prepare(self.source)
        self.assertEqual(len(calls),2);self.assertEqual(self.cache._source_index,{})
        self.assertFalse(list(self.cache.root.rglob('manifest.json')))

    def test_legacy_source_index_is_not_authority_but_content_payloads_stay_reusable(self):
        first=self.cache.prepare(self.source);entry=json.loads(self.cache._source_index_path.read_bytes())
        for row in entry.values():
            for key in ('dev','ino','ctime_ns'):row.pop(key)
        self.cache._source_index_path.write_text(json.dumps(entry))
        cache=StockArtworkCache(self.cache.root);self.assertEqual(cache._source_index,{})
        with mock.patch('clickwheel.ipod.artworkdb_writer.rgb565.convert_art_for_ipod',
                        side_effect=AssertionError('Correct content-addressed pixels were rebuilt')):
            self.assertEqual(cache.prepare(self.source),first)
        self.assertTrue(all(key in cache._source_index[str(self.source)] for key in ('dev','ino','ctime_ns')))

    def test_late_warm_source_change_during_payload_validation_is_rechecked(self):
        self.cache.prepare(self.source);load=self.cache._load;changed=False
        def race(*args,**kwargs):
            nonlocal changed
            result=load(*args,**kwargs)
            if not changed:
                changed=True;self.replace(self.blue)
            return result
        with mock.patch.object(self.cache,'_load',side_effect=race):result=self.cache.prepare(self.source)
        self.assertEqual(result.source_sha256,hashlib.sha256(self.blue).hexdigest())

    def test_cancelled_cold_and_warm_preparation_never_publishes_or_rereads(self):
        for warm in (False,True):
            with self.subTest(warm=warm):
                if warm:self.cache.prepare(self.source)
                before=dict(self.cache._source_index);stop=threading.Event();stop.set()
                with operation_scope(stop),mock.patch.object(Path,'open',
                        side_effect=AssertionError('Cancelled artwork performed content IO')):
                    with self.assertRaises(OperationCancelled):self.cache.prepare(self.source)
                self.assertEqual(self.cache._source_index,before)

    def test_cancel_after_open_stops_before_encoding_and_index_publication(self):
        stop=threading.Event();opened=Path.open
        def cancelled(path,*args,**kwargs):
            handle=opened(path,*args,**kwargs)
            if path==self.source:stop.set()
            return handle
        with operation_scope(stop),mock.patch.object(Path,'open',cancelled),mock.patch(
                'clickwheel.ipod.artworkdb_writer.rgb565.convert_art_for_ipod',
                side_effect=AssertionError('Cancelled artwork encoded')):
            with self.assertRaises(OperationCancelled):self.cache.prepare(self.source)
        self.assertEqual(self.cache._source_index,{})
        self.assertFalse(list(self.cache.root.rglob('manifest.json')))
