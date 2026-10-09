# Close-zoom waveform review — October 2, 2026

This is a historical experiment/acceptance record. The accepted October 6
policy remains in the October 9 build, now accepted for normal use. Current
release state is in [HANDOFF](HANDOFF.md); no diagnostic window remains open.

## Reference and scope

Sonny supplied the 16:40:53 recording of rekordbox displaying STOP CAPPIN v15
in BLUE, RGB and 3BAND. “Redraw” means changing the visible shape when zooming;
the reported fuzz occurs during playback. Evidence is retained locally in
`build/redraw-review/`; the recording, source audio and canonical cache were
read-only. No device discovery or writes occur in the comparison harness.

The first candidate corrects two independent rendering issues:

- Close detail uses the physical-pixel source footprint. The old half-pixel
  override selected unresolvable 1-ms cells at Retina 384. The compact ladder
  now selects 2 ms there and retains 1 ms at 576–1500. The recording's RGB
  contour fit at approximately 750 physical pixels/second supports 2 ms:
  robust residual 5.58 pixels versus 8.16 for 1 ms. At approximately 1500 and
  3000 physical pixels/second the same comparison favors retaining 1 ms.
  These fitted contours are evidence for sampling choice, not exact pixel parity.
- BLUE integrates its nonlinear radial color over the visible pixel, splitting
  at coverage/radial branch changes and using four-point Gauss-Legendre
  quadrature. The prior coverage-centroid approximation had errors up to 0.054
  in linear color in the seeded oracle; the new integration stays below 0.000060
  in those cases. Independent dense quadrature and subdivision invariance now
  exercise the actual Metal helper and final compositor. Coverage stays exact;
  color quadrature is numerical, not an assertion of exact rekordbox equivalence.

Source samples, compact caches, authored PWV3/PWV5/PWV7 streams, palette and
transport are unchanged. 3BAND retains its original 150-Hz rows and layer scales.
A fit to the new 3BAND recording favors linear rows over held rows, but exact
layer-height/interpolation parity remains unproven; no speculative scale retune
was promoted.

## Remaining outline report and discovered buffer defect

Sonny initially said the first B “improves both,” then reported remaining RGB
fuzz, strongest at 864 but present at other zooms. A private monotone-cell
interpolation experiment rounded the contour and did not resolve the reported
moving outline. It is rejected and is not in production source. Neither the
private perpendicular-stroke nor direct-cell experiment was promoted.

The previous recenter test returned to the same origin after a distant seek.
It did not compare the same song position rendered from *different overlapping*
buffer origins. That missing case exposed a real defect: floating-point source
coordinates formed relative to each tile could round to opposite sides of a
held cell boundary. The real-track overlap audit found differences up to 124
8-bit color codes, with 56 of 120 comparisons exceeding two codes.

Attribute preparation now supplies small clock blocks aligned to absolute song
coordinates. Each block contains CPU-double-derived integer sample anchors and
local phases for the waveform and signed detail, including the existing legacy
source-origin correction. The GPU uses the same local coordinate arithmetic in
every overlapping tile. This changes neither the source mip nor reconstruction
kernel. On the same 120 comparisons across all modes and five close zooms, the
maximum difference is now one code. The native synthetic drawable regression
also checks overlapping origins across all thirteen zooms.

## Validation boundaries

The final `./check.sh --native --render` passed on October 2 after the clock
correction: **1,079 tests, zero skips, zero expected failures** (1,038 offscreen
and 41 Cocoa). Native build, pyflakes and engine mypy passed; the offscreen
render was inspected. The expanded native test covers different overlapping
origins at every zoom plus a non-whole-second target at 864. Running the earlier
renderer against the strengthened test produced six failing overlap subcases;
that intentional mutation evidence is in `overlap-mutation.json`.

An earlier 1,079-test pass did not cover this defect. Passing the corrected gate
establishes the numerical/render contracts, not complete visual acceptance.
At that checkpoint, muted B was the canonical-clock correction, installed as signed
personal build 122 after the final gate. Sonny's subsequent October 3 playback review rejected it as a fix for the visible artifact.
That installation is superseded; current rollback and launch/state evidence are
in [HANDOFF](HANDOFF.md).

The earlier first-B temporal capture submitted 1,620 frames across all three
modes at 384/864/1500, with zero audio underruns and zero GPU-deadline drops.
There were six dropped presentations and twelve presentation gaps above 12.5 ms;
no claim of zero presentation drops or performance improvement is justified.
That capture predates the clock correction. No physical-camera recording was
made. Exact rekordbox pixel parity, broader-track/panel acceptance and the
retained five-probe profile approximation remain open.

## October 3 playback rejection

