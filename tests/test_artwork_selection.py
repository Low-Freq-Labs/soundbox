"""Selected artwork owners fence export intent without GUI image/filesystem work."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from soundbox.artwork import ArtCache, ArtResult


def image(color, *, progressive=False):
    out=BytesIO();Image.new('RGB',(12,12),color).save(out,'JPEG',progressive=progressive)
    return out.getvalue()


class ArtworkSelectionOwners(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        self.root=Path(area.name);self.cache=ArtCache(self.root/'art')

    def store(self, track, color, *, source='https://soundcloud.com/artist/song'):
        self.cache.store_ok(track,ArtResult(image(color),'image/jpeg',1.0,source,'Same title'))

    def test_same_metadata_byte_replacement_changes_selected_owner(self):
        self.store('selected','red');token=self.cache.selection_token(['selected'])
        before=dict(self.cache.index['selected'])
        self.store('selected','blue')
        self.assertEqual(self.cache.index['selected'],before)
        self.assertFalse(self.cache.selection_current(token))

    def test_unrelated_cover_and_unrelated_exclusion_preserve_selected_owner(self):
        self.store('selected','red');token=self.cache.selection_token(['selected'])
        self.store('unrelated','blue')
        self.cache.exclude_source('https://soundcloud.com/another-account/song')
        self.assertTrue(self.cache.selection_current(token))
        self.cache.exclude_source('https://soundcloud.com/artist/song')
        self.assertFalse(self.cache.selection_current(token))
        token=self.cache.selection_token(['selected']);self.cache.allow_source('artist')
        self.assertFalse(self.cache.selection_current(token))

    def test_assigned_default_removal_and_edge_pool_change_invalidate(self):
        paths=[]
        for color in ('red','blue'):
            path=self.root/(color+'.jpg');path.write_bytes(image(color));paths.append(path)
        self.cache.import_default_photos(paths);self.cache.configure_default_tracks(['selected','other'])
        token=self.cache.selection_token(['selected'])
        assigned=self.cache._default_assignments['selected']
        self.cache.remove_default_photos([assigned])
        self.assertFalse(self.cache.selection_current(token))
        edge=self.cache.selection_token(['outside-configured-library'])
        self.cache.remove_default_photos(list(self.cache._defaults['photos']))
        self.assertFalse(self.cache.selection_current(edge))

    def test_same_extension_normalization_replaces_only_rewritten_owner(self):
        self.store('unchanged','blue')
        (self.cache.root/'selected.jpg').write_bytes(image('red',progressive=True))
        self.cache.index['selected']={'status':'ok','ext':'jpg'}
        self.cache._save()
        selected=self.cache.selection_token(['selected']);untouched=self.cache.selection_token(['unchanged'])
        self.assertEqual(self.cache.normalize_all(),(2,1))
        self.assertFalse(self.cache.selection_current(selected))
        self.assertTrue(self.cache.selection_current(untouched))

    def test_failed_image_or_policy_publication_retains_original_token(self):
        self.store('selected','red');token=self.cache.selection_token(['selected'])
        for action in (lambda:self.store('selected','blue'),
                       lambda:self.cache.exclude_source('https://soundcloud.com/artist/song')):
            with mock.patch.object(self.cache._area,'publish',side_effect=OSError('generated failure')):
                with self.assertRaises(OSError):action()
            self.assertTrue(self.cache.selection_current(token))

    def test_restore_or_unknown_other_cache_publication_refuses_old_owners(self):
        self.store('selected','red');token=self.cache.selection_token(['selected'])
        other=ArtCache(self.cache.root);other.store_none('unrelated')
        self.assertFalse(self.cache.selection_current(token))
        self.cache.store_none('freshen')
        token=self.cache.selection_token(['selected']);self.cache._area.generation+=1
        self.assertFalse(self.cache.selection_current(token))

    def test_selection_api_never_reads_files_images_hashes_or_walks_track_index(self):
        self.store('selected','red')
        class SelectedLookupsOnly(dict):
            def __iter__(self):raise AssertionError('walked full track index')
            def items(self):raise AssertionError('walked full track index')
            def keys(self):raise AssertionError('walked full track index')
        self.cache.index=SelectedLookupsOnly(self.cache.index)
        with mock.patch.object(Path,'read_bytes',side_effect=AssertionError('image read')), \
                mock.patch.object(Path,'read_text',side_effect=AssertionError('JSON read')), \
                mock.patch.object(Path,'stat',side_effect=AssertionError('filesystem probe')), \
                mock.patch.object(json,'loads',side_effect=AssertionError('JSON parse')), \
                mock.patch('soundbox.artwork.hashlib.sha256',side_effect=AssertionError('hash')):
            token=self.cache.selection_token(['selected'])
            self.assertTrue(self.cache.selection_current(token))

    def test_split_indexes_once_and_shares_one_large_fallback_pool(self):
        self.cache._defaults['photos']={str(i):{'ext':'jpg'} for i in range(512)}
        whole=self.cache.selection_token(f'edge-{i}' for i in range(1024))
        with mock.patch.object(self.cache,'selection_token',side_effect=AssertionError('Per-track token recapture')), \
             mock.patch.object(Path,'read_bytes',side_effect=AssertionError('Image read')), \
             mock.patch.object(Path,'stat',side_effect=AssertionError('Filesystem read')):
            parts=self.cache.split_selection(whole)
        self.assertEqual(len(parts),1024)
        for tid,part in parts.items():
            self.assertEqual(part._rows[0][0],tid)
            self.assertEqual(len(part._rows),1)
            self.assertIs(part._edge_pool,whole._edge_pool)
            self.assertEqual(part._edge_seed,whole._edge_seed)

    def test_split_cover_photo_and_fallback_keep_only_their_actual_dependencies(self):
        self.store('cover','red')
        photo=self.root/'photo.jpg';photo.write_bytes(image('blue'))
        self.cache.import_default_photos([photo]);self.cache.configure_default_tracks(['assigned'])
        whole=self.cache.selection_token(['cover','assigned','edge'])
        parts=self.cache.split_selection(whole)
        self.assertTrue(all(self.cache.selection_current(part) for part in parts.values()))
        self.assertEqual(parts['cover']._edge_pool,())
        self.assertEqual(parts['assigned']._edge_pool,())
        self.assertIs(parts['edge']._edge_pool,whole._edge_pool)
        self.cache.remove_default_photos(list(self.cache._defaults['photos']))
        self.assertTrue(self.cache.selection_current(parts['cover']))
        self.assertFalse(self.cache.selection_current(parts['assigned']))
        self.assertFalse(self.cache.selection_current(parts['edge']))

    def test_split_selected_currentness_is_independent_of_other_tracks_and_policies(self):
        self.store('one','red');self.store('two','blue',source='https://soundcloud.com/other/song')
        parts=self.cache.split_selection(self.cache.selection_token(['one','two']))
        self.store('unrelated','green')
        self.assertTrue(all(self.cache.selection_current(part) for part in parts.values()))
        self.cache.exclude_source('https://soundcloud.com/artist/song')
        self.assertFalse(self.cache.selection_current(parts['one']))
        self.assertTrue(self.cache.selection_current(parts['two']))
        self.store('two','red',source='https://soundcloud.com/other/song')
        self.assertFalse(self.cache.selection_current(parts['two']))

    def test_split_rejects_other_cache_restored_or_unknown_generation(self):
        self.store('selected','red');whole=self.cache.selection_token(['selected'])
        other=ArtCache(self.root/'other')
        with self.assertRaises(ValueError):other.split_selection(whole)
        publisher=ArtCache(self.cache.root);publisher.store_none('unrelated')
        with self.assertRaises(ValueError):self.cache.split_selection(whole)
        self.cache.store_none('freshen');whole=self.cache.selection_token(['selected'])
        self.cache._area.generation+=1
        with self.assertRaises(ValueError):self.cache.split_selection(whole)

    def test_indexed_fallback_checks_compare_unchanged_pool_only_once(self):
        class CountedPool(dict):
            reads=0
            def items(self):
                self.reads+=1
                return super().items()
        pool=CountedPool({str(i):{'ext':'jpg'} for i in range(512)})
        self.cache._defaults['photos']=pool
        whole=self.cache.selection_token(f'edge-{i}' for i in range(1024))
        parts=self.cache.split_selection(whole);pool.reads=0
        self.assertTrue(all(self.cache.selection_current(token) for token in parts.values()))
        self.assertEqual(pool.reads,1)
        replacement=CountedPool(pool)
        self.cache._defaults['photos']=replacement
        self.assertTrue(all(self.cache.selection_current(token) for token in parts.values()))
        self.assertEqual(replacement.reads,1)
        self.cache._defaults['seed']='changed-seed'
        self.assertFalse(self.cache.selection_current(parts['edge-0']))
