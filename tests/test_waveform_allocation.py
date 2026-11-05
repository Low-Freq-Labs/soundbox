"""Frozen complete analyzer output through channel-allocation changes."""
import hashlib
import json
import unittest

import numpy as np

from soundbox.track_analysis import StreamingAnalyzer, _range_mip_sections, RANGE_MIP_DIVISORS
from soundbox.waveform_detail import DETAIL_DIVISORS


class RangeMipParity(unittest.TestCase):
    def test_every_cell_matches_direct_song_anchored_extrema(self):
        rng = np.random.default_rng(4107)
        for count in (1, 2, 3, 7, 13, 17, 63, 64, 65, 127, 193, 257, 1025):
            low = rng.integers(-32768, 32768, count, dtype=np.int16)
            high = rng.integers(-32768, 32768, count, dtype=np.int16)
            low[0] = -32768
            high[-1] = 32767
            # Reordered, repeated and non-multiple levels must also retain the
            # original base-clock cells, including their partial last cell.
            for divisors in (RANGE_MIP_DIVISORS, DETAIL_DIVISORS,
                             (64, 3, 2, 12, 1, 4, 6, 13, 64, 5)):
                with self.subTest(count=count, divisors=divisors):
                    packed, actual_count = _range_mip_sections(
                        low.astype('<i2').tobytes(), high.astype('<i2').tobytes(), divisors)
                    self.assertEqual(actual_count, count)
                    for divisor in divisors:
                        expected_low = np.asarray([
                            min(low[start:start + divisor])
                            for start in range(0, count, divisor)], dtype='<i2').tobytes()
                        expected_high = np.asarray([
                            max(high[start:start + divisor])
                            for start in range(0, count, divisor)], dtype='<i2').tobytes()
                        self.assertEqual(packed[f'detail_minimum_d{divisor}'], expected_low)
                        self.assertEqual(packed[f'detail_maximum_d{divisor}'], expected_high)

    def test_empty_truncated_and_invalid_inputs_keep_the_existing_contract(self):
        self.assertEqual(_range_mip_sections(b'', b'\0\0'), ({}, 0))
        packed, count = _range_mip_sections(b'\x00\x80x', b'\xff\x7f\0\0', (1, 2))
        self.assertEqual(count, 1)
        self.assertEqual(packed['detail_minimum_d2'], b'\x00\x80')
        self.assertEqual(packed['detail_maximum_d2'], b'\xff\x7f')
        for divisor in (0, -1):
            with self.assertRaises(ValueError):
                _range_mip_sections(b'\0\0', b'\0\0', (divisor,))


class WaveformAllocationParity(unittest.TestCase):
    def test_all_packed_sections_and_metadata_retain_preoptimization_bytes(self):
        # Captured from the original analyzer, including clipped signed extrema,
        # non-finite sanitization, multichannel geometry and partial final cells.
        cases=(
            (8000,1,'nonfinite',False,'38a16c8f2cef84e15f599a8b527cd34002a737d560c295e2057488005e6eba77'),
            (44100,2,'antiphase',True,'b48cd09e66aea2a16415ddff49f67f80311531c3572b1ce318a768096611368d'),
            (48000,8,'finite',True,'4bff25966b1e552b887e5742ca2d008b64887815d99f4a67b95dac5d82eb773b'),
            (96000,4,'nonfinite',False,'4bf40801ac58d6acade6d7926123edf1824dc62b912f1c4e308278a837eda8ea'),
        )
        def packed(raw):
            if isinstance(raw,bytes):
                return dict(bytes_sha256=hashlib.sha256(raw).hexdigest(),length=len(raw))
            raise TypeError(type(raw).__name__)
        for rate,channels,kind,chunked,expected in cases:
            with self.subTest(rate=rate,channels=channels,kind=kind):
                frames=rate//5+137;grid=np.arange(frames,dtype=np.float32)[:,None]
                data=(((grid*37+np.arange(channels,dtype=np.float32)[None,:]*113)%2048)-1024)/768
                if kind=='antiphase':data[:,1]=-data[:,0]
                data[0,:]=-3;data[1,:]=2;data[2,:]=-0.0
                if kind=='nonfinite':
                    data[3,0]=np.nan;data[4,0]=np.inf;data[5,-1]=-np.inf
                original=data.copy();analyzer=StreamingAnalyzer(rate)
                with np.errstate(all='ignore'):
                    if chunked:
                        position=0
                        for size in (1,37,511,1283,len(data)):
                            analyzer.feed(data[position:position+size]);position+=size
                            if position>=len(data):break
                    else:analyzer.feed(data)
                    result=analyzer.finish(120)
                np.testing.assert_array_equal(data,original)
                raw=json.dumps(result,sort_keys=True,separators=(',',':'),default=packed).encode()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),expected)

    def test_empty_mono_and_multichannel_chunks_remain_empty(self):
        for shape in ((0,),(0,1),(0,8)):
            with self.subTest(shape=shape):
                analyzer=StreamingAnalyzer(48000);analyzer.feed(np.empty(shape,dtype=np.float32))
                self.assertIsNone(analyzer.finish(120))
