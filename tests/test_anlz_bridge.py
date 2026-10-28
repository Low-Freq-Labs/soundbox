import unittest

from research.cdj_export.anlz_bridge import export_contract


class AnlzBridge(unittest.TestCase):
    def test_maps_v4_units_and_preserves_all_memory_cues(self):
        master = bytes(
            list(range(24)) + [255] * 8 + [128] * 8 + [64] * 8)
        data = {
            "beats": [[1, 125, 140.12], [2, 554, 140.0]],
            "cues": [
                {
                    "kind": "hot", "slot": 0, "position_ms": 125,
                    "end_ms": -1, "type": "single", "label": "A",
                    "color": [255, 20, 0],
                },
                *[
                    {
                        "kind": "memory", "slot": i,
                        "position_ms": 1000 + i, "end_ms": -1,
                        "type": "single", "label": str(i),
                    }
                    for i in range(10)
                ],
            ],
            "heights": [.5, 1.0],
            "colors": [[1, 2, 3], [4, 5, 6]],
            "_spectral_24": master,
            "sample_rate": 150.0,
            "eligibility": {"eligible": True, "format": "wav"},
        }
        got = export_contract(data)
        self.assertTrue(got["eligible"])
        self.assertEqual(got["pqtz"][0].tempo_x100, 14012)
        self.assertEqual(got["pqtz"][1].beat, 2)
        self.assertEqual(len(got["pcob_pco2"]), 11)
        self.assertEqual(got["pcob_pco2"][0].rgb, (255, 20, 0))
        self.assertEqual(got['display_waveform_fields'].rgb, ((1, 2, 3), (4, 5, 6)))
        self.assertNotIn('pwv5_rgb', got)
        self.assertNotIn('pwv7_mid_high_low', got)

    def test_invalid_musical_data_is_rejected_without_substitution(self):
        for row in ([9, 0, 120], [1, -1, 120], [1, 0, 700], [1, 0, 140.125], [1, 0, float('nan')]):
            with self.subTest(row=row), self.assertRaises(ValueError):
                export_contract({'beats': [row]})
        for patch in ({'slot': 16}, {'slot': -1}, {'kind': 'unknown'},
                      {'color': [300, 0, 0]}, {'position_ms': -1},
                      {'type': 'loop', 'end_ms': 0}):
            cue = dict(kind='hot', slot=0, position_ms=0, end_ms=-1, type='single')
            cue.update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                export_contract({'cues': [cue]})

    def test_native_streams_are_byte_preserving_and_separate_from_display_fields(self):
        from research.cdj_export.anlz_bridge import NativeWaveformStreams
        streams = NativeWaveformStreams(b'\xff', b'\xe3\x7c', b'\x80\xff\x00')
        self.assertEqual(streams.pwv7, b'\x80\xff\x00')
        with self.assertRaises(ValueError):
            NativeWaveformStreams(b'\xff', bytes(3), bytes(3))

    def test_overflow_hot_cue_is_never_demoted_to_memory(self):
        got = export_contract({
            "cues": [{
                "kind": "hot", "slot": 8, "position_ms": 500,
                "end_ms": -1, "type": "single", "label": "I",
                "color": [1, 2, 3],
            }],
            "eligibility": {"eligible": True},
        })
        (cue,) = got["pcob_pco2"]
        self.assertEqual(cue.kind, "hot")
        self.assertEqual(cue.hot_cue, 9)
