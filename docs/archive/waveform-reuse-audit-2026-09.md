# Rekordbox waveform reuse — September 29, 2026

Historical evidence; dated status and instructions below apply only to their
original checkpoints. Current behavior and priorities are in [PLAYER](../PLAYER.md),
[ROADMAP](../ROADMAP.md) and [HANDOFF](../HANDOFF.md).
Original `build/` paths are historical evidence locators. Some private archives
have since been retired; [project layout](../PROJECT_LAYOUT.md) distinguishes
retained receipts from deleted material. Old paths do not guarantee retention
or authorize replay; inspect retained scripts before reuse.

Historical app-control, source-write, installer, restore, replay and cleanup
instructions below grant no present authorization. Current permissions and
protected recovery bases are governed by [the current handoff](../HANDOFF.md).

## Requested direction and current boundary

The initial request preferred saved rekordbox streams and small storage, with a
visual comparison before changing the renderer/cache. **The September 29 evening
clarification makes visual fidelity the acceptance boundary:** Sonny rejects the
filled-envelope compromise and requires the detailed rekordbox BLUE/RGB appearance
and its separate 3BAND geometry, including zooming and motion. Reuse saved data
where it meets that reference; do not retire detail merely to meet a storage
target. Exact fallback parity remains a validation target. Production implementation is installed locally; the exact build and final
validation status are recorded in [HANDOFF](../HANDOFF.md). No whole-library invalidation is used.

## Follow-up: thick/jagged close detail

Sonny rejected the installed contour as too thick/jagged. Doubling/tripling
the retained cell rate was tested in isolated caches/renderers: it thinned the
trace but worsened the grid-registered maximum-zoom reference bound fit
(11.91 pixels RMSE at 1 kHz versus 14.67 at 2 kHz and 17.34 at 3 kHz, after
fitting vertical scale). Connecting the original cells linearly also worsened
the fit (18.66). These are geometric comparisons, not complete visual acceptance.

The follow-up preserves 1-ms data and changes reconstruction: close stops use
half a physical pixel of source support, avoiding early max-pooling at 384.
The signed interval minimum changes from 3.6 points (7.2 Retina pixels) to one
physical pixel before coverage. A real drawable regression captures a constant
thin RGB carrier; the earlier installed renderer produces eight colored rows
and the updated renderer stays within three including antialiasing.

No new cache profile or analysis migration is required. 3BAND geometry remains
independent. Isolated variants and native evidence are in the git-ignored
build/waveform-detail-stepup directory. See HANDOFF for installed provenance
and final motion/check results. Exact rekordbox identity remains unestablished.

## September 30 color-pulse correction

Sonny now accepts the detail and reports renewed red/white/color pulsing in
`~/Downloads/IMG_0376.MOV`. Inspected representative frames across its 55-second
Nuclear Bass Face playback. This is an iPhone HEVC/HLG recording at approximately
60 fps, not a paired native drawable trace: camera/display sampling and exposure
cannot be separated from renderer changes using it alone. The local rekordbox
6 installation reports 6.8.7.0303; the earlier restricted session could inspect
files but could not launch/control the GUI. No new live rekordbox comparison is claimed.

The shader contained a concrete normalization defect. It prints
`color * max(abs(minimum), abs(maximum))` onto its song lattice, but previously
recovered color by dividing by the magnitude of the **interpolated signed
bounds**. At a polarity or dominant-boundary crossing that denominator can
shrink while the premultiplied color weight does not. RGB channels brighten
and may clip toward white; BLUE's edge tint has the same incorrect recovery.

The candidate retains the original weight in a previously unused float of the
same 32-byte attribute. Interpolation carries color and weight together. It
changes no signed bounds, minimum stroke, LOD, detail rate, cache, transport or
3BAND compositor. Source tests verify the kernel stores/interpolates that weight.
`tests/test_waveform_color_math.py` compiles the actual extracted Metal recovery
expression as Clang vector arithmetic, without AppKit/GPU. Constant/mixed-color
fixtures cover sign changes, dominant-boundary switches, same-polarity slopes
and a zero endpoint across 1001 phases. The old expression fails; the candidate
has zero error against the expected weighted interpolation in those fixtures.
This is arithmetic evidence, not Metal compiler/drawable acceptance.

