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
                384.0, 576.0, 864.0, 1152.0, 1500.0,
            ),
        )
        self.assertEqual(CLOSE_ZOOMS, UI_ZOOMS[8:])

    def test_reports_signed_nyquist_aliases(self):
        self.assertAlmostEqual(_fold_frequency(252.0, 120.0), 12.0)
        self.assertAlmostEqual(_fold_frequency(350.0, 120.0), -10.0)
        self.assertAlmostEqual(_fold_frequency(41.2, 60.0), -18.8)

    def test_mirrors_close_audio_detail_mip_choice(self):
        for rate, expected in ((1000.0, 1), (6300.0, 8)):
            for zoom in (384.0, 576.0, 864.0, 1152.0, 1500.0):
                summary = _signed_carrier_lattice_summary(rate, zoom, 2.0)
                self.assertEqual(summary["selected_divisor"],
                                 expected)
                self.assertAlmostEqual(summary["panel_alias_hz_signed"],
                                       _fold_frequency(rate / summary["selected_divisor"], 120.0))

    def test_mirrors_wide_and_middle_audio_detail_mip_choice(self):
        for zoom, divisor in ((24, 66), (32, 50), (48, 35), (64, 50), (184, 18)):
            with self.subTest(zoom=zoom):
                summary = _signed_carrier_lattice_summary(6300.0, zoom, 2.0)
                self.assertEqual(summary["selected_divisor"], divisor)
