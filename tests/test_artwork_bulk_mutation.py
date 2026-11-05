"""Atomic artist mutations preserve sequential curation and newer real covers."""
from copy import deepcopy
import io
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from soundbox import artwork,artencode,state_io


def image(color):
    out=io.BytesIO();Image.new('RGB',(24,24),color).save(out,'PNG');return out.getvalue()


class ArtworkBulkMutation(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        self.root=Path(area.name);self.counter=0
        self.result=artwork.ArtResult(image('orange'),'image/png',.987,'https://soundcloud.com/artist','Artist')

    def pair(self,*,avatars=True,count=40):
        base=self.root/str(self.counter);self.counter+=1;seed=base/'seed';art=seed/'art';art.mkdir(parents=True)
        ids=[f'track-{i:03d}' for i in range(count)];selected=ids[:min(8,count)]
        index={tid:dict(status='ok',ext='jpg',src='https://soundcloud.com/artist',conf=.95,origin='manual')
               if avatars and tid in selected else dict(status='none') for tid in ids}
        (art/'index.json').write_text(artwork.json.dumps(index))
        old=artencode.ensure_device_safe(image('blue'))
        for tid in selected:
            if avatars:(art/f'{tid}.jpg').write_bytes(old)
        cache=artwork.ArtCache(art);photos=[]
        for i,color in enumerate(('red','green','blue')):
            path=base/f'source-{i}.png';path.write_bytes(image(color));photos.append(path)
        cache.import_default_photos(photos);cache._defaults['seed']='fixed-bulk-fixture'
        cache.configure_default_tracks(ids)
        result=[]
        for name in ('sequential','bulk','naive'):
            dest=base/name;shutil.copytree(seed,dest)
            current=artwork.ArtCache(dest/'art');current.configure_default_tracks(ids);result.append(current)
        return (*result,selected,photos)

    @staticmethod
    def files(cache):
        return {str(path.relative_to(cache.root.parent)):path.read_bytes()
                for path in cache.root.parent.rglob('*') if path.is_file()}

    def test_avatar_store_normalizes_clones_and_publishes_once_with_exact_sequential_files(self):
        sequential,bulk,_naive,ids,sources=self.pair();original={p:p.read_bytes() for p in sources}
        for tid in ids:sequential.store_ok(tid,self.result)
        old_index=bulk.index
        with mock.patch.object(artencode,'ensure_device_safe',wraps=artencode.ensure_device_safe) as encoded, \
             mock.patch.object(bulk._area,'publish',wraps=bulk._area.publish) as published, \
             mock.patch.object(bulk,'_save',wraps=bulk._save) as saved, \
             mock.patch.object(bulk,'_rebuild_default_assignments',wraps=bulk._rebuild_default_assignments) as balanced, \
             mock.patch.object(artwork.copy,'deepcopy',wraps=deepcopy) as copied:
            self.assertEqual(bulk.store_many([*ids,ids[0]],self.result,avatar_only=True),ids)
        self.assertEqual(encoded.call_count,1);self.assertEqual(published.call_count,1)
        self.assertEqual(saved.call_count,1);self.assertEqual(balanced.call_count,1)
        self.assertEqual(sum(call.args[0] is old_index for call in copied.call_args_list),0)
        self.assertIs(bulk.index["track-039"],old_index["track-039"])
        self.assertEqual(self.files(bulk),self.files(sequential))
        self.assertEqual({p:p.read_bytes() for p in sources},original)

    def test_block_preserves_path_dependent_default_assignments_but_serializes_and_publishes_once(self):
        sequential,bulk,naive,ids,_sources=self.pair();ids=[ids[i] for i in (7,0,3,2,1,6,5,4)]
        for tid in ids:sequential.block(tid)
        for tid in ids:naive.index[tid]={'status':'blocked'}
        naive._rebuild_default_assignments(persist=False)
        self.assertNotEqual(naive._defaults['assignments'],sequential._defaults['assignments'])
        with mock.patch.object(bulk._area,'publish',wraps=bulk._area.publish) as published, \
             mock.patch.object(bulk,'_save_defaults',wraps=bulk._save_defaults) as defaults:
            self.assertEqual(bulk.block_many(ids,avatar_only=True),ids)
        self.assertEqual(published.call_count,1);self.assertEqual(defaults.call_count,1)
        self.assertEqual(bulk._default_assignments,sequential._default_assignments)
        self.assertEqual(self.files(bulk),self.files(sequential))

    def test_generic_store_changes_eligibility_in_input_order_and_keeps_exact_defaults(self):
        sequential,bulk,_naive,ids,_sources=self.pair(avatars=False)
        for tid in reversed(ids):sequential.store_ok(tid,self.result,origin='auto')
        self.assertEqual(bulk.store_many(reversed(ids),self.result,origin='auto'),list(reversed(ids)))
        self.assertEqual(self.files(bulk),self.files(sequential))

    def test_stale_avatar_group_skips_newer_real_cover_and_manual_block_from_other_cache(self):
        _seq,bulk,_naive,ids,_sources=self.pair()
        other=artwork.ArtCache(bulk.root);other.configure_default_tracks(bulk._default_track_ids)
        cover=artwork.ArtResult(image('pink'),'image/png',1,'https://soundcloud.com/artist/real-cover','Cover')
        other.store_ok(ids[0],cover);other.block(ids[1])
        protected=(bulk.root/f'{ids[0]}.jpg').read_bytes()
        self.assertEqual(bulk.store_many(ids[:3],self.result,avatar_only=True),[ids[2]])
        self.assertEqual((bulk.root/f'{ids[0]}.jpg').read_bytes(),protected)
        self.assertEqual(bulk.index[ids[1]],{'status':'blocked'})
        self.assertEqual(bulk.block_many(ids[:4],avatar_only=True),ids[2:4])
        self.assertEqual((bulk.root/f'{ids[0]}.jpg').read_bytes(),protected)
        self.assertEqual(bulk.index[ids[0]]['src'],cover.source_url)

    def test_fresh_uploader_policy_rejects_store_before_encode_and_quarantined_avatar_is_skipped(self):
        _seq,bulk,_naive,ids,_sources=self.pair()
        bulk.exclude_source(self.result.source_url);before=self.files(bulk)
        with mock.patch.object(artencode,'ensure_device_safe',side_effect=AssertionError('excluded image encoded')):
            with self.assertRaisesRegex(ValueError,'excluded'):bulk.store_many(ids,self.result)
        self.assertEqual(self.files(bulk),before)
        self.assertEqual(bulk.store_many(ids,self.result,avatar_only=True),[])
        self.assertEqual(bulk.block_many(ids,avatar_only=True),[])
        self.assertEqual(self.files(bulk),before)

    def test_mid_publication_failure_restores_exact_disk_and_original_namespace_owners(self):
        _seq,bulk,_naive,ids,_sources=self.pair();before=self.files(bulk)
        owners=(bulk.index,bulk._defaults,bulk._default_assignments);revision=bulk._area.art_revision
        original=state_io.os.replace;failed=False;target=bulk.root/f'{ids[2]}.jpg'
        def replace(source,destination):
            nonlocal failed
            if Path(destination)==target and not failed:failed=True;raise OSError('fixture disk failure')
            return original(source,destination)
        with mock.patch.object(state_io.os,'replace',side_effect=replace):
            with self.assertRaisesRegex(OSError,'fixture disk failure'):bulk.store_many(ids,self.result,avatar_only=True)
        self.assertTrue(failed);self.assertEqual(self.files(bulk),before)
        self.assertIs(bulk.index,owners[0]);self.assertIs(bulk._defaults,owners[1])
        self.assertIs(bulk._default_assignments,owners[2]);self.assertEqual(bulk._area.art_revision,revision)
        self.assertEqual(bulk.store_many(ids,self.result,avatar_only=True),ids)

    def test_mid_block_failure_restores_all_images_and_assignments(self):
        _seq,bulk,_naive,ids,_sources=self.pair();before=self.files(bulk)
        original=state_io._remove;failed=False;target=bulk.root/f'{ids[2]}.jpg'
        def remove(path):
            nonlocal failed
            if Path(path)==target and not failed:failed=True;raise OSError('fixture delete failure')
            return original(path)
        with mock.patch.object(state_io,'_remove',side_effect=remove):
            with self.assertRaisesRegex(OSError,'fixture delete failure'):bulk.block_many(ids,avatar_only=True)
        self.assertTrue(failed);self.assertEqual(self.files(bulk),before)

    def test_no_eligible_targets_does_not_publish_or_normalize(self):
        _seq,bulk,_naive,ids,_sources=self.pair(avatars=False)
        with mock.patch.object(bulk._area,'publish',side_effect=AssertionError('empty publish')), \
             mock.patch.object(artencode,'ensure_device_safe',side_effect=AssertionError('empty encode')):
            self.assertEqual(bulk.store_many(ids,self.result,avatar_only=True),[])
            self.assertEqual(bulk.block_many(ids,avatar_only=True),[])
            self.assertEqual(bulk.store_many([],self.result),[])

    def test_absent_default_manifest_and_last_eligible_song_match_single_store(self):
        sequential,bulk,_naive,ids,_sources=self.pair(avatars=False,count=1)
        sequential._defaults_index_path.unlink();bulk._defaults_index_path.unlink()
        sequential.store_ok(ids[0],self.result);bulk.store_many(ids,self.result)
        self.assertFalse(bulk._defaults_index_path.exists())
        self.assertEqual(self.files(bulk),self.files(sequential))

    def test_normalization_failure_preserves_existing_entry_and_nested_owner(self):
        root=self.root/'normalization';root.mkdir()
        (root/'legacy.png').write_bytes(image('blue'))
        (root/'index.json').write_text(artwork.json.dumps({
            'legacy':dict(status='ok',ext='png',extension={'nested':['keep']})}))
        cache=artwork.ArtCache(root);before=self.files(cache)
        index=cache.index;entry=index['legacy'];nested=entry['extension']
        with mock.patch.object(cache._area,'publish',side_effect=OSError('fixture normalize failure')):
            with self.assertRaisesRegex(OSError,'fixture normalize failure'):cache.normalize_all()
        self.assertIs(cache.index,index);self.assertIs(cache.index['legacy'],entry)
        self.assertIs(cache.index['legacy']['extension'],nested)
        self.assertEqual(entry['ext'],'png');self.assertEqual(self.files(cache),before)
        self.assertEqual(cache.normalize_all(),(1,1))
        self.assertEqual(cache.index['legacy']['ext'],'jpg')
        self.assertEqual(entry['ext'],'png')
        self.assertIs(cache.index['legacy']['extension'],nested)

    def test_unchanged_balance_keeps_unrelated_cover_and_photo_owners(self):
        _seq,cache,_naive,_ids,_sources=self.pair()
        old_index=cache.index;entries=dict(old_index)
        photos=dict(cache._defaults['photos']);before=self.files(cache)
        with mock.patch.object(artwork.copy,'deepcopy',wraps=deepcopy) as copied, \
                mock.patch.object(cache._area,'publish',side_effect=AssertionError('unchanged publish')):
            cache.configure_default_tracks(cache._default_track_ids)
        self.assertEqual(copied.call_count,0)
        self.assertEqual(self.files(cache),before)
        for key,entry in entries.items():self.assertIs(cache.index[key],entry)
        for key,entry in photos.items():self.assertIs(cache._defaults['photos'][key],entry)

    def test_failed_normalization_releases_serial_owner_before_another_cache_publishes(self):
        root=self.root/'concurrent';root.mkdir()
        original_image=image('blue');(root/'legacy.png').write_bytes(original_image)
        (root/'index.json').write_text(artwork.json.dumps({
            'legacy':dict(status='ok',ext='png',extension={'nested':['keep']})}))
        cache=artwork.ArtCache(root);other=artwork.ArtCache(root)
        original_entry=cache.index['legacy'];publish=cache._area.publish
        entered=threading.Event();release=threading.Event();errors=[]
        def failing_publish(*args,**kwargs):
            if not entered.is_set():
                entered.set()
                if not release.wait(2):raise AssertionError('fixture release missing')
                raise OSError('fixture publication failure')
            return publish(*args,**kwargs)
        def normalize():
            try:cache.normalize_all()
            except Exception as exc:errors.append(exc)
        def update():
            try:other.store_none('new')
            except Exception as exc:errors.append(exc)
        with mock.patch.object(cache._area,'publish',side_effect=failing_publish):
            first=threading.Thread(target=normalize);second=threading.Thread(target=update)
            first.start()
            try:
                self.assertTrue(entered.wait(2));second.start()
            finally:release.set()
            first.join(3);second.join(3)
            self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual([str(exc) for exc in errors],['fixture publication failure'])
        self.assertEqual(original_entry['ext'],'png')
        self.assertIs(cache.index['legacy'],original_entry)
        current=artwork.ArtCache(root)
        self.assertEqual(current.index['legacy'],original_entry)
        self.assertEqual(current.index['new'],{'status':'none'})
        self.assertEqual((root/'legacy.png').read_bytes(),original_image)
        self.assertFalse((root/'legacy.jpg').exists())


if __name__=='__main__':unittest.main()