A read-only CPU model of the checksummed Nuclear compact cache, 52–75 seconds,
finds 6794 adjacent lattice pairs with over 1% spurious midpoint color gain at
each close stop. Maximum modeled gain is about 2.14 before clamping; at 384,
the 99th percentile is about 1.42. These are attribute-level values, **not**
measured whole-pixel brightness gains. Evidence is under the ignored
`build/waveform-pulse-investigation/` directory: original source snapshot,
video contact sheet, `color-recovery-report.json` and reproducible CPU scripts.
The live cache was read, not rewritten; its inspected SHA-256 is
8c027a4d0b0c415b66e0a793e11d9a5ff21311c0feb984280032d5058df69161.

The September 30 audit also found a coverage approximation, subsequently
corrected October 2 (see [the release gate](../test-release-gate.md)). At this
historical checkpoint the signed compositor overwrote joint coverage with the smaller of separately
integrated upper/lower half-planes. These are not mathematically equivalent
when both boundaries cross a pixel. A synthetic diagonal narrow-interval test
of the extracted shader helper yields approximately 0.509375 versus 0.0125
from dense integration of the pointwise overlap. This does not measure the
error in Sonny's recording. The candidate deliberately leaves geometry/coverage
unchanged pending native comparison because detail is accepted; do not describe
that legacy approximation as exact or claim the color fix solves every wiggle.

The first restricted-session validation passed bare `./check.sh` (1022 tests /
13 skips), CPU arithmetic and the hermetic UI render. Cocoa aborted before deck
initialization and the offline Metal toolchain was unavailable. That earlier
ad-hoc package was not installed. Those access restrictions are historical.

The resumed September 30 session passed the same bare gate plus all 40 Cocoa/
Metal tests. The runtime Metal compiler accepted the shader. Before/candidate
captures contain 507 exact drawable frames per renderer: all thirteen stops,
RGB/BLUE/3BAND, 52.837/64/74 seconds, four fractional phases and recenter returns.
3BAND and every recenter return are pixel-identical. The correction removes
excess brightness (maximum reduction 64 RGB display codes and 24 BLUE codes in
these captures); no compared RGB/BLUE pixel brightens. Source geometry is unchanged.

Moving captures cover 39 segments / 3510 frames per renderer. Both have zero
underruns and reported GPU deadline misses, but before/candidate have 14/15
zero-time dropped presentations and maximum actual gaps of 20.833/16.667 ms.
These are bounded traces, not zero-drop or physical-panel acceptance. Native
stills and sampled moving frames were inspected. A fresh paired phone/rekordbox
recording was not made.

The personal app was rebuilt with `Soundbox Self-Signed`, safely installed and
relaunched at 23:00:47. The source fingerprint remains
`f01b7c9ffb1748a5e24713268f23a14ab04e007541251ca840118f179bde7601`.
See HANDOFF for exact executable, state/rollback evidence and the two index
changes observed during the fresh library refresh.

**September 30 installed review rejected playback motion:** dark regions make a
wavy/pulsating pattern centrally in long thick sections and at the ends of shorter
sections. It is strongest in red/yellow/orange/white, weaker in blue/green, and
visible only during playback. Paused detail remains accepted. Do not reduce
resolution or blur detail to conceal it. The normalization correction is valid,
but it does not resolve the reported artifact.

An isolated `joint-source/` experiment uses the existing joint interval resolve
without replacing its coverage by the legacy half-plane minimum; it also includes
all attribute knots for RGB/BLUE. It was not in production or installed at that
checkpoint. Its 507
native frames and the registered phase comparisons have not established a decisive
improvement. Bilinear image registration itself filters captured pixels and is
not an independent flicker ground truth. Flat-color phase controls keep solid
fields identical; the selected actual-detail crop's integrated brightness varies
under 0.013%, which does not rule out local motion aliasing. The muted
`live_diagnostic.py` separates original colors, flat-red/same-detail,
original-colors/solid-shape and flat-red/solid-shape for physical observation.
All scripts and captures remain under the ignored investigation directory.

