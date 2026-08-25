# SoundBox gotchas & niche facts

Hard-won + researched. **Read before touching workers, playlists, sync, art, or
the iPod path.** Related: `ARCHITECTURE.md`, `ROADMAP.md`.

## Repo hygiene
- **NEVER pipe `./check.sh` through `tail`/`grep` in a `&&` chain** — the
  pipeline exit code is `tail`'s `0`, so a FAILING gate gets masked and the
  build/commit proceeds. (Real incident: a syntax-broken module shipped + got
  committed.) Run `./check.sh` bare (or `> file 2>&1; echo $?`), then build.
- Project uses local `main`, tracking `origin/main` on the Low Freq Labs GitHub
  repository. Preserve any existing dirty tree and inspect `git status` at
  handoff. Do not commit or push unless Sonny explicitly asks; saying
  **“wrap up”** is standing approval to validate, commit the intended completed
  changes, and push the current branch.
  `dist/`, `build/`, `.venv/` are ignored.
- **`AGENTS.md` and `CLAUDE.md` are synchronized runtime equivalents.** A shared
  rule changed in one must change in the other in the same edit. Shipped
  behavior/status belongs in the relevant docs; committed work goes in
  `ROADMAP.md`; exploratory UX belongs in `new-ideas-7-22.md`.
- **Installation must preserve rollback and AppData.** Back up/hash
  `library.json`, gracefully quit, move the old app bundle into a temporary
  rollback directory, install with `ditto`, verify/relaunch/log-check, and
  compare curated state. Never use `rm -rf /Applications/Soundbox.app`; never
  replace or clear SoundBox AppData as part of an app install.

## Qt / PySide6 (these bite HARD)
- **A tooltip must add information.** Never assign a cell's displayed string as
  its tooltip just to repeat fully visible text on hover. Keep tooltips for icon
  meaning, warnings, hidden diagnostics, or genuinely richer content.
- **Classic-iPod playlist IDs are unsigned 64-bit values.** Never carry them in
  a Qt `Signal(int, ...)` (signed 32-bit) or store the Python integer directly
  in a `QVariant`/item role (signed 64-bit). Real Apple IDs exceed both ranges;
  the former prevents delete slots from firing and the latter can abort a tree
  render halfway through. Preserve the exact decimal string across every Qt
  boundary and convert to Python `int` only at the stock-iPod engine call.
- **QThread "destroyed while running" → SIGABRT.** Never let the last Python ref
  to a worker QThread drop *mid-run*. Slots on a worker's CUSTOM signals
  (`finished_all`/`done`/`failed`) run WHILE `run()` is still finishing — nulling
  `self.x_worker` there GCs the thread on its own thread → abort. FIX (in place):
  drop the ref only on the built-in `finished` signal via
  `MainWindow._finish_worker(attr, w)` (main thread, after `run()` returns). Used
  for export/refresh/resync workers + `PlayerBar._wf`. Don't regress it.
- **A custom worker result signal is not a device-completion boundary.** `done`,
  `finished_all`, and similar signals are emitted inside `run()`; the QThread and
  its UI ownership still exist. Device workers store their result, consume it
  only from the built-in `finished` signal, clear the owning attribute first,
  and then enter UI result/finalization handling. Newly connected rows, Eject,
  Sync, success dialogs, and 100% progress must all follow that same boundary.
