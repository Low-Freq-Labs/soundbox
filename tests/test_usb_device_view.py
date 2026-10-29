"""USB dashboard reads only disposable playlist text; no live volume discovery."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from desktop.devices import DeviceFacts
from desktop.usb_device_view import UsbSnapshot, read_usb_snapshot


class UsbPlaylistSnapshot(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.token = self.root.stat().st_dev
        self.facts = DeviceFacts(capacity_bytes=1000, free_bytes=400)

    def read(self):
        return read_usb_snapshot(str(self.root), self.token, self.facts)

    def test_nested_playlists_have_titles_without_opening_media_or_pioneer(self):
        folder = self.root/'Playlists/CRATES'
        folder.mkdir(parents=True)
        (self.root/'PIONEER').mkdir()
        playlist = folder/'SET ONE.m3u8'
        data = ('#EXTM3U\n#EXTINF:12,Artist – Title\n../../Music/a.wav\n'
                '/Music/b.mp3\n/PiOnEeR/never.wav\n../../../outside.wav\nhttps://invalid/test.mp3\n').encode()
        playlist.write_bytes(data)
        original = os.open
        opened = []
        def guarded(path, *args, **kwargs):
            opened.append(str(path))
            if str(path).casefold() == 'pioneer' or str(path).endswith(('.wav', '.mp3')):
                raise AssertionError('Reader opened protected content or media')
            return original(path, *args, **kwargs)
        with mock.patch('desktop.usb_device_view.os.open', side_effect=guarded):
            result = self.read()
        self.assertFalse(result.error, result.error)
        self.assertEqual(len(result.playlists), 1)
        row = result.playlists[0]
        self.assertEqual(row.entries, (('Artist – Title', '/Music/a.wav'), ('b', '/Music/b.mp3')))
        self.assertEqual(row.skipped, 3)
        self.assertEqual(playlist.read_bytes(), data)
        self.assertNotIn('PIONEER', opened)

    def test_absent_folder_is_empty_but_redirects_and_bad_mounts_are_errors(self):
        self.assertEqual(self.read().playlists, ())
        self.assertFalse(self.read().error)
        outside = self.root/'elsewhere'
        outside.mkdir()
        (self.root/'Playlists').symlink_to(outside, target_is_directory=True)
        result = self.read()
        self.assertTrue(result.error)
        self.assertEqual(result.playlists, ())
        self.assertTrue(read_usb_snapshot(str(self.root), self.token + 1, self.facts).error)

    def test_linked_child_and_oversize_payload_fail_without_partial_rows(self):
        folder = self.root/'Playlists'
        folder.mkdir()
        (folder/'A.m3u8').write_text('/Music/a.wav\n')
        (folder/'B.m3u8').symlink_to(folder/'A.m3u8')
        self.assertTrue(self.read().error)
        (folder/'B.m3u8').unlink()
        with mock.patch('desktop.usb_device_view._MAX_BYTES', 4):
            result = self.read()
        self.assertTrue(result.error)
        self.assertEqual(result.playlists, ())
        with mock.patch('desktop.usb_device_view._MAX_TOTAL_BYTES', 4):
            self.assertTrue(self.read().error)

    def test_cached_facts_do_not_probe_an_unknown_mount_or_io_registry(self):
        from desktop import devices
        target = '/Volumes/NO-REAL-PROBE'
        with mock.patch.dict(devices._INFO_CACHE, {}, clear=True),                 mock.patch.object(devices, '_is_external', side_effect=AssertionError('diskutil probe')),                 mock.patch.object(devices, '_classic_ipod_facts', side_effect=AssertionError('IORegistry probe')):
            self.assertEqual(devices.device_facts(target, cached_only=True), DeviceFacts())
            devices._INFO_CACHE[target] = {'MediaType':'iPod','BusProtocol':'USB'}
            self.assertEqual(devices.device_facts(target, cached_only=True).protocol, 'USB')

    def test_periodic_device_scan_reuses_snapshot_and_explicit_refresh_reads_once(self):
        from desktop.workers import DeviceScanWorker
        from soundbox.cdj_usb import metadata_stamp
        target = str(self.root)
        cached = UsbSnapshot(self.root, self.token, self.facts,native_stamp=metadata_stamp(self.root,self.token))
        profile = SimpleNamespace(has_stock_ipod=False, kind='generic_usb')
        def worker(requests=()):
            return DeviceScanWorker(None, known_paths={target}, known_mount_tokens={target:self.token},
                                    usb_views={target:cached}, usb_requests=requests)
        with mock.patch('desktop.workers.list_devices', return_value=[('USB',target,400)]), \
                mock.patch('desktop.workers.device_profile', return_value=profile), \
                mock.patch('desktop.workers.device_facts', return_value=self.facts) as facts, \
                mock.patch('desktop.usb_device_view.read_usb_snapshot', return_value=cached) as reader:
            first = worker();first.run()
            self.assertIs(first.usb_snapshots[target], cached)
            reader.assert_not_called();facts.assert_not_called()
            refresh = worker({target});refresh.run()
            reader.assert_called_once_with(target, self.token, self.facts)
            facts.assert_called_once_with(target, cached_only=True)


class UsbPlaybackReferences(unittest.TestCase):
    def test_selected_audio_and_analysis_are_usb_paths_and_redirects_fail(self):
        from soundbox.cdj_usb import CdjSong, prepare_playback
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            (root/'Contents').mkdir()
            (root/'Contents/track.wav').write_bytes(b'captured fixture')
            analysis = root/'PIONEER/USBANLZ/A'
            analysis.mkdir(parents=True)
            (analysis/'ANLZ0000.DAT').write_bytes(b'fixture metadata')
            song = CdjSong('1','/Contents/track.wav','Fixture',analysis='/PIONEER/USBANLZ/A/ANLZ0000.DAT')
            audio,dat = prepare_playback(root,root.stat().st_dev,song)
            self.assertEqual(audio,root/'Contents/track.wav')
            self.assertEqual(dat,analysis/'ANLZ0000.DAT')
            (analysis/'ANLZ0000.EXT').symlink_to(analysis/'ANLZ0000.DAT')
            with self.assertRaises(ValueError):
                prepare_playback(root,root.stat().st_dev,song)
            (analysis/'ANLZ0000.EXT').unlink()
            (root/'Contents/track.wav').unlink()
            (root/'Contents/track.wav').symlink_to(analysis/'ANLZ0000.DAT')
            with self.assertRaises(ValueError):
                prepare_playback(root,root.stat().st_dev,song)
