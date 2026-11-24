"""Native PWV6 preview framing, complete worker preload and current ABI ownership."""
import ctypes
import os
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from desktop.native_deck import NativeDeckBackend, _NATIVE_ABI_VERSION, _bind
from desktop.player import WaveformWorker
from soundbox.rekordbox_analysis import read_three_band_preview
from tests import test_blue_preview as blue_fixtures


ROWS = bytes((index * 13) % 128 for index in range(1200 * 3))


def tag(rows=ROWS, *, width=3, count=1200, header=20):
    fields = struct.pack('>II', width, count) + bytes(max(0, header - 20))
    return b'PWV6' + struct.pack('>II', header, header + len(rows)) + fields + rows


class ThreeBandPreviewReader(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.dat = Path(self.area.name) / 'ANLZ0000.DAT'; self.two_ex = self.dat.with_suffix('.2EX')

    def test_original1200_triplets_skip_large_detail_without_requantizing(self):
        decoy = tag(rows=bytes(len(ROWS))) + bytes(1_000_000)
        detail = b'PWV7' + struct.pack('>II', 24, 24 + len(decoy)) + struct.pack('>III', 3, len(decoy) // 3, 0x00960000) + decoy
        self.two_ex.write_bytes(blue_fixtures.image(detail, tag()))
        original = Path.open; reads = []; opened = []
        class Counted:
            def __init__(self, stream):self.stream = stream
            def __enter__(self):return self
            def __exit__(self, *_):self.stream.close()
            def __getattr__(self, name):return getattr(self.stream, name)
            def read(self, size):reads.append(size); return self.stream.read(size)
        def opening(path, *args, **kwargs):
            opened.append(path); self.assertEqual(args, ('rb',)); self.assertEqual(kwargs, {'buffering': 0})
            return Counted(original(path, *args, **kwargs))
        before = self.two_ex.read_bytes()
        with mock.patch.object(Path, 'open', opening):
            self.assertEqual(read_three_band_preview(self.dat), ROWS)
        self.assertEqual(opened, [self.two_ex]); self.assertLessEqual(sum(reads), 5000)
        self.assertEqual(self.two_ex.read_bytes(), before)

    def test_missing_wrong_width_count_header_or_highbit_use_empty_fallback(self):
        self.assertEqual(read_three_band_preview(self.dat), b'')
        for raw in (blue_fixtures.image(tag(width=6)), blue_fixtures.image(tag(count=1199)),
                    blue_fixtures.image(tag(header=24)), blue_fixtures.image(tag(rows=ROWS[:-1])),
                    blue_fixtures.image(tag(), tag()), blue_fixtures.image(tag(rows=b'\x80' + ROWS[1:]))):
            with self.subTest(size=len(raw)):
                self.two_ex.write_bytes(raw)
                self.assertEqual(read_three_band_preview(self.dat), b'')

    def test_descriptor_mutation_and_path_replacement_reject_stale_rows(self):
        original = os.fstat
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.two_ex.write_bytes(blue_fixtures.image(tag())); before = self.two_ex.stat(); calls = []
                def observed(fd):
                    calls.append(fd)
                    if len(calls) == 2:
                        if replace:
                            other = self.two_ex.with_name('replacement.2EX')
                            other.write_bytes(blue_fixtures.image(tag(rows=bytes(len(ROWS)))))
                            os.replace(other, self.two_ex)
                        else:
                            with self.two_ex.open('ab') as stream:stream.write(b'changed')
                    fresh = original(fd)
                    return before if replace else fresh
                with mock.patch('soundbox.rekordbox_analysis.os.fstat', side_effect=observed):
                    self.assertEqual(read_three_band_preview(self.dat), b'')
                self.assertEqual(len(calls), 2)


class ThreeBandPreviewWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def worker(self, reader, *, blue_reader=None):
        calls = []; detail = {'_audio_detail': {'rate': 1000}}
        backend = SimpleNamespace(set_complete_analysis=lambda overview, data, generation:
            calls.append((threading.get_ident(), data['_blue_preview'], data['_three_band_preview'])) or (True, True))
        worker = WaveformWorker('/never-open/source.wav', '/never-open/ANLZ0000.DAT',
            track_id='track', track_analysis_dir='/never-open/cache', generation=5, native_backend=backend)
        worker.transient_analysis = True
        for target, value in [('load_manifest', {}), ('load_overview', {'_overview_heights': b'\xff'}), ('load_detail', detail)]:
            self.enterContext(mock.patch('soundbox.track_analysis.' + target, return_value=value))
        self.enterContext(mock.patch('soundbox.track_analysis.store', side_effect=AssertionError('Preview must not write cache')))
        self.enterContext(mock.patch('soundbox.rekordbox_analysis.read_blue_preview', side_effect=blue_reader or (lambda path: blue_fixtures.ROWS)))
        self.enterContext(mock.patch('soundbox.rekordbox_analysis.read_three_band_preview', side_effect=reader))
        self.addCleanup(worker.wait, 2000)
        return worker, calls

    def test_both_previews_are_resident_off_gui_before_first_complete_frame(self):
        entered = threading.Event(); release = threading.Event(); readers = []
        def read(path):
            self.assertEqual(path, '/never-open/ANLZ0000.DAT')
            readers.append(threading.get_ident()); entered.set()
            if not release.wait(2):raise RuntimeError('Preview fixture timed out')
            return ROWS
        worker, calls = self.worker(read); self.addCleanup(release.set)
        worker.start(); self.assertTrue(entered.wait(1))
        self.assertEqual(calls, []); self.assertFalse(worker.surface_ready)
        release.set(); self.assertTrue(worker.wait(2000)); self.app.processEvents()
        self.assertNotEqual(readers[0], threading.get_ident())
        self.assertEqual(calls, [(readers[0], blue_fixtures.ROWS, ROWS)])
        self.assertTrue(worker.surface_ready)

    def test_cancellation_during_preview_never_publishes_surface(self):
        entered = threading.Event(); release = threading.Event()
        def read(_path):entered.set(); release.wait(2); return ROWS
        worker, calls = self.worker(read); self.addCleanup(release.set)
        worker.start(); self.assertTrue(entered.wait(1)); worker.requestInterruption(); release.set()
        self.assertTrue(worker.wait(2000)); self.app.processEvents()
        self.assertEqual(calls, []); self.assertFalse(worker.surface_ready)

    def test_cancel_between_previews_skips_the_second_read(self):
        holder = {}
        def blue(_path):holder['worker'].requestInterruption(); return blue_fixtures.ROWS
        worker, calls = self.worker(mock.Mock(side_effect=AssertionError('Cancelled second read')), blue_reader=blue)
        holder['worker'] = worker; worker.start(); self.assertTrue(worker.wait(2000))
        self.assertEqual(calls, []); self.assertFalse(worker.surface_ready)

    def test_missing_optional_preview_keeps_the_complete_derived_fallback(self):
        worker, calls = self.worker(lambda _path: b'')
        self.assertEqual(worker._load_stages(), (True, True, True))
        self.assertEqual(calls[0][2], b''); self.assertTrue(worker.surface_ready)


class ThreeBandPreviewUpload(unittest.TestCase):
    def host(self):
        host, events, _ = blue_fixtures.BluePreviewUpload().host(); packets = []
        def three(handle, generation, packed, count):
            events.append('three'); packets.append((generation, ctypes.string_at(packed, count * 3) if count else b'', count)); return 1
        host._lib.sb_deck_set_three_band_overview_for_generation = three
        return host, events, packets

    def test_blue_then_full_pwV6_and_empty_next_track_are_grouped(self):
        host, events, packets = self.host()
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_three_band_preview': ROWS}, 7), (True, True))
        self.assertEqual(events, ['begin', 'overview', 'streams', 'audio', 'blue', 'three', 'end'])
        host._analysis_generation = 8
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {}, 8), (True, True))
        self.assertEqual(packets, [(7, ROWS, 1200), (8, b'', 0)])

    def test_stale_generation_or_native_rejection_blocks_ready(self):
        host, events, _ = self.host()
        def stale(*args):host._analysis_generation = 8; return 1
        host._lib.sb_deck_set_three_band_overview_for_generation = stale
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_three_band_preview': ROWS}, 7), (False, False))
        self.assertEqual(events[-1], 'end')
        host._lib.sb_deck_set_three_band_overview_for_generation = lambda *args: 0
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {}, 8), (True, False))

    def test_invalid_size_or_signed_highbit_is_rejected_before_cabi(self):
        host, events, packets = self.host()
        for rows in (b'x', b'x' * 3, b'x' * 3603, b'\x80' + ROWS[1:], bytearray(ROWS)):
            self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_three_band_preview': rows}, 7), (True, False))
        self.assertEqual(packets, [])

    def test_current_abi_requires_the_three_band_generation_symbol(self):
        class Library:
            def __getattr__(self, name):
                if name == 'sb_deck_set_three_band_overview_for_generation':raise AttributeError(name)
                value = mock.Mock(); setattr(self, name, value); return value
        self.assertEqual(_NATIVE_ABI_VERSION, 9)
        with mock.patch('desktop.native_deck.ctypes.CDLL', return_value=Library()), self.assertRaises(AttributeError):
            _bind(Path('/synthetic/deck.dylib'))