After restarting the comparison, Sonny reported the canonical-clock B was the
same or worse. The outline/fuzz and apparent color slipping while scrolling
are descriptions of the same unresolved artifact. The overlap correction fixes
a demonstrated numerical defect but did not establish a perceptual improvement.
At that checkpoint, build 122 remained installed. Color-only diagnostic work was private under
`build/color-lock-review/`; it is not a released correction.

The RGB height-weight diagnostic changes real-track captures by at most one code
at 384/864/1500; BLUE and 3BAND are pixel-identical. This does not explain the
reported large color movement and is not promoted. A reference-motion audit then
registers successive recording frames: reliable RGB/BLUE pairs translate in
whole logical points (two physical pixels), with essentially unchanged pixels
after alignment. The ignored whole-point motion prototype tests that policy
without changing source detail, hue or silhouette. It preserves 3BAND and leaves
audio/grid clocks continuous. Sonny reports colors are better, but darker shades
against lighter shades remain weird inside thick filled regions. This is partial
improvement, not acceptance; it is not installed. Numerical and capture evidence lives in
`build/color-lock-review/`, including `reference/*-motion.json`. This evidence
supersedes no production policy until the diagnostic is accepted.

Registration summary: 256 of 284 inspected adjacent pairs met the stated
registration-quality cutoff (99th-percentile integer-aligned error below ten
codes); all 256 use even physical-pixel shifts. Median mean aligned errors are
0.014–0.019 codes across the three segments. Unmatched pairs remain excluded
evidence, not assertions of identical behavior during every transition. In the
prototype's sampled fractional phases at RGB/BLUE 864, the checked interior
crop is pixel-identical while the quantized scroll position stays fixed. The
39 captured 3BAND frames are identical to build 122. This verifies the intended
diagnostic distinction, not user acceptance of the motion tradeoff.

Follow-up RGB color fits against three recording frames do not establish a
better replacement interpolation. Broad Gaussian color averaging worsens these
fits; linear, monotone and smoothstep variants differ modestly with no consistent
winner. Recording transfer, alignment and color-model residuals remain material.
Do not turn those fits into a palette calibration or justify blurring detail.
Evidence is `reference/color-fit.json` and `reference/color-curve-fit.json`.

A three-second native capture for each mode at 864 records 1,080 submitted
frames, zero audio underruns, seven dropped presentations and two associated GPU
deadline misses. It is diagnostic evidence, not a clean cadence gate. The initial
interior registration also includes continuously moving grid/playhead overlays
and needs correct quantized alignment across tile recentering; its maxima cannot
be attributed to the fill shader without separating those effects.

The corrected registration restores the canonical-origin offset at tile
recentering and excludes observed grid/playhead columns at the lane boundary.
Across 359 adjacent pairs per mode, the sampled opaque interior is byte-identical
after whole-point alignment: 4,177,712 RGB pixel comparisons and 4,037,448 BLUE
comparisons, maximum channel error zero. Evidence is
`pixelstep-motion/registered-fill-overlay-excluded.json` and its analysis script.
This supports rigid movement of the sampled fill in this prototype, not physical
display acceptance or a claim that the user's perceived artifact is absent.

The private RGB isolation window is `preview_isolation.py`: A retains the
whole-point prototype; B puts the imported colors on a flat strip; C keeps the
original detailed contour with one constant color. It distinguishes moving color
transitions from carrier geometry. The controls are deliberately not proposed
product changes. The preceding column-coverage probe initially remained
unpresented because the user located the remaining effect inside filled regions.

## October 6 isolation feedback

Sonny viewed the reopened isolation diagnostic and reported the same effect
**only in C**, the solid-red detailed contour. B's moving color strip did not
show it. This supports investigating contour motion/coverage while retaining the
authored colors. It is not proof of a specific coverage bug or hardware cause.

`preview_contour.py` compares the previous whole-point renderer with the private
RGB column-coverage probe. Both use the same source cells, selected LOD and
scrolling. B uses the physical column's minimum/maximum bounds to increase
coverage only where the prior interval already had nonzero coverage; it retains
the original coverage-conditioned color. The effect can thicken fine detail and
is not an exact-area renderer. It must not be labeled a mathematical correction.
The preview offers Solid red and RGB, starts at 864, and is muted/read-only.
Existing 117-frame captures show BLUE and 3BAND remain byte-identical to the
whole-point control. Sonny accepted B: “B reduces fuzz; detail looks good.”
The implementation is promoted to source, with two robustness guards: normalize
reversed endpoint inputs and never reduce existing minimum-stroke coverage.
The captured 39 RGB frames of the original candidate already had no brightness
reductions; these guards protect edge cases rather than intentionally retuning
that accepted appearance. The October 6 gate passed all 1,081 tests with zero
skips or expected failures. All 117 production comparison frames match the
accepted prototype byte-for-byte, including BLUE and 3BAND controls. The signed
personal app was safely installed and relaunched at 17:25 CDT; curated state and
cache inventories were verified intact. Its Git-derived build number remains
122, but its source fingerprint is now
`4a3255767e1904e2ff6f2c26193da4e8dd545a9f35f5d966d897c094f821e958`.
That first installation was superseded by the accepted smaller-increment build
below; current startup/state/rollback evidence is in [HANDOFF](HANDOFF.md). The October 9 wrap-up publishes the accepted follow-ups; these historical
measurements do not establish blanket physical acceptance.

