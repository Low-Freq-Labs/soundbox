import unittest

from research.cdj_export.anlz_bridge import export_contract


class AnlzBridge(unittest.TestCase):
    def test_maps_v4_units_and_preserves_all_memory_cues(self):
        master = bytes(
            list(range(24)) + [255] * 8 + [128] * 8 + [64] * 8)
        data = {
            "beats": [[1, 125, 140.125], [9, 554, 140.0]],
            "cues": [
                {
                    "kind": "hot", "slot": 0, "position_ms": 125,
                    "end_ms": -1, "type": "single", "label": "A",
                    "color": [300, 20, -2],
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
        self.assertEqual(got["pwv5_rgb"], bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual(len(got["pwv7_mid_high_low"]), 6)

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
