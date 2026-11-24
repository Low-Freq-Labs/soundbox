"""Native BLUE palette facts and dark encoded-color compositing regressions.

Pinned numeric palette samples came from the independently inspected native
reference; no private recording/binary/cache is opened by routine tests. RGB
oracles remain in test_waveform_gpu_math and are unchanged.
"""
import platform
import unittest

import numpy as np

from tests import test_waveform_gpu_math as _math

# Native 61-entry float32 ramps: outer purple/cyan, inner purple/cyan.
# These include interior entries, not just the production seven anchors.
_NATIVE_LUT_SAMPLES = [
    (0, 0, [51.0, 134.0, 206.0]),
    (0, 1, [51.90000153, 129.5, 204.6000061]),
    (0, 5, [55.50000763, 111.5, 199.00003052]),
    (0, 10, [60.00001526, 89.0, 192.00006104]),
    (0, 17, [64.90001678, 76.39997864, 196.20010376]),
    (0, 20, [67.00000763, 70.99996948, 198.00012207]),
    (0, 29, [69.7000351, 61.99996948, 230.400177]),
    (0, 30, [70.00003815, 60.99996948, 234.00018311]),
    (0, 40, [80.00003815, 59.99998474, 255.00024414]),
    (0, 50, [80.00003815, 59.99998474, 255.00024414]),
    (0, 60, [80.00003815, 59.99998474, 255.00024414]),
    (1, 0, [51.0, 134.0, 206.0]),
    (1, 1, [49.5, 136.19999695, 204.1000061]),
    (1, 5, [43.5, 144.99998474, 196.50003052]),
    (1, 10, [36.0, 155.99996948, 187.00006104]),
    (1, 17, [35.30001068, 176.99996948, 196.1000824]),
    (1, 20, [35.00001526, 185.99996948, 200.00009155]),
    (1, 29, [33.20000839, 195.90002441, 204.50009155]),
    (1, 30, [33.00000763, 197.00003052, 205.00009155]),
    (1, 40, [32.00002289, 204.0, 209.00003052]),
    (1, 50, [27.50001526, 206.62496948, 209.87496948]),
    (1, 60, [23.00000763, 209.24993896, 210.74990845]),
    (2, 0, [255.0, 255.0, 255.0]),
    (2, 1, [251.5, 255.0, 255.0]),
    (2, 5, [237.5, 255.0, 255.0]),
    (2, 10, [220.0, 255.0, 255.0]),
    (2, 17, [130.39997864, 169.60002136, 255.0]),
    (2, 20, [91.99996948, 133.00003052, 255.0]),
    (2, 29, [71.29994202, 97.90002441, 221.70002747]),
    (2, 30, [68.99993896, 94.00002289, 218.00003052]),
    (2, 40, [64.99992371, 87.00005341, 209.00009155]),
    (2, 50, [63.99993896, 94.00002289, 212.00012207]),
    (2, 60, [62.99995422, 100.99999237, 215.00015259]),
    (3, 0, [255.0, 255.0, 255.0]),
    (3, 1, [238.0, 255.0, 255.0]),
    (3, 5, [170.0, 255.0, 255.0]),
    (3, 10, [85.0, 255.0, 255.0]),
    (3, 17, [68.89997864, 223.5, 223.5]),
    (3, 20, [61.9999733, 210.0, 210.0]),
    (3, 29, [26.89996338, 169.5, 210.0]),
    (3, 30, [23.0, 165.0, 210.0]),
    (3, 40, [23.0, 165.0, 210.0]),
    (3, 50, [23.0, 165.0, 210.0]),
    (3, 60, [23.0, 165.0, 210.0]),
]

