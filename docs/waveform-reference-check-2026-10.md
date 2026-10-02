# Supplied waveform recordings — October 1 verification

Sonny asked to verify 3BAND detail and BLUE colors after accepting the scrolling
sampling fix. The recordings are local, read-only inputs on Desktop:

- `Screen Recording 2026-09-29 at 9.14.12 PM.mov` (39.292 seconds): rekordbox
  Nuclear Bass Face RGB zoom sweep.
- `Screen Recording 2026-10-01 at 12.36.06 AM.mov` (72.903 seconds): the same
  rekordbox track in BLUE, then 3BAND, with an RGB view at the end.

Actual filenames contain the macOS narrow space before AM/PM. Both are
3006×444 H.264, 4:2:0 limited-range BT.709. Sampled frames and contact sheets were
inspected; video is variable-cadence (~57.4 fps average despite 120 fps nominal),
so this is appearance evidence, not a clean 120-Hz presentation measurement.
For color measurements the 20-second frame was explicitly converted from the
recording's declared BT.709 transfer to sRGB. Compression/chroma subsampling and
capture color management limit fine palette fitting; do not tune every code
value from an unconverted video frame.

## 3BAND: imported detail intact, exact appearance not established

Fresh read-only comparison against Nuclear's existing ANLZ proves the cached
streams are fully byte-identical, not merely equal-length or matching prefixes:
PWV3 29,290 bytes, PWV5 58,580 bytes, PWV7 87,870 bytes. The latter contains
29,290 independent three-band rows. No analysis/source/cache file was changed.

The renderer uses the full 150-Hz PWV7 detail at close zoom, with independent
low/mid/high heights. A new production-Metal regression uses alternating peaks
in different bands to verify that the close tap preserves every row and its
linear intermediate values at 576/864/1152/1500. It would detect pair pooling
or shared-height substitution in that helper; it does not claim the whole
compositor is a perfect rekordbox match.

At 60 seconds into the October 1 recording, fitted horizontal scale is about
3000 physical pixels/second. A high-layer contour fit using all 150-Hz rows
scores about 2.45–2.47 pixels robust RMSE; paired 75-Hz rows score about
3.85–4.16 pixels. This supports retaining the full-rate detail. The near tie
between held and linear 150-Hz fits does **not** establish an exact interpolation
rule. These fits optimize alignment and vertical scale; they do not validate
SoundBox's per-band gain constants. Mixed-color classification, compression,
geometry/zoom alignment and layer-height calibration remain limitations.

Verdict: no lost imported rows or demonstrated half-rate detail defect. Exact
3BAND visual parity is still unverified; do not label the entire mode “correct.”

## BLUE: original mismatch and subsequent correction

In the color-managed 20-second reference frame, the waveform region contains
**6094 pixels** within the broad saturated-cyan box R≤24, G≥235, B≥235.
The pre-correction native `blueWaveSample` mapper produced eight edge endpoints ranging
from roughly (36,87,209) to (61,153,245), then blends them with a pale-white core
and the background.

The convex hull of those colors in linear light cannot reach that cyan box.
The proof executes the actual C++ mapper and solves feasibility over all
interpolation/core/background weights, rather than comparing one chosen frame
or assuming a radial position. Thus the mismatch cannot be repaired by merely
changing coverage, smoothing detail or adjusting the scroll clock. The source
PWV3 bytes are intact; the display mapping is the problem.

The follow-up implements a color-only correction: PWV3 retains its height and
three-bit edge selector, with edge display channels R=.08, G=.07+.78×code/7,
B=.86. The interior blends cyan (0,1,1) to pale white (237/255,1,1) by
`smoothstep(0,.28,halfWidth)`, where halfWidth is normalized source interval
width, not screen pixels. The radial edge blend starts at zero rather than .28, eliminating the broad
white plateau. Sonny found the first .14-start candidate better but still too
white; this additional narrowing follows that feedback.
Coverage, signed bounds, source rate, LOD, footprint, timing and RGB/3BAND remain
unchanged. No waveform cache conversion is needed.

The initial palette/width parameters came from a robust fit to 11,478 interior pixels of
the color-managed close frame, using extracted local extents and approximate
PWV3 clock registration. Per-channel mean absolute error in that diagnostic
model falls from about 43/14/8 to 36/11/5 display codes. The remaining red error,
compressed video, imperfect extent/clock registration and P3 capture conversion
mean this is an empirical improvement, **not exact rekordbox parity**. Do not
present those model errors as matched native-frame pixel errors.

The gamut regression now passes. A production-Metal regression additionally
requires fully covered thin interval centers to reach cyan, preserves white in
broad centers, and proves restoring an unconditional white center violates the
cyan requirement. It is no longer an expected failure; the independent legacy
signed-coverage approximation remains one.

Across 507 actual native captures (all modes/stops, three song positions, four
subpixel phases and recenter), all 338 RGB/3BAND frames are pixel-identical to the
previous installed renderer. BLUE returns exactly after recenter at every stop.
The first candidate reduced sampled maximum-zoom-out near-white pixels from
39.1% to 29.6%, but Sonny still found it too white. The narrower-core revision reduces it to 20.5% (about half the original).
Threshold counts are not perceptual acceptance. Thin detail is preserved. A muted A/B preview uses the
same read-only source/cache with separately loaded old/new native libraries.
Sonny accepted the narrowed version in the live muted preview: “B looks right
now.” It is installed; additional-track and exact-reference comparison remain
open. See HANDOFF for the final signed build and state verification.

Ignored evidence/scripts: `build/waveform-test-audit/` contains probes, extracted
frames, contact sheets, `import-proof.json`, `threeband-high-fit.json`, fitted
contours and `blue-gamut-proof.json`. Recordings and source music stay outside
version control. The test-strengthening and tiny-moment arithmetic correction
are separate, installed work; see [test audit](waveform-test-audit-2026-10.md)
and [HANDOFF](HANDOFF.md).
