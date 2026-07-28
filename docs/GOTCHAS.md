# SoundBox gotchas & niche facts

Hard-won + researched. **Read before touching workers, playlists, sync, art, or
the iPod path.** Related: `ARCHITECTURE.md`, `ROADMAP.md`.

## Repo hygiene
- **NEVER pipe `./check.sh` through `tail`/`grep` in a `&&` chain** — the
  pipeline exit code is `tail`'s `0`, so a FAILING gate gets masked and the
  build/commit proceeds. (Real incident: a syntax-broken module shipped + got
  committed.) Run `./check.sh` bare (or `> file 2>&1; echo $?`), then build.
- Project is under git (local `master`, no remote). Preserve the existing dirty
  tree and inspect `git status` at handoff. Commit/push only when Sonny asks.
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
- **QThread "destroyed while running" → SIGABRT.** Never let the last Python ref
  to a worker QThread drop *mid-run*. Slots on a worker's CUSTOM signals
  (`finished_all`/`done`/`failed`) run WHILE `run()` is still finishing — nulling
  `self.x_worker` there GCs the thread on its own thread → abort. FIX (in place):
  drop the ref only on the built-in `finished` signal via
  `MainWindow._finish_worker(attr, w)` (main thread, after `run()` returns). Used
  for export/refresh/resync workers + `PlayerBar._wf`. Don't regress it.
