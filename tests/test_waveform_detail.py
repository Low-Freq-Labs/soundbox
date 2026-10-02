"""Compact import-first analysis, timing and source-safety contracts."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from soundbox import track_analysis as ta
from soundbox.waveform_detail import (
    DetailSupplement, analyze_imported, complete_import, mp3_origin,
    DETAIL_MODEL, DETAIL_DIVISORS,
)


class CompactWaveform(unittest.TestCase):
    def test_cells_match_absolute_reference_across_chunk_boundaries(self):
        for rate in (32000, 44100, 48000, 96000):
            y = np.random.default_rng(3).uniform(-1, 1, rate * 2 + 17).astype('f4')
            starts = np.arange((len(y) * 1000 + rate - 1) // rate) * rate // 1000
            expected = [np.rint(operation.reduceat(y, starts) * 32767).astype('<i2').tobytes()
                        for operation in (np.minimum, np.maximum)]
            for chunk in (1, 47, 131072):
                analyzer = DetailSupplement(rate)
                for first in range(0, len(y), chunk):
                    analyzer.feed(y[first:first+chunk])
                self.assertEqual(analyzer.finish(), tuple(expected))

    def test_import_bypasses_fft_and_persists_one_detail_level(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.wav'
            sf.write(source, np.sin(np.arange(48000) * .01) * .7, 48000, subtype='PCM_16')
            digest = hashlib.sha256(source.read_bytes()).digest()
            imported = {'sample_rate': 150.0, 'beats': [[1, 0, 120]],
                        '_blue_detail': bytes([31]) * 150,
                        '_rgb_detail': bytes.fromhex('e47c') * 150,
                        '_detail_bands': bytes([30, 40, 50]) * 150}
            with mock.patch.object(ta, 'StreamingAnalyzer', side_effect=AssertionError('FFT forbidden')):
                data = analyze_imported(source, imported)
            self.assertEqual(data['waveform_model'], DETAIL_MODEL)
            self.assertNotIn('spectral_bands', data)
            target = ta.store(root / 'cache', 'test', source, data)
            self.assertIsNotNone(target)
            self.assertTrue(ta.is_ready(root / 'cache', 'test', source))
            self.assertTrue(ta.verify_complete(root / 'cache', 'test', source))
            raw = ta._read_container_manifest(root / 'cache', 'test', source)[1]
            self.assertEqual(raw['range_mip_divisors'], [1])
            self.assertNotIn('spectral_24', raw['sections'])
            detail = ta.load_detail(root / 'cache', 'test', source)
            self.assertEqual(detail['_audio_detail']['mip_divisors'], list(DETAIL_DIVISORS))
            self.assertEqual(detail['_blue_detail'], imported['_blue_detail'])
            self.assertEqual(detail['_rgb_detail'], imported['_rgb_detail'])
            self.assertEqual(detail['_detail_bands'], imported['_detail_bands'])
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
            self.assertIsNone(analyze_imported(source, imported, lambda: False))
            # Corruption is a miss, never a partially trusted playable contour.
            packed = bytearray(target.read_bytes()); packed[-1] ^= 255; target.write_bytes(packed)
            self.assertIsNone(ta.load_detail(root / 'cache', 'test', source))

    def test_partial_import_does_not_select_supplement(self):
        self.assertFalse(complete_import({'sample_rate': 150, '_blue_detail': b'1'}))
        self.assertFalse(complete_import({'sample_rate': 150, '_blue_detail': b'1',
                                         '_rgb_detail': b'123', '_detail_bands': b'123'}))

    def test_header_origin_uses_encoder_facts_and_rejects_unknown_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'fixture.mp3'
            frame = bytearray(417)
            frame[:4] = bytes.fromhex('fffb9000')
            frame[36:40] = b'Info'
            frame[40:44] = (1).to_bytes(4, 'big')
            frame[44:48] = (100).to_bytes(4, 'big')
            frame[48:57] = b'LAME3.100'
            frame[69:72] = ((576 << 12) | 1152).to_bytes(3, 'big')
            source.write_bytes(frame + bytes.fromhex('fffb9000') + bytes(413))
            hint = mp3_origin(source)
            self.assertEqual((hint.lead_frames, hint.decoded_frames, hint.sample_rate),
                             (2257, 100 * 1152 - 576 - 1152, 44100))
            frame[48:52] = b'FAKE'
            source.write_bytes(frame + bytes(417))
            self.assertEqual(mp3_origin(source).lead_frames, 0)