# (spectral input, native outer/inner display codes, spectral radius fractions).
# Expected endpoints were evaluated with the independent 61-entry native table
# audit, not by reading or evaluating the production shader.
_NATIVE_PALETTE_CASES = [
    ((0, 0, 0), [51, 134, 206], [51, 134, 206], [0.0, 0.0]),
    ((1, 0, 0), [80, 59, 255], [170, 255, 255], [0.0, 0.0]),
    ((0, 1, 0), [23, 209, 210], [23, 209, 210], [1.0, 0.0]),
    ((0, 0, 1), [51, 134, 206], [255, 255, 255], [1.0, 1.0]),
    ((1, 1, 1), [51, 134, 206], [110, 194, 230], [0.6666666666666666, 0.3333333333333333]),
    ((1, 0.5, 0.25), [56, 107, 197], [56, 107, 197], [0.42857142857142855, 0.14285714285714285]),
    ((1, 0.25, 0.5), [61, 85, 193], [234, 255, 255], [0.42857142857142855, 0.2857142857142857]),
    ((0.25, 1, 0.5), [35, 161, 189], [67, 223, 233], [0.8571428571428571, 0.2857142857142857]),
    ((0.5, 0.25, 1), [56, 107, 197], [255, 255, 255], [0.7142857142857143, 0.5714285714285714]),
    ((0.5, 1, 0.25), [42, 147, 194], [50, 180, 211], [0.7142857142857143, 0.14285714285714285]),
    ((0.01, 0.7, 1), [32, 201, 207], [221, 255, 255], [0.9941520467836257, 0.5847953216374269]),
    ((1, 0.001, 0.02), [80, 59, 255], [67, 91, 214], [0.020568070519098924, 0.019588638589618023]),
    ((-1, 0.3, 0.5), [23, 209, 210], [238, 255, 255], [1.0, 0.625]),
]


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'production Metal renderer requires arm64 macOS')
class WaveformBluePalette(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _math.WaveformGPUMath.setUpClass.__func__(cls)

    def execute(self, rows, source=None):
        return _math.WaveformGPUMath.execute(self, rows, source)

    def test_four_native_lut_families_match_independent_interior_reference_samples(self):
        rows=np.zeros((len(_NATIVE_LUT_SAMPLES),64),dtype=np.float32)
        rows[:,0]=16
        rows[:,1:3]=[(family,index) for family,index,_ in _NATIVE_LUT_SAMPLES]
        expected=[rgb for _,_,rgb in _NATIVE_LUT_SAMPLES]
        # Native float32 table interpolation accumulates up to3.4e-5 display
        # codes; this tolerance remains far below one 8-bit display step.
        np.testing.assert_allclose(self.execute(rows)[:,:3],expected,atol=7e-5,rtol=0)

    def test_spectral_palette_branches_and_unclassified_base_match_native_reference(self):
        rows=np.zeros((len(_NATIVE_PALETTE_CASES),64),dtype=np.float32);rows[:,0]=13
        rows[:,1:4]=[values for values,_,_,_ in _NATIVE_PALETTE_CASES]
        expected=np.array([[*(np.array(outer)/255),*(np.array(inner)/255),*radii]
                           for _,outer,inner,radii in _NATIVE_PALETTE_CASES])
        actual=self.execute(rows)[:,:8]
        # Flooring a native float32 interpolant at an integer boundary can
        # differ by one code across CPU/GPU arithmetic; hue/radius branches
        # still have their independently fixed native endpoint expectations.
        np.testing.assert_allclose(actual[:,:6],expected[:,:6],atol=1/255+1e-6,rtol=0)
        np.testing.assert_allclose(actual[:,6:],expected[:,6:],atol=2e-6,rtol=0)
        np.testing.assert_allclose(actual[0,:6],np.tile(np.array([51,134,206])/255,2),atol=1e-7,rtol=0)
        np.testing.assert_array_equal(actual[0,6:],0)

    def test_palette_is_ratio_based_and_negative_channels_do_not_invent_energy(self):
        rows=[]
        for scale in (.0001,.1,1,1000):
            row=np.zeros(64);row[:4]=[13,scale,.25*scale,.5*scale];rows.append(row)
        actual=self.execute(rows)[:,:8]
        np.testing.assert_allclose(actual,np.tile(actual[0],(4,1)),atol=1e-7,rtol=0)
        rows=np.zeros((2,64));rows[:,0]=13;rows[:,1:4]=[[-1,.3,.5],[0,.3,.5]]
        np.testing.assert_array_equal(*self.execute(rows)[:,:8])

    def test_native_overview_palette_white_underlay_and_tip_gradient_are_independent_of_geometry(self):
        palette=np.array([[16,96,152],[16,128,184],[8,144,248],[16,184,232],
                          [120,192,224],[144,192,248],[144,192,248],[208,232,248]])/255
        background=np.array([13,14,18])/255
        rows=[];expected=[]
        for color in palette:
            for outer,front in ((1,0),(1,.4),(.6,.2)):
                for distance in (0,2.2,5.5,10.45,11):
                    for antialias in (.25,.5):
                        row=np.zeros(64);row[:11]=[15,outer,front,*color,distance,antialias,*background]
                        rows.append(row)
                        radial=distance/11;band=antialias/11
                        white=np.clip((front+band-radial)/(2*band),0,1)*.5 if front else 0
                        under=background*(1-white)+white
                        strength=1-.5*np.clip(radial/outer,0,1)
                        expected.append(under*(1-strength)+color*strength)
        np.testing.assert_allclose(self.execute(rows)[:,:3],expected,atol=3e-6,rtol=0)

    def test_overview_complete_compositor_keeps_dark_partial_coverage_and_silence(self):
        background=np.array([13,14,18])/255;color=np.array([16,96,152])/255
        rows=[];expected=[]
        for weight in (0,.00001,1):
            for coverage in (0,.000001,.001,.01,.1,.5,1):
                row=np.zeros(64);row[:6]=[17,weight,coverage,0,.5,1]
                row[16:21]=[1,0,*color];rows.append(row)
                vivid=1-(1-coverage)**3 if weight else 0
                expected.append(_math.linear_color(background+(color-background)*vivid))
        actual=self.execute(rows)[:,:3]
        np.testing.assert_allclose(actual,expected,atol=3e-6,rtol=0)
        np.testing.assert_array_equal(actual[:7],np.tile(actual[0],(7,1)))

    def test_blue_main_encoded_edges_remain_nearly_black_without_an_opacity_floor(self):
        color=np.array([80,60,255])/255
        rows=[];expected=[];coverages=(0,.000001,.00001,.0001,.001,.005,.01,.06,.18,.5,1)
        for coverage in coverages:
            row=np.zeros(64,dtype=np.float32)
            row[:7]=[2,2,.3+(.5/60)*(1-2*coverage),60,.5,1,92]
            row[8:10]=-.3;row[15:17]=.3;row[22:24]=[0,1]
            row[29:35]=np.tile(color,2);row[53:56]=color
            rows.append(row)
            lifted=coverage*(1+1.5*(1-coverage/.18)**2) if coverage<.18 else coverage
            expected.append(_math.linear_color(color*lifted))
        actual=self.execute(rows)
        np.testing.assert_allclose(actual[:,4:7],expected,atol=3e-6,rtol=0)
        displayed=_math.encoded_color(actual[:,4:7])
        np.testing.assert_array_equal(actual[0,4:7],0)
        self.assertLess(float(displayed[1].max()),.00001)
        self.assertLess(float(displayed[4].max()*255),1)
        self.assertTrue(np.all(np.diff(displayed.max(1))>=0))
        # Reintroducing linear-light edge blending would brighten dark ink.
        c=actual[6,0];lifted=c*(1+1.5*(1-c/.18)**2)
        wrong=_math.encoded_color(_math.linear_color(color)*lifted)
        self.assertGreater(float(wrong.max()-displayed[6].max()),.12)