### Post-install mid-zoom stepping

Sonny subsequently reports visible steps about five zoom-outs from the maximum,
which maps to 256 if counting five decrements (384 if counting the starting stop).
The whole-logical-point rule was applied too broadly: on a 120-Hz display, 256
points/s must alternate integer travel while 24 points/s holds a point across
several refreshes. The earlier rounded-position regression intentionally checks
that policy, so its pass does not establish smooth motion at these stops.

The private `preview_midmotion.py` retains RGB column presentation but restores
fractional RGB/BLUE offset through 384; 576–1500 and all 3BAND behavior are the
installed control. This is a single motion-policy experiment, not source-detail
filtering. It was subsequently rejected and never promoted or installed. Its own libraries and
capture evidence are under ignored `build/color-lock-review/`.

The 105-frame comparison per variant samples RGB/BLUE/3BAND at
24/128/256/384/576/864/1500. All 65 close/3BAND control frames are byte-identical.
Across the wider RGB/BLUE fractional-phase probes, actual attribute travel versus
the presented source-frame travel differs by at most 0.000001 physical pixels
in B, versus 1.501 physical pixels in the installed rounded policy. Evidence:
`midmotion-comparison.json`. This establishes the intended offset change, not
perceptual smoothness or absence of returning fuzz; the live A/B asked both.

Sonny rejected this fractional-motion B: stepping stopped, but fuzz became
very bad. It remains private and uninstalled. Continuous phase combined with
conservative occupied-column coverage is therefore not an accepted pairing;
precise numerical travel did not establish perceptual quality.

The next probe, `preview_pixelmotion.py`, instead retains the exact installed
contour/color presentation and permits one physical pixel per increment through
384, rather than one logical point (two physical pixels on the Retina display).
This halves the quantization unit while retaining a fixed physical raster phase.
Close stops and 3BAND are controls. It is an incremental-motion tradeoff, not
continuous motion, and far-zoom holds can still occur. This probe was subsequently
accepted and promoted as recorded below; the fractional probe remained rejected.

Pixel-motion evidence: 65 close/3BAND control frames are byte-identical to the
installed policy. All 24 sampled wider RGB/BLUE phase comparisons, including
the detailed edges, are byte-identical after their integer translation and
grid/playhead exclusion. `pixelmotion-comparison.json` records these checks.
The A/B opened at RGB 256 to check whether the smaller steps were smooth
enough without renewed fuzz; that diagnostic window is now closed. Raster identity does not answer that physical
perception question on the user's behalf.


### Accepted smaller increments

Sonny accepted the one-physical-pixel B for the stepping/fuzz comparison:
“yupppp that did it.” The production offset now uses one physical pixel through
384 points/s, retaining whole logical points at 576–1500 and continuous 3BAND.
This matches the accepted probe exactly; coverage, source detail and colors are
unchanged. The Cocoa regression adds a 0.75-physical-pixel input displacement
that must advance exactly one pixel at wider zooms while preserving aligned
edge pixels. That catches the previous two-physical-pixel Retina hold. It also
retains the smaller-phase hold and all-mode/all-zoom recenter checks. The final
`./check.sh --render` passed all 1,081 tests (1,040 offscreen + 41 Cocoa), zero
skips/expected failures. The old renderer fails the strengthened RGB-256 check
with a 242-code difference against a limit of 2 (`pixel-step-mutation.json`).
All 105 final production frames match the accepted probe byte-for-byte across
seven zooms and all three modes (`pixelmotion-production-parity.json`). The
signed personal app was installed at 17:59:33 CDT and relaunch/state-verified at
17:59:53. Source fingerprint:
`22addecb92f959619af609ec97e265b41b90c8a61a5c8c6e3125bbe6ba6bd8bc`.
Library bytes and cache inventories/indexes match postquit through postlaunch.
Far-zoom pixel holds remain
an explicit limit of integer motion; user acceptance does not imply continuous
subpixel motion at every zoom or exact rekordbox parity.
