"""Disposable caches use exact existing writers across an owned engine process."""
from dataclasses import asdict, replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from soundbox import browser_cache, libcache, library_codec
from soundbox.cache_process import CacheError, CacheProcess
from soundbox.library import Library, LibraryTag, Playlist, TagCategory, Track
from soundbox.worker_protocol import ProtocolError


def library():
    track = Track('one', '日本語', 'Artist', 'file:///never-open.wav', bpm='140',
        source_cues=[{'kind': 1, 'position': 24.0}], source_cue_revision='fresh',
        source_cue_refs=[{'UUID': 'cue', 'clock': 17}], source_tag_times={'tag': 1.5},
        source_tag_ids=['tag'], info={'opaque': [None, 1, 'x']}, source_native_id='native',
        source_native_path='/never-native.wav', source_uuid='uuid')
    return Library({'one': track}, [Playlist('Favorites', [], ['one', 'one'], 'p', ['one'], 'old')],
        Path('/never/master.db'), source_kind='rekordbox-db', source_library_id='source',
        hidden_ids={'hidden'}, removed_by_playlist={'p': ['hidden']}, title_junk=['junk'],
        source_binding_issues={'unavailable': 'Missing native identity'},
        source_tags=[LibraryTag('tag', 'House', 'Genre', 'tag-uuid', 'cat', 'cat-uuid')],
        source_tag_categories=[TagCategory('cat', 'Genre', 'cat-uuid')],
        source_playlist_catalog={'revision': 'raw', 'nodes': [{'id': 'p', 'clock': 5}],
                                 'members': [{'id': 'member', 'ContentID': 'native'}]})


class FullLibraryCodec(unittest.TestCase):
    def test_full_values_mapping_keys_and_duplicate_occurrences_survive(self):
        original = library()
        original.tracks_by_id = {'logical-map-key': original.tracks_by_id['one']}
        receiver = library_codec.LibraryReceiver(library_codec.describe(original), max_records=100)
        for kind, field, values in library_codec.batches(original):receiver.add(kind, field, values)
        actual = receiver.finish()
        self.assertEqual(asdict(actual), asdict(original))
        self.assertIsInstance(actual.source, Path)
        self.assertEqual(actual.playlists[0].keys, ['one', 'one'])

    def test_incomplete_duplicate_schema_and_declared_count_changes_rejected(self):
        original = library()
        receiver = library_codec.LibraryReceiver(library_codec.describe(original), max_records=100)
        with self.assertRaisesRegex(ProtocolError, 'Incomplete'):receiver.finish()
        record = next(library_codec.batches(original))
        receiver.add(*record)
        with self.assertRaises(ProtocolError):receiver.add(*record)
        descriptor = library_codec.describe(original);descriptor['schema'] = ('old',)
        with self.assertRaises(ProtocolError):library_codec.LibraryReceiver(descriptor, max_records=100)
        with self.assertRaises(ProtocolError):library_codec.LibraryReceiver(library_codec.describe(original), max_records=0)