- Accessing a `deleteLater()`'d worker raises `RuntimeError: Internal C++ object
  already deleted`. Guard with the `_running(w)` helper.
- `shutdown()` (from `closeEvent` + `app.aboutToQuit`) must stop/wait every
  worker BEFORE Qt teardown.
- **There is no Qt playback fallback.** Qt Multimedia is analysis-only through
  `QAudioDecoder` for AAC/ALAC. Native-deck initialization failure must leave
  library management usable, disable player controls, and surface the exact
  diagnostic. Do not reintroduce an engine setting or silent fallback.
- **A consumed Qt key event does not disable Cocoa press-and-hold text input.**
  `QTableView` advertises a macOS text-input client even with edit triggers off,
  and the first track-load transition can reach the native accent service before
  repeat `QKeyEvent`s arrive. Keep `Qt.WA_InputMethodEnabled` false on every
  `NoTypeAhead` browser view, but do not mistake that for the native boundary:
  the packaged entrypoint must also set the bundle-local
  `ApplePressAndHoldEnabled=false` default before importing Qt, and the bundle
  plist declares the same policy. Delegate-created line editors remain separate
  widgets with IME support. Never change the global macOS preference.
- **Native player thread boundary:** no Python callback, Objective-C decode,
  allocation, file I/O, or blocking lock may enter `renderCallback`. Commands
  are atomics/generation-tagged; AVAudioFile runs only on the decode thread.
  Metal runs from CAMetalDisplayLink, not `PlayerBar._scroll_timer`.
- **A static Metal deck must not own a permanent display loop.** Playback,
  loading, preview, and live resize wake the link; every other mutation marks
  one dirty frame and the link pauses again. Python snapshot polling is 8 ms
  only while active and 125 ms while paused. Any new render-state setter must
  request a dirty frame, and any focus-return transport path must pull one
  authoritative native snapshot before repainting Qt state.
- **Qt child resize events are not Metal resize events.** An application-wide
  `Resize` filter sees every control affected by a window layout and must only
  defer background work. `NativeDeckBackend` filters the waveform host itself;
  its one synchronous C call commits the AppKit child frame and Retina
  `drawableSize` before requesting a frame. Returning to generic resize wakes
  causes both a request storm and a one-frame small/stretched waveform.
- **Scrolling waveform filtering happens in COVERAGE space, never by averaging
  per-tap alpha.** Keep one song-anchored peak pyramid and select one
  footprint-matched level with `ceil(log2(footprint))`; blending adjacent
  power-of-two lattices makes them beat against each other as they move.
  Overview, compact fallback geometry, and ultra geometry use this one rule;
  adding a zoom threshold recreates wide/close disagreement. Do not add a
  separate translucent/RMS envelope: it reads as a ghost waveform. Color comes
  from the compact SoundBox RGB filterbank stream: overlapping LP 100 Hz,
  HP 300 + LP 3000 Hz, and HP 1500 Hz paths, with 25/3/1 ms peak holds and
  per-column energy transfer. Its pyramid must amplitude-weight RGB and use
  the same selected mip/song anchor as geometry, otherwise far-zoom colors
  alternate as pixels move. The persisted normalized 24-band master is
  independent analysis and must never be remixed by a renderer or override the
  dedicated RGB rows. Rekordbox PWV5 is a read-only fallback/reference, never
  the source for a successfully analyzed SoundBox color record. Current
  audio-detail and color positions are integer sample anchors + local float
  phase; never collapse either into one absolute float.
- **BLUE and RGB both need a signed range, not filled polarity envelopes.**
  Making peak(L,R) the primary silhouette inflates
  stereo ambience and fills attack/decay valleys; mirroring
  `abs((L + R) / 2)` forces upper/lower correlation even where the real signal
  is strongly asymmetric. Current `soundbox-polarity-range-u16-v4` stores
  independent positive and negative stereo-midpoint peaks in `detail` and
  `detail_lower`, plus signed midpoint minimum/maximum int16 sections and a
  1/16 peak-channel safety floor. Both BLUE and RGB render the signed min/max
  interval; only their PWV3/PWV5 color compositors differ. Drawing raw
  independent maxima is the rejected hairy contour, and smoothing those maxima
  into a crest still leaves the rejected center seam because both sides remain
  filled back to zero. Legacy records may use a compatibility interval only
  while the rolling recook adds the signed sections. Keep the 150 Hz overview
  on complete non-overlapping 1/150-second cells; the spectral FFT window looks
  ahead about 46 ms and must not own geometry. The visually validated
  `height^1.4` display curve, 0.84 ceiling, and continuous low-end shoulder
  apply only during native/Qt upload. The shoulder is 1/64 of full display
  height after a four-LSB knee so very quiet nonzero audio remains visible;
  true zero remains zero and persisted amplitudes stay linear. Packed PWV
  heights are already authored display values and do not receive this
  transfer. Applying an equal display-height floor to both sides at close zoom
  reconnects signed lobes through the center and turns blue transients into
  filled blocks, even when the polarity cache is correct.
- **RGB timeline compensation is decoder-specific, never global.** The
  seven-row / 46.7 ms correction is proven for MP3 and remains there. Applying
  it to WAV/AIFF/FLAC makes lossless color late enough for a kick to inherit a
  preceding clap/snare hue. Current lossless and Qt-decoded player-only paths
  use zero correction; add a format offset only after measuring that decoder
  against an authoritative same-track timeline. Geometry, RGB, grid, and cues
  must still begin at the same track origin.
- **BLUE, RGB, and 3BAND are three source streams and three geometry models.**
  Rekordbox-backed records copy the exact packed PWV3/PWV5/PWV7
  rows into SoundBox-owned canonical storage during one read-only import; other
  sources derive all three during the same analysis pass. Version and readiness
  gate the source set together under
  `rekordbox-blue-rgb-three-band-v1`, so changing the Settings selector must
  only swap an already-resident native pyramid—never recook a track or reopen
  ANLZ. RGB uses PWV5 color and BLUE uses PWV3 edge tint; both use the
  SoundBox signed min/max interval trace. 3BAND instead
  uses three native PWV7 layer heights and must bypass both audio-derived
  contours. Treat the stored
  rows as blue-low/amber-mid/white-high display layers, with measured scales
  of `0.40/0.43/0.40` against Rekordbox's 64-point detail height. BLUE has a
  white core and blue/cyan edge. 3BAND uses the measured blue/brown/cream/
  pale-blue/white membership table, not generic alpha-composited paints.
- **A zero PWV5 row is zero authored RGB geometry.** PWV5 can encode
  both height and RGB as zero while the independent SoundBox overview/ultra
  envelopes prove that the source audio is nonzero. Because the native color
  pyramid amplitude-weights its rows, leaving that row untouched makes the
  correct contour visually disappear. At upload only, use the independent
  overview height to identify audible zero rows, borrow the nearest valid PWV5
  hue, and assign the minimum representable PWV5 height. Never change cached
  bytes or substitute a track-specific timestamp. PWV5 is one RGB color plus
  one compact height per row, not three
  independently layered band heights; only PWV7/3BAND uses the layered
  compositor. Keep PWV5 cell boundaries crisp and song-anchored.
  Cubic-smearing the 150 Hz colors turns visible Rekordbox
  strips into broad pastel gradients. The measured screen expansion retains
  red's 0..14 range while leaving calibrated headroom above green/blue's
  three-bit maxima (`14 / 8 / 8.75` divisors in native display space).
- **The supplied references are not all the same zoom.** Bad Decisions is
  `768` points/s and High & Scared is `1536`; neither is the `184` stop used
  by the rejected harness. BLUE and RGB both need the signed interval cache
  reconstructed linearly at the physical-pixel footprint; widening or
  smoothing center-filled peaks does not create either close waveform.
  3BAND is not a recolored version of that contour: render its native PWV7
  layers directly. PWV7 storage uses the shared 150 Hz clock, but the supplied
  Rekordbox edges expose one display facet per paired row (75 Hz); select the
  first per-band peak mip and linearly connect those paired values. Rendering
  every stored row doubles the visible corner density, while holding a row as
  a rectangle creates a different rejected staircase.
  `scripts/render_waveform_reference.py --corpus` is the required
  real-track visual proof; a byte correlation is not acceptance.
- **Audio-detail peak levels must match the physical-pixel footprint, not just
  the next power of two.** A 4.2-cell pixel rendered from an 8-cell max mip
  throws away nearly half the contour and rounds Rekordbox's narrow broken
  attacks into slabs. Build the native in-memory ladder at the measured
  integer divisors (`1,2,3,4,5,6,8,9,13,...`). Below the close family, select
  the first level within the tightly bounded 6% footprint tolerance. At the
  five close UI stops (384/576/864/1152/1536), BLUE/RGB instead select from a
  four-physical-pixel output footprint (`audio_detail_lod(footprint * 4)`).
  On a 6300 Hz record at Retina scale two, the fixed stops resolve to divisors
  `35/25/18/13/8`, or 0.20–0.26 extrema cells per physical pixel. The final
  divisor is eight because the 6% footprint tolerance accepts it; do not copy
  the stale pre-measurement value of nine. Live acceptance showed that both
  the original
  0.91–1.03 density and the first 0.51–0.63 reduction still left a static
  pulse. This is still one stable song-anchored peak level—never blend two
  lattices, never blur final pixels, and never persist or recook these runtime
  mips. 3BAND has its independent paired-PWV7 cadence and must bypass this
  rule.
- **Peak mips change typical height even when their extrema are correct.**
  Coarser max-pooled rows make the same song window taller, so the dedicated
  close source caused a visible 256→384 height jump while finer rows made
  64/92/128 and the closest stops progressively thinner. From 64 px/s upward,
  normalize signed min/max by the selected integer divisor against the
  50-cell reference with the shallow `pow(50 / divisor, 0.22)` curve, plus
  narrow headroom compensation at 64/92/128. Never apply this as a straight
  multiply: loud 256 px/s peaks clip to the lane edge and then collapse when
  the four-pixel family begins at 384. Apply gain only through remaining
  headroom, `m + (gain - 1) * m * (1 - m)`. That fixes digital zero and full
  scale while equalizing the middle of the trace without changing horizontal
  knot cadence, colors, cache data, or 3BAND.
- **Waveform primaries are authored for the built-in Display-P3 surface.**
  Keep `CAMetalLayer.colorspace` explicitly set to Display P3 while retaining
  the sRGB-transfer Metal attachment for linear-light coverage blending.
  Leaving the layer color space implicit converts Rekordbox red `255/0/0` into
  `234/51/35` and high blue `127/162/255` into `134/162/248` on capture. In
  3BAND, preserve the measured flat membership palette rather than
  remixing band energy into red/pink/blue hues.
- **The scrolling surface must be fixed in song space, not reconstructed at a
  new playback phase each frame.** Print height and amplitude-weighted RGB onto
  a private song-coordinate attribute lattice with four attributes per
  physical pixel at every zoom. Whole-pixel close translation was exact after
  integer alignment but made dense one-to-two-pixel spikes visibly strobe.
  Interpolate attributes for continuous subpixel motion. The buffer needs a
  generous guarded margin scaled by the attribute density and absolute
  integer-song-coordinate recentering so regeneration cannot create a viewport
  seam.
- **A signed extrema cell needs about four physical pixels at the close
  stops.** “At most one cell per pixel” was sufficient to stop gross aliasing,
  and about two pixels per cell improved it, but neither removed the final
  static pulse: adjacent min/max cells can alternate from one extreme to the
  other, placing their highest spatial frequency at a visibly phase-sensitive
  screen cadence. Keep `kLodBias = 0`; select the dedicated close peak level
  from a four-pixel output footprint without a final-image filter.
  Midpoint/profile taps integrate final coverage; they do not buy sharper
  source detail.
- **Do not recover close detail by flooring the mip.** The fixed lattice stops
  phase-dependent redraw, but it cannot make above-Nyquist values displayable.
  A floor-mip prototype looked richly detailed in a still and immediately
  produced a nasty hairy/wiggly edge in motion on the real stress track. Keep
  the conservative ceiling mip. The targeted zoom recording later justified a
  deliberate kernel split: positive B-spline reconstruction through 384 px/s
  for stable mid/wide contours, then direct linear reconstruction at 576 px/s
  and above for BLUE/RGB signed intervals. PWV7 retains its
  native cell timing. The
  supplied `1536`
  references proved that monotone Hermite rounded the native straight-sided
  carrier peaks. Do not extend the averaging B-spline into the close range.
- **Antialias coverage must remain continuous through final compositing.** A
  hard minimum vividness for every nonzero fragment turns a contour crossing a
  pixel into an on/off color flash, even when the song coordinate itself is
  perfectly smooth. Use the cubic ease-out only for the small overview, where
  true subpixel peaks need visibility. The scrolling lane must use exact
  geometric coverage at every zoom; at 24 px/s the ease-out still exaggerated
  20% physical coverage to nearly 49%, making saturated red/orange edges pulse.
  Coverage policy may change compositing only—never height, hue, LOD, detail
  density, or timing. This rule applies to both sides of a signed interval:
  averaging five independent inside/outside tests still quantizes BLUE/RGB
  coverage into fifths. Integrate the upper boundary with `profile_coverage`,
  integrate the inverted lower boundary the same way, and take their overlap.
  The interval's enforced minimum width keeps the two one-pixel AA bands
  disjoint. The exact High & Scared close-RGB phase sweep cut the aligned
  greater-than-64-code change tail by almost 90% without changing its accepted
  silhouette.
- **BLUE/RGB color must be resolved conditionally on signed coverage.** The
  continuous upper/lower boundary resolve can be perfectly stable while an
  independently averaged color still ticks inside it. RGB may inherit hue from
  a horizontal tap that does not cover the queried destination pixel; BLUE
  exposes the same defect more strongly because its white/cyan/blue radial
  color depends on the moving signed center and width. Integrate clipped-linear
  coverage moments per quarter-pixel segment, accumulate only visible color in
  premultiplied linear light, and evaluate BLUE's radial field at the local
  coverage centroid. Preserve the accepted `interval_profile_coverage` result
  exactly so color work cannot move the silhouette. A temporary flat BLUE fill
  can diagnose this class of defect but is not a fix because it discards PWV3
  depth information.
- **Timing-reference color must not inherit waveform color.** Grid lines are
  drawn after the waveform. Downbeats use opaque red and ordinary beats use
  opaque gray; translucent timing ink lets saturated waveform content alter
  both color and contrast. Keep hierarchy in the red/gray colors themselves,
  not by exposing the waveform through the marker.
- **A one-point Metal grid rectangle is not subpixel-stable on Retina by
  itself.** Non-MSAA triangles rasterize it as a hard two-physical-pixel box,
  so its center advances on integer pixel boundaries while the waveform below
  moves continuously. Carry signed local line distance and half-width through
  the grid vertex, expand the fringe geometry by half a point, and evaluate
  analytic box coverage in physical pixels in the fragment. This applies to
  both downbeats and ordinary scrolling beats; color/opacity policy is
  separate from edge coverage.
- **Final coverage must be composited in linear light, not patched per
  primitive in BGRA8Unorm.** A two-pixel line alternates between coverage
  distributions `[0.5, 1.0, 0.5]` and `[1.0, 1.0]`; waveform contours have the
  same problem. Their areas match, but blending display-code RGB directly
  gives unequal SDR energy. A line-only `coverage^(1/2.2)` compensation proved
  the diagnosis but left waveform edges in the wrong color space. Keep the
  layer/pipeline BGRA8Unorm_sRGB, decode display-authored RGB in both final
  fragments, blend linear coverage, and let Metal encode once on store.
  Clear-color components are also linear inputs for an sRGB attachment: use
  the decoded values for `#0d0e12`, not `13/255, 14/255, 18/255`.
- **Do not insert a translated RGBA waveform texture between song data and the
  drawable.** It filters already-antialiased coverage and hue a second time,
  making contours and adjacent colors breathe. A `float4` attribute buffer is
  different: it stores premultiplied RGB plus height, never coverage.
  Premultiplication is required: interpolating straight RGB and height
  separately before multiplying them creates a phase-dependent quadratic
  signal at the moving edge. Translate the premultiplied attributes and
  compute the one-pixel vertical coverage exactly once in the final fragment.
  Never cache or interpolate final alpha.
- **A fixed attribute lattice still needs one-physical-pixel horizontal
  integration.** One center height sample leaves subpixel needles that crawl
  through dense passages. Below 1152 px/s, retain the accepted five
  quarter-pixel boundaries over `[-0.5, +0.5]`. At the two closest stops, a
  fractional song phase can place one integer attribute-lattice knot between
  those probes; integrate the two pixel endpoints plus every interior knot
  with their actual spans. Feed the same positioned profile to silhouette,
  signed-interval, color, and 3BAND coverage. Do not widen the footprint: one
  pixel removes the alias while retaining the close detail that B-spline
  reconstruction erased.
