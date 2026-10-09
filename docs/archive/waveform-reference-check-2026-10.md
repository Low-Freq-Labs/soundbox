# Supplied waveform recordings — October 1 verification

Historical evidence; dated status and instructions below apply only to their
original checkpoints. Current behavior and priorities are in [PLAYER](../PLAYER.md),
[ROADMAP](../ROADMAP.md) and [HANDOFF](../HANDOFF.md).
Original `build/` evidence paths now resolve beneath the external archive listed
in [project layout](../PROJECT_LAYOUT.md). Inspect old scripts before reuse.

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
cyan requirement. The gamut expected failure was retired October 1. The
independent signed-coverage expected failure was then corrected October 2; see
[the release gate](../test-release-gate.md).

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
and [HANDOFF](../HANDOFF.md).

## October 8 RGB color recovery — accepted

The supplied SoundBox/rekordbox screenshots of HotinHere_Ruudy_Finalv2 expose
quiet green rendered near (0,74,25) where PWV5 already carries (0,6,2)/7, or
approximately (0,219,73). This is not evidence for a track-specific palette
boost: compact five-bit height is zero across that passage, and the weighted
color path fabricated black before the existing close-color blend. The quiet
signed contour remains valid. Color-only recovery now divides every positive
weight exactly and uses the authored color at zero weight. Source rows, contour,
coverage, footprint, LOD, alpha and motion policies are unchanged; no recook.

Sonny accepted B in the muted same-track comparison: **“B looks right.”** The
Metal oracle covers all 512 encoded RGB combinations, thirteen zooms and five
weights including zero/tiny values. A native drawable regression compares eight
representative colors at zero versus full compact height with identical signed
geometry at 32/128. Read-only same-track captures across thirteen zooms and five
positions/phases per zoom retain all 65 BLUE and 65 3BAND frames byte-for-byte.
The screenshots have different framing and no embedded ICC profile, so this is
a demonstrated darkness correction, not a claim of exact RGB pixel parity with
rekordbox. Evidence: ignored `build/palette-review/`.
