# Waveform test audit — October 1, 2026

## Scope and finding

Sonny requested a follow-up after the complete-corner renderer B improved the
playback artifact: the previous tests should have caught it. This audit covers
waveform profile sampling, coverage/color arithmetic, and actual native drawable
translation/recentering. It is not an audit of every SoundBox subsystem.

The previous suite had three material gaps:

- Source-string assertions could preserve a wrong sampling condition while
  passing. Three “coverage math” tests ran a Python transcription, not production
  code, and were removed. The remaining source contracts are structural guards,
  not evidence of correct numerical output.
- `./check.sh` runs Qt offscreen, so native Cocoa tests were skipped. The old
  final “ALL CHECKS PASSED” line concealed that distinction. The ordinary suite
  now executes headless Metal math on supported Macs; `--native` adds an explicit
  fresh Cocoa process whose gate fails on skipped/expected-failure tests. The
  summary explicitly names the omitted gate and reported limitations.
- Developer comparisons depended on private tracks and ignored scripts. A
  tracked synthetic native drawable test now covers all thirteen zooms and
  RGB/BLUE/3BAND with temporary generated audio and analysis, no AppData or device
  discovery. It tests exact integer-pixel translation, nonblank output and exact
  return after a forced attribute-buffer recenter. Preview is audio-frame
  quantized, so translation steps must be representable in both audio samples
  and physical pixels; arbitrary fractional milliseconds are not an exact oracle.

## Executable numerical checks

`tests/test_waveform_gpu_math.py` extracts the complete production Metal shader
and calls its actual helpers through `tests/native/shader_math_runner.mm`.
This is headless GPU execution without AppKit/CoreAudio, library/cache reads or
removable-device discovery. Compiler, shader and GPU failures fail on arm64
macOS; other platforms explicitly skip because this renderer is macOS-only.

Fixed-seed cases cover clipped area/first moments, very small covered pieces,
all complete-corner branches at fractional phases and distant local coordinates,
RGB color recovery across sign changes and silence, joint RGB coverage/color
against independent dense quadrature, BLUE bounds and reversal symmetry.
Quadrature computes pointwise membership without copying the shader's crossing
partitions/moment formula. BLUE's radial centroid evaluation is an approximation;
its bounds/symmetry tests do not claim exact radial quadrature.

Mutation checks deliberately restore the original missing-corner and incorrect
color-weight expressions and prove that their outputs violate the numerical
oracle. Both precision regressions also fail against the saved pre-audit shader,
with no compiler/fixture error. Evidence lives in ignored
`build/waveform-test-audit/mutation-proof.json`.

## Additional confirmed defect and correction

The first GPU reversal test failed: reversing an identical BLUE profile changed
premultiplied linear color by up to 0.00044494 in the seeded cases. The old clipped
moment formula subtracted nearly equal squares/cubes near the end of a segment.
On tiny visible pieces, cancellation corrupted the centroid used to place color.

The correction integrates in local midpoint coordinates: area is midpoint value
× span; first moment is area × midpoint + slope × span³ / 12. Fully covered
pieces use span × midpoint. This is the same integral with stable arithmetic;
it changes neither authored geometry, sampling footprint, LOD, stroke width,
clock nor the accepted separate silhouette policy. The reversal and tiny-piece
oracles pass after the correction.

## Explicit remaining limitations

The production signed silhouette still takes the minimum of separately integrated
upper/lower edges. That is not the integral of their pointwise intersection.
`test_known_signed_coverage_approximation_matches_pointwise_area` asserts the
correct value and is explicitly marked expected-failure. Its GPU execution runs
in setup, outside expected-failure handling, so compiler/GPU failures cannot be
mistaken for that known numerical limit. An unexpected pass fails the suite and
requires retiring the label. This has not been silently changed after Sonny's
visual acceptance; the isolated joint-coverage experiment remains separate.

The retained five-probe RGB/BLUE 64–184 and 3BAND below 1152 branches are also
approximations: complete-corner peak invariance is not claimed there. The CPU
profile regression no longer asserts that their peak loss is desirable. Future
changes require physical comparison, particularly after Sonny accepted those
stops. Neither a passing GPU oracle nor a synthetic drawable proves every real
track, fallback source, display scale, codec, refresh mode or audible/panel
synchronization. Physical rekordbox pixel parity remains a separate gate.

The follow-up [recording verification](waveform-reference-check-2026-10.md)
added an independent 3BAND close-detail GPU test and initially exposed a second
named expected failure: the old BLUE palette could not reach saturated cyan.
The subsequent BLUE correction retires that expected-failure label. A new actual
Metal test requires thin, fully covered interval centers to remain cyan, broad
centers to retain white, and restores the fixed-white bug as a mutation that must
violate the cyan criterion. This distinguishes reachable palette endpoints from
colors the compositor actually produces. Exact visual parity remains unproven.

Native comparison of the arithmetic correction: 507 frames across all stops
and modes differ by at most one code in RGB/BLUE, with all 3BAND pixels identical.
All recenter returns are identical. The Cocoa gate passes 41 tests with no
skips. The moving trace has 1080 frames, zero audio underruns/reported GPU deadline
misses, three dropped presentations and a 41.667-ms maximum actual gap while the
build was active; this is not zero-drop timing acceptance.

Final validation, signed install and curated-state evidence are in HANDOFF.

October 2 wrap-up also pins SciPy in the developer `requirements.txt`: the
BLUE convex-hull oracle and motion-comparison utility require it. This test/tool
dependency is not part of either application's runtime composition.