- **Do not cure source aliasing by widening the final image filter.** Narrow
  fixed-stop `[1, 2, 1]` reconstruction remains song-space and same-LOD.
  The separate 384–1536 BLUE/RGB fix is a discrete peak-source decision:
  select one peak row from the four-physical-pixel output footprint, then
  retain the same one-pixel final integration. A triangle/RMS mip and fixed
  150–600 Hz low-pass probes reduced motion numerically but rounded the
  reference facets and were rejected. Do not turn the selected-row rule into
  a broader screen kernel, an adjacent-LOD blend, or a 3BAND change.
- **Inactive preview is an idempotent native transition.** PlayerBar publishes
  coarse transport text positions during ordinary playback. Those
  `preview_active=false` calls must do nothing once native preview is already
  off; waking `CAMetalDisplayLink` or posting a run-loop block for each poll
  introduces full-app pacing noise that an isolated deck harness will not
  reproduce. Active drag preview still republishes every position and wakes
  one frame.
- **Do not pin `CAMetalDisplayLink` to 120 Hz.** Let Core Animation negotiate
  the active screen cadence so fixed-60 Hz, ProMotion, power throttling, and
  moving the window between displays remain evenly paced. Snapshot render
  buffers under the renderer mutex, then release it before command encoding;
  the display thread also reuses its overlay vertex storage. The final
  2026-07-28 phone/native comparison proved that the remaining rare doubled
  interval begins at `CAMetalDisplayLink` itself: its next
  `targetPresentationTimestamp` occasionally advances by 16.667 ms, the
  callback arrives on that same interval, and Metal still has about 8.2 ms
  before `targetTimestamp`. Geometry, GPU load, the CoreAudio PLL, and drawable
  presentation are therefore downstream of the omission. Hard 120 Hz and a
  90–120 Hz floor both increased gaps and were reverted. A registered 60 fps
  phone capture cannot resolve every 120 Hz panel refresh by itself, but paired
  with native target/presentation counters it can expose the conserved
  half-step/one-and-a-half-step sampling pairs without screen-recorder
  compositor interference.
  A 2026-07-29 retry against the new 120 Hz screen recording also rejected
  three apparent fixes: the system-default frame-rate range tied the existing
  60–120 preference, fixed 60 Hz introduced 25 ms gaps, and a continuously
  running display-thread run loop tied the existing bounded run-mode pump in
  paired trials. Keep the explicit 60–120 preferred range, latency one, common
  run-loop modes, and timestamp-driven prediction.
  A subsequent grid-on/grid-off isolation covered 5,400 exact drawables per
  state at 1536 px/s. Display-link gaps were 23 with the ordinary static
  2,688-vertex beat buffer and 24 without it; both had zero GPU deadline
  misses and zero CoreAudio underruns. Mean GPU time changed by only 0.021 ms.
  Do not rebuild, hide, decimate, or otherwise blame the beat grid for this
  cadence omission.
- **Exact-drawable diagnostics must remain bounded and off the display path.**
  The temporal trace preallocates its one shared Metal buffer before playback
  (maximum 600 frames / 512 MiB), then adds only a final-texture blit and
  timestamp bookkeeping per captured drawable. Never add file I/O, PNG/video
  encoding, Python callbacks, allocation, or blocking waits to `draw`, its
  command completion, `addPresentedHandler`, or the CoreAudio callback.
  Finalization is allowed only after playback stops and only beneath the
  caller's explicit output directory. Preserve dropped presentations as
  `presentedTime == 0` ledger rows; omitting them would falsify the cadence
  experiment. The dual-edge Gray/parity marker is diagnostic-only and must be
  gated by the exact reserved trace sequence. Never expose it during ordinary
  playback. Keep the fixed-waveform calibration: without it, a 60 fps camera
  cannot distinguish panel phase/exposure blending from waveform motion.
- **A display-link submission is not an onscreen frame.** Target timestamps
  can run ahead of the drawable the hand actually sees. Count cadence from
  `MTLDrawable.presentedTime`, ignore its documented zero value for a dropped
  drawable, and keep presentation latency at one. On mouse-down, retain the
  newest fresh presented or irreversibly scheduled source frame. Freezing an
  older submitted frame produces a visible rewind. A measured latency-two
  retry on 2026-07-27 reduced five-second close-zoom cadence and did not remove
  the occasional doubled presentation interval; do not revive it as a hitch
  fix. The 2026-07-28 retry was worse again and reached a 25 ms gap at
  1152 px/s.
- **NEVER motion-blur the scrolling waveform.** This was built twice — a
  180-degree box, then a full-interval tent — and rejected on sight both
  times. The premise is wrong: rendered motion blur is for content the eye
  does *not* track. A DJ tracks the waveform, so under smooth pursuit it is
  stationary on the retina and the blur is painted onto a stationary image
  and never averages out. It rounds every edge, destroys the detail it was
  meant to protect, visibly "expands" when transport starts, and — because a
  grab freezes the last rendered frame — hands back a blurred still. The
  display's own sample-and-hold already supplies ~25 Retina pixels of
  persistence blur at 1536 px/s; a shutter only doubles it. Keep the
  integration window equal to the physical pixel and keep tap weights flat.
  `test_no_temporal_shutter_blurs_the_moving_waveform` fails the build if any
  shutter symbol returns.
- **Diagnose close-zoom fizz in song space before changing the clock.** Check
  the selected LOD, attribute-lattice anchoring/recentering, sample density,
  and final coverage; a stable presentation clock cannot hide content above
  the pixel Nyquist or a contour that is being reprinted at changing phases.
- **A signed audio contour can be spatially correct and still strobe.** The
  paired 2026-07-29 physical/GPU run isolated a 41.209 Hz bass fundamental in
  the source, the same 41.33 Hz line in exact BLUE/RGB drawables, and its
  approximately 18.9 Hz fold in the 60 fps phone capture. 3BAND's unsigned
  Rekordbox band envelopes omit that polarity carrier and stayed at the fixed
  calibration floor. Five-refresh motion registration held BLUE/RGB to p99
  0–1 display code, so do not misdiagnose this as random redraw, chroma
  breathing, position jitter, or GPU lateness. Remedy experiments must isolate
  signed interval center/polarity motion from accepted outer extrema and
  attacks; never reach first for final-pixel blur or a playing-only shutter.
- **Removing the signed carrier cannot preserve its paused pixels.** The
  supplied close Rekordbox screenshots visibly contain the same oscillating
  polarity trace that strobes when translated. The carrier-neutral compositor
  experiment proves the tradeoff: using the existing unsigned peak
  magnitude cuts the real 41 Hz drawable band from 19–37% to 1–3%, but turns
  the signed zigzag into a symmetric filled envelope and materially changes
  the paused reference silhouette. The matched physical A/B proves the remedy:
  BLUE/RGB's phone-folded carrier falls below calibration and 3BAND control.
  The first physical review accepted its motion, but live use immediately
  rejected the lost detail. The signed contour therefore remains the close
  BLUE/RGB product default for stopped and playing. The neutral-envelope ABI
  and shader branch were removed on 2026-07-30 after they served their causal
  experiment. Keep 3BAND outside any replacement and never reintroduce a
  playing-only filter.
- **Do not fill a soft black gap to make it look sharp.** A low-opacity neutral
  bed behind the signed contour reduced binary negative-space carrier power,
  but live comparison preferred the untouched far-left BLUE control because
  every bed made the result look filled. Later 0.75 signed-coverage and 1.0 RGB
  fractional-coordinate sharpening added contrast after the exact resolve and
  made the remaining physical carrier easier to read as pulsing/static. The
  2026-07-30 simplification removed the bed and both post-resolve transfers
  from the ABI and shader. Keep the exact continuous interval/color integral,
  keep 3BAND exact, and regression-check all thirteen real UI zooms.
- **Never blend two mip levels' coverage. Pick one.** Levels are song-anchored
  maxima on power-of-two lattices, so level N quantizes every 2^N bins and
  N+1 every 2^(N+1). Blending scrolls both at the same speed with different
  quantization and they beat against each other. The artifact tracks the
  fractional LOD exactly: edges were bad at 32/64/128/256/576 px/s (fraction
  ~0.62) and clean at 24/48/92/184/384 px/s (fraction ~0.05). Continuous
  blending only ever protected zoom *transitions*, and zoom is thirteen fixed
  steps, so the level is constant for the whole of playback. `biased_lod`
  ceils the footprint and `kIntegrationTaps` integrates coverage within that
  one selected level.
- **Averaging per-tap coverages quantizes the edge.** One tap's analytic band
  is about `antialias / heightScale` -- roughly 1% of full height -- so each
  tap is effectively binary and the mean of N of them takes only N+1 values.
  Sliding taps then cross one at a time and the contour visibly wiggles at
  every zoom. `profile_coverage` integrates the exact area under the
  piecewise-linear tap profile instead.
- **Native fps counters do not measure what reaches the screen.** A 120 fps
  screen recording of the real app showed only **52-54 unique frames per
  second**, with irregular hold lengths of 0, 1, 2 and 3 captured frames,
  while `sb_deck_display_stats` simultaneously reported ~117 presented fps.
  The deck renders roughly twice as fast as the window is composited and the
  surviving frames are unevenly spaced. Every native counter is blind to
  this; only a frame-accurate capture of the screen shows it. Note macOS may
  drop ProMotion to 60 Hz *while recording*, so confirm the symptom without
  a recorder before treating the rate itself as the bug.
- **A Python polling loop cannot measure display freezes.** Sampling
  `sb_deck_presented_frame` from Python and differencing shows "freezes" that
  are the *observer* stalling, not the deck: a 490 ms Python hiccup is
  indistinguishable from a 490 ms display stall. Trust `max_gap_ms`, which the
  presented handler computes natively from real `presentedTime`.
- **Main-thread widget work steals displayed frames, and an isolated deck
  harness will never show it.** The 20 Hz transport position path called
  `_update_transport()`, which called `cue_panel.set_loaded()`, which
  rebuilt all sixteen cue buttons — allocating a `QPixmap` and `QIcon` per
  filled slot — even though `loaded` had not changed. That cost **eight
  displayed frames per second at 1536 px/s** in the real app: 106.9 -> 118.0
  fps once guarded. Every dropped frame at maximum zoom is a doubled 51-pixel
  scroll step, which reads as shaking, and no shader change can fix it.
  Anything on the position path must early-out when its state is unchanged.
  `WaveformBar._repaint()` exists for the same reason: `paintEvent`
  early-returns under Metal, so a Qt repaint composites for nothing.
