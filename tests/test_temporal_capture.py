import unittest

from scripts.capture_waveform_temporal import (
    CLOSE_ZOOMS,
    UI_ZOOMS,
    _fold_frequency,
    _signed_carrier_lattice_summary,
)


class TemporalCaptureAnalysis(unittest.TestCase):
    def test_accepts_every_real_ui_zoom_while_defaulting_to_close_detail(self):
        self.assertEqual(
            UI_ZOOMS,
            (
                24.0, 32.0, 48.0, 64.0, 92.0, 128.0, 184.0, 256.0,
                384.0, 576.0, 864.0, 1152.0, 1536.0,
            ),
        )
        self.assertEqual(CLOSE_ZOOMS, UI_ZOOMS[8:])

    def test_reports_signed_nyquist_aliases(self):
        self.assertAlmostEqual(_fold_frequency(252.0, 120.0), 12.0)
        self.assertAlmostEqual(_fold_frequency(350.0, 120.0), -10.0)
        self.assertAlmostEqual(_fold_frequency(41.2, 60.0), -18.8)

    def test_mirrors_close_audio_detail_mip_choice(self):
        cases = [
            (384.0, 35, -60.0),
            (576.0, 25, 12.0),
            (864.0, 18, -10.0),
            (1152.0, 13, 60.0 / 13.0),
            (1536.0, 8, -52.5),
        ]
        for zoom, divisor, panel_alias in cases:
            with self.subTest(zoom=zoom):
                summary = _signed_carrier_lattice_summary(
                    6300.0, zoom, 2.0)
                self.assertEqual(summary["selected_divisor"], divisor)
                self.assertAlmostEqual(
                    summary["panel_alias_hz_signed"], panel_alias)
                self.assertGreaterEqual(
                    summary["cells_per_physical_pixel"], 0.20)
                self.assertLessEqual(
                    summary["cells_per_physical_pixel"], 0.26)
