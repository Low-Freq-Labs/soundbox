"""External-reference limit for BLUE, distinct from arithmetic correctness.

The Oct 1 Nuclear recording contains thousands of saturated cyan waveform
pixels (R<=24, G/B>=235 after BT.709-to-sRGB conversion). The native mapping
must be capable of reaching that broad color region before claiming parity.
No recording, live audio, AppData or rekordbox files are opened by this test.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.support.native_source import native_deck_source

import numpy as np
from scipy.optimize import linprog


class WaveformBluePalette(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('Clang required for native color mapper')
        source = native_deck_source()
        mapper = re.search(r'    static WaveSample blueWaveSample\(uint8_t packed\) \{.*?\n    \}',
                           source, re.S)[0]
        white = re.search(r'whiteLinear = display_srgb_to_linear\(\s*float3\((.*?)\)\)',
                          source, re.S)[1]
        # Only color endpoints are under test. The height function's output
        # cannot affect r/g/b; stub it without duplicating color arithmetic.
        code = '''#include <cstdint>
#include <cstdio>
struct WaveSample {float height,r,g,b;};
float displayWaveformHeight(float h){return h;}
MAPPER
int main(){
 for(int i=0;i<8;i++){
  auto v=blueWaveSample(uint8_t((i<<5)|31));
  printf("%.9g %.9g %.9g\\n",v.r,v.g,v.b);
 }
 float white[]={WHITE};
 printf("%.9g %.9g %.9g\\n",white[0],white[1],white[2]);
}
'''.replace('MAPPER', mapper).replace('WHITE', white)
        with tempfile.TemporaryDirectory(prefix='sb-blue-palette-') as temp:
            root = Path(temp); cpp = root/'palette.cpp'; binary = root/'palette'
            cpp.write_text(code)
            subprocess.run([compiler, str(cpp), '-o', str(binary)], check=True,
                           capture_output=True, text=True)
            output = subprocess.check_output([str(binary)], text=True)
        palette = np.array([[float(v) for v in row.split()] for row in output.splitlines()])
        # Include black and actual clear color: a conservative superset of
        # every edge interpolation, radial mix and coverage-composited color.
        cyan = re.search(r'cyanLinear = display_srgb_to_linear\(\s*float3\((.*?)\)\)', source, re.S)[1]
        cyan = [float(v.strip()) for v in cyan.split(',')]
        palette = np.r_[palette, [cyan, [0, 0, 0], [13/255, 14/255, 18/255]]]

        def linear(v):
            return np.where(v <= .04045, v/12.92, ((v+.055)/1.055)**2.4)

        colors = linear(palette)
        bounds = linear(np.array([24, 235, 235])/255)
        cls.feasibility = linprog(np.zeros(len(colors)),
                                 A_ub=[colors[:, 0], -colors[:, 1], -colors[:, 2]],
                                 b_ub=[bounds[0], -bounds[1], -bounds[2]],
                                 A_eq=[np.ones(len(colors))], b_eq=[1],
                                 bounds=(0, None), method='highs')
        if cls.feasibility.status not in (0, 2):
            raise RuntimeError(cls.feasibility.message)

    def test_blue_palette_can_reach_recorded_saturated_cyan(self):
        # This gamut gate is necessary but not sufficient: GPU tests also
        # require the thin-interval compositor to actually reach cyan.
        self.assertTrue(self.feasibility.success,
                        'BLUE palette cannot reproduce reference cyan')