- **Measure renderer changes in the real app, not only in a deck harness.**
  A bare `QWidget` host with hand-uploaded analysis ran 118-120 fps while the
  real app ran 107-113 on the same machine and build, and the harness could
  not see the cue-panel cost at all because nothing was connected to
  `positionChanged`. Run-to-run fps also varies by 5+, so attribute costs
  with **paired alternating A/B rounds inside one process**, never by
  comparing two separate runs.
- **Verify shader edits by compiling the Metal source, not by reading tests.**
  The source is a runtime string, so a syntax error is silent: pipelines fail,
  nothing presents, and the failure surfaces as unrelated cadence tests
  reporting zero presentations. `half` is a reserved MSL type name and is an
  easy accidental variable. Extract the `R"METAL(...)METAL"` body and hand it
  to `newLibraryWithSource:` to get the real compiler diagnostic.
- **The visual clock is a continuous PLL, not a corrected counter.** Never
  `fetch_add` a phase correction onto the visual frame at an Audio Unit callback
  boundary: the former 0.15 ms cap became almost half a Retina pixel at 1536
  px/s and looked like close-zoom shaking. CoreAudio timestamp observations may
  adjust `visualEffectiveRate_`, while rebasing must preserve the already
  predicted fractional position. Keep the sequence lock around the
  frame/host/rate tuple. Likewise, scrolling grid vertices stay as integer
  milliseconds + local phase; subtracting absolute float seconds loses visible
  precision late in a track.
- **Do not spread an omitted ProMotion callback across later frames.**
  An exact synthetic A/B made four-refresh repayment look best and reduced the
  largest source-frame advance from 735 to 460, but the installed app felt
  worse and the direct-prediction control was clearly better. Repayment adds
  temporary visual lag, then exposes that lag as several frames of altered
  velocity—a lingering rubber-band effect instead of one rare correct-time
  double advance. Keep CoreAudio authoritative and predict its position
  directly for each display target. The absent callback cannot be rendered.
  Do not revive the recovery clock or its diagnostic injection probe.
- **Player cache schemas are data contracts, not renderer version numbers.**
  A shader/filter/clock change does not require re-preparing the library.
  Bump/rebuild only when the persisted payload itself changes. The hot path is
  canonical V5 sections. Cache hits start one short-lived staged reader: never
  read/hash the whole record or build Metal buffers on the UI thread.
  `WaveformFillWorker` remains incremental, prioritizes the
  selected track, and cooperatively yields inside long tracks during playback
  and foreground interaction. Startup metadata discovery reads only V5
  manifests. Compare the exact cached beat rows with the current read-only
  Rekordbox PQTZ fingerprint so later manual grid corrections refresh metadata
  without an audio recook; do not treat `grid=rekordbox` as permanently fresh.
- **`djmdCue.Kind` has a reserved hot-slot gap.** A/B/C are 1/2/3, value 4 is
  reserved, and D–P are 5–17. `slot = Kind - 1` silently shifts every cue from
  D onward and makes D impossible. Keep the explicit mapping regression test,
  reject reserved/unknown values, and preserve I–P even though the current
  panel shows A–H.
- Player interaction and transport semantics are specified in `PLAYER.md`.
  Keep double-click = the only browser load gesture (paused), Space =
  play/pause for the already-loaded deck only, C = momentary CUE, one decoder
  seek on drag release, and first-Rekordbox-beat loading when a valid imported
  grid exists. A missing/invalid imported grid uses the SoundBox fallback with
  beat 1 at `0:00`; neither path writes to Rekordbox.
- The scrolling detail waveform claims transport on mouse-down by design:
  use the native atomic hold to freeze the newest drawable already presented
  or committed at latency one,
  re-anchor Qt drag math to its fractional-millisecond timestamp, cancel any
  pending gain ramp, hard-mute, and pause synchronously. The ordinary pause
  envelope advances to its fade endpoint and must not own a waveform grab.
  Predicting a fresh frame or rounding to whole milliseconds visibly jerks at
  high zoom. Relative movement follows the pointer continuously; do not add a
  dead zone that releases its accumulated delta as one jump. Release sends one
  exact authoritative seek—even without movement—and resumes only when the
  deck was playing before the grab. Never seek the decoder on move events. The
  whole-track overview deliberately retains absolute click/drag navigation.
- Ordinary click/Space pause must capture that same exact newest Metal frame
  at fade completion before stopping and seeking the decoder. A coarse Python
  millisecond estimate trails the drawable by about one refresh: recordings
  showed forward settling followed by a 7–13 physical-pixel snap backward.
  Resume must start from the held fractional-millisecond frame. This does not
  replace the gain envelope; it makes its endpoint authoritative.
- Overview and ultra waveform buffers are one visible surface. Prepared loads
  must group both native uploads while presentation is suspended and commit
  them together. Painting the overview while a long ultra section is still
  decompressing/building makes the waveform visibly change size on promotion.
- No manual SoundBox grid editor is shipped. V5 has a storage-only ownership
  boundary for later work, but current precedence is simple: a valid read-only
  Rekordbox grid wins exactly; otherwise use the zero-anchored fallback. Never
  infer that boundary from AAC/ALAC/WAV or a filename—those only happened to
  identify Sonny's current sets. Player load must apply the same zero-anchor
  contract immediately to a legacy fallback while bulk metadata migration is
  still in progress.
- Custom QWidget with layout `stretch=1` may not expand — also set
  `setSizePolicy(Expanding, …)`.
- QSS: `QLabel { background: transparent }` or you get dark boxes behind text.
- **NEVER do synchronous removable-USB I/O on the main thread** (`statvfs`,
  reading the manifest, `walk`/`rglob`, `rmtree`, drift diff). macOS fskit
  BLOCKS these for SECONDS when the stick is busy → whole-app beachball + frozen
  animations. Thread it (`DeviceScanWorker`/`DeleteWorker`), show the lower-left
  progress bar, cache drift in `_drift_cache`. Any new device op must be threaded.
- **Confirmation belongs before exact iPod planning.** Sync, composite export,
  and composite playlist deletion must read as one action: immediate Yes/No,
  visible background planning, guarded commit, one completion. Never compute a
  full plan (or retained-playlist metadata signatures) merely to populate the
  approval dialog. Planning and commit use different work units, so map them to
  monotonic segments of one bar rather than resetting the range at the phase
  boundary.
- **Do not sustain full-file hash reads across a classic iPod during a UI plan.**
  The real MA446 dropped off USB twice while the composite planner streamed its
  managed audio baseline, even after device polling was serialized. Schema-3
  planning uses manifest ownership/path/size plus exact Mac-side desired
  derivatives. Commit replaces one managed audio file at a time using a
  same-volume rollback rename, verifies the newly landed bytes, and briefly
  yields between tracks. Keep polling paused through plan, confirmation, and
  commit; never run a second CLI/device reader beside the app.
- **Post-commit iPod refreshes are one serialized device operation.** Refresh
  the already-open Device/Songs/Playlists snapshot first, then start the sidebar
  free-space/drift scan from that worker's `finished` signal. Starting both
  readers together adds avoidable USB contention and leaves the visible
  snapshot stale until the user navigates away and back.
- **Connection discovery, dashboard preload, and drift are ordered UX phases.**
  A debounced `/Volumes` watcher begins detection without waiting for the
  three-second recovery poll. No new generic or iPod row publishes before the
  discovery QThread has cleared ownership. Never publish a stock/dual iPod's sidebar
  row as a loading affordance. Keep it absent and put `Loading iPod...` in the
  lower-left status while the genuinely new mount skips first-pass drift,
  preloads the complete immutable Device/Songs/Playlists snapshot exactly once,
  and then runs the normal drift scan. The completed follow-up scan is the sole
  visibility gate. Thus every clickable iPod already has a cached snapshot and
  clicking it is presentation-only; it must never parse/hash iTunesDB again. A
  failed initial read remains hidden and retries through the bounded poll rather
  than entering an immediate worker loop. Sidebar and stock-dashboard readers
  remain strictly single-flight. Bind path-keyed caches to the mount root's
  `st_dev`, and clear/reprobe every disk/profile/identity cache when the same
  `/Volumes` name returns with a different token.
- **A stock shadow source row describes two files.** `size`/`sha256` belong to
  the verified device payload; `source_size`/`mtime_ns` describe the canonical
  Mac source. Picture removal changes payload size without changing the source.
  Comparing source stat against payload size creates a permanent false
  Sync-needed state immediately after a good commit.
- **The offscreen UI harness must suppress MainWindow's immediate device scan.**
  Stopping `_dev_timer` after construction is too late: `__init__` already calls
  `_refresh_devices()`. Patch that method during construction so running UI
  tests beside the real app cannot create a second removable-device reader.

## API traps
- **Qt slots SWALLOW exceptions** — a crash inside a signal handler prints to
  stderr and keeps going, so a UI test toggling a widget can PASS while the slot
  is broken. Test slot logic by DIRECT call too. (This hid a real `is_untracked`
  crash that only surfaced in the packaged app.)
- **Rapid track loads pile up waveform/analysis workers** → hang + latent GC-mid-run
  crash. Any "load X, user can rapid-switch" worker needs interrupt-previous +
  a strong-ref set (`_wf_pending`), never a single `self.x` reassignment;
  every result is track-generation guarded; `shutdown_waveforms()`
  interrupts + waits all.
- **A new track can change after its first background cook.** Rekordbox/tag
  tools may finish rewriting a newly landed container minutes later; direct
  SoundBox imports can also be replaced in place. One refresh-time waveform
  pass therefore is not enough. Successful source loads/imports arm the
  single-shot 60-second identity recheck, which rearms only after the previous
  worker finishes. `is_ready` uses size + mtime identity and recooks only stale
  records; the scan must stay manifest-only and must not inflate waveform
  payloads or overlap workers.
