"""Waveform section extraction preserves the established compressed bytes."""
from copy import deepcopy
import unittest

import zstandard

from soundbox.waveform_sections import decode_sections, encode_sections


class WaveformSections(unittest.TestCase):
    def test_established_payload_and_independent_section_decode(self):
        streams = {'overview': b'first waveform', 'detail': bytes(range(32))}
        rows, payload = encode_sections(streams)
        self.assertEqual(payload.hex(),
            '28b52ffd200e71000066697273742077617665666f726d'
            '28b52ffd2020010100000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f')
        self.assertEqual(rows, {
            'overview': {'offset': 0, 'size': 23, 'raw_size': 14,
                         'sha256': '4d7a06be390c2c3ffbef66bb54968b89fd0e4959341057e1950e9cce4ea49173'},
            'detail': {'offset': 23, 'size': 41, 'raw_size': 32,
                       'sha256': '630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd'}})
        for name, row in rows.items():
            section = payload[row['offset']:row['offset'] + row['size']]
            self.assertEqual(zstandard.ZstdDecompressor().decompress(section), streams[name])
        self.assertEqual(decode_sections(payload, {'sections': rows}), streams)

    def test_damaged_or_mismatched_section_never_returns_partial_streams(self):
        rows, payload = encode_sections({'overview': b'complete', 'detail': b'protected'})
        bad_hash = deepcopy(rows)
        bad_hash['detail']['sha256'] = '0' * 64
        bad_size = deepcopy(rows)
        bad_size['detail']['raw_size'] += 1
        for data, sections in ((payload, bad_hash), (payload, bad_size),
                               (payload[:-1], rows)):
            with self.subTest(data=data, sections=sections):
                self.assertIsNone(decode_sections(data, {'sections': sections}))