The October 1 section below supersedes the pending motion diagnosis: the
complete-corner candidate received physical A/B acceptance. The joint-coverage
variant remained experimental at that checkpoint; October 2 later corrected
production coverage with independent final-fragment oracles and a fresh native
comparison. Broader 1:1 acceptance still requires pairing
native drawables/timestamps with physical-panel observation and a matched
rekordbox recording. Thirteen point-per-second stops are not proof of identical
rekordbox visible-time windows. Decoder-origin parity outside known headers,
fallback analysis, BLUE transfer and full pixel parity remain open.

## October 1 targeted fractional-phase sampling correction

Sonny reports the remaining wiggle primarily at 256 (white/green/blue ends,
smaller warm-color effect), lesser slow oscillation at 384 and “756” (provisionally
576), and faster oscillation at 864. Other stops look good in that review. This is
physical feedback about motion, not blanket acceptance of every mode/track.

The four stops share the fixed five-probe profile path. As the song lattice moves
fractionally, those probes can omit interior lattice corners and reconstruct a
different contour. `audit_moving_knots.py` compiles the actual extracted profile
builder: a synthetic periodic triangular profile with true above-threshold area
0.25 can disappear at a half-lattice phase under the fixed probes. The experimental
complete-knot path retains area 0.25 through all 101 tested phases. This is a
synthetic sampling proof, not a measurement of flicker in Nuclear Bass Face.

The initially isolated `knots-source/` candidate enables complete corners
only for the affected RGB/BLUE stops. It preserves existing coverage/color math,
source detail, LOD, stroke width and clocks. The integration footprint remains
one physical pixel; no blur, adjacent-mip blend or resolution reduction is used.
Its 507 exact native captures preserve every unaffected zoom and every 3BAND
frame byte-for-byte. Candidate stills were inspected. Sonny then reported
“b seems rock sold” in the live A/B review; the narrow sampling change was
promoted to production. This accepts the reviewed motion, not every track or mode.

`live_ab.py` supplies a muted single-backend comparison at 52.837–75 seconds.
A is installed sampling and B is the complete-corner candidate. Its separate
ignored `ab-source/` library has an explicit diagnostic uniform/setter; eight
self-checks verify both selections are pixel-identical to their standalone
renderers at 256/384/576/864. The diagnostic ABI is not a production change.
Only the sampling condition/comment is promoted; the diagnostic uniform/setter
stays outside production. `tests/test_waveform_profile_math.py` compiles the
actual profile builder and checks fractional-phase peak area and complete
corners, with the unchanged branches as positive controls for the former
missing-peak defect. The previous joint-coverage experiment remained separate
until the October 2 coverage correction documented in the release gate.
The personal app was safely installed and relaunched October 1 at 00:18:35.
All 507 production frames exactly match B. Bare checks passed (1023 tests,
13 skips), as did 40 Cocoa tests. Twelve moving segments captured 1080 frames
with no underruns or GPU deadline misses, two dropped presentations and a
maximum 16.667-ms actual gap. Library, index bytes and cache inventories remain
identical across the install. Exact build and rollback evidence is in HANDOFF.

## Production implementation — September 29 evening

Sonny accepted the interactive candidate and authorized production integration.
Complete imported streams plus grid now select the compact supplement before the full
analyzer: source audio is decoded once in bounded blocks into exact 1-ms signed
midpoint cells. No duplicate filter bank, spectral master or beat detection is
run. The overview comes from PWV5. Incomplete/unsupported imports keep the full
fallback. Compact V5 stores only one detail level and reconstructs its zoom mips
in memory. It remains source-keyed, section-checksummed and atomically published.
At the September implementation, old V5 remained ready and only selected local
tracks upgraded on interactive load. The October 9 follow-up adds automatic
background upgrades for eligible local records; the old cache remains playable.
New/missing tracks and explicit reanalysis use the same import-first route.

Four complete records, generated only in the isolated developer cache:

