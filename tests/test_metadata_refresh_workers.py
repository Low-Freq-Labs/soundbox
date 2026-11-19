"""Native refresh compares scalar metadata in its existing read worker."""
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock

from desktop.workers import RefreshWorker
from soundbox.library import Library, Track


class MetadataRefreshWorker(unittest.TestCase):
    def library(self):
        return Library({'1': Track('1', 'Title', 'Artist', 'file:///synthetic/song.wav',
            source_uuid='00000000-0000-4000-8000-000000000001')}, [], Path('/synthetic/master.db'),
            source_kind='rekordbox-db', source_library_id='123')

    def read(self, incoming, baseline=None):
        worker = RefreshWorker(incoming.source)
        worker.metadata_baseline = baseline
        with mock.patch('desktop.workers.rekordbox_db.load_live', return_value=incoming) as load:
            worker.run()
        self.assertIs(worker.result, incoming)
        self.assertEqual(worker.error, '')
        load.assert_called_once_with(incoming.source)
        return worker.metadata_changed_ids

    def baseline(self, library):
        return ((str(library.source), library.source_kind, library.source_library_id), MappingProxyType({
            track.native_id: (track.source_uuid, track.source_native_path or str(track.path), track.name, track.artist)
            for track in library.tracks_by_id.values()}))

    def test_unchanged_metadata_and_prep_only_changes_publish_no_dirty_rows(self):
        original = self.library()
        track = replace(original.tracks_by_id['1'], bpm='140', key='8A', source_tag_ids=['tag'],
                        source_cues=[{'position': 1000}])
        incoming = replace(original, tracks_by_id={'1': track})
        with mock.patch('soundbox.metadata.derive_artist', side_effect=AssertionError('No derivation in inventory')):
            self.assertEqual(self.read(incoming, self.baseline(original)), ())

    def test_only_the_changed_song_is_dirty_without_copying_musical_arrays(self):
        original = self.library()
        class NeverCopy(list):
            def __deepcopy__(self, memo):
                raise AssertionError('Refresh inventory must not copy musical arrays')
        for number in range(2, 5002):
            original.tracks_by_id[str(number)] = replace(original.tracks_by_id['1'], track_id=str(number),
                source_cues=NeverCopy([{'position': number}]))
        incoming = replace(original, tracks_by_id=dict(original.tracks_by_id))
        incoming.tracks_by_id['123'] = replace(incoming.tracks_by_id['123'], artist='New Artist')
        self.assertEqual(self.read(incoming, self.baseline(original)), ('123',))
        self.assertIs(incoming.tracks_by_id['124'].source_cues, original.tracks_by_id['124'].source_cues)

    def test_replaced_uuid_or_raw_native_path_remains_dirty(self):
        original = self.library()
        for changes in ({'source_uuid': '00000000-0000-4000-8000-000000000002'},
                        {'source_native_path': '/synthetic/relocated.wav'}, {'name': 'New Title'}):
            with self.subTest(changes=changes):
                incoming = replace(original, tracks_by_id={'1': replace(original.tracks_by_id['1'], **changes)})
                self.assertEqual(self.read(incoming, self.baseline(original)), ('1',))

    def test_initial_and_foreign_library_do_not_reuse_an_equal_numeric_id(self):
        original = self.library()
        self.assertEqual(self.read(original), ('1',))
        incoming = replace(original, source_library_id='456')
        self.assertEqual(self.read(incoming, self.baseline(original)), ('1',))

    def test_projected_logical_id_does_not_make_unchanged_native_metadata_dirty(self):
        original = self.library()
        projected = replace(original, tracks_by_id={'logical': replace(original.tracks_by_id['1'],
            track_id='logical', source_native_id='1')})
        self.assertEqual(self.read(original, self.baseline(projected)), ())

    def test_owned_helper_runs_after_existing_recovery_preflight_and_retains_exact_dirty_inventory(self):
        original = self.library()
        incoming = replace(original, tracks_by_id={'1': replace(original.tracks_by_id['1'], artist='New Artist')})
        order = []
        worker = RefreshWorker(original.source)
        worker.preflight = lambda path:order.append(('recovery', path))
        worker.retained_tracks = {'123': {'1': 'uuid'}}
        worker.metadata_baseline = self.baseline(original)
        def read(path, **kwargs):
            order.append(('reader', path, kwargs))
            return incoming
        worker.native_reader = SimpleNamespace(read_live=mock.Mock(side_effect=read))
        with mock.patch('desktop.workers.rekordbox_db.load_live', side_effect=AssertionError('No parent fallback')):
            worker.run()
        self.assertIs(worker.result, incoming)
        self.assertEqual(worker.metadata_changed_ids, ('1',))
        self.assertEqual(order, [('recovery', original.source),
            ('reader', original.source, {'retain_tracks': worker.retained_tracks})])

    def test_helper_failure_and_recovery_failure_do_not_fall_back_or_publish_a_partial_library(self):
        from soundbox.worker_protocol import ProtocolError
        for recovery_failure in (False, True):
            with self.subTest(recovery_failure=recovery_failure):
                worker = RefreshWorker(self.library().source)
                worker.preflight = mock.Mock(side_effect=RuntimeError('Recovery requires attention') if recovery_failure else None)
                worker.native_reader = SimpleNamespace(read_live=mock.Mock(side_effect=ProtocolError('Read helper exited')))
                failures = [];published = []
                worker.failed.connect(failures.append);worker.done.connect(published.append)
                with mock.patch('desktop.workers.rekordbox_db.load_live', side_effect=AssertionError('No fallback')):
                    worker.run()
                self.assertIsNone(worker.result)
                self.assertFalse(published)
                self.assertEqual(failures, [worker.error])
                self.assertIn('Recovery requires attention' if recovery_failure else 'Read helper exited', worker.error)
                self.assertEqual(worker.native_reader.read_live.call_count, 0 if recovery_failure else 1)

    def test_explicit_standalone_reader_retains_existing_retained_tracks_interface(self):
        incoming = self.library();worker = RefreshWorker(incoming.source)
        worker.retained_tracks = {'123': {'1': 'uuid'}}
        with mock.patch('desktop.workers.rekordbox_db.load_live', return_value=incoming) as read:
            worker.run()
        read.assert_called_once_with(incoming.source, retain_tracks=worker.retained_tracks)
        self.assertIs(worker.result, incoming)


if __name__ == '__main__':
    unittest.main()