- **A ready cache can still preserve an early Rekordbox fallback.** Rekordbox
  may finish PWV3/PWV5/PWV7 after SoundBox has already stored a structurally
  complete V5 record. The recurring scan must use stream provenance to keep
  complete Rekordbox-backed modes on the manifest-only path; only modes still
  marked as SoundBox fallbacks may probe the current read-only ANLZ payload.
  A newly valid exact stream queues that one atomic V5 recook. The explicit
  track-row Reanalyze action remains the escape hatch and bypasses `is_ready`
  for only the requested track. Both routes retain the old playable cache until
  a complete replacement is atomically stored and hot-refresh the deck after
  success. Never delete the record first, and never write source audio or
  Rekordbox analysis.
- **Track replacement is an atomic renderer boundary.** Call native
  `begin_track(generation)` before setting the source. It clears overview,
  detail/mips, grid, imported cues, and session CUE together. Packed overview
  and detail uploads happen on the staged worker and native code rechecks the
  generation after allocation, before buffer promotion.
- **Do not reintroduce black RGB delay padding at 0:00.** New RGB resampling
  clamps to the first valid hue; selective overview load repairs audible
  zero-RGB legacy rows. Negative-time fragments and true silence stay pure deck
  background.
- **Computed-row cache:** effective title/artist is cached per track_id, warmed
  in background (debounced, generation-guarded), invalidated on
  override/alias/registry/title-junk change. `_row_for` returns a COPY. If you
  add anything that changes effective title/artist, call `_invalidate_rows()`.
- **A new per-track `Track` field that affects a view MUST survive two caches or
  it silently reverts on the instant paint.** (1) Add it to `libcache._TRACK_FIELDS`
  and bump `SCHEMA` — else the on-disk snapshot drops it and every track loads with
  the default. (2) `_landed_cache` (path → landed time) is cleared in
  `_apply_loaded_library` — else a value computed off the instant-paint snapshot
  sticks after the live rekordbox read replaces it. This is exactly how `added_ts`
  (Collection newest-added sort) got defeated twice before it worked.
- **Restoring a tree selection with signals BLOCKED re-renders NOTHING.** The
  two sidebars are asymmetric: `_populate_tree` re-selects an rb playlist with
  signals LIVE (fires `_on_rb_select` → fresh rows), but `_refresh_custom`
  re-seats Collection/Artists/custom inside `blockSignals(True)` — the table
  keeps its old rows. So any path that swaps `self.lib` under a live SoundBox
  view (refresh-done, instant paint) must re-render by hand;
  `_apply_loaded_library` does. (Real bug: new rekordbox adds stayed invisible
  in Collection until you clicked away and back.)
- **`_restore_view` re-seats ONLY on a view SWITCH** (same `_view_key` → early
  return), and `model.set_rows` is a full model reset (selection + scroll
  gone). In-place reload with fresh rows = `_stash_view()` → reload → null
  `_view_key` → `_restore_view(key)`, or the user gets dumped at the top.
- **Worker convention:** `OneShotTask` and `_OneShotArt` emit result-OR-Exception
  — done-handlers MUST isinstance-check. Never map an exception to a "nothing
  there" answer (a working art URL once flashed "No art found" with no log trace).
- `Track.is_untracked(*, nokey, short, short_secs)` is **KEYWORD-ONLY**.
- `engine.export_playlists(lib, [Playlist_objects], target)` takes Playlist
  **OBJECTS**, not display-path strings. `SyncReport` has no `files_copied`
  (use `.playlists` / `.bytes_to_copy`).
- `Playlist.display_path` joins with `" / "` (SPACES around slash), not `"/"`.
- Settings page lives on `body_stack` (index 1); track table / device browser on
  `stack` (0/1).
- Splitting files with a script bypasses the Edit tool's read-tracking — Read
  again before Edit after any scripted rewrite.

## Cancel semantics (sync.py — don't regress)
- `sync.run` on cancel: writes HONEST partial m3u8s (only on-device entries +
  "# CANCELLED: N …" footer) and records incomplete playlists with **sig="partial"**
  → drift flags them → next Sync completes. A cancelled export must NEVER look
  fully synced.
- `sync.resync` on cancel: ownership prune + forget are SKIPPED (re-polls
  `should_cancel` directly); next Sync finishes.
  Tests: `tests/test_cancel.py`.
- Manifest v2 owns individual normalized paths. Only `owned: true` rows may be
  repaired or pruned; matching pre-existing media can be referenced with
  `owned: false` but is never claimed or deleted. Every target write goes
  through a pinned `DeviceSession`, same-filesystem temp, and atomic replace.
  FAT identity is Unicode-NFD + casefold, not Python path equality.

## Type-checking + test-honesty
- `./check.sh` gates: pyflakes + **mypy on `soundbox/` (must stay 0 errors)** +
  the full suite. `desktop/` is NOT mypy-gated yet (PySide6 stub gaps).
- Never `assertX(...) and assertY(...)` (short-circuits the second). Never test
  dict/py semantics against themselves — drive the real app helper. A
  failure-tolerance test must create a REAL failure (e.g. squat a file where a
  dir must go), not assert its own monkeypatch.

## Property-test findings (keep hypothesis in the loop)
- `str.split()` does NOT cover everything `str.splitlines()` splits on
  (`\x1c-\x1e`, `\x85`, ` / `) — text in line-oriented formats (m3u8!)
  must be flattened via `ch.isprintable()`.
- `sanitize_component` turns ALL non-printables into `-` (they once split m3u8
  path lines on-device). Any new filename-ish surface must use it.
- Device path sanitization is not metadata sanitization. Stock iTunesDB text and
  the SoundBox UI preserve printable Unicode. FAT/Rockbox path components use
  readable Latin transliteration (`CØSTA` → `COSTA`, not `C_STA`), retain
  non-Latin scripts, remove decorative `*`/`?`, normalize compatibility
  punctuation, and protect DOS names such as `CON`/`LPT1`.
- Hypothesis tests are `derandomize=True` — deterministic; a failure is REAL.

## Offscreen UI tests can dirty the REAL config
- Constructing `MainWindow()` offscreen and firing a widget handler runs the real
  slot → `_save_state()` → WRITES the real `library.json`. For render/smoke
  tests, set state via `win.settings[...] = …` + `win._sync_settings_controls()`
  (blockSignals, NO save), or point AppDataLocation at a temp dir.
- `setup_logging()` must honor `QStandardPaths` test mode too. Otherwise fixture
  failures such as the intentional `boom` cases are appended to the production
  `~/Library/Logs/Soundbox/soundbox.log` and corrupt later hardware log audits.
- `desktop/_shot.py` is the SELF-CONTAINED reference render harness (synthetic
  lib + qttest sandbox): `PYTHONPATH=. .venv/bin/python -m desktop._shot`.
- **A failed `open_library` pops a MODAL QMessageBox** → headless/offscreen
  scripts hang FOREVER. (The old Desktop test XML is deleted — smokes must use a
  synthetic Library or the live DB.)
- `tests/test_resync.py` (and others) end with a `__main__` guard — appending
  test methods via heredoc lands them AFTER it = silently uncollected. Verify the
  test COUNT went up.
- unittest runs classes alphabetically; UI tests that mutate lib/view state must
  fully restore (incl. `_reload_current_view()`) or later classes see stale rows.
- **`setCurrentItem` on the ALREADY-current item fires NO signal** → the slot
  never runs and the test silently operates on whatever rows an earlier class
  left in the table. This masked RowCache's fake warm rows (`{"id","title":"x"}`
  left in `_row_cache`) for the WHOLE suite — SearchSelection "selected" SET ONE
  as a no-op and filtered old real rows — until a new class cleared the tree
  selection, the re-select genuinely fired, and 'brav' matched nothing. Rules:
  (1) fake `_row_cache` rows are view state — clear them in tearDown; (2) adding
  a class that moves selection can change which later `setCurrentItem`s actually
  fire, surfacing latent poison in classes you didn't touch — bisect predecessor
  classes, not the failing test.

## Metadata / veto plumbing
- Direct-import duration probing must keep both paths: libsndfile handles the
  ordinary WAV/AIFF/FLAC headers cheaply, while Mutagen supplies ALAC/AAC MP4
  container duration. `imports.inject` backfills legacy zero-time records and
  the app persists a repair once. `Track.imported` is the non-negotiable
  exemption from rekordbox cue/placeholder hiding; unknown duration must never
  make a real direct import disappear.
- Title pipeline order: effective ARTIST is computed from the RAW/overridden name
  FIRST, then `effective_title = override | tidy(neat(name, artist), junk)`. Junk
  rules: `metadata.BUILTIN_TITLE_RULES` + user words; `Library.title_junk=None`
  means defaults. The defaults are four intent groups (mixdown, copy/version,
  BPM/key, render/file), not separate rows for regex position or spelling.
  `normalize_title_rules_off` conservatively migrates the former eight IDs. A
  custom plain bracket tag such as `(Original Mix)` is one exact literal tag,
  including its bracket type; it does not match bare text, longer content, or
  unrelated empty brackets. Other custom plain words/phrases likewise match
  only as a complete trailing unit or complete paired-bracket contents. Raw
  regex remains the explicitly advanced, run-as-written escape hatch.
- `manifest._excluded(pl, hidden, removals)` is the single source of veto truth
  for sig/entry/diff/prune — any new "don't export this track" feature must flow
  through it or devices won't drift.
- Artist aliases: `build_registry(..., aliases)` applies them TWICE so chains
  resolve (yeschef→YESCHEF!→YES CHEF). `suggest_merges` is O(n²) with quick-ratio
  prefilters — keep it off the main thread.
- Row alignment: QTreeView items QSS-locked to `height:24px`; table + rb tree use
  `ScrollPerItem` — don't reintroduce pixel scrolling or padding-based heights.

## Python 3.14 / build
- **`aifc`, `audioop`, `sunau` REMOVED in Python 3.13+** → can't decode AIFF with
  stdlib. Use `soundfile` (libsndfile) — reads WAV/AIFF/FLAC/MP3 (NOT ALAC/.m4a).
- **ALAC/AAC V5 analysis uses `qt_detail_peaks()` (QAudioDecoder =
  same ffmpeg backend that plays the file).** THREE traps, don't regress:
  (1) an unreadable file reports
  its error SYNCHRONOUSLY inside `dec.start()` — check `done`/`dec.error()` BEFORE
  `loop.exec()` or the worker strands until process exit → SIGABRT; (2)
  `dec.stop()` is async — bounded `isDecoding()` wait before the object dies; (3)
  `drain()` checks interruption/cooperative policy at each bounded PCM chunk.
  It must consume at most one analysis chunk per event-loop turn so decoder
  callbacks cannot monopolize the GIL; QAudioDecoder backpressure is the pause
  mechanism. Must run off the UI thread.