class DerivedCacheProcess(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.lib = library()
        self.rows = {'one': self.lib.tracks_by_id['one'].to_row()}
        self.inputs = (frozenset({('one', '日本語', 'Artist')}), 'policy', '["trusted"]')
        self.service = CacheProcess(self.root / 'cache')
        self.addCleanup(self.service.close)

    def test_early_default_entry_prewarmed_once_and_matches_existing_bytes(self):
        reference = self.root / 'reference';reference.mkdir()
        libcache.dump(self.lib, reference / 'libcache.json')
        key = browser_cache.fingerprint(self.inputs)
        browser_cache.dump(reference / 'browsercache.json', key, self.rows)
        self.service.start();process = self.service.process
        self.service.start();self.assertIs(self.service.process, process)
        with mock.patch.object(libcache, 'dump', side_effect=AssertionError('No parent serialization')):
            self.service.write(self.lib, self.inputs, self.rows)
        for name in ('libcache.json', 'browsercache.json'):
            self.assertEqual((reference / name).read_bytes(), (self.root / 'cache' / name).read_bytes())
        self.service.close();self.assertEqual(process.returncode, 0)
        self.assertFalse(self.service.available)
        with self.assertRaises(ProtocolError):self.service.start()

    def test_serialization_failure_preserves_both_previous_files_then_next_job_succeeds(self):
        self.service.start();self.service.write(self.lib, self.inputs, self.rows)
        paths = [self.root / 'cache' / name for name in ('libcache.json', 'browsercache.json')]
        before = [path.read_bytes() for path in paths]
        bad_rows = {'one': {**self.rows['one'], 'title': float('nan')}}
        with self.assertRaises(CacheError):self.service.write(self.lib, self.inputs, bad_rows)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertTrue(self.service.available)
        self.service.write(self.lib, self.inputs, self.rows)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertFalse(list((self.root / 'cache').glob('.derived-cache-*')))

    def test_unsupported_record_aborts_without_replacing_previous_caches(self):
        self.service.start();self.service.write(self.lib, self.inputs, self.rows)
        before = (self.root / 'cache' / 'libcache.json').read_bytes()
        bad = replace(self.lib, tracks_by_id={'one': replace(self.lib.tracks_by_id['one'], info={'unsupported':object()})})
        with self.assertRaisesRegex(CacheError, 'unsupported'):self.service.write(bad, self.inputs, self.rows)
        self.assertEqual((self.root / 'cache' / 'libcache.json').read_bytes(), before)
        self.assertTrue(self.service.available)
        self.service.write(self.lib, self.inputs, self.rows)

    def test_large_single_record_and_input_header_keep_existing_cache_bytes(self):
        large = replace(self.lib, tracks_by_id={'one': replace(self.lib.tracks_by_id['one'],
            info={'comments':'x' * (1024**2 + 1)})})
        inputs = (self.inputs[0], 'header' * 200000)
        reference = self.root / 'large-reference';reference.mkdir()
        libcache.dump(large, reference / 'libcache.json')
        browser_cache.dump(reference / 'browsercache.json', browser_cache.fingerprint(inputs), self.rows)
        self.service.start();self.service.write(large, inputs, self.rows)
        for name in ('libcache.json', 'browsercache.json'):
            self.assertEqual((self.root / 'cache' / name).read_bytes(), (reference / name).read_bytes())

    def test_browser_destination_symlink_retains_original_skip_behavior(self):
        self.service.start()
        target = self.root / 'protected';target.write_bytes(b'retain external bytes')
        destination = self.root / 'cache' / 'browsercache.json';destination.symlink_to(target)
        self.service.write(self.lib, self.inputs, self.rows)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(target.read_bytes(), b'retain external bytes')
        self.assertTrue((self.root / 'cache' / 'libcache.json').is_file())

    def test_worker_entry_excludes_desktop_macdefaults_and_qt(self):
        # The real early entry dispatch checks these modules before serving.
        # A sentinel engine probe additionally checks the imported server tree.
        actor = '''
import sys
from soundbox import cache_process
assert not any(name.startswith(('PySide6','desktop')) or name=='soundbox.macos_defaults' for name in sys.modules)
from soundbox.worker_entry import main
raise SystemExit(main(sys.argv[-4:]))
'''
        service = CacheProcess(self.root / 'guarded', command=(sys.executable, '-c', actor))
        self.addCleanup(service.close)
        service.start();service.write(self.lib, self.inputs, self.rows)
        service.close();self.assertEqual(service.process.returncode, 0)

    def test_frame_order_failure_retires_only_job_and_preserves_previous_snapshot(self):
        self.service.start();self.service.write(self.lib, self.inputs, self.rows)
        before = (self.root / 'cache' / 'libcache.json').read_bytes()
        channel = self.service._worker.channel
        nonce = self.service._nonce + 1
        channel.send(('begin', 'cache', 1, nonce, library_codec.describe(self.lib), None, None, 0, 0))
        self.assertEqual(channel.receive()[0], ('ack', 'cache', 1, nonce, 0))
        channel.send(('commit', 'cache', 1, nonce, 2))
        self.assertEqual(channel.receive()[0][:4], ('error', 'cache', 1, nonce))
        self.assertEqual((self.root / 'cache' / 'libcache.json').read_bytes(), before)
        self.service._nonce = nonce
        self.service.write(self.lib, self.inputs, self.rows)

    def test_explicit_retry_joins_failed_child_and_retains_service_nonce(self):
        self.service.start();self.service.write(self.lib, self.inputs, self.rows)
        old=self.service.process;nonce=self.service._nonce
        self.service._worker.invalidate(ProtocolError('Synthetic interrupted transport'))
        self.service.start()
        self.assertIsNotNone(old.returncode)
        self.assertIsNot(self.service.process,old)
        self.service.write(self.lib,self.inputs,self.rows)
        self.assertEqual(self.service._nonce,nonce+1)