- Accessing a `deleteLater()`'d worker raises `RuntimeError: Internal C++ object
  already deleted`. Guard with the `_running(w)` helper.
- `shutdown()` (from `closeEvent` + `app.aboutToQuit`) must stop/wait every
  worker BEFORE Qt teardown.
- **There is no Qt playback fallback.** Qt Multimedia is analysis-only through
  `QAudioDecoder` for AAC/ALAC. Native-deck initialization failure must leave
  library management usable, disable player controls, and surface the exact
  diagnostic. Do not reintroduce an engine setting or silent fallback.
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
- **Waveform geometry is channel-safe linear uint16 analysis, not mono-summed
  display bytes.** Measure each window as the peak absolute sample in either
  channel: `abs((L + R) / 2)` erased wide/anti-phase reverb tails that remained
  audible in both speakers. Persist normalized little-endian uint16 peaks under
  `waveform_model=soundbox-channel-peak-u16-v1`; legacy uint8 V4 records remain
  playable but are not current-quality. The `0.80` display exponent and
  continuous four-LSB visibility shoulder are applied only during native/Qt
  upload, after the linear cache is read. The shoulder is exactly zero at zero
  and runs before spatial reconstruction; it is not a hard post-reconstruction
  floor. Never bake either visual curve into analysis: keeping the cache linear
  is what makes later appearance tuning recook-free, while true digital zero
  stays exactly zero.
- **The scrolling surface must be fixed in song space, not reconstructed at a
  new playback phase each frame.** Print height and amplitude-weighted RGB onto
  a private song-coordinate attribute lattice with four attributes per
  physical pixel at every zoom. Whole-pixel close translation was exact after
  integer alignment but made dense one-to-two-pixel spikes visibly strobe.
  Interpolate attributes for continuous subpixel motion. The buffer needs a
  generous guarded margin scaled by the attribute density and absolute
  integer-song-coordinate recentering so regeneration cannot create a viewport
  seam.
- **A pixel cannot display more than about one envelope value.** Keep
  `kLodBias = 0` and ceil the footprint level. Negative bias leaves
  undisplayable high-frequency content under a pixel, which reappears as a
  moving hairy contour rather than useful detail. The midpoint taps exist to
  integrate final coverage, not to buy a sharper mip.
- **Do not recover close detail by flooring the mip.** The fixed lattice stops
  phase-dependent redraw, but it cannot make above-Nyquist values displayable.
  A floor-mip prototype looked richly detailed in a still and immediately
  produced a nasty hairy/wiggly edge in motion on the real stress track. Keep
  the conservative ceiling mip. The targeted zoom recording later justified a
  deliberate kernel split: positive B-spline reconstruction through 384 px/s
  for stable mid/wide contours, returning to an interpolating monotone cubic
  at 576 px/s and above. Do not extend the averaging B-spline into the close
  range where it visibly hid useful detail, and do not return to linear there:
  its slope corners move through the raster even on a fixed song lattice.
- **Antialias coverage must remain continuous through final compositing.** A
  hard minimum vividness for every nonzero fragment turns a contour crossing a
  pixel into an on/off color flash, even when the song coordinate itself is
  perfectly smooth. Use the cubic ease-out only for the small overview, where
  true subpixel peaks need visibility. The scrolling lane must use exact
  geometric coverage at every zoom; at 24 px/s the ease-out still exaggerated
  20% physical coverage to nearly 49%, making saturated red/orange edges pulse.
  Coverage policy may change compositing only—never height, hue, LOD, detail
  density, or timing.
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
  through dense passages. Sample five quarter-pixel boundaries over
  `[-0.5, +0.5]`, feed their heights to `profile_coverage`, and
  integrate premultiplied RGB over that same span. Do not widen the footprint:
  one pixel removes the alias while retaining the close detail that B-spline
  reconstruction erased.
- **Localized stop artifacts do not justify a coarser mip or a wider global
  filter.** The real all-zoom recording isolated residual contour motion at
  the fixed 64 and 184 px/s stops. Those stops use a narrow zoom-locked,
  same-LOD `[1, 2, 1]` pass at `-0.5/0/+0.5` physical pixel before the existing
  one-pixel integration. Its weight is zero at all eleven other fixed stops.
  Do not extend it into 576–1536 px/s: that is the detail-preserving monotone
  path, and broad averaging there repeats the previously rejected detail loss.
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
- **Player cache schemas are data contracts, not renderer version numbers.**
  A shader/filter/clock change does not require re-preparing the library.
  Bump/rebuild only when the persisted payload itself changes. The hot path is
  canonical V4 sections. Cache hits start one short-lived staged reader: never
  read/hash the whole record or build Metal buffers on the UI thread.
  `WaveformFillWorker` remains incremental, prioritizes the
  selected track, and cooperatively yields inside long tracks during playback
  and foreground interaction. Startup metadata discovery reads only V4
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
- Overview and ultra waveform buffers are one visible surface. Prepared loads
  must group both native uploads while presentation is suspended and commit
  them together. Painting the overview while a long ultra section is still
  decompressing/building makes the waveform visibly change size on promotion.
- No manual SoundBox grid editor is shipped. V4 has a storage-only ownership
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
- Hypothesis tests are `derandomize=True` — deterministic; a failure is REAL.

## Offscreen UI tests can dirty the REAL config
- Constructing `MainWindow()` offscreen and firing a widget handler runs the real
  slot → `_save_state()` → WRITES the real `library.json`. For render/smoke
  tests, set state via `win.settings[...] = …` + `win._sync_settings_controls()`
  (blockSignals, NO save), or point AppDataLocation at a temp dir.
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
- Title pipeline order: effective ARTIST is computed from the RAW/overridden name
  FIRST, then `effective_title = override | tidy(neat(name, artist), junk)`. Junk
  rules: `metadata.BUILTIN_TITLE_RULES` + user words; `Library.title_junk=None`
  means defaults.
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
- **ALAC/AAC V4 analysis uses `qt_detail_peaks()` (QAudioDecoder =
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
  foreground-critical and bypasses those deferrals. Do not reduce V4
  resolution, skip spectral/color sections, or substitute a colorless
  placeholder to save power.
- **PQTZ-only means direct DAT rows, not a smaller generic ANLZ parse.** The
  legacy grid migration bounds-checks and decodes only the eight-byte PQTZ rows
  from `ANLZ0000.DAT`. It must never call the generic parser or open EXT/2EX;
  thousands of tiny metadata migrations otherwise become a needless energy
  spike. Every Rekordbox path remains read-only.
- **A V4 cache hit can still be upgrade-pending.** Metadata migration can leave
  a playable record waiting for its current color/provenance version. Only
  `track_analysis.is_ready()` decides whether background preparation is done;
  it requires an ultra-rate contour plus RGB. The player may load any valid V4
  record immediately. Do not collapse those two meanings. The read-only audit
  reports valid/playable and current-quality coverage separately for the same
  reason.
- **Cancellation must not downgrade V4.** The native analyzer returns no result
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
  cleanup.** V4 audio cooking pauses below 5 GiB and resumes above 7 GiB.
  `library.json`, its small rotating backups, art, and existing V4 analysis are
  not expendable. Installer app rollbacks are temporary and only one may exist
  during verification.
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
- `rb_local_deleted` truthy = soft-deleted → skip (tracks AND playlists).
- Collection has ~5244 tracks but only ~2940 are in playlists; load only
  playlist-referenced tracks.
- **`DjmdPlaylist.ID` is STABLE across rename + move** → the device-sync identity.
- **"Date added" = `content.created_at` (the DB row's real timestamp, time-of-day
  precision), NOT `DateCreated`.** `DateCreated` is a stale tag/metadata date that
  can predate the actual add by weeks (e.g. a track bounced 6/29, added 7/10 reads
  `DateCreated=6/29`, `created_at=7/10 16:43`). Collection's newest-added sort keys
  on `created_at` → `Track.added_ts`. `DateCreated[:10]` stays as display `date_added`.
- rekordbox reorders → manifest sig is order-sensitive → reorder = drift = resync.
- rekordbox auto-writes a `PIONEER/` folder onto any USB it touches (not us —
  leave it alone).

## Cover art (SoundCloud scrape + Rockbox rendering)
- **Paste an ARTIST PROFILE URL into the art picker → you get the AVATAR**
  (og:image of a profile page IS the avatar; lands baseline-JPEG via `store_ok`).
- SoundCloud deep extract 403s → use FLAT search `thumbnails` (t500x500) for
  covers; artist avatar via the page `<meta og:image>`, NOT yt-dlp's user API.
- Rule: real title match → track cover, ELSE artist avatar (never a different
  track's cover).
- yt-dlp = gray area → PUBLIC fork must NOT bundle it.
- **⚠ Rockbox can't render progressive/multi-scan JPEG, RLE BMP, or PNG.**
  HANDLED: `soundbox/artencode.py` normalizes ALL cached art to baseline JPEG;
  `store_ok` enforces at the cache boundary. Any NEW art path (manual picker!)
  must go through `ArtCache.store_ok` or `artencode.ensure_device_safe`.
- Rockbox art search order in a track folder: `<trackname>.{jpg,bmp}` →
  `<albumtitle>.{jpg,bmp}` → `cover.{jpg,bmp}` → `folder.jpg`. We write per-track
  `cover.jpg` → matches. The theme/WPS must support album art or nothing shows.

## Rockbox / iPod (Phase 3 — hardware)
- **iPod MUST be FAT32, NOT HFS+.** Rockbox does not support HFS+.
- m3u8: UTF-8 **no BOM**; absolute-from-drive-root paths (`/Music/Artist/...`);
  Playlist Catalogue default dir = `/Playlists`.
- Rockbox DB is OPTIONAL (playlists play via File Browser); DB mode needs
  "Update now" after dumping files.
- Device sync manifest lives at `<device>/.soundbox/manifest.json`.
- Device detection is read-only and capability-based. See `DEVICES.md`.
- A stock-only iPod is preview-only: never write `iTunesDB`, `iTunesCDB`,
  `Library.itdb`, or files beneath `iPod_Control`. A dual-boot FAT32 iPod may
  receive the Rockbox-style `/Music`, `/Playlists`, and `/.soundbox` export;
  its stock database remains untouched.
- UNVALIDATED until hardware: theme renders cover.jpg; exact order/nesting holds;
  no choke at ~2900 tracks; DB refresh flow. (USB is a good proxy for everything
  EXCEPT on-device rendering/playback.)