- **Battery efficiency is cooperative, never a lower-quality cache or a
  colorless loaded deck.** AC uses uncapped optimized analysis. Battery mode
  budgets only bulk worker CPU; below 50%, a large backlog is slowed further.
  Low Power Mode, playback, scrolling, resizing, and focus transitions suspend
  only bulk work at chunk boundaries. The currently loaded track remains
  foreground-critical and bypasses those deferrals. Do not reduce V5
  resolution, skip spectral/color sections, or substitute a colorless
  placeholder to save power.
- **PQTZ-only means direct DAT rows, not a smaller generic ANLZ parse.** The
  legacy grid migration bounds-checks and decodes only the eight-byte PQTZ rows
  from `ANLZ0000.DAT`. It must never call the generic parser or open EXT/2EX;
  thousands of tiny metadata migrations otherwise become a needless energy
  spike. Every Rekordbox path remains read-only.
- **A V4 cache hit is playable but still V5-upgrade-pending.** Only
  `track_analysis.is_ready()` decides whether background preparation is done;
  it requires the complete V5 mip/color contract. The player may load any valid
  V4 record immediately. Do not collapse those two meanings. The read-only
  audit reports playable V4 and verified V5 coverage separately.
- **Cancellation must not downgrade canonical analysis.** The native analyzer returns no result
  when its cooperative checkpoint is interrupted. In that case preparation
  must retain the existing record; writing only an imported Rekordbox shell
  discards SoundBox detail data and creates a permanent retry loop.
- **Never put librosa/numba/LLVM in background beat analysis.** LLVM's first
  compilation overflowed a PySide QThread's 512 KiB stack during
  `MCJIT::finalizeObject` and SIGBUSed the entire frozen app; disabling JIT by
  environment variable was not honored reliably by the PyInstaller bundle.
  `track_analysis._beat_grid()` is deliberately NumPy-only (FFT
  autocorrelation + onset-phase comb), so the live worker has no LLVM path.
  `WaveformFillWorker` also reserves a 16 MiB stack as defense in depth against
  future scientific-library lazy imports; do not remove it because current
  code happens not to JIT.
- **A nearly full data volume must pause preparation, never trigger cache
  cleanup.** Full V5 audio cooking pauses below 20 GiB and resumes above
  25 GiB. A verified one-record V4→V5 reduction uses a 5/7 GiB guard because
  it retires its larger V4 only after complete V5 verification. `library.json`,
  its small rotating backups, art, and existing analysis are not expendable.
  Installer app rollbacks are temporary and only one may exist during
  verification.
- Build: `./build_app.sh` first builds the arm64 CoreAudio/Metal dylib, then
  PyInstaller collects it alongside pyrekordbox, sqlcipher3,
  sqlalchemy, yt_dlp, soundfile, zstandard, and NumPy's standard runtime hook,
  `--collect-submodules PySide6.QtMultimedia`, `--collect-all PIL`.
- The app is locally self-signed when the `Soundbox Self-Signed` identity is
  available (otherwise ad-hoc), but it is NOT Apple-notarized → Gatekeeper may
  require right-click → Open. Logs at
  `~/Library/Logs/Soundbox/soundbox.log` (Python errors here; C++ Qt fatals also
  leave `crash-stacks.log` / macOS crash reports).

## rekordbox DB (pyrekordbox) — SACRED, read-only temp copy ONLY
- **`db.get_playlist_contents()` returns tracks sorted by ContentID, NOT playlist
  order.** For track order use `DjmdSongPlaylist.TrackNo`. (Sonny's order is
  sacred — this was a real bug.)
- **SMART playlists (Attribute 4) have ZERO `DjmdSongPlaylist` rows** (computed) →
  for them use `get_playlist_contents()`. Attribute: 0=normal, 1=folder, 4=smart,
  -128=cloud(skip).
- **Removed My Tags remain as soft-deleted `djmdSongMyTag` history.**
  `pyrekordbox 0.4.4` does not include `rb_local_deleted` in its My Tag
  smart-list predicate, so calling `get_playlist_contents()` directly
  resurrects old genre memberships and also breaks `not contains`. SoundBox
  rebuilds any smart-list predicate containing `myTag`, preserves
  pyrekordbox's other condition clauses and ALL/ANY operator, and requires the
  tag definition, association, and content to be active. `Jump Bros!` was the
  proving row: Trap remained active while its historical DnB link was deleted.
- **A snapshot is `master.db` plus its matching WAL, never `-shm`.** Copying a
  main file and a WAL across a rekordbox checkpoint can mix two points in time.
  Compare device/inode/size/mtime/ctime for both before and after the copy and
  retry the whole pair when either changes or the WAL disappears. SHM contains
  process-local locks/index state and must be reconstructed for the copy.
- `rb_local_deleted` truthy = soft-deleted. Apply it at every visible endpoint:
  content, playlist nodes, ordinary membership rows, My Tag definitions, and
  My Tag associations. Filtering only the parent row is insufficient.
- The rekordbox Collection is larger than its playlist-referenced set;
  SoundBox intentionally loads only tracks reached from an active normal or
  smart playlist because it is a playlist exporter. A newly tagged/added track
  enters on the same refresh; a track removed from every playlist leaves.
- Validate `Attribute`, parent folders, sibling `Seq`, membership `TrackNo`,
  reachability, stable IDs, and unique display paths before accepting a tree.
  Keep exact `TrackNo` order. An ambiguity or an unverified smart field must
  fail refresh visibly, never degrade to an empty/all-tracks playlist.
- **`DjmdPlaylist.ID` is STABLE across rename + move** → the device-sync identity.
- **"Date added" = `content.created_at` (the DB row's real timestamp, time-of-day
  precision), NOT `DateCreated`.** `DateCreated` is a stale tag/metadata date that
  can predate the actual add by weeks (e.g. a track bounced 6/29, added 7/10 reads
  `DateCreated=6/29`, `created_at=7/10 16:43`). Collection's newest-added sort keys
  on `created_at` → `Track.added_ts`. `DateCreated[:10]` stays as display `date_added`.
- rekordbox reorders → manifest sig is order-sensitive → reorder = drift = resync.
- rekordbox auto-writes a `PIONEER/` folder onto any USB it touches. It is a
  hard external-product boundary: count its space, but never use it for drift,
  manifest ownership, verification, cleanup, import, browsing actions, or an
  ordinary write. The explicit generic whole-volume Erase is the only exception.

## Cover art (SoundCloud scrape + Rockbox/stock rendering)
- **Paste an ARTIST PROFILE URL into the art picker → you get the AVATAR**
  (og:image of a profile page IS the avatar; lands baseline-JPEG via `store_ok`).
- SoundCloud deep extract 403s → use FLAT search `thumbnails` (t500x500) for
  covers; artist avatar via the page `<meta og:image>`, NOT yt-dlp's user API.
