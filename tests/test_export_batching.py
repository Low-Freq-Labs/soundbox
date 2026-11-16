"""Generic exports plan shared tracks once per queued device batch."""

import unittest
from unittest import mock

from desktop.workers import ExportWorker
from soundbox.library import Library, Playlist, Track


class ExportBatchingTests(unittest.TestCase):
    def test_adjacent_admissions_keep_their_own_captured_generation_and_progress(self):
        source = Track('one', 'Before', 'Artist', 'file:///synthetic/one.wav')
        library = Library({'one': source}, [], '/source/master.db')
        playlist = Playlist('Selected', [], ['one'])
        worker = ExportWorker(library)
        worker.add('/synthetic/USB', [playlist], titles={'one': 'First title'})
        source.name = 'After'; playlist.keys.append('one')
        worker.add('/synthetic/USB', [playlist], titles={'one': 'Second title'})
        events = []
        worker.progress.connect(lambda done, total, _label: events.append((done, total)))
        with mock.patch('desktop.workers.engine.export_playlists',
                        return_value=mock.Mock(written_dirs=set(), cancelled=False, playlists=[])) as export, \
                mock.patch('desktop.workers.engine.clean_device'):
            worker.run()
        self.assertEqual([call.args[0].tracks_by_id['one'].name for call in export.call_args_list], ['Before', 'After'])
        self.assertEqual([call.args[1][0].keys for call in export.call_args_list], [['one'], ['one', 'one']])
        self.assertEqual([call.kwargs['title_overrides']['one'] for call in export.call_args_list], ['First title', 'Second title'])
        self.assertEqual(events, [(1, 3), (3, 3)])
        self.assertFalse(worker._jobs)

    def test_queued_drop_freezes_tracks_memberships_curation_and_unselected_guard_paths(self):
        source = Track('one', 'Before', 'Artist', 'file:///synthetic/one.wav')
        other = Track('other', 'Other', 'Artist', 'file:///unselected/other.wav')
        library = Library({'one': source, 'other': other}, [], '/source/master.db')
        playlist = Playlist('Selected', [], ['one', 'one'])
        titles = {'one': 'Saved'}
        worker = ExportWorker(library, titles=titles)
        worker.add('/synthetic/USB', [playlist])
        source.name = 'Later'; playlist.keys.clear(); titles['one'] = 'Later'
        library.tracks_by_id.clear()
        with mock.patch('desktop.workers.engine.export_playlists',
                        return_value=mock.Mock(written_dirs=set(), cancelled=False, playlists=[])) as export, \
                mock.patch('desktop.workers.engine.clean_device'):
            worker.run()
        frozen, selected = export.call_args.args[:2]
        self.assertEqual(frozen.tracks_by_id['one'].name, 'Before')
        self.assertEqual(selected[0].keys, ['one', 'one'])
        self.assertEqual(export.call_args.kwargs['title_overrides'], {'one': 'Saved'})
        self.assertIn(other.path, frozen.export_source_paths)

    def test_same_device_batch_preserves_memberships_and_scales_progress(self):
        library = Library(tracks_by_id={
            "one": Track("one", "One", "Artist", "file:///synthetic/one.wav"),
            "two": Track("two", "Two", "Artist", "file:///synthetic/two.wav"),
        }, playlists=[], source="fixture")
        a = Playlist("A", [], ["one", "two"], pid="a")
        b = Playlist("B", [], ["one"], pid="b")
        worker = ExportWorker(library)
        worker.add("/synthetic/USB", [a, b])
        events = []
        calls = []
        worker.progress.connect(lambda d, t, label: events.append((d, t)))

        def export(lib, selected, target, **kwargs):
            calls.append((selected, target))
            # A drop arriving during this batch must be handled next.
            if len(calls) == 1:
                worker.add("/synthetic/USB", [b])
                kwargs["on_progress"](1, 2, "one")
                kwargs["on_progress"](2, 2, "two")
            return mock.Mock(written_dirs=set(), cancelled=False, playlists=[])

        with mock.patch("desktop.workers.engine.export_playlists", side_effect=export), \
                mock.patch("desktop.workers.engine.clean_device"):
            worker.run()
        self.assertEqual(calls, [([a, b], "/synthetic/USB"), ([b], "/synthetic/USB")])
        self.assertEqual(events, [(1, 4), (3, 4), (3, 4), (4, 4)])
        self.assertEqual(a.keys, ["one", "two"])
        self.assertEqual(b.keys, ["one"])

    def test_different_device_jobs_keep_fifo_boundaries(self):
        library = Library(tracks_by_id={}, playlists=[], source="fixture")
        playlist = Playlist("Empty", [], [], pid="empty")
        worker = ExportWorker(library)
        for device in ("/synthetic/A", "/synthetic/B", "/synthetic/A"):
            worker.add(device, [playlist])
        with mock.patch("desktop.workers.engine.export_playlists",
                        return_value=mock.Mock(written_dirs=set(), cancelled=False, playlists=[])) as export, \
                mock.patch("desktop.workers.engine.clean_device"):
            worker.run()
        self.assertEqual([call.args[2] for call in export.call_args_list],
                         ["/synthetic/A", "/synthetic/B", "/synthetic/A"])
