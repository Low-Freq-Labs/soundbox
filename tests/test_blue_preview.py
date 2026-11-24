"""Bounded native palette reads and complete, generation-owned publication."""
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
from soundbox.rekordbox_analysis import read_blue_preview


ROWS = bytes((index * 31) % 256 for index in range(1200 * 6))


def tag(kind=b'PWV4', rows=ROWS, *, width=6, count=1200):
    return kind + struct.pack('>IIIII', 24, 24 + len(rows), width, count, 0) + rows


def image(*tags):
    body = b''.join(tags)
    return b'PMAI' + struct.pack('>II', 28, 28 + len(body)) + bytes(16) + body


class BluePreviewReader(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(); self.addCleanup(self.area.cleanup)
        self.dat = Path(self.area.name) / 'ANLZ0000.DAT'; self.ext = self.dat.with_suffix('.EXT')

    def test_exact_current_rows_skip_large_unrelated_payloads_with_bounded_io(self):
        decoy = tag(rows=bytes(len(ROWS))) + bytes(2_000_000)
        unrelated = b'XXXX' + struct.pack('>II', 12, 12 + len(decoy)) + decoy
        self.ext.write_bytes(image(unrelated, tag()))
        original = Path.open; reads = []; opens = []
        class Counted:
            def __init__(self, stream):self.stream = stream
            def __enter__(self):return self
            def __exit__(self, *_):self.stream.close()
            def __getattr__(self, name):return getattr(self.stream, name)
            def read(self, size):reads.append(size); return self.stream.read(size)
        def opened(path, *args, **kwargs):
            opens.append(path); self.assertEqual(args, ('rb',)); self.assertEqual(kwargs, {'buffering': 0})
            return Counted(original(path, *args, **kwargs))
        before = self.ext.read_bytes()
        with mock.patch.object(Path, 'open', opened):
            self.assertEqual(read_blue_preview(self.dat), ROWS)
        self.assertEqual(opens, [self.ext]); self.assertLessEqual(sum(reads), 8000)
        self.assertEqual(self.ext.read_bytes(), before)

    def test_missing_wrong_or_ambiguous_palette_uses_derived_fallback(self):
        self.assertEqual(read_blue_preview(self.dat), b'')
        cases = [b'bad', image(tag(width=3)), image(tag(count=1199)), image(tag(), tag()),
                 image(tag(rows=ROWS[:-1])), image(tag()).replace(b'PMAI', b'NOPE', 1),
                 image(b'PWV4' + struct.pack('>II', 24, 12)), image() + b'trailer']
        for raw in cases:
            with self.subTest(size=len(raw)):
                self.ext.write_bytes(raw)
                self.assertEqual(read_blue_preview(self.dat), b'')
        self.assertEqual(read_blue_preview(''), b'')

    def test_changed_open_descriptor_rejects_instead_of_publishing_alias_bytes(self):
        self.ext.write_bytes(image(tag())); original = os.fstat; calls = []
        def observed(fd):
            calls.append(fd)
            if len(calls) == 2:
                with self.ext.open('ab') as stream:stream.write(b'changed')
            return original(fd)
        with mock.patch('soundbox.rekordbox_analysis.os.fstat', side_effect=observed):
            self.assertEqual(read_blue_preview(self.dat), b'')
        self.assertEqual(len(calls), 2)

    def test_path_replacement_after_read_is_not_the_open_file(self):
        self.ext.write_bytes(image(tag())); replacement = self.ext.with_name('replacement.EXT')
        replacement.write_bytes(image(tag(rows=bytes(len(ROWS)))))
        before = self.ext.stat(); original = os.fstat; calls = []
        def observed(fd):
            calls.append(fd)
            if len(calls) == 2:os.replace(replacement, self.ext)
            original(fd)
            # Independently exercise the final path check even if a filesystem
            # does not change the retired descriptor's metadata on rename.
            return before
        with mock.patch('soundbox.rekordbox_analysis.os.fstat', side_effect=observed):
            self.assertEqual(read_blue_preview(self.dat), b'')

    def test_tag_budget_is_bounded_even_for_unavailable_optional_data(self):
        other = b'XXXX' + struct.pack('>II', 12, 12)
        self.ext.write_bytes(image(*([other] * 65), tag()))
        self.assertEqual(read_blue_preview(self.dat), b'')


class BluePreviewWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def worker(self, reader):
        calls = []; detail = {'_audio_detail': {'rate': 1000}}
        backend = SimpleNamespace(set_complete_analysis=lambda overview, detail, generation:
                                  (calls.append((threading.get_ident(), detail['_blue_preview'])) or (True, True)))
        worker = WaveformWorker('/never-open/source.wav', '/never-open/ANLZ0000.DAT',
                                track_id='track', track_analysis_dir='/never-open/cache',
                                generation=4, native_backend=backend)
        worker.transient_analysis = True
        for target, value in [('load_manifest', {}), ('load_overview', {'_overview_heights': b'\xff'}),
                              ('load_detail', detail)]:
            self.enterContext(mock.patch('soundbox.track_analysis.' + target, return_value=value))
        self.enterContext(mock.patch('soundbox.track_analysis.store', side_effect=AssertionError('Read-only palette load')))
        self.enterContext(mock.patch('soundbox.rekordbox_analysis.read_blue_preview', side_effect=reader))
        self.addCleanup(worker.wait, 2000)
        return worker, calls, detail

    def test_palette_prepares_off_gui_before_first_complete_surface_in_every_mode(self):
        entered = threading.Event(); release = threading.Event(); reader_threads = []
        def read(path):
            self.assertEqual(path, '/never-open/ANLZ0000.DAT')
            reader_threads.append(threading.get_ident()); entered.set()
            if not release.wait(2):raise RuntimeError('Palette fixture timed out')
            return ROWS
        worker, calls, detail = self.worker(read)
        self.addCleanup(release.set)
        worker.start(); self.assertTrue(entered.wait(1))
        self.assertFalse(worker.surface_ready); self.assertEqual(calls, [])
        release.set(); self.assertTrue(worker.wait(2000)); self.app.processEvents()
        self.assertEqual(calls, [(reader_threads[0], ROWS)])
        self.assertNotEqual(reader_threads[0], threading.get_ident())
        self.assertTrue(worker.surface_ready); self.assertTrue(detail['_waveform_complete'])

    def test_cancel_after_palette_read_cannot_publish_any_ready_surface(self):
        entered = threading.Event(); release = threading.Event()
        def read(_path):
            entered.set(); release.wait(2); return ROWS
        worker, calls, _ = self.worker(read); self.addCleanup(release.set)
        worker.start(); self.assertTrue(entered.wait(1)); worker.requestInterruption(); release.set()
        self.assertTrue(worker.wait(2000)); self.app.processEvents()
        self.assertFalse(worker.surface_ready); self.assertEqual(calls, [])

    def test_optional_unavailable_preview_still_has_a_complete_fallback_frame(self):
        worker, calls, detail = self.worker(lambda _path: b'')
        self.assertEqual(worker._load_stages(), (True, True, True))
        self.assertEqual(detail['_blue_preview'], b''); self.assertEqual(calls[0][1], b'')
        self.assertTrue(worker.surface_ready)


class BluePreviewUpload(unittest.TestCase):
    def host(self):
        events = []; packets = []
        def blue(handle, generation, packed, count):
            events.append('blue'); packets.append((generation, ctypes.string_at(packed, count * 6) if count else b'', count)); return 1
        lib = SimpleNamespace(sb_deck_begin_waveform_update=lambda handle: events.append('begin'),
                              sb_deck_end_waveform_update=lambda handle: events.append('end'),
                              sb_deck_set_blue_overview_for_generation=blue,
                              sb_deck_set_three_band_overview_for_generation=lambda *args: 1)
        host = SimpleNamespace(_lib=lib, _handle=123, _analysis_generation=7,
            set_overview=lambda *args: events.append('overview') or True,
            diagnostic_detail_shape=lambda: 0,
            _set_waveform_streams=lambda *args: events.append('streams') or True,
            _set_detail_bands=lambda *args: False,
            _set_audio_detail=lambda *args: events.append('audio'))
        host._set_blue_preview = lambda rows, generation: NativeDeckBackend._set_blue_preview(host, rows, generation)
        host._set_three_band_preview = lambda rows, generation: NativeDeckBackend._set_three_band_preview(host, rows, generation)
        return host, events, packets

    def test_palette_and_empty_next_track_publish_inside_the_group(self):
        host, events, packets = self.host()
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_blue_preview': ROWS}, 7), (True, True))
        self.assertEqual(events, ['begin', 'overview', 'streams', 'audio', 'blue', 'end'])
        host._analysis_generation = 8
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {}, 8), (True, True))
        self.assertEqual(packets, [(7, ROWS, 1200), (8, b'', 0)])

    def test_stale_generation_or_failed_palette_rejects_complete_readiness(self):
        host, events, packets = self.host()
        def changed(*args):host._analysis_generation = 8; return 1
        host._lib.sb_deck_set_blue_overview_for_generation = changed
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_blue_preview': ROWS}, 7), (False, False))
        self.assertEqual(events[-1], 'end')
        host._lib.sb_deck_set_blue_overview_for_generation = lambda *args: 0
        self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {}, 8), (True, False))

    def test_malformed_transfer_never_reaches_the_native_palette(self):
        host, events, packets = self.host()
        for rows in (b'x', b'x' * 6, b'x' * 7206, 'not bytes'):
            self.assertEqual(NativeDeckBackend.set_complete_analysis(host, {}, {'_blue_preview': rows}, 7), (True, False))
        self.assertEqual(packets, [])

    def test_current_abi_requires_the_blue_generation_bound_symbol(self):
        class Library:
            def __getattr__(self, name):
                if name == 'sb_deck_set_blue_overview_for_generation':raise AttributeError(name)
                value = mock.Mock(); setattr(self, name, value); return value
        self.assertEqual(_NATIVE_ABI_VERSION, 9)
        with mock.patch('desktop.native_deck.ctypes.CDLL', return_value=Library()), self.assertRaises(AttributeError):
            _bind(Path('/synthetic/deck.dylib'))