| Track | Existing bytes | Compact bytes |
|---|---:|---:|
| Nuclear Bass Face | 2,726,778 | 920,577 |
| WORLD OF WONK VIP | 2,865,993 | 962,740 |
| High & Scared | 2,468,703 | 874,056 |
| Bad Decisions | 2,415,742 | 854,927 |

These measurements include the complete compact record and establish approximately 65–66% reduction
for these four tracks, not a whole-library savings estimate. No source, ANLZ,
library.json or live cache was modified by the measurement.

The renderer incorporates the accepted candidate's linear transfer, full RGB
channels/interpolation, held signed close detail and original 150-Hz 3BAND rows.
Maximum zoom is 1500 points/s. Source clocks are now coupled: recognized MPEG-1
LAME Info/Xing headers supply packet length/encoder delay/valid-frame count;
native playback and supplemental analysis restore the same lead-in. Native seeks
map back to decoded PCM and duration includes the lead. Header probing stays on
the decode thread and AVAudioFile must match the expected rate/frame count.
Unknown headers retain the old decoded clock. The 20-track read-only survey
supports the same alignment family as Nuclear Bass Face; it does not establish
all encoder variants. The 529-sample synthesis convention is also documented in
[LAME's decoder source](https://github.com/lameproject/lame/blob/master/frontend/get_audio.c).

Automated source-safety, chunk-boundary, corruption, cancellation, native origin,
seek/duration/end and worker-routing checks accompany the change. See HANDOFF
for the final gates, native captures and installed fingerprint. Exact pixel
parity, every codec/header variant and long-session physical-panel acceptance
are not claimed.

Installed-app acceptance loaded Nuclear Bass Face paused and upgraded only its
cache: 2,726,778 → 920,681 bytes, with section/payload checksums valid. Its live
metadata accounts for the small difference from the isolated record above.
No analysis files were added or removed. All other files remained ready.

The following records the earlier isolated investigation; statements describing
unfinished production work below are historical to that stage.

## Earlier evening investigation — historical candidate stage

Native GUI capture now works. The earlier permission failure below is historical.
Sonny supplied three maximum-zoom screenshots of **NGHTMRE & Subtronics — Nuclear
Bass Face (feat. Boogie T)**, SoundBox track `265095720`, near `00:52.8`, and
`~/Desktop/Screen Recording 2026-09-29 at 9.14.12 PM.mov` (the real filename uses
a narrow no-break space before `PM`). The recording is 3006×444, 39.292 seconds;
its nominal 120-fps stream has approximately 57.49 actual frames/second. Do not
call it a 120-fps physical-panel capture. The original movie is read-only.

The three still originals and local experiments are preserved beneath
`build/waveform-native-20260929/`, especially `nuclear-reference/`,
`nuclear-matched/`, `matched-source/`, `matched-motion/` and `comparison-*.png`.
These are git-ignored developer evidence, not installed code. The original
Bad Decisions / High & Scared corpus is still missing; both source tracks were
located through their current library paths and rendered without hydration.

- The stored-only experiment fixed the unsigned lower-bound bug and removed the
  extra packed-height transfer **in a disposable copy of the native source**.
  Its filled BLUE/RGB envelope fails the newly explicit visual requirement.
- The screenshot harness could capture the preceding mode when source position
  stayed unchanged. It now waits for a new actual presentation as well as the
  requested frame, then allows WindowServer to expose the new surface. Earlier
  multi-mode screenshots must not be treated as reliable by filename alone.
- Beat markers in the fresh screenshots establish 3000 physical pixels/second
  (1500 points/second at Retina scale two). The production maximum remains 1536;
  only the disposable comparison build accepts 1500 for equal-scale evidence.
- Nuclear Bass Face's cached PWV3/5/7 bytes are exact prefixes of the current
  sidecars, with ten trailing rows truncated by the old audio-duration limit.
  There is no shifted/color-corrupt prefix. This does not validate every library
  cache. SoundFile and AVAudioFile excerpts agree at the same source frame.
- The current independent MP3 contour is early relative to the imported grid.
  A fit against the supplied RGB screenshot strongly favors approximately
  **2256–2257 leading samples at 44.1 kHz**. This track's header has a 1152-sample
  Info frame and a 576-sample LAME encoder delay; adding the decoder delay explains
  that result. This is a specimen-specific finding, **not permission to add a
  constant delay to every MP3 or change playback clocks without tests**.
- A candidate retains stereo-midpoint signed extrema in 1-ms cells, uses a
  linear amplitude transfer, holds close cells with spatial antialiasing, and
  interpolates the full-range imported RGB colors. It preserves the oscillating
  carrier. These are measured candidate choices, not a new canonical cache model.
- Fresh 3BAND per-layer fits favor the original 150-Hz rows with a small row-time
  offset over the production paired-row rule. The production eight-percent
  artificial facet adds visible teeth to smooth decays. The candidate removes
  it. The existing seven-color membership palette matches the supplied colors.
- Grid-registered RGB signed-bound measurements improved from approximately
  35.84 to 11.91 pixels RMSE and from 0.747 to 0.975 correlation, after fitting
  one vertical scale for the different lane framing. These measurements exclude
  guides and do **not** establish pixel parity, color parity, or user acceptance.
- Nine short native motion segments (BLUE/RGB/3BAND at 48/384/1500) completed
  810 exact drawable captures with zero dropped presentations, GPU deadline
  misses or audio underruns; maximum actual presentation gap was 8.334 ms.
  No phone/panel acceptance was performed. The old four-pixel LOD model was
  excluded from the experiment's timing summaries because it does not describe
  the candidate.
- The one-track 1-ms signed stream alone is 781,008 raw bytes / 766,635 bytes at
  Zstandard level 6, against a 2,726,778-byte complete current V5 record. This
  is not a complete replacement record or a library-wide savings estimate.

The matched RGB comparison has been presented for Sonny's review. A separate
muted native window is available through the git-ignored
`build/waveform-native-20260929/interactive_preview.py`: fixed rekordbox reference
above, live candidate below, mode selector, zoom, motion playback and reset to
52.837 seconds. It creates no MainWindow and writes no AppData; its captured
`interactive-preview.png` was inspected. The installed app remains build 119.
This is a one-track research preview, not a production implementation. Remaining
work includes motion/zoom visual review, independent track/format validation,
correct coupling of any MP3 analysis-origin correction to audio/transport/grid
timelines, robust production implementation, and the compact-cache design and
safe migration. Never ship the per-specimen prototype constants or silently
move only the visual trace while leaving audible playback on another timeline.

## Read-only findings

Manifest-only inventory of 2,924 V5 files (not an active-library coverage audit):

| Stored content | Compressed bytes |
|---|---:|
| Whole files including headers | 11,566,745,722 |
| SoundBox signed-range mip sections | 7,833,825,925 |
| SoundBox 24-band sections | 2,844,176,406 |
| BLUE/RGB/3BAND packed streams | 500,453,789 |
| SoundBox overview sections | 339,529,395 |

2,841 records report all three imported modes; 83 report generated modes.
These counts do not prove current source identity or payload integrity across
the entire library. The two established comparison tracks did receive an exact
byte comparison: their cached BLUE/RGB/3BAND streams equal the current EXT/2EX
payloads. No audio decoding, source writes, cache cleanup or library mutation
was performed by the successful data preview.

The current implementation imports AFTER analyzing audio in both
`WaveformFillWorker.run` and `WaveformWorker.run`. `track_analysis.is_ready`
requires the high-rate signed-range mip set. `store` rejects records without it.
`load_detail` also reads the spectral master. Thus importing exact PWV bytes
does not currently avoid decoding, storage or a SoundBox-derived contour.

Renderer findings, not yet corrected:

- `blueWaveSample` and `rgbWaveSample` call `displayWaveformHeight` on packed
  heights, applying the SoundBox power curve/ceiling despite earlier documentation
  claiming packed heights bypass it. PWV7 uses separate per-band scaling.
- `wave_attribute_kernel` replaces BLUE/RGB geometry with the signed audio
  trace whenever it exists. Imported geometry is not the sole shape authority.
- Without audio detail, `lowerHeight` initially equals positive `height`, but
  the BLUE/RGB fragment path interprets it as the signed interval minimum.
  Removing detail alone therefore does not establish a valid symmetric fallback.
- Native upload may alter zero PWV5 rows using SoundBox overview geometry.
  A pure imported path must not silently apply this independent-audio repair.

The [primary format investigation](https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html)
describes the 150-row/s streams and their packed height/color fields. That fact
does not establish everything rekordbox's desktop renderer reads or how it
reconstructs close zoom. Do not infer a quality ceiling or claim pixel parity
from the sample rate alone.

## Initial preview and validation (historical)

`scripts/preview_rekordbox_waveforms.py` reads only explicitly supplied analysis
sidecars and writes a PNG, standalone interactive HTML and SHA-256 stream proof
to an explicit output directory outside the analysis directory. It opens no
audio, AppData or devices, and creates no Qt/native window. The PNG/HTML plots
saved rows with linear interpolation, a stated diagnostic palette and no
SoundBox detail. A second RGB lane isolates the current packed-height curve.
These are data plots, not screenshots of either application's renderer. The
3BAND normalization is diagnostic, not validated native vertical calibration.

Local outputs:

Wrap-up preserved copies beneath git-ignored `build/waveform-preview-20260929/`
with the same two subdirectories, so the next session need not depend on `/tmp`.

- `/tmp/soundbox-waveform-preview/bad-decisions/{index.html,waveforms.png,proof.json}`
  at 25.597 s, two seconds visible.
- `/tmp/soundbox-waveform-preview/high-and-scared/{index.html,waveforms.png,proof.json}`
  at 27 s, one second visible.

Both PNGs were inspected. Parser/preview/reference tests passed (15 tests,
three missing-private-corpus skips); generated JavaScript passed `node --check`.
The bare `./check.sh` passed native build, lint, engine typing and 1,015 tests
with 13 environment/fixture skips. This does not establish GUI/native visual
acceptance; no live device fixture was enabled for this run.
The native harness gained an explicit `--stored-waveforms-only` diagnostic
which bypasses independent audio detail and zero-row repair, checks imported
provenance, and records upload sizes. Its render is **not accepted**: even the
baseline native window failed to open in this restricted session (macOS
pasteboard/HI services connection failure, exit 134). The old Desktop reference
corpus is also absent. The live app, cache format, and installed build remain
unchanged. Native comparison requires a session with GUI access and fresh or
relocated reference captures.

## Initial implementation/acceptance sequence (historical)

The original plan below predates the September 29 production migration and
October 1 sampling correction. Its restricted-session permission statements
are historical; native capture, signing and personal installation now work.

1. Compare exact imported geometry with actual rekordbox at the same track,
   time, mode and physical zoom. Correct pure-stream signed bounds and audit
   display transfer/interpolation before deciding what additional detail is
   truly necessary. Validate scrolling as well as still frames.
2. Make compact imported data a complete ready record. Read valid ANLZ modes
   first; only generate missing modes. Keep a small independent local copy,
   explicit per-mode provenance and checked refresh when sidecars change.
3. Use the same renderer for generated and imported modes; assess generated
   fallback similarity with independent reference captures. Do not promise
   that the existing SoundBox filterbank reproduces rekordbox's analyzer.
4. Convert one validated existing cache at a time, atomically. Preserve cues,
   grids, ownership, stable identities and playable old records on failure;
   never hydrate audio merely to discard redundant sections. Verify new records
   before retiring old data. Do not globally invalidate caches or lower the
   full-audio storage guard to force the migration through.
5. Run regression/visual gates, build and safely install when environment
   permissions permit, verify real curation, then measure actual savings.
   Current permissions cannot replace `/Applications/Soundbox.app` or write
   live AppData. No installation or live conversion was attempted.

The larger rekordbox-alternative direction still requires editable grids/cues,
robust independent library preparation and independently accepted CDJ export.
This request does not authorize writing the rekordbox library or `/PIONEER`.
