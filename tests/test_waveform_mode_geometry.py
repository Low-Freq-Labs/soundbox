"""Production Metal mode geometry; no AppKit, audio or live state inputs."""
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

import numpy as np

from tests.support.native_source import native_deck_source, waveform_shader

ROOT = Path(__file__).resolve().parents[1]
ZOOMS = (24, 32, 48, 64, 92, 128, 184, 256, 384, 576, 864, 1152, 1500)
PHASES = (0, .375, .875)


def compile_frame_runner(directory):
    """Use the actual CPU uniform ABI instead of maintaining a second layout."""
    deck = (ROOT / 'native/SoundboxDeck.mm').read_text()
    start = deck.index('struct Uniforms {')
    end = deck.index('\n};', start) + 3
    (directory / 'waveform_test_uniforms.h').write_text(
        '#include <cstdint>\nconstexpr unsigned kWaveMipLevels = 32;\n' + deck[start:end])
    binary = directory / 'frame-runner'
    subprocess.run(['clang++', '-std=c++17', '-fobjc-arc', '-framework', 'Foundation',
                    '-framework', 'Metal', '-I', str(directory),
                    str(ROOT / 'tests/native/waveform_frame_runner.mm'), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


def render_mode_frames(binary, source, directory, *, rgb_only=False, varied_bands=False):
    """Drive unchanged production kernel/vertex/fragment entrypoints headlessly."""
    directory.mkdir()
    shader = directory / 'source.metal'
    shader.write_text(waveform_shader(source))
    kind='rgb' if rgb_only else 'all-variable-bands' if varied_bands else 'all'
    result = subprocess.run([str(binary), str(shader), str(directory), kind],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise AssertionError(f'Metal frame runner failed ({result.returncode}): {result.stderr}')
    return json.loads((directory / 'cases.json').read_text())


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'production Metal renderer requires arm64 macOS')
class WaveformModeGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sb-mode-geometry-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        binary = compile_frame_runner(cls.directory)
        cls.frames = cls.directory / 'current'
        cases = render_mode_frames(binary, native_deck_source(), cls.frames)
        cls.cases = {(row['scale'], row['zoom'], row['phase'], row['mode'], bool(row['detail'])): row for row in cases}
        cls.variable_frames=cls.directory/'variable-bands'
        variable=render_mode_frames(binary,native_deck_source(),cls.variable_frames,varied_bands=True)
        cls.variable_cases={(row['scale'],row['zoom'],row['phase'],row['mode'],bool(row['detail'])):row for row in variable}

    def attributes(self, case, directory=None):
        values = np.frombuffer(((self.frames if directory is None else directory) / case['values']).read_bytes(), dtype=np.float32)
        return values.reshape(case['attributes'], 8)

    def test_blue_shares_exact_rgb_geometry_at_all_zooms_scales_and_phases(self):
        self.assertEqual(len(self.cases), len(ZOOMS)*2*len(PHASES)*3*2)
        for detail in (False, True):
            for scale in (1, 2):
                for zoom in ZOOMS:
                    for phase in PHASES:
                        reference = self.attributes(self.cases[scale, zoom, phase, 2, detail])
                        self.assertTrue(np.isfinite(reference).all())
                        for mode in (1,):
                            with self.subTest(scale=scale, zoom=zoom, phase=phase, mode=mode, detail=detail):
                                actual = self.attributes(self.cases[scale, zoom, phase, mode, detail])
                                # Bounds and their original color weight are geometry;
                                # the display palettes deliberately differ.
                                np.testing.assert_array_equal(actual[:, [3, 4, 5]], reference[:, [3, 4, 5]])

    def test_shared_geometry_retains_signed_asymmetry_and_distinct_mode_palettes(self):
        for mode in (1, 2):
            values = self.attributes(self.cases[2, 1500, .375, mode, True])
            self.assertTrue(np.any(values[:, 4] > 0), 'positive-only intervals were filled back to zero')
            self.assertTrue(np.any(values[:, 3] < 0), 'negative-only intervals were mirrored')
            self.assertGreater(float(np.max(np.abs(values[:, 3] + values[:, 4]))), .1)
        rgb = self.attributes(self.cases[2, 92, 0, 2, True])
        for mode in (1, 3):
            self.assertFalse(np.array_equal(self.attributes(self.cases[2, 92, 0, mode, True])[:, :3], rgb[:, :3]))

    def test_three_band_keeps_own_independent_layers_and_ignores_audio_geometry(self):
        for scale in (1,2):
            for zoom in ZOOMS:
                for phase in PHASES:
                    with self.subTest(scale=scale,zoom=zoom,phase=phase):
                        own=self.attributes(self.cases[scale,zoom,phase,3,False])
                        detailed=self.attributes(self.cases[scale,zoom,phase,3,True])
                        np.testing.assert_array_equal(own,detailed)
                        np.testing.assert_array_equal(own[:,3],own[:,4])
                        np.testing.assert_array_equal(own[:,3],own[:,5])
                        # Generated PWV7 has three distinct constant band ratios.
                        # Every source-derived corner must retain all of them.
                        np.testing.assert_allclose(own[:,1],own[:,0]*.58,atol=3e-6,rtol=0)
                        np.testing.assert_allclose(own[:,2],own[:,0]*.21,atol=3e-6,rtol=0)
                        self.assertFalse(np.array_equal(own[:,[3,4,5]],
                            self.attributes(self.cases[scale,zoom,phase,2,True])[:,[3,4,5]]))

    def test_real_srgb_frames_are_complete_finite_and_mode_distinct(self):
        for key, case in self.cases.items():
            with self.subTest(case=key):
                raw = (self.frames / case['pixels']).read_bytes()
                self.assertEqual(len(raw), case['width']*case['height']*4)
                pixels = np.frombuffer(raw, dtype=np.uint8).reshape(case['height'], case['width'], 4)
                self.assertTrue(np.all(pixels[:, :, 3] == 255))
                lane = pixels[50*case['scale']:, :, :3]
                self.assertGreater(int(np.max(lane)), 50, 'blank frames must not satisfy geometry equality')
        for mode in (1, 3):
            self.assertNotEqual((self.frames / self.cases[2, 92, 0, mode, True]['pixels']).read_bytes(),
                                (self.frames / self.cases[2, 92, 0, 2, True]['pixels']).read_bytes())

    def test_variable_pwv7_dominance_retains_own_independent_geometry_at_all_zooms(self):
        dominant=set()
        for scale in (1,2):
            for zoom in ZOOMS:
                for phase in PHASES:
                    with self.subTest(scale=scale,zoom=zoom,phase=phase):
                        own=self.attributes(self.variable_cases[scale,zoom,phase,3,False],self.variable_frames)
                        detailed=self.attributes(self.variable_cases[scale,zoom,phase,3,True],self.variable_frames)
                        np.testing.assert_array_equal(own,detailed)
                        np.testing.assert_allclose(own[:,:3].max(1),own[:,5],atol=3e-6,rtol=0)
                        dominant.update(np.argmax(own[:,:3],axis=1).tolist())
                        self.assertFalse(np.array_equal(own[:,[3,4,5]],
                            self.attributes(self.variable_cases[scale,zoom,phase,2,True],self.variable_frames)[:,[3,4,5]]))
                        # Adding a genuinely different PWV7 stream must not
                        # perturb either protected resident RGB/BLUE renderer.
                        for mode in (1,2):
                            actual=self.variable_frames/self.variable_cases[scale,zoom,phase,mode,True]['pixels']
                            reference=self.frames/self.cases[scale,zoom,phase,mode,True]['pixels']
                            self.assertEqual(actual.read_bytes(),reference.read_bytes())
        self.assertEqual(dominant,{0,1,2},'fixture must exercise each independently dominant band')