- Rule: real title match → track cover, ELSE artist avatar (never a different
  track's cover).
- **`Unknown Artist` must never drive unattended art discovery.** A title alone
  is not accountable enough, and searching the placeholder can select an
  unrelated same-title upload or even a random profile using that name. Leave
  it coverless for a manual artist correction/picker/URL. Manual art remains
  available and is recorded with `origin=manual`; the background worker records
  `origin=auto` for new cache rows.
- **Orient hyphenated working titles with the effective artist.** Libraries use
  both `ARTIST - TITLE` and `TITLE - ARTIST`. Unconditionally dropping the left
  side made `CRASH TEST - WODD` use `WODD` as its song core, so any upload from
  WODD could receive the own-account rescue. `_core_title_for_artist` selects
  the opposite edge, and the unattended full-title floor is 0.80. Legacy cache
  rows predate provenance and require a read-only confidence/source/image audit;
  never infer that a `blocked` row or a confidence-1.0 picker/URL result is safe
  to replace merely because an old automatic row looks suspicious.
- **Managed defaults are a fallback layer, never discovered or per-track art.**
  An `ok` track cover or artist avatar always wins. Both natural misses and
  deliberate `blocked` rows receive a pool picture when the pool is non-empty;
  the underlying miss/block remains intact, so removing the pool reveals the
  same state and auto-fetch still skips blocks. Visible/exportable tracks are
  deterministically shuffled over the pool in exact round-robin balance.
  The separate pool manifest persists assignments; rebalancing must preserve
  valid mappings and move only the minimum needed for exact balance, or one new
  real cover creates gratuitous full-library device-art drift. Imported pool
  images are copied to `art/defaults/`, capped/normalized as
  baseline JPEG, backed up recursively, and then enter the ordinary MA446
  RGB565 and per-track Rockbox BMP derivative pipelines. Never make a default
  row `status=ok`, or it will suppress real discovery and masquerade as curation.
- yt-dlp = gray area → PUBLIC fork must NOT bundle it.
- **⚠ Rockbox can't render progressive/multi-scan JPEG, RLE BMP, or PNG.**
  HANDLED: `soundbox/artencode.py` normalizes ALL cached art to baseline JPEG;
  `store_ok` enforces at the cache boundary. Any NEW art path (manual picker!)
  must go through `ArtCache.store_ok` or `artencode.ensure_device_safe`.
- Rockbox first searches dimension-specific art requested by the theme, then
  repeats its track/album/cover search without the dimensions. AdwaitaPod Dark
  Simplified requests 160×160 in its WPS and 116×116 in its SBS mini-player.
  Read bounded square `%Cl` dimensions from the active WPS/SBS and write them as
  per-track `filename.<side>x<side>.bmp` siblings, retaining prior exact sizes.
  Also write a lightweight dimensionless 160×160 `filename.bmp` fallback. A
  160-only export makes one home screen blank; exact-only export makes a theme
  switch blank. NightPod is the acceptance case: WPS 130×130 and SBS 30×30,
  with optional `%Cl` placement flags after the dimensions. Folder `cover.*`
  files can collide when several tracks share a folder.
- **Embedded art outranks that external cover and is a low-memory risk.**
  The real `Take a Trip (DIRTYSNATCHA REMIX)` MP3 decoded cleanly but carried a
  3000×3000, 17,432,315-byte APIC. The track froze on Rockbox even though the
  installed theme displayed no covers; controlled original/picture-free/
  external-cover A/B playback isolated that APIC as the trigger. Rockbox
  exports therefore strip picture
  records only from a plan-scoped device payload, verify MP3 frames/WAV data/
  AIFF SSND/MP4 mdat/FLAC frames plus technical properties unchanged, and clear
  large staging after the batch. Composite payloads also retain only the
  effective SoundBox title/artist metadata: otherwise Rockbox can display a
  scrambled `iPod_Control/Music/Fxx/ABCD.*` filename, album, year, or comment
  even while iTunesDB is correct. Both
  transformations happen only in verified disposable copies. Never retag the
  canonical source. Do not keep a full playlist's normalized copies through the
  confirmation dialog: a large Collection can exceed available Mac space even
  when it easily fits the iPod. Planning must verify one derivative, retain its
  exact canonical-source and output size/hash contract, and discard it; commit
  rematerializes and verifies one track at a time. Managed Rockbox covers are uncompressed 8-bit paletted BMPs:
  retained exact theme sizes plus a dimensionless 160×160 fallback. Do not pass
  the canonical 500px image through to Rockbox or touch stock ithmb art.
- **Do not rebuild every committed derivative to plan an incremental sync.**
  Schema-3 media rows carry canonical source size/mtime/ctime, effective
  title/artist, payload hash, and the per-file media profile. Reuse that exact
  committed identity when all of them still match; stat-check every planned
  source again before the first device write. Once a row records an explicit
  media profile, do not reopen it for embedded-art or
  title/artist inspection during every plan—the committed profile is the policy
  result, and a source stat change is what invalidates it. A changed source,
  display field, or media profile must still take the full derivative
  verification route. A narrower legacy profile cannot prove that competing
  metadata is absent and therefore requires a one-time verified migration.
- **Do not hash unchanged device artwork during a no-op plan.** A Collection
  owns thousands of tiny BMPs; reading every one merely to retain it has reset
  the real MA446 USB transport with `ENXIO`. When the verified Mac variant's
  size/hash matches the owned manifest row, retain that device path without a
  live read. A planned artwork rewrite or deletion still requires the exact
  live-byte baseline before any mutation.
- **An in-flight iPod refresh is not a blank device.** Keep the immutable last
  verified Device/Songs/Playlists snapshot visible through planning, commit,
  deletion, and the post-write read. Replace the three surfaces atomically only
  when that read succeeds; on a transient read failure, retain the snapshot and
  label it as the last verified state. A successful commit is not user-complete
  until that snapshot refresh and the following sidebar free-space/drift scan
  both finish. Show `Finalizing iPod...` during those reads and delay the success
  dialog until Eject is actually available; never make dismissing an alleged
  completion dialog start another blocking device worker. `QMessageBox` runs a
  nested event loop, so install the explicit eject-ready latch before releasing
  `_exporting` and opening the modal; that latch pauses recurring polling until
  Eject or a new explicit device action while leaving Eject genuinely enabled.
  Do not make Eject cancel, adopt, or finish dashboard work: no such work
  may remain at the success boundary. Keep Eject fail-closed if any device read,
  writer, planner, or import really is active, and suppress polling through
  verification/unmount outcome. A no-write/up-to-date plan is still a completed
  Sync or Delete and must pass through the same final scan/latch before its OK.
- **Device progress is a transaction meter, not a copy meter.** Planning owns
  0–30% for composite sync, guarded device work owns 30–80%, and final
  dashboard/sidebar/drift reads
  plus worker-ownership release own 80–100%. Keep it monotonic across queued
  batches and multiple targets. Internal planner units can be roughly twice the
  unique managed source union, so use them only for the percentage bar: show
  animated `Planning iPod...` with no `#/#`. Show the counter only under
  `Syncing audio`, where it represents actual audio work. Queued-job formulas
  must give each job its complete proportional slice. Never publish 100%, `Complete`, a success modal,
  or enabled Eject/Sync while any required worker attribute remains owned. A
  failed required final read is terminal but not successful: retain Eject,
  report `Final refresh failed`, and suppress the success callback.
- **An explicit managed-playlist Delete is authoritative.** Do not force a
  separate Sync merely because a different schema-3 playlist changed or is no
  longer available from the current source. Reconcile other available managed
  playlists in the same transaction; reconstruct an unavailable unrelated
  playlist from its exact committed manifest snapshot so it is retained rather
  than silently deleted. Validate malformed identities/snapshots and keep all
  ordinary manifest-last rollback guards. Schema-3 presentation fields such as
  `device_name` are not a reason to refuse the user's checked deletion.
- **Rockbox playlist appends are incoming membership, not media imports.** The
  three-second poll may use the owned M3U8 size because a catalogue append must
  add a path line; do not hash every unchanged playlist on every tick. Confirmed
  planning reads/hashes only bounded managed M3U8 payloads and may authorize an
  external baseline only when the committed track sequence is an exact prefix
  and every appended path maps through schema-3 ownership to one current source
  track. Persist a genuinely new membership before device writes. If the track
  was already present, approve the exact live bytes for recovery but omit the
  appended occurrence from the desired payload, so Sync removes the accidental
  duplicate. Preserve duplicates that predate this Rockbox edit. Unknown paths
  are not imports; deletion/reorder edits and SoundBox-removal conflicts fail
  closed. Rockbox may serialize primary-volume entries as `/<HDD0>/...`; strip
  only that exact leading HDD0 token before schema-3 ownership lookup and
  canonical M3U8 regeneration. Expected playlist drift must not erase the
  separately valid read-only device identity/database dashboard: show the
  affected playlist as Sync needed, but never present its changed entries as
  trusted before confirmed planning.
- **Rockbox payload metadata has one authority.** Some purchased MP3s store
  `Purchased at Beatport.com` in `TXXX:COMMENT`/`COMM`; WAVs may have several
  INFO/ID3/BWF containers containing receipts, years, albums, or stale titles.
  Themes and Rockbox parsers do not choose consistently among them. Strip all
  competing user-facing metadata from the disposable payload and write exactly
  the effective SoundBox title/artist pair. Never modify the canonical source.
- **MP3 tag size is not an audio property.** Mutagen estimates the duration of
  some CBR MP3s from total file size, so replacing a padded ID3 block can move
  its reported length by milliseconds while every MPEG frame remains exact.
  The Rockbox derivative contract compares MP3 sample rate/channels plus the
  exact tag-excluded audio-essence hash; never reject an otherwise byte-exact
  payload because that tag-size-dependent duration estimate changed.
- **Let Rockbox own its binary tag cache.** SoundBox may atomically and
  recovery-safely enforce `tagcache_autoupdate: on`,
  `playlist viewer track display: title from tags`, `sort playlists: alpha`,
  and a minimum `max files in playlist: 10000` in `.rockbox/config.cfg`,
  preserving every unrelated line and the existing LF/CRLF style. Normalizing
  Rockbox's CRLF file to LF made an already-correct config look perpetually
  dirty. Preserve any playlist limit already above 10,000; higher limits consume
  playback buffer, and Rockbox needs a full restart before a changed limit is
  active. Stock iTunesDB playlists have 32-bit MHIP counts and no corresponding
  firmware setting; do not invent one. Never synthesize or patch
  `database_*.tcd`.
  Rockbox's next boot reconciles added, changed, and deleted media; an explicit
  Initialize Now remains a one-time user action if an old cache is corrupt.
  Dashboard freshness may compare `database_idx.tcd` only to the current
  manifest/config mtimes (with FAT tolerance); that is a pending-restart signal,
  not permission to parse, delete, or rewrite the cache.
- **Playlist Catalogue order is a separate Rockbox setting, and physical
  behavior wins.** Its browser uses `sort playlists`, not the ordinary file-
  browser sort. Although upstream supports date sorting, the MA446 continued to
  present the catalogue alphabetically after controlled FAT mtimes. Enforce
  `sort playlists: alpha` and prefix only Rockbox M3U8 filenames with a zero-
  padded manifest position (`01 - HOUSE.m3u8`). Keep stock titles and manifest
  `device_name` clean, retain full rekordbox hierarchy in the manifest, and add
  folder context only for duplicate leaves. Reorder is a transactional rename
  of tiny owned playlist files, never a reason to duplicate or recopy media.
  FAT resolves case-only names to the same directory entry, so `DnB` to `DNB`
  cannot use the ordinary write-new/delete-old path. Preserve the verified old
  path in the playlist plan, recovery-back and unlink that owned entry before
  publishing the requested spelling, suppress its duplicate stale manifest
  row, and restore both the original spelling and bytes on rollback. A distinct
  file at a non-case-equivalent destination remains a hard collision.
- **Deleting the last managed track can expose directory AppleDouble clutter.**
  Scrubbing only `._filename` for transaction payload paths misses Finder's
  `._Artist` directory sidecars after the real tree becomes empty. Composite
  planning treats every regular `._*`/`*.sbpart` below `/Music` or `/Playlists`
  as a verified transient orphan, permits a cleanup-only empty-library plan,
  and removes empty child directories only after the manifest-last commit.
  Do not use `._*` presence as drift: Finder can recreate those files as soon
  as a clean export finishes, creating a permanent false Sync-needed loop.
  Only an abandoned `*.sbpart` independently indicates an interrupted write.
- **Rockbox WAV titles are RIFF INFO, not ID3-in-WAV.** Its WAV metadata parser
  reads `LIST/INFO` `INAM` and `IART`. Mutagen's normal WAVE tag writer creates
  an ID3 chunk that Rockbox ignores, causing a shared `ABCD.wav` to display its
  scrambled filename. A real source also carried a 23.5 KiB opaque `NITR` field
  before `INAM`/`IART`; desktop parsers found the artist but classic Rockbox did
  not. Put one small canonical title/artist INFO list immediately after `fmt`
  and remove every competing INFO/ID3/BWF/XMP display container from the
  disposable copy. Normalize every WAV payload even when its text already
  matches, preserve all PCM bytes, and verify the resulting RIFF values.
- **Composite drift includes effective metadata.** Membership-only playlist
  signatures cannot notice a SoundBox title/artist edit. Keep the schema-3
  `metadata_sig` generated by the same effective metadata function as planning,
  and pass both override maps into every sidebar/dashboard drift worker.
- **Never regenerate an orphan to prove it can be deleted.** Export profiles
  intentionally evolve (the physical 500px → 160px cover migration exposed
  this). Manifest ownership plus the exact live baseline authorizes cleanup;
  rename the live file into same-volume transaction recovery, restore that
  exact file on failure, and discard it only after manifest-last success.
- **A dual device view is not an iTunesDB view.** Build Songs/Playlists from the
  schema-3 manifest and parsed iTunesDB together, label rows Both/Stock/Rockbox,
  and read only verified small M3U8 payloads for order. Reporting the iTunesDB
  count or Apple smart indexes as the whole device library is misleading.
- **The curated baseline JPEG is canonical for both firmware paths.** Never
  embed it into source audio to satisfy stock firmware. `stock_artwork/` under
  AppData is disposable content-addressed RGB565: MA446 format 1028 is
  100×100×2 bytes and 1029 is 200×200×2 bytes. Background preparation
  pauses during playback; sync still verifies every payload hash.
- **Stock artwork is four coupled layers:** MHIT (`mhiiLink`/count/size), one
  per-track ArtworkDB MHII with a matching song dbid, MHNI format offsets, and
  the two ithmb pixel files. Never update only one layer. Full replacement and
  compaction preserve ArtworkDB/ithmb bytes first; incremental sync follows the
  unreferenced-slot rule below. Validate all bounds, then publish the linking
  iTunesDB last. Missing/incomplete old pixels fail closed.
- **Incremental ithmb writes may touch only currently unreferenced slots.** Keep
  image IDs stable, reuse free fixed-size holes before appending, and publish
  ArtworkDB before iTunesDB. Hash free-slot pixels during the Mac-side plan and
  relink an exact match without rewriting it; only a nonmatching free slot needs
  a range write. Never overwrite the current track's live slot.
  Failed range writes can leave harmless orphan pixels; a later plan rediscovers
  them as free. Full playlist deletion remains the compaction path.
- **The Mac stock shadow is acceleration, never authority.** It lives under
  AppData `stock_ipod_state/`, keyed by library persistent ID + volume UUID, and
  requires matching identity, model, iTunesDB, ArtworkDB, and ithmb facts.
  Missing/mismatched state performs full reconciliation. Save it after device
  commit; shadow-save failure must not roll back a healthy iPod.
- **A valid stock shadow may still contain only a subset of source mappings.**
  Playlist-scoped bootstrap must not classify a later-selected track as new
  merely because its source key is absent from the shadow. Reconcile only the
  missing candidates against live device media by size/suffix and exact content
  hash before planning a copy. Likewise, compare prepared RGB565 payloads with
  their current device slices when the local artwork cache key is absent. A
  partial acceleration index must never turn an artwork-only edit into duplicate
  audio or unchanged artwork writes.
- **Do not content-scan manifest-proven managed stock media during an additive
  plan.** An exact source→DBID/path/payload baseline already proves those rows.
  Building original/payload reconciliation keys for them can make every queued
  playlist stream the existing iPod library and has produced macOS `ENXIO`
  (`Errno 6`) before any write. Narrow content reconciliation to newly selected,
  unproven sources; their partial-shadow adoption rule remains unchanged.

## Rockbox / iPod (Phase 3 — hardware)

- Do not stream large interrupted device payloads merely to construct the
  confirmation dialog. A dual recovery plan may identify an exact-path,
  exact-size candidate, but commit preflight must verify its full expected
  SHA-256 before the first write. Report that verification through normal sync
  progress and persist completed proofs in the Mac-side journal.
- **The mounted on-disk layout selects the exporter.** SoundBox does not know
  which firmware the user last booted. `/.rockbox/` alone uses ordinary Rockbox
  export; `/.rockbox/` plus `iPod_Control/` uses only the MA446/FAT32 composite
  path. Root `/Music` files are not automatically visible to stock firmware.
- **Dual-library means shared files, not two exports.** Stock-safe media lives
  once under `iPod_Control/Music` and is referenced by both iTunesDB and Rockbox
  playlists; stock-unsafe media lives once under `/Music` and stays absent from
  iTunesDB. Schema 3 extends the original stable-ID/signature/drift manifest and
  coordinates the ownership graph and rollback. Ordinary schema-2 export must
  refuse schema 3; generic Sync must route it through the composite planner,
  never either independent exporter. Playlist rename/move/content/removal is
  coordinated across both firmware views. Managed audio uses its schema-3
  ownership/path/size baseline and a reproducible exact source-side payload;
  art and playlists retain live hash checks. Delete before the final manifest
  publish and restore the verified source payload on rollback.
- **Content equality is not library identity.** Deduplicate repeated playlist
  references to the same canonical source path, but never collapse two distinct
  source paths onto one iTunesDB record merely because their bytes match. The
  real full-library plan contained 19 byte-identical pairs and six had different
  effective title/artist labels. Each distinct source needs its own DBID,
  metadata/artwork, and scrambled device path.
- **Scrambled stock names require filename artwork, not folder artwork.** For
  `/iPod_Control/Music/Fxx/ABCD.wav`, write `ABCD.jpg` beside it. Never guess the
  audio suffix when recording cover membership: retain an explicit cover→audio
  mapping because shared files may be WAV or MP3.
- **iPod MUST be FAT32, NOT HFS+.** Rockbox does not support HFS+.
- m3u8: UTF-8 **no BOM**; absolute-from-drive-root paths (`/Music/Artist/...`).
  The Playlist Catalogue default dir is `/Playlists` and it does not recurse:
  dual sync writes direct, zero-padded children such as `01 - HOUSE.m3u8`,
  never `/Playlists/GENRES/HOUSE.m3u8`. Keep full hierarchy in the manifest;
  stock/SoundBox titles use the concise leaf unless a collision needs context.
- Rockbox DB is OPTIONAL (playlists play without it). Composite sync enables
  Rockbox's own automatic update and reports restart pending; after dumping or
  retagging files, restart Rockbox so its firmware-owned cache reconciles. Use
  full database initialization only when an older cache is actually corrupt.
- Device sync manifest lives at `<device>/.soundbox/manifest.json`.
- **A USB remount can defeat rollback without corrupting the committed
  generation.** `DeviceSession` must reject the new mount identity; a read-only
  stock/composite plan must also carry its pinned identity across the
  confirmation modal and compare it before commit performs any write. Never cross
  the remount to delete paths remembered by the old session. A dual recovery
  directory named for the still-current manifest can authorize a later retry to
  adopt only freshly planned path/size/SHA-256 matches. Persist each completed
  full-hash proof in Mac-side disposable state and recheck its size, timestamp,
  and edge fingerprint so repeated USB resets resume verification rather than
  rereading all prior survivors. The UI must report the preserved baseline and
  verified-resume state, not imply that rollback necessarily removed new files.
- **A filesystem-browser confirmation grants authority to one mounted volume,
  not a reusable path string.** Pin before showing rename/new-folder/artist/delete
  input, carry that session into a delete worker, require all selected items to
  share the mount, and revalidate recursively without following symlinks. M3U8
  device import is read-only but still must confine every resolved entry to that
  volume; otherwise `..` or an on-device symlink can copy an unrelated Mac file.
- **Dual playlist drops are confirmed jobs, not hidden planner mutations.** Ask
  Yes/No for each drop before expensive work. Deduplicate by stable playlist
  identity and retain FIFO intent. Once the active worker finishes, coalesce all
  already-confirmed waiting playlists into one next generation so iTunesDB and
  the same managed-media baseline are not reparsed for every drop. Drops that
  arrive after that plan starts form the following batch. Carry one cumulative
  entry counter/summary across the queue and cancel unseen queued work after a
  failure; never revive hidden stale intent later.
- Device detection is read-only and capability-based. See `DEVICES.md`.
- `soundbox.stock_ipod` is the sole exception to the `iPod_Control` no-write
  rule. Its first boundary is stock-only MA446 + checksum-zero `iTunesDB` +
  MP3/WAV: Clickwheel renders only off-device, SoundBox reparses and audits the
  database plus artwork linkage, archives recovery bytes, copies media/artwork
  atomically, and commits the database last. Never pass the live mount to
  Clickwheel's high-level copy/sync
  helpers; they do not meet SoundBox's rollback, stale-state, play-count, or
  smart-playlist contract. On a dual-boot FAT32 MA446, only
  `soundbox.dual_ipod` may invoke this transaction. It supplies verified
  picture-free payloads, pre-copies Rockbox-only files, publishes Rockbox
  playlists after the validated stock commit, writes schema 3 last, and uses
  exact stock rollback if a later publication fails.
- **Apple Videos is the only deletable smart index.** It must match the exact
  `Videos` title plus MHSD-5 type 0. A real Apple database can contain both an
  ordinary-MHLP smart alias and the canonical MHSD-5 record with different
  playlist IDs. Prefer the canonical record in the device snapshot and remove
  both representations when that canonical ID is selected; otherwise the UI
  can report success while the true index remains. Neither representation is a
  media owner or candidate. Preserve all other smart rules and never generalize
  this to Music, Movies, TV Shows, or Audiobooks. On-The-Go does not exist in
  iTunesDB and cannot be deleted there.
- **The stock Artists → artist → All level is firmware UI.** SoundBox supplies
  clean title/artist metadata and the intended artist indexes, but classic stock
  firmware inserts its own `All` level before the songs. Do not corrupt album or
  artist metadata trying to remove that accepted firmware navigation layer.
- **MHBD `DatabaseID` is not a device identity.** Apple and SoundBox database
  rewrites legitimately replace it. The library persistent ID remained stable
  across every captured generation; pair it with the macOS volume UUID when
  remembering a confirmed model. `stock_ipod_info` owns read-only UI snapshots
  and may merge `Play Counts` only in memory.
- **The validated MA446 Rockbox path has real hardware acceptance.** The
  original/picture-free/external-cover `Take a Trip` A/B proved the giant APIC
  caused the freeze; picture-free media, theme-native art, full-Collection
  commit, firmware database refresh, and zero-padded playlist order all passed
  on-device. Do not generalize that result to another iPod model, checksum
  family, or schema-2 migration without a separate fixture and physical gate.
