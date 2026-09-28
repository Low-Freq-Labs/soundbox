"""Generic exports plan shared tracks once per queued device batch."""

import unittest
from unittest import mock

from desktop.workers import ExportWorker
from soundbox.library import Library, Playlist, Track


class ExportBatchingTests(unittest.TestCase):
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
